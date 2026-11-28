"""Ken French form screen (loop §166): what the code does, on synthetic files shaped like the library's."""

from __future__ import annotations

import math
import pathlib

import pytest

from scripts.research import kf_form_screen as kf

COLS = {
    "6_Portfolios_2x3.csv": ["SMALL LoBM", "ME1 BM2", "SMALL HiBM", "BIG LoBM", "ME2 BM2", "BIG HiBM"],
    "6_Portfolios_ME_OP_2x3.csv": ["SMALL LoOP", "ME1 OP2", "SMALL HiOP", "BIG LoOP", "ME2 OP2", "BIG HiOP"],
    "6_Portfolios_ME_INV_2x3.csv": ["SMALL LoINV", "ME1 INV2", "SMALL HiINV", "BIG LoINV", "ME2 INV2", "BIG HiINV"],
    "6_Portfolios_ME_Prior_12_2.csv": ["SMALL LoPRIOR", "ME1 PRIOR2", "SMALL HiPRIOR", "BIG LoPRIOR", "ME2 PRIOR2", "BIG HiPRIOR"],
}


def _write(d: pathlib.Path, value=lambda f, c, ym: 1.0, mkt=lambda ym: 0.8, rf=lambda ym: 0.3,
           first: int = 192607, last: int = 202609) -> None:
    span = kf.months(first, last)
    for f, cols in COLS.items():
        lines = ["This file was created ...", "Missing data are indicated by -99.99 or -999.", "",
                 "  Average Value Weighted Returns -- Monthly", "," + ",".join(cols)]
        lines += [f"{ym}," + ",".join(f"{value(f, c, ym):.4f}" for c in cols) for ym in span]
        lines += ["", "  Average Equal Weighted Returns -- Monthly", "," + ",".join(cols)]
        lines += [f"{ym}," + ",".join("-99.99" for _ in cols) for ym in span]
        (d / f).write_text("\n".join(lines) + "\n", encoding="latin-1")
    lines = ["This file was created ...", "", ",Mkt-RF,SMB,HML,RF"]
    lines += [f"{ym},{mkt(ym) - rf(ym):.4f},0.1,0.1,{rf(ym):.4f}" for ym in span]
    lines += ["", " Annual Factors: January-December ", ",Mkt-RF,SMB,HML,RF", "1927,5.0,1.0,1.0,3.0"]
    (d / "F-F_Research_Data_Factors.csv").write_text("\n".join(lines) + "\n", encoding="latin-1")


def test_the_value_weighted_monthly_section_is_picked_by_title_and_pre_1963_rows_are_dropped(tmp_path):
    _write(tmp_path, value=lambda f, c, ym: 2.0 if c == "BIG HiBM" else 9.0)
    s = kf.load_cell(tmp_path / "6_Portfolios_2x3.csv", "BIG HiBM", kf.DEV_END)
    assert min(s) == 196307 and max(s) == 201606
    assert set(s.values()) == {0.02}  # the equal-weighted block (all -99.99) is never read


def test_a_dev_run_reads_no_row_after_2016_06(tmp_path):
    _write(tmp_path, value=lambda f, c, ym: -99.99 if ym > kf.DEV_END else 1.0 + ym % 3,
           mkt=lambda ym: -99.99 if ym > kf.DEV_END else 0.8 + ym % 2)
    res = kf.run(tmp_path, "dev")
    assert res["dev"]["span"] == "196307..201606"
    with pytest.raises(kf.ScreenError):
        kf.run(tmp_path, "holdout")


def test_a_missing_month_blocks(tmp_path):
    _write(tmp_path, first=196401)
    with pytest.raises(kf.ScreenError):
        kf.run(tmp_path, "dev")


def test_the_signal_holds_the_risky_leg_for_ten_months_then_follows_the_moving_average():
    n = 14
    bench = [0.01] * 10 + [-0.30, 0.0, 0.0, 0.0]
    risky = [0.02] * n
    rf = [0.001] * n
    before, after = kf.timed(risky, rf, bench, [0.0] * n)
    # Months 0..9 hold (no signal yet); month 10 decided at end of 9 (rising) -> hold;
    # month 11 decided at end of 10 (the crash puts the level under its 10-month mean) -> RF.
    assert before[:11] == [0.02] * 11
    assert before[11] == 0.001
    assert after[11] == pytest.approx(0.001 - kf.SWITCH_COST)
    assert after[:11] == before[:11]


def test_metrics_and_the_three_conditions():
    m = kf.metrics([0.1, -0.5, 0.2], [0.0, 0.0, 0.0])
    assert m["mdd"] == pytest.approx(-0.5)
    assert m["cagr"] == pytest.approx((1.1 * 0.5 * 1.2) ** 4 - 1)
    ex = [0.1, -0.5, 0.2]
    mean = sum(ex) / 3
    sd = math.sqrt(sum((e - mean) ** 2 for e in ex) / 2)
    assert m["sr"] == pytest.approx(mean / sd * math.sqrt(12))
    b = {"cagr": 0.1, "sr": 0.5, "mdd": -0.5}
    assert kf.beats({"cagr": 0.11, "sr": 0.6, "mdd": -0.4}, b)
    assert not kf.beats({"cagr": 0.11, "sr": 0.6, "mdd": -0.5}, b)  # margin 0, a tie fails


def test_the_holdout_is_shown_only_for_gate_a_passers_and_costs_decide_b(tmp_path):
    def value(f, c, ym):  # F1 beats the market on everything; the others lose
        noise = 3.0 if ym % 2 else -3.0
        if c == "BIG HiBM":
            return 1.5 + 0.5 * noise
        if c == "BIG HiPRIOR":
            return 0.86 + noise  # a hair above the market before cost, 65bp costs sink it
        return 0.2 + noise
    _write(tmp_path, value=value, mkt=lambda ym: 0.8 + (3.0 if ym % 2 else -3.0))
    res = kf.run(tmp_path, "holdout")
    assert "F1" in res["gate_A_passers"]
    assert res["verdict"]["F2"] == "CLOSED_A"
    assert set(res["holdout"]["forms"]) == set(res["gate_A_passers"])
    assert res["verdict"]["F1"] == "FORM_PASS"
    assert kf.F5_COST == pytest.approx(0.002075)
