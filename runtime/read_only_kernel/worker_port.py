from __future__ import annotations

from typing import Any

from .errors import KernelBlocked
from .types import RouteSelection
from .worker import execute_contract_inspection_worker


def invoke_worker(
    *,
    route: RouteSelection,
    task: dict[str, Any],
    assignment: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    if route.selected_route != "ROLE":
        raise KernelBlocked(
            "ROUTE_NOT_SUPPORTED",
            "The deterministic read-only worker accepts the ROLE route only.",
        )
    return execute_contract_inspection_worker(
        task=task,
        assignment=assignment,
        created_at=created_at,
    )
