from __future__ import annotations

from itertools import combinations
from pathlib import Path

from gate_matrix import (
    ACTIVE_CHECKS,
    ACTIVE_CHECK_PATHS,
    DEFERRED_CHECKS,
    DEFERRED_CHECK_PATHS,
    GATE_DEFINITIONS,
    GATE_SCOPE_ORDER,
    LEGACY_COMPATIBILITY_CHECKS,
    LEGACY_COMPATIBILITY_CHECK_PATHS,
    REPOSITORY_RELEASE_CHECKS,
)

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def main() -> int:
    scope_paths = {
        "active": ACTIVE_CHECK_PATHS,
        "deferred": DEFERRED_CHECK_PATHS,
        "legacy": LEGACY_COMPATIBILITY_CHECK_PATHS,
    }
    for left, right in combinations(GATE_SCOPE_ORDER, 2):
        overlap = scope_paths[left].intersection(scope_paths[right])
        if overlap:
            fail(f"{left} and {right} Gate checks overlap: " + ", ".join(sorted(overlap)))

    if tuple(GATE_DEFINITIONS) != GATE_SCOPE_ORDER:
        fail("Gate definitions must preserve canonical active/deferred/legacy order")

    gate_ids = [str(GATE_DEFINITIONS[scope]["gate_id"]) for scope in GATE_SCOPE_ORDER]
    evidence_filenames = [str(GATE_DEFINITIONS[scope]["evidence_filename"]) for scope in GATE_SCOPE_ORDER]
    if len(gate_ids) != len(set(gate_ids)):
        fail("Gate IDs must be unique")
    if len(evidence_filenames) != len(set(evidence_filenames)):
        fail("Gate evidence filenames must be unique")

    all_checks = [*ACTIVE_CHECKS, *DEFERRED_CHECKS, *LEGACY_COMPATIBILITY_CHECKS]
    missing = [command[0] for _, command in all_checks if not (ROOT / command[0]).exists()]
    for rel in (
        "scripts/run_architecture_gate.py",
        "scripts/run_active_gate.py",
        "scripts/run_deferred_architecture_gate.py",
        "scripts/run_legacy_compatibility_gate.py",
        "scripts/run_split_repository_gate.py",
    ):
        if not (ROOT / rel).exists():
            missing.append(rel)
    if missing:
        fail("Gate matrix or entrypoint references missing files: " + ", ".join(sorted(set(missing))))

    expected_repository_checks = [*ACTIVE_CHECKS, *DEFERRED_CHECKS, LEGACY_COMPATIBILITY_CHECKS[0]]
    if REPOSITORY_RELEASE_CHECKS != expected_repository_checks:
        fail("Repository Release Gate must reuse the canonical scoped matrices")

    repository_labels = {label for label, _ in REPOSITORY_RELEASE_CHECKS}
    for label in ("Core Release Reproducibility", "Core Apply Idempotency"):
        if label in repository_labels:
            fail(f"Parameterized legacy check must remain specialized: {label}")

    if DEFERRED_CHECKS != [("Deferred Architecture", ["scripts/validate_deferred_architecture.py"])]:
        fail("Deferred Gate must have one canonical harness and no phase-specific Gate entries")

    active_by_label = {label: command for label, command in ACTIVE_CHECKS}
    if active_by_label.get("Execution Validation and Audit Foundation") != [
        "scripts/validate_execution_validation_audit_contracts.py", "--scope", "active"
    ]:
        fail("Active Gate must exclude deferred Execution Request/Result validation")

    prohibited_active_fragments = (
        "Executor Foundation", "Operations Evidence", "Control, Supervision",
        "Runtime Promotion Readiness", "Runtime-Authoritative Read-only Entry",
        "At-Most-Once", "Protected Local Governance State", "Disabled Single Read-only Entry",
    )
    for label, _ in ACTIVE_CHECKS:
        if any(fragment in label for fragment in prohibited_active_fragments):
            fail(f"Deferred scope leaked into Active Gate: {label}")

    print("THOMAS_AGENT_GATE_SEPARATION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
