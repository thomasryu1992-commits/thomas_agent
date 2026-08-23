"""R6 Scheduler CLI tests (add/list/enable/disable/remove/tick)."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import control, heartbeat, scheduler, task_registry
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.scheduler import KIND_PRUNE, KIND_TASK, ScheduleStore, build_schedule
from runtime.mvp_runtime.scheduler_cli import main, remaining_period
from runtime.mvp_runtime.store import LedgerStore
from runtime.mvp_runtime.working_memory import WorkingMemoryStore

T0 = "2026-07-16T09:00:00Z"
DUE = "2026-07-17T09:00:00Z"      # after a 1-day interval created at T0
PAST = "2026-07-16T08:00:00Z"


def _stores(tmp_path):
    return ScheduleStore(tmp_path), LedgerStore(tmp_path / "ledger")


def test_add_records_and_lists(tmp_path, capsys):
    store, ledger = _stores(tmp_path)
    rc = main(["add", "--kind", "analysis_task", "--request", "analyze X", "--interval-seconds", "3600"],
              store=store, ledger=ledger, now=T0)
    assert rc == 0
    assert "added schedule" in capsys.readouterr().out
    assert len(store.list()) == 1
    event = json.loads((ledger.root / "scheduler_events.jsonl").read_text(encoding="utf-8").strip())
    assert event["action"] == "created"
    rc = main(["list"], store=store, ledger=ledger)
    assert store.list()[0].kind == KIND_TASK
    assert "analysis_task" in capsys.readouterr().out


def test_add_task_without_request_is_blocked(tmp_path, capsys):
    store, ledger = _stores(tmp_path)
    rc = main(["add", "--kind", "analysis_task", "--interval-seconds", "3600"], store=store, ledger=ledger, now=T0)
    assert rc == 2
    assert "MISSING_REQUEST" in capsys.readouterr().err
    assert store.list() == []


def test_disable_enable_remove(tmp_path, capsys):
    store, ledger = _stores(tmp_path)
    s = build_schedule(kind=KIND_TASK, request="x", interval_seconds=3600, created_by="op", now=T0)
    store.add(s)
    assert main(["disable", s.schedule_id], store=store, ledger=ledger) == 0
    assert store.list()[0].enabled is False
    assert main(["enable", s.schedule_id], store=store, ledger=ledger) == 0
    assert store.list()[0].enabled is True
    assert main(["remove", s.schedule_id], store=store, ledger=ledger) == 0
    assert store.list() == []
    rc = main(["remove", "nope"], store=store, ledger=ledger)
    assert rc == 2
    assert "NOT_FOUND" in capsys.readouterr().err


# --- schedule-set mutations are audited ------------------------------------------
#
# Until 2026-08-04 only `add` wrote an event, so a schedule turned off left no trace in the
# ledger, none in the store (a disable rewrites `enabled` in place), and none in the repo
# (`schedules.jsonl` is per-machine). Why six `crypto_factory` schedules were off could only
# be answered by reading the REPLACEMENT schedules' `reason` text and inferring from a gap
# in fire timestamps. These tests pin that the answer is now recorded rather than inferred.

def _events(ledger):
    text = (ledger.root / "scheduler_events.jsonl").read_text(encoding="utf-8").strip()
    return [json.loads(line) for line in text.splitlines() if line]


def test_disable_enable_remove_each_record_one_event(tmp_path):
    store, ledger = _stores(tmp_path)
    s = build_schedule(kind=KIND_TASK, request="x", interval_seconds=3600, created_by="op", now=T0)
    store.add(s)  # added through the store, so the ledger holds only what the verbs write
    for verb in ("disable", "enable", "remove"):
        assert main([verb, s.schedule_id], store=store, ledger=ledger, now=T0) == 0

    events = _events(ledger)
    assert [e["action"] for e in events] == ["disabled", "enabled", "removed"]
    assert [e["status"] for e in events] == ["disabled", "enabled", "removed"]
    # `previously_enabled` is the state BEFORE each verb: on, then off, then on again.
    assert [e["previously_enabled"] for e in events] == [True, False, True]
    for e in events:
        assert e["schedule_id"] == s.schedule_id and e["kind"] == KIND_TASK
        assert e["created_at"] == T0
        # Not runs: `find_abandoned_runs` pairs starts to terminals by this field alone, so
        # its absence is what keeps a mutation from ever being read as an unfinished fire.
        assert "schedule_run_id" not in e


def test_a_reissued_disable_is_recorded_and_says_nothing_changed(tmp_path):
    """A no-op is still an operator action, and the event has to be able to say which it was."""
    store, ledger = _stores(tmp_path)
    s = build_schedule(kind=KIND_TASK, request="x", interval_seconds=3600, created_by="op", now=T0)
    store.add(s)
    assert main(["disable", s.schedule_id], store=store, ledger=ledger, now=T0) == 0
    assert main(["disable", s.schedule_id], store=store, ledger=ledger, now=T0) == 0

    first, second = _events(ledger)
    assert first["previously_enabled"] is True     # a real transition
    assert second["previously_enabled"] is False   # the same command, already in effect
    assert first["status"] == second["status"] == "disabled"


def test_removed_event_is_the_only_surviving_copy_of_the_record(tmp_path):
    """After the rewrite the store has nothing left, so the event carries what it destroyed."""
    store, ledger = _stores(tmp_path)
    s = build_schedule(kind=KIND_TASK, request="BTCUSDT 15m", interval_seconds=3600,
                       created_by="op", now=T0, reason="mining tier retired for cost")
    store.add(s)
    store.record_result(s.schedule_id, last_run_at=T0, last_status="generated=4 fused=4")
    assert main(["remove", s.schedule_id], store=store, ledger=ledger, now=T0) == 0

    assert store.list() == []
    event = _events(ledger)[0]
    assert event["request"] == "BTCUSDT 15m"
    assert event["reason"] == "mining tier retired for cost"
    assert event["last_run_at"] == T0 and event["last_status"] == "generated=4 fused=4"


def test_a_blocked_mutation_records_nothing(tmp_path, capsys):
    """NOT_FOUND changed no state, so an event claiming it did would be a false record."""
    store, ledger = _stores(tmp_path)
    for verb in ("disable", "enable", "remove"):
        assert main([verb, "nope"], store=store, ledger=ledger, now=T0) == 2
    capsys.readouterr()
    assert not (ledger.root / "scheduler_events.jsonl").exists()


def test_tick_runs_due_prune(tmp_path, capsys):
    store, ledger = _stores(tmp_path)
    control_store = ControlStore(tmp_path)
    wm = WorkingMemoryStore(tmp_path / "wm")
    wm.append([{"candidate_id": "stale", "scope": "task_working_memory", "status": "CANDIDATE",
                "content": "old", "created_at": PAST, "expires_at": PAST}])
    store.add(build_schedule(kind=KIND_PRUNE, request="", interval_seconds=86400, created_by="op", now=T0))
    rc = main(["tick", "--max-ticks", "1", "--interval-seconds", "0"],
              store=store, ledger=ledger, control_store=control_store, working_memory=wm, now=DUE)
    assert rc == 0
    assert "fired 1" in capsys.readouterr().out
    assert wm.read_all() == []


@pytest.mark.parametrize("interval, elapsed, expected", [
    (30.0, 20.0, 10.0),      # pm_scan's shape: the 20s scan is part of its own period
    (30.0, 0.0, 30.0),       # an idle tick still waits the whole period
    (30.0, 30.0, 0.0),       # exactly consumed
    (30.0, 190.0, 0.0),      # a crypto_factory fire: poll again now, never sleep negative
])
def test_the_tick_sleeps_the_remainder_of_its_period(interval, elapsed, expected):
    """Sleeping a fresh full interval on top of the work widened the poll grid to
    `work + interval`, and a schedule can only be claimed on a poll line — so the grid
    became the cadence. This is the loop half of the 140s pm_scan drift."""
    assert remaining_period(interval, elapsed) == expected


class _PassClock:
    """A monotonic that only advances while a pass is doing work (see the test below)."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class _WorkCostsTime(ScheduleStore):
    """A ScheduleStore that charges the clock for the one call only `run_due` makes.

    `claim_due` is the pass's own work: the loop's other store reader (the startup gap
    scan) is `list`, and it runs before the first tick. So the clock advances strictly
    BETWEEN the loop's two monotonic reads — which is the ordering under test."""

    def __init__(self, root, clock: _PassClock, cost: float) -> None:
        super().__init__(root)
        self._clock = clock
        self._cost = cost

    def claim_due(self, *args, **kwargs):
        self._clock.advance(self._cost)
        return super().claim_due(*args, **kwargs)


