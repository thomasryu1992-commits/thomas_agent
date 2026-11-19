"""L26 pairs relative value (PAIRS_RELATIVE_VALUE_V0.1): ten distance pairs of the top thirty, traded on their spread.

Each month the top thirty's pairs are formed on the 365 days before the month's first day (both coins with at least
330 closes; distance = mean squared difference of the log closes, each pair's spread anchored at its first common
day). The ten nearest are listed. At each daily decision an unopened listed pair with |z| >= 2 at that close opens:
short the rich leg, long the cheap one, one tenth each, held as quantities from then on. It closes when z reaches
zero or crosses it, at the twentieth decision after it opened, when it leaves the list at a month change, or when a
leg has no bar. The book is accounted by ``strategy_ladder.simulate_held`` under the daily signal ``S-REV``.

The order of the document's section 4 is the order of the commands: ``pairs-counts`` (no return is read; the stop
rule), ``pairs-null`` (random pairs from the same eligible set, 600 books), ``pairs-plant`` (a planted reverting
pair, the door's size), ``pairs-book`` (the real book, once). Nothing after 2024-12-31 is read.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import basket_search as bs  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import signal_lab as lab  # noqa: E402
from scripts.research import strategy_ladder as sl  # noqa: E402

SPEC = "PAIRS_RELATIVE_VALUE_V0.1"
SPAN = (date(2021, 1, 1), date(2024, 12, 31))   # measured; the market also holds 2020 for the first formation
TIER = "top30"
SIGNAL = "S-REV"                                 # only its daily decision calendar is used
FORMATION_DAYS = 365
MIN_CLOSES = 330
PAIRS = 10
OPEN_Z = 2.0
MAX_HOLD = 20                                    # decisions after the opening one
LEG = 0.1
MIN_OPENS = 12                                   # in each of COUNT_YEARS, or the stop rule holds
COUNT_YEARS = (2022, 2023, 2024)
NULLS = 600
NULL_SEED = "pairs-null"
PLANT_SEED = "pairs-plant"
PLANT_HALF_LIVES = (2, 5, 10)
PLANT_SEEDS = 50
PLANT_PAIRS = 3
PLANT_MIN_MONTHS = 36                            # of the 48 measured months a planted coin must be in the tier
PLANT_POWER = 0.5                                # under this share at h = 2, a "none" is "the door cannot see"
ALPHA = 0.05
COST_UNIT = (xsb.cost.DEFAULT_TAKER_FEE_BPS + xsb.cost.DEFAULT_SLIPPAGE_BPS) / 10_000

Key = tuple[str, str]


@dataclass(frozen=True, slots=True)
class Pair:
    a: str
    b: str
    anchor: float        # ln a - ln b at the first common day of the formation window
    mean: float          # of the anchored spread over the window
    sd: float
    distance: float      # mean squared anchored spread

    @property
    def key(self) -> Key:
        return (self.a, self.b)


def _close(market: xsb.Market, symbol: str, day: date) -> float | None:
    bar = (market.bars.get(symbol) or {}).get(day)
    return bar.close if bar is not None and bar.close > 0 else None


def formation(market: xsb.Market, members: Sequence[str], month: date) -> list[Pair]:
    """Every eligible pair of ``members`` on the closes of the 365 days before ``month`` (nothing on or after it)."""
    first = month - timedelta(days=FORMATION_DAYS)
    logs: dict[str, dict[date, float]] = {}
    for symbol in sorted(set(members)):
        bars = market.bars.get(symbol) or {}
        own = {}
        for i in range(FORMATION_DAYS):
            day = first + timedelta(days=i)
            bar = bars.get(day)
            if bar is not None and bar.close > 0:
                own[day] = math.log(bar.close)
        if len(own) >= MIN_CLOSES:
            logs[symbol] = own
    names = sorted(logs)
    out: list[Pair] = []
    for i, a in enumerate(names):
        la = logs[a]
        for b in names[i + 1:]:
            lb = logs[b]
            days = sorted(la.keys() & lb.keys())
            if len(days) < 2:
                continue
            anchor = la[days[0]] - lb[days[0]]
            s = [la[d] - lb[d] - anchor for d in days]
            n = len(s)
            mean = sum(s) / n
            var = sum((x - mean) ** 2 for x in s) / n
            if not var > 0:
                continue
            out.append(Pair(a, b, anchor, mean, math.sqrt(var), sum(x * x for x in s) / n))
    return out


def formations(market: xsb.Market, monthly: Mapping[date, Sequence[str]]) -> dict[date, list[Pair]]:
    """The eligible pairs of every measured month."""
    return {m: formation(market, members, m) for m, members in sorted(monthly.items()) if SPAN[0] <= m <= SPAN[1]}


def nearest(pairs: Sequence[Pair], count: int = PAIRS) -> list[Pair]:
    return sorted(pairs, key=lambda p: (p.distance, p.a, p.b))[:count]


def random_pairs(pairs: Sequence[Pair], seed: str, month: date, count: int = PAIRS) -> list[Pair]:
    """The null's choice: ``count`` of the same eligible pairs, in a seeded order that ignores the distance."""
    return sorted(pairs, key=lambda p: xsb.random_key(seed, month, f"{p.a}|{p.b}"))[:count]


