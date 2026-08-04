"""C8 strategy factory — seeded generation, validation, backtest evidence, candidates.

Ports the source strategy factory's S2/S3 core (template library subset, seeded
parameter mutation, the pre-backtest validator) plus a replay backtest built from the
already-ported evaluator and settlement math — the source's own guarantee ("a strategy
behaves identically in backtest and live" because both share one evaluator and one
exit model) holds here by construction, since ``strategy.evaluate_spec`` and
``paper.settle_trade_plan`` are exactly what the live cycle runs.

Template library: the families whose features the C3 rows compute. ``funding_fade_*``
joined when the funding series landed, and the ``htf_*`` legs when every timeframe in
the ladder became collected (Thomas 2026-07-25) — the standing rule being that a
family is ported only once its inputs exist, since specs that can never match would
be noise pretending to be diversity.

``taker_*`` and ``session_*`` joined under that same rule once ``features`` began
computing their columns. They differ from every family before them in what they are made
of: the twenty families that preceded them all read transformations of one series, the
OHLCV history, so adding a twenty-first of that kind recombines information the pool
already holds. Order flow (who crossed the spread) and session (who is at the desk) are
not recoverable from price at all — which is the argument for adding them and, equally,
the reason each is minted as its own family first rather than grafted onto proven ones:
a new information source has to earn a verdict on its own evidence before fusion may
carry it anywhere.

Everything in this module is ALLOW-tier record creation: the factory produces
**candidates with evidence**, appended to the candidates store. It cannot touch the
active pool — installing a candidate is the operator promotion door
(``scripts/promote_strategy_candidates.py``, pre-R10 posture), and the R9
approval-request wiring for promotion is a separate increment (C8b) because widening
``_APPROVAL_REQUIRED_SCOPES`` carries its own explicit Thomas sign-off (the
CANDIDATE_ROLE_TRIAL precedent).

Determinism: generation is seeded (source rule — same seed, same batch); the factory
derives its seed from the candle window's content hash, so a scheduled run is
reproducible from its recorded inputs and no wall-clock randomness exists anywhere.
``champion_score`` is the C8b robustness score (anti-overfit: observations-per-
parameter dominant, regime breadth, in-window pass rate; see ``robustness.py`` for
what the unported inputs honestly score) — raw expectancy rides alongside in the
evidence, and ``score_basis`` names the meaning on every candidate.

C12: the replay backtest is costed. Every simulated trade's gross (intended-price) R
is decomposed into net R after fees + slippage via ``cost.apply_cost_model`` (the
source's S4b cost model, ported in R-space — see ``cost.py``). This was once the ONLY
costed path, matching the source's boundary; since 2026-07-30 the paper kernel charges the
same model in ``paper.build_outcome_record``, so a paper expectancy and the one below are
finally the same kind of number (``cost.py`` records why the boundary moved). ``champion_score`` and
``expectancy`` are computed over the costed (net) R, so a strategy that only looks
good gross now scores accordingly; ``robustness.cost_robustness`` is measured for
real instead of always zero.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field, replace
from itertools import combinations
from typing import Any, Callable, Mapping, Sequence

from runtime.read_only_kernel import integrity

from . import features, market_data
from ..errors import ToolBlocked
from .cost import (
    FUNDING_INTERVALS_PER_DAY,
    FUNDING_SOURCE_FALLBACK,
    FUNDING_SOURCE_PARTIAL,
    FUNDING_SOURCE_VENUE,
    MAX_ENTRY_COST_R,
    CostModel,
    apply_cost_model,
    round_trip_cost_r,
)
from .feedback import summarize_outcomes
from .features import build_feature_rows
from .paper import settle_trade_plan
from .pool import candidate_id, derive_candidate_id
from .robustness import MIN_HOLDOUT_TRADES, score_robustness
from .strategy import SCHEMA_VERSION, SpecParseError, StrategySpec, evaluate_spec

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
# market periods instead of on trades. Five, and the number is bounded on both sides:
#
# - **Below**, by the test being possible at all. A one-sided t at 95% needs a spread, so four
#   periods is the floor `robustness.MIN_HOLDOUT_PERIODS` enforces; five leaves one period of
#   headroom for a slice that closes nothing.
# - **Above**, by what the window can supply. The tail is `HOLDOUT_FRACTION` of the replay
#   span — 150 days at every routed timeframe today — so five slices are 30 days each, and the
#   block-to-block measurement that motivated this used 50-day blocks. Cutting finer does not
#   buy independence; it buys correlated slices that LOOK like more evidence, which is the
#   error this whole change exists to stop making.
#
# The honest consequence, stated here rather than discovered later: at today's 500-day window
# this is not enough periods to confirm anything. Simulated over the store 2026-08-04, a
# period-based interval returns **0 CONFIRMED of 421** judgeable blocks, against 1 under the
# trade-based test — and that one is PROVISIONAL, so `promotable_backlog` was already 0 and
# does not move. The door does not get stricter in effect; it gets honest about having been
# shut. What reopens it is a deeper window, not a smaller number here.
HOLDOUT_PERIODS = 5

DEFAULT_BATCH_SIZE = 4
_MUTATION_SCALE = 0.35
_MAX_ATTEMPTS_PER_SPEC = 12

# Validator bounds (source S3, verbatim). Outside = rejected, never clamped.
STOP_ATR_RANGE = (0.3, 5.0)
TARGET_ATR_RANGE = (0.5, 10.0)
MAX_HOLDING_BARS_RANGE = (1, 500)
MAX_RISK_PER_TRADE_R = 2.0
MIN_REWARD_RISK = 1.0
MAX_ENTRY_CONDITIONS = 8

# What a FUSED child may carry, which is a different question from the line above and has to be
# a different number.
#
# `MAX_ENTRY_CONDITIONS` is source S3's, verbatim, and it answers "is this rule legal". It says
# nothing about whether the child can produce evidence anyone can judge, and measured
# 2026-08-04 that is where the band at its own boundary lands: of the mints carrying exactly 8
# entry conditions, **0 of 22 on the current cost basis and 0 of 25 across the whole store**
# closed enough holdout trades to reach `robustness.MIN_HOLDOUT_TRADES`. Not a low yield — none.
#
# The mechanism is fusion's own: entry conditions are the deduplicated union under AND, so a
# child fires only where BOTH parents would. Measured over all 394 parent-child pairs in the
# store (every one resolvable, so family/symbol/timeframe/direction are controlled by
# construction rather than by matching), a child closes **0.51x** its parent median's trades —
# fewer than both parents in 66% of pairs — and where both parents were judgeable, 28% of pairs
# produce a child that is not. The share with a judgeable holdout falls monotonically with the
# count: 4 conditions 81%, 5 64%, 6 30%, 7 16%, 8 **0%**.
#
# **Only the zero band is cut, and that is the whole of the decision taken here.** Refusing at
# 7 or 6 would reject 50-56% of all fusions, which trades exploration for judgeability and is a
# claim about the search space this measurement does not settle — see `REMAINING_WORK.md`
# section F1, which carries the wider version and deliberately does not take it. This one
# removes a band with no measured yield at all, and it is cheap to be wrong about: `mint_fusions`
# draws from `combinations(bucket, 2)` until `pairs` children carry evidence, so a refusal
# redirects the draw instead of costing a mint.
#
# **What reopens it:** a mint at 8 conditions that reaches `MIN_HOLDOUT_TRADES`. That cannot come
# from this timeframe ladder at these signal rates without the replay window growing, so the
# thing to re-measure first is `market_data.factory_candle_target`, not this number.
MAX_FUSION_ENTRY_CONDITIONS = 7

# The features a generated spec may reference — exactly what build_feature_rows
# computes. Membership IS the look-ahead guard (the schema has no forward-shift
# operator and every row column is point-in-time).
NUMERIC_FEATURES = frozenset({
    "open", "high", "low", "close", "volume",
    "ma20", "ma50", "ema20", "ema50", "atr", "atr_pct_of_price", "atr_percentile",
    "rsi", "adx", "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_lower", "bb_width_pct", "bb_percent_b", "bb_width_percentile",
    "roc_4", "price_distance_ma20", "volume_zscore",
    "mark_price", "index_price", "mark_index_basis_bps",
    # C9: the funding series rides the default binance_futures grant, so generated
    # specs may reference it. This set gates what the factory may MINT, not what the
    # evaluator may read — imported specs evaluate anything.
    "funding_rate", "funding_zscore",
    # The liquidation columns were held out while the Coinalyze feed was unconfigured.
    # Admitted by explicit Thomas decision 2026-07-24, now that the feed is live, and
    # safe on their own terms: with no feed these three fill with **None**, so the
    # fail-closed evaluator treats them as indeterminate and a spec naming them simply
    # never matches. No feed, no trade — the honest outcome.
    #
    # ``liquidation_spike_ratio`` is the exception and always was: with no feed it
    # falls back to a **constant 0.0** ("legacy constant, pre-C9" in features.py), so
    # a minted ``spike_ratio < x`` condition matches on a fabricated value rather than
    # on absent data. That hazard predates this change and is not widened by it — the
    # three added here are strictly safer than the one already present.
    "liquidation_spike_ratio", "liquidation_total", "long_liquidation", "short_liquidation",
    # Higher-timeframe context (Thomas 2026-07-25): the read of the last CLOSED candle
    # one step up the traded ladder. Safe on the liquidation terms, not the spike-ratio
    # terms — with no HTF supplied every column is **None**, so an htf_* condition is
    # indeterminate and simply never matches. The numeric ones are normalized ratios,
    # so a mined threshold carries the same meaning on every symbol.
    *features.HTF_NUMERIC_COLUMNS,
    # Open interest (Thomas 2026-07-25) — the positioning leg beside funding and
    # liquidations. Only the NORMALIZED derivatives are mintable: raw open_interest is
    # a venue-scale quantity, so a mined threshold on it would mean something different
    # on every symbol and nothing at all after the venue grows. Absent feed = None =
    # never matches, the liquidation posture.
    "open_interest_change_pct", "open_interest_zscore",
    # Taker order flow — who CROSSED the spread, from the kline legs the collector was
    # already downloading and discarding. The first information source admitted here that
    # is not a transformation of the price series: two bars with identical OHLC differ
    # completely depending on which side was the aggressor.
    #
    # The same normalized-only rule as open interest, and for the same reason. Raw
    # `quote_volume`, `trade_count`, `taker_buy_base`, `taker_buy_quote` and
    # `avg_trade_size` are on the row as evidence but are deliberately NOT here: every one
    # of them is a venue-scale quantity. The five below are ratios or z-scores, so a mined
    # threshold carries the same meaning on BTC and on SOL.
    #
    # Absent legs (a pre-flow snapshot, or a venue that changes its payload) leave all of
    # them None, so a flow spec is indeterminate and simply stops trading — the open
    # interest posture, never the spike-ratio one.
    "taker_buy_ratio", "taker_flow_imbalance", "taker_flow_zscore", "taker_flow_ma",
    "avg_trade_size_zscore", "trade_count_zscore",
    # The premium index — the basis funding is computed from, at BAR resolution rather than
    # the 8h event cadence `funding_rate` carries. Both normalized (a fraction of the index
    # price, and its z-score), so a mined threshold means the same thing on every symbol.
    #
    # `mark_price`, `index_price` and `mark_index_basis_bps` were already in this set and
    # stay; what changed is that they now carry measured values instead of close/close/0.0,
    # so a literal condition on the basis finally selects on something.
    "premium_index", "premium_index_zscore",
    # Cross-asset context (see features.REFERENCE_NUMERIC_COLUMNS). The first columns in this
    # vocabulary that describe a relationship BETWEEN symbols rather than one symbol's own
    # history — which is what makes them the only available handle on the shared market beta
    # every pool strategy currently carries. All normalized: two rates of change, their
    # difference, and a correlation.
    *features.REFERENCE_NUMERIC_COLUMNS,
    # Cross-sectional context (see features.XS_NUMERIC_COLUMNS). The reference columns above
    # describe a relationship between this symbol and ONE proxy; these describe its place among
    # a whole cohort, which is a different statement and the one a cross-sectional strategy
    # needs — every altcoin can beat BTC in the same hour, and that says nothing about which of
    # them to buy or which to sell.
    #
    # All three normalized, and one of them self-calibrating: a rank fraction in [0, 1], an
    # excess rate of change, and a dispersion measured as a ratio to its own recent normal
    # rather than as a level. `xs_dispersion` and `xs_members` are on the row as evidence and
    # deliberately NOT here — a dispersion LEVEL means something different at 1h than at 4h,
    # and `xs_members` is a count of how many peers answered, so a condition on it would mine
    # feed availability rather than the market. The raw-`open_interest` rule.
    *features.XS_NUMERIC_COLUMNS,
    # Positioning (see features.POSITIONING_NUMERIC_COLUMNS). The first columns in this vocabulary
    # describing what is HELD rather than what traded — every other source here, including the
    # taker flow, is a property of transactions. Large capital's long share against the whole
    # account population cannot be recovered from OHLCV at any resolution.
    #
    # Only the standardised pair is here. `positioning_divergence` and the three raw shares are on
    # the row as evidence and deliberately NOT mintable: a level that is dimensionless is not the
    # same as a level that is comparable, and whether +0.05 of divergence means the same on BTC as
    # on DOGE is a question nobody has answered. The `xs_dispersion` split.
    #
    # Admitting them here does not make them reachable — `POSITIONING_FAMILIES` stay unminted
    # until the store's coverage says so. Membership gates what a spec MAY reference; the family
    # gate decides what gets built.
    *features.POSITIONING_NUMERIC_COLUMNS,
})
_REGIME_VALUES = frozenset({"TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOLATILITY",
                            "LOW_VOLATILITY", "UNCLEAR"})
CATEGORICAL_FEATURES: dict[str, frozenset[str]] = {
    "market_regime": _REGIME_VALUES,
    # Same classifier, one timeframe up — so the same closed vocabulary.
    "htf_market_regime": _REGIME_VALUES,
    # Session context. CATEGORICAL rather than a numeric hour on purpose: `hour_of_day`
    # would admit `== 3`, which is a free pick of one bucket in twenty-four that the
    # robustness scorer counts as a single literal — the cheapest possible way to mine
    # noise. Three labels with only ==/!= available bounds that to a choice among three.
    "session": features.SESSION_VALUES,
    "day_type": features.DAY_TYPE_VALUES,
    # The market proxy's regime, from the same classifier — so the same closed vocabulary.
    "ref_market_regime": _REGIME_VALUES,
}
_NUMERIC_COMPARISONS = frozenset({">", ">=", "<", "<=", "==", "!="})
_CATEGORICAL_COMPARISONS = frozenset({"==", "!="})


@dataclass(frozen=True)
class ParamSpec:
    """A tunable parameter and the closed interval it may take."""

    lo: float
    hi: float
    integer: bool = False


@dataclass(frozen=True)
class StrategyTemplate:
    family: str
    direction: str  # "long" | "short"
    timeframe: str
    param_space: dict[str, ParamSpec]
    base_params: dict[str, float]
    entry_builder: Callable[[dict], list[dict]] = field(repr=False)


# `stop_atr`'s floor is 1.2 and not 0.8, measured on this store 2026-08-02 over the 240
# candidates carrying the current cost basis. 1R is `stop_atr` x ATR and costs are a fixed
# ~10-16 bps round trip, so the multiple decides what fraction of the risk unit friction eats:
#
#   stop_atr    15m net/trade    1h net/trade    4h net/trade
#   0.8-1.2       -0.3746R         -0.0070R        +0.0939R
#   1.2-1.6       +0.0054R         +0.0228R        +0.1026R
#
# **The aggregate overstates it and the honest number is smaller.** Within each family at 15m
# the gap is ~0.05-0.15R rather than 0.38R — the tight bucket happened to hold more of the
# losing families — and it runs the right way in 7 of 10 families on cells of n=1-7. So this is
# a real second-order improvement, not the fix for the fast timeframes: at 15m friction is
# 0.28R against a gross edge of 0.09R, and no stop multiple closes a 3x gap. What it does do is
# stop spending half of every mint on a band that is negative at 15m, marginal at 1h and worse
# at 4h than the alternative, for free.
#
# The BASE moves with the floor, and that is not cosmetic. `mutate_params` draws
# `base +/- (hi - lo) * 0.35` and clamps, so raising `lo` to 1.2 while leaving the base at 1.2
# would pin **49.8%** of draws to exactly 1.2 — one value, one rule hash, a collapse in the
# parameter's diversity rather than a shift in it. At 1.45 that is 5.4%, ~77% of draws land in
# the band measured above, and the tail still reaches 1.73 so the region this store has never
# sampled (1.6-2.0, n=2) stays explorable.
#
# `target_atr` carries the same trap less severely and it was never measured until 2026-08-04:
# the base 3.0 clears the 1.6 floor by 1.4 against a span of +/-2.24, so **18.9%** of draws pin
# at exactly 1.6. Under the floor tolerated for the stop (10%) that is a fail, and it is why
# "the low-target region" is mostly a spike ON the bound rather than a region — of the accepted
# draws below target 2.0, roughly four in five are the pinned value. `_apply_reward_risk_floor`
# takes it to 14.4% as a side effect, which is an improvement and not a fix; moving this base up
# would be the fix, and it is a change to where the trend search aims, so it is not made here.
_EXIT_PARAMS = {
    "stop_atr": ParamSpec(1.2, 2.0),
    "target_atr": ParamSpec(1.6, 8.0),
    "max_holding_bars": ParamSpec(12, 48, integer=True),
}
_EXIT_BASE = {"stop_atr": 1.45, "target_atr": 3.0, "max_holding_bars": 24}

# The exit geometry above is a TREND geometry, and until now every family shared it. A target
# reaching 8x ATR over a 48-bar hold is "ride it while it runs" — which is the right shape for
# a breakout and the wrong one for a fade, whose thesis is that price returns to a mean and is
# COMPLETE when it gets there. The search could draw a mean-reversion spec entering at RSI 30
# with an 8x target and a 48-bar hold, and such a spec's entry and exit argue with each other:
# the entry says "this is stretched", the exit says "hold for a trend".
#
# Measured 2026-08-04 on the holdout at the current cost basis, by mechanism class rather than
# by family (fused rows excluded, lineage-collapsed): TREND is +0.0071R GROSS *negative* over
# 32,847 out-of-sample trades (t = -2.54), while FADE reads +0.0707R over 966 and PULLBACK
# +0.0726R over 2,017. **The fade figure is not a finding and this change does not rest on it**
# — an adversarial pass returned WEAKENED (the gap halves under an unweighted median, flips sign
# on 2 of 5 symbols, and 58% of it is a cost add-back artifact of fade's tighter stops). What the
# measurement does establish is the asymmetry: the well-sampled class is the one confidently at
# zero, and the class that might not be has never been given a geometry matching its premise.
#
# Three bounds, each doing one thing:
#
# - ``stop_atr`` floors HIGHER than the trend space (1.4 vs 1.2). A fade enters at an extreme,
#   which is exactly where the next bar's noise is largest, and cost-in-R is `fee_bps/stop_bps`
#   so a wider 1R is directly cheaper. Nothing here is free: a wider stop is a bigger loss when
#   the mean does not come back.
# - ``target_atr`` ceilings at 3.4 instead of 8.0. The premise completes at the mean.
# - ``max_holding_bars`` runs 4-16 against the trend's 12-48 — a bounce that has not happened
#   in sixteen bars was not a bounce, and holding it for forty-eight is paying carry to find out.
#
# **The lower target bound is 2.0 because it must equal the upper stop bound, not because 2.0
# was chosen.** ``validate_strategy`` enforces ``target_atr / stop_atr >= MIN_REWARD_RISK`` (1.0),
# so a space with ``target.lo < stop.hi`` contains pairs the validator refuses; at ``target.lo ==
# stop.hi`` the property is arithmetic rather than lucky. Since 2026-08-04 `mutate_params` also
# enforces the ratio at draw time (`_apply_reward_risk_floor`), so such a space no longer mints
# refusals — but a space that never reaches the repair is still the stronger construction, and
# the repair costs an rng draw and bends the target's marginal. Build them this way; the floor is
# the net for spaces that cannot be. This is also the binding reason the classic fade geometry
# (RR below 1 carried by a high hit rate) cannot be expressed here at all; widening
# MIN_REWARD_RISK is a separate, explicit decision.
#
# Bases sit mid-range for the reason recorded above the trend base: a base ON a bound pins ~50%
# of draws to that bound. At 1.7/2.7/10 no draw clamps at all (spans are +/-0.21, +/-0.49, +/-4.2),
# so the whole interval stays reachable and the worst-case drawn R:R is 2.21/1.91 = 1.16.
_FADE_EXIT_PARAMS = {
    "stop_atr": ParamSpec(1.4, 2.0),
    "target_atr": ParamSpec(2.0, 3.4),
    "max_holding_bars": ParamSpec(4, 16, integer=True),
}
_FADE_EXIT_BASE = {"stop_atr": 1.7, "target_atr": 2.7, "max_holding_bars": 10}

# Every generation space the factory mints from. `_fused_exit_param` clamps against the UNION
# of these rather than against `_EXIT_PARAMS` alone — see there for why the union is the honest
# bound once more than one space exists.
_GENERATION_SPACES = (_EXIT_PARAMS, _FADE_EXIT_PARAMS)


def _trend_pullback_entry(p: dict) -> list[dict]:
    return [
        {"feature": "ma20", "comparison": ">", "value_from": "ma50"},
        {"feature": "adx", "comparison": ">=", "value": p["adx_min"]},
        {"feature": "rsi", "comparison": "<=", "value": p["rsi_max"]},
    ]


def _trend_pullback_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "ma20", "comparison": "<", "value_from": "ma50"},
        {"feature": "adx", "comparison": ">=", "value": p["adx_min"]},
        {"feature": "rsi", "comparison": ">=", "value": p["rsi_min"]},
    ]


# --- crossovers: the shape the vocabulary could not express until #456 ------------------------
#
# Every family above tests a STATE. `ma20 > ma50` is true for the whole of a trend, so the spec
# is eligible on every bar of it and the entry that actually happens is decided by whichever bar
# the other conditions happen to clear on — not by the trend starting. These ask for the bar the
# relationship CHANGED, which is a different hypothesis and one the search has never been able
# to see.
#
# Two conditions, and the second one is the whole difference: the relation holds now, and did
# NOT hold one bar ago. `lag` shifts the whole condition, so the existing AND composes them.
#
# The filter conditions are deliberately kept from the state families they mirror. The point of
# the pair is to isolate ONE change — state versus event — so that if the crossover version
# scores differently, the difference is attributable. Adding new filters at the same time would
# make the comparison say nothing.


def _ma_cross_up_entry(p: dict) -> list[dict]:
    return [
        {"feature": "ma20", "comparison": ">", "value_from": "ma50"},
        {"feature": "ma20", "comparison": "<=", "value_from": "ma50", "lag": 1},
        {"feature": "adx", "comparison": ">=", "value": p["adx_min"]},
    ]


def _ma_cross_down_entry(p: dict) -> list[dict]:
    return [
        {"feature": "ma20", "comparison": "<", "value_from": "ma50"},
        {"feature": "ma20", "comparison": ">=", "value_from": "ma50", "lag": 1},
        {"feature": "adx", "comparison": ">=", "value": p["adx_min"]},
    ]


def _macd_cross_up_entry(p: dict) -> list[dict]:
    return [
        {"feature": "macd", "comparison": ">", "value_from": "macd_signal"},
        {"feature": "macd", "comparison": "<=", "value_from": "macd_signal", "lag": 1},
        {"feature": "adx", "comparison": ">=", "value": p["adx_min"]},
    ]


def _macd_cross_down_entry(p: dict) -> list[dict]:
    return [
        {"feature": "macd", "comparison": "<", "value_from": "macd_signal"},
        {"feature": "macd", "comparison": ">=", "value_from": "macd_signal", "lag": 1},
        {"feature": "adx", "comparison": ">=", "value": p["adx_min"]},
    ]


def _breakout_entry(p: dict) -> list[dict]:
    return [
        {"feature": "close", "comparison": ">", "value_from": "ma20"},
        {"feature": "ma20", "comparison": ">", "value_from": "ma50"},
        {"feature": "adx", "comparison": ">=", "value": p["adx_min"]},
    ]


def _breakdown_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "close", "comparison": "<", "value_from": "ma20"},
        {"feature": "ma20", "comparison": "<", "value_from": "ma50"},
        {"feature": "adx", "comparison": ">=", "value": p["adx_min"]},
    ]


def _htf_trend_long_entry(p: dict) -> list[dict]:
    # NOT MINTED — see RETIRED_FAMILIES. Superseded by `htf_trend_strength_long` below, which
    # asks the same question of a continuous column instead of a label that cannot answer it
    # on the bars that matter most.
    return [
        {"feature": "htf_market_regime", "comparison": "==", "value": "TREND_UP"},
        {"feature": "close", "comparison": ">", "value_from": "ma20"},
        {"feature": "adx", "comparison": ">=", "value": p["adx_min"]},
    ]


def _htf_trend_short_entry(p: dict) -> list[dict]:
    # NOT MINTED — see RETIRED_FAMILIES.
    return [
        {"feature": "htf_market_regime", "comparison": "==", "value": "TREND_DOWN"},
        {"feature": "close", "comparison": "<", "value_from": "ma20"},
        {"feature": "adx", "comparison": ">=", "value": p["adx_min"]},
    ]


def _htf_trend_strength_long_entry(p: dict) -> list[dict]:
    # The same premise as `htf_trend_long` — only go long while the timeframe ABOVE is
    # trending up — asked of the separation itself rather than of the label built from it.
    #
    # **What the label could not say.** `classify_market_regime` tests volatility FIRST and
    # returns `HIGH_VOLATILITY` before it ever reaches its trend branch, so
    # `htf_market_regime == "TREND_UP"` is false on every higher-timeframe bar in the top
    # quintile of its own ATR range however cleanly it is trending. Those are the bars where
    # 1R (`stop_atr` x ATR) is largest against a fixed-bps round trip — the premise
    # `volatility_expansion_*` exists on — so the retired pair was excluded from precisely the
    # bars whose friction it could best afford.
    #
    # `htf_ma20_distance_ma50` is `(ma20 - ma50) / ma50`, so ONE condition carries both the
    # direction the label carried and the strength it discarded (a 0.1% separation and an 8%
    # one were the same label), and the ADX floor stops being `ADX_TREND_THRESHOLD = 20.0`
    # hard-coded inside the classifier — where 19.9 and 20.1 switched the family off and on —
    # and becomes a bound the search can move.
    #
    # Free parameters are unchanged at 5: `count_free_parameters` charges literals and not
    # `value_from`, and this trades one literal for another. Measured 2026-08-04 (see
    # `docs/REMAINING_WORK.md`, section F): at 4h the share of draws reaching
    # `MIN_HOLDOUT_TRADES` goes 20% -> 48% long and 17% -> 77% short. It buys no edge — 0 of
    # 960 specs cleared the selection-adjusted bar, and that is the point of the retirement
    # note rather than of this one.
    return [
        {"feature": "htf_ma20_distance_ma50", "comparison": ">=", "value": p["htf_sep_min"]},
        {"feature": "close", "comparison": ">", "value_from": "ma20"},
        {"feature": "adx", "comparison": ">=", "value": p["adx_min"]},
    ]


def _htf_trend_strength_short_entry(p: dict) -> list[dict]:
    # The mirror, and the sign is negated on the VALUE rather than expressed by flipping the
    # comparison against a positive bound: `htf_sep_min` means "this far apart" in both
    # directions, so one parameter range describes both families and a draw that is a weak
    # long signal is an equally weak short one.
    return [
        {"feature": "htf_ma20_distance_ma50", "comparison": "<=", "value": -p["htf_sep_min"]},
        {"feature": "close", "comparison": "<", "value_from": "ma20"},
        {"feature": "adx", "comparison": ">=", "value": p["adx_min"]},
    ]


def _htf_pullback_long_entry(p: dict) -> list[dict]:
    # The reason the families exist: buy weakness only while the timeframe ABOVE is
    # still trending up. Same dip, opposite meaning, depending on the higher regime.
    #
    # No separate htf_adx floor: ``classify_market_regime`` only returns TREND_UP when
    # adx is already at or above its trend threshold, so the two conditions overlapped —
    # the extra one bought little selectivity and cost a free parameter, which the
    # robustness score divides its trade count by.
    return [
        {"feature": "htf_market_regime", "comparison": "==", "value": "TREND_UP"},
        {"feature": "rsi", "comparison": "<=", "value": p["rsi_max"]},
    ]


def _htf_pullback_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "htf_market_regime", "comparison": "==", "value": "TREND_DOWN"},
        {"feature": "rsi", "comparison": ">=", "value": p["rsi_min"]},
    ]


def _oi_squeeze_long_entry(p: dict) -> list[dict]:
    # Position building ahead of a move: open interest climbing while the market has not
    # yet confirmed a trend in this direction is crowding, and the release tends to
    # travel. The regime gate is what makes it a squeeze rather than plain trend-following.
    #
    # It asks "not yet trending up" rather than "== RANGE" because RANGE was an
    # arbitrarily narrow spelling of that premise: the classifier also emits
    # LOW_VOLATILITY, HIGH_VOLATILITY and UNCLEAR, none of which is a confirmed up-trend
    # either. Measured on live frames, requiring exactly RANGE fired the full condition on
    # 0.43% of ETHUSDT 1h bars (10-15 trades over a 500-day replay) — below the sample the
    # robustness scorer needs to judge anything, so the family was structurally unable to
    # earn a verdict, whatever its edge. The same premise as `!= TREND_UP` fires on 4.88%.
    return [
        {"feature": "open_interest_change_pct", "comparison": ">=", "value": p["oi_change_min"]},
        {"feature": "open_interest_zscore", "comparison": ">=", "value": p["oi_z_min"]},
        {"feature": "market_regime", "comparison": "!=", "value": "TREND_UP"},
        {"feature": "close", "comparison": ">", "value_from": "ma20"},
    ]


def _oi_squeeze_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "open_interest_change_pct", "comparison": ">=", "value": p["oi_change_min"]},
        {"feature": "open_interest_zscore", "comparison": ">=", "value": p["oi_z_min"]},
        {"feature": "market_regime", "comparison": "!=", "value": "TREND_DOWN"},
        {"feature": "close", "comparison": "<", "value_from": "ma20"},
    ]


def _oi_unwind_long_entry(p: dict) -> list[dict]:
    # The mirror: open interest FALLING hard while price is washed out is capitulation
    # finishing — positions are leaving, not arriving.
    return [
        {"feature": "open_interest_change_pct", "comparison": "<=", "value": -p["oi_change_min"]},
        {"feature": "rsi", "comparison": "<=", "value": p["rsi_max"]},
    ]


def _oi_unwind_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "open_interest_change_pct", "comparison": "<=", "value": -p["oi_change_min"]},
        {"feature": "rsi", "comparison": ">=", "value": p["rsi_min"]},
    ]


def _mean_reversion_long_entry(p: dict) -> list[dict]:
    return [
        {"feature": "rsi", "comparison": "<=", "value": p["rsi_max"]},
        {"feature": "market_regime", "comparison": "==", "value": "RANGE"},
    ]


def _mean_reversion_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "rsi", "comparison": ">=", "value": p["rsi_min"]},
        {"feature": "market_regime", "comparison": "==", "value": "RANGE"},
    ]


def _macd_momentum_entry(p: dict) -> list[dict]:
    return [
        {"feature": "macd_hist", "comparison": ">", "value": 0.0},
        {"feature": "macd", "comparison": ">", "value_from": "macd_signal"},
        {"feature": "adx", "comparison": ">=", "value": p["adx_min"]},
    ]


def _macd_momentum_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "macd_hist", "comparison": "<", "value": 0.0},
        {"feature": "macd", "comparison": "<", "value_from": "macd_signal"},
        {"feature": "adx", "comparison": ">=", "value": p["adx_min"]},
    ]


def _bollinger_breakout_entry(p: dict) -> list[dict]:
    return [
        {"feature": "bb_percent_b", "comparison": ">=", "value": p["percent_b_min"]},
        {"feature": "volume_zscore", "comparison": ">=", "value": p["volume_z_min"]},
        {"feature": "ma20", "comparison": ">", "value_from": "ma50"},
    ]


def _bollinger_breakdown_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "bb_percent_b", "comparison": "<=", "value": p["percent_b_max"]},
        {"feature": "volume_zscore", "comparison": ">=", "value": p["volume_z_min"]},
        {"feature": "ma20", "comparison": "<", "value_from": "ma50"},
    ]


def _rel_strength_long_entry(p: dict) -> list[dict]:
    # Cross-sectional momentum in the single-symbol form this router can express: buy what is
    # outperforming the benchmark, while it is also in its own uptrend. The excess is what
    # matters — a symbol up 2% on a day the benchmark is up 3% is not strong, and no column
    # before `rel_strength_roc_4` could say so.
    return [
        {"feature": "rel_strength_roc_4", "comparison": ">=", "value": p["rel_min"]},
        {"feature": "close", "comparison": ">", "value_from": "ma20"},
    ]


def _rel_strength_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "rel_strength_roc_4", "comparison": "<=", "value": -p["rel_min"]},
        {"feature": "close", "comparison": "<", "value_from": "ma20"},
    ]


def _xs_momentum_long_entry(p: dict) -> list[dict]:
    # Cross-sectional momentum: buy the cohort's strongest, and only while being strongest is
    # worth something. The second condition is the part `rel_strength_long` cannot express —
    # when every member moves together, the leader leads by nothing and the rank is noise; the
    # dispersion ratio says whether there is a spread to rank across at all.
    #
    # `xs_rank_edge` is one parameter used by BOTH directions rather than an independent floor
    # and ceiling, and that is a safety property, not a tidiness one: the long leg requires
    # rank >= 1 - edge and the short leg rank <= edge, so with edge capped at 0.4 the two
    # conditions are DISJOINT by construction. Independent params could be mined into an
    # overlap (long >= 0.4 while short <= 0.6), and a long and a short matching on the same
    # feature row is `paper.BLOCK_DIRECTION_CONFLICT` — the whole pair failing closed on
    # exactly the bars the families were minted for.
    return [
        {"feature": "xs_rank_pct", "comparison": ">=", "value": 1.0 - p["xs_rank_edge"]},
        {"feature": "xs_dispersion_ratio", "comparison": ">=", "value": p["xs_dispersion_min"]},
    ]


def _positioning_divergence_long_entry(p: dict) -> list[dict]:
    # The research record's own thesis, and the only premise in this library that reads what is
    # HELD: the top cohort's positions have swung long relative to the whole account population by
    # an unusual amount. Confirmed by trend, like `rel_strength_long`, because a divergence says
    # who is positioned and not when — and a positioning reading is hourly, so it moves far more
    # slowly than the bar being entered on.
    return [
        {"feature": "positioning_divergence_zscore", "comparison": ">=", "value": p["divergence_z_min"]},
        {"feature": "close", "comparison": ">", "value_from": "ma20"},
    ]


def _positioning_divergence_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "positioning_divergence_zscore", "comparison": "<=", "value": -p["divergence_z_min"]},
        {"feature": "close", "comparison": "<", "value_from": "ma20"},
    ]


def _xs_momentum_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "xs_rank_pct", "comparison": "<=", "value": p["xs_rank_edge"]},
        {"feature": "xs_dispersion_ratio", "comparison": ">=", "value": p["xs_dispersion_min"]},
    ]


# The same two columns, read with the opposite sign: buy the cohort's laggards, sell its
# leaders. Cross-sectional REVERSION, the hypothesis the momentum pair could not express.
#
# **This is a swap and not an addition, and the reason is structural rather than budgetary.**
# `xs_momentum_short` fires on `xs_rank_pct <= edge` and `xs_reversion_long` fires on the same
# condition — so with both in the library one bar can match a LONG and a SHORT on the same
# feature row, which `paper.route_entries` refuses as `BLOCK_DIRECTION_CONFLICT`. The pair would
# fail closed on exactly the bars both families were minted for. Momentum and reversion over one
# ranking are mutually exclusive claims about the same number, and the library may hold one.
#
# Which one, measured 2026-08-04 on the holdout at the current cost basis: `xs_momentum_long`
# reads -0.0177R GROSS over 3,040 out-of-sample trades and `xs_momentum_short` -0.0048R over
# 2,626. That is 5,666 trades saying the momentum reading of this column is not positive before
# costs; it is not evidence that the reversion reading is, and nothing here claims otherwise.
# The claim is narrower: one of the two signs gets the slot, the measured one has had 5,666
# trades to show something and has not, and the other has never been minted.
#
# Exit parameters are deliberately UNCHANGED from the momentum pair (`_EXIT_PARAMS`, not
# `_FADE_EXIT_PARAMS`, despite the reversion premise). The point of a swap is to isolate ONE
# change so a difference in score is attributable to it — the same discipline the crossover
# families keep against their state siblings. Handing this pair a new geometry at the same time
# would make the comparison say nothing.
#
# Disjointness is preserved by construction, exactly as in the momentum pair: the long leg takes
# `rank <= edge` and the short leg `rank >= 1 - edge`, so with `xs_rank_edge` capped at 0.4 the
# two can never match the same row.
def _xs_reversion_long_entry(p: dict) -> list[dict]:
    return [
        {"feature": "xs_rank_pct", "comparison": "<=", "value": p["xs_rank_edge"]},
        {"feature": "xs_dispersion_ratio", "comparison": ">=", "value": p["xs_dispersion_min"]},
    ]


def _xs_reversion_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "xs_rank_pct", "comparison": ">=", "value": 1.0 - p["xs_rank_edge"]},
        {"feature": "xs_dispersion_ratio", "comparison": ">=", "value": p["xs_dispersion_min"]},
    ]


def _premium_fade_short_entry(p: dict) -> list[dict]:
    # The funding_fade premise, timed properly. `funding_fade_short` reads `funding_zscore`,
    # which is an 8h event carried forward, so on a 1h frame it decides an entry on a value
    # that last moved up to eight hours ago. `premium_index_zscore` is the same crowding
    # pressure measured on the bar being traded.
    #
    # Both families are kept rather than one replacing the other: they encode the same
    # premise at different resolutions, and which resolution actually pays is the question
    # the evidence should answer, not something to settle by assertion.
    return [
        {"feature": "premium_index_zscore", "comparison": ">=", "value": p["premium_z_min"]},
        {"feature": "rsi", "comparison": ">=", "value": p["rsi_min"]},
    ]


def _premium_fade_long_entry(p: dict) -> list[dict]:
    return [
        {"feature": "premium_index_zscore", "comparison": "<=", "value": -p["premium_z_min"]},
        {"feature": "rsi", "comparison": "<=", "value": p["rsi_max"]},
    ]


def _taker_flow_long_entry(p: dict) -> list[dict]:
    # Flow-confirmed trend: price above its mean AND the aggressor side has been the buy
    # side for a sustained stretch. The rolling mean rather than the single bar's print is
    # the point — one bar's imbalance is mostly that bar's own move restated, while the
    # mean is the part that persisted across bars.
    return [
        {"feature": "taker_flow_ma", "comparison": ">=", "value": p["flow_ma_min"]},
        {"feature": "close", "comparison": ">", "value_from": "ma20"},
    ]


def _taker_flow_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "taker_flow_ma", "comparison": "<=", "value": -p["flow_ma_min"]},
        {"feature": "close", "comparison": "<", "value_from": "ma20"},
    ]


def _taker_absorption_long_entry(p: dict) -> list[dict]:
    # The family that justifies the whole feature: price and flow DISAGREE. RSI says the
    # move is washed out, while aggressive buying is unusually heavy against its own recent
    # norm — someone is absorbing the supply that is being sold into them.
    #
    # This is the shape no price transformation can express. A washed-out RSI is in the row
    # already; what is new is being able to ask what the tape was doing while it got there.
    return [
        {"feature": "taker_flow_zscore", "comparison": ">=", "value": p["flow_z_min"]},
        {"feature": "rsi", "comparison": "<=", "value": p["rsi_max"]},
    ]


def _taker_absorption_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "taker_flow_zscore", "comparison": "<=", "value": -p["flow_z_min"]},
        {"feature": "rsi", "comparison": ">=", "value": p["rsi_min"]},
    ]


def _session_label(p: dict) -> str:
    """The session this spec trades, chosen by the seeded mutation rather than by hand.

    A hand-written ``session == "US"`` template would be the author picking one bucket in
    three and the robustness scorer never learning that a choice was made. Routing the
    choice through a mutated parameter makes it a literal on the emitted condition, which
    is exactly what ``count_free_parameters`` charges for."""
    index = int(p["session_index"]) % len(features.SESSION_BOUNDS)
    return features.SESSION_BOUNDS[index][0]


def _session_trend_long_entry(p: dict) -> list[dict]:
    return [
        {"feature": "session", "comparison": "==", "value": _session_label(p)},
        {"feature": "close", "comparison": ">", "value_from": "ma20"},
        {"feature": "adx", "comparison": ">=", "value": p["adx_min"]},
    ]


def _session_trend_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "session", "comparison": "==", "value": _session_label(p)},
        {"feature": "close", "comparison": "<", "value_from": "ma20"},
        {"feature": "adx", "comparison": ">=", "value": p["adx_min"]},
    ]


def _funding_fade_short_entry(p: dict) -> list[dict]:
    # Crowded longs: funding far above its rolling norm while momentum is
    # stretched — fade the crowd short. (C9: the funding feed made this mintable.)
    return [
        {"feature": "funding_zscore", "comparison": ">=", "value": p["funding_z_min"]},
        {"feature": "rsi", "comparison": ">=", "value": p["rsi_min"]},
    ]


def _funding_fade_long_entry(p: dict) -> list[dict]:
    # Crowded shorts: funding far below its rolling norm while momentum is washed out.
    return [
        {"feature": "funding_zscore", "comparison": "<=", "value": p["funding_z_max"]},
        {"feature": "rsi", "comparison": "<=", "value": p["rsi_max"]},
    ]


# --- volatility regime --------------------------------------------------------
#
# The first families whose premise is HOW MUCH the market is moving rather than which way.
# Every other family here reads direction, flow, carry or crowding and is indifferent to the
# size of the move it is entering; these two pairs make that size the entry condition.
#
# Both read PERCENTILES, and that is the whole reason they are mintable at all. The raw
# volatility columns (`atr`, `atr_pct_of_price`, `bb_width_pct`) are on the row and are in
# `NUMERIC_FEATURES`, but a mined threshold on any of them is a LEVEL: ~0.2% of price at 15m
# and ~3% at 1d, so one `ParamSpec` retimed across the ladder would mean a different claim at
# every rung — the `xs_dispersion` split, in volatility's costume. `atr_percentile` and
# `bb_width_percentile` are rank fractions in [0, 1] against the symbol's own recent window, so
# "the top fifth of this symbol's own volatility" is the same claim on BTC at 15m as on DOGE
# at 1d, and no level had to be authorized.
#
# There is a second, narrower reason to mine the expansion side, measured on this runtime's own
# record 2026-07-31: costs are a FIXED ~10-16 bps round trip while 1R = `stop_atr` x ATR shrinks
# with the bar, so the median 1R runs 21.6 bps at 15m against 309.8 bps at 1d and the cost eats
# 46-74% of the risk unit at the fast end. An entry gated on high `atr_percentile` is the one
# handle in this vocabulary that widens 1R without touching `stop_atr` — it selects the bars
# where the same multiple of ATR is a bigger move. Stated as motivation, not as a claim: whether
# it survives is what `backtest_spec` is for, and nothing here assumes the answer.


def _volatility_expansion_long_entry(p: dict) -> list[dict]:
    # Trend, but only where this symbol's own volatility is in the upper part of its
    # recent range — the same trend rule the breakout family mines, restricted to the
    # bars where a move large enough to clear its costs is actually on offer.
    return [
        {"feature": "atr_percentile", "comparison": ">=", "value": p["vol_pct_min"]},
        {"feature": "close", "comparison": ">", "value_from": "ma20"},
        {"feature": "ma20", "comparison": ">", "value_from": "ma50"},
    ]


def _volatility_expansion_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "atr_percentile", "comparison": ">=", "value": p["vol_pct_min"]},
        {"feature": "close", "comparison": "<", "value_from": "ma20"},
        {"feature": "ma20", "comparison": "<", "value_from": "ma50"},
    ]


def _volatility_squeeze_long_entry(p: dict) -> list[dict]:
    # The opposite premise: bands compressed to the low end of their own range, price pushing
    # out of the upper one. It is `bollinger_breakout` with the compression made an explicit
    # precondition rather than left to chance, which is a different claim about WHEN the
    # breakout is worth taking.
    #
    # NOT MINTED — see RETIRED_FAMILIES. Kept because the claim above was never tested on its
    # own terms: what the store measured is that a compressed band is a small 1R, and a small
    # 1R loses to the fees before the claim gets a hearing.
    return [
        {"feature": "bb_width_percentile", "comparison": "<=", "value": p["squeeze_max"]},
        {"feature": "bb_percent_b", "comparison": ">=", "value": p["percent_b_min"]},
    ]


def _volatility_squeeze_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "bb_width_percentile", "comparison": "<=", "value": p["squeeze_max"]},
        {"feature": "bb_percent_b", "comparison": "<=", "value": p["percent_b_max"]},
    ]


TEMPLATES: tuple[StrategyTemplate, ...] = (
    # The crossover pairs lead, so a reader meets the one structural addition first.
    StrategyTemplate("ma_cross_up", "long", "1h",
                     {"adx_min": ParamSpec(15.0, 30.0), **_EXIT_PARAMS},
                     {"adx_min": 22.0, **_EXIT_BASE}, _ma_cross_up_entry),
    StrategyTemplate("ma_cross_down", "short", "1h",
                     {"adx_min": ParamSpec(15.0, 30.0), **_EXIT_PARAMS},
                     {"adx_min": 22.0, **_EXIT_BASE}, _ma_cross_down_entry),
    StrategyTemplate("macd_cross_up", "long", "1h",
                     {"adx_min": ParamSpec(15.0, 30.0), **_EXIT_PARAMS},
                     {"adx_min": 20.0, **_EXIT_BASE}, _macd_cross_up_entry),
    StrategyTemplate("macd_cross_down", "short", "1h",
                     {"adx_min": ParamSpec(15.0, 30.0), **_EXIT_PARAMS},
                     {"adx_min": 20.0, **_EXIT_BASE}, _macd_cross_down_entry),
    StrategyTemplate("trend_pullback", "long", "1h",
                     {"adx_min": ParamSpec(15.0, 30.0), "rsi_max": ParamSpec(45.0, 65.0), **_EXIT_PARAMS},
                     {"adx_min": 22.0, "rsi_max": 55.0, **_EXIT_BASE}, _trend_pullback_entry),
    StrategyTemplate("trend_pullback_short", "short", "1h",
                     {"adx_min": ParamSpec(15.0, 30.0), "rsi_min": ParamSpec(35.0, 55.0), **_EXIT_PARAMS},
                     {"adx_min": 22.0, "rsi_min": 45.0, **_EXIT_BASE}, _trend_pullback_short_entry),
    StrategyTemplate("breakout", "long", "1h",
                     {"adx_min": ParamSpec(18.0, 35.0), **_EXIT_PARAMS},
                     {"adx_min": 25.0, **_EXIT_BASE}, _breakout_entry),
    StrategyTemplate("breakdown_short", "short", "1h",
                     {"adx_min": ParamSpec(18.0, 35.0), **_EXIT_PARAMS},
                     {"adx_min": 25.0, **_EXIT_BASE}, _breakdown_short_entry),
    StrategyTemplate("mean_reversion", "long", "1h",
                     {"rsi_max": ParamSpec(20.0, 40.0), **_FADE_EXIT_PARAMS},
                     {"rsi_max": 30.0, **_FADE_EXIT_BASE}, _mean_reversion_long_entry),
    StrategyTemplate("mean_reversion_short", "short", "1h",
                     {"rsi_min": ParamSpec(60.0, 80.0), **_FADE_EXIT_PARAMS},
                     {"rsi_min": 70.0, **_FADE_EXIT_BASE}, _mean_reversion_short_entry),
    # `macd_momentum` / `_short` are RETIRED from the rotation — 2026-08-04. Their builders
    # are kept below and re-listing them here is the whole of re-enabling; see RETIRED_FAMILIES
    # for the measurement and for what would justify it.
    StrategyTemplate("bollinger_breakout", "long", "1h",
                     {"percent_b_min": ParamSpec(0.9, 1.1), "volume_z_min": ParamSpec(0.5, 2.0), **_EXIT_PARAMS},
                     {"percent_b_min": 1.0, "volume_z_min": 1.0, **_EXIT_BASE}, _bollinger_breakout_entry),
    StrategyTemplate("bollinger_breakdown_short", "short", "1h",
                     {"percent_b_max": ParamSpec(-0.1, 0.1), "volume_z_min": ParamSpec(0.5, 2.0), **_EXIT_PARAMS},
                     {"percent_b_max": 0.0, "volume_z_min": 1.0, **_EXIT_BASE}, _bollinger_breakdown_short_entry),
    StrategyTemplate("funding_fade_long", "long", "1h",
                     {"funding_z_max": ParamSpec(-2.5, -1.0), "rsi_max": ParamSpec(25.0, 45.0), **_FADE_EXIT_PARAMS},
                     {"funding_z_max": -1.5, "rsi_max": 38.0, **_FADE_EXIT_BASE}, _funding_fade_long_entry),
    StrategyTemplate("funding_fade_short", "short", "1h",
                     {"funding_z_min": ParamSpec(1.0, 2.5), "rsi_min": ParamSpec(55.0, 75.0), **_FADE_EXIT_PARAMS},
                     {"funding_z_min": 1.5, "rsi_min": 62.0, **_FADE_EXIT_BASE}, _funding_fade_short_entry),
    # HTF families (Thomas 2026-07-25). Ported now that every timeframe in the ladder
    # is collected — the "untimeable" objection that held them back was that the
    # higher leg's data was not there to time against, and it now is.
    #
    # `htf_trend_long` / `_short` are RETIRED from the rotation — 2026-08-04 — and REPLACED
    # rather than merely dropped: the pair below asks their question of a continuous column.
    # Their builders are kept and re-listing them here is the whole of re-enabling; see
    # RETIRED_FAMILIES for the measurement.
    #
    # The upper bound is 3% rather than open-ended: `htf_ma20_distance_ma50` above ~3% selects
    # a tail of bars thin enough that the family arrives unjudgeable for want of trades, which
    # is the failure this replacement exists to fix rather than to re-create at the other end
    # of the range. Same reasoning as `volatility_expansion_*`'s 0.9 percentile ceiling.
    StrategyTemplate("htf_trend_strength_long", "long", "1h",
                     {"htf_sep_min": ParamSpec(0.0, 0.030),
                      "adx_min": ParamSpec(15.0, 30.0), **_EXIT_PARAMS},
                     {"htf_sep_min": 0.008, "adx_min": 20.0, **_EXIT_BASE},
                     _htf_trend_strength_long_entry),
    StrategyTemplate("htf_trend_strength_short", "short", "1h",
                     {"htf_sep_min": ParamSpec(0.0, 0.030),
                      "adx_min": ParamSpec(15.0, 30.0), **_EXIT_PARAMS},
                     {"htf_sep_min": 0.008, "adx_min": 20.0, **_EXIT_BASE},
                     _htf_trend_strength_short_entry),
    StrategyTemplate("htf_pullback_long", "long", "1h",
                     {"rsi_max": ParamSpec(25.0, 45.0), **_EXIT_PARAMS},
                     {"rsi_max": 38.0, **_EXIT_BASE}, _htf_pullback_long_entry),
    StrategyTemplate("oi_squeeze_long", "long", "1h",
                     {"oi_change_min": ParamSpec(0.01, 0.08), "oi_z_min": ParamSpec(0.5, 2.0), **_EXIT_PARAMS},
                     {"oi_change_min": 0.03, "oi_z_min": 1.0, **_EXIT_BASE}, _oi_squeeze_long_entry),
    StrategyTemplate("oi_squeeze_short", "short", "1h",
                     {"oi_change_min": ParamSpec(0.01, 0.08), "oi_z_min": ParamSpec(0.5, 2.0), **_EXIT_PARAMS},
                     {"oi_change_min": 0.03, "oi_z_min": 1.0, **_EXIT_BASE}, _oi_squeeze_short_entry),
    StrategyTemplate("oi_unwind_long", "long", "1h",
                     {"oi_change_min": ParamSpec(0.01, 0.08), "rsi_max": ParamSpec(20.0, 40.0), **_FADE_EXIT_PARAMS},
                     {"oi_change_min": 0.03, "rsi_max": 30.0, **_FADE_EXIT_BASE}, _oi_unwind_long_entry),
    StrategyTemplate("oi_unwind_short", "short", "1h",
                     {"oi_change_min": ParamSpec(0.01, 0.08), "rsi_min": ParamSpec(60.0, 80.0), **_FADE_EXIT_PARAMS},
                     {"oi_change_min": 0.03, "rsi_min": 70.0, **_FADE_EXIT_BASE}, _oi_unwind_short_entry),
    StrategyTemplate("htf_pullback_short", "short", "1h",
                     {"rsi_min": ParamSpec(55.0, 75.0), **_EXIT_PARAMS},
                     {"rsi_min": 62.0, **_EXIT_BASE}, _htf_pullback_short_entry),
    # Taker order flow. The legs these read arrived in every klines response the collector
    # ever made; nothing new is fetched to mint them.
    StrategyTemplate("taker_flow_long", "long", "1h",
                     {"flow_ma_min": ParamSpec(0.02, 0.20), **_EXIT_PARAMS},
                     {"flow_ma_min": 0.06, **_EXIT_BASE}, _taker_flow_long_entry),
    StrategyTemplate("taker_flow_short", "short", "1h",
                     {"flow_ma_min": ParamSpec(0.02, 0.20), **_EXIT_PARAMS},
                     {"flow_ma_min": 0.06, **_EXIT_BASE}, _taker_flow_short_entry),
    StrategyTemplate("taker_absorption_long", "long", "1h",
                     {"flow_z_min": ParamSpec(0.8, 2.5), "rsi_max": ParamSpec(25.0, 45.0), **_FADE_EXIT_PARAMS},
                     {"flow_z_min": 1.3, "rsi_max": 38.0, **_FADE_EXIT_BASE}, _taker_absorption_long_entry),
    StrategyTemplate("taker_absorption_short", "short", "1h",
                     {"flow_z_min": ParamSpec(0.8, 2.5), "rsi_min": ParamSpec(55.0, 75.0), **_FADE_EXIT_PARAMS},
                     {"flow_z_min": 1.3, "rsi_min": 62.0, **_FADE_EXIT_BASE}, _taker_absorption_short_entry),
    # Premium index — the funding_fade premise at bar resolution instead of 8h steps.
    StrategyTemplate("premium_fade_long", "long", "1h",
                     {"premium_z_min": ParamSpec(1.0, 2.5), "rsi_max": ParamSpec(25.0, 45.0), **_FADE_EXIT_PARAMS},
                     {"premium_z_min": 1.5, "rsi_max": 38.0, **_FADE_EXIT_BASE}, _premium_fade_long_entry),
    StrategyTemplate("premium_fade_short", "short", "1h",
                     {"premium_z_min": ParamSpec(1.0, 2.5), "rsi_min": ParamSpec(55.0, 75.0), **_FADE_EXIT_PARAMS},
                     {"premium_z_min": 1.5, "rsi_min": 62.0, **_FADE_EXIT_BASE}, _premium_fade_short_entry),
    # Cross-asset relative strength. Not minted for the reference symbol itself — see
    # REFERENCE_FAMILIES and `templates_for_timeframe`.
    StrategyTemplate("rel_strength_long", "long", "1h",
                     {"rel_min": ParamSpec(0.005, 0.05), **_EXIT_PARAMS},
                     {"rel_min": 0.015, **_EXIT_BASE}, _rel_strength_long_entry),
    StrategyTemplate("rel_strength_short", "short", "1h",
                     {"rel_min": ParamSpec(0.005, 0.05), **_EXIT_PARAMS},
                     {"rel_min": 0.015, **_EXIT_BASE}, _rel_strength_short_entry),
    # Cross-sectional REVERSION — the first families whose entry depends on symbols the cycle
    # is not trading. Both param ranges are bounded by construction rather than by a guess:
    # `xs_rank_edge` at 0.4 is the widest value that keeps the long and short legs disjoint
    # (see `_xs_reversion_long_entry`), and `xs_dispersion_min` is a ratio against the cohort's
    # own recent dispersion, so 1.0 means "normal" on every symbol and every timeframe and
    # there is no level anybody had to authorize.
    #
    # This pair REPLACED `xs_momentum_long` / `_short` on 2026-08-04 rather than joining them —
    # the two readings of `xs_rank_pct` are mutually exclusive by construction and the library
    # may hold one. The builders above record why, and the retired pair's are kept.
    # Positioning divergence — minted only where the store's coverage reaches the replay span
    # (see POSITIONING_FAMILIES). The z threshold matches the funding and premium fade families,
    # because all three mine "how unusual is this crowding reading" over the same window.
    StrategyTemplate("positioning_divergence_long", "long", "1h",
                     {"divergence_z_min": ParamSpec(1.0, 2.5), **_EXIT_PARAMS},
                     {"divergence_z_min": 1.5, **_EXIT_BASE}, _positioning_divergence_long_entry),
    StrategyTemplate("positioning_divergence_short", "short", "1h",
                     {"divergence_z_min": ParamSpec(1.0, 2.5), **_EXIT_PARAMS},
                     {"divergence_z_min": 1.5, **_EXIT_BASE}, _positioning_divergence_short_entry),
    StrategyTemplate("xs_reversion_long", "long", "1h",
                     {"xs_rank_edge": ParamSpec(0.0, 0.4),
                      "xs_dispersion_min": ParamSpec(0.8, 1.6), **_EXIT_PARAMS},
                     {"xs_rank_edge": 0.2, "xs_dispersion_min": 1.0, **_EXIT_BASE},
                     _xs_reversion_long_entry),
    StrategyTemplate("xs_reversion_short", "short", "1h",
                     {"xs_rank_edge": ParamSpec(0.0, 0.4),
                      "xs_dispersion_min": ParamSpec(0.8, 1.6), **_EXIT_PARAMS},
                     {"xs_rank_edge": 0.2, "xs_dispersion_min": 1.0, **_EXIT_BASE},
                     _xs_reversion_short_entry),
    # Session context. `session_index` is mutated like any other parameter, so WHICH session
    # a spec claims is part of the seeded search and is charged as a free parameter.
    StrategyTemplate("session_trend_long", "long", "1h",
                     {"session_index": ParamSpec(0, len(features.SESSION_BOUNDS) - 1, integer=True),
                      "adx_min": ParamSpec(15.0, 30.0), **_EXIT_PARAMS},
                     {"session_index": 1, "adx_min": 20.0, **_EXIT_BASE}, _session_trend_long_entry),
    StrategyTemplate("session_trend_short", "short", "1h",
                     {"session_index": ParamSpec(0, len(features.SESSION_BOUNDS) - 1, integer=True),
                      "adx_min": ParamSpec(15.0, 30.0), **_EXIT_PARAMS},
                     {"session_index": 1, "adx_min": 20.0, **_EXIT_BASE}, _session_trend_short_entry),
    # Volatility regime — the expansion side only; the squeeze pair that shipped beside it is
    # in RETIRED_FAMILIES below. The param range is bounded by what leaves a sample behind
    # rather than by a preference: a percentile floor above 0.9 selects a tenth of the bars,
    # which is how a family arrives FRAGILE for want of trades rather than for want of edge.
    StrategyTemplate("volatility_expansion_long", "long", "1h",
                     {"vol_pct_min": ParamSpec(0.5, 0.9), **_EXIT_PARAMS},
                     {"vol_pct_min": 0.7, **_EXIT_BASE}, _volatility_expansion_long_entry),
    StrategyTemplate("volatility_expansion_short", "short", "1h",
                     {"vol_pct_min": ParamSpec(0.5, 0.9), **_EXIT_PARAMS},
                     {"vol_pct_min": 0.7, **_EXIT_BASE}, _volatility_expansion_short_entry),
    # `volatility_squeeze_long` / `_short` are RETIRED from the rotation — 2026-08-02. Their
    # builders are kept below and re-listing them here is the whole of re-enabling; see
    # RETIRED_FAMILIES for the measurement and for what would justify it.
)

# Families whose builders exist and which are deliberately NOT minted.
#
# `volatility_squeeze_*`, retired 2026-08-02 on its first full day of evidence: **28 candidates
# across 14 generations, ZERO of them ROBUST, median net −0.2241R/trade**, and the worst figures
# in the store at every timeframe (15m −0.4194R, 1h −0.1579R, 4h −0.0577R against family medians
# of −0.05R, +0.02R and +0.10R).
#
# It reads as a mechanism rather than a run of luck, which is why one day is enough to stop
# minting on it. The pair gates entry on `bb_width_percentile` being LOW — compressed bands are
# a narrow ATR, a narrow ATR is a narrow 1R, and 1R is the denominator every fixed-bps cost is
# divided by. Its sibling `volatility_expansion_*` takes the opposite side of the same variable
# and is measurably cheaper (0.2438R vs 0.2713R per trade at 15m, 0.1334R vs 0.1555R at 1h). One
# family widens the risk unit and one narrows it; the store now says which.
#
# Retired rather than deleted, and stopped rather than judged forever. The premise — "a breakout
# is worth more when it comes out of compression" — is not refuted by this; what is refused is
# paying for it with a risk unit too small to clear the fees. Re-list it if the entry gains a
# compensating widener (a target scaled to the squeeze, an ATR floor), or if the cost structure
# moves far enough that a narrow 1R stops being decisive.
#
# `macd_momentum` / `_short`, retired 2026-08-04, and this one is a **redundancy** finding rather
# than a cost one. The pair gates on `macd > macd_signal` plus `macd_hist > 0` — a STATE, true for
# the whole of a trend, which is precisely the defect the `lag` vocabulary was added to fix (see
# `strategy.PREVIOUS_BAR_PREFIX`'s note: a state family re-fires on every bar of a move rather than
# on the bar the relationship changed). The EVENT version of the same hypothesis is already in the
# library as `macd_cross_up` / `_down`, minted from the same `macd`/`macd_signal` pair with the
# same `adx_min` filter. So the rotation was spending two of its slots restating a hypothesis it
# already holds in a strictly better form.
#
# The evidence agrees and is well-sampled: measured on the holdout at the current cost basis,
# lineage-collapsed, `macd_momentum` reads **-0.0578R GROSS over 2,979 out-of-sample trades**
# (t_gross -2.97) and `macd_momentum_short` -0.0106R over 3,258. Six thousand out-of-sample
# trades is more evidence than any other retirement in this file has rested on.
#
# Re-list it if the crossover pair is itself retired (the state form is then the only form of
# the hypothesis left), or if a measurement ever separates them in the state form's favour.
#
# `xs_momentum_long` / `_short`, retired 2026-08-04, and this one is a **swap** — the slots went
# to `xs_reversion_long` / `_short`, which read the same `xs_rank_pct` column with the opposite
# sign. They cannot coexist: `xs_momentum_short` and `xs_reversion_long` fire on the identical
# condition, so both in the library means one bar matching a LONG and a SHORT on the same feature
# row, which `paper.route_entries` fails closed as `BLOCK_DIRECTION_CONFLICT`. The builders above
# carry the full reasoning. Measured: -0.0177R and -0.0048R GROSS over 3,040 and 2,626 holdout
# trades — 5,666 trades in which the momentum reading has not been positive before costs.
# Re-list it only by swapping the reversion pair back out.
#
# `htf_trend_long` / `_short`, retired 2026-08-04 and **replaced in the same commit** by
# `htf_trend_strength_*` — which is the difference from the retirement above. This pair is not
# stopped for producing bad evidence; it is stopped for producing evidence nobody can judge,
# and its premise moves to a family that can.
#
# The mechanism is one `return` in `features.classify_market_regime`: it tests volatility before
# it tests trend, so `htf_market_regime == "TREND_UP"` is false on every higher-timeframe bar in
# the top quintile of its own ATR range, however cleanly that bar is trending. 1R is
# `stop_atr` x ATR against a fixed-bps round trip, so those excluded bars are exactly the ones
# whose friction the family could best afford — the premise `volatility_expansion_*` was added
# on. Two lesser losses ride along: the ADX floor is `ADX_TREND_THRESHOLD = 20.0` hard-coded
# inside the classifier rather than a bound the search can move, and trend STRENGTH is discarded
# (a 0.1% ma20/ma50 separation and an 8% one are one label).
#
# Measured 2026-08-04 over 960 specs, 5 symbols x 12 paired draws x long/short, exits drawn from
# their own rng so the entry rule is the only difference between arms
# (`docs/REMAINING_WORK.md`, section F): at 4h the share of draws reaching `MIN_HOLDOUT_TRADES`
# goes **20% -> 48%** long and **17% -> 77%** short, at identical `free_parameters` (5).
#
# **This buys no edge and the retirement does not claim one.** Nothing in that measurement
# cleared the selection-adjusted bar (0 of 960; one spec cleared the uncorrected 1.96 where ~24
# are expected by chance), and the newly judgeable rows are judgeably NEGATIVE — the same result
# the pooled backtest reaches from the other direction. What the replacement buys is that a
# verdict on this premise becomes reachable at 4h, where four of the five routable strategies
# live and where the retired pair could not produce one. Re-list the old pair only if the
# regime label stops folding volatility over trend; the premise itself is unrefuted and is
# carried by the replacement.
RETIRED_FAMILIES = frozenset({
    "volatility_squeeze_long", "volatility_squeeze_short",
    "macd_momentum", "macd_momentum_short",
    "xs_momentum_long", "xs_momentum_short",
    "htf_trend_long", "htf_trend_short",
})

# Families whose entry rules read the open-interest columns — mintable only where the
# feed is configured; with no feed their conditions are indeterminate and never match,
# so a minted spec is harmless (it simply does not trade) rather than wrong.
OI_FAMILIES = frozenset({"oi_squeeze_long", "oi_squeeze_short",
                         "oi_unwind_long", "oi_unwind_short"})

# Families whose entry rules read HTF columns — mintable only where a higher
# timeframe exists to read (see ``market_data.HIGHER_TIMEFRAME``).
#
# Both the minted pair and the retired one are named, for the reason CROSS_SECTION_FAMILIES
# states below: the gate asks whether a family that reads `htf_*` may be minted here, which is
# a property of the COLUMNS and not of which pair currently reads them — so leaving the retired
# pair out would silently un-gate it the day somebody re-listed it.
HTF_FAMILIES = frozenset({"htf_trend_long", "htf_trend_short",
                          "htf_trend_strength_long", "htf_trend_strength_short",
                          "htf_pullback_long", "htf_pullback_short"})

# Families whose entry rules read ``session`` — mintable only where a bar is short enough
# for the label to describe the market during it rather than just its opening instant.
# `features.MAX_SESSION_BAR_MINUTES` owns that rule; this is the minting side of it.
SESSION_FAMILIES = frozenset({"session_trend_long", "session_trend_short"})

# Families whose entry rules read the cross-asset columns — mintable for every symbol EXCEPT
# the market proxy itself, whose relative strength against itself is a constant zero. The
# gate is on the symbol rather than the timeframe, which is why `templates_for_timeframe`
# takes one.
REFERENCE_FAMILIES = frozenset({"rel_strength_long", "rel_strength_short"})

# Families whose entry rules read the cross-sectional columns — mintable only where the
# declared cohort, minus the symbol being mined, still reaches
# `features.MIN_CROSS_SECTION_MEMBERS`. Currently that holds for every symbol, so the gate
# does not bind today; it exists because the cohort is a constant somebody may edit, and the
# failure it prevents is silent. Shrink `CROSS_SECTION_UNIVERSE` below the floor and every
# `xs_*` column becomes permanently None, so these families would still mint, still backtest,
# still take zero trades, and be retired as FRAGILE — the family blamed for a cohort that was
# too small to rank.
#
# Names only what is MINTED, which is why the retired `xs_momentum_*` pair is absent even though
# it reads the same columns: `tests/test_mvp_runtime_crypto_cross_section.py` iterates this set
# and requires every member to be in the rotation and to fire on a real frame, so a retired name
# here would turn that coverage into a StopIteration. Re-listing the momentum pair means adding
# it back here too — the same one-line reversal the retirement itself is.
CROSS_SECTION_FAMILIES = frozenset({"xs_reversion_long", "xs_reversion_short"})

# Families whose entry rules read the positioning columns — mintable only where the store has
# accumulated enough history to answer the whole replay window
# (`positioning_store.coverage_summary(...)["eligible"]`, which requires
# `REQUIRED_COVERAGE_DAYS = FACTORY_DEPTH_DAYS`).
#
# **This is the gate that turns "decide later" into "the data decides", and it is the reason the
# feature could be wired today at all.** The vendor keeps 30 days; the factory replays 500. Minted
# against a 6%-covered window these families would not merely be thin — they would be
# *un-scoreable*: the walk-forward split would put every trade in the newest slice and none in the
# older ones, so `temporal_consistency` is 0 by construction and no amount of real edge could
# clear the robustness bar. The family would then be retired as FRAGILE, blamed for a window that
# had no data in it. That is the `liquidation_spike_ratio` failure in a different costume, and it
# is why the note in the research record said "collect now, decide later".
#
# What changes here is only WHO decides. The columns, the alignment, the vocabulary and the
# families are built and tested now; the store's own measured coverage flips them on, so nobody
# has to remember a paragraph in a document sixteen months from now.
POSITIONING_FAMILIES = frozenset({"positioning_divergence_long", "positioning_divergence_short"})


def template_features(template: StrategyTemplate) -> frozenset[str]:
    """Every feature name ``template`` would mint a condition on.

    Derived by building the template's own conditions, not declared beside it. A declaration
    would be a third table to hold in step with `_FEATURE_FEED` and the builder itself, and
    the two that already existed were reduced to one for exactly that reason — a second
    statement of the same fact is a second thing that can be wrong.

    ``base_params`` is what the conditions are built from because the builders read params
    for THRESHOLDS only; which feature a condition names is fixed by the family.
    ``test_a_templates_feature_set_does_not_move_with_its_params`` holds that, since it is
    the assumption this whole function rests on.

    Lag needs no handling here: a lagged condition carries ``lag`` as its own key and leaves
    ``feature``/``value_from`` as base names, the same shape ``referenced_features`` reads.
    """
    names: set[str] = set()
    for cond in template.entry_builder(dict(template.base_params)):
        if cond.get("feature"):
            names.add(str(cond["feature"]))
        if cond.get("value_from"):
            names.add(str(cond["value_from"]))
    return frozenset(names)


def _oi_feed_reaches(timeframe: str) -> bool:
    """Does the derivative feed's history span what the factory replays at ``timeframe``?

    **The premise `market_data` states beside the OI interval — "the only depth that covers the
    factory's 500-day replay" — is true of every timeframe except 1d, and nothing noticed for
    six days.** `MIN_FACTORY_BARS` (2026-07-29) floored 1d at 2,000 BARS to buy it enough trades
    to be scoreable, and at 1d a bar is a day, so the window stopped being a 500-day calendar
    span and became a 2,000-day one. The daily open-interest series is
    :data:`market_data.DERIVATIVE_HISTORY_DAYS` = 520 days, so it answers about a quarter of it.

    It went unseen because the 1d contexts left the factory rotation on 2026-07-23, six days
    BEFORE the floor landed — the floor and this premise never both applied to a minting context
    until 1d was put back on 2026-08-04. Measured that day on live frames: with the feed
    configured, all four oi_* families are determinate on ~26% of the 1d replay rows and on 100%
    of the 15m/1h/4h ones.

    A quarter-covered window is not merely thin, it is the failure `POSITIONING_FAMILIES`
    documents: the walk-forward split puts every trade in the newest slice and none in the older
    ones, so ``temporal_consistency`` is 0 by construction, no edge can clear the robustness bar,
    and the family is retired as FRAGILE for a window that had no data in it. `unsuppliable_features`
    does not catch it — that refuses a column None on EVERY row, and this one is populated on the
    newest quarter.

    Arithmetic rather than a measured parameter, unlike ``positioning_eligible``: that store
    accumulates and its coverage genuinely changes day to day, while this is the depth this
    runtime *asks* for against the window it *chose* to replay. Both are constants here, so a
    caller has nothing to measure and cannot get it wrong by omission.
    """
    minutes = market_data.TIMEFRAMES.get(str(timeframe))
    if minutes is None:
        return False
    replay_days = market_data.factory_candle_target(str(timeframe)) * minutes / 1440.0
    return replay_days <= market_data.DERIVATIVE_HISTORY_DAYS


def templates_for_timeframe(
    timeframe: str, *, symbol: str | None = None, positioning_eligible: bool = False,
    venue: str = market_data.BINANCE_FUTURES,
) -> tuple[StrategyTemplate, ...]:
    """The rotation retimed to ``timeframe`` (and narrowed for ``symbol``).

    Every price/feed family is retimeable. Four groups need more than a retiming, and all
    four drop out for the same reason — a spec whose conditions can never be *determined* is
    permanently no-entry, which is noise pretending to be diversity:

    - the htf_* families need a higher timeframe to read, so they drop at the top of the
      ladder (``1d``);
    - the session_* families need a bar shorter than one session block, so they drop at ``1d``
      too, where every bar opens at 00:00 UTC and ``features`` reports no session at all;
    - the rel_strength_* families need a reference symbol that is not this one, so they drop
      when mining the market proxy — ``features`` returns None for every ``ref_*`` column
      there rather than a correlation of 1.0 against itself;
    - the xs_* families need a cohort that still reaches
      ``features.MIN_CROSS_SECTION_MEMBERS`` after this symbol is taken out of it;
    - the positioning_* families need a store whose accumulated coverage spans the replay
      window, which the caller measures and passes as ``positioning_eligible``;
    - the oi_* families need a derivative feed whose history reaches the replay window, which
      is pure arithmetic here rather than a parameter — see ``_oi_feed_reaches``.

    ``symbol=None`` keeps the reference families: a caller that does not say which symbol it
    is mining is asking for the library, not for a mintable set, and narrowing on a guess
    would silently hide families from whoever asked. The cross-sectional gate needs no such
    carve-out — its cohort is a declared constant, so ``symbol=None`` only ever removes one
    fewer member than a named symbol would, which cannot turn a passing cohort into a failing
    one.

    ``positioning_eligible`` takes the OPPOSITE default to that, and the asymmetry is the point:
    an unstated symbol is a question about the library, but unstated coverage is a caller who did
    not measure — and the cost of guessing wrong is a family mined over a window that is 94%
    indeterminate, scored as FRAGILE, and retired for it. Fail closed. It is a parameter rather
    than a read because this function is pure and the coverage lives on disk; the scheduler's
    factory path is where the store is read.

    The taker_* and premium_* families need no gate of that kind — their legs ride the same
    klines call as the OHLCV, at every timeframe and for every symbol. They do need the last
    one below, because riding the klines call is a statement about Binance's klines.

    ``venue`` is a gate of a different kind and the strongest of them. The five above ask
    whether a family's conditions can be DETERMINED in this context; this one asks whether
    the venue's data can produce them at all, and the answer does not improve with a
    different timeframe or symbol. Minting them anyway would be the validator's problem to
    catch — it does, per spec — but a family that can only ever be refused is a rotation slot
    spent producing nothing, and `liquidation_spike_ratio` is the case where refusal is not
    even the outcome: with no feed it reads a constant 0.0 rather than None, so a mined
    condition on it is not indeterminate, it is TRUE on every bar of a venue that never
    measured it. The default is `binance_futures` for the reason `StrategySpec.venue` carries
    the same one — it is the only venue anything has been mined on."""
    timeframe = str(timeframe)
    has_htf = timeframe in market_data.HIGHER_TIMEFRAME
    bar_minutes = market_data.TIMEFRAMES.get(timeframe)
    has_session = bar_minutes is not None and bar_minutes <= features.MAX_SESSION_BAR_MINUTES
    has_reference = symbol is None or str(symbol) != market_data.REFERENCE_SYMBOL
    # +1 for the symbol being mined: it is a cohort member (the one being ranked) whether or
    # not it is a declared universe member.
    cohort_size = 1 + sum(
        1 for member in market_data.CROSS_SECTION_UNIVERSE if member != str(symbol)
    )
    has_cross_section = cohort_size >= features.MIN_CROSS_SECTION_MEMBERS
    has_oi_history = _oi_feed_reaches(timeframe)
    # Raises on an undeclared venue rather than resolving to an empty vocabulary, which would
    # silently return no templates at all and read as "this timeframe mints nothing".
    numeric, categorical = known_features(venue)
    mintable = numeric | frozenset(categorical)

    def _minted(template: StrategyTemplate) -> bool:
        if template.family in HTF_FAMILIES and not has_htf:
            return False
        if template.family in SESSION_FAMILIES and not has_session:
            return False
        if template.family in REFERENCE_FAMILIES and not has_reference:
            return False
        if template.family in CROSS_SECTION_FAMILIES and not has_cross_section:
            return False
        if template.family in POSITIONING_FAMILIES and not positioning_eligible:
            return False
        if template.family in OI_FAMILIES and not has_oi_history:
            return False
        # Whole-family, not per-condition: a template is one premise, and one it can state
        # only half of is a different premise nobody chose to mine.
        if not template_features(template) <= mintable:
            return False
        return True

    return tuple(replace(t, timeframe=timeframe) for t in TEMPLATES if _minted(t))


# --- S3 validator (source rules, restricted to the ported feature registry) ---

# --- which feed each mintable feature needs ----------------------------------------------
#
# TOTAL over the vocabulary: every mintable feature names a feed, `FEED_CANDLES` included.
#
# It was not always. Until this table absorbed the candle-derived half it listed only the
# features needing something BEYOND candles, and `known_features` read "absent from the table"
# as "available wherever candles are" — so a feature nobody classified was mintable on EVERY
# venue, including one whose data cannot produce it. The guard for that was a test, and a test
# only holds while it is written correctly; the first version of it checked the containment
# that happened to be true rather than the one that mattered, and caught nothing.
#
# So the default moved into the code. An unclassified feature now resolves to
# `market_data.UNCLASSIFIED_FEED`, which no venue declares and none can, so it is mintable
# NOWHERE and any spec naming it takes BLOCK_UNKNOWN_FEATURE. Still a mistake — it just fails
# in the direction that refuses to trade rather than the one that trades on absent data.
# `test_every_mintable_feature_is_classified` remains, and now catches the mistake at CI
# instead of being the only thing standing between it and a mined constant.
_FEATURE_FEED: dict[str, str] = {
    # Everything OHLCV alone produces — directly, or through the higher-timeframe,
    # reference-symbol and cross-section legs, which are all just more candles.
    **{name: market_data.FEED_CANDLES for name in (
        "open", "high", "low", "close", "volume",
        "ma20", "ma50", "ema20", "ema50", "atr", "atr_pct_of_price", "atr_percentile",
        "rsi", "adx", "macd", "macd_signal", "macd_hist",
        "bb_upper", "bb_lower", "bb_width_pct", "bb_percent_b", "bb_width_percentile",
        "roc_4", "price_distance_ma20", "volume_zscore",
        # One step up the ladder — the same indicators over a slower candle.
        "htf_rsi", "htf_adx", "htf_price_distance_ma20", "htf_ma20_distance_ma50",
        # One proxy symbol's candles beside this symbol's.
        "ref_roc_4", "rel_strength_roc_4", "ref_correlation",
        # A cohort's candles, reduced to this symbol's place among them.
        "xs_rank_pct", "xs_excess_roc_4", "xs_dispersion_ratio",
        # The categoricals are classifiers over the candle series. Named individually rather
        # than swept in from `CATEGORICAL_FEATURES`, because a future categorical need not be
        # candle-derived — a funding or positioning REGIME would be a label over a feed.
        "market_regime", "htf_market_regime", "ref_market_regime", "session", "day_type",
    )},
    "funding_rate": market_data.FEED_FUNDING,
    "funding_zscore": market_data.FEED_FUNDING,
    "mark_price": market_data.FEED_DERIVATIVE_PRICE,
    "index_price": market_data.FEED_DERIVATIVE_PRICE,
    "mark_index_basis_bps": market_data.FEED_DERIVATIVE_PRICE,
    "premium_index": market_data.FEED_DERIVATIVE_PRICE,
    "premium_index_zscore": market_data.FEED_DERIVATIVE_PRICE,
    "liquidation_spike_ratio": market_data.FEED_LIQUIDATION,
    "liquidation_total": market_data.FEED_LIQUIDATION,
    "long_liquidation": market_data.FEED_LIQUIDATION,
    "short_liquidation": market_data.FEED_LIQUIDATION,
    "open_interest_change_pct": market_data.FEED_OPEN_INTEREST,
    "open_interest_zscore": market_data.FEED_OPEN_INTEREST,
    "positioning_divergence_change": market_data.FEED_POSITIONING,
    "positioning_divergence_zscore": market_data.FEED_POSITIONING,
    # The aggressor split, and only it. `avg_trade_size_zscore` and `trade_count_zscore` sit
    # in the same feature builder but need the trade COUNT rather than the buy/sell split —
    # grouping them here by where the code lives would disable two features a venue can serve.
    "taker_buy_ratio": market_data.FEED_TAKER_FLOW,
    "taker_flow_imbalance": market_data.FEED_TAKER_FLOW,
    "taker_flow_zscore": market_data.FEED_TAKER_FLOW,
    "taker_flow_ma": market_data.FEED_TAKER_FLOW,
    "avg_trade_size_zscore": market_data.FEED_TRADE_COUNT,
    "trade_count_zscore": market_data.FEED_TRADE_COUNT,
}

def known_features(venue: str) -> tuple[frozenset[str], dict[str, Any]]:
    """The (numeric, categorical) vocabulary a spec mined on ``venue`` may name.

    A feature whose feed the venue does not provide is not merely useless there — it is
    unsafe. The evaluator treats a missing column as indeterminate, so most such specs
    would never fire, never accrue a sample, and so never be demotable off a routing slot
    (the latch `BUILD_HISTORY` records for the loss breaker). **And one is worse than that:**
    `liquidation_spike_ratio` falls back to a constant 0.0 with no feed rather than to None,
    so a mined `< x` condition on it matches every bar forever, on a number nobody measured.

    Fail-closed twice, on the two things that can be missing here.

    An unknown VENUE raises — `market_data.venue_feeds` refuses rather than returning an
    empty vocabulary, so a typo blocks instead of silently rejecting every feature.

    An unclassified FEATURE resolves to `UNCLASSIFIED_FEED`, which no venue declares, so it
    is admitted nowhere. `.get` with that default is the whole mechanism and the reason
    `_FEATURE_FEED` is total: the alternative reading of a missing key — "needs nothing
    beyond candles, so it is available everywhere" — hands a venue a feature its data cannot
    produce, and `liquidation_spike_ratio`'s constant-0.0 fallback makes that a mined
    condition matching every bar rather than an inert one.
    """
    feeds = market_data.venue_feeds(venue)
    numeric = frozenset(
        name for name in NUMERIC_FEATURES
        if _FEATURE_FEED.get(name, market_data.UNCLASSIFIED_FEED) in feeds
    )
    categorical = {
        name: values for name, values in CATEGORICAL_FEATURES.items()
        if _FEATURE_FEED.get(name, market_data.UNCLASSIFIED_FEED) in feeds
    }
    return numeric, categorical


def validate_strategy(spec: StrategySpec) -> dict[str, Any]:
    """Approval-for-backtest verdict. Pure, fail-closed, never mutates.

    Judged against the vocabulary of the spec's OWN venue (`known_features`), which is why
    the venue rides on the spec rather than arriving as an argument here — an argument could
    be omitted or supplied wrongly, and a spec cannot be separated from where it was mined.
    """
    reasons: list[str] = []
    try:
        numeric_features, categorical_features = known_features(spec.venue)
    except ToolBlocked:
        # An undeclared venue is not a feature problem and must not be reported as one:
        # every condition would "fail" for a reason that has nothing to do with it.
        return {
            "strategy_id": spec.strategy_id,
            "strategy_rule_hash": spec.strategy_rule_hash,
            "approved_for_backtest": False,
            "block_reasons": ["BLOCK_UNKNOWN_VENUE"],
        }
    if spec.schema_version != SCHEMA_VERSION:
        reasons.append("BLOCK_SCHEMA_VERSION")
    if len(spec.entry_rules.conditions) > MAX_ENTRY_CONDITIONS:
        reasons.append("BLOCK_TOO_MANY_CONDITIONS")
    for cond in spec.entry_rules.conditions:
        if cond.feature in numeric_features:
            if cond.comparison not in _NUMERIC_COMPARISONS:
                reasons.append("BLOCK_INVALID_COMPARISON")
            if cond.value is not None and isinstance(cond.value, str):
                reasons.append("BLOCK_INVALID_FEATURE_VALUE")
        elif cond.feature in categorical_features:
            if cond.comparison not in _CATEGORICAL_COMPARISONS:
                reasons.append("BLOCK_INVALID_COMPARISON")
            if cond.value is not None and cond.value not in categorical_features[cond.feature]:
                reasons.append("BLOCK_INVALID_FEATURE_VALUE")
        else:
            reasons.append("BLOCK_UNKNOWN_FEATURE")
        if cond.value_from is not None and cond.value_from not in numeric_features:
            reasons.append("BLOCK_UNKNOWN_FEATURE" if cond.value_from not in categorical_features
                           else "BLOCK_VALUE_FROM_NOT_NUMERIC")

    exit_rules = spec.exit_rules
    if not (STOP_ATR_RANGE[0] <= exit_rules.stop_atr <= STOP_ATR_RANGE[1]):
        reasons.append("BLOCK_INVALID_PARAMETER_RANGE")
    if not (TARGET_ATR_RANGE[0] <= exit_rules.target_atr <= TARGET_ATR_RANGE[1]):
        reasons.append("BLOCK_INVALID_PARAMETER_RANGE")
    if not (MAX_HOLDING_BARS_RANGE[0] <= exit_rules.max_holding_bars <= MAX_HOLDING_BARS_RANGE[1]):
        reasons.append("BLOCK_UNBOUNDED_HOLDING")
    if exit_rules.target_atr / exit_rules.stop_atr < MIN_REWARD_RISK:
        reasons.append("BLOCK_INVALID_RISK_REWARD")
    if spec.risk_constraints.max_risk_per_trade_R > MAX_RISK_PER_TRADE_R:
        reasons.append("BLOCK_INVALID_PARAMETER_RANGE")

    block_reasons = sorted(set(reasons))
    return {
        "strategy_id": spec.strategy_id,
        "strategy_rule_hash": spec.strategy_rule_hash,
        "approved_for_backtest": not block_reasons,
        "block_reasons": block_reasons,
    }


# --- S2 generator (seeded, verbatim mechanics) --------------------------------

def mutate_params(
    base_params: dict[str, float], param_space: dict[str, ParamSpec], rng: random.Random,
    *, scale: float = _MUTATION_SCALE,
) -> dict[str, float]:
    """Perturb each parameter within a fraction of its range, clamped to bounds.

    Every parameter is drawn independently EXCEPT the exit pair — see
    :func:`_apply_reward_risk_floor` for the one coupling and why it lives here."""
    out: dict[str, float] = {}
    for name, spec in param_space.items():
        base = base_params[name]
        span = (spec.hi - spec.lo) * scale
        val = base + rng.uniform(-span, span)
        val = max(spec.lo, min(spec.hi, val))
        out[name] = int(round(val)) if spec.integer else round(val, 4)
    _apply_reward_risk_floor(out, base_params, param_space, rng, scale=scale)
    return out


# The one pair of generated parameters that is not free of the other, and the reason the coupling
# is enforced here rather than by shaping each space's bounds.
#
# `validate_strategy` refuses `target_atr / stop_atr < MIN_REWARD_RISK`, and the loop above draws
# both independently. So any space whose ``target_atr.lo`` sits below its ``stop_atr.hi`` proposes
# pairs the validator then refuses: the attempt is spent against `_MAX_ATTEMPTS_PER_SPEC`, and
# whatever survives is biased toward the high-target corner, because that corner is the only one
# that is never refused.
#
# Measured 2026-08-04 over 200,000 draws from `_EXIT_PARAMS` at `_EXIT_BASE`: **4.71% refused**,
# minimum drawn R:R 0.925. That is the rate from the TEMPLATE base, and it is the floor rather
# than the figure — `generate_batch` centres half of every batch on `elite_base_params`, which
# returns the params of a prior ACCEPTED candidate, so every accepted draw is a reachable centre.
# Over 400 reachable centres: median 0%, mean 4.99%, p90 **19.4%**, worst **36.9%** (at centre
# stop=1.7185, target=1.7627), with 9.5% of centres above 20%. `champion_score` picks the centre
# and knows nothing about R:R, so nothing stops a family settling beside the diagonal and
# refusing a third of its own draws for as long as that centre holds.
#
# **Raising ``target_atr.lo`` to ``stop_atr.hi`` — the `_FADE_EXIT_PARAMS` construction — is the
# obvious fix and is the wrong one for the trend space.** It deletes target_atr [1.6, 2.0), which
# is 27.7% of trend draws, and that region is not dead weight: bucketed on the candidate store by
# holdout R/trade it is the LEAST negative band (target==1.6 -0.2642, 1.6-2.0 -0.2502, 2.0-3.0
# -0.3059, >=3.0 -0.3216), and paired within (family, timeframe, symbol) to control the confound
# the `_EXIT_PARAMS` note warns about it runs +0.0174R median in favour of the low band, 34 of 58
# cells. Weak and near enough to null — but the direction is wrong for deleting it, and a fade
# space's argument does not transfer to a trend space that measures differently.
#
# The bound cannot move without the base moving either: at ``target_atr.lo`` 2.0 the base 3.0
# pins **26.2%** of draws on the new floor (against 18.9% on the present one), which is the
# collapse `_EXIT_BASE` documents, and clearing it needs base 4.1 — median target 3.00 -> 4.10,
# median R:R 2.07 -> 2.83. That is not a consistency fix, it is a re-aiming of the trend geometry
# with nothing measured behind it.
#
# So the target is drawn against a floor the stop sets. Two properties make this cheap:
#
# - **It is a redraw, not a clamp.** Clamping the target up to the stop is one line shorter and
#   puts 4.71% of draws on R:R exactly 1.0 — the worst legal ratio in the space — where the
#   redraw leaves 0.005%. Measured, that concentration does NOT run away through the elite loop
#   (it holds at ~4.2% over 40 generations rather than compounding), so it is a standing bias
#   and not a spiral; it is still 4.71% of the search spent on the boundary for no reason.
# - **The redraw is the truncated draw in closed form**, not a retry loop: one extra `rng` call,
#   no arbitrary retry ceiling, and the same distribution — against rejection sampling at 300,000
#   draws the target mean and median agree to <=0.0008 and <=0.0012 at the template base, at an
#   adverse elite centre, and at the worst corner of the space.
#
# Net effect on the trend space: refusals 4.71% -> 0.00%, floor pinning 18.9% -> 14.4%, the low
# band kept (target<2.0 27.7% -> 23.5%, the loss being only the part that was never mintable),
# median target 3.002 -> 3.122, median R:R 2.072 -> 2.137.
#
# **This does not make a space's own bounds irrelevant, and `_FADE_EXIT_PARAMS` should stay
# self-consistent.** A space that satisfies ``target.lo >= stop.hi`` never reaches this function,
# which is a stronger guarantee than being repaired by it: the repair costs an rng draw and bends
# the target's marginal distribution, however slightly. This is the safety net for a space that
# cannot be built that way, not a licence to stop building them that way.
#
# The deeper fix is not taken here: the quantity the validator constrains is the RATIO, so a space
# expressing ``target_atr`` as a multiple of the drawn ``stop_atr`` would make the floor
# structural and need no repair at all. That changes the recorded `mint_params` key set, which
# `elite_base_params` reads off stored candidates, and would strand every centre in the store.
def _apply_reward_risk_floor(
    out: dict[str, float], base_params: dict[str, float], param_space: dict[str, ParamSpec],
    rng: random.Random, *, scale: float,
) -> None:
    """Redraw ``target_atr`` above ``MIN_REWARD_RISK x`` the drawn ``stop_atr``, in place."""
    target_spec = param_space.get("target_atr")
    if target_spec is None or "stop_atr" not in param_space:
        return
    floor = out["stop_atr"] * MIN_REWARD_RISK
    if out["target_atr"] >= floor:
        return
    # No legal target exists inside this space for the stop that was drawn, so there is nothing
    # to redraw toward: leave the draw alone and let `validate_strategy` refuse the pair.
    #
    # The ceiling clamp at the end of this function is what prevents a target ABOVE the space
    # being invented here — the laundering `_fused_exit_param` refuses for the same reason — and
    # it holds with or without this branch. What the branch buys is that the impossible case
    # stays a DRAW instead of becoming the ceiling: without it every such pair returns
    # `target_spec.hi` exactly, a value the search never chose, recorded into `mint_params` and
    # read back later as an elite centre.
    if floor > target_spec.hi:
        return
    lo = max(target_spec.lo, floor)
    base = base_params["target_atr"]
    span = (target_spec.hi - target_spec.lo) * scale
    # The mutation window intersected with the legal region. `max(lo, ...)` on BOTH ends is what
    # keeps the interval non-empty when the window sits entirely below the floor — a case
    # `_EXIT_PARAMS` cannot reach (its span is 2.24 against a floor that never exceeds 2.0) but
    # which a future space could.
    val = rng.uniform(max(lo, base - span), max(lo, min(target_spec.hi, base + span)))
    # `max(lo, ...)` after rounding, not before: rounding to the 4-decimal grid can land a hair
    # under the floor once `MIN_REWARD_RISK` is not 1.0 and the floor is off-grid.
    out["target_atr"] = min(target_spec.hi, max(lo, round(val, 4)))


# --- where the search looks next ------------------------------------------------------------
#
# `mutate_params` draws `base +/- (hi - lo) * 0.35` around `template.base_params`, and that base
# is a CONSTANT. A hundred generations later the search is still sampling the same neighbourhood
# it started in: repeated sampling, not evolution. Nothing a generation learns changes where the
# next one looks.
#
# Moving the centre is the fix, and it has a hazard that has to be named or it is a worse bug
# than the one it closes. Hill-climbing on a noisy fitness surface converges on the noise —
# which is exactly the failure this store already exhibits, expectancy falling toward the cost
# of trading as sample size grows. So two rules:
#
# **The centre follows ROBUSTNESS, never expectancy.** Centring on the highest expectancy is
# precisely the mechanism that produced a store of maxima. `champion_score` is the anti-overfit
# score; a region that scores well there has more trades per parameter and broader regimes
# behind it, which is what "worth looking near" should mean.
#
# **Half the draws stay home.** Even-indexed mints use the elite centre, odd-indexed use the
# template's own base. The search cannot collapse onto one point however good that point looks,
# and the region the template was written for is never abandoned. Deterministic — it is the
# accept index, not a coin flip.
#
# What keeps this honest is the two gates that landed first: #438's holdout interval and #440's
# selection-adjusted ranking, which charges the attempt count that concentrating the search will
# raise. The open risk is A2 — the holdout tail is shared, so a region that got lucky on it can
# be confirmed repeatedly. That is not closed here and concentrating the search makes it matter
# more, which is the honest reason it is written down rather than left implicit.
ELITE_EVIDENCE_MIN_TRADES = 20


def elite_base_params(
    candidates: list[Mapping[str, Any]], *, family: str, symbol: str, timeframe: str,
    fallback: dict[str, float],
) -> dict[str, float]:
    """The params of the most ROBUST prior candidate for this family and context.

    ``fallback`` (the template's own base) whenever there is nothing better to say: no prior
    candidate here, none carrying `mint_params`, or none with enough closed trades for its score
    to mean anything. A centre moved on three trades is not a lesson.

    Pure and deterministic given the store, which is what keeps a replay reproducible — the
    centre is derived from recorded evidence, never from wall-clock or draw order.
    """
    best_score = None
    best_params: dict[str, float] | None = None
    for record in candidates:
        spec = record.get("strategy_spec") or {}
        if spec.get("strategy_family") != family or spec.get("timeframe") != timeframe:
            continue
        if symbol not in (spec.get("symbol_scope") or []):
            continue
        params = record.get("mint_params")
        if not isinstance(params, Mapping) or not params:
            continue
        evidence = record.get("backtest_evidence") or {}
        closed = evidence.get("closed_count")
        if not isinstance(closed, (int, float)) or closed < ELITE_EVIDENCE_MIN_TRADES:
            continue
        score = record.get("champion_score")
        if not isinstance(score, (int, float)):
            continue
        # `>` not `>=`: on a tie the EARLIER record wins, so the centre does not drift with
        # store order among equals.
        if best_score is None or score > best_score:
            best_score, best_params = float(score), {str(k): v for k, v in params.items()}
    if best_params is None:
        return dict(fallback)
    # Only keys the template still declares: a param the family dropped must not be resurrected
    # from an old record, and one it gained must come from the template's own base.
    return {name: best_params.get(name, fallback[name]) for name in fallback}


def build_spec_dict(
    template: StrategyTemplate, params: dict[str, float], *,
    strategy_id: str, generation_id: str, symbol: str = "BTCUSDT",
    venue: str = market_data.BINANCE_FUTURES,
    symbol_scope: Sequence[str] | None = None,
) -> dict[str, Any]:
    # `venue` is recorded here rather than left to `StrategySpec`'s default because this is
    # where the fact exists: a spec mined by this function was mined on that venue's data.
    # The default agrees with the dataclass's for the same reason it has one.
    #
    # `symbol_scope` defaults to `[symbol]`, which is what every one of the 1,140 stored
    # candidates carries and what `run_factory` still mints. A caller may widen it for a spec
    # backtested by `backtest_spec_pooled` over that same set — the two have to agree or the
    # evidence describes a different strategy than the record claims, which is why the scope is
    # an argument here rather than something the pooled backtest infers. Sorted, because
    # `symbol_scope` is inside `strategy_rule_fingerprint`: the same spec reached by a different
    # caller ordering must be the same rule hash.
    scope = sorted({str(s) for s in symbol_scope}) if symbol_scope else [symbol]
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": strategy_id,
        "strategy_version": "1.0",
        "generation_id": generation_id,
        "strategy_family": template.family,
        "status": "GENERATED",
        "symbol_scope": scope,
        "timeframe": template.timeframe,
        "direction": template.direction,
        "entry_rules": {"operator": "AND", "conditions": template.entry_builder(params)},
        "exit_rules": {
            "stop_model": "atr",
            "stop_atr": params["stop_atr"],
            "target_atr": params["target_atr"],
            "max_holding_bars": int(params["max_holding_bars"]),
        },
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
        "created_by": "mvp_factory",
        "venue": venue,
    }


def context_rotation_index(
    existing_candidates: list[Mapping[str, Any]], *, symbol: str, timeframe: str,
) -> int:
    """How many times THIS ``(symbol, timeframe)`` has already been mined.

    The rotation cursor, and the thing the generation number cannot be. Generation ids
    are global — every scheduled fire takes the next one across every context — so a
    context's own generation numbers stride by however many factory schedules exist
    (15 on this machine: 5 symbols x 3 timeframes). ``_rotation_offset`` multiplies
    that stride by ``count``, and a strided walk over a modulus does not visit every
    residue: see :func:`_rotation_offset` for the arithmetic and what it cost.

    Counting DISTINCT generation ids rather than candidates, because one fire mints a
    whole batch (plus any fused children) under a single id and that is one step of the
    rotation, not four.

    Where it is imprecise it is imprecise in the safe direction. A fire that minted
    nothing for this context does not advance the cursor, so the context re-mints the
    same block next time — with a fresh seed (the candle hash moved), so it mints
    different parameters of the same families rather than stalling. And the absolute
    value does not matter at all: the imported predecessor rows inflate the starting
    count for some contexts, which only sets the PHASE. What the rotation owes is that
    the cursor advances by one per fire, and it does."""
    seen: set[str] = set()
    for record in existing_candidates:
        spec = record.get("strategy_spec")
        if not isinstance(spec, Mapping):
            continue
        if str(spec.get("timeframe")) != str(timeframe):
            continue
        scope = spec.get("symbol_scope")
        if not isinstance(scope, (list, tuple)) or symbol not in scope:
            continue
        for value in (record.get("generation_id"), spec.get("generation_id")):
            if isinstance(value, str) and value:
                seen.add(value)
                break
    return len(seen)


def context_rotation_phase(symbol: str, timeframe: str, *, count: int, total: int) -> int:
    """Where in the rotation this context STARTS, so two contexts do not march in lockstep.

    :func:`context_rotation_index` fixed which families a context can reach; this fixes how
    many the factory reaches per fire. Its docstring already names the missing half — "the
    absolute value does not matter at all, it only sets the PHASE" — and the phase it was
    relying on is the count of generations already in the store, which every context acquires
    at the same rate because they all fire on the same schedule. So they converge, and
    measured 2026-08-04 they had: **13 of 20 contexts sat on rotation_index 11**, all four
    non-BTC symbols identical across 15m/1h/4h, and the only contexts off the block were the
    1d ones whose imported row counts happened to differ. The phases that existed were an
    accident of history rather than the design.

    What that costs is breadth per day. A fire mints ``count`` consecutive families, so 20
    synchronised contexts mint the SAME four — measured in the candidate store, 2026-08-03's
    seeded mints were ``bollinger_breakout`` 14, ``bollinger_breakdown_short`` 14,
    ``macd_momentum`` 13, ``macd_momentum_short`` 13, i.e. one block of four drawn 14 times
    over. The library is 36-38 families and a fire advances by 4, so any given family gets one
    day of mints roughly every ten, and ``htf_trend_long`` — the only family in the store with
    a positive median holdout — had not been minted since 2026-07-30.

    The phase is a stable hash of the context rather than a counter, because it must not move
    when the store is pruned, re-imported, or read on another machine: a phase that drifts
    would re-synchronise the contexts it exists to separate. Taken modulo the number of
    distinct offsets the walk actually visits (``total // gcd(count, total)``) — a phase past
    that lands on an offset the rotation already covers and buys nothing.

    **Coverage is unchanged and that is the property to preserve on any edit here.** A fire at
    ``k`` reads offsets ``(k + phase) * count``, which steps by ``count`` exactly as before, so
    ``ceil(total / count)`` consecutive fires still tile the whole library whatever the phase
    is. This shifts WHERE a context starts, never how much it eventually sees.
    """
    if total <= 0:
        return 0
    cycle = total // math.gcd(max(count, 1), total)
    digest = integrity.short_id(
        "crypto_rotation_phase", {"symbol": str(symbol), "timeframe": str(timeframe)}
    )
    return int(digest.rsplit("_", 1)[1][:8], 16) % max(cycle, 1)


def _rotation_offset(generation_id: str, seed: int, count: int, total: int,
                     rotation_index: int | None = None, phase: int = 0) -> int:
    """The first family index this run mints. Deterministic, marches forward.

    ``rotation_index`` is the caller's per-context cursor
    (:func:`context_rotation_index`) and is the correct step. The generation-number
    fallback below is kept for callers whose generations really are consecutive — the
    proposer CLI, and tests that mint GEN-000, GEN-001, ... by hand — where it is
    equivalent and needs no store to compute.

    **Why the fallback is not enough, measured 2026-07-31.** A step that advances by
    `s` per fire visits offsets `(k*s*count) % total`, and those form the multiples of
    `gcd(s*count, total)` — the whole library only when that gcd is `count`. In
    production `s` is the number of factory schedules (15), `count` is 4 and `total` is
    36, so the gcd is 12: three blocks of four out of nine, and **24 of the 36 families
    were unreachable for any given context, permanently**. The candidate store showed it
    plainly — 440 seeded candidates over ~110 generations, and a median context had
    minted 16 distinct families, none more than 20.

    This is the SAME defect the docstring in :func:`generate_batch` describes, one layer
    out: that one selected `templates[0..3]` on every run, this one selects one of three
    fixed blocks. The fix then made *consecutive* generations rotate, and the test written
    for it walks GEN-000, GEN-001, ... — a rotation production never performs. A test that
    strides is in the suite now beside it.

    ``phase`` is :func:`context_rotation_phase` — a per-context constant that separates
    contexts firing the same fire number. It is applied on BOTH paths, including the
    generation-number fallback: a constant shift changes neither the stride nor the residues
    a strided walk can reach, so the fallback's behaviour (and the test pinning it) is
    unaffected, and one code path is worth more than a branch that would need its own."""
    if total <= 0:
        return 0
    if rotation_index is not None:
        step = int(rotation_index)
    else:
        try:
            step = int(str(generation_id).rsplit("-", 1)[1])
        except (ValueError, IndexError):
            step = int(seed)
    return ((step + int(phase)) * max(count, 1)) % total


def generate_batch(
    generation_id: str, *, seed: int, start_index: int = 1, count: int = DEFAULT_BATCH_SIZE,
    symbol: str = "BTCUSDT", timeframe: str = "1d",
    known_rule_hashes: frozenset[str] = frozenset(),
    positioning_eligible: bool = False,
    rotation_index: int | None = None,
    elite_params: Mapping[str, Mapping[str, float]] | None = None,
    venue: str = market_data.BINANCE_FUTURES,
) -> dict[str, Any]:
    """Produce ``count`` validated, distinct candidate specs (source mechanics).

    ``known_rule_hashes`` extends the duplicate guard across the existing pool and
    candidate store, so a batch never re-mints a strategy that already exists.

    ``positioning_eligible`` is passed straight through to :func:`templates_for_timeframe` and
    defaults to False for the reason stated there — unmeasured coverage must not mint a family
    over a window that cannot score it.

    ``rotation_index`` is this context's own fire count (:func:`context_rotation_index`) and
    is what the rotation should step on. It defaults to None — the generation-number
    behaviour — because a caller that does not pass it is a caller with no store to count
    from, and for those callers the generations really are consecutive. `run_factory` passes
    it; see :func:`_rotation_offset` for what the global generation number did instead.

    ``venue`` reaches both the template gate and the minted spec, and it has to be the same
    one in both places: a spec recorded as mined on a venue whose vocabulary it was not
    chosen against is the separation :class:`StrategySpec` carries the field to prevent."""
    rng = random.Random(seed)
    templates = templates_for_timeframe(
        timeframe, symbol=symbol, positioning_eligible=positioning_eligible, venue=venue
    )
    # Which slice of the family list THIS run mints. Without it the picker was
    # ``templates[len(accepted) % len(templates)]``, and since a batch is four specs
    # that selected templates[0..3] on every run ever: 228 of 228 factory candidates
    # came from those four families, while the other sixteen — htf_*, oi_*,
    # funding_fade_*, mean_reversion*, macd_momentum*, bollinger_* — existed in the
    # library and could never be minted at all. Porting a family was therefore
    # invisible work. Stepping by ``count`` per RUN OF THIS CONTEXT walks the whole list
    # in ceil(len/count) runs with no overlap, and stays deterministic. "Of this context"
    # is the correction of 2026-07-31: the step used to be the global generation number,
    # which advances once per fire across every context and so strides — see
    # `_rotation_offset` for what that cost.
    #
    # The phase is the second half of that correction: the cursor decides how far a context
    # has walked, the phase decides where it started, and without one every context walks in
    # step and the whole factory explores `count` families a day. See
    # `context_rotation_phase`. Derived here from this batch's own symbol/timeframe rather
    # than taken as a parameter — the caller would have to compute it from the two arguments
    # it already passes, which is a chance for a caller to pass a phase from a different
    # context than the batch it is minting.
    offset = _rotation_offset(
        generation_id, seed, count, len(templates), rotation_index=rotation_index,
        phase=context_rotation_phase(symbol, timeframe, count=count, total=len(templates)),
    )
    accepted: list[StrategySpec] = []
    accepted_params: dict[str, dict[str, float]] = {}
    validations: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_hashes: set[str] = set(known_rule_hashes)

    attempts = 0
    while len(accepted) < count and attempts < count * _MAX_ATTEMPTS_PER_SPEC:
        attempts += 1
        template = templates[(offset + len(accepted)) % len(templates)]
        # Half the draws around what this family has already learned, half around where it
        # started. The index, not a coin flip, so the split is reproducible — and so the search
        # cannot collapse onto one point however good that point looks. See `elite_base_params`.
        elite = (elite_params or {}).get(template.family)
        centre = (
            {name: float(elite.get(name, template.base_params[name]))
             for name in template.base_params}
            if elite and len(accepted) % 2 == 0 else template.base_params
        )
        params = mutate_params(centre, template.param_space, rng)
        strategy_id = f"S{start_index + len(accepted):03d}"
        spec_dict = build_spec_dict(template, params, strategy_id=strategy_id,
                                    generation_id=generation_id, symbol=symbol, venue=venue)
        try:
            spec = StrategySpec.from_dict(spec_dict)
        except SpecParseError as exc:
            rejected.append({"strategy_family": template.family, "reason": f"parse: {exc}"})
            continue
        verdict = validate_strategy(spec)
        if not verdict["approved_for_backtest"]:
            rejected.append({"strategy_family": template.family, "block_reasons": verdict["block_reasons"]})
            continue
        if spec.strategy_rule_hash in seen_hashes:
            rejected.append({"strategy_family": template.family, "reason": "duplicate_rule_hash"})
            continue
        seen_hashes.add(spec.strategy_rule_hash)
        accepted.append(spec)
        accepted_params[spec.strategy_id] = dict(params)
        validations.append(verdict)

    return {
        "generation_id": generation_id,
        "seed": seed,
        "requested_count": count,
        "accepted_count": len(accepted),
        "specs": [s.to_dict() for s in accepted],
        # The draw each spec came from, keyed by strategy_id. Returned rather than re-derived,
        # for the reason `mint_params` states on the record: reversing a param out of a spec
        # would be a second implementation of every family's entry builder.
        "params": accepted_params,
        "validations": validations,
        "rejected": rejected,
        "batch_complete": len(accepted) == count,
    }


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
    per_bar = (minutes / 1440.0) * FUNDING_INTERVALS_PER_DAY
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
) -> tuple[list[dict[str, Any]], float, float, float, float, int]:
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
                outcomes.append({
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
                })
                position = None
                entry_regime = None
        if position is None:
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
            # (`paper.entry_cost_refusal`, `live_entry`). The backtest scored every signal, so
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
            total_funding_cost_r, uneconomic_entries)


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
    saw least) because `pool.evidence_depth_of` reads it as a calendar span: five
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
        part, part_fees, part_maker, part_slip, part_carry, part_uneconomic = _replay(
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
    rates they never faced, which is the failure `pool.cost_basis_rank` exists to catch after
    the fact — so `backtest_spec` refuses the mismatch at the door instead.
    """

    rows: list[dict[str, Any]]
    candles: list[Mapping[str, Any]]
    funding: list[float]
    funding_source: str
    split: int
    cost: CostModel


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
    bar settles through ``paper.settle_trade_plan`` (pessimistic SL-first, the spec's
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
    scored_bars: list[int] = []
    for frame in frames:
        split = frame.split
        part, part_fees, part_maker, part_slip, part_carry, part_uneconomic = _replay(
            spec, frame.rows[:split], frame.candles[:split],
            cost=cost, funding=frame.funding[:split],
        )
        outcomes.extend(part)
        total_fee_cost_r += part_fees
        total_maker_fee_cost_r += part_maker
        total_slippage_cost_r += part_slip
        total_funding_cost_r += part_carry
        uneconomic_entries += part_uneconomic
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
        # `paper.regime_admits` now reads through the pool entry.
        "per_regime": {
            regime: {"trades": regime_trades[regime], "total_r": round(total, 8)}
            for regime, total in sorted(regime_r.items())
        },
    }

    # Walk-forward-lite: equal-bar slices of the replay; a slice's sign counts only
    # with enough trades. temporal_stability stays None (the source walk-forward
    # module was not ported) — the scorer treats that as absent evidence, not skip.
    # The shallowest leg sets the slice width, so a trade closing late on a longer leg clamps
    # into the last window rather than opening a window the other legs never reached. Bar *i*
    # is the same calendar window on every leg — that is the one-timeframe precondition above,
    # and it is what makes pooling by `closed_at_bar` mean anything.
    window_bars = max(1, min(scored_bars) // BACKTEST_WINDOWS)
    window_r: dict[int, list[float]] = {}
    for outcome in outcomes:
        window_r.setdefault(min(outcome["closed_at_bar"] // window_bars, BACKTEST_WINDOWS - 1), []).append(
            outcome["result_R"]
        )
    counted = [values for values in window_r.values() if len(values) >= MIN_TRADES_PER_WINDOW]
    walk_forward = {
        "walk_forward_pass_rate": (
            sum(1 for values in counted if sum(values) > 0) / len(counted) if counted else None
        ),
        "temporal_stability": None,
        "windows": BACKTEST_WINDOWS,
        "windows_counted": len(counted),
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
        "cost_summary": {
            "total_net_r": total_net_r,
            "total_fee_cost_r": round(total_fee_cost_r, 8),
            # The maker share of the line above. `pool.expectancy_at` re-derives an old
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
                "funding_bps_per_interval": cost.funding_bps_per_interval,
                # Which quality of evidence the carry is: the venue's own settlements over this
                # window, or the modelled base rate because the series was missing.
                "funding_source": funding_source,
            },
        },
        "regime_breakdown": regime_breakdown,
        "walk_forward": walk_forward,
        "holdout": holdout,
        "robustness": robustness,
        # The score's whole meaning, recorded where it is used: the anti-overfit
        # robustness score (C8b), with raw expectancy kept alongside.
        "champion_score": robustness["robustness_score"],
        "score_basis": "robustness_score_v1",
        # PER SYMBOL, and the shallowest leg's — `pool.evidence_depth_of` turns this into a
        # calendar span, and five symbols over 350 days is 350 days of market seen five times,
        # not 1,750 days of it. Summing would tier a pooled candidate as though it had been
        # shown history that does not exist.
        "bars_replayed": min(scored_bars),
        # How many legs are behind every figure above. Absent on evidence minted before pooling,
        # which is exactly what 1 means, so a reader needs no migration to interpret an old row.
        "symbols_replayed": len(frames),
    }


# --- fusion: crossover of two proven lineages ---------------------------------

# How many top-ranked lineages the pair search may draw from. A ceiling, not a
# quota: the caller's ``fusion_pairs`` decides how many children are actually minted.
FUSION_PARENT_POOL = 6


class FusionRefused(ValueError):
    """A parent pair cannot be fused. Carries a stable short ``reason``."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _condition_key(cond: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """Total order over conditions — the dedupe key AND the sort key.

    The rule hash covers the condition *sequence*, so the union must be ordered by
    content alone; that is what makes ``fuse(a, b)`` and ``fuse(b, a)`` the same
    child (and therefore the same hash, caught by the duplicate guard)."""
    value = cond.get("value")
    return (
        str(cond.get("feature")),
        str(cond.get("comparison")),
        str(cond.get("value_from") or ""),
        "" if value is None else repr(value),
    )


# Which validator range bounds each exit parameter. The generation space (`_EXIT_PARAMS`) is
# strictly INSIDE these, which is the fact the clamp below turns on.
_EXIT_LEGAL_RANGE = {
    "stop_atr": STOP_ATR_RANGE,
    "target_atr": TARGET_ATR_RANGE,
    "max_holding_bars": MAX_HOLDING_BARS_RANGE,
}


def _fused_exit_param(name: str, first: float, second: float) -> float | int:
    """The parents' midpoint, held inside the space the factory currently explores.

    Same clamp and same constants as :func:`mutate_params`, so a fused parameter and a
    mutated one can never land in different places.

    **The clamp is a preference applied to legal inputs, and it must never rescue an illegal
    one.** ``_EXIT_PARAMS`` is strictly inside the validator's range — stop_atr [1.2, 2.0]
    against [0.3, 5.0], target_atr [1.6, 8.0] against [0.5, 10.0] — so clamping unconditionally
    would map *every* value to a legal one, and a parent that was never validator-legal (an
    imported spec at stop_atr 12.0, say) would be laundered into a valid child. That would
    silently delete the refusal `fuse_specs` documents and
    ``test_fusion_validates_the_child_even_when_a_parent_never_was`` pins.

    So an out-of-range PARENT disables the clamp for that parameter and the midpoint goes
    through untouched, where `validate_strategy` refuses it. Two legal parents, one merely
    minted under an older generation space, clamp. The two bounds answer different questions —
    *"is this a legal strategy"* and *"is this what the factory currently explores"* — and only
    the second is a preference.

    **The clamp is the UNION of the generation spaces, not `_EXIT_PARAMS` alone**, because since
    2026-08-04 there is more than one: `_FADE_EXIT_PARAMS` holds fade families to a shorter hold
    (4-16) than the trend space (12-48). Clamping a fused child to the trend space would take the
    midpoint of two fade parents holding 6 and 8 bars and push it to 12 — silently restoring the
    geometry the fade space exists to avoid, on exactly the children of the families it applies
    to. The union answers the question the docstring above states ("is this what the factory
    currently explores") correctly once the answer is a set of spaces rather than one.

    It changes nothing for any pre-existing case: the unions for `stop_atr` and `target_atr` are
    identical to `_EXIT_PARAMS` (the fade space is strictly inside them), and for
    `max_holding_bars` only the floor moves, 12 -> 4, which cannot bind on two trend parents
    since the midpoint of two values at or above 12 is at or above 12. The one behaviour that
    does move is an imported parent below 12 bars, which used to be lifted to 12 and now passes
    through — correctly, because 4-16 is now a region the factory does mint.
    """
    spec = _EXIT_PARAMS[name]
    low, high = _EXIT_LEGAL_RANGE[name]
    lo = min(space[name].lo for space in _GENERATION_SPACES)
    hi = max(space[name].hi for space in _GENERATION_SPACES)
    midpoint = (first + second) / 2
    if low <= first <= high and low <= second <= high:
        midpoint = max(lo, min(hi, midpoint))
    return int(round(midpoint)) if spec.integer else round(midpoint, 4)


def fuse_specs(
    first: StrategySpec, second: StrategySpec, *, strategy_id: str, generation_id: str,
) -> StrategySpec:
    """Cross two parents into a child that enters only where BOTH would.

    Entry conditions are the **deduplicated union** under AND, so the child is by
    construction at least as selective as either parent — a crossover can never
    loosen an entry. Measured over all 394 parent-child pairs in the store, that costs
    roughly half the evidence: a child closes **0.51x** its parent median's trades, and
    past a point it closes too few for its holdout to be judged at all. Hence the second
    condition bound, ``MAX_FUSION_ENTRY_CONDITIONS``, which refuses the band where that
    has never once come out judgeable. Exits are the midpoint of the parents'; risk takes
    the stricter (minimum) cap. Everything the parents must agree on (schema, **venue**,
    direction, timeframe, symbol scope, stop model, AND-operator) is a fail-closed
    precondition, not something to reconcile: unioning an OR parent's conditions
    into an AND would silently change what that parent meant.

    The child carries the parents' venue, which is the whole reason they must share one.
    This is a minting path like ``build_spec_dict`` and it was silent about the venue until
    2026-08-03, so every fused child read as ``binance_futures`` whatever it came from —
    the separation `StrategySpec.venue` exists to make impossible, reintroduced by the one
    caller that built a spec dict without it.

    The child is structurally parsed and put through the same ``validate_strategy``
    as any generated spec; a blend that lands outside the **validator's** bounds (an
    R:R below the floor, say) refuses rather than being clamped into range.

    **The GENERATION space is the other kind of bound, and it clamps.** ``_EXIT_PARAMS``
    is what `mutate_params` samples from and what `generate_batch` therefore explores;
    `validate_strategy` is what a spec must satisfy to be legal at all. Averaging two
    parents that are both inside an interval lands inside it — the midpoint of a convex
    set is in the set — so the only way a child escapes is a **parent minted under an
    older space**. Measured 2026-08-02, the day after `stop_atr`'s floor moved 0.8 → 1.2
    (#420): 43% of the candidate store predates the move, and 20 of that day's 60 fused
    children landed below the new floor, one of them (GEN-738 at 1.1700) reaching the
    promotable board.

    Clamped rather than refused, and the asymmetry with the paragraph above is the point.
    A validator breach means the child is not a legal strategy; an out-of-space parent
    means the child is legal but outside what the factory currently chooses to explore.
    Refusing the second would block fusion against 43% of the store to enforce an
    efficiency rule the promotion door already backstops on evidence (cost basis, ROBUST,
    holdout, positive expectancy at current rates). Clamping is also what `mutate_params`
    does with the same constants, which keeps one answer to "where may a minted parameter
    land" instead of two."""
    if first.schema_version != second.schema_version:
        raise FusionRefused("schema_version_mismatch")
    if first.venue != second.venue:
        # Beside `schema_version` rather than beside `symbol_scope`, because this is not two
        # rule sets that fail to line up — it is two rule sets judged against DIFFERENT
        # vocabularies, so merging their conditions produces a spec neither parent's venue
        # was asked about. It passed until now only because hyperliquid's vocabulary happens
        # to be a subset of binance's; a venue with a feed binance lacks would have made the
        # child unvalidatable on the venue it claimed. Refused rather than resolved: which
        # venue a cross-venue child belongs to is a question with no correct answer.
        raise FusionRefused("venue_mismatch")
    if first.direction != second.direction:
        raise FusionRefused("direction_mismatch")
    if first.timeframe != second.timeframe:
        raise FusionRefused("timeframe_mismatch")
    if sorted(first.symbol_scope) != sorted(second.symbol_scope):
        raise FusionRefused("symbol_scope_mismatch")
    if first.exit_rules.stop_model != second.exit_rules.stop_model:
        raise FusionRefused("stop_model_mismatch")
    if "OR" in (first.entry_rules.operator, second.entry_rules.operator):
        raise FusionRefused("non_and_parent")

    merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for condition in (*first.entry_rules.conditions, *second.entry_rules.conditions):
        as_dict = condition.to_dict()
        merged.setdefault(_condition_key(as_dict), as_dict)
    conditions = [merged[key] for key in sorted(merged)]
    if len(conditions) > MAX_ENTRY_CONDITIONS:
        raise FusionRefused("too_many_conditions")
    # Both branches fire, and they are kept apart because they refuse for unrelated reasons: the
    # one above is source S3's legality bound, this one is evidence judgeability (see
    # `MAX_FUSION_ENTRY_CONDITIONS`). A single check would make the tighter number look like a
    # revision of the validator, and the next reader would raise it back toward 8 to "match".
    if len(conditions) > MAX_FUSION_ENTRY_CONDITIONS:
        raise FusionRefused("holdout_unjudgeable")

    # "breakout+mean_reversion", stable and order-independent; a shared component
    # collapses so re-fusing a lineage does not grow the name without adding meaning.
    families = sorted({*first.strategy_family.split("+"), *second.strategy_family.split("+")})

    spec_dict = {
        "schema_version": first.schema_version,
        "strategy_id": strategy_id,
        "strategy_version": "1.0",
        "generation_id": generation_id,
        "strategy_family": "+".join(families),
        "status": "GENERATED",
        "symbol_scope": sorted(first.symbol_scope),
        "timeframe": first.timeframe,
        "direction": first.direction.value,
        "entry_rules": {"operator": "AND", "conditions": conditions},
        "exit_rules": {
            "stop_model": first.exit_rules.stop_model,
            "stop_atr": _fused_exit_param(
                "stop_atr", first.exit_rules.stop_atr, second.exit_rules.stop_atr
            ),
            "target_atr": _fused_exit_param(
                "target_atr", first.exit_rules.target_atr, second.exit_rules.target_atr
            ),
            "max_holding_bars": _fused_exit_param(
                "max_holding_bars",
                first.exit_rules.max_holding_bars,
                second.exit_rules.max_holding_bars,
            ),
        },
        "risk_constraints": {
            "max_risk_per_trade_R": min(
                first.risk_constraints.max_risk_per_trade_R,
                second.risk_constraints.max_risk_per_trade_R,
            ),
        },
        "created_by": "mvp_factory_fusion",
        # Both parents' venue — they are equal or this never got here. Recorded rather than
        # left to `StrategySpec`'s default, which would label every fused child
        # `binance_futures` no matter what it was mined from: the second minting path, and
        # the one #463 did not close when it fixed `build_spec_dict`.
        "venue": first.venue,
    }
    try:
        child = StrategySpec.from_dict(spec_dict)
    except SpecParseError as exc:
        raise FusionRefused(f"parse: {exc}") from exc
    verdict = validate_strategy(child)
    if not verdict["approved_for_backtest"]:
        raise FusionRefused(f"validator: {','.join(verdict['block_reasons'])}")
    return child


def carries_retired_family(record: Mapping[str, Any]) -> bool:
    """Does any component of this row's family name name a family that left the rotation?

    Component-wise, because a fused family is ``"a+b"``: a child of a retired parent carries
    that parent's entry conditions, so treating only the exact name as retired lets the
    lineage keep breeding under a compound name.
    """
    spec = record.get("strategy_spec")
    family = spec.get("strategy_family") if isinstance(spec, Mapping) else None
    if not isinstance(family, str):
        return False
    return any(part in RETIRED_FAMILIES for part in family.split("+"))


# --- the holdout has to reach parent SELECTION, not just the child's own gate -----------------
#
# `champion_score` is what ranks the breeding pool, and it is `robustness.score_robustness` —
# sample adequacy, temporal consistency, regime breadth, parsimony, cost survival. By design it
# carries **no out-of-sample term at all**: holdout "never moves the score — it gates the ROBUST
# verdict" (`robustness.score_robustness`). So until now the holdout could refuse a candidate at
# the promotion door and still have no say in which lineages got to breed. Measured 2026-08-04
# over the 961 stored rows with a judgeable holdout, `champion_score` correlates with holdout
# expectancy at only **r = +0.37**, and its top decile has a median holdout of **-0.161R against
# -0.179R for all of them** — a rank order that barely distinguishes the population it sorts.
#
# **`holdout_status` is the wrong authority to filter on, which is the trap here.** Of the 1,366
# rows eligible to parent today, **0 read CONFIRMED** — requiring it would take fusion from 40
# fusable buckets to 0 and end the crossover path outright. And 854 read INSUFFICIENT, mostly for
# a missing `stdev_r`, which is a schema vintage rather than a shallow sample (`holdout_status`
# documents that cost deliberately). Filtering on the status would therefore select by when a row
# was minted. The two holdout FACTS a row of any vintage carries are its depth and its return,
# and those are what this predicate reads.
#
# **Depth alone is not enough, and measuring it is what settles the shape.** Filtering to a
# judgeable holdout while still ranking by `champion_score` makes the selected pool *worse* out
# of sample — median holdout of the chosen parents moves -0.098R -> **-0.164R** — because within
# the judgeable subset the score is mildly anti-selective. Adding the non-negative bit and
# leaving the ranking alone moves it to **+0.091R** with every selected parent non-negative.
#
# **A one-bit filter at zero, deliberately, rather than ranking by holdout magnitude.** The unit
# of independence here is the market period, not the trade, and this store carries roughly ten of
# them — so ordering lineages by the SIZE of a holdout edge would mine a quantity the repo has
# already established is not resolvable below 0.05R. What this predicate claims is only the
# coarse thing the evidence supports: a lineage that demonstrably lost on unseen bars should not
# breed. Ranking stays `champion_score`, so the pool keeps one ranking currency.
#
# **What it costs, stated because it is not free.** On today's store 3 of the 15 live
# (symbol, timeframe) contexts — BTCUSDT 1d, SOLUSDT 1d, SOLUSDT 1h — lose every fusable bucket,
# and the 1d tier halves. Those fires mint seeded candidates only until rows carrying a judgeable
# non-negative holdout accumulate there, which is `_fuse_batch`'s documented dry-bucket behaviour
# rather than a new failure mode. Re-measure before tightening further: with the child-side bar
# (`FUSION_IMPROVEMENT_METRICS`) also reducing crossover supply, the parent pool now refills from
# the seeded rotation more slowly than it did.
def holdout_permits_parenting(record: Mapping[str, Any]) -> bool:
    """May this row breed, on its out-of-sample evidence alone?

    Reads the two facts a holdout block of any vintage carries: enough closed trades to be
    judged at all (``robustness.MIN_HOLDOUT_TRADES``, the same floor the promotion door uses),
    and a return that is not negative.

    Fail-closed on absence: no holdout block, an unreadable ``closed_count``/``expectancy``, or
    a block predating holdouts entirely means no out-of-sample evidence exists, which is not
    the same as passing — the rule `robustness.holdout_status` applies for UNCONFIRMED, applied
    here to the question of breeding rather than of verdict."""
    holdout = (record.get("backtest_evidence") or {}).get("holdout")
    if not isinstance(holdout, Mapping):
        return False
    closed, expectancy = holdout.get("closed_count"), holdout.get("expectancy")
    if isinstance(closed, bool) or not isinstance(closed, (int, float)):
        return False
    if isinstance(expectancy, bool) or not isinstance(expectancy, (int, float)):
        return False
    return closed >= MIN_HOLDOUT_TRADES and expectancy >= 0


def rank_fusion_parents(
    existing_candidates: list[Mapping[str, Any]], *, top_n: int = FUSION_PARENT_POOL,
) -> list[dict[str, Any]]:
    """The best-scoring distinct lineages available as parents, deterministically.

    Only rows carrying a numeric ``champion_score`` and a parseable spec can parent
    — an unscored or legacy-shaped row has no evidence to pass on — and only rows whose
    out-of-sample tail both can be judged and did not lose (:func:`holdout_permits_parenting`,
    which carries the measurement). Ordering is (score desc, candidate_id asc) so a tie never
    depends on file order, and a lineage appears once however many times it was appended
    (latest-wins).

    **"Once per lineage" was keyed on the wrong identity, and a re-score is what
    exposed it.** ``candidate_id`` derives from (generation, rules, *evidence window*)
    — see :func:`pool.derive_candidate_id` — so re-scoring a spec at a new window mints
    a DIFFERENT id for the SAME strategy, and both rows then entered the pool as if
    they were two lineages. `scripts/rescore_stale_holdout_candidates.py` appended 336
    such rows on 2026-08-04; measured on the store that day, **348 rule hashes appeared
    in more than one row (709 rows, up to 3 per hash)**, and rebuilding
    :func:`fusion_parent_buckets` over the live contexts put twins in 20 of 27 fusable
    buckets, occupying 36 of their top slots. Every twin pair then dies in
    ``_fuse_batch`` as ``duplicate_rule_hash``, and ``(twin_a, X)`` and ``(twin_b, X)``
    propose the identical child twice — so the bucket's parent diversity and its pair
    draws are both silently halved.

    ``strategy_rule_hash`` is the identity that answers "is this the same strategy",
    which is the question this dedup is asking; the id falls back to ``candidate_id``
    only for a row carrying no usable hash, so hash-less legacy rows collapse into each
    other rather than into one bucket.

    **Latest-wins is kept deliberately, rather than best-score-wins.** Two rows sharing
    a rule hash are one strategy measured over two windows, and taking the higher score
    would pick whichever window happened to flatter it — the max-of-many-draws selection
    this store is already full of (see ``robustness.MIN_HOLDOUT_TRADES``). File order
    puts the freshest evidence last, and fresh beats flattering.

    **A retired family may not parent, and that was leaking until 2026-08-04.**
    ``RETIRED_FAMILIES`` is enforced by de-listing from ``TEMPLATES``, which stops the
    DIRECT mint path and nothing else — fusion draws its parents from the candidate STORE,
    which still holds every row the family produced before it was retired. Measured on the
    08:09Z fire, the first one after `macd_momentum_*` and `xs_momentum_*` were retired:
    three of eighty children carried a retired parent, one of them
    (``macd_momentum_short+xs_momentum_short``) built from two retired families and nothing
    else. Their holdouts read -0.467R, -0.471R and -0.122R, which is what the retirement
    said they would.

    This is the leak that makes every retirement note in this file false as written — each
    says re-listing one line is the whole of re-enabling, and the mirror of that claim is
    that de-listing is the whole of disabling. It was not. 229 of the store's 1,556 rows
    (14.7%) carry a retired component today, so this narrows the parent pool measurably
    rather than cosmetically; ``_fuse_batch`` simply draws fewer pairs, which is its
    documented behaviour when a bucket runs dry.

    Filtered HERE rather than in ``fusion_parent_buckets`` because this is the function that
    answers *"which stored rows may parent"* — the bucketing below is about compatibility,
    and a second eligibility rule there would split one question across two places."""
    best: dict[str, dict[str, Any]] = {}
    for record in existing_candidates:
        score = record.get("champion_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            continue
        if not isinstance(record.get("strategy_spec"), Mapping):
            continue
        if carries_retired_family(record):
            continue
        # Applied BEFORE the latest-wins collapse below, so a lineage is judged on the row being
        # considered rather than on whichever of its re-scores happened to carry a holdout.
        if not holdout_permits_parenting(record):
            continue
        cid = candidate_id(record)
        rule_hash = record.get("strategy_rule_hash")
        key = rule_hash if isinstance(rule_hash, str) and rule_hash else cid
        best[key] = {**record, "candidate_id": cid}
    ranked = sorted(best.values(), key=lambda r: (-float(r["champion_score"]), r["candidate_id"]))
    return ranked[:top_n]


def _fusion_bucket_key(record: Mapping[str, Any]) -> tuple | None:
    """The context two parents must already agree on, or None if unreadable.

    Exactly :func:`fuse_specs`' preconditions — schema, direction, timeframe, symbol
    scope, stop model — because those are not differences to reconcile but the
    definition of "these two describe the same trade"."""
    spec = record.get("strategy_spec")
    if not isinstance(spec, Mapping):
        return None
    scope = spec.get("symbol_scope")
    if not isinstance(scope, (list, tuple)):
        return None
    exits = spec.get("exit_rules")
    return (
        spec.get("schema_version"), spec.get("direction"), spec.get("timeframe"),
        tuple(sorted(str(s) for s in scope)),
        (exits or {}).get("stop_model") if isinstance(exits, Mapping) else None,
    )


def fusion_parent_buckets(
    existing_candidates: list[Mapping[str, Any]], *, symbol: str, timeframe: str,
    per_bucket: int = FUSION_PARENT_POOL,
) -> list[list[dict[str, Any]]]:
    """Fusable parent groups, best bucket first, best parent first within each.

    Ranking parents GLOBALLY and pairing the top N was the wired-but-inert version of
    this: a crossover is only defined inside one (direction, timeframe, symbol_scope,
    …) context, and the global leaders are spread across ~40 such contexts, so nearly
    every pair drawn from them disagreed on something structural. The first live day
    showed it exactly — 240 pairs attempted, 240 refused, every one on
    direction/symbol/timeframe mismatch and not one on merit.

    Grouping first makes compatibility structural: every pair a bucket yields already
    agrees, so the refusals that remain are the ones worth reading (duplicate rules, a
    child that trades nothing). Buckets of one are dropped — there is no pair to make —
    and buckets are ordered by their best parent so the strongest context fuses first.

    Buckets are additionally confined to the context being MINED (``symbol`` /
    ``timeframe``), because a fused child is backtested on the caller's snapshot: a
    child inheriting an ETH 4h scope but scored on a BTC 1h replay would be stored
    with evidence that never described it. Global ranking hid that — compatible pairs
    were so rare it effectively never arose — so making pairs common has to close it
    in the same change."""
    buckets: dict[tuple, list[dict[str, Any]]] = {}
    for record in rank_fusion_parents(existing_candidates, top_n=len(existing_candidates)):
        key = _fusion_bucket_key(record)
        if key is None:
            continue
        _schema, _direction, bucket_timeframe, scope, _stop = key
        if bucket_timeframe != timeframe or symbol not in scope:
            continue  # not the context this run can produce honest evidence for
        buckets.setdefault(key, []).append(record)  # already score-ordered
    ordered = [members[:per_bucket] for members in buckets.values() if len(members) >= 2]
    ordered.sort(key=lambda members: (-float(members[0]["champion_score"]), members[0]["candidate_id"]))
    return ordered


# --- the improvement bar: a child is stored only when it beats the parents it came from -------
#
# Until 2026-08-04 the only thing a child had to do to become a candidate was close one trade.
# Measured over the 447 stored crossover children whose parents both resolve, that admitted a
# population where **9.8% out-score their best parent and 1.1% close more trades** (median -67),
# and 101 of the children scoring at or below their best parent went on to parent a further
# generation themselves. Fusion was accumulation, not selection: the pressure sat entirely at the
# promotion door, which is lineage-blind, so a child strictly worse than both parents on every
# measure was stored, ranked, and bred from exactly like one that improved on them.
#
# **The comparison has to be re-measured, and this is the reason it could not simply be read off
# the store.** A parent is minted in an earlier fire and carries the evidence of an earlier candle
# window: of the 894 parent/child evidence links in the store, **890 are on different windows and
# 0 on the same one**. Comparing a child's stored numbers against its parents' would therefore
# score the market's drift between two windows and call the result lineage improvement. Both
# sides are replayed on the SAME snapshot here, which is also why the replays are memoised — a
# bucket of ``FUSION_PARENT_POOL`` parents offers 15 pairs and would otherwise replay each parent
# up to 5 times for one answer that cannot change between them.
#
# **Two legs, deliberately asymmetric.** ``expectancy`` must strictly improve: it is the return
# per trade, and a crossover that does not raise it has no reason to exist when keeping the better
# parent was free. ``champion_score`` is only required not to REGRESS, because it is
# `robustness.score_robustness` — sample adequacy, temporal consistency, regime breadth,
# parsimony, cost survival — and 45% of its weight (``sample_adequacy`` + ``parameter_parsimony``)
# moves against a fused child by construction: the AND-union closes 0.51x its parent median's
# trades while carrying more conditions. Requiring it to strictly improve would refuse on the
# arithmetic of fusion rather than on the child's merit. Requiring it not to fall stops the one
# case the expectancy leg cannot see alone — a higher return read off a handful of trades — since
# that is precisely what drives ``sample_adequacy`` down.
#
# Compared against the MAXIMUM over the parents on each metric independently, not against the
# better parent picked once: the question this gate answers is "was fusing these two worth more
# than keeping either of them", and a child that beats the weaker parent while losing to the
# stronger one has not answered it.
FUSION_IMPROVEMENT_METRICS = ("expectancy", "champion_score")


def _fusion_improvement(
    child: Mapping[str, Any], parents: Sequence[Mapping[str, Any]],
) -> str | None:
    """``None`` if the child clears the bar above, else a stable refusal reason.

    Every reading is taken from evidence replayed on one snapshot — the caller's job, since
    this function cannot see which window produced what it is handed.

    Fail-closed on an unreadable metric: a missing or non-numeric ``expectancy`` /
    ``champion_score`` on either side means the comparison was never made, which is not the
    same as passing it, and silently treating an absent number as zero would admit exactly the
    children whose evidence is malformed."""
    def readings(evidence: Mapping[str, Any]) -> list[float] | None:
        out: list[float] = []
        for name in FUSION_IMPROVEMENT_METRICS:
            value = evidence.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            out.append(float(value))
        return out

    child_reading = readings(child)
    parent_readings = [readings(evidence) for evidence in parents]
    if child_reading is None or not parent_readings or any(r is None for r in parent_readings):
        return "improvement_unmeasurable"
    child_expectancy, child_score = child_reading
    if child_expectancy <= max(r[0] for r in parent_readings):
        return "no_expectancy_gain"
    if child_score < max(r[1] for r in parent_readings):
        return "champion_score_regression"
    return None


def _fuse_batch(
    buckets: list[list[Mapping[str, Any]]], snapshot: Mapping[str, Any], *, generation_id: str,
    start_index: int, pairs: int, seen_hashes: set[str], evidence_sha: str, now: str,
    frame: ReplayFrame | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fuse parents pairwise, bucket by bucket, until ``pairs`` children carry evidence.

    Each bucket is a set of lineages that already agree on schema/direction/timeframe/
    symbol/stop model (see :func:`fusion_parent_buckets`), so every pair offered here
    is structurally fusable and a refusal means something real.

    Children are backtested on their own — a crossover inherits its parents' rules,
    never their evidence, so a child that overfits cannot ride a parent's score.
    A child that closed **no** trades is refused rather than stored: an unsatisfiable
    union (``rsi <= 30`` from one parent, ``rsi >= 70`` from the other) parses and
    validates perfectly well and would otherwise sit in the store as a scored
    candidate that can never trade.

    A child that traded but did not IMPROVE on its parents is refused the same way — see
    :data:`FUSION_IMPROVEMENT_METRICS` for the bar and why the parents are replayed here
    rather than read from their stored rows. That refusal is ordered last because it is the
    only one costing a replay, so the cheap structural checks drop the pairs that would
    waste it; and it costs no mint, because the pair stream simply draws again."""
    minted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    replayed: dict[str, dict[str, Any]] = {}

    def on_this_window(spec: StrategySpec) -> dict[str, Any]:
        """This parent's evidence on the CHILD's snapshot, replayed once per fire."""
        cached = replayed.get(spec.strategy_rule_hash)
        if cached is None:
            cached = replayed[spec.strategy_rule_hash] = backtest_spec(spec, snapshot, frame=frame)
        return cached

    pair_stream = (pair for bucket in buckets for pair in combinations(bucket, 2))
    for left, right in pair_stream:
        if len(minted) >= pairs:
            break
        parent_ids = sorted([left["candidate_id"], right["candidate_id"]])
        try:
            first = StrategySpec.from_dict(dict(left["strategy_spec"]))
            second = StrategySpec.from_dict(dict(right["strategy_spec"]))
            child = fuse_specs(
                first, second,
                strategy_id=f"S{start_index + len(minted):03d}",
                generation_id=generation_id,
            )
        except (FusionRefused, SpecParseError) as exc:
            rejected.append({"parent_candidate_ids": parent_ids,
                             "reason": getattr(exc, "reason", f"parse: {exc}")})
            continue
        if child.strategy_rule_hash in seen_hashes:
            rejected.append({"parent_candidate_ids": parent_ids, "reason": "duplicate_rule_hash"})
            continue
        evidence = backtest_spec(child, snapshot, frame=frame)
        if not evidence["closed_count"]:
            rejected.append({"parent_candidate_ids": parent_ids, "reason": "no_trades"})
            continue
        parent_evidence = {left["candidate_id"]: on_this_window(first),
                           right["candidate_id"]: on_this_window(second)}
        refusal = _fusion_improvement(evidence, [parent_evidence[pid] for pid in parent_ids])
        if refusal is not None:
            rejected.append({"parent_candidate_ids": parent_ids, "reason": refusal})
            continue
        seen_hashes.add(child.strategy_rule_hash)
        record = {
            "strategy_id": child.strategy_id,
            "strategy_rule_hash": child.strategy_rule_hash,
            "generation_id": generation_id,
            "status": "BACKTESTED",
            "champion_score": evidence["champion_score"],
            "strategy_spec": child.to_dict(),
            "backtest_evidence": evidence,
            "evidence_input_sha256": evidence_sha,
            "provenance": "mvp_factory_fusion",
            "derivation_type": "crossover",
            "parent_candidate_ids": parent_ids,
            # What the child had to beat, on the window it was beaten on. Recorded because the
            # parents' own rows carry a DIFFERENT window's numbers, so nothing downstream could
            # reconstruct this comparison from the store — and without it "the gate passed" is
            # an assertion rather than evidence.
            "fusion_improvement": {
                "measured_on_evidence_sha256": evidence_sha,
                "child": {m: evidence[m] for m in FUSION_IMPROVEMENT_METRICS},
                "parents": {pid: {m: parent_evidence[pid][m] for m in FUSION_IMPROVEMENT_METRICS}
                            for pid in parent_ids},
            },
            "created_at_utc": now,
        }
        record["candidate_id"] = derive_candidate_id(record)
        minted.append(record)
    return minted, rejected


def next_generation_id(existing: list[Mapping[str, Any]]) -> str:
    """GEN-%03d after the highest generation number seen in the given records."""
    highest = 0
    for record in existing:
        for value in (record.get("generation_id"),
                      (record.get("strategy_spec") or {}).get("generation_id")
                      if isinstance(record.get("strategy_spec"), Mapping) else None):
            if isinstance(value, str) and value.startswith("GEN-"):
                try:
                    highest = max(highest, int(value.split("-", 1)[1]))
                except ValueError:
                    continue
    return f"GEN-{highest + 1:03d}"


def run_factory(
    snapshot: Mapping[str, Any],
    *,
    active_pool: Mapping[str, Any],
    existing_candidates: list[Mapping[str, Any]],
    now: str,
    count: int = DEFAULT_BATCH_SIZE,
    fusion_pairs: int = 0,
    positioning_eligible: bool = False,
) -> dict[str, Any]:
    """One factory run: generate → backtest → candidate records. Pure (no I/O).

    ``positioning_eligible`` is the caller's measurement of whether the positioning store covers
    the replay window; it reaches :func:`templates_for_timeframe` unchanged. Kept as a parameter
    rather than read here because this function is pure — the scheduler reads the store.

    The seed derives from the candle window's content hash — reproducible from the
    recorded inputs, no wall-clock randomness. Candidate records carry the spec, its
    backtest evidence, the generation lineage, and provenance ``mvp_factory``; the
    caller appends them to the candidates store. Nothing here touches the pool.

    ``fusion_pairs`` (default 0 — no behaviour change) additionally crosses up to
    that many pairs drawn from the best-scoring **already durable** lineages in
    ``existing_candidates``. Parents are deliberately never taken from the batch
    being minted: the store requires a parent to be durable before the child citing
    it is appended, and a same-run parent has no independent evidence anyway."""
    pool_entries = list(active_pool.get("active_strategies") or [])
    known_hashes = frozenset(
        h for h in (
            *(e.get("strategy_rule_hash") for e in pool_entries),
            *(c.get("strategy_rule_hash") for c in existing_candidates),
        ) if isinstance(h, str) and h
    )
    generation_id = next_generation_id([*pool_entries, *existing_candidates])
    candles_sha = integrity.sha256_record({"candles": snapshot.get("candles") or []})
    seed = int(candles_sha.split(":", 1)[1][:8], 16)

    symbol = str(snapshot.get("symbol") or "BTCUSDT")
    timeframe = str(snapshot.get("timeframe") or "1d")
    # Read off the snapshot for the same reason as the two above: it is a property of the
    # data this run is mining, not a claim by whoever called. It was briefly a parameter,
    # which meant the venue an env var selected at collection and the venue the factory
    # recorded were two independent values that nothing checked against each other — the
    # scheduler passed none, so a hyperliquid collection minted binance_futures specs.
    # Absent means the snapshot predates the field, which proves binance_futures: the
    # `StrategySpec.from_dict` migration fact, one layer up.
    venue = str(snapshot.get("venue") or market_data.BINANCE_FUTURES)
    # What each family has already learned in THIS context. Empty on a store whose candidates
    # predate `mint_params`, which is every one of them today — so the first generation after
    # this lands still draws around the template base, and the one after it has something to
    # move toward. Same shape as every other "record it before you can use it" step here.
    elite_centres = {
        template.family: elite_base_params(
            existing_candidates, family=template.family, symbol=symbol, timeframe=timeframe,
            fallback=template.base_params,
        )
        for template in templates_for_timeframe(
            timeframe, positioning_eligible=positioning_eligible, venue=venue
        )
    }
    batch = generate_batch(
        generation_id, seed=seed, count=count,
        symbol=symbol,
        timeframe=timeframe,
        elite_params=elite_centres,
        known_rule_hashes=known_hashes,
        positioning_eligible=positioning_eligible,
        venue=venue,
        # Counted from the store this function was already given — the rotation steps on
        # THIS context's fire count, not on the global generation number.
        rotation_index=context_rotation_index(
            existing_candidates, symbol=symbol, timeframe=timeframe,
        ),
    )

    # Built once for the whole run. Features, candles and carry are properties of the market and
    # the calendar, not of any spec, and `build_feature_rows` alone is 6.0s at the 48,000-bar 15m
    # window — so rebuilding it per candidate cost ~30s a fire on a sequential scheduler.
    frame = build_replay_frame(snapshot)

    candidates: list[dict[str, Any]] = []
    starved_specs: list[dict[str, Any]] = []
    for spec_dict in batch["specs"]:
        spec = StrategySpec.from_dict(spec_dict)
        # Before scoring, because scoring it is the waste: a spec naming a feature these rows
        # never supply cannot enter here, and evidence saying otherwise would be evidence for a
        # trade this runtime cannot reproduce. See `unsuppliable_features`.
        starved = unsuppliable_features(spec, frame.rows)
        if starved:
            starved_specs.append({"strategy_family": spec.strategy_family,
                                  "reason": UNSUPPLIABLE_FEATURE, "features": starved})
            continue
        evidence = backtest_spec(spec, snapshot, frame=frame)
        record = {
            "strategy_id": spec.strategy_id,
            "strategy_rule_hash": spec.strategy_rule_hash,
            "generation_id": generation_id,
            "status": "BACKTESTED",
            "champion_score": evidence["champion_score"],
            "strategy_spec": spec.to_dict(),
            "backtest_evidence": evidence,
            "evidence_input_sha256": candles_sha,
            "provenance": "mvp_factory",
            "derivation_type": "seeded_template",
            # What this candidate was drawn from, recorded because nothing did and the search
            # therefore could not learn: `elite_base_params` reads exactly this. Stored on the
            # record rather than re-derived from the spec — the mapping from a param name to the
            # condition it lands in lives in the family's own entry builder, so reversing it
            # would be a second implementation of every template, silently wrong the first time
            # one changed.
            "mint_params": batch["params"].get(spec.strategy_id, {}),
            "parent_candidate_ids": [],
            "created_at_utc": now,
        }
        # Stored id == derived id: strategy_id restarts every generation, so the
        # lineage-derived candidate_id is the only key promotions may use.
        record["candidate_id"] = derive_candidate_id(record)
        candidates.append(record)

    fused: list[dict[str, Any]] = []
    fusion_rejected: list[dict[str, Any]] = []
    if fusion_pairs > 0:
        fused, fusion_rejected = _fuse_batch(
            fusion_parent_buckets(
                existing_candidates,
                symbol=str(snapshot.get("symbol") or ""),
                timeframe=str(snapshot.get("timeframe") or ""),
            ),
            snapshot,
            generation_id=generation_id,
            start_index=len(candidates) + 1,
            pairs=fusion_pairs,
            seen_hashes={*known_hashes, *(c["strategy_rule_hash"] for c in candidates)},
            evidence_sha=candles_sha,
            frame=frame,
            now=now,
        )

    return {
        "factory_version": "crypto_factory.v0.1",
        "generation_id": generation_id,
        "seed": seed,
        "requested_count": batch["requested_count"],
        "accepted_count": batch["accepted_count"],
        # Mint-time refusals and score-time ones together: a caller reading "why did this fire
        # produce so few candidates" must not have to know which loop dropped them. Kept as a
        # separate key rather than merged into `batch["rejected"]`, because that list is the
        # generator's own record and this refusal happens after it, with facts it cannot see.
        "rejected": [*batch["rejected"], *starved_specs],
        "candidates": [*candidates, *fused],
        "fused_count": len(fused),
        "fusion_rejected": fusion_rejected,
        "evidence_input_sha256": candles_sha,
        "created_at": now,
    }
