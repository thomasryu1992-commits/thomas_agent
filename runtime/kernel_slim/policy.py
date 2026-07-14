from __future__ import annotations

from typing import Any, Mapping

from .types import PolicyDecision, PreflightResult


def evaluate_policy(
    *,
    preflight: PreflightResult,
    governance_policy: Mapping[str, Any],
) -> PolicyDecision:
    if not preflight.allowed:
        return PolicyDecision(
            disposition="BLOCK",
            blockers=preflight.blockers,
            approval_required=False,
            evidence={"source": "preflight"},
        )

    task = preflight.resolved.get("task", {})
    requested_disposition = task.get("permission", {}).get("decision", "BLOCK")

    allowed = {"ALLOW", "EXECUTE_AND_REPORT"}
    if requested_disposition not in allowed:
        return PolicyDecision(
            disposition="BLOCK",
            blockers=("READ_ONLY_KERNEL_DISPOSITION_NOT_ALLOWED",),
            approval_required=requested_disposition == "APPROVAL_REQUIRED",
            evidence={"requested_disposition": requested_disposition},
        )

    runtime_effect = governance_policy.get("runtime_effect", {})
    if runtime_effect.get("grants_runtime_execution") is not False:
        return PolicyDecision(
            disposition="BLOCK",
            blockers=("GOVERNANCE_RUNTIME_BOUNDARY_INVALID",),
            approval_required=False,
            evidence={},
        )

    return PolicyDecision(
        disposition=requested_disposition,
        blockers=(),
        approval_required=False,
        evidence={
            "policy_id": governance_policy.get("policy_id"),
            "policy_version": governance_policy.get("policy_version"),
        },
    )
