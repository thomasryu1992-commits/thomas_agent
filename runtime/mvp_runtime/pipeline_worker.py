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

from . import socket_door, timeutil
from .control import ControlStore
from .errors import ControlBlocked, MvpRuntimeError
from .naver_research import MAX_SEED_CHARS
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

# What the door forwards: the fields it validated, nothing else. `request_id` is
# deliberately absent — idempotency is claimed at the door, and a frame carrying one here did
# not come from the door. `naver_keywords` is optional and travels only when the caller sent
# it: read-only [K#] evidence for the run, re-validated here with the door's own rule because
# fail-closed means not trusting the peer to have checked, even when the peer is our own door.
_ALLOWED_KEYS: frozenset[str] = frozenset(
    {"request", "kind", "reason", "naver_keywords", "actor_profile", "job", "inventory",
     "proposal_inputs", "ideation_inputs"}
)

# The second thing this process does, and the reason it is a *set* rather than a second
# entrypoint: after the plane separation this is where the model credentials live, so anything
# that needs a model has to happen here — not only the P3 pipeline the dispatch door forwards.
#
# `crypto_data_review` is the first such caller (Phase 2 D5). Its fire stays the scheduler's:
# the scheduler builds the inventory (pure state reads, no model), appends the record to the
# ledger, sends the operator's sheet, and judges the stall. Only the **model call** crosses,
# which is the smallest cut that takes an LLM response out of the process holding the Binance
# keys. The worker imports `crypto.data_review` for its prompt and parser and nothing else —
# it collects no market data and touches no pool.
#
# A job is NOT a request kind and deliberately does not share that set: `_ALLOWED_KINDS` is the
# assistant-facing permission surface, and folding a scheduler-only job into it would widen
# what the dispatch door can express. The door cannot express `job` at all — its own
# `_ALLOWED_KEYS` does not name it, so a frame carrying one is refused before it forwards.
JOB_DATA_REVIEW = "crypto_data_review"
# The second job (D6). Its input is a handful of scalars, not a market frame, and that is a
# property of the prompt rather than a concession: `build_proposal_prompt` takes family names,
# a focus, a count and a venue and has never read the snapshot. The frame is used only by the
# judging half, which invokes no model and therefore stays beside the market data.
JOB_CRYPTO_PROPOSE = "crypto_propose"
# The third job, and the first that is not crypto: the blog lane's weekly package. Same reason
# as the two above — the Naver keys and the model providers are here and on no other service,
# so the scheduler delegates the sequence rather than collecting anything itself. Unlike them
# it runs the ORDINARY pipeline twice (research, then content) rather than making one bare
# model call, which is why it needs the ledger and the memory stores the other two do not.
JOB_CONTENT_IDEATION = "content_ideation"
_ALLOWED_JOBS: frozenset[str] = frozenset(
    {JOB_DATA_REVIEW, JOB_CRYPTO_PROPOSE, JOB_CONTENT_IDEATION}
)

# What a job frame may carry beside `job` itself. Pipeline keys are refused on a job frame
# rather than ignored: a caller that sent both believed both would be used. Each job then
# requires its own input below, so a frame carrying the other job's input is refused too.
_JOB_KEYS: frozenset[str] = frozenset(
    {"job", "inventory", "proposal_inputs", "ideation_inputs", "reason"}
)

# Bounds on the one job input that is a list. A prompt naming every family ever proposed would
# spend the allowance on recitation; the live table is twelve.
_MAX_EXISTING_FAMILIES = 200

# Who a run is recorded as. Two callers reach this socket — the dispatch door and (since the
# scheduler's analysis_task delegation) the scheduler — and the ledger must keep telling them
# apart: "an operator reading the ledger must be able to tell an action that came from SSH
# from one that came from the assistant" is the property `socket_door.ASSISTANT_ACTOR` exists
# for, and a second caller inheriting the first's identity would quietly end it.
#
# **A caller declares its own profile, and neither declaration is reachable by the assistant.**
# `dispatch_bridge._ALLOWED_KEYS` is closed and does not name `actor_profile`, so a frame
# arriving at the door carrying one is refused (ARGUMENT_NOT_ACCEPTED) before it reaches here
# — the assistant cannot express `scheduler`. The door's forward does not send the key at all
# and gets the default below.
#
# Deliberately NOT a peer check: both callers run as uid 10001 in this deployment, so
# SO_PEERCRED cannot separate them and a second socket would not either. This is an
# attribution declaration, not an authorization — the profile chooses no capability, both
# profiles run the same closed kind set at the same P3 ceiling, and nothing here reads it to
# decide whether the work may run.
ASSISTANT_PROFILE = "assistant"
SCHEDULER_PROFILE = "scheduler"
_ACTOR_PROFILES: dict[str, dict[str, str]] = {
    # `source_ref_format` is applied to the caller's stated reason. The assistant's is stamped
    # here because its reason is free text from the far end; the scheduler states its own
    # source_ref verbatim (`scheduler:<schedule_id>`), which is the exact string an in-process
    # scheduled run recorded before the delegation, so ledger rows read identically across it.
    ASSISTANT_PROFILE: {
        "requester_id": ASSISTANT_ACTOR,
        "requester_type": "agent",
        "channel": "agent",
        "source_ref_format": ASSISTANT_ACTOR + ":dispatch: {reason}",
    },
    SCHEDULER_PROFILE: {
        "requester_id": "mvp.scheduler",
        "requester_type": "scheduler",
        "channel": "scheduler",
        "source_ref_format": "{reason}",
    },
}

