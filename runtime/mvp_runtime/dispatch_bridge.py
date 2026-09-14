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

from . import bridge_idempotency, registry_console, socket_door, task_registry, timeutil
from .control import ControlStore
from . import workflow as wf, workflow_console
from .errors import WorkflowBlocked
from .workflow_store import WorkflowStore
from .errors import ControlBlocked, PersistenceError, TaskRegistryBlocked
from .naver_research import MAX_SEED_CHARS
from .store import LedgerStore
from .task_registry import TaskRegistryStore

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

# Door API v3 (sequence 2, P05): a frame carrying `command` is a workflow command, never a
# dispatch. Its own closed key set — a v3 frame that also carries v2 keys is refused, and a v2
# frame never sees these keys. Served only when the door was opened with the workflow store
# (`--workflow-manager`); otherwise every v3 command is refused by name (WORKFLOW_UNAVAILABLE)
# and nothing falls back to a v2 run (acceptance A18). The reads (`workflow.status`,
# `workflow.list`, `workflow.events`) live on this door rather than the read door because the
# store is this process's — the manager loop writes it here — and because the policy's closed
# `assistant_read` verb list (1.5.0) is a governance change Thomas applies, not a door's.
COMMAND_KEY = "command"
V3_COMMANDS: frozenset[str] = frozenset({
    "capabilities", "workflow.submit", "workflow.status", "workflow.list", "workflow.events", "workflow.cancel",
    "workflow.retry_step", "workflow.propose_update", "workflow.report_usage",
    # P09 (V0.2 §1.3, conditional): a schedule change inside the policy's delegated scope is
    # applied, outside it recorded as a proposal, on a financial kind refused. Dormant —
    # every change refused — until the policy carries `assistant_schedule` (1.6.0).
    "schedule.propose_change",
})
_V3_KEYS: frozenset[str] = frozenset(
    {COMMAND_KEY, bridge_idempotency.REQUEST_ID_KEY, "plan", "workflow_id", "expected_version",
     "reason", "after_cursor", "limit", "step_key", "reported_usage", "change"}
) | socket_door.ENVELOPE_KEYS
_V3_READS: frozenset[str] = frozenset({"capabilities", "workflow.status", "workflow.list", "workflow.events"})
MAX_LIST = 50
MAX_EVENTS = 200

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

# What the door hands to the engine: the validated fields, nothing else — request, kind,
# reason, the optional keyword seeds, and the optional `client_id` (attribution the assistant
# named; door API v2). In production this is `open_door`'s forward over the worker socket; in
# tests it is a capture. Injected so `apply_dispatch` stays pure with respect to the transport
# and testable without a listener.
Executor = Callable[[str, str, str, "str | None", "str | None"], dict[str, Any]]

# How long the door waits for the worker's answer. A real run on the free-tier chain is
# minute-plus; the assistant's own client gives up at 280s, but the run must be allowed to
# finish and land in the ledger regardless. Since PR8 (2026-09-04) the worker opens an `AGENT`
# registry entry for every assistant run and closes it with the outcome, so a reply that
# misses the client's window is NOT lost: the same `request_id` replays
# `{task_id, status, result}` from that entry and the ledger (`_replay_data` below), and the
# read door's `result <registry_entry_id>` fetches the same text. This deadline bounds only how
# long THIS connection waits. (Before PR8 the entry did not exist and a missed reply really was
# a lost report; the note that stood here said so and outlived the fix by ten days.)
WORKER_DEADLINE_SECONDS = 600.0

# The largest result a replay carries inline (door API v2, D-4). `call_door` reads at most
# 1 MiB of reply, and a rendered response is UTF-8 Korean at ~3 bytes a character; past this
# the replay names where the result lives (`result_ref`) and the read door's `result` fetches
# it — the same text, one more round trip, never a truncated one.
MAX_REPLAY_RESULT_BYTES = 768 * 1024


def socket_path(root: Path | None = None) -> Path:
    """The socket path, overridable per-deployment via ``MVP_DISPATCH_BRIDGE_SOCKET``."""
    return socket_door.resolve_socket_path(SOCKET_ENV, SOCKET_REL, root)


