"""C3 feature rows — the evaluator's input, built from a C2 market snapshot.

Ports the slice of the source system's ``feature_store.build_feature_frame`` that the
active strategy population actually references (audited against the real pool: 38
strategies, 21 distinct feature names), with the source's own defaults for every
period. Everything OHLCV-derived is computed by ``indicators``; the four names that
come from feeds not yet ported (mark/index price, basis, liquidation spike) carry the
source's **documented no-feed fallbacks** — mark/index fall back to close (making the
basis 0), liquidations legacy-fill to a 0.0 spike ratio — so a spec referencing them
evaluates exactly as the source system does when those feeds are absent. Every other
unknown feature is simply absent from the row, which the evaluator treats as
indeterminate → no entry (fail-closed, the source's own rule).

Warm-up rows carry ``data_quality_status="WARMUP"`` when any of close/atr/rsi/adx is
still None (the source's definition), so downstream health checks can refuse to trade
on an unwarmed row without re-deriving the rule.
"""

from __future__ import annotations

from typing import Any

from . import indicators

# Source-system defaults (config keys features.*), fixed — the MVP carries no AppConfig.
MA_FAST = 20
MA_SLOW = 50
EMA_FAST = 20
EMA_SLOW = 50
ATR_PERIOD = 14
RSI_PERIOD = 14
ADX_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BB_PERIOD = 20
BB_STD = 2.0
PERCENTILE_WINDOW = 100
ROC_FAST = 4

# The minimum candle count for a fully-warmed row: bb_width_percentile needs
# BB_PERIOD - 1 warm-up plus max(10, PERCENTILE_WINDOW // 5) observations.
MIN_WARM_CANDLES = BB_PERIOD - 1 + max(10, PERCENTILE_WINDOW // 5)


def build_feature_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Compute one feature row per candle from a C2 snapshot dict.

    Deterministic, pure, no I/O. Row values are float or None (indeterminate).
    """
    candles = snapshot.get("candles") or []
    opens = [c["open"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]

    ma20 = indicators.sma(closes, MA_FAST)
    ma50 = indicators.sma(closes, MA_SLOW)
    ema20 = indicators.ema(closes, EMA_FAST)
    ema50 = indicators.ema(closes, EMA_SLOW)
    atr = indicators.atr(highs, lows, closes, ATR_PERIOD)
    rsi = indicators.rsi(closes, RSI_PERIOD)
    adx = indicators.adx(highs, lows, closes, ADX_PERIOD)
    _, _, macd_hist = indicators.macd(closes, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    bb_upper, bb_lower, bb_width_pct, bb_percent_b = indicators.bollinger(closes, BB_PERIOD, BB_STD)
    bb_width_percentile = indicators.rolling_percentile(bb_width_pct, PERCENTILE_WINDOW)
    roc_4 = indicators.roc(closes, ROC_FAST)

    rows: list[dict[str, Any]] = []
    for i, candle in enumerate(candles):
        close = closes[i]
        row: dict[str, Any] = {
            "timestamp": candle["open_time"],
            "open": opens[i],
            "high": highs[i],
            "low": lows[i],
            "close": close,
            "volume": volumes[i],
            "ma20": ma20[i],
            "ma50": ma50[i],
            "ema20": ema20[i],
            "ema50": ema50[i],
            "atr": atr[i],
            "rsi": rsi[i],
            "adx": adx[i],
            "macd_hist": macd_hist[i],
            "bb_upper": bb_upper[i],
            "bb_lower": bb_lower[i],
            "bb_width_pct": bb_width_pct[i],
            "bb_percent_b": bb_percent_b[i],
            "bb_width_percentile": bb_width_percentile[i],
            "roc_4": roc_4[i],
            "price_distance_ma20": (
                (close - ma20[i]) / ma20[i] if (ma20[i] not in (None, 0)) else None
            ),
            # No-feed fallbacks, verbatim from the source's absent-feed behavior:
            # mark/index ffill().fillna(close) → close; basis → 0; liquidation
            # legacy 0-fill → spike ratio 0.0.
            "mark_price": close,
            "index_price": close,
            "mark_index_basis_bps": 0.0,
            "liquidation_spike_ratio": 0.0,
        }
        row["data_quality_status"] = (
            "WARMUP"
            if any(row[k] is None for k in ("close", "atr", "rsi", "adx"))
            else "OK"
        )
        rows.append(row)
    return rows


def latest_feature_row(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The most recent feature row — what the entry evaluator consumes. Empty
    snapshot → empty row (every lookup indeterminate, so nothing can match)."""
    rows = build_feature_rows(snapshot)
    return rows[-1] if rows else {}
