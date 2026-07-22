"""C3 pure-Python technical indicators — pandas-parity port of the source system.

Ports ``crypto_AI_System/src/crypto_ai_system/features/indicators.py`` without pandas/
numpy, keeping ``requirements-runtime.txt`` minimal (the C1 open-decision resolved in
favor of rewrite). Parity is not aspirational: ``tests/fixtures/`` holds outputs
computed by the *source* implementation over a shared candle fixture, and the parity
test asserts these functions reproduce them value-for-value (None where pandas has NaN).

Conventions:
- A series is ``list[float | None]``; ``None`` is pandas NaN — *indeterminate*, never a
  silent zero. Warm-up prefixes are None exactly where pandas ``min_periods`` masks.
- Rolling windows count only non-None observations against ``min_periods`` and ignore
  None values in the aggregate, matching pandas rolling semantics.
- Rolling std uses ddof=1 (the pandas default), and divisions that pandas maps to
  NaN/inf map to None here.
"""

from __future__ import annotations

import math

Series = list  # list[float | None]; alias for signature readability


def _is_num(value: object) -> bool:
    return isinstance(value, (int, float)) and not (isinstance(value, float) and math.isnan(value))


def _window_values(series: Series, i: int, window: int) -> list[float]:
    """Non-None values in the window ending at ``i`` (pandas: NaN excluded)."""
    start = max(0, i - window + 1)
    return [v for v in series[start : i + 1] if _is_num(v)]


def rolling_mean(series: Series, window: int, min_periods: int) -> Series:
    out: Series = []
    for i in range(len(series)):
        values = _window_values(series, i, window)
        out.append(sum(values) / len(values) if len(values) >= min_periods else None)
    return out


def rolling_sum(series: Series, window: int, min_periods: int) -> Series:
    out: Series = []
    for i in range(len(series)):
        values = _window_values(series, i, window)
        out.append(sum(values) if len(values) >= min_periods else None)
    return out


def rolling_std(series: Series, window: int, min_periods: int) -> Series:
    """Sample standard deviation (ddof=1, the pandas default)."""
    out: Series = []
    for i in range(len(series)):
        values = _window_values(series, i, window)
        if len(values) < max(min_periods, 2):  # ddof=1: one value has no variance
            out.append(None)
            continue
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        out.append(math.sqrt(var))
    return out


def sma(series: Series, period: int) -> Series:
    return rolling_mean(series, period, min_periods=period)


def ema(series: Series, span: int) -> Series:
    """Exponential moving average, pandas ``ewm(span=..., adjust=False,
    min_periods=span)``: the recursion starts at the first non-None value, None
    inputs leave the state unchanged, and output is masked until ``span`` non-None
    observations have been seen."""
    alpha = 2.0 / (span + 1.0)
    out: Series = []
    state: float | None = None
    seen = 0
    for value in series:
        if _is_num(value):
            seen += 1
            state = value if state is None else (1.0 - alpha) * state + alpha * value
        out.append(state if seen >= span and state is not None and _is_num(value) else None)
    return out


def true_range(high: Series, low: Series, close: Series) -> Series:
    """TR; the first bar has no previous close, so it degenerates to high-low
    (pandas ``concat(...).max(axis=1)`` skips the NaN legs)."""
    out: Series = []
    for i in range(len(close)):
        h, low_i = high[i], low[i]
        if not (_is_num(h) and _is_num(low_i)):
            out.append(None)
            continue
        if i == 0 or not _is_num(close[i - 1]):
            out.append(h - low_i)
            continue
        prev_close = close[i - 1]
        out.append(max(h - low_i, abs(h - prev_close), abs(low_i - prev_close)))
    return out


def atr(high: Series, low: Series, close: Series, period: int = 14) -> Series:
    return rolling_mean(true_range(high, low, close), period, min_periods=period)


def rsi(close: Series, period: int = 14) -> Series:
    gain: Series = [None]
    loss: Series = [None]
    for i in range(1, len(close)):
        if _is_num(close[i]) and _is_num(close[i - 1]):
            delta = close[i] - close[i - 1]
            gain.append(max(delta, 0.0))
            loss.append(max(-delta, 0.0))
        else:
            gain.append(None)
            loss.append(None)
    avg_gain = rolling_mean(gain, period, min_periods=period)
    avg_loss = rolling_mean(loss, period, min_periods=period)
    out: Series = []
    for g, l in zip(avg_gain, avg_loss):
        if g is None or l is None or l == 0:  # zero loss: rs undefined (pandas NaN)
            out.append(None)
        else:
            out.append(100.0 - 100.0 / (1.0 + g / l))
    return out


