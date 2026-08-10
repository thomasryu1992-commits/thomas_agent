"""The dispatch door — what the assistant can start, and what it provably cannot.

The halt door's tests guard one verb that must never get through; the read door's guard a
whole mutating category. These guard an *escalation*: a dispatch runs the pipeline, so the
claim is that every request this door can express stays at permission P3 on a non-trading
role, and that ``write_path`` — the one input that would lift a run above that — cannot be
smuggled in.

Since the plane separation the door validates and FORWARDS rather than running the pipeline
itself, so these capture the ``execute`` call it makes — exactly the three validated fields —
and exercise every refusal that precedes it. The engine's own contract (attribution, provider
caps, the reply shape) is tested in ``test_mvp_runtime_pipeline_worker``.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import control, dispatch_bridge, planner, socket_door
from runtime.mvp_runtime.control import ACTIVE, ControlStore
from runtime.mvp_runtime.errors import ControlBlocked
from runtime.mvp_runtime.socket_door import ASSISTANT_ACTOR
from runtime.mvp_runtime.store import LedgerStore

NOW = "2026-07-30T09:00:00Z"

unix_only = pytest.mark.skipif(
    not socket_door.UNIX_SOCKETS_AVAILABLE, reason="bridge doors listen on AF_UNIX only",
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
def captured_execute():
    """The forward to the pipeline worker, captured. The door's whole output is this one call
    plus the relayed reply, so the capture list is the assertion and not scaffolding."""
    calls: list[dict] = []

    def _execute(text, kind, reason):
        calls.append({"request": text, "kind": kind, "reason": reason})
        return {"ok": True, "kind": kind, "task_id": "task-1",
                "final_response": "ok", "actor": ASSISTANT_ACTOR}

    _execute.calls = calls
    return _execute


def _valid(**over):
    req = {"request": "analyze this idea", "kind": "analysis", "reason": "operator asked"}
    req.update(over)
    return req


# --- the permission surface stays exactly four kinds --------------------------

def test_allowed_kinds_is_exactly_the_four():
    assert dispatch_bridge._ALLOWED_KINDS == frozenset(
        {"analysis", "research", "translation", "content"}
    )


def test_every_allowed_kind_is_routable_and_development_is_excluded_not_missing():
    """Drift gate. Each allowed kind must be one the planner actually routes, and
    ``development`` must be a kind the table names but this door leaves out — proving the
    exclusion is a decision, not a rename that silently dropped it."""
    known = set(planner.REQUEST_KIND_CAPABILITIES)
    assert dispatch_bridge._ALLOWED_KINDS <= known
    assert "development" in known
    assert "development" not in dispatch_bridge._ALLOWED_KINDS
    # No trading/business kind is admitted; none exists in the table, and this door must not be
    # the place one first appears.
    for forbidden in ("development", "trade", "trading", "crypto", "business", "live_trader"):
        assert forbidden not in dispatch_bridge._ALLOWED_KINDS


@pytest.mark.parametrize("kind", ["development", "trade", "crypto", "live_trader", "admin", ""])
def test_a_kind_outside_the_set_is_refused(tmp_path, kind, captured_execute):
    store = ControlStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(
            _valid(kind=kind), control_store=store, execute=captured_execute,
        )
    assert exc.value.reason_code in {"KIND_NOT_PERMITTED", "MALFORMED_REQUEST"}
    assert not captured_execute.calls  # never reached the worker


# --- malformed / incomplete requests refuse before forwarding -----------------

def test_a_non_object_is_refused(tmp_path, captured_execute):
    store = ControlStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(
            ["not", "a", "dict"], control_store=store, execute=captured_execute,
        )
    assert exc.value.reason_code == "MALFORMED_REQUEST"
    assert not captured_execute.calls


def test_empty_request_text_is_refused(tmp_path, captured_execute):
    store = ControlStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(
            _valid(request="   "), control_store=store, execute=captured_execute,
        )
    assert exc.value.reason_code == "REQUEST_REQUIRED"
    assert not captured_execute.calls


def test_a_missing_reason_is_refused(tmp_path, captured_execute):
    store = ControlStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(
            {"request": "analyze this", "kind": "analysis"},
            control_store=store, execute=captured_execute,
        )
    assert exc.value.reason_code == "REASON_REQUIRED"
    assert not captured_execute.calls


@pytest.mark.parametrize("sneaky", ["write_path", "writer", "priority", "requester_type", "provider"])
def test_an_unnamed_key_is_refused_so_write_path_cannot_be_smuggled(tmp_path, sneaky, captured_execute):
    """The security-relevant one. ``write_path`` is the only input that lifts a run to a P3
    workspace write; the door refuses any key it does not name rather than silently dropping
    it, so an injected request cannot reach a write by adding a field."""
    store = ControlStore(tmp_path)
    req = _valid()
    req[sneaky] = "x"
    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(req, control_store=store, execute=captured_execute)
    assert exc.value.reason_code == "ARGUMENT_NOT_ACCEPTED"
    assert not captured_execute.calls


# --- kill switch --------------------------------------------------------------

def test_a_dispatch_is_refused_while_killed(tmp_path, captured_execute):
    store = ControlStore(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="test", now=NOW,
                          reason="test halt", ledger=FakeLedger())
    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(_valid(), control_store=store, execute=captured_execute)
    # the control state's own refusal code, not a dispatch-specific one
    assert exc.value.reason_code == store.load().refusal_reason_code()
    assert not captured_execute.calls  # a halt stops new work before it starts


# --- the happy path forwards exactly the validated fields and relays the reply

def test_a_valid_dispatch_forwards_validated_fields_and_relays_the_reply(tmp_path, captured_execute):
    store = ControlStore(tmp_path)
    assert store.load().mode == ACTIVE
    out = dispatch_bridge.apply_dispatch(
        _valid(reason="operator asked for a market read"),
        control_store=store, execute=captured_execute,
    )
    assert out["ok"] is True
    assert out["final_response"] == "ok"
    assert out["task_id"] == "task-1"
    assert out["actor"] == ASSISTANT_ACTOR

    # Exactly the three validated fields cross the boundary — no write_path, no writer, no
    # requester override, nothing the frame carried that the door did not validate.
    assert captured_execute.calls == [{
        "request": "analyze this idea", "kind": "analysis",
        "reason": "operator asked for a market read",
    }]


def test_a_pipeline_block_is_relayed_not_swallowed(tmp_path):
    """A BLOCK ran the pipeline and carries its task_id — a real answer, relayed as-is."""
    store = ControlStore(tmp_path)

    def _blocked(text, kind, reason):
        return {"ok": False, "kind": kind, "task_id": "task-9",
                "reason_code": "NO_ROUTABLE_ROLE", "reason": "none", "actor": ASSISTANT_ACTOR}

    out = dispatch_bridge.apply_dispatch(_valid(), control_store=store, execute=_blocked)
    assert out["ok"] is False
    assert out["reason_code"] == "NO_ROUTABLE_ROLE"
    assert out["task_id"] == "task-9"
    assert out["actor"] == ASSISTANT_ACTOR


def test_a_worker_refusal_surfaces_under_the_workers_own_reason_code(tmp_path):
    """A refusal envelope has no task_id: nothing ran. The door re-raises it as its own typed
    refusal so the assistant sees the worker's reason, not a generic failure."""
    store = ControlStore(tmp_path)

    def _refused(text, kind, reason):
        return {"ok": False, "reason_code": "KILLED",
                "reason": "runtime is KILLED; new work is blocked"}

    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(_valid(), control_store=store, execute=_refused)
    assert exc.value.reason_code == "KILLED"


