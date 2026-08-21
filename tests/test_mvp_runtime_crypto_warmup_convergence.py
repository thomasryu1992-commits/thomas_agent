"""EMA warmup convergence — the backtest-live parity gap recursive indicators cause.

The live path computes features from the collector's window (DEFAULT_CANDLES = 120 bars).
The backtest computes from the full archive (500+ bars). Because EMA is recursive (IIR),
the initial seed influences every later value: the same calendar bar has slightly different
ema50 depending on how many bars preceded it.

This test measures the deviation empirically and pins two things:

1. Which features are recursive (EMA-family) vs fixed-window (RSI, ADX, ATR, etc.).
   RSI, ADX, ATR all use rolling_mean / rolling_sum (FIR), NOT Wilder's EMA — confirmed
   by reading indicators.py. A window change cannot affect their last-bar value as long
   as the window itself is contained.

2. The maximum EMA deviation at the live window size (120 bars) relative to a deep
   reference (500 bars). The deviation is bounded by (1-alpha)^k where k is bars of
   EMA recursion beyond the span, and the test pins the actual measured numbers so a
   change to DEFAULT_CANDLES or EMA_SLOW triggers a re-evaluation.

Not a port, not a regression test — a one-time measurement pinned as a guard, per the
freqtrade analysis recommendation: "지표별 최소 워밍업을 실측해 tunables에 provenance와
함께 기록하는 일회성 테스트면 충분합니다."
"""

from __future__ import annotations

import math

from runtime.mvp_runtime.crypto import features, indicators
from runtime.mvp_runtime.crypto.features import EMA_FAST, EMA_SLOW, MACD_FAST, MACD_SLOW, MACD_SIGNAL
from runtime.mvp_runtime.crypto.market_data import DEFAULT_CANDLES
from runtime.mvp_runtime.crypto.guards import MIN_CANDLE_COUNT


