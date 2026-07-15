"""R2.2 Role Assignment (final planner step).

Assemble a ``role_assignment.v0.2`` binding the bound task to the selected Role,
consistent with the PermissionDecision. Prime never lets an assignment exceed the
Role: capabilities come from the selected Role, tool/program allowlists stay empty,
and the authority invariant required <= effective <= granted <= role_permission_ceiling
is enforced. The record is validated against the closed schema; fail-closed otherwise.

Role-owned fields (completion/quality criteria, memory + validation policy) are read
from the Role Definition itself via the hash-verified loader, so the assignment cannot
drift from the approved Role.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from runtime import registry_resolution
from runtime.read_only_kernel import integrity, schema_validation
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .authority import authority_invariant_holds
from .errors import PlannerBlocked

ROLE_ASSIGNMENT_SCHEMA_VERSION = "role_assignment.v0.2"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _mvp_execution_budget() -> dict[str, Any]:
    """MVP assignment budget: one model call, no tools/programs, concrete caps."""
    return {
        "schema_version": "execution_budget.v0.1",
        "limits": {
            "max_agent_invocations": 1,
            "max_model_calls": 1,
            "max_tool_calls": 0,
            "max_program_calls": 0,
            "max_revision_cycles": 1,
            "max_validation_cycles": 1,
            "max_retry_count": 1,
            "max_parallel_workers": 1,
            "max_runtime_seconds": 120,
            "token_budget": 8000,
            "cost_budget": 0,
            "cost_currency": "USD",
        },
        "usage": {
            "agent_invocations": 0,
            "model_calls": 0,
            "tool_calls": 0,
            "program_calls": 0,
            "revision_cycles": 0,
            "validation_cycles": 0,
            "retry_count": 0,
            "peak_parallel_workers": 0,
            "runtime_seconds": 0,
            "tokens_used": 0,
            "cost_used": 0,
            "cost_currency": "USD",
        },
    }


def build_role_assignment(
    bound_task: Mapping[str, Any],
    role: Mapping[str, Any],
    permission_decision: Mapping[str, Any],
    *,
    required_capabilities: list[str],
    created_at: str,
    expires_at: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build and schema-validate a role_assignment.v0.2. Fail-closed.

    ``role`` is a resolved registry entry (from planner.select_role); the assignment's
    authority/permission mirror the PermissionDecision, and role-owned criteria are read
    from the hash-verified Role Definition.
    """
    root = repo_root if repo_root is not None else _repo_root()
    identity = bound_task.get("identity", {})
    context = bound_task.get("context", {})
    scope = bound_task.get("scope", {})
    task_validation = bound_task.get("validation", {})

    task_id = identity.get("task_id")
    trace_id = identity.get("trace_id")
    ccb = context.get("core_context_binding_id")
    if not (isinstance(ccb, str) and ccb.startswith("ccb-")):
        raise PlannerBlocked("NOT_BOUND", "task must be bound before a role assignment")

    if not isinstance(role, Mapping) or not role.get("role_id") or not role.get("version") or not role.get("definition_path"):
        raise PlannerBlocked("INVALID_ROLE", "role must carry role_id, version, and definition_path")
    role_id = role.get("role_id")
    role_version = role.get("version")
    ceiling = role.get("permission_ceiling")
    capabilities = set(role.get("capabilities", []))
    if not set(required_capabilities).issubset(capabilities):
        raise PlannerBlocked(
            "CAPABILITY_EXCEEDS_ROLE",
            f"assigned capabilities {sorted(required_capabilities)} exceed role {role_id} {sorted(capabilities)}",
        )

    # Mirror the PermissionDecision authority (already validated by governance).
    pd_authority = permission_decision.get("authority", {})
    required = pd_authority.get("required_permission_level")
    effective = pd_authority.get("effective_permission_level")
    granted = pd_authority.get("assignment_granted_permission_level")
    try:
        invariant_holds = authority_invariant_holds(required, effective, granted, ceiling)
    except ValueError as exc:
        raise PlannerBlocked("INVALID_AUTHORITY", "authority levels must be P0..P6") from exc
    if not invariant_holds:
        raise PlannerBlocked(
            "AUTHORITY_INVARIANT",
            f"required<=effective<=granted<=ceiling violated: {required}<={effective}<={granted}<={ceiling}",
        )
    if permission_decision.get("decision", {}).get("permission_decision") != "ALLOW":
        raise PlannerBlocked("PERMISSION_NOT_ALLOW", "assignment requires an ALLOW permission decision")

    # Read role-owned criteria from the hash-verified Role Definition.
    try:
        definition = registry_resolution.load_markdown_yaml_front_matter(
            path=root / role["definition_path"],
            expected_hash=role.get("definition_sha256"),
        )
    except registry_resolution.RegistryResolutionError as exc:
        raise PlannerBlocked("ROLE_DEFINITION_INVALID", str(exc)) from exc

    memory_policy = definition.get("memory_policy", {})
    validation_policy = definition.get("validation_policy", {})

    seed = {"task_id": task_id, "task_revision": identity.get("task_revision"), "role_id": role_id, "ccb": ccb}
    assignment_id = integrity.short_id("assignment", seed)
    actor_instance_id = integrity.short_id("agent", seed)

    assignment: dict[str, Any] = {
        "schema_version": ROLE_ASSIGNMENT_SCHEMA_VERSION,
        "assignment_id": assignment_id,
        "assignment_mode": "normal",
        "trace_id": trace_id,
        "task_id": task_id,
        "core_context_binding_id": ccb,
        "parent_task_id": None,
        "role_id": role_id,
        "role_version": role_version,
        "role_definition_ref": f"role_registry:{role_id}@{role_version}",
        "actor_instance_id": actor_instance_id,
        "assigned_by": "thomas_prime",
        "assignment_status": "ASSIGNED",
        "role_scope": {
            "role_objective": scope.get("primary_objective", ""),
            "assigned_capabilities": list(required_capabilities),
            "excluded_capabilities": list(definition.get("unsupported_capabilities", [])),
            "required_outputs": list(scope.get("expected_outputs", [])),
            "completion_criteria": list(definition.get("completion_criteria", [])),
            "quality_criteria": list(definition.get("quality_criteria", [])),
        },
        "input_refs": list(context.get("input_refs", [])),
        "context_refs": list(context.get("context_refs", [])),
        "active_core_rule_ids": list(context.get("active_core_rule_ids", [])),
        "memory_scope": {
            "readable_memory_refs": [],
            "readable_scopes": list(memory_policy.get("readable_scopes", [])),
            "prohibited_scopes": list(memory_policy.get("prohibited_scopes", [])),
            "memory_candidate_creation_allowed": bool(memory_policy.get("candidate_creation_allowed", False)),
            "allowed_candidate_types": list(memory_policy.get("allowed_candidate_types", [])),
            "validated_memory_write_allowed": False,
            "core_memory_write_allowed": False,
        },
        "authority": {
            "required_permission_level": required,
            "role_permission_ceiling": ceiling,
            "assignment_granted_permission_level": granted,
            "effective_permission_level": effective,
        },
        "permission": {
            "permission_decision": "ALLOW",
            "permission_decision_ref": permission_decision.get("permission_decision_id"),
            "approval_id": None,
        },
        "allowed_program_ids": [],
        "allowed_tool_ids": [],
        "validation": {
            "mode": validation_policy.get("default_mode", "automatic"),
            "validator_role_id": None,
            "acceptance_criteria": list(task_validation.get("acceptance_criteria") or ["objective_met", "output_contract_valid"]),
            "rejection_criteria": list(task_validation.get("rejection_criteria", [])),
            "maximum_cycles": 1,
        },
        "execution_budget": _mvp_execution_budget(),
        "constraints": list(scope.get("constraints", [])),
        "escalation_target": "thomas_prime",
        "trial_authorization_ref": None,
        "expires_at": expires_at,
        "created_at": created_at,
    }

    schema_path = root / "schemas" / f"{ROLE_ASSIGNMENT_SCHEMA_VERSION}.schema.json"
    try:
        schema_validation.validate_against_schema(assignment, schema_path, "role_assignment")
    except RuntimeSchemaError as exc:
        raise PlannerBlocked("ASSIGNMENT_SCHEMA_INVALID", str(exc)) from exc
    return assignment
