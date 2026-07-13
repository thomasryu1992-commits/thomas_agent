from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from lib.safe_io import safe_child_path, safe_repo_path


class CoreReleaseVerificationError(ValueError):
    pass


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)

    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    if not isinstance(data, dict):
        raise CoreReleaseVerificationError(
            f"{path}: expected YAML mapping"
        )

    return data


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_prefixed(data: bytes) -> str:
    return "sha256:" + sha256_bytes(data)


def sha256_file(path: Path) -> str:
    return sha256_prefixed(path.read_bytes())


def bundle_payload(
    entries: list[dict[str, Any]],
    *,
    path_key: str = "logical_path",
) -> bytes:
    normalized = sorted(
        (
            {
                "path": item[path_key],
                "sha256": item["sha256"],
            }
            for item in entries
        ),
        key=lambda item: item["path"],
    )

    return "".join(
        f"{item['path']}\0{item['sha256']}\n"
        for item in normalized
    ).encode("utf-8")


def _release_dir_from_manifest(
    root: Path,
    manifest_path: Path,
    release_id: str,
) -> Path:
    expected = safe_repo_path(
        root,
        f"THOMAS_CORE/releases/{release_id}",
        allow_directory=True,
    )

    if manifest_path.resolve() != (expected / "manifest.yaml").resolve():
        raise CoreReleaseVerificationError(
            "Manifest must be stored at "
            "THOMAS_CORE/releases/<release_id>/manifest.yaml"
        )

    return expected


