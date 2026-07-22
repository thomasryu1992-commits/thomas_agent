"""C11 performance digest — is the edge decaying? (source port, review-only)

The performance report blends all history into one mean, and the longer the registry
grows the better that blend hides a dead edge — each new week is a smaller share of
it. This buckets closed outcomes into ISO weeks and calendar months and compares the
most recent *complete* period against the one before it. Completeness is the point:
judging a half-finished week against a full one manufactures a decline out of the
missing days.

Review-only, like every feedback module: it reports a trend; acting on one stays a
human decision.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from runtime.read_only_kernel import integrity

from .. import timeutil
from .feedback import summarize_outcomes

PERFORMANCE_DIGEST_VERSION = "performance_digest.v1"

WEEKLY = "weekly"
MONTHLY = "monthly"
DEFAULT_MIN_BUCKET_SAMPLE = 5
TREND_THRESHOLD_R = 0.1

IMPROVING = "IMPROVING"
DEGRADING = "DEGRADING"
STABLE = "STABLE"
INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value not in {None, ""} else default
    except (TypeError, ValueError):
        return default


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return timeutil.parse_iso(value)
    except (TypeError, ValueError):
        return None


def _week_key(moment: datetime) -> str:
    iso = moment.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _month_key(moment: datetime) -> str:
    return f"{moment.year:04d}-{moment.month:02d}"


def _week_end(key: str) -> datetime:
    year, week = int(key[:4]), int(key[6:])
    monday = date.fromisocalendar(year, week, 1)
    return datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc) + timedelta(days=7)


def _month_end(key: str) -> datetime:
    year, month = int(key[:4]), int(key[5:])
    return datetime(year + (month // 12), (month % 12) + 1, 1, tzinfo=timezone.utc)


def _bucket_summary(rows: list[Mapping[str, Any]], *, period: str, period_end: datetime, now: datetime) -> dict[str, Any]:
    summary = summarize_outcomes(rows)
    closed = [r for r in rows if r.get("outcome_closed") is True]
    closed_count = int(summary["closed_count"])
    return {
        "period": period,
        # An open period is still accumulating; comparing it to a closed one
        # measures the calendar, not the strategy.
        "complete": period_end <= now,
        "closed_count": closed_count,
        "win_count": summary["win_count"],
        "loss_count": summary["loss_count"],
        "breakeven_count": summary["breakeven_count"],
        "win_rate": round(summary["win_count"] / closed_count, 6) if closed_count else 0.0,
        "expectancy_R": summary["expectancy"],
        "total_R": round(sum(_f(r.get("result_R")) for r in closed), 6),
        "max_drawdown_R": summary["max_drawdown"],
    }


def _buckets(rows: list[Mapping[str, Any]], *, period: str, now: datetime) -> list[dict[str, Any]]:
    key_of = _week_key if period == WEEKLY else _month_key
    end_of = _week_end if period == WEEKLY else _month_end
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        moment = _parse(row.get("created_at_utc"))
        if moment is None:
            continue
        groups.setdefault(key_of(moment), []).append(row)
    return [
        _bucket_summary(values, period=key, period_end=end_of(key), now=now)
        for key, values in sorted(groups.items())
    ]


def _trend(buckets: list[dict[str, Any]], *, min_bucket_sample: int) -> dict[str, Any]:
    """Compare the last two complete periods, or say why we cannot."""
    complete = [b for b in buckets if b["complete"]]
    base = {
        "verdict": INSUFFICIENT_SAMPLE,
        "latest_period": None,
        "previous_period": None,
        "expectancy_delta_R": None,
        "reason": None,
    }
    if len(complete) < 2:
        return {**base, "reason": "fewer_than_two_complete_periods"}

    latest, previous = complete[-1], complete[-2]
    thin = [b["period"] for b in (latest, previous) if b["closed_count"] < min_bucket_sample]
    if thin:
        return {
            **base,
            "latest_period": latest["period"],
            "previous_period": previous["period"],
            "reason": f"below_min_sample: {', '.join(thin)}",
        }

    delta = latest["expectancy_R"] - previous["expectancy_R"]
    if delta > TREND_THRESHOLD_R:
        verdict = IMPROVING
    elif delta < -TREND_THRESHOLD_R:
        verdict = DEGRADING
    else:
        verdict = STABLE
    return {
        "verdict": verdict,
        "latest_period": latest["period"],
        "previous_period": previous["period"],
        "latest_expectancy_R": latest["expectancy_R"],
        "previous_expectancy_R": previous["expectancy_R"],
        "expectancy_delta_R": round(delta, 6),
        "reason": None,
    }


def build_performance_digest(
    outcomes: Iterable[Mapping[str, Any]],
    *,
    now: str,
    min_bucket_sample: int = DEFAULT_MIN_BUCKET_SAMPLE,
) -> dict[str, Any]:
    """Bucket closed outcomes by week and month, and read the recent trend."""
    now_dt = _parse(now) or datetime.now(timezone.utc).replace(microsecond=0)
    rows = [dict(r) for r in outcomes if isinstance(r, Mapping)]

    weekly = _buckets(rows, period=WEEKLY, now=now_dt)
    monthly = _buckets(rows, period=MONTHLY, now=now_dt)
    overall = summarize_outcomes(rows)

    digest = {
        "performance_digest_version": PERFORMANCE_DIGEST_VERSION,
        "created_at_utc": now,
        "min_bucket_sample": int(min_bucket_sample),
        "trend_threshold_R": TREND_THRESHOLD_R,
        "outcome_count": overall["outcome_count"],
        "closed_count": overall["closed_count"],
        # Rows whose created_at_utc could not be read land in no period; say so
        # rather than letting them silently vanish from every bucket.
        "unbucketed_count": sum(1 for r in rows if _parse(r.get("created_at_utc")) is None),
        "overall_expectancy_R": overall["expectancy"],
        "weekly": weekly,
        "monthly": monthly,
        "weekly_trend": _trend(weekly, min_bucket_sample=min_bucket_sample),
        "monthly_trend": _trend(monthly, min_bucket_sample=min_bucket_sample),
        "review_only": True,
        "live_trading_allowed_by_this_module": False,
    }
    digest["performance_digest_id"] = integrity.short_id(
        "performance_digest", {"version": PERFORMANCE_DIGEST_VERSION, "n": str(len(rows)), "at": now}
    )
    return digest


def render_digest_text(digest: Mapping[str, Any]) -> str:
    """Plain-text digest lines for the dashboard / Telegram."""
    lines = ["=== performance digest ==="]
    for label, trend_key in (("weekly", "weekly_trend"), ("monthly", "monthly_trend")):
        trend = digest.get(trend_key) or {}
        if trend.get("verdict") == INSUFFICIENT_SAMPLE:
            lines.append(f"{label:8}: {INSUFFICIENT_SAMPLE} ({trend.get('reason')})")
        else:
            lines.append(
                f"{label:8}: {trend.get('verdict')} — {trend.get('previous_period')} "
                f"{trend.get('previous_expectancy_R')}R -> {trend.get('latest_period')} "
                f"{trend.get('latest_expectancy_R')}R (Δ {trend.get('expectancy_delta_R')}R)"
            )
    lines.append(
        f"overall : {digest.get('closed_count')} closed, expectancy {digest.get('overall_expectancy_R')}R"
        + (f", unbucketed {digest.get('unbucketed_count')}" if digest.get("unbucketed_count") else "")
    )
    return "\n".join(lines)
