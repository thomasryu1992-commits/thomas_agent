"""Ken French second form screen (loop §170): N1 – N3 on synthetic files shaped like the library's."""

from __future__ import annotations

import pathlib

import pytest

from scripts.research import kf_form_screen as kf
from scripts.research import kf_form_screen2 as kf2


def _cols(f: str) -> list[str]:
    cells = {c for k, (ff, cols, _) in kf2.FORMS2.items() if ff == f for c in cols}
    return sorted(cells) + ["SMALL OTHER"]


def _write(d: pathlib.Path, value=lambda c, ym: 1.0 + ym % 3, mkt=lambda ym: 0.8 + ym % 2, last: int = 202609) -> None:
    span = kf.months(192607, last)
    for f in {ff for ff, _, _ in kf2.FORMS2.values()}:
        cols = _cols(f)
        lines = ["created", "", "  Average Value Weighted Returns -- Monthly", "," + ",".join(cols)]
        lines += [f"{ym}," + ",".join(f"{value(c, ym):.4f}" for c in cols) for ym in span if ym >= 196307]
        lines += ["", "  Average Equal Weighted Returns -- Monthly", "," + ",".join(cols)]
        lines += [f"{ym}," + ",".join("-99.99" for _ in cols) for ym in span if ym >= 196307]
        (d / f).write_text("\n".join(lines) + "\n", encoding="latin-1")
    lines = ["created", "", ",Mkt-RF,SMB,HML,RF"]
    lines += [f"{ym},{mkt(ym) - 0.3:.4f},0.1,0.1,0.3000" for ym in span]
    (d / "F-F_Research_Data_Factors.csv").write_text("\n".join(lines) + "\n", encoding="latin-1")


def test_n1_and_n2_are_equal_weights_of_their_four_cells_and_n3_is_one_cell(tmp_path):
    vals = {c: i + 1.0 for i, c in enumerate(sorted({c for _, cols, _ in kf2.FORMS2.values() for c in cols}))}
    _write(tmp_path, value=lambda c, ym: vals.get(c, 50.0) + (1.0 if ym % 2 else -1.0))
    span, forms, _, _ = kf2.build(tmp_path, tmp_path, kf.DEV_END)
    t = span.index(196307)  # odd month: +1
    for k, (_, cols, cost) in kf2.FORMS2.items():
        want = sum(vals[c] + 1.0 for c in cols) / len(cols) / 100.0
        assert forms[k][0][t] == pytest.approx(want)
        assert forms[k][1][t] == pytest.approx(want - cost)
    assert len(kf2.FORMS2["N1"][1]) == 4 and len(kf2.FORMS2["N2"][1]) == 4 and kf2.FORMS2["N3"][1] == ("BIG LoVAR",)


def test_a_dev_run_reads_no_row_after_2016_06(tmp_path):
    _write(tmp_path, value=lambda c, ym: -99.99 if ym > kf.DEV_END else 1.0 + ym % 3,
           mkt=lambda ym: -99.99 if ym > kf.DEV_END else 0.8 + ym % 2)
    assert kf2.run(tmp_path, tmp_path, "dev")["dev"]["span"] == "196307..201606"
    with pytest.raises(kf.ScreenError):
        kf2.run(tmp_path, tmp_path, "holdout")


def test_the_holdout_shows_only_gate_a_passers(tmp_path):
    def value(c, ym):
        noise = 3.0 if ym % 2 else -3.0
        if c in kf2.FORMS2["N1"][1]:
            return 1.5 + 0.5 * noise  # beats the market on all three
        return 0.2 + noise
    _write(tmp_path, value=value, mkt=lambda ym: 0.8 + (3.0 if ym % 2 else -3.0))
    res = kf2.run(tmp_path, tmp_path, "holdout")
    assert res["gate_A_passers"] == ["N1"]
    assert set(res["holdout"]["forms"]) == {"N1"}
    assert res["verdict"] == {"N1": "FORM_PASS", "N2": "CLOSED_A", "N3": "CLOSED_A"}
