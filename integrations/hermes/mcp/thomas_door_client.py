"""One socket client for the four Thomas doors — door API v2 frames, one receive loop.

Until 2026-09-04 each shim carried its own copy of the socket block (four of them, three
receive predicates, two `json.dumps` conventions). This module is that block, once. It holds
no authority: a frame is a request, the far side decides, and the only identity that matters
is the socket peer's uid (`SO_PEERCRED`) — nothing in a frame changes what a door will do.

What a v2 frame adds (`docs/runtime-contracts/DOOR_API_V2_DESIGN_V0.1.md`, proposal 1):

- ``proto: 2`` — asks for the typed reply. A v1 door ignores it? No: a door that does not know
  proto 2 refuses with ``PROTO_UNSUPPORTED``, which is the honest answer and is rendered as
  such. Every door on this host has spoken v2 since candidate-833.
- ``client_id`` — attribution only (the registry's ``created_by = assistant_bridge:<id>``).
  It is NOT identity and grants nothing. ``THOMAS_CLIENT_ID`` in the environment overrides
  the default; an invalid value falls back rather than being sent for the door to refuse.
- ``request_id`` — the idempotency key, only where the caller asks for one (dispatch, and the
  switch door's enable). Re-sending the same id within 24 h returns the run's
  ``{task_id, status, result}`` instead of starting a second run (proposal 3).

Every reply carries ``data`` (a dict, possibly empty) beside the human ``reply``; a refusal
echoes the envelope and may carry ``data`` too (``REQUEST_IN_FLIGHT`` says ``status: RUNNING``).
"""

from __future__ import annotations

import json
import os
import re
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

PROTO = 2
CLIENT_ID_ENV = "THOMAS_CLIENT_ID"
DEFAULT_CLIENT_ID = "hermes:telegram"
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

# Reason codes this side renders specially. Strings, not imports: the runtime is on the other
# side of a socket and this container never has its modules.
PROTO_UNSUPPORTED = "PROTO_UNSUPPORTED"
REQUEST_IN_FLIGHT = "REQUEST_IN_FLIGHT"
PEER_NOT_PERMITTED = "PEER_NOT_PERMITTED"


@dataclass(frozen=True)
class Door:
    name: str
    socket_env: str
    default_path: str
    timeout_seconds: float
    service: str
    # What the model must do when the door cannot be reached — door-specific because the
    # wrong instruction is the failure: "do not guess numbers" is right for a read and
    # meaningless for a stop, where the right instruction is "use the control channel".
    when_unavailable: str

    @property
    def path(self) -> str:
        return os.environ.get(self.socket_env, self.default_path)


DOORS: dict[str, Door] = {
    "read": Door(
        "read", "THOMAS_READ_SOCKET", "/opt/bridge/read.sock", 20.0, "read-bridge",
        "Say so — do not answer from memory and do not guess numbers.",
    ),
    "switch": Door(
        "switch", "THOMAS_SWITCH_SOCKET", "/opt/bridge/switch.sock", 20.0, "switch-bridge",
        "Tell Thomas to use the Telegram control channel instead — do not report this as done.",
    ),
    # A dispatch RUNS the pipeline, so the answer can take minutes. 280s sits deliberately
    # UNDER the Hermes MCP client's own 300s ceiling: the shim's honest message must arrive
    # before the client's blunt TimeoutError tears the connection down (measured 2026-08-10).
    "dispatch": Door(
        "dispatch", "THOMAS_DISPATCH_SOCKET", "/opt/bridge/dispatch.sock", 280.0, "dispatch-bridge",
        "Do not fabricate a result.",
    ),
    # An ingest may run pdftotext and re-index; the door's own deadline is 180s.
    "knowledge": Door(
        "knowledge", "THOMAS_KNOWLEDGE_SOCKET", "/opt/bridge/knowledge.sock", 180.0, "knowledge-bridge",
        "Say so — do not answer from memory and do not describe documents you think were filed.",
    ),
}

# Transport outcomes, named so a shim can tell "nothing was sent" from "sent, no answer in time".
NO_SOCKET = "NO_SOCKET"
NOT_SENT = "NOT_SENT"
TIMEOUT_AFTER_SEND = "TIMEOUT_AFTER_SEND"
EMPTY_REPLY = "EMPTY_REPLY"
UNPARSEABLE = "UNPARSEABLE"
FRAME_TOO_LARGE = "FRAME_TOO_LARGE"


