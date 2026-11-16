"""Signal lab: where, if anywhere, a variable ranks next week's returns — by scenario, liquidity tier and horizon.

``docs/proposals/STRATEGY_RESEARCH_REBUILD_V0.1.md`` (Thomas 2026-11-13). The tools before this one measured one
thing: a finished basket's net Sharpe. That folds the signal, the portfolio and the costs into a single number, so a
weak signal reads as a failure and nothing says in which market state, which coins or which holding period a variable
works. This tool measures the layer underneath, before any strategy exists.

- **A cell** is one variable, one liquidity tier, one horizon and one scenario. Its reading is the rank correlation
  between the variable at a weekly decision and the return over the horizon that follows, averaged over the
  decisions in that scenario, with its t value, the number of decisions and the number of separate spells.
- **The return is the one a book would hold:** from the open after the decision to the open ``horizon`` days later,
  counted only for a coin that traded on the decision day and on both of those days. Before costs.
- **Scenarios are read at the decision, from the past only:** the market trend (the regime label of
  ``regime_strategies``), Bitcoin's volatility, the dispersion of the coins' returns and the level of funding, each
  against its own trailing median; the share of wildly moving coins in the top tier, likewise; and trend crossed
  with volatility.
- **Chance is measured, not assumed.** Random variables go through the same grid — some redrawn every week, some
  fixed per coin like a slow characteristic. A real cell is read against what those produce.
- **The span is development only** (:data:`SPAN`). The market is cut at its last day and a decision whose return
  would reach past it is dropped, so nothing here reads 2025 or later.
- **No verdict.** A cell that meets :func:`candidates`' rule is a lead to write up as a hypothesis, at most
  :data:`CANDIDATE_CAP` of them; it is not a strategy and not evidence that one exists.

``run`` is sandbox-only and prints the whole map as JSON.
"""

from __future__ import annotations

import argparse
import json
import math
import os
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
from scripts import binance_archive as ba  # noqa: E402
from scripts.research import basket_search as bs  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import depth_basket_search as ds  # noqa: E402
from scripts.research import forward_panel as fp  # noqa: E402
from scripts.research import lake  # noqa: E402
from scripts.research import metrics_basket_search as ms  # noqa: E402
from scripts.research import regime_strategies as rg  # noqa: E402
from scripts.research import spot_share_search as sp  # noqa: E402

SPEC = "STRATEGY_RESEARCH_REBUILD_V0.1"
SPAN = bs.DEV                              # 2020-01-01 .. 2024-12-31; nothing later is read
HORIZONS = (7, 28)                         # days held; 28 is read on every fourth decision so windows do not overlap
TIERS = {"top30": (0, 30), "rank31_100": (30, 100)}    # by the month's trailing volume, highest first
# Round 40 (section 57): H-R1's tier, read by its own runs only. Its members come per decision day from
# ``strategy_ladder.LowerTier``; it is never one of TIERS, so the existing tiers' cells and lines cannot move.
LOWER_TIER_NAME = "rank101_liquid"
LOOKBACK = 30
MIN_NAMES = 8                              # coins a decision needs before its cross-section is ranked
MIN_DECISIONS = 20                         # decisions a cell needs before it shows a number
MIN_SPELLS = 4                             # separate stretches a scenario cell needs: one long bull run is one sample
REFERENCE_DECISIONS = 52                   # trailing decisions a scenario's own median is taken over
REFERENCE_MIN = 26
CONTROLS_WEEKLY = 53                       # random variables redrawn at every decision (25, 28, 32, 42, 44, 47, 50 as the registry grew)
CONTROLS_FIXED = 53                        # random variables fixed per coin, like a slow characteristic
EXTREME_MOVE = 0.30                        # a daily close-to-close move this large is an extreme day
WILD_MOVE = 0.50                           # a coin whose largest daily move in the lookback reached this is wild
AGE_CAP_DAYS = 1000                        # listing age is read up to here; older coins are alike
CANDIDATE_CAP = 3
MIN_PERSISTENCE = 0.5                      # week-to-week rank correlation a lead needs: one-day reversal died on cost
# Axes at rest (STRATEGY_DISCOVERY_LOOP_V0.1 section 18): two checks in a row, none beat its placebos. Their cells
# are still mapped and shown; a cell on a resting axis is not a lead.
# "breadth" (section 34, 2026-11-14) is born at rest: a new axis has to beat its own placebo axes before any of its
# cells may be a lead.
RESTING_AXES = frozenset({"trend", "vol", "dispersion", "heat", "universe", "breadth"})
AXES = ("trend", "vol", "dispersion", "heat", "universe", "breadth")       # the order cells and checks list them in
# Axes under check (section 36): labelled and checked beside their placebos, never mapped. One that beats its
# placebos at its own level may join AXES in a later round. Leverage (section 38) has a label only where the
# positioning values are attached, so it is checked in a run that loads them.
CANDIDATE_AXES = ("dominance", "volume", "correlation", "drawdown", "month", "leverage")
AXIS_ALPHA = 0.05                          # an axis is held to this over its place in the order axes were checked in
# The map's six axes cut finer (section 39): the five that split at their own median are cut at their own thirds,
# and a trend state is cut into a spell's first decisions and the rest. Checked beside placebos, never mapped.
FINER_OF = {"trend_fine": "trend", "vol_fine": "vol", "dispersion_fine": "dispersion", "heat_fine": "heat",
            "universe_fine": "universe", "breadth_fine": "breadth"}
FINER_AXES = tuple(FINER_OF)
EARLY_DECISIONS = 4                        # a trend spell's first four weekly decisions are "early": a month
# Section 40: how far apart the top tier's coins' funding stands. Checked like the finer axes, never mapped.
LATE_AXES = ("funding_spread",)
# breadth was the first new axis; each later one meets a stricter level
AXIS_ORDER = ("breadth",) + CANDIDATE_AXES + FINER_AXES + LATE_AXES
# Section 40: a variable with a file and no record is first ranked beside its nearest registered kin, on inputs
# alone. One that orders the top tier's coins as a kin does (this rank correlation or more, either sign) is that
# kin read again, and is not mapped.
OVERLAP_MAX = 0.8
DRAWDOWN_DAYS = 365                        # the reference coin's high is looked for over these days
MONTH_START_DAYS = 7                       # a decision in the month's first week is "start"


def axis_level(axis: str) -> float | None:
    """The share of placebo axes a new axis must beat: :data:`AXIS_ALPHA` over its place in :data:`AXIS_ORDER`.
    Checking several axes at one level each wakes one by chance; the first five axes have no level (they rest)."""
    return AXIS_ALPHA / (AXIS_ORDER.index(axis) + 1) if axis in AXIS_ORDER else None
REFERENCE = bs.REFERENCE

Variable = Callable[[xsb.Market, str, date], "float | None"]


def _with_lookback(feature: bs.Feature, lookback: int = LOOKBACK) -> Variable:
    return lambda market, symbol, day: feature(market, symbol, day, lookback)


def _control(kind: str, k: int) -> Variable:
    def value(market: xsb.Market, symbol: str, day: date) -> float:
        stamp = day if kind == "week" else date(2000, 1, 1)
        return int(xsb.random_key(f"{SPEC}|{kind}|{k}", stamp, symbol)[:12], 16) / 16 ** 12
    return value


def _change(level: Variable) -> Variable:
    """A registered level at the decision day less the same level one :data:`LOOKBACK` earlier (section 3.1's first
    shape: the change against the period before). Both readings are of that day and earlier."""
    def value(market: xsb.Market, symbol: str, day: date) -> float | None:
        now, before = level(market, symbol, day), level(market, symbol, day - timedelta(days=LOOKBACK))
        return None if now is None or before is None else now - before
    return value


# The positioning levels whose 30-day change is registered (STRATEGY_DISCOVERY_LOOP_V0.1 section 33). Open interest
# is already a change; the divergence is the difference of two of these and its change adds no reading of its own.
CHANGE_OF = {"ls_change": "ls_level", "top_change": "top_level", "taker_change": "taker_level",
             # Section 35: the rest of the change shape, after the strikes written there.
             "momentum_change": "momentum", "funding_change": "funding", "spot_share_change": "spot_share",
             "taker_buy_share_change": "taker_buy_share"}


SPREAD_LOOKBACKS = (7, 14, 90)             # section 3.1's fourth shape for the two late levels (the name is the first's)
LATE_LEVELS = ("spread", "premium")        # levels registered after the shape rounds; each gets its change and position
HISTORY_POINTS = 6                         # a level's own history: its value at each of the six lookbacks before
HISTORY_MIN = 4                            # of those points must be there, or the coin has no position that day


def _position(level: Variable) -> Variable:
    """Where a registered level stands against the coin's own recent past (section 3.1's second shape): the level
    at the decision day less the mean of the same level at the :data:`HISTORY_POINTS` earlier lookbacks, over their
    spread. Unlike a number taken off every coin alike, this is centred coin by coin, so it changes the ranks.
    Every reading is of the decision day or earlier."""
    def value(market: xsb.Market, symbol: str, day: date) -> float | None:
        now = level(market, symbol, day)
        past = [v for v in (level(market, symbol, day - timedelta(days=LOOKBACK * k)) for k in range(1, HISTORY_POINTS + 1))
                if v is not None]
        if now is None or len(past) < HISTORY_MIN:
            return None
        spread = statistics.pstdev(past)
        return (now - statistics.fmean(past)) / spread if spread > 0 else None
    return value


# The levels whose own-history position is registered (section 37, after the strikes written there).
POSITION_OF = {"momentum_position": "momentum", "funding_position": "funding", "ls_position": "ls_level",
               "top_position": "top_level", "taker_position": "taker_level", "spot_share_position": "spot_share",
               "taker_buy_share_position": "taker_buy_share", "trade_size_position": "trade_size"}


