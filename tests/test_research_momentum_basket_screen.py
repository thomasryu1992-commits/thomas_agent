"""The momentum basket screen (MOMENTUM_BASKET_V0.1, Thomas 2026-11-10): the one change (inverse-volatility
sizing), coverage before any return, the fixed window and the verdict's edges. Synthetic markets only."""

from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timedelta, timezone

import pytest

from scripts.research import cross_section_basket as xsb
from scripts.research import momentum_basket_screen as mb

W0, W1 = mb.SCREEN_WINDOW
FIRST = date(2026, 9, 1)
N = 40


def _ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def _market(*, flip: bool = False, last: date = W1, symbols: int = N) -> xsb.Market:
    """``symbols`` coins with volume falling by index. Coin ``i`` drifts by ``(i − 19.5) × 0.05%`` a day with a
    wiggle that differs by coin; with ``flip`` the drift changes sign every 30 days, so last month's winners lose."""
    market = xsb.Market()
    days = [FIRST + timedelta(days=k) for k in range((last - FIRST).days + 1)]
    for i in range(symbols):
        name = f"S{i:02d}USDT"
        drift, wiggle = (i - 19.5) * 0.0005, 0.002 + 0.001 * (i % 5)
        level, bars = 100.0, {}
        for k, day in enumerate(days):
            sign = -1.0 if flip and (k // 30) % 2 else 1.0
            close = level * math.exp(sign * drift + (wiggle if k % 2 else -wiggle))
            bars[day] = xsb.Bar(open=level, close=close, quote_volume=1000.0 - i)
            level = close
        market.bars[name] = bars
        market.funding[name] = [(_ms(day) + 8 * 3_600_000, 0.0001) for day in days]
    return market


def test_the_window_is_the_registered_one_and_cannot_be_moved():
    assert mb.SCREEN_WINDOW == (date(2027, 2, 1), date(2027, 5, 31))
    assert (W0 - timedelta(days=1)).weekday() == xsb.SUNDAY      # the first basket is decided the day before
    with pytest.raises(SystemExit):
        mb.main(["run", "--start", "2026-12-01"])


def test_volatility_is_the_spread_of_the_thirty_daily_returns_or_nothing():
    market = _market()
    day = date(2027, 1, 31)
    bars = market.bars["S03USDT"]
    returns = [bars[day - timedelta(days=k)].close / bars[day - timedelta(days=k + 1)].close - 1 for k in range(30)]
    assert mb.trailing_vol(market, "S03USDT", day) == pytest.approx(statistics.stdev(returns))
    assert mb.VOL_LOOKBACK_DAYS == 30 and mb.MIN_VOL_RETURNS == 20
    for k in range(12):                                           # 11 closes gone leave fewer than 20 returns
        bars.pop(day - timedelta(days=2 * k + 1), None)
    assert mb.trailing_vol(market, "S03USDT", day) is None
    flat = xsb.Market(bars={"F": {day - timedelta(days=k): xsb.Bar(1.0, 1.0, 1.0) for k in range(40)}})
    assert mb.trailing_vol(flat, "F", day) is None


def test_the_baskets_are_s_mom30s_and_only_the_sizing_changes():
    market = _market()
    day = date(2027, 1, 31)
    members = xsb.universe_at(market, date(2027, 1, 1), top_n=mb.TOP_N)
    equal = xsb.target_weights(market, members, day, mb.SIGNAL)
    sized = mb.vol_weights(market, members, day, mb.SIGNAL)
    assert set(sized) == set(equal) and len(sized) == 12
    longs = {s: w for s, w in sized.items() if w > 0}
    shorts = {s: w for s, w in sized.items() if w < 0}
    assert {s for s, w in equal.items() if w > 0} == set(longs)
    assert sum(longs.values()) == pytest.approx(1.0) and sum(shorts.values()) == pytest.approx(-1.0)
    a, b = sorted(longs)[:2]
    assert longs[a] / longs[b] == pytest.approx(mb.trailing_vol(market, b, day) / mb.trailing_vol(market, a, day))
    assert len({round(w, 9) for w in longs.values()}) > 1         # not equal weights


def test_a_member_without_a_volatility_is_not_ranked():
    market = _market()
    day = date(2027, 1, 31)
    members = xsb.universe_at(market, date(2027, 1, 1), top_n=mb.TOP_N)
    best = max(xsb.target_weights(market, members, day, mb.SIGNAL).items(), key=lambda kv: kv[1])[0]
    for k in range(12):
        market.bars[best].pop(day - timedelta(days=2 * k + 1), None)
    assert best not in mb.vol_weights(market, members, day, mb.SIGNAL)


def test_persistent_winners_are_kept_and_the_judged_run_is_the_sized_one():
    market = _market()
    out = mb.screen(market)
    row = out["strategies"]["M"]
    assert out["status"] == mb.JUDGED and row["verdict"] == mb.KEEP and row["mean_bps_per_day"] > 0
    assert row["days"] == (W1 - W0).days + 1                       # held from the window's first day
    monthly = mb.universes(market, W0, W1)
    direct = xsb.simulate(market, mb.SIGNAL, monthly, start=W0, end=W1, weigher=mb.vol_weights)
    assert min(direct["daily"]) == W0
    assert row["mean_bps_per_day"] == pytest.approx(
        statistics.fmean(v["net"] for v in direct["daily"].values()) * 10_000, abs=1e-3)
    assert out["shown_only"]["equal_weight"]["mean_bps_per_day"] != row["mean_bps_per_day"]
    assert isinstance(out["shown_only"]["random_baskets_mean_bps_per_day"], float)


def test_winners_that_reverse_every_month_are_dropped():
    row = mb.screen(_market(flip=True))["strategies"]["M"]
    assert row["mean_bps_per_day"] < 0 and row["verdict"] == mb.DROP


def test_a_window_without_a_basket_is_dropped(monkeypatch):
    monkeypatch.setattr(mb, "TOP_N", 2)                            # two members cannot make a basket
    row = mb.screen(_market())["strategies"]["M"]
    assert row["days"] == 0 and row["mean_bps_per_day"] == 0 and row["verdict"] == mb.DROP


def test_bars_after_the_window_do_not_change_the_result():
    inside = mb.screen(_market())["strategies"]["M"]
    beyond = mb.screen(_market(last=W1 + timedelta(days=20)))["strategies"]["M"]
    assert beyond == inside and inside["forced_closes"] == 12      # every leg ends at the last day's close


def _assert_not_yet_without_returns(out: dict) -> None:
    assert out["status"] == mb.NOT_YET
    assert set(out) == {"spec", "model", "signal", "top_n", "window", "strategies_screened", "status", "short"}


def test_klines_short_of_the_last_day_are_not_yet():
    out = mb.screen(_market(last=W1 - timedelta(days=1)))
    _assert_not_yet_without_returns(out)
    assert out["short"] == ["klines through 2027-05-30"]


def test_a_month_with_too_few_perps_is_not_yet():
    market = _market()
    for name in list(market.bars)[5:]:
        market.bars[name] = {d: b for d, b in market.bars[name].items() if not (d.year == 2027 and d.month == 3)}
    out = mb.screen(market)
    _assert_not_yet_without_returns(out)
    assert out["short"] == ["klines 2027-03: 5 perps"]


def test_a_members_missing_funding_month_is_not_yet_and_named():
    market = _market()
    april = (_ms(date(2027, 4, 1)), _ms(date(2027, 5, 1)))
    market.funding["S04USDT"] = [(t, r) for t, r in market.funding["S04USDT"] if not april[0] <= t < april[1]]
    out = mb.screen(market)
    _assert_not_yet_without_returns(out)
    assert out["short"] == ["S04USDT funding 2027-04: 30 of 30 day(s)"]
    assert "S04USDT" in mb.funding_plan(market, W0, W1)["2027-04"]
    assert sorted(mb.funding_plan(market, W0, W1)) == ["2027-02", "2027-03", "2027-04", "2027-05"]


def test_funding_of_a_coin_outside_the_universe_is_not_asked_for():
    market = _market()
    market.funding["S39USDT"] = []                                 # ranked 40th by volume: never a member
    assert mb.screen(market)["status"] == mb.JUDGED


def test_an_archive_wide_hole_blocks():
    market = _market()
    hole = date(2027, 3, 17)
    for bars in market.bars.values():
        bars.pop(hole)
    with pytest.raises(mb.Hole, match="2027-03-17"):
        mb.screen(market)


def test_it_refuses_outside_the_sandbox(monkeypatch, capsys):
    monkeypatch.setattr(mb.xsb, "_in_sandbox", lambda: False)
    assert mb.main(["run"]) == mb.EXIT_USAGE
    assert "sandbox" in capsys.readouterr().err
