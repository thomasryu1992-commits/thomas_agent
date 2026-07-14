#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from lib.execution_foundation import (
    ExecutionFoundationError,
    actor_ref,
    authority_sufficient,
    budget_within,
    compute_audit_event_sha256,
    compute_execution_request_fingerprint,
    requester_ref,
)

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def error(message: str) -> None:
    ERRORS.append(message)


def load_yaml(path_or_rel: Path | str) -> dict[str, Any]:
    path = path_or_rel if isinstance(path_or_rel, Path) else ROOT / path_or_rel
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected YAML mapping")
    return data


def load_json(rel: str) -> dict[str, Any]:
    data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{rel}: expected JSON object")
    return data


def schema_issues(rel: str, schema_rel: str) -> list[str]:
    data = load_yaml(rel)
    schema = load_json(schema_rel)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [
        f"{'.'.join(str(p) for p in issue.path) or '<root>'}: {issue.message}"
        for issue in sorted(validator.iter_errors(data), key=lambda item: list(item.path))
    ]


def file_sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def no_effect_runtime(record: dict[str, Any], rel: str) -> None:
    runtime = record.get("runtime_effect", {})
    for key, value in runtime.items():
        if key == "mode":
            if value not in {"REVIEW_ONLY", "EVIDENCE_ONLY"}:
                error(f"{rel}: invalid runtime_effect.mode")
        elif value is not False:
            error(f"{rel}: runtime_effect.{key} must be false")


