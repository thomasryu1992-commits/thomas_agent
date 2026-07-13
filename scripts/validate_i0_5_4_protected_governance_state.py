#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.protected_governance_state import (
    ProtectedGovernanceStateStore,
    ProtectedStateConflict,
    ProtectedStateError,
    SimulatedCrashAfterCommit,
    StoreConfig,
    inspect_recovery_state,
    validate_durable_transition_result_semantics,
    validate_recovery_report_semantics,
)
from runtime.read_only_kernel.integrity import sha256_value

AUTH_REL = "examples/runtime_entry_authorization/SYNTHETIC_ONLY_runtime_entry_authorization_approved_not_consumed_v0.1.yaml"
REAL_AUTH_REL = "examples/runtime_entry_authorization/runtime_entry_authorization_ready_for_thomas_action_approval_review_v0.1.yaml"
REGISTRY_REL = "05_REGISTRIES/I0_5_4_PROTECTED_GOVERNANCE_STATE_COMPONENTS_REVIEW_ONLY.yaml"
FIXTURE_REL = "tests/fixtures/protected_governance_state/mutation_cases.yaml"
FIXED_CREATED_AT = "2026-07-13T10:00:00Z"
FIXED_REGISTERED_AT = "2026-07-13T10:00:01Z"
FIXED_TRANSITION_AT = "2026-07-13T10:00:02Z"
FIXED_RECOVERY_AT = "2026-07-13T10:00:03Z"

FORBIDDEN_IMPORT_ROOTS = {
    "aiohttp", "anthropic", "boto3", "ftplib", "httpx", "openai",
    "paramiko", "playwright", "requests", "selenium", "smtplib",
    "socket", "subprocess", "telnetlib", "urllib", "webbrowser",
}
FORBIDDEN_CALL_TOKENS = {
    "execute_contract_inspection_worker",
    "run_bundle",
    "start_runtime_session",
    "place_order",
    "send_email",
}


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path}: expected YAML object")
    return value


