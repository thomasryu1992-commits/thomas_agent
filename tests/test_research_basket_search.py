"""The basket search (BASKET_SEARCH_PROTOCOL_V0.1, Thomas 2026-11-10): the development cut, the locked span, the
features a candidate may rank, and the rule. Synthetic markets only."""

from __future__ import annotations

import json
import math
import statistics
from datetime import date, datetime, timedelta, timezone

import pytest

from scripts.research import basket_search as bs
from scripts.research import cross_section_basket as xsb

FIRST = date(2021, 6, 1)
SPAN = (date(2022, 1, 1), date(2022, 12, 31))
N = 40


def _ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def _market(*, last: date = SPAN[1], quiet_wins: bool = True) -> xsb.Market:
    """``N`` coins with volume falling by index and wiggle rising with it. With ``quiet_wins`` the calm coins
    drift up and the wild ones down; otherwise the other way round. S00 is the reference."""
    market = xsb.Market()
    days = [FIRST + timedelta(days=k) for k in range((last - FIRST).days + 1)]
    for i in range(N):
        name = bs.REFERENCE if i == 0 else f"S{i:02d}USDT"
        wiggle = 0.004 + 0.001 * i
        drift = (19.5 - i) * 0.0002 * (1 if quiet_wins else -1)
        level, bars = 100.0, {}
        for k, day in enumerate(days):
            close = level * math.exp(drift + (wiggle if (k + i) % 2 else -wiggle))
            bars[day] = xsb.Bar(open=level, close=close, quote_volume=1000.0 - i,
                                high=max(level, close) * 1.001, low=min(level, close) * 0.999)
            level = close
        market.bars[name] = bars
        market.funding[name] = [(_ms(day) + 8 * 3_600_000, 0.0001) for day in days]
    return market


LOW_VOL = bs.Candidate(why="test", features=(("vol", 1.0),), long_low=True)


def test_the_two_spans_do_not_overlap_and_the_budget_is_ten():
    assert bs.DEV == (date(2020, 1, 1), date(2024, 12, 31))
    assert bs.CONFIRM == (date(2025, 1, 1), date(2026, 9, 30))
    assert bs.DEV[1] + timedelta(days=1) == bs.CONFIRM[0]
    assert bs.BUDGET == 10 and bs.ENTRY_SHARPE == 1.0 and bs.TOP_N == 30


def test_the_registry_stays_inside_the_budget_and_the_feature_list():
    assert len(bs.CANDIDATES) <= bs.BUDGET
    assert bs.FINAL is None or bs.FINAL in bs.CANDIDATES
    for candidate in bs.CANDIDATES.values():
        assert candidate.features and {name for name, _ in candidate.features} <= set(bs.FEATURES)


def test_no_feature_ranks_a_trailing_return_or_funding():
    assert set(bs.FEATURES) == {"vol", "max", "skew", "illiquidity", "volume_growth", "range", "beta", "idio_vol"}
    market = _market()
    members = list(market.bars)[:30]
    day = date(2022, 6, 26)
    before = {name: {s: fn(market, s, day, 30) for s in members} for name, fn in bs.FEATURES.items()}
    market.funding = {s: [(t, 9.9) for t, _ in rows] for s, rows in market.funding.items()}
    assert before == {name: {s: fn(market, s, day, 30) for s in members} for name, fn in bs.FEATURES.items()}
    # Doubling every close leaves every feature where it was: none of them is a level or a cumulative return.
    scaled = xsb.Market(bars={s: {d: xsb.Bar(b.open * 2, b.close * 2, b.quote_volume, b.high * 2, b.low * 2)
                                  for d, b in bars.items()} for s, bars in market.bars.items()})
    for name, fn in bs.FEATURES.items():
        for s in members:
            assert fn(scaled, s, day, 30) == pytest.approx(before[name][s]), name


