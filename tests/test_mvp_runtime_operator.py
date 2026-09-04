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
    PARTIAL_DELIVERY_REASON,
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
from runtime.mvp_runtime.safety_gate import NETWORK_ACCESS
from runtime.mvp_runtime.worker import MockProvider

NOW = "2026-07-16T09:00:00Z"

from tests._helpers import requires_local_core, make_gate_authorization

REG = OperatorIdentity(operator_id="tg-12345", chat_id="chat-777")

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
_TG_AUTH = make_gate_authorization(flags=(NETWORK_ACCESS,), provider_id="telegram")


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


# --- outbound notification (the identity gate's other half) -----------------

def _register(tmp_path, chat_id="chat-1"):
    state = tmp_path / ".runtime_governance_state"
    state.mkdir(exist_ok=True)
    (state / "operator_registration.json").write_text(
        json.dumps({"operator_id": "tg-1", "chat_id": chat_id, "approver": "Thomas"}), encoding="utf-8"
    )


def test_notify_goes_only_to_the_registered_chat(tmp_path):
    from runtime.mvp_runtime.operator import notify_operator

    _register(tmp_path, chat_id="chat-registered")
    ch = MockOperatorChannel()
    notify_operator(ch, "scheduler is down", repo_root=tmp_path)
    # The destination is the registration's, never the caller's choice.
    assert ch.sent == [("chat-registered", "scheduler is down")]


def test_notify_without_registration_fails_closed(tmp_path):
    from runtime.mvp_runtime.operator import notify_operator

    ch = MockOperatorChannel()
    with pytest.raises(OperatorBlocked) as exc:
        notify_operator(ch, "nobody to tell", repo_root=tmp_path)
    assert exc.value.reason_code == "REGISTRATION_MISSING"
    assert ch.sent == []


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


def test_select_telegram_env_alone_returns_channel(monkeypatch, tmp_path):
    """The environment is the gate (Thomas 2026-08-10): the opt-in alone selects the real
    channel — construction stays key-free (the token is read by name at send time), and
    an unset or different value still reaches only the Mock."""
    monkeypatch.setenv(OPERATOR_CHANNEL_ENV, "telegram")
    channel = select_operator_channel(now="2026-07-16T00:00:00Z", root=tmp_path)
    assert isinstance(channel, TelegramChannel)


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


class _FoundSomethingElseProvider(MockProvider):
    """The mock answers every prompt with the same five findings, so a second run through it
    proposes nothing the store does not already hold. This one answers with a different
    finding, which is what "a later run adds more" was always reaching for."""

    def generate(self, prompt: str, *, max_output_tokens: int, timeout_seconds: int):
        result = super().generate(prompt, max_output_tokens=max_output_tokens,
                                  timeout_seconds=timeout_seconds)
        result.analysis["key_findings"] = ["retention: 유지율은 3개월 차에 갈린다"]
        return result


@requires_local_core
def test_operator_accumulates_working_memory(tmp_path):
    """Working memory accumulates ACROSS runs — that is what the store is for.

    What it stopped doing is storing the same finding twice. `MockProvider` returns a fixed
    five findings regardless of prompt, so a second run through it is precisely the duplicate
    case, and asserting the row count grew was asserting the duplication rather than the
    accumulation. Both halves are pinned below so the distinction cannot quietly invert."""
    from runtime.mvp_runtime.working_memory import WorkingMemoryStore
    wm = WorkingMemoryStore(tmp_path / "wm")

    run_operator_once(MockOperatorChannel(inbound=[_msg()]), REG, provider=MockProvider(), working_memory=wm, now=NOW)
    after_first = len(wm.read_all())
    assert after_first  # the operator run accumulated working memory

    # The same findings again, from a different request: recorded once, not twice.
    run_operator_once(MockOperatorChannel(inbound=[_msg(text="구독 사업 유지율 분석")]), REG,
                      provider=MockProvider(), working_memory=wm, now="2026-07-16T10:00:00Z")
    assert len(wm.read_all()) == after_first

    # A run that actually found something else still accumulates.
    run_operator_once(MockOperatorChannel(inbound=[_msg(text="유지율 곡선 분석")]), REG,
                      provider=_FoundSomethingElseProvider(), working_memory=wm,
                      now="2026-07-16T11:00:00Z")
    assert len(wm.read_all()) > after_first

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
    total = len(calls)
    assert total > 1                                    # split into several sendMessage calls
    # Each part is numbered, so a missing one is visible to Thomas rather than reading as an
    # analysis that just stopped mid-sentence. The counter is a prefix line, not content.
    bodies = []
    for index, c in enumerate(calls, start=1):
        head, _, body = c["text"].partition("\n")
        assert head == f"({index}/{total})"
        bodies.append(body)
        # The counter fits in the headroom between the split limit and Telegram's real cap.
        assert sum(2 if ord(ch) > 0xFFFF else 1 for ch in c["text"]) <= 4096
    # The split cuts AFTER a newline, so each body keeps its own line break — plain
    # concatenation must reproduce the reply exactly.
    assert "".join(bodies) == long_reply                # nothing lost, nothing duplicated


