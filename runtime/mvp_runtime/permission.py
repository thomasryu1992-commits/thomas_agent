"""R2.2 PermissionDecision (governance step).

Build an immutable ``permission_decision.v0.3`` record for a bound task and have
governance judge it. For the MVP internal-analysis action the disposition is
ALLOW. The record is validated twice — against the closed schema and against the
canonical Governance Policy semantics (``validate_permission_record``) — and any
issue fails closed. ALLOW is never an executor token: ``runtime_effect`` stays
REVIEW_ONLY with every grant flag false.

Reuses scripts helpers in-process: ``compute_action_fingerprint`` (action identity),
``validate_permission_record`` / ``scope_policy_map`` / ``POLICY_BINDING`` (governance
evaluation). scripts/ is added to sys.path (localized bridge) as in binding.py.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import yaml

from runtime.read_only_kernel import integrity, schema_validation
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .errors import PlannerBlocked

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
_LEVEL_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4, "P5": 5, "P6": 6}


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
) -> dict[str, Any]:
    """Build and fully validate a permission_decision.v0.3 for a bound task.

    Fails closed (``PlannerBlocked``) if the task is unbound, the scope disposition
    is unknown, the authority invariant fails, the record violates the schema or the
    Governance Policy semantics, or the disposition is not ALLOW (the MVP only
    performs ALLOW-tier internal analysis).
    """
    root = repo_root if repo_root is not None else _repo_root()
    identity = bound_task.get("identity", {})
    context = bound_task.get("context", {})
    task_id = identity.get("task_id")
    trace_id = identity.get("trace_id")
    revision = identity.get("task_revision")
    ccb = context.get("core_context_binding_id")
    if not (isinstance(ccb, str) and ccb.startswith("ccb-")):
        raise PlannerBlocked("NOT_BOUND", "task must be bound to a Core Release before a permission decision")

    required_rank = _LEVEL_RANK.get(required_permission_level)
    ceiling_rank = _LEVEL_RANK.get(role_permission_ceiling)
    if required_rank is None or ceiling_rank is None:
        raise PlannerBlocked("INVALID_LEVEL", "required level / role ceiling must be P0..P6")

    # Load the canonical Governance Policy and map the action scope to its disposition.
    try:
        policy = yaml.safe_load((root / "governance" / "GOVERNANCE_POLICY.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PlannerBlocked("POLICY_UNAVAILABLE", f"cannot load governance policy: {exc}") from exc
    disposition = scope_policy_map(policy).get(permission_scope)
    if disposition is None:
        raise PlannerBlocked("UNKNOWN_SCOPE", f"permission_scope {permission_scope!r} has no policy disposition")

    created = _parse_ts(now)
    expires_at = _fmt_ts(created + timedelta(minutes=ttl_minutes))
    created_at = _fmt_ts(created)

    # Least privilege: required == effective == granted == the action's need; the role
    # ceiling is the upper bound the grant stays within.
    effective_level = required_permission_level
    granted_level = required_permission_level
    authority_sufficient = required_rank <= _LEVEL_RANK[effective_level] <= _LEVEL_RANK[granted_level] <= ceiling_rank

    requester_ref = f"thomas_prime:{actor_id}"
    fingerprint_payload = {
        "schema_version": "action_fingerprint_payload.v0.1",
        "task_id": task_id,
        "task_revision": revision,
        "core_context_binding_id": ccb,
        "requester_ref": requester_ref,
        "permission_scope": permission_scope,
        "action_type": "internal.analysis.create",
        "target_ref": f"internal:{task_id}:analysis",
        "tool_id": None,
        "program_id": None,
        "data_scope": ["assigned.context", "task.request"],
        "content_sha256": None,
        "amount_decimal": None,
        "currency": None,
        "normalized_parameters": {"output_format": "structured_markdown", "visibility": "internal"},
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
            "authority_reasons": ["Internal analysis within the assigned Task scope and authority ceiling."],
        },
        "risk": {
            "risk_level": "GREEN",
            "risk_reasons": ["Internal-only analysis with no external, financial, or runtime effect."],
            "policy_disposition": disposition,
        },
        "decision": {
            "permission_decision": decision,
            "decision_reasons": ["Authority is sufficient and the exact action is internal and reversible."],
            "constraints": ["No external publication, tool/program execution, or runtime mutation."],
        },
        "approval": {
            "approval_required": False,
            "approval_id": None,
            "approval_status": "NOT_REQUIRED",
        },
        "runtime_effect": {
            "mode": "REVIEW_ONLY",
            "executor_handoff_allowed": False,
            "external_execution_allowed": False,
            "financial_execution_allowed": False,
            "runtime_mutation_allowed": False,
            "tool_enablement_allowed": False,
            "program_enablement_allowed": False,
            "permission_expansion_allowed": False,
        },
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

    if record["decision"]["permission_decision"] != "ALLOW":
        raise PlannerBlocked(
            "NOT_ALLOWED",
            f"MVP only performs ALLOW-tier actions; scope {permission_scope} disposition is {decision}",
        )
    return record
