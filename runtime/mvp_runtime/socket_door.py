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

Four properties of *how* a door stands, all of them shared and therefore all of them here:

- **One door per path, proven rather than assumed.** Binding used to remove whatever socket
  file it found. A unix socket's pathname can be unlinked while its server is still serving,
  so that turned a double start into two live servers — the old one keeping its established
  connections and its bound inode, the new one taking every connection after. Both write the
  same state under the same file locks, so nothing corrupts; what breaks is the operator's
  model of which process answered, at the moment that question matters most. The path is now
  probed before it is removed (``door_is_live``).
- **A bounded number of handlers.** One thread per connection with no ceiling means a client
  in a reconnect loop is a local denial of service against the stop path.
- **Peer identity, when the deployment states it.** File permissions authorize a *group*;
  ``SO_PEERCRED`` authorizes a *process*. See ``resolve_client_uids`` for why the uid check is
  opt-in and the gid one is not.
- **Errors that say what happened without saying where.** The assistant is the less trusted
  end of this socket; an untyped exception's text is the runtime's internals.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import socket
import socketserver
import stat
import struct
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
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

# The socket's directory, set to match: the client must traverse it to reach the socket, and a
# grant on the socket alone is not a grant. Group gets r-x (enough to walk in and open a named
# socket), never w, and world gets nothing.
DIR_MODE = (
    stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP
)  # 0o750

# Which gid the doors hand their sockets to. Unset (the default) keeps the socket's group as
# the door process's own gid — the behaviour before this existed. See `grant_client_access`
# for why an implicit group turned out to be a grant nobody could see.
CLIENT_GID_ENV = "MVP_BRIDGE_CLIENT_GID"

# Which uids may be on the other end, checked against the kernel's answer rather than the
# filesystem's. Unset means unchecked — see `resolve_client_uids` for why that default is the
# honest one here and not a fail-open hole.
CLIENT_UID_ENV = "MVP_BRIDGE_CLIENT_UID"

# One request per connection, and a short deadline: a client that opens the socket and then
# says nothing must not be able to hold a door.
REQUEST_TIMEOUT_SECONDS = 10.0

# How long the pre-bind probe waits for an answer from a socket that already exists. A live
# door on a local unix socket accepts immediately; a full backlog is the only way this elapses,
# and a door with a full backlog is emphatically live.
PROBE_TIMEOUT_SECONDS = 1.0

# A console request is a handful of bytes. Anything larger is not one, and reading it would
# only give a malformed frame a bigger buffer to arrive in.
#
# **This is the default, not the rule.** It was a module constant until the knowledge door
# arrived, whose whole purpose is to carry a document — a frame two orders of magnitude past
# this, by design rather than by malformation. A door states its own ceiling; the three doors
# that carry console verbs state nothing and get this one, unchanged. The distinction the
# ceiling is drawing is "bigger than this door's requests ever are", and that differs per
# door, so it belongs on the door.
MAX_FRAME_BYTES = 8192

# How many requests a door serves at once. Past this, a connection is answered BRIDGE_BUSY and
# closed rather than queued behind a thread nobody capped: the client on the other end is a
# model that retries, and "one thread per connection, unbounded" makes a retry loop into a
# local denial of service against the same host the stop path lives on.
#
# **Why the stop door is not 1.** The obvious reading of "the switch door is the most dangerous,
# so give it the smallest ceiling" gets the risk backwards. `disable` (kill/pause) and `enable`
# share that door, and `enable` does the slow work — approval store reads, a Core binding, a
# fingerprint. A ceiling of 1 lets one in-flight *start* request refuse the *stop* request
# behind it, which is the precise failure `switch-bridge` was split into its own service to
# avoid. The ceiling is a DoS bound, not an authority bound; authority is `_ALLOWED_COMMANDS`
# and the approval, and neither loosens because four requests are in flight instead of one.
DEFAULT_MAX_CONCURRENT_REQUESTS = 4

