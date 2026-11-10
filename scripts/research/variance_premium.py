"""Variance premium, index proxy: does selling 30-day variance at Deribit's DVOL and paying what the index then
realized clear an annual Sharpe of 1.0 before any cost?

``docs/proposals/VARIANCE_PREMIUM_V0.1.md`` (pre-registered; Thomas 2026-11-10; stage 3a of
``RESEARCH_DATA_SOURCES_V0.1.md``). It runs in the research sandbox only, reads the research cache and writes
nothing.

- **What it is:** a necessary condition, not a strategy. DVOL is an index and cannot be traded, and this measurement
  charges no cost. A proxy that fails closes the direction; one that passes only earns the tradable measurement on
  option prices (stage 3b, its own pre-registration). Neither result reaches the forward screen.
- **Blocks:** one entry every :data:`HORIZON_DAYS` days from :data:`START`, so no two blocks share a day. The last
  entry is the last whose horizon ends by :data:`END`.
- **Implied:** DVOL's daily close on the entry day ``d`` (annualized volatility, percent).
- **Realized:** ``iv_rv_observe.realized_vol`` over ``d .. d + 30`` (Binance index daily closes, zero mean, 365-day
  year), the forward observation's own definition.
- **Payoff of a block:** ``(IV² − RV²) / (2 × IV)``, in volatility points: a variance swap sold at ``IV²`` per unit
  of vega notional. The portfolio's block is the mean over :data:`PAIRS`.
- **Rule:** the annual Sharpe of the blocks (block Sharpe × √(365 / 30)) is at least :data:`ENTRY_SHARPE`.
  Everything else is shown, never judged.
- **Missing data:** a block without its DVOL close or its 31 index closes, in either currency, is ``BLOCKED``.
  Nothing is imputed or skipped.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import iv_rv_observe as ivrv  # noqa: E402

SPEC = "VARIANCE_PREMIUM_V0.1"
PAIRS = ivrv.PAIRS                       # {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
HORIZON_DAYS = ivrv.HORIZON_DAYS         # 30
START = date(2021, 3, 24)                # DVOL's first daily bar
END = date(2026, 9, 30)                  # the last complete month the other candidates were measured through
ENTRY_SHARPE = 1.0
SPLIT = date(2024, 1, 1)                 # shown apart: an unreviewed backtest put the premium near zero after this


class DataGap(Exception):
    """A block lacks its DVOL close or one of its index closes."""


def entry_days(start: date = START, end: date = END) -> list[date]:
    """One entry every ``HORIZON_DAYS`` days from ``start``; the last one's horizon ends by ``end``."""
    out, day = [], start
    while day + timedelta(days=HORIZON_DAYS) <= end:
        out.append(day)
        day += timedelta(days=HORIZON_DAYS)
    return out


def payoff(iv: float, rv: float) -> float:
    """A variance swap sold at ``iv²`` and settled at ``rv²``, per unit of vega notional, in volatility points."""
    return (iv * iv - rv * rv) / (2 * iv)


def blocks(dvol: Mapping[date, float], index: Mapping[date, float], *, name: str = "",
           start: date = START, end: date = END) -> dict[date, float]:
    out: dict[date, float] = {}
    for day in entry_days(start, end):
        iv = dvol.get(day)
        rv = ivrv.realized_vol(index, day)
        if iv is None or iv <= 0 or rv is None:
            raise DataGap(f"{name} {day}: no DVOL close or not all 31 index closes")
        out[day] = payoff(iv, rv)
    return out


def portfolio(per_currency: Mapping[str, Mapping[date, float]]) -> dict[date, float]:
    days = sorted(set.intersection(*(set(b) for b in per_currency.values())))
    if any(set(b) != set(days) for b in per_currency.values()):
        raise DataGap("the currencies do not share every block")
    return {d: statistics.fmean(b[d] for b in per_currency.values()) for d in days}


