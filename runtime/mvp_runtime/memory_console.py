"""Operator memory console — list working-memory candidates and promote one, over the
already-verified control channel (Telegram or the local host CLI).

This is the *convenience door* onto the exact same promotion the offline
``scripts/promote_memory_candidate.py`` performs — same building blocks, same guards,
same audit event — exposed as a fourth control-channel command family beside the console
(``control``), approval (``approval``) and feedback (``operator_feedback``) verbs. The R4
identity gate in ``handle_operator_message`` has already run before this module is ever
consulted, so only the registered Thomas can reach it — which is precisely the operator
identity the promotion's EXECUTE_AND_REPORT "report" requires. No new contract, schema,
registry, or gate: promotion is already modelled (EXECUTE_AND_REPORT, ``OTHER`` /
``MEMORY_PROMOTED`` via :func:`audit.build_promotion_audit`), and this just routes to it.

Two verbs, sharing the console tokenizer (``control.command_verb``) so the channels can
never drift on what counts as a command:

- ``/memory`` (or ``/memory list``) — read-only listing of the LIVE candidates eligible
  for promotion (status CANDIDATE, in the working-memory scope, not expired), most recent
  first. Read-only, so (like ``/status`` and ``/feedback``) it is answered in ANY runtime
  mode; a PAUSED/KILLED runtime must still let Thomas see what is promotable.
- ``/promote <candidate_id> <reason>`` — the EXECUTE_AND_REPORT promotion. Kill-switch
  bound (checked first; a PAUSED/KILLED runtime refuses — promotion mutates VALIDATED
  memory and ``kill_allows`` is read-only only), latest-wins candidate lookup, expired
  candidate refused, and every partial failure after the durable write reported as exactly
  what it is (PROMOTED_NOT_RETIRED / PROMOTED_UNAUDITED) — the same hardening the offline
  script carries (QA wave 6d). The ``<reason>`` is mandatory: it is the "report" half of
  EXECUTE_AND_REPORT, so a promotion with no reason is refused, never defaulted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import memory, timeutil
from .audit import build_promotion_audit
from .control import command_verb
from .errors import MvpRuntimeError, OperatorBlocked
from .working_memory import WorkingMemoryStore, find_candidate, mark_promoted

LIST_COMMAND = "memory"
PROMOTE_COMMAND = "promote"
_LIST_SUBVERB = "list"

# A Telegram listing must stay readable: cap the candidates shown and note the remainder,
# rather than emitting a wall of near-duplicate lines (the store accumulates one candidate
# per finding per run, so repeated analyses pile up fast). The operator promotes by id, so
# the newest are the ones most likely wanted; older ones are still promotable by id.
MAX_LISTED = 20
_PREVIEW_CHARS = 80


def parse_memory_command(text: Any) -> tuple[str, str | None, str | None] | None:
    """Classify a memory-console command, or return None if ``text`` is not one.

    Returns ``(action, candidate_id, reason)`` where ``action`` is ``"LIST"`` or
    ``"PROMOTE"``. For LIST both trailing fields are None. For PROMOTE either field may be
    None when the operator omitted it — :func:`apply_memory_command` turns that into a typed
    usage refusal rather than guessing. Same tokenizer as the console/approval/feedback
    parsers (leading slash optional, ``@botname`` menu suffix stripped)."""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    head, _, rest = stripped.partition(" ")
    verb = command_verb(head, slash_seen=stripped.startswith("/"))
    rest = rest.strip()
    if verb == LIST_COMMAND:
        # `/memory`, `/memory list`, or `/memory <anything>` all list — listing is the only
        # read verb, so an unrecognized tail is treated as "just list" rather than refused.
        return ("LIST", None, None)
    if verb == PROMOTE_COMMAND:
        candidate_id, _, reason = rest.partition(" ")
        candidate_id = candidate_id.strip() or None
        reason = reason.strip() or None
        return ("PROMOTE", candidate_id, reason)
    return None


def _live_candidates(store: WorkingMemoryStore, now: str) -> list[dict[str, Any]]:
    """The candidates eligible for promotion right now: latest entry per id is a live
    (un-promoted, un-expired) CANDIDATE in the working-memory scope. Latest-wins per id so a
    candidate already retired by a PROMOTED marker (or superseded) drops out — the same
    liveness :func:`working_memory.find_candidate` enforces for a single id, applied to all."""
    latest: dict[str, dict[str, Any]] = {}
    for entry in store.read_all():
        if (isinstance(entry, dict)
                and isinstance(entry.get("candidate_id"), str)
                and entry.get("scope") == memory.CANDIDATE_SCOPE):
            latest[entry["candidate_id"]] = entry
    live = [
        e for e in latest.values()
        if e.get("status") == memory.CANDIDATE_STATUS and not memory.is_expired(e, now)
    ]
    # Most recent first; entries without a created_at sort last (empty string).
    live.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    return live


def _format_listing(live: list[dict[str, Any]]) -> str:
    if not live:
        return ("승격 가능한 working-memory 후보가 없습니다.\n"
                "(작업을 실행하면 재사용 후보가 쌓이고, 여기에서 승격할 수 있습니다.)")
    shown = live[:MAX_LISTED]
    lines = [f"승격 가능한 후보 {len(live)}개" + (f" (최근 {len(shown)}개 표시):" if len(live) > len(shown) else ":")]
    for cand in shown:
        content = str(cand.get("content", "")).replace("\n", " ")
        preview = content[:_PREVIEW_CHARS] + ("…" if len(content) > _PREVIEW_CHARS else "")
        lines.append(f"• {cand['candidate_id']}\n  {preview}")
    lines.append("\n승격: /promote <candidate_id> <사유>")
    return "\n".join(lines)


def apply_memory_command(
    command: tuple[str, str | None, str | None],
    *,
    operator_id: str,
    working_memory: WorkingMemoryStore | None,
    ledger: Any,
    control_store: Any,
    now: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Execute a parsed memory-console command and return ``{"reply", "action", ...}``.

    Raises ``OperatorBlocked`` for every refusal (no store wired, empty/expired/unknown
    candidate, kill switch, missing reason) so the caller renders one typed REFUSED reply,
    and lets a store ``PersistenceError`` propagate — a promotion Thomas asked for that was
    NOT durably written or audited must be reported as a failure, never confirmed.
    """
    action, candidate_id, reason = command
    stamp = now or timeutil.utc_now_iso()

    if working_memory is None:
        raise OperatorBlocked(
            "MEMORY_UNAVAILABLE", "이 채널에 working-memory 저장소가 연결되어 있지 않습니다."
        )

    if action == "LIST":
        # Read-only: no kill-switch gate, answered in any runtime mode.
        live = _live_candidates(working_memory, stamp)
        return {"reply": _format_listing(live), "action": "MEMORY_LISTED", "count": len(live)}

    # --- PROMOTE (EXECUTE_AND_REPORT) ---------------------------------------------------
    if not candidate_id:
        raise OperatorBlocked(
            "USAGE", "사용법: /promote <candidate_id> <사유> — candidate_id가 필요합니다. "
            "먼저 /memory 로 후보 목록을 확인하세요."
        )
    if not reason:
        # The reason IS the EXECUTE_AND_REPORT "report" — never defaulted.
        raise OperatorBlocked(
            "MISSING_REASON", "승격에는 사유가 필요합니다: /promote <candidate_id> <사유>"
        )
    if ledger is None:
        # EXECUTE_AND_REPORT with no ledger cannot produce its report — fail closed rather
        # than mutate validated memory unaudited.
        raise OperatorBlocked(
            "MEMORY_UNAVAILABLE", "감사 원장이 연결되어 있지 않아 승격을 기록할 수 없습니다."
        )
    if control_store is None:
        # Promotion mutates validated memory; without the control state the kill switch
        # cannot be honored, so refuse (fail-closed) exactly as if it were not ACTIVE.
        raise OperatorBlocked(
            "KILL_STATE_UNAVAILABLE", "control 상태를 확인할 수 없어 승격을 거부합니다."
        )

    # Kill-switch first (kill_allows is read-only only) — the same door every mutating path
    # goes through (R8 tool_write, R6 scheduler_execution, R10 consume).
    state = control_store.load()
    if not state.execution_allowed:
        raise OperatorBlocked(
            state.refusal_reason_code(),
            f"런타임이 {state.mode} 상태입니다 — 승격은 validated 메모리를 변경하므로 "
            "ACTIVE가 아닐 때는 거부됩니다. /resume 후 다시 시도하세요.",
        )

    # THE shared latest-wins, live-CANDIDATE-only lookup — identical resolution to the R9
    # ask and the R10 spend, so this door can never promote a copy another door rejects.
    match = find_candidate(working_memory, candidate_id)
    if match is None:
        raise OperatorBlocked(
            "CANDIDATE_GONE",
            f"'{candidate_id}' 후보를 찾을 수 없습니다 (없거나, 이미 승격되었거나, 만료됨). "
            "/memory 로 현재 목록을 확인하세요.",
        )
    if memory.is_expired(match, stamp):
        raise OperatorBlocked(
            "CANDIDATE_EXPIRED",
            f"후보가 {match.get('expires_at')}에 만료되어 승격할 수 없습니다.",
        )

    # Build the audit event BEFORE persisting anything: a promotion that cannot be audited
    # (e.g. incomplete origin provenance) fails closed here with nothing yet written.
    try:
        validated = memory.promote_candidate(
            match, promoted_by=operator_id, reason=reason, now=stamp
        )
        audit_event, _sha = build_promotion_audit(
            match, validated, promoted_by=operator_id, reason=reason,
            now=stamp, previous_hash=ledger.last_audit_hash(), repo_root=repo_root,
        )
        working_memory.append_validated([validated])
    except MvpRuntimeError as exc:
        # promote_candidate / build_promotion_audit refusals are typed; surface them as-is.
        raise OperatorBlocked(exc.reason_code, exc.reason) from None

    # Write order matches the offline script: validated entry -> PROMOTED retirement marker
    # -> audit event. Each later leg failing is reported as what it IS (the promotion is
    # already durable), never masked as "nothing happened".
    try:
        mark_promoted(working_memory, match,
                      validated_memory_id=validated["validated_memory_id"], now=stamp)
    except MvpRuntimeError as exc:
        raise OperatorBlocked(
            "PROMOTED_NOT_RETIRED",
            f"승격은 기록되었으나 후보의 PROMOTED 마커 기록에 실패했습니다 ({exc.reason_code}); "
            "후보가 다시 승격 가능한 상태로 남아 있습니다 — 확인이 필요합니다.",
        ) from None
    try:
        ledger.append_audit_events([audit_event])
    except MvpRuntimeError as exc:
        raise OperatorBlocked(
            "PROMOTED_UNAUDITED",
            f"승격은 기록되었으나 감사 이벤트 기록에 실패했습니다 ({exc.reason_code}); "
            "감사되지 않은 validated 메모리 변경이 디스크에 있습니다 — 원장을 먼저 확인하세요.",
        ) from None

    reply = (
        f"승격 완료: {candidate_id}\n"
        f"→ {validated['validated_memory_id']} (VALIDATED, {validated['scope']})\n"
        f"사유: {validated['promotion_reason']}\n"
        f"감사: {audit_event['audit_event_id']} (OTHER / MEMORY_PROMOTED)\n"
        "이제 관련된 다음 작업에 신뢰 지식([V#])으로 반영됩니다."
    )
    return {
        "reply": reply, "action": "MEMORY_PROMOTED",
        "validated_memory_id": validated["validated_memory_id"],
        "audit_event_id": audit_event["audit_event_id"],
    }
