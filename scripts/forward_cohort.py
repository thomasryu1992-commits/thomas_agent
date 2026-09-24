#!/usr/bin/env python3
"""Operator tool: the forward cohort (``crypto/forward_cohort.py``; Phase 1, option A).

Four subcommands, each dry unless it says otherwise:

- ``freeze`` lists the lineages a cohort frozen now would hold, with the attempt counts per
  context; ``--apply`` appends the sealed record. Membership never changes after that.
- ``walk`` advances every member-context to the newest closed bar through the venue collector;
  ``--apply`` writes the rows and the walker state. Without it the walk is computed and
  reported and nothing is written.
- ``freeze-nulls`` lists the null arm each frozen cohort without one would get: a coin-flip twin
  per member (``crypto/forward_cohort_null.py``); ``--apply`` appends the sealed record. ``walk``
  walks the twins after the members, into the null arm's own stores.
- ``report`` prints each member's forward numbers over its cohort rows. Reads only.

Nothing here reaches the pool, the arming door or an order: cohort rows live in their own
store, which the arming door's reader refuses. Writes runtime state, so it runs in the container
as uid 10001, in module form::

    docker exec thomas-scheduler python -m scripts.forward_cohort freeze
    docker exec thomas-scheduler python -m scripts.forward_cohort freeze --apply
    docker exec thomas-scheduler python -m scripts.forward_cohort freeze-nulls --apply
    docker exec thomas-scheduler python -m scripts.forward_cohort walk --apply
    docker exec thomas-scheduler python -m scripts.forward_cohort report

A damaged candidate, pool, forward or cohort store refuses the run (``EXIT_BLOCKED``) before
anything is fetched or written.
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
from runtime.mvp_runtime.crypto import forward_cohort, forward_cohort_null  # noqa: E402


def _freeze(root: Path, now: str, apply: bool) -> int:
    record = forward_cohort.freeze_cohort(root, now=now, apply=apply)
    print("%-26s %-4s %-28s %-15s %s" % ("candidate", "tf", "family", "holdout", "selected"))
    for member in record["members"]:
        print("%-26s %-4s %-28s %-15s %s" % (
            member["candidate_id"], member["timeframe"], member["strategy_family"],
            member["holdout_status"], member["selected_at_utc"][:10]))
    print(f"\n{record['cohort_size']} member(s) over {len(record['context_sizes'])} context(s); "
          f"largest context K = {max(record['context_sizes'].values(), default=0)}")
    if apply:
        print(f"FROZEN {record['cohort_id']} at {record['frozen_at_utc']}")
    else:
        print("DRY RUN — nothing frozen. Re-run with --apply.")
    return EXIT_OK


def _freeze_nulls(root: Path, now: str, apply: bool) -> int:
    records = forward_cohort_null.freeze_nulls(root, now=now, apply=apply)
    if not records:
        print("every frozen cohort already has its null arm")
        return EXIT_OK
    for record in records:
        print(f"{record['cohort_id']}: {record['null_size']} twin(s), rate rule: {record['rate_rule']}")
        for skip in record["skipped"]:
            print(f"  skipped {skip['parent_candidate_id']}: {skip['reason']}")
    print("FROZEN" if apply else "DRY RUN — nothing frozen. Re-run with --apply.")
    return EXIT_OK


def _walk(root: Path, now: str, apply: bool) -> int:
    frame_for = forward_cohort.memoized_frames(forward_cohort.collector_frames(root, now=now))
    summary = forward_cohort.run_cohort_walk(root, now=now, frame_for=frame_for, persist=apply)
    print(f"members={summary['members']} contexts={summary['contexts']} walked={summary['walked']} "
          f"opened={summary['opened']} settled={summary['settled']}")
    for line in summary["failed"]:
        print(f"  FAILED {line}")
    nulls = forward_cohort_null.run_null_walk(root, now=now, frame_for=frame_for, persist=apply)
    if nulls["members"]:
        print(forward_cohort_null.status_line(nulls))
    if not apply:
        print("DRY RUN — nothing written. Re-run with --apply.")
    return EXIT_OK


def _report(root: Path) -> int:
    for cohort in forward_cohort.cohort_report(root):
        print(f"{cohort['cohort_id']} frozen {cohort['frozen_at_utc']} K={cohort['cohort_size']}")
        print("  %-26s %-4s %-28s %5s %9s %6s  %s" % (
            "candidate", "tf", "family", "n", "mean_R", "slices", "status (display only)"))
        for m in sorted(cohort["members"], key=lambda m: -(m.get("priceable_count") or 0)):
            mean = m.get("mean_net_r")
            print("  %-26s %-4s %-28s %5s %9s %6s  %s" % (
                m.get("candidate_id"), m.get("timeframe") or "", m.get("strategy_family") or "",
                m.get("priceable_count", ""), "" if mean is None else f"{mean:+.3f}",
                m.get("active_slices", ""), m.get("status")))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forward_cohort", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "freeze-nulls", "walk"):
        p = sub.add_parser(name)
        p.add_argument("--apply", action="store_true", help="write; without it nothing is written")
    sub.add_parser("report")
    args = parser.parse_args(argv)

    try:
        assert_not_foreign_root_run(None)
    except MvpRuntimeError as exc:
        print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return EXIT_BLOCKED

    now = timeutil.utc_now_iso()
    try:
        if args.command == "freeze":
            return _freeze(ROOT, now, args.apply)
        if args.command == "freeze-nulls":
            return _freeze_nulls(ROOT, now, args.apply)
        if args.command == "walk":
            return _walk(ROOT, now, args.apply)
        return _report(ROOT)
    except MvpRuntimeError as exc:
        print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return EXIT_BLOCKED


if __name__ == "__main__":
    sys.exit(main())
