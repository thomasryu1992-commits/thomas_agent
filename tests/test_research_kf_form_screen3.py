"""Ken French third form screen (loop §176): nine forms on Developed ex US, new target, synthetic files."""

from __future__ import annotations

import pathlib

import pytest

from scripts.research import kf_form_screen as kf
from scripts.research import kf_form_screen3 as kf3


def _files() -> dict[str, list[str]]:
    out: dict[str, set[str]] = {}
    for f, col, _ in kf3.CELLS3.values():
        out.setdefault(f, set()).add(col)
    for f, cols, _ in kf3.UNIONS3.values():
        out.setdefault(f, set()).update(cols)
    return {f: sorted(c) + ["SMALL OTHER"] for f, c in out.items()}


def _write(d: pathlib.Path, value=lambda c, ym: 1.0 + ym % 3, mkt=lambda ym: 0.8 + ym % 2,
           first: int = 199007) -> None:
    span = kf.months(first, 202609)
    for f, cols in _files().items():
        lines = ["created", "", "  Average Value Weighted Returns -- Monthly", "," + ",".join(cols)]
        lines += [f"{ym}," + ",".join(f"{value(c, ym):.4f}" for c in cols) for ym in span]
        lines += ["", "  Average Equal Weighted Returns -- Monthly", "," + ",".join(cols)]
        lines += [f"{ym}," + ",".join("-99.99" for _ in cols) for ym in span]
        (d / f).write_text("\n".join(lines) + "\n", encoding="latin-1")
    lines = ["created", "", ",Mkt-RF,SMB,HML,RF"]
    lines += [f"{ym},{mkt(ym) - 0.3:.4f},0.1,0.1,0.3000" for ym in span]
    lines += ["", " Annual Factors: January-December ", ",Mkt-RF,SMB,HML,RF", "1991,5.0,1.0,1.0,3.0"]
    (d / kf3.FACTORS).write_text("\n".join(lines) + "\n", encoding="latin-1")


def test_the_span_is_1990_11_to_2026_09_and_the_earlier_rows_never_count(tmp_path):
    # Rows 1990-07..10 hold absurd values; if any reached a form or B the measures would show it.
    _write(tmp_path, value=lambda c, ym: 900.0 if ym < kf3.FIRST else 1.0 + ym % 3,
           mkt=lambda ym: 900.0 if ym < kf3.FIRST else 0.8 + ym % 2)
    span, forms, bench, rf = kf3.build(tmp_path)
    assert span[0] == 199011 and span[-1] == 202609 and len(span) == 431
    assert max(bench) < 0.05 and all(max(pre) < 0.05 for pre, _ in forms.values())
    assert set(forms) == set(kf3.FORMS3)


def test_a_file_that_starts_late_blocks(tmp_path):
    _write(tmp_path, first=199101)
    with pytest.raises(kf.ScreenError):
        kf3.run(tmp_path)


def test_unions_are_equal_weights_and_costs_are_the_us_ones(tmp_path):
    cols = sorted({c for _, cs, _ in kf3.UNIONS3.values() for c in cs})
    vals = {c: i + 1.0 for i, c in enumerate(cols)}
    _write(tmp_path, value=lambda c, ym: vals.get(c, 50.0))
    span, forms, _, _ = kf3.build(tmp_path)
    for k, (_, cs, cost) in kf3.UNIONS3.items():
        want = sum(vals[c] for c in cs) / len(cs) / 100.0
        assert forms[k][0][0] == pytest.approx(want)
        assert forms[k][1][0] == pytest.approx(want - cost)
    assert kf3.F5_COST == pytest.approx(0.002075)
    assert forms["F1"][1][0] == pytest.approx(0.50 - 0.0005)  # BIG HiBM is not a union column: 50.0%


def test_the_new_target_judges_sr_and_drawdown_only():
    b = {"cagr": 0.10, "sr": 0.5, "mdd": -0.5}
    assert kf3.beats_new({"cagr": 0.01, "sr": 0.6, "mdd": -0.4}, b)  # lower CAGR does not matter
    assert not kf3.beats_new({"cagr": 0.20, "sr": 0.6, "mdd": -0.5}, b)  # a tie on drawdown fails
    assert not kf3.beats_new({"cagr": 0.20, "sr": 0.5, "mdd": -0.1}, b)  # a tie on SR fails


def test_verdicts_pass_cost_dependent_and_fail(tmp_path):
    def value(c, ym):
        noise = 3.0 if ym % 2 else -3.0
        if c == "BIG HiBM":
            return 0.7 + 0.5 * noise  # lower mean than the market, half the swing: SR and drawdown both better
        if c == "BIG HiPRIOR":
            return 0.84 + noise  # a hair above the market before cost; 65bp sinks it
        return 0.2 + noise
    _write(tmp_path, value=value, mkt=lambda ym: 0.8 + (3.0 if ym % 2 else -3.0))
    res = kf3.run(tmp_path)
    assert res["span"] == "199011..202609" and res["B"]["months"] == 431
    assert res["verdict"]["F1"] == "FORM_PASS"
    assert res["forms"]["F1"]["after"]["cagr"] < res["B"]["cagr"]  # passes without beating on return
    assert res["verdict"]["F4"] == "COST_DEPENDENT"
    assert res["verdict"]["F2"] == "FAIL"
