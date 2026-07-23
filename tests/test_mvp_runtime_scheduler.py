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


@pytest.mark.parametrize("row, why", [
    ({"kind": KIND_TASK, "interval_seconds": 60, "next_run_at": T1}, "missing schedule_id"),
    ({"schedule_id": "s1", "interval_seconds": 60, "next_run_at": T1}, "missing kind"),
    ({"schedule_id": "s1", "kind": KIND_TASK, "next_run_at": T1}, "missing interval"),
    ({"schedule_id": "s1", "kind": KIND_TASK, "interval_seconds": "soon", "next_run_at": T1}, "garbage interval"),
    ({"schedule_id": "s1", "kind": KIND_TASK, "interval_seconds": 60}, "missing next_run_at"),
])
def test_malformed_schedule_record_fails_closed_with_a_typed_error(tmp_path, row, why):
    """A raw KeyError/ValueError escaped scheduler_cli's `except MvpRuntimeError`, so the
    CLI died with a traceback instead of a BLOCK."""
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(SchedulerBlocked) as exc:
        store.list()
    assert exc.value.reason_code == "SCHEDULE_RECORD_INVALID", why


@pytest.mark.parametrize("bad", [None, "None", "2026-07-16", "2026-07-16T09:00:00+00:00", 12345])
def test_a_non_canonical_next_run_at_is_refused_not_silently_dormant(tmp_path, bad):
    """The dangerous one: `next_run_at: null` became the string "None", which sorts ABOVE
    every real timestamp — so `next_run_at <= now` was never true and the schedule silently
    never fired, with no error anywhere. A dormant schedule that looks healthy is worse
    than a loud one."""
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({
        "schedule_id": "s1", "kind": KIND_TASK, "request": "x",
        "interval_seconds": 60, "next_run_at": bad,
    }) + "\n", encoding="utf-8")
    with pytest.raises(SchedulerBlocked) as exc:
        store.list()
    assert exc.value.reason_code == "SCHEDULE_RECORD_INVALID"


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
    started, fired = _events(ledger)                  # every fire is bracketed
    assert [started["action"], fired["action"]] == ["started", "fired"]
    assert fired["integrity"]["event_sha256"].startswith("sha256:")


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


# --- a raising fire is recorded, never fatal ---------------------------------

def test_raising_fire_is_recorded_and_the_batch_survives(tmp_path):
    """One bad fire must not kill the tick loop or vanish: the failure lands as a
    durable 'failed' scheduler event + last_status, and the NEXT schedule in the
    same batch still fires."""
    store = ScheduleStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    _task_schedule(store, now=T0, interval=60, request="boom")
    _task_schedule(store, now=T0, interval=60, request="fine")
    good = FakeExecutor()

    def executor(request, **kwargs):
        if request == "boom":
            raise PersistenceError("CANDIDATES_TAMPERED", "store failed verification")
        return good(request, **kwargs)

    summary = run_due(store, now=T1, ledger=ledger, executor=executor,
                      control_store=ControlStore(tmp_path))
    assert summary["fired"] == 1 and summary["failed"] == 1
    assert [c["request"] for c in good.calls] == ["fine"]  # the batch continued

    by_request = {s.request: s for s in store.list()}
    assert by_request["boom"].last_status == "failed:CANDIDATES_TAMPERED"
    assert by_request["boom"].next_run_at == T2            # claimed: at-most-once kept
    assert by_request["fine"].last_status == "COMPLETED"
    events = _events(ledger)
    assert [e["action"] for e in events] == ["started", "failed", "started", "fired"]


def test_unexpected_exception_is_recorded_with_its_type(tmp_path):
    store = ScheduleStore(tmp_path)
    _task_schedule(store, now=T0, interval=60)

    def executor(request, **kwargs):
        raise RuntimeError("not an MvpRuntimeError")

    summary = run_due(store, now=T1, executor=executor, control_store=ControlStore(tmp_path))
    assert summary["failed"] == 1 and summary["fired"] == 0
    assert summary["results"][0]["status"] == "failed:UNEXPECTED:RuntimeError"
    assert store.list()[0].last_status == "failed:UNEXPECTED:RuntimeError"


