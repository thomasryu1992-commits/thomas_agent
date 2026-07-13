#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def load_schema(rel: str) -> dict:
    path = ROOT / rel
    return json.loads(
        path.read_text(encoding="utf-8")
    )


def table_fields(
    text: str,
    start_heading: str,
    end_heading: str,
) -> set[str]:
    start = text.find(start_heading)

    if start < 0:
        ERRORS.append(
            f"Missing heading: {start_heading}"
        )
        return set()

    end = text.find(
        end_heading,
        start + len(start_heading),
    )

    section = (
        text[start:]
        if end < 0
        else text[start:end]
    )

    return set(
        re.findall(
            r"^\|\s*`([^`]+)`\s*\|",
            section,
            re.MULTILINE,
        )
    )


def compare_required(
    doc_rel: str,
    schema_rel: str,
    start_heading: str,
    end_heading: str,
) -> None:
    text = (
        ROOT / doc_rel
    ).read_text(encoding="utf-8")

    schema = load_schema(
        schema_rel
    )

    documented = table_fields(
        text,
        start_heading,
        end_heading,
    )
    required = set(
        schema.get("required", [])
    )

    missing_in_doc = sorted(
        required - documented
    )
    missing_in_schema = sorted(
        documented - required
    )

    if missing_in_doc:
        ERRORS.append(
            f"{doc_rel}: required Schema fields "
            f"missing from table: {missing_in_doc}"
        )

    if missing_in_schema:
        ERRORS.append(
            f"{schema_rel}: documented required "
            f"fields missing from Schema: "
            f"{missing_in_schema}"
        )


