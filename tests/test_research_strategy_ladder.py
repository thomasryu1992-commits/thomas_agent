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
    _oi_zip(tmp_path, "AUSDT", date(2022, 1, 1), [("2022-01-01 00:00:00", 5), ("2022-01-01 23:55:00", 100)])
    _oi_zip(tmp_path, "AUSDT", date(2022, 1, 2), [("2022-01-02 00:00:00", 7), ("2022-01-02 12:00:00", 8)])
    _oi_zip(tmp_path, "AUSDT", date(2022, 1, 4), [("2022-01-04 23:55:00", 400)])
    (tmp_path / "AUSDT" / "AUSDT-metrics-2022-01-05.zip").write_bytes(b"not a zip")
    (tmp_path / "AUSDT" / "AUSDT-metrics-2022-01-03.zip").write_bytes(b"not a zip")
    got = sl.closing_oi(tmp_path, ["AUSDT", "NOFILESUSDT"], span)
    assert got == {"AUSDT": {date(2021, 12, 31): 90.0, date(2022, 1, 1): 100.0}}, (
        "the day before the span is read for the first change; a day whose last row is at noon has no closing value; "
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
