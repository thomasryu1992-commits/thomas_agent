from __future__ import annotations

from datetime import date, timedelta

import pytest

from scripts.research import flow_timing as ft


def _day(i: int) -> date:
    return ft.FIRST_DAY + timedelta(days=i)


def _flows(rows_by_day):
    return {d: rows for d, rows in rows_by_day.items()}


def test_the_family_and_the_lines_are_the_registered_ones():
    assert ft.WINDOWS == (1, 3, 7)
    assert ft.ALPHA == pytest.approx(0.05 / 3), "three registered: 0.05 / 3 whatever stops first"
    assert ft.SWITCH_COST == pytest.approx(0.0016) and ft.MAX_COST == 0.15
    assert ft.BASE == 90 and ft.NULLS == 600 and ft.PLANT_SEEDS == 50 and ft.MUST_SEE == 0.20
    assert ft.decisions(1)[0] == date(2022, 10, 26) and ft.decisions(7)[0] == date(2022, 11, 1)
    assert ft.decisions(3)[-1] == date(2024, 12, 31)


def test_a_key_is_known_from_its_first_day_until_its_drop_day_and_again_after_it_returns():
    keys = {"A": [(_day(10), None)], "B": [(_day(0), _day(5)), (_day(20), None)]}
    assert ft.known(keys, _day(9)) == frozenset()                # B dropped on day 5, A not yet known
    assert ft.known(keys, _day(10)) == frozenset({"A"})
    assert ft.known(keys, _day(4)) == frozenset({"B"}) and ft.known(keys, _day(5)) == frozenset()
    assert ft.known(keys, _day(25)) == frozenset({"A", "B"})


def test_a_transfer_counts_only_when_exactly_one_side_is_known():
    rows = [("", "A", 10.0), ("A", "", 3.0), ("A", "B", 100.0), ("", "", 50.0), ("B", "", 7.0)]
    assert ft.net(rows, frozenset({"A"})) == pytest.approx(10 - 3 - 100)      # A→B leaves A when B is unknown
    assert ft.net(rows, frozenset({"A", "B"})) == pytest.approx(10 - 3 - 7)   # A→B is between, left out


def test_the_label_reads_no_day_on_or_after_its_decision():
    keys = {"A": [(ft.FIRST_DAY, None)]}
    days = [ft.FIRST_DAY + timedelta(days=i) for i in range((ft.LAST_DECISION - ft.FIRST_DAY).days + 1)]
    flows = {d: [("", "A", float((i * 37) % 11))] for i, d in enumerate(days)}
    base = ft.states(flows, keys, 3)
    d = date(2023, 6, 1)
    moved = dict(flows)
    for later in days:
        if later >= d:
            moved[later] = [("", "A", 1e9)]
    assert ft.states(moved, keys, 3)[d] == base[d]


def test_an_address_joining_the_list_does_not_lift_the_label():
    """Flows flat on every key every day: the window equals its base, so no decision is above (all −1). With the
    moving set the record's file used, B joining would lift every window for 90 days."""
    days = [ft.FIRST_DAY + timedelta(days=i) for i in range((ft.LAST_DECISION - ft.FIRST_DAY).days + 1)]
    keys = {"A": [(ft.FIRST_DAY, None)], "B": [(date(2023, 11, 21), None)]}
    flows = {d: [("", "A", 5.0), ("", "B", 500.0)] for d in days}
    state = ft.states(flows, keys, 1)
    assert set(state.values()) == {-1}


def _alternating(spell: int, n: int, first: date = date(2022, 11, 1)) -> dict[date, int]:
    return {first + timedelta(days=i): (1 if (i // spell) % 2 == 0 else -1) for i in range(n)}


def test_the_counts_gate_stops_on_cost_and_passes_slow_balanced_states():
    fast = ft.counts(_alternating(1, 790))                                # switches every day
    assert fast["paper_cost_per_year"] > ft.MAX_COST and not fast["gates"]["cost"] and not fast["passes"]
    slow = ft.counts(_alternating(10, 790))
    assert slow["gates"] == {"cost": True, "year_drift": True, "minimum": True} and slow["passes"]
    assert slow["switches"] == 78


def test_the_counts_gate_stops_on_a_year_held_one_way_and_on_too_few_spells():
    first = date(2023, 1, 1)
    held = {first + timedelta(days=i): (1 if i < 300 else -1) for i in range(365)}
    held.update({date(2024, 1, 1) + timedelta(days=i): (1 if (i // 10) % 2 == 0 else -1) for i in range(366)})
    c = ft.counts(held)
    assert not c["gates"]["year_drift"] and c["largest_year_drift"] >= ft.MAX_YEAR_DRIFT
    few = ft.counts(_alternating(200, 790))                               # four spells, two a state
    assert not few["gates"]["minimum"]


def test_the_plant_reading_voids_on_false_alarms_and_needs_rho_020_seen():
    def books(rho, at):
        return [{"rho": rho, "seed": k, "p": (0.001 if k < at else 0.5), "good_share": 0.5} for k in range(50)]
    ok = ft.plant_reading(books(0.0, 2) + books(0.05, 0) + books(0.10, 41) + books(0.20, 45))
    assert ok["passes"] and ok["smallest_seen"] == 0.10 and not ok["void"]
    blind = ft.plant_reading(books(0.0, 0) + books(0.05, 0) + books(0.10, 10) + books(0.20, 39))
    assert not blind["passes"] and blind["smallest_seen"] is None
    void = ft.plant_reading(books(0.0, 4) + books(0.05, 50) + books(0.10, 50) + books(0.20, 50))
    assert void["void"] and not void["passes"]


def test_a_planted_book_is_judged_against_the_spell_shuffle_on_its_own_returns():
    state = _alternating(15, 360)
    nulls = ft.null_states(state, 7)[:60]
    strong = [ft.plant_book(state, nulls, 7, 0.20, s)["p"] for s in range(5)]
    none = ft.plant_book(state, nulls, 7, 0.0, 0)
    assert all(p is not None for p in strong) and none["p"] is not None
    again = ft.plant_book(state, nulls, 7, 0.20, 0)
    assert again["p"] == strong[0], "seeded: the same book twice"


def test_a_file_with_an_unreadable_quantity_is_refused(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("day,from_key,to_key,usdt,n,n_bad_qty\n2022-07-27,,k1,5.0,1,0\n2022-07-28,k1,,2.0,1,1\n")
    with pytest.raises(ValueError):
        ft.load_flows(p)
    p.write_text("day,from_key,to_key,usdt,n,n_bad_qty\n2022-07-27,,k1,5.0,1,0\n")
    assert ft.load_flows(p) == {date(2022, 7, 27): [("", "k1", 5.0)]}
