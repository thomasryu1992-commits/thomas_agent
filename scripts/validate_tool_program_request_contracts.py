
#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from lib.action_fingerprint import compute_action_fingerprint
from lib.resource_request import (
    ResourceRequestError,
    authority_sufficient,
    budget_within,
    compute_request_fingerprint,
    load_yaml,
    parse_role_front_matter,
    registry_index,
    resource_runtime_eligible,
)

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def error(message: str) -> None:
    ERRORS.append(message)


def load_schema(rel: str) -> dict[str, Any]:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def schema_issues(rel: str, schema_rel: str) -> list[str]:
    data = load_yaml(ROOT / rel)
    validator = Draft202012Validator(load_schema(schema_rel), format_checker=FormatChecker())
    return [
        ((".".join(str(p) for p in issue.path) or "<root>") + ": " + issue.message)
        for issue in sorted(validator.iter_errors(data), key=lambda item: list(item.path))
    ]


def validate_runtime_guards(rel: str, record: dict[str, Any]) -> None:
    expected_false = [
        "request_record_can_execute", "executor_handoff_allowed", "tool_execution_allowed",
        "program_execution_allowed", "resource_enablement_allowed", "registry_mutation_allowed",
        "runtime_mutation_allowed", "external_execution_allowed", "financial_execution_allowed",
        "permission_expansion_allowed",
    ]
    effect = record["runtime_effect"]
    if effect.get("mode") != "REVIEW_ONLY":
        error(f"{rel}: mode must remain REVIEW_ONLY")
    for key in expected_false:
        if effect.get(key) is not False:
            error(f"{rel}: {key} must remain false")


def validate_permission_binding(rel: str, record: dict[str, Any]) -> None:
    ref = record["permission"]["permission_decision_ref"]
    path = ROOT / ref
    if not path.exists():
        error(f"{rel}: Permission Decision ref missing: {ref}")
        return
    permission = load_yaml(path)
    fields = ["trace_id", "task_id", "task_revision", "core_context_binding_id"]
    for field in fields:
        if permission.get(field) != record.get(field):
            error(f"{rel}: Permission Decision {field} mismatch")
    if permission.get("permission_decision_id") != record["permission"]["permission_decision_id"]:
        error(f"{rel}: Permission Decision ID mismatch")
    if permission.get("action_fingerprint") != record["permission"]["action_fingerprint"]:
        error(f"{rel}: action_fingerprint mismatch against Permission Decision")
    if permission.get("decision", {}).get("permission_decision") != record["permission"]["permission_decision"]:
        error(f"{rel}: Permission Decision value mismatch")
    try:
        actual = compute_action_fingerprint(permission["fingerprint_payload"])
    except Exception as exc:
        error(f"{rel}: Permission action fingerprint payload invalid: {exc}")
    else:
        if actual != permission.get("action_fingerprint"):
            error(f"{rel}: referenced Permission Decision fingerprint does not recompute")


def validate_role_scope(rel: str, record: dict[str, Any], resource_type: str, resource_id: str) -> None:
    role_path = ROOT / record["role_scope"]["role_definition_ref"]
    assignment_path = ROOT / record["role_scope"]["role_assignment_ref"]
    try:
        role = parse_role_front_matter(role_path)
        assignment = load_yaml(assignment_path)
    except Exception as exc:
        error(f"{rel}: role/assignment evidence failed: {exc}")
        return
    key = "allowed_tool_ids" if resource_type == "TOOL" else "allowed_program_ids"
    role_allowed = resource_id in role.get(key, [])
    assignment_allowed = resource_id in assignment.get(key, [])
    if role_allowed != record["role_scope"]["role_definition_resource_allowlisted"]:
        error(f"{rel}: Role Definition allowlist snapshot mismatch")
    if assignment_allowed != record["role_scope"]["assignment_resource_allowlisted"]:
        error(f"{rel}: Role Assignment allowlist snapshot mismatch")
    budget_key = "max_tool_calls" if resource_type == "TOOL" else "max_program_calls"
    remaining = assignment.get("execution_budget", {}).get("limits", {}).get(budget_key)
    used_key = "tool_calls" if resource_type == "TOOL" else "program_calls"
    used = assignment.get("execution_budget", {}).get("usage", {}).get(used_key)
    if isinstance(remaining, int) and isinstance(used, int):
        actual_remaining = max(remaining - used, 0)
        if actual_remaining != record["budget"]["remaining_call_count"]:
            error(f"{rel}: remaining Resource call budget snapshot mismatch")