def verify_manifest(
    root: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = manifest_path.resolve()
    manifest = load_yaml(manifest_path)

    if manifest.get("schema_version") != "thomas_core_release_manifest.v0.3":
        raise CoreReleaseVerificationError(
            "Core Release Manifest schema must be v0.3"
        )

    if manifest.get("release_status") != "review_ready":
        raise CoreReleaseVerificationError(
            "Core Release Manifest must be review_ready"
        )

    if manifest.get("runtime_use_allowed_without_separate_approval") is not False:
        raise CoreReleaseVerificationError(
            "Release Manifest must not approve itself"
        )

    if manifest.get("hash_algorithm") != "sha256":
        raise CoreReleaseVerificationError(
            "Release Manifest hash algorithm must be sha256"
        )

    release_id = manifest.get("release_id")
    core_version = manifest.get("core_version")

    if not isinstance(release_id, str) or not release_id:
        raise CoreReleaseVerificationError("Release ID is required")

    if not isinstance(core_version, str) or not core_version:
        raise CoreReleaseVerificationError("Core version is required")

    expected_prefix = f"thomas-core-v{core_version}-"

    if not release_id.startswith(expected_prefix):
        raise CoreReleaseVerificationError(
            "Release ID does not match Core version"
        )

    release_dir = _release_dir_from_manifest(
        root,
        manifest_path,
        release_id,
    )

    files = manifest.get("files")

    if not isinstance(files, list) or not files:
        raise CoreReleaseVerificationError(
            "Release Manifest files must be a non-empty list"
        )

    seen_logical: set[str] = set()
    seen_snapshot: set[str] = set()
    verified_entries: list[dict[str, Any]] = []

    for item in files:
        if not isinstance(item, dict):
            raise CoreReleaseVerificationError(
                "Release file entry must be a mapping"
            )

        logical = item.get("logical_path")
        snapshot = item.get("snapshot_path")
        expected_hash = item.get("sha256")
        expected_size = item.get("size_bytes")

        if not isinstance(logical, str) or not logical:
            raise CoreReleaseVerificationError(
                "Release logical_path is invalid"
            )

        if not isinstance(snapshot, str) or not snapshot:
            raise CoreReleaseVerificationError(
                f"Release snapshot_path is invalid: {logical}"
            )

        if logical in seen_logical:
            raise CoreReleaseVerificationError(
                f"Duplicate logical path: {logical}"
            )

        if snapshot in seen_snapshot:
            raise CoreReleaseVerificationError(
                f"Duplicate snapshot path: {snapshot}"
            )

        seen_logical.add(logical)
        seen_snapshot.add(snapshot)

        snapshot_path = safe_child_path(
            release_dir,
            snapshot,
            must_exist=True,
        )

        data = snapshot_path.read_bytes()
        actual_hash = sha256_prefixed(data)

        if actual_hash != expected_hash:
            raise CoreReleaseVerificationError(
                f"Release artifact hash mismatch: {logical}"
            )

        if len(data) != expected_size:
            raise CoreReleaseVerificationError(
                f"Release artifact size mismatch: {logical}"
            )

        verified_entries.append(
            {
                "logical_path": logical,
                "snapshot_path": snapshot,
                "sha256": actual_hash,
                "size_bytes": len(data),
            }
        )

    actual_bundle = sha256_prefixed(
        bundle_payload(verified_entries)
    )

    if actual_bundle != manifest.get("core_bundle_sha256"):
        raise CoreReleaseVerificationError(
            "Core bundle SHA256 mismatch"
        )

    canonical = manifest.get("canonical_artifacts")

    if not isinstance(canonical, dict):
        raise CoreReleaseVerificationError(
            "canonical_artifacts must be a mapping"
        )

    by_logical = {
        item["logical_path"]: item
        for item in verified_entries
    }

    expected_canonical = {
        "philosophy": "THOMAS_CORE/THOMAS_CORE_PHILOSOPHY.md",
        "active_core": "THOMAS_CORE/MVP_ACTIVE_CORE.yaml",
        "runtime_policy_projection": (
            "THOMAS_CORE/CORE_RUNTIME_POLICY_PROJECTION.yaml"
        ),
    }

    for key, logical in expected_canonical.items():
        item = canonical.get(key)

        if not isinstance(item, dict):
            raise CoreReleaseVerificationError(
                f"Canonical artifact missing: {key}"
            )

        expected_entry = by_logical.get(logical)

        if expected_entry is None:
            raise CoreReleaseVerificationError(
                f"Canonical artifact not in Release file set: {logical}"
            )

        for field in [
            "logical_path",
            "snapshot_path",
            "sha256",
        ]:
            if item.get(field) != expected_entry.get(field):
                raise CoreReleaseVerificationError(
                    f"Canonical artifact mismatch: {key}.{field}"
                )

    active_core_item = canonical["active_core"]
    active_core_path = safe_child_path(
        release_dir,
        active_core_item["snapshot_path"],
        must_exist=True,
    )
    active_core = load_yaml(active_core_path)

    active_rule_ids = [
        item.get("id")
        for item in active_core.get("active_rules", [])
        if isinstance(item, dict)
    ]

    manifest_ids = (
        manifest.get("active_runtime", {})
        .get("active_rule_ids")
    )

    if manifest_ids != active_rule_ids:
        raise CoreReleaseVerificationError(
            "Manifest Active Rule IDs do not match snapshotted Active Core"
        )

    build = manifest.get("build")

    if not isinstance(build, dict):
        raise CoreReleaseVerificationError(
            "Release build evidence is missing"
        )

    if build.get("validation_skipped") is not False:
        raise CoreReleaseVerificationError(
            "A review-ready Release must not skip validation"
        )

    evidence = build.get("validation_evidence")

    if not isinstance(evidence, list) or not evidence:
        raise CoreReleaseVerificationError(
            "Validation evidence is missing"
        )

    toolchain = build.get("validation_toolchain")

    if not isinstance(toolchain, dict):
        raise CoreReleaseVerificationError(
            "Validation toolchain evidence is missing"
        )

    validator_files = toolchain.get("validator_files")

    if not isinstance(validator_files, list) or not validator_files:
        raise CoreReleaseVerificationError(
            "Snapshotted validator file evidence is missing"
        )

    for item in validator_files:
        if not isinstance(item, dict):
            raise CoreReleaseVerificationError(
                "Invalid validator snapshot evidence"
            )

        snapshot = item.get("snapshot_path")
        expected_hash = item.get("sha256")

        if not isinstance(snapshot, str):
            raise CoreReleaseVerificationError(
                "Validator snapshot path is invalid"
            )

        path = safe_child_path(
            release_dir,
            snapshot,
            must_exist=True,
        )

        if sha256_file(path) != expected_hash:
            raise CoreReleaseVerificationError(
                f"Validator snapshot hash mismatch: {snapshot}"
            )

    lock = toolchain.get("dependency_lock")

    if not isinstance(lock, dict):
        raise CoreReleaseVerificationError(
            "Dependency lock snapshot is missing"
        )

    lock_path = safe_child_path(
        release_dir,
        lock.get("snapshot_path", ""),
        must_exist=True,
    )

    if sha256_file(lock_path) != lock.get("sha256"):
        raise CoreReleaseVerificationError(
            "Dependency lock snapshot hash mismatch"
        )

    gate = toolchain.get("release_gate_evidence")

    if not isinstance(gate, dict):
        raise CoreReleaseVerificationError(
            "Release Gate evidence snapshot is missing"
        )

    gate_path = safe_child_path(
        release_dir,
        gate.get("snapshot_path", ""),
        must_exist=True,
    )

    if sha256_file(gate_path) != gate.get("sha256"):
        raise CoreReleaseVerificationError(
            "Release Gate evidence snapshot hash mismatch"
        )

    gate_record = load_yaml(gate_path)

    if gate_record.get("schema_version") != "thomas_release_gate_evidence.v0.1":
        raise CoreReleaseVerificationError(
            "Release Gate evidence schema mismatch"
        )

    if gate_record.get("result") != "PASS":
        raise CoreReleaseVerificationError(
            "Release Gate evidence result must be PASS"
        )

    if (
        gate_record.get("repository_source_fingerprint")
        != gate.get("repository_source_fingerprint")
    ):
        raise CoreReleaseVerificationError(
            "Release Gate source fingerprint mismatch"
        )

    if gate_record.get("checks") != evidence:
        raise CoreReleaseVerificationError(
            "Manifest validation evidence differs from snapshotted Gate evidence"
        )

    environment = toolchain.get("environment")

    if not isinstance(environment, dict):
        raise CoreReleaseVerificationError(
            "Validation environment evidence is missing"
        )

    if not isinstance(environment.get("dependency_versions"), dict):
        raise CoreReleaseVerificationError(
            "Installed dependency versions are missing"
        )

    return manifest


def approval_is_revoked(
    root: Path,
    approval_id: str,
    activation_id: str | None = None,
) -> bool:
    directory = safe_repo_path(
        root,
        "THOMAS_CORE/revocations",
        allow_directory=True,
    )

    if not directory.exists():
        return False

    for path in directory.glob("*.yaml"):
        record = load_yaml(path)

        if record.get("schema_version") != "core_revocation.v0.1":
            continue

        if record.get("status") != "effective":
            continue

        if record.get("target_approval_id") == approval_id:
            return True

        if (
            activation_id
            and record.get("target_activation_id") == activation_id
        ):
            return True

    return False


def verify_approval(
    root: Path,
    manifest_path: Path,
    approval_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = root.resolve()
    manifest_path = manifest_path.resolve()
    approval_path = approval_path.resolve()

    manifest = verify_manifest(root, manifest_path)
    approval = load_yaml(approval_path)

    if approval.get("schema_version") != "thomas_core_release_approval.v0.3":
        raise CoreReleaseVerificationError(
            "Core Release Approval schema must be v0.3"
        )

    if approval.get("status") != "approved":
        raise CoreReleaseVerificationError(
            "Core Release Approval must be approved"
        )

    if approval.get("approved_by") != "Thomas":
        raise CoreReleaseVerificationError(
            "Core Release must be approved by Thomas"
        )

    for key in [
        "release_id",
        "core_version",
        "core_bundle_sha256",
    ]:
        if approval.get(key) != manifest.get(key):
            raise CoreReleaseVerificationError(
                f"Approval and Manifest mismatch: {key}"
            )

    if approval.get("core_release_manifest_sha256") != sha256_file(manifest_path):
        raise CoreReleaseVerificationError(
            "Approval Manifest SHA256 mismatch"
        )

    approval_id = approval.get("approval_id")
    if not isinstance(approval_id, str) or not approval_id.startswith("core-approval-"):
        raise CoreReleaseVerificationError("Approval ID is invalid")

    expected_path = safe_repo_path(
        root,
        f"THOMAS_CORE/approvals/{approval_id}.yaml",
    )
    if approval_path != expected_path:
        raise CoreReleaseVerificationError(
            "Approval must be stored under THOMAS_CORE/approvals/<approval_id>.yaml"
        )

    source = approval.get("approval_source")
    if not isinstance(source, dict):
        raise CoreReleaseVerificationError("Approval source evidence is missing")

    for key in [
        "source_type",
        "source_id",
        "source_hash",
        "identity_verification_method",
        "verification_status",
    ]:
        if not source.get(key):
            raise CoreReleaseVerificationError(f"Approval source missing: {key}")

    if source.get("verification_status") not in {
        "verified_by_control_channel",
        "verified_by_protected_review",
        "verified_by_signature",
    }:
        raise CoreReleaseVerificationError(
            "Approval requires externally verified evidence"
        )

    scope = approval.get("scope")
    if not isinstance(scope, dict):
        raise CoreReleaseVerificationError("Approval scope is missing")

    if scope.get("authorizes_core_runtime_reference") is not True:
        raise CoreReleaseVerificationError(
            "Approval must authorize Core Runtime reference"
        )

    for key in [
        "grants_execution_permission",
        "grants_external_action_permission",
        "grants_financial_authority",
        "changes_agent_permission_ceiling",
        "changes_tool_or_program_scope",
    ]:
        if scope.get(key) is not False:
            raise CoreReleaseVerificationError(
                f"Approval scope must keep {key}=false"
            )

    provenance = approval.get("git_provenance")
    if not isinstance(provenance, dict):
        raise CoreReleaseVerificationError("Approval Git provenance is missing")

    approved_commit_sha = provenance.get("approved_commit_sha")
    if (
        not isinstance(approved_commit_sha, str)
        or len(approved_commit_sha) != 40
        or any(char not in "0123456789abcdef" for char in approved_commit_sha)
    ):
        raise CoreReleaseVerificationError(
            "Approval approved_commit_sha is invalid"
        )

    if approval_is_revoked(root, approval_id):
        raise CoreReleaseVerificationError(
            f"Approval has been revoked: {approval_id}"
        )

    return manifest, approval


def verify_activation_record(
    root: Path,
    activation_path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    Path,
    Path,
]:
    root = root.resolve()
    activation_path = activation_path.resolve()
    activation = load_yaml(activation_path)

    if activation.get("schema_version") != "core_activation.v0.1":
        raise CoreReleaseVerificationError(
            "Core Activation schema must be v0.1"
        )

    if activation.get("status") != "effective":
        raise CoreReleaseVerificationError(
            "Core Activation record must be effective"
        )

    activation_id = activation.get("activation_id")
    if not isinstance(activation_id, str) or not activation_id.startswith("core-activation-"):
        raise CoreReleaseVerificationError("Activation ID is invalid")

    expected_path = safe_repo_path(
        root,
        f"THOMAS_CORE/activations/{activation_id}.yaml",
    )
    if activation_path != expected_path:
        raise CoreReleaseVerificationError(
            "Activation must be stored under THOMAS_CORE/activations/<activation_id>.yaml"
        )

    manifest_path = safe_repo_path(
        root,
        activation.get("manifest_path", ""),
        must_exist=True,
    )
    approval_path = safe_repo_path(
        root,
        activation.get("approval_path", ""),
        must_exist=True,
    )

    manifest, approval = verify_approval(root, manifest_path, approval_path)

    if activation.get("release_id") != manifest.get("release_id"):
        raise CoreReleaseVerificationError("Activation Release ID mismatch")
    if activation.get("approval_id") != approval.get("approval_id"):
        raise CoreReleaseVerificationError("Activation Approval ID mismatch")
    if activation.get("manifest_sha256") != sha256_file(manifest_path):
        raise CoreReleaseVerificationError("Activation Manifest SHA256 mismatch")
    if activation.get("approval_sha256") != sha256_file(approval_path):
        raise CoreReleaseVerificationError("Activation Approval SHA256 mismatch")

    source = activation.get("activation_source")
    if not isinstance(source, dict):
        raise CoreReleaseVerificationError("Activation source evidence is missing")
    if source.get("verification_status") not in {
        "verified_by_control_channel",
        "verified_by_protected_review",
        "verified_by_signature",
    }:
        raise CoreReleaseVerificationError("Activation requires a verified source")

    provenance = activation.get("git_provenance")
    if not isinstance(provenance, dict):
        raise CoreReleaseVerificationError("Activation Git provenance is missing")
    source_commit_sha = provenance.get("source_commit_sha")
    if (
        not isinstance(source_commit_sha, str)
        or len(source_commit_sha) != 40
        or any(char not in "0123456789abcdef" for char in source_commit_sha)
    ):
        raise CoreReleaseVerificationError("Activation source_commit_sha is invalid")

    if approval_is_revoked(root, approval["approval_id"], activation_id):
        raise CoreReleaseVerificationError(
            "Activation or its Approval has been revoked"
        )

    return activation, manifest, approval, manifest_path, approval_path


def verify_deactivation_record(
    root: Path,
    deactivation_path: Path,
) -> dict[str, Any]:
    root = root.resolve()
    deactivation_path = deactivation_path.resolve()
    record = load_yaml(deactivation_path)

    if record.get("schema_version") != "core_deactivation.v0.1":
        raise CoreReleaseVerificationError(
            "Core Deactivation schema must be v0.1"
        )

    if record.get("status") != "effective":
        raise CoreReleaseVerificationError(
            "Core Deactivation record must be effective"
        )

    record_id = record.get("deactivation_id")

    if (
        not isinstance(record_id, str)
        or not record_id.startswith("core-deactivation-")
    ):
        raise CoreReleaseVerificationError(
            "Deactivation ID is invalid"
        )

    expected = safe_repo_path(
        root,
        f"THOMAS_CORE/deactivations/{record_id}.yaml",
    )

    if deactivation_path != expected:
        raise CoreReleaseVerificationError(
            "Deactivation path mismatch"
        )

    source = record.get("deactivation_source")
    if not isinstance(source, dict):
        raise CoreReleaseVerificationError(
            "Deactivation source evidence is missing"
        )
    if source.get("verification_status") not in {
        "verified_by_control_channel",
        "verified_by_protected_review",
        "verified_by_signature",
    }:
        raise CoreReleaseVerificationError(
            "Deactivation requires a verified source"
        )

    previous = record.get("previous_current")
    if not isinstance(previous, dict):
        raise CoreReleaseVerificationError(
            "Deactivation previous Current evidence is missing"
        )
    if not previous.get("current_pointer_sha256"):
        raise CoreReleaseVerificationError(
            "Deactivation previous Current SHA256 is missing"
        )

    provenance = record.get("git_provenance")
    if not isinstance(provenance, dict):
        raise CoreReleaseVerificationError("Deactivation Git provenance is missing")
    source_commit_sha = provenance.get("source_commit_sha")
    if (
        not isinstance(source_commit_sha, str)
        or len(source_commit_sha) != 40
        or any(char not in "0123456789abcdef" for char in source_commit_sha)
    ):
        raise CoreReleaseVerificationError("Deactivation source_commit_sha is invalid")

    return record


def verify_current_pointer(
    root: Path,
    pointer_path: Path,
) -> dict[str, Any]:
    root = root.resolve()
    pointer_path = pointer_path.resolve()
    pointer = load_yaml(pointer_path)

    if pointer.get("schema_version") != "current_core_release.v0.2":
        raise CoreReleaseVerificationError(
            "Current Core pointer schema must be v0.2"
        )

    status = pointer.get("runtime_activation_status")

    if status == "approved_via_activation_registry":
        activation_path = safe_repo_path(
            root,
            pointer.get("activation_path", ""),
            must_exist=True,
        )

        activation, manifest, approval, _, _ = (
            verify_activation_record(root, activation_path)
        )

        if (
            pointer.get("activation_id")
            != activation.get("activation_id")
        ):
            raise CoreReleaseVerificationError(
                "Current pointer Activation ID mismatch"
            )

        if (
            pointer.get("activation_sha256")
            != sha256_file(activation_path)
        ):
            raise CoreReleaseVerificationError(
                "Current pointer Activation SHA256 mismatch"
            )

        for key in [
            "release_id",
            "core_version",
            "core_bundle_sha256",
        ]:
            if pointer.get(key) != manifest.get(key):
                raise CoreReleaseVerificationError(
                    f"Current pointer Manifest mismatch: {key}"
                )

        if (
            pointer.get("approval_id")
            != approval.get("approval_id")
        ):
            raise CoreReleaseVerificationError(
                "Current pointer Approval ID mismatch"
            )

        return pointer

    if status == "deactivated_fail_closed":
        deactivation_path = safe_repo_path(
            root,
            pointer.get("deactivation_path", ""),
            must_exist=True,
        )

        record = verify_deactivation_record(
            root,
            deactivation_path,
        )

        if (
            pointer.get("deactivation_id")
            != record.get("deactivation_id")
        ):
            raise CoreReleaseVerificationError(
                "Current pointer Deactivation ID mismatch"
            )

        if (
            pointer.get("deactivation_sha256")
            != sha256_file(deactivation_path)
        ):
            raise CoreReleaseVerificationError(
                "Current pointer Deactivation SHA256 mismatch"
            )

        return pointer

    raise CoreReleaseVerificationError(
        "Current Core pointer has an invalid Runtime activation status"
    )
