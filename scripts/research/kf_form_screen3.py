"""Third form screen: the nine buildable forms on Developed ex US, judged by the new stock target (stock design, step 2).

``docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md`` §176 is the registration (Thomas 2026-11-25, "예, 등록대로"); §173 the
new target, §175 the data. Loading, the signal and the measures are :mod:`scripts.research.kf_form_screen` unchanged.
What differs:

- **Data:** the seven Developed ex US files fetched 2026-11-25 04:14Z into the research sandbox (never the repo).
- **Span:** 1990-11 – 2026-09 (431 months), read once as a whole. No development / holdout split. The 1990-07 – 10
  rows are dropped before anything is computed. E1 / E2's index starts at 1990-11 (first ten months hold the risky leg).
- **Benchmark B:** the region's Mkt-RF + RF (RF = US 1-month T-bill).
- **Nine forms:** F1 – F5, E1, E2 as §166 and N1, N2 as §170 with this data's column names; costs are the US
  literature values (§176 [추정]). N3 is absent (no variance sort in this data).
- **Judgement (new target):** after-cost annual SR above B AND after-cost maximum drawdown shallower than B, margin 0.
  CAGR is reported, never judged. Before cost passes, after cost fails → "비용에 달림" (COST_DEPENDENT).
- **Never:** another cell, cost, span, signal or margin after the result; a second read.

Divisor across the three screens: 19. It reads only the research sandbox and writes nothing unless ``--json`` names a
file.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Sequence

from scripts.research import kf_form_screen as kf

FIRST = 199011
LAST = 202609
FACTORS = "Developed_ex_US_3_Factors.csv"

CELLS3: dict[str, tuple[str, str, float]] = {
    "F1": ("Developed_ex_US_6_Portfolios_ME_BE-ME.csv", "BIG HiBM", 0.0005),
    "F2": ("Developed_ex_US_6_Portfolios_ME_OP.csv", "BIG HiOP", 0.0003),
    "F3": ("Developed_ex_US_6_Portfolios_ME_INV.csv", "BIG LoINV", 0.0010),
    "F4": ("Developed_ex_US_6_Portfolios_ME_Prior_12_2.csv", "BIG HiPRIOR", 0.0065),
}
F5_COST = sum(c for _, _, c in CELLS3.values()) / 4.0
UNIONS3: dict[str, tuple[str, tuple[str, ...], float]] = {
    "N1": ("Developed_ex_US_32_Portfolios_ME_BE-ME_OP_2x4x4.csv",
           ("BIG HiBM HiOP", "ME2 BM4 OP3", "ME2 BM3 HiOP", "ME2 BM3 OP3"), 0.0006),
    "N2": ("Developed_ex_US_32_Portfolios_ME_INV(TA)_OP_2x4x4.csv",
           ("ME2 OP4 LoINV", "ME2 OP4 INV2", "ME2 OP3 LoINV", "ME2 OP3 INV2"), 0.0013),
}
FORMS3 = ("F1", "F2", "F3", "F4", "F5", "E1", "E2", "N1", "N2")


def _trim(s: dict[int, float], name: str, span: list[int]) -> dict[int, float]:
    """Keep 1990-11 .. 2026-09 only; the span must be covered exactly."""
    out = {t: v for t, v in s.items() if FIRST <= t <= LAST}
    if sorted(out) != span:
        raise kf.ScreenError(f"{name}: months do not cover {FIRST}..{LAST} exactly")
    return out


def build(data: pathlib.Path):
    """Every form's (before, after) monthly returns over 1990-11 .. 2026-09, plus B and RF."""
    span = kf.months(FIRST, LAST)
    bench_m, rf_m = kf.load_factors(data / FACTORS, LAST)
    bench_m, rf_m = _trim(bench_m, "B", span), _trim(rf_m, "RF", span)
    bench = [bench_m[t] for t in span]
    rf = [rf_m[t] for t in span]
    forms: dict[str, tuple[list[float], list[float]]] = {}
    cells = {k: _trim(kf.load_cell(data / f, col, LAST), k, span) for k, (f, col, _) in CELLS3.items()}
    for k, (_, _, c) in CELLS3.items():
        r = [cells[k][t] for t in span]
        forms[k] = (r, [x - c for x in r])
    f5 = [sum(cells[k][t] for k in CELLS3) / 4.0 for t in span]
    forms["F5"] = (f5, [x - F5_COST for x in f5])
    forms["E1"] = kf.timed(bench, rf, bench, [0.0] * len(span))
    forms["E2"] = kf.timed(f5, rf, bench, [F5_COST] * len(span))
    for k, (f, cols, c) in UNIONS3.items():
        series = [_trim(kf.load_cell(data / f, col, LAST), f"{k} {col}", span) for col in cols]
        r = [sum(s[t] for s in series) / len(series) for t in span]
        forms[k] = (r, [x - c for x in r])
    return span, forms, bench, rf


def beats_new(form: dict[str, float], bench: dict[str, float]) -> bool:
    """The new target (§173): SR above and drawdown shallower; CAGR not judged; margin 0."""
    return form["sr"] > bench["sr"] and form["mdd"] > bench["mdd"]


def run(data: pathlib.Path) -> dict:
    span, forms, bench, rf = build(data)
    b = kf.metrics(bench, rf)
    out: dict = {"span": f"{FIRST}..{LAST}", "B": b, "forms": {}, "verdict": {}}
    for k in FORMS3:
        pre, post = forms[k]
        mp, mq = kf.metrics(pre, rf), kf.metrics(post, rf)
        pb, pa = beats_new(mp, b), beats_new(mq, b)
        out["forms"][k] = {"before": mp, "after": mq, "pass_before": pb, "pass_after": pa}
        out["verdict"][k] = "FORM_PASS" if pa else ("COST_DEPENDENT" if pb else "FAIL")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=pathlib.Path, required=True, help="folder with the seven Developed ex US files")
    ap.add_argument("--json", type=pathlib.Path)
    a = ap.parse_args(argv)
    res = run(a.data)
    print(f"span {res['span']}  B: {kf._fmt(res['B'])}")
    for k, f in res["forms"].items():
        print(f"{k} before {kf._fmt(f['before'])}  pass={f['pass_before']}")
        print(f"{k} after  {kf._fmt(f['after'])}  pass={f['pass_after']}")
    print("verdict:", json.dumps(res["verdict"]))
    if a.json:
        a.json.write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
