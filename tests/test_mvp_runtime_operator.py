"""R4.1 Operator control-channel tests.

The identity gate runs everywhere (no Core needed — it blocks before any task). The
accepted-message path runs the full pipeline, so it needs a local Core activation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.errors import OperatorBlocked, SafetyGateBlocked
from runtime.mvp_runtime.operator import (
    OPERATOR_CHANNEL_ENV,
    InboundMessage,
    MockOperatorChannel,
    OperatorIdentity,
    TelegramChannel,
    handle_operator_message,
    load_operator_registration,
    run_operator_once,
    select_operator_channel,
    verify_control_channel,
)
from runtime.mvp_runtime.safety_gate import NETWORK_ACCESS, Authorization, build_activation_record
from runtime.mvp_runtime.worker import MockProvider

LOCAL_POINTER = Path(__file__).resolve().parents[1] / DEFAULT_POINTER_REL
NOW = "2026-07-16T09:00:00Z"

requires_local_core = pytest.mark.skipif(not LOCAL_POINTER.is_file(), reason="no local Core activation")

REG = OperatorIdentity(operator_id="tg-12345", chat_id="chat-777")

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
_TG_AUTH = Authorization(
    flags=(NETWORK_ACCESS,), provider_id="telegram", activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z", evidence_ref=".runtime_governance_state/evidence.md",
)


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


# --- R4.2: channel selection behind the Safety-Flag Gate ---------------------

def test_select_channel_defaults_to_mock(monkeypatch):
    monkeypatch.delenv(OPERATOR_CHANNEL_ENV, raising=False)
    assert isinstance(select_operator_channel(), MockOperatorChannel)


def test_select_telegram_without_activation_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv(OPERATOR_CHANNEL_ENV, "telegram")
    with pytest.raises(SafetyGateBlocked) as exc:
        select_operator_channel(now="2026-07-16T00:00:00Z", root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def test_select_telegram_with_activation_returns_channel(monkeypatch, tmp_path):
    state = tmp_path / ".runtime_governance_state"
    state.mkdir()
    evidence_rel = ".runtime_governance_state/telegram_gate_approval.md"
    (tmp_path / evidence_rel).write_text("operator decision", encoding="utf-8")
    record = build_activation_record(
        flags=[NETWORK_ACCESS], provider_id="telegram", activated_at="2026-07-01T00:00:00Z",
        expires_at="2026-12-31T23:59:59Z", evidence_ref=evidence_rel, authority_level="P2",
    )
    (state / "safety_flag_activation.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setenv(OPERATOR_CHANNEL_ENV, "telegram")
    assert isinstance(select_operator_channel(now="2026-07-16T00:00:00Z", root=tmp_path), TelegramChannel)


# --- R4.2: TelegramChannel egress self-guard + HTTP path ---------------------

class _FakeResp:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_urlopen(monkeypatch, payload_or_exc):
    def fake_urlopen(request, timeout):
        if isinstance(payload_or_exc, Exception):
            raise payload_or_exc
        return _FakeResp(payload_or_exc)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def test_telegram_poll_without_authorization_fails_closed(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "test-token")
    with pytest.raises(SafetyGateBlocked) as exc:
        TelegramChannel().poll()
    assert exc.value.reason_code == "NOT_AUTHORIZED"


def test_telegram_no_token_fails_closed(monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    with pytest.raises(OperatorBlocked) as exc:
        TelegramChannel(authorization=_TG_AUTH).poll()
    assert exc.value.reason_code == "NO_BOT_TOKEN"


def test_telegram_poll_maps_updates(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "test-token")
    _patch_urlopen(monkeypatch, {"ok": True, "result": [
        {"update_id": 10, "message": {"from": {"id": 12345}, "chat": {"id": 777, "type": "private"}, "text": "분석해줘"}},
        {"update_id": 11, "message": {"from": {"id": 9}, "chat": {"id": 8, "type": "group"}, "text": "hi"}},
    ]})
    msgs = TelegramChannel(authorization=_TG_AUTH).poll()
    assert [m.sender_id for m in msgs] == ["12345", "9"]
    assert msgs[0].chat_type == "private" and msgs[1].chat_type == "group"
    assert msgs[0].channel == "telegram_private"


def test_telegram_long_poll_http_timeout_outlasts_hold(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "test-token")
    seen = {}

    def fake_urlopen(request, timeout):
        seen["timeout"] = timeout
        return _FakeResp({"ok": True, "result": []})
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    TelegramChannel(authorization=_TG_AUTH).poll(long_poll_seconds=25)
    assert seen["timeout"] == 35   # 25s server hold + 10s buffer, so the client never aborts early
    TelegramChannel(authorization=_TG_AUTH).poll(long_poll_seconds=0)
    assert seen["timeout"] == 30   # short poll uses the default timeout


def test_telegram_transport_error_fails_closed_without_leaking(monkeypatch):
    import urllib.error
    monkeypatch.setenv(TOKEN_ENV, "secret-token-value")
    _patch_urlopen(monkeypatch, urllib.error.URLError("refused"))
    with pytest.raises(OperatorBlocked) as exc:
        TelegramChannel(authorization=_TG_AUTH).poll()
    assert exc.value.reason_code == "CHANNEL_TRANSPORT"
    assert "secret-token-value" not in str(exc.value)


# --- R4.2: poll -> handle -> send loop --------------------------------------

def test_run_once_drops_unverified_without_replying():
    ch = MockOperatorChannel(inbound=[
        _msg(sender_id="tg-99999"),          # impostor
        _msg(chat_type="group"),             # group
    ])
    summary = run_operator_once(ch, REG, provider=MockProvider(), now=NOW)
    assert summary["handled"] == 0 and summary["dropped"] == 2
    assert ch.sent == []                     # no reply to unverified senders


def test_run_once_forwards_long_poll_to_channel():
    ch = MockOperatorChannel()
    run_operator_once(ch, REG, long_poll_seconds=25, provider=MockProvider(), now=NOW)
    assert ch.last_long_poll_seconds == 25


@requires_local_core
def test_run_once_handles_registered_and_replies():
    ch = MockOperatorChannel(inbound=[_msg(), _msg(sender_id="tg-99999")])
    summary = run_operator_once(ch, REG, provider=MockProvider(), now=NOW)
    assert summary["handled"] == 1 and summary["dropped"] == 1
    assert len(ch.sent) == 1 and ch.sent[0][0] == "chat-777"
    assert "Key findings" in ch.sent[0][1]
