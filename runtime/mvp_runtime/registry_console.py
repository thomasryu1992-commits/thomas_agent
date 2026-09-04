"""Operator task console — see what is running, what already ran, and get a result back.

The coordination half of the control channel, beside the emergency console (``control``),
approval (``approval``), feedback (``operator_feedback``) and memory (``memory_console``)
verb families. Same tokenizer as all of them (``control.command_verb``), so the channels
can never drift on what counts as a command, and the same precondition: the R4 identity
gate in ``handle_operator_message`` has already run, so only the registered Thomas is here.

Four verbs, three of them read-only:

- ``/tasks`` — what is open right now (RUNNING, then QUEUED).
- ``/history [n]`` — the last n finished entries (default 10).
- ``/result <id>`` — re-send the deliverable of a finished entry.
- ``/cancel <id>`` — cancel a QUEUED entry. The one verb that changes state.

The read verbs write no ledger event. That is the ``/status`` precedent, and it is a
correctness point rather than a saving: a read that appends to the log it reads races its
own tail. ``/cancel`` does write one — it changed something.

**``/result`` stores nothing of its own.** It re-renders the response from the run's records
in the durable ledger (agent output + the search hits it cited + whether an independent
reviewer passed it), using the pipeline's own renderer. Keeping the text in the registry
would make a second copy of a delivered analysis that could drift from the audited one; the
ledger is the authority, so the registry holds only the pointer to it.

**Kill switch.** The read verbs answer in any runtime mode — ``kill_allows`` covers
read-only status, and an operator staring at a PAUSED runtime most needs to see what it was
doing. ``/cancel`` mutates coordination state, so it goes through the same door every
mutating path uses: kill-switch checked first, refused mode-aware.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import task_registry, timeutil
from .control import command_verb, parse_count_arg, with_note
from .errors import MvpRuntimeError, OperatorBlocked, PersistenceError, TaskRegistryBlocked
from .events import stamped_event
from .task_registry import RegistryEntry, TaskRegistryStore

TASKS_COMMAND = "tasks"
HISTORY_COMMAND = "history"
RESULT_COMMAND = "result"
CANCEL_COMMAND = "cancel"
COMMANDS = frozenset({TASKS_COMMAND, HISTORY_COMMAND, RESULT_COMMAND, CANCEL_COMMAND})

REGISTRY_EVENT_TYPE = "task_registry_event.v0"

# A Telegram listing must stay readable; the operator drills in by id for the rest.
MAX_LISTED = 20
DEFAULT_HISTORY = 10
MAX_HISTORY = 50
_PREVIEW_CHARS = 70

# Rendered as the status column. The registry's own vocabulary, not a friendlier synonym —
# the word in the chat is the word in the record.
# Rendered beside the status for the one origin that is not the operator's own request: an
# assistant run listed among Thomas's would otherwise read as something he asked for.
_ORIGIN_MARK = {task_registry.AGENT_ORIGIN: "비서"}

_STATUS_MARK = {
    task_registry.QUEUED: "대기",
    task_registry.RUNNING: "실행 중",
    task_registry.DELIVERED: "완료",
    task_registry.FAILED: "실패",
    task_registry.BLOCKED: "차단",
    task_registry.CANCELLED: "취소",
}


def parse_registry_command(text: Any) -> tuple[str, str | None] | None:
    """Classify a task-console command, or return None if ``text`` is not one.

    Returns ``(action, argument)`` where ``action`` is one of ``TASKS``/``HISTORY``/
    ``RESULT``/``CANCEL``. A missing argument stays None so :func:`apply_registry_command`
    can refuse it as a typed usage error rather than guess which task was meant.
    """
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    head, _, rest = stripped.partition(" ")
    verb = command_verb(head, slash_seen=stripped.startswith("/"))
    if verb not in COMMANDS:
        return None
    return (verb.upper(), rest.strip() or None)


def _preview(text: str) -> str:
    flat = " ".join(str(text).split())
    return flat[:_PREVIEW_CHARS] + ("…" if len(flat) > _PREVIEW_CHARS else "")


def _elapsed(entry: RegistryEntry, now: str) -> str:
    """How long an open entry has been in its current state, in whole minutes/seconds.

    Deliberately not a percentage: the pipeline is a sequence of stages, not a measurable
    fraction, and a made-up progress bar would be the one number in this file that is not
    true. Elapsed time is something the registry actually knows."""
    anchor = entry.started_at or entry.submitted_at
    try:
        seconds = int((timeutil.parse_iso(now) - timeutil.parse_iso(anchor)).total_seconds())
    except (MvpRuntimeError, ValueError):
        return ""
    if seconds < 0:
        return ""
    if seconds < 60:
        return f"{seconds}초"
    return f"{seconds // 60}분"


def _short(entry_id: str) -> str:
    """The id prefix an operator can retype; ``find`` accepts any unambiguous prefix."""
    return entry_id[:12]


def _origin(entry: RegistryEntry) -> str:
    mark = _ORIGIN_MARK.get(entry.origin)
    return f" · {mark}" if mark else ""


def _format_open(entries: list[RegistryEntry], now: str) -> str:
    if not entries:
        return ("현재 진행 중이거나 대기 중인 작업이 없습니다.\n"
                "요청을 보내면 여기에 나타나고, /history 로 지난 작업을 볼 수 있습니다.")
    lines = [f"진행 중인 작업 {len(entries)}개:"]
    for entry in entries[:MAX_LISTED]:
        elapsed = _elapsed(entry, now)
        suffix = f" · {elapsed} 경과" if elapsed else ""
        lines.append(f"• [{_STATUS_MARK.get(entry.status, entry.status)}{_origin(entry)}]{suffix}\n"
                     f"  {_preview(entry.request_text)}\n"
                     f"  id: {_short(entry.registry_entry_id)}")
    if len(entries) > MAX_LISTED:
        lines.append(f"…외 {len(entries) - MAX_LISTED}개")
    return "\n".join(lines)


def _format_history(entries: list[RegistryEntry], total: int) -> str:
    if not entries:
        return "완료된 작업 기록이 없습니다."
    lines = [f"최근 완료 {len(entries)}개 (전체 {total}개):"]
    for entry in entries:
        reason = f" ({entry.last_reason_code})" if entry.last_reason_code else ""
        when = entry.finished_at or entry.submitted_at
        lines.append(f"• [{_STATUS_MARK.get(entry.status, entry.status)}{_origin(entry)}]{reason} {when}\n"
                     f"  {_preview(entry.request_text)}\n"
                     f"  id: {_short(entry.registry_entry_id)}")
    lines.append("\n결과 다시 보기: /result <id>")
    return "\n".join(lines)


def cancel_refusal(entry: Any) -> tuple[str, str] | None:
    """``(reason_code, message)`` when this entry cannot be cancelled, else None.

    ONE authority for "why not", used by three callers that used to answer differently: the
    ``/cancel`` pre-check, the same verb's lost-race path (a task that started running while
    the operator typed), and the front desk's cancel *proposal*, which only said "현재 X
    상태라 취소할 수 없습니다" and left Thomas without the verb that would actually stop it.
    """
    if getattr(entry, "status", None) == task_registry.RUNNING:
        # Deliberately not cancellable here: stopping in-flight execution is the kill
        # switch's authority, and duplicating it in a coordination verb would give the
        # operator two different answers to "how do I stop this".
        return ("TASK_ALREADY_RUNNING",
                "이미 실행 중인 작업입니다 — 실행을 멈추려면 /pause 또는 /kill 을 사용하세요.")
    if getattr(entry, "is_terminal", False):
        status = getattr(entry, "status", "?")
        return ("TASK_ALREADY_FINISHED",
                f"이미 {_STATUS_MARK.get(status, status)}로 끝난 작업입니다.")
    return None


def _raced_refusal(registry: TaskRegistryStore, entry: RegistryEntry) -> tuple[str, str]:
    """The honest reason a cancel lost its race, read back from the state that won."""
    try:
        current = registry.find(entry.registry_entry_id)
    except MvpRuntimeError:
        current = None
    refusal = cancel_refusal(current) if current is not None else None
    if refusal is not None:
        return refusal
    # The status moved but not into anything this verb explains (or it cannot be re-read):
    # say that plainly rather than invent a cause.
    return ("TASK_STATE_CHANGED",
            "취소하려는 동안 작업 상태가 바뀌었습니다 — /tasks 로 현재 상태를 확인해 주세요.")


def render_result(entry: RegistryEntry, ledger: Any) -> str | None:
    """A delivered entry's response re-rendered from the ledger, or None when the evidence is
    not there. The dispatch door uses this to answer an idempotent replay with the run's
    result (door API v2) — same renderer as `/result`, so the two never disagree."""
    return _rerender_from_ledger(entry, ledger)


def _rerender_from_ledger(entry: RegistryEntry, ledger: Any) -> str | None:
    """Re-render a delivered response from the run's records, or None if unavailable.

    Imported lazily: ``pipeline`` pulls in the whole run stack, and the console is also
    reached from paths (a plain listing) that must not pay for it.
    """
    if ledger is None or not entry.trace_id:
        return None
    from .pipeline import render_response

    # Streamed, and prescreened on the trace id: the ledger is ~21 MB on the live host and a
    # run's rows are a handful of lines in it; decoding the rest to discard them cost
    # 160–220 ms per re-render (2026-09-04), paid on every `/result` and every replay.
    #
    # The `try` wraps the LOOP, not the call. `iter_records` is a generator, so building it
    # runs no code and raises nothing; an unreadable ledger surfaces on the first `next`.
    # Guarding only the call would have caught nothing and turned a degradation into a crash.
    kinds: dict[str, Any] = {}
    try:
        for row in ledger.iter_records(trace_id=entry.trace_id):
            if isinstance(row, dict) and row.get("trace_id") == entry.trace_id:
                kind = row.get("kind")
                if isinstance(kind, str):
                    kinds[kind] = row.get("record")
    except PersistenceError:
        return None
    agent_output = kinds.get("agent_output")
    if not isinstance(agent_output, dict):
        return None
    tool_use = kinds.get("tool_use")
    hits = tool_use.get("hits") if isinstance(tool_use, dict) else None
    try:
        return render_response(
            agent_output,
            independently_validated=isinstance(kinds.get("independent_validation_result"), dict),
            search_hits=hits if isinstance(hits, list) else None,
        )
    except (KeyError, TypeError, ValueError):
        # The ledger row is not shaped like an agent output (hand-edited, or an older
        # record version). Say "cannot re-render", never a half-rendered analysis.
        return None


def apply_registry_command(
    command: tuple[str, str | None],
    *,
    operator_id: str,
    registry: TaskRegistryStore | None,
    ledger: Any = None,
    control_store: Any = None,
    now: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Execute a parsed task-console command and return ``{"reply", "action", ...}``.

    Raises ``OperatorBlocked`` for every refusal so the caller renders one typed REFUSED
    reply. A store failure is *not* swallowed here the way the pipeline's recording seam
    swallows it: an operator who asked what is running must be told the registry could not
    answer, never shown an empty list that reads as "nothing is running".
    """
    action, argument = command
    stamp = now or timeutil.utc_now_iso()

    if registry is None:
        raise OperatorBlocked(
            "REGISTRY_UNAVAILABLE", "이 채널에 작업 레지스트리가 연결되어 있지 않습니다."
        )

    try:
        entries = registry.latest()
    except (TaskRegistryBlocked, PersistenceError) as exc:
        raise OperatorBlocked(
            exc.reason_code,
            f"작업 레지스트리를 읽을 수 없습니다 ({exc.reason_code}) — 상태를 확인할 수 없습니다.",
        ) from None

    if action == "TASKS":
        # RUNNING first: "what is happening right now" is the question being asked.
        order = {task_registry.RUNNING: 0, task_registry.QUEUED: 1}
        open_entries = sorted(
            (e for e in entries if e.status in task_registry.OPEN_STATUSES),
            key=lambda e: (order.get(e.status, 9), e.submitted_at),
        )
        return {"reply": _format_open(open_entries, stamp), "action": "TASKS_LISTED",
                "count": len(open_entries)}

    if action == "HISTORY":
        # Shared with `/audit`, which had the opposite behavior on the same typo: a mistyped
        # count refused here and silently defaulted there. Both are read-only, so neither
        # denies the operator their read — the count is clamped and the substitution stated.
        limit, note = parse_count_arg(
            argument, default=DEFAULT_HISTORY, maximum=MAX_HISTORY, usage="사용법: /history [개수]",
        )
        finished = [e for e in entries if e.is_terminal]
        return {"reply": with_note(_format_history(finished[:limit], len(finished)), note),
                "action": "HISTORY_LISTED", "count": len(finished)}

    if action == "RESULT":
        entry = _require_entry(registry, argument, "/result <id>")
        if not entry.is_terminal:
            raise OperatorBlocked(
                "TASK_NOT_FINISHED",
                f"이 작업은 아직 {_STATUS_MARK.get(entry.status, entry.status)} 상태입니다 — "
                "완료되면 결과를 보내드립니다.",
            )
        if entry.status != task_registry.DELIVERED:
            reason = f" ({entry.last_reason_code})" if entry.last_reason_code else ""
            raise OperatorBlocked(
                "NO_DELIVERABLE",
                f"이 작업은 {_STATUS_MARK.get(entry.status, entry.status)}로 끝나 전달할 결과가 "
                f"없습니다{reason}.",
            )
        rendered = _rerender_from_ledger(entry, ledger)
        if rendered is None:
            # Honest refusal: the entry says DELIVERED, but the evidence to re-render it is
            # not in this ledger (different machine, pruned, or a malformed row).
            raise OperatorBlocked(
                "RESULT_UNAVAILABLE",
                "이 작업은 완료되었지만 원장에서 결과를 다시 만들 수 없습니다 "
                f"(trace: {entry.trace_id or '없음'}).",
            )
        header = f"[다시 보내기] {_preview(entry.request_text)}\n" + "-" * 20 + "\n"
        return {"reply": header + rendered, "action": "RESULT_RESENT",
                "registry_entry_id": entry.registry_entry_id, "trace_id": entry.trace_id}

    if action == "CANCEL":
        entry = _require_entry(registry, argument, "/cancel <id>")
        if control_store is None:
            raise OperatorBlocked(
                "KILL_STATE_UNAVAILABLE", "control 상태를 확인할 수 없어 취소를 거부합니다."
            )
        # Kill-switch first — the same door R8 tool_write, R6 scheduler_execution and R10
        # consume go through. Cancelling mutates coordination state, and kill_allows is
        # read-only only.
        state = control_store.load()
        if not state.execution_allowed:
            raise OperatorBlocked(
                state.refusal_reason_code(),
                f"런타임이 {state.mode} 상태입니다 — 취소는 ACTIVE일 때만 가능합니다 "
                "(/resume 후 다시 시도하세요).",
            )
        refusal = cancel_refusal(entry)
        if refusal is not None:
            raise OperatorBlocked(*refusal)
        try:
            cancelled = registry.transition(
                entry.registry_entry_id, task_registry.CANCELLED,
                now=stamp, reason_code="OPERATOR_CANCELLED",
            )
        except TaskRegistryBlocked as exc:
            # The check above is unlocked and `transition` re-checks under the store lock, so
            # a task that started running in between lands here. That is not a storage
            # failure, and saying "취소를 기록하지 못했습니다" about it described the wrong
            # event — the truthful answer is the same one the pre-check would have given a
            # moment later, so ask the same helper about the state that actually won.
            if exc.reason_code == "TRANSITION_INVALID":
                raise OperatorBlocked(*_raced_refusal(registry, entry)) from None
            raise OperatorBlocked(exc.reason_code, f"취소를 기록하지 못했습니다: {exc.reason}") from None
        except PersistenceError as exc:
            raise OperatorBlocked(exc.reason_code, f"취소를 기록하지 못했습니다: {exc.reason}") from None
        if ledger is not None:
            try:
                ledger.append_block(stamped_event(
                    REGISTRY_EVENT_TYPE, action="task_cancelled",
                    registry_entry_id=cancelled.registry_entry_id,
                    actor=operator_id, created_at=stamp,
                ))
            except PersistenceError:
                # The cancellation is already durable in the registry; report it rather
                # than pretend the whole verb failed.
                return {"reply": f"취소했습니다: {_short(cancelled.registry_entry_id)}\n"
                                 "(경고: 취소 이벤트를 원장에 기록하지 못했습니다)",
                        "action": "TASK_CANCELLED",
                        "registry_entry_id": cancelled.registry_entry_id}
        return {"reply": f"취소했습니다: {_short(cancelled.registry_entry_id)}\n"
                         f"  {_preview(cancelled.request_text)}",
                "action": "TASK_CANCELLED", "registry_entry_id": cancelled.registry_entry_id}

    raise OperatorBlocked("UNKNOWN_COMMAND", f"알 수 없는 작업 명령입니다: {action}")


def _require_entry(registry: TaskRegistryStore, argument: str | None, usage: str) -> RegistryEntry:
    """Resolve the id argument to exactly one entry, or refuse with a typed reason.

    A registry id (or an unambiguous prefix of one), or — door API v2 — a run's own
    ``task_…``/``trace_…`` id exactly, which is what the dispatch reply hands the assistant."""
    if not argument:
        raise OperatorBlocked("USAGE", f"사용법: {usage} — 먼저 /tasks 또는 /history 로 id를 확인하세요.")
    token = argument.split()[0]
    try:
        entry = registry.find(token)
    except (TaskRegistryBlocked, PersistenceError) as exc:
        raise OperatorBlocked(exc.reason_code, exc.reason) from None
    if entry is None:
        raise OperatorBlocked(
            "ENTRY_NOT_FOUND",
            f"'{token}' 작업을 찾을 수 없습니다 — /tasks 또는 /history 로 id를 확인하세요.",
        )
    return entry
