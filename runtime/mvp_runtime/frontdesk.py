"""F2 Conversational front desk — the runtime of the ``conversation.frontdesk`` role.

Thomas talks to the channel in plain language; this module turns each message into exactly
one governed action. The privilege separation is structural, not behavioral: the model's
output is forced into the closed ``frontdesk_turn`` shape (ten typed turns, no field
that could name a tool, file, provider, or permission), and the ONE turn that changes state
(``SUBMIT_TASK``) feeds exclusively the already-governed F1 task queue. ``CANCEL_TASK`` used to
be the second; it now only *proposes* the ``/cancel`` for Thomas to type, so the model's own
output can no longer mutate coordination state at all. The front desk holds the conversation;
it never holds authority.

v0.3 lets ``SUBMIT_TASK`` carry a ``request_kind``, which is what makes the other activated
Roles reachable by conversation at all: the kind gate in the operator channel runs the front
desk only for an unmarked message, so before this every conversational submission queued with
no kind and routed to the analysis Role — five of six Roles were reachable only by typing a
``!marker``, which bypasses the conversation. The addition changes routing, not authority: a
kind names CAPABILITIES and the Role Registry still decides which Role covers them.

Reuse over invention, per the contract (`03_ROLE_CONTRACTS/CONVERSATION_FRONTDESK_ROLE.md`):

- **Provider**: the R7.2 triage precedent — the turn rides in the shared analysis JSON the
  gated providers already parse (``recommendation.turn``), so no new provider surface, no
  new parse layer, and the exact ``select_env_gated_chain`` semantics
  ``MVP_VALIDATOR_PROVIDER`` has (the environment is the gate since 2026-08-10; a chain
  never silently shrinks).
- **Dispatch**: the QUERY_*/CANCEL turns call the same ``registry_console`` appliers the
  deterministic verbs use — the conversational door can never answer differently from
  ``/tasks``, because it *is* ``/tasks``. Deterministic data beats model narration: for
  those turns the model's ``reply_text`` is dropped and the console's rendering is sent,
  since a narration of coordination state can be stale or invented and the listing cannot.
- **Session memory**: R5 working-memory entries under the role's own ``frontdesk_session``
  scope — readable by no other role (the contract prohibits the reverse read too), expiring
  like every candidate, prunable by the same retention pass.

Activation is registry-bound and fail-closed: selection refuses unless the registry says
the role is ACTIVE, non-routable, and its definition hash matches — so the D2 flip in the
role registry is load-bearing, not decorative.

Failure directions, each chosen once:
- Provider/turn failure → **degrade to the F1 path** (``FRONTDESK_DEGRADED`` audited,
  caller falls through to the plain enqueue). Conversation dies, the channel lives, and no
  message is lost — the R3 ``SEARCH_DEGRADED`` direction.
- Schema-invalid turn → **downgrade to CHAT_REPLY** (``FRONTDESK_TURN_INVALID`` audited).
  Uncertain submits nothing; the contract's ``invalid_turn_downgrade`` made executable.
- Verbatim mismatch on SUBMIT_TASK → **no submission**, honest reply asking Thomas to
  restate (``FRONTDESK_VERBATIM_MISMATCH`` audited). A paraphrase must never become the
  pipeline's input under his name.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from . import (
    memory, memory_console, registry_console, safety_gate, schema_cache, task_registry,
    timeutil,
)
from .budgets import (
    FRONTDESK_TIMEOUT_SECONDS,
    FRONTDESK_TOKEN_ALLOWANCE,
    MAX_SESSION_ENTRY_CHARS,
    clip_for_prompt,
)
from .errors import MvpRuntimeError, OperatorBlocked, PersistenceError, ProviderError
from .events import stamped_event
from .paths import repo_root as _repo_root
from .worker import Provider

FRONTDESK_PROVIDER_ENV = "MVP_FRONTDESK_PROVIDER"
FRONTDESK_ROLE_ID = "conversation.frontdesk"
TURN_SCHEMA_VERSION = "frontdesk_turn.v0.4"
TURN_EVENT_TYPE = "frontdesk_turn.v0"

SESSION_SCOPE = "frontdesk_session"
SESSION_CANDIDATE_TYPE = "frontdesk_session_context"
# A conversation is a working set, not knowledge: expire exchanges after 12 hours so a
# stale thread never shapes tomorrow's turns, and the R5 retention pass can prune them.
SESSION_TTL_MINUTES = 12 * 60
# How many recent exchanges feed the prompt, and how far back the verbatim check looks.
# One window for both on purpose: a request the model can still "see" is one the operator
# can still be quoted from.
SESSION_CONTEXT_TURNS = 10

# Read turns answered by the task-registry console.
_TURN_KINDS_REGISTRY = frozenset({"QUERY_STATUS", "QUERY_HISTORY", "QUERY_RESULT"})
# v0.2 read turns answered by the runtime's OTHER deterministic renderers. Added because
# the front desk could previously only see the task queue: asked anything else — "what is
# the crypto scheduler doing?" — it had no action to reach the answer with, so it fell to
# CHAT_REPLY and narrated a check it could not perform. The fix is a listed capability,
# not open access: each is read-only and renders from the same source its /verb does.
_TURN_KINDS_RUNTIME = frozenset({"QUERY_SCHEDULES", "QUERY_CONTROL", "QUERY_MEMORY"})
_TURN_KINDS_CHAT = frozenset({"CLARIFY", "CHAT_REPLY"})

# What each request kind is called in the prompt, so the model can match Thomas's words to
# one. The KEYS are not an authority — `planner.REQUEST_KIND_CAPABILITIES` is, and a test
# pins these two sets equal, so a kind added to the router without a gloss (or the reverse)
# fails in CI rather than becoming a kind the front desk can never select. The wordings are
# the operator's own marker vocabulary (`!분석` `!조사` `!번역` `!콘텐츠` `!개발`): the same
# request should reach the same Role whether he typed the marker or just said it.
REQUEST_KIND_GLOSSES: dict[str, str] = {
    "analysis": "분석 (사업 아이디어·구조·타당성 분석) — 기본값",
    "research": "조사 (자료·근거 수집과 출처 비교)",
    "translation": "번역 (다른 언어로 옮기기)",
    "content": "콘텐츠 (글·게시물 기획과 작성)",
    "development": "개발 (기술 분석과 구현 계획)",
}


def select_frontdesk_provider(*, now: str | None = None, root: Path | None = None) -> Provider | None:
    """Choose the front desk's own gated provider, or ``None`` when the feature is off.

    ``None`` (env unset) is the quiet default: the channel behaves exactly as F1 built it.
    But an env var that IS set must mean what it says, so with ``MVP_FRONTDESK_PROVIDER``
    present this **raises** rather than silently ignoring a misconfiguration:

    - the registry must carry ``conversation.frontdesk`` as ACTIVE and non-routable, with
      a matching definition hash (the D2 activation flip is what this checks — a candidate
      role's provider env is a request the governance has not granted);
    - unknown/duplicate chain members fail the whole selection closed
      (``select_env_gated_chain`` semantics; the environment is the gate since 2026-08-10).
    """
    if not os.environ.get(FRONTDESK_PROVIDER_ENV, "").strip():
        return None
    _require_active_role(root)
    # Imported here, not at module top: providers pulls the full adapter stack, and every
    # non-frontdesk caller of this module (the operator loop with the feature off) should
    # not pay for it.
    from .providers import FailoverProvider, MockProvider, _hosted_factories

    del now  # the environment is the gate (Thomas 2026-08-10)
    chain = safety_gate.select_env_gated_chain(
        env_var=FRONTDESK_PROVIDER_ENV,
        factories=_hosted_factories(),
        flags=("model_invocation", "network_access"),
        default_factory=MockProvider,
    )
    return chain[0] if len(chain) == 1 else FailoverProvider(chain)


def _require_active_role(root: Path | None) -> None:
    """Fail closed unless the registry's D2 flip actually happened, hash-verified."""
    import yaml

    base = root if root is not None else _repo_root()
    registry_path = base / "03_ROLE_CONTRACTS" / "ROLE_REGISTRY.yaml"
    try:
        registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        entries = [e for e in registry.get("non_dynamic_roles", [])
                   if isinstance(e, Mapping) and e.get("role_id") == FRONTDESK_ROLE_ID]
    except (OSError, ValueError, AttributeError) as exc:
        raise OperatorBlocked(
            "FRONTDESK_ROLE_UNRESOLVED", f"could not read the role registry: {exc}"
        ) from exc
    if len(entries) != 1:
        raise OperatorBlocked(
            "FRONTDESK_ROLE_UNRESOLVED",
            f"{FRONTDESK_ROLE_ID} must be registered exactly once in non_dynamic_roles",
        )
    entry = entries[0]
    if entry.get("status") != "active":
        raise OperatorBlocked(
            "FRONTDESK_ROLE_INACTIVE",
            f"{FRONTDESK_ROLE_ID} is {entry.get('status')!r} in the registry; the "
            "conversational channel needs the explicit activation flip (decision D2), "
            "not just a provider env var",
        )
    if entry.get("routable") is not False:
        # The privilege separation depends on this bit; a registry that flipped it is not
        # a registry this module should trust.
        raise OperatorBlocked(
            "FRONTDESK_ROLE_MISCONFIGURED", f"{FRONTDESK_ROLE_ID} must be non-routable"
        )
    definition_rel = entry.get("definition_path")
    expected = entry.get("definition_sha256")
    from hashlib import sha256 as _sha256

    try:
        actual = _sha256((base / str(definition_rel)).read_bytes()).hexdigest()
    except OSError as exc:
        raise OperatorBlocked(
            "FRONTDESK_ROLE_UNRESOLVED", f"role definition unreadable: {exc}"
        ) from exc
    if actual != expected:
        raise OperatorBlocked(
            "FRONTDESK_ROLE_HASH_MISMATCH",
            f"{FRONTDESK_ROLE_ID} definition hash mismatch (expected {expected}, got {actual})",
        )


# --- session memory ----------------------------------------------------------


def _session_entries(working_memory: Any, now: str) -> list[dict[str, Any]]:
    """The live session exchanges, oldest first, capped to the context window."""
    if working_memory is None:
        return []
    live = [
        e for e in working_memory.read_all()
        if isinstance(e, dict)
        and e.get("scope") == SESSION_SCOPE
        and not memory.is_expired(e, now)
    ]
    live.sort(key=lambda e: e.get("created_at") or "")
    return live[-SESSION_CONTEXT_TURNS:]


def _record_exchange(
    working_memory: Any, *, operator_text: str, turn_kind: str, reply: str, now: str,
) -> None:
    """Persist one exchange as a frontdesk_session candidate. Best-effort: losing session
    context degrades the next turn's memory, never this turn's answer."""
    if working_memory is None:
        return
    entry = {
        "candidate_id": integrity.short_id(
            "fdsess", {"text": operator_text, "kind": turn_kind, "at": now}
        ),
        "candidate_type": SESSION_CANDIDATE_TYPE,
        "scope": SESSION_SCOPE,
        "status": memory.CANDIDATE_STATUS,
        "validated": False,
        "promotable": False,
        # The prompt context, compact. BOTH halves capped: the reply always was, but
        # `operator_text` was not — so one pasted business plan became permanent prompt
        # weight in every later turn for the whole 12-hour TTL. Measured: a 1.6k-char paste
        # across ten turns took the front-desk prompt from 1,398 to 18,183 chars, on a call
        # that fires for every plain-text message including "고마워".
        "content": (f"Thomas: {clip_for_prompt(operator_text, MAX_SESSION_ENTRY_CHARS)}\n"
                    f"Frontdesk[{turn_kind}]: {clip_for_prompt(reply, 400)}"),
        # The raw operator words, kept verbatim for the SUBMIT_TASK check window.
        "operator_text": operator_text,
        "created_at": now,
        memory.EXPIRES_AT: timeutil.plus_minutes(now, SESSION_TTL_MINUTES),
        "origin": None,
    }
    try:
        working_memory.append([entry])
    except PersistenceError:
        pass


# --- the model turn ----------------------------------------------------------


_PROMPT_TEMPLATE = """당신은 Thomas Agent 런타임의 대화형 프런트데스크(conversation.frontdesk)입니다.
Thomas의 텔레그램 메시지 하나를 읽고, 아래 목록 중 정확히 하나의 턴으로 응답합니다.

턴 종류:
- SUBMIT_TASK: Thomas가 분석/작업을 요청함. payload.request_text에는 Thomas의 요청 문장을
  **한 글자도 바꾸지 말고 원문 그대로** 넣습니다 (요약/의역/번역 금지 — 당신의 이해는
  reply_text에서 확인용으로만 서술). important와 independent_validation은 **생략 금지 —
  항상 두 필드 모두** 넣습니다. Thomas가 그렇게 **말했을 때만** true, 말하지 않았으면
  **false**를 명시적으로 넣으세요 (어조에서 추론 금지). 둘 중 하나라도 빠지면 그 턴은
  접수되지 않습니다.
  payload.request_kind는 **어떤 종류의 작업인지**를 아래 중 하나로 고릅니다:
%(request_kinds)s
  같은 규칙이 적용됩니다 — Thomas의 **말에서** 고르고, 주제의 분위기로 추측하지 마세요.
  ("이 문서 번역해줘"는 translation, "번역 시장이 어떤지 분석해줘"는 analysis입니다.)
  종류가 분명하지 않으면 **null**을 넣으세요 (분석으로 처리됩니다). 종류를 잘못 고르면
  다른 출력 형식을 가진 담당에게 가서 엉뚱한 모양의 답이 나오므로, 애매하면 null입니다.
- QUERY_STATUS: 지금 무엇이 실행/대기 중인지 물음.
- QUERY_HISTORY: 지난 작업 목록을 물음. payload.limit은 개수를 말했을 때만.
- QUERY_RESULT: 특정 작업의 결과를 다시 보여달라 함. payload.entry_id 필요.
- QUERY_SCHEDULES: 스케줄러/정기 실행에 대해 물음 (예: "crypto 스케줄러 어떻게 돼있어?",
  "정기 작업 뭐 돌고 있어?", "다음 실행 언제야?").
- QUERY_CONTROL: 런타임이 가동/일시정지/정지 중인지, 실행이 허용되는지 물음.
- QUERY_MEMORY: 승격 가능한 메모리 후보 목록을 물음.
- CANCEL_TASK: 대기 중인 작업 취소 요청. payload.entry_id 필요.
- CLARIFY: 의도가 불확실하면 추측하지 말고 되물음.
  되물은 뒤 Thomas가 답하면, 그 답만 따로 제출하지 말고 **원래 요청과 함께** 제출하세요:
  payload.request_text에는 원래 요청 문장을 원문 그대로, payload.clarification_texts에는
  그가 추가로 말한 문장(들)을 역시 **원문 그대로** 넣습니다. 예 — "Prediction 데이터
  분석해줘" → (되물음) → "7일"이면 request_text는 "Prediction 데이터 분석해줘",
  clarification_texts는 ["7일"]입니다. 두 항목 모두 그가 실제로 보낸 문장이어야 하며,
  요약하거나 합쳐 쓰면 접수되지 않습니다.
- CHAT_REPLY: 그 외 대화 (인사, 감사, 잡담, 질문에 대한 짧은 답).

규칙:
- 불확실하면 SUBMIT_TASK 대신 CLARIFY.
- reply_text는 항상 Thomas에게 보일 자연어.
- **할 수 없는 일을 하겠다고 말하지 마세요.** 위 목록에 없는 것을 요청받으면
  "확인할게요" 같은 약속 대신, 그것은 지금 볼 수 없다고 분명히 답하세요 (CHAT_REPLY).
  CHAT_REPLY와 CLARIFY는 **아무 행동도 하지 않습니다** — 그 턴의 텍스트가 무언가를
  했거나 하겠다는 뜻으로 읽히면 그건 거짓말입니다.
- 조회 턴(QUERY_*)의 reply_text에는 상태를 지어내지 마세요. 실제 데이터는 런타임이
  붙입니다. 짧게 무엇을 조회하는지만 쓰면 됩니다.

출력: 응답 JSON에는 **%(envelope_keys)s 필드가 반드시 있어야 합니다** — 대화 턴이라 내용은
짧아도 되고 목록은 비어 있어도 됩니다, 있기만 하면 됩니다. 하나라도 빠지면 응답 전체가
버려지고 당신의 턴은 전달되지 않습니다.
그 위에 recommendation을 다음 형태로 채우세요.
"recommendation": {"action": "<턴 종류>", "turn": {"schema_version": "%(schema_version)s",
"turn_kind": "<턴 종류>", "payload": {...}, "reply_text": "..."}}
"""


def _prompt_header() -> str:
    """The instruction block, with the schema version the runtime ACTUALLY enforces and the
    request kinds the router can ACTUALLY resolve.

    Interpolated rather than typed out: the header hard-coded ``frontdesk_turn.v0.1`` while the
    validator's ``const`` had moved to v0.2, so a model that followed the instruction exactly
    produced a turn that failed validation — and every failed turn is downgraded to CHAT_REPLY.
    The whole vocabulary (SUBMIT_TASK, every QUERY_*, CANCEL_TASK) was one obedient model away
    from being dead, silently, with only ``FRONTDESK_TURN_INVALID`` in the ledger to say so. The
    version now comes from the same constant the validation uses, so the two cannot drift.

    The kind list is built the same way and for the same reason: a kind the prompt offers but
    the schema's enum or the router's table does not carry is a submission that dies at
    validation or at the queue's far end. Listing them from one dict, pinned by a test to the
    router's own table, is what keeps the three honest.

    The envelope keys are interpolated for the third instance of the same lesson, and this one
    was measured rather than reasoned about. The turn rides inside the shared analysis JSON, so
    the provider's parser requires that JSON's own fields — and this prompt described only
    ``recommendation`` and never mentioned them. Groq's ``json_object`` mode constrains no keys,
    so the model was free to omit them and did, in **3 of 8** live turns
    (``MALFORMED_RESPONSE``: the whole response discarded, the turn never delivered, the
    channel degraded to the plain queue). Naming them here took that to **0 of 8**. Taken from
    the parser's own constant so a fourth required field cannot appear without this prompt
    learning about it."""
    from .providers import _REQUIRED_ANALYSIS_KEYS      # the parser's own list, not a copy

    kinds = "\n".join(
        f"    - {kind}: {REQUEST_KIND_GLOSSES[kind]}" for kind in sorted(REQUEST_KIND_GLOSSES)
    )
    return _PROMPT_TEMPLATE % {
        "schema_version": TURN_SCHEMA_VERSION,
        "request_kinds": kinds,
        "envelope_keys": ", ".join(_REQUIRED_ANALYSIS_KEYS),
    }


def _build_prompt(session: list[dict[str, Any]], text: str) -> str:
    lines = [_prompt_header()]
    if session:
        lines.append("최근 대화:")
        lines += [str(e.get("content", "")) for e in session]
        lines.append("")
    lines.append(f"Thomas의 새 메시지:\n{text}")
    return "\n".join(lines)


def _extract_turn(analysis: Mapping[str, Any], repo_root: Path) -> dict[str, Any] | None:
    """The validated turn from the shared analysis JSON, or None if unusable."""
    recommendation = analysis.get("recommendation")
    if not isinstance(recommendation, Mapping):
        return None
    turn = recommendation.get("turn")
    if not isinstance(turn, dict):
        return None
    try:
        schema_cache.validate_against_schema(
            turn, repo_root / "schemas" / f"{TURN_SCHEMA_VERSION}.schema.json", "frontdesk_turn"
        )
    except Exception:  # noqa: BLE001 — RuntimeSchemaError or a registry surprise: same downgrade
        return None
    return turn


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _verbatim_ok(request_text: str, current: str, session: list[dict[str, Any]]) -> bool:
    """True when the submitted text is genuinely the operator's words: a normalized
    substring of the current message or of a recent session message. Whitespace is the one
    liberty granted (Telegram wraps lines); characters are not."""
    needle = _normalize(request_text)
    if not needle:
        return False
    if needle in _normalize(current):
        return True
    return any(
        needle in _normalize(str(e.get("operator_text", ""))) for e in session
    )


def _clarification_texts(payload: Mapping[str, Any]) -> list[str]:
    """The follow-up segments this submission carries, with nulls and blanks dropped.

    Only cleaning happens here; deduplication belongs to :func:`_compose_request`, which sees
    ``request_text`` too. Doing it in this function was the first shape and it was wrong in a
    way its own test caught: a model that helpfully repeated the request inside this list
    produced "분석해줘\\n분석해줘", because the two halves were deduplicated separately and so
    never compared against each other."""
    raw = payload.get("clarification_texts")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item.strip()]


