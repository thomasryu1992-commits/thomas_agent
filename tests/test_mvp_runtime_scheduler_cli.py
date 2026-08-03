"""R6 Scheduler CLI tests (add/list/enable/disable/remove/tick)."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import control
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
