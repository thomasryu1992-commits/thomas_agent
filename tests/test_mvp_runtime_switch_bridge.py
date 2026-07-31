"""The switch door — permission surface, the approval wall, and what a grant is bound to.

This door is the first one that can *start* trading, so most of what follows is negative: an
``enable`` never acts on the request that asks for it, and every way of presenting a grant that
Thomas did not give for exactly this action is refused. The halt door's tests guarded a verb
that could not exist here; these guard a verb that can, which is why there are more of them.

None of these need a local Core — the ask-construction path is captured, and every refusal
under test precedes it.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import approval as approval_mod
from runtime.mvp_runtime import control, switch_bridge
from runtime.mvp_runtime.control import ACTIVE, KILLED, PAUSED, ControlStore
from runtime.mvp_runtime.errors import ControlBlocked
from runtime.mvp_runtime.permission import TRADING_SWITCH_PERMISSION_SCOPE

NOW = "2026-07-31T09:00:00Z"
LATER = "2026-07-31T09:10:00Z"
MUCH_LATER = "2026-08-01T09:00:00Z"


class FakeLedger:
    def __init__(self):
        self.control: list[dict] = []

    def append_control(self, entry):
        self.control.append(entry)

    def last_audit_hash(self):
        return "sha256:" + "ab" * 32


class FakeApprovalStore:
    """Enough of ``ApprovalStore`` for the door: latest-wins reads, appends, and a path the
    spend lock can sit beside."""

    def __init__(self, tmp_path):
        self.path = tmp_path / "approvals.jsonl"
        self.records: dict[str, dict] = {}
        self.decisions: dict[str, dict] = {}
        self.appended: list[dict] = []

    def get(self, approval_id):
        return self.records.get(approval_id)

    def get_permission_decision(self, decision_id):
        return self.decisions.get(decision_id)

    def append(self, records):
        for record in records:
            self.appended.append(dict(record))
            self.records[record["approval_id"]] = dict(record)

    def append_permission_decision(self, decision):
        self.decisions[decision["permission_decision_id"]] = dict(decision)


def _apply(request, store, *, approvals=None, ledger=None, now=NOW):
    return switch_bridge.apply_switch(
        request, control_store=store, ledger=ledger or FakeLedger(),
        approval_store=approvals, now=now,
    )


def _grant(approval_id="approval_test", *, domain="crypto", status=None,
           scope=TRADING_SWITCH_PERMISSION_SCOPE, expires_at="2026-07-31T09:30:00Z",
           fingerprint="fp_bound"):
    """A grant record shaped like the fields the door reads."""
    return {
        "approval_id": approval_id,
        "status": status or approval_mod.STATUS_APPROVED,
        "permission_decision_id": "permdec_test",
        "validity": {"expires_at": expires_at},
        "action_fingerprint": fingerprint,
        "approved_action_snapshot": {
            "permission_scope": scope,
            "target_ref": f"trading_switch:{domain}",
        },
    }


@pytest.fixture
def approved(tmp_path, monkeypatch):
    """A store holding one APPROVED trading-switch grant, with the fingerprint check and the
    consumed-record builder stubbed — both are exercised on their own below."""
    store = FakeApprovalStore(tmp_path)
    store.records["approval_test"] = _grant()
    store.decisions["permdec_test"] = {"permission_decision_id": "permdec_test"}
    monkeypatch.setattr(switch_bridge, "compute_action_fingerprint", lambda s: "fp_bound")
    monkeypatch.setattr(
        switch_bridge.approval_mod, "build_consumed_record",
        lambda rec, dec, **kw: {**rec, "status": approval_mod.STATUS_CONSUMED},
    )
    return store


# --- stopping stays free ------------------------------------------------------

def test_disable_halts_without_any_approval(tmp_path):
    """An emergency control you must first get signed is not an emergency control."""
    store = ControlStore(tmp_path)
    out = _apply({"command": "disable", "reason": "assistant: stop trading now"}, store)
    assert out["ok"] is True
    assert store.load().mode == KILLED
    assert store.load().execution_allowed is False


def test_disable_pause_mode(tmp_path):
    store = ControlStore(tmp_path)
    out = _apply({"command": "disable", "mode": "pause", "reason": "assistant: hold"}, store)
    assert out["ok"] is True
    assert store.load().mode == PAUSED


def test_disable_is_idempotent(tmp_path):
    store = ControlStore(tmp_path)
    _apply({"command": "disable", "reason": "first"}, store)
    _apply({"command": "disable", "reason": "second"}, store)
    assert store.load().mode == KILLED


def test_disable_is_attributed_to_the_assistant(tmp_path):
    """A halt from SSH and a halt from the assistant must be distinguishable in the ledger."""
    store, ledger = ControlStore(tmp_path), FakeLedger()
    out = _apply({"command": "disable", "reason": "assistant: stop"}, store, ledger=ledger)
    assert out["actor"] == switch_bridge.ASSISTANT_ACTOR != "local_console"
    assert ledger.control and ledger.control[0]["actor"] == switch_bridge.ASSISTANT_ACTOR


def test_a_stop_must_state_its_reason(tmp_path):
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "disable"}, ControlStore(tmp_path))
    assert exc.value.reason_code == "REASON_REQUIRED"


# --- the raw control verbs are not nameable here ------------------------------

@pytest.mark.parametrize("verb", ["resume", "kill", "pause", "stop", "audit", "recovery"])
def test_control_verbs_cannot_be_named_directly(tmp_path, verb):
    """`enable`/`disable` are the whole surface. A caller cannot reach `control`'s verbs by
    naming them, so widening `control` cannot widen this door."""
    store = ControlStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": verb, "reason": "assistant: go"}, store)
    assert exc.value.reason_code == "VERB_NOT_PERMITTED"
    assert store.load().mode == ACTIVE


def test_resume_is_absent_from_the_verb_set():
    """The frozenset is the enforcement, not documentation of it."""
    assert control.CMD_RESUME not in switch_bridge._ALLOWED_COMMANDS
    assert switch_bridge._ALLOWED_COMMANDS == {"status", "enable", "disable"}


def test_the_stop_modes_stay_within_the_policy_emergency_controls():
    """Both stops this door applies are already granted emergency controls."""
    assert set(switch_bridge._DISABLE_MODES.values()) == {control.CMD_KILL, control.CMD_PAUSE}


# --- the frame ----------------------------------------------------------------

def test_an_unexpected_key_is_refused_not_ignored(tmp_path):
    """A field the door does not understand must never be read as consent to something it
    does. `write_path` is the shape that matters: the one lever that lifts a run's authority."""
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "status", "write_path": "/etc"}, ControlStore(tmp_path))
    assert exc.value.reason_code == "ARGUMENT_NOT_ACCEPTED"


