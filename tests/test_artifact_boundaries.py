from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.lib.artifact_boundaries import (
    scan_retired_import_consumers,
    validate_architecture_reference_boundaries,
    validate_artifact_boundaries,
)


ROOT = Path(__file__).resolve().parents[1]


class ArtifactBoundaryTests(unittest.TestCase):
    def load_yaml(self, rel: str):
        return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))

    def test_repository_boundaries_are_valid(self):
        self.assertEqual(validate_artifact_boundaries(ROOT), [])

    def test_generated_index_never_grants_authority(self):
        record = self.load_yaml("generated/GENERATED_ARTIFACT_INDEX.yaml")
        self.assertFalse(record["authoritative"])
        self.assertFalse(record["runtime_use_allowed"])
        self.assertFalse(record["rules"]["generated_artifacts_grant_activation"])
        self.assertFalse(record["rules"]["generated_artifacts_grant_permission"])
        self.assertFalse(record["rules"]["generated_artifacts_grant_authority"])

    def test_historical_index_never_grants_authority(self):
        record = self.load_yaml("historical/HISTORICAL_ARTIFACT_INDEX.yaml")
        self.assertFalse(record["authoritative"])
        self.assertFalse(record["runtime_use_allowed"])
        self.assertFalse(record["rules"]["historical_artifacts_are_active_authority"])
        self.assertFalse(record["rules"]["historical_artifacts_participate_in_active_gate"])

    def test_release_manifest_and_snapshot_classification_are_separate(self):
        record = self.load_yaml("historical/HISTORICAL_ARTIFACT_INDEX.yaml")
        release = record["core_release_integrity"]
        self.assertEqual(
            release["immutable_manifests"]["classification"],
            "IMMUTABLE_RELEASE_EVIDENCE",
        )
        self.assertEqual(
            release["copied_source_snapshots"]["classification"],
            "HISTORICAL_REPRODUCIBILITY_SNAPSHOT",
        )
        self.assertFalse(release["immutable_manifests"]["move_or_rewrite_allowed"])
        self.assertFalse(release["copied_source_snapshots"]["move_or_rewrite_allowed"])

    def test_retired_compatibility_has_zero_active_import_consumers(self):
        self.assertEqual(scan_retired_import_consumers(ROOT), [])

    def test_retired_active_paths_are_absent(self):
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

    def test_architecture_reference_lanes_are_explicit_and_consistent(self):
        self.assertEqual(validate_architecture_reference_boundaries(ROOT), [])

        active = (ROOT / "docs/ACTIVE_ARCHITECTURE.md").read_text(encoding="utf-8")
        active_section = active.split("Active authority and execution lane:", 1)[1].split(
            "Inactive candidate lane", 1
        )[0]
        self.assertNotIn("System Constitution", active_section)
        self.assertIn("Governance Policy", active_section)

        constitution = (ROOT / "governance/SYSTEM_CONSTITUTION.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("**Status:** Migration Candidate", constitution)
        self.assertIn("**Authoritative:** No — explicit cutover required", constitution)
        self.assertIn("**Active dependency:** None", constitution)

    def test_architecture_reference_validator_blocks_candidate_in_active_lane(self):
        with tempfile.TemporaryDirectory(prefix="architecture_reference_boundary_") as tmp:
            root = Path(tmp)
            for rel in (
                "docs/ACTIVE_ARCHITECTURE.md",
                "docs/README.md",
                "governance/SYSTEM_CONSTITUTION.md",
            ):
                source = ROOT / rel
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

            active_path = root / "docs/ACTIVE_ARCHITECTURE.md"
            active = active_path.read_text(encoding="utf-8")
            active = active.replace(
                "Thomas Core\n  ↓\nGovernance Policy",
                "Thomas Core\n  ↓\nSystem Constitution\n  ↓\nGovernance Policy",
                1,
            )
            active_path.write_text(active, encoding="utf-8")

            errors = validate_architecture_reference_boundaries(root)
            self.assertTrue(
                any("must not appear in the active authority lane" in item for item in errors),
                errors,
            )

    def test_architecture_reference_validator_blocks_candidate_self_insertion(self):
        with tempfile.TemporaryDirectory(prefix="constitution_reference_boundary_") as tmp:
            root = Path(tmp)
            for rel in (
                "docs/ACTIVE_ARCHITECTURE.md",
                "docs/README.md",
                "governance/SYSTEM_CONSTITUTION.md",
            ):
                source = ROOT / rel
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

            constitution_path = root / "governance/SYSTEM_CONSTITUTION.md"
            constitution = constitution_path.read_text(encoding="utf-8")
            constitution = constitution.replace(
                "Thomas Core\n↓\nGovernance Policy",
                "Thomas Core\n↓\nSystem Constitution\n↓\nGovernance Policy",
                1,
            )
            constitution_path.write_text(constitution, encoding="utf-8")

            errors = validate_architecture_reference_boundaries(root)
            self.assertTrue(
                any("must not insert itself into the current active authority lane" in item for item in errors),
                errors,
            )

    def test_canonical_registry_resolver_is_active(self):
        self.assertTrue((ROOT / "runtime/registry_resolution.py").is_file())
        for rel in (
            "03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml",
            "05_REGISTRIES/PROGRAM_REGISTRY.yaml",
            "05_REGISTRIES/TOOL_REGISTRY.yaml",
        ):
            registry = self.load_yaml(rel)
            self.assertNotIn("compatibility", registry)
            self.assertEqual(
                registry["resolution"]["resolver_module"],
                "runtime/registry_resolution.py",
            )
            self.assertFalse(
                registry["resolution"]["resolved_view_authoritative"]
            )

    def test_retired_import_scanner_detects_legacy_consumer(self):
        with tempfile.TemporaryDirectory(prefix="artifact_boundary_test_") as tmp:
            root = Path(tmp)
            path = root / "scripts/example.py"
            path.parent.mkdir(parents=True)
            path.write_text(
                "from runtime.compat.legacy_registry_projection import project_role_registry\n",
                encoding="utf-8",
            )
            consumers = scan_retired_import_consumers(root)
            self.assertEqual(len(consumers), 1)
            self.assertIn("runtime.compat.legacy_registry_projection", consumers[0])


if __name__ == "__main__":
    unittest.main()
