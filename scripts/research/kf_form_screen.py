"""Form screen of US long-only large-cap designs on the Ken French monthly portfolios (stock design, step 2).

``docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md`` §166 is the registration (Thomas 2026-11-25, "예, 등록대로"), and
§165 records the data. This code follows the registration and adds nothing to it.

- **Data:** five CSVs from the Ken French Data Library, fetched 2026-11-25 into the research sandbox (never the repo).
  Each portfolio file's section is picked by its exact title ``Average Value Weighted Returns -- Monthly``; the
  factor file's monthly block is the first block of ``YYYYMM`` rows under the ``Mkt-RF`` header. Percent / 100.
- **Span:** development 1963-07 – 2016-06, holdout 2016-07 – 2026-09. Rows before 1963-07 are never loaded. A
  development run loads no row after 2016-06.
- **Benchmark B:** Mkt-RF + RF (CRSP value-weighted market), a proxy for S&P 500 total return.
- **Seven forms** (long-only, monthly):

  ====  ===============================================  =====================================
  form  definition                                       literature cost
  ====  ===============================================  =====================================
  F1    BIG HiBM                                         5bp / month
  F2    BIG HiOP                                         3bp / month
  F3    BIG LoINV                                        10bp / month
  F4    BIG HiPRIOR                                      65bp / month
  F5    1/4 each of F1 – F4, rebalanced monthly          20.75bp / month
  E1    B or RF by a 10-month moving-average signal      0.2% one-way per switch
  E2    F5 or RF by the same signal                      F5's cost while held + 0.2% per switch
  ====  ===============================================  =====================================

  The signal: hold the risky leg in month t+1 when B's total-return index at the end of month t is above the mean of
  its last ten month-end values (t included); otherwise hold RF. The index starts at 1963-07, so before ten month-ends
  exist there is no signal and the risky leg is held (1963-07 – 1964-04); the first signal, at the end of 1964-04,
  decides 1964-05. A switch is charged in the month the new position is held. Holding from the start is not a switch.
- **Measures:** CAGR of total return, SR = mean(r − RF) / sd(r − RF) × √12 (sample sd), maximum drawdown of the
  wealth curve that starts at 1 before the first month.
- **Three conditions** against B over the same span: higher CAGR, higher SR, shallower (greater) maximum drawdown.
  Margin 0. Taxes are not applied to either side.
- **Gates:** A = development, before cost, all three. B = development, after cost, all three. The holdout is read
  only for forms that pass A, once, before and after cost. Form pass = A and B and the holdout after cost.
  A fail closes the form; A pass with B fail is "비용에 달림".
- **Never:** another cell, cost, span, signal or margin after a result; the holdout for a form that failed A.

It reads only the research sandbox and writes nothing unless ``--json`` names a file.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys
from collections.abc import Sequence

TITLE = "Average Value Weighted Returns -- Monthly"
FIRST = 196307
DEV_END = 201606
HOLDOUT_START = 201607
HOLDOUT_END = 202609
SWITCH_COST = 0.002
SMA_MONTHS = 10

CELLS = {
    "F1": ("6_Portfolios_2x3.csv", "BIG HiBM", 0.0005),
    "F2": ("6_Portfolios_ME_OP_2x3.csv", "BIG HiOP", 0.0003),
    "F3": ("6_Portfolios_ME_INV_2x3.csv", "BIG LoINV", 0.0010),
    "F4": ("6_Portfolios_ME_Prior_12_2.csv", "BIG HiPRIOR", 0.0065),
}
F5_COST = sum(c for _, _, c in CELLS.values()) / 4.0
FORMS = ("F1", "F2", "F3", "F4", "F5", "E1", "E2")
_ROW = re.compile(r"^\d{6}$")


class ScreenError(RuntimeError):
    """A data file is not what the registration describes."""


def _rows(lines: Sequence[str], start: int) -> tuple[list[str], list[list[str]]]:
    """Header at ``start`` (first cell empty) and the YYYYMM rows that follow it."""
    header = [c.strip() for c in lines[start].split(",")]
    if header[0] != "":
        raise ScreenError(f"no header at line {start + 1}")
    rows: list[list[str]] = []
    for ln in lines[start + 1:]:
        cells = [c.strip() for c in ln.split(",")]
        if not _ROW.match(cells[0]):
            break
        rows.append(cells)
    if not rows:
        raise ScreenError(f"no monthly rows under line {start + 1}")
    return header, rows


def _series(header: list[str], rows: list[list[str]], col: str, last: int) -> dict[int, float]:
    if header.count(col) != 1:
        raise ScreenError(f"column {col!r} not found exactly once")
    i = header.index(col)
    out: dict[int, float] = {}
    for cells in rows:
        ym = int(cells[0])
        if ym < FIRST or ym > last:
            continue
        v = float(cells[i])
        if v <= -99.99:
            raise ScreenError(f"missing value for {col} at {ym}")
        out[ym] = v / 100.0
    return out


def load_cell(path: pathlib.Path, col: str, last: int) -> dict[int, float]:
    lines = path.read_text(encoding="latin-1").splitlines()
    hits = [i for i, ln in enumerate(lines) if ln.strip() == TITLE]
    if len(hits) != 1:
        raise ScreenError(f"{path.name}: section {TITLE!r} found {len(hits)} times")
    header, rows = _rows(lines, hits[0] + 1)
    return _series(header, rows, col, last)


def load_factors(path: pathlib.Path, last: int) -> tuple[dict[int, float], dict[int, float]]:
    lines = path.read_text(encoding="latin-1").splitlines()
    hits = [i for i, ln in enumerate(lines) if [c.strip() for c in ln.split(",")][:2] == ["", "Mkt-RF"]]
    if not hits:
        raise ScreenError(f"{path.name}: no Mkt-RF header")
    header, rows = _rows(lines, hits[0])
    if any(len(r[0]) != 6 for r in rows):
        raise ScreenError(f"{path.name}: first block is not monthly")
    mkt_rf = _series(header, rows, "Mkt-RF", last)
    rf = _series(header, rows, "RF", last)
    return {ym: mkt_rf[ym] + rf[ym] for ym in mkt_rf}, rf


def months(first: int, last: int) -> list[int]:
    out, y, m = [], first // 100, first % 100
    while y * 100 + m <= last:
        out.append(y * 100 + m)
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def timed(risky: list[float], rf: list[float], bench: list[float], cost: list[float]) -> tuple[list[float], list[float]]:
    """Hold ``risky`` or ``rf`` by the moving-average signal on ``bench``; return (before cost, after cost)."""
    level, levels = 1.0, []
    before: list[float] = []
    after: list[float] = []
    held_prev: bool | None = None
    for t in range(len(risky)):
        if t == 0 or len(levels) < SMA_MONTHS:
            hold = True
        else:
            window = levels[-SMA_MONTHS:]
            hold = levels[-1] > sum(window) / SMA_MONTHS
        r = risky[t] if hold else rf[t]
        c = cost[t] if hold else 0.0
        if held_prev is not None and hold != held_prev:
            c += SWITCH_COST
        before.append(r)
        after.append(r - c)
        held_prev = hold
        level *= 1.0 + bench[t]
        levels.append(level)
    return before, after


def metrics(r: Sequence[float], rf: Sequence[float]) -> dict[str, float]:
    n = len(r)
    wealth, peak, mdd = 1.0, 1.0, 0.0
    for x in r:
        wealth *= 1.0 + x
        peak = max(peak, wealth)
        mdd = min(mdd, wealth / peak - 1.0)
    ex = [a - b for a, b in zip(r, rf)]
    mean = sum(ex) / n
    sd = math.sqrt(sum((e - mean) ** 2 for e in ex) / (n - 1))
    return {"cagr": wealth ** (12.0 / n) - 1.0, "sr": mean / sd * math.sqrt(12.0), "mdd": mdd, "months": n}


def beats(form: dict[str, float], bench: dict[str, float]) -> bool:
    return form["cagr"] > bench["cagr"] and form["sr"] > bench["sr"] and form["mdd"] > bench["mdd"]


def build(data: pathlib.Path, last: int) -> tuple[list[int], dict[str, tuple[list[float], list[float]]], list[float], list[float]]:
    """Every form's (before, after) monthly returns over 1963-07 .. ``last``, plus B and RF."""
    bench_m, rf_m = load_factors(data / "F-F_Research_Data_Factors.csv", last)
    cells = {k: load_cell(data / f, col, last) for k, (f, col, _) in CELLS.items()}
    span = months(FIRST, last)
    for name, s in [("B", bench_m), ("RF", rf_m), *cells.items()]:
        if sorted(s) != span:
            raise ScreenError(f"{name}: months do not cover {FIRST}..{last} exactly")
    bench = [bench_m[t] for t in span]
    rf = [rf_m[t] for t in span]
    forms: dict[str, tuple[list[float], list[float]]] = {}
    for k, (_, _, c) in CELLS.items():
        r = [cells[k][t] for t in span]
        forms[k] = (r, [x - c for x in r])
    f5 = [sum(cells[k][t] for k in CELLS) / 4.0 for t in span]
    forms["F5"] = (f5, [x - F5_COST for x in f5])
    forms["E1"] = timed(bench, rf, bench, [0.0] * len(span))
    forms["E2"] = timed(f5, rf, bench, [F5_COST] * len(span))
    return span, forms, bench, rf


