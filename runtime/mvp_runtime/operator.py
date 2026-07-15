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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import OperatorBlocked
from .pipeline import run_task
from .store import LedgerStore
from .worker import Provider

# Local, per-machine (gitignored) registration of the single authorized operator — like the
# Core pointer and safety-flag activation, this is machine state, not shared source.
REGISTRATION_REL = ".runtime_governance_state/operator_registration.json"
PRIMARY_CHANNEL = "telegram_private"
REQUIRED_APPROVER = "Thomas"


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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


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
    now: str | None = None,
    store: LedgerStore | None = None,
    repo_root: Path | None = None,
) -> OperatorReply:
    """Verify an inbound operator message and, only if it is from the registered operator,
    run the task and return the reply. An unverified message is refused with a generic reason
    and no task runs. Never raises for a fail-closed condition — those become a REFUSED reply.
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

    result = run_task(
        text,
        provider=provider,
        search_tool=search_tool,
        now=now,
        store=store,
        repo_root=repo_root,
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
    return OperatorReply(
        text=f"Your request was not completed ({block.get('reason_code', 'BLOCKED')}).",
        accepted=True, status="BLOCKED", reason_code=block.get("reason_code"), trace_id=trace_id,
    )
