"""Does a selected strategy's entry beat a coin flip through the same exits? — anchored at mint.

**The question, and why nothing in the repo answered it.** Every gate here judges a spec against
ZERO: `holdout_permits_parenting` and `holdout_permits_centring` both end in `expectancy >= 0`,
and the promotion door reads the same sign. But a strategy's R is `stop_atr x ATR` against a
fixed-bps round trip, so the number that comes back is dominated by the shared exit and cost
geometry rather than by the entry rule. Measured 2026-08-05 over 12 (symbol, timeframe) cells, a
RANDOM entry through the same exits on the same bars returns **-0.1274R at 1h, -0.0564R at 4h and
-0.0345R at 1d**. So `expectancy >= 0` is a bar standing 0.127R above chance at 1h and 0.035R
above it at 1d — three different difficulties wearing one line of code, and nobody chose that.

Two questions were being answered as one:

- *Should this trade?* — absolute, against zero. Correct as it stands: a spec that beats chance
  and still loses money still loses money.
- *Does this entry carry information?* — relative, against a null with the same exits and the
  same bars. **Never asked**, and it is the one that says whether searching for entry rules is
  worth anything at all.

**Why the answer needs a mint anchor, which is the whole reason this is a scheduled measurement
rather than a script somebody ran once.** The obvious version — replay today's window and compare
the holdout against a null — was run on 2026-08-05 and gave +0.1133R for 114 specs selected on
being positive in-sample, 91 of them beating their own null at p = 0.000. That figure cannot be
trusted, and the reason is arithmetic rather than statistical: today's holdout is the most recent
30% of a 1,000-day window (300 days), and a spec minted on 2026-08-04 was scored on a window
covering roughly 500 to 150 days back. **About half of that "holdout" was the spec's own scored
data**, and the specs were selected on exactly that data. The null arm has no such selection, so
the comparison tilts toward the real arm by construction.

The only bars that are honestly out of sample for a spec are the ones **after it was minted**.
That is what this module measures, and on the day it lands it will measure almost nothing — the
newest cohort is one day old. It is written to accumulate: every fire re-reads the store, and
every spec's evaluable window grows by a day. `docs/REMAINING_WORK.md` records the constraint
this is subject to (the unit of independence is the market period, not the trade; nothing under
0.05R resolves), so the honest expectation is weeks, not days.

Fail direction: this is an ALLOW-tier read. It creates a record and changes nothing — no pool,
no candidates, no orders. A cell that cannot be measured is reported as such rather than
dropped, because "no spec was old enough" and "no spec beat its null" are different facts and
only the record can tell them apart.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import replace
from typing import Any, Mapping, Sequence

from runtime.read_only_kernel import integrity

from . import market_data
from .factory import ReplayFrame, backtest_spec, unsuppliable_features
from .strategy import SpecParseError, StrategySpec, evaluate_spec

RECORD_TYPE = "crypto_null_control.v0"
LEDGER_KIND = "crypto_null_control"

# How many null draws each spec is compared against. Twelve rather than more because the null's
# spread is what costs replays and its MEDIAN is what the comparison uses — the median of twelve
# draws is already stable to well inside the 0.05R this store can resolve, and each extra draw is
# a full replay of the post-mint window.
NULL_DRAWS = 12

# How much market a spec must have lived through before its window is worth replaying, in DAYS
# rather than in bars.
#
# Bars was the obvious unit and it is the wrong one, for the reason `_GENERATION_SPACES` records
# about the exit geometry: a bar count means a different span at every rung of the ladder. At a
# 120-bar floor the oldest spec in the store (13 days on 2026-08-05) is refused at 4h with 78
# bars and admitted at 1h with 312 — the same spec, the same evidence, judged ready on one
# timeframe and not on another purely by how the ladder slices the calendar.
#
# Thirty days because the binding constraint is independence, not sample size:
# `docs/REMAINING_WORK.md` records that the unit here is the market period rather than the trade,
# and that nothing under 0.05R resolves. A month is one period, which is the least that can mean
# anything — and on the day this lands NOTHING clears it, which is the honest state rather than a
# defect. The store's oldest spec is 13 days old. This measurement is built to accumulate.
MIN_POST_MINT_DAYS = 30

# And this many closed trades, on the same reasoning applied to the other axis: a window can be
# long and still produce three trades.
MIN_POST_MINT_TRADES = 10

# The feature name the null entry conditions on. Deliberately NOT in `factory.NUMERIC_FEATURES`:
# a null spec must never be mintable, promotable, or mistakable for a strategy, and the validator
# refusing it with BLOCK_UNKNOWN_FEATURE is the property that guarantees it. It exists only on the
# rows this module writes it onto, for the length of one comparison.
NULL_FEATURE = "null_signal"
NULL_FAMILY = "null_control"


def post_mint_index(candles: Sequence[Mapping[str, Any]], created_at_utc: str) -> int:
    """The first bar index whose OPEN is strictly after ``created_at_utc``.

    Strictly after, not at-or-after: a spec minted during a bar was minted with that bar's data
    already in its scored window, so the bar it was born in is in-sample and the anchor must
    exclude it. Off-by-one in the safe direction — it costs one bar and cannot leak one.

    Returns ``len(candles)`` when the mint is newer than every bar, which the caller reads as
    "nothing to measure yet" rather than as an empty edge.
    """
    stamp = str(created_at_utc or "")
    if not stamp:
        return len(candles)
    for index, candle in enumerate(candles):
        if str(candle.get("open_time") or "") > stamp:
            return index
    return len(candles)


def _null_spec(spec: StrategySpec, rate: float) -> StrategySpec:
    """``spec`` with its entry replaced by a coin flip of the same signal rate.

    Everything else is held: the exit geometry, the risk cap, the direction, the venue. That is
    what makes the difference attributable to the entry rule rather than to the geometry the
    whole library shares.

    The rate is matched on SIGNALS rather than on trades, and the gap is recorded rather than
    hidden: a real entry's signals cluster in the regime its rule selects, while a null's are
    uniform, so the null completes roughly twice the trades at the same signal rate. Both arms
    are compared per-trade (R/trade), so the count does not enter the comparison directly — but
    it does mean the null samples the window more evenly than the strategy does, which is the
    honest reading of "the same rate".
    """
    payload = spec.to_dict()
    # The hash covers the rules; leaving the parent's would make `from_dict` refuse it as
    # tampered, which is the check doing its job.
    payload.pop("strategy_rule_hash", None)
    payload["strategy_family"] = NULL_FAMILY
    payload["entry_rules"] = {
        "operator": "AND",
        "conditions": [{"feature": NULL_FEATURE, "comparison": "<=", "value": round(rate, 6)}],
    }
    return StrategySpec.from_dict(payload)


def _slice(frame: ReplayFrame, start: int) -> ReplayFrame:
    """``frame`` from ``start`` on, with the split recomputed for the shorter window.

    Slicing rather than rebuilding is what keeps this affordable AND correct: the rows were
    computed over the whole series, so every value in them is already the point-in-time read for
    its own bar — taking a suffix cannot reach forward and does not re-run warmup.
    """
    from .factory import holdout_split_index

    rows, candles = frame.rows[start:], frame.candles[start:]
    funding = frame.funding[start:] if frame.funding is not None else None
    return replace(frame, rows=rows, candles=candles, funding=funding,
                   split=holdout_split_index(len(rows)))


def _totals(evidence: Mapping[str, Any]) -> tuple[float, int]:
    """Total (R, closed trades) over the WHOLE evaluated window, scored slice and holdout both.

    `backtest_spec` splits whatever it is given, but here both halves are already post-mint, so
    the split is an artifact of the tool rather than a boundary that means anything. Summing them
    is what makes the evaluated window "every bar after the mint" instead of "the last 30% of the
    bars after the mint".
    """
    closed = int(evidence.get("closed_count") or 0)
    total_r = float(evidence.get("expectancy") or 0.0) * closed
    holdout = evidence.get("holdout")
    if isinstance(holdout, Mapping):
        holdout_closed = int(holdout.get("closed_count") or 0)
        closed += holdout_closed
        total_r += float(holdout.get("total_R") or 0.0)
    return total_r, closed


def min_post_mint_bars(timeframe: str) -> int:
    """`MIN_POST_MINT_DAYS` expressed in this timeframe's own bars.

    An unknown timeframe returns a floor nothing can clear, so a typo refuses to measure rather
    than measuring against a bar count that means nothing.
    """
    minutes = market_data.TIMEFRAMES.get(str(timeframe))
    if not minutes:
        return 1 << 30
    return max(1, int(MIN_POST_MINT_DAYS * 1440 / minutes))


def measure_spec(
    spec: StrategySpec, snapshot: Mapping[str, Any], frame: ReplayFrame, *,
    created_at_utc: str, seed: int, timeframe: str, draws: int = NULL_DRAWS,
) -> dict[str, Any] | None:
    """One spec against ``draws`` nulls on the bars minted after it. ``None`` if unmeasurable.

    Pure given (spec, snapshot, frame, created_at_utc, seed) — the null draws come from a seeded
    rng, so a replay of a recorded fire reproduces the same comparison exactly.
    """
    start = post_mint_index(frame.candles, created_at_utc)
    available = len(frame.rows) - start
    if available < min_post_mint_bars(timeframe):
        return {"status": "too_young", "post_mint_bars": max(available, 0)}
    window = _slice(frame, start)
    # The guard `unsuppliable_features` cannot give: it returns [] for empty rows, so an empty
    # window reads as "everything is suppliable" and every later number is computed from nothing.
    if not window.rows:
        return {"status": "empty_window", "post_mint_bars": 0}
    if unsuppliable_features(spec, window.rows):
        return {"status": "unsuppliable", "post_mint_bars": available}

    real_r, real_closed = _totals(backtest_spec(spec, snapshot, frame=window))
    if real_closed < MIN_POST_MINT_TRADES:
        return {"status": "too_few_trades", "post_mint_bars": available, "trades": real_closed}

    matched = sum(1 for row in window.rows if evaluate_spec(spec, row).matched)
    rate = matched / len(window.rows)
    if rate <= 0:
        return {"status": "no_signal", "post_mint_bars": available}
    try:
        null = _null_spec(spec, rate)
    except SpecParseError as exc:
        return {"status": f"null_unbuildable: {exc}", "post_mint_bars": available}

    null_per_trade: list[float] = []
    for draw in range(draws):
        rng = random.Random(seed + draw)
        for row in window.rows:
            row[NULL_FEATURE] = rng.random()
        null_r, null_closed = _totals(backtest_spec(null, snapshot, frame=window))
        if null_closed >= MIN_POST_MINT_TRADES // 2:
            null_per_trade.append(null_r / null_closed)
    # The column is scratch space on rows this module does not own; leaving it behind would put a
    # feature nobody declared in front of whatever reads the frame next.
    for row in window.rows:
        row.pop(NULL_FEATURE, None)
    if not null_per_trade:
        return {"status": "null_no_trades", "post_mint_bars": available}

    real_per_trade = real_r / real_closed
    null_median = statistics.median(null_per_trade)
    return {
        "status": "measured",
        "post_mint_bars": available,
        "trades": real_closed,
        "signal_rate": round(rate, 6),
        "real_r_per_trade": round(real_per_trade, 6),
        "null_r_per_trade_median": round(null_median, 6),
        "edge_vs_null": round(real_per_trade - null_median, 6),
        "beats_null_draws": sum(1 for value in null_per_trade if real_per_trade > value),
        "null_draws": len(null_per_trade),
    }


def summarize(measurements: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The cell-independent readout: how many were measurable, and did they beat chance.

    Reports the unmeasurable counts beside the answer rather than under it. "No spec was old
    enough" and "no spec beat its null" are different facts and the record has to keep them
    apart — on the day this lands the first is the whole story.
    """
    measured = [m for m in measurements if m.get("status") == "measured"]
    statuses: dict[str, int] = {}
    for entry in measurements:
        key = str(entry.get("status") or "unknown")
        statuses[key] = statuses.get(key, 0) + 1
    if not measured:
        return {"measured_count": 0, "statuses": statuses}
    edges = [float(m["edge_vs_null"]) for m in measured]
    beats = sum(1 for m in measured if float(m["edge_vs_null"]) > 0)
    return {
        "measured_count": len(measured),
        "statuses": statuses,
        "median_edge_vs_null": round(statistics.median(edges), 6),
        "mean_edge_vs_null": round(statistics.fmean(edges), 6),
        "specs_beating_null": beats,
        "median_real_r_per_trade": round(
            statistics.median(float(m["real_r_per_trade"]) for m in measured), 6),
        "median_null_r_per_trade": round(
            statistics.median(float(m["null_r_per_trade_median"]) for m in measured), 6),
        "median_post_mint_bars": int(
            statistics.median(int(m["post_mint_bars"]) for m in measured)),
    }


