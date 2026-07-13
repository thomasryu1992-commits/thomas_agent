from __future__ import annotations

import json
from contextlib import closing
import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from runtime.read_only_kernel.integrity import sha256_value
from .sqlite_store import (
    ProtectedGovernanceStateStore,
    ProtectedStateError,
    RECORD_SCOPE,
)

RECOVERY_COMPONENT_ID = "thomas.runtime_entry.crash_recovery.inspector"
RECOVERY_COMPONENT_VERSION = "0.1.0"


def inspect_recovery_state(
    store: ProtectedGovernanceStateStore,
    *,
    created_at: str,
) -> dict[str, Any]:
    _parse_time(created_at)
    if not store.path.exists():
        raise ProtectedStateError("protected governance state database is not initialized")

    anomalies: list[dict[str, Any]] = []
    manual_review_sessions: list[dict[str, Any]] = []
    with closing(store._connect()) as connection:  # internal inspector for the same candidate component
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        authorizations = [dict(row) for row in connection.execute(
            "SELECT * FROM authorizations ORDER BY authorization_id"
        ).fetchall()]
        sessions = [dict(row) for row in connection.execute(
            "SELECT * FROM sessions ORDER BY session_id"
        ).fetchall()]
        receipts = [dict(row) for row in connection.execute(
            "SELECT * FROM transition_receipts ORDER BY transition_id"
        ).fetchall()]
        audit_rows = connection.execute(
            "SELECT sequence_number, event_record_json FROM audit_events ORDER BY sequence_number"
        ).fetchall()
        audit = []
        for row in audit_rows:
            record = json.loads(row["event_record_json"])
            if record.get("lineage", {}).get("sequence_number") != row["sequence_number"]:
                anomalies.append({"code": "AUDIT_ROW_SEQUENCE_MISMATCH"})
            audit.append(record)

    by_authorization = {item["authorization_id"]: item for item in authorizations}
    by_session = {item["session_id"]: item for item in sessions}

    for authorization in authorizations:
        state = authorization["state"]
        session_id = authorization["session_id"]
        if state == "UNUSED" and session_id is not None:
            anomalies.append({
                "code": "UNUSED_AUTHORIZATION_HAS_SESSION",
                "authorization_id": authorization["authorization_id"],
                "session_id": session_id,
            })
        if state in {"CONSUMED", "CONSUMED_OR_UNKNOWN_FAIL_CLOSED"}:
            if not session_id or session_id not in by_session:
                anomalies.append({
                    "code": "CONSUMED_AUTHORIZATION_MISSING_SESSION",
                    "authorization_id": authorization["authorization_id"],
                    "session_id": session_id,
                })
            else:
                session = by_session[session_id]
                if session["authorization_id"] != authorization["authorization_id"]:
                    anomalies.append({
                        "code": "AUTHORIZATION_SESSION_LINK_MISMATCH",
                        "authorization_id": authorization["authorization_id"],
                        "session_id": session_id,
                    })
                if session["state"] in {"RESERVED", "UNKNOWN_FAIL_CLOSED"}:
                    manual_review_sessions.append({
                        "authorization_id": authorization["authorization_id"],
                        "authorization_state": state,
                        "session_id": session_id,
                        "session_state": session["state"],
                        "reuse_allowed": False,
                        "auto_resume_allowed": False,
                    })

    for session in sessions:
        authorization = by_authorization.get(session["authorization_id"])
        if authorization is None:
            anomalies.append({
                "code": "SESSION_MISSING_AUTHORIZATION",
                "authorization_id": session["authorization_id"],
                "session_id": session["session_id"],
            })
        elif authorization["session_id"] != session["session_id"]:
            anomalies.append({
                "code": "SESSION_AUTHORIZATION_LINK_MISMATCH",
                "authorization_id": session["authorization_id"],
                "session_id": session["session_id"],
            })

    audit_error = _validate_audit_chain(audit)
    if audit_error:
        anomalies.append({"code": audit_error})

    receipt_pairs = {(item["authorization_id"], item["session_id"]) for item in receipts}
    for session in sessions:
        if (session["authorization_id"], session["session_id"]) not in receipt_pairs:
            anomalies.append({
                "code": "SESSION_MISSING_DURABLE_TRANSITION_RECEIPT",
                "authorization_id": session["authorization_id"],
                "session_id": session["session_id"],
            })

    if integrity != "ok":
        status = "FAIL_CLOSED_INCONSISTENT_STATE"
        blockers = ["SQLITE_INTEGRITY_CHECK_FAILED"]
    elif anomalies:
        status = "FAIL_CLOSED_INCONSISTENT_STATE"
        blockers = [item["code"] for item in anomalies]
    elif manual_review_sessions:
        status = "MANUAL_REVIEW_REQUIRED_NO_REUSE"
        blockers = ["CONSUMED_AUTHORIZATION_WITH_RESERVED_SESSION"]
    else:
        status = "CLEAN_NO_PENDING_SESSION"
        blockers = []

    payload = {
        "schema_version": "runtime_entry_recovery_report_fingerprint_payload.v0.1",
        "store_id": store.store_id,
        "status": status,
        "blocking_reasons": blockers,
        "anomalies": deepcopy(anomalies),
        "manual_review_sessions": deepcopy(manual_review_sessions),
        "created_at": created_at,
    }
    return {
        "schema_version": "runtime_entry_recovery_report.v0.1",
        "recovery_report_id": "recovery_" + sha256_value(payload)[7:27],
        "phase": "I0.5.4",
        "status": status,
        "owner": "Thomas",
        "record_scope": RECORD_SCOPE,
        "runtime_source_of_truth": False,
        "store_binding": {
            "store_id": store.store_id,
            "backend": "SQLITE",
            "recovery_component_id": RECOVERY_COMPONENT_ID,
            "recovery_component_version": RECOVERY_COMPONENT_VERSION,
        },
        "integrity_check": {
            "sqlite_integrity_check": "PASS" if integrity == "ok" else "FAIL",
            "audit_hash_chain": "PASS" if not audit_error else "FAIL",
            "authorization_session_linkage": "PASS" if not anomalies else "FAIL",
        },
        "counts": {
            "authorizations": len(authorizations),
            "sessions": len(sessions),
            "transition_receipts": len(receipts),
            "audit_events": len(audit),
            "anomalies": len(anomalies),
            "manual_review_sessions": len(manual_review_sessions),
        },
        "anomalies": anomalies,
        "manual_review_sessions": manual_review_sessions,
        "decision": {
            "result": status,
            "blocking_reasons": blockers,
            "authorization_reuse_allowed": False,
            "automatic_session_resume_allowed": False,
            "manual_review_required": status != "CLEAN_NO_PENDING_SESSION",
            "new_thomas_approval_required_for_retry": status != "CLEAN_NO_PENDING_SESSION",
            "runtime_entry_allowed": False,
        },
        "runtime_effect": {
            "mode": "READ_ONLY_RECOVERY_INSPECTION",
            "writes_protected_state": False,
            "consumes_approval": False,
            "reserves_session": False,
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
        },
        "integrity": {
            "hash_schema": "runtime_entry_recovery_report_fingerprint_payload.v0.1",
            "recovery_fingerprint_payload": payload,
            "recovery_sha256": sha256_value(payload),
        },
        "created_at": created_at,
    }


