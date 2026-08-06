"""C8b robustness scorer — is this edge real, or fitted to noise? (source port)

Verbatim port of the source's ``backtesting/robustness_scorer.py``: the absolute
backtest asks whether a strategy looks good; this scores whether looking good *means*
anything. The dominant term is observations-per-parameter — a spec carrying four
tuned numbers and eight trades clears an expectancy threshold by chance often enough
that clearing it is not evidence. Scoring only: it ranks and warns; nothing here
blocks or promotes.

Inputs this port cannot measure score ZERO, never full credit — the module's own rule
("absence of evidence, not evidence of stability"):

- ``temporal_stability`` is not computed (the source's walk-forward module was not
  ported); the factory supplies only the in-backtest window pass rate, so the
  ``insufficient_walk_forward_evidence`` warning rides on every candidate.
- The cost model was not ported, so ``cost_robustness`` inputs are withheld and the
  term scores 0 with its warning — uniformly for every candidate, which preserves the
  ranking while never crediting an unmeasured property.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from ..coerce import as_float as _f
from .strategy import StrategySpec

ROBUSTNESS_SCORER_VERSION = "robustness_scorer.v1"

# Exit rules always carry three fitted numbers: stop_atr, target_atr, max_holding_bars.
EXIT_FREE_PARAMETERS = 3
# The validator caps entry conditions at 8, so 11 is the worst case and 3 the floor.
MAX_FREE_PARAMETERS = 11
MIN_FREE_PARAMETERS = EXIT_FREE_PARAMETERS

# ~10 observations per fitted parameter to mean anything; under ~5 it is noise.
HEALTHY_TRADES_PER_PARAMETER = 10.0
CRITICAL_TRADES_PER_PARAMETER = 5.0

ROBUST = "ROBUST"
PROVISIONAL = "PROVISIONAL"
FRAGILE = "FRAGILE"

# First-pass ordering for candidate ranking (M4a): a more-believable edge outranks a
# less-believable one before any performance term is consulted. An unknown/absent
# verdict sorts last — no evidence of robustness is not evidence of it.
VERDICT_ORDER: dict[str, int] = {ROBUST: 0, PROVISIONAL: 1, FRAGILE: 2}
_UNKNOWN_VERDICT_RANK = 3


def verdict_rank(verdict: Any) -> int:
    """The first-pass sort rank of a robustness verdict (lower = more believable)."""
    return VERDICT_ORDER.get(str(verdict), _UNKNOWN_VERDICT_RANK)

ROBUST_SCORE_THRESHOLD = 0.70
FRAGILE_SCORE_THRESHOLD = 0.35

# Out-of-sample confirmation (the factory's holdout — see factory.HOLDOUT_FRACTION).
# ROBUST is the only verdict that claims an edge is believable, so it is the only one
# that must be earned on bars the score never saw. Everything the scorer measured
# before this was in-sample by construction, which meant a high score could not
# distinguish a real edge from a lucky fit; promotion then selected the maximum of
# many such scores, which is how noise gets promoted.
HOLDOUT_CONFIRMED = "CONFIRMED"          # the unseen tail shows an edge that clears its own noise
HOLDOUT_CONTRADICTED = "CONTRADICTED"    # enough unseen trades, and the edge does not clear it
HOLDOUT_INSUFFICIENT = "INSUFFICIENT"    # the tail cannot be judged (too few trades, or no spread)
HOLDOUT_UNCONFIRMED = "UNCONFIRMED"      # no holdout was evaluated at all

# **The gate above did not close, and the store says by how much.** It asked for 3 closed
# trades and `total_R > 0`, which is the test "t > 0" on a sample of three — a coin flip at a
# design R:R of 2.4 clears it roughly half the time. Measured on this machine's store
# 2026-08-03, over the 847 candidates carrying a holdout block: **236 read CONFIRMED**, and 26
# of them cleared every other filter the live-promotion door applies.
#
# That the survivors are selection rather than edge is measurable directly, because the same
# store records how many trades each candidate's backtest closed:
#
#   backtest trades   candidates   mean expectancy
#   < 20                    150         +0.114R
#   20-49                   152         +0.093R
#   50-199                  333         -0.003R
#   200+                    344         -0.212R
#
# Expectancy is a monotonically DECREASING function of sample size, converging on roughly the
# cost of trading. That is the signature of a population with no edge in it: the estimate is
# noise at small n and the truth at large n. Across all 979 candidates the mean is -0.044R and
# only 38.5% are positive, while 3 of 959 clear a Bonferroni-corrected t. A gate that admits
# 236 of them is not measuring survival, it is measuring how many candidates were tried.
#
# Two numbers close it, and both are the same idea: a confirmation must be able to fail.
#
# **25 closed trades.** Not a power calculation — the honest floor is far higher — but the
# point at which the second test below stops being arithmetic over a handful of rows. Measured:
# it moves CONFIRMED from 236 to 107 on the stored blocks, and the half it removes is the half
# whose mean holdout expectancy was inflated (+0.246R claimed at a median of 21 trades, versus
# +0.117R for those clearing 25). Of the 847 blocks, 533 already clear it, so the floor does
# not empty the store — it empties the part of it that never had a sample.
MIN_HOLDOUT_TRADES = 25

# **The edge must clear its own noise.** `total_R > 0` reads a mean without a spread beside it,
# and a mean is not a finding. The interval is the same one `dashboard.sample_verdict` draws
# over paper outcomes, deliberately — "can this sample tell the sign of its own edge" is one
# question and it gets one answer in this runtime, which is why the multiplier lives here (the
# module that judges whether an edge is real) and the board imports it rather than restating it.
#
# Two-sided 95%, and not the one-sided 1.645 that a "is it positive" gate would suggest on its
# own. The correction this gate cannot make is for the number of candidates tried — 979 and
# rising daily, `pool.rank_candidates` taking the maximum over all of them — so the stricter of
# two defensible multipliers is the one that leaves the smaller unpaid debt. A1 in
# `docs/TRADING_STRATEGY_REVIEW_RECORD.md` is the real fix and needs the attempt count on the
# record; this is what can be charged without it.
CONFIDENCE_Z = 1.96

# --- selection: the correction the interval above cannot make on its own -------------------
#
# A 95% interval says "one sample in twenty clears this by chance". Score 979 candidates on
# substantially the same bars and take the maximum, and one in twenty is not a warning — it is
# a production line. Measured on this machine's store 2026-08-03: 39 of 959 candidates clear an
# uncorrected t of 1.96, which is **4.1%** against the 2.5% a pure-noise population predicts.
# The store is, to within its own resolution, noise; and `rank_candidates` sorts it and hands
# an operator the top of it.
#
# ALPHA is the family-wise error rate: the probability that ANY of the attempts clears by
# chance, rather than the probability each one does. Bonferroni divides it across them, which is
# conservative when the attempts correlate — and these correlate heavily, since a rolling 500-day
# window means two generations minted a day apart overlap 99.8%. Conservative is the right
# direction for a threshold that gates real money, and the honest alternatives (White's Reality
# Check, SPA, a Deflated Sharpe) all need the joint distribution of the attempts, which this
# store does not retain.
SELECTION_ALPHA = 0.05

# What "one attempt" means, and it is deliberately NOT the lineage. A1's minimal fix names
# `attempts_in_lineage`, but the family choice is itself a searched degree of freedom — 20
# templates, and `count_free_parameters` already declines to count which one was picked. Two
# candidates from different families on the same symbol and timeframe were tried against the
# same bars, so they are two attempts at one question. Counting per family would divide the
# burden by the very choice that creates it.
SELECTION_CONTEXT = "symbol_scope + timeframe"

# The believability tiers `pool.rank_candidates` orders on, lower is better.
SELECTION_CLEARS_CORRECTED = 0    # survives the attempt count it was drawn from
SELECTION_CLEARS_UNCORRECTED = 1  # would pass on its own; not against its siblings
SELECTION_BELOW = 2               # measured, and it does not clear
SELECTION_UNMEASURED = 3          # no spread recorded, or the attempt count is unknown


def selection_adjusted_z(attempts: int) -> float:
    """The two-sided threshold a t must clear when it is the best of ``attempts`` tries.

    ``attempts <= 1`` is the uncorrected :data:`CONFIDENCE_Z` — one try needs no correction,
    and a store that cannot count its own attempts must not be handed a *weaker* bar than a
    store that can. Grows like ``sqrt(2 ln N)``: 1.96 at one attempt, 3.48 at 100, 4.06 at
    1000. That the bar rises slowly is the point — an edge that is real does not care."""
    if attempts <= 1:
        return CONFIDENCE_Z
    return statistics.NormalDist().inv_cdf(1.0 - SELECTION_ALPHA / (2.0 * attempts))


def expectancy_t(expectancy: Any, stdev_r: Any, closed_count: Any) -> float | None:
    """``expectancy / (stdev_r / sqrt(n))`` — how many standard errors from zero.

    ``None`` when it cannot be computed, which is every candidate minted before the factory
    recorded a spread. Never a substitute figure: a t invented from an assumed dispersion
    would rank candidates on a number nobody measured, and this whole module exists because
    ranking on unmeasured properties is how noise gets promoted. A ``stdev_r`` of zero is
    also None, for the reason :func:`holdout_status` states — no observed variation is not
    evidence of none."""
    for value in (expectancy, stdev_r, closed_count):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
    if stdev_r <= 0 or closed_count < 2:
        return None
    return float(expectancy) / (float(stdev_r) / math.sqrt(float(closed_count)))


def selection_rank(t_stat: float | None, attempts: int | None) -> int:
    """Which believability tier this candidate's edge sits in once selection is charged."""
    if t_stat is None or attempts is None:
        return SELECTION_UNMEASURED
    if t_stat >= selection_adjusted_z(int(attempts)):
        return SELECTION_CLEARS_CORRECTED
    if t_stat >= CONFIDENCE_Z:
        return SELECTION_CLEARS_UNCORRECTED
    return SELECTION_BELOW


