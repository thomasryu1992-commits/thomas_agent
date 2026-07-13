#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any
import yaml


def load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected YAML mapping")
    return data


def record_identity(record: dict[str, Any]) -> tuple[str, str, str | None, str, str, int, str]:
    schema = record.get("schema_version")
    mapping = {
        "execution_request.v0.1": ("EXECUTION_REQUEST", "execution_request_id", "request_fingerprint"),
        "execution_result.v0.1": ("EXECUTION_RESULT", "execution_result_id", None),
        "tool_request.v0.1": ("TOOL_REQUEST", "tool_request_id", "request_fingerprint"),
        "program_request.v0.1": ("PROGRAM_REQUEST", "program_request_id", "request_fingerprint"),
        "permission_decision.v0.3": ("PERMISSION_DECISION", "permission_decision_id", "action_fingerprint"),
        "approval.v0.1": ("APPROVAL", "approval_id", "action_fingerprint"),
    }
    if schema not in mapping:
        raise ValueError(f"unsupported subject schema: {schema}")
    subject_type, id_key, fingerprint_key = mapping[schema]
    fingerprint = record.get(fingerprint_key) if fingerprint_key else None
    return (
        subject_type,
        record[id_key],
        fingerprint,
        record["trace_id"],
        record["task_id"],
        record["task_revision"],
        record["core_context_binding_id"],
    )


def file_sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Validation Result v0.1 preview")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--validation-result-id", required=True)
    parser.add_argument("--subject-created-by", required=True)
    parser.add_argument("--validator-actor-id", required=True)
    parser.add_argument("--validator-role-id")
    parser.add_argument("--validator-role-version")
    parser.add_argument("--validator-context-id", required=True)
    parser.add_argument("--mode", choices=["AUTOMATIC", "INDEPENDENT", "RISK_REVIEW", "CONTRACT", "SECURITY"], required=True)
    parser.add_argument("--result", choices=["PASS", "REVISE", "BLOCK"], required=True)
    parser.add_argument("--reason", action="append", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    subject_path = Path(args.subject)
    subject = load(subject_path)
    subject_type, subject_id, subject_fp, trace_id, task_id, task_revision, binding_id = record_identity(subject)
    if subject_fp is None:
        subject_fp = file_sha(subject_path)
    independent_required = args.mode == "INDEPENDENT"
    independence_verified = (not independent_required) or (args.validator_actor_id != args.subject_created_by)
    check_result = args.result

    record = {
        "schema_version": "validation_result.v0.1",
        "validation_result_id": args.validation_result_id,
        "trace_id": trace_id,
        "task_id": task_id,
        "task_revision": task_revision,
        "core_context_binding_id": binding_id,
        "subject": {
            "subject_type": subject_type,
            "subject_id": subject_id,
            "subject_ref": subject_path.as_posix(),
            "subject_fingerprint": subject_fp,
            "subject_created_by_actor_id": args.subject_created_by,
        },
        "validator": {
            "validator_type": "ROLE" if args.validator_role_id else "SYSTEM",
            "validator_actor_id": args.validator_actor_id,
            "validator_role_id": args.validator_role_id,
            "validator_role_version": args.validator_role_version,
            "validator_execution_context_id": args.validator_context_id,
            "independent_required": independent_required,
            "independence_verified": independence_verified,
        },
        "validation": {
            "validation_mode": args.mode,
            "result": args.result,
            "acceptance_criteria": ["subject_contract_valid", "lineage_consistent", "permission_boundary_preserved"],
            "rejection_criteria": ["subject_contract_invalid", "lineage_mismatch", "hidden_execution_or_permission_effect"],
            "checks": [
                {
                    "check_id": "review_summary",
                    "result": check_result,
                    "evidence_refs": [subject_path.as_posix()],
                    "notes": "; ".join(args.reason),
                }
            ],
            "result_reasons": args.reason,
            "recommended_next_state": "BLOCKED" if args.result == "BLOCK" else None,
        },
        "findings": {
            "facts": ["Validation does not grant Permission or execution authority."],
            "risks": args.reason if args.result != "PASS" else [],
            "omissions": [],
            "assumptions": [],
            "limitations": ["Review-only foundation; no Runtime execution was observed."],
        },
        "evidence_refs": [subject_path.as_posix()],
        "permission_boundary": {
            "grants_permission": False,
            "grants_approval": False,
            "grants_authority": False,
            "grants_execution": False,
            "grants_activation": False,
            "mutates_subject": False,
        },
        "runtime_effect": {
            "mode": "REVIEW_ONLY",
            "grants_permission": False,
            "grants_approval": False,
            "grants_authority": False,
            "grants_execution": False,
            "grants_activation": False,
            "executor_handoff_allowed": False,
            "side_effects_allowed": False,
            "runtime_mutation_allowed": False,
        },
        "lifecycle": {"created_at": args.created_at, "supersedes": []},
        "audit_refs": [f"audit:validation:{args.validation_result_id}"],
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(record, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8", newline="\n")
    print(f"WROTE: {output}")
    print("VALIDATION_ONLY: no Permission, Approval, Authority, execution, or Activation was granted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