# Bound on the recorded reason: long enough to attribute, short enough that a reason cannot
# bloat the source record. The assistant keeps the full text; this is the audit stub.
_MAX_REASON_ON_SOURCE = 180

# Matches the door's ceiling (`dispatch_bridge.MAX_CONCURRENT_REQUESTS`): the door holds one
# of its slots for the life of each forward, so its ceiling is the effective one and this is
# the backstop that holds if a second caller ever reaches this socket.
MAX_CONCURRENT_REQUESTS = 2

# This door's own frame ceiling, stated because a job frame carries an INPUT rather than a
# sentence. The shared default is 8 KiB, sized for console verbs; the crypto data-review
# inventory measured 3,298 bytes on the live host (2026-08-10) and grows with the venue's
# mintable vocabulary and the timeframe ladder. Sitting at 40% of a ceiling that fails as a
# dropped connection — which reads as the worker being down — is not a margin worth keeping.
# Deliberately far short of the knowledge door's document ceiling: this carries a summary,
# never a corpus.
MAX_FRAME_BYTES = 64 * 1024

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
    independent_validation: bool | str = False,
    revise: bool = False,
) -> dict[str, Any]:
    """Re-validate one forwarded dispatch and run it, or raise a typed ``ControlBlocked``.

    ``independent_validation`` and ``revise`` are the two assurance policies the operator loop
    has always been able to set and this one could not. Every task the dispatch door has ever
    forwarded — fifteen on record, including every blog draft — ran with `AUTOMATIC` checks
    only, because `run_task`'s defaults are False and nothing here passed anything else. They
    default to False HERE too: turning either on is a deployment decision (a flag on the
    service's command), not a code change, and both cost model calls per task.

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

    if request.get("job") is not None:
        return _apply_job(
            request, control_store=control_store, providers=providers, now=now,
            ledger=ledger, working_memory=working_memory, programization=programization,
            repo_root=repo_root,
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

    raw_profile = request.get("actor_profile")
    if raw_profile is None:
        # The door does not send one, and its absence means the assistant — today's behaviour,
        # unchanged. Named rather than inferred for the second caller.
        profile_name = ASSISTANT_PROFILE
    elif isinstance(raw_profile, str) and raw_profile.strip() in _ACTOR_PROFILES:
        profile_name = raw_profile.strip()
    else:
        # Refused rather than defaulted to the assistant: a caller that named a profile
        # believed its work would be recorded as that, and quietly filing it under a different
        # actor is the ledger-honesty failure this field exists to prevent.
        raise ControlBlocked(
            "ACTOR_PROFILE_NOT_PERMITTED",
            f"{raw_profile!r} is not an attribution profile this worker records; it carries "
            f"{sorted(_ACTOR_PROFILES)} only",
        )
    profile = _ACTOR_PROFILES[profile_name]

    raw_seeds = request.get("naver_keywords")
    if raw_seeds is None:
        naver_keywords = None
    elif not isinstance(raw_seeds, str) or not raw_seeds.strip() or len(raw_seeds) > MAX_SEED_CHARS:
        # The door already refused these shapes; a frame failing here did not come from the
        # door, and this worker does not guess at what it meant.
        raise ControlBlocked(
            "MALFORMED_REQUEST",
            f"'naver_keywords' must be a non-empty string of at most {MAX_SEED_CHARS} "
            "characters when given",
        )
    else:
        naver_keywords = raw_seeds.strip()

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
    # the run is attributed to its caller, never to Thomas.
    result = run_task(
        text.strip(),
        request_kind=kind,
        keyword_seeds=naver_keywords,
        independent_validation=independent_validation,
        revise=revise,
        provider=resolved.get("provider"),
        validator_provider=resolved.get("validator_provider"),
        search_tool=resolved.get("search_tool"),
        working_memory=working_memory,
        programization=programization,
        store=ledger,
        repo_root=repo_root,
        now=now,
        requester_id=profile["requester_id"],
        requester_type=profile["requester_type"],
        channel=profile["channel"],
        source_ref=_source_ref(reason, profile),
        authenticated=True,
    )

    identity = _identity(result)
    if result.get("status") == "COMPLETED":
        return {
            "ok": True,
            "kind": kind,
            "task_id": identity.get("task_id"),
            "trace_id": identity.get("trace_id"),
            "final_response": result.get("final_response", ""),
            "actor": profile["requester_id"],
        }
    # A pipeline BLOCK is a real answer, not a worker error: it names its `kind`, so the door
    # completes the idempotency claim and relays it — the assistant reports "the runtime
    # refused this" rather than "the door broke".
    block = result.get("block") or {}
    return {
        "ok": False,
        "kind": kind,
        "task_id": identity.get("task_id"),
        "trace_id": identity.get("trace_id"),
        "reason_code": block.get("reason_code", "DISPATCH_BLOCKED"),
        "reason": block.get("message", "the runtime blocked this dispatch"),
        "actor": profile["requester_id"],
    }


def _apply_job(
    request: dict[str, Any],
    *,
    control_store: ControlStore,
    providers: dict[str, Any] | None,
    now: str | None,
    ledger: LedgerStore | None = None,
    working_memory: WorkingMemoryStore | None = None,
    programization: ProgramizationStore | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Run one named job — a bounded model call on a caller-supplied input.

    Separate from the pipeline path above because it *is* separate: no Task is bound, no Role
    is assigned, no P3 ceiling applies, because nothing here is a task — it is one model call
    whose prompt and parser belong to the caller's own module. What it shares with the
    pipeline path is the thing that matters: the credentials, and the kill switch.

    ``now`` is resolved here, against the clock, exactly as ``pipeline.run_task`` resolves its
    own. The door does not supply one — ``build_worker_door``'s ``_apply`` calls ``apply_work``
    without it — so ``None`` is what every job actually receives, and the pipeline path never
    noticed because ``run_task`` has this same line. A job's record does not: the data review
    stamps ``created_at`` from it AND seeds ``review_id`` off it, so a ``None`` produced a
    record with a null timestamp and a **constant** id — `short_id("data_review", {"at": None})`
    is `data_review_246e68234b481e1cb348` for every fire, forever. Measured on the 2026-08-15
    fire, the first one to run through this door (delegation shipped in #680, 2026-08-10; the
    2026-08-08 record still has a real timestamp because it ran in-process).

    Resolved here rather than sent in the frame on purpose. `_JOB_KEYS` is closed, and a
    caller-supplied timestamp would let the frame choose the id its own record is filed under
    — the record's identity should come from the clock of the process that writes it, not from
    the request. Nothing is lost: the caller's own occurrence time is already the schedule's
    `last_run_at`, and the two differ by the socket round trip.
    """
    now = now if now is not None else timeutil.utc_now_iso()
    job = request.get("job")
    if not isinstance(job, str) or job.strip() not in _ALLOWED_JOBS:
        raise ControlBlocked(
            "JOB_NOT_PERMITTED",
            f"{job!r} is not a job this worker runs; it carries {sorted(_ALLOWED_JOBS)} only",
        )
    job = job.strip()

    unexpected = set(request) - _JOB_KEYS
    if unexpected:
        # In particular `request`/`kind`: a frame carrying both a job and a dispatch is two
        # requests, and guessing which one was meant is how a caller gets the other.
        raise ControlBlocked(
            "ARGUMENT_NOT_ACCEPTED",
            f"a {job!r} frame accepts only {sorted(_JOB_KEYS)}; it will not act on "
            f"{sorted(unexpected)}",
        )

    # Same halt binding as a dispatch: this process is an execution door too, and a job that
    # spends model quota is new work.
    state = control_store.load()
    if not state.execution_allowed:
        raise ControlBlocked(
            state.refusal_reason_code(),
            f"runtime is {state.mode}; new work is blocked until an authenticated resume",
        )

    resolved = providers or {}
    if job == JOB_CONTENT_IDEATION:
        inputs = request.get("ideation_inputs")
        if not isinstance(inputs, dict) or not str(inputs.get("seeds") or "").strip():
            raise ControlBlocked(
                "IDEATION_INPUTS_REQUIRED",
                f"{job!r} needs an 'ideation_inputs' object carrying a non-empty 'seeds' "
                f"string; the schedule's request column is where it comes from",
            )
        for other in ("inventory", "proposal_inputs"):
            if other in request:
                raise ControlBlocked(
                    "ARGUMENT_NOT_ACCEPTED", f"{job!r} does not take {other!r}"
                )
        from . import blog_content

        # Unlike the two crypto jobs this runs the ordinary pipeline — twice — so it is handed
        # the ledger and the memory stores rather than only a provider. Its own refusals
        # (`NO_ELIGIBLE_KEYWORD`, a blocked governed run) are `ToolError`s and travel to the
        # scheduler as the fire's status, the same shape a blocked review has.
        return {
            "ok": True,
            "job": job,
            **blog_content.run_content_ideation(
                inputs,
                providers=providers or {},
                ledger=ledger,
                working_memory=working_memory,
                programization=programization,
                now=now,
                repo_root=repo_root,
            ),
        }

    if job == JOB_DATA_REVIEW:
        inventory = request.get("inventory")
        if not isinstance(inventory, dict) or not inventory:
            raise ControlBlocked(
                "INVENTORY_REQUIRED",
                f"{job!r} needs a non-empty 'inventory' object; the caller builds it, this "
                f"worker does not collect one",
            )
        if "proposal_inputs" in request:
            raise ControlBlocked(
                "ARGUMENT_NOT_ACCEPTED",
                f"{job!r} does not take 'proposal_inputs'",
            )
        from .crypto import data_review as crypto_data_review

        provider = (
            resolved.get("validator_provider") or crypto_data_review.MockDataReviewProvider()
        )
        record = crypto_data_review.review_data_gaps(inventory, provider=provider, now=now)
        # The record travels whole: the caller appends it to the ledger, renders the operator's
        # sheet from it, and judges the stall on it. Summarising here would make this worker the
        # authority on a record it does not own.
        return {"ok": True, "job": job, "record": record}

    inputs = request.get("proposal_inputs")
    if not isinstance(inputs, dict):
        raise ControlBlocked(
            "PROPOSAL_INPUTS_REQUIRED",
            f"{job!r} needs a 'proposal_inputs' object naming the families already installed",
        )
    if "inventory" in request:
        raise ControlBlocked("ARGUMENT_NOT_ACCEPTED", f"{job!r} does not take 'inventory'")
    families = inputs.get("existing_families")
    if (not isinstance(families, list)
            or not all(isinstance(f, str) for f in families)
            or len(families) > _MAX_EXISTING_FAMILIES):
        raise ControlBlocked(
            "PROPOSAL_INPUTS_REQUIRED",
            f"'existing_families' must be a list of at most {_MAX_EXISTING_FAMILIES} strings",
        )
    focus = inputs.get("focus")
    if focus is not None and not isinstance(focus, str):
        raise ControlBlocked("PROPOSAL_INPUTS_REQUIRED", "'focus' must be a string when given")

    from .crypto import proposer as crypto_proposer

    provider = (
        resolved.get("validator_provider") or crypto_proposer.MockProposerProvider()
    )
    # Only the model half runs here. The verdicts — which need the market frame — are the
    # caller's, and this worker never sees one.
    generation = crypto_proposer.generate_proposals(
        provider=provider, existing_families=families, focus=focus,
    )
    return {"ok": True, "job": job, "generation": generation}