WEIGHTS: dict[str, float] = {
    "sample_adequacy": 0.30,
    "temporal_consistency": 0.25,
    "regime_breadth": 0.20,
    "parameter_parsimony": 0.15,
    "cost_robustness": 0.10,
}


def count_free_parameters(spec: StrategySpec) -> int:
    """A ``value_from`` condition states a structural relationship (nothing to tune);
    every literal threshold or label is a degree of freedom someone chose.

    **A lag is one of those choices.** Looking back a bar is not a property of the market, it
    is a decision the search made about where to look — and once the factory can mint it (the
    crossover families), a spec that uses it has searched a strictly larger space than one that
    does not. Counting it keeps ``trades_per_parameter`` from getting quietly more optimistic at
    exactly the moment the space got bigger, which matters because that ratio drives both
    ``sample_adequacy`` (the heaviest component) and the FRAGILE veto.

    Only a lag that is USED counts. Choosing not to look back is the default, costs nothing to
    express, and charging every condition for the option would move `free_parameters` on every
    spec ever scored — a re-rating of the whole store dressed up as an accounting fix.

    **Still a floor, not the truth.** A4 in ``docs/TRADING_STRATEGY_REVIEW_RECORD.md`` remains
    open: which of the template families was chosen, which of four timeframes, which symbol, and
    how many attempts it took are all degrees of freedom and none of them are counted here.
    ``pool.selection_rank`` charges the attempt count separately; the rest is unpaid."""
    literals = sum(1 for c in spec.entry_rules.conditions if c.value is not None)
    lags = sum(1 for c in spec.entry_rules.conditions if c.lag)
    return literals + lags + EXIT_FREE_PARAMETERS


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _sample_adequacy(trades_per_parameter: float) -> float:
    return _clamp01(trades_per_parameter / HEALTHY_TRADES_PER_PARAMETER)


