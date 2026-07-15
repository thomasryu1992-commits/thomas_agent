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
from typing import Any, Protocol, runtime_checkable

from . import safety_gate
from .errors import OperatorBlocked
from .pipeline import run_task
from .safety_gate import NETWORK_ACCESS, Authorization
from .store import LedgerStore
from .worker import Provider

# Local, per-machine (gitignored) registration of the single authorized operator — like the
# Core pointer and safety-flag activation, this is machine state, not shared source.
REGISTRATION_REL = ".runtime_governance_state/operator_registration.json"
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


# --- R4.2: the channel transport (mock default; real Telegram behind the gate) ----------


@runtime_checkable
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
    var alone fails closed (``SafetyGateBlocked``), never silently opening a network path."""
    choice = os.environ.get(OPERATOR_CHANNEL_ENV, "").strip().lower()
    if choice != TELEGRAM:
        return MockOperatorChannel()
    authorization = safety_gate.authorize(
        _NETWORK_FLAGS, provider_id=TELEGRAM, now=now or safety_gate.utc_now_iso(), root=root
    )
    return TelegramChannel(authorization=authorization)


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

    def __init__(self, *, token_env: str = "TELEGRAM_BOT_TOKEN", authorization: Authorization | None = None):
        self._token_env = token_env  # the NAME of the env var, never the value
        self._authorization = authorization
        self._offset = 0  # getUpdates cursor; advances past processed updates

    def _assert(self) -> str:
        safety_gate.assert_authorization(
            self._authorization, required_flags=_NETWORK_FLAGS, provider_id=self.provider_id,
            now=safety_gate.utc_now_iso(),
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

    def poll(self, *, long_poll_seconds: int = 0) -> list[InboundMessage]:
        # Real long-poll: getUpdates holds the connection up to ``long_poll_seconds`` server
        # side; the HTTP timeout must outlast that hold (+buffer) or it would abort early.
        token = self._assert()
        http_timeout = (long_poll_seconds + self._HTTP_TIMEOUT_BUFFER) if long_poll_seconds > 0 else self._DEFAULT_HTTP_TIMEOUT
        payload = self._call(
            token, "getUpdates",
            {"offset": self._offset, "timeout": long_poll_seconds, "allowed_updates": json.dumps(["message"])},
            timeout=http_timeout,
        )
        messages: list[InboundMessage] = []
        for update in payload.get("result", []):
            if not isinstance(update, dict):
                continue
            self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
            messages.append(_message_from_update(update))
        return [m for m in messages if m is not None]

    def send(self, chat_id: str, text: str) -> None:
        token = self._assert()
        self._call(token, "sendMessage", {"chat_id": chat_id, "text": text}, timeout=30)


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
    now: str | None = None,
    store: LedgerStore | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Poll one batch, handle each verified message, and send its reply. ``long_poll_seconds``
    lets a network channel hold the poll open until a message arrives (0 = return immediately).
    Messages that fail the control-channel identity gate are **silently dropped** — an
    unverified sender gets no reply (no engagement, no info leak) and no task runs. Returns a
    small summary."""
    handled: list[OperatorReply] = []
    dropped = 0
    for message in channel.poll(long_poll_seconds=long_poll_seconds):
        try:
            verify_control_channel(message, registration)
        except OperatorBlocked:
            dropped += 1
            continue
        reply = handle_operator_message(
            message, registration=registration, provider=provider, search_tool=search_tool,
            now=now, store=store, repo_root=repo_root,
        )
        channel.send(message.chat_id, reply.text)
        handled.append(reply)
    return {"handled": len(handled), "dropped": dropped, "replies": handled}
