"""The replay backtest: one spec replayed over a candle window into costed evidence.

Moved whole out of ``factory`` (crypto refactor plan PR-08). ``factory`` re-exports, as
the same objects, the names its callers still read there (refactor plan PR-16 removed the rest; the
set is pinned by ``test_mvp_runtime_crypto_reexport_roster.py``).

This is the part of the factory that reads nothing else in it. It takes a spec, a snapshot and
the cost model and returns evidence. It does not generate, validate or store a spec, and it
imports nothing from ``factory``: the template space there reads ``holdout_split_index`` from
here, not the other way round.

What is here:

- the evidence windows: the train/holdout split (``HOLDOUT_FRACTION``, ``holdout_split_index``),
  the walk-forward slices and the period subtotals;
- ``funding_charges_per_bar``: the funding a held bar is charged, and how well sourced it is;
- ``_replay``: ``strategy.evaluate_spec`` decides entries and ``trade_plan.settle_trade_plan``
  settles them, the same two functions the live cycle runs;
- ``ReplayFrame`` and ``build_replay_frame``: what a replay needs that does not depend on the spec;
- ``backtest_spec`` and ``backtest_spec_pooled``: the evidence block a candidate row carries.

Pure: no file, clock, environment or network read.

A test that patches a name one of these functions reads patches it here. A patch on
``factory.<name>`` rebinds ``factory``'s copy and does not reach a function defined in this
module.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from . import market_data
from .cost import (
    FUNDING_SOURCE_FALLBACK,
    FUNDING_SOURCE_PARTIAL,
    FUNDING_SOURCE_VENUE,
    MAX_ENTRY_COST_R,
    CostModel,
    apply_cost_model,
    round_trip_cost_r,
)
from .outcome_math import summarize_outcomes
from .distribution_gate import compute_distribution_reference
from .features import build_feature_rows
from .trade_plan import (
    ASSUMED_LEVERAGE, COOLDOWN_BARS_AFTER_STOPLOSS, MAINTENANCE_MARGIN_RATE,
    settle_trade_plan, stop_is_beyond_liquidation,
)
from .robustness import score_robustness
from .strategy import StrategySpec, evaluate_spec


# Walk-forward-lite: the replay window splits into this many equal-bar slices; a
# slice needs this many closed trades before its sign counts toward the pass rate.
BACKTEST_WINDOWS = 3
MIN_TRADES_PER_WINDOW = 3

# Out-of-sample holdout. The most recent slice of the replay window is withheld from
# scoring entirely: the spec is minted and scored on the earlier bars, then replayed
# once on this tail to see whether the edge survives data the score never saw.
#
# Why this exists: every number the old evidence carried — expectancy, walk-forward
# pass rate, champion_score — was computed on the SAME bars the candidate was mined
# on, and promotion then picks the highest scorer out of a growing store. Selecting
# the maximum over many draws scored in-sample is precisely how noise gets promoted,
# and no in-sample statistic can detect it. The tail is recent rather than random
# because that is the regime a promoted strategy trades next.
HOLDOUT_FRACTION = 0.30
MIN_BARS_FOR_HOLDOUT = 60

# How well-sourced each funding label is, for pooling legs that disagree. Higher is stronger;
# an unknown label sorts below every known one, so a future source this table has not been
# taught about can only ever weaken a pooled claim, never strengthen it.
_FUNDING_SOURCE_STRENGTH = {
    FUNDING_SOURCE_FALLBACK: 1,
    FUNDING_SOURCE_PARTIAL: 2,
    FUNDING_SOURCE_VENUE: 3,
}

# How many equal-bar slices the tail is subtotalled into, so a confirmation can be judged on
# market periods instead of on trades.
#
# **This was 5, and the argument for 5 was wrong — measured 2026-08-06.** That comment said
# cutting finer "does not buy independence; it buys correlated slices that LOOK like more
# evidence", and cited the +0.459 figure. But +0.459 is correlation BETWEEN CONTEXTS at the
# same moment, not between adjacent moments; the autocorrelation along the time axis, which is
# the quantity that argument needs, had never been measured. Measured now over 263,815 replayed
# trades on the 1000-day window, lag-1 autocorrelation of the block-mean gross series against
# the `-1/(K-1)` small-sample bias a white-noise series would show:
#
#   slices   days/slice   observed   iid bias   excess
#      5        200        -0.179     -0.250    +0.071
#     10        100        -0.192     -0.111    -0.081
#     15         67        -0.389     -0.071    -0.318
#     30         33        -0.127     -0.034    -0.093
#     60         17        -0.054     -0.017    -0.037
#
# **Nowhere positive.** Adjacent slices are independent or mildly mean-reverting, so finer
# slices carry real information and there is no independence argument against cutting them.
# What IS strongly dependent is the trades *inside* a slice: variance inflation over the
# holdout region measures **10-15x**, i.e. treating trades as independent understates the
# standard error by a factor of ~3.4. Both facts point the same way — the market PERIOD is the
# unit, and there is no reason to hold the count down.
#
# Ten, because that is what the window now supplies and what the candidates can occupy:
#
# - **Power.** `t(9)/sqrt(10)` against `t(4)/sqrt(5)` detects an effect **1.74x smaller**. The
#   window doubled to 1000 days in the meantime, which lengthened the tail from 150 to 300 days
#   and bought the period gate nothing at all while this stayed at 5 — the test's resolution is
#   set by the slice COUNT, not by the depth behind it.
# - **Occupancy.** Measured over the 627 specs that clear `robustness.MIN_HOLDOUT_TRADES`
#   (the only ones judgeable at all): at ten slices the median occupies **10 of 10**, and
#   **96% occupy at least 8** — the floor `robustness.MIN_HOLDOUT_PERIODS` enforces. Cutting to
#   twelve buys nothing (97%) and the 15-20 slice band is where the negative autocorrelation
#   above is sharpest, which makes the test conservative in a way that is harder to reason
#   about than it is worth.
#
# Thirty days a slice at today's window — the same slice WIDTH the old five gave at the old
# 500-day window, so nothing about a slice's internal composition changes; there are simply
# twice as many of them.
HOLDOUT_PERIODS = 10

# The SCORED region subtotalled the way the tail above already is, so temporal stability can
# one day be judged on market periods rather than on trades — the independence unit the
# 2026-08-04/06 measurements above established. Twenty is width-matched to the tail, not
# count-matched: the train span is 70% of the window against the tail's 30%, so twenty slices
# give ~35 days each — the '30 slices / 33 days' row of the autocorrelation table above
# (mildly mean-reverting, i.e. conservative), well clear of the 50-67-day band that table
# flags as hardest to reason about.
#
# **This increment records; it does not judge.** `walk_forward.temporal_stability` stays None
# until the store's own occupancy and discrimination numbers say the subtotals mean something
# (`scripts/walk_forward_stability_report.py` is the reader; the decision gate is
# `docs/proposals/WALK_FORWARD_TEMPORAL_STABILITY_V0.1.md`) — the same measured-then-moved
# order `HOLDOUT_PERIODS` itself followed off 5. Until then the scorer keeps reading absent
# evidence, so nothing about any score or verdict moves.
WALK_FORWARD_PERIODS = 20
# Below this many JUDGED periods (≥1 closed trade) stability may never be claimed — the field
# stays None, not 0: a spec whose feed covers only the newest quarter of the window must read
# "cannot measure", never "unstable" (the `factory._oi_feed_reaches` lesson, where structurally
# empty older slices retired a family for a window that had no data in it). Eight carries the
# resolution argument `robustness.MIN_HOLDOUT_PERIODS` made, and it is what stops a spec that
# traded one hot month from buying full credit off three slices.
WALK_FORWARD_MIN_PERIODS = 8


# --- replay backtest (shared evaluator + shared exit math) --------------------

def holdout_split_index(total_bars: int) -> int:
    """Where the scored window ends and the untouched holdout begins.

    Deterministic (a pure function of the bar count — no randomness, no dates), so a
    replay of the same window always splits identically. A window too short to leave
    both sides usable yields ``total_bars``: everything trains, nothing is held out,
    and the verdict layer then reports the holdout as UNCONFIRMED rather than pretending
    a two-bar tail proved something."""
    if total_bars < MIN_BARS_FOR_HOLDOUT:
        return total_bars
    return max(1, int(total_bars * (1.0 - HOLDOUT_FRACTION)))


def funding_charges_per_bar(
    candles: list[Mapping[str, Any]], funding: list[Mapping[str, Any]] | None,
    *, timeframe: str, cost: CostModel,
) -> tuple[list[float], str]:
    """Per-bar funding rates, parallel to ``candles`` → ``(charges, source)``.

    ``charges[i]`` is the sum of the settlement rates that landed **inside** bar ``i`` — i.e.
    in ``[open_time[i], open_time[i+1])`` — as fractions, the shape ``/fapi/v1/fundingRate``
    returns. A *sum*, not a level: two settlements can fall in one 1d bar-and-a-bit, and three
    always do. That is the difference from ``features._asof_align``, which carries the last
    rate at or before each bar open because a feature asks "what is the rate now" while a cost
    asks "what was charged while I held".

    Bars are half-open on purpose. A settlement exactly at a bar's open belongs to that bar, so
    summing ``charges[entry+1 : exit+1]`` charges every settlement strictly after the entry bar
    opened and up to the exit — the intervals a position opened at bar ``entry``'s close and
    closed at bar ``exit``'s actually sat through.

    With no usable series the venue's BASE rate is spread over the bar's own span
    (``cost.funding_bps_per_interval`` x settlements-per-bar), and the returned source says so.
    Never silently zero: a missing series means "unmeasured", and charging nothing for it would
    be the one direction this whole change exists to close.
    """
    from .. import timeutil as _timeutil

    charges = [0.0] * len(candles)
    if not candles:
        return charges, FUNDING_SOURCE_VENUE if funding else FUNDING_SOURCE_FALLBACK

    events: list[tuple[Any, float]] = []
    for event in funding or []:
        raw, rate = event.get("timestamp"), event.get("funding_rate")
        if not isinstance(raw, str) or not isinstance(rate, (int, float)) or isinstance(rate, bool):
            continue
        try:
            events.append((_timeutil.parse_iso(raw), float(rate)))
        except (ValueError, TypeError):
            continue

    # Settlements per bar, from the bar's own span. Sub-8h timeframes get a fraction, which
    # is right: a 15m bar sits through 1/32 of an interval on average, and charging a whole
    # one per bar would price a scalper like a swing trader.
    minutes = market_data.TIMEFRAMES.get(timeframe, 1440)
    # From the MODEL, not the module constant: Hyperliquid settles 24 times a day against
    # Binance's 3, and a per-bar charge computed from a fixed 3 is wrong by 8x on the venue the
    # archive is already collecting. The default is still 3, so nothing here moves today.
    per_bar = (minutes / 1440.0) * cost.funding_intervals_per_day
    modelled = cost.funding_bps_per_interval / 10000.0 * per_bar

    if not events:
        return [modelled] * len(candles), FUNDING_SOURCE_FALLBACK

    events.sort(key=lambda pair: pair[0])
    bar_opens: list[Any] = []
    for candle in candles:
        try:
            bar_opens.append(_timeutil.parse_iso(str(candle.get("open_time"))))
        except (ValueError, TypeError):
            bar_opens.append(None)

    # **A series that starts inside the window leaves the bars before it UNMEASURED, and the
    # bucketing below scores unmeasured as zero.** The empty-series guard above is the same
    # rule for the all-or-nothing case; this is the partial one, and it is the case a deeper
    # replay window creates. `market_data.DERIVATIVE_HISTORY_DAYS` is 520, so a 1500-day window
    # would leave roughly two thirds of its bars charged nothing while `FUNDING_SOURCE_VENUE`
    # claimed the venue had priced them — free carry on exactly the deep history a longer
    # window is bought for, and in the direction that flatters it.
    #
    # Uncovered means the bar's WHOLE span predates the series, not merely its open. A bar
    # opening at 00:00 against a first settlement at 08:00 is covered — that settlement is
    # inside it — and testing the open alone would label a fully-covered window partial and
    # over-charge its first bar. The bar's span is `[open, next_open)`, so the test is against
    # the same upper bound the bucketing below uses.
    first_event = events[0][0]
    uncovered = 0

    cursor = 0
    for i, opened in enumerate(bar_opens):
        if opened is None:
            continue
        # The next parseable bar open bounds this bar; the last bar is bounded by nothing.
        upper = next((b for b in bar_opens[i + 1:] if b is not None), None)
        if upper is not None and upper <= first_event:
            charges[i] = modelled
            uncovered += 1
            continue
        # The last bar is bounded by nothing, so it takes every remaining settlement — a trade
        # cannot close after it anyway.
        while cursor < len(events) and events[cursor][0] < opened:
            cursor += 1  # before this bar (only reachable for leading events)
        total = 0.0
        scan = cursor
        while scan < len(events) and (upper is None or events[scan][0] < upper):
            total += events[scan][1]
            scan += 1
        charges[i] = total
        cursor = scan
    return charges, (FUNDING_SOURCE_PARTIAL if uncovered else FUNDING_SOURCE_VENUE)


def _replay(
    spec: StrategySpec, rows: list[dict[str, Any]], candles: list[Mapping[str, Any]],
    *, cost: CostModel, funding: list[float] | None = None, offset: int = 0,
) -> tuple[list[dict[str, Any]], float, float, float, float, int, int, int]:
    """One pass of the live components over ``rows``. Pure; returns (outcomes, fees, slip).

    Extracted so the scored window and the holdout run through **exactly** the same
    code — a holdout evaluated by a second, slightly different replay would prove
    nothing about the first. Each pass starts flat: a position open at the split does
    not carry across, so the holdout measures only what it can attribute to itself."""
    outcomes: list[dict[str, Any]] = []
    position: dict[str, Any] | None = None
    entry_regime: str | None = None
    total_fee_cost_r = 0.0
    total_maker_fee_cost_r = 0.0
    total_slippage_cost_r = 0.0
    total_funding_cost_r = 0.0
    # Signals the economics door refused. Counted rather than dropped silently: a candidate
    # whose evidence is thin because most of its setups were uneconomic is a different fact
    # from one that simply did not fire, and only the count can tell them apart.
    uneconomic_entries = 0
    liquidation_refused = 0
    cooldown_skipped = 0
    cooldown_remaining = 0
    charges = funding if funding is not None else [0.0] * len(rows)

    for i, row in enumerate(rows):
        candle = candles[i]
        if position is not None:
            reason, exit_price, _gross_r = settle_trade_plan(
                position, candle, row.get("close"), spec.exit_rules.max_holding_bars, False
            )
            if reason is not None:
                # Every settlement strictly after the entry bar opened, through the exit bar.
                # The entry bar itself is excluded: the position opens at that bar's CLOSE, so
                # a settlement inside it happened before the trade existed.
                carry = sum(charges[position["entry_index"] + 1 : i + 1])
                breakdown = apply_cost_model(
                    position["direction"], position["entry_price"], float(exit_price),
                    position["risk"], cost=cost, close_reason=reason,
                    funding_rate_sum=carry,
                )
                total_fee_cost_r += breakdown.fee_cost_r
                total_maker_fee_cost_r += breakdown.maker_fee_cost_r
                total_slippage_cost_r += breakdown.slippage_cost_r
                total_funding_cost_r += breakdown.funding_cost_r
                resolution = position.get("exit_resolution") or "unambiguous"
                outcome: dict[str, Any] = {
                    "outcome_closed": True,
                    "result_R": breakdown.net_r,
                    "gross_R": breakdown.gross_r,
                    "fee_cost_R": breakdown.fee_cost_r,
                    "maker_fee_cost_R": breakdown.maker_fee_cost_r,
                    "slippage_cost_R": breakdown.slippage_cost_r,
                    "funding_cost_R": breakdown.funding_cost_r,
                    "close_reason": reason,
                    "created_at_utc": candle.get("close_time"),
                    "strategy_id": spec.strategy_id,
                    "entry_regime": entry_regime,
                    "closed_at_bar": offset + i,
                    "exit_resolution": resolution,
                }
                if resolution == "pessimistic_sl_first":
                    direction = position["direction"]
                    entry = float(position["entry_price"])
                    tp = float(position["take_profit"])
                    risk = float(position["risk"])
                    alt_gross_r = (tp - entry) / risk if direction == "LONG" else (entry - tp) / risk
                    outcome["ambiguous_gap_r"] = round(alt_gross_r - breakdown.gross_r, 8)
                outcomes.append(outcome)
                position = None
                entry_regime = None
                if reason == "stop_loss":
                    cooldown_remaining = COOLDOWN_BARS_AFTER_STOPLOSS
        if position is None:
            if cooldown_remaining > 0:
                cooldown_remaining -= 1
                cooldown_skipped += 1
                continue
            close, atr = row.get("close"), row.get("atr")
            if not (isinstance(close, (int, float)) and isinstance(atr, (int, float)) and close > 0 and atr > 0):
                continue
            if not evaluate_spec(spec, row).matched:
                continue
            stop_distance = spec.exit_rules.stop_atr * atr
            target_distance = spec.exit_rules.target_atr * atr
            long = spec.direction.value != "short"
            # **The runtime's economics door, applied where the evidence is made.**
            # `cost.MAX_ENTRY_COST_R` refuses a plan whose round trip eats more than a quarter
            # of its own R, and until 2026-08-02 only the two RUNTIME doors enforced it
            # (`trade_plan.entry_cost_refusal`, `live_entry`). The backtest scored every signal, so
            # a candidate's expectancy described a population the runtime would not trade —
            # measured on this store: 48 of 50 15m entries refused, 11 of 17 at 1h, none at 4h.
            #
            # The gap is not noise, it is selection: cost in R is `fee_bps / stop_bps`, so what
            # the door removes is systematically the TIGHT-stop, low-ATR bars — the expensive
            # ones. Scoring them into a candidate's mean answers a question nobody asked, and
            # answers it pessimistically at the fast end where the door removes almost
            # everything.
            #
            # Priced exactly as the doors price it — taker in, taker plus adverse slippage out,
            # through the same `round_trip_cost_r` and the same constant — so a spec cannot be
            # economic at one door and uneconomic at another.
            if round_trip_cost_r(
                "LONG" if long else "SHORT", float(close), abs(stop_distance), cost=cost
            ) > MAX_ENTRY_COST_R:
                uneconomic_entries += 1
                continue
            stop_price = close - stop_distance if long else close + stop_distance
            if stop_is_beyond_liquidation(float(close), stop_price, long):
                liquidation_refused += 1
                continue
            position = {
                "direction": "LONG" if long else "SHORT",
                "entry_price": float(close),
                "stop_loss": close - stop_distance if long else close + stop_distance,
                "take_profit": close + target_distance if long else close - target_distance,
                "risk": abs(stop_distance),
                # The stop-management rules, carried onto the position so the replay settles
                # through exactly the same `settle_trade_plan` the live cycle runs. Trail is
                # converted to price HERE, where the ATR is known, for the reason
                # `advance_managed_stop` states: the settlement is a pure function of prices.
                "breakeven_at_r": spec.exit_rules.breakeven_at_r,
                "trail_distance": (
                    spec.exit_rules.trail_atr * atr if spec.exit_rules.trail_atr is not None else None
                ),
                "holding_candles": 0,
                # Where the carry starts. Kept on the position rather than in a parallel
                # variable so a settlement can only ever bill the window of the trade that is
                # actually open — the two cannot drift apart.
                "entry_index": i,
            }
            entry_regime = row.get("market_regime")
    return (outcomes, total_fee_cost_r, total_maker_fee_cost_r, total_slippage_cost_r,
            total_funding_cost_r, uneconomic_entries, cooldown_skipped, liquidation_refused)


# A feature the rows never supply, and why minting on one is not merely wasteful.
#
# `mark_price`, `index_price` and `mark_index_basis_bps` are **None on every bar** unless the
# snapshot carries `mark_prices`/`index_prices`, which this runtime's collector does not. The
# fail-closed evaluator reads None as indeterminate, so a spec naming one never matches — no
# trade, which is the honest outcome and not the problem.
#
# The problem is that 9 candidates in this store carry backtest evidence with `closed_count > 0`
# on exactly those columns. They are C7 imports: scored in the source system, where the feed
# supplied values. Here they are rankable, promotable in principle, and structurally incapable
# of ever entering — evidence that says "this traded and made X" for a trade this runtime cannot
# reproduce. That is a backtest/live divergence wearing a perfectly ordinary candidate record.
#
# Refused at MINT, where it costs nothing and the replay rows are right there to ask. A
# read-time tier over the 20 already stored is a separate increment: it needs to know what the
# feed supplies NOW, which `pool` cannot see.
#
# "Never supplied" is measured over the replay, not declared in a list. A list would go stale
# the day a feed is configured, and would have to be maintained in the opposite direction from
# the truth — the rows already know.
UNSUPPLIABLE_FEATURE = "references a feature this runtime never supplies"


def unsuppliable_features(spec: StrategySpec, rows: list[dict[str, Any]]) -> list[str]:
    """The spec's referenced features that are None on every replay row.

    Empty rows return empty: nothing was observed either way, and a caller with no data must not
    be told a feature is missing — that is a claim, and this function only reports what it saw.
    """
    if not rows:
        return []
    missing = []
    for name in sorted(spec.referenced_features()):
        if all(row.get(name) is None for row in rows):
            missing.append(name)
    return missing


# How many EARLIER tails a spec is scored on beside its own, each ending exactly where the
# previous one begins. Three reaches back about four times the holdout's own depth.
#
# **Why this exists at all.** `REMAINING_WORK.md` §F9 records the first result the pooled door
# ever produced: five `oi_unwind_short` draws clearing a selection-adjusted bar at 4h, t up to
# 5.16, holdout expectancy +0.62R. Walked backwards into adjacent windows, **16 of 16 draws
# across two seed namespaces** decayed to −0.23…−0.31R. The effect was a property of the newest
# ~500 days, not of the rule. A promotion door reading only the newest tail takes that row on
# its face.
#
# **And the field that looks like it already covers this does not.** `period_r` partitions the
# holdout INTO slices, and the reversal is 2,000+ bars before the holdout begins; measured
# 2026-08-08 on those same draws, all ten slices read positive. The two answer different
# questions — *"was the tail uniform"* and *"does the tail's answer hold before it"*.
#
# **It is cheap, which is the reason it can sit on the mint path.** Every FEATURE is a
# trailing-window computation (`rolling_percentile` over `PERCENTILE_WINDOW`, the z-scores over
# their own), so a prefix of a built frame carries exactly the values those bars had in the full
# series. This is the **lookahead guard**: it ensures no feature at bar i depends on bars
# after i, which is what makes replay-live parity hold on differently-sized candle windows.
# Pinned by two tests:
#   - `test_slicing_a_built_frame_equals_rebuilding_it_except_the_last_bars_funding`
#     (OHLCV + funding + taker_flow, the original 200-bar verification)
#   - `test_prefix_invariance_holds_with_htf_and_external_series`
#     (adds close-time-keyed HTF columns and backward-asof liquidation events)
# No `build_feature_rows` call, no refetch; every earlier window is a prefix of candles already
# in hand, and only the replay repeats, over 0.7 + 0.49 + 0.34 of the series.
#
# **The funding series is the one exception and it is one bar wide.** `funding_charges_per_bar`
# spreads settlements across the bars they fall in, so the FINAL bar of a prefix was charged
# knowing the series continued past it; rebuilding from clipped candles gives that bar a
# different charge. Measured on a 200-bar fixture: identical at every index except the last.
# It is left rather than repaired because repairing it needs the raw funding events, which a
# `ReplayFrame` deliberately does not carry — and because the bar in question is the one a
# position cannot settle on anyway, there being no bar after it to settle against. A/B against
# the rebuild on the live 5-symbol cohort produced identical R and identical trade counts in
# every window.
PRIOR_WINDOWS = 3


def _prefix_frame(frame: "ReplayFrame", keep: int) -> "ReplayFrame | None":
    """The same frame over its first ``keep`` bars, or ``None`` if that leaves no holdout.

    ``split`` is recomputed by :func:`holdout_split_index` rather than scaled, so the prefix is
    split by the same rule the full frame was — which is what makes window *k+1*'s tail end
    exactly where window *k*'s begins.

    **Rows are exact; the last bar's funding charge is not** — see the note above
    :data:`PRIOR_WINDOWS`. A test pins both halves of that, so the day it stops being one bar
    wide is a red suite rather than a drift in the numbers.
    """
    if keep < MIN_BARS_FOR_HOLDOUT or keep > len(frame.rows):
        return None
    split = holdout_split_index(keep)
    if split >= keep:                       # everything trains, nothing is held out
        return None
    return ReplayFrame(
        rows=frame.rows[:keep], candles=frame.candles[:keep], funding=frame.funding[:keep],
        funding_source=frame.funding_source, split=split, cost=frame.cost,
        symbol=frame.symbol,   # a prefix of one market is still that market
    )


def _prior_window_evidence(
    spec: StrategySpec, frames: Sequence["ReplayFrame"], *, cost: CostModel,
    windows: int = PRIOR_WINDOWS,
) -> tuple[list[float], list[int]]:
    """(R summed, trades) per earlier tail, newest first. Shorter lists mean the series ran out.

    Reports rather than judges — like `period_r`, and for the same reason: what a sign flip
    across these windows should DO at the promotion door is a decision, and a measurement that
    pre-empts it is harder to argue with than one that states itself.
    """
    r_by_window: list[float] = []
    n_by_window: list[int] = []
    keep = min(len(f.rows) for f in frames) if frames else 0
    for _ in range(windows):
        keep = holdout_split_index(keep) if keep >= MIN_BARS_FOR_HOLDOUT else 0
        prefixes = [p for p in (_prefix_frame(f, keep) for f in frames) if p is not None]
        if len(prefixes) != len(frames) or not prefixes:
            break                            # a leg ran out; a partial cohort is a different pool
        total_r = 0.0
        closed = 0
        for prefix in prefixes:
            part, *_ = _replay(spec, prefix.rows[prefix.split:], prefix.candles[prefix.split:],
                               cost=cost, funding=prefix.funding[prefix.split:],
                               offset=prefix.split)
            for outcome in part:
                if outcome.get("outcome_closed"):
                    total_r += float(outcome.get("result_R") or 0.0)
                    closed += 1
        r_by_window.append(round(total_r, 8))
        n_by_window.append(closed)
    return r_by_window, n_by_window


def _holdout_evidence(
    spec: StrategySpec, frames: Sequence["ReplayFrame"],
    *, cost: CostModel, funding_source: str = FUNDING_SOURCE_VENUE,
) -> dict[str, Any]:
    """What the spec did on bars that never touched its score. Compact by design.

    Only the few numbers a confirmation needs: how many trades the unseen tail
    produced and whether they were profitable in aggregate. The verdict layer turns
    that into CONFIRMED / CONTRADICTED / INSUFFICIENT — this function judges nothing.

    Takes **frames**, plural, and pools their tails into one block. A spec scoped to
    several symbols is one hypothesis fitted on all of them, so its tail is all of their
    tails — which is the whole reason a pooled spec can reach ``MIN_HOLDOUT_TRADES``
    where a single-symbol one at the same timeframe cannot. ``bars`` stays the
    PER-SYMBOL depth (the shallowest frame's, so the claim is bounded by the leg that
    saw least) because `candidate_ranking.evidence_depth_of` reads it as a calendar span: five
    symbols over 150 days is still 150 days of market, seen five times. ``symbols``
    says how many times.
    """
    outcomes: list[dict[str, Any]] = []
    fees = maker_fees = slippage = carry = 0.0
    uneconomic = 0
    bars: list[int] = []
    # Pooled by period INDEX, not by frame: every frame here is the same timeframe over the
    # same window, so period k is the same calendar slice on each symbol. That is the point —
    # what makes the shared tail one observation rather than many is that the market is the
    # same in it, so the symbols must be summed inside a period, never treated as extra ones.
    period_r = [0.0] * HOLDOUT_PERIODS
    period_trades = [0] * HOLDOUT_PERIODS
    for frame in frames:
        part, part_fees, part_maker, part_slip, part_carry, part_uneconomic, _cd, _liq = _replay(
            spec, frame.rows[frame.split:], frame.candles[frame.split:],
            cost=cost, funding=frame.funding[frame.split:], offset=frame.split,
        )
        outcomes.extend(part)
        fees += part_fees
        maker_fees += part_maker
        slippage += part_slip
        carry += part_carry
        uneconomic += part_uneconomic
        tail_bars = len(frame.rows) - frame.split
        bars.append(tail_bars)
        width = max(1, tail_bars // HOLDOUT_PERIODS)
        for outcome in part:
            closed_at = outcome.get("closed_at_bar")
            if not isinstance(closed_at, int):
                continue
            index = min(HOLDOUT_PERIODS - 1, max(0, (closed_at - frame.split) // width))
            period_r[index] += float(outcome["result_R"])
            period_trades[index] += 1
    results = [float(o["result_R"]) for o in outcomes]
    total_r = round(sum(results), 8)
    closed = len(outcomes)
    prior_r, prior_n = _prior_window_evidence(spec, frames, cost=cost)
    return {
        "bars": min(bars) if bars else 0,
        # Absent on every block minted before pooling existed, and 1 is what those mean —
        # `pool` and `robustness` read the count only to describe the evidence, never to
        # gate on it, so a missing field degrades to the single-symbol reading it had.
        "symbols": len(frames),
        "closed_count": closed,
        "win_count": sum(1 for r in results if r > 0),
        "total_R": total_r,
        "expectancy": round(total_r / closed, 8) if closed else 0.0,
        # The spread the confirmation is judged against. Without it `holdout_status` compared a
        # mean to zero, which cannot fail on a small tail — the whole reason it now needs an
        # interval. Sample stdev (n-1), matching `dashboard.sample_verdict`: these trades are a
        # sample of what the spec would do, never the whole of it, and `pstdev` understates the
        # spread of exactly that inference. 0.0 below two trades, where the statistic is
        # undefined; the trade floor refuses that block anyway, and a stored 0.0 reads as
        # INSUFFICIENT rather than as a zero-width interval that excludes zero for free.
        "stdev_r": round(statistics.stdev(results), 8) if closed >= 2 else 0.0,
        # **The spread above is drawn over TRADES, and trades are not independent draws.**
        # Measured 2026-08-04 over 700 frozen specs and 113,127 replayed trades: the same
        # market periods are good or bad for everything at once — mean pairwise correlation of
        # per-block gross across the ten routed contexts is **+0.459**, and block-to-block
        # dispersion of gross is 0.1087R against an effect being hunted at 0.01-0.05R. So the
        # unit of independence is the market PERIOD, and `stdev_r / sqrt(closed_count)`
        # overstates precision by roughly sqrt(trades / periods).
        #
        # These two lists are what makes the honest interval computable at all: the tail's R,
        # subtotalled over equal-bar slices. `robustness.holdout_status` draws its interval
        # over them; nothing else can, because the per-trade timestamps are not stored.
        # See `HOLDOUT_PERIODS` for why the count is what it is.
        "period_r": [round(value, 8) for value in period_r],
        "period_trades": period_trades,
        # The same spec on EARLIER tails, newest first — see `PRIOR_WINDOWS`. Sibling of the two
        # fields above and deliberately not folded into them: `period_r` cuts the holdout up,
        # this asks whether the holdout's answer survives before it. A shorter list than
        # `PRIOR_WINDOWS` means the series ran out, which is information, not an error.
        "prior_window_r": prior_r,
        "prior_window_trades": prior_n,
        # The holdout runs the same door as the scored window — a confirmation measured over a
        # wider population than the score would not be confirming the same thing.
        "refused_entries": uneconomic,
        # The cost breakdown the main evidence has carried all along, and this block did not.
        # Without it a holdout cannot be re-derived at another taker rate the way the main
        # figures can, so a rate change leaves `holdout_status` — which gates ROBUST — stuck
        # on whatever rate happened to be current when the candidate was minted. The replay
        # already computes these; only the return dropped them.
        "fee_cost_r": round(fees, 8),
        # The maker share of `fee_cost_r`, without which the re-derivation above is wrong rather
        # than merely unavailable: `expectancy_at` scales the taker rate, and scaling a maker fee
        # by it would report a rate this candidate never faced on that leg.
        "maker_fee_cost_r": round(maker_fees, 8),
        "slippage_cost_r": round(slippage, 8),
        # Signed, and on the same footing as the fee legs for the same reason: a holdout whose
        # carry is invisible cannot be compared against a scored window whose carry is not.
        "funding_cost_r": round(carry, 8),
        "cost_model": {
            "taker_fee_bps": cost.taker_fee_bps,
            "maker_fee_bps": cost.maker_fee_bps,
            "slippage_bps": cost.slippage_bps,
            # Stamped since Thomas's 2026-08-11 direction: the stop leg's own rate joined
            # `candidate_ranking.cost_basis_rank`'s comparison, and a row that does not say what its
            # stops paid is judged on its general slippage (the pre-split identity) — so a
            # 12bps-scored row MUST carry the field or it reads OPTIMISTIC forever.
            "stop_slippage_bps": cost.stop_slippage_bps,
            "funding_bps_per_interval": cost.funding_bps_per_interval,
            "funding_source": funding_source,
        },
    }


@dataclass(frozen=True)
class ReplayFrame:
    """The spec-INDEPENDENT half of a backtest, computed once and replayed many times.

    Features, candles and the carry series are properties of the market and the calendar. The
    spec decides only which bars it enters on. `backtest_spec` nevertheless rebuilt all three
    per spec, and `build_feature_rows` is the expensive one: **6.0 seconds at 48,000 bars**,
    which is the 15m factory window (500 calendar days). A batch of four plus fusion children
    therefore spent ~30 seconds per fire recomputing an identical frame — on a scheduler that
    runs schedules sequentially, and shares that tick with the live leg.

    ``cost`` is carried because the carry series depends on it: with no venue funding history
    `funding_charges_per_bar` spreads ``cost.funding_bps_per_interval`` over each bar. A frame
    built under one cost model and replayed under another would silently score trades against
    rates they never faced, which is the failure `candidate_ranking.cost_basis_rank` exists to catch after
    the fact — so `backtest_spec` refuses the mismatch at the door instead.
    """

    rows: list[dict[str, Any]]
    candles: list[Mapping[str, Any]]
    funding: list[float]
    funding_source: str
    split: int
    cost: CostModel
    # Which market this frame IS. Absent until 2026-08-23, and the omission is what made a
    # pooled evidence block able to say `symbols_replayed: 5` and nothing about the five: the
    # scored loop already replays one frame at a time, so the per-symbol figures were computed
    # and then summed away with no way to label them on the way past. Optional because a frame
    # built by a caller that predates the field still replays correctly — it just cannot
    # contribute a labelled leg, which reads as an absent breakdown rather than a wrong one.
    symbol: str | None = None


def build_replay_frame(
    snapshot: Mapping[str, Any], *, cost: CostModel | None = None
) -> ReplayFrame:
    """Everything a replay needs that does not depend on the spec. Pure.

    The train/holdout split is computed here too (see HOLDOUT_FRACTION): features are built over
    the FULL series and only then sliced, so the holdout starts with warm indicators instead of
    re-warming — the split is about what the SCORE may see, not about the data itself."""
    cost = cost or CostModel()
    rows = build_feature_rows(dict(snapshot))
    candles = list(snapshot.get("candles") or [])
    funding, funding_source = funding_charges_per_bar(
        candles, snapshot.get("funding"),
        timeframe=str(snapshot.get("timeframe") or "1d"), cost=cost,
    )
    return ReplayFrame(
        rows=rows, candles=candles, funding=funding, funding_source=funding_source,
        split=holdout_split_index(len(rows)), cost=cost,
        symbol=(str(snapshot.get("symbol")) if snapshot.get("symbol") else None),
    )


def backtest_spec(
    spec: StrategySpec, snapshot: Mapping[str, Any], *, cost: CostModel | None = None,
    frame: ReplayFrame | None = None,
) -> dict[str, Any]:
    """Replay ``spec`` over one snapshot's history. Deterministic, pure.

    The single-symbol form of :func:`backtest_spec_pooled`, and byte-identical to what
    this function returned before pooling existed — every caller that mines one
    ``(symbol, timeframe)`` context keeps exactly the evidence it had.

    Uses the exact live-path components: ``evaluate_spec`` decides entries on row i,
    the position opens at row i's close with the spec's ATR exits, and every later
    bar settles through ``trade_plan.settle_trade_plan`` (pessimistic SL-first, the spec's
    own ``max_holding_bars`` as the time exit — backtest semantics). Rows whose
    features are indeterminate never enter (the evaluator's rule).

    C12: every closed trade's gross (intended-price) R is costed via
    ``cost.apply_cost_model`` (fees + slippage, source S4b). ``result_R`` on each
    outcome — and therefore ``expectancy``/``champion_score`` for this spec — is the
    NET R after costs; ``gross_R`` rides alongside for transparency."""
    return backtest_spec_pooled(
        spec, [snapshot], cost=cost, frames=None if frame is None else [frame]
    )


def backtest_spec_pooled(
    spec: StrategySpec, snapshots: Sequence[Mapping[str, Any]], *,
    cost: CostModel | None = None, frames: Sequence[ReplayFrame] | None = None,
) -> dict[str, Any]:
    """Replay one spec across several symbols' histories and pool the result.

    **One hypothesis, N symbols' worth of evidence.** Every spec in the store is scoped
    to a single symbol (``build_spec_dict`` defaults to ``[symbol]``), so the factory's
    evidence-per-hypothesis ratio is fixed by how often one symbol signals — and at 4h
    and 1d that is below `robustness.MIN_HOLDOUT_TRADES`, which is why
    `REMAINING_WORK.md` F1 finds the search producing evidence it cannot confirm.
    Pooling moves the ratio the only way that helps: adding a family costs +1 hypothesis
    at the same data, adding a symbol as its own context costs +N hypotheses at +N data,
    and this is +0 hypotheses at +N data.

    It is also the harder thing to overfit, which is the second reason: one parameter set
    has to hold on BTC and DOGE at once. The feature vocabulary was already built for
    that — ``NUMERIC_FEATURES`` admits only normalized columns precisely so "a mined
    threshold carries the same meaning on BTC and on SOL" — and nothing had used it.

    Preconditions, fail-closed rather than reconciled: every frame must carry the same
    cost model (a pooled figure mixing rates describes no book), and the caller is
    responsible for handing frames of ONE timeframe, since the walk-forward slices below
    index by bar and bar *i* only names the same calendar window across symbols when the
    bar length is the same.

    ``bars_replayed`` stays the per-symbol scored depth for the reason ``holdout.bars``
    does; ``symbols_replayed`` is how many legs are behind it. This does **not** decide
    what the factory mints — ``run_factory`` is untouched, and moving the rotation onto
    pooled specs is a separate decision that wants generations of evidence, not this
    function landing.
    """
    if not snapshots and not frames:
        raise ValueError("a pooled backtest needs at least one snapshot or frame")
    cost = cost or CostModel()
    # Reused when the caller already built them (`run_factory` builds one per fire and replays
    # every spec and fusion child through it), rebuilt when it did not. A frame from a different
    # cost model is refused rather than used: see `ReplayFrame`.
    if frames is None:
        frames = [build_replay_frame(snapshot, cost=cost) for snapshot in snapshots]
    else:
        for frame in frames:
            if frame.cost != cost:
                raise ValueError(
                    "replay frame was built under a different cost model than this backtest "
                    "charges; the carry series would price trades at rates they never faced"
                )
    # The weaker label wins when the legs disagree: a pooled carry is only as well-sourced as
    # its worst leg, and reporting `venue_history` over a book where one symbol fell back to
    # the modelled rate would overstate what the funding figure is evidence of.
    #
    # Ordered rather than a VENUE/not-VENUE test, because there are three answers now and
    # collapsing the middle one loses the distinction it was added to carry: a leg with a
    # PARTIAL series measured most of its window, and calling that `modelled_constant` would
    # understate it exactly as calling it `venue_history` overstated it.
    funding_source = min(
        (f.funding_source for f in frames),
        key=lambda source: _FUNDING_SOURCE_STRENGTH.get(source, 0),
        default=FUNDING_SOURCE_VENUE,
    )
    outcomes: list[dict[str, Any]] = []
    total_fee_cost_r = total_maker_fee_cost_r = total_slippage_cost_r = total_funding_cost_r = 0.0
    uneconomic_entries = 0
    cooldown_entries = 0
    liquidation_entries = 0
    scored_bars: list[int] = []
    per_symbol: dict[str, dict[str, Any]] = {}
    for frame in frames:
        split = frame.split
        part, part_fees, part_maker, part_slip, part_carry, part_uneconomic, part_cooldown, part_liq = _replay(
            spec, frame.rows[:split], frame.candles[:split],
            cost=cost, funding=frame.funding[:split],
        )
        outcomes.extend(part)
        # The leg, kept rather than summed away. `part` is exactly this symbol's scored trades
        # and it is discarded on the next line, which is why a pooled block could report how
        # MANY symbols it replayed and nothing about any of them. A pooled expectancy is an
        # average across markets, so "the cohort earns +0.29R" and "each of the five earns
        # about +0.29R" are different claims and only the second licenses trading one of them.
        if frame.symbol:
            leg = summarize_outcomes(part)
            per_symbol[frame.symbol] = {
                "closed_count": leg["closed_count"],
                # **None, not 0.0, when the leg never traded.** `summarize_outcomes` returns
                # 0.0 for an empty population and that is right for a total — a book with no
                # trades has made no money. It is wrong for a leg being COMPARED against its
                # siblings, which is this block's only purpose: at 0.0 "this symbol broke even"
                # and "this symbol never entered" are the same number, and the second is the
                # one that must stop a promotion. `closed_count` disambiguates them for a
                # reader who checks it, and the whole reason this block exists is that someone
                # will scan the expectancies. Same absent-means-unknown contract the block
                # already keeps for a frame with no symbol.
                "expectancy": leg["expectancy"] if leg["closed_count"] else None,
                "total_r": round(sum(o["result_R"] for o in part), 8),
                "win_count": leg["win_count"],
            }
        total_fee_cost_r += part_fees
        total_maker_fee_cost_r += part_maker
        total_slippage_cost_r += part_slip
        total_funding_cost_r += part_carry
        uneconomic_entries += part_uneconomic
        cooldown_entries += part_cooldown
        liquidation_entries += part_liq
        scored_bars.append(split)
    holdout = _holdout_evidence(spec, frames, cost=cost, funding_source=funding_source)

    summary = summarize_outcomes(outcomes)

    # Regime breadth: which regimes this spec actually traded in, and how many of
    # them were profitable in aggregate (the scorer's fitted-to-one-regime signal).
    regime_r: dict[str, float] = {}
    for outcome in outcomes:
        regime = str(outcome.get("entry_regime") or "UNCLEAR")
        regime_r[regime] = regime_r.get(regime, 0.0) + outcome["result_R"]
    regime_trades: dict[str, int] = {}
    for outcome in outcomes:
        regime = str(outcome.get("entry_regime") or "UNCLEAR")
        regime_trades[regime] = regime_trades.get(regime, 0) + 1
    regime_breakdown = {
        "regimes_traded": sorted(regime_r),
        "profitable_regime_count": sum(1 for total in regime_r.values() if total > 0),
        # Which regime produced what, kept rather than collapsed. The two fields above are
        # what `robustness` needs (a count, for its breadth term) and for a long time they
        # were all this block recorded — so the loop computed a per-regime R and then threw
        # away the only thing that says WHERE the edge was. That is the input a router needs
        # to decline a regime a strategy has already demonstrated it loses in, which is what
        # `trade_plan.regime_admits` now reads through the pool entry.
        "per_regime": {
            regime: {"trades": regime_trades[regime], "total_r": round(total, 8)}
            for regime, total in sorted(regime_r.items())
        },
    }

    # Intrabar ambiguity: trades where a single bar touched both SL and TP. The
    # pessimistic SL-first assumption applies to all of them in the backtest, and the
    # gap is the maximum R a more favourable resolution could have added.
    ambiguous_exits = sum(
        1 for o in outcomes if o.get("exit_resolution") == "pessimistic_sl_first"
    )
    ambiguous_gap_r = round(sum(
        float(o.get("ambiguous_gap_r") or 0.0) for o in outcomes
    ), 8)

    # Walk-forward-lite: equal-bar slices of the replay; a slice's sign counts only
    # with enough trades. The shallowest leg sets the slice width, so a trade closing late on
    # a longer leg clamps into the last window rather than opening a window the other legs
    # never reached. Bar *i* is the same calendar window on every leg — that is the
    # one-timeframe precondition above, and it is what makes pooling by `closed_at_bar`
    # mean anything.
    window_bars = max(1, min(scored_bars) // BACKTEST_WINDOWS)
    window_r: dict[int, list[float]] = {}
    for outcome in outcomes:
        window_r.setdefault(min(outcome["closed_at_bar"] // window_bars, BACKTEST_WINDOWS - 1), []).append(
            outcome["result_R"]
        )
    counted = [values for values in window_r.values() if len(values) >= MIN_TRADES_PER_WINDOW]
    # The scored region subtotalled the way `_holdout_evidence` subtotals the tail — same
    # field names, same clamp rule, and net R like everything here (`result_R` is costed).
    # These are `temporal_stability`'s INPUTS, recorded so the store can measure whether they
    # discriminate before anything judges on them; the field itself stays None until that
    # decision is taken on the store's own numbers (see WALK_FORWARD_PERIODS). The scorer
    # reads None as absent evidence, so this block moves no score and no verdict — and the
    # holdout suite's "changing only the tail leaves walk_forward identical" test now pins
    # these subtotals to the scored region for free.
    wf_width = max(1, min(scored_bars) // WALK_FORWARD_PERIODS)
    wf_period_r = [0.0] * WALK_FORWARD_PERIODS
    wf_period_trades = [0] * WALK_FORWARD_PERIODS
    for outcome in outcomes:
        index = min(outcome["closed_at_bar"] // wf_width, WALK_FORWARD_PERIODS - 1)
        wf_period_r[index] += float(outcome["result_R"])
        wf_period_trades[index] += 1
    walk_forward = {
        "walk_forward_pass_rate": (
            sum(1 for values in counted if sum(values) > 0) / len(counted) if counted else None
        ),
        "temporal_stability": None,
        "windows": BACKTEST_WINDOWS,
        "windows_counted": len(counted),
        "period_r": [round(value, 8) for value in wf_period_r],
        "period_trades": wf_period_trades,
        "periods": WALK_FORWARD_PERIODS,
        "periods_judged": sum(1 for n in wf_period_trades if n > 0),
    }

    # C12: total_net_r is the sum of costed R over every closed trade — the
    # scorer's cost_robustness reads what FRACTION of the pre-cost edge survives
    # fees + slippage (net / (net + costs)), so it needs sums, not per-trade means.
    total_net_r = round(sum(o["result_R"] for o in outcomes), 8)
    cost_metrics = {
        "trade_count": summary["closed_count"],
        "total_net_r": total_net_r,
        "fee_cost_r": round(total_fee_cost_r, 8),
        "slippage_cost_r": round(total_slippage_cost_r, 8),
        # Included so `cost_robustness` measures what fraction of the edge survives the costs
        # this book ACTUALLY pays. On a 1d spec holding 12-48 days the carry is several times
        # the fee legs, so a robustness score computed without it was answering a question
        # about a cheaper instrument than the one being traded.
        "funding_cost_r": round(total_funding_cost_r, 8),
    }
    robustness = score_robustness(spec, cost_metrics, walk_forward, regime_breakdown, holdout=holdout)
    return {
        "strategy_id": spec.strategy_id,
        "strategy_rule_hash": spec.strategy_rule_hash,
        "closed_count": summary["closed_count"],
        "expectancy": summary["expectancy"],
        # The spread beside the mean, so `expectancy` can be read as a measurement rather than
        # a number. `win_count`/`avg_win_R`/`avg_loss_R` are enough to reconstruct a two-point
        # approximation of it, and that approximation is biased LOW — it collapses the spread
        # within wins and within losses — which inflates every t derived from it. Recording the
        # real one costs one line and removes the temptation.
        #
        # Sample stdev (n-1), matching `holdout.stdev_r` and `dashboard.sample_verdict`: these
        # trades are a sample of what the spec would do on this market, never the whole of it.
        # 0.0 below two trades, where the statistic is undefined — `robustness.expectancy_t`
        # reads that back as "cannot compute", not as a zero-width interval.
        "stdev_r": round(statistics.stdev(o["result_R"] for o in outcomes), 8)
        if summary["closed_count"] >= 2 else 0.0,
        "win_count": summary["win_count"],
        "loss_count": summary["loss_count"],
        # M4a: realized payoff legs, so a candidate carries its win-rate and realized
        # reward:risk (avg_win_R / avg_loss_R) for the second-pass promotion ranking.
        "avg_win_R": summary["avg_win_R"],
        "avg_loss_R": summary["avg_loss_R"],
        "max_drawdown": summary["max_drawdown"],
        # **What the economics door removed, and that it ran at all.** Deliberately its own
        # block rather than a term in `cost_summary`: the cost basis answers "at what RATES was
        # this scored", and a tier ordering built on it refuses evidence scored more cheaply
        # than the venue charges. This is a different axis — the same rates over a NARROWER
        # population — so folding it in would silently retier the whole store on a question the
        # tier was not asked.
        #
        # Absence of this block is how a reader tells evidence minted before 2026-08-02, when
        # the backtest scored every signal and a candidate's expectancy described trades the
        # runtime would have refused. Whether the promotion door should REQUIRE the block is a
        # separate decision and deliberately not taken here: requiring it would refuse the
        # entire existing store at once, and the convergence path is the same re-minting the
        # cost basis already relies on.
        "entry_cost_door": {
            "applied": True,
            "max_entry_cost_r": MAX_ENTRY_COST_R,
            # Refusal EVENTS, not distinct opportunities, and the difference is easy to trip
            # over: a refused signal leaves the book flat, so the same setup can be refused
            # again on the very next bar, while an accepted one occupies bars until it closes.
            # Measured on BTCUSDT 15m, 672 trades without the door against 3,613 refusals with
            # it. So this number over `closed_count` is NOT a rejection rate — it reads as one
            # and would be wrong by an order of magnitude. What it is good for is comparing two
            # specs on the same bars, and for seeing at a glance that a spec's setups mostly
            # arrive on bars too quiet to pay for themselves.
            "refused_entries": uneconomic_entries,
        },
        "cooldown_door": {
            "applied": True,
            "cooldown_bars": COOLDOWN_BARS_AFTER_STOPLOSS,
            "skipped_entries": cooldown_entries,
        },
        "intrabar_gap": {
            "ambiguous_exits": ambiguous_exits,
            "total_gap_r": ambiguous_gap_r,
            "gap_per_trade_r": round(ambiguous_gap_r / ambiguous_exits, 4) if ambiguous_exits else 0.0,
        },
        "liquidation_guard": {
            "applied": True,
            "assumed_leverage": ASSUMED_LEVERAGE,
            "maintenance_margin_rate": MAINTENANCE_MARGIN_RATE,
            "refused_entries": liquidation_entries,
        },
        "cost_summary": {
            "total_net_r": total_net_r,
            "total_fee_cost_r": round(total_fee_cost_r, 8),
            # The maker share of the line above. `candidate_ranking.expectancy_at` re-derives an old
            # candidate's expectancy at a different TAKER rate, and that algebra is linear in the
            # taker portion only — so the portion has to be recorded, not inferred. A record
            # without this field predates the maker exit and is all-taker by construction, which
            # is exactly how `expectancy_at` reads a missing value.
            "total_maker_fee_cost_r": round(total_maker_fee_cost_r, 8),
            "total_slippage_cost_r": round(total_slippage_cost_r, 8),
            # SIGNED, unlike the two above: a short book in a positive-funding regime is paid to
            # hold, so a negative figure here is a real credit and not a sign error. It is
            # deliberately NOT folded into `total_fee_cost_r`, which `expectancy_at` rescales by
            # a taker ratio — carry does not scale with the fee rate, and mixing them would make
            # every future rate change silently wrong.
            "total_funding_cost_r": round(total_funding_cost_r, 8),
            "cost_model": {
                "taker_fee_bps": cost.taker_fee_bps,
                "maker_fee_bps": cost.maker_fee_bps,
                "slippage_bps": cost.slippage_bps,
                "stop_slippage_bps": cost.stop_slippage_bps,
                "funding_bps_per_interval": cost.funding_bps_per_interval,
                # Which quality of evidence the carry is: the venue's own settlements over this
                # window, or the modelled base rate because the series was missing.
                "funding_source": funding_source,
            },
        },
        "regime_breakdown": regime_breakdown,
        "distribution_reference": compute_distribution_reference(
            [row for frame in frames for row in frame.rows[:frame.split]]
        ),
        "walk_forward": walk_forward,
        "holdout": holdout,
        "robustness": robustness,
        # The score's whole meaning, recorded where it is used: the anti-overfit
        # robustness score (C8b), with raw expectancy kept alongside.
        "champion_score": robustness["robustness_score"],
        "score_basis": "robustness_score_v1",
        # PER SYMBOL, and the shallowest leg's — `candidate_ranking.evidence_depth_of` turns this into a
        # calendar span, and five symbols over 350 days is 350 days of market seen five times,
        # not 1,750 days of it. Summing would tier a pooled candidate as though it had been
        # shown history that does not exist.
        "bars_replayed": min(scored_bars),
        # How many legs are behind every figure above. Absent on evidence minted before pooling,
        # which is exactly what 1 means, so a reader needs no migration to interpret an old row.
        "symbols_replayed": len(frames),
        # WHICH legs, and what each earned on its own — the breakdown `symbols_replayed` could
        # only count. Added 2026-08-23 for §F9's blocked promotion: a pooled candidate carries
        # the whole cohort's `symbol_scope`, so promoting it needs every one of those contexts,
        # and narrowing it to the free ones had no evidence behind it while the block held a
        # five-market average and nothing else. This is not a new measurement — the scored loop
        # already replays one frame per symbol and these figures were being summed away.
        #
        # Empty when no frame carried a symbol (a caller predating `ReplayFrame.symbol`), which
        # is the same absent-means-unknown contract as the line above: a reader that finds no
        # breakdown learns the block cannot say, never that the legs agreed.
        "per_symbol": per_symbol,
    }