def _residual_momentum(market: xsb.Market, symbol: str, day: date) -> float | None:
    """The coin's month less its beta times the reference coin's month (section 3.1's third shape). Taking the
    market's move off every coin alike would change no rank; the beta is the coin's own, so this does."""
    own, market_move = xsb.trailing_return(market, symbol, day, LOOKBACK), xsb.trailing_return(market, REFERENCE, day, LOOKBACK)
    beta = bs._beta(market, symbol, day, LOOKBACK)
    return None if own is None or market_move is None or beta is None else own - beta * market_move


def _daily_moves(market: xsb.Market, symbol: str, day: date, lookback: int) -> list[float] | None:
    """The absolute close-to-close moves of the ``lookback`` days ending at ``day``; None under the usual share."""
    bars = market.bars.get(symbol) or {}
    moves = []
    for k in range(lookback):
        now, before = bars.get(day - timedelta(days=k)), bars.get(day - timedelta(days=k + 1))
        if now is not None and before is not None and before.close > 0:
            moves.append(abs(now.close / before.close - 1))
    return moves if len(moves) >= math.ceil(lookback * bs.MIN_SHARE) else None


def _listing_age(market: xsb.Market, symbol: str, day: date) -> float | None:
    first = market.first_day(symbol)
    return None if first is None or first > day else float(min((day - first).days, AGE_CAP_DAYS))


def _extreme_days(market: xsb.Market, symbol: str, day: date) -> float | None:
    moves = _daily_moves(market, symbol, day, LOOKBACK)
    return None if moves is None else float(sum(1 for m in moves if m >= EXTREME_MOVE))


# --- the kline columns no other loader keeps (the first inventory, 2026-11-13) ----------------------------------

def read_trade_flow(folder: Path) -> dict[date, tuple[float, float]]:
    """One symbol's ``{day: (taker-buy share of quote volume, quote volume per trade)}`` from its monthly zips: the
    kline file's trade count and taker-buy quote volume, which ``cross_section_basket.read_klines`` drops. A day
    without volume or trades is left out."""
    out: dict[date, tuple[float, float]] = {}
    for path in sorted(folder.glob("*.zip")) if folder.exists() else []:
        for row in xsb._rows(path):
            try:
                day, quote, count, taker = xsb._utc_day(float(row[0])), float(row[7]), float(row[8]), float(row[10])
            except (ValueError, IndexError):
                continue          # the header row
            if quote > 0 and count > 0 and 0 <= taker <= quote:
                out[day] = (taker / quote, quote / count)
    return out


def attach_trade_flow(market: xsb.Market, archive: Path, span: tuple[date, date]) -> xsb.Market:
    """Hang the trade-flow columns on a market already cut at the span's end, for the coins the tiers ever hold —
    the files cover every listed symbol, and only those are ranked."""
    wanted = {s for monthly in tier_members(market, span).values() for members in monthly.values() for s in members}
    market.trade_flow = {s: {d: v for d, v in read_trade_flow(archive / xsb.KLINES_KIND / s).items() if d <= span[1]}
                         for s in sorted(wanted)}
    return market


def _flow_window(market: xsb.Market, symbol: str, day: date, first: int, last: int) -> list[tuple[float, float]] | None:
    """The trade-flow values of the days ``day-first .. day-last+1`` back; None under the usual share of them."""
    days = getattr(market, "trade_flow", {}).get(symbol) or {}
    values = [days[d] for d in (day - timedelta(days=k) for k in range(first, last)) if d in days]
    return values if len(values) >= math.ceil((last - first) * bs.MIN_SHARE) else None


def _taker_buy_share(market: xsb.Market, symbol: str, day: date) -> float | None:
    values = _flow_window(market, symbol, day, 0, LOOKBACK)
    return None if values is None else statistics.fmean(v[0] for v in values)


def _trade_size(market: xsb.Market, symbol: str, day: date) -> float | None:
    values = _flow_window(market, symbol, day, 0, LOOKBACK)
    return None if values is None else statistics.fmean(v[1] for v in values)


def _trade_size_growth(market: xsb.Market, symbol: str, day: date) -> float | None:
    recent, before = _flow_window(market, symbol, day, 0, LOOKBACK), _flow_window(market, symbol, day, LOOKBACK, 2 * LOOKBACK)
    if recent is None or before is None:
        return None
    return statistics.fmean(v[1] for v in recent) / statistics.fmean(v[1] for v in before) - 1


def variables() -> dict[str, tuple[str, Variable]]:
    """``{name: (group, function)}`` — every variable the data on disk supports, each at a stated lookback."""
    out: dict[str, tuple[str, Variable]] = {}
    for name, feature in bs.FEATURES.items():
        out[f"{name}_{LOOKBACK}"] = ("price_volume", _with_lookback(feature))
    for lookback in (7, 30, 90):
        out[f"momentum_{lookback}"] = ("price_volume", _with_lookback(xsb.trailing_return, lookback))
    for lookback in (7, 30):
        out[f"funding_{lookback}"] = ("funding", _with_lookback(xsb.trailing_funding, lookback))
    for name, feature in ms.FEATURES.items():
        out[f"{name}_{LOOKBACK}"] = ("positioning", _with_lookback(feature))
    for name, feature in ds.FEATURES.items():
        out[f"{name}_{LOOKBACK}"] = ("depth", _with_lookback(feature))
    for name, feature in sp.FEATURES.items():
        out[f"{name}_{LOOKBACK}"] = ("spot", _with_lookback(feature))
    # Added 2026-11-13 (STRATEGY_DISCOVERY_LOOP_V0.1 section 13): what kind of coin it is. The 2025-2026 top thirty
    # holds coins that move several hundred percent in a day; the development years' mostly did not.
    out["listing_age"] = ("coin_character", _listing_age)
    out[f"extreme_days_{LOOKBACK}"] = ("coin_character", _extreme_days)
    # Added 2026-11-13 (section 16): the kline columns the first inventory found unread. They exist for every coin,
    # where the positioning files reach only the top ones.
    out[f"taker_buy_share_{LOOKBACK}"] = ("trade_flow", _taker_buy_share)
    out[f"trade_size_{LOOKBACK}"] = ("trade_flow", _trade_size)
    out[f"trade_size_growth_{LOOKBACK}"] = ("trade_flow", _trade_size_growth)
    # Added 2026-11-14 (section 33): "the change of positioning", which the first inventory listed as there and unread.
    for name, base in CHANGE_OF.items():
        group, level = out[f"{base}_{LOOKBACK}"]
        out[f"{name}_{LOOKBACK}"] = (group, _change(level))
    # Added 2026-11-14 (section 37): the second and third shapes. The fourth (other lookbacks) left no survivor.
    for name, base in POSITION_OF.items():
        group, level = out[f"{base}_{LOOKBACK}"]
        out[f"{name}_{LOOKBACK}"] = (group, _position(level))
    out[f"residual_momentum_{LOOKBACK}"] = ("price_volume", _residual_momentum)
    # Added 2026-11-14 (section 40): two readings with a file and no record. Each was first ranked beside its kin on
    # inputs alone (0.13 and 0.72 on the top tier, under the 0.8 that would have made it that kin read again).
    out[f"spread_{LOOKBACK}"] = ("price_volume", _spread)
    out[f"premium_{LOOKBACK}"] = ("premium", _premium)
    # Added 2026-11-14 (section 41): the two new levels' shapes. No strike written in sections 33 or 37 covers them.
    for base in LATE_LEVELS:
        group, level = out[f"{base}_{LOOKBACK}"]
        out[f"{base}_change_{LOOKBACK}"] = (group, _change(level))
        out[f"{base}_position_{LOOKBACK}"] = (group, _position(level))
    # Added 2026-11-14 (section 42): the spread at the other lookbacks. No closed reading strikes them (section 37.2's
    # rule); the premium's are struck there. They are levels at another window and get no shape.
    for lookback in SPREAD_LOOKBACKS:
        out[f"spread_{lookback}"] = ("price_volume", _with_lookback(_spread, lookback))
    # Added 2026-11-14 (section 43): the premium's too. Section 40 struck them on grounds that did not stand.
    for lookback in SPREAD_LOOKBACKS:
        out[f"premium_{lookback}"] = ("premium", _with_lookback(_premium, lookback))
    return out


def controls() -> dict[str, tuple[str, Variable]]:
    out = {f"control_week_{k:02d}": ("control", _control("week", k)) for k in range(CONTROLS_WEEKLY)}
    out.update({f"control_fixed_{k:02d}": ("control", _control("fixed", k)) for k in range(CONTROLS_FIXED)})
    return out


# --- the market, cut ---------------------------------------------------------------------------------------------

def cut_market(market: xsb.Market, span: tuple[date, date] = SPAN) -> xsb.Market:
    """``market`` with no bar, settlement or attached series after the span's last day."""
    held = bs.cut(market, span[1])
    for name in ("metrics", "depth", "spot", "trade_flow"):
        series = getattr(market, name, None)
        if series is not None:
            setattr(held, name, {s: {d: v for d, v in days.items() if d <= span[1]} for s, days in series.items()})
    for name in ("spot_of", "spot_files"):
        if hasattr(market, name):
            setattr(held, name, getattr(market, name))
    return held


def last_day(market: xsb.Market) -> date | None:
    days = [max(bars) for bars in market.bars.values() if bars]
    for name in ("metrics", "depth", "spot", "trade_flow"):
        days += [max(v) for v in (getattr(market, name, None) or {}).values() if v]
    return max(days) if days else None


# --- decisions, returns, scenarios -------------------------------------------------------------------------------

