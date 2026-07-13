from __future__ import annotations

from copy import deepcopy
from typing import Any

from runtime.read_only_kernel.integrity import scan_for_secret_bearing_keys, sha256_record, sha256_value, short_id
from .authorization import validate_entry_authorization_semantics

ATOMIC_TRANSITION_PLANNER_ID = "thomas.runtime_entry.atomic_transition_preview"
ATOMIC_TRANSITION_PLANNER_VERSION = "0.1.0"


class AtomicTransitionError(ValueError):
    pass


def _precondition_blockers(preconditions: dict[str, bool]) -> list[str]:
    mapping = [
        ("exact_bindings_verified", "EXACT_BINDINGS_NOT_VERIFIED"),
        ("component_bindings_verified", "COMPONENT_BINDINGS_NOT_VERIFIED"),
        ("action_fingerprint_verified", "ACTION_FINGERPRINT_NOT_VERIFIED"),
        ("approval_verified", "ACTION_APPROVAL_NOT_VERIFIED"),
        ("approval_not_expired", "ACTION_APPROVAL_EXPIRED"),
        ("approval_not_revoked", "ACTION_APPROVAL_REVOKED"),
        ("authorization_unused", "AUTHORIZATION_NOT_UNUSED"),
        ("nonce_unseen", "NONCE_ALREADY_SEEN"),
        ("ttl_valid", "AUTHORIZATION_TTL_INVALID"),
        ("resource_limits_within_task_budget", "RESOURCE_LIMITS_EXCEED_TASK_BUDGET"),
        ("kill_switch_allows_entry", "KILL_SWITCH_BLOCKED"),
        ("runtime_boundary_still_read_only", "RUNTIME_BOUNDARY_NOT_READ_ONLY"),
    ]
    return [reason for key, reason in mapping if preconditions.get(key) is not True]


