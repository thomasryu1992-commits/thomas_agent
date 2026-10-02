"""The holding clock counts confirmed bars, not scheduler passes (2026-10-02).

`trade_plan.advance_holding` is the one rule paper, the counterfactual book and the live leg share for
"a bar has passed". It used to advance on a ``None`` timestamp, which is what a degraded collection
produces: every failed 15-minute pass on a position's own timeframe spent one of its bars. These pin
the corrected rule at the function and at the paper-side caller. The live leg's cases live with its
other clock tests in `test_mvp_runtime_crypto_live_route.py`.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import trade_plan

T0 = "2026-07-28T00:00:00Z"
T1 = "2026-07-28T04:00:00Z"


def _position(**kw):
    base = {"direction": "LONG", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 110.0,
            "risk": 5.0, "holding_candles": 10, "last_counted_candle_ts": T0}
    base.update(kw)
    return base


def test_a_new_confirmed_bar_counts_once_and_is_remembered():
    # Case A
    position = _position()
    assert trade_plan.advance_holding(position, T1) == trade_plan.HOLD_NEW_BAR
    assert position["holding_candles"] == 11 and position["last_counted_candle_ts"] == T1


def test_the_same_bar_again_counts_nothing():
    # Case B
    position = _position()
    assert trade_plan.advance_holding(position, T0) == trade_plan.HOLD_DUPLICATE_BAR
    assert position["holding_candles"] == 10 and position["last_counted_candle_ts"] == T0


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_no_bar_timestamp_spends_nothing(missing):
    # Case C: a pass with no confirmed bar is not a bar, however many of them run.
    position = _position()
    for _ in range(5):
        assert trade_plan.advance_holding(position, missing) == trade_plan.HOLD_NO_CONFIRMED_BAR
    assert position["holding_candles"] == 10 and position["last_counted_candle_ts"] == T0


def test_an_outage_then_a_bar_counts_one_bar_not_the_missed_passes():
    # Case F: two passes with no bar, then a bar. No catch-up.
    position = _position()
    trade_plan.advance_holding(position, None)
    trade_plan.advance_holding(position, None)
    assert position["holding_candles"] == 10
    trade_plan.advance_holding(position, T1)
    assert position["holding_candles"] == 11


def test_paper_does_not_age_a_position_on_a_degraded_pass_and_does_not_time_it_out():
    """`settle_trade_plan` with no candle and no close is the degraded paper pass: nothing to
    settle, and since 2026-10-02 nothing spent either."""
    position = _position(holding_candles=11)
    assert trade_plan.settle_trade_plan(position, None, None, 12, False) == (None, None, None)
    assert position["holding_candles"] == 11


def test_paper_time_exit_still_fires_on_the_bar_that_reaches_the_limit():
    # Case G at the paper caller: 11 of 12, a degraded pass holds, the next real bar exits.
    position = _position(holding_candles=11)
    trade_plan.settle_trade_plan(position, None, None, 12, False)
    assert position["holding_candles"] == 11
    candle = {"close_time": T1, "high": 101.0, "low": 99.0, "close": 100.5}
    reason, price, _r = trade_plan.settle_trade_plan(position, candle, 100.5, 12, False)
    assert position["holding_candles"] == 12
    assert reason == "time_exit" and price == 100.5
