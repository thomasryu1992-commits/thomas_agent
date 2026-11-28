"""L30 CTREND-like trend composite (STRATEGY_DISCOVERY_LOOP section 116; Thomas 2026-11-20 "기본값대로").

After Fieberg, Liedtke, Poddig, Walker and Zaremba, "A Trend Factor for the Cross Section of Cryptocurrency
Returns" (JFQA 2025): 28 technical indicators, each a weekly cross-sectional rank in [-0.5, 0.5]; per indicator a
univariate cross-sectional regression of the next week's demeaned return, its coefficients averaged over a rolling
52 weeks (the indicator's forecast); an elastic net (L1 share 0.5, lambda by the corrected AIC) over the pooled
forecasts; CTREND is the plain mean of the forecasts the net keeps with a positive weight.

The departures from the paper are fixed in section 116.2: quote volume where the paper uses market cap (regression
weights: the trailing 30-day quote volume), Binance perps ranked by the month's trailing volume (the month's top 30),
standard indicator formulas (the paper's appendix A was not read), a missing indicator at the middle rank (0), the
record's top-30 weekly book (L9T on the forecast, long the high, held weekly, the engine's costs), 2021-2024.

Commands in the registered order, each refusing without the one before it: ``ctrend-counts`` (no return of any book;
stops when the first forecast is after 2021-06-30), ``ctrend-overlap --market bars|positioning|depth`` (the real
book's weights beside every struck top-30 book that market loads, and S-MOM30's basket; no profit and loss),
``ctrend-time`` (one null pipeline timed; sets N), ``ctrend-null`` (the whole pipeline on 28 fixed per-coin random
inputs), ``ctrend-plant`` (one of them planted), ``ctrend-book`` (the real book, once).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
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
from scripts.research import momentum_basket_screen as mbs  # noqa: E402
from scripts.research import search_prune as sp  # noqa: E402
from scripts.research import signal_lab as lab  # noqa: E402
from scripts.research import strategy_ladder as sl  # noqa: E402

SPEC = "STRATEGY_DISCOVERY_LOOP_V0.1 section 116 (L30)"
SPAN = lab.SPAN                                   # 2020-01-01 .. 2024-12-31: the forecasts' history starts here
BOOK_SPAN = (date(2021, 1, 1), date(2024, 12, 31))  # the 48 months read
TIER = "top30"
HORIZON = 7
WINDOW_WEEKS = 52
MIN_WEEKS = 26                                    # valid weeks a forecast's window needs
MIN_COINS = 20                                    # coins with a target a week needs to enter the regressions
LAST_FIRST_FORECAST = date(2021, 6, 30)           # counts stop: a first forecast after this leaves too short a book
L1_SHARE = 0.5
LAMBDAS = 50
LAMBDA_RATIO = 1e-4
CD_SWEEPS = 200
CD_TOL = 1e-7
WEIGHT_DAYS = 30                                  # the regressions' weight: trailing quote volume over these days
NULL_CAP = 200
NULL_FLOOR = 100                                  # under it the timed N stops and asks (section 116.4)
NULL_HOURS = 6.0
NULL_SEED = "ctrend-null"
PLANT_SEED = "ctrend-plant"
PLANT_RHOS = (0.0, 0.05, 0.10, 0.20)
PLANT_SEEDS = 30
PLANT_FALSE_ALARMS = 3                            # more of the 30 size-0 seeds at p <= ALPHA voids the plant reading
PLANT_SEES = 0.80
ALPHA = 0.05
OVERLAP_CLOSE = sl.LOWER_OVERLAP_CLOSE            # 0.5, on the median's size
OVERLAP_MARKETS = ("bars", "positioning", "depth")
OVERLAP_GROUPS = {"bars": frozenset({"price_volume", "coin_character", "trade_flow"}),
                  "positioning": frozenset({"positioning"}), "depth": frozenset({"depth"})}
SMA_DAYS = (3, 5, 10, 20, 50, 100, 200)


# --- the indicators ------------------------------------------------------------------------------------------------

INDICATORS = (("rsi",), ("stoch_k",), ("stoch_d",), ("stoch_rsi",), ("cci",)) \
    + tuple((f"sma_{n}",) for n in SMA_DAYS) + (("macd",), ("macd_diff_signal",)) \
    + tuple((f"volsma_{n}",) for n in SMA_DAYS) + (("volmacd",), ("volmacd_diff_signal",), ("chaikin",)) \
    + (("boll_low",), ("boll_mid",), ("boll_high",), ("boll_width",))
NAMES = tuple(i[0] for i in INDICATORS)


def _ema(values: Sequence[float], span: int) -> list[float]:
    alpha, out = 2 / (span + 1), []
    for i, v in enumerate(values):
        out.append(v if i == 0 else alpha * v + (1 - alpha) * out[-1])
    return out


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def indicator_series(bars: Mapping[date, xsb.Bar]) -> dict[date, dict[str, float]]:
    """Every day's 28 values from that day's close and before, over the coin's consecutive bars. A value needs its
    whole window; a ratio with a zero or missing denominator has none. Formulas: RSI 14 (Cutler, simple means), fast
    and slow stochastics 14 / 3, stochastic RSI 14, CCI 20 (typical price, mean deviation, 0.015), SMA / close, MACD
    as (EMA12 - EMA26) / EMA12 and its gap to a 9-day EMA, volume SMA / today's volume, the same MACD on volume,
    Chaikin money flow 20, Bollinger 20 / 2 sd: (SMA -/+ 2 sd) / close, SMA / close, 4 sd / SMA."""
    days = sorted(bars)
    out: dict[date, dict[str, float]] = {d: {} for d in days}
    closes = [bars[d].close for d in days]
    vols = [bars[d].quote_volume for d in days]
    highs = [bars[d].high for d in days]
    lows = [bars[d].low for d in days]
    ema12, ema26 = _ema(closes, 12), _ema(closes, 26)
    macd = [(a - b) / a if a else 0.0 for a, b in zip(ema12, ema26)]
    macd_sig = _ema(macd, 9)
    vema12, vema26 = _ema(vols, 12), _ema(vols, 26)
    vmacd = [(a - b) / a if a else 0.0 for a, b in zip(vema12, vema26)]
    vmacd_sig = _ema(vmacd, 9)
    rsi: list[float | None] = []
    for i, d in enumerate(days):
        row = out[d]
        c, v = closes[i], vols[i]
        if i >= 14:
            moves = [closes[k] - closes[k - 1] for k in range(i - 13, i + 1)]
            gain, loss = sum(m for m in moves if m > 0) / 14, sum(-m for m in moves if m < 0) / 14
            rsi.append(100.0 if loss == 0 else 100 - 100 / (1 + gain / loss))
        else:
            rsi.append(None)
        if rsi[-1] is not None:
            row["rsi"] = rsi[-1]
            window = [r for r in rsi[-14:] if r is not None]
            if len(window) == 14 and max(window) > min(window):
                row["stoch_rsi"] = (rsi[-1] - min(window)) / (max(window) - min(window))
        hl = highs[max(0, i - 13):i + 1], lows[max(0, i - 13):i + 1]
        if i >= 13 and all(h is not None for h in hl[0]) and all(x is not None for x in hl[1]):
            hi, lo = max(hl[0]), min(hl[1])
            if hi > lo:
                row["stoch_k"] = (c - lo) / (hi - lo)
        ks = [out[days[k]].get("stoch_k") for k in range(max(0, i - 2), i + 1)]
        if i >= 2 and all(k is not None for k in ks):
            row["stoch_d"] = _mean(ks)
        if i >= 19 and all(h is not None for h in highs[i - 19:i + 1]) and all(x is not None for x in lows[i - 19:i + 1]):
            tp = [(highs[k] + lows[k] + closes[k]) / 3 for k in range(i - 19, i + 1)]
            m = _mean(tp)
            dev = _mean([abs(t - m) for t in tp])
            if dev > 0:
                row["cci"] = (tp[-1] - m) / (0.015 * dev)
            mfv = [((closes[k] - lows[k]) - (highs[k] - closes[k])) / (highs[k] - lows[k]) * vols[k]
                   if highs[k] > lows[k] else 0.0 for k in range(i - 19, i + 1)]
            vsum = sum(vols[i - 19:i + 1])
            if vsum > 0:
                row["chaikin"] = sum(mfv) / vsum
        for n in SMA_DAYS:
            if i >= n - 1:
                if c > 0:
                    row[f"sma_{n}"] = _mean(closes[i - n + 1:i + 1]) / c
                if v > 0:
                    row[f"volsma_{n}"] = _mean(vols[i - n + 1:i + 1]) / v
        if i >= 25:
            row["macd"], row["macd_diff_signal"] = macd[i], macd[i] - macd_sig[i]
            row["volmacd"], row["volmacd_diff_signal"] = vmacd[i], vmacd[i] - vmacd_sig[i]
        if i >= 19 and c > 0:
            w = closes[i - 19:i + 1]
            m, sd = _mean(w), statistics.pstdev(w)
            row["boll_low"], row["boll_mid"], row["boll_high"] = (m - 2 * sd) / c, m / c, (m + 2 * sd) / c
            if m > 0:
                row["boll_width"] = 4 * sd / m
    return out


def runs(bars: Mapping[date, xsb.Bar]) -> list[dict[date, xsb.Bar]]:
    """The coin's bars cut where a day is missing: an indicator never spans a gap."""
    out: list[dict[date, xsb.Bar]] = []
    previous = None
    for d in sorted(bars):
        if previous is None or d - previous != timedelta(days=1):
            out.append({})
        out[-1][d] = bars[d]
        previous = d
    return out


