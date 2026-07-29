"""R4.1 Operator control channel — identity gate + message handling (foundation).

Thomas submits a request over a control channel (Telegram private 1:1) and gets the
analysis back. This module is the channel-neutral core: it verifies an inbound message
against the **canonical Governance Policy control-channel rules** and, only for a verified
operator, runs the existing pipeline and returns a reply.

Governance (``governance/GOVERNANCE_POLICY.yaml`` ``control_channel``): the primary channel
is ``TELEGRAM_PRIVATE_1_TO_1``, the required approver is Thomas, and both a registered user
id and a registered private-chat id must match. Group/channel messages, a different user,
and forwarded messages are invalid sources. This module enforces exactly those identity
rules and **fails closed** — an unverified message never runs a task.

**This module does open sockets** — that sentence used to read "network-free by construction
… nothing here opens a socket … emergency controls are also later", written when both were
true and left in place through the increments that made them false. ``TelegramChannel`` below
is the real long-poll adapter, and every console verb family routes through
``handle_operator_message``. Anyone reasoning about this file's egress from its own docstring
was reasoning from a promise it had stopped keeping.

What is actually true, and is the property that matters:

- ``handle_operator_message`` and ``verify_control_channel`` are pure message handling — they
  open nothing, which is why the whole identity gate is testable with no network.
- The network lives in exactly one place, ``TelegramChannel``, and it is only ever *reachable*
  through ``select_operator_channel``, which returns ``MockOperatorChannel`` unless the
  Safety-Flag Gate authorized ``network_access`` for the ``telegram`` provider. The env var
  alone opens nothing; the capable object is not constructed before the gate opens.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from . import (
    approval, control, domain_console, frontdesk, memory_console, operator_feedback,
    registry_console, safety_gate, task_registry, timeutil,
)
from .audit import build_approval_decision_audit, build_audit_gap_record
from .budgets import clip_for_prompt
from .control import ControlStore
from .errors import (
    ApprovalBlocked, AuditError, ControlBlocked, MvpRuntimeError, OperatorBlocked, PersistenceError,
)
from .events import stamped_event
from .paths import repo_root as _repo_root
from .pipeline import run_task
from .safety_gate import NETWORK_ACCESS, Authorization
from .store import LedgerStore
from .worker import Provider

# Local, per-machine (gitignored) registration of the single authorized operator — like the
# Core pointer and safety-flag activation, this is machine state, not shared source.
REGISTRATION_REL = ".runtime_governance_state/operator_registration.json"
# The Telegram getUpdates cursor, persisted so a restart resumes AFTER the messages it
# already fetched instead of re-fetching (and re-executing) up to 24h of updates.
OFFSET_STATE_REL = ".runtime_governance_state/telegram_offset.json"
PRIMARY_CHANNEL = "telegram_private"
REQUIRED_APPROVER = "Thomas"

# Opting into the real network-backed operator channel + its backend. Like the model
# provider and search tool, the env var alone is NOT sufficient: the Safety-Flag Gate must
# authorize network_access before a network-capable channel is ever built.
OPERATOR_CHANNEL_ENV = "MVP_OPERATOR_CHANNEL"
TELEGRAM = "telegram"
# The control channel crosses the network but never invokes a model, so it needs only the
# network_access safety flag (not model_invocation).
_NETWORK_FLAGS = (NETWORK_ACCESS,)

# R7.1: a request whose FIRST token is one of these is marked important — its task is
# intaken at HIGH priority, which under the "auto" validation policy adds the independent
# reviewer to that request. Matched case-insensitively as a standalone leading token.
IMPORTANT_MARKERS = ("!중요", "!important")

# 8.5: a leading kind marker routes the request to the Role whose capabilities that kind
# needs. Same shape as the importance marker and parsed by the same loop — one marker parser,
# so the empty-request and hidden-command guards below cannot apply to one marker and not the
# other. The marker names a KIND, never a Role: `planner` maps kinds to capabilities and the
# Role Registry decides who covers them.
#
# Only kinds with a Korean word worth typing are aliased; the canonical name always works, so
# this table adds convenience and never adds a kind. A kind here that `planner` does not know
# would be caught by `test_every_operator_kind_marker_is_a_real_kind` rather than at run time.
KIND_MARKERS = {
    "!번역": "translation", "!translate": "translation",
    "!조사": "research", "!research": "research",
    "!분석": "analysis", "!analysis": "analysis",
    "!콘텐츠": "content", "!content": "content",
    "!개발": "development", "!development": "development",
}

# Verbs that CHANGE STATE, across every command family — the ones that must carry a slash on a
# chat channel (see the gate in handle_operator_message). Read-only verbs (status, audit,
# recovery, memory, tasks, history, result, feedback) are deliberately absent: answering one of
# those to a prose message is harmless, so narrowing them would only add friction.
_MUTATING_VERBS = frozenset({
    "kill", "pause", "resume", "stop", "stop_task",   # console
    "approve", "reject",                              # approval decisions
    "promote",                                        # memory -> VALIDATED
    "cancel",                                         # queue entry
})

# A long reply is sent as several numbered parts; when some of them land and a later one
# fails, the outcome is neither "delivered" nor "not delivered". This is the third answer,
# so no caller has to describe a partial delivery as a total loss.
PARTIAL_DELIVERY_REASON = "CHANNEL_PARTIAL_DELIVERY"

# EVERY verb this channel answers, and the governance authority that permits it.
#
# The drift gate used to cover only `control.COMMANDS` against the policy's
# `emergency_controls_allowed`, so the four families added since (approval, memory, registry,
# feedback) grew on the same verified door with no governance edit anywhere and nothing that
# would notice a fifth. Adding those verbs to `emergency_controls_allowed` would be the WRONG
# fix: that list is the *emergency* console grant, and putting `/tasks` in it would widen an
# emergency authority to cover ordinary coordination reads. So the completeness gate lives
# here — one inventory of the whole door — and the test asserts it both ways: no verb without a
# named authority, no named authority without a verb. A new family fails the gate until its
# author writes down what permits it.
_EMERGENCY_GRANT = "policy:control_channel.local_operator_console.emergency_controls_allowed"
CHANNEL_VERB_AUTHORITY: dict[str, str] = {
    # control.py — the emergency console. Every one of these is named in the policy list, which
    # the gate still checks verb-by-verb (that is the /resume drift this started as).
    "status": _EMERGENCY_GRANT,
    "pause": _EMERGENCY_GRANT,
    "kill": _EMERGENCY_GRANT,
    "resume": _EMERGENCY_GRANT,
    "stop": _EMERGENCY_GRANT,
    "audit": _EMERGENCY_GRANT,
    "recovery": _EMERGENCY_GRANT,
    # approval.py — R9. The policy models the ask/answer lifecycle itself, and requires exactly
    # this channel's verified identity as the verification (`approval_lifetime`, and
    # `control_channel.explicit_approval_expression_required`).
    "approve": "policy:approval_lifetime + control_channel.explicit_approval_expression_required",
    "reject": "policy:approval_lifetime + control_channel.explicit_approval_expression_required",
    # memory_console.py — R5. Listing is an INTERNAL_READ; promotion is the
    # SENSITIVE_MEMORY_GOVERNANCE (P4) action, which is APPROVAL_REQUIRED and reaches Thomas
    # only through this identity gate.
    "memory": "policy:permission_model INTERNAL_READ (ALLOW)",
    "promote": "policy:permission_model SENSITIVE_MEMORY_GOVERNANCE (APPROVAL_REQUIRED)",
    # registry_console.py — F1 task coordination. The three reads ride `kill_allows:
    # read_only_status`; /cancel mutates coordination state only (no effect outside the queue)
    # and is kill-switch bound like every other mutating door.
    "tasks": "policy:kill_switch.kill_allows read_only_status",
    "history": "policy:kill_switch.kill_allows read_only_status",
    "result": "policy:kill_switch.kill_allows read_only_status",
    "cancel": "policy:permission_model INTERNAL_MODIFY of runtime coordination state",
    # operator_feedback.py — E1. Recording Thomas's verdict on a delivered run; the operator
    # identity IS the authority, and the record is an append-only internal note.
    "feedback": "policy:permission_model INTERNAL_WRITE of operator feedback (ALLOW)",
    # domain_console.py — the crypto/prediction read verbs. Two authorities are named because
    # two questions are being answered: INTERNAL_READ is what permits reading the domain state
    # at all, and `kill_allows: read_only_status` is what lets these answer while the runtime
    # is halted — which is exactly when a board is most worth reading. Nothing here mutates,
    # holds a store, or can reach the order path (asserted by an import test), so there is no
    # third authority to name: the money path's permission is P5 FINANCIAL_APPROVED_TRADING_USE
    # and no verb on this channel is anywhere near it.
    "crypto": "policy:permission_model INTERNAL_READ (ALLOW) + kill_switch.kill_allows read_only_status",
    "pred": "policy:permission_model INTERNAL_READ (ALLOW) + kill_switch.kill_allows read_only_status",
}


@dataclass(frozen=True)
class OperatorIdentity:
    """The one registered operator whose messages the runtime will act on.

    ``operator_id`` / ``chat_id`` are the registered Telegram user id and private-chat id
    (strings; identifiers, never secrets). ``approver`` is the required approver name."""

    operator_id: str
    chat_id: str
    approver: str = REQUIRED_APPROVER


@dataclass(frozen=True)
class InboundMessage:
    """One inbound operator message, already received from the channel adapter."""

    text: str
    sender_id: str
    chat_id: str
    chat_type: str = "private"          # private | group | channel
    is_forwarded: bool = False
    channel: str = PRIMARY_CHANNEL
    received_at: str | None = None


@dataclass(frozen=True)
class OperatorReply:
    """The reply to send back, plus whether a task actually ran."""

    text: str
    accepted: bool
    status: str                          # ACCEPTED result status, or REFUSED
    reason_code: str | None = None
    trace_id: str | None = None
    # F1: the coordination entry this reply is about — set when a request was queued, so
    # the caller can tell Thomas which id to follow with /tasks or /result.
    registry_entry_id: str | None = None


def load_operator_registration(repo_root: Path | None = None) -> OperatorIdentity:
    """Load the local operator registration. Fail-closed if missing/malformed — with no
    registered operator the runtime cannot verify anyone, so it acts for no one."""
    root = repo_root if repo_root is not None else _repo_root()
    path = root / REGISTRATION_REL
    if not path.is_file():
        raise OperatorBlocked(
            "REGISTRATION_MISSING",
            f"no operator registration at {REGISTRATION_REL}; the control channel is inactive (fail-closed)",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OperatorBlocked("REGISTRATION_MALFORMED", f"operator registration is unreadable: {exc}") from exc
    operator_id = data.get("operator_id")
    chat_id = data.get("chat_id")
    if not (isinstance(operator_id, str) and operator_id and isinstance(chat_id, str) and chat_id):
        raise OperatorBlocked("REGISTRATION_MALFORMED", "operator registration needs non-empty operator_id and chat_id")
    approver = data.get("approver", REQUIRED_APPROVER)
    return OperatorIdentity(operator_id=operator_id, chat_id=chat_id, approver=str(approver))


def verify_control_channel(message: InboundMessage, registration: OperatorIdentity) -> None:
    """Enforce the canonical control-channel identity rules. Raises ``OperatorBlocked``
    (fail-closed) on any mismatch; returns None when the message is a genuine 1:1 message
    from the registered operator."""
    if message.channel != PRIMARY_CHANNEL or message.chat_type != "private":
        raise OperatorBlocked("NOT_PRIVATE_CHANNEL", "only the Telegram private 1:1 control channel is accepted")
    if message.is_forwarded:
        raise OperatorBlocked("FORWARDED_MESSAGE", "forwarded messages are not a valid control-channel source")
    if not isinstance(message.sender_id, str) or message.sender_id != registration.operator_id:
        raise OperatorBlocked("UNREGISTERED_USER", "sender is not the registered operator")
    if not isinstance(message.chat_id, str) or message.chat_id != registration.chat_id:
        raise OperatorBlocked("CHAT_NOT_REGISTERED", "message is not in the registered private chat")


def handle_operator_message(
    message: InboundMessage,
    *,
    registration: OperatorIdentity,
    provider: Provider | None = None,
    search_tool: Any | None = None,
    working_memory: Any | None = None,
    programization: Any | None = None,
    now: str | None = None,
    store: LedgerStore | None = None,
    control_store: ControlStore | None = None,
    approval_store: Any | None = None,
    registry: Any | None = None,
    frontdesk_provider: Provider | None = None,
    independent_validation: bool | str = False,
    validator_provider: Provider | None = None,
    repo_root: Path | None = None,
    ack: Any | None = None,
) -> OperatorReply:
    """Verify an inbound operator message and, only if it is from the registered operator,
    handle it. An unverified message is refused with a generic reason and no task runs. Never
    raises for a fail-closed condition — those become a REFUSED reply.

    ``ack`` (optional, ``Callable[[str], None]``) is called with a short "received, working"
    notice at exactly one point: on the **inline path only** — after every refusal path has
    passed and immediately before ``run_task`` runs. An inline run holds the channel for the
    length of a model call, and to the operator that silence was indistinguishable from a dead
    service. The ack is a ``CONTROL_CHANNEL_RESPONSE`` (ALLOW) on the already-verified channel,
    and it is **best-effort**: a failed ack send must never cost the run itself, so failures
    are swallowed here rather than propagated.

    **With a ``registry`` wired — which every deployment has since F1 — ``ack`` never fires**,
    because the request is QUEUED and the queue receipt IS the immediate reply. So this
    parameter covers only the registry-less inline mode (the library/pipeline calling mode the
    suite uses, and any deployment that turns the queue off). It is kept rather than deleted
    because that mode is real and would otherwise answer a request with nothing at all until
    the model returned; the loop passes it unconditionally so turning the registry off cannot
    also silently turn the notice off.

    When ``control_store`` is provided, an emergency-console command (``/status`` ``/pause``
    ``/kill`` ``/resume`` ``/stop <task_id>``) is handled as a control action rather than a
    task, and a task request is refused while the runtime is PAUSED or KILLED. ``/resume`` is
    accepted here only because the message already passed the operator identity gate
    (``resume_requires_thomas_authentication``). ``working_memory`` (opt-in) is shared with the
    run so the operator channel accumulates and reuses working memory like the one-shot CLI.

    When ``approval_store`` is provided, ``/approve <id>`` and ``/reject <id>`` (R9) record
    Thomas's decision on a pending Approval Request. Passing the identity gate above IS the
    verification the approval record requires, so no separate proof is needed — and nothing
    that fails the gate can ever reach the decision path. An approved Approval authorizes no
    execution: consumption is gate-pinned unimplemented.

    ``/feedback <good|bad|note>`` (E1) records Thomas's verdict on the last delivered run
    to the feedback ledger — handled like the console/approval commands (any runtime mode,
    behind the same identity gate), and like every ``/`` command it never reaches the
    pipeline.

    ``/memory`` lists the live promotable working-memory candidates (read-only, any mode)
    and ``/promote <candidate_id> <reason>`` promotes one to VALIDATED memory — the
    convenience door onto ``scripts/promote_memory_candidate.py`` over the already-verified
    channel. Promotion is EXECUTE_AND_REPORT and kill-switch bound (refused unless ACTIVE);
    it reuses ``working_memory`` (the store) and ``store`` (the ledger) already threaded here.

    ``registry`` (opt-in, F1) is the task-coordination store. When wired, every request this
    channel runs is recorded as one entry (RUNNING on the way in, a terminal on the way out)
    and ``/tasks`` ``/history`` ``/result`` ``/cancel`` answer from it. The recording is
    best-effort — a bookkeeping failure never costs Thomas the analysis — while the console
    verbs are not: asked what is running, an unreadable registry says so rather than showing
    an empty list.
    """
    try:
        verify_control_channel(message, registration)
    except OperatorBlocked as exc:
        # Generic refusal — do not echo which check failed to an unverified sender.
        return OperatorReply(
            text="This control channel only accepts requests from the registered operator.",
            accepted=False, status="REFUSED", reason_code=exc.reason_code,
        )

    text = message.text.strip() if isinstance(message.text, str) else ""
    if not text:
        return OperatorReply(text="Empty request.", accepted=False, status="REFUSED", reason_code="EMPTY_REQUEST")

    # A state-changing verb must carry its slash on a CHAT channel. Every parser below accepts
    # the bare verb — deliberately, because on the local console you typed it into a terminal on
    # purpose (`control.command_verb`). Telegram is prose: "kill it" halted the runtime, and
    # "cancel 그거" cancelled a queued task, with no way for the operator to have meant otherwise.
    # Read-only verbs stay slash-optional (answering /status to "status?" costs nothing), so this
    # narrows only the verbs that change state. Refusing with the exact form to type is better
    # than silently treating it as conversation: if he meant the command, he still gets it in one
    # more keystroke; if he did not, nothing happened. The parsers are untouched, so the local
    # console keeps its convenience — this is a property of the channel, not of the grammar.
    if not text.startswith("/"):
        first = control.command_verb(text.partition(" ")[0], slash_seen=False)
        if first in _MUTATING_VERBS:
            return OperatorReply(
                text=(f"상태를 바꾸는 명령은 슬래시를 붙여주세요: `/{first}`. "
                      "(대화로 하신 말이라면 그냥 다시 말씀하시면 됩니다.)"),
                accepted=False, status="REFUSED", reason_code="SLASH_REQUIRED",
            )

    if control_store is not None:
        # Emergency-console commands are handled before (and regardless of) the run state — a
        # KILLED runtime must still answer /status and accept /resume from the verified operator.
        command = control.parse_command(text)
        if command is not None:
            verb, arg = command
            try:
                outcome = control.apply_command(
                    control_store, verb, actor=registration.operator_id, now=now, arg=arg, ledger=store,
                )
            except ControlBlocked as exc:
                return OperatorReply(text=exc.reason, accepted=False, status="REFUSED", reason_code=exc.reason_code)
            except PersistenceError as exc:
                # A ledger write failure must not escape: `run_operator_once` and the loop
                # catch only OperatorBlocked, so an uncaught PersistenceError killed the whole
                # control channel with a traceback — and it does so AFTER apply_command has
                # already saved the new mode, meaning a /resume could take effect with no
                # reply, no ledger event, and no operator channel left to ask. The state
                # change is already durable, so this reports the gap rather than pretending
                # the command failed (the approval-audit-gap precedent below).
                return OperatorReply(
                    text=(f"명령은 적용되었지만 원장 기록에 실패했습니다 ({exc.reason_code}). "
                          "`/audit`와 `/recovery`로 상태를 확인하세요."),
                    accepted=True, status="CONTROL", reason_code=exc.reason_code,
                )
            return OperatorReply(text=outcome["reply"], accepted=True, status="CONTROL", reason_code=outcome["action"])

    # R9: /approve <id> and /reject <id>. Handled after the identity gate and, like the
    # console commands, regardless of run state: answering a pending ask is not starting
    # work, and a paused runtime must still let Thomas close out what it already asked.
    # The gate above is precisely the verification the approval record demands — registered
    # user, registered private chat, not forwarded — so reaching here IS the proof.
    if approval_store is not None:
        approval_command = approval.parse_approval_command(text)
        if approval_command is not None:
            verb, approval_id, reason = approval_command
            try:
                outcome = approval.apply_command(
                    approval_store, verb, approval_id, now=now or timeutil.utc_now_iso(),
                    repo_root=repo_root,
                    reason=reason,
                    verification=approval.Verification(
                        approved_by=registration.approver,
                        method=approval.TELEGRAM_VERIFICATION_METHOD,
                        verification_ref=f"telegram:private_chat:{message.chat_id}:{approval_id}",
                    ),
                )
            except ApprovalBlocked as exc:
                return OperatorReply(text=exc.reason, accepted=False, status="REFUSED", reason_code=exc.reason_code)
            except PersistenceError as exc:
                # A corrupt/unwritable approval store must be a typed refusal, not a dead
                # channel: ApprovalStore.get / get_permission_decision / append all raise
                # PersistenceError, and nothing upstream catches it. Refusing is the safe
                # direction here — unlike the control branch, no state was changed yet.
                return OperatorReply(text=exc.reason, accepted=False, status="REFUSED", reason_code=exc.reason_code)
            if store is not None:
                try:
                    store.append_audit_events(build_approval_decision_audit(
                        outcome["approval"], now=now or timeutil.utc_now_iso(),
                        actor_id=registration.operator_id,
                        genesis_previous_hash=store.last_audit_hash(), repo_root=repo_root,
                    ))
                except (PersistenceError, AuditError) as exc:
                    # The decision is already durable in the approval store; losing Thomas's
                    # answer to protect a log would be the wrong trade. But the gap must not
                    # live only in a chat suffix — record it durably (a different ledger
                    # file, so a broken audit ledger does not take it too) so `recovery` can
                    # answer "the trail has a known hole here" later.
                    try:
                        store.append_block(build_audit_gap_record(
                            "approval_decision", reason_code=exc.reason_code,
                            subject_ref=approval_id or "unknown",
                            now=now or timeutil.utc_now_iso(), detail=exc.reason,
                        ))
                    except PersistenceError:
                        pass          # already failing to write; the reply still says so
                    return OperatorReply(
                        text=outcome["reply"] + f"\n(WARNING: decision audit failed: {exc.reason_code})",
                        accepted=True, status="APPROVAL", reason_code=outcome["action"],
                    )
            return OperatorReply(text=outcome["reply"], accepted=True, status="APPROVAL", reason_code=outcome["action"])

    # E1: /feedback records Thomas's verdict on the last delivered run. Like /approve,
    # it is handled in any runtime mode — judging already-delivered work is not new
    # execution, and a PAUSED runtime must still let Thomas say what he thinks of what
    # it already sent. The identity gate above is what makes the verdict *his*.
    feedback_payload = operator_feedback.parse_feedback_command(text)
    if feedback_payload is not None:
        try:
            outcome = operator_feedback.apply_feedback(
                feedback_payload, operator_id=registration.operator_id,
                store=store, working_memory=working_memory,
                # The verdict is answered in any mode; the M5a memory candidate is a mutation
                # and is kill-bound inside apply_feedback (memory_console's rule).
                control_store=control_store, now=now, repo_root=repo_root,
            )
        except (OperatorBlocked, PersistenceError) as exc:
            return OperatorReply(text=exc.reason, accepted=False, status="REFUSED", reason_code=exc.reason_code)
        return OperatorReply(text=outcome["reply"], accepted=True, status="FEEDBACK", reason_code="FEEDBACK_RECORDED")

    # Memory console: /memory (list live promotable candidates, read-only) and
    # /promote <id> <reason> (EXECUTE_AND_REPORT promotion to VALIDATED memory). The
    # convenience door onto scripts/promote_memory_candidate.py — same guards, same audit —
    # over the already-verified channel. Promotion is kill-switch bound inside
    # apply_memory_command; listing is read-only and answered in any mode.
    memory_command = memory_console.parse_memory_command(text)
    if memory_command is not None:
        try:
            outcome = memory_console.apply_memory_command(
                memory_command, operator_id=registration.operator_id,
                working_memory=working_memory, ledger=store, control_store=control_store,
                now=now, repo_root=repo_root,
            )
        except (OperatorBlocked, PersistenceError) as exc:
            return OperatorReply(text=exc.reason, accepted=False, status="REFUSED", reason_code=exc.reason_code)
        return OperatorReply(text=outcome["reply"], accepted=True, status="MEMORY", reason_code=outcome["action"])

    # F1 task console: /tasks /history /result (read-only, any runtime mode — an operator
    # facing a PAUSED runtime most needs to see what it was doing) and /cancel (mutates
    # coordination state, so kill-switch bound inside apply_registry_command).
    registry_command = registry_console.parse_registry_command(text)
    if registry_command is not None:
        try:
            outcome = registry_console.apply_registry_command(
                registry_command, operator_id=registration.operator_id,
                registry=registry, ledger=store, control_store=control_store,
                now=now, repo_root=repo_root,
            )
        except (OperatorBlocked, PersistenceError) as exc:
            return OperatorReply(text=exc.reason, accepted=False, status="REFUSED", reason_code=exc.reason_code)
        return OperatorReply(text=outcome["reply"], accepted=True, status="REGISTRY", reason_code=outcome["action"])

    # Domain console: /crypto and /pred. Read-only in every mode (kill_allows covers
    # read-only status), no ledger event, and no store to wire — each subcommand reads the
    # same local state the host CLI reads and renders it with that module's own renderer.
    domain_command = domain_console.parse_domain_command(text)
    if domain_command is not None:
        try:
            outcome = domain_console.apply_domain_command(
                domain_command, operator_id=registration.operator_id,
                now=now, repo_root=repo_root,
            )
        except (OperatorBlocked, PersistenceError) as exc:
            return OperatorReply(text=exc.reason, accepted=False, status="REFUSED", reason_code=exc.reason_code)
        return OperatorReply(text=outcome["reply"], accepted=True, status="DOMAIN", reason_code=outcome["action"])

    if text.startswith("/"):
        # A leading-slash message that matched no console/approval verb is refused, never
        # run as a task: a typo'd ``/killl`` (or an emergency verb reaching a deployment
        # without its store wired) silently becoming a full pipeline run — model call
        # included — is the fail-open direction.
        return OperatorReply(
            text=("Unknown command. Available: /status /pause /kill /resume /stop <task_id> "
                  "/audit /recovery /approve <id> [reason] /reject <id> [reason] "
                  "/feedback <good|bad|한줄평> /memory /promote <id> <사유> "
                  "/tasks /history [n] /result <id> /cancel <id> "
                  f"{domain_console.usage('crypto')} {domain_console.usage('pred')}"),
            accepted=False, status="REFUSED", reason_code="UNKNOWN_COMMAND",
        )

    if control_store is not None:
        # A task request is refused while the runtime is not ACTIVE (kill blocks new execution).
        #
        # `control_store is None` deliberately does NOT refuse here, unlike `memory_console` /
        # `registry_console`. Those are reached only when a console IS wired, so a missing kill
        # switch there is an inconsistent wiring and refusing is right. This function also serves
        # as the library entry point with no console at all (the pipeline-only calling mode much
        # of the suite uses), where "no control store" means "no console", not "the kill switch
        # went missing". The real protection is that the deployment always wires it — asserted by
        # `test_the_operator_loop_always_wires_the_kill_switch` rather than by breaking the
        # library mode.
        state = control_store.load()
        if not state.execution_allowed:
            reason_code = state.refusal_reason_code()
            return OperatorReply(
                text=f"Runtime is {state.mode}; new requests are blocked. Send /resume to continue (or /status).",
                accepted=False, status="REFUSED", reason_code=reason_code,
            )

    # R7.1: a leading importance marker raises the task's priority, which (under the
    # "auto" validation policy) adds the independent reviewer to exactly this request.
    # 8.5: a leading kind marker routes it to the Role that kind's capabilities need.
    #
    # ONE loop for both, and they may appear in either order ("!중요 !번역 ..." and
    # "!번역 !중요 ..." mean the same thing) — two parsers would inevitably guard one marker
    # and not the other. Each marker must be its own leading token: "!중요한 아이디어..." is
    # prose, not a flag. At most one of each; a repeat stops the scan and stays in the text,
    # where the request itself will show it rather than a marker silently winning.
    priority = "NORMAL"
    request_kind: str | None = None
    while True:
        head, _, rest = text.partition(" ")
        lowered = head.lower()
        if lowered in IMPORTANT_MARKERS and priority == "NORMAL":
            priority = "HIGH"
        elif lowered in KIND_MARKERS and request_kind is None:
            request_kind = KIND_MARKERS[lowered]
        else:
            break
        text = rest.strip()
        if not text:
            return OperatorReply(
                text=f"'{head}' 뒤에 요청 내용을 함께 보내주세요 (예: {head} 이 사업 아이디어를 분석해줘: ...).",
                accepted=False, status="REFUSED", reason_code="EMPTY_REQUEST",
            )
        # The marker strip happens AFTER the unknown-command guard above, so a slash command
        # hidden behind it used to skip that guard entirely: `!중요 /killl` became a pipeline
        # task and spent a model call on a typo'd emergency verb — the exact fail-open the guard
        # exists to prevent. Re-check the post-marker text. (A marked *real* command is still
        # refused rather than executed: the marker means "this request is important" or "route
        # it this way", and a command is not a request — mixing the two is a mistake worth
        # naming.) Inside the loop, so the guard covers every marker, not just the first.
        if text.startswith("/"):
            return OperatorReply(
                text=("명령에는 표시를 붙이지 마세요 — 표시는 요청에만 씁니다. "
                      f"명령만 따로 보내주세요 (예: {text.partition(' ')[0]})."),
                accepted=False, status="REFUSED", reason_code="MARKED_COMMAND",
            )

    # Every refusal path is behind us. What happens next is one of three things, in order: a
    # front-desk conversation turn, a queued task, or (with no registry) an inline run.
    stamp = now or timeutil.utc_now_iso()

    # F2: with a front-desk provider wired, an UNMARKED plain-text message is a
    # conversation turn, not automatically a task — the front desk decides (submit /
    # query / clarify / chat) through its closed turn contract. Placement is deliberate:
    # AFTER the kill-switch refusal (a PAUSED runtime stops the conversation LLM — this
    # is where 'frontdesk model calls are kill-bound' is enforced, once) and AFTER the
    # marker parse (a `!중요`-marked request is the operator being explicit; deterministic
    # intent never waits on a model). None back means degraded — fall through to the F1
    # queue path, so conversation dying never loses a message.
    if (frontdesk_provider is not None and registry is not None
            and priority == "NORMAL" and request_kind is None):
        try:
            turn_outcome = frontdesk.run_turn(
                text, provider=frontdesk_provider, registry=registry,
                working_memory=working_memory, ledger=store, control_store=control_store,
                operator_id=registration.operator_id, now=stamp, repo_root=repo_root,
            )
        except MvpRuntimeError:
            # `run_turn` handles only ProviderError/TimeoutError itself, so a corrupt working
            # memory (PersistenceError from the session read), a revoked provider grant
            # (SafetyGateBlocked), or any other typed failure escaped and killed the channel —
            # the loop catches only OperatorBlocked. The module's promise is "conversation dying
            # never loses a message", so every typed failure degrades to the F1 queue path
            # exactly like a provider error does, instead of taking the operator channel down.
            turn_outcome = None
        if turn_outcome is not None:
            return OperatorReply(
                text=turn_outcome["reply"], accepted=True, status="FRONTDESK",
                reason_code=turn_outcome["action"],
                registry_entry_id=turn_outcome.get("registry_entry_id"),
            )
        # Degraded: the raw message continues down the F1 path unchanged — same
        # channel behavior as frontdesk-off, so no message is ever lost to a model.

    # F1 increment 2: with a registry wired, the request is QUEUED and the loop's drain
    # runs it between polls. The registry IS the queue, so "no registry" means there is
    # nothing to queue into and the request runs inline below — that is one rule, not a
    # mode switch. Queueing is what keeps the channel answering: an inline run holds the
    # loop for the length of a model call, during which /tasks, /cancel and /kill could
    # not land.
    if registry is not None:
        try:
            entry, position = task_registry.enqueue(
                registry, request_text=text, origin="TELEGRAM",
                requester_id=registration.operator_id, now=stamp,
                flags={"important": priority == "HIGH",
                       "independent_validation": bool(independent_validation)},
                request_kind=request_kind,
            )
        except MvpRuntimeError as exc:
            # NOT swallowed, unlike the bookkeeping seam: a request that failed to queue
            # was never accepted, and silently dropping one Thomas believes is running is
            # the one outcome worse than refusing it.
            return OperatorReply(text=exc.reason, accepted=False, status="REFUSED",
                                 reason_code=exc.reason_code)
        marker_note = ""
        if priority == "HIGH":
            # Truthful note: the marker adds the reviewer only when a validation policy is
            # active ("auto" or always-on); otherwise it is recorded priority only.
            marker_note = " · 중요 표시: 독립 검증 포함" if independent_validation else " · 중요 표시 적용"
        queue_note = "바로 시작합니다" if position == 1 else f"대기 {position}번째"
        return OperatorReply(
            text=(f"접수했습니다 ({queue_note}){marker_note}\n"
                  f"id: {entry.registry_entry_id[:12]}\n"
                  "완료되면 결과를 보내드립니다 — /tasks 로 진행 상황을 볼 수 있습니다."),
            accepted=True, status="QUEUED", reason_code="TASK_QUEUED",
            registry_entry_id=entry.registry_entry_id,
        )

    # Inline path (no registry): every refusal is behind us, so this message WILL run the
    # pipeline now. Say so — the model call takes tens of seconds and the operator
    # otherwise stares at silence.
    if ack is not None:
        marker_note = ""
        if priority == "HIGH":
            marker_note = " (중요 표시: 독립 검증 포함)" if independent_validation else " (중요 표시 적용)"
        try:
            ack("접수했습니다 — 분석 중입니다. 모델 호출에 수십 초 걸릴 수 있습니다." + marker_note)
        except OperatorBlocked:
            pass    # best-effort: the notice is a courtesy, the run is the job

    result = run_task(
        text,
        provider=provider,
        search_tool=search_tool,
        working_memory=working_memory,
        programization=programization,
        now=now,
        store=store,
        repo_root=repo_root,
        independent_validation=independent_validation,
        validator_provider=validator_provider,
        priority=priority,
        request_kind=request_kind,
        channel="telegram",
        requester_type="real_thomas",
        requester_id=registration.operator_id,
        authenticated=True,
        source_ref=f"telegram:private_chat:{message.chat_id}",
    )
    return render_result_reply(result)


def render_result_reply(result: dict[str, Any]) -> OperatorReply:
    """Render a finished pipeline result as the reply Thomas receives.

    Shared by the inline path and the queue drain so the two can never drift on what a
    blocked run tells him — the drain reaches Thomas through a different door, not with a
    different voice.
    """
    identity = result.get("records", {}).get("received_task", {}).get("identity", {})
    trace_id = identity.get("trace_id")
    if result["status"] == "COMPLETED":
        return OperatorReply(text=result["final_response"], accepted=True,
                             status="COMPLETED", trace_id=trace_id)

    block = result.get("block") or {"reason_code": "BLOCKED"}
    reason_code = block.get("reason_code", "BLOCKED")
    reply_text = f"Your request was not completed ({reason_code})."
    detail = str(block.get("message") or "").strip()
    if detail:
        # The block's reasons ARE the deliverable of a withheld run: for a
        # VALIDATION_REVISE/BLOCK they carry the reviewer's actionable revision requests,
        # which this reply used to drop, leaving Thomas a bare code and nothing to act
        # on. The recipient is the verified operator, so there is nothing to redact.
        if reason_code.startswith("VALIDATION_"):
            # The validation message is "; "-joined reasons — render them as a list.
            detail = "\n".join(f"- {p.strip()}" for p in detail.split(";") if p.strip())
        reply_text += "\n" + detail
    if reason_code == "PROVIDER_ERROR":
        # The one BLOCK an operator can fix by doing nothing: free-tier providers throttle
        # and time out transiently, so say the actionable thing instead of only the code.
        reply_text += "\n일시적인 모델 제공자 오류일 수 있습니다 — 잠시 후 같은 요청을 다시 보내보세요."
    elif reason_code in ("VALIDATION_REVISE", "VALIDATION_BLOCK"):
        reply_text += "\n위 지적을 반영해 요청을 보완해서 다시 보내주시면 새로 분석합니다."
    return OperatorReply(
        text=reply_text,
        accepted=True, status="BLOCKED", reason_code=reason_code, trace_id=trace_id,
    )


# --- R4.2: the channel transport (mock default; real Telegram behind the gate) ----------


class OperatorChannel(Protocol):
    def poll(self, *, long_poll_seconds: int = 0) -> list[InboundMessage]: ...
    # Read what is waiting WITHOUT claiming it: the cursor does not move, so the next `poll`
    # delivers these same messages and handles them normally. That is the whole design of the
    # halt peek (see `peek_for_halt`) — an unclaimed read cannot lose a message, and the price
    # is that whatever the peek acts on must be safe to see twice.
    def peek(self) -> list[InboundMessage]: ...
    # Returns the sent message's id when the transport has one, else None. Callers that only
    # deliver ignore it; the progress notice needs it, because editing one message in place is
    # the difference between a live status line and six notifications for one task.
    def send(self, chat_id: str, text: str) -> str | None: ...


@dataclass
class MockOperatorChannel:
    """Deterministic, network-free channel for tests and local runs. ``inbound`` is drained
    on each ``poll``; ``sent`` captures ``(chat_id, text)`` for assertions. ``long_poll_seconds``
    is accepted for protocol parity but ignored — the in-memory queue returns immediately.

    ``edited`` captures ``(chat_id, message_id, text)``. The mock supports editing so the
    progress path is exercised by the default test channel rather than only by the networked
    one — a feature only the real transport can run is a feature only production tests."""

    inbound: list[InboundMessage] = field(default_factory=list)
    sent: list[tuple[str, str]] = field(default_factory=list)
    edited: list[tuple[str, str, str]] = field(default_factory=list)
    network_egress: bool = False
    last_long_poll_seconds: int | None = None
    peeks: int = 0

    def poll(self, *, long_poll_seconds: int = 0) -> list[InboundMessage]:
        self.last_long_poll_seconds = long_poll_seconds
        batch, self.inbound = list(self.inbound), []
        return batch

    def peek(self) -> list[InboundMessage]:
        """What `poll` would return, WITHOUT draining — the unclaimed read the halt peek uses.
        The same messages stay queued for the next `poll`, exactly as the real cursor behaves."""
        self.peeks += 1
        return list(self.inbound)

    def send(self, chat_id: str, text: str) -> str | None:
        self.sent.append((chat_id, text))
        return f"mock-msg-{len(self.sent)}"

    def edit(self, chat_id: str, message_id: str, text: str) -> None:
        self.edited.append((chat_id, message_id, text))


def select_operator_channel(*, now: str | None = None, root: Path | None = None) -> OperatorChannel:
    """Choose the operator channel — the enforced Safety-Flag Gate chokepoint.

    Defaults to the network-free ``MockOperatorChannel`` (no gate needed). A real
    ``TelegramChannel`` is returned ONLY when both ``MVP_OPERATOR_CHANNEL=telegram`` AND the
    Safety-Flag Gate authorizes ``network_access`` against a local activation record. The env
    var alone fails closed (``SafetyGateBlocked``), never silently opening a network path.

    Shares ``safety_gate.select_gated`` with the provider, search tool, and writer — one
    place decides that the capable implementation is never built before the gate opens."""
    state_path = (root if root is not None else _repo_root()) / OFFSET_STATE_REL
    return safety_gate.select_gated(
        env_var=OPERATOR_CHANNEL_ENV,
        opt_in_value=TELEGRAM,
        flags=_NETWORK_FLAGS,
        provider_id=TELEGRAM,
        default_factory=MockOperatorChannel,
        gated_factory=lambda authorization: TelegramChannel(
            authorization=authorization, state_path=state_path,
        ),
        now=now,
        root=root,
    )


def notify_operator(channel: OperatorChannel, text: str, *, repo_root: Path | None = None) -> None:
    """Send an UNSOLICITED notification to the registered operator.

    The outbound half of R4's identity gate. The destination is never caller-supplied: it
    is always the ONE registered private chat (``load_operator_registration``), so a
    notification can only ever reach Thomas — no caller can address anyone else, and the
    same registration that decides whose messages are obeyed decides who gets told.
    Fails closed (``OperatorBlocked``) with no registration: with nobody registered there
    is nobody to notify. The transport is whatever the Safety-Flag Gate handed the caller,
    so on the default mock channel this notifies nobody and opens no socket.
    """
    registration = load_operator_registration(repo_root)
    channel.send(registration.chat_id, text)


class TelegramChannel:
    """Real Telegram Bot API control channel (long-poll ``getUpdates`` + ``sendMessage``).

    Behind the Safety-Flag Gate: makes outbound HTTPS calls and re-verifies the egress
    authorization at socket-open time (defense in depth). The bot token is read from an env
    var **by name** at call time; per the Telegram API it sits in the URL path over HTTPS and
    is **never** logged, echoed, or included in an error. Inert until selected and tokened.
    """

    provider_id = TELEGRAM
    network_egress = True
    _API = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, *, token_env: str = "TELEGRAM_BOT_TOKEN", authorization: Authorization | None = None,
                 state_path: Path | None = None):
        self._token_env = token_env  # the NAME of the env var, never the value
        self._authorization = authorization
        # getUpdates cursor; advances past fetched updates. With ``state_path`` (the
        # production path via select_operator_channel) it is durable: without persistence,
        # every restart resets to 0 and Telegram re-delivers every unconfirmed update —
        # duplicate model calls, duplicate ledger records, duplicate replies. ``None``
        # keeps the cursor in-memory (tests; no machine-local state is touched).
        self._state_path = state_path
        self._offset = 0
        self._offset_loaded = state_path is None

    def _assert(self) -> str:
        safety_gate.assert_authorization(
            self._authorization, required_flags=_NETWORK_FLAGS, provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )
        token = os.environ.get(self._token_env)
        if not token:
            raise OperatorBlocked("NO_BOT_TOKEN", f"environment variable {self._token_env} is not set")
        return token

    # Extra HTTP timeout beyond the server-side long-poll hold, so the client waits out the
    # full long-poll plus network latency instead of aborting it early.
    _HTTP_TIMEOUT_BUFFER = 10
    _DEFAULT_HTTP_TIMEOUT = 30

    def _call(self, token: str, method: str, params: dict[str, Any], *, timeout: int) -> dict[str, Any]:
        data = urllib.parse.urlencode(params).encode("utf-8")
        request = urllib.request.Request(
            self._API.format(token=token, method=method), data=data, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (TimeoutError, urllib.error.URLError, ValueError):
            # Deliberately generic — never echo the URL (it carries the token) or the token.
            raise OperatorBlocked("CHANNEL_TRANSPORT", f"telegram {method} failed or timed out") from None
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise OperatorBlocked("CHANNEL_TRANSPORT", f"telegram {method} returned an error response")
        return payload

    def _load_offset(self) -> int:
        """The persisted cursor, 0 when none exists yet. A malformed state file fails closed
        (the operator deletes/fixes it) rather than silently restarting at 0 and replaying."""
        if self._state_path is None or not self._state_path.is_file():
            return 0
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return int(data["offset"])
        except (OSError, ValueError, KeyError, TypeError):
            raise OperatorBlocked(
                "OFFSET_STATE_MALFORMED",
                f"telegram offset state at {self._state_path} is unreadable; fix or delete it",
            ) from None

    def _save_offset(self) -> None:
        """Persist the advanced cursor atomically, BEFORE the batch is handed to the caller:
        a fetched batch is claimed once. If persisting fails, fail closed — processing a
        batch whose claim is not durable would re-execute it after the next restart."""
        if self._state_path is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"offset": self._offset}), encoding="utf-8")
            os.replace(tmp, self._state_path)
        except OSError as exc:
            raise OperatorBlocked(
                "OFFSET_PERSIST_FAILED", f"could not persist the telegram offset: {exc}"
            ) from None

    def poll(self, *, long_poll_seconds: int = 0) -> list[InboundMessage]:
        # Real long-poll: getUpdates holds the connection up to ``long_poll_seconds`` server
        # side; the HTTP timeout must outlast that hold (+buffer) or it would abort early.
        token = self._assert()
        if not self._offset_loaded:
            self._offset = self._load_offset()
            self._offset_loaded = True
        http_timeout = (long_poll_seconds + self._HTTP_TIMEOUT_BUFFER) if long_poll_seconds > 0 else self._DEFAULT_HTTP_TIMEOUT
        payload = self._call(
            token, "getUpdates",
            {"offset": self._offset, "timeout": long_poll_seconds, "allowed_updates": json.dumps(["message"])},
            timeout=http_timeout,
        )
        before = self._offset
        messages: list[InboundMessage] = []
        for update in payload.get("result", []):
            if not isinstance(update, dict):
                continue
            # A non-int update_id (null, a string, an object) would make int() raise
            # TypeError/ValueError — not an OperatorBlocked, so the loop's handler misses
            # it and the whole service dies with a traceback. Skip the malformed update
            # instead; the cursor does not advance past it, so nothing is silently claimed.
            try:
                update_id = int(update.get("update_id"))
            except (TypeError, ValueError):
                continue
            self._offset = max(self._offset, update_id + 1)
            messages.append(_message_from_update(update))
        if self._offset != before:
            self._save_offset()
        return [m for m in messages if m is not None]

    def peek(self) -> list[InboundMessage]:
        """Read what is waiting **without claiming it**: same offset, `timeout=0`, and the
        cursor is neither advanced in memory nor persisted.

        This is the load-bearing property of the halt peek, and it is a deliberate asymmetry
        with `poll`. Advancing here would claim every update in the batch while handling only
        the halt verbs in it, so an ordinary request sent in the same second as a `/kill` would
        be **silently destroyed** — the one outcome the F1 enqueue rule exists to prevent. Not
        advancing means the next `poll` re-delivers these same messages and handles them the
        normal way: one ledger event, one reply, from one place.

        The price is that the peek's caller sees a message it will see again, so it may only
        act on what is safe to act on twice. That is why `peek_for_halt` acts on `/kill` and
        `/pause` and nothing else, and why it writes state but never a ledger event or a reply.
        """
        token = self._assert()
        if not self._offset_loaded:
            self._offset = self._load_offset()
            self._offset_loaded = True
        payload = self._call(
            token, "getUpdates",
            {"offset": self._offset, "timeout": 0, "allowed_updates": json.dumps(["message"])},
            timeout=self._DEFAULT_HTTP_TIMEOUT,
        )
        messages = [_message_from_update(u) for u in payload.get("result", []) if isinstance(u, dict)]
        return [m for m in messages if m is not None]

    # Telegram rejects a sendMessage over 4096 UTF-16 code units. Split just under that so a
    # substantive analysis is delivered as several messages instead of failing outright — an
    # undeliverable reply after a completed run burns the model call and loses the answer.
    # The 96 units of headroom below the real cap hold the `(i/n)` counter added per chunk.
    _MAX_SEND_UNITS = 4000

    def send(self, chat_id: str, text: str) -> str | None:
        token = self._assert()
        chunks = _split_for_send(text, self._MAX_SEND_UNITS)
        total = len(chunks)
        first_message_id: str | None = None
        for index, chunk in enumerate(chunks, start=1):
            # A multi-part answer is NUMBERED. Without this, losing part 3 of 5 to a transport
            # error left Thomas reading an analysis that simply stopped mid-sentence with
            # nothing to say a piece was missing — the reply looked complete and was not.
            body = f"({index}/{total})\n{chunk}" if total > 1 else chunk
            try:
                payload = self._call(token, "sendMessage", {"chat_id": chat_id, "text": body}, timeout=30)
                if index == 1:
                    # Only the FIRST part's id is returned, and only an editable single-part
                    # message is ever edited by the caller: editing part 1 of a five-part
                    # answer would rewrite a fragment and leave the other four contradicting
                    # it. The progress notice is one short line, so it is always single-part.
                    result = payload.get("result")
                    if isinstance(result, dict) and result.get("message_id") is not None:
                        first_message_id = str(result["message_id"])
            except OperatorBlocked as exc:
                if index == 1:
                    raise       # nothing arrived: an ordinary delivery failure
                # Parts already landed, so "the reply was not delivered" is a false report.
                # A distinct code lets the caller say what actually happened; the counter
                # above is what tells Thomas the same thing on the channel itself.
                raise OperatorBlocked(
                    PARTIAL_DELIVERY_REASON,
                    f"delivered {index - 1} of {total} message parts before failing "
                    f"({exc.reason_code}); the answer reached Thomas incomplete",
                ) from exc
        return first_message_id

    def edit(self, chat_id: str, message_id: str, text: str) -> None:
        """Rewrite one already-sent message in place (``editMessageText``).

        Used only for the progress notice, and deliberately NOT wired into any delivery path:
        an analysis Thomas has already read must not change under him, and the ledger is
        append-only for the same reason. Editing is how a status line stays one message
        instead of becoming six notifications for one task."""
        token = self._assert()
        self._call(
            token, "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": text},
            timeout=30,
        )


def _split_for_send(text: str, limit: int) -> list[str]:
    """Split ``text`` into chunks of at most ``limit`` UTF-16 code units (Telegram's unit of
    account — astral-plane characters count double), cutting after the last newline inside
    the window when there is one so chunks break between lines, not mid-sentence."""
    chunks: list[str] = []
    start = 0
    units = 0
    cut_candidate = -1  # index just past the most recent newline inside the current window
    i = start
    while i < len(text):
        units += 2 if ord(text[i]) > 0xFFFF else 1
        if units > limit:
            cut = cut_candidate if cut_candidate > start else i
            chunks.append(text[start:cut])
            start, units, cut_candidate = cut, 0, -1
            i = cut
            continue
        if text[i] == "\n":
            cut_candidate = i + 1
        i += 1
    if start < len(text) or not chunks:
        chunks.append(text[start:])
    return chunks


def _message_from_update(update: dict[str, Any]) -> InboundMessage | None:
    """Map a Telegram update to an InboundMessage. Only plain ``message`` updates with text
    are handled; a group/channel type or a forwarded flag is preserved so the identity gate
    (not this mapper) rejects it."""
    msg = update.get("message")
    if not isinstance(msg, dict) or not isinstance(msg.get("text"), str):
        return None
    chat = msg.get("chat", {}) if isinstance(msg.get("chat"), dict) else {}
    sender = msg.get("from", {}) if isinstance(msg.get("from"), dict) else {}
    chat_type = chat.get("type")
    is_forwarded = ("forward_origin" in msg) or ("forward_from" in msg) or ("forward_date" in msg)
    return InboundMessage(
        text=msg["text"],
        sender_id=str(sender.get("id", "")),
        chat_id=str(chat.get("id", "")),
        chat_type="private" if chat_type == "private" else str(chat_type or "unknown"),
        is_forwarded=bool(is_forwarded),
        channel=PRIMARY_CHANNEL,
    )


# What each reported pipeline stage is called on the channel. The KEYS must be exactly
# `pipeline.PROGRESS_STAGES` (pinned by a test): a stage the pipeline reports and this map does
# not know would print a bare identifier at Thomas, and a label for a stage that no longer
# exists is a line that can never appear.
#
# Stage NAMES, never a percentage. A percentage would have to be invented — the run has no
# measurable denominator (a model call takes as long as it takes), and a progress bar that
# reaches 80% and sits there is a lie told slowly.
PROGRESS_LABELS: dict[str, str] = {
    "readonly_search": "자료 검색 중",
    "analysis_worker": "분석 중",
    "automatic_validation": "결과 검증 중",
    "independent_validation": "독립 검증 중",
    "revision": "수정본 생성 중",
    "controlled_write": "파일 작성 중",
}
PROGRESS_STARTED_LABEL = "시작했습니다"


class ProgressNotice:
    """One live status line for one queued run: sent once, then edited in place.

    Why a second message rather than editing the queue receipt: the receipt is sent from the
    message handler, possibly polls earlier, and its id is not on the durable entry — carrying
    it there would mean another `task_registry_entry` version for something that is pure UI and
    does not survive a restart anyway. So the drain sends its own line when the run actually
    STARTS, which is a different fact from "queued" and worth saying once on its own.

    Every failure direction is the same one: **the run wins.** A notice that cannot be sent
    means no progress display and a run that proceeds silently, exactly as before this existed;
    a notice that cannot be edited is switched off for the rest of the run rather than retried,
    because a channel that just failed an edit will most likely fail the next five too, and
    spending five more failing HTTP calls per run buys nothing — the delivery at the end is the
    message that matters. Nothing here is audited: these are `CONTROL_CHANNEL_RESPONSE` (ALLOW)
    notices on the already-verified channel, the ack precedent, and the durable record of what
    this run did is the registry entry plus its ledger trail.
    """

    def __init__(self, channel: OperatorChannel, chat_id: str, *, entry_id: str, request_text: str):
        self._channel = channel
        self._chat_id = chat_id
        self._entry_id = entry_id
        self._request = clip_for_prompt(request_text, 60)
        self._message_id: str | None = None
        self._last_text: str | None = None
        self._live = callable(getattr(channel, "edit", None))

    def _render(self, label: str) -> str:
        return f"[{self._entry_id[:12]}] {self._request}\n상태: {label}"

    def start(self) -> None:
        """Send the line. Best-effort: a channel that cannot send it, or one with no edit
        support at all, simply gets no progress display."""
        if not self._live:
            return
        text = self._render(PROGRESS_STARTED_LABEL)
        try:
            self._message_id = self._channel.send(self._chat_id, text)
        except Exception:      # noqa: BLE001 — a status line must never cost the run
            self._live = False
            return
        if self._message_id is None:
            # The transport sent it but cannot say which message it was, so there is nothing
            # to edit. One notice is better than six, so stop here rather than falling back
            # to a new message per stage.
            self._live = False
            return
        self._last_text = text

    def stage(self, stage: str) -> None:
        """Update the line for one pipeline stage. An unknown stage is dropped rather than
        printed raw — the label map is pinned to the pipeline's own list, so this can only
        fire if the two drift, and a bare `analysis_worker` on the channel helps nobody."""
        label = PROGRESS_LABELS.get(stage)
        if label is not None:
            self.finish(label)

    def finish(self, label: str) -> None:
        """Rewrite the line with ``label``. Also the terminal update, so a finished run does
        not sit forever claiming it is still analyzing — the deliverable is a separate
        message, and this one only ever holds the status."""
        if not self._live or self._message_id is None:
            return
        text = self._render(label)
        if text == self._last_text:
            # Telegram rejects an edit that changes nothing ("message is not modified"), which
            # would then switch the notice off for the rest of the run over a non-event.
            return
        try:
            self._channel.edit(self._chat_id, self._message_id, text)
        except Exception:      # noqa: BLE001 — see the class docstring
            self._live = False
            return
        self._last_text = text


# The ONLY verbs the mid-run peek may act on, and the list is short for one reason: the peek
# does not claim the messages it reads, so everything here is seen again by the next poll and
# must therefore be safe to apply twice.
#
# `kill` and `pause` qualify — applying KILLED to an already-KILLED runtime is the same runtime.
# `resume` is deliberately absent and its absence is the safety property: a peek that could
# resume would be a halt undone by a message the operator sent *before* the halt, re-read out of
# order. `approve` is absent because consuming an approval twice is exactly what
# `one_time_use_required` forbids. `status`/`audit` are absent because they reply, and a reply
# the next poll sends again is noise the peek has no reason to create.
PEEKABLE_HALT_VERBS = frozenset({control.CMD_KILL, control.CMD_PAUSE})


def peek_for_halt(
    channel: OperatorChannel,
    *,
    registration: OperatorIdentity,
    control_store: ControlStore | None,
    now: str | None = None,
) -> str | None:
    """Look for a halt command the loop has not reached yet, and make it real if there is one.

    Returns the verb applied, or None. **Never raises** — see the failure direction below.

    The gap this closes is not "a long analysis cannot be interrupted"; it is that while one
    runs, `/kill` exists **nowhere in the runtime**. The operator loop is the only process that
    receives Telegram, and it does not poll while it drains — so the control state file, which
    is what the crypto cycle's `live_route` re-reads immediately before a live entry, cannot
    change for the length of an analysis. That made this loop's responsiveness a dependency of
    the money path in another container.

    So this writes **state** and nothing else: no ledger event, no reply. Both belong to the
    normal handling that follows, because the peek does not claim what it reads (see
    `TelegramChannel.peek`) and the next poll will deliver the same message again. Applying the
    transition through the same `control.apply_command` with `ledger=None` keeps one owner for
    what a halt *means* while leaving the durable audit to happen exactly once.

    What this deliberately does NOT do: stop the analysis that is running. That needs an abort
    path and a registry terminal for an interrupted RUNNING entry — decision K4 in
    `docs/proposals/CONTROL_LANE_SEPARATION_V0.1.md`, not this. The drain already re-reads the
    control state before claiming the next task, so nothing further starts.

    Failure direction: every exception is swallowed. The peek reaches the network on a path
    whose job is somebody's analysis, and a safety net that can itself destroy the run is not
    one. A failed peek leaves exactly the behaviour that existed before it.
    """
    if control_store is None:
        return None
    reader = getattr(channel, "peek", None)
    if not callable(reader):
        return None       # a transport that cannot peek degrades to the pre-existing silence
    try:
        for message in reader():
            try:
                verify_control_channel(message, registration)
            except OperatorBlocked:
                continue          # not the registered operator; the normal path drops it too
            command = control.parse_command(message.text)
            if command is None or command[0] not in PEEKABLE_HALT_VERBS:
                continue
            verb, arg = command
            control.apply_command(
                control_store, verb, actor=registration.operator_id,
                now=now or timeutil.utc_now_iso(), arg=arg,
                ledger=None,      # the audit belongs to the normal handling, once
            )
            return verb
    except Exception:      # noqa: BLE001 — deliberate: see the docstring
        return None
    return None


def run_queued_task(
    entry: Any,
    *,
    channel: OperatorChannel,
    registration: OperatorIdentity,
    registry: Any,
    provider: Provider | None = None,
    search_tool: Any | None = None,
    working_memory: Any | None = None,
    programization: Any | None = None,
    now: str | None = None,
    store: LedgerStore | None = None,
    independent_validation: bool | str = False,
    validator_provider: Provider | None = None,
    control_store: ControlStore | None = None,
    repo_root: Path | None = None,
) -> tuple[OperatorReply, bool, bool]:
    """Run one already-claimed queued entry, deliver its result, and close the entry.

    Returns ``(reply, delivered, partial)``. ``delivered`` is reported separately rather than
    folded into ``reply.reason_code``, which for a blocked run already carries the block's own
    code — overloading it would have made a send failure invisible in the batch summary
    exactly when the run was withheld. ``partial`` is True when the send failed *after* some
    numbered message parts had already arrived: not delivered, but not lost either.

    The entry arrives RUNNING (``claim_next_queued`` claimed it under the store lock), so
    this owns exactly one thing: turn it into a terminal that matches what actually
    happened. ``DELIVERED`` only after the send succeeds — a completed analysis Thomas
    never received is ``FAILED``/``SEND_FAILED``, and ``/result`` exists so he can still
    fetch it. Reply text comes from the shared :func:`render_result_reply`, so a run that
    arrives through the queue speaks the same way as one that ran inline.
    """
    stamp = now or timeutil.utc_now_iso()
    # The silence this closes: the queue receipt was the last thing Thomas heard until the
    # finished analysis arrived, which for a validated run is two model calls later. "Queued"
    # and "still working" are different facts and the channel could only say the first.
    progress = ProgressNotice(
        channel, registration.chat_id,
        entry_id=entry.registry_entry_id, request_text=entry.request_text,
    )
    progress.start()

    def _at_stage(stage: str) -> None:
        """One callback, two jobs, in this order on purpose.

        The status line is what Thomas sees; the halt peek is what protects the money path in
        the other container. Both hang off the stage boundaries #294 installed, because a
        boundary is exactly where this loop is between two long-running things and can afford a
        round trip. Neither can raise: `ProgressNotice` swallows its own failures and
        `peek_for_halt` swallows everything.
        """
        progress.stage(stage)
        peek_for_halt(channel, registration=registration,
                      control_store=control_store, now=stamp)

    reply = render_result_reply(run_task(
        entry.request_text,
        on_progress=_at_stage,
        provider=provider,
        search_tool=search_tool,
        working_memory=working_memory,
        programization=programization,
        now=now,
        store=store,
        repo_root=repo_root,
        independent_validation=independent_validation,
        validator_provider=validator_provider,
        priority="HIGH" if entry.flags.get("important") else "NORMAL",
        # The kind has to come off the ENTRY, not off a message that is long gone: the drain
        # runs minutes after submission, so a kind that lived only in the operator's text
        # would silently become an analysis by the time it ran.
        request_kind=entry.request_kind,
        channel="telegram",
        requester_type="real_thomas",
        requester_id=registration.operator_id,
        authenticated=True,
        source_ref=f"telegram:private_chat:{registration.chat_id}",
    ))

    # Closed BEFORE the deliverable is sent: a long analysis is several messages, and a status
    # line still reading "분석 중" underneath a finished answer is the display contradicting the
    # channel. What it says is what happened to the RUN, not whether the send worked — that is
    # not known yet, and the send's own outcome speaks for itself.
    progress.finish("완료 — 결과 전송 중" if reply.status == "COMPLETED" else f"중단됨 ({reply.reason_code or 'BLOCKED'})")

    delivered = True
    partial = False
    try:
        channel.send(registration.chat_id, reply.text)
    except OperatorBlocked as exc:
        delivered = False
        partial = exc.reason_code == PARTIAL_DELIVERY_REASON

    if delivered and reply.status == "COMPLETED" and reply.trace_id:
        # E1: the pointer /feedback binds to. Recorded here rather than in the message
        # loop because in queue mode this is where a run actually completes — leaving it
        # behind would have silently degraded /feedback to "no delivered run" forever.
        # AFTER the send, so feedback can never target a run Thomas did not see.
        try:
            operator_feedback.record_delivery(reply.trace_id, now=stamp, repo_root=repo_root)
        except OperatorBlocked:
            pass        # best-effort: losing the pointer must not cost the run

    if reply.status == "COMPLETED":
        # Still not DELIVERED on a partial send — Thomas does not have the whole answer, and
        # `/result <id>` re-renders it from the ledger — but the reason says which of the two
        # happened instead of calling a half-delivered analysis a plain send failure.
        status = task_registry.DELIVERED if delivered else task_registry.FAILED
        reason_code = None if delivered else ("SEND_INCOMPLETE" if partial else "SEND_FAILED")
    else:
        # A withheld run has no deliverable to lose, so the send outcome does not change
        # what happened to it — only whether Thomas heard about it.
        status = task_registry.BLOCKED
        reason_code = reply.reason_code or "BLOCKED"
    task_registry.close_entry(
        registry, entry, status=status, now=stamp, trace_id=reply.trace_id,
        result_ref=f"ledger:{reply.trace_id}" if reply.trace_id else None,
        reason_code=reason_code,
    )
    return replace(reply, registry_entry_id=entry.registry_entry_id), delivered, partial


def drain_queue(
    *,
    channel: OperatorChannel,
    registration: OperatorIdentity,
    registry: Any | None,
    control_store: ControlStore | None,
    max_tasks: int,
    now: str | None = None,
    **run_kwargs: Any,
) -> tuple[list[OperatorReply], int, int]:
    """Execute up to ``max_tasks`` queued entries.

    Returns ``(replies, send_failures, partial_sends)``, where ``partial_sends`` counts the
    subset of the failures on which some message parts had already reached Thomas. They are
    counted, not folded into the failures, because "the reply was not delivered" is a false
    statement about a half-delivered analysis and the operator's log said exactly that.

    ``max_tasks`` defaults to 1 at the call site on purpose: returning to the poll between
    tasks is the entire point of the queue. A drain that emptied the whole backlog in one
    pass would hold the loop exactly as long as the inline execution it replaced, and
    ``/cancel`` or ``/kill`` issued mid-backlog would not land until it finished.

    Kill-switch is re-read **before every claim**, not once per batch (the scheduler's
    per-fire precedent): one task can hold the drain for minutes, and a kill issued during
    it must stop the tasks behind it, not just the next batch. Entries already queued stay
    queued — a kill drops nothing, it pauses the drain.
    """
    executed: list[OperatorReply] = []
    send_failures = 0
    partial_sends = 0
    if registry is None:
        return executed, send_failures, partial_sends
    while len(executed) < max_tasks:
        if control_store is not None and not control_store.load().execution_allowed:
            break
        try:
            entry = registry.claim_next_queued(now=now or timeutil.utc_now_iso())
        except MvpRuntimeError:
            # An unreadable/unwritable registry cannot be claimed against. Stop draining
            # rather than risk running a task whose claim was not durably recorded — the
            # direction that never runs one twice.
            break
        if entry is None:
            break
        reply, delivered, partial = run_queued_task(
            entry, channel=channel, registration=registration, registry=registry,
            # The drain already holds the control store — it re-reads it before every claim.
            # Threading it into the run is what lets the mid-run halt peek write to the same
            # state, so a `/kill` sent during an analysis reaches the crypto cycle's guard in
            # the other container instead of waiting for this task to finish.
            control_store=control_store,
            now=now, **run_kwargs,
        )
        executed.append(reply)
        if not delivered:
            send_failures += 1
            partial_sends += int(partial)
    return executed, send_failures, partial_sends


def run_operator_once(
    channel: OperatorChannel,
    registration: OperatorIdentity,
    *,
    long_poll_seconds: int = 0,
    provider: Provider | None = None,
    search_tool: Any | None = None,
    working_memory: Any | None = None,
    programization: Any | None = None,
    now: str | None = None,
    store: LedgerStore | None = None,
    control_store: ControlStore | None = None,
    approval_store: Any | None = None,
    registry: Any | None = None,
    frontdesk_provider: Provider | None = None,
    max_queued_tasks: int = 1,
    independent_validation: bool | str = False,
    validator_provider: Provider | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Poll one batch, handle each verified message, and send its reply. ``long_poll_seconds``
    lets a network channel hold the poll open until a message arrives (0 = return immediately).
    ``working_memory`` (opt-in) is shared across handled messages so the operator channel
    accumulates and reuses working memory. ``control_store`` (opt-in) enables the emergency
    console: control commands are handled and a PAUSED/KILLED runtime refuses task requests.
    ``approval_store`` (opt-in) enables the R9 decision path: without it, Thomas's
    ``/approve <id>`` would fall through to the pipeline and be analyzed as a task — the loop
    entrypoint must pass it or the documented answer path does not exist in production.
    Messages that fail the control-channel identity gate are **silently dropped** — an unverified
    sender gets no reply (no engagement, no info leak) and no task runs. Returns a small summary,
    including whether this channel's transport crossed the network (``network_egress``) so the
    loop can observe/report control-channel egress the same way provider/tool egress is recorded
    on the run.

    ``registry`` (opt-in, F1) makes task requests **queued** rather than run inline, and this
    pass then drains up to ``max_queued_tasks`` of them (default 1) after handling messages.
    One per pass is deliberate: coming back to the poll between tasks is what lets ``/tasks``,
    ``/cancel`` and ``/kill`` land while a backlog is still running. When work is already
    waiting, the poll does not long-poll — holding the channel open for a message while a
    queued task sits unstarted would be the one wait nobody asked for."""
    handled: list[OperatorReply] = []
    dropped = 0
    send_failures = 0
    partial_sends = 0
    effective_long_poll = long_poll_seconds
    if registry is not None and long_poll_seconds:
        try:
            if registry.queued_count() > 0:
                effective_long_poll = 0
        except MvpRuntimeError:
            pass        # the drain below reports an unusable registry; the poll proceeds
    for message in channel.poll(long_poll_seconds=effective_long_poll):
        try:
            verify_control_channel(message, registration)
        except OperatorBlocked:
            dropped += 1
            continue
        reply = handle_operator_message(
            message, registration=registration, provider=provider, search_tool=search_tool,
            working_memory=working_memory, programization=programization,
            now=now, store=store, control_store=control_store,
            approval_store=approval_store, registry=registry,
            frontdesk_provider=frontdesk_provider,
            independent_validation=independent_validation,
            validator_provider=validator_provider, repo_root=repo_root,
            # The received-working notice, sent back on the same verified chat the request came
            # from, and swallowed on failure (the notice is a courtesy, not the job). With the
            # registry wired below this never fires — the QUEUED receipt is the immediate reply.
            # Passed unconditionally anyway: running without a registry must not also mean
            # running without the notice.
            ack=lambda text, _chat=message.chat_id: channel.send(_chat, text),
        )
        try:
            channel.send(message.chat_id, reply.text)
        except OperatorBlocked as exc:
            # The batch is already claimed (the poll cursor advanced before handling), so a
            # failed delivery must not abort the remaining messages — a /kill or /approve
            # queued behind this one would be lost forever. The handled work itself is
            # durable (ledger, control state, approval store); only this reply's delivery
            # is lost, and the summary reports it.
            send_failures += 1
            if exc.reason_code == PARTIAL_DELIVERY_REASON:
                partial_sends += 1
        else:
            if reply.status == "COMPLETED" and reply.trace_id:
                # E1: a completed analysis actually reached Thomas — record the pointer
                # /feedback binds to. AFTER the send, so feedback can never target a run
                # he did not see. Best-effort like the ack: losing the pointer degrades
                # /feedback to an honest refusal and must not cost the batch.
                try:
                    operator_feedback.record_delivery(
                        reply.trace_id, now=now or timeutil.utc_now_iso(), repo_root=repo_root,
                    )
                except OperatorBlocked:
                    pass
        handled.append(reply)
    if dropped and store is not None:
        # An unverified sender is still silently dropped — no reply, no engagement, no
        # info leak — but "somebody probed this bot" is worth being able to answer later,
        # and it lived only in an in-memory counter. ONE entry per batch carrying the
        # count, not one per message: a per-message record would make a spammer a
        # disk-fill vector, and the count answers the question just as well.
        try:
            store.append_block(stamped_event(
                "operator_probe.v0", action="unverified_messages_dropped",
                dropped=dropped, channel=PRIMARY_CHANNEL,
                created_at=now or timeutil.utc_now_iso(),
            ))
        except PersistenceError:
            pass          # a diagnostic note must never break the loop

    # The drain runs AFTER the batch is handled, so a /kill or /cancel that arrived in this
    # very batch is already in effect before the next task starts.
    executed, drain_send_failures, drain_partial_sends = drain_queue(
        channel=channel, registration=registration, registry=registry,
        control_store=control_store, max_tasks=max_queued_tasks, now=now,
        provider=provider, search_tool=search_tool, working_memory=working_memory,
        programization=programization, store=store,
        independent_validation=independent_validation,
        validator_provider=validator_provider, repo_root=repo_root,
    )
    send_failures += drain_send_failures
    partial_sends += drain_partial_sends
    return {
        "handled": len(handled),
        "dropped": dropped,
        "executed": len(executed),
        "send_failures": send_failures,
        # The subset of send_failures on which part of the answer DID arrive. Reported
        # separately so the operator log stops calling those "not delivered".
        "partial_sends": partial_sends,
        "replies": handled,
        "executed_replies": executed,
        "network_egress": bool(getattr(channel, "network_egress", False)),
    }
