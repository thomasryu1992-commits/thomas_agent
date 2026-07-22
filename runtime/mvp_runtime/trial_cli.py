"""Operator CLI for the Candidate Role Trial — ask Thomas, then run the approved trial.

The *asking* side lives here; the *answering* side is the verified control channel
(``/approve <id>`` / ``/reject <id>``), exactly as for a memory promotion — deciding from
this local console is deliberately impossible. Running the approved trial is a separate,
gated, single-use spend (the ``approval_consumption`` safety flag; fail-closed when off).

Usage:
    # Ask Thomas to approve one isolated trial of a candidate role:
    python -m runtime.mvp_runtime.trial_cli request research.general "재택 물리치료 시장 근거 조사"

    # After /approve on the control channel, spend the grant and run the trial once:
    python -m runtime.mvp_runtime.trial_cli run approval_abc123

``list`` / ``show`` live in approval_cli — trial approvals are ordinary approvals in the
same store. A trial never activates a role: the outcome is a durable trial report in the
ledger, evidence for a separate Thomas promotion decision.
"""

from __future__ import annotations

import argparse
import sys

from . import approval, timeutil, trial
from .approval_store import ApprovalStore
from .audit import build_approval_request_audit, build_audit_gap_record
from .cli_common import EXIT_BLOCKED, EXIT_OK, force_utf8_io, gate_banners, report_block
from .errors import MvpRuntimeError
from .providers import select_provider, select_validator_provider
from .store import LedgerStore
from .worker import MockProvider


def _record_audit_gap(ledger: LedgerStore, gap_kind: str, exc: MvpRuntimeError, *,
                      subject_ref: str, now: str) -> None:
    """Durably note that something happened whose audit event could not be written
    (best-effort by construction; the approval_cli precedent)."""
    try:
        ledger.append_block(build_audit_gap_record(
            gap_kind, reason_code=exc.reason_code, subject_ref=subject_ref,
            now=now, detail=exc.reason,
        ))
    except MvpRuntimeError:
        sys.stderr.write("WARNING: the audit gap itself could not be recorded\n")


def _request(args: argparse.Namespace) -> int:
    now = timeutil.utc_now_iso()
    prepared = trial.request_trial(args.role_id, args.trial_request, now=now,
                                   ttl_minutes=args.ttl_minutes)
    permission_decision = prepared["permission_decision"]
    request = prepared["approval_request"]

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
        _record_audit_gap(ledger, "approval_request", exc, subject_ref=request["approval_id"], now=now)
        sys.stderr.write(f"WARNING: request audit failed ({exc.reason_code}); the request stands\n")

    history = None
    history_failure = ""
    try:
        history = approval.decision_history(store, request)
    except MvpRuntimeError as exc:
        history_failure = f"\n과거 유사 결정: 조회 실패 ({exc.reason_code}) — 이력 없이 요청합니다\n"

    sys.stdout.write(approval.request_message(request, permission_decision, history=history) + "\n")
    if history_failure:
        sys.stdout.write(history_failure)
    role = prepared["role"]
    sys.stderr.write(
        f"\nSTORED: {request['approval_id']} is PENDING until {request['validity']['expires_at']}.\n"
        f"Trial bound to {role['role_id']}@{role['version']} "
        f"(definition {role['definition_sha256'][:12]}…) and this exact task text.\n"
        "Send it to Thomas on the verified control channel; he answers with /approve <id> [reason] "
        "or /reject <id> [reason]. Then run:  python -m runtime.mvp_runtime.trial_cli run "
        f"{request['approval_id']}\n"
    )
    return EXIT_OK


def _run(args: argparse.Namespace) -> int:
    # Same gated provider selection as the main CLI. The analysis MockProvider maps to
    # None so trial.run_trial substitutes its role-aware MockTrialProvider.
    provider = select_provider()
    validator_provider = select_validator_provider()
    gate_banners(provider=provider)
    result = trial.run_trial(
        args.approval_id,
        provider=None if isinstance(provider, MockProvider) else provider,
        validator_provider=validator_provider,
    )

    consumed = result["approval"]
    sys.stderr.write(
        f"CONSUMED {consumed['approval_id']} -> {consumed['consumption']['consumption_ref']} "
        "(one-time use; a failed run does not refund the grant)\n"
    )
    persist_error = result.get("persist_error")
    if persist_error is None:
        sys.stderr.write(f"LEDGER: recorded to {LedgerStore.default().root}\n")
    else:
        sys.stderr.write(
            f"LEDGER: NOT recorded ({persist_error}) — this trial has no durable audit trail\n"
        )
    report = result["records"].get("trial_report")
    if report is not None:
        sys.stderr.write(
            f"TRIAL REPORT: {report['role_id']}@{report['role_version']} -> "
            f"{report['final_result']} (automatic {report['automatic_result']}, "
            f"independent {report['independent_result'] or '—'}); promotion_effect=NONE\n"
        )
    if result["status"] == "COMPLETED":
        sys.stdout.write(result["final_response"] + "\n")
        return EXIT_OK
    block = result["block"] or {"reason_code": "BLOCKED", "message": "unknown"}
    sys.stderr.write(f"BLOCKED {block['reason_code']}: {block['message']}\n")
    return EXIT_BLOCKED


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    request = sub.add_parser("request", help="ask Thomas to approve one isolated candidate-role trial")
    request.add_argument("role_id", help="candidate role id (e.g. research.general, translation.general)")
    request.add_argument("trial_request", help="the exact trial task text (bound into the approval)")
    request.add_argument("--ttl-minutes", type=int, default=None,
                         help="approval lifetime (default: the policy maximum for the scope)")
    request.set_defaults(func=_request)

    run = sub.add_parser("run", help="spend an APPROVED trial grant and run the trial once (gated)")
    run.add_argument("approval_id")
    run.set_defaults(func=_run)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except MvpRuntimeError as exc:
        return report_block(exc)


if __name__ == "__main__":
    raise SystemExit(main())
