#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from lib.action_fingerprint import (
    FingerprintPayloadError,
    compute_action_fingerprint,
    normalize_fingerprint_payload,
)


ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []
LEVEL_RANK = {f"P{index}": index for index in range(7)}
DECISION_RANK = {
    "ALLOW": 0,
    "EXECUTE_AND_REPORT": 1,
    "APPROVAL_REQUIRED": 2,
    "BLOCK": 3,
}
POLICY_REL = "governance/GOVERNANCE_POLICY.yaml"
POLICY_BINDING = {
    "policy_id": "thomas.governance.policy",
    "policy_version": "1.1.0",
    "policy_ref": POLICY_REL,
}
LEGACY_POLICY_BINDING = {
    "policy_id": "thomas.permission_approval.operating_policy",
    "policy_version": "0.1.0",
    "policy_ref": "docs/runtime-contracts/THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1.yaml",
}
RUNTIME_FALSE_FIELDS = (
    "executor_handoff_allowed",
    "external_execution_allowed",
    "financial_execution_allowed",
    "runtime_mutation_allowed",
    "tool_enablement_allowed",
    "program_enablement_allowed",
    "permission_expansion_allowed",
)
POLICY_RUNTIME_FALSE_FIELDS = (
    "grants_runtime_execution",
    "grants_tool_or_program_enablement",
    "grants_external_execution",
    "grants_financial_execution",
    "grants_permission_expansion",
    *RUNTIME_FALSE_FIELDS,
    "approval_consumption_allowed",
    "core_activation_allowed",
)


def error(message: str) -> None:
    ERRORS.append(message)


def load_yaml(rel_or_path: str | Path) -> dict[str, Any]:
    path = Path(rel_or_path)
    if not path.is_absolute():
        path = ROOT / path
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"__load_error__": f"{path}: YAML parse failed: {exc}"}
    if not isinstance(data, dict):
        return {"__load_error__": f"{path}: expected YAML mapping"}
    return data


def load_json(rel: str) -> dict[str, Any]:
    path = ROOT / rel
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"__load_error__": f"{rel}: JSON parse failed: {exc}"}
    if not isinstance(data, dict):
        return {"__load_error__": f"{rel}: expected JSON object"}
    return data


def validator_for(rel: str) -> Draft202012Validator:
    schema = load_json(rel)
    if "__load_error__" in schema:
        raise RuntimeError(schema["__load_error__"])
    return Draft202012Validator(schema, format_checker=FormatChecker())


def schema_issues(
    validator: Draft202012Validator,
    data: dict[str, Any],
) -> list[str]:
    if "__load_error__" in data:
        return [str(data["__load_error__"])]
    issues: list[str] = []
    for issue in sorted(validator.iter_errors(data), key=lambda item: list(item.path)):
        path = ".".join(str(part) for part in issue.path) or "<root>"
        issues.append(f"{path}: {issue.message}")
    return issues


def parse_dt(value: Any, label: str, issues: list[str]) -> datetime | None:
    if not isinstance(value, str):
        issues.append(f"{label}: expected date-time string")
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        issues.append(f"{label}: invalid date-time")
        return None


def validate_policy_binding(
    record: dict[str, Any],
    issues: list[str],
) -> None:
    if record.get("operating_policy") != POLICY_BINDING:
        issues.append(
            "operating_policy must bind canonical thomas.governance.policy v1.1.0"
        )


