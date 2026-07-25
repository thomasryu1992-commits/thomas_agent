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

from typing import Any, Mapping

from runtime.read_only_kernel import integrity

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

WEIGHTS: dict[str, float] = {
    "sample_adequacy": 0.30,
    "temporal_consistency": 0.25,
    "regime_breadth": 0.20,
    "parameter_parsimony": 0.15,
    "cost_robustness": 0.10,
}


def count_free_parameters(spec: StrategySpec) -> int:
    """A ``value_from`` condition states a structural relationship (nothing to tune);
    every literal threshold or label is a degree of freedom someone chose."""
    literals = sum(1 for c in spec.entry_rules.conditions if c.value is not None)
    return literals + EXIT_FREE_PARAMETERS


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


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
    """Fraction of the pre-cost edge surviving fees/slippage. With no cost model the
    factory withholds ``total_net_r``, so this scores 0 (unmeasured ≠ survives)."""
    net = _f(metrics.get("total_net_r"))
    if net <= 0:
        return 0.0
    costs = _f(metrics.get("fee_cost_r")) + _f(metrics.get("slippage_cost_r"))
    gross = net + costs
    if gross <= 0:
        return 0.0
    return _clamp01(net / gross)


def _warnings(
    trades_per_parameter: float,
    walk_forward: Mapping[str, Any],
    regime_breakdown: Mapping[str, Any],
    components: Mapping[str, float],
) -> list[str]:
    warnings: list[str] = []
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


def _verdict(score: float, trades_per_parameter: float) -> str:
    # The veto is not a tiebreak: below the critical ratio every other component is
    # computed on the same too-small sample — a high score there IS the overfitting.
    if trades_per_parameter < CRITICAL_TRADES_PER_PARAMETER:
        return FRAGILE
    if score >= ROBUST_SCORE_THRESHOLD:
        return ROBUST
    if score < FRAGILE_SCORE_THRESHOLD:
        return FRAGILE
    return PROVISIONAL


def score_robustness(
    spec: StrategySpec,
    metrics: Mapping[str, Any],
    walk_forward: Mapping[str, Any],
    regime_breakdown: Mapping[str, Any],
) -> dict[str, Any]:
    """Score how much of this strategy's backtest is believable."""
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
        "verdict": _verdict(score, trades_per_parameter),
        "warnings": _warnings(trades_per_parameter, walk_forward, regime_breakdown, components),
        "healthy_trades_per_parameter": HEALTHY_TRADES_PER_PARAMETER,
        "critical_trades_per_parameter": CRITICAL_TRADES_PER_PARAMETER,
    }
    record["robustness_id"] = integrity.short_id(
        "robustness", {"version": ROBUSTNESS_SCORER_VERSION, "spec": spec.strategy_rule_hash,
                       "score": str(record["robustness_score"]), "verdict": record["verdict"]}
    )
    return record
