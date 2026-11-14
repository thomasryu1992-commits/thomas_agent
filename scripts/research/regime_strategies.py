"""Regime strategy set: one market-level BULL/SIDEWAYS/BEAR label, twelve strategies per regime, two stages.

``docs/proposals/CRYPTO_REGIME_STRATEGY_SET_V0.1.md`` (Thomas 2026-11-04: §6 Q1–Q4 "기본값대로", Q3 option B).
It reads the archive ``cross_section_basket`` reads (``klines-1d`` and ``fundingRate`` under
``<RESEARCH_CACHE>/archive``), plus the daily ``metrics`` files under ``<RESEARCH_CACHE>/archive-metrics`` for open
interest, and writes nothing.

- **Regime (§1):** each UTC close, from values up to that close. BTCUSDT close against its SMA200 (``T1``), the
  20-day change of its SMA50 (``T2``), and the month's top-30 coin perps' share closing above their own SMA50
  (``B``). BULL is T1 up, T2 > 0, B ≥ 0.55; BEAR the mirror with B ≤ 0.45; otherwise SIDEWAYS. A new label is
  confirmed after :data:`CONFIRM_DAYS` identical raw readings in a row.
- **Engine:** a strategy hands the engine the weights it decides at day ``d``'s close; they are held from
  ``d+1``'s open to ``d+2``'s, exactly as ``cross_section_basket.simulate`` holds a basket. Every unit of weight
  traded pays taker plus slippage (``crypto/cost.py``), funding settled in a held day is paid by longs, and a
  leg whose next bar is missing closes at its last close.
- **Two kinds of strategy (§7 of the record, "구현 선택"):** the baskets that name their own legs (B03, D03,
  S04, S09, B07, D07) keep the v0.2 basket mechanics: fixed ±1/k, rebalanced on Sunday's close, no stop, no
  six-leg cap. Everything else is a position strategy: at most :data:`MAX_POSITIONS` open, the day's notional
  of 1 split evenly over them, a stop at :data:`STOP_ATR` × ATR(14) judged on the close, and
  :data:`MAX_HOLD_DAYS` at most. New entries only while the confirmed regime is the strategy's own.
- **Segments (§5, option B):** the window is cut into the held-out quarters (one a year, drawn from
  :data:`HOLDOUT_SEED`), a :data:`EMBARGO_DAYS` embargo on each side, and the screening rest. Each segment runs
  from a clean book and closes everything at its end, so no trade spans two segments.
- **Weeks:** Monday–Sunday; a week belongs to the segment holding its Monday, and to a regime when that regime is
  the label on more than half of its days inside the segment.

``regimes`` prints the label record and the holdout split. ``stage1`` screens on the screening segments only.
``stage2`` reads the held-out quarters once and needs the stage-1 record. Both run in the research sandbox only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import zipfile
import io
import csv
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from runtime.mvp_runtime.crypto import cost, indicators as ind  # noqa: E402
from scripts.research import cross_section_basket as xs  # noqa: E402

SPEC = "CRYPTO_REGIME_STRATEGY_SET_V0.1"
BTC, ETH = "BTCUSDT", "ETHUSDT"
TOP_N = 30
LABEL_START = date(2020, 1, 1)            # labels need SMA200 on BTCUSDT (futures bars from 2019-09)
WINDOW_START = date(2020, 10, 1)          # the first month the top 30 is full (CROSS_SECTION_CARRY… §2)
WINDOW_END = xs.END
BULL, SIDEWAYS, BEAR = "BULL", "SIDEWAYS", "BEAR"
REGIMES = (BULL, SIDEWAYS, BEAR)
CONFIRM_DAYS = 3
BREADTH_BULL, BREADTH_BEAR = 0.55, 0.45
BREADTH_MIN_MEMBERS = 4
HOLDOUT_SEED = f"{SPEC}/holdout"
HOLDOUT_YEARS = tuple(range(2021, 2027))
EMBARGO_DAYS = 30
MAX_POSITIONS = 6
STOP_ATR = 2.5
TRAIL_ATR = 3.0
MAX_HOLD_DAYS = 30
SCREEN_MIN_T = 1.5
SCREEN_MIN_TRADES = 30
SCREEN_KEEP = 3
FLOOR_WEEKS = 13
FLOOR_TRADES = 20
HOLM_ALPHA = 0.10
RANDOM_SEEDS = 8
METRICS_DIR = "metrics"
# §5 option B: a candidate derived from a hypothesis already tested on this window carries that test's p values
# (exact, from runs 20261104T070402Z-164942 and 20261104T092514Z-253611).
PRIOR_P: dict[str, dict[str, float]] = {
    "S-MOM30": {"v0.1 S-MOM30": 0.12249320636703333, "v0.2 S-MOM30": 0.001573773950072075},
    "S-MOM7": {"v0.1 S-MOM7": 0.28617573036292365},
    "S-CARRY": {"v0.2 S-CARRY": 0.15483770934833957},
}
PARENT = {"B03": "S-MOM30", "D03": "S-MOM30", "S04": "S-MOM7", "S09": "S-CARRY", "D07": "S-CARRY"}


# --- the held-out quarters and the segments -------------------------------------------------------------

def quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    start = date(year, 3 * quarter - 2, 1)
    end = date(year + (quarter == 4), 1 if quarter == 4 else 3 * quarter + 1, 1) - timedelta(days=1)
    return start, end


def holdout_quarters(seed: str = HOLDOUT_SEED, years: Sequence[int] = HOLDOUT_YEARS,
                     end: date = WINDOW_END) -> list[tuple[int, int]]:
    """One quarter a year, ``sha256(seed/year) mod n + 1``, n counting only the quarters that end in the window."""
    out = []
    for year in years:
        n = sum(1 for q in range(1, 5) if quarter_bounds(year, q)[1] <= end)
        q = int(hashlib.sha256(f"{seed}/{year}".encode()).hexdigest(), 16) % n + 1
        out.append((year, q))
    return out


def segments(start: date = WINDOW_START, end: date = WINDOW_END,
             held: Sequence[tuple[int, int]] | None = None) -> dict[str, list[tuple[date, date]]]:
    """``{"holdout": [...], "screen": [...], "embargo": [...]}`` as inclusive day ranges."""
    held = holdout_quarters() if held is None else held
    blocks = sorted(quarter_bounds(y, q) for y, q in held)
    blocked = [(a - timedelta(days=EMBARGO_DAYS), b + timedelta(days=EMBARGO_DAYS)) for a, b in blocks]
    screen, embargo, day, current = [], [], start, None
    while day <= end:
        inside = any(a <= day <= b for a, b in blocks)
        near = any(a <= day <= b for a, b in blocked)
        kind = "holdout" if inside else "embargo" if near else "screen"
        if kind == "screen":
            if current is None:
                current = [day, day]
                screen.append(current)
            current[1] = day
        else:
            current = None
            if kind == "embargo":
                if embargo and embargo[-1][1] == day - timedelta(days=1):
                    embargo[-1] = (embargo[-1][0], day)
                else:
                    embargo.append((day, day))
        day += timedelta(days=1)
    return {"holdout": [(max(a, start), min(b, end)) for a, b in blocks],
            "screen": [(a, b) for a, b in screen], "embargo": embargo}


# --- the series a strategy reads --------------------------------------------------------------------------

class Data:
    """One market's bars, funding and open interest, with each symbol's indicators computed once."""

    def __init__(self, market: xs.Market, monthly: Mapping[date, Sequence[str]],
                 oi: Mapping[str, Mapping[date, float]] | None = None) -> None:
        self.market = market
        self.monthly = dict(monthly)
        self.oi = {s: dict(v) for s, v in (oi or {}).items()}
        self._series: dict[str, dict[str, Any]] = {}
        self.oi_reads = {"asked": 0, "missing": 0}

    def members(self, day: date) -> list[str]:
        return list(self.monthly.get(date(day.year, day.month, 1), ()))

    def bar(self, symbol: str, day: date) -> xs.Bar | None:
        return (self.market.bars.get(symbol) or {}).get(day)

    def _base(self, symbol: str) -> dict[str, Any]:
        if symbol not in self._series:
            bars = self.market.bars.get(symbol) or {}
            days = sorted(bars)
            self._series[symbol] = {
                "days": days, "index": {d: i for i, d in enumerate(days)},
                "open": [bars[d].open for d in days], "close": [bars[d].close for d in days],
                "high": [bars[d].high if bars[d].high is not None else max(bars[d].open, bars[d].close) for d in days],
                "low": [bars[d].low if bars[d].low is not None else min(bars[d].open, bars[d].close) for d in days],
                "qv": [bars[d].quote_volume for d in days]}
        return self._series[symbol]

    def series(self, symbol: str, key: str) -> list[float | None]:
        base = self._base(symbol)
        if key in base:
            return base[key]
        c, h, lo = base["close"], base["high"], base["low"]
        if key.startswith("sma"):
            base[key] = fast_mean(c, int(key[3:]))
        elif key.startswith("ema"):
            base[key] = ind.ema(c, int(key[3:]))
        elif key.startswith("rsi"):
            base[key] = ind.rsi(c, int(key[3:]))
        elif key == "atr14":
            base[key] = ind.atr(h, lo, c, 14)
        elif key == "atr_pct":
            base[key] = ind.rolling_percentile(self.series(symbol, "atr14"), 100)
        elif key in ("bb_up", "bb_lo", "bb_width"):
            up, low_band, width, _ = ind.bollinger(c, 20, 2.0)
            base["bb_up"], base["bb_lo"], base["bb_width"] = up, low_band, width
        elif key == "bb_width_pct":
            base[key] = ind.rolling_percentile(self.series(symbol, "bb_width"), 100)
        elif key == "ret1":
            base[key] = [None] + [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
        elif key == "ret_sd60":   # the 60 daily returns before today's
            r = self.series(symbol, "ret1")
            base[key] = [None] + ind.rolling_std(r, 60, 60)[:-1]
        elif key in ("qv_avg20", "qv_avg30"):   # the n days before today's
            base[key] = [None] + fast_mean(base["qv"], int(key[-2:]))[:-1]
        else:
            raise KeyError(key)
        return base[key]

    def at(self, symbol: str, key: str, day: date, back: int = 0) -> float | None:
        base = self._base(symbol)
        i = base["index"].get(day)
        if i is None or i - back < 0:
            return None
        return self.series(symbol, key)[i - back]

    def window(self, symbol: str, key: str, day: date, n: int, *, include_today: bool) -> list[float] | None:
        """The ``n`` values ending today (or yesterday), None when the history is shorter."""
        base = self._base(symbol)
        i = base["index"].get(day)
        if i is None:
            return None
        end = i + 1 if include_today else i
        if end - n < 0:
            return None
        return self.series(symbol, key)[end - n:end]

    def ret(self, symbol: str, day: date, lookback: int) -> float | None:
        now, then = self.at(symbol, "close", day), self.at(symbol, "close", day, lookback)
        return None if now is None or then is None else now / then - 1

    def funding_sum(self, symbol: str, day: date, days: int) -> float | None:
        return xs.trailing_funding(self.market, symbol, day, days)

    def open_interest(self, symbol: str, day: date) -> float | None:
        self.oi_reads["asked"] += 1
        value = (self.oi.get(symbol) or {}).get(day)
        if value is None:
            self.oi_reads["missing"] += 1
        return value


def fast_mean(values: Sequence[float], n: int) -> list[float | None]:
    """``indicators.sma`` over a gap-free float series, by running sums (the same values, O(len))."""
    out: list[float | None] = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        out.append(total / n if i >= n - 1 else None)
    return out


# --- the regime label ------------------------------------------------------------------------------------

def raw_regime(data: Data, day: date) -> str | None:
    s200, s50, s50_then = (data.at(BTC, "sma200", day), data.at(BTC, "sma50", day),
                           data.at(BTC, "sma50", day, 20))
    close = data.at(BTC, "close", day)
    if None in (s200, s50, s50_then, close):
        return None
    above = []
    for symbol in data.members(day):
        c, m = data.at(symbol, "close", day), data.at(symbol, "sma50", day)
        if c is not None and m is not None:
            above.append(c > m)
    if len(above) < BREADTH_MIN_MEMBERS:
        return None
    breadth = sum(above) / len(above)
    up, slope = close > s200, s50 - s50_then
    if up and slope > 0 and breadth >= BREADTH_BULL:
        return BULL
    if not up and slope < 0 and breadth <= BREADTH_BEAR:
        return BEAR
    return SIDEWAYS


def regime_labels(data: Data, start: date = LABEL_START, end: date = WINDOW_END) -> dict[str, Any]:
    """Confirmed labels by day, and the record §4 asks for (days, spells, days the lag held a label)."""
    labels: dict[date, str] = {}
    state, candidate, run, lagged = None, None, 0, 0
    day = start
    while day <= end:
        raw = raw_regime(data, day)
        if raw is not None:
            if state is None:
                state = raw
            elif raw != state:
                run = run + 1 if raw == candidate else 1
                candidate = raw
                if run >= CONFIRM_DAYS:
                    state, candidate, run = raw, None, 0
            else:
                candidate, run = None, 0
            lagged += raw != state
            labels[day] = state
        day += timedelta(days=1)
    return {"labels": labels, "lagged_days": lagged}


def label_record(labels: Mapping[date, str], start: date = WINDOW_START, end: date = WINDOW_END) -> dict[str, Any]:
    days = {r: 0 for r in REGIMES}
    spells = {r: 0 for r in REGIMES}
    prev = None
    for day in sorted(d for d in labels if start <= d <= end):
        days[labels[day]] += 1
        if labels[day] != prev:
            spells[labels[day]] += 1
            prev = labels[day]
    return {"days": days, "spells": spells}


# --- the engine --------------------------------------------------------------------------------------------

Decide = Callable[[date, dict[str, float]], "dict[str, float] | None"]


def run(data: Data, decide: Decide, start: date, end: date, *, fee_bps: float | None = None) -> dict[str, Any]:
    """Daily rows over [start, end] from a clean book; whatever is open after ``end`` closes at ``end+1``'s open.

    ``decide(d, weights)`` returns the weights decided at ``d``'s close (None keeps the book). It is asked for
    ``start−1`` … ``end−1``. A row carries gross, cost, funding, net and the legs entered that day."""
    fee_bps = cost.DEFAULT_TAKER_FEE_BPS if fee_bps is None else fee_bps
    unit = (fee_bps + cost.DEFAULT_SLIPPAGE_BPS) / 10_000
    weights: dict[str, float] = {}
    daily: dict[date, dict[str, float]] = {}
    forced = 0
    day = start
    while day <= end:
        target = decide(day - timedelta(days=1), dict(weights))
        trade_cost, entries = 0.0, 0
        if target is not None:
            target = {s: w for s, w in target.items() if w != 0}
            for symbol in set(weights) | set(target):
                old, new = weights.get(symbol, 0.0), target.get(symbol, 0.0)
                trade_cost += abs(new - old) * unit
                entries += new != 0 and (old == 0 or (old > 0) != (new > 0))
            weights = target
        gross = paid = exit_cost = 0.0
        closing = []
        for symbol, w in weights.items():
            today, tomorrow = data.bar(symbol, day), data.bar(symbol, day + timedelta(days=1))
            if today is None:
                closing.append(symbol)
                continue
            if tomorrow is None:
                gross += w * (today.close / today.open - 1)
                exit_cost += abs(w) * unit
                forced += 1
                closing.append(symbol)
            else:
                gross += w * (tomorrow.open / today.open - 1)
            rate, _ = xs.funding_on(data.market, symbol, day)
            paid += -w * rate
        for symbol in closing:
            weights.pop(symbol, None)
        if day == end:                     # the segment ends: everything still open closes at end+1's open
            exit_cost += sum(abs(w) for w in weights.values()) * unit
            weights = {}
        if trade_cost or exit_cost or gross or paid or entries or weights or closing:
            daily[day] = {"gross": gross, "cost": trade_cost + exit_cost, "funding": paid,
                          "net": gross - trade_cost - exit_cost + paid, "entries": entries}
        day += timedelta(days=1)
    return {"daily": daily, "forced_closes": forced}


# --- strategies ----------------------------------------------------------------------------------------------

@dataclass
class Position:
    symbol: str
    side: int                    # +1 long, −1 short
    signal_day: date
    atr: float | None
    hedge: str | None = None     # a pair's other leg, held the other way
    info: dict[str, Any] = field(default_factory=dict)

    def held(self, day: date) -> int:
        return (day - self.signal_day).days


@dataclass(frozen=True)
class Strategy:
    sid: str
    regime: str
    kind: str                                   # "basket", "position" or "rotation"
    needs: tuple[str, ...] = ()                  # "funding", "oi"


class Rules:
    """The decide functions of §3. ``labels`` is the confirmed regime by day."""

    def __init__(self, data: Data, labels: Mapping[date, str]) -> None:
        self.d = data
        self.labels = labels

    def in_regime(self, sid: str, day: date) -> bool:
        return self.labels.get(day) == STRATEGIES[sid].regime

    # baskets: fixed ±1/k on Sunday's close (cross_section_basket's weights), flat when out of regime
    def basket(self, sid: str) -> Decide:
        d = self.d

        def decide(day: date, book: dict[str, float]) -> dict[str, float] | None:
            if day.weekday() != xs.SUNDAY:
                return None
            if not self.in_regime(sid, day):
                return {}
            members = d.members(day)
            if sid in ("B03", "D03"):
                w = xs.target_weights(d.market, members, day, "S-MOM30")
                return {s: v for s, v in w.items() if (v > 0) == (sid == "B03")}
            if sid == "S04":
                return {s: -v for s, v in xs.target_weights(d.market, members, day, "S-MOM7").items()}
            if sid == "S09":
                return xs.target_weights(d.market, members, day, "S-CARRY")
            if sid == "D07":
                return {s: v for s, v in xs.target_weights(d.market, members, day, "S-CARRY").items() if v < 0}
            if sid == "B07":
                ranked = sorted(((s, r) for s in members if (r := d.ret(s, day, 30)) is not None),
                                key=lambda sr: (-sr[1], sr[0]))[:10]
                funded = [(s, f) for s, _ in ranked if (f := d.funding_sum(s, day, 7)) is not None]
                if not funded:
                    return {}
                median = statistics.median(f for _, f in funded)
                picks = [s for s, f in funded if f <= median]
                return {s: 1.0 / len(picks) for s in picks}
            raise KeyError(sid)
        return decide

    # rotations: one basket of fixed legs, entered and left on a market condition
    def rotation(self, sid: str) -> Decide:
        d = self.d
        held: dict[str, Any] = {}

        def condition(day: date) -> bool | None:
            if sid == "B09":
                eth, btc = d.ret(ETH, day, 30), d.ret(BTC, day, 30)
                return None if eth is None or btc is None else (1 + eth) / (1 + btc) - 1 > 0
            rets = [r for s in d.members(day) if (r := d.ret(s, day, 14)) is not None]
            btc = d.ret(BTC, day, 14)
            return None if btc is None or not rets else btc > statistics.fmean(rets)

        def legs(day: date) -> dict[str, float]:
            alts = [s for s in d.members(day) if s != BTC]
            if sid == "B09":
                picks = alts[:10]                                   # members are ordered by volume
                return {**{s: 0.5 / len(picks) for s in picks}, BTC: -0.5} if picks else {}
            ranked = sorted(((s, r) for s in alts if (r := d.ret(s, day, 14)) is not None),
                            key=lambda sr: (sr[1], sr[0]))[:10]
            return {**{s: -0.5 / len(ranked) for s, _ in ranked}, BTC: 0.5} if ranked else {}

        def decide(day: date, book: dict[str, float]) -> dict[str, float] | None:
            ok = condition(day)
            if held.get("on"):
                if ok is False or (day - held["since"]).days >= MAX_HOLD_DAYS:
                    held.clear()
                    return {}
                return {s: w for s, w in held["legs"].items() if s in book}
            if ok and self.in_regime(sid, day):
                held.update(on=True, since=day, legs=legs(day))
                return dict(held["legs"])
            return {} if book else None
        return decide

    # position strategies
    def positions(self, sid: str) -> Decide:
        d = self.d
        book: list[Position] = []
        entry_signal = ENTRIES[sid]
        exit_rule = EXITS[sid]

        def decide(day: date, current: dict[str, float]) -> dict[str, float] | None:
            # a leg the engine force-closed (delisting) is gone
            book[:] = [p for p in book if p.symbol in current]     # the engine closed a delisted leg
            keep = []
            for p in book:
                if p.held(day) >= MAX_HOLD_DAYS or exit_rule(self, p, day) or stopped(self, sid, p, day):
                    continue
                keep.append(p)
            book[:] = keep
            if self.in_regime(sid, day) and len(book) < MAX_POSITIONS:
                taken = {p.symbol for p in book}
                found = []
                for symbol in ([BTC] if sid == "B05" else [ETH] if sid == "S07" else d.members(day)):
                    if symbol in taken:
                        continue
                    hit = entry_signal(self, symbol, day)
                    if hit is not None:
                        side, strength, hedge, info = hit
                        found.append((strength, symbol, side, hedge, info))
                found.sort(key=lambda f: (-f[0], f[1]))
                for strength, symbol, side, hedge, info in found[:MAX_POSITIONS - len(book)]:
                    book.append(Position(symbol, side, day, d.at(symbol, "atr14", day), hedge, info))
            return weights_of(book)
        return decide


def weights_of(book: Sequence[Position]) -> dict[str, float]:
    """The day's notional of 1 split evenly over the open positions; a pair puts half on each leg."""
    out: dict[str, float] = {}
    if not book:
        return out
    each = 1.0 / len(book)
    for p in book:
        if p.hedge is None:
            out[p.symbol] = out.get(p.symbol, 0.0) + p.side * each
        else:
            out[p.symbol] = out.get(p.symbol, 0.0) + p.side * each / 2
            out[p.hedge] = out.get(p.hedge, 0.0) - p.side * each / 2
    return out