def _temporal_consistency(walk_forward: Mapping[str, Any]) -> float:
    """Credit only what was demonstrated; both signals absent scores zero."""
    parts = [
        v
        for v in (walk_forward.get("walk_forward_pass_rate"), walk_forward.get("temporal_stability"))
        if v is not None
    ]
    if not parts:
        return 0.0
    return _clamp01(sum(float(p) for p in parts) / len(parts))


def _regime_breadth(regime_breakdown: Mapping[str, Any]) -> float:
    """An edge that only appears in one of several regimes is fitted to that regime."""
    traded = regime_breakdown.get("regimes_traded") or []
    if not traded:
        return 0.0
    profitable = int(regime_breakdown.get("profitable_regime_count", 0) or 0)
    return _clamp01(profitable / len(traded))


def _parameter_parsimony(free_parameters: int) -> float:
    span = MAX_FREE_PARAMETERS - MIN_FREE_PARAMETERS
    if span <= 0:
        return 1.0
    return _clamp01((MAX_FREE_PARAMETERS - free_parameters) / span)


def _cost_robustness(metrics: Mapping[str, Any]) -> float:
    """Fraction of the pre-cost edge surviving fees/slippage/funding. With no cost model the
    factory withholds ``total_net_r``, so this scores 0 (unmeasured ≠ survives).

    ``funding_cost_r`` is SIGNED — a short book in a positive-funding regime is paid to hold —
    so a credit correctly makes ``gross`` smaller than ``net`` and the ratio saturates at 1.0
    through the clamp. That is the honest reading: an edge that survives its costs completely
    is the best this component can say, and being paid to hold is not evidence of a better
    entry rule. Absent (a record predating the carry) reads as 0.0 and the score is then the
    pre-2026-07-29 figure, which is why `pool.cost_basis_rank` refuses such evidence outright
    rather than leaving it to look merely slightly better than it was."""
    net = _f(metrics.get("total_net_r"))
    if net <= 0:
        return 0.0
    costs = (_f(metrics.get("fee_cost_r")) + _f(metrics.get("slippage_cost_r"))
             + _f(metrics.get("funding_cost_r")))
    gross = net + costs
    if gross <= 0:
        return 0.0
    return _clamp01(net / gross)


