#!/usr/bin/env python3
"""Read-only measurement: is a strategy FAMILY negative out of sample, on periods?

    docker exec thomas-scheduler python -m scripts.family_period_test

Writes nothing, asks no venue, and decides nothing — it prints a table an operator
reads before choosing whether to retire a family from the factory's template list.

**The whole point of this script is the unit it tests on, and it exists because the
obvious version is wrong.** ``docs/TRADING_ALPHA_RESEARCH_RECORD.md`` establishes that
the unit of independence here is the market PERIOD, not the trade and not the lineage:
one family's lineages run the same signal over the same bars, so their expectancies are
strongly correlated. A t computed across lineage means therefore measures the size of
that correlation rather than the size of the effect.

That is not a theoretical worry. Measured 2026-08-05, the lineage-mean form scored
``breakout+oi_squeeze_long+trend_pullback`` at **t = +19.19** on 15 lineages and 74
trades — a number financial data does not produce — and called 28 of 60 families
negative at t < -3. The same data on periods called **zero** families either way. It
also flipped a sign: ``breakdown_short`` reads t = -6.54 on lineage means and +0.06 on
periods, so the lineage form guarantees neither magnitude nor direction.

So this script pools every lineage of a family INTO each period bucket and tests the
resulting per-period expectancies. Correlated lineages inside one bucket stop being
independent observations and become what they are: one observation of that period.

Reads the candidate store only (``pool.read_candidates``). Fail-closed on thin evidence:

- a family with fewer than ``MIN_HOLDOUT_PERIODS`` non-empty periods is not printed as
  "no result", it is not tested at all — the same floor ``_periods_confirm`` applies;
- periods that closed no trade are dropped rather than scored 0.0, because a slice the
  family never traded in is absence of evidence, and counting it as break-even would
  shrink the spread with data nobody measured;
- a zero spread is skipped rather than yielding an infinite t.

The verdict column uses ``robustness.t_critical_95`` at n-1 degrees of freedom, matching
the gate. A family is only actionable when it prints NEGATIVE — and note that period
count does NOT grow over time (the holdout slots are fixed). What accumulates is
lineages per family, which narrows each bucket and raises |t|.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from runtime.mvp_runtime.crypto import pool, robustness

NEGATIVE = "NEGATIVE(95%)"
POSITIVE = "positive(95%)"


def _holdout(record: Mapping[str, Any]) -> Mapping[str, Any]:
    evidence = record.get("backtest_evidence")
    holdout = evidence.get("holdout") if isinstance(evidence, Mapping) else None
    return holdout if isinstance(holdout, Mapping) else {}


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def collect_period_buckets(
    records: list[dict[str, Any]],
) -> tuple[dict[str, dict[int, list[float]]], dict[str, set[str]], int]:
    """Pool each family's lineages into per-period ``[sum_R, trades]`` buckets.

    Returns ``(buckets, lineages, carrying)`` where ``carrying`` is how many records
    carried a usable ``period_r`` at all — printed because zero of it is the difference
    between "no family is negative" and "nothing has been measured yet", and those two
    read identically in the table.
    """
    buckets: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    lineages: dict[str, set[str]] = defaultdict(set)
    carrying = 0
    for record in records:
        holdout = _holdout(record)
        periods, counts = holdout.get("period_r"), holdout.get("period_trades")
        if not isinstance(periods, (list, tuple)) or not isinstance(counts, (list, tuple)):
            continue
        if len(periods) != len(counts):
            continue
        carrying += 1
        spec = record.get("strategy_spec")
        family = spec.get("strategy_family") if isinstance(spec, Mapping) else None
        if not isinstance(family, str) or not family:
            continue
        lineages[family].add(str(record.get("strategy_rule_hash") or ""))
        for index, (total, trades) in enumerate(zip(periods, counts)):
            total_r, trade_count = _numeric(total), _numeric(trades)
            if total_r is None or trade_count is None or trade_count <= 0:
                continue
            buckets[family][index][0] += total_r
            buckets[family][index][1] += trade_count
    return buckets, lineages, carrying


def score_family(bucket: Mapping[int, list[float]]) -> dict[str, Any] | None:
    """One family's period-level t, or None when the evidence cannot carry a test."""
    values = [total / trades for total, trades in bucket.values() if trades > 0]
    if len(values) < robustness.MIN_HOLDOUT_PERIODS:
        return None
    spread = statistics.stdev(values)
    if spread <= 0:
        return None
    mean = statistics.mean(values)
    t_stat = mean / (spread / math.sqrt(len(values)))
    critical = robustness.t_critical_95(len(values) - 1)
    verdict = NEGATIVE if t_stat < -critical else (POSITIVE if t_stat > critical else "")
    return {
        "periods": len(values),
        "trades": int(sum(trades for _, trades in bucket.values())),
        "mean_r": mean,
        "t": t_stat,
        "verdict": verdict,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None, help="repository root (default: the runtime's)")
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else None

    records = pool.read_candidates(root)
    buckets, lineages, carrying = collect_period_buckets(records)
    print(f"candidates carrying period_r: {carrying} of {len(records)}")
    if not carrying:
        # Not a failure: the field is written by the factory, so a store minted before
        # that code shipped has none. Saying so beats an empty table read as "all clear".
        print("no period-level evidence yet — the factory has not minted a generation "
              "carrying period_r. Nothing can be judged, and nothing should be retired.")
        return 0

    scored = []
    for family, bucket in buckets.items():
        result = score_family(bucket)
        if result is not None:
            scored.append((result["t"], family, len(lineages[family]), result))

    print(f"\n{'family':<36} {'lin':>4} {'per':>4} {'trades':>7} {'meanR':>9} {'t':>8}")
    for _, family, lineage_count, result in sorted(scored):
        mark = f"  {result['verdict']}" if result["verdict"] else ""
        print(f"{family:<36} {lineage_count:>4} {result['periods']:>4} {result['trades']:>7} "
              f"{result['mean_r']:>9.4f} {result['t']:>8.2f}{mark}")

    negative = sum(1 for _, _, _, r in scored if r["verdict"] == NEGATIVE)
    positive = sum(1 for _, _, _, r in scored if r["verdict"] == POSITIVE)
    print(f"\ntestable on periods: {len(scored)}   negative: {negative}   positive: {positive}")
    if not scored:
        print(f"no family clears {robustness.MIN_HOLDOUT_PERIODS} non-empty periods yet.")
    elif not negative:
        # The actionable reading. Period count is fixed by the holdout slots, so waiting
        # buys lineages rather than periods — which is what narrows the buckets.
        print("nothing is actionable: no family is negative at 95%. More lineages per "
              "family narrow the period buckets; the period count itself does not grow.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