class Indicators:
    """The 28 values per coin on the decision Sundays, computed once per coin on first use (only the Sundays are
    kept: every day of every coin would not fit the sandbox)."""

    def __init__(self, market: xsb.Market):
        self.market, self.cache = market, {}

    def __call__(self, symbol: str, day: date) -> dict[str, float]:
        if symbol not in self.cache:
            series: dict[date, dict[str, float]] = {}
            for run in runs(self.market.bars.get(symbol) or {}):
                series.update({d: v for d, v in indicator_series(run).items() if d.weekday() == 6})
            self.cache[symbol] = series
        return self.cache[symbol].get(day, {})


def real_inputs(market: xsb.Market) -> dict[str, lab.Variable]:
    table = Indicators(market)
    return {name: (lambda m, s, d, name=name: table(s, d).get(name)) for name in NAMES}


def null_inputs(k: int, space: str = NULL_SEED) -> dict[str, lab.Variable]:
    """Seed ``k``'s 28 random inputs: one draw per coin fixed over the span (``search_prune.noise_variable``)."""
    return {f"{space}_{k:03d}_{i:02d}": sp.noise_variable(TIER, k, i, "fixed", space) for i in range(len(NAMES))}


# --- the weekly table ----------------------------------------------------------------------------------------------

def centred_ranks(values: Mapping[str, float]) -> dict[str, float]:
    """Cross-sectional ranks in [-0.5, 0.5] (ties share the mean rank); one coin alone sits at 0."""
    order = sorted(values, key=lambda s: (values[s], s))
    n, out, i = len(order), {}, 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = 0.0 if n == 1 else ((i + j) / 2) / (n - 1) - 0.5
        i = j + 1
    return out


