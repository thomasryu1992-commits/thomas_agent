"""Premium convergence: does a spot-long / perp-short opened when the perp trades above spot by more than the
round trip, and closed when the premium is gone, clear an annual Sharpe of 1.0?

``docs/proposals/PREMIUM_CONVERGENCE_V0.1.md`` (pre-registered; Thomas 2026-11-10). It follows the
long-spot-only arbitrage of He, Manela, Ross and von Wachter, *Fundamentals of Perpetual Futures* (§4.3), on
this repository's cost model. It reads only the research cache and writes nothing.

- **Bars:** the 1h index, premium-index and mark klines of six USD-M perps (:data:`SYMBOLS`). The spot leg is
  priced at the index, the perp at ``index × (1 + premium)``, as in ``FUNDING_BASIS_V0.1``.
- **Hour:** hour ``h`` runs from its open (the close of ``h − 1``) to the next open. The decision at ``h``'s open
  reads the premium close of ``h − 1`` and trades at that instant.
- **Signal (one strategy, ``PC``):** enter when that premium is above :func:`threshold`, the round trip of both
  legs in the cost model; exit when it is at or below 0.
- **Position:** long spot and short perp, notional 1 each at entry, the perp on isolated 1x margin, capital
  :data:`CAPITAL`. Price PnL is ``V = S/S0 − P/P0`` marked at every hourly close.
- **Funding:** the short receives ``rate × mark / P0`` at a settlement only when the position is open on both
  sides of its instant (none at the entry or exit instant). Settlements are rounded to the hour.
- **Reset and liquidation:** as ``FUNDING_BASIS_V0.1``, on hourly bars: a close with ``S/S0 ≥ 1.5`` closes and
  reopens both legs; an hour whose mark high reaches 1.95 × the entry mark loses the perp's margin.
- **Seasoning:** a perp first archived after 2020-01-01 is not traded for its first 90 days.
- **A missing premium hour:** no decision at the next open (the position is kept as it is) and the mark is
  carried to the next bar that has one. A held position that meets more than :data:`MAX_BRIDGE_HOURS`
  consecutive such hours, or any hour without an index or mark bar, is BLOCKED. Nothing is imputed.
- **Day and portfolio:** a symbol's day is the sum of its hourly PnL over the UTC day, on capital; idle days are
  0. The portfolio is the mean over the symbols trading that day.
- **Entry rule:** the annual Sharpe of complete months (monthly Sharpe × √12) is at least
  :data:`ENTRY_SHARPE`. Everything else in the report is shown, never judged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from runtime.mvp_runtime.crypto import cost  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import funding_basis as fb  # noqa: E402
from scripts.research import lake  # noqa: E402

SPEC = "PREMIUM_CONVERGENCE_V0.1"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT")
TIMEFRAME = "1h"
START = date(2020, 1, 1)
END = date(2026, 9, 30)                  # the last complete month before the 2026-10-28 archive hole
ARCHIVE_FIRST_DAY = START
SEASONING_DAYS = 90                      # FUNDING_BASIS_V0.1
SPOT_FEE_BPS = 10.0                      # FUNDING_BASIS_V0.1
RESET_RATIO = 1.5
LIQUIDATION_RATIO = 1.95
CAPITAL = 2.0
MAX_BRIDGE_HOURS = 3
STRATEGIES = ("PC",)
ENTRY_SHARPE = 1.0
RECENT_MONTHS = 24
PAPER_LAST_MONTH = "2024-03"             # the paper's sample ends 2024-03-11; later months are shown apart
SCREEN_WINDOW = (date(2027, 1, 1), date(2027, 4, 30))
HOUR_MS = 3_600_000
DAY_MS = 86_400_000


class DataGap(Exception):
    """A held position met an hour without its index or mark bar, or too long a run without a premium bar."""


@dataclass(frozen=True, slots=True)
class Hour:
    index: float                 # index close: the spot leg's price
    premium: float | None        # premium index close, (perp − index) / index; None when the bar is missing
    mark: float
    mark_high: float


@dataclass
class Series:
    """One symbol's inputs: hourly bars by open time (ms), settlements by hour-rounded instant, first archived day."""
    hours: dict[int, Hour]
    settlements: dict[int, float]
    first_archived: date
    name: str = ""

    def seasoned_from(self) -> date:
        listed = self.first_archived
        return listed if listed <= ARCHIVE_FIRST_DAY else listed + timedelta(days=SEASONING_DAYS)

    def first_day(self, start: date = START) -> date | None:
        """The first UTC day traded: seasoned, on or after ``start``, and with the bar before its first hour."""
        if not self.hours:
            return None
        first_bar = datetime.fromtimestamp(min(self.hours) / 1000, tz=timezone.utc).date()
        return max(start, self.seasoned_from(), first_bar + timedelta(days=1))


