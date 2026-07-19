from __future__ import annotations

from itertools import combinations
from pathlib import Path
from typing import Any

import yaml

from gate_matrix import (
    ACTIVE_CHECKS,
    ACTIVE_CHECK_PATHS,
    CI_SCOPE_PATH_PATTERNS,
    DEFERRED_CHECKS,
    DEFERRED_CHECK_PATHS,
    GATE_DEFINITIONS,
    GATE_SCOPE_ORDER,
    LEGACY_COMPATIBILITY_CHECKS,
    LEGACY_COMPATIBILITY_CHECK_PATHS,
    REPOSITORY_RELEASE_CHECKS,
    classify_ci_scopes,
)

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_WORKFLOW = ".github/workflows/architecture-slimming-gates.yml"
FULL_WORKFLOW = ".github/workflows/thomas-agent-runtime-validation.yml"


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def load_workflow(rel: str) -> dict[str, Any]:
    value = yaml.load((ROOT / rel).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    if not isinstance(value, dict):
        fail(f"{rel}: workflow must decode to a mapping")
    return value


def collect_run_commands(value: Any) -> list[str]:
    commands: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "run" and isinstance(child, str):
                commands.append(" ".join(child.split()))
            commands.extend(collect_run_commands(child))
    elif isinstance(value, list):
        for child in value:
            commands.extend(collect_run_commands(child))
    return commands


def validate_workflow_scope_policy() -> None:
    active = load_workflow(ACTIVE_WORKFLOW)
    triggers = active.get("on")
    if not isinstance(triggers, dict) or set(triggers) != {
        "pull_request", "push", "workflow_dispatch"
    }:
        fail("Architecture scope workflow must run on pull_request, main push, and workflow_dispatch")
    push = triggers.get("push")
    if not isinstance(push, dict) or push.get("branches") != ["main"]:
        fail("Architecture scope workflow push trigger must be limited to main")

    jobs = active.get("jobs")
    required_jobs = {
        "classify-changes", "active-gate", "deferred-gate", "legacy-gate", "full-gate"
    }
    if not isinstance(jobs, dict) or set(jobs) != required_jobs:
        fail("Architecture scope workflow must contain exactly the classifier and four canonical Gate jobs")
    # Scoped gates yield to the full Release Gate: its check list is a superset of every
    # scope, so running both per pipeline executed each validator twice for no coverage.
    if jobs["active-gate"].get("if") != "needs.classify-changes.outputs.full != 'true'":
        fail("Active Gate job must yield to the full Release Gate (which reruns every active check)")
    if jobs["deferred-gate"].get("if") != (
            "needs.classify-changes.outputs.deferred == 'true' && needs.classify-changes.outputs.full != 'true'"):
        fail("Deferred Gate job must use the canonical deferred classifier output and yield to the full Gate")
    if jobs["legacy-gate"].get("if") != (
            "needs.classify-changes.outputs.legacy == 'true' && needs.classify-changes.outputs.full != 'true'"):
        fail("Legacy Gate job must use the canonical legacy classifier output and yield to the full Gate")
    if jobs["full-gate"].get("if") != "needs.classify-changes.outputs.full == 'true'":
        fail("Full Gate job must use the canonical full classifier output")

    commands = collect_run_commands(active)
    required_commands = (
        "python scripts/classify_ci_scope_changes.py",
        "python scripts/run_architecture_gate.py --scope active --check-only",
        "python scripts/run_architecture_gate.py --scope deferred --check-only",
        "python scripts/run_architecture_gate.py --scope legacy --check-only",
        "python scripts/run_repository_release_gate.py --full --check-only",
    )
    for token in required_commands:
        if not any(token in command for command in commands):
            fail(f"Architecture scope workflow missing command: {token}")

    full = load_workflow(FULL_WORKFLOW)
    full_triggers = full.get("on")
    if not isinstance(full_triggers, dict) or set(full_triggers) != {
        "workflow_dispatch", "schedule", "push"
    }:
        fail("Full Repository workflow must be manual, nightly, and release-tag triggered only")
    if "pull_request" in full_triggers:
        fail("Full Repository workflow must not run for every pull request")
    full_push = full_triggers.get("push")
    if not isinstance(full_push, dict) or full_push.get("tags") != ["v*", "release-*"]:
        fail("Full Repository workflow push trigger must be limited to release tags")
    schedules = full_triggers.get("schedule")
    if not isinstance(schedules, list) or not schedules or not schedules[0].get("cron"):
        fail("Full Repository workflow must retain a nightly schedule")
    full_commands = collect_run_commands(full)
    expected = "python scripts/run_repository_release_gate.py --full --check-only"
    if expected not in full_commands:
        fail("Full Repository workflow must run the canonical full Release Gate")


def validate_classifier_policy() -> None:
    if set(CI_SCOPE_PATH_PATTERNS) != {"deferred", "legacy", "full"}:
        fail("CI scope path policy must define deferred, legacy, and full only")
    for scope, patterns in CI_SCOPE_PATH_PATTERNS.items():
        if not patterns or len(patterns) != len(set(patterns)):
            fail(f"CI scope {scope} patterns must be non-empty and unique")

    cases = (
        (["runtime/read_only_kernel/preflight.py"], {"active": True, "deferred": False, "legacy": False, "full": False}),
        (["deferred/DEFERRED_ARCHITECTURE.yaml"], {"active": True, "deferred": True, "legacy": False, "full": False}),
        (["historical/architecture/old.md"], {"active": True, "deferred": False, "legacy": True, "full": False}),
        (["scripts/gate_matrix.py"], {"active": True, "deferred": True, "legacy": True, "full": True}),
    )
    for paths, expected in cases:
        actual = classify_ci_scopes(paths)
        if actual != expected:
            fail(f"CI scope classification mismatch for {paths}: {actual} != {expected}")


def validate_active_runner_does_not_execute_deferred_gate() -> None:
    text = (ROOT / "scripts/run_slimming_gate.py").read_text(encoding="utf-8")
    for forbidden in (
        "validate_deferred_architecture.py",
        "tests.test_deferred_architecture",
        "Deferred Architecture Structure",
    ):
        if forbidden in text:
            fail(f"Active Slimming runner must not execute Deferred ownership: {forbidden}")

    slimming = (ROOT / "scripts/validate_slimming_package.py").read_text(encoding="utf-8")
    if "def validate_deferred(" in slimming or "validate_deferred()" in slimming:
        fail("Active Slimming validator must not duplicate Deferred manifest semantics")


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
        "scripts/classify_ci_scope_changes.py",
        ACTIVE_WORKFLOW,
        FULL_WORKFLOW,
    ):
        if not (ROOT / rel).exists():
            missing.append(rel)
    if missing:
        fail("Gate matrix, CI classifier, workflow, or entrypoint references missing files: " + ", ".join(sorted(set(missing))))

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

    validate_classifier_policy()
    validate_workflow_scope_policy()
    validate_active_runner_does_not_execute_deferred_gate()

    print("THOMAS_AGENT_GATE_AND_CI_SCOPE_SEPARATION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