def z_score(market: xsb.Market, pair: Pair, day: date) -> float | None:
    """The pair's z at ``day``'s close against its formation; None when a leg has no close that day."""
    a, b = _close(market, pair.a, day), _close(market, pair.b, day)
    if a is None or b is None:
        return None
    return (math.log(a) - math.log(b) - pair.anchor - pair.mean) / pair.sd


@dataclass(slots=True)
class Position:
    pair: Pair
    sign: int            # +1: z was high, a is rich (short a, long b); -1 the other way
    opened: date
    count: int           # decisions since the opening one
    closes: tuple[float, float]


class Trader:
    """The pairs' open/close state, driven one decision at a time in order. One instance is one book."""

    def __init__(self, market: xsb.Market, chosen: Mapping[date, Sequence[Pair]]):
        self.market = market
        self.chosen = chosen
        self.month: date | None = None
        self.listed: dict[Key, Pair] = {}
        self.open: dict[Key, Position] = {}
        self.blocked: dict[Key, int] = {}          # stopped at twenty: no reopening until z reaches zero
        self.events: list[dict[str, Any]] = []
        self.last: date | None = None

    def _close_position(self, key: Key, day: date, reason: str) -> None:
        pos = self.open.pop(key)
        self.events.append({"pair": f"{key[0]}|{key[1]}", "opened": pos.opened, "closed": day, "reason": reason,
                            "held": pos.count})

    def decide(self, day: date) -> dict[str, float]:
        if self.last is not None and day <= self.last:
            raise ValueError(f"decisions must come in order: {day} after {self.last}")
        self.last = day
        if day < SPAN[0]:
            return {}
        closed_now: set[Key] = set()
        month = date(day.year, day.month, 1)
        if month != self.month:
            self.month = month
            self.listed = {p.key: p for p in self.chosen.get(month, ())}
            for key in sorted(self.open):
                if key in self.listed:
                    self.open[key].pair = self.listed[key]      # a kept pair is read against the new formation
                else:
                    self._close_position(key, day, "month")
                    closed_now.add(key)
            self.blocked = {k: v for k, v in self.blocked.items() if k in self.listed}
        for key in sorted(self.open):
            pos = self.open[key]
            pos.count += 1
            z = z_score(self.market, pos.pair, day)
            if z is None:
                self._close_position(key, day, "missing")
            elif pos.sign * z <= 0:
                self._close_position(key, day, "zero")
            elif pos.count >= MAX_HOLD:
                self._close_position(key, day, "twenty")
                self.blocked[key] = pos.sign
            else:
                continue
            closed_now.add(key)
        for key, pair in sorted(self.listed.items()):
            if key in self.open or key in closed_now:
                continue
            z = z_score(self.market, pair, day)
            if z is None:
                continue
            if key in self.blocked:
                if self.blocked[key] * z > 0:
                    continue
                del self.blocked[key]
            if abs(z) >= OPEN_Z:
                sign = 1 if z > 0 else -1
                self.open[key] = Position(pair, sign, day, 0,
                                          (_close(self.market, pair.a, day), _close(self.market, pair.b, day)))
                self.events.append({"pair": f"{key[0]}|{key[1]}", "opened": day, "closed": None, "reason": "open",
                                    "held": 0})
        return self.weights(day)

    def weights(self, day: date) -> dict[str, float]:
        """Each open pair's legs as quantities held from the opening decision's close: one tenth then, its move since."""
        out: dict[str, float] = {}
        for key, pos in sorted(self.open.items()):
            a_now, b_now = _close(self.market, pos.pair.a, day), _close(self.market, pos.pair.b, day)
            out[pos.pair.a] = out.get(pos.pair.a, 0.0) - pos.sign * LEG * a_now / pos.closes[0]
            out[pos.pair.b] = out.get(pos.pair.b, 0.0) + pos.sign * LEG * b_now / pos.closes[1]
        return {s: w for s, w in out.items() if abs(w) > 1e-12}