def test_a_short_reply_is_not_numbered(monkeypatch):
    """One part is not a series — numbering it would put a `(1/1)` on every ordinary answer."""
    monkeypatch.setenv(TOKEN_ENV, "test-token")
    calls = _capture_urlopen(monkeypatch, [{"ok": True, "result": []}])
    TelegramChannel(authorization=_TG_AUTH).send("chat-777", "짧은 답변")
    assert [c["text"] for c in calls] == ["짧은 답변"]


def test_a_failure_after_the_first_part_is_reported_as_partial(monkeypatch):
    """"The reply was not delivered" is false when three of five parts arrived. A distinct
    reason code exists so nothing downstream has to describe it that way."""
    monkeypatch.setenv(TOKEN_ENV, "test-token")
    long_reply = "\n".join("사업성 분석 결과 문단 " + "상세 " * 50 for _ in range(60))
    calls = _capture_urlopen(monkeypatch, [{"ok": True, "result": []}, {"ok": False}])
    with pytest.raises(OperatorBlocked) as exc:
        TelegramChannel(authorization=_TG_AUTH).send("chat-777", long_reply)
    assert exc.value.reason_code == PARTIAL_DELIVERY_REASON
    assert "1 of 3" in exc.value.reason
    assert len(calls) == 2                              # the first landed, the second failed


def test_a_failure_on_the_very_first_part_stays_an_ordinary_send_failure(monkeypatch):
    """Nothing arrived, so it is not partial — the caller must keep treating it as a plain
    delivery failure (FAILED/SEND_FAILED on the queue entry)."""
    monkeypatch.setenv(TOKEN_ENV, "test-token")
    _capture_urlopen(monkeypatch, [{"ok": False}])       # the only part is rejected
    with pytest.raises(OperatorBlocked) as exc:
        TelegramChannel(authorization=_TG_AUTH).send("chat-777", "짧은 답변")
    assert exc.value.reason_code == "CHANNEL_TRANSPORT"


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


# --- R7.1: the importance marker ----------------------------------------------

def _capture_run_task(monkeypatch):
    import runtime.mvp_runtime.operator as operator_mod
    captured = {}

    def fake_run_task(text, **kwargs):
        captured["text"] = text
        captured["kwargs"] = kwargs
        return {"status": "COMPLETED", "final_response": "ok", "records": {}}

    monkeypatch.setattr(operator_mod, "run_task", fake_run_task)
    return captured


def test_important_marker_raises_priority_and_is_stripped(monkeypatch):
    captured = _capture_run_task(monkeypatch)
    reply = handle_operator_message(
        _msg(text="!중요 이 사업 아이디어를 분석해줘: 구독 모델"),
        registration=REG, now=NOW, independent_validation="auto",
    )
    assert reply.accepted is True
    assert captured["text"] == "이 사업 아이디어를 분석해줘: 구독 모델"
    assert captured["kwargs"]["priority"] == "HIGH"
    assert captured["kwargs"]["independent_validation"] == "auto"


def test_english_marker_is_case_insensitive(monkeypatch):
    captured = _capture_run_task(monkeypatch)
    handle_operator_message(_msg(text="!IMPORTANT analyze this idea"), registration=REG, now=NOW)
    assert captured["text"] == "analyze this idea"
    assert captured["kwargs"]["priority"] == "HIGH"


def test_marker_must_be_a_standalone_token(monkeypatch):
    """'!중요한 아이디어...' is prose that happens to start with the marker's characters —
    it must run unchanged at NORMAL priority."""
    captured = _capture_run_task(monkeypatch)
    handle_operator_message(_msg(text="!중요한 아이디어를 분석해줘"), registration=REG, now=NOW)
    assert captured["text"] == "!중요한 아이디어를 분석해줘"
    assert captured["kwargs"]["priority"] == "NORMAL"


