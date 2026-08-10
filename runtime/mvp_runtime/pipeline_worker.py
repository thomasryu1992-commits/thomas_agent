"""The pipeline worker — the dispatch door's engine, in a process of its own.

The dispatch door used to run ``pipeline.run_task`` in-process, which made the one
assistant-facing service that executes work also the one that had to hold credentials: the
model provider chain, the search key, and — once the Naver lane landed — a Search Ad secret
that signs against the ad account. The split
(``docs/proposals/CREDENTIAL_PLANE_SEPARATION_V0.1.md``, Thomas 2026-08-10) puts the frame
parsing and the keys in different address spaces: the door validates and forwards holding no
credential env; this worker holds the env and runs the pipeline.

**This is not an assistant door, and its socket placement is half of the point.** The doors'
sockets live under ``bridge/``, which the door processes hand to the assistant's gid and the
assistant's container mounts. A worker socket there would let the assistant call the engine
directly, past every cap the door enforces. So this socket lives under ``internal/`` — a
directory the assistant does not mount — and this module refuses to listen at all unless the
deployment names the uids permitted to connect (``MVP_BRIDGE_CLIENT_UID``; the door's own uid,
10001 in the shipped image). Group reachability alone is not accepted: the assistant's
container has at times carried the runtime's gid in ``group_add``, so a mount mistake must not
be one permission bit away from being access. ``SO_PEERCRED`` is what refuses it either way.

**Validation here is defence in depth, not a second authority.** The door owns the permission
surface — its ``_ALLOWED_KINDS`` is imported rather than redefined, so there is exactly one
place the set lives — and the worker re-checks kind, shape, and the kill switch on arrival
because fail-closed means not trusting the peer to have checked, even when the peer is our own
door. Idempotency is NOT re-done here: the claim is the door's, made before the forward.

**What this process holds and what it must never hold.** Model, search, and Naver env — yes;
that is its purpose. Money — never: no ``BINANCE_*``, no ``MVP_LIVE_*``, no venue or order
key, pinned per-service by ``test_deployment_env_passthrough``. A compromised worker can spend
model quota and reach the research APIs; it cannot place an order.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import socket_door
from .control import ControlStore
from .errors import ControlBlocked, MvpRuntimeError
from .pipeline import run_task
from .programization import ProgramizationStore
from .socket_door import ASSISTANT_ACTOR
from .store import LedgerStore
from .working_memory import WorkingMemoryStore

# The permission surface stays the door's; see the module docstring. Imported, not copied, so
# a widening there is a widening here and the two can never disagree.
from .dispatch_bridge import _ALLOWED_KINDS

# Under `internal/`, deliberately NOT `bridge/` — see the module docstring. `open_door`
# refuses a path under a directory named `bridge` outright, as a tripwire against an override
# pointing the engine back into the assistant-mounted directory for convenience.
SOCKET_REL = ".runtime_governance_state/internal/pipeline.sock"
SOCKET_ENV = "MVP_PIPELINE_WORKER_SOCKET"

# What the door forwards: the three fields it validated, nothing else. `request_id` is
# deliberately absent — idempotency is claimed at the door, and a frame carrying one here did
# not come from the door.
_ALLOWED_KEYS: frozenset[str] = frozenset({"request", "kind", "reason"})

# Bound on the recorded reason: long enough to attribute, short enough that a reason cannot
# bloat the source record. The assistant keeps the full text; this is the audit stub.
_MAX_REASON_ON_SOURCE = 180

# Matches the door's ceiling (`dispatch_bridge.MAX_CONCURRENT_REQUESTS`): the door holds one
# of its slots for the life of each forward, so its ceiling is the effective one and this is
# the backstop that holds if a second caller ever reaches this socket.
MAX_CONCURRENT_REQUESTS = 2

# Selecting the analysis/validator/search providers is a side-effecting, gated step (the
# Safety-Flag Gate reads a per-machine grant). It is injected so `apply_work` stays pure with
# respect to it and testable without a grant: the default (no selector) runs the deterministic
# mock pipeline, and the CLI supplies a selector that fails closed.
ProviderSelector = Callable[[], dict[str, Any]]


def socket_path(root: Path | None = None) -> Path:
    """The socket path, overridable per-deployment via ``MVP_PIPELINE_WORKER_SOCKET``."""
    return socket_door.resolve_socket_path(SOCKET_ENV, SOCKET_REL, root)


def apply_work(
    request: Any,
    *,
    control_store: ControlStore,
    ledger: LedgerStore | None = None,
    working_memory: WorkingMemoryStore | None = None,
    programization: ProgramizationStore | None = None,
    providers: dict[str, Any] | None = None,
    now: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Re-validate one forwarded dispatch and run it, or raise a typed ``ControlBlocked``.

    The reply shape is the dispatch contract the assistant has always seen — the door relays
    it unchanged. A raise here reaches the door as a typed envelope without a ``task_id``,
    which is how the door tells "the run never happened" (release the id, surface the refusal)
    from "the run happened and blocked" (a real answer, relayed).
    """
    if not isinstance(request, dict):
        raise ControlBlocked("MALFORMED_REQUEST", "request must be a JSON object")

    unexpected = set(request) - _ALLOWED_KEYS
    if unexpected:
        raise ControlBlocked(
            "ARGUMENT_NOT_ACCEPTED",
            f"this worker accepts only {sorted(_ALLOWED_KEYS)}; it will not act on "
            f"{sorted(unexpected)}",
        )

    text = request.get("request")
    if not isinstance(text, str) or not text.strip():
        raise ControlBlocked("REQUEST_REQUIRED", "a forwarded dispatch needs a non-empty 'request'")

    kind = request.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        # The door always names a kind (it resolves its default before forwarding); a frame
        # without one did not come from the door, and this worker does not guess.
        raise ControlBlocked(
            "MALFORMED_REQUEST", "a forwarded dispatch names its 'kind'; this frame does not",
        )
    kind = kind.strip().lower()
    if kind not in _ALLOWED_KINDS:
        raise ControlBlocked(
            "KIND_NOT_PERMITTED",
            f"{kind!r} is not a kind this worker runs; it carries {sorted(_ALLOWED_KINDS)} only",
        )

    reason = request.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ControlBlocked("REASON_REQUIRED", "a dispatch must state its reason; it is recorded")
    reason = reason.strip()

    # The door checked this before forwarding; checked again so a halt that lands mid-flight
    # still stops the run, and so this engine refuses on its own even if a frame ever arrives
    # by a path that is not the door.
    state = control_store.load()
    if not state.execution_allowed:
        raise ControlBlocked(
            state.refusal_reason_code(),
            f"runtime is {state.mode}; new work is blocked until an authenticated resume",
        )

    resolved = providers or {}
    # No `write_path`/`writer` is passed — nothing on this socket can lift a run above P3, and
    # the run is attributed to the assistant, never to Thomas.
    result = run_task(
        text.strip(),
        request_kind=kind,
        provider=resolved.get("provider"),
        validator_provider=resolved.get("validator_provider"),
        search_tool=resolved.get("search_tool"),
        working_memory=working_memory,
        programization=programization,
        store=ledger,
        repo_root=repo_root,
        now=now,
        requester_id=ASSISTANT_ACTOR,
        requester_type="agent",
        channel="agent",
        source_ref=_source_ref(reason),
        authenticated=True,
    )

    if result.get("status") == "COMPLETED":
        return {
            "ok": True,
            "kind": kind,
            "task_id": result.get("task_id"),
            "final_response": result.get("final_response", ""),
            "actor": ASSISTANT_ACTOR,
        }
    # A pipeline BLOCK is a real answer, not a worker error: it carries its `task_id`, so the
    # door completes the idempotency claim and relays it — the assistant reports "the runtime
    # refused this" rather than "the door broke".
    block = result.get("block") or {}
    return {
        "ok": False,
        "kind": kind,
        "task_id": result.get("task_id"),
        "reason_code": block.get("reason_code", "DISPATCH_BLOCKED"),
        "reason": block.get("message", "the runtime blocked this dispatch"),
        "actor": ASSISTANT_ACTOR,
    }


