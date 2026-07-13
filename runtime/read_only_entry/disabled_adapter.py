from __future__ import annotations

from typing import Any

from runtime.read_only_kernel.integrity import scan_for_secret_bearing_keys, sha256_record, sha256_value, short_id
from .planner import validate_entry_plan_semantics

ADAPTER_ID = "thomas.runtime_authoritative_read_only_entry.disabled"
ADAPTER_VERSION = "0.1.0"


class DisabledEntryAdapterError(ValueError):
    pass


def build_disabled_entry_evidence(plan: dict[str, Any], *, plan_ref: str, created_at: str) -> dict[str, Any]:
    scan_for_secret_bearing_keys(plan)
    validate_entry_plan_semantics(plan)
    plan_sha256 = sha256_record(plan)
    seed = {"plan_sha256": plan_sha256, "adapter_id": ADAPTER_ID, "adapter_version": ADAPTER_VERSION, "created_at": created_at}
    evidence_id = short_id("entryev", seed)
    payload = {
        "schema_version": "disabled_runtime_authoritative_read_only_entry_evidence_fingerprint_payload.v0.1",
        "evidence_id": evidence_id,
        "entry_plan_id": plan["entry_plan_id"],
        "entry_plan_sha256": plan_sha256,
        "adapter_id": ADAPTER_ID,
        "adapter_version": ADAPTER_VERSION,
        "result": "BLOCKED_DISABLED_ENTRY_ADAPTER",
        "created_at": created_at,
    }
    return {
        "schema_version": "disabled_runtime_authoritative_read_only_entry_evidence.v0.1",
        "evidence_id": evidence_id,
        "phase": "I0.5.2",
        "status": "BLOCKED_DISABLED_ENTRY_ADAPTER",
        "entry_plan": {
            "entry_plan_id": plan["entry_plan_id"],
            "entry_plan_ref": plan_ref,
            "entry_plan_sha256": plan_sha256,
            "entry_plan_decision": plan["decision"]["result"],
        },
        "adapter": {
            "adapter_id": ADAPTER_ID,
            "adapter_version": ADAPTER_VERSION,
            "implementation_available": True,
            "enabled": False,
            "runtime_source_of_truth": False,
            "entry_call_allowed": False,
        },
        "checks": [
            {"check_id": "entry_plan_semantics", "result": "PASS", "notes": "Entry Plan passed review-only semantic validation."},
            {"check_id": "adapter_disabled", "result": "PASS", "notes": "The adapter is implemented only to emit disabled evidence and remains disabled."},
            {"check_id": "no_session_start", "result": "PASS", "notes": "No Runtime-authoritative session can be started in I0.5.2."},
        ],
        "decision": {
            "result": "BLOCKED_DISABLED_ENTRY_ADAPTER",
            "reason_codes": ["RUNTIME_AUTHORITATIVE_ENTRY_ADAPTER_DISABLED", "SEPARATE_ACTION_APPROVAL_AND_FUTURE_CONSUMPTION_REQUIRED"],
            "entry_performed": False,
            "runtime_authoritative_session_started": False,
            "executor_handoff_performed": False,
            "approval_consumed": False,
        },
        "runtime_effect": {
            "mode": "DISABLED_EVIDENCE_ONLY",
            "filesystem_write_performed": False,
            "model_invocation_performed": False,
            "tool_execution_performed": False,
            "program_execution_performed": False,
            "network_call_performed": False,
            "external_action_performed": False,
            "financial_action_performed": False,
            "runtime_mutation_performed": False,
            "approval_consumption_performed": False,
            "runtime_session_started": False,
            "executor_handoff_performed": False,
            "permission_expansion_performed": False,
            "authority_expansion_performed": False,
            "core_activation_performed": False,
        },
        "integrity": {
            "hash_schema": "disabled_runtime_authoritative_read_only_entry_evidence_fingerprint_payload.v0.1",
            "evidence_fingerprint_payload": payload,
            "evidence_sha256": sha256_value(payload),
        },
        "created_at": created_at,
    }


def validate_disabled_entry_evidence_semantics(record: dict[str, Any]) -> None:
    scan_for_secret_bearing_keys(record)
    if record.get("schema_version") != "disabled_runtime_authoritative_read_only_entry_evidence.v0.1":
        raise DisabledEntryAdapterError("disabled entry evidence schema mismatch")
    if record.get("status") != "BLOCKED_DISABLED_ENTRY_ADAPTER":
        raise DisabledEntryAdapterError("disabled entry evidence status mismatch")
    adapter = record.get("adapter", {})
    if adapter.get("adapter_id") != ADAPTER_ID or adapter.get("adapter_version") != ADAPTER_VERSION:
        raise DisabledEntryAdapterError("disabled adapter identity/version mismatch")
    if adapter.get("implementation_available") is not True:
        raise DisabledEntryAdapterError("disabled adapter implementation evidence must be explicit")
    for key in ["enabled", "runtime_source_of_truth", "entry_call_allowed"]:
        if adapter.get(key) is not False:
            raise DisabledEntryAdapterError(f"disabled adapter boundary violated: {key}")
    decision = record.get("decision", {})
    if decision.get("result") != "BLOCKED_DISABLED_ENTRY_ADAPTER":
        raise DisabledEntryAdapterError("disabled adapter decision must block")
    for key in ["entry_performed", "runtime_authoritative_session_started", "executor_handoff_performed", "approval_consumed"]:
        if decision.get(key) is not False:
            raise DisabledEntryAdapterError(f"disabled adapter decision effect must remain false: {key}")
    effects = record.get("runtime_effect", {})
    for key, value in effects.items():
        if key == "mode":
            if value != "DISABLED_EVIDENCE_ONLY":
                raise DisabledEntryAdapterError("disabled adapter Runtime effect mode mismatch")
        elif value is not False:
            raise DisabledEntryAdapterError(f"disabled adapter Runtime effect must remain false: {key}")
    integrity = record.get("integrity", {})
    if integrity.get("evidence_sha256") != sha256_value(integrity.get("evidence_fingerprint_payload")):
        raise DisabledEntryAdapterError("disabled entry evidence fingerprint mismatch")
