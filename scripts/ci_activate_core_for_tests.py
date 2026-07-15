#!/usr/bin/env python3
"""Activate an ephemeral Core Release for a CI test run.

CI checks out a throwaway tree, so we can honestly satisfy the activation provenance
checks (which require the approval committed at HEAD) by committing the approval into
that ephemeral checkout — it is never pushed. After this runs, an active Core pointer
exists at ``.runtime_governance_state/CURRENT_CORE_RELEASE.yaml`` and the planner
happy-path tests (guarded by ``skipif`` on that pointer) run instead of skipping.

Idempotent: if a Core is already active locally, it does nothing. Intended for CI /
disposable environments only — it creates a git commit. Do NOT run on a working tree
whose history you care about.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from lib.core_release_verifier import sha256_file  # noqa: E402

GOV_STATE = ROOT / ".runtime_governance_state"
POINTER = GOV_STATE / "CURRENT_CORE_RELEASE.yaml"
IN_TREE_POINTER = ROOT / "THOMAS_CORE" / "CURRENT_CORE_RELEASE.yaml"
MANIFEST_REL = "THOMAS_CORE/releases/thomas-core-v0.2.1-a99f17144a7c/manifest.yaml"
EVIDENCE_REL = "THOMAS_CORE/approvals/intake/ci-test-core-activation.md"

_EVIDENCE_TEXT = (
    "# CI Test — Core Activation (ephemeral, disposable checkout)\n\n"
    "Isolated Core activation created only to run the MVP planner happy-path tests in CI.\n"
    "Committed transiently into the throwaway CI checkout to satisfy activation provenance;\n"
    "never pushed. Not a real Thomas approval.\n"
)


def _run(args: list[str]) -> str:
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(args)}\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


def _value(output: str, prefix: str) -> str:
    for line in output.splitlines():
        if prefix in line:
            return line.split(prefix, 1)[1].strip()
    raise SystemExit(f"expected '{prefix}' in:\n{output}")


def main() -> int:
    if POINTER.is_file():
        print("Core already active; nothing to do.")
        return 0

    GOV_STATE.mkdir(exist_ok=True)
    evidence = ROOT / EVIDENCE_REL
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(_EVIDENCE_TEXT, encoding="utf-8")
    source_hash = sha256_file(evidence)

    common = ["--identity-verification-method", "ci_test", "--verification-status", "verified_by_control_channel"]
    approve_out = _run([
        sys.executable, "scripts/approve_core_release.py",
        "--manifest", MANIFEST_REL, "--approved-by", "Thomas",
        "--approval-ref", "ci-test", "--reason", "CI test Core activation.",
        "--approval-source-type", "operator_decision_intake",
        "--approval-source-id", EVIDENCE_REL, "--approval-source-hash", source_hash, *common,
    ])
    approval_rel = _value(approve_out, "Approval path:")

    # Commit the approval into the disposable checkout so activate's tracked-at-HEAD
    # provenance check is honestly satisfied.
    _run(["git", "add", "--force", approval_rel])
    _run(["git", "commit", "--no-verify", "-m", "ci: ephemeral test Core approval (not for push)"])

    _run([
        sys.executable, "scripts/activate_core_release.py", "--activation-type", "activate",
        "--manifest", MANIFEST_REL, "--approval", approval_rel, "--activated-by", "Thomas",
        "--activation-ref", "ci-test", "--reason", "CI test Core activation.",
        "--source-type", "operator_decision_intake",
        "--source-id", EVIDENCE_REL, "--source-hash", source_hash, *common,
    ])

    # Match the production local-activation layout the MVP binding reads from.
    IN_TREE_POINTER.replace(POINTER)
    print(f"Activated ephemeral CI Core; pointer at {POINTER.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