def test_the_loop_measures_the_pass_it_just_ran(tmp_path, capsys):
    # Wiring, not arithmetic: `pass_started` must be taken BEFORE run_due, so the sleep
    # shrinks by the work. Timing a REAL pass could not test that on Windows, where
    # time.monotonic has ~15.6ms granularity: a prune of 0 items finishes inside one
    # tick, elapsed measured 0.0, and the sleep came back the full 30.0. So the clock is
    # injected (like `sleep`) and the pass's own work is what moves it — a `pass_started`
    # read after run_due measures 0.0 and sleeps 30.0 here, on every platform.
    clock = _PassClock()
    store = _WorkCostsTime(tmp_path, clock, cost=5.0)
    ledger = LedgerStore(tmp_path / "ledger")
    control_store = ControlStore(tmp_path)
    wm = WorkingMemoryStore(tmp_path / "wm")
    store.add(build_schedule(kind=KIND_PRUNE, request="", interval_seconds=86400,
                             created_by="op", now=T0))
    slept: list[float] = []
    rc = main(["tick", "--max-ticks", "2", "--interval-seconds", "30"],
              store=store, ledger=ledger, control_store=control_store, working_memory=wm,
              now=DUE, sleep=slept.append, monotonic=clock)
    assert rc == 0
    assert len(slept) == 1                  # only between ticks, never after the last
    assert slept[0] == 25.0                 # the 30s period minus the 5s pass, not 30.0


