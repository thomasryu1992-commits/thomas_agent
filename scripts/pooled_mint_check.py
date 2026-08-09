#!/usr/bin/env python3
"""Did pooled minting do the one thing it was wired for? Read-only.

    docker exec thomas-scheduler python -m scripts.pooled_mint_check

**The reading is pre-registered here rather than decided when the numbers arrive**, which is the
whole reason this file exists instead of an ad-hoc query. F9 shape B went live 2026-08-09 and the
first cohort fire is 2026-08-10; writing the interpretation after seeing four rows is how a thin
result gets talked into being a finding.

WHAT POOLING WAS FOR. At 4h a single-symbol row closes a **median 9** holdout trades against
``robustness.MIN_HOLDOUT_TRADES`` = 25, so those rows are unjudgeable permanently — rows do not
pool across generations, and waiting adds nothing. A pooled spec is one hypothesis at N symbols'
data, so its tail is all of their tails. Either that clears the floor or the premise is wrong.

WHAT IT WAS NOT FOR. Edge. 0 of 1,140 candidates confirm out of sample, a random entry loses
0.13R here, and the only result that ever cleared a selection-adjusted bar decayed to −0.23…−0.31R
in earlier windows across 16 of 16 draws. This script prints `prior_window_r` beside every
confirmation for exactly that reason.

ONE DAY IS NOT A VERDICT. 4h now mints ~4 specs a day rather than ~20, and this store's unit of
independence is the market period (~10 of them). What the first fires answer is *"does the wiring
work"* — see ``docs/REMAINING_WORK.md`` F9 and the `#420` error.
"""

from __future__ import annotations

import argparse
import statistics
from pathlib import Path
from typing import Any, Mapping

from runtime.mvp_runtime.crypto import pool, robustness

# The single-symbol 4h baseline this replaces, measured 2026-08-08 over the stored rows.
SINGLE_SYMBOL_4H_MEDIAN_TRADES = 9


def _holdout(record: Mapping[str, Any]) -> Mapping[str, Any]:
    return (record.get("backtest_evidence") or {}).get("holdout") or {}


def _scope(record: Mapping[str, Any]) -> list[str]:
    scope = (record.get("strategy_spec") or {}).get("symbol_scope")
    return [str(s) for s in scope] if isinstance(scope, (list, tuple)) else []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None, help="repository root (default: the runtime's)")
    args = parser.parse_args(argv)

    records = pool.read_candidates(Path(args.root) if args.root else None)
    pooled = [r for r in records if len(_scope(r)) > 1]
    print(f"store rows: {len(records)}   pooled rows: {len(pooled)}")
    if not pooled:
        # Not a failure and not a result: the schedules fire once a day, so an empty answer
        # before the first cohort fire is the only honest one.
        print("no pooled rows yet — the cohort schedules have not fired, or were reverted.")
        return 0

    by_timeframe: dict[str, list[Mapping[str, Any]]] = {}
    for record in pooled:
        by_timeframe.setdefault(
            str((record.get("strategy_spec") or {}).get("timeframe")), []).append(record)

    floor = robustness.MIN_HOLDOUT_TRADES
    for timeframe, rows in sorted(by_timeframe.items()):
        trades = [h.get("closed_count") or 0 for h in map(_holdout, rows)]
        judgeable = [t for t in trades if t >= floor]
        legs = {len(_scope(r)) for r in rows}
        print(f"\n=== {timeframe}: {len(rows)} pooled rows, legs={sorted(legs)} ===")
        print(f"  holdout trades: median {statistics.median(trades):.0f}, "
              f"reaching the {floor}-trade floor {len(judgeable)}/{len(trades)}")
        # THE PRE-REGISTERED READING. Stated as a verdict line so a reader cannot take the
        # number and supply their own.
        if timeframe == "4h":
            if judgeable:
                print(f"  READS: the wiring did what it was for — single-symbol 4h ran a median "
                      f"of {SINGLE_SYMBOL_4H_MEDIAN_TRADES}.")
            else:
                print("  READS: pooling did NOT fix 4h's thin tail. That is the evidence to "
                      "revert on, not a reason to wait longer.")

        # A leg count other than the cohort's means a fire ran degraded and should have
        # cancelled — `scheduler` refuses that, so seeing it here is a guard defect.
        for record in rows:
            symbols = _holdout(record).get("symbols")
            if isinstance(symbols, int) and symbols != len(_scope(record)):
                print(f"  GUARD DEFECT: {pool.candidate_id(record)} claims "
                      f"{len(_scope(record))} symbols and replayed {symbols}")

        confirmed = [r for r in rows
                     if robustness.holdout_status(_holdout(r)) == "CONFIRMED"]
        print(f"  CONFIRMED: {len(confirmed)}/{len(rows)}")
        for record in confirmed:
            holdout = _holdout(record)
            prior_r = holdout.get("prior_window_r") or []
            prior_n = holdout.get("prior_window_trades") or []
            per = [f"{r / n:+.3f}" if n else "  n/a" for r, n in zip(prior_r, prior_n)]
            flips = any(
                isinstance(holdout.get("expectancy"), (int, float))
                and n and (r / n) * holdout["expectancy"] < 0
                for r, n in zip(prior_r, prior_n))
            print(f"    {pool.candidate_id(record):26} "
                  f"exp={holdout.get('expectancy'):+.4f} n={holdout.get('closed_count')}  "
                  f"prior={' '.join(per) or 'none'}"
                  + ("   SIGN FLIPS — a property of the recent window" if flips else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
