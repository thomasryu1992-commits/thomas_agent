from __future__ import annotations

from typing import Any, Mapping

from .types import PolicyDecision, PreflightResult


REQUIRED_DISABLED_RUNTIME_EFFECTS = (
    "grants_runtime_execution",
    "grants_tool_or_program_enablement",
    "grants_external_execution",
    "grants_financial_execution",
    "grants_permission_expansion",
)


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

    if governance_policy.get("authoritative") is not False:
        return PolicyDecision(
            disposition="BLOCK",
            blockers=("GOVERNANCE_MIGRATION_AUTHORITY_INVALID",),
            approval_required=False,
            evidence={},
        )

    task = preflight.resolved.get("task", {})
    requested_disposition = task.get("permission", {}).get("decision", "BLOCK")

    if requested_disposition not in {"ALLOW", "EXECUTE_AND_REPORT"}:
        return PolicyDecision(
            disposition="BLOCK",
            blockers=("READ_ONLY_KERNEL_DISPOSITION_NOT_ALLOWED",),
            approval_required=requested_disposition == "APPROVAL_REQUIRED",
            evidence={"requested_disposition": requested_disposition},
        )

    runtime_effect = governance_policy.get("runtime_effect", {})
    invalid_fields = [
        field
        for field in REQUIRED_DISABLED_RUNTIME_EFFECTS
        if runtime_effect.get(field) is not False
    ]
    if invalid_fields:
        return PolicyDecision(
            disposition="BLOCK",
            blockers=tuple(
                f"GOVERNANCE_RUNTIME_BOUNDARY_INVALID:{field}"
                for field in invalid_fields
            ),
            approval_required=False,
            evidence={"invalid_runtime_effect_fields": invalid_fields},
        )

    return PolicyDecision(
        disposition=requested_disposition,
        blockers=(),
        approval_required=False,
        evidence={
            "policy_id": governance_policy.get("policy_id"),
            "policy_version": governance_policy.get("policy_version"),
            "policy_authoritative": False,
        },
    )