def _replay_data(
    prior: dict[str, Any], *, registry: TaskRegistryStore | None, ledger: LedgerStore | None,
) -> dict[str, Any]:
    """What a repeated id gets back beside "already applied": the run's identity from the
    recorded outcome, its current status from the registry, and — when it delivered — its
    result re-rendered from the ledger (door API v2: `{task_id, status, result}`).

    The outcome row never carries text (a test pins that); the registry entry the outcome
    names is the pointer, and the ledger is the authority the text comes from. A result too
    large to carry inline is named by `result_ref` instead of truncated.
    """
    outcome = dict(prior.get("outcome") or {})
    data: dict[str, Any] = {**outcome, "status": None, "result": None, "result_ref": None}
    entry_id = outcome.get("registry_entry_id")
    if registry is None or not isinstance(entry_id, str) or not entry_id:
        return data
    try:
        entry = registry.find(entry_id)
    except (TaskRegistryBlocked, PersistenceError):
        entry = None
    if entry is None:
        return data
    data["status"] = entry.status
    data["trace_id"] = entry.trace_id
    data["result_ref"] = entry.result_ref
    if entry.status == task_registry.DELIVERED:
        rendered = registry_console.render_result(entry, ledger)
        if rendered is not None and len(rendered.encode("utf-8")) <= MAX_REPLAY_RESULT_BYTES:
            data["result"] = rendered
    return data


def apply_dispatch(
    request: Any,
    *,
    control_store: ControlStore,
    ledger: LedgerStore | None = None,
    execute: Executor | None = None,
    now: str | None = None,
    registry: TaskRegistryStore | None = None,
    workflow_store: WorkflowStore | None = None,
    manager_enabled: bool = False,
    schedule_store: Any | None = None,
    delegation: Any | None = None,
    v2_intake: bool = True,
) -> dict[str, Any]:
    """Validate one dispatch request and forward it, or raise a typed ``ControlBlocked``.

    ``v2_intake=False`` (P10, the entry-point cutover) refuses NEW single dispatches by name
    (``V2_INTAKE_CLOSED``) while the v3 commands, the reads, and the replay of a request id
    accepted before the close all keep working — closing an intake drains an entry point, it
    does not lose what the entry point already took.

    Pure with respect to the transport on both sides — a decoded object in (plus an injected
    executor), a reply out — which is what makes the permission surface testable without a
    listener or a worker.
    """
    if not isinstance(request, dict):
        raise ControlBlocked("MALFORMED_REQUEST", "request must be a JSON object")

    if COMMAND_KEY in request:
        return apply_workflow_command(
            request, control_store=control_store, workflow_store=workflow_store, now=now,
            schedule_store=schedule_store, ledger=ledger, delegation=delegation,
            manager_enabled=manager_enabled, v2_intake=v2_intake,
        )

    unexpected = set(request) - _ALLOWED_KEYS
    if unexpected:
        raise ControlBlocked(
            "ARGUMENT_NOT_ACCEPTED",
            f"this door accepts only {sorted(_ALLOWED_KEYS)}; it will not act on {sorted(unexpected)}",
        )
    # The envelope is validated here and echoed on the reply. `proto` never crosses to the
    # worker — the dialect is this door's business; `client_id` does, as attribution the task
    # record carries (`created_by`) and the registry entry the worker opens can be traced to.
    _proto, client_id = socket_door.validate_envelope(request)

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
    if not v2_intake:
        # The cutover (P10): new single dispatches are refused here, by name, before anything
        # is claimed or run. A retry of an id this door already accepted is not new work — it
        # falls through to the claim below and replays, so a client that lost a reply during
        # the close still gets its run back.
        live = (bridge_idempotency.lookup(ledger, door=_DOOR, request_id=request_id, now=now or timeutil.utc_now_iso())
                if request_id is not None and ledger is not None else None)
        if live is None:
            raise ControlBlocked(
                "V2_INTAKE_CLOSED",
                "this door no longer takes single dispatches: the entry point is being cut over to "
                "workflows (door API v3, submit_workflow); nothing was started. A retry of a "
                "request_id accepted before the close still replays",
            )
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
                data=_replay_data(prior, registry=registry, ledger=ledger),
            )

    try:
        reply = execute(text.strip(), kind, reason, naver_keywords, client_id)
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
            outcome={"kind": kind, "task_id": reply.get("task_id"), "ok": bool(reply.get("ok")),
                     "registry_entry_id": reply.get("registry_entry_id")},
            now=stamp,
        )
        # Named on the fresh reply as well as the replayed one, so `replayed` is the only thing
        # that separates them and a caller never has to infer which it got.
        reply[bridge_idempotency.REQUEST_ID_KEY] = request_id
    # `data` is the run's identity — what a client needs to find the run again — never its text.
    return socket_door.envelope(
        reply, request=request,
        data={key: reply.get(key)
              for key in ("kind", "task_id", "trace_id", "registry_entry_id", "actor") if key in reply},
    )


