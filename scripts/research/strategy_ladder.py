"""Strategy ladder: turn a cell of the signal lab's map into a book, and ask whether it survives costs and time.

``docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md`` (Thomas 2026-11-13). The map (``signal_lab``) says where a variable
ranks returns before costs. This module holds the logics that build a strategy from such a cell, and the two rungs
after the map: the book net of the engine's costs on the development years, and its stability when the years take
turns being left out.

- **L1, the rank-weight book.** Every ranked coin is held, long or short by which side of the middle rank it is on
  and in proportion to its distance from it, each side summing to one, no coin above :data:`NAME_CAP` of its side.
  The six-against-six basket staked a side on a handful of coins; a variable that is right about ranks and wrong
  about the few largest moves loses there.
- **L2, the scenario-switching book.** L1's weights, with the direction read from the scenario at the decision. The
  direction of each scenario is the sign of the variable's rank correlation in the *other* years: a year's positions
  never use that year's returns.
- **What a logic fixes is fixed here**, before a candidate is measured: the tier, the weights, the cap, the engine's
  costs and funding. A changed value is a new logic.
- **Chance.** L1 is read beside random variables built into the same book. L2 is read beside placebo scenarios: the
  same spells in a shuffled order, switched by the same rule.
- **Development years only.** Everything runs on :data:`SPAN` through the lab's cut market; nothing reads 2025.

``run`` is sandbox-only and prints one report for every registered candidate.
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
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import basket_search as bs  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import signal_lab as lab  # noqa: E402

SPEC = "STRATEGY_DISCOVERY_LOOP_V0.1"
SPAN = lab.SPAN
NAME_CAP = 0.10                            # the most of one side a single coin may be
RANDOM_BOOKS = 10                          # of each kind of random variable, built into L1 for comparison
PLACEBO_SCENARIOS = 20                     # shuffled-spell scenario sequences L2 is read beside
STABILITY_SHARE = 0.5                      # of the entry return the year-by-year mean must reach
MAX_NEGATIVE_YEARS = 2

# The candidates of this round, fixed before any of them was measured (the proposal's ledger).
CANDIDATES: dict[str, dict[str, str]] = {
    "C001": {"round": "1", "logic": "L1", "variable": "idio_vol_30", "long": "low", "tier": "top30", "accounting": "engine",
             "from": "the map's one lead: low-volatility coins lead the next week"},
    "C002": {"round": "1", "logic": "L2", "variable": "funding_30", "axis": "heat", "tier": "top30", "accounting": "engine",
             "from": "the map's sign flip: high funding lags when funding is hot and leads when it is cool"},
    # Round 2, registered 2026-11-13 before either was measured: the same lead under the ladder's own accounting,
    # and under the risk-matched logic that C001's miss asked for.
    "C003": {"round": "2", "logic": "L1", "variable": "idio_vol_30", "long": "low", "tier": "top30", "accounting": "held",
             "from": "C001's lead again, with quantities held for the week instead of re-set daily"},
    "C004": {"round": "2", "logic": "L8", "variable": "idio_vol_30", "long": "low", "tier": "top30", "accounting": "held",
             "from": "C001 lost with the market rising: weights by risk, the leftover market exposure hedged in Bitcoin"},
}
HEDGE = bs.REFERENCE                       # the coin a risk-matched book's leftover market exposure is hedged in

Direction = Callable[[date], int]          # +1 long the high end, -1 long the low end, 0 flat

# --- rung 4: the confirm span, on a budget (D4) -------------------------------------------------------------------
CONFIRM = bs.CONFIRM                       # 2025-01-01 .. 2026-09-30; the days after it stay reserved
CONFIRM_BUDGET = 5                         # reads in all; at most one a month
CONFIRM_ALPHA = 0.10                       # the k-th read is held to alpha / k
# The reads, in order. A candidate is written here BEFORE its read, with everything the read fixes, and is never
# removed. C002's directions are the ones every leave-one-year-out fold learned on the development years.
CONFIRM_READS: tuple[dict[str, Any], ...] = (
    {"read": 1, "candidate": "C002", "signs": {"cool": 1, "hot": -1}, "registered": "2026-11-13",
     "asked": "D5, Thomas 2026-11-13"},
)
# read number -> the run that spent it. Filled in the commit that records the result; ``confirm`` refuses a read
# that is already here.
CONFIRM_DONE: dict[int, str] = {
    1: "20261113T042318Z-1057691 at a7ccd64b: C002 CONFIRM_FAIL, annual Sharpe -0.942 against 1.0",
}
# The variable groups that read the series ``signal_lab.hold`` attaches (positioning, depth, spot bars).
ATTACHED_GROUPS = frozenset({"positioning", "depth", "spot"})


# --- L1 ----------------------------------------------------------------------------------------------------------

def _capped(scores: Mapping[str, float]) -> dict[str, float]:
    """Shares of one side in proportion to ``scores``, none above :data:`NAME_CAP`; equal shares when the side is
    too small for the cap to be met."""
    if not scores:
        return {}
    if len(scores) * NAME_CAP < 1:
        return dict.fromkeys(scores, 1 / len(scores))
    fixed: dict[str, float] = {}
    free = dict(scores)
    while True:
        room = 1 - NAME_CAP * len(fixed)
        total = sum(free.values())
        over = [s for s, v in free.items() if v / total * room > NAME_CAP + 1e-12]
        if not over:
            return {**dict.fromkeys(fixed, NAME_CAP), **{s: v / total * room for s, v in free.items()}}
        for s in over:
            fixed[s] = free.pop(s)


def rank_weights(values: Mapping[str, float], *, long_high: bool) -> dict[str, float]:
    """Weights over every ranked coin: long one side of the middle rank and short the other, in proportion to the
    distance from it, each side summing to one. Empty under :data:`signal_lab.MIN_NAMES` coins."""
    if len(values) < lab.MIN_NAMES:
        return {}
    order = sorted(values, key=lambda s: (values[s], s))
    middle = (len(order) + 1) / 2
    scores = {s: (position - middle) * (1 if long_high else -1) for position, s in enumerate(order, start=1)}
    longs = _capped({s: v for s, v in scores.items() if v > 0})
    shorts = _capped({s: -v for s, v in scores.items() if v < 0})
    return {**longs, **{s: -w for s, w in shorts.items()}}


def book(read: lab.Variable, direction: Direction) -> Callable[..., dict[str, float]]:
    """A ``cross_section_basket.simulate`` weigher: L1's weights on ``read``, in the direction of the day."""
    def weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                *, seed: str | None = None) -> dict[str, float]:
        way = direction(day)
        if way == 0:
            return {}
        values = {s: v for s in members if (v := read(market, s, day)) is not None}
        return rank_weights(values, long_high=way > 0)
    return weights


