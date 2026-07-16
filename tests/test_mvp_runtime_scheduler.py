"""R6 Scheduler tests — schedule store, due firing, kill-switch binding, overlap, prune.

A fake executor stands in for the pipeline, so these run without a local Core.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import control, scheduler
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import PersistenceError, SchedulerBlocked
from runtime.mvp_runtime.scheduler import (
    KIND_PRUNE,
    KIND_TASK,
    MIN_INTERVAL_SECONDS,
    ScheduleStore,
    build_schedule,
    run_due,
)
from runtime.mvp_runtime.store import LedgerStore
from runtime.mvp_runtime.working_memory import WorkingMemoryStore

T0 = "2026-07-16T09:00:00Z"
T1 = "2026-07-16T09:01:00Z"   # T0 + 60s
T2 = "2026-07-16T09:02:00Z"   # T0 + 120s
PAST = "2026-07-16T08:00:00Z"


class FakeExecutor:
    def __init__(self, status="COMPLETED"):
        self.calls: list[dict] = []
        self._status = status

    def __call__(self, request, **kwargs):
        self.calls.append({"request": request, **kwargs})
        return {"status": self._status}


def _task_schedule(store, *, now=T0, interval=60, request="analyze X", enabled=True):
    s = build_schedule(kind=KIND_TASK, request=request, interval_seconds=interval,
                       created_by="op", now=now, enabled=enabled)
    store.add(s)
    return s


# --- build_schedule validation ----------------------------------------------

def test_build_schedule_ok():
    s = build_schedule(kind=KIND_TASK, request="hi", interval_seconds=3600, created_by="op", now=T0)
    assert s.kind == KIND_TASK and s.interval_seconds == 3600 and s.enabled is True
    assert s.next_run_at == "2026-07-16T10:00:00Z"     # T0 + 3600s


@pytest.mark.parametrize("kwargs, code", [
    (dict(kind="bogus", request="x", interval_seconds=60), "UNKNOWN_KIND"),
    (dict(kind=KIND_TASK, request="x", interval_seconds=MIN_INTERVAL_SECONDS - 1), "INVALID_INTERVAL"),
    (dict(kind=KIND_TASK, request="  ", interval_seconds=60), "MISSING_REQUEST"),
])
def test_build_schedule_fail_closed(kwargs, code):
    with pytest.raises(SchedulerBlocked) as exc:
        build_schedule(created_by="op", now=T0, **kwargs)
    assert exc.value.reason_code == code


def test_prune_schedule_needs_no_request():
    s = build_schedule(kind=KIND_PRUNE, request="", interval_seconds=86400, created_by="op", now=T0)
    assert s.kind == KIND_PRUNE


# --- store CRUD -------------------------------------------------------------

def test_store_add_list_remove_toggle(tmp_path):
    store = ScheduleStore(tmp_path)
    assert store.list() == []
    s = _task_schedule(store)
    assert [x.schedule_id for x in store.list()] == [s.schedule_id]
    assert store.set_enabled(s.schedule_id, False) is True
    assert store.list()[0].enabled is False
    assert store.remove(s.schedule_id) is True
    assert store.list() == []
    assert store.remove("nope") is False


def test_store_corrupt_fails_closed(tmp_path):
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(PersistenceError) as exc:
        store.list()
    assert exc.value.reason_code == "SCHEDULES_UNREADABLE"


# --- run_due firing ---------------------------------------------------------

def test_due_schedule_fires_and_advances(tmp_path):
    store = ScheduleStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    s = _task_schedule(store, now=T0, interval=60)   # next_run = T1
    ex = FakeExecutor()
    summary = run_due(store, now=T1, ledger=ledger, executor=ex)
    assert summary["fired"] == 1 and summary["skipped"] == 0
    assert len(ex.calls) == 1 and ex.calls[0]["request"] == "analyze X"
    assert ex.calls[0]["channel"] == "scheduler" and ex.calls[0]["requester_type"] == "scheduler"
    updated = store.list()[0]
    assert updated.last_run_at == T1 and updated.last_status == "COMPLETED"
    assert updated.next_run_at == T2                  # advanced by another interval
    event = json.loads((ledger.root / "scheduler_events.jsonl").read_text(encoding="utf-8").strip())
    assert event["action"] == "fired" and event["integrity"]["event_sha256"].startswith("sha256:")


def test_not_due_schedule_does_not_fire(tmp_path):
    store = ScheduleStore(tmp_path)
    _task_schedule(store, now=T0, interval=60)        # next_run = T1
    ex = FakeExecutor()
    summary = run_due(store, now=T0, executor=ex)     # now < next_run
    assert summary["fired"] == 0 and ex.calls == []


def test_disabled_schedule_does_not_fire(tmp_path):
    store = ScheduleStore(tmp_path)
    _task_schedule(store, now=T0, interval=60, enabled=False)
    ex = FakeExecutor()
    assert run_due(store, now=T1, executor=ex)["fired"] == 0
    assert ex.calls == []


def test_two_due_schedules_both_fire_sequentially(tmp_path):
    store = ScheduleStore(tmp_path)
    _task_schedule(store, now=T0, interval=60, request="a")
    _task_schedule(store, now=T0, interval=60, request="b")
    ex = FakeExecutor()
    summary = run_due(store, now=T1, executor=ex)
    assert summary["fired"] == 2
    assert sorted(c["request"] for c in ex.calls) == ["a", "b"]


# --- kill-switch binding (governance kill_blocks: scheduler_execution) -------

@pytest.mark.parametrize("command, status", [("kill", "skipped_not_active"), ("pause", "skipped_not_active")])
def test_killed_or_paused_skips_execution(tmp_path, command, status):
    store = ScheduleStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    control_store = ControlStore(tmp_path)
    control.apply_command(control_store, command, actor="op", now=T0)
    _task_schedule(store, now=T0, interval=60)
    ex = FakeExecutor()
    summary = run_due(store, now=T1, control_store=control_store, ledger=ledger, executor=ex)
    assert summary["fired"] == 0 and summary["skipped"] == 1
    assert ex.calls == []                             # never executed while not ACTIVE
    assert store.list()[0].next_run_at == T2          # occurrence dropped, cadence advanced
    event = json.loads((ledger.root / "scheduler_events.jsonl").read_text(encoding="utf-8").strip())
    assert event["action"] == "skipped" and event["status"] == status


def test_resume_lets_schedule_fire_again(tmp_path):
    store = ScheduleStore(tmp_path)
    control_store = ControlStore(tmp_path)
    _task_schedule(store, now=T0, interval=60)
    ex = FakeExecutor()
    control.apply_command(control_store, "kill", actor="op", now=T0)
    run_due(store, now=T1, control_store=control_store, executor=ex)     # skipped, next_run -> T2
    control.apply_command(control_store, "resume", actor="op", now=T0)
    run_due(store, now=T2, control_store=control_store, executor=ex)     # now fires
    assert len(ex.calls) == 1


# --- memory_prune kind ------------------------------------------------------

def test_memory_prune_schedule_prunes_expired(tmp_path):
    store = ScheduleStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    wm = WorkingMemoryStore(tmp_path / "wm")
    wm.append([{"candidate_id": "stale", "scope": "task_working_memory", "status": "CANDIDATE",
                "content": "old", "created_at": PAST, "expires_at": PAST}])
    s = build_schedule(kind=KIND_PRUNE, request="", interval_seconds=86400, created_by="op", now=T0)
    store.add(s)
    summary = run_due(store, now="2026-07-17T09:00:00Z", ledger=ledger, working_memory=wm)
    assert summary["fired"] == 1
    assert summary["results"][0]["status"] == "pruned:1"
    assert wm.read_all() == []
