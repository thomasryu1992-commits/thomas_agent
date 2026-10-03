"""The template space: what a minted spec may be, and which templates a context is offered.

Moved whole out of ``factory`` (crypto refactor plan PR-09). ``factory`` re-exports, as
the same objects, the names its callers still read there, ``factory.TEMPLATES`` and
``factory.validate_strategy`` among them, and every template builder (refactor plan PR-16 removed the
rest; the set is pinned by ``test_mvp_runtime_crypto_reexport_roster.py``).

What is here:

- the validator's bounds (``STOP_ATR_RANGE``, ``MAX_ENTRY_CONDITIONS``, ...) and the feature
  vocabulary a generated spec may name (``NUMERIC_FEATURES``, ``CATEGORICAL_FEATURES``);
- ``ParamSpec`` and ``StrategyTemplate``, the exit parameter spaces, and one entry builder per
  family;
- ``TEMPLATES``, the library, and the family sets that say what each family needs
  (``OI_FAMILIES``, ``HTF_FAMILIES``, ``RETIRED_FAMILIES``, ...);
- ``templates_for_timeframe``: the rotation a ``(timeframe, symbol, venue)`` context is offered,
  after the feeds that cannot reach the replay window and the holds that cannot be judged are
  taken out;
- ``known_features`` and ``validate_strategy``: the approval-for-backtest verdict.

It draws nothing and scores nothing. The generator that mutates these parameters, and the
comments here that name it (``mutate_params``, ``generate_batch``, ``_apply_reward_risk_floor``,
``elite_base_params``), are in ``factory``. The holdout split comes from ``backtest``. This
module imports neither ``factory`` nor anything above the strategy layer.

Pure: no file, clock, environment or network read.

A test that patches a name one of these functions reads patches it here. A patch on
``factory.<name>`` rebinds ``factory``'s copy and does not reach a function defined in this
module.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable

from . import features, market_data
from ..errors import ToolBlocked
from .backtest import holdout_split_index
from .robustness import MIN_HOLDOUT_TRADES
from .strategy import SCHEMA_VERSION, StrategySpec

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
    # Whether "near" means anything for this parameter, which decides how it is DRAWN rather
    # than what it may be. Everything in the library is ordered except `session_index`, whose
    # values index ASIA/EUROPE/US — three labels with no order, no midpoint and no neighbour, so
    # perturbing one by a fraction of its range is an operation on a number that is standing in
    # for a name. See :func:`mutate_params` for what the perturbation did to it.
    ordered: bool = True

    def __post_init__(self) -> None:
        # An unordered CONTINUOUS parameter has no meaning the draw below could serve, and it
        # would be truncated to an integer silently rather than refused. Fail closed at import.
        if not self.ordered and not self.integer:
            raise ValueError("an unordered ParamSpec must be integer")


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
# The BASE still moves with the floor, but no longer to escape a clamp. Historically it did:
# `mutate_params` clamped, so raising `lo` to 1.2 while leaving the base at 1.2 pinned **49.8%**
# of draws to exactly 1.2 — one value, one rule hash, a collapse in the parameter's diversity
# rather than a shift in it — and 1.45 was chosen to bring that to 5.4%. What survives that
# reasoning is the aim: at 1.45 roughly 77% of draws land in the band measured above, and the
# tail still reaches 1.73 so the region this store has never sampled (1.6-2.0, n=2) stays
# explorable. Since 2026-08-05 `mutate_params` FOLDS at the bound instead, so a base near one
# spreads its overshoot back into the space rather than stacking it on the edge.
#
# **That change is why this base and `target_atr`'s are left where they are.** The pinning was
# measured per-parameter on 2026-08-04 and read as `target_atr`'s problem — base 3.0 clears the
# 1.6 floor by 1.4 against a span of +/-2.24, so 18.9% of draws pinned, 14.4% after
# `_apply_reward_risk_floor` — with the conclusion that "moving this base up would be the fix".
# Measured from the centres the factory actually uses, it was not: `generate_batch` draws half
# of every batch around `elite_base_params`, which never reads `_EXIT_BASE`, so moving the base
# fixes the template half and leaves the elite half pinning 15.0%, while shifting the median
# drawn target 3.12 -> 3.86. Folding takes both halves to 0.0% and moves the median target by
# 0.10. See `_fold_into_bounds` for the full table and for the ratchet that makes the elite half
# the half that matters.
# **The CEILING opens to 4.0 (Thomas 2026-08-25), and the floor and base do not move.**
#
# The floor's measurement above is about the bottom of the range and still holds. Nothing had
# ever measured the top, and 2.0 was not chosen against evidence — it is the value the space
# was born with, while `validate_strategy` has always admitted `STOP_ATR_RANGE` up to 5.0. So
# the region 2.0-5.0 is not a rejected hypothesis; it is an untested one, and the reason it
# stayed untested is arithmetic: `span = (hi - lo) * _MUTATION_SCALE` = 0.28, so a base of 1.45
# could not draw past **1.73**. Every spec in the store sits under that number because no draw
# could reach higher, not because higher scored worse.
#
# What made it worth testing: a fixed ~10-16 bps round trip is a FRACTION of 1R = stop_atr x
# ATR, so widening the stop shrinks friction in R terms. Measured 2026-08-25 by replaying
# stored 1h specs on one snapshot with the stop swept and the reward:risk PRESERVED (target
# scaled with the stop, so the signal is held still and only the geometry moves):
#
#   stop_atr     1.2      1.6      2.0      2.6      3.2      4.0
#   cost/trade   0.151    0.130    0.114    0.098    0.086    0.074
#   net/trade   -0.162   -0.059   -0.039   -0.062   -0.032   -0.032
#
# **Two things that reading gets wrong if it stops at the aggregate.** First, cost does NOT
# fall as 1/stop: `cost x stop` runs 0.181 -> 0.296 across that sweep (+63%, reproduced on a
# second, weaker spec set), because a wider stop holds longer — more funding, and fewer trades
# closing inside the window. An extrapolation that assumes the product is constant predicts a
# break-even that does not arrive. Second, the aggregate hides the result that matters: it
# mixes specs whose best stop is 3.2 (`breakdown_short`, +0.122 -> +0.276) with specs it makes
# WORSE (`volatility_expansion_short`, +0.149 -> +0.020). The axis is per-spec, so what the
# ceiling buys is not "wider is better" — it is that the search can reach a spec's own optimum
# where today it is truncated at 1.73.
#
# **This does not open the fast timeframes, and is not offered as doing so.** At 15m friction
# is 0.28R against a gross edge of 0.09R and no stop multiple closes a 3x gap — the same
# conclusion the floor's comment reached, re-measured and unchanged.
#
# The BASE stays at 1.45 deliberately. Widening `hi` alone already triples the span (0.28 ->
# 0.98), so the template half reaches 2.43 and the 1.2-1.6 band the floor measured falls from
# ~73% to ~41% of draws — a dilution, not an abandonment. Moving the base as well would spend
# the region that HAS evidence to reach one that has none. Above 2.43 the ratchet is the elite
# half: `generate_batch` centres half of every batch on `elite_base_params`, so a wide-stop
# spec that actually scores walks the centre up a generation at a time, and one that does not
# never leaves 2.43. That is the property this change is buying — an opened space that the
# evidence walks into, rather than a new number asserted into the geometry.
# **The exit pair is drawn as (stop, reward:risk), not (stop, target)** — Thomas 2026-08-25,
# and the ceiling above is what made the old parameterisation untenable rather than merely
# awkward.
#
# `target_atr` was drawn independently of `stop_atr` with `_apply_reward_risk_floor` pushing it
# up when the pair came out illegal. That is fine while the stop barely moves, and it breaks as
# soon as the stop's range opens: the floor tracks the stop, so widening the ceiling drags the
# whole target distribution up behind it. Measured on the 2.0 -> 4.0 move: the low-target band
# (< 2.0) fell 17.5% -> 11.1% of draws and the median target rose 3.02 -> 3.28, re-aiming a
# distribution the fold exists to hold still. Raising `target_atr`'s own ceiling makes it worse
# (8.6% at 10.0), because the squeeze is the coupling, not the bound.
#
# It also could not express the thing the sweep measured. Widening the stop while HOLDING the
# reward:risk is what turned -0.162R/trade into -0.032R at 1h; widening the stop alone
# compresses the ratio instead, which is the confound that cancels the gain. Under
# (stop, reward_risk) that operation is one axis moving and the other held — the search can
# now reach it, where before it could only stumble into it.
#
# What the ratio's own bounds preserve: `lo` is `MIN_REWARD_RISK`, so the floor
# `_apply_reward_risk_floor` used to enforce by redrawing is now structural and no draw can be
# illegal. `hi` is 5.0, which is where the old space topped out in practice (target 8.0 over a
# 1.6 stop). The base is 3.0 / 1.45 = 2.07, the ratio the old base described, so the aim is
# carried over rather than re-chosen.
#
# `target_atr` is still what the SPEC stores and what every consumer reads — the change is to
# how the pair is drawn, not to the geometry's representation, so stored specs, rule hashes and
# `validate_strategy` are untouched. Elite centres migrate themselves: `_project` keeps only the
# keys the template declares and falls back to the base for the rest, so a stored `mint_params`
# carrying `target_atr` contributes its stop and takes the template's ratio for one generation,
# after which the store carries `reward_risk` of its own.
# **The bounds are chosen so the PRODUCT is legal, which is what makes the pair safe to draw
# independently.** `target_atr = stop_atr x reward_risk` and `validate_strategy` refuses a
# target outside `TARGET_ATR_RANGE` (0.5, 10.0), so a corner of the box has to stay inside it:
# 3.0 x 3.3 = 9.9. Two independent draws whose product is bounded need no clamp and no redraw —
# the old floor's `_apply_reward_risk_floor` existed precisely because the old pair COULD be
# drawn illegal, and the whole point of this axis is that it cannot.
#
# 3.0 rather than the 4.0 a stop-only widening would have taken: the ceilings trade against
# each other under that product, and the sweep's optima sit at stop 2.6-3.2 WITH ratio 2.7-3.1,
# so a box that reaches ratio 3.3 and stop 3.0 covers them where stop 4.0 x ratio 2.5 would
# clip the ratio instead. Both are far past the 1.73 the old space could actually draw.
_EXIT_PARAMS = {
    "stop_atr": ParamSpec(1.2, 3.0),
    "reward_risk": ParamSpec(MIN_REWARD_RISK, 3.3),
    "max_holding_bars": ParamSpec(12, 48, integer=True),
}
_EXIT_BASE = {"stop_atr": 1.45, "reward_risk": 2.07, "max_holding_bars": 24}

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
# Bases sit mid-range for the reason recorded above the trend base: a base ON a bound used to
# pin ~50% of draws to that bound. At 1.7/2.7/10 no draw reaches a bound at all (spans are
# +/-0.21, +/-0.49, +/-4.2), so the whole interval stays reachable and the worst-case drawn R:R
# is 2.21/1.91 = 1.16. `mutate_params` folds at the bound since 2026-08-05 and a space built
# this way never reaches the fold — the same relationship this space already has with
# `_apply_reward_risk_floor`, and worth keeping for the same reason: a property that holds by
# construction is stronger than one restored by a repair.
# The fade geometry moves to the same axis and keeps its own numbers. Its ratio spans
# [1.0, 2.4] against the trend space's [1.0, 5.0] — 3.4 over a 1.4 stop is where the old
# target bound topped out — and its STOP ceiling deliberately does not move with the trend
# space's: a fade is complete when price returns to the mean, so a wide stop buys a longer
# wait in a position whose premise has already failed rather than a cheaper risk unit.
_FADE_EXIT_PARAMS = {
    "stop_atr": ParamSpec(1.4, 2.0),
    "reward_risk": ParamSpec(MIN_REWARD_RISK, 2.4),
    "max_holding_bars": ParamSpec(4, 16, integer=True),
}
_FADE_EXIT_BASE = {"stop_atr": 1.7, "reward_risk": 1.59, "max_holding_bars": 10}

# Every generation space the factory mints from. `_fused_exit_param` clamps against the UNION
# of these rather than against `_EXIT_PARAMS` alone — see there for why the union is the honest
# bound once more than one space exists.
#
# **Neither space is a function of the TIMEFRAME, and measured 2026-08-05 neither should be.**
# The question is fair to ask and this note exists so it is asked once: `templates_for_timeframe`
# retimes every template by rewriting one field, so `max_holding_bars` 4-16 is four to sixteen
# HOURS at 1h and four to sixteen DAYS at 1d, while the fade space's own reasoning above is
# stated in time ("a bounce that has not happened in sixteen bars was not a bounce"). Cost-in-R
# meanwhile varies ninefold across the ladder — 0.3065R per trade at 15m, 0.1530 at 1h, 0.0790 at
# 4h, 0.0348 at 1d, holdout, trade-weighted — so a geometry that is right at one end has no
# reason to be right at the other.
#
# It does not separate. Holdout net R per trade by timeframe x holding band, seeded rows, reads
# a spread of at most 0.04R inside every timeframe (15m -0.362 long-hold against -0.381 short,
# 1h -0.168/-0.201, 1d +0.068/+0.075) — under the 0.05R nothing in this store resolves. The one
# apparent exception is 4h, where short-hold trend rows read **-0.1571R over 3,070 trades against
# -0.0418R over 8,896** — and it is the confound the `_EXIT_PARAMS` note warns about, not a
# finding: paired within (family, timeframe, symbol) the 4h gap **reverses** to +0.0133R with 11
# of 20 cells favouring the short hold. Over all 51 paired cells the median is -0.0223R and 24
# favour short, which is a coin flip at a magnitude nothing here can resolve.
#
# So the ninefold cost spread is real and the holding period is not the lever on it — the
# denominator is, which is what the `_EXIT_PARAMS` note already records and what
# `docs/BUILD_HISTORY.md` carries for the ladder as a whole. What would reopen this is a
# measurement separating the bands INSIDE a cell, not another aggregate.
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


def _htf_reversal_long_entry(p: dict) -> list[dict]:
    # The higher timeframe's own MOMENTUM, which no minted family has ever read. Every htf_*
    # family above enters on where the slow leg is pointing — `htf_ma20_distance_ma50` for the
    # strength pair, `htf_market_regime` for the pullback pair — and both are statements about
    # TREND. `htf_rsi` and `htf_adx` have been on the row since the HTF leg was wired on
    # 2026-07-25 and are read by nothing, so "the slow leg is stretched" is a question this
    # search has never been able to ask.
    #
    # The premise: the higher timeframe is washed out while the traded one has already turned.
    # That ordering is what separates it from `mean_reversion` — a local RSI of 30 says this bar
    # is stretched, and says nothing about whether the move it is stretched against is a dip in
    # something larger or the whole of it.
    #
    # Mirrored around 50 through ONE edge per leg rather than through four independent bounds,
    # the `htf_sep_min` convention: a draw that is a weak long signal is an equally weak short
    # one, and the two legs stay disjoint by construction at every value in the range (the
    # `xs_rank_edge` argument — at the loosest draw the long needs htf_rsi <= 40 and the short
    # htf_rsi >= 60).
    return [
        {"feature": "htf_rsi", "comparison": "<=", "value": 50.0 - p["htf_rsi_edge"]},
        {"feature": "rsi", "comparison": ">=", "value": 50.0 + p["rsi_edge"]},
    ]


def _htf_reversal_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "htf_rsi", "comparison": ">=", "value": 50.0 + p["htf_rsi_edge"]},
        {"feature": "rsi", "comparison": "<=", "value": 50.0 - p["rsi_edge"]},
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


def _taker_flow_fade_long_entry(p: dict) -> list[dict]:
    # The single bar's print, faded — and it is the column `_taker_flow_long_entry`'s own note
    # argues AGAINST reading, which is the reason this family is worth a slot rather than the
    # reason it is not. That note is right that "one bar's imbalance is mostly that bar's own
    # move restated"; the flow families it justifies therefore all read the part that PERSISTED
    # (`taker_flow_ma`, or the z-score against a rolling norm). Nothing reads the restatement
    # itself, so `taker_flow_imbalance` is on the row unmined.
    #
    # A move restated by its own aggressor flow is exactly what an exhaustion print is: the
    # signed form means a draw of 0.20 reads "sellers took 60% of the bar's volume" on BTC and
    # on DOGE alike (see `features`: imbalance is 2 x ratio - 1), and pairing it with a washed
    # out RSI asks for the bar where heavy one-way aggression has already spent the move.
    #
    # Distinct from `taker_absorption_long` in the SIGN, and the two are opposite premises on
    # one variable rather than variations: absorption buys heavy BUY aggression into a washed
    # out RSI (someone is taking the supply), this buys heavy SELL aggression into the same RSI
    # (the supply is finished). Both cannot be right, which is the point of minting them.
    return [
        {"feature": "taker_flow_imbalance", "comparison": "<=", "value": -p["flow_edge"]},
        {"feature": "rsi", "comparison": "<=", "value": p["rsi_max"]},
    ]


def _taker_flow_fade_short_entry(p: dict) -> list[dict]:
    return [
        {"feature": "taker_flow_imbalance", "comparison": ">=", "value": p["flow_edge"]},
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


def _funding_momentum_long_entry(p: dict) -> list[dict]:
    # The OTHER sign of the same variable. Both funding families above fade the crowd, so the
    # search has spent every funding slot it has on one direction of one hypothesis and has
    # never asked the opposite one: that carry PERSISTS, and unusual funding beside a trend is
    # the crowd being early rather than wrong.
    #
    # `funding_z_min` deliberately reuses `funding_fade_short`'s range and base rather than a
    # tuned pair. The whole value of this family is that its threshold is the fade family's, so
    # a difference in outcome is attributable to the entry SHAPE — the `ma_cross_*` argument,
    # which kept the filters of the state families it mirrors for exactly this reason. Confirmed
    # by trend rather than by RSI because the premise is continuation, and an RSI bound would
    # be quietly asking for a pullback inside it.
    #
    # This is the cheapest genuinely new hypothesis available in this vocabulary — one column,
    # already collected, already gated, whose accumulated evidence to date is all on the other
    # side. Nothing here predicts it works; the fade side has not either.
    return [
        {"feature": "funding_zscore", "comparison": ">=", "value": p["funding_z_min"]},
        {"feature": "close", "comparison": ">", "value_from": "ma20"},
    ]


def _funding_momentum_short_entry(p: dict) -> list[dict]:
    # The sign on the VALUE, not on the comparison — `htf_trend_strength_short`'s convention,
    # so one range describes both legs.
    return [
        {"feature": "funding_zscore", "comparison": "<=", "value": -p["funding_z_min"]},
        {"feature": "close", "comparison": "<", "value_from": "ma20"},
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
    # Session context. WHICH session a spec claims is part of the seeded search and is charged as
    # a free parameter — `ordered=False` is what makes that true rather than merely intended. The
    # values index ASIA/EUROPE/US, so the perturbation every other parameter gets was operating on
    # a number standing in for a name and pinned 71% of draws on the base; see `mutate_params`.
    StrategyTemplate("session_trend_long", "long", "1h",
                     {"session_index": ParamSpec(0, len(features.SESSION_BOUNDS) - 1,
                                                 integer=True, ordered=False),
                      "adx_min": ParamSpec(15.0, 30.0), **_EXIT_PARAMS},
                     {"session_index": 1, "adx_min": 20.0, **_EXIT_BASE}, _session_trend_long_entry),
    StrategyTemplate("session_trend_short", "short", "1h",
                     {"session_index": ParamSpec(0, len(features.SESSION_BOUNDS) - 1,
                                                 integer=True, ordered=False),
                      "adx_min": ParamSpec(15.0, 30.0), **_EXIT_PARAMS},
                     {"session_index": 1, "adx_min": 20.0, **_EXIT_BASE}, _session_trend_short_entry),
    # Volatility regime — the expansion side only; the squeeze pair that shipped beside it is
    # in RETIRED_FAMILIES below. The param range is bounded by what leaves a sample behind
    # rather than by a preference: a percentile floor above 0.9 selects a tenth of the bars,
    # which is how a family arrives FRAGILE for want of trades rather than for want of edge.
    # Three pairs added to widen the SEARCH SPACE rather than to back a hypothesis — measured
    # 2026-08-08 over this file, across 60 parameter draws per template rather than the base
    # params alone: of the 56 columns in `NUMERIC_FEATURES`, the 38 minted families between them
    # read **21**, and the rotation had never conditioned on the other 35. Two of those are
    # reached here (`htf_rsi`, `taker_flow_imbalance`); the third pair reads an already-read
    # column at a sign the library holds no family at (`funding_zscore` above zero). Every one is
    # already collected, already classified in `_FEATURE_FEED`, and already gated by an existing
    # set — so what is new here is which questions get asked, not what gets fetched or what may
    # route.
    #
    # (The count is of MINTABLE NUMERIC columns. The families reference 24 distinct feature
    # names in total, which is the same measurement plus three categoricals — a number worth
    # naming here because conflating the two is how this comment first read 24/56.)
    #
    # **The cost is dilution and it is real.** `DEFAULT_BATCH_SIZE` is 4 per fire whatever the
    # library holds, so a context's revisit interval stretches: measured per context,
    # 34 -> 40 families at BTC 1h/4h (**+17.6%**), 36 -> 42 at the non-proxy symbols (+16.7%),
    # and 22 -> 24 at 1d (+9.1%, where the htf and funding pairs are gated out). That is
    # slower evidence accrual for every existing family, against an effective sample already
    # measured in market periods rather than trades. Worth taking only for premises the search
    # cannot otherwise reach, which is why the squeeze/contraction proposals that arrived beside
    # these were NOT ported: they re-propose `volatility_squeeze_*`, retired 2026-08-04 on a
    # measurement, and re-listing that pair is a one-line reversal nobody needs a new family for.
    StrategyTemplate("htf_reversal_long", "long", "1h",
                     {"htf_rsi_edge": ParamSpec(10.0, 30.0),
                      "rsi_edge": ParamSpec(2.0, 20.0), **_FADE_EXIT_PARAMS},
                     {"htf_rsi_edge": 20.0, "rsi_edge": 8.0, **_FADE_EXIT_BASE},
                     _htf_reversal_long_entry),
    StrategyTemplate("htf_reversal_short", "short", "1h",
                     {"htf_rsi_edge": ParamSpec(10.0, 30.0),
                      "rsi_edge": ParamSpec(2.0, 20.0), **_FADE_EXIT_PARAMS},
                     {"htf_rsi_edge": 20.0, "rsi_edge": 8.0, **_FADE_EXIT_BASE},
                     _htf_reversal_short_entry),
    # The fade exit space, because the entry is a reversal claim: it says the slow leg is
    # stretched and the fast one has turned, and that claim is COMPLETE when the stretch
    # unwinds. The mechanism-class rule the two spaces were split on, not a preference.
    StrategyTemplate("funding_momentum_long", "long", "1h",
                     {"funding_z_min": ParamSpec(1.0, 2.5), **_EXIT_PARAMS},
                     {"funding_z_min": 1.5, **_EXIT_BASE}, _funding_momentum_long_entry),
    StrategyTemplate("funding_momentum_short", "short", "1h",
                     {"funding_z_min": ParamSpec(1.0, 2.5), **_EXIT_PARAMS},
                     {"funding_z_min": 1.5, **_EXIT_BASE}, _funding_momentum_short_entry),
    # `flow_edge` is the single-bar imbalance, so its range is wider than `taker_flow_long`'s
    # 0.02-0.20 on the ROLLING MEAN of the same series — a mean is narrower than its inputs by
    # construction. The bounds are read off the definition rather than off a measurement: the
    # signed form puts 0.10 at "one side took 55% of the bar" and 0.40 at 70%, and above 70% the
    # family selects a tail thin enough to arrive unjudgeable, which is the `vol_pct_min` ceiling
    # argument. Nothing has measured this distribution; the search moves the bound.
    #
    # **The risk that carries, named rather than hidden.** The one installed family whose entry
    # is a RAW-SCALE flow threshold is the one that produces nothing: measured 2026-08-08 over
    # the candidate store, `taker_flow_long` has a median `closed_count` of **0** with 14 of its
    # 15 stored specs taking zero trades (`taker_flow_short` 7.5). The feed itself is not the
    # problem — `taker_absorption_*`, reading the z-score of this same series, medians 40 and 43
    # with no zero-trade spec at all — so the failure sits in thresholding a raw flow level. This
    # pair thresholds a raw flow level. What argues the other way is only mechanical: the
    # single-bar print has strictly wider dispersion than its own rolling mean, so the same
    # numeric bound selects far more bars. That was NOT confirmed on data — no archived candle
    # carries the aggressor split (`taker_buy_base` is null on every hyperliquid row), so the
    # distribution could not be measured offline. If the pair arrives FRAGILE for want of trades,
    # the bound is where to look first, and the z-score form is the sibling that works.
    StrategyTemplate("taker_flow_fade_long", "long", "1h",
                     {"flow_edge": ParamSpec(0.10, 0.40),
                      "rsi_max": ParamSpec(20.0, 40.0), **_FADE_EXIT_PARAMS},
                     {"flow_edge": 0.20, "rsi_max": 30.0, **_FADE_EXIT_BASE},
                     _taker_flow_fade_long_entry),
    StrategyTemplate("taker_flow_fade_short", "short", "1h",
                     {"flow_edge": ParamSpec(0.10, 0.40),
                      "rsi_min": ParamSpec(60.0, 80.0), **_FADE_EXIT_PARAMS},
                     {"flow_edge": 0.20, "rsi_min": 70.0, **_FADE_EXIT_BASE},
                     _taker_flow_fade_short_entry),
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

# Families whose entry rules read the funding columns — same gate as `OI_FAMILIES`, against a
# different series, and **it binds today at 1d**. The funding fetch reaches ~1,067 days
# (`market_data.funding_history_days`); 1h and 4h replay 1,000 and are covered, 1d replays
# `MIN_FACTORY_BARS` = 2,000 BARS = 2,000 days and is covered **53%**. Identical defect to the
# one `_oi_feed_reaches` was written for, on the identical timeframe, missed because that fix
# named only the OI series. Caught before it cost anything: the store holds 44 `funding_fade_*`
# candidates and **none at 1d** — 1d rejoined the rotation on 2026-08-04 and the cursor has not
# reached this block yet, which it would have within ~9 fires.
FUNDING_FAMILIES = frozenset({"funding_fade_long", "funding_fade_short",
                              "funding_momentum_long", "funding_momentum_short"})

# Families whose entry rules read HTF columns — mintable only where a higher
# timeframe exists to read (see ``market_data.HIGHER_TIMEFRAME``).
#
# Both the minted pair and the retired one are named, for the reason CROSS_SECTION_FAMILIES
# states below: the gate asks whether a family that reads `htf_*` may be minted here, which is
# a property of the COLUMNS and not of which pair currently reads them — so leaving the retired
# pair out would silently un-gate it the day somebody re-listed it.
HTF_FAMILIES = frozenset({"htf_trend_long", "htf_trend_short",
                          "htf_trend_strength_long", "htf_trend_strength_short",
                          "htf_pullback_long", "htf_pullback_short",
                          "htf_reversal_long", "htf_reversal_short"})

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
# families are built and tested now. **Since 2026-10-03 coverage alone no longer flips them on**
# (Thomas, `CRYPTO_ARCHIVE_BACKFILL_V0.1.md` R2): a backfill can fill the store in an afternoon, and
# minting a new family is research the review-D3 pause holds until 2027-03-22. The factory now gets
# `positioning_store.mint_eligible`, which is coverage AND `positioning_store.MINTING_DECIDED`.
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


def _replay_days(timeframe: str) -> float | None:
    """Calendar days the factory replays at ``timeframe``, or ``None`` for an unknown one."""
    minutes = market_data.TIMEFRAMES.get(str(timeframe))
    if minutes is None:
        return None
    return market_data.factory_candle_target(str(timeframe)) * minutes / 1440.0


def _funding_feed_reaches(timeframe: str) -> bool:
    """Does the funding series' depth span what the factory replays at ``timeframe``?

    :func:`_oi_feed_reaches` for a different series, and written because that one's docstring
    describes a failure the funding leg was one constant away from repeating. The OI gap went
    unseen for six days because the window moved (`MIN_FACTORY_BARS`) while the feed depth did
    not; funding is bound by **two** constants rather than one — the records asked for and what
    the pager can serve — so it has two ways to be left behind rather than one.

    **It binds now, at 1d.** `market_data.funding_history_days()` is ~1,067 days; 1h and 4h
    replay 1,000 and pass, while 1d replays `MIN_FACTORY_BARS` = 2,000 bars = 2,000 days and
    fails at 53% coverage. No 1d `funding_fade_*` candidate exists yet — the rotation cursor has
    not reached that block since 1d came back on 2026-08-04 — so this closes the defect one
    fire ahead of it rather than after, which is the difference from the OI case.

    What it prevents is specific and is **not** caught by `unsuppliable_features`: that refuses a
    column that is None on EVERY row, while a feed short of the window is populated on the newest
    part of it. The consequence is the one `_oi_feed_reaches` documents — every trade lands in
    the newest walk-forward slice, `temporal_consistency` is 0 by construction, and the family is
    retired FRAGILE for a window that had no data in it.
    """
    replay_days = _replay_days(timeframe)
    if replay_days is None:
        return False
    return replay_days <= market_data.funding_history_days()


# The longest hold whose own holdout can still produce a judgeable sample, in bars.
#
# **The generation spaces are written in BARS and the retiming only swaps the label.**
# `templates_for_timeframe` returns `replace(t, timeframe=timeframe)`, so `_EXIT_PARAMS`'
# `max_holding_bars` of 12-48 draws 12-48 HOURS at 1h and 12-48 DAYS at 1d — the same number
# standing for a calendar span 24x apart, which nobody chose. Measured 2026-08-06 over the 86
# rows minted on the current window, holds came out at 1h 0.4-1.4 days (median 1.0), 4h 1.0-6.0
# (2.8) and 1d 6-37 (**26**).
#
# What that costs is the tier's whole judgeability. A hold occupies its bars, so the most trades
# a holdout can close is `holdout_bars / max_holding_bars` — an exit-geometry ceiling the entry
# rule cannot lift:
#
#   | tf | holdout bars | median hold | ceiling | actual | ceiling used | ceiling < floor |
#   |----|--------------|-------------|---------|--------|--------------|-----------------|
#   | 1d |          600 |          26 |  **23** |     16 |          77% |         **55%** |
#   | 4h |        1,800 |          16 |     109 |     17 |          22% |              0% |
#   | 1h |        7,200 |          24 |     307 |   91.5 |          39% |              0% |
#
# At 1d the median ceiling is **below `MIN_HOLDOUT_TRADES` itself**, 55% of rows cannot reach the
# floor whatever they signal, and the 77% utilisation says the entry rule is already firing near
# that ceiling. 4h fails for the opposite reason — ceiling 109 against a floor of 25, and only
# 22% of it used — which is a signal-rate problem and NOT what this bound addresses.
#
# **Deepening the window is not available at 1d.** `MIN_FACTORY_BARS` already floors it at 2,000
# bars, and that constant's own note records why it cannot rise: the shortest routed history is
# ~2.1k daily bars (SOLUSDT), so a higher floor would score a window the venue never served.
# `REMAINING_WORK.md` F1's "re-measure `factory_candle_target` first" is spent here.
#
# **This is the narrow version, and the wide one is deliberately not taken.** Re-expressing the
# hold as a calendar span — the `factory_candle_target` precedent, which is the real fix for the
# cause — would move 4h and 1h as well, on an intent nothing in this repo recorded. This bound
# claims only what the table measures: a spec whose exit geometry makes its own holdout
# unjudgeable is the `_fuse_batch` "scored candidate that can never trade" defect one notch
# weaker, and refusing it at mint costs nothing that was ever judgeable. Same shape as
# `MAX_FUSION_ENTRY_CONDITIONS`, which removes only its own measured-zero band and leaves the
# validator's `MAX_HOLDING_BARS_RANGE` alone — that one answers "is this hold legal", this one
# answers "can the result be judged".
#
# It binds **1d only** on today's ladder (600 // 25 = 24 against a space topping at 48); 4h
# yields 72 and 1h 288, both above their spaces. Fusion needs no matching change: a child's hold
# is its parents' midpoint, so two capped parents cannot exceed the cap, and a pre-cap parent
# long enough to breach it cannot parent at all — `holdout_permits_parenting` requires the
# judgeable holdout its own geometry denies it.
def judgeable_holding_bars(timeframe: str) -> int:
    """The longest ``max_holding_bars`` whose holdout can still close ``MIN_HOLDOUT_TRADES``.

    Reads the window through the same two functions the replay does
    (:func:`market_data.factory_candle_target`, :func:`holdout_split_index`) rather than
    restating the split, so a change to either reaches this bound without being copied."""
    total = market_data.factory_candle_target(str(timeframe))
    holdout_bars = total - holdout_split_index(total)
    return max(1, holdout_bars // MIN_HOLDOUT_TRADES)


def _judgeable_hold_space(
    template: StrategyTemplate, cap: int,
) -> StrategyTemplate | None:
    """``template`` with its hold narrowed to ``cap``, or None if it cannot fit inside it.

    None means the family's SHORTEST legal hold already exceeds what this timeframe can judge,
    so every draw would be unjudgeable by construction — the same answer, for the same reason,
    that the feed gates above give a family whose columns this timeframe cannot supply. It does
    not fire on today's ladder (the shortest space starts at 4 against a 1d cap of 24) and is
    here so that a future window or floor cannot turn the bound into a space with ``lo > hi``."""
    spec = template.param_space.get("max_holding_bars")
    if spec is None or spec.hi <= cap:
        return template
    if spec.lo > cap:
        return None
    return replace(
        template,
        param_space={**template.param_space,
                     "max_holding_bars": replace(spec, hi=float(cap))},
        # The centre has to live inside its own space: `mutate_params` folds a centre outside
        # the bounds back in, so leaving it out would not produce an illegal draw — it would
        # aim the family somewhere nobody chose. Only this parameter moves.
        base_params={**template.base_params,
                     "max_holding_bars": min(template.base_params["max_holding_bars"], cap)},
    )


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
    has_funding_history = _funding_feed_reaches(timeframe)
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
        if template.family in FUNDING_FAMILIES and not has_funding_history:
            return False
        # Whole-family, not per-condition: a template is one premise, and one it can state
        # only half of is a different premise nobody chose to mine.
        if not template_features(template) <= mintable:
            return False
        return True

    # Applied on the way out rather than as another `_minted` clause, because it is not an
    # eligibility question: every family above may be minted here, and this narrows the SPACE
    # one of their parameters is drawn from. The None case is the exception and it drops the
    # family for the reason `_judgeable_hold_space` states.
    cap = judgeable_holding_bars(timeframe)
    retimed = (
        _judgeable_hold_space(replace(t, timeframe=timeframe), cap)
        for t in TEMPLATES if _minted(t)
    )
    return tuple(t for t in retimed if t is not None)


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
