"""The switch shim against the switch door's real frames (review of #915).

`test_shim_rendering.py` feeds the renderer hand-written frames, so a frame that changed shape at the
runtime — `changed` or `mode` moving into `data` only — would leave every real halt rendering as "Nothing
changed" with those tests green. Here the frames come from `switch_bridge.apply_switch` itself, enveloped
as the socket sends them (`socket_door.refusal_payload` for a refusal), over temp stores only.
"""

from __future__ import annotations

import pytest

import thomas_door_client as door
import switch_bridge_mcp as switch_shim

from runtime.mvp_runtime import control, socket_door, switch_bridge
from runtime.mvp_runtime.control import ACTIVE, HALT_HARD, HALT_SOFT, KILLED, PAUSED, ControlState, ControlStore
from runtime.mvp_runtime.errors import MvpRuntimeError

NOW = "2026-09-19T12:00:00Z"


class _Ledger:
    def append_control(self, entry):
        pass


@pytest.fixture(autouse=True)
def halt_granted(monkeypatch):
    monkeypatch.setattr(control, "granted_emergency_controls",
                        lambda root=None: frozenset({"pause", "kill", "status", "resume", "halt_trading"}))


def _render(store, mode):
    payload = {"command": "disable", "mode": mode, "reason": "r", "domain": "crypto"}
    try:
        frame = switch_bridge.apply_switch(dict(payload), control_store=store, ledger=_Ledger(), now=NOW)
    except MvpRuntimeError as exc:
        frame = socket_door.refusal_payload(exc, payload)
    answer = door.Answer(door=door.DOORS["switch"], frame=frame, failure=None, sent=True, detail="")
    return switch_shim._render(answer, payload=payload, retry_tool="start_trading", request_id=None)


def _store(tmp_path, **state):
    store = ControlStore(tmp_path)
    store.save(ControlState(updated_by="op", updated_at=NOW, reason="r", **state))
    return store


def test_a_hard_halt_on_an_armed_runtime_renders_as_applied_with_positions_managed(tmp_path):
    text = _render(_store(tmp_path, mode=ACTIVE, trading_armed=True), "hard")
    assert text.startswith("DONE: halt_trading applied to crypto. Runtime mode is now ACTIVE (changed=True")
    assert "under the HARD halt" in text and "positions keep being settled" in text


def test_a_soft_halt_over_a_hard_one_renders_as_not_changed(tmp_path):
    text = _render(_store(tmp_path, mode=ACTIVE, trading_armed=False, halt_level=HALT_HARD), "soft")
    assert text.startswith("NOT CHANGED:") and "cannot loosen" in text and "applied" not in text


@pytest.mark.parametrize("stop", [KILLED, PAUSED])
def test_a_halt_under_an_operator_stop_renders_as_recorded_with_positions_unmanaged(tmp_path, stop):
    text = _render(_store(tmp_path, mode=stop, trading_armed=False), "hard")
    assert text.startswith(f"DONE: halt_trading applied to crypto. Runtime mode is now {stop} (changed=True")
    assert "The HARD halt is recorded under the stop" in text and "NOT being settled" in text


def test_a_halt_under_a_tighter_one_under_a_stop_renders_as_not_changed_with_the_warning(tmp_path):
    text = _render(_store(tmp_path, mode=KILLED, trading_armed=False, halt_level=HALT_HARD), "soft")
    assert text.startswith("NOT CHANGED:") and "NOT being settled" in text


def test_a_halt_under_a_stop_derived_by_failing_closed_renders_as_not_changed(tmp_path):
    store = ControlStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json", encoding="utf-8")
    text = _render(store, "soft")
    assert text.startswith("NOT CHANGED:") and "Runtime mode is KILLED" in text and "NOT being settled" in text


def test_a_refused_halt_renders_as_a_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "granted_emergency_controls", lambda root=None: frozenset({"kill"}))
    text = _render(_store(tmp_path, mode=ACTIVE, trading_armed=True), "soft")
    assert text.startswith(f"REFUSED [{control.VERB_NOT_GRANTED}]") and "Nothing was changed" in text


def test_the_same_halt_twice_renders_the_second_as_not_changed(tmp_path):
    store = _store(tmp_path, mode=ACTIVE, trading_armed=False, halt_level=HALT_SOFT)
    text = _render(store, "soft")
    assert text.startswith("NOT CHANGED:") and "already halted" in text