def test_a_door_without_a_forwarder_fails_closed_never_running_in_process(tmp_path):
    """The fallback that must not exist: a door that cannot reach the worker refuses — it does
    not run the pipeline itself, because the keys that would take are exactly what this
    process no longer holds."""
    store = ControlStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(_valid(), control_store=store)
    assert exc.value.reason_code == "WORKER_UNAVAILABLE"


def test_a_transport_failure_from_the_forward_propagates_typed(tmp_path):
    store = ControlStore(tmp_path)

    def _down(text, kind, reason):
        raise ControlBlocked("WORKER_UNAVAILABLE", "the pipeline worker is not answering")

    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(_valid(), control_store=store, execute=_down)
    assert exc.value.reason_code == "WORKER_UNAVAILABLE"


# --- the door's own socket wires its stated ceiling ---------------------------

@unix_only
def test_the_door_wires_its_stated_ceiling(tmp_path, monkeypatch):
    """`MAX_CONCURRENT_REQUESTS = 2` existed but was never passed to the listener, so the door
    served the shared default of 4 while its own constant and `docs/BUILD_HISTORY.md` said 2.
    Wired with the split; pinned here so the constant and the listener cannot drift apart
    again."""
    monkeypatch.delenv(socket_door.CLIENT_GID_ENV, raising=False)
    monkeypatch.delenv(socket_door.CLIENT_UID_ENV, raising=False)
    server = dispatch_bridge.open_door(
        tmp_path / "dispatch.sock",
        control_store=ControlStore(tmp_path),
        ledger=LedgerStore(tmp_path),
        worker_socket=tmp_path / "absent-pipeline.sock",
    )
    try:
        assert server.max_concurrent_requests == dispatch_bridge.MAX_CONCURRENT_REQUESTS == 2
    finally:
        server.server_close()
