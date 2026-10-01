"""The seeded generator: which templates a fire draws, around which centres, and the draws.

Moved whole out of ``factory`` (crypto refactor plan PR-10). ``factory`` re-exports, as
the same objects, the names its callers still read there (refactor plan PR-16 removed the rest; the
set is pinned by ``test_mvp_runtime_crypto_reexport_roster.py``).

What is here:

- the generator's knobs (``DEFAULT_BATCH_SIZE``, ``_MUTATION_SCALE``, ``_EXIT_PROBE_SLOTS``,
  ``_MAX_ATTEMPTS_PER_SPEC``) and the probe slot's stop ceiling, read off the candles the fire
  will be scored on (``liquidation_admissible_stop_atr``, ``cohort_probe_stop_ceiling``);
- ``mutate_params`` and the two rules a draw is folded through (``_fold_into_bounds``,
  ``_apply_reward_risk_floor``);
- where the search looks next: ``elite_centres`` and ``elite_base_params`` read the centres off
  the stored candidates, ``context_rotation_phase`` and ``_rotation_offset`` place a fire in the
  rotation;
- ``build_spec_dict`` and ``generate_batch``: the batch of validated spec dicts a fire mints.

It scores nothing and stores nothing. The templates and the validator come from
``template_space``. ``context_rotation_index``, the cursor ``run_factory`` counts off the store,
stays in ``factory``: it has to know what a hypothesis trial row is, and that is defined there.
This module imports nothing from ``factory``.

Deterministic: a draw depends on its seed and its arguments. No file, clock, environment or
network read.

A test that patches a name one of these functions reads patches it here. A patch on
``factory.<name>`` rebinds ``factory``'s copy and does not reach a function defined in this
module.
"""

from __future__ import annotations

import math
import random
from typing import Any, Mapping, Sequence

from runtime.read_only_kernel import integrity

from . import features, indicators, market_data
from .trade_plan import ASSUMED_LEVERAGE, MAINTENANCE_MARGIN_RATE, liquidation_price
from .robustness import MIN_HOLDOUT_TRADES
from .strategy import SCHEMA_VERSION, SpecParseError, StrategySpec
from .template_space import (
    MIN_REWARD_RISK, ParamSpec, StrategyTemplate, templates_for_timeframe, validate_strategy,
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
