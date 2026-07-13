#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any
import yaml

from lib.execution_foundation import actor_ref, compute_audit_event_sha256


def load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected YAML mapping")
    return data


def subject_identity(record: dict[str, Any], path: Path) -> tuple[str, str, str | None, dict[str, Any]]:
    schema = record.get("schema_version")
    mapping = {
        "execution_request.v0.1": ("EXECUTION_REQUEST", "execution_request_id", "request_fingerprint", "requested_by"),
        "execution_result.v0.1": ("EXECUTION_RESULT", "execution_result_id", None, None),
        "validation_result.v0.1": ("VALIDATION_RESULT", "validation_result_id", None, "validator"),
        "tool_request.v0.1": ("TOOL_REQUEST", "tool_request_id", "request_fingerprint", "requested_by"),
        "program_request.v0.1": ("PROGRAM_REQUEST", "program_request_id", "request_fingerprint", "requested_by"),
    }
    if schema not in mapping:
        raise ValueError(f"unsupported Audit subject schema: {schema}")
    subject_type, id_key, fp_key, actor_key = mapping[schema]
    fingerprint = record.get(fp_key) if fp_key else None
    if fingerprint is None:
        fingerprint = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    if actor_key == "validator":
        v = record["validator"]
        actor = {
            "actor_type": "role" if v.get("validator_role_id") else "system",
            "actor_id": v["validator_actor_id"],
            "role_id": v.get("validator_role_id"),
            "role_version": v.get("validator_role_version"),
            "assignment_id": None,
        }
    elif actor_key:
        actor = record[actor_key]
    else:
        actor = {"actor_type": "system", "actor_id": "execution.review", "role_id": None, "role_version": None, "assignment_id": None}
    return subject_type, record[id_key], fingerprint, actor


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an append-only Audit Event v0.1 preview")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--audit-event-id", required=True)
    parser.add_argument("--event-type", required=True)
    parser.add_argument("--outcome", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--reason-code", action="append", default=[])
    parser.add_argument("--sequence-number", type=int, required=True)
    parser.add_argument("--previous-event-sha256")
    parser.add_argument("--parent-audit-event-id", action="append", default=[])
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    subject_path = Path(args.subject)
    subject = load(subject_path)
    subject_type, subject_id, subject_fp, actor = subject_identity(subject, subject_path)
    subject_ref = subject_path.as_posix()
    payload = {
        "schema_version": "audit_event_fingerprint_payload.v0.1",
        "audit_event_id": args.audit_event_id,
        "trace_id": subject["trace_id"],
        "task_id": subject["task_id"],
        "task_revision": subject["task_revision"],
        "core_context_binding_id": subject["core_context_binding_id"],
        "event_type": args.event_type,
        "actor_ref": actor_ref(actor),
        "subject_ref": subject_ref,
        "subject_fingerprint": subject_fp,
        "event_summary": args.summary,
        "outcome": args.outcome,
        "reason_codes": args.reason_code,
        "payload_sha256": None,
        "evidence_refs": [subject_ref],
        "related_record_refs": [subject_ref],
        "parent_audit_event_ids": args.parent_audit_event_id,
        "previous_event_sha256": args.previous_event_sha256,
        "sequence_number": args.sequence_number,
        "created_at": args.created_at,
    }
    event_sha = compute_audit_event_sha256(payload)
    record = {
        "schema_version": "audit_event.v0.1",
        "audit_event_id": args.audit_event_id,
        "trace_id": subject["trace_id"],
        "task_id": subject["task_id"],
        "task_revision": subject["task_revision"],
        "core_context_binding_id": subject["core_context_binding_id"],
        "event_type": args.event_type,
        "actor": actor,
        "subject": {
            "subject_type": subject_type,
            "subject_id": subject_id,
            "subject_ref": subject_ref,
            "subject_fingerprint": subject_fp,
        },
        "event": {
            "event_summary": args.summary,
            "outcome": args.outcome,
            "reason_codes": args.reason_code,
            "payload_ref": None,
            "payload_sha256": None,
            "evidence_refs": [subject_ref],
            "related_record_refs": [subject_ref],
        },
        "lineage": {
            "parent_audit_event_ids": args.parent_audit_event_id,
            "previous_event_sha256": args.previous_event_sha256,
            "sequence_number": args.sequence_number,
        },
        "integrity": {
            "hash_schema": "audit_event_fingerprint_payload.v0.1",
            "event_fingerprint_payload": payload,
            "event_sha256": event_sha,
            "append_only": True,
            "overwrite_allowed": False,
            "delete_allowed": False,
        },
        "sensitivity": "INTERNAL",
        "runtime_effect": {
            "mode": "EVIDENCE_ONLY",
            "grants_permission": False,
            "grants_approval": False,
            "grants_authority": False,
            "grants_execution": False,
            "grants_activation": False,
            "mutates_runtime": False,
        },
        "created_at": args.created_at,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(record, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8", newline="\n")
    print(f"WROTE: {output}")
    print(f"EVENT_SHA256: {event_sha}")
    print("EVIDENCE_ONLY: Audit does not grant Permission or execution")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
