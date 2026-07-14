#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.read_only_kernel.integrity import sha256_file, sha256_value
from runtime.read_only_kernel.io import resolve_read_only_path

REF_ARGUMENTS = {
    "task": "task",
    "core_context_binding": "core-context-binding",
    "role_assignment": "role-assignment",
    "role_definition": "role-definition",
    "role_registry": "role-registry",
    "tool_registry": "tool-registry",
    "program_registry": "program-registry",
    "governance_policy": "governance-policy",
    "i0_4_contract_set_index": "i0-4-contract-set-index",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an I0.5 read-only development replay input bundle and print it to stdout."
    )
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--created-at", default=None)
    for key, cli_name in REF_ARGUMENTS.items():
        parser.add_argument(f"--{cli_name}", dest=key, required=True)
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve(strict=True)
    refs: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for key in REF_ARGUMENTS:
        ref = getattr(args, key)
        path = resolve_read_only_path(repo_root, ref)
        refs[key] = path.relative_to(repo_root).as_posix()
        hashes[key] = sha256_file(path)

    governance_path = resolve_read_only_path(repo_root, refs["governance_policy"])
    governance_policy = yaml.safe_load(governance_path.read_text(encoding="utf-8"))
    if not isinstance(governance_policy, dict):
        raise ValueError("Governance Policy must decode to an object")
    policy_id = governance_policy.get("policy_id")
    policy_version = governance_policy.get("policy_version")
    if not isinstance(policy_id, str) or not policy_id:
        raise ValueError("Governance Policy policy_id must be a non-empty string")
    if not isinstance(policy_version, str) or not policy_version:
        raise ValueError("Governance Policy policy_version must be a non-empty string")
    governance_binding = {
        "policy_id": policy_id,
        "policy_version": policy_version,
        "policy_ref": refs["governance_policy"],
        "policy_sha256": hashes["governance_policy"],
    }

    constraints = {
        "filesystem_read_only": True,
        "external_network_allowed": False,
        "tool_execution_allowed": False,
        "program_execution_allowed": False,
        "model_invocation_allowed": False,
        "external_action_allowed": False,
        "runtime_mutation_allowed": False,
        "filesystem_write_allowed": False,
        "secrets_allowed": False,
    }
    created_at = args.created_at or utc_now()
    payload = {
        "schema_version": "read_only_runtime_input_bundle_fingerprint_payload.v0.1",
        "bundle_id": args.bundle_id,
        "run_mode": "DEVELOPMENT_REPLAY",
        "refs": refs,
        "sha256": hashes,
        "governance_binding": governance_binding,
        "constraints": constraints,
        "created_at": created_at,
    }
    bundle = {
        "schema_version": "read_only_runtime_input_bundle.v0.1",
        "bundle_id": args.bundle_id,
        "run_mode": "DEVELOPMENT_REPLAY",
        "refs": refs,
        "sha256": hashes,
        "governance_binding": governance_binding,
        "constraints": constraints,
        "integrity": {
            "hash_schema": "read_only_runtime_input_bundle_fingerprint_payload.v0.1",
            "bundle_fingerprint_payload": payload,
            "bundle_sha256": sha256_value(payload),
        },
        "created_at": created_at,
    }
    print(yaml.safe_dump(bundle, allow_unicode=True, sort_keys=False), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
