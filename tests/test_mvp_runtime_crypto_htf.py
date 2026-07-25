"""Higher-timeframe (HTF) context — the regime leg htf_* families filter on.

The load-bearing property is **no look-ahead**: at every lower bar the HTF read must
come from the last higher candle that had already CLOSED, never the one still forming.
That is what makes an htf_* backtest comparable to its live routing, and it is the
single easiest thing to get silently wrong in a multi-timeframe system — so it is
asserted directly against hand-built candles, not inferred from a passing backtest."""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import factory
from runtime.mvp_runtime.crypto.features import (
    HTF_COLUMNS,
    build_feature_rows,
    latest_feature_row,
)
from runtime.mvp_runtime.crypto.market_data import HIGHER_TIMEFRAME
from runtime.mvp_runtime.crypto.strategy import StrategySpec, evaluate_spec


def _candle(open_time, close_time, close, *, high=None, low=None):
    return {"open_time": open_time, "close_time": close_time,
            "open": close, "high": high if high is not None else close + 1,
            "low": low if low is not None else close - 1, "close": close, "volume": 10.0}


def _ltf_1h(n=8, day="2026-07-20"):
    """n hourly bars from 00:00 on ``day``."""
    return [_candle(f"{day}T{i:02d}:00:00Z", f"{day}T{i + 1:02d}:00:00Z", 100.0 + i) for i in range(n)]


def _htf_4h(day="2026-07-20"):
    """Two 4h candles: 00:00-04:00 and 04:00-08:00."""
    return [
        _candle(f"{day}T00:00:00Z", f"{day}T04:00:00Z", 100.0),
        _candle(f"{day}T04:00:00Z", f"{day}T08:00:00Z", 200.0),
    ]


# --- the look-ahead rule ------------------------------------------------------

def test_htf_read_never_comes_from_a_still_forming_candle():
    """A bar may only see a higher candle that already closed.

    Bars 00:00-03:00 open while the FIRST 4h candle is still forming: nothing has
    closed yet, so their HTF read is indeterminate — not a peek at that candle's
    eventual values. The bar opening at 04:00 is the first that may use it (it closed
    exactly then), and every bar before 08:00 keeps reading it rather than the second
    candle still in progress."""
    rows = build_feature_rows({"candles": _ltf_1h(), "htf_candles": _htf_4h()})
    # A distinguishing column that does not need a long warm-up: the regime label is
    # UNCLEAR/None-ish on short series, so assert on presence-vs-absence of the read.
    reads = [r["htf_market_regime"] for r in rows]
    assert reads[0] is reads[1] is reads[2] is reads[3] is None, "peeked at an unclosed candle"
    assert all(r is not None for r in reads[4:]), "closed candle was not visible"


def test_htf_alignment_holds_when_the_higher_series_is_unsorted():
    """The align sorts by event time, so candle order in the payload cannot leak a
    later read into an earlier bar."""
    ordered = build_feature_rows({"candles": _ltf_1h(), "htf_candles": _htf_4h()})
    shuffled = build_feature_rows({"candles": _ltf_1h(), "htf_candles": list(reversed(_htf_4h()))})
    assert [r["htf_market_regime"] for r in ordered] == [r["htf_market_regime"] for r in shuffled]


def test_htf_columns_are_none_without_a_higher_timeframe():
    """No HTF supplied → indeterminate, never a fabricated constant (the liquidation
    posture). This is what makes admitting the columns to the factory safe."""
    rows = build_feature_rows({"candles": _ltf_1h()})
    assert rows and all(rows[-1][col] is None for col in HTF_COLUMNS)


def test_unkeyable_higher_candle_is_invisible():
    broken = [{**c, "close_time": None} for c in _htf_4h()]
    rows = build_feature_rows({"candles": _ltf_1h(), "htf_candles": broken})
    assert all(rows[-1][col] is None for col in HTF_COLUMNS)


# --- fail-closed evaluation ---------------------------------------------------

def _htf_spec(**overrides):
    base = {
        "schema_version": "strategy_spec.v1",
        "strategy_id": "S1", "strategy_version": "1.0", "strategy_family": "htf_trend_long",
        "symbol_scope": ["BTCUSDT"], "timeframe": "1h", "direction": "long",
        "entry_rules": {"operator": "AND", "conditions": [
            {"feature": "htf_market_regime", "comparison": "==", "value": "TREND_UP"},
        ]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 3.0, "max_holding_bars": 12},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("comparison,value", [("==", "TREND_UP"), ("!=", "TREND_UP")])
def test_htf_spec_never_matches_without_the_higher_leg(comparison, value):
    """Both directions: an absent HTF read cannot satisfy `==` NOR `!=`. A `!=` that
    matched on None would turn a missing filter into a permissive one — exactly the
    liquidation_spike_ratio hazard, which these columns must not repeat."""
    row = latest_feature_row({"candles": _ltf_1h()})
    spec = StrategySpec.from_dict(_htf_spec(entry_rules={"operator": "AND", "conditions": [
        {"feature": "htf_market_regime", "comparison": comparison, "value": value}]}))
    assert evaluate_spec(spec, row).matched is False


def test_numeric_htf_condition_also_fails_closed():
    row = latest_feature_row({"candles": _ltf_1h()})
    for comparison, value in ((">", -1e12), ("<", 1e12)):  # both permissive directions
        spec = StrategySpec.from_dict(_htf_spec(entry_rules={"operator": "AND", "conditions": [
            {"feature": "htf_adx", "comparison": comparison, "value": value}]}))
        assert evaluate_spec(spec, row).matched is False


# --- factory vocabulary + families --------------------------------------------

def test_htf_features_are_mintable_and_validate():
    spec = StrategySpec.from_dict(_htf_spec(entry_rules={"operator": "AND", "conditions": [
        {"feature": "htf_market_regime", "comparison": "==", "value": "TREND_UP"},
        {"feature": "htf_adx", "comparison": ">=", "value": 20.0},
    ]}))
    assert factory.validate_strategy(spec)["approved_for_backtest"] is True


def test_htf_regime_rejects_a_value_outside_the_closed_vocabulary():
    spec = StrategySpec.from_dict(_htf_spec(entry_rules={"operator": "AND", "conditions": [
        {"feature": "htf_market_regime", "comparison": "==", "value": "MOON"}]}))
    assert "BLOCK_INVALID_FEATURE_VALUE" in factory.validate_strategy(spec)["block_reasons"]


@pytest.mark.parametrize("timeframe", sorted(HIGHER_TIMEFRAME))
def test_htf_families_are_minted_where_a_higher_timeframe_exists(timeframe):
    families = {t.family for t in factory.templates_for_timeframe(timeframe)}
    assert factory.HTF_FAMILIES <= families


def test_htf_families_are_withheld_at_the_top_of_the_ladder():
    """1d has no collected step above it: minting htf_* there would produce specs whose
    filter is permanently indeterminate — no-entry forever, not merely selective."""
    assert "1d" not in HIGHER_TIMEFRAME
    families = {t.family for t in factory.templates_for_timeframe("1d")}
    assert not (factory.HTF_FAMILIES & families)
    assert families, "the non-HTF rotation must survive at 1d"


def test_generated_htf_specs_pass_their_own_validator():
    batch = factory.generate_batch("GEN-001", seed=11, timeframe="1h")
    assert batch["accepted_count"] == batch["requested_count"]
    for spec_dict in batch["specs"]:
        spec = StrategySpec.from_dict(spec_dict)
        assert factory.validate_strategy(spec)["approved_for_backtest"] is True