def weigher(trader: Trader) -> Callable[..., dict[str, float]]:
    def weigh(market: xsb.Market, members: Sequence[str], decision: date, signal: str, seed: str | None = None):
        return trader.decide(decision)
    return weigh


def decision_days() -> list[date]:
    """The decisions ``simulate_held`` asks for over :data:`SPAN` that can hold a pair (from the span's first day)."""
    out, day = [], SPAN[0]
    while day < SPAN[1]:
        out.append(day)
        day += timedelta(days=1)
    return out


# --- 4.1 counts (no return) ---------------------------------------------------------------------------------------

def counts(market: xsb.Market, monthly: Mapping[date, Sequence[str]], table: Mapping[date, Sequence[Pair]]) -> dict[str, Any]:
    chosen = {m: nearest(pairs) for m, pairs in table.items()}
    trader = Trader(market, chosen)
    days = decision_days()
    empty = naked = 0
    for day in days:
        before = set(trader.open)
        trader.decide(day)
        if not trader.open:
            empty += 1
        entry = day + timedelta(days=1)
        for key in set(trader.open) - before:
            if any((market.bars.get(s) or {}).get(entry) is None for s in key):
                naked += 1
    opens = [e for e in trader.events if e["reason"] == "open"]
    closes = [e for e in trader.events if e["reason"] != "open"]
    years = sorted({d.year for d in days})
    by_year: dict[str, Any] = {}
    for y in years:
        o = sum(1 for e in opens if e["opened"].year == y)
        held = [e["held"] for e in closes if e["opened"].year == y]
        paper = o * 4 * COST_UNIT * LEG * 100
        by_year[str(y)] = {"opens": o, "mean_held_decisions": round(statistics.fmean(held), 2) if held else None,
                           "paper_cost_pct": round(paper, 3),
                           "paper_cost_total_position_pct": round(paper / bs.POSITION_TOTAL, 3)}
    reasons: dict[str, int] = {}
    for e in closes:
        reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
    months = {}
    shared = []
    for m, pairs in sorted(chosen.items()):
        coins: dict[str, int] = {}
        for p in pairs:
            coins[p.a] = coins.get(p.a, 0) + 1
            coins[p.b] = coins.get(p.b, 0) + 1
        share = sum(1 for c in coins.values() if c >= 2) / len(coins) if coins else None
        if share is not None:
            shared.append(share)
        months[m.isoformat()] = {"members": len(monthly.get(m, ())), "eligible_pairs": len(table[m]),
                                 "listed": len(pairs), "coins_in_two_or_more_share": round(share, 3) if share is not None else None}
    short = [y for y in COUNT_YEARS if by_year.get(str(y), {}).get("opens", 0) < MIN_OPENS]
    return {"years": by_year, "close_reasons": reasons, "still_open_at_end": len(trader.open),
            "decisions": len(days), "no_open_pair_share": round(empty / len(days), 4),
            "naked_entries": naked, "mean_coins_in_two_or_more_share": round(statistics.fmean(shared), 3) if shared else None,
            "months": months,
            "stop": {"rule": f"fewer than {MIN_OPENS} opens in any of {list(COUNT_YEARS)}", "short_years": short,
                     "stops": bool(short)}}


# --- 4.2 null, 4.3 plant, 4.4 book --------------------------------------------------------------------------------

def book(market: xsb.Market, chosen: Mapping[date, Sequence[Pair]]) -> dict[str, Any]:
    report = sl.measure(market, weigher(Trader(market, chosen)), SPAN, simulate=sl.simulate_held, cadence=SIGNAL)
    report["later_years"] = sl.later_years(report["years_total_position_pct"])
    return report


NULL_KEYS = ("annual_sharpe", "total_position_annual_pct", "years_total_position_pct", "clears_entry", "months")


def null_books(market: xsb.Market, table: Mapping[date, Sequence[Pair]], seeds: range) -> dict[str, Any]:
    out = {}
    for k in seeds:
        seed = f"{NULL_SEED}-{k}"
        chosen = {m: random_pairs(pairs, seed, m) for m, pairs in table.items()}
        report = book(market, chosen)
        out[str(k)] = {key: report[key] for key in NULL_KEYS}
    return out


def null_p(sharpe: float | None, nulls: Sequence[float | None]) -> float | None:
    """Section 4.4 ②(다): (1 + nulls at or above) / (nulls + 1). A null book without a Sharpe is never at or above
    and stays in the count; a book without one has no p."""
    if sharpe is None:
        return None
    return (1 + sum(1 for v in nulls if v is not None and v >= sharpe)) / (len(nulls) + 1)


