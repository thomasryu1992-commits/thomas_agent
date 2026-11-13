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