def validate_execution_request(rel: str, record: dict[str, Any]) -> None:
    try:
        actual = compute_execution_request_fingerprint(record["request_fingerprint_payload"])
    except ExecutionFoundationError as exc:
        error(f"{rel}: fingerprint payload invalid: {exc}")
    else:
        if actual != record["request_fingerprint"]:
            error(f"{rel}: request_fingerprint mismatch")

    payload = record["request_fingerprint_payload"]
    upstream = record["upstream"]
    expected_pairs = {
        "task_id": record["task_id"],
        "task_revision": record["task_revision"],
        "core_context_binding_id": record["core_context_binding_id"],
        "requester_ref": requester_ref(record["requested_by"]),
        "upstream_request_type": upstream["request_type"],
        "upstream_request_id": upstream["request_id"],
        "upstream_request_ref": upstream["request_ref"],
        "upstream_request_fingerprint": upstream["request_fingerprint"],
        "action_fingerprint": upstream["action_fingerprint"],
        "permission_decision_id": record["permission"]["permission_decision_id"],
        "approval_id": record["approval"]["approval_id"],
        "executor_id": None,
        "target_ref": record["execution_plan"]["target_ref"],
        "data_scope": record["execution_plan"]["data_scope"],
        "normalized_parameters": record["execution_plan"]["normalized_parameters"],
        "idempotency_key": record["idempotency"]["idempotency_key"],
        "assignment_budget_ref": record["budget"]["assignment_budget_ref"],
        "expires_at": record["lifecycle"]["expires_at"],
    }
    for key, expected in expected_pairs.items():
        if payload.get(key) != expected:
            error(f"{rel}: fingerprint payload {key} mismatch")

    actual_authority = authority_sufficient(record["authority"])
    if actual_authority != record["authority"]["authority_sufficient"]:
        error(f"{rel}: authority_sufficient snapshot mismatch")
    if actual_authority != record["validation"]["authority_match"]:
        error(f"{rel}: validation.authority_match mismatch")

    actual_budget = budget_within(record["budget"])
    if actual_budget != record["budget"]["within_assignment_budget"]:
        error(f"{rel}: budget snapshot mismatch")
    if actual_budget != record["validation"]["budget_within_limit"]:
        error(f"{rel}: validation budget snapshot mismatch")

    upstream_path = ROOT / upstream["request_ref"]
    if not upstream_path.exists():
        error(f"{rel}: upstream request ref missing")
    else:
        upstream_record = load_yaml(upstream_path)
        schema = upstream_record.get("schema_version")
        if upstream["request_type"] == "TOOL_REQUEST":
            expected_id = upstream_record.get("tool_request_id")
            expected_fp = upstream_record.get("request_fingerprint")
            expected_action_fp = upstream_record.get("permission", {}).get("action_fingerprint")
            expected_operation = upstream_record.get("operation", {})
        elif upstream["request_type"] == "PROGRAM_REQUEST":
            expected_id = upstream_record.get("program_request_id")
            expected_fp = upstream_record.get("request_fingerprint")
            expected_action_fp = upstream_record.get("permission", {}).get("action_fingerprint")
            expected_operation = upstream_record.get("invocation", {})
        else:
            expected_id = upstream_record.get("permission_decision_id")
            expected_fp = upstream_record.get("action_fingerprint")
            expected_action_fp = upstream_record.get("action_fingerprint")
            expected_operation = upstream_record.get("fingerprint_payload", {})
        match = all([
            expected_id == upstream["request_id"],
            expected_fp == upstream["request_fingerprint"],
            expected_action_fp == upstream["action_fingerprint"],
            upstream_record.get("task_id") == record["task_id"],
            upstream_record.get("task_revision") == record["task_revision"],
            upstream_record.get("core_context_binding_id") == record["core_context_binding_id"],
        ])
        if not match:
            error(f"{rel}: upstream binding mismatch")
        if match != record["validation"]["upstream_binding_match"]:
            error(f"{rel}: validation.upstream_binding_match mismatch")
        if upstream["request_type"] == "ACTION_PERMISSION":
            expected_target = expected_operation.get("target_ref")
            expected_scope = expected_operation.get("permission_scope")
        else:
            expected_target = expected_operation.get("target_ref")
            expected_scope = expected_operation.get("permission_scope")
        if expected_target != upstream["target_ref"] or expected_scope != upstream["permission_scope"]:
            error(f"{rel}: upstream operation snapshot mismatch")

    perm_path = ROOT / record["permission"]["permission_decision_ref"]
    if not perm_path.exists():
        error(f"{rel}: Permission Decision ref missing")
    else:
        perm = load_yaml(perm_path)
        p_match = all([
            perm.get("permission_decision_id") == record["permission"]["permission_decision_id"],
            perm.get("action_fingerprint") == record["permission"]["action_fingerprint"],
            perm.get("action_fingerprint") == upstream["action_fingerprint"],
            perm.get("decision", {}).get("permission_decision") == record["permission"]["permission_decision"],
            perm.get("task_id") == record["task_id"],
            perm.get("task_revision") == record["task_revision"],
            perm.get("core_context_binding_id") == record["core_context_binding_id"],
        ])
        if p_match != record["permission"]["binding_verified"]:
            error(f"{rel}: Permission binding_verified mismatch")
        if p_match != record["validation"]["permission_binding_match"]:
            error(f"{rel}: validation.permission_binding_match mismatch")

    approval = record["approval"]
    a_match = not approval["approval_required"]
    if approval["approval_ref"]:
        approval_path = ROOT / approval["approval_ref"]
        if not approval_path.exists():
            error(f"{rel}: Approval ref missing")
        else:
            ap = load_yaml(approval_path)
            a_match = all([
                ap.get("approval_id") == approval["approval_id"],
                ap.get("permission_decision_id") == record["permission"]["permission_decision_id"],
                ap.get("action_fingerprint") == record["permission"]["action_fingerprint"],
                ap.get("task_id") == record["task_id"],
                ap.get("task_revision") == record["task_revision"],
                ap.get("core_context_binding_id") == record["core_context_binding_id"],
                ap.get("status") == approval["approval_status"],
            ])
    if a_match != approval["binding_verified"]:
        error(f"{rel}: Approval binding_verified mismatch")
    if a_match != record["validation"]["approval_binding_match"]:
        error(f"{rel}: validation.approval_binding_match mismatch")

    if record["permission"]["permission_decision"] == "APPROVAL_REQUIRED":
        if not approval["approval_required"] or not approval["approval_id"]:
            error(f"{rel}: APPROVAL_REQUIRED lacks Approval binding")

    plan = record["execution_plan"]
    if any([
        plan["executor_registered"], plan["executor_enabled"],
        plan["executor_implementation_available"], plan["executor_handoff_allowed"],
        record["validation"]["executor_ready"],
    ]):
        error(f"{rel}: Executor readiness must remain false in I0.4.4")
    if plan["executor_id"] is not None or plan["executor_registry_ref"] is not None:
        error(f"{rel}: Executor identity/Registry must remain null in I0.4.4")

    if record["validation"]["request_review_result"] != "BLOCK":
        error(f"{rel}: current no-Executor foundation must remain BLOCK")
    if record["lifecycle"]["review_status"] != "BLOCKED":
        error(f"{rel}: current no-Executor foundation must use BLOCKED lifecycle")
    if not record["validation"]["block_reasons"]:
        error(f"{rel}: blocked Execution Request requires reasons")
    no_effect_runtime(record, rel)


