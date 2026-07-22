"""C4 cycle guards — data health + risk limits → the ``allow_new_position`` verdict.

Ports the source system's ``data_health/health_check.py`` and ``risk/risk_guard.py``
as pure functions (no file I/O, no env, no wall clock — the caller injects ``now`` and
the data). The contract's verdict semantics hold: a failed guard **degrades the cycle
to no-new-position mode, it never blocks the cycle** — analysis and feedback still
run; only opening a new paper position is refused. The stricter of the two guards
always wins (`merge_trade_verdict`), and any independent-validation verdict merges the
same way at the pipeline layer (R7 stricter-wins).

Two deliberate deltas from the source, both narrowing:

- The source cross-checked two storage files (market data vs snapshot) for drift; the
  C2 snapshot is a single artifact, so that check has nothing to compare and is
  dropped rather than faked.
- The source's ``is_fallback`` flag marked its synthetic-fallback path; here a failed
  backend produces a **degraded, candle-less** collection instead (C2), which fails
  the candle-count check — same outcome (no trading on fallback data) through the
  stronger signal.

Limit defaults are the source's ``config/settings.py`` values, fixed as constants;
risk history rows use the source's outcome-registry field names (``result_R``,
``outcome_closed``, ``created_at_utc``) — exactly what the C7 import carries — so the
guard reads migrated and native outcomes alike.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .. import timeutil

# data-health defaults (source config/settings.py; TIMEFRAME_MINUTES there is the
# cycle timeframe — the caller passes the snapshot's own timeframe minutes instead).
MIN_CANDLE_COUNT = 50
MAX_ALLOWED_CANDLE_GAP_MULTIPLE = 1.5
BLOCK_SYNTHETIC_DATA_FOR_TRADING = True

# risk-guard defaults (source config/settings.py).
RISK_PER_TRADE = 0.01
DAILY_MAX_LOSS_R = -2.0
WEEKLY_MAX_LOSS_R = -5.0
MAX_CONSECUTIVE_LOSSES = 3
MAX_DRAWDOWN_PCT = -10.0


def _stale_limit_minutes(timeframe_minutes: int) -> int:
    return max(180, 3 * timeframe_minutes)


def _validate_ohlcv(candle: dict[str, Any], idx: int) -> list[str]:
    problems: list[str] = []
    try:
        o = float(candle["open"])
        h = float(candle["high"])
        low = float(candle["low"])
        c = float(candle["close"])
        v = float(candle.get("volume", 0))
    except (KeyError, TypeError, ValueError):
        return [f"invalid_ohlcv_numeric_at_{idx}"]

    if h < max(o, c) or low > min(o, c) or h < low:
        problems.append(f"invalid_ohlc_logic_at_{idx}")
    if v <= 0:
        problems.append(f"non_positive_volume_at_{idx}")
    return problems


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return timeutil.parse_iso(value)
    except (ValueError, TypeError):
        return None


def run_data_health_check(
    snapshot: dict[str, Any], *, now: str, timeframe_minutes: int
) -> dict[str, Any]:
    """Judge one C2 snapshot. Returns the source-shaped health verdict.

    ``allow_trading=False`` refuses new positions for this cycle; it never raises.
    """
    problems: list[str] = []
    candles = snapshot.get("candles") or []

    if len(candles) < MIN_CANDLE_COUNT:
        problems.append("insufficient_candle_count")

    if snapshot.get("is_synthetic") and BLOCK_SYNTHETIC_DATA_FOR_TRADING:
        problems.append("synthetic_data_source_blocks_trading")
    if snapshot.get("degraded"):
        problems.append("degraded_collection_blocks_trading")

    last_time = _parse(candles[-1].get("close_time")) if candles else None
    if last_time is None:
        problems.append("missing_latest_candle_time")
    else:
        age_minutes = (timeutil.parse_iso(now) - last_time).total_seconds() / 60
        if age_minutes > _stale_limit_minutes(timeframe_minutes):
            problems.append("stale_market_data")

    parsed_times = [t for t in (_parse(c.get("open_time")) for c in candles) if t is not None]
    if len(parsed_times) >= 2:
        expected = timedelta(minutes=timeframe_minutes).total_seconds()
        max_gap = expected * MAX_ALLOWED_CANDLE_GAP_MULTIPLE
        for i in range(1, len(parsed_times)):
            gap = (parsed_times[i] - parsed_times[i - 1]).total_seconds()
            if gap > max_gap:
                problems.append(f"candle_gap_detected_at_index_{i}")
                break
            if gap <= 0:
                problems.append(f"non_increasing_timestamp_at_index_{i}")
                break

    for idx, candle in enumerate(candles[-min(len(candles), 200):]):
        problems.extend(_validate_ohlcv(candle, idx))

    allow_trading = not problems
    return {
        "created_at": now,
        "status": "UNHEALTHY" if problems else "HEALTHY",
        "allow_trading": allow_trading,
        "problems": sorted(set(problems)),
        "is_synthetic": bool(snapshot.get("is_synthetic", False)),
        "candle_count": len(candles),
        "latest_candle_time": candles[-1].get("close_time") if candles else None,
    }


def _closed_rows(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Closed outcomes in the source registry's shape, sorted by close time."""
    rows = [
        {
            "pnl_r": float(r.get("result_R", 0.0) or 0.0),
            "exit_time": r.get("created_at_utc"),
        }
        for r in outcomes
        if isinstance(r, dict) and r.get("outcome_closed") is True
    ]
    return sorted(rows, key=lambda x: str(x.get("exit_time", "")))


