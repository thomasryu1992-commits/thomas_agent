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

Network-free by construction: like ``MockProvider`` / ``MockSearchTool``, this handles an
already-received message. The real Telegram network adapter (long-poll/webhook, bot token
by env var) goes behind the Safety-Flag Gate in a later increment; nothing here opens a
socket. Emergency operator-console controls (pause/stop/kill/status) are also later.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import approval, control, memory_console, operator_feedback, safety_gate, timeutil
from .audit import build_approval_decision_audit, build_audit_gap_record
from .control import ControlStore
from .errors import ApprovalBlocked, AuditError, ControlBlocked, OperatorBlocked, PersistenceError
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
    independent_validation: bool | str = False,
    validator_provider: Provider | None = None,
    repo_root: Path | None = None,
    ack: Any | None = None,
) -> OperatorReply:
    """Verify an inbound operator message and, only if it is from the registered operator,
    handle it. An unverified message is refused with a generic reason and no task runs. Never
    raises for a fail-closed condition — those become a REFUSED reply.

    ``ack`` (optional, ``Callable[[str], None]``) is called with a short "received, working"
    notice at exactly one point: after EVERY refusal path (identity gate, empty request,
    console/approval command routing, unknown-command refusal, kill switch) and immediately
    before the pipeline runs. A pipeline run holds the channel for the length of a model
    call, and to the operator that silence was indistinguishable from a dead service. The
    ack is a ``CONTROL_CHANNEL_RESPONSE`` (ALLOW) on the already-verified channel, and it is
    **best-effort**: a failed ack send must never cost the run itself, so failures are
    swallowed here rather than propagated.

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
                store=store, now=now, repo_root=repo_root,
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

    if text.startswith("/"):
        # A leading-slash message that matched no console/approval verb is refused, never
        # run as a task: a typo'd ``/killl`` (or an emergency verb reaching a deployment
        # without its store wired) silently becoming a full pipeline run — model call
        # included — is the fail-open direction.
        return OperatorReply(
            text=("Unknown command. Available: /status /pause /kill /resume /stop <task_id> "
                  "/audit /recovery /approve <id> [reason] /reject <id> [reason] "
                  "/feedback <good|bad|한줄평> /memory /promote <id> <사유>"),
            accepted=False, status="REFUSED", reason_code="UNKNOWN_COMMAND",
        )

    if control_store is not None:
        # A task request is refused while the runtime is not ACTIVE (kill blocks new execution).
        state = control_store.load()
        if not state.execution_allowed:
            reason_code = state.refusal_reason_code()
            return OperatorReply(
                text=f"Runtime is {state.mode}; new requests are blocked. Send /resume to continue (or /status).",
                accepted=False, status="REFUSED", reason_code=reason_code,
            )

    # R7.1: a leading importance marker raises the task's priority, which (under the
    # "auto" validation policy) adds the independent reviewer to exactly this request.
    # The marker must be its own leading token — "!중요한 아이디어..." is prose, not a flag.
    priority = "NORMAL"
    head, _, rest = text.partition(" ")
    if head.lower() in IMPORTANT_MARKERS:
        priority = "HIGH"
        text = rest.strip()
        if not text:
            return OperatorReply(
                text=f"'{head}' 뒤에 분석할 요청을 함께 보내주세요 (예: {head} 이 사업 아이디어를 분석해줘: ...).",
                accepted=False, status="REFUSED", reason_code="EMPTY_REQUEST",
            )

    # Every refusal path is behind us: this message WILL run the pipeline. Say so now —
    # the model call takes tens of seconds and the operator otherwise stares at silence.
    if ack is not None:
        marker_note = ""
        if priority == "HIGH":
            # Truthful note: the marker adds the reviewer only when a validation policy
            # is active ("auto" or always-on); otherwise it is recorded priority only.
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
        channel="telegram",
        requester_type="real_thomas",
        requester_id=registration.operator_id,
        authenticated=True,
        source_ref=f"telegram:private_chat:{message.chat_id}",
    )
    trace_id = result.get("records", {}).get("received_task", {}).get("identity", {}).get("trace_id")
    if result["status"] == "COMPLETED":
        return OperatorReply(text=result["final_response"], accepted=True, status="COMPLETED", trace_id=trace_id)

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
    def send(self, chat_id: str, text: str) -> None: ...


@dataclass
class MockOperatorChannel:
    """Deterministic, network-free channel for tests and local runs. ``inbound`` is drained
    on each ``poll``; ``sent`` captures ``(chat_id, text)`` for assertions. ``long_poll_seconds``
    is accepted for protocol parity but ignored — the in-memory queue returns immediately."""

    inbound: list[InboundMessage] = field(default_factory=list)
    sent: list[tuple[str, str]] = field(default_factory=list)
    network_egress: bool = False
    last_long_poll_seconds: int | None = None

    def poll(self, *, long_poll_seconds: int = 0) -> list[InboundMessage]:
        self.last_long_poll_seconds = long_poll_seconds
        batch, self.inbound = list(self.inbound), []
        return batch

    def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


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

    # Telegram rejects a sendMessage over 4096 UTF-16 code units. Split just under that so a
    # substantive analysis is delivered as several messages instead of failing outright — an
    # undeliverable reply after a completed run burns the model call and loses the answer.
    _MAX_SEND_UNITS = 4000

    def send(self, chat_id: str, text: str) -> None:
        token = self._assert()
        for chunk in _split_for_send(text, self._MAX_SEND_UNITS):
            self._call(token, "sendMessage", {"chat_id": chat_id, "text": chunk}, timeout=30)


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
    on the run."""
    handled: list[OperatorReply] = []
    dropped = 0
    send_failures = 0
    for message in channel.poll(long_poll_seconds=long_poll_seconds):
        try:
            verify_control_channel(message, registration)
        except OperatorBlocked:
            dropped += 1
            continue
        reply = handle_operator_message(
            message, registration=registration, provider=provider, search_tool=search_tool,
            working_memory=working_memory, programization=programization,
            now=now, store=store, control_store=control_store,
            approval_store=approval_store,
            independent_validation=independent_validation,
            validator_provider=validator_provider, repo_root=repo_root,
            # The received-working notice, sent back on the same verified chat the request
            # came from. handle_operator_message fires it only once every refusal path has
            # passed, and swallows a send failure (the notice is a courtesy, not the job).
            ack=lambda text, _chat=message.chat_id: channel.send(_chat, text),
        )
        try:
            channel.send(message.chat_id, reply.text)
        except OperatorBlocked:
            # The batch is already claimed (the poll cursor advanced before handling), so a
            # failed delivery must not abort the remaining messages — a /kill or /approve
            # queued behind this one would be lost forever. The handled work itself is
            # durable (ledger, control state, approval store); only this reply's delivery
            # is lost, and the summary reports it.
            send_failures += 1
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
    return {
        "handled": len(handled),
        "dropped": dropped,
        "send_failures": send_failures,
        "replies": handled,
        "network_egress": bool(getattr(channel, "network_egress", False)),
    }
