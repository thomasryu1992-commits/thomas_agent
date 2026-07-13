#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.read_only_entry.integration_candidate import (
    build_disabled_single_entry_integration_candidate,
    integration_fingerprint_payload,
    validate_disabled_single_entry_integration_candidate_semantics,
)
from runtime.read_only_kernel.integrity import sha256_value

REAL_AUTH = (
    "examples/runtime_entry_authorization/"
    "runtime_entry_authorization_ready_for_thomas_action_approval_review_v0.1.yaml"
)
SYNTH_AUTH = (
    "examples/runtime_entry_authorization/"
    "SYNTHETIC_ONLY_runtime_entry_authorization_approved_not_consumed_v0.1.yaml"
)
SYNTH_TRANSITION = (
    "examples/protected_governance_state/"
    "SYNTHETIC_ONLY_runtime_entry_durable_transition_committed_v0.1.yaml"
)
REAL_EXAMPLE = (
    "examples/single_read_only_entry_integration/"
    "disabled_single_read_only_entry_integration_candidate_real_review_v0.1.yaml"
)
SYNTH_EXAMPLE = (
    "examples/single_read_only_entry_integration/"
    "SYNTHETIC_ONLY_disabled_single_read_only_entry_integration_candidate_"
    "after_durable_commit_v0.1.yaml"
)
REGISTRY = (
    "05_REGISTRIES/"
    "I0_5_5_SINGLE_READ_ONLY_ENTRY_INTEGRATION_COMPONENTS_REVIEW_ONLY.yaml"
)
FIXTURES = "tests/fixtures/single_read_only_entry_integration/mutation_cases.yaml"
SCHEMA = "schemas/disabled_single_read_only_entry_integration_candidate.v0.1.schema.json"
CREATED_AT = "2026-07-13T11:00:00Z"

FORBIDDEN_IMPORT_ROOTS = {
    "aiohttp",
    "anthropic",
    "boto3",
    "ftplib",
    "httpx",
    "openai",
    "paramiko",
    "playwright",
    "requests",
    "selenium",
    "smtplib",
    "socket",
    "sqlite3",
    "subprocess",
    "telnetlib",
    "urllib",
    "webbrowser",
}
FORBIDDEN_CALL_TOKENS = {
    "attempt_atomic_transition",
    "execute_contract_inspection_worker",
    "run_bundle",
    "start_runtime_session",
    "sqlite3.connect",
    "place_order",
    "send_email",
}


def load_yaml(rel: str) -> dict[str, Any]:
    value = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{rel}: expected YAML object")
    return value


def validate_schema(record: dict[str, Any]) -> None:
    schema = json.loads((ROOT / SCHEMA).read_text(encoding="utf-8"))
    issues = sorted(
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(record),
        key=lambda item: list(item.path),
    )
    if issues:
        raise AssertionError(
            "schema errors: "
            + "; ".join(
                f"{list(item.path)}: {item.message}" for item in issues[:10]
            )
        )


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


def recompute_hash(record: dict[str, Any]) -> None:
    payload = integration_fingerprint_payload(record)
    record["integrity"]["candidate_fingerprint_payload"] = payload
    record["integrity"]["candidate_sha256"] = sha256_value(payload)