def decisions(span: tuple[date, date], horizon: int) -> list[date]:
    """The weekly decision days whose whole forward window lies inside the span; every fourth for a 28-day horizon."""
    days = [d for d in xsb.held_days(span[0], span[1])
            if xsb.is_rebalance_day(bs.CADENCE, d) and d + timedelta(days=1 + horizon) <= span[1]]
    return days[::max(1, horizon // 7)]


def forward_return(market: xsb.Market, symbol: str, day: date, horizon: int) -> float | None:
    """From the open after ``day`` to the open ``horizon`` days later; None unless the coin traded on ``day`` and on
    both of those days (a frozen bar that outlives trading is not a return)."""
    bars = market.bars.get(symbol) or {}
    now, start, end = bars.get(day), bars.get(day + timedelta(days=1)), bars.get(day + timedelta(days=1 + horizon))
    if any(b is None or not b.quote_volume or b.quote_volume <= 0 for b in (now, start, end)):
        return None
    return end.open / start.open - 1 if start.open > 0 else None


def _median_split(value: float | None, history: Sequence[float], high: str, low: str) -> str | None:
    if value is None or len(history) < REFERENCE_MIN:
        return None
    return high if value > statistics.median(history) else low


def _spread(market: xsb.Market, symbol: str, day: date, lookback: int = LOOKBACK) -> float | None:
    """A spread estimated from daily highs, lows and closes over the lookback: with ``c`` the log close and ``m`` the
    middle of the log high and log low, the root of the mean of ``4 (c_t - m_t)(c_t - m_{t+1})`` over the day pairs
    that end by ``day``, and zero where that mean is negative. A close far from both days' middles is a wide spread."""
    bars = market.bars.get(symbol) or {}
    terms = []
    for k in range(lookback):
        now, after = bars.get(day - timedelta(days=k + 1)), bars.get(day - timedelta(days=k))
        if any(b is None or b.high is None or b.low is None or min(b.high, b.low, b.close) <= 0 for b in (now, after)):
            continue
        middle = lambda b: (math.log(b.high) + math.log(b.low)) / 2          # noqa: E731
        terms.append(4 * (math.log(now.close) - middle(now)) * (math.log(now.close) - middle(after)))
    if len(terms) < math.ceil(lookback * bs.MIN_SHARE):
        return None
    return math.sqrt(max(0.0, statistics.fmean(terms)))


def _premium(market: xsb.Market, symbol: str, day: date, lookback: int = LOOKBACK) -> float | None:
    """The mean of the premium index's daily close over the lookback: how far the perp has stood from its index."""
    series = (getattr(market, "premium", None) or {}).get(symbol) or {}
    values = [series[d] for d in (day - timedelta(days=k) for k in range(lookback)) if d in series]
    return statistics.fmean(values) if len(values) >= math.ceil(lookback * bs.MIN_SHARE) else None


# name: (the group whose data it reads, the reading, the registered kin it is ranked beside)
OVERLAP_CANDIDATES: dict[str, tuple[str, Variable, tuple[str, ...]]] = {
    "spread_30": ("price_volume", _spread, ("range_30", "illiquidity_30", "vol_30")),
    "premium_30": ("premium", _premium, ("funding_7", "funding_30")),
}


def overlap(market: xsb.Market, name: str, span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """How alike a candidate and each of its kin order a tier's coins: at every weekly decision the rank correlation
    of the two over the coins with both, then the median over decisions. Reads no return. The rule reads the top
    tier: at :data:`OVERLAP_MAX` or more with any kin, either sign, the candidate is not mapped."""
    _, read, kin = OVERLAP_CANDIDATES[name]
    registered = variables()
    tiers = tier_members(market, span)
    out: dict[str, Any] = {}
    for tier, monthly in tiers.items():
        rows: dict[str, list[float]] = {k: [] for k in kin}
        ranked = values = zeros = 0
        for day in decisions(span, HORIZONS[0]):
            own = {s: v for s in monthly.get(date(day.year, day.month, 1), ()) if (v := read(market, s, day)) is not None}
            ranked += len(own) >= MIN_NAMES
            values, zeros = values + len(own), zeros + sum(1 for v in own.values() if v == 0)
            for k in kin:
                pairs = [(own[s], v) for s in own if (v := registered[k][1](market, s, day)) is not None]
                if len(pairs) >= MIN_NAMES and (rho := rank_correlation(pairs)) is not None:
                    rows[k].append(rho)
        out[tier] = {"decisions_ranked": ranked, "share_at_zero": round(zeros / values, 4) if values else None,
                     "kin": {k: {"decisions": len(v), "median_rank_correlation": round(statistics.median(v), 4) if v else None}
                             for k, v in rows.items()}}
    top = {k: abs(v["median_rank_correlation"]) for k, v in out["top30"]["kin"].items()
           if v["median_rank_correlation"] is not None and v["decisions"] >= MIN_DECISIONS}
    nearest = max(top, key=top.get) if top else None
    return {"candidate": name, "tiers": out, "threshold": OVERLAP_MAX, "nearest_kin": nearest,
            "compared": bool(top), "mapped": bool(top) and top[nearest] < OVERLAP_MAX}


def _third_split(value: float | None, history: Sequence[float], high: str, low: str) -> str | None:
    """Where the value stands among its own earlier values: two thirds or more of them under it is ``high``, fewer
    than a third is ``low``, and between is ``mid``. A ``high`` here is a ``high`` of :func:`_median_split` too."""
    if value is None or len(history) < REFERENCE_MIN:
        return None
    under = sum(1 for h in history if h < value)
    return high if 3 * under >= 2 * len(history) else low if 3 * under < len(history) else "mid"


def scenario_labels(market: xsb.Market, monthly: Mapping[date, Sequence[str]], days: Sequence[date],
                    span: tuple[date, date] = SPAN) -> dict[date, dict[str, str]]:
    """``{decision: {axis: label}}``, each label read from that day and earlier. ``monthly`` is the top tier."""
    trend = rg.regime_labels(rg.Data(market, monthly), end=span[1])["labels"]
    btc = market.bars.get(REFERENCE) or {}

    def btc_vol(day: date) -> float | None:
        closes = [btc.get(day - timedelta(days=k)) for k in range(LOOKBACK + 1)]
        if any(b is None for b in closes):
            return None
        returns = [closes[k].close / closes[k + 1].close - 1 for k in range(LOOKBACK)]
        return statistics.pstdev(returns)

    def members(day: date) -> Sequence[str]:
        return monthly.get(date(day.year, day.month, 1), ())

    def dispersion(day: date) -> float | None:
        values = [v for v in (xsb.trailing_return(market, s, day, 7) for s in members(day)) if v is not None]
        return statistics.pstdev(values) if len(values) >= MIN_NAMES else None

    def heat(day: date) -> float | None:
        values = [v for v in (xsb.trailing_funding(market, s, day, 7) for s in members(day)) if v is not None]
        return statistics.median(values) if len(values) >= MIN_NAMES else None

    def wild_share(day: date) -> float | None:
        moves = [m for m in (_daily_moves(market, s, day, LOOKBACK) for s in members(day)) if m is not None]
        return sum(1 for m in moves if max(m) >= WILD_MOVE) / len(moves) if len(moves) >= MIN_NAMES else None

    def funding_spread(day: date) -> float | None:
        """How far apart the top tier's coins' week of funding stands: the spread of what :func:`heat` takes the middle of."""
        values = [v for v in (xsb.trailing_funding(market, s, day, 7) for s in members(day)) if v is not None]
        return statistics.pstdev(values) if len(values) >= MIN_NAMES else None

    def breadth(day: date) -> float | None:
        """The share of the top tier's coins that are up over the last seven days."""
        values = [v for v in (xsb.trailing_return(market, s, day, 7) for s in members(day)) if v is not None]
        return sum(1 for v in values if v > 0) / len(values) if len(values) >= MIN_NAMES else None

    def dominance(day: date) -> float | None:
        """The reference coin's month against the median of the top tier's other coins: who led."""
        own = xsb.trailing_return(market, REFERENCE, day, LOOKBACK)
        others = [v for v in (xsb.trailing_return(market, s, day, LOOKBACK) for s in members(day) if s != REFERENCE)
                  if v is not None]
        return None if own is None or len(others) < MIN_NAMES else own - statistics.median(others)

    def volume(day: date) -> float | None:
        """The top tier's quote volume over the last seven days, summed over the coins with all seven."""
        totals = []
        for symbol in members(day):
            bars = market.bars.get(symbol) or {}
            week = [bars[d].quote_volume for d in (day - timedelta(days=k) for k in range(7)) if d in bars]
            if len(week) == 7:
                totals.append(sum(week))
        return sum(totals) if len(totals) >= MIN_NAMES else None

    def correlation(day: date) -> float | None:
        """The mean correlation between pairs of the top tier's coins, on a month of daily moves."""
        series = []
        for symbol in members(day):
            bars = market.bars.get(symbol) or {}
            closes = [bars.get(day - timedelta(days=k)) for k in range(LOOKBACK + 1)]
            if all(b is not None and b.close > 0 for b in closes):
                moves = [closes[k].close / closes[k + 1].close - 1 for k in range(LOOKBACK)]
                if len(set(moves)) > 1:
                    series.append(moves)
        if len(series) < MIN_NAMES:
            return None
        return statistics.fmean(statistics.correlation(a, b) for i, a in enumerate(series) for b in series[i + 1:])

    def leverage(day: date) -> float | None:
        """The top tier's open interest against its trading: per coin, the week's open interest in quote terms (the
        positioning files give contracts, so each day's is multiplied by that day's close) over the week's quote
        volume; then the median over the coins with all seven days of both. None without positioning values."""
        metrics = getattr(market, "metrics", None) or {}
        ratios = []
        for symbol in members(day):
            bars, values = market.bars.get(symbol) or {}, metrics.get(symbol) or {}
            held = traded = 0.0
            for d in (day - timedelta(days=k) for k in range(7)):
                bar, oi = bars.get(d), (values.get(d) or {}).get("oi")
                if bar is None or oi is None or bar.quote_volume <= 0:
                    break
                held, traded = held + oi * bar.close, traded + bar.quote_volume
            else:
                ratios.append(held / traded)
        return statistics.median(ratios) if len(ratios) >= MIN_NAMES else None

    def drawdown(day: date) -> float | None:
        """The reference coin's close against its highest close of the last :data:`DRAWDOWN_DAYS` days (0 at a high)."""
        closes = [b.close for d in (day - timedelta(days=k) for k in range(DRAWDOWN_DAYS)) if (b := btc.get(d)) is not None]
        return None if btc.get(day) is None or len(closes) < DRAWDOWN_DAYS // 2 else btc[day].close / max(closes) - 1

    # Each axis is compared with its own values at the earlier weekly decisions, never with a later one.
    weekly = [d for d in xsb.held_days(span[0], span[1]) if xsb.is_rebalance_day(bs.CADENCE, d)]
    axes = {"vol": (btc_vol, "high", "low"), "dispersion": (dispersion, "wide", "narrow"), "heat": (heat, "hot", "cool"),
            "universe": (wild_share, "wild", "calm"), "breadth": (breadth, "broad", "thin"),
            "dominance": (dominance, "btc_led", "alt_led"), "volume": (volume, "busy", "quiet"),
            "correlation": (correlation, "tight", "loose"), "drawdown": (drawdown, "shallow", "deep"),
            "leverage": (leverage, "levered", "light"), "funding_spread": (funding_spread, "apart", "together")}
    series = {axis: {d: read(d) for d in weekly} for axis, (read, _, _) in axes.items()}
    # How many weekly decisions in a row, this one included, have carried the same trend label.
    age: dict[date, int] = {}
    previous, run = None, 0
    for d in weekly:
        state = trend.get(d)
        run = 0 if state is None else run + 1 if state == previous else 1
        previous, age[d] = state, run
    out: dict[date, dict[str, str]] = {}
    for day in days:
        labels: dict[str, str] = {}
        if trend.get(day) is not None:
            labels["trend"] = str(trend[day]).lower()
            if age.get(day):
                labels["trend_fine"] = f"{labels['trend']}_{'early' if age[day] <= EARLY_DECISIONS else 'late'}"
        for axis, (_, high, low) in axes.items():
            earlier = [series[axis][d] for d in weekly if d < day and series[axis][d] is not None][-REFERENCE_DECISIONS:]
            label = _median_split(series[axis].get(day), earlier, high, low)
            if label is not None:
                labels[axis] = label
            if f"{axis}_fine" in FINER_OF and (fine := _third_split(series[axis].get(day), earlier, high, low)):
                labels[f"{axis}_fine"] = fine
        labels["month"] = "start" if day.day <= MONTH_START_DAYS else "rest"      # the calendar: no history needed
        out[day] = labels
    return out


def scenario_keys(labels: Mapping[str, str]) -> list[str]:
    keys = ["all"] + [f"{axis}={labels[axis]}" for axis in AXES if axis in labels]
    if "trend" in labels and "vol" in labels:
        keys.append(f"trend={labels['trend']}|vol={labels['vol']}")
    return keys


# --- one cross-section -------------------------------------------------------------------------------------------

def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    out, i = [0.0] * len(values), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def rank_correlation(pairs: Sequence[tuple[float, float]]) -> float | None:
    """Spearman's rank correlation, average ranks for ties; None with no spread on a side."""
    a, b = _ranks([p[0] for p in pairs]), _ranks([p[1] for p in pairs])
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def tercile_spread(pairs: Sequence[tuple[float, float]]) -> float:
    """Mean forward return of the top third by the variable minus the bottom third's."""
    ordered = sorted(pairs, key=lambda p: p[0])
    k = max(1, len(ordered) // 3)
    return statistics.fmean(r for _, r in ordered[-k:]) - statistics.fmean(r for _, r in ordered[:k])


def spells(indices: Sequence[int]) -> int:
    """How many separate stretches of consecutive decisions ``indices`` (positions in the decision list) make."""
    ordered = sorted(indices)
    return sum(1 for i, value in enumerate(ordered) if i == 0 or value != ordered[i - 1] + 1)


def cell(samples: Sequence[tuple[int, float, float]], *, scenario: str) -> dict[str, Any]:
    """One cell from its decisions' ``(position, rank correlation, tercile spread)``."""
    n, runs = len(samples), spells([s[0] for s in samples])
    if n < MIN_DECISIONS or (scenario != "all" and runs < MIN_SPELLS):
        return {"decisions": n, "spells": runs, "too_few": True}
    ics = [s[1] for s in samples]
    spreads = [s[2] for s in samples]
    spread = statistics.fmean(spreads)
    sd, spread_sd = statistics.stdev(ics), statistics.stdev(spreads)
    # ``spread_t`` (round 38): the rank correlation says which way the ranks lean, the spread's own t says whether
    # the mean a rank book earns stands apart from zero. On the second tier the two often point opposite ways.
    return {"decisions": n, "spells": runs, "too_few": False,
            "mean_rank_correlation": round(statistics.fmean(ics), 4),
            "t": round(statistics.fmean(ics) / (sd / math.sqrt(n)), 2) if sd > 0 else None,
            "positive_share": round(sum(1 for v in ics if v > 0) / n, 3),
            "tercile_spread_bps": round(spread * 10_000, 1),
            "spread_t": round(spread / (spread_sd / math.sqrt(n)), 2) if spread_sd > 0 else None}


# --- the grid ----------------------------------------------------------------------------------------------------

def tier_members(market: xsb.Market, span: tuple[date, date]) -> dict[str, dict[date, list[str]]]:
    widest = max(hi for _, hi in TIERS.values())
    monthly = xsb.universes(market, top_n=widest, start=date(span[0].year, span[0].month, 1) - timedelta(days=1),
                            end=span[1])
    return {tier: {date(m.year, m.month, 1): members[lo:hi] for m, members in monthly.items()}
            for tier, (lo, hi) in TIERS.items()}


def grid(market: xsb.Market, names: Mapping[str, tuple[str, Variable]], span: tuple[date, date] = SPAN,
         *, members: Mapping[str, Callable[[date], Sequence[str]]] | None = None) -> dict[str, Any]:
    """Every cell of ``names`` over the span, with coverage, week-to-week rank persistence and what was dropped.
    ``market`` must already be cut at the span's last day. ``members`` replaces :data:`TIERS` with tiers whose
    members are given per decision day (round 40); the scenario labels stay the top thirty's either way."""
    latest = last_day(market)
    if latest is not None and latest > span[1]:
        raise ValueError(f"the market reaches {latest}, past the span's last day {span[1]}; cut it first")
    tiers = tier_members(market, span)
    by_day: dict[str, Callable[[date], Sequence[str]]] = (
        dict(members) if members is not None else
        {tier: (lambda day, monthly=monthly: monthly.get(date(day.year, day.month, 1), ())) for tier, monthly in tiers.items()})
    samples: dict[tuple[str, str, int, str], list[tuple[int, float, float]]] = {}
    coverage: dict[tuple[str, str], list[int]] = {}
    persistence: dict[tuple[str, str], list[float]] = {}
    dropped = {"untraded_returns": 0, "returns_asked": 0}
    for horizon in HORIZONS:
        days = decisions(span, horizon)
        labels = scenario_labels(market, tiers["top30"], days, span)
        for tier, members_at in by_day.items():
            previous: dict[str, dict[str, float]] = {}
            for position, day in enumerate(days):
                returns = {}
                for symbol in members_at(day):
                    dropped["returns_asked"] += 1
                    value = forward_return(market, symbol, day, horizon)
                    if value is None:
                        dropped["untraded_returns"] += 1
                    else:
                        returns[symbol] = value
                keys = scenario_keys(labels[day])
                for name, (_, read) in names.items():
                    values = {s: v for s in returns if (v := read(market, s, day)) is not None}
                    if horizon == HORIZONS[0]:
                        coverage.setdefault((name, tier), []).append(len(values))
                        before = previous.get(name)
                        if before:
                            shared = [(before[s], values[s]) for s in values if s in before]
                            if len(shared) >= MIN_NAMES and (rho := rank_correlation(shared)) is not None:
                                persistence.setdefault((name, tier), []).append(rho)
                        previous[name] = values
                    if len(values) < MIN_NAMES:
                        continue
                    pairs = [(values[s], returns[s]) for s in values]
                    ic = rank_correlation(pairs)
                    if ic is None:
                        continue
                    sample = (position, ic, tercile_spread(pairs))
                    for key in keys:
                        samples.setdefault((name, tier, horizon, key), []).append(sample)
    cells = [{"variable": name, "group": names[name][0], "tier": tier, "horizon_days": horizon, "scenario": key,
              **cell(rows, scenario=key)}
             for (name, tier, horizon, key), rows in sorted(samples.items())]
    summary = {f"{name}|{tier}": {"mean_names_ranked": round(statistics.fmean(counts), 1),
                                  "share_of_decisions_ranked": round(sum(1 for c in counts if c >= MIN_NAMES) / len(counts), 3),
                                  "rank_persistence": (round(statistics.fmean(persistence[(name, tier)]), 3)
                                                       if persistence.get((name, tier)) else None)}
               for (name, tier), counts in sorted(coverage.items())}
    return {"cells": cells, "variables": summary, "dropped": dropped}


# --- is a scenario axis worth having? (the monthly check) --------------------------------------------------------

PLACEBO_AXES = 200                         # shuffled-spell copies each axis is compared with
FINER_PLACEBOS = 400                       # the axes that stand 8th and later: 0.05 / 11 already lies under one in 201
# One variable at a time there are a few hundred tests, and the smallest p a test can show is one over the number
# of placebos plus one. With 200 that floor (0.005) sits above anything Benjamini-Hochberg could pass at q 0.10
# over 230 tests (0.0004) — the first run could not have passed anything. 5,000 puts the floor at 0.0002.
PLACEBO_AXES_BY_VARIABLE = 7200            # 5000 until 41 variables, 6000 until 50: the earlier ones are the same placebos
PLACEBO_AXES_BY_VARIABLE_ROUND_4 = 5000    # what round 4's reading used; the counts at that point are kept beside the full ones


def ic_series(market: xsb.Market, names: Mapping[str, tuple[str, Variable]], span: tuple[date, date] = SPAN
              ) -> tuple[list[date], dict[tuple[str, str], dict[date, float]]]:
    """The weekly decisions and, per variable and tier, its rank correlation with the following week at each."""
    tiers = tier_members(market, span)
    days = decisions(span, HORIZONS[0])
    out: dict[tuple[str, str], dict[date, float]] = {}
    for tier, monthly in tiers.items():
        for day in days:
            returns = {s: r for s in monthly.get(date(day.year, day.month, 1), ())
                       if (r := forward_return(market, s, day, HORIZONS[0])) is not None}
            for name, (_, read) in names.items():
                pairs = [(v, returns[s]) for s in returns if (v := read(market, s, day)) is not None]
                if len(pairs) >= MIN_NAMES and (ic := rank_correlation(pairs)) is not None:
                    out.setdefault((name, tier), {})[day] = ic
    return days, out


def separation(series: Mapping[tuple[str, str], Mapping[date, float]], labels: Mapping[date, str | None]) -> float | None:
    """How far apart an axis's states put the variables: per variable and tier, the share of the weekly values'
    variance that lies between the states' means; then the mean over those with :data:`MIN_DECISIONS` in two states
    or more."""
    shares = []
    for values in series.values():
        groups: dict[str, list[float]] = {}
        for day, ic in values.items():
            if labels.get(day) is not None:
                groups.setdefault(labels[day], []).append(ic)
        groups = {k: v for k, v in groups.items() if len(v) >= MIN_DECISIONS}
        if len(groups) < 2:
            continue
        everything = [ic for v in groups.values() for ic in v]
        mean, total = statistics.fmean(everything), statistics.pvariance(everything) * len(everything)
        if total > 0:
            shares.append(sum(len(v) * (statistics.fmean(v) - mean) ** 2 for v in groups.values()) / total)
    return statistics.fmean(shares) if shares else None


def shuffled_spells(labels: Sequence[str | None], seed: str) -> list[str | None]:
    """The same spells in another order: a placebo axis with the real one's persistence and shares.

    A decision with no label keeps none. The unlabelled decisions are the first months, before an axis has a
    history to compare with; a placebo that moved that block would label weeks the real axis cannot, and would be
    credited with the difference between those months and the rest (the first axis check, 2026-11-13)."""
    import random
    runs: list[list[str]] = []
    for label in labels:
        if label is None:
            continue
        if runs and runs[-1][0] == label:
            runs[-1].append(label)
        else:
            runs.append([label])
    random.Random(seed).shuffle(runs)
    moved = iter([label for run in runs for label in run])
    return [None if label is None else next(moved) for label in labels]


def finer_gate(axis: str, labels: Sequence[str | None]) -> tuple[list[str | None], dict[str, Any]]:
    """A state is cut finer only where every part has :data:`MIN_DECISIONS` decisions in :data:`MIN_SPELLS` spells
    (section 3.2: "모자라면 나누지 않는다"). A trend state with a thin part goes back to the whole state; one of the
    other axes, cut in three, is cut whole or not at all. Reads labels only."""
    whole = (lambda label: label.rsplit("_", 1)[0]) if axis == "trend_fine" else (lambda label: FINER_OF[axis])
    places: dict[str, list[int]] = {}
    for i, label in enumerate(labels):
        if label is not None:
            places.setdefault(label, []).append(i)
    parts = {label: {"decisions": len(at), "spells": spells(at)} for label, at in sorted(places.items())}
    thin = {whole(label) for label, n in parts.items() if n["decisions"] < MIN_DECISIONS or n["spells"] < MIN_SPELLS}
    kept = [None if label is None else whole(label) if whole(label) in thin else label for label in labels]
    return kept, {"parts": parts, "merged": sorted(thin), "split": sorted({whole(label) for label in places} - thin)}


def axis_check(market: xsb.Market, names: Mapping[str, tuple[str, Variable]], span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """Each axis's separation beside what :data:`PLACEBO_AXES` shuffled copies of it reach. An axis no better than
    its placebos is a candidate to rest; this decides nothing by itself. A finer axis is first passed through
    :func:`finer_gate`, and stands beside :data:`FINER_PLACEBOS` copies of what the gate left."""
    days, series = ic_series(market, names, span)
    labels = scenario_labels(market, tier_members(market, span)["top30"], days, span)
    out = {}
    for axis in AXES + CANDIDATE_AXES + FINER_AXES + LATE_AXES:
        real, gate = [labels[d].get(axis) for d in days], None
        if axis in FINER_OF:
            real, gate = finer_gate(axis, real)
        checked = gate is None or bool(gate["split"])          # nothing cut finer: nothing to check
        actual = separation(series, dict(zip(days, real))) if checked else None
        fake = [separation(series, dict(zip(days, shuffled_spells(real, f"{SPEC}|axis|{axis}|{k}"))))
                for k in range((PLACEBO_AXES if axis in AXES + CANDIDATE_AXES else FINER_PLACEBOS) if checked else 0)]
        fake = sorted(v for v in fake if v is not None)
        out[axis] = {**(gate or {}),
                     "states": dict(sorted({str(k): real.count(k) for k in set(real)}.items())),
                     "separation": round(actual, 5) if actual is not None else None,
                     "placebo_mean": round(statistics.fmean(fake), 5) if fake else None,
                     "placebo_p95": round(fake[int(0.95 * (len(fake) - 1))], 5) if fake else None,
                     "placebos_at_or_above": sum(1 for v in fake if actual is not None and v >= actual),
                     "placebos": len(fake)}
        level = axis_level(axis)
        if level is not None:
            share = (1 + out[axis]["placebos_at_or_above"]) / (1 + len(fake)) if fake and actual is not None else None
            out[axis].update(level=round(level, 5), p=None if share is None else round(share, 5),
                             beats_its_placebos=share is not None and share < level)
    return {"axes": out, "variables": len({name for name, _ in series}), "decisions": len(days)}


def axis_by_variable(market: xsb.Market, names: Mapping[str, tuple[str, Variable]], span: tuple[date, date] = SPAN,
                     *, q: float = 0.10) -> dict[str, Any]:
    """The axis check one variable at a time. An average over every variable hides an axis that matters for two of
    them, so each (variable, tier, axis) gets its own separation beside its own placebos; the share of placebos at
    or above it is its p. With hundreds of such tests some will look good by chance, so the list that counts is the
    one that survives Benjamini–Hochberg at ``q`` — and the count under 0.05 is shown beside what chance gives."""
    days, series = ic_series(market, names, span)
    labels = scenario_labels(market, tier_members(market, span)["top30"], days, span)
    tests = []
    counts: list[list[Any]] = []             # [variable, tier, axis, above, counted, above and counted at round 4's 5000]
    dropped: list[list[str]] = []            # a test whose placebos mostly had no separation: named, never silently gone
    for axis in AXES:
        real = [labels[d].get(axis) for d in days]
        actual_labels = dict(zip(days, real))
        actual = {key: separation({key: values}, actual_labels) for key, values in series.items()}
        actual = {key: value for key, value in actual.items() if value is not None}
        above, counted = dict.fromkeys(actual, 0), dict.fromkeys(actual, 0)
        early: tuple[dict, dict] | None = None
        for k in range(PLACEBO_AXES_BY_VARIABLE):                # one placebo at a time: none is kept
            fake = dict(zip(days, shuffled_spells(real, f"{SPEC}|axis|{axis}|{k}")))
            for key, value in actual.items():
                placebo = separation({key: series[key]}, fake)
                if placebo is not None:
                    counted[key] += 1
                    above[key] += placebo >= value
            if k + 1 == PLACEBO_AXES_BY_VARIABLE_ROUND_4:        # the same placebos round 4 read, in the same order
                early = (dict(above), dict(counted))
        for key in sorted(actual):
            counts.append([key[0], key[1], axis, above[key], counted[key],
                           *((early[0][key], early[1][key]) if early is not None else (None, None))])
            if counted[key] < PLACEBO_AXES_BY_VARIABLE // 2:
                dropped.append([key[0], key[1], axis])
                continue
            states: dict[str, list[float]] = {}
            for day, ic in series[key].items():
                if actual_labels.get(day) is not None:
                    states.setdefault(actual_labels[day], []).append(ic)
            tests.append({"variable": key[0], "tier": key[1], "axis": axis, "separation": round(actual[key], 5),
                          "p": round((1 + above[key]) / (1 + counted[key]), 5),
                          "state_means": {k: round(statistics.fmean(v), 4) for k, v in sorted(states.items())
                                          if len(v) >= MIN_DECISIONS}})
    ordered = sorted(tests, key=lambda t: (t["p"], t["variable"], t["tier"], t["axis"]))
    cutoff = max((i for i, t in enumerate(ordered, start=1) if t["p"] <= q * i / len(ordered)), default=0)
    return {"tests": len(tests), "p_under_0.05": sum(1 for t in tests if t["p"] < 0.05),
            "expected_under_0.05_by_chance": round(0.05 * len(tests), 1), "q": q,
            "placebos_per_test": PLACEBO_AXES_BY_VARIABLE, "survive_benjamini_hochberg": ordered[:cutoff], "smallest_p": ordered[:10],
            "all_tests": [[t["variable"], t["tier"], t["axis"], t["p"]] for t in ordered], "counts": counts, "dropped": dropped}


def round4_matches(counts: Sequence[Sequence[Any]], recorded: Sequence[Sequence[Any]]) -> dict[str, Any]:
    """A run's counts at round 4's 5,000 placebos beside round 4's recorded p, for every test both hold. The placebos
    are drawn in the same order from the same seeds, so the two must agree to the recorded five places."""
    at = {(row[0], row[1], row[2]): (row[5], row[6]) for row in counts}
    shared, differ = 0, []
    for variable, tier, axis, p in recorded:
        if (variable, tier, axis) not in at:
            continue
        shared += 1
        above, counted = at[(variable, tier, axis)]
        if above is None or round((1 + above) / (1 + counted), 5) != p:
            differ.append([variable, tier, axis])
    return {"shared": shared, "differ": differ}


def axes_family(runs: Sequence[Mapping[str, Any]], *, q: float = 0.10) -> dict[str, Any]:
    """The per-variable axis check over every group's run as ONE family, from the counts and not from any run's
    printed p (rounded) or its own survivor list (a correction over that group alone). Refuses runs with different
    placebo counts and a test that appears twice."""
    sizes = {run["placebos_per_test"] for run in runs}
    if len(sizes) != 1:
        return {"refused": "the runs used different numbers of placebos", "placebos_per_test": sorted(sizes)}
    size = sizes.pop()
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    dropped = []
    for run in runs:
        for variable, tier, axis, above, counted, *_ in run["counts"]:
            key = (variable, tier, axis)
            if key in rows or [variable, tier, axis] in dropped:
                return {"refused": "a test appears in two runs", "test": list(key)}
            if counted < size // 2:
                dropped.append([variable, tier, axis])
                continue
            rows[key] = {"variable": variable, "tier": tier, "axis": axis, "above": above, "counted": counted,
                         "p": (1 + above) / (1 + counted)}
    ordered = sorted(rows.values(), key=lambda r: (r["p"], r["variable"], r["tier"], r["axis"]))
    cutoff = max((i for i, r in enumerate(ordered, start=1) if r["p"] <= q * i / len(ordered)), default=0)
    by_axis: dict[str, int] = {}
    for r in ordered:
        by_axis[r["axis"]] = by_axis.get(r["axis"], 0) + 1
    return {"tests": len(ordered), "placebos_per_test": size, "q": q, "tests_by_axis": dict(sorted(by_axis.items())),
            "variables": len({r["variable"] for r in ordered}), "dropped": sorted(dropped),
            "p_under_0.05": sum(1 for r in ordered if r["p"] < 0.05), "expected_under_0.05_by_chance": round(0.05 * len(ordered), 1),
            "first_threshold": q / len(ordered) if ordered else None, "smallest_possible_p": 1 / (1 + size),
            "survive_benjamini_hochberg": ordered[:cutoff], "smallest_p": ordered[:10]}


# --- what kind of universe is it, month by month (D7) ------------------------------------------------------------

CHARACTER_END = bs.CONFIRM[1]              # 2026-09-30: the days after it stay reserved


def _percentile(values: Sequence[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(share * len(ordered)) - 1))]


def universe_character(market: xsb.Market, span: tuple[date, date]) -> dict[str, Any]:
    """The top tier's character by month: how large funding is, how many coins move wildly, how old the coins are
    and how fast the membership turns over. Every value is read at the month's weekly decisions from that day and
    earlier and averaged over them. **No return of any book or candidate is computed.**"""
    monthly = tier_members(market, span)["top30"]
    weekly = [d for d in xsb.held_days(span[0], span[1]) if xsb.is_rebalance_day(bs.CADENCE, d)]
    rows: dict[str, dict[str, list[float]]] = {}
    for day in weekly:
        members = monthly.get(date(day.year, day.month, 1), ())
        funding = [abs(v) * 10_000 for v in (xsb.trailing_funding(market, s, day, 7) for s in members) if v is not None]
        moves = [m for m in (_daily_moves(market, s, day, LOOKBACK) for s in members) if m is not None]
        ages = [(day - first).days for first in (market.first_day(s) for s in members) if first is not None and first <= day]
        row = rows.setdefault(day.strftime("%Y-%m"), {})
        if len(funding) >= MIN_NAMES:
            row.setdefault("funding_abs_median_bps_week", []).append(statistics.median(funding))
            row.setdefault("funding_abs_p90_bps_week", []).append(_percentile(funding, 0.90))
        if len(moves) >= MIN_NAMES:
            row.setdefault("wild_share", []).append(sum(1 for m in moves if max(m) >= WILD_MOVE) / len(moves))
        if len(ages) >= MIN_NAMES:
            row.setdefault("listing_age_median_days", []).append(statistics.median(ages))
    months = {}
    previous: set[str] | None = None
    for start in sorted(monthly):
        key, members = start.strftime("%Y-%m"), set(monthly[start])
        body = {name: round(statistics.fmean(values), 3) for name, values in sorted(rows.get(key, {}).items())}
        body["members"] = len(members)
        if previous and members:
            body["overlap_with_previous_month"] = round(len(members & previous) / len(members), 3)
        if span[0] <= start <= span[1] and body.get("members"):
            months[key] = body
        previous = members or previous
    years: dict[str, dict[str, float]] = {}
    for year in sorted({k[:4] for k in months}):
        picked = [v for k, v in months.items() if k.startswith(year)]
        names = sorted({n for v in picked for n in v if n != "members"})
        years[year] = {n: round(statistics.fmean(v[n] for v in picked if n in v), 3) for n in names}
    return {"months": months, "years": years, "returns_computed": False}


# --- what is on disk and not read (the monthly check) ------------------------------------------------------------

# The columns of the files this lab's loaders read, and which of them a loader keeps. Binance's daily kline file has
# twelve fixed columns; ``cross_section_basket.read_klines`` keeps five of them.
KLINE_COLUMNS = ("open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count",
                 "taker_buy_volume", "taker_buy_quote_volume", "ignore")
KLINE_COLUMNS_READ = ("open_time", "open", "high", "low", "close", "quote_volume")


def inventory(cache: Path) -> dict[str, Any]:
    """What the cache holds beside what the registered variables read: file kinds with their symbol counts, and the
    kline columns no loader keeps. Reads names and one header row; no value."""
    kinds = {}
    for folder in sorted(p for p in cache.iterdir() if p.is_dir()) if cache.exists() else []:
        for kind in sorted(p for p in folder.iterdir() if p.is_dir()):
            kinds[f"{folder.name}/{kind.name}"] = sum(1 for p in kind.iterdir() if p.is_dir())
    sample = next(iter(sorted((cache / "archive" / xsb.KLINES_KIND).glob("*/*.zip"))), None) if cache.exists() else None
    width = len(xsb._rows(sample)[-1]) if sample is not None else None
    return {"kinds": kinds, "kline_columns_in_file": width,
            "kline_columns_not_read": [c for c in KLINE_COLUMNS if c not in KLINE_COLUMNS_READ and c not in ("close_time", "ignore")],
            "registered_variable_groups": sorted({group for group, _ in variables().values()})}


# --- reading the grid against chance -----------------------------------------------------------------------------

def _shown(cells: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [c for c in cells if not c["too_few"] and c.get("t") is not None]


def chance(control_cells: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """What the random variables produced: per horizon, the largest |t| anywhere in the grid and how often |t| > 2."""
    out = {}
    for horizon in HORIZONS:
        ts = sorted(abs(c["t"]) for c in _shown(control_cells) if c["horizon_days"] == horizon)
        out[str(horizon)] = {"cells": len(ts), "largest_abs_t": ts[-1] if ts else None,
                             "p95_abs_t": ts[int(0.95 * (len(ts) - 1))] if ts else None,
                             "share_abs_t_over_2": round(sum(1 for t in ts if t > 2) / len(ts), 4) if ts else None}
        spreads = sorted(abs(c["spread_t"]) for c in _shown(control_cells)
                         if c["horizon_days"] == horizon and c.get("spread_t") is not None)
        out[str(horizon)].update({"spread_cells": len(spreads), "largest_abs_spread_t": spreads[-1] if spreads else None,
                                  "p95_abs_spread_t": spreads[int(0.95 * (len(spreads) - 1))] if spreads else None,
                                  "share_abs_spread_t_over_2": (round(sum(1 for t in spreads if t > 2) / len(spreads), 4)
                                                                if spreads else None)})
    return out


# Round 38: a second lead rule on the same cells, read on the spread's own t. A cell that meets it is a lead for the
# hypothesis form, never a book: these variables give no book on the named tier ("*" is every tier) whatever it says.
SPREAD_NO_BOOK: dict[tuple[str, str], str] = {
    **{("*", name): "the closed volatility cluster and its kin: no more books from it (sections 20, 33.1)"
       for name in ("vol_30", "idio_vol_30", "max_30", "skew_30", "range_30", "beta_30", "extreme_days_30")},
    **{("rank31_100", name): "H-R2's book and its other windows (section 49)" for name in ("momentum_30", "momentum_7", "momentum_90")},
    **{("rank31_100", name): "H-M1's variables on this tier (section 32.2)" for name in ("oi_growth_30", "ls_level_30")},
    **{("rank31_100", name): "the tier sweep's lead, closed, and its other window (section 53.6)" for name in ("funding_30", "funding_7")},
    **{("rank31_100", name): "the tier sweep's second and third books: left alone (section 52.8)"
       for name in ("taker_level_30", "taker_buy_share_30")},
    ("*", "spread_change_30"): "the top tier's lead, not put on the ladder (D10): never read again (section 46)",
    ("*", "trade_size_position_30"): "the top tier's runner-up: left alone (section 45)",
    # Round 40 (section 57): H-R1's tier, ranks 101 and after. One list for the map and the tier sweep there.
    (LOWER_TIER_NAME, "momentum_7"): "H-R1 without its skip day: the week's return on this tier is H-R1's variable (section 23.2)",
    **{(LOWER_TIER_NAME, name): "H-R2's record: never measured below rank 100, nor at another window (section 49)"
       for name in ("momentum_30", "momentum_90")},
    **{(LOWER_TIER_NAME, name): "the tier sweep's closed lead: never on another tier, never at another window (section 53.6)"
       for name in ("funding_30", "funding_7")},
    # Round 45 (section 63) fetched this tier's own positioning files, so the other nine are read here from them
    # (``strategy_ladder.lower_positioning_market``); before that they were struck as a sample chosen by next month's rank.
    **{(LOWER_TIER_NAME, name): "H-M1's variables: closed with 'the tier boundary is not changed' (section 31)"
       for name in ("oi_growth_30", "ls_level_30")},
    **{(LOWER_TIER_NAME, name): "no depth file lies in the span" for name in ("imbalance_1_30", "imbalance_5_30", "depth_growth_30")},
}


def no_book(variable: str, tier: str) -> str | None:
    return SPREAD_NO_BOOK.get(("*", variable)) or SPREAD_NO_BOOK.get((tier, variable))


def spread_candidates(real: Mapping[str, Any], against: Mapping[str, Any]) -> dict[str, Any]:
    """The cells whose spread stands out: |spread t| above the largest any random variable reached at that horizon,
    a variable that keeps its rank from week to week, the same sign as the variable's all-weeks spread in its tier,
    and not on a resting axis. Every such cell is listed (no cap), each with whether the rank correlation agrees in
    sign and whether a record forbids a book of that variable there."""
    shown = [c for c in _shown(real["cells"]) if c.get("spread_t") is not None]
    whole = {(c["variable"], c["tier"], c["horizon_days"]): c for c in shown if c["scenario"] == "all"}
    found = []
    for c in shown:
        if any(part.split("=")[0] in RESTING_AXES for part in c["scenario"].split("|") if "=" in part):
            continue
        bar = against[str(c["horizon_days"])].get("largest_abs_spread_t")
        steady = real["variables"].get(f"{c['variable']}|{c['tier']}", {}).get("rank_persistence")
        base = whole.get((c["variable"], c["tier"], c["horizon_days"]))
        if bar is None or abs(c["spread_t"]) <= bar or steady is None or steady < MIN_PERSISTENCE:
            continue
        if base is None or (base["spread_t"] > 0) != (c["spread_t"] > 0):
            continue
        found.append({**c, "rank_correlation_agrees": (c["t"] > 0) == (c["spread_t"] > 0),
                      "no_book": no_book(c["variable"], c["tier"])})
    found.sort(key=lambda c: (-abs(c["spread_t"]), c["variable"], c["tier"], c["scenario"]))
    return {"rule": "abs(spread t) above the random variables' largest at that horizon; rank persistence at least "
                    f"{MIN_PERSISTENCE}; same sign as the variable's all-weeks spread in its tier; not on a resting axis",
            "met_the_rule": len(found), "leads": found}


def candidates(real: Mapping[str, Any], against: Mapping[str, Any], *, cap: int | None = CANDIDATE_CAP) -> dict[str, Any]:
    """The leads, by the rule fixed before the run: a shown cell whose |t| is above the largest any random variable
    reached at that horizon, whose variable keeps its rank from week to week, and whose sign agrees with the same
    variable's all-weeks cell in its tier. At most :data:`CANDIDATE_CAP`, largest |t| first."""
    whole = {(c["variable"], c["tier"], c["horizon_days"]): c for c in _shown(real["cells"]) if c["scenario"] == "all"}
    found = []
    for c in _shown(real["cells"]):
        if any(part.split("=")[0] in RESTING_AXES for part in c["scenario"].split("|") if "=" in part):
            continue
        bar = against[str(c["horizon_days"])]["largest_abs_t"]
        steady = real["variables"].get(f"{c['variable']}|{c['tier']}", {}).get("rank_persistence")
        base = whole.get((c["variable"], c["tier"], c["horizon_days"]))
        if bar is None or abs(c["t"]) <= bar or steady is None or steady < MIN_PERSISTENCE:
            continue
        if base is None or (base["mean_rank_correlation"] > 0) != (c["mean_rank_correlation"] > 0):
            continue
        found.append(c)
    found.sort(key=lambda c: (-abs(c["t"]), c["variable"], c["tier"], c["scenario"]))
    return {"rule": "abs(t) above the random variables' largest at that horizon; rank persistence at least "
                    f"{MIN_PERSISTENCE}; same sign as the variable's all-weeks cell in its tier; not on a resting axis",
            "resting_axes": sorted(RESTING_AXES),
            "cap": cap, "met_the_rule": len(found), "leads": found if cap is None else found[:cap]}


def excess(real_cells: Sequence[Mapping[str, Any]], against: Mapping[str, Any]) -> dict[str, Any]:
    """How many real cells show |t| > 2 beside how many the random variables' rate would give."""
    out = {}
    for horizon in HORIZONS:
        shown = [c for c in _shown(real_cells) if c["horizon_days"] == horizon]
        rate = against[str(horizon)]["share_abs_t_over_2"]
        out[str(horizon)] = {"cells": len(shown), "abs_t_over_2": sum(1 for c in shown if abs(c["t"]) > 2),
                             "expected_from_random": round(rate * len(shown), 1) if rate is not None else None}
    return out


# --- the baseline Thomas approved: the plain pool of the registered definitions ------------------------------------

def pool(market: xsb.Market, span: tuple[date, date] = SPAN, *, simulate: Callable[..., Any] = xsb.simulate) -> dict[str, Any]:
    """Every registered definition as registered and their equal-weight pool over the span, net of the engine's
    costs (the forward panel's reading, on development years). A member without a basket on a day counts as zero."""
    start, end = span
    monthly = xsb.universes(market, top_n=bs.TOP_N, start=date(start.year, start.month, 1) - timedelta(days=1), end=end)
    monthly = {date(m.year, m.month, 1): v for m, v in monthly.items()}
    rows, daily = {}, {}
    for name, candidate, features in fp.members():
        sim = simulate(market, bs.CADENCE, monthly, start=start, end=end, weigher=bs.weigher(candidate, features))
        sharpe, values = bs.annual_sharpe(sim["daily"], start, end)
        rows[name] = {"annual_sharpe": sharpe, "sum_pct": round(sum(values) * 100, 2), "months": len(values)}
        daily[name] = {day: row["net"] for day, row in sim["daily"].items()}
    days = sorted({day for series in daily.values() for day in series})
    pooled = {day: {"net": sum(series.get(day, 0.0) for series in daily.values()) / len(daily)} for day in days}
    sharpe, values = bs.annual_sharpe(pooled, start, end)
    report = {"annual_sharpe": sharpe, "sum_pct": round(sum(values) * 100, 2), "months": len(values)}
    report["total_position_annual_pct"] = bs.total_position_annual_pct(report["sum_pct"], report["months"])
    report["clears_entry"] = bs.clears_entry(report)
    report["years_pct"] = {str(y): round(sum(r["net"] for d, r in pooled.items() if d.year == y) * 100, 2)
                           for y in sorted({d.year for d in pooled})}
    return {"members": rows, "pool": report, "rule": "equal weight of every registered definition; no basket counts as zero"}


# --- the host side -----------------------------------------------------------------------------------------------

def hold(cache: Path, span: tuple[date, date] = SPAN, *, attach: bool = True, flow: bool = False) -> xsb.Market | None:
    """The market cut at the span's last day with positioning, depth and spot attached, or None when a piece is missing.

    ``attach=False`` loads the perp bars and funding alone, for a book whose variable and scenario read nothing
    else: the attached series are most of the memory, and a span that runs to 2026 does not fit the sandbox with
    them. ``flow`` also hangs the trade-flow columns (:func:`attach_trade_flow`)."""
    archive = cache / "archive"
    snapshot = xsb.read_contract_types(archive)
    if not attach:
        if snapshot is None:
            return None
        market = xsb.load_market(archive, funding=True)
        market.contract_types = snapshot[0]
        held = cut_market(market, span)
        return attach_trade_flow(held, archive, span) if flow else held
    try:
        listing = set(json.loads((cache / sp.SPOT_DIR / ba.SYMBOLS_FILENAME).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        listing = set()
    if snapshot is None or not listing:
        return None
    market = xsb.load_market(archive, funding=True)
    market.contract_types = snapshot[0]
    held = ds.cut(market, ds.load_daily(cache), span[1], ms.load_daily(cache))
    held = sp.attach(held, xsb.load_market(cache / sp.SPOT_DIR, funding=False), listing, span[1], sp.months_on_disk(cache))
    held = cut_market(held, span)
    return attach_trade_flow(held, archive, span) if flow else held


def hold_positioning(cache: Path, span: tuple[date, date] = SPAN) -> xsb.Market | None:
    """The market cut at the span's last day with the positioning values alone attached: all a run of that group
    reads. Depth and spot are most of the attached memory, and once the positioning files of both tiers were on
    disk the three no longer fitted the sandbox together (the first run of 2026-11-14 was killed while loading).
    None when the contract types or the derived positioning values are missing."""
    held = hold(cache, span, attach=False)
    daily = ms.load_daily(cache)
    if held is None or not daily:
        return None
    held.metrics = {s: {d: v for d, v in days.items() if d <= span[1]} for s, days in daily.items()}
    return held


def read_premium_api(cache: Path, symbols: Sequence[str], last: date) -> dict[str, dict[date, float]]:
    """The premium index's daily close from the venue API's files, up to ``last``. These exist only for the symbols
    an earlier search asked for (section 50), so nothing ranks coins on them any more; :func:`premium_sources`
    lays them beside the archive's."""
    out: dict[str, dict[date, float]] = {}
    for symbol in symbols:
        days: dict[date, float] = {}
        for row in lake.read_rows(lake.derivative_path(cache, "premium", symbol, "1d")):
            try:
                day, close = xsb._utc_day(float(row[0])), float(row[4])
            except (TypeError, ValueError, IndexError):
                continue
            if day <= last:
                days[day] = close
        if days:
            out[symbol] = days
    return out


def read_premium(cache: Path, symbols: Sequence[str], last: date) -> dict[str, dict[date, float]]:
    """``{symbol: {day: the premium index's daily close}}`` from the archive's monthly files, up to ``last`` and no
    further. One source and the same rule for every coin: a day the archive lacks is not filled from elsewhere."""
    out: dict[str, dict[date, float]] = {}
    for symbol in symbols:
        folder = cache / "archive" / ba.PREMIUM_KIND / symbol
        days: dict[date, float] = {}
        for path in sorted(folder.glob("*.zip")) if folder.exists() else []:
            for row in xsb._rows(path):
                try:
                    day, close = xsb._utc_day(float(row[0])), float(row[4])
                except (TypeError, ValueError, IndexError):
                    continue          # the header row
                if day <= last:
                    days[day] = close
        if days:
            out[symbol] = days
    return out


def premium_sources(market: xsb.Market, cache: Path, span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """The archive's premium beside the venue API's, with no return in it: where both have a day the closes must be
    equal; which days each has that the other lacks; and how many of each tier's members have a premium at all,
    under each source."""
    symbols = sorted(market.bars)
    archive, api = read_premium(cache, symbols, span[1]), read_premium_api(cache, symbols, span[1])
    compared = differ = only_api = only_archive = 0
    worst: list[list[Any]] = []
    for symbol in sorted(set(archive) & set(api)):
        a, b = archive[symbol], api[symbol]
        for day in set(a) & set(b):
            compared += 1
            if a[day] != b[day]:
                differ += 1
                if len(worst) < 10:
                    worst.append([symbol, day.isoformat()])
        first = max(min(a), min(b))                      # count gaps only where both series have begun
        only_api += sum(1 for d in b if d not in a and d >= first)
        only_archive += sum(1 for d in a if d not in b and d >= first)
    tiers = {}
    for tier, monthly in tier_members(market, span).items():
        rows = {"member_months": 0, "with_archive_premium": 0, "with_api_premium": 0}
        without: set[str] = set()
        for month, members in monthly.items():
            if not span[0] <= month <= span[1]:
                continue
            for symbol in members:
                rows["member_months"] += 1
                rows["with_archive_premium"] += symbol in archive
                rows["with_api_premium"] += symbol in api
                if symbol not in archive:
                    without.add(symbol)
        tiers[tier] = {**rows, "members_without_archive_premium": sorted(without)}
    return {"symbols": len(symbols), "archive_symbols": len(archive), "api_symbols": len(api),
            "in_both": len(set(archive) & set(api)), "days_compared": compared, "days_differ": differ,
            "first_differences": worst, "days_only_in_api": only_api, "days_only_in_archive": only_archive,
            "tiers": tiers, "values_equal": compared > 0 and differ == 0, "reads_returns": False}


def hold_premium(cache: Path, span: tuple[date, date] = SPAN) -> xsb.Market | None:
    """The market cut at the span's last day with the premium index attached. None when a piece is missing."""
    held = hold(cache, span, attach=False)
    if held is None:
        return None
    held.premium = read_premium(cache, sorted(held.bars), span[1])
    return held if held.premium else None


def hold_depth(cache: Path, span: tuple[date, date] = SPAN) -> xsb.Market | None:
    """The market cut at the span's last day with the depth values alone attached. The combined load (positioning,
    depth and spot together) no longer fits the sandbox. None when a piece is missing."""
    held = hold(cache, span, attach=False)
    daily = ds.load_daily(cache)
    if held is None or not daily:
        return None
    held.depth = {s: {d: v for d, v in days.items() if d <= span[1]} for s, days in daily.items()}
    return held


def hold_spot(cache: Path, span: tuple[date, date] = SPAN) -> xsb.Market | None:
    """The market cut at the span's last day with the spot bars alone attached: all a run of the spot group reads.
    None when the contract types or the spot listing are missing."""
    held = hold(cache, span, attach=False)
    try:
        listing = set(json.loads((cache / sp.SPOT_DIR / ba.SYMBOLS_FILENAME).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        listing = set()
    if held is None or not listing:
        return None
    return sp.attach(held, xsb.load_market(cache / sp.SPOT_DIR, funding=False), listing, span[1], sp.months_on_disk(cache))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="signal_lab", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("list", "run", "axes", "axes-by-variable", "inventory", "universe", "overlap",
                                            "premium-sources"))
    parser.add_argument("--candidate", default=None, help="overlap: the candidate variable to rank beside its kin")
    parser.add_argument("--only", default=None, help="run: a variable group to map alone, with the random variables")
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    args = parser.parse_args(argv)
    if args.command == "list":
        print(json.dumps({"spec": SPEC, "span": [d.isoformat() for d in SPAN], "horizons": list(HORIZONS),
                          "tiers": {k: list(v) for k, v in TIERS.items()},
                          "variables": {name: group for name, (group, _) in variables().items()},
                          "controls": len(controls())}, indent=1))
        return EXIT_OK
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    if args.command == "inventory":
        print(json.dumps({"spec": SPEC, **inventory(cache)}, indent=1, sort_keys=True))
        return EXIT_OK
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    if args.command == "universe":
        span = (SPAN[0], CHARACTER_END)
        bs.check_search_span(span)
        market = hold(cache, span, attach=False)
        if market is None:
            print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in span], "last_day_read": last_day(market).isoformat(),
                          "judgement": None, **universe_character(market, span)}, indent=1, sort_keys=True))
        return EXIT_OK
    bs.check_development_span(SPAN)
    if args.command == "premium-sources":
        market = hold(cache, attach=False)
        if market is None:
            print("BLOCKED: contract types are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        body = premium_sources(market, cache)
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "last_day_read": last_day(market).isoformat(),
                          "judgement": None, **body}, indent=1, sort_keys=True))
        return EXIT_OK if body["values_equal"] else EXIT_BLOCKED
    if args.command == "overlap":
        if args.candidate not in OVERLAP_CANDIDATES:
            print(f"refused: --candidate is one of {sorted(OVERLAP_CANDIDATES)}", file=sys.stderr)
            return EXIT_USAGE
        market = hold_premium(cache) if OVERLAP_CANDIDATES[args.candidate][0] == "premium" else hold(cache, attach=False)
        if market is None:
            print("BLOCKED: contract types or the premium files are missing from the cache", file=sys.stderr)
            return EXIT_BLOCKED
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "last_day_read": last_day(market).isoformat(),
                          "reads_returns": False, "judgement": None,
                          **({"premium_symbols": len(market.premium),
                              "premium_last_day": max(max(days) for days in market.premium.values()).isoformat()}
                             if getattr(market, "premium", None) else {}),
                          **overlap(market, args.candidate)}, indent=1, sort_keys=True))
        return EXIT_OK
    chosen = {n: v for n, v in variables().items() if args.only is None or v[0] == args.only}
    if not chosen:
        print(f"refused: no registered variable is in group {args.only!r}", file=sys.stderr)
        return EXIT_USAGE
    groups = {group for group, _ in chosen.values()}
    # Load what the chosen variables read and nothing else: the attached series are most of the memory.
    if groups == {"positioning"}:
        market = hold_positioning(cache)
    elif groups == {"spot"}:
        market = hold_spot(cache)
    elif groups == {"premium"}:
        market = hold_premium(cache)
    elif groups == {"depth"}:
        market = hold_depth(cache)
    else:
        market = hold(cache, attach=bool(groups & {"positioning", "depth", "spot"}), flow="trade_flow" in groups)
    if market is None:
        print("BLOCKED: contract types, the spot symbol list or the derived values are missing from the cache", file=sys.stderr)
        return EXIT_BLOCKED
    if args.command == "axes-by-variable":
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "last_day_read": last_day(market).isoformat(),
                          "judgement": None, "only": args.only, **axis_by_variable(market, chosen)},
                         indent=1, sort_keys=True))
        return EXIT_OK
    if args.command == "axes":
        print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                          "span": [d.isoformat() for d in SPAN], "last_day_read": last_day(market).isoformat(),
                          "judgement": None, **axis_check(market, variables())}, indent=1, sort_keys=True))
        return EXIT_OK
    real, random = grid(market, chosen), grid(market, controls())
    against = chance(random["cells"])
    print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                      "span": [d.isoformat() for d in SPAN], "last_day_read": last_day(market).isoformat(),
                      "before_costs": True, "judgement": None, "only": args.only,
                      "chance": against, "excess": excess(real["cells"], against),
                      "candidates": candidates(real, against), "spread_candidates": spread_candidates(real, against),
                      "dropped": real["dropped"],
                      "variables": real["variables"], "cells": real["cells"],
                      **({"baseline_pool": pool(market)} if args.only is None else {})},
                     indent=1, sort_keys=True, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