def _pnl_since(rows: list[dict[str, Any]], start_time: datetime) -> float:
    total = 0.0
    for row in rows:
        t = _parse(row.get("exit_time"))
        if t and t >= start_time:
            total += row["pnl_r"]
    return total


def _consecutive_losses(rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in reversed(rows):
        if row["pnl_r"] < 0:
            count += 1
        else:
            break
    return count


def _drawdowns_r(rows: list[dict[str, Any]]) -> tuple[float, float]:
    """(max historical drawdown, CURRENT drawdown from peak), both <= 0, in R.

    The breaker acts on the CURRENT drawdown so it unlatches when equity recovers
    to a new peak — the historical max is reporting only (the source's B-fix)."""
    equity = peak = max_dd = 0.0
    for row in rows:
        equity += row["pnl_r"]
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return max_dd, equity - peak


def _drawdown_limit_r() -> float:
    """MAX_DRAWDOWN_PCT (equity %) mapped to R via risk-per-trade: -10% at 1% = 10R."""
    risk_pct = RISK_PER_TRADE if RISK_PER_TRADE > 0 else 0.01
    return (abs(MAX_DRAWDOWN_PCT) / 100.0) / risk_pct


def risk_guard_unreadable(error: str, *, now: str) -> dict[str, Any]:
    """The fail-closed verdict when the outcome history cannot be read.

    No risk history means the loss limits would be computed over nothing, which is
    fail-open — so the guard refuses new positions, loudly (the source's B-4 rule).
    The C5 store wires its read failure here rather than passing an empty list."""
    return {
        "created_at": now,
        "status": "BLOCK_NEW_POSITION",
        "allow_new_position": False,
        "daily_pnl_r": 0.0,
        "weekly_pnl_r": 0.0,
        "consecutive_losses": 0,
        "drawdown_r": 0.0,
        "problems": ["risk_history_unreadable"],
        "risk_history_error": str(error),
    }


def run_risk_guard(outcomes: list[dict[str, Any]], *, now: str) -> dict[str, Any]:
    """Judge the closed-outcome history against the loss limits. Never raises.

    ``outcomes`` is the caller-loaded registry content (C5 native or C7 imported);
    an *unreadable* registry must go through :func:`risk_guard_unreadable` instead —
    an empty list here honestly means "no closed trades yet", which allows trading.
    """
    rows = _closed_rows(outcomes)
    now_dt = timeutil.parse_iso(now)
    day_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=day_start.weekday())

    daily_pnl_r = _pnl_since(rows, day_start)
    weekly_pnl_r = _pnl_since(rows, week_start)
    consecutive_losses = _consecutive_losses(rows)
    max_drawdown_r, current_drawdown_r = _drawdowns_r(rows)

    problems: list[str] = []
    if daily_pnl_r <= DAILY_MAX_LOSS_R:
        problems.append("daily_loss_limit_breached")
    if weekly_pnl_r <= WEEKLY_MAX_LOSS_R:
        problems.append("weekly_loss_limit_breached")
    if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
        problems.append("max_consecutive_losses_breached")
    if current_drawdown_r <= -_drawdown_limit_r():
        problems.append("max_drawdown_proxy_breached")

    allow_new_position = not problems
    return {
        "created_at": now,
        "status": "NORMAL" if allow_new_position else "BLOCK_NEW_POSITION",
        "allow_new_position": allow_new_position,
        "daily_pnl_r": round(daily_pnl_r, 4),
        "weekly_pnl_r": round(weekly_pnl_r, 4),
        "consecutive_losses": consecutive_losses,
        "drawdown_r": round(current_drawdown_r, 4),
        "max_drawdown_r": round(max_drawdown_r, 4),
        "drawdown_limit_r": round(_drawdown_limit_r(), 4),
        "problems": problems,
    }


def merge_trade_verdict(health: dict[str, Any], risk: dict[str, Any]) -> dict[str, Any]:
    """Stricter-wins merge of the two guards — the cycle's single trade verdict.

    ``allow_new_position`` only when BOTH guards allow; the reason codes of every
    refusing guard ride along, so the audit trail says exactly why a cycle ran in
    no-new-position mode. The cycle itself is never blocked here (DEGRADED, not
    halted — the source's fail-closed semantics and this repo's R3/R7.2 posture).
    """
    allow = bool(health.get("allow_trading")) and bool(risk.get("allow_new_position"))
    problems = sorted({*health.get("problems", []), *risk.get("problems", [])})
    return {
        "allow_new_position": allow,
        "status": "ALLOW" if allow else "NO_NEW_POSITION",
        "problems": problems,
        "data_health": health,
        "risk_guard": risk,
    }
