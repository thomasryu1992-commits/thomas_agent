"""The runner's two sockets (G's and X's) on the shared door transport, held to the runner's rules.

``socket_door.SocketDoor`` brings the framing, the size cap, the deadline, the bounded handlers and
the one-door-per-path probe. Three of its defaults are wrong for the runner and are replaced here:

- **Who may connect is a literal list, never the environment, and never empty.** The shared door
  reads ``MVP_BRIDGE_CLIENT_UID`` and serves the group alone when it is unset. A runner socket is
  constructed with its permitted uids and refuses to exist without one; the peer's uid is checked
  on the kernel's answer (``SO_PEERCRED``) on every connection, and an unreadable answer refuses.
- **Every reply is a code and its fixed sentence.** The shared handler answers a typed error with
  ``str(exc)`` and an unexpected one with a log reference whose text names the door; here every
  refusal is rebuilt by ``reasons.refusal_reply`` and nothing from the exception leaves.
- **Busy is ``RUNNER_BUSY``**, in the reply shape of the socket that refuses.

The two sockets differ in two ways, both from C2 §6.1: a peer off the list is ``PEER_NOT_PERMITTED`` at
G's socket and ``ACTOR_PEER_NOT_PERMITTED`` at X's (§6.1b), and each socket answers a refusal in its own
closed shape — G in ``deploy_runner_reply``, X in ``deploy_actor_reply`` — so the constructor takes the
refusal code and the refusal builder rather than assuming G's.

**What is still inherited.** The shared constructor reads two environment variables before this
class replaces their effect: ``MVP_BRIDGE_CLIENT_UID`` is parsed (a malformed value refuses to start,
as ``RUNNER_STARTUP_BLOCKED``) and then discarded for the literal list, and ``MVP_BRIDGE_CLIENT_GID``,
when set, hands the socket **and its directory** to that group. The runner's unit environment is
install work (C4) and must not set the second; this class does not undo it. Nothing else here reads
the environment, and ``resolve_socket_path`` (which defaults under the repository) is never called:
the caller passes every path.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from .. import socket_door
from ..errors import MvpRuntimeError
from .reasons import RunnerRefused, refusal_reply

ServeFn = Callable[[Any, int], dict[str, Any]]
RefuseFn = Callable[..., dict[str, Any]]


def gate_refusal(code: str, *, error_ref: str | None = None) -> dict[str, Any]:
    """G's refusal: the caller-facing reply (``deploy_runner_reply``)."""
    return refusal_reply(code, error_ref=error_ref)
_ERRORS = itertools.count(1)


def _encode(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")


def decode_frame(raw: bytes) -> Any:
    """One ASCII JSON object, or ``MALFORMED_REQUEST``. Duplicate keys and non-finite numbers refuse."""
    def no_duplicates(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate key")
            out[key] = value
        return out

    def no_constant(_):
        raise ValueError("non-finite number")

    try:
        return json.loads(raw.decode("ascii"), object_pairs_hook=no_duplicates, parse_constant=no_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise RunnerRefused("MALFORMED_REQUEST") from None


class _Handler(socket_door._Handler):
    def handle(self) -> None:
        self.request.settimeout(self.server.request_timeout_seconds)
        try:
            raw = self._read_frame()
        except OSError:
            return  # gone, stalled, or oversized: nobody to answer, nothing interpreted
        try:
            uid = self.server.peer_uid(self.request)
            payload = self.server.serve(decode_frame(raw), uid)
        except RunnerRefused as exc:
            payload = self.server.refuse(exc.reason_code)
        except Exception as exc:  # noqa: BLE001 — answer with the generic code, keep standing
            ref = f"{os.getpid()}-{next(_ERRORS):04d}"
            sys.stderr.write(f"RUNNER_ERROR[{self.server.door_label}] ref={ref} {type(exc).__name__}\n")
            sys.stderr.flush()
            payload = self.server.refuse("RUNNER_ERROR", error_ref=ref)
        self._send(payload)

    def _send(self, payload: dict[str, Any]) -> None:
        try:
            self.request.sendall(_encode(payload))
        except (OSError, TypeError, ValueError):
            return


class RunnerDoor(socket_door.SocketDoor):
    """A unix socket that admits only ``allowed_uids`` and hands ``serve(frame, peer_uid)`` each frame."""

    def __init__(self, path: Path, serve: ServeFn, *, allowed_uids: frozenset[int],
                 peer_refusal: str = "PEER_NOT_PERMITTED", refuse: RefuseFn = gate_refusal,
                 max_concurrent_requests: int = 2, request_timeout_seconds: float = 10.0) -> None:
        uids = frozenset(allowed_uids)
        if not uids or any(not isinstance(u, int) or isinstance(u, bool) or u < 0 for u in uids):
            raise RunnerRefused("RUNNER_STARTUP_BLOCKED")
        if peer_refusal not in ("PEER_NOT_PERMITTED", "ACTOR_PEER_NOT_PERMITTED"):
            raise RunnerRefused("RUNNER_STARTUP_BLOCKED")
        try:
            super().__init__(Path(path), lambda _frame: {}, max_concurrent_requests=max_concurrent_requests,
                             max_frame_bytes=socket_door.MAX_FRAME_BYTES,
                             request_timeout_seconds=request_timeout_seconds)
        except MvpRuntimeError:
            raise RunnerRefused("RUNNER_STARTUP_BLOCKED") from None
        self.allowed_client_uids = uids  # replaces whatever the environment said
        self.RequestHandlerClass = _Handler
        self.serve = serve
        self.refuse = refuse
        self.peer_refusal = peer_refusal

    def authorize_peer(self, request: Any) -> None:
        self.peer_uid(request)

    def peer_uid(self, request: Any) -> int:
        creds = socket_door.peer_credentials(request)
        if creds is None:
            raise RunnerRefused("CREDENTIALS_UNAVAILABLE")
        if creds[1] not in self.allowed_client_uids:
            raise RunnerRefused(self.peer_refusal)
        return creds[1]

    def _refuse_busy(self, request: Any) -> None:
        try:
            request.settimeout(self.request_timeout_seconds)
            request.sendall(_encode(self.refuse("RUNNER_BUSY")))
        except OSError:
            return
