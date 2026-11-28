from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pytest

from scripts.research import cross_section_basket as xsb
from scripts.research import pairs_value as pv
from scripts.research import strategy_ladder as sl

START = date(2020, 1, 1)
END = date(2021, 3, 31)


def _days(start=START, end=END):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _market(paths: dict[str, dict[date, float]]) -> xsb.Market:
    market = xsb.Market()
    for s, closes in paths.items():
        market.bars[s] = {d: xsb.Bar(open=c, close=c, quote_volume=1e6) for d, c in closes.items()}
    return market


def _walk(seed, start=START, end=END, base=100.0, sd=0.02):
    rng, p, out = random.Random(seed), base, {}
    for d in _days(start, end):
        p *= math.exp(rng.gauss(0, sd))
        out[d] = p
    return out


def _pair(s="A", t="B", *, z_on=(), seed=1):
    """B a random walk; A = B times a small noise, jolted on the given days so the spread's z is large there."""
    b = _walk(seed)
    rng = random.Random(seed + 100)
    a = {d: c * math.exp(rng.gauss(0, 0.005)) for d, c in b.items()}
    for d, k in z_on:
        a[d] = b[d] * math.exp(k)
    return {s: a, t: b}


def _p(a="A", b="B", sd=0.01):
    return pv.Pair(a, b, anchor=0.0, mean=0.0, sd=sd, distance=0.0)


def test_formation_reads_only_the_closes_before_the_month():
    paths = {**_pair(), "C": _walk(7), "D": _walk(8)}
    month = date(2021, 2, 1)
    before = pv.formation(_market(paths), list(paths), month)
    moved = {s: {d: (c * (5.0 if d >= month else 1.0)) for d, c in v.items()} for s, v in paths.items()}
    assert pv.formation(_market(moved), list(paths), month) == before
    assert len(before) == 6 and pv.nearest(before, 1)[0].key == ("A", "B"), "the near-clone pair is the nearest"


def test_formation_needs_330_closes_for_both_coins():
    paths = _pair()
    month = date(2021, 2, 1)
    paths["B"] = {d: c for d, c in paths["B"].items() if not (date(2020, 6, 1) <= d < date(2020, 7, 10))}   # 39 gone
    assert pv.formation(_market(paths), ["A", "B"], month) == []
    paths["B"].update({d: 100.0 for d in _days(date(2020, 6, 1), date(2020, 6, 9))})                     # 30 gone
    assert len(pv.formation(_market(paths), ["A", "B"], month)) == 1


def test_z_reads_only_the_decision_close():
    market = _market(_pair())
    pair = pv.formation(market, ["A", "B"], date(2021, 2, 1))[0]
    day = date(2021, 2, 10)
    z = pv.z_score(market, pair, day)
    for s in ("A", "B"):
        market.bars[s][day + timedelta(days=1)] = xsb.Bar(open=1.0, close=1.0, quote_volume=1.0)
    assert pv.z_score(market, pair, day) == z


def _flat(days, a_moves=None):
    """A and B flat at 100; A multiplied by e^(0.01 * k) on the given days: z = k against a pair of sd 0.01."""
    a_moves = a_moves or {}
    return _market({"A": {d: 100.0 * math.exp(0.01 * a_moves.get(d, 0.0)) for d in days},
                    "B": {d: 100.0 for d in days}})


def _run(market, days, chosen):
    trader = pv.Trader(market, chosen)
    return trader, [trader.decide(d) for d in days]


def test_a_pair_opens_at_two_and_closes_at_zero_short_the_rich_leg():
    days = list(_days(date(2021, 1, 1), date(2021, 1, 20)))
    z = {days[2]: 2.5, days[3]: 1.0, days[4]: 0.0}
    trader, weights = _run(_flat(days, z), days, {date(2021, 1, 1): [_p()]})
    assert weights[1] == {} and weights[2] == pytest.approx({"A": -0.1 * math.exp(0.025) / math.exp(0.025), "B": 0.1})
    assert weights[3] != {} and weights[4] == {}
    assert [e["reason"] for e in trader.events] == ["open", "zero"] and trader.events[1]["held"] == 2


