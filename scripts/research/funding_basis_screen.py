"""Funding-basis screen: one fixed four-month forward window, kept when its mean daily return is positive.

``docs/proposals/STRATEGY_SCREEN_GATE_V0.1.md`` (Thomas 2026-11-09). The screen replaces the one-month verdict gate
of 2026-11-08 for research hypotheses. It drops hypotheses and never confirms one: a strategy it keeps is still
unconfirmed, and nothing it says reaches FORWARD_CONFIRMED, the LIVE door or a judgement rule.

- **Model:** ``FUNDING_BASIS_V0.1`` exactly (:func:`funding_basis.simulate_symbol`), same eleven symbols, costs,
  reset, liquidation and strategies. Positions open fresh at the window's first open.
- **Window (fixed):** :data:`WINDOW_START` to :data:`WINDOW_END`, every day after the pre-registration merged.
- **Verdict (per strategy):** ``SCREEN_KEEP`` when the mean daily portfolio return over the whole window is
  above zero, else ``SCREEN_DROP``. One-sided α 0.5; power 0.80 for a true annual Sharpe of 1.5 [계산].
- **No early reading:** until every symbol's bars and settlements cover the whole window the command reports
  ``NOT_YET`` with the day the data reaches, and nothing about returns. A missing bar inside the window is
  ``BLOCKED``, never filled.
- **Shown, never judged:** annualised return, Sharpe, funding share, worst month, drawdown, events.

It reads only the research cache and writes nothing.
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

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import funding_basis as fb  # noqa: E402

SPEC = "STRATEGY_SCREEN_GATE_V0.1"
WINDOW_START = date(2026, 12, 1)
WINDOW_END = date(2027, 3, 31)
LAST_SETTLEMENT_HOUR = 16                # an 8h or 4h schedule settles at or after 16:00 on every day

KEEP, DROP = "SCREEN_KEEP", "SCREEN_DROP"
NOT_YET, JUDGED = "NOT_YET", "JUDGED"


class Gap(Exception):
    """A day inside the window is missing while later data exists."""


def covered_through(series: fb.Series, start: date, end: date) -> date | None:
    """The last day ``d ≤ end`` such that bars ``start − 1 .. d`` and the settlements of ``d`` are all present.

    None when even ``start − 1`` / ``start`` is missing. Raises :class:`Gap` when a bar is missing but a later
    one inside the window exists (the archive skipped a day; never filled).
    """
    last_settled = series.settlements[-1][0] if series.settlements else -1
    through = None
    day = start - timedelta(days=1)
    while day <= end:
        if day not in series.days:
            if any(d in series.days for d in _days(day + timedelta(days=1), end)):
                raise Gap(f"{series.name} {day}: missing bar inside the window")
            break
        if day >= start:
            if last_settled < fb._ms(day) + LAST_SETTLEMENT_HOUR * fb.HOUR_MS:
                break
            through = day
        day += timedelta(days=1)
    return through


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def screen_strategy(strategy: str, series: Mapping[str, fb.Series], start: date, end: date) -> dict[str, Any]:
    return summarise(strategy, {name: fb.simulate_symbol(strategy, s, start, end) for name, s in series.items()})


def summarise(strategy: str, legs: Mapping[str, fb.Leg]) -> dict[str, Any]:
    """The verdict and the shown-only numbers over the legs' portfolio (also used by the v0.2.1 screen)."""
    daily, funding = fb.portfolio(legs)
    values = [daily[d] for d in sorted(daily)]
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    months = fb.by_month(daily)
    monthly = {m: round(sum(v) * 100, 4) for m, v in months.items()}
    worst = min(monthly, key=monthly.get)
    return {
        "strategy": strategy,
        "verdict": KEEP if mean > 0 else DROP,
        "days": len(values),
        "mean_bps_per_day": round(mean * 10_000, 4),
        "annualised_return_pct": round(mean * 365 * 100, 4),
        "annual_sharpe": None if sd == 0 else round(mean / sd * math.sqrt(365), 3),
        "sum_pct": round(sum(values) * 100, 4),
        "funding_sum_pct": round(sum(funding.values()) * 100, 4),
        "months_pct": monthly,
        "worst_month": {"month": worst, "pct": monthly[worst]},
        "max_drawdown_pct": round(fb.max_drawdown(daily) * 100, 4),
        "events": {name: {"entries": leg.entries, "exits": leg.exits, "resets": leg.resets,
                          "liquidations": leg.liquidations} for name, leg in legs.items()},
    }


def screen(series: Mapping[str, fb.Series], *, start: date = WINDOW_START, end: date = WINDOW_END) -> dict[str, Any]:
    """The report. ``NOT_YET`` carries only how far the data reaches; ``JUDGED`` carries the verdicts."""
    reach = {name: covered_through(s, start, end) for name, s in sorted(series.items())}
    head = {"spec": SPEC, "model": fb.SPEC, "window": [start.isoformat(), end.isoformat()],
            "symbols": sorted(series)}
    if any(d != end for d in reach.values()):
        last_bar = min((max(s.days) for s in series.values() if s.days), default=None)
        return {**head, "status": NOT_YET, "data_through": last_bar.isoformat() if last_bar else None,
                "short": sorted(n for n, d in reach.items() if d != end)}
    strategies = {name: screen_strategy(name, series, start, end) for name in fb.STRATEGIES}
    return {**head, "status": JUDGED, "input_digest": fb.input_digest(series), "strategies": strategies}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="funding_basis_screen", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("run",))
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    series, missing = fb.load(cache)
    if missing:
        print(f"BLOCKED: no index/premium/mark/funding/klines for {','.join(missing)} under {cache}",
              file=sys.stderr)
        return EXIT_BLOCKED
    try:
        report = {"measured_at": timeutil.utc_now_iso(), **fb.provenance(), **screen(series)}
    except (Gap, fb.DataGap) as gap:
        print(f"BLOCKED: {gap}", file=sys.stderr)
        return EXIT_BLOCKED
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return EXIT_OK
    if report["status"] == NOT_YET:
        print(f"NOT_YET: window {report['window'][0]}..{report['window'][1]}, data through "
              f"{report['data_through']}, short: {','.join(report['short'])}")
        return EXIT_OK
    for name, row in report["strategies"].items():
        print(f"{name}: {row['verdict']} | mean {row['mean_bps_per_day']} bps/day "
              f"({row['annualised_return_pct']}%/yr on capital) | SR {row['annual_sharpe']} "
              f"| worst {row['worst_month']} | maxDD {row['max_drawdown_pct']}%")
    print(f"input {report['input_digest']}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
