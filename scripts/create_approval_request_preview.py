#!/usr/bin/env python3
"""Operator tool (STATUS: manual-only, no automated caller).

Builds a schema-valid Approval Request *preview* record for inspection — referenced from
``docs/runtime-contracts/APPROVAL_FLOW_V0.1.md`` as the way to look at the record shape
without going through the live ask path (``approval_cli request``). Nothing in CI, the
gates, or the runtime invokes it; if the live flow's rendering ever fully supersedes it,
it can be retired with just that doc reference.
"""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from validate_permission_approval_contracts import (
    POLICY_REL,
    load_yaml,
    validate_approval_record,
    validate_permission_record,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def as_z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a PENDING review-only Approval Request from an "
            "APPROVAL_REQUIRED Permission Decision."
        )
    )
    parser.add_argument("--permission", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expires-minutes", type=int, default=15)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.expires_minutes < 1:
        raise ValueError("--expires-minutes must be at least 1")

    root = Path(__file__).resolve().parents[1]
    permission_path = Path(args.permission)
    output = Path(args.output)
    if output.exists() and not args.force:
        raise FileExistsError(f"Refusing to overwrite existing file: {output}")

    permission = yaml.safe_load(permission_path.read_text(encoding="utf-8"))
    if not isinstance(permission, dict):
        raise ValueError("Permission input must be a YAML mapping")
    policy = load_yaml(POLICY_REL)
    permission_issues = validate_permission_record(permission, policy)
    if permission_issues:
        raise ValueError("Invalid Permission Decision: " + "; ".join(permission_issues))
    if (
        permission.get("decision", {}).get("permission_decision")
        != "APPROVAL_REQUIRED"
    ):
        raise ValueError("Approval Requests require an APPROVAL_REQUIRED decision")

    approval_id = permission["approval"]["approval_id"]
    if not approval_id:
        approval_id = "approval_" + uuid.uuid4().hex[:24]

    issued = utc_now()
    permission_expiry = datetime.fromisoformat(
        permission["lifecycle"]["expires_at"].replace("Z", "+00:00")
    )
    permission_scope = permission["fingerprint_payload"]["permission_scope"]
    scope_ttls = policy.get("approval_lifetime", {}).get(
        "scope_max_ttl_minutes",
        {},
    )
    default_ttl = policy.get("approval_lifetime", {}).get(
        "default_approval_ttl_minutes",
        30,
    )
    policy_max_minutes = scope_ttls.get(permission_scope, default_ttl)
    if args.expires_minutes > policy_max_minutes:
        raise ValueError(
            f"--expires-minutes exceeds policy maximum for {permission_scope}: "
            f"{policy_max_minutes}"
        )

    requested_expiry = issued + timedelta(minutes=args.expires_minutes)
    expires = min(permission_expiry, requested_expiry)
    if expires <= issued:
        raise ValueError("Permission Decision is already expired")

    permission_ref = permission_path.as_posix()
    try:
        permission_ref = permission_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        pass

    runtime_effect = {
        "mode": "REVIEW_ONLY",
        "executor_handoff_allowed": False,
        "external_execution_allowed": False,
        "financial_execution_allowed": False,
        "runtime_mutation_allowed": False,
        "tool_enablement_allowed": False,
        "program_enablement_allowed": False,
        "permission_expansion_allowed": False,
    }
    approval = {
        "schema_version": "approval.v0.1",
        "approval_id": approval_id,
        "permission_decision_id": permission["permission_decision_id"],
        "permission_decision_ref": permission_ref,
        "trace_id": permission["trace_id"],
        "task_id": permission["task_id"],
        "task_revision": permission["task_revision"],
        "core_context_binding_id": permission["core_context_binding_id"],
        "operating_policy": permission["operating_policy"],
        "action_fingerprint": permission["action_fingerprint"],
        "approved_action_snapshot": permission["fingerprint_payload"],
        "approval_scope": "REVIEW_ONLY",
        "status": "PENDING",
        "approver": {
            "required_approver": "Thomas",
            "approved_by": None,
            "verification_status": "NOT_VERIFIED",
            "identity_verification_method": None,
            "verification_ref": None,
        },
        "decision": {
            "decision_reason": None,
            "decided_at": None,
        },
        "consumption": {
            "one_time_use": True,
            "consumption_status": "NOT_CONSUMED",
            "previewed_at": None,
            "preview_ref": None,
        },
        "validity": {
            "issued_at": as_z(issued),
            "expires_at": as_z(expires),
        },
        "runtime_effect": runtime_effect,
        "audit_refs": [f"audit:approval_request:{approval_id}"],
    }

    schema = json.loads(
        (root / "schemas/approval.v0.1.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    schema_errors = sorted(
        validator.iter_errors(approval), key=lambda item: list(item.path)
    )
    semantic_errors = validate_approval_record(
        approval,
        {permission_ref: permission},
        policy,
    )
    if schema_errors or semantic_errors:
        print("FAIL: Approval Request preview is invalid")
        for issue in schema_errors:
            path = ".".join(str(part) for part in issue.path) or "<root>"
            print(f" - {path}: {issue.message}")
        for issue in semantic_errors:
            print(f" - {issue}")
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        yaml.safe_dump(approval, sort_keys=False, allow_unicode=True, width=110),
        encoding="utf-8",
        newline="\n",
    )
    print(f"PASS: PENDING review-only Approval Request written to {output}")
    print("No approval was granted and no executor handoff is allowed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
