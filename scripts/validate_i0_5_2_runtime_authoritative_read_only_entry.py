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

from runtime.read_only_kernel.integrity import sha256_value
from runtime.read_only_entry.planner import build_entry_plan, validate_entry_plan_semantics, EntryPlanError
from runtime.read_only_entry.disabled_adapter import build_disabled_entry_evidence, validate_disabled_entry_evidence_semantics, DisabledEntryAdapterError

FIXED_NOW = "2026-07-13T08:30:00Z"
FORBIDDEN_IMPORT_ROOTS = {"aiohttp","boto3","ftplib","httpx","paramiko","playwright","requests","selenium","smtplib","socket","subprocess","telnetlib","urllib","webbrowser","openai","anthropic","google"}
FORBIDDEN_CALL_NAMES = {"eval","exec","compile","__import__","open"}
FORBIDDEN_ATTR_CALLS = {"write_text","write_bytes","unlink","rename","mkdir","rmdir","touch","chmod","symlink_to","hardlink_to"}


def validate_schema(value: dict[str, Any], rel: str, label: str) -> None:
    schema = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value), key=lambda item: list(item.absolute_path))
    if errors:
        raise AssertionError(label + ": " + "; ".join(item.message for item in errors[:5]))


def static_runtime_review() -> None:
    for path in sorted((ROOT / "runtime/read_only_entry").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in FORBIDDEN_IMPORT_ROOTS:
                        raise AssertionError(f"{path}: forbidden import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in FORBIDDEN_IMPORT_ROOTS:
                    raise AssertionError(f"{path}: forbidden import {node.module}")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALL_NAMES:
                    raise AssertionError(f"{path}: forbidden call {node.func.id}")
                if isinstance(node.func, ast.Attribute) and node.func.attr in FORBIDDEN_ATTR_CALLS:
                    raise AssertionError(f"{path}: forbidden mutating call {node.func.attr}")


def readiness(*, design_ready: bool, activation_ready: bool) -> dict[str, Any]:
    if activation_ready and not design_ready:
        raise ValueError("activation ready requires design ready")
    requirements = {"synthetic_test_only": True}
    design_result = "READY_FOR_THOMAS_DESIGN_DECISION" if design_ready else "BLOCKED_NOT_READY"
    activation_result = "READY_FOR_RUNTIME_ACTIVATION_REVIEW" if activation_ready else "BLOCKED_NOT_READY"
    payload = {"schema_version":"runtime_promotion_readiness_fingerprint_payload.v0.1","readiness_id":"rpr_i052_synthetic","requirements":requirements,"design_blocking_reasons":[] if design_ready else ["SYNTHETIC_DESIGN_BLOCK"],"activation_blocking_reasons":[] if activation_ready else ["SYNTHETIC_ACTIVATION_BLOCK"],"created_at":FIXED_NOW}
    return {
        "schema_version":"runtime_promotion_readiness.v0.1","readiness_id":"rpr_i052_synthetic","phase":"I0.5.1_REV3","status":"REVIEW_ONLY_NOT_RUNTIME_ACTIVE",
        "requirements":requirements,"checks":[],
        "summary":{"result":design_result,"blocking_reasons":payload["design_blocking_reasons"],"design_readiness":{"result":design_result,"blocking_reasons":payload["design_blocking_reasons"],"ready_for_runtime_authoritative_design":design_ready},"activation_readiness":{"result":activation_result,"blocking_reasons":payload["activation_blocking_reasons"],"ready_for_runtime_activation_review":activation_ready},"ready_for_runtime_authoritative_design":design_ready,"ready_for_runtime_activation_review":activation_ready,"ready_for_runtime_activation":False,"ready_for_external_execution":False,"ready_for_financial_execution":False},
        "runtime_effect":{"grants_runtime_permission":False,"grants_runtime_activation":False,"grants_core_activation":False,"grants_tool_enablement":False,"grants_program_enablement":False,"grants_executor_enablement":False,"grants_external_execution":False,"grants_financial_execution":False,"consumes_approval":False,"mutates_runtime":False},
        "integrity":{"hash_schema":"runtime_promotion_readiness_fingerprint_payload.v0.1","readiness_fingerprint_payload":payload,"readiness_sha256":sha256_value(payload)},"created_at":FIXED_NOW,
    }


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


def positive_records() -> tuple[dict, dict, dict]:
    blocked = build_entry_plan(readiness(design_ready=True, activation_ready=False), readiness_ref="synthetic:test:activation-blocked", created_at=FIXED_NOW)
    ready = build_entry_plan(readiness(design_ready=True, activation_ready=True), readiness_ref="synthetic:test:activation-ready", created_at=FIXED_NOW)
    evidence = build_disabled_entry_evidence(ready, plan_ref="in_memory:synthetic-ready-plan", created_at=FIXED_NOW)
    for label, record, schema in [
        ("blocked plan", blocked, "schemas/runtime_authoritative_read_only_entry_plan.v0.1.schema.json"),
        ("ready plan", ready, "schemas/runtime_authoritative_read_only_entry_plan.v0.1.schema.json"),
        ("disabled evidence", evidence, "schemas/disabled_runtime_authoritative_read_only_entry_evidence.v0.1.schema.json"),
    ]:
        validate_schema(record, schema, label)
    validate_entry_plan_semantics(blocked)
    validate_entry_plan_semantics(ready)
    validate_disabled_entry_evidence_semantics(evidence)
    assert blocked["decision"]["result"] == "BLOCKED_NOT_READY"
    assert ready["decision"]["result"] == "READY_FOR_THOMAS_ENTRY_APPROVAL_DESIGN"
    assert ready["decision"]["ready_for_runtime_entry"] is False
    assert evidence["decision"]["result"] == "BLOCKED_DISABLED_ENTRY_ADAPTER"

    example_specs = [
        (
            "examples/runtime_authoritative_read_only_entry/runtime_authoritative_read_only_entry_plan_blocked_v0.1.yaml",
            "schemas/runtime_authoritative_read_only_entry_plan.v0.1.schema.json",
            validate_entry_plan_semantics,
        ),
        (
            "examples/runtime_authoritative_read_only_entry/runtime_authoritative_read_only_entry_plan_ready_for_approval_design_v0.1.yaml",
            "schemas/runtime_authoritative_read_only_entry_plan.v0.1.schema.json",
            validate_entry_plan_semantics,
        ),
        (
            "examples/runtime_authoritative_read_only_entry/disabled_runtime_authoritative_read_only_entry_evidence_v0.1.yaml",
            "schemas/disabled_runtime_authoritative_read_only_entry_evidence.v0.1.schema.json",
            validate_disabled_entry_evidence_semantics,
        ),
    ]
    for rel, schema, semantic in example_specs:
        record = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise AssertionError(f"{rel}: expected YAML object")
        validate_schema(record, schema, rel)
        semantic(record)
    return blocked, ready, evidence


def negative_cases(ready_plan: dict, evidence: dict) -> int:
    cases = [
        ("plan_runtime_source", ready_plan, "runtime_source_of_truth", True, EntryPlanError),
        ("plan_kernel_version", ready_plan, "kernel.kernel_version", "9.9.9", EntryPlanError),
        ("plan_requested_runs", ready_plan, "kernel.requested_run_count", 2, EntryPlanError),
        ("plan_runtime_enabled", ready_plan, "kernel.runtime_authoritative_mode_enabled", True, EntryPlanError),
        ("plan_write_allowed", ready_plan, "entry_scope.filesystem_write_allowed", True, EntryPlanError),
        ("plan_model_allowed", ready_plan, "entry_scope.model_invocation_allowed", True, EntryPlanError),
        ("plan_tool_allowed", ready_plan, "entry_scope.tool_execution_allowed", True, EntryPlanError),
        ("plan_program_allowed", ready_plan, "entry_scope.program_execution_allowed", True, EntryPlanError),
        ("plan_network_allowed", ready_plan, "entry_scope.network_access_allowed", True, EntryPlanError),
        ("plan_external_allowed", ready_plan, "entry_scope.external_action_allowed", True, EntryPlanError),
        ("plan_financial_allowed", ready_plan, "entry_scope.financial_action_allowed", True, EntryPlanError),
        ("plan_runtime_mutation", ready_plan, "entry_scope.runtime_mutation_allowed", True, EntryPlanError),
        ("plan_approval_not_required", ready_plan, "approval_boundary.separate_action_approval_required", False, EntryPlanError),
        ("plan_wrong_permission_scope", ready_plan, "approval_boundary.permission_scope", "INTERNAL_READ", EntryPlanError),
        ("plan_approval_present", ready_plan, "approval_boundary.approval_present", True, EntryPlanError),
        ("plan_approval_verified", ready_plan, "approval_boundary.approval_verified", True, EntryPlanError),
        ("plan_consumption_supported", ready_plan, "approval_boundary.approval_consumption_supported_by_current_contract", True, EntryPlanError),
        ("plan_no_future_atomic", ready_plan, "approval_boundary.future_atomic_consumption_required", False, EntryPlanError),
        ("plan_handoff_allowed", ready_plan, "approval_boundary.executor_handoff_allowed", True, EntryPlanError),
        ("plan_ready_for_entry", ready_plan, "decision.ready_for_runtime_entry", True, EntryPlanError),
        ("plan_entry_performed", ready_plan, "decision.entry_performed", True, EntryPlanError),
        ("plan_grants_activation", ready_plan, "runtime_effect.grants_runtime_activation", True, EntryPlanError),
        ("plan_starts_session", ready_plan, "runtime_effect.starts_runtime_session", True, EntryPlanError),
        ("plan_bad_hash", ready_plan, "integrity.entry_plan_sha256", "sha256:" + "0"*64, EntryPlanError),
        ("evidence_adapter_enabled", evidence, "adapter.enabled", True, DisabledEntryAdapterError),
        ("evidence_source_truth", evidence, "adapter.runtime_source_of_truth", True, DisabledEntryAdapterError),
        ("evidence_entry_call", evidence, "adapter.entry_call_allowed", True, DisabledEntryAdapterError),
        ("evidence_entry_performed", evidence, "decision.entry_performed", True, DisabledEntryAdapterError),
        ("evidence_session_started", evidence, "decision.runtime_authoritative_session_started", True, DisabledEntryAdapterError),
        ("evidence_handoff", evidence, "decision.executor_handoff_performed", True, DisabledEntryAdapterError),
        ("evidence_approval_consumed", evidence, "decision.approval_consumed", True, DisabledEntryAdapterError),
        ("evidence_network", evidence, "runtime_effect.network_call_performed", True, DisabledEntryAdapterError),
        ("evidence_runtime_mutation", evidence, "runtime_effect.runtime_mutation_performed", True, DisabledEntryAdapterError),
        ("evidence_core_activation", evidence, "runtime_effect.core_activation_performed", True, DisabledEntryAdapterError),
        ("evidence_bad_hash", evidence, "integrity.evidence_sha256", "sha256:" + "0"*64, DisabledEntryAdapterError),
    ]
    for case_id, base, path, value, expected in cases:
        record = deepcopy(base)
        set_path(record, path, value)
        try:
            if expected is EntryPlanError:
                validate_entry_plan_semantics(record)
            else:
                validate_disabled_entry_evidence_semantics(record)
        except expected:
            continue
        raise AssertionError(f"{case_id}: expected {expected.__name__}")
    return len(cases)


def main() -> int:
    static_runtime_review()
    blocked, ready, evidence = positive_records()
    negative_count = negative_cases(ready, evidence)
    registry = yaml.safe_load((ROOT / "05_REGISTRIES/I0_5_2_RUNTIME_AUTHORITATIVE_READ_ONLY_ENTRY_COMPONENTS_REVIEW_ONLY.yaml").read_text(encoding="utf-8"))
    assert registry["runtime_source_of_truth"] is False
    assert registry["runtime_authoritative_mode_enabled"] is False
    assert all(component["enabled"] is False for component in registry["components"])
    assert all(value is False for value in registry["review_only_effects"].values())
    print("PASS: I0.5.2 Runtime-authoritative read-only Entry Design validation completed")
    print("Positive Entry Plans: 2 PASS")
    print("Disabled Entry Evidence: 1 PASS")
    print(f"Fail-closed mutation fixtures: {negative_count} PASS")
    print("No Runtime-authoritative session, Approval consumption, Executor handoff, model/Tool/Program/network/write/external/financial action, Runtime mutation, Permission expansion, Authority expansion, or Core activation occurred.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
