"""Market-neutral cross-section: does a long-short basket beat zero and a random basket after costs?

``docs/proposals/MARKET_NEUTRAL_CROSS_SECTION_V0.1.md`` (pre-registered; Thomas 2026-11-04: Q1 top 10 by volume
plus the symbols he names, Q2 three fixed signals, Q3 1d only, Q4 the archive fetched as ``claude``). It reads
only the archive files ``scripts/binance_archive.py fetch-monthly`` kept (``klines-1d`` and ``fundingRate``,
under ``<RESEARCH_CACHE>/archive``) and writes nothing.

- **Universe (§4):** on each month's first day, the :data:`TOP_N` USDT perps by quote volume over the
  :data:`VOLUME_LOOKBACK_DAYS` days before it, among those with a bar the day before and a first bar at least
  :data:`MIN_AGE_DAYS` earlier, plus every named symbol that meets the same age and trading rule. Delisted
  symbols are in while they traded. Below :data:`MIN_UNIVERSE` members there is no basket.
- **Coins only (§4, amended 2026-11-04 before the official run):** a USDT perp is a coin perp, contract type
  :data:`COIN_CONTRACT`. The gold, oil and stock perps Binance has listed since 2025-12-11
  (``TRADIFI_PERPETUAL``) are left out, read from the ``contract_types.json`` snapshot that
  ``binance_archive.py contract-types`` writes beside the archive. A symbol the snapshot does not hold was
  delisted before it was taken and is kept: the four that traded after 2025-12-11 are coin tokens (the
  proposal's record lists them). Without the snapshot both commands stop BLOCKED.
- **Basket:** ``k = max(2, floor(0.2·n))`` long and ``k`` short, weights ``+1/k`` and ``−1/k``, so the daily
  gross is the long side's mean return minus the short side's.
- **Signals (§5, fixed):** ``S-REV`` (1-day return, losers long, rebalanced daily), ``S-MOM7`` and
  ``S-MOM30`` (7- and 30-day return, winners long, rebalanced weekly on Sunday's close). A decision uses
  closes up to its day and is executed at the next day's open; a day's return runs open to next open.
- **Costs:** every unit of weight traded pays the taker fee plus slippage (``crypto/cost.py``, the runtime's
  own constants); funding settled inside a held day is paid by longs and received by shorts at the archived
  rate. The sensitivity run doubles slippage for the lower-volume half of each month's universe.
- **Delisting:** a held leg whose next bar is missing is closed at its last close, pays the exit cost, and
  stays flat until the next rebalance. These forced closes are counted and their net contribution reported.
- **Random baseline:** the same days, universe, ``k`` and rebalance days with the ranking drawn from
  ``sha256(seed|day|symbol)``, over :data:`RANDOM_SEEDS` seeds, averaged.
- **Reading (§5):** non-overlapping Monday–Sunday weeks of net return. Per signal: the weekly mean, its
  two-sided p (normal approximation of the t statistic over weeks), Holm at :data:`HOLM_ALPHA` over the three
  signals, the mean of (strategy − random) per week, and the calendar years with positive net. LEAD, NULL or
  ANTI per the pre-registered rule; the 95% interval is shown, never judged.

- **Test v0.2 (``--test v0.2``, ``CROSS_SECTION_CARRY_AND_WIDE_MOMENTUM_V0.1``, Thomas 2026-11-04):** the top 30,
  ``S-CARRY`` (funding settled over the 7 UTC days to the decision day; lowest long, highest short; weekly) and
  ``S-MOM30``. Its Holm family is v0.1's three p values plus its own two. It also reports the weekly mean
  without funding and the weekly correlation of its two signals, for display only.

``universe`` prints the monthly universes and every symbol that ever enters, which is the list
``fetch-monthly --kind fundingRate`` needs. ``run`` measures, in the research sandbox only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import statistics
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from runtime.mvp_runtime.crypto import cost  # noqa: E402

KLINES_KIND = "klines-1d"
FUNDING_KIND = "fundingRate"
CONTRACT_TYPES_FILENAME = "contract_types.json"     # binance_archive.CONTRACT_TYPES_FILENAME
COIN_CONTRACT = "PERPETUAL"
TOP_N = 10
MIN_AGE_DAYS = 90
VOLUME_LOOKBACK_DAYS = 30
BASKET_SHARE = 0.2
MIN_SIDE = 2
MIN_UNIVERSE = 2 * MIN_SIDE
START = date(2020, 1, 1)
END = date(2026, 10, 30)                 # the last held day: its return runs to 2026-10-31's open, the last bar
                                         # the 2026-10 monthly file holds, so the window ends there (§5)
YEARS = tuple(range(START.year, END.year + 1))
LEAD_MIN_POSITIVE_YEARS = 5
HOLM_ALPHA = 0.10
RANDOM_SEEDS = 8
CONFIDENCE_Z = 1.96
SIGNALS: dict[str, dict[str, Any]] = {
    "S-REV": {"lookback": 1, "winners_long": False, "rebalance": "daily"},
    "S-MOM7": {"lookback": 7, "winners_long": True, "rebalance": "weekly"},
    "S-MOM30": {"lookback": 30, "winners_long": True, "rebalance": "weekly"},
    # CROSS_SECTION_CARRY_AND_WIDE_MOMENTUM_V0.1 §3: funding settled over the 7 UTC days to the decision day,
    # lowest long, highest short (a descending sort, reversed).
    "S-CARRY": {"score": "funding", "lookback": 7, "winners_long": False, "rebalance": "weekly"},
}
# One pre-registered test = one universe size and one signal set. v0.1 is MARKET_NEUTRAL_CROSS_SECTION_V0.1 as it
# ran on 2026-11-04 (run 20261104T070402Z-164942); v0.2 is CROSS_SECTION_CARRY_AND_WIDE_MOMENTUM_V0.1. A later
# test carries the earlier tests' p values into its Holm family, so the archive is not read twice for free.
TESTS: dict[str, dict[str, Any]] = {
    "v0.1": {"top_n": TOP_N, "signals": ("S-REV", "S-MOM7", "S-MOM30"), "prior_p": {}},
    "v0.2": {"top_n": 30, "signals": ("S-CARRY", "S-MOM30"),
             "prior_p": {"v0.1 S-REV": 0.13569777593264654, "v0.1 S-MOM7": 0.28617573036292365,
                         "v0.1 S-MOM30": 0.12249320636703333}},
}
SUNDAY = 6


@dataclass(frozen=True, slots=True)
class Bar:
    open: float
    close: float
    quote_volume: float


@dataclass
class Market:
    """Daily bars and funding settlements per symbol, as the archive holds them."""
    bars: dict[str, dict[date, Bar]] = field(default_factory=dict)
    funding: dict[str, list[tuple[int, float]]] = field(default_factory=dict)
    # {symbol: exchangeInfo contractType}; a symbol absent from it is a coin (delisted before the snapshot)
    contract_types: dict[str, str] = field(default_factory=dict)
    _funding_by_day: dict[str, dict[date, float]] = field(default_factory=dict, repr=False)

    def funding_day(self, symbol: str) -> dict[date, float] | None:
        """``{UTC day: summed rate}`` for one symbol, built once; None when it has no funding on file."""
        if symbol not in self._funding_by_day:
            rows = self.funding.get(symbol) or []
            by_day: dict[date, float] = {}
            for t, rate in rows:
                day = _utc_day(t)
                by_day[day] = by_day.get(day, 0.0) + rate
            self._funding_by_day[symbol] = by_day
        return self._funding_by_day[symbol] or None

    def first_day(self, symbol: str) -> date | None:
        days = self.bars.get(symbol)
        return min(days) if days else None


# --- reading the archive ------------------------------------------------------------------------------

def _rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        text = archive.read(archive.namelist()[0]).decode("utf-8")
    return [row for row in csv.reader(io.StringIO(text)) if row]


def _utc_day(ms: float) -> date:
    if ms > 1e14:   # microseconds, the unit Binance moved some spot files to in 2025
        ms /= 1000
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()


def read_klines(folder: Path) -> dict[date, Bar]:
    """One symbol's daily bars from its monthly zips. Older files have no header row; newer ones do."""
    out: dict[date, Bar] = {}
    for path in sorted(folder.glob("*.zip")) if folder.exists() else []:
        for row in _rows(path):
            try:
                day = _utc_day(float(row[0]))
                bar = Bar(open=float(row[1]), close=float(row[4]), quote_volume=float(row[7]))
            except (ValueError, IndexError):
                continue          # the header row
            if bar.open > 0 and bar.close > 0:
                out[day] = bar
    return out


