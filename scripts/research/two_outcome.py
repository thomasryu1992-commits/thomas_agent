"""L33, the two-outcome trade book: L32's entries with a stop narrow enough that the stop and the target both bind.

``docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md`` sections 130 (design) and 131 (registration; Thomas 2026-11-22
"기본값대로"). L32's stop sat about a fifth of the price away and half its trades ended at the 30-day limit, so it
never tested "at 1:3 find a win rate above 33%". Here the stop is :data:`STOP_ATR` of the daily ATR, about 4% of the
price, which a daily bar cannot judge: the first touch is read on HOURLY bars.

- **A trade.** The signal is L32's (:func:`fixed_ratio.breakout`), read at the daily close and entered at that
  instant's hourly open. Stop :data:`STOP_ATR` x daily ATR(14) from the entry, target :data:`RATIOS` times that, a
  :data:`HOLD_HOURS`-hour limit as a back door. An hour touching both is the stop; an open beyond the stop exits
  at the open; an open beyond the target is paid the target only; a path whose hourly bars run out ends at its
  last close and counts as a time-out. No hour after the span is read.
- **The table.** One coin's hourly bars at a time (all of them would not fit the sandbox), every member day and
  both sides: how the trade a signal there would open ends (:func:`table`). Every book is a selection from it.
- **The book.** :data:`RISK` of the budget a trade, at most :data:`MAX_OPEN` open, the widest breakout first; the
  result is booked on the day the trade ends.
- **The null** moves every signal the same number of days round its own calendar year, one draw a book, and a
  signal that lands where its coin is no member goes to a member of that day with no signal (:func:`shifted`): the
  count, the sides, each year's share and the days signals arrive together stay. Before any profit or loss is read the nulls must take about the
  trades the real book takes (:func:`structure`).
- **Order, fixed before anything is read:** counts -> structure -> nulls -> planted books -> the real book once.
  It is a second look at L32's entries on L32's span, so the null door's divisor counts L32's cells too.
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import random
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import basket_search as bs  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import fixed_ratio as fr  # noqa: E402
from scripts.research import signal_lab as lab  # noqa: E402
from scripts.research import strategy_ladder as sl  # noqa: E402

SPEC = "STRATEGY_DISCOVERY_LOOP_V0.1 section 131 (L33)"
SPAN = bs.DEV                               # 2020-01-01 .. 2024-12-31; no hour after it is read
TIERS = fr.TIERS
RATIOS = (3, 5)
DIVISOR = 8                                 # L33's four cells and L32's four: the same entries on the same span
HOURLY_KIND = "klines-1h"
STOP_ATR = 0.5                              # of the daily ATR(14)
HOLD_HOURS = 30 * 24
RISK = 0.0025                               # of the budget, lost at the stop
MAX_OPEN = fr.MAX_OPEN
COST_BPS = fr.COST_BPS
MIN_SHIFT = fr.MIN_SHIFT
NULLS = 600
PLANT_SEEDS = 100
PLANT_SEED_BASE = 10_000
LIFTS = (0.0, 0.03, 0.05, 0.08, 0.12, 0.16, 0.20)      # added to the shifted signals' own win rate
THOMAS_LINE = fr.THOMAS_LINE
MIN_YEAR_TRADES = fr.MIN_YEAR_TRADES
MAX_TIE_SHARE = 0.05                        # trades touching stop and target inside one hour
MAX_TIME_SHARE = 0.05                       # trades ending at the limit: over it the cell is not the geometry asked for
STRUCTURE_BAND = {"trades": 0.10, "long_share": 0.05, "mean_open_at_entry": 0.10}   # relative, absolute, relative
OPEN_TOLERANCE = 1e-9
ONE_DAY = fr.ONE_DAY
HOUR_MS = 3_600_000

Signal = fr.Signal
Hours = dict[int, tuple[float, float, float, float]]        # hours since the epoch -> open, high, low, close


def hour_of(day: date) -> int:
    """The hour index of ``day``'s 00:00 UTC."""
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()) // 3600


