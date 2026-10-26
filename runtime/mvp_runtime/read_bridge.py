"""The read-only door — the assistant asks what this runtime is doing, and gets the answer
the console would have given.

Thomas can already hand the assistant's container a read-only mount of the state directory,
and that is genuinely faster to set up than this file. It buys two things this does not:
the assistant reads *raw* JSONL and narrates it, so a number it reports can be a
misreading; and the mount carries the whole tree, including ``approvals/`` and
``safety_flag_*``, which have nothing to do with "how is trading going".

So this door renders instead of exposing. Every verb dispatches to the **same applier the
Telegram channel uses** — ``domain_console``, ``registry_console``, ``memory_console``,
``control`` — and returns that applier's own text. The assistant is then narrating a
rendered board rather than a file, and its answer cannot disagree with ``/crypto status``,
because it *is* ``/crypto status``. That is the front desk's rule (*"Deterministic data
beats model narration"*) applied one layer out.

**Why a separate door from ``switch_bridge``.** Different authority. Stopping and starting
ride ``control_channel.local_operator_console.emergency_controls_allowed``; these reads ride
``permission_model`` INTERNAL_READ (ALLOW) plus ``kill_switch.kill_allows:
read_only_status`` — which is also why they keep answering while the runtime is KILLED, the
moment a board is most worth reading. Folding reads into the switch door would also destroy
the one property that door is for: a small verb set that cannot be widened.

**Nothing here can mutate.** ``registry_console`` also implements ``CANCEL`` and
``memory_console`` also implements ``PROMOTE``; both are absent from ``_READS`` — not
filtered out, never named. There is no request this module can build that produces them,
and a test asserts the table stays that way. No ledger event is written, no control state
is touched, and ``runtime_status`` is dispatched with ``ledger=None`` so that is true by
construction rather than by the applier's good behaviour.
"""

from __future__ import annotations

import json
import os
import re
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from . import control, domain_console, memory_console, registry_console, socket_door, store_reads, timeutil
from .approval_store import ApprovalStore
from .control import ControlStore
from .errors import ControlBlocked
from .filelock import ACQUIRE_DEADLINE_SECONDS, ACQUIRE_POLL_SECONDS

try:  # POSIX only; where it is missing the request log refuses rather than writing unlocked
    import fcntl
except ImportError:  # pragma: no cover - Windows, where no profiled door can listen anyway
    fcntl = None  # type: ignore[assignment]
from .scheduler import ScheduleStore
from .store import LedgerStore
from .task_registry import TaskRegistryStore
from .working_memory import WorkingMemoryStore

# The identity these reads are attributed to. Reads record nothing, so this only reaches the
# appliers' `operator_id` parameter — but it is the assistant's name, not the operator's,
# for the same reason the halt door has its own actor.
from .socket_door import ASSISTANT_ACTOR

SOCKET_REL = ".runtime_governance_state/bridge/read.sock"
SOCKET_ENV = "MVP_READ_BRIDGE_SOCKET"

# Families, so the dispatch below reads as a table rather than a chain of ifs.
_CONTROL, _DOMAIN, _REGISTRY, _MEMORY = "control", "domain", "registry", "memory"
# The fourth family (door API v2): stores the console never rendered — schedules, scheduler
# events, heartbeats, one approval's standing. Reads only; `store_reads` says what each is.
_STORES = "stores"