def stopped(rules: Rules, sid: str, p: Position, day: date) -> bool:
    """The common stop (§2): 2.5 × ATR(14) from the entry open, on the close. A strategy whose exit is its own
    stop (B02, D02, D09 trail; S03 the box) uses that instead; pairs and B05's BTC have no common stop."""
    if sid in OWN_STOP or p.hedge is not None or p.atr is None or day <= p.signal_day:
        return False
    entry = rules.d.bar(p.symbol, p.signal_day + timedelta(days=1))
    close = rules.d.at(p.symbol, "close", day)
    if entry is None or close is None:
        return False
    return (close - entry.open) * p.side <= -STOP_ATR * p.atr


OWN_STOP = frozenset({"B02", "D02", "D09", "S03"})
Entry = Callable[[Rules, str, date], "tuple[int, float, str | None, dict[str, Any]] | None"]
Exit = Callable[[Rules, Position, date], bool]


def _v(r: Rules, s: str, key: str, day: date, back: int = 0) -> float | None:
    return r.d.at(s, key, day, back)


def _all(*values: Any) -> bool:
    return all(v is not None for v in values)


def _days(n: int) -> Exit:
    return lambda r, p, day: p.held(day) >= n


def _trail(r: Rules, p: Position, day: date) -> bool:
    """B02/D02/D09: 3 × ATR(14) behind the best close since entry, on the close."""
    close, atr = _v(r, p.symbol, "close", day), _v(r, p.symbol, "atr14", day)
    if not _all(close, atr):
        return False
    best = p.info.get("best", close)
    best = max(best, close) if p.side > 0 else min(best, close)
    p.info["best"] = best
    return (close - best) * p.side <= -TRAIL_ATR * atr


