"""The transport every bridge door sits on — one JSON object in, one out, over a unix socket.

Three doors face the assistant: ``switch_bridge`` (the trading switch), ``read_bridge`` (the
console reads) and ``dispatch_bridge`` (bounded P3 work). They carry different authorities and
must stay separate modules, but they
must **not** answer a malformed frame differently. A door that dies on a bad byte is a
denial of service, and a door that half-applies a truncated request is worse; getting that
right twice, and keeping it right through two future edits, is the drift this module exists
to prevent. So the framing, the deadline, the size cap, and the error envelope live here
once, and each door supplies only its own ``apply`` function.

**Why a unix socket and not a port.** This deployment has never accepted an inbound
connection — no published ports, no listeners, every egress an outbound poll. A TCP listener
would change that posture for the whole system. A socket in a shared directory is a file:
reachable only by a process that can already open a path on this host, which is the same
authentication the local console rests on (``console_cli``: *"physical/SSH access to the
host IS the operator authentication"*). No ``network_access`` grant is involved because no
network is.

Windows has no ``AF_UNIX``, so ``socketserver.ThreadingUnixStreamServer`` does not exist
there — and naming it at class-definition time made the importing module unimportable on
that platform, which took its permission-surface tests down with it. The doors are
deployment-only (Linux containers); the rules they enforce are not. So the base is resolved
at import and an absent one becomes a typed refusal at the moment someone tries to listen.
"""

from __future__ import annotations

import json
import os
import socket
import socketserver
import stat
from pathlib import Path
from typing import Any, Callable

from .errors import ControlBlocked, MvpRuntimeError

# The actor every door attributes its effects to. Deliberately NOT ``console_cli.LOCAL_ACTOR``:
# an operator reading the ledger must be able to tell an action that came from SSH from one
# that came from the assistant, which matters precisely because the assistant is the less
# trusted of the two. It lives here, with the transport the doors share, because it is one
# identity for all of them — it used to live in ``halt_bridge`` and was imported from there by
# doors that had nothing to do with halting, which made retiring that door a rename.
ASSISTANT_ACTOR = "assistant_bridge"

# Group-readable/writable and nothing else. The assistant's container runs under a different
# uid than this runtime, so the shared group IS the access control. World access would make
# "any process on this host" the authorization, which is wider than the host-console
# precedent these doors rest on.
SOCKET_MODE = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP  # 0o660

# One request per connection, and a short deadline: a client that opens the socket and then
# says nothing must not be able to hold a door.
REQUEST_TIMEOUT_SECONDS = 10.0

# A console request is a handful of bytes. Anything larger is not one, and reading it would
# only give a malformed frame a bigger buffer to arrive in.
MAX_FRAME_BYTES = 8192

_UNIX_STREAM_SERVER = getattr(socketserver, "ThreadingUnixStreamServer", None)
UNIX_SOCKETS_AVAILABLE = _UNIX_STREAM_SERVER is not None

# What a door's `apply` is handed and what it must return: a decoded request object in, a
# JSON-serialisable reply out, or a raised MvpRuntimeError carrying its reason_code.
ApplyFn = Callable[[Any], dict[str, Any]]


def decode_request(raw: bytes) -> Any:
    """Decode one frame, or raise ``ControlBlocked`` — never a guess at what was meant."""
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ControlBlocked("MALFORMED_REQUEST", f"request is not valid JSON: {exc}") from exc


class _Handler(socketserver.BaseRequestHandler):
    """One JSON object in, one JSON object out, connection closed."""

    def handle(self) -> None:
        self.request.settimeout(REQUEST_TIMEOUT_SECONDS)
        try:
            raw = self._read_frame()
        except (OSError, socket.timeout):
            return  # The peer went away or stalled; there is nobody to answer.

        try:
            payload = self.server.apply(decode_request(raw))    # type: ignore[attr-defined]
        except MvpRuntimeError as exc:
            payload = {"ok": False, "reason_code": exc.reason_code, "reason": str(exc)}
        except Exception as exc:  # noqa: BLE001 — a door must answer, then keep standing
            payload = {"ok": False, "reason_code": "BRIDGE_ERROR", "reason": str(exc)}

        try:
            self.request.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            return

    def _read_frame(self) -> bytes:
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = self.request.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_FRAME_BYTES:
                raise OSError("frame too large")
            if b"\n" in chunk:
                break
        return b"".join(chunks).split(b"\n", 1)[0]


class SocketDoor(_UNIX_STREAM_SERVER or object):  # type: ignore[misc]
    """The listener. Threading so one stalled peer cannot wedge a door."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, path: Path, apply: ApplyFn) -> None:
        if not UNIX_SOCKETS_AVAILABLE:
            raise ControlBlocked(
                "UNIX_SOCKETS_UNAVAILABLE",
                "this platform has no AF_UNIX; the bridge doors listen on a unix socket only",
            )
        self.apply = apply
        path.parent.mkdir(parents=True, exist_ok=True)
        # A stale socket file from an unclean shutdown would make bind() fail. Removing it is
        # safe precisely because a live server holds no lock on the path — if another server
        # is actually running, its own bind already owns the inode, and the deployment
        # contract is one door per socket path.
        if path.exists() and path.is_socket():
            path.unlink()
        super().__init__(str(path), _Handler)
        os.chmod(path, SOCKET_MODE)


def resolve_socket_path(env_var: str, relative: str, root: Path | None = None) -> Path:
    """A door's socket path: ``env_var`` when set, else ``relative`` under the repo root."""
    override = os.environ.get(env_var, "").strip()
    if override:
        return Path(override)
    from .paths import repo_root

    return (root if root is not None else repo_root()) / relative
