#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import yaml

from lib.core_release_verifier import sha256_file, verify_current_pointer
from lib.git_provenance import head_commit, require_clean_worktree, require_file_tracked_at_head
from lib.safe_io import atomic_write_text, exclusive_lock, immutable_write_text, safe_repo_path

ROOT = Path(__file__).resolve().parents[1]
CURRENT_POINTER = ROOT / "THOMAS_CORE/CURRENT_CORE_RELEASE.yaml"
LOCK_PATH = ROOT / ".git/thomas_agent_locks/core_deactivation.lock"
VERIFIED_STATUSES = {
    "verified_by_control_channel",
    "verified_by_protected_review",
    "verified_by_signature",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create an immutable fail-closed Core Deactivation event and update the Current pointer."
    )
    parser.add_argument("--deactivated-by", required=True)
    parser.add_argument("--deactivation-ref", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--source-type", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--source-hash", required=True)
    parser.add_argument("--identity-verification-method", required=True)
    parser.add_argument("--verification-status", required=True, choices=sorted(VERIFIED_STATUSES))
    args = parser.parse_args()

    if not CURRENT_POINTER.exists():
        raise FileNotFoundError("CURRENT_CORE_RELEASE.yaml does not exist")

    if not (
        args.source_hash.startswith("sha256:")
        and len(args.source_hash) == 71
        and all(char in "0123456789abcdef" for char in args.source_hash[7:])
    ):
        raise ValueError("Source hash must be sha256:<64 lowercase hex>")

    current = verify_current_pointer(ROOT, CURRENT_POINTER)

    with exclusive_lock(LOCK_PATH):
        require_clean_worktree(ROOT)
        require_file_tracked_at_head(ROOT, CURRENT_POINTER)
        source_commit_sha = head_commit(ROOT)

        seed = (
            str(current.get("activation_id")) + "\0" + args.deactivation_ref + "\0"
            + args.source_hash + "\0" + source_commit_sha
        ).encode("utf-8")
        deactivation_id = "core-deactivation-" + hashlib.sha256(seed).hexdigest()[:24]
        deactivation_path = safe_repo_path(ROOT, f"THOMAS_CORE/deactivations/{deactivation_id}.yaml")

        record = {
            "schema_version": "core_deactivation.v0.1",
            "deactivation_id": deactivation_id,
            "status": "effective",
            "effective_at_utc": utc_now(),
            "deactivated_by": args.deactivated_by,
            "deactivation_ref": args.deactivation_ref,
            "deactivation_reason": args.reason,
            "deactivation_source": {
                "source_type": args.source_type,
                "source_id": args.source_id,
                "source_hash": args.source_hash,
                "identity_verification_method": args.identity_verification_method,
                "verification_status": args.verification_status,
            },
            "previous_current": {
                "runtime_activation_status": current.get("runtime_activation_status"),
                "activation_id": current.get("activation_id"),
                "release_id": current.get("release_id"),
                "current_pointer_sha256": sha256_file(CURRENT_POINTER),
            },
            "git_provenance": {
                "source_commit_sha": source_commit_sha,
            },
            "scope": {
                "fail_closed_for_new_task_bindings_after_commit": True,
                "changes_existing_task_bindings": False,
                "grants_execution_permission": False,
            },
        }
        immutable_write_text(
            deactivation_path,
            yaml.safe_dump(record, sort_keys=False, allow_unicode=True, width=120),
        )

        pointer = {
            "schema_version": "current_core_release.v0.2",
            "runtime_activation_status": "deactivated_fail_closed",
            "deactivation_id": deactivation_id,
            "deactivation_path": deactivation_path.relative_to(ROOT).as_posix(),
            "deactivation_sha256": sha256_file(deactivation_path),
            "updated_at_utc": utc_now(),
            "updated_by": args.deactivated_by,
            "update_ref": args.deactivation_ref,
            "scope": {
                "authorizes_new_task_core_binding": False,
                "changes_existing_task_bindings": False,
                "grants_execution_permission": False,
            },
        }
        atomic_write_text(
            CURRENT_POINTER,
            yaml.safe_dump(pointer, sort_keys=False, allow_unicode=True, width=120),
        )

    print("PASS: created fail-closed Core Deactivation event and updated Current pointer")
    print(f"Deactivation ID: {deactivation_id}")
    print("Commit the Deactivation record and CURRENT_CORE_RELEASE.yaml together before Runtime use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
