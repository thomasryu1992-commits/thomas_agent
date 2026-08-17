"""The factory fire leaves the pass (#705 remedy: process separation).

Approved Thomas 2026-08-17, as proposed: the KIND_FACTORY handler fetches in-pass, hands the
pure ``run_factory`` call to a child, and the pass continues; ``_collect_factory_child``
closes the occurrence bracket on a later pass. These tests pin the orchestration — the
bracket, the spool, the failure directions, and concurrency 1 — with a tiny injected
compute so the machinery is what runs, not the mining.

The synthetic-registry tests (timeout / died / orphan / foreign root) inject the module
state directly instead of arranging a real slow or dead child, so they hold on platforms
without fork exactly as on production Linux — no skips, per the suite's skip floor.
"""

from __future__ import annotations

import json
import time

import pytest

from runtime.mvp_runtime import scheduler
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.crypto import factory
from runtime.mvp_runtime.scheduler import (
    KIND_FACTORY,
    ScheduleStore,
    build_schedule,
    run_due,
    schedule_run_id,
)
from runtime.mvp_runtime.store import LEDGER_REL, SCHEDULER_FILE, LedgerStore

T0 = "2026-07-22T10:00:00Z"
T1 = "2026-07-23T11:00:00Z"
T2 = "2026-07-23T11:01:00Z"


@pytest.fixture(autouse=True)
def _no_leftover_child():
    yield
    scheduler._reset_factory_child()


def _factory_store(tmp_path, *, count=1):
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        store.add(build_schedule(kind=KIND_FACTORY, request="", interval_seconds=86400,
                                 created_by=f"op{i}", now=T0))
    return store


def _events(tmp_path):
    path = tmp_path / LEDGER_REL / SCHEDULER_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _tiny_result(**overrides):
    base = {"candidates": [], "generation_id": "GEN-777", "accepted_count": 0,
            "requested_count": 0, "fused_count": 0, "seeded_topup_count": 0,
            "ablated_count": 0, "luck_filtered_count": 0}
    base.update(overrides)
    return base


def _tiny_factory(snapshot, **kwargs):
    return _tiny_result()


def test_a_spawn_pass_leaves_the_bracket_open_and_a_collect_pass_closes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(factory, "run_factory", _tiny_factory)
    store = _factory_store(tmp_path)
    ledger = LedgerStore(tmp_path / LEDGER_REL)

    first = run_due(store, now=T1, control_store=ControlStore(tmp_path),
                    ledger=ledger, repo_root=tmp_path)
    assert first["spawned"] == 1 and first["fired"] == 0 and first["failed"] == 0
    assert first["results"][0]["action"] == "spawned"
    run_id = first["results"][0]["schedule_run_id"]
    # The bracket is open: a started event exists under this run id, no terminal yet.
    events = [e for e in _events(tmp_path) if e.get("schedule_run_id") == run_id]
    assert [e["action"] for e in events] == ["started"]
    assert scheduler.factory_child_busy()

    assert scheduler.factory_child_join(60), "the child never wrote its result"
    second = run_due(store, now=T2, control_store=ControlStore(tmp_path),
                     ledger=ledger, repo_root=tmp_path)
    assert second["fired"] == 1 and second["spawned"] == 0
    entry = second["results"][0]
    assert entry["schedule_run_id"] == run_id, "the collect must close the SAME occurrence"
    assert entry["status"].startswith("generated=0/0")
    events = [e for e in _events(tmp_path) if e.get("schedule_run_id") == run_id]
    assert [e["action"] for e in events] == ["started", "fired"]
    assert not scheduler.factory_child_busy()
    # The spool is empty — nothing survives a completed occurrence.
    spool = scheduler._factory_spool_dir(tmp_path)
    assert not any(spool.glob("*.json"))
    # And the ledger carries the factory record the parent (single writer) appended.
    rows = (tmp_path / LEDGER_REL / "records.jsonl").read_text(encoding="utf-8").splitlines()
    assert any(json.loads(r)["kind"] == "crypto_factory" for r in rows)


def test_a_second_due_factory_fire_defers_unclaimed_while_a_child_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(factory, "run_factory", _tiny_factory)
    store = _factory_store(tmp_path, count=2)
    ledger = LedgerStore(tmp_path / LEDGER_REL)

    first = run_due(store, now=T1, control_store=ControlStore(tmp_path),
                    ledger=ledger, repo_root=tmp_path)
    # One spawned, the sibling deferred — and the deferral claimed nothing, so it stays due.
    assert first["spawned"] == 1 and first["deferred"] == 1
    deferral = [r for r in first["results"] if r["action"] == "deferred"]
    assert deferral and deferral[0]["status"] == "deferred_factory_child_running"
    assert sum(1 for s in store.list() if s.next_run_at <= T1) == 1

    assert scheduler.factory_child_join(60)
    second = run_due(store, now=T2, control_store=ControlStore(tmp_path),
                     ledger=ledger, repo_root=tmp_path)
    # The collect lands first, then the loop spawns the sibling the deferral preserved.
    assert second["fired"] == 1 and second["spawned"] == 1


def test_a_child_that_raises_is_a_terminal_failed_with_no_candidates(tmp_path, monkeypatch):
    def _boom(snapshot, **kwargs):
        raise ValueError("lattice exploded")
    monkeypatch.setattr(factory, "run_factory", _boom)
    store = _factory_store(tmp_path)
    ledger = LedgerStore(tmp_path / LEDGER_REL)

    run_due(store, now=T1, control_store=ControlStore(tmp_path), ledger=ledger, repo_root=tmp_path)
    assert scheduler.factory_child_join(60)
    summary = run_due(store, now=T2, control_store=ControlStore(tmp_path),
                      ledger=ledger, repo_root=tmp_path)
    assert summary["failed"] == 1
    status = summary["results"][0]["status"]
    assert status.startswith("failed:FACTORY_CHILD:") and "ValueError" in status
    assert not (tmp_path / LEDGER_REL / "records.jsonl").exists() or not any(
        json.loads(r)["kind"] == "crypto_factory"
        for r in (tmp_path / LEDGER_REL / "records.jsonl").read_text(encoding="utf-8").splitlines()
    ), "a failed child must append nothing"


