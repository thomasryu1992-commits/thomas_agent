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
import pathlib
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
    Core or a model. Returns the list the worker's one call lands in.

    **The reply mirrors `run_task`'s real shape, ids and all**, and that is load-bearing
    rather than tidiness: this fake used to answer with a top-level ``task_id``, which the
    real pipeline has never returned — it carries the ids on the received-task record. So the
    worker read ``result["task_id"]``, every dispatch reply carried ``task_id: null``, and the
    test suite could not see it because the fake was the only thing that ever put one there.
    """
    calls: list[dict] = []

    def _fake(raw_request, **kwargs):
        calls.append({"raw_request": raw_request, **kwargs})
        return {
            "status": "COMPLETED",
            "final_response": "ok",
            "records": {
                "received_task": {"identity": {"task_id": "task-1", "trace_id": "trace-1"}},
            },
        }

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
        assert reply["trace_id"] == "trace-1"
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


# --- attribution: two callers, and only one of them can be the scheduler ------

def test_the_default_profile_is_the_assistant(tmp_path, captured_run_task):
    """The door sends no profile, so absence must keep meaning what it meant before the
    scheduler existed as a caller."""
    pipeline_worker.apply_work(_valid(), control_store=ControlStore(tmp_path))
    call = captured_run_task[0]
    assert call["requester_id"] == ASSISTANT_ACTOR
    assert call["requester_type"] == "agent"
    assert call["channel"] == "agent"


def test_the_scheduler_profile_records_the_scheduler_not_the_assistant(tmp_path, captured_run_task):
    """The whole point of the profile: a delegated schedule fire must not land in the ledger
    as assistant-initiated work, because an operator reads that column to tell the two apart."""
    out = pipeline_worker.apply_work(
        _valid(actor_profile=pipeline_worker.SCHEDULER_PROFILE, reason="scheduler:sched_abc"),
        control_store=ControlStore(tmp_path),
    )
    call = captured_run_task[0]
    assert call["requester_id"] == "mvp.scheduler"
    assert call["requester_type"] == "scheduler"
    assert call["channel"] == "scheduler"
    assert out["actor"] == "mvp.scheduler"
    # Verbatim, so the row reads exactly as an in-process scheduled run's did before the
    # delegation — no `assistant_bridge:dispatch:` prefix on scheduler work.
    assert call["source_ref"] == "scheduler:sched_abc"
    assert ASSISTANT_ACTOR not in call["source_ref"]


@pytest.mark.parametrize("bogus", ["operator", "thomas", "", "  ", 42, ["scheduler"]])
def test_an_unknown_profile_is_refused_never_defaulted(tmp_path, bogus, captured_run_task):
    """Defaulting an unrecognised profile to the assistant would file work under an actor its
    caller did not name — the ledger-honesty failure this field exists to prevent."""
    with pytest.raises(ControlBlocked) as exc:
        pipeline_worker.apply_work(
            _valid(actor_profile=bogus), control_store=ControlStore(tmp_path),
        )
    assert exc.value.reason_code == "ACTOR_PROFILE_NOT_PERMITTED"
    assert not captured_run_task


def test_the_assistant_cannot_express_the_scheduler_profile(tmp_path):
    """The property the attribution split rests on, asserted at the door rather than argued.

    Both callers run as uid 10001, so SO_PEERCRED cannot separate them and a second socket
    would not either. What keeps the assistant out of the scheduler's identity is that the
    door's key set is closed: a frame carrying `actor_profile` is refused before it is ever
    forwarded, so the string cannot reach the worker from the assistant's side."""
    assert "actor_profile" not in dispatch_bridge._ALLOWED_KEYS
    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(
            {"request": "analyze", "kind": "analysis", "reason": "x",
             "actor_profile": pipeline_worker.SCHEDULER_PROFILE},
            control_store=ControlStore(tmp_path), execute=lambda *a, **k: {"ok": True},
        )
    assert exc.value.reason_code == "ARGUMENT_NOT_ACCEPTED"


@unix_only
def test_a_request_through_the_door_is_attributed_to_the_assistant(tmp_path, monkeypatch,
                                                                   captured_run_task):
    """The other half, end to end rather than by inspection: work that arrives through the
    door is recorded as the assistant's, because the door sends no profile and the worker's
    default is the assistant. Driven through both real sockets, so it would catch a door that
    started sending one."""
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
            door_path, _valid(reason="whose work is this"), deadline_seconds=10.0,
        )
        assert reply["actor"] == ASSISTANT_ACTOR
        assert captured_run_task[0]["requester_id"] == ASSISTANT_ACTOR
        assert captured_run_task[0]["requester_type"] == "agent"
    finally:
        for server in (door, worker):
            server.shutdown()
            server.server_close()


# --- the job surface (Phase 2 D5: the model call, not the fire) ---------------

def _inventory():
    from runtime.mvp_runtime.crypto.data_review import build_data_inventory
    return build_data_inventory([], [])


