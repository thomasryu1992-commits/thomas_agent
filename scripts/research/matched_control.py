"""L34, the matched control: L33's real book, read beside same-day, same-side trades in other member coins.

``docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md`` sections 134 (design) and 135 (registration; Thomas 2026-11-22
"기본값대로"). L33's null rebuilt a six-place book from shifted signals; it took a quarter to a third fewer trades than
the real book, so nothing was read (section 132). Here nothing is rebuilt.

- **The real book** is L33's, unchanged (:func:`two_outcome.table`, :func:`two_outcome.book`): its counts must be
  the ones section 132 recorded (:data:`L33_COUNTS`), or the run refuses.
- **A control set** pairs every trade the real book took with a trade on the same day and the same side in another
  member coin, drawn from the :data:`NEAREST` members of that day whose stop distance is nearest the trade's own
  (:func:`pools`, :func:`control`). Count, day, side and stop width match by construction. A set is a list of
  matched trades, not a six-place book: its trades may overlap.
- **What it asks** is coin choice alone. The control shares the day and the side, so its p says nothing about
  timing: a miss reads "no better than another member on that day and side".
- **The plant** draws, with probability lambda, a winning coin among the five when there is one (:func:`control`
  with ``prefer``), and is read at the set's realised win rate.
- **Order, fixed before anything is read:** counts -> structure (no profit or loss) -> 600 control sets -> planted
  sets -> the real book once. It is the third look at the same entries on the same span: the null door's divisor
  is :data:`DIVISOR`.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

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
from scripts.research import two_outcome as tw  # noqa: E402

SPEC = "STRATEGY_DISCOVERY_LOOP_V0.1 section 135 (L34)"
SPAN = tw.SPAN
TIERS = tw.TIERS
RATIOS = tw.RATIOS
DIVISOR = 12                                # L32's four cells, L33's four and these four: one entry family, one span
NEAREST = 5                                 # members of the day, by stop distance, a control coin is drawn from
SETS = 600
PLANT_SEEDS = 100
PLANT_SEED_BASE = 10_000
LAMBDAS = (0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40)
THOMAS_LINE = fr.THOMAS_LINE
MIN_MATCHED = 0.95                          # of the real book's trades with a control
STOP_BAND = 0.10                            # the controls' median stop distance against the real book's, relative
CAPPED_BAND = 0.05                          # the share of capped trades against the real book's, absolute
MAX_TIME_SHARE = tw.MAX_TIME_SHARE          # of the controls' trades ending at the limit
# The real book's counts as section 132.1 recorded them: trades, trades by year, the share ending at the limit.
L33_COUNTS: dict[tuple[str, int], dict[str, Any]] = {
    ("top30", 3): {"trades": 3550, "by_year": {"2020": 504, "2021": 855, "2022": 632, "2023": 796, "2024": 763}, "time_out_share": 0.0039},
    ("top30", 5): {"trades": 2742, "by_year": {"2020": 421, "2021": 662, "2022": 459, "2023": 648, "2024": 552}, "time_out_share": 0.0098},
    ("rank31_100", 3): {"trades": 3816, "by_year": {"2020": 81, "2021": 1055, "2022": 755, "2023": 999, "2024": 926}, "time_out_share": 0.005},
    ("rank31_100", 5): {"trades": 2901, "by_year": {"2020": 74, "2021": 812, "2022": 568, "2023": 762, "2024": 685}, "time_out_share": 0.0124},
}

Table = Mapping[tuple[str, date, int], Mapping[str, Any] | None]


# --- the pairing -------------------------------------------------------------------------------------------------

def pools(made: Table, trades: Sequence[Mapping[str, Any]], members: Mapping[date, Sequence[str]]) -> list[list[str]]:
    """Per real trade, the :data:`NEAREST` members of its signal day whose stop distance is nearest its own, nearest
    first (a tie goes to the name): never the trade's own coin, never a coin that cannot be opened there."""
    out = []
    for trade in trades:
        day, side, own = trade["signal_day"], trade["side"], trade["stop_frac"]
        near = sorted((abs(one["stop_frac"] - own), coin) for coin in members.get(day, ())
                      if coin != trade["symbol"] and (one := made.get((coin, day, side))) is not None)
        out.append([coin for _, coin in near[:NEAREST]])
    return out