def test_the_features_are_what_their_names_say():
    market = _market()
    day, s = date(2022, 6, 26), "S07USDT"
    bars = market.bars[s]
    r = [bars[day - timedelta(days=k)].close / bars[day - timedelta(days=k + 1)].close - 1 for k in range(30)]
    assert bs.FEATURES["vol"](market, s, day, 30) == pytest.approx(statistics.stdev(r))
    assert bs.FEATURES["max"](market, s, day, 30) == pytest.approx(max(r))
    assert bs.FEATURES["illiquidity"](market, s, day, 30) == pytest.approx(statistics.fmean(abs(v) for v in r) / 993.0)
    assert bs.FEATURES["volume_growth"](market, s, day, 30) == pytest.approx(0.0)
    assert bs.FEATURES["beta"](market, bs.REFERENCE, day, 30) == pytest.approx(1.0)
    assert bs.FEATURES["idio_vol"](market, bs.REFERENCE, day, 30) == pytest.approx(0.0, abs=1e-12)
    for k in range(12):                                           # too few returns left: no value, never a guess
        bars.pop(day - timedelta(days=2 * k + 1), None)
    assert bs.FEATURES["vol"](market, s, day, 30) is None


def test_a_score_is_the_weighted_sum_of_rank_percentiles_over_members_that_have_every_feature():
    market = _market()
    day = date(2022, 6, 26)
    members = [bs.REFERENCE, "S01USDT", "S02USDT", "S03USDT"]
    one = bs.scores(market, members, day, bs.Candidate(why="t", features=(("vol", 1.0),)))
    assert one == {bs.REFERENCE: 0.25, "S01USDT": 0.5, "S02USDT": 0.75, "S03USDT": 1.0}   # wiggle rises with index
    two = bs.scores(market, members, day, bs.Candidate(why="t", features=(("vol", 1.0), ("max", 0.5))))
    assert two["S03USDT"] == pytest.approx(1.5) and two[bs.REFERENCE] == pytest.approx(0.375)
    for k in range(12):
        market.bars["S02USDT"].pop(day - timedelta(days=2 * k + 1), None)
    assert "S02USDT" not in bs.scores(market, members, day, bs.Candidate(why="t", features=(("vol", 1.0),)))


def test_the_weigher_puts_the_named_end_long_and_each_side_sums_to_one():
    market = _market()
    day = date(2022, 6, 26)
    members = list(market.bars)[:30]
    high = bs.weigher(bs.Candidate(why="t", features=(("vol", 1.0),)))(market, members, day, bs.CADENCE)
    low = bs.weigher(LOW_VOL)(market, members, day, bs.CADENCE)
    assert len(high) == 12 and {s for s, w in high.items() if w > 0} == {s for s, w in low.items() if w < 0}
    assert "S29USDT" in {s for s, w in high.items() if w > 0}     # the wildest of the thirty
    assert sum(w for w in low.values() if w > 0) == pytest.approx(1.0)
    assert sum(w for w in low.values() if w < 0) == pytest.approx(-1.0)
    sized = bs.weigher(bs.Candidate(why="t", features=(("vol", 1.0),), long_low=True, inverse_vol=True))(
        market, members, day, bs.CADENCE)
    assert set(sized) == set(low) and len({round(w, 9) for w in sized.values() if w > 0}) > 1
    twin = bs.weigher(LOW_VOL)(market, members, day, bs.CADENCE, seed="a")
    assert twin == bs.weigher(LOW_VOL)(market, members, day, bs.CADENCE, seed="a")
    assert twin != bs.weigher(LOW_VOL)(market, members, day, bs.CADENCE, seed="b") and twin != low


def test_the_cut_removes_every_later_bar_and_settlement():
    market = _market(last=date(2023, 3, 31))
    held = bs.cut(market, SPAN[1])
    assert max(max(bars) for bars in held.bars.values()) == SPAN[1]
    assert all(xsb._utc_day(t) <= SPAN[1] for rows in held.funding.values() for t, _ in rows)
    assert len(held.funding["S05USDT"]) == (SPAN[1] - FIRST).days + 1


