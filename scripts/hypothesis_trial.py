#!/usr/bin/env python3
"""Operator tool: the hypothesis trials (``crypto/forward_trial.py``; HYPOTHESIS_TRIAL_V0.1, option C).

Two subcommands:

- ``list`` prints every trial beside its coin-flip twin: forward trades, mean net R, the judge's
  status, its own backtest holdout, and whether it is open or closed. Reads only.
- ``close <candidate_id> --graduate|--retire --reason TEXT`` is Thomas's decision on one trial.
  It frees the trial's slot under ``factory.MAX_OPEN_TRIALS``, stops its walk and its twin's, and
  seals the forward record as it stood. Dry unless ``--apply``. ``--graduate`` is refused unless the
  trial's own holdout is CONFIRMED (Q5): graduating RECORDS the intent — installing the family is
  still a code change in ``factory.TEMPLATES``, by PR. Closing never re-queues the proposal.

Writes runtime state, so it runs in the container as uid 10001, in module form::

    docker exec thomas-scheduler python -m scripts.hypothesis_trial list
    docker exec thomas-scheduler python -m scripts.hypothesis_trial close cand_... --retire --reason "..."
    docker exec thomas-scheduler python -m scripts.hypothesis_trial close cand_... --retire --reason "..." --apply

A damaged candidate or trial store refuses the run (``EXIT_BLOCKED``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run  # noqa: E402
from runtime.mvp_runtime.crypto import forward_trial  # noqa: E402


def _mean(value) -> str:
    return "-" if value is None else f"{value:+.3f}"


def _list(root: Path) -> int:
    lines = forward_trial.trial_report(root)
    if not lines:
        print("no hypothesis trial minted yet")
        return EXIT_OK
    print("%-26s %-4s %-32s %-8s %-13s %5s %8s %-22s | twin %5s %8s %s" % (
        "candidate", "tf", "family", "state", "holdout", "n", "mean_R", "status",
        "n", "mean_R", "status"))
    for line in lines:
        twin = line.get("twin") or {}
        close = line.get("close")
        print("%-26s %-4s %-32s %-8s %-13s %5s %8s %-22s | twin %5s %8s %s" % (
            line["candidate_id"], line.get("timeframe") or "", line.get("strategy_family") or "",
            close["decision"] if close else "open", line.get("holdout_status") or "",
            line.get("priceable_count", 0), _mean(line.get("mean_net_r")), line.get("status"),
            twin.get("priceable_count", 0), _mean(twin.get("mean_net_r")), twin.get("status")))
    open_count = sum(1 for line in lines if line.get("close") is None)
    print(f"\n{open_count} open of {forward_trial.MAX_OPEN_TRIALS} slots; {len(lines) - open_count} closed")
    return EXIT_OK


def _close(root: Path, args: argparse.Namespace, now: str) -> int:
    decision = forward_trial.CLOSE_GRADUATE if args.graduate else forward_trial.CLOSE_RETIRE
    record = forward_trial.close_trial(root, args.candidate_id, decision=decision, reason=args.reason,
                                       now=now, apply=args.apply)
    print(f"{record['candidate_id']}: {record['decision']} — {record['reason']}")
    print(f"  forward at close: {record['forward_at_close']}")
    print(f"  twin at close:    {record['twin_at_close']}")
    if decision == forward_trial.CLOSE_GRADUATE:
        print("  Graduation installs nothing: the family goes into factory.TEMPLATES by PR.")
    print("CLOSED" if args.apply else "DRY RUN — nothing written. Re-run with --apply.")
    return EXIT_OK


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hypothesis_trial", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    close = sub.add_parser("close")
    close.add_argument("candidate_id")
    which = close.add_mutually_exclusive_group(required=True)
    which.add_argument("--graduate", action="store_true", help="Thomas will install it (holdout CONFIRMED only)")
    which.add_argument("--retire", action="store_true", help="the trial ends here")
    close.add_argument("--reason", required=True, help="why, in a sentence; sealed into the record")
    close.add_argument("--apply", action="store_true", help="write; without it nothing is written")
    args = parser.parse_args(argv)
    root = root if root is not None else ROOT

    try:
        assert_not_foreign_root_run(None)
    except MvpRuntimeError as exc:
        print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return EXIT_BLOCKED
    try:
        if args.command == "list":
            return _list(root)
        return _close(root, args, timeutil.utc_now_iso())
    except MvpRuntimeError as exc:
        print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return EXIT_BLOCKED


if __name__ == "__main__":
    sys.exit(main())