def _prior_high(r: Rules, s: str, day: date, n: int) -> float | None:
    w = r.d.window(s, "high", day, n, include_today=False)
    return max(w) if w else None


def _prior_low(r: Rules, s: str, day: date, n: int) -> float | None:
    w = r.d.window(s, "low", day, n, include_today=False)
    return min(w) if w else None


def _not_btc(s: str) -> bool:
    return s != BTC


# BULL
def e_b01(r, s, day):
    c, m, rsi = _v(r, s, "close", day), _v(r, s, "sma50", day), _v(r, s, "rsi14", day)
    return (1, 40 - rsi, None, {}) if _all(c, m, rsi) and c > m and rsi < 40 else None


def e_b02(r, s, day):
    c, hi, qv, avg = _v(r, s, "close", day), _prior_high(r, s, day, 20), _v(r, s, "qv", day), _v(r, s, "qv_avg20", day)
    return (1, c / hi - 1, None, {}) if _all(c, hi, qv, avg) and c > hi and qv > 1.5 * avg else None


def e_b04(r, s, day):
    if not _not_btc(s):
        return None
    mine, btc, c, m = r.d.ret(s, day, 14), r.d.ret(BTC, day, 14), _v(r, s, "close", day), _v(r, s, "sma20", day)
    return (1, mine - btc, None, {}) if _all(mine, btc, c, m) and mine - btc >= 0.10 and c > m else None


