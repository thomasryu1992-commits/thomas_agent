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

from pathlib import Path
from typing import Any

from . import control, domain_console, memory_console, registry_console, socket_door
from .control import ControlStore
from .errors import ControlBlocked
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

# THE permission surface. Read it as the answer to "what can the assistant see": every entry
# is a read, and the two mutating verbs their consoles also implement (registry CANCEL,
# memory PROMOTE) are not here and never will be — a test asserts it.
_READS: dict[str, tuple[str, Any]] = {
    "runtime_status":   (_CONTROL, control.CMD_STATUS),
    "crypto_status":    (_DOMAIN, ("CRYPTO", "status")),
    "crypto_readiness": (_DOMAIN, ("CRYPTO", "readiness")),
    "crypto_paper":     (_DOMAIN, ("CRYPTO", "paper")),
    "tasks":            (_REGISTRY, "TASKS"),
    "history":          (_REGISTRY, "HISTORY"),
    "result":           (_REGISTRY, "RESULT"),
    "memory":           (_MEMORY, "LIST"),
}

# The only two that mean anything with an argument. Everything else refuses one rather than
# accepting it silently — a caller that passed an argument believed it would be used, and
# answering as though it had not been passed is the kind of quiet mismatch that later reads
# as a wrong answer.
_TAKES_ARGUMENT = frozenset({"history", "result"})


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
) -> dict[str, Any]:
    """Validate one request and render it, or raise a typed error.

    Pure with respect to the transport — an already-decoded object in, a reply out — which
    is what makes the permission surface testable without a listener.
    """
    if not isinstance(request, dict):
        raise ControlBlocked("MALFORMED_REQUEST", "request must be a JSON object")

    command = request.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ControlBlocked("MALFORMED_REQUEST", "request needs a non-empty 'command'")
    command = command.strip().lower()

    if command not in _READS:
        raise ControlBlocked(
            "VERB_NOT_PERMITTED",
            f"{command!r} is not a read this door serves; it carries {sorted(_READS)}",
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

    if family == _CONTROL:
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
    else:
        outcome = memory_console.apply_memory_command(
            (spec, argument, None), operator_id=ASSISTANT_ACTOR,
            working_memory=working_memory, ledger=ledger, control_store=control_store,
            now=now, repo_root=repo_root,
        )

    return {
        "ok": True,
        "command": command,
        "reply": outcome["reply"],
        "action": outcome.get("action"),
    }


# The widest ceiling of the three doors, because these are the cheapest requests: a read
# renders from stores already on disk and returns. It is a ceiling all the same — an assistant
# polling the board in a loop is the likeliest source of connection pressure precisely because
# nothing about a read discourages it.
MAX_CONCURRENT_REQUESTS = 8


def open_door(
    path: Path,
    *,
    control_store: ControlStore,
    ledger: LedgerStore,
    registry: TaskRegistryStore | None = None,
    working_memory: WorkingMemoryStore | None = None,
) -> socket_door.SocketDoor:
    """Listen on ``path`` and serve reads from it.

    Framing, deadline, size cap, peer check, concurrency ceiling and error envelope come from
    ``socket_door``, shared with the other doors so a malformed frame cannot be answered two
    different ways. What this door supplies is its verb table and its ceiling.
    """
    return socket_door.SocketDoor(
        path,
        lambda request: apply_read(
            request, control_store=control_store, ledger=ledger,
            registry=registry, working_memory=working_memory,
        ),
        max_concurrent_requests=MAX_CONCURRENT_REQUESTS,
    )
