"""Funding basis: would a one-month forward test confirm a delta-neutral spot-long / perp-short carry?

``docs/proposals/FUNDING_BASIS_V0.1.md`` (pre-registered; Thomas 2026-11-08: a hypothesis is built only if its
forward verdict takes under a month). It reads only the research cache and writes nothing.

- **Position:** per symbol, long spot and short the USD-M perp, equal notional ``N = 1`` at entry, the perp on
  isolated 1x margin, so a position ties up capital ``2N``. The spot leg is priced at Binance's index (a
  multi-exchange composite); the perp at ``index × (1 + premium)``. Tracking error between Binance spot and the
  index is not modelled.
- **Day:** day ``d`` runs from its open (the close of ``d − 1``) to the next open. A decision for ``d`` uses closes
  and settlements strictly before its open.
- **Funding:** the short receives ``rate × mark / P0`` per settlement. Settlement times are rounded to the hour; a
  settlement counts only when the position is open on both sides of its instant (none at the entry or exit
  instant). A settlement at a day's open is booked on that day.
- **Price PnL:** ``V_t = S_t / S0 − P_t / P0``, booked as its daily change.
- **Reset:** at a close where ``S / S0 ≥`` :data:`RESET_RATIO`, both legs are closed and reopened (exit plus entry
  costs) so the perp's margin is topped up.
- **Liquidation:** on a day whose mark high reaches :data:`LIQUIDATION_RATIO` × the entry mark, the perp's whole
  margin is lost (``−N``), the spot leg is sold at that day's close with its exit cost, and the day earns no
  funding. No other fee is charged.
- **Costs:** spot :data:`SPOT_FEE_BPS` plus slippage, perp the runtime's taker fee plus slippage
  (``crypto/cost.py``), on each leg's notional at entry and exit.
- **Portfolio:** each day the mean, over the symbols eligible that day, of the return on capital (idle = 0).
- **Strategies (fixed):** ``B-ALL`` holds every eligible symbol; ``B-POS`` enters when the mean settled rate of
  the last 7 days is at least :data:`POS_ENTER_RATE` and exits when the mean of the last 3 days is below 0.
- **Forward protocol (§3):** one calendar month of daily portfolio returns, one-sided, circular block bootstrap
  (block :data:`BLOCK_DAYS` days, :data:`BOOTSTRAP` resamples, seeded); CONFIRMED when the mean is positive and
  ``p ≤`` :data:`ALPHA`.
- **Gate:** the share of complete months the protocol confirms is at least :data:`GATE_SHARE`, over the whole span
  and over the last :data:`RECENT_MONTHS` months. The monthly Sharpe and the tail months are shown, never judged.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import random
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
from scripts.research import lake  # noqa: E402

SPEC = "FUNDING_BASIS_V0.1"
# The eleven perps whose 1d index, premium and mark series the research cache holds (fixed before any run).
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
           "LTCUSDT", "DOTUSDT")
START = date(2020, 1, 1)
END = date(2026, 9, 30)                  # the last complete month every series covers
ARCHIVE_FIRST_DAY = START                # a perp first archived on this day was listed before it
SEASONING_DAYS = 90                      # cross_section_basket.MIN_AGE_DAYS
SPOT_FEE_BPS = 10.0                      # CROSS_SECTION_SPOT_HOLDOUT_V0.1's spot taker fee
RESET_RATIO = 1.5
LIQUIDATION_RATIO = 1.95                 # isolated 1x short: liquidated near 2x entry, less maintenance margin
CAPITAL = 2.0                            # spot notional + perp margin, per unit notional
POS_ENTER_RATE = 0.00005                 # mean settled rate over the last 7 days, per settlement
POS_ENTER_DAYS = 7
POS_EXIT_DAYS = 3
STRATEGIES = ("B-ALL", "B-POS")
BLOCK_DAYS = 5
BOOTSTRAP = 2000
ALPHA = 0.05
GATE_SHARE = 0.80
RECENT_MONTHS = 24
GATE_MONTHLY_SHARPE = 2.49               # (1.645 + 0.842): one month at 80% power, one-sided 0.05 (display)
STRESS_MONTHS = ("2020-03", "2021-05", "2022-05", "2022-11", "2025-10")
HOUR_MS = 3_600_000
DAY_MS = 86_400_000


class DataGap(Exception):
    """A held position met a day without its index, premium or mark bar: fail closed, never impute."""


@dataclass(frozen=True, slots=True)
class Day:
    index: float          # index close: the spot leg's price
    premium: float        # premium index close: (perp − index) / index
    mark: float           # mark close
    mark_high: float


@dataclass
class Series:
    """One symbol's inputs: daily bars by UTC day, settlements ``(hour-rounded ms, rate)``, the first archived day."""
    days: dict[date, Day]
    settlements: list[tuple[int, float]]
    first_archived: date
    name: str = ""

    def __post_init__(self) -> None:
        self._times = [t for t, _ in self.settlements]
        self._prefix = [0.0]
        for _, rate in self.settlements:
            self._prefix.append(self._prefix[-1] + rate)

    def mean_rate(self, since_ms: int, before_ms: int) -> float | None:
        """Mean settled rate over ``[since_ms, before_ms)``; None when nothing settled there."""
        lo = bisect.bisect_left(self._times, since_ms)
        hi = bisect.bisect_left(self._times, before_ms)
        return (self._prefix[hi] - self._prefix[lo]) / (hi - lo) if hi > lo else None

    def seasoned_from(self) -> date:
        listed = self.first_archived
        return listed if listed <= ARCHIVE_FIRST_DAY else listed + timedelta(days=SEASONING_DAYS)

    def eligible(self, day: date) -> bool:
        return day >= self.seasoned_from() and day in self.days and day - timedelta(days=1) in self.days


