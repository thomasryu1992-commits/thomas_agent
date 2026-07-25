"""LP5.4 tests — the live outcome bridge. Pure, no venue, no network.

The gap this closes: the risk guard, the lifecycle demoter and the C6 feedback report key
on ``result_R``, ``created_at_utc`` and strategy lineage, none of which a live outcome
carried — so a live loss streak could not demote a strategy the way a paper one does.

The load-bearing property under test is the exclusion rule. ``guards._closed_rows`` reads a
missing ``result_R`` as ``0.0``, which is a *breakeven*, so feeding it a real live loss
whose risk was never recorded would SHORTEN a loss streak instead of extending it. The
bridge must refuse to pass such a row rather than fabricate an R for it.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import guards, lifecycle
from runtime.mvp_runtime.crypto.feedback import summarize_outcomes
from runtime.mvp_runtime.crypto.live_pnl import (
    UNKNOWN_R,
    build_live_outcome_record,
    live_analysis_summary,
    live_outcomes_for_analysis,
)

NOW = "2026-07-25T12:00:00Z"


def _live(pnl: float, *, risk: float | None = 20.0, position_id="p1", now=NOW, **kw):
    return build_live_outcome_record(
        realized_pnl_usdt=pnl, symbol=kw.pop("symbol", "BTCUSDT"), side="SELL",
        quantity=0.002, risk_usdt=risk, position_id=position_id, now=now, **kw,
    )


# --- the record now carries what the consumers read ----------------------------

def test_result_r_is_computed_from_the_recorded_risk():
    record = _live(-20.0, risk=20.0)
    assert record["result_R"] == -1.0
    assert _live(40.0, risk=20.0)["result_R"] == 2.0


def test_created_at_utc_is_emitted_alongside_closed_at_utc():
    """Consumers key on created_at_utc; the live record's own vocabulary stays intact."""
    record = _live(-20.0)
    assert record["created_at_utc"] == record["closed_at_utc"] == NOW


def test_lineage_rides_through_for_the_lifecycle():
    record = _live(-20.0, candidate_id="cand_abc", strategy_rule_hash="deadbeef",
                   strategy_generation_id="GEN-007", strategy_id="S001")
    assert lifecycle.outcome_attribution_key(record) == "cand:cand_abc"


def test_lineage_falls_back_to_generation_and_rule_hash():
    record = _live(-20.0, strategy_rule_hash="deadbeef", strategy_generation_id="GEN-007")
    assert lifecycle.outcome_attribution_key(record) == "gen:GEN-007:deadbeef"


def test_no_recorded_risk_yields_none_never_zero():
    """0.0 would read as a breakeven to every consumer. None says 'unknown', which the
    bridge can then act on."""
    record = _live(-20.0, risk=None)
    assert record["result_R"] is None
    assert record["risk_usdt"] is None
    # The money is still fully recorded — only the R statistic is unavailable.
    assert record["realized_pnl_usdt"] == -20.0


@pytest.mark.parametrize("risk", [None, 0, 0.0, -5.0])
def test_every_unusable_risk_refuses_an_r(risk):
    assert _live(-20.0, risk=risk)["result_R"] is None


def test_the_record_still_self_hashes_with_the_new_fields():
    record = _live(-20.0)
    assert isinstance(record["record_sha256"], str) and record["record_sha256"]
    # Deterministic: the same settlement hashes the same way.
    assert record["record_sha256"] == _live(-20.0)["record_sha256"]


# --- the exclusion rule (the reason this module exists) ------------------------

def test_a_row_without_r_is_excluded_not_passed_through():
    readable, excluded = live_outcomes_for_analysis([_live(-20.0, risk=None)])
    assert readable == []
    assert len(excluded) == 1 and excluded[0]["reason"] == UNKNOWN_R
    # The excluded row still reports the real money, so nothing is hidden.
    assert excluded[0]["realized_pnl_usdt"] == -20.0


