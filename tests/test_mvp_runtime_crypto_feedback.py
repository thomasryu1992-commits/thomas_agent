"""C6 feedback analytics tests — summary math, independence rule, report semantics.

The source rules under test: expectancy/drawdown math, the independent-event gate
(closed-count inflates with scheduler uptime; eligibility needs independent events),
review-only recommendations (negative expectancy → drop, positive → candidate draft),
and the report's honesty rules (unreadable history raises; review-only flags false)."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import paper
from runtime.mvp_runtime.crypto import feedback
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


def _outcome(result_r, closed_at, *, strategy_id="S1", closed=True, outcome_id=None,
             priced=True, holding_candles=4, timeframe="1d"):
    """One settled paper outcome.

    ``priced`` controls whether the row carries the fills a cost model needs. Real records have
    carried them since C5 and the risk denominator since 2026-07-29; ``priced=False`` stands in
    for the imported crypto_AI_System history and for pre-`risk` rows, which the net figure
    reports as uncostable rather than pricing on a guess."""
    record = {
        "outcome_id": outcome_id or f"out_{strategy_id}_{closed_at}",
        "result_R": result_r,
        "outcome_closed": closed,
        "created_at_utc": closed_at,
        "strategy_id": strategy_id,
    }
    if priced:
        risk = 4.0
        record.update({
            "direction": "LONG", "entry_price": 100.0, "risk": risk,
            "exit_price": 100.0 + result_r * risk,
            "close_reason": "take_profit" if result_r > 0 else "stop_loss",
            "holding_candles": holding_candles, "timeframe": timeframe,
        })
    return record


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
    # M4a realized payoff legs: avg win (2.0, 1.5)=1.75, avg loss magnitude (1.0,1.0)=1.0.
    assert summary["avg_win_R"] == 1.75 and summary["avg_loss_R"] == 1.0
    # Equity path: 2, 1, 2.5, 1.5 -> deepest fall from a peak is 1.0R.
    assert summary["max_drawdown"] == 1.0


def test_avg_win_loss_R_degenerate_cases():
    # No losses: avg_loss_R is 0.0 (the ranking reads this as an undefined ratio).
    all_wins = summarize_outcomes([_outcome(1.0, NOW), _outcome(2.0, NOW)])
    assert all_wins["avg_win_R"] == 1.5 and all_wins["avg_loss_R"] == 0.0
    # No wins: avg_win_R is 0.0, avg_loss_R the positive mean magnitude.
    all_losses = summarize_outcomes([_outcome(-1.0, NOW), _outcome(-2.0, NOW)])
    assert all_losses["avg_win_R"] == 0.0 and all_losses["avg_loss_R"] == 1.5


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


# --- Gate 0 reads the costed figure (2026-07-29) -----------------------------------------------
#
# `live_candidate_eligible` is the machine-readable half of the operator checklist's first line
# — "paper trading by this runtime shows positive expectancy over a sustained window" — and it
# turned on GROSS expectancy. Paper R is measured on intended fills and carries no costs by
# design; that design is intact, and it was never the problem. The problem was the gate that
# authorized real money reading a venue with no fee, no spread and no carry.

def _priced(result_r, closed_at, **kw):
    return _outcome(result_r, closed_at, **kw)


def test_the_net_figure_is_strictly_below_the_gross_one():
    """Costs only ever subtract, so every disagreement between the two runs one way."""
    gross = summarize_outcomes(SPREAD)
    net = feedback.summarize_net_of_costs(SPREAD)
    assert net["costed_count"] == gross["closed_count"]
    assert net["expectancy"] < gross["expectancy"]
    assert net["basis"] == feedback.NET_BASIS


def test_a_book_positive_before_costs_and_negative_after_is_not_eligible():
    """The case the flag existed to catch and could not: an edge smaller than its own frictions.
    Diagnosed distinctly from "no edge" — the entry rule works, the holding period does not."""
    thin = [
        _priced(0.02, "2026-07-18T00:00:00Z", holding_candles=30),
        _priced(0.02, "2026-07-19T00:00:00Z", holding_candles=30),
        _priced(0.03, "2026-07-20T00:00:00Z", holding_candles=30),
        _priced(0.02, "2026-07-21T00:00:00Z", holding_candles=30),
    ]
    report = build_performance_report(thin, now=NOW)
    assert summarize_outcomes(thin)["expectancy"] > 0, "gross is positive — that is the premise"
    assert report["net_summary"]["expectancy"] < 0
    assert report["live_candidate_eligible"] is False
    assert "NEGATIVE_EXPECTANCY_NET_OF_COSTS" in report["failure_modes"]
    assert "NEGATIVE_EXPECTANCY" not in report["failure_modes"], "the gross book was positive"


def test_a_long_hold_costs_more_than_a_short_one():
    """Carry scales with time held, which is what the fee legs alone could never express."""
    brief = feedback.net_result_r(_priced(1.0, NOW, holding_candles=1))
    long_hold = feedback.net_result_r(_priced(1.0, NOW, holding_candles=40))
    assert long_hold < brief


def test_rows_that_cannot_be_priced_are_named_never_assumed_free():
    """The imported crypto_AI_System history and every pre-`risk` row. An outcome the cost model
    cannot judge must not be counted as evidence for a live decision."""
    mixed = [*SPREAD, _priced(1.0, "2026-07-22T00:00:00Z", priced=False)]
    net = feedback.summarize_net_of_costs(mixed)
    assert net["uncostable_count"] == 1
    assert net["costed_count"] == 4
    report = build_performance_report(mixed, now=NOW)
    assert feedback.UNCOSTABLE in report["failure_modes"]
    assert report["live_candidate_eligible"] is False


def test_an_entirely_unpriceable_history_certifies_nothing():
    """Fail-closed: a report that cannot price a single row must not certify eligibility on the
    gross figure alone, however good that figure looks."""
    unpriced = [_priced(r, at, priced=False) for r, at in (
        (2.0, "2026-07-18T00:00:00Z"), (1.5, "2026-07-19T00:00:00Z"),
        (2.0, "2026-07-20T00:00:00Z"), (1.5, "2026-07-21T00:00:00Z"))]
    report = build_performance_report(unpriced, now=NOW)
    assert report["summary"]["expectancy"] > 1.0
    assert report["net_summary"]["costed_count"] == 0
    assert report["live_candidate_eligible"] is False


def test_the_break_even_row_is_uncostable_rather_than_derived_wrong():
    """`risk` is recorded now, but an older row is recovered from `result_R = move / risk` —
    which divides by zero exactly on a flat trade, the row a cost model would push negative."""
    flat = _priced(0.0, NOW, priced=False)
    flat.update({"direction": "LONG", "entry_price": 100.0, "exit_price": 100.0})
    assert feedback.net_result_r(flat) is None


def test_a_recorded_risk_prices_a_break_even_row():
    """Which is why the field is recorded rather than always derived."""
    flat = _priced(0.0, NOW)
    assert flat["risk"] == 4.0
    net = feedback.net_result_r(flat)
    assert net is not None and net < 0.0, "a flat trade still pays its costs"


def test_the_report_text_puts_the_two_figures_next_to_each_other():
    """They answer the same question about different venues. A reader who stops after the first
    line must at least have seen them adjacent, and must know which one the flag was judged on."""
    text = render_report_text(build_performance_report(SPREAD, now=NOW))
    gross_at, net_at = text.index("expectancy      :"), text.index("expectancy (net):")
    assert net_at - gross_at < 120, "the costed line is not directly under the gross one"
    assert "GROSS" in text and "fees + slippage + funding" in text
    assert "judged on the NET figure" in text


def test_the_gross_summary_keeps_its_meaning_and_its_consumers():
    """`paper.settle_trade_plan` is the same code the factory replays, `guards` thresholds are
    calibrated on gross R, and the imported history is gross. The net figure is a derivation at
    the door, never a rewrite of an append-only store."""
    report = build_performance_report(SPREAD, now=NOW)
    assert report["summary"] == summarize_outcomes(SPREAD)
    assert all("net" not in key for key in report["summary"])
