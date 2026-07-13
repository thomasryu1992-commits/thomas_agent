from __future__ import annotations

from copy import deepcopy
from typing import Any

from runtime.protected_governance_state import validate_durable_transition_result_semantics
from runtime.read_only_kernel.integrity import (
    scan_for_secret_bearing_keys,
    sha256_record,
    sha256_value,
    short_id,
)
from .authorization import validate_entry_authorization_semantics
from .disabled_adapter import ADAPTER_ID, ADAPTER_VERSION

INTEGRATION_COMPONENT_ID = "thomas.runtime_entry.single_read_only_integration_candidate"
INTEGRATION_COMPONENT_VERSION = "0.1.0"
KERNEL_ID = "thomas.read_only_runtime_kernel"
KERNEL_VERSION = "0.1.1"
STORE_COMPONENT_ID = "thomas.protected_governance_state.sqlite_candidate"
STORE_COMPONENT_VERSION = "0.1.0"
TRANSITION_COMPONENT_ID = "thomas.runtime_entry.durable_cas.sqlite_candidate"
TRANSITION_COMPONENT_VERSION = "0.1.0"
ENTRY_MODE = "RUNTIME_AUTHORITATIVE_READ_ONLY"
EXPECTED_OUTPUT_SCHEMAS = [
    "read_only_runtime_run.v0.1",
    "agent_output.v0.2",
    "validation_result.v0.1",
    "audit_event.v0.1",
]


class SingleEntryIntegrationError(ValueError):
    pass


def _valid_sha(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(char in "0123456789abcdef" for char in value[7:])
    )


def _authorization_evidence(
    authorization: dict[str, Any],
    *,
    ref: str,
) -> dict[str, Any]:
    approval = authorization["action_approval"]
    decision = authorization["decision"]
    return {
        "authorization_id": authorization["authorization_id"],
        "authorization_ref": ref,
        "authorization_sha256": sha256_record(authorization),
        "authorization_status": authorization["status"],
        "record_scope": authorization["record_scope"],
        "approval_verified": approval["approval_verified"],
        "approval_status": approval["approval_status"],
        "consumption_state": approval["consumption_state"],
        "current_contract_real_consumption_supported": approval[
            "current_contract_real_consumption_supported"
        ],
        "usable_for_runtime_entry": decision["usable_for_runtime_entry"],
    }


def _transition_evidence(
    transition: dict[str, Any] | None,
    *,
    ref: str | None,
) -> dict[str, Any]:
    if transition is None:
        return {
            "present": False,
            "transition_id": None,
            "transition_ref": None,
            "transition_sha256": None,
            "transition_status": "NOT_PRESENT",
            "record_scope": None,
            "synthetic_commit_observed": False,
            "session_id": None,
            "session_state": "NOT_RESERVED",
            "real_action_approval_consumed": False,
            "runtime_session_started": False,
            "kernel_called": False,
        }
    return {
        "present": True,
        "transition_id": transition["transition_id"],
        "transition_ref": ref,
        "transition_sha256": sha256_record(transition),
        "transition_status": transition["status"],
        "record_scope": transition["record_scope"],
        "synthetic_commit_observed": (
            transition["status"] == "COMMITTED_SYNTHETIC_TEST_ONLY"
        ),
        "session_id": transition["session"].get("session_id"),
        "session_state": transition["session"].get("after_state", "NOT_RESERVED"),
        "real_action_approval_consumed": transition["authorization_state"][
            "real_action_approval_consumed"
        ],
        "runtime_session_started": transition["session"]["runtime_session_started"],
        "kernel_called": transition["session"]["kernel_called"],
    }


