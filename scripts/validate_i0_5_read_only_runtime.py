#!/usr/bin/env python3
from __future__ import annotations

import ast
import shutil
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.read_only_kernel.integrity import sha256_file, sha256_record, sha256_value
from runtime.read_only_kernel.kernel import run_bundle
from runtime.read_only_kernel.schema_validation import validate_against_schema as validate_runtime_schema

FIXED_NOW = "2026-07-13T04:02:00Z"
BUNDLE_REL = "examples/read_only_runtime/input/read_only_runtime_input_bundle_v0.1.yaml"
CASES_REL = "tests/fixtures/read_only_runtime/mutation_cases.yaml"
FORBIDDEN_IMPORT_ROOTS = {
    "aiohttp", "boto3", "ftplib", "httpx", "paramiko", "playwright",
    "requests", "selenium", "smtplib", "socket", "subprocess", "telnetlib",
    "urllib", "webbrowser", "openai", "anthropic", "google.generativeai",
}
FORBIDDEN_CALL_NAMES = {"eval", "exec", "compile", "__import__"}
FORBIDDEN_ATTR_CALLS = {
    "write_text", "write_bytes", "unlink", "rename", "mkdir",
    "rmdir", "touch", "chmod", "symlink_to", "hardlink_to",
}


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected object")
    return value


