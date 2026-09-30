"""C8 strategy factory — seeded generation, validation, backtest evidence, candidates.

Ports the source strategy factory's S2/S3 core (template library subset, seeded
parameter mutation, the pre-backtest validator) plus a replay backtest built from the
already-ported evaluator and settlement math — the source's own guarantee ("a strategy
behaves identically in backtest and live" because both share one evaluator and one
exit model) holds here by construction, since ``strategy.evaluate_spec`` and
``trade_plan.settle_trade_plan`` are exactly what the live cycle runs. The replay itself
lives in ``backtest.py`` and the template library and validator in ``template_space.py``;
both are re-exported here, and what is said below about them describes those modules.

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
same model in ``trade_plan.build_outcome_record``, so a paper expectancy and the one below are
finally the same kind of number (``cost.py`` records why the boundary moved). ``champion_score`` and
``expectancy`` are computed over the costed (net) R, so a strategy that only looks
good gross now scores accordingly; ``robustness.cost_robustness`` is measured for
real instead of always zero.
"""

from __future__ import annotations

import math
import random
from itertools import combinations
from typing import Any, Callable, Mapping, Sequence

from runtime.read_only_kernel import integrity

from . import features, indicators, market_data
from .cost import (
    FUNDING_INTERVALS_PER_DAY,
    FUNDING_SOURCE_FALLBACK,
    FUNDING_SOURCE_PARTIAL,
    FUNDING_SOURCE_VENUE,
    CostModel,
)
from .trade_plan import ASSUMED_LEVERAGE, MAINTENANCE_MARGIN_RATE, liquidation_price
from .candidate_identity import candidate_id, derive_candidate_id
from .robustness import MIN_HOLDOUT_TRADES
from .strategy import SCHEMA_VERSION, SpecParseError, StrategySpec
# The replay backtest lives in `backtest.py` (moved whole, refactor plan PR-08). Every name it defines
# is re-exported here as the same object, so `factory.backtest_spec` and the rest still resolve. A
# test that patches a name the replay reads patches it on `backtest`: a patch on this module's copy
# does not reach a function defined there.
from .backtest import (  # noqa: F401
    BACKTEST_WINDOWS, HOLDOUT_FRACTION, HOLDOUT_PERIODS, MIN_BARS_FOR_HOLDOUT,
    MIN_TRADES_PER_WINDOW, PRIOR_WINDOWS, UNSUPPLIABLE_FEATURE, WALK_FORWARD_MIN_PERIODS,
    WALK_FORWARD_PERIODS, ReplayFrame, _FUNDING_SOURCE_STRENGTH, _holdout_evidence,
    _prefix_frame, _prior_window_evidence, _replay, backtest_spec, backtest_spec_pooled,
    build_replay_frame, funding_charges_per_bar, holdout_split_index, unsuppliable_features,
)
# The template space lives in `template_space.py` (moved whole, refactor plan PR-09): the validator's
# bounds, the feature vocabulary, the template library and `validate_strategy`. Re-exported here as
# the same objects, under the same rule as the replay above: patch a name where the function that
# reads it is defined.
from .template_space import (  # noqa: F401
    CATEGORICAL_FEATURES, CROSS_SECTION_FAMILIES, FUNDING_FAMILIES, HTF_FAMILIES,
    MAX_ENTRY_CONDITIONS, MAX_FUSION_ENTRY_CONDITIONS, MAX_HOLDING_BARS_RANGE, MAX_RISK_PER_TRADE_R,
    MIN_REWARD_RISK, NUMERIC_FEATURES, OI_FAMILIES, POSITIONING_FAMILIES, ParamSpec,
    REFERENCE_FAMILIES, RETIRED_FAMILIES, SESSION_FAMILIES, STOP_ATR_RANGE, StrategyTemplate,
    TARGET_ATR_RANGE, TEMPLATES, _CATEGORICAL_COMPARISONS, _EXIT_BASE, _EXIT_PARAMS,
    _FADE_EXIT_BASE, _FADE_EXIT_PARAMS, _FEATURE_FEED, _GENERATION_SPACES, _NUMERIC_COMPARISONS,
    _REGIME_VALUES, _bollinger_breakdown_short_entry, _bollinger_breakout_entry,
    _breakdown_short_entry, _breakout_entry, _funding_fade_long_entry, _funding_fade_short_entry,
    _funding_feed_reaches, _funding_momentum_long_entry, _funding_momentum_short_entry,
    _htf_pullback_long_entry, _htf_pullback_short_entry, _htf_reversal_long_entry,
    _htf_reversal_short_entry, _htf_trend_long_entry, _htf_trend_short_entry,
    _htf_trend_strength_long_entry, _htf_trend_strength_short_entry, _judgeable_hold_space,
    _ma_cross_down_entry, _ma_cross_up_entry, _macd_cross_down_entry, _macd_cross_up_entry,
    _macd_momentum_entry, _macd_momentum_short_entry, _mean_reversion_long_entry,
    _mean_reversion_short_entry, _oi_feed_reaches, _oi_squeeze_long_entry, _oi_squeeze_short_entry,
    _oi_unwind_long_entry, _oi_unwind_short_entry, _positioning_divergence_long_entry,
    _positioning_divergence_short_entry, _premium_fade_long_entry, _premium_fade_short_entry,
    _rel_strength_long_entry, _rel_strength_short_entry, _replay_days, _session_label,
    _session_trend_long_entry, _session_trend_short_entry, _taker_absorption_long_entry,
    _taker_absorption_short_entry, _taker_flow_fade_long_entry, _taker_flow_fade_short_entry,
    _taker_flow_long_entry, _taker_flow_short_entry, _trend_pullback_entry,
    _trend_pullback_short_entry, _volatility_expansion_long_entry,
    _volatility_expansion_short_entry, _volatility_squeeze_long_entry,
    _volatility_squeeze_short_entry, _xs_momentum_long_entry, _xs_momentum_short_entry,
    _xs_reversion_long_entry, _xs_reversion_short_entry, judgeable_holding_bars, known_features,
    template_features, templates_for_timeframe, validate_strategy,
)

DEFAULT_BATCH_SIZE = 4
_MUTATION_SCALE = 0.35

# Which accept-index slots (mod 4, after `_elite_flip`) centre their stop_atr draw on the
# family's own stop CEILING instead of the learned/template centre. One slot of each parity —
# 1 is a base-centred draw, 2 an elite-centred one — so the probe rides both halves of the
# exploration split and every other parameter keeps the centre that half would have used.
#
# This exists because the ratchet cannot reach the region the evidence points at. The
# 2026-08-25 controlled re-backtests found per-trade cost falls steeply with stop width and
# individual 1h specs turning +0.28R NET around stop 2.6-3.2 (RR preserved) — and #782 widened
# the generation space to (1.2, 3.0) to make that reachable. Measured 15 generations later
# (GEN-857~871, 2026-08-30): batch stop_atr median still 1.5-1.7, maximum 2.07, ZERO draws
# above 2.1. The mechanism is arithmetic: a draw spans centre +/- 0.35 x (hi - lo) = +/- 0.63,
# so nothing above 2.37 can be minted unless a centre first walks past ~1.74 — and centres are
# picked by `champion_score`, whose correlation with stop_atr over those generations is -0.075
# (robustness is deliberately blind to cost-in-R, `score_robustness`). The score will never
# walk the centre there, so the space #782 opened stays unexplored forever without a forced
# coverage slot. A probe draw folds into the top scale of its own family's interval
# ([2.37, 3.0] for the trend space; the fade space's ceiling deliberately stays 2.0 and its
# probe respects that — see `_FADE_EXIT_PARAMS` for why a wide stop is wrong for a fade).
#
# Coverage, not preference: probe rows pass the same validation, backtest, ablation and entry
# bar as any draw, and no score or ranking term changes with them. If the sweep's finding
# holds pooled, their evidence promotes them on the existing doors; if it does not, the store
# records that at ~2 draws per fire. Identify probe rows by the band, not a flag — a mint
# provenance field would widen a closed schema for a question `stop_atr >= 2.37` answers.
_EXIT_PROBE_SLOTS = (1, 2)

# What fraction of a context's bars must be able to CARRY the probe's stop for the slot to be
# worth spending. The probe re-centres on a ceiling; if `trade_plan.stop_is_beyond_liquidation`
# then refuses most entries at that width, the slot mints a row with no evidence instead of a
# wide-stop datapoint — which is what the first harvest did on 1d (2026-08-31, GEN-873:
# `refused_entries` 2,428-4,796 against 198 for the same families' narrow rows, and 0/1/1/2
# closed trades against 15-41).
#
# **0.5 is calibrated against that harvest rather than chosen for tidiness**, because the
# criterion that matters is judgeable evidence, not comfortable admission. The same fire's 4h
# probes were refused heavily too — 407 and 168 refusals at stop 2.96 and 2.62 — and still
# closed 139 and 119 trades, one of them clearing the observation entry bar at +0.114R while
# every ordinary draw of those three generations failed it. A rule that admits "most" entries
# would have forbidden both. Measured on 600 live bars per leg (2026-08-31), the cohort
# ceiling by quantile, against a generation space of [1.2, 3.0]:
#
#     admit      0.90    0.70    0.50    0.30
#     1h         2.49    3.60    7.05    9.22   (non-binding at 0.5; 1h refuses ~nothing)
#     4h         1.56    2.04    2.75    3.34   (0.7 forbids BOTH rows that worked)
#     1d         0.46    0.62    0.73    0.85   (under the space FLOOR at every quantile)
#
# So 0.5 — "more bars carry it than refuse it" — is the only stated rule that keeps the 4h
# band that produced evidence and drops the 1d band that produced none.
#
# **What this exposes and deliberately does not fix:** on 1d the admissible width is 0.46-0.94
# against a space that starts at 1.2, so at `ASSUMED_LEVERAGE` every 1d row this factory can
# mint is one the guard refuses on most bars — which is a candidate explanation for that
# tier's chronically thin evidence (median 12 closes), not just for the probe's. The assumed
# leverage is a money-path constant and moving it is Thomas's decision, not a side effect of
# a search-coverage change.
PROBE_LIQUIDATION_ADMIT_FRACTION = 0.5

# How far under the quantile's width the ceiling sits, relatively. Only large enough to clear
# the guard's `<=` boundary and float noise in the price-space comparison it makes there.
_LIQUIDATION_CEILING_STEP = 1e-6


