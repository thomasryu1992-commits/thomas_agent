from __future__ import annotations

from typing import Any, Mapping

from .types import PolicyDecision, PreflightResult


CANONICAL_POLICY_ID = "thomas.governance.policy"
CANONICAL_POLICY_VERSION = "1.1.0"

REQUIRED_DISABLED_RUNTIME_EFFECTS = (
    "grants_runtime_execution",
    "grants_tool_or_program_enablement",
    "grants_external_execution",
    "grants_financial_execution",
    "grants_permission_expansion",
    "executor_handoff_allowed",
    "external_execution_allowed",
    "financial_execution_allowed",
    "runtime_mutation_allowed",
    "tool_enablement_allowed",
    "program_enablement_allowed",
    "permission_expansion_allowed",
    "approval_consumption_allowed",
    "core_activation_allowed",
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

    if governance_policy.get("authoritative") is not True:
        return PolicyDecision(
            disposition="BLOCK",
            blockers=("GOVERNANCE_POLICY_NOT_AUTHORITATIVE",),
            approval_required=False,
            evidence={},
        )

    if (
        governance_policy.get("policy_id") != CANONICAL_POLICY_ID
        or governance_policy.get("policy_version") != CANONICAL_POLICY_VERSION
        or governance_policy.get("status") != "ACTIVE_POLICY_SOURCE"
    ):
        return PolicyDecision(
            disposition="BLOCK",
            blockers=("GOVERNANCE_POLICY_IDENTITY_INVALID",),
            approval_required=False,
            evidence={
                "policy_id": governance_policy.get("policy_id"),
                "policy_version": governance_policy.get("policy_version"),
                "status": governance_policy.get("status"),
            },
        )

    task = preflight.resolved.get("task", {})
    requested_disposition = task.get("permission", {}).get("decision", "BLOCK")

    if requested_disposition not in {
        "ALLOW",
        "EXECUTE_AND_REPORT",
        "APPROVAL_REQUIRED",
        "BLOCK",
    }:
        return PolicyDecision(
            disposition="BLOCK",
            blockers=("GOVERNANCE_DISPOSITION_UNKNOWN",),
            approval_required=False,
            evidence={"requested_disposition": requested_disposition},
        )

    # The current slim replay kernel has no Approval-consumption or Executor path.
    # Only already-resolved read-only ALLOW / EXECUTE_AND_REPORT records may continue.
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
            "policy_authoritative": True,
            "policy_status": governance_policy.get("status"),
            "runtime_effect_mode": runtime_effect.get("mode"),
        },
    )