def weight(market: xsb.Market, symbol: str, day: date) -> float:
    bars = market.bars.get(symbol) or {}
    return sum(b.quote_volume for d in (day - timedelta(days=k) for k in range(WEIGHT_DAYS)) if (b := bars.get(d)))


class Week:
    """One decision: the members with a bar, their 28 ranks (0 where missing), weights and demeaned targets."""

    def __init__(self, day: date, rows: list[tuple[str, tuple[float, ...], float, float | None]],
                 present: Sequence[int] = ()):
        self.day, self.rows = day, rows
        self.present = tuple(present)              # per input, the members with a value read (before ranking)
        self.valid = sum(1 for r in rows if r[3] is not None) >= MIN_COINS
        self._stats: Stats | None = None

    def stats(self) -> "Stats":
        """The week's weighted sums over its rows with a target and a weight (cached)."""
        if self._stats is None:
            self._stats = Stats.of([(x, w, y) for _s, x, w, y in self.rows if y is not None and w > 0])
        return self._stats


class Stats:
    """Weighted sums of the 28 ranks and the target: enough to rebuild any window's Gram matrix."""

    def __init__(self, p: int):
        self.n, self.sw, self.sy, self.syy = 0, 0.0, 0.0, 0.0
        self.sx, self.sxy = [0.0] * p, [0.0] * p
        self.sxx = [[0.0] * p for _ in range(p)]

    @classmethod
    def of(cls, rows: Sequence[tuple[Sequence[float], float, float]]) -> "Stats":
        p = len(rows[0][0]) if rows else len(NAMES)
        out = cls(p)
        for x, w, y in rows:
            out.n += 1
            out.sw += w
            out.sy += w * y
            out.syy += w * y * y
            for a in range(p):
                wa = w * x[a]
                out.sx[a] += wa
                out.sxy[a] += wa * y
                row = out.sxx[a]
                for b in range(a, p):
                    row[b] += wa * x[b]
        return out

    def add(self, other: "Stats") -> None:
        p = len(self.sx)
        self.n += other.n
        self.sw += other.sw
        self.sy += other.sy
        self.syy += other.syy
        for a in range(p):
            self.sx[a] += other.sx[a]
            self.sxy[a] += other.sxy[a]
            for b in range(a, p):
                self.sxx[a][b] += other.sxx[a][b]


