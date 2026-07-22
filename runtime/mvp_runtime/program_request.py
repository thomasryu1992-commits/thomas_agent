"""Program Request creation — invocation evidence, never invocation (explicit Thomas
decision 2026-07-22).

The Program Request contract (``program_request.v0.1``, an ACTIVE record contract) records
one exact requested Program invocation with its registry snapshot, permission binding,
budget evidence, and fail-closed validation verdict. Creating a request is ALLOW-tier
(`tool_or_program_request_creation: ALLOW`); the record **executes nothing** — its
``runtime_effect`` block is schema-pinned REVIEW_ONLY/false throughout.

The honest current shape (contract §7): the registry contains no active Programs, so every
buildable request is **fail-closed BLOCK evidence** — registry-ineligible, allowlist-empty,
budget-zero — for a Program Thomas may later register/activate (each APPROVAL_REQUIRED,
neither reachable from here). The request anchors to a REAL originating task: the pattern's
last valid observation carries the task identity the repetitions came from (the
promotion-audit lineage precedent), and the refused invocation binds a real BLOCK
PermissionDecision built by the one permission authority
(``build_resource_refusal_permission_decision``).

Chain of custody: TRIGGERED pattern -> UNDER_REVIEW -> DRAFT candidate -> shadow PASS ->
ACCEPTED -> **program request** (here) -> registration/activation (separate Thomas
approvals). A request therefore requires an ACCEPTED candidate — the review milestone —
and one request exists per candidate.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

import yaml

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.integrity import IntegrityError
from runtime.registry_resolution import (
    RegistryResolutionError,
    load_resource_definitions,
    resolve_resource_registry,
)

from .errors import ProgramizationBlocked
from .events import stamped_event
from .paths import repo_root as _repo_root
from .permission import MVP_TTL_MINUTES, POLICY_BINDING, build_resource_refusal_permission_decision
from .programization import REVIEW_EVENT_TYPE, ProgramizationStore, _validate
from . import timeutil

REQUEST_SCHEMA_VERSION = "program_request.v0.1"
PROGRAM_REGISTRY_REL = "05_REGISTRIES/PROGRAM_REGISTRY.yaml"

# The pattern's role is the MVP analysis role; its contract ceiling bounds the authority
# evaluation recorded on the refusal evidence (03_ROLE_CONTRACTS: permission_ceiling P3).
_ROLE_CEILING = "P3"


def _registry_snapshot(program_id: str, program_version: str, root: Path) -> dict[str, Any]:
    """The honest registry snapshot for the requested program.

    A registered entry is resolved through the canonical resolver (definition hash checked,
    fail-closed on mismatch); an unknown id/version yields an ``unregistered`` snapshot —
    a fact to record, not an error. Resolver failures (corrupt registry, hash mismatch)
    refuse the request entirely."""
    try:
        registry = yaml.safe_load((root / PROGRAM_REGISTRY_REL).read_text(encoding="utf-8"))
        definitions = load_resource_definitions(repo_root=root, registry=registry, collection_key="programs")
        resolved = resolve_resource_registry(
            repo_root=root, registry=registry, definitions=definitions,
            governance_policy={"policy_id": POLICY_BINDING["policy_id"]},
            collection_key="programs", id_key="program_id",
        )
    except (OSError, yaml.YAMLError, RegistryResolutionError) as exc:
        raise ProgramizationBlocked("REGISTRY_UNRESOLVABLE", f"program registry cannot be resolved: {exc}") from exc

    for entry in resolved.get("programs", []):
        if entry.get("program_id") == program_id and entry.get("version") == program_version:
            return {
                "registered": True,
                "status": str(entry.get("status")),
                "enabled": bool(entry.get("enabled")),
                "runtime_implementation_available": bool(entry.get("runtime_implementation_available")),
                "deterministic": bool(entry.get("deterministic", False)),
                "required_permission_level": str(entry.get("required_permission_level") or "P4"),
            }
    return {
        "registered": False,
        "status": "unregistered",
        "enabled": False,
        "runtime_implementation_available": False,
        "deterministic": False,
        "required_permission_level": "P4",
    }


def _lineage_task(store: ProgramizationStore, pattern: Mapping[str, Any] | None, pattern_id: str) -> dict[str, Any]:
    """A synthetic task mapping carrying the REAL identity of the pattern's last valid
    observation — the promotion-audit precedent: off-run-path records anchor to the
    originating task, never to a fabricated one."""
    valid_ids = set(pattern.get("valid_observation_ids", [])) if pattern else set()
    anchor: Mapping[str, Any] | None = None
    for wrapper in store.read_observations():
        record = wrapper.get("record", {}) if isinstance(wrapper, dict) else {}
        if record.get("observation_id") in valid_ids:
            anchor = record
    if anchor is None:
        raise ProgramizationBlocked(
            "REQUEST_LINEAGE_MISSING",
            f"pattern {pattern_id!r} has no valid observation to anchor the request to",
        )
    return {
        "identity": {"task_id": anchor["task_id"], "task_revision": anchor["task_revision"],
                     "trace_id": anchor["trace_id"]},
        "context": {"core_context_binding_id": anchor["core_context_binding_id"]},
    }


def create_program_request(
    store: ProgramizationStore,
    candidate_id: str,
    *,
    program_id: str,
    program_version: str,
    operation_type: str = "program.invoke",
    requested_by: str,
    reason: str,
    now: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Create the fail-closed ``program_request.v0.1`` for an ACCEPTED candidate.

    Every field is computed honestly from real state: registry snapshot via the canonical
    resolver, task lineage from the pattern's last valid observation, a real BLOCK
    PermissionDecision for the exact invocation, zero program-call budget (none exists),
    and a validation verdict that must come out BLOCK while the registry has no active
    Programs. One request per candidate; secret-bearing content and schema violations
    refuse before anything persists. Returns the request record (the paired permission
    decision rides the stored wrapper row)."""
    root = repo_root if repo_root is not None else _repo_root()
    if not (isinstance(requested_by, str) and requested_by.strip()):
        raise ProgramizationBlocked("MISSING_OPERATOR", "a program request requires an operator identity")
    if not (isinstance(reason, str) and reason.strip()):
        raise ProgramizationBlocked("MISSING_REASON", "a program request requires an operator reason")

    with store.lock():
        candidate = store.latest_candidates().get(candidate_id)
        if candidate is None:
            raise ProgramizationBlocked("CANDIDATE_NOT_FOUND", f"no candidate {candidate_id!r}")
        if candidate.get("status") != "ACCEPTED":
            raise ProgramizationBlocked(
                "REQUEST_REQUIRES_ACCEPTED",
                "a program request is the accepted review's next step — the candidate must be ACCEPTED",
            )
        if any(row.get("candidate_id") == candidate_id for row in store.read_requests()):
            raise ProgramizationBlocked("REQUEST_EXISTS", f"candidate {candidate_id!r} already has a program request")

        pattern_id = str(candidate.get("pattern_id"))
        pattern = store.latest_patterns().get(pattern_id)
        task = _lineage_task(store, pattern, pattern_id)
        pattern_role = str((pattern or {}).get("pattern_signature", {}).get("role_id") or "general.specialist")
        snapshot = _registry_snapshot(program_id, program_version, root)
        # A registered-but-not-active program is a DISABLED resource; an unknown one is
        # UNREGISTERED. Both are BLOCK by policy — the scope names which fact refused it.
        scope = "DISABLED_RESOURCE_EXECUTION" if snapshot["registered"] else "UNREGISTERED_RESOURCE_EXECUTION"

        candidate_sha = integrity.sha256_record(dict(candidate))
        normalized_parameters = {"pattern_id": pattern_id, "candidate_id": candidate_id}
        decision = build_resource_refusal_permission_decision(
            task,
            program_id=program_id, program_version=program_version,
            permission_scope=scope,
            required_permission_level=snapshot["required_permission_level"],
            role_permission_ceiling=_ROLE_CEILING,
            target_ref=f"programization_candidate:{candidate_id}",
            content_sha256=candidate_sha,
            normalized_parameters=normalized_parameters,
            now=now, repo_root=root,
        )

        expires_at = timeutil.format_iso(timeutil.parse_iso(now) + timedelta(minutes=MVP_TTL_MINUTES))
        budget_ref = f"programization_pattern:{pattern_id}#no_program_call_budget"
        deterministic_required = True    # the candidate's deterministic slice is the point
        fingerprint_payload = {
            "schema_version": "resource_request_fingerprint_payload.v0.1",
            "resource_type": "PROGRAM",
            "task_id": task["identity"]["task_id"],
            "task_revision": task["identity"]["task_revision"],
            "core_context_binding_id": task["context"]["core_context_binding_id"],
            "requester_ref": "system:mvp.programization.review",
            "resource_id": program_id,
            "resource_version": program_version,
            "operation_type": operation_type,
            "permission_scope": scope,
            "target_ref": f"programization_candidate:{candidate_id}",
            "data_scope": ["programization.review"],
            "input_refs": [f"programization_candidate:{candidate_id}"],
            "input_sha256": [candidate_sha],
            "content_sha256": None,
            "normalized_parameters": normalized_parameters,
            "assignment_budget_ref": budget_ref,
            "expires_at": expires_at,
        }
        request_fingerprint = integrity.sha256_value(fingerprint_payload)
        request_id = integrity.short_id(
            "progreq", {"candidate_id": candidate_id, "program_id": program_id,
                        "program_version": program_version, "created_at": now},
        )

        block_reasons = []
        if not snapshot["registered"]:
            block_reasons.append("program_not_registered")
        else:
            if not (snapshot["status"] == "active" and snapshot["enabled"]):
                block_reasons.append("program_not_active_and_enabled")
            if not snapshot["runtime_implementation_available"]:
                block_reasons.append("runtime_implementation_unavailable")
        block_reasons += [
            "role_definition_not_allowlisted",
            "assignment_not_allowlisted",
            "assignment_program_call_budget_is_zero",
        ]
        determinism_match = bool(snapshot["deterministic"]) or not deterministic_required
        if not determinism_match:
            block_reasons.append("determinism_requirement_not_satisfied")
        if not decision["authority"]["authority_sufficient"]:
            block_reasons.append("authority_insufficient")

        request = {
            "schema_version": REQUEST_SCHEMA_VERSION,
            "program_request_id": request_id,
            "trace_id": task["identity"]["trace_id"],
            "task_id": task["identity"]["task_id"],
            "task_revision": task["identity"]["task_revision"],
            "core_context_binding_id": task["context"]["core_context_binding_id"],
            "operating_policy": dict(POLICY_BINDING),
            "requested_by": {
                "actor_type": "system", "actor_id": "mvp.programization.review",
                "role_id": None, "role_version": None, "assignment_id": None,
            },
            "role_scope": {
                "role_definition_ref": f"role_registry:{pattern_role}",
                "role_assignment_ref": f"programization_pattern:{pattern_id}",
                "role_definition_resource_allowlisted": False,
                "assignment_resource_allowlisted": False,
            },
            "resource": {
                "program_id": program_id,
                "program_version": program_version,
                "registry_ref": PROGRAM_REGISTRY_REL,
                "registry_status": snapshot["status"],
                "registry_enabled": snapshot["enabled"],
                "runtime_implementation_available": snapshot["runtime_implementation_available"],
                "deterministic": snapshot["deterministic"],
                "required_permission_level": snapshot["required_permission_level"],
            },
            "invocation": {
                "invocation_type": operation_type,
                "permission_scope": scope,
                "target_ref": f"programization_candidate:{candidate_id}",
                "data_scope": ["programization.review"],
                "input_refs": [f"programization_candidate:{candidate_id}"],
                "input_sha256": [candidate_sha],
                "content_sha256": None,
                "normalized_parameters": normalized_parameters,
                "purpose": reason.strip(),
                "deterministic_required": deterministic_required,
                "expected_output_contract": "program_result.v0.1.future",
                "expected_output_ref": f"review://program/{program_id}/result",
            },
            # Mirrored from the refusing PermissionDecision — one computation, one truth.
            "authority": {
                "request_required_permission_level": snapshot["required_permission_level"],
                "resource_required_permission_level": snapshot["required_permission_level"],
                "role_permission_ceiling": _ROLE_CEILING,
                "assignment_granted_permission_level": decision["authority"]["assignment_granted_permission_level"],
                "effective_permission_level": decision["authority"]["effective_permission_level"],
                "authority_sufficient": decision["authority"]["authority_sufficient"],
                "authority_reasons": list(decision["authority"]["authority_reasons"]),
            },
            "permission": {
                "permission_decision_ref": f"programization_requests:{request_id}#permission_decision",
                "permission_decision_id": decision["permission_decision_id"],
                "permission_decision": "BLOCK",
                "action_fingerprint": decision["action_fingerprint"],
                "approval_id": None,
                "binding_verified": True,
            },
            "budget": {
                "assignment_budget_ref": budget_ref,
                "requested_call_count": 1,
                "remaining_call_count": 0,
                "requested_runtime_seconds": 60,
                "remaining_runtime_seconds": 0,
                "requested_cost_decimal": "0",
                "remaining_cost_decimal": "0",
                "cost_currency": "USD",
                "within_assignment_budget": False,
            },
            "validation": {
                "registry_match": snapshot["registered"],
                "registry_runtime_eligible": bool(
                    snapshot["registered"] and snapshot["status"] == "active"
                    and snapshot["enabled"] and snapshot["runtime_implementation_available"]
                ),
                "role_definition_allowlist_match": False,
                "assignment_allowlist_match": False,
                "policy_scope_match": True,
                "permission_binding_match": True,
                "budget_within_limit": False,
                "determinism_match": determinism_match,
                "lineage_complete": True,
                "review_result": "BLOCK",
                "block_reasons": block_reasons,
            },
            "request_fingerprint_payload": fingerprint_payload,
            "request_fingerprint": request_fingerprint,
            "runtime_effect": {
                "mode": "REVIEW_ONLY",
                "request_record_can_execute": False,
                "executor_handoff_allowed": False,
                "tool_execution_allowed": False,
                "program_execution_allowed": False,
                "resource_enablement_allowed": False,
                "registry_mutation_allowed": False,
                "runtime_mutation_allowed": False,
                "external_execution_allowed": False,
                "financial_execution_allowed": False,
                "permission_expansion_allowed": False,
            },
            "lifecycle": {
                "review_status": "BLOCKED",
                "created_at": now,
                "expires_at": expires_at,
                "supersedes": [],
            },
            "audit_refs": [f"programization_ledger:program_request:{request_id}"],
        }
        try:
            integrity.scan_for_secret_bearing_keys(request)
        except IntegrityError as exc:
            raise ProgramizationBlocked("SECRET_IN_REQUEST", str(exc)) from exc
        _validate(request, REQUEST_SCHEMA_VERSION, "program_request", root)
        store.append_request({"candidate_id": candidate_id, "request": request,
                              "permission_decision": decision})
    return request


def build_request_event(
    request: Mapping[str, Any],
    *,
    candidate_id: str,
    requested_by: str,
    reason: str,
    now: str,
) -> dict[str, Any]:
    """The tamper-evident standalone ledger event for one program-request creation."""
    return stamped_event(
        REVIEW_EVENT_TYPE,
        action="program_request_created",
        candidate_id=candidate_id,
        program_request_id=str(request.get("program_request_id")),
        program_id=str(request.get("resource", {}).get("program_id")),
        program_version=str(request.get("resource", {}).get("program_version")),
        review_result=str(request.get("validation", {}).get("review_result")),
        block_reasons=list(request.get("validation", {}).get("block_reasons", [])),
        reviewed_by=requested_by, reason=reason, created_at=now,
    )