# --- L8: the risk-matched book -------------------------------------------------------------------------------------

def risk_weights(values: Mapping[str, float], vols: Mapping[str, float], betas: Mapping[str, float],
                 *, long_high: bool) -> dict[str, float]:
    """L1's sides with each coin's score divided by its volatility, so a wild coin carries no more risk than a quiet
    one; then the book's leftover sensitivity to Bitcoin is offset with a Bitcoin position. Coins without a
    volatility or a beta are not held."""
    usable = {s: v for s, v in values.items() if vols.get(s) and betas.get(s) is not None}
    if len(usable) < lab.MIN_NAMES:
        return {}
    order = sorted(usable, key=lambda s: (usable[s], s))
    middle = (len(order) + 1) / 2
    scores = {s: (position - middle) * (1 if long_high else -1) / vols[s] for position, s in enumerate(order, start=1)}
    longs = _capped({s: v for s, v in scores.items() if v > 0})
    shorts = _capped({s: -v for s, v in scores.items() if v < 0})
    weights = {**longs, **{s: -w for s, w in shorts.items()}}
    exposure = sum(w * betas[s] for s, w in weights.items())
    weights[HEDGE] = weights.get(HEDGE, 0.0) - exposure
    return weights


def risk_book(read: lab.Variable, direction: Direction) -> Callable[..., dict[str, float]]:
    """A weigher for L8: the 30-day volatility and the 30-day beta to Bitcoin are read at the decision."""
    def weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                *, seed: str | None = None) -> dict[str, float]:
        way = direction(day)
        if way == 0:
            return {}
        values = {s: v for s in members if (v := read(market, s, day)) is not None}
        vols = {s: bs.FEATURES["vol"](market, s, day, lab.LOOKBACK) for s in values}
        betas = {s: (1.0 if s == HEDGE else bs.FEATURES["beta"](market, s, day, lab.LOOKBACK)) for s in values}
        return risk_weights(values, vols, betas, long_high=way > 0)
    return weights


