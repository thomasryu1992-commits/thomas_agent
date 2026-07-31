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

Limit defaults are the source's ``config/settings.py`` values, fixed as constants; a
registered per-machine record may override them within the relaxation bounds below
(``risk_limits``, which owns the record — this module owns the numbers). Risk history rows
use the source's outcome-registry field names (``result_R``, ``outcome_closed``,
``created_at_utc``) — exactly what the C7 import carries — so the guard reads migrated and
native outcomes alike.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping

from .. import timeutil
from . import cost

# data-health defaults (source config/settings.py; TIMEFRAME_MINUTES there is the
# cycle timeframe — the caller passes the snapshot's own timeframe minutes instead).
MIN_CANDLE_COUNT = 50
MAX_ALLOWED_CANDLE_GAP_MULTIPLE = 1.5
BLOCK_SYNTHETIC_DATA_FOR_TRADING = True

# risk-guard defaults (source config/settings.py). These stay the behaviour of an
# unconfigured runtime: with nothing registered, the guard judges on exactly these numbers.
RISK_PER_TRADE = 0.01
DAILY_MAX_LOSS_R = -2.0
WEEKLY_MAX_LOSS_R = -5.0
MAX_CONSECUTIVE_LOSSES = 3
MAX_DRAWDOWN_PCT = -10.0

# Relaxation bounds — how far a registered override may WIDEN each breaker. Changing a
# number here needs Thomas approval; registering a config inside them does not, which is the
# whole point of the split. A config outside a bound is REFUSED, never clamped — the posture
# `live_budget` takes on its 200 USDT ceiling, for the same reason: a clamp silently trades
# under limits nobody chose.
#
# Tightening is deliberately unbounded. A stricter breaker needs no ceiling, so the bounds
# are one-sided and every one of them sits on the loosening side of the default.
MAX_RISK_PER_TRADE = 0.02          # 2× default; matches factory.MAX_RISK_PER_TRADE_R = 2.0
MIN_DAILY_MAX_LOSS_R = -6.0        # 3× default
MIN_WEEKLY_MAX_LOSS_R = -15.0      # 3× default
MAX_MAX_CONSECUTIVE_LOSSES = 10
MIN_MAX_DRAWDOWN_PCT = -25.0

# Verdict problem codes for a limits set that must not be traded on.
RISK_LIMITS_INVALID_PROBLEM = "risk_limits_invalid"
RISK_LIMITS_UNUSABLE_PROBLEM = "risk_limits_unusable"

# ``RiskLimits.source`` values. "default" is the module constants; "registered" is a verified,
# in-window record. There is no third value: anything else failed closed before reaching here.
SOURCE_DEFAULT = "default"
SOURCE_REGISTERED = "registered"


