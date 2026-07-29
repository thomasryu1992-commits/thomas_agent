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
    return {"schema_version": "frontdesk_turn.v0.4", "turn_kind": kind,
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


def test_submit_turn_carries_the_request_kind_to_the_queue(tmp_path):
    """v0.3: the whole point. Before it, every conversational submission queued with no kind
    and ran as an analysis, so five of the six activated Roles were reachable only by typing
    a `!marker` — which bypasses the front desk entirely, i.e. they were not reachable by
    conversation at all."""
    registry = TaskRegistryStore(tmp_path)
    provider = TurnProvider(_turn(
        "SUBMIT_TASK",
        {"request_text": "이 문서 영어로 번역해줘", "important": False,
         "independent_validation": False, "request_kind": "translation"},
    ))

    outcome = _run("이 문서 영어로 번역해줘", provider, tmp_path, registry=registry)

    assert outcome["action"] == "FRONTDESK_TASK_QUEUED"
    assert outcome["request_kind"] == "translation"
    assert registry.latest()[0].request_kind == "translation"


def test_the_receipt_names_the_kind_it_read(tmp_path):
    """Reading the kind from conversation is a guess the marker path never had to make, so
    the guess is shown: the receipt arrives BEFORE the pipeline runs, which turns a misread
    into a `/cancel` rather than a confident answer of the wrong shape."""
    registry = TaskRegistryStore(tmp_path)
    provider = TurnProvider(_turn(
        "SUBMIT_TASK",
        {"request_text": "리액트 상태관리 어떻게 짤지 봐줘", "important": False,
         "independent_validation": False, "request_kind": "development"},
    ))

    outcome = _run("리액트 상태관리 어떻게 짤지 봐줘", provider, tmp_path, registry=registry)

    assert "종류: development" in outcome["reply"]


def test_absent_kind_keeps_the_pre_v0_3_routing(tmp_path):
    """Null is the analysis kind — exactly where every conversational submission went
    before, so a model that omits the field changes nothing."""
    registry = TaskRegistryStore(tmp_path)
    for payload in (
        {"request_text": "세차 구독 사업 분석해줘", "important": False,
         "independent_validation": False},
        {"request_text": "세차 구독 사업 분석해줘", "important": False,
         "independent_validation": False, "request_kind": None},
    ):
        outcome = _run("세차 구독 사업 분석해줘", TurnProvider(_turn("SUBMIT_TASK", payload)),
                       tmp_path, registry=registry)
        assert outcome["action"] == "FRONTDESK_TASK_QUEUED"
        assert outcome["request_kind"] is None
        assert registry.latest()[0].request_kind is None
        assert "종류:" not in outcome["reply"]


def test_an_unroutable_kind_is_refused_never_defaulted_to_analysis(tmp_path, monkeypatch):
    """Only reachable if the schema's enum and the router's table drift apart — and then the
    refusal must be the router's, not a lenient second opinion. Silently analyzing a request
    someone asked to have translated is the wrong answer delivered confidently; and queueing
    a kind the router will refuse only moves the failure minutes downstream."""
    from runtime.mvp_runtime import planner

    monkeypatch.delitem(planner.REQUEST_KIND_CAPABILITIES, "translation")
    registry = TaskRegistryStore(tmp_path)
    provider = TurnProvider(_turn(
        "SUBMIT_TASK",
        {"request_text": "이 문서 번역해줘", "important": False,
         "independent_validation": False, "request_kind": "translation"},
    ))

    outcome = _run("이 문서 번역해줘", provider, tmp_path, registry=registry)

    assert outcome["action"] == "UNKNOWN_REQUEST_KIND"
    assert registry.latest() == []          # nothing queued


def test_the_prompt_offers_exactly_the_kinds_the_router_can_resolve():
    """Three lists have to agree — the prompt's, the schema's enum, and the router's table.
    A kind in the prompt that is missing from either of the others is a submission that dies
    at validation or at the queue's far end, and the operator would only see a downgrade."""
    from runtime.mvp_runtime import planner

    schema = json.loads((ROOT / "schemas" / "frontdesk_turn.v0.4.schema.json").read_text(
        encoding="utf-8"))
    submit = next(b for b in schema["allOf"]
                  if b["if"]["properties"]["turn_kind"]["const"] == "SUBMIT_TASK")
    enum = set(submit["then"]["properties"]["payload"]["properties"]["request_kind"]["enum"])

    assert set(frontdesk.REQUEST_KIND_GLOSSES) == set(planner.REQUEST_KIND_CAPABILITIES)
    assert enum == set(planner.REQUEST_KIND_CAPABILITIES) | {None}
    header = frontdesk._prompt_header()
    for kind in planner.REQUEST_KIND_CAPABILITIES:
        assert f"- {kind}:" in header


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


def test_a_running_task_proposal_names_the_verb_that_would_stop_it(tmp_path):
    """The proposal said "현재 RUNNING 상태라 취소할 수 없습니다" and stopped there, leaving
    Thomas without the verb that WOULD stop it. It now borrows the console's own answer, so the
    conversational door and /cancel cannot give different reasons."""
    registry = TaskRegistryStore(tmp_path)
    entry, _ = enqueue(registry, request_text="실행 중인 작업", origin="TELEGRAM",
                       requester_id="tg-12345", now=NOW)
    registry.claim_next_queued(now=NOW)          # QUEUED -> RUNNING
    provider = TurnProvider(_turn("CANCEL_TASK", {"entry_id": entry.registry_entry_id}))

    outcome = _run("그거 취소해줘", provider, tmp_path, registry=registry,
                   control_store=ControlStore(tmp_path / "control"))

    assert outcome["action"] == "CANCEL_PROPOSAL_NOT_CANCELLABLE"
    assert "/kill" in outcome["reply"]           # the verb that actually stops it
    assert registry.latest()[0].status == "RUNNING"


def test_chat_and_clarify_do_nothing_but_reply(tmp_path):
    registry = TaskRegistryStore(tmp_path)
    for kind in ("CHAT_REPLY", "CLARIFY"):
        outcome = _run("고마워!", TurnProvider(_turn(kind, {}, reply="천만에요.")),
                       tmp_path, registry=registry)
        assert outcome["action"] == f"FRONTDESK_{kind}"
        assert outcome["reply"] == "천만에요."
    assert registry.latest() == []


# --- what a conversation costs per turn ---------------------------------------

def test_a_long_paste_does_not_become_permanent_prompt_weight(tmp_path):
    """The front desk fires for EVERY plain-text message, including "고마워", and carries ten
    session turns. The reply half of a session entry was capped and the operator half was not,
    so one pasted business plan sat in every later prompt for the whole 12-hour TTL. Measured:
    a 1,637-char paste across ten turns took the prompt from 1,398 to 18,183 chars."""
    from runtime.mvp_runtime.budgets import MAX_SESSION_ENTRY_CHARS

    wm = WorkingMemoryStore(tmp_path / "wm")
    long_msg = "이 사업 아이디어를 분석해줘: " + ("구독형 세차의 단위경제와 재방문율. " * 60)
    for _ in range(10):
        frontdesk._record_exchange(wm, operator_text=long_msg, turn_kind="SUBMIT_TASK",
                                   reply="접수했습니다", now=NOW)
    session = frontdesk._session_entries(wm, NOW)
    prompt = frontdesk._build_prompt(session, "그거 어떻게 됐어?")
    assert len(session) == 10                     # the window itself is unchanged
    assert len(prompt) < 10_000, f"prompt grew to {len(prompt)} chars"
    assert "chars omitted]" in prompt             # and says it clipped


def test_clipping_the_prompt_does_not_break_the_verbatim_guard(tmp_path):
    """The catch: SUBMIT_TASK is only accepted when the text is genuinely Thomas's words, and
    `_verbatim_ok` searches the session. Clipping the stored `operator_text` would have made a
    long request unquotable and silently unsubmittable — so only the PROMPT-facing `content`
    is clipped; the raw words stay whole for the check."""
    wm = WorkingMemoryStore(tmp_path / "wm")
    long_msg = "이 사업 아이디어를 분석해줘: " + ("구독형 세차의 단위경제. " * 60)
    frontdesk._record_exchange(wm, operator_text=long_msg, turn_kind="CLARIFY",
                               reply="어떤 부분을 보길 원하세요?", now=NOW)
    from runtime.mvp_runtime.budgets import MAX_SESSION_ENTRY_CHARS

    session = frontdesk._session_entries(wm, NOW)
    entry = session[0]
    # The prompt-facing half is clipped...
    assert "chars omitted]" in entry["content"]
    assert long_msg not in entry["content"]
    assert len(entry["content"]) < MAX_SESSION_ENTRY_CHARS + 200
    # ...while the raw words are kept whole, so the request stays quotable...
    assert entry["operator_text"] == long_msg
    # ...and the submit is therefore still accepted as verbatim.
    assert frontdesk._verbatim_ok(long_msg, "응 그거 해줘", session) is True


# --- the instruction the model actually reads ---------------------------------
#
# Every test above builds its own turn with the right schema_version, which is exactly why
# nobody noticed that the PROMPT told the model to emit `frontdesk_turn.v0.1` long after the
# validator's const moved to v0.2. An obedient model therefore failed validation on every
# turn and got downgraded to CHAT_REPLY — the whole vocabulary silently dead, with only
# FRONTDESK_TURN_INVALID in the ledger. These check the prompt, not our own fixtures.

def test_a_turn_built_exactly_as_the_prompt_instructs_is_accepted(tmp_path):
    """Round-trip through the prompt's own words: whatever schema_version the instruction
    names must be one the runtime accepts."""
    import re

    instructed = re.findall(r"frontdesk_turn\.v\d+\.\d+", frontdesk._prompt_header())
    assert instructed, "the prompt no longer tells the model which schema_version to emit"
    for version in set(instructed):
        turn = {"schema_version": version, "turn_kind": "SUBMIT_TASK",
                "payload": {"request_text": "이 사업 아이디어를 분석해줘: 구독 세차",
                            "important": False, "independent_validation": False},
                "reply_text": "접수하겠습니다"}
        assert frontdesk._extract_turn({"recommendation": {"turn": turn}}, ROOT) is not None, (
            f"the prompt instructs schema_version={version!r}, which the validator rejects"
        )


def test_the_prompt_lists_every_turn_kind_the_schema_allows():
    """A kind in the schema but not the prompt is a capability the model cannot know it has;
    a kind in the prompt but not the schema is an instruction that can only fail validation."""
    import json

    schema = json.loads(
        (ROOT / "schemas" / f"{frontdesk.TURN_SCHEMA_VERSION}.schema.json").read_text(encoding="utf-8")
    )
    prompt = frontdesk._prompt_header()
    for kind in schema["properties"]["turn_kind"]["enum"]:
        assert kind in prompt, f"{kind} is allowed by the schema but absent from the prompt"


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


def test_the_conversational_kind_reaches_the_pipeline_across_the_whole_seam(tmp_path, monkeypatch):
    """The JOIN, not the stages. Each half of this was already covered — the front desk writes
    a kind onto the entry, and the drain passes `entry.request_kind` to the pipeline — and a
    field renamed or dropped between them would leave both green while every conversational
    request silently ran as an analysis again. That is the failure mode this repository has
    already paid for once (#201), so the seam gets its own test."""
    monkeypatch.setattr("runtime.mvp_runtime.operator_feedback.record_delivery",
                        lambda *a, **k: None)
    seen: dict = {}

    def _capture(*_args, **kwargs):
        seen["request_kind"] = kwargs.get("request_kind")
        return {"status": "COMPLETED", "final_response": "번역 결과",
                "records": {"received_task": {"identity": {"trace_id": "trace_f2",
                                                           "task_id": "task_f2"}}}}

    monkeypatch.setattr("runtime.mvp_runtime.operator.run_task", _capture)
    registry = TaskRegistryStore(tmp_path)
    provider = TurnProvider(_turn(
        "SUBMIT_TASK", {"request_text": "이 문서 영어로 번역해줘", "important": False,
                        "independent_validation": False, "request_kind": "translation"}))
    channel = MockOperatorChannel(inbound=[_msg("이 문서 영어로 번역해줘")])

    run_operator_once(channel, REG, registry=registry, frontdesk_provider=provider,
                      repo_root=ROOT)

    assert seen["request_kind"] == "translation"
    assert registry.latest()[0].status == task_registry.DELIVERED


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


# --- v0.4: clarification resume ----------------------------------------------
#
# The gap, measured before it was built: the verbatim rule requires a submission to be a
# substring of ONE message, so after "분석해줘" → (CLARIFY) → "7일" the front desk could
# submit the request without the period, or the period without the request, and the one
# thing Thomas actually meant was the only unsubmittable option.

def test_a_clarification_answer_is_carried_into_the_same_request(tmp_path):
    """The whole point. Two of his messages, one submission, and the period survives."""
    registry = TaskRegistryStore(tmp_path)
    working_memory = WorkingMemoryStore(tmp_path / "wm")
    # Turn 1: he asks, the front desk asks back.
    _run("Prediction 데이터 분석해줘", TurnProvider(_turn("CLARIFY", {}, reply="기간을 알려주세요.")),
         tmp_path, registry=registry, working_memory=working_memory)
    # Turn 2: he answers, and the front desk assembles both.
    provider = TurnProvider(_turn(
        "SUBMIT_TASK",
        {"request_text": "Prediction 데이터 분석해줘", "important": False,
         "independent_validation": False, "clarification_texts": ["7일"]},
    ))
    outcome = _run("7일", provider, tmp_path, registry=registry, working_memory=working_memory)

    assert outcome["action"] == "FRONTDESK_TASK_QUEUED"
    assert registry.latest()[0].request_text == "Prediction 데이터 분석해줘\n7일"


def test_one_bad_segment_refuses_the_whole_submission(tmp_path):
    """Checked per segment, and a failure is total. Checking the composed string would pass a
    paraphrase glued between two real quotes; submitting only the segments that passed would
    silently drop the very answer this feature exists to carry."""
    registry = TaskRegistryStore(tmp_path)
    provider = TurnProvider(_turn(
        "SUBMIT_TASK",
        {"request_text": "Prediction 데이터 분석해줘", "important": False,
         "independent_validation": False,
         "clarification_texts": ["최근 7일 기준으로, 그리고 요약도 붙여서"]},   # never typed
    ))
    outcome = _run("Prediction 데이터 분석해줘", provider, tmp_path, registry=registry)

    assert outcome["action"] == "FRONTDESK_VERBATIM_MISMATCH"
    assert registry.latest() == []


def test_the_receipt_quotes_what_was_actually_assembled(tmp_path):
    """Assembling is the one thing he cannot see from his own scrollback — he said two things
    and one request went in. The composed text arrives BEFORE the pipeline runs."""
    registry = TaskRegistryStore(tmp_path)
    wm = WorkingMemoryStore(tmp_path / "wm")
    _run("이 아이디어 분석해줘", TurnProvider(_turn("CLARIFY", {}, reply="기간은요?")),
         tmp_path, registry=registry, working_memory=wm)
    outcome = _run("30일", TurnProvider(_turn(
        "SUBMIT_TASK", {"request_text": "이 아이디어 분석해줘", "important": False,
                        "independent_validation": False, "clarification_texts": ["30일"]})),
        tmp_path, registry=registry, working_memory=wm)

    assert "요청 내용:" in outcome["reply"]
    assert "이 아이디어 분석해줘\n30일" in outcome["reply"]


def test_a_single_segment_request_gets_no_echo(tmp_path):
    """Noise on every task would train him past the one place it matters."""
    outcome = _run("이 아이디어 분석해줘", TurnProvider(_turn(
        "SUBMIT_TASK", {"request_text": "이 아이디어 분석해줘", "important": False,
                        "independent_validation": False})), tmp_path)
    assert "요청 내용:" not in outcome["reply"]


def test_a_repeated_segment_is_not_submitted_twice(tmp_path):
    """Every element is still his own words, so a repeat is not dangerous — but "7일 7일"
    reaches the specialist as an emphasis he did not write."""
    registry = TaskRegistryStore(tmp_path)
    outcome = _run("7일", TurnProvider(_turn(
        "SUBMIT_TASK", {"request_text": "7일", "important": False,
                        "independent_validation": False,
                        "clarification_texts": ["7일", "7일"]})),
        tmp_path, registry=registry)

    assert outcome["action"] == "FRONTDESK_TASK_QUEUED"
    assert registry.latest()[0].request_text == "7일"


def test_blank_and_null_segments_are_dropped(tmp_path):
    registry = TaskRegistryStore(tmp_path)
    _run("분석해줘", TurnProvider(_turn(
        "SUBMIT_TASK", {"request_text": "분석해줘", "important": False,
                        "independent_validation": False,
                        "clarification_texts": ["   ", "분석해줘"]})),
        tmp_path, registry=registry)
    assert registry.latest()[0].request_text == "분석해줘"


def test_omitting_the_field_is_exactly_the_v0_3_behaviour(tmp_path):
    """Opt-in: a turn that carries no clarification is byte-identical to before."""
    registry = TaskRegistryStore(tmp_path)
    for payload in (
        {"request_text": "분석해줘", "important": False, "independent_validation": False},
        {"request_text": "분석해줘", "important": False, "independent_validation": False,
         "clarification_texts": None},
    ):
        _run("분석해줘", TurnProvider(_turn("SUBMIT_TASK", payload)), tmp_path, registry=registry)
        assert registry.latest()[0].request_text == "분석해줘"


def test_nothing_of_the_front_desks_own_is_added_to_the_request(tmp_path):
    """No labels, no "clarification:" prefix. Every character submitted is one he typed."""
    registry = TaskRegistryStore(tmp_path)
    wm = WorkingMemoryStore(tmp_path / "wm")
    _run("A를 분석해줘", TurnProvider(_turn("CLARIFY", {}, reply="언제 기준인가요?")),
         tmp_path, registry=registry, working_memory=wm)
    _run("B 기준", TurnProvider(_turn(
        "SUBMIT_TASK", {"request_text": "A를 분석해줘", "important": False,
                        "independent_validation": False, "clarification_texts": ["B 기준"]})),
        tmp_path, registry=registry, working_memory=wm)

    submitted = registry.latest()[0].request_text
    assert set(submitted.split("\n")) == {"A를 분석해줘", "B 기준"}


# --- the prompt must name what the PARSER requires ---------------------------

def test_the_prompt_names_every_required_analysis_field():
    """Measured, not reasoned about: the turn rides inside the shared analysis JSON, and this
    prompt described only `recommendation`. Groq's `json_object` mode constrains no keys, so
    the model omitted the envelope fields in 3 of 8 live turns — the whole response discarded
    (`MALFORMED_RESPONSE`), the turn never delivered. Naming them took that to 0 of 8.

    Pinned against the parser's OWN constant, so a fourth required field cannot be added
    without this prompt learning about it — the same drift gate the schema version and the
    request kinds already have."""
    from runtime.mvp_runtime.providers import _REQUIRED_ANALYSIS_KEYS

    header = frontdesk._prompt_header()
    for key in _REQUIRED_ANALYSIS_KEYS:
        assert key in header, key


def test_the_prompt_demands_the_flags_rather_than_only_explaining_them():
    """`important`/`independent_validation` are schema-REQUIRED, and the prompt used to say
    only when to make them true — so a small model read "neither applies" as "omit both" and
    the turn failed validation (2 of 8 live turns). The instruction now says the fields are
    mandatory and false is a value, not an absence."""
    header = frontdesk._prompt_header()
    assert "생략 금지" in header
    assert "false" in header