# THE permission surface. Read it as the answer to "what can the assistant see": every entry
# is a read, and the two mutating verbs their consoles also implement (registry CANCEL,
# memory PROMOTE) are not here and never will be — a test asserts it.
_READS: dict[str, tuple[str, Any]] = {
    "runtime_status":   (_CONTROL, control.CMD_STATUS),
    "crypto_status":    (_DOMAIN, ("CRYPTO", "status")),
    "crypto_readiness": (_DOMAIN, ("CRYPTO", "readiness")),
    "crypto_paper":     (_DOMAIN, ("CRYPTO", "paper")),
    # The third of Thomas's original three (position, funds, return). It reads a snapshot the
    # scheduler wrote, never the venue — this door holds no venue credential and adding one
    # would put an order-capable key in the process the assistant talks to.
    "crypto_funds":     (_DOMAIN, ("CRYPTO", "funds")),
    "tasks":            (_REGISTRY, "TASKS"),
    "history":          (_REGISTRY, "HISTORY"),
    "result":           (_REGISTRY, "RESULT"),
    "memory":           (_MEMORY, "LIST"),
    # Door API v2. Rows and events, never verbs that change them: `enable`/`disable`/`remove`
    # stay in the scheduler CLI, and this door's rule ("no mutating verb is ever named here")
    # covers these four exactly as it covers the nine above.
    "schedules":        (_STORES, store_reads.SCHEDULES),
    "scheduler_events": (_STORES, store_reads.SCHEDULER_EVENTS),
    "heartbeat":        (_STORES, store_reads.HEARTBEAT),
    "approval_status":  (_STORES, store_reads.APPROVAL_STATUS),
    # Review D8 (Thomas 2026-09-26: "주간 다이제스트는 Hermes가 돌린다"). Counts per lane over the run
    # ledger — never a record — so the assistant can hand Thomas the weekly lane evidence. Dormant:
    # refused by name until the committed policy lists it (POLICY_GATED_READS below).
    "lane_digest":      (_STORES, store_reads.LANE_DIGEST),
    # P1-b of MULTI_ASSET_EXPANSION_V0.1.md (2026-10-02): the broker account's aggregate, from the
    # snapshot scheduler-maint wrote — never a broker call, never a symbol. Dormant like lane_digest:
    # refused by name until the committed policy lists it.
    "holdings_status":  (_DOMAIN, ("HOLDINGS", "status")),
}

# The reads this door carries dormant, the switch door's pattern (`switch_bridge.POLICY_GATED_COMMANDS`):
# each refuses as CONTROL_VERB_NOT_GRANTED until the committed policy lists it under
# `control_channel.assistant_read.verbs` (`control.granted_read_verbs`), read at the call. The other
# reads never consult the policy, so a policy that cannot be read can never blind the assistant to a
# board. The code is the same before and after the grant; the policy line is the switch
# (`docs/runtime-contracts/POLICY_1_6_1_DRAFT.md`).
POLICY_GATED_READS: frozenset[str] = frozenset({"lane_digest", "holdings_status"})

# Why each read is permitted — the same inventory the operator channel keeps
# (`operator.CHANNEL_VERB_AUTHORITY`), so a verb cannot join `_READS` without naming the clause
# that admits it, and the policy's `assistant_read` block (1.5.0) has something to be checked
# against. A test pins the two key sets equal in both directions.
_INTERNAL_READ = "policy:permission_model INTERNAL_READ (ALLOW) + kill_switch.kill_allows read_only_status"
_AUDIT_READ = "policy:permission_model INTERNAL_READ (ALLOW) + kill_switch.kill_allows audit_read"
READ_VERB_AUTHORITY: dict[str, str] = {
    "runtime_status": "policy:kill_switch.kill_allows read_only_status",
    "crypto_status": _INTERNAL_READ,
    "crypto_readiness": _INTERNAL_READ,
    "crypto_paper": _INTERNAL_READ,
    "crypto_funds": _INTERNAL_READ + " (snapshot the scheduler wrote; no venue key here)",
    "tasks": _INTERNAL_READ,
    "history": _INTERNAL_READ,
    "result": _INTERNAL_READ + " (ledger re-render; never a raw record)",
    "memory": _INTERNAL_READ + " (candidates list; PROMOTE is not reachable)",
    "schedules": _INTERNAL_READ + " (rows only; mutation stays in scheduler_cli)",
    "scheduler_events": _AUDIT_READ,
    "heartbeat": "policy:kill_switch.kill_allows read_only_status",
    "approval_status": _AUDIT_READ + " (summary only; approvals/ records never exposed)",
    "lane_digest": _AUDIT_READ + " (per-lane counts over the run ledger; dormant until policy 1.6.1)",
    "holdings_status": _INTERNAL_READ + " (aggregate snapshot scheduler-maint wrote; no broker key here; dormant until the policy lists it)",
}

