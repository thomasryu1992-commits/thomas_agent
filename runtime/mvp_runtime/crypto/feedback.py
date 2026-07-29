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
from ..coerce import as_float as _f
from .cost import FUNDING_INTERVALS_PER_DAY, CostModel, apply_cost_model
from .market_data import TIMEFRAMES

PERFORMANCE_REPORT_VERSION = "performance_report.v1-mvp"

# --- what the paper record measures, and what Gate 0 needs it to ------------------------------
#
# Paper `result_R` is measured on INTENDED fills and carries no costs. That is deliberate and
# stays that way: `paper.settle_trade_plan` is the same code the factory replays, the risk
# guard's thresholds (`guards.DAILY_MAX_LOSS_R` and friends) are calibrated on it, and the
# imported crypto_AI_System history is gross too. Re-costing the durable store would move all
# three at once and rewrite evidence that is meant to be append-only.
#
# What could not stay is the READING. `live_candidate_eligible` — the machine-readable Gate 0
# flag, and the operator checklist's first line ("paper trading by this runtime shows positive
# expectancy over a sustained window") — turned on `expectancy > 0` computed on that cost-free
# figure. So the gate that authorizes real money was answering a question about a venue that
# charges nothing: no fee, no spread, and (since the funding work) no carry. The costed number
# is strictly smaller, so every error ran toward permitting.
#
# The fix is a second figure, derived at the door rather than written into the store: the same
# `cost.apply_cost_model` the factory scores with, applied to each row's recorded fills. Gross
# stays exactly where it was and keeps its consumers; net is what eligibility reads.
NET_BASIS = "net_of_fees_slippage_and_funding"
# A row that cannot be re-costed — no risk denominator, no prices, an unparseable direction.
# Counted and named, never silently dropped and never silently treated as free: an outcome the
# cost model cannot judge is one the eligibility flag must not count as evidence.
UNCOSTABLE = "OUTCOME_NOT_COSTABLE"

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


def _risk_of(outcome: Mapping[str, Any]) -> float | None:
    """The risk-per-unit ``result_R`` was divided by, from the row or derived from it.

    Recorded directly since 2026-07-29. Older rows (and the imported crypto_AI_System history)
    predate the field, so it is recovered from the identity `result_R = signed_move / risk` —
    exact where it works, and it fails precisely on a break-even row, which is the one a cost
    model would push negative. Those are reported as uncostable rather than assumed."""
    stored = _f(outcome.get("risk"))
    if stored > 0:
        return stored
    entry, exit_price = _f(outcome.get("entry_price")), _f(outcome.get("exit_price"))
    result_r = _f(outcome.get("result_R"))
    if not (entry > 0 and exit_price > 0) or result_r == 0:
        return None
    signed = (exit_price - entry) if str(outcome.get("direction")) == "LONG" else (entry - exit_price)
    risk = signed / result_r
    return risk if risk > 0 else None


def _funding_intervals(outcome: Mapping[str, Any]) -> float:
    """How many 8h settlements this position was open across, from its bars and timeframe.

    Paper does not record wall-clock duration, but it records `holding_candles` and the
    `timeframe` those candles are in, and that product IS the duration. A row missing either
    is charged nothing on this axis rather than a guess — the fee legs still apply, and the
    resulting figure is stated as the optimistic side of the honest one."""
    bars = _f(outcome.get("holding_candles"))
    minutes = TIMEFRAMES.get(str(outcome.get("timeframe") or ""))
    if bars <= 0 or not minutes:
        return 0.0
    return (bars * minutes / 1440.0) * FUNDING_INTERVALS_PER_DAY


def net_result_r(
    outcome: Mapping[str, Any], *, cost: CostModel | None = None
) -> float | None:
    """One paper outcome re-read NET of fees, slippage and carry — or None if it cannot be.

    The same `cost.apply_cost_model` the factory scores candidates with, on the row's own
    recorded fills, so the paper track record and the backtest evidence answer to one cost
    model rather than two. Deliberately a derivation and not a stored field: `result_R` keeps
    its meaning, its consumers, and its place in an append-only store.

    Carry is charged at the model's base rate rather than the venue's realized settlements —
    unlike the backtest, a settled paper row has no funding series attached, and refetching one
    per row at report time would put a network read behind a board that must render offline."""
    cost = cost or CostModel()
    risk = _risk_of(outcome)
    entry, exit_price = _f(outcome.get("entry_price")), _f(outcome.get("exit_price"))
    direction = str(outcome.get("direction") or "")
    if risk is None or not (entry > 0 and exit_price > 0) or direction not in {"LONG", "SHORT"}:
        return None
    carry = _funding_intervals(outcome) * cost.funding_bps_per_interval / 10000.0
    return apply_cost_model(
        direction, entry, exit_price, risk,
        cost=cost, close_reason=str(outcome.get("close_reason") or ""), funding_rate_sum=carry,
    ).net_r


