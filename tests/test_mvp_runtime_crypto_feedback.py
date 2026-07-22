"""C6 feedback analytics tests — summary math, independence rule, report semantics.

The source rules under test: expectancy/drawdown math, the independent-event gate
(closed-count inflates with scheduler uptime; eligibility needs independent events),
review-only recommendations (negative expectancy → drop, positive → candidate draft),
and the report's honesty rules (unreadable history raises; review-only flags false)."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import paper
from runtime.mvp_runtime.crypto.feedback import (
    RECOMMEND_CREATE_CANDIDATE_PROFILE_DRAFT,
    RECOMMEND_DROP_CANDIDATE_PROFILE,
    RECOMMEND_EXPAND_TEST_COVERAGE,
    RECOMMEND_REPEAT_IN_PAPER,
    STATUS_BLOCKED_NO_OUTCOMES,
    STATUS_INSUFFICIENT_SAMPLE,
    STATUS_RECORDED,
    build_performance_report,
    count_independent_trade_events,
    r_distribution,
    render_report_text,
    run_paper_performance_report,
    summarize_outcomes,
)
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-07-22T12:00:00Z"


def _outcome(result_r, closed_at, *, strategy_id="S1", closed=True, outcome_id=None):
    return {
        "outcome_id": outcome_id or f"out_{strategy_id}_{closed_at}",
        "result_R": result_r,
        "outcome_closed": closed,
        "created_at_utc": closed_at,
        "strategy_id": strategy_id,
    }


# Hours apart -> each is an independent event.
SPREAD = [
    _outcome(2.0, "2026-07-18T00:00:00Z"),
    _outcome(-1.0, "2026-07-19T00:00:00Z"),
    _outcome(1.5, "2026-07-20T00:00:00Z"),
    _outcome(-1.0, "2026-07-21T00:00:00Z"),
]


# --- summary math -------------------------------------------------------------

def test_summary_math():
    summary = summarize_outcomes(SPREAD)
    assert summary["closed_count"] == 4
    assert summary["win_count"] == 2 and summary["loss_count"] == 2
    assert summary["expectancy"] == round((2.0 - 1.0 + 1.5 - 1.0) / 4, 8)
    assert summary["win_loss_ratio"] == 1.0
    # Equity path: 2, 1, 2.5, 1.5 -> deepest fall from a peak is 1.0R.
    assert summary["max_drawdown"] == 1.0


def test_summary_empty_and_open_rows():
    assert summarize_outcomes([])["closed_count"] == 0
    summary = summarize_outcomes([_outcome(-5.0, NOW, closed=False)])
    assert summary["outcome_count"] == 1 and summary["closed_count"] == 0


def test_win_loss_ratio_with_zero_losses_is_win_count():
    summary = summarize_outcomes([_outcome(1.0, NOW), _outcome(2.0, NOW)])
    assert summary["win_loss_ratio"] == 2.0


def test_by_strategy_breakdown():
    rows = SPREAD + [_outcome(3.0, "2026-07-21T12:00:00Z", strategy_id="S2")]
    by_strategy = summarize_outcomes(rows)["by_strategy"]
    assert by_strategy["S1"]["closed_count"] == 4
    assert by_strategy["S2"] == {"closed_count": 1, "win_count": 1, "loss_count": 0, "expectancy": 3.0}


def test_r_distribution_buckets():
    rows = [_outcome(v, NOW) for v in (-1.5, -0.5, 0.0, 0.5, 1.5, 2.5)]
    assert r_distribution(rows) == {
        "lt_minus_1R": 1, "minus_1R_to_0R": 1, "zero_R": 1,
        "zero_to_1R": 1, "one_to_2R": 1, "gte_2R": 1,
    }


# --- independent events -------------------------------------------------------

def test_consecutive_cycle_reentries_are_one_event():
    rows = [_outcome(1.0, f"2026-07-22T10:{m:02d}:00Z") for m in (0, 15, 30)]
    assert count_independent_trade_events(rows) == 1


def test_gap_beyond_merge_window_splits_events():
    rows = [_outcome(1.0, "2026-07-22T10:00:00Z"), _outcome(1.0, "2026-07-22T13:00:00Z")]
    assert count_independent_trade_events(rows) == 2


def test_different_strategies_are_different_events():
    rows = [
        _outcome(1.0, "2026-07-22T10:00:00Z", strategy_id="S1"),
        _outcome(1.0, "2026-07-22T10:05:00Z", strategy_id="S2"),
    ]
    assert count_independent_trade_events(rows) == 2


# --- report semantics ---------------------------------------------------------

def test_no_outcomes_blocks_report():
    report = build_performance_report([], now=NOW)
    assert report["status"] == STATUS_BLOCKED_NO_OUTCOMES
    assert report["recommendation"] == RECOMMEND_EXPAND_TEST_COVERAGE
    assert report["live_candidate_eligible"] is False


def test_insufficient_closed_sample():
    report = build_performance_report(SPREAD[:2], now=NOW)
    assert report["status"] == STATUS_INSUFFICIENT_SAMPLE
    assert report["recommendation"] == RECOMMEND_REPEAT_IN_PAPER


def test_uptime_inflated_sample_is_still_insufficient():
    # 5 closed outcomes minutes apart = 1 independent event: the closed count alone
    # must not unlock eligibility (the source's scheduler-uptime rule).
    rows = [_outcome(1.0, f"2026-07-22T10:{m:02d}:00Z") for m in (0, 10, 20, 30, 40)]
    report = build_performance_report(rows, now=NOW)
    assert report["sample_size"] == 5 and report["independent_event_count"] == 1
    assert report["status"] == STATUS_INSUFFICIENT_SAMPLE
    assert "INSUFFICIENT_INDEPENDENT_TRADE_EVENTS" in report["failure_modes"]


def test_negative_expectancy_recommends_drop():
    rows = [_outcome(-1.0, f"2026-07-{d:02d}T00:00:00Z") for d in (18, 19, 20, 21)]
    report = build_performance_report(rows, now=NOW)
    assert report["status"] == STATUS_RECORDED
    assert report["recommendation"] == RECOMMEND_DROP_CANDIDATE_PROFILE
    assert "NEGATIVE_EXPECTANCY" in report["failure_modes"]
    assert report["live_candidate_eligible"] is False


def test_positive_expectancy_is_candidate_eligible():
    report = build_performance_report(SPREAD, now=NOW)
    assert report["status"] == STATUS_RECORDED
    assert report["recommendation"] == RECOMMEND_CREATE_CANDIDATE_PROFILE_DRAFT
    assert report["live_candidate_eligible"] is True


def test_report_is_deterministic_and_review_only():
    a = build_performance_report(SPREAD, now=NOW)
    b = build_performance_report(SPREAD, now=NOW)
    assert a == b and a["performance_report_id"].startswith("performance_report")
    # Review-only, structurally: the report can never claim an execution right.
    assert a["live_trading_allowed_by_this_module"] is False
    assert a["runtime_settings_mutated_by_this_module"] is False


def test_render_contains_the_decision_inputs():
    report = build_performance_report(SPREAD, now=NOW)
    text = render_report_text(report)
    assert "expectancy" in text and str(report["summary"]["expectancy"]) in text
    assert report["recommendation"] in text
    assert report["performance_report_id"] in text


# --- store integration --------------------------------------------------------

def test_run_paper_performance_report_reads_store(tmp_path):
    state = paper.state_dir(tmp_path)
    state.mkdir(parents=True)
    lines = "".join(json.dumps(o) + "\n" for o in SPREAD)
    (state / "paper_outcomes.jsonl").write_text(lines, encoding="utf-8")
    report, text = run_paper_performance_report(now=NOW, root=tmp_path)
    assert report["sample_size"] == 4
    assert "paper performance report" in text


def test_run_paper_performance_report_fails_closed_on_corrupt_store(tmp_path):
    state = paper.state_dir(tmp_path)
    state.mkdir(parents=True)
    (state / "paper_outcomes.jsonl").write_text("{broken\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        run_paper_performance_report(now=NOW, root=tmp_path)
    assert exc.value.reason_code == "OUTCOME_HISTORY_UNREADABLE"