def _ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def _round_hour(ms: int) -> int:
    return int(round(ms / HOUR_MS)) * HOUR_MS


# --- reading the cache ----------------------------------------------------------------------------------

def read_series(cache: Path, symbol: str) -> Series | None:
    bars: dict[str, dict[date, list[Any]]] = {}
    for kind in ("index", "premium", "mark"):
        rows = {}
        for row in lake.read_rows(lake.derivative_path(cache, kind, symbol, "1d")):
            try:
                rows[xsb._utc_day(float(row[0]))] = row
            except (TypeError, ValueError, IndexError):
                continue
        bars[kind] = rows
    days = {}
    for day in set(bars["index"]) & set(bars["premium"]) & set(bars["mark"]):
        try:
            days[day] = Day(index=float(bars["index"][day][4]), premium=float(bars["premium"][day][4]),
                            mark=float(bars["mark"][day][4]), mark_high=float(bars["mark"][day][2]))
        except (TypeError, ValueError, IndexError):
            continue
    archive = cache / "archive"
    # one settlement per hour: the archive's last row for that hour wins (read_funding sorts by time, then rate)
    settlements = sorted({_round_hour(t): r for t, r in xsb.read_funding(archive / xsb.FUNDING_KIND / symbol)}.items())
    klines = xsb.read_klines(archive / xsb.KLINES_KIND / symbol)
    if not days or not settlements or not klines:
        return None
    return Series(days=days, settlements=settlements, first_archived=min(klines))


# --- one symbol --------------------------------------------------------------------------------------------

def _costs(spot_notional: float, perp_notional: float) -> float:
    spot = (SPOT_FEE_BPS + cost.DEFAULT_SLIPPAGE_BPS) / 10_000
    perp = (cost.DEFAULT_TAKER_FEE_BPS + cost.DEFAULT_SLIPPAGE_BPS) / 10_000
    return spot * spot_notional + perp * perp_notional


def wants(strategy: str, series: Series, day: date, holding: bool) -> bool:
    """The decision at ``day``'s open, from settlements strictly before it."""
    if strategy == "B-ALL":
        return True
    if strategy == "B-POS":
        at = _ms(day)
        if holding:
            recent = series.mean_rate(at - POS_EXIT_DAYS * DAY_MS, at)
            return recent is None or recent >= 0
        trailing = series.mean_rate(at - POS_ENTER_DAYS * DAY_MS, at)
        return trailing is not None and trailing >= POS_ENTER_RATE
    raise ValueError(f"unknown strategy {strategy}")


