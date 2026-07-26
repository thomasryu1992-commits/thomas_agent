"""Record, late, a live order that reached the venue and left nothing on the audit chain.

    python -m scripts.record_unreported_live_order --canary-order-id canary_a30509... \
        --reason "LedgerStore(None) crashed step 8 before the append (PR #228)"

An operator tool for one narrow situation: a real order filled, its canary registry row was
written, and the audit append then failed — so `p5_policy_gate`'s `post_action_report_and_audit`
is unmet for money that has already moved. This writes the statement of that gap.

**What it is not.** It does not repair the ledger, and it cannot.
`AUDIT_RECOVERY_CONSOLE_V0.1` is explicit that the trail is diagnosed and never repaired,
because a damaged trail is evidence; and mechanically `append_audit_events` re-anchors onto the
current tip, so nothing can be inserted into the past. This appends **forward**: the original
hole stays a hole, and a new event says what belongs in it. Anyone reading the chain sees both
the gap and the account of it, which is the honest shape.

It also does not reconstruct the order's P5 PermissionDecision. That decision was prepared, the
order filled, and the process died with the decision id, trace id, and bound task still only in
memory. Those are gone. The event says so in `ORIGINAL_PERMISSION_DECISION_UNRECOVERABLE` rather
than minting a stand-in that would read like the report it is not.

**Narrow by construction.** One purpose, one event, one canary order per invocation, and it
refuses a second recording of the same order. A general "append an operator note" verb would be
a much wider write door into the audit ledger than this need justifies.

Deliberately places no order and touches no venue: the only network read is the canary registry
and the ledger, both local.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE, force_utf8_io
from runtime.mvp_runtime.crypto import live_governance, live_promotion
from runtime.mvp_runtime.errors import MvpRuntimeError
from runtime.mvp_runtime.store import LedgerStore

# The marker that makes a recording findable again, and therefore refusable a second time.
RECOVERED_REASON_CODE = "LIVE_ORDER_REPORT_RECOVERED"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="record_unreported_live_order",
        description="Record, late, a live order that was never written to the audit chain.",
    )
    parser.add_argument("--canary-order-id", required=True,
                        help="the canary registry row identifying the unreported order")
    parser.add_argument("--reason", required=True,
                        help="why the original audit event is missing (recorded verbatim)")
    parser.add_argument("--json", action="store_true", help="emit the event as JSON")
    parser.add_argument("--root", type=Path, default=None, help="state root (defaults to the repo)")
    return parser.parse_args(argv)


def already_recorded(events: list[dict], canary_order_id: str) -> str | None:
    """The audit id of an existing recording for this order, or None.

    Idempotency matters more here than in most places: a second recording of one order would
    make the chain assert the same real money twice, which is a different kind of wrong from
    the gap it is trying to describe.
    """
    for event in events:
        subject = event.get("subject") if isinstance(event.get("subject"), dict) else {}
        body = event.get("event") if isinstance(event.get("event"), dict) else {}
        codes = body.get("reason_codes") or []
        if subject.get("subject_id") == canary_order_id and RECOVERED_REASON_CODE in codes:
            return str(event.get("audit_event_id") or "unknown")
    return None


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    args = _parse_args(argv)
    if not args.reason.strip():
        sys.stderr.write("ERROR: --reason must not be empty\n")
        return EXIT_USAGE

    root = args.root
    now = timeutil.utc_now_iso()

    try:
        # 1. The evidence. The registry row was written BEFORE the crash, which is the only
        #    reason anything can be said about the order at all.
        records = live_promotion.read_canary_orders(root)
        matches = [r for r in records if r.get("canary_order_id") == args.canary_order_id]
        if not matches:
            sys.stderr.write(
                f"BLOCKED CANARY_ORDER_NOT_FOUND: no canary registry row with id "
                f"{args.canary_order_id!r}; {len(records)} row(s) present. This tool records only "
                "orders the registry already evidences — it will not assert one from a flag.\n"
            )
            return EXIT_BLOCKED
        canary_record = matches[0]

        # 2. Refuse a second recording of the same order.
        store = LedgerStore.default(root)
        existing = already_recorded(store.read_audit_events(), args.canary_order_id)
        if existing is not None:
            sys.stderr.write(
                f"BLOCKED ALREADY_RECORDED: {args.canary_order_id} was already recorded by audit "
                f"event {existing}. Recording it twice would make the chain assert the same order "
                "twice.\n"
            )
            return EXIT_BLOCKED

        # 3. The Core-bound task for the RECORDING. Fails closed without an active Core.
        recording = live_governance.prepare_unreported_order_recording(
            canary_record, now=now, repo_root=root,
        )

        # 4. The event, then the append. Unlike the order path there is no point of no return
        #    before this line, so an ordinary failure here simply means nothing was recorded.
        event, sha = live_governance.report_unreported_live_order(
            recording, canary_record, reason=args.reason, now=now, repo_root=root,
        )
        store.append_audit_events([event])
    except MvpRuntimeError as exc:
        sys.stderr.write(f"BLOCKED {exc.reason_code}: {exc.reason}\n")
        return EXIT_BLOCKED

    if args.json:
        sys.stdout.write(json.dumps({"event": event, "event_sha256": sha},
                                    ensure_ascii=False, indent=1, default=str) + "\n")
    else:
        sys.stdout.write(
            f"recorded {event['audit_event_id']}\n"
            f"  order     : {canary_record.get('symbol')} "
            f"exchange_order_id={canary_record.get('exchange_order_id')}\n"
            f"  placed    : {canary_record.get('recorded_at_utc')}\n"
            f"  recorded  : {now} (forward append — the original gap remains visible)\n"
            f"  evidence  : {canary_record.get('record_sha256')}\n"
            f"  ledger    : {store.root}\n"
        )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
