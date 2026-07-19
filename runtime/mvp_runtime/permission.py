"""R2.2 PermissionDecision (governance step).

Build an immutable ``permission_decision.v0.3`` record for a bound task and have
governance judge it. The MVP mints two ALLOW-tier actions: the specialist's internal
analysis (``INTERNAL_ANALYSIS``) and — for R3 — a read-only web search
(``INTERNAL_READ``). Each record is validated twice — against the closed schema and
against the canonical Governance Policy semantics (``validate_permission_record``) — and
any issue fails closed. ALLOW is never an executor token: ``runtime_effect`` stays
REVIEW_ONLY with every grant flag false.

The two actions differ only in their action-identity fields (scope, action_type,
target, tool, data scope, parameters) and human-readable reasons; the governance
evaluation, authority invariant, and REVIEW_ONLY guarantee are identical and handled
once. A read-only search is modelled as an ``INTERNAL_READ`` ALLOW action at P1 (READ) —
NOT a ``tool_request`` (that contract is an executor-handoff review packet, the wrong
shape for an internal read).

Reuses scripts helpers in-process: ``compute_action_fingerprint`` (action identity),
``validate_permission_record`` / ``scope_policy_map`` / ``POLICY_BINDING`` (governance
evaluation). scripts/ is added to sys.path (localized bridge) as in binding.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import yaml

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from . import schema_cache
from . import timeutil
from .authority import authority_invariant_holds, permission_decision_runtime_effect, rank_of
from .errors import PlannerBlocked
from .paths import repo_root as _repo_root
from .tools import SEARCH_TOOL_ID
from .workspace import WORKSPACE_REL, WRITE_TOOL_ID

from . import _scripts_bridge  # noqa: F401  (side effect: scripts/ on sys.path, once)

from lib.action_fingerprint import compute_action_fingerprint  # noqa: E402
from validate_permission_approval_contracts import (  # noqa: E402
    POLICY_BINDING,
    scope_policy_map,
    validate_permission_record,
)

PERMISSION_DECISION_SCHEMA_VERSION = "permission_decision.v0.3"
MVP_TTL_MINUTES = 30

# Governance scope + least-privilege authority level for the R3 read-only search action.
SEARCH_PERMISSION_SCOPE = "INTERNAL_READ"
SEARCH_REQUIRED_PERMISSION_LEVEL = "P1"  # READ — a read-only lookup, one level below ANALYZE

# Governance scope + level for the R7 independent validation action. A distinct ALLOW-tier
# scope (SIMULATION_VALIDATION) both matches the action semantically and keeps the validator's
# permission_decision_id distinct from the specialist's (the id seed includes the scope).
VALIDATION_PERMISSION_SCOPE = "SIMULATION_VALIDATION"
VALIDATION_REQUIRED_PERMISSION_LEVEL = "P2"  # ANALYZE — read-only review of an internal output

# Governance scope + level for the R8 controlled write. The Governance Policy already
# prices WORKSPACE_REVERSIBLE_WRITE at EXECUTE_AND_REPORT, making this the runtime's first
# non-ALLOW action. P3 CREATE is the honest level for creating a new file: the policy's
# authority ladder is P3 CREATE / P4 INTERNAL_MODIFY, so create-only stays at P3 and within
# the specialist's ceiling. Modifying an existing file would be P4 — above that ceiling,
# and not implemented (see workspace.py: writes are create-only).
WRITE_PERMISSION_SCOPE = "WORKSPACE_REVERSIBLE_WRITE"
WRITE_REQUIRED_PERMISSION_LEVEL = "P3"  # CREATE — creates a new internal artifact

# Governance scope + level for the R9 memory-promotion action — the runtime's first
# APPROVAL_REQUIRED action. Promotion changes persistent VALIDATED memory, which Prime's
# conditional P4 explicitly excludes (THOMAS_PRIME_CHARTER §10: "Active Core, Policy,
# Validated Memory를 변경하지 않는다"), which is exactly why it needs Thomas. Prime's
# authority here is the authority to *prepare* the request for review; the decision is
# Thomas's. Mirrors examples/permission/permission_approval_required_v0.3.yaml.
MEMORY_PROMOTION_PERMISSION_SCOPE = "SENSITIVE_MEMORY_GOVERNANCE"
MEMORY_PROMOTION_REQUIRED_PERMISSION_LEVEL = "P4"  # INTERNAL_MODIFY — mutates validated memory

EXECUTE_AND_REPORT = "EXECUTE_AND_REPORT"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
# Dispositions the MVP can ACT on: it has an implementation and a reporting path for each.
_EXECUTABLE_DISPOSITIONS = frozenset({"ALLOW", EXECUTE_AND_REPORT})
# Dispositions a RECORD may be built for. Building an APPROVAL_REQUIRED decision is not
# acting on it — the record is REVIEW_ONLY evidence that states an action needs Thomas, and
# it is the object an Approval Request binds to. Building the decision still performs nothing:
# an APPROVAL_REQUIRED action executes only when its APPROVED approval is later *consumed*
# (R10), a separate step gated behind the `approval_consumption` safety flag (see
# consumption.py) — never as a side effect of the decision. BLOCK stays unbuildable: a BLOCK
# means do not, and there is nothing to record a request against.
_BUILDABLE_DISPOSITIONS = frozenset({"ALLOW", EXECUTE_AND_REPORT, APPROVAL_REQUIRED})
# The EXECUTE_AND_REPORT scopes the MVP actually implements. Kept as an explicit allowlist
# so widening the disposition gate does not silently admit the other scopes governance
# prices at EXECUTE_AND_REPORT (GIT_AGENT_BRANCH_CHANGE, LOCAL_BUILD_TEST, ...).
_EXECUTE_AND_REPORT_SCOPES = frozenset({WRITE_PERMISSION_SCOPE})
# Likewise for APPROVAL_REQUIRED: only the scope the runtime can actually ask about. The
# other APPROVAL_REQUIRED scopes (PUBLICATION, EXTERNAL_COMMUNICATION, FINANCIAL_*, ...)
# name actions the runtime has no implementation for, so a request record for one would
# assert an ask it could never honour. Refuse rather than record a fiction.
_APPROVAL_REQUIRED_SCOPES = frozenset({MEMORY_PROMOTION_PERMISSION_SCOPE})


@dataclass(frozen=True)
class _ActionSpec:
    """The action-identity fields + reasons that distinguish one ALLOW action from
    another. Everything else about a PermissionDecision (governance evaluation, authority
    invariant, REVIEW_ONLY effect) is action-independent and built once."""

    action_type: str
    target_suffix: str            # target_ref = f"internal:{task_id}:{target_suffix}"
    tool_id: str | None
    data_scope: tuple[str, ...]
    normalized_parameters: dict[str, Any]
    risk_reason: str
    authority_reason: str
    decision_reason: str
    constraint: str
    # An action on something that is not a task-internal artifact names its own target
    # (e.g. a memory candidate) instead of the internal:{task_id}:{suffix} form.
    target_ref: str | None = None
    # The exact content the action is bound to, when it has one. Part of the action
    # fingerprint, so the approved action cannot be swapped for a different payload.
    content_sha256: str | None = None
    # GREEN suits internal read/analysis work; an action needing Thomas is not GREEN.
    risk_level: str = "GREEN"


_ANALYSIS_ACTION = _ActionSpec(
    action_type="internal.analysis.create",
    target_suffix="analysis",
    tool_id=None,
    data_scope=("assigned.context", "task.request"),
    normalized_parameters={"output_format": "structured_markdown", "visibility": "internal"},
    risk_reason="Internal-only analysis with no external, financial, or runtime effect.",
    authority_reason="Internal analysis within the assigned Task scope and authority ceiling.",
    decision_reason="Authority is sufficient and the exact action is internal and reversible.",
    constraint="No external publication, tool/program execution, or runtime mutation.",
)

_SEARCH_ACTION = _ActionSpec(
    action_type="internal.read.search",
    target_suffix="search",
    tool_id=SEARCH_TOOL_ID,
    data_scope=("task.request", "web.public.read"),
    normalized_parameters={"result_scope": "web.public", "visibility": "internal"},
    risk_reason="Read-only public web search; no external write, publication, financial, or runtime effect.",
    authority_reason="Read-only information lookup within the assigned Task scope and authority ceiling.",
    decision_reason="Authority is sufficient and the search is a reversible, read-only information lookup.",
    constraint="Read-only search; no external write, publication, tool/program execution, or runtime mutation.",
)

_VALIDATION_ACTION = _ActionSpec(
    action_type="internal.validation.review",
    target_suffix="validation",
    tool_id=None,
    data_scope=("task.request", "agent_output.review"),
    normalized_parameters={"review_target": "agent_output", "visibility": "internal"},
    risk_reason="Read-only independent review of an internal output; no external, financial, or runtime effect.",
    authority_reason="Independent validation within the assigned Task scope and the validator role's ceiling.",
    decision_reason="Authority is sufficient and the review is a read-only assessment of an internal artifact.",
    constraint="Review only; the validator never modifies the original output and grants nothing.",
)


_WRITE_ACTION = _ActionSpec(
    action_type="workspace.file.create",
    target_suffix="workspace_write",
    tool_id=WRITE_TOOL_ID,
    data_scope=("task.request", "workspace.internal"),
    normalized_parameters={"write_mode": "create_only", "workspace_root": WORKSPACE_REL, "visibility": "internal"},
    risk_reason="Create-only write into the approved internal workspace; no external, financial, or runtime effect.",
    authority_reason="Creating a new internal artifact within the assigned Task scope and authority ceiling.",
    decision_reason=(
        "Authority is sufficient and the write is confined, create-only, and reversible by "
        "deleting the created file; the outcome is reported."
    ),
    constraint=(
        "Create-only inside the approved workspace; never overwrites or deletes, and performs "
        "no publication, tool/program execution, or runtime mutation."
    ),
)


def _parse_ts(value: str) -> datetime:
    try:
        return timeutil.parse_iso(value)
    except (AttributeError, ValueError) as exc:
        raise PlannerBlocked("INVALID_TIMESTAMP", f"now is not a valid RFC3339 date-time: {value!r}") from exc


def build_permission_decision(
    bound_task: Mapping[str, Any],
    *,
    permission_scope: str,
    required_permission_level: str,
    role_permission_ceiling: str,
    now: str,
    actor_id: str = "thomas.prime",
    ttl_minutes: int = MVP_TTL_MINUTES,
    repo_root: Path | None = None,
    action: "_ActionSpec | None" = None,
    approval_id: str | None = None,
) -> dict[str, Any]:
    """Build and fully validate a permission_decision.v0.3 for a bound task.

    ``action`` selects the action-identity fields + reasons (defaults to the internal
    analysis action). Fails closed (``PlannerBlocked``) if the task is unbound, the scope
    disposition is unknown, the authority invariant fails, the record violates the schema
    or the Governance Policy semantics, or the disposition is not ALLOW (the MVP only
    performs ALLOW-tier actions).
    """
    action = action if action is not None else _ANALYSIS_ACTION
    root = repo_root if repo_root is not None else _repo_root()
    identity = bound_task.get("identity", {})
    context = bound_task.get("context", {})
    task_id = identity.get("task_id")
    trace_id = identity.get("trace_id")
    revision = identity.get("task_revision")
    ccb = context.get("core_context_binding_id")
    if not (isinstance(ccb, str) and ccb.startswith("ccb-")):
        raise PlannerBlocked("NOT_BOUND", "task must be bound to a Core Release before a permission decision")

    if rank_of(required_permission_level) is None or rank_of(role_permission_ceiling) is None:
        raise PlannerBlocked("INVALID_LEVEL", "required level / role ceiling must be P0..P6")

    # Load the canonical Governance Policy and map the action scope to its disposition.
    try:
        policy = yaml.safe_load((root / "governance" / "GOVERNANCE_POLICY.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PlannerBlocked("POLICY_UNAVAILABLE", f"cannot load governance policy: {exc}") from exc
    disposition = scope_policy_map(policy).get(permission_scope)
    if disposition is None:
        raise PlannerBlocked("UNKNOWN_SCOPE", f"permission_scope {permission_scope!r} has no policy disposition")
    # Fail closed explicitly on anything the MVP cannot perform, before building the
    # record — do not rely on a downstream schema conditional to reject it. The MVP
    # performs ALLOW actions and, since R8, the one EXECUTE_AND_REPORT action it has an
    # implementation and a reporting path for (see _EXECUTABLE_DISPOSITIONS). Everything
    # stricter — APPROVAL_REQUIRED, BLOCK — stays refused: the MVP has no approval flow,
    # so an APPROVAL_REQUIRED action has no way to become authorized and must not proceed.
    if disposition not in _BUILDABLE_DISPOSITIONS:
        raise PlannerBlocked(
            "NOT_ALLOWED",
            f"MVP cannot build a {disposition} decision; scope {permission_scope} disposition is {disposition}",
        )
    if disposition == EXECUTE_AND_REPORT and permission_scope not in _EXECUTE_AND_REPORT_SCOPES:
        # An EXECUTE_AND_REPORT scope the runtime has no implementation for (e.g.
        # GIT_AGENT_BRANCH_CHANGE, LOCAL_BUILD_TEST) must not ride in on R8's widening.
        raise PlannerBlocked(
            "NOT_ALLOWED",
            f"scope {permission_scope} is EXECUTE_AND_REPORT but the MVP implements no such action",
        )
    if disposition == APPROVAL_REQUIRED and permission_scope not in _APPROVAL_REQUIRED_SCOPES:
        # An APPROVAL_REQUIRED scope the runtime cannot perform even once approved must not
        # ride in on R9's widening — it could only ever produce an ask it can't honour.
        raise PlannerBlocked(
            "NOT_ALLOWED",
            f"scope {permission_scope} is APPROVAL_REQUIRED but the MVP implements no such action",
        )

    created = _parse_ts(now)
    expires_at = timeutil.format_iso(created + timedelta(minutes=ttl_minutes))
    created_at = timeutil.format_iso(created)

    # Least privilege: required == effective == granted == the action's need; the role
    # ceiling is the upper bound the grant stays within.
    effective_level = required_permission_level
    granted_level = required_permission_level
    authority_sufficient = authority_invariant_holds(
        required_permission_level, effective_level, granted_level, role_permission_ceiling
    )
    if not authority_sufficient:
        raise PlannerBlocked(
            "AUTHORITY_INSUFFICIENT",
            f"authority invariant fails: required {required_permission_level} exceeds role ceiling {role_permission_ceiling}",
        )

    requester_ref = f"thomas_prime:{actor_id}"
    fingerprint_payload = {
        "schema_version": "action_fingerprint_payload.v0.1",
        "task_id": task_id,
        "task_revision": revision,
        "core_context_binding_id": ccb,
        "requester_ref": requester_ref,
        "permission_scope": permission_scope,
        "action_type": action.action_type,
        "target_ref": action.target_ref or f"internal:{task_id}:{action.target_suffix}",
        "tool_id": action.tool_id,
        "program_id": None,
        "data_scope": list(action.data_scope),
        "content_sha256": action.content_sha256,
        "amount_decimal": None,
        "currency": None,
        "normalized_parameters": dict(action.normalized_parameters),
        "expires_at": expires_at,
    }
    try:
        action_fingerprint = compute_action_fingerprint(fingerprint_payload)
    except ValueError as exc:
        raise PlannerBlocked("FINGERPRINT_FAILED", str(exc)) from exc

    # The Approval an APPROVAL_REQUIRED decision waits on is identified by the action
    # itself: derive its id from the action fingerprint so that ANY material change to the
    # action yields a different approval_id, exactly as the Governance Policy requires
    # (`action_identity.invalidated_by_any_material_field_change`). Deriving it here rather
    # than taking it from the caller also removes the footgun of pointing a decision at a
    # reused or unrelated approval.
    if disposition == APPROVAL_REQUIRED and approval_id is None:
        approval_id = integrity.short_id("approval", {"action_fingerprint": action_fingerprint})

    decision = disposition  # exactly the policy minimum for this scope
    permdec_id = integrity.short_id(
        "permdec",
        {"task_id": task_id, "task_revision": revision, "ccb": ccb, "scope": permission_scope, "expires_at": expires_at},
    )

    record: dict[str, Any] = {
        "schema_version": PERMISSION_DECISION_SCHEMA_VERSION,
        "permission_decision_id": permdec_id,
        "trace_id": trace_id,
        "task_id": task_id,
        "task_revision": revision,
        "core_context_binding_id": ccb,
        "operating_policy": dict(POLICY_BINDING),
        "requested_by": {
            "actor_type": "thomas_prime",
            "actor_id": actor_id,
            "role_id": None,
            "role_version": None,
            "assignment_id": None,
        },
        "fingerprint_payload": fingerprint_payload,
        "action_fingerprint": action_fingerprint,
        "authority": {
            "required_permission_level": required_permission_level,
            "role_permission_ceiling": role_permission_ceiling,
            "assignment_granted_permission_level": granted_level,
            "effective_permission_level": effective_level,
            "authority_sufficient": bool(authority_sufficient),
            "authority_reasons": [action.authority_reason],
        },
        "risk": {
            "risk_level": action.risk_level,
            "risk_reasons": [action.risk_reason],
            "policy_disposition": disposition,
        },
        "decision": {
            "permission_decision": decision,
            "decision_reasons": [action.decision_reason],
            "constraints": [action.constraint],
        },
        # An APPROVAL_REQUIRED decision names the Approval it waits on and starts PENDING;
        # everything else carries no approval state at all (the schema enforces both).
        "approval": (
            {"approval_required": True, "approval_id": approval_id, "approval_status": "PENDING"}
            if disposition == APPROVAL_REQUIRED
            else {"approval_required": False, "approval_id": None, "approval_status": "NOT_REQUIRED"}
        ),
        "runtime_effect": permission_decision_runtime_effect(),
        "lifecycle": {
            "decision_status": "ACTIVE",
            "created_at": created_at,
            "expires_at": expires_at,
            "supersedes": [],
        },
        "audit_refs": [f"audit:permission:{permdec_id}"],
    }

    # Closed-schema validation, then canonical Governance Policy semantics.
    schema_path = root / "schemas" / f"{PERMISSION_DECISION_SCHEMA_VERSION}.schema.json"
    try:
        schema_cache.validate_against_schema(record, schema_path, "permission_decision")
    except RuntimeSchemaError as exc:
        raise PlannerBlocked("PERMISSION_SCHEMA_INVALID", str(exc)) from exc
    issues = validate_permission_record(record, policy)
    if issues:
        raise PlannerBlocked("PERMISSION_SEMANTICS_INVALID", "; ".join(issues[:5]))
    return record


def build_search_permission_decision(
    bound_task: Mapping[str, Any],
    *,
    role_permission_ceiling: str,
    now: str,
    actor_id: str = "thomas.prime",
    ttl_minutes: int = MVP_TTL_MINUTES,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the ALLOW PermissionDecision for the R3 read-only web search.

    A thin wrapper over :func:`build_permission_decision` fixing the search scope
    (``INTERNAL_READ``), least-privilege level (P1 READ), and the search action spec.
    Fails closed identically. Used by the pipeline to authorize the search before the
    specialist may use the (gated) search tool."""
    return build_permission_decision(
        bound_task,
        permission_scope=SEARCH_PERMISSION_SCOPE,
        required_permission_level=SEARCH_REQUIRED_PERMISSION_LEVEL,
        role_permission_ceiling=role_permission_ceiling,
        now=now,
        actor_id=actor_id,
        ttl_minutes=ttl_minutes,
        repo_root=repo_root,
        action=_SEARCH_ACTION,
    )