@dataclass
class Leg:
    """One symbol's daily returns on capital and the events that produced them."""
    returns: dict[date, float] = field(default_factory=dict)
    funding: dict[date, float] = field(default_factory=dict)
    entries: int = 0
    exits: int = 0
    resets: int = 0
    liquidations: int = 0


def simulate_symbol(strategy: str, series: Series, start: date = START, end: date = END) -> Leg:
    leg = Leg()
    by_time = dict(series.settlements)
    times = sorted(by_time)
    open_ = held_yesterday = False
    s0 = p0 = m0 = 0.0
    value = 0.0                     # V_t = S_t/S0 − P_t/P0 at the last close
    day = start
    while day <= end:
        if not series.eligible(day):
            if open_:
                raise DataGap(f"{series.name} {day}: no bar while a position is open")
            held_yesterday = False
            day += timedelta(days=1)
            continue
        prev, today = series.days[day - timedelta(days=1)], series.days[day]
        pnl = funding = 0.0
        hold = wants(strategy, series, day, open_)
        if open_ and not hold:                               # exit at this open (= the last close)
            pnl -= _costs(prev.index / s0, prev.index * (1 + prev.premium) / p0)
            open_, leg.exits = False, leg.exits + 1
        if not open_ and hold:                               # enter at this open
            s0, p0, m0 = prev.index, prev.index * (1 + prev.premium), prev.mark
            value, held_yesterday = 0.0, False
            pnl -= _costs(1.0, 1.0)
            open_, leg.entries = True, leg.entries + 1
        if open_:
            day_open = _ms(day)
            if today.mark_high >= LIQUIDATION_RATIO * m0:
                spot = today.index / s0 - 1
                pnl += (spot - 1.0) - value                  # the perp's margin is gone; spot sold at the close
                pnl -= _costs(today.index / s0, 0.0)
                open_, held_yesterday = False, False
                leg.liquidations += 1
            else:
                if held_yesterday and day_open in by_time:   # open on both sides of the instant
                    funding += by_time[day_open] * prev.mark / p0
                lo = bisect.bisect_right(times, day_open)
                hi = bisect.bisect_left(times, day_open + DAY_MS)
                funding += sum(by_time[t] for t in times[lo:hi]) * today.mark / p0
                new_value = today.index / s0 - today.index * (1 + today.premium) / p0
                pnl += funding + (new_value - value)
                value = new_value
                if today.index / s0 >= RESET_RATIO:          # top the margin up: close and reopen at the close
                    pnl -= _costs(today.index / s0, today.index * (1 + today.premium) / p0) + _costs(1.0, 1.0)
                    s0, p0, m0 = today.index, today.index * (1 + today.premium), today.mark
                    value = 0.0
                    leg.resets += 1
                held_yesterday = True
        else:
            held_yesterday = False
        leg.returns[day] = pnl / CAPITAL
        leg.funding[day] = funding / CAPITAL
        day += timedelta(days=1)
    return leg


# --- the portfolio and the forward protocol ---------------------------------------------------------------

def portfolio(legs: Mapping[str, Leg]) -> tuple[dict[date, float], dict[date, float]]:
    """``({day: mean return}, {day: mean funding})`` over the symbols eligible that day (idle = 0)."""
    days = sorted({d for leg in legs.values() for d in leg.returns})
    ret, fund = {}, {}
    for day in days:
        members = [leg for leg in legs.values() if day in leg.returns]
        ret[day] = sum(leg.returns[day] for leg in members) / len(members)
        fund[day] = sum(leg.funding[day] for leg in members) / len(members)
    return ret, fund