def read_funding(folder: Path) -> list[tuple[int, float]]:
    """One symbol's settlements ``(calc_time ms, rate)`` from its monthly zips, oldest first."""
    out: list[tuple[int, float]] = []
    for path in sorted(folder.glob("*.zip")) if folder.exists() else []:
        for row in _rows(path):
            try:
                out.append((int(float(row[0])), float(row[-1])))
            except (ValueError, IndexError):
                continue
    return sorted(set(out))


def read_contract_types(archive: Path) -> tuple[dict[str, str], str] | None:
    """``({symbol: contractType}, fetched_at)`` from the snapshot, or None when it is missing or unreadable."""
    try:
        record = json.loads((archive / CONTRACT_TYPES_FILENAME).read_text(encoding="utf-8"))
        kinds, fetched_at = record["contract_types"], record["fetched_at"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(kinds, dict) or not kinds or not isinstance(fetched_at, str) \
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in kinds.items()):
        return None
    return dict(kinds), fetched_at


def load_market(archive: Path, *, funding: bool = True) -> Market:
    market = Market()
    klines = archive / KLINES_KIND
    for folder in sorted(klines.iterdir()) if klines.exists() else []:
        bars = read_klines(folder)
        if bars:
            market.bars[folder.name] = bars
    if funding:
        for symbol in market.bars:
            market.funding[symbol] = read_funding(archive / FUNDING_KIND / symbol)
    return market