def test_bare_marker_is_refused_not_run(monkeypatch):
    import runtime.mvp_runtime.operator as operator_mod
    monkeypatch.setattr(operator_mod, "run_task", lambda *a, **k: pytest.fail("run_task must not run"))
    reply = handle_operator_message(_msg(text="!중요"), registration=REG, now=NOW)
    assert reply.accepted is False and reply.reason_code == "EMPTY_REQUEST"


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


def test_with_a_registry_the_queue_receipt_replaces_the_ack(tmp_path, monkeypatch):
    """The ack's docstring says it fires only on the inline path — pin that, because every
    deployment wires a registry (``operator_cli`` always builds one), so the ack is dead code
    there and the QUEUED receipt is what tells the operator they were heard. A `handled`
    message must never leave the channel silent whichever path it took."""
    import runtime.mvp_runtime.operator as operator_mod
    from runtime.mvp_runtime.task_registry import TaskRegistryStore
    monkeypatch.setattr(operator_mod, "run_task",
                        lambda *a, **k: pytest.fail("a queued request must not run inline"))

    ch = MockOperatorChannel(inbound=[_msg()])
    summary = run_operator_once(ch, REG, provider=MockProvider(), now=NOW,
                               registry=TaskRegistryStore(tmp_path / "registry"),
                               max_queued_tasks=0, repo_root=tmp_path)
    assert summary["handled"] == 1
    assert len(ch.sent) == 1                              # the receipt only — no ack
    assert "분석 중" not in ch.sent[0][1]
    assert "접수했습니다" in ch.sent[0][1]


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


def test_validation_revise_reply_carries_the_reviewers_reasons(monkeypatch):
    """The reviewer's revision requests ARE the deliverable of a withheld run — over the
    live channel they used to be dropped, leaving a bare VALIDATION_REVISE code."""
    import runtime.mvp_runtime.operator as operator_mod
    monkeypatch.setattr(
        operator_mod, "run_task",
        lambda *a, **k: {"status": "BLOCKED", "records": {},
                         "block": {"stage": "validation", "reason_code": "VALIDATION_REVISE",
                                   "message": ("All automatic output checks passed.; "
                                               "Start with one pilot store first; "
                                               "Seek professional financial advice")}},
    )
    reply = handle_operator_message(_msg(), registration=REG, provider=MockProvider(), now=NOW)
    assert reply.status == "BLOCKED" and reply.reason_code == "VALIDATION_REVISE"
    assert "- Start with one pilot store first" in reply.text
    assert "- Seek professional financial advice" in reply.text
    assert "다시 보내주시면 새로 분석합니다" in reply.text


def test_other_block_replies_include_their_message_verbatim(monkeypatch):
    import runtime.mvp_runtime.operator as operator_mod
    monkeypatch.setattr(
        operator_mod, "run_task",
        lambda *a, **k: {"status": "BLOCKED", "records": {},
                         "block": {"stage": "pipeline", "reason_code": "OUT_OF_MVP_SCOPE",
                                   "message": "task scope must carry the constraint"}},
    )
    reply = handle_operator_message(_msg(), registration=REG, provider=MockProvider(), now=NOW)
    assert "task scope must carry the constraint" in reply.text
    assert "새로 분석합니다" not in reply.text     # the resubmit guidance is validation-only


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


@requires_local_core
def test_free_text_after_the_id_becomes_the_recorded_decision_reason(tmp_path):
    """Over the deployed loop, `/reject <id> <free text>` must land Thomas's own words in
    the durable decision record — the accumulated reasons are what later preference
    inference reads, so boilerplate-only capture here would starve it."""
    from runtime.mvp_runtime import approval, permission
    from runtime.mvp_runtime.approval_store import ApprovalStore
    from runtime.mvp_runtime.binding import bind_task_to_core
    from runtime.mvp_runtime.intake import build_task

    task = build_task("이 사업 아이디어를 분석해줘", now=NOW)
    _, bound = bind_task_to_core(task, now=NOW)
    candidate = {
        "candidate_id": "memcand_reason01",
        "candidate_type": "operating_preference",
        "content": "Thomas prefers cash-flow first framing in business analyses.",
    }
    permdec = permission.build_memory_promotion_permission_decision(bound, candidate, now=NOW)
    request = approval.build_approval_request(permdec, now=NOW)
    astore = ApprovalStore(tmp_path / "approvals")
    astore.append([request])
    astore.append_permission_decision(permdec)

    ch = MockOperatorChannel(
        inbound=[_msg(text=f"/reject {request['approval_id']} 근거 문서가 부족함")])
    summary = run_operator_once(ch, REG, provider=MockProvider(), now=NOW,
                                approval_store=astore)
    assert summary["handled"] == 1
    decided = astore.get(request["approval_id"])
    assert decided["status"] == "REJECTED"
    assert decided["decision"]["decision_reason"] == "근거 문서가 부족함"
    assert ch.sent and "Reason recorded: 근거 문서가 부족함" in ch.sent[0][1]


