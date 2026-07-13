from __future__ import annotations

from copy import deepcopy
from typing import Any

from runtime.read_only_kernel.integrity import scan_for_secret_bearing_keys, sha256_record, sha256_value, short_id

KERNEL_ID = "thomas.read_only_runtime_kernel"
KERNEL_VERSION = "0.1.1"
ENTRY_MODE = "RUNTIME_AUTHORITATIVE_READ_ONLY"


class EntryPlanError(ValueError):
    pass


def _readiness_state(readiness: dict[str, Any]) -> tuple[str, str, bool, bool]:
    if readiness.get("schema_version") != "runtime_promotion_readiness.v0.1":
        raise EntryPlanError("readiness schema_version must be runtime_promotion_readiness.v0.1")
    if readiness.get("status") != "REVIEW_ONLY_NOT_RUNTIME_ACTIVE":
        raise EntryPlanError("readiness status must remain REVIEW_ONLY_NOT_RUNTIME_ACTIVE")
    summary = readiness.get("summary")
    if not isinstance(summary, dict):
        raise EntryPlanError("readiness summary is required")
    design = summary.get("design_readiness")
    activation = summary.get("activation_readiness")
    if not isinstance(design, dict) or not isinstance(activation, dict):
        raise EntryPlanError("readiness must contain Design and Activation Readiness")
    design_result = design.get("result")
    activation_result = activation.get("result")
    design_ready = design.get("ready_for_runtime_authoritative_design") is True
    activation_ready = activation.get("ready_for_runtime_activation_review") is True
    if design_result not in {"BLOCKED_NOT_READY", "READY_FOR_THOMAS_DESIGN_DECISION"}:
        raise EntryPlanError("Design Readiness result is invalid")
    if activation_result not in {"BLOCKED_NOT_READY", "READY_FOR_RUNTIME_ACTIVATION_REVIEW"}:
        raise EntryPlanError("Activation Readiness result is invalid")
    if design_ready != (design_result == "READY_FOR_THOMAS_DESIGN_DECISION"):
        raise EntryPlanError("Design Readiness result and flag mismatch")
    if activation_ready != (activation_result == "READY_FOR_RUNTIME_ACTIVATION_REVIEW"):
        raise EntryPlanError("Activation Readiness result and flag mismatch")
    if activation_ready and not design_ready:
        raise EntryPlanError("Activation Readiness cannot be ready while Design Readiness is blocked")
    if summary.get("result") != design_result:
        raise EntryPlanError("top-level readiness result must mirror Design Readiness")
    if summary.get("ready_for_runtime_activation") is not False:
        raise EntryPlanError("readiness must not claim actual Runtime activation")
    runtime_effect = readiness.get("runtime_effect")
    if not isinstance(runtime_effect, dict) or any(value is not False for value in runtime_effect.values()):
        raise EntryPlanError("readiness Runtime effects must all remain false")
    integrity = readiness.get("integrity")
    if not isinstance(integrity, dict):
        raise EntryPlanError("readiness integrity is required")
    payload = integrity.get("readiness_fingerprint_payload")
    if integrity.get("readiness_sha256") != sha256_value(payload):
        raise EntryPlanError("readiness fingerprint mismatch")
    return design_result, activation_result, design_ready, activation_ready


