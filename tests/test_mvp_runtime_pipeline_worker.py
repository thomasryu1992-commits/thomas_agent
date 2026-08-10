"""The pipeline worker — the engine's contract, and the placement that keeps it an engine.

Two claims are under test. First, the execution contract the dispatch door used to own:
attribution to ``assistant_bridge``, the capped ``run_task`` arguments, the reply shapes the
assistant has always seen. Second — the reason the worker exists — that it is NOT an
assistant door: its socket lives outside the assistant-mounted ``bridge/`` directory, it
refuses to listen without a stated uid allowlist, and it re-checks kind and kill switch on
arrival rather than trusting its peer.

These do not run the pipeline (that needs a local Core); ``run_task`` is captured. The
round-trip tests stand a real worker door and a real dispatch door on private sockets and
speak through both, so the forward path is exercised end to end without a model.
"""

from __future__ import annotations

import os
import threading

import pytest

from runtime.mvp_runtime import control, dispatch_bridge, pipeline_worker, socket_door
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import ControlBlocked
from runtime.mvp_runtime.socket_door import ASSISTANT_ACTOR
from runtime.mvp_runtime.store import LedgerStore

NOW = "2026-08-10T09:00:00Z"

unix_only = pytest.mark.skipif(
    not socket_door.UNIX_SOCKETS_AVAILABLE, reason="the worker listens on AF_UNIX only",
)


class FakeLedger:
    def __init__(self):
        self.control: list[dict] = []
        self.blocks: list[dict] = []

    def append_control(self, entry):
        self.control.append(entry)

    def append_block(self, entry):
        self.blocks.append(entry)

    def last_audit_hash(self):
        return "sha256:" + "ab" * 32


@pytest.fixture
def captured_run_task(monkeypatch):
    """Replace the pipeline call with a capture so the worker's contract is tested without a
    Core or a model. Returns the list the worker's one call lands in."""
    calls: list[dict] = []

    def _fake(raw_request, **kwargs):
        calls.append({"raw_request": raw_request, **kwargs})
        return {"status": "COMPLETED", "final_response": "ok", "task_id": "task-1"}

    monkeypatch.setattr(pipeline_worker, "run_task", _fake)
    return calls


def _valid(**over):
    req = {"request": "analyze this idea", "kind": "analysis", "reason": "operator asked"}
    req.update(over)
    return req


# --- the permission surface is the door's, imported not copied ----------------

def test_the_kind_set_is_the_doors_own_object():
    """One authority: widening the door's set widens the worker's, and the two can never
    disagree. Identity, not equality — a copy that happened to match today could drift."""
    assert pipeline_worker._ALLOWED_KINDS is dispatch_bridge._ALLOWED_KINDS


@pytest.mark.parametrize("kind", ["development", "trade", "crypto", "admin"])
def test_the_worker_re_refuses_a_kind_the_door_would_have_refused(tmp_path, kind, captured_run_task):
    """Defence in depth: the door already refuses these, and the worker refuses them again
    rather than trusting that every frame on its socket came through the door."""
    with pytest.raises(ControlBlocked) as exc:
        pipeline_worker.apply_work(_valid(kind=kind), control_store=ControlStore(tmp_path))
    assert exc.value.reason_code == "KIND_NOT_PERMITTED"
    assert not captured_run_task


def test_a_frame_without_a_kind_did_not_come_from_the_door(tmp_path, captured_run_task):
    """The door resolves its default before forwarding, so the worker never guesses one."""
    with pytest.raises(ControlBlocked) as exc:
        pipeline_worker.apply_work(
            {"request": "analyze this", "reason": "asked"},
            control_store=ControlStore(tmp_path),
        )
    assert exc.value.reason_code == "MALFORMED_REQUEST"
    assert not captured_run_task


def test_a_request_id_is_refused_here_because_idempotency_is_the_doors(tmp_path, captured_run_task):
    """The claim happens at the door, before the forward. A frame carrying `request_id` on
    this socket bypassed that, and honouring it would run unprotected work."""
    with pytest.raises(ControlBlocked) as exc:
        pipeline_worker.apply_work(
            _valid(request_id="req-1"), control_store=ControlStore(tmp_path),
        )
    assert exc.value.reason_code == "ARGUMENT_NOT_ACCEPTED"
    assert not captured_run_task