def plant_candidates(monthly: Mapping[date, Sequence[str]]) -> list[str]:
    seen: dict[str, int] = {}
    for m, members in monthly.items():
        if SPAN[0] <= m <= SPAN[1]:
            for s in set(members):
                seen[s] = seen.get(s, 0) + 1
    return sorted(s for s, n in seen.items() if n >= PLANT_MIN_MONTHS)


def planted_pairs(candidates: Sequence[str], k: int) -> list[Key]:
    """Seed ``k``'s three disjoint pairs, fixed for the whole span (the same for every half-life)."""
    order = sorted(candidates, key=lambda s: xsb.random_key(f"{PLANT_SEED}-{k}", SPAN[0], s))[:2 * PLANT_PAIRS]
    if len(order) < 2 * PLANT_PAIRS:
        raise ValueError("too few coins stay in the tier to plant three disjoint pairs")
    return [(order[2 * i], order[2 * i + 1]) for i in range(PLANT_PAIRS)]


def month_sizes(table: Mapping[date, Sequence[Pair]]) -> dict[date, float]:
    """The median formation sd of the spread over each month's eligible pairs: the planted deviation's size."""
    return {m: statistics.median(p.sd for p in pairs) for m, pairs in table.items() if pairs}


def plant_leg(market: xsb.Market, a: str, b: str, half_life: float, sizes: Mapping[date, float],
              rng: random.Random) -> tuple[dict[date, xsb.Bar], list[float]]:
    """Coin ``a`` rebuilt as ``b`` plus a reverting deviation x (AR(1), phi = 0.5 ** (1 / h), stationary sd the
    month's size). On the days both have a bar: close = K * b.close * e^x_t, open = K * b.open * e^x_(t-1), a's own
    quote volume (its tier rank stays its own). Returns the bars and the x path."""
    phi = 0.5 ** (1 / half_life)
    first_month = min(sizes)
    bars_a, bars_b = market.bars[a], market.bars[b]
    days = sorted(bars_a.keys() & bars_b.keys())
    scale = bars_a[days[0]].close / bars_b[days[0]].close
    x_prev, path, out = 0.0, [], {}
    for i, day in enumerate(days):
        month = date(day.year, day.month, 1)
        size = sizes.get(month, sizes[first_month] if month < first_month else sizes[max(sizes)])
        x = 0.0 if i == 0 else phi * x_prev + rng.gauss(0.0, 1.0) * size * math.sqrt(1 - phi * phi)
        bb = bars_b[day]
        out[day] = xsb.Bar(open=scale * bb.open * math.exp(x_prev), close=scale * bb.close * math.exp(x),
                           quote_volume=bars_a[day].quote_volume)
        path.append(x)
        x_prev = x
    return out, path


def planted_market(market: xsb.Market, pairs: Sequence[Key], half_life: float, sizes: Mapping[date, float],
                   k: int) -> xsb.Market:
    bars, funding = dict(market.bars), dict(market.funding)
    for a, b in pairs:
        rng = random.Random(f"{PLANT_SEED}-{k}-{a}|{b}")      # the same draws for every half-life
        bars[a], _ = plant_leg(market, a, b, half_life, sizes, rng)
        funding[a] = list(market.funding.get(b, []))
    return xsb.Market(bars=bars, funding=funding, contract_types=market.contract_types)


def plant_books(market: xsb.Market, real_table: Mapping[date, Sequence[Pair]], monthly: Mapping[date, Sequence[str]],
                half_life: float, seeds: range, nulls: Sequence[float | None]) -> dict[str, Any]:
    sizes = month_sizes(real_table)
    candidates = plant_candidates(monthly)
    out = {}
    for k in seeds:
        pairs = planted_pairs(candidates, k)
        planted = planted_market(market, pairs, half_life, sizes, k)
        p_monthly = lab.tier_members(planted, SPAN)[TIER]
        chosen = {m: nearest(ps) for m, ps in formations(planted, p_monthly).items()}
        listed = [sum(1 for m in chosen if any(p.key == key for p in chosen[m])) / len(chosen) for key in pairs]
        report = book(planted, chosen)
        p = null_p(report["annual_sharpe"], nulls)
        out[str(k)] = {"pairs": [f"{a}|{b}" for a, b in pairs], "listed_month_share": [round(v, 3) for v in listed],
                       **{key: report[key] for key in NULL_KEYS},
                       "p": round(p, 4) if p is not None else None, "p_passes": p is not None and p <= ALPHA}
    return out


