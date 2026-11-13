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
    assert len(nulls) == sl.SWEEP_NULLS == 300 and not set(nulls) & set(sl.lab.controls())
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
    for only in ("null", "price_volume,funding,coin_character", "trade_flow", "positioning,depth,spot"):
        assert sl.main(["sweep", "--only", only]) == sl.EXIT_BLOCKED
    assert asked == [(False, False), (False, False), (False, True), (True, False)]


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
