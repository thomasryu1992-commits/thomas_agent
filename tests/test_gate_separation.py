from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import yaml

from scripts.gate_matrix import (
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


def load_workflow(rel: str) -> dict[str, Any]:
    value = yaml.load((ROOT / rel).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(value, dict)
    return value


class GateSeparationTests(unittest.TestCase):
    def test_scope_check_sets_are_pairwise_disjoint(self):
        self.assertFalse(ACTIVE_CHECK_PATHS.intersection(DEFERRED_CHECK_PATHS))
        self.assertFalse(ACTIVE_CHECK_PATHS.intersection(LEGACY_COMPATIBILITY_CHECK_PATHS))
        self.assertFalse(DEFERRED_CHECK_PATHS.intersection(LEGACY_COMPATIBILITY_CHECK_PATHS))

    def test_gate_definitions_preserve_one_canonical_scope_order(self):
        self.assertEqual(tuple(GATE_DEFINITIONS), GATE_SCOPE_ORDER)
        self.assertEqual(GATE_SCOPE_ORDER, ("active", "deferred", "legacy"))

    def test_gate_definitions_have_unique_ids_and_evidence_files(self):
        gate_ids = [GATE_DEFINITIONS[scope]["gate_id"] for scope in GATE_SCOPE_ORDER]
        evidence_files = [GATE_DEFINITIONS[scope]["evidence_filename"] for scope in GATE_SCOPE_ORDER]
        self.assertEqual(len(gate_ids), len(set(gate_ids)))
        self.assertEqual(len(evidence_files), len(set(evidence_files)))

    def test_active_gate_contains_current_read_only_runtime(self):
        labels = {label for label, _ in ACTIVE_CHECKS}
        self.assertIn("I0.5 Read-only Runtime Kernel", labels)
        self.assertIn("Architecture Slimming", labels)

    def test_deferred_gate_has_one_canonical_harness(self):
        self.assertEqual(
            DEFERRED_CHECKS,
            [("Deferred Architecture", ["scripts/validate_deferred_architecture.py"])],
        )

    def test_execution_validation_audit_active_scope_excludes_execution_preview(self):
        commands = {label: command for label, command in ACTIVE_CHECKS}
        self.assertEqual(
            commands["Execution Validation and Audit Foundation"],
            ["scripts/validate_execution_validation_audit_contracts.py", "--scope", "active"],
        )

    def test_active_gate_excludes_deferred_phases(self):
        labels = {label for label, _ in ACTIVE_CHECKS}
        for label in labels:
            self.assertNotIn("I0.5.5", label)
            self.assertNotIn("Executor Foundation", label)
            self.assertNotIn("Operations Evidence", label)

    def test_repository_release_gate_reuses_scoped_matrix(self):
        self.assertEqual(
            REPOSITORY_RELEASE_CHECKS,
            [*ACTIVE_CHECKS, *DEFERRED_CHECKS, LEGACY_COMPATIBILITY_CHECKS[0]],
        )

    def test_parameterized_legacy_checks_are_not_duplicated_in_release_matrix(self):
        labels = {label for label, _ in REPOSITORY_RELEASE_CHECKS}
        self.assertNotIn("Core Release Reproducibility", labels)
        self.assertNotIn("Core Apply Idempotency", labels)

    def test_ci_scope_path_policy_has_one_owner_per_scope(self):
        self.assertEqual(set(CI_SCOPE_PATH_PATTERNS), {"deferred", "legacy", "full"})
        for patterns in CI_SCOPE_PATH_PATTERNS.values():
            self.assertTrue(patterns)
            self.assertEqual(len(patterns), len(set(patterns)))

    def test_active_only_change_does_not_select_deferred_or_legacy(self):
        self.assertEqual(
            classify_ci_scopes(["runtime/read_only_kernel/preflight.py"]),
            {"active": True, "deferred": False, "legacy": False, "full": False},
        )

    def test_deferred_change_selects_active_and_deferred_only(self):
        self.assertEqual(
            classify_ci_scopes(["deferred/DEFERRED_ARCHITECTURE.yaml"]),
            {"active": True, "deferred": True, "legacy": False, "full": False},
        )

    def test_legacy_change_selects_active_and_legacy_only(self):
        self.assertEqual(
            classify_ci_scopes(["historical/architecture/old.md"]),
            {"active": True, "deferred": False, "legacy": True, "full": False},
        )

    def test_shared_gate_infrastructure_change_selects_all_scopes_and_full(self):
        self.assertEqual(
            classify_ci_scopes(["scripts/gate_matrix.py"]),
            {"active": True, "deferred": True, "legacy": True, "full": True},
        )

    def test_architecture_workflow_routes_each_scope_conditionally(self):
        workflow = load_workflow(".github/workflows/architecture-slimming-gates.yml")
        jobs = workflow["jobs"]
        self.assertEqual(
            set(jobs),
            {"classify-changes", "active-gate", "deferred-gate", "legacy-gate", "full-gate"},
        )
        self.assertEqual(
            jobs["deferred-gate"]["if"],
            "needs.classify-changes.outputs.deferred == 'true'",
        )
        self.assertEqual(
            jobs["legacy-gate"]["if"],
            "needs.classify-changes.outputs.legacy == 'true'",
        )
        self.assertEqual(
            jobs["full-gate"]["if"],
            "needs.classify-changes.outputs.full == 'true'",
        )
        # Scoped gates yield to the full Release Gate (a superset of every scope) at STEP
        # level, so a full-scope change runs each validator once while the jobs still
        # complete for required-check policies.
        for job_name in ("active-gate", "deferred-gate", "legacy-gate"):
            gate_steps = [s for s in jobs[job_name]["steps"]
                          if "run_architecture_gate.py" in str(s.get("run", ""))]
            self.assertEqual(len(gate_steps), 1, job_name)
            self.assertEqual(
                gate_steps[0]["if"],
                "needs.classify-changes.outputs.full != 'true'",
                job_name,
            )

    def test_full_workflow_is_not_a_default_pull_request_gate(self):
        workflow = load_workflow(".github/workflows/thomas-agent-runtime-validation.yml")
        triggers = workflow["on"]
        self.assertNotIn("pull_request", triggers)
        self.assertEqual(set(triggers), {"workflow_dispatch", "schedule", "push"})
        self.assertEqual(triggers["push"]["tags"], ["v*", "release-*"])

    def test_active_slimming_runner_does_not_call_deferred_gate_or_tests(self):
        text = (ROOT / "scripts/run_slimming_gate.py").read_text(encoding="utf-8")
        self.assertNotIn("validate_deferred_architecture.py", text)
        self.assertNotIn("tests.test_deferred_architecture", text)

    def test_active_slimming_validator_does_not_duplicate_deferred_semantics(self):
        text = (ROOT / "scripts/validate_slimming_package.py").read_text(encoding="utf-8")
        self.assertNotIn("def validate_deferred(", text)
        self.assertNotIn("validate_deferred()", text)


if __name__ == "__main__":
    unittest.main()