def apply_workflow_command(
    request: dict[str, Any],
    *,
    control_store: ControlStore,
    workflow_store: WorkflowStore | None,
    now: str | None = None,
    manager_enabled: bool = False,
    schedule_store: Any | None = None,
    ledger: LedgerStore | None = None,
    delegation: Any | None = None,
    v2_intake: bool = True,
) -> dict[str, Any]:
    """Door API v3: one workflow command, or a typed refusal (sequence 2, P05).

    The principal is this door's peer — the assistant actor — never a value in the frame.
    ``workflow.submit`` requires ``request_id`` (at-most-once is not optional for a plan), is
    refused while the runtime is PAUSED/KILLED, and answers ``accepted`` only after the store's
    transaction committed. The reads and ``workflow.cancel`` answer while halted: a read is a
    read, and stopping the assistant's own work needs no gate.
    """
    unexpected = set(request) - _V3_KEYS
    if unexpected:
        raise ControlBlocked(
            "ARGUMENT_NOT_ACCEPTED",
            f"a workflow command accepts only {sorted(_V3_KEYS)}; it will not act on {sorted(unexpected)}",
        )
    _proto, _client_id = socket_door.validate_envelope(request)
    command = request.get(COMMAND_KEY)
    if not isinstance(command, str) or command.strip().lower() not in V3_COMMANDS:
        raise ControlBlocked(
            "VERB_NOT_PERMITTED",
            f"{command!r} is not a workflow command this door serves; it carries {sorted(V3_COMMANDS)}",
        )
    command = command.strip().lower()
    stamp = now or timeutil.utc_now_iso()

    if command == "capabilities":
        data = {
            "proto": max(socket_door.SUPPORTED_PROTOS),
            "commands": sorted(V3_COMMANDS), "kinds": sorted(_ALLOWED_KINDS),
            "plan_schema": wf.PLAN_SCHEMA_VERSION, "max_steps": wf.MAX_STEPS,
            "workflow_manager": bool(manager_enabled and workflow_store is not None),
            "v2_intake": "open" if v2_intake else "closed",          # P10: the cutover state of this door
        }
        reply = f"door API v3: {', '.join(data['commands'])} (workflow manager " + ("on" if data["workflow_manager"] else "off") + ")"
        return socket_door.envelope({"ok": True, "command": command, "reply": reply}, request=request, data=data)

    if workflow_store is None:
        # Refused by name, never a v2 run in disguise: a client that asked for v3 on a door
        # that does not serve it must learn that here (A18).
        raise ControlBlocked(
            "WORKFLOW_UNAVAILABLE",
            "this door was opened without the workflow manager; workflow commands are not served "
            "here and nothing was run",
        )

    if command == "workflow.submit":
        state = control_store.load()
        if not state.execution_allowed:
            raise ControlBlocked(
                state.refusal_reason_code(),
                f"runtime is {state.mode}; new workflows are not accepted until an authenticated resume",
            )
        request_id = bridge_idempotency.request_id_of(request)
        if request_id is None:
            raise ControlBlocked("REQUEST_ID_REQUIRED", "a workflow submission carries a request_id; it is its name")
        plan = request.get("plan")
        if not isinstance(plan, dict):
            raise WorkflowBlocked("PLAN_INVALID", "'plan' must be a JSON object (workflow_plan.v0.1)")
        outcome = workflow_store.submit(principal=socket_door.ASSISTANT_ACTOR, request_id=request_id, plan=plan, now=stamp)
        data = {"workflow_id": outcome.workflow_id, "status": outcome.status, "accepted_at": outcome.accepted_at,
                "replayed": outcome.replayed, "request_id": request_id}
        head = "REPLAYED (not re-accepted)" if outcome.replayed else "ACCEPTED"
        reply = f"{head}: workflow {outcome.workflow_id} [{outcome.status}] request_id={request_id}"
        return socket_door.envelope(
            {"ok": True, "command": command, "reply": reply, "replayed": outcome.replayed,
             bridge_idempotency.REQUEST_ID_KEY: request_id},
            request=request, data=data,
        )

    if command == "workflow.list":
        limit = _bounded_int(request.get("limit"), default=20, cap=MAX_LIST, name="limit")
        rows = workflow_store.list_workflows(limit=limit)
        data = {"workflows": [{k: r.get(k) for k in ("workflow_id", "status", "goal", "created_at", "updated_at",
                                                     "row_version", "last_reason_code")} for r in rows],
                "count": len(rows), "as_of": stamp}
        return socket_door.envelope({"ok": True, "command": command, "reply": workflow_console.render_list(rows)},
                                    request=request, data=data)

    if command == "workflow.events":
        after = _bounded_int(request.get("after_cursor"), default=0, cap=None, name="after_cursor")
        limit = _bounded_int(request.get("limit"), default=50, cap=MAX_EVENTS, name="limit")
        events, next_cursor = workflow_store.events_after(after, limit=limit)
        data = {"events": events, "next_cursor": next_cursor, "count": len(events), "as_of": stamp}
        return socket_door.envelope(
            {"ok": True, "command": command, "reply": workflow_console.render_events(events, next_cursor)},
            request=request, data=data,
        )

    if command == "schedule.propose_change":
        # P09. The principal is this door's peer; the scope is the policy's; the store and the
        # ledger are the scheduler's own (the same rows and events scheduler_cli writes).
        from . import schedule_delegation                 # noqa: PLC0415 — loaded for this command only
        if schedule_store is None:
            raise ControlBlocked("SCHEDULES_UNAVAILABLE", "this door was opened without the schedule store")
        state = control_store.load()
        if not state.execution_allowed:
            raise ControlBlocked(state.refusal_reason_code(),
                                 f"runtime is {state.mode}; schedule changes wait for an authenticated resume")
        change = schedule_delegation.ChangeRequest.parse(request.get("change"))
        outcome = schedule_delegation.apply_change(
            schedule_store, ledger, change, actor=socket_door.ASSISTANT_ACTOR, now=stamp,
            delegation=delegation, bounded=True,
        )
        data = {"verdict": outcome.verdict, "action": change.action, "reasons": list(outcome.reasons),
                "schedule": outcome.schedule.as_record() if outcome.schedule is not None else None, "as_of": stamp}
        return socket_door.envelope(
            {"ok": True, "command": command, "reply": schedule_delegation.render_outcome(change, outcome)},
            request=request, data=data,
        )

    workflow_id = request.get("workflow_id")
    if not isinstance(workflow_id, str) or not wf.WORKFLOW_ID_PATTERN.match(workflow_id.strip()):
        raise ControlBlocked("MALFORMED_REQUEST", f"'{command}' needs a 'workflow_id' (wf_…)")
    workflow_id = workflow_id.strip()

    if command == "workflow.status":
        view = workflow_store.status_view(workflow_id, now=stamp)
        return socket_door.envelope({"ok": True, "command": command, "reply": workflow_console.render_view(view)},
                                    request=request, data=view)

    if command == "workflow.report_usage":
        # The reported budget layer (P08, V0.2 Q24): what the assistant says it spent on this
        # workflow, from its own accounting. Recorded and shown beside the enforced budget,
        # never added to it, never a reason to block anything.
        usage = request.get("reported_usage")
        if not isinstance(usage, dict):
            raise ControlBlocked("MALFORMED_REQUEST", "'workflow.report_usage' carries a 'reported_usage' object")
        view = workflow_store.record_reported_usage(workflow_id, principal=socket_door.ASSISTANT_ACTOR, usage=usage, now=stamp)
        reported = view["budget"]["reported"]
        return socket_door.envelope(
            {"ok": True, "command": command,
             "reply": (f"USAGE REPORTED for {workflow_id}: {reported['input_tokens']} in / {reported['output_tokens']} out tokens,"
                       f" ≈${reported['estimated_cost_usd']:.4f} [{reported['cost_status']}] — shown beside the budget, not enforced\n"
                       + workflow_console.render_view(view))},
            request=request, data=view,
        )

    # workflow.cancel / workflow.retry_step — both act at the version the caller read
    expected = request.get("expected_version")
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
        raise ControlBlocked(
            "MALFORMED_REQUEST",
            f"'{command}' needs the 'expected_version' the caller read (workflow.status → row_version)",
        )
    reason = request.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ControlBlocked("REASON_REQUIRED", f"'{command}' must state its reason; it is recorded")
    if command == "workflow.propose_update":
        plan = request.get("plan")
        if not isinstance(plan, dict):
            raise WorkflowBlocked("PLAN_INVALID", "'plan' must be a JSON object (workflow_plan.v0.1) — the whole new version")
        view = workflow_store.propose_update(workflow_id, expected_version=expected, plan=plan, reason=reason.strip(), now=stamp)
        return socket_door.envelope(
            {"ok": True, "command": command,
             "reply": f"PLAN UPDATED to v{view['plan_version']}\n{workflow_console.render_view(view)}"},
            request=request, data=view,
        )
    if command == "workflow.retry_step":
        step_key = request.get("step_key")
        if not isinstance(step_key, str) or not step_key.strip():
            raise ControlBlocked("MALFORMED_REQUEST", "'workflow.retry_step' names the 'step_key' to re-open")
        view = workflow_store.retry_step(workflow_id, step_key.strip(), expected_version=expected,
                                         reason=reason.strip(), now=stamp)
        return socket_door.envelope(
            {"ok": True, "command": command,
             "reply": f"RETRY OPENED for step {step_key.strip()!r}\n{workflow_console.render_view(view)}"},
            request=request, data=view,
        )
    view = workflow_store.request_cancel(workflow_id, expected_version=expected, reason=reason.strip(), now=stamp)
    done = view["status"] == wf.W_CANCELLED
    head = "CANCELLED" if done else f"CANCELLING (status={view['status']}; a running attempt stops at its next boundary or its lease)"
    return socket_door.envelope(
        {"ok": True, "command": command, "reply": f"{head}\n{workflow_console.render_view(view)}"},
        request=request, data=view,
    )


