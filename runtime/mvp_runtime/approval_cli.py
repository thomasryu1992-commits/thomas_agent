"""Operator CLI for the R9 approval flow — ask Thomas, list, and read back the answer.

The *asking* side lives here; the *answering* side is the control channel (``/approve <id>``
/ ``/reject <id>`` in a verified Telegram private chat), because only that channel can prove
the answer is Thomas's. Deciding from this local console is deliberately impossible: the
Governance Policy's ``local_operator_console.new_high_risk_approval_creation_allowed: false``
and the approval record's own requirement of a
``telegram_private_control_channel`` verification ref both forbid it.

Usage:
    # Ask Thomas to approve promoting a working-memory candidate to VALIDATED:
    python -m runtime.mvp_runtime.approval_cli request --candidate-id memcand_abc123

    # What is Thomas still being asked?
    python -m runtime.mvp_runtime.approval_cli list

    # What did he answer, and how do we know it was him?
    python -m runtime.mvp_runtime.approval_cli show approval_abc123

An APPROVED approval does not act on its own. Spending it — ``consume`` — performs the one
bound promotion exactly once, and only behind the ``approval_consumption`` safety flag
(fail-closed when the flag is off). See ``consumption.py``.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from . import approval, consumption, timeutil
from .approval_store import ApprovalStore
from .audit import build_approval_request_audit, build_audit_gap_record
from .binding import bind_task_to_core
from .cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE, force_utf8_io, report_block
from .errors import MvpRuntimeError
from .intake import build_task
from .permission import build_memory_promotion_permission_decision
from .store import LedgerStore
from .working_memory import WorkingMemoryStore, find_candidate


def _record_audit_gap(ledger: LedgerStore, gap_kind: str, exc: MvpRuntimeError, *,
                      subject_ref: str, now: str) -> None:
    """Durably note that something happened whose audit event could not be written.

    Best-effort by construction: this runs *because* a ledger write already failed, so it
    may fail too. The stderr warning stays either way — this only adds the durable half
    when it can."""
    try:
        ledger.append_block(build_audit_gap_record(
            gap_kind, reason_code=exc.reason_code, subject_ref=subject_ref,
            now=now, detail=exc.reason,
        ))
    except MvpRuntimeError:
        sys.stderr.write("WARNING: the audit gap itself could not be recorded\n")


def _find_candidate(candidate_id: str) -> dict[str, Any]:
    """Locate the live working-memory candidate by id, or fail closed.

    The candidate is read from the store rather than taken from the caller, so the approval
    is bound to what is actually on record — an operator cannot ask Thomas to approve
    content that does not exist. Uses THE shared lookup (``working_memory.find_candidate``,
    latest-wins, CANDIDATE-scope filtered): the ask and the spend (consumption) must resolve
    "the candidate" identically, or Thomas can be asked about content the spend then rejects.
    """
    entry = find_candidate(WorkingMemoryStore.default(), candidate_id)
    if entry is None:
        raise MvpRuntimeError("UNKNOWN_CANDIDATE", f"no working-memory candidate with id {candidate_id}")
    return entry


def _request(args: argparse.Namespace) -> int:
    candidate = _find_candidate(args.candidate_id)
    now = timeutil.utc_now_iso()

    # The approval binds to a Task + Core Binding, so the ask is anchored to a real, bound
    # request rather than floating free.
    task = build_task(
        f"메모리 후보를 VALIDATED로 승격 검토: {candidate['candidate_id']}",
        now=now, channel="manual", requester_type="real_thomas", requester_id="Thomas",
        authenticated=True,
    )
    _, bound = bind_task_to_core(task, now=now)
    permission_decision = build_memory_promotion_permission_decision(bound, candidate, now=now)
    request = approval.build_approval_request(permission_decision, now=now, ttl_minutes=args.ttl_minutes)

    store = ApprovalStore.default()
    store.append_permission_decision(permission_decision)
    store.append([request])

    ledger = LedgerStore.default()
    try:
        ledger.append_audit_events(build_approval_request_audit(
            request, now=now, genesis_previous_hash=ledger.last_audit_hash(),
        ))
        sys.stderr.write(f"LEDGER: approval request audited to {ledger.root}\n")
    except MvpRuntimeError as exc:
        # The ask is already durable in the approval store, so it stands — but the gap must
        # not live only in a stderr line nobody keeps. Record it durably (different file, so
        # a broken audit ledger does not take this with it) and let `recovery` surface it.
        _record_audit_gap(ledger, "approval_request", exc, subject_ref=request["approval_id"], now=now)
        sys.stderr.write(f"WARNING: request audit failed ({exc.reason_code}); the request stands\n")

    sys.stdout.write(approval.request_message(request, permission_decision) + "\n")
    sys.stderr.write(
        f"\nSTORED: {request['approval_id']} is PENDING until {request['validity']['expires_at']}.\n"
        "Send it to Thomas on the verified control channel; he answers with /approve <id> [reason] "
        "or /reject <id> [reason].\n"
    )
    return EXIT_OK


def _list(args: argparse.Namespace) -> int:
    store = ApprovalStore.default()
    pending = store.pending()
    if not pending:
        sys.stdout.write("No approvals are pending.\n")
        return EXIT_OK
    now = timeutil.utc_now_iso()
    for item in pending:
        snapshot = item["approved_action_snapshot"]
        expired = " (EXPIRED — can no longer be decided)" if approval.is_expired(item, now=now) else ""
        sys.stdout.write(
            f"{item['approval_id']}  {snapshot['action_type']} on {snapshot['target_ref']}\n"
            f"  expires {item['validity']['expires_at']}{expired}\n"
        )
    return EXIT_OK


def _show(args: argparse.Namespace) -> int:
    record = ApprovalStore.default().get(args.approval_id)
    if record is None:
        sys.stderr.write(f"BLOCKED UNKNOWN_APPROVAL: no approval with id {args.approval_id}\n")
        return EXIT_BLOCKED
    approver = record["approver"]
    snapshot = record["approved_action_snapshot"]
    lines = [
        f"approval_id : {record['approval_id']}",
        f"status      : {record['status']}",
        f"action      : {snapshot['action_type']} on {snapshot['target_ref']}",
        f"fingerprint : {record['action_fingerprint']}",
        f"validity    : {record['validity']['issued_at']} -> {record['validity']['expires_at']}",
        f"approver    : {approver['approved_by'] or '—'} ({approver['verification_status']})",
        f"verified via: {approver['identity_verification_method'] or '—'}",
        f"evidence    : {approver['verification_ref'] or '—'}",
        f"decided at  : {record['decision']['decided_at'] or '—'}",
        f"reason      : {record['decision']['decision_reason'] or '—'}",
        f"consumption : {record['consumption']['consumption_status']} (one-time use)",
    ]
    if record["consumption"].get("consumption_ref"):
        lines.append(f"consumed    : {record['consumption']['consumed_at']} -> {record['consumption']['consumption_ref']}")
    if record["status"] == "APPROVED":
        lines.append("")
        lines.append("APPROVED does not act on its own. Spend it with:")
        lines.append(f"  python -m runtime.mvp_runtime.approval_cli consume {record['approval_id']}")
        lines.append("(only on a machine where the approval_consumption safety flag is activated).")
    elif record["status"] == "CONSUMED":
        lines.append("")
        lines.append("CONSUMED — the one-time grant was spent; it cannot be consumed again.")
    sys.stdout.write("\n".join(lines) + "\n")
    return EXIT_OK


def _consume(args: argparse.Namespace) -> int:
    result = consumption.consume_approval(args.approval_id)
    consumed = result["approval"]
    validated = result["validated"]
    audit_events = result["audit"]
    sys.stdout.write(
        f"CONSUMED {consumed['approval_id']}\n"
        f"  promoted -> {validated['validated_memory_id']} (VALIDATED, {validated['scope']})\n"
        f"  by {validated['promoted_by']}: {validated['promotion_reason']}\n"
    )
    if audit_events:
        sys.stdout.write(f"  audited as {audit_events[0]['audit_event_id']} (OTHER / APPROVAL_CONSUMED)\n")
    sys.stderr.write(
        "The approval is now CONSUMED and single-use. Validated memory, the approval store, "
        "and the ledger are local, gitignored, per-machine. Never committed.\n"
    )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    request = sub.add_parser("request", help="ask Thomas to approve promoting a memory candidate")
    request.add_argument("--candidate-id", required=True, help="candidate_id of the working-memory candidate")
    request.add_argument("--ttl-minutes", type=int, default=None,
                         help="approval lifetime (default: the policy maximum for the scope)")
    request.set_defaults(func=_request)

    listing = sub.add_parser("list", help="list approvals still awaiting an answer")
    listing.set_defaults(func=_list)

    show = sub.add_parser("show", help="show one approval and its verification evidence")
    show.add_argument("approval_id")
    show.set_defaults(func=_show)

    consume = sub.add_parser(
        "consume", help="spend an APPROVED approval to perform its one bound promotion (gated)")
    consume.add_argument("approval_id")
    consume.set_defaults(func=_consume)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except MvpRuntimeError as exc:
        return report_block(exc)


if __name__ == "__main__":
    raise SystemExit(main())