def test_tick_skips_while_killed(tmp_path, capsys):
    store, ledger = _stores(tmp_path)
    control_store = ControlStore(tmp_path)
    control.apply_command(control_store, "kill", actor="op", now=T0)
    wm = WorkingMemoryStore(tmp_path / "wm")
    store.add(build_schedule(kind=KIND_TASK, request="x", interval_seconds=86400, created_by="op", now=T0))
    rc = main(["tick", "--max-ticks", "1", "--interval-seconds", "0"],
              store=store, ledger=ledger, control_store=control_store, working_memory=wm, now=DUE)
    assert rc == 0
    assert "skipped 1" in capsys.readouterr().out


def _budget_bound_tick(tmp_path, monkeypatch, *, budget):
    """One tick with a due non-risk schedule and the maintenance budget set by the caller.

    A negative budget binds on the first candidate (elapsed 0 > -1), which is the only way to
    drive this branch without a fire that genuinely runs for a minute — `run_due` reads the real
    `time.monotonic`, not the clock the CLI injects for its own sleep arithmetic.
    """
    from runtime.mvp_runtime import scheduler as scheduler_mod

    monkeypatch.setattr(scheduler_mod, "MAINTENANCE_PASS_BUDGET_SECONDS", budget)
    store, ledger = _stores(tmp_path)
    control_store = ControlStore(tmp_path)
    wm = WorkingMemoryStore(tmp_path / "wm")
    store.add(build_schedule(kind=KIND_PRUNE, request="", interval_seconds=86400,
                             created_by="op", now=T0))
    rc = main(["tick", "--max-ticks", "1", "--interval-seconds", "0"],
              store=store, ledger=ledger, control_store=control_store, working_memory=wm,
              now=DUE)
    assert rc == 0


