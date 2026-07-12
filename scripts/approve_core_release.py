#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import yaml

from lib.core_release_verifier import load_yaml, sha256_file, verify_manifest
from lib.git_provenance import head_commit, require_clean_worktree, require_tree_tracked_at_head
from lib.safe_io import exclusive_lock, immutable_write_text, safe_repo_path

ROOT = Path(__file__).resolve().parents[1]
REVIEW_POINTER = ROOT / "THOMAS_CORE/REVIEW_CORE_RELEASE.yaml"
LOCK_PATH = ROOT / ".git/thomas_agent_locks/core_approval.lock"

VERIFIED_STATUSES = {
    "verified_by_control_channel",
    "verified_by_protected_review",
    "verified_by_signature",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record one Runtime-authoritative Thomas Approval for an exact committed Core Release."
    )
    parser.add_argument("--manifest")
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--approval-ref", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument(
        "--approval-source-type",
        required=True,
        choices=[
            "telegram_authenticated_control_channel",
            "github_protected_review",
            "signed_git_commit",
            "operator_decision_intake",
        ],
    )
    parser.add_argument("--approval-source-id", required=True)
    parser.add_argument("--approval-source-hash", required=True)
    parser.add_argument("--identity-verification-method", required=True)
    parser.add_argument("--verification-status", required=True, choices=sorted(VERIFIED_STATUSES))
    args = parser.parse_args()

    if args.approved_by != "Thomas":
        raise ValueError("--approved-by must be exactly Thomas")

    if not (
        args.approval_source_hash.startswith("sha256:")
        and len(args.approval_source_hash) == 71
        and all(char in "0123456789abcdef" for char in args.approval_source_hash[7:])
    ):
        raise ValueError("--approval-source-hash must be sha256:<64 lowercase hex>")

    if args.manifest:
        manifest_path = safe_repo_path(ROOT, args.manifest, must_exist=True)
    else:
        review = load_yaml(REVIEW_POINTER)
        manifest_path = safe_repo_path(ROOT, review["manifest_path"], must_exist=True)

    manifest = verify_manifest(ROOT, manifest_path)

    with exclusive_lock(LOCK_PATH):
        require_clean_worktree(ROOT)
        # Verifies every Release snapshot file is tracked and identical to HEAD.
        require_tree_tracked_at_head(ROOT, manifest_path.parent)
        approved_commit_sha = head_commit(ROOT)

        seed = (
            manifest["release_id"] + "\0"
            + manifest["core_bundle_sha256"] + "\0"
            + sha256_file(manifest_path) + "\0"
            + approved_commit_sha + "\0"
            + args.approval_source_id + "\0"
            + args.approval_source_hash
        ).encode("utf-8")

        approval_id = "core-approval-" + hashlib.sha256(seed).hexdigest()[:24]
        approval_path = safe_repo_path(ROOT, f"THOMAS_CORE/approvals/{approval_id}.yaml")

        record = {
            "schema_version": "thomas_core_release_approval.v0.3",
            "approval_id": approval_id,
            "status": "approved",
            "approved_by": "Thomas",
            "approved_at_utc": utc_now(),
            "approval_ref": args.approval_ref,
            "approval_reason": args.reason,
            "approval_source": {
                "source_type": args.approval_source_type,
                "source_id": args.approval_source_id,
                "source_hash": args.approval_source_hash,
                "identity_verification_method": args.identity_verification_method,
                "verification_status": args.verification_status,
                "note": (
                    "The CLI validates and preserves externally verified evidence; "
                    "the external control channel, protected review, or signature system performs authentication."
                ),
            },
            "release_id": manifest["release_id"],
            "core_version": manifest["core_version"],
            "core_bundle_sha256": manifest["core_bundle_sha256"],
            "core_release_manifest_sha256": sha256_file(manifest_path),
            "manifest_path": manifest_path.relative_to(ROOT).as_posix(),
            "git_provenance": {
                "approved_commit_sha": approved_commit_sha,
            },
            "scope": {
                "authorizes_core_runtime_reference": True,
                "grants_execution_permission": False,
                "grants_external_action_permission": False,
                "grants_financial_authority": False,
                "changes_agent_permission_ceiling": False,
                "changes_tool_or_program_scope": False,
            },
            "immutability": {
                "approval_record_edit_in_place_allowed": False,
                "revocation_or_supersession_requires_new_record": True,
            },
        }

        immutable_write_text(
            approval_path,
            yaml.safe_dump(record, sort_keys=False, allow_unicode=True, width=120),
        )

    print("PASS: recorded Runtime-authoritative Thomas Core Release Approval")
    print(f"Approval ID: {approval_id}")
    print("Approval path: " + approval_path.relative_to(ROOT).as_posix())
    print("Next: commit the Approval record before activation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
