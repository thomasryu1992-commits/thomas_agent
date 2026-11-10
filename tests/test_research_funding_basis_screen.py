"""The funding-basis screen (STRATEGY_SCREEN_GATE_V0.1, Thomas 2026-11-09): one fixed window, kept on a positive
mean, nothing about returns before the window is covered, a gap blocks. Nothing here reads a real cache."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from scripts.research import funding_basis as fb
from scripts.research import funding_basis_screen as sc

START = date(2027, 1, 1)
END = date(2027, 1, 31)


def _ms(day: date, hour: int = 0) -> int:
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).timestamp() * 1000)


def _series(last: date = END, *, rate: float = 0.0001, settle_until: date | None = None,
            skip: date | None = None) -> fb.Series:
    days, settlements = {}, []
    day = START - timedelta(days=30)
    while day <= last:
        if day != skip:
            days[day] = fb.Day(index=100.0, premium=0.0, mark=100.0, mark_high=100.0)
        if day <= (settle_until or last):
            for hour in (0, 8, 16):
                settlements.append((_ms(day, hour), rate))
        day += timedelta(days=1)
    return fb.Series(days=days, settlements=settlements, first_archived=fb.ARCHIVE_FIRST_DAY, name="TESTUSDT")


def _screen(series: fb.Series) -> dict:
    return sc.screen({"TESTUSDT": series}, start=START, end=END)


def test_the_window_is_four_whole_calendar_months() -> None:
    assert sc.WINDOW_START.day == 1
    assert (sc.WINDOW_END + timedelta(days=1)).day == 1
    months = {(d.year, d.month) for d in sc._days(sc.WINDOW_START, sc.WINDOW_END)}
    assert len(months) == 4


def test_an_uncovered_window_says_not_yet_and_nothing_about_returns() -> None:
    report = _screen(_series(last=END - timedelta(days=5)))
    assert report["status"] == sc.NOT_YET
    assert report["data_through"] == (END - timedelta(days=5)).isoformat()
    assert "strategies" not in report and "input_digest" not in report


def test_bars_without_the_last_days_settlements_are_not_yet_covered() -> None:
    report = _screen(_series(settle_until=END - timedelta(days=1)))
    assert report["status"] == sc.NOT_YET


def test_a_missing_bar_inside_the_window_blocks() -> None:
    with pytest.raises(sc.Gap):
        _screen(_series(skip=START + timedelta(days=10)))


def test_a_positive_carry_is_kept_by_both_strategies() -> None:
    report = _screen(_series())
    assert report["status"] == sc.JUDGED
    rows = report["strategies"]
    assert {r["verdict"] for r in rows.values()} == {sc.KEEP}
    assert rows["B-ALL"]["days"] == 31


def test_a_negative_carry_is_dropped_and_an_idle_strategy_is_dropped_too() -> None:
    rows = _screen(_series(rate=-0.0001))["strategies"]
    assert rows["B-ALL"]["verdict"] == sc.DROP
    assert rows["B-POS"]["mean_bps_per_day"] == 0.0          # never entered
    assert rows["B-POS"]["verdict"] == sc.DROP               # the mean must be above zero, not at it


def test_the_screen_uses_the_registered_model_unchanged() -> None:
    s = _series()
    leg = fb.simulate_symbol("B-ALL", s, START, END)
    row = _screen(s)["strategies"]["B-ALL"]
    assert row["sum_pct"] == pytest.approx(round(sum(leg.returns.values()) * 100, 4))


def test_the_command_refuses_outside_the_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc.xsb, "_in_sandbox", lambda: False)
    assert sc.main(["run"]) == sc.EXIT_USAGE