def liquidation_admissible_stop_atr(
    candles: Sequence[Mapping[str, Any]], *,
    leverage: float = ASSUMED_LEVERAGE, mmr: float = MAINTENANCE_MARGIN_RATE,
    admit: float = PROBE_LIQUIDATION_ADMIT_FRACTION,
) -> float | None:
    """The widest ``stop_atr`` that ``admit`` of these bars can carry, or None if unknowable.

    A stop sits ``stop_atr x ATR`` from entry, and `trade_plan.stop_is_beyond_liquidation` refuses
    it once that distance reaches the isolated-margin liquidation price. So per bar the widest
    legal multiple is ``room x close / ATR``, where ``room`` is the liquidation distance as a
    fraction of entry — **read out of `trade_plan.liquidation_price` itself rather than re-derived
    here**, so the two can never disagree about what the guard does.

    Returns the multiple that ``admit`` of the bars are at or above (the ``1 - admit``
    quantile), which is a floor on coverage rather than a promise: a bar more volatile than
    that still refuses. None when there is no usable ATR yet (a short series), and callers
    treat None as "no ceiling known" rather than as zero — an unknown must not silently
    become a refusal to probe.

    Pure and deterministic given the candles, so a replay reproduces the same ceiling.
    """
    if leverage <= 0 or not 0.0 < admit <= 1.0:
        return None
    room = 1.0 - liquidation_price(1.0, "LONG", leverage=leverage, mmr=mmr)
    if room <= 0:
        return None
    highs = [c.get("high") for c in candles]
    lows = [c.get("low") for c in candles]
    closes = [c.get("close") for c in candles]
    if not closes:
        return None
    atr_series = indicators.atr(highs, lows, closes, features.ATR_PERIOD)
    admissible = [
        room * float(close) / float(atr)
        for close, atr in zip(closes, atr_series)
        if isinstance(close, (int, float)) and not isinstance(close, bool)
        and isinstance(atr, (int, float)) and not isinstance(atr, bool)
        and float(atr) > 0 and float(close) > 0
    ]
    if not admissible:
        return None
    admissible.sort()
    index = int(round((1.0 - admit) * (len(admissible) - 1)))
    # Strictly BELOW the quantile's own width, because the guard refuses at equality
    # (`stop_price <= liq`): a ceiling sitting exactly on a bar's admissible width is refused
    # on that bar, and on a series with ties — a clustered ATR, which a smoothed series has
    # plenty of — that is not a rounding detail but a large share of the bars. Measured while
    # building this: a ceiling taken as the raw quantile admitted 60% where it promised 90%.
    # The step is relative and far below any width the search can distinguish.
    return admissible[index] * (1.0 - _LIQUIDATION_CEILING_STEP)


def cohort_probe_stop_ceiling(
    legs: Sequence[Mapping[str, Any]], **kwargs: Any
) -> float | None:
    """The probe ceiling for a pooled mint: the MOST BINDING leg's, not the primary's.

    One pooled spec is scored across every leg, so a stop the cohort's most volatile symbol
    cannot carry is refused there however comfortable the primary is. Legs that cannot say
    (no usable ATR) are skipped rather than treated as unbounded — an unknown leg must not
    raise the ceiling the known ones set.
    """
    known = [
        ceiling for ceiling in (
            liquidation_admissible_stop_atr(leg.get("candles") or [], **kwargs) for leg in legs
        ) if ceiling is not None
    ]
    return min(known) if known else None

# What its name says, and it did not until 2026-08-05: the retry budget is `count x` this, and
# `generate_batch` re-drew the SAME template on every refusal because the template is picked by
# the ACCEPT index, which a refusal does not move. So the number bounded the fire and not the
# spec, and a family that could not produce an acceptable draw spent all 48 attempts alone while
# the three slots behind it were never tried — the fire minted nothing rather than three.
#
# **Never observed, and the guard is not a claim that it was.** Every one of the 185 fires in the
# store minted exactly `count`, and the loop is byte-identical on that path (the cursor advances
# only on acceptance, so it equals `len(accepted)`). What makes it worth closing anyway is the
# failure MODE rather than its rate: the cost is the whole fire, the cause is invisible in
# `generated=N` unless N is compared with what was asked, and the one time this mechanism did
# bite — an adverse elite centre refusing a third of its own draws on the reward:risk floor — it
# was found by measuring the store, not by anything reporting it. See
# `test_an_adverse_elite_centre_no_longer_spends_a_batch_on_refusals`, which records that the
# batch "does not fail ... which is why this went unnoticed".
_MAX_ATTEMPTS_PER_SPEC = 12


# --- S2 generator (seeded, verbatim mechanics) --------------------------------