# How many connections may be *admitted* beyond the number that may run at once.
#
# Without this the bound is wrong in the direction that hurts an honest client. A handler
# releases its slot after the reply is sent and the socket is closed, so a client that reads
# its answer and immediately reconnects can arrive while the previous slot is still held —
# microseconds, but it only has to win that race once. At a ceiling of 1 a strictly
# *sequential* client got BRIDGE_BUSY exactly that way, often enough to fail a test on a
# loaded machine. That is not the traffic a ceiling exists to refuse.
#
# So admission and execution are bounded separately: the pool caps how many run at once, and
# this slack lets a few queue behind it rather than being refused for a slot that is about to
# free. A small fixed number rather than a fraction of the ceiling, because what it absorbs is
# a release lag, and a release lag does not scale with the ceiling.
ADMISSION_SLACK = 4

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


# ── The frame envelope (door API v2) ───────────────────────────────────────────────────────
#
# Three optional keys every door reads the same way, defined here beside the transport so no
# door can grow its own dialect: `proto` names the protocol version a client speaks (absent
# means 1, the pre-v2 frame), `client_id` is attribution metadata — WHICH assistant session,
# cron job or delegate sent this, never WHO is allowed to — and `request_id` stays the
# idempotency key `bridge_idempotency` owns. Identity is the peer uid checked at `connect()`
# and nothing in the frame; a door that used `client_id` to decide anything would be trusting
# the least trusted end of the socket with the one thing the socket exists to establish.
#
# Replies carry the envelope back: `proto` echoed only when the frame named one, `client_id`
# echoed when given, and `data` — the structured view of the reply — always present so a v2
# client never has to probe for the key (`None` when a verb has nothing structured). The text
# `reply` stays exactly as it was: v1 clients read it, and the console renderers own it.
PROTO_KEY = "proto"
CLIENT_ID_KEY = "client_id"
ENVELOPE_KEYS: frozenset[str] = frozenset({PROTO_KEY, CLIENT_ID_KEY})
SUPPORTED_PROTOS: frozenset[int] = frozenset({1, 2})
DEFAULT_PROTO = 1
MAX_CLIENT_ID_LENGTH = 64
_CLIENT_ID = re.compile(r"^[A-Za-z0-9._:-]+$")


def negotiate_proto(request: Any) -> int:
    """The protocol version a frame speaks: ``DEFAULT_PROTO`` when it names none.

    A version this door does not speak is refused by name rather than served as v1: a client
    that asked for 3 believed it would get 3's guarantees, and answering in an older dialect
    would be the quiet mismatch a reader downstream misreads as a wrong answer.
    """
    raw = request.get(PROTO_KEY) if isinstance(request, dict) else None
    if raw is None:
        return DEFAULT_PROTO
    if isinstance(raw, bool) or not isinstance(raw, int) or raw not in SUPPORTED_PROTOS:
        raise ControlBlocked(
            "PROTO_UNSUPPORTED",
            f"{PROTO_KEY!r} must be one of {sorted(SUPPORTED_PROTOS)}; got {raw!r}",
        )
    return raw


def client_id_of(request: Any) -> str | None:
    """The frame's ``client_id``, validated as a name, or ``None`` when it carries none."""
    raw = request.get(CLIENT_ID_KEY) if isinstance(request, dict) else None
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ControlBlocked(
            "MALFORMED_REQUEST", f"{CLIENT_ID_KEY!r} must be a non-empty string when given",
        )
    value = raw.strip()
    if len(value) > MAX_CLIENT_ID_LENGTH or not _CLIENT_ID.match(value):
        raise ControlBlocked(
            "MALFORMED_REQUEST",
            f"{CLIENT_ID_KEY!r} is a name: at most {MAX_CLIENT_ID_LENGTH} characters of "
            f"letters, digits, '.', '_', ':' or '-'",
        )
    return value


