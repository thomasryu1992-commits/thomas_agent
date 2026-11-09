"""Funding basis v0.2: the same carry over a point-in-time monthly top-30 universe — does it clear the entry floor?

``docs/proposals/FUNDING_BASIS_V0.2.md`` (pre-registered; Thomas 2026-11-09, "기본값대로"). Under
``STRATEGY_SCREEN_GATE_V0.1`` a hypothesis goes to the four-month forward screen only when its historical annual
Sharpe is at least :data:`ENTRY_SHARPE`. This measures that number. It reads only the research cache and writes
nothing.

- **Model:** ``FUNDING_BASIS_V0.1`` per symbol (positions, funding, price PnL, reset, liquidation, costs,
  strategies), reusing its functions. What differs is who is held, and when:
- **Universe (month M):** the :data:`TOP_N` USDT crypto perps by quote volume over month M − 1 in the archive's
  1d klines (delisted perps included, TradFi perps left out), kept when Binance spot held a USDT pair for that
  perp in month M − 1 (the spot archive has that month's 1d file; a ``1000``-prefixed perp maps to its unprefixed
  spot pair). Seasoning as v0.1.
- **Leaving:** a symbol that is held on the last day of its member month, and is not a member next month, is
  closed at that close with exit costs. A perp whose bars end while held (delisted) is closed at its last close.
- **Missing data:** a member month in which the perp traded (the archive has its 1d kline) on a day that lacks
  the index, premium or mark bar, or in which no funding settled, is excluded for that symbol. Each exclusion is
  listed. A member perp with no readable series is excluded for all its member months. Days the perp did not
  trade (after a delisting) are not exclusions: nothing could be held then. When excluded member-months exceed :data:`MAX_EXCLUDED_SHARE` of all member-months the run is
  BLOCKED.
- **Portfolio:** each day the mean, over that day's members, of the return on capital (idle = 0).
- **Entry floor (per strategy):** annual Sharpe (monthly Sharpe × √12, complete months
  :data:`START`..:data:`END`) ``≥`` :data:`ENTRY_SHARPE`. A strategy that clears it is screened over
  :data:`SCREEN_WINDOW`. Everything else is shown, never judged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import funding_basis as fb  # noqa: E402

SPEC = "FUNDING_BASIS_V0.2"
START = fb.START
END = fb.END
TOP_N = 30
ENTRY_SHARPE = 1.0                       # STRATEGY_SCREEN_GATE_V0.1 entry floor (annual)
SCREEN_WINDOW = (date(2027, 1, 1), date(2027, 4, 30))
MAX_EXCLUDED_SHARE = 0.05
RECENT_MONTHS = 24
TRADIFI = "TRADIFI_PERPETUAL"
SPOT_PREFIXES = ("1000000", "1000")
_SPOT_FILE = re.compile(r"-1d-(\d{4})-(\d{2})\.zip\Z")


class Blocked(Exception):
    """The inputs cannot support a verdict (too many exclusions, no universe)."""


# --- the universe -----------------------------------------------------------------------------------------

def month_key(day: date) -> str:
    return fb.month_key(day)


def prev_month(key: str) -> str:
    y, m = int(key[:4]), int(key[5:])
    return f"{y - (m == 1):04d}-{(m - 2) % 12 + 1:02d}"


def months_between(start: date, end: date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = y + (m == 12), m % 12 + 1
    return out


def spot_pair(perp: str, spot_listing: set[str]) -> str | None:
    if perp in spot_listing:
        return perp
    for prefix in SPOT_PREFIXES:
        if perp.startswith(prefix) and perp[len(prefix):] in spot_listing:
            return perp[len(prefix):]
    return None


def universe(volumes: Mapping[str, Mapping[str, float]], spot_months: Mapping[str, set[str]],
             spot_listing: set[str], months: Sequence[str]) -> dict[str, list[str]]:
    """``{month: [members]}``: top :data:`TOP_N` by last month's volume, then those with a spot pair last month."""
    out = {}
    for month in months:
        last = prev_month(month)
        ranked = sorted(volumes.get(last, {}).items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_N]
        members = []
        for perp, _ in ranked:
            pair = spot_pair(perp, spot_listing)
            if pair is not None and last in spot_months.get(pair, set()):
                members.append(perp)
        out[month] = sorted(members)
    return out