# === the 2026-07-25 control-channel review: no typed failure kills the channel ===
#
# The loop and run_operator_once catch only OperatorBlocked, so any other typed error escaping
# handle_operator_message took the whole operator channel down with a traceback. Three branches
# were missing a guard; each of these asserts a typed reply instead of a raised exception.

class _BrokenLedger:
    """A ledger whose every write fails — the corrupt/full-disk case."""

    def __init__(self, root=None):
        self.root = root

    def last_audit_hash(self):
        return None

    def _boom(self, *a, **k):
        from runtime.mvp_runtime.errors import PersistenceError
        raise PersistenceError("LEDGER_WRITE_FAILED", "disk full")

    append_control = append_audit_events = append_records = _boom
    append_block = append_feedback_event = append_memory_event = _boom


def test_a_ledger_failure_on_a_console_command_does_not_kill_the_channel(tmp_path):
    """/resume saves the new mode BEFORE the ledger append, so an uncaught PersistenceError left
    the runtime resumed with no reply, no ledger event, and no channel — the worst of the three."""
    from runtime.mvp_runtime import control

    store = control.ControlStore(tmp_path)
    control.apply_command(store, "kill", actor="tg-12345", now=NOW)   # writes nothing (no ledger)
    reply = handle_operator_message(
        _msg(text="/resume"), registration=REG, control_store=store,
        store=_BrokenLedger(tmp_path), now=NOW, repo_root=tmp_path,
    )
    # A typed reply, not an exception — and it says the state changed but the record did not.
    assert reply.status == "CONTROL" and reply.reason_code == "LEDGER_WRITE_FAILED"
    assert store.load().mode == "ACTIVE"          # the command really did take effect
    assert "원장" in reply.text or "audit" in reply.text.lower()


def test_a_broken_approval_store_is_a_typed_refusal_not_a_crash(tmp_path):
    from runtime.mvp_runtime.errors import PersistenceError

    class _BrokenApprovals:
        def get(self, *a, **k):
            raise PersistenceError("APPROVAL_STORE_UNREADABLE", "corrupt approvals.jsonl")

    reply = handle_operator_message(
        _msg(text="/approve appr_123"), registration=REG, approval_store=_BrokenApprovals(),
        store=None, now=NOW, repo_root=tmp_path,
    )
    assert reply.accepted is False and reply.status == "REFUSED"
    assert reply.reason_code == "APPROVAL_STORE_UNREADABLE"


def test_a_frontdesk_failure_degrades_to_the_queue_instead_of_killing_the_channel(tmp_path):
    """run_turn handles only ProviderError/TimeoutError, so a corrupt working memory or a revoked
    provider grant escaped. The module's promise is that conversation dying never loses a
    message — so any typed failure must fall through to the F1 queue path."""
    from runtime.mvp_runtime import frontdesk as frontdesk_mod
    from runtime.mvp_runtime import operator as operator_mod
    from runtime.mvp_runtime.errors import PersistenceError

    class _Registry:
        def __init__(self):
            self.enqueued = []

        def queued_count(self):
            return 0

        def submit(self, *a, **k):
            self.enqueued.append((a, k))
            raise PersistenceError("REGISTRY_WRITE_FAILED", "queue unavailable")

    def _boom(*a, **k):
        raise PersistenceError("WORKING_MEMORY_UNREADABLE", "corrupt session store")

    registry = _Registry()
    original = frontdesk_mod.run_turn
    frontdesk_mod.run_turn = _boom
    operator_mod.frontdesk.run_turn = _boom
    try:
        reply = handle_operator_message(
            _msg(text="이 아이디어 어때?"), registration=REG, frontdesk_provider=object(),
            registry=registry, store=None, now=NOW, repo_root=tmp_path,
        )
    finally:
        frontdesk_mod.run_turn = original
        operator_mod.frontdesk.run_turn = original
    # It fell through to the queue (which we made fail too) => a typed refusal, never a crash.
    assert reply.accepted is False and reply.reason_code == "REGISTRY_WRITE_FAILED"
    assert registry.enqueued, "the frontdesk failure must degrade to the F1 queue path"


