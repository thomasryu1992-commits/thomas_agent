"""R2.2 Thomas Prime orchestrator.

``plan_task`` runs the full planning pipeline for one RECEIVED task and returns the
coherent set of records the runtime needs downstream:

    RECEIVED task
      -> classify (decision)
      -> bind to active Core (binding, bound task)
      -> select Role (general.specialist)
      -> PermissionDecision (ALLOW)
      -> role_assignment
      -> PLANNED task (classification + permission + routing applied, schema-valid)

Prime plans and routes; it does not perform the specialist work. Every step fails
closed via ``PlannerBlocked``. The returned PLANNED task is re-validated against the
closed task.v0.3 schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity, schema_validation
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from . import timeutil
from .assignment import build_role_assignment
from .binding import bind_task_to_core
from .errors import PlannerBlocked
from .paths import repo_root as _repo_root
from .permission import (
    MVP_TTL_MINUTES,
    build_permission_decision,
    build_search_permission_decision,
)
from .planner import classify_task, load_resolved_roles, select_role

TASK_SCHEMA_VERSION = "task.v0.3"


def _apply_plan_to_task(
    bound_task: Mapping[str, Any],
    decision: Mapping[str, Any],
    role: Mapping[str, Any],
    permission_decision: Mapping[str, Any],
    assignment: Mapping[str, Any],
    *,
    now: str,
) -> dict[str, Any]:
    """Produce a PLANNED task.v0.3 from the bound task and the planning records."""
    task = dict(bound_task)
    permdec_id = permission_decision["permission_decision_id"]
    audit_id = integrity.short_id("audit", {"task_id": task["identity"]["task_id"], "kind": "planned"})

    task["classification"] = dict(decision["classification"])
    task["authority"] = dict(decision["authority"])
    task["permission"] = {
        "evaluation_status": "DECIDED",
        "permission_decision": "ALLOW",
        "permission_decision_ref": permdec_id,
        "approval_state": "NOT_REQUIRED",
        "approval_id": None,
        "action_fingerprint": None,
    }
    task["routing"] = {
        "required_capabilities": list(decision["required_capabilities"]),
        "selected_route": "ROLE",
        "assigned_role_ids": [assignment["role_id"]],
        "assigned_actor_ids": [assignment["actor_instance_id"]],
        "role_assignment_ids": [assignment["assignment_id"]],
        "program_request_ids": [],
        "tool_request_ids": [],
    }
    lifecycle = dict(task["lifecycle"])
    lifecycle.update(
        {
            "status": "PLANNED",
            "previous_status": "RECEIVED",
            "status_reason": "Classified, bound, routed to a single specialist Role.",
            "transition_event_ref": audit_id,
            "status_entered_at": now,
        }
    )
    task["lifecycle"] = lifecycle
    results = dict(task.get("results", {}))
    results["validation_output_refs"] = list(results.get("validation_output_refs", []))
    task["results"] = results
    audit = dict(task.get("audit", {}))
    audit["updated_at"] = now
    audit["audit_refs"] = list(dict.fromkeys([*audit.get("audit_refs", []), audit_id]))
    task["audit"] = audit
    return task


def plan_task(
    task: Mapping[str, Any],
    *,
    now: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Plan a RECEIVED task end-to-end. Returns a dict with the coherent records.

    Keys: ``task`` (PLANNED task.v0.3), ``binding``, ``permission_decision``,
    ``role_assignment``, ``decision``, ``role``. Fails closed via ``PlannerBlocked``.
    """
    root = repo_root if repo_root is not None else _repo_root()

    decision = classify_task(task)
    binding, bound = bind_task_to_core(task, repo_root=root, now=now)

    resolved = load_resolved_roles(root)
    role = select_role(
        resolved,
        required_capabilities=decision["required_capabilities"],
        required_permission_level=decision["authority"]["required_permission_level"],
    )

    permission_decision = build_permission_decision(
        bound,
        permission_scope=decision["permission_scope"],
        required_permission_level=decision["authority"]["required_permission_level"],
        role_permission_ceiling=role["permission_ceiling"],
        now=now,
        repo_root=root,
    )

    # R3: authorize the specialist's read-only web search as a separate INTERNAL_READ
    # ALLOW action (its own PermissionDecision — the search is a distinct governed action,
    # not part of the analysis grant). The pipeline consumes this before running the tool.
    search_permission_decision = build_search_permission_decision(
        bound,
        role_permission_ceiling=role["permission_ceiling"],
        now=now,
        repo_root=root,
    )

    expires_at = timeutil.plus_minutes(now, MVP_TTL_MINUTES)
    role_assignment = build_role_assignment(
        bound,
        role,
        permission_decision,
        required_capabilities=decision["required_capabilities"],
        created_at=now,
        expires_at=expires_at,
        repo_root=root,
    )

    planned = _apply_plan_to_task(bound, decision, role, permission_decision, role_assignment, now=now)

    schema_path = root / "schemas" / f"{TASK_SCHEMA_VERSION}.schema.json"
    try:
        schema_validation.validate_against_schema(planned, schema_path, "planned_task")
    except RuntimeSchemaError as exc:
        raise PlannerBlocked("PLANNED_TASK_INVALID", str(exc)) from exc

    return {
        "task": planned,
        "binding": binding,
        "permission_decision": permission_decision,
        "search_permission_decision": search_permission_decision,
        "role_assignment": role_assignment,
        "decision": decision,
        "role": role,
    }
