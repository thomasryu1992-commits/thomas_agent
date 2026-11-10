"""The premium convergence screen (PREMIUM_CONVERGENCE_V0.1 §5): coverage before any simulation, the verdict's
edges, a flat start, and the model left unchanged. Synthetic series only."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from scripts.research import funding_basis_screen as sc
from scripts.research import premium_convergence as pc
from scripts.research import premium_convergence_screen as ps

W0, W1 = pc.SCREEN_WINDOW
H = pc.HOUR_MS
HOURS = ((W1 - W0).days + 1) * 24                     # hour k counts from W0's 00:00 open


def _series(name: str = "BTCUSDT", *, premiums: dict[int, float | None] | None = None, drop: set[int] = frozenset(),
            last_hour: int = HOURS - 1, unsettled: set[date] = frozenset(), last_settlement_hour: int = 16,
            rate: float = 0.0001) -> pc.Series:
    hours = {}
    for k in range(-48, last_hour + 1):
        if k not in drop:
            hours[pc._ms(W0) + k * H] = pc.Hour(index=100.0, premium=(premiums or {}).get(k, 0.0), mark=100.0,
                                                 mark_high=100.0)
    settlements = {}
    day = W0 - timedelta(days=2)
    while day <= W1:
        for hh in (0, 8, 16):
            if day not in unsettled and not (day == W1 and hh > last_settlement_hour):
                settlements[pc._ms(day) + hh * H] = rate
        day += timedelta(days=1)
    return pc.Series(hours=hours, settlements=settlements, first_archived=date(2020, 1, 1), name=name)


def _two(**kw) -> dict[str, pc.Series]:
    return {"BTCUSDT": _series("BTCUSDT", **kw), "ETHUSDT": _series("ETHUSDT")}


def test_the_window_is_the_registered_one_and_cannot_be_moved():
    assert pc.SCREEN_WINDOW == (date(2027, 1, 1), date(2027, 4, 30))
    with pytest.raises(SystemExit):
        ps.main(["run", "--start", "2026-11-01"])


def test_a_window_without_a_trade_is_dropped():
    out = ps.screen(_two())
    assert out["status"] == ps.JUDGED
    row = out["strategies"]["PC"]
    assert row["days"] == HOURS // 24
    assert row["mean_bps_per_day"] == 0 and row["verdict"] == ps.DROP
    assert all(e["entries"] == 0 for e in row["events"].values())


def test_a_convergence_inside_the_window_is_kept():
    out = ps.screen(_two(premiums={10: 0.01}))         # open at hour 11, the premium is 0 again at its close
    row = out["strategies"]["PC"]
    assert row["events"]["BTCUSDT"]["entries"] == 1 and row["events"]["BTCUSDT"]["exits"] == 1
    assert row["mean_bps_per_day"] > 0 and row["verdict"] == ps.KEEP


def test_a_premium_that_widens_and_stays_is_dropped():
    widening = {10: 0.005, **{k: 0.03 for k in range(11, HOURS)}}
    out = ps.screen({"BTCUSDT": _series(premiums=widening, rate=0.0)})
    row = out["strategies"]["PC"]
    assert row["events"]["BTCUSDT"]["entries"] == 1 and row["events"]["BTCUSDT"]["exits"] == 0   # marked, not closed
    assert row["mean_bps_per_day"] < 0 and row["verdict"] == ps.DROP


def test_it_starts_flat_whatever_the_premium_was_before_the_window():
    before = {k: 0.02 for k in range(-40, -1)}         # above the threshold until two hours before the window
    out = ps.screen({"BTCUSDT": _series(premiums=before)})
    events = out["strategies"]["PC"]["events"]["BTCUSDT"]
    assert (events["entries"], events["exits"], events["held_hours"]) == (0, 0, 0)


def test_the_first_open_reads_the_bar_before_the_window():
    out = ps.screen({"BTCUSDT": _series(premiums={-1: 0.01})})
    assert out["strategies"]["PC"]["events"]["BTCUSDT"]["entries"] == 1


def _assert_not_yet_without_returns(out: dict) -> None:
    assert out["status"] == ps.NOT_YET
    assert set(out) == {"spec", "model", "window", "symbols", "strategies_screened", "status", "data_through", "short"}


def test_bars_short_of_the_last_hour_are_not_yet():
    out = ps.screen(_two(last_hour=HOURS - 2, premiums={10: 0.01}))
    _assert_not_yet_without_returns(out)
    assert out["short"] == ["BTCUSDT index/mark through 2027-04-30T22Z", "BTCUSDT premium through 2027-04-30T22Z"]


def test_a_premium_series_that_trails_the_index_is_not_yet():
    out = ps.screen(_two(premiums={k: None for k in range(HOURS - 5, HOURS)}))
    _assert_not_yet_without_returns(out)
    assert out["short"] == ["BTCUSDT premium through 2027-04-30T18Z"]


def test_a_funding_month_the_refresh_skipped_is_not_yet():
    february = {date(2027, 2, 1) + timedelta(days=i) for i in range(28)}
    out = ps.screen(_two(unsettled=february))
    _assert_not_yet_without_returns(out)
    assert out["short"] == ["BTCUSDT funding missing from 2027-02-01 (28 day(s))"]


def test_funding_that_stops_before_the_last_days_afternoon_is_not_yet():
    out = ps.screen(_two(last_settlement_hour=8))
    _assert_not_yet_without_returns(out)
    assert out["short"] == [f"BTCUSDT funding through 2027-04-30T08Z"]
    assert sc.LAST_SETTLEMENT_HOUR == 16


def test_a_hole_while_flat_is_skipped_and_counted():
    out = ps.screen(_two(drop={500}))
    assert out["status"] == ps.JUDGED
    assert out["strategies"]["PC"]["window_missing_hours"]["BTCUSDT"] == {"index_or_mark": 1, "premium": 0}


def test_a_hole_while_held_blocks():
    held = {k: 0.003 for k in range(11, 600)}           # between 0 and the threshold: kept open
    with pytest.raises(pc.DataGap):
        ps.screen(_two(premiums={10: 0.01, **held}, drop={500}))


def test_it_refuses_outside_the_sandbox(monkeypatch, capsys):
    monkeypatch.setattr(ps.xsb, "_in_sandbox", lambda: False)
    assert ps.main(["run"]) == ps.EXIT_USAGE
    assert "sandbox" in capsys.readouterr().err


def test_an_empty_cache_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(ps.xsb, "_in_sandbox", lambda: True)
    assert ps.main(["run", "--cache", str(tmp_path)]) == ps.EXIT_BLOCKED
