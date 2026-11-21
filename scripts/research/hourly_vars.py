"""Two variables from the hourly bars, and the input-only doors before the map (round 68, section 86).

``price_delay_30``: over the 720 hours that end when the decision day's daily bar closes (the last instant a daily
variable reads), the coin's hourly log return regressed on BTC's same-hour return alone (plain R² = R²₀) and on BTC's
same-hour and six earlier hours' returns (R²₆); the value is 1 − R²₀ ÷ R²₆. ``volume_peak_share_30``: the share of
the window's quote volume traded in its 24 largest hours. Neither reads a return after the decision day.

Pure Python (the research venv has no numpy): BTC's cross-products are prefix sums built once, so a coin's regression
costs one pass over its own window. Only coins in the month's top 100 are computed, at the days asked for.
"""

from __future__ import annotations

import heapq
import math
from array import array
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from scripts.research import cross_section_basket as xsb

HOURLY_KIND = "klines-1h"
HOUR_MS = 3_600_000
WINDOW = 720                    # hours: 30 days
LAGS = 6                        # BTC's returns 1 .. 6 hours earlier
MIN_HOURS = 648                 # 90% of the window
PEAK_HOURS = 24
REFERENCE = "BTCUSDT"
QUOTE_VOLUME_COLUMN = 7


def hour_of(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()) // 3600


def window_end(day: date) -> int:
    """The hour the window ends at (exclusive): the decision day's daily bar closes at the next day's 00:00 UTC."""
    return hour_of(day + timedelta(days=1))


class Series:
    """One symbol's hourly opens and quote volumes as flat arrays from its first hour (NaN where a bar is missing)."""

    def __init__(self, rows: Mapping[int, tuple[float, float]]) -> None:
        self.first = min(rows) if rows else 0
        last = max(rows) if rows else -1
        self.opens = array("d", (rows[h][0] if h in rows else math.nan for h in range(self.first, last + 1)))
        self.quote = array("d", (rows[h][1] if h in rows else math.nan for h in range(self.first, last + 1)))

    def _at(self, values: array, hour: int) -> float:
        k = hour - self.first
        return values[k] if 0 <= k < len(values) else math.nan

    def open(self, hour: int) -> float:
        return self._at(self.opens, hour)

    def volume(self, hour: int) -> float:
        return self._at(self.quote, hour)

    def ret(self, hour: int) -> float:
        """Log return from the open of ``hour`` to the open of the next hour; NaN when either is missing."""
        a, b = self.open(hour), self.open(hour + 1)
        return math.log(b / a) if a > 0 and b > 0 else math.nan


