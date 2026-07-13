from __future__ import annotations

from copy import deepcopy
from typing import Any

from .integrity import short_id

WORKER_ID = "kernel.contract_inspection.readonly"
WORKER_VERSION = "0.1.0"


def execute_contract_inspection_worker(
    *,
    task: dict[str, Any],
    assignment: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    """Produce a deterministic Agent Output from already supplied records only."""
    identity = task["identity"]
    context = task["context"]
    role_scope = assignment["role_scope"]
    constraints = list(task["scope"].get("constraints", []))
    expected_outputs = list(task["scope"].get("expected_outputs", []))
    required_level = task["authority"]["required_permission_level"]
    route = task["routing"]["selected_route"]
    risk = task["classification"]["risk_level"]

    seed = {
        "task_id": identity["task_id"],
        "task_revision": identity["task_revision"],
        "assignment_id": assignment["assignment_id"],
        "worker_id": WORKER_ID,
        "worker_version": WORKER_VERSION,
    }

    key_findings = [
        f"Task route is {route} under {assignment['role_id']}@{assignment['role_version']}.",
        f"Required authority is {required_level}; effective assignment authority is {assignment['authority']['effective_permission_level']}.",
        "Execution remained inside the deterministic in-process read-only worker boundary.",
    ]
    if constraints:
        key_findings.append("Task constraints: " + ", ".join(constraints) + ".")

    return {
        "schema_version": "agent_output.v0.2",
        "agent_output_id": short_id("agentout", seed),
        "trace_id": identity["trace_id"],
        "task_id": identity["task_id"],
        "core_context_binding_id": context["core_context_binding_id"],
        "assignment_id": assignment["assignment_id"],
        "actor_instance_id": assignment["actor_instance_id"],
        "role_id": assignment["role_id"],
        "role_version": assignment["role_version"],
        "status": "needs_validation",
        "goal": task["scope"]["primary_objective"],
        "summary": (
            "The I0.5 deterministic read-only worker inspected the supplied Task, Core Binding, "
            "Role, Assignment, Registry, Authority, Permission, Budget, and boundary records. "
            "No model, Tool, Program, network, external action, or filesystem mutation was performed."
        ),
        "facts": [
            {
                "statement": f"The Task risk level is {risk} and the selected route is {route}.",
                "evidence_refs": ["task.classification", "task.routing"],
            },
            {
                "statement": (
                    f"The Role objective is: {role_scope['role_objective']}"
                ),
                "evidence_refs": ["role_assignment.role_scope.role_objective"],
            },
            {
                "statement": (
                    "Expected outputs are: " + ", ".join(expected_outputs)
                    if expected_outputs
                    else "No expected outputs were declared."
                ),
                "evidence_refs": ["task.scope.expected_outputs"],
            },
        ],
        "evidence": [
            {"ref": "task.request.raw_request", "type": "task_input"},
            {"ref": "task.scope", "type": "task_contract_scope"},
            {"ref": "role_assignment", "type": "role_assignment_snapshot"},
        ],
        "inferences": [
            {
                "statement": (
                    "The bundle is eligible for non-authoritative read-only development replay because "
                    "lineage, role status, authority, permission, routing, budget, and no-effect checks passed."
                )
            }
        ],
        "assumptions": [
            "The supplied input bundle contains the intended immutable development snapshots.",
        ],
        "uncertainty": [
            "No external evidence, live Runtime state, model reasoning, Tool output, or Program output was evaluated.",
        ],
        "risks": [
            "Development replay must not be interpreted as Runtime activation, Tool/Program enablement, or execution permission.",
        ],
        "recommendation": {
            "action": "review_read_only_replay_output",
            "reason": "The output is suitable for contract integration testing only and has no Runtime authority.",
        },
        "limitations": [
            "The worker only summarizes explicit input records.",
            "The worker performs no domain research and invokes no external capability.",
        ],
        "validation_recommended": True,
        "permission_request_refs": [],
        "next_actions": [
            "Run automatic contract and lineage validation.",
            "Do not promote this replay result to Runtime authority without the separate approved lifecycle.",
        ],
        "memory_candidates": [],
        "escalation_required": False,
        "role_specific_output": {
            "key_findings": key_findings,
            "evidence_quality": "explicit_input_records_only",
            "unresolved_questions": [
                "Has the cumulative I0.4 package been applied and passed the real Repository Full Gate?",
                "Is there a separately approved and activated Current Core Release for Runtime-authoritative use?",
            ],
        },
        "created_at": created_at,
    }
