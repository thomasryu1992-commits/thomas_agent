from __future__ import annotations

from .errors import KernelBlocked
from .types import PreflightContext, RouteSelection


def select_route(preflight: PreflightContext) -> RouteSelection:
    task = preflight.task
    assignment = preflight.assignment
    routing = task.get("routing", {})

    if routing.get("selected_route") != "ROLE":
        raise KernelBlocked(
            "ROUTE_NOT_SUPPORTED",
            "I0.5 v0.1 supports one explicit ROLE route only.",
        )
    if assignment.get("assignment_id") != routing.get("role_assignment_ids", [None])[0]:
        raise KernelBlocked(
            "ASSIGNMENT_LINEAGE_MISMATCH",
            "Task and Role Assignment lineage must match exactly.",
        )

    return RouteSelection(
        selected_route="ROLE",
        role_id=assignment["role_id"],
        role_version=assignment["role_version"],
        assignment_id=assignment["assignment_id"],
        actor_instance_id=assignment["actor_instance_id"],
    )
