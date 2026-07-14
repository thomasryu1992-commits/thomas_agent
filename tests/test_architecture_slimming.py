from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from runtime.compat.legacy_registry_projection import (
    ProjectionError,
    project_resource_registry,
)
from runtime.kernel_slim import run_read_only_kernel


REPO_ROOT = Path(__file__).resolve().parents[1]


class ArchitectureSlimmingTests(unittest.TestCase):
    def load_yaml(self, rel: str):
        return yaml.safe_load((REPO_ROOT / rel).read_text(encoding="utf-8"))

    def test_governance_remains_review_only(self):
        policy = self.load_yaml("governance/GOVERNANCE_POLICY.yaml")
        effect = policy["runtime_effect"]
        self.assertFalse(effect["grants_runtime_execution"])
        self.assertFalse(effect["grants_tool_or_program_enablement"])
        self.assertFalse(effect["grants_external_execution"])
        self.assertFalse(effect["grants_financial_execution"])
        self.assertFalse(effect["grants_permission_expansion"])

    def test_slim_role_registry_has_no_duplicate_fields(self):
        registry = self.load_yaml(
            "03_ROLE_CONTRACTS/ROLE_REGISTRY_SLIM_CANDIDATE.yaml"
        )
        prohibited = {
            "capabilities",
            "capability_set_sha256",
            "permission_ceiling",
            "restrictions",
            "validation_default",
            "promotion_requirements",
            "selection_policy",
        }
        for role in registry["roles"]:
            self.assertFalse(prohibited.intersection(role))

    def test_program_projection_is_non_authoritative(self):
        registry = self.load_yaml(
            "05_REGISTRIES/PROGRAM_REGISTRY_SLIM_CANDIDATE.yaml"
        )
        policy = self.load_yaml("governance/GOVERNANCE_POLICY.yaml")
        definitions = {
            item["definition_path"]: self.load_yaml(item["definition_path"])
            for item in registry["programs"]
        }
        projected = project_resource_registry(
            repo_root=REPO_ROOT,
            slim_registry=registry,
            definitions=definitions,
            governance_policy=policy,
            collection_key="programs",
            id_key="program_id",
        )
        self.assertFalse(projected["_projection"]["authoritative"])
        self.assertFalse(projected["_projection"]["may_expand_authority"])

    def test_projection_blocks_hash_mismatch(self):
        registry = self.load_yaml(
            "05_REGISTRIES/PROGRAM_REGISTRY_SLIM_CANDIDATE.yaml"
        )
        policy = self.load_yaml("governance/GOVERNANCE_POLICY.yaml")
        definitions = {
            item["definition_path"]: self.load_yaml(item["definition_path"])
            for item in registry["programs"]
        }
        broken = json.loads(json.dumps(registry))
        broken["programs"][0]["definition_sha256"] = "0" * 64
        with self.assertRaises(ProjectionError):
            project_resource_registry(
                repo_root=REPO_ROOT,
                slim_registry=broken,
                definitions=definitions,
                governance_policy=policy,
                collection_key="programs",
                id_key="program_id",
            )

    def test_valid_read_only_replay_candidate(self):
        policy = self.load_yaml("governance/GOVERNANCE_POLICY.yaml")
        bundle = {
            "task": {
                "identity": {"task_id": "task-test"},
                "permission": {"decision": "ALLOW"},
            },
            "core_context_binding": {"id": "core-bind"},
            "role_assignment": {
                "assignment_id": "assign-test",
                "actor_instance_id": "actor-test",
                "role_id": "general.specialist",
                "role_version": "0.3.0",
            },
            "role_registry": {},
            "program_registry": {},
            "tool_registry": {},
            "requested_effects": {},
        }

        def worker(*, task, assignment, created_at):
            return {
                "task_id": task["identity"]["task_id"],
                "status": "needs_validation",
                "created_at": created_at,
            }

        result = run_read_only_kernel(
            repo_root=REPO_ROOT,
            input_bundle=bundle,
            governance_policy=policy,
            created_at="2026-07-14T00:00:00Z",
            worker=worker,
        )
        self.assertEqual(result["status"], "REPLAY_COMPLETED")
        self.assertFalse(result["runtime_authoritative"])
        self.assertFalse(result["external_effect_performed"])

    def test_effect_request_blocks(self):
        policy = self.load_yaml("governance/GOVERNANCE_POLICY.yaml")
        bundle = {
            "task": {
                "identity": {"task_id": "task-block"},
                "permission": {"decision": "ALLOW"},
            },
            "core_context_binding": {},
            "role_assignment": {
                "assignment_id": "assign",
                "actor_instance_id": "actor",
                "role_id": "general.specialist",
                "role_version": "0.3.0",
            },
            "role_registry": {},
            "program_registry": {},
            "tool_registry": {},
            "requested_effects": {"external_action": True},
        }
        result = run_read_only_kernel(
            repo_root=REPO_ROOT,
            input_bundle=bundle,
            governance_policy=policy,
            created_at="2026-07-14T00:00:00Z",
            worker=lambda **_: {},
        )
        self.assertEqual(result["status"], "REPLAY_BLOCKED")
        self.assertIn("NON_READ_ONLY_EFFECT_REQUESTED", result["blockers"])


if __name__ == "__main__":
    unittest.main()
