"""The dispatch door — the assistant asks this runtime to *do* a bounded piece of work.

Two assistant doors already exist: ``switch_bridge`` stops and starts the runtime,
``read_bridge`` looks at it. This is the third verb the design always pointed at: it *starts*
work. Starting is the thing the switch door will not do unprompted (``resume`` re-arms an
autonomous path, and the assistant on the other end reads untrusted text and can be talked into
a tool call — which is why that door requires an approval Thomas signs). Admitting a start here
without one is safe only because of what it is bounded to — not trust, but **effect class**.

Every task dispatched here runs the ordinary intake pipeline (:func:`pipeline.run_task`) as
one of a fixed, non-trading, REVIEW_ONLY role set, capped at permission P3. The guarantee is
not "the assistant will only ask for safe work" — a prompt injection is exactly what defeats
that — it is that this door **cannot express** unsafe work:

- ``_ALLOWED_KINDS`` is a closed set of request kinds, each of which resolves (via
  ``planner.REQUEST_KIND_CAPABILITIES``) to a P3-ceiling specialist: analysis ->
  ``general.specialist``, research -> ``research.general``, translation ->
  ``translation.general``, content -> ``content.general``. ``development`` (also P3) is left
  out on purpose, every trading kind is absent because none exists, and
  ``execution.live_trader`` is non-routable so no kind here can select it. A kind outside the
  set is refused, never defaulted, and a test asserts the set stays exactly these four.
- The door never passes ``write_path``/``writer`` — the only inputs that lift a run to a P3
  workspace write — and refuses any request key it does not name, so a caller cannot smuggle
  one in. A dispatched run's largest effect is a rendered, validated report in the ledger.
  The permission invariant (``authority.authority_invariant_holds``, enforced in
  ``permission`` and ``assignment``) is the deeper net beneath the kind allowlist.
- Nothing here reaches the money path. There is no crypto/trading kind, no venue, no order;
  the live-trading stack is a different container and this door carries no verb into it.

**The kill switch is checked here, not downstream.** ``run_task`` does not read control state
— the CLI, the operator loop, and the crypto cycle each check it at their own door. This is
one of those doors, so it refuses while the runtime is PAUSED/KILLED: a halt must stop new
work arriving over a socket exactly as it stops work arriving over Telegram.

**Attribution.** Every dispatched task is stamped ``requester_id = assistant_bridge``,
``requester_type = "agent"`` (honestly not Thomas), so the ledger can tell assistant-initiated
work from the operator's own. The stated reason rides the task's source reference and is
recorded with it.

Failure directions, each chosen once:

- request that is not an object -> ``MALFORMED_REQUEST``; empty text -> ``REQUEST_REQUIRED``.
- kind outside ``_ALLOWED_KINDS`` -> ``KIND_NOT_PERMITTED`` (named, not defaulted — a caller
  that asked for ``development`` believed it would run).
- an unnamed key (e.g. ``write_path``) -> ``ARGUMENT_NOT_ACCEPTED``: a caller that passed it
  believed it would be used, and silently dropping it is the quiet mismatch a later reader
  misreads as a wrong answer.
- missing reason -> ``REASON_REQUIRED``. An unattributed start on a shared budget is not worth
  the convenience of omitting it.
- runtime PAUSED/KILLED -> the control state's own refusal ``reason_code``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import bridge_idempotency, socket_door, timeutil
from .control import ControlStore
from .errors import ControlBlocked, MvpRuntimeError
from .pipeline import run_task
from .programization import ProgramizationStore
from .store import LedgerStore
from .working_memory import WorkingMemoryStore

# The assistant's name on every dispatched task, shared with the other doors so a rename in
# one place cannot desync attribution.
from .socket_door import ASSISTANT_ACTOR

# Its own subdirectory sibling of the other doors' sockets, so the assistant's container
# reaches it through the same group-owned `bridge/` mount.
SOCKET_REL = ".runtime_governance_state/bridge/dispatch.sock"
SOCKET_ENV = "MVP_DISPATCH_BRIDGE_SOCKET"

# THE permission surface, as request kinds. Each resolves to a routable specialist whose
# permission ceiling is P3 and whose runtime effect is REVIEW_ONLY. `development` (also P3) is
# excluded on purpose — the MVP use case is analysis, and widening this is a governance
# decision, not a default. A test asserts the set stays exactly these four and that
# `development` is a real kind left out rather than one that silently disappeared.
_ALLOWED_KINDS: frozenset[str] = frozenset({"analysis", "research", "translation", "content"})

# The kind a request omitting one gets. Analysis is the MVP's core use case and its role
# (`general.specialist`) is the one that legitimately holds it.
_DEFAULT_KIND = "analysis"

# The whole request surface. Anything else is refused rather than ignored — in particular
# `write_path`/`writer`, the only inputs that would lift a run to a P3 workspace write.
# `request_id` is optional and carries no authority: it names the request so a retry after a
# client-side timeout is the same request rather than a second one (`bridge_idempotency`).
_ALLOWED_KEYS: frozenset[str] = frozenset(
    {"request", "kind", "reason", bridge_idempotency.REQUEST_ID_KEY}
)

# This door's name in the request ledger. Ids are scoped per door, so the same id at the switch
# door and this one are two different requests — which they are.
_DOOR = "dispatch"

# Bound on the recorded reason: long enough to attribute, short enough that a reason cannot
# bloat the source record. The assistant keeps the full text; this is the audit stub.
_MAX_REASON_ON_SOURCE = 180

# Selecting the analysis/validator/search providers is a side-effecting, gated step (the
# Safety-Flag Gate reads a per-machine grant). It is injected so `apply_dispatch` stays pure
# with respect to it and testable without a grant: the default (no selector) runs the
# deterministic mock pipeline, and the CLI supplies a selector that fails closed.
ProviderSelector = Callable[[], dict[str, Any]]


def socket_path(root: Path | None = None) -> Path:
    """The socket path, overridable per-deployment via ``MVP_DISPATCH_BRIDGE_SOCKET``."""
    return socket_door.resolve_socket_path(SOCKET_ENV, SOCKET_REL, root)


def apply_dispatch(
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
    """Validate one dispatch request and run it, or raise a typed ``ControlBlocked``.

    Pure with respect to the transport and the provider gate — a decoded object in (plus
    already-resolved providers), a reply out — which is what makes the permission surface
    testable without a listener or a grant.
    """
    if not isinstance(request, dict):
        raise ControlBlocked("MALFORMED_REQUEST", "request must be a JSON object")

    unexpected = set(request) - _ALLOWED_KEYS
    if unexpected:
        raise ControlBlocked(
            "ARGUMENT_NOT_ACCEPTED",
            f"this door accepts only {sorted(_ALLOWED_KEYS)}; it will not act on {sorted(unexpected)}",
        )

    text = request.get("request")
    if not isinstance(text, str) or not text.strip():
        raise ControlBlocked("REQUEST_REQUIRED", "a dispatch needs a non-empty 'request'")

    raw_kind = request.get("kind")
    if raw_kind is None:
        kind = _DEFAULT_KIND
    elif isinstance(raw_kind, str) and raw_kind.strip():
        kind = raw_kind.strip().lower()
    else:
        raise ControlBlocked("MALFORMED_REQUEST", "'kind' must be a non-empty string when given")
    if kind not in _ALLOWED_KINDS:
        raise ControlBlocked(
            "KIND_NOT_PERMITTED",
            f"{kind!r} is not a kind this door dispatches; it carries {sorted(_ALLOWED_KINDS)} only",
        )

    reason = request.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ControlBlocked("REASON_REQUIRED", "a dispatch must state its reason; it is recorded")
    reason = reason.strip()

    request_id = bridge_idempotency.request_id_of(request)
    if request_id is not None and ledger is None:
        # Fail closed rather than running unprotected: a caller that sent an id asked for
        # at-most-once, and honouring the request while dropping the guarantee gives it the
        # duplicate it was trying to avoid, silently.
        raise ControlBlocked(
            "IDEMPOTENCY_UNAVAILABLE",
            f"{bridge_idempotency.REQUEST_ID_KEY!r} needs the ledger this door was opened "
            f"without; refusing rather than dispatching without at-most-once",
        )

    # Kill-switch binding: this is an execution door and `run_task` does not read control
    # state. A PAUSED/KILLED runtime refuses new work here exactly as on every other door.
    state = control_store.load()
    if not state.execution_allowed:
        raise ControlBlocked(
            state.refusal_reason_code(),
            f"runtime is {state.mode}; new dispatches are blocked until an authenticated resume",
        )

    # Last, so a frame that was going to be refused anyway never burns its id — and first
    # relative to the effect, so the claim and the run cannot be separated by a check.
    stamp = now or timeutil.utc_now_iso()
    fingerprint = bridge_idempotency.fingerprint(request) if request_id is not None else ""
    if request_id is not None:
        prior = bridge_idempotency.claim(
            ledger, door=_DOOR, request_id=request_id,
            request_fingerprint=fingerprint, now=stamp,
        )
        if prior is not None:
            return bridge_idempotency.replay_reply(prior)

    resolved = providers or {}
    # No `write_path`/`writer` is passed — the door has no key that lifts a run above P3, and
    # the run is attributed to the assistant, never to Thomas.
    try:
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
    except BaseException:
        # The run did not reach an outcome, so the id names nothing. Holding it would make the
        # caller wait out the claim TTL to retry something that never happened.
        if request_id is not None:
            bridge_idempotency.release(
                ledger, door=_DOOR, request_id=request_id,
                request_fingerprint=fingerprint, now=stamp,
            )
        raise

    if result.get("status") == "COMPLETED":
        reply = {
            "ok": True,
            "kind": kind,
            "task_id": result.get("task_id"),
            "final_response": result.get("final_response", ""),
            "actor": ASSISTANT_ACTOR,
        }
    else:
        # A pipeline BLOCK is a real answer, not a bridge error: surface its reason_code so the
        # assistant reports "the runtime refused this" rather than "the door broke".
        block = result.get("block") or {}
        reply = {
            "ok": False,
            "kind": kind,
            "task_id": result.get("task_id"),
            "reason_code": block.get("reason_code", "DISPATCH_BLOCKED"),
            "reason": block.get("message", "the runtime blocked this dispatch"),
            "actor": ASSISTANT_ACTOR,
        }

    # A BLOCK completes the id as surely as a COMPLETED does: both ran the pipeline, both left
    # a task in the ledger, and re-running one on a retry is the duplicate work this prevents.
    # The recorded outcome is the task's identity, never its text — the report is already in the
    # ledger and the read door's `result <task_id>` is how it is fetched.
    if request_id is not None:
        bridge_idempotency.complete(
            ledger, door=_DOOR, request_id=request_id, request_fingerprint=fingerprint,
            outcome={"kind": kind, "task_id": result.get("task_id"), "ok": reply["ok"]},
            now=stamp,
        )
        # Named on the fresh reply as well as the replayed one, so `replayed` is the only thing
        # that separates them and a caller never has to infer which it got.
        reply[bridge_idempotency.REQUEST_ID_KEY] = request_id
    return reply


def _source_ref(reason: str) -> str:
    """The reason, recorded on the task's source. Bounded so a long reason cannot bloat the
    record."""
    return f"{ASSISTANT_ACTOR}:dispatch: {reason}"[:_MAX_REASON_ON_SOURCE]


# The narrowest ceiling of the three doors, and the only one where the ceiling is about cost
# rather than only about liveness. This is the door that RUNS the pipeline: a slot is held for
# the length of a full analysis, not the length of a store read, and each one can invoke a model
# on a quota shared with the operator's own assistant (see the OPENROUTER note in
# `docker-compose.yml`). Two concurrent runs is a bound on both the thread count and the spend.
MAX_CONCURRENT_REQUESTS = 2


def open_door(
    path: Path,
    *,
    control_store: ControlStore,
    ledger: LedgerStore,
    working_memory: WorkingMemoryStore | None = None,
    programization: ProgramizationStore | None = None,
    resolve_providers: ProviderSelector | None = None,
) -> socket_door.SocketDoor:
    """Listen on ``path`` and dispatch from it.

    ``resolve_providers`` is called once per request so a grant that changes between requests
    is re-read; a gate failure becomes a typed refusal rather than a dropped connection.
    Framing, deadline, size cap, peer check, concurrency ceiling and error envelope come from
    ``socket_door``, shared with the other doors so a malformed frame cannot be answered two
    different ways.
    """

    def _apply(request: Any) -> dict[str, Any]:
        providers: dict[str, Any] = {}
        if resolve_providers is not None:
            try:
                providers = resolve_providers()
            except MvpRuntimeError as exc:
                raise ControlBlocked(exc.reason_code, str(exc)) from exc
        return apply_dispatch(
            request,
            control_store=control_store,
            ledger=ledger,
            working_memory=working_memory,
            programization=programization,
            providers=providers,
        )

    return socket_door.SocketDoor(path, _apply)