# The reads that mean anything with an argument (a count, an id, a window in days). Everything
# else refuses one rather than accepting it silently — a caller that passed an argument believed
# it would be used, and answering as though it had not been passed is the kind of quiet mismatch
# that later reads as a wrong answer.
_TAKES_ARGUMENT = frozenset({"history", "result", "scheduler_events", "approval_status", "lane_digest"})


def socket_path(root: Path | None = None) -> Path:
    """The socket path, overridable per-deployment via ``MVP_READ_BRIDGE_SOCKET``."""
    return socket_door.resolve_socket_path(SOCKET_ENV, SOCKET_REL, root)


def apply_read(
    request: Any,
    *,
    control_store: ControlStore,
    ledger: LedgerStore,
    registry: TaskRegistryStore | None = None,
    working_memory: WorkingMemoryStore | None = None,
    now: str | None = None,
    repo_root: Path | None = None,
    schedules: ScheduleStore | None = None,
    approval_store: ApprovalStore | None = None,
    profile: DoorProfile | None = None,
) -> dict[str, Any]:
    """Validate one request and render it, or raise a typed error.

    ``profile`` is one of ``PROFILES``, never assembled by the caller: it narrows THIS door to the
    profile's reads and answers ``schedules``/``scheduler_events`` with its closed reply contract
    (why: ``docs/history/2026-10-16-claude-read-door-profile.md``). ``None`` is the assistant's door, unchanged.

    Pure with respect to the transport — an already-decoded object in, a reply out — which
    is what makes the permission surface testable without a listener.
    """
    if profile is not None:
        profile = _registered(profile)
    if not isinstance(request, dict):
        raise ControlBlocked("MALFORMED_REQUEST", "request must be a JSON object")
    # The envelope first: a version this door does not speak is refused before any verb is
    # interpreted. This door ignores keys it does not read, so `proto`/`client_id` were already
    # tolerated here — validating them is what turns tolerance into a contract.
    socket_door.validate_envelope(request)

    command = request.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ControlBlocked("MALFORMED_REQUEST", "request needs a non-empty 'command'")
    command = command.strip().lower()

    # A profiled door answers an unknown name and a read outside its profile the same way, naming
    # only what it serves: the rest of the table is not this reader's to learn (review R-05).
    served = profile.reads if profile is not None else _READS
    if command not in served:
        raise ControlBlocked(
            "VERB_NOT_PERMITTED",
            f"{command!r} is not a read this door serves; it carries {sorted(served)}",
        )
    projection = profile.projection if profile is not None else None
    if command in POLICY_GATED_READS and command not in control.granted_read_verbs(repo_root):
        raise ControlBlocked(
            control.VERB_NOT_GRANTED,
            f"{command} is not granted by the committed Governance Policy yet "
            "(control_channel.assistant_read.verbs); nothing was read",
        )

    argument = request.get("argument")
    if argument is not None and not isinstance(argument, str):
        raise ControlBlocked("MALFORMED_REQUEST", "'argument' must be a string when given")
    if argument is not None and command not in _TAKES_ARGUMENT:
        raise ControlBlocked(
            "ARGUMENT_NOT_ACCEPTED",
            f"{command!r} takes no argument; only {sorted(_TAKES_ARGUMENT)} do",
        )
    argument = argument.strip() if isinstance(argument, str) and argument.strip() else None

    family, spec = _READS[command]

    if command == "holdings_status" and projection is not None:
        # Under a reply contract the stored snapshot is projected, never rendered: freshness and the H6 check
        # states only (`store_reads.read_holdings_status`, HOLDINGS_STATUS_READ_AND_AUTO_FLOWS R1).
        outcome = store_reads.read_holdings_status(now=now or timeutil.utc_now_iso(), repo_root=repo_root,
                                                   projection=projection)
    elif family == _CONTROL and projection is not None and spec == control.CMD_STATUS:
        # Under a reply contract the typed state is read, never the rendered `/status` text
        # (`store_reads.read_runtime_status`). No profile serves this read today.
        outcome = store_reads.read_runtime_status(control_store, now=now or timeutil.utc_now_iso(),
                                                  projection=projection)
    elif family == _CONTROL:
        # ledger=None on purpose: `status` writes no event, and passing no ledger makes that
        # a property of this call rather than a promise the applier keeps.
        outcome = control.apply_command(
            control_store, spec, actor=ASSISTANT_ACTOR, now=now, ledger=None,
        )
    elif family == _DOMAIN:
        outcome = domain_console.apply_domain_command(
            spec, operator_id=ASSISTANT_ACTOR, now=now, repo_root=repo_root,
        )
    elif family == _REGISTRY:
        outcome = registry_console.apply_registry_command(
            (spec, argument), operator_id=ASSISTANT_ACTOR, registry=registry,
            ledger=ledger, control_store=control_store, now=now, repo_root=repo_root,
        )
    elif family == _STORES:
        stamp = now or timeutil.utc_now_iso()
        if spec == store_reads.SCHEDULES:
            outcome = store_reads.read_schedules(schedules, now=stamp, projection=projection)
        elif spec == store_reads.SCHEDULER_EVENTS:
            outcome = store_reads.read_scheduler_events(ledger, argument, now=stamp, projection=projection)
        elif spec == store_reads.HEARTBEAT:
            outcome = store_reads.read_heartbeat(now=stamp, repo_root=repo_root, projection=projection)
        elif spec == store_reads.LANE_DIGEST:
            outcome = store_reads.read_lane_digest(ledger, argument, now=stamp)
        else:
            outcome = store_reads.read_approval_status(approval_store, argument, now=stamp)
    else:
        outcome = memory_console.apply_memory_command(
            (spec, argument, None), operator_id=ASSISTANT_ACTOR,
            working_memory=working_memory, ledger=ledger, control_store=control_store,
            now=now, repo_root=repo_root,
        )

    # `data` is the applier's outcome minus its rendered text — the structured view a v2 client
    # reads instead of parsing the console reply. The reply itself is unchanged.
    return socket_door.envelope(
        {
            "ok": True,
            "command": command,
            "reply": outcome["reply"],
            "action": outcome.get("action"),
        },
        request=request,
        data=outcome["data"] if isinstance(outcome.get("data"), dict) else socket_door.plain(outcome),
    )


