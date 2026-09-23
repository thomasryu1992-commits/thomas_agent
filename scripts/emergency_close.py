#!/usr/bin/env python3
"""Operator tool: the emergency close (crypto PR6c; Thomas decision 49, 2026-09-19).

Closes every live position this machine has booked, at market and reduceOnly, under the HARD halt,
on one single-use approval Thomas gives on the control channel. It opens nothing and resizes
nothing, and a position the venue holds that the book does not is never touched.

    # What would a close bind? Read-only: the control state and the book.
    python -m scripts.emergency_close --show

    # 1) ASK Thomas. Stores the PENDING approval and sends nothing. Needs the HARD halt with the
    #    runtime ACTIVE (console_cli halt_trading hard --reason "..." first).
    python -m scripts.emergency_close --request --requested-by thomas --reason "..."

    # 2) Thomas answers /approve <id> on the control channel. The ask expires after 15 minutes.
    # 3) Spend the approval once and close:
    python -m scripts.emergency_close --confirm --approval-id approval_abc123

Run it in the scheduler container as the service user. That is the only container with the
live-trading environment, and the closes are signed with its order key:
``docker exec -u 10001 thomas-scheduler python -m scripts.emergency_close ...``.

**Asking and spending are separate invocations, and this tool never does both.** The confirm checks
everything it can without the venue before it spends, so a refusal there leaves the approval
APPROVED and sends nothing:

- the HARD halt it was approved under is still the one in effect;
- the live gate is open;
- the confirmation phrase the close guard needs is set;
- an approved position is still booked;
- the account can be read.

The account is read once, just before the spend. After the spend, each position is judged again just
before its close, against that read and a fresh read of the halt and the book. It is skipped when the
halt, the book or the venue moved, and the report says which. The report is also kept on the record
ledger under the approval id. The skip rules are in ``live_route.run_emergency_close``.

Two confirms of one grant close once: the spend is a single-use compare-and-set before any close. The
loser is refused ``ALREADY_CONSUMED``, including when it meets the book or the venue the winner has
already emptied (2026-09-23).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import approval as approval_mod  # noqa: E402
from runtime.mvp_runtime import permission, timeutil  # noqa: E402
from runtime.mvp_runtime.approval_store import ApprovalStore  # noqa: E402
from runtime.mvp_runtime.audit import build_approval_request_audit  # noqa: E402
from runtime.mvp_runtime.binding import bind_task_to_core  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE, force_utf8_io  # noqa: E402
from runtime.mvp_runtime.control import ControlStore  # noqa: E402
from runtime.mvp_runtime.crypto import live_route  # noqa: E402
from runtime.mvp_runtime.errors import ApprovalBlocked, MvpRuntimeError, ToolError  # noqa: E402
from runtime.mvp_runtime.intake import build_task  # noqa: E402
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run  # noqa: E402
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore  # noqa: E402

# The grant is not an emergency close, or it does not carry what one binds.
NOT_A_CLOSE_GRANT = "EMERGENCY_CLOSE_NOT_A_CLOSE_GRANT"
GRANT_INCOMPLETE = "EMERGENCY_CLOSE_GRANT_INCOMPLETE"


def _stores(root: Path | None) -> tuple[ApprovalStore, LedgerStore]:
    base = root if root is not None else ROOT
    return ApprovalStore.default(base), LedgerStore(base / LEDGER_REL)


def _control(root: Path | None) -> ControlStore:
    return ControlStore(root) if root is not None else ControlStore.default()


def _listing(rows: list[dict]) -> str:
    return ", ".join(f"{r['symbol']} {r['direction']} {r['quantity']} ({r['position_id']})" for r in rows)


def run_show(*, root: Path | None, as_json: bool) -> int:
    """What a close would bind now, or why none can be asked for. Reads; writes nothing."""
    state = _control(root).load()
    try:
        rows = live_route.emergency_close_rows(live_route.list_open_live_positions(root))
        book_error = None
    except MvpRuntimeError as exc:
        rows, book_error = [], exc.reason_code
    # The same refusals the ask makes, in its order, so --show never says an ask can be made that
    # --request would refuse (review of #913).
    problem = live_route.emergency_halt_problem(state, None) or (
        live_route.emergency_book_problem(rows) if rows else None)
    if as_json:
        sys.stdout.write(json.dumps({"mode": state.mode, "halt_level": state.halt_level,
                                     "askable": problem is None and bool(rows), "problem": problem,
                                     "book_error": book_error, "positions": rows},
                                    ensure_ascii=False, indent=2) + "\n")
        return EXIT_OK
    lines = [
        f"control state : {state.mode}" + (f", {state.halt_level} halt" if state.halt_level else ", no halt"),
        f"booked        : {len(rows)} position(s)" + (f" - {_listing(rows)}" if rows else "")
        + (f" (the book could not be read: {book_error})" if book_error else ""),
        "a close ask   : " + ("can be made (--request)" if problem is None and rows else
                             f"cannot be made - {problem or 'nothing is booked'}"),
    ]
    sys.stdout.write("\n".join(lines) + "\n")
    return EXIT_OK


def run_request(*, root: Path | None, now: str, requested_by: str, reason: str) -> dict:
    """Store the PENDING ask for closing every booked position under the HARD halt in effect. Sends
    nothing and changes nothing else."""
    approvals, ledger = _stores(root)
    assert_not_foreign_root_run(root)
    content = live_route.emergency_close_content(root=root, requested_by=requested_by, reason=reason,
                                                 control_store=_control(root))
    rows = content["positions"]
    # `root` is the STATE root. The Core binding, the policy and the schemas come from the image's own
    # tree, like every other ask (repo_root=None).
    task = build_task(
        f"긴급 청산 검토: 장부의 라이브 포지션 {len(rows)}개({', '.join(r['symbol'] for r in rows)})를 "
        f"시장가 reduceOnly로 청산 - {reason}",
        now=now, channel="manual", requester_type="real_thomas", requester_id="Thomas",
        authenticated=True,
    )
    _binding, bound = bind_task_to_core(task, now=now)
    decision = permission.build_emergency_close_permission_decision(bound, content=content, now=now)
    request = approval_mod.build_approval_request(decision, now=now)
    approvals.append_permission_decision(decision)
    approvals.append([request])
    warnings = []
    try:
        ledger.append_audit_events(build_approval_request_audit(
            request, now=now, genesis_previous_hash=ledger.last_audit_hash(),
        ))
    except MvpRuntimeError as exc:
        warnings.append(f"the request audit was not written ({exc.reason_code}); the request stands")
    return {"approval_id": request["approval_id"], "expires_at": request["validity"]["expires_at"],
            "content": content, "warnings": warnings}


# The refusals a losing confirm meets when the winner has already closed the positions: raised
# before this confirm's spend, so they say "nothing was spent", which is true of this confirm alone.
_LOSER_REFUSALS = frozenset({live_route.EMERGENCY_CLOSE_NOTHING_BOOKED, live_route.EMERGENCY_CLOSE_NOTHING_CLOSABLE})


def run_confirm(*, root: Path | None, now: str, approval_id: str, clock=None) -> dict:
    """Spend the APPROVED emergency-close grant once and close what it names.

    ``clock`` reads the time again at the spend: two signed account reads can sit between the check
    above and the spend, and an approval must not be spent after it expired (review of #913)."""
    approvals, ledger = _stores(root)
    assert_not_foreign_root_run(root)
    control = _control(root)
    approval, decision, snapshot = approval_mod.validate_spendable_approval(
        approvals, approval_id, now=now, control_state=control.load(),
        expected_scope=permission.EMERGENCY_CLOSE_PERMISSION_SCOPE,
        kill_action="spending an emergency-close grant", refusal_phrase="nothing is closed",
        scope_refusal="is not an emergency-close grant",
    )
    target_ref = str(snapshot.get("target_ref") or "")
    if not target_ref.startswith(permission.EMERGENCY_CLOSE_TARGET_PREFIX):
        raise ApprovalBlocked(NOT_A_CLOSE_GRANT, f"{approval_id} is not an emergency-close grant")
    content = snapshot.get("normalized_parameters") or {}
    halt_ref, positions = content.get("halt_ref"), content.get("positions")
    if not (isinstance(halt_ref, str) and halt_ref and isinstance(positions, list) and positions):
        raise ApprovalBlocked(GRANT_INCOMPLETE,
                              f"{approval_id} does not name the halt and the positions it closes; ask again")

    def spend() -> None:
        # One exclusion for the compare-and-set: the approval re-read APPROVED and unexpired, the halt
        # Thomas approved still the one in effect, then CONSUMED. Spent before anything is sent, so a
        # failure after it is spent-but-unrun, the safe direction (ask again).
        with approval_mod.spend_lock(approvals, approval_id):
            spent_at = clock() if clock is not None else now
            fresh = approvals.get(approval_id)
            if approval_mod.is_expired(fresh, now=spent_at):
                raise ApprovalBlocked("APPROVAL_EXPIRED",
                                      f"approval expired at {fresh['validity']['expires_at']} before it could "
                                      "be spent; nothing was spent")
            problem = live_route.emergency_halt_problem(control.load(), halt_ref)
            if problem is not None:
                raise ToolError(live_route.EMERGENCY_CLOSE_HALT_CHANGED, f"{problem}; nothing was spent")
            consumed = approval_mod.build_consumed_record(
                fresh, decision, consumed_at=spent_at, consumption_ref=target_ref,
            )
            approvals.append([consumed])

    try:
        report = live_route.run_emergency_close(positions, halt_ref=halt_ref, spend=spend, now=now, root=root,
                                                control_store=control)
    except ToolError as exc:
        # Two confirms of one grant (2026-09-23, Thomas: option B). The loser validated the grant
        # APPROVED above; if the winner then closed the positions, the loser met an empty book or a
        # flat venue and was refused "nothing was spent" - true of the loser, but it hid that the
        # grant was already spent. Only the CODE of those two refusals changes: whether to refuse,
        # the order of the checks, the spend and the closes are live_route's, untouched here.
        if exc.reason_code in _LOSER_REFUSALS:
            latest = approvals.get(approval_id)
            if latest is not None and latest.get("status") == approval_mod.STATUS_CONSUMED:
                raise ApprovalBlocked(
                    "ALREADY_CONSUMED",
                    f"approval {approval_id} was spent by a concurrent --confirm, which closed what it "
                    f"could; this one sent nothing ({exc.reason_code}; --show says what is booked now)",
                ) from exc
        raise
    report = {**report, "approval_id": approval["approval_id"], "target_ref": target_ref}
    # The report is kept, not only printed (review of #913): one row per spent grant beside the audit
    # events and the outcomes, naming every order it sent. The closes stand whether or not it lands.
    warnings = []
    try:
        ledger.append_records(approval["approval_id"], {"crypto_emergency_close": report})
    except (MvpRuntimeError, OSError) as exc:
        warnings.append(f"the report was not recorded ({getattr(exc, 'reason_code', type(exc).__name__)}); "
                        "the closes above stand, and their audit events and outcomes are on record")
    return {"approval_id": approval["approval_id"], "report": report, "warnings": warnings}


def _report_lines(report: dict) -> list[str]:
    lines = []
    for row in report["positions"]:
        line = f"{row['status']:<24}{row['symbol']} {row['direction']} {row['quantity']} ({row['position_id']})"
        if row.get("outcome_id"):
            line += f" outcome {row['outcome_id']}"
        if row.get("detail"):
            line += f" - {row['detail']}"
        if row.get("reason_codes"):
            line += f" [{', '.join(row['reason_codes'])}]"
        lines.append(line)
        order = row.get("order")
        if order and row["status"] != live_route.EMERGENCY_CLOSED:
            # Sent, and not confirmed closed: what went out and what the venue said, so the operator can
            # find it at the venue.
            lines.append(f"{'':<24}order {order.get('client_order_id')} (venue {order.get('exchange_order_id')}): "
                         f"{order.get('reconcile_status')}, filled {order.get('executed_qty')}"
                         + (f"; {'; '.join(order['mismatches'])}" if order.get("mismatches") else ""))
    for held in report.get("booked_not_in_grant") or ():
        lines.append(f"{'BOOKED, NOT IN GRANT':<24}{held['symbol']} {held['direction']} {held['quantity']} "
                     f"({held['position_id']}) - booked after the ask; ask again to close it")
    for held in report.get("untracked_at_venue") or ():
        lines.append(f"{'NOT BOOKED, NOT TOUCHED':<24}{held['symbol']} {held.get('side')} {held.get('venue_quantity')} "
                     "- the venue holds it and the book does not; close it at the venue if it must go")
    closed = sum(1 for row in report["positions"] if row["status"] == live_route.EMERGENCY_CLOSED)
    gone = sum(1 for row in report["positions"]
               if row["status"] in (live_route.EMERGENCY_SKIPPED_NOT_BOOKED, live_route.EMERGENCY_SKIPPED_CLOSED_AT_VENUE))
    lines.append(f"{report['status']}: {closed} of {len(report['positions'])} closed"
                 + (f", {gone} already gone" if gone else "") + "; the approval is spent"
                 + (f" [{', '.join(report['live_reason_codes'])}]" if report["live_reason_codes"] else ""))
    return lines


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--show", action="store_true")
    mode.add_argument("--request", action="store_true")
    mode.add_argument("--confirm", action="store_true")
    parser.add_argument("--requested-by")
    parser.add_argument("--reason")
    parser.add_argument("--approval-id")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", type=Path, default=None, help="state root (defaults to the repo)")
    args = parser.parse_args(argv)
    now = timeutil.utc_now_iso()
    try:
        if args.show:
            return run_show(root=args.root, as_json=args.json)
        if args.request:
            if not (args.requested_by and args.reason):
                sys.stderr.write("USAGE: --request needs --requested-by and --reason\n")
                return EXIT_USAGE
            out = run_request(root=args.root, now=now, requested_by=args.requested_by, reason=args.reason)
            for warning in out["warnings"]:
                sys.stderr.write(f"WARNING: {warning}\n")
            sys.stdout.write(
                f"ASKED: close {len(out['content']['positions'])} booked position(s) - "
                f"{_listing(out['content']['positions'])}; approval {out['approval_id']} until "
                f"{out['expires_at']}\n"
                f"Thomas answers /approve {out['approval_id']} (or /reject); then run --confirm --approval-id "
                f"{out['approval_id']}. Nothing has been sent.\n"
            )
            return EXIT_OK
        if not args.approval_id:
            sys.stderr.write("USAGE: --confirm needs --approval-id\n")
            return EXIT_USAGE
        out = run_confirm(root=args.root, now=now, approval_id=args.approval_id, clock=timeutil.utc_now_iso)
    except MvpRuntimeError as exc:
        sys.stderr.write(f"BLOCKED {exc.reason_code}: {exc}\n")
        return EXIT_BLOCKED
    for warning in out["warnings"]:
        sys.stderr.write(f"WARNING: {warning}\n")
    report = out["report"]
    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write("\n".join(_report_lines(report)) + "\n")
    # BLOCKED means an approved position is still open as far as the runtime knows.
    return EXIT_OK if report["status"] == "COMPLETE" else EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