def _ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def _round_hour(ms: float) -> int:
    return int(round(ms / HOUR_MS)) * HOUR_MS


# --- reading the cache ----------------------------------------------------------------------------------

def _bars(cache: Path, kind: str, symbol: str) -> dict[int, list[Any]]:
    rows: dict[int, list[Any]] = {}
    for row in lake.read_rows(lake.derivative_path(cache, kind, symbol, TIMEFRAME)):
        try:
            rows[int(row[0])] = row
        except (TypeError, ValueError, IndexError):
            continue
    return rows


def read_series(cache: Path, symbol: str) -> Series | None:
    index, premium, mark = (_bars(cache, k, symbol) for k in ("index", "premium", "mark"))
    hours: dict[int, Hour] = {}
    for t in set(index) & set(mark):
        try:
            p = premium.get(t)
            hours[t] = Hour(index=float(index[t][4]), premium=None if p is None else float(p[4]),
                            mark=float(mark[t][4]), mark_high=float(mark[t][2]))
        except (TypeError, ValueError, IndexError):
            continue
    archive = cache / "archive"
    settlements = {_round_hour(t): r for t, r in xsb.read_funding(archive / xsb.FUNDING_KIND / symbol)}
    klines = xsb.read_klines(archive / xsb.KLINES_KIND / symbol)
    if not hours or not settlements or not klines:
        return None
    return Series(hours=hours, settlements=settlements, first_archived=min(klines), name=symbol)


# --- one symbol --------------------------------------------------------------------------------------------

def _costs(spot_notional: float, perp_notional: float) -> float:
    spot = (SPOT_FEE_BPS + cost.DEFAULT_SLIPPAGE_BPS) / 10_000
    perp = (cost.DEFAULT_TAKER_FEE_BPS + cost.DEFAULT_SLIPPAGE_BPS) / 10_000
    return spot * spot_notional + perp * perp_notional


def threshold() -> float:
    """The premium above which a position opens: entry plus exit of both legs, on notional 1 each."""
    return 2 * _costs(1.0, 1.0)


@dataclass
class Leg:
    returns: dict[date, float] = field(default_factory=dict)
    funding: dict[date, float] = field(default_factory=dict)
    entries: int = 0
    exits: int = 0
    resets: int = 0
    liquidations: int = 0
    held_hours: int = 0
    hours: int = 0
    bridged_hours: int = 0


def simulate_symbol(series: Series, start: date = START, end: date = END) -> Leg:
    leg = Leg()
    first = series.first_day(start)
    if first is None or first > end:
        return leg
    c = threshold()
    open_ = False
    s0 = p0 = m0 = 0.0
    value = 0.0                     # V = S/S0 − P/P0 at the last marked close
    bridge = 0                      # consecutive hours whose premium close is missing
    day = first
    while day <= end:
        day_pnl = day_funding = 0.0
        for k in range(24):
            t = _ms(day) + k * HOUR_MS
            prev, bar = series.hours.get(t - HOUR_MS), series.hours.get(t)
            leg.hours += 1
            if prev is None or bar is None:
                if open_:
                    raise DataGap(f"{series.name} {datetime.fromtimestamp(t / 1000, tz=timezone.utc):%Y-%m-%d %H}h:"
                                  " no index or mark bar while a position is open")
                continue
            pnl = funding = 0.0
            was_open = open_
            if prev.premium is None:                        # no decision: keep the position as it is
                bridge += 1
                if open_ and bridge > MAX_BRIDGE_HOURS:
                    raise DataGap(f"{series.name}: more than {MAX_BRIDGE_HOURS} hours without a premium bar")
            else:
                bridge = 0
                if open_ and prev.premium <= 0:             # exit at this open (= the last close)
                    pnl -= _costs(prev.index / s0, prev.index * (1 + prev.premium) / p0)
                    open_, leg.exits = False, leg.exits + 1
                elif not open_ and prev.premium > c:        # enter at this open
                    s0, p0, m0 = prev.index, prev.index * (1 + prev.premium), prev.mark
                    value = 0.0
                    pnl -= _costs(1.0, 1.0)
                    open_, leg.entries = True, leg.entries + 1
            if open_:
                if was_open and t in series.settlements:     # open on both sides of the instant
                    funding = series.settlements[t] * prev.mark / p0
                if bar.mark_high >= LIQUIDATION_RATIO * m0:
                    pnl += funding + (bar.index / s0 - 1 - 1.0) - value   # the margin is gone; spot sold at close
                    pnl -= _costs(bar.index / s0, 0.0)
                    open_, leg.liquidations = False, leg.liquidations + 1
                else:
                    pnl += funding
                    if bar.premium is not None:
                        new_value = bar.index / s0 - bar.index * (1 + bar.premium) / p0
                        pnl += new_value - value
                        value = new_value
                        if bar.index / s0 >= RESET_RATIO:      # top the margin up: close and reopen at the close
                            pnl -= _costs(bar.index / s0, bar.index * (1 + bar.premium) / p0) + _costs(1.0, 1.0)
                            s0, p0, m0 = bar.index, bar.index * (1 + bar.premium), bar.mark
                            value = 0.0
                            leg.resets += 1
                    else:
                        leg.bridged_hours += 1
                    leg.held_hours += 1
            day_pnl += pnl
            day_funding += funding
        leg.returns[day] = day_pnl / CAPITAL
        leg.funding[day] = day_funding / CAPITAL
        day += timedelta(days=1)
    return leg