class _FakeProcess:
    """Quacks like multiprocessing.Process for the synthetic-registry tests."""

    def __init__(self, *, alive, exitcode=None):
        self._alive = alive
        self.exitcode = exitcode
        self.killed = False

    def is_alive(self):
        return self._alive and not self.killed

    def kill(self):
        self.killed = True

    def join(self, timeout=None):
        return None


def _inject_child(tmp_path, store, *, process, deadline_offset=3600.0):
    """Register a synthetic in-flight child for the claimed first schedule."""
    schedule = store.list()[0]
    claimed = store.claim_due(schedule.schedule_id, now=T1)
    run_id = schedule_run_id(claimed, claimed_at=T1)
    spool = scheduler._factory_spool_dir(tmp_path)
    spool.mkdir(parents=True, exist_ok=True)
    meta_path = spool / f"{run_id}.meta.json"
    meta_path.write_text(json.dumps({
        "schedule_run_id": run_id, "schedule_id": claimed.schedule_id,
        "kind": claimed.kind, "spawned_at": T1,
        "timeout_seconds": scheduler.FACTORY_CHILD_TIMEOUT_SECONDS, "pid": None,
    }), encoding="utf-8")
    scheduler._factory_child = {
        "run_id": run_id, "schedule": claimed, "previous_status": claimed.last_status,
        "process": process, "spawned_monotonic": time.monotonic(),
        "deadline_monotonic": time.monotonic() + deadline_offset,
        "meta_path": meta_path, "result_path": spool / f"{run_id}.result.json",
        "root": tmp_path,
    }
    return run_id


def test_a_child_past_its_deadline_is_killed_and_failed(tmp_path):
    store = _factory_store(tmp_path)
    ledger = LedgerStore(tmp_path / LEDGER_REL)
    hung = _FakeProcess(alive=True)
    _inject_child(tmp_path, store, process=hung, deadline_offset=-1.0)

    summary = run_due(store, now=T2, control_store=ControlStore(tmp_path),
                      ledger=ledger, repo_root=tmp_path)
    assert summary["failed"] == 1
    assert summary["results"][0]["status"] == "failed:FACTORY_CHILD_TIMEOUT"
    assert hung.killed, "a timed-out child must not keep computing"
    assert not scheduler.factory_child_busy()


def test_a_child_that_died_without_a_result_is_failed_with_its_exit_code(tmp_path):
    store = _factory_store(tmp_path)
    ledger = LedgerStore(tmp_path / LEDGER_REL)
    _inject_child(tmp_path, store, process=_FakeProcess(alive=False, exitcode=1))

    summary = run_due(store, now=T2, control_store=ControlStore(tmp_path),
                      ledger=ledger, repo_root=tmp_path)
    assert summary["failed"] == 1
    assert summary["results"][0]["status"] == "failed:FACTORY_CHILD_DIED:exit=1"
    assert not scheduler.factory_child_busy()


def test_an_orphaned_spool_from_a_dead_process_fails_closed_and_is_cleaned(tmp_path):
    """A meta file with no registered child is a fire whose parent died: the next process
    fails it, never resumes it — even a result the child wrote after the fact is discarded,
    because nobody can vouch for a computation whose supervisor is gone."""
    store = _factory_store(tmp_path)
    ledger = LedgerStore(tmp_path / LEDGER_REL)
    schedule = store.list()[0]
    claimed = store.claim_due(schedule.schedule_id, now=T1)
    run_id = schedule_run_id(claimed, claimed_at=T1)
    spool = scheduler._factory_spool_dir(tmp_path)
    spool.mkdir(parents=True, exist_ok=True)
    (spool / f"{run_id}.meta.json").write_text(json.dumps({
        "schedule_run_id": run_id, "schedule_id": claimed.schedule_id, "kind": claimed.kind,
        "spawned_at": T1, "timeout_seconds": 900.0, "pid": None,
    }), encoding="utf-8")
    (spool / f"{run_id}.result.json").write_text(
        json.dumps({"ok": True, "result": _tiny_result()}), encoding="utf-8")
    assert scheduler._factory_child is None

    summary = run_due(store, now=T2, control_store=ControlStore(tmp_path),
                      ledger=ledger, repo_root=tmp_path)
    assert summary["failed"] == 1
    assert summary["results"][0]["status"] == "failed:FACTORY_CHILD_ORPHANED"
    assert summary["results"][0]["schedule_run_id"] == run_id
    assert not any(spool.glob("*.json")), "an orphaned spool must not survive"
    assert store.list()[0].last_status == "failed:FACTORY_CHILD_ORPHANED"


def test_a_foreign_roots_child_is_not_collected_into_this_store(tmp_path):
    """Module state meets a different repo root (a test-only construction, but the guard is
    what keeps one context's child from writing into another's ledger)."""
    store = _factory_store(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    _inject_child(tmp_path, store, process=_FakeProcess(alive=True))
    scheduler._factory_child["root"] = other

    summary = run_due(store, now=T2, control_store=ControlStore(tmp_path),
                      ledger=LedgerStore(tmp_path / LEDGER_REL), repo_root=tmp_path)
    assert summary["failed"] == 0 and summary["fired"] == 0
    assert scheduler.factory_child_busy(), "a foreign child is left alone, not consumed"