def validate_envelope(request: Any) -> tuple[int, str | None]:
    """Validate the envelope keys up front, before a door applies anything.

    Called by every door right after its own key check, so a frame that names a version the
    door cannot honour, or a malformed client name, is refused before any effect and before
    any idempotency claim — the same rule the doors apply to every other malformed field.
    """
    return negotiate_proto(request), client_id_of(request)


def _echo_envelope(reply: dict[str, Any], request: Any, *, strict: bool) -> None:
    if not isinstance(request, dict):
        return
    try:
        proto = negotiate_proto(request)
        client_id = client_id_of(request)
    except ControlBlocked:
        if strict:
            raise
        return
    if proto != DEFAULT_PROTO:
        reply[PROTO_KEY] = proto
    if client_id is not None:
        reply[CLIENT_ID_KEY] = client_id


def envelope(reply: dict[str, Any], *, request: Any, data: Any = None) -> dict[str, Any]:
    """Stamp a door's reply with the frame's envelope and its structured ``data`` view."""
    _echo_envelope(reply, request, strict=True)
    reply["data"] = data
    return reply


def refusal_payload(exc: MvpRuntimeError, request: Any = None) -> dict[str, Any]:
    """The reply for a typed refusal raised inside ``apply``.

    A refusal is still an answer to a frame, so it carries the envelope back when the frame's
    envelope was well-formed — a v2 client can tell which of its requests was refused. A frame
    whose envelope was itself the problem gets the bare refusal: nothing is echoed that could
    not be read.
    """
    payload: dict[str, Any] = {"ok": False, "reason_code": exc.reason_code, "reason": str(exc)}
    detail = getattr(exc, "data", None)
    if isinstance(detail, dict):
        payload["data"] = detail
    _echo_envelope(payload, request, strict=False)
    if isinstance(request, dict):
        request_id = request.get("request_id")
        if isinstance(request_id, str) and request_id.strip():
            payload["request_id"] = request_id.strip()
    return payload


def plain(outcome: Any) -> dict[str, Any]:
    """A console outcome minus its rendered text, kept to JSON-safe values — the ``data`` a
    read-style verb can offer without a renderer of its own."""
    if not isinstance(outcome, dict):
        return {}
    return {
        key: value for key, value in outcome.items()
        if key != "reply" and isinstance(value, (str, int, float, bool, list, dict, type(None)))
    }


# Numbers one internal failure from the next within a process, so the redacted reply and the
# logged traceback can be tied together by an operator holding only the reply. Deliberately not
# `integrity.short_id`: that is a *deterministic* id over a seed, and two different crashes with
# the same seed must not share a reference.
_ERROR_SEQUENCE = itertools.count(1)


def _next_error_ref() -> str:
    return f"{os.getpid()}-{next(_ERROR_SEQUENCE):04d}"


def internal_error_payload(exc: BaseException, door: str) -> dict[str, Any]:
    """The reply for an exception no door anticipated: what failed, never where.

    ``MvpRuntimeError`` messages are authored, operator-facing text and go back verbatim — that
    is what a typed refusal is for. Everything else is the runtime describing its own internals
    to the least trusted end of the socket: file paths under the state directory, store layout,
    a dependency's own error text. None of that helps the assistant and all of it helps someone
    who has talked the assistant into probing.

    The detail is not lost, it is moved. The traceback goes to stderr, which is the service log
    (`docker logs thomas-<door>-bridge`), and both ends carry the same ``ref`` so the reply the
    assistant relays and the traceback an operator reads are one incident rather than two.
    """
    ref = _next_error_ref()
    sys.stderr.write(
        f"BRIDGE_ERROR[{door}] ref={ref} {type(exc).__name__}: {exc}\n"
        + "".join(traceback.format_exception(exc))
    )
    sys.stderr.flush()
    return {
        "ok": False,
        "reason_code": "BRIDGE_ERROR",
        "reason": (
            "the door could not complete this request; the failure is recorded in this "
            f"service's log as ref {ref}"
        ),
        "error_ref": ref,
    }


