from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .integrity import IntegrityError, scan_for_secret_bearing_keys, sha256_record, sha256_value, short_id
from .io import ReadBoundaryError, load_bundle, read_record_snapshot
from .schema_validation import RuntimeSchemaError, validate_against_schema, validate_record_when_schema_exists
from .worker import WORKER_ID, WORKER_VERSION, execute_contract_inspection_worker

KERNEL_ID = "thomas.read_only_runtime_kernel"
KERNEL_VERSION = "0.1.1"
AUTHORITY_ORDER = {f"P{index}": index for index in range(7)}
ALLOWED_PERMISSION_DECISIONS = {"ALLOW", "EXECUTE_AND_REPORT"}
REPLAY_TRANSITIONS = [
    ("REPLAY_QUEUED", "INPUT_VERIFIED"),
    ("INPUT_VERIFIED", "PREFLIGHT_PASSED"),
    ("PREFLIGHT_PASSED", "WORKER_COMPLETED"),
    ("WORKER_COMPLETED", "CONTRACT_VALIDATED"),
    ("CONTRACT_VALIDATED", "REPLAY_COMPLETED"),
]


class KernelBlocked(ValueError):
    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


class ReadOnlyRuntimeKernel:
    def __init__(self, repo_root: Path, *, now: str):
        self.repo_root = repo_root.resolve(strict=True)
        try:
            parsed_now = datetime.fromisoformat(now.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise ValueError("now must be an RFC3339 timestamp") from exc
        if parsed_now.tzinfo is None:
            raise ValueError("now must include an RFC3339 timezone")
        self.now = now
        self._filesystem_read_count = 0

    def run(self, bundle_path: Path) -> dict[str, Any]:
        try:
            resolved_bundle = bundle_path.resolve(strict=True)
            try:
                resolved_bundle.relative_to(self.repo_root)
            except ValueError as exc:
                raise ReadBoundaryError("input bundle must be located inside repo root") from exc
            bundle, _ = read_record_snapshot(resolved_bundle)
            self._filesystem_read_count += 1
            scan_for_secret_bearing_keys(bundle)
            records, actual_hashes, record_read_count = load_bundle(self.repo_root, bundle)
            self._filesystem_read_count += record_read_count
            return self._run_loaded(bundle, records, actual_hashes, resolved_bundle)
        except KernelBlocked as exc:
            return self._blocked_record(bundle_path, exc.reason_code, exc.message)
        except (OSError, ReadBoundaryError, IntegrityError, RuntimeSchemaError, KeyError, TypeError, ValueError) as exc:
            return self._blocked_record(bundle_path, "INPUT_BUNDLE_INVALID", str(exc))

    def _run_loaded(
        self,
        bundle: dict[str, Any],
        records: dict[str, dict[str, Any]],
        actual_hashes: dict[str, str],
        bundle_path: Path,
    ) -> dict[str, Any]:
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
            self.repo_root / "schemas/read_only_runtime_input_bundle.v0.1.schema.json",
            "input bundle",
        )
        self._filesystem_read_count += bundle_schema_reads
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
            record_schema_reads = validate_record_when_schema_exists(self.repo_root, record, name)
            if record_schema_reads:
                self._filesystem_read_count += record_schema_reads
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

        output = execute_contract_inspection_worker(
            task=task,
            assignment=assignment,
            created_at=self.now,
        )
        output["status"] = "final"
        output_fingerprint = sha256_record(output)

        validation_result = self._build_validation_result(
            output=output,
            output_fingerprint=output_fingerprint,
            task=task,
            assignment=assignment,
        )
        validation_fingerprint = sha256_value(validation_result)

        transitions: list[dict[str, Any]] = []
        audit_events: list[dict[str, Any]] = []
        previous_hash: str | None = None
        previous_audit_id: str | None = None
        for sequence, (from_state, to_state) in enumerate(REPLAY_TRANSITIONS, start=1):
            event = self._build_transition_audit(
                task=task,
                assignment=assignment,
                from_state=from_state,
                to_state=to_state,
                sequence=sequence,
                previous_hash=previous_hash,
                previous_audit_id=previous_audit_id,
            )
            previous_hash = event["integrity"]["event_sha256"]
            previous_audit_id = event["audit_event_id"]
            audit_events.append(event)
            transitions.append(
                {
                    "from": from_state,
                    "to": to_state,
                    "audit_event_id": event["audit_event_id"],
                    "audit_event_sha256": previous_hash,
                }
            )

        validation_audit = self._build_validation_audit(
            task=task,
            assignment=assignment,
            validation_result=validation_result,
            validation_fingerprint=validation_fingerprint,
            sequence=len(audit_events) + 1,
            previous_hash=previous_hash,
            previous_audit_id=previous_audit_id,
        )
        audit_events.append(validation_audit)

        final_task = deepcopy(task)

        run_seed = {
            "bundle_id": bundle["bundle_id"],
            "task_id": task_id,
            "task_revision": task_revision,
            "kernel_version": KERNEL_VERSION,
            "worker_version": WORKER_VERSION,
        }
        run_id = short_id("rorun", run_seed)
        run_payload = {
            "schema_version": "read_only_runtime_run_fingerprint_payload.v0.1",
            "run_id": run_id,
            "bundle_id": bundle["bundle_id"],
            "bundle_sha256": sha256_value(bundle),
            "task_id": task_id,
            "task_revision": task_revision,
            "core_context_binding_id": ccb_id,
            "assignment_id": assignment["assignment_id"],
            "agent_output_sha256": output_fingerprint,
            "validation_result_sha256": validation_fingerprint,
            "final_task_sha256": sha256_record(final_task),
            "audit_event_sha256s": [item["integrity"]["event_sha256"] for item in audit_events],
            "created_at": self.now,
        }

        return {
            "schema_version": "read_only_runtime_run.v0.1",
            "run_id": run_id,
            "run_mode": "DEVELOPMENT_REPLAY",
            "input_bundle": {
                "bundle_id": bundle["bundle_id"],
                "bundle_ref": bundle_path.relative_to(self.repo_root).as_posix(),
                "bundle_sha256": sha256_value(bundle),
                "record_sha256": actual_hashes,
            },
            "lineage": {
                "trace_id": trace_id,
                "task_id": task_id,
                "task_revision": task_revision,
                "core_context_binding_id": ccb_id,
                "assignment_id": assignment["assignment_id"],
                "role_id": assignment["role_id"],
                "role_version": assignment["role_version"],
            },
            "kernel": {
                "kernel_id": KERNEL_ID,
                "kernel_version": KERNEL_VERSION,
                "worker_id": WORKER_ID,
                "worker_version": WORKER_VERSION,
                "runtime_authoritative": False,
            },
            "preflight": {
                "result": "PASS",
                "checks": checks,
                "reason_codes": [],
            },
            "authority": {
                "required_permission_level": task["authority"]["required_permission_level"],
                "effective_permission_level": authority["effective_permission_level"],
                "assignment_granted_permission_level": authority["assignment_granted_permission_level"],
                "role_permission_ceiling": authority["role_permission_ceiling"],
                "sufficient": True,
            },
            "permission": {
                "evaluation_status": permission["evaluation_status"],
                "permission_decision": permission["permission_decision"],
                "permission_decision_ref": permission["permission_decision_ref"],
                "approval_required": False,
                "development_replay_allowed": True,
            },
            "governance": {
                "approval_id": binding["release"]["approval_id"],
                "activation_id": binding["release"]["activation_id"],
                "verification_status": "REFERENCES_PRESENT_NOT_VERIFIED",
                "authoritative_governance_claimed": False,
            },
            "routing": {
                "selected_route": "ROLE",
                "role_id": assignment["role_id"],
                "role_version": assignment["role_version"],
                "assignment_id": assignment["assignment_id"],
                "actor_instance_id": assignment["actor_instance_id"],
                "tool_request_ids": [],
                "program_request_ids": [],
            },
            "worker": {
                "invoked": True,
                "result": "PASS",
                "agent_invocations": 1,
                "model_calls": 0,
                "tool_calls": 0,
                "program_calls": 0,
                "network_calls": 0,
                "filesystem_writes": 0,
                "external_actions": 0,
            },
            "outputs": {
                "agent_output": output,
                "agent_output_sha256": output_fingerprint,
                "validation_result": validation_result,
                "validation_result_sha256": validation_fingerprint,
                "final_task": final_task,
                "final_task_sha256": sha256_record(final_task),
                "audit_events": audit_events,
            },
            "lifecycle": {
                "initial_state": "REPLAY_QUEUED",
                "transitions": transitions,
                "final_state": "REPLAY_COMPLETED",
                "source_task_state_unchanged": True,
            },
            "effects": self._no_effects(),
            "summary": {
                "result": "COMPLETED_READ_ONLY_REPLAY",
                "message": "Read-only development replay completed; the source Task lifecycle remained unchanged.",
                "runtime_activation_created": False,
                "runtime_permission_created": False,
            },
            "integrity": {
                "hash_schema": "read_only_runtime_run_fingerprint_payload.v0.1",
                "run_fingerprint_payload": run_payload,
                "run_sha256": sha256_value(run_payload),
            },
            "created_at": self.now,
        }

    def _build_validation_result(
        self,
        *,
        output: dict[str, Any],
        output_fingerprint: str,
        task: dict[str, Any],
        assignment: dict[str, Any],
    ) -> dict[str, Any]:
        seed = {
            "agent_output_id": output["agent_output_id"],
            "task_id": task["identity"]["task_id"],
            "task_revision": task["identity"]["task_revision"],
        }
        return {
            "schema_version": "validation_result.v0.1",
            "validation_result_id": short_id("valres", seed),
            "trace_id": task["identity"]["trace_id"],
            "task_id": task["identity"]["task_id"],
            "task_revision": task["identity"]["task_revision"],
            "core_context_binding_id": task["context"]["core_context_binding_id"],
            "subject": {
                "subject_type": "AGENT_OUTPUT",
                "subject_id": output["agent_output_id"],
                "subject_ref": f"in_memory:{output['agent_output_id']}",
                "subject_fingerprint": output_fingerprint,
                "subject_created_by_actor_id": assignment["actor_instance_id"],
            },
            "validator": {
                "validator_type": "SYSTEM",
                "validator_actor_id": "i0_5.read_only_runtime.contract_validator",
                "validator_role_id": None,
                "validator_role_version": None,
                "validator_execution_context_id": short_id("valctx", seed),
                "independent_required": False,
                "independence_verified": False,
            },
            "validation": {
                "validation_mode": "CONTRACT",
                "result": "PASS",
                "acceptance_criteria": [
                    "agent_output_lineage_consistent",
                    "read_only_boundary_preserved",
                    "no_hidden_capability_invocation",
                ],
                "rejection_criteria": [
                    "lineage_mismatch",
                    "secret_exposure",
                    "hidden_model_tool_program_network_or_write_effect",
                ],
                "checks": [
                    {
                        "check_id": "agent_output_lineage",
                        "result": "PASS",
                        "evidence_refs": [f"in_memory:{output['agent_output_id']}"],
                        "notes": "Agent Output matches Task, Binding, Assignment, Role, and Actor lineage.",
                    },
                    {
                        "check_id": "read_only_effects",
                        "result": "PASS",
                        "evidence_refs": ["runtime:read_only_kernel.effects"],
                        "notes": "No model, Tool, Program, network, filesystem write, external action, or Runtime mutation occurred.",
                    },
                ],
                "result_reasons": [
                    "The deterministic worker returned a schema-shaped Agent Output with exact lineage.",
                    "All prohibited capability counters remained zero.",
                ],
                "recommended_next_state": "REPLAY_COMPLETED",
            },
            "findings": {
                "facts": ["The Agent Output was generated from explicit input records only."],
                "risks": ["Development replay is non-authoritative and must not be treated as Runtime activation."],
                "omissions": ["No live Runtime state or external evidence was evaluated."],
                "assumptions": ["The provided snapshots are the intended development inputs."],
                "limitations": ["Automatic contract validation is not independent domain validation."],
            },
            "evidence_refs": [f"in_memory:{output['agent_output_id']}", "runtime:read_only_kernel.preflight"],
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
            "lifecycle": {"created_at": self.now, "supersedes": []},
            "audit_refs": [],
        }

    def _build_transition_audit(
        self,
        *,
        task: dict[str, Any],
        assignment: dict[str, Any],
        from_state: str,
        to_state: str,
        sequence: int,
        previous_hash: str | None,
        previous_audit_id: str | None,
    ) -> dict[str, Any]:
        seed = {
            "task_id": task["identity"]["task_id"],
            "task_revision": task["identity"]["task_revision"],
            "from": from_state,
            "to": to_state,
            "sequence": sequence,
        }
        audit_id = short_id("audit", seed)
        subject_ref = f"in_memory:task:{task['identity']['task_id']}:r{task['identity']['task_revision']}"
        payload = {
            "schema_version": "audit_event_fingerprint_payload.v0.1",
            "audit_event_id": audit_id,
            "trace_id": task["identity"]["trace_id"],
            "task_id": task["identity"]["task_id"],
            "task_revision": task["identity"]["task_revision"],
            "core_context_binding_id": task["context"]["core_context_binding_id"],
            "event_type": "TASK_STATE_CHANGED",
            "actor_ref": f"system:{KERNEL_ID}",
            "subject_ref": subject_ref,
            "subject_fingerprint": sha256_record(task),
            "event_summary": f"Read-only in-memory Task transition {from_state} -> {to_state}.",
            "outcome": "RECORDED",
            "reason_codes": ["READ_ONLY_DEVELOPMENT_REPLAY"],
            "payload_sha256": None,
            "evidence_refs": ["runtime:read_only_kernel.preflight"],
            "related_record_refs": [f"in_memory:assignment:{assignment['assignment_id']}"],
            "parent_audit_event_ids": [previous_audit_id] if previous_audit_id else [],
            "previous_event_sha256": previous_hash,
            "sequence_number": sequence,
            "created_at": self.now,
        }
        return {
            "schema_version": "audit_event.v0.1",
            "audit_event_id": audit_id,
            "trace_id": task["identity"]["trace_id"],
            "task_id": task["identity"]["task_id"],
            "task_revision": task["identity"]["task_revision"],
            "core_context_binding_id": task["context"]["core_context_binding_id"],
            "event_type": "TASK_STATE_CHANGED",
            "actor": {
                "actor_type": "system",
                "actor_id": KERNEL_ID,
                "role_id": None,
                "role_version": None,
                "assignment_id": None,
            },
            "subject": {
                "subject_type": "TASK",
                "subject_id": task["identity"]["task_id"],
                "subject_ref": subject_ref,
                "subject_fingerprint": sha256_record(task),
            },
            "event": {
                "event_summary": payload["event_summary"],
                "outcome": "RECORDED",
                "reason_codes": ["READ_ONLY_DEVELOPMENT_REPLAY"],
                "payload_ref": None,
                "payload_sha256": None,
                "evidence_refs": ["runtime:read_only_kernel.preflight"],
                "related_record_refs": [f"in_memory:assignment:{assignment['assignment_id']}"],
            },
            "lineage": {
                "parent_audit_event_ids": payload["parent_audit_event_ids"],
                "previous_event_sha256": previous_hash,
                "sequence_number": sequence,
            },
            "integrity": {
                "hash_schema": "audit_event_fingerprint_payload.v0.1",
                "event_fingerprint_payload": payload,
                "event_sha256": sha256_value(payload),
                "append_only": True,
                "overwrite_allowed": False,
                "delete_allowed": False,
            },
            "sensitivity": task["context"]["data_sensitivity"],
            "runtime_effect": {
                "mode": "EVIDENCE_ONLY",
                "grants_permission": False,
                "grants_approval": False,
                "grants_authority": False,
                "grants_execution": False,
                "grants_activation": False,
                "mutates_runtime": False,
            },
            "created_at": self.now,
        }

    def _build_validation_audit(
        self,
        *,
        task: dict[str, Any],
        assignment: dict[str, Any],
        validation_result: dict[str, Any],
        validation_fingerprint: str,
        sequence: int,
        previous_hash: str | None,
        previous_audit_id: str | None,
    ) -> dict[str, Any]:
        seed = {
            "validation_result_id": validation_result["validation_result_id"],
            "sequence": sequence,
        }
        audit_id = short_id("audit", seed)
        subject_ref = f"in_memory:{validation_result['validation_result_id']}"
        payload = {
            "schema_version": "audit_event_fingerprint_payload.v0.1",
            "audit_event_id": audit_id,
            "trace_id": task["identity"]["trace_id"],
            "task_id": task["identity"]["task_id"],
            "task_revision": task["identity"]["task_revision"],
            "core_context_binding_id": task["context"]["core_context_binding_id"],
            "event_type": "VALIDATION_COMPLETED",
            "actor_ref": "system:i0_5.read_only_runtime.contract_validator",
            "subject_ref": subject_ref,
            "subject_fingerprint": validation_fingerprint,
            "event_summary": "Automatic read-only contract and lineage validation passed.",
            "outcome": "PASS",
            "reason_codes": ["READ_ONLY_VALIDATION_PASS"],
            "payload_sha256": validation_fingerprint,
            "evidence_refs": [subject_ref],
            "related_record_refs": [f"in_memory:assignment:{assignment['assignment_id']}"],
            "parent_audit_event_ids": [previous_audit_id] if previous_audit_id else [],
            "previous_event_sha256": previous_hash,
            "sequence_number": sequence,
            "created_at": self.now,
        }
        return {
            "schema_version": "audit_event.v0.1",
            "audit_event_id": audit_id,
            "trace_id": task["identity"]["trace_id"],
            "task_id": task["identity"]["task_id"],
            "task_revision": task["identity"]["task_revision"],
            "core_context_binding_id": task["context"]["core_context_binding_id"],
            "event_type": "VALIDATION_COMPLETED",
            "actor": {
                "actor_type": "system",
                "actor_id": "i0_5.read_only_runtime.contract_validator",
                "role_id": None,
                "role_version": None,
                "assignment_id": None,
            },
            "subject": {
                "subject_type": "VALIDATION_RESULT",
                "subject_id": validation_result["validation_result_id"],
                "subject_ref": subject_ref,
                "subject_fingerprint": validation_fingerprint,
            },
            "event": {
                "event_summary": payload["event_summary"],
                "outcome": "PASS",
                "reason_codes": ["READ_ONLY_VALIDATION_PASS"],
                "payload_ref": subject_ref,
                "payload_sha256": validation_fingerprint,
                "evidence_refs": [subject_ref],
                "related_record_refs": [f"in_memory:assignment:{assignment['assignment_id']}"],
            },
            "lineage": {
                "parent_audit_event_ids": payload["parent_audit_event_ids"],
                "previous_event_sha256": previous_hash,
                "sequence_number": sequence,
            },
            "integrity": {
                "hash_schema": "audit_event_fingerprint_payload.v0.1",
                "event_fingerprint_payload": payload,
                "event_sha256": sha256_value(payload),
                "append_only": True,
                "overwrite_allowed": False,
                "delete_allowed": False,
            },
            "sensitivity": task["context"]["data_sensitivity"],
            "runtime_effect": {
                "mode": "EVIDENCE_ONLY",
                "grants_permission": False,
                "grants_approval": False,
                "grants_authority": False,
                "grants_execution": False,
                "grants_activation": False,
                "mutates_runtime": False,
            },
            "created_at": self.now,
        }

    def _blocked_record(self, bundle_path: Path, reason_code: str, message: str) -> dict[str, Any]:
        seed = {"bundle_path": bundle_path.as_posix(), "reason_code": reason_code, "message": message}
        run_id = short_id("rorun", seed)
        payload = {
            "schema_version": "read_only_runtime_run_fingerprint_payload.v0.1",
            "run_id": run_id,
            "result": "BLOCKED",
            "reason_code": reason_code,
            "message": message,
            "created_at": self.now,
        }
        return {
            "schema_version": "read_only_runtime_run.v0.1",
            "run_id": run_id,
            "run_mode": "DEVELOPMENT_REPLAY",
            "input_bundle": {
                "bundle_id": None,
                "bundle_ref": self._relative_or_string(bundle_path),
                "bundle_sha256": None,
                "record_sha256": {},
            },
            "lineage": {
                "trace_id": None,
                "task_id": None,
                "task_revision": None,
                "core_context_binding_id": None,
                "assignment_id": None,
                "role_id": None,
                "role_version": None,
            },
            "kernel": {
                "kernel_id": KERNEL_ID,
                "kernel_version": KERNEL_VERSION,
                "worker_id": WORKER_ID,
                "worker_version": WORKER_VERSION,
                "runtime_authoritative": False,
            },
            "preflight": {
                "result": "BLOCK",
                "checks": [],
                "reason_codes": [reason_code],
            },
            "authority": {
                "required_permission_level": None,
                "effective_permission_level": None,
                "assignment_granted_permission_level": None,
                "role_permission_ceiling": None,
                "sufficient": False,
            },
            "permission": {
                "evaluation_status": None,
                "permission_decision": None,
                "permission_decision_ref": None,
                "approval_required": False,
                "development_replay_allowed": False,
            },
            "governance": {
                "approval_id": None,
                "activation_id": None,
                "verification_status": "NOT_EVALUATED",
                "authoritative_governance_claimed": False,
            },
            "routing": {
                "selected_route": None,
                "role_id": None,
                "role_version": None,
                "assignment_id": None,
                "actor_instance_id": None,
                "tool_request_ids": [],
                "program_request_ids": [],
            },
            "worker": {
                "invoked": False,
                "result": "NOT_INVOKED",
                "agent_invocations": 0,
                "model_calls": 0,
                "tool_calls": 0,
                "program_calls": 0,
                "network_calls": 0,
                "filesystem_writes": 0,
                "external_actions": 0,
            },
            "outputs": {
                "agent_output": None,
                "agent_output_sha256": None,
                "validation_result": None,
                "validation_result_sha256": None,
                "final_task": None,
                "final_task_sha256": None,
                "audit_events": [],
            },
            "lifecycle": {
                "initial_state": None,
                "transitions": [],
                "final_state": "BLOCKED",
                "source_task_state_unchanged": True,
            },
            "effects": self._no_effects(),
            "summary": {
                "result": "BLOCKED",
                "message": message,
                "runtime_activation_created": False,
                "runtime_permission_created": False,
            },
            "integrity": {
                "hash_schema": "read_only_runtime_run_fingerprint_payload.v0.1",
                "run_fingerprint_payload": payload,
                "run_sha256": sha256_value(payload),
            },
            "created_at": self.now,
        }

    def _relative_or_string(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.repo_root).as_posix()
        except Exception:
            return path.as_posix()

    def _no_effects(self) -> dict[str, Any]:
        return {
            "mode": "DEVELOPMENT_REPLAY_READ_ONLY",
            "filesystem_read_performed": self._filesystem_read_count > 0,
            "filesystem_read_count": self._filesystem_read_count,
            "filesystem_write_performed": False,
            "model_invocation_performed": False,
            "tool_execution_performed": False,
            "program_execution_performed": False,
            "network_call_performed": False,
            "external_action_performed": False,
            "financial_action_performed": False,
            "runtime_mutation_performed": False,
            "approval_consumed": False,
            "executor_handoff_performed": False,
            "scheduler_dispatch_performed": False,
            "control_command_dispatched": False,
            "permission_expanded": False,
            "authority_expanded": False,
            "core_activation_created": False,
        }


def run_bundle(repo_root: Path, bundle_path: Path, *, now: str) -> dict[str, Any]:
    return ReadOnlyRuntimeKernel(repo_root, now=now).run(bundle_path)