def plant_reading(rows: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    return {"seeds": n,
            "listed_share_mean": round(statistics.fmean(v for r in rows.values() for v in r["listed_month_share"]), 3) if n else None,
            "p_passes_share": round(sum(1 for r in rows.values() if r["p_passes"]) / n, 3) if n else None,
            "clears_entry_share": round(sum(1 for r in rows.values() if r["clears_entry"]) / n, 3) if n else None}


def judge(report: Mapping[str, Any], nulls: Sequence[float | None]) -> dict[str, Any]:
    p = null_p(report["annual_sharpe"], nulls)
    a, b = bool(report["clears_entry"]), bool(report["later_years"]["passes"])
    c = p is not None and p <= ALPHA
    return {"entry": a, "later_years": b, "null": {"books": len(nulls), "p": round(p, 4) if p is not None else None,
                                                    "alpha": ALPHA, "passes": c},
            "candidate": a and b and c}


# --- the command --------------------------------------------------------------------------------------------------

def _seeds(text: str | None, top: int) -> range | None:
    try:
        lo, hi = (int(v) for v in (text or "").split(":"))
    except ValueError:
        return None
    return range(lo, hi) if 0 <= lo < hi <= top else None


def read_nulls(files: str) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for path in files.split(","):
        body = json.loads(Path(path).read_text(encoding="utf-8"))
        for k, row in body["books"].items():
            if k in rows:
                raise ValueError(f"null seed {k} appears twice")
            rows[k] = row
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pairs_value", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("pairs-counts", "pairs-null", "pairs-plant", "pairs-book"))
    parser.add_argument("--seeds", default=None, help="pairs-null: A:B of 0..600; pairs-plant: A:B of 0..50")
    parser.add_argument("--half-life", type=float, choices=PLANT_HALF_LIVES, default=None, help="pairs-plant")
    parser.add_argument("--null", default=None, help="pairs-plant, pairs-book: the null chunk files, comma-separated")
    parser.add_argument("--counts", type=Path, default=None, help="pairs-book: the recorded counts (stop rule not held)")
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    seeds = None
    if args.command in ("pairs-null", "pairs-plant"):
        seeds = _seeds(args.seeds, NULLS if args.command == "pairs-null" else PLANT_SEEDS)
        if seeds is None:
            print(f"refused: {args.command} needs --seeds A:B inside its range", file=sys.stderr)
            return EXIT_USAGE
    nulls: list[float | None] = []
    if args.command in ("pairs-plant", "pairs-book"):
        if args.command == "pairs-plant" and args.half_life is None:
            print("refused: pairs-plant needs --half-life 2|5|10", file=sys.stderr)
            return EXIT_USAGE
        if args.null is None:
            print(f"refused: {args.command} needs --null (all {NULLS} null books)", file=sys.stderr)
            return EXIT_USAGE
        rows = read_nulls(args.null)
        if sorted(rows, key=int) != [str(k) for k in range(NULLS)]:
            print(f"refused: the null files do not hold seeds 0..{NULLS - 1} exactly", file=sys.stderr)
            return EXIT_USAGE
        nulls = [rows[str(k)]["annual_sharpe"] for k in range(NULLS)]
    if args.command == "pairs-book":
        if args.counts is None:
            print("refused: pairs-book needs --counts (section 4.1 first)", file=sys.stderr)
            return EXIT_USAGE
        if json.loads(args.counts.read_text(encoding="utf-8"))["stop"]["stops"] is not False:
            print("refused: the counts hold the stop rule: L26 is closed, no book is measured", file=sys.stderr)
            return EXIT_BLOCKED
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    market = lab.hold(cache, attach=False)
    if market is None:
        print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
        return EXIT_BLOCKED
    monthly = lab.tier_members(market, SPAN)[TIER]
    table = formations(market, monthly)
    head = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
            "span": [d.isoformat() for d in SPAN], "last_day_read": lab.last_day(market).isoformat(),
            "command": args.command}
    if args.command == "pairs-counts":
        body: dict[str, Any] = {"reads_returns": False, **counts(market, monthly, table)}
    elif args.command == "pairs-null":
        body = {"reads_returns": True, "seeds": [seeds.start, seeds.stop], "books": null_books(market, table, seeds)}
    elif args.command == "pairs-plant":
        rows = plant_books(market, table, monthly, args.half_life, seeds, nulls)
        body = {"reads_returns": True, "half_life": args.half_life, "seeds": [seeds.start, seeds.stop],
                "books": rows, "reading": plant_reading(rows)}
    else:
        report = book(market, {m: nearest(pairs) for m, pairs in table.items()})
        body = {"reads_returns": True, "book": report, "judgement": judge(report, nulls)}
    print(json.dumps({**head, **body}, indent=1, sort_keys=True, ensure_ascii=False, default=str))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
