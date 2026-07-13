#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import yaml

from lib.core_release_verifier import load_yaml, sha256_file, verify_approval
from lib.git_provenance import head_commit, require_clean_worktree, require_file_tracked_at_head
from lib.safe_io import atomic_write_text, exclusive_lock, immutable_write_text, safe_repo_path

ROOT = Path(__file__).resolve().parents[1]
CURRENT_POINTER = ROOT / "THOMAS_CORE/CURRENT_CORE_RELEASE.yaml"
LOCK_PATH = ROOT / ".git/thomas_agent_locks/core_activation.lock"
VERIFIED_STATUSES = {
    "verified_by_control_channel",
    "verified_by_protected_review",
    "verified_by_signature",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create an immutable Core Activation or Rollback event and atomically update "
            "the Current pointer. Commit both files before Runtime use."
        )
    )
    parser.add_argument("--activation-type", choices=["activate", "rollback"], default="activate")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--approval", required=True)
    parser.add_argument("--activated-by", required=True)
    parser.add_argument("--activation-ref", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--source-type", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--source-hash", required=True)
    parser.add_argument("--identity-verification-method", required=True)
    parser.add_argument("--verification-status", required=True, choices=sorted(VERIFIED_STATUSES))
    args = parser.parse_args()

    if not (
        args.source_hash.startswith("sha256:")
        and len(args.source_hash) == 71
        and all(char in "0123456789abcdef" for char in args.source_hash[7:])
    ):
        raise ValueError("Source hash must be sha256:<64 lowercase hex>")

    manifest_path = safe_repo_path(ROOT, args.manifest, must_exist=True)
    approval_path = safe_repo_path(ROOT, args.approval, must_exist=True)
    manifest, approval = verify_approval(ROOT, manifest_path, approval_path)

    with exclusive_lock(LOCK_PATH):
        require_clean_worktree(ROOT)
        require_file_tracked_at_head(ROOT, manifest_path)
        require_file_tracked_at_head(ROOT, approval_path)
        source_commit_sha = head_commit(ROOT)

        previous = load_yaml(CURRENT_POINTER) if CURRENT_POINTER.exists() else None
        seed = (
            args.activation_type + "\0" + manifest["release_id"] + "\0"
            + approval["approval_id"] + "\0" + source_commit_sha + "\0"
            + args.activation_ref + "\0" + args.source_hash
        ).encode("utf-8")
        activation_id = "core-activation-" + hashlib.sha256(seed).hexdigest()[:24]
        activation_path = safe_repo_path(ROOT, f"THOMAS_CORE/activations/{activation_id}.yaml")

        record = {
            "schema_version": "core_activation.v0.1",
            "activation_id": activation_id,
            "activation_type": args.activation_type,
            "status": "effective",
            "effective_at_utc": utc_now(),
            "activated_by": args.activated_by,
            "activation_ref": args.activation_ref,
            "activation_reason": args.reason,
            "activation_source": {
                "source_type": args.source_type,
                "source_id": args.source_id,
                "source_hash": args.source_hash,
                "identity_verification_method": args.identity_verification_method,
                "verification_status": args.verification_status,
            },
            "release_id": manifest["release_id"],
            "core_version": manifest["core_version"],
            "core_bundle_sha256": manifest["core_bundle_sha256"],
            "manifest_path": manifest_path.relative_to(ROOT).as_posix(),
            "manifest_sha256": sha256_file(manifest_path),
            "approval_id": approval["approval_id"],
            "approval_path": approval_path.relative_to(ROOT).as_posix(),
            "approval_sha256": sha256_file(approval_path),
            "previous_current": (
                {
                    "runtime_activation_status": previous.get("runtime_activation_status"),
                    "activation_id": previous.get("activation_id"),
                    "release_id": previous.get("release_id"),
                }
                if isinstance(previous, dict)
                else None
            ),
            "git_provenance": {
                "source_commit_sha": source_commit_sha,
            },
            "scope": {
                "authorizes_new_task_core_binding_after_commit": True,
                "changes_existing_task_bindings": False,
                "grants_execution_permission": False,
                "grants_external_action_permission": False,
                "grants_financial_authority": False,
            },
        }

        immutable_write_text(
            activation_path,
            yaml.safe_dump(record, sort_keys=False, allow_unicode=True, width=120),
        )

        pointer = {
            "schema_version": "current_core_release.v0.2",
            "runtime_activation_status": "approved_via_activation_registry",
            "activation_id": activation_id,
            "activation_path": activation_path.relative_to(ROOT).as_posix(),
            "activation_sha256": sha256_file(activation_path),
            "release_id": manifest["release_id"],
            "core_version": manifest["core_version"],
            "core_bundle_sha256": manifest["core_bundle_sha256"],
            "approval_id": approval["approval_id"],
            "updated_at_utc": utc_now(),
            "updated_by": args.activated_by,
            "update_ref": args.activation_ref,
            "scope": {
                "authorizes_new_task_core_binding_after_commit": True,
                "changes_existing_task_bindings": False,
                "grants_execution_permission": False,
            },
        }
        atomic_write_text(
            CURRENT_POINTER,
            yaml.safe_dump(pointer, sort_keys=False, allow_unicode=True, width=120),
        )

    print(f"PASS: created Core {args.activation_type} event and updated Current pointer")
    print(f"Activation ID: {activation_id}")
    print("Commit the Activation record and CURRENT_CORE_RELEASE.yaml together before Runtime use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
