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

import random
from dataclasses import dataclass, field, replace
from itertools import combinations
from typing import Any, Callable, Mapping

from runtime.read_only_kernel import integrity

from . import features, market_data
from .cost import (
    FUNDING_INTERVALS_PER_DAY,
    FUNDING_SOURCE_FALLBACK,
    FUNDING_SOURCE_VENUE,
    CostModel,
    apply_cost_model,
)
from .feedback import summarize_outcomes
from .features import build_feature_rows
from .paper import settle_trade_plan
from .pool import candidate_id, derive_candidate_id
from .robustness import score_robustness
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


_EXIT_PARAMS = {
    "stop_atr": ParamSpec(0.8, 2.0),
    "target_atr": ParamSpec(1.6, 8.0),
    "max_holding_bars": ParamSpec(12, 48, integer=True),
}
_EXIT_BASE = {"stop_atr": 1.2, "target_atr": 3.0, "max_holding_bars": 24}


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
    return [
        {"feature": "htf_market_regime", "comparison": "==", "value": "TREND_UP"},
        {"feature": "close", "comparison": ">", "value_from": "ma20"},
        {"feature": "adx", "comparison": ">=", "value": p["adx_min"]},
    ]


def _htf_trend_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "htf_market_regime", "comparison": "==", "value": "TREND_DOWN"},
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
    # The opposite premise, and deliberately kept: bands compressed to the low end of their
    # own range, price pushing out of the upper one. It is `bollinger_breakout` with the
    # compression made an explicit precondition rather than left to chance, which is a
    # different claim about WHEN the breakout is worth taking.
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
                     {"rsi_max": ParamSpec(20.0, 40.0), **_EXIT_PARAMS},
                     {"rsi_max": 30.0, **_EXIT_BASE}, _mean_reversion_long_entry),
    StrategyTemplate("mean_reversion_short", "short", "1h",
                     {"rsi_min": ParamSpec(60.0, 80.0), **_EXIT_PARAMS},
                     {"rsi_min": 70.0, **_EXIT_BASE}, _mean_reversion_short_entry),
    StrategyTemplate("macd_momentum", "long", "1h",
                     {"adx_min": ParamSpec(15.0, 30.0), **_EXIT_PARAMS},
                     {"adx_min": 20.0, **_EXIT_BASE}, _macd_momentum_entry),
    StrategyTemplate("macd_momentum_short", "short", "1h",
                     {"adx_min": ParamSpec(15.0, 30.0), **_EXIT_PARAMS},
                     {"adx_min": 20.0, **_EXIT_BASE}, _macd_momentum_short_entry),
    StrategyTemplate("bollinger_breakout", "long", "1h",
                     {"percent_b_min": ParamSpec(0.9, 1.1), "volume_z_min": ParamSpec(0.5, 2.0), **_EXIT_PARAMS},
                     {"percent_b_min": 1.0, "volume_z_min": 1.0, **_EXIT_BASE}, _bollinger_breakout_entry),
    StrategyTemplate("bollinger_breakdown_short", "short", "1h",
                     {"percent_b_max": ParamSpec(-0.1, 0.1), "volume_z_min": ParamSpec(0.5, 2.0), **_EXIT_PARAMS},
                     {"percent_b_max": 0.0, "volume_z_min": 1.0, **_EXIT_BASE}, _bollinger_breakdown_short_entry),
    StrategyTemplate("funding_fade_long", "long", "1h",
                     {"funding_z_max": ParamSpec(-2.5, -1.0), "rsi_max": ParamSpec(25.0, 45.0), **_EXIT_PARAMS},
                     {"funding_z_max": -1.5, "rsi_max": 38.0, **_EXIT_BASE}, _funding_fade_long_entry),
    StrategyTemplate("funding_fade_short", "short", "1h",
                     {"funding_z_min": ParamSpec(1.0, 2.5), "rsi_min": ParamSpec(55.0, 75.0), **_EXIT_PARAMS},
                     {"funding_z_min": 1.5, "rsi_min": 62.0, **_EXIT_BASE}, _funding_fade_short_entry),
    # HTF families (Thomas 2026-07-25). Ported now that every timeframe in the ladder
    # is collected — the "untimeable" objection that held them back was that the
    # higher leg's data was not there to time against, and it now is.
    StrategyTemplate("htf_trend_long", "long", "1h",
                     {"adx_min": ParamSpec(15.0, 30.0), **_EXIT_PARAMS},
                     {"adx_min": 20.0, **_EXIT_BASE}, _htf_trend_long_entry),
    StrategyTemplate("htf_trend_short", "short", "1h",
                     {"adx_min": ParamSpec(15.0, 30.0), **_EXIT_PARAMS},
                     {"adx_min": 20.0, **_EXIT_BASE}, _htf_trend_short_entry),
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
                     {"oi_change_min": ParamSpec(0.01, 0.08), "rsi_max": ParamSpec(20.0, 40.0), **_EXIT_PARAMS},
                     {"oi_change_min": 0.03, "rsi_max": 30.0, **_EXIT_BASE}, _oi_unwind_long_entry),
    StrategyTemplate("oi_unwind_short", "short", "1h",
                     {"oi_change_min": ParamSpec(0.01, 0.08), "rsi_min": ParamSpec(60.0, 80.0), **_EXIT_PARAMS},
                     {"oi_change_min": 0.03, "rsi_min": 70.0, **_EXIT_BASE}, _oi_unwind_short_entry),
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
                     {"flow_z_min": ParamSpec(0.8, 2.5), "rsi_max": ParamSpec(25.0, 45.0), **_EXIT_PARAMS},
                     {"flow_z_min": 1.3, "rsi_max": 38.0, **_EXIT_BASE}, _taker_absorption_long_entry),
    StrategyTemplate("taker_absorption_short", "short", "1h",
                     {"flow_z_min": ParamSpec(0.8, 2.5), "rsi_min": ParamSpec(55.0, 75.0), **_EXIT_PARAMS},
                     {"flow_z_min": 1.3, "rsi_min": 62.0, **_EXIT_BASE}, _taker_absorption_short_entry),
    # Premium index — the funding_fade premise at bar resolution instead of 8h steps.
    StrategyTemplate("premium_fade_long", "long", "1h",
                     {"premium_z_min": ParamSpec(1.0, 2.5), "rsi_max": ParamSpec(25.0, 45.0), **_EXIT_PARAMS},
                     {"premium_z_min": 1.5, "rsi_max": 38.0, **_EXIT_BASE}, _premium_fade_long_entry),
    StrategyTemplate("premium_fade_short", "short", "1h",
                     {"premium_z_min": ParamSpec(1.0, 2.5), "rsi_min": ParamSpec(55.0, 75.0), **_EXIT_PARAMS},
                     {"premium_z_min": 1.5, "rsi_min": 62.0, **_EXIT_BASE}, _premium_fade_short_entry),
    # Cross-asset relative strength. Not minted for the reference symbol itself — see
    # REFERENCE_FAMILIES and `templates_for_timeframe`.
    StrategyTemplate("rel_strength_long", "long", "1h",
                     {"rel_min": ParamSpec(0.005, 0.05), **_EXIT_PARAMS},
                     {"rel_min": 0.015, **_EXIT_BASE}, _rel_strength_long_entry),
    StrategyTemplate("rel_strength_short", "short", "1h",
                     {"rel_min": ParamSpec(0.005, 0.05), **_EXIT_PARAMS},
                     {"rel_min": 0.015, **_EXIT_BASE}, _rel_strength_short_entry),
    # Cross-sectional momentum — the first families whose entry depends on symbols the cycle
    # is not trading. Both param ranges are bounded by construction rather than by a guess:
    # `xs_rank_edge` at 0.4 is the widest value that keeps the long and short legs disjoint
    # (see `_xs_momentum_long_entry`), and `xs_dispersion_min` is a ratio against the cohort's
    # own recent dispersion, so 1.0 means "normal" on every symbol and every timeframe and
    # there is no level anybody had to authorize.
    # Positioning divergence — minted only where the store's coverage reaches the replay span
    # (see POSITIONING_FAMILIES). The z threshold matches the funding and premium fade families,
    # because all three mine "how unusual is this crowding reading" over the same window.
    StrategyTemplate("positioning_divergence_long", "long", "1h",
                     {"divergence_z_min": ParamSpec(1.0, 2.5), **_EXIT_PARAMS},
                     {"divergence_z_min": 1.5, **_EXIT_BASE}, _positioning_divergence_long_entry),
    StrategyTemplate("positioning_divergence_short", "short", "1h",
                     {"divergence_z_min": ParamSpec(1.0, 2.5), **_EXIT_PARAMS},
                     {"divergence_z_min": 1.5, **_EXIT_BASE}, _positioning_divergence_short_entry),
    StrategyTemplate("xs_momentum_long", "long", "1h",
                     {"xs_rank_edge": ParamSpec(0.0, 0.4),
                      "xs_dispersion_min": ParamSpec(0.8, 1.6), **_EXIT_PARAMS},
                     {"xs_rank_edge": 0.2, "xs_dispersion_min": 1.0, **_EXIT_BASE},
                     _xs_momentum_long_entry),
    StrategyTemplate("xs_momentum_short", "short", "1h",
                     {"xs_rank_edge": ParamSpec(0.0, 0.4),
                      "xs_dispersion_min": ParamSpec(0.8, 1.6), **_EXIT_PARAMS},
                     {"xs_rank_edge": 0.2, "xs_dispersion_min": 1.0, **_EXIT_BASE},
                     _xs_momentum_short_entry),
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
    # Volatility regime. Both param ranges are bounded by what leaves a sample behind rather
    # than by a preference: a percentile floor above 0.9 (or a squeeze ceiling below 0.1)
    # selects a tenth of the bars, which is how a family arrives FRAGILE for want of trades
    # rather than for want of edge. The `percent_b` ranges are the `bollinger_*` families'
    # verbatim, because the squeeze pair mines the same band crossing under a new precondition
    # and two different bands would make the comparison between them meaningless.
    StrategyTemplate("volatility_expansion_long", "long", "1h",
                     {"vol_pct_min": ParamSpec(0.5, 0.9), **_EXIT_PARAMS},
                     {"vol_pct_min": 0.7, **_EXIT_BASE}, _volatility_expansion_long_entry),
    StrategyTemplate("volatility_expansion_short", "short", "1h",
                     {"vol_pct_min": ParamSpec(0.5, 0.9), **_EXIT_PARAMS},
                     {"vol_pct_min": 0.7, **_EXIT_BASE}, _volatility_expansion_short_entry),
    StrategyTemplate("volatility_squeeze_long", "long", "1h",
                     {"squeeze_max": ParamSpec(0.1, 0.4), "percent_b_min": ParamSpec(0.9, 1.1),
                      **_EXIT_PARAMS},
                     {"squeeze_max": 0.25, "percent_b_min": 1.0, **_EXIT_BASE},
                     _volatility_squeeze_long_entry),
    StrategyTemplate("volatility_squeeze_short", "short", "1h",
                     {"squeeze_max": ParamSpec(0.1, 0.4), "percent_b_max": ParamSpec(-0.1, 0.1),
                      **_EXIT_PARAMS},
                     {"squeeze_max": 0.25, "percent_b_max": 0.0, **_EXIT_BASE},
                     _volatility_squeeze_short_entry),
)

