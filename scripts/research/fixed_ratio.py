"""L32, the fixed reward-to-risk trade book: single-coin trades with a stop and a target at a fixed ratio.

``docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md`` sections 126 (design) and 127 (registration; Thomas 2026-11-22
"기본값대로"). Thomas's lines: at 1:3 a win rate above 33%, at 1:5 above 20%. Fixing the ratio earns nothing by
itself: an entry with no information wins about ``1 / (1 + ratio)`` of the time, which is the break-even rate, so
the book is judged against entries that keep everything but their timing.

- **A trade.** The signal is read at day ``t``'s close and entered at the next day's open. The stop is
  :data:`STOP_ATR` x ATR(:data:`ATR_DAYS`) from the entry, the target :data:`RATIOS` times that distance, the limit
  :data:`HOLD_DAYS` days. Daily highs and lows decide which is touched first; a day that touches both counts as the
  stop, an open beyond the stop exits at the open, and an open beyond the target is paid the target only.
- **The entry family, one:** a close above the highest of the :data:`BREAKOUT_DAYS` closes before it is a long, a
  close below the lowest a short (:func:`signals`).
- **The book.** Every trade risks :data:`RISK` of the budget (notional = risk / stop distance, at most one
  :data:`MAX_OPEN`-th of the budget), at most :data:`MAX_OPEN` open, the widest breakout first; a coin with an open
  trade takes no new one. Returns are on a budget of 1, idle days included.
- **The null** moves every signal the same number of calendar days round the span, one draw a book
  (:func:`shifted`): sides, exits, sizing and which signals arrive together stay, the timing goes. **The plant** thins a null's non-winning entries until its win rate is the size
  asked for (:func:`planted`), Thomas's lines among the sizes: can this door see them?
- **Order, fixed before anything is read:** counts (no profit or loss) -> the nulls -> the planted books -> the real
  book once. Each stage refuses without the one before it. The pass line is the ladder's alone; a win rate is
  written down and judges nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import basket_search as bs  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import signal_lab as lab  # noqa: E402
from scripts.research import strategy_ladder as sl  # noqa: E402

SPEC = "STRATEGY_DISCOVERY_LOOP_V0.1 section 127 (L32)"
SPAN = bs.DEV                               # 2020-01-01 .. 2024-12-31; nothing later is read
TIERS = ("top30", "rank31_100")
RATIOS = (3, 5)
CELLS = len(TIERS) * len(RATIOS)            # the divisor of the null door
BREAKOUT_DAYS = 20
ATR_DAYS = 14
STOP_ATR = 2.5
HOLD_DAYS = 30
RISK = 0.01                                 # of the budget, lost at the stop
MAX_OPEN = 6
# a side's cost in bps: the engine's for the top thirty, the stressed-coin slippage for the next seventy
COST_BPS = {"top30": xsb.cost.DEFAULT_TAKER_FEE_BPS + xsb.cost.DEFAULT_SLIPPAGE_BPS,
            "rank31_100": xsb.cost.DEFAULT_TAKER_FEE_BPS + sl.LISTING_SLIPPAGE_BPS}
MIN_SHIFT = 30                              # days a null's signals move at least, where a coin has the days
NULLS = 300
PLANT_SEEDS = 100
PLANT_SEED_BASE = 10_000                    # the planted books' shifts are none of the nulls'
LIFTS = (0.0, 0.03, 0.05, 0.08)             # added to the shifted book's own win rate
THOMAS_LINE = {3: 1 / 3, 5: 0.20}           # the win rates Thomas named, planted as they are
MIN_YEAR_TRADES = 30                        # in each of 2022-2024, or the cell is not measured
MAX_TIE_SHARE = 0.05                        # trades touching stop and target on one day; over it daily bars cannot judge
ONE_DAY = timedelta(days=1)

Signal = tuple[date, str, int, float]       # the signal day, the coin, the side (+1 long, -1 short), the breakout width in ATR


# --- one trade ---------------------------------------------------------------------------------------------------

def atr(bars: Mapping[date, xsb.Bar], day: date) -> float | None:
    """The plain mean of the true range over the :data:`ATR_DAYS` days ending at ``day``; None without every bar."""
    total = 0.0
    for k in range(ATR_DAYS):
        bar, before = bars.get(day - k * ONE_DAY), bars.get(day - (k + 1) * ONE_DAY)
        if bar is None or before is None or bar.high is None or bar.low is None:
            return None
        total += max(bar.high - bar.low, abs(bar.high - before.close), abs(bar.low - before.close))
    return total / ATR_DAYS


def eligible(bars: Mapping[date, xsb.Bar], day: date) -> bool:
    """A day a signal can be read and entered on: every bar of the breakout window, the day itself and the next."""
    return all(day - k * ONE_DAY in bars for k in range(BREAKOUT_DAYS + 1)) and day + ONE_DAY in bars


def breakout(bars: Mapping[date, xsb.Bar], day: date) -> tuple[int, float] | None:
    """``(side, width in ATR)`` when the close is past every one of the :data:`BREAKOUT_DAYS` closes before it."""
    if not eligible(bars, day):
        return None
    close, size = bars[day].close, atr(bars, day)
    before = [bars[day - k * ONE_DAY].close for k in range(1, BREAKOUT_DAYS + 1)]
    if not size or size <= 0:
        return None
    if close > max(before):
        return 1, (close - max(before)) / size
    if close < min(before):
        return -1, (min(before) - close) / size
    return None


def trade(bars: Mapping[date, xsb.Bar], day: date, side: int, ratio: int) -> dict[str, Any] | None:
    """The trade a signal at ``day``'s close opens, to its exit; None when it cannot be opened (no next bar, no
    range, or a long whose stop would sit at or under zero).

    ``outcome`` is ``win`` (the target), ``loss`` (the stop) or ``time`` (the limit, or the coin's last bar)."""
    size = atr(bars, day)
    first = bars.get(day + ONE_DAY)
    if first is None or not size or size <= 0:
        return None
    entry, distance = first.open, STOP_ATR * size
    stop, target = entry - side * distance, entry + side * ratio * distance
    if side == 1 and stop <= 0:
        return None
    unreachable = side == -1 and target <= 0
    out = {"side": side, "entry_day": day + ONE_DAY, "entry": entry, "stop_frac": distance / entry,
           "tie": False, "gap": False, "unreachable": unreachable}
    last_day, last_close = day + ONE_DAY, entry
    for held in range(1, HOLD_DAYS + 1):
        now = day + held * ONE_DAY
        bar = bars.get(now)
        if bar is None:                      # the coin's bars ran out: out at the last close seen
            return {**out, "exit_day": last_day, "exit": last_close, "outcome": "time"}
        beyond_stop = (bar.open - stop) * side <= 0
        beyond_target = not unreachable and (bar.open - target) * side >= 0
        if held > 1 and beyond_stop:
            return {**out, "exit_day": now, "exit": bar.open, "outcome": "loss", "gap": True}
        if held > 1 and beyond_target:
            return {**out, "exit_day": now, "exit": target, "outcome": "win"}
        low, high = (bar.low, bar.high) if side == 1 else (bar.high, bar.low)       # toward the stop, toward the target
        at_stop = (low - stop) * side <= 0
        at_target = not unreachable and (high - target) * side >= 0
        if at_stop:
            return {**out, "exit_day": now, "exit": stop, "outcome": "loss", "tie": at_target}
        if at_target:
            return {**out, "exit_day": now, "exit": target, "outcome": "win"}
        last_day, last_close = now, bar.close
    return {**out, "exit_day": last_day, "exit": last_close, "outcome": "time"}


def r_multiple(made: Mapping[str, Any]) -> float:
    """The trade's price result in units of its stop distance, before cost and funding."""
    return made["side"] * (made["exit"] - made["entry"]) / (made["stop_frac"] * made["entry"])


# --- the signals -------------------------------------------------------------------------------------------------

def member_days(market: xsb.Market, tier: str, span: tuple[date, date] = SPAN) -> dict[str, list[date]]:
    """Per coin, the days of the span it is in the tier (the month's members) and a signal can be entered on."""
    out: dict[str, list[date]] = {}
    for month, members in sorted(lab.tier_members(market, span)[tier].items()):
        day = max(month, span[0])
        following = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
        while day < following and day + ONE_DAY <= span[1]:
            for symbol in members:
                if eligible(market.bars.get(symbol, {}), day):
                    out.setdefault(symbol, []).append(day)
            day += ONE_DAY
    return out


def signals(market: xsb.Market, days: Mapping[str, Sequence[date]]) -> list[Signal]:
    """Every breakout on the coins' member days, oldest first."""
    found = [(day, symbol, *hit) for symbol, own in days.items() for day in own
             if (hit := breakout(market.bars[symbol], day)) is not None]
    return sorted(found)


def places(days: Mapping[str, Sequence[date]]) -> dict[str, dict[date, int]]:
    """Per coin, its member days as a lookup (what :func:`shifted` asks of a moved day)."""
    return {symbol: {day: k for k, day in enumerate(own)} for symbol, own in days.items()}


def shifted(real: Sequence[Signal], days: Mapping[str, Sequence[date]], seed: int, tier: str,
            where: Mapping[str, Mapping[date, int]] | None = None, span: tuple[date, date] = SPAN) -> list[Signal]:
    """The null's signals: every signal moved the SAME number of calendar days round the span, one draw a book, at
    least :data:`MIN_SHIFT` days either way. Sides and widths stay, and so does which signals arrive together: the
    book holds six places for thousands of signals, and breakouts come in one-sided clusters, so a draw per coin
    would scatter them into a different book. A signal that lands on a day its coin is no member is dropped."""
    where = places(days) if where is None else where
    length = (span[1] - span[0]).days + 1
    step = random.Random(f"L32|{tier}|{seed}").randint(MIN_SHIFT, length - MIN_SHIFT)
    moved = ((span[0] + timedelta(days=((day - span[0]).days + step) % length), symbol, side, width)
             for day, symbol, side, width in real)
    return sorted(entry for entry in moved if entry[0] in where[entry[1]])


def ending(market: xsb.Market, signal: Signal, ratio: int, known: dict[tuple, str | None]) -> str | None:
    """How the trade this signal would open ends on its own (``win``, ``loss``, ``time``; None when it cannot be
    opened), kept in ``known``: the planted books ask it of the same coin-days again and again."""
    key = (signal[1], signal[0], signal[2], ratio)
    if key not in known:
        made = trade(market.bars[signal[1]], signal[0], signal[2], ratio)
        known[key] = None if made is None else made["outcome"]
    return known[key]


def planted(market: xsb.Market, moved: Sequence[Signal], ratio: int, seed: int, *, lift: float | None = None,
            target: float | None = None, known: dict[tuple, str | None] | None = None,
            ) -> tuple[list[Signal], dict[str, Any]]:
    """A null's signals with non-winning entries dropped until the win rate of what is left is the size asked for:
    the null's own rate plus ``lift``, or ``target`` as it is. ``lift`` 0 keeps every signal. The second value says
    what was built; ``built`` is False when the null already wins at the asked rate or more."""
    known = {} if known is None else known
    made = [(s, {"outcome": end}) for s in moved if (end := ending(market, s, ratio, known)) is not None]
    wins = sum(1 for _, m in made if m["outcome"] == "win")
    rest = len(made) - wins
    own = wins / len(made) if made else 0.0
    asked = own + lift if target is None else target
    note = {"null_win_rate": round(own, 4), "asked_win_rate": round(asked, 4), "built": True}
    if not made or (target is None and not lift):
        return [s for s, _ in made], note
    if asked <= own or not wins or asked >= 1:
        return [s for s, _ in made], {**note, "built": asked == own}
    keep = wins * (1 - asked) / (rest * asked)
    draw = random.Random(f"L32|plant|{seed}|{ratio}|{asked:.6f}")
    return [s for s, m in made if m["outcome"] == "win" or draw.random() < keep], note


# --- the book ----------------------------------------------------------------------------------------------------

def book(market: xsb.Market, entries: Iterable[Signal], tier: str, ratio: int) -> dict[str, Any]:
    """The book ``entries`` build: the trades taken, what was passed over, and the day's net on a budget of 1 (as a
    fraction). A signal read at day ``t`` is taken at ``t + 1``'s open if the coin has no open trade and fewer than
    :data:`MAX_OPEN` are open; a trade that exits on day ``t`` frees its place for the signals of ``t``."""
    by_day: dict[date, list[Signal]] = {}
    for entry in entries:
        by_day.setdefault(entry[0], []).append(entry)
    cost = COST_BPS[tier] / 10_000
    held: dict[str, date] = {}               # coin -> the day its open trade exits
    taken: list[dict[str, Any]] = []
    net: dict[date, float] = {}
    passed = {"coin_open": 0, "book_full": 0, "not_openable": 0}
    open_counts: list[int] = []
    for day in sorted(by_day):
        held = {s: until for s, until in held.items() if until > day}
        for _, symbol, side, width in sorted(by_day[day], key=lambda e: (-e[3], e[1])):
            if symbol in held:
                passed["coin_open"] += 1
                continue
            if len(held) >= MAX_OPEN:
                passed["book_full"] += 1
                continue
            made = trade(market.bars[symbol], day, side, ratio)
            if made is None:
                passed["not_openable"] += 1
                continue
            notional = min(RISK / made["stop_frac"], 1 / MAX_OPEN)
            held[symbol] = made["exit_day"]
            open_counts.append(len(held))
            funding = market.funding_day(symbol) or {}
            bars, reference, paid = market.bars[symbol], made["entry"], 0.0
            now = made["entry_day"]
            while now <= made["exit_day"]:
                price = made["exit"] if now == made["exit_day"] else bars[now].close
                rate = funding.get(now, 0.0)
                net[now] = net.get(now, 0.0) + notional * (side * (price - reference) / made["entry"] - side * rate)
                paid += side * rate
                reference = price
                now += ONE_DAY
            net[made["entry_day"]] = net.get(made["entry_day"], 0.0) - notional * cost
            net[made["exit_day"]] = net.get(made["exit_day"], 0.0) - notional * cost
            taken.append({**made, "symbol": symbol, "signal_day": day, "notional": notional,
                          "funding_r": -paid / made["stop_frac"], "cost_r": 2 * cost / made["stop_frac"]})
    return {"trades": taken, "net": net, "passed_over": passed,
            "mean_open_at_entry": round(statistics.fmean(open_counts), 3) if open_counts else None}


def counted(made: Mapping[str, Any]) -> dict[str, Any]:
    """Section 126.4 step 1: how many trades, where and when, and how they ended on the calendar. No profit or loss:
    neither a win count nor an R nor a net."""
    trades = made["trades"]
    years = sorted({t["entry_day"].year for t in trades})
    by_year = {str(y): {"trades": sum(1 for t in trades if t["entry_day"].year == y),
                        "long": sum(1 for t in trades if t["entry_day"].year == y and t["side"] == 1),
                        "short": sum(1 for t in trades if t["entry_day"].year == y and t["side"] == -1)} for y in years}
    n = len(trades)
    thin = [str(y) for y in sl.LATER_YEARS if by_year.get(str(y), {}).get("trades", 0) < MIN_YEAR_TRADES]
    tie = sum(1 for t in trades if t["tie"]) / n if n else None
    return {"trades": n, "by_year": by_year, "passed_over": made["passed_over"],
            "mean_open_at_entry": made["mean_open_at_entry"],
            "tie_share": round(tie, 4) if tie is not None else None,
            "gap_share": round(sum(1 for t in trades if t["gap"]) / n, 4) if n else None,
            "unreachable_target_share": round(sum(1 for t in trades if t["unreachable"]) / n, 4) if n else None,
            "median_stop_pct": round(statistics.median(t["stop_frac"] for t in trades) * 100, 2) if n else None,
            "capped_notional_share": round(sum(1 for t in trades if t["notional"] < RISK / t["stop_frac"]) / n, 4) if n else None,
            "median_days_held": statistics.median((t["exit_day"] - t["entry_day"]).days + 1 for t in trades) if n else None,
            "stop": {"thin_years": thin, "too_many_ties": tie is not None and tie > MAX_TIE_SHARE,
                     "stops": bool(thin) or n == 0 or (tie is not None and tie > MAX_TIE_SHARE)}}


def measured(made: Mapping[str, Any], span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """The book's numbers on a budget of 1: the ladder's annual Sharpe (monthly sums) and yearly rate, the calendar
    years, and the trade tally in R. The win rate is the targets reached over every trade; a time-out is no win."""
    trades = made["trades"]
    daily = {day: {"net": value * 100} for day, value in made["net"].items() if span[0] <= day <= span[1]}
    sharpe, months = bs.annual_sharpe(daily, *span)
    total = sum(months)
    years: dict[str, float] = {str(y): 0.0 for y in range(span[0].year, span[1].year + 1)}
    for day, row in daily.items():
        years[str(day.year)] = years.get(str(day.year), 0.0) + row["net"]
    n = len(trades)
    ends = {name: sum(1 for t in trades if t["outcome"] == name) for name in ("win", "loss", "time")}
    out: dict[str, Any] = {"annual_sharpe": sharpe, "sum_pct": round(total, 3), "months": len(months),
                           "annual_pct": round(total / (len(months) / 12), 2) if months else None,
                           "years_pct": {y: round(v, 2) for y, v in sorted(years.items())}, "trades": n, "ends": ends,
                           "long_share": round(sum(1 for t in trades if t["side"] == 1) / n, 4) if n else None,
                           "mean_open_at_entry": made["mean_open_at_entry"]}
    if n:
        price = [r_multiple(t) for t in trades]
        timed = [r_multiple(t) for t in trades if t["outcome"] == "time"]
        out.update({"win_rate": round(ends["win"] / n, 4), "time_share": round(ends["time"] / n, 4),
                    "mean_price_r": round(statistics.fmean(price), 4),
                    "mean_cost_r": round(statistics.fmean(t["cost_r"] for t in trades), 4),
                    "mean_funding_r": round(statistics.fmean(t["funding_r"] for t in trades), 4),
                    "mean_time_out_r": round(statistics.fmean(timed), 4) if timed else None,
                    "by_side": {name: {"trades": len(part), "win_rate": round(sum(1 for t in part if t["outcome"] == "win") / len(part), 4)}
                                for name, side in (("long", 1), ("short", -1))
                                if (part := [t for t in trades if t["side"] == side])}})
        out["mean_net_r"] = round(out["mean_price_r"] - out["mean_cost_r"] + out["mean_funding_r"], 4)
    return out


# --- the doors ---------------------------------------------------------------------------------------------------

def line(null_annual: Sequence[float]) -> dict[str, Any]:
    """Section 94.4's rule for a book that carries the market's drift: the yearly rate the real book must reach is
    the entry rule's or the nulls' 95th percentile, whichever is higher; a 95th percentile at
    :data:`strategy_ladder.L6_UNUSABLE_PCT` cannot tell the book from the drift."""
    kept = sorted(null_annual)
    p95 = kept[int(0.95 * (len(kept) - 1))] if kept else None
    return {"null_p95_annual_pct": p95, "line_annual_pct": None if p95 is None else max(bs.ENTRY_RETURN_TOTAL_PCT, p95),
            "usable": p95 is not None and p95 < sl.L6_UNUSABLE_PCT}


def seen(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The planted reading: at size 0 the seeds under p 0.05 must be chance's number
    (:data:`strategy_ladder.SENS_FALSE_ALARM`), or nothing is read; a size is seen when
    :data:`strategy_ladder.SENS_SHARE` of its seeds pass the null door at the cells' divisor."""
    by_size: dict[str, dict[str, Any]] = {}
    for row in rows:
        size = by_size.setdefault(row["size"], {"seeds": 0, "built": 0, "under_0.05": 0, "pass_door": 0})
        size["seeds"] += 1
        if row["built"]:
            size["built"] += 1
            size["under_0.05"] += row["p"] is not None and row["p"] < sl.NULL_ALPHA
            size["pass_door"] += bool(row["passes"])
    for size in by_size.values():
        size["share"] = round(size["pass_door"] / size["built"], 3) if size["built"] else None
        size["seen"] = size["built"] == size["seeds"] and size["share"] is not None and size["share"] >= sl.SENS_SHARE
    zero = by_size.get("lift_0.00", {})
    low, high = sl.SENS_FALSE_ALARM
    valid = bool(zero) and zero["built"] == PLANT_SEEDS and low <= zero["under_0.05"] <= high
    return {"sizes": by_size, "size_zero_valid": valid, "false_alarm_band": [low, high]}


def verdict(real: Mapping[str, Any], null_sharpes: Sequence[float | None], drift: Mapping[str, Any]) -> dict[str, Any]:
    """The ladder, as it stands: 2(a) annual Sharpe and the yearly rate against the drift line, 2(b) the later years,
    2(c) the nulls at the cells' divisor, 3 the five years. The win rate judges nothing."""
    if not drift["usable"]:
        return {"judged": False, "why": "the nulls' 95th percentile reaches the unusable level: the cell cannot tell "
                                        "the book from the market's drift"}
    sharpe, yearly = real["annual_sharpe"], real["annual_pct"]
    entry = sharpe is not None and sharpe >= bs.ENTRY_SHARPE and yearly is not None and yearly >= drift["line_annual_pct"]
    later = sl.later_years(real["years_pct"])
    beside = sl.beside_nulls(sharpe, null_sharpes, candidates=CELLS)
    steady = sl.stability(real["years_pct"])
    return {"judged": True, "2a_entry": entry, "2b_later_years": later, "2c_beside_nulls": beside, "3_stability": steady,
            "passes": bool(entry and later["passes"] and beside["passes"] and steady["passes"])}


# --- the command -------------------------------------------------------------------------------------------------

def _file(path: str | None) -> dict[str, Any] | None:
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def _same_cell(body: Mapping[str, Any] | None, command: str, tier: str, ratio: int) -> bool:
    return bool(body) and (body.get("command"), body.get("tier"), body.get("ratio")) == (command, tier, ratio)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fixed_ratio", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("fixed-counts", "fixed-null", "fixed-plant", "fixed-real"))
    parser.add_argument("--tier", choices=TIERS, required=True)
    parser.add_argument("--ratio", type=int, choices=RATIOS, required=True)
    parser.add_argument("--counts", default=None)
    parser.add_argument("--null", default=None)
    parser.add_argument("--plant", default=None)
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    cmd, tier, ratio = args.command, args.tier, args.ratio
    counts, null, plant = _file(args.counts), _file(args.null), _file(args.plant)
    if cmd != "fixed-counts" and not (_same_cell(counts, "fixed-counts", tier, ratio) and not counts["counts"]["stop"]["stops"]):
        print("refused: needs this cell's --counts, and one that did not stop", file=sys.stderr)
        return EXIT_USAGE
    if cmd in ("fixed-plant", "fixed-real") and not (_same_cell(null, "fixed-null", tier, ratio) and len(null["books"]) == NULLS):
        print(f"refused: needs this cell's --null with all {NULLS} books", file=sys.stderr)
        return EXIT_USAGE
    if cmd == "fixed-real" and not _same_cell(plant, "fixed-plant", tier, ratio):
        print("refused: needs this cell's --plant: the door is read before the real book", file=sys.stderr)
        return EXIT_USAGE
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    bs.check_search_span(SPAN)
    market = lab.hold(cache, SPAN, attach=False)
    if market is None:
        print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
        return EXIT_BLOCKED
    days = member_days(market, tier)
    real = signals(market, days)
    where = places(days)
    head = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC, "command": cmd, "tier": tier,
            "ratio": ratio, "span": [d.isoformat() for d in SPAN], "last_day_read": lab.last_day(market).isoformat(),
            "signals": {"all": len(real), "long": sum(1 for s in real if s[2] == 1), "coins": len(days)}}
    if cmd == "fixed-counts":
        body: dict[str, Any] = {"reads_profit_or_loss": False, "counts": counted(book(market, real, tier, ratio))}
    elif cmd == "fixed-null":
        books = []
        for seed in range(NULLS):
            moved = shifted(real, days, seed, tier, where)
            made = measured(book(market, moved, tier, ratio))
            books.append({"seed": seed, "signals_kept": len(moved),
                          **{k: made.get(k) for k in ("annual_sharpe", "annual_pct", "trades", "long_share",
                                                      "mean_open_at_entry", "win_rate", "time_share", "mean_price_r",
                                                      "mean_cost_r", "mean_funding_r", "mean_net_r")}})
        body = {"reads_profit_or_loss": True, "reads_real_entries": False, "books": books,
                "drift": line([b["annual_pct"] for b in books if b["annual_pct"] is not None]),
                "break_even_before_cost": round(1 / (1 + ratio), 4)}
    elif cmd == "fixed-plant":
        sharpes = [b["annual_sharpe"] for b in null["books"]]
        sizes: list[tuple[str, dict[str, Any]]] = [(f"lift_{x:.2f}", {"lift": x}) for x in LIFTS]
        sizes.append(("thomas_line", {"target": THOMAS_LINE[ratio]}))
        rows = []
        known: dict[tuple, str | None] = {}
        for k in range(PLANT_SEEDS):
            seed = PLANT_SEED_BASE + k
            moved = shifted(real, days, seed, tier, where)
            for name, how in sizes:
                entries, note = planted(market, moved, ratio, seed, known=known, **how)
                made = measured(book(market, entries, tier, ratio)) if note["built"] else {}
                beside = sl.beside_nulls(made.get("annual_sharpe"), sharpes, candidates=CELLS)
                rows.append({"size": name, "seed": seed, **note, "win_rate": made.get("win_rate"),
                             "annual_sharpe": made.get("annual_sharpe"), "p": beside["p"], "passes": beside["passes"]})
        body = {"reads_profit_or_loss": True, "reads_real_entries": False, "rows": rows, "seen": seen(rows)}
    else:
        drift = line([b["annual_pct"] for b in null["books"] if b["annual_pct"] is not None])
        if not drift["usable"]:
            print("BLOCKED: the nulls' 95th percentile reaches the unusable level: this cell cannot tell a book from the "
                  "market's drift, and its real book is not read (section 126.4 step 3)", file=sys.stderr)
            return EXIT_BLOCKED
        if not plant["seen"]["size_zero_valid"]:
            print("BLOCKED: the planted books at size zero are not chance's number: the plant is broken, and nothing "
                  "is read (section 126.4 step 4)", file=sys.stderr)
            return EXIT_BLOCKED
        made = measured(book(market, real, tier, ratio))
        body = {"reads_profit_or_loss": True, "reads_real_entries": True, "book": made, "drift": drift,
                "null_win_rate_median": statistics.median(b["win_rate"] for b in null["books"] if b["win_rate"] is not None),
                "break_even_before_cost": round(1 / (1 + ratio), 4), "thomas_line": round(THOMAS_LINE[ratio], 4),
                "door_sees_thomas_line": bool(plant["seen"]["size_zero_valid"]
                                              and plant["seen"]["sizes"].get("thomas_line", {}).get("seen")),
                "verdict": verdict(made, [b["annual_sharpe"] for b in null["books"]], drift)}
    print(json.dumps({**head, **body}, indent=1, sort_keys=True, ensure_ascii=False, default=str))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