@dataclass(frozen=True)
class RiskLimits:
    """The five risk-breaker limits as one injectable value, carrying its own provenance.

    Defaults are the module constants, so ``RiskLimits()`` is exactly the behaviour these
    limits had while they were hardcoded. ``problems()`` is the single place the relaxation
    bounds are enforced, and :func:`run_risk_guard` calls it on every set it is handed — so a
    caller that skips the record layer cannot trade on out-of-bounds limits either.

    ``limits_id`` / ``record_sha256`` are metadata: the id and hash of the record these numbers
    came from, so a cycle's audit trail names the exact record that judged it. They are absent
    for the defaults, which are pinned by the code and need no record to prove.
    """

    risk_per_trade: float = RISK_PER_TRADE
    daily_max_loss_r: float = DAILY_MAX_LOSS_R
    weekly_max_loss_r: float = WEEKLY_MAX_LOSS_R
    max_consecutive_losses: int = MAX_CONSECUTIVE_LOSSES
    max_drawdown_pct: float = MAX_DRAWDOWN_PCT
    source: str = SOURCE_DEFAULT
    limits_id: str | None = None
    record_sha256: str | None = None

    def problems(self) -> list[str]:
        """Every bound this set violates, named. Empty means safe to judge trades on.

        Returns problems rather than raising: the guard never raises (a breaker that throws is
        a breaker that stops running), so the refusal has to be a value. The record layer
        turns a non-empty list into its typed ``ToolError``.
        """
        out: list[str] = []
        if not (0 < self.risk_per_trade <= MAX_RISK_PER_TRADE):
            out.append(f"risk_per_trade {self.risk_per_trade} outside (0, {MAX_RISK_PER_TRADE}]")
        if not (MIN_DAILY_MAX_LOSS_R <= self.daily_max_loss_r < 0):
            out.append(f"daily_max_loss_r {self.daily_max_loss_r} outside [{MIN_DAILY_MAX_LOSS_R}, 0)")
        if not (MIN_WEEKLY_MAX_LOSS_R <= self.weekly_max_loss_r < 0):
            out.append(f"weekly_max_loss_r {self.weekly_max_loss_r} outside [{MIN_WEEKLY_MAX_LOSS_R}, 0)")
        # A daily limit looser than the weekly one is incoherent, not merely lax: the weekly
        # breaker would trip first in every history, so the daily number would bound nothing
        # while still reading like a limit. Refused rather than silently inert.
        if self.daily_max_loss_r < self.weekly_max_loss_r:
            out.append(
                f"daily_max_loss_r {self.daily_max_loss_r} is looser than weekly_max_loss_r "
                f"{self.weekly_max_loss_r}"
            )
        if not (1 <= self.max_consecutive_losses <= MAX_MAX_CONSECUTIVE_LOSSES):
            out.append(
                f"max_consecutive_losses {self.max_consecutive_losses} outside "
                f"[1, {MAX_MAX_CONSECUTIVE_LOSSES}]"
            )
        if not (MIN_MAX_DRAWDOWN_PCT <= self.max_drawdown_pct < 0):
            out.append(f"max_drawdown_pct {self.max_drawdown_pct} outside [{MIN_MAX_DRAWDOWN_PCT}, 0)")
        return out

    def as_record(self) -> dict[str, Any]:
        """The limits as they ride along in a verdict — what judged this cycle, and from where."""
        return {
            "risk_per_trade": self.risk_per_trade,
            "daily_max_loss_r": self.daily_max_loss_r,
            "weekly_max_loss_r": self.weekly_max_loss_r,
            "max_consecutive_losses": self.max_consecutive_losses,
            "max_drawdown_pct": self.max_drawdown_pct,
            "drawdown_limit_r": round(_drawdown_limit_r(self), 4),
            "source": self.source,
            "limits_id": self.limits_id,
            "record_sha256": self.record_sha256,
        }


DEFAULT_RISK_LIMITS = RiskLimits()


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
    """Closed outcomes in the source registry's shape, sorted by close time.

    ``pnl_r`` is **net of costs wherever the row can price them** (``cost.outcome_net_r``),
    falling back to the stored ``result_R``. The breakers below are stated in R against a
    fixed risk fraction, i.e. they are a claim about equity, and paper ``result_R`` never had
    fees or slippage taken out of it — so a day of trades that lost real money could read as
    flat here and leave the daily breaker clear. Netting moves every one of these limits in the
    conservative direction (a net figure is never larger than its gross), which is why it needs
    no new threshold and no re-authorization: the numbers are the ones already registered, now
    measured against what a trade actually costs.
    """
    rows = [
        {
            "pnl_r": _judged_r(r),
            "exit_time": r.get("created_at_utc"),
        }
        for r in outcomes
        if isinstance(r, dict) and r.get("outcome_closed") is True
    ]
    return sorted(rows, key=lambda x: str(x.get("exit_time", "")))


def _judged_r(outcome: Mapping[str, Any]) -> float:
    """One outcome's R after costs, or its stored R when it cannot be priced.

    A missing ``result_R`` still reads as ``0.0`` — a BREAKEVEN — exactly as before. That is
    load-bearing rather than sloppy: ``cycle`` relies on it and drops R-less live rows upstream
    (see ``live_outcomes_for_analysis``) precisely because a loss read as breakeven would
    SHORTEN a loss streak. Changing it here would move that decision into two places.
    """
    net = cost.outcome_net_r(outcome)
    return float(net) if net is not None else float(outcome.get("result_R", 0.0) or 0.0)


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


