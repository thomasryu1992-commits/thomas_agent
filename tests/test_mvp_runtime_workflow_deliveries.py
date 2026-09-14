"""The operator's push of workflow core events and the `deliveries` it records (sequence 2,
P08; V0.2 Q23; acceptance A19).

The operator is the one pusher: a workflow's arrival at COMPLETED / FAILED / BLOCKED /
CANCELLED / WAITING_REPLAN goes to the registered control chat once, with the delivery
recorded before and after the send. Step and attempt events are not pushed (Hermes narrates
them by polling); a workflow waiting for an approval is not pushed here either — the ask
itself is that push. The cursor lives in the store, so a restarted operator resumes where the
previous one stopped, and an unchanged store yields silence.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import workflow as wf
from runtime.mvp_runtime.errors import OperatorBlocked, PersistenceError
from runtime.mvp_runtime.operator import MockOperatorChannel, PUSH_CHANNEL, push_workflow_events
from runtime.mvp_runtime.workflow_store import WorkflowStore

NOW = "2026-09-14T12:00:00Z"
LATER = "2026-09-14T12:05:00Z"


def _register(tmp_path, chat_id="chat-registered"):
    state = tmp_path / ".runtime_governance_state"
    state.mkdir(exist_ok=True)
    (state / "operator_registration.json").write_text(
        json.dumps({"operator_id": "tg-1", "chat_id": chat_id, "approver": "Thomas"}), encoding="utf-8")


def _plan(goal="시장 조사", *, max_attempts=2, gated=False):
    step = {"id": "research", "capability": "research", "request": "조사", "reason": "r", "max_attempts": max_attempts}
    if gated:
        step["requires_approval"] = True
    return {"schema_version": "workflow_plan.v0.1", "goal": goal, "steps": [step], "budget": {"max_model_calls": 4}}


def _complete(store, request_id, goal="시장 조사"):
    wid = store.submit(principal="hermes", request_id=request_id, plan=_plan(goal), now=NOW).workflow_id
    (att,) = store.claim_ready(now=NOW)
    store.record_result(att.attempt_id, now=NOW, succeeded=True, result_ref=f"ledger:trace_{request_id}", model_calls=1)
    return wid


@pytest.fixture
def ready(tmp_path):
    """A registered chat, a store with one completed workflow already in it, and the first pass
    done — the pass that adopts the backlog and sends nothing."""
    _register(tmp_path)
    store = WorkflowStore(tmp_path)
    _complete(store, "hermes-0", goal="배경 작업")
    ch = MockOperatorChannel()
    first = push_workflow_events(ch, store, now=NOW, repo_root=tmp_path)
    assert first["adopted"] and first["sent"] == [] and ch.sent == []
    return tmp_path, store, ch


# --- the first pass, and silence ------------------------------------------------------------------

def test_the_first_pass_adopts_the_backlog_and_sends_nothing(tmp_path):
    _register(tmp_path)
    store = WorkflowStore(tmp_path)
    _complete(store, "hermes-0")
    ch = MockOperatorChannel()
    report = push_workflow_events(ch, store, now=NOW, repo_root=tmp_path)
    tail = store.max_event_cursor()
    assert report == {"sent": [], "uncertain": [], "retried": 0, "cursor": tail, "adopted": True}
    assert ch.sent == [] and store.delivery_cursor(PUSH_CHANNEL) == tail
    (row,) = store.deliveries(PUSH_CHANNEL)
    assert row["status"] == wf.DELIVERY_SKIPPED and row["event_cursor"] == tail
    # nothing changed: the next pass is silent and moves nothing
    again = push_workflow_events(ch, store, now=LATER, repo_root=tmp_path)
    assert again["sent"] == [] and not again["adopted"] and again["cursor"] == tail and ch.sent == []


def test_an_empty_store_adopts_cursor_zero_without_a_delivery_row(tmp_path):
    _register(tmp_path)
    store = WorkflowStore(tmp_path)
    report = push_workflow_events(MockOperatorChannel(), store, now=NOW, repo_root=tmp_path)
    assert report["adopted"] and report["cursor"] == 0 and store.deliveries(PUSH_CHANNEL) == []


# --- what is pushed, once ---------------------------------------------------------------------------

def test_a_completed_workflow_is_pushed_once_and_the_cursor_passes_its_step_events(ready):
    tmp_path, store, ch = ready
    wid = _complete(store, "hermes-1", goal="경쟁사 조사")
    report = push_workflow_events(ch, store, now=LATER, repo_root=tmp_path)
    assert len(report["sent"]) == 1 and report["uncertain"] == []
    (chat_id, text) = ch.sent[0]
    assert chat_id == "chat-registered" and "✅ 완료" in text and wid in text and "경쟁사 조사" in text
    assert "1/1 성공" in text and f"event #{report['sent'][0]}" in text
    assert store.delivery_cursor(PUSH_CHANNEL) == store.max_event_cursor()        # past the step events too
    rows = {r["event_cursor"]: r["status"] for r in store.deliveries(PUSH_CHANNEL)}
    assert rows[report["sent"][0]] == wf.DELIVERY_CONFIRMED
    # the same events are not pushed twice, and an unchanged store is silence
    assert push_workflow_events(ch, store, now=LATER, repo_root=tmp_path)["sent"] == []
    assert len(ch.sent) == 1


def test_a_workflow_waiting_for_a_decision_is_pushed_with_the_step_and_its_reason(ready):
    tmp_path, store, ch = ready
    wid = store.submit(principal="hermes", request_id="hermes-1", plan=_plan(max_attempts=1), now=NOW).workflow_id
    (att,) = store.claim_ready(now=NOW)
    store.record_result(att.attempt_id, now=NOW, succeeded=False, reason_code="PROVIDER_UNAVAILABLE")
    assert store.status_view(wid, now=NOW)["status"] == wf.W_WAITING_REPLAN
    report = push_workflow_events(ch, store, now=LATER, repo_root=tmp_path)
    assert len(report["sent"]) == 1
    text = ch.sent[0][1]
    assert "⏸ 결정 대기" in text and "research 실패 [PROVIDER_UNAVAILABLE]" in text
    assert "retry_workflow_step" in text and "/approve" not in text
    # the decision (a retry) puts it back to RUNNING — not pushed; its completion is
    view = store.status_view(wid, now=NOW)
    store.retry_step(wid, "research", expected_version=view["row_version"], reason="복구", now=LATER)
    assert push_workflow_events(ch, store, now=LATER, repo_root=tmp_path)["sent"] == []
    (att2,) = store.claim_ready(now=LATER)
    store.record_result(att2.attempt_id, now=LATER, succeeded=True, result_ref="ledger:trace_2", model_calls=1)
    report = push_workflow_events(ch, store, now=LATER, repo_root=tmp_path)
    assert len(report["sent"]) == 1 and "✅ 완료" in ch.sent[1][1] and len(ch.sent) == 2


def test_a_cancel_is_pushed_when_it_is_real_not_when_it_is_requested(ready):
    tmp_path, store, ch = ready
    wid = store.submit(principal="hermes", request_id="hermes-1", plan=_plan(), now=NOW).workflow_id
    (att,) = store.claim_ready(now=NOW)
    view = store.status_view(wid, now=NOW)
    view = store.request_cancel(wid, expected_version=view["row_version"], reason="필요 없어짐", now=NOW)
    assert view["status"] == wf.W_CANCELLING
    assert push_workflow_events(ch, store, now=LATER, repo_root=tmp_path)["sent"] == []        # CANCELLING: not yet
    store.confirm_cancelled(att.attempt_id, now=LATER)
    report = push_workflow_events(ch, store, now=LATER, repo_root=tmp_path)
    assert len(report["sent"]) == 1 and "⏹ 취소됨" in ch.sent[0][1] and "필요 없어짐" in ch.sent[0][1]


def test_a_workflow_waiting_for_an_approval_is_not_pushed_here(ready):
    """The ask is the push for that state (`announce_pending_approvals`); one event, one pusher."""
    tmp_path, store, ch = ready
    store.submit(principal="hermes", request_id="hermes-1", plan=_plan(gated=True), now=NOW)
    report = push_workflow_events(ch, store, now=LATER, repo_root=tmp_path)
    assert report["sent"] == [] and ch.sent == []
    assert store.delivery_cursor(PUSH_CHANNEL) == store.max_event_cursor()        # read and passed, not pushed


def test_step_and_attempt_events_are_never_pushed(ready):
    tmp_path, store, ch = ready
    wid = store.submit(principal="hermes", request_id="hermes-1", plan=_plan(), now=NOW).workflow_id
    (att,) = store.claim_ready(now=NOW)
    store.record_result(att.attempt_id, now=NOW, succeeded=False, reason_code="PROVIDER_UNAVAILABLE")   # RETRY_WAIT → READY
    assert store.status_view(wid, now=NOW)["status"] == wf.W_RUNNING
    events, _ = store.events_after(0, limit=500)
    assert sum(1 for e in events if e["entity"] != "workflow") > 3
    assert push_workflow_events(ch, store, now=LATER, repo_root=tmp_path)["sent"] == [] and ch.sent == []


# --- delivery state: PENDING → CONFIRMED | UNCERTAIN -----------------------------------------------------

class _RefusingChannel(MockOperatorChannel):
    def __init__(self, fail_times=1):
        super().__init__()
        self.fail_times = fail_times

    def send(self, chat_id, text):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise OperatorBlocked("CHANNEL_TRANSPORT", "telegram: connection reset")
        return super().send(chat_id, text)


def test_a_failed_send_is_recorded_uncertain_and_never_re_sent(tmp_path):
    _register(tmp_path)
    store = WorkflowStore(tmp_path)
    push_workflow_events(MockOperatorChannel(), store, now=NOW, repo_root=tmp_path)          # adopt (empty)
    _complete(store, "hermes-1")
    ch = _RefusingChannel(fail_times=1)
    report = push_workflow_events(ch, store, now=LATER, repo_root=tmp_path)
    assert report["sent"] == [] and len(report["uncertain"]) == 1 and ch.sent == []
    (row,) = [r for r in store.deliveries(PUSH_CHANNEL) if r["status"] != wf.DELIVERY_SKIPPED]
    assert row["status"] == wf.DELIVERY_UNCERTAIN and "CHANNEL_TRANSPORT" in row["detail"]
    assert store.delivery_cursor(PUSH_CHANNEL) == store.max_event_cursor()        # the cursor moved on
    # the channel works again: the uncertain one is NOT re-sent (it may have arrived); new ones are
    _complete(store, "hermes-2")
    report = push_workflow_events(ch, store, now=LATER, repo_root=tmp_path)
    assert len(report["sent"]) == 1 and len(ch.sent) == 1 and "hermes-2" not in ch.sent[0][1]
    assert [r["status"] for r in store.deliveries(PUSH_CHANNEL)] == [wf.DELIVERY_UNCERTAIN, wf.DELIVERY_CONFIRMED]


def test_a_pending_row_left_by_a_crash_is_retried_once_then_settled(tmp_path):
    _register(tmp_path)
    store = WorkflowStore(tmp_path)
    push_workflow_events(MockOperatorChannel(), store, now=NOW, repo_root=tmp_path)
    _complete(store, "hermes-1")
    events, tail = store.events_after(0, limit=500)
    done = next(e for e in events if e["entity"] == "workflow" and e["to_status"] == wf.W_COMPLETED)
    # the previous process recorded PENDING and died before the send was confirmed
    store.record_delivery(PUSH_CHANNEL, done["cursor"], wf.DELIVERY_PENDING, now=NOW)
    store.set_delivery_cursor(PUSH_CHANNEL, tail, now=NOW)
    ch = MockOperatorChannel()
    report = push_workflow_events(ch, store, now=LATER, repo_root=tmp_path)
    assert report["retried"] == 1 and report["sent"] == [done["cursor"]] and len(ch.sent) == 1
    assert store.deliveries(PUSH_CHANNEL, status=wf.DELIVERY_PENDING) == []
    # a second crash on the retry: the retry's failure settles it UNCERTAIN, and it is not retried again
    _complete(store, "hermes-2")
    events, tail = store.events_after(0, limit=500)
    done2 = [e for e in events if e["entity"] == "workflow" and e["to_status"] == wf.W_COMPLETED][-1]
    store.record_delivery(PUSH_CHANNEL, done2["cursor"], wf.DELIVERY_PENDING, now=NOW)
    store.set_delivery_cursor(PUSH_CHANNEL, tail, now=NOW)
    failing = _RefusingChannel(fail_times=5)
    report = push_workflow_events(failing, store, now=LATER, repo_root=tmp_path)
    assert report["retried"] == 1 and report["uncertain"] == [done2["cursor"]]
    assert push_workflow_events(failing, store, now=LATER, repo_root=tmp_path)["retried"] == 0


def test_a_restarted_operator_resumes_from_the_stored_cursor(ready):
    tmp_path, store, ch = ready
    _complete(store, "hermes-1")
    push_workflow_events(ch, store, now=LATER, repo_root=tmp_path)
    assert len(ch.sent) == 1
    fresh_store, fresh_channel = WorkflowStore(tmp_path), MockOperatorChannel()          # a new process
    report = push_workflow_events(fresh_channel, fresh_store, now=LATER, repo_root=tmp_path)
    assert report["sent"] == [] and not report["adopted"] and fresh_channel.sent == []
    _complete(fresh_store, "hermes-2")
    assert len(push_workflow_events(fresh_channel, fresh_store, now=LATER, repo_root=tmp_path)["sent"]) == 1


def test_the_per_pass_cap_leaves_the_rest_for_the_next_pass_and_loses_none(ready):
    tmp_path, store, ch = ready
    wids = [_complete(store, f"hermes-{i}", goal=f"작업 {i}") for i in range(1, 6)]
    first = push_workflow_events(ch, store, now=LATER, repo_root=tmp_path, limit=2)
    assert len(first["sent"]) == 2 and len(ch.sent) == 2
    assert store.delivery_cursor(PUSH_CHANNEL) < store.max_event_cursor()
    second = push_workflow_events(ch, store, now=LATER, repo_root=tmp_path, limit=2)
    third = push_workflow_events(ch, store, now=LATER, repo_root=tmp_path, limit=2)
    assert len(second["sent"]) == 2 and len(third["sent"]) == 1 and len(ch.sent) == 5
    assert [w for w in wids if any(w in text for _, text in ch.sent)] == wids
    assert push_workflow_events(ch, store, now=LATER, repo_root=tmp_path, limit=2)["sent"] == []


def test_a_store_that_cannot_be_read_raises_for_the_caller_to_report(tmp_path):
    _register(tmp_path)
    store = WorkflowStore(tmp_path, readonly=True)             # no file yet → unreadable
    with pytest.raises(PersistenceError) as exc:
        push_workflow_events(MockOperatorChannel(), store, now=NOW, repo_root=tmp_path)
    assert exc.value.reason_code == "WORKFLOW_STORE_UNAVAILABLE"