BOOKS = {"L1": book, "L2": book, "L8": risk_book}


# --- the ladder's own accounting ---------------------------------------------------------------------------------

def simulate_held(market: xsb.Market, signal: str, monthly: Mapping[date, Sequence[str]], *, start: date, end: date,
                  weigher: Callable[..., dict[str, float]]) -> dict[str, Any]:
    """Daily net returns of a book whose quantities are fixed at each decision and held to the next.

    ``cross_section_basket.simulate`` adds each day's return at the decision's dollar weights, which is a book
    re-set to those weights every day at no cost. On coins that move several hundred percent in a day the two
    differ by tens of points (the first confirm read, 2026-11-13). Here a coin bought at the entry open earns its own
    move from that open, a short that doubles is twice as large the next day, the trade at a decision is the
    difference between the target and what the held position has become, and funding is paid on what the position
    is worth that day. Costs per unit traded are the engine's. The engine itself is not changed: registered screens
    read through it."""
    unit = (xsb.cost.DEFAULT_TAKER_FEE_BPS + xsb.cost.DEFAULT_SLIPPAGE_BPS) / 10_000
    held: dict[str, tuple[float, float]] = {}            # coin -> (weight at entry, entry open)
    daily: dict[date, dict[str, float]] = {}
    for day in xsb.held_days(start, end):
        decision = day - timedelta(days=1)
        trade_cost = 0.0
        if xsb.is_rebalance_day(signal, decision):
            target = weigher(market, monthly.get(date(decision.year, decision.month, 1), ()), decision, signal, seed=None)
            now = {s: w * bar.open / entry for s, (w, entry) in held.items()
                   if (bar := (market.bars.get(s) or {}).get(day)) is not None}
            enter = {s: w for s, w in target.items() if (market.bars.get(s) or {}).get(day) is not None}
            trade_cost = sum(abs(enter.get(s, 0.0) - now.get(s, 0.0)) for s in set(now) | set(enter)) * unit
            held = {s: (w, market.bars[s][day].open) for s, w in enter.items()}
        if not held and not trade_cost:
            continue
        gross = funding = exit_cost = 0.0
        gone: list[str] = []
        for symbol, (w, entry) in held.items():
            bars = market.bars[symbol]
            today, tomorrow = bars.get(day), bars.get(day + timedelta(days=1))
            if today is None:
                gone.append(symbol)
                continue
            if tomorrow is None:                      # the coin stops here: out at today's close
                gross += w * (today.close - today.open) / entry
                exit_cost += abs(w) * today.close / entry * unit
                gone.append(symbol)
            else:
                gross += w * (tomorrow.open - today.open) / entry
            rate, _known = xsb.funding_on(market, symbol, day)
            funding += -w * today.open / entry * rate
        for symbol in gone:
            held.pop(symbol)
        daily[day] = {"gross": gross, "cost": trade_cost + exit_cost, "funding": funding,
                      "net": gross - trade_cost - exit_cost + funding}
    return {"daily": daily}