# The widest ceiling of the three doors, because these are the cheapest requests: a read
# renders from stores already on disk and returns. It is a ceiling all the same — an assistant
# polling the board in a loop is the likeliest source of connection pressure precisely because
# nothing about a read discourages it.
MAX_CONCURRENT_REQUESTS = 8



# --- door profiles (review R-01; why: docs/history/2026-10-16-claude-read-door-profile.md) ---
#
# A door for a narrower reader than the assistant is opened by NAMING a profile, never by assembling
# options. A profile fixes its read subset and its reply projection, and every profiled door also
# demands a uid allowlist and a request log. None of those has a switch: a deployment that leaves one
# out gets a door that refuses to listen, not a door that quietly answers with less protection. (Four
# independent flags were the first cut; dropping any one of them served raw `last_status` text.)
# Repository implementation only (Thomas 2026-10-15, R-01 alternative a; D-11 a1/f2 confirmed for the
# repository 2026-10-16). It grants no access: no compose service runs a profiled door, and the Claude
# door stays BLOCKED until its operational checks pass.


@dataclass(frozen=True)
class DoorProfile:
    """One named reader's door: which reads it serves, and the reply contract for stored text."""

    name: str
    reads: frozenset[str]
    projection: str


CLAUDE_PROFILE = "claude"

# The reads a profiled door may carry at all: those answered through a closed reply contract. A read
# joins this set when it has one, never by being added to a profile.
_PROFILE_ADMISSIBLE_READS: frozenset[str] = frozenset({"schedules", "scheduler_events", "holdings_status"})