def _compose_request(segments: list[str]) -> str:
    """The request the pipeline receives: the operator's segments, newline-joined, in the
    order the front desk assembled them, each appearing once.

    A newline rather than a space because these were separate messages and the specialist
    should read them as separate statements — "30일로 해줘" glued onto the end of a sentence
    reads as part of that sentence. Nothing is added: no labels, no "clarification:" prefix,
    nothing of the front desk's own. Every character submitted is one Thomas typed.

    Deduplicated by normalized text across ALL segments, first occurrence winning. A repeat is
    never dangerous — every segment already had to be his own words — but "7일 7일" reaches
    the specialist as an emphasis he did not write, and a model echoing the request into the
    clarification list is the likeliest way to produce one. The cost is that a genuine
    repetition loses its second copy, which is the cheaper mistake."""
    seen: set[str] = set()
    kept: list[str] = []
    for segment in segments:
        text = segment.strip()
        key = _normalize(text)
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(text)
    return "\n".join(kept)


def _resolve_request_kind(raw: Any) -> str | None:
    """The request kind to queue, asked of the router that owns the question.

    The schema's enum already bounds this, so reaching the refusal below means the enum and
    ``planner.REQUEST_KIND_CAPABILITIES`` have drifted apart. Asking the router anyway is
    deliberate: it is the authority on which kinds are routable, and the alternative — queueing
    a kind it will refuse — turns a drift into a task that sits in the queue only to fail at the
    far end, minutes later, in a different process.

    An unknown kind is **refused, never defaulted to analysis**. That direction is the router's
    own recorded decision (``capabilities_for_request_kind``): silently analyzing a request
    someone asked to have translated is the wrong answer delivered confidently, which is worse
    than a refusal naming the kinds that exist. The front desk does not get to re-decide it
    more leniently just because it is closer to the operator.
    """
    if raw is None:
        return None
    from .planner import capabilities_for_request_kind      # light, but only this branch needs it

    kind, _capabilities = capabilities_for_request_kind(str(raw))
    return kind