def test_exploration_cannot_see_past_the_development_span(monkeypatch):
    monkeypatch.setattr(bs, "DEV", SPAN)
    clean = _market()
    spoiled = _market(last=date(2023, 6, 30))
    for bars in spoiled.bars.values():
        for day in [d for d in bars if d > SPAN[1]]:
            bars[day] = xsb.Bar(open=1e9, close=1e-9, quote_volume=1e12, high=1e9, low=1e-9)
    a = bs.measure(bs.cut(clean, bs.DEV[1]), LOW_VOL, bs.DEV)
    b = bs.measure(bs.cut(spoiled, bs.DEV[1]), LOW_VOL, bs.DEV)
    assert a == b and a["weeks"] > 40


def test_the_rule_is_an_annual_sharpe_of_one_over_complete_months():
    good = bs.measure(_market(), LOW_VOL, SPAN)
    bad = bs.measure(_market(quiet_wins=False), LOW_VOL, SPAN)
    assert good["months"] == 12 and good["pass"] is True and good["annual_sharpe"] >= 1.0
    assert bad["pass"] is False and bad["annual_sharpe"] < 0
    daily = {date(2022, 1, 10): {"net": 0.03}, date(2022, 3, 5): {"net": -0.01}, date(2023, 1, 2): {"net": 9.0}}
    sharpe, values = bs.annual_sharpe(daily, SPAN[0], SPAN[1])
    assert values == [0.03, 0.0, -0.01] + [0.0] * 9               # idle months are 0; the day outside is not read
    assert sharpe == pytest.approx(round(statistics.fmean(values) / statistics.stdev(values) * math.sqrt(12), 3))
    assert bs._complete_months(date(2022, 1, 15), date(2022, 4, 20)) == ["2022-02", "2022-03"]


def _run(monkeypatch, argv, **patch):
    monkeypatch.setattr(bs.xsb, "_in_sandbox", lambda: True)
    for name, value in patch.items():
        monkeypatch.setattr(bs, name, value)
    return bs.main(argv)


def test_the_locked_span_opens_only_for_the_final_candidate(monkeypatch, capsys, tmp_path):
    registry = {"A01": LOW_VOL, "A02": bs.Candidate(why="t", features=(("max", 1.0),))}
    assert _run(monkeypatch, ["confirm", "--candidate", "A01"], CANDIDATES=registry, FINAL=None) == bs.EXIT_USAGE
    assert _run(monkeypatch, ["confirm", "--candidate", "A01"], CANDIDATES=registry, FINAL="A02") == bs.EXIT_USAGE
    assert "read once" in capsys.readouterr().err
    assert _run(monkeypatch, ["explore", "--candidate", "A09"], CANDIDATES=registry) == bs.EXIT_USAGE
    over = {f"A{k:02d}": LOW_VOL for k in range(1, 12)}
    assert _run(monkeypatch, ["explore", "--candidate", "A01"], CANDIDATES=over) == bs.EXIT_USAGE
    # the final one gets as far as the archive, which is not there
    assert _run(monkeypatch, ["confirm", "--candidate", "A02", "--archive", str(tmp_path)],
                CANDIDATES=registry, FINAL="A02") == bs.EXIT_BLOCKED


def test_it_refuses_outside_the_sandbox_and_lists_anywhere(monkeypatch, capsys):
    monkeypatch.setattr(bs.xsb, "_in_sandbox", lambda: False)
    monkeypatch.setattr(bs, "CANDIDATES", {"A01": LOW_VOL})
    assert bs.main(["explore", "--candidate", "A01"]) == bs.EXIT_USAGE
    assert "sandbox" in capsys.readouterr().err
    assert bs.main(["list"]) == bs.EXIT_OK
    listed = json.loads(capsys.readouterr().out)
    assert listed["budget"] == 10 and listed["candidates"]["A01"]["long"] == "low"