def _warnings(
    trades_per_parameter: float,
    walk_forward: Mapping[str, Any],
    regime_breakdown: Mapping[str, Any],
    components: Mapping[str, float],
    holdout_state: str = HOLDOUT_UNCONFIRMED,
) -> list[str]:
    warnings: list[str] = []
    if holdout_state == HOLDOUT_CONTRADICTED:
        warnings.append("holdout_contradicts_in_sample_edge")
    elif holdout_state == HOLDOUT_INSUFFICIENT:
        warnings.append("holdout_too_thin_to_confirm")
    elif holdout_state == HOLDOUT_UNCONFIRMED:
        warnings.append("no_out_of_sample_evidence")
    if trades_per_parameter < CRITICAL_TRADES_PER_PARAMETER:
        warnings.append("trades_per_parameter_below_critical")
    elif trades_per_parameter < HEALTHY_TRADES_PER_PARAMETER:
        warnings.append("trades_per_parameter_below_healthy")
    if (
        walk_forward.get("walk_forward_pass_rate") is None
        or walk_forward.get("temporal_stability") is None
    ):
        warnings.append("insufficient_walk_forward_evidence")
    if len(regime_breakdown.get("regimes_traded") or []) <= 1:
        warnings.append("single_regime_sample")
    elif components["regime_breadth"] < 0.5:
        warnings.append("edge_concentrated_in_minority_of_regimes")
    if components["cost_robustness"] < 0.5:
        warnings.append("edge_largely_consumed_by_costs")
    return sorted(set(warnings))


def holdout_expectancy(holdout: Mapping[str, Any]) -> float:
    """The tail's mean R per trade. Stored directly; derived when only the total is.

    Both are written by ``factory._holdout_evidence`` and agree by construction, so the
    fallback is for hand-built and imported blocks rather than for drift between the two."""
    stored = holdout.get("expectancy")
    if isinstance(stored, (int, float)) and not isinstance(stored, bool):
        return float(stored)
    closed = _f(holdout.get("closed_count"))
    return _f(holdout.get("total_R")) / closed if closed else 0.0


# The unit of independence is the market PERIOD, not the trade. Measured 2026-08-04 over 700
# frozen specs and 113,127 replayed trades: mean pairwise correlation of per-block gross across
# the ten routed contexts is **+0.459** — the same weeks are good or bad for everything at once
# — and block-to-block dispersion of gross is 0.1087R against an effect being hunted at
# 0.01-0.05R. `stdev_r / sqrt(closed_count)` therefore overstates precision by roughly
# sqrt(trades / periods): about 2.4x for the single block that passed on this store.
#
# **Measured directly 2026-08-06, and the sqrt estimate was low.** Variance inflation over the
# holdout region — the observed variance of a slice mean against what pooled within-slice
# variance predicts for independent draws — is **10-15x**, so the i.i.d. standard error is
# understated by a factor of ~3.4, not 2.4. Trades inside a slice are strongly dependent;
# adjacent slices are not (see `factory.HOLDOUT_PERIODS` for the autocorrelation table). Those
# two facts are the whole justification for this gate, and they were measured in that order.
#
# Eight of `factory.HOLDOUT_PERIODS`' ten, keeping the proportional headroom four-of-five had:
# a slice that closes nothing is survivable, three are not. The price is measured rather than
# assumed — over the 627 specs clearing MIN_HOLDOUT_TRADES, **96% occupy at least eight** of
# ten slices, so the floor costs 4% of the population that could be judged at all.
MIN_HOLDOUT_PERIODS = 8