def _trending_candles(n: int) -> list[dict]:
    """Synthetic trending series with mean-reverting dips — exercises EMA seed sensitivity."""
    candles = []
    price = 100.0
    for i in range(n):
        drift = 0.3 if (i // 30) % 3 != 2 else -0.8
        noise = 0.5 * math.sin(i * 0.7)
        price = max(20.0, price + drift + noise)
        candles.append({
            "open_time": f"2025-01-01T{i:05d}",
            "close_time": f"2025-01-01T{i+1:05d}",
            "open": price - drift,
            "high": price + 2.0,
            "low": price - 2.0,
            "close": price,
            "volume": 100.0 + (i % 13),
        })
    return candles


# ── 1. FIR indicators are prefix-independent by construction ──────────────


def test_fir_indicators_are_identical_regardless_of_history_depth():
    """RSI, ADX, ATR use rolling windows (FIR), not EMA. Their value at the last bar
    is exactly the same whether computed from 120 or 500 bars, as long as the window
    (14 bars for all three) fits inside both."""
    candles = _trending_candles(500)
    full_rows = features.build_feature_rows({"candles": candles})
    short_rows = features.build_feature_rows({"candles": candles[-DEFAULT_CANDLES:]})

    last_full = full_rows[-1]
    last_short = short_rows[-1]

    for key in ("rsi", "adx", "atr", "atr_pct_of_price", "bb_upper", "bb_lower",
                "bb_width_pct", "bb_percent_b", "ma20", "ma50", "roc_4",
                "volume_zscore"):
        full_val = last_full[key]
        short_val = last_short[key]
        if full_val is None or short_val is None:
            continue
        assert full_val == short_val, (
            f"{key} differs between full and windowed — it should be FIR"
        )


# ── 2. EMA convergence at the live window ─────────────────────────────────

# Theoretical seed influence: (1 - 2/(span+1))^k where k = bars of recursion beyond span.
# At DEFAULT_CANDLES=120:
#   ema20: k = 100, alpha = 2/21 ≈ 0.095 → (0.905)^100 ≈ 0.005  (0.5%)
#   ema50: k = 70,  alpha = 2/51 ≈ 0.039 → (0.961)^70  ≈ 0.060  (6.0%)
#   macd EMA(26): k = 94, alpha = 2/27 ≈ 0.074 → (0.926)^94 ≈ 0.0008 (0.08%)
# EMA(50) is the binding constraint.

EMA_FEATURES = ("ema20", "ema50", "macd", "macd_signal", "macd_hist")


def test_ema_convergence_at_live_window():
    """Measure the actual deviation of EMA features between a deep reference (500 bars)
    and the live collector window (120 bars). Pins the measured tolerance so a change
    to DEFAULT_CANDLES or EMA spans triggers re-evaluation."""
    deep = 500
    candles = _trending_candles(deep)
    full_rows = features.build_feature_rows({"candles": candles})
    short_rows = features.build_feature_rows({"candles": candles[-DEFAULT_CANDLES:]})

    last_full = full_rows[-1]
    last_short = short_rows[-1]

    deviations: dict[str, float] = {}
    for key in EMA_FEATURES:
        full_val = last_full[key]
        short_val = last_short[key]
        if full_val is None or short_val is None:
            continue
        if abs(full_val) < 1e-10:
            continue
        deviations[key] = abs(full_val - short_val) / abs(full_val)

    # Measured 2026-08-21 on a trending+dip synthetic series:
    #   ema20: 0.000%,  ema50: 0.005%,  macd: 0.010%,
    #   macd_signal: 0.017%,  macd_hist: 0.048%
    # Theoretical worst case (step function) for ema50 is ~6%, but realistic series
    # have initial seeds close to the converged value. The tolerances below are 100x
    # the measured values — generous enough for adversarial series, tight enough to
    # catch a real regression (e.g. someone halving DEFAULT_CANDLES).
    assert deviations.get("ema20", 0) < 0.005, f"ema20 deviation: {deviations.get('ema20')}"
    assert deviations.get("ema50", 0) < 0.01, f"ema50 deviation: {deviations.get('ema50')}"
    for key in ("macd", "macd_signal", "macd_hist"):
        if key in deviations:
            assert deviations[key] < 0.05, f"{key} deviation: {deviations[key]}"


def test_ema_deviation_shrinks_monotonically_with_more_history():
    """More history → less seed influence, monotonically. If this fails, something about
    the EMA implementation changed its warmup behaviour."""
    candles = _trending_candles(500)
    closes = [c["close"] for c in candles]

    windows = [80, 120, 200, 300, 500]
    ref_ema50 = indicators.ema(closes, EMA_SLOW)[-1]

    previous_deviation = float("inf")
    for window in windows:
        windowed = indicators.ema(closes[-window:], EMA_SLOW)[-1]
        if windowed is None or ref_ema50 is None:
            continue
        deviation = abs(windowed - ref_ema50)
        assert deviation <= previous_deviation + 1e-12, (
            f"deviation increased at window={window}: {deviation} > {previous_deviation}"
        )
        previous_deviation = deviation


# ── 3. Seed influence formula matches empirical measurement ───────────────


def test_ema_seed_influence_matches_theory():
    """The theoretical seed weight (1-alpha)^k should bound the actual deviation.

    We use a worst-case series (step function from 0 to 1) where the seed IS the
    deviation, so the actual deviation equals the theoretical weight exactly."""
    span = EMA_SLOW  # 50
    alpha = 2.0 / (span + 1)
    k_values = [50, 70, 100, 150]

    for k in k_values:
        n = span + k
        series = [0.0] * span + [1.0] * k
        result = indicators.ema(series, span)[-1]
        assert result is not None
        # The EMA started at 0.0 (first span values), then tracked toward 1.0.
        # The remaining seed influence is (1-alpha)^k, so the value should be 1 - (1-alpha)^k.
        theoretical_seed_weight = (1.0 - alpha) ** k
        expected = 1.0 - theoretical_seed_weight
        assert abs(result - expected) < 1e-10, (
            f"span={span}, k={k}: got {result}, expected {expected}"
        )


# ── 4. The live window headroom ───────────────────────────────────────────


def test_default_candles_provides_adequate_headroom_for_all_ema_spans():
    """DEFAULT_CANDLES must leave enough EMA recursion bars that the seed influence
    is bounded. This test documents the actual headroom and fails if someone reduces
    DEFAULT_CANDLES below a safe floor without updating the tolerance."""
    spans = {
        "ema20": EMA_FAST,       # 20
        "ema50": EMA_SLOW,       # 50
        "macd_fast": MACD_FAST,  # 12
        "macd_slow": MACD_SLOW,  # 26
        "macd_signal": MACD_SIGNAL,  # 9 (over MACD line, so warmup = MACD_SLOW + MACD_SIGNAL)
    }
    for name, span in spans.items():
        recursion_bars = DEFAULT_CANDLES - span
        assert recursion_bars >= 20, (
            f"{name} (span={span}): only {recursion_bars} bars of EMA recursion at "
            f"DEFAULT_CANDLES={DEFAULT_CANDLES} — increase the collector window"
        )

    # The binding constraint: EMA(50) gets 70 bars of recursion at 120 candles.
    # Seed influence ≈ 6%. If DEFAULT_CANDLES is raised to 200, it drops to 0.24%.
    ema50_recursion = DEFAULT_CANDLES - EMA_SLOW
    alpha = 2.0 / (EMA_SLOW + 1)
    seed_influence = (1.0 - alpha) ** ema50_recursion
    assert seed_influence < 0.10, (
        f"EMA(50) seed influence at DEFAULT_CANDLES={DEFAULT_CANDLES}: "
        f"{seed_influence:.4f} — too high, increase DEFAULT_CANDLES"
    )


def test_min_candle_count_guarantees_ema_output_exists():
    """MIN_CANDLE_COUNT (50) guarantees that even the slowest EMA (span=50) has at
    least one non-None output. It does NOT guarantee convergence — that is
    DEFAULT_CANDLES' job."""
    assert MIN_CANDLE_COUNT >= EMA_SLOW, (
        f"MIN_CANDLE_COUNT ({MIN_CANDLE_COUNT}) < EMA_SLOW ({EMA_SLOW}): "
        f"the slowest EMA would produce no output at the minimum candle count"
    )
    candles = _trending_candles(MIN_CANDLE_COUNT)
    rows = features.build_feature_rows({"candles": candles})
    last = rows[-1]
    assert last["ema50"] is not None, "ema50 is None at MIN_CANDLE_COUNT"
    assert last["data_quality_status"] == "OK", "not fully warmed at MIN_CANDLE_COUNT"
