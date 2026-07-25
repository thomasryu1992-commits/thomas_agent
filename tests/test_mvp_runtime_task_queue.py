"""F1 increment 2 — the queue: submission is non-blocking, the drain runs the work.

The property this buys is that the channel keeps answering while work is pending. So what
must hold: a request is acknowledged without running, the loop returns to the poll between
tasks, the claim is atomic (no task runs twice), the kill switch stops the drain without
dropping the backlog, and a queue that cannot be written to refuses rather than silently
swallowing a request Thomas believes was accepted.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import control, task_registry
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import OperatorBlocked, TaskRegistryBlocked
from runtime.mvp_runtime.operator import (
    InboundMessage,
    MockOperatorChannel,
    OperatorIdentity,
    drain_queue,
    handle_operator_message,
    run_operator_once,
)
from runtime.mvp_runtime.task_registry import (
    DELIVERED,
    QUEUE_DEPTH_LIMIT,
    QUEUED,
    RUNNING,
    TaskRegistryStore,
    build_entry,
    enqueue,
)

NOW = "2026-07-25T09:00:00Z"
REG = OperatorIdentity(operator_id="tg-12345", chat_id="chat-777")


def _registry(tmp_path) -> TaskRegistryStore:
    return TaskRegistryStore(tmp_path)


def _msg(text):
    return InboundMessage(text=text, sender_id="tg-12345", chat_id="chat-777")


def _control(tmp_path, mode=control.ACTIVE):
    store = ControlStore(tmp_path / "control")
    if mode != control.ACTIVE:
        store.save(control.ControlState(mode=mode, updated_by="tg-12345", updated_at=NOW,
                                        reason="test"))
    return store


def _stub_run(monkeypatch, calls=None):
    def _run(request, **kwargs):
        if calls is not None:
            calls.append(request)
        return {"status": "COMPLETED", "final_response": f"분석: {request}",
                "records": {"received_task": {"identity": {"task_id": "task_1",
                                                           "trace_id": "trace_1"}}}}
    monkeypatch.setattr("runtime.mvp_runtime.operator.run_task", _run)


# --- submission is non-blocking ----------------------------------------------

def test_a_request_is_queued_not_run(tmp_path, monkeypatch):
    """The whole point: handling a request must not hold the channel for a model call."""
    calls = []
    _stub_run(monkeypatch, calls)
    store = _registry(tmp_path)

    reply = handle_operator_message(_msg("분석해줘: 아이디어"), registration=REG,
                                    registry=store, now=NOW, repo_root=tmp_path)

    assert calls == []                                  # nothing ran
    assert reply.accepted and reply.status == "QUEUED"
    assert reply.reason_code == "TASK_QUEUED"
    assert store.latest()[0].status == QUEUED
    assert reply.registry_entry_id == store.latest()[0].registry_entry_id


def test_the_acknowledgement_says_where_in_the_queue_it_landed(tmp_path):
    store = _registry(tmp_path)
    first = handle_operator_message(_msg("첫 번째"), registration=REG, registry=store,
                                    now=NOW, repo_root=tmp_path)
    second = handle_operator_message(_msg("두 번째"), registration=REG, registry=store,
                                     now="2026-07-25T09:00:05Z", repo_root=tmp_path)
    assert "바로 시작합니다" in first.text
    assert "대기 2번째" in second.text


def test_without_a_registry_the_request_still_runs_inline(tmp_path, monkeypatch):
    """No registry means nothing to queue into — one rule, not a mode switch."""
    calls = []
    _stub_run(monkeypatch, calls)
    reply = handle_operator_message(_msg("분석해줘: 아이디어"), registration=REG,
                                    now=NOW, repo_root=tmp_path)
    assert calls == ["분석해줘: 아이디어"]
    assert reply.status == "COMPLETED"


# --- the drain ---------------------------------------------------------------

def test_one_pass_runs_one_task_and_returns_to_the_poll(tmp_path, monkeypatch):
    """Coming back to the poll between tasks is what keeps /tasks and /kill responsive."""
    calls = []
    _stub_run(monkeypatch, calls)
    store = _registry(tmp_path)
    channel = MockOperatorChannel(inbound=[_msg("첫 번째"), _msg("두 번째")])

    summary = run_operator_once(channel, REG, registry=store, repo_root=tmp_path)

    assert summary["handled"] == 2 and summary["executed"] == 1
    assert calls == ["첫 번째"]
    statuses = sorted(e.status for e in store.latest())
    assert statuses == [DELIVERED, QUEUED]

    # The second task runs on the next pass — FIFO, in the order he asked.
    run_operator_once(channel, REG, registry=store, repo_root=tmp_path)
    assert calls == ["첫 번째", "두 번째"]


def test_the_result_is_sent_as_its_own_message(tmp_path, monkeypatch):
    _stub_run(monkeypatch)
    store = _registry(tmp_path)
    channel = MockOperatorChannel(inbound=[_msg("분석해줘: 아이디어")])

    run_operator_once(channel, REG, registry=store, repo_root=tmp_path)

    texts = [text for _chat, text in channel.sent]
    assert len(texts) == 2
    assert "접수했습니다" in texts[0]
    assert "분석: 분석해줘: 아이디어" in texts[1]
    assert all(chat == REG.chat_id for chat, _text in channel.sent)


def test_an_empty_queue_drains_nothing(tmp_path):
    executed, failures = drain_queue(
        channel=MockOperatorChannel(), registration=REG, registry=_registry(tmp_path),
        control_store=None, max_tasks=1, now=NOW,
    )
    assert executed == [] and failures == 0


def test_the_important_marker_survives_the_queue(tmp_path, monkeypatch):
    """A flag recorded at submission must still shape the run one pass later."""
    seen = {}

    def _run(request, **kwargs):
        seen["priority"] = kwargs.get("priority")
        return {"status": "COMPLETED", "final_response": "ok", "records": {}}

    monkeypatch.setattr("runtime.mvp_runtime.operator.run_task", _run)
    store = _registry(tmp_path)
    channel = MockOperatorChannel(inbound=[_msg("!중요 분석해줘: 아이디어")])

    run_operator_once(channel, REG, registry=store, repo_root=tmp_path)

    assert seen["priority"] == "HIGH"
    assert store.latest()[0].flags["important"] is True


# --- atomic claim ------------------------------------------------------------

def test_a_claim_is_atomic_so_no_task_runs_twice(tmp_path):
    store = _registry(tmp_path)
    entry, _position = enqueue(store, request_text="한 번만", origin="TELEGRAM",
                               requester_id="tg-12345", now=NOW)

    first = store.claim_next_queued(now=NOW)
    second = store.claim_next_queued(now=NOW)

    assert first is not None and first.registry_entry_id == entry.registry_entry_id
    assert first.status == RUNNING
    assert second is None          # the second drain finds it already claimed


def test_the_claim_precedes_the_run_so_a_crash_never_reruns_it(tmp_path):
    """A crash after the claim leaves RUNNING for startup reconciliation — never QUEUED,
    which would run the task a second time."""
    store = _registry(tmp_path)
    enqueue(store, request_text="작업", origin="TELEGRAM", requester_id="tg-12345", now=NOW)
    store.claim_next_queued(now=NOW)

    task_registry.reconcile_stale_running(store, now="2026-07-25T10:00:00Z")

    entry = store.latest()[0]
    assert entry.status == task_registry.FAILED
    assert entry.last_reason_code == task_registry.ABANDONED_REASON_CODE
    assert store.claim_next_queued(now=NOW) is None


def test_claims_are_oldest_first(tmp_path):
    store = _registry(tmp_path)
    enqueue(store, request_text="먼저", origin="TELEGRAM", requester_id="tg", now=NOW)
    enqueue(store, request_text="나중", origin="TELEGRAM", requester_id="tg",
            now="2026-07-25T09:00:30Z")
    assert store.claim_next_queued(now=NOW).request_text == "먼저"


def test_same_second_arrivals_keep_their_order(tmp_path):
    """Timestamps are second-resolution, so a burst shares one ``submitted_at``. Breaking
    that tie on the hashed id ran them in an order unrelated to the order he asked."""
    store = _registry(tmp_path)
    sent = ["첫째", "둘째", "셋째", "넷째"]
    for text in sent:
        enqueue(store, request_text=text, origin="TELEGRAM", requester_id="tg", now=NOW)

    claimed = []
    while (entry := store.claim_next_queued(now=NOW)) is not None:
        claimed.append(entry.request_text)
    assert claimed == sent


# --- kill switch -------------------------------------------------------------

@pytest.mark.parametrize("mode", [control.PAUSED, control.KILLED])
def test_the_kill_switch_stops_the_drain_without_dropping_the_backlog(tmp_path, monkeypatch, mode):
    calls = []
    _stub_run(monkeypatch, calls)
    store = _registry(tmp_path)
    enqueue(store, request_text="대기 중인 작업", origin="TELEGRAM",
            requester_id="tg-12345", now=NOW)

    executed, _failures = drain_queue(
        channel=MockOperatorChannel(), registration=REG, registry=store,
        control_store=_control(tmp_path, mode), max_tasks=1, now=NOW,
    )

    assert executed == [] and calls == []
    # Paused, not dropped: it runs when the operator resumes.
    assert store.latest()[0].status == QUEUED


def test_the_kill_switch_is_rechecked_before_every_claim(tmp_path, monkeypatch):
    """A kill issued while one task holds the drain must stop the tasks behind it."""
    store = _registry(tmp_path)
    for index in range(3):
        enqueue(store, request_text=f"작업 {index}", origin="TELEGRAM",
                requester_id="tg-12345", now=f"2026-07-25T09:0{index}:00Z")
    control_store = _control(tmp_path)
    calls = []

    def _run(request, **kwargs):
        calls.append(request)
        control_store.save(control.ControlState(mode=control.KILLED, updated_by="tg-12345",
                                                updated_at=NOW, reason="mid-drain kill"))
        return {"status": "COMPLETED", "final_response": "ok", "records": {}}

    monkeypatch.setattr("runtime.mvp_runtime.operator.run_task", _run)

    executed, _failures = drain_queue(
        channel=MockOperatorChannel(), registration=REG, registry=store,
        control_store=control_store, max_tasks=10, now=NOW,
    )

    assert len(executed) == 1 and calls == ["작업 0"]
    assert sum(1 for e in store.latest() if e.status == QUEUED) == 2


def test_a_task_request_is_still_refused_while_not_active(tmp_path):
    """The kill switch blocks new work at the door too — nothing is queued to burst later."""
    store = _registry(tmp_path)
    reply = handle_operator_message(_msg("분석해줘"), registration=REG, registry=store,
                                    control_store=_control(tmp_path, control.KILLED),
                                    now=NOW, repo_root=tmp_path)
    assert not reply.accepted and reply.reason_code == "RUNTIME_KILLED"
    assert store.latest() == []


# --- fail-closed submission --------------------------------------------------

def test_a_full_queue_is_refused_rather_than_dropped(tmp_path):
    store = _registry(tmp_path)
    for index in range(QUEUE_DEPTH_LIMIT):
        store.submit(build_entry(request_text=f"작업 {index}", origin="TELEGRAM",
                                 requester_id="tg-12345", now=f"2026-07-25T09:00:{index:02d}Z",
                                 status=QUEUED))
    with pytest.raises(TaskRegistryBlocked) as exc:
        enqueue(store, request_text="하나 더", origin="TELEGRAM", requester_id="tg-12345",
                now=NOW)
    assert exc.value.reason_code == "QUEUE_FULL"


def test_a_failed_enqueue_refuses_instead_of_silently_dropping(tmp_path, monkeypatch):
    """Unlike the bookkeeping seam, the queue is the execution path: a swallowed failure
    would lose a request Thomas believes is running."""
    calls = []
    _stub_run(monkeypatch, calls)
    store = _registry(tmp_path)
    monkeypatch.setattr(
        TaskRegistryStore, "submit",
        lambda self, entry: (_ for _ in ()).throw(TaskRegistryBlocked("REGISTRY_WRITE_FAILED", "디스크 오류")),
    )

    reply = handle_operator_message(_msg("분석해줘: 아이디어"), registration=REG,
                                    registry=store, now=NOW, repo_root=tmp_path)

    assert not reply.accepted and reply.reason_code == "REGISTRY_WRITE_FAILED"
    assert calls == []


def test_an_unusable_registry_stops_the_drain(tmp_path):
    store = _registry(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json\n", encoding="utf-8")
    executed, failures = drain_queue(
        channel=MockOperatorChannel(), registration=REG, registry=store,
        control_store=None, max_tasks=1, now=NOW,
    )
    assert executed == [] and failures == 0


# --- responsiveness ----------------------------------------------------------

def test_pending_work_suppresses_the_long_poll(tmp_path, monkeypatch):
    """Holding the channel open for a message while a queued task sits unstarted would be
    the one wait nobody asked for."""
    _stub_run(monkeypatch)
    store = _registry(tmp_path)
    enqueue(store, request_text="대기 중", origin="TELEGRAM", requester_id="tg-12345", now=NOW)
    channel = MockOperatorChannel()

    run_operator_once(channel, REG, registry=store, long_poll_seconds=25, repo_root=tmp_path)

    assert channel.last_long_poll_seconds == 0


def test_an_idle_queue_keeps_the_long_poll(tmp_path):
    store = _registry(tmp_path)
    channel = MockOperatorChannel()
    run_operator_once(channel, REG, registry=store, long_poll_seconds=25, repo_root=tmp_path)
    assert channel.last_long_poll_seconds == 25


def test_console_commands_are_answered_while_work_is_queued(tmp_path, monkeypatch):
    """The property the whole increment exists for."""
    _stub_run(monkeypatch)
    store = _registry(tmp_path)
    for index in range(3):
        enqueue(store, request_text=f"작업 {index}", origin="TELEGRAM",
                requester_id="tg-12345", now=f"2026-07-25T09:0{index}:00Z")
    channel = MockOperatorChannel(inbound=[_msg("/tasks")])

    summary = run_operator_once(channel, REG, registry=store,
                                control_store=_control(tmp_path), repo_root=tmp_path)

    assert summary["handled"] == 1
    listing = channel.sent[0][1]
    assert "진행 중인 작업 3개" in listing
