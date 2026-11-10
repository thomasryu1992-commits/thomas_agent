"""Funding basis v0.2 (FUNDING_BASIS_V0.2, Thomas 2026-11-09): the point-in-time universe, leaving and delisting
closes, the missing-data exclusion, the block on too many exclusions, and that a symbol that is always a member is
v0.1's simulation exactly. Nothing here reads a real cache."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.research import funding_basis as fb
from scripts.research import funding_basis_v02 as v2

D0 = date(2021, 3, 1)


def _ms(day: date, hour: int = 0) -> int:
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).timestamp() * 1000)


def _series(first: date, last: date, *, rate: float = 0.0001, index=lambda d: 100.0, skip=(),
            settle=True) -> fb.Series:
    days, settlements = {}, []
    day = first
    while day <= last:
        if day not in skip:
            px = index(day)
            days[day] = fb.Day(index=px, premium=0.0, mark=px, mark_high=px)
        if settle:
            for hour in (0, 8, 16):
                settlements.append((_ms(day, hour), rate))
        day += timedelta(days=1)
    return fb.Series(days=days, settlements=settlements, first_archived=fb.ARCHIVE_FIRST_DAY, name="TESTUSDT")


def test_prev_month_wraps_the_year() -> None:
    assert v2.prev_month("2021-01") == "2020-12"
    assert v2.prev_month("2021-07") == "2021-06"
    assert v2.months_between(date(2020, 11, 5), date(2021, 2, 1)) == ["2020-11", "2020-12", "2021-01", "2021-02"]


def test_the_universe_ranks_last_months_volume_and_needs_last_months_spot_pair(monkeypatch) -> None:
    monkeypatch.setattr(v2, "TOP_N", 2)
    volumes = {"2021-02": {"AUSDT": 5.0, "BUSDT": 9.0, "1000CUSDT": 7.0, "DUSDT": 1.0}}
    spot_months = {"AUSDT": {"2021-02"}, "BUSDT": {"2021-01"}, "CUSDT": {"2021-02"}}
    members = v2.universe(volumes, spot_months, {"AUSDT", "BUSDT", "CUSDT"}, ["2021-03"])
    # top 2 by volume: BUSDT (no spot file in 2021-02) and 1000CUSDT (maps to CUSDT); AUSDT is third
    assert members == {"2021-03": ["1000CUSDT"]}


def test_a_member_every_month_is_the_v01_simulation_exactly() -> None:
    s = _series(D0 - timedelta(days=10), D0 + timedelta(days=80), index=lambda d: 100 + (d - D0).days * 0.3)
    end = D0 + timedelta(days=60)
    months = set(v2.months_between(D0, end + timedelta(days=40)))
    for strategy in fb.STRATEGIES:
        assert v2.simulate_member(strategy, s, months, set(s.days), D0, end).returns == \
            fb.simulate_symbol(strategy, s, D0, end).returns


def test_leaving_the_universe_closes_at_the_last_member_close_and_books_nothing_after() -> None:
    s = _series(D0 - timedelta(days=5), D0 + timedelta(days=70))
    leg = v2.simulate_member("B-ALL", s, {"2021-03"}, set(s.days), D0, D0 + timedelta(days=60))
    last = date(2021, 3, 31)
    assert max(leg.returns) == last
    assert leg.entries == 1 and leg.exits == 1
    assert leg.returns[last] == pytest.approx((3 * 0.0001 - fb._costs(1.0, 1.0)) / 2)


def test_a_perp_whose_bars_end_while_held_is_closed_at_its_last_close() -> None:
    last = D0 + timedelta(days=9)
    s = _series(D0 - timedelta(days=5), last)
    leg = v2.simulate_member("B-ALL", s, {"2021-03", "2021-04"}, set(s.days), D0, D0 + timedelta(days=40))
    assert max(leg.returns) == last and leg.exits == 1


def test_bars_that_outlive_trading_are_not_held_even_when_the_index_is_spliced() -> None:
    """v0.2.1: delisted LUNAUSDT kept a frozen mark and a spliced index after its last trade (2022-05-13)."""
    last_trade = D0 + timedelta(days=9)
    s = _series(D0 - timedelta(days=5), D0 + timedelta(days=40),
                index=lambda d: 100.0 if d <= D0 + timedelta(days=20) else 130_000.0)
    traded = {d for d in s.days if d <= last_trade}
    leg = v2.simulate_member("B-ALL", s, {"2021-03", "2021-04"}, traded, D0, D0 + timedelta(days=40))
    assert max(leg.returns) == last_trade
    assert leg.entries == 1 and leg.exits == 1 and leg.resets == 0
    assert leg.returns[last_trade] == pytest.approx((3 * 0.0001 - fb._costs(1.0, 1.0)) / 2)


def test_a_position_opens_only_at_a_close_the_perp_traded_on() -> None:
    s = _series(D0 - timedelta(days=5), D0 + timedelta(days=20))
    traded = {d for d in s.days if d >= D0}                         # first trade on D0: nothing to open at D0 - 1
    leg = v2.simulate_member("B-ALL", s, {"2021-03"}, traded, D0, D0 + timedelta(days=20))
    assert leg.returns[D0] == 0.0
    assert leg.entries == 1 and leg.returns[D0 + timedelta(days=1)] < 0      # opened at D0's close


def test_a_traded_day_without_a_derivative_bar_or_a_month_without_settlements_is_excluded() -> None:
    traded = {D0 + timedelta(days=i) for i in range(-1, 61)}
    gap = _series(D0 - timedelta(days=5), D0 + timedelta(days=70), skip={D0 + timedelta(days=40)})
    assert v2.missing_months(gap, traded, ["2021-03", "2021-04"]) == ["2021-04"]
    unsettled = _series(D0 - timedelta(days=5), D0 + timedelta(days=70), settle=False)
    assert v2.missing_months(unsettled, traded, ["2021-03"]) == ["2021-03"]


def test_days_the_perp_did_not_trade_are_not_exclusions() -> None:
    s = _series(D0 - timedelta(days=5), D0 + timedelta(days=9))      # delisted on day 10
    traded = {D0 + timedelta(days=i) for i in range(-5, 10)}
    assert v2.missing_months(s, traded, ["2021-03", "2021-04"]) == []


def _inputs(series: fb.Series, months: list[str], excluded: list[str] = ()) -> v2.Inputs:
    return v2.Inputs(series={"TESTUSDT": series}, members={m: ["TESTUSDT"] for m in months},
                     excluded={"TESTUSDT": list(excluded)} if excluded else {},
                     traded={"TESTUSDT": set(series.days)})


def test_too_many_excluded_member_months_block() -> None:
    s = _series(D0 - timedelta(days=5), D0 + timedelta(days=200))
    months = v2.months_between(D0, D0 + timedelta(days=150))
    with pytest.raises(v2.Blocked):
        v2.measure(_inputs(s, months, [months[0]]), start=D0, end=D0 + timedelta(days=150))


def test_a_steady_carry_clears_the_entry_floor_and_names_the_screen_window() -> None:
    first, last = D0 - timedelta(days=5), D0 + timedelta(days=200)
    days, settlements = {}, []
    day = first
    while day <= last:
        days[day] = fb.Day(index=100.0, premium=0.0, mark=100.0, mark_high=100.0)
        for hour in (0, 8, 16):
            settlements.append((_ms(day, hour), 0.0001 + 0.00002 * ((day - D0).days % 7)))
        day += timedelta(days=1)
    s = fb.Series(days=days, settlements=settlements, first_archived=fb.ARCHIVE_FIRST_DAY, name="TESTUSDT")
    months = v2.months_between(D0, D0 + timedelta(days=180))
    report = v2.measure(_inputs(s, months), start=D0, end=D0 + timedelta(days=180))
    assert report["strategies"]["B-ALL"]["entry"]["pass"] is True
    assert report["overall"] == "ENTRY_PASS"
    assert report["screen"]["window"] == ["2026-12-01", "2027-03-31"]


def test_the_spot_archive_months_are_read_from_file_names(tmp_path: Path) -> None:
    folder = tmp_path / "klines-1d" / "PEPEUSDT"
    folder.mkdir(parents=True)
    for name in ("PEPEUSDT-1d-2023-05.zip", "PEPEUSDT-1d-2023-06.zip", "PEPEUSDT-1d-2023-06.zip.CHECKSUM"):
        (folder / name).write_bytes(b"")
    assert v2.read_spot_months(tmp_path) == {"PEPEUSDT": {"2023-05", "2023-06"}}


def test_the_command_refuses_outside_the_sandbox(monkeypatch) -> None:
    monkeypatch.setattr(v2.xsb, "_in_sandbox", lambda: False)
    assert v2.main(["run"]) == v2.EXIT_USAGE