def control(made: Table, trades: Sequence[Mapping[str, Any]], near: Sequence[Sequence[str]], seed: int, tier: str,
            ratio: int, prefer: float = 0.0) -> list[tuple[str, date, int]]:
    """One control set: for every real trade a coin from its pool, drawn evenly, never the same coin twice on one
    day inside the set; a trade whose pool is used up has no control. With probability ``prefer`` the draw is made
    among the pool's WINNING coins when there is one (the plant)."""
    draw = random.Random(f"L34|{tier}|{ratio}|{seed}|{prefer:.4f}")
    used: dict[date, set[str]] = {}
    out = []
    for trade, pool in zip(trades, near):
        day, side = trade["signal_day"], trade["side"]
        free = [coin for coin in pool if coin not in used.setdefault(day, set())]
        if not free:
            continue
        winners = [coin for coin in free if made[(coin, day, side)]["outcome"] == "win"]
        wants = prefer > 0 and draw.random() < prefer
        coin = draw.choice(winners if wants and winners else free)
        used[day].add(coin)
        out.append((coin, day, side))
    return out


def settled(made: Table, picks: Sequence[tuple[str, date, int]], tier: str) -> dict[str, Any]:
    """The matched trades as the real book books its own: the same risk unit against each trade's own stop, the
    same cap on one trade's notional, the same cost, the result on the day the trade ends. No limit on how many
    are open at once: a control set is not a book."""
    cost = tw.COST_BPS[tier] / 10_000
    taken, net = [], {}
    for coin, day, side in picks:
        one = made[(coin, day, side)]
        notional = min(tw.RISK / one["stop_frac"], 1 / tw.MAX_OPEN)
        price = side * (one["exit"] - one["entry"]) / one["entry"]
        net[one["exit_day"]] = net.get(one["exit_day"], 0.0) + notional * (price - one["funding"] - 2 * cost)
        taken.append({**one, "symbol": coin, "signal_day": day, "notional": notional,
                      "funding_r": -one["funding"] / one["stop_frac"], "cost_r": 2 * cost / one["stop_frac"]})
    return {"trades": taken, "net": net, "passed_over": {}, "mean_open_at_entry": None}


