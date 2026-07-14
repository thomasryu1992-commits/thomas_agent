from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ReadCounter:
    value: int = 0

    def add(self, count: int) -> None:
        if not isinstance(count, int) or count < 0:
            raise ValueError("filesystem read count increment must be a non-negative integer")
        self.value += count


@dataclass(slots=True)
class PreflightContext:
    checks: list[dict[str, Any]]
    task: dict[str, Any]
    binding: dict[str, Any]
    assignment: dict[str, Any]
    authority: dict[str, Any]
    permission: dict[str, Any]
    task_id: str
    task_revision: int
    trace_id: str
    core_context_binding_id: str


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    authority: dict[str, Any]
    permission: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RouteSelection:
    selected_route: str
    role_id: str
    role_version: str
    assignment_id: str
    actor_instance_id: str