def build_entry_plan(
    readiness: dict[str, Any],
    *,
    readiness_ref: str,
    created_at: str,
) -> dict[str, Any]:
    scan_for_secret_bearing_keys(readiness)
    design_result, activation_result, design_ready, activation_ready = _readiness_state(readiness)
    readiness_sha256 = sha256_record(readiness)
    blocking_reasons: list[str] = []
    if not design_ready:
        blocking_reasons.append("DESIGN_READINESS_NOT_READY")
    if not activation_ready:
        blocking_reasons.append("ACTIVATION_READINESS_NOT_READY")
    ready_for_approval_design = not blocking_reasons
    checks = [
        {
            "check_id": "design_readiness",
            "result": "PASS" if design_ready else "BLOCK",
            "notes": "Design Readiness must be READY_FOR_THOMAS_DESIGN_DECISION.",
        },
        {
            "check_id": "activation_readiness",
            "result": "PASS" if activation_ready else "BLOCK",
            "notes": "Activation Readiness must be READY_FOR_RUNTIME_ACTIVATION_REVIEW.",
        },
        {
            "check_id": "separate_action_approval_boundary",
            "result": "PASS",
            "notes": "A separate exact Action Approval remains required before any future Runtime entry.",
        },
        {
            "check_id": "current_approval_contract_no_consumption",
            "result": "PASS",
            "notes": "Approval v0.1 is review-only and cannot be consumed or handed to Runtime by this phase.",
        },
        {
            "check_id": "disabled_entry_adapter",
            "result": "PASS",
            "notes": "The I0.5.2 adapter is disabled and cannot start a Runtime-authoritative session.",
        },
    ]
    seed = {
        "readiness_sha256": readiness_sha256,
        "kernel_id": KERNEL_ID,
        "kernel_version": KERNEL_VERSION,
        "created_at": created_at,
    }
    entry_plan_id = short_id("entryplan", seed)
    fingerprint_payload = {
        "schema_version": "runtime_authoritative_read_only_entry_plan_fingerprint_payload.v0.1",
        "entry_plan_id": entry_plan_id,
        "readiness_sha256": readiness_sha256,
        "kernel_id": KERNEL_ID,
        "kernel_version": KERNEL_VERSION,
        "entry_mode": ENTRY_MODE,
        "blocking_reasons": blocking_reasons,
        "created_at": created_at,
    }
    return {
        "schema_version": "runtime_authoritative_read_only_entry_plan.v0.1",
        "entry_plan_id": entry_plan_id,
        "phase": "I0.5.2",
        "status": "READY_FOR_THOMAS_ENTRY_APPROVAL_DESIGN" if ready_for_approval_design else "REVIEW_ONLY_BLOCKED",
        "owner": "Thomas",
        "runtime_source_of_truth": False,
        "kernel": {
            "kernel_id": KERNEL_ID,
            "kernel_version": KERNEL_VERSION,
            "requested_mode": ENTRY_MODE,
            "requested_run_count": 1,
            "runtime_authoritative_mode_enabled": False,
        },
        "readiness": {
            "readiness_id": readiness.get("readiness_id"),
            "readiness_ref": readiness_ref,
            "readiness_sha256": readiness_sha256,
            "design_result": design_result,
            "activation_result": activation_result,
            "design_ready": design_ready,
            "activation_ready": activation_ready,
        },
        "entry_scope": {
            "single_run_only": True,
            "max_runs": 1,
            "exact_task_binding_required": True,
            "exact_input_bundle_binding_required": True,
            "current_core_binding_required": True,
            "filesystem_read_only": True,
            "filesystem_write_allowed": False,
            "model_invocation_allowed": False,
            "tool_execution_allowed": False,
            "program_execution_allowed": False,
            "network_access_allowed": False,
            "external_action_allowed": False,
            "financial_action_allowed": False,
            "runtime_mutation_allowed": False,
        },
        "approval_boundary": {
            "separate_action_approval_required": True,
            "action_approval_contract_ref": "docs/runtime-contracts/APPROVAL_CONTRACT_V0.1.md",
            "permission_scope": "RUNTIME_GOVERNANCE",
            "approval_present": False,
            "approval_verified": False,
            "approval_consumption_supported_by_current_contract": False,
            "future_atomic_consumption_required": True,
            "executor_handoff_allowed": False,
        },
        "checks": checks,
        "decision": {
            "result": "READY_FOR_THOMAS_ENTRY_APPROVAL_DESIGN" if ready_for_approval_design else "BLOCKED_NOT_READY",
            "blocking_reasons": blocking_reasons,
            "ready_for_thomas_entry_approval_design": ready_for_approval_design,
            "ready_for_runtime_entry": False,
            "entry_performed": False,
        },
        "runtime_effect": {
            "mode": "REVIEW_ONLY_ENTRY_DESIGN",
            "grants_runtime_permission": False,
            "grants_runtime_activation": False,
            "grants_core_activation": False,
            "grants_tool_enablement": False,
            "grants_program_enablement": False,
            "grants_executor_enablement": False,
            "grants_external_execution": False,
            "grants_financial_execution": False,
            "consumes_approval": False,
            "starts_runtime_session": False,
            "mutates_runtime": False,
        },
        "integrity": {
            "hash_schema": "runtime_authoritative_read_only_entry_plan_fingerprint_payload.v0.1",
            "entry_plan_fingerprint_payload": fingerprint_payload,
            "entry_plan_sha256": sha256_value(fingerprint_payload),
        },
        "created_at": created_at,
    }


