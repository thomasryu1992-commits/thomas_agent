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
import functools
import json
import math
import os
import random
import statistics
import sys
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
                  unit_of: Callable[[str], float] | None = None) -> dict[str, Any]:
    """Daily net returns of a book whose quantities are fixed at each decision and held to the next.

    ``cross_section_basket.simulate`` adds each day's return at the decision's dollar weights, which is a book
    re-set to those weights every day at no cost. On coins that move several hundred percent in a day the two
    differ by tens of points (the first confirm read, 2026-11-13). Here a coin bought at the entry open earns its own
    move from that open, a short that doubles is twice as large the next day, the trade at a decision is the
    difference between the target and what the held position has become, and funding is paid on what the position
    is worth that day. Costs per unit traded are the engine's. The engine itself is not changed: registered screens
    read through it. ``every`` holds each decision for that many weeks (L10: four). ``unit_of`` gives a coin its own
    cost per unit traded (L12); without it every coin pays the engine's, by the same arithmetic as before."""
    unit = (xsb.cost.DEFAULT_TAKER_FEE_BPS + xsb.cost.DEFAULT_SLIPPAGE_BPS) / 10_000
    held: dict[str, tuple[float, float]] = {}            # coin -> (weight at entry, entry open)
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
                exit_cost += abs(w) * today.close / entry * (unit if unit_of is None else unit_of(symbol))
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
SWEEP_NULLS = 300                          # random-variable books: enough for a p under what the correction asks of 27


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


class DeepFunding:
    """Per decision day, read from that day and earlier: the pool (coin perps listed :data:`xsb.MIN_AGE_DAYS` or
    more, over the listing book's liquidity floor, with funding settled in the window and a beta), each with its
    trailing funding, beta and trailing return; and how many otherwise eligible coins have no funding on file."""

    def __init__(self, market: xsb.Market) -> None:
        self.market = market
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
                paid = xsb.trailing_funding(self.market, symbol, day, DEEP_WINDOW)
                beta = bs._beta(self.market, symbol, day, DEEP_BETA_DAYS)
                if paid is not None and beta is not None:
                    pool[symbol] = (paid, beta, xsb.trailing_return(self.market, symbol, day, DEEP_WINDOW))
            self._at[day] = (pool, unfunded)
        return self._at[day]

    def picks(self, day: date) -> tuple[str, ...]:
        if day not in self._picks:
            self._picks[day] = tuple(s for s, (paid, _, _) in self.at(day)[0].items() if paid <= DEEP_FUNDING)
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
            others = sorted(s for s, (paid, _, _) in pool.items() if paid > DEEP_FUNDING)
            own = pool[symbol][2] if symbol in pool else None
            if matched and own is not None:
                near = sorted((s for s in others if pool[s][2] is not None), key=lambda s: (abs(pool[s][2] - own), s))
                others = near[:DEEP_NEIGHBOURS] or others
            self._others[key] = tuple(others)
        others = self._others[key]
        return random.Random(f"{seed}:{symbol}:{start.isoformat()}").choice(others) if others else None


def deep_basket(names: Iterable[str], betas: Mapping[str, float], *, hedged: bool = True) -> dict[str, float]:
    """Equal longs, none above :data:`DEEP_NAME_CAP`, and the hedge coin against their summed beta. Under five names
    the rest of the long side is idle and earns nothing."""
    names = sorted(set(names))
    if not names:
        return {}
    weight = min(1 / len(names), DEEP_NAME_CAP)
    out = dict.fromkeys(names, weight)
    hedge = -sum(weight * betas[s] for s in names)
    if hedged and hedge:
        out[HEDGE] = hedge
    return out


def deep_book(pools: DeepFunding, placebo: str | None = None, *, matched: bool = False,
              hedged: bool = True) -> Callable[..., dict[str, float]]:
    """L15: long every pool coin whose trailing funding is deep negative. With ``placebo`` each of those coins is
    replaced by its partner (held only while the partner itself is in the pool that day)."""
    def weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                *, seed: str | None = None) -> dict[str, float]:
        pool = pools.at(day)[0]
        names = pools.picks(day)
        if placebo is not None:
            names = [p for s in names if (p := pools.partner(s, day, placebo, matched=matched)) is not None and p in pool]
        return deep_basket(names, {s: pool[s][1] for s in names}, hedged=hedged)
    return weights


