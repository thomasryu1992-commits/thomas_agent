"""Taker order flow + session context — the two feature groups that are not price.

Everything else in the feature row is a transformation of the OHLCV series, so a
twenty-first template family built on it recombines what the pool already has. These two
groups are different in kind: who CROSSED the spread is invisible in OHLC (two bars with
identical open/high/low/close differ entirely depending on which side was the aggressor),
and who is at the desk is invisible in price altogether.

What is asserted here is mostly about **absence**, because that is where a new feature can
silently do damage. A column that fills with a constant when its input is missing lets a
mined condition match on a fabricated value — the `liquidation_spike_ratio` hazard this
repo already carries once. Every column added here must go indeterminate instead, and the
fail-closed evaluator then simply declines to trade.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import factory, features
from runtime.mvp_runtime.crypto.features import build_feature_rows, latest_feature_row
from runtime.mvp_runtime.crypto.market_data import (
    BinanceFuturesCollector,
    MockMarketDataCollector,
    collect_market_data,
)
from runtime.mvp_runtime.crypto.strategy import StrategySpec, evaluate_spec

NOW = "2026-07-29T00:00:00Z"


def _candle(hour, *, close=100.0, volume=100.0, taker_buy=None, trades=None, day="2026-07-20"):
    """One 1h bar. ``taker_buy=None`` omits the flow legs entirely — a pre-flow snapshot."""
    candle = {
        "open_time": f"{day}T{hour:02d}:00:00Z",
        "close_time": f"{day}T{hour + 1:02d}:00:00Z",
        "open": close, "high": close + 1.0, "low": close - 1.0, "close": close,
        "volume": volume,
    }
    if taker_buy is not None:
        candle["taker_buy_base"] = taker_buy
        candle["taker_buy_quote"] = taker_buy * close
        candle["quote_volume"] = volume * close
        candle["trade_count"] = trades if trades is not None else 50.0
    return candle


# --- the venue payload the collector used to discard ---------------------------

def test_parser_reads_all_twelve_kline_fields():
    """The row carries twelve fields and the parser read seven. Indices 7-10 are the
    whole point: they arrive in every response the collector already makes."""
    raw = (
        '[[1750000000000,"100.0","110.0","90.0","105.0","1000.0",1750003599999,'
        '"105000.0",250,"600.0","63000.0","0"]]'
    )
    parsed = BinanceFuturesCollector()._parse(raw, now_ms=1750003600000)
    assert len(parsed) == 1
    candle = parsed[0][1]
    assert candle.close == 105.0 and candle.volume == 1000.0
    assert candle.quote_volume == 105000.0
    assert candle.trade_count == 250.0
    assert candle.taker_buy_base == 600.0
    assert candle.taker_buy_quote == 63000.0


def test_parser_degrades_to_none_when_the_venue_stops_sending_flow_legs():
    """A payload change must not take down the cycle — only the specs that read flow.

    The OHLCV legs are the contract and a row missing them is still malformed; the flow
    legs are optional on purpose, so a shortened row yields None columns rather than a
    raised error that would stop every strategy, including the twenty that never look at
    flow."""
    raw = '[[1750000000000,"100.0","110.0","90.0","105.0","1000.0",1750003599999]]'
    candle = BinanceFuturesCollector()._parse(raw, now_ms=1750003600000)[0][1]
    assert candle.close == 105.0, "the OHLCV legs must still parse"
    assert candle.taker_buy_base is None
    assert candle.quote_volume is None


def test_parser_still_refuses_a_row_missing_the_ohlcv_legs():
    from runtime.mvp_runtime.errors import ToolError

    with pytest.raises(ToolError):
        BinanceFuturesCollector()._parse('[[1750000000000,"100.0"]]', now_ms=1750003600000)


def test_collected_snapshot_carries_the_flow_legs():
    snapshot, _record = collect_market_data(
        "BTCUSDT", "1h", collector=MockMarketDataCollector(), now=NOW, limit=5
    )
    assert all("taker_buy_base" in c for c in snapshot["candles"])
    assert all(c["taker_buy_base"] is not None for c in snapshot["candles"])


# --- the derived columns -------------------------------------------------------

def test_imbalance_is_the_signed_share_of_aggressive_buying():
    rows = build_feature_rows({"candles": [_candle(0, volume=100.0, taker_buy=75.0)]})
    assert rows[0]["taker_buy_ratio"] == pytest.approx(0.75)
    assert rows[0]["taker_flow_imbalance"] == pytest.approx(0.5)  # 2*0.75 - 1


def test_balanced_flow_is_zero_imbalance_not_missing():
    rows = build_feature_rows({"candles": [_candle(0, volume=100.0, taker_buy=50.0)]})
    assert rows[0]["taker_flow_imbalance"] == pytest.approx(0.0)


def test_average_trade_size_is_volume_over_trade_count():
    rows = build_feature_rows({"candles": [_candle(0, volume=100.0, taker_buy=50.0, trades=25.0)]})
    assert rows[0]["avg_trade_size"] == pytest.approx(4.0)


# --- absence is indeterminate, never a fabricated balance ----------------------

def test_missing_flow_legs_leave_every_derived_column_none():
    """The open-interest posture, not the spike-ratio one. A snapshot collected before the
    flow legs existed must not report balanced flow — it must report nothing."""
    rows = build_feature_rows({"candles": [_candle(h) for h in range(6)]})
    for column in ("taker_buy_ratio", "taker_flow_imbalance", "taker_flow_zscore",
                   "taker_flow_ma", "avg_trade_size", "avg_trade_size_zscore",
                   "trade_count_zscore"):
        assert all(r[column] is None for r in rows), f"{column} fabricated a value"


def test_zero_volume_bar_is_indeterminate_rather_than_a_zero_divide():
    rows = build_feature_rows({"candles": [_candle(0, volume=0.0, taker_buy=0.0)]})
    assert rows[0]["taker_buy_ratio"] is None
    assert rows[0]["taker_flow_imbalance"] is None


def test_impossible_buy_volume_reads_as_unknown_rather_than_clamped():
    """Buy volume above total volume is a broken payload. Clamping it to 1.0 would turn
    that into a maximally bullish reading — the direction that trades."""
    rows = build_feature_rows({"candles": [_candle(0, volume=100.0, taker_buy=140.0)]})
    assert rows[0]["taker_buy_ratio"] is None


def test_a_flow_spec_cannot_enter_without_flow_data():
    """The end-to-end consequence: no legs, no entry. Not a neutral reading, no entry."""
    spec = StrategySpec.from_dict({
        "schema_version": "strategy_spec.v1", "strategy_id": "S001", "strategy_version": "1.0",
        "strategy_family": "taker_flow_long", "direction": "long", "timeframe": "1h",
        "symbol_scope": ["BTCUSDT"],
        "entry_rules": {"operator": "AND", "conditions": [
            {"feature": "taker_flow_imbalance", "comparison": ">=", "value": -5.0},
        ]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.0, "target_atr": 2.0,
                       "max_holding_bars": 12},
    })
    row = latest_feature_row({"candles": [_candle(h) for h in range(6)]})
    # The threshold is below every possible imbalance, so this matches on ANY real reading.
    assert evaluate_spec(spec, row).matched is False, "matched on absent flow data"
    with_flow = latest_feature_row({"candles": [_candle(h, taker_buy=50.0) for h in range(6)]})
    assert evaluate_spec(spec, with_flow).matched is True


# --- session context -----------------------------------------------------------

@pytest.mark.parametrize("hour,expected", [
    (0, "ASIA"), (7, "ASIA"), (8, "EUROPE"), (15, "EUROPE"), (16, "US"), (23, "US"),
])
def test_session_blocks_cover_every_utc_hour(hour, expected):
    assert features.classify_session(hour) == expected


def test_session_is_labelled_from_the_bar_open():
    rows = build_feature_rows({"candles": [_candle(h) for h in (0, 9, 18)]})
    assert [r["session"] for r in rows] == ["ASIA", "EUROPE", "US"]


def test_weekend_bars_are_marked():
    # 2026-07-20 is a Monday; 2026-07-25 is a Saturday.
    weekday = build_feature_rows({"candles": [_candle(0, day="2026-07-20")]})[0]
    weekend = build_feature_rows({"candles": [_candle(0, day="2026-07-25")]})[0]
    assert weekday["day_type"] == "WEEKDAY"
    assert weekend["day_type"] == "WEEKEND"


def test_daily_bars_carry_no_session_label():
    """At 1d every bar opens at 00:00 UTC, so a session label would be the constant
    "ASIA" — a fabricated value a mined condition could match on, which is exactly the
    hazard this column must not add. It goes None instead."""
    daily = [
        {"open_time": f"2026-07-{20 + i}T00:00:00Z", "close_time": f"2026-07-{21 + i}T00:00:00Z",
         "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0}
        for i in range(3)
    ]
    rows = build_feature_rows({"candles": daily})
    assert all(r["session"] is None for r in rows)
    assert all(r["day_type"] is not None for r in rows), "a daily bar IS one calendar day"


# --- what the factory may mint --------------------------------------------------

def test_only_normalized_flow_columns_are_mintable():
    """Raw flow legs are venue-scale, exactly like raw open_interest: a threshold on
    `trade_count` means one thing on BTC and another on SOL, and another again next year.
    They ride the row as evidence and stay out of the minting vocabulary."""
    for name in ("taker_buy_ratio", "taker_flow_imbalance", "taker_flow_zscore",
                 "taker_flow_ma", "avg_trade_size_zscore", "trade_count_zscore"):
        assert name in factory.NUMERIC_FEATURES
    for name in ("quote_volume", "trade_count", "taker_buy_base", "taker_buy_quote",
                 "avg_trade_size"):
        assert name not in factory.NUMERIC_FEATURES, f"{name} is venue-scale and must not be mintable"


def test_session_is_categorical_so_no_threshold_can_be_mined_across_it():
    assert "session" in factory.CATEGORICAL_FEATURES
    assert "session" not in factory.NUMERIC_FEATURES
    spec = {
        "schema_version": "strategy_spec.v1", "strategy_id": "S001", "strategy_version": "1.0",
        "strategy_family": "x", "direction": "long", "timeframe": "1h", "symbol_scope": ["BTCUSDT"],
        "entry_rules": {"operator": "AND", "conditions": [
            {"feature": "session", "comparison": ">=", "value": "EUROPE"},
        ]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.0, "target_atr": 2.0, "max_holding_bars": 12},
    }
    verdict = factory.validate_strategy(StrategySpec.from_dict(spec))
    assert verdict["approved_for_backtest"] is False
    assert "BLOCK_INVALID_COMPARISON" in verdict["block_reasons"]


def test_an_invented_session_label_is_refused():
    spec = {
        "schema_version": "strategy_spec.v1", "strategy_id": "S001", "strategy_version": "1.0",
        "strategy_family": "x", "direction": "long", "timeframe": "1h", "symbol_scope": ["BTCUSDT"],
        "entry_rules": {"operator": "AND", "conditions": [
            {"feature": "session", "comparison": "==", "value": "TOKYO_LUNCH"},
        ]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.0, "target_atr": 2.0, "max_holding_bars": 12},
    }
    verdict = factory.validate_strategy(StrategySpec.from_dict(spec))
    assert verdict["approved_for_backtest"] is False
    assert "BLOCK_INVALID_FEATURE_VALUE" in verdict["block_reasons"]


def test_session_families_are_not_minted_at_daily():
    """The htf_* rule, applied to the other column that goes None by timeframe: a spec
    whose session condition can never be determined is permanently no-entry, so it is
    never minted rather than minted and silently inert."""
    hourly = {t.family for t in factory.templates_for_timeframe("1h")}
    daily = {t.family for t in factory.templates_for_timeframe("1d")}
    assert factory.SESSION_FAMILIES <= hourly
    assert not (factory.SESSION_FAMILIES & daily)
    # The flow families need no such gate — their legs ride the klines call at every
    # timeframe, so they must survive the top of the ladder.
    assert {"taker_flow_long", "taker_absorption_long"} <= daily


def test_the_new_families_mint_and_backtest():
    """They have to produce real evidence, not merely parse. A family that mints and then
    trades nothing is the failure mode `fusion` already refuses children for."""
    snapshot, _ = collect_market_data(
        "BTCUSDT", "1h", collector=MockMarketDataCollector(), now=NOW, limit=400
    )
    minted: dict[str, dict] = {}
    for n in range(12):  # enough generations for the rotation to reach every family
        for spec in factory.generate_batch(f"GEN-{n:03d}", seed=n, timeframe="1h")["specs"]:
            minted.setdefault(spec["strategy_family"], spec)

    wanted = factory.SESSION_FAMILIES | {"taker_flow_long", "taker_flow_short",
                                         "taker_absorption_long", "taker_absorption_short"}
    assert wanted <= set(minted), f"never minted: {sorted(wanted - set(minted))}"

    traded = 0
    for family in sorted(wanted):
        evidence = factory.backtest_spec(StrategySpec.from_dict(minted[family]), snapshot)
        assert evidence["bars_replayed"] > 0
        traded += 1 if evidence["closed_count"] else 0
    assert traded, "not one new family closed a trade on a 400-bar replay"


def test_the_session_choice_is_charged_as_a_free_parameter():
    """Which session a spec claims is a search decision, so it must cost what a threshold
    costs. Routing it through a mutated parameter (rather than hard-coding "US" in the
    template) is what makes `count_free_parameters` see it."""
    from runtime.mvp_runtime.crypto.robustness import count_free_parameters

    spec_dict = next(
        s for n in range(12)
        for s in factory.generate_batch(f"GEN-{n:03d}", seed=n, timeframe="1h")["specs"]
        if s["strategy_family"] == "session_trend_long"
    )
    spec = StrategySpec.from_dict(spec_dict)
    session_conditions = [c for c in spec.entry_rules.conditions if c.feature == "session"]
    assert len(session_conditions) == 1 and session_conditions[0].value in features.SESSION_VALUES
    # adx threshold + session label + the three exit parameters.
    assert count_free_parameters(spec) == 5
