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

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import yaml

from runtime.read_only_kernel import integrity, schema_validation
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .authority import authority_invariant_holds, permission_decision_runtime_effect, rank_of
from .errors import PlannerBlocked
from .tools import SEARCH_TOOL_ID

_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parse_ts(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise PlannerBlocked("INVALID_TIMESTAMP", f"now is not a valid RFC3339 date-time: {value!r}") from exc


def _fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


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
    # record — do not rely on a downstream schema conditional to reject it.
    if disposition != "ALLOW":
        raise PlannerBlocked(
            "NOT_ALLOWED",
            f"MVP only performs ALLOW-tier actions; scope {permission_scope} disposition is {disposition}",
        )

    created = _parse_ts(now)
    expires_at = _fmt_ts(created + timedelta(minutes=ttl_minutes))
    created_at = _fmt_ts(created)

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
        "target_ref": f"internal:{task_id}:{action.target_suffix}",
        "tool_id": action.tool_id,
        "program_id": None,
        "data_scope": list(action.data_scope),
        "content_sha256": None,
        "amount_decimal": None,
        "currency": None,
        "normalized_parameters": dict(action.normalized_parameters),
        "expires_at": expires_at,
    }
    try:
        action_fingerprint = compute_action_fingerprint(fingerprint_payload)
    except ValueError as exc:
        raise PlannerBlocked("FINGERPRINT_FAILED", str(exc)) from exc

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
            "risk_level": "GREEN",
            "risk_reasons": [action.risk_reason],
            "policy_disposition": disposition,
        },
        "decision": {
            "permission_decision": decision,
            "decision_reasons": [action.decision_reason],
            "constraints": [action.constraint],
        },
        "approval": {
            "approval_required": False,
            "approval_id": None,
            "approval_status": "NOT_REQUIRED",
        },
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
        schema_validation.validate_against_schema(record, schema_path, "permission_decision")
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