def _propose_cancel(entry_id: Any, *, registry: Any, control_store: Any = None) -> dict[str, Any]:
    """Resolve the entry the model wants cancelled and propose the command — never cancel.

    Read-only by construction: it looks the entry up and hands back the exact `/cancel` to type.
    An unresolvable or ambiguous id is reported honestly rather than guessed at, and an entry
    that is no longer cancellable is named as such so the operator does not type a verb that
    will only refuse — including while the runtime is halted, since `/cancel` is kill-bound and
    proposing it then would be a false instruction."""
    if control_store is not None:
        try:
            state = control_store.load()
        except MvpRuntimeError as exc:
            return {"reply": f"런타임 상태를 읽을 수 없어 취소를 안내할 수 없습니다 ({exc.reason_code}).",
                    "action": exc.reason_code}
        if not state.execution_allowed:
            return {"reply": f"런타임이 {state.mode} 상태입니다 — 취소는 `/resume` 이후에 가능합니다.",
                    "action": state.refusal_reason_code()}
    if not (isinstance(entry_id, str) and entry_id.strip()):
        return {"reply": "어떤 작업을 취소할까요? `/tasks`로 목록을 확인하실 수 있습니다.",
                "action": "CANCEL_PROPOSAL_NO_ID"}
    try:
        entry = registry.find(entry_id.strip())
    except MvpRuntimeError as exc:
        # Ambiguous prefix, unreadable registry — the honest typed answer, not a guess.
        return {"reply": exc.reason, "action": exc.reason_code}
    if entry is None:
        return {"reply": f"'{entry_id}'에 해당하는 작업을 찾지 못했습니다. `/tasks`로 확인해 주세요.",
                "action": "CANCEL_PROPOSAL_NOT_FOUND"}
    full_id = getattr(entry, "registry_entry_id", entry_id)
    # The console's own answer to "why not", not a second wording: telling Thomas the status
    # and stopping there left him without the verb that WOULD stop a running task.
    refusal = registry_console.cancel_refusal(entry)
    if refusal is not None:
        return {"reply": f"{full_id}: {refusal[1]}", "action": "CANCEL_PROPOSAL_NOT_CANCELLABLE"}
    if getattr(entry, "status", None) != task_registry.QUEUED:
        # Only QUEUED is proposable. Anything the console does not explain (a status added
        # later) is reported rather than turned into a `/cancel` that would only refuse.
        return {"reply": f"{full_id} 은(는) 현재 {getattr(entry, 'status', '?')} 상태라 취소할 수 없습니다.",
                "action": "CANCEL_PROPOSAL_NOT_CANCELLABLE"}
    return {
        "reply": (f"이 작업을 취소하시려면 다음을 보내주세요: `/cancel {full_id}`\n"
                  f"(대기 중: {getattr(entry, 'request_text', '') or ''}".rstrip() + ")"),
        "action": "CANCEL_PROPOSAL",
    }