def static_review() -> None:
    path = ROOT / "runtime/read_only_entry/integration_candidate.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=path.as_posix())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_IMPORT_ROOTS:
                    raise AssertionError(f"forbidden import: {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in FORBIDDEN_IMPORT_ROOTS:
                raise AssertionError(f"forbidden import: {node.module}")
    for token in FORBIDDEN_CALL_TOKENS:
        if token in source:
            raise AssertionError(
                f"forbidden state/Runtime/external call token: {token}"
            )


def validate_registry(record: dict[str, Any]) -> None:
    if (
        record.get("schema_version")
        != "i0_5_5_single_read_only_entry_integration_components.v0.1"
    ):
        raise AssertionError("I0.5.5 registry schema mismatch")
    if (
        record.get("owner") != "Thomas"
        or record.get("runtime_source_of_truth") is not False
        or record.get("runtime_authoritative_mode_enabled") is not False
    ):
        raise AssertionError("I0.5.5 registry ownership/source boundary mismatch")
    components = record.get("components")
    if not isinstance(components, list) or len(components) != 1:
        raise AssertionError(
            "I0.5.5 registry must contain exactly one integration component"
        )
    item = components[0]
    if (
        item.get("component_id")
        != "thomas.runtime_entry.single_read_only_integration_candidate"
        or item.get("version") != "0.1.0"
    ):
        raise AssertionError("I0.5.5 component identity/version mismatch")
    required_true = [
        "implementation_available",
        "uses_existing_entry_plan",
        "uses_existing_action_approval_contract",
        "uses_existing_exact_entry_authorization",
        "uses_existing_protected_state_candidate",
        "uses_existing_disabled_entry_adapter",
        "uses_existing_audit_contract",
    ]
    required_false = [
        "enabled",
        "runtime_authoritative",
        "creates_new_permission_model",
        "creates_new_approval_model",
        "creates_new_audit_model",
        "real_approval_consumption_allowed",
        "runtime_governance_state_write_allowed",
        "runtime_session_start_allowed",
        "runtime_handoff_allowed",
        "kernel_call_allowed",
        "model_invocation_allowed",
        "tool_execution_allowed",
        "program_execution_allowed",
        "network_allowed",
        "domain_write_allowed",
        "workspace_write_allowed",
        "core_write_allowed",
        "external_action_allowed",
        "financial_action_allowed",
    ]
    if any(item.get(key) is not True for key in required_true):
        raise AssertionError("I0.5.5 existing-contract reuse evidence mismatch")
    if any(item.get(key) is not False for key in required_false):
        raise AssertionError(
            "I0.5.5 component boundary must remain disabled/no-effect"
        )
    if any(
        value is not False
        for value in record.get("review_only_effects", {}).values()
    ):
        raise AssertionError("I0.5.5 registry effects must remain false")


def positive_records() -> tuple[dict[str, Any], dict[str, Any]]:
    real = build_disabled_single_entry_integration_candidate(
        load_yaml(REAL_AUTH),
        authorization_ref=REAL_AUTH,
        created_at=CREATED_AT,
    )
    synthetic = build_disabled_single_entry_integration_candidate(
        load_yaml(SYNTH_AUTH),
        authorization_ref=SYNTH_AUTH,
        durable_transition=load_yaml(SYNTH_TRANSITION),
        durable_transition_ref=SYNTH_TRANSITION,
        created_at=CREATED_AT,
    )
    for record in [real, synthetic]:
        validate_schema(record)
        validate_disabled_single_entry_integration_candidate_semantics(record)
        assert (
            record["decision"]["result"]
            == "BLOCKED_DISABLED_INTEGRATION_CANDIDATE"
        )
        assert record["decision"]["ready_for_runtime_entry"] is False
        assert record["kernel_invocation_candidate"]["kernel_called"] is False
        assert all(
            value is False
            for key, value in record["runtime_effect"].items()
            if key != "mode"
        )
    assert real["decision"]["blocking_reasons"] == [
        "ACTION_APPROVAL_NOT_VERIFIED",
        "REAL_APPROVAL_CONSUMPTION_UNAVAILABLE",
        "DURABLE_TRANSITION_NOT_PRESENT",
        "RUNTIME_ENTRY_ADAPTER_DISABLED",
        "KERNEL_CALL_NOT_ALLOWED",
    ]
    assert synthetic["decision"]["blocking_reasons"] == [
        "SYNTHETIC_SCOPE_NOT_RUNTIME_ELIGIBLE",
        "RUNTIME_ENTRY_ADAPTER_DISABLED",
        "KERNEL_CALL_NOT_ALLOWED",
    ]
    assert (
        synthetic["protected_state_boundary"][
            "synthetic_transition_commit_observed"
        ]
        is True
    )
    assert (
        synthetic["protected_state_boundary"][
            "recovery_inspection_required_before_any_future_action"
        ]
        is True
    )
    for rel, expected in [
        (REAL_EXAMPLE, real),
        (SYNTH_EXAMPLE, synthetic),
    ]:
        saved = load_yaml(rel)
        if saved != expected:
            raise AssertionError(f"{rel}: example is not reproducible")
    return real, synthetic


def mutation_tests(
    real: dict[str, Any],
    synthetic: dict[str, Any],
) -> tuple[int, int]:
    fixture = load_yaml(FIXTURES)
    candidate_count = 0
    for case in fixture["candidate_cases"]:
        base = synthetic if case.get("base", "synthetic") == "synthetic" else real
        record = deepcopy(base)
        set_path(record, case["path"], deepcopy(case.get("value")))
        if case.get("recompute_hash", True):
            recompute_hash(record)
        try:
            validate_disabled_single_entry_integration_candidate_semantics(record)
        except Exception:
            candidate_count += 1
        else:
            raise AssertionError(
                f"{case['case_id']}: mutated integration candidate unexpectedly passed"
            )
    registry = load_yaml(REGISTRY)
    validate_registry(registry)
    registry_count = 0
    for case in fixture["registry_cases"]:
        record = deepcopy(registry)
        set_path(record, case["path"], deepcopy(case.get("value")))
        try:
            validate_registry(record)
        except Exception:
            registry_count += 1
        else:
            raise AssertionError(
                f"{case['case_id']}: mutated registry unexpectedly passed"
            )
    return candidate_count, registry_count


def main() -> int:
    static_review()
    real, synthetic = positive_records()
    candidate_count, registry_count = mutation_tests(real, synthetic)
    print(
        "PASS: I0.5.5 disabled single read-only Entry integration "
        "candidate validation completed"
    )
    print(
        "Real review chain: blocked before Approval consumption/CAS/Session/Kernel PASS"
    )
    print(
        "Synthetic exact Authorization + durable transition linkage: structurally "
        "consistent but final Adapter/Kernel block PASS"
    )
    print(f"Integration candidate fail-closed mutations: {candidate_count} PASS")
    print(f"Registry fail-closed mutations: {registry_count} PASS")
    print(
        "No real Approval verification/consumption, production state write, Runtime "
        "Session start, Runtime handoff, Kernel call, model, Tool, Program, Executor, "
        "network, Data Plane write, external action, or financial action occurred."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