def test_a_non_object_is_refused(tmp_path):
    with pytest.raises(ControlBlocked) as exc:
        _apply(["disable"], ControlStore(tmp_path))
    assert exc.value.reason_code == "MALFORMED_REQUEST"


@pytest.mark.parametrize("domain", ["forex", "equities", "prediction"])
def test_an_unswitched_domain_is_refused(tmp_path, domain):
    """`prediction` is in this list on purpose: it is the domain this door is shaped to accept
    later, and it must not be accepted before it has a switch to flip."""
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "disable", "reason": "x", "domain": domain}, ControlStore(tmp_path))
    assert exc.value.reason_code == "DOMAIN_NOT_PERMITTED"


def test_an_unknown_stop_mode_is_refused(tmp_path):
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "disable", "reason": "x", "mode": "destroy"}, ControlStore(tmp_path))
    assert exc.value.reason_code == "MODE_NOT_PERMITTED"


def test_status_is_read_only(tmp_path):
    store, ledger = ControlStore(tmp_path), FakeLedger()
    out = _apply({"command": "status"}, store, ledger=ledger)
    assert out["ok"] is True and out["mode"] == ACTIVE
    assert ledger.control == []          # a read writes no control event


# --- starting: the ask --------------------------------------------------------

def test_enable_without_a_grant_changes_nothing_and_asks(tmp_path, monkeypatch):
    """The whole point of the door. `enable` never acts on the frame that asks for it."""
    store = ControlStore(tmp_path)
    _apply({"command": "disable", "reason": "stopped first"}, store)
    approvals = FakeApprovalStore(tmp_path)
    monkeypatch.setattr(switch_bridge, "build_task", lambda *a, **k: {"task": "t"})
    monkeypatch.setattr(switch_bridge, "bind_task_to_core", lambda *a, **k: (None, {"bound": True}))
    monkeypatch.setattr(
        switch_bridge, "build_trading_switch_permission_decision",
        lambda *a, **k: {"permission_decision_id": "permdec_x"},
    )
    monkeypatch.setattr(
        switch_bridge.approval_mod, "build_approval_request",
        lambda dec, **k: {"approval_id": "approval_x",
                          "validity": {"expires_at": "2026-07-31T09:30:00Z"}},
    )

    out = _apply({"command": "enable", "reason": "assistant: Thomas asked"}, store,
                 approvals=approvals)

    assert out["ok"] is False and out["reason_code"] == "APPROVAL_REQUIRED"
    assert out["approve_with"] == "/approve approval_x"
    assert store.load().mode == KILLED       # still halted; the ask moved nothing
    assert approvals.appended                # but the ask is durable


# --- starting: spending a grant ----------------------------------------------

def test_an_approved_grant_re_arms_the_runtime(tmp_path, approved):
    store = ControlStore(tmp_path)
    _apply({"command": "disable", "reason": "stopped"}, store)
    assert store.load().mode == KILLED

    out = _apply({"command": "enable", "reason": "assistant: Thomas approved",
                  "approval_id": "approval_test"}, store, approvals=approved)

    assert out["ok"] is True
    assert store.load().mode == ACTIVE
    assert out["actor"] == switch_bridge.ASSISTANT_ACTOR
    assert out["domain"] == "crypto"


