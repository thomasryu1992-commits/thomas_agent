"""Quarterly cash-and-carry: does a spot-long / dated-futures-short held to delivery clear the screen's entry floor?

``docs/proposals/QUARTERLY_CARRY_V0.1.md`` (pre-registered; Thomas 2026-11-09). Under
``STRATEGY_SCREEN_GATE_V0.1`` a hypothesis is screened only if its historical annual Sharpe is at least
:data:`ENTRY_SHARPE`. It reads only the research cache and writes nothing.

- **Contracts:** Binance USD-M quarterly futures on BTC and ETH (``BTCUSDT_YYMMDD``), the only dated USDT
  contracts the archive holds. Delivery is 08:00 UTC on the date in the name. Each base's contracts must be
  every quarter's last Friday from its first to its last, or the run is BLOCKED: a missing contract would
  silently make the next one the front.
- **Position:** long spot ``1/S0`` units (notional 1) and short the *same quantity* of the contract (notional
  ``F0/S0``). Equal quantity is what locks a dated basis; equal notional would leave ``basis × price move``
  open. The contract is on isolated margin ``1`` (the spot notional), so a position ties up :data:`CAPITAL`.
- **Prices:** spot is the Binance spot 1d close (``archive-spot``), the contract its own 1d close. Both close at
  00:00 UTC, so the two legs are priced at the same instant.
- **Day:** day ``d`` runs from its open (the close of ``d − 1``) to the next open; a decision for ``d`` uses only
  closes before its open.
- **Which contract:** the listed contract with the nearest delivery ``D`` and ``D − d ≥``
  :data:`MIN_DAYS_TO_DELIVERY`. If it has no close at ``d − 1``, nothing opens that day; a further contract is
  never taken instead.
- **Exit:** at the open of ``D`` (the close of ``D − 1``), both legs, at taker cost. The contract's bar on ``D``
  is never read: it stops at 08:00 UTC. The delivery fee is not modelled; a taker close is charged instead.
- **Price PnL:** ``V_t = (S_t − S0 − F_t + F0) / S0``, booked as its daily change. At delivery it tends to
  ``(F0 − S0) / S0``, whatever the price did.
- **Reset:** at a close where ``S / S0 ≥`` :data:`RESET_RATIO`, both legs are closed and reopened in the same
  contract (exit plus entry costs), as in ``FUNDING_BASIS_V0.1``.
- **Liquidation:** on a day whose contract *last-price* high reaches :data:`LIQUIDATION_RATIO` × ``F0`` the
  margin (``1``) is lost and the spot leg is sold at that day's close with its exit cost. The archive has no
  mark series for dated contracts; the last-price high reaches the level at least as often as the mark.
- **Costs:** spot :data:`SPOT_FEE_BPS` plus slippage, the contract the runtime's taker fee plus slippage
  (``crypto/cost.py``), on each leg's notional at entry and exit.
- **Strategies (fixed):** ``Q-ALL`` always holds the selected contract. ``Q-POS`` opens only when the basis it
  would lock beats the round trip: ``(F0 − S0) / S0 − 2 × entry cost > 0``; once open it holds to delivery.
  The threshold is the cost model, not a free parameter.
- **Missing data:** a held position meeting a day without its spot or contract bar is BLOCKED, never imputed.
- **Portfolio:** each day the mean, over the bases eligible that day, of the return on capital (idle = 0).
- **Entry criterion:** the annual Sharpe (monthly Sharpe × √12) over complete months is at least
  :data:`ENTRY_SHARPE`. ENTRY_PASS when any strategy passes. Everything else is shown, never judged.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import math
import os
import re
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
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

SPEC = "QUARTERLY_CARRY_V0.1"
BASES = ("BTCUSDT", "ETHUSDT")
CONTRACT = re.compile(r"\A(BTCUSDT|ETHUSDT)_(\d{6})\Z")
START = date(2020, 1, 1)                 # eligibility decides the real start: the first contract's first bar
END = date(2026, 9, 30)                  # the last complete month without a gap: the USD-M archive has no 2026-10-28
MIN_DAYS_TO_DELIVERY = 7                 # never open a position that cannot be held a week
SPOT_FEE_BPS = fb.SPOT_FEE_BPS
RESET_RATIO = fb.RESET_RATIO
LIQUIDATION_RATIO = fb.LIQUIDATION_RATIO
CAPITAL = 2.0                            # spot notional 1 + contract margin 1
MARGIN = 1.0
STRATEGIES = ("Q-ALL", "Q-POS")
ENTRY_SHARPE = 1.0
RECENT_MONTHS = 24
SCREEN_WINDOW = (date(2027, 1, 1), date(2027, 4, 30))
SPOT_ARCHIVE = "archive-spot"


class DataGap(Exception):
    """A held position met a day without its spot or contract bar: fail closed, never impute."""


class MissingContract(Exception):
    """A quarter between a base's first and last contract has no contract in the cache."""


