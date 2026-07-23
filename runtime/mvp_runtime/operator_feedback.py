"""E1 Operator feedback capture — Thomas's verdict on a delivered run, durably recorded.

The runtime's ground-truth gap: the ledger records what ran and whether validation
PASSED, but "was the answer actually useful" lives only in Thomas's head. This module
gives that judgment a durable, append-only home so later evaluation (E2) and
preference distillation (E3) have a real corpus instead of proxy metrics.

Scope is deliberately the narrowest capture that closes the gap:

- ``/feedback <one-line verdict or note>`` on the ALREADY-verified control channel —
  the R4 identity gate has run before this module is ever consulted, so only the
  registered Thomas can leave feedback (the same property the R9 decision path relies
  on). Parsed as a third command family beside the console and approval verbs, sharing
  their tokenizer (``control.command_verb``) so the channels can never drift.
- Feedback binds to the LAST DELIVERED completed run: the loop records a small local
  pointer (``last_delivered.json``, atomic write, per-machine like the Telegram offset)
  after a COMPLETED reply is actually sent. No pointer -> typed refusal, never a guess
  about which run Thomas meant (fail-closed). Explicit task addressing is deferred
  until the one-target default proves insufficient.
- The event is a self-hashed ``operator_feedback.v0`` ``stamped_event`` on its own
  append-only ledger stream (``feedback_events.jsonl``) — the ``control_events`` /
  ``memory_events`` precedent: an operator-console event lives on its own stream, not
  the run's audit chain. Recording feedback is an INTERNAL_READ-tier annotation of past
  work, not a new execution, so (like ``/approve``) it is answered in any runtime mode —
  a PAUSED runtime must still let Thomas judge what it already delivered.
- Capture only: nothing reads feedback back into planning here. Evaluation (E2) and
  distillation (E3) are separate increments with their own approvals.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import timeutil
from .control import command_verb
from .errors import OperatorBlocked
from .events import stamped_event
from .paths import repo_root as _repo_root

FEEDBACK_EVENT_TYPE = "operator_feedback.v0"
COMMAND = "feedback"

# The one delivery pointer, per-machine and gitignored beside the Telegram offset. A
# single file (not per-chat) because the identity gate admits exactly one registered
# private chat — there is only ever one conversation this could refer to.
LAST_DELIVERED_REL = ".runtime_governance_state/last_delivered.json"

# A leading verdict token classifies the feedback; anything else is an unclassified
# NOTE (still worth recording — a note is corpus, a forced GOOD/BAD would be a guess).
_VERDICT_TOKENS = {
    "good": "GOOD", "좋음": "GOOD", "좋아": "GOOD", "굿": "GOOD",
    "bad": "BAD", "나쁨": "BAD", "별로": "BAD",
}
VERDICT_NOTE = "NOTE"


def parse_feedback_command(text: Any) -> str | None:
    """Return the payload after ``/feedback`` (possibly empty), or None if ``text`` is
    not a feedback command. Same tokenizer as the console/approval parsers (leading
    slash optional, ``@botname`` suffix stripped)."""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    head, _, rest = stripped.partition(" ")
    if command_verb(head, slash_seen=stripped.startswith("/")) != COMMAND:
        return None
    return rest.strip()


def split_verdict(payload: str) -> tuple[str, str]:
    """``(verdict, comment)`` from the payload: a recognized leading token classifies
    it (GOOD/BAD), otherwise the whole payload is an unclassified NOTE."""
    head, _, rest = payload.partition(" ")
    verdict = _VERDICT_TOKENS.get(head.strip().lower())
    if verdict is None:
        return VERDICT_NOTE, payload
    return verdict, rest.strip()


def record_delivery(trace_id: str, *, now: str, repo_root: Path | None = None) -> None:
    """Persist the last-delivered pointer atomically (the Telegram-offset pattern).

    Called by the loop only AFTER a COMPLETED reply was actually sent, so feedback can
    never bind to a run Thomas has not seen. Raises ``OperatorBlocked`` on a write
    failure; the caller treats the pointer as a courtesy (best-effort, like the ack) —
    losing it degrades ``/feedback`` to an honest NO_FEEDBACK_TARGET refusal, which
    must not cost the already-delivered reply or the rest of the batch."""
    root = repo_root if repo_root is not None else _repo_root()
    path = root / LAST_DELIVERED_REL
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"trace_id": trace_id, "delivered_at": now}), encoding="utf-8"
        )
        os.replace(tmp, path)
    except OSError as exc:
        raise OperatorBlocked(
            "DELIVERY_POINTER_PERSIST_FAILED", f"could not persist the delivery pointer: {exc}"
        ) from None


def load_last_delivered(repo_root: Path | None = None) -> dict[str, Any] | None:
    """The last-delivered pointer, or None when nothing was delivered yet. A malformed
    file fails closed (the operator deletes/fixes it) rather than binding feedback to
    garbage."""
    path = (repo_root if repo_root is not None else _repo_root()) / LAST_DELIVERED_REL
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        trace_id = data["trace_id"]
        delivered_at = data["delivered_at"]
    except (OSError, ValueError, KeyError, TypeError):
        raise OperatorBlocked(
            "FEEDBACK_TARGET_UNREADABLE",
            f"delivery pointer at {LAST_DELIVERED_REL} is unreadable; fix or delete it",
        ) from None
    if not (isinstance(trace_id, str) and trace_id and isinstance(delivered_at, str) and delivered_at):
        raise OperatorBlocked(
            "FEEDBACK_TARGET_UNREADABLE",
            f"delivery pointer at {LAST_DELIVERED_REL} needs non-empty trace_id and delivered_at",
        )
    return {"trace_id": trace_id, "delivered_at": delivered_at}


def build_feedback_event(
    *,
    trace_id: str,
    delivered_at: str,
    verdict: str,
    comment: str,
    operator_id: str,
    now: str,
) -> dict[str, Any]:
    """A tamper-evident operator-feedback event for the durable ledger."""
    return stamped_event(
        FEEDBACK_EVENT_TYPE, action="operator_feedback",
        trace_id=trace_id, delivered_at=delivered_at,
        verdict=verdict, comment=comment,
        operator_id=operator_id, created_at=now,
    )


def apply_feedback(
    payload: str,
    *,
    operator_id: str,
    store: Any,
    now: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Record one feedback event against the last delivered run.

    ``store`` is a ``LedgerStore`` (needs ``append_feedback_event``) — duck-typed like
    the memory/control seams. Raises ``OperatorBlocked`` for every refusal
    (no ledger wired, empty payload, no delivered run, unreadable pointer) and lets the
    store's ``PersistenceError`` propagate: a feedback Thomas typed that was NOT
    durably recorded must be reported as a failure, never confirmed."""
    if store is None:
        raise OperatorBlocked(
            "FEEDBACK_UNAVAILABLE", "no ledger is wired on this channel; feedback cannot be recorded"
        )
    if not payload:
        raise OperatorBlocked(
            "EMPTY_FEEDBACK", "usage: /feedback <good|bad|한줄평> — 내용이 비어 있습니다"
        )
    target = load_last_delivered(repo_root)
    if target is None:
        raise OperatorBlocked(
            "NO_FEEDBACK_TARGET", "아직 전달된 분석 결과가 없어 피드백을 연결할 대상이 없습니다"
        )
    verdict, comment = split_verdict(payload)
    event = build_feedback_event(
        trace_id=target["trace_id"], delivered_at=target["delivered_at"],
        verdict=verdict, comment=comment, operator_id=operator_id,
        now=now or timeutil.utc_now_iso(),
    )
    store.append_feedback_event(event)
    reply = (
        f"피드백을 기록했습니다 ({verdict}) — 대상 실행: {target['trace_id']}."
    )
    return {"reply": reply, "event": event, "verdict": verdict}