def weeks(market: xsb.Market, reads: Mapping[str, lab.Variable],
          members: Mapping[date, Sequence[str]]) -> dict[date, Week]:
    names = list(reads)                              # the order given: NAMES for the real inputs
    out = {}
    for day in sl.sundays(SPAN):
        if not SPAN[0] <= day <= SPAN[1]:
            continue
        coins = [s for s in members.get(date(day.year, day.month, 1), ()) if day in (market.bars.get(s) or {})]
        ranks, present = [], []
        for name in names:
            values = {s: v for s in coins if (v := reads[name](market, s, day)) is not None}
            ranks.append(centred_ranks(values))
            present.append(len(values))
        raw = {s: r for s in coins if (r := lab.forward_return(market, s, day, HORIZON)) is not None}
        mean = statistics.fmean(raw.values()) if raw else 0.0
        out[day] = Week(day, [(s, tuple(r.get(s, 0.0) for r in ranks), weight(market, s, day),
                               raw[s] - mean if s in raw else None) for s in sorted(coins)], present)
    return out


# --- the model -----------------------------------------------------------------------------------------------------

def univariate(week: Week, j: int) -> tuple[float, float] | None:
    """The weighted cross-sectional regression of the demeaned target on indicator ``j``: (intercept, slope)."""
    rows = [(x[j], w, y) for _s, x, w, y in week.rows if y is not None and w > 0]
    sw = sum(w for _x, w, _y in rows)
    if sw <= 0:
        return None
    mx = sum(w * x for x, w, _y in rows) / sw
    my = sum(w * y for _x, w, y in rows) / sw
    sxx = sum(w * (x - mx) ** 2 for x, w, _y in rows)
    if sxx <= 0:
        return None
    b = sum(w * (x - mx) * (y - my) for x, w, y in rows) / sxx
    return my - b * mx, b


def window(table: Mapping[date, Week], day: date) -> list[Week]:
    """The valid weeks whose target window has closed by ``day`` (decision + 8 days <= day), the last 52 of them."""
    usable = [w for d, w in sorted(table.items()) if w.valid and d + timedelta(days=1 + HORIZON) <= day
              and d > day - timedelta(days=1 + HORIZON + 7 * WINDOW_WEEKS)]
    return usable[-WINDOW_WEEKS:]


def standardised(stats: Stats, signs: Sequence[int]) -> tuple[list[int], list[list[float]], list[float], float, int]:
    """The weighted-centred, standardised Gram matrix and correlations of the forecast columns. A forecast column is
    ``a_j + b_j * rank_j``: centred and scaled it is ``sign(b_j)`` times the standardised rank, so the window's rank
    sums are enough (columns with ``b_j = 0`` or no spread are left out)."""
    p, sw = len(stats.sx), stats.sw
    if sw <= 0:
        return [], [], [], 0.0, 0
    mx = [v / sw for v in stats.sx]
    my = stats.sy / sw
    cov = [[0.0] * p for _ in range(p)]
    for a in range(p):
        for b in range(a, p):
            cov[a][b] = cov[b][a] = stats.sxx[a][b] / sw - mx[a] * mx[b]
    sd = [math.sqrt(max(cov[a][a], 0.0)) for a in range(p)]
    keep = [a for a in range(p) if sd[a] > 1e-12 and signs[a] != 0]
    gram = [[signs[a] * signs[b] * cov[a][b] / (sd[a] * sd[b]) for b in keep] for a in keep]
    corr = [signs[a] * (stats.sxy[a] / sw - mx[a] * my) / sd[a] for a in keep]
    vy = stats.syy / sw - my * my
    return keep, gram, corr, vy, stats.n