def test_a_bound_maintenance_budget_says_so_on_the_pass_it_bound(tmp_path, capsys, monkeypatch):
    """The gap this closes: the service runs `--max-ticks 0`, so the end-of-loop summary that
    carries `deferred` is unreachable — the `while` never exits and SIGTERM raises no
    KeyboardInterrupt. The count accumulated for the life of the container and died with it.

    Narrowly the AGGREGATE, not the fact: the per-result line already printed every individual
    deferral, and the durable record is the `ACTION_DEFERRED` scheduler event (#596). What was
    missing was one line saying the budget bound this pass, without counting the others."""
    _budget_bound_tick(tmp_path, monkeypatch, budget=-1.0)
    err = capsys.readouterr().err
    assert "maintenance budget bound this pass" in err
    assert "deferred 1" in err
    assert "running total 1" in err


def test_a_pass_that_did_not_bind_says_nothing(tmp_path, capsys, monkeypatch):
    """Quiet by construction. A line every pass is a line an operator learns to skip, which is
    the failure the breaker and route watches were both written to avoid."""
    _budget_bound_tick(tmp_path, monkeypatch, budget=3600.0)
    err = capsys.readouterr().err
    assert "maintenance budget" not in err


def test_the_end_of_loop_summary_keeps_the_shape_ci_greps_for(tmp_path, capsys, monkeypatch):
    """`.github/workflows/docker-image.yml` greps the literal "fired 0, skipped 0, failed 0".
    The new line is additive and on stderr; this pins that the summary itself did not move."""
    _budget_bound_tick(tmp_path, monkeypatch, budget=-1.0)
    out = capsys.readouterr().out
    assert "fired 0, skipped 0, failed 0, deferred 1 over 1 tick(s)" in out


# === the tick loop can run one lane ================================================
# `docs/proposals/SCHEDULER_LANE_SPLIT_V0.1.md` (Thomas 2026-08-19). `--lane` selects one
# side of the RISK_KINDS/MAINTENANCE_KINDS partition; the default `all` is the
# single-process deployment, byte-for-byte the old behavior. The hard part of the split is
# not what a lane fires — it is what a lane's STARTUP must leave alone, because both lanes
# share one store and one ledger and each one's restart diagnosis reads shapes the other
# lane produces while healthy.

LATE = "2026-07-19T09:00:00Z"      # two full intervals past DUE for an 86400s schedule


def _tick(tmp_path, store, ledger, lane, *, now, capsys):
    rc = main(["tick", "--max-ticks", "1", "--interval-seconds", "0", "--lane", lane],
              store=store, ledger=ledger, control_store=ControlStore(tmp_path),
              working_memory=WorkingMemoryStore(tmp_path / "wm"), repo_root=tmp_path, now=now)
    assert rc == 0
    return capsys.readouterr().out


def test_a_risk_lane_tick_leaves_a_due_maintenance_schedule_for_its_own_lane(tmp_path, capsys):
    store, ledger = _stores(tmp_path)
    store.add(build_schedule(kind=KIND_PRUNE, request="", interval_seconds=86400,
                             created_by="op", now=T0))

    out = _tick(tmp_path, store, ledger, "risk", now=DUE, capsys=capsys)
    assert "fired 0" in out
    assert store.list()[0].next_run_at <= DUE          # still due, still unclaimed

    out = _tick(tmp_path, store, ledger, "maintenance", now=DUE, capsys=capsys)
    assert "fired 1" in out
    assert store.list()[0].last_run_at == DUE


