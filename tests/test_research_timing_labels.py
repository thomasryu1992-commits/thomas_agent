from __future__ import annotations

import json
import random
from datetime import date, timedelta

import pytest

from scripts.research import basket_search as bs
from scripts.research import cross_section_basket as xsb
from scripts.research import signal_lab as lab
from scripts.research import strategy_ladder as sl
from scripts.research import timing_labels as tl
from scripts.research import trend_book_search as tbs


def _sundays(n, first=date(2020, 1, 5)):
    return [first + timedelta(days=7 * i) for i in range(n)]


def test_the_states_come_from_the_recorded_labels_with_the_named_state_cut():
    days = _sundays(3)
    labels = {days[0]: {"breadth": "thin", "dominance": "alt_led", "correlation": "tight", "heat": "cool"},
              days[1]: {"breadth": "broad", "dominance": "btc_led", "correlation": "loose", "heat": "hot"},
              days[2]: {}}
    s = tl.states(labels, days)
    assert [s["breadth"][d] for d in days] == [-1, 1, None]
    assert [s["dominance"][d] for d in days] == [1, -1, None]
    assert [s["correlation"][d] for d in days] == [-1, 1, None]
    assert [s["heat"][d] for d in days] == [1, -1, None]
    assert tl.ALPHA == pytest.approx(0.0125), "four registered: 0.05 / 4 whatever is left later"


def test_the_excess_is_twice_the_base_one_way_or_the_other():
    assert tl.excess_weights({"A": 0.25, "B": 0.75}, 1) == pytest.approx({"A": 0.5, "B": 1.5})
    assert tl.excess_weights({"A": 0.25, "B": 0.75}, -1) == pytest.approx({"A": -0.5, "B": -1.5})
    assert tl.excess_weights({"A": 1.0}, None) == {} and tl.excess_weights({}, 1) == {}


def test_the_null_is_the_records_spell_shuffle_and_leaves_unlabelled_weeks_in_place():
    days = _sundays(60)
    rng = random.Random(1)
    state, s = {}, 1
    for i, d in enumerate(days):
        if i < 10:
            state[d] = None
            continue
        if rng.random() < 0.2:
            s = -s
        state[d] = s
    one = tl.null_state(state, days, "breadth", 3)
    assert one == tl.null_state(state, days, "breadth", 3) and one != tl.null_state(state, days, "breadth", 4)
    assert all(one[d] is None for d in days[:10]) and sorted(v for v in one.values() if v) == sorted(v for v in state.values() if v)
    assert [one[d] for d in days] == lab.shuffled_spells([state[d] for d in days], f"{tl.SPEC}|breadth|null|3")


def test_the_plant_keeps_every_spell_and_sets_only_its_state():
    days = _sundays(80)
    rng = random.Random(2)
    state, s = {}, 1
    for i, d in enumerate(days):
        if i % 7 == 0:
            s = -s
        state[d] = None if i < 5 else s
    weeks = {d: rng.gauss(0, 0.05) for d in days}
    one = tl.planted_state(state, days, weeks, 0.10, 0)
    assert one == tl.planted_state(state, days, weeks, 0.10, 0)
    runs = tl._spells(state, days)
    for run in runs:
        assert len({one[d] for d in run}) == 1, "a spell gets one state"
    assert all(one[d] is None for d in days[:5])
    sure = tl.planted_state(state, days, weeks, 1.0, 0)
    means = [sum(weeks[d] for d in run) / len(run) for run in runs]
    good = [m for run, m in zip(runs, means) if sure[run[0]] == 1]
    cut = [m for run, m in zip(runs, means) if sure[run[0]] == -1]
    assert min(good) > max(cut), "at rho 1 the good spells are the ones with the better weeks"