def elastic_net_positive(keep: Sequence[int], gram: Sequence[Sequence[float]], corr: Sequence[float], vy: float,
                         n: int) -> list[int]:
    """The columns (indices into ``keep``'s source) whose elastic-net weight is positive at the lambda the
    corrected AIC picks, on standardised columns (:func:`standardised`); lambda runs over :data:`LAMBDAS` values from
    the smallest that keeps none down to :data:`LAMBDA_RATIO` of it; coordinate descent, warm-started."""
    q = len(keep)
    if q == 0 or n == 0 or vy <= 0:
        return []
    top = max(abs(c) for c in corr) / L1_SHARE
    if top <= 0 or vy <= 0:
        return []
    best, best_beta = None, [0.0] * q
    beta = [0.0] * q
    for step in range(LAMBDAS):
        lam = top * LAMBDA_RATIO ** (step / (LAMBDAS - 1))
        for _sweep in range(CD_SWEEPS):
            moved = 0.0
            for a in range(q):
                row = gram[a]
                rho = corr[a] - sum(row[b] * beta[b] for b in range(q) if beta[b] != 0.0 and b != a)
                shrunk = math.copysign(max(abs(rho) - lam * L1_SHARE, 0.0), rho)
                new = shrunk / (gram[a][a] + lam * (1 - L1_SHARE))
                moved = max(moved, abs(new - beta[a]))
                beta[a] = new
            if moved < CD_TOL:
                break
        mse = vy - 2 * sum(beta[a] * corr[a] for a in range(q)) \
            + sum(beta[a] * gram[a][b] * beta[b] for a in range(q) for b in range(q))
        df = sum(1 for v in beta if v != 0)
        if mse <= 0 or n - df - 1 <= 0:
            continue
        aicc = n * math.log(mse) + 2 * df + 2 * df * (df + 1) / (n - df - 1)
        if best is None or aicc < best:
            best, best_beta = aicc, list(beta)
    return [keep[a] for a in range(q) if best_beta[a] > 0]


def forecasts(table: Mapping[date, Week]) -> tuple[dict[date, dict[str, float]], dict[date, dict[str, Any]]]:
    """Per decision with a 26-week window: CTREND for each member, and what the net kept (or that it kept none and
    the plain mean of all forecasts stood in, the paper's equation 9)."""
    coef: dict[tuple[date, int], tuple[float, float] | None] = {}
    out: dict[date, dict[str, float]] = {}
    notes: dict[date, dict[str, Any]] = {}
    p = len(NAMES)
    for day, week in sorted(table.items()):
        past = window(table, day)
        if len(past) < MIN_WEEKS or not week.rows:
            continue
        smooth: list[tuple[float, float] | None] = []
        for j in range(p):
            fits = []
            for w in past:
                if (w.day, j) not in coef:
                    coef[(w.day, j)] = univariate(w, j)
                if coef[(w.day, j)] is not None:
                    fits.append(coef[(w.day, j)])
            smooth.append((statistics.fmean(a for a, _b in fits), statistics.fmean(b for _a, b in fits)) if fits else None)
        total = Stats(p)
        for w in past:
            total.add(w.stats())
        signs = [0 if smooth[j] is None or smooth[j][1] == 0 else (1 if smooth[j][1] > 0 else -1) for j in range(p)]
        keep, gram, corr, vy, n = standardised(total, signs)
        kept = elastic_net_positive(keep, gram, corr, vy, n)
        use = kept or [j for j in range(p) if smooth[j] is not None]
        if not use:
            continue
        out[day] = {s: statistics.fmean(smooth[j][0] + smooth[j][1] * x[j] for j in use) for s, x, _wt, _y in week.rows}
        notes[day] = {"window_weeks": len(past), "kept": len(kept), "fallback": not kept}
    return out, notes


def score_variable(scores: Mapping[date, Mapping[str, float]]) -> lab.Variable:
    def read(market: xsb.Market, symbol: str, day: date) -> float | None:
        return scores.get(day, {}).get(symbol)
    return read


# --- the book ------------------------------------------------------------------------------------------------------

def book_weigher(read: lab.Variable) -> Callable[..., dict[str, float]]:
    """The record's top-30 weekly book: L9T on the score, long the high."""
    return sl.BOOKS["L9T"](read, lambda _d: 1)


def measure(market: xsb.Market, read: lab.Variable) -> dict[str, Any]:
    report = sl.measure(market, book_weigher(read), BOOK_SPAN, simulate=sl.simulate_held)
    report["later_years"] = sl.later_years(report["years_total_position_pct"])
    return report


def pipeline(market: xsb.Market, reads: Mapping[str, lab.Variable], members) -> tuple[dict[str, Any], dict]:
    scores, notes = forecasts(weeks(market, reads, members))
    return measure(market, score_variable(scores)), notes


KEEP = ("annual_sharpe", "total_position_annual_pct", "years_total_position_pct", "clears_entry", "months")


def null_p(sharpe: float | None, nulls: Sequence[float | None]) -> float | None:
    if sharpe is None:
        return None
    return (1 + sum(1 for v in nulls if v is not None and v >= sharpe)) / (len(nulls) + 1)


def judge(report: Mapping[str, Any], nulls: Sequence[float | None]) -> dict[str, Any]:
    p = null_p(report["annual_sharpe"], nulls)
    a, b, c = bool(report["clears_entry"]), bool(report["later_years"]["passes"]), p is not None and p <= ALPHA
    return {"entry": a, "later_years": b, "null": {"books": len(nulls), "p": round(p, 4) if p is not None else None,
                                                    "alpha": ALPHA, "passes": c}, "candidate": a and b and c}