def _derive_blockers(
    authorization: dict[str, Any],
    transition: dict[str, Any] | None,
) -> list[str]:
    blockers: list[str] = []
    scope = authorization["record_scope"]
    approval = authorization["action_approval"]
    if scope == "REAL_REVIEW_RECORD":
        if approval["approval_verified"] is not True:
            blockers.append("ACTION_APPROVAL_NOT_VERIFIED")
        if approval["current_contract_real_consumption_supported"] is not True:
            blockers.append("REAL_APPROVAL_CONSUMPTION_UNAVAILABLE")
        if transition is None:
            blockers.append("DURABLE_TRANSITION_NOT_PRESENT")
    else:
        blockers.append("SYNTHETIC_SCOPE_NOT_RUNTIME_ELIGIBLE")
        if authorization["status"] != "APPROVED_NOT_CONSUMED_REVIEW_ONLY":
            blockers.append("SYNTHETIC_AUTHORIZATION_NOT_APPROVED_FOR_TEST_REVIEW")
        if transition is None:
            blockers.append("SYNTHETIC_DURABLE_TRANSITION_NOT_PRESENT")
    blockers.extend(
        [
            "RUNTIME_ENTRY_ADAPTER_DISABLED",
            "KERNEL_CALL_NOT_ALLOWED",
        ]
    )
    return blockers


def _integration_checks(
    authorization: dict[str, Any],
    transition: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    approval = authorization["action_approval"]
    synthetic = authorization["record_scope"] == "SYNTHETIC_TEST_ONLY"
    transition_ok = (
        transition is not None
        and transition["status"] == "COMMITTED_SYNTHETIC_TEST_ONLY"
        and transition["session"].get("after_state") == "RESERVED"
        and transition["authorization_state"].get("after_state") == "CONSUMED"
    )
    return [
        {
            "check_id": "entry_authorization_semantics",
            "result": "PASS",
            "notes": "Exact Entry Authorization passed I0.5.3 semantic validation.",
        },
        {
            "check_id": "action_approval_boundary",
            "result": (
                "PASS"
                if synthetic and approval["approval_verified"] is True
                else "BLOCK"
            ),
            "notes": (
                "Only the synthetic fixture carries review-only approval evidence; "
                "no real Approval verifier or consumer is available."
            ),
        },
        {
            "check_id": "durable_transition_binding",
            "result": "PASS" if transition_ok else "BLOCK",
            "notes": (
                "A matching synthetic I0.5.4 durable transition may be observed "
                "for integration testing only."
            ),
        },
        {
            "check_id": "disabled_entry_adapter",
            "result": "PASS",
            "notes": (
                "The existing I0.5.2 adapter is present but remains disabled and "
                "cannot accept a Runtime handoff."
            ),
        },
        {
            "check_id": "kernel_call_boundary",
            "result": "PASS",
            "notes": (
                "The integration candidate creates only a hash-bound invocation "
                "envelope; Kernel invocation remains prohibited."
            ),
        },
        {
            "check_id": "prohibited_effects",
            "result": "PASS",
            "notes": (
                "No model, Tool, Program, network, Data Plane write, external "
                "action, or financial action is permitted."
            ),
        },
    ]


def integration_fingerprint_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": (
            "disabled_single_read_only_entry_integration_candidate_"
            "fingerprint_payload.v0.1"
        ),
        "candidate_id": record["candidate_id"],
        "authorization_sha256": record["authorization_evidence"][
            "authorization_sha256"
        ],
        "transition_sha256": record["durable_transition_evidence"][
            "transition_sha256"
        ],
        "exact_bindings": deepcopy(record["exact_bindings"]),
        "component_bindings": deepcopy(record["component_bindings"]),
        "kernel_invocation_candidate": deepcopy(
            record["kernel_invocation_candidate"]
        ),
        "blocking_reasons": deepcopy(record["decision"]["blocking_reasons"]),
        "created_at": record["created_at"],
    }