def build_validation_permission_decision(
    bound_task: Mapping[str, Any],
    *,
    role_permission_ceiling: str,
    now: str,
    actor_id: str = "thomas.prime",
    ttl_minutes: int = MVP_TTL_MINUTES,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the ALLOW PermissionDecision for the R7 independent validation review.

    A thin wrapper over :func:`build_permission_decision` fixing the validation scope
    (``SIMULATION_VALIDATION``), the least-privilege level (P2 ANALYZE), and the review
    action spec. Fails closed identically. The validator agent acts under this decision,
    separate from the specialist's analysis grant."""
    return build_permission_decision(
        bound_task,
        permission_scope=VALIDATION_PERMISSION_SCOPE,
        required_permission_level=VALIDATION_REQUIRED_PERMISSION_LEVEL,
        role_permission_ceiling=role_permission_ceiling,
        now=now,
        actor_id=actor_id,
        ttl_minutes=ttl_minutes,
        repo_root=repo_root,
        action=_VALIDATION_ACTION,
    )


def build_memory_promotion_permission_decision(
    bound_task: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    role_permission_ceiling: str = MEMORY_PROMOTION_REQUIRED_PERMISSION_LEVEL,
    now: str,
    approval_id: str | None = None,
    actor_id: str = "thomas.prime",
    ttl_minutes: int = MVP_TTL_MINUTES,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the APPROVAL_REQUIRED PermissionDecision for promoting one working-memory
    candidate to VALIDATED — the runtime's first action that Thomas must decide.

    The action is bound to the exact candidate: its id is the target, and its content hash
    is in the fingerprint, so an approval of *this* promotion cannot be re-pointed at
    different content (any material change invalidates the fingerprint, per the Governance
    Policy's `action_identity.invalidated_by_any_material_field_change`).

    Building this record does not promote anything and never can: the decision is
    APPROVAL_REQUIRED, and approval consumption is gate-pinned unimplemented. Promotion
    remains what R5.3 made it — an explicit operator action.
    """
    candidate_id = candidate.get("candidate_id")
    content = candidate.get("content")
    if not (isinstance(candidate_id, str) and candidate_id):
        raise PlannerBlocked("INVALID_CANDIDATE", "candidate must carry a candidate_id")
    if not (isinstance(content, str) and content.strip()):
        raise PlannerBlocked("INVALID_CANDIDATE", "candidate must carry content to promote")

    action = _ActionSpec(
        action_type="memory.validated.promote",
        target_suffix="memory_promotion",
        tool_id=None,
        data_scope=(f"memory.candidate.{candidate_id}", "task.evidence"),
        normalized_parameters={"target_scope": "validated_memory", "promotion_mode": "manual_review"},
        risk_reason="Promotion changes persistent validated organizational memory.",
        authority_reason="Prime may prepare a validated-memory promotion for Thomas review.",
        decision_reason="Persistent validated-memory promotion requires exact Thomas approval.",
        constraint="Approval is review-only and cannot create executor handoff.",
        target_ref=f"memory_candidate:{candidate_id}",
        content_sha256=integrity.sha256_record({"content": content}),
        risk_level="ORANGE",
    )
    return build_permission_decision(
        bound_task,
        permission_scope=MEMORY_PROMOTION_PERMISSION_SCOPE,
        required_permission_level=MEMORY_PROMOTION_REQUIRED_PERMISSION_LEVEL,
        role_permission_ceiling=role_permission_ceiling,
        now=now,
        actor_id=actor_id,
        ttl_minutes=ttl_minutes,
        repo_root=repo_root,
        action=action,
        approval_id=approval_id,
    )


def build_write_permission_decision(
    bound_task: Mapping[str, Any],
    *,
    role_permission_ceiling: str,
    now: str,
    actor_id: str = "thomas.prime",
    ttl_minutes: int = MVP_TTL_MINUTES,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the EXECUTE_AND_REPORT PermissionDecision for the R8 controlled write.

    A thin wrapper over :func:`build_permission_decision` fixing the write scope
    (``WORKSPACE_REVERSIBLE_WRITE``), the least-privilege level (P3 CREATE), and the write
    action spec. Fails closed identically — including on authority: a role whose ceiling is
    below P3 cannot obtain this grant.

    Unlike its siblings this decision is **EXECUTE_AND_REPORT, not ALLOW** — the runtime's
    first. The disposition comes from the canonical Governance Policy, not from here; the
    caller owes the "report" half (audit + operator report), which the pipeline provides."""
    return build_permission_decision(
        bound_task,
        permission_scope=WRITE_PERMISSION_SCOPE,
        required_permission_level=WRITE_REQUIRED_PERMISSION_LEVEL,
        role_permission_ceiling=role_permission_ceiling,
        now=now,
        actor_id=actor_id,
        ttl_minutes=ttl_minutes,
        repo_root=repo_root,
        action=_WRITE_ACTION,
    )