# Two-sided 95% Student-t critical values by degrees of freedom. A table rather than a
# computed quantile because the runtime carries no statistics dependency and this needs six
# numbers; `CONFIDENCE_Z` is the large-sample limit the last row approaches. Using the NORMAL
# 1.96 over four or five observations is the error this constant exists to avoid — at df=3 the
# true bar is 3.182, 62% wider.
_T_CRITICAL_95: dict[int, float] = {
    3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 15: 2.131, 20: 2.086, 30: 2.042,
}


def t_critical_95(degrees_of_freedom: int) -> float:
    """The two-sided 95% t bar for this many degrees of freedom, erring WIDE.

    Between tabulated rows it returns the next larger row's value rather than interpolating —
    a wider bar refuses more, which is the direction a fail-closed gate must round."""
    if degrees_of_freedom <= 0:
        return float("inf")
    for df in sorted(_T_CRITICAL_95):
        if degrees_of_freedom <= df:
            return _T_CRITICAL_95[df]
    return CONFIDENCE_Z


def _periods_confirm(holdout: Mapping[str, Any]) -> bool:
    """Whether the tail's PERIOD subtotals clear zero on their own dispersion.

    Fail-closed on absence: a block minted before ``period_r`` existed cannot compute this and
    therefore cannot CONFIRM. That is the ``stdev_r`` precedent applied again, and for the same
    reason — letting a record buy the weaker test by omitting a field is the failure mode
    ``cost.outcome_net_r`` names in the other direction.

    Periods that closed no trade are dropped rather than counted as 0.0: a slice the spec never
    traded in is absence of evidence, and scoring it as a break-even observation would shrink
    the spread with data nobody measured."""
    periods = holdout.get("period_r")
    counts = holdout.get("period_trades")
    if not isinstance(periods, (list, tuple)) or not isinstance(counts, (list, tuple)):
        return False
    if len(periods) != len(counts):
        return False
    values = [
        float(r) for r, n in zip(periods, counts)
        if isinstance(r, (int, float)) and not isinstance(r, bool)
        and isinstance(n, (int, float)) and not isinstance(n, bool) and n > 0
    ]
    if len(values) < MIN_HOLDOUT_PERIODS:
        return False
    spread = statistics.stdev(values)
    if spread <= 0:
        return False
    stderr = spread / math.sqrt(len(values))
    return statistics.mean(values) - t_critical_95(len(values) - 1) * stderr > 0


def holdout_status(holdout: Mapping[str, Any] | None) -> str:
    """Classify what the untouched tail showed. Fail-closed toward not-confirmed.

    A missing holdout block (every candidate minted before this existed) reads as
    UNCONFIRMED rather than as a pass: no evidence of out-of-sample survival is not
    evidence of it — the same rule this module already applies to unknown verdicts.

    CONFIRMED needs the tail to clear ``MIN_HOLDOUT_TRADES`` **and** to put zero outside the
    interval its own dispersion draws. Three things therefore read as INSUFFICIENT rather than
    as a pass, and the third is the one that matters most in practice:

    - too few closed trades to judge;
    - a ``closed_count`` this function cannot read as a number;
    - **no ``stdev_r``** — every block written before the field existed. Those cannot compute
      an interval at all, and the alternative (fall back to ``total_R > 0`` for them) would let
      a record buy the weaker test by omitting a field, which is the failure mode
      ``cost.outcome_net_r`` names in the other direction. Absence is not an opt-out. It costs
      the 847 stored blocks their CONFIRMED status until the factory re-mints them carrying the
      spread, which is the correct price: what those blocks proved is exactly what is unknown.

    A ``stdev_r`` of zero is INSUFFICIENT for the same reason ``dashboard.sample_verdict``
    refuses it — identical observations draw a zero-width interval that excludes zero by
    construction, which is absence of observed variation, not evidence of none.
    """
    if not isinstance(holdout, Mapping):
        return HOLDOUT_UNCONFIRMED
    if not holdout:
        return HOLDOUT_UNCONFIRMED
    closed = holdout.get("closed_count")
    if not isinstance(closed, (int, float)) or isinstance(closed, bool) or closed < MIN_HOLDOUT_TRADES:
        return HOLDOUT_INSUFFICIENT
    stdev = holdout.get("stdev_r")
    if not isinstance(stdev, (int, float)) or isinstance(stdev, bool) or stdev <= 0:
        return HOLDOUT_INSUFFICIENT
    stderr = float(stdev) / math.sqrt(float(closed))
    if holdout_expectancy(holdout) - CONFIDENCE_Z * stderr <= 0:
        # The weaker test already failed. A block that cannot clear the trade-level interval
        # cannot clear the period-level one either — the period interval is strictly wider —
        # so this stays CONTRADICTED without needing the period data, and legacy blocks keep
        # the verdict they always had.
        return HOLDOUT_CONTRADICTED
    return HOLDOUT_CONFIRMED if _periods_confirm(holdout) else HOLDOUT_INSUFFICIENT


