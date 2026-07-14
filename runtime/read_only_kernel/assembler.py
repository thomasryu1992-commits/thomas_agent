from __future__ import annotations

from pathlib import Path
from typing import Any

from .constants import KERNEL_ID, KERNEL_VERSION
from .integrity import sha256_record, sha256_value, short_id
from .types import RouteSelection
from .worker import WORKER_ID, WORKER_VERSION


def build_no_effects(filesystem_read_count: int) -> dict[str, Any]:
    return {
        "mode": "DEVELOPMENT_REPLAY_READ_ONLY",
        "filesystem_read_performed": filesystem_read_count > 0,
        "filesystem_read_count": filesystem_read_count,
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


def relative_or_string(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root).as_posix()
    except Exception:
        return path.as_posix()


def assemble_completed_run(
    *,
    repo_root: Path,
    now: str,
    filesystem_read_count: int,
    bundle: dict[str, Any],
    actual_hashes: dict[str, str],
    bundle_path: Path,
    preflight_checks: list[dict[str, Any]],
    task: dict[str, Any],
    binding: dict[str, Any],
    assignment: dict[str, Any],
    authority: dict[str, Any],
    permission: dict[str, Any],
    route: RouteSelection,
    output: dict[str, Any],
    output_fingerprint: str,
    validation_result: dict[str, Any],
    validation_fingerprint: str,
    final_task: dict[str, Any],
    audit_events: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    run_id: str,
    run_payload: dict[str, Any],
    task_id: str,
    task_revision: int,
    trace_id: str,
    core_context_binding_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": "read_only_runtime_run.v0.1",
        "run_id": run_id,
        "run_mode": "DEVELOPMENT_REPLAY",
        "input_bundle": {
            "bundle_id": bundle["bundle_id"],
            "bundle_ref": bundle_path.relative_to(repo_root).as_posix(),
            "bundle_sha256": sha256_value(bundle),
            "record_sha256": actual_hashes,
        },
        "lineage": {
            "trace_id": trace_id,
            "task_id": task_id,
            "task_revision": task_revision,
            "core_context_binding_id": core_context_binding_id,
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
            "checks": preflight_checks,
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
            "selected_route": route.selected_route,
            "role_id": route.role_id,
            "role_version": route.role_version,
            "assignment_id": route.assignment_id,
            "actor_instance_id": route.actor_instance_id,
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
        "effects": build_no_effects(filesystem_read_count),
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
        "created_at": now,
    }


def build_blocked_run(
    *,
    repo_root: Path,
    now: str,
    filesystem_read_count: int,
    bundle_path: Path,
    reason_code: str,
    message: str,
) -> dict[str, Any]:
    seed = {"bundle_path": bundle_path.as_posix(), "reason_code": reason_code, "message": message}
    run_id = short_id("rorun", seed)
    payload = {
        "schema_version": "read_only_runtime_run_fingerprint_payload.v0.1",
        "run_id": run_id,
        "result": "BLOCKED",
        "reason_code": reason_code,
        "message": message,
        "created_at": now,
    }
    return {
        "schema_version": "read_only_runtime_run.v0.1",
        "run_id": run_id,
        "run_mode": "DEVELOPMENT_REPLAY",
        "input_bundle": {
            "bundle_id": None,
            "bundle_ref": relative_or_string(bundle_path, repo_root),
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
        "effects": build_no_effects(filesystem_read_count),
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
        "created_at": now,
    }
