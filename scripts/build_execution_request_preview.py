#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

from lib.execution_foundation import (
    authority_sufficient,
    budget_within,
    compute_execution_request_fingerprint,
    requester_ref,
)

POLICY = {
    "policy_id": "thomas.permission_approval.operating_policy",
    "policy_version": "0.1.0",
    "policy_ref": "docs/runtime-contracts/THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1.yaml",
}


def load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected YAML mapping")
    return data


def upstream_view(record: dict[str, Any], ref: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    schema = record.get("schema_version")
    if schema == "tool_request.v0.1":
        operation = record["operation"]
        resource = record["resource"]
        upstream = {
            "request_type": "TOOL_REQUEST",
            "request_id": record["tool_request_id"],
            "request_ref": ref,
            "request_fingerprint": record["request_fingerprint"],
            "action_fingerprint": record["permission"]["action_fingerprint"],
            "resource_id": resource["tool_id"],
            "resource_version": resource["tool_version"],
            "requested_operation": operation["operation_type"],
            "permission_scope": operation["permission_scope"],
            "target_ref": operation["target_ref"],
            "data_scope": operation["data_scope"],
        }
        return upstream, record["requested_by"], operation
    if schema == "program_request.v0.1":
        invocation = record["invocation"]
        resource = record["resource"]
        upstream = {
            "request_type": "PROGRAM_REQUEST",
            "request_id": record["program_request_id"],
            "request_ref": ref,
            "request_fingerprint": record["request_fingerprint"],
            "action_fingerprint": record["permission"]["action_fingerprint"],
            "resource_id": resource["program_id"],
            "resource_version": resource["program_version"],
            "requested_operation": invocation["invocation_type"],
            "permission_scope": invocation["permission_scope"],
            "target_ref": invocation["target_ref"],
            "data_scope": invocation["data_scope"],
        }
        return upstream, record["requested_by"], invocation
    if schema == "permission_decision.v0.3":
        payload = record["fingerprint_payload"]
        upstream = {
            "request_type": "ACTION_PERMISSION",
            "request_id": record["permission_decision_id"],
            "request_ref": ref,
            "request_fingerprint": record["action_fingerprint"],
            "action_fingerprint": record["action_fingerprint"],
            "resource_id": payload.get("tool_id") or payload.get("program_id"),
            "resource_version": None,
            "requested_operation": payload["action_type"],
            "permission_scope": payload["permission_scope"],
            "target_ref": payload["target_ref"],
            "data_scope": payload["data_scope"],
        }
        operation = {
            "normalized_parameters": payload["normalized_parameters"],
        }
        return upstream, record["requested_by"], operation
    raise ValueError(f"unsupported upstream schema: {schema}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an I0.4.4 Review-only Execution Request")
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--permission", required=True)
    parser.add_argument("--approval")
    parser.add_argument("--output", required=True)
    parser.add_argument("--execution-request-id", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--expires-at", required=True)
    args = parser.parse_args()

    upstream_path = Path(args.upstream)
    permission_path = Path(args.permission)
    approval_path = Path(args.approval) if args.approval else None
    upstream_record = load(upstream_path)
    permission_record = load(permission_path)
    approval_record = load(approval_path) if approval_path else None

    upstream, requested_by, operation = upstream_view(upstream_record, upstream_path.as_posix())
    task_id = permission_record["task_id"]
    task_revision = permission_record["task_revision"]
    binding_id = permission_record["core_context_binding_id"]

    permission = {
        "permission_decision_ref": permission_path.as_posix(),
        "permission_decision_id": permission_record["permission_decision_id"],
        "permission_decision": permission_record["decision"]["permission_decision"],
        "action_fingerprint": permission_record["action_fingerprint"],
        "binding_verified": permission_record["action_fingerprint"] == upstream["action_fingerprint"],
    }

    approval_required = permission["permission_decision"] == "APPROVAL_REQUIRED"
    approval_status = "NOT_REQUIRED"
    approval_id = None
    approval_ref = None
    approval_binding = not approval_required
    if approval_record:
        approval_id = approval_record["approval_id"]
        approval_ref = approval_path.as_posix()
        approval_status = approval_record["status"]
        approval_binding = (
            approval_record["permission_decision_id"] == permission["permission_decision_id"]
            and approval_record["action_fingerprint"] == permission["action_fingerprint"]
            and approval_record["task_id"] == task_id
            and approval_record["task_revision"] == task_revision
            and approval_record["core_context_binding_id"] == binding_id
        )
    elif approval_required:
        approval_id = permission_record["approval"].get("approval_id")
        approval_status = permission_record["approval"].get("approval_status", "PENDING")

    authority = dict(permission_record["authority"])
    authority_ok = authority_sufficient(authority)
    authority["authority_sufficient"] = authority_ok

    if upstream_record.get("schema_version") in {"tool_request.v0.1", "program_request.v0.1"}:
        source_budget = upstream_record["budget"]
        budget = {
            "assignment_budget_ref": source_budget["assignment_budget_ref"],
            "requested_runtime_seconds": source_budget["requested_runtime_seconds"],
            "remaining_runtime_seconds": source_budget["remaining_runtime_seconds"],
            "requested_cost_decimal": source_budget["requested_cost_decimal"],
            "remaining_cost_decimal": source_budget["remaining_cost_decimal"],
            "cost_currency": source_budget["cost_currency"],
            "within_assignment_budget": source_budget["within_assignment_budget"],
        }
    else:
        budget = {
            "assignment_budget_ref": "policy:review_only:no_execution_budget",
            "requested_runtime_seconds": 0,
            "remaining_runtime_seconds": 0,
            "requested_cost_decimal": "0",
            "remaining_cost_decimal": "0",
            "cost_currency": "USD",
            "within_assignment_budget": True,
        }
    budget["within_assignment_budget"] = budget_within(budget)

    block_reasons = ["no_executor_registry", "no_executor_registered", "no_executor_implementation", "executor_handoff_disabled"]
    if permission["permission_decision"] == "BLOCK":
        block_reasons.append("permission_decision_block")
    if not permission["binding_verified"]:
        block_reasons.append("permission_binding_mismatch")
    if approval_required and not approval_binding:
        block_reasons.append("approval_binding_incomplete")
    if approval_required and approval_status not in {"APPROVED", "CONSUMPTION_PREVIEWED"}:
        block_reasons.append("approval_not_approved")
    if not authority_ok:
        block_reasons.append("authority_insufficient")
    if not budget["within_assignment_budget"]:
        block_reasons.append("budget_insufficient")
    if upstream_record.get("validation", {}).get("review_result") == "BLOCK":
        block_reasons.append("upstream_request_blocked")

    idempotency_key = f"idem_{args.execution_request_id}"
    payload = {
        "schema_version": "execution_request_fingerprint_payload.v0.1",
        "task_id": task_id,
        "task_revision": task_revision,
        "core_context_binding_id": binding_id,
        "requester_ref": requester_ref(requested_by),
        "upstream_request_type": upstream["request_type"],
        "upstream_request_id": upstream["request_id"],
        "upstream_request_ref": upstream["request_ref"],
        "upstream_request_fingerprint": upstream["request_fingerprint"],
        "action_fingerprint": upstream["action_fingerprint"],
        "permission_decision_id": permission["permission_decision_id"],
        "approval_id": approval_id,
        "executor_id": None,
        "target_ref": upstream["target_ref"],
        "data_scope": upstream["data_scope"],
        "normalized_parameters": operation.get("normalized_parameters", {}),
        "idempotency_key": idempotency_key,
        "assignment_budget_ref": budget["assignment_budget_ref"],
        "expires_at": args.expires_at,
    }

    record = {
        "schema_version": "execution_request.v0.1",
        "execution_request_id": args.execution_request_id,
        "trace_id": permission_record["trace_id"],
        "task_id": task_id,
        "task_revision": task_revision,
        "core_context_binding_id": binding_id,
        "operating_policy": POLICY,
        "requested_by": requested_by,
        "upstream": upstream,
        "authority": authority,
        "permission": permission,
        "approval": {
            "approval_required": approval_required,
            "approval_ref": approval_ref,
            "approval_id": approval_id,
            "approval_status": approval_status,
            "binding_verified": approval_binding,
            "approval_consumed": False,
        },
        "execution_plan": {
            "execution_mode": "PREVIEW_ONLY",
            "executor_id": None,
            "executor_version": None,
            "executor_registry_ref": None,
            "executor_registered": False,
            "executor_enabled": False,
            "executor_implementation_available": False,
            "executor_handoff_allowed": False,
            "target_ref": upstream["target_ref"],
            "data_scope": upstream["data_scope"],
            "normalized_parameters": operation.get("normalized_parameters", {}),
            "expected_effects": ["No effect. Review-only execution plan evidence."],
            "rollback_plan": None,
            "preconditions": [
                "valid_upstream_request",
                "authority_sufficient",
                "permission_binding_valid",
                "approval_binding_valid_when_required",
                "budget_available",
                "registered_enabled_executor_with_implementation",
                "hot_path_revalidation",
            ],
            "expected_output_contract": "execution_result.v0.1",
        },
        "idempotency": {
            "idempotency_key": idempotency_key,
            "one_time_use": True,
            "duplicate_execution_allowed": False,
        },
        "budget": budget,
        "validation": {
            "lineage_complete": True,
            "upstream_binding_match": True,
            "permission_binding_match": permission["binding_verified"],
            "approval_binding_match": approval_binding,
            "authority_match": authority_ok,
            "budget_within_limit": budget["within_assignment_budget"],
            "executor_ready": False,
            "request_review_result": "BLOCK",
            "block_reasons": sorted(set(block_reasons)),
        },
        "request_fingerprint_payload": payload,
        "request_fingerprint": compute_execution_request_fingerprint(payload),
        "runtime_effect": {
            "mode": "REVIEW_ONLY",
            "request_can_execute": False,
            "executor_handoff_allowed": False,
            "executor_call_allowed": False,
            "tool_execution_allowed": False,
            "program_execution_allowed": False,
            "external_execution_allowed": False,
            "financial_execution_allowed": False,
            "runtime_mutation_allowed": False,
            "side_effects_allowed": False,
            "permission_expansion_allowed": False,
        },
        "lifecycle": {
            "review_status": "BLOCKED",
            "created_at": args.created_at,
            "expires_at": args.expires_at,
            "supersedes": [],
        },
        "audit_refs": [f"audit:execution_request:{args.execution_request_id}"],
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(record, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8", newline="\n")
    print(f"WROTE: {output}")
    print("REVIEW_ONLY: no Executor handoff or execution occurred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
