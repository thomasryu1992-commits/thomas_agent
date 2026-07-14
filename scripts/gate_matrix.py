from __future__ import annotations

ACTIVE_CHECKS = [
    ("Architecture Slimming", ["scripts/validate_slimming_package.py"]),
    ("Static Integrity", ["scripts/validate_static_integrity.py"]),
    ("I0.4.1 Preconditions", ["scripts/validate_i0_preconditions.py"]),
    ("Runtime Contract Consistency", ["scripts/validate_contract_consistency.py"]),
    ("Task Contract", ["scripts/validate_task_contracts.py"]),
    ("Permission and Approval Foundation", ["scripts/validate_permission_approval_contracts.py"]),
    ("Tool and Program Request Foundation", ["scripts/validate_tool_program_request_contracts.py"]),
    ("Execution Validation and Audit Foundation", ["scripts/validate_execution_validation_audit_contracts.py"]),
    ("I0.5 Read-only Runtime Kernel", ["scripts/validate_i0_5_read_only_runtime.py"]),
    ("Thomas Core", ["scripts/validate_thomas_core.py"]),
    ("Core Projection Consistency", ["scripts/validate_core_projection_consistency.py", "--strict"]),
    ("Runtime Lineage Bundle", ["scripts/validate_runtime_lineage_bundle.py"]),
    ("Programization and Operational Knowledge", ["scripts/validate_programization_contracts.py"]),
    ("Core Lifecycle Schemas", ["scripts/validate_core_lifecycle_schemas.py"]),
    ("Contract Schema Parity", ["scripts/validate_contract_schema_parity.py"]),
    ("Security Hardening", ["scripts/validate_security_hardening.py"]),
]

DEFERRED_CHECKS = [
    ("Executor Foundation Review-Only", ["scripts/validate_executor_foundation_contracts.py"]),
    (
        "Operations Evidence and Executor Candidate Intake Review-Only",
        ["scripts/validate_operations_evidence_executor_intake.py"],
    ),
    (
        "Control, Supervision, Threshold, and Sandbox Review-Only",
        ["scripts/validate_control_supervision_threshold_sandbox.py"],
    ),
    (
        "I0.5.1 Runtime Promotion Readiness",
        ["scripts/validate_i0_5_1_runtime_promotion_readiness.py"],
    ),
    (
        "I0.5.2 Runtime-Authoritative Read-only Entry Design",
        ["scripts/validate_i0_5_2_runtime_authoritative_read_only_entry.py"],
    ),
    (
        "I0.5.3 Exact Entry Authorization and At-Most-Once Transition Design",
        ["scripts/validate_i0_5_3_runtime_entry_authorization.py"],
    ),
    (
        "I0.5.4 Protected Local Governance State and Durable CAS Candidate",
        ["scripts/validate_i0_5_4_protected_governance_state.py"],
    ),
    (
        "I0.5.5 Disabled Single Read-only Entry Integration Candidate",
        ["scripts/validate_i0_5_5_disabled_single_read_only_entry_integration.py"],
    ),
]

LEGACY_COMPATIBILITY_CHECKS = [
    ("I0.4 Consolidated Contract Set", ["scripts/validate_i0_4_consolidated_contract_set.py"]),
    ("Core Release Reproducibility", ["scripts/validate_core_release_reproducibility.py"]),
    ("Core Apply Idempotency", ["scripts/test_apply_core_idempotency.py"]),
]

DEFERRED_VALIDATOR_FILENAMES = {
    command[0]
    for _, command in DEFERRED_CHECKS
}

ACTIVE_VALIDATOR_FILENAMES = {
    command[0]
    for _, command in ACTIVE_CHECKS
}
