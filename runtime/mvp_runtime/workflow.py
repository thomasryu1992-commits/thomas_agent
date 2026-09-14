"""Workflow model — plans, steps, attempts and their lifecycles, as pure functions.

Sequence 2, P03 (``docs/HERMES_ORCHESTRATOR_ARCHITECTURE_V0.2.md`` §3–§4). Hermes proposes a
plan; this module says whether it is one the runtime may hold — its closed schema
(``schemas/workflow_plan.v0.1.schema.json``), a DAG over known steps, capabilities from the
closed set the dispatch door already serves — and states the transitions a workflow, a step and
an attempt may make. It performs no I/O, calls no model and imports no domain package; the
store (``workflow_store.py``) persists what this validates and the manager loop (P04) drives it.

**What a plan cannot say.** The schema is closed: a plan names no effect class, no actor and no
permission. ``effect_class`` is a property of the capability (:data:`CAPABILITIES`), the
principal is the door's socket peer, and the Role is Prime's choice at execution. The 2026-09-14
review's rule is that external data never overwrites capability, actor or authority fields.

**Forward-only lifecycles.** Like the task registry's, every edge is in a table and anything
else is ``TRANSITION_INVALID`` — the value of a coordination record is that its statuses can be
believed. A completed attempt is never re-opened: a retry is a new attempt, and
``attempt_id`` is the fence that keeps a late result from the old one out (V0.2 Q22).

**Effect classes.** ``none`` — a model call plus reads and workspace writes, whose double
execution costs money and nothing else; every capability of the first sequence. ``external``
— approval consumption, publishing, an order: not executed by a workflow at all yet, and the
one class for which an expired attempt is *reconciled* rather than retried (V0.2 Q21).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from . import schema_cache
from .errors import WorkflowBlocked
from .paths import repo_root as _repo_root

PLAN_SCHEMA_VERSION = "workflow_plan.v0.1"
EVENT_SCHEMA_VERSION = "workflow_event.v0.1"

EFFECT_NONE = "none"
EFFECT_EXTERNAL = "external"
EFFECT_CLASSES = frozenset({EFFECT_NONE, EFFECT_EXTERNAL})


@dataclass(frozen=True)
class Capability:
    kind: str
    effect_class: str


# The closed set a plan may name — exactly the dispatch door's four kinds; a test pins the two
# sets equal so neither can drift from the other. The effect class belongs to the capability,
# never to the request.
CAPABILITIES: dict[str, Capability] = {
    "analysis": Capability("analysis", EFFECT_NONE),
    "research": Capability("research", EFFECT_NONE),
    "translation": Capability("translation", EFFECT_NONE),
    "content": Capability("content", EFFECT_NONE),
}

# Design starting points (V0.2 §4.3), confirmed by load tests before the pilot.
MAX_STEPS = 10
MAX_ATTEMPTS_PER_STEP = 3
DEFAULT_MAX_ATTEMPTS = 1
MAX_NAVER_KEYWORDS_CHARS = 200

# --- workflow lifecycle ----------------------------------------------------------------------
W_RECEIVED = "RECEIVED"
W_VALIDATED = "VALIDATED"
W_RUNNING = "RUNNING"
W_WAITING_APPROVAL = "WAITING_APPROVAL"
W_WAITING_REPLAN = "WAITING_REPLAN"
W_CANCELLING = "CANCELLING"
W_COMPLETED = "COMPLETED"
W_FAILED = "FAILED"
W_BLOCKED = "BLOCKED"
W_CANCELLED = "CANCELLED"
WORKFLOW_TERMINAL = frozenset({W_COMPLETED, W_FAILED, W_BLOCKED, W_CANCELLED})
WORKFLOW_TRANSITIONS: dict[str, frozenset[str]] = {
    W_RECEIVED: frozenset({W_VALIDATED, W_BLOCKED}),
    W_VALIDATED: frozenset({W_RUNNING, W_CANCELLING, W_CANCELLED, W_BLOCKED}),
    W_RUNNING: frozenset({W_WAITING_APPROVAL, W_WAITING_REPLAN, W_CANCELLING,
                          W_COMPLETED, W_FAILED, W_BLOCKED}),
    W_WAITING_APPROVAL: frozenset({W_RUNNING, W_CANCELLING, W_CANCELLED, W_BLOCKED, W_FAILED}),
    W_WAITING_REPLAN: frozenset({W_RUNNING, W_CANCELLING, W_CANCELLED, W_BLOCKED, W_FAILED}),
    # A last in-flight attempt may still land while cancelling; the workflow honours it.
    W_CANCELLING: frozenset({W_CANCELLED, W_COMPLETED, W_FAILED, W_BLOCKED}),
    W_COMPLETED: frozenset(),
    W_FAILED: frozenset(),
    W_BLOCKED: frozenset(),
    W_CANCELLED: frozenset(),
}

# --- step lifecycle --------------------------------------------------------------------------
S_PENDING = "PENDING"
S_READY = "READY"
S_RUNNING = "RUNNING"
S_SUCCEEDED = "SUCCEEDED"
S_RETRY_WAIT = "RETRY_WAIT"
S_WAITING_APPROVAL = "WAITING_APPROVAL"
S_NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"
S_FAILED = "FAILED"
S_BLOCKED = "BLOCKED"
S_CANCEL_REQUESTED = "CANCEL_REQUESTED"
S_CANCELLED = "CANCELLED"
STEP_TERMINAL = frozenset({S_SUCCEEDED, S_FAILED, S_BLOCKED, S_CANCELLED})
STEP_TRANSITIONS: dict[str, frozenset[str]] = {
    S_PENDING: frozenset({S_READY, S_CANCELLED, S_BLOCKED}),
    S_READY: frozenset({S_RUNNING, S_CANCELLED, S_BLOCKED}),
    S_RUNNING: frozenset({S_SUCCEEDED, S_RETRY_WAIT, S_FAILED, S_BLOCKED,
                          S_NEEDS_RECONCILIATION, S_WAITING_APPROVAL, S_CANCEL_REQUESTED}),
    S_RETRY_WAIT: frozenset({S_READY, S_CANCELLED, S_BLOCKED, S_FAILED}),
    S_WAITING_APPROVAL: frozenset({S_READY, S_CANCELLED, S_BLOCKED, S_FAILED}),
    S_NEEDS_RECONCILIATION: frozenset({S_SUCCEEDED, S_FAILED, S_READY, S_CANCELLED, S_BLOCKED}),
    S_CANCEL_REQUESTED: frozenset({S_CANCELLED, S_SUCCEEDED, S_FAILED}),
    S_SUCCEEDED: frozenset(),
    S_FAILED: frozenset(),
    S_BLOCKED: frozenset(),
    S_CANCELLED: frozenset(),
}
# The step statuses that still owe the workflow work.
STEP_OPEN = frozenset({S_PENDING, S_READY, S_RUNNING, S_RETRY_WAIT, S_WAITING_APPROVAL,
                       S_NEEDS_RECONCILIATION, S_CANCEL_REQUESTED})

# --- attempt lifecycle -----------------------------------------------------------------------
A_RUNNING = "RUNNING"
A_SUCCEEDED = "SUCCEEDED"
A_FAILED = "FAILED"
A_EXPIRED = "EXPIRED"
A_CANCELLED = "CANCELLED"
ATTEMPT_TERMINAL = frozenset({A_SUCCEEDED, A_FAILED, A_EXPIRED, A_CANCELLED})
ATTEMPT_TRANSITIONS: dict[str, frozenset[str]] = {
    A_RUNNING: frozenset({A_SUCCEEDED, A_FAILED, A_EXPIRED, A_CANCELLED}),
    A_SUCCEEDED: frozenset(),
    A_FAILED: frozenset(),
    A_EXPIRED: frozenset(),
    A_CANCELLED: frozenset(),
}

_TABLES = {"workflow": WORKFLOW_TRANSITIONS, "step": STEP_TRANSITIONS, "attempt": ATTEMPT_TRANSITIONS}


def assert_transition(entity: str, current: str, target: str, ident: str) -> None:
    """Refuse any edge the lifecycle table does not name. ``entity`` is workflow/step/attempt."""
    table = _TABLES[entity]
    if current not in table:
        raise WorkflowBlocked("TRANSITION_INVALID", f"{entity} {ident} has unknown status {current!r}")
    if target not in table[current]:
        raise WorkflowBlocked(
            "TRANSITION_INVALID",
            f"{entity} {ident} is {current}; {current} -> {target} is not a legal transition "
            "(the lifecycle is forward-only)",
        )


# --- the validated plan ----------------------------------------------------------------------

@dataclass(frozen=True)
class PlanStep:
    key: str
    capability: str
    effect_class: str
    request: str
    reason: str
    depends_on: tuple[str, ...]
    input_refs: tuple[str, ...]
    naver_keywords: str | None
    max_attempts: int
    options: dict[str, bool] = field(default_factory=dict)

    @property
    def calls_per_attempt(self) -> int:
        """Model calls one attempt may spend: the specialist, plus the independent reviewer and
        the one bounded revision when the step asked for them. What the budget reserves."""
        return 1 + int(bool(self.options.get("independent_validation"))) + int(bool(self.options.get("revise")))


@dataclass(frozen=True)
class PlanBudget:
    max_model_calls: int
    max_tokens: int | None


@dataclass(frozen=True)
class ValidatedPlan:
    goal: str
    steps: tuple[PlanStep, ...]
    order: tuple[str, ...]       # a topological order of the step keys
    budget: PlanBudget
    plan_hash: str
    plan: dict[str, Any]         # the plan as submitted, for the store's plan_versions row

    def step(self, key: str) -> PlanStep:
        for step in self.steps:
            if step.key == key:
                return step
        raise KeyError(key)


def plan_hash(plan: Mapping[str, Any]) -> str:
    """The plan's identity — every field, canonical bytes. The same plan re-submitted under the
    same request id replays; a different plan under a spent id is a conflict."""
    try:
        return integrity.sha256_record(dict(plan))
    except Exception as exc:  # IntegrityError or an unserialisable value
        raise WorkflowBlocked("PLAN_INVALID", f"the plan cannot be hashed: {exc}") from exc


def validate_plan(plan: Any) -> ValidatedPlan:
    """The plan the runtime may hold, or a typed refusal — before anything is written.

    Schema first (closed: an unknown key, an effect class or an actor field is refused there),
    then what a schema cannot say: distinct step keys, dependencies on steps that exist, input
    references that are dependencies, no self-dependency, no cycle, and the capability in the
    closed set. Acceptance A01: every refusal here happens before a row exists.
    """
    if not isinstance(plan, Mapping):
        raise WorkflowBlocked("PLAN_INVALID", "a plan is a mapping")
    record = dict(plan)
    try:
        schema_cache.validate_against_schema(
            record, _repo_root() / "schemas" / f"{PLAN_SCHEMA_VERSION}.schema.json", "workflow_plan",
        )
    except RuntimeSchemaError as exc:
        raise WorkflowBlocked("PLAN_INVALID", str(exc)) from exc

    raw_steps = record["steps"]
    keys = [str(s["id"]) for s in raw_steps]
    if len(set(keys)) != len(keys):
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        raise WorkflowBlocked("PLAN_INVALID", f"duplicate step ids: {dupes}")
    known = set(keys)
    steps: list[PlanStep] = []
    for raw in raw_steps:
        key = str(raw["id"])
        capability = str(raw["capability"])
        cap = CAPABILITIES.get(capability)
        if cap is None:
            raise WorkflowBlocked(
                "CAPABILITY_NOT_PERMITTED",
                f"step {key!r} names capability {capability!r}; a workflow may run {sorted(CAPABILITIES)} only",
            )
        depends_on = tuple(str(d) for d in raw.get("depends_on") or ())
        input_refs = tuple(str(d) for d in raw.get("input_refs") or ())
        unknown = sorted(set(depends_on) - known)
        if unknown:
            raise WorkflowBlocked("PLAN_INVALID", f"step {key!r} depends on unknown steps {unknown}")
        if key in depends_on:
            raise WorkflowBlocked("PLAN_INVALID", f"step {key!r} depends on itself")
        not_deps = sorted(set(input_refs) - set(depends_on))
        if not_deps:
            raise WorkflowBlocked(
                "PLAN_INVALID",
                f"step {key!r} reads the results of {not_deps}, which are not among its dependencies",
            )
        options = {k: bool(v) for k, v in (raw.get("options") or {}).items()}
        keywords = raw.get("naver_keywords")
        keywords = keywords.strip() if isinstance(keywords, str) and keywords.strip() else None
        steps.append(PlanStep(
            key=key, capability=capability, effect_class=cap.effect_class,
            request=str(raw["request"]).strip(), reason=str(raw["reason"]).strip(),
            depends_on=depends_on, input_refs=input_refs, naver_keywords=keywords,
            max_attempts=int(raw.get("max_attempts") or DEFAULT_MAX_ATTEMPTS), options=options,
        ))
        if not steps[-1].request or not steps[-1].reason:
            raise WorkflowBlocked("PLAN_INVALID", f"step {key!r} has a blank request or reason")

    order = topological_order({s.key: s.depends_on for s in steps})
    budget_raw = record["budget"]
    budget = PlanBudget(
        max_model_calls=int(budget_raw["max_model_calls"]),
        max_tokens=int(budget_raw["max_tokens"]) if budget_raw.get("max_tokens") is not None else None,
    )
    needed = sum(s.calls_per_attempt for s in steps)
    if needed > budget.max_model_calls:
        raise WorkflowBlocked(
            "BUDGET_EXHAUSTED",
            f"the plan needs at least {needed} model call(s) for one attempt of every step; "
            f"the budget allows {budget.max_model_calls}",
        )
    return ValidatedPlan(
        goal=str(record["goal"]).strip(), steps=tuple(steps), order=order, budget=budget,
        plan_hash=plan_hash(record), plan=record,
    )


def topological_order(deps: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    """Kahn's algorithm over ``{key: depends_on}``; a cycle is a typed refusal that names the
    steps left in it. Deterministic: ties break on the plan's own key order."""
    remaining = {key: set(value) for key, value in deps.items()}
    order: list[str] = []
    while remaining:
        ready = [key for key, value in remaining.items() if not value]
        if not ready:
            raise WorkflowBlocked(
                "PLAN_INVALID", f"the plan's dependencies form a cycle among {sorted(remaining)}",
            )
        for key in ready:
            order.append(key)
            del remaining[key]
        for value in remaining.values():
            value.difference_update(ready)
    return tuple(order)


