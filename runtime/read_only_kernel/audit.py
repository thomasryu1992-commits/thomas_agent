from __future__ import annotations

from typing import Any

from .constants import KERNEL_ID
from .integrity import sha256_record, sha256_value, short_id


def build_transition_audit(
    *,
    task: dict[str, Any],
    assignment: dict[str, Any],
    from_state: str,
    to_state: str,
    sequence: int,
    previous_hash: str | None,
    previous_audit_id: str | None,
    now: str,
) -> dict[str, Any]:
    seed = {
        "task_id": task["identity"]["task_id"],
        "task_revision": task["identity"]["task_revision"],
        "from": from_state,
        "to": to_state,
        "sequence": sequence,
    }
    audit_id = short_id("audit", seed)
    subject_ref = f"in_memory:task:{task['identity']['task_id']}:r{task['identity']['task_revision']}"
    payload = {
        "schema_version": "audit_event_fingerprint_payload.v0.1",
        "audit_event_id": audit_id,
        "trace_id": task["identity"]["trace_id"],
        "task_id": task["identity"]["task_id"],
        "task_revision": task["identity"]["task_revision"],
        "core_context_binding_id": task["context"]["core_context_binding_id"],
        "event_type": "TASK_STATE_CHANGED",
        "actor_ref": f"system:{KERNEL_ID}",
        "subject_ref": subject_ref,
        "subject_fingerprint": sha256_record(task),
        "event_summary": f"Read-only in-memory Task transition {from_state} -> {to_state}.",
        "outcome": "RECORDED",
        "reason_codes": ["READ_ONLY_DEVELOPMENT_REPLAY"],
        "payload_sha256": None,
        "evidence_refs": ["runtime:read_only_kernel.preflight"],
        "related_record_refs": [f"in_memory:assignment:{assignment['assignment_id']}"],
        "parent_audit_event_ids": [previous_audit_id] if previous_audit_id else [],
        "previous_event_sha256": previous_hash,
        "sequence_number": sequence,
        "created_at": now,
    }
    return {
        "schema_version": "audit_event.v0.1",
        "audit_event_id": audit_id,
        "trace_id": task["identity"]["trace_id"],
        "task_id": task["identity"]["task_id"],
        "task_revision": task["identity"]["task_revision"],
        "core_context_binding_id": task["context"]["core_context_binding_id"],
        "event_type": "TASK_STATE_CHANGED",
        "actor": {
            "actor_type": "system",
            "actor_id": KERNEL_ID,
            "role_id": None,
            "role_version": None,
            "assignment_id": None,
        },
        "subject": {
            "subject_type": "TASK",
            "subject_id": task["identity"]["task_id"],
            "subject_ref": subject_ref,
            "subject_fingerprint": sha256_record(task),
        },
        "event": {
            "event_summary": payload["event_summary"],
            "outcome": "RECORDED",
            "reason_codes": ["READ_ONLY_DEVELOPMENT_REPLAY"],
            "payload_ref": None,
            "payload_sha256": None,
            "evidence_refs": ["runtime:read_only_kernel.preflight"],
            "related_record_refs": [f"in_memory:assignment:{assignment['assignment_id']}"],
        },
        "lineage": {
            "parent_audit_event_ids": payload["parent_audit_event_ids"],
            "previous_event_sha256": previous_hash,
            "sequence_number": sequence,
        },
        "integrity": {
            "hash_schema": "audit_event_fingerprint_payload.v0.1",
            "event_fingerprint_payload": payload,
            "event_sha256": sha256_value(payload),
            "append_only": True,
            "overwrite_allowed": False,
            "delete_allowed": False,
        },
        "sensitivity": task["context"]["data_sensitivity"],
        "runtime_effect": {
            "mode": "EVIDENCE_ONLY",
            "grants_permission": False,
            "grants_approval": False,
            "grants_authority": False,
            "grants_execution": False,
            "grants_activation": False,
            "mutates_runtime": False,
        },
        "created_at": now,
    }


def build_validation_audit(
    *,
    task: dict[str, Any],
    assignment: dict[str, Any],
    validation_result: dict[str, Any],
    validation_fingerprint: str,
    sequence: int,
    previous_hash: str | None,
    previous_audit_id: str | None,
    now: str,
) -> dict[str, Any]:
    seed = {
        "validation_result_id": validation_result["validation_result_id"],
        "sequence": sequence,
    }
    audit_id = short_id("audit", seed)
    subject_ref = f"in_memory:{validation_result['validation_result_id']}"
    payload = {
        "schema_version": "audit_event_fingerprint_payload.v0.1",
        "audit_event_id": audit_id,
        "trace_id": task["identity"]["trace_id"],
        "task_id": task["identity"]["task_id"],
        "task_revision": task["identity"]["task_revision"],
        "core_context_binding_id": task["context"]["core_context_binding_id"],
        "event_type": "VALIDATION_COMPLETED",
        "actor_ref": "system:i0_5.read_only_runtime.contract_validator",
        "subject_ref": subject_ref,
        "subject_fingerprint": validation_fingerprint,
        "event_summary": "Automatic read-only contract and lineage validation passed.",
        "outcome": "PASS",
        "reason_codes": ["READ_ONLY_VALIDATION_PASS"],
        "payload_sha256": validation_fingerprint,
        "evidence_refs": [subject_ref],
        "related_record_refs": [f"in_memory:assignment:{assignment['assignment_id']}"],
        "parent_audit_event_ids": [previous_audit_id] if previous_audit_id else [],
        "previous_event_sha256": previous_hash,
        "sequence_number": sequence,
        "created_at": now,
    }
    return {
        "schema_version": "audit_event.v0.1",
        "audit_event_id": audit_id,
        "trace_id": task["identity"]["trace_id"],
        "task_id": task["identity"]["task_id"],
        "task_revision": task["identity"]["task_revision"],
        "core_context_binding_id": task["context"]["core_context_binding_id"],
        "event_type": "VALIDATION_COMPLETED",
        "actor": {
            "actor_type": "system",
            "actor_id": "i0_5.read_only_runtime.contract_validator",
            "role_id": None,
            "role_version": None,
            "assignment_id": None,
        },
        "subject": {
            "subject_type": "VALIDATION_RESULT",
            "subject_id": validation_result["validation_result_id"],
            "subject_ref": subject_ref,
            "subject_fingerprint": validation_fingerprint,
        },
        "event": {
            "event_summary": payload["event_summary"],
            "outcome": "PASS",
            "reason_codes": ["READ_ONLY_VALIDATION_PASS"],
            "payload_ref": subject_ref,
            "payload_sha256": validation_fingerprint,
            "evidence_refs": [subject_ref],
            "related_record_refs": [f"in_memory:assignment:{assignment['assignment_id']}"],
        },
        "lineage": {
            "parent_audit_event_ids": payload["parent_audit_event_ids"],
            "previous_event_sha256": previous_hash,
            "sequence_number": sequence,
        },
        "integrity": {
            "hash_schema": "audit_event_fingerprint_payload.v0.1",
            "event_fingerprint_payload": payload,
            "event_sha256": sha256_value(payload),
            "append_only": True,
            "overwrite_allowed": False,
            "delete_allowed": False,
        },
        "sensitivity": task["context"]["data_sensitivity"],
        "runtime_effect": {
            "mode": "EVIDENCE_ONLY",
            "grants_permission": False,
            "grants_approval": False,
            "grants_authority": False,
            "grants_execution": False,
            "grants_activation": False,
            "mutates_runtime": False,
        },
        "created_at": now,
    }