def test_spending_marks_the_grant_consumed(tmp_path, approved):
    """One-time use: the spend appends a CONSUMED record, so a replayed frame finds it spent."""
    store = ControlStore(tmp_path)
    _apply({"command": "disable", "reason": "stopped"}, store)
    _apply({"command": "enable", "reason": "go", "approval_id": "approval_test"},
           store, approvals=approved)
    assert approved.records["approval_test"]["status"] == approval_mod.STATUS_CONSUMED

    _apply({"command": "disable", "reason": "stopped again"}, store)
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "enable", "reason": "replay", "approval_id": "approval_test"},
               store, approvals=approved)
    assert exc.value.reason_code == "ALREADY_CONSUMED"
    assert store.load().mode == KILLED


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda g: g.update(status=approval_mod.STATUS_PENDING), "NOT_APPROVED"),
        (lambda g: g.update(status=approval_mod.STATUS_REJECTED), "NOT_APPROVED"),
        (lambda g: g.update(status=approval_mod.STATUS_CONSUMED), "ALREADY_CONSUMED"),
        (lambda g: g["approved_action_snapshot"].update(permission_scope="INTERNAL_READ"),
         "SCOPE_NOT_SPENDABLE"),
        (lambda g: g["approved_action_snapshot"].update(target_ref="memory_candidate:abc"),
         "TARGET_NOT_SWITCH"),
        (lambda g: g["approved_action_snapshot"].update(target_ref="trading_switch:forex"),
         "DOMAIN_NOT_PERMITTED"),
        (lambda g: g.update(action_fingerprint="fp_drifted"), "FINGERPRINT_MISMATCH"),
    ],
)
def test_a_grant_that_is_not_exactly_this_action_is_refused(tmp_path, approved, mutate, expected):
    """Every way of presenting a grant Thomas did not give for exactly this action. Each must
    leave the runtime halted — a refusal that still moved the state would be worse than no door."""
    store = ControlStore(tmp_path)
    _apply({"command": "disable", "reason": "stopped"}, store)
    mutate(approved.records["approval_test"])

    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "enable", "reason": "go", "approval_id": "approval_test"},
               store, approvals=approved)
    assert exc.value.reason_code == expected
    assert store.load().mode == KILLED


def test_an_expired_grant_is_refused(tmp_path, approved):
    store = ControlStore(tmp_path)
    _apply({"command": "disable", "reason": "stopped"}, store)
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "enable", "reason": "go", "approval_id": "approval_test"},
               store, approvals=approved, now=MUCH_LATER)
    assert exc.value.reason_code == "APPROVAL_EXPIRED"
    assert store.load().mode == KILLED


def test_an_unknown_grant_is_refused(tmp_path, approved):
    store = ControlStore(tmp_path)
    _apply({"command": "disable", "reason": "stopped"}, store)
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "enable", "reason": "go", "approval_id": "approval_nope"},
               store, approvals=approved)
    assert exc.value.reason_code == "UNKNOWN_APPROVAL"
    assert store.load().mode == KILLED


def test_a_grant_without_its_bound_decision_is_refused(tmp_path, approved):
    """The decision is what the fingerprint is re-validated against; without it there is
    nothing to re-validate and the grant is not spendable."""
    store = ControlStore(tmp_path)
    _apply({"command": "disable", "reason": "stopped"}, store)
    approved.decisions.clear()
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "enable", "reason": "go", "approval_id": "approval_test"},
               store, approvals=approved)
    assert exc.value.reason_code == "PERMISSION_DECISION_MISSING"
    assert store.load().mode == KILLED


def test_the_domain_comes_from_the_snapshot_not_the_request(tmp_path, approved):
    """The request's `domain` cannot redirect a grant. Thomas approved `crypto`; a frame
    naming something else is answered from what he approved, never from what it claims."""
    store = ControlStore(tmp_path)
    _apply({"command": "disable", "reason": "stopped"}, store)
    out = _apply({"command": "enable", "reason": "go", "domain": "crypto",
                  "approval_id": "approval_test"}, store, approvals=approved)
    assert out["domain"] == "crypto"

    # And a snapshot for another domain is refused even when the frame says `crypto`.
    _apply({"command": "disable", "reason": "stopped"}, store)
    approved.records["approval_test"] = _grant(domain="forex")
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "enable", "reason": "go", "domain": "crypto",
                "approval_id": "approval_test"}, store, approvals=approved)
    assert exc.value.reason_code == "DOMAIN_NOT_PERMITTED"
    assert store.load().mode == KILLED


def test_enable_still_needs_a_reason(tmp_path, approved):
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "enable", "approval_id": "approval_test"},
               ControlStore(tmp_path), approvals=approved)
    assert exc.value.reason_code == "REASON_REQUIRED"


def test_the_spend_records_the_approval_on_the_control_event(tmp_path, approved):
    """An operator reading the ledger must be able to get from the resume back to the grant
    that authorized it."""
    store, ledger = ControlStore(tmp_path), FakeLedger()
    _apply({"command": "disable", "reason": "stopped"}, store, ledger=ledger)
    _apply({"command": "enable", "reason": "Thomas approved", "approval_id": "approval_test"},
           store, approvals=approved, ledger=ledger)
    assert any("approval_test" in str(entry.get("reason", "")) for entry in ledger.control)
