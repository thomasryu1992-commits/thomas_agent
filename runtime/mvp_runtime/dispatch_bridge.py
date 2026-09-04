"""The dispatch door — the assistant asks this runtime to *do* a bounded piece of work.

Two assistant doors already exist: ``switch_bridge`` stops and starts the runtime,
``read_bridge`` looks at it. This is the third verb the design always pointed at: it *starts*
work. Starting is the thing the switch door will not do unprompted (``resume`` re-arms an
autonomous path, and the assistant on the other end reads untrusted text and can be talked into
a tool call — which is why that door requires an approval Thomas signs). Admitting a start here
without one is safe only because of what it is bounded to — not trust, but **effect class**.

**This door validates and forwards; it does not run the pipeline.** It used to, and that
implementation convenience made it the one assistant-facing process that had to hold model and
search keys — and, once the Naver lane landed, an ad-account credential — in the same address
space that parses the assistant's frames. The split
(``docs/proposals/CREDENTIAL_PLANE_SEPARATION_V0.1.md``, Thomas 2026-08-10) moves execution to
``pipeline_worker``, a separate service holding that env, reached over an internal socket the
assistant's container cannot traverse to. This process carries no credential env at all, like
the read and switch doors.

What the door still is — every guarantee below is enforced HERE, before anything is forwarded:

- ``_ALLOWED_KINDS`` is a closed set of request kinds, each of which resolves (via
  ``planner.REQUEST_KIND_CAPABILITIES``) to a P3-ceiling specialist: analysis ->
  ``general.specialist``, research -> ``research.general``, translation ->
  ``translation.general``, content -> ``content.general``. ``development`` (also P3) is left
  out on purpose, every trading kind is absent because none exists, and
  ``execution.live_trader`` is non-routable so no kind here can select it. A kind outside the
  set is refused, never defaulted, and a test asserts the set stays exactly these four. The
  worker re-checks the same set on arrival — defence in depth, not a second authority.
- The door never passes ``write_path``/``writer`` — the only inputs that lift a run to a P3
  workspace write — and refuses any request key it does not name, so a caller cannot smuggle
  one in. What crosses to the worker is exactly the validated fields (request, kind, reason,
  and optionally ``naver_keywords``, which adds read-only [K#] evidence and lifts nothing);
  a dispatched run's largest effect is a rendered, validated report in the ledger.
- Nothing here reaches the money path. There is no crypto/trading kind, no venue, no order;
  the live-trading stack is a different container, this door carries no verb into it, and the
  worker's own environment carries no money variable either (pinned per-service by
  ``test_deployment_env_passthrough``).

**The kill switch is checked here, not downstream.** ``run_task`` does not read control state
— the CLI, the operator loop, and the crypto cycle each check it at their own door. This is
one of those doors, so it refuses while the runtime is PAUSED/KILLED: a halt must stop new
work arriving over a socket exactly as it stops work arriving over Telegram. (The worker
re-checks before running, so a halt that lands mid-flight still refuses the run.)

**Attribution.** Every dispatched task is stamped ``requester_id = assistant_bridge``,
``requester_type = "agent"`` (honestly not Thomas) — by the worker, whose reply this door
relays unchanged, so the contract the assistant sees is byte-for-byte what it was before the
split.

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
- worker unreachable / not answering -> ``WORKER_UNAVAILABLE``, and **never** a fallback to
  in-door execution: a fallback would need the keys back in this process, which quietly undoes
  the separation at the first hiccup. The transport detail goes to this service's log, not to
  the assistant.
- worker refusal (its kill-switch re-check, its kind re-check, ``BRIDGE_BUSY``) -> relayed as
  this door's own typed refusal under the worker's ``reason_code``. Nothing ran, so an
  idempotency id the frame carried is released, exactly as an in-process refusal would have.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

from . import bridge_idempotency, socket_door, timeutil
from .control import ControlStore
from .errors import ControlBlocked
from .naver_research import MAX_SEED_CHARS
from .store import LedgerStore

# Its own subdirectory sibling of the other doors' sockets, so the assistant's container
# reaches it through the same group-owned `bridge/` mount.
SOCKET_REL = ".runtime_governance_state/bridge/dispatch.sock"
SOCKET_ENV = "MVP_DISPATCH_BRIDGE_SOCKET"

# THE permission surface, as request kinds. Each resolves to a routable specialist whose
# permission ceiling is P3 and whose runtime effect is REVIEW_ONLY. `development` (also P3) is
# excluded on purpose — the MVP use case is analysis, and widening this is a governance
# decision, not a default. A test asserts the set stays exactly these four and that
# `development` is a real kind left out rather than one that silently disappeared.
# `pipeline_worker` imports this set for its arrival re-check; the authority stays here.
_ALLOWED_KINDS: frozenset[str] = frozenset({"analysis", "research", "translation", "content"})

# The kind a request omitting one gets. Analysis is the MVP's core use case and its role
# (`general.specialist`) is the one that legitimately holds it.
_DEFAULT_KIND = "analysis"

# The whole request surface. Anything else is refused rather than ignored — in particular
# `write_path`/`writer`, the only inputs that would lift a run to a P3 workspace write.
# `request_id` is optional and carries no authority: it names the request so a retry after a
# client-side timeout is the same request rather than a second one (`bridge_idempotency`).
_ALLOWED_KEYS: frozenset[str] = frozenset(
    {"request", "kind", "reason", "naver_keywords", bridge_idempotency.REQUEST_ID_KEY}
) | socket_door.ENVELOPE_KEYS

# `naver_keywords` (optional): comma-separated seeds for the Naver keyword brief, forwarded
# so the worker's run carries measured demand as [K#] evidence. Admitted on every kind this
# door dispatches, deliberately: this door's refusals mean EFFECT — what a request cannot do —
# and the brief is read-only evidence collection that lifts no permission and changes no role.
# Refusing it on, say, translation would encode an editorial judgment as a security boundary,
# and the next reader could no longer trust that a refusal here means effect. The length cap
# is the lane's own (`naver_research.MAX_SEED_CHARS`): a longer string would not fail the run
# downstream, it would DEGRADE the brief (SEED_TOO_LONG in the record) — honest there, but a
# caller at this door asked for evidence and deserves the refusal where it can still fix it.

# This door's name in the request ledger. Ids are scoped per door, so the same id at the switch
# door and this one are two different requests — which they are.
_DOOR = "dispatch"

# What the door hands to the engine: the three validated fields, nothing else. In production
# this is `open_door`'s forward over the worker socket; in tests it is a capture. Injected so
# `apply_dispatch` stays pure with respect to the transport and testable without a listener.
Executor = Callable[[str, str, str, "str | None"], dict[str, Any]]

# How long the door waits for the worker's answer. A real run on the free-tier chain is
# minute-plus; the assistant's own client gives up at 280s, but the run must be allowed to
# finish and land in the ledger regardless. NB: the worker writes no task-registry entry, so
# the read door's `result`/`history` CANNOT fetch it — a missed reply is a lost report (v2).
WORKER_DEADLINE_SECONDS = 600.0


def socket_path(root: Path | None = None) -> Path:
    """The socket path, overridable per-deployment via ``MVP_DISPATCH_BRIDGE_SOCKET``."""
    return socket_door.resolve_socket_path(SOCKET_ENV, SOCKET_REL, root)


def apply_dispatch(
    request: Any,
    *,
    control_store: ControlStore,
    ledger: LedgerStore | None = None,
    execute: Executor | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Validate one dispatch request and forward it, or raise a typed ``ControlBlocked``.

    Pure with respect to the transport on both sides — a decoded object in (plus an injected
    executor), a reply out — which is what makes the permission surface testable without a
    listener or a worker.
    """
    if not isinstance(request, dict):
        raise ControlBlocked("MALFORMED_REQUEST", "request must be a JSON object")

    unexpected = set(request) - _ALLOWED_KEYS
    if unexpected:
        raise ControlBlocked(
            "ARGUMENT_NOT_ACCEPTED",
            f"this door accepts only {sorted(_ALLOWED_KEYS)}; it will not act on {sorted(unexpected)}",
        )
    # The envelope is validated here and echoed on the reply; it never crosses to the worker —
    # the forward frame stays exactly the validated fields, and `client_id` is attribution the
    # ledger will carry once the registry records these runs (door API v2, next increment).
    socket_door.validate_envelope(request)

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

    raw_seeds = request.get("naver_keywords")
    if raw_seeds is None:
        naver_keywords = None
    elif not isinstance(raw_seeds, str) or not raw_seeds.strip():
        raise ControlBlocked(
            "MALFORMED_REQUEST", "'naver_keywords' must be a non-empty string when given",
        )
    elif len(raw_seeds) > MAX_SEED_CHARS:
        raise ControlBlocked(
            "MALFORMED_REQUEST",
            f"'naver_keywords' exceeds {MAX_SEED_CHARS} characters; a longer list would only "
            "degrade the brief downstream — trim the seeds instead",
        )
    else:
        naver_keywords = raw_seeds.strip()

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

    # Kill-switch binding: this is an execution door and the pipeline does not read control
    # state. A PAUSED/KILLED runtime refuses new work here exactly as on every other door.
    state = control_store.load()
    if not state.execution_allowed:
        raise ControlBlocked(
            state.refusal_reason_code(),
            f"runtime is {state.mode}; new dispatches are blocked until an authenticated resume",
        )

    # A door with nowhere to send validated work refuses it — before the claim, so the id is
    # never burned on a misconfiguration, and NEVER by running the pipeline itself: the keys
    # that would take are exactly what this process no longer holds.
    if execute is None:
        raise ControlBlocked(
            "WORKER_UNAVAILABLE",
            "this door was opened without a forwarder to the pipeline worker; nothing was "
            "dispatched",
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
            return socket_door.envelope(
                bridge_idempotency.replay_reply(prior), request=request,
                data=dict(prior.get("outcome") or {}),
            )

    try:
        reply = execute(text.strip(), kind, reason, naver_keywords)
        if not isinstance(reply, dict):
            raise ControlBlocked(
                "WORKER_UNAVAILABLE",
                "the pipeline worker's reply was not an object; nothing usable came back",
            )
        if "kind" not in reply and not reply.get("ok"):
            # A refusal envelope, not a run. The discriminator is deliberate: the worker's two
            # run-reply shapes always echo the `kind` they ran (a BLOCK is a run — it may even
            # lack a task_id when it blocked before a task existed), while a bare typed
            # envelope (`socket_door`'s refusal/BUSY/ERROR shape) never carries one. Nothing
            # ran, so surface it as this door's typed refusal — the handler below releases the
            # claim, exactly as an in-process refusal used to, and the same id stays retriable
            # once the cause clears.
            raise ControlBlocked(
                str(reply.get("reason_code") or "WORKER_REFUSED"),
                str(reply.get("reason") or "the pipeline worker refused this dispatch"),
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

    # A BLOCK completes the id as surely as a COMPLETED does: both ran the pipeline, both left
    # a task in the ledger, and re-running one on a retry is the duplicate work this prevents.
    # The recorded outcome is the task's identity, never its text — and until the worker writes
    # a task-registry entry (door API v2), the read door cannot fetch that report by its id.
    if request_id is not None:
        bridge_idempotency.complete(
            ledger, door=_DOOR, request_id=request_id, request_fingerprint=fingerprint,
            outcome={"kind": kind, "task_id": reply.get("task_id"), "ok": bool(reply.get("ok"))},
            now=stamp,
        )
        # Named on the fresh reply as well as the replayed one, so `replayed` is the only thing
        # that separates them and a caller never has to infer which it got.
        reply[bridge_idempotency.REQUEST_ID_KEY] = request_id
    # `data` is the run's identity — what a client needs to find the run again — never its text.
    return socket_door.envelope(
        reply, request=request,
        data={key: reply.get(key) for key in ("kind", "task_id", "trace_id", "actor") if key in reply},
    )


# The narrowest ceiling of the doors, and the only one where the ceiling is about cost rather
# than only about liveness. A slot is held for the length of a full analysis (the door waits on
# the worker's answer), and each forwarded run can invoke a model on a quota shared with the
# operator's own assistant (see the OPENROUTER note in `docker-compose.yml`). Two concurrent
# forwards is a bound on both the thread count and the spend; the worker states the same
# ceiling on its own socket as the backstop.
MAX_CONCURRENT_REQUESTS = 2


def open_door(
    path: Path,
    *,
    control_store: ControlStore,
    ledger: LedgerStore,
    worker_socket: Path,
    worker_deadline_seconds: float = WORKER_DEADLINE_SECONDS,
) -> socket_door.SocketDoor:
    """Listen on ``path``, validate, and forward to the worker at ``worker_socket``.

    Framing, deadline, size cap, peer check, concurrency ceiling and error envelope come from
    ``socket_door``, shared with the other doors so a malformed frame cannot be answered two
    different ways. The transport detail of a failed forward goes to this service's log; the
    assistant gets ``WORKER_UNAVAILABLE`` and no path — the same redaction rule as
    ``BRIDGE_ERROR``.
    """

    def _forward(
        text: str, kind: str, reason: str, naver_keywords: str | None
    ) -> dict[str, Any]:
        # The seeds key travels only when present, so a frame without it stays byte-identical
        # to the pre-lane contract and the worker's closed key set never sees a null.
        frame: dict[str, Any] = {"request": text, "kind": kind, "reason": reason}
        if naver_keywords is not None:
            frame["naver_keywords"] = naver_keywords
        try:
            return socket_door.call_door(
                worker_socket,
                frame,
                deadline_seconds=worker_deadline_seconds,
            )
        except ControlBlocked as exc:
            if exc.reason_code in {"DOOR_UNREACHABLE", "DOOR_REPLY_MALFORMED"}:
                sys.stderr.write(f"DISPATCH_FORWARD[{exc.reason_code}]: {exc}\n")
                sys.stderr.flush()
                raise ControlBlocked(
                    "WORKER_UNAVAILABLE",
                    "the pipeline worker is not answering; nothing was dispatched — the "
                    "failure is recorded in this service's log",
                ) from exc
            raise

    def _apply(request: Any) -> dict[str, Any]:
        return apply_dispatch(
            request, control_store=control_store, ledger=ledger, execute=_forward,
        )

    return socket_door.SocketDoor(
        path, _apply, max_concurrent_requests=MAX_CONCURRENT_REQUESTS,
    )