def test_the_fail_open_this_prevents():
    """Proof by contrast: passing an R-less live loss to the guard would count it as a
    breakeven and BREAK a loss streak that should have continued."""
    lossy = _live(-20.0, risk=None)
    # What the guard would do with the raw row:
    assert float(lossy.get("result_R", 0.0) or 0.0) == 0.0   # a breakeven, not a loss
    # What the bridge does instead:
    readable, excluded = live_outcomes_for_analysis([lossy])
    assert readable == [] and excluded[0]["reason"] == UNKNOWN_R


def test_open_positions_are_not_outcomes():
    readable, excluded = live_outcomes_for_analysis([{"outcome_closed": False, "result_R": -1.0}])
    assert readable == [] and excluded == []


def test_non_numeric_r_is_excluded():
    for bad in ("oops", None, True):
        readable, excluded = live_outcomes_for_analysis(
            [{"outcome_closed": True, "result_R": bad, "realized_pnl_usdt": -1.0}]
        )
        assert readable == [], f"{bad!r} must not be read as an R"
        assert excluded and excluded[0]["reason"] == UNKNOWN_R


def test_malformed_rows_are_skipped_not_crashed():
    readable, excluded = live_outcomes_for_analysis(["nope", 42, None])
    assert readable == [] and excluded == []


# --- the readable rows are exactly what the consumers already eat --------------

def test_the_risk_guard_reads_a_bridged_live_loss_streak():
    """The gap's own example: a live loss streak must be able to halt trading the way a
    paper one does."""
    losses = [
        _live(-20.0, position_id=f"p{i}", now=f"2026-07-2{i}T00:00:00Z") for i in range(1, 4)
    ]
    readable, excluded = live_outcomes_for_analysis(losses)
    assert len(readable) == 3 and excluded == []
    verdict = guards.run_risk_guard(readable, now="2026-07-25T00:00:00Z")
    # The guard consumed them without a live-specific branch and reached a verdict.
    assert verdict["status"] in {"NORMAL", "BLOCK_NEW_POSITION"}
    assert verdict["consecutive_losses"] == 3


def test_the_feedback_summary_reads_bridged_live_rows():
    readable, _ = live_outcomes_for_analysis([
        _live(40.0, position_id="w1"), _live(-20.0, position_id="l1"),
    ])
    summary = summarize_outcomes(readable)
    assert summary["closed_count"] == 2
    assert summary["win_count"] == 1 and summary["loss_count"] == 1
    assert summary["expectancy"] == pytest.approx(0.5)   # (+2R, -1R) / 2


def test_the_lifecycle_groups_bridged_rows_by_lineage():
    rows = [
        _live(-20.0, position_id="a", candidate_id="cand_1"),
        _live(-20.0, position_id="b", candidate_id="cand_2"),
    ]
    readable, _ = live_outcomes_for_analysis(rows)
    assert {lifecycle.outcome_attribution_key(r) for r in readable} == {"cand:cand_1", "cand:cand_2"}


def test_bridged_rows_stay_identifiable_as_live():
    readable, _ = live_outcomes_for_analysis([_live(-20.0)])
    assert readable[0]["stage"] == "live"


def test_pre_bridge_rows_fall_back_to_closed_at_utc():
    """A row written before this increment has closed_at_utc but no created_at_utc; if it
    carries an R at all it should still be readable."""
    legacy = {
        "outcome_closed": True, "result_R": -1.0, "closed_at_utc": NOW,
        "realized_pnl_usdt": -20.0, "strategy_id": "S001",
    }
    readable, excluded = live_outcomes_for_analysis([legacy])
    assert excluded == []
    assert readable[0]["created_at_utc"] == NOW


# --- the summary keeps the two streams distinguishable ------------------------

def test_summary_reports_both_counts():
    summary = live_analysis_summary([
        _live(-20.0, position_id="a"),
        _live(-20.0, position_id="b", risk=None),
    ])
    assert summary["readable_count"] == 1
    assert summary["excluded_count"] == 1
    assert summary["excluded"][0]["reason"] == UNKNOWN_R


def test_summary_of_an_empty_stream_is_honest():
    summary = live_analysis_summary([])
    assert summary == {"readable_count": 0, "excluded_count": 0, "excluded": [], "readable": []}


def test_the_bridge_is_pure():
    rows = [_live(-20.0)]
    before = dict(rows[0])
    live_outcomes_for_analysis(rows)
    assert rows[0] == before
