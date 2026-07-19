"""R4 Operator emergency console tests — control state, transitions, and loop enforcement.

None of these need a local Core: control commands and the PAUSED/KILLED refusal short-circuit
before any task runs, so they exercise the safety control in isolation.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import control
from runtime.mvp_runtime.control import ACTIVE, KILLED, PAUSED, ControlState, ControlStore
from runtime.mvp_runtime.errors import ControlBlocked
from runtime.mvp_runtime.operator import InboundMessage, OperatorIdentity, handle_operator_message
from runtime.mvp_runtime.store import LedgerStore

NOW = "2026-07-16T09:00:00Z"
REG = OperatorIdentity(operator_id="tg-12345", chat_id="chat-777")


class FakeLedger:
    def __init__(self):
        self.control: list[dict] = []

    def append_control(self, entry):
        self.control.append(entry)


def _store(tmp_path):
    return ControlStore(tmp_path)


def _task_msg(text="이 사업 아이디어를 분석해줘: 구독형 반려동물 사료"):
    return InboundMessage(text=text, sender_id="tg-12345", chat_id="chat-777", chat_type="private",
                          is_forwarded=False, channel="telegram_private")


# --- state + store ----------------------------------------------------------

def test_missing_file_is_active(tmp_path):
    state = _store(tmp_path).load()
    assert state.mode == ACTIVE
    assert state.execution_allowed is True


def test_pause_then_load_blocks_execution(tmp_path):
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_PAUSE, actor="tg-12345", now=NOW)
    state = store.load()
    assert state.mode == PAUSED
    assert state.execution_allowed is False
    assert state.updated_by == "tg-12345"


def test_kill_then_resume_round_trip(tmp_path):
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW, reason="halt")
    assert store.load().mode == KILLED
    control.apply_command(store, control.CMD_RESUME, actor="op", now=NOW)
    assert store.load().mode == ACTIVE
    assert store.load().execution_allowed is True


def test_corrupt_file_fails_closed_to_killed(tmp_path):
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json", encoding="utf-8")
    state = store.load()
    assert state.mode == KILLED
    assert "fail-closed" in state.reason
    assert state.execution_allowed is False


def test_unknown_mode_fails_closed_to_killed(tmp_path):
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"mode": "BOGUS"}), encoding="utf-8")
    assert store.load().mode == KILLED


def test_save_write_failure_fails_closed(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")  # a file where a directory parent is expected
    store = ControlStore(blocker / "nested")
    with pytest.raises(ControlBlocked) as exc:
        store.save(ControlState(mode=PAUSED))
    assert exc.value.reason_code == "CONTROL_WRITE_FAILED"


# --- commands ---------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("/status", (control.CMD_STATUS, None)),
    ("status", (control.CMD_STATUS, None)),
    ("/PAUSE", (control.CMD_PAUSE, None)),
    ("/stop task-9", (control.CMD_STOP, "task-9")),
    ("stop_task task-9", (control.CMD_STOP, "task-9")),
    ("/resume  ", (control.CMD_RESUME, None)),
])
def test_parse_command_recognizes(text, expected):
    assert control.parse_command(text) == expected


@pytest.mark.parametrize("text", ["이 사업 아이디어를 분석해줘", "/bogus", "", "   ", None])
def test_parse_command_ignores_non_commands(text):
    assert control.parse_command(text) is None


def test_status_is_read_only_no_event(tmp_path):
    store = _store(tmp_path)
    ledger = FakeLedger()
    out = control.apply_command(store, control.CMD_STATUS, actor="op", now=NOW, ledger=ledger)
    assert out["changed"] is False
    assert "mode: ACTIVE" in out["reply"]
    assert ledger.control == []


def test_stop_requires_task_id(tmp_path):
    with pytest.raises(ControlBlocked) as exc:
        control.apply_command(_store(tmp_path), control.CMD_STOP, actor="op", now=NOW, arg=None)
    assert exc.value.reason_code == "MISSING_TASK_ID"


def test_stop_records_request_and_event(tmp_path):
    store = _store(tmp_path)
    ledger = FakeLedger()
    out = control.apply_command(store, control.CMD_STOP, actor="op", now=NOW, arg="task-9", ledger=ledger)
    assert out["changed"] is True
    assert "task-9" in store.load().stop_requested_task_ids
    assert ledger.control[0]["action"] == "stop"
    assert ledger.control[0]["task_id"] == "task-9"


def test_unknown_command_raises(tmp_path):
    with pytest.raises(ControlBlocked) as exc:
        control.apply_command(_store(tmp_path), "detonate", actor="op", now=NOW)
    assert exc.value.reason_code == "UNKNOWN_COMMAND"


def test_control_event_is_tamper_evident(tmp_path):
    store = _store(tmp_path)
    ledger = FakeLedger()
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW, ledger=ledger)
    event = ledger.control[0]
    assert event["action"] == "kill"
    assert event["resulting_mode"] == KILLED
    assert event["integrity"]["event_sha256"].startswith("sha256:")


def test_control_event_persisted_to_real_ledger(tmp_path):
    store = _store(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    control.apply_command(store, control.CMD_PAUSE, actor="op", now=NOW, ledger=ledger)
    rows = [json.loads(ln) for ln in (ledger.root / "control_events.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows[0]["action"] == "pause"
    assert rows[0]["resulting_mode"] == PAUSED


# --- loop enforcement (handle_operator_message) -----------------------------

def test_killed_runtime_refuses_task_request(tmp_path):
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW)
    reply = handle_operator_message(_task_msg(), registration=REG, control_store=store, now=NOW)
    assert reply.accepted is False
    assert reply.status == "REFUSED"
    assert reply.reason_code == "RUNTIME_KILLED"


def test_paused_runtime_refuses_task_request(tmp_path):
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_PAUSE, actor="op", now=NOW)
    reply = handle_operator_message(_task_msg(), registration=REG, control_store=store, now=NOW)
    assert reply.status == "REFUSED"
    assert reply.reason_code == "RUNTIME_PAUSED"


def test_status_command_answered_even_when_killed(tmp_path):
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW)
    reply = handle_operator_message(_task_msg(text="/status"), registration=REG, control_store=store, now=NOW)
    assert reply.status == "CONTROL"
    assert "mode: KILLED" in reply.text


def test_resume_via_channel_clears_kill(tmp_path):
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW)
    reply = handle_operator_message(_task_msg(text="/resume"), registration=REG, control_store=store, now=NOW)
    assert reply.status == "CONTROL"
    assert store.load().mode == ACTIVE


def test_pause_command_then_task_refused(tmp_path):
    store = _store(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    reply = handle_operator_message(_task_msg(text="/pause"), registration=REG, control_store=store, store=ledger, now=NOW)
    assert reply.status == "CONTROL"
    refused = handle_operator_message(_task_msg(), registration=REG, control_store=store, now=NOW)
    assert refused.reason_code == "RUNTIME_PAUSED"
    # The /pause was durably recorded.
    rows = (ledger.root / "control_events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(rows[0])["action"] == "pause"


def test_unverified_sender_never_reaches_control(tmp_path):
    store = _store(tmp_path)
    control.apply_command(store, control.CMD_KILL, actor="op", now=NOW)
    impostor = InboundMessage(text="/resume", sender_id="tg-99999", chat_id="chat-777", chat_type="private")
    reply = handle_operator_message(impostor, registration=REG, control_store=store, now=NOW)
    assert reply.status == "REFUSED"
    assert reply.reason_code == "UNREGISTERED_USER"
    # The kill still stands — an impostor cannot resume.
    assert store.load().mode == KILLED


# --- policy drift gate --------------------------------------------------------

def test_console_verbs_stay_within_the_policy_grant():
    """Mechanical drift gate: every emergency verb the console exposes must be granted by
    the Governance Policy (control_channel.local_operator_console.emergency_controls_allowed).
    /resume silently exceeding the policy for several waves is exactly the drift class this
    catches — a new console verb now requires the matching policy edit in the same change."""
    import yaml

    from runtime.mvp_runtime.paths import repo_root

    policy = yaml.safe_load(
        (repo_root() / "governance" / "GOVERNANCE_POLICY.yaml").read_text(encoding="utf-8")
    )
    allowed = set(policy["control_channel"]["local_operator_console"]["emergency_controls_allowed"])
    policy_name = {control.CMD_STOP: "stop_task"}  # the policy names the stop verb stop_task
    for verb in control.COMMANDS:
        assert policy_name.get(verb, verb) in allowed, (
            f"console verb {verb!r} is not granted by the Governance Policy - "
            "either drop the verb or extend emergency_controls_allowed explicitly"
        )