# === chat-channel command hygiene (2026-07-25 review) ==========================

@pytest.mark.parametrize("text", [
    "kill it", "pause 잠깐만", "resume 다시", "stop 그거", "cancel 그거",
    "promote 이거 좋았어", "approve 해줘", "reject 별로야",
])
def test_a_bare_state_changing_verb_is_refused_on_a_chat_channel(tmp_path, text):
    """Verified 2026-07-25: `kill it` HALTED the runtime. Every parser accepts the bare verb —
    right on a terminal, wrong in prose. A refusal naming the slash form is safe both ways."""
    from runtime.mvp_runtime import control

    store = control.ControlStore(tmp_path)
    reply = handle_operator_message(
        _msg(text=text), registration=REG, control_store=store, store=None,
        now=NOW, repo_root=tmp_path,
    )
    assert reply.accepted is False and reply.reason_code == "SLASH_REQUIRED"
    assert store.load().mode == "ACTIVE"          # nothing happened
    assert "/" in reply.text                      # and it says what to type


@pytest.mark.parametrize("text", ["status", "audit", "recovery"])
def test_bare_read_only_verbs_still_work(tmp_path, text):
    """Only state-changing verbs are narrowed; answering /status to "status" costs nothing."""
    from runtime.mvp_runtime import control

    reply = handle_operator_message(
        _msg(text=text), registration=REG, control_store=control.ControlStore(tmp_path),
        store=None, now=NOW, repo_root=tmp_path,
    )
    assert reply.status == "CONTROL"


def test_the_slash_form_of_a_state_changing_verb_still_works(tmp_path):
    from runtime.mvp_runtime import control

    store = control.ControlStore(tmp_path)
    reply = handle_operator_message(
        _msg(text="/kill"), registration=REG, control_store=store, store=None,
        now=NOW, repo_root=tmp_path,
    )
    assert reply.status == "CONTROL" and store.load().mode == "KILLED"


def test_an_importance_marker_cannot_smuggle_a_command_past_the_unknown_guard(tmp_path, monkeypatch):
    """Verified 2026-07-25: `!중요 /killl` skipped the unknown-command guard (the marker strip
    runs after it) and became a pipeline task, spending a model call on a typo'd emergency verb."""
    import runtime.mvp_runtime.operator as operator_mod
    from runtime.mvp_runtime import control

    monkeypatch.setattr(operator_mod, "run_task", lambda *a, **k: pytest.fail("must not run a task"))
    reply = handle_operator_message(
        _msg(text="!중요 /killl"), registration=REG, control_store=control.ControlStore(tmp_path),
        store=None, now=NOW, repo_root=tmp_path,
    )
    assert reply.accepted is False and reply.reason_code == "MARKED_COMMAND"


def test_the_operator_loop_always_wires_the_kill_switch():
    """`handle_operator_message` tolerates `control_store=None` because it is also the
    library/pipeline-only entry point. What must hold is that the DEPLOYMENT never uses that
    mode — so the loop's own wiring is asserted here instead of breaking the library contract."""
    import inspect

    from runtime.mvp_runtime import operator_cli

    source = inspect.getsource(operator_cli)
    assert "control_store" in source
    # The loop constructs a ControlStore and hands it down; a refactor that drops it fails here.
    assert "ControlStore" in source


# --- announcing the switch door's asks ---------------------------------------
#
# The delivery half that never existed. `approval.py` mints an ask and nothing carries it:
# every other approval is minted by Thomas running a script, so the id is already on his
# screen, but the switch door's are minted by an assistant and went nowhere. The one real
# attempt (2026-07-31) expired unanswered fifteen minutes later, and the enable chain had
# never once completed in production before 2026-08-22.

from runtime.mvp_runtime import approval as _approval  # noqa: E402
from runtime.mvp_runtime import permission as _permission  # noqa: E402
from runtime.mvp_runtime.approval_store import ApprovalStore  # noqa: E402
from runtime.mvp_runtime.binding import bind_task_to_core  # noqa: E402
from runtime.mvp_runtime.intake import build_task  # noqa: E402
from runtime.mvp_runtime.operator import (  # noqa: E402
    ANNOUNCED_POINTER_REL,
    MAX_ANNOUNCEMENTS_PER_BATCH,
    announce_pending_approvals,
    load_announced,
)

