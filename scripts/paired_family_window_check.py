#!/usr/bin/env python3
"""Did doubling the replay window actually deepen the holdout? Paired by FAMILY.

    docker exec thomas-scheduler python -m scripts.paired_family_window_check

Read-only. Writes nothing, asks no venue, decides nothing — it prints a table and one
machine-readable ``SERIES`` line per timeframe so the trajectory can be followed across days.

**WHY PAIRED.** A day's mints are dominated by the rotation cursor's position: on 2026-08-05 the
4h block drew ``taker_flow_long`` (0 holdout trades) beside ``xs_reversion_short`` (121), so that
day's raw MEDIAN said more about which families were drawn than about the window. The same family
across the two windows removes that. Measured 2026-08-05 over 11 shared 4h families: median ratio
1.64x while the raw day-over-day median went 30 -> 17 — Simpson's paradox, and the reason this
script exists rather than a one-line median.

**WHAT TO EXPECT.** Windows: 1h 12,000 -> 24,000 bars, 4h 3,000 -> 6,000
(``FACTORY_DEPTH_DAYS`` 500 -> 1,000, live since 2026-08-05). A ratio near 2.0 means the holdout
deepened as intended; near 1.0 means the extra bars bought no extra closed trades. 1d is unchanged
by design (``MIN_FACTORY_BARS`` floors it at 2,000 bars) and is reported only as a control. The
open question is 4h: 1h answered at 2.05x and its factory schedules were frozen 2026-08-06, so its
numbers no longer move.

**WHY IT LIVES HERE.** It ran for two days from ``/root/.claude/projects/`` — outside the
repository, and therefore outside the discipline that would have caught a stale constant. The
sibling script that stayed outside did exactly that: ``/root/holdout-split/split_half_test.py``
hard-coded ``HOLDOUT_PERIODS = 5``, the factory moved to 10, and every row minted after the change
was silently dropped by a length check while the log reported "not enough rows carry period_r"
about a store where 246 did. Three days of runs re-measured the same 80 rows. Nothing about that
failure needed a human to be careless — it needed only a copied number and no test. So the
thresholds here are IMPORTED, and the file is somewhere a test can reach it.
"""

from __future__ import annotations

import argparse
import collections
import statistics as st
from pathlib import Path
from typing import Any, Mapping

from runtime.mvp_runtime.crypto import pool
from runtime.mvp_runtime.crypto.robustness import MIN_HOLDOUT_TRADES

# (timeframe, old full window, new full window) — full = bars_replayed + holdout.bars
PAIRS: list[tuple[str, int, int]] = [
    ("1h", 12_000, 24_000),
    ("4h", 3_000, 6_000),
    ("1d", 2_000, 2_000),
]


def full_window(record: Mapping[str, Any]) -> int | None:
    """Total bars the row was scored over — the scored window plus its holdout tail.

    ``None`` when either half is absent, which is how a row minted before the field existed
    reports itself. Never substituted with a default: a guessed window would place the row in
    the wrong arm of the comparison this script is entirely about.
    """
    evidence = record.get("backtest_evidence") or {}
    holdout = evidence.get("holdout") or {}
    scored, tail = evidence.get("bars_replayed"), holdout.get("bars")
    if not isinstance(scored, (int, float)) or not isinstance(tail, (int, float)):
        return None
    return int(scored) + int(tail)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None, help="repository root (default: the runtime's)")
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else None

    rows = pool.read_candidates(root)
    print(f"store: {len(rows)} rows\n")

    for timeframe, old_w, new_w in PAIRS:
        old: dict[Any, list[int]] = collections.defaultdict(list)
        new: dict[Any, list[int]] = collections.defaultdict(list)
        for record in rows:
            spec = record.get("strategy_spec") or {}
            if spec.get("timeframe") != timeframe:
                continue
            holdout = (record.get("backtest_evidence") or {}).get("holdout") or {}
            closed, window = holdout.get("closed_count"), full_window(record)
            if not isinstance(closed, (int, float)) or window is None:
                continue
            family = spec.get("strategy_family")
            if window == old_w:
                old[family].append(int(closed))
            elif window == new_w and old_w != new_w:
                new[family].append(int(closed))

        if old_w == new_w:  # 1d control: no split to make
            everything = [x for v in old.values() for x in v]
            if everything:
                judgeable = sum(1 for x in everything if x >= MIN_HOLDOUT_TRADES)
                print(f"== {timeframe} (unchanged by MIN_FACTORY_BARS, control) "
                      f"n={len(everything)} median={st.median(everything):.0f} "
                      f"judgeable={judgeable}/{len(everything)}")
            continue

        shared = sorted(set(old) & set(new), key=str)
        print(f"== {timeframe}: {old_w} -> {new_w} bars, {len(shared)} shared families")
        if not shared:
            print("   no family has rows on BOTH windows yet — the lap has not come round\n")
            continue
        print(f"   {'family':34} {'old n':>6} {'old med':>8} {'new n':>6} {'new med':>8} "
              f"{'ratio':>7}")
        ratios: list[float] = []
        for family in shared:
            o, n = st.median(old[family]), st.median(new[family])
            ratio = n / o if o else float("inf")
            if o:
                ratios.append(ratio)
            print(f"   {str(family)[:34]:34} {len(old[family]):6} {o:8.0f} "
                  f"{len(new[family]):6} {n:8.0f} {ratio:7.2f}x")
        every_new = [x for v in new.values() for x in v]
        judgeable = sum(1 for x in every_new if x >= MIN_HOLDOUT_TRADES)
        print(f"   MEDIAN RATIO {st.median(ratios):.2f}x over {len(ratios)} families"
              f"   | new-window rows {len(every_new)}, judgeable {judgeable}"
              f" ({100 * judgeable / len(every_new):.0f}%)\n")
        # One machine-readable line per timeframe so the trajectory reads as a series rather
        # than by eye across daily dumps. `/root/window-check/run.sh` greps for it.
        print(f"SERIES\t{timeframe}\t{st.median(ratios):.3f}\t{len(ratios)}"
              f"\t{len(every_new)}\t{judgeable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