def e_b05(r, s, day):
    one = r.d.ret(BTC, day, 1)
    return (1, -one, None, {}) if one is not None and one < -0.05 else None


def e_b06(r, s, day):
    c, up = _v(r, s, "close", day), _v(r, s, "bb_up", day)
    pct = r.d.window(s, "bb_width_pct", day, 5, include_today=False)
    squeezed = pct is not None and any(v is not None and v < 0.20 for v in pct)
    return (1, c / up - 1, None, {}) if _all(c, up) and squeezed and c > up else None


def e_b08(r, s, day):
    c, highs = _v(r, s, "close", day), r.d.window(s, "high", day, 90, include_today=True)
    return (1, c / max(highs), None, {}) if c is not None and highs and c >= 0.95 * max(highs) else None


def e_b11(r, s, day):
    qv, avg, c, hi, lo = (_v(r, s, "qv", day), _v(r, s, "qv_avg30", day), _v(r, s, "close", day),
                          _v(r, s, "high", day), _v(r, s, "low", day))
    if not _all(qv, avg, c, hi, lo) or hi <= lo:
        return None
    return (1, qv / avg, None, {}) if qv > 3 * avg and (c - lo) / (hi - lo) >= 0.8 else None


def e_b12(r, s, day):
    f, sl, f0, sl0 = _v(r, s, "ema10", day), _v(r, s, "ema30", day), _v(r, s, "ema10", day, 1), _v(r, s, "ema30", day, 1)
    return (1, f / sl - 1, None, {}) if _all(f, sl, f0, sl0) and f0 <= sl0 and f > sl else None


def e_b10(r, s, day):
    if not _not_btc(s):
        return None
    ret7, f7 = r.d.ret(s, day, 7), r.d.funding_sum(s, day, 7)
    if not _all(ret7, f7) or ret7 < 0.50:
        return None
    pool = sorted(v for m in r.d.members(day) if (v := r.d.funding_sum(m, day, 7)) is not None)
    cut = pool[min(len(pool) - 1, int(math.ceil(0.9 * len(pool))) - 1)] if pool else None
    return (-1, ret7, BTC, {}) if cut is not None and f7 >= cut else None


# SIDEWAYS
def e_s01(r, s, day):
    c, up, lo = _v(r, s, "close", day), _v(r, s, "bb_up", day), _v(r, s, "bb_lo", day)
    if not _all(c, up, lo) or up <= lo:
        return None
    if c < lo:
        return 1, (lo - c) / (up - lo), None, {}
    if c > up:
        return -1, (c - up) / (up - lo), None, {}
    return None