def validate_registry(rel: str, record: dict[str, Any]) -> None:
    is_tool = record["schema_version"] == "tool_request.v0.1"
    resource_type = "TOOL" if is_tool else "PROGRAM"
    registry_rel = "05_REGISTRIES/TOOL_REGISTRY.yaml" if is_tool else "05_REGISTRIES/PROGRAM_REGISTRY.yaml"
    collection = "tools" if is_tool else "programs"
    id_key = "tool_id" if is_tool else "program_id"
    version_key = "tool_version" if is_tool else "program_version"
    registry = load_yaml(ROOT / registry_rel)
    entries = registry_index(registry, collection, id_key)
    resource = record["resource"]
    rid = resource[id_key]
    entry = entries.get(rid)
    expected_match = bool(entry and entry.get("version") == resource[version_key])
    if expected_match != record["validation"]["registry_match"]:
        error(f"{rel}: registry_match is inconsistent with Registry")
    if entry:
        mapping = {
            "registry_status": "status",
            "registry_enabled": "enabled",
            "runtime_implementation_available": "runtime_implementation_available",
            "required_permission_level": "required_permission_level",
        }
        for target, source in mapping.items():
            if resource.get(target) != entry.get(source):
                error(f"{rel}: Resource snapshot mismatch for {target}")
        if is_tool:
            if resource.get("tool_class") != entry.get("tool_class"):
                error(f"{rel}: Tool class snapshot mismatch")
            if resource.get("external_action") != entry.get("external_action"):
                error(f"{rel}: Tool external_action snapshot mismatch")
        else:
            if resource.get("deterministic") != entry.get("deterministic"):
                error(f"{rel}: Program deterministic snapshot mismatch")
    eligible = expected_match and resource_runtime_eligible(entry)
    if eligible != record["validation"]["registry_runtime_eligible"]:
        error(f"{rel}: registry_runtime_eligible is inconsistent")
    validate_role_scope(rel, record, resource_type, rid)


def validate_semantics(rel: str, record: dict[str, Any]) -> None:
    try:
        actual_fp = compute_request_fingerprint(record["request_fingerprint_payload"])
    except ResourceRequestError as exc:
        error(f"{rel}: request fingerprint payload invalid: {exc}")
    else:
        if actual_fp != record.get("request_fingerprint"):
            error(f"{rel}: request_fingerprint mismatch")
    if authority_sufficient(record["authority"]) != record["authority"]["authority_sufficient"]:
        error(f"{rel}: authority_sufficient mismatch")
    if budget_within(record["budget"]) != record["budget"]["within_assignment_budget"]:
        error(f"{rel}: within_assignment_budget mismatch")
    if record["validation"]["budget_within_limit"] != record["budget"]["within_assignment_budget"]:
        error(f"{rel}: budget validation snapshot mismatch")
    validate_permission_binding(rel, record)
    validate_registry(rel, record)
    validate_runtime_guards(rel, record)

    is_tool = record["schema_version"] == "tool_request.v0.1"
    op = record["operation"] if is_tool else record["invocation"]
    payload = record["request_fingerprint_payload"]
    resource = record["resource"]
    pairs = {
        "task_id": record["task_id"],
        "task_revision": record["task_revision"],
        "core_context_binding_id": record["core_context_binding_id"],
        "resource_id": resource["tool_id" if is_tool else "program_id"],
        "resource_version": resource["tool_version" if is_tool else "program_version"],
        "operation_type": op["operation_type" if is_tool else "invocation_type"],
        "permission_scope": op["permission_scope"],
        "target_ref": op["target_ref"],
        "data_scope": op["data_scope"],
        "input_refs": op["input_refs"],
        "input_sha256": op["input_sha256"],
        "content_sha256": op["content_sha256"],
        "normalized_parameters": op["normalized_parameters"],
        "assignment_budget_ref": record["budget"]["assignment_budget_ref"],
        "expires_at": record["lifecycle"]["expires_at"],
    }
    for key, expected in pairs.items():
        if payload.get(key) != expected:
            error(f"{rel}: fingerprint payload {key} does not match Request")
    expected_type = "TOOL" if is_tool else "PROGRAM"
    if payload.get("resource_type") != expected_type:
        error(f"{rel}: fingerprint payload resource_type mismatch")

    if not is_tool:
        expected_det = (not op["deterministic_required"]) or resource["deterministic"]
        if expected_det != record["validation"]["determinism_match"]:
            error(f"{rel}: determinism_match mismatch")

    should_block = not all([
        record["validation"]["registry_match"],
        record["validation"]["registry_runtime_eligible"],
        record["validation"]["role_definition_allowlist_match"],
        record["validation"]["assignment_allowlist_match"],
        record["authority"]["authority_sufficient"],
        record["validation"]["policy_scope_match"],
        record["validation"]["permission_binding_match"],
        record["validation"]["budget_within_limit"],
        record["validation"]["lineage_complete"],
        record["validation"].get("determinism_match", True),
    ])
    if should_block:
        if record["validation"]["review_result"] != "BLOCK":
            error(f"{rel}: failed preconditions must result in BLOCK")
        if record["permission"]["permission_decision"] != "BLOCK":
            error(f"{rel}: failed preconditions must bind a BLOCK Permission Decision")
        if record["lifecycle"]["review_status"] != "BLOCKED":
            error(f"{rel}: blocked validation must use BLOCKED lifecycle")


