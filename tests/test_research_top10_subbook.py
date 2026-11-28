"""TOP10_SUBBOOK_V0.1 (loop §153 – §154): the top-10 read of the B-POS and PC screens, on synthetic data only."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from scripts.research import cross_section_basket as xsb
from scripts.research import funding_basis as fb
from scripts.research import funding_basis_screen as sc
from scripts.research import funding_basis_v02 as v
from scripts.research import premium_convergence as pc
from scripts.research import top10_subbook as t

START, END = date(2027, 1, 1), date(2027, 2, 28)


def _market(volumes: dict[str, float], *, through: date, later: dict[str, float] | None = None) -> xsb.Market:
    """Coins listed a year before START, trading every day through ``through``; ``later`` replaces their volume
    from START on (a ranking that read it would change)."""
    m = xsb.Market()
    first = START - timedelta(days=365)
    for symbol, vol in volumes.items():
        bars, day = {}, first
        while day <= through:
            q = (later or {}).get(symbol, vol) if day >= START else vol
            bars[day] = xsb.Bar(open=1.0, close=1.0, quote_volume=q)
            day += timedelta(days=1)
        m.bars[symbol] = bars
    return m


def _leg(values: dict[date, float]) -> fb.Leg:
    return fb.Leg(returns=dict(values), funding={d: 0.0 for d in values})


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _top(*names: str) -> frozenset[str]:
    return frozenset(names)


def test_constants_and_windows_come_from_the_baskets() -> None:
    assert t.SPEC == "TOP10_SUBBOOK_V0.1" and t.TOP == 10 and t.BASKETS == ("B-POS", "PC")
    assert t.window("B-POS") == v.SCREEN_WINDOW and t.window("PC") == pc.SCREEN_WINDOW
    for basket in ("S-MOM30", "M04"):
        with pytest.raises(ValueError):
            t.window(basket)


def test_top10_is_the_trailing_volume_ranking_read_before_each_month() -> None:
    vols = {f"C{i:02d}USDT": float(100 - i) for i in range(12)}
    market = _market(vols, through=END, later={"C11USDT": 10_000.0})
    top = t.top10_by_month(market, START, END)
    assert set(top) == {"2027-01", "2027-02"}
    assert top["2027-01"] == frozenset(f"C{i:02d}USDT" for i in range(10))      # C11's jump is not read yet
    assert "C11USDT" in top["2027-02"] and "C09USDT" not in top["2027-02"]      # read by February


def test_a_month_ranking_fewer_than_ten_coins_blocks() -> None:
    market = _market({f"C{i}USDT": 1.0 for i in range(9)}, through=END)
    with pytest.raises(t.Blocked):
        t.top10_by_month(market, START, END)


def test_sub_legs_keep_only_the_months_a_coin_is_in_the_top10() -> None:
    jan, feb = _days(date(2027, 1, 1), date(2027, 1, 31)), _days(date(2027, 2, 1), date(2027, 2, 28))
    legs = {"A": _leg({d: 0.01 for d in jan + feb}), "B": _leg({d: 0.02 for d in jan + feb}),
            "C": _leg({d: 0.03 for d in jan})}
    sub = t.sub_legs(legs, {"2027-01": _top("A"), "2027-02": _top("A", "B")})
    assert set(sub) == {"A", "B"}                                  # C never in the top 10: dropped
    assert sorted(sub["A"].returns) == jan + feb and sorted(sub["B"].returns) == feb


def test_the_read_only_drops_more() -> None:
    days = _days(START, END)
    good = {"A": _leg({d: 0.001 for d in days})}
    bad = {"A": _leg({d: -0.001 for d in days})}
    top = {"2027-01": _top("A"), "2027-02": _top("A")}
    assert t.read(sc.KEEP, good, top, START, END)["verdict"] == t.KEEP
    assert t.read(sc.DROP, good, top, START, END)["verdict"] == t.DROP      # no rescue of a dropped basket
    assert t.read(sc.KEEP, bad, top, START, END)["verdict"] == t.DROP
    empty = t.read(sc.KEEP, good, {"2027-01": _top("Z"), "2027-02": _top("Z")}, START, END)
    assert empty["verdict"] == t.DROP and empty["mean_bps_per_day"] == 0      # never in: mean zero


def test_days_without_a_subbook_coin_count_as_zero_and_keep_the_sign() -> None:
    jan = _days(date(2027, 1, 1), date(2027, 1, 31))
    legs = {"A": _leg({d: 0.002 for d in _days(START, END)})}
    row = t.read(sc.KEEP, legs, {"2027-01": _top("A"), "2027-02": _top("Z")}, START, END)
    assert row["days"] == len(_days(START, END))
    assert row["mean_bps_per_day"] == pytest.approx(20 * len(jan) / row["days"], abs=1e-3)
    assert row["verdict"] == t.KEEP
    assert row["coin_days_nonzero"] == len(jan)


def test_each_day_averages_the_legs_present_as_the_basket_does() -> None:
    days = _days(START, END)
    legs = {"A": _leg({d: 0.004 for d in days}), "B": _leg({d: 0.0 for d in days})}     # B idle: counts as 0
    row = t.read(sc.KEEP, legs, {"2027-01": _top("A", "B"), "2027-02": _top("A", "B")}, START, END)
    assert row["mean_bps_per_day"] == pytest.approx(20.0)


def test_capture_records_the_screens_legs_and_restores_summarise() -> None:
    original = sc.summarise
    legs = {"A": _leg({START: 0.01, START + timedelta(days=1): -0.005})}
    with t.capturing() as seen:
        out = sc.summarise("B-POS", legs)
    assert sc.summarise is original and seen == [("B-POS", legs)]
    assert out == original("B-POS", legs)
    with pytest.raises(RuntimeError):
        with t.capturing():
            raise RuntimeError("boom")
    assert sc.summarise is original


def _screen_with(legs: dict[str, fb.Leg], verdict_only: bool = False):
    def run() -> dict:
        row = sc.summarise("B-POS", legs)          # the call the real screen makes
        return {"status": sc.JUDGED, "strategies": {"B-POS": row}}
    return run


def test_judge_reads_the_legs_the_screen_judged() -> None:
    start, end = t.window("B-POS")
    days = _days(start, end)
    legs = {"A": _leg({d: 0.001 for d in days}), "B": _leg({d: -0.002 for d in days})}
    top = {m.strftime("%Y-%m"): _top("A") for m in t.month_starts(start, end)}
    report = t.judge("B-POS", _screen_with(legs), lambda s, e: top)
    assert report["status"] == sc.JUDGED
    assert report["basket_report"]["strategies"]["B-POS"]["verdict"] == sc.DROP     # the basket as a whole loses
    assert report["top10_read"]["verdict"] == t.DROP                              # so the sub-book cannot keep


def test_a_basket_not_judged_leaves_the_read_unjudged_and_never_ranks() -> None:
    calls = []
    report = t.judge("PC", lambda: {"status": sc.NOT_YET, "short": ["x"]}, lambda s, e: calls.append(1) or {})
    assert report["status"] == sc.NOT_YET and "top10_read" not in report and calls == []


def test_archive_short_of_the_last_ranking_is_not_yet() -> None:
    start, end = t.window("B-POS")
    days = _days(start, end)
    legs = {"A": _leg({d: 0.001 for d in days})}

    def short(s: date, e: date):
        raise t.NotYet("perp klines through 2027-01-15")
    report = t.judge("B-POS", _screen_with(legs), short)
    assert report["status"] == sc.NOT_YET and "top10_read" not in report


def test_refuses_outside_the_sandbox() -> None:
    assert t.main(["run", "--basket", "PC"]) == t.EXIT_USAGE