_ANN_NOW = "2026-08-22T09:00:00Z"


def _switch_ask(store, tmp_path, *, arms=True, now=_ANN_NOW, stop_ref="stop_abc123"):
    """Mint a real switch-door ask into `store` and return it.

    The binding uses the repo's own Core activation (as every other approval test does) while
    the store, the registration and the announced pointer live under `tmp_path` — those are
    what the announcer reads and writes, and they are what must stay isolated per test.
    """
    task = build_task("자동매매를 다시 켜줘", now=now)
    _, bound = bind_task_to_core(task, now=now)
    builder = (_permission.build_trading_switch_permission_decision if arms
               else _permission.build_nonfinancial_resume_permission_decision)
    permdec = builder(
        bound, "crypto", stop_ref=stop_ref,
        stop_summary="the KILLED placed by local_console", now=now,
    )
    req = _approval.build_approval_request(permdec, now=now)
    store.append([req])
    store.append_permission_decision(permdec)
    return req


@requires_local_core
def test_the_first_run_marks_the_backlog_seen_without_sending_it(tmp_path):
    """Ten asks are pending on the deployment host, the oldest from 2026-07-21. A first run
    that announced its backlog would open with a burst of messages Thomas cannot act on."""
    _register(tmp_path, chat_id="chat-registered")
    store = ApprovalStore(tmp_path)
    ask = _switch_ask(store, tmp_path)
    ch = MockOperatorChannel()

    assert load_announced(tmp_path) is None
    sent = announce_pending_approvals(ch, store, now=_ANN_NOW, repo_root=tmp_path)

    assert sent == []
    assert ch.sent == []
    assert load_announced(tmp_path) == {ask["approval_id"]}
    assert (tmp_path / ANNOUNCED_POINTER_REL).is_file()


@requires_local_core
def test_a_pending_switch_ask_is_announced_once_to_the_registered_chat(tmp_path):
    """The whole point: the ask reaches the window `/approve` is read in — and only once."""
    _register(tmp_path, chat_id="chat-registered")
    store = ApprovalStore(tmp_path)
    announce_pending_approvals(ch := MockOperatorChannel(), store, now=_ANN_NOW, repo_root=tmp_path)

    ask = _switch_ask(store, tmp_path)
    sent = announce_pending_approvals(ch, store, now=_ANN_NOW, repo_root=tmp_path)

    assert sent == [ask["approval_id"]]
    assert len(ch.sent) == 1
    chat_id, text = ch.sent[0]
    assert chat_id == "chat-registered"          # never the caller's choice
    assert ask["approval_id"] in text
    assert "Approval Request" in text

    # A second pass sends nothing: the pointer is keyed on the id, not a timestamp.
    assert announce_pending_approvals(ch, store, now=_ANN_NOW, repo_root=tmp_path) == []
    assert len(ch.sent) == 1


@requires_local_core
def test_an_expired_ask_is_never_announced(tmp_path):
    """A dead id cannot be approved. Sending one invites Thomas to answer something that will
    refuse him — which is how the 2026-07-31 attempt read from his side."""
    _register(tmp_path, chat_id="chat-registered")
    store = ApprovalStore(tmp_path)
    announce_pending_approvals(ch := MockOperatorChannel(), store, now=_ANN_NOW, repo_root=tmp_path)
    _switch_ask(store, tmp_path)

    long_after = "2026-08-22T23:00:00Z"
    assert announce_pending_approvals(ch, store, now=long_after, repo_root=tmp_path) == []
    assert ch.sent == []


@requires_local_core
def test_only_the_switch_doors_asks_are_announced(tmp_path):
    """`pending()` returns every scope. Memory candidates, strategy-pool promotions and probe
    batches are already in front of Thomas by the flow that minted them — announcing those
    would put a candidate's full text into the control channel for nothing. The filter is the
    target prefix, because both switch asks share RUNTIME_GOVERNANCE with strategy promotion."""
    _register(tmp_path, chat_id="chat-registered")
    store = ApprovalStore(tmp_path)
    announce_pending_approvals(ch := MockOperatorChannel(), store, now=_ANN_NOW, repo_root=tmp_path)

    task = build_task("이 사업 아이디어를 분석해줘", now=_ANN_NOW)
    _, bound = bind_task_to_core(task, now=_ANN_NOW)
    candidate = {
        "candidate_id": "memcand_notannounced01",
        "candidate_type": "operating_preference",
        "content": "Thomas prefers cash-flow first framing.",
    }
    permdec = _permission.build_memory_promotion_permission_decision(
        bound, candidate, now=_ANN_NOW)
    memory_req = _approval.build_approval_request(permdec, now=_ANN_NOW)
    store.append([memory_req])
    store.append_permission_decision(permdec)

    switch_req = _switch_ask(store, tmp_path)

    sent = announce_pending_approvals(ch, store, now=_ANN_NOW, repo_root=tmp_path)
    assert sent == [switch_req["approval_id"]]
    assert memory_req["approval_id"] not in "".join(t for _, t in ch.sent)