def validate_execution_result(rel: str, record: dict[str, Any]) -> None:
    request_path = ROOT / record["execution_request_ref"]
    if not request_path.exists():
        error(f"{rel}: Execution Request ref missing")
    else:
        request = load_yaml(request_path)
        expected = [
            request.get("execution_request_id") == record["execution_request_id"],
            request.get("request_fingerprint") == record["execution_request_fingerprint"],
            request.get("trace_id") == record["trace_id"],
            request.get("task_id") == record["task_id"],
            request.get("task_revision") == record["task_revision"],
            request.get("core_context_binding_id") == record["core_context_binding_id"],
        ]
        if not all(expected):
            error(f"{rel}: Execution Result lineage mismatch")
    evidence = record["execution_evidence"]
    for key, value in evidence.items():
        if key == "side_effect_summary":
            if value:
                error(f"{rel}: side_effect_summary must be empty")
        elif key in {"execution_attempt_id", "started_at", "finished_at"}:
            if value is not None:
                error(f"{rel}: {key} must remain null")
        elif value is not False:
            error(f"{rel}: {key} must remain false")
    metrics = record["metrics"]
    if any(metrics[key] != 0 for key in ["runtime_seconds", "tool_calls", "program_calls", "external_calls"]):
        error(f"{rel}: execution metrics must remain zero")
    if metrics["cost_decimal"] != "0":
        error(f"{rel}: execution cost must remain zero")
    if record["result_status"] == "BLOCKED" and not record["output"]["block_reasons"]:
        error(f"{rel}: BLOCKED result requires block reasons")
    if record["result_status"] == "PREVIEWED" and not record["output"]["preview_summary"]:
        error(f"{rel}: PREVIEWED result requires preview summary")
    if len(record["output"]["output_refs"]) != len(record["output"]["output_sha256"]):
        error(f"{rel}: output refs and hashes must have equal length")
    no_effect_runtime(record, rel)


def subject_expected_fingerprint(subject: dict[str, Any]) -> str | None:
    path = ROOT / subject["subject_ref"]
    if not path.exists():
        return None
    record = load_yaml(path)
    schema = record.get("schema_version")
    if schema == "execution_request.v0.1":
        return record.get("request_fingerprint")
    if schema in {"tool_request.v0.1", "program_request.v0.1"}:
        return record.get("request_fingerprint")
    if schema in {"permission_decision.v0.3", "approval.v0.1"}:
        return record.get("action_fingerprint")
    return file_sha(path)


def validate_validation_result(rel: str, record: dict[str, Any]) -> None:
    subject = record["subject"]
    subject_path = ROOT / subject["subject_ref"]
    if not subject_path.exists():
        error(f"{rel}: Validation subject ref missing")
    else:
        expected = subject_expected_fingerprint(subject)
        if expected != subject["subject_fingerprint"]:
            error(f"{rel}: Validation subject fingerprint mismatch")
    validator = record["validator"]
    actual_independent = (
        not validator["independent_required"]
        or (
            validator["validator_actor_id"] != subject["subject_created_by_actor_id"]
            and bool(validator["validator_execution_context_id"])
        )
    )
    if actual_independent != validator["independence_verified"]:
        error(f"{rel}: independence_verified mismatch")
    if validator["independent_required"] and validator["validator_actor_id"] == subject["subject_created_by_actor_id"]:
        error(f"{rel}: self-review cannot be independent")

    result = record["validation"]["result"]
    check_results = [item["result"] for item in record["validation"]["checks"]]
    if result == "PASS" and any(item != "PASS" for item in check_results):
        error(f"{rel}: PASS result contains non-PASS check")
    if result == "BLOCK" and "BLOCK" not in check_results:
        error(f"{rel}: BLOCK result requires a BLOCK check")
    if result == "REVISE" and not any(item in {"REVISE", "BLOCK"} for item in check_results):
        error(f"{rel}: REVISE result requires a non-PASS check")
    if not record["evidence_refs"]:
        error(f"{rel}: Validation requires evidence")
    for key, value in record["permission_boundary"].items():
        if value is not False:
            error(f"{rel}: permission_boundary.{key} must be false")
    no_effect_runtime(record, rel)