def test_the_twentieth_decision_closes_and_blocks_until_zero():
    days = list(_days(date(2021, 1, 1), date(2021, 1, 31)))
    z = {d: -3.0 for d in days[1:25]}             # cheap A for 24 days, then back to zero
    trader, weights = _run(_flat(days, z), days, {date(2021, 1, 1): [_p()]})
    assert weights[1]["A"] > 0 and weights[21] == {}, "opened at decision 1, closed at the twentieth after it"
    assert weights[22] == {} and weights[24] == {}, "still at -3: not reopened before z reaches zero"
    assert [e["reason"] for e in trader.events] == ["open", "twenty"] and trader.events[1]["held"] == 20


def test_a_month_change_closes_unlisted_pairs_and_a_missing_leg_closes():
    days = list(_days(date(2021, 1, 25), date(2021, 2, 5)))
    z = {d: 3.0 for d in days}
    market = _flat(days, z)
    chosen = {date(2021, 1, 1): [_p()], date(2021, 2, 1): [_p("A", "C")]}
    market.bars["C"] = {d: xsb.Bar(100.0, 100.0, 1e6) for d in days}
    trader, weights = _run(market, days, chosen)
    assert [(e["reason"], e["closed"]) for e in trader.events if e["reason"] != "open"][0] == ("month", date(2021, 2, 1))
    del market.bars["A"][date(2021, 2, 4)]
    trader, _ = _run(market, days, chosen)
    assert ("missing", date(2021, 2, 4)) in [(e["reason"], e["closed"]) for e in trader.events]


def test_a_pair_closed_at_a_decision_is_not_reopened_at_it_and_nothing_before_the_span():
    days = list(_days(date(2020, 12, 30), date(2021, 1, 10)))
    z = {d: 3.0 for d in days}
    market = _flat(days, z)
    chosen = {date(2020, 12, 1): [_p()], date(2021, 1, 1): [_p()]}
    trader, weights = _run(market, days, chosen)
    assert weights[0] == {} and weights[1] == {}, "the span starts 2021-01-01; simulate_held asks for 2020-12-31 first"
    assert weights[2] != {}
    del market.bars["A"][date(2021, 1, 5)]
    trader, weights = _run(market, days, chosen)
    i = days.index(date(2021, 1, 5))
    assert weights[i] == {} and weights[i + 1] != {}, "closed for the missing leg, reopened at the next decision"
    with pytest.raises(ValueError):
        trader.decide(date(2021, 1, 1))


def test_two_pairs_on_one_coin_are_netted():
    days = [date(2021, 1, 1)]
    market = _market({"A": {days[0]: 100.0 * math.exp(0.03)}, "B": {days[0]: 100.0}, "C": {days[0]: 100.0 * math.exp(-0.03)}})
    trader, weights = _run(market, days, {date(2021, 1, 1): [_p("A", "B"), _p("B", "C", sd=0.01)]})
    assert weights[0] == pytest.approx({"A": -0.1, "C": 0.1}), "B is long in one pair and short in the other: nothing left on it"


def test_the_null_draws_from_the_same_eligible_pairs_ignoring_distance():
    pairs = [pv.Pair(f"S{i}", f"T{i}", 0.0, 0.0, 1.0, float(i)) for i in range(40)]
    month = date(2021, 3, 1)
    one, again = pv.random_pairs(pairs, "pairs-null-3", month), pv.random_pairs(pairs, "pairs-null-3", month)
    assert one == again and len(one) == 10 and set(one) <= set(pairs)
    assert one != pv.nearest(pairs) and pv.random_pairs(pairs, "pairs-null-4", month) != one