def test_a_job_runs_the_model_call_and_returns_the_whole_record(tmp_path):
    """The record travels whole: the scheduler appends it, renders the operator sheet from
    it, and judges the stall on it. Summarising here would make the worker the authority on
    a record it does not own."""
    from runtime.mvp_runtime.crypto.data_review import (
        DATA_REVIEW_RECORD_TYPE, MockDataReviewProvider,
    )
    out = pipeline_worker.apply_work(
        {"job": pipeline_worker.JOB_DATA_REVIEW, "inventory": _inventory(),
         "reason": "scheduler:crypto_data_review"},
        control_store=ControlStore(tmp_path),
        providers={"validator_provider": MockDataReviewProvider()},
    )
    assert out["ok"] is True
    assert out["job"] == pipeline_worker.JOB_DATA_REVIEW
    assert out["record"]["record_type"] == DATA_REVIEW_RECORD_TYPE
    assert out["record"]["review_id"]


def test_a_job_never_runs_the_pipeline(tmp_path, captured_run_task):
    """A job is not a Task: no Role, no P3 binding, no `run_task`. What it shares with the
    dispatch path is the credentials and the kill switch, and nothing else."""
    from runtime.mvp_runtime.crypto.data_review import MockDataReviewProvider
    pipeline_worker.apply_work(
        {"job": pipeline_worker.JOB_DATA_REVIEW, "inventory": _inventory()},
        control_store=ControlStore(tmp_path),
        providers={"validator_provider": MockDataReviewProvider()},
    )
    assert captured_run_task == []


@pytest.mark.parametrize("bogus", ["crypto_backtest", "anything", "", 42, ["x"]])
def test_an_unnamed_job_is_refused(tmp_path, bogus):
    with pytest.raises(ControlBlocked) as exc:
        pipeline_worker.apply_work(
            {"job": bogus, "inventory": _inventory()}, control_store=ControlStore(tmp_path),
        )
    assert exc.value.reason_code == "JOB_NOT_PERMITTED"


def test_a_job_frame_carrying_a_dispatch_is_refused(tmp_path, captured_run_task):
    """Two requests in one frame: guessing which was meant is how a caller gets the other."""
    with pytest.raises(ControlBlocked) as exc:
        pipeline_worker.apply_work(
            {"job": pipeline_worker.JOB_DATA_REVIEW, "inventory": _inventory(),
             "request": "analyze this", "kind": "analysis"},
            control_store=ControlStore(tmp_path),
        )
    assert exc.value.reason_code == "ARGUMENT_NOT_ACCEPTED"
    assert captured_run_task == []


@pytest.mark.parametrize("bad", [None, {}, "not an object", 42])
def test_a_job_without_an_inventory_is_refused(tmp_path, bad):
    """The worker collects nothing — the caller owns the state reads, and an empty inventory
    would produce a review of nothing that still reads like a review."""
    frame = {"job": pipeline_worker.JOB_DATA_REVIEW}
    if bad is not None:
        frame["inventory"] = bad
    with pytest.raises(ControlBlocked) as exc:
        pipeline_worker.apply_work(frame, control_store=ControlStore(tmp_path))
    assert exc.value.reason_code == "INVENTORY_REQUIRED"


def test_a_job_is_refused_while_killed(tmp_path):
    """A job spends model quota, so it is new work and a halt stops it."""
    from runtime.mvp_runtime.crypto.data_review import MockDataReviewProvider
    store = ControlStore(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="test", now=NOW,
                          reason="halt", ledger=FakeLedger())
    with pytest.raises(ControlBlocked) as exc:
        pipeline_worker.apply_work(
            {"job": pipeline_worker.JOB_DATA_REVIEW, "inventory": _inventory()},
            control_store=store, providers={"validator_provider": MockDataReviewProvider()},
        )
    assert exc.value.reason_code == store.load().refusal_reason_code()


def test_the_assistant_cannot_express_a_job(tmp_path):
    """Same property the actor profile rests on: the door's key set is closed, so `job`
    cannot reach the worker from the assistant's side at all."""
    assert "job" not in dispatch_bridge._ALLOWED_KEYS
    assert "inventory" not in dispatch_bridge._ALLOWED_KEYS
    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(
            {"request": "x", "kind": "analysis", "reason": "y",
             "job": pipeline_worker.JOB_DATA_REVIEW},
            control_store=ControlStore(tmp_path), execute=lambda *a, **k: {"ok": True},
        )
    assert exc.value.reason_code == "ARGUMENT_NOT_ACCEPTED"


def test_the_worker_states_a_frame_ceiling_the_inventory_fits_in():
    """A job frame carries an INPUT, not a sentence. The measured inventory was ~3.3 KB on
    the live host and grows with the venue's vocabulary; the shared 8 KiB console default
    fails as a dropped connection, which reads as the worker being down."""
    import json
    assert pipeline_worker.MAX_FRAME_BYTES > socket_door.MAX_FRAME_BYTES
    frame = json.dumps({"job": pipeline_worker.JOB_DATA_REVIEW, "inventory": _inventory()})
    assert len(frame.encode()) < pipeline_worker.MAX_FRAME_BYTES


# --- the proposer job (D6): the model half only ------------------------------

