"""FLOW_AND_TREND_SEARCH_V0.1 (Thomas 2026-11-11): the entry rule for new candidates, the pinned development spans,
the flow basket search and the trend book. Synthetic markets only."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

import pytest

from scripts.research import basket_search as bs
from scripts.research import cross_section_basket as xsb
from scripts.research import flow_basket_search as fl
from scripts.research import metrics_basket_search as ms
from scripts.research import trend_book_search as tr

FIRST = date(2021, 6, 1)
SPAN = (date(2022, 1, 1), date(2022, 12, 31))
N = 40


def _ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def _market(*, falling_keep_falling: bool = True, taker_leads: bool = True):
    """``N`` coins, volume falling by index. Coins 0–14 drift up, 15–29 drift down (with ``falling_keep_falling``;
    otherwise every coin's drift flips each 45 days). The taker buy share rises with the drift when it leads."""
    market, daily = xsb.Market(), {}
    days = [FIRST + timedelta(days=k) for k in range((SPAN[1] - FIRST).days + 1)]
    for i in range(N):
        name = bs.REFERENCE if i == 0 else f"S{i:02d}USDT"
        base = (14.5 - i) * 0.0004
        level, bars, rows = 100.0, {}, {}
        for k, day in enumerate(days):
            drift = base if falling_keep_falling else base * (1 if (k // 45) % 2 == 0 else -1)
            close = level * math.exp(drift + (0.003 if (k + i) % 2 else -0.003))
            bars[day] = xsb.Bar(open=level, close=close, quote_volume=1000.0 - i)
            level = close
            taker = 0.5 + (0.004 * (14.5 - i) if taker_leads else -0.004 * (14.5 - i))
            rows[day] = {"oi": 1000.0, "ls": 0.5, "top": 0.5 + 0.001 * (i % 9), "taker": taker}
        market.bars[name], daily[name] = bars, rows
        market.funding[name] = [(_ms(day) + 8 * 3_600_000, 0.0) for day in days]
    return market, daily


# --- the entry rule and the spans --------------------------------------------------------------------------

def test_a_new_candidate_needs_the_sharpe_and_fifteen_percent_a_year_on_total_position():
    assert (bs.ENTRY_SHARPE, bs.ENTRY_RETURN_TOTAL_PCT, bs.POSITION_TOTAL) == (1.0, 15.0, 2.0)
    assert bs.total_position_annual_pct(60.0, 24) == 15.0          # 60% in two years on one side is 15% a year on two
    assert bs.total_position_annual_pct(30.0, 12) == 15.0 and bs.total_position_annual_pct(10.0, 0) is None
    assert bs.clears_entry({"annual_sharpe": 1.0, "sum_pct": 30.0, "months": 12})
    assert not bs.clears_entry({"annual_sharpe": 2.3, "sum_pct": 2.0, "months": 12})       # steady and tiny: a carry
    assert not bs.clears_entry({"annual_sharpe": 0.99, "sum_pct": 90.0, "months": 12})
    assert not bs.clears_entry({"annual_sharpe": None, "sum_pct": 90.0, "months": 12})
    assert not bs.clears_entry({"annual_sharpe": 1.4, "sum_pct": 29.9, "months": 12})


def test_a_development_span_ends_by_2024_and_both_tools_use_one():
    for span in (bs.DEV, ms.DEV_LONG, ms.DEV_SHORT, fl.DEV, tr.DEV):
        bs.check_development_span(span)
    for end in (date(2025, 1, 1), date(2026, 9, 30), date(2026, 10, 1)):
        with pytest.raises(ValueError):
            bs.check_development_span((date(2023, 1, 1), end))
    with pytest.raises(ValueError):
        bs.check_development_span(bs.CONFIRM)                      # the spent span is not development data
    assert fl.DEV == ms.DEV_SHORT == (date(2023, 2, 1), date(2024, 12, 31)) and tr.DEV == bs.DEV


# --- the flow basket search --------------------------------------------------------------------------------

def _run(module, monkeypatch, argv, **patch):
    monkeypatch.setattr(module.xsb, "_in_sandbox", lambda: True)
    for name, value in patch.items():
        monkeypatch.setattr(module, name, value)
    return module.main(argv)


def test_the_flow_search_reads_two_columns_and_has_no_confirm(monkeypatch, capsys, tmp_path):
    assert fl.ALLOWED == ("top_level", "taker_level") and fl.BUDGET == 3
    assert not {"oi_growth", "ls_level", "divergence"} & set(fl.ALLOWED)       # M04's features and the mix are out
    assert all(set(n for n, _ in c.features) <= set(fl.ALLOWED) for c in fl.CANDIDATES.values())
    assert len(fl.CANDIDATES) <= fl.BUDGET
    with pytest.raises(SystemExit):
        fl.main(["confirm", "--candidate", "F01"])
    cache = ["--cache", str(tmp_path)]
    follow = bs.Candidate(why="t", features=(("taker_level", 1.0),))
    assert fl.main(["explore", "--candidate", "F01", *cache]) == fl.EXIT_USAGE              # outside the sandbox
    assert _run(fl, monkeypatch, ["explore", "--candidate", "F09", *cache], CANDIDATES={"F01": follow}) == fl.EXIT_USAGE
    over = {f"F{k:02d}": follow for k in range(1, 5)}
    assert _run(fl, monkeypatch, ["explore", "--candidate", "F01", *cache], CANDIDATES=over) == fl.EXIT_USAGE
    m04ish = {"F01": bs.Candidate(why="t", features=(("ls_level", 1.0),))}
    assert _run(fl, monkeypatch, ["explore", "--candidate", "F01", *cache], CANDIDATES=m04ish) == fl.EXIT_USAGE
    assert "reads only" in capsys.readouterr().err
    assert _run(fl, monkeypatch, ["explore", "--candidate", "F01", *cache], CANDIDATES={"F01": follow}) == fl.EXIT_BLOCKED
    late = (date(2023, 2, 1), date(2025, 6, 30))
    assert _run(fl, monkeypatch, ["explore", "--candidate", "F01", *cache], CANDIDATES={"F01": follow}, DEV=late) == fl.EXIT_USAGE
    assert "forward only" in capsys.readouterr().err


def test_following_the_taker_flow_passes_when_it_leads_and_fails_when_it_misleads(monkeypatch):
    monkeypatch.setattr(fl, "DEV", SPAN)
    follow = bs.Candidate(why="t", features=(("taker_level", 1.0),))
    market, daily = _market()
    good = fl.explore(ms.cut(market, daily, SPAN[1]), follow)
    assert good["pass"] is True and good["total_position_annual_pct"] >= 15.0 and good["months"] == 12
    assert good["total_position_annual_pct"] == bs.total_position_annual_pct(good["sum_pct"], 12)
    market, daily = _market(taker_leads=False)
    bad = fl.explore(ms.cut(market, daily, SPAN[1]), follow)
    assert bad["pass"] is False and bad["annual_sharpe"] < 0
    assert good["overlap"]["reference"] == "M04" and good["overlap"]["decisions_compared"] == 52


def test_a_steady_but_tiny_flow_edge_does_not_pass(monkeypatch):
    market, daily = _market()
    held = ms.cut(market, daily, SPAN[1])
    tiny = {"annual_sharpe": 2.3, "sum_pct": 4.0, "months": 12, "pass": True}       # the old rule alone would pass it
    monkeypatch.setattr(fl.bs, "measure", lambda *a, **k: dict(tiny))
    monkeypatch.setattr(fl, "DEV", SPAN)
    got = fl.explore(held, bs.Candidate(why="t", features=(("taker_level", 1.0),)))
    assert got["pass"] is False and got["total_position_annual_pct"] == 2.0 and got["entry_return_total_pct"] == 15.0


def test_the_counts_show_how_many_members_a_decision_can_rank(monkeypatch):
    market, daily = _market()
    for d in list(daily["S07USDT"]):
        daily["S07USDT"][d] = {**daily["S07USDT"][d], "taker": None}          # one member without the column
    held = ms.cut(market, daily, SPAN[1])
    counts = fl.ranked_counts(held, ("taker_level",), SPAN)
    assert counts["decisions"] == 52 and counts["weeks_without_a_basket"] == 0
    assert counts["members_ranked_side_weeks"] == [[30, 29, 5, 52]]
    assert fl.ranked_counts(held, ("top_level",), SPAN)["members_ranked_side_weeks"] == [[30, 30, 6, 52]]


def test_the_overlap_counts_legs_on_the_references_side(monkeypatch):
    market, daily = _market()
    held = ms.cut(market, daily, SPAN[1])
    same = bs.Candidate(why="t", features=(("taker_level", 1.0),))
    monkeypatch.setitem(ms.CANDIDATES, "M04", same)
    assert fl.reference_overlap(held, same, SPAN)["mean_share_of_legs_on_the_references_side"] == 1.0
    opposite = bs.Candidate(why="t", features=(("taker_level", 1.0),), long_low=True)
    assert fl.reference_overlap(held, opposite, SPAN)["mean_share_of_legs_on_the_references_side"] == 0.0


# --- the trend book ----------------------------------------------------------------------------------------

def test_the_judged_book_is_trend_minus_always_long():
    market, _ = _market()
    day = date(2022, 6, 26)
    members = xsb.universe_at(market, date(2022, 6, 1), top_n=bs.TOP_N)
    excess = tr.book(30)(market, members, day, bs.CADENCE)
    trend = tr.book(30, tr.TREND)(market, members, day, bs.CADENCE)
    hold = tr.book(30, tr.ALWAYS_LONG)(market, members, day, bs.CADENCE)
    assert sum(hold.values()) == pytest.approx(1.0) and all(v > 0 for v in hold.values()) and len(hold) == 30
    assert sum(abs(v) for v in trend.values()) == pytest.approx(1.0)
    for s in hold:                                                 # excess = trend − always-long, member by member
        assert excess.get(s, 0.0) == pytest.approx(trend[s] - hold[s])
    assert all(v < 0 for v in excess.values()) and 0 < len(excess) < 30
    assert sum(abs(v) for v in excess.values()) <= 2.0 + 1e-9
    down = {s for s in members if xsb.trailing_return(market, s, day, 30) <= 0}
    assert set(excess) == down
    twin = tr.book(30)(market, members, day, bs.CADENCE, seed="x")
    assert len(twin) == len(excess) and set(twin) != set(excess)   # the same number shorted, other members
    assert tr.DEFINITIONS == {"T01": 30, "T02": 90}


def test_shorting_what_keeps_falling_passes_and_a_trend_that_flips_does_not(monkeypatch):
    market, _ = _market()
    good = tr.measure(bs.cut(market, SPAN[1]), 30, SPAN)
    assert good["pass"] is True and good["annual_sharpe"] >= 1.0 and good["total_position_annual_pct"] >= 15.0
    assert set(good["shown_only"]) == {"always_long", "trend", "weekly_correlation_with_S-MOM30"}
    market, _ = _market(falling_keep_falling=False)
    bad = tr.measure(bs.cut(market, SPAN[1]), 30, SPAN)
    assert bad["pass"] is False


def test_the_trend_tool_takes_only_its_two_definitions_in_the_sandbox(monkeypatch, capsys, tmp_path):
    with pytest.raises(SystemExit):
        tr.main(["explore", "--definition", "T03"])
    with pytest.raises(SystemExit):
        tr.main(["confirm", "--definition", "T01"])
    assert tr.main(["explore", "--definition", "T01", "--archive", str(tmp_path)]) == tr.EXIT_USAGE
    assert "sandbox" in capsys.readouterr().err
    argv = ["explore", "--definition", "T01", "--archive", str(tmp_path)]
    assert _run(tr, monkeypatch, argv) == tr.EXIT_BLOCKED
    assert _run(tr, monkeypatch, argv, DEV=(date(2020, 1, 1), date(2025, 12, 31))) == tr.EXIT_USAGE
    assert "forward only" in capsys.readouterr().err
