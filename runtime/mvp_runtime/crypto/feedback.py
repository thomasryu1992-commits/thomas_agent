"""C6 feedback analytics — outcome summary, performance report, rendered digest.

Ports the review-only core of the source system's feedback stage
(``outcome_analytics_v2.summarize_outcomes``, the performance-report status/
recommendation/eligibility rules, and the R-distribution) over this port's R-based
outcome records. Like every source feedback module: **this reports, it never acts** —
a recommendation is a string for Thomas, not a state change, and acting on one stays
a human decision (in this runtime: the R9 approval door, C8).

Fields the source averaged from execution telemetry this port does not produce
(slippage, latency, rejection/stale/API-error rates, reconciliation mismatches) are
dropped rather than reported as constant zeros — a zero error rate that means "not
measured" would read as "no errors". The independent-event rule survives the port:
consecutive-cycle re-entries of one setup land minutes apart, so closed-outcome count
inflates with scheduler uptime; eligibility requires enough INDEPENDENT trade events
(cluster gap 120 minutes, the source constant), not just enough rows.

Delivery rides existing paths (the contract's C6 rule): the rendered digest is plain
text for the R4 Telegram channel and the R8 workspace write — this module produces
report + text; the pipeline wiring that sends them is C7.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from runtime.read_only_kernel import integrity

from .. import timeutil
from . import paper

PERFORMANCE_REPORT_VERSION = "performance_report.v1-mvp"

# Source constant: re-entries of the same setup in consecutive scheduler cycles land
# minutes apart; genuinely new setups arrive hours later. Two hours separates them.
TRADE_EVENT_MERGE_GAP_MINUTES = 120

MIN_SAMPLE_SIZE = 3  # source default

# Statuses / recommendations (source vocabulary, review-only).
STATUS_BLOCKED_NO_OUTCOMES = "PERFORMANCE_REPORT_BLOCKED_NO_OUTCOMES"
STATUS_INSUFFICIENT_SAMPLE = "PERFORMANCE_REPORT_REVIEW_ONLY_INSUFFICIENT_SAMPLE"
STATUS_RECORDED = "PERFORMANCE_REPORT_RECORDED"

RECOMMEND_EXPAND_TEST_COVERAGE = "EXPAND_TEST_COVERAGE"
RECOMMEND_REPEAT_IN_PAPER = "REPEAT_IN_PAPER"
RECOMMEND_DROP_CANDIDATE_PROFILE = "DROP_CANDIDATE_PROFILE"
RECOMMEND_CREATE_CANDIDATE_PROFILE_DRAFT = "CREATE_CANDIDATE_PROFILE_DRAFT"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value not in {None, ""} else default
    except (TypeError, ValueError):
        return default


def summarize_outcomes(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Core outcome metrics over R (source math verbatim; drawdown is peak-to-trough
    of cumulative R, reported positive). ``by_strategy`` is this port's analog of the
    source's per-signal summary — the axis C8's generation work needs."""
    rows = [dict(r) for r in records if isinstance(r, Mapping)]
    closed = [r for r in rows if r.get("outcome_closed") is True]
    result_rs = [_f(r.get("result_R")) for r in closed]
    wins = [v for v in result_rs if v > 0]
    losses = [v for v in result_rs if v < 0]
    win_count = len(wins)
    loss_count = len(losses)
    expectancy = sum(result_rs) / len(result_rs) if result_rs else 0.0
    # Realized payoff legs (M4a): the average winning R and the average losing R as a
    # positive magnitude, so avg_win_R / avg_loss_R is the realized reward:risk. Kept
    # separate from expectancy — the ranking wants win-rate and payoff as two axes.
    avg_win_r = sum(wins) / win_count if win_count else 0.0
    avg_loss_r = -sum(losses) / loss_count if loss_count else 0.0
    cumulative = peak = max_dd = 0.0
    for value in result_rs:
        cumulative += value
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)

    by_strategy: dict[str, dict[str, Any]] = {}
    for row in closed:
        key = str(row.get("strategy_id") or "unattributed")
        bucket = by_strategy.setdefault(key, {"closed_count": 0, "win_count": 0, "loss_count": 0, "_sum": 0.0})
        value = _f(row.get("result_R"))
        bucket["closed_count"] += 1
        bucket["_sum"] += value
        bucket["win_count"] += 1 if value > 0 else 0
        bucket["loss_count"] += 1 if value < 0 else 0
    for bucket in by_strategy.values():
        bucket["expectancy"] = round(bucket.pop("_sum") / bucket["closed_count"], 8)

    return {
        "outcome_count": len(rows),
        "closed_count": len(closed),
        "win_count": win_count,
        "loss_count": loss_count,
        "breakeven_count": sum(1 for v in result_rs if v == 0),
        "expectancy": round(expectancy, 8),
        "win_loss_ratio": round(win_count / loss_count, 8) if loss_count else float(win_count),
        "average_R": round(expectancy, 8),
        "avg_win_R": round(avg_win_r, 8),
        "avg_loss_R": round(avg_loss_r, 8),
        "max_drawdown": round(max_dd, 8),
        "by_strategy": by_strategy,
    }