def build_atomic_transition_preview(
    authorization_record: dict[str, Any],
    *,
    authorization_ref: str,
    preconditions: dict[str, bool],
    created_at: str,
) -> dict[str, Any]:
    validate_entry_authorization_semantics(authorization_record)
    scan_for_secret_bearing_keys(preconditions)
    expected_keys = {
        "exact_bindings_verified", "component_bindings_verified", "action_fingerprint_verified",
        "approval_verified", "approval_not_expired", "approval_not_revoked", "authorization_unused",
        "nonce_unseen", "ttl_valid", "resource_limits_within_task_budget", "kill_switch_allows_entry",
        "runtime_boundary_still_read_only",
    }
    if set(preconditions) != expected_keys or any(not isinstance(value, bool) for value in preconditions.values()):
        raise AtomicTransitionError("atomic transition preconditions are incomplete")
    approval = authorization_record["action_approval"]
    if authorization_record.get("status") != "APPROVED_NOT_CONSUMED_REVIEW_ONLY" or approval.get("approval_status") != "APPROVED_NOT_CONSUMED":
        raise AtomicTransitionError("atomic transition preview requires an approved, unused review-only Authorization")
    blockers = _precondition_blockers(preconditions)
    eligible = not blockers
    authorization_sha = sha256_record(authorization_record)
    seed = {"authorization_sha256": authorization_sha, "preconditions": preconditions, "created_at": created_at}
    transition_id = short_id("reatx", seed)
    reservation_id = short_id("reservation", {"transition_id": transition_id, "kind": "reservation"})
    session_id = short_id("session", {"transition_id": transition_id, "kind": "session"})
    limits = authorization_record["resource_limits"]
    payload = {
        "schema_version": "runtime_entry_atomic_transition_preview_fingerprint_payload.v0.1",
        "transition_id": transition_id,
        "authorization_sha256": authorization_sha,
        "preconditions": deepcopy(preconditions),
        "blocking_reasons": blockers,
        "reservation_id": reservation_id,
        "session_id": session_id,
        "created_at": created_at,
    }
    return {
        "schema_version": "runtime_entry_atomic_transition_preview.v0.1",
        "transition_id": transition_id,
        "phase": "I0.5.3",
        "status": "ELIGIBLE_FOR_I0_5_4_IMPLEMENTATION_REVIEW" if eligible else "BLOCKED_NOT_ELIGIBLE",
        "owner": "Thomas",
        "record_scope": "SYNTHETIC_TEST_ONLY",
        "runtime_source_of_truth": False,
        "authorization_binding": {
            "authorization_id": authorization_record["authorization_id"],
            "authorization_ref": authorization_ref,
            "authorization_sha256": authorization_sha,
            "authorization_status": authorization_record["status"],
            "action_fingerprint_sha256": authorization_record["action_fingerprint"]["sha256"],
            "approval_id": approval["approval_id"],
            "approval_sha256": approval["approval_sha256"],
        },
        "preconditions": deepcopy(preconditions),
        "compare_and_set": {
            "expected_authorization_state": "UNUSED",
            "target_authorization_state": "CONSUMED",
            "expected_session_state": "NOT_RESERVED",
            "target_session_state": "RESERVED",
            "atomic_all_or_none": True,
            "durable_state_required": True,
            "process_restart_persistence_required": True,
            "consume_before_kernel_call": True,
            "reuse_after_any_attempt_allowed": False,
            "ambiguous_outcome_policy": "CONSUMED_OR_UNKNOWN_FAIL_CLOSED",
        },
        "protected_state_boundary": {
            "store_class": "PROTECTED_LOCAL_GOVERNANCE_STATE",
            "currently_implemented": False,
            "currently_enabled": False,
            "actual_compare_and_set_allowed": False,
            "actual_governance_state_write_allowed": False,
            "future_authorization_consumption_write_required": True,
            "future_session_reservation_write_required": True,
            "future_append_only_audit_write_required": True,
            "domain_write_allowed": False,
            "workspace_write_allowed": False,
            "task_source_write_allowed": False,
            "input_bundle_write_allowed": False,
            "core_write_allowed": False,
            "tool_program_state_write_allowed": False,
            "external_system_write_allowed": False,
            "financial_write_allowed": False,
        },
        "session_reservation": {
            "reservation_id": reservation_id,
            "session_id": session_id,
            "planned_state": "RESERVED",
            "start_after_atomic_commit_only": True,
            "max_runtime_seconds": limits["max_runtime_seconds"],
            "max_files_read": limits["max_files_read"],
            "max_total_bytes_read": limits["max_total_bytes_read"],
            "actual_reservation_created": False,
            "actual_session_started": False,
        },
        "audit_boundary": {
            "audit_contract_ref": "docs/runtime-contracts/AUDIT_EVENT_CONTRACT_V0.1.md",
            "append_only_required": True,
            "hash_chain_required": True,
            "required_event_types": [
                "RUNTIME_ENTRY_AUTHORIZATION_CHECKED",
                "AUTHORIZATION_CONSUMPTION_COMMITTED",
                "RUNTIME_SESSION_RESERVED",
                "RUNTIME_ENTRY_ATTEMPT_TERMINATED",
            ],
            "future_audit_write_required": True,
            "actual_audit_write_performed": False,
        },
        "decision": {
            "result": "ELIGIBLE_FOR_I0_5_4_IMPLEMENTATION_REVIEW" if eligible else "BLOCKED_NOT_ELIGIBLE",
            "blocking_reasons": blockers,
            "attempt_semantics": "AT_MOST_ONCE_ATTEMPT",
            "eligible_for_i0_5_4_implementation_review": eligible,
            "actual_compare_and_set_performed": False,
            "approval_consumed": False,
            "session_reserved": False,
            "runtime_entry_performed": False,
        },
        "runtime_effect": {
            "mode": "REVIEW_ONLY_ATOMIC_TRANSITION_PREVIEW",
            "grants_runtime_permission": False,
            "grants_runtime_activation": False,
            "consumes_approval": False,
            "performs_compare_and_set": False,
            "writes_governance_state": False,
            "reserves_runtime_session": False,
            "starts_runtime_session": False,
            "calls_kernel": False,
            "model_invocation": False,
            "tool_execution": False,
            "program_execution": False,
            "network_access": False,
            "domain_write": False,
            "workspace_write": False,
            "core_write": False,
            "external_action": False,
            "financial_action": False,
            "mutates_runtime": False,
        },
        "integrity": {
            "hash_schema": "runtime_entry_atomic_transition_preview_fingerprint_payload.v0.1",
            "transition_fingerprint_payload": payload,
            "transition_sha256": sha256_value(payload),
        },
        "created_at": created_at,
    }


