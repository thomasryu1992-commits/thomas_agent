"""E1 operator-feedback tests — parse, delivery pointer, recording, channel wiring.

Feedback is the control channel's ground-truth capture: Thomas's verdict on a
delivered run, bound to the last delivered COMPLETED reply, recorded append-only on
its own ledger stream. Fail-closed everywhere: no pointer, an unreadable pointer, an
empty payload, or a missing ledger all refuse with a typed reason — never a guess.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import control, operator_feedback
from runtime.mvp_runtime.errors import OperatorBlocked, PersistenceError
from runtime.mvp_runtime.jsonl import read_objects
from runtime.mvp_runtime.operator import (
    InboundMessage,
    MockOperatorChannel,
    OperatorIdentity,
    OperatorReply,
    handle_operator_message,
    run_operator_once,
)
from runtime.mvp_runtime.operator_feedback import (
    LAST_DELIVERED_REL,
    apply_feedback,
    load_last_delivered,
    parse_feedback_command,
    record_delivery,
    split_verdict,
)
from runtime.mvp_runtime.store import FEEDBACK_FILE, LedgerStore

NOW = "2026-07-23T09:00:00Z"
REG = OperatorIdentity(operator_id="tg-12345", chat_id="chat-777")


def _msg(text):
    return InboundMessage(text=text, sender_id="tg-12345", chat_id="chat-777",
                          chat_type="private", is_forwarded=False, channel="telegram_private")


def _ledger(tmp_path):
    return LedgerStore(tmp_path / "ledger")


def _feedback_rows(tmp_path):
    return read_objects(tmp_path / "ledger" / FEEDBACK_FILE,
                        read_code="LEDGER_UNREADABLE", label="feedback")


# --- parsing -----------------------------------------------------------------

@pytest.mark.parametrize("text, payload", [
    ("/feedback good 유용했음", "good 유용했음"),
    ("feedback bad", "bad"),                        # leading slash optional (console rule)
    ("/feedback@thomas_bot 좋음", "좋음"),           # Telegram menu suffix stripped
    ("/FEEDBACK note", "note"),
    ("/feedback", ""),                              # a feedback command with no payload
])
def test_parse_accepts_feedback_forms(text, payload):
    assert parse_feedback_command(text) == payload


@pytest.mark.parametrize("text", ["/feedbackx good", "/approve fb-1", "이 사업 아이디어를 분석해줘", "", None, 42])
def test_parse_rejects_non_feedback(text):
    assert parse_feedback_command(text) is None


@pytest.mark.parametrize("payload, verdict, comment", [
    ("good 아주 유용했음", "GOOD", "아주 유용했음"),
    ("좋음", "GOOD", ""),
    ("BAD 근거가 빈약함", "BAD", "근거가 빈약함"),
    ("별로 너무 김", "BAD", "너무 김"),
    ("그냥 참고 메모", "NOTE", "그냥 참고 메모"),
])
def test_split_verdict(payload, verdict, comment):
    assert split_verdict(payload) == (verdict, comment)


# --- delivery pointer --------------------------------------------------------

def test_delivery_pointer_round_trip(tmp_path):
    record_delivery("trace_abc", now=NOW, repo_root=tmp_path)
    assert load_last_delivered(tmp_path) == {"trace_id": "trace_abc", "delivered_at": NOW}


def test_no_pointer_is_none(tmp_path):
    assert load_last_delivered(tmp_path) is None


@pytest.mark.parametrize("content", [
    "not json", json.dumps({"trace_id": "t"}), json.dumps({"trace_id": "", "delivered_at": NOW}),
    json.dumps({"trace_id": None, "delivered_at": NOW}),
])
def test_malformed_pointer_fails_closed(tmp_path, content):
    path = tmp_path / LAST_DELIVERED_REL
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    with pytest.raises(OperatorBlocked) as exc:
        load_last_delivered(tmp_path)
    assert exc.value.reason_code == "FEEDBACK_TARGET_UNREADABLE"


# --- apply_feedback ----------------------------------------------------------

def test_apply_records_event_on_the_feedback_stream(tmp_path):
    record_delivery("trace_abc", now=NOW, repo_root=tmp_path)
    outcome = apply_feedback("good 핵심을 잘 짚음", operator_id="tg-12345",
                             store=_ledger(tmp_path), now=NOW, repo_root=tmp_path)
    assert outcome["verdict"] == "GOOD"
    assert "trace_abc" in outcome["reply"]
    rows = _feedback_rows(tmp_path)
    assert len(rows) == 1
    event = rows[0]
    assert event["record_type"] == "operator_feedback.v0"
    assert event["trace_id"] == "trace_abc"
    assert event["delivered_at"] == NOW
    assert event["verdict"] == "GOOD"
    assert event["comment"] == "핵심을 잘 짚음"
    assert event["operator_id"] == "tg-12345"
    assert event["integrity"]["event_sha256"]


def test_apply_without_ledger_fails_closed(tmp_path):
    record_delivery("trace_abc", now=NOW, repo_root=tmp_path)
    with pytest.raises(OperatorBlocked) as exc:
        apply_feedback("good", operator_id="tg-1", store=None, now=NOW, repo_root=tmp_path)
    assert exc.value.reason_code == "FEEDBACK_UNAVAILABLE"


def test_apply_empty_payload_fails_closed(tmp_path):
    with pytest.raises(OperatorBlocked) as exc:
        apply_feedback("", operator_id="tg-1", store=_ledger(tmp_path), now=NOW, repo_root=tmp_path)
    assert exc.value.reason_code == "EMPTY_FEEDBACK"


def test_apply_without_delivered_run_fails_closed(tmp_path):
    with pytest.raises(OperatorBlocked) as exc:
        apply_feedback("good", operator_id="tg-1", store=_ledger(tmp_path), now=NOW, repo_root=tmp_path)
    assert exc.value.reason_code == "NO_FEEDBACK_TARGET"


# --- channel wiring (handle_operator_message) --------------------------------

def test_feedback_command_never_reaches_the_pipeline(tmp_path, monkeypatch):
    import runtime.mvp_runtime.operator as operator_mod
    monkeypatch.setattr(operator_mod, "run_task", lambda *a, **k: pytest.fail("run_task must not run"))
    record_delivery("trace_abc", now=NOW, repo_root=tmp_path)
    reply = handle_operator_message(_msg("/feedback bad 근거 부족"), registration=REG,
                                    store=_ledger(tmp_path), now=NOW, repo_root=tmp_path)
    assert reply.accepted is True and reply.status == "FEEDBACK"
    assert reply.reason_code == "FEEDBACK_RECORDED"
    assert _feedback_rows(tmp_path)[0]["verdict"] == "BAD"


def test_feedback_refusal_is_typed_not_a_task(tmp_path, monkeypatch):
    import runtime.mvp_runtime.operator as operator_mod
    monkeypatch.setattr(operator_mod, "run_task", lambda *a, **k: pytest.fail("run_task must not run"))
    reply = handle_operator_message(_msg("/feedback"), registration=REG,
                                    store=_ledger(tmp_path), now=NOW, repo_root=tmp_path)
    assert reply.accepted is False and reply.status == "REFUSED"
    assert reply.reason_code == "EMPTY_FEEDBACK"


def test_feedback_answers_while_killed(tmp_path):
    # Judging already-delivered work is not new execution: like /approve, /feedback is
    # handled before the kill-switch task refusal.
    control_store = control.ControlStore(tmp_path)
    control.apply_command(control_store, "kill", actor="tg-12345", now=NOW)
    record_delivery("trace_abc", now=NOW, repo_root=tmp_path)
    reply = handle_operator_message(_msg("/feedback good"), registration=REG,
                                    store=_ledger(tmp_path), control_store=control_store,
                                    now=NOW, repo_root=tmp_path)
    assert reply.accepted is True and reply.status == "FEEDBACK"
    assert len(_feedback_rows(tmp_path)) == 1


def test_unknown_command_lists_feedback(tmp_path):
    control_store = control.ControlStore(tmp_path)
    reply = handle_operator_message(_msg("/help"), registration=REG,
                                    control_store=control_store, now=NOW, repo_root=tmp_path)
    assert reply.reason_code == "UNKNOWN_COMMAND"
    assert "/feedback" in reply.text


def test_feedback_persistence_failure_is_reported(tmp_path):
    record_delivery("trace_abc", now=NOW, repo_root=tmp_path)

    class _BrokenLedger:
        def append_feedback_event(self, entry):
            raise PersistenceError("LEDGER_WRITE_FAILED", "disk full")

    reply = handle_operator_message(_msg("/feedback good"), registration=REG,
                                    store=_BrokenLedger(), now=NOW, repo_root=tmp_path)
    assert reply.accepted is False and reply.status == "REFUSED"
    assert reply.reason_code == "LEDGER_WRITE_FAILED"


# --- delivery pointer wiring (run_operator_once) -----------------------------

def _completed_reply(**overrides):
    params = dict(text="analysis", accepted=True, status="COMPLETED", trace_id="trace_run1")
    params.update(overrides)
    return OperatorReply(**params)


def test_loop_records_pointer_after_completed_send(tmp_path, monkeypatch):
    import runtime.mvp_runtime.operator as operator_mod
    monkeypatch.setattr(operator_mod, "handle_operator_message", lambda *a, **k: _completed_reply())
    channel = MockOperatorChannel(inbound=[_msg("이 사업 아이디어를 분석해줘: x")])
    summary = run_operator_once(channel, REG, now=NOW, repo_root=tmp_path)
    assert summary["handled"] == 1
    assert load_last_delivered(tmp_path) == {"trace_id": "trace_run1", "delivered_at": NOW}


@pytest.mark.parametrize("reply", [
    _completed_reply(status="BLOCKED", reason_code="PROVIDER_ERROR"),   # not a delivered analysis
    _completed_reply(status="REFUSED", accepted=False, trace_id=None),  # refusals have no run
])
def test_loop_skips_pointer_for_non_completed(tmp_path, monkeypatch, reply):
    import runtime.mvp_runtime.operator as operator_mod
    monkeypatch.setattr(operator_mod, "handle_operator_message", lambda *a, **k: reply)
    channel = MockOperatorChannel(inbound=[_msg("x")])
    run_operator_once(channel, REG, now=NOW, repo_root=tmp_path)
    assert load_last_delivered(tmp_path) is None


def test_loop_skips_pointer_when_send_failed(tmp_path, monkeypatch):
    # An undelivered reply is not something Thomas saw — no pointer, and the failure
    # is already counted by the summary.
    import runtime.mvp_runtime.operator as operator_mod
    monkeypatch.setattr(operator_mod, "handle_operator_message", lambda *a, **k: _completed_reply())

    class _DeadSendChannel(MockOperatorChannel):
        def send(self, chat_id, text):
            raise OperatorBlocked("CHANNEL_TRANSPORT", "send failed")

    channel = _DeadSendChannel(inbound=[_msg("x")])
    summary = run_operator_once(channel, REG, now=NOW, repo_root=tmp_path)
    assert summary["send_failures"] == 1
    assert load_last_delivered(tmp_path) is None


# --- M5a: /feedback bad <note> captures a correction candidate ----------------

def _active(tmp_path):
    """An ACTIVE control store — the M5a memory capture is kill-bound, so a caller that wants
    the candidate minted must prove the runtime is running."""
    return control.ControlStore(tmp_path)


def _wm(tmp_path):
    from runtime.mvp_runtime.working_memory import WorkingMemoryStore
    return WorkingMemoryStore(tmp_path / "wm")


def _memory_rows(tmp_path):
    from runtime.mvp_runtime.store import MEMORY_FILE
    path = tmp_path / "ledger" / MEMORY_FILE
    if not path.is_file():
        return []
    return read_objects(path, read_code="LEDGER_UNREADABLE", label="memory")


def test_feedback_bad_with_note_mints_a_correction_candidate(tmp_path):
    record_delivery("trace_abc", now=NOW, repo_root=tmp_path)
    wm = _wm(tmp_path)
    outcome = apply_feedback("bad 표를 넣어서 다시 정리해줘", operator_id="tg-12345",
                             store=_ledger(tmp_path), working_memory=wm, control_store=_active(tmp_path),
                             now=NOW, repo_root=tmp_path)
    assert outcome["verdict"] == "BAD"
    cand = outcome["learning_candidate"]
    assert cand is not None and cand["status"] == "CANDIDATE" and cand["validated"] is False
    assert cand["learning_source"] == "operator_feedback"
    assert cand["content"] == "표를 넣어서 다시 정리해줘"
    assert cand["correction_ref"] == "trace:trace_abc"
    assert "교정 후보" in outcome["reply"]
    # Stored in working memory and audited on the memory-event stream.
    assert [c["candidate_id"] for c in wm.read_all()] == [cand["candidate_id"]]
    events = [e for e in _memory_rows(tmp_path)
              if e.get("record_type") == "working_memory_learning_event.v0"]
    assert len(events) == 1 and events[0]["candidate_id"] == cand["candidate_id"]
    assert events[0]["operator_id"] == "tg-12345"


def test_feedback_good_mints_no_correction(tmp_path):
    record_delivery("trace_abc", now=NOW, repo_root=tmp_path)
    wm = _wm(tmp_path)
    outcome = apply_feedback("good 훌륭함", operator_id="tg-1",
                             store=_ledger(tmp_path), working_memory=wm, control_store=_active(tmp_path),
                             now=NOW, repo_root=tmp_path)
    assert outcome["learning_candidate"] is None
    assert wm.read_all() == []


def test_feedback_bad_without_note_mints_no_correction(tmp_path):
    """A bare `bad` has no delta to learn — the verdict is recorded, no candidate is minted."""
    record_delivery("trace_abc", now=NOW, repo_root=tmp_path)
    wm = _wm(tmp_path)
    outcome = apply_feedback("bad", operator_id="tg-1",
                             store=_ledger(tmp_path), working_memory=wm, control_store=_active(tmp_path),
                             now=NOW, repo_root=tmp_path)
    assert outcome["verdict"] == "BAD" and outcome["learning_candidate"] is None
    assert wm.read_all() == []
    assert len(_feedback_rows(tmp_path)) == 1     # the verdict itself is still recorded


def test_feedback_without_working_memory_still_records(tmp_path):
    record_delivery("trace_abc", now=NOW, repo_root=tmp_path)
    outcome = apply_feedback("bad 근거 부족", operator_id="tg-1",
                             store=_ledger(tmp_path), now=NOW, repo_root=tmp_path)
    assert outcome["verdict"] == "BAD" and outcome["learning_candidate"] is None
    assert len(_feedback_rows(tmp_path)) == 1


def test_capture_failure_never_fails_the_recorded_feedback(tmp_path):
    """The verdict is durably recorded before capture runs, so a working-memory failure must
    not turn a recorded feedback into an error (best-effort by construction)."""
    from runtime.mvp_runtime.errors import PersistenceError as _PE

    class _BrokenWM:
        def append(self, entries):
            raise _PE("WORKING_MEMORY_WRITE_FAILED", "disk full")

    record_delivery("trace_abc", now=NOW, repo_root=tmp_path)
    outcome = apply_feedback("bad 표 필요", operator_id="tg-1",
                             store=_ledger(tmp_path), working_memory=_BrokenWM(),
                             control_store=_active(tmp_path), now=NOW, repo_root=tmp_path)
    assert outcome["verdict"] == "BAD" and outcome["learning_candidate"] is None
    assert len(_feedback_rows(tmp_path)) == 1     # feedback recorded despite the capture failure


# --- the 2026-07-25 control-channel review: memory mutation is kill-bound ------

def test_a_killed_runtime_records_the_verdict_but_mints_no_memory(tmp_path):
    """The verdict is deliberately answered in any mode — judging delivered work is not new
    execution. Minting a correction candidate MUTATES working memory, and memory_console gates
    exactly that on the kill switch. This path did not, so a KILLED runtime still wrote memory."""
    record_delivery("trace_abc", now=NOW, repo_root=tmp_path)
    wm = _wm(tmp_path)
    killed = control.ControlStore(tmp_path)
    control.apply_command(killed, "kill", actor="tg-1", now=NOW)

    outcome = apply_feedback("bad 표를 넣어줘", operator_id="tg-1", store=_ledger(tmp_path),
                             working_memory=wm, control_store=killed, now=NOW, repo_root=tmp_path)
    assert outcome["verdict"] == "BAD"                 # the verdict IS recorded
    assert len(_feedback_rows(tmp_path)) == 1
    assert outcome["learning_candidate"] is None       # ...but memory is not mutated
    assert wm.read_all() == []


def test_a_paused_runtime_also_mints_no_memory(tmp_path):
    record_delivery("trace_abc", now=NOW, repo_root=tmp_path)
    wm = _wm(tmp_path)
    paused = control.ControlStore(tmp_path)
    control.apply_command(paused, "pause", actor="tg-1", now=NOW)
    outcome = apply_feedback("bad 표를 넣어줘", operator_id="tg-1", store=_ledger(tmp_path),
                             working_memory=wm, control_store=paused, now=NOW, repo_root=tmp_path)
    assert outcome["learning_candidate"] is None and wm.read_all() == []


def test_no_control_store_means_no_capture(tmp_path):
    """Fail-closed on a missing safety store, matching memory_console/registry_console: the
    verdict is safe either way, so refusing to mutate costs nothing."""
    record_delivery("trace_abc", now=NOW, repo_root=tmp_path)
    wm = _wm(tmp_path)
    outcome = apply_feedback("bad 표를 넣어줘", operator_id="tg-1", store=_ledger(tmp_path),
                             working_memory=wm, control_store=None, now=NOW, repo_root=tmp_path)
    assert outcome["verdict"] == "BAD" and len(_feedback_rows(tmp_path)) == 1
    assert outcome["learning_candidate"] is None and wm.read_all() == []
