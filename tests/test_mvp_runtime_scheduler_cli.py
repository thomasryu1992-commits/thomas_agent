"""R6 Scheduler CLI tests (add/list/enable/disable/remove/tick)."""

from __future__ import annotations

import json

from runtime.mvp_runtime import control
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.scheduler import KIND_PRUNE, KIND_TASK, ScheduleStore, build_schedule
from runtime.mvp_runtime.scheduler_cli import main
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
