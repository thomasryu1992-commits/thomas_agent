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
from runtime.mvp_runtime import safety_gate
from runtime.mvp_runtime.safety_gate import NETWORK_ACCESS, Authorization, build_activation_record
from runtime.mvp_runtime.worker import MockProvider

NOW = "2026-07-16T09:00:00Z"

from tests._helpers import requires_local_core

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


def test_handle_forwards_working_memory_to_run(monkeypatch):
    import runtime.mvp_runtime.operator as operator_mod
    captured = {}

    def fake_run_task(text, **kwargs):
        captured.update(kwargs)
        return {"status": "COMPLETED", "final_response": "ok", "records": {}}
    monkeypatch.setattr(operator_mod, "run_task", fake_run_task)

    sentinel = object()
    handle_operator_message(_msg(), registration=REG, working_memory=sentinel, provider=MockProvider(), now=NOW)
    assert captured.get("working_memory") is sentinel


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
    path = safety_gate.activation_path(tmp_path, "telegram")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
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
    # Two sends for the one accepted task: the received-working ack, then the answer.
    assert [c for c, _ in ch.sent] == ["chat-777", "chat-777"]
    assert "분석 중" in ch.sent[0][1]
    assert "Key findings" in ch.sent[1][1]


@requires_local_core
def test_operator_accumulates_working_memory(tmp_path):
    from runtime.mvp_runtime.working_memory import WorkingMemoryStore
    wm = WorkingMemoryStore(tmp_path / "wm")

    run_operator_once(MockOperatorChannel(inbound=[_msg()]), REG, provider=MockProvider(), working_memory=wm, now=NOW)
    after_first = len(wm.read_all())
    assert after_first  # the operator run accumulated working memory

    run_operator_once(MockOperatorChannel(inbound=[_msg(text="구독 사업 유지율 분석")]), REG,
                      provider=MockProvider(), working_memory=wm, now="2026-07-16T10:00:00Z")
    assert len(wm.read_all()) > after_first  # a later operator run adds more

# --- Telegram offset persistence (a restart must not re-deliver) --------------

_UPDATE = {"update_id": 10, "message": {
    "from": {"id": 12345}, "chat": {"id": 777, "type": "private"}, "text": "분석해줘"}}


def _capture_urlopen(monkeypatch, payloads):
    """Pop one payload per call; record each call's parsed form params."""
    import urllib.parse as _urlparse
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(dict(_urlparse.parse_qsl(request.data.decode("utf-8"))))
        return _FakeResp(payloads.pop(0))
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return calls


def test_telegram_offset_persists_across_restarts(monkeypatch, tmp_path):
    monkeypatch.setenv(TOKEN_ENV, "test-token")
    state = tmp_path / "state" / "telegram_offset.json"
    calls = _capture_urlopen(monkeypatch, [
        {"ok": True, "result": [_UPDATE]},
        {"ok": True, "result": []},
    ])
    TelegramChannel(authorization=_TG_AUTH, state_path=state).poll()
    assert json.loads(state.read_text(encoding="utf-8"))["offset"] == 11
    # A fresh instance — a restarted process — resumes AFTER the fetched update instead
    # of re-fetching (and re-executing) up to 24h of unconfirmed updates from offset 0.
    TelegramChannel(authorization=_TG_AUTH, state_path=state).poll()
    assert calls[1]["offset"] == "11"


def test_malformed_update_id_is_skipped_not_fatal(monkeypatch):
    """int(None) raises TypeError, which is not OperatorBlocked — the loop's handler
    misses it and the whole service dies with a traceback. The malformed update is
    skipped and the cursor does not advance past it, so nothing is silently claimed."""
    monkeypatch.setenv(TOKEN_ENV, "test-token")
    _patch_urlopen(monkeypatch, {"ok": True, "result": [
        {"update_id": None, "message": {"from": {"id": 12345},
                                        "chat": {"id": 777, "type": "private"}, "text": "a"}},
        {"update_id": "abc", "message": {"from": {"id": 12345},
                                         "chat": {"id": 777, "type": "private"}, "text": "b"}},
        _UPDATE,
    ]})
    channel = TelegramChannel(authorization=_TG_AUTH)
    msgs = channel.poll()
    assert [m.text for m in msgs] == ["분석해줘"]      # only the well-formed update
    assert channel._offset == 11                      # advanced past that one only


