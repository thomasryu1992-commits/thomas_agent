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
    summary = run_due(store, now=T1, ledger=ledger, executor=ex, control_store=ControlStore(tmp_path))
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
    summary = run_due(store, now=T0, executor=ex, control_store=ControlStore(tmp_path))     # now < next_run
    assert summary["fired"] == 0 and ex.calls == []


def test_disabled_schedule_does_not_fire(tmp_path):
    store = ScheduleStore(tmp_path)
    _task_schedule(store, now=T0, interval=60, enabled=False)
    ex = FakeExecutor()
    assert run_due(store, now=T1, executor=ex, control_store=ControlStore(tmp_path))["fired"] == 0
    assert ex.calls == []


def test_two_due_schedules_both_fire_sequentially(tmp_path):
    store = ScheduleStore(tmp_path)
    _task_schedule(store, now=T0, interval=60, request="a")
    _task_schedule(store, now=T0, interval=60, request="b")
    ex = FakeExecutor()
    summary = run_due(store, now=T1, executor=ex, control_store=ControlStore(tmp_path))
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


def test_kill_mid_batch_stops_the_remaining_schedules(tmp_path):
    """The control state is re-read before EACH fire, not once per batch: a /kill issued
    while schedule 1 holds the tick (a pipeline run takes minutes) must stop schedules 2
    and 3 behind it — the once-per-batch snapshot ran them after the kill."""
    store = ScheduleStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    control_store = ControlStore(tmp_path)
    _task_schedule(store, now=T0, interval=60, request="first")
    _task_schedule(store, now=T0, interval=60, request="second")

    class _KillsDuringFirst:
        def __init__(self):
            self.calls = []

        def __call__(self, request, **kwargs):
            self.calls.append(request)
            control.apply_command(control_store, "kill", actor="op", now=T1)
            return {"status": "COMPLETED"}

    ex = _KillsDuringFirst()
    summary = run_due(store, now=T1, control_store=control_store, ledger=ledger, executor=ex)
    assert ex.calls == ["first"]                       # the second never executed
    assert summary["fired"] == 1 and summary["skipped"] == 1
    events = [json.loads(line) for line in
              (ledger.root / "scheduler_events.jsonl").read_text(encoding="utf-8").strip().splitlines()]
    assert [e["action"] for e in events] == ["fired", "skipped"]


def test_operator_disable_mid_batch_survives_and_wins(tmp_path):
    """An operator disable landing while the tick executes an earlier schedule must both
    stop the disabled schedule from firing in this batch AND survive the batch — the old
    stale-snapshot replace_all reverted the disable, so the schedule silently kept firing
    with no trace."""
    store = ScheduleStore(tmp_path)
    control_store = ControlStore(tmp_path)
    first = _task_schedule(store, request="first")
    second = _task_schedule(store, request="second")

    class _DisablesSecondDuringFirst:
        def __init__(self):
            self.calls = []

        def __call__(self, request, **kwargs):
            self.calls.append(request)
            if request == "first":
                store.set_enabled(second.schedule_id, False)   # the docker-exec disable
            return {"status": "COMPLETED"}

    ex = _DisablesSecondDuringFirst()
    summary = run_due(store, now=T1, control_store=control_store, executor=ex)
    assert ex.calls == ["first"] and summary["fired"] == 1
    by_id = {s.schedule_id: s for s in store.list()}
    assert by_id[second.schedule_id].enabled is False          # the disable survived
    assert by_id[first.schedule_id].last_status == "COMPLETED"


def test_operator_remove_mid_batch_survives_and_wins(tmp_path):
    store = ScheduleStore(tmp_path)
    control_store = ControlStore(tmp_path)
    _task_schedule(store, request="first")
    second = _task_schedule(store, request="second")

    class _RemovesSecondDuringFirst:
        def __init__(self):
            self.calls = []

        def __call__(self, request, **kwargs):
            self.calls.append(request)
            if request == "first":
                assert store.remove(second.schedule_id) is True
            return {"status": "COMPLETED"}

    ex = _RemovesSecondDuringFirst()
    summary = run_due(store, now=T1, control_store=control_store, executor=ex)
    assert ex.calls == ["first"] and summary["fired"] == 1
    assert [s.request for s in store.list()] == ["first"]      # the remove survived


def test_no_control_store_defaults_to_the_per_machine_state_not_allowed(tmp_path):
    """With no injected control_store, run_due consults the per-machine state under
    repo_root — the old `else True` default silently ran with no kill binding at all."""
    store = ScheduleStore(tmp_path)
    control.apply_command(ControlStore(tmp_path), "kill", actor="op", now=T0)
    _task_schedule(store, now=T0, interval=60)
    ex = FakeExecutor()
    summary = run_due(store, now=T1, repo_root=tmp_path, executor=ex)
    assert summary["fired"] == 0 and summary["skipped"] == 1 and ex.calls == []


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
    summary = run_due(store, now="2026-07-17T09:00:00Z", ledger=ledger, working_memory=wm,
                      control_store=ControlStore(tmp_path))
    assert summary["fired"] == 1
    assert summary["results"][0]["status"] == "pruned:1"
    assert wm.read_all() == []


def test_mid_batch_failure_does_not_refire_the_completed_schedule(tmp_path):
    """The occurrence is claimed durably BEFORE executing: a failure on a LATER schedule in
    the same batch must not leave an earlier, already-executed schedule's next_run_at
    un-advanced — that re-fired it (a duplicate full pipeline run) on the next tick."""
    store = ScheduleStore(tmp_path)
    first = _task_schedule(store, request="first")
    second = _task_schedule(store, request="second")

    class _ExplodingOnSecond:
        def __init__(self):
            self.calls = []

        def __call__(self, request, **kwargs):
            self.calls.append(request)
            if request == "second":
                raise PersistenceError("LEDGER_WRITE_FAILED", "disk full mid-batch")
            return {"status": "COMPLETED"}

    executor = _ExplodingOnSecond()
    with pytest.raises(PersistenceError):
        run_due(store, now=T1, executor=executor, control_store=ControlStore(tmp_path))

    by_id = {s.schedule_id: s for s in store.list()}
    # The first schedule executed and its claim survived the later failure: it will NOT
    # fire again at the same tick time.
    assert executor.calls == ["first", "second"]
    assert by_id[first.schedule_id].next_run_at > T1
    # The second schedule's occurrence was claimed too (at-most-once: a crash drops the
    # occurrence rather than doubling it, matching the kill-skip rule).
    assert by_id[second.schedule_id].next_run_at > T1
