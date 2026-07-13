from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from runtime.read_only_kernel.integrity import scan_for_secret_bearing_keys, sha256_record, sha256_value, short_id
from .planner import EntryPlanError, validate_entry_plan_semantics

AUTHORIZATION_BUILDER_ID = "thomas.runtime_entry.authorization_builder"
AUTHORIZATION_BUILDER_VERSION = "0.1.0"
KERNEL_ID = "thomas.read_only_runtime_kernel"
KERNEL_VERSION = "0.1.1"
ENTRY_PLANNER_ID = "thomas.runtime_authoritative_read_only_entry.planner"
ENTRY_PLANNER_VERSION = "0.1.0"
ENTRY_ADAPTER_ID = "thomas.runtime_authoritative_read_only_entry.disabled"
ENTRY_ADAPTER_VERSION = "0.1.0"
MAX_TTL_SECONDS = 900
MAX_RUNTIME_SECONDS = 60
MAX_FILES_READ = 32
MAX_TOTAL_BYTES_READ = 8 * 1024 * 1024
EXPECTED_OUTPUT_SCHEMAS = [
    "read_only_runtime_run.v0.1",
    "agent_output.v0.2",
    "validation_result.v0.1",
    "audit_event.v0.1",
]


class EntryAuthorizationError(ValueError):
    pass


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception as exc:
        raise EntryAuthorizationError(f"invalid RFC3339 timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise EntryAuthorizationError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 71 and value.startswith("sha256:") and all(c in "0123456789abcdef" for c in value[7:])


def _validate_exact_path(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise EntryAuthorizationError("allowed read path must be a non-empty string")
    if "*" in value or "?" in value or "[" in value or "]" in value:
        raise EntryAuthorizationError(f"wildcards are forbidden in allowed read path: {value}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise EntryAuthorizationError(f"unsafe allowed read path: {value}")
    if "\\" in value:
        raise EntryAuthorizationError(f"allowed read path must use repository-relative POSIX form: {value}")


def _validate_entry_plan(plan: dict[str, Any]) -> None:
    try:
        validate_entry_plan_semantics(plan)
    except EntryPlanError as exc:
        raise EntryAuthorizationError(f"invalid I0.5.2 Entry Plan: {exc}") from exc
    if plan.get("status") != "READY_FOR_THOMAS_ENTRY_APPROVAL_DESIGN":
        raise EntryAuthorizationError("entry plan is not ready for Thomas Entry Approval design")
    decision = plan.get("decision", {})
    if decision.get("result") != "READY_FOR_THOMAS_ENTRY_APPROVAL_DESIGN" or decision.get("ready_for_thomas_entry_approval_design") is not True:
        raise EntryAuthorizationError("entry plan decision is not ready")

def _design_decision_payload(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "runtime_design_decision_fingerprint_payload.v0.1",
        "decision_id": decision.get("decision_id"),
        "decision_ref": decision.get("decision_ref"),
        "status": decision.get("status"),
        "approved_by": decision.get("approved_by"),
        "verification_status": decision.get("verification_status"),
        "verification_ref": decision.get("verification_ref"),
        "approves_read_only_runtime_foundation": decision.get("approves_read_only_runtime_foundation"),
        "approves_single_attempt": decision.get("approves_single_attempt"),
        "approves_exact_hash_binding": decision.get("approves_exact_hash_binding"),
        "keeps_prohibited_effects_disabled": decision.get("keeps_prohibited_effects_disabled"),
        "grants_runtime_activation": decision.get("grants_runtime_activation"),
        "grants_runtime_entry_permission": decision.get("grants_runtime_entry_permission"),
    }


def _normalize_design_decision(decision: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(decision)
    normalized.pop("hash_schema", None)
    normalized.pop("decision_fingerprint_payload", None)
    normalized.pop("decision_sha256", None)
    payload = _design_decision_payload(normalized)
    normalized["hash_schema"] = "runtime_design_decision_fingerprint_payload.v0.1"
    normalized["decision_fingerprint_payload"] = payload
    normalized["decision_sha256"] = sha256_value(payload)
    return normalized


def _validate_design_decision(decision: dict[str, Any]) -> None:
    payload = _design_decision_payload(decision)
    if decision.get("hash_schema") != "runtime_design_decision_fingerprint_payload.v0.1":
        raise EntryAuthorizationError("design decision hash schema mismatch")
    if decision.get("decision_fingerprint_payload") != payload or decision.get("decision_sha256") != sha256_value(payload):
        raise EntryAuthorizationError("design decision fingerprint mismatch")
    approved = decision.get("status") == "THOMAS_APPROVED"
    if approved:
        if decision.get("approved_by") != "Thomas" or decision.get("verification_status") != "VERIFIED_BY_PROTECTED_REVIEW":
            raise EntryAuthorizationError("approved design decision requires Thomas protected-review evidence")
        if not isinstance(decision.get("verification_ref"), str) or not decision.get("verification_ref"):
            raise EntryAuthorizationError("approved design decision requires verification_ref")
    else:
        if decision.get("status") != "PENDING_THOMAS_DECISION" or decision.get("approved_by") is not None or decision.get("verification_status") != "NOT_VERIFIED" or decision.get("verification_ref") is not None:
            raise EntryAuthorizationError("pending design decision evidence is inconsistent")


def _design_decision_ready(decision: dict[str, Any]) -> bool:
    _validate_design_decision(decision)
    return (
        decision.get("status") == "THOMAS_APPROVED"
        and decision.get("approved_by") == "Thomas"
        and decision.get("verification_status") == "VERIFIED_BY_PROTECTED_REVIEW"
        and decision.get("approves_read_only_runtime_foundation") is True
        and decision.get("approves_single_attempt") is True
        and decision.get("approves_exact_hash_binding") is True
        and decision.get("keeps_prohibited_effects_disabled") is True
        and decision.get("grants_runtime_activation") is False
        and decision.get("grants_runtime_entry_permission") is False
    )
def _validate_bindings(bindings: dict[str, Any]) -> None:
    required = {
        "task": ["task_id", "task_revision", "sha256"],
        "input_bundle": ["input_bundle_id", "sha256"],
        "current_core": ["release_id", "core_bundle_sha256"],
        "core_context_binding": ["core_context_binding_id", "sha256"],
    }
    if set(bindings) != set(required):
        raise EntryAuthorizationError("exact bindings must contain Task, Input Bundle, Current Core, and Core Context Binding only")
    for section, fields in required.items():
        value = bindings.get(section)
        if not isinstance(value, dict) or set(value) != set(fields):
            raise EntryAuthorizationError(f"invalid exact binding section: {section}")
        for field in fields:
            item = value[field]
            if field == "task_revision":
                if not isinstance(item, int) or isinstance(item, bool) or item < 1:
                    raise EntryAuthorizationError("task_revision must be a positive integer")
            elif "sha256" in field or field == "sha256":
                if not _valid_sha(item):
                    raise EntryAuthorizationError(f"invalid SHA-256 binding: {section}.{field}")
            elif not isinstance(item, str) or not item:
                raise EntryAuthorizationError(f"invalid exact binding: {section}.{field}")


def _validate_components(components: dict[str, Any]) -> None:
    expected = {
        "kernel": (KERNEL_ID, KERNEL_VERSION),
        "entry_planner": (ENTRY_PLANNER_ID, ENTRY_PLANNER_VERSION),
        "entry_adapter": (ENTRY_ADAPTER_ID, ENTRY_ADAPTER_VERSION),
    }
    if set(components) != set(expected):
        raise EntryAuthorizationError("component bindings must contain Kernel, Entry Planner, and Entry Adapter only")
    for name, (component_id, version) in expected.items():
        item = components.get(name)
        if not isinstance(item, dict) or set(item) != {"component_id", "version", "implementation_sha256"}:
            raise EntryAuthorizationError(f"invalid component binding: {name}")
        if item.get("component_id") != component_id or item.get("version") != version or not _valid_sha(item.get("implementation_sha256")):
            raise EntryAuthorizationError(f"component identity/version/hash mismatch: {name}")


def _validate_limits(limits: dict[str, Any]) -> None:
    issued = _parse_time(limits.get("issued_at"))
    expires = _parse_time(limits.get("expires_at"))
    ttl = int((expires - issued).total_seconds())
    if ttl <= 0 or ttl > MAX_TTL_SECONDS or limits.get("ttl_seconds") != ttl:
        raise EntryAuthorizationError("authorization TTL is invalid or exceeds 15 minutes")
    values = {
        "max_runtime_seconds": MAX_RUNTIME_SECONDS,
        "max_files_read": MAX_FILES_READ,
        "max_total_bytes_read": MAX_TOTAL_BYTES_READ,
    }
    for key, maximum in values.items():
        value = limits.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > maximum:
            raise EntryAuthorizationError(f"{key} exceeds the I0.5.3 hard cap")


def _action_payload(*, exact_bindings: dict[str, Any], component_bindings: dict[str, Any], one_time_boundary: dict[str, Any], resource_limits: dict[str, Any], allowed_read_paths: list[str], expected_output_schemas: list[str]) -> dict[str, Any]:
    return {
        "schema_version": "runtime_entry_action_fingerprint_payload.v0.1",
        "exact_bindings": deepcopy(exact_bindings),
        "component_bindings": deepcopy(component_bindings),
        "one_time_boundary": deepcopy(one_time_boundary),
        "resource_limits": deepcopy(resource_limits),
        "allowed_read_paths": list(allowed_read_paths),
        "expected_output_schemas": list(expected_output_schemas),
    }


def build_entry_authorization(
    entry_plan: dict[str, Any],
    *,
    entry_plan_ref: str,
    design_decision: dict[str, Any],
    exact_bindings: dict[str, Any],
    component_bindings: dict[str, Any],
    nonce_sha256: str,
    resource_limits: dict[str, Any],
    allowed_read_paths: list[str],
    expected_output_schemas: list[str],
    created_at: str,
) -> dict[str, Any]:
    scan_for_secret_bearing_keys(entry_plan)
    scan_for_secret_bearing_keys(design_decision)
    design_decision = _normalize_design_decision(design_decision)
    _validate_entry_plan(entry_plan)
    _validate_bindings(exact_bindings)
    _validate_components(component_bindings)
    _validate_limits(resource_limits)
    if not _valid_sha(nonce_sha256):
        raise EntryAuthorizationError("nonce_sha256 must be a SHA-256 value")
    if not isinstance(allowed_read_paths, list) or not allowed_read_paths or len(allowed_read_paths) > MAX_FILES_READ:
        raise EntryAuthorizationError("allowed_read_paths must be a non-empty bounded list")
    if len(allowed_read_paths) != len(set(allowed_read_paths)):
        raise EntryAuthorizationError("allowed_read_paths contains duplicates")
    for path in allowed_read_paths:
        _validate_exact_path(path)
    if expected_output_schemas != EXPECTED_OUTPUT_SCHEMAS:
        raise EntryAuthorizationError("expected output schemas must exactly match the I0.5.3 ordered set")

    decision_ready = _design_decision_ready(design_decision)
    blocking_reasons = [] if decision_ready else ["THOMAS_RUNTIME_DESIGN_DECISION_NOT_APPROVED"]
    one_time_boundary = {
        "attempt_semantics": "AT_MOST_ONCE_ATTEMPT",
        "max_attempts": 1,
        "nonce_sha256": nonce_sha256,
        "plaintext_nonce_stored": False,
        "nonce_reuse_allowed": False,
        "consume_before_session_start": True,
        "reuse_after_any_terminal_outcome_allowed": False,
    }
    action_payload = _action_payload(
        exact_bindings=exact_bindings,
        component_bindings=component_bindings,
        one_time_boundary=one_time_boundary,
        resource_limits=resource_limits,
        allowed_read_paths=allowed_read_paths,
        expected_output_schemas=expected_output_schemas,
    )
    action_sha = sha256_value(action_payload)
    seed = {"entry_plan_sha256": sha256_record(entry_plan), "action_sha256": action_sha, "created_at": created_at}
    authorization_id = short_id("reauth", seed)
    status = "READY_FOR_THOMAS_ACTION_APPROVAL_REVIEW" if decision_ready else "BLOCKED_NOT_READY"
    record_payload = {
        "schema_version": "runtime_entry_authorization_fingerprint_payload.v0.1",
        "authorization_id": authorization_id,
        "entry_plan_sha256": sha256_record(entry_plan),
        "design_decision_sha256": design_decision.get("decision_sha256"),
        "action_fingerprint_sha256": action_sha,
        "status": status,
        "created_at": created_at,
    }
    return {
        "schema_version": "runtime_entry_authorization.v0.1",
        "authorization_id": authorization_id,
        "phase": "I0.5.3",
        "status": status,
        "owner": "Thomas",
        "record_scope": "REAL_REVIEW_RECORD",
        "runtime_source_of_truth": False,
        "entry_plan": {
            "entry_plan_id": entry_plan["entry_plan_id"],
            "entry_plan_ref": entry_plan_ref,
            "entry_plan_sha256": sha256_record(entry_plan),
            "entry_plan_status": entry_plan["status"],
            "ready_for_thomas_entry_approval_design": True,
        },
        "design_decision": deepcopy(design_decision),
        "exact_bindings": deepcopy(exact_bindings),
        "component_bindings": deepcopy(component_bindings),
        "one_time_boundary": one_time_boundary,
        "resource_limits": deepcopy(resource_limits),
        "allowed_read_paths": list(allowed_read_paths),
        "expected_output_schemas": list(expected_output_schemas),
        "action_fingerprint": {
            "hash_schema": "runtime_entry_action_fingerprint_payload.v0.1",
            "fingerprint_payload": action_payload,
            "sha256": action_sha,
        },
        "action_approval": {
            "contract_ref": "docs/runtime-contracts/APPROVAL_CONTRACT_V0.1.md",
            "permission_scope": "RUNTIME_GOVERNANCE",
            "approval_id": None,
            "approval_ref": None,
            "approval_sha256": None,
            "approval_status": "NOT_PRESENT",
            "approval_verified": False,
            "consumption_state": "NOT_CONSUMED",
            "current_contract_real_consumption_supported": False,
            "i0_5_3_atomic_transition_required": True,
        },
        "decision": {
            "result": status,
            "blocking_reasons": blocking_reasons,
            "ready_for_thomas_action_approval_review": decision_ready,
            "ready_for_atomic_transition_review": False,
            "usable_for_runtime_entry": False,
            "runtime_entry_performed": False,
        },
        "runtime_effect": {
            "mode": "REVIEW_ONLY_EXACT_ENTRY_AUTHORIZATION_DESIGN",
            "grants_runtime_permission": False,
            "grants_runtime_activation": False,
            "grants_core_activation": False,
            "consumes_approval": False,
            "performs_compare_and_set": False,
            "reserves_runtime_session": False,
            "starts_runtime_session": False,
            "calls_kernel": False,
            "model_invocation": False,
            "tool_execution": False,
            "program_execution": False,
            "network_access": False,
            "governance_state_write": False,
            "domain_write": False,
            "workspace_write": False,
            "core_write": False,
            "external_action": False,
            "financial_action": False,
            "mutates_runtime": False,
        },
        "integrity": {
            "hash_schema": "runtime_entry_authorization_fingerprint_payload.v0.1",
            "record_fingerprint_payload": record_payload,
            "record_sha256": sha256_value(record_payload),
        },
        "created_at": created_at,
    }


def validate_entry_authorization_semantics(record: dict[str, Any]) -> None:
    scan_for_secret_bearing_keys(record)
    if record.get("schema_version") != "runtime_entry_authorization.v0.1" or record.get("phase") != "I0.5.3":
        raise EntryAuthorizationError("entry authorization schema/phase mismatch")
    if record.get("runtime_source_of_truth") is not False:
        raise EntryAuthorizationError("entry authorization cannot be a Runtime source of truth")
    if record.get("record_scope") not in {"REAL_REVIEW_RECORD", "SYNTHETIC_TEST_ONLY"}:
        raise EntryAuthorizationError("entry authorization record_scope is invalid")
    if record.get("owner") != "Thomas":
        raise EntryAuthorizationError("entry authorization owner must be Thomas")
    _validate_bindings(record.get("exact_bindings", {}))
    _validate_components(record.get("component_bindings", {}))
    _validate_limits(record.get("resource_limits", {}))
    one = record.get("one_time_boundary", {})
    if one != {
        "attempt_semantics": "AT_MOST_ONCE_ATTEMPT",
        "max_attempts": 1,
        "nonce_sha256": one.get("nonce_sha256"),
        "plaintext_nonce_stored": False,
        "nonce_reuse_allowed": False,
        "consume_before_session_start": True,
        "reuse_after_any_terminal_outcome_allowed": False,
    } or not _valid_sha(one.get("nonce_sha256")):
        raise EntryAuthorizationError("one-time boundary mismatch")
    paths = record.get("allowed_read_paths")
    if not isinstance(paths, list) or not paths or len(paths) > MAX_FILES_READ or len(paths) != len(set(paths)):
        raise EntryAuthorizationError("allowed read paths are invalid")
    for path in paths:
        _validate_exact_path(path)
    if record.get("expected_output_schemas") != EXPECTED_OUTPUT_SCHEMAS:
        raise EntryAuthorizationError("expected output schema set mismatch")
    design_ready = _design_decision_ready(record.get("design_decision", {}))
    approval = record.get("action_approval", {})
    approval_status = approval.get("approval_status")
    if approval.get("permission_scope") != "RUNTIME_GOVERNANCE" or approval.get("contract_ref") != "docs/runtime-contracts/APPROVAL_CONTRACT_V0.1.md":
        raise EntryAuthorizationError("Action Approval boundary mismatch")
    if approval.get("current_contract_real_consumption_supported") is not False or approval.get("i0_5_3_atomic_transition_required") is not True:
        raise EntryAuthorizationError("Approval consumption boundary mismatch")
    approved = approval_status == "APPROVED_NOT_CONSUMED"
    if approved:
        if record.get("record_scope") != "SYNTHETIC_TEST_ONLY":
            raise EntryAuthorizationError("I0.5.3 has no real Action Approval verifier; approved records are synthetic fixtures only")
        if not approval.get("approval_verified") or approval.get("consumption_state") != "UNUSED":
            raise EntryAuthorizationError("approved review record must be verified and UNUSED")
        if not all(isinstance(approval.get(k), str) and approval.get(k) for k in ["approval_id", "approval_ref"]) or not _valid_sha(approval.get("approval_sha256")):
            raise EntryAuthorizationError("approved review record requires exact Approval binding")
    else:
        if approval_status == "NOT_PRESENT":
            if any(approval.get(k) is not None for k in ["approval_id", "approval_ref", "approval_sha256"]) or approval.get("approval_verified") is not False or approval.get("consumption_state") != "NOT_CONSUMED":
                raise EntryAuthorizationError("not-present Approval fields are inconsistent")
    action = record.get("action_fingerprint", {})
    expected_action_payload = _action_payload(
        exact_bindings=record["exact_bindings"],
        component_bindings=record["component_bindings"],
        one_time_boundary=record["one_time_boundary"],
        resource_limits=record["resource_limits"],
        allowed_read_paths=record["allowed_read_paths"],
        expected_output_schemas=record["expected_output_schemas"],
    )
    if action.get("fingerprint_payload") != expected_action_payload or action.get("sha256") != sha256_value(expected_action_payload):
        raise EntryAuthorizationError("Action fingerprint mismatch")
    status = record.get("status")
    expected_status = "APPROVED_NOT_CONSUMED_REVIEW_ONLY" if approved and design_ready else ("READY_FOR_THOMAS_ACTION_APPROVAL_REVIEW" if design_ready else "BLOCKED_NOT_READY")
    if status != expected_status:
        raise EntryAuthorizationError("entry authorization status is not derived from evidence")
    decision = record.get("decision", {})
    if decision.get("result") != expected_status:
        raise EntryAuthorizationError("entry authorization decision result mismatch")
    if decision.get("ready_for_thomas_action_approval_review") is not design_ready:
        raise EntryAuthorizationError("Action Approval review readiness mismatch")
    if decision.get("ready_for_atomic_transition_review") is not approved:
        raise EntryAuthorizationError("atomic transition review readiness mismatch")
    expected_reasons = [] if design_ready else ["THOMAS_RUNTIME_DESIGN_DECISION_NOT_APPROVED"]
    if decision.get("blocking_reasons") != expected_reasons:
        raise EntryAuthorizationError("entry authorization blockers mismatch")
    if decision.get("usable_for_runtime_entry") is not False or decision.get("runtime_entry_performed") is not False:
        raise EntryAuthorizationError("I0.5.3 authorization cannot be used for Runtime entry")
    effect = record.get("runtime_effect", {})
    if effect.get("mode") != "REVIEW_ONLY_EXACT_ENTRY_AUTHORIZATION_DESIGN" or any(v is not False for k, v in effect.items() if k != "mode"):
        raise EntryAuthorizationError("entry authorization Runtime effects must remain false")
    integrity = record.get("integrity", {})
    if integrity.get("record_sha256") != sha256_value(integrity.get("record_fingerprint_payload")):
        raise EntryAuthorizationError("entry authorization fingerprint mismatch")