# --- the universe --------------------------------------------------------------------------------------

def month_starts(start: date, end: date) -> list[date]:
    out, day = [], date(start.year, start.month, 1)
    while day <= end:
        out.append(day)
        day = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
    return out


def is_coin(market: Market, symbol: str) -> bool:
    return market.contract_types.get(symbol, COIN_CONTRACT) == COIN_CONTRACT


def eligible(market: Market, symbol: str, month_start: date) -> bool:
    """A coin perp, trading the day before ``month_start`` and listed at least :data:`MIN_AGE_DAYS` before it."""
    if not is_coin(market, symbol):
        return False
    bars = market.bars.get(symbol) or {}
    first = market.first_day(symbol)
    return (month_start - timedelta(days=1)) in bars and first is not None \
        and first <= month_start - timedelta(days=MIN_AGE_DAYS)


def trailing_volume(market: Market, symbol: str, month_start: date) -> float:
    bars = market.bars.get(symbol) or {}
    return sum(bars[d].quote_volume for d in (month_start - timedelta(days=i)
                                              for i in range(1, VOLUME_LOOKBACK_DAYS + 1)) if d in bars)


def universe_at(market: Market, month_start: date, *, named: Sequence[str] = (), top_n: int = TOP_N) -> list[str]:
    """The month's members, by trailing volume, highest first: the top ``top_n`` plus every eligible named
    symbol. Reads nothing on or after ``month_start``."""
    ranked = sorted((s for s in market.bars if eligible(market, s, month_start)),
                    key=lambda s: (-trailing_volume(market, s, month_start), s))
    members = ranked[:top_n]
    members += [s for s in ranked if s in set(named) and s not in members]
    return sorted(members, key=lambda s: (-trailing_volume(market, s, month_start), s))


def universes(market: Market, *, named: Sequence[str] = (), top_n: int = TOP_N) -> dict[date, list[str]]:
    return {m: universe_at(market, m, named=named, top_n=top_n) for m in month_starts(START, END)}


def side_size(members: int) -> int:
    return 0 if members < MIN_UNIVERSE else max(MIN_SIDE, int(members * BASKET_SHARE))


# --- signals and baskets --------------------------------------------------------------------------------

def trailing_return(market: Market, symbol: str, day: date, lookback: int) -> float | None:
    bars = market.bars.get(symbol) or {}
    now, then = bars.get(day), bars.get(day - timedelta(days=lookback))
    return None if now is None or then is None else now.close / then.close - 1


def trailing_funding(market: Market, symbol: str, day: date, lookback: int) -> float | None:
    """Rates settled on the UTC days ``day−lookback+1 … day``, summed; None when none settled in them. A
    settlement stamped 00:00 of ``day+1`` belongs to the next day and is not read."""
    by_day = market.funding_day(symbol) or {}
    window = [by_day[d] for d in (day - timedelta(days=i) for i in range(lookback)) if d in by_day]
    return sum(window) if window else None


