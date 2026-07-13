"""I0.5.4 protected local governance state candidate.

This package is disabled for Runtime use and supports only explicit
SYNTHETIC_TEST_ONLY writes inside caller-provided temporary state roots.
"""

from .recovery import (
    RECOVERY_COMPONENT_ID,
    RECOVERY_COMPONENT_VERSION,
    inspect_recovery_state,
    validate_recovery_report_semantics,
)
from .sqlite_store import (
    RECORD_SCOPE,
    STORE_COMPONENT_ID,
    STORE_COMPONENT_VERSION,
    STORE_SCHEMA_VERSION,
    TRANSITION_COMPONENT_ID,
    TRANSITION_COMPONENT_VERSION,
    ProtectedGovernanceStateStore,
    ProtectedStateConflict,
    ProtectedStateError,
    SimulatedCrashAfterCommit,
    SimulatedCrashBeforeCommit,
    StoreConfig,
    validate_durable_transition_result_semantics,
)

__all__ = [
    "RECOVERY_COMPONENT_ID",
    "RECOVERY_COMPONENT_VERSION",
    "RECORD_SCOPE",
    "STORE_COMPONENT_ID",
    "STORE_COMPONENT_VERSION",
    "STORE_SCHEMA_VERSION",
    "TRANSITION_COMPONENT_ID",
    "TRANSITION_COMPONENT_VERSION",
    "ProtectedGovernanceStateStore",
    "ProtectedStateConflict",
    "ProtectedStateError",
    "SimulatedCrashAfterCommit",
    "SimulatedCrashBeforeCommit",
    "StoreConfig",
    "inspect_recovery_state",
    "validate_durable_transition_result_semantics",
    "validate_recovery_report_semantics",
]
