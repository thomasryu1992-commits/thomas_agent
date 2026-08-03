"""A candidate that cannot possibly enter, carrying evidence that says it traded.

`mark_price`, `index_price` and `mark_index_basis_bps` are **None on every bar** unless the
snapshot carries `mark_prices`/`index_prices`, which this runtime's collector does not. The
fail-closed evaluator reads None as indeterminate, so a spec naming one never matches. No trade,
which is the honest outcome and not the problem.

The problem, measured on this store: **20 candidates reference one of those columns and 9 of
them carry `closed_count > 0`.** They are C7 imports — scored in the source system, where the
feed supplied values. Here they are rankable, promotable in principle, and structurally
incapable of ever entering. Evidence that says "this traded and made X" for a trade this runtime
cannot reproduce is a backtest/live divergence wearing a perfectly ordinary candidate record.

Refused at mint, where it costs nothing and the replay rows are right there to ask. What is
"never supplied" is MEASURED over those rows rather than declared in a list — a list would go
stale the day a feed is configured, and would have to be maintained in the opposite direction
from the truth.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto.factory import (
    UNSUPPLIABLE_FEATURE,
    unsuppliable_features,
)
from runtime.mvp_runtime.crypto.strategy import StrategySpec


def _spec(*conditions):
    return StrategySpec.from_dict({
        "schema_version": "strategy_spec.v1", "strategy_id": "S1", "strategy_version": "1.0",
        "strategy_family": "x", "symbol_scope": ["BTCUSDT"], "timeframe": "1h",
        "direction": "long",
        "entry_rules": {"operator": "AND", "conditions": list(conditions)},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 3.0,
                       "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    })


LIVE = {"feature": "close", "comparison": ">", "value": 0.0}
DEAD = {"feature": "mark_price", "comparison": ">", "value": 0.0}
ROWS = [{"close": 1.0, "mark_price": None, "index_price": None},
        {"close": 2.0, "mark_price": None, "index_price": None}]


def test_a_column_that_is_none_on_every_bar_is_named():
    assert unsuppliable_features(_spec(DEAD), ROWS) == ["mark_price"]


def test_a_column_that_is_supplied_is_not():
    assert unsuppliable_features(_spec(LIVE), ROWS) == []


def test_one_observation_is_enough_to_be_supplied():
    """Sparse is not absent. A feed that answers on some bars supplies the column; the
    evaluator's own rule handles the bars it does not."""
    rows = [{"mark_price": None}, {"mark_price": 1900.0}, {"mark_price": None}]
    assert unsuppliable_features(_spec(DEAD), rows) == []


def test_a_value_from_reference_is_checked_too():
    """The right-hand side of a comparison is read from the row exactly like the left."""
    spec = _spec({"feature": "close", "comparison": ">", "value_from": "index_price"})
    assert unsuppliable_features(spec, ROWS) == ["index_price"]


def test_every_starved_feature_is_named_not_just_the_first():
    spec = _spec(DEAD, {"feature": "close", "comparison": ">", "value_from": "index_price"})
    assert unsuppliable_features(spec, ROWS) == ["index_price", "mark_price"]


def test_no_rows_claims_nothing():
    """Nothing was observed either way. A caller with no data must not be told a feature is
    missing — that is a claim, and this function only reports what it saw."""
    assert unsuppliable_features(_spec(DEAD), []) == []


def test_a_lagged_reference_is_checked_against_its_base_series():
    """`referenced_features` strips the lag, and it must: `prev_mark_price` is supplied exactly
    when `mark_price` is, so checking the lagged spelling would find nothing either way."""
    spec = _spec({"feature": "mark_price", "comparison": ">", "value": 0.0, "lag": 1})
    assert unsuppliable_features(spec, ROWS) == ["mark_price"]


def test_the_reason_says_what_it_means():
    assert "never supplies" in UNSUPPLIABLE_FEATURE


def test_the_check_runs_before_the_backtest(monkeypatch):
    """Scoring it is the waste, and the evidence is the hazard: a scored starved spec would
    carry numbers for a trade this runtime cannot reproduce."""
    import inspect

    from runtime.mvp_runtime.crypto import factory

    source = inspect.getsource(factory.run_factory)
    check = source.index("unsuppliable_features(spec, frame.rows)")
    score = source.index("backtest_spec(spec, snapshot, frame=frame)")
    assert check < score, "the refusal must come before the scoring it exists to avoid"