def test_a_killed_runtime_is_refused_at_the_worker_too(tmp_path, captured_run_task):
    """A halt that lands between the door's check and the run must still stop the run."""
    store = ControlStore(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="test", now=NOW,
                          reason="test halt", ledger=FakeLedger())
    with pytest.raises(ControlBlocked) as exc:
        pipeline_worker.apply_work(_valid(), control_store=store)
    assert exc.value.reason_code == store.load().refusal_reason_code()
    assert not captured_run_task


# --- the execution contract, moved here with the execution --------------------

def test_a_valid_dispatch_runs_capped_and_attributed(tmp_path, captured_run_task):
    out = pipeline_worker.apply_work(
        _valid(reason="operator asked for a market read"),
        control_store=ControlStore(tmp_path),
    )
    assert out["ok"] is True
    assert out["kind"] == "analysis"
    assert out["final_response"] == "ok"
    assert out["actor"] == ASSISTANT_ACTOR

    assert len(captured_run_task) == 1
    call = captured_run_task[0]
    assert call["request_kind"] == "analysis"
    assert call["requester_id"] == ASSISTANT_ACTOR
    assert call["requester_type"] == "agent"
    assert call["channel"] == "agent"
    assert call["authenticated"] is True
    assert call["keyword_seeds"] is None  # no seeds sent -> the run carries none
    assert "operator asked for a market read" in call["source_ref"]
    # the escalation inputs are never passed
    assert "write_path" not in call
    assert "writer" not in call


# --- the naver_keywords key (the lane, through the worker) --------------------

def test_forwarded_seeds_reach_run_task_as_keyword_seeds(tmp_path, captured_run_task):
    out = pipeline_worker.apply_work(
        _valid(kind="research", naver_keywords=" 미리캔버스, 포스터제작 "),
        control_store=ControlStore(tmp_path),
    )
    assert out["ok"] is True
    assert captured_run_task[0]["keyword_seeds"] == "미리캔버스, 포스터제작"


@pytest.mark.parametrize("bad", [42, "", "   ", ["미리캔버스"], "x" * 201])
def test_seeds_the_door_would_have_refused_are_refused_here_too(bad, tmp_path, captured_run_task):
    """Defence in depth, same rule as the door: a frame failing this did not come from the
    door, and the worker does not guess at what it meant."""
    with pytest.raises(ControlBlocked) as exc:
        pipeline_worker.apply_work(
            _valid(naver_keywords=bad), control_store=ControlStore(tmp_path),
        )
    assert exc.value.reason_code == "MALFORMED_REQUEST"
    assert captured_run_task == []


def test_a_pipeline_block_is_shaped_as_an_answer_with_its_kind(tmp_path, monkeypatch):
    """A BLOCK is a run: the reply names the kind (the door's ran-vs-refused discriminator)
    even when the pipeline blocked before a task existed and `task_id` is None."""
    def _blocked(raw_request, **kwargs):
        return {"status": "BLOCKED",
                "block": {"reason_code": "NO_ROUTABLE_ROLE", "message": "none"}}

    monkeypatch.setattr(pipeline_worker, "run_task", _blocked)
    out = pipeline_worker.apply_work(_valid(), control_store=ControlStore(tmp_path))
    assert out["ok"] is False
    assert out["kind"] == "analysis"
    assert out["task_id"] is None
    assert out["reason_code"] == "NO_ROUTABLE_ROLE"
    assert out["actor"] == ASSISTANT_ACTOR


def test_the_source_ref_reads_identically_across_the_split(tmp_path, captured_run_task):
    """Ledger continuity: attribution strings must not change shape because the process did."""
    pipeline_worker.apply_work(_valid(reason="why"), control_store=ControlStore(tmp_path))
    assert captured_run_task[0]["source_ref"] == f"{ASSISTANT_ACTOR}:dispatch: why"


# --- the placement: an engine, not a door -------------------------------------

def test_the_worker_socket_lives_under_internal_not_bridge(tmp_path, monkeypatch):
    monkeypatch.delenv(pipeline_worker.SOCKET_ENV, raising=False)
    assert pipeline_worker.SOCKET_REL.startswith(".runtime_governance_state/internal/")
    parts = {p.lower() for p in pipeline_worker.socket_path(tmp_path).parts}
    assert "internal" in parts
    assert "bridge" not in parts


