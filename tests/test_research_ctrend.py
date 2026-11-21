from __future__ import annotations

import json
import math
import random
import statistics
from datetime import date, timedelta

import pytest

from scripts.research import cross_section_basket as xsb
from scripts.research import ctrend as ct
from scripts.research import strategy_ladder as sl


def _bars(n=260, start=date(2020, 1, 1), seed=1, gap=None):
    rng, p, out = random.Random(seed), 100.0, {}
    for i in range(n):
        d = start + timedelta(days=i)
        if gap and gap[0] <= d <= gap[1]:
            continue
        o = p
        p = o * math.exp(rng.gauss(0, 0.03))
        out[d] = xsb.Bar(open=o, close=p, quote_volume=1e6 * (1 + rng.random()), high=max(o, p) * 1.01, low=min(o, p) * 0.99)
    return out


def test_there_are_28_indicators_and_their_formulas_hold():
    assert len(ct.NAMES) == 28 and len(set(ct.NAMES)) == 28
    bars = _bars()
    series = ct.indicator_series(bars)
    days = sorted(bars)
    d = days[-1]
    closes = [bars[x].close for x in days]
    vols = [bars[x].quote_volume for x in days]
    row = series[d]
    assert set(row) == set(ct.NAMES), "every indicator has a value after 200 days"
    assert row["sma_20"] == pytest.approx(statistics.fmean(closes[-20:]) / closes[-1])
    assert row["volsma_50"] == pytest.approx(statistics.fmean(vols[-50:]) / vols[-1])
    m, sd = statistics.fmean(closes[-20:]), statistics.pstdev(closes[-20:])
    assert row["boll_high"] == pytest.approx((m + 2 * sd) / closes[-1]) and row["boll_width"] == pytest.approx(4 * sd / m)
    assert 0 <= row["rsi"] <= 100 and 0 <= row["stoch_k"] <= 1 and 0 <= row["stoch_rsi"] <= 1
    moves = [closes[k] - closes[k - 1] for k in range(len(closes) - 14, len(closes))]
    gain, loss = sum(x for x in moves if x > 0) / 14, sum(-x for x in moves if x < 0) / 14
    assert row["rsi"] == pytest.approx(100 - 100 / (1 + gain / loss))
    assert "sma_200" not in series[days[198]] and "sma_200" in series[days[199]], "a value needs its whole window"


def test_the_indicators_read_only_the_day_and_before_and_never_span_a_gap():
    bars = _bars(260)
    day = sorted(bars)[230]
    before = ct.Indicators(_market({"A": bars}))("A", day)
    moved = dict(bars)
    for d in sorted(bars)[231:]:
        moved[d] = xsb.Bar(open=1.0, close=1.0, quote_volume=1.0, high=1.0, low=1.0)
    assert ct.Indicators(_market({"A": moved}))("A", day) == before
    gapped = _bars(260, gap=(date(2020, 3, 1), date(2020, 3, 1)))
    after = ct.Indicators(_market({"A": gapped}))("A", date(2020, 3, 8))           # a Sunday: only Sundays are kept
    assert "sma_20" not in after and "sma_5" in after, "the run restarts after the missing day"
    assert ct.Indicators(_market({"A": gapped}))("A", date(2020, 3, 9)) == {}, "a weekday is not a decision"


def _market(paths):
    m = xsb.Market()
    m.bars.update(paths)
    return m


def test_ranks_are_centred_and_a_missing_value_sits_at_the_middle():
    assert ct.centred_ranks({"a": 1.0, "b": 2.0, "c": 3.0}) == {"a": -0.5, "b": 0.0, "c": 0.5}
    assert ct.centred_ranks({"a": 1.0, "b": 1.0, "c": 3.0}) == {"a": -0.25, "b": -0.25, "c": 0.5}
    assert ct.centred_ranks({"a": 5.0}) == {"a": 0.0}


def test_the_univariate_regression_is_weighted_least_squares():
    rows = [("s%d" % i, (x, 0.0), w, y) for i, (x, w, y) in enumerate([(-0.5, 1, -1.0), (0.0, 2, 0.5), (0.5, 1, 1.0)])]
    wk = ct.Week(date(2021, 1, 3), rows)
    a, b = ct.univariate(wk, 0)
    xs, ws, ys = [-0.5, 0.0, 0.5], [1, 2, 1], [-1.0, 0.5, 1.0]
    mx = sum(w * x for x, w in zip(xs, ws)) / 4
    my = sum(w * y for y, w in zip(ys, ws)) / 4
    slope = sum(w * (x - mx) * (y - my) for x, w, y in zip(xs, ws, ys)) / sum(w * (x - mx) ** 2 for x, w in zip(xs, ws))
    assert b == pytest.approx(slope) and a == pytest.approx(my - slope * mx)
    assert ct.univariate(wk, 1) is None, "no spread, no slope"


