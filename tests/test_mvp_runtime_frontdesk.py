"""F2 front-desk runtime tests — the turn loop, its boundaries, and its failure directions.

What must hold: a turn can only do what its closed schema can express; every invalid or
uncertain turn collapses toward "nothing happens" (downgrade / no submission / degrade to
the F1 path); the conversational door answers coordination questions with the console's own
rendering, never the model's; the kill switch stops the conversation LLM; and selection is
registry-bound — a provider env var against an unactivated role fails at startup.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.mvp_runtime import control, frontdesk, memory, task_registry
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import OperatorBlocked, ProviderError
from runtime.mvp_runtime.operator import (
    InboundMessage,
    MockOperatorChannel,
    OperatorIdentity,
    handle_operator_message,
    run_operator_once,
)
from runtime.mvp_runtime.store import LedgerStore
from runtime.mvp_runtime.task_registry import QUEUED, TaskRegistryStore, enqueue
from runtime.mvp_runtime.worker import ProviderResult
from runtime.mvp_runtime.working_memory import WorkingMemoryStore

NOW = "2026-07-25T13:00:00Z"
REG = OperatorIdentity(operator_id="tg-12345", chat_id="chat-777")
ROOT = Path(__file__).resolve().parents[1]


class TurnProvider:
    """A scripted front-desk provider: returns the given turn inside the shared analysis
    JSON (the triage precedent), recording every prompt for assertions."""

    model_id = "test.frontdesk"
    model_version = "0"
    network_egress = False

    def __init__(self, turn=None, *, summary="테스트 요약", raise_error=False):
        self.turn = turn
        self.summary = summary
        self.raise_error = raise_error
        self.prompts: list[str] = []

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        self.prompts.append(prompt)
        if self.raise_error:
            raise ProviderError("PROVIDER_UNAVAILABLE", "scripted outage")
        analysis = {"summary": self.summary, "key_findings": [], "facts": [],
                    "inferences": [], "risks": [], "evidence_quality": "low",
                    "unresolved_questions": [],
                    "recommendation": {"action": (self.turn or {}).get("turn_kind", "NONE"),
                                       "turn": self.turn}}
        return ProviderResult(analysis=analysis, model_id=self.model_id, model_version="0",
                              input_tokens=10, output_tokens=5, latency_ms=1)


def _turn(kind, payload, reply="알겠습니다."):
    return {"schema_version": "frontdesk_turn.v0.2", "turn_kind": kind,
            "payload": payload, "reply_text": reply}


def _run(text, provider, tmp_path, **kwargs):
    kwargs.setdefault("registry", TaskRegistryStore(tmp_path))
    kwargs.setdefault("operator_id", "tg-12345")
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("repo_root", ROOT)   # schemas live in the repo, not the tmp store
    return frontdesk.run_turn(text, provider=provider, **kwargs)


# --- dispatch ----------------------------------------------------------------

def test_submit_turn_enqueues_verbatim(tmp_path):
    registry = TaskRegistryStore(tmp_path)
    provider = TurnProvider(_turn(
        "SUBMIT_TASK",
        {"request_text": "구독형 세차 사업 아이디어를 분석해줘",
         "important": False, "independent_validation": False},
        reply="세차 구독 사업성 분석으로 접수할게요.",
    ))

    outcome = _run("구독형 세차 사업 아이디어를 분석해줘", provider, tmp_path, registry=registry)

    assert outcome["action"] == "FRONTDESK_TASK_QUEUED"
    entry = registry.latest()[0]
    assert entry.status == QUEUED
    assert entry.origin == "FRONTDESK"
    assert entry.request_text == "구독형 세차 사업 아이디어를 분석해줘"
    # The narration AND the deterministic ack both reach the operator.
    assert "세차 구독 사업성 분석으로 접수할게요." in outcome["reply"]
    assert "접수했습니다" in outcome["reply"]


def test_query_turns_answer_with_the_console_rendering_not_narration(tmp_path):
    """Deterministic data beats narration: the reply for a status question is /tasks
    output, and the model's own account of state is dropped."""
    registry = TaskRegistryStore(tmp_path)
    enqueue(registry, request_text="대기 중 작업", origin="TELEGRAM",
            requester_id="tg-12345", now=NOW)
    provider = TurnProvider(_turn("QUERY_STATUS", {},
                                  reply="지금 아무 작업도 없는 것 같아요!"))  # wrong on purpose

    outcome = _run("지금 뭐 하고 있어?", provider, tmp_path, registry=registry)

    assert outcome["action"] == "TASKS_LISTED"
    assert "진행 중인 작업 1개" in outcome["reply"]
    assert "아무 작업도 없는" not in outcome["reply"]


