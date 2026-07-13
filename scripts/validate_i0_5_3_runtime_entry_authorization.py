#!/usr/bin/env python3
from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.read_only_kernel.integrity import sha256_record, sha256_value
from runtime.read_only_entry.planner import build_entry_plan
from runtime.read_only_entry.authorization import (
    EXPECTED_OUTPUT_SCHEMAS,
    EntryAuthorizationError,
    build_entry_authorization,
    validate_entry_authorization_semantics,
)
from runtime.read_only_entry.atomic_transition import (
    AtomicTransitionError,
    build_atomic_transition_preview,
    validate_atomic_transition_preview_semantics,
)

FIXED_NOW = "2026-07-13T10:00:00Z"
SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
SHA_D = "sha256:" + "d" * 64
SHA_E = "sha256:" + "e" * 64
SHA_F = "sha256:" + "f" * 64


def validate_schema(value: dict[str, Any], rel: str) -> None:
    import json
    schema = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value), key=lambda item: list(item.absolute_path))
    if errors:
        raise AssertionError(f"{rel}: " + "; ".join(item.message for item in errors[:5]))


def synthetic_readiness() -> dict[str, Any]:
    requirements = {"synthetic_test_only": True}
    payload = {
        "schema_version": "runtime_promotion_readiness_fingerprint_payload.v0.1",
        "readiness_id": "rpr_i053_synthetic",
        "requirements": requirements,
        "design_blocking_reasons": [],
        "activation_blocking_reasons": [],
        "created_at": FIXED_NOW,
    }
    return {
        "schema_version": "runtime_promotion_readiness.v0.1",
        "readiness_id": "rpr_i053_synthetic",
        "phase": "I0.5.1_REV3",
        "status": "REVIEW_ONLY_NOT_RUNTIME_ACTIVE",
        "requirements": requirements,
        "checks": [],
        "summary": {
            "result": "READY_FOR_THOMAS_DESIGN_DECISION",
            "blocking_reasons": [],
            "design_readiness": {
                "result": "READY_FOR_THOMAS_DESIGN_DECISION",
                "blocking_reasons": [],
                "ready_for_runtime_authoritative_design": True,
            },
            "activation_readiness": {
                "result": "READY_FOR_RUNTIME_ACTIVATION_REVIEW",
                "blocking_reasons": [],
                "ready_for_runtime_activation_review": True,
            },
            "ready_for_runtime_authoritative_design": True,
            "ready_for_runtime_activation_review": True,
            "ready_for_runtime_activation": False,
            "ready_for_external_execution": False,
            "ready_for_financial_execution": False,
        },
        "runtime_effect": {
            "grants_runtime_permission": False,
            "grants_runtime_activation": False,
            "grants_core_activation": False,
            "grants_tool_enablement": False,
            "grants_program_enablement": False,
            "grants_executor_enablement": False,
            "grants_external_execution": False,
            "grants_financial_execution": False,
            "consumes_approval": False,
            "mutates_runtime": False,
        },
        "integrity": {
            "hash_schema": "runtime_promotion_readiness_fingerprint_payload.v0.1",
            "readiness_fingerprint_payload": payload,
            "readiness_sha256": sha256_value(payload),
        },
        "created_at": FIXED_NOW,
    }


def synthetic_entry_plan() -> dict[str, Any]:
    return build_entry_plan(
        synthetic_readiness(),
        readiness_ref="synthetic:test:i0.5.3-readiness",
        created_at=FIXED_NOW,
    )