def _validate_audit_chain(audit: list[dict[str, Any]]) -> str | None:
    previous_id = None
    previous_sha = None
    for expected_sequence, event in enumerate(audit, start=1):
        if event.get("schema_version") != "audit_event.v0.1":
            return "AUDIT_EVENT_SCHEMA_MISMATCH"
        lineage = event.get("lineage", {})
        integrity = event.get("integrity", {})
        if lineage.get("sequence_number") != expected_sequence:
            return "AUDIT_SEQUENCE_GAP_OR_REORDER"
        if lineage.get("previous_event_sha256") != previous_sha:
            return "AUDIT_PREVIOUS_HASH_MISMATCH"
        expected_parents = [previous_id] if previous_id else []
        if lineage.get("parent_audit_event_ids") != expected_parents:
            return "AUDIT_PARENT_EVENT_MISMATCH"
        payload = integrity.get("event_fingerprint_payload")
        if not isinstance(payload, dict):
            return "AUDIT_PAYLOAD_INVALID"
        if integrity.get("hash_schema") != "audit_event_fingerprint_payload.v0.1":
            return "AUDIT_HASH_SCHEMA_MISMATCH"
        if integrity.get("event_sha256") != sha256_value(payload):
            return "AUDIT_EVENT_HASH_MISMATCH"
        if integrity.get("append_only") is not True or integrity.get("overwrite_allowed") is not False or integrity.get("delete_allowed") is not False:
            return "AUDIT_APPEND_ONLY_BOUNDARY_MISMATCH"
        if event.get("event_type") != "OTHER":
            return "AUDIT_EVENT_TYPE_NOT_COMPATIBLE"
        reason_codes = event.get("event", {}).get("reason_codes", [])
        if len(reason_codes) != 1 or reason_codes[0] not in {
            "RUNTIME_ENTRY_AUTHORIZATION_CHECKED",
            "AUTHORIZATION_CONSUMPTION_COMMITTED",
            "RUNTIME_SESSION_RESERVED",
        }:
            return "AUDIT_EVENT_SUBTYPE_INVALID"
        if payload.get("audit_event_id") != event.get("audit_event_id"):
            return "AUDIT_RECORD_PAYLOAD_ID_MISMATCH"
        if payload.get("previous_event_sha256") != previous_sha or payload.get("sequence_number") != expected_sequence:
            return "AUDIT_RECORD_PAYLOAD_LINEAGE_MISMATCH"
        if payload.get("reason_codes") != reason_codes:
            return "AUDIT_RECORD_PAYLOAD_REASON_MISMATCH"
        previous_id = event.get("audit_event_id")
        previous_sha = integrity.get("event_sha256")
    return None


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ProtectedStateError("timestamp must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception as exc:
        raise ProtectedStateError(f"invalid RFC3339 timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise ProtectedStateError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def validate_recovery_report_semantics(record: dict[str, Any]) -> None:
    if record.get("schema_version") != "runtime_entry_recovery_report.v0.1":
        raise ProtectedStateError("recovery report schema mismatch")
    if record.get("phase") != "I0.5.4" or record.get("owner") != "Thomas":
        raise ProtectedStateError("recovery report phase/owner mismatch")
    if record.get("record_scope") != RECORD_SCOPE or record.get("runtime_source_of_truth") is not False:
        raise ProtectedStateError("recovery report scope/source boundary mismatch")
    status = record.get("status")
    if status not in {
        "CLEAN_NO_PENDING_SESSION",
        "MANUAL_REVIEW_REQUIRED_NO_REUSE",
        "FAIL_CLOSED_INCONSISTENT_STATE",
    }:
        raise ProtectedStateError("recovery report status is invalid")
    store = record.get("store_binding", {})
    if store.get("backend") != "SQLITE" or store.get("recovery_component_id") != RECOVERY_COMPONENT_ID or store.get("recovery_component_version") != RECOVERY_COMPONENT_VERSION:
        raise ProtectedStateError("recovery component binding mismatch")
    decision = record.get("decision", {})
    if decision.get("result") != status:
        raise ProtectedStateError("recovery decision result mismatch")
    for key in ["authorization_reuse_allowed", "automatic_session_resume_allowed", "runtime_entry_allowed"]:
        if decision.get(key) is not False:
            raise ProtectedStateError(f"recovery must keep {key}=false")
    if status == "CLEAN_NO_PENDING_SESSION":
        if decision.get("manual_review_required") is not False or decision.get("blocking_reasons") != []:
            raise ProtectedStateError("clean recovery state must have no blockers or manual review")
    else:
        if decision.get("manual_review_required") is not True:
            raise ProtectedStateError("non-clean recovery state requires manual review")
        if not isinstance(decision.get("blocking_reasons"), list) or not decision["blocking_reasons"]:
            raise ProtectedStateError("non-clean recovery state requires blockers")
    if status == "MANUAL_REVIEW_REQUIRED_NO_REUSE":
        if not record.get("manual_review_sessions") or decision.get("new_thomas_approval_required_for_retry") is not True:
            raise ProtectedStateError("manual-review recovery requires pending Session evidence and new Thomas approval")
    if status == "FAIL_CLOSED_INCONSISTENT_STATE" and decision.get("new_thomas_approval_required_for_retry") is not True:
        raise ProtectedStateError("inconsistent recovery state requires a new Thomas approval before retry")
    effect = record.get("runtime_effect", {})
    if effect.get("mode") != "READ_ONLY_RECOVERY_INSPECTION" or any(value is not False for key, value in effect.items() if key != "mode"):
        raise ProtectedStateError("recovery inspector must remain read-only/no-effect")
    integrity = record.get("integrity", {})
    payload = integrity.get("recovery_fingerprint_payload")
    if integrity.get("hash_schema") != "runtime_entry_recovery_report_fingerprint_payload.v0.1" or integrity.get("recovery_sha256") != sha256_value(payload):
        raise ProtectedStateError("recovery report fingerprint mismatch")