def e_s02(r, s, day):
    v = _v(r, s, "rsi2", day)
    if v is None:
        return None
    return (1, 10 - v, None, {}) if v < 10 else (-1, v - 90, None, {}) if v > 90 else None


def e_s03(r, s, day):
    c, highs, lows = (_v(r, s, "close", day), r.d.window(s, "high", day, 30, include_today=True),
                      r.d.window(s, "low", day, 30, include_today=True))
    if c is None or not highs or not lows or max(highs) <= min(lows):
        return None
    hi, lo = max(highs), min(lows)
    where = (c - lo) / (hi - lo)
    box = {"hi": hi, "lo": lo}
    return (1, 0.1 - where, None, box) if where <= 0.10 else (-1, where - 0.9, None, box) if where >= 0.90 else None


def e_s05(r, s, day):
    z = funding_z(r, s, day)
    if z is None:
        return None
    return (-1, z, None, {}) if z > 2 else (1, -z, None, {}) if z < -2 else None


def funding_z(r: Rules, s: str, day: date) -> float | None:
    """z of the 3-day funding sum against its last 60 values (today's included)."""
    values = [r.d.funding_sum(s, day - timedelta(days=i), 3) for i in range(60)]
    if any(v is None for v in values):
        return None
    sd = statistics.stdev(values)
    return None if sd == 0 else (values[0] - statistics.fmean(values)) / sd


def ratio_z(r: Rules, s: str, other: str, day: date, n: int) -> float | None:
    values = []
    for i in range(n):
        a, b = _v(r, s, "close", day - timedelta(days=i)), _v(r, other, "close", day - timedelta(days=i))
        if not _all(a, b):
            return None
        values.append(a / b)
    sd = statistics.stdev(values)
    return None if sd == 0 else (values[0] - statistics.fmean(values)) / sd


def e_s06(r, s, day):
    if not _not_btc(s):
        return None
    z = ratio_z(r, s, BTC, day, 20)
    if z is None:
        return None
    return (1, -z, BTC, {"z": z}) if z < -2 else (-1, z, BTC, {"z": z}) if z > 2 else None


def e_s07(r, s, day):
    z = ratio_z(r, ETH, BTC, day, 30)
    if z is None:
        return None
    return (1, -z, BTC, {"z": z}) if z < -2 else (-1, z, BTC, {"z": z}) if z > 2 else None


def e_s08(r, s, day):
    c, prev = _v(r, s, "close", day), _v(r, s, "close", day, 1)
    yesterday = day - timedelta(days=1)
    hi, lo = _prior_high(r, s, yesterday, 20), _prior_low(r, s, yesterday, 20)
    if not _all(c, prev, hi, lo):
        return None
    if prev > hi and c <= hi:
        return -1, prev / hi - 1, None, {}
    if prev < lo and c >= lo:
        return 1, lo / prev - 1, None, {}
    return None


def e_s10(r, s, day):
    c, mid, atr = _v(r, s, "close", day), _v(r, s, "ema20", day), _v(r, s, "atr14", day)
    if not _all(c, mid, atr) or atr <= 0:
        return None
    dist = (c - mid) / atr
    return (-1, dist, None, {}) if dist > 2 else (1, -dist, None, {}) if dist < -2 else None


def e_s11(r, s, day):
    oi_now, oi_then = r.d.open_interest(s, day), r.d.open_interest(s, day - timedelta(days=3))
    move, f3 = r.d.ret(s, day, 3), r.d.funding_sum(s, day, 3)
    if not _all(oi_now, oi_then, move, f3) or oi_then <= 0 or f3 == 0:
        return None
    if oi_now / oi_then - 1 >= 0.20 and abs(move) <= 0.03:
        return (-1 if f3 > 0 else 1), oi_now / oi_then - 1, None, {}
    return None


def e_s12(r, s, day):
    c = [_v(r, s, "close", day, i) for i in range(4)]
    if not _all(*c):
        return None
    if c[0] < c[1] < c[2] < c[3]:
        return 1, c[3] / c[0] - 1, None, {}
    if c[0] > c[1] > c[2] > c[3]:
        return -1, c[0] / c[3] - 1, None, {}
    return None


# BEAR
def e_d01(r, s, day):
    c, m, rsi = _v(r, s, "close", day), _v(r, s, "sma50", day), _v(r, s, "rsi14", day)
    return (-1, rsi - 60, None, {}) if _all(c, m, rsi) and c < m and rsi > 60 else None


def e_d02(r, s, day):
    c, lo, qv, avg = _v(r, s, "close", day), _prior_low(r, s, day, 20), _v(r, s, "qv", day), _v(r, s, "qv_avg20", day)
    return (-1, lo / c - 1, None, {}) if _all(c, lo, qv, avg) and c < lo and qv > 1.5 * avg else None


def e_d04(r, s, day):
    if not _not_btc(s):
        return None
    mine, btc, c, m = r.d.ret(s, day, 14), r.d.ret(BTC, day, 14), _v(r, s, "close", day), _v(r, s, "sma20", day)
    return (-1, btc - mine, None, {}) if _all(mine, btc, c, m) and mine - btc <= -0.10 and c < m else None


def e_d05(r, s, day):
    one, sd, qv, avg = _v(r, s, "ret1", day), _v(r, s, "ret_sd60", day), _v(r, s, "qv", day), _v(r, s, "qv_avg30", day)
    if not _all(one, sd, qv, avg) or sd <= 0:
        return None
    return (1, -one / sd, None, {}) if one < -3 * sd and qv > 3 * avg else None


def e_d06(r, s, day):
    c, m, three = _v(r, s, "close", day), _v(r, s, "sma50", day), r.d.ret(s, day, 3)
    return (-1, three, None, {}) if _all(c, m, three) and c < m and three >= 0.15 else None


def e_d09(r, s, day):
    highs = r.d.window(s, "high", day, 40, include_today=True)
    c, swing = _v(r, s, "close", day), _prior_low(r, s, day, 20)
    if not highs or not _all(c, swing):
        return None
    lower_high = max(highs[20:]) < max(highs[:20])
    return (-1, swing / c - 1, None, {}) if lower_high and c < swing else None


def e_d10(r, s, day):
    pct, c, m = _v(r, s, "atr_pct", day), _v(r, s, "close", day), _v(r, s, "sma20", day)
    return (-1, pct, None, {}) if _all(pct, c, m) and pct > 0.80 and c < m else None


def e_d11(r, s, day):
    one = _v(r, s, "ret1", day)
    if one is None or one > -0.08:
        return None
    oi_now, oi_then = r.d.open_interest(s, day), r.d.open_interest(s, day - timedelta(days=1))
    if not _all(oi_now, oi_then) or oi_then <= 0:
        return None
    return (-1, -one, None, {}) if oi_now / oi_then - 1 <= -0.10 else None


def e_d12(r, s, day):
    hi, c, m = _v(r, s, "high", day), _v(r, s, "close", day), _v(r, s, "sma200", day)
    return (-1, (m - c) / m, None, {}) if _all(hi, c, m) and hi >= m and c < m else None


