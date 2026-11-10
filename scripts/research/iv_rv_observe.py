"""IV–RV observation: how far did Deribit's implied volatility sit above what the index then realized? Forward only.

``docs/proposals/RESEARCH_DATA_SOURCES_V0.1.md`` stage 2 (Thomas 2026-10-06 exemption; D-A6 and Q2 2026-11-10). A
read-only observation: it is not a hypothesis, judges nothing, and no door reads it. It runs in the research sandbox
and writes nothing.

- **Forward only.** It reports days from :data:`OBSERVE_FROM` on. Implied minus realized volatility over past days is
  the gross return of a short-variance position, and no return is computed before a strategy's pre-registration
  (stage 3). For earlier days it prints dates and counts, never a value.
- **IV:** DVOL's daily close for day ``d``, annualized volatility in percent (Deribit's own index; 365-day year).
- **RV:** the Binance index's daily closes from ``d`` to ``d + 30``: ``100 × sqrt(365 / 30 × Σ r²)`` over the 30
  daily log returns, zero mean. A day without all 31 closes is *pending*.
- **Shown:** each observed day's IV, RV and IV − RV in volatility points, their count and mean, and the pending days.
  The two indexes differ (Deribit's against Binance's) [추정: by little on daily closes].
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
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime.cli_common import EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import fetch_deribit, lake  # noqa: E402

SPEC = "IV_RV_OBSERVATION_V0.1"
OBSERVE_FROM = date(2026, 11, 10)        # the day stage 2 was built; nothing before it is valued
HORIZON_DAYS = 30
YEAR_DAYS = 365
PAIRS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}


def read_dvol(cache: Path, currency: str) -> dict[date, float]:
    out: dict[date, float] = {}
    path = fetch_deribit.dvol_path(cache, currency)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                out[xsb._utc_day(float(row[0]))] = float(row[4])
    return out


def read_index(cache: Path, symbol: str) -> dict[date, float]:
    out: dict[date, float] = {}
    for row in lake.read_rows(lake.derivative_path(cache, "index", symbol, "1d")):
        try:
            out[xsb._utc_day(float(row[0]))] = float(row[4])
        except (TypeError, ValueError, IndexError):
            continue
    return out


def realized_vol(closes: Mapping[date, float], day: date) -> float | None:
    """Annualized close-to-close volatility (percent) over the :data:`HORIZON_DAYS` days after ``day``."""
    path = [closes.get(day + timedelta(days=k)) for k in range(HORIZON_DAYS + 1)]
    if any(c is None or c <= 0 for c in path):
        return None
    total = sum(math.log(b / a) ** 2 for a, b in zip(path, path[1:]))
    return 100 * math.sqrt(YEAR_DAYS / HORIZON_DAYS * total)


def observe(dvol: Mapping[date, float], index: Mapping[date, float], *, start: date = OBSERVE_FROM) -> dict[str, Any]:
    before = sorted(d for d in dvol if d < start)
    rows, pending = [], []
    for day in sorted(d for d in dvol if d >= start):           # nothing before ``start`` reaches a value
        rv = realized_vol(index, day)
        if rv is None:
            pending.append(day.isoformat())
            continue
        iv = dvol[day]
        rows.append({"day": day.isoformat(), "iv": round(iv, 2), "rv": round(rv, 2), "iv_minus_rv": round(iv - rv, 2)})
    gaps = [r["iv_minus_rv"] for r in rows]
    return {
        "observed": rows,
        "observed_days": len(rows),
        "mean_iv_minus_rv": round(statistics.fmean(gaps), 2) if gaps else None,
        "pending_days": pending,
        "before_start": {"days": len(before), "first": before[0].isoformat() if before else None,
                         "last": before[-1].isoformat() if before else None},
        "index_last_day": max(index).isoformat() if index else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iv_rv_observe", description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    report = {"spec": SPEC, "observe_from": OBSERVE_FROM.isoformat(), "horizon_days": HORIZON_DAYS,
              "currencies": {cur: observe(read_dvol(cache, cur), read_index(cache, sym)) for cur, sym in PAIRS.items()}}
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return EXIT_OK
    for cur, r in report["currencies"].items():
        print(f"{cur}: observed {r['observed_days']} day(s), mean IV-RV {r['mean_iv_minus_rv']} vol pts, "
              f"pending {len(r['pending_days'])}, DVOL before {OBSERVE_FROM} {r['before_start']['days']} day(s) "
              f"(not valued), index through {r['index_last_day']}")
        for row in r["observed"]:
            print(f"  {row['day']}: IV {row['iv']}  RV {row['rv']}  IV-RV {row['iv_minus_rv']}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
