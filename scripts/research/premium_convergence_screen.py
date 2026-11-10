"""Premium convergence screen: PC over one fixed four-month forward window, kept when its mean daily return is positive.

``docs/proposals/PREMIUM_CONVERGENCE_V0.1.md`` §5 (Thomas 2026-11-10). The official run (ENTRY_PASS, annual Sharpe
1.021) sends PC to the screen. The rule is ``STRATEGY_SCREEN_GATE_V0.1``: ``SCREEN_KEEP`` when the mean daily
portfolio return over the whole window is above zero, else ``SCREEN_DROP``. A strategy it keeps is still
unconfirmed. A window with no trade has a mean of exactly zero and is ``SCREEN_DROP``.

- **Model:** ``PREMIUM_CONVERGENCE_V0.1`` unchanged (:func:`premium_convergence.simulate_symbol`): the same six
  symbols, threshold, exit, costs, reset, liquidation and missing-bar rules. It starts flat at the window's first
  open, whose decision reads the bar before it, as every hour's does. A position open at the window's last close is
  marked there and not closed, as in the entry run.
- **Window:** :data:`premium_convergence.SCREEN_WINDOW` only (one source of truth, no override).
- **Coverage before any simulation.** Data not fetched yet looks like a flat market (a missing hour is skipped
  while flat), so a short cache would read as ``SCREEN_DROP``. Each symbol needs:
  1. index and mark bars through the window's last hour, and a premium bar at that hour too;
  2. funding settlements on every window day, through :data:`funding_basis_screen.LAST_SETTLEMENT_HOUR` of the last.
     Funding comes from the monthly archive (``funding_basis_refresh``), not from the 1h bars, so a month the
     refresh skipped would otherwise count as no funding.
  Anything short is ``NOT_YET`` with dates only. Holes inside the window are left to the model's own rules
  (``PREMIUM_CONVERGENCE_V0.1.md`` §2): skipped while flat, ``BLOCKED`` while held.
- **No early reading:** a ``NOT_YET`` report carries how far the data reaches and what is short, nothing about
  returns.
- **Shown, never judged:** annualised return, Sharpe, funding, worst month, drawdown, events, held hours and the
  window's missing-hour counts.

It reads only the research cache and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import funding_basis as fb  # noqa: E402
from scripts.research import funding_basis_screen as sc  # noqa: E402
from scripts.research import premium_convergence as pc  # noqa: E402

SPEC = sc.SPEC
KEEP, DROP, NOT_YET, JUDGED = sc.KEEP, sc.DROP, sc.NOT_YET, sc.JUDGED


def _iso_hour(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%HZ")


def shortfall(series: pc.Series, start: date, end: date) -> list[str]:
    """What this symbol still lacks for the window, as dates only; empty when it is covered."""
    last_hour = pc._ms(end) + 23 * pc.HOUR_MS
    short = []
    bars = [t for t in series.hours if t <= last_hour]
    if not bars or max(bars) < last_hour:
        short.append(f"{series.name} index/mark through {_iso_hour(max(bars)) if bars else None}")
    priced = [t for t, h in series.hours.items() if h.premium is not None and t <= last_hour]
    if not priced or max(priced) < last_hour:
        short.append(f"{series.name} premium through {_iso_hour(max(priced)) if priced else None}")
    settled = {datetime.fromtimestamp(t / 1000, tz=timezone.utc).date() for t in series.settlements}
    unsettled = [d for d in sc._days(start, end) if d not in settled]
    if unsettled:
        short.append(f"{series.name} funding missing from {unsettled[0].isoformat()} ({len(unsettled)} day(s))")
    elif max(series.settlements) < pc._ms(end) + sc.LAST_SETTLEMENT_HOUR * pc.HOUR_MS:
        short.append(f"{series.name} funding through {_iso_hour(max(series.settlements))}")
    return short


def screen(series: Mapping[str, pc.Series]) -> dict[str, Any]:
    """The report. ``NOT_YET`` carries only how far the data reaches; ``JUDGED`` carries the verdict."""
    start, end = pc.SCREEN_WINDOW
    head = {"spec": SPEC, "model": pc.SPEC, "window": [start.isoformat(), end.isoformat()],
            "symbols": sorted(series), "strategies_screened": list(pc.STRATEGIES)}
    short = [line for name in sorted(series) for line in shortfall(series[name], start, end)]
    if short:
        last = min((max(s.hours) for s in series.values() if s.hours), default=None)
        return {**head, "status": NOT_YET, "data_through": None if last is None else _iso_hour(last),
                "short": short}
    legs = {name: pc.simulate_symbol(s, start, end) for name, s in sorted(series.items())}
    (strategy,) = pc.STRATEGIES
    row = sc.summarise(strategy, legs)
    for name, leg in legs.items():
        row["events"][name]["held_hours"] = leg.held_hours
        row["events"][name]["bridged_hours"] = leg.bridged_hours
    gaps = pc.coverage(series, start, end)
    row["window_missing_hours"] = {name: {"index_or_mark": c["missing_index_or_mark_count"],
                                          "premium": c["missing_premium_count"]} for name, c in gaps.items()}
    return {**head, "status": JUDGED, "threshold_pct": round(pc.threshold() * 100, 4),
            "input_digest": pc.input_digest(series), "strategies": {strategy: row}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="premium_convergence_screen", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("run",))
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    series, missing = pc.load(cache)
    if missing:
        print(f"BLOCKED: no 1h index/premium/mark bars, funding or archive klines for {','.join(missing)} "
              f"under {cache}", file=sys.stderr)
        return EXIT_BLOCKED
    try:
        report = {"measured_at": timeutil.utc_now_iso(), **fb.provenance(), **screen(series)}
    except pc.DataGap as gap:
        print(f"BLOCKED: {gap}", file=sys.stderr)
        return EXIT_BLOCKED
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return EXIT_OK
    if report["status"] == NOT_YET:
        print(f"NOT_YET: window {report['window'][0]}..{report['window'][1]}, data through "
              f"{report['data_through']}; short: {'; '.join(report['short'])}")
        return EXIT_OK
    for name, row in report["strategies"].items():
        print(f"{name}: {row['verdict']} | mean {row['mean_bps_per_day']} bps/day "
              f"({row['annualised_return_pct']}%/yr on capital) | SR {row['annual_sharpe']} "
              f"| worst {row['worst_month']} | maxDD {row['max_drawdown_pct']}%")
    print(f"input {report['input_digest']}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