def _week(day, n=25, p=28, rng=None, signal=None):
    rng = rng or random.Random(day.toordinal())
    rows = []
    for i in range(n):
        x = tuple(rng.random() - 0.5 for _ in range(p))
        y = (signal(x) if signal else 0.0) + rng.gauss(0, 0.05)
        rows.append((f"c{i}", x, 1.0 + rng.random(), y))
    return ct.Week(day, rows)


def test_the_window_holds_only_closed_valid_weeks_and_at_most_52():
    days = [date(2020, 1, 5) + timedelta(days=7 * i) for i in range(70)]
    table = {d: _week(d) for d in days}
    table[days[10]] = ct.Week(days[10], table[days[10]].rows[:5])            # too few coins: not valid
    day = days[60]
    past = ct.window(table, day)
    assert len(past) <= 52 and all(w.day + timedelta(days=8) <= day for w in past)
    assert days[59] not in [w.day for w in past], "last week's target window is not closed yet"
    assert days[10] not in [w.day for w in ct.window(table, days[40])]


def test_the_window_sums_rebuild_the_rows_gram_and_the_net_keeps_the_informative_forecast():
    rng = random.Random(3)
    weeks = [_week(date(2020, 1, 5) + timedelta(days=7 * i), rng=rng, signal=lambda x: -0.8 * x[2] + 0.4 * x[5])
             for i in range(30)]
    total = ct.Stats(28)
    for w in weeks:
        total.add(w.stats())
    rows = [(x, wt, y) for w in weeks for _s, x, wt, y in w.rows]
    direct = ct.Stats.of(rows)
    assert total.sxx[2][5] == pytest.approx(direct.sxx[2][5]) and total.n == direct.n
    signs = [1] * 28
    signs[2] = -1                                    # the column's forecast slope is negative: its sign flips
    keep, gram, corr, vy, n = ct.standardised(total, signs)
    assert gram[2][2] == pytest.approx(1.0) and corr[2] > 0
    kept = ct.elastic_net_positive(keep, gram, corr, vy, n)
    assert 2 in kept and 5 in kept
    assert len(kept) < 28


def test_forecasts_wait_for_26_weeks_and_fall_back_to_the_plain_mean():
    days = [date(2020, 1, 5) + timedelta(days=7 * i) for i in range(40)]
    rng = random.Random(4)
    table = {d: _week(d, rng=rng, signal=lambda x: x[0]) for d in days}
    scores, notes = ct.forecasts(table)
    first = min(scores)
    assert len(ct.window(table, first)) >= 26 and len(ct.window(table, first - timedelta(days=7))) < 26
    assert notes[first]["kept"] >= 1
    flat = {d: _week(d, rng=random.Random(5)) for d in days}
    for w in flat.values():
        w.rows = [(s, x, wt, 0.0) for s, x, wt, _y in w.rows]
        w._stats = None
    scores, notes = ct.forecasts(flat)
    assert all(n["fallback"] for n in notes.values()), "nothing to keep: the plain mean of every forecast"


