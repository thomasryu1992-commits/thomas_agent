"""E1 on US 1926-07 – 1963-06 with tax and margins (loop §179), on synthetic factor files."""

from __future__ import annotations

import pathlib

import pytest

from scripts.research import kf_form_screen as kf
from scripts.research import kf_form_screen4 as kf4


def _write(d: pathlib.Path, mkt=lambda ym: 0.8 + (3.0 if ym % 2 else -3.0), rf=lambda ym: 0.3,
           first: int = 192607, last: int = 202609) -> None:
    lines = ["This file was created ...", "", ",Mkt-RF,SMB,HML,RF"]
    lines += [f"{ym},{mkt(ym) - rf(ym):.4f},0.1,0.1,{rf(ym):.4f}" for ym in kf.months(first, last)]
    lines += ["", " Annual Factors: January-December ", ",Mkt-RF,SMB,HML,RF", "1927,5.0,1.0,1.0,3.0"]
    (d / kf4.FACTORS).write_text("\n".join(lines) + "\n", encoding="latin-1")


def test_only_1926_07_to_1963_06_is_read(tmp_path):
    _write(tmp_path, mkt=lambda ym: 900.0 if ym > kf4.LAST else 0.8 + ym % 2,
           rf=lambda ym: -99.99 if ym > kf4.LAST else 0.3)
    span, bench, rf = kf4.load(tmp_path / kf4.FACTORS)
    assert span[0] == 192607 and span[-1] == 196306 and len(span) == 444
    assert max(bench) < 0.05 and max(rf) < 0.01
    res = kf4.run(tmp_path)
    assert res["B"]["months"] == 444 and res["span"] == "192607..196306"


def test_a_missing_month_blocks(tmp_path):
    _write(tmp_path, first=192701)
    with pytest.raises(kf.ScreenError):
        kf4.run(tmp_path)


def test_without_tax_the_path_is_the_registered_after_cost_e1():
    span = kf.months(192607, 193012)
    bench = [0.03 if i % 7 else -0.12 for i in range(len(span))]
    rf = [0.002] * len(span)
    _, after_cost = kf.timed(bench, rf, bench, [0.0] * len(span))
    got, paid = kf4.taxed(span, bench, rf, cg=0.0, interest=0.0)
    assert got == pytest.approx(after_cost)
    before, _ = kf.timed(bench, rf, bench, [0.0] * len(span))
    pos = kf4.holds(bench)
    assert False in pos and before == [b if p else r for b, r, p in zip(bench, rf, pos)]


def test_a_realized_gain_is_taxed_in_december_and_cash_interest_monthly():
    span = kf.months(192607, 192712)  # 18 months; index 10 = 1927-05, index 11 = 1927-06
    bench = [0.05] * 10 + [-0.20] + [0.0] * 7
    rf = [0.003] * len(span)
    pos = kf4.holds(bench)
    assert pos[:11] == [True] * 11 and pos[11:] == [False] * 7  # leaves at the end of 1927-05, stays out
    got, paid = kf4.taxed(span, bench, rf)
    gain = 1.05 ** 10 * 0.8 * (1.0 - kf.SWITCH_COST) - 1.0
    assert paid == {1927: pytest.approx(0.22 * gain)}
    assert got[11] == pytest.approx(0.003 * (1 - 0.154) - kf.SWITCH_COST)
    assert got[12] == pytest.approx(0.003 * (1 - 0.154))
    w_nov = 1.05 ** 10 * 0.8 * (1 + got[11]) * (1 + 0.003 * (1 - 0.154)) ** 5
    assert got[17] == pytest.approx((w_nov * (1 + 0.003 * (1 - 0.154)) - 0.22 * gain) / w_nov - 1.0)


def test_losses_net_within_the_year_and_none_carry_forward():
    span = kf.months(192607, 192812)
    bench = [0.05] * 10 + [-0.60] + [0.0] * (len(span) - 11)
    rf = [0.0] * len(span)
    _, paid = kf4.taxed(span, bench, rf)
    assert paid == {}  # 1927 net loss: no tax, and nothing carries into 1928


def test_the_margins_are_strict():
    b = {"cagr": 0.1, "sr": 0.40, "mdd": -0.80}
    assert kf4.beats_margin({"cagr": 0.0, "sr": 0.46, "mdd": -0.69}, b)
    assert not kf4.beats_margin({"cagr": 0.2, "sr": 0.45, "mdd": -0.30}, b)  # SR +0.05 exactly is a tie
    assert not kf4.beats_margin({"cagr": 0.2, "sr": 0.60, "mdd": -0.705}, b)  # 9.5 points shallower
