"""The metrics basket screen (METRICS_BASKET_SCREEN_V0.1, Thomas 2026-11-11): the registered window and strategy,
coverage before any return, the verdict's edges, and the host refresh. Synthetic markets only."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone

import pytest

from scripts import binance_archive as ba
from scripts.research import basket_search as bs
from scripts.research import cross_section_basket as xsb
from scripts.research import metrics_basket_diagnostic as md
from scripts.research import metrics_basket_screen as sn
from scripts.research import metrics_basket_search as ms
from scripts.research import momentum_basket_screen as mbs

W0, W1 = sn.SCREEN_WINDOW
FIRST = date(2026, 9, 1)
N = 40
GUEST = "S30USDT"                        # heavy volume through January only: a February member, not a March one


def _ms(day: date, hour: int) -> int:
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).timestamp() * 1000)


def _market(*, crowded_lose: bool = True, last: date = W1):
    """``N`` coins, volume falling by index, so the top 30 are the first 30. Open-interest growth and the long share
    of all accounts rise with the index: M04 buys the low end and sells the high end of the top 30."""
    market, daily = xsb.Market(), {}
    days = [FIRST + timedelta(days=k) for k in range((last - FIRST).days + 1)]
    for i in range(N):
        name = f"S{i:02d}USDT"
        drift = (14.5 - i) * 0.0004 * (1 if crowded_lose else -1)
        level, bars, rows = 100.0, {}, {}
        for k, day in enumerate(days):
            close = level * math.exp(drift + (0.003 if (k + i) % 2 else -0.003))
            volume = 1000.0 - i
            if name == GUEST:
                volume = 5000.0 if day < date(2027, 2, 1) else 1.0
            bars[day] = xsb.Bar(open=level, close=close, quote_volume=volume)
            level = close
            rows[day] = {"oi": 1000.0 * (1 + 0.001 * i) ** k, "ls": 0.30 + 0.01 * i, "top": 0.5, "taker": 0.5}
        market.bars[name], daily[name] = bars, rows
        market.funding[name] = [(_ms(day, h), 0.0001) for day in days for h in (0, 8, 16)]
    return market, daily


def _screen(market, daily, **described):
    described = {"absent": set(), "funding_months": set(), "refreshed_through": W1, **described}
    return sn.screen(market, daily, **described)


def test_the_window_is_the_registered_one_and_cannot_be_moved():
    assert sn.SCREEN_WINDOW == (date(2027, 3, 1), date(2027, 6, 30))
    assert (W0 - timedelta(days=1)).weekday() == xsb.SUNDAY      # the first basket is decided the day before
    with pytest.raises(SystemExit):
        sn.main(["run", "--start", "2026-12-01"])
    with pytest.raises(SystemExit):
        sn.main(["run", "--candidate", "M03"])


def test_the_strategy_is_m04_as_it_was_measured():
    m04 = ms.CANDIDATES[sn.STRATEGY]
    assert sn.STRATEGY == ms.FINAL == "M04"
    assert m04.features == (("oi_growth", 1.0), ("ls_level", 1.0)) and m04.long_low is True
    assert m04.lookback == 30 and m04.inverse_vol is False
    assert (sn.TOP_N, bs.CADENCE, ms.MIN_ROWS, bs.MIN_SHARE) == (30, "S-MOM30", 144, 2 / 3)
    assert sn.LOOKBACK_DAYS == m04.lookback


def test_the_universe_table_is_the_one_every_m04_measurement_used():
    market, _ = _market()
    table = sn.universes(market, W0, W1)
    assert table == xsb.universes(market, top_n=bs.TOP_N, start=date(W0.year, W0.month, 1) - timedelta(days=1), end=W1)
    assert min(table) == date(2027, 2, 1)                          # February's members decide the first basket
    assert GUEST in table[date(2027, 2, 1)] and GUEST not in table[date(2027, 3, 1)]


def test_fading_the_crowd_is_kept_and_the_judged_run_is_m04s_own():
    market, daily = _market()
    report = _screen(market, daily)
    row = report["strategies"]["M04"]
    assert report["status"] == sn.JUDGED and row["verdict"] == sn.KEEP and row["mean_bps_per_day"] > 0
    held = ms.cut(market, daily, W1)
    direct = xsb.simulate(held, bs.CADENCE, sn.universes(market, W0, W1), start=W0, end=W1,
                          weigher=bs.weigher(ms.CANDIDATES["M04"], ms.FEATURES))
    assert row["sum_pct"] == round(sum(v["net"] for v in direct["daily"].values()) * 100, 4)
    assert min(direct["daily"]) == W0 and row["days"] == (W1 - W0).days + 1     # held from the window's first day
    assert set(report["shown_only"]) == {"random_baskets_mean_bps_per_day", "split"}
    assert report["definition"] == ms.CANDIDATES["M04"].as_dict()


def test_a_crowd_that_wins_is_dropped():
    market, daily = _market(crowded_lose=False)
    row = _screen(market, daily)["strategies"]["M04"]
    assert row["verdict"] == sn.DROP and row["mean_bps_per_day"] < 0


def test_a_window_without_a_basket_is_dropped():
    market, daily = _market()
    empty = {s: {d: {"oi": None, "ls": None, "top": None, "taker": None} for d in rows} for s, rows in daily.items()}
    row = _screen(market, empty)["strategies"]["M04"]
    assert row["verdict"] == sn.DROP and row["days"] == 0 and row["mean_bps_per_day"] == 0.0


def test_bars_and_metrics_after_the_window_do_not_change_the_result():
    market, daily = _market()
    late_market, late_daily = _market(last=W1 + timedelta(days=40))
    for s in late_daily:
        for d in late_daily[s]:
            if d > W1:
                late_daily[s][d] = {"oi": 1e-9, "ls": 0.999, "top": 0.5, "taker": 0.5}
    a, b = _screen(market, daily), _screen(late_market, late_daily)
    assert a["strategies"] == b["strategies"] and a["universe_hash"] == b["universe_hash"]


def test_klines_short_of_the_last_day_are_not_yet_and_carry_no_result():
    market, daily = _market(last=W1 - timedelta(days=3))
    report = _screen(market, daily)
    assert report["status"] == sn.NOT_YET and report["short"] == [f"klines through {W1 - timedelta(days=3)}"]
    assert not {"strategies", "shown_only", "universe_hash", "excused"} & set(report)


def test_a_members_traded_day_without_funding_is_not_yet_unless_the_archive_was_asked():
    market, daily = _market()
    day = date(2027, 4, 14)
    market.funding["S03USDT"] = [(t, r) for t, r in market.funding["S03USDT"] if xsb._utc_day(t) != day]
    report = _screen(market, daily)
    assert report["status"] == sn.NOT_YET and report["short"] == ["S03USDT funding 2027-04: 1 day(s)"]
    asked = _screen(market, daily, funding_months={("S03USDT", "2027-04")})    # the month's file is on disk: a real gap
    assert asked["status"] == sn.JUDGED and asked["excused"]["funding_days"] == 1
    outside, daily2 = _market()                                                  # a coin outside the top 30 is not asked
    outside.funding["S39USDT"] = []
    assert _screen(outside, daily2)["status"] == sn.JUDGED


def test_a_day_nobody_traded_needs_no_funding_and_a_held_over_week_does():
    market, daily = _market()
    bars = market.bars["S05USDT"]
    for d in [d for d in bars if d >= date(2027, 5, 20)]:                         # delisted: flat bars, no volume
        bars[d] = xsb.Bar(open=1.0, close=1.0, quote_volume=0.0)
    market.funding["S05USDT"] = [(t, r) for t, r in market.funding["S05USDT"] if xsb._utc_day(t) < date(2027, 5, 20)]
    assert _screen(market, daily)["status"] == sn.JUDGED

    def without(day):                                             # a fresh market: settlements are indexed once
        market, daily = _market()
        market.funding[GUEST] = [(t, r) for t, r in market.funding[GUEST] if xsb._utc_day(t) != day]
        return _screen(market, daily)

    assert without(date(2027, 3, 12))["status"] == sn.JUDGED      # past the week a February leg can still be held
    assert without(date(2027, 3, 3))["short"] == [f"{GUEST} funding 2027-03: 1 day(s)"]      # inside it


def test_a_metrics_day_not_built_is_not_yet_unless_the_archive_does_not_serve_it():
    market, daily = _market()
    day = date(2027, 2, 3)                                         # 26 days before March: inside March's lookback
    daily["S07USDT"].pop(day)
    report = _screen(market, daily)
    assert report["status"] == sn.NOT_YET
    assert report["short"] == ["S07USDT metrics for 2027-02: 1 day(s)", "S07USDT metrics for 2027-03: 1 day(s)"]
    served_not = {ba.daily_name("S07USDT", ba.METRICS, day)}
    excused = _screen(market, daily, absent=served_not)
    assert excused["status"] == sn.JUDGED and excused["excused"]["metrics_days"] == 2
    early = date(2026, 12, 20)                                     # before any member month's 30 days: never read
    daily["S08USDT"].pop(early)
    assert _screen(market, daily, absent=served_not)["status"] == sn.JUDGED


def test_excused_metrics_days_must_have_been_asked_again_after_the_window():
    market, daily = _market()
    for through in (None, W1 - timedelta(days=1)):
        report = _screen(market, daily, refreshed_through=through)
        assert report["status"] == sn.NOT_YET and len(report["short"]) == 1 and "asked again" in report["short"][0]
    assert _screen(market, daily, refreshed_through=W1)["status"] == sn.JUDGED


def test_an_archive_wide_hole_blocks():
    market, daily = _market()
    hole = date(2027, 4, 7)
    for name in list(market.bars)[:20]:
        market.bars[name].pop(hole)
    with pytest.raises(mbs.Hole):
        _screen(market, daily)


def test_the_split_is_shown_and_never_moves_the_verdict(monkeypatch):
    market, daily = _market()
    report = _screen(market, daily)
    split = report["shown_only"]["split"]
    assert split["totals_pct"]["net"] == pytest.approx(report["strategies"]["M04"]["sum_pct"], abs=0.011)
    assert set(split["by_settlements_per_day"]) == {"3"} and split["by_side_pct"]["long"]["price"] > 0

    def broken(*a, **k):
        raise md.DoesNotReconcile("day 2027-03-02: funding")

    monkeypatch.setattr(md, "diagnose", broken)
    again = _screen(market, daily)
    assert again["strategies"] == report["strategies"] and "unavailable" in again["shown_only"]["split"]
    flat = json.dumps(report["strategies"]).lower() + " ".join(report["shown_only"])
    assert "sharpe" not in flat and "pass" not in flat


def test_it_refuses_outside_the_sandbox_and_refresh_refuses_inside(monkeypatch, capsys, tmp_path):
    assert sn.main(["run", "--cache", str(tmp_path)]) == sn.EXIT_USAGE
    assert "sandbox" in capsys.readouterr().err
    monkeypatch.setattr(sn.xsb, "_in_sandbox", lambda: True)
    assert sn.main(["refresh", "--cache", str(tmp_path)]) == sn.EXIT_USAGE
    assert "host" in capsys.readouterr().err


# --- the host refresh ----------------------------------------------------------------------------------------

class Fetches:
    def __init__(self, missing=()):
        self.metrics, self.monthly, self.missing = [], [], set(missing)

    def fetch(self, dest, *, symbols, start, end):
        self.metrics.append((symbols[0], start, end))
        return {symbols[0]: {"kept": (end - start).days + 1, "present": 0, "missing": 0}}

    def fetch_monthly(self, dest, *, kind, symbols, start, end):
        self.monthly.append((kind, tuple(symbols), start, end))


def _refresh(monkeypatch, tmp_path, market, *, today, quiet=lambda: False):
    (tmp_path / "archive-metrics").mkdir(exist_ok=True)
    monkeypatch.setattr(sn.xsb, "read_contract_types", lambda archive: ({}, "now"))
    monkeypatch.setattr(sn.xsb, "load_market", lambda archive, funding=True: market)
    monkeypatch.setattr(sn.ms, "build", lambda cache, say=None: 7)
    fake, said = Fetches(), []
    code = sn.refresh(tmp_path, today=today, fetch=fake.fetch, fetch_monthly=fake.fetch_monthly, quiet=quiet,
                      say=said.append)
    return code, fake, said


def test_refresh_finds_nothing_before_the_klines_can_name_a_member(monkeypatch, tmp_path):
    market, _ = _market(last=date(2026, 12, 31))                  # February's ranking needs January's klines
    code, fake, said = _refresh(monkeypatch, tmp_path, market, today=date(2027, 1, 2))
    assert code == sn.EXIT_OK and fake.metrics == [] and "nothing to fetch yet" in said[0]
    assert sn.read_refreshed_through(tmp_path) is None


def test_refresh_fetches_each_members_month_and_the_thirty_days_before_never_a_day_too_young(monkeypatch, tmp_path):
    market, _ = _market(last=date(2027, 2, 28))                   # names February's and March's members
    today = date(2027, 3, 2)
    through = today - timedelta(days=sn.PUBLICATION_LAG_DAYS)
    missing = tmp_path / "archive-metrics" / ba.MISSING_FILENAME
    missing.parent.mkdir()
    stale = ba.daily_name("S02USDT", ba.METRICS, date(2027, 2, 10))
    other = ba.daily_name("S39USDT", ba.METRICS, date(2027, 2, 10))            # not a member: left alone
    missing.write_text(json.dumps({"S02USDT": [stale], "S39USDT": [other]}))
    code, fake, said = _refresh(monkeypatch, tmp_path, market, today=today)
    assert code == sn.EXIT_OK
    spans = {s: (a, b) for s, a, b in fake.metrics}
    assert spans["S02USDT"] == (date(2027, 1, 2), through)       # February − 30 days, through the newest safe day
    assert spans[GUEST] == (date(2027, 1, 2), through)           # the February-only member too
    assert "S39USDT" not in spans and max(b for _, b in spans.values()) == through < today
    assert json.loads(missing.read_text()) == {"S39USDT": [other]}             # the member's excused day is asked again
    kind, members, start, end = fake.monthly[0]
    assert kind == "fundingRate" and GUEST in members and "S02USDT" in members and start == date(2027, 2, 1)
    marker = json.loads((tmp_path / "archive-metrics" / sn.REFRESH_MARKER).read_text())
    assert marker["through"] == through.isoformat() and marker["members_named_through"] == "2027-03"
    assert sn.read_refreshed_through(tmp_path) is None            # June's members are not named yet


def test_refresh_after_the_window_records_that_everything_was_asked_again(monkeypatch, tmp_path):
    market, _ = _market()
    code, fake, _ = _refresh(monkeypatch, tmp_path, market, today=W1 + timedelta(days=sn.PUBLICATION_LAG_DAYS))
    assert code == sn.EXIT_OK and max(b for _, _, b in fake.metrics) == W1
    assert sn.read_refreshed_through(tmp_path) == W1
    code, fake, _ = _refresh(monkeypatch, tmp_path, market, today=W1 + timedelta(days=sn.PUBLICATION_LAG_DAYS - 1))
    assert sn.read_refreshed_through(tmp_path) == W1 - timedelta(days=1)       # a day too early is not through the end


def test_refresh_stops_inside_the_quiet_window(monkeypatch, tmp_path):
    market, _ = _market()
    code, fake, said = _refresh(monkeypatch, tmp_path, market, today=date(2027, 7, 5), quiet=lambda: True)
    assert code == sn.EXIT_BLOCKED and fake.metrics == [] and "quiet window" in said[-1]
    assert sn.read_refreshed_through(tmp_path) is None
