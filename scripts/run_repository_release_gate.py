#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from gate_matrix import REPOSITORY_RELEASE_CHECKS
from lib.release_gate_evidence import repository_source_fingerprint, write_gate_evidence

# Deferred detailed validators are subordinate to scripts/validate_deferred_architecture.py.
# scripts/validate_executor_foundation_contracts.py
# scripts/validate_operations_evidence_executor_intake.py
# scripts/validate_control_supervision_threshold_sandbox.py
# scripts/validate_i0_5_1_runtime_promotion_readiness.py
# scripts/validate_i0_5_2_runtime_authoritative_read_only_entry.py
# scripts/validate_i0_5_3_runtime_entry_authorization.py
# scripts/validate_i0_5_4_protected_governance_state.py
# scripts/validate_i0_5_5_disabled_single_read_only_entry_integration.py

ROOT = Path(__file__).resolve().parents[1]
VALIDATION_PYTHON = os.environ.get("THOMAS_VALIDATION_PYTHON", sys.executable)
EVIDENCE_REL = "generated/release_gate/RELEASE_GATE_EVIDENCE.yaml"
GIT_EXECUTABLE = os.environ.get("THOMAS_GIT") or shutil.which("git") or "git"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(
    command: list[str],
    label: str,
    *,
    timeout: int = 300,
) -> dict[str, str]:
    # Use a regular temporary file instead of PIPE capture.
    # This avoids pipe-EOF deadlocks when a validator launches a nested
    # subprocess that briefly inherits stdout/stderr.
    child_env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "THOMAS_GIT": GIT_EXECUTABLE,
    }
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output_file:
        proc = subprocess.run(
            command,
            cwd=ROOT,
            stdout=output_file,
            stderr=subprocess.STDOUT,
            text=True,
            env=child_env,
            timeout=timeout,
        )
        output_file.flush()
        output_file.seek(0)
        output = output_file.read().strip()

    print(f"\n=== {label} ===")
    if output:
        print(output)
    if proc.returncode != 0:
        raise RuntimeError(f"{label} failed: {' '.join(command)}")

    return {
        "check_id": label.lower().replace(" ", "_"),
        "label": label,
        "command": " ".join(command),
        "result": "PASS",
        "output_sha256": "sha256:" + hashlib.sha256(output.encode("utf-8")).hexdigest(),
    }


def git_has_head() -> bool:
    proc = subprocess.run(
        [GIT_EXECUTABLE, "rev-parse", "--verify", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode == 0


def git_diff_checks() -> list[dict[str, str]]:
    if not (ROOT / ".git").exists():
        print("\n=== Git Diff Checks ===")
        print("SKIP: .git directory not present")
        return [{
            "check_id": "git_diff_checks",
            "label": "Git Diff Checks",
            "command": "not_applicable_without_git",
            "result": "PASS",
            "output_sha256": "sha256:" + hashlib.sha256(b"SKIP:no_git").hexdigest(),
        }]

    results = [
        run([GIT_EXECUTABLE, "diff", "--check"], "Git Working Diff Check", timeout=60),
        run([GIT_EXECUTABLE, "diff", "--cached", "--check"], "Git Staged Diff Check", timeout=60),
    ]
    if git_has_head():
        results.append(
            run([GIT_EXECUTABLE, "diff", "HEAD", "--check"], "Git HEAD Diff Check", timeout=60)
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Thomas Agent repository-wide compatibility Release Gate once and write "
            "reusable Gate evidence."
        )
    )
    parser.add_argument("--manifest")
    parser.add_argument("--approval")
    parser.add_argument("--activation")
    parser.add_argument("--current-pointer")
    parser.add_argument("--require-current-committed", action="store_true")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Run the full Gate without writing new Release Gate evidence. Intended for CI.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Also run the isolated end-to-end lifecycle self-test after writing normal Gate evidence.",
    )
    args = parser.parse_args()
    python = VALIDATION_PYTHON

    checks = [
        (label, [python, *command])
        for label, command in REPOSITORY_RELEASE_CHECKS
    ]

    evidence_checks: list[dict[str, str]] = []
    for label, command in checks:
        evidence_checks.append(run(command, label))

    release_command = [python, "scripts/validate_core_release_reproducibility.py"]
    if args.manifest:
        release_command += ["--manifest", args.manifest, "--require-manifest"]
    if args.approval:
        release_command += ["--approval", args.approval, "--require-approved"]
    if args.activation:
        release_command += ["--activation", args.activation, "--require-activation"]
    if args.current_pointer:
        release_command += ["--current-pointer", args.current_pointer, "--require-current"]
    if args.require_current_committed:
        release_command.append("--require-current-committed")

    evidence_checks.append(run(release_command, "Core Release Reproducibility"))
    evidence_checks.extend(git_diff_checks())

    # Run the nested-process idempotency test last. Some process hosts keep
    # inherited descriptors from its child apply runs alive briefly; no later
    # validator subprocess should depend on those descriptors closing.
    evidence_checks.append(
        run(
            [python, "scripts/test_apply_core_idempotency.py"],
            "Core Apply Idempotency",
        )
    )

    source_fingerprint, source_entries = repository_source_fingerprint(ROOT)
    evidence = {
        "schema_version": "thomas_release_gate_evidence.v0.1",
        "result": "PASS",
        "generated_at_utc": utc_now(),
        "generated_by": "scripts/run_repository_release_gate.py",
        "repository_source_fingerprint": source_fingerprint,
        "source_file_count": len(source_entries),
        "checks": evidence_checks,
        "scope": {
            "authorizes_release_build_for_unchanged_source": True,
            "grants_core_approval": False,
            "grants_core_activation": False,
            "grants_execution_permission": False,
        },
    }
    if args.check_only:
        print("\nPASS: Thomas Agent repository-wide Release Gate completed in check-only mode")
        print("No Release Gate evidence was written. This mode grants no Release, Core, Runtime, or execution authority.")
    else:
        evidence_path = write_gate_evidence(ROOT, EVIDENCE_REL, evidence)
        print("\nPASS: Thomas Agent repository-wide Release Gate completed")
        print("Gate evidence: " + evidence_path.relative_to(ROOT).as_posix())
        print("The Release Builder will reuse this evidence only while the repository source fingerprint is unchanged.")

    if args.full:
        run(
            [python, "scripts/self_test_core_release_flow.py"],
            "I0.4.1 Lean End-to-End Self-Test",
            timeout=900,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