def validate_entry_plan_semantics(record: dict[str, Any]) -> None:
    scan_for_secret_bearing_keys(record)
    if record.get("schema_version") != "runtime_authoritative_read_only_entry_plan.v0.1":
        raise EntryPlanError("entry plan schema_version mismatch")
    if record.get("runtime_source_of_truth") is not False:
        raise EntryPlanError("entry plan cannot be a Runtime source of truth")
    kernel = record.get("kernel", {})
    if kernel.get("kernel_id") != KERNEL_ID or kernel.get("kernel_version") != KERNEL_VERSION:
        raise EntryPlanError("entry plan Kernel identity/version mismatch")
    if kernel.get("requested_mode") != ENTRY_MODE or kernel.get("requested_run_count") != 1:
        raise EntryPlanError("entry plan must request one Runtime-authoritative read-only run only")
    if kernel.get("runtime_authoritative_mode_enabled") is not False:
        raise EntryPlanError("entry plan cannot enable Runtime-authoritative mode")
    readiness = record.get("readiness", {})
    design_ready = readiness.get("design_ready") is True
    activation_ready = readiness.get("activation_ready") is True
    expected_blockers = []
    if not design_ready:
        expected_blockers.append("DESIGN_READINESS_NOT_READY")
    if not activation_ready:
        expected_blockers.append("ACTIVATION_READINESS_NOT_READY")
    ready = not expected_blockers
    decision = record.get("decision", {})
    if decision.get("blocking_reasons") != expected_blockers:
        raise EntryPlanError("entry plan blockers do not match readiness")
    if decision.get("result") != ("READY_FOR_THOMAS_ENTRY_APPROVAL_DESIGN" if ready else "BLOCKED_NOT_READY"):
        raise EntryPlanError("entry plan decision result mismatch")
    if decision.get("ready_for_thomas_entry_approval_design") is not ready:
        raise EntryPlanError("entry approval-design readiness flag mismatch")
    if decision.get("ready_for_runtime_entry") is not False or decision.get("entry_performed") is not False:
        raise EntryPlanError("I0.5.2 cannot claim Runtime entry readiness or performance")
    if record.get("status") != ("READY_FOR_THOMAS_ENTRY_APPROVAL_DESIGN" if ready else "REVIEW_ONLY_BLOCKED"):
        raise EntryPlanError("entry plan status mismatch")
    scope = record.get("entry_scope", {})
    required_true = [
        "single_run_only",
        "exact_task_binding_required",
        "exact_input_bundle_binding_required",
        "current_core_binding_required",
        "filesystem_read_only",
    ]
    required_false = [
        "filesystem_write_allowed",
        "model_invocation_allowed",
        "tool_execution_allowed",
        "program_execution_allowed",
        "network_access_allowed",
        "external_action_allowed",
        "financial_action_allowed",
        "runtime_mutation_allowed",
    ]
    if scope.get("max_runs") != 1 or any(scope.get(key) is not True for key in required_true):
        raise EntryPlanError("entry scope single-run/read-only requirements are invalid")
    if any(scope.get(key) is not False for key in required_false):
        raise EntryPlanError("entry scope contains a prohibited capability")
    approval = record.get("approval_boundary", {})
    if approval.get("separate_action_approval_required") is not True:
        raise EntryPlanError("separate Action Approval must remain required")
    if approval.get("permission_scope") != "RUNTIME_GOVERNANCE":
        raise EntryPlanError("entry Action Approval scope must be RUNTIME_GOVERNANCE")
    if approval.get("approval_present") is not False or approval.get("approval_verified") is not False:
        raise EntryPlanError("I0.5.2 cannot claim an Action Approval is present or verified")
    if approval.get("approval_consumption_supported_by_current_contract") is not False:
        raise EntryPlanError("Approval v0.1 consumption must remain unsupported")
    if approval.get("future_atomic_consumption_required") is not True or approval.get("executor_handoff_allowed") is not False:
        raise EntryPlanError("future atomic consumption and no-handoff boundaries are invalid")
    effects = record.get("runtime_effect", {})
    for key, value in effects.items():
        if key == "mode":
            if value != "REVIEW_ONLY_ENTRY_DESIGN":
                raise EntryPlanError("entry plan Runtime effect mode mismatch")
        elif value is not False:
            raise EntryPlanError(f"entry plan Runtime effect must remain false: {key}")
    integrity = record.get("integrity", {})
    if integrity.get("entry_plan_sha256") != sha256_value(integrity.get("entry_plan_fingerprint_payload")):
        raise EntryPlanError("entry plan fingerprint mismatch")