def validate_schema(data: Any, rel: str, label: str) -> None:
    schema = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    issues = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(data),
        key=lambda item: list(item.path),
    )
    if issues:
        raise AssertionError(
            f"{label}: schema errors: "
            + "; ".join(f"{list(item.path)}: {item.message}" for item in issues[:10])
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


def recompute_transition_hash(record: dict[str, Any]) -> None:
    payload = record["integrity"]["result_fingerprint_payload"]
    status = record["status"]
    payload["result"] = status
    payload["blocking_reasons"] = record["decision"]["blocking_reasons"] if status == "BLOCKED_FAIL_CLOSED" else payload.pop("blocking_reasons", None)
    if status != "BLOCKED_FAIL_CLOSED" and "blocking_reasons" in payload:
        payload.pop("blocking_reasons", None)
    record["integrity"]["result_sha256"] = sha256_value(payload)


def recompute_recovery_hash(record: dict[str, Any]) -> None:
    payload = record["integrity"]["recovery_fingerprint_payload"]
    payload["status"] = record["status"]
    payload["blocking_reasons"] = deepcopy(record["decision"]["blocking_reasons"])
    payload["anomalies"] = deepcopy(record["anomalies"])
    payload["manual_review_sessions"] = deepcopy(record["manual_review_sessions"])
    record["integrity"]["recovery_sha256"] = sha256_value(payload)


def static_code_review() -> None:
    root = ROOT / "runtime/protected_governance_state"
    for path in sorted(root.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=path.as_posix())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in FORBIDDEN_IMPORT_ROOTS:
                        raise AssertionError(f"{path}: forbidden import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in FORBIDDEN_IMPORT_ROOTS:
                    raise AssertionError(f"{path}: forbidden import {node.module}")
        for token in FORBIDDEN_CALL_TOKENS:
            if token in source:
                raise AssertionError(f"{path}: forbidden Runtime/external call token {token}")
    store_source = (root / "sqlite_store.py").read_text(encoding="utf-8")
    for required in ["BEGIN IMMEDIATE", "synchronous = FULL", "journal_mode = DELETE", "SYNTHETIC_TEST_ONLY"]:
        if required not in store_source:
            raise AssertionError(f"SQLite candidate missing required durability boundary: {required}")


def validate_registry_semantics(record: dict[str, Any]) -> None:
    if record.get("schema_version") != "i0_5_4_protected_governance_state_components.v0.1":
        raise AssertionError("I0.5.4 registry schema mismatch")
    if record.get("owner") != "Thomas" or record.get("runtime_source_of_truth") is not False or record.get("runtime_authoritative_mode_enabled") is not False:
        raise AssertionError("I0.5.4 registry ownership/source boundary mismatch")
    components = record.get("components")
    if not isinstance(components, list) or len(components) != 3:
        raise AssertionError("I0.5.4 registry must contain exactly three components")
    expected = [
        ("thomas.protected_governance_state.sqlite_candidate", "0.1.0"),
        ("thomas.runtime_entry.durable_cas.sqlite_candidate", "0.1.0"),
        ("thomas.runtime_entry.crash_recovery.inspector", "0.1.0"),
    ]
    for item, (component_id, version) in zip(components, expected):
        if item.get("component_id") != component_id or item.get("version") != version:
            raise AssertionError("I0.5.4 component identity/version mismatch")
        if item.get("enabled") is not False or item.get("runtime_authoritative") is not False:
            raise AssertionError("I0.5.4 components must remain disabled/non-authoritative")
    store, transition, recovery = components
    for key in [
        "real_approval_consumption_allowed", "runtime_session_start_allowed",
        "kernel_call_allowed", "network_allowed", "domain_write_allowed",
        "workspace_write_allowed", "core_write_allowed", "external_write_allowed",
        "financial_write_allowed",
    ]:
        if store.get(key) is not False:
            raise AssertionError(f"I0.5.4 store must keep {key}=false")
    for key in ["real_approval_consumption_allowed", "runtime_session_start_allowed", "kernel_call_allowed", "executor_handoff_allowed"]:
        if transition.get(key) is not False:
            raise AssertionError(f"I0.5.4 transition must keep {key}=false")
    if recovery.get("read_only_inspection") is not True:
        raise AssertionError("I0.5.4 recovery inspector must be read-only")
    for key in ["automatic_retry_allowed", "automatic_resume_allowed", "state_write_allowed"]:
        if recovery.get(key) is not False:
            raise AssertionError(f"I0.5.4 recovery must keep {key}=false")
    effect = record.get("review_only_effects", {})
    if any(value is not False for value in effect.values()):
        raise AssertionError("I0.5.4 registry effects must remain false")


def positive_flows() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    auth = load_yaml(ROOT / AUTH_REL)
    real_auth = load_yaml(ROOT / REAL_AUTH_REL)

    try:
        ProtectedGovernanceStateStore(StoreConfig(Path(tempfile.gettempdir()), allow_test_writes=False))
    except ProtectedStateError:
        pass
    else:
        raise AssertionError("protected-state store opened without explicit test-write enablement")

    with tempfile.TemporaryDirectory(prefix="i054_commit_") as temp:
        root = Path(temp)
        store = ProtectedGovernanceStateStore(StoreConfig(root, allow_test_writes=True))
        initial = store.initialize(created_at=FIXED_CREATED_AT)
        validate_schema(initial, "schemas/protected_governance_state_snapshot.v0.1.schema.json", "initial snapshot")
        assert initial["counts"] == {"authorizations": 0, "sessions": 0, "transition_receipts": 0, "audit_events": 0}
        registered = store.register_synthetic_authorization(auth, created_at=FIXED_REGISTERED_AT)
        assert registered["counts"]["authorizations"] == 1
        committed = store.attempt_atomic_transition(
            auth,
            expected_authorization_version=0,
            transition_id="transition_i054_committed",
            session_id="session_i054_committed",
            created_at=FIXED_TRANSITION_AT,
        )
        validate_schema(committed, "schemas/runtime_entry_durable_transition_result.v0.1.schema.json", "committed transition")
        validate_durable_transition_result_semantics(committed)
        audit_schema = ROOT / "schemas/audit_event.v0.1.schema.json"
        if audit_schema.exists():
            for index, event in enumerate(committed["audit"]["events"], start=1):
                validate_schema(event, "schemas/audit_event.v0.1.schema.json", f"committed audit event {index}")
        assert committed["status"] == "COMMITTED_SYNTHETIC_TEST_ONLY"
        assert committed["runtime_effect"]["test_only_local_governance_state_write_performed"] is True
        reopened = ProtectedGovernanceStateStore(StoreConfig(root, allow_test_writes=True))
        persisted = reopened.snapshot(created_at=FIXED_RECOVERY_AT)
        validate_schema(persisted, "schemas/protected_governance_state_snapshot.v0.1.schema.json", "reopened snapshot")
        assert persisted["counts"] == {"authorizations": 1, "sessions": 1, "transition_receipts": 1, "audit_events": 3}
        assert persisted["authorizations"][0]["state"] == "CONSUMED"
        assert persisted["sessions"][0]["state"] == "RESERVED"
        replay = reopened.attempt_atomic_transition(
            auth,
            expected_authorization_version=0,
            transition_id="transition_i054_replay",
            session_id="session_i054_replay",
            created_at=FIXED_RECOVERY_AT,
        )
        validate_schema(replay, "schemas/runtime_entry_durable_transition_result.v0.1.schema.json", "replay block")
        validate_durable_transition_result_semantics(replay)
        assert replay["status"] == "BLOCKED_FAIL_CLOSED"
        assert replay["decision"]["blocking_reasons"] == ["AUTHORIZATION_ALREADY_CONSUMED_OR_BLOCKED"]
        after_replay = reopened.snapshot(created_at="2026-07-13T10:00:04Z")
        assert after_replay["counts"] == persisted["counts"]
        recovery = inspect_recovery_state(reopened, created_at="2026-07-13T10:00:05Z")
        validate_schema(recovery, "schemas/runtime_entry_recovery_report.v0.1.schema.json", "manual recovery")
        validate_recovery_report_semantics(recovery)
        assert recovery["status"] == "MANUAL_REVIEW_REQUIRED_NO_REUSE"

    with tempfile.TemporaryDirectory(prefix="i054_precommit_") as temp:
        root = Path(temp)
        store = ProtectedGovernanceStateStore(StoreConfig(root, allow_test_writes=True))
        store.initialize(created_at=FIXED_CREATED_AT)
        store.register_synthetic_authorization(auth, created_at=FIXED_REGISTERED_AT)
        crash_before = store.attempt_atomic_transition(
            auth,
            expected_authorization_version=0,
            transition_id="transition_i054_crash_before",
            session_id="session_i054_crash_before",
            created_at=FIXED_TRANSITION_AT,
            simulate_crash_before_commit=True,
        )
        validate_schema(crash_before, "schemas/runtime_entry_durable_transition_result.v0.1.schema.json", "crash-before block")
        validate_durable_transition_result_semantics(crash_before)
        assert crash_before["decision"]["blocking_reasons"] == ["SIMULATED_CRASH_BEFORE_COMMIT_ROLLED_BACK"]
        rolled_back = store.snapshot(created_at=FIXED_RECOVERY_AT)
        assert rolled_back["authorizations"][0]["state"] == "UNUSED"
        assert rolled_back["counts"]["sessions"] == 0
        clean_recovery = inspect_recovery_state(store, created_at="2026-07-13T10:00:04Z")
        validate_recovery_report_semantics(clean_recovery)
        assert clean_recovery["status"] == "CLEAN_NO_PENDING_SESSION"

    with tempfile.TemporaryDirectory(prefix="i054_postcommit_") as temp:
        root = Path(temp)
        store = ProtectedGovernanceStateStore(StoreConfig(root, allow_test_writes=True))
        store.initialize(created_at=FIXED_CREATED_AT)
        store.register_synthetic_authorization(auth, created_at=FIXED_REGISTERED_AT)
        try:
            store.attempt_atomic_transition(
                auth,
                expected_authorization_version=0,
                transition_id="transition_i054_crash_after",
                session_id="session_i054_crash_after",
                created_at=FIXED_TRANSITION_AT,
                simulate_crash_after_commit=True,
            )
        except SimulatedCrashAfterCommit:
            pass
        else:
            raise AssertionError("expected simulated crash after durable commit")
        reopened = ProtectedGovernanceStateStore(StoreConfig(root, allow_test_writes=True))
        crash_recovery = inspect_recovery_state(reopened, created_at=FIXED_RECOVERY_AT)
        validate_recovery_report_semantics(crash_recovery)
        assert crash_recovery["status"] == "MANUAL_REVIEW_REQUIRED_NO_REUSE"
        assert crash_recovery["decision"]["authorization_reuse_allowed"] is False
        assert crash_recovery["decision"]["automatic_session_resume_allowed"] is False

    with tempfile.TemporaryDirectory(prefix="i054_concurrent_") as temp:
        root = Path(temp)
        store = ProtectedGovernanceStateStore(StoreConfig(root, allow_test_writes=True))
        store.initialize(created_at=FIXED_CREATED_AT)
        store.register_synthetic_authorization(auth, created_at=FIXED_REGISTERED_AT)

        def attempt(index: int) -> dict[str, Any]:
            local_store = ProtectedGovernanceStateStore(StoreConfig(root, allow_test_writes=True))
            return local_store.attempt_atomic_transition(
                auth,
                expected_authorization_version=0,
                transition_id=f"transition_i054_concurrent_{index}",
                session_id=f"session_i054_concurrent_{index}",
                created_at=FIXED_TRANSITION_AT,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            concurrent_results = list(executor.map(attempt, [1, 2]))
        statuses = sorted(item["status"] for item in concurrent_results)
        assert statuses == ["BLOCKED_FAIL_CLOSED", "COMMITTED_SYNTHETIC_TEST_ONLY"]
        concurrent_snapshot = store.snapshot(created_at=FIXED_RECOVERY_AT)
        assert concurrent_snapshot["counts"] == {
            "authorizations": 1,
            "sessions": 1,
            "transition_receipts": 1,
            "audit_events": 3,
        }

    with tempfile.TemporaryDirectory(prefix="i054_real_block_") as temp:
        store = ProtectedGovernanceStateStore(StoreConfig(Path(temp), allow_test_writes=True))
        store.initialize(created_at=FIXED_CREATED_AT)
        try:
            store.register_synthetic_authorization(real_auth, created_at=FIXED_REGISTERED_AT)
        except ProtectedStateError:
            pass
        else:
            raise AssertionError("non-synthetic real review Authorization was accepted by I0.5.4 test store")

    with tempfile.TemporaryDirectory(prefix="i054_expired_") as temp:
        store = ProtectedGovernanceStateStore(StoreConfig(Path(temp), allow_test_writes=True))
        store.initialize(created_at=FIXED_CREATED_AT)
        store.register_synthetic_authorization(auth, created_at=FIXED_REGISTERED_AT)
        expired = store.attempt_atomic_transition(
            auth,
            expected_authorization_version=0,
            transition_id="transition_i054_expired",
            session_id="session_i054_expired",
            created_at="2026-07-13T10:11:00Z",
        )
        validate_durable_transition_result_semantics(expired)
        assert expired["decision"]["blocking_reasons"] == ["AUTHORIZATION_EXPIRED"]

    return committed, replay, recovery, clean_recovery


def validate_mutations(committed: dict[str, Any], recovery: dict[str, Any]) -> tuple[int, int, int]:
    fixture = load_yaml(ROOT / FIXTURE_REL)
    transition_count = 0
    for case in fixture["transition_cases"]:
        mutated = deepcopy(committed)
        set_path(mutated, case["path"], deepcopy(case.get("value")))
        recompute_transition_hash(mutated)
        try:
            validate_durable_transition_result_semantics(mutated)
        except Exception:
            transition_count += 1
        else:
            raise AssertionError(f"{case['case_id']}: mutated transition unexpectedly passed")

    recovery_count = 0
    for case in fixture["recovery_cases"]:
        mutated = deepcopy(recovery)
        set_path(mutated, case["path"], deepcopy(case.get("value")))
        recompute_recovery_hash(mutated)
        try:
            validate_recovery_report_semantics(mutated)
        except Exception:
            recovery_count += 1
        else:
            raise AssertionError(f"{case['case_id']}: mutated recovery unexpectedly passed")

    registry = load_yaml(ROOT / REGISTRY_REL)
    validate_registry_semantics(registry)
    registry_count = 0
    for case in fixture["registry_cases"]:
        mutated = deepcopy(registry)
        set_path(mutated, case["path"], deepcopy(case.get("value")))
        try:
            validate_registry_semantics(mutated)
        except Exception:
            registry_count += 1
        else:
            raise AssertionError(f"{case['case_id']}: mutated registry unexpectedly passed")
    return transition_count, recovery_count, registry_count


def main() -> int:
    static_code_review()
    committed, replay, recovery, clean_recovery = positive_flows()
    transition_count, recovery_count, registry_count = validate_mutations(committed, recovery)
    print("PASS: I0.5.4 protected local governance state candidate validation completed")
    print("Synthetic durable commit/reopen/replay block: PASS")
    print("Crash-before-commit rollback: PASS")
    print("Crash-after-commit recovery/manual-review/no-reuse: PASS")
    print("Two concurrent writers: exactly one synthetic commit and one fail-closed block PASS")
    print("Non-synthetic Authorization and expired Authorization: BLOCKED")
    print(f"Transition fail-closed mutations: {transition_count} PASS")
    print(f"Recovery fail-closed mutations: {recovery_count} PASS")
    print(f"Registry fail-closed mutations: {registry_count} PASS")
    print("No real Approval verification/consumption, Runtime Session start, Kernel call, model, Tool, Program, Executor, network, domain/workspace/Core/external/financial write, automatic retry, or automatic resume occurred.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