def validate_audit_event(rel: str, record: dict[str, Any]) -> None:
    integrity = record["integrity"]
    payload = integrity["event_fingerprint_payload"]
    try:
        actual_hash = compute_audit_event_sha256(payload)
    except ExecutionFoundationError as exc:
        error(f"{rel}: Audit payload invalid: {exc}")
    else:
        if actual_hash != integrity["event_sha256"]:
            error(f"{rel}: event_sha256 mismatch")

    expected_pairs = {
        "audit_event_id": record["audit_event_id"],
        "trace_id": record["trace_id"],
        "task_id": record["task_id"],
        "task_revision": record["task_revision"],
        "core_context_binding_id": record["core_context_binding_id"],
        "event_type": record["event_type"],
        "actor_ref": actor_ref(record["actor"]),
        "subject_ref": record["subject"]["subject_ref"],
        "subject_fingerprint": record["subject"]["subject_fingerprint"],
        "event_summary": record["event"]["event_summary"],
        "outcome": record["event"]["outcome"],
        "reason_codes": record["event"]["reason_codes"],
        "payload_sha256": record["event"]["payload_sha256"],
        "evidence_refs": record["event"]["evidence_refs"],
        "related_record_refs": record["event"]["related_record_refs"],
        "parent_audit_event_ids": record["lineage"]["parent_audit_event_ids"],
        "previous_event_sha256": record["lineage"]["previous_event_sha256"],
        "sequence_number": record["lineage"]["sequence_number"],
        "created_at": record["created_at"],
    }
    for key, expected in expected_pairs.items():
        if payload.get(key) != expected:
            error(f"{rel}: Audit payload {key} mismatch")

    subject_path = ROOT / record["subject"]["subject_ref"]
    if not subject_path.exists():
        error(f"{rel}: Audit subject ref missing")
    else:
        expected_fp = subject_expected_fingerprint({
            "subject_ref": record["subject"]["subject_ref"],
            "subject_fingerprint": record["subject"]["subject_fingerprint"],
        })
        if expected_fp != record["subject"]["subject_fingerprint"]:
            error(f"{rel}: Audit subject fingerprint mismatch")

    seq = record["lineage"]["sequence_number"]
    prev = record["lineage"]["previous_event_sha256"]
    if seq == 1 and prev is not None:
        error(f"{rel}: sequence 1 must not have previous_event_sha256")
    if seq > 1 and prev is None:
        error(f"{rel}: sequence >1 requires previous_event_sha256")
    if seq > 1 and not record["lineage"]["parent_audit_event_ids"]:
        error(f"{rel}: sequence >1 requires parent Audit Event ID")
    if (record["event"]["payload_ref"] is None) != (record["event"]["payload_sha256"] is None):
        error(f"{rel}: payload_ref and payload_sha256 must appear together")
    if integrity["append_only"] is not True or integrity["overwrite_allowed"] is not False or integrity["delete_allowed"] is not False:
        error(f"{rel}: Audit immutability guard mismatch")
    no_effect_runtime(record, rel)