def build_record(
    cells: Sequence[Mapping[str, Any]], *, now: str, symbol: str, timeframe: str,
) -> dict[str, Any]:
    """The ledger record for one fire. Evidence only — it installs and changes nothing."""
    measurements = [m for cell in cells for m in (cell.get("measurements") or [])]
    record = {
        "record_type": RECORD_TYPE,
        "symbol": symbol,
        "timeframe": timeframe,
        "cells": list(cells),
        "summary": summarize(measurements),
        "installation_effect": "NONE",
        "created_at": now,
    }
    record["record_sha256"] = integrity.sha256_record(record)
    return record


def status_line(record: Mapping[str, Any]) -> str:
    """One line for the scheduler's status column."""
    summary = record.get("summary") or {}
    measured = int(summary.get("measured_count") or 0)
    if not measured:
        statuses = summary.get("statuses") or {}
        return f"null_control measured=0 ({', '.join(f'{k}={v}' for k, v in sorted(statuses.items())) or 'no specs'})"
    return (f"null_control measured={measured} "
            f"beat={summary.get('specs_beating_null')} "
            f"edge={summary.get('median_edge_vs_null'):+.4f}R")


__all__ = [
    "LEDGER_KIND",
    "MIN_POST_MINT_DAYS",
    "NULL_DRAWS",
    "RECORD_TYPE",
    "build_record",
    "measure_spec",
    "min_post_mint_bars",
    "post_mint_index",
    "status_line",
    "summarize",
]
