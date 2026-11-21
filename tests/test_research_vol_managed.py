from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta

import pytest

from scripts.research import basket_search as bs
from scripts.research import cross_section_basket as xsb
from scripts.research import strategy_ladder as sl
from scripts.research import trend_book_search as tbs
from scripts.research import vol_managed as vm


def _days(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _market(n=4, start=date(2020, 1, 1), end=date(2020, 4, 30), sd=0.03, seed=1):
    rng, market = random.Random(seed), xsb.Market()
    for i in range(n):
        p, bars = 100.0, {}
        for d in _days(start, end):
            p *= math.exp(rng.gauss(0, sd))
            bars[d] = xsb.Bar(open=p, close=p, quote_volume=1e6)
        market.bars[f"C{i}"] = bars
    return market


def _sundays(n, first=date(2020, 1, 5)):
    return [first + timedelta(days=7 * i) for i in range(n)]


def test_the_book_vol_reads_only_closes_to_the_day():
    market = _market()
    share = {s: 0.25 for s in market.bars}
    day = date(2020, 3, 1)
    sigma = vm.book_vol(market, share, day)
    for s in market.bars:
        for d in _days(day + timedelta(days=1), date(2020, 4, 30)):
            market.bars[s][d] = xsb.Bar(open=1.0, close=1.0, quote_volume=1.0)
    assert sigma is not None and vm.book_vol(market, share, day) == sigma


def test_the_book_vol_is_the_share_weighted_daily_return_sd():
    market = xsb.Market()
    days = list(_days(date(2020, 1, 1), date(2020, 2, 15)))
    up = {d: 100.0 * (1.01 ** i) for i, d in enumerate(days)}
    flip = {d: 100.0 * (1.02 if i % 2 else 1.0) for i, d in enumerate(days)}
    market.bars["A"] = {d: xsb.Bar(c, c, 1e6) for d, c in up.items()}
    market.bars["B"] = {d: xsb.Bar(c, c, 1e6) for d, c in flip.items()}
    day = days[-1]
    got = vm.book_vol(market, {"A": 0.5, "B": 0.5}, day)
    rets = []
    for k in range(30):
        a = up[day - timedelta(days=k)] / up[day - timedelta(days=k + 1)] - 1
        b = flip[day - timedelta(days=k)] / flip[day - timedelta(days=k + 1)] - 1
        rets.append(0.5 * a + 0.5 * b)
    import statistics
    assert got == pytest.approx(statistics.stdev(rets))


def test_the_scale_reads_only_earlier_decisions_caps_at_two_and_waits_for_history(monkeypatch):
    days = _sundays(20)
    sigmas = {d: 0.02 for d in days[:13]}
    sigmas.update({days[13]: 0.04, days[14]: 0.005, days[15]: None, days[16]: 0.02})
    monkeypatch.setattr(tbs, "shares", lambda market, members, day, lookback: ({"A": 1.0}, {"A": 0.1}))
    monkeypatch.setattr(vm, "book_vol", lambda market, share, day: sigmas.get(day, 0.02))
    table = vm.scales(xsb.Market(), {}, days)
    assert all(table[d]["k"] == 1.0 and table[d]["target"] is None for d in days[:13]), "under 13 earlier values"
    assert table[days[13]]["target"] == 0.02 and table[days[13]]["k"] == pytest.approx(0.5)
    assert table[days[14]]["k"] == vm.CAP, "0.02 / 0.005 = 4, capped"
    assert table[days[15]]["k"] == 1.0, "no σ: scale 1"
    assert table[days[16]]["target"] == pytest.approx(0.02), "median of 13×0.02, 0.04, 0.005: the own σ is not in it"


def test_the_excess_is_twice_k_minus_one_on_the_shares():
    assert vm.excess_weights({"A": 0.25, "B": 0.75}, 1.5) == pytest.approx({"A": 0.25, "B": 0.75})
    assert vm.excess_weights({"A": 0.25, "B": 0.75}, 0.5) == pytest.approx({"A": -0.25, "B": -0.75})
    assert vm.excess_weights({"A": 1.0}, 1.0) == {}
    assert sum(abs(v) for v in vm.excess_weights({"A": 0.4, "B": 0.6}, 2.0).values()) == bs.POSITION_TOTAL


def test_the_null_shuffles_whole_13_week_blocks_and_keeps_each_decisions_shares():
    days = _sundays(39)                                   # three whole blocks
    table = {d: {"share": {f"S{i}": 1.0}, "sigma": 0.01, "target": 0.01, "k": 0.5 + i / 100} for i, d in enumerate(days)}
    one = vm.shuffled(table, days, 3)
    assert one == vm.shuffled(table, days, 3) and one != vm.shuffled(table, days, 4)
    assert sorted(one.values()) == sorted(r["k"] for r in table.values())
    ks = [one[d] for d in days]
    blocks = [ks[i:i + 13] for i in range(0, 39, 13)]
    for block in blocks:
        assert all(round(b - a, 9) == 0.01 for a, b in zip(block, block[1:])), "a block keeps its order"
    w = vm.weigher(table, one)(xsb.Market(), [], days[0], "S-MOM30")
    assert w == pytest.approx({"S0": 2 * (one[days[0]] - 1)})


def test_the_overlap_closes_on_a_scale_that_follows_the_downtrend_share(monkeypatch):
    days = _sundays(30)
    rng = random.Random(2)
    down = {d: rng.random() for d in days}

    def shares(market, members, day, lookback):
        return {"A": down[day], "B": 1 - down[day]}, {"A": -0.1, "B": 0.1}
    monkeypatch.setattr(tbs, "shares", shares)
    row = lambda k: {"share": {"A": 1.0}, "k": k, "target": 0.01, "sigma": 0.01}   # noqa: E731
    follow = {d: row(1.0 - down[d]) for d in days}
    out = vm.overlap(xsb.Market(), follow, {}, days)
    assert out["trend"]["T01"]["correlation"] == pytest.approx(1.0) and out["passes"] is False
    apart = {d: row(0.5 + rng.random()) for d in days}
    out = vm.overlap(xsb.Market(), apart, {}, days)
    assert out["trend"]["T01"]["weeks"] == 30 and out["passes"] is (out["trend"]["T01"]["correlation"] < 0.5)
    for d in days[:12]:                                   # a scale of 1 for want of history is not read
        follow[d] = {"share": {"A": 1.0}, "k": 1.0, "target": None, "sigma": 0.01}
    out = vm.overlap(xsb.Market(), follow, {}, days)
    assert out["trend"]["T01"]["weeks"] == 18 and out["trend"]["T01"]["unread_scale_weeks"] == 12
    assert out["trend"]["T01"]["correlation"] == pytest.approx(1.0)


def test_the_counts_read_no_return_and_stop_on_a_still_scale(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("counts must not simulate a book")
    monkeypatch.setattr(xsb, "simulate", refuse)
    monkeypatch.setattr(sl, "simulate_held", refuse)
    days = _sundays(10)
    still = {d: {"share": {"A": 1.0}, "sigma": 0.01, "target": None, "k": 1.0} for d in days}
    out = vm.counts(still, days)
    assert out["stop"]["stops"] is True and out["years"]["2020"]["no_history"] == 10
    moving = {d: {"share": {"A": 1.0}, "sigma": 0.01, "target": 0.01, "k": (0.5 if i % 2 else 1.5)}
              for i, d in enumerate(days)}
    out = vm.counts(moving, days)
    assert out["mean_abs_k_minus_1"] == 0.5 and out["years"]["2020"]["below_one"] == 5
    unit = 8 / 10_000
    paper = (1.0 + 9 * 2.0) * unit
    assert out["paper_cost_total_position_annual_pct"] == pytest.approx(round(paper * 100 / 2 / 5, 3))
    assert out["years"]["2020"]["mean_k_minus_1"] == 0.0 and out["stop"]["stops"] is False
    levered = {d: {"share": {"A": 1.0}, "sigma": 0.01, "target": 0.01, "k": 1.3} for d in days}
    out = vm.counts(levered, days)
    assert out["largest_year_drift"] == pytest.approx(0.3) and out["stop"]["stops"] is True, "a year-long bet stops"


def test_the_door_asks_for_the_recorded_numbers_to_the_digit(monkeypatch):
    seen = []

    def run(market, members, weigh):
        seen.append(weigh)
        return {"annual_sharpe": 0.056, "sum_pct": 21.86} if len(seen) == 1 else {"annual_sharpe": 0.677,
                                                                                   "sum_pct": 256.66}
    monkeypatch.setattr(vm, "run", run)
    out = vm.door(xsb.Market(), {})
    assert out["books"]["T01"]["same"] is True and out["books"]["always_long"]["same"] is False
    assert out["passes"] is False


def test_the_span_is_the_trend_books_and_the_book_is_judged_on_all_three():
    assert vm.SPAN == tbs.DEV == (date(2020, 1, 1), date(2024, 12, 31))
    assert len(bs._complete_months(*vm.SPAN)) == 60
    assert vm.decisions(*vm.SPAN)[0] == date(2020, 1, 5), "2020-01-01 is a Wednesday: the first decision is the Sunday after"
    good = {"clears_entry": True, "later_years": {"passes": True}, "annual_sharpe": 1.2}
    assert vm.judge(good, [0.0] * 600)["candidate"] is True
    assert vm.judge(good, [1.3] * 30 + [0.0] * 570)["null"]["p"] == pytest.approx(round(31 / 601, 4))
    assert vm.judge(good, [1.3] * 30 + [0.0] * 570)["candidate"] is False
    assert vm.null_p(None, [0.0]) is None


def test_the_commands_refuse_outside_the_sandbox_and_out_of_order(monkeypatch, tmp_path):
    assert vm.main(["vol-overlap"]) == vm.EXIT_USAGE
    monkeypatch.setattr(vm.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setenv("RESEARCH_CACHE", str(tmp_path))

    def put(name, body):
        path = tmp_path / name
        path.write_text(json.dumps(body))
        return str(path)
    ok_overlap = put("o.json", {"overlap": {"passes": True}})
    bad_overlap = put("ob.json", {"overlap": {"passes": False}})
    ok_counts = put("c.json", {"counts": {"stop": {"stops": False}}})
    ok_door = put("d.json", {"door": {"passes": True}})
    assert vm.main(["vol-counts"]) == vm.EXIT_USAGE
    assert vm.main(["vol-counts", "--overlap", bad_overlap]) == vm.EXIT_USAGE
    assert vm.main(["vol-door", "--overlap", ok_overlap]) == vm.EXIT_USAGE, "needs the counts"
    assert vm.main(["vol-null", "--seeds", "0:601", "--overlap", ok_overlap, "--counts", ok_counts,
                    "--door", ok_door]) == vm.EXIT_USAGE
    assert vm.main(["vol-null", "--seeds", "0:5", "--overlap", ok_overlap, "--counts", ok_counts]) == vm.EXIT_USAGE
    short = put("n.json", {"books": {"0": {"annual_sharpe": 0.1}}})
    assert vm.main(["vol-book", "--overlap", ok_overlap, "--counts", ok_counts, "--door", ok_door,
                    "--null", short]) == vm.EXIT_USAGE
    assert vm.main(["vol-overlap"]) == vm.EXIT_BLOCKED, "no contract types in an empty cache"


def test_end_to_end_the_excess_earns_twice_k_minus_one_of_the_base_and_the_null_covers_every_decision():
    start, end = date(2020, 3, 1), date(2020, 6, 30)
    rng, market = random.Random(9), xsb.Market()
    for i in range(6):
        p, bars = 100.0, {}
        for d in _days(date(2019, 10, 1), date(2020, 7, 1)):
            o = p * math.exp(rng.gauss(0, 0.01))
            p = o * math.exp(rng.gauss(0, 0.03 * (1 + i / 3)))
            bars[d] = xsb.Bar(open=o, close=p, quote_volume=1e6)
        market.bars[f"C{i}"] = bars
    names = sorted(market.bars)
    members = {date(2019, m, 1): names for m in (10, 11, 12)} | {date(2020, m, 1): names for m in range(1, 8)}
    table = vm.scales(market, members, vm.decisions(date(2019, 11, 1), end))
    span_days = vm.decisions(start, end)
    assert any(table[d]["k"] != 1.0 for d in span_days), "the scale moves inside the test span"
    base = xsb.simulate(market, bs.CADENCE, members, start=start, end=end, weigher=tbs.book(vm.LOOKBACK, tbs.ALWAYS_LONG))
    excess = xsb.simulate(market, bs.CADENCE, members, start=start, end=end, weigher=vm.weigher(table))
    for day, row in base["daily"].items():
        decision = max(d for d in span_days if d < day)
        k = table[decision]["k"]
        assert excess["daily"].get(day, {"gross": 0.0})["gross"] == pytest.approx(2 * (k - 1) * row["gross"], abs=1e-12)
    null = xsb.simulate(market, bs.CADENCE, members, start=start, end=end,
                        weigher=vm.weigher(table, vm.shuffled(table, span_days, 0)))
    assert null["daily"], "every decision the engine asks for has a shuffled scale"


def test_the_planted_scale_is_the_real_scales_rearranged_to_follow_the_next_week():
    days = _sundays(60)
    rng = random.Random(4)
    table = {d: {"share": {"A": 1.0}, "k": 0.4 + rng.random(), "target": 0.01, "sigma": 0.01} for d in days}
    weeks = {d: rng.gauss(0, 0.05) for d in days[:-1]}             # the last decision holds no full week
    one = vm.planted(table, days, weeks, 0.10, 7)
    assert one == vm.planted(table, days, weeks, 0.10, 7) and one != vm.planted(table, days, weeks, 0.10, 8)
    assert sorted(one[d] for d in days[:-1]) == sorted(table[d]["k"] for d in days[:-1])
    assert one[days[-1]] == table[days[-1]]["k"], "a decision without a week keeps its own scale"
    sure = vm.planted(table, days, weeks, 1.0, 0)
    order = sorted(days[:-1], key=lambda d: weeks[d])
    assert [sure[d] for d in order] == sorted(sure[d] for d in order), "at rho 1 the largest scale meets the best week"


def test_the_plant_reading_counts_seeds_against_the_null():
    nulls = [0.0] * 590 + [2.0] * 10
    rows = {f"0.1|{k}": {"rho": 0.1, "seed": k, "annual_sharpe": 1.5 if k < 45 else -1.0,
                         "clears_entry": k < 20} for k in range(50)}
    rows |= {f"0.05|{k}": {"rho": 0.05, "seed": k, "annual_sharpe": -1.0, "clears_entry": False} for k in range(50)}
    out = vm.plant_reading(rows, nulls)
    assert out["by_rho"]["0.1"] == {"seeds": 50, "p_at_alpha": 45, "and_entry": 20, "sees": True}
    assert out["by_rho"]["0.05"]["sees"] is False and out["by_rho"]["0.2"]["seeds"] == 0
    assert out["smallest_seen"] == 0.1


def test_the_plant_and_the_book_refuse_without_their_inputs(monkeypatch, tmp_path):
    monkeypatch.setattr(vm.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setenv("RESEARCH_CACHE", str(tmp_path))

    def put(name, body):
        path = tmp_path / name
        path.write_text(json.dumps(body))
        return str(path)
    gates = ["--overlap", put("o.json", {"overlap": {"passes": True}}),
             "--counts", put("c.json", {"counts": {"stop": {"stops": False}}}),
             "--door", put("d.json", {"door": {"passes": True}})]
    assert vm.main(["vol-plant", "--rho", "0.3", "--seeds", "0:5", *gates]) == vm.EXIT_USAGE
    assert vm.main(["vol-plant", "--rho", "0.1", "--seeds", "0:51", *gates]) == vm.EXIT_USAGE
    full = put("n.json", {"books": {str(k): {"annual_sharpe": 0.1} for k in range(600)}})
    assert vm.main(["vol-book", *gates, "--null", full]) == vm.EXIT_USAGE, "needs every planted book"
    some = put("p.json", {"books": {f"0.1|{k}": {"rho": 0.1} for k in range(50)}})
    assert vm.main(["vol-book", *gates, "--null", full, "--plant", some]) == vm.EXIT_USAGE