def _answer_runtime_query(
    kind: str,
    *,
    working_memory: Any,
    ledger: Any,
    control_store: Any,
    schedules: Any,
    operator_id: str,
    now: str,
    root: Path,
) -> dict[str, Any]:
    """Answer one read-only runtime query from the runtime's OWN renderer.

    Same rule as the registry queries: the model chose the subject, the runtime produces
    the words. A model's account of scheduler or control state can be stale or invented;
    the store's cannot. Each subject is unavailable-not-guessed if its source is not wired.
    """
    if kind == "QUERY_SCHEDULES":
        from .scheduler import ScheduleStore, render_schedule_summary

        store = schedules if schedules is not None else ScheduleStore.default(root)
        try:
            listing = render_schedule_summary(store.list(), now=now)
        except MvpRuntimeError as exc:
            return {"reply": f"스케줄을 읽을 수 없습니다 ({exc.reason_code}).",
                    "action": exc.reason_code}
        return {"reply": listing, "action": "SCHEDULES_LISTED"}

    if kind == "QUERY_CONTROL":
        from .control import ControlStore, status_lines

        store = control_store if control_store is not None else ControlStore.default(root)
        return {"reply": status_lines(store.load(), ledger=ledger), "action": "CONTROL_STATUS"}

    # QUERY_MEMORY — the same listing /memory prints, through the same applier. LIST is
    # read-only there, so it answers in any runtime mode and needs no kill-switch check.
    try:
        outcome = memory_console.apply_memory_command(
            ("LIST", None, None), operator_id=operator_id, working_memory=working_memory,
            ledger=ledger, control_store=control_store, now=now, repo_root=root,
        )
    except OperatorBlocked as exc:
        return {"reply": exc.reason, "action": exc.reason_code}
    return {"reply": outcome["reply"], "action": outcome["action"]}


