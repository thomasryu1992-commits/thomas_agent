"""The vocabulary could not say "crossed" — only "is above".

`macd > macd_signal` is a STATE. It is true for the whole of a trend, so a spec built on it
re-fires on every bar of that trend rather than on the bar the relationship changed. Writing
"crossed above" needs the previous bar, and there was no way to reach it: no lag operator, and
`value_from` reads the same row.

That is not a tuning problem. The search could not see the hypothesis at all, whatever it did
with parameters — which is the shape of the finding that opened this work: candidate expectancy
converges on the cost of trading as the sample grows, because the space being searched contains
no edge.

`lag` shifts the WHOLE condition back a bar, so a crossover is two ordinary conditions the
existing AND already combines. What these pin, in order of how much damage getting it wrong
would do:

1. every spec already in the store keeps its `strategy_rule_hash` — `from_dict` VERIFIES that
   hash rather than re-minting it;
2. a lagged condition on a row that has no previous bar is indeterminate, never a match;
3. both sides shift together, or a crossover silently becomes something else.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto.features import PREVIOUS_BAR_PREFIX, attach_previous_bar
from runtime.mvp_runtime.crypto.strategy import (
    MAX_CONDITION_LAG,
    SpecParseError,
    StrategySpec,
    evaluate_spec,
)

CROSSOVER = [
    {"feature": "macd", "comparison": ">", "value_from": "macd_signal"},
    {"feature": "macd", "comparison": "<=", "value_from": "macd_signal", "lag": 1},
]


def _spec_dict(conditions=None, **over):
    base = {
        "schema_version": "strategy_spec.v1",
        "strategy_id": "S1", "strategy_version": "1.0", "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"], "timeframe": "1h", "direction": "long",
        "entry_rules": {"operator": "AND", "conditions": conditions or [
            {"feature": "close", "comparison": ">", "value_from": "ma20"}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 3.0,
                       "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    base.update(over)
    return base


# --- 1. the identity hash ------------------------------------------------------

def test_a_spec_without_lag_hashes_exactly_as_before():
    """Pinned literally rather than recomputed — a test that recomputes both sides passes just
    as happily if the fingerprint changed for everyone. Computed on the commit before this."""
    spec = StrategySpec.from_dict(_spec_dict())
    assert spec.strategy_rule_hash == (
        "b1970aed63d55d6f9be2b21a81c054489fdd0f45419997abc3ca453402b0e575"
    )


def test_lag_zero_and_lag_absent_are_the_same_condition():
    absent = StrategySpec.from_dict(_spec_dict())
    explicit = StrategySpec.from_dict(_spec_dict(
        entry_rules={"operator": "AND", "conditions": [
            {"feature": "close", "comparison": ">", "value_from": "ma20", "lag": 0}]}))
    assert absent.strategy_rule_hash == explicit.strategy_rule_hash


def test_looking_back_is_a_different_strategy():
    now = StrategySpec.from_dict(_spec_dict())
    back = StrategySpec.from_dict(_spec_dict(
        entry_rules={"operator": "AND", "conditions": [
            {"feature": "close", "comparison": ">", "value_from": "ma20", "lag": 1}]}))
    assert now.strategy_rule_hash != back.strategy_rule_hash


def test_to_dict_round_trips_without_inventing_a_lag_key():
    raw = _spec_dict()
    spec = StrategySpec.from_dict(raw)
    assert spec.to_dict()["entry_rules"] == raw["entry_rules"]


def test_a_lagged_spec_round_trips_with_its_lag():
    spec = StrategySpec.from_dict(_spec_dict(
        entry_rules={"operator": "AND", "conditions": CROSSOVER}))
    reparsed = StrategySpec.from_dict(spec.to_dict())
    assert [c.lag for c in reparsed.entry_rules.conditions] == [0, 1]
    assert reparsed.strategy_rule_hash == spec.strategy_rule_hash


@pytest.mark.parametrize("bad", [-1, MAX_CONDITION_LAG + 1, 1.0, "1", True, None])
def test_an_unusable_lag_is_refused(bad):
    with pytest.raises(SpecParseError):
        StrategySpec.from_dict(_spec_dict(
            entry_rules={"operator": "AND", "conditions": [
                {"feature": "close", "comparison": ">", "value_from": "ma20", "lag": bad}]}))


# --- 2. the crossover, which is the point --------------------------------------

def _rows():
    """Three bars: below, crossing, then still above."""
    return attach_previous_bar([
        {"macd": 0.2, "macd_signal": 0.5},
        {"macd": 1.0, "macd_signal": 0.5},
        {"macd": 1.4, "macd_signal": 0.5},
    ])


def test_a_crossover_fires_once_where_a_state_fires_every_bar():
    """The whole defect in one assertion. The state version matches bars 2 AND 3; the crossover
    matches only the bar the relationship changed."""
    crossing = StrategySpec.from_dict(_spec_dict(
        entry_rules={"operator": "AND", "conditions": CROSSOVER}))
    state = StrategySpec.from_dict(_spec_dict(
        entry_rules={"operator": "AND", "conditions": [CROSSOVER[0]]}))
    rows = _rows()
    assert [evaluate_spec(state, r).matched for r in rows] == [False, True, True]
    assert [evaluate_spec(crossing, r).matched for r in rows] == [False, True, False]


def test_the_first_bar_has_no_previous_and_cannot_match():
    """Indeterminate, never a match — a spec that looks back must not match on a bar that has
    nothing behind it."""
    spec = StrategySpec.from_dict(_spec_dict(
        entry_rules={"operator": "AND", "conditions": CROSSOVER}))
    first = _rows()[0]
    assert first[f"{PREVIOUS_BAR_PREFIX}macd"] is None
    assert evaluate_spec(spec, first).matched is False


def test_a_row_built_without_previous_columns_is_indeterminate():
    """A caller that assembled a row by hand, or an older cached frame. The fail-closed
    evaluator's own rule: no data means no match, never an invented one."""
    spec = StrategySpec.from_dict(_spec_dict(
        entry_rules={"operator": "AND", "conditions": CROSSOVER}))
    assert evaluate_spec(spec, {"macd": 1.0, "macd_signal": 0.5}).matched is False


