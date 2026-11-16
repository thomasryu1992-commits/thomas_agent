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
import bisect
import csv
import functools
import io
import json
import math
import os
import random
import statistics
import sys
import zipfile
from array import array
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

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
# A position that would pay more than this a week in funding, at its last seven days' rate, is not held (L9). The
# lead's own size on the map is about 65 bps a week between the top and bottom thirds, so about half of that a side:
# a position paying it away has nothing left.
FUNDING_FLOOR = 0.0030
MIN_SIDE = 4                               # coins a side needs after the funding filter, or the book is flat
HOLD_ANCHOR = date(2020, 1, 5)             # a Sunday; multi-week holding counts decisions from here
CANDIDATES.update({
    # Round 3, registered 2026-11-13 before any was measured: the low-volatility lead through the two logics the
    # second round's deaths pointed at, and through both together.
    "C005": {"round": "3", "logic": "L9", "variable": "idio_vol_30", "long": "low", "tier": "top30", "accounting": "held",
             "from": "C004 paid about 5.7% a year in funding: the same book without the positions that pay it"},
    "C006": {"round": "3", "logic": "L8", "variable": "idio_vol_30", "long": "low", "tier": "top30", "accounting": "held4",
             "from": "the map's 28-day cells hold for this variable: the same book decided every fourth week"},
    "C007": {"round": "3", "logic": "L9", "variable": "idio_vol_30", "long": "low", "tier": "top30", "accounting": "held4",
             "from": "both changes together"},
    # Round 5, registered 2026-11-13 before any was measured: the map's new lead, and the tail-out universe for it
    # and for the low-volatility lead.
    "C008": {"round": "5", "logic": "L9", "variable": "taker_buy_share_30", "long": "high", "tier": "top30",
             "accounting": "held4", "from": "the fourth round's lead: coins bought aggressively go on to lead"},
    "C009": {"round": "5", "logic": "L9T", "variable": "taker_buy_share_30", "long": "high", "tier": "top30",
             "accounting": "held4", "from": "the same book without the tail of its universe"},
    "C010": {"round": "5", "logic": "L9T", "variable": "idio_vol_30", "long": "low", "tier": "top30",
             "accounting": "held4", "from": "C007, which lost 2026, without the tail of its universe"},
})

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
    # C007 holds one direction, so there is nothing to freeze but the candidate itself (its logic, its accounting
    # and their fixed values are in CANDIDATES and the constants above).
    {"read": 2, "candidate": "C007", "registered": "2026-11-13", "asked": "D6, Thomas 2026-11-13"},
    # C010 is C007 without its universe's tail. C007's result on this span is known (0.08; 2025 +15.3%, 2026 -11.9%),
    # so this read is a diagnosis of the cause as much as a test of the book.
    {"read": 3, "candidate": "C010", "registered": "2026-11-13", "asked": "D8, Thomas 2026-11-13"},
)
# read number -> the run that spent it. Filled in the commit that records the result; ``confirm`` refuses a read
# that is already here.
CONFIRM_DONE: dict[int, str] = {
    1: "20261113T042318Z-1057691 at a7ccd64b: C002 CONFIRM_FAIL, annual Sharpe -0.942 against 1.0",
    2: "20261113T052257Z-1129790 at c117234c: C007 CONFIRM_FAIL, annual Sharpe 0.08 against 1.245",
    3: "20261113T063506Z-1216563 at 339c2b91: C010 CONFIRM_FAIL, annual Sharpe 0.138 against 1.388",
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
                 *, long_high: bool, funding: Mapping[str, float | None] | None = None) -> dict[str, float]:
    """L1's sides with each coin's score divided by its volatility, so a wild coin carries no more risk than a quiet
    one; then the book's leftover sensitivity to Bitcoin is offset with a Bitcoin position. Coins without a
    volatility or a beta are not held.

    ``funding`` (L9) is each coin's funding over the last seven days. A coin is left out of the side it ranks on when
    holding it there would pay more than :data:`FUNDING_FLOOR` a week at that rate — a long in a coin with high
    positive funding, a short in one with deeply negative funding. Ranks are taken before the filter, so leaving a
    coin out does not move another across the middle; a side left with under :data:`MIN_SIDE` coins makes the book
    flat."""
    usable = {s: v for s, v in values.items() if vols.get(s) and betas.get(s) is not None}
    if len(usable) < lab.MIN_NAMES:
        return {}
    order = sorted(usable, key=lambda s: (usable[s], s))
    middle = (len(order) + 1) / 2
    scores = {s: (position - middle) * (1 if long_high else -1) / vols[s] for position, s in enumerate(order, start=1)}
    if funding is not None:
        def pays(symbol: str, score: float) -> bool:
            rate = funding.get(symbol)
            return rate is not None and (rate if score > 0 else -rate) > FUNDING_FLOOR
        scores = {s: v for s, v in scores.items() if v == 0 or not pays(s, v)}
        if min(sum(1 for v in scores.values() if v > 0), sum(1 for v in scores.values() if v < 0)) < MIN_SIDE:
            return {}
    longs = _capped({s: v for s, v in scores.items() if v > 0})
    shorts = _capped({s: -v for s, v in scores.items() if v < 0})
    weights = {**longs, **{s: -w for s, w in shorts.items()}}
    exposure = sum(w * betas[s] for s, w in weights.items())
    weights[HEDGE] = weights.get(HEDGE, 0.0) - exposure
    return weights


def risk_book(read: lab.Variable, direction: Direction, *, funding_aware: bool = False) -> Callable[..., dict[str, float]]:
    """A weigher for L8, and with ``funding_aware`` for L9: the 30-day volatility, the 30-day beta to Bitcoin and
    (L9) the last seven days' funding are read at the decision."""
    def weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                *, seed: str | None = None) -> dict[str, float]:
        way = direction(day)
        if way == 0:
            return {}
        values = {s: v for s in members if (v := read(market, s, day)) is not None}
        vols = {s: bs.FEATURES["vol"](market, s, day, lab.LOOKBACK) for s in values}
        betas = {s: (1.0 if s == HEDGE else bs.FEATURES["beta"](market, s, day, lab.LOOKBACK)) for s in values}
        funding = {s: xsb.trailing_funding(market, s, day, 7) for s in values} if funding_aware else None
        return risk_weights(values, vols, betas, long_high=way > 0, funding=funding)
    return weights


BOOKS = {"L1": book, "L2": book, "L8": risk_book, "L9": functools.partial(risk_book, funding_aware=True)}


# --- L11: the universe without its tail ----------------------------------------------------------------------------

TAIL_FUNDING_SHARE = 0.90                  # a coin whose |7-day funding| is above this share of the tier's is tail


def without_tail(market: xsb.Market, members: Sequence[str], day: date) -> list[str]:
    """The members that are not tail at ``day``: a coin is tail when it moved :data:`signal_lab.WILD_MOVE` or more in
    a day within the lookback, or when its last seven days' funding is larger in size than
    :data:`TAIL_FUNDING_SHARE` of the members'. The 2026 top thirty holds a handful of such coins and the
    development years' almost none (the universe's character, 2026-11-13); both confirm reads were lost there."""
    funding = {s: xsb.trailing_funding(market, s, day, 7) for s in members}
    sizes = [abs(v) for v in funding.values() if v is not None]
    bar = lab._percentile(sizes, TAIL_FUNDING_SHARE) if len(sizes) >= lab.MIN_NAMES else None
    kept = []
    for symbol in members:
        moves = lab._daily_moves(market, symbol, day, lab.LOOKBACK)
        wild = moves is not None and max(moves) >= lab.WILD_MOVE
        hot = bar is not None and funding[symbol] is not None and abs(funding[symbol]) > bar
        if not wild and not hot:
            kept.append(symbol)
    return kept


def tail_out(build: Callable[..., Callable[..., dict[str, float]]]) -> Callable[..., Callable[..., dict[str, float]]]:
    """A logic's book built on the members left after :func:`without_tail`."""
    def builder(read: lab.Variable, direction: Direction) -> Callable[..., dict[str, float]]:
        inner = build(read, direction)

        def weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                    *, seed: str | None = None) -> dict[str, float]:
            return inner(market, without_tail(market, members, day), day, signal, seed=seed)
        return weights
    return builder


BOOKS["L9T"] = tail_out(BOOKS["L9"])


# --- the ladder's own accounting ---------------------------------------------------------------------------------

def is_decision(signal: str, day: date, every: int) -> bool:
    """A weekly decision day, and with ``every`` above one only each ``every``-th of them, counted from
    :data:`HOLD_ANCHOR` so the calendar does not depend on where a run starts."""
    return xsb.is_rebalance_day(signal, day) and ((day - HOLD_ANCHOR).days // 7) % every == 0


def simulate_held(market: xsb.Market, signal: str, monthly: Mapping[date, Sequence[str]], *, start: date, end: date,
                  weigher: Callable[..., dict[str, float]], every: int = 1,
                  unit_of: Callable[[str], float] | None = None,
                  leg_of: Callable[[str, date], str] | None = None) -> dict[str, Any]:
    """Daily net returns of a book whose quantities are fixed at each decision and held to the next.

    ``cross_section_basket.simulate`` adds each day's return at the decision's dollar weights, which is a book
    re-set to those weights every day at no cost. On coins that move several hundred percent in a day the two
    differ by tens of points (the first confirm read, 2026-11-13). Here a coin bought at the entry open earns its own
    move from that open, a short that doubles is twice as large the next day, the trade at a decision is the
    difference between the target and what the held position has become, and funding is paid on what the position
    is worth that day. Costs per unit traded are the engine's. The engine itself is not changed: registered screens
    read through it. ``every`` holds each decision for that many weeks (L10: four). ``unit_of`` gives a coin its own
    cost per unit traded (L12); without it every coin pays the engine's, by the same arithmetic as before.
    ``leg_of`` names the leg a coin belongs to at a decision (section 46); each day's row then carries ``legs``,
    the same net split by leg. A trade is charged to the coin's leg at the new decision, or to the leg it leaves
    when it is no longer held. Nothing else changes: without ``leg_of`` the rows are what they always were."""
    unit = (xsb.cost.DEFAULT_TAKER_FEE_BPS + xsb.cost.DEFAULT_SLIPPAGE_BPS) / 10_000
    held: dict[str, tuple[float, float]] = {}            # coin -> (weight at entry, entry open)
    legs: dict[str, str] = {}                             # coin -> its leg at the decision it is held from
    daily: dict[date, dict[str, float]] = {}
    for day in xsb.held_days(start, end):
        decision = day - timedelta(days=1)
        trade_cost = 0.0
        if is_decision(signal, decision, every):
            target = weigher(market, monthly.get(date(decision.year, decision.month, 1), ()), decision, signal, seed=None)
            now = {s: w * bar.open / entry for s, (w, entry) in held.items()
                   if (bar := (market.bars.get(s) or {}).get(day)) is not None}
            enter = {s: w for s, w in target.items() if (market.bars.get(s) or {}).get(day) is not None}
            if unit_of is None:
                trade_cost = sum(abs(enter.get(s, 0.0) - now.get(s, 0.0)) for s in set(now) | set(enter)) * unit
            else:
                trade_cost = sum(abs(enter.get(s, 0.0) - now.get(s, 0.0)) * unit_of(s) for s in set(now) | set(enter))
            by_leg: dict[str, float] = {}
            if leg_of is not None:
                entering = {s: leg_of(s, decision) for s in enter}
                for s in set(now) | set(enter):
                    leg = entering[s] if s in entering else legs.get(s) or leg_of(s, decision)
                    by_leg[leg] = by_leg.get(leg, 0.0) - abs(enter.get(s, 0.0) - now.get(s, 0.0)) * (
                        unit if unit_of is None else unit_of(s))
                legs = entering
            held = {s: (w, market.bars[s][day].open) for s, w in enter.items()}
        else:
            by_leg = {}
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
            own_gross = own_exit = 0.0
            if tomorrow is None:                      # the coin stops here: out at today's close
                own_gross = w * (today.close - today.open) / entry
                own_exit = abs(w) * today.close / entry * (unit if unit_of is None else unit_of(symbol))
                gone.append(symbol)
            else:
                own_gross = w * (tomorrow.open - today.open) / entry
            rate, _known = xsb.funding_on(market, symbol, day)
            own_funding = -w * today.open / entry * rate
            gross, exit_cost, funding = gross + own_gross, exit_cost + own_exit, funding + own_funding
            if leg_of is not None:
                leg = legs[symbol]
                by_leg[leg] = by_leg.get(leg, 0.0) + own_gross - own_exit + own_funding
        for symbol in gone:
            held.pop(symbol)
        daily[day] = {"gross": gross, "cost": trade_cost + exit_cost, "funding": funding,
                      "net": gross - trade_cost - exit_cost + funding}
        if leg_of is not None:
            daily[day]["legs"] = by_leg
    return {"daily": daily}


ACCOUNTING = {"engine": xsb.simulate, "held": simulate_held, "held4": functools.partial(simulate_held, every=4)}
HOLD_WEEKS = {"engine": 1, "held": 1, "held4": 4}       # how many weeks each accounting holds a decision


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
            *, simulate: Callable[..., Any] = xsb.simulate, cadence: str = bs.CADENCE) -> dict[str, Any]:
    """One book over the span, net of the engine's costs and funding: rung 2 (the entry rule) and rung 3.
    ``cadence`` names the engine signal whose decision days are used (weekly unless a book says otherwise)."""
    start, end = span
    sim = simulate(market, cadence, monthly_members(market, span), start=start, end=end, weigher=weigher)
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


shuffled_spells = lab.shuffled_spells       # one placebo construction for the map's axes and L2's scenarios


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
    if candidate["logic"] != "L2":             # one direction: nothing is read from a scenario
        way = 1 if candidate["long"] == "high" else -1
        return lab.variables()[candidate["variable"]][1], (lambda _day: way), {}
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
    """What the read will stand on, with no return in it: the decisions in the span, how many carry each label (L2),
    how many coins the variable ranks, and how many the book would hold on each side."""
    candidate = CANDIDATES[entry["candidate"]]
    read, direction, labels = _confirm_frame(market, entry)
    monthly = monthly_members(market, CONFIRM)
    every = HOLD_WEEKS[candidate.get("accounting", "engine")]
    days = [d for d in sundays(CONFIRM) if CONFIRM[0] - timedelta(days=1) <= d <= CONFIRM[1]
            and is_decision(bs.CADENCE, d, every)]
    build = BOOKS[candidate["logic"]](read, direction)
    ranked, longs, shorts = [], [], []
    for d in days:
        members = monthly.get(date(d.year, d.month, 1), ())
        ranked.append(sum(1 for s in members if read(market, s, d) is not None))
        weights = {s: w for s, w in build(market, members, d, bs.CADENCE).items() if s != HEDGE or s in members}
        longs.append(sum(1 for w in weights.values() if w > 0))
        shorts.append(sum(1 for w in weights.values() if w < 0))
    out: dict[str, Any] = {
        "decisions": len(days), "hold_weeks": every,
        "coins_ranked": {"min": min(ranked), "median": statistics.median(ranked), "max": max(ranked),
                         "decisions_under_the_floor": sum(1 for n in ranked if n < lab.MIN_NAMES)},
        "coins_held": {"long_min": min(longs), "long_median": statistics.median(longs), "short_min": min(shorts),
                       "short_median": statistics.median(shorts),
                       "flat_decisions": sum(1 for a, b in zip(longs, shorts) if a == 0 and b == 0)},
        "last_day_in_market": lab.last_day(market).isoformat()}
    if labels:
        by_label: dict[str, int] = {}
        for d in days:
            by_label[str(labels.get(d))] = by_label.get(str(labels.get(d)), 0) + 1
        out["by_label"] = dict(sorted(by_label.items()))
    return out


def confirm(market: xsb.Market, entry: Mapping[str, Any], *, simulate: Callable[..., Any] | None = None) -> dict[str, Any]:
    """The one read: the candidate as registered over the confirm span, net of costs, against the bar of its read
    number. Beside it, deciding nothing: for a switching book the two never-switching ones, for a one-direction book
    the same logic built on random variables."""
    candidate = CANDIDATES[entry["candidate"]]
    simulate = simulate or ACCOUNTING[candidate.get("accounting", "engine")]
    build = BOOKS[candidate["logic"]]
    read, direction, _ = _confirm_frame(market, entry)
    report = measure(market, build(read, direction), CONFIRM, simulate=simulate)
    bar = confirm_bar(entry["read"])
    sharpe = report["annual_sharpe"]
    out = {"read": entry["read"], "of": CONFIRM_BUDGET, "candidate": entry["candidate"],
           "bar_annual_sharpe": bar, "verdict": "CONFIRM_PASS" if sharpe is not None and sharpe >= bar else "CONFIRM_FAIL",
           **report}
    if candidate["logic"] == "L2":
        out["signs"] = dict(entry["signs"])
        out["never_switching_books"] = {
            name: measure(market, build(read, lambda _day, way=way: way), CONFIRM, simulate=simulate)["annual_sharpe"]
            for name, way in (("always_long_high", 1), ("always_long_low", -1))}
    else:
        names = list(lab.controls())
        picked = names[:RANDOM_BOOKS] + names[lab.CONTROLS_WEEKLY:lab.CONTROLS_WEEKLY + RANDOM_BOOKS]
        sharpes = [measure(market, build(lab.controls()[name][1], lambda _day: 1), CONFIRM, simulate=simulate)["annual_sharpe"]
                   for name in picked]
        out["random_books_same_logic"] = {
            **_spread(sharpes),
            "at_or_above_this_book": sum(1 for v in sharpes if v is not None and sharpe is not None and v >= sharpe)}
    return out


# --- the book sweep: which variables make a book at all? ------------------------------------------------------------

SWEEP_LOGIC, SWEEP_ACCOUNTING = "L9T", "held4"       # the logic and accounting of the best book so far (C010)
SWEEP_NULLS = 600                          # random-variable books: enough for a p under what the correction asks of 60
SWEEP_NULLS_ROUND_6 = 300                  # the sweep of round 6 read these; they are the same books, by name, among the 400


def null_variables(count: int = SWEEP_NULLS) -> dict[str, tuple[str, lab.Variable]]:
    """Random variables for the sweep's null, half redrawn weekly and half fixed per coin, on seeds of their own
    (not the map's controls)."""
    half = count // 2
    out = {f"null_week_{k:03d}": ("null", lab._control("week", 1000 + k)) for k in range(half)}
    out.update({f"null_fixed_{k:03d}": ("null", lab._control("fixed", 1000 + k)) for k in range(count - half)})
    return out


def sweep(market: xsb.Market, names: Mapping[str, tuple[str, lab.Variable]], span: tuple[date, date] = SPAN,
          *, simulate: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Every variable in ``names`` built into the same book. The side it is long is the sign of its own mean rank
    correlation with the following week on the span — the same rule for a real variable and for a random one, so a
    random book gets the same benefit of picking its better-looking side."""
    simulate = simulate or ACCOUNTING[SWEEP_ACCOUNTING]
    monthly = monthly_members(market, span)
    out: dict[str, Any] = {}
    for name, (group, read) in names.items():
        ics = decision_ics(market, read, monthly, span)
        if len(ics) < lab.MIN_DECISIONS:
            out[name] = {"group": group, "skipped": "ranked at too few decisions"}
            continue
        mean = statistics.fmean(ics.values())
        way = 1 if mean > 0 else -1
        report = measure(market, BOOKS[SWEEP_LOGIC](read, lambda _day, way=way: way), span, simulate=simulate)
        out[name] = {"group": group, "long": "high" if way > 0 else "low", "mean_rank_correlation": round(mean, 4),
                     "decisions_ranked": len(ics), "annual_sharpe": report["annual_sharpe"],
                     "total_position_annual_pct": report["total_position_annual_pct"],
                     "clears_entry": report["clears_entry"], "negative_years": report["stability"]["negative_years"],
                     "funding_pct": report["funding_pct"]}
    return out


def sweep_reading(real: Mapping[str, Mapping[str, Any]], null: Mapping[str, Mapping[str, Any]], *, q: float = 0.10) -> dict[str, Any]:
    """Each real book beside the null books: its p is the share of null books at or above it. The books that count
    are the ones that survive Benjamini–Hochberg at ``q``; the number under 0.05 is shown beside what chance gives."""
    nulls = sorted(v["annual_sharpe"] for v in null.values() if v.get("annual_sharpe") is not None)
    rows = []
    for name, body in real.items():
        sharpe = body.get("annual_sharpe")
        if sharpe is None or not nulls:
            continue
        p = (1 + sum(1 for v in nulls if v >= sharpe)) / (1 + len(nulls))
        rows.append({"variable": name, **{k: body[k] for k in ("group", "long", "annual_sharpe", "total_position_annual_pct",
                                                               "clears_entry", "negative_years")}, "p": round(p, 4)})
    rows.sort(key=lambda r: (r["p"], -r["annual_sharpe"], r["variable"]))
    cutoff = max((i for i, r in enumerate(rows, start=1) if r["p"] <= q * i / len(rows)), default=0)
    return {"books": len(rows), "null_books": len(nulls),
            "null": {"mean": round(statistics.fmean(nulls), 3), "p95": nulls[int(0.95 * (len(nulls) - 1))], "max": nulls[-1],
                     "share_clearing_sharpe_1": round(sum(1 for v in nulls if v >= bs.ENTRY_SHARPE) / len(nulls), 4)} if nulls else None,
            "p_under_0.05": sum(1 for r in rows if r["p"] < 0.05), "expected_under_0.05_by_chance": round(0.05 * len(rows), 2),
            "q": q, "survive_benjamini_hochberg": rows[:cutoff], "all": rows}


# The sweep, one group at a time (section 44). Round 6 loaded positioning, depth and spot together; with both tiers'
# positioning files on disk that load no longer fits the sandbox, and the premium has a door of its own.
SWEEP_BARS_GROUPS = frozenset({"price_volume", "funding", "coin_character", "trade_flow"})
SWEEP_ALONE = {"positioning": "hold_positioning", "spot": "hold_spot", "premium": "hold_premium"}
SWEEP_ROW_KEYS = ("long", "annual_sharpe", "total_position_annual_pct", "decisions_ranked")


def sweep_market(cache: Path, wanted: set[str]) -> xsb.Market | None:
    """The market a sweep of ``wanted`` reads and nothing else. Raises ``ValueError`` for a set with no loader: a
    group whose series are not attached would come back "ranked at too few decisions" and look like a result."""
    if wanted <= SWEEP_BARS_GROUPS | {"null"}:
        return lab.hold(cache, attach=False, flow="trade_flow" in wanted)
    if len(wanted) == 1 and next(iter(wanted)) in SWEEP_ALONE:
        return getattr(lab, SWEEP_ALONE[next(iter(wanted))])(cache)
    raise ValueError(f"no loader for {sorted(wanted)}: {sorted(SWEEP_ALONE)} are swept alone, depth has no loader of its own")


def sweep_matches(earlier: Mapping[str, Mapping[str, Any]], later: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """The books both runs built whose numbers differ, by name. Empty when the later run reproduces the earlier."""
    return sorted(name for name in set(earlier) & set(later)
                  if any(earlier[name].get(k) != later[name].get(k) for k in SWEEP_ROW_KEYS))


def sweep_family(earlier: Mapping[str, Mapping[str, Any]], later: Mapping[str, Mapping[str, Any]],
                 null: Mapping[str, Mapping[str, Any]], *, q: float = 0.10) -> dict[str, Any]:
    """Every book ever swept beside ONE null set. ``earlier`` are the books of the first sweep as recorded, ``later``
    the books of the re-run groups (old and new variables); a book in both must agree, or nothing is read. Each
    row says whether its variable is new, and how many decisions ranked it."""
    differ = sweep_matches(earlier, later)
    if differ:
        return {"refused": "a re-run book differs from its record", "differ": differ}
    books = {**earlier, **later}
    reading = sweep_reading(books, null, q=q)
    for row in reading["all"]:
        row["new"] = row["variable"] not in earlier
        row["decisions_ranked"] = books[row["variable"]].get("decisions_ranked")
    skipped = sorted(name for name, body in books.items() if body.get("skipped"))
    return {**reading, "new_books": sum(1 for r in reading["all"] if r["new"]), "skipped": skipped,
            "new_p_under_0.05": sum(1 for r in reading["all"] if r["new"] and r["p"] < 0.05),
            "new_survivors": [r["variable"] for r in reading["survive_benjamini_hochberg"] if r["new"]]}


# --- a lead from the sweep, before any hypothesis form (section 45) -----------------------------------------------

# The book the sweep read, exactly: its variable and the side the sweep chose. Nothing here is a variant of it.
LEADS = {"spread_change_30": {"long": "low", "group": "price_volume"}}
# Books the lead's positions are laid beside, each long its LOW side in the sweep's logic. The rule reads the first:
# the closed volatility cluster's book (C010). The rest are printed for the form's "nearest record" line.
LEAD_KIN = ("idio_vol_30", "vol_30", "illiquidity_30", "momentum_30", "spread_30")
LEAD_KIN_MAX = 0.5                         # a median position overlap of this size, either sign, is the same book


def _cosine(a: Mapping[str, float], b: Mapping[str, float]) -> float | None:
    dot = sum(w * b.get(s, 0.0) for s, w in a.items())
    size = math.sqrt(sum(w * w for w in a.values())) * math.sqrt(sum(w * w for w in b.values()))
    return dot / size if size > 0 else None


def lead_check(market: xsb.Market, name: str, span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """What a lead's book holds, decision by decision, with no return read: how many of its coins share a value (a
    tie is broken by the coin's name, which is not the variable), how much of the book those coins carry, and how
    alike its positions are to each reference book's (the cosine of the two books' coin weights, hedge left out)."""
    registry, monthly = lab.variables(), monthly_members(market, span)
    read = registry[name][1]
    way = 1 if LEADS[name]["long"] == "high" else -1
    own = BOOKS[SWEEP_LOGIC](read, lambda _day: way)
    others = {kin: BOOKS[SWEEP_LOGIC](registry[kin][1], lambda _day: -1) for kin in LEAD_KIN}
    rows: dict[str, list[float]] = {"zero_share": [], "tied_share": [], "tied_weight_share": [], "coins_held": []}
    alike: dict[str, list[float]] = {kin: [] for kin in LEAD_KIN}
    dated: list[list[Any]] = []
    decisions = 0
    for day in xsb.held_days(*span):
        decision = day - timedelta(days=1)
        if not is_decision(bs.CADENCE, decision, HOLD_WEEKS[SWEEP_ACCOUNTING]):
            continue
        members = monthly.get(date(decision.year, decision.month, 1), ())
        weights = {s: w for s, w in own(market, members, decision, bs.CADENCE, seed=None).items() if s != HEDGE}
        if not weights:
            continue
        decisions += 1
        values = {s: v for s in without_tail(market, members, decision) if (v := read(market, s, decision)) is not None}
        seen: dict[float, int] = {}
        for v in values.values():
            seen[v] = seen.get(v, 0) + 1
        tied = {s for s, v in values.items() if seen[v] > 1}
        gross = sum(abs(w) for w in weights.values())
        rows["zero_share"].append(sum(1 for v in values.values() if v == 0) / len(values))
        rows["tied_share"].append(len(tied) / len(values))
        rows["tied_weight_share"].append(sum(abs(w) for s, w in weights.items() if s in tied) / gross)
        rows["coins_held"].append(float(len(weights)))
        dated.append([decision.isoformat(), round(rows["tied_share"][-1], 4), round(rows["tied_weight_share"][-1], 4)])
        for kin, build in others.items():
            other = {s: w for s, w in build(market, members, decision, bs.CADENCE, seed=None).items() if s != HEDGE}
            if (value := _cosine(weights, other)) is not None:
                alike[kin].append(value)
    def middle(values: Sequence[float]) -> float | None:
        return round(statistics.median(values), 4) if values else None
    kin = {k: {"decisions": len(v), "median": middle(v), "share_positive": round(sum(1 for x in v if x > 0) / len(v), 3) if v else None}
           for k, v in alike.items()}
    first = kin[LEAD_KIN[0]]["median"]
    return {"lead": name, "long": LEADS[name]["long"], "logic": SWEEP_LOGIC, "decisions_with_a_book": decisions,
            "medians": {k: middle(v) for k, v in rows.items()},
            "largest": {k: round(max(v), 4) if v else None for k, v in rows.items() if k != "coins_held"},
            "by_decision": dated,          # [decision, tied share of coins, tied share of weight]
            "position_overlap": kin, "kin_rule": {"beside": LEAD_KIN[0], "threshold": LEAD_KIN_MAX,
                                                  "kin": first is not None and abs(first) >= LEAD_KIN_MAX},
            "reads_returns": False}


LEAD_LEGS = ("untied", "tied", "hedge")
LEAD_UNTIED_SHARE = 0.5                    # of the book's net the untied coins must carry for the lead to be about the variable


def lead_attribution(market: xsb.Market, name: str, span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """The swept book's own net return split by leg: the coins whose value another coin shares at the decision
    (ordered by name, not by the variable), the coins with a value of their own, and the hedge coin. The same
    book, the same accounting, one run; the legs sum to the book's net day by day or nothing is returned."""
    registry, monthly = lab.variables(), monthly_members(market, span)
    read = registry[name][1]
    way = 1 if LEADS[name]["long"] == "high" else -1
    weigher = BOOKS[SWEEP_LOGIC](read, lambda _day: way)
    tied_at: dict[date, set[str]] = {}

    def leg_of(symbol: str, decision: date) -> str:
        if symbol == HEDGE:
            return "hedge"
        if decision not in tied_at:
            members = monthly.get(date(decision.year, decision.month, 1), ())
            values = {s: v for s in without_tail(market, members, decision) if (v := read(market, s, decision)) is not None}
            seen: dict[float, int] = {}
            for v in values.values():
                seen[v] = seen.get(v, 0) + 1
            tied_at[decision] = {s for s, v in values.items() if seen[v] > 1}
        return "tied" if symbol in tied_at[decision] else "untied"

    sim = ACCOUNTING[SWEEP_ACCOUNTING](market, bs.CADENCE, monthly, start=span[0], end=span[1], weigher=weigher, leg_of=leg_of)
    daily = sim["daily"]
    worst = max((abs(sum(row["legs"].values()) - row["net"]) for row in daily.values()), default=0.0)
    if worst > 1e-9:
        return {"refused": "the legs do not sum to the book's net", "largest_gap": worst}
    sharpe, months = bs.annual_sharpe(daily, *span)
    total = sum(months)
    years = sorted({d.year for d in daily})
    pct = lambda value: round(value * 100 / bs.POSITION_TOTAL, 2)                                  # noqa: E731
    legs = {leg: {"total_pct": pct(sum(row["legs"].get(leg, 0.0) for row in daily.values())),
                  "by_year_pct": {str(y): pct(sum(row["legs"].get(leg, 0.0) for d, row in daily.items() if d.year == y))
                                  for y in years}} for leg in LEAD_LEGS}
    book_total = pct(sum(row["net"] for row in daily.values()))
    untied = legs["untied"]["total_pct"]
    share = round(untied / book_total, 4) if book_total > 0 else None
    return {"lead": name, "long": LEADS[name]["long"], "logic": SWEEP_LOGIC, "accounting": SWEEP_ACCOUNTING,
            "annual_sharpe": sharpe, "total_position_annual_pct": bs.total_position_annual_pct(round(total * 100, 2), len(months)),
            "book_total_pct": book_total,
            "book_by_year_pct": {str(y): pct(sum(row["net"] for d, row in daily.items() if d.year == y)) for y in years},
            "legs": legs, "largest_gap": worst, "decisions_named": len(tied_at),
            "rule": {"untied_share_of_net": share, "threshold": LEAD_UNTIED_SHARE,
                     "about_the_variable": share is not None and share >= LEAD_UNTIED_SHARE}}


