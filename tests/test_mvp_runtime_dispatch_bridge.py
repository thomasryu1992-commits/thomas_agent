"""The dispatch door — what the assistant can start, and what it provably cannot.

The halt door's tests guard one verb that must never get through; the read door's guard a
whole mutating category. These guard an *escalation*: a dispatch runs the pipeline, so the
claim is that every request this door can express stays at permission P3 on a non-trading
role, and that ``write_path`` — the one input that would lift a run above that — cannot be
smuggled in.

These do not run the pipeline (that needs a local Core). They check the door's contract by
capturing the ``run_task`` call it makes and by exercising every refusal that precedes it.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import control, dispatch_bridge, planner
from runtime.mvp_runtime.control import ACTIVE, ControlStore
from runtime.mvp_runtime.errors import ControlBlocked
from runtime.mvp_runtime.halt_bridge import ASSISTANT_ACTOR

NOW = "2026-07-30T09:00:00Z"


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
    """Replace the pipeline call with a capture so the door's contract is tested without a
    Core or a model. Returns the list the door's one call lands in."""
    calls: list[dict] = []

    def _fake(raw_request, **kwargs):
        calls.append({"raw_request": raw_request, **kwargs})
        return {"status": "COMPLETED", "final_response": "ok", "task_id": "task-1"}

    monkeypatch.setattr(dispatch_bridge, "run_task", _fake)
    return calls


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
def test_a_kind_outside_the_set_is_refused(tmp_path, kind, captured_run_task):
    store = ControlStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(_valid(kind=kind), control_store=store)
    assert exc.value.reason_code in {"KIND_NOT_PERMITTED", "MALFORMED_REQUEST"}
    assert not captured_run_task  # never reached the pipeline


# --- malformed / incomplete requests refuse before running --------------------

def test_a_non_object_is_refused(tmp_path, captured_run_task):
    store = ControlStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(["not", "a", "dict"], control_store=store)
    assert exc.value.reason_code == "MALFORMED_REQUEST"
    assert not captured_run_task


def test_empty_request_text_is_refused(tmp_path, captured_run_task):
    store = ControlStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(_valid(request="   "), control_store=store)
    assert exc.value.reason_code == "REQUEST_REQUIRED"
    assert not captured_run_task


def test_a_missing_reason_is_refused(tmp_path, captured_run_task):
    store = ControlStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch({"request": "analyze this", "kind": "analysis"}, control_store=store)
    assert exc.value.reason_code == "REASON_REQUIRED"
    assert not captured_run_task


@pytest.mark.parametrize("sneaky", ["write_path", "writer", "priority", "requester_type", "provider"])
def test_an_unnamed_key_is_refused_so_write_path_cannot_be_smuggled(tmp_path, sneaky, captured_run_task):
    """The security-relevant one. ``write_path`` is the only input that lifts a run to a P3
    workspace write; the door refuses any key it does not name rather than silently dropping
    it, so an injected request cannot reach a write by adding a field."""
    store = ControlStore(tmp_path)
    req = _valid()
    req[sneaky] = "x"
    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(req, control_store=store)
    assert exc.value.reason_code == "ARGUMENT_NOT_ACCEPTED"
    assert not captured_run_task


# --- kill switch --------------------------------------------------------------

def test_a_dispatch_is_refused_while_killed(tmp_path, captured_run_task):
    store = ControlStore(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="test", now=NOW,
                          reason="test halt", ledger=FakeLedger())
    with pytest.raises(ControlBlocked) as exc:
        dispatch_bridge.apply_dispatch(_valid(), control_store=store)
    # the control state's own refusal code, not a dispatch-specific one
    assert exc.value.reason_code == store.load().refusal_reason_code()
    assert not captured_run_task  # a halt stops new work before it starts


# --- the happy path calls run_task with capped, attributed arguments ----------

def test_a_valid_dispatch_runs_capped_and_attributed(tmp_path, captured_run_task):
    store = ControlStore(tmp_path)
    assert store.load().mode == ACTIVE
    out = dispatch_bridge.apply_dispatch(
        _valid(reason="operator asked for a market read"), control_store=store
    )
    assert out["ok"] is True
    assert out["final_response"] == "ok"
    assert out["actor"] == ASSISTANT_ACTOR

    assert len(captured_run_task) == 1
    call = captured_run_task[0]
    assert call["request_kind"] == "analysis"
    assert call["requester_id"] == ASSISTANT_ACTOR
    assert call["requester_type"] == "agent"
    assert call["channel"] == "agent"
    assert call["authenticated"] is True
    assert "operator asked for a market read" in call["source_ref"]
    # the escalation inputs are never passed
    assert "write_path" not in call
    assert "writer" not in call


def test_a_pipeline_block_is_surfaced_not_swallowed(tmp_path, monkeypatch):
    store = ControlStore(tmp_path)

    def _blocked(raw_request, **kwargs):
        return {"status": "BLOCKED", "block": {"reason_code": "NO_ROUTABLE_ROLE", "message": "none"}}

    monkeypatch.setattr(dispatch_bridge, "run_task", _blocked)
    out = dispatch_bridge.apply_dispatch(_valid(), control_store=store)
    assert out["ok"] is False
    assert out["reason_code"] == "NO_ROUTABLE_ROLE"
    assert out["actor"] == ASSISTANT_ACTOR
