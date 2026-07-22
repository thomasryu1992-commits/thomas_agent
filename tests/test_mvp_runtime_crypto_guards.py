"""C4 cycle-guard tests — data health, risk limits, stricter-wins verdict.

The contract's semantics under test: a failed guard degrades the cycle to
no-new-position mode (never blocks, never raises), synthetic/degraded data is never
trade-eligible, an unreadable risk history fails closed, and the merged verdict is
the stricter of the two guards."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.crypto.guards import (
    merge_trade_verdict,
    risk_guard_unreadable,
    run_data_health_check,
    run_risk_guard,
)

NOW = "2026-07-22T12:00:00Z"  # a Wednesday; week starts Monday 2026-07-20
_NOW_DT = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)


def _snapshot(n: int = 60, *, timeframe_minutes: int = 1440, is_synthetic: bool = False, **overrides):
    """A clean snapshot whose last candle closed one hour before NOW."""
    step = timedelta(minutes=timeframe_minutes)
    last_close = _NOW_DT - timedelta(hours=1)
    candles = []
    for i in range(n):
        close_time = last_close - (n - 1 - i) * step
        candles.append({
            "open_time": timeutil.format_iso(close_time - step),
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0,
            "close_time": timeutil.format_iso(close_time),
        })
    snap = {"symbol": "BTCUSDT", "timeframe": "1d", "candles": candles, "is_synthetic": is_synthetic}
    snap.update(overrides)
    return snap


def _outcome(result_r: float, closed_at: str, *, closed: bool = True):
    return {"result_R": result_r, "outcome_closed": closed, "created_at_utc": closed_at}


# --- data health --------------------------------------------------------------

def test_clean_real_snapshot_is_healthy():
    verdict = run_data_health_check(_snapshot(), now=NOW, timeframe_minutes=1440)
    assert verdict["status"] == "HEALTHY" and verdict["allow_trading"] is True
    assert verdict["problems"] == []


def test_synthetic_data_never_trade_eligible():
    verdict = run_data_health_check(_snapshot(is_synthetic=True), now=NOW, timeframe_minutes=1440)
    assert verdict["allow_trading"] is False
    assert "synthetic_data_source_blocks_trading" in verdict["problems"]


def test_degraded_collection_never_trade_eligible():
    verdict = run_data_health_check(_snapshot(degraded=True), now=NOW, timeframe_minutes=1440)
    assert verdict["allow_trading"] is False
    assert "degraded_collection_blocks_trading" in verdict["problems"]


def test_insufficient_candles_refused():
    verdict = run_data_health_check(_snapshot(10), now=NOW, timeframe_minutes=1440)
    assert verdict["allow_trading"] is False
    assert "insufficient_candle_count" in verdict["problems"]


def test_empty_snapshot_refused_not_crashed():
    verdict = run_data_health_check({"candles": []}, now=NOW, timeframe_minutes=1440)
    assert verdict["allow_trading"] is False
    assert "missing_latest_candle_time" in verdict["problems"]


def test_stale_data_refused():
    snap = _snapshot()
    stale_now = timeutil.format_iso(_NOW_DT + timedelta(days=4))  # 1d limit = 3 days
    verdict = run_data_health_check(snap, now=stale_now, timeframe_minutes=1440)
    assert verdict["allow_trading"] is False
    assert "stale_market_data" in verdict["problems"]


def test_candle_gap_detected():
    snap = _snapshot()
    del snap["candles"][30]  # a 2x-interval hole > 1.5x tolerance
    verdict = run_data_health_check(snap, now=NOW, timeframe_minutes=1440)
    assert verdict["allow_trading"] is False
    assert any(p.startswith("candle_gap_detected") for p in verdict["problems"])


def test_non_increasing_timestamps_detected():
    snap = _snapshot()
    snap["candles"][31]["open_time"] = snap["candles"][30]["open_time"]
    verdict = run_data_health_check(snap, now=NOW, timeframe_minutes=1440)
    assert any(p.startswith("non_increasing_timestamp") for p in verdict["problems"])


def test_broken_ohlc_logic_detected():
    snap = _snapshot()
    snap["candles"][-1]["high"] = 90.0  # high below open/close
    verdict = run_data_health_check(snap, now=NOW, timeframe_minutes=1440)
    assert verdict["allow_trading"] is False
    assert any(p.startswith("invalid_ohlc_logic") for p in verdict["problems"])


def test_non_positive_volume_detected():
    snap = _snapshot()
    snap["candles"][-1]["volume"] = 0.0
    verdict = run_data_health_check(snap, now=NOW, timeframe_minutes=1440)
    assert any(p.startswith("non_positive_volume") for p in verdict["problems"])


# --- risk guard ---------------------------------------------------------------

def test_no_history_allows_trading():
    verdict = run_risk_guard([], now=NOW)
    assert verdict["status"] == "NORMAL" and verdict["allow_new_position"] is True


def test_healthy_history_allows_trading():
    outcomes = [_outcome(1.5, "2026-07-21T10:00:00Z"), _outcome(-0.5, "2026-07-22T09:00:00Z")]
    verdict = run_risk_guard(outcomes, now=NOW)
    assert verdict["allow_new_position"] is True and verdict["problems"] == []


def test_daily_loss_limit_breached():
    outcomes = [_outcome(-1.0, "2026-07-22T08:00:00Z"), _outcome(-1.2, "2026-07-22T10:00:00Z")]
    verdict = run_risk_guard(outcomes, now=NOW)
    assert verdict["allow_new_position"] is False
    assert "daily_loss_limit_breached" in verdict["problems"]
    assert verdict["daily_pnl_r"] == -2.2


def test_weekly_loss_limit_breached_without_daily_breach():
    outcomes = [
        _outcome(-1.9, "2026-07-20T10:00:00Z"),
        _outcome(-1.9, "2026-07-21T10:00:00Z"),
        _outcome(0.5, "2026-07-21T18:00:00Z"),  # break the consecutive-loss streak
        _outcome(-1.8, "2026-07-22T10:00:00Z"),
    ]  # weekly -5.1 <= -5.0; daily -1.8 > -2.0; streak 1; drawdown -5.1 > -10
    verdict = run_risk_guard(outcomes, now=NOW)
    assert "weekly_loss_limit_breached" in verdict["problems"]
    assert "daily_loss_limit_breached" not in verdict["problems"]


def test_last_week_losses_do_not_count_against_this_week():
    outcomes = [_outcome(-4.0, "2026-07-17T10:00:00Z")]  # Friday last week
    verdict = run_risk_guard(outcomes, now=NOW)
    assert verdict["allow_new_position"] is True
    assert verdict["weekly_pnl_r"] == 0.0


def test_consecutive_losses_breached():
    outcomes = [
        _outcome(-0.3, "2026-07-10T10:00:00Z"),
        _outcome(-0.3, "2026-07-11T10:00:00Z"),
        _outcome(-0.3, "2026-07-12T10:00:00Z"),
    ]
    verdict = run_risk_guard(outcomes, now=NOW)
    assert "max_consecutive_losses_breached" in verdict["problems"]


def test_drawdown_breaker_uses_current_not_historical_max():
    # A deep historical dip that fully recovered must NOT latch the breaker.
    outcomes = (
        [_outcome(-1.0, f"2026-07-0{d}T10:00:00Z") for d in range(1, 10)]  # -9R dip
        + [_outcome(-1.5, "2026-07-10T10:00:00Z")]  # trough: -10.5R from peak
        + [_outcome(12.0, "2026-07-11T10:00:00Z")]  # full recovery to a new peak
    )
    verdict = run_risk_guard(outcomes, now=NOW)
    assert verdict["max_drawdown_r"] == -10.5
    assert verdict["drawdown_r"] == 0.0
    assert "max_drawdown_proxy_breached" not in verdict["problems"]


def test_current_drawdown_breaches():
    outcomes = [_outcome(-1.0, f"2026-06-{d:02d}T10:00:00Z") for d in range(1, 11)]  # -10R, old dates
    verdict = run_risk_guard(outcomes, now=NOW)
    assert "max_drawdown_proxy_breached" in verdict["problems"]
    # Old losses breach neither time-window limit — the drawdown is what refuses.
    assert "daily_loss_limit_breached" not in verdict["problems"]


def test_open_outcomes_are_ignored():
    outcomes = [_outcome(-99.0, "2026-07-22T10:00:00Z", closed=False)]
    verdict = run_risk_guard(outcomes, now=NOW)
    assert verdict["allow_new_position"] is True


def test_unreadable_history_fails_closed():
    verdict = risk_guard_unreadable("OSError: registry corrupt", now=NOW)
    assert verdict["allow_new_position"] is False
    assert verdict["problems"] == ["risk_history_unreadable"]
    assert "registry corrupt" in verdict["risk_history_error"]


# --- stricter-wins merge ------------------------------------------------------

def _healthy():
    return run_data_health_check(_snapshot(), now=NOW, timeframe_minutes=1440)


def _normal_risk():
    return run_risk_guard([], now=NOW)


def test_both_allow_yields_allow():
    verdict = merge_trade_verdict(_healthy(), _normal_risk())
    assert verdict == {
        "allow_new_position": True,
        "status": "ALLOW",
        "problems": [],
        "data_health": _healthy(),
        "risk_guard": _normal_risk(),
    }


@pytest.mark.parametrize("health_ok,risk_ok", [(False, True), (True, False), (False, False)])
def test_any_refusal_wins(health_ok, risk_ok):
    health = _healthy() if health_ok else run_data_health_check(
        _snapshot(is_synthetic=True), now=NOW, timeframe_minutes=1440
    )
    risk = _normal_risk() if risk_ok else risk_guard_unreadable("boom", now=NOW)
    verdict = merge_trade_verdict(health, risk)
    assert verdict["allow_new_position"] is False
    assert verdict["status"] == "NO_NEW_POSITION"
    assert verdict["problems"]  # every refusing guard's reasons ride along


def test_merge_collects_both_guards_reasons():
    health = run_data_health_check(_snapshot(is_synthetic=True), now=NOW, timeframe_minutes=1440)
    risk = risk_guard_unreadable("boom", now=NOW)
    verdict = merge_trade_verdict(health, risk)
    assert "synthetic_data_source_blocks_trading" in verdict["problems"]
    assert "risk_history_unreadable" in verdict["problems"]