def scope_policy_map(policy: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for decision, scopes in policy.get("policy_dispositions", {}).items():
        if decision not in DECISION_RANK:
            raise ValueError(f"unknown permission disposition: {decision}")
        if not isinstance(scopes, list):
            raise ValueError(f"policy_dispositions.{decision} must be a list")
        for scope in scopes:
            if not isinstance(scope, str) or not scope:
                raise ValueError(f"policy_dispositions.{decision} contains invalid scope")
            if scope in mapping:
                raise ValueError(f"permission scope appears more than once: {scope}")
            mapping[scope] = decision
    return mapping


def validate_runtime_effect(record: dict[str, Any], issues: list[str]) -> None:
    effect = record.get("runtime_effect", {})
    if effect.get("mode") != "REVIEW_ONLY":
        issues.append("runtime_effect.mode must be REVIEW_ONLY")
    for field in RUNTIME_FALSE_FIELDS:
        if effect.get(field) is not False:
            issues.append(f"runtime_effect.{field} must remain false")


def validate_policy_record(policy: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if "__load_error__" in policy:
        return [str(policy["__load_error__"])]

    expected_identity = {
        "schema_version": "thomas_governance_policy.v1",
        "policy_id": "thomas.governance.policy",
        "policy_version": "1.1.0",
        "status": "ACTIVE_POLICY_SOURCE",
        "owner": "Thomas",
        "authoritative": True,
        "operating_model": "BOUNDED_MAXIMUM_AUTONOMY",
    }
    for field, expected in expected_identity.items():
        if policy.get(field) != expected:
            issues.append(f"{field} must equal {expected!r}")

    if policy.get("decision_order") != [
        "ALLOW",
        "EXECUTE_AND_REPORT",
        "APPROVAL_REQUIRED",
        "BLOCK",
    ]:
        issues.append("decision_order must run from ALLOW to BLOCK")

    try:
        mapping = scope_policy_map(policy)
    except ValueError as exc:
        issues.append(str(exc))
        mapping = {}
    if not mapping:
        issues.append("canonical Governance Policy must define permission scopes")

    required_scopes = {
        "INTERNAL_ANALYSIS": "ALLOW",
        "GIT_AGENT_BRANCH_CHANGE": "EXECUTE_AND_REPORT",
        "RUNTIME_GOVERNANCE": "APPROVAL_REQUIRED",
        "AUTHORITY_ESCALATION": "BLOCK",
        "SELF_APPROVAL": "BLOCK",
        "APPROVAL_REUSE": "BLOCK",
        "SECRET_EXFILTRATION": "BLOCK",
        "KILL_SWITCH_BYPASS": "BLOCK",
        "UNREGISTERED_RESOURCE_EXECUTION": "BLOCK",
        "DISABLED_RESOURCE_EXECUTION": "BLOCK",
    }
    for scope, expected in required_scopes.items():
        if mapping.get(scope) != expected:
            issues.append(f"{scope} must map to {expected}")

    source = policy.get("source_of_truth", {})
    if not source.get("active_for"):
        issues.append("source_of_truth.active_for must declare owned Governance domains")
    if source.get("generated_or_reference_artifacts_authoritative") is not False:
        issues.append("generated/reference artifacts must remain non-authoritative")

    cutover = policy.get("cutover", {})
    if cutover.get("previous_policy_sources_replaced") is not True:
        issues.append("PR #9 cutover must replace previous policy rule ownership")
    for field in (
        "grants_runtime_execution",
        "grants_tool_or_program_enablement",
        "grants_external_or_financial_execution",
        "grants_approval_consumption",
        "grants_executor_handoff",
        "grants_core_activation",
    ):
        if cutover.get(field) is not False:
            issues.append(f"cutover.{field} must remain false")

    authority = policy.get("authority", {})
    if authority.get("approval_cannot_expand_authority") is not True:
        issues.append("canonical policy must prohibit Approval-based Authority expansion")
    if authority.get("insufficient_authority_result") != "BLOCK":
        issues.append("insufficient Authority must result in BLOCK")
    if authority.get("self_approval_allowed") is not False:
        issues.append("self approval must remain disabled")
    if authority.get("runtime_self_activation_allowed") is not False:
        issues.append("Runtime self activation must remain disabled")
    if set(authority.get("levels", {})) != set(LEVEL_RANK):
        issues.append("Authority level map must contain exactly P0 through P6")

    action_identity = policy.get("action_identity", {})
    if action_identity.get("payload_schema") != "action_fingerprint_payload.v0.1":
        issues.append("action identity payload schema mismatch")
    if action_identity.get("algorithm") != "SHA-256":
        issues.append("action identity algorithm must be SHA-256")
    if action_identity.get("secrets_forbidden") is not True:
        issues.append("Secrets must remain forbidden in action fingerprints")
    if action_identity.get("float_values_allowed", False) is True:
        issues.append("float values must remain forbidden in action fingerprints")

    control = policy.get("control_channel", {})
    if control.get("primary_channel") != "TELEGRAM_PRIVATE_1_TO_1":
        issues.append("primary Control Channel must be Telegram private 1:1")
    if control.get("required_approver") != "Thomas":
        issues.append("Control Channel approver must be Thomas")
    if control.get("allowed_identity_verification_methods") != [
        "telegram_private_control_channel"
    ]:
        issues.append("Control Channel verification method mismatch")

    lifetime = policy.get("approval_lifetime", {})
    if lifetime.get("one_time_use_required") is not True:
        issues.append("Action Approval must remain one-time-use")
    if lifetime.get("approval_reuse_allowed") is not False:
        issues.append("Approval reuse must remain disabled")
    if lifetime.get("approval_consumption_implemented") is not False:
        issues.append("real Approval consumption must remain unimplemented")
    if lifetime.get("default_approval_ttl_minutes") != 30:
        issues.append("default Approval TTL must remain 30 minutes")
    if lifetime.get("permission_decision_max_ttl_minutes") != 60:
        issues.append("Permission Decision maximum TTL must remain 60 minutes")

    if policy.get("financial", {}).get("financial_executor_enabled") is not False:
        issues.append("financial executor must remain disabled")
    if policy.get("memory_learning", {}).get("automatic_runtime_promotion_allowed") is not False:
        issues.append("automatic Runtime promotion must remain disabled")
    validation = policy.get("validation", {})
    for field in (
        "validation_grants_permission",
        "validation_grants_approval",
        "validation_grants_authority",
    ):
        if validation.get(field) is not False:
            issues.append(f"validation.{field} must remain false")
    if policy.get("kill_switch", {}).get("agent_can_disable_or_bypass") is not False:
        issues.append("Agents must not disable or bypass the Kill Switch")
    if policy.get("conflict_policy", {}).get("stricter_rule_wins") is not True:
        issues.append("stricter Governance rule must win")
    if policy.get("conflict_policy", {}).get("fail_closed_on_uncertainty") is not True:
        issues.append("Governance uncertainty must fail closed")

    effect = policy.get("runtime_effect", {})
    if effect.get("mode") != "REVIEW_ONLY":
        issues.append("canonical policy runtime_effect.mode must remain REVIEW_ONLY")
    for field in POLICY_RUNTIME_FALSE_FIELDS:
        if effect.get(field) is not False:
            issues.append(f"canonical policy runtime_effect.{field} must remain false")
    return issues


def validate_permission_record(
    data: dict[str, Any],
    policy: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    if "__load_error__" in data:
        return [str(data["__load_error__"])]

    validate_policy_binding(data, issues)
    payload = data.get("fingerprint_payload", {})
    try:
        normalized = normalize_fingerprint_payload(payload)
        expected_fingerprint = compute_action_fingerprint(normalized)
    except FingerprintPayloadError as exc:
        issues.append(str(exc))
        expected_fingerprint = None

    if expected_fingerprint and data.get("action_fingerprint") != expected_fingerprint:
        issues.append("action_fingerprint does not match canonical fingerprint_payload")

    for field in ("task_id", "task_revision", "core_context_binding_id"):
        if payload.get(field) != data.get(field):
            issues.append(f"fingerprint_payload.{field} must match top-level {field}")

    requested_by = data.get("requested_by", {})
    expected_requester_ref = f"{requested_by.get('actor_type')}:{requested_by.get('actor_id')}"
    if payload.get("requester_ref") != expected_requester_ref:
        issues.append("fingerprint_payload.requester_ref must equal actor_type:actor_id")

    authority = data.get("authority", {})
    try:
        required = LEVEL_RANK[authority["required_permission_level"]]
        effective = LEVEL_RANK[authority["effective_permission_level"]]
        granted = LEVEL_RANK[authority["assignment_granted_permission_level"]]
        ceiling = LEVEL_RANK[authority["role_permission_ceiling"]]
        calculated = required <= effective <= granted <= ceiling
    except Exception:
        issues.append("authority levels are incomplete or invalid")
        calculated = False

    if authority.get("authority_sufficient") is not calculated:
        issues.append("authority_sufficient does not match the authority chain")

    decision = data.get("decision", {}).get("permission_decision")
    approval = data.get("approval", {})
    try:
        minimum_by_scope = scope_policy_map(policy)
    except ValueError as exc:
        issues.append(str(exc))
        minimum_by_scope = {}

    permission_scope = payload.get("permission_scope")
    minimum_decision = minimum_by_scope.get(permission_scope)
    if minimum_decision is None:
        issues.append("permission_scope is not registered in the canonical Governance Policy")
    elif decision in DECISION_RANK:
        if DECISION_RANK[decision] < DECISION_RANK[minimum_decision]:
            issues.append(
                f"{permission_scope} requires at least {minimum_decision}; "
                f"{decision} is less restrictive"
            )
        if data.get("risk", {}).get("policy_disposition") != minimum_decision:
            issues.append(
                "risk.policy_disposition must match the canonical Governance Policy "
                f"for {permission_scope}"
            )
    else:
        issues.append("permission_decision is unknown")

    if not calculated and decision != "BLOCK":
        issues.append("insufficient Authority must produce BLOCK")

    if decision == "APPROVAL_REQUIRED":
        if approval.get("approval_required") is not True:
            issues.append("APPROVAL_REQUIRED must set approval_required=true")
        if not isinstance(approval.get("approval_id"), str):
            issues.append("APPROVAL_REQUIRED must bind an approval_id")
        if approval.get("approval_status") != "PENDING":
            issues.append("APPROVAL_REQUIRED must start with PENDING approval")
    else:
        if approval != {
            "approval_required": False,
            "approval_id": None,
            "approval_status": "NOT_REQUIRED",
        }:
            issues.append("non-APPROVAL_REQUIRED decisions must not carry approval state")

    amount = payload.get("amount_decimal")
    currency = payload.get("currency")
    if (amount is None) != (currency is None):
        issues.append("amount_decimal and currency must be paired")

    created = parse_dt(data.get("lifecycle", {}).get("created_at"), "lifecycle.created_at", issues)
    expires = parse_dt(data.get("lifecycle", {}).get("expires_at"), "lifecycle.expires_at", issues)
    payload_expires = parse_dt(payload.get("expires_at"), "fingerprint_payload.expires_at", issues)
    if created and expires and created >= expires:
        issues.append("permission decision must expire after creation")
    if created and expires:
        max_minutes = policy.get("approval_lifetime", {}).get(
            "permission_decision_max_ttl_minutes"
        )
        if isinstance(max_minutes, int):
            duration_minutes = (expires - created).total_seconds() / 60
            if duration_minutes > max_minutes:
                issues.append("permission decision TTL exceeds the canonical maximum")
    if expires and payload_expires and expires != payload_expires:
        issues.append("fingerprint_payload.expires_at must match lifecycle.expires_at")

    validate_runtime_effect(data, issues)
    return issues


def validate_approval_record(
    data: dict[str, Any],
    permission_cache: dict[str, dict[str, Any]] | None = None,
    policy: dict[str, Any] | None = None,
) -> list[str]:
    issues: list[str] = []
    if "__load_error__" in data:
        return [str(data["__load_error__"])]
    validate_policy_binding(data, issues)
    policy = policy or {}

    snapshot = data.get("approved_action_snapshot", {})
    try:
        expected_fingerprint = compute_action_fingerprint(snapshot)
    except FingerprintPayloadError as exc:
        issues.append(str(exc))
        expected_fingerprint = None
    if expected_fingerprint and data.get("action_fingerprint") != expected_fingerprint:
        issues.append("action_fingerprint does not match approved_action_snapshot")

    for field in ("task_id", "task_revision", "core_context_binding_id"):
        if snapshot.get(field) != data.get(field):
            issues.append(f"approved_action_snapshot.{field} must match top-level {field}")

    issued = parse_dt(data.get("validity", {}).get("issued_at"), "validity.issued_at", issues)
    expires = parse_dt(data.get("validity", {}).get("expires_at"), "validity.expires_at", issues)
    if issued and expires and issued >= expires:
        issues.append("approval must expire after issuance")

    permission_scope = snapshot.get("permission_scope")
    scope_ttls = policy.get("approval_lifetime", {}).get("scope_max_ttl_minutes", {})
    default_ttl = policy.get("approval_lifetime", {}).get("default_approval_ttl_minutes")
    max_ttl = scope_ttls.get(permission_scope, default_ttl)
    if issued and expires and isinstance(max_ttl, int):
        duration_minutes = (expires - issued).total_seconds() / 60
        if duration_minutes > max_ttl:
            issues.append(f"approval TTL exceeds policy maximum for {permission_scope}")

    try:
        minimum_decision = scope_policy_map(policy).get(permission_scope)
    except ValueError as exc:
        issues.append(str(exc))
        minimum_decision = None
    if minimum_decision != "APPROVAL_REQUIRED":
        issues.append("Action Approval may exist only for an APPROVAL_REQUIRED policy scope")

    status = data.get("status")
    approver = data.get("approver", {})
    decision = data.get("decision", {})
    consumption = data.get("consumption", {})

    if status == "PENDING":
        if approver.get("approved_by") is not None:
            issues.append("PENDING approval cannot have approved_by")
        if approver.get("verification_status") != "NOT_VERIFIED":
            issues.append("PENDING approval must be NOT_VERIFIED")
        if decision.get("decided_at") is not None:
            issues.append("PENDING approval cannot have decided_at")
    elif status in {"APPROVED", "REJECTED", "REVOKED", "CONSUMPTION_PREVIEWED"}:
        if approver.get("approved_by") != "Thomas":
            issues.append(f"{status} approval must be decided by Thomas")
        if approver.get("verification_status") != "VERIFIED":
            issues.append(f"{status} approval requires verified identity")
        allowed_methods = policy.get("control_channel", {}).get(
            "allowed_identity_verification_methods", []
        )
        if approver.get("identity_verification_method") not in allowed_methods:
            issues.append(
                f"{status} approval must use the approved Telegram private Control Channel"
            )
        verification_ref = approver.get("verification_ref")
        if not isinstance(verification_ref, str) or not verification_ref.startswith(
            "telegram:private_chat:"
        ):
            issues.append(
                f"{status} approval requires a Telegram private-chat verification_ref"
            )
        decided = parse_dt(decision.get("decided_at"), "decision.decided_at", issues)
        if decided and expires and decided > expires:
            issues.append("approval decision occurred after expiration")

    if status == "CONSUMPTION_PREVIEWED":
        if consumption.get("consumption_status") != "PREVIEWED_ONLY":
            issues.append(
                "CONSUMPTION_PREVIEWED requires PREVIEWED_ONLY consumption status"
            )
        if not consumption.get("previewed_at") or not consumption.get("preview_ref"):
            issues.append("consumption preview requires timestamp and reference")
    else:
        if consumption.get("consumption_status") != "NOT_CONSUMED":
            issues.append("non-preview status must remain NOT_CONSUMED")
        if consumption.get("previewed_at") is not None:
            issues.append("non-preview status cannot have previewed_at")
        if consumption.get("preview_ref") is not None:
            issues.append("non-preview status cannot have preview_ref")

    if consumption.get("one_time_use") is not True:
        issues.append("Action Approval must remain one-time-use")
    if data.get("approval_scope") != "REVIEW_ONLY":
        issues.append("approval_scope must remain REVIEW_ONLY")
    validate_runtime_effect(data, issues)

    permission_ref = data.get("permission_decision_ref")
    if isinstance(permission_ref, str):
        if permission_cache is not None and permission_ref in permission_cache:
            permission = permission_cache[permission_ref]
        else:
            permission = load_yaml(permission_ref)
        if "__load_error__" in permission:
            issues.append(str(permission["__load_error__"]))
        else:
            if permission.get("decision", {}).get("permission_decision") != "APPROVAL_REQUIRED":
                issues.append(
                    "Approval may reference only an APPROVAL_REQUIRED Permission Decision"
                )
            pairs = (
                ("permission_decision_id", "permission_decision_id"),
                ("trace_id", "trace_id"),
                ("task_id", "task_id"),
                ("task_revision", "task_revision"),
                ("core_context_binding_id", "core_context_binding_id"),
                ("action_fingerprint", "action_fingerprint"),
            )
            for approval_field, permission_field in pairs:
                if data.get(approval_field) != permission.get(permission_field):
                    issues.append(
                        f"{approval_field} does not match referenced Permission Decision"
                    )
            if snapshot != permission.get("fingerprint_payload"):
                issues.append(
                    "approved_action_snapshot differs from referenced fingerprint_payload"
                )
            if data.get("operating_policy") != permission.get("operating_policy"):
                issues.append(
                    "operating_policy does not match referenced Permission Decision"
                )
    return issues


def require_doc_tokens(rel: str, tokens: list[str]) -> None:
    text = (ROOT / rel).read_text(encoding="utf-8")
    for token in tokens:
        if token not in text:
            error(f"{rel}: missing required token: {token}")


def main() -> int:
    policy = load_yaml(POLICY_REL)
    policy_issues = validate_policy_record(policy)
    if policy_issues:
        error(f"{POLICY_REL}: expected valid, got {policy_issues}")

    permission_validator = validator_for("schemas/permission_decision.v0.3.schema.json")
    approval_validator = validator_for("schemas/approval.v0.1.schema.json")

    permission_positive = [
        "examples/permission/permission_allow_v0.3.yaml",
        "examples/permission/permission_approval_required_v0.3.yaml",
        "examples/permission/permission_block_authority_insufficient_v0.3.yaml",
    ]
    approval_positive = [
        "examples/approval/approval_pending_v0.1.yaml",
        "examples/approval/approval_approved_review_only_v0.1.yaml",
        "examples/approval/approval_consumption_preview_v0.1.yaml",
    ]

    permission_cache: dict[str, dict[str, Any]] = {}
    for rel in permission_positive:
        data = load_yaml(rel)
        permission_cache[rel] = data
        issues = schema_issues(permission_validator, data)
        issues.extend(validate_permission_record(data, policy))
        if issues:
            error(f"{rel}: expected valid, got {issues}")

    for rel in approval_positive:
        data = load_yaml(rel)
        issues = schema_issues(approval_validator, data)
        issues.extend(validate_approval_record(data, permission_cache, policy))
        if issues:
            error(f"{rel}: expected valid, got {issues}")

    negative_cases = [
        ("permission", "tests/fixtures/permission/invalid_authority_insufficient_allow.yaml"),
        ("permission", "tests/fixtures/permission/invalid_fingerprint_mismatch.yaml"),
        ("permission", "tests/fixtures/permission/invalid_approval_required_without_id.yaml"),
        ("permission", "tests/fixtures/permission/invalid_runtime_effect_enabled.yaml"),
        ("permission", "tests/fixtures/permission/invalid_changed_target_reuse.yaml"),
        ("permission", "tests/fixtures/permission/invalid_changed_content_reuse.yaml"),
        ("permission", "tests/fixtures/permission/invalid_changed_amount_reuse.yaml"),
        ("permission", "tests/fixtures/permission/invalid_task_revision_reuse.yaml"),
        ("permission", "tests/fixtures/permission/invalid_binding_reuse.yaml"),
        ("permission", "tests/fixtures/permission/invalid_policy_binding_mismatch.yaml"),
        ("permission", "tests/fixtures/permission/invalid_policy_scope_underclassified.yaml"),
        ("approval", "tests/fixtures/approval/invalid_unverified_approved.yaml"),
        ("approval", "tests/fixtures/approval/invalid_decision_after_expiry.yaml"),
        ("approval", "tests/fixtures/approval/invalid_permission_ref_mismatch.yaml"),
        ("approval", "tests/fixtures/approval/invalid_consumption_preview_without_ref.yaml"),
        ("approval", "tests/fixtures/approval/invalid_runtime_handoff_enabled.yaml"),
        ("approval", "tests/fixtures/approval/invalid_approval_ttl_exceeds_policy.yaml"),
        ("approval", "tests/fixtures/approval/invalid_approval_channel_group.yaml"),
    ]

    for kind, rel in negative_cases:
        data = load_yaml(rel)
        if kind == "permission":
            issues = schema_issues(permission_validator, data)
            issues.extend(validate_permission_record(data, policy))
        else:
            issues = schema_issues(approval_validator, data)
            issues.extend(validate_approval_record(data, permission_cache, policy))
        if not issues:
            error(f"{rel}: negative fixture unexpectedly passed")

    require_doc_tokens(
        "docs/runtime-contracts/PERMISSION_DECISION_CONTRACT_V0.3.md",
        [
            "permission_decision.v0.3",
            "Authority does not equal Permission",
            "Approval cannot expand Authority",
            "REVIEW_ONLY",
            "action_fingerprint",
            "APPROVAL_REQUIRED",
            "BLOCK",
            "governance/GOVERNANCE_POLICY.yaml",
        ],
    )
    require_doc_tokens(
        "docs/runtime-contracts/APPROVAL_CONTRACT_V0.1.md",
        [
            "approval.v0.1",
            "Runtime-Authoritative Core Approval",
            "one_time_use",
            "CONSUMPTION_PREVIEWED",
            "REVIEW_ONLY",
            "governance/GOVERNANCE_POLICY.yaml",
        ],
    )
    require_doc_tokens(
        "docs/runtime-contracts/ACTION_FINGERPRINT_POLICY_V0.1.md",
        [
            "action_fingerprint_payload.v0.1",
            "RFC 8785",
            "SHA-256",
            "Secret",
            "Task revision",
            "governance/GOVERNANCE_POLICY.yaml",
        ],
    )
    require_doc_tokens(
        "docs/runtime-contracts/THOMAS_PERMISSION_APPROVAL_OPERATING_PRINCIPLES_V0.1.md",
        [
            "HUMAN_READABLE_REFERENCE",
            "BOUNDED_MAXIMUM_AUTONOMY",
            "Approval cannot expand Authority",
            "Thomas Telegram private 1:1",
            "Autonomous financial spend without a registered Budget remains `0`",
            "Protected Branch force push",
            "Ten independent valid repetitions trigger Programization Review only",
            "governance/GOVERNANCE_POLICY.yaml",
        ],
    )
    require_doc_tokens(
        "docs/runtime-contracts/THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1.yaml",
        [
            "SUPERSEDED_BY_CANONICAL_GOVERNANCE_POLICY",
            "authoritative: false",
            "runtime_use_allowed: false",
            "policy_id: thomas.governance.policy",
            "policy_ref: governance/GOVERNANCE_POLICY.yaml",
            "rules_embedded: false",
        ],
    )
    require_doc_tokens(
        POLICY_REL,
        [
            "thomas_governance_policy.v1",
            "policy_id: thomas.governance.policy",
            "policy_version: 1.1.0",
            "status: ACTIVE_POLICY_SOURCE",
            "authoritative: true",
            "one_time_use_required: true",
            "financial_executor_enabled: false",
            "approval_consumption_allowed: false",
            "core_activation_allowed: false",
        ],
    )

    if ERRORS:
        print("FAIL: canonical Governance / Permission / Approval validation found errors")
        for item in ERRORS:
            print(f" - {item}")
        return 1

    print("PASS: canonical Governance Policy and Permission/Approval records validated")
    print(
        "Validated one active policy authority, 6 positive records, 18 negative fixtures, "
        "canonical action fingerprints, Authority/Permission separation, action-bound Approval, "
        "cross-record lineage, historical schema compatibility, and Review-only execution guards"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