def scores(market: Market, members: Sequence[str], day: date, signal: str) -> list[tuple[str, float]]:
    """The rankable members and their score at ``day``'s close; a member without one sits the rebalance out."""
    rule = SIGNALS[signal]
    read = trailing_funding if rule.get("score") == "funding" else trailing_return
    scored = [(s, read(market, s, day, rule["lookback"])) for s in members]
    return [(s, v) for s, v in scored if v is not None]


def random_key(seed: str, day: date, symbol: str) -> str:
    return hashlib.sha256(f"{seed}|{day.isoformat()}|{symbol}".encode()).hexdigest()


def target_weights(market: Market, members: Sequence[str], day: date, signal: str,
                   *, seed: str | None = None) -> dict[str, float]:
    """The basket decided at ``day``'s close: ``+1/k`` on the long side, ``−1/k`` on the short side.
    With ``seed`` the ranking is the seeded random draw instead of the signal."""
    rule = SIGNALS[signal]
    scored = scores(market, members, day, signal)
    k = side_size(len(scored))
    if k == 0:
        return {}
    if seed is not None:
        order = sorted((s for s, _ in scored), key=lambda s: random_key(seed, day, s))
    else:
        order = [s for s, _ in sorted(scored, key=lambda sr: (-sr[1], sr[0]))]
        if not rule["winners_long"]:
            order.reverse()
    longs, shorts = order[:k], order[-k:]
    return {**{s: 1.0 / k for s in longs}, **{s: -1.0 / k for s in shorts}}


def is_rebalance_day(signal: str, day: date) -> bool:
    return SIGNALS[signal]["rebalance"] == "daily" or day.weekday() == SUNDAY


# --- the simulation -------------------------------------------------------------------------------------

def held_days(start: date = START, end: date = END) -> Iterable[date]:
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def funding_on(market: Market, symbol: str, day: date) -> tuple[float, bool]:
    """Sum of the rates settled inside ``day`` (UTC), and whether the symbol has any funding on file."""
    by_day = market.funding_day(symbol)
    if by_day is None:
        return 0.0, False
    return by_day.get(day, 0.0), True