def missing_months(series: fb.Series, traded: set[date], months: Sequence[str]) -> list[str]:
    """Member months with a traded day that has no derivative bar (or no bar the day before the month), or no
    settlement at all."""
    settled = {month_key(xsb._utc_day(t)) for t, _ in series.settlements}
    out = []
    for month in months:
        days = sorted(d for d in traded if month_key(d) == month)
        if not days:
            continue
        need = set(days)
        if days[0].day == 1:
            need.add(days[0] - timedelta(days=1))         # the first open needs the previous close
        if (need - set(series.days)) or month not in settled:
            out.append(month)
    return out


# --- one symbol with membership ---------------------------------------------------------------------------

def simulate_member(strategy: str, series: fb.Series, member_months: set[str], start: date = START,
                    end: date = END) -> fb.Leg:
    """``fb.simulate_symbol`` with membership: returns only on member days; exit at the close before leaving."""
    import bisect

    leg = fb.Leg()
    by_time = dict(series.settlements)
    times = sorted(by_time)
    last_bar = max(series.days) if series.days else start
    open_ = held_yesterday = False
    s0 = p0 = m0 = 0.0
    value = 0.0
    day = start
    while day <= end:
        if month_key(day) not in member_months or not series.eligible(day):
            if open_:
                raise fb.DataGap(f"{series.name} {day}: open outside membership or without a bar")
            held_yesterday = False
            day += timedelta(days=1)
            continue
        prev, today = series.days[day - timedelta(days=1)], series.days[day]
        pnl = funding = 0.0
        hold = fb.wants(strategy, series, day, open_)
        if open_ and not hold:
            pnl -= fb._costs(prev.index / s0, prev.index * (1 + prev.premium) / p0)
            open_, leg.exits = False, leg.exits + 1
        if not open_ and hold:
            s0, p0, m0 = prev.index, prev.index * (1 + prev.premium), prev.mark
            value, held_yesterday = 0.0, False
            pnl -= fb._costs(1.0, 1.0)
            open_, leg.entries = True, leg.entries + 1
        if open_:
            day_open = fb._ms(day)
            if today.mark_high >= fb.LIQUIDATION_RATIO * m0:
                spot = today.index / s0 - 1
                pnl += (spot - 1.0) - value
                pnl -= fb._costs(today.index / s0, 0.0)
                open_, held_yesterday = False, False
                leg.liquidations += 1
            else:
                if held_yesterday and day_open in by_time:
                    funding += by_time[day_open] * prev.mark / p0
                lo = bisect.bisect_right(times, day_open)
                hi = bisect.bisect_left(times, day_open + fb.DAY_MS)
                funding += sum(by_time[t] for t in times[lo:hi]) * today.mark / p0
                new_value = today.index / s0 - today.index * (1 + today.premium) / p0
                pnl += funding + (new_value - value)
                value = new_value
                tomorrow = day + timedelta(days=1)
                leaving = (day == last_bar or month_key(tomorrow) not in member_months
                           or tomorrow not in series.days)
                if leaving:                                       # close at this close
                    pnl -= fb._costs(today.index / s0, today.index * (1 + today.premium) / p0)
                    open_, held_yesterday = False, False
                    leg.exits += 1
                else:
                    if today.index / s0 >= fb.RESET_RATIO:
                        pnl -= fb._costs(today.index / s0, today.index * (1 + today.premium) / p0) \
                            + fb._costs(1.0, 1.0)
                        s0, p0, m0 = today.index, today.index * (1 + today.premium), today.mark
                        value = 0.0
                        leg.resets += 1
                    held_yesterday = True
        else:
            held_yesterday = False
        leg.returns[day] = pnl / fb.CAPITAL
        leg.funding[day] = funding / fb.CAPITAL
        day += timedelta(days=1)
    return leg


# --- measurement ------------------------------------------------------------------------------------------

@dataclass
class Inputs:
    series: dict[str, fb.Series]
    members: dict[str, list[str]]            # month -> members (before exclusions)
    excluded: dict[str, list[str]]          # symbol -> excluded member months


def member_months(inputs: Inputs) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for month, symbols in inputs.members.items():
        for symbol in symbols:
            out[symbol].add(month)
    return {s: ms - set(inputs.excluded.get(s, ())) for s, ms in out.items() if s in inputs.series}


def _sharpe(values: Sequence[float]) -> float | None:
    return fb._sharpe(values)


