#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import yaml

from lib.core_release_verifier import load_yaml, sha256_file
from lib.git_provenance import head_commit, require_clean_worktree, require_file_tracked_at_head
from lib.safe_io import exclusive_lock, immutable_write_text, safe_repo_path

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / ".git/thomas_agent_locks/core_revocation.lock"
VERIFIED_STATUSES = {
    "verified_by_control_channel",
    "verified_by_protected_review",
    "verified_by_signature",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create an immutable effective Revocation for a committed Core Approval or Activation."
    )
    parser.add_argument("--approval", required=True)
    parser.add_argument("--activation")
    parser.add_argument("--revoked-by", required=True)
    parser.add_argument("--revocation-ref", required=True)
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

    approval_path = safe_repo_path(ROOT, args.approval, must_exist=True)
    approval = load_yaml(approval_path)
    activation_path = safe_repo_path(ROOT, args.activation, must_exist=True) if args.activation else None
    activation = load_yaml(activation_path) if activation_path else None

    with exclusive_lock(LOCK_PATH):
        require_clean_worktree(ROOT)
        require_file_tracked_at_head(ROOT, approval_path)
        if activation_path:
            require_file_tracked_at_head(ROOT, activation_path)
        source_commit_sha = head_commit(ROOT)

        seed = (
            str(approval.get("approval_id", "")) + "\0"
            + str(activation.get("activation_id") if activation else "") + "\0"
            + args.revocation_ref + "\0" + args.source_hash + "\0" + source_commit_sha
        ).encode("utf-8")
        revocation_id = "core-revocation-" + hashlib.sha256(seed).hexdigest()[:24]
        path = safe_repo_path(ROOT, f"THOMAS_CORE/revocations/{revocation_id}.yaml")

        record = {
            "schema_version": "core_revocation.v0.1",
            "revocation_id": revocation_id,
            "status": "effective",
            "effective_at_utc": utc_now(),
            "revoked_by": args.revoked_by,
            "revocation_ref": args.revocation_ref,
            "revocation_reason": args.reason,
            "revocation_source": {
                "source_type": args.source_type,
                "source_id": args.source_id,
                "source_hash": args.source_hash,
                "identity_verification_method": args.identity_verification_method,
                "verification_status": args.verification_status,
            },
            "target_approval_id": approval.get("approval_id"),
            "target_approval_path": approval_path.relative_to(ROOT).as_posix(),
            "target_approval_sha256": sha256_file(approval_path),
            "target_activation_id": activation.get("activation_id") if activation else None,
            "target_activation_path": activation_path.relative_to(ROOT).as_posix() if activation_path else None,
            "git_provenance": {
                "source_commit_sha": source_commit_sha,
            },
            "effect": {
                "runtime_reference_invalid": True,
                "new_task_binding_prohibited": True,
                "existing_task_binding_automatic_change": False,
                "operator_must_deactivate_or_rollback_if_current": True,
            },
        }
        immutable_write_text(
            path,
            yaml.safe_dump(record, sort_keys=False, allow_unicode=True, width=120),
        )

    print("PASS: created effective immutable Core Revocation record")
    print(f"Revocation ID: {revocation_id}")
    print("Commit the Revocation record. If the target is Current, deactivate fail closed or roll back.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
