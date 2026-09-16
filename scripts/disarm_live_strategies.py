#!/usr/bin/env python3
"""Operator tool: take strategies OUT of the pool's LIVE tier (Thomas decision 10, 2026-09-15).

    # What is armed right now?
    python -m scripts.disarm_live_strategies --list

    # Disarm named strategies, or every armed one. No approval, applied at once.
    python -m scripts.disarm_live_strategies --strategy-ids S001,S004 --disarmed-by thomas --reason "..."
    python -m scripts.disarm_live_strategies --all --disarmed-by thomas --reason "drawdown review"

Arming is Thomas's approved decision (``scripts/promote_strategy_candidates.py --live-tier LIVE``);
disarming is nobody's approval to wait for. It is the same asymmetry the stage ladder runs on — a
demotion needs no approval because a stop that needs one is not a stop — and the same asymmetry the
pool's automatic writer already has: ``pool.disarm_live_tier`` cannot write any tier but
OBSERVATION, so this door cannot arm anything however it is called.

What disarming does and does not do: a disarmed strategy keeps its slot and keeps papering, and it
can no longer open a REAL position. Positions it already holds are unaffected — settlement,
protection, the time exit and reconciliation never read the tier, exactly as they never read the
execution stage. To stop entries machine-wide instead, demote the execution stage
(``scripts/register_execution_stage.py --demote``) or halt trading on the control channel.

Run it in the scheduler container as the service user (it writes governed state):
``docker exec -u 10001 thomas-scheduler python -m scripts.disarm_live_strategies ...``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE, force_utf8_io  # noqa: E402
from runtime.mvp_runtime.crypto import pool as pool_store  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.events import stamped_event  # noqa: E402
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run  # noqa: E402
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore  # noqa: E402

DISARM_EVENT_TYPE = "crypto_strategy_disarm_event.v0"


def armed_strategy_ids(root: Path | None) -> list[str]:
    return sorted(pool_store.live_routable_strategy_ids(pool_store.load_active_pool(root)))


def run_disarm(*, strategy_ids: list[str], disarmed_by: str, reason: str,
               root: Path | None = None, now: str | None = None) -> dict:
    """Move the named strategies to OBSERVATION and record it. Returns the ledger summary."""
    now = now or timeutil.utc_now_iso()
    armed_before = armed_strategy_ids(root)
    moved = pool_store.disarm_live_tier(
        strategy_ids, root=root, now=now,
        reasons=[f"operator disarm by {disarmed_by}: {reason}"],
    )
    summary = {
        "requested": sorted(strategy_ids),
        "armed_before": armed_before,
        "armed_after": armed_strategy_ids(root),
        "disarmed": moved,
        "disarmed_by": disarmed_by,
        "reason": reason,
        # Stated on the record, because the whole point of this door is that nothing was waited on.
        "approval_id": None,
        "approval_required": False,
        "created_at": now,
    }
    LedgerStore((root if root is not None else ROOT) / LEDGER_REL).append_control(
        stamped_event(DISARM_EVENT_TYPE, **summary)
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--list", action="store_true", help="print the armed strategies and exit")
    parser.add_argument("--strategy-ids", help="comma-separated strategy ids to disarm")
    parser.add_argument("--all", action="store_true", help="disarm every armed strategy")
    parser.add_argument("--disarmed-by", help="operator identity")
    parser.add_argument("--reason", help="why (recorded on the pool entry and the ledger)")
    parser.add_argument("--root", type=Path, default=None, help="state root (defaults to the repo)")
    args = parser.parse_args(argv)
    root = args.root

    try:
        if args.list:
            armed = armed_strategy_ids(root)
            sys.stdout.write(("armed (LIVE tier): " + ", ".join(armed) if armed
                              else "armed (LIVE tier): none") + "\n")
            return EXIT_OK
        if args.all and args.strategy_ids:
            sys.stderr.write("USAGE: --all and --strategy-ids are exclusive\n")
            return EXIT_USAGE
        ids = armed_strategy_ids(root) if args.all else [
            s.strip() for s in (args.strategy_ids or "").split(",") if s.strip()
        ]
        if not ids or not (args.disarmed_by and args.reason):
            sys.stderr.write("USAGE: --strategy-ids <ids> (or --all) with --disarmed-by and --reason\n")
            return EXIT_USAGE
        # After the read-only branch and the usage checks, before the write: a host-side root run
        # would leave the pool and the ledger owned by a uid the services cannot write again.
        assert_not_foreign_root_run(root)
        out = run_disarm(strategy_ids=ids, disarmed_by=args.disarmed_by, reason=args.reason, root=root)
    except MvpRuntimeError as exc:
        sys.stderr.write(f"BLOCKED {exc.reason_code}: {exc}\n")
        return EXIT_BLOCKED
    sys.stdout.write(
        f"DISARMED {out['disarmed']} of {len(out['requested'])} named; "
        f"armed now: {', '.join(out['armed_after']) or 'none'}\n"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