def x_rsi_above(level: float, days: int) -> Exit:
    return lambda r, p, day: p.held(day) >= days or ((v := _v(r, p.symbol, "rsi14", day)) is not None and v > level)


def x_rsi_below(level: float, days: int) -> Exit:
    return lambda r, p, day: p.held(day) >= days or ((v := _v(r, p.symbol, "rsi14", day)) is not None and v < level)


def x_close_vs(key: str, below: bool, days: int | None = None) -> Exit:
    def check(r: Rules, p: Position, day: date) -> bool:
        if days is not None and p.held(day) >= days:
            return True
        c, m = _v(r, p.symbol, "close", day), _v(r, p.symbol, key, day)
        return _all(c, m) and (c < m if below else c > m)
    return check


def x_middle_band(r: Rules, p: Position, day: date) -> bool:
    c, m = _v(r, p.symbol, "close", day), _v(r, p.symbol, "sma20", day)
    return _all(c, m) and (c >= m if p.side > 0 else c <= m)


def x_box(r: Rules, p: Position, day: date) -> bool:
    c = _v(r, p.symbol, "close", day)
    if c is None:
        return False
    hi, lo = p.info["hi"], p.info["lo"]
    mid = (hi + lo) / 2
    return (c >= mid or c < lo) if p.side > 0 else (c <= mid or c > hi)


def x_ema20(r: Rules, p: Position, day: date) -> bool:
    c, m = _v(r, p.symbol, "close", day), _v(r, p.symbol, "ema20", day)
    return _all(c, m) and (c >= m if p.side > 0 else c <= m)


def x_ema_cross_down(r: Rules, p: Position, day: date) -> bool:
    f, sl = _v(r, p.symbol, "ema10", day), _v(r, p.symbol, "ema30", day)
    return _all(f, sl) and f < sl


def x_z_cross(other_from: Callable[[Position], tuple[str, str, int]], days: int | None) -> Exit:
    def check(r: Rules, p: Position, day: date) -> bool:
        if days is not None and p.held(day) >= days:
            return True
        a, b, n = other_from(p)
        z = ratio_z(r, a, b, day, n)
        return z is not None and (z >= 0 if p.side > 0 else z <= 0)
    return check


ENTRIES: dict[str, Entry] = {
    "B01": e_b01, "B02": e_b02, "B04": e_b04, "B05": e_b05, "B06": e_b06, "B08": e_b08, "B10": e_b10,
    "B11": e_b11, "B12": e_b12,
    "S01": e_s01, "S02": e_s02, "S03": e_s03, "S05": e_s05, "S06": e_s06, "S07": e_s07, "S08": e_s08,
    "S10": e_s10, "S11": e_s11, "S12": e_s12,
    "D01": e_d01, "D02": e_d02, "D04": e_d04, "D05": e_d05, "D06": e_d06, "D09": e_d09, "D10": e_d10,
    "D11": e_d11, "D12": e_d12,
}
EXITS: dict[str, Exit] = {
    "B01": x_rsi_above(60, 10), "B02": _trail, "B04": x_close_vs("sma20", True, 14), "B05": _days(3),
    "B06": x_middle_band, "B08": x_close_vs("sma20", True), "B10": _days(3), "B11": _days(5),
    "B12": x_ema_cross_down,
    "S01": x_middle_band, "S02": _days(3), "S03": x_box, "S05": _days(3),
    "S06": x_z_cross(lambda p: (p.symbol, BTC, 20), 10), "S07": x_z_cross(lambda p: (ETH, BTC, 30), None),
    "S08": _days(3), "S10": x_ema20, "S11": _days(3), "S12": _days(2),
    "D01": x_rsi_below(40, 10), "D02": _trail, "D04": x_close_vs("sma20", False, 14), "D05": _days(2),
    "D06": _days(5), "D09": _trail, "D10": _days(5), "D11": _days(2), "D12": x_close_vs("sma200", False, 10),
}
_BASKETS = {"B03": BULL, "B07": BULL, "S04": SIDEWAYS, "S09": SIDEWAYS, "D03": BEAR, "D07": BEAR}
_ROTATIONS = {"B09": BULL, "D08": BEAR}
_NEEDS = {"B07": ("funding",), "B10": ("funding",), "S05": ("funding",), "S09": ("funding",),
          "S11": ("funding", "oi"), "D07": ("funding",), "D11": ("oi",)}
STRATEGIES: dict[str, Strategy] = {
    **{sid: Strategy(sid, reg, "basket", _NEEDS.get(sid, ())) for sid, reg in _BASKETS.items()},
    **{sid: Strategy(sid, reg, "rotation") for sid, reg in _ROTATIONS.items()},
    **{sid: Strategy(sid, {"B": BULL, "S": SIDEWAYS, "D": BEAR}[sid[0]], "position", _NEEDS.get(sid, ()))
       for sid in ENTRIES},
}
STRATEGIES = dict(sorted(STRATEGIES.items(), key=lambda kv: ("BSD".index(kv[0][0]), kv[0])))


def decider(rules: Rules, sid: str) -> Decide:
    kind = STRATEGIES[sid].kind
    return rules.basket(sid) if kind == "basket" else rules.rotation(sid) if kind == "rotation" \
        else rules.positions(sid)


# --- the random control ----------------------------------------------------------------------------------------

def random_twin(data: Data, path: Mapping[date, Mapping[str, float]], sid: str, seed: str) -> Decide:
    """§4: the same days, legs, holding spans, weights and directions; each leg's symbol is drawn from
    ``sha256(seed|sid|entry day|symbol)`` among the entry day's members. The direction is the strategy's own
    (amendment 2026-11-04), so the twin carries the same market exposure and the gap is the symbol choice."""
    episodes: dict[tuple[str, date], str] = {}
    out: dict[date, dict[str, float]] = {}
    running: dict[str, date] = {}
    for day in sorted(path):
        weights = path[day]
        for symbol in list(running):
            if symbol not in weights:
                running.pop(symbol)
        target: dict[str, float] = {}
        for symbol, w in weights.items():
            if symbol not in running:
                running[symbol] = day
            start = running[symbol]
            key = (symbol, start)
            if key not in episodes:
                members = data.members(start) or [symbol]
                h = hashlib.sha256(f"{seed}|{sid}|{start.isoformat()}|{symbol}".encode()).digest()
                episodes[key] = members[h[0] % len(members)]
            other = episodes[key]
            target[other] = target.get(other, 0.0) + w
        out[day] = target

    def decide(day: date, book: dict[str, float]) -> dict[str, float] | None:
        return dict(out.get(day, {}))
    return decide


def recording(decide: Decide, path: dict[date, dict[str, float]]) -> Decide:
    def wrapped(day: date, book: dict[str, float]) -> dict[str, float] | None:
        target = decide(day, book)
        path[day] = dict(book if target is None else {s: w for s, w in target.items() if w})
        return target
    return wrapped