@requires_local_core
def test_the_batch_cap_leaves_the_rest_for_the_next_pass(tmp_path):
    """A cap, not a rate limit — and the unsent ones must NOT be marked seen. Marking by a
    timestamp watermark instead of the id set is exactly how a capped pass loses an ask."""
    _register(tmp_path, chat_id="chat-registered")
    store = ApprovalStore(tmp_path)
    announce_pending_approvals(ch := MockOperatorChannel(), store, now=_ANN_NOW, repo_root=tmp_path)

    asks = [_switch_ask(store, tmp_path, stop_ref=f"stop_{i:04d}")
            for i in range(MAX_ANNOUNCEMENTS_PER_BATCH + 2)]
    first = announce_pending_approvals(ch, store, now=_ANN_NOW, repo_root=tmp_path)
    assert len(first) == MAX_ANNOUNCEMENTS_PER_BATCH

    second = announce_pending_approvals(ch, store, now=_ANN_NOW, repo_root=tmp_path)
    assert len(second) == 2
    assert set(first) | set(second) == {a["approval_id"] for a in asks}
    assert len(ch.sent) == len(asks)


@requires_local_core
def test_announcing_without_a_registration_fails_closed(tmp_path):
    """Same gate as every other outbound: with nobody registered there is nobody to notify,
    and the loop reports the reason code rather than sending anywhere."""
    store = ApprovalStore(tmp_path)
    (tmp_path / ANNOUNCED_POINTER_REL).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / ANNOUNCED_POINTER_REL).write_text(
        json.dumps({"announced_approval_ids": [], "updated_at": _ANN_NOW}), encoding="utf-8")
    _switch_ask(store, tmp_path)

    ch = MockOperatorChannel()
    with pytest.raises(OperatorBlocked) as exc:
        announce_pending_approvals(ch, store, now=_ANN_NOW, repo_root=tmp_path)
    assert exc.value.reason_code == "REGISTRATION_MISSING"
    assert ch.sent == []

# --- PR11: the approval mirror — the same ask in the assistant's window, decided here ----------

from runtime.mvp_runtime.operator import (  # noqa: E402
    MIRROR_SEND_ONLY_REASON,
    MIRROR_TOKEN_ENV,
    SendOnlyChannel,
    mirror_message,
    select_mirror_channel,
)


def test_the_send_only_wrapper_refuses_to_poll_or_peek_and_delegates_send():
    """One bot token, one poller: a second `getUpdates` caller would steal the assistant's
    updates, so the wrapper cannot poll by construction — not by convention."""
    inner = MockOperatorChannel()
    mirror = SendOnlyChannel(inner)
    with pytest.raises(OperatorBlocked) as exc:
        mirror.poll()
    assert exc.value.reason_code == MIRROR_SEND_ONLY_REASON
    with pytest.raises(OperatorBlocked) as exc:
        mirror.peek()
    assert exc.value.reason_code == MIRROR_SEND_ONLY_REASON
    assert mirror.send("chat-1", "hi") == "mock-msg-1"
    assert inner.sent == [("chat-1", "hi")]
    assert mirror.network_egress is False           # the wrapped channel's capability, not a claim


def test_the_mirror_is_off_and_says_so_without_the_gate_the_token_or_with_the_control_bots_own_token(
    monkeypatch, capsys,
):
    monkeypatch.delenv(OPERATOR_CHANNEL_ENV, raising=False)
    monkeypatch.setenv(MIRROR_TOKEN_ENV, "assistant-token-value")
    assert select_mirror_channel() is None
    monkeypatch.setenv(OPERATOR_CHANNEL_ENV, "telegram")
    monkeypatch.delenv(MIRROR_TOKEN_ENV, raising=False)
    assert select_mirror_channel() is None
    monkeypatch.setenv(MIRROR_TOKEN_ENV, "shared-token-value")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "shared-token-value")
    assert select_mirror_channel() is None
    err = capsys.readouterr().err
    assert err.count("OPERATOR: approval mirror OFF") == 3
    assert "one bot cannot mirror itself" in err
    assert "token-value" not in err                  # a token value is never printed, even on refusal


