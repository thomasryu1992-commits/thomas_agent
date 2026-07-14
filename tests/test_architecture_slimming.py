from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from runtime.registry_resolution import (
    RegistryResolutionError,
    load_resource_definitions,
    resolve_resource_registry,
    resolve_role_registry,
)


ROOT = Path(__file__).resolve().parents[1]


class ArchitectureSlimmingTests(unittest.TestCase):
    def load_yaml(self, rel: str):
        return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))

    def test_governance_is_single_active_policy_source_and_review_only(self):
        policy = self.load_yaml("governance/GOVERNANCE_POLICY.yaml")
        self.assertTrue(policy["authoritative"])
        self.assertEqual(policy["status"], "ACTIVE_POLICY_SOURCE")
        self.assertEqual(policy["policy_id"], "thomas.governance.policy")
        effect = policy["runtime_effect"]
        for field in (
            "grants_runtime_execution",
            "grants_tool_or_program_enablement",
            "grants_external_execution",
            "grants_financial_execution",
            "grants_permission_expansion",
            "executor_handoff_allowed",
            "approval_consumption_allowed",
            "core_activation_allowed",
        ):
            self.assertFalse(effect[field], field)

    def test_active_registries_are_index_only(self):
        role_registry = self.load_yaml("03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml")
        self.assertTrue(role_registry["authoritative"])
        self.assertNotIn("compatibility", role_registry)
        for role in role_registry["roles"]:
            for field in (
                "capabilities", "permission_ceiling", "restrictions",
                "validation_default", "promotion_requirements",
            ):
                self.assertNotIn(field, role)

        for rel, collection in (
            ("05_REGISTRIES/PROGRAM_REGISTRY.yaml", "programs"),
            ("05_REGISTRIES/TOOL_REGISTRY.yaml", "tools"),
        ):
            registry = self.load_yaml(rel)
            self.assertTrue(registry["authoritative"])
            self.assertNotIn("compatibility", registry)
            for item in registry[collection]:
                self.assertNotIn("purpose", item)
                self.assertNotIn("required_permission_level", item)
                self.assertFalse(item["enabled"])
                self.assertFalse(item["runtime_implementation_available"])

    def test_role_resolution_uses_definition_and_remains_non_authoritative(self):
        registry = self.load_yaml("03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml")
        policy = self.load_yaml("governance/GOVERNANCE_POLICY.yaml")
        resolved = resolve_role_registry(
            repo_root=ROOT,
            registry=registry,
            governance_policy=policy,
        )
        self.assertFalse(resolved["_resolution"]["authoritative"])
        by_id = {item["role_id"]: item for item in resolved["roles"]}
        self.assertIn("research", by_id["general.specialist"]["capabilities"])
        self.assertEqual(by_id["general.specialist"]["role_version"] if "role_version" in by_id["general.specialist"] else by_id["general.specialist"]["version"], "0.3.0")

    def test_role_resolution_blocks_hash_mismatch(self):
        registry = self.load_yaml("03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml")
        broken = json.loads(json.dumps(registry))
        broken["roles"][0]["definition_sha256"] = "0" * 64
        with self.assertRaises(RegistryResolutionError):
            resolve_role_registry(
                repo_root=ROOT,
                registry=broken,
                governance_policy=self.load_yaml("governance/GOVERNANCE_POLICY.yaml"),
            )

    def test_program_and_tool_resolution_uses_definitions(self):
        policy = self.load_yaml("governance/GOVERNANCE_POLICY.yaml")
        cases = (
            ("05_REGISTRIES/PROGRAM_REGISTRY.yaml", "programs", "program_id"),
            ("05_REGISTRIES/TOOL_REGISTRY.yaml", "tools", "tool_id"),
        )
        for rel, collection, id_key in cases:
            with self.subTest(rel=rel):
                registry = self.load_yaml(rel)
                definitions = load_resource_definitions(
                    repo_root=ROOT,
                    registry=registry,
                    collection_key=collection,
                )
                resolved = resolve_resource_registry(
                    repo_root=ROOT,
                    registry=registry,
                    definitions=definitions,
                    governance_policy=policy,
                    collection_key=collection,
                    id_key=id_key,
                )
                self.assertFalse(resolved["_resolution"]["authoritative"])
                self.assertFalse(resolved["_resolution"]["may_expand_authority"])
                self.assertTrue(resolved[collection])
                self.assertIn("required_permission_level", resolved[collection][0])

    def test_parallel_candidates_and_legacy_shims_are_not_active(self):
        for rel in (
            "runtime/compat",
            "runtime/kernel_slim",
            "runtime/read_only_kernel/slim_candidate.py",
            "03_ROLE_CONTRACTS/ROLE_REGISTRY_SLIM_CANDIDATE.yaml",
            "05_REGISTRIES/PROGRAM_REGISTRY_SLIM_CANDIDATE.yaml",
            "05_REGISTRIES/TOOL_REGISTRY_SLIM_CANDIDATE.yaml",
        ):
            with self.subTest(rel=rel):
                self.assertFalse((ROOT / rel).exists())
        self.assertTrue((ROOT / "runtime/registry_resolution.py").is_file())
        self.assertTrue((ROOT / "historical/compatibility").is_dir())

    def test_deferred_generated_and_historical_are_not_runtime_authority(self):
        deferred = self.load_yaml("deferred/DEFERRED_ARCHITECTURE.yaml")
        generated = self.load_yaml("generated/GENERATED_ARTIFACT_INDEX.yaml")
        historical = self.load_yaml("historical/HISTORICAL_ARTIFACT_INDEX.yaml")
        self.assertFalse(deferred["runtime_authoritative"])
        self.assertFalse(generated["authoritative"])
        self.assertFalse(generated["runtime_use_allowed"])
        self.assertFalse(historical["authoritative"])
        self.assertFalse(historical["runtime_use_allowed"])


if __name__ == "__main__":
    unittest.main()