# --- the reading -------------------------------------------------------------------------------------------------

def regime_weeks(labels: Mapping[date, str], spans: Sequence[tuple[date, date]]) -> dict[date, str]:
    """The majority label of each week whose Monday lies in a span, over the week's days inside that span."""
    out: dict[date, str] = {}
    for a, b in spans:
        day = a
        while day <= b:
            monday = xs.week_of(day)
            if a <= monday <= b and monday not in out:
                inside = [labels.get(monday + timedelta(days=i)) for i in range(7)
                          if monday + timedelta(days=i) <= b]
                inside = [x for x in inside if x is not None]
                for regime in REGIMES:
                    if inside and inside.count(regime) * 2 > len(inside):
                        out[monday] = regime
            day += timedelta(days=1)
    return out


def measure_strategy(data: Data, rules_for: Callable[[], Rules], sid: str, spans: Sequence[tuple[date, date]],
                     weeks: Mapping[date, str], seeds: int = RANDOM_SEEDS) -> dict[str, Any]:
    regime = STRATEGIES[sid].regime
    path: dict[date, dict[str, float]] = {}
    daily: dict[date, dict[str, float]] = {}
    forced = 0
    for a, b in spans:
        result = run(data, recording(decider(rules_for(), sid), path), a, b)
        daily.update(result["daily"])
        forced += result["forced_closes"]
    mine = [w for w, r in weeks.items() if r == regime]
    by_week = xs.weekly(daily) if daily else {}
    values = [by_week.get(w, 0.0) for w in mine]
    trades = sum(int(row["entries"]) for day, row in daily.items() if weeks.get(xs.week_of(day)) == regime)
    tail = sum(v for w, v in by_week.items() if w in weeks and weeks[w] != regime)
    random_means = []
    for k in range(seeds):
        twin: dict[date, dict[str, float]] = {}
        for a, b in spans:
            twin.update(run(data, random_twin(data, path, sid, f"{SPEC}/random/{k}"), a, b)["daily"])
        tw = xs.weekly(twin) if twin else {}
        random_means.append(statistics.fmean([tw.get(w, 0.0) for w in mine]) if mine else 0.0)
    stats = t_stats(values)
    random_mean = statistics.fmean(random_means) if random_means else 0.0
    years: dict[int, float] = {}
    for w in mine:
        years[w.year] = years.get(w.year, 0.0) + by_week.get(w, 0.0)
    return {"regime": regime, "kind": STRATEGIES[sid].kind, **stats, "trades": trades,
            "random_mean_bps": round(random_mean * 1e4, 3),
            "vs_random_bps": None if stats["mean_bps"] is None else round(stats["mean_bps"] - random_mean * 1e4, 3),
            "years": {str(y): round(v * 1e4, 3) for y, v in sorted(years.items())},
            "out_of_regime_tail_bps": round(tail * 1e4, 3), "forced_closes": forced}


def t_stats(values: Sequence[float]) -> dict[str, Any]:
    n = len(values)
    if n < 2:
        return {"weeks": n, "mean_bps": None, "t": None, "p_one_sided": None}
    mean, sd = statistics.fmean(values), statistics.stdev(values)
    t = mean / (sd / math.sqrt(n)) if sd > 0 else None
    p = None if t is None else 0.5 * math.erfc(t / math.sqrt(2))
    return {"weeks": n, "mean_bps": round(mean * 1e4, 3), "t": t, "p_one_sided": p}


def screens(row: Mapping[str, Any]) -> bool:
    return row["mean_bps"] is not None and row["mean_bps"] > 0 and row["t"] is not None \
        and row["t"] >= SCREEN_MIN_T and row["trades"] >= SCREEN_MIN_TRADES and row["vs_random_bps"] > 0


def candidates(rows: Mapping[str, Mapping[str, Any]]) -> dict[str, list[str]]:
    out = {}
    for regime in REGIMES:
        passing = [(sid, r) for sid, r in rows.items() if r["regime"] == regime and screens(r)]
        out[regime] = [sid for sid, _ in sorted(passing, key=lambda sr: (-sr[1]["t"], sr[0]))[:SCREEN_KEEP]]
    return out


def judgeable(row: Mapping[str, Any]) -> bool:
    return row["weeks"] >= FLOOR_WEEKS and row["trades"] >= FLOOR_TRADES and row["p_one_sided"] is not None


def family(rows: Mapping[str, Mapping[str, Any]]) -> dict[str, float]:
    """Holm family: every judgeable candidate's one-sided p, plus the prior p values of the tested hypotheses a
    judgeable candidate derives from (each prior once). A NOT_JUDGEABLE candidate is no test, so it brings none."""
    out = {sid: r["p_one_sided"] for sid, r in rows.items() if judgeable(r)}
    for sid in list(out):
        out.update(PRIOR_P.get(PARENT.get(sid, ""), {}))
    return out


def confirm(rows: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    fam = family(rows)
    names = list(fam)
    passed = {names[i] for i in xs.holm([fam[n] for n in names], HOLM_ALPHA)}
    out = {}
    for sid, r in rows.items():
        if not judgeable(r):
            out[sid] = "NOT_JUDGEABLE"
            continue
        years = list(r["years"].values())
        ok = sid in passed and r["mean_bps"] > 0 and r["vs_random_bps"] > 0 \
            and sum(v > 0 for v in years) * 2 > len(years)
        out[sid] = "CONFIRMED" if ok else "NOT_CONFIRMED"
    return out


# --- open interest from the metrics files --------------------------------------------------------------------

def read_oi(root: Path, symbols: Sequence[str]) -> dict[str, dict[date, float]]:
    """The day's open interest = ``sum_open_interest`` on the day file's row with the latest stamp (23:55 UTC).

    Some day files are not in time order, so the file's last row can be a mid-day reading. Of rows sharing the
    latest stamp the later one in the file is read, which is the last row whenever the file is in order."""
    out: dict[str, dict[date, float]] = {}
    for symbol in symbols:
        folder = root / METRICS_DIR / symbol
        days: dict[date, float] = {}
        for path in sorted(folder.glob("*.zip")) if folder.exists() else []:
            try:
                with zipfile.ZipFile(path) as z:
                    rows = list(csv.reader(io.StringIO(z.read(z.namelist()[0]).decode("utf-8"))))
                last = sorted((row for row in rows[1:] if len(row) > 2), key=lambda row: row[0])[-1]   # stable
                days[date.fromisoformat(path.stem[-10:])] = float(last[2])
            except (OSError, ValueError, IndexError, zipfile.BadZipFile):
                continue
        if days:
            out[symbol] = days
    return out


def oi_plan(monthly: Mapping[date, Sequence[str]], start: date = WINDOW_START,
            end: date = WINDOW_END) -> list[tuple[str, date, date]]:
    """The metrics days S11 and D11 can read: each member's months from 4 days before the month, merged into
    ``(symbol, first, last)`` spans for ``binance_archive.py fetch``."""
    days: dict[str, set[date]] = {}
    for month, members in monthly.items():
        if not start <= month <= end:
            continue
        nxt = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
        for symbol in members:
            day = month - timedelta(days=4)
            while day < nxt and day <= end:
                days.setdefault(symbol, set()).add(day)
                day += timedelta(days=1)
    out = []
    for symbol in sorted(days):
        run_start = prev = None
        for day in sorted(days[symbol]):
            if prev is None or day != prev + timedelta(days=1):
                if prev is not None:
                    out.append((symbol, run_start, prev))
                run_start = day
            prev = day
        out.append((symbol, run_start, prev))
    return out


# --- the command line ---------------------------------------------------------------------------------------------

def _cache() -> Path:
    return Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))