@dataclass(frozen=True, slots=True)
class Contract:
    symbol: str
    delivery: date
    bars: dict[date, xsb.Bar]            # days before delivery only: the delivery day's bar stops at 08:00 UTC


@dataclass
class Base:
    name: str
    spot: dict[date, xsb.Bar]
    contracts: list[Contract]            # by delivery

    def front(self, day: date) -> Contract | None:
        """The listed contract with the nearest delivery at least :data:`MIN_DAYS_TO_DELIVERY` after ``day``."""
        prev = day - timedelta(days=1)
        for c in self.contracts:
            if (c.delivery - day).days >= MIN_DAYS_TO_DELIVERY and c.bars and min(c.bars) <= prev:
                return c
        return None

    def eligible(self, day: date) -> bool:
        prev = day - timedelta(days=1)
        return day in self.spot and prev in self.spot and self.front(day) is not None


def delivery_of(symbol: str) -> date:
    m = CONTRACT.fullmatch(symbol)
    if m is None:
        raise ValueError(f"not a quarterly contract: {symbol}")
    s = m.group(2)
    return date(2000 + int(s[:2]), int(s[2:4]), int(s[4:]))


def last_friday(year: int, month: int) -> date:
    last = date(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - calendar.FRIDAY) % 7)


def quarter_ends(first: date, last: date) -> list[date]:
    out = []
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        if m % 3 == 0:
            out.append(last_friday(y, m))
        y, m = (y + (m == 12), m % 12 + 1)
    return [d for d in out if first <= d <= last]


def check_quarters(base: str, deliveries: Sequence[date]) -> None:
    if not deliveries:
        raise MissingContract(f"{base}: no quarterly contract in the cache")
    expected = quarter_ends(min(deliveries), max(deliveries))
    missing = sorted(set(expected) - set(deliveries))
    if missing:
        raise MissingContract(f"{base}: no contract delivering {', '.join(d.isoformat() for d in missing)}")


# --- reading the cache ----------------------------------------------------------------------------------

def read_base(cache: Path, base: str) -> Base | None:
    spot = xsb.read_klines(cache / SPOT_ARCHIVE / xsb.KLINES_KIND / base)
    folder = cache / "archive" / xsb.KLINES_KIND
    contracts = []
    for path in sorted(folder.glob(f"{base}_*")) if folder.exists() else []:
        if CONTRACT.fullmatch(path.name) is None:
            continue
        delivery = delivery_of(path.name)
        bars = {d: b for d, b in xsb.read_klines(path).items() if d < delivery}
        contracts.append(Contract(symbol=path.name, delivery=delivery, bars=bars))
    if not spot or not contracts:
        return None
    contracts.sort(key=lambda c: c.delivery)
    check_quarters(base, [c.delivery for c in contracts])
    return Base(name=base, spot=spot, contracts=contracts)


# --- one base ---------------------------------------------------------------------------------------------

def _rates() -> tuple[float, float]:
    return ((SPOT_FEE_BPS + cost.DEFAULT_SLIPPAGE_BPS) / 10_000,
            (cost.DEFAULT_TAKER_FEE_BPS + cost.DEFAULT_SLIPPAGE_BPS) / 10_000)


def _costs(spot_notional: float, contract_notional: float) -> float:
    spot, contract = _rates()
    return spot * spot_notional + contract * contract_notional


def locked_edge(s0: float, f0: float) -> float:
    """The basis a position opened at ``(s0, f0)`` locks, less the round trip at entry prices."""
    return (f0 - s0) / s0 - 2 * _costs(1.0, f0 / s0)


def wants(strategy: str, s0: float, f0: float) -> bool:
    if strategy == "Q-ALL":
        return True
    if strategy == "Q-POS":
        return locked_edge(s0, f0) > 0
    raise ValueError(f"unknown strategy {strategy}")


