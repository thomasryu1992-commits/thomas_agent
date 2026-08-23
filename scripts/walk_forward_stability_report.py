#!/usr/bin/env python3
"""Do the walk-forward period subtotals discriminate? Read-only, and the PR-2 decision input.

    docker exec thomas-scheduler python -m scripts.walk_forward_stability_report

Every candidate minted since `factory.WALK_FORWARD_PERIODS` landed carries the scored region
subtotalled into equal-bar periods (`walk_forward.period_r` / `period_trades`). Nothing judges
on them yet — `temporal_stability` is written as None — and this script is what decides whether
that changes: it aggregates the stored blocks, adds no new measurement, replays nothing, and
writes nothing.

Everything derived here is RECOMPUTED under today's rule from the stored inputs
(`score_robustness` over the stored walk_forward/regime/holdout blocks) — never read back from
the stored robustness block, which is a mint-time label this repo's stored-snapshot tests pin
to exactly one recomputing reader. That also makes the baseline honest: both sides of every
delta below went through the same rule vintage.

THE THREE QUESTIONS IT ANSWERS, pre-registered in
`docs/proposals/WALK_FORWARD_TEMPORAL_STABILITY_V0.1.md`:

1. **Occupancy** — how many periods do real candidates actually judge (≥1 closed trade),
   overall and by timeframe? This is what confirms or moves `WALK_FORWARD_PERIODS` (20) and
   `WALK_FORWARD_MIN_PERIODS` (8), the same way `HOLDOUT_PERIODS` was confirmed at 10.
2. **Discrimination** — does the would-be temporal_stability separate holdout-CONFIRMED from
   holdout-CONTRADICTED blocks? The holdout is not an input to the metric, so it is a
   legitimate validation target. If the split is flat, the metric is noise and must not
   enter the score.
3. **Migration** — if the field were judged today, how far would scores move and how many
   verdicts would flip? The holdout gate keeps ROBUST unreachable either way; what moves is
   the PROVISIONAL/FRAGILE interior, and this prints by how much.

WHAT IT IS NOT. A go-decision. The output feeds a proposal to Thomas; enabling the judgment
is PR-2 and a decision, never a side effect of this script printing a friendly number.
"""

from __future__ import annotations

import argparse
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from runtime.mvp_runtime.crypto import factory, pool, robustness
from runtime.mvp_runtime.crypto.strategy import StrategySpec


def would_be_temporal_stability(walk_forward: Mapping[str, Any]) -> float | None:
    """The judgment PR-2 would make, computed exactly as proposed: the positive share of
    JUDGED periods (≥1 closed trade — an untraded slice is absence of evidence, the
    `_periods_confirm` precedent), None below the judged-period floor."""
    period_r = walk_forward.get("period_r")
    period_trades = walk_forward.get("period_trades")
    if not isinstance(period_r, list) or not isinstance(period_trades, list):
        return None
    if len(period_r) != len(period_trades):
        return None
    judged = [(r, n) for r, n in zip(period_r, period_trades)
              if isinstance(n, (int, float)) and not isinstance(n, bool) and n > 0
              and isinstance(r, (int, float)) and not isinstance(r, bool)]
    if len(judged) < factory.WALK_FORWARD_MIN_PERIODS:
        return None
    return sum(1 for r, _ in judged if r > 0) / len(judged)


def _rescore(record: Mapping[str, Any], stability: float | None) -> dict[str, Any] | None:
    """Today's rule over the stored inputs, with `temporal_stability` set as asked.

    A full `score_robustness` recompute rather than a one-term patch of the stored score:
    the stored block is a mint-time label, and applying the current rule to BOTH sides is
    what makes the migration delta mean "what judging would change" instead of "what changed
    since this record was minted". None when the stored inputs cannot be rescored (an odd
    spec, missing blocks) — skipped and counted, never guessed."""
    evidence = record.get("backtest_evidence") or {}
    walk_forward = evidence.get("walk_forward")
    cost_summary = evidence.get("cost_summary")
    if not isinstance(walk_forward, Mapping) or not isinstance(cost_summary, Mapping):
        return None
    try:
        spec = StrategySpec.from_dict(record.get("strategy_spec") or {})
    except Exception:
        return None
    metrics = {
        "trade_count": evidence.get("closed_count", 0),
        "total_net_r": cost_summary.get("total_net_r", 0.0),
        "fee_cost_r": cost_summary.get("total_fee_cost_r", 0.0),
        "slippage_cost_r": cost_summary.get("total_slippage_cost_r", 0.0),
        "funding_cost_r": cost_summary.get("total_funding_cost_r", 0.0),
    }
    holdout = evidence.get("holdout")
    return robustness.score_robustness(
        spec, metrics, {**walk_forward, "temporal_stability": stability},
        evidence.get("regime_breakdown") or {},
        holdout=holdout if isinstance(holdout, Mapping) else None,
    )


