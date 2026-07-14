from __future__ import annotations

from typing import Any, Mapping


def validate_output(
    *,
    output: Mapping[str, Any],
    task: Mapping[str, Any],
) -> dict[str, Any]:
    blockers: list[str] = []

    if output.get("task_id") != task.get("identity", {}).get("task_id"):
        blockers.append("OUTPUT_TASK_ID_MISMATCH")

    if output.get("status") != "needs_validation":
        blockers.append("OUTPUT_STATUS_INVALID")

    return {
        "status": "passed" if not blockers else "failed",
        "blockers": blockers,
        "validation_grants_permission": False,
    }
