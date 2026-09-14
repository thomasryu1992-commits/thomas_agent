"""The workflow manager loop: accepted plans become worker attempts and results (sequence 2, P04).

The loop lives inside the dispatch-bridge process and speaks the v2 dispatch frame to the
worker socket. Under test the socket call is injected, so a tick runs without a listener; one
test drives the real transport through a `SocketDoor` on this host.
"""

from __future__ import annotations

import os
import socket
import threading

import pytest

from runtime.mvp_runtime import dispatch_bridge_cli, socket_door, workflow as wf
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import ControlBlocked, WorkflowBlocked
from runtime.mvp_runtime.workflow_manager import CLIENT_ID_PREFIX, WORKER_UNAVAILABLE, WorkflowManager
from runtime.mvp_runtime.workflow_store import WorkflowStore

NOW = "2026-09-14T08:00:00Z"
LATER = "2026-09-14T08:03:00Z"
MUCH_LATER = "2026-09-14T08:20:00Z"

unix_only = pytest.mark.skipif(not socket_door.UNIX_SOCKETS_AVAILABLE, reason="the worker door listens on AF_UNIX")


def _plan(steps=None, budget=6):
    return {
        "schema_version": "workflow_plan.v0.1", "goal": "g",
        "steps": steps or [{"id": "research", "capability": "research", "request": "조사", "reason": "why",
                            "naver_keywords": "사장님", "max_attempts": 2}],
        "budget": {"max_model_calls": budget},
    }


def _ok_reply(frame):
    return {"ok": True, "kind": frame["kind"], "task_id": "task_1", "trace_id": "trace_1",
            "registry_entry_id": "treg_1", "final_response": "done", "actor": "assistant_bridge"}


class _Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def _manager(tmp_path, call, *, door_live=True, concurrency=2, synchronous=True, clock=None, log=None):
    store = WorkflowStore(tmp_path)
    logs: list[str] = []
    manager = WorkflowManager(
        store, control_store=ControlStore(tmp_path), worker_socket=tmp_path / "internal" / "pipeline.sock",
        call=call, door_is_live=lambda p: door_live, clock=clock or _Clock(), concurrency=concurrency,
        synchronous=synchronous, log=log or logs.append,
    )
    manager.logs = logs
    return store, manager


def _submit(store, plan=None, request_id="hermes-1"):
    return store.submit(principal="hermes", request_id=request_id, plan=plan or _plan(), now=NOW).workflow_id


# --- one tick ---------------------------------------------------------------------------------

def test_a_tick_claims_a_ready_step_sends_the_v2_frame_and_records_the_result(tmp_path):
    frames = []

    def call(path, frame, *, deadline_seconds):
        frames.append((path, frame, deadline_seconds))
        return _ok_reply(frame)

    store, manager = _manager(tmp_path, call)
    wid = _submit(store)
    report = manager.tick()
    assert report["claimed"] == 1 and report["error"] is None
    (path, frame, deadline) = frames[0]
    assert path == tmp_path / "internal" / "pipeline.sock" and deadline == 600.0
    assert frame == {"request": "조사", "kind": "research", "reason": "why", "naver_keywords": "사장님",
                     "client_id": f"{CLIENT_ID_PREFIX}{wid}", "workflow_id": wid,
                     "attempt_id": store.status_view(wid, now=NOW)["steps"][0]["current_attempt_id"]}
    view = store.status_view(wid, now=NOW)
    assert view["status"] == wf.W_COMPLETED
    (step,) = view["steps"]
    assert step["status"] == wf.S_SUCCEEDED and step["result_ref"] == "ledger:trace_1"
    attempt = store.attempt(step["current_attempt_id"])
    assert attempt["trace_id"] == "trace_1" and attempt["registry_entry_id"] == "treg_1"
    assert manager.tick()["claimed"] == 0


def test_the_frame_carries_no_null_and_names_its_options_only_when_asked(tmp_path):
    frames = []
    store, manager = _manager(tmp_path, lambda p, f, *, deadline_seconds: frames.append(f) or _ok_reply(f))
    _submit(store, _plan(steps=[{"id": "a", "capability": "analysis", "request": "x", "reason": "r",
                                 "options": {"independent_validation": True, "revise": False}}]))
    manager.tick()
    assert "naver_keywords" not in frames[0] and "options" not in frames[0]
    assert set(frames[0]) == {"request", "kind", "reason", "client_id", "attempt_id", "workflow_id", "workflow_options"}
    assert frames[0]["workflow_options"] == {"independent_validation": True}      # a False option does not travel