def test_telegram_malformed_offset_state_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv(TOKEN_ENV, "test-token")
    state = tmp_path / "telegram_offset.json"
    state.write_text("{broken", encoding="utf-8")
    with pytest.raises(OperatorBlocked) as exc:
        TelegramChannel(authorization=_TG_AUTH, state_path=state).poll()
    assert exc.value.reason_code == "OFFSET_STATE_MALFORMED"


def test_telegram_without_state_path_keeps_the_cursor_in_memory(monkeypatch, tmp_path):
    """Direct construction (the test path) must not create machine-local state files."""
    monkeypatch.setenv(TOKEN_ENV, "test-token")
    _patch_urlopen(monkeypatch, {"ok": True, "result": [_UPDATE]})
    channel = TelegramChannel(authorization=_TG_AUTH)
    channel.poll()
    assert channel._offset == 11
    assert list(tmp_path.iterdir()) == []


# --- Telegram 4096-unit send chunking -----------------------------------------

def test_split_for_send_short_text_is_one_chunk():
    from runtime.mvp_runtime.operator import _split_for_send
    assert _split_for_send("짧은 답변", 4000) == ["짧은 답변"]
    assert _split_for_send("", 4000) == [""]


def test_split_for_send_cuts_after_newlines_and_loses_nothing():
    from runtime.mvp_runtime.operator import _split_for_send
    text = "\n".join(f"분석 라인 {i}: " + "내용" * 40 for i in range(120))
    chunks = _split_for_send(text, 4000)
    assert len(chunks) > 1
    assert "".join(chunks) == text                      # nothing lost, nothing reordered
    for chunk in chunks[:-1]:
        assert chunk.endswith("\n")                     # preferred cut is a line boundary
    for chunk in chunks:
        assert sum(2 if ord(c) > 0xFFFF else 1 for c in chunk) <= 4000


def test_split_for_send_counts_utf16_units_for_astral_chars():
    from runtime.mvp_runtime.operator import _split_for_send
    text = "\U0001F600" * 2100                          # each emoji is 2 UTF-16 units
    chunks = _split_for_send(text, 4000)
    assert [len(c) for c in chunks] == [2000, 100]
    assert "".join(chunks) == text


def test_telegram_send_chunks_long_replies(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "test-token")
    long_reply = "\n".join("사업성 분석 결과 문단 " + "상세 " * 50 for _ in range(60))
    calls = _capture_urlopen(monkeypatch, [{"ok": True, "result": []}] * 10)
    TelegramChannel(authorization=_TG_AUTH).send("chat-777", long_reply)
    assert len(calls) > 1                               # split into several sendMessage calls
    assert "".join(c["text"] for c in calls) == long_reply
    for c in calls:
        assert sum(2 if ord(ch) > 0xFFFF else 1 for ch in c["text"]) <= 4000


# --- a failed send must not abort the rest of an already-claimed batch --------

class _ResultSendFailsChannel(MockOperatorChannel):
    """Fails the RESULT delivery (never the working ack, which is best-effort anyway) —
    the shape of a reply Telegram rejects after the work is already done."""

    def send(self, chat_id: str, text: str) -> None:
        if "분석 결과" in text:
            self.sent.append((chat_id, "<DELIVERY FAILED>"))
            raise OperatorBlocked("CHANNEL_TRANSPORT", "telegram sendMessage returned an error response")
        super().send(chat_id, text)