# --- operator alerting (failure / recovery / downtime gap) -------------------

class RecordingNotifier:
    """Stands in for scheduler_cli.OperatorAlerter without a channel or registration."""

    def __init__(self, explode=False):
        self.calls: list[tuple[str, str]] = []
        self._explode = explode

    def __call__(self, key, text):
        self.calls.append((key, text))
        if self._explode:
            raise RuntimeError("transport down")


def _failing_executor(request, **kwargs):
    raise PersistenceError("LEDGER_WRITE_FAILED", "disk full")


def test_failed_fire_alerts_the_operator(tmp_path):
    store = ScheduleStore(tmp_path)
    _task_schedule(store, now=T0, interval=60)
    notifier = RecordingNotifier()
    run_due(store, now=T1, executor=_failing_executor, control_store=ControlStore(tmp_path),
            notifier=notifier)
    assert len(notifier.calls) == 1
    key, text = notifier.calls[0]
    assert key == store.list()[0].schedule_id
    assert "스케줄 실패" in text and "failed:LEDGER_WRITE_FAILED" in text


def test_healthy_fire_alerts_nothing(tmp_path):
    store = ScheduleStore(tmp_path)
    _task_schedule(store, now=T0, interval=60)
    notifier = RecordingNotifier()
    run_due(store, now=T1, executor=FakeExecutor(), control_store=ControlStore(tmp_path),
            notifier=notifier)
    assert notifier.calls == []          # steady green says nothing


def test_recovery_after_failure_alerts_once(tmp_path):
    store = ScheduleStore(tmp_path)
    _task_schedule(store, now=T0, interval=60)
    notifier = RecordingNotifier()
    run_due(store, now=T1, executor=_failing_executor, control_store=ControlStore(tmp_path),
            notifier=notifier)
    run_due(store, now=T2, executor=FakeExecutor(), control_store=ControlStore(tmp_path),
            notifier=notifier)
    assert len(notifier.calls) == 2
    assert "스케줄 복구" in notifier.calls[1][1]
    # A second healthy fire is silent again: the failure is behind us.
    run_due(store, now="2026-07-16T09:03:00Z", executor=FakeExecutor(),
            control_store=ControlStore(tmp_path), notifier=notifier)
    assert len(notifier.calls) == 2


def test_a_broken_notifier_never_breaks_scheduling(tmp_path):
    store = ScheduleStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    _task_schedule(store, now=T0, interval=60)
    summary = run_due(store, now=T1, ledger=ledger, executor=_failing_executor,
                      control_store=ControlStore(tmp_path), notifier=RecordingNotifier(explode=True))
    assert summary["failed"] == 1                       # the fire outcome is unaffected
    assert store.list()[0].last_status == "failed:LEDGER_WRITE_FAILED"
    # the ledger is still the truth, and the run is still properly closed
    assert [e["action"] for e in _events(ledger)] == ["started", "failed"]


def test_skipped_by_kill_switch_does_not_alert(tmp_path):
    # A kill is Thomas's own decision — telling him about it is noise, not news.
    store = ScheduleStore(tmp_path)
    control_store = ControlStore(tmp_path)
    control.apply_command(control_store, "kill", actor="op", now=T0)
    _task_schedule(store, now=T0, interval=60)
    notifier = RecordingNotifier()
    summary = run_due(store, now=T1, control_store=control_store, executor=FakeExecutor(),
                      notifier=notifier)
    assert summary["skipped"] == 1 and notifier.calls == []


# --- downtime gap detection ---------------------------------------------------

