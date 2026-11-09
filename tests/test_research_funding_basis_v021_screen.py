"""The v0.2.1 funding-basis screen (FUNDING_BASIS_V0.2 §4, §7, §8; Thomas 2026-11-09): B-POS only, the window from
v0.2.1, coverage checked before any exclusion, nothing about returns before the window is covered, a delisting
inside the window closed at the last traded close. Nothing here reads a real cache."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from scripts.research import funding_basis as fb
from scripts.research import funding_basis_v02 as v
from scripts.research import funding_basis_v021_screen as s21

START, END = date(2027, 1, 1), date(2027, 1, 31)
SYMBOLS = ("AUSDT", "BUSDT")


@pytest.fixture(autouse=True)
def _two_members(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(v, "TOP_N", 2)


def _ms(day: date, hour: int = 0) -> int:
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).timestamp() * 1000)


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _series(name: str, *, last: date = END, settle_until: date | None = None, skip=(), index=lambda d: 100.0
            ) -> fb.Series:
    days, settlements = {}, []
    for day in _days(date(2026, 11, 1), last):
        if day not in skip:
            px = index(day)
            days[day] = fb.Day(index=px, premium=0.0, mark=px, mark_high=px)
        if day <= (settle_until or last):
            settlements.extend((_ms(day, h), 0.0001) for h in (0, 8, 16))
    return fb.Series(days=days, settlements=settlements, first_archived=fb.ARCHIVE_FIRST_DAY, name=name)


def _data(*, traded_until: dict[str, date] | None = None, volumes: dict | None = None, spot: dict | None = None,
          series: dict | None = None) -> s21.Forward:
    traded_until = traded_until or {}
    return s21.Forward(
        volumes={"2026-12": {"AUSDT": 2.0, "BUSDT": 1.0}} if volumes is None else volumes,
        traded={sym: set(_days(date(2026, 12, 1), traded_until.get(sym, END))) for sym in SYMBOLS},
        spot_months={sym: {"2026-12"} for sym in SYMBOLS} if spot is None else spot,
        spot_listing=set(SYMBOLS),
        series={sym: _series(sym) for sym in SYMBOLS} if series is None else series,
    )


def _screen(data: s21.Forward) -> dict:
    return s21.screen(data, start=START, end=END)


def test_the_window_and_the_screened_strategy_come_from_v021() -> None:
    assert s21.SCREENED == ("B-POS",)
    report = s21.screen(_data(volumes={}))
    assert report["window"] == [d.isoformat() for d in v.SCREEN_WINDOW]


def test_a_covered_window_is_judged_for_b_pos_only() -> None:
    report = _screen(_data())
    assert report["status"] == s21.JUDGED
    assert set(report["strategies"]) == {"B-POS"}
    assert report["strategies"]["B-POS"]["verdict"] == s21.KEEP
    assert report["strategies"]["B-POS"]["days"] == 31


def test_klines_short_of_the_window_end_say_not_yet_and_nothing_about_returns() -> None:
    data = _data()
    data.traded = {sym: {d for d in days if d <= END - timedelta(days=3)} for sym, days in data.traded.items()}
    report = _screen(data)
    assert report["status"] == s21.NOT_YET
    assert report["data_through"] == (END - timedelta(days=3)).isoformat()
    assert "strategies" not in report and "input_digest" not in report and "universe" not in report


def test_a_missing_ranking_month_is_not_yet() -> None:
    report = _screen(_data(volumes={"2026-11": {"AUSDT": 1.0}}))
    assert report["status"] == s21.NOT_YET and report["short"] == ["klines 2026-12"]


def test_a_member_whose_bars_stop_before_its_last_trade_is_not_yet_not_a_delisting() -> None:
    bars_stop = set(_days(END - timedelta(days=9), END))                  # settlements (archive) go on
    series = {"AUSDT": _series("AUSDT", skip=bars_stop), "BUSDT": _series("BUSDT")}
    report = _screen(_data(series=series))
    assert report["status"] == s21.NOT_YET and report["short"] == ["AUSDT"]
    assert "strategies" not in report


def test_the_last_days_settlements_must_be_in_before_the_window_is_covered() -> None:
    series = {"AUSDT": _series("AUSDT", settle_until=END - timedelta(days=1)), "BUSDT": _series("BUSDT")}
    assert _screen(_data(series=series))["status"] == s21.NOT_YET


def test_an_unreadable_member_that_traded_is_not_yet() -> None:
    report = _screen(_data(series={"AUSDT": None, "BUSDT": _series("BUSDT")}))
    assert report["status"] == s21.NOT_YET and report["short"] == ["AUSDT"]


def test_a_delisting_inside_the_window_closes_at_the_last_trade_even_when_bars_go_on() -> None:
    last_trade = START + timedelta(days=9)
    spliced = _series("AUSDT", index=lambda d: 100.0 if d <= START + timedelta(days=15) else 130_000.0)
    report = _screen(_data(traded_until={"AUSDT": last_trade}, series={"AUSDT": spliced, "BUSDT": _series("BUSDT")}))
    assert report["status"] == s21.JUDGED
    events = report["strategies"]["B-POS"]["events"]["AUSDT"]
    assert events["exits"] == 1 and events["resets"] == 0 and events["liquidations"] == 0
    assert report["strategies"]["B-POS"]["verdict"] == s21.KEEP


def test_a_ranking_month_with_klines_for_fewer_perps_than_the_universe_blocks() -> None:
    with pytest.raises(v.Blocked):
        _screen(_data(volumes={"2026-12": {"AUSDT": 2.0}}))             # the fetch missed BUSDT


def test_a_ranking_month_missing_from_the_spot_archive_is_not_yet() -> None:
    report = _screen(_data(spot={sym: {"2026-11"} for sym in SYMBOLS}))
    assert report["status"] == s21.NOT_YET and report["short"] == ["spot 2026-12"]


def test_a_top_perp_without_a_spot_pair_simply_drops_out_of_the_universe() -> None:
    report = _screen(_data(spot={"AUSDT": {"2026-12"}, "BUSDT": {"2026-10"}}))
    assert report["status"] == s21.JUDGED
    assert report["universe"]["members"] == {"2027-01": 1}
    assert set(report["strategies"]["B-POS"]["events"]) == {"AUSDT"}


def test_exclusions_are_listed_and_too_many_block(monkeypatch: pytest.MonkeyPatch) -> None:
    gap = {"AUSDT": _series("AUSDT", skip={START + timedelta(days=5)}), "BUSDT": _series("BUSDT")}
    with pytest.raises(v.Blocked):
        _screen(_data(series=gap))                                        # 1 of 2 member-months > 5%
    monkeypatch.setattr(v, "MAX_EXCLUDED_SHARE", 1.0)
    report = _screen(_data(series=gap))
    assert report["universe"]["excluded"] == {"AUSDT": ["2027-01"]}
    assert set(report["strategies"]["B-POS"]["events"]) == {"BUSDT"}


def test_the_command_refuses_outside_the_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(s21.xsb, "_in_sandbox", lambda: False)
    assert s21.main(["run"]) == s21.EXIT_USAGE