def plant_reading(rows: Mapping[str, Mapping[str, Any]], nulls: Sequence[float | None]) -> dict[str, Any]:
    out = {}
    for rho in PLANT_RHOS:
        books = [r for r in rows.values() if r["rho"] == rho]
        ps = [null_p(r["annual_sharpe"], nulls) for r in books]
        seen = sum(1 for p in ps if p is not None and p <= ALPHA)
        out[str(rho)] = {"seeds": len(books), "p_at_alpha": seen,
                         "and_entry": sum(1 for p, r in zip(ps, books) if p is not None and p <= ALPHA and r["clears_entry"]),
                         "sees": bool(books) and seen / len(books) >= PLANT_SEES}
    void = out[str(0.0)]["p_at_alpha"] > PLANT_FALSE_ALARMS
    return {"by_rho": out, "false_alarms": out[str(0.0)]["p_at_alpha"], "void": void,
            "smallest_seen": None if void else min((r for r in PLANT_RHOS if r > 0 and out[str(r)]["sees"]), default=None)}


def null_count(seconds_per_book: float) -> int:
    """Section 116.4: N = min(200, floor(6 hours / one book))."""
    return min(NULL_CAP, int(NULL_HOURS * 3600 // max(seconds_per_book, 1e-9)))


# --- the gates without a return ------------------------------------------------------------------------------------

def counts(table: Mapping[date, Week], notes: Mapping[date, Mapping[str, Any]]) -> dict[str, Any]:
    """Per year: decisions, member rows, valid weeks, forecasts, nets that kept none; the first forecast; each
    indicator's share of member rows with a value is in ``coverage``. No book is built."""
    years: dict[str, dict[str, Any]] = {}
    for day, week in sorted(table.items()):
        y = years.setdefault(str(day.year), {"decisions": 0, "rows": 0, "valid_weeks": 0, "forecasts": 0,
                                             "fallback": 0, "kept": []})
        y["decisions"] += 1
        y["rows"] += len(week.rows)
        y["valid_weeks"] += week.valid
        if day in notes:
            y["forecasts"] += 1
            y["fallback"] += notes[day]["fallback"]
            y["kept"].append(notes[day]["kept"])
    for y in years.values():
        kept = y.pop("kept")
        y["mean_kept"] = round(statistics.fmean(kept), 2) if kept else None
    first = min(notes) if notes else None
    return {"years": years, "first_forecast": first.isoformat() if first else None,
            "stop": {"last_first_forecast": LAST_FIRST_FORECAST.isoformat(),
                     "stops": first is None or first > LAST_FIRST_FORECAST}}


def coverage(table: Mapping[date, Week]) -> dict[str, float | None]:
    """Each input's share of member rows with a value read (a middle rank of 0 is a value too)."""
    rows = sum(len(w.rows) for w in table.values())
    return {name: round(sum(w.present[j] for w in table.values() if w.present) / rows, 3) if rows else None
            for j, name in enumerate(NAMES)}


def turnover(market: xsb.Market, read: lab.Variable, members) -> float | None:
    """The book's mean weekly one-way turnover over the 48 months, from its weights alone (no return)."""
    build = book_weigher(read)
    held: dict[str, float] = {}
    moves = []
    for day in sl.sundays(BOOK_SPAN):
        if not BOOK_SPAN[0] <= day + timedelta(days=1) <= BOOK_SPAN[1]:
            continue
        target = build(market, members.get(date(day.year, day.month, 1), ()), day, bs.CADENCE)
        if held or target:
            moves.append(0.5 * sum(abs(target.get(k, 0.0) - held.get(k, 0.0)) for k in set(held) | set(target)))
        held = target
    return round(statistics.fmean(moves), 4) if moves else None


def overlap(market: xsb.Market, scores: Mapping[date, Mapping[str, float]], which: str, members) -> dict[str, Any]:
    """The real book's weights beside every struck top-30 book whose group this market loads (built long the high:
    the size of a cosine does not depend on the side) and, on the bars market, S-MOM30's basket. No return read."""
    registered = lab.variables()
    loaded = OVERLAP_GROUPS[which]
    struck = {n: g for n in registered if (n in sl.TIER_SWEEP_OUT or lab.no_book(n, TIER))
              for g in (registered[n][0],)}

    def with_members(build):
        return lambda m, _members, day, signal, **kw: build(m, members.get(date(day.year, day.month, 1), ()), day, signal)
    others = {n: with_members(book_weigher(registered[n][1])) for n, g in sorted(struck.items()) if g in loaded}
    if which == "bars":
        others["S-MOM30"] = with_members(lambda m, mem, day, signal: mbs.vol_weights(m, mem, day, mbs.SIGNAL))
    mine = with_members(book_weigher(score_variable(scores)))

    class Days:
        days = [d for d in sl.sundays(BOOK_SPAN) if BOOK_SPAN[0] <= d + timedelta(days=1) <= BOOK_SPAN[1]]
    rows = sl._beside(market, Days, mine, others)
    closed = sorted(n for n, r in rows.items() if r["closes"])
    unloaded = sorted(n for n, g in struck.items() if not any(g in v for v in OVERLAP_GROUPS.values()))
    empty = sorted(n for n, r in rows.items() if r["decisions"] == 0)
    return {"market": which, "threshold": OVERLAP_CLOSE, "compared": sorted(others), "beside": rows,
            "not_loaded_anywhere": unloaded, "closed_by": closed,
            "blocked": not rows or bool(empty), "not_compared": empty,
            "passes": bool(rows) and not empty and not closed}


# --- the command ---------------------------------------------------------------------------------------------------

def _read(path: str | None) -> dict[str, Any] | None:
    return None if path is None else json.loads(Path(path).read_text(encoding="utf-8"))


def _books(files: str | None) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for path in (files or "").split(","):
        if path:
            for k, row in json.loads(Path(path).read_text(encoding="utf-8"))["books"].items():
                if k in rows:
                    raise ValueError(f"book {k} appears twice")
                rows[k] = row
    return rows


def _seeds(text: str | None, top: int) -> range | None:
    try:
        lo, hi = (int(v) for v in (text or "").split(":"))
    except ValueError:
        return None
    return range(lo, hi) if 0 <= lo < hi <= top else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ctrend", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("ctrend-counts", "ctrend-overlap", "ctrend-time", "ctrend-null",
                                            "ctrend-plant", "ctrend-book"))
    parser.add_argument("--market", choices=OVERLAP_MARKETS, default=None)
    parser.add_argument("--counts", default=None)
    parser.add_argument("--overlap", default=None, help="the three overlap files, comma-separated")
    parser.add_argument("--time", default=None)
    parser.add_argument("--seeds", default=None)
    parser.add_argument("--rho", type=float, choices=PLANT_RHOS, default=None)
    parser.add_argument("--null", default=None, help="the null chunk files, comma-separated")
    parser.add_argument("--plant", default=None, help="the plant files, comma-separated")
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    cmd = args.command
    cnt = _read(args.counts)
    if cmd != "ctrend-counts" and not (cnt and not cnt["counts"]["stop"]["stops"]):
        print("refused: needs --counts that did not stop", file=sys.stderr)
        return EXIT_USAGE
    if cmd == "ctrend-overlap" and args.market is None:
        print("refused: ctrend-overlap needs --market bars|positioning|depth (all three are run)", file=sys.stderr)
        return EXIT_USAGE
    if cmd in ("ctrend-time", "ctrend-null", "ctrend-plant", "ctrend-book"):
        found = [json.loads(Path(p).read_text(encoding="utf-8")) for p in (args.overlap or "").split(",") if p]
        if sorted(f.get("market") for f in found) != sorted(OVERLAP_MARKETS):
            print("refused: needs the bars, positioning and depth overlap files", file=sys.stderr)
            return EXIT_USAGE
        if any(f.get("blocked") for f in found):
            print("BLOCKED: an overlap could not compare every book: report it; this is not a verdict on L30",
                  file=sys.stderr)
            return EXIT_BLOCKED
        if not all(f.get("passes") is True for f in found):
            print("refused: the overlap closed L30: nothing more is built", file=sys.stderr)
            return EXIT_BLOCKED
    n_null = None
    if cmd in ("ctrend-null", "ctrend-plant", "ctrend-book"):
        timed = _read(args.time)
        if not timed or "null_count" not in timed:
            print("refused: needs --time (section 116.4 sets N there)", file=sys.stderr)
            return EXIT_USAGE
        n_null = timed["null_count"]
        if n_null < NULL_FLOOR:
            print(f"refused: N = {n_null} is under {NULL_FLOOR}: ask Thomas (section 116.4)", file=sys.stderr)
            return EXIT_BLOCKED
    seeds = None
    if cmd in ("ctrend-null", "ctrend-plant"):
        seeds = _seeds(args.seeds, n_null if cmd == "ctrend-null" else PLANT_SEEDS)
        if seeds is None or (cmd == "ctrend-plant" and args.rho is None):
            print(f"refused: {cmd} needs --seeds A:B inside its range" + (" and --rho" if cmd == "ctrend-plant" else ""),
                  file=sys.stderr)
            return EXIT_USAGE
    nulls: list[float | None] = []
    plants: dict[str, Any] = {}
    if cmd in ("ctrend-plant", "ctrend-book"):
        try:
            rows = _books(args.null)
            plants = _books(args.plant) if cmd == "ctrend-book" else {}
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return EXIT_USAGE
        if sorted(rows, key=int) != [str(k) for k in range(n_null)]:
            print(f"refused: --null must hold seeds 0..{n_null - 1} exactly", file=sys.stderr)
            return EXIT_USAGE
        nulls = [rows[str(k)]["annual_sharpe"] for k in range(n_null)]
        if cmd == "ctrend-book" and sorted(plants) != sorted(f"{r}|{k}" for r in PLANT_RHOS for k in range(PLANT_SEEDS)):
            print(f"refused: --plant must hold {PLANT_SEEDS} seeds at each of {PLANT_RHOS}", file=sys.stderr)
            return EXIT_USAGE
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    if cmd == "ctrend-overlap" and args.market == "positioning":
        market = lab.hold_positioning(cache)
    elif cmd == "ctrend-overlap" and args.market == "depth":
        market = lab.hold_depth(cache)
    else:
        market = lab.hold(cache, attach=False, flow=cmd == "ctrend-overlap")
    if market is None:
        print("BLOCKED: contract types or the attached values are missing from the cache", file=sys.stderr)
        return EXIT_BLOCKED
    members = sl.monthly_members(market, SPAN)
    head = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC, "command": cmd,
            "span": [d.isoformat() for d in SPAN], "book_span": [d.isoformat() for d in BOOK_SPAN],
            "last_day_read": lab.last_day(market).isoformat()}
    if cmd == "ctrend-counts":
        table = weeks(market, real_inputs(market), members)
        _scores, notes = forecasts(table)
        body: dict[str, Any] = {"reads_book_returns": False, "counts": counts(table, notes), "coverage": coverage(table)}
    elif cmd == "ctrend-overlap":
        scores, _notes = forecasts(weeks(market, real_inputs(market), members))
        body = {"reads_book_returns": False, **overlap(market, scores, args.market, members),
                "real_turnover": turnover(market, score_variable(scores), members)}
    elif cmd == "ctrend-time":
        began = time.monotonic()
        table = weeks(market, null_inputs(0), members)
        built = time.monotonic()
        scores, _notes = forecasts(table)
        fitted = time.monotonic()
        measure(market, score_variable(scores))          # timed only: nothing of it is kept or printed
        booked = time.monotonic()
        per_book = booked - began
        body = {"reads_book_returns": False, "seconds": {"table": round(built - began, 1),
                                                         "forecasts": round(fitted - built, 1),
                                                         "book": round(booked - fitted, 1)},
                "null_count": null_count(per_book), "floor": NULL_FLOOR,
                "null_seed_0_turnover": turnover(market, score_variable(scores), members)}
    elif cmd == "ctrend-null":
        books = {}
        for k in seeds:
            report, _n = pipeline(market, null_inputs(k), members)
            books[str(k)] = {key: report[key] for key in KEEP}
        body = {"reads_book_returns": True, "seeds": [seeds.start, seeds.stop], "books": books}
    elif cmd == "ctrend-plant":
        view = sp.View(market, TIER, SPAN)
        books = {}
        for k in seeds:
            reads = null_inputs(k, PLANT_SEED)
            last = sorted(reads)[-1]
            reads[last] = sp.planted_variable(view, reads[last], args.rho)
            report, _n = pipeline(market, reads, members)
            books[f"{args.rho}|{k}"] = {"rho": args.rho, "seed": k, "planted": last, **{key: report[key] for key in KEEP}}
        body = {"reads_book_returns": True, "rho": args.rho, "seeds": [seeds.start, seeds.stop], "books": books}
    else:
        report, notes = pipeline(market, real_inputs(market), members)
        kept = sorted(notes.values(), key=lambda v: v["kept"])
        body = {"reads_book_returns": True, "book": report, "judgement": judge(report, nulls),
                "plant": plant_reading(plants, nulls),
                "null_p95": sorted(v for v in nulls if v is not None)[int(0.95 * (len(nulls) - 1))] if nulls else None,
                "mean_kept": round(statistics.fmean(v["kept"] for v in kept), 2) if kept else None}
    print(json.dumps({**head, **body}, indent=1, sort_keys=True, ensure_ascii=False, default=str))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