def adx(high: Series, low: Series, close: Series, period: int = 14) -> Series:
    """ADX, replicating the source's exact sequence — including its sequential
    ``where`` rebinding: minus_dm is compared against the already-zeroed plus_dm,
    which differs from a simultaneous comparison only on exact +DM/-DM ties."""
    n = len(close)
    plus_dm: Series = []
    minus_dm: Series = []
    for i in range(n):
        if i == 0 or not (_is_num(high[i]) and _is_num(high[i - 1]) and _is_num(low[i]) and _is_num(low[i - 1])):
            # diff() is NaN at the head; a NaN comparison is False → .where fills 0.0.
            plus_dm.append(0.0)
            minus_dm.append(0.0)
            continue
        pdm = high[i] - high[i - 1]
        mdm = low[i - 1] - low[i]
        plus = pdm if (pdm > mdm and pdm > 0) else 0.0
        minus = mdm if (mdm > plus and mdm > 0) else 0.0  # vs the zeroed plus, as in the source
        plus_dm.append(plus)
        minus_dm.append(minus)

    tr_sum = rolling_sum(true_range(high, low, close), period, min_periods=period)
    plus_sum = rolling_sum(plus_dm, period, min_periods=period)
    minus_sum = rolling_sum(minus_dm, period, min_periods=period)

    dx: Series = []
    for i in range(n):
        t, p, m = tr_sum[i], plus_sum[i], minus_sum[i]
        if t is None or p is None or m is None or t == 0:
            dx.append(None)
            continue
        plus_di = 100.0 * p / t
        minus_di = 100.0 * m / t
        denom = plus_di + minus_di
        dx.append(None if denom == 0 else 100.0 * abs(plus_di - minus_di) / denom)
    return rolling_mean(dx, period, min_periods=period)


def macd(close: Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[Series, Series, Series]:
    """MACD line, signal line, histogram. None until the slow EMA has warmed up."""
    fast_ema = ema(close, fast)
    slow_ema = ema(close, slow)
    line: Series = [
        f - s if (f is not None and s is not None) else None for f, s in zip(fast_ema, slow_ema)
    ]
    signal_line = ema(line, signal)
    hist: Series = [
        m - s if (m is not None and s is not None) else None for m, s in zip(line, signal_line)
    ]
    return line, signal_line, hist


def bollinger(close: Series, period: int = 20, num_std: float = 2.0) -> tuple[Series, Series, Series, Series]:
    """Upper, lower, width (% of mid), and %B. Zero-width / zero-mid stay None."""
    mid = sma(close, period)
    std = rolling_std(close, period, min_periods=period)
    upper: Series = []
    lower: Series = []
    width_pct: Series = []
    percent_b: Series = []
    for i in range(len(close)):
        m, s = mid[i], std[i]
        if m is None or s is None:
            upper.append(None), lower.append(None), width_pct.append(None), percent_b.append(None)
            continue
        u, low_band = m + num_std * s, m - num_std * s
        upper.append(u)
        lower.append(low_band)
        width_pct.append(None if m == 0 else (u - low_band) / m)
        band = u - low_band
        c = close[i]
        percent_b.append(None if (band == 0 or not _is_num(c)) else (c - low_band) / band)
    return upper, lower, width_pct, percent_b


def roc(close: Series, period: int) -> Series:
    """Rate of change over ``period`` bars, as a fraction (0.01 == +1%)."""
    out: Series = []
    for i in range(len(close)):
        if i < period or not (_is_num(close[i]) and _is_num(close[i - period])) or close[i - period] == 0:
            out.append(None)  # pandas: NaN head, inf (division by zero) → NaN
        else:
            out.append(close[i] / close[i - period] - 1.0)
    return out


def zscore(series: Series, window: int, min_periods: int | None = None) -> Series:
    """Rolling z-score (pandas parity: zero-variance → None). ``min_periods``
    defaults to the window; the funding z-score uses the source's looser 10."""
    min_periods = window if min_periods is None else min_periods
    mean = rolling_mean(series, window, min_periods=min_periods)
    std = rolling_std(series, window, min_periods=min_periods)
    out: Series = []
    for i in range(len(series)):
        value, m, s = series[i], mean[i], std[i]
        if not _is_num(value) or m is None or s is None or s == 0:
            out.append(None)
        else:
            out.append((value - m) / s)
    return out


def rolling_percentile(series: Series, window: int = 100) -> Series:
    """Percentile rank of the window's last value (pandas ``rank(pct=True)``,
    average method over the window's non-None values; min_periods as the source:
    ``max(10, window // 5)``)."""
    min_periods = max(10, window // 5)
    out: Series = []
    for i in range(len(series)):
        values = _window_values(series, i, window)
        last = series[i]
        if len(values) < min_periods or not _is_num(last):
            out.append(None)
            continue
        less = sum(1 for v in values if v < last)
        equal = sum(1 for v in values if v == last)
        out.append((less + (equal + 1) / 2.0) / len(values))
    return out
