from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Iterable


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

# The repository-wide Release Gate remains the comprehensive integration and
# release path. It is intentionally not the default check for every pull request.
REPOSITORY_RELEASE_CHECKS = [
    *ACTIVE_CHECKS,
    *DEFERRED_CHECKS,
    LEGACY_COMPATIBILITY_CHECKS[0],
]

# CI path classification is routing metadata only. It selects which existing
# canonical Gate should run; it does not create a Gate or grant authority.
CI_SCOPE_PATH_PATTERNS = {
    "deferred": (
        "deferred/**",
        "generated/deferred/**",
        "runtime/read_only_entry/**",
        "runtime/protected_governance_state/**",
        "05_REGISTRIES/*REVIEW_ONLY.yaml",
        "docs/runtime-contracts/*RUNTIME_ENTRY*",
        "docs/runtime-contracts/*RUNTIME_AUTHORITATIVE*",
        "docs/runtime-contracts/*RUNTIME_PROMOTION*",
        "docs/runtime-contracts/*PROTECTED*GOVERNANCE*",
        "docs/runtime-contracts/*SINGLE_READ_ONLY_ENTRY*",
        "docs/runtime-contracts/*EXECUTOR*",
        "docs/runtime-contracts/*EXECUTION_REQUEST*",
        "docs/runtime-contracts/*EXECUTION_RESULT*",
        "docs/runtime-contracts/*PRE_EXECUTION*",
        "docs/runtime-contracts/*APPROVAL_CONSUMPTION*",
        "docs/runtime-contracts/*ROLLBACK_RECOVERY*",
        "docs/runtime-contracts/*MONITORING*",
        "docs/runtime-contracts/*ALERT_EVENT*",
        "docs/runtime-contracts/*HEALTH_SNAPSHOT*",
        "docs/runtime-contracts/*CLOCK_SYNC*",
        "docs/runtime-contracts/*PROCESS_SUPERVISOR*",
        "docs/runtime-contracts/*SCHEDULER*",
        "docs/runtime-contracts/*CONTROL_CHANNEL*",
        "docs/runtime-contracts/*KILL_SWITCH*",
        "docs/runtime-contracts/*SANDBOX*",
        "schemas/runtime_entry*",
        "schemas/runtime_authoritative*",
        "schemas/runtime_promotion*",
        "schemas/protected_governance*",
        "schemas/disabled_single_read_only_entry*",
        "schemas/executor*",
        "schemas/disabled_executor*",
        "schemas/execution_request*",
        "schemas/execution_result*",
        "schemas/pre_execution*",
        "schemas/approval_consumption*",
        "schemas/rollback_recovery*",
        "schemas/monitoring*",
        "schemas/alert_event*",
        "schemas/health_snapshot*",
        "schemas/clock_sync*",
        "schemas/process_supervisor*",
        "schemas/scheduler*",
        "schemas/control_channel*",
        "schemas/kill_switch*",
        "schemas/local_reversible_sandbox*",
        "examples/runtime_authoritative_read_only_entry/**",
        "examples/executor*/**",
        "examples/execution_requests/**",
        "examples/execution_results/**",
        "examples/operations_evidence/**",
        "examples/control_supervision/**",
        "examples/sandbox_candidate/**",
        "examples/threshold_policy/**",
        "scripts/validate_deferred_architecture.py",
        "scripts/lib/deferred_validation.py",
        "scripts/*runtime_promotion*",
        "scripts/*runtime_authoritative*",
        "scripts/*runtime_entry*",
        "scripts/*protected_governance*",
        "scripts/*executor*",
        "scripts/*execution_request*",
        "scripts/*execution_result*",
        "scripts/*pre_execution*",
        "scripts/*approval_consumption*",
        "scripts/*rollback_recovery*",
        "scripts/*operations_evidence*",
        "scripts/*monitoring*",
        "scripts/*control_supervision*",
        "scripts/*sandbox*",
        "tests/test_deferred_architecture.py",
        "tests/fixtures/runtime_promotion/**",
        "tests/fixtures/executor*/**",
        "tests/fixtures/execution_requests/**",
        "tests/fixtures/execution_results/**",
        "tests/fixtures/operations_evidence/**",
        "tests/fixtures/control_supervision/**",
        "tests/fixtures/sandbox*/**",
    ),
    "legacy": (
        "historical/**",
        "generated/legacy/**",
        "THOMAS_CORE/releases/**",
        "THOMAS_CORE/CORE_RELEASE_MANIFEST_TEMPLATE.yaml",
        "THOMAS_CORE/CURRENT_CORE_RELEASE.yaml",
        "THOMAS_CORE/approvals/**",
        "THOMAS_CORE/activations/**",
        "THOMAS_CORE/deactivations/**",
        "THOMAS_CORE/revocations/**",
        "05_REGISTRIES/I0_4_RUNTIME_CONTRACT_SET_INDEX.yaml",
        "docs/runtime-contracts/I0_4_*",
        "schemas/core_activation*",
        "schemas/core_deactivation*",
        "schemas/core_revocation*",
        "schemas/current_core_release*",
        "schemas/thomas_core_release*",
        "scripts/*i0_4*",
        "scripts/validate_core_release_reproducibility.py",
        "scripts/test_apply_core_idempotency.py",
        "scripts/lib/core_release_verifier.py",
        "tests/fixtures/core_release/**",
    ),
    "full": (
        ".github/workflows/**",
        "requirements-validation.in",
        "requirements-validation.lock",
        "scripts/gate_matrix.py",
        "scripts/classify_ci_scope_changes.py",
        "scripts/run_architecture_gate.py",
        "scripts/run_active_gate.py",
        "scripts/run_deferred_architecture_gate.py",
        "scripts/run_legacy_compatibility_gate.py",
        "scripts/run_split_repository_gate.py",
        "scripts/run_repository_release_gate.py",
        "scripts/run_slimming_gate.py",
        "scripts/validate_gate_separation.py",
        "scripts/lib/release_gate_evidence.py",
        "tests/test_gate_separation.py",
    ),
}


def _check_paths(checks: list[tuple[str, list[str]]]) -> set[str]:
    return {command[0] for _, command in checks}


def _normalize_ci_path(path: str) -> str:
    return path.strip().replace("\\", "/").lstrip("./")


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def classify_ci_scopes(paths: Iterable[str]) -> dict[str, bool]:
    """Classify changed paths without changing Gate composition or authority."""
    normalized = {_normalize_ci_path(path) for path in paths if path and path.strip()}
    result = {
        "active": True,
        "deferred": any(_matches_any(path, CI_SCOPE_PATH_PATTERNS["deferred"]) for path in normalized),
        "legacy": any(_matches_any(path, CI_SCOPE_PATH_PATTERNS["legacy"]) for path in normalized),
        "full": any(_matches_any(path, CI_SCOPE_PATH_PATTERNS["full"]) for path in normalized),
    }
    if result["full"]:
        # Shared Gate/CI infrastructure changes require every scoped Gate plus the
        # repository-wide integration Gate.
        result["deferred"] = True
        result["legacy"] = True
    return result


ACTIVE_CHECK_PATHS = _check_paths(ACTIVE_CHECKS)
DEFERRED_CHECK_PATHS = _check_paths(DEFERRED_CHECKS)
LEGACY_COMPATIBILITY_CHECK_PATHS = _check_paths(LEGACY_COMPATIBILITY_CHECKS)

# Compatibility aliases retained for existing imports during PR #6.
ACTIVE_VALIDATOR_FILENAMES = ACTIVE_CHECK_PATHS
DEFERRED_VALIDATOR_FILENAMES = DEFERRED_CHECK_PATHS
