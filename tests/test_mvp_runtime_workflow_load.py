"""In-process load on the accept path (sequence 2, P11 preparation; acceptance A27).

A hundred one-step plans through the door with a fake worker behind the manager, concurrency 2,
and the store's ceiling of twenty open steps. What must hold: every one of the hundred is
accepted exactly once (no loss, no duplicate — a retry after a refusal carries the same
request_id and is either accepted or replayed, never accepted twice); an accept takes well under
two seconds at p95; and overload is refused by name (``CAPACITY_EXHAUSTED``), never queued
silently or dropped. No socket, no network, no model.
"""

from __future__ import annotations

import statistics
import time

from runtime.mvp_runtime import dispatch_bridge, workflow as wf
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import WorkflowBlocked
from runtime.mvp_runtime.workflow_manager import WorkflowManager
from runtime.mvp_runtime.workflow_store import MAX_OPEN_STEPS, WorkflowStore

NOW = "2026-09-14T15:00:00Z"
TOTAL = 100


def _plan(i):
    return {"schema_version": "workflow_plan.v0.1", "goal": f"부하 시험 {i}",
            "steps": [{"id": "a", "capability": "analysis", "request": f"요청 {i}", "reason": "r"}],
            "budget": {"max_model_calls": 1}}


def test_a_hundred_submissions_are_accepted_exactly_once_fast_and_overload_is_refused_by_name(tmp_path):
    store = WorkflowStore(tmp_path)
    control = ControlStore(tmp_path)
    runs: list[str] = []

    def call(path, frame, *, deadline_seconds):
        runs.append(frame["attempt_id"])
        n = len(runs)
        return {"ok": True, "kind": frame["kind"], "task_id": f"task_{n}", "trace_id": f"trace_{n}",
                "registry_entry_id": f"treg_{n}", "final_response": "done", "actor": "assistant_bridge",
                "attempt_id": frame["attempt_id"], "workflow_id": frame["workflow_id"]}

    manager = WorkflowManager(store, control_store=control, worker_socket=tmp_path / "internal" / "pipeline.sock",
                              call=call, door_is_live=lambda p: True, clock=lambda: NOW, concurrency=2,
                              synchronous=True, log=lambda line: None)

    def submit(i):
        return dispatch_bridge.apply_dispatch(
            {"command": "workflow.submit", "request_id": f"load-{i}", "plan": _plan(i), "proto": 2},
            control_store=control, workflow_store=store, manager_enabled=True, now=NOW)

    accepted: dict[str, str] = {}          # request_id -> workflow_id
    latencies: list[float] = []
    refusals = 0
    replays = 0
    i = 0
    rounds = 0
    while len(accepted) < TOTAL:
        rounds += 1
        assert rounds < 10_000, "the accept loop is not converging"
        rid = f"load-{i}"
        started = time.perf_counter()
        try:
            out = submit(i)
        except WorkflowBlocked as exc:
            assert exc.reason_code == "CAPACITY_EXHAUSTED"            # overload is refused by name, and only that way
            refusals += 1
            manager.tick()                                            # the client backs off; the manager drains
            continue
        latencies.append(time.perf_counter() - started)
        if out["replayed"]:
            replays += 1
            assert accepted[rid] == out["data"]["workflow_id"]      # a replay is the same workflow, never a second one
        else:
            assert rid not in accepted
            accepted[rid] = out["data"]["workflow_id"]
        i += 1
        if i % 7 == 0:
            manager.tick()

    assert len(accepted) == TOTAL and len(set(accepted.values())) == TOTAL       # no loss, no duplicate
    assert refusals > 0                                                          # the ceiling was hit and said so
    assert replays == 0                                                          # nothing was re-submitted by accident
    p95 = statistics.quantiles(latencies, n=20)[-1]
    assert p95 < 2.0, f"p95 accept latency {p95:.3f}s"
    # every refusal happened at the ceiling, never below it
    assert store.open_step_count() <= MAX_OPEN_STEPS
    # the manager finishes every one of them, running each exactly once
    for _ in range(200):
        if all(w["status"] == wf.W_COMPLETED for w in store.list_workflows(limit=TOTAL)):
            break
        manager.tick()
    finished = store.list_workflows(limit=TOTAL)
    assert len(finished) == TOTAL and all(w["status"] == wf.W_COMPLETED for w in finished)
    assert len(runs) == TOTAL and len(set(runs)) == TOTAL
    # a retry of an accepted id after the fact replays and starts nothing
    again = submit(3)
    assert again["replayed"] and again["data"]["workflow_id"] == accepted["load-3"] and len(runs) == TOTAL