def test_a_worker_block_fails_the_attempt_under_its_own_code_and_the_step_retries(tmp_path):
    replies = iter([
        {"ok": False, "kind": "research", "reason_code": "PROVIDER_UNAVAILABLE", "reason": "r", "task_id": "task_1",
         "trace_id": "trace_1", "registry_entry_id": "treg_1"},
        _ok_reply({"kind": "research"}),
    ])
    store, manager = _manager(tmp_path, lambda p, f, *, deadline_seconds: next(replies))
    wid = _submit(store)
    manager.tick()
    step = store.status_view(wid, now=NOW)["steps"][0]
    assert step["status"] == wf.S_READY and step["last_reason_code"] is None   # RETRY_WAIT -> READY
    first = store.attempt(wf.attempt_id_for(step["step_id"], 1))
    assert first["status"] == wf.A_FAILED and first["reason_code"] == "PROVIDER_UNAVAILABLE"
    manager.tick()
    assert store.status_view(wid, now=NOW)["status"] == wf.W_COMPLETED


def test_a_transport_failure_records_nothing_and_leaves_the_attempt_to_its_lease(tmp_path):
    def down(path, frame, *, deadline_seconds):
        raise ControlBlocked("DOOR_UNREACHABLE", "timed out")

    clock = _Clock()
    store, manager = _manager(tmp_path, down, clock=clock)
    wid = _submit(store)
    manager.tick()
    step = store.status_view(wid, now=NOW)["steps"][0]
    assert step["status"] == wf.S_RUNNING                       # nothing recorded: it may be running
    assert any("DOOR_UNREACHABLE" in line and "left to its lease" in line for line in manager.logs)
    clock.now = MUCH_LATER                                      # past the 11-minute lease
    report = manager.tick()
    assert report["expired"] == 1
    assert store.status_view(wid, now=MUCH_LATER)["steps"][0]["attempts_opened"] == 2   # a new attempt opened


def test_a_missing_worker_door_fails_the_attempt_at_once_because_nothing_was_sent(tmp_path):
    calls = []
    store, manager = _manager(tmp_path, lambda *a, **k: calls.append(1), door_live=False)
    wid = _submit(store)
    manager.tick()
    assert calls == []
    step = store.status_view(wid, now=NOW)["steps"][0]
    assert store.attempt(wf.attempt_id_for(step["step_id"], 1))["reason_code"] == WORKER_UNAVAILABLE
    assert step["status"] == wf.S_READY                         # one attempt left; retried next tick


def test_a_paused_or_killed_runtime_claims_nothing(tmp_path, monkeypatch):
    frames = []
    store, manager = _manager(tmp_path, lambda p, f, *, deadline_seconds: frames.append(f) or _ok_reply(f))
    wid = _submit(store)

    class _Killed:
        mode = "KILLED"
        execution_allowed = False

    monkeypatch.setattr(manager._control, "load", lambda: _Killed())
    report = manager.tick()
    assert report["claimed"] == 0 and "KILLED" in report["skipped"] and frames == []
    assert store.status_view(wid, now=NOW)["steps"][0]["status"] == wf.S_READY


def test_dependents_flow_through_ticks_in_plan_order(tmp_path):
    frames = []
    store, manager = _manager(tmp_path, lambda p, f, *, deadline_seconds: frames.append(f) or _ok_reply(f))
    wid = _submit(store, _plan(steps=[
        {"id": "research", "capability": "research", "request": "1", "reason": "r"},
        {"id": "draft", "capability": "content", "request": "2", "reason": "r", "depends_on": ["research"],
         "input_refs": ["research"]},
    ]))
    assert manager.tick()["claimed"] == 1
    assert manager.tick()["claimed"] == 1
    assert [f["request"] for f in frames] == ["1", "2"]
    assert store.status_view(wid, now=NOW)["status"] == wf.W_COMPLETED


def test_a_tick_never_raises(tmp_path, monkeypatch):
    store, manager = _manager(tmp_path, lambda *a, **k: _ok_reply({"kind": "x"}))
    _submit(store)
    monkeypatch.setattr(store, "claim_ready", lambda **kw: (_ for _ in ()).throw(WorkflowBlocked("STORE_READ_ONLY", "no")))
    report = manager.tick()
    assert report["error"].startswith("STORE_READ_ONLY") and any("STORE_READ_ONLY" in line for line in manager.logs)
    monkeypatch.setattr(store, "expire_overdue", lambda **kw: (_ for _ in ()).throw(RuntimeError("disk")))
    assert manager.tick()["error"] == "RuntimeError: disk"


def test_concurrency_caps_the_attempts_in_flight(tmp_path):
    release = threading.Event()
    started = threading.Semaphore(0)

    def slow(path, frame, *, deadline_seconds):
        started.release()
        release.wait(5)
        return _ok_reply(frame)

    store, manager = _manager(tmp_path, slow, synchronous=False, concurrency=2)
    _submit(store, _plan(steps=[{"id": f"s{i}", "capability": "analysis", "request": "x", "reason": "r"}
                                for i in range(3)], budget=9))
    assert manager.tick()["claimed"] == 2
    started.acquire(timeout=2); started.acquire(timeout=2)
    assert manager.in_flight() == 2
    assert manager.tick()["skipped"] == "every slot is in flight"
    release.set()
    for _ in range(50):
        if manager.in_flight() == 0:
            break
        threading.Event().wait(0.05)
    assert manager.in_flight() == 0
    assert manager.tick()["claimed"] == 1
    manager.stop()