@unix_only
def test_the_worker_refuses_to_listen_without_a_uid_allowlist(tmp_path, monkeypatch):
    """Group reachability alone is not a stated peer for the process that holds the keys."""
    monkeypatch.delenv(socket_door.CLIENT_UID_ENV, raising=False)
    monkeypatch.delenv(socket_door.CLIENT_GID_ENV, raising=False)
    with pytest.raises(ControlBlocked) as exc:
        pipeline_worker.open_door(
            tmp_path / "internal" / "pipeline.sock",
            control_store=ControlStore(tmp_path), ledger=LedgerStore(tmp_path),
        )
    assert exc.value.reason_code == "WORKER_UID_ALLOWLIST_REQUIRED"


@unix_only
def test_the_worker_refuses_a_socket_inside_the_assistant_mounted_directory(tmp_path, monkeypatch):
    """The tripwire: pointing the override back into `bridge/` recreates the pre-split
    exposure, so the worker refuses the path outright rather than serving it."""
    monkeypatch.setenv(socket_door.CLIENT_UID_ENV, str(os.getuid()))
    monkeypatch.delenv(socket_door.CLIENT_GID_ENV, raising=False)
    with pytest.raises(ControlBlocked) as exc:
        pipeline_worker.open_door(
            tmp_path / "bridge" / "pipeline.sock",
            control_store=ControlStore(tmp_path), ledger=LedgerStore(tmp_path),
        )
    assert exc.value.reason_code == "WORKER_SOCKET_IN_ASSISTANT_DIR"


# --- the forward path, end to end over real sockets ---------------------------

@unix_only
def test_a_dispatch_travels_door_to_worker_and_back(tmp_path, monkeypatch, captured_run_task):
    """The whole plane-R path without a model: client -> dispatch door (validates, forwards)
    -> worker (re-validates, runs the captured pipeline) -> reply relayed to the client."""
    monkeypatch.delenv(socket_door.CLIENT_GID_ENV, raising=False)

    worker_path = tmp_path / "internal" / "pipeline.sock"
    monkeypatch.setenv(socket_door.CLIENT_UID_ENV, str(os.getuid()))
    worker = pipeline_worker.open_door(
        worker_path, control_store=ControlStore(tmp_path), ledger=LedgerStore(tmp_path),
    )
    monkeypatch.delenv(socket_door.CLIENT_UID_ENV, raising=False)

    door_path = tmp_path / "bridge" / "dispatch.sock"
    door = dispatch_bridge.open_door(
        door_path, control_store=ControlStore(tmp_path), ledger=LedgerStore(tmp_path),
        worker_socket=worker_path,
    )

    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (worker, door)]
    for t in threads:
        t.start()
    try:
        reply = socket_door.call_door(
            door_path, _valid(reason="round trip"), deadline_seconds=10.0,
        )
        assert reply["ok"] is True
        assert reply["task_id"] == "task-1"
        assert reply["actor"] == ASSISTANT_ACTOR
        assert len(captured_run_task) == 1
        assert captured_run_task[0]["requester_id"] == ASSISTANT_ACTOR
    finally:
        for server in (door, worker):
            server.shutdown()
            server.server_close()


@unix_only
def test_a_dead_worker_answers_worker_unavailable_with_no_path_in_the_reply(tmp_path, monkeypatch):
    """Fail-closed, no fallback: with the worker gone the door refuses — it does not (and
    cannot) run the pipeline itself — and the assistant-facing envelope names no filesystem
    path, per the BRIDGE_ERROR redaction rule."""
    monkeypatch.delenv(socket_door.CLIENT_GID_ENV, raising=False)
    monkeypatch.delenv(socket_door.CLIENT_UID_ENV, raising=False)

    door_path = tmp_path / "bridge" / "dispatch.sock"
    door = dispatch_bridge.open_door(
        door_path, control_store=ControlStore(tmp_path), ledger=LedgerStore(tmp_path),
        worker_socket=tmp_path / "internal" / "pipeline.sock",  # never created
    )
    thread = threading.Thread(target=door.serve_forever, daemon=True)
    thread.start()
    try:
        reply = socket_door.call_door(
            door_path, _valid(reason="worker is down"), deadline_seconds=10.0,
        )
        assert reply["ok"] is False
        assert reply["reason_code"] == "WORKER_UNAVAILABLE"
        assert str(tmp_path) not in reply.get("reason", "")
    finally:
        door.shutdown()
        door.server_close()