# Families whose entry rules read the open-interest columns — mintable only where the
# feed is configured; with no feed their conditions are indeterminate and never match,
# so a minted spec is harmless (it simply does not trade) rather than wrong.
OI_FAMILIES = frozenset({"oi_squeeze_long", "oi_squeeze_short",
                         "oi_unwind_long", "oi_unwind_short"})

# Families whose entry rules read HTF columns — mintable only where a higher
# timeframe exists to read (see ``market_data.HIGHER_TIMEFRAME``).
HTF_FAMILIES = frozenset({"htf_trend_long", "htf_trend_short",
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
CROSS_SECTION_FAMILIES = frozenset({"xs_momentum_long", "xs_momentum_short"})

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


def templates_for_timeframe(
    timeframe: str, *, symbol: str | None = None, positioning_eligible: bool = False,
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
      window, which the caller measures and passes as ``positioning_eligible``.

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

    The taker_* and premium_* families need no gate — their legs ride the same klines call as
    the OHLCV, at every timeframe and for every symbol."""
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
        return True

    return tuple(replace(t, timeframe=timeframe) for t in TEMPLATES if _minted(t))


# --- S3 validator (source rules, restricted to the ported feature registry) ---

def validate_strategy(spec: StrategySpec) -> dict[str, Any]:
    """Approval-for-backtest verdict. Pure, fail-closed, never mutates."""
    reasons: list[str] = []
    if spec.schema_version != SCHEMA_VERSION:
        reasons.append("BLOCK_SCHEMA_VERSION")
    if len(spec.entry_rules.conditions) > MAX_ENTRY_CONDITIONS:
        reasons.append("BLOCK_TOO_MANY_CONDITIONS")
    for cond in spec.entry_rules.conditions:
        if cond.feature in NUMERIC_FEATURES:
            if cond.comparison not in _NUMERIC_COMPARISONS:
                reasons.append("BLOCK_INVALID_COMPARISON")
            if cond.value is not None and isinstance(cond.value, str):
                reasons.append("BLOCK_INVALID_FEATURE_VALUE")
        elif cond.feature in CATEGORICAL_FEATURES:
            if cond.comparison not in _CATEGORICAL_COMPARISONS:
                reasons.append("BLOCK_INVALID_COMPARISON")
            if cond.value is not None and cond.value not in CATEGORICAL_FEATURES[cond.feature]:
                reasons.append("BLOCK_INVALID_FEATURE_VALUE")
        else:
            reasons.append("BLOCK_UNKNOWN_FEATURE")
        if cond.value_from is not None and cond.value_from not in NUMERIC_FEATURES:
            reasons.append("BLOCK_UNKNOWN_FEATURE" if cond.value_from not in CATEGORICAL_FEATURES
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
    """Perturb each parameter within a fraction of its range, clamped to bounds."""
    out: dict[str, float] = {}
    for name, spec in param_space.items():
        base = base_params[name]
        span = (spec.hi - spec.lo) * scale
        val = base + rng.uniform(-span, span)
        val = max(spec.lo, min(spec.hi, val))
        out[name] = int(round(val)) if spec.integer else round(val, 4)
    return out


def build_spec_dict(
    template: StrategyTemplate, params: dict[str, float], *,
    strategy_id: str, generation_id: str, symbol: str = "BTCUSDT",
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": strategy_id,
        "strategy_version": "1.0",
        "generation_id": generation_id,
        "strategy_family": template.family,
        "status": "GENERATED",
        "symbol_scope": [symbol],
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
    }


def _rotation_offset(generation_id: str, seed: int, count: int, total: int) -> int:
    """The first family index this generation mints. Deterministic, marches forward."""
    if total <= 0:
        return 0
    try:
        step = int(str(generation_id).rsplit("-", 1)[1])
    except (ValueError, IndexError):
        step = int(seed)
    return (step * max(count, 1)) % total


def generate_batch(
    generation_id: str, *, seed: int, start_index: int = 1, count: int = DEFAULT_BATCH_SIZE,
    symbol: str = "BTCUSDT", timeframe: str = "1d",
    known_rule_hashes: frozenset[str] = frozenset(),
    positioning_eligible: bool = False,
) -> dict[str, Any]:
    """Produce ``count`` validated, distinct candidate specs (source mechanics).

    ``known_rule_hashes`` extends the duplicate guard across the existing pool and
    candidate store, so a batch never re-mints a strategy that already exists.

    ``positioning_eligible`` is passed straight through to :func:`templates_for_timeframe` and
    defaults to False for the reason stated there — unmeasured coverage must not mint a family
    over a window that cannot score it."""
    rng = random.Random(seed)
    templates = templates_for_timeframe(
        timeframe, symbol=symbol, positioning_eligible=positioning_eligible
    )
    # Which slice of the family list THIS run mints. Without it the picker was
    # ``templates[len(accepted) % len(templates)]``, and since a batch is four specs
    # that selected templates[0..3] on every run ever: 228 of 228 factory candidates
    # came from those four families, while the other sixteen — htf_*, oi_*,
    # funding_fade_*, mean_reversion*, macd_momentum*, bollinger_* — existed in the
    # library and could never be minted at all. Porting a family was therefore
    # invisible work. Stepping by ``count`` per generation walks the whole list in
    # ceil(len/count) runs with no overlap, and stays deterministic (generations are
    # sequential; the seed is the fallback when an id is not parseable).
    offset = _rotation_offset(generation_id, seed, count, len(templates))
    accepted: list[StrategySpec] = []
    validations: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_hashes: set[str] = set(known_rule_hashes)

    attempts = 0
    while len(accepted) < count and attempts < count * _MAX_ATTEMPTS_PER_SPEC:
        attempts += 1
        template = templates[(offset + len(accepted)) % len(templates)]
        params = mutate_params(template.base_params, template.param_space, rng)
        strategy_id = f"S{start_index + len(accepted):03d}"
        spec_dict = build_spec_dict(template, params, strategy_id=strategy_id,
                                    generation_id=generation_id, symbol=symbol)
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
        validations.append(verdict)

    return {
        "generation_id": generation_id,
        "seed": seed,
        "requested_count": count,
        "accepted_count": len(accepted),
        "specs": [s.to_dict() for s in accepted],
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

    if not events:
        # Settlements per bar, from the bar's own span. Sub-8h timeframes get a fraction, which
        # is right: a 15m bar sits through 1/32 of an interval on average, and charging a whole
        # one per bar would price a scalper like a swing trader.
        minutes = market_data.TIMEFRAMES.get(timeframe, 1440)
        per_bar = (minutes / 1440.0) * FUNDING_INTERVALS_PER_DAY
        return [cost.funding_bps_per_interval / 10000.0 * per_bar] * len(candles), FUNDING_SOURCE_FALLBACK

    events.sort(key=lambda pair: pair[0])
    bar_opens: list[Any] = []
    for candle in candles:
        try:
            bar_opens.append(_timeutil.parse_iso(str(candle.get("open_time"))))
        except (ValueError, TypeError):
            bar_opens.append(None)

    cursor = 0
    for i, opened in enumerate(bar_opens):
        if opened is None:
            continue
        # The next parseable bar open bounds this bar; the last bar is bounded by nothing, so it
        # takes every remaining settlement. A trade cannot close after the last bar anyway.
        upper = next((b for b in bar_opens[i + 1:] if b is not None), None)
        while cursor < len(events) and events[cursor][0] < opened:
            cursor += 1  # before this bar (only reachable for leading events)
        total = 0.0
        scan = cursor
        while scan < len(events) and (upper is None or events[scan][0] < upper):
            total += events[scan][1]
            scan += 1
        charges[i] = total
        cursor = scan
    return charges, FUNDING_SOURCE_VENUE


def _replay(
    spec: StrategySpec, rows: list[dict[str, Any]], candles: list[Mapping[str, Any]],
    *, cost: CostModel, funding: list[float] | None = None, offset: int = 0,
) -> tuple[list[dict[str, Any]], float, float]:
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
            position = {
                "direction": "LONG" if long else "SHORT",
                "entry_price": float(close),
                "stop_loss": close - stop_distance if long else close + stop_distance,
                "take_profit": close + target_distance if long else close - target_distance,
                "risk": abs(stop_distance),
                "holding_candles": 0,
                # Where the carry starts. Kept on the position rather than in a parallel
                # variable so a settlement can only ever bill the window of the trade that is
                # actually open — the two cannot drift apart.
                "entry_index": i,
            }
            entry_regime = row.get("market_regime")
    return (outcomes, total_fee_cost_r, total_maker_fee_cost_r, total_slippage_cost_r,
            total_funding_cost_r)


def _holdout_evidence(
    spec: StrategySpec, rows: list[dict[str, Any]], candles: list[Mapping[str, Any]],
    *, cost: CostModel, offset: int, funding: list[float] | None = None,
    funding_source: str = FUNDING_SOURCE_VENUE,
) -> dict[str, Any]:
    """What the spec did on bars that never touched its score. Compact by design.

    Only the few numbers a confirmation needs: how many trades the unseen tail
    produced and whether they were profitable in aggregate. The verdict layer turns
    that into CONFIRMED / CONTRADICTED / INSUFFICIENT — this function judges nothing."""
    outcomes, fees, maker_fees, slippage, carry = _replay(
        spec, rows, candles, cost=cost, funding=funding, offset=offset
    )
    total_r = round(sum(float(o["result_R"]) for o in outcomes), 8)
    closed = len(outcomes)
    return {
        "bars": len(rows),
        "closed_count": closed,
        "win_count": sum(1 for o in outcomes if float(o["result_R"]) > 0),
        "total_R": total_r,
        "expectancy": round(total_r / closed, 8) if closed else 0.0,
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
    """Replay ``spec`` over the snapshot's history. Deterministic, pure.

    Uses the exact live-path components: ``evaluate_spec`` decides entries on row i,
    the position opens at row i's close with the spec's ATR exits, and every later
    bar settles through ``paper.settle_trade_plan`` (pessimistic SL-first, the spec's
    own ``max_holding_bars`` as the time exit — backtest semantics). Rows whose
    features are indeterminate never enter (the evaluator's rule).

    C12: every closed trade's gross (intended-price) R is costed via
    ``cost.apply_cost_model`` (fees + slippage, source S4b). ``result_R`` on each
    outcome — and therefore ``expectancy``/``champion_score`` for this spec — is the
    NET R after costs; ``gross_R`` rides alongside for transparency."""
    cost = cost or CostModel()
    # Reused when the caller already built it (`run_factory` builds one per fire and replays
    # every spec and fusion child through it), rebuilt when it did not. A frame from a different
    # cost model is refused rather than used: see `ReplayFrame`.
    if frame is None:
        frame = build_replay_frame(snapshot, cost=cost)
    elif frame.cost != cost:
        raise ValueError(
            "replay frame was built under a different cost model than this backtest charges; "
            "the carry series would price trades at rates they never faced"
        )
    all_rows, all_candles = frame.rows, frame.candles
    all_funding, funding_source, split = frame.funding, frame.funding_source, frame.split
    rows, candles = all_rows[:split], all_candles[:split]
    (outcomes, total_fee_cost_r, total_maker_fee_cost_r, total_slippage_cost_r,
     total_funding_cost_r) = _replay(
        spec, rows, candles, cost=cost, funding=all_funding[:split]
    )
    holdout = _holdout_evidence(
        spec, all_rows[split:], all_candles[split:], cost=cost, offset=split,
        funding=all_funding[split:], funding_source=funding_source,
    )

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
    window_bars = max(1, len(rows) // BACKTEST_WINDOWS)
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
        "win_count": summary["win_count"],
        "loss_count": summary["loss_count"],
        # M4a: realized payoff legs, so a candidate carries its win-rate and realized
        # reward:risk (avg_win_R / avg_loss_R) for the second-pass promotion ranking.
        "avg_win_R": summary["avg_win_R"],
        "avg_loss_R": summary["avg_loss_R"],
        "max_drawdown": summary["max_drawdown"],
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
        "bars_replayed": len(rows),
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


def fuse_specs(
    first: StrategySpec, second: StrategySpec, *, strategy_id: str, generation_id: str,
) -> StrategySpec:
    """Cross two parents into a child that enters only where BOTH would.

    Entry conditions are the **deduplicated union** under AND, so the child is by
    construction at least as selective as either parent — a crossover can never
    loosen an entry. Exits are the midpoint of the parents'; risk takes the
    stricter (minimum) cap. Everything the parents must agree on (schema,
    direction, timeframe, symbol scope, stop model, AND-operator) is a fail-closed
    precondition, not something to reconcile: unioning an OR parent's conditions
    into an AND would silently change what that parent meant.

    The child is structurally parsed and put through the same ``validate_strategy``
    as any generated spec; a blend that lands outside the validator's bounds (an
    R:R below the floor, say) refuses rather than being clamped into range."""
    if first.schema_version != second.schema_version:
        raise FusionRefused("schema_version_mismatch")
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
            "stop_atr": round((first.exit_rules.stop_atr + second.exit_rules.stop_atr) / 2, 4),
            "target_atr": round((first.exit_rules.target_atr + second.exit_rules.target_atr) / 2, 4),
            "max_holding_bars": int(
                round((first.exit_rules.max_holding_bars + second.exit_rules.max_holding_bars) / 2)
            ),
        },
        "risk_constraints": {
            "max_risk_per_trade_R": min(
                first.risk_constraints.max_risk_per_trade_R,
                second.risk_constraints.max_risk_per_trade_R,
            ),
        },
        "created_by": "mvp_factory_fusion",
    }
    try:
        child = StrategySpec.from_dict(spec_dict)
    except SpecParseError as exc:
        raise FusionRefused(f"parse: {exc}") from exc
    verdict = validate_strategy(child)
    if not verdict["approved_for_backtest"]:
        raise FusionRefused(f"validator: {','.join(verdict['block_reasons'])}")
    return child


def rank_fusion_parents(
    existing_candidates: list[Mapping[str, Any]], *, top_n: int = FUSION_PARENT_POOL,
) -> list[dict[str, Any]]:
    """The best-scoring distinct lineages available as parents, deterministically.

    Only rows carrying a numeric ``champion_score`` and a parseable spec can parent
    — an unscored or legacy-shaped row has no evidence to pass on. Ordering is
    (score desc, candidate_id asc) so a tie never depends on file order, and a
    lineage appears once however many times it was appended (latest-wins)."""
    best: dict[str, dict[str, Any]] = {}
    for record in existing_candidates:
        score = record.get("champion_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            continue
        if not isinstance(record.get("strategy_spec"), Mapping):
            continue
        cid = candidate_id(record)
        best[cid] = {**record, "candidate_id": cid}
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
    candidate that can never trade."""
    minted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    pair_stream = (pair for bucket in buckets for pair in combinations(bucket, 2))
    for left, right in pair_stream:
        if len(minted) >= pairs:
            break
        parent_ids = sorted([left["candidate_id"], right["candidate_id"]])
        try:
            child = fuse_specs(
                StrategySpec.from_dict(dict(left["strategy_spec"])),
                StrategySpec.from_dict(dict(right["strategy_spec"])),
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

    batch = generate_batch(
        generation_id, seed=seed, count=count,
        symbol=str(snapshot.get("symbol") or "BTCUSDT"),
        timeframe=str(snapshot.get("timeframe") or "1d"),
        known_rule_hashes=known_hashes,
        positioning_eligible=positioning_eligible,
    )

    # Built once for the whole run. Features, candles and carry are properties of the market and
    # the calendar, not of any spec, and `build_feature_rows` alone is 6.0s at the 48,000-bar 15m
    # window — so rebuilding it per candidate cost ~30s a fire on a sequential scheduler.
    frame = build_replay_frame(snapshot)

    candidates: list[dict[str, Any]] = []
    for spec_dict in batch["specs"]:
        spec = StrategySpec.from_dict(spec_dict)
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
        "rejected": batch["rejected"],
        "candidates": [*candidates, *fused],
        "fused_count": len(fused),
        "fusion_rejected": fusion_rejected,
        "evidence_input_sha256": candles_sha,
        "created_at": now,
    }