# D-11 f2 names four more reads for the Claude door. Traced field by field, each answers raw:
# `heartbeat` puts `"<type>: <message>"` of a parse failure in `detail`; `runtime_status` carries the
# stored `updated_by` (an operator id) and the operator's free-text `reason`; `crypto_readiness` and
# `holdings_status` render stored free text and pass stored keys through. A profile must not answer a
# sensitive read raw, so they are held out until each has a contract or Thomas decides otherwise.
# `holdings_status` left this set on 2026-10-26 (HOLDINGS_STATUS_READ_AND_AUTO_FLOWS R1): it is answered through
# contract v1.1 (`store_reads.project_holdings_status`), freshness and H6 check states, never the board.
_PROFILE_HELD_READS: frozenset[str] = frozenset({"runtime_status", "heartbeat", "crypto_readiness"})

PROFILES: Mapping[str, DoorProfile] = MappingProxyType({
    CLAUDE_PROFILE: DoorProfile(
        name=CLAUDE_PROFILE,
        reads=frozenset({"schedules", "scheduler_events", "holdings_status"}),
        projection=store_reads.PROJECTION_V1,
    ),
})


def _registered(profile: Any) -> DoorProfile:
    """``profile`` itself, if it is the object ``PROFILES`` holds under its name and is coherent.

    Identity rather than equality, so a caller cannot build a look-alike with a wider read set or
    no projection and hand it in. Coherence is checked at every use, not at import: a profile
    edited into an unsafe shape refuses to serve rather than failing the import of every door."""
    if not isinstance(profile, DoorProfile) or PROFILES.get(profile.name) is not profile:
        raise ControlBlocked("DOOR_PROFILE_INVALID",
                             "a door profile must be one this module defines; nothing was read")
    reads = profile.reads
    if (not isinstance(reads, frozenset) or not reads or not reads <= _PROFILE_ADMISSIBLE_READS
            or not reads <= set(_READS) or profile.projection not in store_reads.PROJECTIONS):
        raise ControlBlocked("DOOR_PROFILE_INVALID",
                             f"door profile {profile.name!r} is not coherent (a read without a reply contract, "
                             f"or no reply projection); nothing was read")
    return profile


def resolve_profile(name: Any) -> DoorProfile:
    """The profile named ``name``, or a refusal — never a fallback to the unprofiled door."""
    if not isinstance(name, str) or name not in PROFILES:
        raise ControlBlocked("DOOR_PROFILE_UNKNOWN",
                             f"no door profile is named {str(name)[:32]!r}; it carries {sorted(PROFILES)}. "
                             f"Refusing to listen")
    return _registered(PROFILES[name])


# --- the request log of a profiled door (D-11 d2; review R-02) --------------------------------------
#
# One JSONL row per answer the door sends — a reply, a typed refusal, a peer refused at the door, a
# frame that would not decode, a connection turned away busy — plus one `DOOR_OPENED` row before it
# listens, which is also the proof that the log can be written. Fields are fixed: when, the uid the
# KERNEL reported for the peer (`SO_PEERCRED`; null when the platform cannot say — never a guess),
# which read (a name from `_READS`, `<not_a_read>`, or null when no request was decoded), and the
# outcome (`ok` or the reason code). Never the argument, never the reply. A row that cannot be
# written replaces the answer with REQUEST_LOG_UNAVAILABLE, whose text names no path and no error.

_NOT_A_READ = "<not_a_read>"
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}\Z")
_LOG_FIELDS = ("at", "peer_uid", "read", "outcome")
# The read name of the request being served on this handler thread. Reset in the handler's setup
# and taken (then cleared) by its reply, so a pool thread never carries one request's name into
# the next.
_REQUEST = threading.local()


def _audit_read(request: Any) -> str | None:
    command = request.get("command") if isinstance(request, dict) else None
    if not isinstance(command, str):
        return None
    command = command.strip().lower()
    return command if command in _READS else _NOT_A_READ