def test_result_turn_reuses_the_console_path_honestly(tmp_path):
    registry = TaskRegistryStore(tmp_path)
    provider = TurnProvider(_turn("QUERY_RESULT", {"entry_id": "treg_nothere"}))
    outcome = _run("아까 결과 다시 보여줘", provider, tmp_path, registry=registry)
    # The console's typed refusal becomes the conversational answer — same truth, same door.
    assert outcome["action"] == "ENTRY_NOT_FOUND"


def test_cancel_turn_is_kill_switch_bound_via_the_console(tmp_path):
    registry = TaskRegistryStore(tmp_path)
    entry, _ = enqueue(registry, request_text="취소될 작업", origin="TELEGRAM",
                       requester_id="tg-12345", now=NOW)
    control_store = ControlStore(tmp_path / "control")
    control_store.save(control.ControlState(mode=control.KILLED, updated_by="tg-12345",
                                            updated_at=NOW, reason="test"))
    provider = TurnProvider(_turn("CANCEL_TASK", {"entry_id": entry.registry_entry_id}))

    outcome = _run("그 작업 취소해줘", provider, tmp_path, registry=registry,
                   control_store=control_store)

    assert outcome["action"] == "RUNTIME_KILLED"
    assert registry.latest()[0].status == QUEUED


def test_chat_and_clarify_do_nothing_but_reply(tmp_path):
    registry = TaskRegistryStore(tmp_path)
    for kind in ("CHAT_REPLY", "CLARIFY"):
        outcome = _run("고마워!", TurnProvider(_turn(kind, {}, reply="천만에요.")),
                       tmp_path, registry=registry)
        assert outcome["action"] == f"FRONTDESK_{kind}"
        assert outcome["reply"] == "천만에요."
    assert registry.latest() == []


# --- fail directions ---------------------------------------------------------

def test_invalid_turn_downgrades_to_chat_reply(tmp_path):
    """The contract's invalid_turn_downgrade rule, executable: an unusable turn submits
    nothing and the model's summary is the honest reply."""
    registry = TaskRegistryStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    bad = _turn("SUBMIT_TASK", {"request_text": "x", "important": False,
                                "independent_validation": False, "tool_id": "shell"})

    outcome = _run("셸로 뭐 좀 실행해줘", TurnProvider(bad, summary="그건 할 수 없어요."),
                   tmp_path, registry=registry, ledger=ledger)

    assert outcome["action"] == "FRONTDESK_CHAT_REPLY"
    assert outcome["reply"] == "그건 할 수 없어요."
    assert registry.latest() == []
    assert any(b.get("action") == "FRONTDESK_TURN_INVALID" for b in ledger.read_blocks())


def test_missing_turn_object_downgrades_the_same_way(tmp_path):
    outcome = _run("안녕", TurnProvider(None, summary="안녕하세요!"), tmp_path)
    assert outcome["action"] == "FRONTDESK_CHAT_REPLY"
    assert outcome["reply"] == "안녕하세요!"


