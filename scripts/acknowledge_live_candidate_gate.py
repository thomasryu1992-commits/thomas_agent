"""Answer Gate 0 yourself (``crypto_live_candidate_ack.v0.1``). Operator step.

``docs/runtime-contracts/CRYPTO_LIVE_EXECUTION_V0.1.md`` puts Gate 0 on the operator go-live
checklist — *"Paper trading by this runtime shows positive expectancy over a sustained window"* —
and tells the operator to check it on the dashboard. #400/#409 turned that into an automatic
refusal computed over a pool aggregate, which refuses every strategy's entries for any one
strategy's record. This writes the operator's own answer instead, as a signed, expiring record
bound to the exact routable pool it was signed for.

    # see what would be signed, and what the computed answer currently says. Writes nothing.
    python -m scripts.acknowledge_live_candidate_gate --show

    # sign it for the pool that is routable right now, for 7 days
    python -m scripts.acknowledge_live_candidate_gate --registered-by thomas \
        --reason "1h/4h pool promoted 2026-07-31; accepting the backtest evidence" --valid-days 7

    # withdraw it — the computed answer takes over again immediately
    python -m scripts.acknowledge_live_candidate_gate --withdraw

**This does not remove the gate and it grants no permission by itself.** `live_entry`'s refusal
is untouched; what changes is who answers it. The `live_trading` grant, the confirmation phrase,
the canary evidence, the registered budget, the loss breakers over live outcomes and both kill
switches all still stand exactly where they stood. What it *does* do is let live entries begin
on the strategies named below, so read `--show` before running it.

Three properties to know:

- **It is bound to the pool.** The record names the routable strategy ids at signing time and
  applies only while that set is exactly those ids. Promote a strategy or let the ladder demote
  one and the acknowledgement stops applying — deliberately, so a signature for one pool cannot
  authorise the next.
- **It expires.** Past ``--valid-days`` the gate returns to its computed answer on its own. A
  checklist tick is a judgement about a moment.
- **The measurement is never overwritten.** The cycle record keeps reporting what the evidence
  says alongside what you signed, so the disagreement stays legible in the ledger.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runtime.mvp_runtime.crypto import feedback, live_candidate_ack, pool
from runtime.mvp_runtime.errors import MvpRuntimeError, ToolError
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run

_ISO = "%Y-%m-%dT%H:%M:%SZ"

EXIT_OK = 0
EXIT_REFUSED = 2


def _now() -> str:
    return datetime.now(timezone.utc).strftime(_ISO)


def _routable(root: Path | None) -> set[str]:
    return pool.routable_strategy_ids(pool.load_active_pool(root))


def _show(root: Path | None, now: str) -> int:
    routable = _routable(root)
    print("Gate 0 — the live-candidate gate\n")
    print(f"  routable strategies ({len(routable)}):")
    for sid in sorted(routable):
        print(f"    - {sid}")

    report, _text = feedback.run_paper_performance_report(
        now=now, root=root, routable_strategy_ids=routable
    )
    print("\n  what the EVIDENCE says:")
    print(f"    eligible        : {report['live_candidate_eligible']}")
    print(f"    closed sample   : {report['sample_size']} of {feedback.LIVE_CANDIDATE_MIN_SAMPLE} needed")
    print(f"    failure modes   : {', '.join(report['failure_modes']) or 'none'}")

    state = live_candidate_ack.resolve_ack(root, now=now, routable_strategy_ids=routable)
    record = state.get("record")
    print("\n  what the OPERATOR has signed:")
    if record is None:
        print(f"    nothing registered ({state['reason']})")
    else:
        print(f"    acknowledgement : {record['acknowledgement_id']}")
        print(f"    signed by       : {record['registered_by']} at {record['registered_at']}")
        print(f"    reason          : {record['reason']}")
        print(f"    window          : {record['valid_from']} .. {record['valid_until']}")
        print(f"    covers          : {', '.join(record['acknowledged_strategy_ids'])}")
        print(f"    applies now     : {state['applies']}  ({state['reason']})")
    print(f"\n  record path       : {live_candidate_ack.ack_path(root)}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="acknowledge_live_candidate_gate", description=__doc__)
    parser.add_argument("--show", action="store_true",
                        help="print the pool, the computed answer and any signed record; write nothing")
    parser.add_argument("--withdraw", action="store_true",
                        help="delete the registered acknowledgement; the computed answer takes over")
    parser.add_argument("--registered-by", help="operator identity recorded on the acknowledgement")
    parser.add_argument("--reason", help="what you checked, in your own words (required to sign)")
    parser.add_argument("--valid-days", type=float, default=7.0,
                        help="how long the acknowledgement applies (default 7)")
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)

    root: Path | None = args.root
    now = _now()

    try:
        # `--show` reads and writes nothing, so it runs anywhere — deliberately BEFORE the root
        # guard below. That guard exists because a host-side root run leaves governed state the
        # uid-10001 services can no longer write; a read has no such consequence, and making the
        # one command whose whole job is "look before you sign" refuse on a host shell would
        # push an operator toward signing without looking.
        if args.show:
            return _show(root, now)

        assert_not_foreign_root_run(root)

        if args.withdraw:
            path = live_candidate_ack.ack_path(root)
            if not path.is_file():
                print("nothing registered; the computed answer was already the one in force")
                return EXIT_OK
            path.unlink()
            print(f"withdrawn: {path}\nGate 0 is back to its computed answer.")
            return EXIT_OK

        if not args.registered_by or not args.reason:
            print("--registered-by and --reason are both required to sign. Try --show first.",
                  file=sys.stderr)
            return EXIT_REFUSED

        routable = _routable(root)
        if not routable:
            print("no routable strategies — there is nothing to acknowledge. Promote a pool first.",
                  file=sys.stderr)
            return EXIT_REFUSED

        valid_until = (
            datetime.strptime(now, _ISO).replace(tzinfo=timezone.utc)
            + timedelta(days=args.valid_days)
        ).strftime(_ISO)

        record = live_candidate_ack.build_ack_record(
            acknowledged_strategy_ids=sorted(routable),
            reason=args.reason,
            valid_from=now,
            valid_until=valid_until,
            registered_by=args.registered_by,
            registered_at=now,
        )
        path = live_candidate_ack.write_ack(record, root=root)
    except (ToolError, MvpRuntimeError) as exc:
        print(f"REFUSED [{getattr(exc, 'reason_code', type(exc).__name__)}] {exc}", file=sys.stderr)
        return EXIT_REFUSED

    print(f"registered: {record['acknowledgement_id']}")
    print(f"  covers  : {', '.join(record['acknowledged_strategy_ids'])}")
    print(f"  window  : {record['valid_from']} .. {record['valid_until']}")
    print(f"  path    : {path}")
    print("\nGate 0 will now read this instead of the computed figure, until the window closes or")
    print("the routable pool changes. Every other live door is unchanged.")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