def mutate_params(
    base_params: dict[str, float], param_space: dict[str, ParamSpec], rng: random.Random,
    *, scale: float = _MUTATION_SCALE, overrides: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Perturb each parameter within a fraction of its range, clamped to bounds.

    Every parameter is drawn independently EXCEPT the exit pair — see
    :func:`_apply_reward_risk_floor` for the one coupling and why it lives here.

    **An UNORDERED parameter is drawn uniformly instead, and ignores the centre.** Perturbing
    one is a category error with a measurable cost: `session_index` spans [0, 2] and the
    perturbation is `base +/- (hi - lo) * 0.35` = `1 +/- 0.7`, so rounding put **71.3%** of draws
    on the base's own value and 14.3% on each of the others. The store agrees almost exactly —
    of 67 `session_trend_*` specs ever minted, **EUROPE 76.1%, ASIA 14.9%, US 9.0%**, which is
    six specs of evidence for the US session against fifty-one for Europe. The comment on
    `CATEGORICAL_FEATURES["session"]` says which session a spec claims is "part of the seeded
    search and is charged as a free parameter"; it was charged as one and searched as an eighth
    of one.

    Uniform rather than centred, because a centre is a claim about a NEIGHBOURHOOD and these
    values have none — "near EUROPE" is not a session. That also removes a ratchet: the elite
    centre reads the best prior candidate, the oversampled label wins on volume, and centring on
    it would re-spend the next generation in the same place for a reason that is an artifact of
    the draw. The robustness scorer charges the parameter either way.
    """
    out: dict[str, float] = {}
    for name, spec in param_space.items():
        if not spec.ordered:
            out[name] = rng.randint(int(spec.lo), int(spec.hi))
            continue
        # `overrides` re-centres THIS name's draw without copying the centre dict — the exit
        # probe's channel (`_EXIT_PROBE_SLOTS`). A parameter-level argument rather than a
        # modified `base_params`, because the centre object's identity says which half of the
        # exploration split chose it and a wrapped copy would erase that. Centring on a bound
        # is well-defined here: `_fold_into_bounds` reflects the overshoot back inside, so the
        # draw spans the `scale`-sized strip against that bound instead of collapsing onto it.
        base = base_params[name] if overrides is None else overrides.get(name, base_params[name])
        span = (spec.hi - spec.lo) * scale
        val = _fold_into_bounds(base + rng.uniform(-span, span), spec.lo, spec.hi)
        out[name] = int(round(val)) if spec.integer else round(val, 4)
    _apply_reward_risk_floor(out, base_params, param_space, rng, scale=scale)
    return out


def _fold_into_bounds(value: float, lo: float, hi: float) -> float:
    """Reflect ``value`` back inside ``[lo, hi]`` instead of clamping it to the bound.

    **The clamp this replaced did not bound the distribution, it collapsed part of it onto one
    number.** A draw is ``base +/- (hi - lo) * scale``, so any centre nearer a bound than its own
    span sends everything that overshoots to exactly that bound — one value, one rule hash, a
    parameter that has stopped varying rather than shifted. Measured 2026-08-05 across the
    library: **16 (parameter, space, base) combinations do it from their own template base**,
    covering 114 of 170 template parameter slots — ``target_atr`` 18.8%, ``flow_ma_min`` and
    ``rel_min`` 18.3%, ``xs_dispersion_min`` 14.3%, ``htf_sep_min`` 11.9%, ``oi_change_min``
    9.2%, ``flow_z_min`` 8.0%, ``stop_atr`` 5.4%, and nine more at 2.4%.

    **And it ratcheted.** ``generate_batch`` centres half of every batch on
    :func:`elite_base_params`, the most ROBUST prior row — so a pinned row becomes a centre ON
    the bound, and a centre on the bound sends *half* of its own children back to it. Measured
    over the 48 real elite centres this store currently supplies for the trend space: 7 (14.6%)
    sit exactly on ``target_atr``'s 1.6 floor, two of them re-pin >50% of their draws, sixteen
    more re-pin 20-50%, and the mean floor-pin over all of them is 15.0%.

    That ratchet is why the fix is here rather than in a base. Moving ``_EXIT_BASE`` up — what
    the note over ``_EXIT_PARAMS`` proposed before this was measured — only ever reached the
    template half: it takes the template-base pin 14.5% -> 0.0% and leaves the elite half at
    15.0%, while moving the median drawn target 3.12 -> 3.86 and the median R:R 2.14 -> 2.66.
    Half the effect, bought with a re-aiming of the trend geometry toward the band this store
    measures as its *worst* (see :func:`_apply_reward_risk_floor` for that table).

    Folding fixes both halves and re-aims nothing. Measured over the same centres: floor pin
    14.5% -> 0.0% from the template base and 15.3% -> 0.0% from the real elite centres, with the
    median target moving 3.12 -> 3.03 and 3.22 -> 3.25 and the median R:R 2.14 -> 2.08 and
    2.13 -> 2.18 — all inside the 0.05R-equivalent nothing in this store resolves.

    **Truncating was the alternative and it re-aims.** Drawing uniformly on the window
    intersected with the space (what :func:`_apply_reward_risk_floor` does, correctly, for a
    constraint that is a hard floor) moves the mean from a centre of 3.0 to 3.42, and from a
    centre sitting at 1.6 to 2.72. A mutation is a claim about a NEIGHBOURHOOD; truncation
    answers a different question near a bound, and the whole point here is to change the shape
    of the draw without changing where it aims.

    A fold rather than a single reflection, so a span wider than the interval cannot land
    outside it — the library has no such space today (the widest is ``max_holding_bars``, span
    12.6 against a width of 36) but a future one costs nothing to be right about. Consumes no
    ``rng`` call of its own, so the seeded-batch reproduction rule is untouched."""
    width = hi - lo
    if width <= 0:
        return lo
    offset = (value - lo) % (2 * width)
    return lo + (offset if offset <= width else 2 * width - offset)


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
    """Redraw ``target_atr`` above ``MIN_REWARD_RISK x`` the drawn ``stop_atr``, in place.

    **Inert for every space in the library since 2026-08-25**, when the exit pair moved to
    (stop, reward_risk) and the floor became structural — a ratio drawn from a range whose
    `lo` IS `MIN_REWARD_RISK` cannot produce an illegal pair, so there is nothing to redraw.
    Kept rather than deleted because it is not a property of the exit spaces: it is what
    `mutate_params` owes any space that draws a `target_atr` against a `stop_atr`, and a
    future one may. It returns immediately when `target_atr` is not a drawn parameter.
    """
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
# **And never a region the holdout already refuted**, which this rule did not say until
# 2026-08-05 and needed to: `champion_score` is computed on the SCORED window, so it is blind to
# the tail by construction, and the maximum over a growing store therefore landed on a row the
# tail had refuted in **227 of 461 contexts (49.2%)**, median holdout expectancy -0.2529R. An
# anti-overfit score that cannot see the out-of-sample evidence is not a defence against
# out-of-sample failure. `holdout_permits_centring` is the filter and records why it fails OPEN
# on an unjudged tail where `holdout_permits_parenting` fails closed.
#
# **Half the draws stay home.** Half of every batch draws around the elite centre and half around
# the template's own base, so the search cannot collapse onto one point however good that point
# looks, and the region the template was written for is never abandoned.
#
# **WHICH half is decided per fire, and it has to be, because the accept index alone locked it.**
# The rule was `len(accepted) % 2 == 0`, and the template is picked with
# `templates[(offset + len(accepted)) % total]` — the same counter. `offset` is always a multiple
# of `count` (4), and `total` is always EVEN because every family is minted as a long/short pair,
# so `offset` is always even and a template's index parity equals its accept parity. The
# assignment was therefore FIXED for the life of the context instead of alternating. Measured
# 2026-08-05 over 60 consecutive fires: **no family of any context ever saw both centres** — 1h
# ETHUSDT 18 elite-only against 18 base-only, 4h ETHUSDT 18/18, 1h BTCUSDT 17/17, 1d ETHUSDT
# 13/13, and BOTH = 0 everywhere.
#
# Both halves lost their guarantee, in opposite directions. The elite half had no anchor left and
# hill-climbed with nothing holding it to the template's region. The base half could not learn at
# all — and could not reach its own space either: `mutate_params` spans `base +/- 0.35 * (hi - lo)`
# from a fixed centre, so from `_EXIT_BASE` a draw stops at `stop_atr` 1.73 of [1.2, 2.0] and
# `target_atr` 5.24 of [1.6, 8.0]. Half the library could never mint the outer third of the exit
# geometry, and the only thing that would have shown it is the measurement above.
#
# :func:`_elite_flip` is the fix — a stable hash of the generation id chooses which parity takes
# the elite centre this fire. Still deterministic, still reproducible from a recorded input (the
# generation id is on every candidate), still exactly half of each batch, and it does not consume
# from the rng, so a family drawing around its base draws what it drew before. What it removes is
# the arithmetic coincidence: the flip depends on neither `count` nor `total` nor their gcd, so no
# library size can re-lock it. Repairing this with parity arithmetic instead — stepping the split
# on `rotation_index` — re-locks at `total = 24`, which is 1d/BTCUSDT with positioning ineligible
# and therefore a live context, not a hypothetical one.
#
# What keeps this honest is the two gates that landed first: #438's holdout interval and #440's
# selection-adjusted ranking, which charges the attempt count that concentrating the search will
# raise. The open risk is A2 — the holdout tail is shared, so a region that got lucky on it can
# be confirmed repeatedly. That is not closed here and concentrating the search makes it matter
# more, which is the honest reason it is written down rather than left implicit.
ELITE_EVIDENCE_MIN_TRADES = 20


def holdout_permits_centring(record: Mapping[str, Any]) -> bool:
    """May this row say where the search looks next for its family and context?

    Everything except a holdout that is judgeable and NEGATIVE. `champion_score` is the
    anti-overfit score but it is computed on the SCORED window, so it cannot see the tail — and
    taking the maximum over a growing store lands on a row the tail refuted about half the time.
    Measured 2026-08-05 over the 461 (family, timeframe, symbol) contexts the store can centre:
    **227 of them, 49.2%, were centred by a candidate whose out-of-sample expectancy is
    judgeably negative**, median -0.2529R and reaching -0.8225R. Only 43 were centred by a row
    with a positive holdout. Concentrating the next generation's draws on a region the tail has
    already refuted is the "hill-climbing converges on the noise" hazard the note above
    :data:`ELITE_EVIDENCE_MIN_TRADES` names, arriving through the one input nobody checked.

    **Fail-OPEN on absence, which is the opposite of :func:`holdout_permits_parenting`, and the
    asymmetry is the point.** A fused child CITES its parents' evidence, so breeding from a row
    whose tail nobody could judge propagates a claim nobody made — fail closed. A centre claims
    nothing: it says where to look, and whatever is minted there earns its own evidence and
    passes every gate unchanged. Refusing the unjudged here would cost the mechanism rather than
    protect it — the same store keeps 273 of 461 contexts centred under this rule and **67 under
    the parenting one**, and a context with no centre is the repeated sampling the elite search
    exists to end.

    So the two doors read the same field and answer different questions, which is why this is a
    second predicate rather than an argument on the first.
    """
    holdout = (record.get("backtest_evidence") or {}).get("holdout")
    if not isinstance(holdout, Mapping):
        return True
    closed, expectancy = holdout.get("closed_count"), holdout.get("expectancy")
    if isinstance(closed, bool) or not isinstance(closed, (int, float)):
        return True
    if isinstance(expectancy, bool) or not isinstance(expectancy, (int, float)):
        return True
    if closed < MIN_HOLDOUT_TRADES:
        return True
    return expectancy >= 0


def _matches_context(
    spec: Mapping[str, Any], *, symbol: str, timeframe: str,
    scope: Sequence[str] | None = None,
) -> bool:
    """Is this stored row a prior fire of the context being mined?

    **Membership when mining one symbol, exact equality when mining a cohort**, and the
    difference is a decision rather than a detail. A pooled hypothesis fitted across five
    symbols and a single-symbol one are not the same search: F2 measured that transferring a
    single-symbol fit to five is strictly harder than searching for parameters that hold across
    five from the start, so a single-symbol elite is the wrong centre to hand a pooled draw, and
    a single-symbol fire is not a step of the pooled rotation. `candidate_ranking.search_context_key` already
    keys the selection correction the same way — `(symbol_scope tuple, timeframe)`.

    The cost is stated rather than discovered: a pooled context starts with NO centre and no
    rotation history, so its first fires draw from the template base. That is what
    `elite_base_params`'s fallback already does for any new context, and F2's own pooled batch
    was unsteered anyway.
    """
    if spec.get("timeframe") != timeframe:
        return False
    row_scope = spec.get("symbol_scope")
    if not isinstance(row_scope, (list, tuple)):
        return False
    if scope is not None:
        return tuple(str(s) for s in row_scope) == tuple(str(s) for s in scope)
    return symbol in row_scope


def _best_mint_params(
    candidates: list[Mapping[str, Any]], *, symbol: str, timeframe: str,
    scope: Sequence[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """The most ROBUST prior candidate's ``mint_params``, per family, in ONE pass.

    The shared half of :func:`elite_base_params` and :func:`elite_centres`, which differ only in
    how many families they are asked about. Kept as one function because the filter IS the
    definition of "what may move a search centre" — a second copy of it is a second thing that
    can be wrong, and it would be wrong silently, since a centre that failed to move looks
    exactly like a family with nothing to learn from.

    Raw params, unprojected: which keys survive is the caller's question, because only the
    caller holds the template whose base supplies the missing ones.
    """
    best_score: dict[str, float] = {}
    best_params: dict[str, dict[str, Any]] = {}
    for record in candidates:
        spec = record.get("strategy_spec")
        # A malformed row is skipped rather than fatal (the `count_unreviewed_backlog` posture):
        # `.get` on a non-Mapping raises, and a factory fire must not die on one bad stored row.
        if not isinstance(spec, Mapping):
            continue
        family = spec.get("strategy_family")
        if not isinstance(family, str) or not family:
            continue
        if not _matches_context(spec, symbol=symbol, timeframe=timeframe, scope=scope):
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
        # `champion_score` cannot see the holdout, so the maximum over the store lands on a row
        # the tail refuted about half the time. See `holdout_permits_centring`.
        if not holdout_permits_centring(record):
            continue
        # `>` not `>=`: on a tie the EARLIER record wins, so the centre does not drift with
        # store order among equals.
        if family not in best_score or score > best_score[family]:
            best_score[family] = float(score)
            best_params[family] = {str(k): v for k, v in params.items()}
    return best_params


def _project(best: Mapping[str, Any] | None, fallback: Mapping[str, float]) -> dict[str, float]:
    """A stored centre reduced to the keys the template still declares.

    A param the family dropped must not be resurrected from an old record, and one it gained
    must come from the template's own base.
    """
    if not best:
        return dict(fallback)
    return {name: best.get(name, fallback[name]) for name in fallback}


def elite_base_params(
    candidates: list[Mapping[str, Any]], *, family: str, symbol: str, timeframe: str,
    fallback: dict[str, float], scope: Sequence[str] | None = None,
) -> dict[str, float]:
    """The params of the most ROBUST prior candidate for this family and context.

    ``fallback`` (the template's own base) whenever there is nothing better to say: no prior
    candidate here, none carrying `mint_params`, or none with enough closed trades for its score
    to mean anything. A centre moved on three trades is not a lesson.

    Pure and deterministic given the store, which is what keeps a replay reproducible — the
    centre is derived from recorded evidence, never from wall-clock or draw order.

    The single-family form. A caller wanting centres for a whole rotation wants
    :func:`elite_centres`, which answers the same question for every family at once and reads
    the store once to do it.
    """
    best = _best_mint_params(candidates, symbol=symbol, timeframe=timeframe, scope=scope)
    return _project(best.get(family), fallback)


def elite_centres(
    candidates: list[Mapping[str, Any]], templates: Sequence[StrategyTemplate], *,
    symbol: str, timeframe: str, scope: Sequence[str] | None = None,
) -> dict[str, dict[str, float]]:
    """Every template's search centre, keyed by family, from ONE pass over the store.

    Same answer as calling :func:`elite_base_params` per template and the same fallback rule;
    what changes is the cost. `run_factory` needs a centre for the whole rotation, and the
    per-family form re-read the entire candidate store for each one — 38 full passes over a
    1,140-row store per fire, of which the batch then uses at most `count`.

    The templates are passed rather than re-derived here so this stays pure and so the caller
    cannot end up centring on a rotation different from the one it mints — `run_factory` used to
    build its centres from an unnarrowed `templates_for_timeframe`, which returned families
    `generate_batch` would never reach for that symbol.
    """
    best = _best_mint_params(candidates, symbol=symbol, timeframe=timeframe, scope=scope)
    return {t.family: _project(best.get(t.family), t.base_params) for t in templates}


def _elite_flip(generation_id: str) -> int:
    """Which parity of this batch draws around the elite centre — 0 or 1.

    A stable hash of the fire's own identity, for the reason recorded above
    :data:`ELITE_EVIDENCE_MIN_TRADES`: the accept index alone is the SAME counter the template
    is picked with, so using it for both fixed each family's centre permanently instead of
    alternating it. This depends on neither `count` nor `total`, so no library size re-locks it.

    Deterministic and reproducible from a recorded input — `generation_id` rides on every
    candidate — so a replay of a fire reproduces its split exactly. The `context_rotation_phase`
    construction, and stable for the same reason: a counter would drift when the store is pruned
    or re-imported, and this must not.
    """
    digest = integrity.short_id("crypto_elite_split", {"generation_id": str(generation_id)})
    return int(digest.rsplit("_", 1)[1][:8], 16) % 2


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
            # Derived, not drawn — see `_EXIT_PARAMS`. Rounded to the same 4-decimal grid
            # `mutate_params` puts every other parameter on, so a spec's exit geometry is
            # comparable across mints however it was reached.
            "target_atr": round(params["stop_atr"] * params["reward_risk"], 4),
            "max_holding_bars": int(params["max_holding_bars"]),
        },
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
        "created_by": "mvp_factory",
        "venue": venue,
    }


def context_rotation_index(
    existing_candidates: list[Mapping[str, Any]], *, symbol: str, timeframe: str,
    scope: Sequence[str] | None = None,
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
        if not _matches_context(spec, symbol=symbol, timeframe=timeframe, scope=scope):
            continue
        # A trial is not a step of any rotation. It carries the fire's generation id, but it is
        # scoped to the whole cohort, so at 1h the single-symbol contexts match it by membership
        # and would count a BTC fire's id as their own step.
        if is_trial(record):
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
    symbol_scope: Sequence[str] | None = None,
    probe_stop_ceiling: float | None = None,
) -> dict[str, Any]:
    """Produce ``count`` validated, distinct candidate specs (source mechanics).

    ``symbol_scope`` widens the minted specs from ``[symbol]`` to a cohort — one hypothesis at
    N symbols' data rather than N hypotheses at one symbol's each. ``symbol`` still selects the
    rotation slice and the templates (`templates_for_timeframe` narrows for the market proxy),
    so it stays the cohort's first member rather than becoming meaningless.

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
    # Which parity of this batch takes the elite centre. Per FIRE rather than fixed, because
    # `offset` is a multiple of `count` and `total` is always even, so the accept index alone
    # gave every family the same centre forever — see `_elite_flip` and the note above
    # `ELITE_EVIDENCE_MIN_TRADES` for the measurement.
    flip = _elite_flip(generation_id)
    accepted: list[StrategySpec] = []
    accepted_params: dict[str, dict[str, float]] = {}
    validations: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_hashes: set[str] = set(known_rule_hashes)

    # The rotation cursor for THIS fire, and it is deliberately not `len(accepted)`. A family
    # that cannot produce an acceptable spec has to be steppable past: the accept index does not
    # move on a refusal, so the loop re-drew the SAME template until the whole budget was gone
    # and the slots behind it were never tried at all. One stuck family therefore cost the fire
    # every candidate, not one. See `_MAX_ATTEMPTS_PER_SPEC`.
    cursor = 0
    misses = 0

    def _refused(entry: dict[str, Any]) -> None:
        """Record a refusal, and step the cursor once a family has spent its own share."""
        nonlocal cursor, misses
        rejected.append(entry)
        misses += 1
        if misses >= _MAX_ATTEMPTS_PER_SPEC:
            cursor += 1
            misses = 0

    attempts = 0
    while len(accepted) < count and attempts < count * _MAX_ATTEMPTS_PER_SPEC:
        attempts += 1
        template = templates[(offset + cursor) % len(templates)]
        # Half the draws around what this family has already learned, half around where it
        # started, so the search cannot collapse onto one point however good that point looks.
        # Derived, not drawn — the split is reproducible from the generation id and consumes
        # nothing from `rng`. See `elite_base_params`.
        elite = (elite_params or {}).get(template.family)
        centre = (
            {name: float(elite.get(name, template.base_params[name]))
             for name in template.base_params}
            if elite and (len(accepted) + flip) % 2 == 0 else template.base_params
        )
        # A quarter of each half additionally probes the family's own stop ceiling — the
        # stop_atr draw alone re-centres on `param_space["stop_atr"].hi`; every other
        # parameter keeps the centre chosen above, so the probe changes exit geometry and
        # nothing else. An override handed to `mutate_params` rather than a copied centre,
        # because which half chose the centre stays legible from the centre OBJECT itself
        # (the split tests discriminate by identity, and a wrapped dict would read as a
        # third centre nobody picked). See `_EXIT_PROBE_SLOTS` for why coverage has to be
        # forced here rather than waited for from the elite ratchet.
        probe = None
        if (len(accepted) + flip) % 4 in _EXIT_PROBE_SLOTS and "stop_atr" in template.param_space:
            stop_space = template.param_space["stop_atr"]
            # The parameter space says what MAY be minted; the liquidation guard says what can
            # actually trade. Probing above the guard's admissible width spends the slot on a
            # row whose entries are refused rather than on a wide-stop datapoint — measured
            # 2026-08-31 on the first harvest, where 1d probes closed 0-2 trades against
            # 15-41 for the same families' narrow rows. `None` means the caller could not say,
            # and then the space's own ceiling stands (the pre-existing behaviour).
            ceiling = (stop_space.hi if probe_stop_ceiling is None
                       else min(stop_space.hi, float(probe_stop_ceiling)))
            # A ceiling at or under the family's own floor means this context cannot carry a
            # probe at all at the assumed leverage. Fall through to the ordinary centre rather
            # than mint at the floor: a "probe" indistinguishable from a normal draw would
            # report coverage this search does not have.
            if ceiling > stop_space.lo:
                probe = {"stop_atr": ceiling}
        params = mutate_params(centre, template.param_space, rng, overrides=probe)
        strategy_id = f"S{start_index + len(accepted):03d}"
        spec_dict = build_spec_dict(template, params, strategy_id=strategy_id,
                                    generation_id=generation_id, symbol=symbol, venue=venue,
                                    symbol_scope=list(symbol_scope) if symbol_scope else None)
        try:
            spec = StrategySpec.from_dict(spec_dict)
        except SpecParseError as exc:
            _refused({"strategy_family": template.family, "reason": f"parse: {exc}"})
            continue
        verdict = validate_strategy(spec)
        if not verdict["approved_for_backtest"]:
            _refused({"strategy_family": template.family, "block_reasons": verdict["block_reasons"]})
            continue
        if spec.strategy_rule_hash in seen_hashes:
            _refused({"strategy_family": template.family, "reason": "duplicate_rule_hash"})
            continue
        seen_hashes.add(spec.strategy_rule_hash)
        accepted.append(spec)
        accepted_params[spec.strategy_id] = dict(params)
        validations.append(verdict)
        # Only on acceptance, which is what keeps this identical to `len(accepted)` for every
        # fire that never exhausts a family — the 185 of 185 in the store today.
        cursor += 1
        misses = 0

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


# --- ablation lattice: a conjunction must beat its own parts ------------------
#
# `docs/proposals/FACTORY_ABLATION_V0.1.md` §3, approved as proposed (Thomas 2026-08-12).
# The generator searched "how many conditions make the score better", and that search
# passes stacked luck: of the 592 stored rows whose holdout could be judged at all, 592
# read CONTRADICTED. The lattice decomposes the luck at mint time — a full conjunction
# that cannot strictly beat its own best proper subset on the TRAIN segment is fit, not
# edge, and the best proper subset registers in its place.

# §3-1: k <= 3, so the lattice is at most 2^3 - 1 = 7 members. A hypothesis with MORE
# conditions stays on the existing path UNCHANGED: the seeded library mints up to k = 4
# today (the `oi_squeeze_*` pair below 1d) and a fused union may carry up to
# `MAX_FUSION_ENTRY_CONDITIONS` = 7 — a k = 4 lattice is 15 members, and §3-1 priced that
# as a cadence reallocation this increment deliberately does not take.
ABLATION_MAX_CONDITIONS = 3

# k = 1 skips the lattice entirely: a single condition has no proper subset to beat.
ABLATION_MIN_CONDITIONS = 2


def _train_frame(frame: ReplayFrame) -> ReplayFrame:
    """The frame's train segment as a frame of its own — the holdout bars are NOT in it.

    This is what makes the lattice's train-only rule structural rather than a convention
    (proposal §1-3): selection code handed this object cannot read a holdout bar, because
    the object does not contain one. Choosing a subset on the shared holdout would replay
    the A2 failure (holdout reuse) amplified by the lattice size, so the bars are removed
    at the door instead of avoided by discipline. ``split`` stays the full frame's, which
    after truncation equals ``len(rows)``: everything trains, nothing is held out.

    Deliberately NOT :func:`_prefix_frame`, which re-splits its prefix by
    :func:`holdout_split_index` and would carve a second holdout out of the train segment."""
    split = frame.split
    return ReplayFrame(
        rows=frame.rows[:split], candles=frame.candles[:split],
        funding=frame.funding[:split], funding_source=frame.funding_source,
        split=split, cost=frame.cost,
        symbol=frame.symbol,   # truncating the holdout away does not change which market it is
    )


def _subset_spec(spec: StrategySpec, indices: tuple[int, ...]) -> StrategySpec:
    """``spec`` carrying only the entry conditions at ``indices`` — all else identical.

    The stored rule hash is dropped so ``from_dict`` recomputes it: the conditions changed,
    so the identity did, and a subset that kept the hypothesis's hash would be refused as
    tampered."""
    as_dict = spec.to_dict()
    conditions = as_dict["entry_rules"]["conditions"]
    as_dict["entry_rules"] = {
        "operator": "AND", "conditions": [conditions[i] for i in indices],
    }
    as_dict.pop("strategy_rule_hash", None)
    return StrategySpec.from_dict(as_dict)


def _train_net_expectancy(spec: StrategySpec, train_frames: Sequence[ReplayFrame]) -> float:
    """Net expectancy of one lattice member over the train frames, pooled across legs.

    The same costed replay the scored window runs (`_replay` under each frame's own cost
    model — fees, slippage, funding carry), so the number a subset is selected on is the
    same KIND of number the winner is later scored on. 0.0 over zero closed trades,
    matching ``backtest_spec_pooled``'s convention for ``expectancy``."""
    total = 0.0
    closed = 0
    for frame in train_frames:
        outcomes, *_ = _replay(
            spec, frame.rows, frame.candles, cost=frame.cost, funding=frame.funding,
        )
        for outcome in outcomes:
            total += float(outcome["result_R"])
            closed += 1
    return round(total / closed, 8) if closed else 0.0


def ablate_hypothesis(
    spec: StrategySpec, frames: Sequence[ReplayFrame],
) -> tuple[StrategySpec, dict[str, Any]] | None:
    """Run the train-only ablation lattice over one drawn hypothesis.

    ``None`` when there is no lattice to run: k outside
    [:data:`ABLATION_MIN_CONDITIONS`, :data:`ABLATION_MAX_CONDITIONS`] (a single condition
    has nothing to ablate; a wider conjunction stays on the existing path by §3-1), or an
    OR hypothesis — dropping an OR member makes the rule STRICTER, the opposite of what a
    proper subset means under AND, so the lattice's reasoning does not transfer.

    Otherwise ``(winner, ablation_block)``. The selection rule is §3-2 as approved: the
    full conjunction wins only if its train net expectancy strictly beats EVERY proper
    subset's; otherwise the best proper subset wins, ties broken toward fewer conditions
    and then toward the enumeration order (sizes ascending, index-lexicographic within a
    size — deterministic, so a re-run selects identically). The winner is ``spec`` itself
    when the full conjunction wins, so its rule hash — the one the draw's duplicate guard
    already cleared — is untouched.

    Holdout bars cannot enter the selection by construction: every member is replayed over
    :func:`_train_frame` truncations, objects that do not contain the holdout. The winner's
    holdout is spent exactly once, by the ordinary full backtest the caller runs next.

    Consumes no randomness — the enumeration, the replays and the tie-breaks are pure
    functions of the spec and the frames, so the seeded draws after a lattice are the same
    draws they would have been without one."""
    conditions = spec.entry_rules.conditions
    k = len(conditions)
    if not (ABLATION_MIN_CONDITIONS <= k <= ABLATION_MAX_CONDITIONS):
        return None
    if spec.entry_rules.operator != "AND":
        return None
    if not frames:
        raise ValueError("an ablation lattice needs at least one frame to replay")
    cost = frames[0].cost
    for frame in frames:
        if frame.cost != cost:
            raise ValueError(
                "ablation frames were built under different cost models; a lattice mixing "
                "rates would select on numbers no one book was charged"
            )
    train = [_train_frame(frame) for frame in frames]
    # Every non-empty subset, the full set last: sizes ascending, index-lexicographic
    # within a size. This order is the tie-break of last resort below and the key order
    # of the evidence map, so it is the one deterministic enumeration, stated once.
    members = [combo for size in range(1, k + 1) for combo in combinations(range(k), size)]
    scores = {
        indices: _train_net_expectancy(
            spec if len(indices) == k else _subset_spec(spec, indices), train
        )
        for indices in members
    }
    full = members[-1]
    proper = members[:-1]
    full_beats = all(scores[full] > scores[indices] for indices in proper)
    if full_beats:
        winner_indices, winner = full, spec
    else:
        winner_indices = min(proper, key=lambda indices: (-scores[indices], len(indices), indices))
        winner = _subset_spec(spec, winner_indices)
    return winner, {
        "lattice_size": len(members),
        "hypothesis_conditions": [c.to_dict() for c in conditions],
        "winner_conditions": [c.to_dict() for c in winner.entry_rules.conditions],
        "full_beat_subsets": full_beats,
        # Keyed by "+"-joined indices into ``hypothesis_conditions``, in enumeration
        # order — compact, and enough to reconstruct which grid the winner survived.
        "train_net_expectancy_by_subset": {
            "+".join(str(i) for i in indices): scores[indices] for indices in members
        },
    }


def _lattice_winner(
    hypothesis: StrategySpec, frames: Sequence[ReplayFrame], *,
    seen_hashes: set[str], stats: dict[str, int],
) -> tuple[StrategySpec, dict[str, Any] | None, str | None]:
    """One drawn hypothesis through the lattice, returning what may register.

    ``(winner, ablation_block, refusal)``. The winner is the hypothesis untouched when
    there was no lattice to run (block and refusal both None). A swapped-in subset winner
    is re-checked here for the two guards its draw cleared with DIFFERENT conditions: the
    duplicate guard — the hash `generate_batch`/`fuse_specs` deduplicated was the full
    conjunction's, and a subset that already exists in the store must be refused, never
    re-minted (`known_rule_hashes`' own rule) — and the validator, unreachable for a
    subset of a validated AND conjunction (per-condition checks over fewer conditions,
    exits untouched) but kept because a typed refusal is cheaper than being wrong about
    "unreachable". On refusal nothing registers: the hypothesis already lost the lattice,
    and minting it anyway would register the exact spec the selection said was fit.

    ``stats`` counts what ablation did (fires report it): every lattice run, and every
    full conjunction that failed to strictly beat its best proper subset — counted at
    selection time, so a later refusal of the winner does not un-count the finding."""
    lattice = ablate_hypothesis(hypothesis, frames)
    if lattice is None:
        return hypothesis, None, None
    winner, block = lattice
    stats["lattices"] += 1
    if not block["full_beat_subsets"]:
        stats["luck_filtered"] += 1
    if winner.strategy_rule_hash != hypothesis.strategy_rule_hash:
        if winner.strategy_rule_hash in seen_hashes:
            return winner, block, "ablation_winner_duplicate_rule_hash"
        if not validate_strategy(winner)["approved_for_backtest"]:
            return winner, block, "ablation_winner_failed_validation"
    return winner, block, None


# --- fusion: crossover of two proven lineages ---------------------------------

# How many top-ranked lineages the pair search may draw from. A ceiling, not a
# quota: the caller's ``fusion_pairs`` decides how many children are actually minted.
FUSION_PARENT_POOL = 6

# Why a fire produced no fused children WITHOUT the fusion path having run. `run_factory` reports
# one of these in ``fusion_skipped``, or ``None`` when the path did execute — see the comment at
# the dispatch for why "it ran and found nothing" has to be distinguishable from "it never ran".
FUSION_NOT_REQUESTED = "not_requested"   # the caller passed fusion_pairs <= 0
# `pooled_fire` was a skip reason from 2026-08-10 to 2026-09-02: `_fuse_batch` scored on one
# frame, so a pooled mint could not fuse without minting a child scored on one leg while
# claiming five. It now takes the cohort's frames (the "second increment" the boundary named),
# so a pooled fire fuses like any other and the reason has no case left to name.
FUSION_SKIP_REASONS = frozenset({FUSION_NOT_REQUESTED})


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

    Same constants as :func:`mutate_params`, and since 2026-08-05 deliberately NOT the same
    operation: that function FOLDS at the bound (:func:`_fold_into_bounds`) because a draw of
    ``base +/- span`` systematically overshoots one, and stacking the overshoot on the edge is a
    delta rather than a bound. A fused value is a MIDPOINT, and a midpoint of two in-space parents
    is in-space by convexity — see
    ``test_fusion_cannot_carry_a_child_outside_the_space_it_mints_from`` — so this clamp fires
    only when a parent was minted under an OLDER space. Pinning such a parent to the nearest legal
    value is the right answer there; reflecting it would move it to an arbitrary interior point
    the search never chose. The two paths differ because the inputs differ.

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
    low, high = _EXIT_LEGAL_RANGE[name]
    if name == "target_atr":
        # `target_atr` is DERIVED from the drawn pair since 2026-08-25, so the region the
        # factory "currently explores" for it is the product's range rather than a bound of
        # its own. Same union over the generation spaces as every other exit parameter, and
        # the same question — a fused midpoint is held to what the factory could have minted.
        lo = min(s["stop_atr"].lo * s["reward_risk"].lo for s in _GENERATION_SPACES)
        hi = max(s["stop_atr"].hi * s["reward_risk"].hi for s in _GENERATION_SPACES)
        integer = False
    else:
        spec = _EXIT_PARAMS[name]
        lo = min(space[name].lo for space in _GENERATION_SPACES)
        hi = max(space[name].hi for space in _GENERATION_SPACES)
        integer = spec.integer
    midpoint = (first + second) / 2
    if low <= first <= high and low <= second <= high:
        midpoint = max(lo, min(hi, midpoint))
    return int(round(midpoint)) if integer else round(midpoint, 4)


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


# Which derivations may PARENT a child. An allowlist of its own rather than "anything the store
# admits", for the reason `pool_admission.PROMOTABLE_DERIVATION_TYPES` is one: a child is written
# as ``crossover``, which the promotion door takes, so a row the door refuses that could still
# parent would reach the live pool one generation later under a derivation nobody quarantined.
# ``hypothesis_trial`` (`pool_state.DERIVATION_TYPES`) is the row this stops. Its own literal
# because this layer cannot import the door's; `tests/test_mvp_runtime_crypto_promotable_derivation.py`
# pins the two equal, so widening one without the other fails there.
BREEDING_DERIVATION_TYPES = frozenset({"seeded_template", "crossover", "mutation"})


def may_breed(record: Mapping[str, Any]) -> bool:
    """Is this row's derivation one a child may cite as a parent?

    Absence passes — the legacy rule the door applies, since hundreds of stored rows predate the
    field. A row that names a derivation outside :data:`BREEDING_DERIVATION_TYPES`, including an
    explicit null, may not."""
    return ("derivation_type" not in record
            or record.get("derivation_type") in BREEDING_DERIVATION_TYPES)


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
# the judgeable subset the score is mildly anti-selective.
#
# **The evidence for adding the non-negative bit is the children already bred, and the obvious
# reading is circular.** That this predicate selects a pool whose median holdout is +0.091R, 100%
# of it non-negative, computes both figures over the very quantity the predicate filters on — it
# is arithmetic, not a finding, and it cannot show that the rows it keeps will BREED better. The
# test that can uses different rows on both sides: selection reads the PARENT's holdout, while the
# outcome measured is the CHILD's. Over the 447 already-bred children (#525 review), paired within
# (symbol, timeframe) because the groups do not share a tier — the qualifying side is 4h-heavy,
# the rest 1h-heavy, and R scale differs by tier — children of parents that pass this predicate
# beat children of parents that fail it by **+0.1151R median-based and +0.1674R trade-weighted,
# 5 of 6 comparable cells each way**. What bounds that: 6 cells at 2-8 rows per group; 38 of 330
# resolved parents are `mvp_rescore` rows, so "holdout as stored today" is not exactly the holdout
# the fusion saw at breeding time; and the smallest cell (BNBUSDT 15m) disagrees in sign between
# the two statistics. Against ~10 independent market periods it clears the resolution floor, but
# not by much.
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
#
# **Still owed: the split-half test**, which selects on the first half of the holdout periods and
# measures the last three — disjoint slices of the same tail, so no row is scored on the bars that
# selected it. It could not run when this landed: `period_r` / `period_trades` arrived with #518
# hours after the last factory fire, so no stored row carried a per-period breakdown to split. It
# becomes measurable once the store holds rows minted on that code.
def holdout_permits_parenting(record: Mapping[str, Any]) -> bool:
    """May this row breed, on its out-of-sample evidence alone?

    Reads the two facts a holdout block of any vintage carries: enough closed trades to be
    judged at all (``robustness.MIN_HOLDOUT_TRADES``, the same floor the promotion door uses),
    and a return that is not negative.

    Fail-closed on absence: no holdout block, an unreadable ``closed_count``/``expectancy``, or
    a block predating holdouts entirely means no out-of-sample evidence exists, which is not
    the same as passing — the rule `robustness.holdout_status` applies for UNCONFIRMED, applied
    here to the question of breeding rather than of verdict.

    :func:`holdout_permits_centring` reads the same field for the search centre and fails OPEN;
    read the two together before changing either — the difference is that a child cites its
    parents' evidence and a centre cites nothing."""
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
        # A quarantined derivation may not breed a promotable child (`may_breed`).
        if not may_breed(record):
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
    per_bucket: int = FUSION_PARENT_POOL, scope: Sequence[str] | None = None,
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
    # Membership when mining one symbol, exact equality when mining a cohort — the same rule
    # `_matches_context` applies to search centres, for the same reason: a pooled child is
    # scored across every leg and claims the whole cohort, so its parents must already have
    # been fitted across that exact cohort. A single-symbol parent that merely CONTAINS the
    # primary would fuse into a child claiming one symbol while five legs scored it — the
    # inverse of the wrong number the pooled boundary existed to prevent.
    cohort = tuple(sorted(str(s) for s in scope)) if scope is not None else None
    buckets: dict[tuple, list[dict[str, Any]]] = {}
    for record in rank_fusion_parents(existing_candidates, top_n=len(existing_candidates)):
        key = _fusion_bucket_key(record)
        if key is None:
            continue
        _schema, _direction, bucket_timeframe, bucket_scope, _stop = key
        if bucket_timeframe != timeframe:
            continue
        if cohort is not None:
            if bucket_scope != cohort:
                continue  # fitted on a different cohort, or on one symbol of this one
        elif symbol not in bucket_scope:
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
    frame: ReplayFrame | None = None, ablation_stats: dict[str, int] | None = None,
    frames: Sequence[ReplayFrame] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fuse parents pairwise, bucket by bucket, until ``pairs`` children carry evidence.

    ``frames`` (a cohort, primary first) makes this a POOLED fusion: every child and every
    parent-on-this-window is scored with `backtest_spec_pooled` across all of them, and the
    lattice slices its train segment from all of them, exactly as the seeded pooled path does.
    Without it the single-frame path is unchanged. The caller must hand buckets confined to
    the same cohort (`fusion_parent_buckets(..., scope=)`), or a child would claim one scope
    and be scored on another — the wrong number this path refused to mint for three weeks.

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
    waste it; and it costs no mint, because the pair stream simply draws again.

    A fused union of :data:`ABLATION_MIN_CONDITIONS`..:data:`ABLATION_MAX_CONDITIONS`
    conditions runs the ablation lattice before its backtest — the winner (the union, or
    its best proper subset) is what every gate after it judges and what registers. Wider
    unions, up to ``MAX_FUSION_ENTRY_CONDITIONS`` = 7, stay on the existing path
    unchanged: §3-1 of the ablation proposal priced the k = 4 lattice (15 members) as a
    cadence decision and declined it. ``ablation_stats`` shares the caller's counters so
    a fire reports seeded and fused lattices as one number."""
    minted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    replayed: dict[str, dict[str, Any]] = {}
    # Built once when the caller did not hand one over: the lattice needs a frame to slice
    # its train segment from, and every child/parent backtest below reuses it. A frame is a
    # pure function of (snapshot, cost model), so results are unchanged — only rebuilds go.
    if frame is None:
        frame = frames[0] if frames else build_replay_frame(snapshot)
    stats = ablation_stats if ablation_stats is not None else {"lattices": 0, "luck_filtered": 0}
    # One scoring rule for child and parents alike, chosen once: a child judged pooled against
    # parents judged on a single leg would pass `_fusion_improvement` on a difference of
    # windows rather than of rules.
    lattice_frames: list[ReplayFrame] = list(frames) if frames else [frame]

    def score(spec: StrategySpec) -> dict[str, Any]:
        if frames:
            return backtest_spec_pooled(spec, [], frames=frames)
        return backtest_spec(spec, snapshot, frame=frame)

    def on_this_window(spec: StrategySpec) -> dict[str, Any]:
        """This parent's evidence on the CHILD's window, replayed once per fire."""
        cached = replayed.get(spec.strategy_rule_hash)
        if cached is None:
            cached = replayed[spec.strategy_rule_hash] = score(spec)
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
        # The ablation lattice sits between the union and its backtest: the winner is what
        # the no-trades and improvement gates below judge, and what registers. A subset
        # winner facing those gates is the point rather than a leniency — it must still
        # improve on BOTH parents to be worth storing as a crossover, and a union whose
        # value was all in one parent's conditions now fails exactly there.
        child, ablation, refusal = _lattice_winner(
            child, lattice_frames, seen_hashes=seen_hashes, stats=stats,
        )
        if refusal is not None:
            rejected.append({"parent_candidate_ids": parent_ids, "reason": refusal})
            continue
        evidence = score(child)
        if not evidence["closed_count"]:
            rejected.append({"parent_candidate_ids": parent_ids, "reason": "no_trades"})
            continue
        parent_evidence = {left["candidate_id"]: on_this_window(first),
                           right["candidate_id"]: on_this_window(second)}
        refusal = _fusion_improvement(evidence, [parent_evidence[pid] for pid in parent_ids])
        if refusal is not None:
            rejected.append({"parent_candidate_ids": parent_ids, "reason": refusal})
            continue
        if ablation is not None:
            # The grid this winner survived, on the record (§1-5). Stored FACT — how many
            # were tried — which `candidate_ranking.attempts_by_context` expands at read time.
            evidence["ablation"] = ablation
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


# Which timeframes mint POOLED, once a caller supplies a cohort. `REMAINING_WORK.md` §F9 lays
# out three shapes; this is B, and the reason it is not A is measured rather than preferred:
# F2 found 1h single-symbol already 12/12 judgeable, while 4h closes a median 9 trades against a
# floor of 25 and 1d is floored at bars rather than days. Pooling where the tails are thin buys
# the judgeability; pooling 1h as well would spend the directional lever for nothing — a pooled
# spec occupies every context of its timeframe in ONE direction, and `routable_directional_
# capacity` then caps the book at 4 of 6 rather than 6 of 6 (F9 has the arithmetic).
#
# Naming the timeframes here rather than in the scheduler keeps the policy where the reasoning
# is; the caller still decides whether to hand over a cohort at all, and a caller that does not
# gets exactly today's behaviour.
POOLED_TIMEFRAMES = frozenset({"4h", "1d"})


# --- hypothesis trials (docs/proposals/HYPOTHESIS_TRIAL_V0.1.md, option C) --------------------
#
# A proposer acceptance was judged on 120 bars of one symbol, which says nothing (§1 of the
# proposal: 39 accepted, all FRAGILE, 33 with no trade). A trial re-scores ONE accepted proposal
# the way the factory scores its own mints — its own timeframe, factory depth, every leg of the
# cohort fire, the ablation lattice, the pooled replay — and stores it once as a
# `hypothesis_trial` row: held for forward evidence, refused by the promotion door
# (`pool_admission.PROMOTABLE_DERIVATION_TYPES`) and barred from parenting
# (:data:`BREEDING_DERIVATION_TYPES`). No parameter search: the proposal is one point, and a
# trial is a question about that point, not a new search context.
#
# Pooled at 1h too, although :data:`POOLED_TIMEFRAMES` keeps the factory's own 1h mints
# single-symbol. That reason is the live book's direction capacity, and a trial never reaches
# the live book (Thomas 2026-09-25, as recommended).
TRIAL_DERIVATION = "hypothesis_trial"
TRIAL_PROVENANCE = "mvp_hypothesis_trial"
# Where a trial row names the proposal it came from. The proposal's OWN rule hash is the identity
# — the trial's hash differs, because widening `symbol_scope` to the cohort is inside the rule
# fingerprint — so this is what "has this proposal been screened" is keyed on.
TRIAL_SOURCE_FIELD = "trial_source"
# Concurrent trials (Thomas 2026-09-24, HYPOTHESIS_TRIAL_V0.1 Q6: 4, as §I2 proposed). Open means
# minted; closing one is the operator's later `close`, so until that lands the cap is a total.
MAX_OPEN_TRIALS = 4
# Proposals one fire may screen before it stops. A refusal costs a lattice and a pooled replay, and
# the queue holds every accepted proposal in the backlog window — bounded so a queue of
# zero-trade proposals cannot turn one fire into nine replays.
MAX_TRIAL_SCREENS_PER_FIRE = 3
# `strategy_id` restarts per generation and the seeded, fused and topup draws number S001..; a
# trial takes its own prefix so `(generation_id, strategy_id)` can never name two rules.
TRIAL_STRATEGY_ID_PREFIX = "T"


def is_trial(record: Mapping[str, Any]) -> bool:
    """Is this stored row a hypothesis trial rather than a factory mint?"""
    return record.get("derivation_type") == TRIAL_DERIVATION


def trial_source_hashes(records: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    """The proposal rule hashes the store already holds a trial for."""
    hashes: set[str] = set()
    for record in records:
        if not is_trial(record):
            continue
        source = record.get(TRIAL_SOURCE_FIELD)
        value = source.get("strategy_rule_hash") if isinstance(source, Mapping) else None
        if isinstance(value, str) and value:
            hashes.add(value)
    return frozenset(hashes)


def open_trial_count(records: Sequence[Mapping[str, Any]], closed_ids: frozenset[str] | set[str] = frozenset()) -> int:
    """Trials holding a slot under :data:`MAX_OPEN_TRIALS`: minted and not closed by Thomas.

    A closed trial frees its slot but its proposal stays screened — :func:`trial_source_hashes`
    still names it, so closing never re-queues the proposal it came from."""
    return len({candidate_id(r) for r in records if is_trial(r)} - set(closed_ids))


def trial_spec_dict(
    proposal_spec: Mapping[str, Any], *, scope: Sequence[str], generation_id: str,
    strategy_id: str, venue: str,
) -> dict[str, Any]:
    """The proposal's spec as the trial it becomes: the cohort's scope, this fire's lineage.

    The stored ``strategy_rule_hash`` is DROPPED, not carried: it is the single-symbol spec's,
    and ``StrategySpec.from_dict`` refuses a hash that does not match the rules it is parsed
    with ("tampered or stale") — which the widened scope guarantees. The parse recomputes it."""
    spec = {k: v for k, v in proposal_spec.items() if k != "strategy_rule_hash"}
    spec.update(
        strategy_id=strategy_id,
        generation_id=generation_id,
        symbol_scope=sorted({str(s) for s in scope}),
        created_by=TRIAL_PROVENANCE,
        venue=venue,
    )
    return spec


def _screen_trials(
    proposals: Sequence[Mapping[str, Any]], *,
    legs: Sequence[Mapping[str, Any]],
    frames_for_legs: Callable[[], Sequence[ReplayFrame]],
    open_trials: int,
    fire_hashes: set[str],
    generation_id: str,
    venue: str,
    now: str,
) -> dict[str, Any]:
    """Screen proposals in order and mint the FIRST that survives. Pure.

    Returns the fire's trial block: ``status``, what was ``minted`` (0 or 1 rows, under
    ``rows``), what was ``refused`` and why. Refusals are per proposal and permanent — the caller
    records them, and a refused proposal is not screened again (a refusal is a review). The skips
    (``not_cohort``, ``cap``, an empty queue) are about the FIRE and say nothing about any
    proposal, so they refuse nothing.

    Iterating rather than taking the head: one zero-trade proposal at the front of the queue
    would otherwise hold it shut forever."""
    block: dict[str, Any] = {"open_before": open_trials, "rows": [], "minted": [], "refused": [],
                             "ablated": 0, "luck_filtered": 0}
    if not proposals:
        return {**block, "status": "empty"}
    if len(legs) < 2:
        return {**block, "status": "skipped:not_cohort"}
    if open_trials >= MAX_OPEN_TRIALS:
        return {**block, "status": "skipped:cap"}

    scope = sorted({str(leg.get("symbol") or "") for leg in legs} - {""})
    evidence_sha = integrity.sha256_record({"candles": [leg.get("candles") or [] for leg in legs]})
    installed = {t.family for t in TEMPLATES}
    stats = {"lattices": 0, "luck_filtered": 0}
    frames: Sequence[ReplayFrame] | None = None

    for index, proposal in enumerate(proposals[:MAX_TRIAL_SCREENS_PER_FIRE], start=1):
        source = {
            "proposal_id": proposal.get("proposal_id"),
            "family": proposal.get("family"),
            "strategy_rule_hash": proposal.get("strategy_rule_hash"),
            "proposed_at": proposal.get("proposed_at"),
        }

        def _refuse(reason: str, **detail: Any) -> None:
            block["refused"].append({**source, "reason": reason, **detail})

        try:
            spec = StrategySpec.from_dict(trial_spec_dict(
                proposal.get("spec") or {}, scope=scope, generation_id=generation_id,
                strategy_id=f"{TRIAL_STRATEGY_ID_PREFIX}{index:03d}", venue=venue,
            ))
        except SpecParseError as exc:
            _refuse("parse", detail=str(exc))
            continue
        # Not a new hypothesis: a family the rotation already mints, or one it retired. The
        # rotation is where those are searched; a trial of one would be a single point of a
        # search that has already been run at depth.
        family_parts = spec.strategy_family.split("+")
        if spec.strategy_family in installed or any(p in RETIRED_FAMILIES for p in family_parts):
            _refuse("known_family")
            continue
        verdict = validate_strategy(spec)
        if not verdict["approved_for_backtest"]:
            _refuse("validator", block_reasons=list(verdict["block_reasons"]))
            continue
        if spec.strategy_rule_hash in fire_hashes:
            _refuse("duplicate_rule_hash")
            continue
        if frames is None:
            # Built on first need: at 1h the fire's own frames are the primary's alone
            # (`POOLED_TIMEFRAMES`), so the other legs' frames are extra work a fire with no
            # screenable proposal never pays. Outside the per-proposal guard below: a frame that
            # cannot be built is the fire's data, not any proposal's fault.
            frames = frames_for_legs()
        starved = sorted({f for fr in frames for f in unsuppliable_features(spec, fr.rows)})
        if starved:
            _refuse("unsuppliable_feature", features=starved)
            continue
        # A proposal is model-written, and this runs inside the fire that mints the rotation. A
        # spec that breaks the scorer is refused by name (permanently — it would break it again)
        # rather than failing the fire and losing the rotation's rows with it.
        try:
            spec, ablation, refusal = _lattice_winner(
                spec, frames, seen_hashes=fire_hashes, stats=stats)
            evidence = None if refusal is not None else backtest_spec_pooled(spec, [], frames=frames)
        except Exception as exc:  # noqa: BLE001 — named in the refusal, never swallowed silently
            _refuse("scoring_error", detail=f"{type(exc).__name__}: {exc}")
            continue
        if refusal is not None:
            _refuse(refusal)
            continue
        if not evidence["closed_count"]:
            _refuse("no_trades")
            continue
        if ablation is not None:
            evidence["ablation"] = ablation
        record = {
            "strategy_id": spec.strategy_id,
            "strategy_rule_hash": spec.strategy_rule_hash,
            "generation_id": generation_id,
            "status": "BACKTESTED",
            "champion_score": evidence["champion_score"],
            "strategy_spec": spec.to_dict(),
            "backtest_evidence": evidence,
            "evidence_input_sha256": evidence_sha,
            "provenance": TRIAL_PROVENANCE,
            "derivation_type": TRIAL_DERIVATION,
            "parent_candidate_ids": [],
            # No `mint_params`: nothing was drawn, and `_best_mint_params` would otherwise read a
            # proposal's point as a search centre for a template family it does not belong to.
            TRIAL_SOURCE_FIELD: source,
            "created_at_utc": now,
        }
        record["candidate_id"] = derive_candidate_id(record)
        fire_hashes.add(spec.strategy_rule_hash)
        block["rows"].append(record)
        block["minted"].append({**source, "candidate_id": record["candidate_id"],
                                "strategy_family": spec.strategy_family})
        break

    block["ablated"] = stats["lattices"]
    block["luck_filtered"] = stats["luck_filtered"]
    if block["minted"]:
        block["status"] = "minted"
    elif block["refused"]:
        block["status"] = "refused"
    else:
        block["status"] = "empty"
    return block


def trial_status(block: Mapping[str, Any] | None) -> str | None:
    """The fire's trial outcome in one token, for the scheduler's status line."""
    if not isinstance(block, Mapping):
        return None
    status = str(block.get("status") or "")
    refused = len(block.get("refused") or [])
    if status == "minted":
        family = (block.get("minted") or [{}])[0].get("strategy_family")
        return f"minted:{family}" + (f",refused:{refused}" if refused else "")
    if status == "refused":
        return f"refused:{refused}"
    return status


def run_factory(
    snapshot: Mapping[str, Any],
    *,
    active_pool: Mapping[str, Any],
    existing_candidates: list[Mapping[str, Any]],
    now: str,
    count: int = DEFAULT_BATCH_SIZE,
    fusion_pairs: int = 0,
    positioning_eligible: bool = False,
    cohort_snapshots: Sequence[Mapping[str, Any]] | None = None,
    trial_proposals: Sequence[Mapping[str, Any]] | None = None,
    closed_trial_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """One factory run: generate → backtest → candidate records. Pure (no I/O).

    ``trial_proposals`` (default None — not asked, no ``trial`` block) is the caller's queue of
    accepted proposals not yet screened, oldest first; the fire screens them after its own
    mints and stores at most one as a ``hypothesis_trial`` row (:func:`_screen_trials`). They are
    scored across the snapshot and EVERY cohort leg whatever the timeframe — see
    :data:`TRIAL_DERIVATION` — and are not counted in ``requested_count``/``accepted_count``,
    which describe the rotation. ``closed_trial_ids`` are the trials Thomas closed
    (`forward_trial.read_trial_closes`, read by the caller — this function is pure); they no longer
    hold a slot.

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
    it is appended, and a same-run parent has no independent evidence anyway.

    Whatever fusion does NOT mint of that allocation is drawn again as seeded specs from this
    fire's own rotation slice, so a dry parent pool costs the fire nothing — see the comment
    over the shortfall draw for the measurement and for why it takes nothing from fusion.

    ``cohort_snapshots`` are the OTHER legs of a pooled mint — one spec scored across all of
    them, ``symbol_scope`` set to the whole cohort. Ignored unless ``snapshot``'s timeframe is in
    :data:`POOLED_TIMEFRAMES`, so a caller cannot pool 1h by accident. Absent (the default) is
    exactly today's single-symbol behaviour, down to the seed.

    Every drawn hypothesis of 2..:data:`ABLATION_MAX_CONDITIONS` entry conditions — seeded,
    topped-up, or fused — passes the train-only ablation lattice before registration
    (:func:`ablate_hypothesis`); the result's ``ablated_count`` / ``luck_filtered_count``
    say what the lattice did."""
    pool_entries = list(active_pool.get("active_strategies") or [])
    known_hashes = frozenset(
        h for h in (
            *(e.get("strategy_rule_hash") for e in pool_entries),
            *(c.get("strategy_rule_hash") for c in existing_candidates),
        ) if isinstance(h, str) and h
    )
    generation_id = next_generation_id([*pool_entries, *existing_candidates])
    timeframe = str(snapshot.get("timeframe") or "1d")
    # Pooling is opt-in per fire AND fenced by the policy above, so neither half alone turns it
    # on. `legs` is every snapshot the spec will be scored across, primary first.
    legs: list[Mapping[str, Any]] = [snapshot]
    if cohort_snapshots and timeframe in POOLED_TIMEFRAMES:
        legs.extend(s for s in cohort_snapshots if s is not snapshot)
    pooled = len(legs) > 1
    # The seed reads EVERY leg, not just the primary. Two cohorts sharing a first symbol would
    # otherwise draw the same parameters, which is the reproducibility rule (`same seed, same
    # batch`) quietly meaning something narrower than it says.
    candles_sha = integrity.sha256_record(
        {"candles": [leg.get("candles") or [] for leg in legs]} if pooled
        else {"candles": snapshot.get("candles") or []}
    )
    seed = int(candles_sha.split(":", 1)[1][:8], 16)

    symbol = str(snapshot.get("symbol") or "BTCUSDT")
    # Sorted so the cohort is one context however the caller ordered its fetches — this tuple is
    # what `_matches_context` and `candidate_ranking.search_context_key` compare on.
    scope = sorted({str(leg.get("symbol") or "") for leg in legs} - {""}) if pooled else None
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
    #
    # `symbol=` reaches the rotation here for the same reason `generate_batch` passes it: without
    # it this centred families that batch will never mint for this symbol (the rel_strength_*
    # pair on the market proxy), which was work spent on an answer nobody could read. One store
    # pass for the whole rotation rather than one per family — see `elite_centres`.
    centres = elite_centres(
        existing_candidates,
        templates_for_timeframe(
            timeframe, symbol=symbol, positioning_eligible=positioning_eligible, venue=venue
        ),
        symbol=symbol,
        timeframe=timeframe,
        scope=scope,
    )
    # Counted from the store this function was already given — the rotation steps on
    # THIS context's fire count, not on the global generation number. A fire spans TWO slices
    # (Thomas 2026-09-24, EXPLORATION_BUDGET_V0.1 decision Q2, option b): the batch takes slice
    # 2k and the shortfall draw after fusion takes slice 2k+1, so the cursor steps two slices per
    # fire and a pass over the library takes half the fires it did. See the shortfall draw.
    rotation_index = context_rotation_index(
        existing_candidates, symbol=symbol, timeframe=timeframe, scope=scope,
    )
    batch_slice = None if rotation_index is None else 2 * rotation_index
    topup_slice = None if rotation_index is None else 2 * rotation_index + 1
    batch = generate_batch(
        generation_id, seed=seed, count=count,
        symbol=symbol,
        timeframe=timeframe,
        elite_params=centres,
        known_rule_hashes=known_hashes,
        positioning_eligible=positioning_eligible,
        venue=venue,
        rotation_index=batch_slice,
        symbol_scope=scope,
        # Derived from the candles this fire will be SCORED on, so the probe's width and the
        # evidence that judges it come from the same bars. Pooled mints take the most binding
        # leg (`cohort_probe_stop_ceiling`); a leg that cannot say is skipped, not treated as
        # unbounded.
        probe_stop_ceiling=cohort_probe_stop_ceiling(legs),
    )

    # Built once for the whole run. Features, candles and carry are properties of the market and
    # the calendar, not of any spec, and `build_feature_rows` alone is 6.0s at the 48,000-bar 15m
    # window — so rebuilding it per candidate cost ~30s a fire on a sequential scheduler.
    frame = build_replay_frame(snapshot)
    frames = [frame, *(build_replay_frame(leg) for leg in legs[1:])] if pooled else [frame]

    candidates: list[dict[str, Any]] = []
    starved_specs: list[dict[str, Any]] = []
    ablation_refused: list[dict[str, Any]] = []
    # Every hash this fire may not register again: the store and pool (`known_hashes`)
    # plus everything registered by this fire so far. An ablation winner is the one kind
    # of spec that reaches registration WITHOUT its hash having passed the draw's own
    # duplicate guard — the conditions changed after the draw — so the guard re-runs at
    # the registration door, against this one running set.
    fire_hashes: set[str] = set(known_hashes)
    # What ablation did this fire, for the result and the operator's status line:
    # `lattices` = hypotheses that ran one (2 <= k <= ABLATION_MAX_CONDITIONS), and
    # `luck_filtered` = full conjunctions that failed to strictly beat their own best
    # proper subset on train net expectancy — the stacked luck the lattice exists to
    # catch, counted at selection time whether or not the subset then registered.
    ablation_stats = {"lattices": 0, "luck_filtered": 0}

    def _score_draw(specs: Sequence[Mapping[str, Any]], params: Mapping[str, Any]) -> None:
        """Backtest one seeded draw, appending survivors to ``candidates``.

        Two draws reach this — the batch below and the shortfall draw after fusion — and they
        produce the same KIND of row from the same rotation slice, so they share the scoring
        path rather than each carrying a copy of it."""
        for spec_dict in specs:
            spec = StrategySpec.from_dict(dict(spec_dict))
            # Before scoring, because scoring it is the waste: a spec naming a feature these
            # rows never supply cannot enter here, and evidence saying otherwise would be
            # evidence for a trade this runtime cannot reproduce. See `unsuppliable_features`.
            # Every leg, not just the primary. A pooled figure whose fourth symbol supplied
            # none of the columns the rule names is a four-leg pool wearing a five-leg label,
            # and `_holdout_evidence` would report `symbols: 5` over it. Fail closed on ANY leg
            # — the guard F2's first pass walked around cost that batch its whole finding.
            #
            # And before the lattice, for the same reason in the other direction: ablation
            # must not rescue a starved hypothesis by dropping its dead condition — the
            # refusal is about the DRAW naming an unsupplied feature, and a subset minted
            # from it would be a hypothesis nobody drew.
            starved = sorted({f for fr in frames for f in unsuppliable_features(spec, fr.rows)})
            if starved:
                starved_specs.append({"strategy_family": spec.strategy_family,
                                      "reason": UNSUPPLIABLE_FEATURE, "features": starved})
                continue
            # The ablation lattice (proposal §3, approved as proposed): between the draw and
            # registration, on the train segment only. The winner — the full conjunction if
            # it strictly beat every proper subset, else the best proper subset — is what
            # runs the ordinary full backtest below and registers. Exactly one winner per
            # hypothesis; k = 1 and k > ABLATION_MAX_CONDITIONS pass through untouched.
            spec, ablation, refusal = _lattice_winner(
                spec, frames, seen_hashes=fire_hashes, stats=ablation_stats,
            )
            if refusal is not None:
                ablation_refused.append({"strategy_family": spec.strategy_family,
                                         "reason": refusal})
                continue
            evidence = (backtest_spec_pooled(spec, [], frames=frames) if pooled
                        else backtest_spec(spec, snapshot, frame=frame))
            if ablation is not None:
                # The grid this winner survived, on the record (§1-5). `lattice_size` is
                # stored FACT — how many members were tried — and `candidate_ranking.attempts_by_context`
                # expands it at read time, so the unregistered siblings still pay the
                # multiple-testing charge.
                evidence["ablation"] = ablation
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
                # What this candidate was drawn from, recorded because nothing did and the
                # search therefore could not learn: `elite_base_params` reads exactly this.
                # Stored on the record rather than re-derived from the spec — the mapping from
                # a param name to the condition it lands in lives in the family's own entry
                # builder, so reversing it would be a second implementation of every template,
                # silently wrong the first time one changed.
                "mint_params": params.get(spec.strategy_id, {}),
                "parent_candidate_ids": [],
                "created_at_utc": now,
            }
            # Stored id == derived id: strategy_id restarts every generation, so the
            # lineage-derived candidate_id is the only key promotions may use.
            record["candidate_id"] = derive_candidate_id(record)
            fire_hashes.add(spec.strategy_rule_hash)
            candidates.append(record)

    _score_draw(batch["specs"], batch["params"])

    fused: list[dict[str, Any]] = []
    fusion_rejected: list[dict[str, Any]] = []
    # **A pooled fire fuses since 2026-09-02; from 2026-08-10 it did not, and the record of
    # why matters more than the fix.** `_fuse_batch` scored on one frame, so a pooled mint would
    # have stored a child scored on one leg while claiming five — the boundary refused rather
    # than mint that, and named itself in `fusion_skipped` (`pooled_fire`, measured 2026-08-15
    # after six days of `fused_count: 0` that read like a dry parent pool). What it cost, measured
    # 2026-09-02 across the store: crossover children pass the observation entry bar at 44.8% on
    # 1d (13 of 29) against 11.2% for the seeded rotation (44 of 392), and every 1d and 4h fire
    # since the first pooled one reported `fused=0` — 25 of the 27 fires since 2026-08-25, the
    # two exceptions being single-symbol 1h. The mechanism was never broken: replayed against the
    # current store at the account's 5x, 1d yields 4 children from 4 pairs. The path was closed.
    #
    # The second increment the boundary asked for is exactly two things: `_fuse_batch` scores
    # child and parents with `backtest_spec_pooled` across the cohort's frames, and
    # `fusion_parent_buckets` confines parents to the EXACT cohort (`scope=`) rather than to
    # any scope containing the primary — otherwise two single-symbol BTC parents would fuse into
    # a child claiming one symbol and scored on five, the same wrong number mirrored.
    #
    # **`fusion_skipped` still names WHY the path did not run, because `fused_count: 0` cannot:**
    # the caller never asked, or fusion ran and no bucket held a pair — and only the second is a
    # fact about the candidate store.
    fusion_skipped = None if fusion_pairs > 0 else FUSION_NOT_REQUESTED
    if fusion_pairs > 0:
        fused, fusion_rejected = _fuse_batch(
            # The function's own `symbol`/`timeframe`, not a second read of the snapshot. Both
            # lines answered the same question and disagreed about the default — `run_factory`
            # falls back to BTCUSDT, this fell back to "" — so a snapshot without a symbol
            # would mint BTCUSDT specs while `fusion_parent_buckets` filtered on "" and dropped
            # every parent (`symbol not in scope`), yielding no bucket and no children, in
            # silence. Unreachable through the scheduler, which always sets it; one fact, one
            # default, is the point.
            fusion_parent_buckets(existing_candidates, symbol=symbol, timeframe=timeframe,
                                  scope=scope),
            snapshot,
            generation_id=generation_id,
            # Ids RESERVED by the seeded draw, never survivors of it. `generate_batch` numbers
            # S001..S00count as it mints, and `_score_draw` then drops the specs whose features
            # this frame never supplies — so with one starved spec `len(candidates) + 1` points
            # at an id already issued, and the first fused child is minted under a name a stored
            # seeded row already holds. Two different strategies, one `(generation_id,
            # strategy_id)`: promotions key on the lineage-derived `candidate_id` so nothing
            # mis-promotes, but `resolve_candidates` refuses the pair as ambiguous and any
            # display surface shows one name over two rules.
            #
            # `count`, not `len(batch["specs"])`, so a draw that accepted fewer than it asked
            # for leaves a GAP in the numbering rather than risking an overlap — and it is the
            # same convention the shortfall draw below already uses.
            start_index=count + 1,
            pairs=fusion_pairs,
            # The fire's own running set — `known_hashes` plus everything registered so far
            # (the same contents the literal union here used to build). `_fuse_batch` adds
            # each minted child to it, so the shortfall draw below and its ablation winners
            # are deduplicated against the fused children without a second convention.
            seen_hashes=fire_hashes,
            evidence_sha=candles_sha,
            frame=frame,
            now=now,
            ablation_stats=ablation_stats,
            # The cohort's frames when pooled, so children and parents are scored across every
            # leg — the same `frames` the seeded draw above is scored on.
            frames=frames if pooled else None,
        )

    # --- the half of the budget fusion declined ----------------------------------------
    #
    # ``fusion_pairs`` is an allocation, and until 2026-08-06 a fusion that could not fill it
    # handed the slots back to nobody. That was invisible while fusion always delivered, and
    # became the dominant term the moment it stopped. Measured on this machine's scheduler
    # ledger, children minted per context: **4.00 a fire** (07-31 → 08-04), **0.40** (08-05,
    # the first fire under `FUSION_IMPROVEMENT_METRICS`), **0.00** (08-06, which recorded
    # `generated=4/4 fused=0` on all five contexts — 20 rows where the budget allowed 40).
    #
    # Two different things bind on those two days and neither is a defect to loosen. On 08-05
    # the CHILD bar bound: 92 attempts, 6 stored, `no_expectancy_gain` 46 and
    # `champion_score_regression` 11 — working exactly as designed. On 08-06 the PARENT POOL
    # bound: 10 attempts in total, because `holdout_permits_parenting` leaves 218 of the
    # store's 1,681 rows able to parent at all and only 24 of those are 1d, which was that
    # fire's whole rotation slot. Both leave the seeded half carrying the search alone.
    #
    # **This takes nothing from fusion.** The batch and `_fuse_batch` above are unchanged and
    # run first, so fusion gets first refusal on every one of its pairs and only the ones it
    # declined are re-spent. That is the distinction from `FACTORY_FUSION_PAIRS = 4 → 2`,
    # which `docs/REMAINING_WORK.md` F1 carries the numbers for and deliberately does not
    # take: that one cuts good fusions with bad, and this one cannot cut any.
    #
    # **The NEXT rotation slice, not the same one drawn denser (Thomas 2026-09-24).** Until then the
    # shortfall re-drew this fire's slice — "breadth rather than depth ... belongs in a diff that
    # argues for it" — and `EXPLORATION_BUDGET_V0.1.md` was that argument: measured over 30 days the
    # shortfall was 340 of 372 fusion slots (46% of every mint), and breadth costs nothing the
    # 2026-08-06 record counts (same volume), while family-level evidence (#965) is what is scarce.
    # So the batch takes slice 2k and this draw slice 2k+1 of the context's cursor k. The draw asks
    # for a whole slice (`count`) and keeps its first `topup_requested`, so when fusion takes some
    # of its pairs the tail of slice 2k+1 is not drawn this pass — on the day this landed fusion
    # took 8.6% of its slots — and waits for the next pass rather than displacing another slice.
    #
    # A distinct seed, because the same one reproduces the batch: the next eight hex digits of
    # the window hash the first eight already seeded, so it stays derived from the recorded
    # input and no wall-clock enters. The batch's own hashes join `known_rule_hashes` anyway,
    # so a collision is refused rather than stored twice.
    #
    # Rows carry `derivation_type: seeded_template` whichever draw they came from, because
    # that is what they are — same function, same space, the next slice of the same rotation.
    # What is worth telling apart is FIRES, and `seeded_topup_count` records that.
    #
    # **Judge it over generations, not days** (the `#420` error, and this is a mint-time
    # change — where that error was made). What it has to move is the count of rows a fire
    # produces that can be judged at all: `robustness.MIN_HOLDOUT_TRADES` was cleared by 84 of
    # a fire's rows on 07-31 and by 5 of 20 on 08-06.
    topup_requested = max(0, fusion_pairs - len(fused))
    topup_accepted = 0
    topup_rejected: list[dict[str, Any]] = []
    if topup_requested:
        topup = generate_batch(
            generation_id, seed=int(candles_sha.split(":", 1)[1][8:16], 16), count=count,
            start_index=count + len(fused) + 1,
            symbol=symbol,
            timeframe=timeframe,
            elite_params=centres,
            # The fire's running set again: store + pool + this fire's seeded rows and fused
            # children — the identical contents the literal union here used to spell out,
            # now including any ablation winner whose hash the draw itself never saw.
            known_rule_hashes=frozenset(fire_hashes),
            positioning_eligible=positioning_eligible,
            venue=venue,
            rotation_index=topup_slice,
            symbol_scope=scope,
        )
        kept = topup["specs"][:topup_requested]
        topup_accepted = len(kept)
        topup_rejected = topup["rejected"]
        _score_draw(kept, topup["params"])

    # After every rotation draw, so a trial never takes a hash the rotation would have minted and
    # the fire's own rows are exactly what they were without it.
    trial = None
    if trial_proposals is not None:
        trial_legs = [snapshot, *(s for s in (cohort_snapshots or ()) if s is not snapshot)]
        trial = _screen_trials(
            trial_proposals,
            legs=trial_legs,
            frames_for_legs=lambda: frames if pooled else [
                frame, *(build_replay_frame(leg) for leg in trial_legs[1:])],
            open_trials=open_trial_count(existing_candidates, closed_trial_ids),
            fire_hashes=fire_hashes,
            generation_id=generation_id,
            venue=venue,
            now=now,
        )
    trial_rows = trial.pop("rows") if trial is not None else []

    return {
        "factory_version": "crypto_factory.v0.1",
        "generation_id": generation_id,
        "seed": seed,
        # What this fire actually mined, so a reader of the result never has to infer it from the
        # rows. `symbol_scope` on a candidate says what the spec covers; these say what the FIRE
        # was, which is the thing a scheduler log and a later audit ask about — and a pooled fire
        # that fused nothing looks identical to a dry parent pool without it.
        "pooled": pooled,
        "pooled_symbols": list(scope) if pooled else [],
        # Both counts describe the FIRE rather than the first draw, which is what they already
        # claimed to mean and what the scheduler's `generated=N/M` reads: a fire that asked for
        # eight and minted eight is complete, and one that asked for four is a fire fusion
        # filled. `seeded_topup_count` below is what decomposes them.
        "requested_count": batch["requested_count"] + topup_requested,
        "accepted_count": batch["accepted_count"] + topup_accepted,
        # Forwarded because the generator's own answer to "did this fire deliver what was asked"
        # stopped here and nothing downstream could reconstruct it: `accepted_count` alone reads
        # as a quantity rather than as a shortfall. The scheduler's status line compares the two.
        "batch_complete": batch["batch_complete"] and topup_accepted == topup_requested,
        # Mint-time refusals and score-time ones together: a caller reading "why did this fire
        # produce so few candidates" must not have to know which loop dropped them. Kept as a
        # separate key rather than merged into `batch["rejected"]`, because that list is the
        # generator's own record and this refusal happens after it, with facts it cannot see.
        "rejected": [*batch["rejected"], *topup_rejected, *starved_specs, *ablation_refused],
        "candidates": [*candidates, *fused, *trial_rows],
        "fused_count": len(fused),
        # What the ablation lattice did this fire — seeded and fused hypotheses together,
        # since `_fuse_batch` shares the counter. `ablated_count` is lattices RUN;
        # `luck_filtered_count` is how many full conjunctions failed to strictly beat their
        # own best proper subset, i.e. how much stacked luck the grid caught. The scheduler's
        # status line forwards both, so the budget a lattice spends is legible per fire.
        "ablated_count": ablation_stats["lattices"],
        "luck_filtered_count": ablation_stats["luck_filtered"],
        # How much of the fusion allocation the shortfall draw re-spent, at MINT time — the
        # same basis as `accepted_count`, so the two decompose without a second convention.
        # Zero on a fire fusion filled, which is the only reading that says "nothing was
        # discarded"; `fused_count + seeded_topup_count == fusion_pairs` whenever the draw
        # itself was complete.
        "seeded_topup_count": topup_accepted,
        "fusion_rejected": fusion_rejected,
        # ``None`` exactly when the fusion path executed, so an empty ``fusion_rejected`` beside a
        # ``None`` here is the one reading that means "fusion looked and the store had no pair".
        "fusion_skipped": fusion_skipped,
        "evidence_input_sha256": candles_sha,
        "created_at": now,
        # Absent when the caller did not ask. Its `minted`/`refused` entries name the proposal
        # (`trial_source`), and are what the next fire and the proposer's backlog read back.
        **({"trial": trial} if trial is not None else {}),
    }