ACCOUNTING = {"engine": xsb.simulate, "held": simulate_held}


# --- the two rungs after the map ---------------------------------------------------------------------------------

def monthly_members(market: xsb.Market, span: tuple[date, date] = SPAN) -> dict[date, list[str]]:
    return lab.tier_members(market, span)["top30"]


def stability(years_total_pct: Mapping[str, float]) -> dict[str, Any]:
    """Rung 3 from the yearly returns on total position: their mean against half the entry return, and how many
    are negative."""
    values = list(years_total_pct.values())
    mean = statistics.fmean(values) if values else None
    negative = sum(1 for v in values if v < 0)
    floor = bs.ENTRY_RETURN_TOTAL_PCT * STABILITY_SHARE
    return {"mean_year_total_position_pct": round(mean, 2) if mean is not None else None, "negative_years": negative,
            "floor_pct": floor, "passes": mean is not None and mean >= floor and negative <= MAX_NEGATIVE_YEARS}


def measure(market: xsb.Market, weigher: Callable[..., dict[str, float]], span: tuple[date, date] = SPAN,
            *, simulate: Callable[..., Any] = xsb.simulate) -> dict[str, Any]:
    """One book over the span, net of the engine's costs and funding: rung 2 (the entry rule) and rung 3."""
    start, end = span
    sim = simulate(market, bs.CADENCE, monthly_members(market, span), start=start, end=end, weigher=weigher)
    sharpe, values = bs.annual_sharpe(sim["daily"], start, end)
    report: dict[str, Any] = {"annual_sharpe": sharpe, "sum_pct": round(sum(values) * 100, 2), "months": len(values)}
    report["total_position_annual_pct"] = bs.total_position_annual_pct(report["sum_pct"], report["months"])
    report["clears_entry"] = bs.clears_entry(report)
    years = sorted({d.year for d in sim["daily"]})
    report["years_total_position_pct"] = {
        str(y): round(sum(r["net"] for d, r in sim["daily"].items() if d.year == y) * 100 / bs.POSITION_TOTAL, 2)
        for y in years}
    report["cost_pct"] = round(sum(r.get("cost", 0.0) for r in sim["daily"].values()) * 100, 2)
    report["funding_pct"] = round(sum(r.get("funding", 0.0) for r in sim["daily"].values()) * 100, 2)
    report["stability"] = stability(report["years_total_position_pct"])
    return report


# --- L2 ----------------------------------------------------------------------------------------------------------

def sundays(span: tuple[date, date]) -> list[date]:
    return [d for d in xsb.held_days(span[0] - timedelta(days=1), span[1]) if xsb.is_rebalance_day(bs.CADENCE, d)]


def decision_ics(market: xsb.Market, read: lab.Variable, monthly: Mapping[date, Sequence[str]],
                 span: tuple[date, date] = SPAN) -> dict[date, float]:
    """The variable's rank correlation with the week that follows, per decision that has one."""
    out = {}
    for day in lab.decisions(span, lab.HORIZONS[0]):
        pairs = []
        for symbol in monthly.get(date(day.year, day.month, 1), ()):
            value, forward = read(market, symbol, day), lab.forward_return(market, symbol, day, lab.HORIZONS[0])
            if value is not None and forward is not None:
                pairs.append((value, forward))
        if len(pairs) >= lab.MIN_NAMES and (ic := lab.rank_correlation(pairs)) is not None:
            out[day] = ic
    return out