def test_the_counts_read_no_return_and_stop_on_a_lopsided_year_or_thin_states(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("counts must not simulate a book")
    monkeypatch.setattr(xsb, "simulate", refuse)
    days = _sundays(104)
    shares = {d: {"A": 1.0} for d in days}
    even = {d: (1 if (i // 4) % 2 else -1) for i, d in enumerate(days)}
    lopsided = {d: (1 if i % 52 < 34 else -1) for i, d in enumerate(days)}
    out = tl.counts({a: even for a in tl.AXES[:2]} | {a: lopsided for a in tl.AXES[2:]}, shares, days)
    assert out["breadth"]["stop"]["stops"] is False and out["breadth"]["largest_year_drift"] < 0.25
    assert out["correlation"]["stop"]["stops"] is True, "34 of 52 weeks in one state: |mean s| = 0.31"
    assert out["breadth"]["states"]["good"]["spells"] == 13
    thin = {d: (1 if i < 100 else -1) for i, d in enumerate(days)}
    out = tl.counts({a: thin for a in tl.AXES}, shares, days)
    assert out["heat"]["stop"]["thin"] is True
    assert sum(y["flips"] for y in out["heat"]["years"].values()) == 1


def test_the_overlap_closes_a_mirror_and_reads_l28_only_where_its_scale_is_read(monkeypatch):
    days = _sundays(40)
    rng = random.Random(5)
    down = {d: rng.random() for d in days}
    monkeypatch.setattr(tbs, "shares", lambda m, mem, day, lb: ({"A": down[day], "B": 1 - down[day]},
                                                                {"A": -0.1, "B": 0.1}))
    mirror = {d: (1 if down[d] > 0.5 else -1) for d in days}       # cuts when few are down: the mirror of T01
    other = {d: rng.choice((1, -1)) for d in days}
    l28 = {d: {"k": 1.0 + rng.gauss(0, 0.2), "target": 0.01 if i >= 10 else None, "sigma": 0.01}
           for i, d in enumerate(days)}
    out = tl.overlap(xsb.Market(), {}, days, {"breadth": mirror, "dominance": other, "correlation": other,
                                              "heat": other}, l28)
    assert out["axes"]["breadth"]["against"]["T01"]["correlation"] < -0.5 and out["axes"]["breadth"]["passes"] is False
    assert out["axes"]["dominance"]["against"]["L28"]["weeks"] == 30
    assert out["axes"]["correlation"]["against"]["dominance"]["correlation"] == pytest.approx(1.0)
    assert out["axes"]["correlation"]["passes"] is False, "a later axis that repeats an earlier one closes"


def test_the_funding_axis_must_also_clear_without_funding():
    base = {"clears_entry": True, "later_years": {"passes": True}, "annual_sharpe": 1.4}
    assert tl.judge("heat", {**base, "annual_sharpe_without_funding": 0.8}, [0.0] * 600)["candidate"] is False
    assert tl.judge("heat", {**base, "annual_sharpe_without_funding": 1.1}, [0.0] * 600)["candidate"] is True
    assert tl.judge("breadth", {**base, "annual_sharpe_without_funding": 0.2}, [0.0] * 600)["candidate"] is True
    assert tl.judge("breadth", base, [1.5] * 7 + [0.0] * 593)["null"]["passes"] is False, "8/601 > 0.0125"


def test_the_funding_axis_reads_its_null_p_without_funding_and_the_plant_has_a_size_zero_control():
    base = {"clears_entry": True, "later_years": {"passes": True}, "annual_sharpe": 1.4,
            "annual_sharpe_without_funding": 1.05}
    nulls_unfunded = [1.1] * 20 + [0.0] * 580                     # the funded nulls would sit far lower
    out = tl.judge("heat", base, nulls_unfunded)
    assert out["p_reads"] == "annual_sharpe_without_funding" and out["null"]["p"] == pytest.approx(round(21 / 601, 4))
    assert out["candidate"] is False
    assert tl.judged_value("breadth", base) == 1.4 and tl.judged_value("heat", base) == 1.05
    rows = {}
    for rho in tl.PLANT_RHOS:
        for k in range(50):
            hit = (rho == 0.0 and k < 4) or (rho == 0.2 and k < 45)
            rows[f"{rho}|{k}"] = {"rho": rho, "annual_sharpe": 2.0 if hit else -1.0, "clears_entry": hit,
                                  "good_share": 0.5}
    reading = tl.plant_reading("breadth", rows, [0.0] * 600)
    assert reading["false_alarms"] == 4 and reading["void"] is True and reading["smallest_seen"] is None
    for k in range(4):
        rows[f"0.0|{k}"]["annual_sharpe"] = -1.0
    reading = tl.plant_reading("breadth", rows, [0.0] * 600)
    assert reading["void"] is False and reading["smallest_seen"] == 0.2
    assert tl.good_share({1: 1, 2: -1, 3: None, 4: 1}) == pytest.approx(0.6667)


def test_end_to_end_the_excess_earns_twice_s_of_the_base():
    import math
    rng, market = random.Random(9), xsb.Market()
    for i in range(6):
        p, bars = 100.0, {}
        for k in range(300):
            d = date(2019, 10, 1) + timedelta(days=k)
            o = p * math.exp(rng.gauss(0, 0.01))
            p = o * math.exp(rng.gauss(0, 0.03 * (1 + i / 3)))
            bars[d] = xsb.Bar(open=o, close=p, quote_volume=1e6)
        market.bars[f"C{i}"] = bars
    names = sorted(market.bars)
    members = {date(2019, m, 1): names for m in (10, 11, 12)} | {date(2020, m, 1): names for m in range(1, 8)}
    start, end = date(2020, 3, 1), date(2020, 6, 30)
    days = tl.vm.decisions(start, end)
    state = {d: (None if i < 2 else (1 if (i // 3) % 2 else -1)) for i, d in enumerate(days)}
    shares = tl.shares_by_day(market, members, days)
    base = xsb.simulate(market, bs.CADENCE, members, start=start, end=end, weigher=tbs.book(30, tbs.ALWAYS_LONG))
    excess = xsb.simulate(market, bs.CADENCE, members, start=start, end=end, weigher=tl.weigher(shares, state))
    for day, row in base["daily"].items():
        s = state[max(d for d in days if d < day)] or 0
        assert excess["daily"].get(day, {"gross": 0.0})["gross"] == pytest.approx(2 * s * row["gross"], abs=1e-12)


def test_the_book_is_the_base_books_and_the_unfunded_sharpe_drops_only_funding(monkeypatch):
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(400)]
    daily = {d: {"gross": 0.001, "cost": 0.0, "funding": 0.002 if d.day % 2 else -0.001,
                 "net": 0.001 + (0.002 if d.day % 2 else -0.001)} for d in days}
    monkeypatch.setattr(xsb, "simulate", lambda *a, **k: {"daily": daily})
    monkeypatch.setattr(tl, "SPAN", (date(2020, 1, 1), date(2021, 1, 31)))
    out = tl.run(xsb.Market(), {}, None)
    plain = {d: {**r, "net": r["gross"]} for d, r in daily.items()}
    assert out["annual_sharpe_without_funding"] == bs.annual_sharpe(plain, *tl.SPAN)[0]
    assert out["annual_sharpe"] == bs.annual_sharpe(daily, *tl.SPAN)[0]


def test_the_commands_refuse_outside_the_sandbox_and_out_of_order(monkeypatch, tmp_path):
    assert tl.main(["timing-overlap"]) == tl.EXIT_USAGE
    monkeypatch.setattr(tl.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setenv("RESEARCH_CACHE", str(tmp_path))

    def put(name, body):
        path = tmp_path / name
        path.write_text(json.dumps(body))
        return str(path)
    ok = {a: {"passes": True} for a in tl.AXES}
    over = put("o.json", {"overlap": {"axes": ok | {"breadth": {"passes": False}}}})
    stop = {a: {"stop": {"stops": False}} for a in tl.AXES}
    cnt = put("c.json", {"counts": stop | {"dominance": {"stop": {"stops": True}}}})
    door = put("d.json", {"door": {"passes": True}})
    assert tl.main(["timing-counts"]) == tl.EXIT_USAGE
    assert tl.main(["timing-door", "--overlap", over]) == tl.EXIT_USAGE, "needs the counts"
    gates = ["--overlap", over, "--counts", cnt, "--door", door]
    assert tl.main(["timing-null", "--axis", "breadth", "--seeds", "0:5", *gates]) == tl.EXIT_USAGE, "closed at overlap"
    assert tl.main(["timing-null", "--axis", "dominance", "--seeds", "0:5", *gates]) == tl.EXIT_USAGE, "stopped at counts"
    assert tl.main(["timing-null", "--axis", "heat", "--seeds", "0:601", *gates]) == tl.EXIT_USAGE
    assert tl.main(["timing-plant", "--axis", "heat", "--rho", "0.3", "--seeds", "0:5", *gates]) == tl.EXIT_USAGE
    full = put("n.json", {"books": {str(k): {"axis": "correlation", "annual_sharpe": 0.1} for k in range(600)}})
    assert tl.main(["timing-book", "--axis", "heat", *gates, "--null", full]) == tl.EXIT_USAGE, "another axis's null"
    assert tl.main(["timing-overlap"]) == tl.EXIT_BLOCKED, "no contract types in an empty cache"
    assert tl.left(json.loads((tmp_path / "o.json").read_text()), json.loads((tmp_path / "c.json").read_text())) == \
        ["correlation", "heat"]
    assert sl.later_years is tl.sl.later_years