def test_the_counts_build_no_book_and_stop_on_a_late_first_forecast(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("counts must not build a book")
    monkeypatch.setattr(sl, "measure", refuse)
    monkeypatch.setattr(sl, "simulate_held", refuse)
    days = [date(2020, 1, 5) + timedelta(days=7 * i) for i in range(40)]
    table = {d: _week(d, rng=random.Random(6)) for d in days}
    _scores, notes = ct.forecasts(table)
    out = ct.counts(table, notes)
    assert out["first_forecast"] is not None and out["stop"]["stops"] is False
    late = {d: n for d, n in notes.items() if d > date(2021, 7, 1)}
    assert ct.counts(table, late)["stop"]["stops"] is True
    assert ct.counts(table, {})["stop"]["stops"] is True


def test_the_overlap_compares_each_market_with_its_own_struck_books(monkeypatch):
    seen = {}
    monkeypatch.setattr(sl, "_beside", lambda market, tier, mine, others: seen.setdefault("o", sorted(others)) and
                        {n: {"decisions": 3, "median_cosine": 0.1, "closes": False} for n in others})
    out = ct.overlap(xsb.Market(), {}, "bars", {})
    assert "S-MOM30" in out["compared"] and "momentum_30" in out["compared"] and "imbalance_1_30" not in out["compared"]
    seen.clear()
    out = ct.overlap(xsb.Market(), {}, "depth", {})
    assert out["compared"] == ["depth_growth_30", "imbalance_1_30", "imbalance_5_30"] and out["passes"] is True
    monkeypatch.setattr(sl, "_beside", lambda market, tier, mine, others:
                        {n: {"decisions": 0 if n == "imbalance_5_30" else 3, "median_cosine": 0.1, "closes": False}
                         for n in others})
    out = ct.overlap(xsb.Market(), {}, "depth", {})
    assert out["blocked"] is True and out["passes"] is False and out["closed_by"] == [], "not compared is not closed"


def test_the_null_count_and_the_plant_reading():
    assert ct.null_count(60) == 200 and ct.null_count(180) == 120 and ct.null_count(300) == 72
    rows = {}
    for rho in ct.PLANT_RHOS:
        for k in range(30):
            hit = (rho == 0.0 and k < 4) or (rho == 0.2 and k < 25)
            rows[f"{rho}|{k}"] = {"rho": rho, "annual_sharpe": 2.0 if hit else -1.0, "clears_entry": hit}
    assert ct.plant_reading(rows, [0.0] * 150)["void"] is True
    for k in range(4):
        rows[f"0.0|{k}"]["annual_sharpe"] = -1.0
    out = ct.plant_reading(rows, [0.0] * 150)
    assert out["void"] is False and out["smallest_seen"] == 0.2


def test_the_commands_refuse_outside_the_sandbox_and_out_of_order(monkeypatch, tmp_path):
    assert ct.main(["ctrend-counts"]) == ct.EXIT_USAGE
    monkeypatch.setattr(ct.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setenv("RESEARCH_CACHE", str(tmp_path))

    def put(name, body):
        path = tmp_path / name
        path.write_text(json.dumps(body))
        return str(path)
    ok = put("c.json", {"counts": {"stop": {"stops": False}}})
    stopped = put("cs.json", {"counts": {"stop": {"stops": True}}})
    assert ct.main(["ctrend-overlap", "--market", "bars"]) == ct.EXIT_USAGE
    assert ct.main(["ctrend-overlap", "--market", "bars", "--counts", stopped]) == ct.EXIT_USAGE
    over = ",".join(put(f"o{m}.json", {"market": m, "passes": True}) for m in ct.OVERLAP_MARKETS)
    two = ",".join(put(f"t{m}.json", {"market": m, "passes": True}) for m in ("bars", "depth"))
    closed = ",".join(put(f"x{m}.json", {"market": m, "passes": m != "depth"}) for m in ct.OVERLAP_MARKETS)
    blocked = ",".join(put(f"b{m}.json", {"market": m, "passes": False, "blocked": m == "depth"}) for m in ct.OVERLAP_MARKETS)
    assert ct.main(["ctrend-time", "--counts", ok, "--overlap", blocked]) == ct.EXIT_BLOCKED
    assert ct.main(["ctrend-time", "--counts", ok, "--overlap", two]) == ct.EXIT_USAGE
    assert ct.main(["ctrend-time", "--counts", ok, "--overlap", closed]) == ct.EXIT_BLOCKED
    assert ct.main(["ctrend-null", "--counts", ok, "--overlap", over, "--seeds", "0:5"]) == ct.EXIT_USAGE, "needs --time"
    few = put("few.json", {"null_count": 80})
    assert ct.main(["ctrend-null", "--counts", ok, "--overlap", over, "--time", few, "--seeds", "0:5"]) == ct.EXIT_BLOCKED
    timed = put("time.json", {"null_count": 150})
    assert ct.main(["ctrend-null", "--counts", ok, "--overlap", over, "--time", timed, "--seeds", "0:151"]) == ct.EXIT_USAGE
    short = put("n.json", {"books": {"0": {"annual_sharpe": 0.1}}})
    assert ct.main(["ctrend-book", "--counts", ok, "--overlap", over, "--time", timed, "--null", short]) == ct.EXIT_USAGE
    assert ct.main(["ctrend-counts"]) == ct.EXIT_BLOCKED, "no contract types in an empty cache"


def test_end_to_end_scores_to_a_day_do_not_move_when_later_bars_do():
    coins = {f"C{i:02d}": _bars(420, seed=100 + i) for i in range(24)}
    members = {date(2020 + (m - 1) // 12, (m - 1) % 12 + 1, 1): sorted(coins) for m in range(1, 16)}
    scores, notes = ct.forecasts(ct.weeks(_market(coins), ct.real_inputs(_market(coins)), members))
    assert scores, "the synthetic market reaches a forecast"
    cut = sorted(scores)[len(scores) // 2]
    moved = {s: {d: (b if d <= cut else xsb.Bar(open=b.open * 3, close=b.close * 0.2, quote_volume=b.quote_volume * 9,
                                                high=b.high * 3, low=b.low * 0.2)) for d, b in bars.items()}
             for s, bars in coins.items()}
    again, _ = ct.forecasts(ct.weeks(_market(moved), ct.real_inputs(_market(moved)), members))
    for day in scores:
        if day <= cut:
            assert again[day] == pytest.approx(scores[day]), day
    assert any(again.get(d) != scores[d] for d in scores if d > cut), "later decisions do read the later bars"
    table = ct.weeks(_market(coins), ct.real_inputs(_market(coins)), members)
    cov = ct.coverage(table)
    assert cov["sma_5"] > cov["sma_200"] > 0.0