class _Handler(socketserver.BaseRequestHandler):
    """One JSON object in, one JSON object out, connection closed."""

    def handle(self) -> None:
        self.request.settimeout(
            getattr(self.server, "request_timeout_seconds", REQUEST_TIMEOUT_SECONDS)
        )

        try:
            raw = self._read_frame()
        except (OSError, socket.timeout):
            return  # The peer went away or stalled; there is nobody to answer.

        # Who is on the other end — after the frame is drained, before a byte of it is
        # interpreted. Checking before the read looks stricter and is worse: replying while
        # the peer is still writing closes a socket with data pending, which reaches the
        # client as a reset rather than an answer, so the door cannot actually TELL an
        # unpermitted peer that it was unpermitted. Nothing is conceded by reading first —
        # the bytes are bounded by MAX_FRAME_BYTES, they are never decoded here, and
        # authorization still precedes every interpretation of what the peer sent.
        try:
            self.server.authorize_peer(self.request)             # type: ignore[attr-defined]
        except MvpRuntimeError as exc:
            self._reply({"ok": False, "reason_code": exc.reason_code, "reason": str(exc)})
            return

        decoded: Any = None
        try:
            decoded = decode_request(raw)
            payload = self.server.apply(decoded)                # type: ignore[attr-defined]
        except MvpRuntimeError as exc:
            payload = refusal_payload(exc, decoded)
        except Exception as exc:  # noqa: BLE001 — a door must answer, then keep standing
            payload = internal_error_payload(exc, self.server.door_label)  # type: ignore[attr-defined]

        self._reply(payload)

    def _reply(self, payload: dict[str, Any]) -> None:
        try:
            self.request.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            return

    def _read_frame(self) -> bytes:
        ceiling = getattr(self.server, "max_frame_bytes", MAX_FRAME_BYTES)
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = self.request.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > ceiling:
                raise OSError("frame too large")
            if b"\n" in chunk:
                break
        return b"".join(chunks).split(b"\n", 1)[0]


