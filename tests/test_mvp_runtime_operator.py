"""R4.1 Operator control-channel tests.

The identity gate runs everywhere (no Core needed — it blocks before any task). The
accepted-message path runs the full pipeline, so it needs a local Core activation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.errors import OperatorBlocked
from runtime.mvp_runtime.operator import (
    InboundMessage,
    OperatorIdentity,
    handle_operator_message,
    load_operator_registration,
    verify_control_channel,
)
from runtime.mvp_runtime.worker import MockProvider

LOCAL_POINTER = Path(__file__).resolve().parents[1] / DEFAULT_POINTER_REL
NOW = "2026-07-16T09:00:00Z"

requires_local_core = pytest.mark.skipif(not LOCAL_POINTER.is_file(), reason="no local Core activation")

REG = OperatorIdentity(operator_id="tg-12345", chat_id="chat-777")


def _msg(**overrides):
    params = dict(
        text="이 사업 아이디어를 분석해줘: 구독형 반려동물 사료",
        sender_id="tg-12345", chat_id="chat-777", chat_type="private",
        is_forwarded=False, channel="telegram_private",
    )
    params.update(overrides)
    return InboundMessage(**params)


# --- identity gate (runs everywhere) ----------------------------------------

def test_registered_private_message_passes():
    assert verify_control_channel(_msg(), REG) is None


@pytest.mark.parametrize("overrides, code", [
    ({"chat_type": "group"}, "NOT_PRIVATE_CHANNEL"),
    ({"chat_type": "channel"}, "NOT_PRIVATE_CHANNEL"),
    ({"channel": "telegram_group"}, "NOT_PRIVATE_CHANNEL"),
    ({"is_forwarded": True}, "FORWARDED_MESSAGE"),
    ({"sender_id": "tg-99999"}, "UNREGISTERED_USER"),
    ({"chat_id": "chat-000"}, "CHAT_NOT_REGISTERED"),
])
def test_invalid_sources_fail_closed(overrides, code):
    with pytest.raises(OperatorBlocked) as exc:
        verify_control_channel(_msg(**overrides), REG)
    assert exc.value.reason_code == code


def test_handle_refuses_unregistered_without_running(monkeypatch):
    # An unverified sender gets a generic refusal and NO task runs.
    import runtime.mvp_runtime.operator as operator_mod
    monkeypatch.setattr(operator_mod, "run_task", lambda *a, **k: pytest.fail("run_task must not run"))
    reply = handle_operator_message(_msg(sender_id="tg-99999"), registration=REG, now=NOW)
    assert reply.accepted is False and reply.status == "REFUSED"
    assert reply.reason_code == "UNREGISTERED_USER"
    assert "registered operator" in reply.text


def test_handle_refuses_empty_request():
    reply = handle_operator_message(_msg(text="   "), registration=REG, now=NOW)
    assert reply.accepted is False and reply.reason_code == "EMPTY_REQUEST"


# --- registration loader ----------------------------------------------------

def test_load_registration_missing_fails_closed(tmp_path):
    with pytest.raises(OperatorBlocked) as exc:
        load_operator_registration(repo_root=tmp_path)
    assert exc.value.reason_code == "REGISTRATION_MISSING"


def test_load_registration_reads_identity(tmp_path):
    state = tmp_path / ".runtime_governance_state"
    state.mkdir()
    (state / "operator_registration.json").write_text(
        json.dumps({"operator_id": "tg-1", "chat_id": "chat-1", "approver": "Thomas"}), encoding="utf-8"
    )
    reg = load_operator_registration(repo_root=tmp_path)
    assert reg.operator_id == "tg-1" and reg.chat_id == "chat-1" and reg.approver == "Thomas"


def test_load_registration_malformed_fails_closed(tmp_path):
    state = tmp_path / ".runtime_governance_state"
    state.mkdir()
    (state / "operator_registration.json").write_text(json.dumps({"operator_id": "tg-1"}), encoding="utf-8")
    with pytest.raises(OperatorBlocked) as exc:
        load_operator_registration(repo_root=tmp_path)
    assert exc.value.reason_code == "REGISTRATION_MALFORMED"


# --- accepted path (needs a Core) -------------------------------------------

@requires_local_core
def test_registered_message_runs_and_replies():
    reply = handle_operator_message(_msg(), registration=REG, provider=MockProvider(), now=NOW)
    assert reply.accepted is True and reply.status == "COMPLETED"
    assert "Key findings" in reply.text
    assert reply.trace_id and reply.trace_id.startswith("trace_")
