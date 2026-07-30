"""The halt-only door — an external assistant may stop this runtime, never start it.

Thomas runs a conversational assistant (Hermes) beside this runtime and wants to halt
trading by talking to it rather than by switching windows. This module is that door, and
it is deliberately a *door*, not a channel: it carries two verbs and cannot be made to
carry a third.

**Why only ``kill`` and ``pause``.** Halting is idempotent and fail-safe — applying KILLED
to an already-KILLED runtime is the same runtime, and the worst outcome of a spurious halt
is that trading stops. Resuming is neither. ``resume`` re-arms an autonomous path that
places real orders, and the assistant on the other end of this socket is a model that reads
untrusted text (web pages, search results) and can be talked into calling a tool. So the
asymmetry is not a policy knob: ``_ALLOWED`` is built from ``control``'s own constants and
the dispatch refuses anything outside it, which means no configuration, no environment
variable, and no prompt can reach ``resume`` through this file.

The runtime already wrote this asymmetry down once. ``operator.PEEKABLE_HALT_VERBS`` admits
``kill``/``pause`` and excludes ``resume`` for the same reason in a different setting (a
re-read message must not undo a halt). This module extends that judgement rather than
inventing one.

**Why a unix socket and not a port.** This deployment has never accepted an inbound
connection — no published ports, no listeners, every egress an outbound poll. A TCP
listener would change that posture for the whole system to buy one verb. A unix socket in a
shared directory is a file: it is reachable only by a process that can already open a path
on this host, which is the same authentication the local console rests on
(``console_cli``: *"physical/SSH access to the host IS the operator authentication"*). No
``network_access`` grant is involved because no network is involved.

**Authority.** ``kill`` and ``pause`` are both named in
``governance/GOVERNANCE_POLICY.yaml`` under
``control_channel.local_operator_console.emergency_controls_allowed``. This module adds no
verb to that list and asks for no new grant — it is a second way to reach an already
granted emergency control, from a process that already has host filesystem access.

**Every halt is attributable.** The actor recorded on the control event is ``ASSISTANT_ACTOR``,
never ``local_console``. An operator reading the ledger can tell a halt that came from SSH
from one that came from the assistant, which matters precisely because the assistant is the
less trusted of the two. The stated reason is required and recorded with it.

Failure directions, each chosen once:

- Unknown/unpermitted verb -> ``VERB_NOT_PERMITTED``, nothing applied. ``resume`` gets its
  own reason code (``RESUME_NEVER_PERMITTED``) because "you asked for the one thing this
  door structurally cannot do" is a different answer from "typo".
- Malformed frame -> ``MALFORMED_REQUEST``. Never guessed at, never partially applied.
- Missing reason -> ``REASON_REQUIRED``. An unattributed halt on a money path is not worth
  the convenience of omitting it.
"""

from __future__ import annotations

import json
import os
import socket
import socketserver
import stat
from pathlib import Path
from typing import Any

from . import control
from .control import ControlStore
from .errors import ControlBlocked, MvpRuntimeError
from .store import LedgerStore

# The actor recorded on every control event this door produces. Deliberately NOT
# `console_cli.LOCAL_ACTOR` — see the module docstring on attributability.
ASSISTANT_ACTOR = "assistant_bridge"

# The whole permission surface of this module, built from control's own constants so a
# rename there cannot silently widen it here. `control.CMD_RESUME` is absent and a test
# asserts it stays absent; this frozenset is the enforcement, not documentation of it.
_ALLOWED: frozenset[str] = frozenset({control.CMD_KILL, control.CMD_PAUSE})

# Default socket location. Lives under the state directory the deployment already mounts,
# in its own subdirectory so the socket's permissions can be set without touching the
# permissions of governance state.
SOCKET_REL = ".runtime_governance_state/bridge/halt.sock"

# The socket is group-readable/writable and nothing else: the assistant's container runs
# under a different uid than this runtime, so the shared group IS the access control. World
# access would make "any process on this host" the authorization, which is wider than the
# host-console precedent this module rests on.
SOCKET_MODE = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP  # 0o660