class SocketDoor(_UNIX_STREAM_SERVER or object):  # type: ignore[misc]
    """The listener. Threading so one stalled peer cannot wedge a door, bounded so a peer in a
    reconnect loop cannot wedge the host, and single per path so a restart cannot fork it."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        path: Path,
        apply: ApplyFn,
        *,
        max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_REQUESTS,
        max_frame_bytes: int = MAX_FRAME_BYTES,
        request_timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        if not UNIX_SOCKETS_AVAILABLE:
            raise ControlBlocked(
                "UNIX_SOCKETS_UNAVAILABLE",
                "this platform has no AF_UNIX; the bridge doors listen on a unix socket only",
            )
        if max_concurrent_requests < 1:
            raise ControlBlocked(
                "BRIDGE_CONCURRENCY_INVALID",
                f"a door must serve at least one request at a time; got "
                f"{max_concurrent_requests}",
            )
        if max_frame_bytes < 1 or request_timeout_seconds <= 0:
            raise ControlBlocked(
                "BRIDGE_LIMITS_INVALID",
                f"a door needs a positive frame ceiling and read deadline; got "
                f"{max_frame_bytes} bytes / {request_timeout_seconds}s",
            )
        self.apply = apply
        self.door_label = path.stem
        self.max_concurrent_requests = max_concurrent_requests
        # Read by `_Handler`, which sees the door only through `self.server`. Per-door rather
        # than module-wide so a door carrying documents cannot widen the ceiling for a door
        # carrying console verbs.
        self.max_frame_bytes = max_frame_bytes
        self.request_timeout_seconds = request_timeout_seconds
        self.allowed_client_uids = resolve_client_uids()
        client_gid = resolve_client_gid()
        path.parent.mkdir(parents=True, exist_ok=True)
        # A socket file already at this path is one of two things, and the difference decides
        # everything. This used to remove it either way, on the reasoning that "a live server
        # holds no lock on the path" — which is true, and is exactly why removing it was unsafe.
        # Unlinking a pathname does not close the server bound to it: the old process keeps its
        # inode and its established connections and goes on serving them, while every new
        # connection reaches the process that bound second. Two doors, one contract, and an
        # operator with no way to tell which one answered — during, typically, a restart being
        # performed because something was already wrong. So the path is asked first.
        if path.exists() and path.is_socket():
            if door_is_live(path):
                raise ControlBlocked(
                    "BRIDGE_ALREADY_RUNNING",
                    f"a door is already listening on {path}; refusing to bind a second one "
                    f"onto the same path. Stop the running service first",
                )
            path.unlink()
        super().__init__(str(path), _Handler)
        os.chmod(path, SOCKET_MODE)
        grant_client_access(path, client_gid)
        # After the bind, so a door that refused to listen never leaves an executor behind.
        # Nothing can arrive before `serve_forever`, so there is no window here — the socket is
        # listening but this process is not yet accepting.
        #
        # Two bounds, deliberately different sizes (see ADMISSION_SLACK). The pool is the other
        # half of the ceiling: a fixed set of reused threads rather than a new one per
        # connection, so the thread count is capped whether or not anything is ever refused.
        self._slots = threading.BoundedSemaphore(max_concurrent_requests + ADMISSION_SLACK)
        self._pool = ThreadPoolExecutor(
            max_workers=max_concurrent_requests, thread_name_prefix=f"door-{self.door_label}",
        )

    def authorize_peer(self, request: Any) -> None:
        """Refuse a peer the deployment did not name, on the kernel's answer about who it is.

        Unset ``MVP_BRIDGE_CLIENT_UID`` means no uid check, and that default is a decision
        rather than an omission. The socket's mode and its directory's mode already narrow
        reachability to one gid — the same authentication the local console rests on — and a
        uid allowlist nobody configured would refuse the operator's own shell before it refused
        anything else. What it adds when it IS set is the distinction file permissions cannot
        draw: the assistant's container and every other process that happens to share that
        group are identical to ``chmod`` and different to ``SO_PEERCRED``.

        Configured-but-unanswerable fails closed. A check the deployment asked for and this
        platform cannot perform is not a check that passes.
        """
        if not self.allowed_client_uids:
            return
        creds = peer_credentials(request)
        if creds is None:
            raise ControlBlocked(
                "PEER_CREDENTIALS_UNAVAILABLE",
                f"{CLIENT_UID_ENV} names which uids may reach this door and this platform "
                f"cannot report the peer's uid; refusing rather than serving unchecked",
            )
        if creds[1] not in self.allowed_client_uids:
            # The allowlist itself is not quoted back: the peer already knows its own uid, and
            # a refusal is not an occasion to describe the configuration to whoever tripped it.
            raise ControlBlocked(
                "PEER_NOT_PERMITTED", f"uid {creds[1]} is not permitted at this door",
            )

    def process_request(self, request: Any, client_address: Any) -> None:
        """Admit what the door can hold; answer the rest and close.

        Never blocks. The accept loop deciding to wait — even briefly — for a slot would make a
        flood of excess connections stall the legitimate ones behind them, which is the outage
        the ceiling is supposed to prevent rather than cause. Past admission the connection is
        refused immediately with an ordinary typed envelope, so a client that retries sensibly
        can see what it hit instead of reading a closed socket as the door being down.
        """
        if not self._slots.acquire(blocking=False):
            self._refuse_busy(request)
            self.shutdown_request(request)
            return
        try:
            self._pool.submit(self._serve, request, client_address)
        except BaseException:
            # Nothing was queued, so nothing else will give the slot back.
            self._slots.release()
            raise

    def _serve(self, request: Any, client_address: Any) -> None:
        """One admitted request, on a pool thread. Mirrors ``ThreadingMixIn``'s own body."""
        try:
            self.finish_request(request, client_address)
        except Exception:                       # noqa: BLE001 — a worker outlives its requests
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            self._slots.release()

    def server_close(self) -> None:
        super().server_close()
        # `getattr`, because `TCPServer.__init__` calls `server_close()` itself when the bind
        # fails — before the pool exists. An AttributeError here would replace the real bind
        # error with a misleading one, at the moment someone is trying to read it.
        pool = getattr(self, "_pool", None)
        if pool is not None:
            # `wait=False`: a stalled handler must not hold a shutdown open, and every request
            # already carries its own deadline (REQUEST_TIMEOUT_SECONDS).
            pool.shutdown(wait=False)

    def _refuse_busy(self, request: Any) -> None:
        payload = {
            "ok": False,
            "reason_code": "BRIDGE_BUSY",
            "reason": (
                f"this door is serving {self.max_concurrent_requests} requests with "
                f"{ADMISSION_SLACK} more admitted; nothing was applied, retry shortly"
            ),
        }
        try:
            request.settimeout(REQUEST_TIMEOUT_SECONDS)
            request.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            return


