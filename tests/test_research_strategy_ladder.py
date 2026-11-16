"""The strategy ladder (STRATEGY_DISCOVERY_LOOP_V0.1, Thomas 2026-11-13): L1's weights, L2's directions learned
without the year they trade, the placebo scenario, and the two rungs' arithmetic. Synthetic inputs only."""

from __future__ import annotations

import json
import random as random_module
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
        assert candidate["variable"] in sl.lab.variables() and candidate["logic"] in sl.BOOKS, name
        assert candidate["accounting"] in sl.ACCOUNTING and candidate["round"] in ("1", "2", "3", "5"), name
        assert candidate["tier"] == "top30"
    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: False)
    assert sl.main(["run"]) == sl.EXIT_USAGE
    capsys.readouterr()
    assert sl.main(["list"]) == sl.EXIT_OK
    assert set(json.loads(capsys.readouterr().out)["candidates"]) == set(sl.CANDIDATES)


# --- rung 4: the confirm span on a budget ----------------------------------------------------------------------------

def test_a_later_confirm_read_is_held_to_a_stricter_bar():
    bars = [sl.confirm_bar(k) for k in range(1, sl.CONFIRM_BUDGET + 1)]
    assert bars[0] == sl.bs.ENTRY_SHARPE == 1.0, "the first read is the entry rule's own number"
    assert bars == sorted(bars) and bars[1] > 1.2 and bars[-1] > bars[1]
    assert sl.CONFIRM == (date(2025, 1, 1), date(2026, 9, 30)) and sl.CONFIRM[1] < sl.bs.RESERVED_FROM


def test_a_read_is_registered_before_it_is_spent_and_cannot_be_spent_twice(monkeypatch, capsys):
    monkeypatch.setattr(sl, "CONFIRM_DONE", {})
    first = sl.next_confirm_read()
    assert first is not None and first["candidate"] in sl.CANDIDATES and set(first["signs"]) == {"cool", "hot"}
    numbers = [r["read"] for r in sl.CONFIRM_READS]
    assert numbers == list(range(1, len(numbers) + 1)) and len(numbers) <= sl.CONFIRM_BUDGET
    monkeypatch.setattr(sl, "CONFIRM_DONE", {r["read"]: "a run" for r in sl.CONFIRM_READS})
    assert sl.next_confirm_read() is None
    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    assert sl.main(["confirm"]) == sl.EXIT_USAGE and "no registered confirm read is open" in capsys.readouterr().err
    monkeypatch.setattr(sl, "CONFIRM_DONE", {k: "a run" for k in range(1, sl.CONFIRM_BUDGET + 1)})
    monkeypatch.setattr(sl, "CONFIRM_READS", (*sl.CONFIRM_READS, {"read": 9, "candidate": "C002", "signs": {}}))
    assert sl.next_confirm_read() is None, "the budget is used even when another read is written down"


