from __future__ import annotations

import unittest

from scripts.gate_matrix import (
    ACTIVE_CHECKS,
    ACTIVE_VALIDATOR_FILENAMES,
    DEFERRED_CHECKS,
    DEFERRED_VALIDATOR_FILENAMES,
)


class GateSeparationTests(unittest.TestCase):
    def test_active_and_deferred_validator_sets_are_disjoint(self):
        self.assertFalse(
            ACTIVE_VALIDATOR_FILENAMES.intersection(
                DEFERRED_VALIDATOR_FILENAMES
            )
        )

    def test_active_gate_contains_current_read_only_runtime(self):
        labels = {label for label, _ in ACTIVE_CHECKS}
        self.assertIn("I0.5 Read-only Runtime Kernel", labels)

    def test_deferred_gate_contains_entry_and_executor_design(self):
        labels = {label for label, _ in DEFERRED_CHECKS}
        self.assertIn("Executor Foundation Review-Only", labels)
        self.assertIn(
            "I0.5.5 Disabled Single Read-only Entry Integration Candidate",
            labels,
        )

    def test_active_gate_excludes_deferred_phases(self):
        labels = {label for label, _ in ACTIVE_CHECKS}
        for label in labels:
            self.assertNotIn("I0.5.5", label)
            self.assertNotIn("Executor Foundation", label)
            self.assertNotIn("Operations Evidence", label)


if __name__ == "__main__":
    unittest.main()