def main() -> int:
    positives = [
        ("examples/tool_requests/tool_request_document_reader_candidate_blocked_v0.1.yaml", "schemas/tool_request.v0.1.schema.json"),
        ("examples/program_requests/program_request_schema_validator_candidate_blocked_v0.1.yaml", "schemas/program_request.v0.1.schema.json"),
    ]
    for rel, schema in positives:
        issues = schema_issues(rel, schema)
        if issues:
            error(f"{rel}: expected valid, got {issues}")
        else:
            validate_semantics(rel, load_yaml(ROOT / rel))

    negative_sets = [
        ("tests/fixtures/tool_requests", "schemas/tool_request.v0.1.schema.json"),
        ("tests/fixtures/program_requests", "schemas/program_request.v0.1.schema.json"),
    ]
    negative_count = 0
    global ERRORS
    for directory, schema in negative_sets:
        for path in sorted((ROOT / directory).glob("*.yaml")):
            negative_count += 1
            rel = path.relative_to(ROOT).as_posix()
            schema_failures = schema_issues(rel, schema)
            saved_errors = ERRORS
            semantic_failures: list[str] = []
            if not schema_failures:
                ERRORS = semantic_failures
                validate_semantics(rel, load_yaml(path))
                ERRORS = saved_errors
            if not schema_failures and not semantic_failures:
                error(f"{rel}: negative fixture unexpectedly passed")

    docs = [
        "docs/runtime-contracts/TOOL_REQUEST_CONTRACT_V0.1.md",
        "docs/runtime-contracts/PROGRAM_REQUEST_CONTRACT_V0.1.md",
        "docs/runtime-contracts/RESOURCE_REQUEST_REVIEW_ONLY_BOUNDARY_V0.1.md",
    ]
    required_tokens = ["REVIEW_ONLY", "cannot expand Authority", "does not execute"]
    for rel in docs:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for token in required_tokens:
            if token not in text:
                error(f"{rel}: missing required safety token: {token}")

    if ERRORS:
        print("FAIL: Tool/Program Request validation found errors")
        for item in ERRORS:
            print(f" - {item}")
        return 1

    print("PASS: Tool Request v0.1 and Program Request v0.1 validation completed")
    print(f"Validated 2 current-Registry blocked examples and {negative_count} fail-closed fixtures")
    print("No Tool or Program execution, enablement, Registry mutation, executor handoff, or permission expansion occurred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