def overlap(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Two limits the real book has and a control set does not, counted and not judged: the share of trades opened
    on a coin the set still holds, and the most trades open on one day."""
    held: dict[str, date] = {}
    stacked = 0
    change: dict[date, int] = {}
    for t in sorted(trades, key=lambda t: (t["entry_day"], t["symbol"])):
        if t["symbol"] in held and held[t["symbol"]] >= t["entry_day"]:
            stacked += 1
        held[t["symbol"]] = max(held.get(t["symbol"], t["exit_day"]), t["exit_day"])
        change[t["entry_day"]] = change.get(t["entry_day"], 0) + 1
        after = t["exit_day"] + tw.ONE_DAY
        change[after] = change.get(after, 0) - 1
    most = now = 0
    for day in sorted(change):
        now += change[day]
        most = max(most, now)
    return {"stacked_share": stacked / len(trades) if trades else None, "most_open": most}


def shape(made_set: Mapping[str, Any], real_trades: int) -> dict[str, Any]:
    """What the structure door reads of one control set, none of it a result."""
    trades = made_set["trades"]
    n = len(trades)
    return {**overlap(trades), "matched_share": n / real_trades if real_trades else None,
            "stop_median": statistics.median(t["stop_frac"] for t in trades) if n else None,
            "capped_share": sum(1 for t in trades if t["notional"] < tw.RISK / t["stop_frac"] * (1 - 1e-9)) / n if n else None,
            "time_out_share": sum(1 for t in trades if t["outcome"] == "time") / n if n else None,
            "median_hours_held": statistics.median(t["hours_held"] for t in trades) if n else None}


def structure(real: Mapping[str, Any], sets: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The structure door, four lines over the control sets' medians: enough real trades have a control, the
    controls' stop distance and capped share sit beside the real book's, and the controls too end at a stop or a
    target. Outside any line the cell is not measured."""
    med = {k: statistics.median(s[k] for s in sets if s[k] is not None) for k in ("matched_share", "stop_median", "capped_share",
                                                                                 "time_out_share", "median_hours_held",
                                                                                 "stacked_share", "most_open")}
    inside = {"matched": med["matched_share"] >= MIN_MATCHED,
              "stop_distance": abs(med["stop_median"] - real["stop_median"]) <= STOP_BAND * real["stop_median"],
              "capped": abs(med["capped_share"] - real["capped_share"]) <= CAPPED_BAND,
              "time_outs": med["time_out_share"] <= MAX_TIME_SHARE}
    return {"real": dict(real), "control_median": {k: round(v, 5) for k, v in med.items()}, "inside": inside,
            "lines": {"matched_at_least": MIN_MATCHED, "stop_distance_band": STOP_BAND, "capped_band": CAPPED_BAND,
                      "time_outs_at_most": MAX_TIME_SHARE},
            "ok": all(inside.values())}


def real_shape(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The real book's side of the structure door."""
    n = len(trades)
    return {**overlap(trades), "trades": n, "stop_median": statistics.median(t["stop_frac"] for t in trades),
            "capped_share": sum(1 for t in trades if t["notional"] < tw.RISK / t["stop_frac"] * (1 - 1e-9)) / n,
            "median_hours_held": statistics.median(t["hours_held"] for t in trades)}


def same_book(counts: Mapping[str, Any], tier: str, ratio: int) -> bool:
    """The real book is the one section 132.1 counted."""
    want = L33_COUNTS[(tier, ratio)]
    return (counts["trades"] == want["trades"] and counts["time_out_share"] == want["time_out_share"]
            and {y: v["trades"] for y, v in counts["by_year"].items()} == want["by_year"])


# --- the doors ---------------------------------------------------------------------------------------------------

def seen(rows: Sequence[Mapping[str, Any]], line: float) -> dict[str, Any]:
    """The planted reading at the set's realised win rate: lambda 0 must be chance's number or nothing is read; the
    door sees Thomas's line when, at the smallest lambda whose sets win at ``line`` or more (by their median),
    :data:`strategy_ladder.SENS_SHARE` of the seeds pass the null door."""
    by_size: dict[str, dict[str, Any]] = {}
    for row in rows:
        size = by_size.setdefault(row["size"], {"seeds": 0, "under_0.05": 0, "pass_door": 0, "rates": []})
        size["seeds"] += 1
        size["under_0.05"] += row["p"] is not None and row["p"] < sl.NULL_ALPHA
        size["pass_door"] += bool(row["passes"])
        if row["win_rate"] is not None:
            size["rates"].append(row["win_rate"])
    for size in by_size.values():
        rates = size.pop("rates")
        size["win_rate_median"] = round(statistics.median(rates), 4) if rates else None
        size["share"] = round(size["pass_door"] / size["seeds"], 3)
        size["seen"] = size["share"] >= sl.SENS_SHARE
    zero = by_size.get("lambda_0.00", {})
    low, high = sl.SENS_FALSE_ALARM
    valid = bool(zero) and zero["seeds"] == PLANT_SEEDS and low <= zero["under_0.05"] <= high
    # A control that already wins at the line: the line is not above what another member wins on that day and side,
    # and the plant cannot ask whether the door sees it.
    already = bool(zero) and (zero["win_rate_median"] or 0) >= line
    at_line = [name for name in sorted(by_size) if name != "lambda_0.00" and (by_size[name]["win_rate_median"] or 0) >= line]
    return {"sizes": by_size, "size_zero_valid": valid, "false_alarm_band": [low, high], "line": round(line, 4),
            "controls_already_at_the_line": already,
            "smallest_size_at_the_line": None if already else (at_line[0] if at_line else None),
            "sees_the_line": None if already else bool(valid and at_line and by_size[at_line[0]]["seen"]),
            "plant_reaches_the_line": None if already else bool(at_line)}


def verdict(real: Mapping[str, Any], null_sharpes: Sequence[float | None], drift: Mapping[str, Any]) -> dict[str, Any]:
    """The ladder, as it stands, with the null door at :data:`DIVISOR`. A miss at 2(c) reads "no better than another
    member coin on that day and side"; the win rate judges nothing."""
    sharpe, yearly = real["annual_sharpe"], real["annual_pct"]
    entry = sharpe is not None and sharpe >= bs.ENTRY_SHARPE and yearly is not None and yearly >= drift["line_annual_pct"]
    later = sl.later_years(real["years_pct"])
    beside = sl.beside_nulls(sharpe, null_sharpes, candidates=DIVISOR)
    steady = sl.stability(real["years_pct"])
    return {"2a_entry": entry, "2b_later_years": later, "2c_beside_controls": beside, "3_stability": steady,
            "passes": bool(entry and later["passes"] and beside["passes"] and steady["passes"])}


# --- the command -------------------------------------------------------------------------------------------------

STAGES = ("match-counts", "match-structure", "match-null", "match-plant", "match-real")
KEPT = ("annual_sharpe", "annual_pct", "trades", "win_rate", "time_share", "mean_price_r", "mean_cost_r", "mean_funding_r", "mean_net_r")


def _file(path: str | None) -> dict[str, Any] | None:
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def _is(body: Mapping[str, Any] | None, command: str, tier: str, ratio: int) -> bool:
    return bool(body) and (body.get("command"), body.get("tier"), body.get("ratio")) == (command, tier, ratio)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="matched_control", description=__doc__.splitlines()[0])
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
    if rank >= 1 and not (_is(counts, "match-counts", tier, ratio) and not counts["counts"]["stop"]["stops"]):
        print("refused: needs this cell's --counts, and one that did not stop", file=sys.stderr)
        return EXIT_USAGE
    if rank >= 2 and not (_is(shaped, "match-structure", tier, ratio) and shaped["structure"]["ok"]):
        print("refused: needs this cell's --structure, and one inside every line", file=sys.stderr)
        return EXIT_USAGE
    if rank >= 3 and not (_is(null, "match-null", tier, ratio) and len(null["sets"]) == SETS):
        print(f"refused: needs this cell's --null with all {SETS} control sets", file=sys.stderr)
        return EXIT_USAGE
    if rank >= 4 and not _is(plant, "match-plant", tier, ratio):
        print("refused: needs this cell's --plant: the door is read before the real book", file=sys.stderr)
        return EXIT_USAGE
    if cmd == "match-real":
        drift = fr.line([s["annual_pct"] for s in null["sets"] if s["annual_pct"] is not None])
        if not drift["usable"]:
            print("BLOCKED: the control sets' 95th percentile reaches the unusable level: this cell cannot tell the book "
                  "from the day and side it shares with its controls, and its real book is not read", file=sys.stderr)
            return EXIT_BLOCKED
        if not plant["seen"]["size_zero_valid"]:
            print("BLOCKED: the planted sets at lambda 0 are not chance's number: the plant is broken, and nothing is "
                  "read", file=sys.stderr)
            return EXIT_BLOCKED
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    bs.check_search_span(SPAN)
    market = lab.hold(cache, SPAN, attach=False)
    if market is None:
        print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
        return EXIT_BLOCKED
    days = fr.member_days(market, tier, SPAN)
    signals = fr.signals(market, days)
    members = tw.members_by_day(fr.places(days))
    info: dict[str, Any] = {}
    made = tw.table(market, cache / "archive", days, ratio, SPAN, info)
    if info["last_hour_read"] is not None and info["last_hour_read"] >= tw.hour_of(SPAN[1] + tw.ONE_DAY):
        print("BLOCKED: an hourly bar after the span was read", file=sys.stderr)
        return EXIT_BLOCKED
    real = tw.book(made, signals, tier)
    tally = tw.counted(real)
    if not same_book(tally, tier, ratio):
        print("BLOCKED: the real book is not the one section 132.1 counted (trades, trades by year or the share "
              "ending at the limit differ)", file=sys.stderr)
        return EXIT_BLOCKED
    trades = real["trades"]
    near = pools(made, trades, members)
    last_hour = None if info["last_hour_read"] is None else datetime.fromtimestamp(
        info["last_hour_read"] * 3600, tz=timezone.utc).strftime("%Y-%m-%dT%H:00Z")
    head = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC, "command": cmd, "tier": tier,
            "ratio": ratio, "span": [d.isoformat() for d in SPAN], "last_day_read": lab.last_day(market).isoformat(),
            "last_hour_read": last_hour, "real_trades": len(trades),
            "pool_sizes": {str(k): sum(1 for p in near if len(p) == k) for k in range(NEAREST + 1)}}
    if cmd == "match-counts":
        body: dict[str, Any] = {"reads_profit_or_loss": False, "counts": tally, "same_book_as_section_132": True}
    elif cmd == "match-structure":
        sets = [shape(settled(made, control(made, trades, near, seed, tier, ratio), tier), len(trades)) for seed in range(SETS)]
        body = {"reads_profit_or_loss": False, "structure": structure(real_shape(trades), sets)}
    elif cmd == "match-null":
        sets = []
        for seed in range(SETS):
            one = fr.measured(settled(made, control(made, trades, near, seed, tier, ratio), tier))
            sets.append({"seed": seed, **{k: one.get(k) for k in KEPT}})
        body = {"reads_profit_or_loss": True, "reads_real_book": False, "sets": sets,
                "drift": fr.line([s["annual_pct"] for s in sets if s["annual_pct"] is not None]),
                "break_even_before_cost": round(1 / (1 + ratio), 4)}
    elif cmd == "match-plant":
        sharpes = [s["annual_sharpe"] for s in null["sets"]]
        rows = []
        for k in range(PLANT_SEEDS):
            for lam in LAMBDAS:
                one = fr.measured(settled(made, control(made, trades, near, PLANT_SEED_BASE + k, tier, ratio, lam), tier))
                beside = sl.beside_nulls(one.get("annual_sharpe"), sharpes, candidates=DIVISOR)
                rows.append({"size": f"lambda_{lam:.2f}", "seed": PLANT_SEED_BASE + k, "win_rate": one.get("win_rate"),
                             "annual_sharpe": one.get("annual_sharpe"), "p": beside["p"], "passes": beside["passes"]})
        body = {"reads_profit_or_loss": True, "reads_real_book": False, "rows": rows, "seen": seen(rows, THOMAS_LINE[ratio])}
    else:
        one = fr.measured(real)
        body = {"reads_profit_or_loss": True, "reads_real_book": True, "book": one, "drift": drift,
                "control_win_rate_median": statistics.median(s["win_rate"] for s in null["sets"] if s["win_rate"] is not None),
                "control_net_r_median": statistics.median(s["mean_net_r"] for s in null["sets"] if s["mean_net_r"] is not None),
                "break_even_before_cost": round(1 / (1 + ratio), 4), "thomas_line": round(THOMAS_LINE[ratio], 4),
                "door_sees_thomas_line": plant["seen"]["sees_the_line"],
                "plant_reaches_the_line": plant["seen"]["plant_reaches_the_line"],
                "controls_already_at_the_line": plant["seen"]["controls_already_at_the_line"],
                "verdict": verdict(one, [s["annual_sharpe"] for s in null["sets"]], drift)}
    print(json.dumps({**head, **body}, indent=1, sort_keys=True, ensure_ascii=False, default=str))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