def test_paraphrased_submission_is_refused(tmp_path):
    """A paraphrase must never become the pipeline's input under the operator's name."""
    registry = TaskRegistryStore(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    provider = TurnProvider(_turn(
        "SUBMIT_TASK",
        {"request_text": "세차 구독 서비스의 사업성을 분석하라",   # the model's rewording
         "important": False, "independent_validation": False},
    ))

    outcome = _run("구독형 세차 아이디어 어때?", provider, tmp_path,
                   registry=registry, ledger=ledger)

    assert outcome["action"] == "FRONTDESK_VERBATIM_MISMATCH"
    assert registry.latest() == []
    assert any(b.get("action") == "FRONTDESK_VERBATIM_MISMATCH" for b in ledger.read_blocks())


def test_verbatim_accepts_whitespace_liberty_only(tmp_path):
    registry = TaskRegistryStore(tmp_path)
    provider = TurnProvider(_turn(
        "SUBMIT_TASK",
        {"request_text": "구독형 세차 사업을 분석해줘", "important": False,
         "independent_validation": False},
    ))
    outcome = _run("구독형 세차\n사업을   분석해줘", provider, tmp_path, registry=registry)
    assert outcome["action"] == "FRONTDESK_TASK_QUEUED"


def test_verbatim_accepts_a_quote_from_the_recent_session(tmp_path):
    """Multi-turn intent: '아까 그거 분석해줘' is served by quoting the earlier message
    verbatim — the lookback window is the session context window."""
    wm = WorkingMemoryStore(tmp_path / "wm")
    frontdesk._record_exchange(wm, operator_text="프리미엄 세차 구독 사업 아이디어",
                               turn_kind="CHAT_REPLY", reply="재미있네요", now=NOW)
    registry = TaskRegistryStore(tmp_path)
    provider = TurnProvider(_turn(
        "SUBMIT_TASK",
        {"request_text": "프리미엄 세차 구독 사업 아이디어", "important": False,
         "independent_validation": False},
    ))
    outcome = _run("아까 말한 거 분석해줘", provider, tmp_path,
                   registry=registry, working_memory=wm)
    assert outcome["action"] == "FRONTDESK_TASK_QUEUED"


def test_provider_outage_degrades_to_none_and_audits(tmp_path):
    ledger = LedgerStore(tmp_path / "ledger")
    outcome = _run("분석해줘", TurnProvider(raise_error=True), tmp_path, ledger=ledger)
    assert outcome is None
    assert any(b.get("action") == "FRONTDESK_DEGRADED" for b in ledger.read_blocks())


def test_queue_full_is_an_honest_reply_not_a_drop(tmp_path):
    registry = TaskRegistryStore(tmp_path)
    for index in range(task_registry.QUEUE_DEPTH_LIMIT):
        registry.submit(task_registry.build_entry(
            request_text=f"작업 {index}", origin="TELEGRAM", requester_id="tg-12345",
            now=f"2026-07-25T13:00:{index:02d}Z", status=QUEUED))
    provider = TurnProvider(_turn(
        "SUBMIT_TASK", {"request_text": "하나 더 분석해줘", "important": False,
                        "independent_validation": False}))
    outcome = _run("하나 더 분석해줘", provider, tmp_path, registry=registry)
    assert outcome["action"] == "QUEUE_FULL"


# --- session memory ----------------------------------------------------------

def test_exchanges_are_recorded_and_feed_the_next_prompt(tmp_path):
    wm = WorkingMemoryStore(tmp_path / "wm")
    provider = TurnProvider(_turn("CHAT_REPLY", {}, reply="첫 번째 답"))
    _run("첫 번째 메시지", provider, tmp_path, working_memory=wm)

    entries = [e for e in wm.read_all() if e.get("scope") == frontdesk.SESSION_SCOPE]
    assert len(entries) == 1
    assert entries[0]["candidate_type"] == frontdesk.SESSION_CANDIDATE_TYPE
    assert entries[0][memory.EXPIRES_AT] > NOW

    second = TurnProvider(_turn("CHAT_REPLY", {}))
    _run("두 번째 메시지", second, tmp_path, working_memory=wm)
    assert "첫 번째 메시지" in second.prompts[0]


def test_expired_session_context_is_never_served(tmp_path):
    wm = WorkingMemoryStore(tmp_path / "wm")
    frontdesk._record_exchange(wm, operator_text="오래된 메시지", turn_kind="CHAT_REPLY",
                               reply="답", now="2026-07-20T00:00:00Z")
    provider = TurnProvider(_turn("CHAT_REPLY", {}))
    _run("새 메시지", provider, tmp_path, working_memory=wm)
    assert "오래된 메시지" not in provider.prompts[0]


# --- selection is registry-bound ---------------------------------------------

def test_env_unset_means_feature_off():
    assert frontdesk.select_frontdesk_provider(root=ROOT) is None


def test_env_set_against_missing_registry_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(frontdesk.FRONTDESK_PROVIDER_ENV, "google_ai_studio")
    with pytest.raises(OperatorBlocked) as exc:
        frontdesk.select_frontdesk_provider(root=tmp_path)
    assert exc.value.reason_code == "FRONTDESK_ROLE_UNRESOLVED"


def test_env_set_against_inactive_role_fails_closed(tmp_path, monkeypatch):
    """A provider env var is a request; the registry's D2 flip is the grant. Candidate
    status + env var must refuse at startup, not quietly run a conversation."""
    _copy_registry(tmp_path, status="candidate")
    monkeypatch.setenv(frontdesk.FRONTDESK_PROVIDER_ENV, "google_ai_studio")
    with pytest.raises(OperatorBlocked) as exc:
        frontdesk.select_frontdesk_provider(root=tmp_path)
    assert exc.value.reason_code == "FRONTDESK_ROLE_INACTIVE"


def test_tampered_definition_fails_closed(tmp_path, monkeypatch):
    _copy_registry(tmp_path, tamper_definition=True)
    monkeypatch.setenv(frontdesk.FRONTDESK_PROVIDER_ENV, "google_ai_studio")
    with pytest.raises(OperatorBlocked) as exc:
        frontdesk.select_frontdesk_provider(root=tmp_path)
    assert exc.value.reason_code == "FRONTDESK_ROLE_HASH_MISMATCH"


def _copy_registry(tmp_path: Path, *, status: str = "active", tamper_definition: bool = False):
    """A minimal registry+definition copy under tmp_path for selection tests."""
    definition = ROOT / "03_ROLE_CONTRACTS" / "CONVERSATION_FRONTDESK_ROLE.md"
    contracts = tmp_path / "03_ROLE_CONTRACTS"
    contracts.mkdir(parents=True, exist_ok=True)
    body = definition.read_text(encoding="utf-8")
    if status != "active":
        body = body.replace("status: active", f"status: {status}", 1)
    (contracts / "CONVERSATION_FRONTDESK_ROLE.md").write_text(body, encoding="utf-8")
    from hashlib import sha256
    digest = sha256((contracts / "CONVERSATION_FRONTDESK_ROLE.md").read_bytes()).hexdigest()
    if tamper_definition:
        (contracts / "CONVERSATION_FRONTDESK_ROLE.md").write_text(body + "\n# tampered\n",
                                                                  encoding="utf-8")
    (contracts / "ROLE_REGISTRY.yaml").write_text(
        "schema_version: role_registry.v0.3\n"
        "non_dynamic_roles:\n"
        "- role_id: conversation.frontdesk\n"
        "  role_type: session_front\n"
        f"  status: {status}\n"
        "  routable: false\n"
        "  definition_path: 03_ROLE_CONTRACTS/CONVERSATION_FRONTDESK_ROLE.md\n"
        f"  definition_sha256: {digest}\n",
        encoding="utf-8",
    )


# --- operator-channel integration --------------------------------------------

def _msg(text):
    return InboundMessage(text=text, sender_id="tg-12345", chat_id="chat-777")


def test_plain_text_goes_to_the_frontdesk_when_wired(tmp_path):
    registry = TaskRegistryStore(tmp_path)
    provider = TurnProvider(_turn("CHAT_REPLY", {}, reply="반가워요."))
    reply = handle_operator_message(_msg("안녕!"), registration=REG, registry=registry,
                                    frontdesk_provider=provider, now=NOW, repo_root=ROOT)
    assert reply.status == "FRONTDESK"
    assert reply.text == "반가워요."
    assert registry.latest() == []       # a greeting is not a task any more


def test_marked_requests_stay_deterministic(tmp_path):
    """`!중요` is the operator being explicit — deterministic intent never waits on a
    model, so the marker path bypasses the front desk entirely."""
    registry = TaskRegistryStore(tmp_path)
    provider = TurnProvider(_turn("CHAT_REPLY", {}))
    reply = handle_operator_message(_msg("!중요 이 아이디어를 분석해줘"), registration=REG,
                                    registry=registry, frontdesk_provider=provider,
                                    now=NOW, repo_root=ROOT)
    assert provider.prompts == []
    assert reply.status == "QUEUED"
    assert registry.latest()[0].flags["important"] is True


def test_slash_verbs_stay_deterministic(tmp_path):
    registry = TaskRegistryStore(tmp_path)
    provider = TurnProvider(_turn("CHAT_REPLY", {}))
    reply = handle_operator_message(_msg("/tasks"), registration=REG, registry=registry,
                                    frontdesk_provider=provider, now=NOW, repo_root=ROOT)
    assert provider.prompts == []
    assert reply.status == "REGISTRY"


def test_kill_switch_stops_the_conversation_llm(tmp_path):
    control_store = ControlStore(tmp_path / "control")
    control_store.save(control.ControlState(mode=control.PAUSED, updated_by="tg-12345",
                                            updated_at=NOW, reason="test"))
    provider = TurnProvider(_turn("CHAT_REPLY", {}))
    reply = handle_operator_message(_msg("안녕!"), registration=REG,
                                    registry=TaskRegistryStore(tmp_path),
                                    control_store=control_store,
                                    frontdesk_provider=provider, now=NOW, repo_root=ROOT)
    assert provider.prompts == []        # the model was never consulted
    assert reply.reason_code == "RUNTIME_PAUSED"


def test_degraded_frontdesk_falls_back_to_the_queue(tmp_path):
    """Conversation dying never loses a message: the raw text continues down the F1 path
    exactly as if the front desk were off."""
    registry = TaskRegistryStore(tmp_path)
    provider = TurnProvider(raise_error=True)
    reply = handle_operator_message(_msg("이 아이디어를 분석해줘"), registration=REG,
                                    registry=registry, frontdesk_provider=provider,
                                    now=NOW, repo_root=ROOT)
    assert reply.status == "QUEUED"
    assert registry.latest()[0].request_text == "이 아이디어를 분석해줘"
    assert registry.latest()[0].origin == "TELEGRAM"     # the F1 door, honestly labelled


def test_frontdesk_submission_is_drained_like_any_queued_task(tmp_path, monkeypatch):
    """End to end: a conversational submission runs through the same drain as a plain
    one — the front desk changed how tasks are ASKED for, not how they run."""
    # This test must pass the REAL repo root (the turn is schema-validated against
    # schemas/), and the same argument is what the delivery pointer would be written
    # under — so the one runtime write that would land in real state is stubbed out. It
    # is best-effort in production and not what this test asserts.
    monkeypatch.setattr("runtime.mvp_runtime.operator_feedback.record_delivery",
                        lambda *a, **k: None)
    monkeypatch.setattr(
        "runtime.mvp_runtime.operator.run_task",
        lambda *a, **k: {"status": "COMPLETED", "final_response": "분석 결과",
                         "records": {"received_task": {"identity": {"trace_id": "trace_f1",
                                                                    "task_id": "task_f1"}}}},
    )
    registry = TaskRegistryStore(tmp_path)
    provider = TurnProvider(_turn(
        "SUBMIT_TASK", {"request_text": "이 아이디어를 분석해줘", "important": False,
                        "independent_validation": False}))
    channel = MockOperatorChannel(inbound=[_msg("이 아이디어를 분석해줘")])

    run_operator_once(channel, REG, registry=registry, frontdesk_provider=provider,
                      repo_root=ROOT)

    assert registry.latest()[0].status == task_registry.DELIVERED
    texts = [t for _c, t in channel.sent]
    assert any("접수했습니다" in t for t in texts)
    assert any("분석 결과" in t for t in texts)


# --- v0.2 runtime queries ----------------------------------------------------
#
# The gap these close, from the live channel on 2026-07-25: asked "현재 crypto 스케쥴러는
# 어떻게 되어있어?", the front desk had no action that could reach a scheduler, fell to
# CHAT_REPLY, and narrated "확인할게요" — a check it could not perform. The answer is a
# listed capability, not open access.

def _schedule(store, kind="crypto_pipeline", interval=900, now=NOW):
    from runtime.mvp_runtime import scheduler
    sched = scheduler.build_schedule(kind=kind, request="BTCUSDT 1h", interval_seconds=interval,
                                     created_by="test", now=now)
    store.add(sched)
    return sched


def _schedule_store(tmp_path):
    from runtime.mvp_runtime.scheduler import ScheduleStore
    return ScheduleStore(tmp_path)


def test_schedule_question_is_answered_with_real_schedules(tmp_path):
    """The exact failure that prompted v0.2, now answered with data."""
    store = _schedule_store(tmp_path)
    _schedule(store)
    provider = TurnProvider(_turn("QUERY_SCHEDULES", {}, reply="스케줄을 확인할게요."))

    outcome = _run("현재 crypto 스케쥴러는 어떻게 되어있어?", provider, tmp_path,
                   schedules=store)

    assert outcome["action"] == "SCHEDULES_LISTED"
    assert "crypto_pipeline" in outcome["reply"]
    assert "15분마다" in outcome["reply"]
    # The model's narration is dropped; the store's rendering is what is sent.
    assert "확인할게요" not in outcome["reply"]


def test_schedule_summary_is_chat_sized(tmp_path):
    """`scheduler_cli list` prints each fire's full last_status — for a crypto pipeline
    that is a multi-line dump past Telegram's send limit."""
    from runtime.mvp_runtime import scheduler
    from dataclasses import replace as _replace

    store = _schedule_store(tmp_path)
    sched = _schedule(store)
    store.record_result(sched.schedule_id, last_run_at=NOW, last_status="x" * 4000)
    rendered = scheduler.render_schedule_summary(store.list(), now=NOW)
    assert len(rendered) < 500
    assert "…" in rendered


def test_many_schedules_of_one_kind_are_grouped(tmp_path):
    from runtime.mvp_runtime import scheduler
    store = _schedule_store(tmp_path)
    for index in range(16):
        _schedule(store, kind="crypto_factory", interval=86400,
                  now=f"2026-07-25T13:{index:02d}:00Z")
    rendered = scheduler.render_schedule_summary(store.list(), now=NOW)
    assert "crypto_factory ×16" in rendered
    assert "…외 13개" in rendered


def test_control_question_is_answered_from_the_control_store(tmp_path):
    control_store = ControlStore(tmp_path / "control")
    control_store.save(control.ControlState(mode=control.PAUSED, updated_by="tg-12345",
                                            updated_at=NOW, reason="점검 중"))
    provider = TurnProvider(_turn("QUERY_CONTROL", {}, reply="상태 확인할게요."))

    outcome = _run("지금 멈춰있어?", provider, tmp_path, control_store=control_store)

    assert outcome["action"] == "CONTROL_STATUS"
    assert "PAUSED" in outcome["reply"]
    assert "점검 중" in outcome["reply"]


def test_memory_question_is_answered_from_the_memory_console(tmp_path):
    provider = TurnProvider(_turn("QUERY_MEMORY", {}, reply="메모리 볼게요."))
    outcome = _run("기억해둔 거 뭐 있어?", provider, tmp_path,
                   working_memory=WorkingMemoryStore(tmp_path / "wm"))
    assert outcome["action"] == "MEMORY_LISTED"


def test_runtime_queries_answer_while_paused(tmp_path):
    """Read-only, so they answer in any mode — a stopped runtime is exactly when you ask."""
    control_store = ControlStore(tmp_path / "control")
    control_store.save(control.ControlState(mode=control.KILLED, updated_by="tg-12345",
                                            updated_at=NOW, reason="kill"))
    store = _schedule_store(tmp_path)
    _schedule(store)
    outcome = _run("스케줄 뭐 있어?", TurnProvider(_turn("QUERY_SCHEDULES", {})), tmp_path,
                   schedules=store, control_store=control_store)
    assert outcome["action"] == "SCHEDULES_LISTED"


def test_an_unreadable_schedule_store_says_so(tmp_path):
    store = _schedule_store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json\n", encoding="utf-8")
    outcome = _run("스케줄?", TurnProvider(_turn("QUERY_SCHEDULES", {})), tmp_path,
                   schedules=store)
    assert outcome["action"] == "SCHEDULES_UNREADABLE"


def test_the_new_turns_change_no_state(tmp_path):
    """Every v0.2 addition is read-only: none may queue, cancel, or promote anything."""
    registry = TaskRegistryStore(tmp_path)
    wm = WorkingMemoryStore(tmp_path / "wm")
    store = _schedule_store(tmp_path)
    _schedule(store)
    for kind in ("QUERY_SCHEDULES", "QUERY_CONTROL", "QUERY_MEMORY"):
        _run("뭐 좀 볼게", TurnProvider(_turn(kind, {})), tmp_path,
             registry=registry, working_memory=wm, schedules=store,
             control_store=ControlStore(tmp_path / "control"))
    assert registry.latest() == []
    assert [e for e in wm.read_all() if e.get("scope") != frontdesk.SESSION_SCOPE] == []


# --- the model proposes a cancel; the operator's /cancel disposes ---------------

def test_a_cancel_turn_never_cancels_it_proposes(tmp_path):
    """The one MUTATION this vocabulary can name used to dispatch straight from the model's own
    entry_id — no equivalent of SUBMIT_TASK's verbatim check. It now resolves read-only and hands
    back the deterministic verb, so a model-chosen id cannot change coordination state."""
    registry = TaskRegistryStore(tmp_path)
    entry, _ = enqueue(registry, request_text="취소 대상 작업", origin="TELEGRAM",
                       requester_id="tg-12345", now=NOW)
    provider = TurnProvider(_turn("CANCEL_TASK", {"entry_id": entry.registry_entry_id}))

    outcome = _run("그 작업 취소해줘", provider, tmp_path, registry=registry,
                   control_store=ControlStore(tmp_path / "control"))

    assert outcome["action"] == "CANCEL_PROPOSAL"
    assert registry.latest()[0].status == QUEUED          # nothing was cancelled
    assert "/cancel" in outcome["reply"]                   # the operator is told what to type
    assert entry.registry_entry_id in outcome["reply"]      # ...on which entry, in full


def test_a_short_invented_prefix_cannot_stand_in_for_a_real_entry(tmp_path):
    """`registry.find` accepts any unique prefix, so a model-invented short id could match a real
    entry. Proposing instead of cancelling makes that harmless — but the entry it names must be
    the FULL id so the operator can see whether it is the one he meant."""
    registry = TaskRegistryStore(tmp_path)
    entry, _ = enqueue(registry, request_text="유일한 작업", origin="TELEGRAM",
                       requester_id="tg-12345", now=NOW)
    short = entry.registry_entry_id[:3]
    provider = TurnProvider(_turn("CANCEL_TASK", {"entry_id": short}))

    outcome = _run("취소", provider, tmp_path, registry=registry,
                   control_store=ControlStore(tmp_path / "control"))

    assert outcome["action"] == "CANCEL_PROPOSAL"
    assert registry.latest()[0].status == QUEUED
    assert entry.registry_entry_id in outcome["reply"]     # full id, not the model's 3 chars


def test_an_unknown_entry_is_reported_not_guessed(tmp_path):
    provider = TurnProvider(_turn("CANCEL_TASK", {"entry_id": "does-not-exist"}))
    outcome = _run("취소", provider, tmp_path,
                   control_store=ControlStore(tmp_path / "control"))
    assert outcome["action"] == "CANCEL_PROPOSAL_NOT_FOUND"


def test_a_non_queued_entry_is_named_rather_than_proposed(tmp_path):
    """Do not tell the operator to type a verb that will only refuse."""
    registry = TaskRegistryStore(tmp_path)
    entry, _ = enqueue(registry, request_text="이미 시작된 작업", origin="TELEGRAM",
                       requester_id="tg-12345", now=NOW)
    registry.claim_next_queued(now=NOW)                    # -> RUNNING
    provider = TurnProvider(_turn("CANCEL_TASK", {"entry_id": entry.registry_entry_id}))

    outcome = _run("취소", provider, tmp_path, registry=registry,
                   control_store=ControlStore(tmp_path / "control"))

    assert outcome["action"] == "CANCEL_PROPOSAL_NOT_CANCELLABLE"
    assert "/cancel" not in outcome["reply"]
