"""C8 strategy factory — seeded generation, validation, backtest evidence, candidates.

Ports the source strategy factory's S2/S3 core (template library subset, seeded
parameter mutation, the pre-backtest validator) plus a replay backtest built from the
already-ported evaluator and settlement math — the source's own guarantee ("a strategy
behaves identically in backtest and live" because both share one evaluator and one
exit model) holds here by construction, since ``strategy.evaluate_spec`` and
``paper.settle_trade_plan`` are exactly what the live cycle runs.

Template subset: the ten families whose features the C3 rows compute. The ``htf_*``
families (higher-timeframe legs) and ``funding_fade_*`` (funding feed) are NOT ported
— their inputs do not exist here yet, and generating specs that can never match would
be noise pretending to be diversity.

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
source's S4b cost model, ported in R-space — see ``cost.py``), matching the source's
own boundary exactly: costs apply to backtest/factory scoring only, never to live
paper trading (``paper.settle_trade_plan`` stays cost-free, unchanged — the source's
live paper kernel never imports the cost model either). ``champion_score`` and
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

from .cost import CostModel, apply_cost_model
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
    "mark_price", "index_price", "mark_index_basis_bps", "liquidation_spike_ratio",
    # C9: the funding series rides the default binance_futures grant, so generated
    # specs may reference it. The liquidation columns stay OUT of the generation
    # registry (the Coinalyze feed is gated off by default; imported specs still
    # EVALUATE them — this set gates what the factory may mint, not the evaluator).
    "funding_rate", "funding_zscore",
})
CATEGORICAL_FEATURES: dict[str, frozenset[str]] = {
    "market_regime": frozenset({"TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOLATILITY",
                                "LOW_VOLATILITY", "UNCLEAR"}),
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
)


def templates_for_timeframe(timeframe: str) -> tuple[StrategyTemplate, ...]:
    """The rotation retimed to ``timeframe`` (all ten families are retimeable —
    the untimeable htf_* families were not ported)."""
    return tuple(replace(t, timeframe=str(timeframe)) for t in TEMPLATES)


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


def generate_batch(
    generation_id: str, *, seed: int, start_index: int = 1, count: int = DEFAULT_BATCH_SIZE,
    symbol: str = "BTCUSDT", timeframe: str = "1d",
    known_rule_hashes: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Produce ``count`` validated, distinct candidate specs (source mechanics).

    ``known_rule_hashes`` extends the duplicate guard across the existing pool and
    candidate store, so a batch never re-mints a strategy that already exists."""
    rng = random.Random(seed)
    templates = templates_for_timeframe(timeframe)
    accepted: list[StrategySpec] = []
    validations: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_hashes: set[str] = set(known_rule_hashes)

    attempts = 0
    while len(accepted) < count and attempts < count * _MAX_ATTEMPTS_PER_SPEC:
        attempts += 1
        template = templates[len(accepted) % len(templates)]
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

def backtest_spec(
    spec: StrategySpec, snapshot: Mapping[str, Any], *, cost: CostModel | None = None,
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
    rows = build_feature_rows(dict(snapshot))
    candles = snapshot.get("candles") or []
    outcomes: list[dict[str, Any]] = []
    position: dict[str, Any] | None = None
    entry_regime: str | None = None
    total_fee_cost_r = 0.0
    total_slippage_cost_r = 0.0

    for i, row in enumerate(rows):
        candle = candles[i]
        if position is not None:
            reason, exit_price, _gross_r = settle_trade_plan(
                position, candle, row.get("close"), spec.exit_rules.max_holding_bars, False
            )
            if reason is not None:
                breakdown = apply_cost_model(
                    position["direction"], position["entry_price"], float(exit_price),
                    position["risk"], cost=cost,
                )
                total_fee_cost_r += breakdown.fee_cost_r
                total_slippage_cost_r += breakdown.slippage_cost_r
                outcomes.append({
                    "outcome_closed": True,
                    "result_R": breakdown.net_r,
                    "gross_R": breakdown.gross_r,
                    "fee_cost_R": breakdown.fee_cost_r,
                    "slippage_cost_R": breakdown.slippage_cost_r,
                    "close_reason": reason,
                    "created_at_utc": candle.get("close_time"),
                    "strategy_id": spec.strategy_id,
                    "entry_regime": entry_regime,
                    "closed_at_bar": i,
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
            }
            entry_regime = row.get("market_regime")

    summary = summarize_outcomes(outcomes)

    # Regime breadth: which regimes this spec actually traded in, and how many of
    # them were profitable in aggregate (the scorer's fitted-to-one-regime signal).
    regime_r: dict[str, float] = {}
    for outcome in outcomes:
        regime = str(outcome.get("entry_regime") or "UNCLEAR")
        regime_r[regime] = regime_r.get(regime, 0.0) + outcome["result_R"]
    regime_breakdown = {
        "regimes_traded": sorted(regime_r),
        "profitable_regime_count": sum(1 for total in regime_r.values() if total > 0),
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
    }
    robustness = score_robustness(spec, cost_metrics, walk_forward, regime_breakdown)
    return {
        "strategy_id": spec.strategy_id,
        "strategy_rule_hash": spec.strategy_rule_hash,
        "closed_count": summary["closed_count"],
        "expectancy": summary["expectancy"],
        "win_count": summary["win_count"],
        "loss_count": summary["loss_count"],
        "max_drawdown": summary["max_drawdown"],
        "cost_summary": {
            "total_net_r": total_net_r,
            "total_fee_cost_r": round(total_fee_cost_r, 8),
            "total_slippage_cost_r": round(total_slippage_cost_r, 8),
            "cost_model": {"taker_fee_bps": cost.taker_fee_bps, "slippage_bps": cost.slippage_bps},
        },
        "regime_breakdown": regime_breakdown,
        "walk_forward": walk_forward,
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


def _fuse_batch(
    parents: list[Mapping[str, Any]], snapshot: Mapping[str, Any], *, generation_id: str,
    start_index: int, pairs: int, seen_hashes: set[str], evidence_sha: str, now: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fuse ranked parents pairwise until ``pairs`` children carry evidence.

    Children are backtested on their own — a crossover inherits its parents' rules,
    never their evidence, so a child that overfits cannot ride a parent's score.
    A child that closed **no** trades is refused rather than stored: an unsatisfiable
    union (``rsi <= 30`` from one parent, ``rsi >= 70`` from the other) parses and
    validates perfectly well and would otherwise sit in the store as a scored
    candidate that can never trade."""
    minted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for left, right in combinations(parents, 2):
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
        evidence = backtest_spec(child, snapshot)
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
) -> dict[str, Any]:
    """One factory run: generate → backtest → candidate records. Pure (no I/O).

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
    )

    candidates: list[dict[str, Any]] = []
    for spec_dict in batch["specs"]:
        spec = StrategySpec.from_dict(spec_dict)
        evidence = backtest_spec(spec, snapshot)
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
            rank_fusion_parents(existing_candidates),
            snapshot,
            generation_id=generation_id,
            start_index=len(candidates) + 1,
            pairs=fusion_pairs,
            seen_hashes={*known_hashes, *(c["strategy_rule_hash"] for c in candidates)},
            evidence_sha=candles_sha,
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
