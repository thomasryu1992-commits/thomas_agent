"""C3 strategy spec parse + entry evaluation tests (fail-closed semantics).

Ports the source system's contract: structural validation rejects malformed specs,
execution authority can never be granted, the rule hash verifies (not re-mints) on
parse, and the evaluator treats missing/None features as indeterminate — a strategy
never enters on data it could not evaluate."""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto.strategy import (
    Direction,
    MatchResult,
    RuleCondition,
    SpecParseError,
    StrategySpec,
    evaluate_condition,
    evaluate_spec,
    load_strategy_pool,
)


def _spec_dict(**overrides):
    base = {
        "schema_version": "strategy_spec.v1",
        "strategy_id": "S1",
        "strategy_version": "1.0",
        "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"],
        "timeframe": "1d",
        "direction": "long",
        "entry_rules": {
            "operator": "AND",
            "conditions": [
                {"feature": "close", "comparison": ">", "value_from": "ma20"},
                {"feature": "adx", "comparison": ">=", "value": 20.0},
            ],
        },
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    base.update(overrides)
    return base


ROW_MATCH = {"close": 105.0, "ma20": 100.0, "adx": 25.0}
ROW_NO_MATCH = {"close": 95.0, "ma20": 100.0, "adx": 25.0}


# --- structural parse: fail-closed ------------------------------------------

def test_valid_spec_parses_and_gets_rule_hash():
    spec = StrategySpec.from_dict(_spec_dict())
    assert spec.strategy_id == "S1" and spec.direction is Direction.LONG
    assert len(spec.strategy_rule_hash) == 64  # source format: plain hex, no prefix
    assert spec.referenced_features() == {"close", "ma20", "adx"}


def test_execution_authority_is_rejected():
    for forbidden in ("can_submit_orders", "can_modify_runtime"):
        with pytest.raises(SpecParseError):
            StrategySpec.from_dict(_spec_dict(**{forbidden: True}))


def test_tampered_rule_hash_is_rejected():
    good = StrategySpec.from_dict(_spec_dict())
    with pytest.raises(SpecParseError):
        StrategySpec.from_dict(_spec_dict(strategy_rule_hash="0" * 64))
    # A provided, correct hash verifies.
    reparsed = StrategySpec.from_dict(_spec_dict(strategy_rule_hash=good.strategy_rule_hash))
    assert reparsed.strategy_rule_hash == good.strategy_rule_hash


def test_rule_hash_ignores_volatile_metadata():
    a = StrategySpec.from_dict(_spec_dict())
    b = StrategySpec.from_dict(_spec_dict(strategy_id="S2", strategy_version="9.9", status="PAPER_ACTIVE"))
    assert a.strategy_rule_hash == b.strategy_rule_hash  # identity is what it does, not what it's called


@pytest.mark.parametrize("mutation", [
    {"timeframe": "2h"},
    {"direction": "up"},
    {"symbol_scope": []},
    {"entry_rules": {"operator": "NOT", "conditions": [{"feature": "x", "comparison": ">", "value": 1}]}},
    {"entry_rules": {"operator": "AND", "conditions": []}},
    {"exit_rules": {"stop_model": "fixed", "stop_atr": 1, "target_atr": 1, "max_holding_bars": 1}},
    {"exit_rules": {"stop_model": "atr", "stop_atr": -1, "target_atr": 1, "max_holding_bars": 1}},
    {"risk_constraints": {"max_risk_per_trade_R": 0}},
    {"strategy_id": ""},
])
def test_malformed_specs_fail_closed(mutation):
    with pytest.raises(SpecParseError):
        StrategySpec.from_dict(_spec_dict(**mutation))


def test_condition_must_set_exactly_one_operand():
    with pytest.raises(SpecParseError):
        RuleCondition.from_dict({"feature": "x", "comparison": ">"}, where="t")
    with pytest.raises(SpecParseError):
        RuleCondition.from_dict(
            {"feature": "x", "comparison": ">", "value": 1, "value_from": "y"}, where="t"
        )


# --- evaluation: indeterminate never matches --------------------------------

def test_and_spec_matches_and_reports_direction():
    result = evaluate_spec(StrategySpec.from_dict(_spec_dict()), ROW_MATCH)
    assert result == MatchResult(matched=True, direction="LONG", condition_results=(True, True))


def test_and_spec_no_match_on_false_condition():
    result = evaluate_spec(StrategySpec.from_dict(_spec_dict()), ROW_NO_MATCH)
    assert result.matched is False and result.direction is None


def test_missing_feature_is_indeterminate_and_blocks_and_entry():
    row = {"close": 105.0, "ma20": 100.0}  # adx missing
    result = evaluate_spec(StrategySpec.from_dict(_spec_dict()), row)
    assert result.condition_results == (True, None)
    assert result.matched is False  # AND with an indeterminate leg never enters


def test_nan_feature_is_indeterminate():
    cond = RuleCondition(feature="adx", comparison=">", value=1.0)
    assert evaluate_condition(cond, {"adx": float("nan")}) is None


def test_or_spec_matches_only_on_genuine_true():
    spec = StrategySpec.from_dict(_spec_dict(
        direction="short",
        entry_rules={
            "operator": "OR",
            "conditions": [
                {"feature": "rsi", "comparison": ">=", "value": 70.0},
                {"feature": "bb_percent_b", "comparison": ">", "value": 1.0},
            ],
        },
    ))
    assert evaluate_spec(spec, {"rsi": 75.0}).matched is True
    assert evaluate_spec(spec, {"rsi": 75.0}).direction == "SHORT"
    # Both indeterminate → no match, not a default entry.
    assert evaluate_spec(spec, {}).matched is False


def test_string_operand_in_ordering_comparison_is_indeterminate():
    cond = RuleCondition(feature="market_regime", comparison=">", value=1.0)
    assert evaluate_condition(cond, {"market_regime": "TREND"}) is None


def test_equality_comparison_supports_labels():
    cond = RuleCondition(feature="market_regime", comparison="==", value="TREND")
    assert evaluate_condition(cond, {"market_regime": "TREND"}) is True
    assert evaluate_condition(cond, {"market_regime": "RANGE"}) is False


# --- pool loader: one bad spec poisons the load ------------------------------

def test_load_strategy_pool_parses_members():
    pool = {"active_strategies": [{"strategy_spec": _spec_dict()}, {"strategy_spec": _spec_dict(strategy_id="S2")}]}
    specs = load_strategy_pool(pool)
    assert [s.strategy_id for s in specs] == ["S1", "S2"]


def test_load_strategy_pool_fails_closed_on_any_bad_member():
    pool = {"active_strategies": [{"strategy_spec": _spec_dict()}, {"strategy_spec": _spec_dict(timeframe="2h")}]}
    with pytest.raises(SpecParseError):
        load_strategy_pool(pool)


@pytest.mark.parametrize("bad", [None, [], {"active_strategies": None}, {"active_strategies": [{}]}])
def test_load_strategy_pool_rejects_malformed_containers(bad):
    with pytest.raises(SpecParseError):
        load_strategy_pool(bad)