def build_disabled_single_entry_integration_candidate(
    authorization: dict[str, Any],
    *,
    authorization_ref: str,
    created_at: str,
    durable_transition: dict[str, Any] | None = None,
    durable_transition_ref: str | None = None,
) -> dict[str, Any]:
    scan_for_secret_bearing_keys(authorization)
    validate_entry_authorization_semantics(authorization)
    if durable_transition is not None:
        scan_for_secret_bearing_keys(durable_transition)
        validate_durable_transition_result_semantics(durable_transition)
        if durable_transition["status"] != "COMMITTED_SYNTHETIC_TEST_ONLY":
            raise SingleEntryIntegrationError(
                "integration candidate accepts only a committed synthetic transition fixture"
            )
        if (
            durable_transition["authorization_state"]["authorization_id"]
            != authorization["authorization_id"]
        ):
            raise SingleEntryIntegrationError(
                "durable transition Authorization ID mismatch"
            )
        if (
            durable_transition["authorization_state"]["authorization_sha256"]
            != authorization["integrity"]["record_sha256"]
        ):
            raise SingleEntryIntegrationError(
                "durable transition Authorization hash mismatch"
            )
        if (
            durable_transition["authorization_state"][
                "action_fingerprint_sha256"
            ]
            != authorization["action_fingerprint"]["sha256"]
        ):
            raise SingleEntryIntegrationError(
                "durable transition action fingerprint mismatch"
            )
        if authorization["record_scope"] != "SYNTHETIC_TEST_ONLY":
            raise SingleEntryIntegrationError(
                "I0.5.4 durable transition evidence is synthetic-only"
            )
        if durable_transition_ref is None:
            raise SingleEntryIntegrationError(
                "durable transition reference is required when transition evidence is present"
            )

    authorization_evidence = _authorization_evidence(
        authorization,
        ref=authorization_ref,
    )
    transition_evidence = _transition_evidence(
        durable_transition,
        ref=durable_transition_ref,
    )
    blockers = _derive_blockers(authorization, durable_transition)
    candidate_seed = {
        "authorization_sha256": authorization_evidence["authorization_sha256"],
        "transition_sha256": transition_evidence["transition_sha256"],
        "component_id": INTEGRATION_COMPONENT_ID,
        "component_version": INTEGRATION_COMPONENT_VERSION,
        "created_at": created_at,
    }
    candidate_id = short_id("entryint", candidate_seed)
    record = {
        "schema_version": (
            "disabled_single_read_only_entry_integration_candidate.v0.1"
        ),
        "candidate_id": candidate_id,
        "phase": "I0.5.5",
        "status": "BLOCKED_DISABLED_INTEGRATION_CANDIDATE",
        "owner": "Thomas",
        "record_scope": authorization["record_scope"],
        "runtime_source_of_truth": False,
        "authorization_evidence": authorization_evidence,
        "durable_transition_evidence": transition_evidence,
        "exact_bindings": deepcopy(authorization["exact_bindings"]),
        "component_bindings": {
            "integration_candidate": {
                "component_id": INTEGRATION_COMPONENT_ID,
                "version": INTEGRATION_COMPONENT_VERSION,
            },
            "kernel": deepcopy(authorization["component_bindings"]["kernel"]),
            "entry_adapter": deepcopy(
                authorization["component_bindings"]["entry_adapter"]
            ),
            "protected_state_store": {
                "component_id": STORE_COMPONENT_ID,
                "version": STORE_COMPONENT_VERSION,
            },
            "durable_transition": {
                "component_id": TRANSITION_COMPONENT_ID,
                "version": TRANSITION_COMPONENT_VERSION,
            },
        },
        "integration_checks": _integration_checks(
            authorization,
            durable_transition,
        ),
        "protected_state_boundary": {
            "synthetic_transition_commit_observed": transition_evidence[
                "synthetic_commit_observed"
            ],
            "session_state_observed": transition_evidence["session_state"],
            "recovery_inspection_required_before_any_future_action": (
                transition_evidence["synthetic_commit_observed"]
            ),
            "real_action_approval_consumption_observed": False,
            "runtime_authoritative_state_write_allowed": False,
            "automatic_retry_allowed": False,
            "automatic_resume_allowed": False,
        },
        "disabled_adapter_boundary": {
            "adapter_id": ADAPTER_ID,
            "adapter_version": ADAPTER_VERSION,
            "implementation_available": True,
            "enabled": False,
            "runtime_source_of_truth": False,
            "entry_call_allowed": False,
            "executor_handoff_allowed": False,
        },
        "kernel_invocation_candidate": {
            "requested_mode": ENTRY_MODE,
            "requested_run_count": 1,
            "exact_task_binding": deepcopy(
                authorization["exact_bindings"]["task"]
            ),
            "exact_input_bundle_binding": deepcopy(
                authorization["exact_bindings"]["input_bundle"]
            ),
            "exact_current_core_binding": deepcopy(
                authorization["exact_bindings"]["current_core"]
            ),
            "exact_core_context_binding": deepcopy(
                authorization["exact_bindings"]["core_context_binding"]
            ),
            "allowed_read_paths": deepcopy(authorization["allowed_read_paths"]),
            "resource_limits": deepcopy(authorization["resource_limits"]),
            "expected_output_schemas": deepcopy(
                authorization["expected_output_schemas"]
            ),
            "candidate_envelope_created": True,
            "runtime_handoff_allowed": False,
            "kernel_call_allowed": False,
            "kernel_called": False,
        },
        "decision": {
            "result": "BLOCKED_DISABLED_INTEGRATION_CANDIDATE",
            "blocking_reasons": blockers,
            "integration_chain_structurally_consistent": True,
            "eligible_for_disabled_adapter_review": True,
            "ready_for_runtime_entry": False,
            "runtime_handoff_performed": False,
            "runtime_session_started": False,
            "kernel_called": False,
            "runtime_entry_performed": False,
        },
        "runtime_effect": {
            "mode": "DISABLED_INTEGRATION_EVIDENCE_ONLY",
            "grants_runtime_permission": False,
            "grants_runtime_activation": False,
            "consumes_real_approval": False,
            "performs_real_compare_and_set": False,
            "writes_runtime_governance_state": False,
            "starts_runtime_session": False,
            "performs_runtime_handoff": False,
            "calls_kernel": False,
            "model_invocation": False,
            "tool_execution": False,
            "program_execution": False,
            "network_access": False,
            "domain_write": False,
            "workspace_write": False,
            "core_write": False,
            "external_action": False,
            "financial_action": False,
            "mutates_runtime": False,
        },
        "integrity": {
            "hash_schema": (
                "disabled_single_read_only_entry_integration_candidate_"
                "fingerprint_payload.v0.1"
            ),
            "candidate_fingerprint_payload": {},
            "candidate_sha256": "",
        },
        "created_at": created_at,
    }
    payload = integration_fingerprint_payload(record)
    record["integrity"]["candidate_fingerprint_payload"] = payload
    record["integrity"]["candidate_sha256"] = sha256_value(payload)
    return record