def test_each_lane_stamps_its_own_heartbeat(tmp_path, capsys):
    """One heartbeat per PROCESS: a shared file would let a live maintenance loop keep a
    dead risk loop reading FRESH — the silent stall the probe exists to catch."""
    store, ledger = _stores(tmp_path)
    _tick(tmp_path, store, ledger, "risk", now=DUE, capsys=capsys)
    _tick(tmp_path, store, ledger, "maintenance", now=DUE, capsys=capsys)
    assert heartbeat.heartbeat_path(heartbeat.SCHEDULER_RISK_SERVICE, tmp_path).is_file()
    assert heartbeat.heartbeat_path(heartbeat.SCHEDULER_MAINTENANCE_SERVICE, tmp_path).is_file()
    assert not heartbeat.heartbeat_path(heartbeat.SCHEDULER_SERVICE, tmp_path).is_file()

    rc = main(["tick", "--max-ticks", "1", "--interval-seconds", "0"],
              store=store, ledger=ledger, control_store=ControlStore(tmp_path),
              working_memory=WorkingMemoryStore(tmp_path / "wm"), repo_root=tmp_path, now=DUE)
    assert rc == 0
    assert heartbeat.heartbeat_path(heartbeat.SCHEDULER_SERVICE, tmp_path).is_file()


def test_a_lanes_startup_never_abandons_the_other_lanes_run(tmp_path, capsys):
    """Both lanes share one ledger, so at a risk-lane restart the maintenance lane's fire
    currently RUNNING next door is an unpaired `started` — exactly the shape the abandoned
    scan notices. Closing it would leave the ledger claiming one occurrence both died and
    fired. A lane pairs up only its own dead."""
    store, ledger = _stores(tmp_path)
    s = build_schedule(kind=KIND_PRUNE, request="", interval_seconds=86400,
                       created_by="op", now=T0)
    ledger.append_scheduler_event(scheduler._scheduler_event(
        scheduler.ACTION_STARTED, s, now=T0, status="running", run_id="srun_next_door"))

    _tick(tmp_path, store, ledger, "risk", now=DUE, capsys=capsys)
    assert all(e["action"] != "abandoned" for e in ledger.read_scheduler_events())

    _tick(tmp_path, store, ledger, "maintenance", now=DUE, capsys=capsys)
    abandoned = [e for e in ledger.read_scheduler_events() if e["action"] == "abandoned"]
    assert len(abandoned) == 1 and abandoned[0]["schedule_run_id"] == "srun_next_door"


def test_a_lanes_startup_reports_only_its_own_gaps(tmp_path, capsys):
    """A maintenance schedule overdue at the risk lane's startup may just be waiting on a
    maintenance loop that has not started yet — a `gap_detected` for it here would be the
    other lane's downtime reported as fact by a process that cannot know it."""
    store, ledger = _stores(tmp_path)
    store.add(build_schedule(kind=KIND_PRUNE, request="", interval_seconds=86400,
                             created_by="op", now=T0))

    _tick(tmp_path, store, ledger, "risk", now=LATE, capsys=capsys)
    assert all(e["action"] != "gap_detected" for e in ledger.read_scheduler_events())

    _tick(tmp_path, store, ledger, "maintenance", now=LATE, capsys=capsys)
    gaps = [e for e in ledger.read_scheduler_events() if e["action"] == "gap_detected"]
    assert len(gaps) == 1 and gaps[0]["kind"] == KIND_PRUNE


def test_only_the_lane_that_fires_analysis_tasks_reconciles_them(tmp_path, capsys, monkeypatch):
    """KIND_TASK is a maintenance kind, so a SCHEDULER-origin registry entry belongs to the
    maintenance lane — the risk lane restarting must not mark the maintenance lane's
    in-flight analysis RUN_ABANDONED while it still runs next door."""
    calls: list[dict] = []

    def _record(store_, **kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(task_registry, "reconcile_stale_running", _record)
    store, ledger = _stores(tmp_path)

    _tick(tmp_path, store, ledger, "risk", now=DUE, capsys=capsys)
    assert calls == []

    _tick(tmp_path, store, ledger, "maintenance", now=DUE, capsys=capsys)
    assert len(calls) == 1 and calls[0]["origins"] == task_registry.SCHEDULER_ORIGINS