def _median_line(label: str, values: list[float]) -> str:
    if not values:
        return f"  {label:32s} n=0"
    return (f"  {label:32s} n={len(values):<4d} p50 {statistics.median(values):.3f}   "
            f"mean {statistics.mean(values):.3f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None, help="repository root (default: the runtime's)")
    args = parser.parse_args(argv)

    records = pool.read_candidates(Path(args.root) if args.root else None)
    rows = []
    for record in records:
        evidence = record.get("backtest_evidence") or {}
        walk_forward = evidence.get("walk_forward") or {}
        if not isinstance(walk_forward.get("period_r"), list):
            continue
        rows.append(record)
    print(f"store rows: {len(records)}   with walk-forward subtotals: {len(rows)}")
    if not rows:
        print("no subtotalled blocks yet — nothing minted since WALK_FORWARD_PERIODS landed.")
        return 0
    stamps = sorted(str(r.get("created_at_utc") or "") for r in rows)
    print(f"subtotal window: {stamps[0]} .. {stamps[-1]}")

    # --- 1. occupancy: what the floor and the count would refuse ----------------------------
    judged_counts: list[int] = []
    by_timeframe: dict[str, list[int]] = defaultdict(list)
    for record in rows:
        walk_forward = record["backtest_evidence"]["walk_forward"]
        judged = int(walk_forward.get("periods_judged") or 0)
        judged_counts.append(judged)
        timeframe = str((record.get("strategy_spec") or {}).get("timeframe") or "?")
        by_timeframe[timeframe].append(judged)
    floor = factory.WALK_FORWARD_MIN_PERIODS
    total = factory.WALK_FORWARD_PERIODS
    below = sum(1 for n in judged_counts if n < floor)
    print(f"\noccupancy (periods judged, of {total}; floor {floor}):")
    print(f"  distribution: {dict(sorted(Counter(judged_counts).items()))}")
    print(f"  below the floor (temporal_stability would stay None): {below}/{len(judged_counts)}")
    for timeframe, counts in sorted(by_timeframe.items()):
        under = sum(1 for n in counts if n < floor)
        print(f"    {timeframe:5s} n={len(counts):<4d} p50 judged {statistics.median(counts):.0f}   "
              f"below floor {under}/{len(counts)}")
    print("  READS: a timeframe living mostly below the floor is structurally excluded, not")
    print("  unstable — that moves the floor or the count, never the reading of those specs.")

    # --- 2. discrimination: the metric against the holdout it never read --------------------
    # The grouping status is recomputed from the stored holdout block (`holdout_status`),
    # exactly as `pool.candidate_quality` does — a mint-time label can be two rule vintages
    # stale, and grouping on it would validate the metric against the wrong answer key.
    stability_by_status: dict[str, list[float]] = defaultdict(list)
    unjudgeable = 0
    for record in rows:
        evidence = record["backtest_evidence"]
        stability = would_be_temporal_stability(evidence["walk_forward"])
        if stability is None:
            unjudgeable += 1
            continue
        holdout = evidence.get("holdout")
        status = robustness.holdout_status(holdout if isinstance(holdout, Mapping) else None)
        stability_by_status[status].append(stability)
    print(f"\nwould-be temporal_stability by (recomputed) holdout status "
          f"({unjudgeable} below the floor):")
    for status in (robustness.HOLDOUT_CONFIRMED, robustness.HOLDOUT_INSUFFICIENT,
                   robustness.HOLDOUT_CONTRADICTED, robustness.HOLDOUT_UNCONFIRMED):
        print(_median_line(status, stability_by_status.get(status, [])))
    print("  READS: the holdout is not an input to the metric, so this split is a legitimate")
    print("  validation target. CONFIRMED not clearly above CONTRADICTED = the metric is")
    print("  noise here, and PR-2 does not happen on these numbers.")

    # --- 3. migration: what judging today would move -----------------------------------------
    deltas: list[float] = []
    flips: Counter[str] = Counter()
    unscorable = 0
    for record in rows:
        stability = would_be_temporal_stability(record["backtest_evidence"]["walk_forward"])
        baseline = _rescore(record, None)
        judged = _rescore(record, stability)
        if baseline is None or judged is None:
            unscorable += 1
            continue
        deltas.append(judged["robustness_score"] - baseline["robustness_score"])
        if judged["verdict"] != baseline["verdict"]:
            flips[f"{baseline['verdict']}->{judged['verdict']}"] += 1
    if deltas:
        print(f"\nscore migration if judged today (n={len(deltas)}, "
              f"{unscorable} unscorable and skipped):")
        print(f"  delta p50 {statistics.median(deltas):+.4f}   min {min(deltas):+.4f}   "
              f"max {max(deltas):+.4f}")
        print(f"  verdict flips: {dict(flips) or 'none'}")
        print("  READS: both sides are today's rule over the stored inputs, so the delta is")
        print("  what JUDGING changes, not what the rules changed since mint. ROBUST stays")
        print("  holdout-gated either way; this is the PROVISIONAL/FRAGILE interior moving.")
        print("  Feed these three sections to the PR-2 proposal — enabling the judgment is")
        print("  Thomas's decision, not this script's.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