def summarize_net_of_costs(
    records: Iterable[Mapping[str, Any]], *, cost: CostModel | None = None
) -> dict[str, Any]:
    """The costed twin of :func:`summarize_outcomes` — what this book would have earned at the
    venue it actually trades at.

    ``costed_count`` and ``uncostable_count`` are reported beside the figures on purpose: an
    expectancy over a subset of the rows is a different claim from one over all of them, and
    the gate below refuses to run on a sample it could only partly price."""
    rows = [dict(r) for r in records if isinstance(r, Mapping)]
    closed = [r for r in rows if r.get("outcome_closed") is True]
    net = [(r, net_result_r(r, cost=cost)) for r in closed]
    costed = [value for _row, value in net if value is not None]
    uncostable = [row for row, value in net if value is None]
    wins = [v for v in costed if v > 0]
    losses = [v for v in costed if v < 0]
    return {
        "basis": NET_BASIS,
        "closed_count": len(closed),
        "costed_count": len(costed),
        "uncostable_count": len(uncostable),
        "total_R": round(sum(costed), 8),
        "expectancy": round(sum(costed) / len(costed), 8) if costed else 0.0,
        "win_count": len(wins),
        "loss_count": len(losses),
        "cost_model": {
            "taker_fee_bps": (cost or CostModel()).taker_fee_bps,
            "maker_fee_bps": (cost or CostModel()).maker_fee_bps,
            "slippage_bps": (cost or CostModel()).slippage_bps,
            "funding_bps_per_interval": (cost or CostModel()).funding_bps_per_interval,
        },
    }


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


def _net_failure_modes(net: Mapping[str, Any]) -> list[str]:
    """What the costed reading adds to the list of reasons not to go live.

    Named separately from the gross ones because the interesting case is the DISAGREEMENT: a
    book that is positive before costs and negative after has an edge smaller than its own
    frictions, and that is a different diagnosis from "no edge" — it says the entry rule works
    and the holding period does not."""
    modes: list[str] = []
    if int(_f(net.get("closed_count"))) == 0:
        return modes  # the gross side already says NO_CLOSED_OUTCOMES
    if _f(net.get("expectancy")) <= 0:
        modes.append("NEGATIVE_EXPECTANCY_NET_OF_COSTS")
    if int(_f(net.get("uncostable_count"))) > 0:
        # Not fatal on its own — it names how much of the sample the figure covers, so a reader
        # is never shown an expectancy over a subset without being told it is one.
        modes.append(UNCOSTABLE)
    return modes


def build_performance_report(
    outcomes: Iterable[Mapping[str, Any]], *, now: str, min_sample_size: int = MIN_SAMPLE_SIZE
) -> dict[str, Any]:
    """The review-only performance report over all outcomes. Deterministic for a
    given (outcomes, now); the id is seeded from the source outcome ids."""
    rows = [dict(r) for r in outcomes if isinstance(r, Mapping)]
    summary = summarize_outcomes(rows)
    net = summarize_net_of_costs(rows)
    independent_event_count = count_independent_trade_events(rows)
    status, recommendation, blockers = _status_and_recommendation(
        summary, has_rows=bool(rows), independent_event_count=independent_event_count,
        min_sample_size=min_sample_size,
    )
    failure_modes = sorted(dict.fromkeys([
        *blockers, *_failure_modes(summary, has_rows=bool(rows)), *_net_failure_modes(net),
    ]))
    live_candidate_eligible = (
        status == STATUS_RECORDED
        and recommendation == RECOMMEND_CREATE_CANDIDATE_PROFILE_DRAFT
        and not failure_modes
        and int(_f(summary.get("closed_count"))) >= min_sample_size
        and independent_event_count >= min_sample_size
        # The costed figure, and this is the whole point of it. This flag is the machine-readable
        # half of the operator checklist's Gate 0, and it used to turn on gross expectancy — a
        # venue with no fee, no spread and no carry. Every disagreement between the two runs one
        # way, because costs only ever subtract.
        and _f(net.get("expectancy")) > 0
        and int(_f(net.get("costed_count"))) >= min_sample_size
    )
    source_ids = sorted(str(r.get("outcome_id")) for r in rows if r.get("outcome_id"))
    report = {
        "performance_report_version": PERFORMANCE_REPORT_VERSION,
        "status": status,
        "recommendation": recommendation,
        "sample_size": int(_f(summary.get("closed_count"))),
        "independent_event_count": independent_event_count,
        "summary": summary,
        # The same rows at the venue's rates. Beside `summary` rather than replacing it: gross
        # is what `paper.settle_trade_plan` measured and what the risk guard is calibrated on,
        # net is what a decision about real money has to read.
        "net_summary": net,
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
        f"expectancy      : {summary.get('expectancy')} R/trade  (GROSS — intended fills, no costs)",
    ]
    # The costed line goes directly under the gross one, never at the bottom: these two answer
    # the same question about different venues, and the one that matters for real money is the
    # second. A reader who stops after the first must at least have seen them adjacent.
    net = report.get("net_summary") or {}
    net_line = f"expectancy (net): {net.get('expectancy')} R/trade  (fees + slippage + funding)"
    if int(_f(net.get("uncostable_count"))) > 0:
        net_line += f"  [over {net.get('costed_count')}/{net.get('closed_count')} costable rows]"
    lines.append(net_line)
    lines += [
        f"win/loss        : {summary.get('win_count')}W {summary.get('loss_count')}L (ratio {summary.get('win_loss_ratio')})",
        f"max drawdown    : {summary.get('max_drawdown')}R",
        f"live candidate  : {'eligible' if report.get('live_candidate_eligible') else 'not eligible'}"
        f"  (judged on the NET figure)",
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
    "NET_BASIS",
    "UNCOSTABLE",
    "build_performance_report",
    "count_independent_trade_events",
    "net_result_r",
    "r_distribution",
    "render_report_text",
    "run_paper_performance_report",
    "summarize_net_of_costs",
    "summarize_outcomes",
]
