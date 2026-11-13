"""The strategy ladder (STRATEGY_DISCOVERY_LOOP_V0.1, Thomas 2026-11-13): L1's weights, L2's directions learned
without the year they trade, the placebo scenario, and the two rungs' arithmetic. Synthetic inputs only."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from scripts.research import cross_section_basket as xsb
from scripts.research import strategy_ladder as sl


def test_l1_holds_every_ranked_coin_by_its_distance_from_the_middle():
    values = {f"C{i:02d}": float(i) for i in range(1, 22)}          # 21 coins: ten a side and the middle one flat
    weights = sl.rank_weights(values, long_high=True)
    longs = {s: w for s, w in weights.items() if w > 0}
    shorts = {s: w for s, w in weights.items() if w < 0}
    assert len(longs) == len(shorts) == 10 and "C11" not in weights
    assert sum(longs.values()) == pytest.approx(1.0) and sum(shorts.values()) == pytest.approx(-1.0)
    assert max(longs.values()) <= sl.NAME_CAP + 1e-9 and min(shorts.values()) >= -sl.NAME_CAP - 1e-9
    ordered = [weights[f"C{i:02d}"] for i in range(12, 22)]
    assert ordered == sorted(ordered), "further from the middle is never less"
    flipped = sl.rank_weights(values, long_high=False)
    assert all(flipped[s] == pytest.approx(-w) for s, w in weights.items())


def test_the_cap_moves_weight_to_the_coins_under_it_and_a_thin_cross_section_is_not_ranked():
    values = {f"C{i:02d}": float(i) for i in range(1, 41)}          # twenty a side: the far end would be 0.095
    weights = sl.rank_weights(values, long_high=True)
    assert weights["C40"] < sl.NAME_CAP and sum(w for w in weights.values() if w > 0) == pytest.approx(1.0)
    steep = sl._capped({"A": 100.0, **{f"B{i}": 1.0 for i in range(12)}})
    assert steep["A"] == pytest.approx(sl.NAME_CAP) and sum(steep.values()) == pytest.approx(1.0)
    assert all(v == pytest.approx(0.9 / 12) for k, v in steep.items() if k != "A")
    assert sl._capped({"A": 3.0, "B": 1.0}) == {"A": 0.5, "B": 0.5}, "too few for the cap: equal shares"
    assert sl.rank_weights({f"C{i}": float(i) for i in range(sl.lab.MIN_NAMES - 1)}, long_high=True) == {}


def test_a_book_is_flat_when_the_day_has_no_direction():
    read = lambda market, symbol, day: float(symbol[1:])            # noqa: E731
    members = [f"C{i:02d}" for i in range(1, 13)]
    day = date(2021, 1, 3)
    assert sl.book(read, lambda _d: 0)(xsb.Market(), members, day, "x") == {}
    high = sl.book(read, lambda _d: 1)(xsb.Market(), members, day, "x")
    low = sl.book(read, lambda _d: -1)(xsb.Market(), members, day, "x")
    assert high["C12"] > 0 > high["C01"] and low["C12"] < 0 < low["C01"]


def _weekly(start: date, count: int) -> list[date]:
    return [start + timedelta(days=7 * k) for k in range(count)]


def test_a_years_direction_is_learned_from_the_other_years_only():
    """Hot weeks lose in 2021 and 2022 and win in 2023. Trading 2023 uses the first two years' sign; trading 2021
    still sees a majority of losing hot weeks, from 2022."""
    days = _weekly(date(2021, 1, 3), 156)
    labels = {d: "hot" for d in days}
    ics = {d: (0.2 if d.year == 2023 else -0.1) for d in days}
    assert sl.learned_signs(ics, labels, without_year=2023) == {"hot": -1}
    assert sl.learned_signs(ics, labels, without_year=None) == {"hot": -1}
    only_2023 = {d: v for d, v in ics.items() if d.year == 2023}
    assert sl.learned_signs(only_2023, labels, without_year=2023) == {}, "nothing to learn from but the year itself"
    assert sl.learned_signs(only_2023, labels, without_year=2022)["hot"] == 1
    direction, signs = sl.switching(ics, labels, [2021, 2022, 2023], held_out=True)
    assert signs["2023"] == {"hot": -1} and direction(date(2023, 6, 4)) == -1
    assert direction(date(2023, 6, 5)) == 0, "a day with no label is flat"


def test_a_decision_whose_week_runs_into_the_held_out_year_is_left_out_too():
    last_of_2021 = date(2021, 12, 26)                                # its return ends 2022-01-03
    assert (last_of_2021 + timedelta(days=8)).year == 2022
    days = _weekly(date(2021, 1, 3), 51) + [last_of_2021]
    ics = {d: -0.1 for d in days[:-1]} | {last_of_2021: 99.0}
    labels = {d: "hot" for d in days}
    assert sl.learned_signs(ics, labels, without_year=2022) == {"hot": -1}
    assert sl.learned_signs(ics, labels, without_year=None) == {"hot": 1}


def test_a_scenario_with_too_few_decisions_gets_no_direction():
    days = _weekly(date(2021, 1, 3), sl.lab.MIN_DECISIONS - 1)
    assert sl.learned_signs({d: 0.3 for d in days}, {d: "cool" for d in days}, without_year=None) == {"cool": 0}


def test_a_placebo_scenario_keeps_the_spells_and_changes_their_order():
    real = ["hot"] * 5 + ["cool"] * 3 + [None] * 2 + ["hot"] * 4 + ["cool"] * 7 + ["hot"] * 1 + ["cool"] * 6

    def runs(labels):
        out = []
        for label in labels:
            if out and out[-1][0] == label:
                out[-1][1] += 1
            else:
                out.append([label, 1])
        return sorted((str(a), b) for a, b in out)

    fake = sl.shuffled_spells(real, "seed-1")
    assert len(fake) == len(real) and sorted(map(str, fake)) == sorted(map(str, real))
    assert fake != real and sl.shuffled_spells(real, "seed-1") == fake != sl.shuffled_spells(real, "seed-2")
    merged = runs(fake)                       # neighbours of one label may join, so compare what cannot change
    assert sum(n for _, n in merged) == len(real) and {a for a, _ in merged} == {a for a, _ in runs(real)}


def test_stability_reads_the_years_against_half_the_entry_return():
    floor = sl.bs.ENTRY_RETURN_TOTAL_PCT * sl.STABILITY_SHARE
    assert sl.stability({"2020": 20.0, "2021": -3.0, "2022": 12.0, "2023": -1.0, "2024": 10.0}) == {
        "mean_year_total_position_pct": 7.6, "negative_years": 2, "floor_pct": floor, "passes": True}
    assert sl.stability({"2020": 60.0, "2021": -3.0, "2022": -2.0, "2023": -1.0, "2024": 10.0})["passes"] is False
    assert sl.stability({"2020": 5.0, "2021": 6.0, "2022": 7.0})["passes"] is False, "steady and too small"
    assert sl.stability({})["passes"] is False


def test_measure_reports_the_entry_rule_and_the_years_on_total_position(monkeypatch):
    span = (date(2020, 1, 1), date(2021, 12, 31))
    monkeypatch.setattr(sl, "monthly_members", lambda market, span=span: {})

    def simulate(market, cadence, monthly, *, start, end, weigher):
        daily = {date(y, m, 15): {"net": 0.04 if m % 2 else -0.01, "cost": 0.001, "funding": 0.0005}
                 for y in (2020, 2021) for m in range(1, 13)}
        return {"daily": daily}

    report = sl.measure(xsb.Market(), lambda *a, **k: {}, span, simulate=simulate)
    assert report["months"] == 24 and report["sum_pct"] == pytest.approx(36.0)
    assert report["total_position_annual_pct"] == pytest.approx(9.0) and report["clears_entry"] is False
    assert report["years_total_position_pct"] == {"2020": 9.0, "2021": 9.0}
    assert report["cost_pct"] == pytest.approx(2.4) and report["funding_pct"] == pytest.approx(1.2)
    assert report["stability"]["passes"] is True and report["annual_sharpe"] > 1


def test_the_rounds_candidates_name_registered_variables_and_it_runs_in_the_sandbox_only(monkeypatch, capsys):
    for name, candidate in sl.CANDIDATES.items():
        assert candidate["variable"] in sl.lab.variables() and candidate["logic"] in ("L1", "L2"), name
        assert candidate["tier"] == "top30"
    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: False)
    assert sl.main(["run"]) == sl.EXIT_USAGE
    capsys.readouterr()
    assert sl.main(["list"]) == sl.EXIT_OK
    assert set(json.loads(capsys.readouterr().out)["candidates"]) == set(sl.CANDIDATES)