def load(archive: Path, metrics: Path) -> tuple[Data, dict[str, Any]]:
    market = xs.load_market(archive, funding=True)
    snapshot = xs.read_contract_types(archive)
    if snapshot is None:
        raise FileNotFoundError(xs.CONTRACT_TYPES_FILENAME)
    market.contract_types, _ = snapshot
    monthly = xs.universes(market, top_n=TOP_N, start=LABEL_START, end=WINDOW_END)
    members = sorted({s for v in monthly.values() for s in v})
    data = Data(market, monthly, read_oi(metrics, members))
    labels = regime_labels(data)
    return data, labels


def code_digest() -> str:
    """This module's own bytes, so stage 2 runs the code stage 1 ran (a changed rule means stage 1 again)."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


def provenance(stage: int) -> dict[str, Any]:
    out = xs.provenance()
    return {"spec": SPEC, "stage": stage, "measured_at": timeutil.utc_now_iso(), **out, "code_digest": code_digest(),
            "holdout_quarters": [f"{y}Q{q}" for y, q in holdout_quarters()],
            "rules": {"confirm_days": CONFIRM_DAYS, "embargo_days": EMBARGO_DAYS, "max_positions": MAX_POSITIONS,
                      "stop_atr": STOP_ATR, "trail_atr": TRAIL_ATR, "max_hold_days": MAX_HOLD_DAYS,
                      "screen": [SCREEN_MIN_T, SCREEN_MIN_TRADES, SCREEN_KEEP],
                      "floor": [FLOOR_WEEKS, FLOOR_TRADES], "holm_alpha": HOLM_ALPHA, "seeds": RANDOM_SEEDS}}


def stage_one(data: Data, labels: Mapping[date, str], *, seeds: int = RANDOM_SEEDS,
              only: Sequence[str] | None = None) -> dict[str, Any]:
    spans = segments()["screen"]
    weeks = regime_weeks(labels, spans)
    rows = {sid: measure_strategy(data, lambda: Rules(data, labels), sid, spans, weeks, seeds)
            for sid in (only or STRATEGIES)}
    return {"weeks_by_regime": {r: sum(1 for v in weeks.values() if v == r) for r in REGIMES},
            "strategies": rows, "candidates": candidates(rows), "oi_reads": dict(data.oi_reads)}


def stage_two(data: Data, labels: Mapping[date, str], picked: Mapping[str, Sequence[str]], *,
              seeds: int = RANDOM_SEEDS) -> dict[str, Any]:
    spans = segments()["holdout"]
    weeks = regime_weeks(labels, spans)
    sids = [sid for regime in REGIMES for sid in picked.get(regime, ())]
    rows = {sid: measure_strategy(data, lambda: Rules(data, labels), sid, spans, weeks, seeds) for sid in sids}
    verdicts = confirm(rows)
    for sid in rows:
        rows[sid]["verdict"] = verdicts[sid]
    return {"weeks_by_regime": {r: sum(1 for v in weeks.values() if v == r) for r in REGIMES},
            "holm_family": family(rows), "strategies": rows, "oi_reads": dict(data.oi_reads)}


def read_stage_one(path: Path) -> dict[str, list[str]] | None:
    """The candidates a stage-1 record names, or None when it is not this test's stage-1 record."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("spec") != SPEC or record.get("stage") != 1 \
            or record.get("code_digest") != code_digest() \
            or record.get("holdout_quarters") != [f"{y}Q{q}" for y, q in holdout_quarters()]:
        return None
    picked = record.get("candidates")
    if not isinstance(picked, dict) or set(picked) != set(REGIMES):
        return None
    for regime, sids in picked.items():
        if not isinstance(sids, list) or len(sids) > SCREEN_KEEP \
                or any(sid not in STRATEGIES or STRATEGIES[sid].regime != regime for sid in sids):
            return None
    return {r: list(v) for r, v in picked.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="regime_strategies", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("regimes", "oi-plan", "stage1", "stage2"):
        p = sub.add_parser(name)
        p.add_argument("--archive", type=Path, default=None, help="default: $RESEARCH_CACHE/archive")
        p.add_argument("--metrics", type=Path, default=None, help="default: $RESEARCH_CACHE/archive-metrics")
    sub.choices["stage2"].add_argument("--stage1", type=Path, required=True,
                                       help="the stage-1 record (its JSON output) naming the candidates")
    args = parser.parse_args(argv)
    if args.command not in ("regimes", "oi-plan") and not xs._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    picked = None
    if args.command == "stage2":
        picked = read_stage_one(args.stage1)
        if picked is None:
            print(f"BLOCKED: {args.stage1} is not a stage-1 record of {SPEC} run by this code "
                  f"(code_digest {code_digest()})", file=sys.stderr)
            return EXIT_BLOCKED
    archive = args.archive or _cache() / "archive"
    metrics = args.metrics or _cache() / "archive-metrics"
    if args.command == "oi-plan":
        market = xs.load_market(archive, funding=False)
        snapshot = xs.read_contract_types(archive)
        if snapshot is None:
            print(f"BLOCKED: no {xs.CONTRACT_TYPES_FILENAME} under {archive}", file=sys.stderr)
            return EXIT_BLOCKED
        market.contract_types, _ = snapshot
        for symbol, a, b in oi_plan(xs.universes(market, top_n=TOP_N, start=WINDOW_START, end=WINDOW_END)):
            print(symbol, a.isoformat(), b.isoformat())
        return EXIT_OK
    try:
        data, labelled = load(archive, metrics)
    except FileNotFoundError as exc:
        print(f"BLOCKED: no {exc} under {archive}", file=sys.stderr)
        return EXIT_BLOCKED
    labels = labelled["labels"]
    split = segments()
    head = {"labels": {**label_record(labels), "lagged_days": labelled["lagged_days"]},
            "segments": {k: [[a.isoformat(), b.isoformat()] for a, b in v] for k, v in split.items()}}
    if args.command == "regimes":
        print(json.dumps({"spec": SPEC, "holdout_quarters": [f"{y}Q{q}" for y, q in holdout_quarters()], **head},
                         indent=1, sort_keys=True))
        return EXIT_OK
    body = stage_one(data, labels) if args.command == "stage1" else stage_two(data, labels, picked)
    print(json.dumps({**provenance(1 if args.command == "stage1" else 2), **head, **body}, indent=1,
                     sort_keys=True, default=str))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
