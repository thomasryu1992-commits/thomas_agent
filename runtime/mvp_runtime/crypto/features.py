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
VOLUME_Z_WINDOW = 20
ADX_TREND_THRESHOLD = 20.0  # entry_policy.adx_trend_threshold source default
FUNDING_Z_WINDOW = 100      # features.funding_z_window source default
FUNDING_Z_MIN_PERIODS = 10  # the source's looser min_periods for the funding z
OI_CHANGE_PERIOD = 1        # bars between the two OI reads a change compares
OI_Z_WINDOW = 30            # how unusual the current OI level is, against its own history
OI_Z_MIN_PERIODS = 10
LIQ_MA_WINDOW = 50          # liquidation spike baseline window
LIQ_MA_MIN_PERIODS = 10


def _asof_align(
    bar_times: list[str], events: list[dict[str, Any]], columns: tuple[str, ...],
    *, numeric: bool = True,
) -> dict[str, list]:
    """Pure-python ``merge_asof(direction='backward')`` — each bar carries the last
    event at or before its OPEN time (an event can never leak into earlier bars);
    bars before the first event, or with an unparseable key, stay None (the
    source's rule: indeterminate, never a constant).

    ``numeric=False`` passes values through uncast, for categorical columns (the
    HTF regime label); the alignment itself is identical, and that identity is the
    point — one look-ahead rule, not two."""
    from .. import timeutil as _timeutil

    parsed_events: list[tuple[Any, dict[str, Any]]] = []
    for event in events:
        raw = event.get("timestamp")
        if not isinstance(raw, str):
            continue
        try:
            parsed_events.append((_timeutil.parse_iso(raw), event))
        except (ValueError, TypeError):
            continue
    parsed_events.sort(key=lambda pair: pair[0])
    out: dict[str, list] = {col: [] for col in columns}
    cursor = 0
    last: dict[str, Any] | None = None
    for raw_bar in bar_times:
        try:
            bar_ts = _timeutil.parse_iso(raw_bar)
        except (ValueError, TypeError):
            for col in columns:
                out[col].append(None)
            continue
        while cursor < len(parsed_events) and parsed_events[cursor][0] <= bar_ts:
            last = parsed_events[cursor][1]
            cursor += 1
        for col in columns:
            value = last.get(col) if last is not None else None
            if not numeric:
                out[col].append(value)
                continue
            try:
                out[col].append(float(value) if value is not None else None)
            except (TypeError, ValueError):
                out[col].append(None)
    return out


def classify_market_regime(row: dict[str, Any], adx_threshold: float = ADX_TREND_THRESHOLD) -> str:
    """Port of the source regime classifier (features/regime.py), row-wise."""
    close, ma20, ma50, adx = row.get("close"), row.get("ma20"), row.get("ma50"), row.get("adx")
    atr_pct = row.get("atr_percentile")
    if close is None or ma20 is None or ma50 is None or adx is None:
        return "UNCLEAR"
    if atr_pct is not None:
        if atr_pct >= 0.80:
            return "HIGH_VOLATILITY"
        if atr_pct <= 0.20 and adx < adx_threshold:
            return "LOW_VOLATILITY"
    if close > ma20 > ma50 and adx >= adx_threshold:
        return "TREND_UP"
    if close < ma20 < ma50 and adx >= adx_threshold:
        return "TREND_DOWN"
    if adx < adx_threshold:
        return "RANGE"
    return "UNCLEAR"