def _audit(ledger: Any, action: str, *, now: str, **fields: Any) -> None:
    """One durable note per noteworthy turn outcome. Best-effort — a conversation event
    must never cost the conversation (the operator-probe precedent)."""
    if ledger is None:
        return
    try:
        ledger.append_block(stamped_event(TURN_EVENT_TYPE, action=action, created_at=now, **fields))
    except PersistenceError:
        pass


# --- the turn loop -----------------------------------------------------------


def run_turn(
    text: str,
    *,
    provider: Provider,
    registry: Any,
    working_memory: Any = None,
    ledger: Any = None,
    control_store: Any = None,
    schedules: Any = None,
    operator_id: str,
    now: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any] | None:
    """Handle one conversational message. Returns ``{"reply", "action", ...}`` — or
    ``None``, which tells the caller "the front desk cannot serve this turn; fall back to
    the deterministic F1 path" (degraded, audited, no message lost).

    The kill switch is the CALLER's gate: the operator channel refuses plain text while
    not ACTIVE before this function is ever reached, so a PAUSED runtime stops the
    conversation LLM without this module owning a second copy of that rule.
    """
    stamp = now or timeutil.utc_now_iso()
    root = repo_root if repo_root is not None else _repo_root()
    session = _session_entries(working_memory, stamp)

    try:
        result = provider.generate(
            _build_prompt(session, text),
            max_output_tokens=FRONTDESK_TOKEN_ALLOWANCE,
            timeout_seconds=FRONTDESK_TIMEOUT_SECONDS,
        )
    except (ProviderError, TimeoutError) as exc:
        reason = getattr(exc, "reason_code", type(exc).__name__)
        _audit(ledger, "FRONTDESK_DEGRADED", now=stamp, reason_code=str(reason))
        return None

    turn = _extract_turn(result.analysis, root)
    if turn is None:
        # The contract's downgrade rule: an invalid turn becomes CHAT_REPLY, and an
        # uncertain front desk submits nothing. The analysis summary is the best honest
        # reply available — it is the model's own words, just not a valid action.
        _audit(ledger, "FRONTDESK_TURN_INVALID", now=stamp, model_id=result.model_id)
        summary = str(result.analysis.get("summary") or "").strip()
        turn = {"turn_kind": "CHAT_REPLY", "payload": {},
                "reply_text": summary or "메시지를 행동으로 옮기지 못했습니다 — 다시 말씀해 주세요."}

    kind = turn["turn_kind"]
    payload = turn.get("payload", {})
    reply_text = turn["reply_text"]
    outcome: dict[str, Any]

    if kind in _TURN_KINDS_CHAT:
        outcome = {"reply": reply_text, "action": f"FRONTDESK_{kind}"}

    elif kind in _TURN_KINDS_RUNTIME:
        outcome = _answer_runtime_query(
            kind, working_memory=working_memory, ledger=ledger, control_store=control_store,
            schedules=schedules, operator_id=operator_id, now=stamp, root=root,
        )

    elif kind == "CANCEL_TASK":
        # A cancel is the one MUTATION this turn vocabulary can name, and it used to be
        # dispatched straight from the model's own `entry_id` — no equivalent of SUBMIT_TASK's
        # verbatim check. Three ways that went wrong: the model could resolve "그거" to the wrong
        # queued entry, `registry.find` accepts any unique *prefix* so a short invented one can
        # match a real entry, and the session prompt can carry externally-sourced text (a
        # QUERY_RESULT reply embeds search-hit content), so the choice was steerable by data.
        #
        # So the model no longer cancels. It RESOLVES the entry read-only and proposes: the reply
        # names exactly what would be cancelled and the deterministic verb to type. The model
        # proposes, the operator's explicit /cancel disposes — same division as everywhere else
        # here, and it keeps the useful half (finding which entry he meant).
        outcome = _propose_cancel(
            payload.get("entry_id"), registry=registry, control_store=control_store
        )

    elif kind in _TURN_KINDS_REGISTRY:
        # Deterministic data beats narration: these answer with the console's own
        # rendering, because a model's account of coordination state can be stale or
        # invented and the listing cannot. The conversational door IS /tasks.
        command = {
            "QUERY_STATUS": ("TASKS", None),
            "QUERY_HISTORY": ("HISTORY", str(payload["limit"]) if payload.get("limit") else None),
            "QUERY_RESULT": ("RESULT", payload.get("entry_id")),
        }[kind]
        try:
            applied = registry_console.apply_registry_command(
                command, operator_id=operator_id, registry=registry, ledger=ledger,
                # The console applier enforces the kill switch for CANCEL itself and
                # refuses without a control store — threading the channel's own store
                # through keeps that gate exactly as strict as the /cancel verb's.
                control_store=control_store,
                now=stamp, repo_root=root,
            )
            outcome = {"reply": applied["reply"], "action": applied["action"]}
        except OperatorBlocked as exc:
            # An honest typed refusal is a fine conversational answer.
            outcome = {"reply": exc.reason, "action": exc.reason_code}

    elif kind == "SUBMIT_TASK":
        # Every segment is checked separately, and one failure refuses the whole submission.
        # Checking the composed string instead would pass a paraphrase glued between two real
        # quotes; checking segments and submitting only the ones that passed would silently
        # drop the very answer this feature exists to carry.
        segments = [payload["request_text"], *_clarification_texts(payload)]
        if not all(_verbatim_ok(segment, text, session) for segment in segments):
            _audit(ledger, "FRONTDESK_VERBATIM_MISMATCH", now=stamp,
                   segments=len(segments))
            outcome = {
                "reply": ("요청을 원문 그대로 접수하지 못했습니다 — 실행할 요청 문장을 "
                          "한 번에 그대로 보내주시면 그대로 접수합니다."),
                "action": "FRONTDESK_VERBATIM_MISMATCH",
            }
        else:
            request_text = _compose_request(segments)
            try:
                request_kind = _resolve_request_kind(payload.get("request_kind"))
                entry, position = task_registry.enqueue(
                    registry, request_text=request_text, origin="FRONTDESK",
                    requester_id=operator_id, now=stamp,
                    flags={"important": bool(payload.get("important")),
                           "independent_validation": bool(payload.get("independent_validation"))},
                    request_kind=request_kind,
                )
            except MvpRuntimeError as exc:
                # The queue's own fail-closed refusals (QUEUE_FULL, an unwritable store) and
                # an unroutable kind alike surface as the reply — never a silent drop (the F1
                # enqueue rule).
                outcome = {"reply": exc.reason, "action": exc.reason_code}
            else:
                queue_note = "바로 시작합니다" if position == 1 else f"대기 {position}번째"
                # The kind is named in the receipt, and that is what makes selecting it from
                # conversation acceptable at all. Routing used to come only from an explicit
                # `!marker`, precisely because a wrong guess sends work to a Role with a
                # different output contract. A model may now read the kind from Thomas's words,
                # so the receipt says which one it read — arriving BEFORE the pipeline runs, so
                # a misread is one `/cancel` away instead of a wrong-shaped answer later.
                kind_note = f"\n종류: {request_kind}" if request_kind else ""
                # When more than one of his messages was assembled into this request, the
                # receipt quotes what was actually submitted. Assembling is the one thing the
                # front desk now does that he cannot see from his own scrollback — he said two
                # things and one request went in — so the composed text is shown BEFORE the
                # pipeline runs, which is what keeps a wrong assembly a `/cancel` rather than a
                # wrong answer. A single-segment request already reads back from his own
                # message, so it gets no echo: noise on every task would train him past it.
                # Keyed off what was COMPOSED, not off how many segments arrived: a model that
                # echoed the request into the clarification list sends two segments that
                # deduplicate to one line, and quoting a single line back as an "assembly"
                # would be showing him a composition that did not happen.
                composed_note = (
                    f"\n요청 내용:\n{clip_for_prompt(request_text, MAX_SESSION_ENTRY_CHARS)}"
                    if "\n" in request_text else ""
                )
                outcome = {
                    "reply": (f"{reply_text}\n\n접수했습니다 ({queue_note}){kind_note}"
                              f"{composed_note}\n"
                              f"id: {entry.registry_entry_id[:12]}\n"
                              "완료되면 결과를 보내드립니다 — /tasks 로 진행 상황을 볼 수 있습니다."),
                    "action": "FRONTDESK_TASK_QUEUED",
                    "registry_entry_id": entry.registry_entry_id,
                    "request_kind": request_kind,
                    "segments": len(segments),
                }
    else:  # pragma: no cover — the schema's enum makes this unreachable
        outcome = {"reply": reply_text, "action": "FRONTDESK_CHAT_REPLY"}

    _audit(ledger, "turn", now=stamp, turn_kind=kind, outcome=outcome["action"],
           model_id=result.model_id,
           input_tokens=result.input_tokens, output_tokens=result.output_tokens,
           usage_reported=getattr(result, "usage_reported", True))
    _record_exchange(working_memory, operator_text=text, turn_kind=kind,
                     reply=outcome["reply"], now=stamp)
    return outcome