def _median(values: Sequence[float | None]) -> float | None:
    kept = [v for v in values if v is not None]
    return round(statistics.median(kept), 4) if kept else None


def deep_counts(market: xsb.Market, span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """What the deep-funding book would stand on, with nothing earned after a decision in it: decisions, names,
    spells, the picks' trailing funding (the signal), and how beaten down and how volatile the picks are beside the
    rest of the pool. The stop rules are judged here."""
    pools = DeepFunding(market)
    days = [d for d in sundays(span) if span[0] - timedelta(days=1) <= d <= span[1]]
    years: dict[str, dict[str, Any]] = {str(y): {"decisions": 0, "active_decisions": 0, "names": [], "invested": [],
                                                 "spells": 0, "pool": [], "unfunded_coin_decisions": 0, "paid": []}
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


def deep_run(market: xsb.Market, span: tuple[date, date] = SPAN, *, placebos: int = DEEP_PLACEBOS) -> dict[str, Any]:
    """The deep-funding book net of stressed costs, beside the same book on placebo coins and on placebo coins as
    beaten down, through rung 2's three conditions and rung 3. A year the book is flat in counts as zero. Refused
    when the counts say stop."""
    counts = deep_counts(market, span)
    if counts["stop"]:
        return {"refused": counts["stop"], "counts": counts}
    pools = DeepFunding(market)
    stressed = functools.partial(simulate_held, unit_of=listing_unit)

    def whole(report: dict[str, Any]) -> dict[str, Any]:
        years = {str(y): report["years_total_position_pct"].get(str(y), 0.0)
                 for y in range(span[0].year, span[1].year + 1)}
        return {**report, "years_total_position_pct": years, "stability": stability(years)}

    report = whole(measure(market, deep_book(pools), span, simulate=stressed))
    bare = whole(measure(market, deep_book(pools, hedged=False), span, simulate=stressed))
    plain = [measure(market, deep_book(pools, placebo=f"plain:{k}"), span, simulate=stressed)["annual_sharpe"]
             for k in range(placebos)]
    alike = [measure(market, deep_book(pools, placebo=f"matched:{k}", matched=True), span, simulate=stressed)["annual_sharpe"]
             for k in range(placebos)]
    legs = deep_legs(report, bare)
    rungs = {"2a_entry": report["clears_entry"], "2b_later_years": later_years(report["years_total_position_pct"]),
             "2c_beside_placebos": beside_nulls(report["annual_sharpe"], plain), "3_stability": report["stability"]}
    matched = beside_nulls(report["annual_sharpe"], alike)
    ladder = bool(rungs["2a_entry"] and rungs["2b_later_years"]["passes"] and rungs["2c_beside_placebos"]["passes"]
                  and rungs["3_stability"]["passes"])
    earned = _before_costs(report) + report["funding_pct"]
    carried = earned > 0 and legs["alts"]["funding_pct"] >= DEEP_CARRY_SHARE * earned
    reading = ("closed" if not ladder else "not_apart_from_beaten_down_coins" if not matched["passes"]
               else "passes_on_carry" if carried else "passes_on_price")
    return {**report, "hypothesis": "H-F1", "logic": "L15", "counts": counts, "rungs": rungs,
            "beside_matched_placebos": matched, "legs": legs,
            "without_the_hedge": {k: bare[k] for k in ("annual_sharpe", "total_position_annual_pct", "years_total_position_pct")},
            "at_engine_cost_annual_sharpe": measure(market, deep_book(pools), span, simulate=simulate_held)["annual_sharpe"],
            "passes": ladder and matched["passes"], "reading": reading}


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
    parser.add_argument("command", choices=("list", "run", "confirm-counts", "confirm", "sweep", "transfer",
                                            "listing-counts", "listing", "lower-counts", "lower",
                                            "crash-symbols", "crash-events", "crash-counts", "crash",
                                            "deep-counts", "deep"))
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
    if args.command in ("sweep", "transfer"):
        wanted = set((args.only or "").split(",")) - {""}
        names = null_variables() if wanted == {"null"} else {n: v for n, v in lab.variables().items() if v[0] in wanted}
        if not names:
            print("refused: --only names no registered variable group (or `null`)", file=sys.stderr)
            return EXIT_USAGE
        market = lab.hold(cache, attach=bool(wanted & ATTACHED_GROUPS), flow="trade_flow" in wanted)
        if market is None:
            print("BLOCKED: contract types or the spot symbol list is missing from the cache", file=sys.stderr)
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
