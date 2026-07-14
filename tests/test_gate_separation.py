from __future__ import annotations

import unittest

from scripts.gate_matrix import (
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


if __name__ == "__main__":
    unittest.main()