# --- the portfolio and the entry rule -----------------------------------------------------------------------

def portfolio(legs: Mapping[str, Leg]) -> dict[date, float]:
    days = sorted({d for leg in legs.values() for d in leg.returns})
    return {d: statistics.fmean(leg.returns[d] for leg in legs.values() if d in leg.returns) for d in days}


def _sharpe(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    sd = statistics.stdev(values)
    return None if sd == 0 else statistics.fmean(values) / sd


def _annual(monthly: Sequence[float]) -> float | None:
    s = _sharpe(monthly)
    return None if s is None else round(s * math.sqrt(12), 3)


def measure_strategy(strategy: str, series: Mapping[str, Series], *, start: date = START,
                     end: date = END) -> dict[str, Any]:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy}")
    legs = {name: simulate_symbol(s, start, end) for name, s in series.items()}
    daily = portfolio(legs)
    months = fb.by_month(daily)
    complete = [m for m in sorted(months) if len(months[m]) == fb._days_in_month(m)]
    monthly = [sum(months[m]) for m in complete]
    recent = monthly[-RECENT_MONTHS:]
    after = [v for m, v in zip(complete, monthly) if m > PAPER_LAST_MONTH]
    annual = _annual(monthly)
    years = sorted({m[:4] for m in complete})
    funding_total = sum(sum(leg.funding.values()) for leg in legs.values()) / max(len(legs), 1)
    return {
        "strategy": strategy,
        "months": len(complete),
        "first_month": complete[0] if complete else None,
        "last_month": complete[-1] if complete else None,
        "annual_sharpe": annual,
        "entry_pass": annual is not None and annual >= ENTRY_SHARPE,
        "recent_annual_sharpe": _annual(recent),
        "after_paper": {"months": len(after), "annual_sharpe": _annual(after),
                        "annual_return_pct": round(statistics.fmean(after) * 12 * 100, 4) if after else None},
        "annual_return_pct": round(statistics.fmean(monthly) * 12 * 100, 4) if monthly else None,
        "recent_annual_return_pct": round(statistics.fmean(recent) * 12 * 100, 4) if recent else None,
        "worst_month": None if not monthly else
        {"month": complete[monthly.index(min(monthly))], "pct": round(min(monthly) * 100, 4)},
        "negative_months": sum(1 for v in monthly if v < 0),
        "max_drawdown_pct": round(fb.max_drawdown(daily) * 100, 4),
        "funding_mean_symbol_pct": round(funding_total * 100, 4),
        "years": {y: round(sum(sum(months[m]) for m in complete if m.startswith(y)) * 100, 4) for y in years},
        "events": {name: {"entries": leg.entries, "exits": leg.exits, "resets": leg.resets,
                          "liquidations": leg.liquidations, "held_hours": leg.held_hours,
                          "active_share": round(leg.held_hours / leg.hours, 4) if leg.hours else None,
                          "mean_hold_hours": round(leg.held_hours / leg.entries, 1) if leg.entries else None,
                          "bridged_hours": leg.bridged_hours}
                   for name, leg in legs.items()},
        "monthly_pct": {m: round(v * 100, 4) for m, v in zip(complete, monthly)},
    }