def _bounded_int(value: Any, *, default: int, cap: int | None, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ControlBlocked("MALFORMED_REQUEST", f"'{name}' must be a non-negative integer when given")
    return min(value, cap) if cap is not None else value


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
    registry: TaskRegistryStore | None = None,
    workflow_store: WorkflowStore | None = None,
    manager_enabled: bool = False,
    schedule_store: Any | None = None,
    delegation: Any | None = None,
    v2_intake: bool = True,
) -> socket_door.SocketDoor:
    """Listen on ``path``, validate, and forward to the worker at ``worker_socket``.

    ``workflow_store`` (sequence 2, P05): the store the workflow manager loop writes in this
    process; when given, door API v3 commands are served from it. Without it every v3 command
    is refused by name.

    Framing, deadline, size cap, peer check, concurrency ceiling and error envelope come from
    ``socket_door``, shared with the other doors so a malformed frame cannot be answered two
    different ways. The transport detail of a failed forward goes to this service's log; the
    assistant gets ``WORKER_UNAVAILABLE`` and no path — the same redaction rule as
    ``BRIDGE_ERROR``.
    """

    def _forward(
        text: str, kind: str, reason: str, naver_keywords: str | None, client_id: str | None,
    ) -> dict[str, Any]:
        # Optional keys travel only when present, so a frame without them stays byte-identical
        # to the pre-lane contract and the worker's closed key set never sees a null.
        frame: dict[str, Any] = {"request": text, "kind": kind, "reason": reason}
        if naver_keywords is not None:
            frame["naver_keywords"] = naver_keywords
        if client_id is not None:
            frame[socket_door.CLIENT_ID_KEY] = client_id
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
            registry=registry, workflow_store=workflow_store, manager_enabled=manager_enabled,
            schedule_store=schedule_store, delegation=delegation, v2_intake=v2_intake,
        )

    return socket_door.SocketDoor(
        path, _apply, max_concurrent_requests=MAX_CONCURRENT_REQUESTS,
    )
