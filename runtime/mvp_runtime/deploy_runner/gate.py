"""G, the gatekeeper: the runner's outward face, with no authority of its own.

C2 specification §0N (N1, Thomas DESIGN_ONLY 2026-10-16). G runs without Docker, without a secret
and without the final judgement. It checks the frame's shape so a malformed request costs the
actor nothing, writes its intake log, forwards, and checks that what comes back is a reply it may
pass on. It does **not** verify signatures, spend approvals, or decide anything the actor would
decide: the actor re-validates every field G forwards and trusts none of G's conclusions, so a
compromised G can lose requests or lie in its own log, and can do nothing an unsigned caller
cannot (GAP-2A–2C).

The intake log is G's own bookkeeping. The actor never reads it; its ``seq`` travels to the actor
as ``gate_intake_seq`` only so an operator can line the two records up.

Order per frame:

1. the peer's uid, on the kernel's answer, against G's literal list (``door.RunnerDoor``);
2. the frame (§6.1a): closed keys — ``proto`` 2, ``client_id``, ``request_id``, ``verb``, ``args``, and
   for an operational action ``approval_document`` (the signed bytes as base64 text) and
   ``approval_signature`` — a known verb and its closed arguments (``check_args``). No approval is
   opened here;
3. ``FORWARDED`` written and ``fsync``-ed, then the actor called with a deadline;
4. the actor's reply held to ``deploy_actor_reply`` (``thomas.actor_reply.v1``); the caller's reply built
   from it — the table's sentence for a refusal, the measured ids for ``FAILED_EFFECT_PARTIAL``, never
   X's ``state`` — and held to ``deploy_runner_reply``; anything else is ``ACTOR_REPLY_INVALID`` /
   ``REPLY_SCHEMA_INVALID``;
5. ``ANSWERED`` with the code that went out.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .. import schema_cache, socket_door
from ..errors import MvpRuntimeError
from .approval_verify import SCHEMAS_DIR, check_args
from .canonical import MAX_DOCUMENT_BYTES, MAX_SIGNATURE_BYTES
from .private_files import append_row, lock_exclusive, open_private, parse_rows, read_all
from .reasons import RunnerRefused, refusal_reply

INTAKE_FILE = "intake.jsonl"
INTAKE_SCHEMA = "deploy_gate_intake.v0.1.schema.json"
ACTOR_REPLY_SCHEMA = "deploy_actor_reply.v0.1.schema.json"
RUNNER_REPLY_SCHEMA = "deploy_runner_reply.v0.1.schema.json"
PROTO = 2
ACTOR_SCHEMA = "thomas.actor_request.v1"
FRAME_KEYS = frozenset({"proto", "client_id", "request_id", "verb", "args", "approval_document",
                        "approval_signature"})
_TOKEN = re.compile(r"^[A-Za-z0-9._:-]{1,128}\Z")
_CLIENT = re.compile(r"^[A-Za-z0-9._:-]{1,64}\Z")
_LOG = "AUDIT_WRITE_FAILED"
# The carriers' ceilings, the ones ``canonical`` enforces again at the actor (C2 §1.3: document 2,048
# bytes, signature 4,096; the whole frame inside the shared door's 8,192).
MAX_DOCUMENT_B64 = (MAX_DOCUMENT_BYTES * 4) // 3 + 4


def _stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_hash(row: dict[str, Any]) -> str:
    return integrity.sha256_value({k: v for k, v in row.items() if k != "row_sha256"})


def check_frame(frame: Any) -> tuple[dict[str, Any], str | None]:
    """The caller's frame, held to its closed shape: the message for X (§6.1b, without the sequence
    number) and the ``client_id``, which stays with G. Raises the code a malformed frame earns."""
    if not isinstance(frame, dict):
        raise RunnerRefused("MALFORMED_REQUEST")
    if set(frame) - FRAME_KEYS:
        raise RunnerRefused("ARGUMENT_NOT_ACCEPTED")
    if frame.get("proto") != PROTO or isinstance(frame.get("proto"), bool):
        raise RunnerRefused("MALFORMED_REQUEST")
    client_id = frame.get("client_id")
    if client_id is not None and (not isinstance(client_id, str) or not _CLIENT.match(client_id)):
        raise RunnerRefused("MALFORMED_REQUEST")
    request_id = frame.get("request_id")
    if request_id is not None and (not isinstance(request_id, str) or not _TOKEN.match(request_id)):
        raise RunnerRefused("MALFORMED_REQUEST")
    if "verb" not in frame or "args" not in frame:
        raise RunnerRefused("MALFORMED_REQUEST")
    args = check_args(frame["verb"], frame["args"])
    document = frame.get("approval_document")
    signature = frame.get("approval_signature")
    for value, ceiling in ((document, MAX_DOCUMENT_B64), (signature, MAX_SIGNATURE_BYTES)):
        if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= ceiling):
            raise RunnerRefused("APPROVAL_MALFORMED")
    message: dict[str, Any] = {"schema": ACTOR_SCHEMA, "request_id": request_id, "verb": frame["verb"], "args": args}
    if document is not None:
        message["approval_document_b64"] = document
    if signature is not None:
        message["approval_signature"] = signature
    return message, client_id


class IntakeLog:
    """G's hash-chained intake log in G's own private directory."""

    def __init__(self, directory: os.PathLike[str] | str) -> None:
        self._dir = Path(directory)

    def read(self) -> list[dict[str, Any]]:
        try:
            fd = open_private(self._dir, INTAKE_FILE, os.O_RDONLY, code=_LOG)
        except FileNotFoundError:
            return []
        try:
            return self._verified(fd)
        finally:
            os.close(fd)

    def _verified(self, fd: int) -> list[dict[str, Any]]:
        rows = parse_rows(read_all(fd, code=_LOG), code=_LOG)
        previous = None
        for seq, row in enumerate(rows, start=1):
            _validate(row, INTAKE_SCHEMA, _LOG)
            if row["seq"] != seq or row["previous_row_sha256"] != previous or row["row_sha256"] != _row_hash(row):
                raise RunnerRefused(_LOG)
            previous = row["row_sha256"]
        return rows

    def append(self, *, event: str, now: str, peer_uid: int, frame: dict[str, Any] | None,
               reason_code: str | None, client_id: str | None = None) -> int:
        fd = open_private(self._dir, INTAKE_FILE, os.O_RDWR | os.O_APPEND, code=_LOG, create=True)
        try:
            lock_exclusive(fd, code=_LOG)
            rows = self._verified(fd)
            row: dict[str, Any] = {
                "seq": len(rows) + 1, "recorded_at": now, "event": event, "peer_uid": peer_uid,
                "client_id": client_id,
                "request_id": frame["request_id"] if frame else None,
                "verb": frame["verb"] if frame else None,
                "frame_sha256": integrity.sha256_value(frame) if frame else None,
                "reason_code": reason_code,
                "previous_row_sha256": rows[-1]["row_sha256"] if rows else None,
            }
            row["row_sha256"] = _row_hash(row)
            _validate(row, INTAKE_SCHEMA, _LOG)
            append_row(fd, row, code=_LOG)
            return row["seq"]
        finally:
            os.close(fd)


def _validate(value: Any, schema: str, code: str) -> None:
    try:
        schema_cache.validate_against_schema(value, SCHEMAS_DIR / schema, schema)
    except RuntimeSchemaError:
        raise RunnerRefused(code) from None


def call_actor_socket(path: Path, message: dict[str, Any], *, deadline_s: float) -> Any:
    """The transport to the actor. Any failure to get a JSON answer is ``ACTOR_UNAVAILABLE``."""
    try:
        return socket_door.call_door(path, message, deadline_seconds=deadline_s, max_reply_bytes=64 * 1024)
    except MvpRuntimeError:
        raise RunnerRefused("ACTOR_UNAVAILABLE") from None


@dataclass
class GateConfig:
    directory: Path
    actor_socket: Path
    actor_deadline_s: float = 900.0
    call_actor: Callable[..., Any] = call_actor_socket
    wall_now: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))


class Gate:
    def __init__(self, config: GateConfig) -> None:
        self.config = config
        self.log = IntakeLog(config.directory)

    def serve(self, frame: Any, peer_uid: int) -> dict[str, Any]:
        """One caller frame in, one caller reply out. A refusal is a reply; G's own log failing is too."""
        stamp = _stamp(self.config.wall_now())
        request_id = frame.get("request_id") if isinstance(frame, dict) else None
        try:
            message, client_id = check_frame(frame)
        except RunnerRefused as exc:
            self.log.append(event="REFUSED", now=stamp, peer_uid=peer_uid, frame=None, reason_code=exc.reason_code)
            return self._out(refusal_reply(exc.reason_code, request_id=request_id), frame)
        seq = self.log.append(event="FORWARDED", now=stamp, peer_uid=peer_uid, frame=message, reason_code=None,
                              client_id=client_id)
        try:
            answer = self.config.call_actor(self.config.actor_socket, {**message, "gate_intake_seq": seq},
                                            deadline_s=self.config.actor_deadline_s)
            _validate(answer, ACTOR_REPLY_SCHEMA, "ACTOR_REPLY_INVALID")
            # X echoes the id or, when it refused before reading one (peer, frame, busy), says none.
            if answer["request_id"] not in (None, message["request_id"]):
                raise RunnerRefused("ACTOR_REPLY_INVALID")
            reply = self._from_actor(answer, message, frame)
            _validate(reply, RUNNER_REPLY_SCHEMA, "REPLY_SCHEMA_INVALID")
        except RunnerRefused as exc:
            reply = self._out(refusal_reply(exc.reason_code, request_id=message["request_id"]), frame)
        self.log.append(event="ANSWERED", now=_stamp(self.config.wall_now()), peer_uid=peer_uid, frame=message,
                        reason_code=None if reply["ok"] else reply["reason_code"], client_id=client_id)
        return reply

    def _from_actor(self, answer: dict[str, Any], message: dict[str, Any], frame: Any) -> dict[str, Any]:
        """The caller's reply, rebuilt from X's: X's ``state`` stays between G and X (§6.1a has no such
        field), and a refusal's sentence is the table's, never X's."""
        if answer["ok"]:
            reply: dict[str, Any] = {"ok": True, "data": answer["data"], "replayed": answer["replayed"]}
            if message["request_id"] is not None:
                reply["request_id"] = message["request_id"]
            return self._out(reply, frame)
        reply = {**refusal_reply(answer["reason_code"], request_id=message["request_id"]),
                 "replayed": answer["replayed"]}
        if answer["reason_code"] == "FAILED_EFFECT_PARTIAL" and "data" in answer:
            reply["data"] = answer["data"]          # the measured ids, schema-checked on both sides
        return self._out(reply, frame)

    @staticmethod
    def _out(reply: dict[str, Any], frame: Any) -> dict[str, Any]:
        reply = dict(reply, proto=PROTO)
        client_id = frame.get("client_id") if isinstance(frame, dict) else None
        if isinstance(client_id, str) and _CLIENT.match(client_id):
            reply["client_id"] = client_id
        return reply