def validate_disabled_single_entry_integration_candidate_semantics(
    record: dict[str, Any],
) -> None:
    scan_for_secret_bearing_keys(record)
    if (
        record.get("schema_version")
        != "disabled_single_read_only_entry_integration_candidate.v0.1"
    ):
        raise SingleEntryIntegrationError("integration candidate schema mismatch")
    if (
        record.get("phase") != "I0.5.5"
        or record.get("status")
        != "BLOCKED_DISABLED_INTEGRATION_CANDIDATE"
    ):
        raise SingleEntryIntegrationError(
            "integration candidate phase/status mismatch"
        )
    if (
        record.get("owner") != "Thomas"
        or record.get("runtime_source_of_truth") is not False
    ):
        raise SingleEntryIntegrationError(
            "integration candidate ownership/source boundary mismatch"
        )
    if record.get("record_scope") not in {
        "REAL_REVIEW_RECORD",
        "SYNTHETIC_TEST_ONLY",
    }:
        raise SingleEntryIntegrationError("integration candidate scope mismatch")

    auth = record.get("authorization_evidence", {})
    if not _valid_sha(auth.get("authorization_sha256")):
        raise SingleEntryIntegrationError(
            "Authorization evidence hash is invalid"
        )
    if auth.get("usable_for_runtime_entry") is not False:
        raise SingleEntryIntegrationError(
            "I0.5.3 Authorization cannot become usable for Runtime entry"
        )
    if auth.get("current_contract_real_consumption_supported") is not False:
        raise SingleEntryIntegrationError(
            "Approval v0.1 real consumption boundary changed"
        )
    if auth.get("record_scope") != record.get("record_scope"):
        raise SingleEntryIntegrationError("Authorization scope mismatch")

    transition = record.get("durable_transition_evidence", {})
    present = transition.get("present") is True
    if present:
        for key in [
            "transition_id",
            "transition_ref",
            "transition_sha256",
            "session_id",
        ]:
            if not isinstance(transition.get(key), str) or not transition.get(key):
                raise SingleEntryIntegrationError(
                    f"durable transition evidence missing {key}"
                )
        if not _valid_sha(transition.get("transition_sha256")):
            raise SingleEntryIntegrationError(
                "durable transition hash is invalid"
            )
        if record.get("record_scope") != "SYNTHETIC_TEST_ONLY":
            raise SingleEntryIntegrationError(
                "durable transition evidence must remain synthetic-only"
            )
        if (
            transition.get("transition_status")
            != "COMMITTED_SYNTHETIC_TEST_ONLY"
        ):
            raise SingleEntryIntegrationError(
                "durable transition status mismatch"
            )
        if (
            transition.get("record_scope") != "SYNTHETIC_TEST_ONLY"
            or transition.get("synthetic_commit_observed") is not True
        ):
            raise SingleEntryIntegrationError(
                "synthetic durable transition boundary mismatch"
            )
        if transition.get("session_state") != "RESERVED":
            raise SingleEntryIntegrationError(
                "synthetic Session reservation evidence mismatch"
            )
    else:
        expected_absent = {
            "present": False,
            "transition_id": None,
            "transition_ref": None,
            "transition_sha256": None,
            "transition_status": "NOT_PRESENT",
            "record_scope": None,
            "synthetic_commit_observed": False,
            "session_id": None,
            "session_state": "NOT_RESERVED",
            "real_action_approval_consumed": False,
            "runtime_session_started": False,
            "kernel_called": False,
        }
        if transition != expected_absent:
            raise SingleEntryIntegrationError(
                "absent durable transition evidence is inconsistent"
            )
    for key in [
        "real_action_approval_consumed",
        "runtime_session_started",
        "kernel_called",
    ]:
        if transition.get(key) is not False:
            raise SingleEntryIntegrationError(
                f"prohibited transition effect became true: {key}"
            )

    bindings = record.get("component_bindings", {})
    expected_components = {
        "integration_candidate": (
            INTEGRATION_COMPONENT_ID,
            INTEGRATION_COMPONENT_VERSION,
        ),
        "kernel": (KERNEL_ID, KERNEL_VERSION),
        "entry_adapter": (ADAPTER_ID, ADAPTER_VERSION),
        "protected_state_store": (
            STORE_COMPONENT_ID,
            STORE_COMPONENT_VERSION,
        ),
        "durable_transition": (
            TRANSITION_COMPONENT_ID,
            TRANSITION_COMPONENT_VERSION,
        ),
    }
    for key, (component_id, version) in expected_components.items():
        item = bindings.get(key, {})
        if (
            item.get("component_id") != component_id
            or item.get("version") != version
        ):
            raise SingleEntryIntegrationError(
                f"component binding mismatch: {key}"
            )
    for key in ["kernel", "entry_adapter"]:
        if not _valid_sha(bindings[key].get("implementation_sha256")):
            raise SingleEntryIntegrationError(
                f"component implementation hash invalid: {key}"
            )

    protected = record.get("protected_state_boundary", {})
    if (
        protected.get("synthetic_transition_commit_observed")
        is not transition.get("synthetic_commit_observed")
    ):
        raise SingleEntryIntegrationError(
            "protected state observation mismatch"
        )
    if (
        protected.get("session_state_observed")
        != transition.get("session_state")
    ):
        raise SingleEntryIntegrationError(
            "protected Session observation mismatch"
        )
    if (
        protected.get("recovery_inspection_required_before_any_future_action")
        is not transition.get("synthetic_commit_observed")
    ):
        raise SingleEntryIntegrationError("recovery requirement mismatch")
    for key in [
        "real_action_approval_consumption_observed",
        "runtime_authoritative_state_write_allowed",
        "automatic_retry_allowed",
        "automatic_resume_allowed",
    ]:
        if protected.get(key) is not False:
            raise SingleEntryIntegrationError(
                f"protected state boundary violated: {key}"
            )

    adapter = record.get("disabled_adapter_boundary", {})
    if (
        adapter.get("adapter_id") != ADAPTER_ID
        or adapter.get("adapter_version") != ADAPTER_VERSION
    ):
        raise SingleEntryIntegrationError(
            "disabled adapter identity/version mismatch"
        )
    if adapter.get("implementation_available") is not True:
        raise SingleEntryIntegrationError(
            "disabled adapter implementation must be explicit"
        )
    for key in [
        "enabled",
        "runtime_source_of_truth",
        "entry_call_allowed",
        "executor_handoff_allowed",
    ]:
        if adapter.get(key) is not False:
            raise SingleEntryIntegrationError(
                f"disabled adapter boundary violated: {key}"
            )

    envelope = record.get("kernel_invocation_candidate", {})
    if (
        envelope.get("requested_mode") != ENTRY_MODE
        or envelope.get("requested_run_count") != 1
    ):
        raise SingleEntryIntegrationError(
            "Kernel invocation candidate mode/count mismatch"
        )
    if (
        envelope.get("exact_task_binding")
        != record.get("exact_bindings", {}).get("task")
    ):
        raise SingleEntryIntegrationError(
            "Kernel envelope Task binding mismatch"
        )
    if (
        envelope.get("exact_input_bundle_binding")
        != record.get("exact_bindings", {}).get("input_bundle")
    ):
        raise SingleEntryIntegrationError(
            "Kernel envelope Input Bundle binding mismatch"
        )
    if (
        envelope.get("exact_current_core_binding")
        != record.get("exact_bindings", {}).get("current_core")
    ):
        raise SingleEntryIntegrationError(
            "Kernel envelope Current Core binding mismatch"
        )
    if (
        envelope.get("exact_core_context_binding")
        != record.get("exact_bindings", {}).get("core_context_binding")
    ):
        raise SingleEntryIntegrationError(
            "Kernel envelope Core Context binding mismatch"
        )
    if envelope.get("candidate_envelope_created") is not True:
        raise SingleEntryIntegrationError(
            "Kernel invocation candidate envelope must be explicit"
        )
    for key in [
        "runtime_handoff_allowed",
        "kernel_call_allowed",
        "kernel_called",
    ]:
        if envelope.get(key) is not False:
            raise SingleEntryIntegrationError(
                f"Kernel invocation boundary violated: {key}"
            )
    paths = envelope.get("allowed_read_paths")
    if (
        not isinstance(paths, list)
        or not paths
        or len(paths) != len(set(paths))
    ):
        raise SingleEntryIntegrationError(
            "Kernel invocation allowed paths are invalid"
        )
    limits = envelope.get("resource_limits", {})
    for key, maximum in [
        ("ttl_seconds", 900),
        ("max_runtime_seconds", 60),
        ("max_files_read", 32),
        ("max_total_bytes_read", 8 * 1024 * 1024),
    ]:
        value = limits.get(key)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 1
            or value > maximum
        ):
            raise SingleEntryIntegrationError(
                f"Kernel invocation resource limit invalid: {key}"
            )
    if envelope.get("expected_output_schemas") != EXPECTED_OUTPUT_SCHEMAS:
        raise SingleEntryIntegrationError(
            "Kernel invocation expected output schema set mismatch"
        )

    expected_blockers: list[str] = []
    if record["record_scope"] == "REAL_REVIEW_RECORD":
        if auth.get("approval_verified") is not True:
            expected_blockers.append("ACTION_APPROVAL_NOT_VERIFIED")
        if auth.get("current_contract_real_consumption_supported") is not True:
            expected_blockers.append("REAL_APPROVAL_CONSUMPTION_UNAVAILABLE")
        if not present:
            expected_blockers.append("DURABLE_TRANSITION_NOT_PRESENT")
    else:
        expected_blockers.append("SYNTHETIC_SCOPE_NOT_RUNTIME_ELIGIBLE")
        if (
            auth.get("authorization_status")
            != "APPROVED_NOT_CONSUMED_REVIEW_ONLY"
        ):
            expected_blockers.append(
                "SYNTHETIC_AUTHORIZATION_NOT_APPROVED_FOR_TEST_REVIEW"
            )
        if not present:
            expected_blockers.append(
                "SYNTHETIC_DURABLE_TRANSITION_NOT_PRESENT"
            )
    expected_blockers.extend(
        ["RUNTIME_ENTRY_ADAPTER_DISABLED", "KERNEL_CALL_NOT_ALLOWED"]
    )
    decision = record.get("decision", {})
    if (
        decision.get("result")
        != "BLOCKED_DISABLED_INTEGRATION_CANDIDATE"
    ):
        raise SingleEntryIntegrationError(
            "integration decision result mismatch"
        )
    if decision.get("blocking_reasons") != expected_blockers:
        raise SingleEntryIntegrationError(
            "integration blocking reasons mismatch"
        )
    if (
        decision.get("integration_chain_structurally_consistent") is not True
        or decision.get("eligible_for_disabled_adapter_review") is not True
    ):
        raise SingleEntryIntegrationError(
            "integration structural-review flags mismatch"
        )
    for key in [
        "ready_for_runtime_entry",
        "runtime_handoff_performed",
        "runtime_session_started",
        "kernel_called",
        "runtime_entry_performed",
    ]:
        if decision.get(key) is not False:
            raise SingleEntryIntegrationError(
                f"integration decision effect must remain false: {key}"
            )

    effects = record.get("runtime_effect", {})
    if effects.get("mode") != "DISABLED_INTEGRATION_EVIDENCE_ONLY":
        raise SingleEntryIntegrationError(
            "integration Runtime effect mode mismatch"
        )
    if any(
        value is not False
        for key, value in effects.items()
        if key != "mode"
    ):
        raise SingleEntryIntegrationError(
            "integration Runtime effects must remain false"
        )

    integrity = record.get("integrity", {})
    expected_payload = integration_fingerprint_payload(record)
    if (
        integrity.get("hash_schema")
        != "disabled_single_read_only_entry_integration_candidate_"
        "fingerprint_payload.v0.1"
    ):
        raise SingleEntryIntegrationError(
            "integration fingerprint schema mismatch"
        )
    if integrity.get("candidate_fingerprint_payload") != expected_payload:
        raise SingleEntryIntegrationError(
            "integration fingerprint payload mismatch"
        )
    if integrity.get("candidate_sha256") != sha256_value(expected_payload):
        raise SingleEntryIntegrationError("integration fingerprint mismatch")
