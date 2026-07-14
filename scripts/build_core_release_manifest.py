#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.metadata
import os
import platform
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from lib.core_release_verifier import (
    bundle_payload,
    load_yaml,
    sha256_file,
    sha256_prefixed,
    verify_manifest,
)
from lib.release_gate_evidence import verify_gate_evidence
from lib.safe_io import atomic_write_text, exclusive_lock, safe_repo_path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "THOMAS_CORE/CORE_RELEASE_MANIFEST_TEMPLATE.yaml"
REVIEW_POINTER_PATH = ROOT / "THOMAS_CORE/REVIEW_CORE_RELEASE.yaml"
LOCK_PATH = ROOT / ".runtime_locks/core_release_build.lock"

TOOLCHAIN_FILES = [
    "scripts/build_core_release_manifest.py",
    "scripts/run_repository_release_gate.py",
    "scripts/validate_core_release_reproducibility.py",
    "scripts/validate_static_integrity.py",
    "scripts/validate_i0_preconditions.py",
    "scripts/validate_contract_consistency.py",
    "scripts/validate_task_contracts.py",
    "scripts/validate_thomas_core.py",
    "scripts/validate_core_projection_consistency.py",
    "scripts/validate_runtime_lineage_bundle.py",
    "scripts/validate_programization_contracts.py",
    "scripts/validate_core_lifecycle_schemas.py",
    "scripts/validate_contract_schema_parity.py",
    "scripts/validate_security_hardening.py",
    "scripts/test_apply_core_idempotency.py",
    "scripts/lib/core_release_verifier.py",
    "scripts/lib/release_gate_evidence.py",
    "scripts/lib/safe_io.py",
    "scripts/lib/git_provenance.py",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def installed_dependency_versions() -> dict[str, str]:
    result: dict[str, str] = {}
    for package in ["PyYAML", "jsonschema", "referencing", "rpds-py", "attrs"]:
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = "NOT_INSTALLED"
    return result


def copy_snapshot(source: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    data = destination.read_bytes()
    return {
        "sha256": sha256_prefixed(data),
        "size_bytes": len(data),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build one self-contained immutable Thomas Core Release from a recent PASS Gate evidence record. "
            "The Builder does not rerun the Gate."
        )
    )
    parser.add_argument("--built-by", default="core-release-builder")
    args = parser.parse_args()

    template = load_yaml(TEMPLATE_PATH)
    if template.get("schema_version") != "thomas_core_release_manifest_template.v0.3":
        raise ValueError("Core Release Manifest Template schema mismatch")

    core_version = template.get("core_version")
    file_set = template.get("release_file_set")
    lock_rel = template.get("validation_lock_file")
    gate_rel = template.get("release_gate_evidence")

    if not isinstance(core_version, str):
        raise ValueError("Template core_version must be a string")
    if not isinstance(file_set, list) or not file_set:
        raise ValueError("Template release_file_set must be non-empty")
    if not isinstance(lock_rel, str) or not lock_rel:
        raise ValueError("Template validation_lock_file is required")
    if not isinstance(gate_rel, str) or not gate_rel:
        raise ValueError("Template release_gate_evidence is required")

    gate_path, gate_evidence = verify_gate_evidence(ROOT, gate_rel)

    source_entries: list[dict[str, Any]] = []
    for logical_path in sorted(file_set):
        source = safe_repo_path(ROOT, logical_path, must_exist=True)
        data = source.read_bytes()
        source_entries.append({
            "logical_path": logical_path,
            "sha256": sha256_prefixed(data),
            "size_bytes": len(data),
        })

    bundle_sha = sha256_prefixed(bundle_payload(source_entries))
    release_id = f"thomas-core-v{core_version}-{bundle_sha.split(':', 1)[1][:12]}"
    releases_dir = safe_repo_path(ROOT, "THOMAS_CORE/releases", allow_directory=True)
    release_dir = releases_dir / release_id
    manifest_path = release_dir / "manifest.yaml"

    with exclusive_lock(LOCK_PATH):
        if manifest_path.exists():
            verify_manifest(ROOT, manifest_path)
            print("PASS: existing immutable Core Release snapshot verified")
        else:
            releases_dir.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=f".{release_id}.", dir=releases_dir))

            try:
                files: list[dict[str, Any]] = []
                for entry in source_entries:
                    logical = entry["logical_path"]
                    source = safe_repo_path(ROOT, logical, must_exist=True)
                    snapshot_rel = (Path("artifacts") / logical).as_posix()
                    copied = copy_snapshot(source, staging / snapshot_rel)
                    if copied["sha256"] != entry["sha256"]:
                        raise RuntimeError(f"Source changed during Release snapshot: {logical}")
                    files.append({
                        "logical_path": logical,
                        "snapshot_path": snapshot_rel,
                        "sha256": copied["sha256"],
                        "size_bytes": copied["size_bytes"],
                    })

                validator_files: list[dict[str, Any]] = []
                for logical in TOOLCHAIN_FILES:
                    source = safe_repo_path(ROOT, logical, must_exist=True)
                    snapshot_rel = (Path("validation_toolchain/files") / logical).as_posix()
                    copied = copy_snapshot(source, staging / snapshot_rel)
                    validator_files.append({
                        "logical_path": logical,
                        "snapshot_path": snapshot_rel,
                        "sha256": copied["sha256"],
                        "size_bytes": copied["size_bytes"],
                    })

                lock_source = safe_repo_path(ROOT, lock_rel, must_exist=True)
                lock_snapshot_rel = "validation_toolchain/requirements-validation.lock"
                copied_lock = copy_snapshot(lock_source, staging / lock_snapshot_rel)

                gate_snapshot_rel = "validation_toolchain/release_gate_evidence.yaml"
                copied_gate = copy_snapshot(gate_path, staging / gate_snapshot_rel)

                environment = {
                    "python_implementation": platform.python_implementation(),
                    "python_version": platform.python_version(),
                    "operating_system": platform.platform(),
                    "dependency_versions": installed_dependency_versions(),
                }
                environment_rel = "validation_toolchain/validation_environment.yaml"
                environment_path = staging / environment_rel
                environment_path.parent.mkdir(parents=True, exist_ok=True)
                environment_path.write_text(
                    yaml.safe_dump(environment, sort_keys=False, allow_unicode=True, width=120),
                    encoding="utf-8",
                    newline="\n",
                )

                by_logical = {item["logical_path"]: item for item in files}
                active_snapshot = staging / by_logical["THOMAS_CORE/MVP_ACTIVE_CORE.yaml"]["snapshot_path"]
                active = load_yaml(active_snapshot)
                active_rule_ids = [
                    item.get("id")
                    for item in active.get("active_rules", [])
                    if isinstance(item, dict)
                ]
                projection = load_yaml(ROOT / "generated/docs/CORE_PROJECTION_MAP.yaml")

                canonical_logical = {
                    "philosophy": "THOMAS_CORE/THOMAS_CORE_PHILOSOPHY.md",
                    "active_core": "THOMAS_CORE/MVP_ACTIVE_CORE.yaml",
                    "runtime_policy_projection": "THOMAS_CORE/CORE_RUNTIME_POLICY_PROJECTION.yaml",
                }

                manifest = {
                    "schema_version": "thomas_core_release_manifest.v0.3",
                    "manifest_version": "0.3.1",
                    "release_id": release_id,
                    "core_version": core_version,
                    "release_status": "review_ready",
                    "runtime_use_allowed_without_separate_approval": False,
                    "owner": "Thomas",
                    "hash_algorithm": "sha256",
                    "core_bundle_sha256": bundle_sha,
                    "historical_verification": {
                        "uses_release_snapshot": True,
                        "uses_current_worktree": False,
                        "artifact_snapshot_root": "artifacts",
                        "toolchain_snapshot_root": "validation_toolchain",
                    },
                    "canonical_artifacts": {
                        key: {
                            "logical_path": logical,
                            "snapshot_path": by_logical[logical]["snapshot_path"],
                            "sha256": by_logical[logical]["sha256"],
                        }
                        for key, logical in canonical_logical.items()
                    },
                    "active_runtime": {
                        "active_rule_ids": active_rule_ids,
                        "active_rule_count": len(active_rule_ids),
                        "projection_map_version": projection.get("map_version"),
                    },
                    "files": files,
                    "build": {
                        "built_at_utc": utc_now(),
                        "built_by": args.built_by,
                        "validation_skipped": False,
                        "validation_evidence": gate_evidence["checks"],
                        "validation_toolchain": {
                            "validator_files": validator_files,
                            "dependency_lock": {
                                "logical_path": lock_rel,
                                "snapshot_path": lock_snapshot_rel,
                                "sha256": copied_lock["sha256"],
                            },
                            "release_gate_evidence": {
                                "logical_path": gate_path.relative_to(ROOT).as_posix(),
                                "snapshot_path": gate_snapshot_rel,
                                "sha256": copied_gate["sha256"],
                                "repository_source_fingerprint": gate_evidence["repository_source_fingerprint"],
                            },
                            "environment": {
                                **environment,
                                "snapshot_path": environment_rel,
                                "sha256": sha256_file(environment_path),
                            },
                        },
                    },
                    "approval_requirement": {
                        "required_for_runtime_use": True,
                        "externally_verified_approval_evidence_required": True,
                        "approval_directory": "THOMAS_CORE/approvals",
                    },
                    "immutability": {
                        "artifact_snapshots_are_immutable": True,
                        "validation_toolchain_snapshot_is_immutable": True,
                        "semantic_change_requires_new_release": True,
                        "approved_release_edit_in_place_allowed": False,
                    },
                    "runtime_binding": {
                        "contract": "core_context_binding.v0.3",
                        "root_task_binding_required": True,
                        "binding_must_be_created_from_task_record": True,
                        "loaded_rules_must_be_explicit": True,
                        "loaded_rules_resolve_against_snapshotted_active_core": True,
                        "silent_mid_task_rebind_allowed": False,
                    },
                }

                (staging / "manifest.yaml").write_text(
                    yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True, width=120),
                    encoding="utf-8",
                    newline="\n",
                )
                os.replace(staging, release_dir)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise

            verify_manifest(ROOT, manifest_path)
            print("PASS: built self-contained immutable Thomas Core Release snapshot")

        review = {
            "schema_version": "review_core_release.v0.2",
            "release_id": release_id,
            "core_version": core_version,
            "core_bundle_sha256": bundle_sha,
            "manifest_path": manifest_path.relative_to(ROOT).as_posix(),
            "core_release_manifest_sha256": sha256_file(manifest_path),
            "review_status": "review_ready_not_runtime_active",
            "updated_at_utc": utc_now(),
        }
        atomic_write_text(
            REVIEW_POINTER_PATH,
            yaml.safe_dump(review, sort_keys=False, allow_unicode=True, width=120),
        )

    print(f"Release ID: {release_id}")
    print(f"Core bundle: {bundle_sha}")
    print("Runtime use: NOT ALLOWED until verified Thomas Approval and committed Activation/Current records exist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