def values(approved: bool) -> dict[str, Any]:
    issued = datetime.fromisoformat(FIXED_NOW.replace("Z", "+00:00"))
    expires = issued + timedelta(minutes=10)
    return {
        "design_decision": {
            "decision_id": "design_i053_synthetic",
            "decision_ref": "synthetic:design-decision",
            "status": "THOMAS_APPROVED" if approved else "PENDING_THOMAS_DECISION",
            "approved_by": "Thomas" if approved else None,
            "verification_status": "VERIFIED_BY_PROTECTED_REVIEW" if approved else "NOT_VERIFIED",
            "verification_ref": "synthetic:protected-review" if approved else None,
            "approves_read_only_runtime_foundation": approved,
            "approves_single_attempt": approved,
            "approves_exact_hash_binding": approved,
            "keeps_prohibited_effects_disabled": approved,
            "grants_runtime_activation": False,
            "grants_runtime_entry_permission": False,
        },
        "exact_bindings": {
            "task": {"task_id": "task_i053", "task_revision": 1, "sha256": SHA_A},
            "input_bundle": {"input_bundle_id": "bundle_i053", "sha256": SHA_B},
            "current_core": {"release_id": "thomas-core-v0.2.1-synthetic", "core_bundle_sha256": SHA_C},
            "core_context_binding": {"core_context_binding_id": "ccb_i053", "sha256": SHA_D},
        },
        "component_bindings": {
            "kernel": {"component_id": "thomas.read_only_runtime_kernel", "version": "0.1.1", "implementation_sha256": SHA_A},
            "entry_planner": {"component_id": "thomas.runtime_authoritative_read_only_entry.planner", "version": "0.1.0", "implementation_sha256": SHA_B},
            "entry_adapter": {"component_id": "thomas.runtime_authoritative_read_only_entry.disabled", "version": "0.1.0", "implementation_sha256": SHA_C},
        },
        "nonce_sha256": SHA_E,
        "resource_limits": {
            "issued_at": issued.isoformat().replace("+00:00", "Z"),
            "expires_at": expires.isoformat().replace("+00:00", "Z"),
            "ttl_seconds": 600,
            "max_runtime_seconds": 30,
            "max_files_read": 12,
            "max_total_bytes_read": 2_000_000,
        },
        "allowed_read_paths": [
            "examples/read_only_runtime/input/task_v0.3_contract_inspection.yaml",
            "examples/read_only_runtime/input/read_only_runtime_input_bundle_v0.1.yaml",
            "THOMAS_CORE/CURRENT_CORE_RELEASE.yaml",
            "examples/read_only_runtime/input/core_context_binding_v0.3.yaml",
        ],
    }


def build(approved: bool) -> dict[str, Any]:
    v = values(approved)
    return build_entry_authorization(
        synthetic_entry_plan(),
        entry_plan_ref="synthetic:entry-plan",
        design_decision=v["design_decision"],
        exact_bindings=v["exact_bindings"],
        component_bindings=v["component_bindings"],
        nonce_sha256=v["nonce_sha256"],
        resource_limits=v["resource_limits"],
        allowed_read_paths=v["allowed_read_paths"],
        expected_output_schemas=EXPECTED_OUTPUT_SCHEMAS,
        created_at=FIXED_NOW,
    )


def approved_review_record() -> dict[str, Any]:
    record = build(True)
    record["record_scope"] = "SYNTHETIC_TEST_ONLY"
    record["status"] = "APPROVED_NOT_CONSUMED_REVIEW_ONLY"
    record["action_approval"].update({
        "approval_id": "approval_i053_synthetic",
        "approval_ref": "synthetic:approval",
        "approval_sha256": SHA_F,
        "approval_status": "APPROVED_NOT_CONSUMED",
        "approval_verified": True,
        "consumption_state": "UNUSED",
    })
    record["decision"].update({
        "result": "APPROVED_NOT_CONSUMED_REVIEW_ONLY",
        "ready_for_atomic_transition_review": True,
    })
    payload = {
        "schema_version": "runtime_entry_authorization_fingerprint_payload.v0.1",
        "authorization_id": record["authorization_id"],
        "entry_plan_sha256": record["entry_plan"]["entry_plan_sha256"],
        "design_decision_sha256": record["design_decision"]["decision_sha256"],
        "action_fingerprint_sha256": record["action_fingerprint"]["sha256"],
        "status": record["status"],
        "created_at": record["created_at"],
    }
    record["integrity"]["record_fingerprint_payload"] = payload
    record["integrity"]["record_sha256"] = sha256_value(payload)
    return record


def set_path(record: Any, dotted: str, value: Any) -> None:
    current = record
    parts = dotted.split(".")
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    last = parts[-1]
    if isinstance(current, list):
        current[int(last)] = value
    else:
        current[last] = value


