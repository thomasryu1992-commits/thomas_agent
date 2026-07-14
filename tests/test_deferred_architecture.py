from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from scripts.gate_matrix import ACTIVE_CHECKS, DEFERRED_CHECKS

ROOT = Path(__file__).resolve().parents[1]


class DeferredArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = yaml.safe_load((ROOT / "deferred/DEFERRED_ARCHITECTURE.yaml").read_text(encoding="utf-8"))

    def test_one_deferred_authority_is_non_runtime_authoritative(self):
        self.assertTrue(self.manifest["authoritative_for_deferred_design"])
        self.assertFalse(self.manifest["runtime_authoritative"])
        self.assertEqual(self.manifest["status"], "DEFERRED_REVIEW_ONLY")

    def test_global_effect_and_activation_grants_remain_false(self):
        constraints = self.manifest["global_constraints"]
        for key, value in constraints.items():
            if key in {"explicit_separate_activation_required", "fail_closed_on_missing_or_ambiguous_authority"}:
                self.assertTrue(value, key)
            else:
                self.assertFalse(value, key)

    def test_five_families_are_ordered_and_disabled(self):
        expected = ["runtime_entry", "executor", "operations", "control_channel", "sandbox"]
        self.assertEqual(self.manifest["family_order"], expected)
        self.assertEqual(list(self.manifest["families"]), expected)
        for family in self.manifest["families"].values():
            self.assertEqual(family["status"], "DEFERRED_DISABLED")
            self.assertTrue(all(value is False for value in family["family_constraints"].values()))

    def test_runtime_entry_consolidates_i0_5_1_through_i0_5_5(self):
        self.assertEqual(
            self.manifest["families"]["runtime_entry"]["phase_aliases"],
            ["I0.5.1", "I0.5.2", "I0.5.3", "I0.5.4", "I0.5.5"],
        )

    def test_contract_and_schema_ownership_is_unique(self):
        contracts = []
        schemas = []
        for family in self.manifest["families"].values():
            for item in family["contracts"]:
                contracts.append(item["contract_ref"])
                schemas.append(item["schema_ref"])
        self.assertEqual(len(contracts), len(set(contracts)))
        self.assertEqual(len(schemas), len(set(schemas)))

    def test_boundaries_exist_and_deny_activation_authority(self):
        for family in self.manifest["families"].values():
            text = (ROOT / family["boundary_ref"]).read_text(encoding="utf-8")
            self.assertIn("Canonical deferred authority", text)
            self.assertIn("No activation authority", text)

    def test_deferred_gate_has_one_harness(self):
        self.assertEqual(
            DEFERRED_CHECKS,
            [("Deferred Architecture", ["scripts/validate_deferred_architecture.py"])],
        )

    def test_active_execution_validator_is_scoped_to_validation_and_audit(self):
        commands = {label: command for label, command in ACTIVE_CHECKS}
        self.assertEqual(
            commands["Execution Validation and Audit Foundation"],
            ["scripts/validate_execution_validation_audit_contracts.py", "--scope", "active"],
        )


if __name__ == "__main__":
    unittest.main()