def resolve_client_gid() -> int | None:
    """The gid the doors grant access to, from ``MVP_BRIDGE_CLIENT_GID``. ``None`` when unset.

    Unset keeps the pre-existing behaviour exactly: the socket's group is whatever gid the door
    process runs as, and reaching it is the client's problem.
    """
    raw = os.environ.get(CLIENT_GID_ENV, "").strip()
    if not raw:
        return None
    try:
        gid = int(raw)
    except ValueError:
        raise ControlBlocked(
            "BRIDGE_CLIENT_GID_INVALID",
            f"{CLIENT_GID_ENV}={raw!r} is not a gid; a door will not guess who may reach it",
        ) from None
    if gid < 0:
        raise ControlBlocked(
            "BRIDGE_CLIENT_GID_INVALID", f"{CLIENT_GID_ENV}={gid} is not a valid gid",
        )
    return gid


def resolve_client_uids() -> frozenset[int]:
    """The uids permitted at a door, from ``MVP_BRIDGE_CLIENT_UID``. Empty when unset.

    Comma-separated, like ``MVP_HOSTED_PROVIDER``, because the operator's own shell and the
    assistant's container are two legitimate peers and naming one must not exclude the other.
    A malformed entry raises rather than being dropped: an allowlist that silently lost a
    member is the shape of grant this door's gid handling already got wrong once, where the
    configuration said one thing and the running system did another with nothing saying so.
    """
    raw = os.environ.get(CLIENT_UID_ENV, "").strip()
    if not raw:
        return frozenset()
    uids: set[int] = set()
    for token in raw.split(","):
        entry = token.strip()
        if not entry:
            continue
        try:
            uid = int(entry)
        except ValueError:
            raise ControlBlocked(
                "BRIDGE_CLIENT_UID_INVALID",
                f"{CLIENT_UID_ENV} carries {entry!r}, which is not a uid; a door will not "
                f"guess who may reach it",
            ) from None
        if uid < 0:
            raise ControlBlocked(
                "BRIDGE_CLIENT_UID_INVALID", f"{CLIENT_UID_ENV} carries {uid}, not a valid uid",
            )
        uids.add(uid)
    if not uids:
        raise ControlBlocked(
            "BRIDGE_CLIENT_UID_INVALID",
            f"{CLIENT_UID_ENV} is set but names no uid; unset it to serve the gid alone, or "
            f"name the uids — an empty allowlist is not a stated intent",
        )
    return frozenset(uids)