@dataclass
class Leg:
    returns: dict[date, float] = field(default_factory=dict)
    entries: int = 0
    deliveries: int = 0
    resets: int = 0
    liquidations: int = 0
    locked_annual: list[float] = field(default_factory=list)   # (F0/S0 − 1) × 365 / days to delivery, at entry
    held_days: int = 0


def simulate_base(strategy: str, base: Base, start: date = START, end: date = END) -> Leg:
    leg = Leg()
    held: Contract | None = None
    s0 = f0 = 0.0
    value = 0.0
    day = start
    while day <= end:
        prev = day - timedelta(days=1)
        pnl = 0.0
        if held is not None and day >= held.delivery:            # exit at the open of delivery day
            pnl -= _costs(base.spot[prev].close / s0, held.bars[prev].close / s0)
            held, leg.deliveries = None, leg.deliveries + 1
        if held is None:
            if not base.eligible(day):
                if pnl:                                           # the delivery exit still happened
                    leg.returns[day] = pnl / CAPITAL
                day += timedelta(days=1)
                continue
            front = base.front(day)
            assert front is not None
            if prev in front.bars and wants(strategy, base.spot[prev].close, front.bars[prev].close):
                held, s0, f0, value = front, base.spot[prev].close, front.bars[prev].close, 0.0
                pnl -= _costs(1.0, f0 / s0)
                leg.entries += 1
                leg.locked_annual.append((f0 / s0 - 1) * 365 / (held.delivery - day).days)
        if held is not None:
            spot, bar = base.spot.get(day), held.bars.get(day)
            if spot is None or bar is None:
                raise DataGap(f"{base.name} {day}: no {'spot' if spot is None else held.symbol} bar while held")
            leg.held_days += 1
            if bar.high >= LIQUIDATION_RATIO * f0:
                pnl += (spot.close / s0 - 1) - MARGIN - value         # the margin is gone; spot sold at the close
                pnl -= _costs(spot.close / s0, 0.0)
                held, leg.liquidations = None, leg.liquidations + 1
            else:
                new_value = (spot.close - s0 - bar.close + f0) / s0
                pnl += new_value - value
                value = new_value
                if spot.close / s0 >= RESET_RATIO:                    # top the margin up in the same contract
                    pnl -= _costs(spot.close / s0, bar.close / s0) + _costs(1.0, bar.close / spot.close)
                    s0, f0, value = spot.close, bar.close, 0.0
                    leg.resets += 1
        leg.returns[day] = pnl / CAPITAL
        day += timedelta(days=1)
    return leg


# --- the portfolio and the entry criterion ----------------------------------------------------------------

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


def measure_strategy(strategy: str, bases: Mapping[str, Base], *, start: date = START,
                     end: date = END) -> dict[str, Any]:
    legs = {name: simulate_base(strategy, b, start, end) for name, b in bases.items()}
    daily = portfolio(legs)
    months = fb.by_month(daily)
    complete = [m for m in sorted(months) if len(months[m]) == fb._days_in_month(m)]
    monthly = [sum(months[m]) for m in complete]
    recent = monthly[-RECENT_MONTHS:]
    annual = _annual(monthly)
    years = sorted({m[:4] for m in complete})
    return {
        "strategy": strategy,
        "months": len(complete),
        "first_month": complete[0] if complete else None,
        "last_month": complete[-1] if complete else None,
        "annual_sharpe": annual,
        "entry_pass": annual is not None and annual >= ENTRY_SHARPE,
        "recent_annual_sharpe": _annual(recent),
        "annual_return_pct": round(statistics.fmean(monthly) * 12 * 100, 4) if monthly else None,
        "recent_annual_return_pct": round(statistics.fmean(recent) * 12 * 100, 4) if recent else None,
        "worst_month": None if not monthly else
        {"month": complete[monthly.index(min(monthly))], "pct": round(min(monthly) * 100, 4)},
        "negative_months": sum(1 for v in monthly if v < 0),
        "max_drawdown_pct": round(fb.max_drawdown(daily) * 100, 4),
        "years": {y: round(sum(sum(months[m]) for m in complete if m.startswith(y)) * 100, 4) for y in years},
        "events": {name: {"entries": leg.entries, "deliveries": leg.deliveries, "resets": leg.resets,
                          "liquidations": leg.liquidations, "held_days": leg.held_days,
                          "mean_locked_annual_pct": round(statistics.fmean(leg.locked_annual) * 100, 4)
                          if leg.locked_annual else None}
                   for name, leg in legs.items()},
        "monthly_pct": {m: round(v * 100, 4) for m, v in zip(complete, monthly)},
    }


