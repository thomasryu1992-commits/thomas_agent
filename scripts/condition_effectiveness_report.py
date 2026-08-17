#!/usr/bin/env python3
"""What do the ablation blocks say about each condition, in aggregate? Read-only.

    docker exec thomas-scheduler python -m scripts.condition_effectiveness_report

Every lattice run (#704) already stores its per-subset train net expectancy on the registered
candidate (`backtest_evidence.ablation`). This script only aggregates those blocks — it adds no
new measurement, no new replay, and it writes nothing.

WHAT THE TABLE IS FOR. Draw mass. The factory keeps spending draws on whatever the templates
contain; the lattice keeps deleting what does not earn its place. Aggregating the two says which
condition families are drawn often and deleted often — a question about where mint mass goes,
which `docs/TRADING_ALPHA_RESEARCH_RECORD.md` ("민팅 질량 배분") already flags as decided by
pool exhaustion rather than policy.

WHAT IT IS NOT FOR. Edge confirmation. The contrasts are train-only and post-selection (only
hypotheses that reached a lattice appear), a "lattice" is not an independent observation (the
store's unit of independence is the market period, ~10 of them), and nothing under 0.05R is
resolvable here. A positive row is a hypothesis about draw allocation, never evidence a condition
works forward.

THE MARGIN SECTION IS PRE-REGISTERED for the §3-2 recheck (`FACTORY_ABLATION_V0.1.md`): approval
was strict-beat with **no** significance threshold. The section prints how many full-conjunction
wins sit under 0.01/0.03/0.05R margins — what a threshold would flip — and the holdout medians of
thin- vs fat-margin winners. That holdout split may only ever feed a *proposal to Thomas* about
the threshold, at ≥50 full-conjunction winners; using it to pick subsets is the A2 holdout-reuse
failure the proposal explicitly declined.
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping

from runtime.mvp_runtime.crypto import factory, pool

#: §3-2 recheck marks: what a significance threshold at each level would have flipped.
MARGIN_MARKS_R = (0.01, 0.03, 0.05)

#: The holdout-by-margin split stays descriptive until this many full-conjunction winners exist.
MIN_WINNERS_FOR_THRESHOLD_PROPOSAL = 50


def _condition_key(cond: Mapping[str, Any]) -> str:
    feature = str(cond.get("feature", "?"))
    comparison = str(cond.get("comparison", "?"))
    target = cond.get("value_from")
    return f"{feature} {comparison} {target}" if target else f"{feature} {comparison}"


def _full_key(k: int) -> str:
    return "+".join(str(i) for i in range(k))


def _winner_margin(block: Mapping[str, Any]) -> float:
    """Full-conjunction train net expectancy minus the best proper subset's."""
    subsets = block["train_net_expectancy_by_subset"]
    full = _full_key(len(block["hypothesis_conditions"]))
    return subsets[full] - max(v for key, v in subsets.items() if key != full)