def test_the_startup_report_counts_inherited_running_attempts(tmp_path):
    def down(path, frame, *, deadline_seconds):
        raise ControlBlocked("DOOR_UNREACHABLE", "gone")

    store, manager = _manager(tmp_path, down)
    _submit(store)
    manager.tick()
    fresh = WorkflowManager(WorkflowStore(tmp_path), control_store=ControlStore(tmp_path),
                            worker_socket=tmp_path / "x.sock", log=lambda s: None)
    report = fresh.startup_report(now=LATER)
    assert report["inherited_running_attempts"] == 1 and len(report["attempt_ids"]) == 1


# --- the real transport --------------------------------------------------------------------

@unix_only
def test_the_real_socket_round_trip_through_a_door(tmp_path, monkeypatch):
    monkeypatch.setenv(socket_door.CLIENT_UID_ENV, str(os.getuid()))
    monkeypatch.setenv(socket_door.CLIENT_GID_ENV, str(os.getgid()))
    seen = []
    sock = tmp_path / "internal" / "pipeline.sock"
    sock.parent.mkdir(parents=True)

    def apply(request):
        seen.append(request)
        return _ok_reply(request)

    server = socket_door.SocketDoor(sock, apply, max_concurrent_requests=2)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        store = WorkflowStore(tmp_path)
        manager = WorkflowManager(store, control_store=ControlStore(tmp_path), worker_socket=sock,
                                  synchronous=True, log=lambda s: None, clock=_Clock())
        wid = _submit(store)
        report = manager.tick()
        assert report["claimed"] == 1 and report["error"] is None
        assert seen and seen[0]["kind"] == "research" and seen[0]["client_id"].startswith(CLIENT_ID_PREFIX)
        assert store.status_view(wid, now=NOW)["status"] == wf.W_COMPLETED
    finally:
        server.shutdown()
        server.server_close()


# --- the flag ---------------------------------------------------------------------------------

def test_the_manager_is_off_unless_the_compose_command_says_so():
    assert dispatch_bridge_cli._parse_args([]).workflow_manager is False
    args = dispatch_bridge_cli._parse_args(["--workflow-manager", "--workflow-concurrency", "1"])
    assert args.workflow_manager is True and args.workflow_concurrency == 1


# --- the attempt frame (P05) ------------------------------------------------------------------

def test_the_frame_names_the_attempt_and_carries_only_the_options_the_step_asked_for(tmp_path):
    frames = []
    store, manager = _manager(tmp_path, lambda p, f, *, deadline_seconds: frames.append(f) or _ok_reply(f))
    wid = _submit(store, _plan(steps=[
        {"id": "a", "capability": "analysis", "request": "x", "reason": "r", "options": {"independent_validation": True}},
        {"id": "b", "capability": "analysis", "request": "y", "reason": "r"},
    ], budget=6))
    manager.tick(); manager.tick()
    assert frames[0]["workflow_id"] == wid and frames[0]["attempt_id"].startswith("wfa_")
    assert frames[0]["workflow_options"] == {"independent_validation": True}
    assert "workflow_options" not in frames[1] and frames[1]["attempt_id"] != frames[0]["attempt_id"]


def test_the_echoed_spend_confirms_the_reservation(tmp_path):
    def call(path, frame, *, deadline_seconds):
        return {**_ok_reply(frame), "attempt_id": frame["attempt_id"], "workflow_id": frame["workflow_id"],
                "usage": {"model_calls": 2, "tokens_used": 900, "agent_invocations": 2, "revision_cycles": 0}}

    store, manager = _manager(tmp_path, call)
    wid = _submit(store)
    manager.tick()
    budget = store.budget_summary(wid)
    assert budget["confirmed_model_calls"] == 2 and budget["confirmed_tokens"] == 900
    assert budget["unconfirmed_model_calls"] == 0


def test_a_reply_that_echoes_another_attempt_is_not_applied(tmp_path):
    store, manager = _manager(tmp_path, lambda p, f, *, deadline_seconds: {**_ok_reply(f), "attempt_id": "wfa_" + "f" * 20})
    wid = _submit(store)
    manager.tick()
    step = store.status_view(wid, now=NOW)["steps"][0]
    assert step["status"] == wf.S_RUNNING and step["result_ref"] is None
    assert any("ATTEMPT_ECHO_MISMATCH" in line for line in manager.logs)
