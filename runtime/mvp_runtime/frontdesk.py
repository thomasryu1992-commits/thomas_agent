"""F2 Conversational front desk — the runtime of the ``conversation.frontdesk`` role.

Thomas talks to the channel in plain language; this module turns each message into exactly
one governed action. The privilege separation is structural, not behavioral: the model's
output is forced into the closed ``frontdesk_turn.v0.1`` shape (seven typed turns, no field
that could name a tool, file, provider, or permission), and the two turns that change state
feed exclusively the already-governed F1 task queue. The front desk holds the conversation;
it never holds authority.

Reuse over invention, per the contract (`03_ROLE_CONTRACTS/CONVERSATION_FRONTDESK_ROLE.md`):

- **Provider**: the R7.2 triage precedent — the turn rides in the shared analysis JSON the
  gated providers already parse (``recommendation.turn``), so no new provider surface, no
  new parse layer, and the exact ``select_gated_chain`` semantics ``MVP_VALIDATOR_PROVIDER``
  has (per-member grants, a chain never silently shrinks, env var alone opens nothing).
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
from .budgets import FRONTDESK_TIMEOUT_SECONDS, FRONTDESK_TOKEN_ALLOWANCE
from .errors import MvpRuntimeError, OperatorBlocked, PersistenceError, ProviderError
from .events import stamped_event
from .paths import repo_root as _repo_root
from .worker import Provider

FRONTDESK_PROVIDER_ENV = "MVP_FRONTDESK_PROVIDER"
FRONTDESK_ROLE_ID = "conversation.frontdesk"
TURN_SCHEMA_VERSION = "frontdesk_turn.v0.2"
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

def select_frontdesk_provider(*, now: str | None = None, root: Path | None = None) -> Provider | None:
    """Choose the front desk's own gated provider, or ``None`` when the feature is off.

    ``None`` (env unset) is the quiet default: the channel behaves exactly as F1 built it.
    But an env var that IS set must mean what it says, so with ``MVP_FRONTDESK_PROVIDER``
    present this **raises** rather than silently ignoring a misconfiguration:

    - the registry must carry ``conversation.frontdesk`` as ACTIVE and non-routable, with
      a matching definition hash (the D2 activation flip is what this checks — a candidate
      role's provider env is a request the governance has not granted);
    - every chain member needs its own local grant (``select_gated_chain`` semantics).
    """
    if not os.environ.get(FRONTDESK_PROVIDER_ENV, "").strip():
        return None
    _require_active_role(root)
    # Imported here, not at module top: providers pulls the full adapter stack, and every
    # non-frontdesk caller of this module (the operator loop with the feature off) should
    # not pay for it.
    from .providers import FailoverProvider, MockProvider, _hosted_factories

    chain = safety_gate.select_gated_chain(
        env_var=FRONTDESK_PROVIDER_ENV,
        factories=_hosted_factories(),
        flags=("model_invocation", "network_access"),
        default_factory=MockProvider,
        now=now,
        root=root,
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
        # The prompt context, compact. reply capped so a re-sent analysis does not balloon
        # the session store — the ledger keeps the full text, this is conversation memory.
        "content": f"Thomas: {operator_text}\nFrontdesk[{turn_kind}]: {reply[:400]}",
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


_PROMPT_HEADER = """당신은 Thomas Agent 런타임의 대화형 프런트데스크(conversation.frontdesk)입니다.
Thomas의 텔레그램 메시지 하나를 읽고, 아래 7종 중 정확히 하나의 턴으로 응답합니다.

턴 종류:
- SUBMIT_TASK: Thomas가 분석/작업을 요청함. payload.request_text에는 Thomas의 요청 문장을
  **한 글자도 바꾸지 말고 원문 그대로** 넣습니다 (요약/의역/번역 금지 — 당신의 이해는
  reply_text에서 확인용으로만 서술). important와 independent_validation은 Thomas가 그렇게
  **말했을 때만** true (어조에서 추론 금지).
- QUERY_STATUS: 지금 무엇이 실행/대기 중인지 물음.
- QUERY_HISTORY: 지난 작업 목록을 물음. payload.limit은 개수를 말했을 때만.
- QUERY_RESULT: 특정 작업의 결과를 다시 보여달라 함. payload.entry_id 필요.
- QUERY_SCHEDULES: 스케줄러/정기 실행에 대해 물음 (예: "crypto 스케줄러 어떻게 돼있어?",
  "정기 작업 뭐 돌고 있어?", "다음 실행 언제야?").
- QUERY_CONTROL: 런타임이 가동/일시정지/정지 중인지, 실행이 허용되는지 물음.
- QUERY_MEMORY: 승격 가능한 메모리 후보 목록을 물음.
- CANCEL_TASK: 대기 중인 작업 취소 요청. payload.entry_id 필요.
- CLARIFY: 의도가 불확실하면 추측하지 말고 되물음.
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

출력: 분석 JSON의 recommendation을 다음 형태로 채우세요.
"recommendation": {"action": "<턴 종류>", "turn": {"schema_version": "frontdesk_turn.v0.1",
"turn_kind": "<턴 종류>", "payload": {...}, "reply_text": "..."}}
"""


def _build_prompt(session: list[dict[str, Any]], text: str) -> str:
    lines = [_PROMPT_HEADER]
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

    elif kind in _TURN_KINDS_REGISTRY or kind == "CANCEL_TASK":
        # Deterministic data beats narration: these answer with the console's own
        # rendering, because a model's account of coordination state can be stale or
        # invented and the listing cannot. The conversational door IS /tasks.
        command = {
            "QUERY_STATUS": ("TASKS", None),
            "QUERY_HISTORY": ("HISTORY", str(payload["limit"]) if payload.get("limit") else None),
            "QUERY_RESULT": ("RESULT", payload.get("entry_id")),
            "CANCEL_TASK": ("CANCEL", payload.get("entry_id")),
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
        request_text = payload["request_text"]
        if not _verbatim_ok(request_text, text, session):
            _audit(ledger, "FRONTDESK_VERBATIM_MISMATCH", now=stamp)
            outcome = {
                "reply": ("요청을 원문 그대로 접수하지 못했습니다 — 실행할 요청 문장을 "
                          "한 번에 그대로 보내주시면 그대로 접수합니다."),
                "action": "FRONTDESK_VERBATIM_MISMATCH",
            }
        else:
            try:
                entry, position = task_registry.enqueue(
                    registry, request_text=request_text, origin="FRONTDESK",
                    requester_id=operator_id, now=stamp,
                    flags={"important": bool(payload.get("important")),
                           "independent_validation": bool(payload.get("independent_validation"))},
                )
            except MvpRuntimeError as exc:
                # The queue's own fail-closed refusals (QUEUE_FULL, an unwritable store)
                # surface as the reply — never a silent drop (the F1 enqueue rule).
                outcome = {"reply": exc.reason, "action": exc.reason_code}
            else:
                queue_note = "바로 시작합니다" if position == 1 else f"대기 {position}번째"
                outcome = {
                    "reply": (f"{reply_text}\n\n접수했습니다 ({queue_note})\n"
                              f"id: {entry.registry_entry_id[:12]}\n"
                              "완료되면 결과를 보내드립니다 — /tasks 로 진행 상황을 볼 수 있습니다."),
                    "action": "FRONTDESK_TASK_QUEUED",
                    "registry_entry_id": entry.registry_entry_id,
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