def _source_ref(reason: str) -> str:
    """The reason, recorded on the task's source. The `dispatch` tag is kept from before the
    split so ledger attribution reads identically across it."""
    return f"{ASSISTANT_ACTOR}:dispatch: {reason}"[:_MAX_REASON_ON_SOURCE]


def open_door(
    path: Path,
    *,
    control_store: ControlStore,
    ledger: LedgerStore,
    working_memory: WorkingMemoryStore | None = None,
    programization: ProgramizationStore | None = None,
    resolve_providers: ProviderSelector | None = None,
) -> socket_door.SocketDoor:
    """Listen on ``path`` and run forwarded work from it — refusing to listen at all unless
    the deployment states who may connect.

    ``resolve_providers`` is called once per request so a grant that changes between requests
    is re-read; a gate failure becomes a typed refusal rather than a dropped connection.
    """
    if not socket_door.resolve_client_uids():
        raise ControlBlocked(
            "WORKER_UID_ALLOWLIST_REQUIRED",
            f"{socket_door.CLIENT_UID_ENV} must name the uids permitted at this socket. This "
            f"process holds the credential env, so group reachability alone is not a stated "
            f"peer — refusing to listen rather than serving whoever shares a gid",
        )
    if "bridge" in {part.lower() for part in path.parts}:
        # A tripwire, not a proof: the property is "not inside a directory the assistant
        # mounts", which this process cannot see. What it CAN see is the one concrete mistake
        # that recreates the pre-split exposure — pointing the override at the doors' own
        # directory — and that one it refuses.
        raise ControlBlocked(
            "WORKER_SOCKET_IN_ASSISTANT_DIR",
            f"{path} is inside the assistant-mounted bridge directory; the engine must not be "
            f"reachable past the door. Use the internal/ default or another private directory",
        )

    def _apply(request: Any) -> dict[str, Any]:
        providers: dict[str, Any] = {}
        if resolve_providers is not None:
            try:
                providers = resolve_providers()
            except MvpRuntimeError as exc:
                raise ControlBlocked(exc.reason_code, str(exc)) from exc
        return apply_work(
            request,
            control_store=control_store,
            ledger=ledger,
            working_memory=working_memory,
            programization=programization,
            providers=providers,
        )

    return socket_door.SocketDoor(
        path, _apply, max_concurrent_requests=MAX_CONCURRENT_REQUESTS,
    )
