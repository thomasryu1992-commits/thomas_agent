"""Task-console tests — /tasks /history /result /cancel over the control channel.

The coordination verbs. What must hold: the read verbs answer in any runtime mode but never
*lie* (an unreadable registry says so instead of showing an empty list), /result re-renders
from the ledger rather than a second copy of the analysis, /cancel is kill-switch bound and
refuses to duplicate the kill switch's authority over in-flight work, and a run recorded
through the operator channel only becomes DELIVERED once it actually reached Thomas.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import control, registry_console, task_registry
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import OperatorBlocked
from runtime.mvp_runtime.operator import (
    InboundMessage,
    MockOperatorChannel,
    OperatorIdentity,
    handle_operator_message,
    run_operator_once,
)
from runtime.mvp_runtime.store import LedgerStore
from runtime.mvp_runtime.task_registry import (
    BLOCKED,
    DELIVERED,
    QUEUED,
    RUNNING,
    TaskRegistryStore,
    build_entry,
)

NOW = "2026-07-25T09:00:00Z"
LATER = "2026-07-25T09:07:00Z"
REG = OperatorIdentity(operator_id="tg-12345", chat_id="chat-777")


def _registry(tmp_path) -> TaskRegistryStore:
    return TaskRegistryStore(tmp_path)


def _entry(store, *, text="구독형 세차 사업 아이디어 분석", status=RUNNING, now=NOW, origin="TELEGRAM"):
    return store.submit(build_entry(
        request_text=text, origin=origin, requester_id="tg-12345", now=now, status=status,
    ))


def _apply(command, **kwargs):
    kwargs.setdefault("operator_id", "tg-12345")
    kwargs.setdefault("now", LATER)
    return registry_console.apply_registry_command(command, **kwargs)


def _msg(text):
    return InboundMessage(text=text, sender_id="tg-12345", chat_id="chat-777")


# --- parsing -----------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("/tasks", ("TASKS", None)),
    ("tasks", ("TASKS", None)),
    ("/tasks@thomas_bot", ("TASKS", None)),          # Telegram menu suffix is addressing
    ("/history", ("HISTORY", None)),
    ("/history 25", ("HISTORY", "25")),
    ("/result treg_abc", ("RESULT", "treg_abc")),
    ("/cancel treg_abc", ("CANCEL", "treg_abc")),
])
def test_parse_registry_command(text, expected):
    assert registry_console.parse_registry_command(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "/status", "분석해줘", None, "/taskss"])
def test_non_registry_text_is_not_a_registry_command(text):
    assert registry_console.parse_registry_command(text) is None


# --- /tasks ------------------------------------------------------------------

def test_tasks_lists_open_entries_running_first(tmp_path):
    store = _registry(tmp_path)
    queued = _entry(store, text="두 번째 요청", status=QUEUED, now=LATER)
    running = _entry(store, text="첫 번째 요청", status=RUNNING, now=NOW)

    outcome = _apply(("TASKS", None), registry=store)

    assert outcome["action"] == "TASKS_LISTED" and outcome["count"] == 2
    reply = outcome["reply"]
    assert reply.index(running.registry_entry_id[:12]) < reply.index(queued.registry_entry_id[:12])
    assert "7분 경과" in reply          # elapsed, not an invented percentage
    assert "%" not in reply


def test_tasks_on_an_empty_registry_says_so(tmp_path):
    outcome = _apply(("TASKS", None), registry=_registry(tmp_path))
    assert outcome["count"] == 0 and "없습니다" in outcome["reply"]


def test_finished_entries_do_not_appear_in_tasks(tmp_path):
    store = _registry(tmp_path)
    entry = _entry(store)
    store.transition(entry.registry_entry_id, DELIVERED, now=LATER)
    assert _apply(("TASKS", None), registry=store)["count"] == 0


def test_unreadable_registry_refuses_rather_than_showing_nothing(tmp_path):
    """An empty list would read as 'nothing is running' — the one answer that must not be
    given when the registry cannot actually be read."""
    store = _registry(tmp_path)
    _entry(store)
    store.path.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(OperatorBlocked) as exc:
        _apply(("TASKS", None), registry=store)
    assert exc.value.reason_code == "REGISTRY_UNREADABLE"


def test_no_registry_wired_is_a_typed_refusal():
    with pytest.raises(OperatorBlocked) as exc:
        _apply(("TASKS", None), registry=None)
    assert exc.value.reason_code == "REGISTRY_UNAVAILABLE"


# --- /history ----------------------------------------------------------------

def test_history_lists_finished_entries_with_their_reason(tmp_path):
    store = _registry(tmp_path)
    done = _entry(store, text="완료된 작업")
    store.transition(done.registry_entry_id, DELIVERED, now=LATER)
    failed = _entry(store, text="차단된 작업", now=LATER)
    store.transition(failed.registry_entry_id, BLOCKED, now=LATER, reason_code="PROVIDER_ERROR")

    outcome = _apply(("HISTORY", None), registry=store)

    assert outcome["action"] == "HISTORY_LISTED" and outcome["count"] == 2
    assert "PROVIDER_ERROR" in outcome["reply"]


def test_history_respects_and_caps_its_limit(tmp_path):
    store = _registry(tmp_path)
    for index in range(4):
        entry = _entry(store, text=f"작업 {index}", now=f"2026-07-25T09:0{index}:00Z")
        store.transition(entry.registry_entry_id, DELIVERED, now=LATER)
    assert _apply(("HISTORY", "2"), registry=store)["reply"].count("• ") == 2
    # Out-of-range values clamp rather than refuse; the count reported is the true total.
    clamped = _apply(("HISTORY", "999"), registry=store)
    assert clamped["count"] == 4
    # ...and the clamp is stated: 999 was asked for, 50 was shown.
    assert "999" in clamped["reply"] and "50" in clamped["reply"]


def test_history_with_a_non_numeric_argument_answers_and_says_it_defaulted(tmp_path):
    """A mistyped count on a read-only verb never denies the read (the `/audit` rule, now
    shared), but the substituted number is never silent either."""
    store = _registry(tmp_path)
    entry = _entry(store, text="작업", now="2026-07-25T09:00:00Z")
    store.transition(entry.registry_entry_id, DELIVERED, now=LATER)
    outcome = _apply(("HISTORY", "많이"), registry=store)
    assert outcome["action"] == "HISTORY_LISTED"
    assert "많이" in outcome["reply"] and "10" in outcome["reply"]


# --- /result -----------------------------------------------------------------

_AGENT_OUTPUT = {
    "goal": "구독형 세차 사업성 분석",
    "summary": "월 구독 모델은 재방문율에 좌우된다.",
    "role_specific_output": {"key_findings": ["세차 빈도가 핵심 변수"]},
}


def _ledger_with_run(tmp_path, trace_id, *, records=None):
    ledger = LedgerStore(tmp_path / "ledger")
    ledger.append_records(trace_id, records or {"agent_output": _AGENT_OUTPUT})
    return ledger


def test_result_rerenders_from_the_ledger(tmp_path):
    store = _registry(tmp_path)
    entry = _entry(store)
    store.transition(entry.registry_entry_id, DELIVERED, now=LATER, trace_id="trace_r1")
    ledger = _ledger_with_run(tmp_path, "trace_r1")

    outcome = _apply(("RESULT", entry.registry_entry_id), registry=store, ledger=ledger)

    assert outcome["action"] == "RESULT_RESENT"
    assert "구독형 세차 사업성 분석" in outcome["reply"]
    assert "세차 빈도가 핵심 변수" in outcome["reply"]
    # The registry stores no copy of the analysis — only the pointer to it.
    assert "월 구독 모델" not in json.dumps(
        [json.loads(l) for l in store.path.read_text(encoding="utf-8").splitlines()], ensure_ascii=False)


def test_result_renders_the_sources_its_citations_point_at(tmp_path):
    store = _registry(tmp_path)
    entry = _entry(store)
    store.transition(entry.registry_entry_id, DELIVERED, now=LATER, trace_id="trace_r2")
    ledger = _ledger_with_run(tmp_path, "trace_r2", records={
        "agent_output": _AGENT_OUTPUT,
        "tool_use": {"hits": [{"title": "세차 시장", "url": "https://example.test/a",
                               "snippet": "요약", "source": "example.test"}]},
    })
    assert "https://example.test/a" in _apply(
        ("RESULT", entry.registry_entry_id), registry=store, ledger=ledger)["reply"]


def test_result_on_an_unfinished_entry_is_refused(tmp_path):
    store = _registry(tmp_path)
    entry = _entry(store)
    with pytest.raises(OperatorBlocked) as exc:
        _apply(("RESULT", entry.registry_entry_id), registry=store)
    assert exc.value.reason_code == "TASK_NOT_FINISHED"


def test_result_on_a_blocked_entry_says_there_is_no_deliverable(tmp_path):
    store = _registry(tmp_path)
    entry = _entry(store)
    store.transition(entry.registry_entry_id, BLOCKED, now=LATER, reason_code="VALIDATION_BLOCK")
    with pytest.raises(OperatorBlocked) as exc:
        _apply(("RESULT", entry.registry_entry_id), registry=store)
    assert exc.value.reason_code == "NO_DELIVERABLE"
    assert "VALIDATION_BLOCK" in exc.value.reason


def test_result_admits_when_the_ledger_cannot_rebuild_it(tmp_path):
    """DELIVERED but unrenderable (pruned, another machine) must refuse, never half-render."""
    store = _registry(tmp_path)
    entry = _entry(store)
    store.transition(entry.registry_entry_id, DELIVERED, now=LATER, trace_id="trace_gone")
    with pytest.raises(OperatorBlocked) as exc:
        _apply(("RESULT", entry.registry_entry_id), registry=store,
               ledger=LedgerStore(tmp_path / "ledger"))
    assert exc.value.reason_code == "RESULT_UNAVAILABLE"


def test_result_without_an_id_is_a_usage_refusal(tmp_path):
    with pytest.raises(OperatorBlocked) as exc:
        _apply(("RESULT", None), registry=_registry(tmp_path))
    assert exc.value.reason_code == "USAGE"


def test_unknown_id_is_refused(tmp_path):
    with pytest.raises(OperatorBlocked) as exc:
        _apply(("RESULT", "treg_nothere"), registry=_registry(tmp_path))
    assert exc.value.reason_code == "ENTRY_NOT_FOUND"


# --- /cancel -----------------------------------------------------------------

def _control(tmp_path, mode=control.ACTIVE):
    store = ControlStore(tmp_path / "control")
    if mode != control.ACTIVE:
        store.save(control.ControlState(mode=mode, updated_by="tg-12345", updated_at=NOW,
                                        reason="test"))
    return store


def test_cancel_a_queued_entry(tmp_path):
    store = _registry(tmp_path)
    entry = _entry(store, status=QUEUED)
    ledger = LedgerStore(tmp_path / "ledger")

    outcome = _apply(("CANCEL", entry.registry_entry_id), registry=store, ledger=ledger,
                     control_store=_control(tmp_path))

    assert outcome["action"] == "TASK_CANCELLED"
    cancelled = store.find(entry.registry_entry_id)
    assert cancelled.status == task_registry.CANCELLED
    assert cancelled.last_reason_code == "OPERATOR_CANCELLED"
    # A verb that changed something records an event (unlike the read verbs).
    assert any(b.get("record_type") == registry_console.REGISTRY_EVENT_TYPE
               for b in ledger.read_blocks())


def test_cancel_does_not_duplicate_the_kill_switch(tmp_path):
    store = _registry(tmp_path)
    entry = _entry(store, status=RUNNING)
    with pytest.raises(OperatorBlocked) as exc:
        _apply(("CANCEL", entry.registry_entry_id), registry=store,
               control_store=_control(tmp_path))
    assert exc.value.reason_code == "TASK_ALREADY_RUNNING"
    assert "/kill" in exc.value.reason


@pytest.mark.parametrize("mode, code", [
    (control.PAUSED, "RUNTIME_PAUSED"),
    (control.KILLED, "RUNTIME_KILLED"),
])
def test_cancel_is_kill_switch_bound(tmp_path, mode, code):
    store = _registry(tmp_path)
    entry = _entry(store, status=QUEUED)
    with pytest.raises(OperatorBlocked) as exc:
        _apply(("CANCEL", entry.registry_entry_id), registry=store,
               control_store=_control(tmp_path, mode))
    assert exc.value.reason_code == code
    assert store.find(entry.registry_entry_id).status == QUEUED


def test_cancel_without_control_state_fails_closed(tmp_path):
    store = _registry(tmp_path)
    entry = _entry(store, status=QUEUED)
    with pytest.raises(OperatorBlocked) as exc:
        _apply(("CANCEL", entry.registry_entry_id), registry=store, control_store=None)
    assert exc.value.reason_code == "KILL_STATE_UNAVAILABLE"


def test_cancel_a_finished_entry_is_refused(tmp_path):
    store = _registry(tmp_path)
    entry = _entry(store)
    store.transition(entry.registry_entry_id, DELIVERED, now=LATER)
    with pytest.raises(OperatorBlocked) as exc:
        _apply(("CANCEL", entry.registry_entry_id), registry=store,
               control_store=_control(tmp_path))
    assert exc.value.reason_code == "TASK_ALREADY_FINISHED"


def test_a_cancel_that_loses_the_race_says_what_actually_happened(tmp_path):
    """The status check is unlocked and `transition` re-checks under the store lock, so a task
    that starts running while the operator types lands in the except branch. It answered
    "취소를 기록하지 못했습니다: ... is not a legal transition", which describes a storage
    failure — the truthful answer is the one the pre-check would have given a moment later."""
    store = _registry(tmp_path)
    entry = _entry(store, status=QUEUED)
    real_transition = store.transition

    def racing_transition(entry_id, target, **kw):
        # The drain claims it between the pre-check and the write.
        store.transition = real_transition
        real_transition(entry_id, RUNNING, now=LATER)
        return real_transition(entry_id, target, **kw)
    store.transition = racing_transition

    with pytest.raises(OperatorBlocked) as exc:
        _apply(("CANCEL", entry.registry_entry_id), registry=store,
               control_store=_control(tmp_path))
    assert exc.value.reason_code == "TASK_ALREADY_RUNNING"
    assert "/kill" in exc.value.reason
    assert "기록하지 못했습니다" not in exc.value.reason
    assert store.find(entry.registry_entry_id).status == RUNNING


def test_one_authority_answers_why_a_cancel_is_refused(tmp_path):
    """The verb, its lost-race path and the front desk's proposal all ask the same helper, so
    the conversational door cannot give a different reason than /cancel does."""
    store = _registry(tmp_path)
    running = _entry(store, status=RUNNING)
    code, message = registry_console.cancel_refusal(store.find(running.registry_entry_id))
    assert code == "TASK_ALREADY_RUNNING" and "/kill" in message
    assert registry_console.cancel_refusal(_entry(store, now=LATER, status=QUEUED)) is None


# --- read verbs answer while the runtime is down -----------------------------

@pytest.mark.parametrize("mode", [control.PAUSED, control.KILLED])
@pytest.mark.parametrize("verb", ["/tasks", "/history"])
def test_read_verbs_answer_in_any_runtime_mode(tmp_path, mode, verb):
    """An operator facing a PAUSED runtime most needs to see what it was doing."""
    store = _registry(tmp_path)
    _entry(store)
    reply = handle_operator_message(
        _msg(verb), registration=REG, registry=store,
        control_store=_control(tmp_path, mode), now=LATER, repo_root=tmp_path,
    )
    assert reply.accepted and reply.status == "REGISTRY"


# --- operator-channel integration --------------------------------------------

def test_registry_verbs_are_listed_in_the_unknown_command_help(tmp_path):
    reply = handle_operator_message(_msg("/nope"), registration=REG, repo_root=tmp_path)
    assert reply.reason_code == "UNKNOWN_COMMAND"
    for verb in ("/tasks", "/history", "/result", "/cancel"):
        assert verb in reply.text


def _stub_completed_run(monkeypatch):
    """Stand in for the pipeline (as the other operator tests do) so these assert the
    coordination bookkeeping, not the analysis."""
    monkeypatch.setattr(
        "runtime.mvp_runtime.operator.run_task",
        lambda *a, **k: {
            "status": "COMPLETED", "final_response": "분석 결과",
            "records": {"received_task": {"identity": {"task_id": "task_x1",
                                                       "trace_id": "trace_x1"}}},
        },
    )


def test_a_completed_run_is_delivered_only_after_it_is_sent(tmp_path, monkeypatch):
    """DELIVERED must mean the analysis reached Thomas, not that it was produced."""
    _stub_completed_run(monkeypatch)
    store = _registry(tmp_path)
    channel = MockOperatorChannel(inbound=[_msg("이 사업 아이디어를 분석해줘: 구독형 세차")])

    run_operator_once(channel, REG, registry=store, repo_root=tmp_path)

    entries = store.latest()
    assert len(entries) == 1
    assert entries[0].status == DELIVERED
    assert entries[0].origin == "TELEGRAM"
    assert entries[0].result_ref == "ledger:trace_x1"


def test_a_failed_send_is_not_recorded_as_delivered(tmp_path, monkeypatch):
    _stub_completed_run(monkeypatch)

    class _DeadChannel(MockOperatorChannel):
        def send(self, chat_id, text):
            raise OperatorBlocked("CHANNEL_TRANSPORT", "telegram is down")

    store = _registry(tmp_path)
    channel = _DeadChannel(inbound=[_msg("이 사업 아이디어를 분석해줘: 구독형 세차")])

    summary = run_operator_once(channel, REG, registry=store, repo_root=tmp_path)

    # Both sends failed — the queue acknowledgement and the result — and both are counted.
    assert summary["send_failures"] == 2
    entry = store.latest()[0]
    assert entry.status == task_registry.FAILED
    assert entry.last_reason_code == "SEND_FAILED"


def test_a_blocked_run_is_closed_by_the_drain(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "runtime.mvp_runtime.operator.run_task",
        lambda *a, **k: {"status": "BLOCKED", "block": {"reason_code": "PROVIDER_ERROR"},
                         "records": {}},
    )
    store = _registry(tmp_path)
    channel = MockOperatorChannel(inbound=[_msg("분석해줘: 아이디어")])

    run_operator_once(channel, REG, registry=store, repo_root=tmp_path)

    entry = store.latest()[0]
    assert entry.status == BLOCKED and entry.last_reason_code == "PROVIDER_ERROR"
    # The block's reply still reaches Thomas, on the same channel.
    assert any("PROVIDER_ERROR" in text for _chat, text in channel.sent)


def test_console_commands_do_not_create_registry_entries(tmp_path):
    """Only actual work is coordinated work — /status is not a task."""
    store = _registry(tmp_path)
    handle_operator_message(_msg("/status"), registration=REG, registry=store,
                            control_store=_control(tmp_path), now=NOW, repo_root=tmp_path)
    assert store.latest() == []


# --- the assistant's entries among the operator's (door API v2) --------------------

def test_an_agent_entry_is_marked_as_the_assistants_in_both_listings(tmp_path):
    store = _registry(tmp_path)
    mine = _entry(store, text="내 요청", status=RUNNING, now=NOW)
    theirs = _entry(store, text="비서가 맡긴 분석", status=RUNNING, now=NOW, origin="AGENT")
    reply = _apply(("TASKS", None), registry=store)["reply"]
    assert "[실행 중 · 비서]" in reply and reply.count("• ") == 2
    assert "[실행 중]" in reply                      # the operator's own line carries no mark
    store.transition(theirs.registry_entry_id, DELIVERED, now=LATER, task_id="task_a1", trace_id="trace_a1")
    history = _apply(("HISTORY", None), registry=store)["reply"]
    assert "[완료 · 비서]" in history
    assert mine.registry_entry_id[:12] in reply


def test_result_resolves_a_dispatch_replys_task_id(tmp_path):
    """The dispatch reply hands the assistant `task_id`; `/result task_…` must reach the entry
    without a second lookup. RUNNING here, so the refusal is TASK_NOT_FINISHED — which proves
    the id resolved (an unknown id is ENTRY_NOT_FOUND)."""
    store = _registry(tmp_path)
    entry = _entry(store, text="비서가 맡긴 분석", status=RUNNING, now=NOW, origin="AGENT")
    store.transition(entry.registry_entry_id, DELIVERED, now=LATER, task_id="task_a1", trace_id="trace_a1")
    again = _entry(store, text="두 번째", status=RUNNING, now=LATER, origin="AGENT")
    with pytest.raises(OperatorBlocked) as exc:
        _apply(("RESULT", "trace_nope"), registry=store)
    assert exc.value.reason_code == "ENTRY_NOT_FOUND"
    # A delivered AGENT entry found by its task id proceeds to the ledger re-render step.
    with pytest.raises(OperatorBlocked) as exc:
        _apply(("RESULT", "task_a1"), registry=store, ledger=None)
    assert exc.value.reason_code == "RESULT_UNAVAILABLE"
    assert again.status == RUNNING
