"""The trading arm — a runtime that is running with live entries held down.

The failure this closes: the assistant could stop the runtime for free and could only start it
again by asking Thomas to sign the RED live-trading re-arm. So getting the operator loop, the
intake CLI, memory or its own dispatch door back cost a signature on the money path, and the
approval got spent on doors it was not minted for.

`ControlState` gains one field and the switch door gains one key. What is pinned here is that
the two shapes of resume stay different in the direction that matters:

- a `scope=runtime` grant resumes the runtime and does NOT arm live entries;
- neither grant can be presented as the other, because the arm is read from the approved
  `target_ref` prefix and never from the frame;
- the local operator console is unchanged — `resume` still arms, which is D4(a);
- a state file written before the field existed reads as ARMED (D1), while a malformed value
  reads as DISARMED (D2). Missing is not a stop order; unreadable is.

The entry half of the effect is pinned next door: `evaluate_live_order_guard` refuses on
`runtime_active=False` in `test_mvp_runtime_crypto_live_guard`, and `live_route` feeds that
argument from `trading_allowed`. The exit half needs no test here because it is structural —
`_run_gated_live_leg` settles and protects before it reads control state at all.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import approval as approval_mod
from runtime.mvp_runtime import control, switch_bridge
from runtime.mvp_runtime.control import ACTIVE, KILLED, PAUSED, ControlState, ControlStore
from runtime.mvp_runtime.errors import ControlBlocked
from runtime.mvp_runtime.permission import (
    NONFINANCIAL_RESUME_TARGET_PREFIX,
    TRADING_SWITCH_PERMISSION_SCOPE,
    TRADING_SWITCH_TARGET_PREFIX,
)

NOW = "2026-08-05T09:00:00Z"


class FakeLedger:
    def __init__(self):
        self.control: list[dict] = []

    def append_control(self, entry):
        self.control.append(entry)

    def last_audit_hash(self):
        return "sha256:" + "ab" * 32


class FakeApprovalStore:
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


def _write_state(store, **over):
    """A control-state file written by hand, so a test can say what a PRE-SPLIT one looked like."""
    record = {
        "record_type": control.RECORD_TYPE, "mode": ACTIVE, "updated_by": "t",
        "updated_at": NOW, "reason": "r", "stop_requested_task_ids": [],
    }
    record.update(over)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps(record), encoding="utf-8")


def _grant(approval_id="approval_test", *, prefix, domain="crypto", store=None):
    """A grant record shaped like the fields the door reads, pointed at ``prefix``.

    ``store`` points ``stop_ref`` at the stop actually in effect — what minting the ask would
    have done. Omitting it leaves a placeholder that no real state hashes to, so a test that
    means to spend and forgets fails loudly (``STOP_CHANGED``) rather than passing by accident.
    """
    return {
        "approval_id": approval_id,
        "status": approval_mod.STATUS_APPROVED,
        "permission_decision_id": "permdec_test",
        "validity": {"expires_at": "2026-08-05T09:30:00Z"},
        "action_fingerprint": "fp_bound",
        "approved_action_snapshot": {
            "permission_scope": TRADING_SWITCH_PERMISSION_SCOPE,
            "target_ref": f"{prefix}{domain}",
            "normalized_parameters": {
                "stop_ref": (switch_bridge.stop_ref(store.load()) if store is not None
                             else "stop_placeholder"),
            },
        },
    }


@pytest.fixture
def spendable(tmp_path, monkeypatch):
    """The fingerprint check and the consumed-record builder stubbed; both are covered on their
    own in `test_mvp_runtime_switch_bridge`."""
    monkeypatch.setattr(switch_bridge, "compute_action_fingerprint", lambda s: "fp_bound")
    monkeypatch.setattr(
        switch_bridge.approval_mod, "build_consumed_record",
        lambda rec, dec, **kw: {**rec, "status": approval_mod.STATUS_CONSUMED},
    )
    store = FakeApprovalStore(tmp_path)
    store.decisions["permdec_test"] = {"permission_decision_id": "permdec_test"}
    return store


# --- D1 / D2: what an old or damaged state file means -------------------------

def test_a_state_file_written_before_the_split_reads_as_armed(tmp_path):
    """D1(a). The field's absence means the machine predates the question, not that someone
    disarmed it — reading it as a stop order would halt entries on every upgrade."""
    store = ControlStore(tmp_path)
    _write_state(store)  # no trading_armed key at all
    assert "trading_armed" not in json.loads(store.path.read_text(encoding="utf-8"))
    assert store.load().trading_armed is True


def test_a_non_boolean_arm_fails_closed(tmp_path):
    """D2. Present-and-unreadable is genuine uncertainty about a safety state, and uncertainty
    is not permission — the same split `load` already makes for an unknown `mode`."""
    store = ControlStore(tmp_path)
    _write_state(store, trading_armed="yes")
    assert store.load().trading_armed is False


def test_an_unreadable_state_file_disarms_as_well_as_kills(tmp_path):
    store = ControlStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ not json", encoding="utf-8")
    state = store.load()
    assert (state.mode, state.trading_armed) == (KILLED, False)


def test_a_fresh_deployment_is_armed(tmp_path):
    """No file at all is a fresh machine, not a stop — the pre-existing rule, extended."""
    assert ControlStore(tmp_path).load().trading_armed is True


def test_the_arm_round_trips_through_the_file(tmp_path):
    store = ControlStore(tmp_path)
    store.save(ControlState(mode=ACTIVE, trading_armed=False))
    assert store.load().trading_armed is False


# --- the conjunction ----------------------------------------------------------

def test_an_active_runtime_can_have_entries_held_down(tmp_path):
    """The state the whole change exists to be able to express, and the only one where the two
    flags disagree."""
    state = ControlState(mode=ACTIVE, trading_armed=False)
    assert state.execution_allowed is True
    assert state.trading_allowed is False


@pytest.mark.parametrize("mode", [PAUSED, KILLED])
def test_a_stopped_runtime_allows_no_entry_however_armed(tmp_path, mode):
    assert ControlState(mode=mode, trading_armed=True).trading_allowed is False


# --- transitions --------------------------------------------------------------

@pytest.mark.parametrize("verb", [control.CMD_KILL, control.CMD_PAUSE])
def test_stopping_disarms_and_needs_no_approval(tmp_path, verb):
    store = ControlStore(tmp_path)
    control.apply_command(store, verb, actor="a", now=NOW, reason="x")
    assert store.load().trading_armed is False


def test_the_operator_console_resume_still_arms(tmp_path):
    """D4(a), and the reason this change is additive: `console_cli resume` restores trading
    today, and taking that away would be a change to the operator path nobody asked for."""
    store = ControlStore(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="a", now=NOW, reason="x")
    control.apply_command(store, control.CMD_RESUME, actor="a", now=NOW, reason="x")
    state = store.load()
    assert (state.mode, state.trading_armed) == (ACTIVE, True)


def test_a_non_arming_resume_starts_the_runtime_and_not_the_money_path(tmp_path):
    store = ControlStore(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="a", now=NOW, reason="x")
    control.apply_command(
        store, control.CMD_RESUME, actor="a", now=NOW, reason="x", resume_arms=False,
    )
    state = store.load()
    assert state.mode == ACTIVE
    assert state.execution_allowed is True
    assert state.trading_allowed is False


def test_a_non_arming_resume_preserves_and_cannot_disarm(tmp_path):
    """`resume_arms=False` means "leave it", never "clear it". Otherwise the cheap grant would
    have an effect the expensive one does not, on a runtime that was already trading."""
    store = ControlStore(tmp_path)
    control.apply_command(
        store, control.CMD_RESUME, actor="a", now=NOW, reason="x", resume_arms=False,
    )
    assert store.load().trading_armed is True


def test_a_stop_request_does_not_silently_re_arm(tmp_path):
    """`/stop <task_id>` keeps `mode` deliberately; letting the arm fall back to the dataclass
    default would make an auditable no-op re-arm a disarmed runtime."""
    store = ControlStore(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="a", now=NOW, reason="x")
    control.apply_command(store, control.CMD_STOP, actor="a", now=NOW, arg="task_1")
    assert store.load().trading_armed is False


def test_status_says_which_it_is(tmp_path):
    store = ControlStore(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="a", now=NOW, reason="x")
    assert "live entries: DISARMED" in control.status_lines(store.load())


# --- the door: which ask gets minted ------------------------------------------

@pytest.fixture
def which_ask(monkeypatch):
    """Records WHICH builder `_open_ask` reached for, with the Core binding stubbed out.

    Returns a one-element list so a test reads `which_ask[0]`. The two builders are covered on
    their own in `test_mvp_runtime_permission`; what is under test here is the routing.
    """
    built: list[str] = []
    monkeypatch.setattr(switch_bridge, "build_task", lambda *a, **k: {"task": "t"})
    monkeypatch.setattr(switch_bridge, "bind_task_to_core", lambda t, **k: (None, t))

    def _record(name):
        def _build(*a, **k):
            built.append(name)
            return {"permission_decision_id": "d"}
        return _build

    monkeypatch.setattr(
        switch_bridge, "build_trading_switch_permission_decision", _record("trading"))
    monkeypatch.setattr(
        switch_bridge, "build_nonfinancial_resume_permission_decision", _record("runtime"))
    monkeypatch.setattr(
        switch_bridge.approval_mod, "build_approval_request",
        lambda d, **k: {"approval_id": "ap_1", "validity": {"expires_at": "2026-08-05T09:30:00Z"}},
    )
    return built


def test_an_unqualified_enable_still_asks_for_trading(tmp_path, which_ask):
    """The default is the EXPENSIVE ask. A caller that forgets the key gets the one that asks
    for more, which Thomas can refuse; the reverse default would let a typo quietly request
    less authority than the caller believed they were requesting."""
    out = _apply({"command": "enable", "reason": "assistant: resume"},
                 ControlStore(tmp_path), approvals=FakeApprovalStore(tmp_path))
    assert which_ask == ["trading"]
    assert out["scope"] == switch_bridge.SCOPE_TRADING


def test_scope_runtime_mints_the_non_financial_ask(tmp_path, which_ask):
    out = _apply({"command": "enable", "reason": "assistant: resume", "scope": "runtime"},
                 ControlStore(tmp_path), approvals=FakeApprovalStore(tmp_path))
    assert which_ask == ["runtime"]
    assert out["scope"] == switch_bridge.SCOPE_RUNTIME
    assert "does NOT re-arm live entries" in out["reason"]


def test_an_unknown_scope_is_refused_by_name(tmp_path):
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "enable", "reason": "r", "scope": "paper"}, ControlStore(tmp_path))
    assert exc.value.reason_code == "SCOPE_NOT_PERMITTED"


def test_scope_cannot_accompany_an_approval_id(tmp_path):
    """The dangerous reading is the plausible one: `scope=runtime` beside a trading grant looks
    like a downgrade and would in fact re-arm. Refused, not ignored."""
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "enable", "reason": "r", "scope": "runtime",
                "approval_id": "ap_1"}, ControlStore(tmp_path))
    assert exc.value.reason_code == "ARGUMENT_NOT_ACCEPTED"


# --- the door: what a grant does when spent -----------------------------------

def test_a_runtime_grant_resumes_without_arming(tmp_path, spendable):
    store = ControlStore(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="a", now=NOW, reason="x")
    spendable.records["approval_test"] = _grant(
        prefix=NONFINANCIAL_RESUME_TARGET_PREFIX, store=store,
    )
    out = _apply({"command": "enable", "reason": "r", "approval_id": "approval_test"},
                 store, approvals=spendable)
    assert out["ok"] is True
    assert out["scope"] == switch_bridge.SCOPE_RUNTIME
    assert out["trading_armed"] is False
    state = store.load()
    assert state.mode == ACTIVE
    assert state.trading_allowed is False


def test_a_trading_grant_arms(tmp_path, spendable):
    store = ControlStore(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="a", now=NOW, reason="x")
    spendable.records["approval_test"] = _grant(
        prefix=TRADING_SWITCH_TARGET_PREFIX, store=store,
    )
    out = _apply({"command": "enable", "reason": "r", "approval_id": "approval_test"},
                 store, approvals=spendable)
    assert out["scope"] == switch_bridge.SCOPE_TRADING
    assert out["trading_armed"] is True
    assert store.load().trading_allowed is True


def test_the_arm_comes_from_the_grant_not_the_frame(tmp_path, spendable):
    """The property that makes the two grants non-interchangeable. The frame cannot carry
    `scope` on this path at all, and the effect is read from the approved `target_ref`."""
    store = ControlStore(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="a", now=NOW, reason="x")
    spendable.records["approval_test"] = _grant(
        prefix=NONFINANCIAL_RESUME_TARGET_PREFIX, store=store,
    )
    # No way to ask for the trading effect: `scope` beside `approval_id` is refused outright,
    # and without it the snapshot is the only thing that decides.
    with pytest.raises(ControlBlocked):
        _apply({"command": "enable", "reason": "r", "approval_id": "approval_test",
                "scope": "trading"}, store, approvals=spendable)
    assert store.load().mode == KILLED  # nothing applied

    out = _apply({"command": "enable", "reason": "r", "approval_id": "approval_test"},
                 store, approvals=spendable)
    assert out["trading_armed"] is False


def test_a_target_that_is_neither_is_refused(tmp_path, spendable):
    store = ControlStore(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="a", now=NOW, reason="x")
    spendable.records["approval_test"] = _grant(prefix="memory_promotion:", store=store)
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "enable", "reason": "r", "approval_id": "approval_test"},
               store, approvals=spendable)
    assert exc.value.reason_code == "TARGET_NOT_SWITCH"
    assert store.load().mode == KILLED


def test_a_runtime_grant_against_an_armed_runtime_reports_what_is_true(tmp_path, spendable):
    """`resume_arms=False` preserves, so spending this against an already-armed runtime leaves
    it armed. The reply reads the state back rather than assuming — the one place this change
    could have reported a disarm that did not happen."""
    store = ControlStore(tmp_path)
    control.apply_command(store, control.CMD_STOP, actor="a", now=NOW, arg="task_1")
    spendable.records["approval_test"] = _grant(
        prefix=NONFINANCIAL_RESUME_TARGET_PREFIX, store=store,
    )
    out = _apply({"command": "enable", "reason": "r", "approval_id": "approval_test"},
                 store, approvals=spendable)
    assert out["scope"] == switch_bridge.SCOPE_RUNTIME
    assert out["trading_armed"] is True
