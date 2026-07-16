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
    build_validation_permission_decision,
    build_write_permission_decision,
)
from .planner import (
    VALIDATOR_REQUIRED_CAPABILITIES,
    VALIDATOR_REQUIRED_PERMISSION_LEVEL,
    classify_task,
    load_resolved_roles,
    select_role,
)

TASK_SCHEMA_VERSION = "task.v0.3"


def _apply_plan_to_task(
    bound_task: Mapping[str, Any],
    decision: Mapping[str, Any],
    role: Mapping[str, Any],
    permission_decision: Mapping[str, Any],
    assignment: Mapping[str, Any],
    *,
    now: str,
    validator_assignment: Mapping[str, Any] | None = None,
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
    # R7: when an independent validator was planned, the routing records BOTH agents of
    # the (minimal) dynamic team — the specialist and the validator.
    assigned_roles = [assignment["role_id"]]
    assigned_actors = [assignment["actor_instance_id"]]
    assignment_ids = [assignment["assignment_id"]]
    if validator_assignment is not None:
        assigned_roles.append(validator_assignment["role_id"])
        assigned_actors.append(validator_assignment["actor_instance_id"])
        assignment_ids.append(validator_assignment["assignment_id"])
    task["routing"] = {
        "required_capabilities": list(decision["required_capabilities"]),
        "selected_route": "ROLE",
        "assigned_role_ids": assigned_roles,
        "assigned_actor_ids": assigned_actors,
        "role_assignment_ids": assignment_ids,
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
    independent_validation: bool = False,
    controlled_write: bool = False,
) -> dict[str, Any]:
    """Plan a RECEIVED task end-to-end. Returns a dict with the coherent records.

    Keys: ``task`` (PLANNED task.v0.3), ``binding``, ``permission_decision``,
    ``role_assignment``, ``decision``, ``role``. Fails closed via ``PlannerBlocked``.

    ``independent_validation`` (R7, opt-in) additionally plans the second agent of the
    minimal dynamic team — the independent validator (``validation.independent``) with its
    own PermissionDecision (SIMULATION_VALIDATION, P2) and its own role_assignment — adding
    keys ``validator_role``, ``validator_permission_decision``, ``validator_assignment``.
    Prime requests validation without lowering any policy (a role activation condition);
    the validator reviews, it never performs the original task.

    ``controlled_write`` (R8, opt-in) additionally plans the workspace write as its own
    governed action, adding ``write_permission_decision`` — a WORKSPACE_REVERSIBLE_WRITE
    grant at P3 whose disposition is EXECUTE_AND_REPORT. Planning it does not perform it;
    the pipeline writes only after validation passes, through the gated writer.
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

    # R7 (opt-in): plan the independent validator as a second, separately-governed agent.
    validator_role = validator_permission_decision = validator_assignment = None
    if independent_validation:
        validator_role = select_role(
            resolved,
            required_capabilities=VALIDATOR_REQUIRED_CAPABILITIES,
            required_permission_level=VALIDATOR_REQUIRED_PERMISSION_LEVEL,
        )
        validator_permission_decision = build_validation_permission_decision(
            bound,
            role_permission_ceiling=validator_role["permission_ceiling"],
            now=now,
            repo_root=root,
        )
        validator_assignment = build_role_assignment(
            bound,
            validator_role,
            validator_permission_decision,
            required_capabilities=list(VALIDATOR_REQUIRED_CAPABILITIES),
            created_at=now,
            expires_at=expires_at,
            repo_root=root,
        )

    # R8 (opt-in): authorize the controlled workspace write as its own governed action.
    # Its disposition is EXECUTE_AND_REPORT (not ALLOW) — the runtime's first — and the
    # grant is refused outright if the specialist's ceiling is below P3 CREATE.
    write_permission_decision = None
    if controlled_write:
        write_permission_decision = build_write_permission_decision(
            bound,
            role_permission_ceiling=role["permission_ceiling"],
            now=now,
            repo_root=root,
        )

    planned = _apply_plan_to_task(
        bound, decision, role, permission_decision, role_assignment,
        now=now, validator_assignment=validator_assignment,
    )

    schema_path = root / "schemas" / f"{TASK_SCHEMA_VERSION}.schema.json"
    try:
        schema_validation.validate_against_schema(planned, schema_path, "planned_task")
    except RuntimeSchemaError as exc:
        raise PlannerBlocked("PLANNED_TASK_INVALID", str(exc)) from exc

    plan: dict[str, Any] = {
        "task": planned,
        "binding": binding,
        "permission_decision": permission_decision,
        "search_permission_decision": search_permission_decision,
        "role_assignment": role_assignment,
        "decision": decision,
        "role": role,
    }
    if independent_validation:
        plan.update({
            "validator_role": validator_role,
            "validator_permission_decision": validator_permission_decision,
            "validator_assignment": validator_assignment,
        })
    if controlled_write:
        plan["write_permission_decision"] = write_permission_decision
    return plan