def _identity(result: Any) -> dict[str, Any]:
    """The run's ids, from where the pipeline actually puts them.

    ``run_task`` surfaces the task and trace ids on the received-task record, not at the top
    level, so reading ``result["task_id"]`` returned None on every reply this door ever sent.
    Two callers need them for real: the scheduler closes its task-registry entry with both,
    and the assistant's idempotent replay names the run by them. (The read door's ``result``
    resolves registry ids only, and this worker writes no registry entry — door API v2.)
    """
    if not isinstance(result, dict):
        return {}
    records = result.get("records")
    if not isinstance(records, dict):
        return {}
    received = records.get("received_task")
    if not isinstance(received, dict):
        return {}
    identity = received.get("identity")
    return identity if isinstance(identity, dict) else {}


def _source_ref(reason: str, profile: dict[str, str]) -> str:
    """The reason, recorded on the task's source, stamped per the caller's profile.

    The assistant's keeps the `dispatch` tag from before the split so its ledger attribution
    reads identically across it; the scheduler states its own ref verbatim
    (``scheduler:<schedule_id>``), which is the string its in-process runs recorded.
    """
    return profile["source_ref_format"].format(reason=reason)[:_MAX_REASON_ON_SOURCE]


def open_door(
    path: Path,
    *,
    control_store: ControlStore,
    ledger: LedgerStore,
    working_memory: WorkingMemoryStore | None = None,
    programization: ProgramizationStore | None = None,
    resolve_providers: ProviderSelector | None = None,
    independent_validation: bool | str = False,
    revise: bool = False,
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
            independent_validation=independent_validation,
            revise=revise,
        )

    return socket_door.SocketDoor(
        path, _apply, max_concurrent_requests=MAX_CONCURRENT_REQUESTS,
        max_frame_bytes=MAX_FRAME_BYTES,
    )