def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def by_month(daily: Mapping[date, float]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for day in sorted(daily):
        out.setdefault(month_key(day), []).append(daily[day])
    return out


def block_bootstrap_p(values: Sequence[float], seed: str, *, block: int = BLOCK_DAYS,
                      resamples: int = BOOTSTRAP) -> float:
    """One-sided p for mean > 0: circular block bootstrap of the centred series."""
    n = len(values)
    mean = sum(values) / n
    centred = [v - mean for v in values]
    rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16))
    blocks = math.ceil(n / block)
    hits = 0
    for _ in range(resamples):
        total = 0.0
        taken = 0
        for _b in range(blocks):
            start = rng.randrange(n)
            for k in range(block):
                if taken == n:
                    break
                total += centred[(start + k) % n]
                taken += 1
        if total / n >= mean - 1e-15:
            hits += 1
    return (1 + hits) / (1 + resamples)


def forward_verdict(values: Sequence[float], seed: str) -> dict[str, Any]:
    mean = sum(values) / len(values)
    p = block_bootstrap_p(values, seed)
    return {"days": len(values), "mean_bps_per_day": round(mean * 10_000, 4), "p": round(p, 5),
            "confirmed": mean > 0 and p <= ALPHA}


def _sharpe(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    sd = statistics.stdev(values)
    return None if sd == 0 else statistics.fmean(values) / sd


def max_drawdown(daily: Mapping[date, float]) -> float:
    peak = level = worst = 0.0
    for day in sorted(daily):
        level += daily[day]
        peak = max(peak, level)
        worst = min(worst, level - peak)
    return worst


def gate(verdicts: Mapping[str, Mapping[str, Any]], months: Sequence[str]) -> dict[str, Any]:
    confirmed = sum(1 for m in months if verdicts[m]["confirmed"])
    share = confirmed / len(months) if months else 0.0
    return {"months": len(months), "confirmed": confirmed, "share": round(share, 4),
            "first": months[0] if months else None, "last": months[-1] if months else None,
            "pass": bool(months) and share >= GATE_SHARE}


def measure_strategy(strategy: str, series: Mapping[str, Series], *, start: date = START,
                     end: date = END) -> dict[str, Any]:
    legs = {name: simulate_symbol(strategy, s, start, end) for name, s in series.items()}
    daily, funding = portfolio(legs)
    months = by_month(daily)
    complete = [m for m in sorted(months) if len(months[m]) == _days_in_month(m)]
    verdicts = {m: forward_verdict(months[m], f"{SPEC}|{strategy}|{m}") for m in complete}
    monthly = [sum(months[m]) for m in complete]
    monthly_ex_funding = [sum(months[m]) - sum(v for d, v in funding.items() if month_key(d) == m)
                          for m in complete]
    full = gate(verdicts, complete)
    recent = gate(verdicts, complete[-RECENT_MONTHS:])
    sharpe = _sharpe(monthly)
    years = sorted({m[:4] for m in complete})
    return {
        "strategy": strategy,
        "gate": {"full": full, "recent": recent, "pass": full["pass"] and recent["pass"]},
        "monthly_sharpe": None if sharpe is None else round(sharpe, 3),
        "annual_sharpe": None if sharpe is None else round(sharpe * math.sqrt(12), 3),
        "verdict_years_at_this_sharpe": None if not sharpe or sharpe <= 0
        else round((GATE_MONTHLY_SHARPE / (sharpe * math.sqrt(12))) ** 2, 3),
        "mean_month_pct": round(statistics.fmean(monthly) * 100, 4) if monthly else None,
        "mean_month_ex_funding_pct": round(statistics.fmean(monthly_ex_funding) * 100, 4) if monthly else None,
        "worst_month": None if not monthly else
        {"month": complete[monthly.index(min(monthly))], "pct": round(min(monthly) * 100, 4)},
        "negative_months": sum(1 for v in monthly if v < 0),
        "max_drawdown_pct": round(max_drawdown(daily) * 100, 4),
        "stress_months_pct": {m: (round(sum(months[m]) * 100, 4) if m in months else None) for m in STRESS_MONTHS},
        "years": {y: {"months": sum(1 for m in complete if m.startswith(y)),
                      "confirmed": sum(1 for m in complete if m.startswith(y) and verdicts[m]["confirmed"]),
                      "sum_pct": round(sum(sum(months[m]) for m in complete if m.startswith(y)) * 100, 4)}
                  for y in years},
        "events": {name: {"entries": leg.entries, "exits": leg.exits, "resets": leg.resets,
                          "liquidations": leg.liquidations} for name, leg in legs.items()},
        "months": {m: {**verdicts[m], "sum_pct": round(sum(months[m]) * 100, 4)} for m in complete},
    }


def _days_in_month(key: str) -> int:
    y, m = int(key[:4]), int(key[5:])
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return (nxt - date(y, m, 1)).days


def input_digest(series: Mapping[str, Series]) -> str:
    h = hashlib.sha256()
    for name in sorted(series):
        s = series[name]
        for day in sorted(s.days):
            d = s.days[day]
            h.update(f"{name}|{day}|{d.index!r}|{d.premium!r}|{d.mark!r}|{d.mark_high!r}".encode())
        for t, r in s.settlements:
            h.update(f"{name}|{t}|{r!r}".encode())
        h.update(f"{name}|first|{s.first_archived}".encode())
    return h.hexdigest()[:16]


def measure(series: Mapping[str, Series], *, start: date = START, end: date = END) -> dict[str, Any]:
    strategies = {name: measure_strategy(name, series, start=start, end=end) for name in STRATEGIES}
    return {"spec": SPEC, "span": [start.isoformat(), end.isoformat()], "symbols": sorted(series),
            "seasoned_from": {n: s.seasoned_from().isoformat() for n, s in sorted(series.items())},
            "input_digest": input_digest(series), "strategies": strategies,
            "overall": "GATE_PASS" if any(r["gate"]["pass"] for r in strategies.values()) else "GATE_FAIL"}


# --- the command line -------------------------------------------------------------------------------------

def provenance() -> dict[str, Any]:
    from runtime.mvp_runtime.crypto import judgement_fingerprint

    snapshot = os.environ.get("RESEARCH_LAKE")
    return {"judgement_fingerprint": judgement_fingerprint.judgement_fingerprint()["short"],
            "lake_snapshot": Path(snapshot).name if snapshot else None,
            "costs_bps": {"spot_fee": SPOT_FEE_BPS, "perp_fee": cost.DEFAULT_TAKER_FEE_BPS,
                          "slippage": cost.DEFAULT_SLIPPAGE_BPS}}


def load(cache: Path) -> tuple[dict[str, Series], list[str]]:
    series, missing = {}, []
    for symbol in SYMBOLS:
        s = read_series(cache, symbol)
        if s is None:
            missing.append(symbol)
        else:
            s.name = symbol
            series[symbol] = s
    return series, missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="funding_basis", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("run",))
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    series, missing = load(cache)
    if missing:
        print(f"BLOCKED: no index/premium/mark/funding/klines for {','.join(missing)} under {cache}",
              file=sys.stderr)
        return EXIT_BLOCKED
    try:
        report = {"measured_at": timeutil.utc_now_iso(), **provenance(), **measure(series)}
    except DataGap as gap:
        print(f"BLOCKED: {gap}", file=sys.stderr)
        return EXIT_BLOCKED
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return EXIT_OK
    for name, row in report["strategies"].items():
        g = row["gate"]
        print(f"{name}: confirmed {g['full']['confirmed']}/{g['full']['months']} ({g['full']['share']}), "
              f"last {g['recent']['months']}: {g['recent']['confirmed']}/{g['recent']['months']} "
              f"({g['recent']['share']}) -> {'PASS' if g['pass'] else 'FAIL'} | monthly SR {row['monthly_sharpe']} "
              f"| mean month {row['mean_month_pct']}% (ex funding {row['mean_month_ex_funding_pct']}%) "
              f"| worst {row['worst_month']} | maxDD {row['max_drawdown_pct']}% | stress {row['stress_months_pct']}")
    print(f"overall: {report['overall']} (input {report['input_digest']})")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