def static_review() -> None:
    forbidden_imports = {"requests", "httpx", "aiohttp", "socket", "subprocess", "openai", "anthropic"}
    forbidden_calls = {"write_text", "write_bytes", "unlink", "rename", "mkdir", "rmdir", "touch", "chmod", "symlink_to", "hardlink_to"}
    for path in [ROOT / "runtime/read_only_entry/authorization.py", ROOT / "runtime/read_only_entry/atomic_transition.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in forbidden_imports:
                        raise AssertionError(f"{path}: forbidden import {alias.name}")
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in forbidden_imports:
                raise AssertionError(f"{path}: forbidden import {node.module}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in forbidden_calls:
                raise AssertionError(f"{path}: forbidden mutating call {node.func.attr}")


def negative_authorization_cases(base: dict[str, Any]) -> int:
    cases = [
        ("runtime_source", "runtime_source_of_truth", True),
        ("wrong_owner", "owner", "Other"),
        ("bad_scope", "record_scope", "RUNTIME_RECORD"),
        ("design_decision_hash", "design_decision.decision_sha256", "sha256:"+"0"*64),
        ("design_grants_activation", "design_decision.grants_runtime_activation", True),
        ("task_revision", "exact_bindings.task.task_revision", 0),
        ("task_hash", "exact_bindings.task.sha256", "sha256:" + "0"*63),
        ("bundle_hash", "exact_bindings.input_bundle.sha256", "bad"),
        ("core_hash", "exact_bindings.current_core.core_bundle_sha256", "bad"),
        ("binding_hash", "exact_bindings.core_context_binding.sha256", "bad"),
        ("kernel_id", "component_bindings.kernel.component_id", "wrong"),
        ("kernel_version", "component_bindings.kernel.version", "9.9.9"),
        ("kernel_impl", "component_bindings.kernel.implementation_sha256", "bad"),
        ("planner_id", "component_bindings.entry_planner.component_id", "wrong"),
        ("adapter_id", "component_bindings.entry_adapter.component_id", "wrong"),
        ("attempts", "one_time_boundary.max_attempts", 2),
        ("semantics", "one_time_boundary.attempt_semantics", "EXACTLY_ONCE"),
        ("nonce_plaintext", "one_time_boundary.plaintext_nonce_stored", True),
        ("nonce_reuse", "one_time_boundary.nonce_reuse_allowed", True),
        ("nonce_hash", "one_time_boundary.nonce_sha256", "bad"),
        ("reuse_terminal", "one_time_boundary.reuse_after_any_terminal_outcome_allowed", True),
        ("runtime_limit", "resource_limits.max_runtime_seconds", 61),
        ("files_limit", "resource_limits.max_files_read", 33),
        ("bytes_limit", "resource_limits.max_total_bytes_read", 8*1024*1024+1),
        ("ttl_claim", "resource_limits.ttl_seconds", 901),
        ("path_absolute", "allowed_read_paths.0", "/etc/passwd"),
        ("path_traversal", "allowed_read_paths.0", "../secret"),
        ("path_wildcard", "allowed_read_paths.0", "examples/**"),
        ("path_windows", "allowed_read_paths.0", "C:\\temp\\file"),
        ("output_order", "expected_output_schemas.0", "audit_event.v0.1"),
        ("action_hash", "action_fingerprint.sha256", "sha256:"+"0"*64),
        ("scope", "action_approval.permission_scope", "INTERNAL_READ"),
        ("real_consumption", "action_approval.current_contract_real_consumption_supported", True),
        ("atomic_not_required", "action_approval.i0_5_3_atomic_transition_required", False),
        ("usable", "decision.usable_for_runtime_entry", True),
        ("performed", "decision.runtime_entry_performed", True),
        ("grant_activation", "runtime_effect.grants_runtime_activation", True),
        ("cas", "runtime_effect.performs_compare_and_set", True),
        ("state_write", "runtime_effect.governance_state_write", True),
        ("kernel_call", "runtime_effect.calls_kernel", True),
        ("bad_record_hash", "integrity.record_sha256", "sha256:"+"0"*64),
    ]
    for case_id, path, value in cases:
        record = deepcopy(base)
        set_path(record, path, value)
        try:
            validate_entry_authorization_semantics(record)
        except EntryAuthorizationError:
            continue
        raise AssertionError(f"{case_id}: expected EntryAuthorizationError")
    return len(cases)


def negative_transition_cases(base: dict[str, Any]) -> int:
    cases = [
        ("runtime_source", "runtime_source_of_truth", True),
        ("record_scope", "record_scope", "REAL_REVIEW_RECORD"),
        ("wrong_status", "status", "BLOCKED_NOT_ELIGIBLE"),
        ("atomic_false", "compare_and_set.atomic_all_or_none", False),
        ("durable_false", "compare_and_set.durable_state_required", False),
        ("persistence_false", "compare_and_set.process_restart_persistence_required", False),
        ("consume_after", "compare_and_set.consume_before_kernel_call", False),
        ("reuse", "compare_and_set.reuse_after_any_attempt_allowed", True),
        ("wrong_expected_auth", "compare_and_set.expected_authorization_state", "CONSUMED"),
        ("wrong_target_auth", "compare_and_set.target_authorization_state", "UNUSED"),
        ("wrong_expected_session", "compare_and_set.expected_session_state", "RESERVED"),
        ("wrong_target_session", "compare_and_set.target_session_state", "NOT_RESERVED"),
        ("ambiguous_retry", "compare_and_set.ambiguous_outcome_policy", "RETRY"),
        ("store_implemented", "protected_state_boundary.currently_implemented", True),
        ("store_enabled", "protected_state_boundary.currently_enabled", True),
        ("cas_allowed", "protected_state_boundary.actual_compare_and_set_allowed", True),
        ("state_write_allowed", "protected_state_boundary.actual_governance_state_write_allowed", True),
        ("domain_write", "protected_state_boundary.domain_write_allowed", True),
        ("workspace_write", "protected_state_boundary.workspace_write_allowed", True),
        ("task_write", "protected_state_boundary.task_source_write_allowed", True),
        ("bundle_write", "protected_state_boundary.input_bundle_write_allowed", True),
        ("core_write", "protected_state_boundary.core_write_allowed", True),
        ("external_write", "protected_state_boundary.external_system_write_allowed", True),
        ("financial_write", "protected_state_boundary.financial_write_allowed", True),
        ("session_before_commit", "session_reservation.start_after_atomic_commit_only", False),
        ("reservation_created", "session_reservation.actual_reservation_created", True),
        ("session_started", "session_reservation.actual_session_started", True),
        ("audit_contract", "audit_boundary.audit_contract_ref", "wrong"),
        ("audit_append_only", "audit_boundary.append_only_required", False),
        ("audit_hash_chain", "audit_boundary.hash_chain_required", False),
        ("audit_actual_write", "audit_boundary.actual_audit_write_performed", True),
        ("exactly_once", "decision.attempt_semantics", "EXACTLY_ONCE"),
        ("actual_cas", "decision.actual_compare_and_set_performed", True),
        ("approval_consumed", "decision.approval_consumed", True),
        ("session_reserved", "decision.session_reserved", True),
        ("entry_performed", "decision.runtime_entry_performed", True),
        ("effect_cas", "runtime_effect.performs_compare_and_set", True),
        ("effect_write", "runtime_effect.writes_governance_state", True),
        ("effect_session", "runtime_effect.starts_runtime_session", True),
        ("effect_kernel", "runtime_effect.calls_kernel", True),
        ("bad_hash", "integrity.transition_sha256", "sha256:"+"0"*64),
    ]
    for case_id, path, value in cases:
        record = deepcopy(base)
        set_path(record, path, value)
        try:
            validate_atomic_transition_preview_semantics(record)
        except AtomicTransitionError:
            continue
        raise AssertionError(f"{case_id}: expected AtomicTransitionError")
    return len(cases)


def main() -> int:
    static_review()
    blocked = build(False)
    ready = build(True)
    approved = approved_review_record()
    validate_entry_authorization_semantics(blocked)
    validate_entry_authorization_semantics(ready)
    validate_entry_authorization_semantics(approved)
    validate_schema(blocked, "schemas/runtime_entry_authorization.v0.1.schema.json")
    validate_schema(ready, "schemas/runtime_entry_authorization.v0.1.schema.json")
    validate_schema(approved, "schemas/runtime_entry_authorization.v0.1.schema.json")
    assert blocked["status"] == "BLOCKED_NOT_READY"
    assert ready["status"] == "READY_FOR_THOMAS_ACTION_APPROVAL_REVIEW"
    assert approved["status"] == "APPROVED_NOT_CONSUMED_REVIEW_ONLY"

    all_true = {key: True for key in [
        "exact_bindings_verified", "component_bindings_verified", "action_fingerprint_verified",
        "approval_verified", "approval_not_expired", "approval_not_revoked", "authorization_unused",
        "nonce_unseen", "ttl_valid", "resource_limits_within_task_budget", "kill_switch_allows_entry",
        "runtime_boundary_still_read_only",
    ]}
    eligible = build_atomic_transition_preview(approved, authorization_ref="synthetic:approved-authorization", preconditions=all_true, created_at=FIXED_NOW)
    blocked_transition = build_atomic_transition_preview(approved, authorization_ref="synthetic:approved-authorization", preconditions={**all_true, "nonce_unseen": False}, created_at=FIXED_NOW)
    validate_atomic_transition_preview_semantics(eligible)
    validate_atomic_transition_preview_semantics(blocked_transition)
    validate_schema(eligible, "schemas/runtime_entry_atomic_transition_preview.v0.1.schema.json")
    validate_schema(blocked_transition, "schemas/runtime_entry_atomic_transition_preview.v0.1.schema.json")
    assert eligible["status"] == "ELIGIBLE_FOR_I0_5_4_IMPLEMENTATION_REVIEW"
    assert blocked_transition["status"] == "BLOCKED_NOT_ELIGIBLE"

    examples = [
        ("examples/runtime_entry_authorization/runtime_entry_authorization_blocked_pending_design_decision_v0.1.yaml", "schemas/runtime_entry_authorization.v0.1.schema.json", validate_entry_authorization_semantics),
        ("examples/runtime_entry_authorization/runtime_entry_authorization_ready_for_thomas_action_approval_review_v0.1.yaml", "schemas/runtime_entry_authorization.v0.1.schema.json", validate_entry_authorization_semantics),
        ("examples/runtime_entry_authorization/SYNTHETIC_ONLY_runtime_entry_authorization_approved_not_consumed_v0.1.yaml", "schemas/runtime_entry_authorization.v0.1.schema.json", validate_entry_authorization_semantics),
        ("examples/runtime_entry_authorization/runtime_entry_atomic_transition_preview_blocked_nonce_seen_v0.1.yaml", "schemas/runtime_entry_atomic_transition_preview.v0.1.schema.json", validate_atomic_transition_preview_semantics),
        ("examples/runtime_entry_authorization/SYNTHETIC_ONLY_runtime_entry_atomic_transition_preview_eligible_v0.1.yaml", "schemas/runtime_entry_atomic_transition_preview.v0.1.schema.json", validate_atomic_transition_preview_semantics),
    ]
    for rel, schema, semantic in examples:
        record = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise AssertionError(f"{rel}: expected YAML object")
        validate_schema(record, schema)
        semantic(record)

    auth_neg = negative_authorization_cases(ready)
    tx_neg = negative_transition_cases(eligible)
    registry = yaml.safe_load((ROOT / "05_REGISTRIES/I0_5_3_RUNTIME_ENTRY_AUTHORIZATION_COMPONENTS_REVIEW_ONLY.yaml").read_text(encoding="utf-8"))
    assert registry["runtime_source_of_truth"] is False
    assert registry["runtime_authoritative_mode_enabled"] is False
    assert all(item["enabled"] is False for item in registry["components"])
    assert all(value is False for value in registry["review_only_effects"].values())

    print("PASS: I0.5.3 exact-entry Authorization and at-most-once atomic-transition design validation completed")
    print("Authorization records: 3 PASS")
    print("Atomic transition previews: 2 PASS")
    print(f"Authorization fail-closed mutations: {auth_neg} PASS")
    print(f"Atomic transition fail-closed mutations: {tx_neg} PASS")
    print("No real Approval was created or verified; no Approval consumption, CAS, governance-state write, Session reservation/start, Kernel call, model/Tool/Program/network/domain/workspace/Core/external/financial effect, Runtime mutation, Permission expansion, or Authority expansion occurred.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