def measure_strategy(strategy: str, inputs: Inputs, *, start: date = START, end: date = END) -> dict[str, Any]:
    months_by_symbol = member_months(inputs)
    legs = {s: simulate_member(strategy, inputs.series[s], ms, start, end)
            for s, ms in sorted(months_by_symbol.items()) if ms}
    daily, funding = fb.portfolio(legs)
    by = fb.by_month(daily)
    complete = [m for m in sorted(by) if len(by[m]) == fb._days_in_month(m)]
    monthly = [sum(by[m]) for m in complete]
    ex_funding = [sum(by[m]) - sum(v for d, v in funding.items() if month_key(d) == m) for m in complete]
    sharpe = _sharpe(monthly)
    annual = None if sharpe is None else sharpe * math.sqrt(12)
    recent = monthly[-RECENT_MONTHS:]
    recent_sharpe = _sharpe(recent)
    liquidations = {s: leg.liquidations for s, leg in legs.items() if leg.liquidations}
    return {
        "strategy": strategy,
        "entry": {"annual_sharpe": None if annual is None else round(annual, 3), "floor": ENTRY_SHARPE,
                  "pass": annual is not None and annual >= ENTRY_SHARPE},
        "monthly_sharpe": None if sharpe is None else round(sharpe, 3),
        "months": len(complete),
        "mean_month_pct": round(statistics.fmean(monthly) * 100, 4) if monthly else None,
        "mean_month_ex_funding_pct": round(statistics.fmean(ex_funding) * 100, 4) if monthly else None,
        "recent": {"months": len(recent),
                   "annual_sharpe": None if recent_sharpe is None else round(recent_sharpe * math.sqrt(12), 3),
                   "annual_return_pct": round(statistics.fmean(recent) * 12 * 100, 4) if recent else None},
        "worst_month": None if not monthly else
        {"month": complete[monthly.index(min(monthly))], "pct": round(min(monthly) * 100, 4)},
        "negative_months": sum(1 for v in monthly if v < 0),
        "max_drawdown_pct": round(fb.max_drawdown(daily) * 100, 4),
        "stress_months_pct": {m: (round(sum(by[m]) * 100, 4) if m in by else None) for m in fb.STRESS_MONTHS},
        "years": {y: round(sum(sum(by[m]) for m in complete if m.startswith(y)) * 100, 4)
                  for y in sorted({m[:4] for m in complete})},
        "symbols_traded": sum(1 for leg in legs.values() if leg.entries),
        "events": {"entries": sum(l.entries for l in legs.values()), "exits": sum(l.exits for l in legs.values()),
                   "resets": sum(l.resets for l in legs.values()),
                   "liquidations": sum(l.liquidations for l in legs.values())},
        "liquidations_by_symbol": dict(sorted(liquidations.items(), key=lambda kv: (-kv[1], kv[0]))),
        "monthly_pct": {m: round(v * 100, 4) for m, v in zip(complete, monthly)},
    }


def universe_digest(members: Mapping[str, Sequence[str]]) -> str:
    h = hashlib.sha256()
    for month in sorted(members):
        h.update(f"{month}|{','.join(members[month])}".encode())
    return h.hexdigest()[:16]


def measure(inputs: Inputs, *, start: date = START, end: date = END) -> dict[str, Any]:
    member_total = sum(len(v) for v in inputs.members.values())
    excluded_total = sum(len(v) for v in inputs.excluded.values())
    if member_total == 0:
        raise Blocked("no member in any month")
    share = excluded_total / member_total
    if share > MAX_EXCLUDED_SHARE:
        raise Blocked(f"excluded member-months {excluded_total}/{member_total} = {share:.3f} > {MAX_EXCLUDED_SHARE}")
    strategies = {name: measure_strategy(name, inputs, start=start, end=end) for name in fb.STRATEGIES}
    passed = sorted(n for n, r in strategies.items() if r["entry"]["pass"])
    return {
        "spec": SPEC, "span": [start.isoformat(), end.isoformat()],
        "universe": {"digest": universe_digest(inputs.members), "distinct": len({s for v in inputs.members.values()
                                                                                for s in v}),
                     "mean_members": round(member_total / len(inputs.members), 2),
                     "member_months": member_total, "excluded_member_months": excluded_total,
                     "excluded": {s: sorted(v) for s, v in sorted(inputs.excluded.items()) if v}},
        "input_digest": fb.input_digest(inputs.series),
        "strategies": strategies,
        "overall": "ENTRY_PASS" if passed else "ENTRY_FAIL",
        "screen": {"strategies": passed, "window": [d.isoformat() for d in SCREEN_WINDOW]} if passed else None,
    }