def validate_semantics(rel: str, record: dict[str, Any]) -> None:
    schema = record.get("schema_version")
    if schema == "execution_request.v0.1":
        validate_execution_request(rel, record)
    elif schema == "execution_result.v0.1":
        validate_execution_result(rel, record)
    elif schema == "validation_result.v0.1":
        validate_validation_result(rel, record)
    elif schema == "audit_event.v0.1":
        validate_audit_event(rel, record)
    else:
        error(f"{rel}: unsupported schema {schema}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Active Validation/Audit or Deferred Execution preview records.")
    parser.add_argument("--scope", choices=("active", "deferred", "all"), default="all")
    args = parser.parse_args()

    execution_positives = [
        ("examples/execution_requests/execution_request_tool_document_reader_candidate_blocked_v0.1.yaml", "schemas/execution_request.v0.1.schema.json"),
        ("examples/execution_requests/execution_request_program_schema_validator_candidate_blocked_v0.1.yaml", "schemas/execution_request.v0.1.schema.json"),
        ("examples/execution_requests/execution_request_memory_promotion_approved_but_no_executor_v0.1.yaml", "schemas/execution_request.v0.1.schema.json"),
        ("examples/execution_results/execution_result_tool_document_reader_blocked_v0.1.yaml", "schemas/execution_result.v0.1.schema.json"),
        ("examples/execution_results/execution_result_memory_promotion_previewed_no_execution_v0.1.yaml", "schemas/execution_result.v0.1.schema.json"),
    ]
    active_positives = [
        ("examples/validation_results/validation_result_execution_request_tool_block_v0.1.yaml", "schemas/validation_result.v0.1.schema.json"),
        ("examples/validation_results/validation_result_execution_result_no_effect_pass_v0.1.yaml", "schemas/validation_result.v0.1.schema.json"),
        ("examples/audit/audit_event_execution_request_tool_created_v0.1.yaml", "schemas/audit_event.v0.1.schema.json"),
        ("examples/audit/audit_event_validation_execution_request_tool_v0.1.yaml", "schemas/audit_event.v0.1.schema.json"),
    ]
    execution_negative_sets = [
        ("tests/fixtures/execution_requests", "schemas/execution_request.v0.1.schema.json"),
        ("tests/fixtures/execution_results", "schemas/execution_result.v0.1.schema.json"),
    ]
    active_negative_sets = [
        ("tests/fixtures/validation_results", "schemas/validation_result.v0.1.schema.json"),
        ("tests/fixtures/audit", "schemas/audit_event.v0.1.schema.json"),
    ]
    execution_docs = {
        "docs/runtime-contracts/EXECUTION_REQUEST_CONTRACT_V0.1.md": ["PREVIEW_ONLY", "does not execute", "Approval cannot repair insufficient Authority"],
        "docs/runtime-contracts/EXECUTION_RESULT_CONTRACT_V0.1.md": ["execution_performed: false", "must never fabricate"],
        "docs/runtime-contracts/EXECUTION_VALIDATION_AUDIT_REVIEW_ONLY_BOUNDARY_V0.1.md": ["Deferred Executor", "Approval consumption", "fabricated `SUCCEEDED`"],
    }
    active_docs = {
        "docs/runtime-contracts/VALIDATION_RESULT_CONTRACT_V0.1.md": ["Validation does not create", "mutates_subject: false"],
        "docs/runtime-contracts/AUDIT_EVENT_CONTRACT_V0.1.md": ["append-only", "Audit never grants"],
        "docs/runtime-contracts/EXECUTION_VALIDATION_AUDIT_REVIEW_ONLY_BOUNDARY_V0.1.md": ["Validation Result", "Audit Event", "Active invariants"],
    }

    positives = []
    negative_sets = []
    doc_tokens = {}
    if args.scope in {"active", "all"}:
        positives.extend(active_positives)
        negative_sets.extend(active_negative_sets)
        doc_tokens.update(active_docs)
    if args.scope in {"deferred", "all"}:
        positives.extend(execution_positives)
        negative_sets.extend(execution_negative_sets)
        doc_tokens.update(execution_docs)

    for rel, schema_rel in positives:
        issues = schema_issues(rel, schema_rel)
        if issues:
            error(f"{rel}: expected valid, got {issues}")
        else:
            validate_semantics(rel, load_yaml(rel))

    negative_count = 0
    global ERRORS
    for directory, schema_rel in negative_sets:
        for fixture in sorted((ROOT / directory).glob("*.yaml")):
            negative_count += 1
            rel = fixture.relative_to(ROOT).as_posix()
            schema_failures = schema_issues(rel, schema_rel)
            saved = ERRORS
            semantic_failures: list[str] = []
            if not schema_failures:
                ERRORS = semantic_failures
                validate_semantics(rel, load_yaml(fixture))
                ERRORS = saved
            if not schema_failures and not semantic_failures:
                error(f"{rel}: negative fixture unexpectedly passed")

    for rel, tokens in doc_tokens.items():
        source = (ROOT / rel).read_text(encoding="utf-8")
        for token in tokens:
            if token not in source:
                error(f"{rel}: missing safety token: {token}")

    if ERRORS:
        print(f"FAIL: {args.scope} Execution/Validation/Audit validation found errors")
        for item in ERRORS:
            print(f" - {item}")
        return 1

    print(f"PASS: {args.scope} Execution/Validation/Audit validation completed")
    print(f"Validated {len(positives)} positive examples and {negative_count} fail-closed fixtures")
    print("No Executor, Tool, Program, external endpoint, financial path, Runtime mutation, Approval consumption, or Permission expansion was enabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
