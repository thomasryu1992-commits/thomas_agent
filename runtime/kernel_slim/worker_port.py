from __future__ import annotations

from typing import Any, Callable, Mapping

from .types import KernelContext, RouteDecision


Worker = Callable[..., dict[str, Any]]


def invoke_worker(
    *,
    context: KernelContext,
    route: RouteDecision,
    worker: Worker,
) -> dict[str, Any]:
    bundle = context.input_bundle
    return worker(
        task=bundle["task"],
        assignment=bundle["role_assignment"],
        created_at=context.created_at,
    )