def test_run_once_send_failure_does_not_abort_the_batch(tmp_path, monkeypatch):
    """The poll cursor is advanced before handling (a batch is claimed once), so a failed
    reply delivery must not abort the remaining messages — a /kill queued behind a long
    analysis would otherwise be lost forever."""
    import runtime.mvp_runtime.operator as operator_mod
    from runtime.mvp_runtime.control import ControlStore, KILLED
    monkeypatch.setattr(
        operator_mod, "run_task",
        lambda *a, **k: {"status": "COMPLETED", "final_response": "분석 결과", "records": {}},
    )
    control_store = ControlStore(tmp_path / "control")
    ch = _ResultSendFailsChannel(inbound=[_msg(text="이 사업 아이디어를 분석해줘"), _msg(text="/kill")])
    summary = run_operator_once(ch, REG, provider=MockProvider(), now=NOW, control_store=control_store)
    assert summary["handled"] == 2 and summary["send_failures"] == 1
    assert control_store.load().mode == KILLED          # the queued /kill still fired


# --- unmatched slash commands must never reach the pipeline -------------------

def test_unknown_slash_command_is_refused_not_analyzed(tmp_path, monkeypatch):
    import runtime.mvp_runtime.operator as operator_mod
    from runtime.mvp_runtime.control import ControlStore
    monkeypatch.setattr(operator_mod, "run_task", lambda *a, **k: pytest.fail("run_task must not run"))
    for text in ("/killl", "/unknown thing", "/approve@otherbot x"):
        reply = handle_operator_message(
            _msg(text=text), registration=REG, now=NOW,
            control_store=ControlStore(tmp_path / "control"),
        )
        assert reply.accepted is False and reply.reason_code == "UNKNOWN_COMMAND", text


def test_unknown_slash_command_refused_even_without_stores(monkeypatch):
    # With no console/approval store wired, a slash message still must not become a task.
    import runtime.mvp_runtime.operator as operator_mod
    monkeypatch.setattr(operator_mod, "run_task", lambda *a, **k: pytest.fail("run_task must not run"))
    reply = handle_operator_message(_msg(text="/status"), registration=REG, now=NOW)
    assert reply.accepted is False and reply.reason_code == "UNKNOWN_COMMAND"


def test_botname_suffixed_kill_fires_the_emergency_verb(tmp_path, monkeypatch):
    """Telegram appends the bot username to menu-picked commands: /kill@bot must KILL,
    never be analyzed as a business idea."""
    import runtime.mvp_runtime.operator as operator_mod
    from runtime.mvp_runtime.control import ControlStore, KILLED
    monkeypatch.setattr(operator_mod, "run_task", lambda *a, **k: pytest.fail("run_task must not run"))
    control_store = ControlStore(tmp_path / "control")
    reply = handle_operator_message(
        _msg(text="/kill@thomas_agent_bot"), registration=REG, now=NOW, control_store=control_store,
    )
    assert reply.accepted is True and reply.status == "CONTROL"
    assert control_store.load().mode == KILLED


# --- the received-working ack --------------------------------------------------

def test_ack_is_sent_before_the_pipeline_runs(tmp_path, monkeypatch):
    """A pipeline run holds the channel for a model call's length; to the operator that
    silence was indistinguishable from a dead service. The ack fires after every refusal
    path and before run_task, on the same verified chat."""
    import runtime.mvp_runtime.operator as operator_mod
    order: list[str] = []

    def fake_run_task(text, **kwargs):
        order.append("run_task")
        return {"status": "COMPLETED", "final_response": "분석 결과", "records": {}}
    monkeypatch.setattr(operator_mod, "run_task", fake_run_task)

    ch = MockOperatorChannel(inbound=[_msg()])
    original_send = ch.send

    def tracking_send(chat_id, text):
        order.append("send")
        original_send(chat_id, text)
    ch.send = tracking_send

    summary = run_operator_once(ch, REG, provider=MockProvider(), now=NOW)
    assert summary["handled"] == 1
    assert order == ["send", "run_task", "send"]          # ack -> pipeline -> result
    assert [c for c, _ in ch.sent] == ["chat-777", "chat-777"]
    assert "분석 중" in ch.sent[0][1]                       # the notice
    assert ch.sent[1][1] == "분석 결과"                     # then the answer