def test_overdue_schedules_finds_only_real_gaps(tmp_path):
    store = ScheduleStore(tmp_path)
    _task_schedule(store, now=T0, interval=60, request="due-soon")     # next_run = T1
    schedules = store.list()
    # One interval late is still normal operation (a tick lands a bit after due).
    assert scheduler.overdue_schedules(schedules, now=T2) == []
    # Two hours late cannot happen while a loop is running: it was not running.
    late = scheduler.overdue_schedules(schedules, now="2026-07-16T11:00:00Z")
    assert len(late) == 1 and late[0][1] == 7140                       # 09:01 -> 11:00


def test_overdue_ignores_disabled_schedules(tmp_path):
    store = ScheduleStore(tmp_path)
    _task_schedule(store, now=T0, interval=60, enabled=False)
    assert scheduler.overdue_schedules(store.list(), now="2026-07-16T11:00:00Z") == []


def test_startup_gap_is_recorded_and_alerted(tmp_path):
    from runtime.mvp_runtime.scheduler_cli import report_startup_gap

    store = ScheduleStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    _task_schedule(store, now=T0, interval=60)
    notifier = RecordingNotifier()
    late = report_startup_gap(store, now="2026-07-16T11:00:00Z", ledger=ledger, alerter=notifier)

    assert len(late) == 1
    event = json.loads((ledger.root / "scheduler_events.jsonl").read_text(encoding="utf-8").strip())
    assert event["action"] == "gap_detected" and event["status"] == "overdue_seconds=7140"
    assert len(notifier.calls) == 1 and "공백 감지" in notifier.calls[0][1]


def test_no_gap_records_nothing(tmp_path):
    from runtime.mvp_runtime.scheduler_cli import report_startup_gap

    store = ScheduleStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    _task_schedule(store, now=T0, interval=60)
    notifier = RecordingNotifier()
    assert report_startup_gap(store, now=T2, ledger=ledger, alerter=notifier) == []
    assert notifier.calls == []
    assert not (ledger.root / "scheduler_events.jsonl").exists()


# --- per-run records (started/terminal pairing, duration, abandonment) -------

def _events(ledger):
    path = ledger.root / "scheduler_events.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_a_fire_is_bracketed_by_started_and_terminal(tmp_path):
    store = ScheduleStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    _task_schedule(store, now=T0, interval=60)
    summary = run_due(store, now=T1, ledger=ledger, executor=FakeExecutor(),
                      control_store=ControlStore(tmp_path))

    started, terminal = _events(ledger)
    assert started["action"] == "started" and started["status"] == "running"
    assert terminal["action"] == "fired"
    # Same run id links the pair; the terminal one carries the measured duration.
    assert started["schedule_run_id"] == terminal["schedule_run_id"]
    assert isinstance(terminal["duration_ms"], int) and terminal["duration_ms"] >= 0
    assert summary["results"][0]["schedule_run_id"] == started["schedule_run_id"]


def test_a_failed_fire_still_closes_its_run(tmp_path):
    store = ScheduleStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    _task_schedule(store, now=T0, interval=60)
    run_due(store, now=T1, ledger=ledger, executor=_failing_executor,
            control_store=ControlStore(tmp_path))
    started, terminal = _events(ledger)
    assert terminal["action"] == "failed"
    assert started["schedule_run_id"] == terminal["schedule_run_id"]
    assert scheduler.find_abandoned_runs(_events(ledger)) == []   # closed, not abandoned


def test_a_skip_opens_no_run(tmp_path):
    store = ScheduleStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    control_store = ControlStore(tmp_path)
    control.apply_command(control_store, "kill", actor="op", now=T0)
    _task_schedule(store, now=T0, interval=60)
    run_due(store, now=T1, ledger=ledger, executor=FakeExecutor(), control_store=control_store)
    events = _events(ledger)
    assert [e["action"] for e in events] == ["skipped"]           # nothing ran, no run id
    assert "schedule_run_id" not in events[0]
    assert scheduler.find_abandoned_runs(events) == []