def ready_keys(status_by_key: Mapping[str, str], deps: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    """The PENDING steps whose every dependency has SUCCEEDED, in plan order (acceptance A02:
    a failed or unfinished dependency keeps its dependents where they are)."""
    return tuple(
        key for key, status in status_by_key.items()
        if status == S_PENDING and all(status_by_key.get(d) == S_SUCCEEDED for d in deps.get(key, ()))
    )


def workflow_status_for(step_statuses: Iterable[str], *, cancelling: bool) -> str:
    """The workflow status its steps imply, once no step transition is pending.

    Open work keeps the workflow RUNNING (or CANCELLING while a cancel is being honoured); then
    every step SUCCEEDED is COMPLETED; a cancelled step with no failure is CANCELLED; any failure
    is FAILED; otherwise a step was BLOCKED (budget, dependency) and so is the workflow.
    """
    statuses = list(step_statuses)
    if any(s in STEP_OPEN for s in statuses):
        return W_CANCELLING if cancelling else W_RUNNING
    if statuses and all(s == S_SUCCEEDED for s in statuses):
        return W_COMPLETED
    if any(s == S_FAILED for s in statuses):
        return W_FAILED
    if any(s == S_CANCELLED for s in statuses) and not any(s == S_BLOCKED for s in statuses):
        return W_CANCELLED
    if any(s == S_CANCELLED for s in statuses) and cancelling:
        return W_CANCELLED
    return W_BLOCKED


# --- identities ------------------------------------------------------------------------------

def workflow_id_for(principal: str, request_id: str, plan_digest: str, accepted_at: str) -> str:
    return integrity.short_id("wf", {"principal": principal, "request_id": request_id,
                                     "plan_hash": plan_digest, "accepted_at": accepted_at})


def step_id_for(workflow_id: str, key: str) -> str:
    return integrity.short_id("wfs", {"workflow_id": workflow_id, "key": key})


def attempt_id_for(step_id: str, number: int) -> str:
    return integrity.short_id("wfa", {"step_id": step_id, "attempt_number": number})


# --- events ----------------------------------------------------------------------------------

def event_record(
    *, workflow_id: str, entity: str, to_status: str, created_at: str,
    step_id: str | None = None, attempt_id: str | None = None, from_status: str | None = None,
    reason_code: str | None = None, detail: str | None = None,
) -> dict[str, Any]:
    """One coordination event in its closed shape, validated before the store inserts it."""
    record = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "workflow_id": workflow_id, "step_id": step_id, "attempt_id": attempt_id,
        "entity": entity, "from_status": from_status, "to_status": to_status,
        "reason_code": reason_code, "detail": detail[:2000] if isinstance(detail, str) else None,
        "created_at": created_at,
    }
    try:
        schema_cache.validate_against_schema(
            record, _repo_root() / "schemas" / f"{EVENT_SCHEMA_VERSION}.schema.json", "workflow_event",
        )
    except RuntimeSchemaError as exc:
        raise WorkflowBlocked("EVENT_INVALID", str(exc)) from exc
    return record
