"""Second form screen on the Ken French monthly portfolios: N1 – N3 (stock design, step 2).

``docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md`` §170 is the registration (Thomas 2026-11-25, "예, 등록대로").
Loading, measures, the three conditions, spans and gates are :mod:`scripts.research.kf_form_screen` unchanged (§166);
only the forms differ:

====  ======================================================================  ===============
form  definition (value-weighted monthly returns, 1/4 each, rebalanced monthly)  cost / month
====  ======================================================================  ===============
N1    BIG HiBM HiOP, ME2 BM4 OP3, ME2 BM3 OP4, ME2 BM3 OP3 (size x B/M x OP)    6bp
N2    BIG HiOP LoINV, ME2 OP4 INV2, ME2 OP3 INV1, ME2 OP3 INV2 (size x OP x INV)  13bp
N3    BIG LoVAR (size x variance, one cell)                                     52bp
====  ======================================================================  ===============

The divisor across both screens is 10. It reads the research sandbox only and writes nothing unless ``--json`` names
a file.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Sequence

from scripts.research import kf_form_screen as kf

FORMS2: dict[str, tuple[str, tuple[str, ...], float]] = {
    "N1": ("32_Portfolios_ME_BEME_OP_2x4x4.csv", ("BIG HiBM HiOP", "ME2 BM4 OP3", "ME2 BM3 OP4", "ME2 BM3 OP3"), 0.0006),
    "N2": ("32_Portfolios_ME_OP_INV_2x4x4.csv", ("BIG HiOP LoINV", "ME2 OP4 INV2", "ME2 OP3 INV1", "ME2 OP3 INV2"), 0.0013),
    "N3": ("25_Portfolios_ME_VAR_5x5.csv", ("BIG LoVAR",), 0.0052),
}


def build(factors: pathlib.Path, data2: pathlib.Path, last: int):
    bench_m, rf_m = kf.load_factors(factors / "F-F_Research_Data_Factors.csv", last)
    span = kf.months(kf.FIRST, last)
    for name, s in (("B", bench_m), ("RF", rf_m)):
        if sorted(s) != span:
            raise kf.ScreenError(f"{name}: months do not cover {kf.FIRST}..{last} exactly")
    forms: dict[str, tuple[list[float], list[float]]] = {}
    for k, (f, cols, cost) in FORMS2.items():
        series = [kf.load_cell(data2 / f, col, last) for col in cols]
        for col, s in zip(cols, series):
            if sorted(s) != span:
                raise kf.ScreenError(f"{k} {col}: months do not cover {kf.FIRST}..{last} exactly")
        r = [sum(s[t] for s in series) / len(series) for t in span]
        forms[k] = (r, [x - cost for x in r])
    return span, forms, [bench_m[t] for t in span], [rf_m[t] for t in span]


def judge(span, forms, bench, rf, first: int, last: int) -> dict:
    i0, i1 = span.index(first), span.index(last) + 1
    rf_s = rf[i0:i1]
    b = kf.metrics(bench[i0:i1], rf_s)
    out = {"span": f"{first}..{last}", "B": b, "forms": {}}
    for k, (pre, post) in forms.items():
        mp, mq = kf.metrics(pre[i0:i1], rf_s), kf.metrics(post[i0:i1], rf_s)
        out["forms"][k] = {"before": mp, "after": mq, "pass_before": kf.beats(mp, b), "pass_after": kf.beats(mq, b)}
    return out


def run(factors: pathlib.Path, data2: pathlib.Path, which: str) -> dict:
    if which == "dev":
        span, forms, bench, rf = build(factors, data2, kf.DEV_END)
        return {"dev": judge(span, forms, bench, rf, kf.FIRST, kf.DEV_END)}
    span, forms, bench, rf = build(factors, data2, kf.HOLDOUT_END)
    dev = judge(span, forms, bench, rf, kf.FIRST, kf.DEV_END)
    passers = [k for k in FORMS2 if dev["forms"][k]["pass_before"]]
    hold = judge(span, forms, bench, rf, kf.HOLDOUT_START, kf.HOLDOUT_END)
    hold["forms"] = {k: v for k, v in hold["forms"].items() if k in passers}
    verdict = {}
    for k in FORMS2:
        a, b = dev["forms"][k]["pass_before"], dev["forms"][k]["pass_after"]
        if not a:
            verdict[k] = "CLOSED_A"
        elif not b:
            verdict[k] = "COST_DEPENDENT"
        else:
            verdict[k] = "FORM_PASS" if hold["forms"][k]["pass_after"] else "HOLDOUT_FAIL"
    return {"holdout": hold, "gate_A_passers": passers, "verdict": verdict}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("span", choices=("dev", "holdout"))
    ap.add_argument("--factors", type=pathlib.Path, required=True, help="folder with F-F_Research_Data_Factors.csv")
    ap.add_argument("--data", type=pathlib.Path, required=True, help="folder with the three N1-N3 files")
    ap.add_argument("--json", type=pathlib.Path)
    a = ap.parse_args(argv)
    res = run(a.factors, a.data, a.span)
    part = res["dev"] if a.span == "dev" else res["holdout"]
    print(f"span {part['span']}  B: {kf._fmt(part['B'])}")
    for k, f in part["forms"].items():
        print(f"{k} before {kf._fmt(f['before'])}  pass={f['pass_before']}")
        print(f"{k} after  {kf._fmt(f['after'])}  pass={f['pass_after']}")
    if a.span == "holdout":
        print("gate A passers:", ",".join(res["gate_A_passers"]) or "-")
        print("verdict:", json.dumps(res["verdict"]))
    if a.json:
        a.json.write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