def test_run_ids_are_distinct_per_occurrence(tmp_path):
    store = ScheduleStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    _task_schedule(store, now=T0, interval=60)
    run_due(store, now=T1, ledger=ledger, executor=FakeExecutor(), control_store=ControlStore(tmp_path))
    run_due(store, now=T2, ledger=ledger, executor=FakeExecutor(), control_store=ControlStore(tmp_path))
    ids = {e["schedule_run_id"] for e in _events(ledger) if "schedule_run_id" in e}
    assert len(ids) == 2


def test_a_process_killed_mid_fire_leaves_a_detectable_orphan(tmp_path):
    """The gap L3a could not close: a fire that never returns writes no terminal event,
    so the occurrence vanished silently. The orphaned start is now the evidence."""
    store = ScheduleStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    _task_schedule(store, now=T0, interval=60)

    def _killed(request, **kwargs):
        raise KeyboardInterrupt       # stands in for SIGKILL: escapes run_due entirely

    with pytest.raises(KeyboardInterrupt):
        run_due(store, now=T1, ledger=ledger, executor=_killed, control_store=ControlStore(tmp_path))

    events = _events(ledger)
    assert [e["action"] for e in events] == ["started"]           # no terminal was written
    orphans = scheduler.find_abandoned_runs(events)
    assert len(orphans) == 1 and orphans[0]["schedule_id"] == store.list()[0].schedule_id


def test_abandoned_is_reported_once_then_stays_quiet(tmp_path):
    from runtime.mvp_runtime.scheduler_cli import report_abandoned_runs

    store = ScheduleStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    _task_schedule(store, now=T0, interval=60)

    def _killed(request, **kwargs):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_due(store, now=T1, ledger=ledger, executor=_killed, control_store=ControlStore(tmp_path))

    notifier = RecordingNotifier()
    found = report_abandoned_runs(ledger=ledger, now=T2, alerter=notifier)
    assert len(found) == 1
    closing = _events(ledger)[-1]
    assert closing["action"] == "abandoned" and closing["status"] == "abandoned_mid_run"
    assert closing["started_at"] == T1
    assert len(notifier.calls) == 1 and "스케줄 중단" in notifier.calls[0][1]

    # A later startup must not re-report it: `abandoned` is itself terminal.
    assert report_abandoned_runs(ledger=ledger, now=T2, alerter=notifier) == []
    assert len(notifier.calls) == 1


def test_abandoned_scan_survives_an_unreadable_ledger(tmp_path):
    from runtime.mvp_runtime.scheduler_cli import report_abandoned_runs

    ledger = LedgerStore(tmp_path / "ledger")
    ledger.root.mkdir(parents=True, exist_ok=True)
    (ledger.root / "scheduler_events.jsonl").write_text("{not json\n", encoding="utf-8")
    # Diagnosis, not a gate: the tick loop must still start.
    assert report_abandoned_runs(ledger=ledger, now=T2, alerter=RecordingNotifier()) == []


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
    assert [e["action"] for e in _events(ledger)] == ["started", "fired", "skipped"]


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
    # Since the tick-survival hardening the failure no longer propagates: it is
    # recorded on the failing schedule and the batch completes normally.
    summary = run_due(store, now=T1, executor=executor, control_store=ControlStore(tmp_path))
    assert summary["fired"] == 1 and summary["failed"] == 1

    by_id = {s.schedule_id: s for s in store.list()}
    # The first schedule executed and its claim survived the later failure: it will NOT
    # fire again at the same tick time.
    assert executor.calls == ["first", "second"]
    assert by_id[first.schedule_id].next_run_at > T1
    assert by_id[first.schedule_id].last_status == "COMPLETED"
    # The second schedule's occurrence was claimed too (at-most-once: a crash drops the
    # occurrence rather than doubling it, matching the kill-skip rule) — and its
    # failure is durable state, not a dead process.
    assert by_id[second.schedule_id].next_run_at > T1
    assert by_id[second.schedule_id].last_status == "failed:LEDGER_WRITE_FAILED"