def read_symbol(folder: Path, *, before: int, after: int | None = None) -> dict[int, tuple[float, float]]:
    """``{hour: (open, quote volume)}`` from one symbol's monthly 1h zips; nothing at or after hour ``before``, and
    with ``after`` nothing before that hour (a load that asks only for late decisions, round 121)."""
    out: dict[int, tuple[float, float]] = {}
    for path in sorted(folder.glob("*.zip")) if folder.exists() else []:
        for row in xsb._rows(path):
            try:
                stamp, price, quote = float(row[0]), float(row[1]), float(row[QUOTE_VOLUME_COLUMN])
            except (ValueError, IndexError):
                continue          # the header row
            if stamp > 1e14:      # microseconds
                stamp /= 1000
            hour = int(stamp // HOUR_MS)
            if price > 0 and hour < before and (after is None or hour >= after):
                out[hour] = (price, quote)
    return out


class Reference:
    """BTC's returns and, over the hours whose same-hour and six earlier returns all exist, prefix sums of each of
    the seven and of each product of two, so the BTC side of any window's regression is two lookups."""

    def __init__(self, btc: Series, first: int, last: int) -> None:
        self.first, self.last = first, last
        n = last - first + 1
        self.rows: list[tuple[float, ...] | None] = []
        for h in range(first, last + 1):
            lagged = tuple(btc.ret(h - k) for k in range(LAGS + 1))
            self.rows.append(None if any(math.isnan(x) for x in lagged) else lagged)
        pairs = [(j, k) for j in range(LAGS + 1) for k in range(j, LAGS + 1)]
        self.pairs = pairs
        self.count = array("d", [0.0]) * (n + 1)
        self.sx = [array("d", [0.0]) * (n + 1) for _ in range(LAGS + 1)]
        self.sxx = [array("d", [0.0]) * (n + 1) for _ in pairs]
        for i, row in enumerate(self.rows):
            self.count[i + 1] = self.count[i] + (row is not None)
            for k in range(LAGS + 1):
                self.sx[k][i + 1] = self.sx[k][i] + (row[k] if row else 0.0)
            for p, (j, k) in enumerate(pairs):
                self.sxx[p][i + 1] = self.sxx[p][i] + (row[j] * row[k] if row else 0.0)

    def row(self, hour: int) -> tuple[float, ...] | None:
        return self.rows[hour - self.first] if self.first <= hour <= self.last else None

    def sums(self, start: int, end: int) -> tuple[float, list[float], list[float]]:
        """Count, the seven sums and the 28 product sums over BTC's valid hours in [start, end)."""
        a, b = max(start, self.first) - self.first, min(end, self.last + 1) - self.first
        if b <= a:
            return 0.0, [0.0] * (LAGS + 1), [0.0] * len(self.pairs)
        return (self.count[b] - self.count[a], [s[b] - s[a] for s in self.sx], [s[b] - s[a] for s in self.sxx])


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting; None when the system is singular."""
    n = len(vector)
    m = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-18:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            if f:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    out = [0.0] * n
    for r in range(n - 1, -1, -1):
        out[r] = (m[r][n] - sum(m[r][c] * out[c] for c in range(r + 1, n))) / m[r][r]
    return out


def delay(coin: Series, ref: Reference, end: int) -> tuple[float, float] | None:
    """``(1 − R²₀ ÷ R²₆, R²₀)`` over the hours [end − WINDOW, end) whose coin return and BTC's seven returns all
    exist; None under :data:`MIN_HOURS` such hours, or when R²₆ is not positive."""
    start = end - WINDOW
    n, sx, sxx = ref.sums(start, end)
    sx, sxx = list(sx), list(sxx)
    sy = syy = 0.0
    sxy = [0.0] * (LAGS + 1)
    for hour in range(start, end):
        row = ref.row(hour)
        if row is None:
            continue
        y = coin.ret(hour)
        if math.isnan(y):          # BTC's hour counted in the prefix sums: take it back out
            n -= 1
            for k in range(LAGS + 1):
                sx[k] -= row[k]
            for p, (j, k) in enumerate(ref.pairs):
                sxx[p] -= row[j] * row[k]
            continue
        sy += y
        syy += y * y
        for k in range(LAGS + 1):
            sxy[k] += row[k] * y
    if n < MIN_HOURS:
        return None
    cyy = syy - sy * sy / n
    cxy = [sxy[k] - sx[k] * sy / n for k in range(LAGS + 1)]
    cxx = [[0.0] * (LAGS + 1) for _ in range(LAGS + 1)]
    for p, (j, k) in enumerate(ref.pairs):
        cxx[j][k] = cxx[k][j] = sxx[p] - sx[j] * sx[k] / n
    if cyy <= 0 or cxx[0][0] <= 0:
        return None
    r2_0 = cxy[0] * cxy[0] / (cxx[0][0] * cyy)
    beta = _solve(cxx, cxy)
    if beta is None:
        return None
    r2_6 = sum(beta[k] * cxy[k] for k in range(LAGS + 1)) / cyy
    if r2_6 <= 0:
        return None
    return 1 - r2_0 / r2_6, r2_0


def peak_share(coin: Series, end: int) -> float | None:
    """The 24 largest hours' share of the quote volume over the bars in [end − WINDOW, end); None under
    :data:`MIN_HOURS` bars or a zero total."""
    volumes = [v for h in range(end - WINDOW, end) if not math.isnan(v := coin.volume(h))]
    total = sum(volumes)
    if len(volumes) < MIN_HOURS or total <= 0:
        return None
    return sum(heapq.nlargest(PEAK_HOURS, volumes)) / total


def build(archive: Path, wanted: Mapping[str, Iterable[date]],
          span: tuple[date, date], *, after: int | None = None) -> tuple[dict[str, dict[date, dict[str, float]]], int | None]:
    """``({symbol: {day: {"price_delay_30", "r2_btc_30", "volume_peak_share_30"}}}, BTC's first hour)`` at the days
    asked for each symbol, one symbol read at a time, every file cut at the span's end. ``after`` leaves out every
    hour before it, BTC's too: for days whose window and lags all lie after it, the values are the same."""
    before = window_end(span[1])
    btc = Series(read_symbol(archive / HOURLY_KIND / REFERENCE, before=before, after=after))
    if not len(btc.opens):
        return {}, None
    ref = Reference(btc, btc.first, before - 1)
    out: dict[str, dict[date, dict[str, float]]] = {}
    for symbol in sorted(wanted):
        days = sorted(set(wanted[symbol]))
        if symbol == REFERENCE or not days:
            continue
        coin = Series(read_symbol(archive / HOURLY_KIND / symbol, before=before, after=after))
        values: dict[date, dict[str, float]] = {}
        for day in days:
            end = window_end(day)
            if end > before:
                continue
            row: dict[str, float] = {}
            if (d := delay(coin, ref, end)) is not None:
                row["price_delay_30"], row["r2_btc_30"] = d
            if (p := peak_share(coin, end)) is not None:
                row["volume_peak_share_30"] = p
            if row:
                values[day] = row
        if values:
            out[symbol] = values
    return out, btc.first


def reader(name: str):
    """A registry-shaped reading of one built value: ``(market, symbol, day) -> float | None``; the market carries
    the built values as ``hourly_values``."""
    def read(market: xsb.Market, symbol: str, day: date) -> float | None:
        return ((getattr(market, "hourly_values", None) or {}).get(symbol) or {}).get(day, {}).get(name)
    return read


def coverage(market: xsb.Market, name: str, tiers: Mapping[str, Mapping[date, Sequence[str]]],
             days: Sequence[date], *, read: Any = None) -> dict[str, Any]:
    """Section 57.3's two ratios per tier: of the cells a null book ranks (a member with a bar on the day), the
    share with a value; the same over the members not in the next month's top 100. A cell whose next month lies past
    the tiers' last month (the span's December) belongs to neither side of the second ratio: whether it leaves is
    not known. ``read`` is another variable's reading in the registry's shape (section 97's R1)."""
    read = read or reader(name)
    top = {m: set(tiers["top30"].get(m, ())) | set(tiers["rank31_100"].get(m, ())) for m in tiers["top30"]}
    out: dict[str, Any] = {}
    for tier in ("top30", "rank31_100"):
        cells = filled = out_cells = out_filled = 0
        for day in days:
            month = date(day.year, day.month, 1)
            following = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
            for symbol in tiers[tier].get(month, ()):
                if (market.bars.get(symbol) or {}).get(day) is None:
                    continue
                has = read(market, symbol, day) is not None
                cells, filled = cells + 1, filled + has
                if following in top and symbol not in top[following]:
                    out_cells, out_filled = out_cells + 1, out_filled + has
        out[tier] = {"cells": cells, "share": round(filled / cells, 4) if cells else None,
                     "cells_out_next_month": out_cells,
                     "share_out_next_month": round(out_filled / out_cells, 4) if out_cells else None}
    return out


def persistence(market: xsb.Market, name: str, tiers: Mapping[str, Mapping[date, Sequence[str]]],
                days: Sequence[date], rank_correlation, *, lags: Sequence[int] = (1, 2, 4), min_names: int = 8) -> dict[str, Any]:
    """How far the variable's order carries: per tier and lag (in weekly decisions), the mean over decisions of the
    rank correlation between its values at the decision and that many decisions later, over the tier's members at
    the first with values at both."""
    read = reader(name)
    out: dict[str, Any] = {}
    for tier in ("top30", "rank31_100"):
        out[tier] = {}
        for lag in lags:
            rhos = []
            for i in range(len(days) - lag):
                a, b = days[i], days[i + lag]
                pairs = [(x, y) for s in tiers[tier].get(date(a.year, a.month, 1), ())
                         if (x := read(market, s, a)) is not None and (y := read(market, s, b)) is not None]
                if len(pairs) >= min_names and (rho := rank_correlation(pairs)) is not None:
                    rhos.append(rho)
            out[tier][f"{7 * lag}d"] = {"decisions": len(rhos), "mean_rank_correlation": round(sum(rhos) / len(rhos), 4) if rhos else None}
    return out