def input_digest(series: Mapping[str, Series]) -> str:
    h = hashlib.sha256()
    for name in sorted(series):
        s = series[name]
        h.update(f"{name}|listed|{s.first_archived}".encode())
        for t in sorted(s.hours):
            b = s.hours[t]
            h.update(f"{name}|{t}|{b.index!r}|{b.premium!r}|{b.mark!r}|{b.mark_high!r}".encode())
        for t in sorted(s.settlements):
            h.update(f"{name}|f|{t}|{s.settlements[t]!r}".encode())
    return h.hexdigest()[:16]


def coverage(series: Mapping[str, Series], start: date = START, end: date = END) -> dict[str, Any]:
    """What the cache holds, with no price or premium value in it: hours, missing hours and dates."""
    out: dict[str, Any] = {}
    for name, s in sorted(series.items()):
        first = s.first_day(start)
        lo, hi = (_ms(first) - HOUR_MS, _ms(end + timedelta(days=1))) if first else (0, 0)
        span = range(lo, hi, HOUR_MS)
        no_bar = [t for t in span if t not in s.hours]
        no_premium = [t for t in span if t in s.hours and s.hours[t].premium is None]
        iso = (lambda t: datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%HZ"))
        out[name] = {
            "first_archived": s.first_archived.isoformat(),
            "seasoned_from": s.seasoned_from().isoformat(),
            "first_day": None if first is None else first.isoformat(),
            "first_hour": iso(min(s.hours)), "last_hour": iso(max(s.hours)),
            "hours_in_span": len(span),
            "missing_index_or_mark_hours": [iso(t) for t in no_bar[:20]], "missing_index_or_mark_count": len(no_bar),
            "missing_premium_hours": [iso(t) for t in no_premium[:20]], "missing_premium_count": len(no_premium),
            "settlements_in_span": sum(1 for t in s.settlements if lo <= t < hi),
        }
    return out


def measure(series: Mapping[str, Series], *, start: date = START, end: date = END) -> dict[str, Any]:
    strategies = {s: measure_strategy(s, series, start=start, end=end) for s in STRATEGIES}
    return {"spec": SPEC, "span": [start.isoformat(), end.isoformat()], "symbols": sorted(series),
            "threshold_pct": round(threshold() * 100, 4), "input_digest": input_digest(series),
            "strategies": strategies, "entry_sharpe": ENTRY_SHARPE,
            "screen_window": [SCREEN_WINDOW[0].isoformat(), SCREEN_WINDOW[1].isoformat()],
            "overall": "ENTRY_PASS" if any(r["entry_pass"] for r in strategies.values()) else "ENTRY_FAIL"}


# --- the command line -------------------------------------------------------------------------------------

def load(cache: Path) -> tuple[dict[str, Series], list[str]]:
    series, missing = {}, []
    for name in SYMBOLS:
        s = read_series(cache, name)
        if s is None:
            missing.append(name)
        else:
            series[name] = s
    return series, missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="premium_convergence", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("load", "run"),
                        help="load: coverage and the input digest only, no returns; run: the measurement")
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    series, missing = load(cache)
    if missing:
        print(f"BLOCKED: no 1h index/premium/mark bars, funding or archive klines for {','.join(missing)} "
              f"under {cache}", file=sys.stderr)
        return EXIT_BLOCKED
    if args.command == "load":
        print(json.dumps({"spec": SPEC, "input_digest": input_digest(series), "coverage": coverage(series)},
                         indent=1, sort_keys=True))
        return EXIT_OK
    try:
        report = {"measured_at": timeutil.utc_now_iso(), **fb.provenance(), **measure(series)}
    except DataGap as gap:
        print(f"BLOCKED: {gap}", file=sys.stderr)
        return EXIT_BLOCKED
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return EXIT_OK
    for name, row in report["strategies"].items():
        print(f"{name}: annual SR {row['annual_sharpe']} over {row['months']} months "
              f"({row['first_month']}..{row['last_month']}) -> {'PASS' if row['entry_pass'] else 'FAIL'} "
              f"| after paper {row['after_paper']} | recent {RECENT_MONTHS} SR {row['recent_annual_sharpe']} "
              f"| annual {row['annual_return_pct']}% | worst {row['worst_month']} | maxDD {row['max_drawdown_pct']}%")
    print(f"overall: {report['overall']} (threshold {report['threshold_pct']}%, input {report['input_digest']})")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