def write_yaml(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n")


def validate_schema(data: Any, schema_path: Path, label: str) -> None:
    validate_runtime_schema(data, schema_path, label)


def set_path(record: Any, dotted_path: str, value: Any) -> None:
    current = record
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    last = parts[-1]
    if isinstance(current, list):
        current[int(last)] = value
    else:
        current[last] = value


def rebuild_bundle(
    repo: Path,
    bundle: dict[str, Any],
    *,
    refresh_governance_binding: bool = True,
) -> None:
    refs = bundle["refs"]
    bundle["sha256"] = {name: sha256_file(repo / ref) for name, ref in refs.items()}
    if refresh_governance_binding and "governance_policy" in refs:
        governance_policy = load_yaml(repo / refs["governance_policy"])
        bundle["governance_binding"] = {
            "policy_id": governance_policy.get("policy_id"),
            "policy_version": governance_policy.get("policy_version"),
            "policy_ref": refs["governance_policy"],
            "policy_sha256": bundle["sha256"]["governance_policy"],
        }
    payload = {
        "schema_version": "read_only_runtime_input_bundle_fingerprint_payload.v0.1",
        "bundle_id": bundle["bundle_id"],
        "run_mode": bundle["run_mode"],
        "refs": bundle["refs"],
        "sha256": bundle["sha256"],
        "governance_binding": bundle["governance_binding"],
        "constraints": bundle["constraints"],
        "created_at": bundle["created_at"],
    }
    bundle["integrity"] = {
        "hash_schema": "read_only_runtime_input_bundle_fingerprint_payload.v0.1",
        "bundle_fingerprint_payload": payload,
        "bundle_sha256": sha256_value(payload),
    }


def copy_test_repo(destination: Path) -> None:
    for rel in [
        "examples/read_only_runtime/input",
        "05_REGISTRIES/I0_4_RUNTIME_CONTRACT_SET_INDEX.yaml",
        "governance/GOVERNANCE_POLICY.yaml",
        "schemas/read_only_runtime_input_bundle.v0.1.schema.json",
    ]:
        source = ROOT / rel
        target = destination / rel
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def static_code_review() -> None:
    runtime_root = ROOT / "runtime/read_only_kernel"
    for path in sorted(runtime_root.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=path.as_posix())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in FORBIDDEN_IMPORT_ROOTS:
                        raise AssertionError(f"{path}: forbidden import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                if root in FORBIDDEN_IMPORT_ROOTS:
                    raise AssertionError(f"{path}: forbidden import {node.module}")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALL_NAMES:
                    raise AssertionError(f"{path}: forbidden call {node.func.id}")
                if isinstance(node.func, ast.Attribute) and node.func.attr in FORBIDDEN_ATTR_CALLS:
                    raise AssertionError(f"{path}: forbidden mutating call {node.func.attr}")
                if isinstance(node.func, ast.Name) and node.func.id == "open":
                    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                        mode = str(node.args[1].value)
                        if any(flag in mode for flag in "wax+"):
                            raise AssertionError(f"{path}: forbidden write-mode open({mode!r})")


def validate_positive() -> dict[str, Any]:
    bundle = load_yaml(ROOT / BUNDLE_REL)
    validate_schema(bundle, ROOT / "schemas/read_only_runtime_input_bundle.v0.1.schema.json", "input bundle")
    governance_binding = bundle["governance_binding"]
    governance_policy = load_yaml(ROOT / bundle["refs"]["governance_policy"])
    assert governance_binding["policy_id"] == governance_policy["policy_id"]
    assert governance_binding["policy_version"] == governance_policy["policy_version"]
    assert governance_binding["policy_ref"] == bundle["refs"]["governance_policy"]
    assert governance_binding["policy_sha256"] == bundle["sha256"]["governance_policy"]
    assert governance_binding["policy_sha256"] == sha256_file(
        ROOT / bundle["refs"]["governance_policy"]
    )
    result = run_bundle(ROOT, ROOT / BUNDLE_REL, now=FIXED_NOW)
    validate_schema(result, ROOT / "schemas/read_only_runtime_run.v0.1.schema.json", "runtime run")
    assert result["summary"]["result"] == "COMPLETED_READ_ONLY_REPLAY"
    assert result["worker"] == {
        "invoked": True,
        "result": "PASS",
        "agent_invocations": 1,
        "model_calls": 0,
        "tool_calls": 0,
        "program_calls": 0,
        "network_calls": 0,
        "filesystem_writes": 0,
        "external_actions": 0,
    }
    assert result["effects"]["filesystem_write_performed"] is False
    assert result["effects"]["runtime_mutation_performed"] is False
    assert result["effects"]["core_activation_created"] is False
    assert result["outputs"]["agent_output_sha256"] == sha256_record(result["outputs"]["agent_output"])
    assert result["outputs"]["validation_result_sha256"] == sha256_record(result["outputs"]["validation_result"])
    assert result["outputs"]["final_task_sha256"] == sha256_record(result["outputs"]["final_task"])
    assert result["outputs"]["final_task"] == load_yaml(ROOT / "examples/read_only_runtime/input/task_v0.3_contract_inspection.yaml")
    assert result["outputs"]["validation_result"]["validation"]["result"] == "PASS"
    assert result["outputs"]["validation_result"]["validator"]["independence_verified"] is False
    assert result["outputs"]["validation_result"]["validation"]["recommended_next_state"] == "REPLAY_COMPLETED"
    assert result["lifecycle"]["initial_state"] == "REPLAY_QUEUED"
    assert result["lifecycle"]["final_state"] == "REPLAY_COMPLETED"
    assert result["lifecycle"]["source_task_state_unchanged"] is True
    assert result["effects"]["filesystem_read_count"] > 0
    assert result["governance"]["verification_status"] == "REFERENCES_PRESENT_NOT_VERIFIED"
    preflight_check_ids = {item["check_id"] for item in result["preflight"]["checks"]}
    for required_check in (
        "governance_policy_binding",
        "governance_policy_active_authority",
        "governance_policy_runtime_effect_disabled",
        "governance_policy_fail_closed",
    ):
        assert required_check in preflight_check_ids
    assert result["input_bundle"]["record_sha256"]["governance_policy"] == governance_binding["policy_sha256"]
    assert result["integrity"]["run_sha256"] == sha256_value(result["integrity"]["run_fingerprint_payload"])

    audits = result["outputs"]["audit_events"]
    assert len(audits) == 6
    previous = None
    for index, event in enumerate(audits, start=1):
        assert event["lineage"]["sequence_number"] == index
        assert event["lineage"]["previous_event_sha256"] == previous
        assert event["integrity"]["event_sha256"] == sha256_value(event["integrity"]["event_fingerprint_payload"])
        assert event["runtime_effect"]["mutates_runtime"] is False
        previous = event["integrity"]["event_sha256"]

    optional_schema_checks = [
        ("agent_output", "schemas/agent_output.v0.2.schema.json"),
        ("final_task", "schemas/task.v0.3.schema.json"),
        ("validation_result", "schemas/validation_result.v0.1.schema.json"),
    ]
    for key, rel in optional_schema_checks:
        schema_path = ROOT / rel
        if schema_path.exists():
            validate_schema(result["outputs"][key], schema_path, key)
    audit_schema = ROOT / "schemas/audit_event.v0.1.schema.json"
    if audit_schema.exists():
        for event in audits:
            validate_schema(event, audit_schema, f"audit {event['audit_event_id']}")
    return result


def validate_negative_cases() -> int:
    fixture = load_yaml(ROOT / CASES_REL)
    cases = fixture.get("cases", [])
    if not cases:
        raise AssertionError("negative fixture set is empty")

    for case in cases:
        with tempfile.TemporaryDirectory(prefix="i0_5_readonly_") as tmp:
            repo = Path(tmp)
            copy_test_repo(repo)
            bundle_path = repo / BUNDLE_REL
            bundle = load_yaml(bundle_path)
            target_name = case["target"]
            if target_name == "bundle":
                target = bundle
            else:
                ref = bundle["refs"][target_name]
                target_path = repo / ref
                target = load_yaml(target_path)
                set_path(target, case["path"], deepcopy(case.get("value")))
                write_yaml(target_path, target)

            if target_name == "bundle":
                set_path(target, case["path"], deepcopy(case.get("value")))
                if case.get("rebuild_integrity"):
                    rebuild_bundle(
                        repo,
                        bundle,
                        refresh_governance_binding=not case["path"].startswith(
                            "governance_binding."
                        ),
                    )
                write_yaml(bundle_path, bundle)
            else:
                rebuild_bundle(repo, bundle)
                write_yaml(bundle_path, bundle)

            result = run_bundle(repo, bundle_path, now=FIXED_NOW)
            validate_schema(result, ROOT / "schemas/read_only_runtime_run.v0.1.schema.json", case["case_id"])
            if result["summary"]["result"] != "BLOCKED":
                raise AssertionError(f"{case['case_id']}: expected BLOCKED")
            reasons = result["preflight"]["reason_codes"]
            if case["expected_reason"] not in reasons:
                raise AssertionError(
                    f"{case['case_id']}: expected {case['expected_reason']}, got {reasons}: {result['summary']['message']}"
                )
            if result["worker"]["invoked"] is not False:
                raise AssertionError(f"{case['case_id']}: worker must not be invoked")
            if any([
                result["effects"]["filesystem_write_performed"],
                result["effects"]["model_invocation_performed"],
                result["effects"]["tool_execution_performed"],
                result["effects"]["program_execution_performed"],
                result["effects"]["network_call_performed"],
                result["effects"]["external_action_performed"],
                result["effects"]["runtime_mutation_performed"],
            ]):
                raise AssertionError(f"{case['case_id']}: prohibited effect flag became true")
    return len(cases)


def validate_regressions() -> None:
    missing = run_bundle(ROOT, ROOT / "examples/read_only_runtime/input/does_not_exist.yaml", now=FIXED_NOW)
    assert missing["summary"]["result"] == "BLOCKED"
    assert missing["effects"]["filesystem_read_performed"] is False
    assert missing["effects"]["filesystem_read_count"] == 0

    with tempfile.TemporaryDirectory(prefix="i0_5_schema_coverage_") as tmp:
        repo = Path(tmp)
        copy_test_repo(repo)
        bundle_path = repo / BUNDLE_REL
        bundle = load_yaml(bundle_path)
        bundle["unexpected_runtime_field"] = True
        write_yaml(bundle_path, bundle)
        result = run_bundle(repo, bundle_path, now=FIXED_NOW)
        assert result["summary"]["result"] == "BLOCKED"
        assert result["preflight"]["reason_codes"] == ["INPUT_BUNDLE_INVALID"]
        assert "Additional properties are not allowed" in result["summary"]["message"]


def main() -> int:
    static_code_review()
    positive = validate_positive()
    negative_count = validate_negative_cases()
    validate_regressions()
    print("PASS: I0.5 read-only runtime kernel validation completed")
    print("Positive development replay: 1 PASS")
    print(f"Fail-closed mutation fixtures: {negative_count} PASS")
    print(f"Lifecycle transitions: {len(positive['lifecycle']['transitions'])} PASS")
    print(f"Audit events: {len(positive['outputs']['audit_events'])} PASS")
    print("Schema-enforcement and accurate read-effect regressions: PASS")
    print("No model, Tool, Program, network, filesystem write, external action, Approval consumption, Executor handoff, Runtime mutation, Permission expansion, Authority expansion, or Core activation occurred.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