def _annual(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    sd = statistics.stdev(values)
    return None if sd == 0 else round(statistics.fmean(values) / sd * math.sqrt(365 / HORIZON_DAYS), 3)


def _shown(series: Mapping[date, float]) -> dict[str, Any]:
    values = [series[d] for d in sorted(series)]
    return {"blocks": len(values), "annual_sharpe": _annual(values),
            "mean_vol_points": round(statistics.fmean(values), 3) if values else None,
            "positive_blocks": sum(1 for v in values if v > 0)}


def measure(per_currency: Mapping[str, Mapping[date, float]]) -> dict[str, Any]:
    whole = portfolio(per_currency)
    days = sorted(whole)
    values = [whole[d] for d in days]
    worst = min(days, key=whole.get)
    head = _shown(whole)
    return {
        "strategy": "V", "first_entry": days[0].isoformat(), "last_entry": days[-1].isoformat(), **head,
        "entry_pass": head["annual_sharpe"] is not None and head["annual_sharpe"] >= ENTRY_SHARPE,
        "worst_block": {"entry": worst.isoformat(), "vol_points": round(whole[worst], 3)},
        "years": {str(y): round(sum(whole[d] for d in days if d.year == y), 3) for y in sorted({d.year for d in days})},
        "before_split": _shown({d: whole[d] for d in days if d < SPLIT}),
        "from_split": _shown({d: whole[d] for d in days if d >= SPLIT}),
        "by_currency": {cur: _shown(b) for cur, b in per_currency.items()},
        "max_drawdown_vol_points": round(_drawdown(values), 3),
    }


def _drawdown(values: Sequence[float]) -> float:
    peak = total = worst = 0.0
    for v in values:
        total += v
        peak = max(peak, total)
        worst = min(worst, total - peak)
    return worst


def coverage(data: Mapping[str, tuple[Mapping[date, float], Mapping[date, float]]]) -> dict[str, Any]:
    """Dates and counts only: no volatility value leaves this function."""
    days = entry_days()
    out: dict[str, Any] = {"entries": len(days), "first_entry": days[0].isoformat(), "last_entry": days[-1].isoformat()}
    for cur, (dvol, index) in data.items():
        no_iv = [d.isoformat() for d in days if dvol.get(d) is None]
        no_rv = [d.isoformat() for d in days if ivrv.realized_vol(index, d) is None]
        out[cur] = {"dvol_days": len(dvol), "index_days": len(index), "entries_without_dvol": no_iv,
                    "entries_without_index": no_rv}
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="variance_premium", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("load", "run"),
                        help="load: dates and counts only, no payoff; run: the measurement")
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    data = {cur: (ivrv.read_dvol(cache, cur), ivrv.read_index(cache, sym)) for cur, sym in PAIRS.items()}
    head = {"spec": SPEC, "horizon_days": HORIZON_DAYS, "span": [START.isoformat(), END.isoformat()],
            "entry_sharpe": ENTRY_SHARPE, "coverage": coverage(data)}
    if args.command == "load":
        print(json.dumps(head, indent=1, sort_keys=True))
        return EXIT_OK
    try:
        result = measure({cur: blocks(dvol, index, name=cur) for cur, (dvol, index) in data.items()})
    except DataGap as gap:
        print(f"BLOCKED: {gap}", file=sys.stderr)
        return EXIT_BLOCKED
    report = {"measured_at": timeutil.utc_now_iso(), **head, "strategies": {"V": result},
              "overall": "PROXY_PASS" if result["entry_pass"] else "PROXY_FAIL"}
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return EXIT_OK
    print(f"V: annual SR {result['annual_sharpe']} over {result['blocks']} blocks "
          f"({result['first_entry']}..{result['last_entry']}) -> {report['overall']} | mean "
          f"{result['mean_vol_points']} vol pts | positive {result['positive_blocks']} | worst {result['worst_block']}")
    print(f"  before {SPLIT}: {result['before_split']} | from {SPLIT}: {result['from_split']}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