# The minimum candle count for a fully-warmed row: bb_width_percentile needs
# BB_PERIOD - 1 warm-up plus max(10, PERCENTILE_WINDOW // 5) observations.
MIN_WARM_CANDLES = BB_PERIOD - 1 + max(10, PERCENTILE_WINDOW // 5)

# --- higher-timeframe (HTF) context -------------------------------------------

# The HTF columns a spec may reference. Numeric ones are NORMALIZED (ratios, not
# prices) so a threshold means the same thing on BTC and DOGE; the regime label is
# the categorical one, and the whole point of the family — "only go long while the
# timeframe above says TREND_UP".
HTF_NUMERIC_COLUMNS = ("htf_adx", "htf_rsi", "htf_price_distance_ma20", "htf_ma20_distance_ma50")
HTF_CATEGORICAL_COLUMNS = ("htf_market_regime",)
HTF_COLUMNS = (*HTF_NUMERIC_COLUMNS, *HTF_CATEGORICAL_COLUMNS)


def _htf_columns(bar_times: list[str], htf_candles: list[dict[str, Any]]) -> dict[str, list]:
    """HTF feature columns aligned onto this timeframe's bars, point-in-time.

    **The look-ahead rule, and why it holds structurally.** Each HTF row is keyed by
    its candle's ``close_time`` — the first instant that candle is *known* — and
    aligned with the same backward :func:`_asof_align` the funding and liquidation
    feeds use, which matches on the lower bar's OPEN time. So a bar opening at 04:00
    sees the HTF candle that closed at 04:00 but never the one closing at 07:59:59,
    for live routing and replay alike. The classic HTF backtest error (letting the
    still-forming higher candle decide the lower bar) is therefore not something this
    code has to remember to avoid: the key is the close, so an unclosed candle has no
    key to match on.

    The HTF rows come from :func:`build_feature_rows` itself — one feature pipeline,
    not a second one that could drift — over a sub-snapshot carrying only candles, so
    the recursion terminates at depth one (no ``htf_candles`` key, no HTF columns)."""
    htf_rows = build_feature_rows({"candles": htf_candles})
    events: list[dict[str, Any]] = []
    for candle, row in zip(htf_candles, htf_rows):
        close_time = candle.get("close_time")
        if not isinstance(close_time, str):
            continue  # unkeyable: cannot be placed in time, so it is never visible
        ma20, ma50, close = row.get("ma20"), row.get("ma50"), row.get("close")
        events.append({
            "timestamp": close_time,
            "htf_adx": row.get("adx"),
            "htf_rsi": row.get("rsi"),
            "htf_price_distance_ma20": (
                (close - ma20) / ma20 if (close is not None and ma20 not in (None, 0)) else None
            ),
            "htf_ma20_distance_ma50": (
                (ma20 - ma50) / ma50 if (ma20 is not None and ma50 not in (None, 0)) else None
            ),
            "htf_market_regime": row.get("market_regime"),
        })
    columns = _asof_align(bar_times, events, HTF_NUMERIC_COLUMNS)
    columns.update(_asof_align(bar_times, events, HTF_CATEGORICAL_COLUMNS, numeric=False))
    return columns


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
    macd_line, macd_signal, macd_hist = indicators.macd(closes, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    bb_upper, bb_lower, bb_width_pct, bb_percent_b = indicators.bollinger(closes, BB_PERIOD, BB_STD)
    bb_width_percentile = indicators.rolling_percentile(bb_width_pct, PERCENTILE_WINDOW)
    roc_4 = indicators.roc(closes, ROC_FAST)
    volume_zscore = indicators.zscore(volumes, VOLUME_Z_WINDOW)
    atr_pct_of_price = [
        (a / c if (a is not None and isinstance(c, (int, float)) and c != 0) else None)
        for a, c in zip(atr, closes)
    ]
    atr_percentile = indicators.rolling_percentile(atr_pct_of_price, PERCENTILE_WINDOW)

    # C9 derivative feeds. Key PRESENT in the snapshot = series semantics (even when
    # the fetch failed and the list is empty): values are NaN-honest — indeterminate,
    # never a constant, so a spec referencing them fails closed to no-entry on outage.
    # Key ABSENT = the feed is not configured: the source's legacy constants apply
    # (funding 0-fill, spike ratio 0.0), the exact pre-C9 behavior.
    bar_times = [c["open_time"] for c in candles]
    # HTF context. Key ABSENT (or empty) = no higher timeframe supplied — every HTF
    # column is None, so an htf_* spec is indeterminate and never matches. That is
    # the liquidation posture, not the spike-ratio one: no HTF, no trade, never a
    # fabricated constant that would let a filter silently pass.
    htf_candles = snapshot.get("htf_candles") or []
    htf = (_htf_columns(bar_times, htf_candles) if htf_candles
           else {col: [None] * len(candles) for col in HTF_COLUMNS})
    has_funding_series = "funding" in snapshot
    if has_funding_series:
        funding_rate = _asof_align(bar_times, snapshot.get("funding") or [], ("funding_rate",))["funding_rate"]
        funding_zscore = indicators.zscore(funding_rate, FUNDING_Z_WINDOW, FUNDING_Z_MIN_PERIODS)
    else:
        funding_rate = [0.0] * len(candles)
        funding_zscore = [0.0] * len(candles)

    has_liq_series = "liquidations" in snapshot
    if has_liq_series:
        aligned = _asof_align(bar_times, snapshot.get("liquidations") or [],
                              ("long_liquidation", "short_liquidation"))
        long_liq, short_liq = aligned["long_liquidation"], aligned["short_liquidation"]
        liq_total = [
            (l + s) if (l is not None and s is not None) else None
            for l, s in zip(long_liq, short_liq)
        ]
        liq_ma = indicators.rolling_mean(liq_total, LIQ_MA_WINDOW, LIQ_MA_MIN_PERIODS)
        liquidation_spike_ratio = [
            (t / m) if (t is not None and m is not None and m != 0) else None
            for t, m in zip(liq_total, liq_ma)
        ]
    else:
        long_liq = short_liq = liq_total = [None] * len(candles)
        liquidation_spike_ratio = [0.0] * len(candles)  # legacy constant, pre-C9

    # Open interest — how much position is OUTSTANDING, the third leg beside what it
    # costs to hold (funding) and what was forcibly closed (liquidations). Raw OI is a
    # venue-scale quantity and means nothing across symbols, so what the row carries
    # (and what a spec may reference) are the NORMALIZED derivatives: the rate of
    # change, and how unusual the current level is against its own recent history.
    #
    # Series semantics throughout: no feed (key absent) or a failed fetch (key present,
    # empty) both leave every column None — indeterminate, never a constant. That is
    # the liquidation posture deliberately, not the spike-ratio one: a missing feed
    # must not let an oi_* condition match on a fabricated zero.
    if "open_interest" in snapshot:
        open_interest = _asof_align(bar_times, snapshot.get("open_interest") or [],
                                    ("open_interest",))["open_interest"]
        oi_prev = [None] * OI_CHANGE_PERIOD + open_interest[:-OI_CHANGE_PERIOD or None]
        open_interest_change_pct = [
            ((cur - prev) / prev) if (cur is not None and prev not in (None, 0)) else None
            for cur, prev in zip(open_interest, oi_prev)
        ]
        open_interest_zscore = indicators.zscore(open_interest, OI_Z_WINDOW, OI_Z_MIN_PERIODS)
    else:
        open_interest = open_interest_change_pct = open_interest_zscore = [None] * len(candles)

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
            "atr_pct_of_price": atr_pct_of_price[i],
            "atr_percentile": atr_percentile[i],
            "rsi": rsi[i],
            "adx": adx[i],
            "macd": macd_line[i],
            "macd_signal": macd_signal[i],
            "macd_hist": macd_hist[i],
            "volume_zscore": volume_zscore[i],
            "bb_upper": bb_upper[i],
            "bb_lower": bb_lower[i],
            "bb_width_pct": bb_width_pct[i],
            "bb_percent_b": bb_percent_b[i],
            "bb_width_percentile": bb_width_percentile[i],
            "roc_4": roc_4[i],
            "price_distance_ma20": (
                (close - ma20[i]) / ma20[i] if (ma20[i] not in (None, 0)) else None
            ),
            # C9 feeds (series semantics when configured; legacy constants when not).
            "funding_rate": funding_rate[i],
            "funding_zscore": funding_zscore[i],
            "long_liquidation": long_liq[i],
            "short_liquidation": short_liq[i],
            "liquidation_total": liq_total[i],
            "liquidation_spike_ratio": liquidation_spike_ratio[i],
            # Positioning: raw level (evidence only) + the normalized derivatives.
            "open_interest": open_interest[i],
            "open_interest_change_pct": open_interest_change_pct[i],
            "open_interest_zscore": open_interest_zscore[i],
            # No-feed fallbacks, verbatim from the source's absent-feed behavior —
            # and verbatim its RUNTIME ROUTER behavior too: runtime_feature_adapter
            # never passes mark/index frames, so basis is 0 in source live routing.
            "mark_price": close,
            "index_price": close,
            "mark_index_basis_bps": 0.0,
            # HTF context: the last CLOSED higher-timeframe candle's read, or None.
            **{col: htf[col][i] for col in HTF_COLUMNS},
        }
        row["market_regime"] = classify_market_regime(row)
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