@dataclass
class Answer:
    """What came back: a parsed frame (``frame``), or a transport failure (``failure``)."""

    door: Door
    frame: dict[str, Any] | None = None
    failure: str | None = None
    detail: str = ""
    sent: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.frame and self.frame.get("ok"))

    @property
    def reason_code(self) -> str | None:
        return None if self.frame is None else self.frame.get("reason_code")

    @property
    def reason(self) -> str:
        return "" if self.frame is None else str(self.frame.get("reason") or "")

    @property
    def data(self) -> dict[str, Any]:
        value = None if self.frame is None else self.frame.get("data")
        return value if isinstance(value, dict) else {}

    @property
    def reply(self) -> str:
        return "" if self.frame is None else str(self.frame.get("reply") or "")

    @property
    def replayed(self) -> bool:
        return bool(self.frame and self.frame.get("replayed"))

    @property
    def request_id(self) -> str | None:
        return None if self.frame is None else self.frame.get("request_id")

    def get(self, key: str, default: Any = None) -> Any:
        return default if self.frame is None else self.frame.get(key, default)

    def failure_text(self) -> str:
        """The standard UNAVAILABLE / UNCLEAR sentence for a transport failure. A shim that
        must say something door-specific (the dispatch door's "started but slow") checks
        ``failure`` itself before falling back to this."""
        door = self.door
        if self.failure == NO_SOCKET:
            return (
                f"UNAVAILABLE: no {door.name} door at {door.path}. The Thomas {door.service} "
                f"service is not running or the socket is not mounted. {door.when_unavailable}"
            )
        if self.failure in (NOT_SENT, TIMEOUT_AFTER_SEND):
            return (
                f"UNAVAILABLE: could not reach the {door.name} door ({self.detail}). "
                f"{door.when_unavailable}"
            )
        if self.failure == FRAME_TOO_LARGE:
            return f"REFUSED: {self.detail}"
        if self.failure == EMPTY_REPLY:
            return f"UNCLEAR: the {door.name} door returned nothing. Assume nothing was delivered."
        return f"UNCLEAR: the {door.name} door returned something unparseable: {self.detail!r}."

    def refused_text(self, *, suffix: str = "") -> str:
        """``REFUSED [code]: reason`` — the two codes a v2 client can explain get a sentence."""
        code = self.reason_code
        if code == PROTO_UNSUPPORTED:
            return (
                f"UNAVAILABLE: the Thomas {self.door.name} door on this machine does not speak "
                f"door API v2 ({self.reason}). Nothing was done. Tell Thomas the runtime needs "
                "redeploying before this tool can be used."
            )
        if code == PEER_NOT_PERMITTED:
            return (
                f"REFUSED [{code}]: {self.reason}. This process is not the door's declared "
                "client — a container or uid problem, not a request problem."
            )
        return f"REFUSED [{code}]: {self.reason}.{suffix}"


def client_id() -> str:
    """Attribution for the registry — never identity. Falls back rather than sends an invalid
    value: a refused frame for a cosmetic field would read as a dead door."""
    value = (os.environ.get(CLIENT_ID_ENV) or "").strip()
    return value if _CLIENT_ID_RE.match(value) else DEFAULT_CLIENT_ID


def new_request_id() -> str:
    return f"hermes-{uuid.uuid4().hex}"


def frame(payload: dict[str, Any], *, request_id: str | None = None) -> dict[str, Any]:
    """The v2 envelope around a door's own keys. ``request_id`` only when the caller wants
    idempotency — the read door refuses keys it does not name."""
    out: dict[str, Any] = dict(payload)
    out["proto"] = PROTO
    out["client_id"] = client_id()
    if request_id:
        out["request_id"] = request_id
    return out


def encode(payload: dict[str, Any]) -> bytes:
    # ensure_ascii=False: half of every frame is Korean, and the doors decode UTF-8.
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def stamp(reply: str) -> str:
    """Prefix a rendered board with the instant it was rendered — the 2026-08-10 rule.

    Only a successful read is stamped. UNAVAILABLE / UNCLEAR / REFUSED already say in words
    that they carry no numbers, and dating a refusal would suggest it did.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"[SNAPSHOT {now} · 이 호출 시점의 값]\n"
        "나중에 이 블록을 인용하려면 이 시각을 함께 말해라. "
        "지금 상태를 묻는 질문의 답으로는 쓰지 말고 새로 호출해라.\n\n"
        f"{reply}"
    )


def ask(
    door_name: str,
    payload: dict[str, Any],
    *,
    request_id: str | None = None,
    max_frame_bytes: int | None = None,
    timeout_seconds: float | None = None,
) -> Answer:
    """Send one v2 frame to one door and return what came back, parsed.

    Never raises for a transport problem: the answer says which one, and ``sent`` says
    whether the far side may have acted. That last bit is the whole reason the failures are
    distinguished — before 2026-08-10 a run that connected, started and outlived the wait
    produced the same words as a dead socket, and the retry started a duplicate run.
    """
    door = DOORS[door_name]
    answer = Answer(door=door)
    path = door.path
    if not os.path.exists(path):
        answer.failure = NO_SOCKET
        return answer
    wire = encode(frame(payload, request_id=request_id))
    if max_frame_bytes is not None and len(wire) > max_frame_bytes:
        answer.failure = FRAME_TOO_LARGE
        answer.detail = (
            f"the request is {len(wire)} bytes, over the door's {max_frame_bytes}-byte frame. "
            "Split the document or file the source file."
        )
        return answer
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(door.timeout_seconds if timeout_seconds is None else timeout_seconds)
            client.connect(path)
            client.sendall(wire)
            answer.sent = True
            chunks: list[bytes] = []
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
            raw = b"".join(chunks).decode("utf-8").strip()
    except OSError as exc:
        answer.failure = TIMEOUT_AFTER_SEND if answer.sent else NOT_SENT
        answer.detail = str(exc)
        return answer
    if not raw:
        answer.failure = EMPTY_REPLY
        return answer
    try:
        parsed = json.loads(raw.split("\n", 1)[0])
    except ValueError:
        answer.failure = UNPARSEABLE
        answer.detail = raw[:400]
        return answer
    if not isinstance(parsed, dict):
        answer.failure = UNPARSEABLE
        answer.detail = raw[:400]
        return answer
    answer.frame = parsed
    return answer
