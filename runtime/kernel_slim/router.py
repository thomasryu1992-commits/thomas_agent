from __future__ import annotations

from .types import PolicyDecision, PreflightResult, RouteDecision


class RoutingError(RuntimeError):
    pass


def select_route(
    *,
    preflight: PreflightResult,
    policy: PolicyDecision,
) -> RouteDecision:
    if policy.disposition == "BLOCK":
        raise RoutingError("blocked policy decisions cannot be routed")

    assignment = preflight.resolved.get("assignment", {})
    role_id = assignment.get("role_id")
    role_version = assignment.get("role_version")
    actor_id = assignment.get("actor_instance_id")

    if not all((role_id, role_version, actor_id)):
        raise RoutingError("incomplete role assignment")

    return RouteDecision(
        route_type="DETERMINISTIC_READ_ONLY_WORKER",
        actor_id=str(actor_id),
        role_id=str(role_id),
        role_version=str(role_version),
        evidence={"assignment_id": assignment.get("assignment_id")},
    )
