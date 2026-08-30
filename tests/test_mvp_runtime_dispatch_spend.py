"""`dispatch_spend` — §6-3's 50 USD/day watch, dormant on free tiers and honest about it.

The proposal (HERMES_AGENT_DISPATCH_V0.1 §6, Thomas 2026-07-30) chose a currency threshold
with no hard cap, knowing free-tier cost is unmetered — so these tests pin three things: the
attribution (assistant tasks only, joined to cost through the trace), the dormancy honesty
(the status line must say the zero is unmetered, not measured), and the alert shape (crossing
raises `SchedulerBlocked`, which is what rides the failure-transition notifier; nothing is
stopped).
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import dispatch_spend, scheduler
from runtime.mvp_runtime.store import LedgerStore

NOW = "2026-08-30T12:00:00Z"


def _task_record(requester_id: str, received_at: str) -> dict:
    return {
        "source": {"requester": {"requester_type": "agent", "requester_id": requester_id,
                                 "authenticated": True}},
        "request": {"received_at": received_at},
    }


def _usage_record(cost_used: float, currency: str = "USD") -> dict:
    return {"schema_version": "execution_budget.v0.1",
            "usage": {"cost_used": cost_used, "cost_currency": currency}}


def _seed(store: LedgerStore, trace: str, *, requester="assistant_bridge",
          received=NOW, cost=0.0, currency="USD") -> None:
    store.root.mkdir(parents=True, exist_ok=True)
    store.append_records(trace, {"task": _task_record(requester, received)})
    store.append_records(trace, {"budget_usage": _usage_record(cost, currency)})


def test_only_assistant_tasks_received_today_are_counted(tmp_path):
    store = LedgerStore.default(tmp_path)
    _seed(store, "trace_assistant", cost=1.25)
    _seed(store, "trace_operator", requester="thomas", cost=100.0)
    _seed(store, "trace_yesterday", received="2026-08-29T23:59:59Z", cost=100.0)
    measured = dispatch_spend.measure_day(store, now=NOW)
    assert measured["runs"] == 1
    assert measured["cost_usd"] == pytest.approx(1.25)
    assert measured["metered_rows"] == 1


def test_the_unmetered_free_tier_day_reads_as_dormant_not_as_a_measured_zero(tmp_path):
    store = LedgerStore.default(tmp_path)
    _seed(store, "trace_a", cost=0.0)
    measured = dispatch_spend.measure_day(store, now=NOW)
    assert measured["runs"] == 1 and measured["metered_rows"] == 0
    line = dispatch_spend.status_line(measured)
    assert "unmetered" in line and "dormant" in line
    # ...and a metered day drops the qualifier: the line claims only what was taken.
    _seed(store, "trace_b", cost=0.10)
    assert "unmetered" not in dispatch_spend.status_line(
        dispatch_spend.measure_day(store, now=NOW))


def test_a_non_usd_row_is_excluded_rather_than_summed_into_the_wrong_unit(tmp_path):
    store = LedgerStore.default(tmp_path)
    _seed(store, "trace_krw", cost=70000.0, currency="KRW")
    measured = dispatch_spend.measure_day(store, now=NOW)
    assert measured["cost_usd"] == 0.0


def test_the_threshold_is_strictly_more_than_fifty(tmp_path):
    assert not dispatch_spend.over_threshold({"cost_usd": 50.0, "threshold_usd": 50.0})
    assert dispatch_spend.over_threshold({"cost_usd": 50.01, "threshold_usd": 50.0})


def _dispatch_schedule() -> scheduler.Schedule:
    return scheduler.Schedule(
        schedule_id="schedule_dispatch_spend_test", kind=scheduler.KIND_DISPATCH_SPEND,
        request="", interval_seconds=3600, enabled=True, created_by="test",
        created_at=NOW, next_run_at=NOW,
    )


def _execute(tmp_path):
    return scheduler._execute(
        _dispatch_schedule(), now=NOW, ledger=None, working_memory=None,
        programization=None, repo_root=tmp_path, executor=lambda **_: {},
    )


def test_an_under_threshold_fire_is_a_quiet_status_line(tmp_path):
    _seed(LedgerStore.default(tmp_path), "trace_a", cost=49.99)
    status = _execute(tmp_path)
    assert status.startswith("dispatch_spend ")
    assert "49.99" in status


def test_crossing_the_threshold_raises_onto_the_failure_notifier(tmp_path):
    _seed(LedgerStore.default(tmp_path), "trace_a", cost=50.01)
    with pytest.raises(scheduler.SchedulerBlocked) as caught:
        _execute(tmp_path)
    assert caught.value.reason_code == dispatch_spend.DISPATCH_SPEND_OVER
    # No hard cap: the message says nothing was stopped, because nothing is.
    assert "nothing was stopped" in str(caught.value.reason)


def test_the_kind_is_classified_maintenance_and_partitioned(tmp_path):
    assert scheduler.KIND_DISPATCH_SPEND in scheduler.KINDS
    assert scheduler.KIND_DISPATCH_SPEND in scheduler.MAINTENANCE_KINDS
    assert scheduler.KIND_DISPATCH_SPEND not in scheduler.RISK_KINDS