def _open_request_log(log_path: Path) -> int:
    """An append descriptor on ``log_path`` that is provably the log, or ``OSError`` (review R-07).

    - ``O_NOFOLLOW``: a symlink at the last component is refused (``ELOOP``) — never followed,
      never created through. A platform without it refuses outright; so does one without
      ``fcntl``, because an unlocked append is not the promise this log makes.
    - ``O_NONBLOCK``: a FIFO planted at the path fails or is refused below instead of hanging
      the handler thread. ``O_CLOEXEC``: the descriptor does not leak into a child.
    - ``O_CREAT`` without ``O_EXCL``: created at mode 0640 (less the umask) if absent, appended
      to if present. Nothing here creates the DIRECTORY: a missing one is a refusal, so a door
      never makes host directories of its own accord.
    - Checked on the descriptor, not the path (no check-then-open race): a regular file, one link
      (a hard link would let the log land in another file), owned by this process's user, and
      writable by nobody else.

    It protects the last component. The directories above it are trusted to their owner — the
    log directory's ownership and mode are an operational decision, not made here."""
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None or fcntl is None:
        raise OSError("this platform cannot open the request log without following a link or locking it")
    flags = (os.O_WRONLY | os.O_APPEND | os.O_CREAT | nofollow
             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
    fd = os.open(log_path, flags, 0o640)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != os.geteuid()
                or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
            raise OSError("the request log is not a private regular file of this door's user")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _log_request(log_path: Path, *, peer_uid: int | None, read: str | None, outcome: str, now: str) -> None:
    row = dict(zip(_LOG_FIELDS, (now, peer_uid, read, outcome)))
    data = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    try:
        fd = _open_request_log(log_path)
        try:
            # The lock is on the log's own descriptor: no sidecar file to plant a link at, and
            # `filelock` (shared by every store) stays as it is.
            deadline = time.monotonic() + ACQUIRE_DEADLINE_SECONDS
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("the request log is held by another writer") from None
                    time.sleep(ACQUIRE_POLL_SECONDS)
            # A row is all or nothing: a short or failed write is cut back to where it began, under
            # the same lock, so the next row does not land on the tail of a torn one.
            start = os.fstat(fd).st_size
            try:
                if os.write(fd, data) != len(data):
                    raise OSError("short write to the request log")
            except OSError:
                try:
                    os.ftruncate(fd, start)
                except OSError:
                    pass
                raise
        finally:
            os.close(fd)       # also releases the lock
    except OSError as exc:
        raise ControlBlocked(
            "REQUEST_LOG_UNAVAILABLE",
            "this door records every request and could not record this one; nothing was returned",
        ) from exc


def _peer_uid(sock: Any) -> int | None:
    creds = socket_door.peer_credentials(sock)
    return creds[1] if creds is not None else None


# --- the profiled door's error replies (review R3-5) ------------------------------------------------
#
# Every refusal a profiled door sends is rebuilt here from its CODE: a closed set of codes, each with a
# fixed sentence. What the transport would have sent — `str(exc)` of a typed refusal (a ledger read that
# hits an OSError carries the absolute path, `jsonl.py`), the refusal's `data`, the peer's uid in
# PEER_NOT_PERMITTED, the pid inside BRIDGE_ERROR's `error_ref` — never leaves. A code outside the set
# answers READ_UNAVAILABLE; the request log keeps the code that was actually raised, for the operator.
# The assistant's door is not touched: this runs only in `_AuditedHandler`.

PROFILE_ERROR_TEXT: Mapping[str, str] = MappingProxyType({
    "PEER_NOT_PERMITTED": "this peer is not permitted at this door",
    "PEER_CREDENTIALS_UNAVAILABLE": "this door could not identify the peer; nothing was read",
    "MALFORMED_REQUEST": "the request is not a well-formed read request; nothing was read",
    "PROTO_UNSUPPORTED": "the request names a protocol version this door does not speak",
    "VERB_NOT_PERMITTED": "this door does not serve that read",
    "ARGUMENT_NOT_ACCEPTED": "that read takes no argument",
    "REPLY_SCHEMA_INVALID": "the reply did not match its contract; nothing was returned",
    "REQUEST_LOG_UNAVAILABLE": "this door records every request and could not record this one; nothing was returned",
    "BRIDGE_BUSY": "this door is busy; nothing was read, retry shortly",
    "BRIDGE_ERROR": "this door could not complete the request; nothing was returned",
    "READ_UNAVAILABLE": "the requested read is unavailable; nothing was returned",
})
_ECHO_TOKEN = re.compile(r"^[\x21-\x7e]{1,128}\Z")


def profile_error_payload(code: Any, echo_from: Any = None) -> dict[str, Any]:
    """The only error shape a profiled door sends: ``{ok, reason_code, reason}`` with a code from
    ``PROFILE_ERROR_TEXT`` and its fixed sentence, plus the request's own envelope echo (proto,
    client_id, request_id) when those are well-formed tokens the peer itself sent."""
    code = code if isinstance(code, str) and code in PROFILE_ERROR_TEXT else "READ_UNAVAILABLE"
    payload: dict[str, Any] = {"ok": False, "reason_code": code, "reason": PROFILE_ERROR_TEXT[code]}
    if isinstance(echo_from, dict):
        proto = echo_from.get(socket_door.PROTO_KEY)
        if isinstance(proto, int) and not isinstance(proto, bool) and proto in socket_door.SUPPORTED_PROTOS:
            payload[socket_door.PROTO_KEY] = proto
        for key in (socket_door.CLIENT_ID_KEY, "request_id"):
            value = echo_from.get(key)
            if isinstance(value, str) and _ECHO_TOKEN.match(value):
                payload[key] = value
    return payload


_FALLBACK_ERROR = {"ok": False, "reason_code": "BRIDGE_ERROR", "reason": PROFILE_ERROR_TEXT["BRIDGE_ERROR"]}


class _AuditedHandler(socket_door._Handler):  # type: ignore[misc]
    """``socket_door``'s handler with every outgoing answer logged first and every refusal rebuilt
    from its code (``profile_error_payload``).

    ``_Handler.handle`` sends every answer it gives — the peer refusal, a decode refusal, the reply
    — through ``_reply``, so that one override sees them all without changing the shared transport.
    The coupling is to a private name of a sibling module, deliberately:
    ``test_every_answer_the_transport_sends_is_logged`` fails if the transport ever answers some
    other way."""

    def setup(self) -> None:
        super().setup()
        _REQUEST.read = None

    def _reply(self, payload: dict[str, Any]) -> None:
        read = getattr(_REQUEST, "read", None)
        _REQUEST.read = None
        code = payload.get("reason_code")
        outcome = "ok" if payload.get("ok") is True else (
            code if isinstance(code, str) and _REASON_CODE.match(code) else "UNKNOWN")
        try:
            _log_request(self.server.request_log, peer_uid=_peer_uid(self.request), read=read,  # type: ignore[attr-defined]
                         outcome=outcome, now=timeutil.utc_now_iso())
        except ControlBlocked as exc:
            payload = profile_error_payload(exc.reason_code, echo_from=payload)
        try:
            if payload.get("ok") is not True:
                payload = profile_error_payload(payload.get("reason_code"), echo_from=payload)
        except Exception:  # noqa: BLE001 — a refusal that cannot be rebuilt is answered by the constant
            payload = dict(_FALLBACK_ERROR)
        super()._reply(payload)


class _ProfileDoor(socket_door.SocketDoor):  # type: ignore[misc]
    """A door opened for one profile: its handler logs every answer, and it will not listen
    without a uid allowlist or with a log it cannot write."""

    def __init__(self, path: Path, apply: Any, *, profile: DoorProfile, request_log: Path,
                 max_concurrent_requests: int) -> None:
        def remembering(request: Any) -> dict[str, Any]:
            _REQUEST.read = _audit_read(request)
            return apply(request)

        super().__init__(path, remembering, max_concurrent_requests=max_concurrent_requests)
        self.profile = profile
        self.request_log = request_log
        self.RequestHandlerClass = _AuditedHandler
        try:
            if not self.allowed_client_uids:      # re-read by the transport itself, after the bind
                raise ControlBlocked("READ_UID_ALLOWLIST_REQUIRED",
                                     f"{socket_door.CLIENT_UID_ENV} is empty at bind; refusing to listen")
            _log_request(request_log, peer_uid=None, read=None, outcome="DOOR_OPENED",
                         now=timeutil.utc_now_iso())
        except BaseException:
            self.server_close()
            path.unlink(missing_ok=True)          # bound by us above: leave no dead socket behind
            raise

    def _refuse_busy(self, request: Any) -> None:
        try:
            _log_request(self.request_log, peer_uid=_peer_uid(request), read=None, outcome="BRIDGE_BUSY",
                         now=timeutil.utc_now_iso())
        except ControlBlocked:
            pass  # the connection is refused either way; nothing is answered that the log missed
        # Not the transport's own busy text (it names the door's ceiling): the fixed one.
        try:
            request.settimeout(socket_door.REQUEST_TIMEOUT_SECONDS)
            request.sendall((json.dumps(profile_error_payload("BRIDGE_BUSY"), ensure_ascii=False) + "\n")
                            .encode("utf-8"))
        except OSError:
            return


def open_door(
    path: Path,
    *,
    control_store: ControlStore,
    ledger: LedgerStore,
    registry: TaskRegistryStore | None = None,
    working_memory: WorkingMemoryStore | None = None,
    schedules: ScheduleStore | None = None,
    approval_store: ApprovalStore | None = None,
    profile: str | None = None,
    request_log: Path | None = None,
) -> socket_door.SocketDoor:
    """Listen on ``path`` and serve reads from it.

    Framing, deadline, size cap, peer check, concurrency ceiling and error envelope come from
    ``socket_door``, shared with the other doors so a malformed frame cannot be answered two
    different ways. What this door supplies is its verb table and its ceiling.

    ``profile`` (a name in ``PROFILES``) opens the door for that reader instead, and then
    ``request_log`` and a non-empty ``MVP_BRIDGE_CLIENT_UID`` are required. Without a profile this
    is the assistant's door, unchanged, and a request log on it is refused: a log belongs to a
    profiled door, so a half-configured door cannot pass for one.
    """
    if profile is None:
        if request_log is not None:
            raise ControlBlocked(
                "DOOR_PROFILE_REQUIRED",
                "a request log is part of a door profile and is not configured on its own; name the "
                "profile. Refusing to listen",
            )
        return socket_door.SocketDoor(
            path,
            lambda request: apply_read(
                request, control_store=control_store, ledger=ledger,
                registry=registry, working_memory=working_memory,
                schedules=schedules, approval_store=approval_store,
            ),
            max_concurrent_requests=MAX_CONCURRENT_REQUESTS,
        )

    door_profile = resolve_profile(profile)
    if request_log is None:
        raise ControlBlocked(
            "REQUEST_LOG_REQUIRED",
            f"door profile {door_profile.name!r} records every request; name its request log. "
            f"Refusing to listen",
        )
    if not socket_door.resolve_client_uids():
        raise ControlBlocked(
            "READ_UID_ALLOWLIST_REQUIRED",
            f"{socket_door.CLIENT_UID_ENV} must name the uids permitted at this door. It was opened "
            f"for a named reader, so group reachability alone is not a stated peer: refusing to listen",
        )

    def apply(request: Any) -> dict[str, Any]:
        return apply_read(
            request, control_store=control_store, ledger=ledger,
            registry=registry, working_memory=working_memory,
            schedules=schedules, approval_store=approval_store,
            profile=door_profile,
        )

    return _ProfileDoor(path, apply, profile=door_profile, request_log=Path(request_log),
                        max_concurrent_requests=MAX_CONCURRENT_REQUESTS)