def test_no_ack_for_refused_or_command_messages(tmp_path, monkeypatch):
    """The ack means "the pipeline is about to run" — a refusal, a console command, or an
    approval answer must produce exactly its one reply, never a working notice."""
    import runtime.mvp_runtime.operator as operator_mod
    from runtime.mvp_runtime.approval_store import ApprovalStore
    from runtime.mvp_runtime.control import ControlStore
    monkeypatch.setattr(operator_mod, "run_task", lambda *a, **k: pytest.fail("run_task must not run"))
    control_store = ControlStore(tmp_path / "control")

    for text in ("/status", "/killl", "/approve approval_nope"):
        ch = MockOperatorChannel(inbound=[_msg(text=text)])
        run_operator_once(ch, REG, provider=MockProvider(), now=NOW,
                          control_store=control_store,
                          approval_store=ApprovalStore(tmp_path / "approvals"))
        assert len(ch.sent) == 1, text                    # one reply, no ack
        assert "분석 중" not in ch.sent[0][1], text

    # Unverified senders still get nothing at all.
    ch = MockOperatorChannel(inbound=[_msg(sender_id="tg-99999")])
    run_operator_once(ch, REG, provider=MockProvider(), now=NOW)
    assert ch.sent == []


def test_a_failed_ack_does_not_cost_the_run(tmp_path, monkeypatch):
    """The notice is a courtesy, the run is the job: an ack send failure is swallowed and
    the pipeline result is still produced and delivered."""
    import runtime.mvp_runtime.operator as operator_mod
    monkeypatch.setattr(
        operator_mod, "run_task",
        lambda *a, **k: {"status": "COMPLETED", "final_response": "분석 결과", "records": {}},
    )

    class _AckFailsChannel(MockOperatorChannel):
        def send(self, chat_id, text):
            if "분석 중" in text:
                raise OperatorBlocked("CHANNEL_TRANSPORT", "telegram sendMessage returned an error response")
            super().send(chat_id, text)

    ch = _AckFailsChannel(inbound=[_msg()])
    summary = run_operator_once(ch, REG, provider=MockProvider(), now=NOW)
    assert summary["handled"] == 1 and summary["send_failures"] == 0
    assert [t for _, t in ch.sent] == ["분석 결과"]         # the answer still arrived


def test_provider_error_reply_carries_the_retry_hint(monkeypatch):
    import runtime.mvp_runtime.operator as operator_mod
    monkeypatch.setattr(
        operator_mod, "run_task",
        lambda *a, **k: {"status": "BLOCKED", "records": {},
                         "block": {"stage": "pipeline", "reason_code": "PROVIDER_ERROR",
                                   "message": "hosted provider request failed or timed out"}},
    )
    reply = handle_operator_message(_msg(), registration=REG, provider=MockProvider(), now=NOW)
    assert reply.status == "BLOCKED" and reply.reason_code == "PROVIDER_ERROR"
    assert "다시 보내" in reply.text                        # actionable, not just a code
    # Other block codes stay terse — the hint is only for the one transient case.
    monkeypatch.setattr(
        operator_mod, "run_task",
        lambda *a, **k: {"status": "BLOCKED", "records": {},
                         "block": {"stage": "pipeline", "reason_code": "OUT_OF_MVP_SCOPE", "message": "x"}},
    )
    other = handle_operator_message(_msg(), registration=REG, provider=MockProvider(), now=NOW)
    assert "다시 보내" not in other.text


# --- R9 over the loop: /approve must reach the approval path ------------------

def test_run_once_routes_approve_to_the_approval_path_not_the_pipeline(tmp_path, monkeypatch):
    """Thomas's /approve over the deployed loop must draw the approval-path answer (here:
    the unknown-id refusal), never be analyzed as a business idea — the wiring bug this
    guards against silently sent it to the pipeline."""
    import runtime.mvp_runtime.operator as operator_mod
    from runtime.mvp_runtime.approval_store import ApprovalStore
    monkeypatch.setattr(operator_mod, "run_task", lambda *a, **k: pytest.fail("run_task must not run"))
    ch = MockOperatorChannel(inbound=[_msg(text="/approve approval_nope")])
    summary = run_operator_once(ch, REG, provider=MockProvider(), now=NOW,
                                approval_store=ApprovalStore(tmp_path / "approvals"))
    assert summary["handled"] == 1
    assert ch.sent and "no approval with id" in ch.sent[0][1]