# --- loading ----------------------------------------------------------------------------------------------

def read_volumes(archive: Path, symbols: Sequence[str], contract_types: Mapping[str, str]
                 ) -> tuple[dict[str, dict[str, float]], dict[str, set[date]]]:
    """``({month: {perp: quote volume}}, {perp: traded days})`` from the archive's 1d klines, TradFi left out."""
    out: dict[str, dict[str, float]] = defaultdict(dict)
    traded: dict[str, set[date]] = {}
    for symbol in symbols:
        if contract_types.get(symbol) == TRADIFI:
            continue
        bars = xsb.read_klines(archive / xsb.KLINES_KIND / symbol)
        traded[symbol] = set(bars)
        for day, bar in bars.items():
            key = month_key(day)
            out[key][symbol] = out[key].get(symbol, 0.0) + bar.quote_volume
    return out, traded


def read_spot_months(spot_archive: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    folder = spot_archive / xsb.KLINES_KIND
    for sub in sorted(folder.iterdir()) if folder.exists() else []:
        months = set()
        for path in sub.glob("*.zip"):
            match = _SPOT_FILE.search(path.name)
            if match:
                months.add(f"{match.group(1)}-{match.group(2)}")
        out[sub.name] = months
    return out


def load(cache: Path, *, start: date = START, end: date = END) -> Inputs:
    archive = cache / "archive"
    spot_archive = cache / "archive-spot"
    perps = json.loads((archive / "symbols.json").read_text())
    contract_types = json.loads((archive / "contract_types.json").read_text())["contract_types"]
    spot_listing = set(json.loads((spot_archive / "symbols.json").read_text()))
    volumes, traded = read_volumes(archive, perps, contract_types)
    members = universe(volumes, read_spot_months(spot_archive), spot_listing, months_between(start, end))
    series: dict[str, fb.Series] = {}
    excluded: dict[str, list[str]] = {}
    for symbol in sorted({s for v in members.values() for s in v}):
        months = [m for m, v in members.items() if symbol in v]
        s = fb.read_series(cache, symbol)
        if s is None:
            excluded[symbol] = months
            continue
        s.name = symbol
        series[symbol] = s
        missing = missing_months(s, traded.get(symbol, set()), months)
        if missing:
            excluded[symbol] = missing
    return Inputs(series=series, members=members, excluded=excluded)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="funding_basis_v02", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("run", "load"))
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    try:
        inputs = load(cache)
    except (OSError, ValueError, KeyError) as error:
        print(f"BLOCKED: cannot read the inputs under {cache}: {error}", file=sys.stderr)
        return EXIT_BLOCKED
    if args.command == "load":                  # loading only: the universe and its gaps, no returns
        total = sum(len(v) for v in inputs.members.values())
        print(json.dumps({"spec": SPEC, "universe_digest": universe_digest(inputs.members),
                          "input_digest": fb.input_digest(inputs.series),
                          "distinct": len({s for v in inputs.members.values() for s in v}),
                          "member_months": total,
                          "excluded_member_months": sum(len(v) for v in inputs.excluded.values()),
                          "excluded": {s: v for s, v in sorted(inputs.excluded.items()) if v}}, indent=1))
        return EXIT_OK
    try:
        report = {"measured_at": timeutil.utc_now_iso(), **fb.provenance(), **measure(inputs)}
    except (Blocked, fb.DataGap) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return EXIT_BLOCKED
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return EXIT_OK
    for name, row in report["strategies"].items():
        print(f"{name}: annual SR {row['entry']['annual_sharpe']} (floor {ENTRY_SHARPE}) -> "
              f"{'PASS' if row['entry']['pass'] else 'FAIL'} | mean month {row['mean_month_pct']}% "
              f"| recent {row['recent']} | worst {row['worst_month']} | maxDD {row['max_drawdown_pct']}% "
              f"| liquidations {row['events']['liquidations']}")
    print(f"overall: {report['overall']} (universe {report['universe']['digest']}, input {report['input_digest']})")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
