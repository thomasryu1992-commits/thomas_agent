from __future__ import annotations


ACTIVE_CHECKS = [
    ("Architecture Slimming", ["scripts/run_slimming_gate.py"]),
    ("Static Integrity", ["scripts/validate_static_integrity.py"]),
    ("I0.4.1 Preconditions", ["scripts/validate_i0_preconditions.py"]),
    ("Runtime Contract Consistency", ["scripts/validate_contract_consistency.py"]),
    ("Task Contract", ["scripts/validate_task_contracts.py"]),
    ("Permission and Approval Foundation", ["scripts/validate_permission_approval_contracts.py"]),
    ("Tool and Program Request Foundation", ["scripts/validate_tool_program_request_contracts.py"]),
    (
        "Execution Validation and Audit Foundation",
        ["scripts/validate_execution_validation_audit_contracts.py", "--scope", "active"],
    ),
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
    ("Deferred Architecture", ["scripts/validate_deferred_architecture.py"]),
]

LEGACY_COMPATIBILITY_CHECKS = [
    ("I0.4 Consolidated Contract Set", ["scripts/validate_i0_4_consolidated_contract_set.py"]),
    ("Core Release Reproducibility", ["scripts/validate_core_release_reproducibility.py"]),
    ("Core Apply Idempotency", ["scripts/test_apply_core_idempotency.py"]),
]

GATE_SCOPE_ORDER = ("active", "deferred", "legacy")

GATE_DEFINITIONS = {
    "active": {
        "gate_id": "thomas.active_architecture",
        "display_name": "Thomas Agent Active Gate",
        "description": "Run current Thomas Agent architecture and read-only Runtime checks.",
        "evidence_filename": "ACTIVE_GATE_EVIDENCE.yaml",
        "checks": ACTIVE_CHECKS,
        "no_authority_message": (
            "This Gate grants no Core, Runtime, Tool, Program, external, or financial authority."
        ),
    },
    "deferred": {
        "gate_id": "thomas.deferred_architecture",
        "display_name": "Thomas Agent Deferred Architecture Gate",
        "description": (
            "Validate deferred Runtime Entry, Executor, Operations, Control, and Sandbox designs."
        ),
        "evidence_filename": "DEFERRED_ARCHITECTURE_GATE_EVIDENCE.yaml",
        "checks": DEFERRED_CHECKS,
        "no_authority_message": (
            "Deferred validation does not activate or authorize any Runtime capability."
        ),
    },
    "legacy": {
        "gate_id": "thomas.legacy_compatibility",
        "display_name": "Thomas Agent Legacy Compatibility Gate",
        "description": "Validate frozen I0.4 and Core release compatibility.",
        "evidence_filename": "LEGACY_COMPATIBILITY_GATE_EVIDENCE.yaml",
        "checks": LEGACY_COMPATIBILITY_CHECKS,
        "no_authority_message": (
            "Legacy compatibility evidence grants no Core, Runtime, or execution authority."
        ),
    },
}

# The repository-wide Release Gate reuses the canonical matrices. The two
# parameterized legacy checks remain in run_repository_release_gate.py because
# that command accepts release-specific CLI arguments and intentionally runs the
# nested-process idempotency test last.
REPOSITORY_RELEASE_CHECKS = [
    *ACTIVE_CHECKS,
    *DEFERRED_CHECKS,
    LEGACY_COMPATIBILITY_CHECKS[0],
]


def _check_paths(checks: list[tuple[str, list[str]]]) -> set[str]:
    return {command[0] for _, command in checks}


ACTIVE_CHECK_PATHS = _check_paths(ACTIVE_CHECKS)
DEFERRED_CHECK_PATHS = _check_paths(DEFERRED_CHECKS)
LEGACY_COMPATIBILITY_CHECK_PATHS = _check_paths(LEGACY_COMPATIBILITY_CHECKS)

# Compatibility aliases retained for existing imports during PR #6.
ACTIVE_VALIDATOR_FILENAMES = ACTIVE_CHECK_PATHS
DEFERRED_VALIDATOR_FILENAMES = DEFERRED_CHECK_PATHS