def test_the_proposer_job_returns_the_model_half_and_never_a_verdict(tmp_path):
    """What crosses back is the generation, not a record: the verdicts need the market frame,
    which this worker never sees. A worker that judged would need the frame and would put a
    second feature source beside the one the router reads."""
    out = pipeline_worker.apply_work(
        {"job": pipeline_worker.JOB_CRYPTO_PROPOSE,
         "proposal_inputs": {"existing_families": ["trend_break"], "focus": None}},
        control_store=ControlStore(tmp_path),
    )
    assert out["ok"] is True
    assert set(out["generation"]) == {"raw", "invocation", "degraded"}
    assert "proposals" not in out and "record" not in out


def test_the_proposer_job_needs_no_market_frame(tmp_path):
    """The finding that unblocked D6, pinned: the prompt is built from scalars, so the job
    input carries no snapshot and the frame stays in the caller."""
    frame = {"job": pipeline_worker.JOB_CRYPTO_PROPOSE,
             "proposal_inputs": {"existing_families": [], "focus": "mean reversion"}}
    out = pipeline_worker.apply_work(frame, control_store=ControlStore(tmp_path))
    assert out["ok"] is True
    assert "snapshot" not in str(frame)


@pytest.mark.parametrize("inputs", [
    None, "not an object", {"existing_families": "trend_break"},
    {"existing_families": [1, 2]}, {"existing_families": ["x"] * 201},
    {"existing_families": [], "focus": 42},
])
def test_malformed_proposal_inputs_are_refused(tmp_path, inputs):
    frame = {"job": pipeline_worker.JOB_CRYPTO_PROPOSE}
    if inputs is not None:
        frame["proposal_inputs"] = inputs
    with pytest.raises(ControlBlocked) as exc:
        pipeline_worker.apply_work(frame, control_store=ControlStore(tmp_path))
    assert exc.value.reason_code == "PROPOSAL_INPUTS_REQUIRED"


def test_the_two_jobs_do_not_accept_each_others_input(tmp_path):
    """A frame carrying the other job's input is a caller that believed it would be used."""
    for job, wrong in ((pipeline_worker.JOB_CRYPTO_PROPOSE, {"inventory": {"a": 1}}),
                       (pipeline_worker.JOB_DATA_REVIEW, {"proposal_inputs": {"a": 1}})):
        frame = {"job": job, **wrong}
        if job == pipeline_worker.JOB_CRYPTO_PROPOSE:
            frame["proposal_inputs"] = {"existing_families": []}
        else:
            frame["inventory"] = {"venue": "binance"}
        with pytest.raises(ControlBlocked) as exc:
            pipeline_worker.apply_work(frame, control_store=ControlStore(tmp_path))
        assert exc.value.reason_code == "ARGUMENT_NOT_ACCEPTED"


# --- the two assurance policies the dispatch path never had ------------------
#
# `operator_cli` has had `--independent-validation` since R7 and the worker had nothing, so
# every task the dispatch door forwarded — fifteen on record, including every blog draft — ran
# with AUTOMATIC checks only. Not a decision anyone took: `run_task`'s defaults are False and
# nothing passed anything else. These pin that the wiring exists AND that it stays off until a
# deployment turns it on, because both policies cost a model call per task.


def test_forwarded_work_runs_without_the_second_reviewer_by_default(monkeypatch):
    """OFF is the default here as it is on the operator loop. Turning it on is a line on the
    service's command — a deploy with a diff, not a behaviour that arrives with a rebuild."""
    seen = {}

    def _capture(text, **kwargs):
        seen.update(kwargs)
        return {"status": "COMPLETED", "final_response": "ok",
                "records": {"task": {"identity": {"task_id": "t", "trace_id": "r"}}}}

    monkeypatch.setattr("runtime.mvp_runtime.pipeline_worker.run_task", _capture)
    pipeline_worker.apply_work(
        {"request": "분석해줘", "kind": "analysis", "reason": "test"},
        control_store=ControlStore(pathlib.Path("/tmp")), now="2026-08-23T09:00:00Z")
    assert seen["independent_validation"] is False
    assert seen["revise"] is False


def test_the_policies_reach_run_task_when_the_deployment_sets_them(monkeypatch):
    """The wiring itself. Before this the arguments did not exist on any frame or signature,
    so the dispatch path could not have been given them even by an operator who wanted to."""
    seen = {}

    def _capture(text, **kwargs):
        seen.update(kwargs)
        return {"status": "COMPLETED", "final_response": "ok",
                "records": {"task": {"identity": {"task_id": "t", "trace_id": "r"}}}}

    monkeypatch.setattr("runtime.mvp_runtime.pipeline_worker.run_task", _capture)
    pipeline_worker.apply_work(
        {"request": "분석해줘", "kind": "analysis", "reason": "test"},
        control_store=ControlStore(pathlib.Path("/tmp")), now="2026-08-23T09:00:00Z",
        independent_validation="AUTO", revise=True)
    assert seen["independent_validation"] == "AUTO"
    assert seen["revise"] is True