def test_the_confirm_read_uses_the_registered_directions_and_decides_by_the_bar(monkeypatch):
    days = sl.sundays((sl.SPAN[0], sl.CONFIRM[1]))
    hot_day, cool_day, blank_day = date(2025, 3, 2), date(2025, 3, 9), date(2025, 3, 16)
    labels = {d: {} for d in days} | {hot_day: {"heat": "hot"}, cool_day: {"heat": "cool"}}
    monkeypatch.setattr(sl.lab, "scenario_labels", lambda market, monthly, asked, span: {d: labels[d] for d in asked})
    monkeypatch.setattr(sl, "monthly_members", lambda market, span=None: {})
    entry = sl.CONFIRM_READS[0]
    _, direction, _ = sl._confirm_frame(xsb.Market(), entry)
    assert (direction(hot_day), direction(cool_day), direction(blank_day)) == (-1, 1, 0)

    def simulate(nets):
        def run(market, cadence, monthly, *, start, end, weigher):
            assert (start, end) == sl.CONFIRM
            return {"daily": {date(2025 + m // 12, m % 12 + 1, 15): {"net": nets[m % len(nets)]} for m in range(21)}}
        return run

    passed = sl.confirm(xsb.Market(), entry, simulate=simulate([0.05, 0.03, 0.04]))
    failed = sl.confirm(xsb.Market(), entry, simulate=simulate([0.05, -0.06, 0.01]))
    assert passed["verdict"] == "CONFIRM_PASS" and passed["annual_sharpe"] >= passed["bar_annual_sharpe"] == 1.0
    assert failed["verdict"] == "CONFIRM_FAIL" and failed["read"] == 1 and failed["of"] == sl.CONFIRM_BUDGET
    assert set(passed["never_switching_books"]) == {"always_long_high", "always_long_low"}


def test_the_confirm_read_loads_only_what_its_candidate_reads(monkeypatch, capsys):
    asked = {}

    def hold(cache, span, *, attach=True, flow=False):
        asked.update(span=span, attach=attach)
        return None

    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(sl.lab, "hold", hold)
    monkeypatch.setattr(sl, "CONFIRM_DONE", {})
    monkeypatch.setattr(sl, "CONFIRM_READS", sl.CONFIRM_READS[:1])
    assert sl.main(["confirm-counts"]) == sl.EXIT_BLOCKED
    capsys.readouterr()
    assert asked == {"span": (sl.SPAN[0], sl.CONFIRM[1]), "attach": False}, "funding needs no attached series"
    assert sl.lab.variables()["spot_share_30"][0] in sl.ATTACHED_GROUPS
    assert sl.lab.variables()["funding_30"][0] not in sl.ATTACHED_GROUPS


def test_the_first_confirm_read_is_spent_and_the_tool_refuses_it(monkeypatch, capsys):
    """Read 1 was spent on 2026-11-13 (C002, CONFIRM_FAIL). With that read alone on the list nothing is open."""
    assert 1 in sl.CONFIRM_DONE and "C002" in sl.CONFIRM_DONE[1]
    open_read = sl.next_confirm_read()
    assert open_read is None or open_read["read"] not in sl.CONFIRM_DONE
    monkeypatch.setattr(sl, "CONFIRM_READS", sl.CONFIRM_READS[:1])
    assert sl.next_confirm_read() is None
    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    for command in ("confirm", "confirm-counts"):
        assert sl.main([command]) == sl.EXIT_USAGE
    assert "no registered confirm read is open" in capsys.readouterr().err


# --- round 2: the ladder's own accounting and the risk-matched book --------------------------------------------------

def _two_coin_market(up_moves):
    """UP opens at 100 and moves by ``up_moves`` open to open; FLAT stays at 100. Decision on Sunday 2021-01-03."""
    start = date(2021, 1, 3)
    up, flat, price = {}, {}, 100.0
    for k in range(12):
        day = start + timedelta(days=k)
        nxt = price * (1 + (up_moves[k - 1] if 1 <= k <= len(up_moves) else 0.0))
        up[day] = xsb.Bar(open=price, close=nxt, quote_volume=1.0)
        flat[day] = xsb.Bar(open=100.0, close=100.0, quote_volume=1.0)
        price = nxt
    return xsb.Market(bars={"UPUSDT": up, "FLATUSDT": flat}), start


def test_a_held_short_loses_what_the_coin_did_and_a_daily_reset_short_loses_more():
    """Short a coin that doubles on Monday and halves back on Tuesday. Held for the week it is flat. Re-set to the
    same dollar weight every day (the basket engine's sum) it loses 100% and wins back 50%."""
    market, sunday = _two_coin_market([1.0, -0.5])
    weigher = lambda m, members, day, signal, seed=None: {"UPUSDT": -1.0, "FLATUSDT": 1.0} if day == sunday else {}  # noqa: E731
    monthly = {date(2021, 1, 1): ["UPUSDT", "FLATUSDT"]}
    span = dict(start=sunday + timedelta(days=1), end=sunday + timedelta(days=7))
    held = sl.simulate_held(market, sl.bs.CADENCE, monthly, weigher=weigher, **span)["daily"]
    reset = xsb.simulate(market, sl.bs.CADENCE, monthly, weigher=weigher, **span)["daily"]
    assert sum(r["gross"] for r in held.values()) == pytest.approx(0.0, abs=1e-12)
    assert sum(r["gross"] for r in reset.values()) == pytest.approx(-0.5)
    monday = sunday + timedelta(days=1)
    assert held[monday]["gross"] == pytest.approx(-1.0) and held[monday + timedelta(days=1)]["gross"] == pytest.approx(1.0)
    assert held[monday]["cost"] == pytest.approx(2 * (xsb.cost.DEFAULT_TAKER_FEE_BPS + xsb.cost.DEFAULT_SLIPPAGE_BPS) / 1e4)


def test_at_the_next_decision_the_trade_is_what_the_held_position_has_become():
    """The short has doubled in value by the next Sunday's entry; staying at the same target costs a trade of one."""
    market, sunday = _two_coin_market([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    weigher = lambda m, members, day, signal, seed=None: {"UPUSDT": -1.0, "FLATUSDT": 1.0}  # noqa: E731
    monthly = {date(2021, 1, 1): ["UPUSDT", "FLATUSDT"]}
    daily = sl.simulate_held(market, sl.bs.CADENCE, monthly, start=sunday + timedelta(days=1),
                             end=sunday + timedelta(days=9), weigher=weigher)["daily"]
    unit = (xsb.cost.DEFAULT_TAKER_FEE_BPS + xsb.cost.DEFAULT_SLIPPAGE_BPS) / 1e4
    second_entry = sunday + timedelta(days=8)
    assert daily[second_entry]["cost"] == pytest.approx(1.0 * unit), "from -2 back to -1; the flat leg does not trade"


def test_held_funding_is_paid_on_what_the_position_is_worth_that_day():
    market, sunday = _two_coin_market([1.0, 0.0])
    tuesday = sunday + timedelta(days=2)
    stamp = int((tuesday - date(1970, 1, 1)).total_seconds() * 1000) + 3600 * 1000
    market.funding = {"UPUSDT": [(stamp, 0.001)]}
    weigher = lambda m, members, day, signal, seed=None: {"UPUSDT": -1.0, "FLATUSDT": 1.0} if day == sunday else {}  # noqa: E731
    daily = sl.simulate_held(market, sl.bs.CADENCE, {date(2021, 1, 1): ["UPUSDT", "FLATUSDT"]},
                             start=sunday + timedelta(days=1), end=sunday + timedelta(days=4), weigher=weigher)["daily"]
    assert daily[tuesday]["funding"] == pytest.approx(2.0 * 0.001), "a short twice its size earns twice the rate"


def test_the_risk_matched_book_gives_a_wild_coin_less_weight_and_carries_no_market_exposure():
    values = {f"C{i:02d}": float(i) for i in range(1, 41)}          # twenty a side, so the cap leaves room to differ
    vols = {s: (0.20 if s == "C40" else 0.05) for s in values}
    betas = {s: 1.0 + i / 80 for i, s in enumerate(sorted(values))}
    weights = sl.risk_weights(values, vols, betas, long_high=True)
    plain = sl.rank_weights(values, long_high=True)
    assert 0 < weights["C40"] < plain["C40"], "four times the volatility, a smaller share of its side"
    assert weights["C40"] < weights["C39"]
    book_only = {s: w for s, w in weights.items() if s != sl.HEDGE}
    assert sum(w for w in book_only.values() if w > 0) == pytest.approx(1.0)
    assert sum(w * betas[s] for s, w in book_only.items()) + weights[sl.HEDGE] == pytest.approx(0.0, abs=1e-12)
    thin = {s: v for s, v in list(values.items())[:sl.lab.MIN_NAMES - 1]}
    assert sl.risk_weights(thin, vols, betas, long_high=True) == {}
    assert sl.risk_weights(values, {**vols, "C01": None}, betas, long_high=True).get("C01") is None


def test_a_round_is_measured_by_its_own_accounting_and_only_its_own_candidates(monkeypatch):
    assert sl.latest_round() == "5"
    assert {n for n, c in sl.CANDIDATES.items() if c["round"] == "2"} == {"C003", "C004"}
    assert all(c["accounting"] == "held" for c in sl.CANDIDATES.values() if c["round"] == "2")
    seen = []
    monkeypatch.setattr(sl, "run_l1", lambda market, candidate, span=None: seen.append(candidate["logic"]) or {})
    monkeypatch.setattr(sl, "run_l2", lambda market, candidate, span=None: seen.append("L2") or {})
    assert set(sl.run_round(xsb.Market(), which="2")) == {"C003", "C004"} and seen == ["L1", "L8"]
    assert set(sl.run_round(xsb.Market(), which="3")) == {"C005", "C006", "C007"}
    assert set(sl.run_round(xsb.Market())) == {"C008", "C009", "C010"}, "the latest round by default"
    assert set(sl.run_round(xsb.Market(), which="1")) == {"C001", "C002"}


# --- round 3: funding-aware weights and a four-week hold -------------------------------------------------------------

def test_a_position_that_would_pay_funding_away_is_not_held():
    values = {f"C{i:02d}": float(i) for i in range(1, 41)}
    vols = dict.fromkeys(values, 0.05)
    betas = dict.fromkeys(values, 1.0)
    week = sl.FUNDING_FLOOR
    funding = {**dict.fromkeys(values, 0.0), "C40": week * 2, "C39": week * 0.9, "C01": -week * 2, "C02": -week * 0.9,
               "C38": -week * 5, "C03": week * 5, "C37": None}
    plain = sl.risk_weights(values, vols, betas, long_high=True)
    aware = sl.risk_weights(values, vols, betas, long_high=True, funding=funding)
    assert "C40" in plain and "C40" not in aware, "a long that pays twice the floor"
    assert "C01" in plain and "C01" not in aware, "a short that pays twice the floor"
    assert {"C39", "C02", "C38", "C03", "C37"} <= set(aware), "under the floor, paid to hold, or unknown: kept"
    book_only = {k: v for k, v in aware.items() if k != sl.HEDGE}
    assert sum(v for v in book_only.values() if v > 0) == pytest.approx(1.0)
    assert sum(v for v in book_only.values() if v < 0) == pytest.approx(-1.0)
    assert aware["C21"] > 0 > aware["C20"], "ranks are taken before the filter: nobody crosses the middle"
    every_long_pays = {**dict.fromkeys(values, 0.0), **{f"C{i:02d}": week * 3 for i in range(24, 41)}}
    assert sl.risk_weights(values, vols, betas, long_high=True, funding=every_long_pays) == {}, "a side too thin: flat"


def test_a_four_week_hold_decides_on_every_fourth_sunday_from_a_fixed_anchor():
    sundays = [d for d in sl.sundays((date(2020, 1, 1), date(2020, 12, 31)))]
    picked = [d for d in sundays if sl.is_decision(sl.bs.CADENCE, d, 4)]
    assert sl.HOLD_ANCHOR in picked and all((b - a).days == 28 for a, b in zip(picked, picked[1:]))
    assert [d for d in sundays if sl.is_decision(sl.bs.CADENCE, d, 1)] == sundays
    assert not sl.is_decision(sl.bs.CADENCE, sl.HOLD_ANCHOR + timedelta(days=1), 1), "not a decision day at all"
    market, sunday = _two_coin_market([0.0] * 10)
    calls = []

    def weigher(m, members, day, signal, seed=None):
        calls.append(day)
        return {"UPUSDT": -1.0, "FLATUSDT": 1.0}

    monthly = {date(2021, 1, 1): ["UPUSDT", "FLATUSDT"]}
    span = dict(start=sunday + timedelta(days=1), end=sunday + timedelta(days=10))
    sl.simulate_held(market, sl.bs.CADENCE, monthly, weigher=weigher, every=1, **span)
    weekly_calls = list(calls)
    calls.clear()
    sl.ACCOUNTING["held4"](market, sl.bs.CADENCE, monthly, weigher=weigher, **span)
    assert len(weekly_calls) == 2 and len(calls) == sum(1 for d in weekly_calls if sl.is_decision(sl.bs.CADENCE, d, 4))


# --- confirm read 2: a one-direction candidate -----------------------------------------------------------------------

def test_the_second_read_is_c007_as_built_and_is_held_to_the_second_bar(monkeypatch):
    entry = next(r for r in sl.CONFIRM_READS if r["read"] == 2)
    candidate = sl.CANDIDATES[entry["candidate"]]
    assert entry["candidate"] == "C007" and (candidate["logic"], candidate["accounting"]) == ("L9", "held4")
    assert sl.confirm_bar(2) == 1.245 and sl.HOLD_WEEKS["held4"] == 4
    read, direction, labels = sl._confirm_frame(xsb.Market(), entry)
    assert direction(date(2025, 6, 1)) == -1 and labels == {}, "long the low end on every day, no scenario read"
    used = {}

    def simulate(nets):
        def run(market, cadence, monthly, *, start, end, weigher):
            used["span"] = (start, end)
            return {"daily": {date(2025 + m // 12, m % 12 + 1, 15): {"net": nets[m % len(nets)]} for m in range(21)}}
        return run

    monkeypatch.setattr(sl, "monthly_members", lambda market, span=None: {})
    strong = sl.confirm(xsb.Market(), entry, simulate=simulate([0.05, 0.03, 0.04]))
    between = sl.confirm(xsb.Market(), entry, simulate=simulate([0.07, -0.03, 0.0]))
    assert used["span"] == sl.CONFIRM and strong["verdict"] == "CONFIRM_PASS" and strong["bar_annual_sharpe"] == 1.245
    assert 1.0 <= between["annual_sharpe"] < 1.245 and between["verdict"] == "CONFIRM_FAIL", "over the first bar, under the second"
    assert set(strong["random_books_same_logic"]) == {"count", "mean", "max", "at_or_above_this_book"}
    assert "never_switching_books" not in strong and "signs" not in strong


def test_the_counts_before_a_read_say_how_many_coins_each_side_would_hold_and_carry_no_return(monkeypatch):
    entry = next(r for r in sl.CONFIRM_READS if r["read"] == 2)
    monkeypatch.setattr(sl, "CONFIRM_DONE", {1: "spent"})
    members = [f"C{i:02d}USDT" for i in range(1, 25)]
    bars = {s: {date(2024, 10, 1) + timedelta(days=k): xsb.Bar(open=100.0, close=100.0 * (1 + 0.001 * ((k * (i + 3)) % 7 - 3)), quote_volume=5.0)
                for k in range(760)} for i, s in enumerate(members + [sl.HEDGE])}
    market = xsb.Market(bars=bars)
    monkeypatch.setattr(sl, "monthly_members", lambda market, span=None: {date(y, m, 1): members for y in (2024, 2025, 2026) for m in range(1, 13)})
    out = sl.confirm_counts(market, entry)
    assert out["hold_weeks"] == 4 and 20 <= out["decisions"] <= 24, "every fourth Sunday over twenty-one months"
    assert out["coins_ranked"]["decisions_under_the_floor"] == 0 and out["coins_held"]["long_min"] >= sl.MIN_SIDE
    assert "by_label" not in out
    flat = json.dumps(out)
    assert all(word not in flat for word in ("sharpe", "net", "pct", "return")), "counts only"


def test_two_confirm_reads_are_spent_and_three_are_left(monkeypatch, capsys):
    """Reads 1 and 2 were spent on 2026-11-13 (C002, C007: both CONFIRM_FAIL). Nothing is open until a third is
    registered."""
    assert {1, 2} <= set(sl.CONFIRM_DONE) and "C007" in sl.CONFIRM_DONE[2] and "CONFIRM_FAIL" in sl.CONFIRM_DONE[2]
    assert sl.confirm_bar(3) == 1.388, "the bar the third read is held to"
    monkeypatch.setattr(sl, "CONFIRM_READS", sl.CONFIRM_READS[:2])
    monkeypatch.setattr(sl, "CONFIRM_DONE", {k: v for k, v in sl.CONFIRM_DONE.items() if k <= 2})
    assert sl.CONFIRM_BUDGET - len(sl.CONFIRM_DONE) == 3 and sl.next_confirm_read() is None
    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    assert sl.main(["confirm"]) == sl.EXIT_USAGE and "no registered confirm read is open" in capsys.readouterr().err


# --- round 5: the universe without its tail ---------------------------------------------------------------------------

def _tail_market():
    """Twenty quiet coins; WILDUSDT doubles once inside the month; three coins carry large funding."""
    day = date(2021, 6, 6)
    members = [f"Q{i:02d}USDT" for i in range(20)] + ["WILDUSDT"]
    bars, funding = {}, {}
    for i, s in enumerate(members):
        price, out = 100.0, {}
        for k in range(45, -2, -1):
            d = day - timedelta(days=k)
            move = 1.0 if (s == "WILDUSDT" and k == 10) else 0.002 * ((i + k) % 3 - 1)
            opened, price = price, price * (1 + move)
            out[d] = xsb.Bar(open=opened, close=price, quote_volume=10.0)
        bars[s] = out
        rate = {0: 0.004, 1: -0.004, 2: 0.003}.get(i, 0.0001) if s != "WILDUSDT" else 0.0001
        funding[s] = [(int((day - timedelta(days=k) - date(1970, 1, 1)).total_seconds() * 1000) + 3600000, rate / 7) for k in range(7)]
    return xsb.Market(bars=bars, funding=funding), members, day


def test_the_tail_is_the_wild_coin_and_the_coins_with_the_largest_funding_whatever_its_sign():
    market, members, day = _tail_market()
    kept = sl.without_tail(market, members, day)
    assert "WILDUSDT" not in kept, "doubled in a day inside the month"
    assert "Q00USDT" not in kept and "Q01USDT" not in kept, "the largest funding, positive and negative alike"
    assert set(members) - set(kept) <= {"WILDUSDT", "Q00USDT", "Q01USDT", "Q02USDT"} and len(kept) >= 17
    assert "Q10USDT" in kept and sl.without_tail(market, [], day) == []
    few = members[:sl.lab.MIN_NAMES - 2]
    assert set(sl.without_tail(market, few + ["WILDUSDT"], day)) == set(few), "too few to rank funding: only the wild rule"


def test_a_tail_out_book_never_holds_a_tail_coin(monkeypatch):
    market, members, day = _tail_market()
    read = lambda m, s, d: float(sum(map(ord, s)) % 17)             # noqa: E731
    weights = sl.BOOKS["L9T"](read, lambda _d: 1)(market, members, day, "x")
    plain = sl.BOOKS["L9"](read, lambda _d: 1)(market, members, day, "x")
    tail = set(members) - set(sl.without_tail(market, members, day))
    assert tail and not (tail & set(weights)), "none of the tail is held"
    assert set(weights) - {sl.HEDGE} <= set(plain) | set(members), "built from the same members, minus the tail"
    assert {c["logic"] for n, c in sl.CANDIDATES.items() if n in ("C009", "C010")} == {"L9T"}
    assert sl.CANDIDATES["C008"]["logic"] == "L9" and sl.lab.variables()["taker_buy_share_30"][0] == "trade_flow"


# --- round 6: the book sweep ------------------------------------------------------------------------------------------

def test_the_sweeps_null_variables_are_their_own_and_enough_for_the_correction():
    nulls = sl.null_variables()
    assert len(nulls) == sl.SWEEP_NULLS == 600 and not set(nulls) & set(sl.lab.controls())
    earlier = sl.null_variables(sl.SWEEP_NULLS_ROUND_6)
    market, day = xsb.Market(), date(2021, 1, 3)
    assert len(earlier) == 300 and set(earlier) <= set(nulls), "round 6's null books are among the 600, by name"
    assert all(earlier[name][1](market, "AUSDT", day) == nulls[name][1](market, "AUSDT", day) for name in earlier), \
        "and each is the same random variable"
    market, day = xsb.Market(), date(2021, 1, 3)
    assert nulls["null_week_000"][1](market, "AUSDT", day) != sl.lab.controls()["control_week_00"][1](market, "AUSDT", day)
    assert nulls["null_fixed_000"][1](market, "AUSDT", day) == nulls["null_fixed_000"][1](market, "AUSDT", day + timedelta(days=7))
    assert 1 / (sl.SWEEP_NULLS + 1) < 0.10 / len(sl.lab.variables()), "a real effect can pass Benjamini–Hochberg over every variable"


def test_a_swept_book_is_long_the_side_its_own_rank_correlation_points_to(monkeypatch):
    asked = {}
    monkeypatch.setattr(sl, "monthly_members", lambda market, span=None: {})
    monkeypatch.setattr(sl, "decision_ics", lambda market, read, monthly, span: read(None, None, None))

    def measure(market, weigher, span, *, simulate):
        return {"annual_sharpe": 0.5, "total_position_annual_pct": 5.0, "clears_entry": False, "funding_pct": 0.0,
                "stability": {"negative_years": 1}}

    monkeypatch.setattr(sl, "measure", measure)
    seen = []
    monkeypatch.setitem(sl.BOOKS, sl.SWEEP_LOGIC, lambda read, direction: seen.append(direction(date(2021, 1, 3))) or (lambda *a, **k: {}))
    days = [date(2020, 1, 5) + timedelta(days=7 * k) for k in range(40)]
    names = {"up": ("g", lambda m, s, d: {x: 0.1 for x in days}), "down": ("g", lambda m, s, d: {x: -0.1 for x in days}),
             "thin": ("g", lambda m, s, d: {x: 0.1 for x in days[:5]})}
    out = sl.sweep(xsb.Market(), names)
    assert seen == [1, -1] and out["up"]["long"] == "high" and out["down"]["long"] == "low"
    assert out["thin"] == {"group": "g", "skipped": "ranked at too few decisions"}


def test_the_sweep_reads_each_book_beside_the_null_books_and_corrects_for_how_many_there_are():
    null = {f"n{i}": {"annual_sharpe": -1.0 + 2.0 * i / 299} for i in range(300)}          # -1 .. +1, evenly
    real = {name: {"group": "g", "long": "low", "annual_sharpe": sharpe, "total_position_annual_pct": 10.0,
                   "clears_entry": bool(sharpe and sharpe >= 1.0), "negative_years": 0}
            for name, sharpe in (("strong", 1.5), ("edge", 0.95), ("plain", 0.0), ("skipped", None))}
    out = sl.sweep_reading(real, null)
    assert out["books"] == 3 and out["null_books"] == 300 and out["expected_under_0.05_by_chance"] == 0.15
    by = {r["variable"]: r for r in out["all"]}
    assert by["strong"]["p"] == pytest.approx(1 / 301, abs=1e-4) and 0.02 < by["edge"]["p"] < 0.04 and by["plain"]["p"] > 0.4
    assert [r["variable"] for r in out["survive_benjamini_hochberg"]] == ["strong", "edge"]
    assert out["p_under_0.05"] == 2 and out["null"]["max"] == 1.0 and out["null"]["share_clearing_sharpe_1"] == pytest.approx(1 / 300, abs=1e-4)


def test_the_sweep_command_takes_groups_or_the_null_and_loads_what_they_read(monkeypatch, capsys):
    asked = []

    def hold(cache, span=None, *, attach=True, flow=False):
        asked.append((attach, flow))
        return None

    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(sl.lab, "hold", hold)
    assert sl.main(["sweep"]) == sl.EXIT_USAGE and sl.main(["sweep", "--only", "nothing"]) == sl.EXIT_USAGE
    capsys.readouterr()
    for only in ("null", "price_volume,funding,coin_character", "trade_flow"):
        assert sl.main(["sweep", "--only", only]) == sl.EXIT_BLOCKED
    assert asked == [(False, False), (False, False), (False, True)]
    # A group whose series are attached is swept alone, through its own door (section 44): round 6's combined load
    # no longer fits, and a premium sweep through the plain door would rank nothing and still exit 0.
    doors = []
    for name in ("hold_positioning", "hold_spot", "hold_premium"):
        monkeypatch.setattr(sl.lab, name, lambda cache, name=name: doors.append(name))
    for only in ("positioning", "spot", "premium"):
        assert sl.main(["sweep", "--only", only]) == sl.EXIT_BLOCKED
    assert doors == ["hold_positioning", "hold_spot", "hold_premium"] and len(asked) == 3, "the plain door was not used"
    capsys.readouterr()
    for only in ("positioning,depth,spot", "depth", "premium,funding", "positioning,spot"):
        assert sl.main(["sweep", "--only", only]) == sl.EXIT_USAGE, only
        assert "no loader for" in capsys.readouterr().err
    assert len(asked) == 3 and len(doors) == 3, "a refused set loads nothing"


def test_the_family_reading_puts_every_book_beside_one_null_and_refuses_a_rerun_that_moved():
    def book(sharpe, *, ranked=200, long="low"):
        return {"group": "g", "long": long, "annual_sharpe": sharpe, "total_position_annual_pct": 10.0,
                "clears_entry": sharpe >= 1.0, "negative_years": 0, "decisions_ranked": ranked, "funding_pct": 1.0}

    null = {f"n{i}": {"annual_sharpe": -1.0 + 2.0 * i / 599} for i in range(600)}          # -1 .. +1, evenly
    earlier = {"old_strong": book(1.5), "old_plain": book(0.0), "old_only": book(0.2)}
    later = {"old_strong": book(1.5), "old_plain": book(0.0), "new_edge": book(0.99, ranked=90), "new_plain": book(0.1),
             "new_thin": {"group": "g", "skipped": "ranked at too few decisions"}}
    out = sl.sweep_family(earlier, later, null)
    assert out["books"] == 5 and out["null_books"] == 600 and out["new_books"] == 2 and out["skipped"] == ["new_thin"]
    by = {r["variable"]: r for r in out["all"]}
    assert by["old_strong"]["p"] == pytest.approx(1 / 601, abs=1e-4), "an old book is read beside the same 600 as a new one"
    assert by["old_strong"]["p"] <= 0.10 / 55, "and one in 601 is small enough to pass the correction over 55 books"
    assert by["new_edge"]["new"] is True and by["old_only"]["new"] is False and by["new_edge"]["decisions_ranked"] == 90
    assert [r["variable"] for r in out["survive_benjamini_hochberg"]] == ["old_strong", "new_edge"]
    assert out["new_survivors"] == ["new_edge"] and out["new_p_under_0.05"] == 1
    moved = sl.sweep_family(earlier, {**later, "old_plain": book(0.01)}, null)
    assert moved == {"refused": "a re-run book differs from its record", "differ": ["old_plain"]}, "nothing is read"
    assert sl.sweep_matches(earlier, {"old_strong": book(1.5, long="high")}) == ["old_strong"]
    assert sl.sweep_matches(earlier, {"old_strong": book(1.5, ranked=199)}) == ["old_strong"]
    assert sl.sweep_matches(earlier, later) == [] and sl.sweep_matches({}, later) == []


# --- round 31: what a lead's book holds, with no return read ------------------------------------------------------------

def test_the_lead_check_counts_ties_and_lays_the_positions_beside_other_books_without_a_return(monkeypatch, capsys):
    members = [f"C{i:02d}" for i in range(1, 42)]
    lead = {**{f"C{i:02d}": float(i - 16) for i in range(1, 16)}, **{f"C{i:02d}": 0.0 for i in range(16, 27)},
            **{f"C{i:02d}": float(i - 26) for i in range(27, 42)}}          # fifteen falls, eleven exact zeros, fifteen rises
    shuffled = {s: float((17 * int(s[1:])) % 41) for s in members}          # a fixed order unrelated to the lead's
    registry = {"lead": ("g", lambda m, s, d: lead.get(s)), "same": ("g", lambda m, s, d: lead.get(s) + 0.01 * int(s[1:])),
                "mirror": ("g", lambda m, s, d: -(lead.get(s) + 0.01 * int(s[1:]))), "other": ("g", lambda m, s, d: shuffled[s])}
    monkeypatch.setattr(sl.lab, "variables", lambda: registry)
    monkeypatch.setattr(sl, "LEADS", {"lead": {"long": "low", "group": "price_volume"}})
    monkeypatch.setattr(sl, "LEAD_KIN", ("same", "mirror", "other"))
    monkeypatch.setattr(sl, "monthly_members", lambda market, span=None: {date(2021, m, 1): members for m in range(1, 7)})
    monkeypatch.setattr(sl, "without_tail", lambda market, members, day: list(members))
    monkeypatch.setitem(sl.BOOKS, sl.SWEEP_LOGIC, sl.book)

    def never(*args, **kwargs):
        raise AssertionError("the lead check reads no return")
    for owner, name in ((sl, "measure"), (sl, "decision_ics"), (sl.lab, "forward_return"), (sl, "simulate_held")):
        monkeypatch.setattr(owner, name, never)
    span = (date(2021, 1, 1), date(2021, 6, 30))
    out = sl.lead_check(xsb.Market(), "lead", span)
    held = [d - timedelta(days=1) for d in xsb.held_days(*span) if sl.is_decision(sl.bs.CADENCE, d - timedelta(days=1), 4)]
    assert out["decisions_with_a_book"] == len(held) >= 5 and out["reads_returns"] is False and out["long"] == "low"
    assert out["medians"]["zero_share"] == out["medians"]["tied_share"] == pytest.approx(11 / 41, abs=1e-4)
    assert out["medians"]["tied_weight_share"] == pytest.approx(30 / 420, abs=1e-3), \
        "the tied coins sit in the middle, eleven of 41 coins and 7% of the weight (distances 5..0..5 of 420)"
    assert out["medians"]["coins_held"] == 40, "the middle coin of 41 has no weight"
    kin = out["position_overlap"]
    assert kin["same"]["median"] > 0.95 and kin["mirror"]["median"] < -0.95 and abs(kin["other"]["median"]) < 0.5
    assert kin["same"]["decisions"] == len(held) and kin["mirror"]["share_positive"] == 0.0
    assert out["kin_rule"] == {"beside": "same", "threshold": 0.5, "kin": True}
    monkeypatch.setattr(sl, "LEAD_KIN", ("mirror", "other"))
    assert sl.lead_check(xsb.Market(), "lead", span)["kin_rule"]["kin"] is True, "the same book upside down is the same book"
    monkeypatch.setattr(sl, "LEAD_KIN", ("other", "same"))
    assert sl.lead_check(xsb.Market(), "lead", span)["kin_rule"]["kin"] is False, "the rule reads the first book only"
    # A tie is broken by the coin's name: with every value equal the book is the alphabet, and the check says so.
    registry["lead"] = ("g", lambda m, s, d: 0.0)
    flat = sl.lead_check(xsb.Market(), "lead", span)
    assert flat["medians"]["tied_share"] == 1.0 and flat["medians"]["tied_weight_share"] == pytest.approx(1.0)
    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    assert sl.main(["lead-check", "--only", "nothing"]) == sl.EXIT_USAGE and "names a lead" in capsys.readouterr().err
    monkeypatch.setattr(sl, "sweep_market", lambda cache, wanted: None)
    assert sl.main(["lead-check", "--only", "lead"]) == sl.EXIT_BLOCKED


def test_the_held_accounting_splits_the_same_net_by_leg_and_changes_nothing_without_it():
    """UP is in leg "a" at the first decision and "b" at the second; FLAT is held once and then dropped."""
    market, sunday = _two_coin_market([0.10, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -0.05, 0.0])
    second = sunday + timedelta(days=7)
    wednesday = sunday + timedelta(days=3)                       # UP's longs pay funding once, midweek
    market.funding = {"UPUSDT": [(int((wednesday - date(1970, 1, 1)).total_seconds() * 1000) + 3600 * 1000, 0.002)]}
    target = {sunday: {"UPUSDT": 1.0, "FLATUSDT": -1.0}, second: {"UPUSDT": 0.5}}
    weigher = lambda m, members, day, signal, seed=None: dict(target.get(day, {}))      # noqa: E731
    monthly = {date(2021, 1, 1): ["UPUSDT", "FLATUSDT"]}
    span = dict(start=sunday + timedelta(days=1), end=sunday + timedelta(days=10))
    leg_of = lambda symbol, decision: "flat" if symbol == "FLATUSDT" else ("a" if decision == sunday else "b")  # noqa: E731
    plain = sl.simulate_held(market, sl.bs.CADENCE, monthly, weigher=weigher, **span)["daily"]
    split = sl.simulate_held(market, sl.bs.CADENCE, monthly, weigher=weigher, leg_of=leg_of, **span)["daily"]
    assert all("legs" not in row for row in plain.values()), "nothing is added unless a leg is asked for"
    assert {d: {k: v for k, v in row.items() if k != "legs"} for d, row in split.items()} == plain
    assert all(sum(row["legs"].values()) == pytest.approx(row["net"], abs=1e-12) for row in split.values())
    unit = (xsb.cost.DEFAULT_TAKER_FEE_BPS + xsb.cost.DEFAULT_SLIPPAGE_BPS) / 1e4
    first, later = sunday + timedelta(days=1), second + timedelta(days=1)
    assert split[first]["legs"] == pytest.approx({"a": 0.10 - unit, "flat": -unit}), "entry costs and Monday's move"
    # At the second decision UP is worth 1.1 and the target is 0.5: that trade is leg b's, the coin's new leg.
    # FLAT is no longer held: closing it is charged to the leg it leaves.
    assert split[later]["legs"]["b"] == pytest.approx(-0.6 * unit + 0.5 * -0.05, abs=1e-9)
    assert split[later]["legs"]["flat"] == pytest.approx(-unit) and "a" not in split[later]["legs"]
    assert plain[wednesday]["funding"] == pytest.approx(-1.1 * 0.002), "a long worth 1.1 pays the rate"
    assert split[wednesday]["legs"] == pytest.approx({"a": -1.1 * 0.002, "flat": 0.0}), "and the payment is its leg's"


def test_the_leads_return_is_split_between_tied_coins_untied_coins_and_the_hedge(monkeypatch):
    members = [f"C{i:02d}" for i in range(1, 42)]
    lead = {**{f"C{i:02d}": float(i - 16) for i in range(1, 16)}, **{f"C{i:02d}": 0.0 for i in range(16, 27)},
            **{f"C{i:02d}": float(i - 26) for i in range(27, 42)}}          # eleven coins tied at zero
    start = date(2020, 12, 1)
    bars = {}
    for i, s in enumerate(members + [sl.HEDGE]):
        price, out = 100.0, {}
        for k in range(240):
            day = start + timedelta(days=k)
            # The hedge coin climbs; the coins with a low value climb, those with a high value sink, the tied ones wobble.
            drift = 0.004 if s == sl.HEDGE else (0.002 if lead.get(s, 0) < 0 else -0.002 if lead.get(s, 0) > 0 else 0.001 * (i % 3 - 1))
            opened, price = price, price * (1 + drift)
            out[day] = xsb.Bar(open=opened, close=price, quote_volume=1.0)
        bars[s] = out
    market = xsb.Market(bars=bars)
    monkeypatch.setattr(sl.lab, "variables", lambda: {"lead": ("g", lambda m, s, d: lead.get(s))})
    monkeypatch.setattr(sl, "LEADS", {"lead": {"long": "low", "group": "price_volume"}})
    monkeypatch.setattr(sl, "monthly_members", lambda market, span=None: {date(2021, m, 1): members for m in range(1, 7)})
    monkeypatch.setattr(sl, "without_tail", lambda market, members, day: list(members))

    def hedged(read, direction):
        inner = sl.book(read, direction)
        return lambda market, members, day, signal, seed=None: {**inner(market, members, day, signal), sl.HEDGE: -0.3}
    monkeypatch.setitem(sl.BOOKS, sl.SWEEP_LOGIC, hedged)
    span = (date(2021, 1, 1), date(2021, 6, 30))
    out = sl.lead_attribution(market, "lead", span)
    assert out["largest_gap"] < 1e-9 and set(out["legs"]) == set(sl.LEAD_LEGS) and out["decisions_named"] >= 5
    parts = sum(out["legs"][leg]["total_pct"] for leg in sl.LEAD_LEGS)
    assert parts == pytest.approx(out["book_total_pct"], abs=0.02), "the three legs are the book"
    assert out["legs"]["untied"]["total_pct"] > 0, "the low side was bought and it climbed; the high side was sold and it sank"
    assert out["legs"]["hedge"]["total_pct"] < 0, "short a hedge coin that rose"
    assert abs(out["legs"]["tied"]["total_pct"]) < out["legs"]["untied"]["total_pct"]
    whole = sl.measure(market, hedged(lambda m, s, d: lead.get(s), lambda _d: -1), span, simulate=sl.ACCOUNTING[sl.SWEEP_ACCOUNTING])
    assert out["annual_sharpe"] == whole["annual_sharpe"] and out["total_position_annual_pct"] == whole["total_position_annual_pct"], \
        "the split book is the measured book"
    share = out["legs"]["untied"]["total_pct"] / out["book_total_pct"]
    assert out["rule"] == {"untied_share_of_net": pytest.approx(share, abs=1e-3), "threshold": 0.5,
                           "about_the_variable": share >= 0.5}
    assert sl.LEAD_UNTIED_SHARE == 0.5
    # A split that does not add up is refused, never read.
    def broken(market, signal, monthly, **kwargs):
        sim = sl.simulate_held(market, signal, monthly, every=4, **kwargs)
        for row in sim["daily"].values():
            row["legs"]["untied"] = row["legs"].get("untied", 0.0) + 0.01
        return sim
    monkeypatch.setitem(sl.ACCOUNTING, sl.SWEEP_ACCOUNTING, broken)
    refused = sl.lead_attribution(market, "lead", span)
    assert set(refused) == {"refused", "largest_gap"} and refused["largest_gap"] == pytest.approx(0.01)


def test_the_one_lead_on_record_is_the_swept_book_and_nothing_else():
    assert sl.LEADS == {"spread_change_30": {"long": "low", "group": "price_volume"}}
    assert sl.LEAD_KIN[0] == "idio_vol_30" and sl.LEAD_KIN_MAX == 0.5, "the rule reads the closed cluster's book"
    assert set(sl.LEAD_KIN) | set(sl.LEADS) <= set(sl.lab.variables())
    assert {sl.lab.variables()[k][0] for k in sl.LEAD_KIN} == {"price_volume"}, "all read from bars: one load"


# --- confirm read 3: the tail-out book ---------------------------------------------------------------------------------

def test_the_third_read_is_c010_which_differs_from_the_second_reads_book_only_by_the_tail():
    third = next(r for r in sl.CONFIRM_READS if r["read"] == 3)
    second = next(r for r in sl.CONFIRM_READS if r["read"] == 2)
    a, b = sl.CANDIDATES[third["candidate"]], sl.CANDIDATES[second["candidate"]]
    assert third["candidate"] == "C010" and (a["logic"], b["logic"]) == ("L9T", "L9")
    assert {k: a[k] for k in ("variable", "long", "tier", "accounting")} == {k: b[k] for k in ("variable", "long", "tier", "accounting")}
    assert sl.BOOKS["L9T"] is not sl.BOOKS["L9"] and sl.confirm_bar(3) == 1.388
    read, direction, labels = sl._confirm_frame(xsb.Market(), third)
    assert direction(date(2026, 6, 7)) == -1 and labels == {}


def test_three_confirm_reads_are_spent_and_two_are_left(monkeypatch, capsys):
    """Read 3 was spent on 2026-11-13 (C010: CONFIRM_FAIL, 2026 still negative). Nothing is open."""
    assert set(sl.CONFIRM_DONE) == {1, 2, 3} and "C010" in sl.CONFIRM_DONE[3] and "CONFIRM_FAIL" in sl.CONFIRM_DONE[3]
    assert sl.CONFIRM_BUDGET - len(sl.CONFIRM_DONE) == 2 and sl.next_confirm_read() is None
    assert sl.confirm_bar(len(sl.CONFIRM_DONE) + 1) == 1.483, "the bar the next read is held to"
    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    assert sl.main(["confirm"]) == sl.EXIT_USAGE and "no registered confirm read is open" in capsys.readouterr().err


# --- round 7: the transfer check inside the development years ----------------------------------------------------------

def test_the_transfer_splits_stay_inside_development_and_the_read_year_follows_the_years_before_it():
    assert len(sl.TRANSFER_SPLITS) == 3
    for before, after in sl.TRANSFER_SPLITS:
        assert before[0] == sl.SPAN[0] and after[0] == before[1] + timedelta(days=1) and after[1] <= sl.SPAN[1]
        assert (after[0].month, after[0].day, after[1].month, after[1].day) == (1, 1, 12, 31) and after[0].year == after[1].year
    assert sl.TRANSFER_SPLITS[-1][1][1] == sl.bs.DEV[1] < sl.CONFIRM[0], "no day of the confirm span"


def test_a_transferred_book_takes_its_side_from_the_earlier_years_only_and_is_the_same_book_in_the_year_after(monkeypatch):
    before, after = sl.TRANSFER_SPLITS[0]
    days = [date(2020, 1, 5) + timedelta(days=7 * k) for k in range(40)]
    monkeypatch.setattr(sl, "monthly_members", lambda market, span=None: {"span": span})
    # The variable looks one way on the earlier years and the other way in the year after.
    monkeypatch.setattr(sl, "decision_ics", lambda market, read, monthly, span: read(span))
    measured, built = [], []

    def measure(market, weigher, span, *, simulate):
        measured.append((weigher, span))
        return {"annual_sharpe": 1.2 if span == before else -0.4, "total_position_annual_pct": 20.0 if span == before else -6.0,
                "clears_entry": span == before}

    def build(read, direction):
        built.append(direction(date(2022, 6, 5)))
        return ("book", len(built))

    monkeypatch.setattr(sl, "measure", measure)
    monkeypatch.setitem(sl.BOOKS, sl.SWEEP_LOGIC, build)
    names = {"turns": ("g", lambda span: {d: (0.1 if span == before else -0.2) for d in days}),
             "thin": ("g", lambda span: {d: 0.1 for d in days[:5]})}
    out = sl.transfer(xsb.Market(), names, splits=[(before, after)])
    assert built == [1], "one book per split, long the side the earlier years point to"
    assert [span for _, span in measured] == [before, after] and measured[0][0] == measured[1][0]
    row = out["turns"]["splits"][0]
    assert row == {"long": "high", "train_rank_correlation": 0.1, "test_rank_correlation": -0.2, "train_sharpe": 1.2,
                   "train_clears_entry": True, "test_sharpe": -0.4, "test_total_position_pct": -6.0}
    assert out["thin"]["splits"] == [None]


def _transfer_books(pairs, *, cleared=lambda before: before >= 1.0):
    return {f"b{i}": {"group": "g", "splits": [{"long": "low", "train_rank_correlation": -0.1, "test_rank_correlation": -0.1 if keep else 0.1,
                                                "train_sharpe": a, "train_clears_entry": cleared(a), "test_sharpe": b,
                                                "test_total_position_pct": 10.0 * b}]}
            for i, (a, b, keep) in enumerate(pairs)}


def test_the_transfer_reading_sets_the_real_carry_beside_null_sets_of_the_same_size():
    rng = sl.random.Random("fixture")
    real = _transfer_books([(x / 10, x / 10 + 0.01 * (x % 3), True) for x in range(-5, 15)])       # the order carries over
    null = _transfer_books([(rng.uniform(-1, 2), rng.uniform(-1, 2), rng.random() < 0.5) for _ in range(300)])
    out = sl.transfer_reading(real, null, draws=400)
    assert out["books"] == 20 and out["null_books"] == 300 and out["splits"] == 1 and out["null_sets"] == 400
    assert out["carry"] > 0.95 and out["p"] == pytest.approx(1 / 401, abs=1e-4) and abs(out["null_carry"]["mean"]) < 0.1
    assert out["carry_by_split"] == [out["carry"]]
    cleared = out["real"]["cleared_entry_before"]
    assert cleared["cells"] == 5 and cleared["test_year_positive_share"] == 1.0 and cleared["test_sharpe_mean"] > 1.0
    assert out["real"]["all"]["cells"] == 20 and out["real"]["all"]["rank_correlation_keeps_its_sign_share"] == 1.0
    assert 0.3 < out["null"]["all"]["rank_correlation_keeps_its_sign_share"] < 0.7
    assert [r["variable"] for r in out["real_cells_that_cleared_entry_before"]] == ["b19", "b18", "b17", "b16", "b15"]
    # No carry: the same reading on books whose next year is unrelated to the years before.
    flat = sl.transfer_reading(dict(list(null.items())[:20]), null, draws=400)
    assert flat["p"] > 0.05


def test_the_transfer_command_takes_groups_or_the_null_like_the_sweep(monkeypatch, capsys):
    asked = []

    def hold(cache, span=None, *, attach=True, flow=False):
        asked.append((span, attach, flow))
        return None

    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(sl.lab, "hold", hold)
    assert sl.main(["transfer"]) == sl.EXIT_USAGE
    capsys.readouterr()
    assert sl.main(["transfer", "--only", "null"]) == sl.EXIT_BLOCKED and sl.main(["transfer", "--only", "trade_flow"]) == sl.EXIT_BLOCKED
    assert asked == [(None, False, False), (None, False, True)], "the development market, as the sweep loads it"


# --- round 8: rung 2's added conditions and the listing book ----------------------------------------------------------

def test_the_later_years_are_the_three_each_read_after_the_years_before_it():
    assert sl.LATER_YEARS == (2022, 2023, 2024)
    good = sl.later_years({"2020": -50.0, "2021": -50.0, "2022": 20.0, "2023": -1.0, "2024": 10.0})
    assert good["passes"] and good["mean_pct"] == pytest.approx(9.67, abs=0.01) and good["negative"] == 1
    assert not sl.later_years({"2022": 30.0, "2023": -1.0, "2024": -1.0})["passes"], "two losing years"
    assert not sl.later_years({"2022": 7.0, "2023": 7.0, "2024": 7.0})["passes"], "under the floor of 7.5"
    assert not sl.later_years({"2022": 30.0, "2023": 30.0})["passes"], "a missing year is not a pass"


def test_a_book_passes_beside_its_null_books_only_under_the_rounds_alpha():
    nulls = [x / 100 for x in range(300)] + [None]                    # 0.00 .. 2.99
    top = sl.beside_nulls(3.5, nulls)
    assert top["p"] == pytest.approx(1 / 301, abs=1e-4) and top["passes"] and top["null_books"] == 300
    edge = sl.beside_nulls(2.90, nulls)
    assert edge["p"] == pytest.approx(11 / 301, abs=1e-4) and edge["passes"]
    assert not sl.beside_nulls(2.90, nulls, candidates=4)["passes"], "0.05 over four candidates"
    assert not sl.beside_nulls(None, nulls)["passes"] and not sl.beside_nulls(1.0, [])["passes"]


def _listing_market():
    """BTC and six seasoned coins from 2020-01-01; six coins listed 2021-03-01, of which NEW6 has no funding and THIN
    trades too little; a dated future that must never count."""
    start, end = date(2020, 1, 1), date(2021, 6, 30)
    def bars(first, volume=5e7):
        return {first + timedelta(days=k): xsb.Bar(open=100.0, close=100.0, quote_volume=volume)
                for k in range((end - first).days + 1)}
    listed = date(2021, 3, 1)
    names = {"BTCUSDT": bars(start), **{f"OLD{i}USDT": bars(start) for i in range(1, 7)},
             **{f"NEW{i}USDT": bars(listed) for i in range(1, 7)}, "THINUSDT": bars(listed, volume=1e6),
             "BTCUSDT_210625": bars(listed)}
    funding = {s: [(0, 0.0)] for s in names if s not in ("NEW6USDT",)}
    return xsb.Market(bars=names, funding=funding), listed


def test_the_listing_window_is_the_second_week_through_the_ninth_and_needs_volume_and_funding():
    market, listed = _listing_market()
    pools = sl.ListingPools(market)
    early, inside, late = listed + timedelta(days=6), listed + timedelta(days=7), listed + timedelta(days=63)
    assert pools.at(early)[0] == () and pools.at(late)[0] == ()
    new, seasoned, unfunded = pools.at(inside)
    assert new == tuple(f"NEW{i}USDT" for i in range(1, 6)) and unfunded == ("NEW6USDT",)
    assert seasoned == tuple(f"OLD{i}USDT" for i in range(1, 7)), "no hedge, no thin coin, no dated future"
    assert set(pools.at(late)[1]) >= {"NEW1USDT", "OLD1USDT"}, "after the window a listing is a seasoned coin"
    assert pools.at(date(2020, 3, 1))[0] == (), "the coins the archive begins with are not listings"


def test_the_listing_book_is_equal_shorts_under_the_cap_with_the_same_dollars_in_the_hedge():
    assert sl.short_basket(["A", "B", "C"]) == {}
    four = sl.short_basket(["A", "B", "C", "D"])
    assert four == {"A": -0.1, "B": -0.1, "C": -0.1, "D": -0.1, sl.HEDGE: pytest.approx(0.4)}, "the cap leaves it under one"
    twenty = sl.short_basket([f"C{i}" for i in range(20)])
    assert twenty["C0"] == pytest.approx(-0.05) and twenty[sl.HEDGE] == pytest.approx(1.0)
    market, listed = _listing_market()
    pools = sl.ListingPools(market)
    day = listed + timedelta(days=13)                                 # a Sunday inside the window
    assert day.weekday() == 6
    real = sl.listing_book(pools)(market, (), day, "x")
    assert set(real) == {f"NEW{i}USDT" for i in range(1, 6)} | {sl.HEDGE}


def test_a_placebo_holds_a_seasoned_partner_for_as_long_as_the_listing_is_held():
    market, listed = _listing_market()
    pools = sl.ListingPools(market)
    first, later = listed + timedelta(days=13), listed + timedelta(days=48)
    partner = pools.partner("NEW1USDT", "placebo:0")
    assert partner in {f"OLD{i}USDT" for i in range(1, 7)} and partner == pools.partner("NEW1USDT", "placebo:0")
    assert {pools.partner("NEW1USDT", f"placebo:{k}") for k in range(40)} == {f"OLD{i}USDT" for i in range(1, 7)}
    book = sl.listing_book(pools, placebo="placebo:3")
    a, b = book(market, (), first, "x"), book(market, (), later, "x")
    assert a == b, "the same partners through the window: a placebo trades no more than the real book"
    assert not set(a) & {f"NEW{i}USDT" for i in range(1, 7)}
    assert book(market, (), listed + timedelta(days=69), "x") == {}, "no listing held, no placebo held"


def test_listing_costs_are_stressed_on_every_coin_but_the_hedge_and_the_default_cost_is_unchanged():
    assert sl.listing_unit("NEW1USDT") == pytest.approx(26 / 1e4) and sl.listing_unit(sl.HEDGE) == pytest.approx(8 / 1e4)
    market, sunday = _two_coin_market([0.0])
    weigher = lambda m, members, day, signal, seed=None: {"UPUSDT": -1.0, "FLATUSDT": 1.0} if day == sunday else {}  # noqa: E731
    span = dict(start=sunday + timedelta(days=1), end=sunday + timedelta(days=3))
    monthly = {date(2021, 1, 1): ["UPUSDT", "FLATUSDT"]}
    plain = sl.simulate_held(market, sl.bs.CADENCE, monthly, weigher=weigher, **span)["daily"]
    priced = sl.simulate_held(market, sl.bs.CADENCE, monthly, weigher=weigher, unit_of=lambda s: 0.01 if s == "UPUSDT" else 0.0,
                              **span)["daily"]
    monday = sunday + timedelta(days=1)
    assert plain[monday]["cost"] == pytest.approx(2 * 8 / 1e4) and priced[monday]["cost"] == pytest.approx(0.01)


def test_the_listing_counts_carry_no_return_and_stop_a_thin_or_unfunded_book(monkeypatch):
    market, listed = _listing_market()
    span = (date(2021, 1, 1), date(2021, 6, 30))
    counts = sl.listing_counts(market, span)
    assert counts["returns_computed"] is False and counts["events"] == 5 and counts["archive_first_day"] == "2020-01-01"
    assert counts["by_year"]["2021"]["active_decisions"] == 8 and counts["by_year"]["2021"]["names_held_median"] == 5
    assert counts["funding_known_share"] == pytest.approx(5 / 6, abs=1e-3)
    assert set(counts["stop"]) == {"too many listing-window coins have no funding on file",
                                   "the book is flat at too many decisions", "too few listings"}
    flat = json.dumps(counts)
    assert all(word not in flat for word in ("sharpe", "pct", "net")), "counts only"
    assert sl.listing_run(market, span)["refused"] == counts["stop"], "a stopped book is not measured"
    monkeypatch.setattr(sl, "LISTING_MIN_FUNDING_KNOWN", 0.8)
    monkeypatch.setattr(sl, "LISTING_MIN_ACTIVE_SHARE", 0.2)
    monkeypatch.setattr(sl, "LISTING_MIN_EVENTS", 5)
    out = sl.listing_run(market, span, placebos=3)
    assert out["hypothesis"] == "H-L1" and out["passes"] is False and out["rungs"]["2c_beside_placebos"]["null_books"] <= 3
    assert out["cost_pct"] > 0 and out["sum_pct"] < 0, "flat prices: the book only pays its costs"


def test_the_listing_commands_load_bars_and_funding_alone(monkeypatch, capsys):
    asked = []

    def hold(cache, span=None, *, attach=True, flow=False):
        asked.append((span, attach, flow))
        return None

    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(sl.lab, "hold", hold)
    assert sl.main(["listing-counts"]) == sl.EXIT_BLOCKED and sl.main(["listing"]) == sl.EXIT_BLOCKED
    assert asked == [(None, False, False)] * 2


# --- round 9: the lower-tier reversal book ------------------------------------------------------------------------------

def _lower_market(moves=None):
    """BTC, three coins that out-trade everything, and ten smaller ones (LOW9 has no funding, LOWTHIN trades too
    little). ``moves`` gives a coin a daily drift, so the past week ranks the tier."""
    start, end = date(2020, 1, 1), date(2021, 6, 30)
    moves = moves or {}
    def bars(volume, drift=0.0):
        out, price = {}, 100.0
        for k in range((end - start).days + 1):
            nxt = price * (1 + drift)
            out[start + timedelta(days=k)] = xsb.Bar(open=price, close=nxt, quote_volume=volume)
            price = nxt
        return out
    names = {"BTCUSDT": bars(9e9), **{f"BIG{i}USDT": bars(5e9 - i) for i in range(3)},
             **{f"LOW{i}USDT": bars(5e7 - i, moves.get(f"LOW{i}USDT", 0.0)) for i in range(10)},
             "LOWTHINUSDT": bars(1e6)}
    return xsb.Market(bars=names, funding={s: [(0, 0.0)] for s in names if s != "LOW9USDT"})


def test_the_reversal_rank_never_reads_the_close_the_book_trades_beside():
    market = _lower_market({"LOW0USDT": 0.01})
    day = date(2021, 3, 7)
    before = sl.skip_day_return(market, "LOW0USDT", day)
    assert before == pytest.approx(1.01 ** 7 - 1)
    bars = market.bars["LOW0USDT"]
    bars[day] = xsb.Bar(open=bars[day].open, close=bars[day].close * 3, quote_volume=bars[day].quote_volume)
    assert sl.skip_day_return(market, "LOW0USDT", day) == before, "the decision day's close is not ranked"
    assert sl.skip_day_return(market, "LOW0USDT", date(2020, 1, 5)) is None, "no return without the whole window"


def test_the_lower_tier_is_the_liquid_funded_coins_after_the_first_ranks(monkeypatch):
    monkeypatch.setattr(sl, "LOWER_TIER_FROM", 4)                     # BTC and the three big coins are the top
    market = _lower_market()
    held, unfunded = sl.LowerTier(market, (date(2021, 1, 1), date(2021, 6, 30))).at(date(2021, 3, 7))
    assert set(held) == {f"LOW{i}USDT" for i in range(9)}, "the top ranks, the thin coin and the unfunded one are out"
    assert unfunded == ("LOW9USDT",)


def test_the_lower_book_is_long_the_weeks_losers_and_short_its_winners(monkeypatch):
    monkeypatch.setattr(sl, "LOWER_TIER_FROM", 4)
    moves = {f"LOW{i}USDT": (i - 4) / 1000 for i in range(9)}        # LOW0 fell most, LOW8 rose most
    market = _lower_market(moves)
    tier = sl.LowerTier(market, (date(2021, 1, 1), date(2021, 6, 30)))
    weights = sl.lower_book(tier, sl.skip_day_return, -1)(market, ("BTCUSDT",), date(2021, 3, 7), "x")
    assert {s for s, w in weights.items() if w > 0} == {f"LOW{i}USDT" for i in range(4)}, "the four that fell most"
    assert {s for s, w in weights.items() if w < 0} == {f"LOW{i}USDT" for i in range(5, 9)}, "the four that rose most"
    assert sum(w for w in weights.values() if w > 0) == pytest.approx(1) == pytest.approx(-sum(w for w in weights.values() if w < 0))
    assert "BTCUSDT" not in weights and "LOW9USDT" not in weights, "the passed members are not read; no funding, no position"


def test_the_lower_counts_carry_no_return_and_stop_a_tier_too_thin_in_the_later_years(monkeypatch):
    monkeypatch.setattr(sl, "LOWER_TIER_FROM", 4)
    market = _lower_market()
    span = (date(2020, 1, 1), date(2021, 6, 30))
    counts = sl.lower_counts(market, span)
    assert counts["returns_computed"] is False and counts["funding_known_share"] == pytest.approx(0.9)
    assert counts["by_year"]["2021"]["names_ranked_median"] == 9 and counts["by_year"]["2021"]["active_decisions"] == 26
    assert counts["first_active_decision"] > "2020-03-31", "ninety days listed before a month counts a coin"
    assert counts["stop"] == ["the tier holds a book at too few decisions in 2022, 2023, 2024"]
    flat = json.dumps(counts)
    assert "sharpe" not in flat and "pct" not in flat
    assert sl.lower_run(market, span)["refused"] == counts["stop"], "a stopped book is not measured"


def test_a_year_the_lower_book_is_flat_in_counts_as_zero_and_the_reading_is_one_of_three(monkeypatch):
    monkeypatch.setattr(sl, "LOWER_TIER_FROM", 4)
    monkeypatch.setattr(sl, "LOWER_MIN_ACTIVE", 0)
    monkeypatch.setattr(sl, "SPAN", (date(2020, 1, 1), date(2021, 6, 30)))
    moves = {f"LOW{i}USDT": (i - 4) / 1000 for i in range(9)}
    market = _lower_market(moves)
    span = (date(2021, 1, 1), date(2021, 6, 30))
    run = sl.lower_run(market, (date(2020, 1, 1), date(2021, 6, 30)), nulls=3)
    assert set(run["years_total_position_pct"]) == {"2020", "2021"}
    assert run["years_total_position_pct"]["2021"] < 0, "long the coins that keep falling, at stressed costs"
    assert run["rungs"]["2c_beside_nulls"]["null_books"] == 3 and run["reading"] == "closed" and not run["passes"]
    only_late = sl.lower_run(market, span, nulls=1)
    assert set(only_late["years_total_position_pct"]) == {"2021"}


# --- round 10: hourly bars and the crash-rebound event book -------------------------------------------------------------

import zipfile  # noqa: E402

H0 = sl.hour_of(date(2021, 3, 10))               # the hour the test crash is seen at


def _crash_world(coins=25, *, crash=True):
    """Daily bars that give coin i a beta of 1 + i/100 to BTC, and hourly opens around 2021-03-10 00:00: BTC falls
    6% in the four hours before it; coin i falls (i + 1)% over the day before it."""
    start, end = date(2020, 6, 1), date(2021, 6, 30)
    days = [start + timedelta(days=k) for k in range((end - start).days + 1)]
    def daily(beta):
        out, price = {}, 100.0
        for k, d in enumerate(days):
            nxt = price * (1 + beta * (0.01 if k % 2 else -0.01))
            out[d] = xsb.Bar(open=price, close=nxt, quote_volume=1e9 if beta == 1.0 else 5e7)
            price = nxt
        return out
    names = {"BTCUSDT": daily(1.0), **{f"C{i:02d}USDT": daily(1 + (i + 1) / 100) for i in range(coins)}}
    market = xsb.Market(bars=names, funding={s: [(0, 0.0)] for s in names})
    hourly = sl.Hourly()
    span = range(sl.hour_of(date(2021, 1, 1)), sl.hour_of(date(2021, 6, 1)))
    hourly.put("BTCUSDT", {h: (94.0 if crash and h >= H0 else 100.0) for h in span})
    for i in range(coins):
        hourly.put(f"C{i:02d}USDT", {h: (100.0 - (i + 1) if h >= H0 else 100.0) for h in span})
    return market, hourly


def test_hourly_opens_are_read_from_the_monthly_zips_as_flat_arrays_and_cut_at_the_span(tmp_path):
    folder = tmp_path / "klines-1h" / "BTCUSDT"
    folder.mkdir(parents=True)
    first = sl.hour_of(date(2024, 12, 31)) + 22
    rows = ["open_time,open,high,low,close,volume"] + [f"{(first + k) * sl.HOUR_MS},{100 + k},0,0,0,0" for k in (0, 1, 3)]
    with zipfile.ZipFile(folder / "BTCUSDT-1h-2024-12.zip", "w") as z:
        z.writestr("BTCUSDT-1h-2024-12.csv", "\n".join(rows))
    hourly = sl.load_hourly(tmp_path, ["BTCUSDT", "NONEUSDT"], (date(2020, 1, 1), date(2024, 12, 31)))
    assert hourly.open("BTCUSDT", first) == 100 and hourly.open("BTCUSDT", first + 1) == 101
    assert hourly.open("BTCUSDT", first + 3) is None, "an hour after the span's last day is never read"
    assert hourly.open("BTCUSDT", first - 1) is None and hourly.open("NONEUSDT", first) is None
    gap = sl.Hourly()
    gap.put("X", {10: 1.0, 13: 4.0})
    assert gap.open("X", 11) is None and gap.last_open("X", 10, 12) is None and gap.last_open("X", 10, 14) == (13, 4.0)


def test_an_event_is_the_first_hour_the_reference_coin_is_down_enough_and_holds_never_overlap():
    _, hourly = _crash_world()
    span = (date(2021, 1, 1), date(2021, 5, 31))
    assert sl.crash_events(hourly, span) == [H0], "hours one to three after it show the same fall and are the same event"
    again = dict.fromkeys(range(H0 + 80, H0 + 200), 80.0)
    hourly.put("BTCUSDT", {**{h: (94.0 if h >= H0 else 100.0) for h in range(H0 - 500, H0 + 80)}, **again})
    assert sl.crash_events(hourly, span) == [H0, H0 + 80]
    assert sl.crash_events(hourly, (date(2021, 1, 1), date(2021, 3, 12))) == [], "a hold that would pass the span's end"
    assert sl.crash_events(_crash_world(crash=False)[1], span) == []


def test_the_crash_book_buys_the_worst_residuals_sells_the_best_and_cancels_its_beta():
    market, hourly = _crash_world()
    members = ["BTCUSDT"] + [f"C{i:02d}USDT" for i in range(25)]
    weights, hedge = sl.crash_book(market, hourly, members, H0)
    longs, shorts = {s for s, w in weights.items() if w > 0}, {s for s, w in weights.items() if w < 0}
    assert longs == {f"C{i:02d}USDT" for i in range(20, 25)}, "the five that fell most beyond their beta"
    assert shorts == {f"C{i:02d}USDT" for i in range(5)} and "BTCUSDT" not in weights
    assert sum(w for w in weights.values() if w > 0) == pytest.approx(1) == pytest.approx(-sum(w for w in weights.values() if w < 0))
    assert hedge == pytest.approx(-0.2, abs=1e-6), "the longs' betas average 0.20 above the shorts'"
    hourly.put("C12USDT", {h: (100.0 if h < H0 else 87.0 if h == H0 else 1.0) for h in range(H0 - 100, H0 + 100)})
    assert sl.crash_book(market, hourly, members, H0) == (weights, hedge), "what a coin does after the hour does not rank it"
    assert sl.crash_book(market, hourly, members[:15], H0) is None, "too few ranked coins, no book"


def test_a_coin_that_stops_trading_inside_the_hold_leaves_at_its_last_open_and_is_not_dropped():
    market, hourly = _crash_world()
    funding = sl._FundingIndex(market)
    hourly.put("C00USDT", {h: (100.0 if h <= H0 + 1 else 50.0) for h in range(H0 - 100, H0 + 10)})   # ends nine hours in
    one = sl.crash_event(hourly, funding, {"C00USDT": 1.0}, 0.0, H0)
    assert one["gross"] == pytest.approx(-0.5), "the loss to its last bar is the book's"
    assert one["cost"] == pytest.approx(2 * 26 / 1e4) and one["net"] == pytest.approx(-0.5 - 0.0052)
    market.funding["C01USDT"] = [((H0 + 1) * sl.HOUR_MS, 0.5), ((H0 + 9) * sl.HOUR_MS, 0.01), ((H0 + 74) * sl.HOUR_MS, 0.5)]
    paid = sl.crash_event(hourly, sl._FundingIndex(market), {"C01USDT": -1.0}, 0.0, H0)
    assert paid["funding"] == pytest.approx(0.01), "a short receives what settles after the entry and by the exit"
    hedged = sl.crash_event(hourly, funding, {"C02USDT": 1.0}, -0.5, H0)
    assert hedged["cost"] == pytest.approx(2 * 26 / 1e4 + 0.5 * 2 * 8 / 1e4), "the reference leg pays its own cost"


def test_placebo_hours_match_the_events_year_by_year_and_keep_clear_of_them():
    _, hourly = _crash_world()
    span = (date(2021, 1, 1), date(2021, 5, 31))
    events = [H0, H0 + 400]
    drawn = sl.placebo_times(events, span, hourly, "seed:1")
    assert len(drawn) == 2 and drawn == sl.placebo_times(events, span, hourly, "seed:1") != sl.placebo_times(events, span, hourly, "seed:2")
    clear = sl.CRASH_SPACING_HOURS + sl.CRASH_RANK_HOURS
    assert all(abs(t - e) >= clear for t in drawn for e in events) and abs(drawn[0] - drawn[1]) >= clear


def test_the_crash_counts_carry_no_return_and_stop_on_too_few_events():
    market, hourly = _crash_world()
    span = (date(2021, 1, 1), date(2021, 5, 31))
    alone = sl.crash_counts(None, hourly, span)
    assert alone["events"] == 1 and alone["reference_only"] and alone["event_hours"] == ["2021-03-10T00:00Z"]
    full = sl.crash_counts(market, hourly, span)
    assert full["events_with_a_book"] == 1 and full["hourly_share"] == 1.0 and full["by_year"]["2021"]["positions_median"] == 10
    assert full["returns_computed"] is False and "too few events over the span" in full["stop"]
    flat = json.dumps(full)
    assert "sharpe" not in flat and "pct" not in flat
    assert sl.crash_run(market, hourly, span)["refused"] == full["stop"], "a stopped book is not measured"


def test_the_crash_run_reads_the_event_on_the_day_its_hold_ends_and_fills_flat_years_with_zero(monkeypatch):
    monkeypatch.setattr(sl, "CRASH_MIN_EVENTS", 1)
    monkeypatch.setattr(sl, "CRASH_MIN_EVENTS_LATER_YEAR", 0)
    market, hourly = _crash_world()
    for i in range(20, 25):                                             # the worst-hit five recover half their fall
        hourly.put(f"C{i:02d}USDT", {h: (100.0 if h < H0 else 100.0 - (i + 1) if h <= H0 + 1 else 100.0 - (i + 1) / 2)
                                     for h in range(sl.hour_of(date(2021, 1, 1)), sl.hour_of(date(2021, 6, 1)))})
    span = (date(2020, 6, 1), date(2021, 5, 31))
    monthly = sl.top_hundred(market, span)
    daily = sl.crash_daily(market, hourly, monthly, [H0])
    assert list(daily) == [date(2021, 3, 13)] and daily[date(2021, 3, 13)]["net"] > 0.1
    run = sl.crash_run(market, hourly, span, placebos=2)
    assert set(run["years_total_position_pct"]) == {"2020", "2021"} and run["years_total_position_pct"]["2020"] == 0.0
    assert run["rungs"]["2c_beside_placebos"]["null_books"] == 2 and run["reference_after_events"]["events"] == 1
    assert run["without_the_beta_leg"]["years_total_position_pct"]["2021"] > 0



# --- round 11: the deep-negative-funding book ---------------------------------------------------------------------------

from datetime import datetime, timezone  # noqa: E402

DEEP_DAY = date(2021, 3, 7)                      # a Sunday decision


def _deep_world(collapse: str | None = None):
    """BTC moving two percent up and down by turns; eight aged coins DEEP0..7 that move (1 + i/10) times as much
    (so that is their beta), of which DEEP0 and DEEP1 pay 0.1% a day to their longs and DEEP2 half of that; a coin
    listed too recently, one with no funding on file and one that trades too little. ``collapse`` halves that coin
    two days after :data:`DEEP_DAY` and ends its bars the day after."""
    start, end = date(2020, 6, 1), date(2021, 6, 30)
    days = [start + timedelta(days=k) for k in range((end - start).days + 1)]

    def bars(scale, volume=5e7, first=start, name=None):
        out, price = {}, 100.0
        for k, day in enumerate(days):
            if day < first:
                continue
            move = (0.02 if k % 2 else -0.02) * scale
            if name is not None and name == collapse:
                if day > DEEP_DAY + timedelta(days=3):
                    break
                if day == DEEP_DAY + timedelta(days=2):
                    move = -0.5
            out[day] = xsb.Bar(open=price, close=price * (1 + move), quote_volume=volume)
            price *= 1 + move
        return out

    def settled(rate):
        return [(int(datetime(d.year, d.month, d.day, 8, tzinfo=timezone.utc).timestamp() * 1000), rate) for d in days]

    names = {"BTCUSDT": bars(1.0, 9e9), **{f"DEEP{i}USDT": bars(1 + i / 10, name=f"DEEP{i}USDT") for i in range(8)},
             "YOUNGUSDT": bars(1.0, first=date(2021, 2, 1)), "NOFUNDUSDT": bars(1.0), "THINUSDT": bars(1.0, 1e6)}
    rates = {"DEEP0USDT": -0.001, "DEEP1USDT": -0.001, "DEEP2USDT": -0.0005, "YOUNGUSDT": -0.001}
    return xsb.Market(bars=names, funding={s: settled(rates.get(s, 0.0)) for s in names if s != "NOFUNDUSDT"})


def test_the_deep_pool_is_aged_liquid_funded_coins_and_the_picks_are_the_ones_paid_most_to_hold():
    pools = sl.DeepFunding(_deep_world())
    pool, unfunded = pools.at(DEEP_DAY)
    assert set(pool) == {f"DEEP{i}USDT" for i in range(8)}, "the hedge, the young, the thin and the unfunded are out"
    assert unfunded == 1
    assert pools.picks(DEEP_DAY) == ("DEEP0USDT", "DEEP1USDT"), "seven days at 0.1% is past the bar; at 0.05% it is not"
    assert pool["DEEP3USDT"][1] == pytest.approx(1.3), "the beta the hedge is sized by"


def test_the_deep_book_is_equal_longs_under_the_cap_with_the_hedge_against_their_beta():
    market = _deep_world()
    pools = sl.DeepFunding(market)
    weights = sl.deep_book(pools)(market, (), DEEP_DAY, "x")
    assert weights["DEEP0USDT"] == weights["DEEP1USDT"] == sl.DEEP_NAME_CAP, "two names do not fill the long side"
    assert weights["BTCUSDT"] == pytest.approx(-sl.DEEP_NAME_CAP * (1.0 + 1.1))
    assert set(sl.deep_book(pools, hedged=False)(market, (), DEEP_DAY, "x")) == {"DEEP0USDT", "DEEP1USDT"}
    assert sl.deep_basket([], {}) == {}


def test_a_deep_placebo_keeps_one_partner_for_the_spell_and_the_matched_one_is_as_beaten_down(monkeypatch):
    pools = sl.DeepFunding(_deep_world())
    later = DEEP_DAY + timedelta(days=7)
    assert pools.spell_start("DEEP0USDT", later) == pools.spell_start("DEEP0USDT", DEEP_DAY) < DEEP_DAY
    for seed in ("a", "b", "c"):
        first = pools.partner("DEEP0USDT", DEEP_DAY, seed, matched=False)
        assert first == pools.partner("DEEP0USDT", later, seed, matched=False), "drawn once for the spell"
        assert first not in pools.picks(DEEP_DAY) and first in pools.at(DEEP_DAY)[0]
    monkeypatch.setattr(sl, "DEEP_NEIGHBOURS", 1)
    start = pools.spell_start("DEEP0USDT", DEEP_DAY)
    pool = pools.at(start)[0]
    nearest = min((s for s in pool if s not in pools.picks(start)), key=lambda s: abs(pool[s][2] - pool["DEEP0USDT"][2]))
    assert pools.partner("DEEP0USDT", DEEP_DAY, "a", matched=True) == nearest == "DEEP2USDT"


def test_a_deep_pick_that_stops_trading_takes_its_loss_into_the_book():
    def held(market):
        sim = sl.simulate_held(market, sl.bs.CADENCE, {}, start=date(2021, 3, 1), end=date(2021, 3, 31),
                               weigher=sl.deep_book(sl.DeepFunding(market)), unit_of=sl.listing_unit)
        return sum(row["gross"] for row in sim["daily"].values())
    lost = held(_deep_world(collapse="DEEP0USDT")) - held(_deep_world())
    assert lost < -0.08, "a fifth of the long side was in a coin that halved and then stopped"


def test_the_deep_counts_carry_nothing_earned_and_stop_a_book_too_thin_in_the_later_years():
    market = _deep_world()
    span = (date(2020, 6, 1), date(2021, 6, 30))
    counts = sl.deep_counts(market, span)
    assert counts["returns_computed"] is False and counts["funding_known_share"] == pytest.approx(8 / 9, abs=5e-3)   # nine once the young coin has aged
    assert counts["spells"] == 3 and counts["spell_weeks_median"] > 4, "the young coin becomes a pick once it has aged"
    assert counts["by_year"]["2021"]["names_held_median"] == 2 and 0.4 < counts["by_year"]["2021"]["invested_share_mean"] < 0.6
    assert counts["by_year"]["2021"]["picks_trailing_funding_mean"] == pytest.approx(-0.007)
    assert set(counts["beside_the_pool"]) == {"trailing_move_median", "volatility_median"}
    assert counts["stop"] == ["too many of the pool's coins have no funding on file",
                              "the book holds a position at too few decisions in 2022, 2023, 2024"]
    flat = json.dumps(counts)
    assert "sharpe" not in flat and "pct" not in flat
    assert sl.deep_run(market, span)["refused"] == counts["stop"], "a stopped book is not measured"


def test_the_deep_run_splits_the_book_into_alts_and_hedge_and_its_reading_is_one_of_four(monkeypatch):
    span = (date(2020, 6, 1), date(2021, 6, 30))
    monkeypatch.setattr(sl, "DEEP_MIN_ACTIVE", 0)
    monkeypatch.setattr(sl, "DEEP_MIN_FUNDING_KNOWN", 0.5)
    monkeypatch.setattr(sl, "SPAN", span)
    run = sl.deep_run(_deep_world(), span, placebos=3)
    assert set(run["years_total_position_pct"]) == {"2020", "2021"}
    legs = run["legs"]
    assert legs["alts"]["funding_pct"] > 0, "the longs are paid"
    assert legs["hedge"]["cost_pct"] > 0 and legs["alts"]["cost_pct"] > legs["hedge"]["cost_pct"]
    whole = run["sum_pct"] + run["cost_pct"] - run["funding_pct"]
    assert legs["alts"]["price_pct"] + legs["hedge"]["price_pct"] == pytest.approx(whole, abs=0.02)
    assert legs["alts"]["funding_pct"] + legs["hedge"]["funding_pct"] == pytest.approx(run["funding_pct"], abs=0.02)
    assert run["rungs"]["2c_beside_placebos"]["null_books"] == 3 == run["beside_matched_placebos"]["null_books"]
    assert run["reading"] in ("closed", "not_apart_from_beaten_down_coins", "passes_on_carry", "passes_on_price")
    assert run["passes"] is (run["reading"] in ("passes_on_carry", "passes_on_price"))
    assert set(run["without_the_hedge"]) == {"annual_sharpe", "total_position_annual_pct", "years_total_position_pct"}


def test_the_deep_commands_load_bars_and_funding_alone(monkeypatch, capsys):
    asked = []

    def hold(cache, span=None, *, attach=True, flow=False):
        asked.append((span, attach, flow))
        return None

    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(sl.lab, "hold", hold)
    assert sl.main(["deep-counts"]) == sl.EXIT_BLOCKED and sl.main(["deep"]) == sl.EXIT_BLOCKED
    assert asked == [(None, False, False)] * 2


# --- round 12: the alt-against-Bitcoin rotation book --------------------------------------------------------------------

ROT_SPAN = (date(2020, 6, 1), date(2021, 6, 30))


def _rotation_world(surge: float = 0.0):
    """BTC moving two percent up and down by turns, and ten coins that move (1 + i/10) times as much plus a drift
    that turns every eight weeks: up against BTC, then down. SURGEUSDT is listed in mid-November 2020, so the first
    month it has the age to be a member of is 2021-03; ``surge`` is its daily drift over the two months before."""
    start, end = ROT_SPAN
    days = [start + timedelta(days=k) for k in range((end - start).days + 1)]

    def bars(scale, volume, drift=None, first=start):
        out, price = {}, 100.0
        for k, day in enumerate(days):
            if day < first:
                continue
            move = (0.02 if k % 2 else -0.02) * scale + (drift(k, day) if drift else 0.0)
            out[day] = xsb.Bar(open=price, close=price * (1 + move), quote_volume=volume)
            price *= 1 + move
        return out

    turn = lambda k, day: 0.004 if (k // 56) % 2 == 0 else -0.004          # noqa: E731
    names = {"BTCUSDT": bars(1.0, 9e9), **{f"ALT{i}USDT": bars(1 + i / 10, 5e8 - i, turn) for i in range(10)},
             "SURGEUSDT": bars(1.0, 9e8, lambda k, day: surge if date(2021, 1, 1) <= day < date(2021, 3, 1) else 0.0,
                               first=date(2020, 11, 15))}
    return xsb.Market(bars=names, funding={s: [(0, 0.0)] for s in names})


def test_the_rotation_signal_reads_the_members_fixed_before_its_window_not_the_months_new_entrants():
    day = date(2021, 3, 7)                                            # the window begins 2021-02-07
    calm, surged = sl.Rotation(_rotation_world(), ROT_SPAN), sl.Rotation(_rotation_world(surge=0.05), ROT_SPAN)
    assert "SURGEUSDT" in calm.monthly[date(2021, 3, 1)] and "SURGEUSDT" not in calm.monthly[date(2021, 2, 1)]
    assert surged.signal(day) == pytest.approx(calm.signal(day)), "a coin that entered on its run-up is not in the signal"
    assert "SURGEUSDT" in calm.basket(calm.monthly[date(2021, 3, 1)], day), "it is traded once it is a member"
    assert calm.signal(date(2020, 6, 7)) is None, "no side without a window of members"


def test_the_rotation_book_is_the_basket_on_the_signals_side_with_the_hedge_against_its_beta():
    market = _rotation_world()
    rot = sl.Rotation(market, ROT_SPAN)
    sides = dict(rot.active())
    up, down = next(d for d, w in sides.items() if w > 0), next(d for d, w in sides.items() if w < 0)
    for day, way in ((up, 1), (down, -1)):
        members = rot.monthly[date(day.year, day.month, 1)]
        weights = sl.rotation_book(rot)(market, members, day, "x")
        alts = {s: w for s, w in weights.items() if s != "BTCUSDT"}
        assert sum(alts.values()) == pytest.approx(way) and len(set(alts.values())) == 1, "equal weights, one side"
        betas = rot.basket(members, day)
        assert weights["BTCUSDT"] == pytest.approx(-way * sum(betas.values()) / len(betas))
        assert sl.rotation_book(rot, hedge="dollar")(market, members, day, "x")["BTCUSDT"] == -way
        assert "BTCUSDT" not in sl.rotation_book(rot, hedge=None)(market, members, day, "x")
    assert sl.rotation_book(rot)(market, (), date(2020, 6, 7), "x") == {}


def test_a_shifted_rotation_takes_the_real_books_sides_later_and_keeps_its_runs():
    rot = sl.Rotation(_rotation_world(), ROT_SPAN)
    rows = rot.active()
    days, sides = [d for d, _ in rows], [w for _, w in rows]
    assert [rot.way(d, 3) for d in days] == sides[3:] + sides[:3]
    assert rot.way(date(2020, 6, 7), 3) == 0, "flat where the real book is flat"
    assert list(rot.shifts()) == list(range(sl.ROTATION_SHIFT_MARGIN, len(rows) - sl.ROTATION_SHIFT_MARGIN + 1))


def test_the_rotation_counts_carry_no_return_and_no_side_and_stop_a_standing_bet():
    counts = sl.rotation_counts(_rotation_world(), ROT_SPAN)
    assert counts["returns_computed"] is False and counts["flips"] == counts["runs"] - 1 >= 2
    assert counts["placebo_shifts"] == counts["active_decisions"] - 2 * sl.ROTATION_SHIFT_MARGIN + 1
    assert counts["by_year"]["2021"]["names_traded_median"] >= 10
    assert counts["stop"] == ["the book has a side at too few decisions in 2022, 2023, 2024",
                              "the side turns too rarely to be told from a standing bet"]
    flat = json.dumps(counts)
    assert "sharpe" not in flat and "pct" not in flat and "long" not in flat and "short" not in flat
    assert sl.rotation_run(_rotation_world(), ROT_SPAN)["refused"] == counts["stop"], "a stopped book is not measured"


def test_the_rotation_run_prints_each_leg_by_year_and_its_reading_is_one_of_three(monkeypatch):
    monkeypatch.setattr(sl, "ROTATION_MIN_ACTIVE", 0)
    monkeypatch.setattr(sl, "ROTATION_MIN_FLIPS", 0)
    monkeypatch.setattr(sl, "SPAN", ROT_SPAN)
    run = sl.rotation_run(_rotation_world(), ROT_SPAN)
    assert set(run["years_total_position_pct"]) == {"2020", "2021"}
    by_year = run["legs_by_year_total_position_pct"]
    for year, value in run["years_total_position_pct"].items():
        assert by_year["alts"][year] + by_year["hedge"][year] == pytest.approx(value, abs=0.02)
    assert run["rungs"]["2c_beside_shifted_sides"]["null_books"] == run["counts"]["placebo_shifts"]
    assert run["reading"] in ("passes", "cost_dependent_lead", "closed")
    assert set(run["dollar_hedged"]) == {"annual_sharpe", "total_position_annual_pct", "years_total_position_pct"}
    assert run["at_engine_cost"]["cost_pct"] < run["cost_pct"], "the judged book pays the stressed cost"


def test_the_rotation_commands_load_bars_and_funding_alone(monkeypatch, capsys):
    asked = []

    def hold(cache, span=None, *, attach=True, flow=False):
        asked.append((span, attach, flow))
        return None

    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(sl.lab, "hold", hold)
    assert sl.main(["rotation-counts"]) == sl.EXIT_BLOCKED and sl.main(["rotation"]) == sl.EXIT_BLOCKED
    assert asked == [(None, False, False)] * 2


# --- round 13: the ladder's bar ------------------------------------------------------------------------------------------

BAR_SPAN = (date(2020, 1, 1), date(2024, 12, 31))


def _noise_books(count=24, *, held=lambda day: True):
    """Daily series of pure noise around a small cost, one row on each day ``held`` allows."""
    import random as _random
    out = []
    for k in range(count):
        rng, daily, day = _random.Random(k), {}, BAR_SPAN[0]
        while day <= BAR_SPAN[1]:
            if held(day):
                daily[day] = {"net": rng.gauss(0, 0.01) - 0.0002, "cost": 0.0002, "funding": 0.0}
            day += timedelta(days=1)
        out.append(daily)
    return out


def test_a_daily_series_is_read_exactly_as_measure_reads_it_with_flat_years_at_zero(monkeypatch):
    span = (date(2020, 6, 1), date(2021, 6, 30))
    market = _deep_world()
    monkeypatch.setattr(sl, "SPAN", span)
    weigher = sl.deep_book(sl.DeepFunding(market))
    measured = sl.measure(market, weigher, span, simulate=sl.simulate_held)
    daily = sl.simulate_held(market, sl.bs.CADENCE, sl.monthly_members(market, span), start=span[0], end=span[1],
                             weigher=weigher)["daily"]
    read = sl.read_daily(daily, span)
    for key in ("annual_sharpe", "sum_pct", "months", "total_position_annual_pct", "clears_entry"):
        assert read[key] == measured[key], key
    for year, value in measured["years_total_position_pct"].items():
        assert read["years_total_position_pct"][year] == value
    assert sl.read_daily({}, BAR_SPAN)["years_total_position_pct"] == dict.fromkeys(map(str, range(2020, 2025)), 0.0)


def test_a_squeezed_book_reads_as_its_daily_series_does_with_the_same_amount_on_every_held_day():
    quarter = lambda day: day.month <= 3                               # noqa: E731
    for daily in (_noise_books(1)[0], _noise_books(1, held=quarter)[0]):
        book = sl.squeeze(daily, BAR_SPAN)
        for add in (0.0, 0.0004):
            lifted = {day: {**row, "net": row["net"] + add} for day, row in daily.items()}
            assert sl.read_squeezed(book, add) == sl.read_daily(lifted, BAR_SPAN)
    assert sum(sl.squeeze(_noise_books(1, held=quarter)[0], BAR_SPAN)["held"]) == 5 * 90 + 2, "only days with a row"


def test_a_book_is_moved_to_a_net_return_on_its_own_days_and_a_book_that_never_holds_cannot_be():
    quarter = lambda day: day.month <= 3                               # noqa: E731
    book = sl.squeeze(_noise_books(1, held=quarter)[0], BAR_SPAN)
    for target in (0.0, 15.0, 40.0):
        assert sl.read_squeezed(book, sl.moved_to(book, target))["total_position_annual_pct"] == pytest.approx(target, abs=0.01)
    assert sl.moved_to(sl.squeeze({}, BAR_SPAN), 15.0) is None


def test_the_bar_is_the_net_return_at_which_half_and_four_fifths_of_a_shapes_null_books_pass():
    books = [sl.squeeze(daily, BAR_SPAN) for daily in _noise_books()]
    curve = sl.bar_curve(books, grid=(0, 10, 15, 20, 30, 40, 60, 80))
    rows = curve["curve"]
    assert curve["books"] == 24 and rows[0]["all"] == 0.0 and rows[-1]["all"] == 1.0
    assert all(a["all"] <= b["all"] for a, b in zip(rows, rows[1:])), "a larger return never passes fewer books"
    assert [row["floor"] for row in rows] == [0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0], "the floor is 15 for every book"
    assert all(row["2a"] <= min(row["sharpe"], row["floor"]) for row in rows), "the entry rule is both of its parts"
    half, most = curve["bar_net_pct"]["0.5"], curve["bar_net_pct"]["0.8"]
    assert curve["floor_pct"] <= half <= most, "noise only raises what the floor asks"
    null = curve["null"]
    assert null["before_cost_total_position_annual_pct_mean"] == pytest.approx(
        null["net_total_position_annual_pct_mean"] + null["cost_total_position_annual_pct_mean"], abs=0.02)
    short = [sl.squeeze(daily, BAR_SPAN) for daily in _noise_books(12)]
    assert sl.bar_curve(short, grid=(80,))["curve"][0]["2c"] == 0.0, "eleven nulls cannot give p under 0.05"
    assert sl._crossing([{"net_pct": 0, "all": 0.2}, {"net_pct": 10, "all": 0.7}], 0.5) == 10, "a grid point, not a line"
    assert sl._crossing([{"net_pct": 0, "all": 0.2}, {"net_pct": 10, "all": 0.4}], 0.5) is None, "not reached on the grid"


def test_each_bar_shape_is_built_as_its_rounds_null_was_and_the_command_loads_what_it_needs(monkeypatch, capsys):
    span = (date(2020, 6, 1), date(2021, 6, 30))
    monkeypatch.setattr(sl, "SPAN", span)
    market = _deep_world()
    weekly, fixed = (sl.bar_books(market, shape, span, books=2) for shape in ("rank_week", "rank_fixed"))
    assert len(weekly) == len(fixed) == 2 and weekly[0] != weekly[1]
    assert fixed[0]["cost"] < weekly[0]["cost"], "a variable fixed per coin turns the book over less"
    deep = sl.bar_books(market, "deep", span, books=2)
    assert len(deep) == 2 and all(sum(book["held"]) for book in deep)
    rotation = sl.bar_books(_rotation_world(), "rotation", ROT_SPAN)
    assert len(rotation) == len(sl.Rotation(_rotation_world(), ROT_SPAN).shifts())
    with pytest.raises(ValueError):
        sl.bar_books(market, "crash", span)
    with pytest.raises(ValueError):
        sl.bar_books(market, "other", span)
    asked = []

    def hold(cache, span=None, *, attach=True, flow=False):
        asked.append((span, attach, flow))
        return None

    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(sl.lab, "hold", hold)
    assert sl.main(["bar"]) == sl.EXIT_USAGE and sl.main(["bar", "--shape", "deep"]) == sl.EXIT_BLOCKED
    assert asked == [(None, False, False)]


# --- round 14: the deep-positive-funding book and the spot-gap book -----------------------------------------------------

def _hot_world(collapse: str | None = None):
    """:func:`_deep_world` with the funding turned round and doubled: DEEP0 and DEEP1 now *charge* their longs 0.2% a
    day (1.4% a week) and DEEP2 half of that; BTC charges its longs 0.01% a day."""
    cold = _deep_world(collapse)
    funding = {s: [(t, -2 * rate) for t, rate in rows] for s, rows in cold.funding.items()}
    funding["BTCUSDT"] = [(t, 0.0001) for t, _ in cold.funding["BTCUSDT"]]
    return xsb.Market(bars=cold.bars, funding=funding)


def test_the_hot_pool_picks_the_coins_that_charge_their_longs_most_and_l15s_pool_is_as_it_was():
    pools = sl.DeepFunding(_hot_world(), sl.HOT_FUNDING)
    assert pools.side == -1 and pools.picks(DEEP_DAY) == ("DEEP0USDT", "DEEP1USDT"), "1.4% a week is past the bar; 0.7% is not"
    start = pools.spell_start("DEEP0USDT", DEEP_DAY)
    for seed in ("a", "b", "c"):
        partner = pools.partner("DEEP0USDT", DEEP_DAY, seed, matched=False)
        assert pools.at(start)[0][partner][0] < sl.HOT_FUNDING, "a placebo coin is one under the threshold"
    cold = sl.DeepFunding(_hot_world())
    assert cold.side == 1 and cold.threshold == sl.DEEP_FUNDING and cold.picks(DEEP_DAY) == (), "nothing here is paid to hold"


def test_the_hot_book_is_equal_shorts_under_the_cap_with_the_hedge_bought_against_their_beta():
    market = _hot_world()
    pools = sl.DeepFunding(market, sl.HOT_FUNDING)
    weights = sl.deep_book(pools)(market, (), DEEP_DAY, "x")
    assert weights["DEEP0USDT"] == weights["DEEP1USDT"] == -sl.DEEP_NAME_CAP, "two names do not fill the short side"
    assert weights["BTCUSDT"] == pytest.approx(sl.DEEP_NAME_CAP * (1.0 + 1.1)), "the hedge is bought"
    assert set(sl.deep_book(pools, hedged=False)(market, (), DEEP_DAY, "x")) == {"DEEP0USDT", "DEEP1USDT"}


def test_a_hot_matched_placebo_is_the_coin_that_ran_up_most_alike_without_the_funding(monkeypatch):
    pools = sl.DeepFunding(_hot_world(), sl.HOT_FUNDING)
    monkeypatch.setattr(sl, "DEEP_NEIGHBOURS", 1)
    start = pools.spell_start("DEEP0USDT", DEEP_DAY)
    pool = pools.at(start)[0]
    nearest = min((s for s in pool if s not in pools.picks(start)), key=lambda s: abs(pool[s][2] - pool["DEEP0USDT"][2]))
    assert pools.partner("DEEP0USDT", DEEP_DAY, "a", matched=True) == nearest == "DEEP2USDT"


def test_a_shorted_coin_that_stops_trading_is_closed_at_its_last_close():
    def held(market):
        sim = sl.simulate_held(market, sl.bs.CADENCE, {}, start=date(2021, 3, 1), end=date(2021, 3, 31),
                               weigher=sl.deep_book(sl.DeepFunding(market, sl.HOT_FUNDING)), unit_of=sl.listing_unit)
        return sum(row["gross"] for row in sim["daily"].values())
    gained = held(_hot_world(collapse="DEEP0USDT")) - held(_hot_world())
    assert gained > 0.08, "a fifth of the short side was in a coin that halved and then stopped"


def test_the_hot_run_is_paid_on_its_shorts_pays_on_its_hedge_and_reads_carry_on_a_short_book(monkeypatch):
    span = (date(2020, 6, 1), date(2021, 6, 30))
    monkeypatch.setattr(sl, "DEEP_MIN_ACTIVE", 0)
    monkeypatch.setattr(sl, "DEEP_MIN_FUNDING_KNOWN", 0.5)
    monkeypatch.setattr(sl, "SPAN", span)
    counts = sl.hot_counts(_hot_world(), span)
    assert counts["returns_computed"] is False and 0 < counts["by_year"]["2021"]["picked_share_of_pool_mean"] < 0.5
    assert counts["by_year"]["2021"]["picks_trailing_funding_mean"] == pytest.approx(0.014)
    run = sl.hot_run(_hot_world(), span, placebos=3)
    assert (run["hypothesis"], run["logic"]) == ("H-F2", "L17")
    legs = run["legs"]
    assert legs["alts"]["funding_pct"] > 0, "the shorts are paid by the longs of the coins they sold"
    assert legs["hedge"]["funding_pct"] < 0, "the bought hedge pays its own funding"
    assert legs["alts"]["funding_pct"] + legs["hedge"]["funding_pct"] == pytest.approx(run["funding_pct"], abs=0.02)
    assert set(run["placebo_net_total_position_annual_pct_mean"]) == {"plain", "matched"}
    assert run["reading"] in ("closed", "not_apart_from_run_up_coins", "passes_on_carry", "passes_on_price")
    # every rung let through: what is left is whether the alts' funding carried the book
    monkeypatch.setattr(sl.bs, "clears_entry", lambda report: True)
    monkeypatch.setattr(sl, "later_years", lambda years: {"passes": True})
    monkeypatch.setattr(sl, "stability", lambda years: {"passes": True})
    monkeypatch.setattr(sl, "beside_nulls", lambda value, nulls, **kw: {"passes": True, "null_books": len(nulls)})
    assert sl.hot_run(_hot_world(), span, placebos=1)["reading"] == "passes_on_carry"
    monkeypatch.setattr(sl, "DEEP_CARRY_SHARE", 100.0)
    assert sl.hot_run(_hot_world(), span, placebos=1)["reading"] == "passes_on_price"
    monkeypatch.setattr(sl, "beside_nulls", lambda value, nulls, **kw: {"passes": False, "null_books": len(nulls)})
    assert sl.hot_run(_hot_world(), span, placebos=1)["reading"] == "closed", "a rung of the ladder itself was missed"


def _gap_world(alone: bool = False):
    """:func:`_deep_world` (DEEP0 and DEEP1 are L15's picks) with spot bars: DEEP0's, DEEP3's and DEEP6's perps close
    0.1%, 0.1% and 0.2% under their spot (DEEP6's spot is quoted per coin, its perp per thousand), DEEP4's closes on
    its spot, DEEP5 has spot bars on the last four days only, the rest have no pair. ``alone`` leaves the gap on
    DEEP0 only."""
    market = _deep_world()

    def spot(name, gap, scale=1.0, last=None):
        rows = sorted(market.bars[name].items())
        rows = rows if last is None else [(d, b) for d, b in rows if DEEP_DAY - timedelta(days=last) < d <= DEEP_DAY]
        return {d: xsb.Bar(open=b.open / scale, close=b.close / scale / (1 + gap), quote_volume=1e7) for d, b in rows}

    wide = 0.0 if alone else -0.001
    market.spot = {"DEEP0USDT": spot("DEEP0USDT", -0.001), "DEEP3USDT": spot("DEEP3USDT", wide),
                   "DEEP4USDT": spot("DEEP4USDT", 0.0), "DEEP5USDT": spot("DEEP5USDT", -0.01, last=4),
                   "SIXUSDT": spot("DEEP6USDT", 2 * wide, scale=1000.0)}
    market.spot["OTHERUSDT"] = spot("DEEP7USDT", -0.3)             # a spot of that name that is another asset
    market.spot_of = {**{s: (s, 1.0) for s in ("DEEP0USDT", "DEEP3USDT", "DEEP4USDT", "DEEP5USDT")},
                      "DEEP6USDT": ("SIXUSDT", 1000.0), "DEEP1USDT": None, "DEEP7USDT": ("OTHERUSDT", 1.0)}
    return market


def test_the_gap_pool_is_the_coins_with_a_spot_close_beside_theirs_and_the_picks_close_under_it():
    pools = sl.GapPools(_gap_world())
    pool = pools.at(DEEP_DAY)[0]
    assert set(pool) == {"DEEP0USDT", "DEEP3USDT", "DEEP4USDT", "DEEP6USDT"}, "no pair, or too few days of it, is out"
    assert pools.reading("DEEP7USDT", DEEP_DAY) is None, "a pair priced 30% away is another asset, not a deep discount"
    assert pools.side == 1 and pools.picks(DEEP_DAY) == ("DEEP0USDT", "DEEP3USDT", "DEEP6USDT")
    assert pool["DEEP0USDT"][0] == pytest.approx(-0.001) and pool["DEEP6USDT"][0] == pytest.approx(-0.002), "the price multiple is undone"
    assert pool["DEEP4USDT"][0] == pytest.approx(0.0)
    weights = sl.deep_book(pools)(pools.market, (), DEEP_DAY, "x")
    assert weights["DEEP3USDT"] == sl.DEEP_NAME_CAP and weights["BTCUSDT"] < 0, "L15's book on other picks"


def test_a_gap_book_that_is_l15s_picks_is_not_measured_and_one_with_too_few_decisions_is_not_either(monkeypatch):
    span = (date(2020, 6, 1), date(2021, 6, 30))
    monkeypatch.setattr(sl, "DEEP_MIN_ACTIVE", 0)
    monkeypatch.setattr(sl, "DEEP_MIN_FUNDING_KNOWN", 0.5)
    few = sl.gap_counts(_gap_world(), span)
    assert few["overlap"]["independent"] is None
    assert few["stop"] == ["too few decisions with a gap pick to tell the book from L15"]
    monkeypatch.setattr(sl.lab.sp, "MIN_COMPARED", 10)
    apart = sl.gap_counts(_gap_world(), span)
    overlap = apart["overlap"]
    assert overlap["share_that_are_l15_picks"] == pytest.approx(1 / 3, abs=1e-3) and overlap["independent"] is True
    assert overlap["chance_level"] == pytest.approx(1 / 4, abs=1e-3), "one of the four pool coins is an L15 pick"
    assert overlap["share_that_are_l17_picks"] == 0.0 and apart["stop"] == []
    assert "picks_gap_mean" in apart["by_year"]["2021"] and "picks_trailing_funding_mean" not in apart["by_year"]["2021"]
    flat = json.dumps(apart)
    assert apart["returns_computed"] is False and "sharpe" not in flat and "pct" not in flat
    same = sl.gap_counts(_gap_world(alone=True), span)
    assert same["overlap"]["share_that_are_l15_picks"] == 1.0 and same["overlap"]["independent"] is False
    assert same["stop"] == ["the gap picks are L15's picks: H-F1 under another signal, not a reading of its own"]
    assert sl.gap_run(_gap_world(alone=True), span)["refused"] == same["stop"], "a stopped book is not measured"
    monkeypatch.setattr(sl, "SPAN", span)
    run = sl.gap_run(_gap_world(), span, placebos=2)
    assert (run["hypothesis"], run["logic"]) == ("H-G1", "L18") and run["counts"]["overlap"]["independent"] is True


def test_the_hot_commands_load_bars_and_funding_alone_and_the_gap_commands_hang_the_spot_bars(monkeypatch, capsys, tmp_path):
    asked, hung = [], []
    market = _deep_world()

    def hold(cache, span=None, *, attach=True, flow=False):
        asked.append((span, attach, flow))
        return None if len(asked) <= 2 else market

    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(sl.lab, "hold", hold)
    monkeypatch.setattr(sl, "with_spot", lambda cache, held: hung.append(held))
    assert sl.main(["hot-counts"]) == sl.EXIT_BLOCKED and sl.main(["hot"]) == sl.EXIT_BLOCKED and hung == []
    assert sl.main(["gap-counts"]) == sl.EXIT_BLOCKED and sl.main(["gap"]) == sl.EXIT_BLOCKED
    assert asked == [(None, False, False)] * 4 and hung == [market, market], "no listing: blocked"
    monkeypatch.undo()
    assert sl.with_spot(tmp_path, market) is None, "no spot listing on file"
    folder = tmp_path / sl.lab.sp.SPOT_DIR
    folder.mkdir()
    (folder / sl.lab.ba.SYMBOLS_FILENAME).write_text(json.dumps(["DEEP0USDT"]), encoding="utf-8")
    late = date(2030, 1, 1)
    spot = xsb.Market(bars={"DEEP0USDT": {DEEP_DAY: xsb.Bar(open=1.0, close=1.0, quote_volume=1.0),
                                          late: xsb.Bar(open=1.0, close=1.0, quote_volume=1.0)}})
    monkeypatch.setattr(sl.xsb, "load_market", lambda path, funding=True: spot)
    held = sl.with_spot(tmp_path, market, (date(2020, 6, 1), date(2021, 6, 30)))
    assert set(held.spot["DEEP0USDT"]) == {DEEP_DAY}, "cut at the span's end"
    assert held.spot_of["DEEP0USDT"] == ("DEEP0USDT", 1.0) and held.spot_of["DEEP3USDT"] is None


# --- round 15: the open-interest flush book ----------------------------------------------------------------------------

import io  # noqa: E402
import zipfile  # noqa: E402

FLUSH_START = date(2022, 1, 1)
FLUSH_TEST_SPAN = (date(2022, 1, 1), date(2022, 6, 30))
FLUSH_DAY = date(2022, 4, 12)
FLUSH_COINS = [f"F{i:02d}USDT" for i in range(14)]


def _flush_world(monkeypatch, *, wide: bool = False, after: float = 0.0):
    """BTC moving two percent up and down by turns and fourteen members that move (1 + i/20) times as much, each
    with a flat open interest of 1000. On :data:`FLUSH_DAY` F00 falls 12% with its open interest down 20% (a flush),
    F01 falls 12% with its open interest flat, F02 loses 20% of its open interest on an ordinary day and F03's open
    interest for that day is missing. ``wide`` flushes F04..F12 as well that day. ``after`` is F00's move the day
    after the flush (nothing a decision on the flush day may read)."""
    first = date(2021, 6, 1)
    days = [first + timedelta(days=k) for k in range((FLUSH_TEST_SPAN[1] - first).days + 1)]
    falls = {"F00USDT", "F01USDT"} | ({f"F{i:02d}USDT" for i in range(4, 13)} if wide else set())
    drains = {"F00USDT", "F02USDT"} | ({f"F{i:02d}USDT" for i in range(4, 13)} if wide else set())

    def bars(name, scale, volume):
        out, price = {}, 100.0
        for k, day in enumerate(days):
            move = (0.02 if k % 2 else -0.02) * scale
            if day == FLUSH_DAY and name in falls:
                move = -0.12
            if day == FLUSH_DAY + timedelta(days=1) and name == "F00USDT":
                move = after or move
            out[day] = xsb.Bar(open=price, close=price * (1 + move), quote_volume=volume)
            price *= 1 + move
        return out

    names = {"BTCUSDT": bars("BTCUSDT", 1.0, 9e9), **{s: bars(s, 1 + i / 20, 5e7) for i, s in enumerate(FLUSH_COINS)}}
    stamp = lambda d: int(datetime(d.year, d.month, d.day, 8, tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
    market = xsb.Market(bars=names, funding={s: [(stamp(d), 0.0) for d in days] for s in names})
    market.closing_oi = {s: {d: (800.0 if d >= FLUSH_DAY and s in drains else 1000.0) for d in days} for s in FLUSH_COINS}
    del market.closing_oi["F03USDT"][FLUSH_DAY]
    months = {date(y, m, 1) for y, m in ((d.year, d.month) for d in days)}
    monkeypatch.setattr(sl, "monthly_members", lambda held, span=None: {m: ["BTCUSDT", *FLUSH_COINS] for m in months})
    return market


def test_a_flush_is_a_coin_whose_open_interest_and_price_both_fell_by_the_days_close(monkeypatch):
    flushes = sl.Flushes(_flush_world(monkeypatch), FLUSH_TEST_SPAN)
    read = flushes.read(FLUSH_DAY)
    assert "BTCUSDT" not in read and read["F00USDT"] == pytest.approx((-0.2, -0.12))
    assert read["F03USDT"][0] is None, "no open interest at that close: no change, and no event"
    assert flushes.events(FLUSH_DAY) == ("F00USDT",), "the price alone (F01) or the open interest alone (F02) is not one"
    assert flushes.events(FLUSH_DAY + timedelta(days=1)) == (), "the day after, the open interest is level again"
    later = sl.Flushes(_flush_world(monkeypatch, after=0.3), FLUSH_TEST_SPAN)
    assert later.read(FLUSH_DAY) == read, "nothing after the day's close is in what the decision reads"


def test_a_day_on_which_many_members_flush_is_the_markets_and_carries_no_event(monkeypatch):
    flushes = sl.Flushes(_flush_world(monkeypatch, wide=True), FLUSH_TEST_SPAN)
    assert len(flushes.flushed(FLUSH_DAY)) == 10 == sl.FLUSH_WIDE and flushes.events(FLUSH_DAY) == ()
    assert flushes.names(FLUSH_DAY) == []


def test_a_flush_is_bought_at_the_next_open_and_held_three_days_with_the_hedge_against_its_beta(monkeypatch):
    market = _flush_world(monkeypatch, after=0.3)
    flushes = sl.Flushes(market, FLUSH_TEST_SPAN)
    held = [k for k in range(-1, 5) if "F00USDT" in flushes.names(FLUSH_DAY + timedelta(days=k))]
    assert held == [0, 1, 2], "in the book decided on the flush day and the two after: held three days from the next open"
    weights = sl.flush_book(flushes)(market, (), FLUSH_DAY, "x")
    assert weights["F00USDT"] == sl.DEEP_NAME_CAP and weights["BTCUSDT"] == pytest.approx(-sl.DEEP_NAME_CAP * 1.0, rel=0.2)
    assert set(sl.flush_book(flushes, hedged=False)(market, (), FLUSH_DAY, "x")) == {"F00USDT"}
    sim = sl.simulate_held(market, sl.FLUSH_CADENCE, {}, start=FLUSH_TEST_SPAN[0], end=FLUSH_TEST_SPAN[1],
                           weigher=sl.flush_book(flushes, hedged=False), unit_of=sl.listing_unit)
    assert sorted(sim["daily"]) == [FLUSH_DAY + timedelta(days=k) for k in (1, 2, 3, 4)], "three held days, and the exit's cost"
    assert sim["daily"][FLUSH_DAY + timedelta(days=4)]["gross"] == 0.0
    first = sim["daily"][FLUSH_DAY + timedelta(days=1)]
    assert first["gross"] == pytest.approx(sl.DEEP_NAME_CAP * 0.3), "the day after the flush is earned from its open"


def test_a_flush_placebo_is_a_member_without_the_flush_and_the_matched_one_fell_as_much(monkeypatch):
    flushes = sl.Flushes(_flush_world(monkeypatch), FLUSH_TEST_SPAN)
    for seed in ("a", "b", "c", "d"):
        plain = flushes.partner("F00USDT", FLUSH_DAY, seed, matched=False)
        assert plain not in ("F00USDT", "F03USDT", "BTCUSDT") and plain in FLUSH_COINS, "not the event, and its open interest is known"
    monkeypatch.setattr(sl, "FLUSH_NEIGHBOURS", 1)
    assert flushes.partner("F00USDT", FLUSH_DAY, "a", matched=True) == "F01USDT", "the coin that fell 12% with its open interest flat"
    assert flushes.names(FLUSH_DAY, "a", matched=True) == ["F01USDT"]


def _oi_zip(folder, symbol, day, stamps):
    folder = folder / symbol
    folder.mkdir(parents=True, exist_ok=True)
    rows = ["create_time,symbol,sum_open_interest,sum_open_interest_value"]
    rows += [f"{stamp},{symbol},{value},1" for stamp, value in stamps]
    with zipfile.ZipFile(folder / f"{symbol}-metrics-{day.isoformat()}.zip", "w") as z:
        z.writestr(f"{symbol}-metrics-{day.isoformat()}.csv", "\n".join(rows) + "\n")


def test_the_closing_open_interest_is_the_days_last_row_and_no_file_past_the_span_is_opened(tmp_path):
    span = (date(2022, 1, 1), date(2022, 1, 3))
    _oi_zip(tmp_path, "AUSDT", date(2021, 12, 31), [("2021-12-31 23:55:00", 90)])
    _oi_zip(tmp_path, "AUSDT", date(2022, 1, 1), [("2022-01-01 12:00:00", 6), ("2022-01-01 23:55:00", 100), ("2022-01-01 00:00:00", 5)])
    _oi_zip(tmp_path, "AUSDT", date(2022, 1, 2), [("2022-01-02 00:00:00", 7), ("2022-01-02 12:00:00", 8)])
    _oi_zip(tmp_path, "AUSDT", date(2022, 1, 4), [("2022-01-04 23:55:00", 400)])
    (tmp_path / "AUSDT" / "AUSDT-metrics-2022-01-05.zip").write_bytes(b"not a zip")
    (tmp_path / "AUSDT" / "AUSDT-metrics-2022-01-03.zip").write_bytes(b"not a zip")
    got = sl.closing_oi(tmp_path, ["AUSDT", "NOFILESUSDT"], span)
    assert got == {"AUSDT": {date(2021, 12, 31): 90.0, date(2022, 1, 1): 100.0}}, (
        "the day before the span is read for the first change; the closing row is the latest stamp wherever it sits "
        "in the file; a day whose latest row is at noon has no closing value; "
        "a broken file has none; a day past the span is not there")


def test_the_flush_counts_carry_nothing_earned_and_stop_a_thin_book_or_one_on_round_tens_days(monkeypatch):
    market = _flush_world(monkeypatch)
    counts = sl.flush_counts(market, FLUSH_TEST_SPAN)
    assert counts["events"] == 1 == counts["event_days"] and counts["returns_computed"] is False
    assert counts["by_year"]["2022"]["days_with_a_position"] == 3 and counts["by_year"]["2022"]["known_share"] > 0.99
    assert counts["events_signal_median"] == {"open_interest_change": -0.2, "day_move": -0.12}
    assert counts["events_without_a_placebo_share"] == {"plain": 0.0, "matched": 0.0}
    assert abs(counts["matched_partner_day_move_median"]) <= 0.12, "one of the three nearest: F01 fell as much, the others barely moved"
    monkeypatch.setattr(sl, "FLUSH_NEIGHBOURS", 1)
    assert sl.flush_counts(market, FLUSH_TEST_SPAN)["matched_partner_day_move_median"] == -0.12, "the nearest one fell as the event did"
    monkeypatch.setattr(sl, "FLUSH_NEIGHBOURS", 3)
    assert counts["stop"] == ["too few days with an event over the span", "too few days with an event in 2022"]
    flat = json.dumps(counts)
    assert "sharpe" not in flat and "pct" not in flat
    assert sl.flush_run(market, FLUSH_TEST_SPAN)["refused"] == counts["stop"], "a stopped book is not measured"
    monkeypatch.setattr(sl, "FLUSH_MIN_EVENT_DAYS", 1)
    monkeypatch.setattr(sl, "FLUSH_MIN_EVENT_DAYS_A_YEAR", 1)
    assert sl.flush_counts(market, FLUSH_TEST_SPAN)["stop"] == []
    for crash in (FLUSH_DAY, FLUSH_DAY - timedelta(days=1)):
        on = sl.flush_counts(market, FLUSH_TEST_SPAN, crash_days=[crash])
        assert on["events_on_crash_days_share"] == 1.0
        assert on["stop"] == ["the events fall on round 10's event days: H-C1 on daily bars, not a reading of its own"]
    assert sl.flush_counts(market, FLUSH_TEST_SPAN, crash_days=[FLUSH_DAY - timedelta(days=2)])["stop"] == []
    wide = sl.flush_counts(_flush_world(monkeypatch, wide=True), FLUSH_TEST_SPAN)
    assert wide["events"] == 0 and wide["by_year"]["2022"]["wide_days"] == 1 and wide["by_year"]["2022"]["flushes_on_wide_days"] == 10
    del market.closing_oi["F05USDT"], market.closing_oi["F06USDT"]
    blind = sl.flush_counts(market, FLUSH_TEST_SPAN)
    assert blind["stop"] == ["too little of the universe has its open interest on file in 2022"]


def test_the_flush_run_is_read_daily_over_its_own_span_and_the_weekly_books_are_read_as_before(monkeypatch):
    market = _flush_world(monkeypatch)
    monkeypatch.setattr(sl, "FLUSH_MIN_EVENT_DAYS", 1)
    monkeypatch.setattr(sl, "FLUSH_MIN_EVENT_DAYS_A_YEAR", 1)
    run = sl.flush_run(market, FLUSH_TEST_SPAN, placebos=3)
    assert (run["hypothesis"], run["logic"]) == ("H-O1", "L19") and set(run["years_total_position_pct"]) == {"2022"}
    assert run["rungs"]["2c_beside_placebos"]["null_books"] == 3 == run["beside_matched_placebos"]["null_books"]
    assert run["reading"] in ("closed", "not_apart_from_beaten_down_coins", "passes")
    assert run["passes"] is (run["reading"] == "passes")
    assert run["legs"]["alts"]["cost_pct"] > 0 and set(run["placebo_net_total_position_annual_pct_mean"]) == {"plain", "matched"}
    asked: list[date] = []

    def recorder(held, members, day, signal, *, seed=None):
        asked.append(day)
        return {}

    sl.measure(market, recorder, FLUSH_TEST_SPAN, simulate=sl.simulate_held)
    assert asked and all(day.weekday() == 6 for day in asked), "a book that names no cadence is decided on Sundays, as before"
    asked.clear()
    sl.measure(market, recorder, FLUSH_TEST_SPAN, simulate=sl.simulate_held, cadence=sl.FLUSH_CADENCE)
    assert len(asked) == (FLUSH_TEST_SPAN[1] - FLUSH_TEST_SPAN[0]).days + 1, "the flush book is decided every day"


def test_the_flush_commands_load_bars_and_funding_over_their_span_then_the_closing_open_interest(monkeypatch, capsys):
    asked, read = [], []
    market = _flush_world(monkeypatch)

    def hold(cache, span=None, *, attach=True, flow=False):
        asked.append((span, attach, flow))
        return None if len(asked) == 1 else market

    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(sl.lab, "hold", hold)
    monkeypatch.setattr(sl, "closing_oi", lambda folder, symbols, span=None: read.append(sorted(symbols)) or market.closing_oi)
    monkeypatch.setattr(sl, "load_hourly", lambda archive, symbols, span=None: read.append(list(symbols)) or sl.Hourly())
    assert sl.main(["flush-counts"]) == sl.EXIT_BLOCKED and read == [], "no contract types: blocked before anything else is read"
    assert sl.main(["flush-counts"]) == sl.EXIT_BLOCKED, "the test market has one event: the counts say stop"
    assert asked == [(sl.FLUSH_SPAN, False, False)] * 2
    assert read == [["BTCUSDT", *FLUSH_COINS], ["BTCUSDT"]], "the members' closing open interest, and the hedge coin's hours alone"
    assert json.loads(capsys.readouterr().out)["returns_computed"] is False


# --- round 16: the funding tail held with its own spot -----------------------------------------------------------------

PAIR_START = date(2021, 1, 4)                    # the Monday after a Sunday decision


def _pair_market(spot_closes, perp_closes, *, rate=0.0, perp_highs=None, perp_days=None, first=(100.0, 100.0)):
    """One coin, PUSDT, from :data:`PAIR_START`: each day opens at the close before (``first`` on the first day)."""
    def bars(closes, start, highs=None):
        out, price = {}, start
        for k, close in enumerate(closes):
            high = None if highs is None else highs[k]
            out[PAIR_START + timedelta(days=k)] = xsb.Bar(open=price, close=close, quote_volume=1e8, high=high)
            price = close
        return out

    perp = bars(perp_closes[:perp_days], first[1], perp_highs or [max(c, 0.0) for c in perp_closes])
    days = [PAIR_START + timedelta(days=k) for k in range(len(spot_closes))]
    settled = [(int(datetime(d.year, d.month, d.day, 8, tzinfo=timezone.utc).timestamp() * 1000), rate) for d in days]
    market = xsb.Market(bars={"PUSDT": perp}, funding={"PUSDT": settled})
    market.spot, market.spot_of = {"PUSDT": bars(spot_closes, first[0])}, {"PUSDT": ("PUSDT", 1.0)}
    return market


def _pairs(market, days, **more):
    always = lambda market, members, day, signal, seed=None: {"PUSDT": 1.0}  # noqa: E731
    sim = sl.simulate_pairs(market, sl.bs.CADENCE, {}, start=PAIR_START, end=PAIR_START + timedelta(days=days - 1),
                            weigher=always, **more)
    return sim["daily"]


def test_a_pair_earns_nothing_from_the_coins_move_while_the_premium_holds_and_the_short_is_paid_the_funding():
    spot = [110.0, 90.0, 130.0, 130.0, 60.0, 61.0]
    daily = _pairs(_pair_market(spot, [c * 1.001 for c in spot], rate=0.001, first=(100.0, 100.1)), 5)
    assert all(row["gross"] == pytest.approx(0.0, abs=1e-12) for row in daily.values()), "the two legs cancel"
    first, second = daily[PAIR_START], daily[PAIR_START + timedelta(days=1)]
    assert first["cost"] == pytest.approx(sl.pair_unit(spot=True) + sl.pair_unit(spot=False)), "both legs are bought"
    assert sl.pair_unit(spot=True) == pytest.approx(0.0031) and sl.pair_unit(spot=False) == pytest.approx(0.0026)
    assert first["funding"] == pytest.approx(0.001), "a positive rate is paid to the short"
    assert second["funding"] == pytest.approx(0.001 * 1.1), "on what the short is worth that day"
    assert second["cost"] == 0.0 and second["net"] == pytest.approx(second["funding"])
    engine = _pairs(_pair_market(spot, [c * 1.001 for c in spot], first=(100.0, 100.1)), 5, stressed=False)
    assert engine[PAIR_START]["cost"] == pytest.approx((10 + 3 + 5 + 3) / 10_000), "the engine's slippage on both legs"


def test_a_premium_that_narrows_pays_the_pair_and_one_that_widens_costs_it():
    flat = [100.0] * 5
    narrowing = _pairs(_pair_market(flat, [100.5, 100.0, 100.0, 100.0, 100.0], first=(100.0, 101.0)), 4)
    assert sum(row["gross"] for row in narrowing.values()) == pytest.approx(1 / 101), "the short gains what the premium gave up"
    widening = _pairs(_pair_market(flat, [101.0, 102.0, 102.0, 102.0, 102.0]), 4)
    assert sum(row["gross"] for row in widening.values()) == pytest.approx(-0.02)


def test_at_the_weekly_decision_a_pair_is_marked_again_and_the_trade_is_what_each_leg_has_become():
    closes = [100.0] * 5 + [130.0] * 4
    daily = _pairs(_pair_market(closes, closes), 8)
    again = daily[PAIR_START + timedelta(days=7)]
    assert again["cost"] == pytest.approx(0.3 * (sl.pair_unit(spot=True) + sl.pair_unit(spot=False))), \
        "each leg grew to 1.3 and the target is 1"


def test_a_short_whose_perp_doubles_loses_its_margin_and_the_spot_is_sold_that_day():
    spot = [150.0, 180.0, 180.0, 180.0]
    log = {}
    daily = _pairs(_pair_market(spot, spot, rate=0.001, perp_highs=[150.0, 200.0, 180.0, 180.0]), 4, log=log)
    hit = daily[PAIR_START + timedelta(days=1)]
    assert hit["gross"] == pytest.approx(0.3 - 0.5), "the spot's day, less the half of the margin that was left"
    assert hit["funding"] == 0.0 and hit["cost"] == pytest.approx(1.8 * sl.pair_unit(spot=True)), "only the spot is sold"
    assert sum(row["gross"] for row in daily.values()) == pytest.approx(0.8 - 1.0), "the spot's gain against the whole margin"
    assert PAIR_START + timedelta(days=2) not in daily, "nothing is held after it"
    assert [(r["day"], r["symbol"]) for r in log["liquidations"]] == [("2021-01-05", "PUSDT")]
    calm = _pairs(_pair_market(spot, spot, perp_highs=[150.0, 194.0, 180.0, 180.0]), 4)
    assert PAIR_START + timedelta(days=2) in calm, "a high under 1.95 times the entry leaves the pair alone"


def test_a_pair_whose_closes_come_apart_is_closed_and_a_gain_printed_that_day_is_not_counted():
    log = {}
    spliced = _pairs(_pair_market([100.0, 300.0, 300.0], [100.0, 100.0, 100.0], rate=0.001), 3, log=log)
    day = PAIR_START + timedelta(days=1)
    assert spliced[day]["gross"] == 0.0 and spliced[day]["funding"] == 0.0, "another asset under the name cannot pay the book"
    assert spliced[day]["cost"] == pytest.approx(3.0 * sl.pair_unit(spot=True) + 1.0 * sl.pair_unit(spot=False))
    assert PAIR_START + timedelta(days=2) not in spliced
    row, = log["out_of_band"]
    assert (row["day"], row["printed"], row["counted"]) == ("2021-01-05", pytest.approx(2.0), 0.0)
    lost = _pairs(_pair_market([100.0, 30.0, 30.0], [100.0, 100.0, 100.0]), 3)
    assert lost[day]["gross"] == pytest.approx(-0.7), "a loss printed that day is counted"
    inside = _pairs(_pair_market([100.0, 100.0, 100.0], [100.0, 109.0, 109.0]), 3)
    assert PAIR_START + timedelta(days=2) in inside, "inside the band the pair stays"


def test_a_pair_one_of_whose_legs_stops_trading_is_closed_at_that_days_close():
    log = {}
    closes = [100.0, 120.0, 120.0, 120.0]
    daily = _pairs(_pair_market(closes, [100.0, 121.0, 121.0, 121.0], perp_days=2), 4, log=log)
    last = PAIR_START + timedelta(days=1)
    assert daily[last]["gross"] == pytest.approx(0.20 - 0.21) and PAIR_START + timedelta(days=2) not in daily
    assert daily[last]["cost"] == pytest.approx(1.2 * sl.pair_unit(spot=True) + 1.21 * sl.pair_unit(spot=False))
    assert log["leg_ended"] == [{"day": "2021-01-05", "symbol": "PUSDT", "leg": "perp"}]


def test_the_tail_is_the_top_tenth_strictly_over_the_median_and_a_tie_at_its_edge_is_left_out_whole():
    ten = {f"C{i}": i / 1000 for i in range(10)}
    assert sl.tail_of(ten) == (("C9",), False), "one in ten"
    assert sl.tail_of({**ten, "C10": 0.010}) == (("C10", "C9"), False), "rounded up"
    assert sl.tail_of({**ten, "C8": 0.009}) == ((), True), "the edge of the tenth sits in a tie: neither is taken"
    assert sl.tail_of({**ten, "C10": 0.009, "C8": 0.009}) == ((), True), "two places, three tied coins: none of them"
    assert sl.tail_of(dict.fromkeys(ten, 0.0003)) == ((), True), "a pool at one rate has no tail"
    assert sl.tail_of({f"C{i}": -i / 1000 for i in range(10)}) == ((), False), "the least negative is not paid: no pick"
    assert sl.tail_of({}) == ((), False)
    default = {"A": 0.0100, "B": 0.0021, "C": 0.0021, "D": 0.0021, "E": 0.0001}
    assert sl.paid_over_the_middle(default) == {"A"}, "a coin at the rate most of the pool pays is not over the middle"
    assert sl.paid_over_the_middle({"A": -0.001, "B": -0.002, "C": -0.003}) == set(), "over the median and still paying away"


def _basis_world():
    """:func:`_deep_world`'s eight coins, each with a spot that closes where its perp does. DEEP1 charges its longs
    0.4% a day until the end of January 2021, 0.1% a day to the end of March, nothing after; DEEP0 0.3% a day
    throughout; the other six pay small rates, all different."""
    market = _deep_world()
    days = sorted(market.bars["BTCUSDT"])

    def settled(rate_on):
        return [(int(datetime(d.year, d.month, d.day, 8, tzinfo=timezone.utc).timestamp() * 1000), rate_on(d)) for d in days]

    small = {"DEEP2USDT": 0.0008, "DEEP3USDT": 0.0006, "DEEP4USDT": 0.0001, "DEEP5USDT": 0.0002,
             "DEEP6USDT": 0.0003, "DEEP7USDT": 0.0004}
    market.funding["DEEP0USDT"] = settled(lambda d: 0.003)
    market.funding["DEEP1USDT"] = settled(lambda d: 0.004 if d <= date(2021, 1, 31) else 0.001 if d <= date(2021, 3, 31) else 0.0)
    for name, rate in small.items():
        market.funding[name] = settled(lambda d, rate=rate: rate)
    coins = [f"DEEP{i}USDT" for i in range(8)]
    market.spot = {s: {d: xsb.Bar(open=b.open, close=b.close, quote_volume=1e7) for d, b in market.bars[s].items()} for s in coins}
    market.spot_of = {s: (s, 1.0) for s in coins}
    return market


BASIS_SPAN = (date(2020, 6, 1), date(2021, 6, 30))


def test_a_coin_enters_the_pair_book_in_the_tail_and_stays_while_its_funding_is_over_the_pools_median():
    tail = sl.BasisTail(_basis_world(), BASIS_SPAN)
    early, middle, late = date(2021, 1, 17), DEEP_DAY, date(2021, 5, 2)
    assert set(tail.pool(middle)) == {f"DEEP{i}USDT" for i in range(8)}, "L15's pool, with a spot beside the perp"
    assert tail.tail(early) == ("DEEP1USDT",) and tail.held(early) == ("DEEP1USDT",), "DEEP0 is second: never in the tenth yet"
    assert tail.tail(middle) == ("DEEP0USDT",) and tail.held(middle) == ("DEEP0USDT", "DEEP1USDT"), \
        "DEEP1 left the tenth and is still over the median"
    assert tail.held(late) == ("DEEP0USDT",), "under the median it leaves"
    assert tail.spell_start("DEEP1USDT", middle) == tail.spell_start("DEEP1USDT", early) < early
    partner = tail.partner("DEEP0USDT", middle, "plain:0")
    start = tail.spell_start("DEEP0USDT", middle)
    assert partner in tail.pool(start) and partner not in tail.held(start), "a pool coin the book did not hold"
    assert tail.partner("DEEP0USDT", middle + timedelta(days=7), "plain:0") == partner, "one partner for the spell"
    weights = sl.basis_book(tail)(tail.market, (), middle, "x")
    assert weights == {"DEEP0USDT": sl.DEEP_NAME_CAP, "DEEP1USDT": sl.DEEP_NAME_CAP}, "pairs under the cap, no hedge leg"
    other = sl.basis_book(tail, placebo="plain:0")(tail.market, (), middle, "x")
    assert other and not set(other) & {"DEEP0USDT"} and all(w == sl.DEEP_NAME_CAP for w in other.values())


def test_the_pair_counts_carry_nothing_earned_and_no_tally_against_a_funding_level(monkeypatch):
    unfunded = sl.basis_counts(_basis_world(), BASIS_SPAN)
    assert unfunded["stop"][0] == "too many of the pool's coins have no funding on file", "one eligible coin in nine has none"
    monkeypatch.setattr(sl, "DEEP_MIN_FUNDING_KNOWN", 0.5)
    counts = sl.basis_counts(_basis_world(), BASIS_SPAN)
    assert counts["stop"] == ["the book holds a pair at too few decisions in 2022, 2023, 2024"]
    assert sl.basis_run(_basis_world(), BASIS_SPAN)["refused"] == counts["stop"], "a stopped book is not measured"
    flat = json.dumps(counts)
    assert counts["returns_computed"] is False and "sharpe" not in flat and "pct" not in flat
    year = counts["by_year"]["2021"]
    assert year["names_held_median"] in (1, 1.5, 2) and year["tail_share_of_pool_mean"] == pytest.approx(1 / 8, abs=1e-3)
    assert year["decisions_with_a_tie_at_the_tenth"] == 0 and year["pool_share_at_the_median_value_mean"] == 0.0
    assert year["entered_trailing_funding_mean"] == pytest.approx(0.021, abs=1e-5), "DEEP0 entered in 2021, at 0.3% a day"
    assert counts["held_share_of_pool_mean"] < sl.BASIS_NOT_A_TAIL and counts["not_a_tail"] is False
    assert counts["pool_beside_l15s_pool"] == pytest.approx(0.975, abs=1e-3), "a coin with no spot pair, once aged, is L15's only"
    monkeypatch.setattr(sl, "BASIS_NOT_A_TAIL", 0.1)
    assert sl.basis_counts(_basis_world(), BASIS_SPAN)["not_a_tail"] is True, "a book holding this much of the pool is labelled"


def test_the_pair_run_is_read_through_the_ladder_beside_other_coins_pairs_and_its_reading_is_one_of_three(monkeypatch):
    monkeypatch.setattr(sl, "BASIS_MIN_ACTIVE", 0)
    monkeypatch.setattr(sl, "DEEP_MIN_FUNDING_KNOWN", 0.5)
    monkeypatch.setattr(sl, "SPAN", BASIS_SPAN)
    run = sl.basis_run(_basis_world(), BASIS_SPAN, placebos=3)
    assert (run["hypothesis"], run["logic"]) == ("H-B1", "L20")
    assert run["legs"]["price_pct"] == pytest.approx(0.0, abs=0.01), "each spot closes on its perp: nothing from price"
    assert run["legs"]["funding_pct"] > 0 and run["funding_under_cost"] is False and run["price_loss_over_funding"] is False
    assert run["sum_pct"] == pytest.approx(run["legs"]["funding_pct"] - run["legs"]["cost_pct"], abs=0.02)
    assert set(run["rungs"]) == {"2a_entry", "2b_later_years", "2c_beside_placebos", "3_stability"}
    assert run["reading"] in ("closed", "passes_on_carry", "passes_on_price") and run["passes"] is (run["reading"] != "closed")
    assert run["liquidations"]["count"] == 0 and run["out_of_band"]["count"] == 0 and run["legs_ended"] == {"perp": 0, "spot": 0}
    assert run["placebo_net_total_position_annual_pct_mean"] < run["total_position_annual_pct"], \
        "the other coins' pairs are paid the small rates"
    assert run["at_engine_cost"]["total_position_annual_pct"] > run["total_position_annual_pct"]
    assert set(run["years_total_position_pct"]) == {"2020", "2021"}


def test_the_basis_commands_hang_the_spot_bars_and_nothing_after_the_development_span_reaches_the_book(monkeypatch, capsys, tmp_path):
    late = date(2025, 1, 2)
    assert sl.SPAN[1] < date(2025, 1, 1)

    def archive(path, funding=True):
        bar = xsb.Bar(open=1.0, close=1.0, quote_volume=1.0)
        stamp = int(datetime(late.year, late.month, late.day, 8, tzinfo=timezone.utc).timestamp() * 1000)
        return xsb.Market(bars={"AUSDT": {date(2024, 12, 30): bar, late: bar}}, funding={"AUSDT": [(stamp, 0.01)]} if funding else {})

    folder = tmp_path / sl.lab.sp.SPOT_DIR
    folder.mkdir()
    (folder / sl.lab.ba.SYMBOLS_FILENAME).write_text(json.dumps(["AUSDT"]), encoding="utf-8")
    seen = []
    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(sl.xsb, "read_contract_types", lambda path: ({}, "snapshot"))
    monkeypatch.setattr(sl.xsb, "load_market", archive)
    monkeypatch.setattr(sl, "basis_counts", lambda market: seen.append(market) or {"stop": []})
    monkeypatch.setattr(sl, "basis_run", lambda market: seen.append(market) or {})
    assert sl.main(["basis-counts", "--cache", str(tmp_path)]) == sl.EXIT_OK
    assert sl.main(["basis", "--cache", str(tmp_path)]) == sl.EXIT_OK
    assert len(seen) == 2
    for market in seen:
        assert set(market.bars["AUSDT"]) == {date(2024, 12, 30)} and set(market.spot["AUSDT"]) == {date(2024, 12, 30)}
        assert not (market.funding_day("AUSDT") or {}), "the 2025 settlement is cut too"
        assert market.spot_of["AUSDT"] == ("AUSDT", 1.0)
    capsys.readouterr()


# --- round 17: M04's score on the next tier --------------------------------------------------------------------------

WIDE_TEST_SPAN = (date(2022, 1, 1), date(2022, 12, 31))
WIDE_DAY = date(2022, 3, 6)                      # a Sunday decision
MIDS = [f"MID{i:02d}USDT" for i in range(12)]


def _wide_world(hole: bool = False):
    """Thirty large coins and twelve smaller ones from mid-2021. MID00 has the slowest open-interest growth and the
    lowest long share and rises fastest; each next one grows faster, is more crowded and does worse. MID11 has no
    positioning in February 2022 and the archive is known not to serve those days. ``hole`` also removes two of
    MID10's days that the archive was never asked for."""
    start, end = date(2021, 6, 1), date(2022, 12, 31)
    days = [start + timedelta(days=k) for k in range((end - start).days + 1)]

    def bars(move, volume):
        out, price = {}, 100.0
        for day in days:
            out[day] = xsb.Bar(open=price, close=price * (1 + move), quote_volume=volume)
            price *= 1 + move
        return out

    names = {f"BIG{i:02d}USDT": bars(0.0, 1e9 - i) for i in range(30)}
    names.update({s: bars((5.5 - i) * 0.0004, 1e7 - i * 1e4) for i, s in enumerate(MIDS)})
    market = xsb.Market(bars=names, funding={s: [] for s in names})
    market.metrics = {s: {day: {"oi": 1000.0 * (1 + 0.001 * (i + 1)) ** k, "ls": 0.40 + 0.02 * i, "top": None, "taker": None}
                          for k, day in enumerate(days)} for i, s in enumerate(MIDS)}
    february = [date(2022, 2, 1) + timedelta(days=k) for k in range(28)]
    for day in february:
        market.metrics["MID11USDT"].pop(day)
    market.metrics_unserved = {"MID11USDT": set(february)}
    if hole:
        for day in (date(2022, 6, 1), date(2022, 6, 2)):
            market.metrics["MID10USDT"].pop(day)
    return market


def test_the_wide_book_is_m04s_own_score_and_basket_rule_on_the_coins_outside_the_months_top_thirty():
    ms = sl.lab.ms
    market = _wide_world()
    tier = sl.WideTier(market, WIDE_TEST_SPAN)
    members = tier.members(WIDE_DAY)
    assert set(members) == set(MIDS), "ranks 31 on: none of the month's top thirty"
    assert tier.candidate is ms.CANDIDATES["M04"] and tier.candidate.as_dict()["features"] == [["oi_growth", 1.0], ["ls_level", 1.0]]
    weights = tier.book()(market, ("BIG00USDT", "BIG01USDT"), WIDE_DAY, "x")
    assert weights == sl.bs.weigher(ms.CANDIDATES["M04"], ms.FEATURES)(market, members, WIDE_DAY, "x"), \
        "M04's weigher on these members, whatever members the caller hands in"
    assert weights == {"MID00USDT": 0.5, "MID01USDT": 0.5, "MID10USDT": -0.5, "MID09USDT": -0.5}, \
        "the least crowded fifth bought, the most crowded sold; MID11 has no score in the window"
    assert "MID11USDT" not in tier.ranked(WIDE_DAY) and len(tier.ranked(WIDE_DAY)) == 11
    assert tier.why_unranked("MID11USDT", WIDE_DAY) == sl.UNSERVED
    assert list(ms.CANDIDATES) == ["M01", "M02", "M03", "M04"] and ms.FINAL == "M04" and ms.BUDGET == 5, \
        "M04's own protocol is as it was: nothing is added to its attempts"


def test_a_wide_null_book_has_the_books_shape_ranks_the_same_coins_and_changes_as_slowly_as_a_trailing_score():
    market = _wide_world()
    tier = sl.WideTier(market, WIDE_TEST_SPAN)
    null = tier.null("wide:0")
    weights = null(market, (), WIDE_DAY, "x")
    assert sorted(weights.values()) == [-0.5, -0.5, 0.5, 0.5] and set(weights) <= tier.ranked(WIDE_DAY)
    assert sl.WideTier(_wide_world(), WIDE_TEST_SPAN).null("wide:0")(market, (), WIDE_DAY, "x") == weights, "a seed is a book"
    picks = {k: tuple(sorted(tier.null(f"wide:{k}")(market, (), WIDE_DAY, "x").items())) for k in range(6)}
    assert len(set(picks.values())) > 1, "another seed is another book"
    held = {s for k in range(30) for s in tier.null(f"wide:{k}")(market, (), WIDE_DAY, "x")}
    assert held == tier.ranked(WIDE_DAY), "thirty null books between them hold every ranked coin and never the unranked one"
    cand = sl.bs.Candidate(why="redrawn", features=tier.candidate.features, long_low=True)
    inner = sl.bs.weigher(cand, sl.lab.ms.FEATURES)

    def redrawn(seed):
        return lambda market, members, day, signal, seed_=seed: inner(market, tier.members(day), day, signal, seed=seed_)

    slow = [tier.turnover(tier.null(f"wide:{k}")) for k in range(5)]
    fast = [tier.turnover(redrawn(f"r{k}")) for k in range(5)]
    assert sum(slow) < 0.6 * sum(fast), "a score redrawn every week trades far more than one averaged over thirty days"
    assert tier.turnover(tier.book()) < min(slow), "this world's real score never changes its order"


def test_the_wide_counts_name_why_a_member_is_unranked_carry_nothing_earned_and_stop_on_an_unexplained_gap(monkeypatch):
    counts = sl.wide_counts(_wide_world(), WIDE_TEST_SPAN)
    year = counts["by_year"]["2022"]
    assert counts["stop"] == [] and (counts["tier"], counts["score"]) == ("rank31_100", "M04")
    assert year["members_median"] == 12 and year["side_median"] == 2 and year["active_decisions"] == year["decisions"]
    assert set(year["unranked"]) == {sl.UNSERVED} and year["ranked_share"] > 0.9
    turnover = counts["turnover"]
    assert turnover["null_books"] == sl.WIDE_TURNOVER_NULLS and turnover["book"] < turnover["null_books_mean"]
    assert turnover["alike"] is False, "this book never trades: its p would say so, not that it picks better"
    flat = json.dumps(counts)
    assert counts["returns_computed"] is False and "sharpe" not in flat and "pct" not in flat
    holed = sl.wide_counts(_wide_world(hole=True), WIDE_TEST_SPAN)
    assert "MID10USDT" in sl.WideTier(_wide_world(hole=True), WIDE_TEST_SPAN).ranked(date(2022, 6, 5)), \
        "the score tolerates two missing days: the coin is still ranked"
    assert holed["by_year"]["2022"]["days_read_without_a_file_or_a_cause"] > 0 and year["days_read_without_a_file_or_a_cause"] == 0
    assert holed["stop"][0].endswith("have no file and no named cause: fetch or build is incomplete"), \
        "a hole in a ranked member's window stops the book too"
    assert sl.wide_run(_wide_world(hole=True), WIDE_TEST_SPAN)["refused"] == holed["stop"], "a stopped book is not measured"
    monkeypatch.setattr(sl, "WIDE_MIN_ACTIVE", 60)
    monkeypatch.setattr(sl, "WIDE_MIN_RANKED", 0.99)
    assert sl.wide_counts(_wide_world(), WIDE_TEST_SPAN)["stop"] == [
        "the score ranks too few of the tier's coins in 2022", "the book stands at too few decisions in 2022"]


def test_the_wide_run_is_judged_held_at_stressed_cost_and_prints_m04s_own_accounting_beside_it(monkeypatch):
    monkeypatch.setattr(sl, "SPAN", WIDE_TEST_SPAN)
    run = sl.wide_run(_wide_world(), WIDE_TEST_SPAN, nulls=3)
    assert (run["hypothesis"], run["logic"]) == ("H-M1", "L21")
    assert run["sum_pct"] > 0 and run["legs"]["price_pct"] > 0 and run["funding_carried_it"] is False, \
        "the uncrowded coins rise and the crowded ones fall"
    assert set(run["rungs"]) == {"2a_entry", "2b_later_years", "2c_beside_null_books", "3_stability"}
    assert run["rungs"]["2c_beside_null_books"]["null_books"] == 3 and run["null_net_total_position_annual_pct_mean"] < run["total_position_annual_pct"]
    assert run["passes"] is False and run["reading"] == "closed", "one year of three: the later years are not there"
    theirs = run["as_m04_was_measured"]
    assert set(theirs) == {"annual_sharpe", "total_position_annual_pct", "years_total_position_pct", "cost_pct", "funding_pct"}
    assert theirs["cost_pct"] < run["cost_pct"], "the engine's cost is under the stressed one"
    tier = sl.WideTier(_wide_world(), WIDE_TEST_SPAN)
    at_engine = sl.measure(tier.market, tier.book(), WIDE_TEST_SPAN, simulate=sl.simulate_held)["cost_pct"]
    assert run["cost_pct"] == pytest.approx(at_engine * 26 / 8, abs=0.02) and at_engine > 0, "judged at 5 + 21 bps on every coin"
    assert set(run["held_at_engine_cost"]) == {"annual_sharpe", "total_position_annual_pct"}
    assert set(run["years_total_position_pct"]) == {"2022"}
    monkeypatch.setattr(sl, "later_years", lambda years: {"passes": True})
    monkeypatch.setattr(sl, "stability", lambda years: {"passes": True})
    monkeypatch.setattr(sl, "beside_nulls", lambda value, nulls, **more: {"passes": True})
    monkeypatch.setattr(sl.bs, "clears_entry", lambda report: True)
    assert sl.wide_run(_wide_world(), WIDE_TEST_SPAN, nulls=1)["reading"] == "passes_beside_null_books_that_trade_differently", \
        "a pass whose null books trade more than the book is labelled"


def test_the_wide_commands_hang_the_positioning_values_and_nothing_after_their_span_reaches_the_book(monkeypatch, capsys, tmp_path):
    assert sl.WIDE_SPAN == (date(2022, 1, 1), date(2024, 12, 31))
    late = date(2025, 1, 2)
    bar = xsb.Bar(open=1.0, close=1.0, quote_volume=1.0)
    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(sl.xsb, "read_contract_types", lambda path: ({}, "snapshot"))
    monkeypatch.setattr(sl.xsb, "load_market", lambda path, funding=True: xsb.Market(bars={"AUSDT": {date(2024, 12, 30): bar, late: bar}}))
    seen = []
    monkeypatch.setattr(sl, "wide_counts", lambda market: seen.append(market) or {"stop": []})
    monkeypatch.setattr(sl, "wide_run", lambda market: seen.append(market) or {})
    assert sl.main(["wide-counts", "--cache", str(tmp_path)]) == sl.EXIT_BLOCKED and seen == [], "no derived values: blocked"
    folder = tmp_path / "derived" / "metrics-daily"
    folder.mkdir(parents=True)
    (folder / "AUSDT.jsonl").write_text('["2024-12-30", 5.0, 0.5, null, null]\n["2025-01-02", 6.0, 0.6, null, null]\n', encoding="utf-8")
    (tmp_path / "archive-metrics").mkdir()
    (tmp_path / "archive-metrics" / sl.lab.ba.MISSING_FILENAME).write_text(json.dumps(
        {"AUSDT": ["AUSDT-metrics-2024-12-29.zip", "AUSDT-metrics-2025-01-03.zip", "not-a-day.zip"]}), encoding="utf-8")
    assert sl.main(["wide-counts", "--cache", str(tmp_path)]) == sl.EXIT_OK and sl.main(["wide", "--cache", str(tmp_path)]) == sl.EXIT_OK
    assert len(seen) == 2
    for market in seen:
        assert set(market.bars["AUSDT"]) == {date(2024, 12, 30)}
        assert market.metrics["AUSDT"] == {date(2024, 12, 30): {"oi": 5.0, "ls": 0.5, "top": None, "taker": None}}
        assert market.metrics_unserved == {"AUSDT": {date(2024, 12, 29)}}
    assert '"span": [\n  "2022-01-01",\n  "2024-12-31"\n ]' in capsys.readouterr().out


def test_the_value_printed_as_m04_was_measured_is_what_m04s_own_protocol_gives_on_the_tiers_members(monkeypatch):
    ms = sl.lab.ms
    market = _wide_world()
    tier = sl.WideTier(market, WIDE_TEST_SPAN)
    printed = sl.wide_run(market, WIDE_TEST_SPAN, nulls=1)["as_m04_was_measured"]      # beside the verdict
    ours = sl.measure(market, tier.book(), WIDE_TEST_SPAN)
    monkeypatch.setattr(sl.bs.xsb, "universes", lambda market, **kw: dict(tier.monthly))   # M04's path, on the tier
    theirs = sl.bs.measure(market, ms.CANDIDATES["M04"], WIDE_TEST_SPAN, ms.FEATURES)
    for key in ("annual_sharpe", "sum_pct", "cost_pct", "funding_pct", "months"):
        assert ours[key] == theirs[key], key
    for key in ("annual_sharpe", "cost_pct", "funding_pct"):
        assert printed[key] == theirs[key], key
    assert printed["total_position_annual_pct"] == sl.bs.total_position_annual_pct(theirs["sum_pct"], theirs["months"])
    assert ours["sum_pct"] != 0 and theirs["weeks"] > 40


# --- round 34: the month's reversal on the next tier (H-R2) -----------------------------------------------------------

MID_TEST_SPAN = (date(2022, 1, 1), date(2022, 12, 31))
MID_DAY = date(2022, 6, 5)                       # a Sunday decision


def _mid_world(*, funded: bool = True):
    """Thirty large flat coins and sixteen smaller ones from mid-2021. Each smaller coin swings on its own 60-day
    cycle, offset from the others, so its 30-day return keeps changing sign and rank; a coin that fell over the
    last month rises over the next."""
    start, end = date(2021, 6, 1), date(2022, 12, 31)
    days = [start + timedelta(days=k) for k in range((end - start).days + 1)]
    mids = [f"MID{i:02d}USDT" for i in range(16)]

    def swing(i):
        out, price = {}, 100.0
        for k, day in enumerate(days):
            up = ((k + 4 * i) // 30) % 2 == 0
            move = (0.004 if up else -0.004) * (1 + i / 40)
            out[day] = xsb.Bar(open=price, close=price * (1 + move), quote_volume=1e7 - i * 1e4)
            price *= 1 + move
        return out

    names = {f"BIG{i:02d}USDT": {d: xsb.Bar(open=100.0, close=100.0, quote_volume=1e9 - i) for d in days} for i in range(30)}
    names.update({s: swing(i) for i, s in enumerate(mids)})
    row = [(int((start - date(1970, 1, 1)).total_seconds() * 1000) + 3600 * 1000, 0.0)]     # on file, and nothing paid
    funding = {s: list(row) for s in names if funded or not s.startswith("MID0")}
    return xsb.Market(bars=names, funding=funding), mids


def test_the_mid_book_stands_on_the_maps_own_tier_and_variable_and_buys_what_fell():
    market, mids = _mid_world()
    tier = sl.MidTier(market, MID_TEST_SPAN)
    assert set(tier.members(MID_DAY)) == set(mids) == set(sl.lab.tier_members(market, MID_TEST_SPAN)["rank31_100"][date(2022, 6, 1)])
    mapped = sl.lab.variables()["momentum_30"][1]
    assert (sl.MID_TIER, sl.MID_VARIABLE) == ("rank31_100", "momentum_30")
    assert all(tier.read(market, s, MID_DAY) == mapped(market, s, MID_DAY) == sl.xsb.trailing_return(market, s, MID_DAY, 30)
               for s in mids), "the mapped variable itself: thirty days to the decision day's close, no day skipped"
    assert tier.ranked(MID_DAY) == frozenset(mids), "nothing is filtered: the coins the cell was read on"
    weights = tier.book()(market, (), MID_DAY, "x")
    month = {s: sl.xsb.trailing_return(market, s, MID_DAY, 30) for s in mids}
    longs, shorts = [s for s, w in weights.items() if w > 0], [s for s, w in weights.items() if w < 0]
    assert max(month[s] for s in longs) < min(month[s] for s in shorts), "long what fell over the month, short what rose"
    assert sum(w for w in weights.values() if w > 0) == pytest.approx(1.0) and sum(weights.values()) == pytest.approx(0.0)
    assert not set(weights) & {f"BIG{i:02d}USDT" for i in range(30)}, "no coin of the month's top thirty"
    assert weights == sl.book(tier.read, lambda _d: -1)(market, list(mids), MID_DAY, "x"), "L1's rank weights, nothing else"


def test_a_mid_null_book_ranks_the_same_coins_and_changes_as_slowly_as_a_months_return():
    market, mids = _mid_world()
    tier = sl.MidTier(market, MID_TEST_SPAN)
    null = tier.null("mid:0")
    weights = null(market, (), MID_DAY, "x")
    assert set(weights) <= tier.ranked(MID_DAY) and sum(weights.values()) == pytest.approx(0.0)
    assert sl.MidTier(_mid_world()[0], MID_TEST_SPAN).null("mid:0")(market, (), MID_DAY, "x") == weights, "a seed is a book"
    assert tier.null("mid:1")(market, (), MID_DAY, "x") != weights, "another seed is another book"
    later = sl.MidTier(market, MID_TEST_SPAN).null("mid:0")
    later(market, (), MID_DAY + timedelta(days=70), "x")
    assert later(market, (), MID_DAY, "x") == weights, "a value does not depend on which day was asked first"
    weekly = tier._weigher(lambda m, s, d: sl.lab._control("week", 7)(m, s, d))
    slow = [tier.turnover(tier.null(f"mid:{k}")) for k in range(5)]
    assert max(slow) < 0.7 * tier.turnover(weekly), "noise summed over thirty days trades far less than noise redrawn weekly"


def test_the_mid_counts_carry_nothing_earned_and_stop_on_missing_funding_or_a_thin_year(monkeypatch):
    market, mids = _mid_world()

    def never(*args, **kwargs):
        raise AssertionError("the counts read no return")
    for name in ("measure", "simulate_held"):
        monkeypatch.setattr(sl, name, never)
    monkeypatch.setattr(sl, "LATER_YEARS", (2022,))
    counts = sl.mid_counts(market, MID_TEST_SPAN)
    year = counts["by_year"]["2022"]
    assert counts["stop"] == [] and (counts["tier"], counts["variable"]) == ("rank31_100", "momentum_30")
    assert year["members_median"] == 16 == year["ranked_median"] and year["active_decisions"] == year["decisions"] >= 50
    assert counts["funding_known_share"] == 1.0 and counts["returns_computed"] is False
    turnover = counts["turnover"]
    assert turnover["null_books"] == sl.MID_TURNOVER_NULLS and 0 < turnover["book"] < 2 and turnover["ratio"] == pytest.approx(
        turnover["book"] / turnover["null_books_mean"], abs=2e-3)
    assert turnover["alike"] == (0.8 <= turnover["ratio"] <= 1.25)
    assert counts["cost_from_turnover_total_position_annual_pct"] == pytest.approx(turnover["book"] * 2 * 0.0026 * 52 / 2 * 100, abs=0.01)
    flat = json.dumps(counts)
    assert "sharpe" not in flat and "sum_pct" not in flat
    unfunded = sl.mid_counts(_mid_world(funded=False)[0], MID_TEST_SPAN)
    assert unfunded["funding_known_share"] == pytest.approx(6 / 16) and unfunded["stop"] == ["too many of the ranked coins have no funding on file"]
    monkeypatch.setattr(sl, "MID_MIN_ACTIVE", 60)
    assert sl.mid_counts(market, MID_TEST_SPAN)["stop"] == ["the book stands at too few decisions in 2022"]


def test_the_mid_run_is_judged_held_at_stressed_cost_beside_slow_null_books(monkeypatch, capsys):
    monkeypatch.setattr(sl, "LATER_YEARS", (2022,))
    market, mids = _mid_world()
    run = sl.mid_run(market, MID_TEST_SPAN, nulls=4)
    assert (run["hypothesis"], run["logic"]) == ("H-R2", "L22") and "refused" not in run
    assert run["legs"]["price_pct"] > 0, "in this world a coin that fell over the month rises over the next"
    assert set(run["rungs"]) == {"2a_entry", "2b_later_years", "2c_beside_null_books", "3_stability"}
    assert run["rungs"]["2c_beside_null_books"]["null_books"] == 4
    tier = sl.MidTier(market, MID_TEST_SPAN)
    at_engine = sl.measure(market, tier.book(), MID_TEST_SPAN, simulate=sl.simulate_held)
    assert run["cost_pct"] == pytest.approx(at_engine["cost_pct"] * 26 / 8, abs=0.05) and at_engine["cost_pct"] > 0, \
        "judged at 5 + 21 bps on every coin"
    assert run["held_at_engine_cost"]["annual_sharpe"] == at_engine["annual_sharpe"]
    assert set(run["years_total_position_pct"]) == {"2022"}
    assert sl.mid_run(_mid_world(funded=False)[0], MID_TEST_SPAN)["refused"] == ["too many of the ranked coins have no funding on file"]
    # The reading table: a ladder pass beside null books that trade alike, one beside null books that do not, a book
    # that only clears at the engine's cost, and everything else.
    alike = {"alike": True}
    counts = sl.mid_counts(market, MID_TEST_SPAN)
    monkeypatch.setattr(sl, "later_years", lambda years: {"passes": True})
    monkeypatch.setattr(sl, "stability", lambda years: {"passes": True})
    monkeypatch.setattr(sl, "beside_nulls", lambda value, nulls, **more: {"passes": True})
    monkeypatch.setattr(sl.bs, "clears_entry", lambda report: True)
    monkeypatch.setattr(sl, "mid_counts", lambda market, span, tier=None: {**counts, "turnover": {**counts["turnover"], **alike}})
    assert sl.mid_run(market, MID_TEST_SPAN, nulls=1)["reading"] == "passes"
    alike["alike"] = False
    assert sl.mid_run(market, MID_TEST_SPAN, nulls=1)["reading"] == "passes_beside_null_books_that_trade_differently"
    stressed_fails = iter([False, True] * 10)
    monkeypatch.setattr(sl.bs, "clears_entry", lambda report: next(stressed_fails))
    assert sl.mid_run(market, MID_TEST_SPAN, nulls=0)["reading"] == "cost_dependent_lead", "clears only at the engine's cost"
    stressed_fails = iter([False, True] * 10)
    monkeypatch.setattr(sl, "beside_nulls", lambda value, nulls, **more: {"passes": False})
    assert sl.mid_run(market, MID_TEST_SPAN, nulls=0)["reading"] == "closed", "clearing at the engine's cost is no lead without the p"
    monkeypatch.setattr(sl, "beside_nulls", lambda value, nulls, **more: {"passes": True})
    monkeypatch.setattr(sl.bs, "clears_entry", lambda report: False)
    assert sl.mid_run(market, MID_TEST_SPAN, nulls=1)["reading"] == "closed"
    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(sl.lab, "hold", lambda cache, span=None, *, attach=True, flow=False: None)
    assert sl.main(["mid-counts"]) == sl.EXIT_BLOCKED and sl.main(["mid"]) == sl.EXIT_BLOCKED


# --- round 36: the sweep on the map's second tier ----------------------------------------------------------------------

def _late(read, first: date):
    return lambda market, symbol, day: None if day < first else read(market, symbol, day)


def test_the_tier_sweep_builds_no_book_the_record_forbids_and_names_why():
    names = sl.tier_sweep_names()
    registered = sl.lab.variables()
    assert set(names) | set(sl.TIER_SWEEP_OUT) == set(registered) and not set(names) & set(sl.TIER_SWEEP_OUT)
    assert {"vol_30", "idio_vol_30", "max_30", "skew_30", "range_30", "beta_30", "extreme_days_30"} <= set(sl.TIER_SWEEP_OUT)
    assert {"oi_growth_30", "ls_level_30", "momentum_30", "momentum_7", "momentum_90"} <= set(sl.TIER_SWEEP_OUT)
    assert {"spread_change_30", "trade_size_position_30"} <= set(sl.TIER_SWEEP_OUT), "the top tier's lead and its runner-up"
    assert {"momentum_change_30", "momentum_position_30", "residual_momentum_30", "ls_change_30"} <= set(names), "shapes are kin, not the closed book"
    assert not [n for n, (group, _) in names.items() if group == "depth"], "no depth files on this tier"
    assert all(sl.TIER_SWEEP_OUT.values()) and len(names) == len(registered) - len(sl.TIER_SWEEP_OUT) == 38
    assert sl.TIER_SWEEP_NULLS == 600 and 0.10 / len(names) > 1 / (sl.TIER_SWEEP_NULLS + 1), "a book above every null can survive"


def test_the_tier_book_is_h_r2s_book_for_any_variable_and_the_gate_reads_h_r2s_numbers():
    market, mids = _mid_world()
    tier, mid = sl.TierSweep(market, MID_TEST_SPAN), sl.MidTier(market, MID_TEST_SPAN)
    read = sl.lab.variables()["momentum_30"][1]
    assert sl.TIER_SWEEP_TIER == sl.MID_TIER and set(tier.members(MID_DAY)) == set(mids)
    assert tier.weigher(read, -1)(market, (), MID_DAY, "x") == mid.book()(market, (), MID_DAY, "x")
    high = tier.weigher(read, 1)(market, (), MID_DAY, "x")
    assert high == {s: -w for s, w in mid.book()(market, (), MID_DAY, "x").items()}, "the other side is the same book reversed"
    stressed = sl.functools.partial(sl.simulate_held, unit_of=sl.listing_unit)
    record = sl.measure(market, mid.book(), MID_TEST_SPAN, simulate=stressed)        # how H-R2 was read
    gate = sl.tier_sweep_gate(market, MID_TEST_SPAN)
    for key in ("annual_sharpe", "sum_pct", "months", "total_position_annual_pct", "clears_entry", "cost_pct", "funding_pct"):
        assert gate[key] == record[key], key
    assert gate["years_total_position_pct"] == record["years_total_position_pct"] and gate["sum_pct"] != 0
    assert set(sl.TIER_GATE_KEYS) <= set(gate)


def test_a_tier_null_is_one_fixed_draw_per_coin_and_trades_less_than_a_months_return():
    market, mids = _mid_world()
    tier = sl.TierSweep(market, MID_TEST_SPAN)
    fixed = tier.fixed("tier:0")
    values = {s: fixed(market, s, MID_DAY) for s in mids}
    assert len(set(values.values())) == len(mids) and all(v is not None for v in values.values())
    assert {s: fixed(market, s, MID_DAY + timedelta(days=77)) for s in mids} == values, "it never changes"
    assert fixed(market, mids[0], date(2021, 1, 1)) is None, "no value where the coin has no bar"
    other = tier.fixed("tier:1")
    assert {s: other(market, s, MID_DAY) for s in mids} != values, "another seed is another variable"
    again = sl.TierSweep(_mid_world()[0], MID_TEST_SPAN).fixed("tier:0")
    assert {s: again(market, s, MID_DAY) for s in mids} == values, "a seed is a book"
    assert {s: fixed(market, s, MID_DAY) for s in mids} == values, "asking for another seed in between changes nothing"
    assert tier.turnover(fixed) == 0.0 < tier.turnover(sl.lab.variables()["momentum_30"][1]), "members do not change in this world"


def test_a_tier_book_is_read_on_its_own_span_and_a_short_one_is_named_and_not_built(monkeypatch):
    market, mids = _mid_world()
    tier = sl.TierSweep(market, MID_TEST_SPAN)
    read = sl.lab.variables()["momentum_30"][1]
    assert tier.first_decision(read) == date(2022, 1, 2), "the span's first weekly decision"
    assert tier.start(read) == date(2022, 2, 1) and len(tier.months(tier.start(read))) == 11, "the month of the entry is not read"
    late = _late(read, date(2022, 6, 10))
    assert tier.first_decision(late) == date(2022, 6, 12) and tier.start(late) == date(2022, 7, 1)
    assert tier.months(tier.start(late)) == [f"2022-{m:02d}" for m in range(7, 13)]
    assert tier.start(_late(read, date(2022, 12, 20))) == date(2023, 1, 1), "December rolls into the next year"
    assert tier.start(lambda m, s, d: None) is None and tier.months(None) == []
    names = {"whole": ("g", read), "late": ("g", late), "never": ("g", lambda m, s, d: None)}
    monkeypatch.setattr(sl, "TIER_SWEEP_MIN_MONTHS", 8)
    books = sl.tier_sweep(market, names, MID_TEST_SPAN)
    assert books["late"] == {"group": "g", "skipped": "its own span is 6 complete months, under 8"}
    assert books["never"]["skipped"].startswith("its own span is 0 complete months")
    assert books["whole"]["months"] == 11 and books["whole"]["start"] == "2022-02-01"
    monkeypatch.setattr(sl, "TIER_SWEEP_MIN_MONTHS", 6)
    monkeypatch.setattr(sl.lab, "MIN_DECISIONS", 10)
    late_book = sl.tier_sweep(market, names, MID_TEST_SPAN)["late"]
    assert (late_book["start"], late_book["months"]) == ("2022-07-01", 6) and set(late_book["years_total_position_pct"]) == {"2022"}
    whole = tier.monthly_rows(late, -1 if late_book["long"] == "low" else 1)
    assert late_book["sum_pct"] == pytest.approx(sum(whole[m]["net"] for m in tier.months(date(2022, 7, 1))) * 100, abs=0.01)
    assert whole["2022-06"]["cost"] > 0, "the entry is paid in the month before its own span and is not read"
    monkeypatch.setattr(sl.lab, "MIN_DECISIONS", 40)
    assert sl.tier_sweep(market, names, MID_TEST_SPAN)["late"]["skipped"] == "ranked at too few decisions"


def test_a_tier_book_is_long_the_side_its_own_rank_correlation_names_on_its_own_span(monkeypatch):
    monkeypatch.setattr(sl, "TIER_SWEEP_MIN_MONTHS", 8)
    market, mids = _mid_world()
    read = sl.lab.variables()["momentum_30"][1]
    names = {"fell_rises": ("g", read), "negated": ("g", lambda m, s, d: None if (v := read(m, s, d)) is None else -v)}
    books = sl.tier_sweep(market, names, MID_TEST_SPAN)
    assert books["fell_rises"]["long"] == "low" and books["fell_rises"]["mean_rank_correlation"] < 0
    assert books["negated"]["long"] == "high" and books["negated"]["mean_rank_correlation"] == -books["fell_rises"]["mean_rank_correlation"]
    for key in ("annual_sharpe", "sum_pct", "cost_pct", "funding_pct", "price_pct", "total_position_annual_pct"):
        assert books["negated"][key] == pytest.approx(books["fell_rises"][key], abs=0.02), key
    book = books["fell_rises"]
    assert book["price_pct"] > 0 and book["cost_pct"] > 0
    assert book["sum_pct"] == pytest.approx(book["price_pct"] + book["funding_pct"] - book["cost_pct"], abs=0.02)
    tier = sl.TierSweep(market, MID_TEST_SPAN)
    rows, months = tier.monthly_rows(read, -1), [f"2022-{m:02d}" for m in range(2, 13)]
    assert book["months"] == 11 and book["decisions_ranked"] == sum(1 for d in tier.ics(read) if d >= date(2022, 2, 1))
    assert book["annual_sharpe"] == sl.tier_reading(rows, months)["annual_sharpe"] is not None
    assert book["cost_pct"] == pytest.approx(sum(rows[m]["cost"] for m in months) * 100, abs=0.01), "every coin at 5 + 21 bps, its own months"
    assert book["total_position_annual_pct"] == sl.bs.total_position_annual_pct(book["sum_pct"], 11)
    assert book["clears_entry"] == sl.bs.clears_entry(book)
    assert sl.tier_side({date(2022, 1, 2): 0.5, date(2022, 6, 5): -0.1, date(2022, 6, 12): -0.1}, date(2022, 6, 1)) == (-1, pytest.approx(-0.1))
    assert sl.tier_side({date(2022, 1, 2): 0.5, date(2022, 6, 5): -0.1}, date(2022, 1, 1))[0] == 1
    assert sl.tier_side({date(2022, 1, 2): 0.5}, date(2022, 6, 1)) == (-1, None)


def test_the_tier_counts_carry_nothing_earned_and_show_ties_dated_names_dead_bars_and_missing_funding(monkeypatch):
    market, mids = _mid_world(funded=False)
    dated, dead = "MID15USDT_221230", mids[14]
    market.bars[dated] = market.bars.pop(mids[15])
    for day in list(market.bars[dead]):
        if day >= date(2022, 6, 1):
            bar = market.bars[dead][day]
            market.bars[dead][day] = xsb.Bar(open=bar.open, close=bar.close, quote_volume=0.0 if day >= date(2022, 7, 1) else bar.quote_volume)

    def never(*args, **kwargs):
        raise AssertionError("the counts read no return")
    for name in ("measure", "simulate_held"):
        monkeypatch.setattr(sl, name, never)
    monkeypatch.setattr(sl.lab, "forward_return", never)
    read = sl.lab.variables()["momentum_30"][1]
    half = lambda m, s, d: 0.0 if s in mids[:8] else read(m, s, d)                     # noqa: E731
    monkeypatch.setattr(sl, "TIER_SWEEP_MIN_MONTHS", 8)
    counts = sl.tier_sweep_counts(market, {"month": ("g", read), "half_tied": ("g", half),
                                           "late": ("g", _late(read, date(2022, 6, 10)))}, MID_TEST_SPAN)
    assert counts["returns_computed"] is False and counts["tier"] == "rank31_100" and counts["null_turnover_books"] == sl.TIER_SWEEP_TURNOVER_NULLS
    month, tied, late = (counts["books"][k] for k in ("month", "half_tied", "late"))
    assert (month["first_decision"], month["start"], month["months"]) == ("2022-01-02", "2022-02-01", 11) and "skipped" not in month
    assert month["ranked_median"] == 16 and month["ranked_cells"] == 16 * month["decisions_with_a_book"]
    assert month["tied_share"] == 0.0 and tied["tied_share"] == 0.5, "eight of sixteen stand at one value"
    assert month["dated_cells"] == month["decisions_with_a_book"], "the dated contract is ranked at every decision"
    assert 0 < month["zero_volume_cells"] < month["decisions_with_a_book"], "a bar with no volume, from July"
    assert month["unfunded_share"] == pytest.approx(11 / 16, abs=0.001), "ten without a file here, and the dated contract has none"
    assert month["turnover"] > 0 and counts["null_turnover_mean"] is not None
    assert month["ranked_beside_null"] == 1.0 and month["months_without_a_book"] == 0
    thin = lambda m, s, d: None if s in mids[:4] or (d.month == 9 and d.year == 2022) else read(m, s, d)   # noqa: E731
    gaps = sl.tier_sweep_counts(market, {"thin": ("g", thin)}, MID_TEST_SPAN)["books"]["thin"]
    assert gaps["ranked_beside_null"] == 0.75, "twelve of the sixteen a null book ranks"
    assert gaps["months_without_a_book"] == 1, "September stands inside its span with no book"
    assert late["skipped"] == "its own span is 6 complete months, under 8" and late["months"] == 6
    flat = json.dumps(counts)
    assert "sharpe" not in flat and "sum_pct" not in flat and "rank_correlation" not in flat


def test_the_tier_null_books_are_kept_whole_and_cut_to_each_books_own_months():
    market, mids = _mid_world()
    null = sl.tier_sweep_null(market, MID_TEST_SPAN, nulls=3)
    assert set(null) == {"tier:0", "tier:1", "tier:2"}
    body = null["tier:0"]
    assert set(body) == {"ics", "high", "low"} and len(body["high"]) == 12 == len(body["low"]) and len(body["ics"]) > 40
    assert all(body["high"][m] < 0 or body["low"][m] < 0 for m in body["high"]), "the two sides are one book reversed, both pay costs"
    # the family reading, on made-up books: a null whose better side changes with the span it is cut to
    months = [f"2022-{m:02d}" for m in range(1, 13)]
    wave = {m: (0.02 if i % 2 else 0.01) for i, m in enumerate(months)}
    other = {m: (0.05 if i % 2 else 0.01) for i, m in enumerate(months)}          # the low side's months: another shape
    made = {f"n{k}": {"ics": {"2022-01-09": 0.9, "2022-08-07": -0.2, "2022-09-04": -0.2},
                      "high": {m: v * (k + 1) / 10 for m, v in wave.items()},
                      "low": {m: (other[m] if m >= "2022-07" else -v) * (k + 1) / 10 for m, v in wave.items()}} for k in range(9)}
    whole = sl.tier_reading({m: {"net": v} for m, v in wave.items()}, months)["annual_sharpe"]
    half = sl.tier_reading({m: {"net": other[m]} for m in months[6:]}, months[6:])["annual_sharpe"]
    assert whole is not None and half is not None and half != sl.tier_reading({m: {"net": wave[m]} for m in months[6:]}, months[6:])["annual_sharpe"]
    book = {"group": "g", "long": "high", "total_position_annual_pct": 20.0}
    books = {"strong": {**book, "start": "2022-01-01", "months": 12, "annual_sharpe": whole + 5, "clears_entry": True},
             "strong_thin": {**book, "start": "2022-01-01", "months": 12, "annual_sharpe": whole + 4, "clears_entry": False},
             "weak": {**book, "start": "2022-01-01", "months": 12, "annual_sharpe": whole - 5, "clears_entry": False},
             "late_weak": {**book, "start": "2022-07-01", "months": 6, "annual_sharpe": half - 5, "clears_entry": False},
             "short": {"group": "g", "skipped": "its own span is 3 complete months, under 24"}}
    reading = sl.tier_sweep_reading(books, made, q=0.5)
    rows = {r["variable"]: r for r in reading["all"]}
    assert reading["books"] == 4 and reading["skipped"] == {"short": "its own span is 3 complete months, under 24"}
    assert rows["strong"]["null_books"] == 9 and rows["strong"]["p"] == pytest.approx(1 / 10) and rows["weak"]["p"] == 1.0
    assert rows["strong"]["null_max"] == whole, "on the whole year every null is long its high side: the first-half rank correlation wins"
    assert rows["late_weak"]["null_max"] == half and rows["late_weak"]["null_books"] == 9, \
        "cut to the second half the null picks its low side and is read on those six months only"
    assert reading["survive_benjamini_hochberg"] == ["strong", "strong_thin"]
    assert reading["leads"] == ["strong"], "a lead survives the correction AND clears the entry rule"
    assert reading["survivors_under_the_entry_rule"] == ["strong_thin"], "named, and nothing is built from it"
    assert reading["clear_entry"] == ["strong"] and reading["first_threshold"] == pytest.approx(0.5 / 4)
    strict = sl.tier_sweep_reading(books, made, q=0.10)
    assert strict["survive_benjamini_hochberg"] == [] == strict["leads"], "one in ten is over a tenth of a quarter"


def test_the_tier_commands_refuse_what_has_no_book_and_print_what_was_left_out(monkeypatch, capsys):
    market, mids = _mid_world()
    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(sl, "SPAN", MID_TEST_SPAN)
    monkeypatch.setattr(sl.lab, "hold", lambda cache, span=None, *, attach=True, flow=False: market)
    monkeypatch.setattr(sl, "TIER_SWEEP_MIN_MONTHS", 8)
    assert sl.main(["tier-sweep", "--only", "depth"]) == sl.EXIT_USAGE, "depth has no book on this tier"
    assert sl.main(["tier-counts", "--only", "null"]) == sl.EXIT_USAGE and sl.main(["tier-sweep"]) == sl.EXIT_USAGE
    capsys.readouterr()
    monkeypatch.setattr(sl, "tier_sweep_gate", lambda m, span=None: {"annual_sharpe": 1.0})
    assert sl.main(["tier-gate"]) == sl.EXIT_OK
    gate = json.loads(capsys.readouterr().out)
    assert gate["book"] == {"annual_sharpe": 1.0} and (gate["variable"], gate["long"], gate["tier"], gate["logic"]) == ("momentum_30", "low", "rank31_100", "L22")
    assert gate["left_out"] == sl.TIER_SWEEP_OUT
    seen = {}
    monkeypatch.setattr(sl, "tier_sweep", lambda m, names: seen.setdefault("books", sorted(names)) and {})
    monkeypatch.setattr(sl, "tier_sweep_null", lambda m: seen.setdefault("null", True) and {})
    assert sl.main(["tier-sweep", "--only", "coin_character"]) == sl.EXIT_OK and seen["books"] == ["listing_age"]
    assert sl.main(["tier-sweep", "--only", "null"]) == sl.EXIT_OK and seen["null"] is True
    monkeypatch.setattr(sl.lab, "hold", lambda cache, span=None, *, attach=True, flow=False: None)
    assert sl.main(["tier-gate"]) == sl.EXIT_BLOCKED and sl.main(["tier-counts", "--only", "funding"]) == sl.EXIT_BLOCKED


# --- round 37: the tier sweep's lead, before any form -------------------------------------------------------------------

LEAD_TEST_SPAN = (date(2022, 1, 1), date(2023, 12, 31))


def _funded_world():
    """Thirty large flat coins and sixteen smaller ones. Each smaller coin pays or receives a funding rate of its own,
    fixed for the whole span (the lowest is deep under H-F1's threshold, the highest over H-F2's), swings on a 60-day
    cycle of its own, and drifts up the more the lower its funding."""
    start, end = date(2021, 6, 1), date(2023, 12, 31)
    days = [start + timedelta(days=k) for k in range((end - start).days + 1)]
    mids = [f"MID{i:02d}USDT" for i in range(16)]
    epoch = date(1970, 1, 1)

    def swing(i):
        out, price = {}, 100.0
        for k, day in enumerate(days):
            up = ((k + 4 * i) // 30) % 2 == 0
            move = (0.004 if up else -0.004) + (7.5 - i) * 0.0003
            out[day] = xsb.Bar(open=price, close=price * (1 + move), quote_volume=1e7 - i * 1e4)
            price *= 1 + move
        return out

    names = {f"BIG{i:02d}USDT": {d: xsb.Bar(open=100.0, close=100.0, quote_volume=1e9 - i) for d in days} for i in range(30)}
    names.update({s: swing(i) for i, s in enumerate(mids)})
    stamp = lambda day: int((day - epoch).total_seconds() * 1000) + 8 * 3600 * 1000      # noqa: E731
    funding = {s: [(stamp(d), 0.0) for d in days[:1]] for s in names if s.startswith("BIG")}
    funding.update({s: [(stamp(d), -0.001 + i * 0.0002) for d in days] for i, s in enumerate(mids)})
    return xsb.Market(bars=names, funding=funding), mids


def test_the_tier_lead_check_reproduces_the_sweeps_row_and_its_parts_add_up(monkeypatch):
    monkeypatch.setattr(sl, "TIER_SWEEP_MIN_MONTHS", 8)
    market, mids = _funded_world()
    registered = sl.lab.variables()
    swept = sl.tier_sweep(market, {"funding_30": registered["funding_30"]}, LEAD_TEST_SPAN)["funding_30"]
    assert swept["long"] == "low" == sl.TIER_LEADS["funding_30"]["long"], "in this world the coins paid to hold also rise"
    check = sl.tier_lead_check(market, "funding_30", LEAD_TEST_SPAN)
    for key in sl.TIER_LEAD_ROW_KEYS:
        assert check["row"][key] == swept[key], key
    assert round(check["annual_sharpe_unrounded"], 3) == swept["annual_sharpe"] and check["annual_sharpe_unrounded"] != swept["annual_sharpe"]
    legs = check["legs_by_year_total_position_pct"]
    for year, whole in legs["book"].items():
        for key in ("price", "funding", "cost", "net"):
            assert legs["long"][year][key] + legs["short"][year][key] == pytest.approx(whole[key], abs=0.03), (year, key)
        assert whole["net"] == pytest.approx(whole["price"] + whole["funding"] - whole["cost"], abs=0.03)
    assert all(legs[side][year]["funding"] > 0 for side in ("long", "short") for year in legs["book"]), \
        "the long side is paid by its coins' shorts and the short side by its coins' longs"
    assert set(check["row"]["years_total_position_pct"]) == set(legs["book"]) == {"2022", "2023"}
    assert check["row"]["years_total_position_pct"]["2023"] == legs["book"]["2023"]["net"]
    total = sum(v["net"] for v in legs["book"].values())
    coins = check["coins"]
    assert coins["net_total_position_pct"] == pytest.approx(total, abs=0.05) and coins["held"] == 16
    assert coins["gainers"] + coins["losers"] <= 16 and len(coins["top"]) == sl.TIER_LEAD_TOP == len(coins["bottom"])
    assert coins["top"][0][1] >= coins["top"][1][1] >= coins["bottom"][-1][1]
    assert coins["top_share_of_net"]["1"] == pytest.approx(coins["top"][0][1] / coins["net_total_position_pct"], abs=0.01)
    months = check["months"]
    assert months["of"] == check["row"]["months"] and months["top"][0][1] >= months["bottom"][-1][1]
    assert months["top_share_of_net"]["3"] == pytest.approx(sum(v for _, v in months["top"][:3]) / total, abs=0.01)
    before = sum(v["price"] + v["funding"] for v in legs["book"].values())
    received = sum(v["funding"] for v in legs["book"].values())
    assert check["funding_share_of_return_before_costs"] == pytest.approx(received / before, abs=0.002)
    assert check["carry_rule"] == {"share": 0.5, "funding_carried_it": received >= 0.5 * before}


def test_the_tier_lead_check_reads_each_later_year_on_the_side_its_earlier_years_name(monkeypatch):
    monkeypatch.setattr(sl, "TIER_SWEEP_MIN_MONTHS", 8)
    monkeypatch.setattr(sl, "LATER_YEARS", (2022, 2023))
    market, mids = _funded_world()
    check = sl.tier_lead_check(market, "funding_30", LEAD_TEST_SPAN)
    years = check["transfer"]["years"]
    assert years["2022"] == {"decisions_before": 0, "long": None, "total_position_pct": None}, "nothing before the book's first year"
    assert years["2023"]["long"] == "low" and years["2023"]["same_side_as_the_lead"] is True and years["2023"]["rank_correlation_before"] < 0
    assert years["2023"]["total_position_pct"] == check["row"]["years_total_position_pct"]["2023"]
    tier = sl.TierSweep(market, LEAD_TEST_SPAN)
    read = sl.lab.variables()["funding_30"][1]
    counted = [d for d in tier.ics(read) if d >= date(2022, 2, 1) and d + timedelta(days=8) <= date(2023, 1, 1)]
    assert years["2023"]["decisions_before"] == len(counted) > 40, "only weeks that ended before the year began"
    assert check["transfer"]["later_years"]["passes"] is False, "a year with no earlier years to name its side does not pass"
    monkeypatch.setattr(sl, "LATER_YEARS", (2023,))
    passing = sl.tier_lead_check(market, "funding_30", LEAD_TEST_SPAN)["transfer"]["later_years"]
    assert passing["years"] == {"2023": years["2023"]["total_position_pct"]}
    assert passing["passes"] == (years["2023"]["total_position_pct"] >= passing["floor_pct"])
    # a lead registered on the side the earlier years do not name: the year is read on the side they do name
    monkeypatch.setattr(sl, "TIER_LEADS", {"funding_30": {"long": "high"}})
    wrong = sl.tier_lead_check(market, "funding_30", LEAD_TEST_SPAN)
    flipped = wrong["transfer"]["years"]["2023"]
    assert flipped["long"] == "low" and flipped["same_side_as_the_lead"] is False
    assert flipped["total_position_pct"] == years["2023"]["total_position_pct"], "the other side's book, that year"
    assert wrong["row"]["years_total_position_pct"]["2023"] != flipped["total_position_pct"]


def test_the_tier_lead_check_lays_the_book_beside_the_closed_book_and_the_closed_funding_tails(monkeypatch):
    monkeypatch.setattr(sl, "TIER_SWEEP_MIN_MONTHS", 8)
    market, mids = _funded_world()
    check = sl.tier_lead_check(market, "funding_30", LEAD_TEST_SPAN)
    assert set(check["positions_beside"]) == {"momentum_30", "funding_7"} and check["closed_book"]["name"] == "momentum_30"
    assert check["positions_beside"]["funding_7"]["median_cosine"] == pytest.approx(1.0), "a fixed rate ranks the same over 7 and 30 days"
    assert abs(check["positions_beside"]["momentum_30"]["median_cosine"]) < 0.5 and check["closed_book"]["is_the_closed_book_again"] is False
    assert check["positions_beside"]["momentum_30"]["decisions"] == check["decisions_with_a_book"] > 90
    tails = check["closed_funding_tails"]
    assert (tails["deep_threshold"], tails["hot_threshold"], tails["window_days"]) == (-0.005, 0.01, 7)
    tier = sl.TierSweep(market, LEAD_TEST_SPAN)
    weights = tier.weigher(sl.lab.variables()["funding_30"][1], -1)(market, (), date(2023, 6, 4), "x")
    deep = sum(w for s, w in weights.items() if w > 0 and int(s[3:5]) <= 1)       # -0.7% and -0.56% a week
    hot = sum(-w for s, w in weights.items() if w < 0 and int(s[3:5]) >= 13)      # 1.12%, 1.26% and 1.4% a week
    assert tails["long_weight_in_deep_coins_median"] == pytest.approx(deep, abs=1e-4) and 0 < deep < 1
    assert tails["short_weight_in_hot_coins_median"] == pytest.approx(hot, abs=1e-4) and 0 < hot < 1
    assert check["on_bars_with_no_volume_total_position_pct"].keys() == {"traded"}
    # the closed book itself, registered as a lead, is the closed book again
    monkeypatch.setattr(sl, "TIER_LEADS", {"momentum_30": {"long": "low"}})
    again = sl.tier_lead_check(market, "momentum_30", LEAD_TEST_SPAN)
    assert again["positions_beside"]["momentum_30"]["median_cosine"] == pytest.approx(1.0) and again["closed_book"]["is_the_closed_book_again"] is True
    # a coin whose bars stop trading: what the book makes on it from then is set apart
    for day in list(market.bars[mids[0]]):
        if day >= date(2023, 3, 1):
            bar = market.bars[mids[0]][day]
            market.bars[mids[0]][day] = xsb.Bar(open=bar.open, close=bar.close, quote_volume=0.0)
    monkeypatch.setattr(sl, "TIER_LEADS", {"funding_30": {"long": "low"}})
    apart = sl.tier_lead_check(market, "funding_30", LEAD_TEST_SPAN)["on_bars_with_no_volume_total_position_pct"]
    assert set(apart) == {"no_volume", "traded"} and apart["no_volume"] != 0


def test_the_tier_lead_command_takes_only_a_registered_lead(monkeypatch, capsys):
    market, mids = _funded_world()
    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(sl.lab, "hold", lambda cache, span=None, *, attach=True, flow=False: market)
    assert sl.TIER_LEADS == {"funding_30": {"long": "low"}}, "the book the tier sweep read, and no variant of it"
    assert sl.main(["tier-lead", "--only", "funding_7"]) == sl.EXIT_USAGE and sl.main(["tier-lead"]) == sl.EXIT_USAGE
    capsys.readouterr()
    monkeypatch.setattr(sl, "tier_lead_check", lambda m, name: {"lead": name})
    assert sl.main(["tier-lead", "--only", "funding_30"]) == sl.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["lead"] == "funding_30" and out["command"] == "tier-lead"
    monkeypatch.setattr(sl.lab, "hold", lambda cache, span=None, *, attach=True, flow=False: None)
    assert sl.main(["tier-lead", "--only", "funding_30"]) == sl.EXIT_BLOCKED


# --- round 39: how large an effect the map's two doors can see --------------------------------------------------------

def _door_frames(decisions=60, coins=20, *, offset=0):
    """Made-up decisions: each coin's forward return is its own fixed draw plus a weekly one."""
    rng = random_module.Random("door-test")
    base = {f"C{i:02d}USDT": rng.gauss(0, 0.01) for i in range(coins + offset)}
    frames = []
    for k in range(decisions):
        day = date(2022, 1, 2) + timedelta(days=7 * k)
        frames.append((day, {s: b + rng.gauss(0, 0.05) for s, b in list(base.items())[offset:]}))
    return {("top30", 7): frames}


DOOR_LINES = {"7": {"largest_abs_t": 4.0, "largest_abs_spread_t": 3.5}, "28": {"largest_abs_t": 5.0, "largest_abs_spread_t": 4.0}}


def test_a_planted_variable_is_its_size_of_the_forward_return_and_never_a_registered_variable():
    returns = {"A": 0.10, "B": -0.20, "C": 0.0, "D": 0.05}
    noise = {"A": 0.3, "B": -1.0, "C": 0.7, "D": 0.0}
    assert sl.planted(returns, "rank", 0.0, noise) == noise, "size 0 is the noise alone"
    full = sl.planted(returns, "rank", 1.0, noise)
    assert sorted(full, key=full.get) == sorted(returns, key=returns.get), "size 1 orders coins as the return does"
    assert sum(full.values()) == pytest.approx(0.0) and full["B"] == -max(full.values())
    raw = sl.planted(returns, "raw", 1.0, noise)
    assert raw["A"] - raw["C"] == pytest.approx(2 * (raw["D"] - raw["C"])), "the raw shape keeps the returns' spacing"
    half = sl.planted(returns, "raw", 0.6, noise)
    assert half["A"] == pytest.approx(0.6 * raw["A"] + 0.8 * noise["A"])
    assert sl.planted({"A": 0.1, "B": 0.1}, "raw", 0.5, noise) == {}, "no spread in the returns, no signal"
    registered = set(sl.lab.variables()) | set(sl.lab.controls())
    assert not {n for n in registered if "door" in n or "plant" in n}
    assert not any(row.startswith("door") for row in sl.TIER_SWEEP_OUT) and set(sl.DOOR_SHAPES) == {"rank", "raw"}


def test_door_noise_is_redrawn_weekly_or_fixed_per_coin_and_a_seed_is_a_draw():
    frames = _door_frames(decisions=5, coins=10)[("top30", 7)]
    week = sl._door_noise("week", 0, frames, "t")
    fixed = sl._door_noise("fixed", 0, frames, "t")
    assert week[0] != week[1] and fixed[0] == fixed[1] == fixed[4]
    assert sl._door_noise("week", 0, frames, "t") == week and sl._door_noise("week", 1, frames, "t") != week
    assert sl._door_noise("fixed", 1, frames, "t") != fixed
    later = _door_frames(decisions=5, coins=10, offset=3)[("top30", 7)]
    moved = sl._door_noise("fixed", 0, later, "t")
    assert all(moved[0][s] == fixed[0][s] for s in moved[0] if s in fixed[0]), "a coin keeps its draw whatever else is listed"


def test_the_doors_see_a_large_planted_effect_and_not_a_planted_nothing():
    frames = _door_frames()
    out = sl.door_size(None, DOOR_LINES, sizes=(0.0, 0.05, 0.6), seeds=12, frames=frames)
    rows = {(r["noise"], r["shape"], r["size"]): r for r in out["rows"]}
    assert len(out["rows"]) == 2 * 2 * 3 and out["lines"]["7"] == {"largest_abs_t": 4.0, "largest_abs_spread_t": 3.5}
    for kind in sl.DOOR_NOISE:
        for shape in sl.DOOR_SHAPES:
            nothing, big = rows[(kind, shape, 0.0)], rows[(kind, shape, 0.6)]
            assert nothing["seeds"] == 12 and nothing["seen_either"] == 0.0
            assert big["seen_ic"] == 1.0 and big["mean_rank_correlation"] > 0.3 and big["rank_book_gross_total_position_annual_pct"] > 0
            assert abs(nothing["mean_rank_correlation"]) < 0.02
    assert rows[("fixed", "rank", 0.0)]["median_persistence"] > 0.9 > 0.2 > abs(rows[("week", "rank", 0.0)]["median_persistence"])
    assert rows[("week", "rank", 0.6)]["seen_rule"] == 0.0, "noise redrawn weekly never keeps its rank: the full rule cannot pass"
    assert rows[("fixed", "rank", 0.0)]["seen_rule"] == 0.0
    reading = out["reading"]
    assert reading["planting_broken"] is False and reading["false_alarms"] == []
    apart = sl.door_size(None, {**DOOR_LINES, "7": {"largest_abs_t": 100.0, "largest_abs_spread_t": 0.0}}, sizes=(0.6,), seeds=3, frames=frames)
    assert all(r["seen_ic"] == 0.0 and r["seen_spread"] == 1.0 == r["seen_either"] for r in apart["rows"]), "each door reads its own line"
    bracket = next(b for b in reading["brackets"] if (b["noise"], b["shape"]) == ("week", "rank"))
    assert bracket["ic"]["seen"]["size"] == 0.6 and bracket["ic"]["below"]["size"] == 0.05
    # the cell is the map's own, and the gross is L1's rank book on the planted values
    noise = sl._door_noise("week", 0, frames[("top30", 7)], "top30:7")
    day, returns = frames[("top30", 7)][3]
    values = sl.planted(returns, "rank", 0.6, noise[3])
    weights = sl.rank_weights(values, long_high=True)
    assert sl._gross(values, returns) == pytest.approx(sum(w * returns[s] for s, w in weights.items()))


def test_the_door_reading_brackets_each_door_and_stops_on_false_alarms():
    def row(size, ic, spread=0.0, rule=0.0, either=None, seeds=100):
        return {"tier": "top30", "horizon_days": 7, "noise": "fixed", "shape": "rank", "size": size, "seeds": seeds,
                "seen_ic": ic, "seen_spread": spread, "seen_rule": rule, "seen_either": max(ic, spread) if either is None else either,
                "mean_rank_correlation": size / 2, "tercile_spread_bps": size * 1000, "rank_book_gross_total_position_annual_pct": size * 100}
    rows = [row(0.0, 0.0), row(0.03, 0.4), row(0.05, 0.79, 0.1), row(0.08, 0.8, 0.5, 0.8), row(0.1, 1.0, 0.95, 1.0)]
    reading = sl.door_reading(rows)
    b = reading["brackets"][0]
    assert (b["ic"]["below"]["size"], b["ic"]["seen"]["size"]) == (0.05, 0.08), "0.79 is under the share, 0.8 is at it"
    assert (b["spread"]["below"]["size"], b["spread"]["seen"]["size"]) == (0.08, 0.1)
    assert b["ic"]["seen"]["rank_book_gross_total_position_annual_pct"] == 8.0
    assert reading["planting_broken"] is False
    never = sl.door_reading([row(0.0, 0.0), row(0.1, 0.5)])["brackets"][0]
    assert never["ic"]["seen"] is None and never["ic"]["below"]["size"] == 0.1, "never seen: the largest size is the floor"
    alarm = sl.door_reading([row(0.0, 0.04), row(0.1, 1.0)])
    assert alarm["planting_broken"] is True and alarm["false_alarms"] == [["top30", 7, "fixed", "rank", 0.04]]
    assert sl.door_reading([row(0.0, 0.03), row(0.1, 1.0)])["planting_broken"] is False, "three of a hundred is allowed"


def test_the_door_frames_are_the_maps_decisions_and_refuse_a_market_past_the_span(monkeypatch):
    market, mids = _mid_world()
    frames = sl.door_frames(market, MID_TEST_SPAN)
    assert set(frames) == {(t, h) for t in ("top30", "rank31_100") for h in (7, 28)}
    days = [d for d, _ in frames[("rank31_100", 7)]]
    assert days == [d for d in sl.lab.decisions(MID_TEST_SPAN, 7)], "every map decision on this tier has its sixteen coins"
    day, returns = frames[("rank31_100", 7)][10]
    assert set(returns) == set(mids) and returns[mids[0]] == sl.lab.forward_return(market, mids[0], day, 7)
    assert [d for d, _ in frames[("rank31_100", 28)]] == sl.lab.decisions(MID_TEST_SPAN, 28)
    with pytest.raises(ValueError, match="past the span"):
        sl.door_frames(market, (date(2022, 1, 1), date(2022, 6, 30)))


def test_the_door_command_reads_the_random_variables_lines_from_the_same_run(monkeypatch, capsys):
    market, mids = _mid_world()
    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setattr(sl.lab, "hold", lambda cache, span=None, *, attach=True, flow=False: market)
    seen = {}
    monkeypatch.setattr(sl.lab, "grid", lambda m, names: seen.setdefault("names", sorted(names)) and {"cells": []})
    monkeypatch.setattr(sl.lab, "chance", lambda cells: DOOR_LINES)
    monkeypatch.setattr(sl, "door_size", lambda m, lines: {"lines": lines, "rows": []})
    assert sl.main(["door-size"]) == sl.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert seen["names"] == sorted(sl.lab.controls()) and out["lines"] == DOOR_LINES and "calibration" in out["planted"]
    monkeypatch.setattr(sl.lab, "hold", lambda cache, span=None, *, attach=True, flow=False: None)
    assert sl.main(["door-size"]) == sl.EXIT_BLOCKED


# --- round 40: ranks 101 and after, read by the map and by the tier sweep --------------------------------------------

LOWER_TEST_SPAN = (date(2020, 1, 1), date(2021, 6, 30))


def test_the_lower_tier_strikes_by_name_what_the_record_forbids_there_and_both_readers_use_one_list():
    out, names = sl.lower_out(), sl.lower_names()
    registered = sl.lab.variables()
    assert set(out) | set(names) == set(registered) and not set(out) & set(names)
    assert {"momentum_7", "momentum_30", "momentum_90", "funding_7", "funding_30"} <= set(out), "H-R1, H-R2, round 37's closed lead"
    assert {n for n, (g, _) in registered.items() if g in ("positioning", "depth")} <= set(out)
    assert {"vol_30", "beta_30", "spread_change_30", "trade_size_position_30"} <= set(out), "every tier's strikes hold here"
    assert {"funding_change_30", "funding_position_30", "momentum_change_30", "residual_momentum_30", "listing_age",
            "taker_buy_share_30", "premium_30", "spot_share_30"} <= set(names), "kin, built and named on the form"
    assert all(out[n] == sl.lab.no_book(n, sl.LOWER_NAME) for n in out), "the map's no_book and the sweep's list are one"
    assert sl.lower_refused_groups() == {"positioning", "depth"} and len(names) == 27
    assert sl.lab.LOWER_TIER_NAME not in sl.lab.TIERS, "the mapped tiers are untouched"


def test_the_lower_sweep_tier_holds_lower_tiers_coins_and_the_gate_reads_h_r1s_numbers(monkeypatch):
    monkeypatch.setattr(sl, "LOWER_TIER_FROM", 4)
    monkeypatch.setattr(sl, "LOWER_MIN_ACTIVE", 0)
    market = _lower_market({f"LOW{i}USDT": (i - 4) / 1000 for i in range(9)})
    tier = sl.lower_sweep_tier(market, LOWER_TEST_SPAN)
    day = date(2021, 3, 7)
    assert tier.members(day) == sl.LowerTier(market, LOWER_TEST_SPAN).at(day)[0] and tier.name == sl.LOWER_NAME
    lower = sl.LowerTier(market, LOWER_TEST_SPAN)
    assert tier.weigher(sl.skip_day_return, -1)(market, (), day, "x") == sl.lower_book(lower, sl.skip_day_return, -1)(market, (), day, "x")
    record = sl.lower_run(market, LOWER_TEST_SPAN, nulls=1)                     # how H-R1 was read
    gate = sl.lower_gate(market, LOWER_TEST_SPAN)
    for key in ("annual_sharpe", "sum_pct", "months", "total_position_annual_pct", "clears_entry", "cost_pct", "funding_pct"):
        assert gate[key] == record[key], key
    assert gate["years_total_position_pct"] == record["years_total_position_pct"] and gate["sum_pct"] != 0


def test_the_default_tier_sweep_and_grid_are_the_mapped_tiers():
    market, mids = _mid_world()
    assert set(sl.TierSweep(market, MID_TEST_SPAN).members(MID_DAY)) == set(mids)
    names = {"momentum_30": sl.lab.variables()["momentum_30"]}
    whole = sl.lab.grid(market, names, MID_TEST_SPAN)
    tiers = sl.lab.tier_members(market, MID_TEST_SPAN)
    given = {t: (lambda d, m=m: m.get(date(d.year, d.month, 1), ())) for t, m in tiers.items()}
    assert sl.lab.grid(market, names, MID_TEST_SPAN, members=given) == whole, "passing the tiers changes nothing"
    alone = sl.lab.grid(market, names, MID_TEST_SPAN, members={"rank31_100": given["rank31_100"]})
    assert {c["tier"] for c in alone["cells"]} == {"rank31_100"}
    assert [c for c in whole["cells"] if c["tier"] == "rank31_100"] == alone["cells"], "a tier's cells do not depend on the others"


def test_coverage_stops_a_variable_that_has_values_mostly_on_coins_about_to_join_the_higher_tier(monkeypatch):
    monkeypatch.setattr(sl, "LOWER_TIER_FROM", 4)
    market = _lower_market()
    tier = sl.lower_sweep_tier(market, LOWER_TEST_SPAN)
    everywhere = lambda m, s, d: 1.0 if s.startswith("LOW") else None
    rising = lambda m, s, d: 1.0 if s in ("LOW0USDT", "LOW1USDT") else None
    monkeypatch.setattr(sl.xsb, "universes", lambda market, *, top_n, start, end, named=(): {
        date(2020, m, 1) if m <= 12 else date(2021, m - 12, 1): ["BTCUSDT", "BIG0USDT", "BIG1USDT", "LOW0USDT"]
        for m in range(1, 19)})
    cover = sl.lower_coverage(market, {"all": ("g", everywhere), "rising": ("g", rising)}, LOWER_TEST_SPAN, tier=tier)
    assert cover["all"]["stop"] is None and cover["all"]["share"] == 1.0 and cover["all"]["share_not_top_next_month"] == 1.0
    assert cover["rising"]["share"] is None and "None" in cover["rising"]["stop"], "never ranks eight coins: no span, stopped"
    monkeypatch.setattr(sl.xsb, "universes", lambda market, *, top_n, start, end, named=(): {
        date(2020, m, 1) if m <= 12 else date(2021, m - 12, 1): ["BTCUSDT", "LOW0USDT", "LOW1USDT", "LOW2USDT"]
        for m in range(1, 19)})
    late = lambda m, s, d: 1.0 if d < date(2021, 1, 1) or s in ("LOW0USDT", "LOW1USDT", "LOW2USDT", "LOW3USDT", "LOW4USDT") else None
    cover = sl.lower_coverage(market, {"late": ("g", late)}, LOWER_TEST_SPAN, tier=tier)["late"]
    assert cover["share"] >= sl.LOWER_COVERAGE > cover["share_not_top_next_month"], cover
    assert "not in the first" in cover["stop"], "the second share stops it on its own"


def test_the_later_years_rule_names_each_side_from_the_years_before_and_never_drops_a_year(monkeypatch):
    monkeypatch.setattr(sl, "LATER_YEARS", (2022, 2023, 2024))

    class Stub:
        def ics(self, read):
            out = {date(2021, 9, 5) + timedelta(days=7 * k): -0.1 for k in range(10)}        # ten before 2022
            out.update({date(2022, 1, 2) + timedelta(days=7 * k): 0.2 for k in range(52)})   # 2022 names "high"
            return out

        def monthly_rows(self, read, way, every=1):
            assert every == 1
            return {f"{y}-06": {"net": (0.2 if way > 0 else -0.2) * sl.bs.POSITION_TOTAL / 100 * 10}
                    for y in (2022, 2023, 2024)}
    months = [f"{y}-{m:02d}" for y in (2022, 2023, 2024) for m in range(1, 13)]
    got = sl.tier_later(Stub(), None, months)
    assert got["sides"]["2022"] == {"decisions_before": 10, "long": None} and got["years"]["2022"] == 0.0, "too few: zero, kept"
    assert got["sides"]["2023"]["long"] == "high" and got["years"]["2023"] == pytest.approx(2.0)
    assert set(got["years"]) == {"2022", "2023", "2024"} and got["passes"] is False, "mean 1.33 under the floor"


def test_the_lower_reading_closes_a_survivor_that_fails_the_later_years():
    null = {f"n{k}": {"ics": {"2021-10-03": 0.01}, "high": {"2021-10": -0.01, "2021-11": 0.0, "2021-12": -0.02},
                      "low": {"2021-10": -0.01, "2021-11": -0.03, "2021-12": 0.0}} for k in range(30)}
    body = {"group": "g", "start": "2021-10-01", "months": 3, "long": "high", "annual_sharpe": 5.0,
            "total_position_annual_pct": 40.0, "clears_entry": True}
    books = {"a": {**body, "later_years": {"passes": True}}, "b": {**body, "later_years": {"passes": False}}}
    reading = sl.lower_sweep_reading(books, null)
    assert reading["leads_before_later_years"] == ["a", "b"] and reading["leads"] == ["a"]
    assert reading["closed_at_later_years"] == ["b"]


def test_the_lower_tier_counts_carry_no_return(monkeypatch):
    monkeypatch.setattr(sl, "LOWER_TIER_FROM", 4)
    market = _lower_market()
    names = {"momentum_change_30": sl.lab.variables()["momentum_change_30"]}
    counts = sl.lower_tier_counts(market, names, LOWER_TEST_SPAN)
    assert counts["returns_computed"] is False and counts["unfunded_share"] == pytest.approx(0.1)
    assert set(counts["sweep"]["books"]["momentum_change_30"]["ranked_decisions_before"]) == {"2022", "2023", "2024"}
    flat = json.dumps(counts)
    assert "sharpe" not in flat and "_pct" not in flat


def test_the_lower_commands_refuse_struck_groups(monkeypatch, capsys):
    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    for command in ("lower-counts", "lower-map", "lower-sweep"):
        assert sl.main([command, "--only", "positioning"]) == sl.EXIT_USAGE
        assert sl.main([command, "--only", "depth"]) == sl.EXIT_USAGE
    assert sl.main(["lower-counts", "--only", "null"]) == sl.EXIT_USAGE
    assert "struck on this tier" in capsys.readouterr().err


# --- round 41: the two map leads' overlap with what the record strikes there ----------------------------------------

def test_the_lead_overlap_lays_each_lead_beside_every_struck_book_with_values_and_h_r1(monkeypatch):
    monkeypatch.setattr(sl, "LOWER_TIER_FROM", 4)
    moves = {f"LOW{i}USDT": (i - 4) / 1000 for i in range(9)}
    market = _lower_market(moves)
    registered = sl.lab.variables()
    # The beta-netted month needs a moving reference coin; this market's is flat, so the lead reads the plain month.
    monkeypatch.setattr(sl.lab, "variables", lambda: {**registered, "residual_momentum_30": registered["momentum_30"]})
    got = sl.lower_lead_overlap(market, LOWER_TEST_SPAN)
    assert got["returns_computed"] is False and got["threshold"] == 0.5
    struck = {n for n, (g, _) in sl.lab.variables().items() if n in sl.lower_out() and g not in ("positioning", "depth")}
    assert set(got["compared"]) == struck | {"H-R1"} and {"vol_30", "momentum_30", "funding_30", "extreme_days_30"} <= struck
    res = got["leads"]["residual_momentum_30"]
    assert res["long"] == "low" and res["beside"]["momentum_30"]["decisions"] > 0
    # Every coin drifts at its own steady rate, so the month's return and the reversal rank the coins alike:
    # long the low month is long the low week, which is H-R1's side, and is momentum_30's high side reversed.
    assert res["beside"]["H-R1"]["median_cosine"] > 0.5 and "H-R1" in res["closed_by"]
    assert res["beside"]["momentum_30"]["median_cosine"] < -0.5 and "momentum_30" in res["closed_by"], "the size is read, not the sign"
    assert set(got["leads"]) == {"residual_momentum_30", "listing_age"} and got["leads"]["listing_age"]["long"] == "high"


# --- H-A1: listing age on ranks 101+, held four weeks ------------------------------------------------------------------

def test_the_no_name_twin_gives_tied_coins_their_groups_mean_weight():
    values = {"A": 18.5, "B": 18.5, "C": 18.5, **{f"X{k:02d}": float(k) for k in range(25)}}
    weights = sl.rank_weights(values, long_high=True)
    assert weights["C"] > weights["B"] > weights["A"], "L1 puts tied coins in name order"
    twin = sl.tie_mean(weights, values)
    assert twin["A"] == twin["B"] == twin["C"] == pytest.approx((weights["A"] + weights["B"] + weights["C"]) / 3)
    assert twin["X24"] == weights["X24"] and sum(twin.values()) == pytest.approx(sum(weights.values()))


def test_a_four_week_book_trades_only_on_the_maps_28_day_decisions(monkeypatch):
    monkeypatch.setattr(sl, "LOWER_TIER_FROM", 4)
    market = _lower_market({f"LOW{i}USDT": (i - 4) / 1000 for i in range(9)})
    tier = sl.lower_sweep_tier(market, LOWER_TEST_SPAN)
    read = sl.lab.variables()["momentum_30"][1]
    sim = sl.simulate_held(market, sl.bs.CADENCE, {}, start=LOWER_TEST_SPAN[0], end=LOWER_TEST_SPAN[1],
                           weigher=tier.weigher(read, 1), unit_of=sl.listing_unit, every=sl.HA1_EVERY)
    traded = {d - timedelta(days=1) for d, row in sim["daily"].items() if row.get("cost") and d < LOWER_TEST_SPAN[1]}
    assert traded and traded <= set(sl.lab.decisions(LOWER_TEST_SPAN, 28)) | set(sl.ha1_days(tier))
    assert all(sl.is_decision(sl.bs.CADENCE, d, 4) for d in traded)
    assert {d for d in sl.lab.decisions(LOWER_TEST_SPAN, 28) if d in set(tier.days)} <= set(sl.ha1_days(tier))
    assert all(sl.is_decision(sl.bs.CADENCE, d, 4) for d in sl.ha1_days(tier)), "the map's 28-day calendar"
    weekly = tier.monthly_rows(read, 1)
    assert tier.monthly_rows(read, 1, every=1) == weekly != tier.monthly_rows(read, 1, every=4)


def test_the_ha1_counts_carry_no_return_and_stop_without_the_later_years(monkeypatch):
    monkeypatch.setattr(sl, "LOWER_TIER_FROM", 4)
    market = _lower_market()
    counts = sl.ha1_counts(market, LOWER_TEST_SPAN)
    assert counts["returns_computed"] is False and counts["stop"] and "2022, 2023, 2024" in counts["stop"][0]
    year = counts["by_year"]["2021"]
    assert year["decisions_with_a_book"] > 0 and year["tied_share_mean"] == 1.0, "every coin starts at the archive's first day"
    assert year["long_weight_on_tied_mean"] == 1.0
    flat = json.dumps(counts)
    assert "sharpe" not in flat and "_pct" not in flat
    assert sl.ha1_run(market, LOWER_TEST_SPAN, nulls=1)["refused"] == counts["stop"]


def test_the_ha1_run_reads_four_rungs_and_the_twin_over_the_whole_span(monkeypatch):
    monkeypatch.setattr(sl, "LOWER_TIER_FROM", 4)
    monkeypatch.setattr(sl, "HA1_MIN_ACTIVE", 0)
    monkeypatch.setattr(sl, "LATER_YEARS", (2021,))
    market = _lower_market({f"LOW{i}USDT": (i - 4) / 1000 for i in range(9)})
    for i in range(9):                                   # give the coins different listing days
        bars = market.bars[f"LOW{i}USDT"]
        for d in [d for d in bars if d < date(2020, 1, 1) + timedelta(days=10 * i)]:
            del bars[d]
    run = sl.ha1_run(market, LOWER_TEST_SPAN, nulls=3)
    assert set(run["rungs"]) == {"2a_entry", "2b_later_years", "2c_beside_null_books", "3_stability", "no_name_twin_entry"}
    assert run["months"] == 18 and set(run["years_total_position_pct"]) == {"2020", "2021"}, "every month of the span"
    assert run["rungs"]["2c_beside_null_books"]["null_books"] == 3 and sum(run["null_sides"].values()) == 3
    assert run["rungs"]["2b_later_years"]["sides"]["2021"]["decisions_before"] >= 0
    assert run["twin"]["annual_sharpe"] is not None and run["hypothesis"] == "H-A1" and run["reading"] in ("passes", "closed")


def test_ha1_passes_only_with_every_rung_and_the_twin():
    good = {"2a_entry": True, "2b_later_years": {"passes": True}, "2c_beside_null_books": {"passes": True},
            "3_stability": {"passes": True}, "no_name_twin_entry": True}
    assert sl.ha1_passes(good)
    for key in good:
        bad = dict(good)
        bad[key] = False if isinstance(good[key], bool) else {"passes": False}
        assert not sl.ha1_passes(bad), key


# --- round 42: the sweep's sensitivity --------------------------------------------------------------------------------

def test_a_planted_sweep_variable_is_its_size_of_next_weeks_rank_and_noise_fixed_per_coin(monkeypatch):
    market, mids = _mid_world()
    tier = sl.TierSweep(market, MID_TEST_SPAN)
    nothing = sl.sens_planted(tier, 0.0, 0)
    values = {s: nothing(market, s, MID_DAY) for s in mids}
    assert {s: nothing(market, s, MID_DAY + timedelta(days=21)) for s in mids} == values, "size 0 is the noise, fixed per coin"
    fixed = tier.fixed("tier:0")
    assert values != {s: fixed(market, s, MID_DAY) for s in mids}, "its noise is not a stored null book's"
    assert {s: sl.sens_planted(tier, 0.0, 1)(market, s, MID_DAY) for s in mids} != values, "a seed is a draw"
    assert nothing(market, mids[0], date(2021, 1, 1)) is None, "no value where the coin has no bar"
    whole = sl.sens_planted(tier, 1.0, 0)
    ics = tier.ics(whole)
    assert ics and all(ic == pytest.approx(1.0) for ic in ics.values()), "size 1 is next week's rank itself"
    assert all(abs(ic) < 0.9 for ic in tier.ics(nothing).values())
    lone = tier.forward(MID_DAY)
    some = dict(list(lone.items())[:5])
    monkeypatch.setattr(tier, "forward", lambda day: some)
    half = sl.sens_planted(tier, 0.6, 0)
    missing = next(s for s in mids if s not in some)
    assert half(market, missing, MID_DAY) == pytest.approx(0.8 * sl.random.Random(f"plant:0:{missing}").gauss(0.0, 1.0)), \
        "a coin with no next-week return is its noise alone"
    assert set(sl.lab.variables()).isdisjoint({"planted", "plant"}), "never registered"


def test_the_sensitivity_lead_is_the_sweeps_rule_for_one_book_in_its_registered_family():
    mid, lower = sl.SENS_FAMILIES["mid"], sl.SENS_FAMILIES["lower"]
    assert (mid["books"], mid["start"], mid["later_years"]) == (36, date(2021, 1, 1), False)
    assert (lower["books"], lower["start"], lower["later_years"]) == (24, date(2021, 9, 1), True)
    assert mid["tier"] == sl.TIER_SWEEP_TIER and lower["tier"] == sl.LOWER_NAME
    book = {"clears_entry": True}
    assert sl.sens_lead(book, 1 / 601, mid) and not sl.sens_lead(book, 2 / 601, mid), "ranks 31-100: above every null"
    passed = {"clears_entry": True, "later_years": {"passes": True}}
    assert sl.sens_lead(passed, 2 / 601, lower) and not sl.sens_lead(passed, 3 / 601, lower), "ranks 101+: one null may stand above"
    assert not sl.sens_lead({"clears_entry": True, "later_years": {"passes": False}}, 1 / 601, lower)
    assert not sl.sens_lead({"clears_entry": True}, 1 / 601, lower), "the later-years rule is read there"
    assert not sl.sens_lead({"clears_entry": False}, 1 / 601, mid)
    assert sl.sens_bracket({0.0: 0.0, 0.05: 0.5, 0.08: 0.8, 0.1: 1.0}) == {"under": 0.05, "at_or_over": 0.08}
    assert sl.sens_bracket({0.0: 0.0, 0.05: 0.79}) == {"under": 0.05, "at_or_over": None}
    assert sl.sens_bracket({0.0: 0.9, 0.05: 1.0}) == {"under": None, "at_or_over": 0.0}


def test_the_sensitivity_run_checks_the_stored_nulls_and_stops_on_false_alarms(monkeypatch):
    market, mids = _mid_world()
    tier = sl.TierSweep(market, MID_TEST_SPAN)
    null = sl.tier_sweep_null(market, MID_TEST_SPAN, nulls=10)
    assert sl.sens_null_check(tier, null) == []
    broken = json.loads(json.dumps(null))
    broken["tier:2"]["high"]["2022-03"] += 0.01
    assert sl.sens_null_check(tier, broken) == ["tier:2"]
    family = {**sl.SENS_FAMILIES["mid"], "books": 1, "start": date(2022, 2, 1)}
    monkeypatch.setattr(sl, "SENS_FAMILIES", {"mid": family})
    assert sl.sens_run(market, "mid", broken, MID_TEST_SPAN, sizes=(0.0,), seeds=1)["refused"].startswith("stored null books differ")
    run = sl.sens_run(market, "mid", null, MID_TEST_SPAN, sizes=(0.0, 1.0), seeds=3)
    assert run["months"] == 11 and run["null_books"] == 10 and run["threshold"] == pytest.approx(0.1)
    whole = run["sizes"]["1.0"]
    assert whole["entry_share"] == 1.0 == whole["lead_share"] and whole["median"]["mean_rank_correlation"] == pytest.approx(1.0)
    assert len(whole["turnover"]) == 3 and run["own_start_later"] == []
    assert run["false_alarm"]["p_under_0.05_at_zero"] == 0 and run["false_alarm"]["stop"] and run["brackets"] is None, \
        "with ten nulls no p is under 0.05: the gate cannot be met, so nothing is bracketed"
    monkeypatch.setattr(sl, "SENS_FALSE_ALARM", (0, 10))
    again = sl.sens_run(market, "mid", null, MID_TEST_SPAN, sizes=(0.0, 1.0), seeds=3)
    assert again["brackets"]["lead_share"]["at_or_over"] == 1.0
    flat = json.dumps(again)
    assert "momentum" not in flat and "funding_30" not in flat, "no real variable is read"


def test_the_sensitivity_command_takes_a_family_and_its_own_null_file(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    assert sl.main(["sens"]) == sl.EXIT_USAGE
    wrong = tmp_path / "null.json"
    wrong.write_text(json.dumps({"tier": sl.LOWER_NAME, "null": {}}), encoding="utf-8")
    assert sl.main(["sens", "--family", "mid", "--null", str(wrong)]) == sl.EXIT_USAGE, "another tier's nulls"
    monkeypatch.setattr(sl.lab, "hold", lambda cache, span=None, *, attach=True, flow=False: None)
    assert sl.main(["sens", "--family", "lower", "--null", str(wrong)]) == sl.EXIT_BLOCKED


# --- round 43: a scenario effect, read by the scenario cells and by the axis check ---------------------------------------

def test_the_split_share_is_the_axis_checks_separation_and_its_sums_path_is_the_same_number():
    rng = random_module.Random("split")
    days = [date(2022, 1, 2) + timedelta(days=7 * k) for k in range(90)]
    labels = [None] * 10 + [("a" if (k // 7) % 3 == 0 else "b" if (k // 7) % 3 == 1 else "c") for k in range(80)]
    x = [rng.gauss(0, 0.1) for _ in days]
    groups = [[i for i, s in enumerate(labels) if s == st] for st in ("a", "b", "c")]
    exact = sl.lab.separation({("v", "t"): dict(zip(days, x))}, dict(zip(days, labels)))
    assert sl.split_share(x, groups) == pytest.approx(exact, rel=1e-12)
    labelled = [x[i] for g in groups for i in g]
    whole = (sum(labelled), sum(v * v for v in labelled), len(labelled))
    assert sl._split_share_full(x.__getitem__, groups, whole) == pytest.approx(exact, rel=1e-9)
    holes = list(x)
    holes[12] = holes[40] = None
    with_holes = sl.lab.separation({("v", "t"): {d: v for d, v in zip(days, holes) if v is not None}}, dict(zip(days, labels)))
    assert sl.split_share(holes, groups) == pytest.approx(with_holes, rel=1e-12)
    thin = [g[:5] if i == 2 else g for i, g in enumerate(groups)]
    assert sl.split_share(x, thin) == pytest.approx(sl.split_share(x, groups[:2]), rel=1e-12), "a thin state is left out"


def test_the_axis_placebos_are_the_checks_own_and_a_test_reads_them_the_same_way_fast_or_slow():
    real = [None] * 5 + ["hi"] * 8 + ["lo"] * 9 + ["hi"] * 7 + ["lo"] * 12 + ["hi"] * 9 + ["lo"] * 10
    placebos = sl.axis_placebos(real, "vol", 30)
    for k in (0, 7, 29):
        shuffled = sl.lab.shuffled_spells(real, f"{sl.lab.SPEC}|axis|vol|{k}")
        assert [list(g) for g in placebos[k]] == [[i for i, s in enumerate(shuffled) if s == st] for st in ("hi", "lo")]
    assert all(len(p[0]) == real.count("hi") and len(p[1]) == real.count("lo") for p in placebos)
    assert sl.axis_carrier(real) == "hi" and sl.axis_carrier([None, "b", "a"]) == "a" and sl.axis_carrier([None]) is None
    assert sl.axis_planted_size("hi", "hi", "flip", 0.1) == 0.1 and sl.axis_planted_size("lo", "hi", "flip", 0.1) == -0.1
    assert sl.axis_planted_size("lo", "hi", "one", 0.1) == 0.0 and sl.axis_planted_size(None, "hi", "flip", 0.1) == 0.0
    rng = random_module.Random("axis-test")
    x = [rng.gauss(0.3 if s == "hi" else 0.0, 0.1) for s in real]
    fast = sl.axis_test(x, real, placebos)
    assert fast["counted"] == 30 and fast["p"] == pytest.approx(1 / 31), "a large state difference beats every placebo"
    groups = [[i for i, s in enumerate(real) if s == st] for st in ("hi", "lo")]
    actual = sl.split_share(x, groups)
    slow_above = sum(1 for p in placebos if sl.split_share(x, p) >= actual - 1e-12)
    assert fast["p"] == (1 + slow_above) / 31
    x[3] = None                                         # an unlabelled decision: still the fast path
    x[8] = None                                         # a labelled one missing: the slow path, same rule
    slow = sl.axis_test(x, real, placebos)
    assert slow["counted"] == 30 and slow["p"] == pytest.approx(1 / 31)


def _axis_world(monkeypatch, decisions=80):
    frames = _door_frames(decisions=decisions, coins=20)[("top30", 7)]
    days = [d for d, _ in frames]
    labels = {d: ({} if k < 10 else {"vol": "high" if ((k - 10) // 7) % 2 == 0 or k >= decisions - 4 else "low"}) for k, d in enumerate(days)}
    monkeypatch.setattr(sl.lab, "decisions", lambda span, horizon: days)
    monkeypatch.setattr(sl.lab, "tier_members", lambda market, span: {"top30": {}})
    monkeypatch.setattr(sl.lab, "scenario_labels", lambda market, monthly, ds, span: {d: labels[d] for d in ds})
    return frames


def test_the_axis_power_reads_a_scenario_effect_by_the_cells_and_by_the_axis_check(monkeypatch):
    frames = _axis_world(monkeypatch)
    out = sl.axis_power(None, "top30", DOOR_LINES, sizes=(0.0, 0.6), seeds=4, axes=("vol",), placebos=50, frames=frames)
    rows = {(r["shape"], r["size"]): r for r in out["rows"]}
    assert out["carriers"] == {"vol": "low"} and out["states"]["vol"] == {"None": 10, "high": 39, "low": 31}
    assert out["threshold"] == pytest.approx(0.10 / 642) and out["placebos_per_test"] == 50
    for shape in sl.AXIS_POWER_SHAPES:
        assert rows[(shape, 0.0)]["cell_rule_share"] == 0.0 and rows[(shape, 0.0)]["wake_share"] == 0.0
        assert rows[(shape, 0.6)]["cell_rule_share"] == 1.0 and rows[(shape, 0.6)]["median_carrier_ic"] > 0.3
        assert rows[(shape, 0.6)]["wake_share"] == 0.0, "fifty placebos cannot reach the family's first threshold"
    assert rows[("one", 0.0)]["median_separation"] == rows[("flip", 0.0)]["median_separation"], "size 0 is one series"
    monkeypatch.setattr(sl, "AXIS_POWER_FAMILY", 1)
    loose = sl.axis_power(None, "top30", DOOR_LINES, sizes=(0.0, 0.6), seeds=4, axes=("vol",), placebos=50, frames=frames)
    flip = next(r for r in loose["rows"] if (r["shape"], r["size"]) == ("flip", 0.6))
    assert flip["wake_share"] == 1.0, "a large flip beats every placebo"
    bracket = next(b for b in loose["reading"]["brackets"] if b["shape"] == "flip")
    assert bracket["wake_share"] == {"under": 0.0, "at_or_over": 0.6} and bracket["order"] == "same"


def test_the_axis_power_reading_orders_the_two_brackets_and_stops_on_false_alarms():
    def row(axis, size, wake, rule, p05=5):
        return {"axis": axis, "shape": "one", "size": size, "wake_share": wake, "cell_rule_share": rule,
                "cell_t_alone_share": rule, "p_under_0.05": p05}
    rows = [row("vol", 0.0, 0.0, 0.0), row("vol", 0.05, 0.3, 0.8), row("vol", 0.1, 0.9, 1.0),
            row("heat", 0.0, 0.0, 0.0), row("heat", 0.05, 0.85, 0.2), row("heat", 0.1, 1.0, 0.5)]
    reading = sl.axis_power_reading(rows)
    by = {b["axis"]: b for b in reading["brackets"]}
    assert by["vol"]["order"] == "cells_first" and by["vol"]["cell_rule_share"] == {"under": 0.0, "at_or_over": 0.05}
    assert by["heat"]["order"] == "axis_only" and by["heat"]["cell_rule_share"] == {"under": 0.1, "at_or_over": None}
    assert reading["stop"] is False
    alarm = sl.axis_power_reading([row("vol", 0.0, 0.0, 0.0, p05=13), row("vol", 0.1, 1.0, 1.0)])
    assert alarm["stop"] and alarm["false_alarm_axes"] == ["vol"] and alarm["brackets"] is None
    assert sl.axis_power_reading([row("vol", 0.0, 0.0, 0.0, p05=12)])["stop"] is False


def test_the_axis_power_command_takes_a_tier_and_holds_the_maps_line_to_the_record(monkeypatch, capsys):
    monkeypatch.setattr(sl.xsb, "_in_sandbox", lambda: True)
    assert sl.main(["axis-power"]) == sl.EXIT_USAGE
    market, mids = _mid_world()
    monkeypatch.setattr(sl, "SPAN", MID_TEST_SPAN)
    monkeypatch.setattr(sl.lab, "hold", lambda cache, span=None, *, attach=True, flow=False: market)
    monkeypatch.setattr(sl.lab, "grid", lambda market, names, span=None, **kw: {"cells": []})
    monkeypatch.setattr(sl.lab, "chance", lambda cells: {"7": {"largest_abs_t": 4.1}, "28": {"largest_abs_t": 5.0}})
    called = []
    monkeypatch.setattr(sl, "axis_power", lambda *a, **k: called.append(1))
    assert sl.main(["axis-power", "--tier", "top30"]) == sl.EXIT_BLOCKED and not called, "another line: nothing is read"
    assert "not the record" in capsys.readouterr().out


def test_a_scenario_cell_meets_the_lead_rule_only_over_the_line_steady_and_with_its_all_weeks_sign():
    cells = {"up": {"t": 5.0, "mean_rank_correlation": 0.08}, "down": {"t": -4.5, "mean_rank_correlation": -0.06},
             "thin": {"decisions": 12, "spells": 2, "too_few": True}}
    shown, over, rule = sl.axis_cell_rule(cells, {"mean_rank_correlation": -0.01, "t": -0.3}, 0.9, 4.30)
    assert set(shown) == {"up", "down"} and over == {"up", "down"}
    assert rule == {"down"}, "the up state's sign is not its all-weeks cell's"
    assert sl.axis_cell_rule(cells, {"mean_rank_correlation": 0.01}, 0.9, 4.30)[2] == {"up"}
    assert sl.axis_cell_rule(cells, {"mean_rank_correlation": 0.01}, 0.4, 4.30)[2] == set(), "a rank that does not keep"
    assert sl.axis_cell_rule(cells, {"mean_rank_correlation": 0.01}, None, 4.30)[2] == set()
    assert sl.axis_cell_rule(cells, {"too_few": True}, 0.9, 4.30)[2] == set()
    assert sl.axis_cell_rule(cells, {"mean_rank_correlation": 0.01}, 0.9, 4.6)[1] == {"up"}, "|t| strictly over the line"


def test_an_axis_test_on_a_tier_with_missing_decisions_takes_the_sums_path_and_gives_the_slow_paths_p():
    real = [None] * 5 + (["hi"] * 6 + ["lo"] * 6) * 8
    placebos = sl.axis_placebos(real, "heat", 40)
    rng = random_module.Random("cut")
    x = [rng.gauss(0.05 if s == "hi" else 0.0, 0.1) for s in real]
    for i in range(5, 30):                          # the tier's first labelled decisions have no value
        x[i] = None
    base = {i for i, v in enumerate(x) if v is not None}
    cut = (frozenset(i for i in base if real[i] is not None),
           [[[i for i in g if i in base] for g in p] for p in placebos])
    fast = sl.axis_test(x, real, placebos, cut=cut)
    slow = sl.axis_test(x, real, placebos)
    assert fast["counted"] == slow["counted"] == 40 and fast["p"] == slow["p"]
    assert fast["separation"] == pytest.approx(slow["separation"], rel=1e-9)
    calls = []
    original = sl.split_share
    monkey = lambda xs, gs: calls.append(1) or original(xs, gs)        # noqa: E731
    sl.split_share, saved = monkey, sl.split_share
    try:
        sl.axis_test(x, real, placebos, cut=cut)
        thin_free = sum(1 for p in cut[1] if all(len(g) >= sl.lab.MIN_DECISIONS for g in p))
        assert len(calls) == 1 + (40 - thin_free), "the real axis once, then only the placebos with a thin state"
        calls.clear()
        other = list(x)
        other[40] = None                            # a series with values elsewhere than the cut: the slow path
        sl.axis_test(other, real, placebos, cut=cut)
        assert len(calls) == 41
    finally:
        sl.split_share = saved


def test_a_placebo_left_with_a_thin_state_after_the_cut_is_read_as_the_slow_path_reads_it():
    real = [None] * 5 + (["hi"] * 3 + ["lo"] * 7) * 9
    placebos = sl.axis_placebos(real, "breadth", 200)
    rng = random_module.Random("thin")
    x = [rng.gauss(0.04 if s == "hi" else 0.0, 0.1) for s in real]
    for i in range(5, 20):
        x[i] = None
    base = {i for i, v in enumerate(x) if v is not None}
    cut = (frozenset(i for i in base if real[i] is not None),
           [[[i for i in g if i in base] for g in p] for p in placebos])
    thin = sum(1 for p in cut[1] if any(len(g) < sl.lab.MIN_DECISIONS for g in p))
    assert 0 < thin < 200, "some placebos keep under twenty of a state after the cut"
    fast, slow = sl.axis_test(x, real, placebos, cut=cut), sl.axis_test(x, real, placebos)
    assert fast["counted"] == slow["counted"] and fast["p"] == slow["p"]