def peer_credentials(sock: Any) -> tuple[int, int, int] | None:
    """``(pid, uid, gid)`` of the process at the other end, or ``None`` where unknowable.

    ``SO_PEERCRED`` is what the kernel recorded at ``connect()`` time, so unlike anything in
    the frame it cannot be chosen by the caller. ``None`` means this platform does not offer
    it — never "nobody is there"; the distinction is the caller's to fail closed on.

    Note what the gid here is and is not: the peer's *effective* gid, not its supplementary
    groups. The assistant reaches these sockets through a supplementary group, so this value
    will not match ``MVP_BRIDGE_CLIENT_GID`` on a correctly configured deployment and must
    never be compared against it. The socket's own mode is what enforces the group.
    """
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        return None
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
    except OSError:
        return None
    pid, uid, gid = struct.unpack("3i", raw)
    return pid, uid, gid


def door_is_live(path: Path) -> bool:
    """Is something serving on ``path`` right now? Uncertainty answers yes.

    The only way to tell a live socket from the remains of one is to try it: a stale pathname
    refuses the connection (nothing is listening on that inode), a live door accepts it. The
    probe sends nothing and closes immediately, so the door on the other side sees a peer that
    hung up before framing anything — a case its handler already returns from.

    Every other error answers **yes**, which is the fail-closed direction here: the cost of
    wrongly believing a dead door is alive is a refused start that names the path an operator
    can then clear by hand; the cost of wrongly believing a live door is dead is two of them.

    **What this does not cover, considered and left.** Two doors starting in the same instant
    can both probe a dead path, both unlink, and both bind — the check and the bind are not
    one operation. Closing that needs an exclusive lock held for the process lifetime rather
    than a probe, which is a real option (``flock`` is released by the kernel when a process
    dies, so it leaves no stale state) and buys nothing against the failure actually seen: a
    restart racing a shutdown, which is sequential, and a manual start beside a running
    service, which is sequential too. A lock would also not have protected the deploy that
    introduced it, since the doors already running would not have been holding one.
    """
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(PROBE_TIMEOUT_SECONDS)
        probe.connect(str(path))
    except (ConnectionRefusedError, FileNotFoundError):
        return False
    except OSError:
        return True
    finally:
        probe.close()
    return True


def grant_client_access(path: Path, client_gid: int | None) -> None:
    """Hand the socket and its directory to ``client_gid``, or refuse to serve.

    **Why the door sets this and not the client.** The socket's group IS the access control
    (see ``SOCKET_MODE``), and until now that group was simply whatever gid the door happened
    to run as — so who could reach a door was decided by the *client's* container config
    rather than by the door. That failed exactly the way an implicit grant fails: the
    assistant's container carried ``group_add: ["10001"]``, its supervisor dropped privileges
    with ``s6-setuidgid``, supplementary groups were recomputed from an ``/etc/group`` with no
    such gid, and every MCP subprocess ran without it. The doors were up, the sockets were
    there, and nothing on either side said why the assistant could not reach them. Naming the
    gid here makes the grant the door's own statement.

    **The directory too.** A socket the client may open is unreachable inside a directory the
    client may not traverse, which is the half that actually bit. Both are set, both stay
    group-only — never world — so the grant widens to exactly one gid.

    Fail-closed: a configured gid that cannot be applied raises rather than serving. A door
    that comes up unreachable is the failure this whole function exists to prevent, and it is
    invisible from both ends; a refusal at the moment of listening is not.
    """
    if client_gid is None:
        return
    try:
        os.chown(path, -1, client_gid)
        os.chown(path.parent, -1, client_gid)
        os.chmod(path.parent, DIR_MODE)
    except OSError as exc:
        raise ControlBlocked(
            "BRIDGE_CLIENT_GID_UNAVAILABLE",
            f"cannot hand {path} to gid {client_gid} ({type(exc).__name__}: {exc}). The door "
            f"process must own the socket and belong to that group — add it to the service's "
            f"`group_add`. Refusing to listen rather than serving a door the client cannot reach",
        ) from exc