def _drawdown_limit_r(limits: RiskLimits = DEFAULT_RISK_LIMITS) -> float:
    """max_drawdown_pct (equity %) mapped to R via risk-per-trade: -10% at 1% = 10R."""
    risk_pct = limits.risk_per_trade if limits.risk_per_trade > 0 else 0.01
    return (abs(limits.max_drawdown_pct) / 100.0) / risk_pct


def risk_guard_unavailable(
    problem: str, error: str, *, now: str, limits: RiskLimits | None = None
) -> dict[str, Any]:
    """The fail-closed verdict when the guard cannot honestly judge the risk state.

    One shape for every "I could not evaluate the breakers" cause, because they all have the
    same correct consequence: refuse new positions, loudly, naming the cause. ``problem`` is
    the reason code that rides into the cycle record; ``error`` is the detail behind it.
    """
    return {
        "created_at": now,
        "status": "BLOCK_NEW_POSITION",
        "allow_new_position": False,
        "daily_pnl_r": 0.0,
        "weekly_pnl_r": 0.0,
        "consecutive_losses": 0,
        "drawdown_r": 0.0,
        "problems": [problem],
        "risk_history_error": str(error),
        "limits": (limits or DEFAULT_RISK_LIMITS).as_record(),
    }


def risk_guard_unreadable(error: str, *, now: str) -> dict[str, Any]:
    """The fail-closed verdict when the outcome history cannot be read.

    No risk history means the loss limits would be computed over nothing, which is
    fail-open — so the guard refuses new positions, loudly (the source's B-4 rule).
    The C5 store wires its read failure here rather than passing an empty list."""
    return risk_guard_unavailable("risk_history_unreadable", error, now=now)


def run_risk_guard(
    outcomes: list[dict[str, Any]], *, now: str, limits: RiskLimits | None = None
) -> dict[str, Any]:
    """Judge the closed-outcome history against the loss limits. Never raises.

    ``outcomes`` is the caller-loaded registry content (C5 native or C7 imported);
    an *unreadable* registry must go through :func:`risk_guard_unreadable` instead —
    an empty list here honestly means "no closed trades yet", which allows trading.

    ``limits`` defaults to the module constants. An out-of-bounds set does not raise and is
    never clamped: it fails the guard closed, so the only two outcomes of handing this
    function bad limits are "refused" and "refused with a reason", never "traded anyway".
    """
    limits = limits or DEFAULT_RISK_LIMITS
    bound_problems = limits.problems()
    if bound_problems:
        return risk_guard_unavailable(
            RISK_LIMITS_INVALID_PROBLEM, "; ".join(bound_problems), now=now, limits=limits
        )

    rows = _closed_rows(outcomes)
    now_dt = timeutil.parse_iso(now)
    day_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=day_start.weekday())

    daily_pnl_r = _pnl_since(rows, day_start)
    weekly_pnl_r = _pnl_since(rows, week_start)
    consecutive_losses = _consecutive_losses(rows)
    max_drawdown_r, current_drawdown_r = _drawdowns_r(rows)

    problems: list[str] = []
    if daily_pnl_r <= limits.daily_max_loss_r:
        problems.append("daily_loss_limit_breached")
    if weekly_pnl_r <= limits.weekly_max_loss_r:
        problems.append("weekly_loss_limit_breached")
    if consecutive_losses >= limits.max_consecutive_losses:
        problems.append("max_consecutive_losses_breached")
    if current_drawdown_r <= -_drawdown_limit_r(limits):
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
        "drawdown_limit_r": round(_drawdown_limit_r(limits), 4),
        "problems": problems,
        # Which limits judged this cycle, and which record they came from. A verdict that
        # records only its outcome cannot be re-checked later against the numbers in force at
        # the time — and those numbers are now configurable, so they have to travel with it.
        "limits": limits.as_record(),
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