# --- the transfer check: does a book that looks good on earlier years earn in the next one? ---------------------

# (the years a book's side is chosen and its Sharpe is ranked on, the year it is then read on). All inside development.
TRANSFER_SPLITS: tuple[tuple[tuple[date, date], tuple[date, date]], ...] = (
    ((date(2020, 1, 1), date(2021, 12, 31)), (date(2022, 1, 1), date(2022, 12, 31))),
    ((date(2020, 1, 1), date(2022, 12, 31)), (date(2023, 1, 1), date(2023, 12, 31))),
    ((date(2020, 1, 1), date(2023, 12, 31)), (date(2024, 1, 1), date(2024, 12, 31))),
)
TRANSFER_DRAWS = 2000                      # sets of null books the size of the real set, for the carry statistic's p
TRANSFER_MIN_BOOKS = 8                     # books with both values a split needs before its carry is computed
TRANSFER_ALPHA = 0.05


def transfer(market: xsb.Market, names: Mapping[str, tuple[str, lab.Variable]],
             splits: Sequence[tuple[tuple[date, date], tuple[date, date]]] = TRANSFER_SPLITS,
             *, simulate: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Every variable built into the sweep's book once per split: its side is the sign of its mean rank correlation
    on the earlier years **only**, and that one book is measured on the earlier years and on the year after."""
    simulate = simulate or ACCOUNTING[SWEEP_ACCOUNTING]
    members = {span: monthly_members(market, span) for pair in splits for span in pair}
    out: dict[str, Any] = {}
    for name, (group, read) in names.items():
        rows: list[dict[str, Any] | None] = []
        for before, after in splits:
            ics = decision_ics(market, read, members[before], before)
            if len(ics) < lab.MIN_DECISIONS:
                rows.append(None)
                continue
            mean = statistics.fmean(ics.values())
            way = 1 if mean > 0 else -1
            weigher = BOOKS[SWEEP_LOGIC](read, lambda _day, way=way: way)
            early, late = measure(market, weigher, before, simulate=simulate), measure(market, weigher, after, simulate=simulate)
            later = decision_ics(market, read, members[after], after)
            rows.append({"long": "high" if way > 0 else "low", "train_rank_correlation": round(mean, 4),
                         "test_rank_correlation": round(statistics.fmean(later.values()), 4) if later else None,
                         "train_sharpe": early["annual_sharpe"], "train_clears_entry": early["clears_entry"],
                         "test_sharpe": late["annual_sharpe"], "test_total_position_pct": late["total_position_annual_pct"]})
        out[name] = {"group": group, "splits": rows}
    return out


def _carry(books: Sequence[Mapping[str, Any]], count: int) -> float | None:
    """Across books, the rank correlation between the earlier years' Sharpe and the next year's; the mean over the
    splits that have enough books."""
    values = []
    for k in range(count):
        pairs = [(row["train_sharpe"], row["test_sharpe"]) for row in (b["splits"][k] for b in books)
                 if row and row["train_sharpe"] is not None and row["test_sharpe"] is not None]
        if len(pairs) >= TRANSFER_MIN_BOOKS and (value := lab.rank_correlation(pairs)) is not None:
            values.append(value)
    return statistics.fmean(values) if values else None


def _cells(books: Mapping[str, Mapping[str, Any]], *, passed: bool | None = None) -> dict[str, Any]:
    """The (book, split) cells, all of them or the ones whose earlier years did or did not clear the entry rule:
    how many, and what the year after gave."""
    rows = [row for b in books.values() for row in b["splits"]
            if row and row["test_total_position_pct"] is not None and (passed is None or row["train_clears_entry"] is passed)]
    sharpes = [row["test_sharpe"] for row in rows if row["test_sharpe"] is not None]
    agree = [row for row in rows if row["test_rank_correlation"] is not None]
    return {"cells": len(rows),
            "test_total_position_pct_mean": round(statistics.fmean(r["test_total_position_pct"] for r in rows), 2) if rows else None,
            "test_sharpe_mean": round(statistics.fmean(sharpes), 3) if sharpes else None,
            "test_year_positive_share": round(sum(1 for r in rows if r["test_total_position_pct"] > 0) / len(rows), 3) if rows else None,
            "rank_correlation_keeps_its_sign_share": round(sum(
                1 for r in agree if (r["train_rank_correlation"] > 0) == (r["test_rank_correlation"] > 0)) / len(agree), 3) if agree else None}


def transfer_reading(real: Mapping[str, Mapping[str, Any]], null: Mapping[str, Mapping[str, Any]],
                     *, draws: int = TRANSFER_DRAWS, seed: str = "transfer") -> dict[str, Any]:
    """The real books' carry beside sets of null books of the same size (its p is the share of sets at or above it),
    and what the year after gave the books that had cleared the entry rule, beside the null books that had and the
    null books as a whole. The verdict is fixed in the proposal, not here."""
    count = max((len(b["splits"]) for b in real.values()), default=0)
    used = [b for b in real.values() if any(b["splits"])]
    value = _carry(used, count)
    pool, sets = list(null.values()), []
    if value is not None and len(pool) >= len(used):
        for i in range(draws):
            drawn = _carry(random.Random(f"{seed}:{i}").sample(pool, len(used)), count)
            if drawn is not None:
                sets.append(drawn)
    ordered = sorted(sets)
    out: dict[str, Any] = {
        "books": len(used), "null_books": len(pool), "splits": count,
        "carry": None if value is None else round(value, 4),
        "carry_by_split": [None if (v := _carry([{"splits": [b["splits"][k]]} for b in used], 1)) is None else round(v, 4)
                           for k in range(count)],
        "null_sets": len(sets),
        "null_carry": {"mean": round(statistics.fmean(ordered), 4), "p95": round(ordered[int(0.95 * (len(ordered) - 1))], 4),
                       "max": round(ordered[-1], 4)} if ordered else None,
        "p": round((1 + sum(1 for v in sets if v >= value)) / (1 + len(sets)), 4) if sets else None,
        "real": {"all": _cells(real), "cleared_entry_before": _cells(real, passed=True), "did_not": _cells(real, passed=False)},
        "null": {"all": _cells(null), "cleared_entry_before": _cells(null, passed=True), "did_not": _cells(null, passed=False)},
        "real_cells_that_cleared_entry_before": sorted(
            ({"variable": name, "split": k + 1, **{f: row[f] for f in ("long", "train_sharpe", "test_sharpe", "test_total_position_pct")}}
             for name, b in real.items() for k, row in enumerate(b["splits"]) if row and row["train_clears_entry"]),
            key=lambda r: (r["split"], -r["train_sharpe"]))}
    return out


# --- rung 2's added conditions (round 8) -------------------------------------------------------------------------

LATER_YEARS = tuple(after[0].year for _, after in TRANSFER_SPLITS)     # 2022, 2023, 2024: each read after the years before it
LATER_MAX_NEGATIVE = 1
NULL_ALPHA = 0.05                          # divided by the number of candidates a round registers


def later_years(years_total_pct: Mapping[str, float]) -> dict[str, Any]:
    """Rung 2(b): the book's return in each of :data:`LATER_YEARS`, every one of them decided on the years before it.
    Their mean must reach the stability floor and at most :data:`LATER_MAX_NEGATIVE` may be negative. For a book
    with nothing fitted this is its yearly returns again; the weight is on the p beside its null books."""
    values = [years_total_pct.get(str(y)) for y in LATER_YEARS]
    floor = bs.ENTRY_RETURN_TOTAL_PCT * STABILITY_SHARE
    if any(v is None for v in values):
        return {"years": dict(zip(map(str, LATER_YEARS), values)), "floor_pct": floor, "passes": False}
    mean, negative = statistics.fmean(values), sum(1 for v in values if v < 0)
    return {"years": dict(zip(map(str, LATER_YEARS), values)), "mean_pct": round(mean, 2), "negative": negative,
            "floor_pct": floor, "passes": mean >= floor and negative <= LATER_MAX_NEGATIVE}


def beside_nulls(value: float | None, nulls: Sequence[float | None], *, candidates: int = 1) -> dict[str, Any]:
    """Rung 2(c): the share of null books at or above the book, against :data:`NULL_ALPHA` over the round's
    candidates."""
    kept = sorted(v for v in nulls if v is not None)
    if value is None or not kept:
        return {"null_books": len(kept), "p": None, "alpha": NULL_ALPHA / candidates, "passes": False}
    p = (1 + sum(1 for v in kept if v >= value)) / (1 + len(kept))
    return {"null_books": len(kept), "p": round(p, 4), "alpha": NULL_ALPHA / candidates,
            "null": {"mean": round(statistics.fmean(kept), 3), "p95": kept[int(0.95 * (len(kept) - 1))], "max": kept[-1]},
            "passes": p < NULL_ALPHA / candidates}


# --- L12: the listing book (round 8, hypothesis H-L1) ------------------------------------------------------------

LISTING_ARCHIVE_START = date(2020, 1, 31)  # a first bar on or before it is the archive beginning, not a listing
LISTING_AGE = (7, 63)                      # days since the first bar at the decision: the second week through the ninth
LISTING_VOLUME_DAYS = 7
LISTING_VOLUME_FLOOR = 10_000_000.0        # median daily quote volume over those days, in dollars
LISTING_SLIPPAGE_BPS = 21.0                # STRESSED_COIN_COST_V0.1: the median at a 50k leg on a stressed day
LISTING_PLACEBOS = 300
LISTING_MIN_ACTIVE_SHARE = 0.6             # of the span's decisions the book must hold something at, or it is not measured
LISTING_MIN_FUNDING_KNOWN = 0.9            # of the eligible coin-decisions must have funding on file, or it is not measured
LISTING_MIN_EVENTS = 150


def listing_unit(symbol: str) -> float:
    """Cost per unit traded: the engine's taker fee, and for every coin but the hedge the stressed slippage."""
    slippage = xsb.cost.DEFAULT_SLIPPAGE_BPS if symbol == HEDGE else LISTING_SLIPPAGE_BPS
    return (xsb.cost.DEFAULT_TAKER_FEE_BPS + slippage) / 10_000


def _first_decision(first: date) -> date:
    day = first + timedelta(days=LISTING_AGE[0])
    while not xsb.is_rebalance_day(bs.CADENCE, day):
        day += timedelta(days=1)
    return day


class ListingPools:
    """Per decision day, read from that day and earlier: the coins in the listing window, the seasoned coins a
    placebo may draw from, and the listing-window coins left out for having no funding on file."""

    def __init__(self, market: xsb.Market) -> None:
        self.market = market
        self._at: dict[date, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {}

    def liquid(self, symbol: str, day: date) -> bool:
        bars = self.market.bars.get(symbol) or {}
        volumes = [bar.quote_volume for k in range(LISTING_VOLUME_DAYS) if (bar := bars.get(day - timedelta(days=k))) is not None]
        return (len(volumes) == LISTING_VOLUME_DAYS and min(volumes) > 0
                and statistics.median(volumes) >= LISTING_VOLUME_FLOOR)

    def at(self, day: date) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        if day not in self._at:
            new, seasoned, unfunded = [], [], []
            for symbol in sorted(self.market.bars):
                first = self.market.first_day(symbol)
                if (symbol == HEDGE or not symbol.endswith("USDT") or not xsb.is_coin(self.market, symbol)
                        or first is None or first > day or not self.liquid(symbol, day)):
                    continue
                age, known = (day - first).days, self.market.funding_day(symbol) is not None
                if age >= LISTING_AGE[1]:
                    if known:
                        seasoned.append(symbol)
                elif age >= LISTING_AGE[0] and first > LISTING_ARCHIVE_START:
                    (new if known else unfunded).append(symbol)
            self._at[day] = (tuple(new), tuple(seasoned), tuple(unfunded))
        return self._at[day]

    def partner(self, symbol: str, seed: str) -> str | None:
        """The seasoned coin a placebo holds in place of ``symbol``: drawn once, from the coins seasoned and liquid at
        the first decision the listing could be held at, so a placebo position lasts as long as a real one."""
        first = self.market.first_day(symbol)
        pool = self.at(_first_decision(first))[1] if first is not None else ()
        return random.Random(f"{seed}:{symbol}").choice(pool) if pool else None


def short_basket(names: Sequence[str]) -> dict[str, float]:
    """Equal shorts, none above :data:`NAME_CAP`, with the same dollars long in the hedge; flat under
    :data:`MIN_SIDE` names. No beta is estimated: a coin in its second week has no history to estimate one from."""
    names = sorted(set(names))
    if len(names) < MIN_SIDE:
        return {}
    weight = min(1 / len(names), NAME_CAP)
    return {**dict.fromkeys(names, -weight), HEDGE: weight * len(names)}


def listing_book(pools: ListingPools, placebo: str | None = None) -> Callable[..., dict[str, float]]:
    """L12: short every coin in its listing window. With ``placebo`` each of those coins is replaced by its seasoned
    partner (held only while the partner itself is liquid that day)."""
    def weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                *, seed: str | None = None) -> dict[str, float]:
        new, seasoned, _ = pools.at(day)
        if placebo is None:
            return short_basket(new)
        if len(new) < MIN_SIDE:
            return {}
        usable = set(seasoned)
        return short_basket([p for s in new if (p := pools.partner(s, placebo)) is not None and p in usable])
    return weights


def listing_counts(market: xsb.Market, span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """What the listing book would stand on, with no return in it: decisions, names held, events and how many
    eligible coin-decisions have no funding on file. The stop rules are judged here."""
    pools = ListingPools(market)
    days = [d for d in sundays(span) if span[0] - timedelta(days=1) <= d <= span[1]]
    years: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    held_total = unfunded_total = active = 0
    for day in days:
        new, seasoned, unfunded = pools.at(day)
        row = years.setdefault(str(day.year), {"decisions": 0, "active_decisions": 0, "names": [], "events": 0,
                                               "unfunded_coin_decisions": 0, "seasoned": []})
        row["decisions"] += 1
        row["events"] += len(set(new) - seen)
        seen.update(new)
        row["unfunded_coin_decisions"] += len(unfunded)
        row["seasoned"].append(len(seasoned))
        held_total, unfunded_total = held_total + len(new), unfunded_total + len(unfunded)
        if len(new) >= MIN_SIDE:
            row["active_decisions"] += 1
            row["names"].append(len(new))
            active += 1
    for row in years.values():
        names, seasoned = row.pop("names"), row.pop("seasoned")
        row["names_held_median"] = statistics.median(names) if names else 0
        row["seasoned_median"] = statistics.median(seasoned) if seasoned else 0
    firsts = [first for s in market.bars if (first := market.first_day(s)) is not None]
    known = held_total / (held_total + unfunded_total) if held_total + unfunded_total else None
    share = active / len(days) if days else None
    stops = [text for wrong, text in (
        (not firsts or min(firsts) > LISTING_ARCHIVE_START, "the archive does not begin where the definition assumes"),
        (known is None or known < LISTING_MIN_FUNDING_KNOWN, "too many listing-window coins have no funding on file"),
        (share is None or share < LISTING_MIN_ACTIVE_SHARE, "the book is flat at too many decisions"),
        (len(seen) < LISTING_MIN_EVENTS, "too few listings"),
    ) if wrong]
    return {"decisions": len(days), "active_share": None if share is None else round(share, 3), "events": len(seen),
            "funding_known_share": None if known is None else round(known, 3),
            "archive_first_day": min(firsts).isoformat() if firsts else None, "by_year": years,
            "last_day_in_market": lab.last_day(market).isoformat() if firsts else None,
            "stop": stops, "returns_computed": False}


def listing_run(market: xsb.Market, span: tuple[date, date] = SPAN, *, placebos: int = LISTING_PLACEBOS) -> dict[str, Any]:
    """The listing book net of its costs, beside the same book on placebo listings, through rung 2's three
    conditions and rung 3. Refused when the counts say stop."""
    counts = listing_counts(market, span)
    if counts["stop"]:
        return {"refused": counts["stop"], "counts": counts}
    pools = ListingPools(market)
    stressed = functools.partial(simulate_held, unit_of=listing_unit)
    report = measure(market, listing_book(pools), span, simulate=stressed)
    nulls = [measure(market, listing_book(pools, placebo=f"placebo:{k}"), span, simulate=stressed)["annual_sharpe"]
             for k in range(placebos)]
    rungs = {"2a_entry": report["clears_entry"], "2b_later_years": later_years(report["years_total_position_pct"]),
             "2c_beside_placebos": beside_nulls(report["annual_sharpe"], nulls), "3_stability": report["stability"]}
    return {**report, "hypothesis": "H-L1", "logic": "L12", "counts": counts,
            "at_engine_cost_annual_sharpe": measure(market, listing_book(pools), span, simulate=simulate_held)["annual_sharpe"],
            "rungs": rungs,
            "passes": bool(rungs["2a_entry"] and rungs["2b_later_years"]["passes"]
                           and rungs["2c_beside_placebos"]["passes"] and rungs["3_stability"]["passes"])}


# --- L13: the lower-tier reversal book (round 9, hypothesis H-R1) -------------------------------------------------

LOWER_TIER_FROM = 100                      # the tier begins after this many ranks of the month's trailing volume
REVERSAL_LOOKBACK = 7                      # days of return the book ranks on
REVERSAL_SKIP = 1                          # days between the last close ranked and the decision day's close
LOWER_NULLS = 300
LOWER_MIN_ACTIVE = 26                      # decisions with a book each of LATER_YEARS needs, or nothing is measured
LOWER_MIN_FUNDING_KNOWN = 0.9              # of the tier's liquid coin-decisions must have funding on file
LOWER_NULL_SEED = 2000                     # the weekly random variables of this null; the sweep's begin at 1000


def skip_day_return(market: xsb.Market, symbol: str, day: date) -> float | None:
    """The :data:`REVERSAL_LOOKBACK`-day return that ends :data:`REVERSAL_SKIP` day before ``day``. A book decided
    on ``day`` trades at the next open, which is the same instant as ``day``'s close; ranking on that close would
    let a last print at the bid or the ask pick the coin and then pay the book as it bounces back."""
    bars = market.bars.get(symbol) or {}
    last = bars.get(day - timedelta(days=REVERSAL_SKIP))
    first = bars.get(day - timedelta(days=REVERSAL_SKIP + REVERSAL_LOOKBACK))
    if last is None or first is None or first.close <= 0:
        return None
    return last.close / first.close - 1


class LowerTier:
    """Per decision day, read from that day and earlier: the coins ranked after the first :data:`LOWER_TIER_FROM`
    by the month's trailing volume that pass the listing book's liquidity floor, split into those with funding on
    file (held) and those without (left out and counted)."""

    def __init__(self, market: xsb.Market, span: tuple[date, date] = SPAN) -> None:
        self.market = market
        self._liquid = ListingPools(market).liquid
        ranked = xsb.universes(market, top_n=len(market.bars),
                               start=date(span[0].year, span[0].month, 1) - timedelta(days=1), end=span[1])
        self.monthly = {date(m.year, m.month, 1): tuple(members[LOWER_TIER_FROM:]) for m, members in ranked.items()}
        self._at: dict[date, tuple[tuple[str, ...], tuple[str, ...]]] = {}

    def at(self, day: date) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if day not in self._at:
            held, unfunded = [], []
            for symbol in self.monthly.get(date(day.year, day.month, 1), ()):
                if symbol == HEDGE or not symbol.endswith("USDT") or not self._liquid(symbol, day):
                    continue
                (held if self.market.funding_day(symbol) is not None else unfunded).append(symbol)
            self._at[day] = (tuple(held), tuple(unfunded))
        return self._at[day]


def lower_book(tier: LowerTier, read: lab.Variable, way: int) -> Callable[..., dict[str, float]]:
    """L1's rank weights on ``read`` over the tier's held coins at the decision; ``way`` below zero is long the low
    values. The members the simulation passes (the top thirty) are not read."""
    inner = book(read, lambda _day: way)

    def weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                *, seed: str | None = None) -> dict[str, float]:
        return inner(market, tier.at(day)[0], day, signal, seed=seed)
    return weights


def lower_counts(market: xsb.Market, span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """What the lower-tier book would stand on, with no return in it: decisions, how many have enough ranked coins
    for a book, and how many liquid coin-decisions lack funding. The stop rules are judged here."""
    tier = LowerTier(market, span)
    days = [d for d in sundays(span) if span[0] - timedelta(days=1) <= d <= span[1]]
    years: dict[str, dict[str, Any]] = {str(y): {"decisions": 0, "active_decisions": 0, "names": [],
                                                 "unfunded_coin_decisions": 0}
                                        for y in range(span[0].year, span[1].year + 1)}
    held_total = unfunded_total = 0
    first_active: date | None = None
    for day in days:
        row = years.get(str(day.year))
        if row is None:
            continue
        held, unfunded = tier.at(day)
        ranked = sum(1 for s in held if skip_day_return(market, s, day) is not None)
        row["decisions"] += 1
        row["unfunded_coin_decisions"] += len(unfunded)
        held_total, unfunded_total = held_total + len(held), unfunded_total + len(unfunded)
        if ranked >= lab.MIN_NAMES:
            row["active_decisions"] += 1
            row["names"].append(ranked)
            first_active = first_active or day
    for row in years.values():
        names = row.pop("names")
        row["names_ranked_median"] = statistics.median(names) if names else 0
    known = held_total / (held_total + unfunded_total) if held_total + unfunded_total else None
    thin = [str(y) for y in LATER_YEARS if years.get(str(y), {}).get("active_decisions", 0) < LOWER_MIN_ACTIVE]
    stops = [text for wrong, text in (
        (known is None or known < LOWER_MIN_FUNDING_KNOWN, "too many of the tier's coins have no funding on file"),
        (bool(thin), "the tier holds a book at too few decisions in " + ", ".join(thin)),
    ) if wrong]
    return {"decisions": len(days), "active_decisions": sum(r["active_decisions"] for r in years.values()),
            "first_active_decision": first_active.isoformat() if first_active else None,
            "funding_known_share": None if known is None else round(known, 3), "by_year": years,
            "last_day_in_market": lab.last_day(market).isoformat() if market.bars else None,
            "stop": stops, "returns_computed": False}


def lower_run(market: xsb.Market, span: tuple[date, date] = SPAN, *, nulls: int = LOWER_NULLS) -> dict[str, Any]:
    """The lower-tier reversal book net of stressed costs, beside the same book on weekly random variables, through
    rung 2's three conditions and rung 3. A year of the span the book is flat in counts as zero, never as absent.
    Refused when the counts say stop."""
    counts = lower_counts(market, span)
    if counts["stop"]:
        return {"refused": counts["stop"], "counts": counts}
    tier = LowerTier(market, span)
    stressed = functools.partial(simulate_held, unit_of=listing_unit)

    def whole(report: dict[str, Any]) -> dict[str, Any]:
        years = {str(y): report["years_total_position_pct"].get(str(y), 0.0)
                 for y in range(span[0].year, span[1].year + 1)}
        return {**report, "years_total_position_pct": years, "stability": stability(years)}

    report = whole(measure(market, lower_book(tier, skip_day_return, -1), span, simulate=stressed))
    null_sharpes = [measure(market, lower_book(tier, lab._control("week", LOWER_NULL_SEED + k), -1), span,
                            simulate=stressed)["annual_sharpe"] for k in range(nulls)]
    engine = whole(measure(market, lower_book(tier, skip_day_return, -1), span, simulate=simulate_held))
    rungs = {"2a_entry": report["clears_entry"], "2b_later_years": later_years(report["years_total_position_pct"]),
             "2c_beside_nulls": beside_nulls(report["annual_sharpe"], null_sharpes), "3_stability": report["stability"]}
    passes = bool(rungs["2a_entry"] and rungs["2b_later_years"]["passes"] and rungs["2c_beside_nulls"]["passes"]
                  and rungs["3_stability"]["passes"])
    lead = not passes and bool(engine["clears_entry"]) and rungs["2c_beside_nulls"]["passes"]
    return {**report, "hypothesis": "H-R1", "logic": "L13", "counts": counts, "rungs": rungs, "passes": passes,
            "at_engine_cost": {k: engine[k] for k in ("annual_sharpe", "total_position_annual_pct", "clears_entry",
                                                      "cost_pct", "years_total_position_pct")},
            "reading": "passes" if passes else "cost_dependent_lead" if lead else "closed"}


# --- L14: the crash-rebound event book (round 10, hypothesis H-C1) -----------------------------------------------

HOURLY_KIND = "klines-1h"
HOUR_MS = 3_600_000
CRASH_WINDOW_HOURS = 4                     # the reference coin's move over these hours makes an event
CRASH_THRESHOLD = -0.05
CRASH_RANK_HOURS = 24                      # coins are ranked on what they did over these hours, net of beta
CRASH_HOLD_HOURS = 72
CRASH_SPACING_HOURS = CRASH_HOLD_HOURS + 1  # the next event is looked for only after the hold: holds never overlap
CRASH_BETA_DAYS = 60
CRASH_SIDE_SHARE = 0.2
CRASH_MIN_RANKED = 20
CRASH_PLACEBOS = 300
CRASH_MIN_EVENTS = 30                      # over the span, or nothing is measured
CRASH_MIN_EVENTS_LATER_YEAR = 3            # in each of LATER_YEARS, or nothing is measured
CRASH_MIN_HOURLY_SHARE = 0.9               # of the universe's coin-events must have hourly bars on file


def hour_of(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()) // 3600


def day_of(hour: int) -> date:
    return datetime.fromtimestamp(hour * 3600, tz=timezone.utc).date()


class Hourly:
    """Hourly opens per symbol as flat arrays of floats (a missing hour is NaN): four hundred coins over five years
    as bar objects do not fit the sandbox. An hour is numbered from the epoch; the open of hour ``h`` is the price
    at that instant."""

    def __init__(self) -> None:
        self.first: dict[str, int] = {}
        self.opens: dict[str, array] = {}

    def put(self, symbol: str, prices: Mapping[int, float]) -> None:
        if prices:
            first, last = min(prices), max(prices)
            self.first[symbol] = first
            self.opens[symbol] = array("d", (prices.get(h, math.nan) for h in range(first, last + 1)))

    def open(self, symbol: str, hour: int) -> float | None:
        series = self.opens.get(symbol)
        if series is None:
            return None
        k = hour - self.first[symbol]
        if k < 0 or k >= len(series) or math.isnan(series[k]):
            return None
        return series[k]

    def last_open(self, symbol: str, after: int, upto: int) -> tuple[int, float] | None:
        """The latest hour in (after, upto] the coin has a bar at, and its open."""
        for hour in range(upto, after, -1):
            price = self.open(symbol, hour)
            if price is not None:
                return hour, price
        return None


def read_hourly(folder: Path, *, before: int | None = None) -> dict[int, float]:
    """``{hour: open}`` from one symbol's monthly 1h zips; nothing at or after hour ``before``."""
    out: dict[int, float] = {}
    for path in sorted(folder.glob("*.zip")) if folder.exists() else []:
        for row in xsb._rows(path):
            try:
                stamp, price = float(row[0]), float(row[1])
            except (ValueError, IndexError):
                continue          # the header row
            if stamp > 1e14:      # microseconds
                stamp /= 1000
            hour = int(stamp // HOUR_MS)
            if price > 0 and (before is None or hour < before):
                out[hour] = price
    return out


def load_hourly(archive: Path, symbols: Iterable[str], span: tuple[date, date] = SPAN) -> Hourly:
    """The symbols' hourly opens, cut at the span's last day: an hour after it is never read."""
    hourly, before = Hourly(), hour_of(span[1] + timedelta(days=1))
    for symbol in sorted(set(symbols)):
        hourly.put(symbol, read_hourly(archive / HOURLY_KIND / symbol, before=before))
    return hourly


def top_hundred(market: xsb.Market, span: tuple[date, date] = SPAN) -> dict[date, tuple[str, ...]]:
    tiers = lab.tier_members(market, span)
    return {m: tuple(tiers["top30"][m]) + tuple(tiers["rank31_100"].get(m, ())) for m in tiers["top30"]}


def crash_events(hourly: Hourly, span: tuple[date, date] = SPAN) -> list[int]:
    """Hours at which the reference coin stands :data:`CRASH_THRESHOLD` or more below where it stood
    :data:`CRASH_WINDOW_HOURS` earlier — the first such hour, then none until the hold after it is over, and none
    whose hold would run past the span."""
    out: list[int] = []
    hour = hour_of(span[0]) + max(CRASH_WINDOW_HOURS, CRASH_RANK_HOURS)
    last = hour_of(span[1] + timedelta(days=1)) - 1 - (1 + CRASH_HOLD_HOURS)
    while hour <= last:
        now, then = hourly.open(HEDGE, hour), hourly.open(HEDGE, hour - CRASH_WINDOW_HOURS)
        if now is not None and then is not None and now / then - 1 <= CRASH_THRESHOLD:
            out.append(hour)
            hour += CRASH_SPACING_HOURS
        else:
            hour += 1
    return out


def placebo_times(events: Sequence[int], span: tuple[date, date], hourly: Hourly, seed: str) -> list[int]:
    """As many hours in each year as that year has events, drawn where the reference coin has bars, spaced like
    events among themselves, and clear of every real event's ranking window and hold."""
    rng = random.Random(seed)
    out: list[int] = []
    clear = CRASH_SPACING_HOURS + CRASH_RANK_HOURS
    for year in range(span[0].year, span[1].year + 1):
        wanted = sum(1 for e in events if day_of(e).year == year)
        lo = max(hour_of(date(year, 1, 1)), hour_of(span[0])) + CRASH_RANK_HOURS
        hi = min(hour_of(date(year + 1, 1, 1)), hour_of(span[1] + timedelta(days=1))) - 1 - (1 + CRASH_HOLD_HOURS)
        drawn: list[int] = []
        for _ in range(20_000):
            if len(drawn) == wanted or hi < lo:
                break
            hour = rng.randint(lo, hi)
            if (hourly.open(HEDGE, hour) is None or hourly.open(HEDGE, hour - CRASH_RANK_HOURS) is None
                    or any(abs(hour - e) < clear for e in events)
                    or any(abs(hour - other) < clear for other in out + drawn)):
                continue
            drawn.append(hour)
        out += drawn
    return sorted(out)


def crash_book(market: xsb.Market, hourly: Hourly, members: Sequence[str],
               hour: int) -> tuple[dict[str, float], float] | None:
    """The book decided at ``hour``: every member with hourly bars, funding and a beta is ranked on its move over the
    :data:`CRASH_RANK_HOURS` to ``hour`` less its beta times the reference coin's; the lowest fifth is bought and the
    highest sold, equal sizes, each side one. With it, the reference-coin leg that cancels the book's net beta.
    Nothing after ``hour`` is read except that the coin has a bar at the entry hour."""
    now, then = hourly.open(HEDGE, hour), hourly.open(HEDGE, hour - CRASH_RANK_HOURS)
    if now is None or then is None:
        return None
    move, day = now / then - 1, day_of(hour) - timedelta(days=1)
    residual: dict[str, float] = {}
    betas: dict[str, float] = {}
    for symbol in members:
        if symbol == HEDGE or market.funding_day(symbol) is None or hourly.open(symbol, hour + 1) is None:
            continue
        here, before = hourly.open(symbol, hour), hourly.open(symbol, hour - CRASH_RANK_HOURS)
        beta = bs._beta(market, symbol, day, CRASH_BETA_DAYS) if here is not None and before is not None else None
        if beta is None:
            continue
        residual[symbol], betas[symbol] = here / before - 1 - beta * move, beta
    if len(residual) < CRASH_MIN_RANKED:
        return None
    order = sorted(residual, key=lambda s: (residual[s], s))
    side = max(MIN_SIDE, int(len(order) * CRASH_SIDE_SHARE))
    weights = {**{s: 1 / side for s in order[:side]}, **{s: -1 / side for s in order[-side:]}}
    return weights, -sum(w * betas[s] for s, w in weights.items())


class _FundingIndex:
    """Settlement times and running sums per symbol, so a hold's funding is two lookups."""

    def __init__(self, market: xsb.Market) -> None:
        self.market = market
        self._rows: dict[str, tuple[list[int], list[float]]] = {}

    def between(self, symbol: str, after_ms: int, upto_ms: int) -> float:
        if symbol not in self._rows:
            rows = self.market.funding.get(symbol) or []
            total, sums = 0.0, [0.0]
            for _, rate in rows:
                total += rate
                sums.append(total)
            self._rows[symbol] = ([ms for ms, _ in rows], sums)
        times, sums = self._rows[symbol]
        return sums[bisect.bisect_right(times, upto_ms)] - sums[bisect.bisect_right(times, after_ms)]


def crash_event(hourly: Hourly, funding: _FundingIndex, weights: Mapping[str, float], hedge: float, hour: int,
                *, unit_of: Callable[[str], float] = None) -> dict[str, float] | None:
    """One event's result per unit of one side: in at the open of the hour after ``hour`` — an hour after the print
    that ranked the coins — and out :data:`CRASH_HOLD_HOURS` later. A coin whose bars stop inside the hold leaves at
    its last open and its loss is counted; it is never dropped. Cost is paid on the way in and on the way out."""
    unit_of = unit_of or listing_unit
    enter, leave = hour + 1, hour + 1 + CRASH_HOLD_HOURS
    legs = dict(weights)
    if hedge:
        legs[HEDGE] = legs.get(HEDGE, 0.0) + hedge
    gross = cost = paid = 0.0
    for symbol, weight in legs.items():
        entry = hourly.open(symbol, enter)
        if entry is None:
            if symbol == HEDGE:
                return None
            continue
        out = hourly.last_open(symbol, enter, leave) or (enter, entry)
        gross += weight * (out[1] / entry - 1)
        cost += abs(weight) * unit_of(symbol) * 2
        paid += -weight * funding.between(symbol, enter * HOUR_MS, out[0] * HOUR_MS)
    return {"gross": gross, "cost": cost, "funding": paid, "net": gross - cost + paid}


def crash_daily(market: xsb.Market, hourly: Hourly, monthly: Mapping[date, Sequence[str]], times: Sequence[int],
                *, hedged: bool = True) -> dict[date, dict[str, float]]:
    """Each event's result on the day its hold ends."""
    funding, daily = _FundingIndex(market), {}
    for hour in times:
        day = day_of(hour)
        built = crash_book(market, hourly, monthly.get(date(day.year, day.month, 1), ()), hour)
        result = crash_event(hourly, funding, built[0], built[1] if hedged else 0.0, hour) if built else None
        if result is None:
            continue
        row = daily.setdefault(day_of(hour + 1 + CRASH_HOLD_HOURS), {"gross": 0.0, "cost": 0.0, "funding": 0.0, "net": 0.0})
        for key, value in result.items():
            row[key] += value
    return daily


def crash_counts(market: xsb.Market | None, hourly: Hourly, span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """What the crash book would stand on, with no return of the book in it. With ``market`` None only the events
    are counted (the reference coin's bars are enough for that); with it, also how much of the universe has hourly
    bars at each event and how many events a book stands at. The stop rules are judged here."""
    events = crash_events(hourly, span)
    years = {str(y): {"events": 0} for y in range(span[0].year, span[1].year + 1)}
    for hour in events:
        years[str(day_of(hour).year)]["events"] += 1
    standing, share = events, None
    if market is not None:
        monthly = top_hundred(market, span)
        asked = found = 0
        standing = []
        for row in years.values():
            row.update({"events_with_a_book": 0, "ranked": []})
        for hour in events:
            day = day_of(hour)
            members = [s for s in monthly.get(date(day.year, day.month, 1), ()) if s != HEDGE]
            asked, found = asked + len(members), found + sum(1 for s in members if hourly.open(s, hour) is not None)
            built = crash_book(market, hourly, members, hour)
            if built is not None:
                standing.append(hour)
                years[str(day.year)]["events_with_a_book"] += 1
                years[str(day.year)]["ranked"].append(len(built[0]))
        for row in years.values():
            ranked = row.pop("ranked")
            row["positions_median"] = statistics.median(ranked) if ranked else 0
        share = found / asked if asked else None
    per_year = {str(y): sum(1 for h in standing if day_of(h).year == y) for y in LATER_YEARS}
    thin = [y for y, n in per_year.items() if n < CRASH_MIN_EVENTS_LATER_YEAR]
    stops = [text for wrong, text in (
        (len(standing) < CRASH_MIN_EVENTS, "too few events over the span"),
        (bool(thin), "too few events in " + ", ".join(thin)),
        (market is not None and (share is None or share < CRASH_MIN_HOURLY_SHARE),
         "too many of the universe's coins have no hourly bars at the events"),
    ) if wrong]
    return {"events": len(events), "events_with_a_book": len(standing) if market is not None else None,
            "by_year": years, "hourly_share": None if share is None else round(share, 3),
            "event_hours": [datetime.fromtimestamp(h * 3600, tz=timezone.utc).strftime("%Y-%m-%dT%H:00Z") for h in events],
            "reference_only": market is None, "stop": stops, "returns_computed": False}


def crash_run(market: xsb.Market, hourly: Hourly, span: tuple[date, date] = SPAN,
              *, placebos: int = CRASH_PLACEBOS) -> dict[str, Any]:
    """The crash book net of stressed costs and funding, with its beta leg, beside the same book at placebo hours,
    through rung 2's three conditions and rung 3. A month or a year with no event counts as zero. Refused when the
    counts say stop."""
    counts = crash_counts(market, hourly, span)
    if counts["stop"]:
        return {"refused": counts["stop"], "counts": counts}
    monthly, events = top_hundred(market, span), crash_events(hourly, span)

    def read(times: Sequence[int], *, hedged: bool = True) -> dict[str, Any]:
        daily = crash_daily(market, hourly, monthly, times, hedged=hedged)
        report = measure(market, None, span, simulate=lambda *_a, **_k: {"daily": daily})
        years = {str(y): report["years_total_position_pct"].get(str(y), 0.0)
                 for y in range(span[0].year, span[1].year + 1)}
        return {**report, "years_total_position_pct": years, "stability": stability(years)}

    report, bare = read(events), read(events, hedged=False)
    nulls = [read(placebo_times(events, span, hourly, f"crash-placebo:{k}"))["annual_sharpe"] for k in range(placebos)]
    after = [out[1] / entry - 1 for h in events
             if (entry := hourly.open(HEDGE, h + 1)) is not None
             and (out := hourly.last_open(HEDGE, h + 1, h + 1 + CRASH_HOLD_HOURS)) is not None]
    rungs = {"2a_entry": report["clears_entry"], "2b_later_years": later_years(report["years_total_position_pct"]),
             "2c_beside_placebos": beside_nulls(report["annual_sharpe"], nulls), "3_stability": report["stability"]}
    return {**report, "hypothesis": "H-C1", "logic": "L14", "counts": counts, "rungs": rungs,
            "passes": bool(rungs["2a_entry"] and rungs["2b_later_years"]["passes"]
                           and rungs["2c_beside_placebos"]["passes"] and rungs["3_stability"]["passes"]),
            "without_the_beta_leg": {k: bare[k] for k in ("annual_sharpe", "total_position_annual_pct",
                                                          "years_total_position_pct")},
            "reference_after_events": {"events": len(after), "mean_pct": round(statistics.fmean(after) * 100, 2) if after else None,
                                       "positive": sum(1 for v in after if v > 0)}}


# --- the round ---------------------------------------------------------------------------------------------------

# --- L15: the deep-negative-funding book (round 11, hypothesis H-F1) -----------------------------------------------

DEEP_FUNDING = -0.005                      # a coin whose last DEEP_WINDOW days of funding sum to this or less is held
DEEP_WINDOW = 7
DEEP_NAME_CAP = 0.20                       # of the long side: five names fill the book, fewer leave part of it idle
DEEP_BETA_DAYS = 60
DEEP_NEIGHBOURS = 10                       # the matched placebo draws among this many coins nearest in trailing return
DEEP_PLACEBOS = 300
DEEP_MIN_ACTIVE = 26                       # decisions with a position each of LATER_YEARS needs, or nothing is measured
DEEP_MIN_FUNDING_KNOWN = 0.9               # of the pool's coin-decisions must have funding on file
DEEP_CARRY_SHARE = 0.5                     # of a passing book's return before costs the alts' funding must be: else price carried it
HOT_FUNDING = 0.01                         # L17 (round 14): a coin whose last DEEP_WINDOW days of funding sum to this or more is sold
GAP_DISCOUNT = -0.00075                    # L18: the perp's close under its own spot's, averaged over DEEP_WINDOW days.
#                                            The funding formula turns a standing premium of this size into DEEP_FUNDING a week
GAP_MIN_DAYS = 5                           # of the DEEP_WINDOW days must have both closes, or the coin is out of L18's pool


class DeepFunding:
    """Per decision day, read from that day and earlier: the pool (coin perps listed :data:`xsb.MIN_AGE_DAYS` or
    more, over the listing book's liquidity floor, with funding settled in the window and a beta), each with its
    trailing funding, beta and trailing return; and how many otherwise eligible coins have no funding on file."""

    def __init__(self, market: xsb.Market, threshold: float = DEEP_FUNDING) -> None:
        self.market = market
        self.threshold = threshold
        self.side = 1 if threshold < 0 else -1      # a coin paid to hold is bought; one that pays its holders is sold
        self._liquid = ListingPools(market).liquid
        self._at: dict[date, tuple[dict[str, tuple[float, float, float | None]], int]] = {}
        self._picks: dict[date, tuple[str, ...]] = {}
        self._start: dict[tuple[str, date], date] = {}
        self._others: dict[tuple[str, date, bool], tuple[str, ...]] = {}

    def at(self, day: date) -> tuple[dict[str, tuple[float, float, float | None]], int]:
        if day not in self._at:
            pool: dict[str, tuple[float, float, float | None]] = {}
            unfunded = 0
            for symbol in sorted(self.market.bars):
                first = self.market.first_day(symbol)
                if (symbol == HEDGE or not symbol.endswith("USDT") or not xsb.is_coin(self.market, symbol)
                        or first is None or first > day - timedelta(days=xsb.MIN_AGE_DAYS)
                        or not self._liquid(symbol, day)):
                    continue
                if self.market.funding_day(symbol) is None:
                    unfunded += 1
                    continue
                paid = self.reading(symbol, day)
                beta = bs._beta(self.market, symbol, day, DEEP_BETA_DAYS)
                if paid is not None and beta is not None:
                    pool[symbol] = (paid, beta, xsb.trailing_return(self.market, symbol, day, DEEP_WINDOW))
            self._at[day] = (pool, unfunded)
        return self._at[day]

    def reading(self, symbol: str, day: date) -> float | None:
        """What a coin is picked on: the funding settled over the window."""
        return xsb.trailing_funding(self.market, symbol, day, DEEP_WINDOW)

    def deep(self, value: float) -> bool:
        """Past the threshold, on the threshold's own side of zero."""
        return value <= self.threshold if self.threshold < 0 else value >= self.threshold

    def picks(self, day: date) -> tuple[str, ...]:
        if day not in self._picks:
            self._picks[day] = tuple(s for s, (paid, _, _) in self.at(day)[0].items() if self.deep(paid))
        return self._picks[day]

    def spell_start(self, symbol: str, day: date) -> date:
        """The first of the unbroken run of weekly decisions, ending at ``day``, at which ``symbol`` was a pick."""
        key = (symbol, day)
        if key not in self._start:
            before = day - timedelta(days=7)
            self._start[key] = self.spell_start(symbol, before) if symbol in self.picks(before) else day
        return self._start[key]

    def partner(self, symbol: str, day: date, seed: str, *, matched: bool) -> str | None:
        """The coin a placebo holds in place of ``symbol``: drawn once, at the first decision of the spell, from the
        pool's coins that are not picks that day, so a placebo position lasts as long as the real one. ``matched``
        draws among the :data:`DEEP_NEIGHBOURS` nearest to ``symbol`` in trailing return: a coin as beaten down,
        without the funding."""
        start = self.spell_start(symbol, day)
        key = (symbol, start, matched)
        if key not in self._others:
            pool = self.at(start)[0]
            others = sorted(s for s, (paid, _, _) in pool.items() if not self.deep(paid))
            own = pool[symbol][2] if symbol in pool else None
            if matched and own is not None:
                near = sorted((s for s in others if pool[s][2] is not None), key=lambda s: (abs(pool[s][2] - own), s))
                others = near[:DEEP_NEIGHBOURS] or others
            self._others[key] = tuple(others)
        others = self._others[key]
        return random.Random(f"{seed}:{symbol}:{start.isoformat()}").choice(others) if others else None


def deep_basket(names: Iterable[str], betas: Mapping[str, float], *, hedged: bool = True, side: int = 1) -> dict[str, float]:
    """Equal longs (``side`` -1: equal shorts), none above :data:`DEEP_NAME_CAP`, and the hedge coin against their
    summed beta. Under five names the rest of that side is idle and earns nothing."""
    names = sorted(set(names))
    if not names:
        return {}
    weight = side * min(1 / len(names), DEEP_NAME_CAP)
    out = dict.fromkeys(names, weight)
    hedge = -sum(weight * betas[s] for s in names)
    if hedged and hedge:
        out[HEDGE] = hedge
    return out


def deep_book(pools: DeepFunding, placebo: str | None = None, *, matched: bool = False,
              hedged: bool = True) -> Callable[..., dict[str, float]]:
    """L15: long every pool coin whose trailing funding is deep negative (L17, a pool with a positive threshold:
    short every coin whose funding is deep positive). With ``placebo`` each of those coins is replaced by its
    partner (held only while the partner itself is in the pool that day)."""
    def weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                *, seed: str | None = None) -> dict[str, float]:
        pool = pools.at(day)[0]
        names = pools.picks(day)
        if placebo is not None:
            names = [p for s in names if (p := pools.partner(s, day, placebo, matched=matched)) is not None and p in pool]
        return deep_basket(names, {s: pool[s][1] for s in names}, hedged=hedged, side=pools.side)
    return weights


def _median(values: Sequence[float | None]) -> float | None:
    kept = [v for v in values if v is not None]
    return round(statistics.median(kept), 4) if kept else None


def deep_counts(market: xsb.Market, span: tuple[date, date] = SPAN, *, pools: DeepFunding | None = None) -> dict[str, Any]:
    """What the deep-funding book would stand on, with nothing earned after a decision in it: decisions, names,
    spells, the picks' trailing funding (the signal), and how beaten down and how volatile the picks are beside the
    rest of the pool. The stop rules are judged here."""
    pools = pools or DeepFunding(market)
    days = [d for d in sundays(span) if span[0] - timedelta(days=1) <= d <= span[1]]
    years: dict[str, dict[str, Any]] = {str(y): {"decisions": 0, "active_decisions": 0, "names": [], "invested": [],
                                                 "spells": 0, "pool": [], "unfunded_coin_decisions": 0, "paid": [],
                                                 "picked": []}
                                        for y in range(span[0].year, span[1].year + 1)}
    pooled = unfunded_total = 0
    lengths: list[int] = []
    running: dict[str, int] = {}
    traits: dict[str, list[float | None]] = {"picks_back": [], "pool_back": [], "picks_vol": [], "pool_vol": []}
    for day in days:
        row = years.get(str(day.year))
        if row is None:
            continue
        pool, unfunded = pools.at(day)
        picks = pools.picks(day)
        row["decisions"] += 1
        row["pool"].append(len(pool))
        row["unfunded_coin_decisions"] += unfunded
        row["invested"].append(min(1.0, len(picks) * DEEP_NAME_CAP))
        if pool:
            row["picked"].append(len(picks) / len(pool))
        pooled, unfunded_total = pooled + len(pool), unfunded_total + unfunded
        for symbol in list(running):
            if symbol not in picks:
                lengths.append(running.pop(symbol))
        for symbol in picks:
            if symbol not in running:
                row["spells"] += 1
            running[symbol] = running.get(symbol, 0) + 1
            row["paid"].append(pool[symbol][0])
        if picks:
            row["active_decisions"] += 1
            row["names"].append(len(picks))
        for symbol, (_, _, back) in pool.items():
            side = "picks" if symbol in picks else "pool"
            traits[f"{side}_back"].append(back)
            traits[f"{side}_vol"].append(bs._vol(market, symbol, day, lab.LOOKBACK))
    lengths.extend(running.values())
    for row in years.values():
        names, invested, pool_sizes, paid = row.pop("names"), row.pop("invested"), row.pop("pool"), row.pop("paid")
        row["names_held_median"] = statistics.median(names) if names else 0
        row["invested_share_mean"] = round(statistics.fmean(invested), 3) if invested else 0.0
        row["pool_median"] = statistics.median(pool_sizes) if pool_sizes else 0
        row["picks_trailing_funding_mean"] = round(statistics.fmean(paid), 5) if paid else None
        picked = row.pop("picked")
        row["picked_share_of_pool_mean"] = round(statistics.fmean(picked), 3) if picked else None
    known = pooled / (pooled + unfunded_total) if pooled + unfunded_total else None
    thin = [str(y) for y in LATER_YEARS if years.get(str(y), {}).get("active_decisions", 0) < DEEP_MIN_ACTIVE]
    stops = [text for wrong, text in (
        (known is None or known < DEEP_MIN_FUNDING_KNOWN, "too many of the pool's coins have no funding on file"),
        (bool(thin), "the book holds a position at too few decisions in " + ", ".join(thin)),
    ) if wrong]
    return {"decisions": len(days), "active_decisions": sum(r["active_decisions"] for r in years.values()),
            "spells": len(lengths), "spell_weeks_median": statistics.median(lengths) if lengths else 0,
            "funding_known_share": None if known is None else round(known, 3), "by_year": years,
            "beside_the_pool": {"trailing_move_median": {"picks": _median(traits["picks_back"]), "pool": _median(traits["pool_back"])},
                                "volatility_median": {"picks": _median(traits["picks_vol"]), "pool": _median(traits["pool_vol"])}},
            "last_day_in_market": lab.last_day(market).isoformat() if market.bars else None,
            "stop": stops, "returns_computed": False}


def _before_costs(report: Mapping[str, Any]) -> float:
    return report["sum_pct"] + report["cost_pct"] - report["funding_pct"]


def deep_legs(report: Mapping[str, Any], bare: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """The book split into its alts and its hedge. The held accounting adds price, funding and cost coin by coin, so
    the hedge is exactly the book less the same alts held without it."""
    return {"alts": {"price_pct": round(_before_costs(bare), 2), "funding_pct": bare["funding_pct"], "cost_pct": bare["cost_pct"]},
            "hedge": {"price_pct": round(_before_costs(report) - _before_costs(bare), 2),
                      "funding_pct": round(report["funding_pct"] - bare["funding_pct"], 2),
                      "cost_pct": round(report["cost_pct"] - bare["cost_pct"], 2)}}


def deep_run(market: xsb.Market, span: tuple[date, date] = SPAN, *, placebos: int = DEEP_PLACEBOS,
             pools: DeepFunding | None = None, names: tuple[str, str, str] = ("H-F1", "L15", "not_apart_from_beaten_down_coins"),
             counter: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    """The deep-funding book net of stressed costs, beside the same book on placebo coins and on placebo coins as
    beaten down, through rung 2's three conditions and rung 3. A year the book is flat in counts as zero. Refused
    when the counts say stop. ``pools`` is the book's pool (L15's by default), ``names`` its hypothesis, logic and
    what a book not apart from the matched placebos is called, ``counter`` the counts whose stop rules refuse it."""
    pools = pools or DeepFunding(market)
    counts = counter(market, span) if counter else deep_counts(market, span, pools=pools)
    if counts["stop"]:
        return {"refused": counts["stop"], "counts": counts}
    stressed = functools.partial(simulate_held, unit_of=listing_unit)

    def whole(report: dict[str, Any]) -> dict[str, Any]:
        years = {str(y): report["years_total_position_pct"].get(str(y), 0.0)
                 for y in range(span[0].year, span[1].year + 1)}
        return {**report, "years_total_position_pct": years, "stability": stability(years)}

    report = whole(measure(market, deep_book(pools), span, simulate=stressed))
    bare = whole(measure(market, deep_book(pools, hedged=False), span, simulate=stressed))
    side = ("annual_sharpe", "total_position_annual_pct")
    plain_rows = [tuple(r[k] for k in side) for r in
                  (measure(market, deep_book(pools, placebo=f"plain:{k}"), span, simulate=stressed) for k in range(placebos))]
    alike_rows = [tuple(r[k] for k in side) for r in
                  (measure(market, deep_book(pools, placebo=f"matched:{k}", matched=True), span, simulate=stressed)
                   for k in range(placebos))]
    plain, alike = [row[0] for row in plain_rows], [row[0] for row in alike_rows]

    def net_mean(rows: Sequence[tuple[float | None, float | None]]) -> float | None:
        kept = [row[1] for row in rows if row[1] is not None]
        return round(statistics.fmean(kept), 2) if kept else None

    legs = deep_legs(report, bare)
    rungs = {"2a_entry": report["clears_entry"], "2b_later_years": later_years(report["years_total_position_pct"]),
             "2c_beside_placebos": beside_nulls(report["annual_sharpe"], plain), "3_stability": report["stability"]}
    matched = beside_nulls(report["annual_sharpe"], alike)
    ladder = bool(rungs["2a_entry"] and rungs["2b_later_years"]["passes"] and rungs["2c_beside_placebos"]["passes"]
                  and rungs["3_stability"]["passes"])
    earned = _before_costs(report) + report["funding_pct"]
    carried = earned > 0 and legs["alts"]["funding_pct"] >= DEEP_CARRY_SHARE * earned
    reading = ("closed" if not ladder else names[2] if not matched["passes"]
               else "passes_on_carry" if carried else "passes_on_price")
    return {**report, "hypothesis": names[0], "logic": names[1], "counts": counts, "rungs": rungs,
            "beside_matched_placebos": matched, "legs": legs,
            # what the same book earns on coins the signal did not pick: the part of the result that is the book's
            # structure (which side of the alts, against which side of the hedge), not the signal
            "placebo_net_total_position_annual_pct_mean": {"plain": net_mean(plain_rows), "matched": net_mean(alike_rows)},
            "without_the_hedge": {k: bare[k] for k in ("annual_sharpe", "total_position_annual_pct", "years_total_position_pct")},
            "at_engine_cost_annual_sharpe": measure(market, deep_book(pools), span, simulate=simulate_held)["annual_sharpe"],
            "passes": ladder and matched["passes"], "reading": reading}


# --- L17: the deep-positive-funding book (round 14, hypothesis H-F2) ---------------------------------------------------

HOT_NAMES = ("H-F2", "L17", "not_apart_from_run_up_coins")


def hot_counts(market: xsb.Market, span: tuple[date, date] = SPAN) -> dict[str, Any]:
    return deep_counts(market, span, pools=DeepFunding(market, HOT_FUNDING))


def hot_run(market: xsb.Market, span: tuple[date, date] = SPAN, *, placebos: int = DEEP_PLACEBOS) -> dict[str, Any]:
    """L15 mirrored: every pool coin whose funding is deep positive sold in equal weights, the hedge coin bought
    against their summed beta, beside the same book on other coins and on coins that ran up as much."""
    return deep_run(market, span, placebos=placebos, pools=DeepFunding(market, HOT_FUNDING), names=HOT_NAMES)


# --- L18: the spot-gap book (round 14, hypothesis H-G1) ------------------------------------------------------------------

GAP_NAMES = ("H-G1", "L18", "not_apart_from_beaten_down_coins")


class GapPools(DeepFunding):
    """L15's pool and book, picked on how far the perp closed under its own spot instead of on the funding. A coin
    with no spot pair, or with both closes (within the spot-share search's price band of each other) on fewer than
    :data:`GAP_MIN_DAYS` of the window's days, is out of the pool. The market must carry the spot bars
    (:func:`with_spot`)."""

    def __init__(self, market: xsb.Market) -> None:
        super().__init__(market, GAP_DISCOUNT)

    def reading(self, symbol: str, day: date) -> float | None:
        pair = (getattr(self.market, "spot_of", None) or {}).get(symbol)
        if pair is None:
            return None
        spot, perp = self.market.spot.get(pair[0]) or {}, self.market.bars[symbol]
        gaps = [gap for k in range(DEEP_WINDOW)
                if (p := perp.get(day - timedelta(days=k))) is not None
                and (b := spot.get(day - timedelta(days=k))) is not None and b.close > 0
                # a pair whose prices are this far apart is another asset under the same name (the spot-share
                # search's guard): without it one mismatched pair is a pick every week and never one of L15's
                and abs(gap := p.close / (b.close * pair[1]) - 1) <= lab.sp.PRICE_BAND]
        return statistics.fmean(gaps) if len(gaps) >= GAP_MIN_DAYS else None


def gap_overlap(market: xsb.Market, span: tuple[date, date] = SPAN, *, pools: GapPools | None = None) -> dict[str, Any]:
    """How much of the gap book is L15 under another signal: of its coin-decisions, the share that are L15's picks
    the same day (and, beside it, L17's). No return is read. The limit and the decisions it needs are the ones the
    spot-share search judged its own overlap by."""
    gaps, cold, hot = pools or GapPools(market), DeepFunding(market), DeepFunding(market, HOT_FUNDING)
    picked = shared = hot_shared = decisions = chance_n = chance_d = pool_gap = pool_cold = 0
    for day in (d for d in sundays(span) if span[0] - timedelta(days=1) <= d <= span[1]):
        pool, mine, theirs = gaps.at(day)[0], set(gaps.picks(day)), set(cold.picks(day))
        pool_gap, pool_cold = pool_gap + len(pool), pool_cold + len(cold.at(day)[0])
        chance_n, chance_d = chance_n + len(theirs & set(pool)), chance_d + len(pool)
        if mine:
            decisions += 1
            picked, shared, hot_shared = picked + len(mine), shared + len(mine & theirs), hot_shared + len(mine & set(hot.picks(day)))
    share = round(shared / picked, 3) if picked else None
    enough = decisions >= lab.sp.MIN_COMPARED
    return {"reference": "L15", "decisions_compared": decisions, "minimum_compared": lab.sp.MIN_COMPARED,
            "coin_decisions": picked, "limit": lab.sp.OVERLAP_LIMIT,
            "share_that_are_l15_picks": share,
            "share_that_are_l17_picks": round(hot_shared / picked, 3) if picked else None,
            "chance_level": round(chance_n / chance_d, 3) if chance_d else None,
            "pool_beside_l15s_pool": round(pool_gap / pool_cold, 3) if pool_cold else None,
            "independent": bool(share < lab.sp.OVERLAP_LIMIT) if enough and share is not None else None}


def gap_counts(market: xsb.Market, span: tuple[date, date] = SPAN, *, pools: GapPools | None = None) -> dict[str, Any]:
    """L15's counts on the gap pool, with the overlap rule among the stop rules."""
    pools = pools or GapPools(market)
    counts = deep_counts(market, span, pools=pools)
    for row in counts["by_year"].values():
        row["picks_gap_mean"] = row.pop("picks_trailing_funding_mean")      # the signal here is the gap
    overlap = gap_overlap(market, span, pools=pools)
    stops = list(counts["stop"])
    if overlap["independent"] is None:
        stops.append("too few decisions with a gap pick to tell the book from L15")
    elif not overlap["independent"]:
        stops.append("the gap picks are L15's picks: H-F1 under another signal, not a reading of its own")
    return {**counts, "overlap": overlap, "stop": stops}


def gap_run(market: xsb.Market, span: tuple[date, date] = SPAN, *, placebos: int = DEEP_PLACEBOS) -> dict[str, Any]:
    pools = GapPools(market)
    return deep_run(market, span, placebos=placebos, pools=pools, names=GAP_NAMES,
                    counter=functools.partial(gap_counts, pools=pools))


def with_spot(cache: Path, market: xsb.Market, span: tuple[date, date] = SPAN) -> xsb.Market | None:
    """``market`` with the spot bars and each perp's pair hung on it, cut at the span's end; None without a listing."""
    try:
        listing = set(json.loads((cache / lab.sp.SPOT_DIR / lab.ba.SYMBOLS_FILENAME).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        listing = set()
    if not listing:
        return None
    return lab.sp.attach(market, xsb.load_market(cache / lab.sp.SPOT_DIR, funding=False), listing, span[1])


# --- L20: the funding tail held with its own spot (round 16, hypothesis H-B1) --------------------------------------------

BASIS_TAIL = 10                            # the pool's top one in this many by trailing funding is the tail
BASIS_NOT_A_TAIL = 0.25                    # held share of the pool, on average, at or over this: the result is labelled so
BASIS_PLACEBOS = 300
BASIS_MIN_ACTIVE = DEEP_MIN_ACTIVE
BASIS_SPOT_FEE_BPS = lab.sp.fb.fb.SPOT_FEE_BPS       # FUNDING_BASIS_V0.1's spot taker fee
BASIS_LIQUIDATION = lab.sp.fb.fb.LIQUIDATION_RATIO   # FUNDING_BASIS_V0.1: a 1x short is liquidated near twice its entry
BASIS_NAMES = ("H-B1", "L20")


def paid_over_the_middle(pool: Mapping[str, float]) -> set[str]:
    """The pool's coins whose trailing funding is strictly above the pool's median and above zero. Strictly: much of
    a pool can sit at one default rate, and a coin at the median is the typical coin, not one that is paid more."""
    if not pool:
        return set()
    middle = statistics.median(pool.values())
    return {s for s, value in pool.items() if value > middle and value > 0}


def tail_of(pool: Mapping[str, float]) -> tuple[tuple[str, ...], bool]:
    """The tail of a pool's trailing funding, and whether a tie at the tenth's edge was left out. The tail is the
    top one in :data:`BASIS_TAIL` (rounded up), each strictly above the pool's median and above zero. Coins tied
    with the best value outside the tenth are left out whole: name order never decides a pick."""
    if not pool:
        return (), False
    ranked = sorted(pool.items(), key=lambda item: (-item[1], item[0]))
    count = math.ceil(len(ranked) / BASIS_TAIL)
    edge = ranked[count][1] if count < len(ranked) else None
    top = [s for s, value in ranked[:count] if edge is None or value > edge]
    return tuple(sorted(set(top) & paid_over_the_middle(pool))), len(top) < count


class BasisTail:
    """The funding tail, held as pairs, decision by decision over a span. Pool: L15's pool among the coins L18's
    pool admits (a spot pair whose close sat beside the perp's on enough of the window's days), read on L15's
    trailing funding. A coin enters when it is in :func:`tail_of` the pool and stays while its funding is strictly
    above the pool's median and above zero. What is held depends on what was held the week before, so the held sets
    are built once, in order, from the span's first decision."""

    def __init__(self, market: xsb.Market, span: tuple[date, date] = SPAN) -> None:
        self.market = market
        self._funding, self._paired = DeepFunding(market), GapPools(market)
        self.days = [d for d in sundays(span) if span[0] - timedelta(days=1) <= d <= span[1]]
        self._pool: dict[date, dict[str, float]] = {}
        self._held: dict[date, tuple[str, ...]] = {}
        self._tail: dict[date, tuple[str, ...]] = {}
        self._start: dict[tuple[str, date], date] = {}
        self.tied: set[date] = set()                   # decisions at which a tie at the tenth's edge was left out
        before: tuple[str, ...] = ()
        last: date | None = None
        for day in self.days:
            pool = self.pool(day)
            tail, tied = tail_of(pool)
            if tied:
                self.tied.add(day)
            held = tuple(sorted((set(before) & paid_over_the_middle(pool)) | set(tail)))
            for symbol in held:
                self._start[(symbol, day)] = self._start[(symbol, last)] if last is not None and symbol in before else day
            self._tail[day], self._held[day] = tail, held
            before, last = held, day

    def pool(self, day: date) -> dict[str, float]:
        if day not in self._pool:
            paired = self._paired.at(day)[0]
            self._pool[day] = {s: row[0] for s, row in self._funding.at(day)[0].items() if s in paired}
        return self._pool[day]

    def unfunded(self, day: date) -> int:
        return self._funding.at(day)[1]

    def l15_pool(self, day: date) -> int:
        return len(self._funding.at(day)[0])

    def tail(self, day: date) -> tuple[str, ...]:
        return self._tail.get(day, ())

    def held(self, day: date) -> tuple[str, ...]:
        return self._held.get(day, ())

    def spell_start(self, symbol: str, day: date) -> date:
        return self._start[(symbol, day)]

    def partner(self, symbol: str, day: date, seed: str) -> str | None:
        """The coin a placebo holds in place of ``symbol``: drawn once, at the spell's first decision, from the pool
        coins the book did not hold that day, so a placebo pair lasts as long as the real one."""
        start = self.spell_start(symbol, day)
        mine = set(self.held(start))
        others = sorted(s for s in self.pool(start) if s not in mine)
        return random.Random(f"{seed}:{symbol}:{start.isoformat()}").choice(others) if others else None


def pair_basket(names: Iterable[str]) -> dict[str, float]:
    """Equal pairs, none above :data:`DEEP_NAME_CAP` of a side. A weight ``w`` is ``w`` of the coin's spot bought and
    ``w`` of its perp sold; under five names the rest of the book is idle."""
    names = sorted(set(names))
    return dict.fromkeys(names, min(1 / len(names), DEEP_NAME_CAP)) if names else {}


def basis_book(tail: BasisTail, placebo: str | None = None) -> Callable[..., dict[str, float]]:
    """L20: a pair on every coin the tail holds. With ``placebo`` each of those coins is replaced by its partner
    (held only while the partner itself is in the pool that day)."""
    def weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                *, seed: str | None = None) -> dict[str, float]:
        names: Iterable[str] = tail.held(day)
        if placebo is not None:
            pool = tail.pool(day)
            names = [p for s in names if (p := tail.partner(s, day, placebo)) is not None and p in pool]
        return pair_basket(names)
    return weights


def pair_unit(*, spot: bool, stressed: bool = True) -> float:
    """Cost per unit traded on one leg of a pair: the leg's taker fee and the stressed slippage (``stressed`` False:
    the engine's). The stressed value was measured on perps; no spot value is on file."""
    fee = BASIS_SPOT_FEE_BPS if spot else xsb.cost.DEFAULT_TAKER_FEE_BPS
    slippage = LISTING_SLIPPAGE_BPS if stressed else xsb.cost.DEFAULT_SLIPPAGE_BPS
    return (fee + slippage) / 10_000


def simulate_pairs(market: xsb.Market, signal: str, monthly: Mapping[date, Sequence[str]], *, start: date, end: date,
                   weigher: Callable[..., dict[str, float]], stressed: bool = True,
                   log: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Daily net returns of a book of pairs: the held accounting (:func:`simulate_held`) on two legs a coin.

    A weight ``w`` holds ``w`` of the coin's spot and is short ``w`` of its perp, both from the entry day's open. The
    quantities are fixed until the next weekly decision, where both legs are marked again and the trade is the
    difference between the target and what each leg has become (this re-mark stands where FUNDING_BASIS_V0.1 reset a
    pair at 1.5 times its entry). Funding is received on what the short is worth that day. A pair ends, with cost on
    what is sold, in three ways, each written to ``log``:

    - **liquidated**: the perp's high (its last price; no mark series is read) reaches :data:`BASIS_LIQUIDATION`
      times the open it was last marked at. The margin left is lost, the spot is sold at that day's close, and the
      day pays no funding (FUNDING_BASIS_V0.1's rule).
    - **out of band**: the two closes are further apart than the spot-share search's price band, so the pair may be
      two assets under one name. Both legs close at that day's close. A gain printed that day is not counted, a loss
      is, and the day pays no funding: a spliced series cannot carry a result.
    - **a leg ends**: the spot or the perp has no bar the next day. Both legs close at that day's close."""
    units = {"perp": pair_unit(spot=False, stressed=stressed), "spot": pair_unit(spot=True, stressed=stressed)}
    links = getattr(market, "spot_of", None) or {}
    spots = getattr(market, "spot", None) or {}

    def legs(symbol: str, day: date) -> tuple[xsb.Bar, xsb.Bar, float] | None:
        link = links.get(symbol)
        if link is None:
            return None
        perp, spot = (market.bars.get(symbol) or {}).get(day), (spots.get(link[0]) or {}).get(day)
        return None if perp is None or spot is None else (perp, spot, link[1])

    def note(kind: str, **row: Any) -> None:
        if log is not None:
            log.setdefault(kind, []).append(row)

    held: dict[str, tuple[float, float, float]] = {}     # coin -> (weight, perp open at the last mark, spot open at it)
    daily: dict[date, dict[str, float]] = {}
    for day in xsb.held_days(start, end):
        decision = day - timedelta(days=1)
        trade_cost = 0.0
        if is_decision(signal, decision, 1):
            target = weigher(market, monthly.get(date(decision.year, decision.month, 1), ()), decision, signal, seed=None)
            opens = {s: both for s in set(held) | set(target) if (both := legs(s, day)) is not None}
            now = {s: (w * opens[s][0].open / perp_entry, w * opens[s][1].open / spot_entry)
                   for s, (w, perp_entry, spot_entry) in held.items() if s in opens}
            enter = {s: w for s, w in target.items() if s in opens}
            for symbol in set(now) | set(enter):
                short, long = now.get(symbol, (0.0, 0.0))
                want = enter.get(symbol, 0.0)
                trade_cost += abs(want - short) * units["perp"] + abs(want - long) * units["spot"]
            held = {s: (w, opens[s][0].open, opens[s][1].open) for s, w in enter.items()}
        if not held and not trade_cost:
            continue
        gross = funding = exit_cost = 0.0
        gone: list[str] = []
        for symbol, (w, perp_entry, spot_entry) in held.items():
            today, tomorrow = legs(symbol, day), legs(symbol, day + timedelta(days=1))
            if today is None:
                gone.append(symbol)
                continue
            perp, spot, multiple = today
            spot_day = w * (spot.close - spot.open) / spot_entry
            perp_day = -w * (perp.close - perp.open) / perp_entry
            both_sold = w * (perp.close / perp_entry * units["perp"] + spot.close / spot_entry * units["spot"])
            if perp.high is not None and perp.high >= BASIS_LIQUIDATION * perp_entry:
                margin_left = w * max(0.0, 2 - perp.open / perp_entry)
                sold = w * spot.close / spot_entry * units["spot"]
                gross += spot_day - margin_left
                exit_cost += sold
                gone.append(symbol)
                note("liquidations", day=day.isoformat(), symbol=symbol, weight=round(w, 4),
                     net=round(spot_day - margin_left - sold, 6))
            elif spot.close <= 0 or abs(perp.close / (spot.close * multiple) - 1) > lab.sp.PRICE_BAND:
                printed = spot_day + perp_day
                gross += min(0.0, printed)
                exit_cost += both_sold
                gone.append(symbol)
                note("out_of_band", day=day.isoformat(), symbol=symbol, weight=round(w, 4), perp_close=perp.close,
                     spot_close=spot.close * multiple, printed=round(printed, 6), counted=round(min(0.0, printed), 6))
            else:
                if tomorrow is None:                  # a leg stops here: out at today's close
                    gross += spot_day + perp_day
                    exit_cost += both_sold
                    gone.append(symbol)
                    after = day + timedelta(days=1)
                    if day < end:                     # the span's own last day ends every pair: not a leg ending
                        note("leg_ended", day=day.isoformat(), symbol=symbol,
                             leg="perp" if (market.bars.get(symbol) or {}).get(after) is None else "spot")
                else:
                    gross += w * ((tomorrow[1].open - spot.open) / spot_entry - (tomorrow[0].open - perp.open) / perp_entry)
                rate, _known = xsb.funding_on(market, symbol, day)
                funding += w * perp.open / perp_entry * rate
        for symbol in gone:
            held.pop(symbol)
        daily[day] = {"gross": gross, "cost": trade_cost + exit_cost, "funding": funding,
                      "net": gross - trade_cost - exit_cost + funding}
    return {"daily": daily}


def basis_counts(market: xsb.Market, span: tuple[date, date] = SPAN, *, tail: BasisTail | None = None) -> dict[str, Any]:
    """What the pair book would stand on, with nothing earned after a decision in it: decisions, names, spells, how
    much of the pool is held, the trailing funding (the signal) of what is held and of what enters, and how often
    the tenth's edge or the median sits in a tie. No tally against any funding level: that is H-F2's count. The
    stop rules are judged here, and so is the label for a book that holds too much of the pool to be a tail."""
    tail = tail or BasisTail(market, span)
    years: dict[str, dict[str, Any]] = {str(y): {"decisions": 0, "active_decisions": 0, "names": [], "invested": [],
                                                 "pool": [], "held_share": [], "tail_share": [], "spells": 0,
                                                 "held_funding": [], "entered_funding": [], "at_median": [],
                                                 "decisions_with_a_tie_at_the_tenth": 0, "unfunded_coin_decisions": 0}
                                        for y in range(span[0].year, span[1].year + 1)}
    pooled = unfunded_total = l15_pooled = 0
    lengths: list[int] = []
    running: dict[str, int] = {}
    shares: list[float] = []
    for day in tail.days:
        row = years.get(str(day.year))
        if row is None:
            continue
        pool, held = tail.pool(day), tail.held(day)
        row["decisions"] += 1
        row["pool"].append(len(pool))
        row["unfunded_coin_decisions"] += tail.unfunded(day)
        row["invested"].append(min(1.0, len(held) * DEEP_NAME_CAP))
        row["decisions_with_a_tie_at_the_tenth"] += day in tail.tied
        pooled, unfunded_total, l15_pooled = pooled + len(pool), unfunded_total + tail.unfunded(day), l15_pooled + tail.l15_pool(day)
        if pool:
            middle = statistics.median(pool.values())
            row["held_share"].append(len(held) / len(pool))
            row["tail_share"].append(len(tail.tail(day)) / len(pool))
            row["at_median"].append(sum(1 for v in pool.values() if v == middle) / len(pool))
            shares.append(len(held) / len(pool))
        for symbol in list(running):
            if symbol not in held:
                lengths.append(running.pop(symbol))
        for symbol in held:
            if symbol not in running:
                row["spells"] += 1
                row["entered_funding"].append(pool[symbol])
            running[symbol] = running.get(symbol, 0) + 1
            row["held_funding"].append(pool[symbol])
        if held:
            row["active_decisions"] += 1
            row["names"].append(len(held))
    lengths.extend(running.values())

    def mean(values: Sequence[float], digits: int) -> float | None:
        return round(statistics.fmean(values), digits) if values else None

    for row in years.values():
        names, pool_sizes = row.pop("names"), row.pop("pool")
        row["names_held_median"] = statistics.median(names) if names else 0
        row["invested_share_mean"] = mean(row.pop("invested"), 3) or 0.0
        row["pool_median"] = statistics.median(pool_sizes) if pool_sizes else 0
        row["held_share_of_pool_mean"] = mean(row.pop("held_share"), 3)
        row["tail_share_of_pool_mean"] = mean(row.pop("tail_share"), 3)
        row["pool_share_at_the_median_value_mean"] = mean(row.pop("at_median"), 3)
        row["held_trailing_funding_mean"] = mean(row.pop("held_funding"), 5)
        row["entered_trailing_funding_mean"] = mean(row.pop("entered_funding"), 5)
    known = pooled / (pooled + unfunded_total) if pooled + unfunded_total else None
    thin = [str(y) for y in LATER_YEARS if years.get(str(y), {}).get("active_decisions", 0) < BASIS_MIN_ACTIVE]
    stops = [text for wrong, text in (
        (known is None or known < DEEP_MIN_FUNDING_KNOWN, "too many of the pool's coins have no funding on file"),
        (bool(thin), "the book holds a pair at too few decisions in " + ", ".join(thin)),
    ) if wrong]
    held_share = mean(shares, 3)
    return {"decisions": len(tail.days), "active_decisions": sum(r["active_decisions"] for r in years.values()),
            "spells": len(lengths), "spell_weeks_median": statistics.median(lengths) if lengths else 0,
            "spell_weeks_mean": mean(lengths, 2), "funding_known_share": None if known is None else round(known, 3),
            "pool_beside_l15s_pool": round(pooled / l15_pooled, 3) if l15_pooled else None,
            "held_share_of_pool_mean": held_share, "not_a_tail_at": BASIS_NOT_A_TAIL,
            "not_a_tail": held_share is not None and held_share >= BASIS_NOT_A_TAIL, "by_year": years,
            "last_day_in_market": lab.last_day(market).isoformat() if market.bars else None,
            "stop": stops, "returns_computed": False}


def basis_run(market: xsb.Market, span: tuple[date, date] = SPAN, *, placebos: int = BASIS_PLACEBOS) -> dict[str, Any]:
    """The pair book net of stressed costs on both legs, beside the same book on other pool coins, through rung 2's
    three conditions and rung 3. A year the book is flat in counts as zero. Refused when the counts say stop."""
    tail = BasisTail(market, span)
    counts = basis_counts(market, span, tail=tail)
    if counts["stop"]:
        return {"refused": counts["stop"], "counts": counts}
    log: dict[str, list[dict[str, Any]]] = {"liquidations": [], "out_of_band": [], "leg_ended": []}
    report = measure(market, basis_book(tail), span, simulate=functools.partial(simulate_pairs, log=log))
    years = {str(y): report["years_total_position_pct"].get(str(y), 0.0) for y in range(span[0].year, span[1].year + 1)}
    report = {**report, "years_total_position_pct": years, "stability": stability(years)}
    side = ("annual_sharpe", "total_position_annual_pct")
    rows = [tuple(r[k] for k in side) for r in
            (measure(market, basis_book(tail, placebo=f"plain:{k}"), span, simulate=simulate_pairs) for k in range(placebos))]
    nets = [row[1] for row in rows if row[1] is not None]
    rungs = {"2a_entry": report["clears_entry"], "2b_later_years": later_years(years),
             "2c_beside_placebos": beside_nulls(report["annual_sharpe"], [row[0] for row in rows]),
             "3_stability": report["stability"]}
    ladder = bool(rungs["2a_entry"] and rungs["2b_later_years"]["passes"] and rungs["2c_beside_placebos"]["passes"]
                  and rungs["3_stability"]["passes"])
    price = round(_before_costs(report), 2)
    earned = price + report["funding_pct"]
    carried = earned > 0 and report["funding_pct"] >= DEEP_CARRY_SHARE * earned
    engine = measure(market, basis_book(tail), span, simulate=functools.partial(simulate_pairs, stressed=False))
    return {**report, "hypothesis": BASIS_NAMES[0], "logic": BASIS_NAMES[1], "counts": counts, "rungs": rungs,
            "legs": {"price_pct": price, "funding_pct": report["funding_pct"], "cost_pct": report["cost_pct"]},
            "funding_under_cost": report["funding_pct"] < report["cost_pct"],
            "price_loss_over_funding": price < 0 and -price > report["funding_pct"],
            "liquidations": {"count": len(log["liquidations"]),
                             "net_pct": round(sum(r["net"] for r in log["liquidations"]) * 100, 2),
                             "events": log["liquidations"]},
            "out_of_band": {"count": len(log["out_of_band"]),
                            "printed_gain_not_counted_pct": round(sum(r["printed"] - r["counted"] for r in log["out_of_band"]) * 100, 2),
                            "counted_pct": round(sum(r["counted"] for r in log["out_of_band"]) * 100, 2),
                            "days": log["out_of_band"]},
            "legs_ended": {leg: sum(1 for r in log["leg_ended"] if r["leg"] == leg) for leg in ("perp", "spot")},
            "placebo_net_total_position_annual_pct_mean": round(statistics.fmean(nets), 2) if nets else None,
            "at_engine_cost": {k: engine[k] for k in side},
            "not_a_tail": counts["not_a_tail"], "passes": ladder,
            "reading": "closed" if not ladder else "passes_on_carry" if carried else "passes_on_price"}


# --- L21: M04's score on the next tier (round 17, hypothesis H-M1) --------------------------------------------------------

WIDE_SPAN = lab.ms.DEV_LONG                # the span M04 was developed on; positioning barely exists before it
WIDE_TIER = "rank31_100"                   # the month's ranks 31 to 100 by trailing volume: no coin of that month's top 30
WIDE_SCORE = "M04"                         # the registered candidate whose score and basket rule are imported, not copied
WIDE_NULLS = 300
WIDE_MIN_ACTIVE = 26                       # decisions with a book each year of the span needs, or nothing is measured
WIDE_MIN_RANKED = 2 / 3                    # of a year's member coin-decisions must be ranked (the features' own MIN_SHARE)
WIDE_TURNOVER_NULLS = 20                   # null books whose turnover the counts print beside the book's
WIDE_TURNOVER_BAND = (0.8, 1.25)           # book turnover / null turnover inside it: the p is about what is picked
WIDE_NAMES = ("H-M1", "L21")
UNSERVED, VALUELESS, UNEXPLAINED = ("the archive serves no file for a day the score reads",
                                    "a file is on disk without the value", "unexplained")


def with_positioning(cache: Path, market: xsb.Market, span: tuple[date, date] = WIDE_SPAN) -> xsb.Market | None:
    """``market`` with the derived daily positioning values hung on it, and the days the archive answered 404 for,
    both cut at the span's end; None when no derived value is on disk (``metrics_basket_search build`` writes them)."""
    daily = lab.ms.load_daily(cache)
    if not daily:
        return None
    market.metrics = {s: {d: v for d, v in days.items() if d <= span[1]} for s, days in daily.items()}
    try:
        names = json.loads((cache / "archive-metrics" / lab.ba.MISSING_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        names = {}
    unserved: dict[str, set[date]] = {}
    for symbol, files in names.items():
        for name in files:
            try:
                day = date.fromisoformat(str(name)[-14:-4])
            except ValueError:
                continue
            if day <= span[1]:
                unserved.setdefault(symbol, set()).add(day)
    market.metrics_unserved = unserved
    return market


class WideTier:
    """M04's book on the coins of the next tier. The score, its features and the basket rule are the registered
    candidate's own objects (``metrics_basket_search.CANDIDATES[WIDE_SCORE]`` through ``basket_search.weigher``);
    only the members differ: each month's :data:`WIDE_TIER`, none of which is in that month's top 30."""

    def __init__(self, market: xsb.Market, span: tuple[date, date] = WIDE_SPAN) -> None:
        self.market, self.span = market, span
        self.candidate = lab.ms.CANDIDATES[WIDE_SCORE]
        self.monthly = {m: tuple(v) for m, v in lab.tier_members(market, span)[WIDE_TIER].items()}
        self.days = [d for d in sundays(span) if span[0] - timedelta(days=1) <= d <= span[1]]
        self._inner = bs.weigher(self.candidate, lab.ms.FEATURES)
        self._ranked: dict[date, frozenset[str]] = {}
        self._noise: tuple[str, dict[str, list[float]]] = ("", {})

    def members(self, day: date) -> tuple[str, ...]:
        return self.monthly.get(date(day.year, day.month, 1), ())

    def ranked(self, day: date) -> frozenset[str]:
        """The members the score can rank at ``day``'s close: the ones with every feature."""
        if day not in self._ranked:
            self._ranked[day] = frozenset(bs.scores(self.market, self.members(day), day, self.candidate, lab.ms.FEATURES))
        return self._ranked[day]

    def book(self) -> Callable[..., dict[str, float]]:
        def weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                    *, seed: str | None = None) -> dict[str, float]:
            return self._inner(market, self.members(day), day, signal)
        return weights

    def _noise_of(self, seed: str, symbol: str) -> list[float]:
        """Running sums of one coin's daily noise over the span and the lookback before it: a stream per seed and
        coin, so a value does not depend on which days were asked first."""
        if self._noise[0] != seed:
            self._noise = (seed, {})
        sums = self._noise[1].get(symbol)
        if sums is None:
            rng = random.Random(f"{seed}:{symbol}")
            total, sums = 0.0, [0.0]
            for _ in range((self.span[1] - self.span[0]).days + 1 + self.candidate.lookback):
                total += rng.gauss(0.0, 1.0)
                sums.append(total)
            self._noise[1][symbol] = sums
        return sums

    def null(self, seed: str) -> Callable[..., dict[str, float]]:
        """The same basket rule on a score with nothing in it: each coin's noise averaged over the score's own
        lookback, so it changes from week to week about as slowly as a trailing feature does (a score redrawn
        every week would trade several times as much and lose to the book on costs alone). It ranks exactly the
        coins the real score ranks."""
        lookback = self.candidate.lookback

        def feature(market: xsb.Market, symbol: str, day: date, _lookback: int) -> float | None:
            if symbol not in self.ranked(day):
                return None
            sums = self._noise_of(seed, symbol)
            at = (day - self.span[0]).days + 1 + lookback
            return (sums[at] - sums[at - lookback]) / lookback if lookback <= at < len(sums) else None

        inner = bs.weigher(bs.Candidate(why="null", features=(("noise", 1.0),), lookback=lookback, long_low=True),
                           {"noise": feature})

        def weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                    *, seed: str | None = None) -> dict[str, float]:
            return inner(market, self.members(day), day, signal)
        return weights

    def why_unranked(self, symbol: str, day: date) -> str:
        """The named cause a member has no score at ``day``: of the days the score reads, one the archive does not
        serve, or one whose file carries no value. Anything else is unexplained (a file not fetched or not built)."""
        absent, unexplained = self.gaps(symbol, day)
        return UNEXPLAINED if unexplained else UNSERVED if absent else VALUELESS

    def gaps(self, symbol: str, day: date) -> tuple[int, int]:
        """Of the days the score reads at ``day`` (the lookback and the day it grows from): how many have no file's
        value on disk, and how many of those the archive was never recorded as not serving."""
        days = (getattr(self.market, "metrics", None) or {}).get(symbol) or {}
        unserved = (getattr(self.market, "metrics_unserved", None) or {}).get(symbol) or set()
        absent = [d for d in (day - timedelta(days=k) for k in range(self.candidate.lookback + 1)) if d not in days]
        return len(absent), sum(1 for d in absent if d not in unserved)

    def turnover(self, weigher: Callable[..., dict[str, float]]) -> float | None:
        """Mean over consecutive decisions of half the summed change in weights: 0 holds, 2 replaces both sides."""
        moves, before = [], None
        for day in self.days:
            now = weigher(self.market, (), day, bs.CADENCE)
            if before and now:
                moves.append(sum(abs(now.get(s, 0.0) - before.get(s, 0.0)) for s in set(now) | set(before)) / 2)
            before = now
        return round(statistics.fmean(moves), 4) if moves else None


def wide_counts(market: xsb.Market, span: tuple[date, date] = WIDE_SPAN, *, tier: WideTier | None = None) -> dict[str, Any]:
    """What the book would stand on, with nothing earned after a decision in it: members, how many the score ranks
    and why the others are not ranked, the side size, and how much the book trades beside its null books. Every
    member's window is checked, ranked or not: a score tolerates missing days, so a hole left by an incomplete
    fetch would otherwise thin the windows unseen. The stop rules are judged here."""
    tier = tier or WideTier(market, span)
    years: dict[str, dict[str, Any]] = {str(y): {"decisions": 0, "active_decisions": 0, "members": [], "ranked": [],
                                                 "side": [], "member_coin_decisions": 0, "ranked_coin_decisions": 0,
                                                 "unranked": {}, "days_read_without_a_file_or_a_cause": 0}
                                        for y in range(span[0].year, span[1].year + 1)}
    book = tier.book()
    for day in tier.days:
        row = years.get(str(day.year))
        if row is None:
            continue
        members, ranked = tier.members(day), tier.ranked(day)
        weights = book(market, (), day, bs.CADENCE)
        row["decisions"] += 1
        row["members"].append(len(members))
        row["ranked"].append(len(ranked))
        row["member_coin_decisions"] += len(members)
        row["ranked_coin_decisions"] += len(ranked)
        if weights:
            row["active_decisions"] += 1
            row["side"].append(sum(1 for w in weights.values() if w > 0))
        for symbol in members:
            holes = tier.gaps(symbol, day)[1]
            row["days_read_without_a_file_or_a_cause"] += holes
            if symbol not in ranked:
                cause = UNEXPLAINED if holes else tier.why_unranked(symbol, day)
                row["unranked"][cause] = row["unranked"].get(cause, 0) + 1
    thin, sparse, unexplained = [], [], 0
    for year, row in years.items():
        members, ranked, side = row.pop("members"), row.pop("ranked"), row.pop("side")
        row["members_median"] = statistics.median(members) if members else 0
        row["ranked_median"] = statistics.median(ranked) if ranked else 0
        row["side_median"] = statistics.median(side) if side else 0
        share = row["ranked_coin_decisions"] / row["member_coin_decisions"] if row["member_coin_decisions"] else None
        row["ranked_share"] = None if share is None else round(share, 3)
        unexplained += row["days_read_without_a_file_or_a_cause"]
        if row["active_decisions"] < WIDE_MIN_ACTIVE:
            thin.append(year)
        if share is None or share < WIDE_MIN_RANKED:
            sparse.append(year)
    own = tier.turnover(book)
    others = [t for t in (tier.turnover(tier.null(f"turnover:{k}")) for k in range(WIDE_TURNOVER_NULLS)) if t is not None]
    beside = round(statistics.fmean(others), 4) if others else None
    ratio = round(own / beside, 3) if own is not None and beside else None
    stops = [text for wrong, text in (
        (unexplained > 0, f"{unexplained} day(s) a member's score reads have no file and no named cause: fetch or build is incomplete"),
        (bool(sparse), "the score ranks too few of the tier's coins in " + ", ".join(sparse)),
        (bool(thin), "the book stands at too few decisions in " + ", ".join(thin)),
    ) if wrong]
    return {"decisions": len(tier.days), "tier": WIDE_TIER, "score": WIDE_SCORE, "by_year": years,
            "turnover": {"book": own, "null_books_mean": beside, "null_books": len(others), "ratio": ratio,
                         "band": list(WIDE_TURNOVER_BAND),
                         "alike": ratio is not None and WIDE_TURNOVER_BAND[0] <= ratio <= WIDE_TURNOVER_BAND[1]},
            "last_day_in_market": lab.last_day(market).isoformat() if market.bars else None,
            "stop": stops, "returns_computed": False}


def wide_run(market: xsb.Market, span: tuple[date, date] = WIDE_SPAN, *, nulls: int = WIDE_NULLS) -> dict[str, Any]:
    """M04's book on the next tier, held and net of stressed costs, beside the same basket rule on scores with
    nothing in them, through rung 2's three conditions and rung 3. Refused when the counts say stop. The value under
    the accounting and cost M04 itself was measured with is printed beside it and judged by nothing."""
    tier = WideTier(market, span)
    counts = wide_counts(market, span, tier=tier)
    if counts["stop"]:
        return {"refused": counts["stop"], "counts": counts}
    stressed = functools.partial(simulate_held, unit_of=listing_unit)

    def whole(report: dict[str, Any]) -> dict[str, Any]:
        years = {str(y): report["years_total_position_pct"].get(str(y), 0.0) for y in range(span[0].year, span[1].year + 1)}
        return {**report, "years_total_position_pct": years, "stability": stability(years)}

    report = whole(measure(market, tier.book(), span, simulate=stressed))
    side = ("annual_sharpe", "total_position_annual_pct")
    rows = [tuple(r[k] for k in side) for r in
            (measure(market, tier.null(f"wide:{k}"), span, simulate=stressed) for k in range(nulls))]
    nets = [row[1] for row in rows if row[1] is not None]
    rungs = {"2a_entry": report["clears_entry"], "2b_later_years": later_years(report["years_total_position_pct"]),
             "2c_beside_null_books": beside_nulls(report["annual_sharpe"], [row[0] for row in rows]),
             "3_stability": report["stability"]}
    ladder = bool(rungs["2a_entry"] and rungs["2b_later_years"]["passes"] and rungs["2c_beside_null_books"]["passes"]
                  and rungs["3_stability"]["passes"])
    price = round(_before_costs(report), 2)
    engine = whole(measure(market, tier.book(), span))
    alike = counts["turnover"]["alike"]
    return {**report, "hypothesis": WIDE_NAMES[0], "logic": WIDE_NAMES[1], "counts": counts, "rungs": rungs,
            "legs": {"price_pct": price, "funding_pct": report["funding_pct"], "cost_pct": report["cost_pct"]},
            "funding_carried_it": price <= 0 < report["funding_pct"],
            "null_net_total_position_annual_pct_mean": round(statistics.fmean(nets), 2) if nets else None,
            "as_m04_was_measured": {k: engine[k] for k in (*side, "years_total_position_pct", "cost_pct", "funding_pct")},
            "held_at_engine_cost": {k: v for k, v in measure(market, tier.book(), span, simulate=simulate_held).items() if k in side},
            "passes": ladder,
            "reading": "closed" if not ladder else "passes" if alike else "passes_beside_null_books_that_trade_differently"}


# --- L22: the month's reversal on the next tier (round 34, hypothesis H-R2) ----------------------------------------------

MID_TIER = "rank31_100"                    # the map's own tier: the cell that met the lead rule is read on these coins
MID_VARIABLE = "momentum_30"               # the mapped variable itself: the 30-day return to the decision day's close
MID_NULLS = 300
MID_MIN_ACTIVE = 26                        # decisions with a book each of LATER_YEARS needs, or nothing is measured
MID_MIN_FUNDING_KNOWN = 0.9                # of the ranked coin-decisions must have funding on file
MID_TURNOVER_NULLS = 20
MID_TURNOVER_BAND = (0.8, 1.25)            # book turnover / null turnover inside it: the p is about what is picked
MID_NAMES = ("H-R2", "L22")


class MidTier:
    """The map's rank 31-100 members and its 30-day momentum, as mapped: no filter is added, so the book stands on
    the coins the cell was read on. Long what fell, short what rose, by L1's rank weights."""

    def __init__(self, market: xsb.Market, span: tuple[date, date] = SPAN) -> None:
        self.market, self.span = market, span
        self.monthly = {m: tuple(v) for m, v in lab.tier_members(market, span)[MID_TIER].items()}
        self.days = [d for d in sundays(span) if span[0] - timedelta(days=1) <= d <= span[1]]
        self.read = lab.variables()[MID_VARIABLE][1]
        self._ranked: dict[date, frozenset[str]] = {}
        self._noise: tuple[str, dict[str, list[float]]] = ("", {})

    def members(self, day: date) -> tuple[str, ...]:
        return self.monthly.get(date(day.year, day.month, 1), ())

    def ranked(self, day: date) -> frozenset[str]:
        if day not in self._ranked:
            self._ranked[day] = frozenset(s for s in self.members(day) if self.read(self.market, s, day) is not None)
        return self._ranked[day]

    def _weigher(self, read: lab.Variable) -> Callable[..., dict[str, float]]:
        inner = book(read, lambda _day: -1)

        def weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                    *, seed: str | None = None) -> dict[str, float]:
            return inner(market, self.members(day), day, signal, seed=seed)
        return weights

    def book(self) -> Callable[..., dict[str, float]]:
        return self._weigher(self.read)

    def null(self, seed: str) -> Callable[..., dict[str, float]]:
        """The same book on a variable with nothing in it: each coin's daily noise summed over the variable's own
        30 days, so its ranks last from week to week as a trailing return's do (round 17's construction; a variable
        redrawn every week would trade several times as much and lose to the book on costs alone). It has a value
        exactly where the real variable has one."""
        def noise(market: xsb.Market, symbol: str, day: date) -> float | None:
            if symbol not in self.ranked(day):
                return None
            if self._noise[0] != seed:
                self._noise = (seed, {})
            sums = self._noise[1].get(symbol)
            if sums is None:
                rng = random.Random(f"{seed}:{symbol}")
                total, sums = 0.0, [0.0]
                for _ in range((self.span[1] - self.span[0]).days + 2 + lab.LOOKBACK):
                    total += rng.gauss(0.0, 1.0)
                    sums.append(total)
                self._noise[1][symbol] = sums
            at = (day - self.span[0]).days + 1 + lab.LOOKBACK
            return sums[at] - sums[at - lab.LOOKBACK] if lab.LOOKBACK <= at < len(sums) else None
        return self._weigher(noise)

    def turnover(self, weigher: Callable[..., dict[str, float]]) -> float | None:
        """Mean over consecutive decisions of half the summed change in weights: 0 holds, 2 replaces both sides."""
        moves, before = [], None
        for day in self.days:
            now = weigher(self.market, (), day, bs.CADENCE)
            if before and now:
                moves.append(sum(abs(now.get(s, 0.0) - before.get(s, 0.0)) for s in set(now) | set(before)) / 2)
            before = now
        return round(statistics.fmean(moves), 4) if moves else None


def mid_counts(market: xsb.Market, span: tuple[date, date] = SPAN, *, tier: MidTier | None = None) -> dict[str, Any]:
    """What the book would stand on, with nothing earned after a decision in it: members, how many the variable
    ranks, how many of those have funding on file, the side size, and how much the book trades beside null books.
    The stop rules are judged here."""
    tier = tier or MidTier(market, span)
    years: dict[str, dict[str, Any]] = {str(y): {"decisions": 0, "active_decisions": 0, "members": [], "ranked": [],
                                                 "side": []} for y in range(span[0].year, span[1].year + 1)}
    weigher = tier.book()
    held = unfunded = 0
    for day in tier.days:
        row = years.get(str(day.year))
        if row is None:
            continue
        ranked = tier.ranked(day)
        weights = weigher(market, (), day, bs.CADENCE)
        row["decisions"] += 1
        row["members"].append(len(tier.members(day)))
        row["ranked"].append(len(ranked))
        held += len(ranked)
        unfunded += sum(1 for s in ranked if market.funding_day(s) is None)
        if weights:
            row["active_decisions"] += 1
            row["side"].append(sum(1 for w in weights.values() if w > 0))
    for row in years.values():
        for key in ("members", "ranked", "side"):
            values = row.pop(key)
            row[f"{key}_median"] = statistics.median(values) if values else 0
    known = 1 - unfunded / held if held else None
    thin = [str(y) for y in LATER_YEARS if years.get(str(y), {}).get("active_decisions", 0) < MID_MIN_ACTIVE]
    own = tier.turnover(weigher)
    others = [t for t in (tier.turnover(tier.null(f"turnover:{k}")) for k in range(MID_TURNOVER_NULLS)) if t is not None]
    beside = round(statistics.fmean(others), 4) if others else None
    ratio = round(own / beside, 3) if own is not None and beside else None
    unit = (xsb.cost.DEFAULT_TAKER_FEE_BPS + LISTING_SLIPPAGE_BPS) / 10_000
    stops = [text for wrong, text in (
        (known is None or known < MID_MIN_FUNDING_KNOWN, "too many of the ranked coins have no funding on file"),
        (bool(thin), "the book stands at too few decisions in " + ", ".join(thin)),
    ) if wrong]
    return {"decisions": len(tier.days), "tier": MID_TIER, "variable": MID_VARIABLE, "by_year": years,
            "funding_known_share": None if known is None else round(known, 4),
            "turnover": {"book": own, "null_books_mean": beside, "null_books": len(others), "ratio": ratio,
                         "band": list(MID_TURNOVER_BAND),
                         "alike": ratio is not None and MID_TURNOVER_BAND[0] <= ratio <= MID_TURNOVER_BAND[1]},
            # two sides traded, 52 decisions a year, on a position of POSITION_TOTAL: weights only, no price in it
            "cost_from_turnover_total_position_annual_pct":
                None if own is None else round(own * 2 * unit * 52 / bs.POSITION_TOTAL * 100, 2),
            "last_day_in_market": lab.last_day(market).isoformat() if market.bars else None,
            "stop": stops, "returns_computed": False}


def mid_run(market: xsb.Market, span: tuple[date, date] = SPAN, *, nulls: int = MID_NULLS) -> dict[str, Any]:
    """The month's reversal on the next tier, held and net of stressed costs, beside the same book on variables with
    nothing in them, through rung 2's three conditions and rung 3. Refused when the counts say stop. The value at
    the engine's cost is printed beside it and is read only by the reading table."""
    tier = MidTier(market, span)
    counts = mid_counts(market, span, tier=tier)
    if counts["stop"]:
        return {"refused": counts["stop"], "counts": counts}
    stressed = functools.partial(simulate_held, unit_of=listing_unit)

    def whole(report: dict[str, Any]) -> dict[str, Any]:
        years = {str(y): report["years_total_position_pct"].get(str(y), 0.0) for y in range(span[0].year, span[1].year + 1)}
        return {**report, "years_total_position_pct": years, "stability": stability(years)}

    report = whole(measure(market, tier.book(), span, simulate=stressed))
    side = ("annual_sharpe", "total_position_annual_pct")
    rows = [tuple(r[k] for k in side) for r in
            (measure(market, tier.null(f"mid:{k}"), span, simulate=stressed) for k in range(nulls))]
    nets = [row[1] for row in rows if row[1] is not None]
    rungs = {"2a_entry": report["clears_entry"], "2b_later_years": later_years(report["years_total_position_pct"]),
             "2c_beside_null_books": beside_nulls(report["annual_sharpe"], [row[0] for row in rows]),
             "3_stability": report["stability"]}
    ladder = bool(rungs["2a_entry"] and rungs["2b_later_years"]["passes"] and rungs["2c_beside_null_books"]["passes"]
                  and rungs["3_stability"]["passes"])
    engine = whole(measure(market, tier.book(), span, simulate=simulate_held))
    alike = counts["turnover"]["alike"]
    lead = not ladder and bool(engine["clears_entry"]) and rungs["2c_beside_null_books"]["passes"]
    return {**report, "hypothesis": MID_NAMES[0], "logic": MID_NAMES[1], "counts": counts, "rungs": rungs,
            "legs": {"price_pct": round(_before_costs(report), 2), "funding_pct": report["funding_pct"], "cost_pct": report["cost_pct"]},
            "null_net_total_position_annual_pct_mean": round(statistics.fmean(nets), 2) if nets else None,
            "held_at_engine_cost": {k: engine[k] for k in (*side, "clears_entry", "cost_pct", "years_total_position_pct")},
            "passes": ladder,
            "reading": ("passes" if alike else "passes_beside_null_books_that_trade_differently") if ladder
                       else "cost_dependent_lead" if lead else "closed"}


# --- the sweep on the map's second tier (round 36, section 52) ---------------------------------------------------------

TIER_SWEEP_TIER = MID_TIER                 # the map's ranks 31 to 100, as the map reads them: nothing filtered
TIER_SWEEP_NULLS = 600                     # one class: a value fixed per coin, the slowest a rank book can trade
TIER_SWEEP_MIN_MONTHS = 24                 # complete months a book needs: a yearly rate under two years is one year's
TIER_SWEEP_TURNOVER_NULLS = 20
# Registered variables no book is built from on this tier, each with the record that says so.
TIER_SWEEP_OUT: dict[str, str] = {
    **dict.fromkeys(("vol_30", "idio_vol_30", "max_30", "skew_30"),
                    "the closed volatility cluster: no more books from it (section 20)"),
    **dict.fromkeys(("range_30", "beta_30", "extreme_days_30"),
                    "read as kin of the closed volatility cluster (section 33.1)"),
    **dict.fromkeys(("oi_growth_30", "ls_level_30"),
                    "H-M1's variables: on this tier they are not built into a book (section 32.2)"),
    "momentum_30": "H-R2's own book: the gate, not a new reading (section 49)",
    **dict.fromkeys(("momentum_7", "momentum_90"),
                    "H-R2 at another window: its registration says the window is never changed (section 49)"),
    "spread_change_30": "the top tier's lead, not put on the ladder (D10): never read again on another tier (section 46)",
    "trade_size_position_30": "the top tier's runner-up: left alone (section 45)",
    **dict.fromkeys(("imbalance_1_30", "imbalance_5_30", "depth_growth_30"), "no depth files on this tier (D11)"),
}


def tier_sweep_names() -> dict[str, tuple[str, lab.Variable]]:
    """The registered variables a book is built from on the second tier: all but :data:`TIER_SWEEP_OUT`."""
    registered = lab.variables()
    unknown = sorted(set(TIER_SWEEP_OUT) - set(registered))
    if unknown:
        raise ValueError(f"left-out names that are not registered: {unknown}")
    return {name: body for name, body in registered.items() if name not in TIER_SWEEP_OUT}


def _month(day: date) -> str:
    return day.strftime("%Y-%m")


class TierSweep:
    """H-R2's book (L22) for any variable: the map's second tier as it is, L1's rank weights over every ranked member,
    weekly, held, every coin at the stressed cost. A book is read on its own span: from the first day of the month
    after its first decision with weights to the span's end, so a variable whose files begin late is not averaged
    with empty months, and the null books are cut to the same months.

    ``members`` (round 40) gives another tier's members per decision day, and ``name`` names it; without them the
    tier is the map's second, as round 36 read it."""

    def __init__(self, market: xsb.Market, span: tuple[date, date] = SPAN, *,
                 members: Callable[[date], Sequence[str]] | None = None, name: str = TIER_SWEEP_TIER) -> None:
        self.market, self.span, self.name = market, span, name
        self._members = members
        self.monthly = ({} if members is not None else
                        {m: tuple(v) for m, v in lab.tier_members(market, span)[TIER_SWEEP_TIER].items()})
        self.days = [d for d in sundays(span) if span[0] - timedelta(days=1) <= d <= span[1]]
        self.ic_days = lab.decisions(span, lab.HORIZONS[0])
        self._forward: dict[date, dict[str, float]] = {}
        self._fixed: tuple[str, dict[str, float]] = ("", {})

    def members(self, day: date) -> tuple[str, ...]:
        if self._members is not None:
            return tuple(self._members(day))
        return self.monthly.get(date(day.year, day.month, 1), ())

    def weigher(self, read: lab.Variable, way: int) -> Callable[..., dict[str, float]]:
        inner = book(read, lambda _day: way)

        def weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                    *, seed: str | None = None) -> dict[str, float]:
            return inner(market, self.members(day), day, signal, seed=seed)
        return weights

    def fixed(self, seed: str) -> lab.Variable:
        """A variable with nothing in it that never changes: one draw per coin, wherever the coin has a bar that
        day. Its ranks move only when the tier's members do, so no real book trades less for the same reason."""
        def read(market: xsb.Market, symbol: str, day: date) -> float | None:
            if day not in (market.bars.get(symbol) or {}):
                return None
            if self._fixed[0] != seed:
                self._fixed = (seed, {})
            values = self._fixed[1]
            if symbol not in values:
                values[symbol] = random.Random(f"{seed}:{symbol}").gauss(0.0, 1.0)
            return values[symbol]
        return read

    def first_decision(self, read: lab.Variable) -> date | None:
        """The first decision at which the book has weights."""
        weigher = self.weigher(read, 1)
        return next((d for d in self.days if weigher(self.market, (), d, bs.CADENCE)), None)

    def start(self, read: lab.Variable) -> date | None:
        """The first day of the month after the first decision with weights: the book's own span begins there."""
        first = self.first_decision(read)
        if first is None:
            return None
        return date(first.year + (first.month == 12), first.month % 12 + 1, 1)

    def months(self, start: date | None) -> list[str]:
        return [] if start is None else bs._complete_months(max(start, self.span[0]), self.span[1])

    def forward(self, day: date) -> dict[str, float]:
        if day not in self._forward:
            self._forward[day] = {s: v for s in self.members(day)
                                  if (v := lab.forward_return(self.market, s, day, lab.HORIZONS[0])) is not None}
        return self._forward[day]

    def ics(self, read: lab.Variable) -> dict[date, float]:
        """The variable's rank correlation with the week that follows, per decision that has one (the sweep's rule)."""
        out = {}
        for day in self.ic_days:
            pairs = [(value, forward) for s, forward in self.forward(day).items()
                     if (value := read(self.market, s, day)) is not None]
            if len(pairs) >= lab.MIN_NAMES and (ic := lab.rank_correlation(pairs)) is not None:
                out[day] = ic
        return out

    def monthly_rows(self, read: lab.Variable, way: int, *, every: int = 1,
                     weigher: Callable[..., dict[str, float]] | None = None) -> dict[str, dict[str, float]]:
        """The book over the whole span, summed by calendar month: net, cost and funding. Held, stressed cost.
        ``every`` holds each decision that many weeks (round 41's H-A1: four); ``weigher`` replaces L1's weights."""
        sim = simulate_held(self.market, bs.CADENCE, {}, start=self.span[0], end=self.span[1],
                            weigher=weigher or self.weigher(read, way), unit_of=listing_unit, every=every)
        out: dict[str, dict[str, float]] = {}
        for day, row in sim["daily"].items():
            month = out.setdefault(_month(day), {"net": 0.0, "cost": 0.0, "funding": 0.0})
            for key in month:
                month[key] += row.get(key, 0.0)
        return out

    def turnover(self, read: lab.Variable) -> float | None:
        """Mean over consecutive decisions with weights of half the summed change in weights."""
        weigher, moves, before = self.weigher(read, 1), [], None
        for day in self.days:
            now = weigher(self.market, (), day, bs.CADENCE)
            if before and now:
                moves.append(sum(abs(now.get(s, 0.0) - before.get(s, 0.0)) for s in set(now) | set(before)) / 2)
            before = now
        return round(statistics.fmean(moves), 4) if moves else None


def tier_side(ics: Mapping[date, float], start: date) -> tuple[int, float | None]:
    """The side a book is long on its own span: the sign of its mean rank correlation over the decisions from
    ``start`` on. The same rule for a real variable and a null one."""
    values = [ic for day, ic in ics.items() if day >= start]
    if not values:
        return -1, None
    mean = statistics.fmean(values)
    return (1 if mean > 0 else -1), mean


def tier_reading(rows: Mapping[str, Mapping[str, float]], months: Sequence[str]) -> dict[str, Any]:
    """A book's months as the entry rule reads them: the annual Sharpe of the monthly nets and the yearly rate on
    total position, by :mod:`basket_search`'s own arithmetic."""
    values = [rows.get(m, {}).get("net", 0.0) for m in months]
    sharpe = (round(statistics.fmean(values) / statistics.stdev(values) * math.sqrt(12), 3)
              if len(values) >= 2 and statistics.stdev(values) != 0 else None)
    report: dict[str, Any] = {"annual_sharpe": sharpe, "sum_pct": round(sum(values) * 100, 2), "months": len(values)}
    report["total_position_annual_pct"] = bs.total_position_annual_pct(report["sum_pct"], report["months"])
    report["clears_entry"] = bs.clears_entry(report)
    return report


def _tier_skip(tier: TierSweep, read: lab.Variable) -> tuple[date | None, list[str], str | None]:
    start = tier.start(read)
    months = tier.months(start)
    if len(months) < TIER_SWEEP_MIN_MONTHS:
        return start, months, f"its own span is {len(months)} complete months, under {TIER_SWEEP_MIN_MONTHS}"
    return start, months, None


def tier_sweep_counts(market: xsb.Market, names: Mapping[str, tuple[str, lab.Variable]],
                      span: tuple[date, date] = SPAN, *, tier: TierSweep | None = None) -> dict[str, Any]:
    """What each book would stand on, with nothing earned after a decision in it: when it begins, how many coins it
    ranks, how many of those are tied at one value, are dated contracts, stand on a bar with no volume or have no
    funding on file, how many coins it ranks beside what a null book ranks on the same days, how many months inside
    its own span have no decision with a book, and how much it trades beside the null books."""
    tier = tier or TierSweep(market, span)
    others = [t for t in (tier.turnover(tier.fixed(f"turnover:{k}")) for k in range(TIER_SWEEP_TURNOVER_NULLS)) if t is not None]
    beside = round(statistics.fmean(others), 4) if others else None
    books: dict[str, Any] = {}
    for name, (group, read) in names.items():
        start, months, skip = _tier_skip(tier, read)
        first = tier.first_decision(read)
        cells = tied = dated = still = unfunded = with_bar = 0
        ranked, active = [], set()
        for day in tier.days:
            if start is None or day < start - timedelta(days=7):
                continue
            values = {s: v for s in tier.members(day) if (v := read(market, s, day)) is not None}
            if len(values) < lab.MIN_NAMES:
                continue
            active.add(_month(day))
            with_bar += sum(1 for s in tier.members(day) if day in market.bars[s])      # what a null book ranks that day
            seen: dict[float, int] = {}
            for v in values.values():
                seen[v] = seen.get(v, 0) + 1
            ranked.append(len(values))
            cells += len(values)
            tied += sum(n for n in seen.values() if n > 1)
            dated += sum(1 for s in values if not s.endswith("USDT"))
            still += sum(1 for s in values if (bar := market.bars[s].get(day)) is not None and bar.quote_volume == 0)
            unfunded += sum(1 for s in values if market.funding_day(s) is None)
        own = tier.turnover(read)
        books[name] = {"group": group, "first_decision": None if first is None else first.isoformat(),
                       "start": None if start is None else start.isoformat(), "months": len(months),
                       "decisions_with_a_book": len(ranked), "ranked_median": statistics.median(ranked) if ranked else 0,
                       "ranked_cells": cells, "ranked_beside_null": round(cells / with_bar, 3) if with_bar else None,
                       "months_without_a_book": sum(1 for m in months if m not in active),
                       "tied_share": round(tied / cells, 4) if cells else None,
                       "dated_cells": dated, "zero_volume_cells": still,
                       "unfunded_share": round(unfunded / cells, 4) if cells else None,
                       "turnover": own, "turnover_beside_null": round(own / beside, 3) if own is not None and beside else None,
                       **({"skipped": skip} if skip else {})}
    return {"tier": tier.name, "decisions": len(tier.days), "null_turnover_mean": beside, "null_turnover_books": len(others),
            "min_months": TIER_SWEEP_MIN_MONTHS, "books": books, "returns_computed": False}


def tier_sweep(market: xsb.Market, names: Mapping[str, tuple[str, lab.Variable]],
               span: tuple[date, date] = SPAN, *, tier: TierSweep | None = None) -> dict[str, Any]:
    """Every variable in ``names`` as the tier's book, on its own span, long the side its own rank correlation
    names. A book whose span is too short is named and not built."""
    tier = tier or TierSweep(market, span)
    out: dict[str, Any] = {}
    for name, (group, read) in names.items():
        start, months, skip = _tier_skip(tier, read)
        if skip:
            out[name] = {"group": group, "skipped": skip}
            continue
        ics = tier.ics(read)
        way, mean = tier_side(ics, start)
        if mean is None or sum(1 for d in ics if d >= start) < lab.MIN_DECISIONS:
            out[name] = {"group": group, "skipped": "ranked at too few decisions"}
            continue
        rows = tier.monthly_rows(read, way)
        report = tier_reading(rows, months)
        years = sorted({m[:4] for m in months})
        cost, funding = (sum(rows.get(m, {}).get(k, 0.0) for m in months) * 100 for k in ("cost", "funding"))
        out[name] = {"group": group, "start": start.isoformat(), "long": "high" if way > 0 else "low",
                     "mean_rank_correlation": round(mean, 4), "decisions_ranked": sum(1 for d in ics if d >= start),
                     **report,
                     "years_total_position_pct": {y: round(sum(rows.get(m, {}).get("net", 0.0) for m in months if m[:4] == y)
                                                           * 100 / bs.POSITION_TOTAL, 2) for y in years},
                     "price_pct": round(report["sum_pct"] + cost - funding, 2),
                     "funding_pct": round(funding, 2), "cost_pct": round(cost, 2)}
    return out


def tier_sweep_null(market: xsb.Market, span: tuple[date, date] = SPAN, *, nulls: int = TIER_SWEEP_NULLS,
                    tier: TierSweep | None = None) -> dict[str, Any]:
    """The null books, each kept whole: its rank correlation at every decision and both sides' monthly nets, so it
    can be cut to any book's own span with its side picked on that span."""
    tier = tier or TierSweep(market, span)
    out: dict[str, Any] = {}
    for k in range(nulls):
        read = tier.fixed(f"tier:{k}")
        out[f"tier:{k}"] = {"ics": {d.isoformat(): round(ic, 6) for d, ic in tier.ics(read).items()},
                            **{side: {m: round(row["net"], 8) for m, row in tier.monthly_rows(read, way).items()}
                               for side, way in (("high", 1), ("low", -1))}}
    return out


def tier_sweep_reading(books: Mapping[str, Mapping[str, Any]], null: Mapping[str, Mapping[str, Any]],
                       *, q: float = 0.10) -> dict[str, Any]:
    """Each book beside the same null books cut to its own months, their side picked on those months. A lead is a
    book that survives Benjamini-Hochberg at ``q`` AND clears the entry rule; a survivor under the entry rule is
    named and nothing is built from it."""
    by_start: dict[str, list[float]] = {}

    def null_sharpes(start: str, months: int) -> list[float]:
        if start not in by_start:
            first = date.fromisoformat(start)
            values = []
            for body in null.values():
                way, mean = tier_side({date.fromisoformat(d): ic for d, ic in body["ics"].items()}, first)
                if mean is None:
                    continue
                side = body["high" if way > 0 else "low"]
                kept = sorted(m for m in side if m >= start[:7])[:months]
                sharpe = tier_reading({m: {"net": side[m]} for m in kept}, kept)["annual_sharpe"]
                if sharpe is not None and len(kept) == months:
                    values.append(sharpe)
            by_start[start] = sorted(values)
        return by_start[start]

    rows, skipped = [], {}
    for name, body in books.items():
        if body.get("skipped") or body.get("annual_sharpe") is None:
            skipped[name] = body.get("skipped", "no Sharpe")
            continue
        nulls = null_sharpes(body["start"], body["months"])
        if not nulls:
            skipped[name] = "no null book covers its months"
            continue
        above = sum(1 for v in nulls if v >= body["annual_sharpe"])
        rows.append({"variable": name, **{k: body[k] for k in ("group", "start", "months", "long", "annual_sharpe",
                                                               "total_position_annual_pct", "clears_entry")},
                     "null_books": len(nulls), "null_at_or_above": above, "p": (1 + above) / (1 + len(nulls)),
                     "null_median": nulls[len(nulls) // 2], "null_max": nulls[-1]})
    rows.sort(key=lambda r: (r["p"], -r["annual_sharpe"], r["variable"]))
    cutoff = max((i for i, r in enumerate(rows, start=1) if r["p"] <= q * i / len(rows)), default=0)
    survivors = rows[:cutoff]
    return {"books": len(rows), "skipped": skipped, "q": q, "first_threshold": q / len(rows) if rows else None,
            "p_under_0.05": sum(1 for r in rows if r["p"] < 0.05), "expected_under_0.05_by_chance": round(0.05 * len(rows), 2),
            "clear_entry": [r["variable"] for r in rows if r["clears_entry"]],
            "survive_benjamini_hochberg": [r["variable"] for r in survivors],
            "leads": [r["variable"] for r in survivors if r["clears_entry"]],
            "survivors_under_the_entry_rule": [r["variable"] for r in survivors if not r["clears_entry"]],
            "all": rows}


def tier_sweep_gate(market: xsb.Market, span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """H-R2's book through this section's code, over the whole span as H-R2 was read: it must give H-R2's record."""
    return _whole_span(TierSweep(market, span), lab.variables()[MID_VARIABLE][1], -1, span)


def _whole_span(tier: TierSweep, read: lab.Variable, way: int, span: tuple[date, date]) -> dict[str, Any]:
    """A book through the tier sweep's code over every month of the span, a month without a book counting as zero:
    how the closed books (H-R2, H-R1) were read, so a gate can hold the code to their records."""
    rows = tier.monthly_rows(read, way)
    months = bs._complete_months(span[0], span[1])
    report = tier_reading(rows, months)
    years = {str(y): round(sum(row["net"] for m, row in rows.items() if m[:4] == str(y)) * 100 / bs.POSITION_TOTAL, 2)
             for y in range(span[0].year, span[1].year + 1)}
    return {**report, "years_total_position_pct": years,
            "cost_pct": round(sum(row["cost"] for row in rows.values()) * 100, 2),
            "funding_pct": round(sum(row["funding"] for row in rows.values()) * 100, 2)}


TIER_GATE_KEYS = ("annual_sharpe", "sum_pct", "months", "total_position_annual_pct", "clears_entry",
                  "years_total_position_pct", "cost_pct", "funding_pct")


# --- a lead from the tier sweep, before any hypothesis form (round 37, section 53) -------------------------------------

# The book the tier sweep read, exactly: its variable and the side the sweep chose. Nothing here is a variant of it.
TIER_LEADS = {"funding_30": {"long": "low"}}
TIER_LEAD_CLOSED = "momentum_30"           # H-R2's book on this tier, long its low side: the closed book it is laid beside
TIER_LEAD_BESIDE = ("funding_7",)          # printed for the form's "nearest record" line; no rule reads them
TIER_LEAD_ROW_KEYS = ("long", "start", "months", "annual_sharpe", "sum_pct", "total_position_annual_pct", "clears_entry",
                      "years_total_position_pct", "price_pct", "funding_pct", "cost_pct")
TIER_LEAD_TOP = 5                          # coins and months listed at each end


def _only(weigher: Callable[..., dict[str, float]], sign: int) -> Callable[..., dict[str, float]]:
    """The same book with one side's coins alone. In the held accounting every coin earns on its own, so the two
    one-sided books add up to the whole."""
    def weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                *, seed: str | None = None) -> dict[str, float]:
        return {s: w for s, w in weigher(market, members, day, signal, seed=seed).items() if w * sign > 0}
    return weights


def tier_lead_check(market: xsb.Market, name: str, span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """The tier sweep's lead, read again as the same book and taken apart: the row it must reproduce, the side each
    later year's earlier years name and what the book on that side made that year, the two sides' price, funding
    and cost by year, which coins and months carried it, how much stood on bars with no volume, how alike its
    positions are to the closed book's, and how much of each side the closed funding-tail books would have held."""
    registry = lab.variables()
    read, way = registry[name][1], (1 if TIER_LEADS[name]["long"] == "high" else -1)
    tier = TierSweep(market, span)
    start = tier.start(read)
    months = tier.months(start)
    own = tier.weigher(read, way)

    def held(weigher: Callable[..., dict[str, float]], **more: Any) -> dict[date, dict[str, Any]]:
        return simulate_held(market, bs.CADENCE, {}, start=span[0], end=span[1], weigher=weigher, unit_of=listing_unit,
                             **more)["daily"]

    def monthly(daily: Mapping[date, Mapping[str, Any]]) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for day, row in daily.items():
            month = out.setdefault(_month(day), {"net": 0.0, "cost": 0.0, "funding": 0.0})
            for key in month:
                month[key] += row.get(key, 0.0)
        return out

    def yearly(rows: Mapping[str, Mapping[str, float]]) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for m in months:
            row, year = rows.get(m, {}), out.setdefault(m[:4], {"price": 0.0, "funding": 0.0, "cost": 0.0, "net": 0.0})
            net, cost, funding = (row.get(k, 0.0) * 100 / bs.POSITION_TOTAL for k in ("net", "cost", "funding"))
            for key, value in (("price", net + cost - funding), ("funding", funding), ("cost", cost), ("net", net)):
                year[key] += value
        return {y: {k: round(v, 2) for k, v in row.items()} for y, row in out.items()}

    def dead(symbol: str, decision: date) -> str:
        bar = (market.bars.get(symbol) or {}).get(decision)
        return "no_volume" if bar is not None and bar.quote_volume == 0 else "traded"

    by_coin = held(own, leg_of=lambda symbol, _decision: symbol)
    rows = monthly(by_coin)
    report = tier_reading(rows, months)
    values = [rows.get(m, {}).get("net", 0.0) for m in months]
    cost, funding = (sum(rows.get(m, {}).get(k, 0.0) for m in months) * 100 for k in ("cost", "funding"))
    row = {"long": TIER_LEADS[name]["long"], "start": start.isoformat() if start else None, **report,
           "years_total_position_pct": {y: v["net"] for y, v in yearly(rows).items()},
           "price_pct": round(report["sum_pct"] + cost - funding, 2), "funding_pct": round(funding, 2), "cost_pct": round(cost, 2)}
    unrounded = (statistics.fmean(values) / statistics.stdev(values) * math.sqrt(12)
                 if len(values) >= 2 and statistics.stdev(values) != 0 else None)

    # the side the earlier years name, year by year (rung 2(b): later_years)
    ics = tier.ics(read)
    other_rows: dict[str, dict[str, float]] | None = None
    transfer: dict[str, Any] = {}
    for year in LATER_YEARS:
        earlier = [ic for day, ic in ics.items()
                   if start is not None and day >= start and day + timedelta(days=1 + lab.HORIZONS[0]) <= date(year, 1, 1)]
        if len(earlier) < lab.MIN_DECISIONS:
            transfer[str(year)] = {"decisions_before": len(earlier), "long": None, "total_position_pct": None}
            continue
        mean = statistics.fmean(earlier)
        picked = 1 if mean > 0 else -1
        if picked != way and other_rows is None:
            other_rows = monthly(held(tier.weigher(read, -way)))
        source = rows if picked == way else other_rows
        value = sum(source.get(m, {}).get("net", 0.0) for m in months if m[:4] == str(year)) * 100 / bs.POSITION_TOTAL
        transfer[str(year)] = {"decisions_before": len(earlier), "rank_correlation_before": round(mean, 4),
                               "long": "high" if picked > 0 else "low", "same_side_as_the_lead": picked == way,
                               "total_position_pct": round(value, 2)}
    rule = later_years({y: v["total_position_pct"] for y, v in transfer.items()})

    # the two sides, each a book of its own coins
    sides = {label: yearly(monthly(held(_only(own, sign)))) for label, sign in (("long", 1), ("short", -1))}
    whole = yearly(rows)
    before_costs = sum(v["price"] + v["funding"] for v in whole.values())
    received = sum(v["funding"] for v in whole.values())

    # which coins and which months
    coins: dict[str, float] = {}
    for day, body in by_coin.items():
        if _month(day) in months:
            for symbol, value in body.get("legs", {}).items():
                coins[symbol] = coins.get(symbol, 0.0) + value * 100 / bs.POSITION_TOTAL
    ordered = sorted(coins.items(), key=lambda kv: (-kv[1], kv[0]))
    net = sum(coins.values())
    by_month = sorted(((m, rows.get(m, {}).get("net", 0.0) * 100 / bs.POSITION_TOTAL) for m in months), key=lambda kv: (-kv[1], kv[0]))
    still: dict[str, float] = {}
    for day, body in held(own, leg_of=dead).items():
        if _month(day) in months:
            for label, value in body.get("legs", {}).items():
                still[label] = still.get(label, 0.0) + value * 100 / bs.POSITION_TOTAL

    # positions beside the closed book's and the kin's; the closed funding tails' coins on each side
    closed = tier.weigher(registry[TIER_LEAD_CLOSED][1], -1)
    beside = {kin: tier.weigher(registry[kin][1], way) for kin in TIER_LEAD_BESIDE}
    alike: dict[str, list[float]] = {TIER_LEAD_CLOSED: [], **{kin: [] for kin in TIER_LEAD_BESIDE}}
    deep_long, hot_short, decisions = [], [], 0
    for day in tier.days:
        if start is None or day < start - timedelta(days=7):
            continue
        weights = own(market, (), day, bs.CADENCE)
        if not weights:
            continue
        decisions += 1
        for kin, build in ((TIER_LEAD_CLOSED, closed), *beside.items()):
            if (value := _cosine(weights, build(market, (), day, bs.CADENCE))) is not None:
                alike[kin].append(value)
        week = {s: xsb.trailing_funding(market, s, day, DEEP_WINDOW) for s in weights}
        deep_long.append(sum(w for s, w in weights.items() if w > 0 and week[s] is not None and week[s] <= DEEP_FUNDING))
        hot_short.append(sum(-w for s, w in weights.items() if w < 0 and week[s] is not None and week[s] >= HOT_FUNDING))

    def middle(numbers: Sequence[float]) -> float | None:
        return round(statistics.median(numbers), 4) if numbers else None

    first = middle(alike[TIER_LEAD_CLOSED])
    return {"lead": name, "tier": TIER_SWEEP_TIER, "logic": MID_NAMES[1], "row": row, "annual_sharpe_unrounded": unrounded,
            "transfer": {"years": transfer, "later_years": rule},
            "legs_by_year_total_position_pct": {"book": whole, **sides},
            "funding_share_of_return_before_costs": round(received / before_costs, 3) if before_costs > 0 else None,
            "carry_rule": {"share": DEEP_CARRY_SHARE,
                           "funding_carried_it": bool(before_costs > 0 and received >= DEEP_CARRY_SHARE * before_costs)},
            "coins": {"held": len(coins), "net_total_position_pct": round(net, 2),
                      "top": [[s, round(v, 2)] for s, v in ordered[:TIER_LEAD_TOP]],
                      "bottom": [[s, round(v, 2)] for s, v in ordered[-TIER_LEAD_TOP:]],
                      "top_share_of_net": {str(k): round(sum(v for _, v in ordered[:k]) / net, 3) if net else None
                                           for k in (1, 3, TIER_LEAD_TOP)},
                      "gainers": sum(1 for v in coins.values() if v > 0), "losers": sum(1 for v in coins.values() if v < 0)},
            "months": {"top": [[m, round(v, 2)] for m, v in by_month[:TIER_LEAD_TOP]],
                       "bottom": [[m, round(v, 2)] for m, v in by_month[-TIER_LEAD_TOP:]],
                       "top_share_of_net": {str(k): round(sum(v for _, v in by_month[:k]) / net, 3) if net else None
                                            for k in (1, 3, TIER_LEAD_TOP)},
                       "positive": sum(1 for _, v in by_month if v > 0), "of": len(by_month)},
            "on_bars_with_no_volume_total_position_pct": {k: round(v, 2) for k, v in sorted(still.items())},
            "decisions_with_a_book": decisions,
            "positions_beside": {kin: {"decisions": len(v), "median_cosine": middle(v)} for kin, v in alike.items()},
            "closed_book": {"name": TIER_LEAD_CLOSED, "max": LEAD_KIN_MAX,
                            "is_the_closed_book_again": first is not None and abs(first) >= LEAD_KIN_MAX},
            "closed_funding_tails": {"long_weight_in_deep_coins_median": middle(deep_long),
                                     "long_weight_in_deep_coins_mean": round(statistics.fmean(deep_long), 4) if deep_long else None,
                                     "short_weight_in_hot_coins_median": middle(hot_short),
                                     "short_weight_in_hot_coins_mean": round(statistics.fmean(hot_short), 4) if hot_short else None,
                                     "deep_threshold": DEEP_FUNDING, "hot_threshold": HOT_FUNDING, "window_days": DEEP_WINDOW}}


# --- how large an effect the map's two doors can see (round 39, section 56) ---------------------------------------------

# A planted variable is a calibration object: it is built from the very forward return the cell reads, so it looks
# ahead by construction. It is never registered, never ranked beside a real variable and never printed as one.
DOOR_SIZES = (0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.065, 0.08, 0.10, 0.13, 0.16)
DOOR_SEEDS = 100
DOOR_SHAPES = ("rank", "raw")              # the forward return's normal score among the members, or the return itself
DOOR_NOISE = ("week", "fixed")             # noise redrawn every decision (a best case), or one draw per coin (persistent)
DOOR_DETECTED = 0.8                        # of the seeds: the share at which a door is said to see a size
DOOR_FALSE_ALARMS = 3                      # of the seeds at size 0, in any one combination: more and the planting is broken


def _normal_scores(values: Mapping[str, float]) -> dict[str, float]:
    ranks = lab._ranks([values[s] for s in sorted(values)])
    n = len(ranks)
    normal = statistics.NormalDist()
    return {s: normal.inv_cdf((r - 0.5) / n) for s, r in zip(sorted(values), ranks)}


def _standardised(values: Mapping[str, float]) -> dict[str, float] | None:
    if len(values) < 2:
        return None
    mean, sd = statistics.fmean(values.values()), statistics.pstdev(values.values())
    return {s: (v - mean) / sd for s, v in values.items()} if sd > 0 else None


def door_frames(market: xsb.Market, span: tuple[date, date] = SPAN) -> dict[tuple[str, int], list[tuple[date, dict[str, float]]]]:
    """Per tier and horizon, the map's own decisions with each member's forward return, exactly as the map reads
    them: members of the decision's month, a return only where the coin traded, a decision only with
    :data:`signal_lab.MIN_NAMES` coins. Refuses a market that reaches past the span."""
    latest = lab.last_day(market)
    if latest is not None and latest > span[1]:
        raise ValueError(f"the market reaches {latest}, past the span's last day {span[1]}; cut it first")
    tiers = lab.tier_members(market, span)
    out: dict[tuple[str, int], list[tuple[date, dict[str, float]]]] = {}
    for horizon in lab.HORIZONS:
        days = lab.decisions(span, horizon)
        for tier, monthly in tiers.items():
            rows = []
            for day in days:
                returns = {s: r for s in monthly.get(date(day.year, day.month, 1), ())
                           if (r := lab.forward_return(market, s, day, horizon)) is not None}
                if len(returns) >= lab.MIN_NAMES:
                    rows.append((day, returns))
            out[(tier, horizon)] = rows
    return out


def planted(returns: Mapping[str, float], shape: str, size: float, noise: Mapping[str, float]) -> dict[str, float]:
    """``size`` times the forward return's signal plus the rest in noise: rank shape is the normal score of the
    return's rank, raw shape the return standardised across the coins. Size 0 is noise alone, size 1 the signal."""
    signal = _normal_scores(returns) if shape == "rank" else _standardised(returns)
    if signal is None:
        return {}
    rest = math.sqrt(max(0.0, 1 - size * size))
    return {s: size * signal[s] + rest * noise[s] for s in returns}


def _door_noise(kind: str, seed: int, frames: Sequence[tuple[date, Mapping[str, float]]], tag: str) -> list[dict[str, float]]:
    if kind == "fixed":
        fixed: dict[str, float] = {}
        out = []
        for _, returns in frames:
            for s in sorted(returns):
                if s not in fixed:
                    fixed[s] = random.Random(f"door:fixed:{seed}:{s}").gauss(0.0, 1.0)
            out.append({s: fixed[s] for s in returns})
        return out
    rng = random.Random(f"door:week:{seed}:{tag}")
    return [{s: rng.gauss(0.0, 1.0) for s in sorted(returns)} for _, returns in frames]


def _gross(values: Mapping[str, float], returns: Mapping[str, float]) -> float:
    """What L1's rank weights, long the high side, made on the forward returns: on two units of position."""
    weights = rank_weights(values, long_high=True)
    return sum(w * returns[s] for s, w in weights.items())


def door_size(market: xsb.Market, lines: Mapping[str, Mapping[str, Any]], span: tuple[date, date] = SPAN, *,
              sizes: Sequence[float] = DOOR_SIZES, seeds: int = DOOR_SEEDS,
              frames: Mapping[tuple[str, int], Sequence[tuple[date, Mapping[str, float]]]] | None = None) -> dict[str, Any]:
    """Every planted variable through the map's own cell and both lead rules' lines, on the all-weeks cell: per shape,
    noise, tier, horizon and size, how many seeds each door sees, with what the planted effect is in the map's units
    (mean rank correlation, tercile spread) and in a book's (L1's rank weights before costs, yearly on total position).
    One seed's noise is drawn, read at every shape and size, and dropped before the next."""
    frames = frames if frames is not None else door_frames(market, span)
    rows: list[dict[str, Any]] = []
    for (tier, horizon), decided in sorted(frames.items()):
        ic_line = lines[str(horizon)]["largest_abs_t"]
        spread_line = lines[str(horizon)]["largest_abs_spread_t"]
        per_year = 365.25 / horizon
        for kind in DOOR_NOISE:
            kept = {(shape, size): {"seen": {"ic": 0, "spread": 0, "either": 0, "rule": 0}, "ts": [], "spread_ts": [],
                                    "ics": [], "spreads": [], "grosses": [], "steady": [], "ic_sd": [], "spread_sd": []}
                    for shape in DOOR_SHAPES for size in sizes}
            for k in range(seeds):
                noise = _door_noise(kind, k, decided, f"{tier}:{horizon}")
                for shape in DOOR_SHAPES:
                    for size in sizes:
                        into = kept[(shape, size)]
                        samples, before, rhos, gross = [], None, [], []
                        for position, ((day, returns), draw) in enumerate(zip(decided, noise)):
                            values = planted(returns, shape, size, draw)
                            if len(values) < lab.MIN_NAMES:
                                continue
                            pairs = [(values[s], returns[s]) for s in values]
                            ic = lab.rank_correlation(pairs)
                            if ic is None:
                                continue
                            samples.append((position, ic, lab.tercile_spread(pairs)))
                            gross.append(_gross(values, returns))
                            if before is not None:
                                shared = [(before[s], values[s]) for s in values if s in before]
                                if len(shared) >= lab.MIN_NAMES and (rho := lab.rank_correlation(shared)) is not None:
                                    rhos.append(rho)
                            before = values
                        cell = lab.cell(samples, scenario="all")
                        if cell["too_few"] or cell["t"] is None or cell.get("spread_t") is None:
                            continue
                        persistence = statistics.fmean(rhos) if rhos else None
                        over_ic, over_spread = abs(cell["t"]) > ic_line, abs(cell["spread_t"]) > spread_line
                        seen = into["seen"]
                        seen["ic"] += over_ic
                        seen["spread"] += over_spread
                        seen["either"] += over_ic or over_spread
                        seen["rule"] += over_ic and persistence is not None and persistence >= lab.MIN_PERSISTENCE
                        into["ts"].append(abs(cell["t"]))
                        into["spread_ts"].append(abs(cell["spread_t"]))
                        into["ics"].append(cell["mean_rank_correlation"])
                        into["spreads"].append(cell["tercile_spread_bps"])
                        into["grosses"].append(statistics.fmean(gross) * per_year / bs.POSITION_TOTAL * 100)
                        if persistence is not None:
                            into["steady"].append(persistence)
                        into["ic_sd"].append(statistics.stdev(x[1] for x in samples))
                        into["spread_sd"].append(statistics.stdev(x[2] for x in samples) * 10_000)
                del noise
            for shape in DOOR_SHAPES:
                for size in sizes:
                    into = kept[(shape, size)]
                    n = len(into["ts"])

                    def middle(xs: Sequence[float], places: int = 4) -> float | None:
                        return round(statistics.median(xs), places) if xs else None
                    rows.append({"tier": tier, "horizon_days": horizon, "noise": kind, "shape": shape, "size": size,
                                 "seeds": n, "decisions": len(decided),
                                 **{f"seen_{k}": round(v / n, 3) if n else None for k, v in into["seen"].items()},
                                 "median_abs_t": middle(into["ts"]), "median_abs_spread_t": middle(into["spread_ts"]),
                                 "mean_rank_correlation": round(statistics.fmean(into["ics"]), 5) if n else None,
                                 "tercile_spread_bps": round(statistics.fmean(into["spreads"]), 1) if n else None,
                                 "rank_book_gross_total_position_annual_pct": round(statistics.fmean(into["grosses"]), 2) if n else None,
                                 "median_persistence": middle(into["steady"]),
                                 "median_ic_sd": middle(into["ic_sd"]), "median_spread_sd_bps": middle(into["spread_sd"], 1)})
    return {"lines": {h: {k: lines[h][k] for k in ("largest_abs_t", "largest_abs_spread_t")} for h in map(str, lab.HORIZONS)},
            "sizes": list(sizes), "seeds": seeds, "detected_share": DOOR_DETECTED, "rows": rows,
            "reading": door_reading(rows)}


def door_reading(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per tier, horizon, noise and shape, each door's bracket: the largest size seen in under :data:`DOOR_DETECTED`
    of the seeds and the smallest seen in that share or more. The false-alarm gate reads size 0."""
    groups: dict[tuple[str, int, str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["tier"], row["horizon_days"], row["noise"], row["shape"]), []).append(row)
    brackets, alarms = [], []
    for (tier, horizon, kind, shape), group in sorted(groups.items()):
        group = sorted(group, key=lambda r: r["size"])
        zero = next((r for r in group if r["size"] == 0), None)
        if zero is not None and zero["seeds"] and zero["seen_either"] * zero["seeds"] > DOOR_FALSE_ALARMS:
            alarms.append([tier, horizon, kind, shape, zero["seen_either"]])
        doors = {}
        for door in ("ic", "spread", "rule"):
            first = next((r for r in group if r[f"seen_{door}"] is not None and r[f"seen_{door}"] >= DOOR_DETECTED), None)
            under = [r for r in group if first is None or r["size"] < first["size"]]
            last = under[-1] if under else None

            def at(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
                return None if row is None else {k: row[k] for k in ("size", f"seen_{door}", "mean_rank_correlation",
                                                                     "tercile_spread_bps", "rank_book_gross_total_position_annual_pct")}
            doors[door] = {"below": at(last), "seen": at(first)}
        brackets.append({"tier": tier, "horizon_days": horizon, "noise": kind, "shape": shape, **doors})
    return {"false_alarms": alarms, "planting_broken": bool(alarms), "brackets": brackets}


# --- ranks 101 and after, read by the map and by the tier sweep (round 40, section 57) --------------------------------

LOWER_NAME = lab.LOWER_TIER_NAME           # H-R1's tier as coded: LowerTier's held coins, per decision day
LOWER_COVERAGE = 0.8                       # of what a null book ranks: a variable under it on this tier is not read
LOWER_GATE_RECORD = "skip_day_return"      # H-R1's variable, long its low side: the gate's book


def lower_out() -> dict[str, str]:
    """Every registered variable no book and no lead is taken from on this tier, with the record that says so. The
    map and the sweep read this one list (``signal_lab.SPREAD_NO_BOOK``)."""
    return {name: reason for name in lab.variables() if (reason := lab.no_book(name, LOWER_NAME))}


def lower_names() -> dict[str, tuple[str, lab.Variable]]:
    out = lower_out()
    return {name: body for name, body in lab.variables().items() if name not in out}


def lower_refused_groups() -> set[str]:
    """The groups every variable of which is struck on this tier: they are not loaded, mapped or swept there."""
    groups: dict[str, list[str]] = {}
    for name, (group, _) in lab.variables().items():
        groups.setdefault(group, []).append(name)
    out = lower_out()
    return {group for group, names in groups.items() if all(n in out for n in names)}


def lower_sweep_tier(market: xsb.Market, span: tuple[date, date] = SPAN) -> TierSweep:
    tier = LowerTier(market, span)
    return TierSweep(market, span, members=lambda day: tier.at(day)[0], name=LOWER_NAME)


def lower_gate(market: xsb.Market, span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """H-R1's book through the tier sweep's code on this round's tier, over the whole span as H-R1 was read: it must
    give H-R1's record."""
    return _whole_span(lower_sweep_tier(market, span), skip_day_return, -1, span)


def _next_month(day: date) -> date:
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


def lower_coverage(market: xsb.Market, names: Mapping[str, tuple[str, lab.Variable]],
                   span: tuple[date, date] = SPAN, *, tier: TierSweep | None = None) -> dict[str, Any]:
    """Per variable, on its own span: the share of what a null book ranks (members with a bar that day) that has a
    value, overall and on the coins that are not among the first :data:`LOWER_TIER_FROM` the next month. A file
    fetched for a higher tier's member months covers a coin of this tier mostly when it is about to join that tier;
    the second share sees that. Under :data:`LOWER_COVERAGE` on either, the variable is not read on this tier."""
    tier = tier or lower_sweep_tier(market, span)
    ranked = xsb.universes(market, top_n=LOWER_TIER_FROM,
                           start=date(span[0].year, span[0].month, 1) - timedelta(days=1), end=span[1])
    top = {date(m.year, m.month, 1): set(v) for m, v in ranked.items()}
    out: dict[str, Any] = {}
    for name, (group, read) in names.items():
        start = tier.start(read)
        cells = valued = rest = rest_valued = 0
        for day in tier.days:
            if start is None or day < start - timedelta(days=7):
                continue
            following = top.get(_next_month(day))
            for symbol in tier.members(day):
                if day not in market.bars[symbol]:
                    continue
                has = read(market, symbol, day) is not None
                cells, valued = cells + 1, valued + has
                if following is not None and symbol not in following:
                    rest, rest_valued = rest + 1, rest_valued + has
        share = valued / cells if cells else None
        share_rest = rest_valued / rest if rest else None
        stop = None
        if share is None or share < LOWER_COVERAGE:
            stop = f"has a value on {share if share is None else round(share, 3)} of what a null book ranks, under {LOWER_COVERAGE}"
        elif share_rest is None or share_rest < LOWER_COVERAGE:
            stop = (f"has a value on {share_rest if share_rest is None else round(share_rest, 3)} of the coins not in the "
                    f"first {LOWER_TIER_FROM} the next month, under {LOWER_COVERAGE}")
        out[name] = {"group": group, "start": None if start is None else start.isoformat(), "cells": cells,
                     "share": None if share is None else round(share, 4), "cells_not_top_next_month": rest,
                     "share_not_top_next_month": None if share_rest is None else round(share_rest, 4), "stop": stop}
    return out


def lower_tier_counts(market: xsb.Market, names: Mapping[str, tuple[str, lab.Variable]],
                      span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """What the map and the sweep would stand on there, with nothing earned after a decision in it: coverage and its
    stop, the sweep's counts, and per book the decisions ranked before each of :data:`LATER_YEARS` (the later-years
    rule names a side from them)."""
    tier = lower_sweep_tier(market, span)
    coverage = lower_coverage(market, names, span, tier=tier)
    sweep = tier_sweep_counts(market, names, span, tier=tier)
    for name, (_, read) in names.items():
        before = {str(y): 0 for y in LATER_YEARS}
        for day in tier.ic_days:
            if sum(1 for s in tier.members(day) if read(market, s, day) is not None) >= lab.MIN_NAMES:
                for y in LATER_YEARS:
                    before[str(y)] += day.year < y
        sweep["books"][name]["ranked_decisions_before"] = before
    held = unfunded = 0
    lower = LowerTier(market, span)
    for day in tier.days:
        h, u = lower.at(day)
        held, unfunded = held + len(h), unfunded + len(u)
    return {"coverage": coverage, "coverage_floor": LOWER_COVERAGE, "sweep": sweep,
            "unfunded_share": round(unfunded / (held + unfunded), 4) if held + unfunded else None,
            "returns_computed": False}


def tier_later(tier: TierSweep, read: lab.Variable, months: Sequence[str], *, every: int = 1) -> dict[str, Any]:
    """Rung 2(b) for a sweep's book (section 55.5, in code from round 40): each of :data:`LATER_YEARS` is read on the
    side the book's rank correlations before that year name. A year with fewer than :data:`lab.MIN_DECISIONS` of them
    names no side and counts as zero, as a year with no book did for H-R1; it is never left out."""
    ics = tier.ics(read)
    rows = {way: tier.monthly_rows(read, way, every=every) for way in (1, -1)}
    years: dict[str, float] = {}
    sides: dict[str, Any] = {}
    for year in LATER_YEARS:
        before = [ic for day, ic in ics.items() if day.year < year]
        if len(before) < lab.MIN_DECISIONS:
            years[str(year)] = 0.0
            sides[str(year)] = {"decisions_before": len(before), "long": None}
            continue
        mean = statistics.fmean(before)
        way = 1 if mean > 0 else -1
        years[str(year)] = round(sum(rows[way].get(m, {}).get("net", 0.0) for m in months if m[:4] == str(year))
                                 * 100 / bs.POSITION_TOTAL, 2)
        sides[str(year)] = {"decisions_before": len(before), "mean_rank_correlation_before": round(mean, 4),
                            "long": "high" if way > 0 else "low"}
    return {**later_years(years), "sides": sides}


def lower_sweep(market: xsb.Market, names: Mapping[str, tuple[str, lab.Variable]],
                span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """Round 36's sweep on this tier: a variable under the coverage floor is named and not built; a book that clears
    the entry rule is also read by :func:`tier_later`."""
    tier = lower_sweep_tier(market, span)
    coverage = lower_coverage(market, names, span, tier=tier)
    kept = {n: v for n, v in names.items() if not coverage[n]["stop"]}
    books = tier_sweep(market, kept, span, tier=tier)
    for name, (group, _) in names.items():
        if name not in kept:
            books[name] = {"group": group, "skipped": coverage[name]["stop"]}
    for name, body in books.items():
        if body.get("clears_entry"):
            body["later_years"] = tier_later(tier, names[name][1], tier.months(date.fromisoformat(body["start"])))
    return books


def lower_sweep_reading(books: Mapping[str, Mapping[str, Any]], null: Mapping[str, Mapping[str, Any]],
                        *, q: float = 0.10) -> dict[str, Any]:
    """Round 36's family reading, then the later-years rule: a lead survives Benjamini-Hochberg, clears the entry
    rule AND passes :func:`tier_later`. A book that does the first two only is named, closed at rung 2(b)."""
    reading = tier_sweep_reading(books, null, q=q)
    before = reading["leads"]
    leads = [name for name in before if books[name].get("later_years", {}).get("passes")]
    return {**reading, "leads_before_later_years": before, "leads": leads,
            "closed_at_later_years": [name for name in before if name not in leads]}


# Round 41 (section 58): round 40's two map leads, each with the side its cell names. Nothing here is a variant.
LOWER_LEADS = {"residual_momentum_30": {"long": "low", "horizon_days": 7, "group": "price_volume"},
               "listing_age": {"long": "high", "horizon_days": 28, "group": "coin_character"}}
LOWER_OVERLAP_CLOSE = 0.5                  # |median cosine| with a struck or closed book at which a lead re-builds it


def lower_lead_overlap(market: xsb.Market, span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """Weights only, no return: each lead's book (L22 on this tier, its cell's side) beside every book the record
    strikes on this tier that has values here, and H-R1's closed book. Per comparison, the cosine of the two books'
    coin weights at each weekly decision where both stand; a lead whose median's size reaches
    :data:`LOWER_OVERLAP_CLOSE` against any of them re-builds that book and is closed."""
    tier = lower_sweep_tier(market, span)
    registered = lab.variables()
    refused = lower_refused_groups()
    others: dict[str, Callable[..., dict[str, float]]] = {
        name: tier.weigher(registered[name][1], 1) for name in sorted(lower_out()) if registered[name][0] not in refused}
    others["H-R1"] = tier.weigher(skip_day_return, -1)
    out: dict[str, Any] = {}
    for lead, spec in LOWER_LEADS.items():
        mine = tier.weigher(registered[lead][1], 1 if spec["long"] == "high" else -1)
        values: dict[str, list[float]] = {name: [] for name in others}
        for day in tier.days:
            weights = mine(market, (), day, bs.CADENCE)
            if not weights:
                continue
            for name, build in others.items():
                if (value := _cosine(weights, build(market, (), day, bs.CADENCE))) is not None:
                    values[name].append(value)
        rows = {}
        for name, v in values.items():
            v = sorted(v)
            median = statistics.median(v) if v else None
            rows[name] = {"decisions": len(v), "median_cosine": None if median is None else round(median, 4),
                          "quartiles": [round(v[len(v) // 4], 4), round(v[(3 * len(v)) // 4], 4)] if v else None,
                          "closes": median is not None and abs(median) >= LOWER_OVERLAP_CLOSE}
        out[lead] = {**spec, "beside": rows, "closed_by": sorted(n for n, r in rows.items() if r["closes"])}
    return {"threshold": LOWER_OVERLAP_CLOSE, "compared": sorted(others), "leads": out, "returns_computed": False}


# --- H-A1: listing age on ranks 101+, held four weeks (round 41's D12, section 59) ----------------------------------------

HA1_VARIABLE = "listing_age"
HA1_WAY = 1                                # long the older coins: the side round 40's 28-day cell names (fitted on this span)
HA1_EVERY = 4                              # weeks a decision is held: the map's 28-day decisions exactly (HOLD_ANCHOR)
HA1_HORIZON = 28
HA1_NULLS = 600
HA1_MIN_ACTIVE = 10                        # of the thirteen four-week decisions in each of LATER_YEARS, or nothing is measured


def tie_mean(weights: Mapping[str, float], values: Mapping[str, float]) -> dict[str, float]:
    """``weights`` with every coin that shares its value with another given the mean weight of that group: L1 puts
    tied coins in name order, and this twin has no name in it. Sums are unchanged."""
    groups: dict[float, list[str]] = {}
    for s in weights:
        groups.setdefault(values[s], []).append(s)
    out = dict(weights)
    for members in groups.values():
        if len(members) > 1:
            mean = statistics.fmean(weights[s] for s in members)
            out.update({s: mean for s in members})
    return out


def ha1_twin(tier: TierSweep, read: lab.Variable, way: int) -> Callable[..., dict[str, float]]:
    """The H-A1 book with tied coins sharing their group's mean weight (no name order)."""
    def weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                *, seed: str | None = None) -> dict[str, float]:
        values = {s: v for s in tier.members(day) if (v := read(market, s, day)) is not None}
        return tie_mean(rank_weights(values, long_high=way > 0), values)
    return weights


def ha1_days(tier: TierSweep) -> list[date]:
    return [d for d in tier.days if is_decision(bs.CADENCE, d, HA1_EVERY)]


def ics_at(tier: TierSweep, read: lab.Variable, horizon: int) -> dict[date, float]:
    """The variable's rank correlation with the ``horizon``-day return at the map's decisions for that horizon."""
    out = {}
    for day in lab.decisions(tier.span, horizon):
        pairs = [(v, f) for s in tier.members(day)
                 if (v := read(tier.market, s, day)) is not None
                 and (f := lab.forward_return(tier.market, s, day, horizon)) is not None]
        if len(pairs) >= lab.MIN_NAMES and (ic := lab.rank_correlation(pairs)) is not None:
            out[day] = ic
    return out


def ha1_counts(market: xsb.Market, span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """What H-A1 stands on, with no return: four-week decisions with a book per year, the tied share, the share of
    the long side tied coins carry, how close the book is to its no-name twin, funding coverage and turnover beside
    the null books. Stops when a later year has fewer than :data:`HA1_MIN_ACTIVE` decisions with a book."""
    tier = lower_sweep_tier(market, span)
    read = lab.variables()[HA1_VARIABLE][1]
    mine, twin = tier.weigher(read, HA1_WAY), ha1_twin(tier, read, HA1_WAY)
    years: dict[str, dict[str, list[float]]] = {}
    for day in ha1_days(tier):
        values = {s: v for s in tier.members(day) if (v := read(market, s, day)) is not None}
        w = mine(market, (), day, bs.CADENCE)
        row = years.setdefault(str(day.year), {"decisions": [], "with_book": [], "tied": [], "long_tied": [],
                                               "twin_cosine": [], "unfunded": []})
        row["decisions"].append(1)
        if not w:
            continue
        seen: dict[float, int] = {}
        for v in values.values():
            seen[v] = seen.get(v, 0) + 1
        tied = {s for s, v in values.items() if seen[v] > 1}
        longs = {s: x for s, x in w.items() if x > 0}
        row["with_book"].append(1)
        row["tied"].append(len(tied) / len(values))
        row["long_tied"].append(sum(x for s, x in longs.items() if s in tied) / sum(longs.values()))
        if (c := _cosine(w, twin(market, (), day, bs.CADENCE))) is not None:
            row["twin_cosine"].append(c)
        row["unfunded"].append(sum(1 for s in w if market.funding_day(s) is None) / len(w))
    by_year = {y: {"decisions": len(r["decisions"]), "decisions_with_a_book": len(r["with_book"]),
                   "tied_share_mean": round(statistics.fmean(r["tied"]), 4) if r["tied"] else None,
                   "long_weight_on_tied_mean": round(statistics.fmean(r["long_tied"]), 4) if r["long_tied"] else None,
                   "twin_cosine_median": round(statistics.median(r["twin_cosine"]), 4) if r["twin_cosine"] else None,
                   "unfunded_share_mean": round(statistics.fmean(r["unfunded"]), 4) if r["unfunded"] else None}
               for y, r in sorted(years.items())}

    def turnover(weigher: Callable[..., dict[str, float]]) -> float | None:
        moves, before = [], None
        for day in ha1_days(tier):
            now = weigher(market, (), day, bs.CADENCE)
            if before and now:
                moves.append(sum(abs(now.get(s, 0.0) - before.get(s, 0.0)) for s in set(now) | set(before)) / 2)
            before = now
        return round(statistics.fmean(moves), 4) if moves else None
    own = turnover(mine)
    nulls = [t for t in (turnover(tier.weigher(tier.fixed(f"turnover:{k}"), 1)) for k in range(TIER_SWEEP_TURNOVER_NULLS))
             if t is not None]
    beside = round(statistics.fmean(nulls), 4) if nulls else None
    thin = [str(y) for y in LATER_YEARS if by_year.get(str(y), {}).get("decisions_with_a_book", 0) < HA1_MIN_ACTIVE]
    return {"by_year": by_year, "turnover": own, "null_turnover_mean": beside,
            "turnover_beside_null": round(own / beside, 3) if own is not None and beside else None,
            "stop": [f"fewer than {HA1_MIN_ACTIVE} four-week decisions with a book in {', '.join(thin)}"] if thin else [],
            "returns_computed": False}


def ha1_reading(tier: TierSweep, rows: Mapping[str, Mapping[str, float]], span: tuple[date, date]) -> dict[str, Any]:
    """A book's monthly rows over the whole span, a month with no book counting as zero (H-R1's rule on this tier)."""
    months = bs._complete_months(span[0], span[1])
    report = tier_reading(rows, months)
    years = {str(y): round(sum(r["net"] for m, r in rows.items() if m[:4] == str(y)) * 100 / bs.POSITION_TOTAL, 2)
             for y in range(span[0].year, span[1].year + 1)}
    cost = round(sum(r["cost"] for r in rows.values()) * 100, 2)
    funding = round(sum(r["funding"] for r in rows.values()) * 100, 2)
    return {**report, "years_total_position_pct": years, "cost_pct": cost, "funding_pct": funding,
            "price_pct": round(report["sum_pct"] + cost - funding, 2), "months_list": months}


def ha1_passes(rungs: Mapping[str, Any]) -> bool:
    """All four rungs and the no-name twin's entry rule: a book that clears only in name order is not H-A1."""
    return bool(rungs["2a_entry"] and rungs["2b_later_years"]["passes"] and rungs["2c_beside_null_books"]["passes"]
                and rungs["3_stability"]["passes"] and rungs["no_name_twin_entry"])


def ha1_run(market: xsb.Market, span: tuple[date, date] = SPAN, *, nulls: int = HA1_NULLS) -> dict[str, Any]:
    """H-A1 through rung 2's three conditions, rung 3 and the no-name twin. Refused when the counts say stop."""
    counts = ha1_counts(market, span)
    if counts["stop"]:
        return {"refused": counts["stop"], "counts": counts}
    tier = lower_sweep_tier(market, span)
    read = lab.variables()[HA1_VARIABLE][1]
    report = ha1_reading(tier, tier.monthly_rows(read, HA1_WAY, every=HA1_EVERY), span)
    months = report.pop("months_list")
    twin = ha1_reading(tier, tier.monthly_rows(read, HA1_WAY, every=HA1_EVERY, weigher=ha1_twin(tier, read, HA1_WAY)), span)
    twin.pop("months_list")
    null_sharpes, null_sides = [], {"high": 0, "low": 0}
    for k in range(nulls):
        fixed = tier.fixed(f"ha1:{k}")
        ics = ics_at(tier, fixed, HA1_HORIZON)
        way = 1 if ics and statistics.fmean(ics.values()) > 0 else -1
        null_sides["high" if way > 0 else "low"] += 1
        null_sharpes.append(ha1_reading(tier, tier.monthly_rows(fixed, way, every=HA1_EVERY), span)["annual_sharpe"])
    own_ics = ics_at(tier, read, HA1_HORIZON)
    rungs = {"2a_entry": report["clears_entry"],
             "2b_later_years": tier_later(tier, read, months, every=HA1_EVERY),
             "2c_beside_null_books": beside_nulls(report["annual_sharpe"], null_sharpes),
             "3_stability": stability(report["years_total_position_pct"]),
             "no_name_twin_entry": twin["clears_entry"]}
    passes = ha1_passes(rungs)
    return {**report, "hypothesis": "H-A1", "logic": "L23", "counts": counts, "rungs": rungs, "passes": passes,
            "twin": twin, "null_sides": null_sides,
            "own_28_day_ics": {"decisions": len(own_ics),
                               "mean": round(statistics.fmean(own_ics.values()), 4) if own_ics else None},
            "fixed_side_years_unread": {y: report["years_total_position_pct"].get(y) for y in map(str, LATER_YEARS)},
            "reading": "passes" if passes else "closed"}


def lower_map(market: xsb.Market, names: Mapping[str, tuple[str, lab.Variable]],
              span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """The map on this tier alone, against the random variables on this tier alone: its own lines, so the lines of
    the two mapped tiers are never recomputed. A variable under the coverage floor is not mapped; a lead on a struck
    variable says so."""
    lower = LowerTier(market, span)
    members = {LOWER_NAME: lambda day: lower.at(day)[0]}
    coverage = lower_coverage(market, names, span,
                              tier=TierSweep(market, span, members=members[LOWER_NAME], name=LOWER_NAME))
    kept = {n: v for n, v in names.items() if not coverage[n]["stop"]}
    real = lab.grid(market, kept, span, members=members)
    random_cells = lab.grid(market, lab.controls(), span, members=members)
    against = lab.chance(random_cells["cells"])
    leads = lab.candidates(real, against, cap=None)        # every cell that meets the rule: struck ones are named, not counted
    leads["leads"] = [{**c, "no_book": lab.no_book(c["variable"], c["tier"])} for c in leads["leads"]]
    return {"tier": LOWER_NAME, "coverage": coverage, "not_mapped": {n: coverage[n]["stop"] for n in names if n not in kept},
            "chance": against, "excess": lab.excess(real["cells"], against), "candidates": leads,
            "spread_candidates": lab.spread_candidates(real, against), "dropped": real["dropped"],
            "variables": real["variables"], "cells": real["cells"]}


# --- L19: the open-interest flush book (round 15, hypothesis H-O1) -------------------------------------------------------

FLUSH_SPAN = lab.ms.DEV_LONG               # the positioning files begin in 2022 for all but a few coins
FLUSH_OI = -0.10                           # the open interest at the day's close against the close before, at or under this
FLUSH_MOVE = -0.05                         # and the coin's own close against the close before, at or under this
FLUSH_CLOSING_HOUR = 23                    # the day's last positioning row must be stamped this hour, or the day has no value
FLUSH_HOLD_DAYS = 3                        # bought at the next open and held this many days (round 10's 72 hours)
FLUSH_BETA_DAYS = 60
FLUSH_NEIGHBOURS = 3                       # the matched placebo draws among this many members nearest in the day's move
FLUSH_PLACEBOS = 300
FLUSH_CADENCE = "S-REV"                    # only its daily cadence is used
FLUSH_WIDE = 10                            # members flushing on one day at or over this: the day is the market's, no event is taken
FLUSH_MIN_EVENT_DAYS = 30                  # days with an event over the span, or nothing is measured (round 10's number)
FLUSH_MIN_EVENT_DAYS_A_YEAR = 10           # and in each year of the span
FLUSH_MIN_KNOWN = 0.9                      # of each year's member coin-days must have the open interest of both closes
FLUSH_MAX_NO_PLACEBO = 0.1                 # of the events may be without a placebo coin, of either kind


class Flushes:
    """Per day, read from that day and earlier: the month's top thirty but the hedge coin, each with the change in
    its open interest from close to close and its own move; the events (both at or past their thresholds, on a day
    that is not the market's); and the coins a book holds the next day (an event is held :data:`FLUSH_HOLD_DAYS`
    days)."""

    def __init__(self, market: xsb.Market, span: tuple[date, date] = FLUSH_SPAN) -> None:
        self.market = market
        self.monthly = monthly_members(market, span)
        self._read: dict[date, dict[str, tuple[float | None, float | None]]] = {}

    def read(self, day: date) -> dict[str, tuple[float | None, float | None]]:
        if day not in self._read:
            metrics, before = getattr(self.market, "closing_oi", {}), day - timedelta(days=1)
            out: dict[str, tuple[float | None, float | None]] = {}
            for symbol in self.monthly.get(date(day.year, day.month, 1), ()):
                if symbol == HEDGE:
                    continue
                days, bars = metrics.get(symbol) or {}, self.market.bars.get(symbol) or {}
                now, then = days.get(day), days.get(before)
                a, b = bars.get(day), bars.get(before)
                out[symbol] = (None if now is None or not then else now / then - 1,
                               None if a is None or b is None or b.close <= 0 else a.close / b.close - 1)
            self._read[day] = out
        return self._read[day]

    def flushed(self, day: date) -> tuple[str, ...]:
        return tuple(s for s, (change, move) in self.read(day).items()
                     if change is not None and move is not None and change <= FLUSH_OI and move <= FLUSH_MOVE)

    def events(self, day: date) -> tuple[str, ...]:
        """The day's flushed coins, or none on a day so many flush that it is the market's (round 10's ground)."""
        flushed = self.flushed(day)
        return flushed if len(flushed) < FLUSH_WIDE else ()

    def partner(self, symbol: str, day: date, seed: str, *, matched: bool) -> str | None:
        """The member a placebo holds in place of an event's coin: one whose open interest is known that day and
        that is not an event. ``matched`` draws among the :data:`FLUSH_NEIGHBOURS` nearest in the day's move: a coin
        that fell as much while its open interest stayed."""
        read, events = self.read(day), set(self.events(day))
        others = sorted(s for s, (change, move) in read.items() if s not in events and change is not None and move is not None)
        if matched and others and symbol in read:
            own = read[symbol][1]
            others = sorted(others, key=lambda s: (abs(read[s][1] - own), s))[:FLUSH_NEIGHBOURS]
        return random.Random(f"{seed}:{symbol}:{day.isoformat()}").choice(others) if others else None

    def names(self, day: date, placebo: str | None = None, *, matched: bool = False) -> list[str]:
        out: list[str] = []
        for back in range(FLUSH_HOLD_DAYS):
            at = day - timedelta(days=back)
            for symbol in self.events(at):
                name = symbol if placebo is None else self.partner(symbol, at, placebo, matched=matched)
                if name is not None:
                    out.append(name)
        return sorted(set(out))


def flush_book(flushes: Flushes, placebo: str | None = None, *, matched: bool = False,
               hedged: bool = True) -> Callable[..., dict[str, float]]:
    """L19: every coin with an event in the last :data:`FLUSH_HOLD_DAYS` days bought in equal weights under L15's
    cap, the hedge coin sold against their summed beta."""
    def weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                *, seed: str | None = None) -> dict[str, float]:
        betas = {s: beta for s in flushes.names(day, placebo, matched=matched)
                 if (beta := bs._beta(market, s, day, FLUSH_BETA_DAYS)) is not None}
        return deep_basket(betas, betas, hedged=hedged)
    return weights


def flush_counts(market: xsb.Market, span: tuple[date, date] = FLUSH_SPAN, *,
                 crash_days: Iterable[date] = ()) -> dict[str, Any]:
    """What the flush book would stand on, with nothing after an event in it: events and the days they fall on, the
    days left out as the market's, how much of the universe has its open interest on file, whether a placebo can be
    drawn, and how many events fall on round 10's event days (``crash_days``) or the day after."""
    flushes = Flushes(market, span)
    crashes = {d + timedelta(days=k) for d in crash_days for k in (0, 1)}
    years: dict[str, dict[str, Any]] = {str(y): {"events": 0, "event_days": 0, "days_with_a_position": 0, "names": [],
                                                 "wide_days": 0, "flushes_on_wide_days": 0,
                                                 "member_coin_days": 0, "known_coin_days": 0}
                                        for y in range(span[0].year, span[1].year + 1)}
    no_plain = no_matched = on_crash = 0
    oi_changes: list[float | None] = []
    moves: list[float | None] = []
    partner_moves: list[float | None] = []     # the day's move of the coin the matched placebo would hold instead
    for day in xsb.held_days(*span):
        row, read, events, flushed = years[str(day.year)], flushes.read(day), flushes.events(day), flushes.flushed(day)
        row["member_coin_days"] += len(read)
        row["known_coin_days"] += sum(1 for change, _ in read.values() if change is not None)
        names = flushes.names(day)
        if names:
            row["days_with_a_position"] += 1
            row["names"].append(len(names))
        if flushed and not events:
            row["wide_days"] += 1
            row["flushes_on_wide_days"] += len(flushed)
        if events:
            row["events"] += len(events)
            row["event_days"] += 1
            on_crash += len(events) if day in crashes else 0
            for symbol in events:
                oi_changes.append(read[symbol][0])
                moves.append(read[symbol][1])
                no_plain += flushes.partner(symbol, day, "counts", matched=False) is None
                alike = flushes.partner(symbol, day, "counts", matched=True)
                no_matched += alike is None
                if alike is not None:
                    partner_moves.append(read[alike][1])
    total = sum(r["events"] for r in years.values())
    event_days = sum(r["event_days"] for r in years.values())
    for row in years.values():
        names = row.pop("names")
        row["names_held_median"] = statistics.median(names) if names else 0
        row["known_share"] = round(row["known_coin_days"] / row["member_coin_days"], 3) if row["member_coin_days"] else None
    thin = [y for y, r in years.items() if r["event_days"] < FLUSH_MIN_EVENT_DAYS_A_YEAR]
    blind = [y for y, r in years.items() if r["known_share"] is None or r["known_share"] < FLUSH_MIN_KNOWN]
    bare = max(no_plain, no_matched) / total if total else 1.0
    crash_share = on_crash / total if total else None
    stops = [text for wrong, text in (
        (event_days < FLUSH_MIN_EVENT_DAYS, "too few days with an event over the span"),
        (bool(thin), "too few days with an event in " + ", ".join(thin)),
        (bool(blind), "too little of the universe has its open interest on file in " + ", ".join(blind)),
        (bare > FLUSH_MAX_NO_PLACEBO, "too many events have no placebo coin to draw"),
        (crash_share is not None and crash_share >= lab.sp.OVERLAP_LIMIT,
         "the events fall on round 10's event days: H-C1 on daily bars, not a reading of its own"),
    ) if wrong]
    return {"events": total, "event_days": event_days, "by_year": years,
            "events_on_crash_days_share": None if crash_share is None else round(crash_share, 3),
            "crash_days_limit": lab.sp.OVERLAP_LIMIT,
            "events_without_a_placebo_share": {"plain": round(no_plain / total, 3) if total else None,
                                               "matched": round(no_matched / total, 3) if total else None},
            "events_signal_median": {"open_interest_change": _median(oi_changes), "day_move": _median(moves)},
            # a decision-day value, not a return after it: whether the matched placebo's coins fell as the events did
            "matched_partner_day_move_median": _median(partner_moves),
            "last_day_in_market": lab.last_day(market).isoformat() if market.bars else None,
            "stop": stops, "returns_computed": False}


def flush_run(market: xsb.Market, span: tuple[date, date] = FLUSH_SPAN, *, placebos: int = FLUSH_PLACEBOS,
              crash_days: Iterable[date] = ()) -> dict[str, Any]:
    """The flush book net of stressed costs, beside the same book on other members and on members that fell as
    much, through rung 2's three conditions and rung 3 over the span the positioning files cover."""
    counts = flush_counts(market, span, crash_days=crash_days)
    if counts["stop"]:
        return {"refused": counts["stop"], "counts": counts}
    flushes = Flushes(market, span)
    stressed = functools.partial(simulate_held, unit_of=listing_unit)

    def read(weigher: Callable[..., dict[str, float]], simulate: Callable[..., Any] = stressed) -> dict[str, Any]:
        report = measure(market, weigher, span, simulate=simulate, cadence=FLUSH_CADENCE)
        years = {str(y): report["years_total_position_pct"].get(str(y), 0.0) for y in range(span[0].year, span[1].year + 1)}
        return {**report, "years_total_position_pct": years, "stability": stability(years)}

    report, bare = read(flush_book(flushes)), read(flush_book(flushes, hedged=False))
    side = ("annual_sharpe", "total_position_annual_pct")
    plain_rows = [tuple(r[k] for k in side) for r in (read(flush_book(flushes, placebo=f"plain:{k}")) for k in range(placebos))]
    alike_rows = [tuple(r[k] for k in side) for r in
                  (read(flush_book(flushes, placebo=f"matched:{k}", matched=True)) for k in range(placebos))]

    def net_mean(rows: Sequence[tuple[float | None, float | None]]) -> float | None:
        kept = [row[1] for row in rows if row[1] is not None]
        return round(statistics.fmean(kept), 2) if kept else None

    rungs = {"2a_entry": report["clears_entry"], "2b_later_years": later_years(report["years_total_position_pct"]),
             "2c_beside_placebos": beside_nulls(report["annual_sharpe"], [row[0] for row in plain_rows]),
             "3_stability": report["stability"]}
    matched = beside_nulls(report["annual_sharpe"], [row[0] for row in alike_rows])
    ladder = bool(rungs["2a_entry"] and rungs["2b_later_years"]["passes"] and rungs["2c_beside_placebos"]["passes"]
                  and rungs["3_stability"]["passes"])
    reading = "closed" if not ladder else "not_apart_from_beaten_down_coins" if not matched["passes"] else "passes"
    return {**report, "hypothesis": "H-O1", "logic": "L19", "counts": counts, "rungs": rungs,
            "beside_matched_placebos": matched, "legs": deep_legs(report, bare),
            "placebo_net_total_position_annual_pct_mean": {"plain": net_mean(plain_rows), "matched": net_mean(alike_rows)},
            "without_the_hedge": {k: bare[k] for k in ("annual_sharpe", "total_position_annual_pct", "years_total_position_pct")},
            "at_engine_cost_annual_sharpe": read(flush_book(flushes), simulate_held)["annual_sharpe"],
            "passes": ladder and matched["passes"], "reading": reading}


def closing_oi(folder: Path, symbols: Iterable[str], span: tuple[date, date] = FLUSH_SPAN) -> dict[str, dict[date, float]]:
    """Each symbol's open interest at the day's close: ``sum_open_interest`` on the row of the day's file with the
    latest stamp of that day, when that stamp is at :data:`FLUSH_CLOSING_HOUR` or later. The latest stamp, not the
    file's last row: some files (most of April 2024) are not in time order. Only files dated from the day before the
    span to its last day are opened."""
    first, out = span[0] - timedelta(days=1), {}
    for symbol in sorted(set(symbols)):
        days: dict[date, float] = {}
        for path in sorted((folder / symbol).glob("*.zip")) if (folder / symbol).is_dir() else []:
            try:
                day = date.fromisoformat(path.stem[-10:])
                if not first <= day <= span[1]:
                    continue
                with zipfile.ZipFile(path) as z:
                    rows = list(csv.reader(io.StringIO(z.read(z.namelist()[0]).decode("utf-8"))))
                stamp, raw = max((row[0], row[2]) for row in rows[1:] if len(row) > 2 and row[0][:10] == day.isoformat())
                value = float(raw)
            except (OSError, ValueError, IndexError, zipfile.BadZipFile):
                continue
            if int(stamp[11:13]) >= FLUSH_CLOSING_HOUR and math.isfinite(value) and value > 0:
                days[day] = value
        if days:
            out[symbol] = days
    return out


def crash_days_of(hourly: Hourly, span: tuple[date, date] = FLUSH_SPAN) -> list[date]:
    """The UTC days round 10's events fall on, within ``span``. The events are found over round 10's own span, so
    they are the hours that round measured (an event's place depends on the events before it)."""
    days = {day_of(hour) for hour in crash_events(hourly)}
    return sorted(d for d in days if span[0] <= d <= span[1])


# --- L16: the alt-against-Bitcoin rotation book (round 12, hypothesis H-D1) ----------------------------------------

ROTATION_WINDOW = 28                       # days of the spread's own return that set the side
ROTATION_BETA_DAYS = 60
ROTATION_SHIFT_MARGIN = 8                  # decisions a placebo's shift keeps away from zero, at both ends
ROTATION_MIN_FLIPS = 6                     # over the span: under it the book is a standing bet no shift can be told from
ROTATION_MIN_ACTIVE = 26                   # decisions with a side each of LATER_YEARS needs, or nothing is measured


class Rotation:
    """The side of the alt basket against the hedge coin at each decision, from the spread's own trailing return.

    The signal is read on what could have been held through the window: the top thirty of the month the window
    *begins* in, with the betas of the day it begins. This month's top thirty would not do. A coin enters it on a
    volume surge, which follows a run-up, so its trailing return is high by selection and the sign would lean to
    "alts up" after every refresh."""

    def __init__(self, market: xsb.Market, span: tuple[date, date] = SPAN) -> None:
        self.market = market
        self.monthly = monthly_members(market, span)
        self.days = [d for d in sundays(span) if span[0] - timedelta(days=1) <= d <= span[1]]
        self._signal: dict[date, float | None] = {}
        self._active: list[tuple[date, int]] | None = None
        self._index: dict[date, int] = {}

    def signal(self, day: date) -> float | None:
        if day not in self._signal:
            start = day - timedelta(days=ROTATION_WINDOW)
            rows = [(moved, beta) for s in self.monthly.get(date(start.year, start.month, 1), ()) if s != HEDGE
                    if (moved := xsb.trailing_return(self.market, s, day, ROTATION_WINDOW)) is not None
                    and (beta := bs._beta(self.market, s, start, ROTATION_BETA_DAYS)) is not None]
            ref = xsb.trailing_return(self.market, HEDGE, day, ROTATION_WINDOW)
            self._signal[day] = (None if ref is None or len(rows) < lab.MIN_NAMES else
                                 statistics.fmean(m for m, _ in rows) - statistics.fmean(b for _, b in rows) * ref)
        return self._signal[day]

    def basket(self, members: Sequence[str], day: date) -> dict[str, float]:
        """The coins the book trades at ``day`` (this month's members but the hedge), each with its beta."""
        return {s: beta for s in members
                if s != HEDGE and (beta := bs._beta(self.market, s, day, ROTATION_BETA_DAYS)) is not None}

    def active(self) -> list[tuple[date, int]]:
        """The decisions with a side, in order: a signal that is not zero and enough coins to trade."""
        if self._active is None:
            rows = []
            for day in self.days:
                value = self.signal(day)
                members = self.monthly.get(date(day.year, day.month, 1), ())
                if value and len(self.basket(members, day)) >= lab.MIN_NAMES:
                    rows.append((day, 1 if value > 0 else -1))
            self._active = rows
            self._index = {day: i for i, (day, _) in enumerate(rows)}
        return self._active

    def way(self, day: date, shift: int = 0) -> int:
        """+1 long the alts, -1 short them, 0 flat. With ``shift`` the side is the one the real book took that many
        active decisions later, wrapping round: the same runs in the same order, moved off the returns they led."""
        rows = self.active()
        at = self._index.get(day)
        return 0 if at is None else rows[(at + shift) % len(rows)][1]

    def shifts(self) -> range:
        return range(ROTATION_SHIFT_MARGIN, len(self.active()) - ROTATION_SHIFT_MARGIN + 1)


def rotation_book(rot: Rotation, shift: int = 0, *, hedge: str | None = "beta") -> Callable[..., dict[str, float]]:
    """L16: the month's top thirty but the hedge coin in equal weights on the signal's side, and the hedge coin
    against them: by their summed beta (``"beta"``, the judged book), by the same dollars (``"dollar"``), or not at
    all (None, the alts alone for the leg split)."""
    def weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                *, seed: str | None = None) -> dict[str, float]:
        way, betas = rot.way(day, shift), rot.basket(members, day)
        if not way or len(betas) < lab.MIN_NAMES:
            return {}
        weight = way / len(betas)
        out = dict.fromkeys(sorted(betas), weight)
        if hedge == "beta":
            out[HEDGE] = -weight * sum(betas.values())
        elif hedge == "dollar":
            out[HEDGE] = -float(way)
        return out
    return weights


def rotation_counts(market: xsb.Market, span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """What the rotation book would stand on, with no return of the book and no side in it: decisions with a side,
    how often the side turns and how long it lasts. The stop rules are judged here."""
    rot = Rotation(market, span)
    years: dict[str, dict[str, Any]] = {str(y): {"decisions": 0, "active_decisions": 0, "flips": 0, "names": []}
                                        for y in range(span[0].year, span[1].year + 1)}
    for day in rot.days:
        if str(day.year) in years:
            years[str(day.year)]["decisions"] += 1
    runs: list[int] = []
    previous = None
    for day, way in rot.active():
        row = years.get(str(day.year))
        if row is None:
            continue
        row["active_decisions"] += 1
        row["names"].append(len(rot.basket(rot.monthly.get(date(day.year, day.month, 1), ()), day)))
        if way != previous:
            if previous is not None:
                row["flips"] += 1
            runs.append(0)
        runs[-1] += 1
        previous = way
    for row in years.values():
        names = row.pop("names")
        row["names_traded_median"] = statistics.median(names) if names else 0
    flips = sum(row["flips"] for row in years.values())
    thin = [str(y) for y in LATER_YEARS if years.get(str(y), {}).get("active_decisions", 0) < ROTATION_MIN_ACTIVE]
    stops = [text for wrong, text in (
        (bool(thin), "the book has a side at too few decisions in " + ", ".join(thin)),
        (flips < ROTATION_MIN_FLIPS, "the side turns too rarely to be told from a standing bet"),
    ) if wrong]
    return {"decisions": len(rot.days), "active_decisions": len(rot.active()), "flips": flips, "runs": len(runs),
            "run_weeks_median": statistics.median(runs) if runs else 0, "placebo_shifts": len(rot.shifts()),
            "by_year": years, "last_day_in_market": lab.last_day(market).isoformat() if market.bars else None,
            "stop": stops, "returns_computed": False}


def rotation_run(market: xsb.Market, span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """The rotation book net of stressed costs, beside the same sides shifted in time, through rung 2's three
    conditions and rung 3. A year the book is flat in counts as zero. Refused when the counts say stop."""
    counts = rotation_counts(market, span)
    if counts["stop"]:
        return {"refused": counts["stop"], "counts": counts}
    rot = Rotation(market, span)
    stressed = functools.partial(simulate_held, unit_of=listing_unit)

    def whole(report: dict[str, Any]) -> dict[str, Any]:
        years = {str(y): report["years_total_position_pct"].get(str(y), 0.0)
                 for y in range(span[0].year, span[1].year + 1)}
        return {**report, "years_total_position_pct": years, "stability": stability(years)}

    report = whole(measure(market, rotation_book(rot), span, simulate=stressed))
    bare = whole(measure(market, rotation_book(rot, hedge=None), span, simulate=stressed))
    dollar = whole(measure(market, rotation_book(rot, hedge="dollar"), span, simulate=stressed))
    engine = whole(measure(market, rotation_book(rot), span, simulate=simulate_held))
    nulls = [measure(market, rotation_book(rot, shift), span, simulate=stressed)["annual_sharpe"] for shift in rot.shifts()]
    rungs = {"2a_entry": report["clears_entry"], "2b_later_years": later_years(report["years_total_position_pct"]),
             "2c_beside_shifted_sides": beside_nulls(report["annual_sharpe"], nulls), "3_stability": report["stability"]}
    passes = bool(rungs["2a_entry"] and rungs["2b_later_years"]["passes"] and rungs["2c_beside_shifted_sides"]["passes"]
                  and rungs["3_stability"]["passes"])
    lead = not passes and bool(engine["clears_entry"]) and rungs["2c_beside_shifted_sides"]["passes"]
    years = report["years_total_position_pct"]
    total, best = sum(years.values()), max(years.values())
    side = ("annual_sharpe", "total_position_annual_pct", "years_total_position_pct")
    return {**report, "hypothesis": "H-D1", "logic": "L16", "counts": counts, "rungs": rungs, "passes": passes,
            "legs": deep_legs(report, bare),
            "legs_by_year_total_position_pct": {
                "alts": bare["years_total_position_pct"],
                "hedge": {y: round(years[y] - bare["years_total_position_pct"][y], 2) for y in years}},
            "largest_year_share": round(best / total, 3) if total > 0 and best > 0 else None,
            "dollar_hedged": {k: dollar[k] for k in side},
            "at_engine_cost": {k: engine[k] for k in (*side, "clears_entry", "cost_pct")},
            "reading": "passes" if passes else "cost_dependent_lead" if lead else "closed"}


# --- the ladder's bar (round 13, a tool round) --------------------------------------------------------------------

BAR_SHAPES = ("rank_week", "rank_fixed", "deep", "rotation", "crash")
BAR_BOOKS = 300                            # null books a shape is read on (the rotation shape has as many as it has shifts)
BAR_SEED = 3000                            # the rank shapes' random variables; the sweep's begin at 1000, round 9's at 2000
BAR_GRID = tuple(k * 2.5 for k in range(49))   # the net return a null book is moved to, % of total position a year: 0 .. 120
BAR_LEVELS = (0.5, 0.8)                    # the pass rates the bar is read at


def read_daily(daily: Mapping[date, Mapping[str, float]], span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """:func:`measure`'s reading of a daily series that is already simulated, with every year of the span present
    (a flat year is zero, as the hypothesis rounds count it). A test holds the two to the same numbers."""
    start, end = span
    sharpe, values = bs.annual_sharpe(daily, start, end)
    report: dict[str, Any] = {"annual_sharpe": sharpe, "sum_pct": round(sum(values) * 100, 2), "months": len(values)}
    report["total_position_annual_pct"] = bs.total_position_annual_pct(report["sum_pct"], report["months"])
    report["clears_entry"] = bs.clears_entry(report)
    by_year = dict.fromkeys(range(start.year, end.year + 1), 0.0)
    for day, row in daily.items():
        if day.year in by_year:
            by_year[day.year] += row["net"]
    report["years_total_position_pct"] = {str(y): round(v * 100 / bs.POSITION_TOTAL, 2) for y, v in by_year.items()}
    report["stability"] = stability(report["years_total_position_pct"])
    return report


def squeeze(daily: Mapping[date, Mapping[str, float]], span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """All of a daily series the ladder reads, and all a steady edge needs: net and the number of days with a row,
    by complete month and by year, and the cost. Three hundred full series do not fit beside the market; these do."""
    start, end = span
    months = bs._complete_months(start, end)
    net, held = dict.fromkeys(months, 0.0), dict.fromkeys(months, 0)
    year_net, year_held = dict.fromkeys(range(start.year, end.year + 1), 0.0), dict.fromkeys(range(start.year, end.year + 1), 0)
    cost = 0.0
    for day, row in daily.items():
        key = day.strftime("%Y-%m")
        if key in net:
            net[key] += row["net"]
            held[key] += 1
            cost += row.get("cost", 0.0)
        if day.year in year_net and start <= day <= end:
            year_net[day.year] += row["net"]
            year_held[day.year] += 1
    return {"net": [net[m] for m in months], "held": [held[m] for m in months],
            "year_net": year_net, "year_held": year_held, "cost": cost}


def read_squeezed(book: Mapping[str, Any], add: float = 0.0) -> dict[str, Any]:
    """:func:`read_daily` of the series ``book`` was squeezed from, with ``add`` put on every day it has a row. A book
    earns nothing while flat, so a year it is mostly out of gets less of what is added."""
    values = [n + add * h for n, h in zip(book["net"], book["held"])]
    spread = statistics.stdev(values) if len(values) > 1 else 0.0
    sharpe = round(statistics.fmean(values) / spread * math.sqrt(12), 3) if spread else None
    report: dict[str, Any] = {"annual_sharpe": sharpe, "sum_pct": round(sum(values) * 100, 2), "months": len(values)}
    report["total_position_annual_pct"] = bs.total_position_annual_pct(report["sum_pct"], report["months"])
    report["clears_entry"] = bs.clears_entry(report)
    report["years_total_position_pct"] = {str(y): round((book["year_net"][y] + add * book["year_held"][y]) * 100 / bs.POSITION_TOTAL, 2)
                                          for y in book["year_net"]}
    report["stability"] = stability(report["years_total_position_pct"])
    return report


def moved_to(book: Mapping[str, Any], net_pct: float) -> float | None:
    """What to add on each of the book's days so that its net return is ``net_pct`` of total position a year; None
    for a book that never holds anything."""
    days = sum(book["held"])
    if not days:
        return None
    return (net_pct / 100 * bs.POSITION_TOTAL * len(book["net"]) / 12 - sum(book["net"])) / days


def _crossing(rows: Sequence[Mapping[str, Any]], level: float) -> float | None:
    """The first net return on the grid at which the share passing every condition reaches ``level``; None when it
    is not reached. Not interpolated: the floor is a step, and a line across it would put the bar under the floor."""
    return next((row["net_pct"] for row in rows if row["all"] is not None and row["all"] >= level), None)


def bar_curve(books: Sequence[Mapping[str, Any]], grid: Sequence[float] = BAR_GRID) -> dict[str, Any]:
    """How large a steady net return a shape's null books need before they pass the ladder. Each null book is moved
    to the net return and read through rung 2's three conditions and rung 3; its null for 2(c) is the other books,
    unmoved. No real edge is this steady: what passes here is a floor under what a real book would need."""
    base = [read_squeezed(book) for book in books]
    sharpes = [b["annual_sharpe"] for b in base]
    years = len(books[0]["net"]) / 12 if books else 0
    rows = []
    for target in grid:
        seen = {"sharpe": 0, "floor": 0, "2a": 0, "2b": 0, "2c": 0, "3": 0, "all": 0}
        moved = 0
        for i, book in enumerate(books):
            add = moved_to(book, target)
            if add is None:
                continue
            moved += 1
            report = read_squeezed(book, add)
            sharpe, yearly = report["annual_sharpe"], report["total_position_annual_pct"]
            passed = {"sharpe": sharpe is not None and sharpe >= bs.ENTRY_SHARPE,
                      "floor": yearly is not None and yearly >= bs.ENTRY_RETURN_TOTAL_PCT,
                      "2a": report["clears_entry"],
                      "2b": later_years(report["years_total_position_pct"])["passes"],
                      "2c": beside_nulls(sharpe, sharpes[:i] + sharpes[i + 1:])["passes"],
                      "3": report["stability"]["passes"]}
            passed["all"] = passed["2a"] and passed["2b"] and passed["2c"] and passed["3"]
            for key, ok in passed.items():
                seen[key] += bool(ok)
        rows.append({"net_pct": target, **{k: round(v / moved, 3) if moved else None for k, v in seen.items()}})
    kept = [v for v in sharpes if v is not None]
    nets = [b["total_position_annual_pct"] for b in base if b["total_position_annual_pct"] is not None]
    costs = [book["cost"] * 100 / bs.POSITION_TOTAL / years for book in books] if years else []
    net, cost = (statistics.fmean(nets) if nets else None), (statistics.fmean(costs) if costs else None)
    return {"books": len(books),
            "null": {"net_total_position_annual_pct_mean": None if net is None else round(net, 2),
                     "cost_total_position_annual_pct_mean": None if cost is None else round(cost, 2),
                     "before_cost_total_position_annual_pct_mean": None if net is None or cost is None else round(net + cost, 2),
                     "annual_sharpe_mean": round(statistics.fmean(kept), 3) if kept else None,
                     "annual_sharpe_sd": round(statistics.stdev(kept), 3) if len(kept) > 1 else None},
            "floor_pct": bs.ENTRY_RETURN_TOTAL_PCT,
            "bar_net_pct": {str(level): _crossing(rows, level) for level in BAR_LEVELS},
            "curve": rows}


def bar_books(market: xsb.Market, shape: str, span: tuple[date, date] = SPAN, *, books: int = BAR_BOOKS,
              hourly: "Hourly | None" = None) -> list[dict[str, Any]]:
    """A shape's null books, each built the way the round that used the shape built its null and squeezed at once.

    ``rank_week`` / ``rank_fixed``: L1 on the top thirty with a random variable redrawn weekly / fixed per coin, the
    engine's cost (the ladder's own candidates). ``deep``: round 11's plain placebo. ``rotation``: round 12's shifted
    sides, every shift. ``crash``: round 10's placebo hours."""
    start, end = span
    stressed = functools.partial(simulate_held, unit_of=listing_unit)

    def held(simulate: Callable[..., Any], weigher: Callable[..., dict[str, float]], monthly: Mapping[date, Sequence[str]]) -> dict[str, Any]:
        return squeeze(simulate(market, bs.CADENCE, monthly, start=start, end=end, weigher=weigher)["daily"], span)

    if shape in ("rank_week", "rank_fixed"):
        monthly, kind = monthly_members(market, span), shape.split("_")[1]
        return [held(simulate_held, book(lab._control(kind, BAR_SEED + k), lambda _day: 1), monthly) for k in range(books)]
    if shape == "deep":
        monthly, pools = monthly_members(market, span), DeepFunding(market)
        return [held(stressed, deep_book(pools, placebo=f"bar:{k}"), monthly) for k in range(books)]
    if shape == "rotation":
        monthly, rot = monthly_members(market, span), Rotation(market, span)
        return [held(stressed, rotation_book(rot, shift), monthly) for shift in rot.shifts()]
    if shape == "crash":
        if hourly is None:
            raise ValueError("the crash shape needs the hourly opens")
        monthly, events = top_hundred(market, span), crash_events(hourly, span)
        return [squeeze(crash_daily(market, hourly, monthly, placebo_times(events, span, hourly, f"bar:{k}")), span)
                for k in range(books)]
    raise ValueError(f"unknown shape {shape!r}")


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
    parser.add_argument("command", choices=("list", "run", "confirm-counts", "confirm", "sweep", "transfer", "lead-check", "lead-legs",
                                            "listing-counts", "listing", "lower-counts", "lower",
                                            "crash-symbols", "crash-events", "crash-counts", "crash",
                                            "deep-counts", "deep", "rotation-counts", "rotation", "bar",
                                            "hot-counts", "hot", "gap-counts", "gap", "flush-counts", "flush",
                                            "basis-counts", "basis", "wide-counts", "wide", "mid-counts", "mid",
                                            "tier-gate", "tier-counts", "tier-sweep", "tier-lead", "door-size",
                                            "lower-gate", "lower-counts", "lower-map", "lower-sweep", "lower-overlap",
                                            "ha1-gate", "ha1-counts", "ha1"))
    parser.add_argument("--shape", choices=BAR_SHAPES, default=None, help="bar: the book shape whose null books are read")
    parser.add_argument("--only", default=None,
                        help="sweep, transfer: variable groups (comma list), or `null` for the random books")
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
        market = lab.hold(cache, (SPAN[0], CONFIRM[1]), attach=group in ATTACHED_GROUPS, flow=group == "trade_flow")
        if market is None:
            print("BLOCKED: contract types or the spot symbol list is missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        body = confirm_counts(market, entry) if args.command == "confirm-counts" else confirm(market, entry)
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in CONFIRM], "command": args.command, **body},
                         indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_OK
    bs.check_development_span(SPAN)
    if args.command in ("listing-counts", "listing"):
        market = lab.hold(cache, attach=False)
        if market is None:
            print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        body = listing_counts(market) if args.command == "listing-counts" else listing_run(market)
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "command": args.command, **body},
                         indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_BLOCKED if body.get("refused") or (args.command == "listing-counts" and body["stop"]) else EXIT_OK
    if args.command.startswith("crash"):
        archive = cache / "archive"
        if args.command == "crash-events":                       # the reference coin's bars alone decide the events
            body = crash_counts(None, load_hourly(archive, [HEDGE]))
        else:
            market = lab.hold(cache, attach=False)
            if market is None:
                print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
                return EXIT_BLOCKED
            names = sorted({HEDGE, *(s for members in top_hundred(market).values() for s in members)})
            if args.command == "crash-symbols":
                print(",".join(names))
                return EXIT_OK
            hourly = load_hourly(archive, names)
            body = crash_counts(market, hourly) if args.command == "crash-counts" else crash_run(market, hourly)
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "command": args.command, **body},
                         indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_BLOCKED if body.get("refused") or (args.command != "crash" and body["stop"]) else EXIT_OK
    if args.command == "bar":
        if args.shape is None:
            print("usage: bar needs --shape", file=sys.stderr)
            return EXIT_USAGE
        market = lab.hold(cache, attach=False)
        if market is None:
            print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        hourly = None
        if args.shape == "crash":
            names = sorted({HEDGE, *(s for members in top_hundred(market).values() for s in members)})
            hourly = load_hourly(cache / "archive", names)
        body = bar_curve(bar_books(market, args.shape, hourly=hourly))
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "command": args.command, "shape": args.shape, **body},
                         indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_OK
    if args.command in ("rotation-counts", "rotation"):
        market = lab.hold(cache, attach=False)
        if market is None:
            print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        body = rotation_counts(market) if args.command == "rotation-counts" else rotation_run(market)
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "command": args.command, **body},
                         indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_BLOCKED if body.get("refused") or (args.command == "rotation-counts" and body["stop"]) else EXIT_OK
    if args.command in ("mid-counts", "mid"):
        market = lab.hold(cache, attach=False)
        if market is None:
            print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        body = (mid_counts if args.command == "mid-counts" else mid_run)(market)
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "last_day_read": lab.last_day(market).isoformat(),
                          "command": args.command, **body}, indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_BLOCKED if body.get("refused") or (args.command == "mid-counts" and body["stop"]) else EXIT_OK
    if args.command in ("ha1-gate", "ha1-counts", "ha1"):
        market = lab.hold(cache, attach=False)
        if market is None:
            print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        if args.command == "ha1-gate":       # every=1 through the H-A1 path: round 40's sweep row for the variable
            body: dict[str, Any] = {"book": tier_sweep(market, {HA1_VARIABLE: lab.variables()[HA1_VARIABLE]},
                                                       tier=lower_sweep_tier(market))[HA1_VARIABLE]}
        elif args.command == "ha1-counts":
            body = ha1_counts(market)
        else:
            body = ha1_run(market)
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "last_day_read": lab.last_day(market).isoformat(),
                          "tier": LOWER_NAME, "variable": HA1_VARIABLE, "long": "high", "hold_weeks": HA1_EVERY,
                          "command": args.command, **body}, indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_BLOCKED if body.get("refused") or (args.command == "ha1-counts" and body["stop"]) else EXIT_OK
    if args.command == "lower-overlap":
        market = lab.hold(cache, attach=False, flow=True)
        if market is None:
            print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "last_day_read": lab.last_day(market).isoformat(),
                          "tier": LOWER_NAME, "command": args.command, **lower_lead_overlap(market)},
                         indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_OK
    if args.command in ("lower-gate", "lower-counts", "lower-map", "lower-sweep"):
        wanted = set((args.only or "").split(",")) - {""}
        refused = wanted & lower_refused_groups()
        names = {n: v for n, v in lower_names().items() if v[0] in wanted}
        if refused:
            print(f"refused: every variable of {sorted(refused)} is struck on this tier", file=sys.stderr)
            return EXIT_USAGE
        if args.command != "lower-gate" and not names and not (args.command == "lower-sweep" and wanted == {"null"}):
            print("refused: --only names no group with a variable read on this tier (or `null` for lower-sweep)", file=sys.stderr)
            return EXIT_USAGE
        if args.command == "lower-map":       # the map shows a group's struck variables too; no lead is taken from them
            names = {n: v for n, v in lab.variables().items() if v[0] in wanted}
        try:
            market = lab.hold(cache, attach=False) if args.command == "lower-gate" else sweep_market(cache, wanted)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return EXIT_USAGE
        if market is None:
            print("BLOCKED: contract types, the spot symbol list or the attached values are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        if args.command == "lower-gate":
            body: dict[str, Any] = {"book": lower_gate(market), "variable": LOWER_GATE_RECORD, "long": "low"}
        elif args.command == "lower-counts":
            body = lower_tier_counts(market, names)
        elif args.command == "lower-map":
            body = lower_map(market, names)
        elif wanted == {"null"}:
            body = {"null": tier_sweep_null(market, tier=lower_sweep_tier(market))}
        else:
            body = {"books": lower_sweep(market, names)}
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "last_day_read": lab.last_day(market).isoformat(),
                          "tier": LOWER_NAME, "logic": MID_NAMES[1], "left_out": lower_out(), "coverage_floor": LOWER_COVERAGE,
                          "only": sorted(wanted), "command": args.command, **body}, indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_OK
    if args.command == "door-size":
        market = lab.hold(cache, attach=False)
        if market is None:
            print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        lines = lab.chance(lab.grid(market, lab.controls())["cells"])
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "last_day_read": lab.last_day(market).isoformat(),
                          "command": args.command, "planted": "built from the forward return the cell reads: a calibration, "
                          "not a variable", **door_size(market, lines)}, indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_OK
    if args.command == "tier-lead":
        if args.only not in TIER_LEADS:
            print(f"refused: --only names a lead of the tier sweep: {sorted(TIER_LEADS)}", file=sys.stderr)
            return EXIT_USAGE
        market = lab.hold(cache, attach=False)
        if market is None:
            print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "last_day_read": lab.last_day(market).isoformat(),
                          "command": args.command, **tier_lead_check(market, args.only)},
                         indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_OK
    if args.command in ("tier-gate", "tier-counts", "tier-sweep"):
        wanted = set((args.only or "").split(",")) - {""}
        names = {n: v for n, v in tier_sweep_names().items() if v[0] in wanted}
        if args.command != "tier-gate" and not names and wanted != {"null"}:
            print("refused: --only names no group with a book on this tier (or `null`)", file=sys.stderr)
            return EXIT_USAGE
        if args.command == "tier-counts" and wanted == {"null"}:
            print("refused: the counts are per variable group", file=sys.stderr)
            return EXIT_USAGE
        try:
            market = lab.hold(cache, attach=False) if args.command == "tier-gate" else sweep_market(cache, wanted)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return EXIT_USAGE
        if market is None:
            print("BLOCKED: contract types, the spot symbol list or the attached values are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        if args.command == "tier-gate":
            body: dict[str, Any] = {"book": tier_sweep_gate(market), "variable": MID_VARIABLE, "long": "low"}
        elif args.command == "tier-counts":
            body = tier_sweep_counts(market, names)
        elif wanted == {"null"}:
            body = {"null": tier_sweep_null(market)}
        else:
            body = {"books": tier_sweep(market, names)}
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "last_day_read": lab.last_day(market).isoformat(),
                          "tier": TIER_SWEEP_TIER, "logic": MID_NAMES[1], "left_out": TIER_SWEEP_OUT,
                          "only": sorted(wanted), "command": args.command, **body}, indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_OK
    if args.command in ("wide-counts", "wide"):
        market = lab.hold(cache, WIDE_SPAN, attach=False)
        if market is not None:
            market = with_positioning(cache, market, WIDE_SPAN)
        if market is None:
            print("BLOCKED: contract types or the derived positioning values are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        body = (wide_counts if args.command == "wide-counts" else wide_run)(market)
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in WIDE_SPAN], "command": args.command, **body},
                         indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_BLOCKED if body.get("refused") or (args.command == "wide-counts" and body["stop"]) else EXIT_OK
    if args.command in ("flush-counts", "flush"):
        market = lab.hold(cache, FLUSH_SPAN, attach=False)
        if market is None:
            print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        members = {s for names in monthly_members(market, FLUSH_SPAN).values() for s in names}
        market.closing_oi = closing_oi(cache / "archive-metrics" / "metrics", members)
        crashes = crash_days_of(load_hourly(cache / "archive", [HEDGE]))
        body = (flush_counts if args.command == "flush-counts" else flush_run)(market, crash_days=crashes)
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in FLUSH_SPAN], "command": args.command, **body},
                         indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_BLOCKED if body.get("refused") or (args.command == "flush-counts" and body["stop"]) else EXIT_OK
    if args.command in ("hot-counts", "hot", "gap-counts", "gap", "basis-counts", "basis"):
        market = lab.hold(cache, attach=False)
        if market is not None and args.command.startswith(("gap", "basis")):
            market = with_spot(cache, market)
        if market is None:
            print("BLOCKED: contract types or the spot listing are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        body = {"hot-counts": hot_counts, "hot": hot_run, "gap-counts": gap_counts, "gap": gap_run,
                "basis-counts": basis_counts, "basis": basis_run}[args.command](market)
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "command": args.command, **body},
                         indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_BLOCKED if body.get("refused") or (args.command.endswith("counts") and body["stop"]) else EXIT_OK
    if args.command in ("deep-counts", "deep"):
        market = lab.hold(cache, attach=False)
        if market is None:
            print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        body = deep_counts(market) if args.command == "deep-counts" else deep_run(market)
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "command": args.command, **body},
                         indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_BLOCKED if body.get("refused") or (args.command == "deep-counts" and body["stop"]) else EXIT_OK
    if args.command in ("lower-counts", "lower"):
        market = lab.hold(cache, attach=False)
        if market is None:
            print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        body = lower_counts(market) if args.command == "lower-counts" else lower_run(market)
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "command": args.command, **body},
                         indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_BLOCKED if body.get("refused") or (args.command == "lower-counts" and body["stop"]) else EXIT_OK
    if args.command in ("lead-check", "lead-legs"):
        if args.only not in LEADS:
            print(f"refused: --only names a lead: {sorted(LEADS)}", file=sys.stderr)
            return EXIT_USAGE
        market = sweep_market(cache, {LEADS[args.only]["group"]})
        if market is None:
            print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "last_day_read": lab.last_day(market).isoformat(),
                          "command": args.command, "judgement": None,
                          **(lead_check if args.command == "lead-check" else lead_attribution)(market, args.only)},
                         indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_OK
    if args.command in ("sweep", "transfer"):
        wanted = set((args.only or "").split(",")) - {""}
        names = null_variables() if wanted == {"null"} else {n: v for n, v in lab.variables().items() if v[0] in wanted}
        if not names:
            print("refused: --only names no registered variable group (or `null`)", file=sys.stderr)
            return EXIT_USAGE
        try:
            market = sweep_market(cache, wanted)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return EXIT_USAGE
        if market is None:
            print("BLOCKED: contract types, the spot symbol list or the attached values are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        body = ({"books": sweep(market, names)} if args.command == "sweep" else
                {"splits": [[[d.isoformat() for d in span] for span in pair] for pair in TRANSFER_SPLITS],
                 "books": transfer(market, names)})
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "last_day_read": lab.last_day(market).isoformat(),
                          "logic": SWEEP_LOGIC, "accounting": SWEEP_ACCOUNTING, "only": sorted(wanted), "command": args.command,
                          **body}, indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_OK
    groups = {lab.variables()[c["variable"]][0] for c in CANDIDATES.values() if c["round"] == (args.round or latest_round())}
    market = lab.hold(cache, attach=bool(groups & ATTACHED_GROUPS), flow="trade_flow" in groups)
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