def learned_signs(ics: Mapping[date, float], labels: Mapping[date, str | None], *,
                  without_year: int | None) -> dict[str, int]:
    """Each scenario's direction: the sign of the mean rank correlation over its decisions, read without the year
    being traded (a decision whose week runs into that year is left out too). Under
    :data:`signal_lab.MIN_DECISIONS` decisions a scenario gets no direction and the book is flat in it."""
    by_label: dict[str, list[float]] = {}
    for day, ic in ics.items():
        label = labels.get(day)
        ends = day + timedelta(days=1 + lab.HORIZONS[0])
        if label is None or (without_year is not None and without_year in (day.year, ends.year)):
            continue
        by_label.setdefault(label, []).append(ic)
    return {label: (0 if len(values) < lab.MIN_DECISIONS else (1 if statistics.fmean(values) > 0 else -1))
            for label, values in sorted(by_label.items())}


def switching(ics: Mapping[date, float], labels: Mapping[date, str | None], years: Sequence[int],
              *, held_out: bool) -> tuple[Direction, dict[str, dict[str, int]]]:
    """The direction of each day, and the signs it came from. ``held_out`` learns a year's signs from the others;
    otherwise one set of signs is learned from every year (in-sample, shown for comparison only)."""
    signs = {str(y): learned_signs(ics, labels, without_year=y if held_out else None) for y in years}

    def direction(day: date) -> int:
        label = labels.get(day)
        return 0 if label is None else signs.get(str(day.year), {}).get(label, 0)
    return direction, signs


def shuffled_spells(labels: Sequence[str | None], seed: str) -> list[str | None]:
    """The same spells in another order: a placebo scenario with the real one's persistence and shares."""
    runs: list[list[str | None]] = []
    for label in labels:
        if runs and runs[-1][0] == label:
            runs[-1].append(label)
        else:
            runs.append([label])
    random.Random(seed).shuffle(runs)
    return [label for run in runs for label in run]


# --- rung 4 ------------------------------------------------------------------------------------------------------

def confirm_bar(read: int, span: tuple[date, date] = CONFIRM) -> float:
    """The annual Sharpe the ``read``-th confirm read must reach: the one-sided normal bound at alpha / read over the
    span's length, and never under the entry rule's 1.0 (which is what the first read is held to)."""
    years = ((span[1] - span[0]).days + 1) / 365.25
    return round(max(bs.ENTRY_SHARPE, statistics.NormalDist().inv_cdf(1 - CONFIRM_ALPHA / read) / years ** 0.5), 3)


def next_confirm_read() -> dict[str, Any] | None:
    """The registered read that has not been spent, or None when there is none or the budget is used."""
    open_reads = [r for r in CONFIRM_READS if r["read"] not in CONFIRM_DONE]
    if not open_reads or len(CONFIRM_DONE) >= CONFIRM_BUDGET:
        return None
    return open_reads[0]


def _confirm_frame(market: xsb.Market, entry: Mapping[str, Any]):
    """The candidate's variable and its direction by day over the confirm span. Labels are read on the whole history
    up to the span's end, so a decision in 2025 is compared with the weeks before it."""
    candidate = CANDIDATES[entry["candidate"]]
    whole = (SPAN[0], CONFIRM[1])
    days = sundays(whole)
    labels = {day: row.get(candidate["axis"])
              for day, row in lab.scenario_labels(market, monthly_members(market, whole), days, whole).items()}
    signs = dict(entry["signs"])

    def direction(day: date) -> int:
        label = labels.get(day)
        return 0 if label is None else signs.get(label, 0)
    return lab.variables()[candidate["variable"]][1], direction, labels


def confirm_counts(market: xsb.Market, entry: Mapping[str, Any]) -> dict[str, Any]:
    """What the read will stand on, with no return in it: the decisions in the span, how many carry each label, and
    how many coins the variable ranks at each."""
    read, _, labels = _confirm_frame(market, entry)
    monthly = monthly_members(market, CONFIRM)
    days = [d for d in sundays(CONFIRM) if CONFIRM[0] - timedelta(days=1) <= d <= CONFIRM[1]]
    ranked = [sum(1 for s in monthly.get(date(d.year, d.month, 1), ()) if read(market, s, d) is not None) for d in days]
    by_label: dict[str, int] = {}
    for d in days:
        by_label[str(labels.get(d))] = by_label.get(str(labels.get(d)), 0) + 1
    return {"decisions": len(days), "by_label": dict(sorted(by_label.items())),
            "coins_ranked": {"min": min(ranked), "median": statistics.median(ranked), "max": max(ranked),
                             "decisions_under_the_floor": sum(1 for n in ranked if n < lab.MIN_NAMES)},
            "last_day_in_market": lab.last_day(market).isoformat()}


