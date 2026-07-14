from __future__ import annotations

from typing import Any, Mapping

from .types import PolicyDecision, RouteDecision


def assemble_run(
    *,
    task: Mapping[str, Any],
    policy: PolicyDecision,
    route: RouteDecision | None,
    output: Mapping[str, Any] | None,
    validation: Mapping[str, Any] | None,
    audit_events: list[Mapping[str, Any]],
    blockers: tuple[str, ...],
) -> dict[str, Any]:
    completed = not blockers and output is not None
    return {
        "schema_version": "read_only_runtime_run.v0.2-candidate",
        "status": "REPLAY_COMPLETED" if completed else "REPLAY_BLOCKED",
        "task_id": task.get("identity", {}).get("task_id"),
        "policy": {
            "disposition": policy.disposition,
            "approval_required": policy.approval_required,
            "blockers": list(policy.blockers),
        },
        "route": None if route is None else {
            "route_type": route.route_type,
            "actor_id": route.actor_id,
            "role_id": route.role_id,
            "role_version": route.role_version,
        },
        "output": None if output is None else dict(output),
        "validation": None if validation is None else dict(validation),
        "audit_events": [dict(event) for event in audit_events],
        "blockers": list(blockers),
        "runtime_authoritative": False,
        "external_effect_performed": False,
    }
