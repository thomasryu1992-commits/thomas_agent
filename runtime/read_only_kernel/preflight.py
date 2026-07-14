from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .constants import ALLOWED_PERMISSION_DECISIONS, AUTHORITY_ORDER
from .errors import KernelBlocked
from .integrity import scan_for_secret_bearing_keys, sha256_value
from .schema_validation import validate_against_schema, validate_record_when_schema_exists
from .types import PreflightContext, ReadCounter


def run_preflight(
    *,
    repo_root: Path,
    bundle: dict[str, Any],
    records: dict[str, dict[str, Any]],
    read_counter: ReadCounter,
) -> PreflightContext:
    checks: list[dict[str, Any]] = []

    def check(check_id: str, condition: bool, reason_code: str, notes: str) -> None:
        checks.append({"check_id": check_id, "result": "PASS" if condition else "BLOCK", "notes": notes})
        if not condition:
            raise KernelBlocked(reason_code, notes)

    check(
        "bundle_schema",
        bundle.get("schema_version") == "read_only_runtime_input_bundle.v0.1",
        "BUNDLE_SCHEMA_INVALID",
        "Input bundle schema_version must be read_only_runtime_input_bundle.v0.1.",
    )
    check(
        "run_mode",
        bundle.get("run_mode") == "DEVELOPMENT_REPLAY",
        "RUNTIME_AUTHORITATIVE_MODE_DISABLED",
        "I0.5 v0.1 permits DEVELOPMENT_REPLAY only.",
    )
    integrity = bundle.get("integrity", {})
    expected_payload = {
        "schema_version": "read_only_runtime_input_bundle_fingerprint_payload.v0.1",
        "bundle_id": bundle.get("bundle_id"),
        "run_mode": bundle.get("run_mode"),
        "refs": bundle.get("refs"),
        "sha256": bundle.get("sha256"),
        "constraints": bundle.get("constraints"),
        "created_at": bundle.get("created_at"),
    }
    check(
        "bundle_integrity",
        integrity.get("hash_schema") == "read_only_runtime_input_bundle_fingerprint_payload.v0.1"
        and integrity.get("bundle_fingerprint_payload") == expected_payload
        and integrity.get("bundle_sha256") == sha256_value(expected_payload),
        "BUNDLE_FINGERPRINT_MISMATCH",
        "Input bundle fingerprint payload and SHA-256 must match the exact refs, hashes, constraints, and timestamp.",
    )
    constraints = bundle.get("constraints", {})
    required_false = [
        "external_network_allowed",
        "tool_execution_allowed",
        "program_execution_allowed",
        "model_invocation_allowed",
        "external_action_allowed",
        "runtime_mutation_allowed",
        "filesystem_write_allowed",
        "secrets_allowed",
    ]
    check(
        "bundle_read_only_constraints",
        constraints.get("filesystem_read_only") is True
        and all(constraints.get(name) is False for name in required_false),
        "READ_ONLY_BOUNDARY_INVALID",
        "Input bundle must preserve all I0.5 read-only and no-effect constraints.",
    )
    bundle_schema_reads = validate_against_schema(
        bundle,
        repo_root / "schemas/read_only_runtime_input_bundle.v0.1.schema.json",
        "input bundle",
    )
    read_counter.add(bundle_schema_reads)
    checks.append(
        {
            "check_id": "input_bundle_schema_validation",
            "result": "PASS",
            "notes": "Input Bundle passed the repository Draft 2020-12 schema.",
        }
    )

    required_records = {
        "task",
        "core_context_binding",
        "role_assignment",
        "role_definition",
        "role_registry",
        "tool_registry",
        "program_registry",
        "i0_4_contract_set_index",
    }
    check(
        "required_records",
        required_records.issubset(records),
        "REQUIRED_RECORD_MISSING",
        "Input bundle must include every required Task, Core, Role, Registry, and I0.4 index record.",
    )
    for record in records.values():
        scan_for_secret_bearing_keys(record)
    schema_validated_records = []
    schema_version_only_records = []
    for name, record in records.items():
        record_schema_reads = validate_record_when_schema_exists(repo_root, record, name)
        if record_schema_reads:
            read_counter.add(record_schema_reads)
            schema_validated_records.append(name)
        else:
            schema_version_only_records.append(name)
    checks.append(
        {
            "check_id": "runtime_record_schema_validation",
            "result": "PASS",
            "notes": (
                f"Validated available schemas for {sorted(schema_validated_records)}; "
                f"semantic fail-closed checks cover records without repository schemas: "
                f"{sorted(schema_version_only_records)}."
            ),
        }
    )

    task = deepcopy(records["task"])
    binding = records["core_context_binding"]
    assignment = records["role_assignment"]
    role = records["role_definition"]
    role_registry = records["role_registry"]
    tool_registry = records["tool_registry"]
    program_registry = records["program_registry"]
    contract_index = records["i0_4_contract_set_index"]

    identity = task["identity"]
    task_id = identity["task_id"]
    task_revision = identity["task_revision"]
    trace_id = identity["trace_id"]
    ccb_id = task["context"]["core_context_binding_id"]

    check(
        "task_schema",
        task.get("schema_version") == "task.v0.3",
        "TASK_SCHEMA_INVALID",
        "Task schema_version must be task.v0.3.",
    )
    check(
        "task_authenticated",
        task.get("source", {}).get("requester", {}).get("authenticated") is True,
        "REQUESTER_NOT_AUTHENTICATED",
        "The development replay Task requester must be marked authenticated in the explicit input snapshot.",
    )
    check(
        "task_lifecycle_queued",
        task.get("lifecycle", {}).get("status") == "QUEUED",
        "TASK_NOT_QUEUED",
        "Read-only kernel accepts a QUEUED Task snapshot only.",
    )
    check(
        "task_no_external_action",
        "no_external_action" in task.get("scope", {}).get("constraints", []),
        "TASK_EXTERNAL_ACTION_BOUNDARY_MISSING",
        "Task scope must explicitly include no_external_action.",
    )
    check(
        "task_no_tool_or_program_requests",
        task.get("routing", {}).get("tool_request_ids") == []
        and task.get("routing", {}).get("program_request_ids") == [],
        "RESOURCE_REQUEST_PRESENT",
        "I0.5 v0.1 does not execute or consume Tool or Program Requests.",
    )
    check(
        "task_role_route",
        task.get("routing", {}).get("selected_route") == "ROLE",
        "ROUTE_NOT_SUPPORTED",
        "I0.5 v0.1 supports one explicit ROLE route only.",
    )
    check(
        "task_single_assignment",
        len(task["routing"].get("role_assignment_ids", [])) == 1
        and len(task["routing"].get("assigned_role_ids", [])) == 1
        and len(task["routing"].get("assigned_actor_ids", [])) == 1,
        "MULTI_ACTOR_ROUTE_NOT_SUPPORTED",
        "I0.5 v0.1 supports exactly one Role Assignment and one Actor.",
    )

    check(
        "binding_schema",
        binding.get("schema_version") == "core_context_binding.v0.3",
        "CORE_BINDING_SCHEMA_INVALID",
        "Core Context Binding schema_version must be core_context_binding.v0.3.",
    )
    check(
        "binding_lineage",
        binding.get("identity", {}).get("task_id") == task_id
        and binding.get("identity", {}).get("task_revision") == task_revision
        and binding.get("identity", {}).get("trace_id") == trace_id
        and binding.get("identity", {}).get("core_context_binding_id") == ccb_id,
        "CORE_BINDING_LINEAGE_MISMATCH",
        "Task and Core Context Binding lineage must match exactly.",
    )
    loaded_rules = set(binding.get("rules", {}).get("loaded_rule_ids", []))
    task_rules = set(task.get("context", {}).get("active_core_rule_ids", []))
    check(
        "binding_rule_subset",
        bool(task_rules) and task_rules.issubset(loaded_rules),
        "CORE_RULE_SCOPE_MISMATCH",
        "Task active_core_rule_ids must be a non-empty subset of the bound Core rules.",
    )
    check(
        "binding_has_release_governance_references",
        bool(binding.get("release", {}).get("approval_id"))
        and bool(binding.get("release", {}).get("activation_id")),
        "CORE_RELEASE_GOVERNANCE_REFERENCE_MISSING",
        "Development replay requires approval_id and activation_id references; presence does not verify the referenced governance records.",
    )

    check(
        "assignment_schema",
        assignment.get("schema_version") == "role_assignment.v0.2",
        "ASSIGNMENT_SCHEMA_INVALID",
        "Role Assignment schema_version must be role_assignment.v0.2.",
    )
    check(
        "assignment_lineage",
        assignment.get("task_id") == task_id
        and assignment.get("trace_id") == trace_id
        and assignment.get("core_context_binding_id") == ccb_id
        and assignment.get("assignment_id") == task["routing"]["role_assignment_ids"][0],
        "ASSIGNMENT_LINEAGE_MISMATCH",
        "Task and Role Assignment lineage must match exactly.",
    )
    check(
        "assignment_route_identity",
        assignment.get("role_id") == task["routing"]["assigned_role_ids"][0]
        and assignment.get("actor_instance_id") == task["routing"]["assigned_actor_ids"][0],
        "ASSIGNMENT_ROUTE_MISMATCH",
        "Task route and Role Assignment identity must match exactly.",
    )
    check(
        "assignment_resource_boundary",
        assignment.get("allowed_tool_ids") == [] and assignment.get("allowed_program_ids") == [],
        "ASSIGNMENT_RESOURCE_SCOPE_NOT_EMPTY",
        "I0.5 v0.1 requires empty Tool and Program allowlists in the exact Assignment.",
    )
    limits = assignment.get("execution_budget", {}).get("limits", {})
    check(
        "assignment_zero_external_budgets",
        limits.get("max_tool_calls") == 0
        and limits.get("max_program_calls") == 0
        and limits.get("max_model_calls") == 0,
        "ASSIGNMENT_EXTERNAL_BUDGET_NOT_ZERO",
        "I0.5 v0.1 Assignment budgets must allow zero Tool, Program, and model calls.",
    )

    check(
        "role_schema",
        role.get("schema_version") == "role_definition.v0.2",
        "ROLE_SCHEMA_INVALID",
        "Role Definition schema_version must be role_definition.v0.2.",
    )
    check(
        "role_identity",
        role.get("role_id") == assignment.get("role_id")
        and role.get("role_version") == assignment.get("role_version"),
        "ROLE_IDENTITY_MISMATCH",
        "Role Definition and Assignment role identity must match.",
    )
    check(
        "role_active_routable",
        role.get("status") == "active" and role.get("routable") is True,
        "ROLE_NOT_ACTIVE_ROUTABLE",
        "Read-only development replay requires an active, routable Role snapshot.",
    )
    check(
        "role_no_external_action",
        role.get("external_action_allowed") is False,
        "ROLE_EXTERNAL_ACTION_ALLOWED",
        "Role Definition must prohibit external action.",
    )
    required_capabilities = set(task["routing"].get("required_capabilities", []))
    assigned_capabilities = set(assignment.get("role_scope", {}).get("assigned_capabilities", []))
    role_capabilities = set(role.get("capabilities", []))
    check(
        "capability_chain",
        bool(required_capabilities)
        and required_capabilities.issubset(assigned_capabilities)
        and assigned_capabilities.issubset(role_capabilities),
        "CAPABILITY_CHAIN_INVALID",
        "Task required capabilities must be within Assignment and Role capability scopes.",
    )

    authority = assignment.get("authority", {})
    try:
        required_level = AUTHORITY_ORDER[task["authority"]["required_permission_level"]]
        assignment_required = AUTHORITY_ORDER[authority["required_permission_level"]]
        effective = AUTHORITY_ORDER[authority["effective_permission_level"]]
        granted = AUTHORITY_ORDER[authority["assignment_granted_permission_level"]]
        ceiling = AUTHORITY_ORDER[authority["role_permission_ceiling"]]
        role_ceiling = AUTHORITY_ORDER[role["permission_ceiling"]]
    except KeyError as exc:
        raise KernelBlocked("AUTHORITY_RECORD_INVALID", f"Unknown or missing Authority field: {exc}") from exc
    check(
        "authority_chain",
        required_level == assignment_required <= effective <= granted <= ceiling == role_ceiling <= AUTHORITY_ORDER["P3"],
        "AUTHORITY_INSUFFICIENT_OR_EXCESSIVE",
        "Authority must satisfy Task == Assignment required <= effective <= granted <= Role ceiling <= P3.",
    )

    permission = task.get("permission", {})
    check(
        "permission_executable",
        permission.get("evaluation_status") == "DECIDED"
        and permission.get("permission_decision") in ALLOWED_PERMISSION_DECISIONS
        and permission.get("approval_state") == "NOT_REQUIRED"
        and permission.get("approval_id") is None,
        "PERMISSION_NOT_EXECUTABLE_READ_ONLY",
        "I0.5 v0.1 permits only DECIDED ALLOW/EXECUTE_AND_REPORT with no Approval requirement.",
    )
    check(
        "assignment_permission_match",
        assignment.get("permission", {}).get("permission_decision") == permission.get("permission_decision")
        and assignment.get("permission", {}).get("permission_decision_ref") == permission.get("permission_decision_ref")
        and assignment.get("permission", {}).get("approval_id") is None,
        "ASSIGNMENT_PERMISSION_MISMATCH",
        "Task and Assignment Permission records must match and require no Approval.",
    )

    role_entries = [
        item
        for item in role_registry.get("active_roles", [])
        if item.get("role_id") == role["role_id"] and item.get("role_version") == role["role_version"]
    ]
    check(
        "role_registry_entry",
        len(role_entries) == 1
        and role_entries[0].get("status") == "active"
        and role_entries[0].get("routable") is True,
        "ROLE_REGISTRY_MISMATCH",
        "Role Registry must contain one matching active and routable Role entry.",
    )
    check(
        "tool_registry_disabled",
        tool_registry.get("status") == "active_registry_no_active_tools"
        and all(item.get("enabled") is False for item in tool_registry.get("tools", [])),
        "TOOL_REGISTRY_NOT_READ_ONLY_SAFE",
        "Tool Registry must contain no enabled Tools for I0.5 v0.1.",
    )
    check(
        "program_registry_disabled",
        program_registry.get("status") == "active_registry_no_active_programs"
        and all(item.get("enabled") is False for item in program_registry.get("programs", [])),
        "PROGRAM_REGISTRY_NOT_READ_ONLY_SAFE",
        "Program Registry must contain no enabled Programs for I0.5 v0.1.",
    )
    check(
        "i0_4_contract_set_frozen",
        contract_index.get("status") == "REVIEW_ONLY_CONSOLIDATED_NOT_RUNTIME_ACTIVE"
        and contract_index.get("contract_set", {}).get("freeze_status")
        == "FROZEN_FOR_I0_5_READ_ONLY_RUNTIME_DESIGN",
        "I0_4_CONTRACT_SET_NOT_FROZEN",
        "I0.4 canonical contract set must be consolidated, non-active, and frozen for I0.5 design.",
    )

    return PreflightContext(
        checks=checks,
        task=task,
        binding=binding,
        assignment=assignment,
        authority=authority,
        permission=permission,
        task_id=task_id,
        task_revision=task_revision,
        trace_id=trace_id,
        core_context_binding_id=ccb_id,
    )