# One request per connection, and a short deadline: a client that opens the socket and then
# says nothing must not be able to hold the door. Halting is a small synchronous action, so
# a generous-but-bounded window is right.
_REQUEST_TIMEOUT_SECONDS = 10.0

# A halt request is a handful of bytes. Anything larger is not one, and reading it would
# only give a malformed frame a bigger buffer to arrive in.
_MAX_FRAME_BYTES = 8192


def socket_path(root: Path | None = None) -> Path:
    """The socket path, overridable per-deployment via ``MVP_HALT_BRIDGE_SOCKET``."""
    override = os.environ.get("MVP_HALT_BRIDGE_SOCKET", "").strip()
    if override:
        return Path(override)
    from .paths import repo_root

    return (root if root is not None else repo_root()) / SOCKET_REL


def apply_halt(
    request: Any,
    *,
    control_store: ControlStore,
    ledger: LedgerStore,
    now: str | None = None,
) -> dict[str, Any]:
    """Validate one request and apply it, or raise a typed ``ControlBlocked``.

    Pure with respect to the transport — it takes an already-decoded object and touches no
    socket — which is what makes the permission surface testable without a listener.
    """
    if not isinstance(request, dict):
        raise ControlBlocked("MALFORMED_REQUEST", "request must be a JSON object")

    command = request.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ControlBlocked("MALFORMED_REQUEST", "request needs a non-empty 'command'")
    command = command.strip().lower()

    # `resume` is answered before the generic refusal so the reply names the reason rather
    # than reading as an unrecognized verb. It is refused here and it is also absent from
    # `_ALLOWED`; either alone would be enough, and that is the point.
    if command == control.CMD_RESUME:
        raise ControlBlocked(
            "RESUME_NEVER_PERMITTED",
            "this door halts only; resuming requires the operator on an authenticated channel",
        )
    if command not in _ALLOWED:
        raise ControlBlocked(
            "VERB_NOT_PERMITTED",
            f"{command!r} is not permitted here; this door carries {sorted(_ALLOWED)} only",
        )

    reason = request.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ControlBlocked("REASON_REQUIRED", "a halt must state its reason; it is recorded")

    outcome = control.apply_command(
        control_store, command, actor=ASSISTANT_ACTOR, now=now,
        reason=reason.strip(), ledger=ledger,
    )
    return {
        "ok": True,
        "reply": outcome["reply"],
        "mode": outcome["mode"],
        "changed": outcome["changed"],
        "action": outcome["action"],
        "actor": ASSISTANT_ACTOR,
    }


class _Handler(socketserver.BaseRequestHandler):
    """One JSON object in, one JSON object out, connection closed."""

    def handle(self) -> None:
        self.request.settimeout(_REQUEST_TIMEOUT_SECONDS)
        try:
            raw = self._read_frame()
        except (OSError, socket.timeout):
            return  # The peer went away or stalled; there is nobody to answer.

        try:
            try:
                request = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise ControlBlocked("MALFORMED_REQUEST", f"request is not valid JSON: {exc}") from exc
            payload = apply_halt(
                request,
                control_store=self.server.control_store,   # type: ignore[attr-defined]
                ledger=self.server.ledger,                 # type: ignore[attr-defined]
            )
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
            if size > _MAX_FRAME_BYTES:
                raise OSError("frame too large")
            if b"\n" in chunk:
                break
        return b"".join(chunks).split(b"\n", 1)[0]


class HaltBridgeServer(socketserver.ThreadingUnixStreamServer):
    """The listener. Threading so one stalled peer cannot wedge the door."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        path: Path,
        *,
        control_store: ControlStore,
        ledger: LedgerStore,
    ) -> None:
        self.control_store = control_store
        self.ledger = ledger
        path.parent.mkdir(parents=True, exist_ok=True)
        # A stale socket file from an unclean shutdown would make bind() fail. Removing it
        # is safe precisely because a live server holds no lock on the path — if another
        # server is actually running, its own bind already owns the inode and this
        # deployment contract is one bridge per state directory.
        if path.exists() and path.is_socket():
            path.unlink()
        super().__init__(str(path), _Handler)
        os.chmod(path, SOCKET_MODE)