# --- the hourly bars ---------------------------------------------------------------------------------------------

def read_hours(folder: Path, last: date = SPAN[1]) -> Hours:
    """One coin's hourly bars from its monthly zips, nothing after ``last``'s final hour: neither a later month's
    file nor a later row is read."""
    end = hour_of(last + ONE_DAY)
    out: Hours = {}
    for path in sorted(folder.glob("*.zip")) if folder.exists() else []:
        if path.name[-11:-4] > last.strftime("%Y-%m"):
            continue
        for row in xsb._rows(path):
            try:
                stamp = float(row[0])
                hour = int((stamp / 1000 if stamp > 1e14 else stamp) // HOUR_MS)
                bar = (float(row[1]), float(row[2]), float(row[3]), float(row[4]))
            except (ValueError, IndexError):
                continue          # the header row
            if hour < end and bar[0] > 0 and bar[3] > 0:
                out[hour] = bar
    return out


def stamps(rows: Sequence[tuple[int, float]]) -> tuple[list[int], list[float]]:
    """A coin's funding settlements as hour indices and a running sum of the rates."""
    hours, sums, total = [], [0.0], 0.0
    for t, rate in rows:
        hours.append(int((t / 1000 if t > 1e14 else t) // HOUR_MS))
        total += rate
        sums.append(total)
    return hours, sums


def paid(funding: tuple[list[int], list[float]], first: int, last: int) -> float:
    """The rates settled from hour ``first`` through hour ``last``, both included."""
    hours, sums = funding
    return sums[bisect.bisect_right(hours, last)] - sums[bisect.bisect_left(hours, first)]


# --- one trade ---------------------------------------------------------------------------------------------------

def trade(hours: Hours, bars: Mapping[date, xsb.Bar], day: date, side: int, ratio: int,
          funding: tuple[list[int], list[float]] = ([], [0.0])) -> dict[str, Any] | None:
    """The trade a signal at ``day``'s close opens, to its end on the hourly bars; None when it cannot be opened
    (no daily range, no hourly bar at the entry, or a long whose stop would sit at or under zero).

    ``outcome`` is ``win`` (the target), ``loss`` (the stop) or ``time`` (the limit, or the hourly bars ran out:
    ``broken``). ``open_differs`` says the hourly open is not the daily open of the entry day."""
    size = fr.atr(bars, day)
    start = hour_of(day + ONE_DAY)
    first, daily = hours.get(start), bars.get(day + ONE_DAY)
    if first is None or daily is None or not size or size <= 0:
        return None
    entry, distance = first[0], STOP_ATR * size
    stop, target = entry - side * distance, entry + side * ratio * distance
    if side == 1 and stop <= 0:
        return None
    unreachable = side == -1 and target <= 0
    out = {"side": side, "entry_day": day + ONE_DAY, "entry": entry, "stop_frac": distance / entry, "tie": False,
           "gap": False, "unreachable": unreachable, "broken": False,
           "open_differs": abs(entry - daily.open) > OPEN_TOLERANCE * entry}

    def ended(hour: int, price: float, outcome: str, **more: Any) -> dict[str, Any]:
        return {**out, **more, "exit_day": date(1970, 1, 1) + timedelta(days=hour // 24), "exit": price,
                "outcome": outcome, "hours_held": hour - start + 1, "funding": side * paid(funding, start, hour)}

    last_close = entry
    for held in range(HOLD_HOURS):
        now = start + held
        bar = hours.get(now)
        if bar is None:                      # the hourly bars ran out: out at the last close seen
            return ended(now - 1 if held else now, last_close, "time", broken=True)
        opened, high, low, close = bar
        if held and (opened - stop) * side <= 0:
            return ended(now, opened, "loss", gap=True)
        if held and not unreachable and (opened - target) * side >= 0:
            return ended(now, target, "win")
        toward_stop, toward_target = (low, high) if side == 1 else (high, low)
        at_target = not unreachable and (toward_target - target) * side >= 0
        if (toward_stop - stop) * side <= 0:
            return ended(now, stop, "loss", tie=at_target)
        if at_target:
            return ended(now, target, "win")
        last_close = close
    return ended(start + HOLD_HOURS - 1, last_close, "time")


def table(market: xsb.Market, archive: Path, days: Mapping[str, Sequence[date]], ratio: int,
          span: tuple[date, date] = SPAN, read: dict[str, Any] | None = None,
          ) -> dict[tuple[str, date, int], dict[str, Any] | None]:
    """How the trade a signal would open ends, for every member day of every coin and both sides, one coin's hourly
    bars in memory at a time. ``read`` is filled with the last hour any coin's bars reached."""
    out: dict[tuple[str, date, int], dict[str, Any] | None] = {}
    latest = None
    for symbol in sorted(days):
        hours = read_hours(archive / HOURLY_KIND / symbol, span[1])
        if hours:
            latest = max(hours) if latest is None else max(latest, max(hours))
        funding = stamps(market.funding.get(symbol) or [])
        bars = market.bars[symbol]
        for day in days[symbol]:
            for side in (1, -1):
                out[(symbol, day, side)] = trade(hours, bars, day, side, ratio, funding)
    if read is not None:
        read["last_hour_read"] = latest
    return out


# --- the null and the plant --------------------------------------------------------------------------------------

def members_by_day(where: Mapping[str, Mapping[date, int]]) -> dict[date, list[str]]:
    """Per day, the coins that are members and can be entered, in name order."""
    out: dict[date, list[str]] = {}
    for symbol in sorted(where):
        for day in where[symbol]:
            out.setdefault(day, []).append(symbol)
    return out


def shifted(real: Sequence[Signal], days: Mapping[str, Sequence[date]], seed: int, tier: str,
            where: Mapping[str, Mapping[date, int]] | None = None, span: tuple[date, date] = SPAN,
            members: Mapping[date, Sequence[str]] | None = None) -> tuple[list[Signal], dict[str, int]]:
    """The null's signals and what became of them: every signal moved the same number of days round ITS OWN
    CALENDAR YEAR (one draw a book, at least :data:`MIN_SHIFT` days either way), so each year keeps its own signals:
    a tier's first year has few members, and signals rotated into it from the other years would find no coin. One
    that lands where its coin is no member goes to a member of that day that has no signal yet, drawn at random,
    with its side and width; it is dropped only when that day has no such member."""
    where = fr.places(days) if where is None else where
    members = members_by_day(where) if members is None else members
    draw = random.Random(f"L33|{tier}|{seed}")
    step = draw.randint(MIN_SHIFT, 365 - MIN_SHIFT)
    out: list[Signal] = []
    used: dict[date, set[str]] = {}
    homeless: list[Signal] = []
    for day, symbol, side, width in real:
        first = max(date(day.year, 1, 1), span[0])
        length = (min(date(day.year, 12, 31), span[1]) - first).days + 1
        moved = first + timedelta(days=((day - first).days + step) % length)
        if moved in where[symbol]:
            out.append((moved, symbol, side, width))
            used.setdefault(moved, set()).add(symbol)
        else:
            homeless.append((moved, symbol, side, width))
    for moved, _, side, width in sorted(homeless):
        free = [s for s in members.get(moved, ()) if s not in used.setdefault(moved, set())]
        if free:
            symbol = draw.choice(free)
            used[moved].add(symbol)
            out.append((moved, symbol, side, width))
    return sorted(out), {"kept_on_own_coin": len(real) - len(homeless), "given_to_a_member": len(out) - (len(real) - len(homeless)),
                         "dropped": len(real) - len(out)}


def planted(made: Mapping[tuple[str, date, int], Mapping[str, Any] | None], moved: Sequence[Signal], seed: int,
            lift: float) -> tuple[list[Signal], dict[str, Any]]:
    """A null's signals with non-winning entries dropped until the win rate of what is left is the null's own plus
    ``lift``. ``lift`` 0 keeps every signal that can be opened."""
    can = [(s, t) for s in moved if (t := made.get((s[1], s[0], s[2]))) is not None]
    wins = sum(1 for _, t in can if t["outcome"] == "win")
    rest = len(can) - wins
    own = wins / len(can) if can else 0.0
    asked = own + lift
    note = {"null_win_rate": round(own, 4), "asked_win_rate": round(asked, 4), "built": True}
    if not lift:
        return [s for s, _ in can], note
    if not wins or not rest or asked >= 1:
        return [s for s, _ in can], {**note, "built": False}
    keep = wins * (1 - asked) / (rest * asked)
    draw = random.Random(f"L33|plant|{seed}|{lift:.4f}")
    return [s for s, t in can if t["outcome"] == "win" or draw.random() < keep], note


# --- the book ----------------------------------------------------------------------------------------------------

def book(made: Mapping[tuple[str, date, int], Mapping[str, Any] | None], entries: Iterable[Signal], tier: str,
         ) -> dict[str, Any]:
    """The book ``entries`` build from the trade table: the trades taken, what was passed over, and each day's net
    on a budget of 1 (as a fraction), booked on the day a trade ends. A signal read at day ``t`` is taken at the
    next instant if the coin has no open trade and fewer than :data:`MAX_OPEN` are open; a trade that ends on day
    ``t`` frees its place for the signals of ``t``."""
    by_day: dict[date, list[Signal]] = {}
    for entry in entries:
        by_day.setdefault(entry[0], []).append(entry)
    cost = COST_BPS[tier] / 10_000
    held: dict[str, date] = {}
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
            one = made.get((symbol, day, side))
            if one is None:
                passed["not_openable"] += 1
                continue
            notional = min(RISK / one["stop_frac"], 1 / MAX_OPEN)
            held[symbol] = one["exit_day"]
            open_counts.append(len(held))
            price = side * (one["exit"] - one["entry"]) / one["entry"]
            net[one["exit_day"]] = net.get(one["exit_day"], 0.0) + notional * (price - one["funding"] - 2 * cost)
            taken.append({**one, "symbol": symbol, "signal_day": day, "notional": notional,
                          "funding_r": -one["funding"] / one["stop_frac"], "cost_r": 2 * cost / one["stop_frac"]})
    return {"trades": taken, "net": net, "passed_over": passed,
            "mean_open_at_entry": round(statistics.fmean(open_counts), 3) if open_counts else None}


def shape(made: Mapping[str, Any]) -> dict[str, Any]:
    """What the structure door compares, none of it a result: trades taken, the long share, the mean open count."""
    trades = made["trades"]
    return {"trades": len(trades),
            "long_share": round(sum(1 for t in trades if t["side"] == 1) / len(trades), 4) if trades else None,
            "mean_open_at_entry": made["mean_open_at_entry"]}


def counted(made: Mapping[str, Any]) -> dict[str, Any]:
    """Section 130.5 step 1. No profit or loss: neither a win count nor an R nor a net. The share of trades that end
    at the limit is here because it says whether the cell is the geometry that was asked for."""
    base = fr.counted(made)
    trades = made["trades"]
    n = len(trades)
    time_share = sum(1 for t in trades if t["outcome"] == "time") / n if n else None
    stops = sorted(t["stop_frac"] for t in trades)
    pick = lambda q: round(stops[int(q * (n - 1))] * 100, 2) if n else None        # noqa: E731
    tie = base["tie_share"]
    stop = {"thin_years": base["stop"]["thin_years"], "too_many_ties": tie is not None and tie > MAX_TIE_SHARE,
            "too_many_time_outs": time_share is not None and time_share > MAX_TIME_SHARE,
            "hourly_open_differs": sum(1 for t in trades if t["open_differs"])}
    stop["stops"] = bool(stop["thin_years"] or stop["too_many_ties"] or stop["too_many_time_outs"]
                         or stop["hourly_open_differs"] or n == 0)
    capped = sum(1 for t in trades if t["notional"] < RISK / t["stop_frac"] * (1 - 1e-9))        # against THIS book's risk unit
    return {**{k: v for k, v in base.items() if k != "stop"}, **shape(made),
            "capped_notional_share": round(capped / n, 4) if n else None,
            "time_out_share": round(time_share, 4) if time_share is not None else None,
            "broken_path_share": round(sum(1 for t in trades if t["broken"]) / n, 4) if n else None,
            "stop_pct": {"p10": pick(0.10), "median": pick(0.50), "p90": pick(0.90)},
            "median_hours_held": statistics.median(t["hours_held"] for t in trades) if n else None, "stop": stop}


def structure(real: Mapping[str, Any], nulls: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The structure door: the nulls' median trades, long share and mean open count against the real book's, inside
    :data:`STRUCTURE_BAND`. Outside it the null is another book and the cell is not measured."""
    medians = {k: statistics.median(b[k] for b in nulls if b[k] is not None) for k in STRUCTURE_BAND}
    inside = {"trades": abs(medians["trades"] - real["trades"]) <= STRUCTURE_BAND["trades"] * real["trades"],
              "long_share": abs(medians["long_share"] - real["long_share"]) <= STRUCTURE_BAND["long_share"],
              "mean_open_at_entry": abs(medians["mean_open_at_entry"] - real["mean_open_at_entry"])
              <= STRUCTURE_BAND["mean_open_at_entry"] * real["mean_open_at_entry"]}
    return {"real": {k: real[k] for k in STRUCTURE_BAND}, "null_median": medians, "inside": inside,
            "band": STRUCTURE_BAND, "ok": all(inside.values())}


def seen(rows: Sequence[Mapping[str, Any]], line: float) -> dict[str, Any]:
    """The planted reading, at the BOOK's realised win rate. Size zero must be chance's number or nothing is read.
    The door sees Thomas's line when, at the smallest size whose books win at ``line`` or more (by their median),
    :data:`strategy_ladder.SENS_SHARE` of the seeds pass the null door."""
    by_size: dict[str, dict[str, Any]] = {}
    for row in rows:
        size = by_size.setdefault(row["size"], {"seeds": 0, "built": 0, "under_0.05": 0, "pass_door": 0, "rates": []})
        size["seeds"] += 1
        if row["built"]:
            size["built"] += 1
            size["under_0.05"] += row["p"] is not None and row["p"] < sl.NULL_ALPHA
            size["pass_door"] += bool(row["passes"])
            if row["win_rate"] is not None:
                size["rates"].append(row["win_rate"])
    for size in by_size.values():
        rates = size.pop("rates")
        size["book_win_rate_median"] = round(statistics.median(rates), 4) if rates else None
        size["share"] = round(size["pass_door"] / size["built"], 3) if size["built"] else None
        size["seen"] = size["built"] == size["seeds"] and size["share"] is not None and size["share"] >= sl.SENS_SHARE
    zero = by_size.get("lift_0.00", {})
    low, high = sl.SENS_FALSE_ALARM
    valid = bool(zero) and zero["built"] == PLANT_SEEDS and low <= zero["under_0.05"] <= high
    at_line = [name for name in sorted(by_size) if (by_size[name]["book_win_rate_median"] or 0) >= line]
    return {"sizes": by_size, "size_zero_valid": valid, "false_alarm_band": [low, high], "line": round(line, 4),
            "smallest_size_at_the_line": at_line[0] if at_line else None,
            "sees_the_line": bool(valid and at_line and by_size[at_line[0]]["seen"]),
            "plant_reaches_the_line": bool(at_line)}


def verdict(real: Mapping[str, Any], null_sharpes: Sequence[float | None], drift: Mapping[str, Any]) -> dict[str, Any]:
    """The ladder, as it stands, with the null door at :data:`DIVISOR`. The win rate judges nothing."""
    sharpe, yearly = real["annual_sharpe"], real["annual_pct"]
    entry = sharpe is not None and sharpe >= bs.ENTRY_SHARPE and yearly is not None and yearly >= drift["line_annual_pct"]
    later = sl.later_years(real["years_pct"])
    beside = sl.beside_nulls(sharpe, null_sharpes, candidates=DIVISOR)
    steady = sl.stability(real["years_pct"])
    return {"2a_entry": entry, "2b_later_years": later, "2c_beside_nulls": beside, "3_stability": steady,
            "passes": bool(entry and later["passes"] and beside["passes"] and steady["passes"])}


# --- the command -------------------------------------------------------------------------------------------------

STAGES = ("two-counts", "two-structure", "two-null", "two-plant", "two-real")


def _file(path: str | None) -> dict[str, Any] | None:
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def _is(body: Mapping[str, Any] | None, command: str, tier: str, ratio: int) -> bool:
    return bool(body) and (body.get("command"), body.get("tier"), body.get("ratio")) == (command, tier, ratio)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="two_outcome", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=STAGES)
    parser.add_argument("--tier", choices=TIERS, required=True)
    parser.add_argument("--ratio", type=int, choices=RATIOS, required=True)
    for name in ("counts", "structure", "null", "plant"):
        parser.add_argument(f"--{name}", default=None)
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    cmd, tier, ratio = args.command, args.tier, args.ratio
    counts, shaped, null, plant = _file(args.counts), _file(args.structure), _file(args.null), _file(args.plant)
    rank = STAGES.index(cmd)
    if rank >= 1 and not (_is(counts, "two-counts", tier, ratio) and not counts["counts"]["stop"]["stops"]):
        print("refused: needs this cell's --counts, and one that did not stop", file=sys.stderr)
        return EXIT_USAGE
    if rank >= 2 and not (_is(shaped, "two-structure", tier, ratio) and shaped["structure"]["ok"]):
        print("refused: needs this cell's --structure, and one inside the band: outside it the null is another book",
              file=sys.stderr)
        return EXIT_USAGE
    if rank >= 3 and not (_is(null, "two-null", tier, ratio) and len(null["books"]) == NULLS):
        print(f"refused: needs this cell's --null with all {NULLS} books", file=sys.stderr)
        return EXIT_USAGE
    if rank >= 4 and not _is(plant, "two-plant", tier, ratio):
        print("refused: needs this cell's --plant: the door is read before the real book", file=sys.stderr)
        return EXIT_USAGE
    if cmd == "two-real":
        drift = fr.line([b["annual_pct"] for b in null["books"] if b["annual_pct"] is not None])
        if not drift["usable"]:
            print("BLOCKED: the nulls' 95th percentile reaches the unusable level: this cell cannot tell a book from the "
                  "market's drift, and its real book is not read", file=sys.stderr)
            return EXIT_BLOCKED
        if not plant["seen"]["size_zero_valid"]:
            print("BLOCKED: the planted books at size zero are not chance's number: the plant is broken, and nothing "
                  "is read", file=sys.stderr)
            return EXIT_BLOCKED
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    bs.check_search_span(SPAN)
    market = lab.hold(cache, SPAN, attach=False)
    if market is None:
        print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
        return EXIT_BLOCKED
    days = fr.member_days(market, tier, SPAN)
    real = fr.signals(market, days)
    where = fr.places(days)
    by_day = members_by_day(where)
    info: dict[str, Any] = {}
    made = table(market, cache / "archive", days, ratio, SPAN, info)
    if info["last_hour_read"] is not None and info["last_hour_read"] >= hour_of(SPAN[1] + ONE_DAY):
        print("BLOCKED: an hourly bar after the span was read", file=sys.stderr)
        return EXIT_BLOCKED
    last_hour = None if info["last_hour_read"] is None else datetime.fromtimestamp(
        info["last_hour_read"] * 3600, tz=timezone.utc).strftime("%Y-%m-%dT%H:00Z")
    head = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC, "command": cmd, "tier": tier,
            "ratio": ratio, "span": [d.isoformat() for d in SPAN], "last_day_read": lab.last_day(market).isoformat(),
            "last_hour_read": last_hour,
            "signals": {"all": len(real), "long": sum(1 for s in real if s[2] == 1), "coins": len(days)},
            "table": {"entries": len(made), "not_openable": sum(1 for t in made.values() if t is None),
                      "hourly_open_differs": sum(1 for t in made.values() if t is not None and t["open_differs"])}}
    if cmd == "two-counts":
        body: dict[str, Any] = {"reads_profit_or_loss": False, "counts": counted(book(made, real, tier))}
    elif cmd == "two-structure":
        books, fate = [], {"kept_on_own_coin": 0, "given_to_a_member": 0, "dropped": 0}
        for seed in range(NULLS):
            moved, note = shifted(real, days, seed, tier, where, SPAN, by_day)
            books.append(shape(book(made, moved, tier)))
            fate = {k: fate[k] + note[k] for k in fate}
        body = {"reads_profit_or_loss": False, "structure": structure(counts["counts"], books),
                "signals_a_book": {k: round(v / NULLS, 1) for k, v in fate.items()}}
    elif cmd == "two-null":
        books = []
        for seed in range(NULLS):
            one = fr.measured(book(made, shifted(real, days, seed, tier, where, SPAN, by_day)[0], tier))
            books.append({"seed": seed, **{k: one.get(k) for k in ("annual_sharpe", "annual_pct", "trades", "long_share",
                                                                   "win_rate", "time_share", "mean_price_r", "mean_cost_r",
                                                                   "mean_funding_r", "mean_net_r")}})
        body = {"reads_profit_or_loss": True, "reads_real_entries": False, "books": books,
                "drift": fr.line([b["annual_pct"] for b in books if b["annual_pct"] is not None]),
                "break_even_before_cost": round(1 / (1 + ratio), 4)}
    elif cmd == "two-plant":
        sharpes = [b["annual_sharpe"] for b in null["books"]]
        rows = []
        for k in range(PLANT_SEEDS):
            seed = PLANT_SEED_BASE + k
            moved = shifted(real, days, seed, tier, where, SPAN, by_day)[0]
            for lift in LIFTS:
                entries, note = planted(made, moved, seed, lift)
                one = fr.measured(book(made, entries, tier)) if note["built"] else {}
                beside = sl.beside_nulls(one.get("annual_sharpe"), sharpes, candidates=DIVISOR)
                rows.append({"size": f"lift_{lift:.2f}", "seed": seed, **note, "win_rate": one.get("win_rate"),
                             "annual_sharpe": one.get("annual_sharpe"), "p": beside["p"], "passes": beside["passes"]})
        body = {"reads_profit_or_loss": True, "reads_real_entries": False, "rows": rows,
                "seen": seen(rows, THOMAS_LINE[ratio])}
    else:
        one = fr.measured(book(made, real, tier))
        body = {"reads_profit_or_loss": True, "reads_real_entries": True, "book": one, "drift": drift,
                "null_win_rate_median": statistics.median(b["win_rate"] for b in null["books"] if b["win_rate"] is not None),
                "break_even_before_cost": round(1 / (1 + ratio), 4), "thomas_line": round(THOMAS_LINE[ratio], 4),
                "door_sees_thomas_line": plant["seen"]["sees_the_line"],
                "plant_reaches_the_line": plant["seen"]["plant_reaches_the_line"],
                "verdict": verdict(one, [b["annual_sharpe"] for b in null["books"]], drift)}
    print(json.dumps({**head, **body}, indent=1, sort_keys=True, ensure_ascii=False, default=str))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