def _within_lattice_lift(block: Mapping[str, Any], index: int) -> float | None:
    """Mean of E(T) − E(T∖{index}) over every stored subset T containing index, |T| ≥ 2.

    Both sides of every contrast replay the same frame, so this dodges the record's trap 1
    (non-matched samples) *within* a lattice; averaging across lattices does not.
    """
    subsets = block["train_net_expectancy_by_subset"]
    k = len(block["hypothesis_conditions"])
    deltas = []
    for size in range(2, k + 1):
        for members in combinations(range(k), size):
            if index not in members:
                continue
            with_key = "+".join(str(i) for i in members)
            without_key = "+".join(str(i) for i in members if i != index)
            if with_key in subsets and without_key in subsets:
                deltas.append(subsets[with_key] - subsets[without_key])
    return statistics.mean(deltas) if deltas else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None, help="repository root (default: the runtime's)")
    args = parser.parse_args(argv)

    records = pool.read_candidates(Path(args.root) if args.root else None)
    with_block = [r for r in records
                  if (r.get("backtest_evidence") or {}).get("ablation")]
    print(f"store rows: {len(records)}   with ablation block: {len(with_block)}")
    if not with_block:
        print("no ablation blocks yet — #704 has not minted through a lattice on this machine.")
        return 0

    stamps = sorted(str(r.get("created_at_utc") or "") for r in with_block)
    print(f"lattice window: {stamps[0]} .. {stamps[-1]}")

    # --- Condition Effectiveness Table (train-only, within-lattice contrasts) ---
    lifts: dict[str, list[float]] = defaultdict(list)
    appearances: dict[str, int] = defaultdict(int)
    survivals: dict[str, int] = defaultdict(int)
    sole_survivals: dict[str, int] = defaultdict(int)
    for record in with_block:
        block = record["backtest_evidence"]["ablation"]
        winner_keys = {_condition_key(c) for c in (block.get("winner_conditions") or [])}
        for index, cond in enumerate(block["hypothesis_conditions"]):
            feature = str(cond.get("feature", "?"))
            appearances[feature] += 1
            if _condition_key(cond) in winner_keys:
                survivals[feature] += 1
                if len(winner_keys) == 1:
                    sole_survivals[feature] += 1
            lift = _within_lattice_lift(block, index)
            if lift is not None:
                lifts[feature].append(lift)

    print("\ncondition effectiveness (train-only; post-selection; NOT edge confirmation):")
    print(f"  {'feature':28s} {'n_lat':>5s} {'mean_lift':>9s} {'med_lift':>9s} "
          f"{'pos':>4s} {'survive':>7s} {'sole':>4s}")
    for feature, values in sorted(lifts.items(), key=lambda kv: -statistics.mean(kv[1])):
        positive = sum(1 for v in values if v > 0)
        print(f"  {feature:28s} {len(values):5d} {statistics.mean(values):9.4f} "
              f"{statistics.median(values):9.4f} {positive:3d}/{len(values):<3d}"
              f"{survivals[feature]:3d}/{appearances[feature]:<3d} {sole_survivals[feature]:4d}")
    print("  READS: rows rank where draw mass meets deletion — a drawn-often, deleted-often")
    print("  feature is a template-allocation question, not a verdict on the feature.")

    # --- §3-2 recheck: winner margins -------------------------------------------------------
    won = [r for r in with_block if r["backtest_evidence"]["ablation"]["full_beat_subsets"]]
    margins = sorted(_winner_margin(r["backtest_evidence"]["ablation"]) for r in won)
    filtered = len(with_block) - len(won)
    print(f"\nlattices: {len(with_block)}   full-conjunction wins: {len(won)}   "
          f"luck-filtered (subset won): {filtered}")
    if won:
        print(f"  win margins: min {margins[0]:.4f}R   median "
              f"{statistics.median(margins):.4f}R   max {margins[-1]:.4f}R")
        for mark in MARGIN_MARKS_R:
            flipped = sum(1 for m in margins if m < mark)
            print(f"  a {mark:.2f}R significance threshold would have flipped "
                  f"{flipped}/{len(won)} wins to their subset")

        def _holdout_medians(rows: list[Mapping[str, Any]]) -> str:
            values = [((r.get("backtest_evidence") or {}).get("holdout") or {}).get("expectancy")
                      for r in rows]
            values = [v for v in values if v is not None]
            if not values:
                return "n=0"
            return f"n={len(values)} holdout p50 {statistics.median(values):+.4f}R"

        thin = [r for r in won if _winner_margin(r["backtest_evidence"]["ablation"]) < 0.05]
        fat = [r for r in won if _winner_margin(r["backtest_evidence"]["ablation"]) >= 0.05]
        print(f"  thin-margin winners (<0.05R): {_holdout_medians(thin)}")
        print(f"  fat-margin winners (>=0.05R): {_holdout_medians(fat)}")
        if len(won) < MIN_WINNERS_FOR_THRESHOLD_PROPOSAL:
            print(f"  READS: descriptive only — under {MIN_WINNERS_FOR_THRESHOLD_PROPOSAL} "
                  f"winners nothing here can support a threshold proposal either way.")

    # --- §3-1 recheck: how much mints past the lattice --------------------------------------
    first_block = stamps[0]
    since = [r for r in records if str(r.get("created_at_utc") or "") >= first_block]
    def _condition_count(record: Mapping[str, Any]) -> int:
        rules = (record.get("strategy_spec") or {}).get("entry_rules") or {}
        return len(rules.get("conditions") or [])
    bypass = [r for r in since if _condition_count(r) > factory.ABLATION_MAX_CONDITIONS]
    print(f"\nsince the first lattice ({first_block}): {len(since)} candidates minted, "
          f"{len(bypass)} with more than {factory.ABLATION_MAX_CONDITIONS} conditions "
          f"(no ablation).")
    print("  READS: the k cap is a cadence decision (§3-1, declined at 4). This line is the")
    print("  size of what that decision leaves unexamined — re-raise it only if it grows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
