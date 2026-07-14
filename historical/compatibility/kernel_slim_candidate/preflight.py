from __future__ import annotations

from typing import Any

from .types import KernelContext, PreflightResult


REQUIRED_RECORDS = (
    "task",
    "core_context_binding",
    "role_assignment",
    "role_registry",
    "program_registry",
    "tool_registry",
)


def run_preflight(context: KernelContext) -> PreflightResult:
    bundle = context.input_bundle
    blockers: list[str] = []

    for key in REQUIRED_RECORDS:
        if key not in bundle:
            blockers.append(f"MISSING_REQUIRED_RECORD:{key}")

    task = bundle.get("task", {})
    if task.get("runtime_mode") not in {None, "READ_ONLY_REPLAY"}:
        blockers.append("RUNTIME_MODE_NOT_ALLOWED")

    effects = bundle.get("requested_effects", {})
    if any(bool(value) for value in effects.values()):
        blockers.append("NON_READ_ONLY_EFFECT_REQUESTED")

    return PreflightResult(
        allowed=not blockers,
        blockers=tuple(blockers),
        resolved={
            "task": task,
            "assignment": bundle.get("role_assignment", {}),
            "registries": {
                "role": bundle.get("role_registry", {}),
                "program": bundle.get("program_registry", {}),
                "tool": bundle.get("tool_registry", {}),
            },
        },
    )