def r_distribution(records: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """The source's R histogram over closed outcomes."""
    values = [_f(r.get("result_R")) for r in records if isinstance(r, Mapping) and r.get("outcome_closed") is True]
    return {
        "lt_minus_1R": sum(1 for v in values if v < -1.0),
        "minus_1R_to_0R": sum(1 for v in values if -1.0 <= v < 0.0),
        "zero_R": sum(1 for v in values if v == 0.0),
        "zero_to_1R": sum(1 for v in values if 0.0 < v < 1.0),
        "one_to_2R": sum(1 for v in values if 1.0 <= v < 2.0),
        "gte_2R": sum(1 for v in values if v >= 2.0),
    }


def count_independent_trade_events(records: Iterable[Mapping[str, Any]]) -> int:
    """Cluster closed outcomes into independent events (source rule, ported to the
    strategy axis): outcomes of the same strategy closing within the merge gap are
    one event; a different strategy, or a gap beyond it, starts a new one."""
    closed = sorted(
        (r for r in records if isinstance(r, Mapping) and r.get("outcome_closed") is True),
        key=lambda r: str(r.get("created_at_utc") or ""),
    )
    events = 0
    last_strategy: str | None = None
    last_time = None
    for row in closed:
        raw = row.get("created_at_utc")
        try:
            moment = timeutil.parse_iso(raw) if isinstance(raw, str) else None
        except ValueError:
            moment = None
        strategy = str(row.get("strategy_id") or "unattributed")
        same_cluster = (
            moment is not None
            and last_time is not None
            and strategy == last_strategy
            and (moment - last_time).total_seconds() / 60.0 <= TRADE_EVENT_MERGE_GAP_MINUTES
        )
        if not same_cluster:
            events += 1
        last_strategy = strategy
        last_time = moment if moment is not None else last_time
    return events


def _status_and_recommendation(
    summary: Mapping[str, Any], *, has_rows: bool, independent_event_count: int, min_sample_size: int
) -> tuple[str, str, list[str]]:
    if not has_rows:
        return STATUS_BLOCKED_NO_OUTCOMES, RECOMMEND_EXPAND_TEST_COVERAGE, ["NO_OUTCOME_RECORDS"]
    blockers: list[str] = []
    closed_count = int(_f(summary.get("closed_count")))
    if closed_count < min_sample_size:
        blockers.append("INSUFFICIENT_CLOSED_OUTCOME_SAMPLE")
        return STATUS_INSUFFICIENT_SAMPLE, RECOMMEND_REPEAT_IN_PAPER, blockers
    if independent_event_count < min_sample_size:
        blockers.append("INSUFFICIENT_INDEPENDENT_TRADE_EVENTS")
        return STATUS_INSUFFICIENT_SAMPLE, RECOMMEND_REPEAT_IN_PAPER, blockers
    expectancy = _f(summary.get("expectancy"))
    if expectancy < 0:
        return STATUS_RECORDED, RECOMMEND_DROP_CANDIDATE_PROFILE, blockers
    if expectancy == 0:
        return STATUS_RECORDED, RECOMMEND_REPEAT_IN_PAPER, blockers
    return STATUS_RECORDED, RECOMMEND_CREATE_CANDIDATE_PROFILE_DRAFT, blockers


def _failure_modes(summary: Mapping[str, Any], *, has_rows: bool) -> list[str]:
    if not has_rows:
        return ["NO_OUTCOME_RECORDS"]
    modes: list[str] = []
    if _f(summary.get("expectancy")) < 0:
        modes.append("NEGATIVE_EXPECTANCY")
    if int(_f(summary.get("closed_count"))) == 0:
        modes.append("NO_CLOSED_OUTCOMES")
    return modes


def build_performance_report(
    outcomes: Iterable[Mapping[str, Any]], *, now: str, min_sample_size: int = MIN_SAMPLE_SIZE
) -> dict[str, Any]:
    """The review-only performance report over all outcomes. Deterministic for a
    given (outcomes, now); the id is seeded from the source outcome ids."""
    rows = [dict(r) for r in outcomes if isinstance(r, Mapping)]
    summary = summarize_outcomes(rows)
    independent_event_count = count_independent_trade_events(rows)
    status, recommendation, blockers = _status_and_recommendation(
        summary, has_rows=bool(rows), independent_event_count=independent_event_count,
        min_sample_size=min_sample_size,
    )
    failure_modes = sorted(dict.fromkeys([*blockers, *_failure_modes(summary, has_rows=bool(rows))]))
    live_candidate_eligible = (
        status == STATUS_RECORDED
        and recommendation == RECOMMEND_CREATE_CANDIDATE_PROFILE_DRAFT
        and not failure_modes
        and int(_f(summary.get("closed_count"))) >= min_sample_size
        and independent_event_count >= min_sample_size
    )
    source_ids = sorted(str(r.get("outcome_id")) for r in rows if r.get("outcome_id"))
    report = {
        "performance_report_version": PERFORMANCE_REPORT_VERSION,
        "status": status,
        "recommendation": recommendation,
        "sample_size": int(_f(summary.get("closed_count"))),
        "independent_event_count": independent_event_count,
        "summary": summary,
        "r_distribution": r_distribution(rows),
        "failure_modes": failure_modes,
        "live_candidate_eligible": live_candidate_eligible,
        "source_outcome_ids": source_ids,
        "created_at_utc": now,
        # Review-only, verbatim from every source feedback module:
        "live_trading_allowed_by_this_module": False,
        "runtime_settings_mutated_by_this_module": False,
    }
    report["performance_report_id"] = integrity.short_id(
        "performance_report", {"version": PERFORMANCE_REPORT_VERSION, "sources": source_ids, "created_at": now}
    )
    return report


def render_report_text(report: Mapping[str, Any]) -> str:
    """Plain-text digest for the R4 Telegram channel / R8 workspace write."""
    summary = report.get("summary") or {}
    lines = [
        "=== paper performance report ===",
        f"status          : {report.get('status')}",
        f"recommendation  : {report.get('recommendation')}",
        f"sample (closed) : {report.get('sample_size')} ({report.get('independent_event_count')} independent events)",
        f"expectancy      : {summary.get('expectancy')}",
        f"win/loss        : {summary.get('win_count')}W {summary.get('loss_count')}L (ratio {summary.get('win_loss_ratio')})",
        f"max drawdown    : {summary.get('max_drawdown')}R",
        f"live candidate  : {'eligible' if report.get('live_candidate_eligible') else 'not eligible'}",
    ]
    modes = report.get("failure_modes") or []
    if modes:
        lines.append(f"failure modes   : {', '.join(modes)}")
    by_strategy = summary.get("by_strategy") or {}
    if by_strategy:
        lines.append("-- by strategy --")
        for strategy_id in sorted(by_strategy):
            b = by_strategy[strategy_id]
            lines.append(
                f"{strategy_id:16}: {b['closed_count']} closed, expectancy {b['expectancy']}, "
                f"{b['win_count']}W {b['loss_count']}L"
            )
    lines.append(f"report id       : {report.get('performance_report_id')}")
    return "\n".join(lines)


def run_paper_performance_report(*, now: str, root=None) -> tuple[dict[str, Any], str]:
    """Read the paper outcome store and produce (report, rendered_text).

    An unreadable store propagates as the typed ``OUTCOME_HISTORY_UNREADABLE`` —
    a report over a silently-truncated history would be a lie with a status field."""
    outcomes = paper.read_outcomes(root)  # raises ToolError when unreadable
    report = build_performance_report(outcomes, now=now)
    return report, render_report_text(report)


__all__ = [
    "build_performance_report",
    "count_independent_trade_events",
    "r_distribution",
    "render_report_text",
    "run_paper_performance_report",
    "summarize_outcomes",
]