def test_the_planted_leg_reverts_at_the_given_half_life():
    days = list(_days(date(2020, 1, 1), date(2024, 12, 31)))
    market = _market({"A": _walk(1, days[0], days[-1]), "B": _walk(2, days[0], days[-1])})
    sizes = {date(2021, 1, 1): 0.05}
    for h in (2, 10):
        bars, path = pv.plant_leg(market, "A", "B", h, sizes, random.Random(5))
        x = [math.log(bars[d].close / market.bars["B"][d].close) for d in days]
        x = [v - x[0] for v in x]
        assert x == pytest.approx(path, abs=1e-9)
        num = sum(x[i] * x[i - 1] for i in range(1, len(x)))
        phi = num / sum(v * v for v in x[:-1])
        assert phi == pytest.approx(0.5 ** (1 / h), abs=0.06)
        assert statistics_sd(x) == pytest.approx(0.05, rel=0.35)
        assert bars[days[5]].open == pytest.approx(bars[days[4]].close * market.bars["B"][days[5]].open
                                                   / market.bars["B"][days[4]].close)


def statistics_sd(x):
    m = sum(x) / len(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / len(x))


def test_the_counts_read_no_return(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("counts must not simulate a book")
    monkeypatch.setattr(sl, "simulate_held", refuse)
    monkeypatch.setattr(sl, "measure", refuse)
    days = list(_days(date(2020, 12, 1), date(2021, 1, 31)))
    market = _flat(days, {d: 3.0 for d in days})
    out = pv.counts(market, {date(2021, 1, 1): ["A", "B"]}, {date(2021, 1, 1): [_p()]})
    assert out["years"]["2021"]["opens"] >= 1 and out["stop"]["stops"] is True


def test_the_book_is_measured_on_2021_to_2024_held_daily(monkeypatch):
    seen = {}
    monkeypatch.setattr(sl, "measure", lambda market, weigher, span, **kw: seen.update(span=span, **kw) or
                        {"years_total_position_pct": {}})
    pv.book(xsb.Market(), {})
    assert seen == {"span": (date(2021, 1, 1), date(2024, 12, 31)), "simulate": sl.simulate_held, "cadence": "S-REV"}
    assert xsb.SIGNALS["S-REV"]["rebalance"] == "daily"
    from scripts.research import basket_search as bs
    assert len(bs._complete_months(*pv.SPAN)) == 48


def test_the_null_p_counts_every_null_book():
    assert pv.null_p(1.0, [0.5] * 599 + [None]) == pytest.approx(1 / 601)
    assert pv.null_p(1.0, [1.0] * 30 + [0.0] * 570) == pytest.approx(31 / 601)
    assert pv.null_p(None, [0.0] * 600) is None


def test_the_commands_refuse_outside_the_sandbox_and_without_their_inputs(monkeypatch, tmp_path):
    assert pv.main(["pairs-counts"]) == pv.EXIT_USAGE
    monkeypatch.setattr(pv.xsb, "_in_sandbox", lambda: True)
    monkeypatch.setenv("RESEARCH_CACHE", str(tmp_path))
    assert pv.main(["pairs-null"]) == pv.EXIT_USAGE
    assert pv.main(["pairs-null", "--seeds", "0:601"]) == pv.EXIT_USAGE
    assert pv.main(["pairs-plant", "--seeds", "0:5", "--half-life", "2"]) == pv.EXIT_USAGE
    short = tmp_path / "n.json"
    short.write_text('{"books": {"0": {"annual_sharpe": 0.1}}}')
    assert pv.main(["pairs-book", "--null", str(short)]) == pv.EXIT_USAGE
    full = tmp_path / "full.json"
    full.write_text('{"books": {%s}}' % ",".join(f'"{k}": {{"annual_sharpe": 0.1}}' for k in range(600)))
    assert pv.main(["pairs-book", "--null", str(full)]) == pv.EXIT_USAGE, "needs the counts first"
    stopped = tmp_path / "c.json"
    stopped.write_text('{"stop": {"stops": true}}')
    assert pv.main(["pairs-book", "--null", str(full), "--counts", str(stopped)]) == pv.EXIT_BLOCKED
    assert pv.main(["pairs-counts"]) == pv.EXIT_BLOCKED, "no contract types in an empty cache"