def member_volume_halves(market: Market, members: Sequence[str], month_start: date) -> set[str]:
    """The lower-volume half of a month's universe (the sensitivity run's doubled-slippage set)."""
    ranked = sorted(members, key=lambda s: (-trailing_volume(market, s, month_start), s))
    return set(ranked[(len(ranked) + 1) // 2:])


def simulate(market: Market, signal: str, monthly: Mapping[date, Sequence[str]], *, seed: str | None = None,
             slippage_low_multiplier: float = 1.0, start: date = START, end: date = END) -> dict[str, Any]:
    """Daily net returns of one signal's basket (or its seeded random twin) over [start, end].

    Day ``t`` holds the weights decided at the latest rebalance close before it, from ``t``'s open to
    ``t+1``'s. A leg whose ``t+1`` bar is missing is closed at ``t``'s close instead and stays flat."""
    fee_bps = cost.DEFAULT_TAKER_FEE_BPS
    slip_bps = cost.DEFAULT_SLIPPAGE_BPS
    weights: dict[str, float] = {}
    daily: dict[date, dict[str, float]] = {}
    forced = {"count": 0, "net": 0.0}
    funding_missing_days = 0
    unranked = 0

    def month_of(day: date) -> date:
        return date(day.year, day.month, 1)

    def unit_cost(symbol: str, day: date) -> float:
        low = member_volume_halves(market, monthly.get(month_of(day), ()), month_of(day)) \
            if slippage_low_multiplier != 1.0 else set()
        slip = slip_bps * (slippage_low_multiplier if symbol in low else 1.0)
        return (fee_bps + slip) / 10_000

    for day in held_days(start, end):
        decision = day - timedelta(days=1)
        trade_cost = 0.0
        if is_rebalance_day(signal, decision):
            members = monthly.get(month_of(decision), ())
            target = target_weights(market, members, decision, signal, seed=seed)
            unranked += len(members) - len(scores(market, members, decision, signal))
            for symbol in set(weights) | set(target):
                trade_cost += abs(target.get(symbol, 0.0) - weights.get(symbol, 0.0)) * unit_cost(symbol, day)
            weights = target
        if not weights:
            continue
        gross = 0.0
        paid_funding = 0.0
        closing: list[str] = []
        exit_cost = 0.0
        for symbol, w in weights.items():
            bars = market.bars[symbol]
            today, tomorrow = bars.get(day), bars.get(day + timedelta(days=1))
            if today is None:
                closing.append(symbol)      # no bar today: the leg ended before it; nothing earned
                continue
            if tomorrow is None:
                leg = w * (today.close / today.open - 1)
                leg_exit = abs(w) * unit_cost(symbol, day)
                gross += leg
                exit_cost += leg_exit
                forced["count"] += 1
                forced["net"] += leg - leg_exit
                closing.append(symbol)
            else:
                gross += w * (tomorrow.open / today.open - 1)
            rate, known = funding_on(market, symbol, day)
            paid_funding += -w * rate
            funding_missing_days += 0 if known else 1
        for symbol in closing:
            weights.pop(symbol, None)
        daily[day] = {"gross": gross, "cost": trade_cost + exit_cost, "funding": paid_funding,
                      "net": gross - trade_cost - exit_cost + paid_funding}
    return {"daily": daily, "forced_closes": forced, "funding_missing_leg_days": funding_missing_days,
            "unranked_member_rebalances": unranked}


# --- the reading ------------------------------------------------------------------------------------------

def week_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def weekly(daily: Mapping[date, Mapping[str, float]], *, key: str = "net") -> dict[date, float]:
    out: dict[date, float] = {}
    for day, row in daily.items():
        value = row["net"] - row["funding"] if key == "ex_funding" else row[key]
        out[week_of(day)] = out.get(week_of(day), 0.0) + value
    return dict(sorted(out.items()))


def summary(values: Sequence[float]) -> dict[str, Any]:
    """Mean, its normal two-sided p and 95% interval, over weekly values (bps of one side's notional)."""
    n = len(values)
    if n < 2:
        return {"weeks": n, "mean_bps": None, "p": None, "ci_bps": None}
    mean = statistics.fmean(values)
    se = statistics.stdev(values) / math.sqrt(n)
    p = math.erfc(abs(mean / se) / math.sqrt(2)) if se > 0 else None
    return {"weeks": n, "mean_bps": round(mean * 1e4, 3), "p": p,
            "ci_bps": [round((mean - CONFIDENCE_Z * se) * 1e4, 3), round((mean + CONFIDENCE_Z * se) * 1e4, 3)]}


def positive_years(weeks: Mapping[date, float]) -> dict[int, bool]:
    totals: dict[int, float] = {}
    for monday, value in weeks.items():
        totals[monday.year] = totals.get(monday.year, 0.0) + value
    return {year: totals.get(year, 0.0) > 0 for year in YEARS}


def holm(pvalues: Sequence[float], alpha: float) -> set[int]:
    """Holm step-down over the given p values (the same rule as ``selection_evidence.holm``)."""
    order = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    out: set[int] = set()
    for rank, index in enumerate(order):
        if pvalues[index] > alpha / (len(pvalues) - rank):
            break
        out.add(index)
    return out


def reading(*, holm_pass: bool, mean_bps: float | None, diff_mean_bps: float | None, years_positive: int) -> str:
    """MARKET_NEUTRAL_CROSS_SECTION_V0.1 §5: LEAD, NULL or ANTI for one signal."""
    if holm_pass and mean_bps is not None:
        if mean_bps > 0 and (diff_mean_bps or 0) > 0 and years_positive >= LEAD_MIN_POSITIVE_YEARS:
            return "LEAD"
        if mean_bps < 0:
            return "ANTI"
    return "NULL"


def family_holm(prior_p: Mapping[str, float], new_p: Sequence[float | None], alpha: float) -> set[int]:
    """Which of ``new_p`` pass Holm over the earlier tests' p values and the new ones together."""
    testable = [i for i, p in enumerate(new_p) if p is not None]
    pvalues = list(prior_p.values()) + [new_p[i] for i in testable]
    offset = len(prior_p)
    return {testable[j - offset] for j in holm(pvalues, alpha) if j >= offset}


def measure(market: Market, *, named: Sequence[str] = (), seeds: int = RANDOM_SEEDS, test: str = "v0.1",
            say: Callable[[str], None] = lambda _l: None) -> dict[str, Any]:
    spec = TESTS[test]
    monthly = universes(market, named=named, top_n=spec["top_n"])
    per_signal: dict[str, dict[str, Any]] = {}
    net_weeks: dict[str, dict[date, float]] = {}
    for signal in spec["signals"]:
        say(f"{signal}: strategy")
        run = simulate(market, signal, monthly)
        weeks = weekly(run["daily"])
        random_weeks: list[dict[date, float]] = []
        for k in range(1, seeds + 1):
            say(f"{signal}: random seed {k}/{seeds}")
            random_weeks.append(weekly(simulate(market, signal, monthly, seed=f"xs-basket|{k}")["daily"]))
        baseline = {w: statistics.fmean(r.get(w, 0.0) for r in random_weeks) for w in weeks}
        say(f"{signal}: sensitivity")
        sensitive = weekly(simulate(market, signal, monthly, slippage_low_multiplier=2.0)["daily"])
        days = run["daily"].values()
        years = positive_years(weeks)
        net_weeks[signal] = weeks
        per_signal[signal] = {
            **summary(list(weeks.values())),
            "vs_random": summary([weeks[w] - baseline[w] for w in weeks]),
            "random_mean_bps": summary(list(baseline.values()))["mean_bps"],
            "sensitivity_mean_bps": summary(list(sensitive.values()))["mean_bps"],
            "years_positive": sum(years.values()), "by_year": {str(y): v for y, v in years.items()},
            "gross_bps_per_day": round(statistics.fmean(d["gross"] for d in days) * 1e4, 3) if days else None,
            "cost_bps_per_day": round(statistics.fmean(d["cost"] for d in days) * 1e4, 3) if days else None,
            "funding_bps_per_day": round(statistics.fmean(d["funding"] for d in days) * 1e4, 3) if days else None,
            "forced_closes": {"count": run["forced_closes"]["count"],
                              "net_bps": round(run["forced_closes"]["net"] * 1e4, 3)},
            "funding_missing_leg_days": run["funding_missing_leg_days"],
            "unranked_member_rebalances": run["unranked_member_rebalances"],
            "ex_funding_mean_bps": summary(list(weekly(run["daily"], key="ex_funding").values()))["mean_bps"],
        }
    names = list(per_signal)
    passed = family_holm(spec["prior_p"], [per_signal[s]["p"] for s in names], HOLM_ALPHA)
    for i, signal in enumerate(names):
        row = per_signal[signal]
        row["holm_pass"] = i in passed
        row["sensitivity_sign_flip"] = (row["mean_bps"] is not None and row["sensitivity_mean_bps"] is not None
                                        and (row["mean_bps"] > 0) != (row["sensitivity_mean_bps"] > 0))
        row["reading"] = reading(holm_pass=row["holm_pass"], mean_bps=row["mean_bps"],
                                 diff_mean_bps=row["vs_random"]["mean_bps"], years_positive=row["years_positive"])
    members = sorted({s for m in monthly.values() for s in m})
    correlation = None
    if len(names) == 2:
        common = sorted(set(net_weeks[names[0]]) & set(net_weeks[names[1]]))
        a, b = [net_weeks[names[0]][w] for w in common], [net_weeks[names[1]][w] for w in common]
        if len(common) > 2 and statistics.pstdev(a) > 0 and statistics.pstdev(b) > 0:
            correlation = round(statistics.correlation(a, b), 4)
    return {
        "test": test, "top_n": spec["top_n"], "holm_family": {**spec["prior_p"], **{
            f"{test} {s}": per_signal[s]["p"] for s in names}},
        "weekly_correlation": correlation,
        "signals": per_signal,
        "overall": "LEAD_FOUND" if any(r["reading"] == "LEAD" for r in per_signal.values())
        else "NO_EDGE_IN_THIS_CROSS_SECTION",
        "universe": {"named": list(named), "members_ever": members,
                     "not_coin_left_out": sorted(s for s in market.bars if not is_coin(market, s)),
                     "months_with_basket": sum(1 for m in monthly.values() if side_size(len(m))),
                     "hash": universe_hash(monthly)},
    }


def universe_hash(monthly: Mapping[date, Sequence[str]]) -> str:
    body = json.dumps({m.isoformat(): list(v) for m, v in sorted(monthly.items())}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def provenance() -> dict[str, Any]:
    from runtime.mvp_runtime.crypto import judgement_fingerprint

    snapshot = os.environ.get("RESEARCH_LAKE")
    return {"judgement_fingerprint": judgement_fingerprint.judgement_fingerprint()["short"],
            "lake_snapshot": Path(snapshot).name if snapshot else None,
            "costs_bps": {"taker": cost.DEFAULT_TAKER_FEE_BPS, "slippage": cost.DEFAULT_SLIPPAGE_BPS}}


# --- the command line -------------------------------------------------------------------------------------

def _archive_default() -> Path:
    return Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache"))) / "archive"


def _in_sandbox() -> bool:
    from runtime.mvp_runtime.crypto import market_data
    from scripts.research import lake

    return getattr(market_data.select_market_data_collector, "__module__", "") == lake.__name__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cross_section_basket", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("universe", "run"):
        p = sub.add_parser(name)
        p.add_argument("--test", choices=sorted(TESTS), default="v0.1",
                       help="the pre-registered test: universe size and signals (default v0.1)")
        p.add_argument("--archive", type=Path, default=None, help="default: $RESEARCH_CACHE/archive")
        p.add_argument("--named", default="", help="comma list of the symbols Thomas named (§4, fixed before run)")
        p.add_argument("--json", action="store_true")
    sub.choices["run"].add_argument("--top-10-only", action="store_true",
                                    help="ignore --named (the display line beside the verdict, §4)")
    args = parser.parse_args(argv)
    archive = args.archive or _archive_default()
    named = [s.strip().upper() for s in args.named.split(",") if s.strip()]
    if args.command == "run" and getattr(args, "top_10_only", False):
        named = []
    if args.command == "run" and not _in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    snapshot = read_contract_types(archive)
    if snapshot is None:
        print(f"BLOCKED: no readable {CONTRACT_TYPES_FILENAME} under {archive} "
              "(python3 scripts/binance_archive.py contract-types --dest <archive>)", file=sys.stderr)
        return EXIT_BLOCKED
    market = load_market(archive, funding=args.command == "run")
    if not market.bars:
        print(f"BLOCKED: no {KLINES_KIND} files under {archive}", file=sys.stderr)
        return EXIT_BLOCKED
    market.contract_types, contract_types_at = snapshot
    if args.command == "universe":
        monthly = universes(market, named=named, top_n=TESTS[args.test]["top_n"])
        members = sorted({s for m in monthly.values() for s in m})
        if args.json:
            print(json.dumps({"members_ever": members, "hash": universe_hash(monthly),
                              "contract_types_at": contract_types_at,
                              "not_coin_left_out": sorted(s for s in market.bars if not is_coin(market, s)),
                              "monthly": {m.isoformat(): v for m, v in monthly.items()}}, indent=1))
        else:
            for month, symbols in monthly.items():
                print(f"{month.isoformat()} k={side_size(len(symbols))} {' '.join(symbols)}")
            print(f"members ever: {len(members)}: {','.join(members)}")
            print(f"not a coin perp, left out (contract types {contract_types_at}): "
                  f"{len([s for s in market.bars if not is_coin(market, s)])}")
        return EXIT_OK
    report = {"measured_at": timeutil.utc_now_iso(), **provenance(), "contract_types_at": contract_types_at,
              **measure(market, named=named, test=args.test, say=(lambda *_a: None) if args.json else print)}
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True, default=str))
        return EXIT_OK
    print(f"universe {report['universe']['hash']}: {len(report['universe']['members_ever'])} symbols ever, "
          f"{report['universe']['months_with_basket']} months with a basket, named {report['universe']['named']}")
    for signal, row in report["signals"].items():
        print(f"{signal}: weeks {row['weeks']} mean {row['mean_bps']} bps CI {row['ci_bps']} p {row['p']} "
              f"Holm {row['holm_pass']} | vs random {row['vs_random']['mean_bps']} | years+ {row['years_positive']}"
              f" | sensitivity {row['sensitivity_mean_bps']} -> {row['reading']}")
    print(f"weekly correlation: {report['weekly_correlation']}")
    print(f"overall: {report['overall']}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