def main() -> int:
    compare_required(
        "03_ROLE_CONTRACTS/ROLE_ASSIGNMENT_CONTRACT.md",
        "schemas/role_assignment.v0.2.schema.json",
        "## 2. Required Fields",
        "## Core Binding Lineage",
    )

    compare_required(
        "docs/runtime-contracts/AGENT_OUTPUT_CONTRACT_V0.2.md",
        "schemas/agent_output.v0.2.schema.json",
        "## 2. Required Fields",
        "## Core Binding Lineage",
    )

    compare_required(
        "docs/runtime-contracts/PERMISSION_DECISION_CONTRACT_V0.3.md",
        "schemas/permission_decision.v0.3.schema.json",
        "## 2. Required Fields",
        "## 3. Thomas-Approved Operating Policy Binding",
    )

    compare_required(
        "docs/runtime-contracts/APPROVAL_CONTRACT_V0.1.md",
        "schemas/approval.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Thomas-Approved Operating Policy Binding",
    )

    compare_required(
        "docs/runtime-contracts/TOOL_REQUEST_CONTRACT_V0.1.md",
        "schemas/tool_request.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Resource Eligibility",
    )

    compare_required(
        "docs/runtime-contracts/PROGRAM_REQUEST_CONTRACT_V0.1.md",
        "schemas/program_request.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Program Eligibility",
    )

    compare_required(
        "docs/runtime-contracts/EXECUTION_REQUEST_CONTRACT_V0.1.md",
        "schemas/execution_request.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Upstream Binding",
    )

    compare_required(
        "docs/runtime-contracts/EXECUTION_RESULT_CONTRACT_V0.1.md",
        "schemas/execution_result.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Result Status",
    )

    compare_required(
        "docs/runtime-contracts/VALIDATION_RESULT_CONTRACT_V0.1.md",
        "schemas/validation_result.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Results",
    )

    compare_required(
        "docs/runtime-contracts/AUDIT_EVENT_CONTRACT_V0.1.md",
        "schemas/audit_event.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Append-Only Rule",
    )

    compare_required(
        "docs/runtime-contracts/EXECUTOR_REGISTRY_CONTRACT_V0.1.md",
        "schemas/executor_registry.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Review-Only Registry Rule",
    )
    compare_required(
        "docs/runtime-contracts/EXECUTOR_READINESS_REVIEW_CONTRACT_V0.1.md",
        "schemas/executor_readiness_review.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Readiness Categories",
    )
    compare_required(
        "docs/runtime-contracts/DISABLED_RESTRICTED_EXECUTION_SERVICE_INTERFACE_V0.1.md",
        "schemas/disabled_executor_evidence.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Disabled Interface Behavior",
    )
    compare_required(
        "docs/runtime-contracts/HOT_PATH_PRE_EXECUTION_REVALIDATION_CONTRACT_V0.1.md",
        "schemas/pre_execution_revalidation.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Mandatory Hot-Path Checks",
    )
    compare_required(
        "docs/runtime-contracts/APPROVAL_CONSUMPTION_CONTRACT_V0.1.md",
        "schemas/approval_consumption_preview.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Atomic Future Design",
    )
    compare_required(
        "docs/runtime-contracts/ROLLBACK_RECOVERY_CONTRACT_V0.1.md",
        "schemas/rollback_recovery_plan.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Requirements",
    )

    compare_required(
        "docs/runtime-contracts/MONITORING_SNAPSHOT_CONTRACT_V0.1.md",
        "schemas/monitoring_snapshot.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Review-Only Boundary",
    )
    compare_required(
        "docs/runtime-contracts/ALERT_EVENT_CONTRACT_V0.1.md",
        "schemas/alert_event.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Review-Only Boundary",
    )
    compare_required(
        "docs/runtime-contracts/HEALTH_SNAPSHOT_CONTRACT_V0.1.md",
        "schemas/health_snapshot.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Review-Only Boundary",
    )
    compare_required(
        "docs/runtime-contracts/CLOCK_SYNC_EVIDENCE_CONTRACT_V0.1.md",
        "schemas/clock_sync_evidence.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Review-Only Boundary",
    )
    compare_required(
        "docs/runtime-contracts/KILL_SWITCH_STATE_CONTRACT_V0.1.md",
        "schemas/kill_switch_state.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Review-Only Boundary",
    )
    compare_required(
        "docs/runtime-contracts/KILL_SWITCH_COMMAND_REVIEW_CONTRACT_V0.1.md",
        "schemas/kill_switch_command_review.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Review-Only Boundary",
    )
    compare_required(
        "docs/runtime-contracts/EXECUTOR_CANDIDATE_INTAKE_CONTRACT_V0.1.md",
        "schemas/executor_candidate_intake.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Review-Only Boundary",
    )
    compare_required(
        "docs/runtime-contracts/EXECUTOR_CANDIDATE_INTAKE_REVIEW_CONTRACT_V0.1.md",
        "schemas/executor_candidate_intake_review.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Review-Only Boundary",
    )

    compare_required(
        "docs/runtime-contracts/CONTROL_CHANNEL_IDENTITY_BINDING_CONTRACT_V0.1.md",
        "schemas/control_channel_identity_binding.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Review-Only Boundary",
    )
    compare_required(
        "docs/runtime-contracts/CONTROL_CHANNEL_COMMAND_ENVELOPE_REVIEW_CONTRACT_V0.1.md",
        "schemas/control_channel_command_envelope_review.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Review-Only Boundary",
    )
    compare_required(
        "docs/runtime-contracts/DISABLED_PROCESS_SUPERVISOR_INTERFACE_V0.1.md",
        "schemas/process_supervisor_snapshot.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Review-Only Boundary",
    )
    compare_required(
        "docs/runtime-contracts/DISABLED_SCHEDULER_INTERFACE_V0.1.md",
        "schemas/scheduler_plan_review.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Review-Only Boundary",
    )
    compare_required(
        "docs/runtime-contracts/MONITORING_ALERT_THRESHOLD_POLICY_V0.1.md",
        "schemas/monitoring_alert_threshold_policy.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Review-Only Boundary",
    )
    compare_required(
        "docs/runtime-contracts/MONITORING_ALERT_THRESHOLD_EVALUATION_CONTRACT_V0.1.md",
        "schemas/monitoring_alert_threshold_evaluation.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Review-Only Boundary",
    )
    compare_required(
        "docs/runtime-contracts/LOCAL_REVERSIBLE_SANDBOX_CANDIDATE_TEST_PLAN_V0.1.md",
        "schemas/local_reversible_sandbox_candidate_test_plan.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Review-Only Boundary",
    )
    compare_required(
        "docs/runtime-contracts/LOCAL_REVERSIBLE_SANDBOX_CANDIDATE_TEST_REVIEW_CONTRACT_V0.1.md",
        "schemas/local_reversible_sandbox_candidate_test_review.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Review-Only Boundary",
    )

    compare_required(
        "docs/runtime-contracts/I0_4_RUNTIME_CONTRACT_SET_INDEX_V0.1.md",
        "schemas/i0_4_runtime_contract_set_index.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Review-Only Boundary",
    )

    compare_required(
        "docs/runtime-contracts/READ_ONLY_RUNTIME_INPUT_BUNDLE_CONTRACT_V0.1.md",
        "schemas/read_only_runtime_input_bundle.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Read-only Boundary",
    )

    compare_required(
        "docs/runtime-contracts/READ_ONLY_RUNTIME_RUN_CONTRACT_V0.1.md",
        "schemas/read_only_runtime_run.v0.1.schema.json",
        "## 2. Required Fields",
        "## 3. Result Modes",
    )

    task_text = (
        ROOT
        / "docs/runtime-contracts/TASK_CONTRACT_V0.3.md"
    ).read_text(encoding="utf-8")
    task_schema = load_schema(
        "schemas/task.v0.3.schema.json"
    )

    for field in task_schema.get(
        "required",
        [],
    ):
        if field not in task_text:
            ERRORS.append(
                "TASK_CONTRACT_V0.3.md missing "
                f"top-level Schema field marker: {field}"
            )

    binding_text = (
        ROOT
        / "docs/runtime-contracts/"
        "CORE_CONTEXT_BINDING_V0.3.md"
    ).read_text(encoding="utf-8")
    binding_schema = load_schema(
        "schemas/core_context_binding.v0.3.schema.json"
    )

    for field in binding_schema.get(
        "required",
        [],
    ):
        if field not in binding_text:
            ERRORS.append(
                "CORE_CONTEXT_BINDING_V0.3.md "
                f"missing Schema field marker: {field}"
            )

    role_template = (
        ROOT
        / "03_ROLE_CONTRACTS/"
        "ROLE_DEFINITION_TEMPLATE.yaml"
    ).read_text(encoding="utf-8")

    for token in [
        "task_contract: task.v0.3",
        "task_contract_minimum: task.v0.3",
        "core_context_binding_required: true",
    ]:
        if token not in role_template:
            ERRORS.append(
                "ROLE_DEFINITION_TEMPLATE.yaml "
                f"missing: {token}"
            )

    if ERRORS:
        print(
            "FAIL: contract/schema parity validation "
            "found errors"
        )

        for item in ERRORS:
            print(f" - {item}")

        return 1

    print(
        "PASS: contract/schema parity validation "
        "completed"
    )
    print(
        "Checked Task, Core Binding, Role Assignment, Agent Output, "
        "Permission Decision, Action Approval, Tool Request, Program Request, "
        "Execution Request, Execution Result, Validation Result, Audit Event, "
        "and Role Definition contract parity"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