def confirm(market: xsb.Market, entry: Mapping[str, Any], *, simulate: Callable[..., Any] = xsb.simulate) -> dict[str, Any]:
    """The one read: the candidate as registered over the confirm span, net of the engine's costs, against the bar of
    its read number. Two never-switching books are shown beside it and decide nothing."""
    read, direction, _ = _confirm_frame(market, entry)
    report = measure(market, book(read, direction), CONFIRM, simulate=simulate)
    bar = confirm_bar(entry["read"])
    sharpe = report["annual_sharpe"]
    beside = {name: measure(market, book(read, lambda _day, way=way: way), CONFIRM, simulate=simulate)["annual_sharpe"]
              for name, way in (("always_long_high", 1), ("always_long_low", -1))}
    return {"read": entry["read"], "of": CONFIRM_BUDGET, "candidate": entry["candidate"], "signs": dict(entry["signs"]),
            "bar_annual_sharpe": bar, "verdict": "CONFIRM_PASS" if sharpe is not None and sharpe >= bar else "CONFIRM_FAIL",
            **report, "never_switching_books": beside}


# --- the round ---------------------------------------------------------------------------------------------------

def _spread(values: Sequence[float | None]) -> dict[str, Any]:
    kept = sorted(v for v in values if v is not None)
    return {"count": len(kept), "mean": round(statistics.fmean(kept), 3) if kept else None,
            "max": kept[-1] if kept else None}


def run_l1(market: xsb.Market, candidate: Mapping[str, str], span: tuple[date, date] = SPAN,
           *, simulate: Callable[..., Any] | None = None) -> dict[str, Any]:
    """A one-direction book (L1 or L8) and the same book built on random variables."""
    simulate = simulate or ACCOUNTING[candidate.get("accounting", "engine")]
    build = BOOKS[candidate["logic"]]
    read = lab.variables()[candidate["variable"]][1]
    way = 1 if candidate["long"] == "high" else -1
    report = measure(market, build(read, lambda _day: way), span, simulate=simulate)
    names = list(lab.controls())
    picked = names[:RANDOM_BOOKS] + names[lab.CONTROLS_WEEKLY:lab.CONTROLS_WEEKLY + RANDOM_BOOKS]
    sharpes = {name: measure(market, build(lab.controls()[name][1], lambda _day: 1), span, simulate=simulate)["annual_sharpe"]
               for name in picked}
    report["random_books"] = {
        "redrawn_weekly": _spread([v for k, v in sharpes.items() if "week" in k]),
        "fixed_per_coin": _spread([v for k, v in sharpes.items() if "fixed" in k]),
        "at_or_above_this_book": sum(1 for v in sharpes.values() if v is not None and report["annual_sharpe"] is not None
                                     and v >= report["annual_sharpe"]),
    }
    return report


