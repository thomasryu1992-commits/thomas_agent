"""E1 once on US 1926-07 – 1963-06 with Korean tax and margins (stock design, steps 2/5/6).

``docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md`` §179 is the registration (Thomas 2026-11-25, "예, 등록대로"). The
signal, switch cost and measures are :mod:`scripts.research.kf_form_screen` unchanged. What is new:

- **Data:** the monthly block of ``F-F_Research_Data_Factors.csv`` (fetched 2026-11-25), rows 1926-07 – 1963-06 only
  (444 months). No row after 1963-06 is converted. Read once as a whole.
- **B:** Mkt-RF + RF, bought and held (never sold, so no capital-gains tax).
- **E1:** the 10-month moving-average signal on B's index (index from 1926-07; first signal decides 1927-05), 0.2%
  one-way per switch, charged in the month the new position is held.
- **Tax:** 22% on each calendar year's net realized gain, deducted from wealth at the end of December. A gain is
  realized when E1 leaves the market: proceeds net of the switch cost minus the basis (1 at the start; on re-entry the
  wealth invested net of the switch cost). No loss carry-forward. 15.4% of RF in every month E1 holds RF and RF > 0.
  Nothing is liquidated at the end of the span (a 1963 realization would be taxed in December 1963, outside it).
- **Judgement:** after cost and tax, E1's SR > B's SR + 0.05 AND E1's maximum drawdown > B's + 0.10 (10 points
  shallower). Ties fail; CAGR reported only. Pass before cost and tax but not after → "비용 · 세금에 달림".
  After-cost, before-tax figures are reported, never judged.
- **Never:** another tax rate, cost, margin or span after the result; a second read.

Divisor across the four screens: 20. It reads only the research sandbox and writes nothing unless ``--json`` names a
file.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Sequence

from scripts.research import kf_form_screen as kf

FIRST = 192607
LAST = 196306
CG_TAX = 0.22
INTEREST_TAX = 0.154
SR_MARGIN = 0.05
MDD_MARGIN = 0.10
FACTORS = "F-F_Research_Data_Factors.csv"


def load(path: pathlib.Path) -> tuple[list[int], list[float], list[float]]:
    """B (Mkt-RF + RF) and RF for 1926-07 .. 1963-06; rows outside the span are never converted."""
    lines = path.read_text(encoding="latin-1").splitlines()
    hits = [i for i, ln in enumerate(lines) if [c.strip() for c in ln.split(",")][:2] == ["", "Mkt-RF"]]
    if not hits:
        raise kf.ScreenError(f"{path.name}: no Mkt-RF header")
    header, rows = kf._rows(lines, hits[0])
    if any(len(r[0]) != 6 for r in rows):
        raise kf.ScreenError(f"{path.name}: first block is not monthly")
    if header.count("Mkt-RF") != 1 or header.count("RF") != 1:
        raise kf.ScreenError(f"{path.name}: Mkt-RF / RF not found exactly once")
    im, ir = header.index("Mkt-RF"), header.index("RF")
    span = kf.months(FIRST, LAST)
    got: dict[int, tuple[float, float]] = {}
    for cells in rows:
        ym = int(cells[0])
        if ym < FIRST or ym > LAST:
            continue
        m, r = float(cells[im]), float(cells[ir])
        if m <= -99.99 or r <= -99.99:
            raise kf.ScreenError(f"missing value at {ym}")
        got[ym] = ((m + r) / 100.0, r / 100.0)
    if sorted(got) != span:
        raise kf.ScreenError(f"months do not cover {FIRST}..{LAST} exactly")
    return span, [got[t][0] for t in span], [got[t][1] for t in span]


def holds(bench: list[float]) -> list[bool]:
    """E1's position each month (True = market), the signal of :func:`kf_form_screen.timed`."""
    level, levels, out = 1.0, [], []
    for x in bench:
        out.append(True if len(levels) < kf.SMA_MONTHS else levels[-1] > sum(levels[-kf.SMA_MONTHS:]) / kf.SMA_MONTHS)
        level *= 1.0 + x
        levels.append(level)
    return out


def taxed(span: list[int], bench: list[float], rf: list[float], cg: float = CG_TAX,
          interest: float = INTEREST_TAX) -> tuple[list[float], dict[int, float]]:
    """E1's monthly returns after switch cost and tax; also the capital-gains tax paid per year (as wealth)."""
    position = holds(bench)
    wealth, basis, year_gain = 1.0, 1.0, 0.0
    held_prev: bool | None = None
    out: list[float] = []
    paid: dict[int, float] = {}
    for t, ym in enumerate(span):
        hold = position[t]
        start = wealth
        switched = held_prev is not None and hold != held_prev
        if switched and held_prev:  # leaving the market at the end of the previous month
            year_gain += start * (1.0 - kf.SWITCH_COST) - basis
        if switched and hold:  # re-entering: the basis is what goes in, net of the switch cost
            basis = start * (1.0 - kf.SWITCH_COST)
        r = bench[t] if hold else rf[t]
        if switched:
            r -= kf.SWITCH_COST
        if not hold and rf[t] > 0:
            r -= interest * rf[t]
        wealth = start * (1.0 + r)
        if ym % 100 == 12:
            if year_gain > 0:
                tax = cg * year_gain
                wealth -= tax
                paid[ym // 100] = tax
            year_gain = 0.0
        out.append(wealth / start - 1.0)
        held_prev = hold
    return out, paid


def beats_margin(form: dict[str, float], bench: dict[str, float]) -> bool:
    return form["sr"] > bench["sr"] + SR_MARGIN and form["mdd"] > bench["mdd"] + MDD_MARGIN


def run(data: pathlib.Path) -> dict:
    span, bench, rf = load(data / FACTORS)
    before, after_cost = kf.timed(bench, rf, bench, [0.0] * len(span))
    after, paid = taxed(span, bench, rf)
    b = kf.metrics(bench, rf)
    m0, m1, m2 = kf.metrics(before, rf), kf.metrics(after_cost, rf), kf.metrics(after, rf)
    p0, p2 = beats_margin(m0, b), beats_margin(m2, b)
    held = holds(bench)
    switches = sum(1 for i in range(1, len(held)) if held[i] != held[i - 1])
    return {
        "span": f"{FIRST}..{LAST}", "B": b,
        "E1": {"before": m0, "after_cost": m1, "after_cost_tax": m2,
               "pass_before": p0, "pass_after": p2, "months_in_cash": held.count(False), "switches": switches,
               "cg_tax_paid_by_year": paid},
        "verdict": "FORM_PASS" if p2 else ("COST_TAX_DEPENDENT" if p0 else "FAIL"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=pathlib.Path, required=True, help="folder with F-F_Research_Data_Factors.csv")
    ap.add_argument("--json", type=pathlib.Path)
    a = ap.parse_args(argv)
    res = run(a.data)
    e = res["E1"]
    print(f"span {res['span']}  B: {kf._fmt(res['B'])}")
    print(f"E1 before          {kf._fmt(e['before'])}  pass={e['pass_before']}")
    print(f"E1 after cost      {kf._fmt(e['after_cost'])}  (not judged)")
    print(f"E1 after cost+tax  {kf._fmt(e['after_cost_tax'])}  pass={e['pass_after']}")
    print(f"months in cash {e['months_in_cash']}  switches {e['switches']}")
    print("verdict:", res["verdict"])
    if a.json:
        a.json.write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