def test_the_mirror_wraps_the_assistants_bot_send_only_behind_the_same_gate(monkeypatch, capsys):
    monkeypatch.setenv(OPERATOR_CHANNEL_ENV, "telegram")
    monkeypatch.setenv(MIRROR_TOKEN_ENV, "assistant-token-value")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "control-token-value")
    mirror = select_mirror_channel()
    assert isinstance(mirror, SendOnlyChannel)
    assert mirror.network_egress is True
    assert isinstance(mirror._inner, TelegramChannel)
    assert mirror._inner._token_env == MIRROR_TOKEN_ENV     # read by NAME, at send time
    with pytest.raises(OperatorBlocked):
        mirror.poll()
    err = capsys.readouterr().err
    assert "OPERATOR: approval mirror ON" in err and "token-value" not in err


@requires_local_core
def test_a_switch_ask_is_mirrored_with_a_different_body_to_the_same_registered_chat(tmp_path):
    """The copy reaches the assistant's window (same registered private chat, other bot) and
    does NOT carry the `/approve | /reject` invitation — it says where the decision goes."""
    _register(tmp_path, chat_id="chat-registered")
    store = ApprovalStore(tmp_path)
    announce_pending_approvals(MockOperatorChannel(), store, now=_ANN_NOW, repo_root=tmp_path)
    ask = _switch_ask(store, tmp_path)
    primary, mirror = MockOperatorChannel(), MockOperatorChannel()

    sent = announce_pending_approvals(primary, store, now=_ANN_NOW, repo_root=tmp_path, mirror=mirror)

    assert sent == [ask["approval_id"]]
    assert len(primary.sent) == 1 and len(mirror.sent) == 1
    assert mirror.sent[0][0] == "chat-registered"
    primary_text, mirror_text = primary.sent[0][1], mirror.sent[0][1]
    assert "가능한 선택:" in primary_text and f"/approve {ask['approval_id']}" in primary_text
    assert "가능한 선택:" not in mirror_text and "(id 뒤에 이유를" not in mirror_text
    assert mirror_text.startswith("[알림 사본] 결정은 관제봇 창에서")
    assert "Approval Request" in mirror_text and ask["approval_id"] in mirror_text
    assert "아무에게도 닿지 않습니다" in mirror_text
    assert mirror_text == mirror_message(ask, store.get_permission_decision(ask["permission_decision_id"]))

    # A second pass sends nothing on either channel: one pointer, keyed on the primary send.
    assert announce_pending_approvals(primary, store, now=_ANN_NOW, repo_root=tmp_path, mirror=mirror) == []
    assert len(primary.sent) == 1 and len(mirror.sent) == 1


class _RefusingMirror(MockOperatorChannel):
    def send(self, chat_id: str, text: str) -> str | None:
        raise OperatorBlocked("NO_BOT_TOKEN", "environment variable HERMES_BOT_TOKEN is not set")


@requires_local_core
def test_a_mirror_failure_is_one_stderr_line_and_never_re_announces(tmp_path, capsys):
    """D-5: the control-channel push is the authority and the pointer follows it alone. A
    mirror that cannot send costs a stderr line, not a second ask on the control channel."""
    _register(tmp_path, chat_id="chat-registered")
    store = ApprovalStore(tmp_path)
    announce_pending_approvals(MockOperatorChannel(), store, now=_ANN_NOW, repo_root=tmp_path)
    ask = _switch_ask(store, tmp_path)
    primary = MockOperatorChannel()

    sent = announce_pending_approvals(
        primary, store, now=_ANN_NOW, repo_root=tmp_path, mirror=_RefusingMirror(),
    )

    assert sent == [ask["approval_id"]] and len(primary.sent) == 1
    assert load_announced(tmp_path) == {ask["approval_id"]}
    assert f"approval {ask['approval_id']} announced but not mirrored (NO_BOT_TOKEN)" in capsys.readouterr().err
    assert announce_pending_approvals(
        primary, store, now=_ANN_NOW, repo_root=tmp_path, mirror=_RefusingMirror(),
    ) == []
    assert len(primary.sent) == 1