def describe_admission(server: Any) -> str:
    """What this door will admit, for its startup line.

    Both facts belong on stderr at listen time for the reason ``grant_client_access`` exists:
    an access rule nobody can see is the rule that turns out to be different from the one the
    deployment thought it configured, and the last time that happened the doors were up,
    unreachable, and silent about it from both ends. A ceiling and a uid allowlist are exactly
    the kind of setting that is wrong for weeks without a symptom.
    """
    peers = getattr(server, "allowed_client_uids", frozenset())
    return (
        f"concurrency={getattr(server, 'max_concurrent_requests', '?')}"
        f"+{ADMISSION_SLACK} queued, "
        f"uids={sorted(peers) if peers else 'unrestricted (group only)'}"
    )


def resolve_socket_path(env_var: str, relative: str, root: Path | None = None) -> Path:
    """A door's socket path: ``env_var`` when set, else ``relative`` under the repo root."""
    override = os.environ.get(env_var, "").strip()
    if override:
        return Path(override)
    from .paths import repo_root

    return (root if root is not None else repo_root()) / relative


# How large a reply the client half will read. A dispatch reply carries a rendered analysis,
# which is orders of magnitude past a console frame; the ceiling exists against a runaway
# peer, not as a contract, and a reply that hits it is treated as malformed, never truncated —
# a truncated JSON object silently dropping fields is the quiet mismatch these doors refuse
# everywhere else.
MAX_REPLY_BYTES = 1 << 20


def call_door(
    path: Path,
    request: dict[str, Any],
    *,
    deadline_seconds: float,
    max_reply_bytes: int = MAX_REPLY_BYTES,
) -> Any:
    """The client half of a door's contract: one frame out, one reply in, connection closed.

    Written for door-to-engine forwarding (the dispatch door hands validated work to
    ``pipeline_worker``); the assistant-side shims speak the same protocol from outside this
    repo. Two failure kinds, kept apart on purpose:

    - **Transport** — nothing listening, connect/read failed, deadline elapsed, or what
      answered was not a door speaking JSON. These raise (``DOOR_UNREACHABLE`` /
      ``DOOR_REPLY_MALFORMED``): the caller got no answer to act on.
    - **Refusal** — the far door answered with a typed error envelope. That is a *reply*, and
      it is returned like any other: whether to relay, translate, or retry a refusal is the
      caller's decision, not the transport's.

    The deadline is per socket operation rather than a wall clock over the whole exchange,
    matching the server side; the slow part of a forwarded dispatch is the far door's apply,
    during which this end sits in a single ``recv``.
    """
    if not UNIX_SOCKETS_AVAILABLE or not hasattr(socket, "AF_UNIX"):
        raise ControlBlocked(
            "UNIX_SOCKETS_UNAVAILABLE",
            "this platform has no AF_UNIX; the bridge doors speak over a unix socket only",
        )
    frame = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(deadline_seconds)
        client.connect(str(path))
        client.sendall(frame)
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > max_reply_bytes:
                raise ControlBlocked(
                    "DOOR_REPLY_MALFORMED",
                    f"the reply from {path} exceeded {max_reply_bytes} bytes; refusing to "
                    f"parse a truncation as an answer",
                )
            if b"\n" in chunk:
                break
    except OSError as exc:
        raise ControlBlocked(
            "DOOR_UNREACHABLE",
            f"no door answered at {path} ({type(exc).__name__}: {exc})",
        ) from exc
    finally:
        client.close()
    raw = b"".join(chunks).split(b"\n", 1)[0]
    if not raw:
        raise ControlBlocked(
            "DOOR_UNREACHABLE", f"the door at {path} closed without answering",
        )
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ControlBlocked(
            "DOOR_REPLY_MALFORMED", f"the reply from {path} is not JSON: {exc}",
        ) from exc