def run_l2(market: xsb.Market, candidate: Mapping[str, str], span: tuple[date, date] = SPAN,
           *, simulate: Callable[..., Any] | None = None) -> dict[str, Any]:
    simulate = simulate or ACCOUNTING[candidate.get("accounting", "engine")]
    read = lab.variables()[candidate["variable"]][1]
    monthly = monthly_members(market, span)
    days = sundays(span)
    axis = candidate["axis"]
    labels = {day: row.get(axis) for day, row in lab.scenario_labels(market, monthly, days, span).items()}
    ics = decision_ics(market, read, monthly, span)
    years = sorted({d.year for d in days})
    learned, signs_all = switching(ics, labels, years, held_out=False)
    held, signs_held = switching(ics, labels, years, held_out=True)
    in_sample = measure(market, book(read, learned), span, simulate=simulate)
    held_out = measure(market, book(read, held), span, simulate=simulate)
    placebo = []
    for k in range(PLACEBO_SCENARIOS):
        fake = dict(zip(days, shuffled_spells([labels[d] for d in days], f"{SPEC}|placebo|{axis}|{k}")))
        direction, _ = switching(ics, fake, years, held_out=True)
        placebo.append(measure(market, book(read, direction), span, simulate=simulate)["annual_sharpe"])
    return {"in_sample": in_sample, "held_out": held_out,
            "signs_in_sample": signs_all[str(years[0])], "signs_held_out": signs_held,
            "decisions_with_a_label": sum(1 for d in days if labels[d] is not None), "decisions": len(days),
            "placebo_scenarios": {**_spread(placebo),
                                  "at_or_above_held_out": sum(1 for v in placebo if v is not None
                                                              and held_out["annual_sharpe"] is not None
                                                              and v >= held_out["annual_sharpe"])}}


def latest_round() -> str:
    return max(candidate["round"] for candidate in CANDIDATES.values())


def run_round(market: xsb.Market, span: tuple[date, date] = SPAN, *, which: str | None = None) -> dict[str, Any]:
    """Every candidate of one round (the latest by default): a round is measured once, when it is registered."""
    which = which or latest_round()
    out = {}
    for name, candidate in CANDIDATES.items():
        if candidate["round"] != which:
            continue
        body = run_l2(market, candidate, span) if candidate["logic"] == "L2" else run_l1(market, candidate, span)
        out[name] = {"candidate": dict(candidate), **body}
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="strategy_ladder", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("list", "run", "confirm-counts", "confirm"))
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    parser.add_argument("--round", default=None, help="default: the latest registered round")
    args = parser.parse_args(argv)
    if args.command == "list":
        print(json.dumps({"spec": SPEC, "span": [d.isoformat() for d in SPAN], "name_cap": NAME_CAP,
                          "candidates": CANDIDATES}, indent=1, ensure_ascii=False))
        return EXIT_OK
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    if args.command in ("confirm-counts", "confirm"):
        entry = next_confirm_read()
        if entry is None:
            print("refused: no registered confirm read is open (spent, or the budget is used)", file=sys.stderr)
            return EXIT_USAGE
        bs.check_search_span(CONFIRM)
        # Only what the candidate reads is loaded: its variable's group says whether the attached series are needed.
        group = lab.variables()[CANDIDATES[entry["candidate"]]["variable"]][0]
        market = lab.hold(cache, (SPAN[0], CONFIRM[1]), attach=group in ATTACHED_GROUPS)
        if market is None:
            print("BLOCKED: contract types or the spot symbol list is missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        body = confirm_counts(market, entry) if args.command == "confirm-counts" else confirm(market, entry)
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in CONFIRM], "command": args.command, **body},
                         indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_OK
    bs.check_development_span(SPAN)
    groups = {lab.variables()[c["variable"]][0] for c in CANDIDATES.values() if c["round"] == (args.round or latest_round())}
    market = lab.hold(cache, attach=bool(groups & ATTACHED_GROUPS))
    if market is None:
        print("BLOCKED: contract types or the spot symbol list is missing from the cache", file=sys.stderr)
        return EXIT_BLOCKED
    print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                      "span": [d.isoformat() for d in SPAN], "last_day_read": lab.last_day(market).isoformat(),
                      "net_of_costs": True, "judgement": None, "entry_rule": {
                          "annual_sharpe": bs.ENTRY_SHARPE, "total_position_annual_pct": bs.ENTRY_RETURN_TOTAL_PCT},
                      "round": args.round or latest_round(),
                      "candidates": run_round(market, which=args.round)}, indent=1, sort_keys=True, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