def classify_verdict(score: float, trades_per_parameter: float, holdout_state: str) -> str:
    # The veto is not a tiebreak: below the critical ratio every other component is
    # computed on the same too-small sample — a high score there IS the overfitting.
    if trades_per_parameter < CRITICAL_TRADES_PER_PARAMETER:
        return FRAGILE
    if score >= ROBUST_SCORE_THRESHOLD:
        # The in-sample score alone can no longer award ROBUST. A candidate that scored
        # well and then failed (or never faced) unseen bars is exactly the one this
        # tier exists to keep out of the promotion shortlist; it stays PROVISIONAL,
        # which is what "looks good, unproven forward" honestly means.
        return ROBUST if holdout_state == HOLDOUT_CONFIRMED else PROVISIONAL
    if score < FRAGILE_SCORE_THRESHOLD:
        return FRAGILE
    return PROVISIONAL


def score_robustness(
    spec: StrategySpec,
    metrics: Mapping[str, Any],
    walk_forward: Mapping[str, Any],
    regime_breakdown: Mapping[str, Any],
    holdout: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score how much of this strategy's backtest is believable.

    ``holdout`` is the factory's out-of-sample tail (omitted by callers predating it,
    which then read as UNCONFIRMED). It never moves the score — it gates the ROBUST
    verdict, so the number stays comparable across candidates while the tier means
    something stronger."""
    holdout_state = holdout_status(holdout)
    free_parameters = count_free_parameters(spec)
    trade_count = int(metrics.get("trade_count", 0) or 0)
    trades_per_parameter = (trade_count / free_parameters) if free_parameters > 0 else 0.0

    components = {
        "sample_adequacy": _sample_adequacy(trades_per_parameter),
        "temporal_consistency": _temporal_consistency(walk_forward),
        "regime_breadth": _regime_breadth(regime_breakdown),
        "parameter_parsimony": _parameter_parsimony(free_parameters),
        "cost_robustness": _cost_robustness(metrics),
    }
    score = sum(components[name] * weight for name, weight in WEIGHTS.items())

    record = {
        "robustness_scorer_version": ROBUSTNESS_SCORER_VERSION,
        "free_parameters": free_parameters,
        "trade_count": trade_count,
        "trades_per_parameter": round(trades_per_parameter, 6),
        "components": {name: round(value, 6) for name, value in components.items()},
        "weights": dict(WEIGHTS),
        "robustness_score": round(score, 6),
        "holdout_status": holdout_state,
        "verdict": classify_verdict(score, trades_per_parameter, holdout_state),
        "warnings": _warnings(trades_per_parameter, walk_forward, regime_breakdown,
                              components, holdout_state),
        "healthy_trades_per_parameter": HEALTHY_TRADES_PER_PARAMETER,
        "critical_trades_per_parameter": CRITICAL_TRADES_PER_PARAMETER,
    }
    record["robustness_id"] = integrity.short_id(
        "robustness", {"version": ROBUSTNESS_SCORER_VERSION, "spec": spec.strategy_rule_hash,
                       "score": str(record["robustness_score"]), "verdict": record["verdict"]}
    )
    return record