def test_both_sides_shift_together():
    """Shifting only the left side would compare an old MACD against a current signal — a
    different question, and one nobody asked for."""
    spec = StrategySpec.from_dict(_spec_dict(
        entry_rules={"operator": "AND", "conditions": [
            {"feature": "macd", "comparison": ">", "value_from": "macd_signal", "lag": 1}]}))
    # Previous bar had macd BELOW signal; current bar has it above. Lagged => False.
    row = {"macd": 9.0, "macd_signal": 0.0, "prev_macd": 0.2, "prev_macd_signal": 0.5}
    assert evaluate_spec(spec, row).matched is False


def test_a_lagged_condition_against_a_constant_reads_the_old_bar():
    spec = StrategySpec.from_dict(_spec_dict(
        entry_rules={"operator": "AND", "conditions": [
            {"feature": "rsi", "comparison": "<", "value": 30.0, "lag": 1}]}))
    assert evaluate_spec(spec, {"rsi": 55.0, "prev_rsi": 25.0}).matched is True
    assert evaluate_spec(spec, {"rsi": 25.0, "prev_rsi": 55.0}).matched is False


def test_referenced_features_names_the_base_series():
    """Callers check a spec against a vocabulary and against what a feed supplies, and both
    questions are about the underlying series — `prev_macd` exists exactly when `macd` does."""
    spec = StrategySpec.from_dict(_spec_dict(
        entry_rules={"operator": "AND", "conditions": CROSSOVER}))
    assert spec.referenced_features() == {"macd", "macd_signal"}


# --- 3. the rows themselves ----------------------------------------------------

def test_every_row_carries_the_one_before_it():
    rows = attach_previous_bar([{"close": 1.0}, {"close": 2.0}, {"close": 3.0}])
    assert [r.get("prev_close") for r in rows] == [None, 1.0, 2.0]


def test_previous_columns_are_not_themselves_lagged():
    """A row carries the bar before it, not the whole history: `prev_prev_*` would grow every
    row with something no condition can reference."""
    rows = attach_previous_bar([{"close": 1.0}, {"close": 2.0}, {"close": 3.0}])
    assert not any(k.startswith("prev_prev_") for r in rows for k in r)


def test_attaching_twice_does_not_compound():
    rows = attach_previous_bar([{"close": 1.0}, {"close": 2.0}])
    again = attach_previous_bar([dict(r) for r in rows])
    assert [r.get("prev_close") for r in again] == [None, 1.0]
    assert not any(k.startswith("prev_prev_") for r in again for k in r)