def judge(span: list[int], forms, bench, rf, first: int, last: int) -> dict:
    i0, i1 = span.index(first), span.index(last) + 1
    rf_s = rf[i0:i1]
    b = metrics(bench[i0:i1], rf_s)
    out = {"span": f"{first}..{last}", "B": b, "forms": {}}
    for k in FORMS:
        pre, post = forms[k]
        mp, mq = metrics(pre[i0:i1], rf_s), metrics(post[i0:i1], rf_s)
        out["forms"][k] = {"before": mp, "after": mq, "pass_before": beats(mp, b), "pass_after": beats(mq, b)}
    return out


def run(data: pathlib.Path, which: str) -> dict:
    if which == "dev":
        span, forms, bench, rf = build(data, DEV_END)
        dev = judge(span, forms, bench, rf, FIRST, DEV_END)
        for f in dev["forms"].values():
            f["gate_A"], f["gate_B"] = f["pass_before"], f["pass_after"]
        return {"dev": dev}
    span, forms, bench, rf = build(data, HOLDOUT_END)
    dev = judge(span, forms, bench, rf, FIRST, DEV_END)
    passers = [k for k in FORMS if dev["forms"][k]["pass_before"]]
    hold = judge(span, forms, bench, rf, HOLDOUT_START, HOLDOUT_END)
    hold["forms"] = {k: v for k, v in hold["forms"].items() if k in passers}
    verdict = {}
    for k in FORMS:
        a, bg = dev["forms"][k]["pass_before"], dev["forms"][k]["pass_after"]
        if not a:
            verdict[k] = "CLOSED_A"
        elif not bg:
            verdict[k] = "COST_DEPENDENT"
        else:
            verdict[k] = "FORM_PASS" if hold["forms"][k]["pass_after"] else "HOLDOUT_FAIL"
    return {"holdout": hold, "gate_A_passers": passers, "verdict": verdict}


def _fmt(m: dict[str, float]) -> str:
    return f"CAGR {m['cagr'] * 100:6.2f}%  SR {m['sr']:5.2f}  MDD {m['mdd'] * 100:7.2f}%"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("span", choices=("dev", "holdout"))
    ap.add_argument("--data", type=pathlib.Path, required=True)
    ap.add_argument("--json", type=pathlib.Path)
    a = ap.parse_args(argv)
    res = run(a.data, a.span)
    part = res["dev"] if a.span == "dev" else res["holdout"]
    print(f"span {part['span']}  B: {_fmt(part['B'])}")
    for k, f in part["forms"].items():
        print(f"{k} before {_fmt(f['before'])}  pass={f['pass_before']}")
        print(f"{k} after  {_fmt(f['after'])}  pass={f['pass_after']}")
    if a.span == "holdout":
        print("gate A passers:", ",".join(res["gate_A_passers"]) or "-")
        print("verdict:", json.dumps(res["verdict"]))
    if a.json:
        a.json.write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