def input_digest(bases: Mapping[str, Base]) -> str:
    h = hashlib.sha256()
    for name in sorted(bases):
        b = bases[name]
        for d in sorted(b.spot):
            bar = b.spot[d]
            h.update(f"{name}|spot|{d}|{bar.close!r}|{bar.high!r}".encode())
        for c in b.contracts:
            for d in sorted(c.bars):
                bar = c.bars[d]
                h.update(f"{c.symbol}|{d}|{bar.close!r}|{bar.high!r}".encode())
    return h.hexdigest()[:16]


def coverage(bases: Mapping[str, Base], end: date = END) -> dict[str, Any]:
    """What the cache holds, with no price in it: each contract's first and last bar against its delivery, and
    the first day each base is eligible."""
    out: dict[str, Any] = {}
    for name, b in sorted(bases.items()):
        first_bar = min((min(c.bars) for c in b.contracts if c.bars), default=None)
        first_eligible = None
        if first_bar is not None:
            day = first_bar + timedelta(days=1)
            while day <= end and first_eligible is None:
                first_eligible = day if b.eligible(day) else None
                day += timedelta(days=1)
        out[name] = {
            "spot": [min(b.spot).isoformat(), max(b.spot).isoformat()],
            "first_eligible_day": None if first_eligible is None else first_eligible.isoformat(),
            "contracts": {c.symbol: {"delivery": c.delivery.isoformat(),
                                     "first_bar": min(c.bars).isoformat() if c.bars else None,
                                     "last_bar": max(c.bars).isoformat() if c.bars else None,
                                     "bars": len(c.bars),
                                     "missing_days": None if not c.bars else
                                     (max(c.bars) - min(c.bars)).days + 1 - len(c.bars)}
                          for c in b.contracts},
        }
    return out


def measure(bases: Mapping[str, Base], *, start: date = START, end: date = END) -> dict[str, Any]:
    strategies = {s: measure_strategy(s, bases, start=start, end=end) for s in STRATEGIES}
    return {"spec": SPEC, "span": [start.isoformat(), end.isoformat()], "bases": sorted(bases),
            "input_digest": input_digest(bases), "strategies": strategies,
            "entry_sharpe": ENTRY_SHARPE,
            "screen_window": [SCREEN_WINDOW[0].isoformat(), SCREEN_WINDOW[1].isoformat()],
            "overall": "ENTRY_PASS" if any(r["entry_pass"] for r in strategies.values()) else "ENTRY_FAIL"}


# --- the command line -------------------------------------------------------------------------------------

def load(cache: Path) -> tuple[dict[str, Base], list[str]]:
    bases, missing = {}, []
    for name in BASES:
        b = read_base(cache, name)
        if b is None:
            missing.append(name)
        else:
            bases[name] = b
    return bases, missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quarterly_carry", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("load", "run"),
                        help="load: coverage and the input digest only, no returns; run: the measurement")
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    try:
        bases, missing = load(cache)
    except MissingContract as gap:
        print(f"BLOCKED: {gap}", file=sys.stderr)
        return EXIT_BLOCKED
    if missing:
        print(f"BLOCKED: no spot klines or quarterly contracts for {','.join(missing)} under {cache}",
              file=sys.stderr)
        return EXIT_BLOCKED
    if args.command == "load":
        print(json.dumps({"spec": SPEC, "input_digest": input_digest(bases), "coverage": coverage(bases)},
                         indent=1, sort_keys=True))
        return EXIT_OK
    try:
        report = {"measured_at": timeutil.utc_now_iso(), **fb.provenance(), **measure(bases)}
    except DataGap as gap:
        print(f"BLOCKED: {gap}", file=sys.stderr)
        return EXIT_BLOCKED
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return EXIT_OK
    for name, row in report["strategies"].items():
        print(f"{name}: annual SR {row['annual_sharpe']} over {row['months']} months "
              f"({row['first_month']}..{row['last_month']}) -> {'PASS' if row['entry_pass'] else 'FAIL'} "
              f"| recent {RECENT_MONTHS} SR {row['recent_annual_sharpe']} | annual {row['annual_return_pct']}% "
              f"| worst {row['worst_month']} | maxDD {row['max_drawdown_pct']}% | events {row['events']}")
    print(f"overall: {report['overall']} (input {report['input_digest']})")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