def validate_atomic_transition_preview_semantics(record: dict[str, Any]) -> None:
    scan_for_secret_bearing_keys(record)
    if record.get("schema_version") != "runtime_entry_atomic_transition_preview.v0.1" or record.get("phase") != "I0.5.3":
        raise AtomicTransitionError("atomic transition schema/phase mismatch")
    if record.get("runtime_source_of_truth") is not False or record.get("owner") != "Thomas":
        raise AtomicTransitionError("atomic transition ownership/source boundary mismatch")
    if record.get("record_scope") != "SYNTHETIC_TEST_ONLY":
        raise AtomicTransitionError("I0.5.3 eligible transition previews are synthetic fixtures only")
    preconditions = record.get("preconditions", {})
    blockers = _precondition_blockers(preconditions)
    eligible = not blockers
    expected_status = "ELIGIBLE_FOR_I0_5_4_IMPLEMENTATION_REVIEW" if eligible else "BLOCKED_NOT_ELIGIBLE"
    if record.get("status") != expected_status:
        raise AtomicTransitionError("atomic transition status is not derived from preconditions")
    cas = record.get("compare_and_set", {})
    expected_cas = {
        "expected_authorization_state": "UNUSED",
        "target_authorization_state": "CONSUMED",
        "expected_session_state": "NOT_RESERVED",
        "target_session_state": "RESERVED",
        "atomic_all_or_none": True,
        "durable_state_required": True,
        "process_restart_persistence_required": True,
        "consume_before_kernel_call": True,
        "reuse_after_any_attempt_allowed": False,
        "ambiguous_outcome_policy": "CONSUMED_OR_UNKNOWN_FAIL_CLOSED",
    }
    if cas != expected_cas:
        raise AtomicTransitionError("atomic compare-and-set design mismatch")
    state = record.get("protected_state_boundary", {})
    required_true = ["future_authorization_consumption_write_required", "future_session_reservation_write_required", "future_append_only_audit_write_required"]
    required_false = ["currently_implemented", "currently_enabled", "actual_compare_and_set_allowed", "actual_governance_state_write_allowed", "domain_write_allowed", "workspace_write_allowed", "task_source_write_allowed", "input_bundle_write_allowed", "core_write_allowed", "tool_program_state_write_allowed", "external_system_write_allowed", "financial_write_allowed"]
    if state.get("store_class") != "PROTECTED_LOCAL_GOVERNANCE_STATE" or any(state.get(k) is not True for k in required_true) or any(state.get(k) is not False for k in required_false):
        raise AtomicTransitionError("protected state boundary mismatch")
    session = record.get("session_reservation", {})
    if session.get("planned_state") != "RESERVED" or session.get("start_after_atomic_commit_only") is not True or session.get("actual_reservation_created") is not False or session.get("actual_session_started") is not False:
        raise AtomicTransitionError("session reservation preview boundary mismatch")
    for key, maximum in [("max_runtime_seconds", 60), ("max_files_read", 32), ("max_total_bytes_read", 8 * 1024 * 1024)]:
        value = session.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > maximum:
            raise AtomicTransitionError(f"session reservation {key} exceeds hard cap")
    audit = record.get("audit_boundary", {})
    if audit != {
        "audit_contract_ref": "docs/runtime-contracts/AUDIT_EVENT_CONTRACT_V0.1.md",
        "append_only_required": True,
        "hash_chain_required": True,
        "required_event_types": [
            "RUNTIME_ENTRY_AUTHORIZATION_CHECKED",
            "AUTHORIZATION_CONSUMPTION_COMMITTED",
            "RUNTIME_SESSION_RESERVED",
            "RUNTIME_ENTRY_ATTEMPT_TERMINATED",
        ],
        "future_audit_write_required": True,
        "actual_audit_write_performed": False,
    }:
        raise AtomicTransitionError("Audit Event v0.1 linkage boundary mismatch")
    decision = record.get("decision", {})
    if decision.get("result") != expected_status or decision.get("blocking_reasons") != blockers or decision.get("eligible_for_i0_5_4_implementation_review") is not eligible:
        raise AtomicTransitionError("atomic transition decision mismatch")
    if decision.get("attempt_semantics") != "AT_MOST_ONCE_ATTEMPT" or any(decision.get(k) is not False for k in ["actual_compare_and_set_performed", "approval_consumed", "session_reserved", "runtime_entry_performed"]):
        raise AtomicTransitionError("I0.5.3 cannot perform atomic transition or Runtime entry")
    effect = record.get("runtime_effect", {})
    if effect.get("mode") != "REVIEW_ONLY_ATOMIC_TRANSITION_PREVIEW" or any(v is not False for k, v in effect.items() if k != "mode"):
        raise AtomicTransitionError("atomic transition Runtime effects must remain false")
    integrity = record.get("integrity", {})
    if integrity.get("transition_sha256") != sha256_value(integrity.get("transition_fingerprint_payload")):
        raise AtomicTransitionError("atomic transition fingerprint mismatch")
