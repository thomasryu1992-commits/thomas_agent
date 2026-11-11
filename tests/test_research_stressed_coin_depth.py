"""The stressed-coin depth measurement (STRESSED_COIN_COST_V0.1, Thomas 2026-11-11): the selection by membership
and settlement count, the depth of a day, the impact assumption, and what it never prints. Synthetic data only."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from scripts import binance_archive as ba
from scripts.research import basket_search as bs
from scripts.research import cross_section_basket as xsb
from scripts.research import stressed_coin_depth as sd

FIRST = date(2024, 9, 1)
LAST = date(2025, 4, 30)
SPAN = (date(2025, 1, 1), LAST)
N = 40
HOURLY = "S03USDT"                       # a member whose funding settles 24 times a day in March
HOT = (date(2025, 3, 10), date(2025, 3, 19))


def _ms(day: date, hour: int) -> int:
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).timestamp() * 1000)


def _market() -> xsb.Market:
    market = xsb.Market()
    days = [FIRST + timedelta(days=k) for k in range((LAST - FIRST).days + 1)]
    for i in range(N):
        name = f"S{i:02d}USDT"
        market.bars[name] = {d: xsb.Bar(open=100.0, close=100.0, quote_volume=1000.0 - i) for d in days}
        funding = []
        for d in days:
            hours = range(24) if name in (HOURLY, "S39USDT") and HOT[0] <= d <= HOT[1] else (0, 8, 16)
            funding += [(_ms(d, h), 0.0001) for h in hours]
        market.funding[name] = funding
    return market


def _rows(day: date, *, bid: float, ask: float, snapshots: int = 1200, thin_every: int = 0):
    out = []
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    for k in range(snapshots):
        stamp = f"{base + timedelta(seconds=30 * k):%Y-%m-%d %H:%M:%S}"
        scale = 0.1 if thin_every and k % thin_every == 0 else 1.0
        for pct, notional in ((-5, bid * 9), (-1, bid * scale), (1, ask * scale), (5, ask * 9)):
            out.append({"timestamp": stamp, "percentage": str(pct), "depth": "1.0", "notional": str(notional)})
    return out


def test_the_selection_is_by_membership_and_settlement_count_never_by_position():
    market = _market()
    chosen = sd.member_days(market, SPAN)
    stressed, control = chosen[sd.STRESSED], chosen[sd.CONTROL]
    assert stressed == [(HOURLY, HOT[0] + timedelta(days=k)) for k in range(10)]      # S39 is never a member
    assert len(control) == len(stressed) and not set(control) & set(stressed)
    assert all(SPAN[0] <= d <= SPAN[1] for _, d in control)
    assert all(s != "S39USDT" for s, _ in control)
    assert sd.member_days(market, SPAN)[sd.CONTROL] == control                         # the seeded draw repeats
    assert (sd.STRESSED_SETTLEMENTS, sd.CONTROL_SETTLEMENTS) == (9, 3)
    assert sd.SPAN == bs.CONFIRM and sd.SPAN[1] < bs.RESERVED_FROM


def test_a_days_depth_is_the_thinner_side_within_one_percent():
    day = date(2025, 3, 10)
    depth = sd.day_depth(_rows(day, bid=200_000.0, ask=300_000.0))
    assert depth == {"median": 200_000.0, "p10": 200_000.0}                            # the ±5% rows are not read
    thin = sd.day_depth(_rows(day, bid=200_000.0, ask=300_000.0, thin_every=5))        # a fifth of the day is thin
    assert thin["median"] == 200_000.0 and thin["p10"] == pytest.approx(20_000.0)
    assert sd.day_depth(_rows(day, bid=1.0, ask=1.0, snapshots=sd.MIN_SNAPSHOTS - 1)) is None     # a partial day
    one_sided = [r for r in _rows(day, bid=5.0, ask=5.0) if r["percentage"] != "1"]
    assert sd.day_depth(one_sided) is None                                             # no ask side: no snapshot counts


def test_the_impact_is_half_the_band_times_the_share_of_depth_eaten():
    assert sd.impact_bps(10_000, 200_000) == pytest.approx(2.5)
    assert sd.impact_bps(50_000, 200_000) == pytest.approx(12.5)
    assert sd.impact_bps(200_000, 200_000) == pytest.approx(50.0)
    assert sd.impact_bps(200_001, 200_000) == sd.OVER_BPS == 100.0
    assert sd.ORDER_SIZES_USD == (1_000, 10_000, 50_000) and sd.BAND_PCT == 1.0


def test_the_report_compares_the_two_classes_and_prints_no_return():
    market = _market()
    chosen = sd.member_days(market, SPAN)

    def read(symbol, day):
        if (symbol, day) == chosen[sd.CONTROL][0]:
            return None                                             # one control file was never served
        deep = (symbol, day) in set(chosen[sd.CONTROL])
        return _rows(day, bid=2_000_000.0 if deep else 40_000.0, ask=2_000_000.0 if deep else 40_000.0)

    report = sd.measure(chosen, read)
    hot, calm = report["classes"][sd.STRESSED], report["classes"][sd.CONTROL]
    assert hot["member_days_measured"] == 10 and calm["member_days_measured"] == 9 and calm["no_file"] == 1
    assert hot["impact_at_day_median"]["10000"]["median_bps"] == pytest.approx(12.5)
    assert calm["impact_at_day_median"]["10000"]["median_bps"] == pytest.approx(0.25)
    assert hot["impact_at_day_median"]["50000"]["share_over_band"] == 1.0              # 50k against 40k of depth
    assert report["engine_slippage_bps"] == xsb.cost.DEFAULT_SLIPPAGE_BPS and report["display_only"] is True
    flat = json.dumps(report).lower()
    for word in ("sharpe", "return", "net", "pnl", "verdict", "pass", "sum_pct"):
        assert word not in flat


def test_fetch_goes_through_the_archive_reader_one_run_of_days_at_a_time(tmp_path):
    chosen = {sd.STRESSED: [("A", date(2025, 3, 10)), ("A", date(2025, 3, 11)), ("A", date(2025, 3, 15))],
              sd.CONTROL: [("B", date(2025, 2, 1)), ("A", date(2025, 3, 12))]}
    assert sd.spans(p for pairs in chosen.values() for p in pairs) == [
        ("A", date(2025, 3, 10), date(2025, 3, 12)), ("A", date(2025, 3, 15), date(2025, 3, 15)),
        ("B", date(2025, 2, 1), date(2025, 2, 1))]
    calls, said = [], []

    def fetcher(dest, *, symbols, start, end, kind):
        calls.append((dest, symbols[0], start, end, kind))
        return {symbols[0]: {"kept": (end - start).days + 1, "present": 0, "missing": 0}}

    assert sd.fetch(tmp_path, chosen, fetcher=fetcher, quiet=lambda: False, say=said.append) == sd.EXIT_OK
    assert [c[1:] for c in calls][0] == ("A", date(2025, 3, 10), date(2025, 3, 12), ba.BOOK_DEPTH)
    assert all(c[0] == tmp_path / sd.DEPTH_DIR for c in calls) and "kept 5" in said[-1]
    calls.clear()
    assert sd.fetch(tmp_path, chosen, fetcher=fetcher, quiet=lambda: True, say=said.append) == sd.EXIT_BLOCKED
    assert calls == [] and "quiet window" in said[-1]


def test_measure_runs_only_in_the_sandbox_and_fetch_only_outside(monkeypatch, capsys, tmp_path):
    assert sd.main(["measure", "--cache", str(tmp_path)]) == sd.EXIT_USAGE
    assert "sandbox" in capsys.readouterr().err
    monkeypatch.setattr(sd.xsb, "_in_sandbox", lambda: True)
    assert sd.main(["fetch", "--cache", str(tmp_path)]) == sd.EXIT_USAGE
    assert "host" in capsys.readouterr().err
    assert sd.main(["measure", "--cache", str(tmp_path)]) == sd.EXIT_BLOCKED            # no archive there


def test_the_archive_reader_takes_the_depth_kind_and_names_no_project(tmp_path):
    seen = []

    def opener(url):
        seen.append(url)
        return None                                                # the archive answers 404: recorded, not kept

    got = ba.fetch(tmp_path, symbols=["ABCUSDT"], start=date(2025, 3, 10), end=date(2025, 3, 11), kind=ba.BOOK_DEPTH,
                   opener=opener, pause=0)
    assert got == {"ABCUSDT": {"kept": 0, "present": 0, "missing": 2}}
    assert seen[0].endswith("/daily/bookDepth/ABCUSDT/ABCUSDT-bookDepth-2025-03-10.zip")
    with pytest.raises(ValueError):
        ba.fetch(tmp_path, symbols=["ABCUSDT"], start=date(2025, 3, 10), end=date(2025, 3, 10), kind="aggTrades",
                 opener=opener, pause=0)
    assert ba.DAILY_KINDS == (ba.METRICS, ba.BOOK_DEPTH)
    assert "thomas" not in ba.USER_AGENT.lower() and "agent" not in ba.USER_AGENT.lower().replace("user-agent", "")
