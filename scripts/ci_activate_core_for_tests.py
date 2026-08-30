#!/usr/bin/env python3
"""Activate an ephemeral Core Release for a CI/local test run.

The activation provenance checks are real and stay real: ``activate_core_release.py``
requires the approval committed at HEAD of a clean tree. This script satisfies them
honestly **without touching the invoking branch** — it adds a disposable ``git worktree``
detached at HEAD, runs approve → commit → activate inside it, copies the resulting
records back (every one of them gitignored), and removes the worktree. The commit that
carries the ephemeral approval lives and dies with that worktree.

It used to commit straight into the invoking checkout, which was honest on CI's
throwaway clone and a trap everywhere else: the "not for push" commit rode along on the
next ``git push`` (leaked to main via #785/#787, removed by #788; nearly again in #805),
and a dirty tree failed the clean-worktree check, leaving no pointer and ~195 tests
silently skipping. Both failure modes were structural — the "disposable environment"
premise belongs to the script, not the caller — so the disposable tree is now the
script's own. The invoking tree gets no commit and does not need to be clean.

After this runs, an active Core pointer exists at
``.runtime_governance_state/CURRENT_CORE_RELEASE.yaml`` and the planner happy-path tests
(guarded by ``skipif`` on that pointer) run instead of skipping.

Idempotent: if a Core is already active locally, it does nothing.

**On a deployment host it needs an explicit escape** (``--allow-foreign-root-run``), because
the state it writes back is the services' to own. Every other state-writing script under
``scripts/`` calls ``state_guard.assert_not_foreign_root_run``; this one could not, because
that guard's remedy — *re-run it under ``docker exec``* — is impossible here: the script
also writes ``THOMAS_CORE/approvals/`` and ``activations/``, which are mounted
**read-only** into the containers, and it makes a git commit against a checkout the
containers do not have. So it was the one writer with no door to refuse at, and a root run
silently left a root-owned pointer behind. See :func:`foreign_root_run_refusal`.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from lib.core_release_verifier import sha256_file  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK  # noqa: E402
from runtime.mvp_runtime.state_guard import STATE_DIR_REL, foreign_state_owner  # noqa: E402

GOV_STATE = ROOT / ".runtime_governance_state"
POINTER_REL = ".runtime_governance_state/CURRENT_CORE_RELEASE.yaml"
POINTER = ROOT / POINTER_REL
# Where activate_core_release.py leaves the pointer inside the worktree; the copy-back
# relocates it to the production local-activation layout the MVP binding reads from.
IN_TREE_POINTER_REL = "THOMAS_CORE/CURRENT_CORE_RELEASE.yaml"
MANIFEST_REL = "THOMAS_CORE/releases/thomas-core-v0.2.1-a99f17144a7c/manifest.yaml"
EVIDENCE_REL = "THOMAS_CORE/approvals/intake/ci-test-core-activation.md"

_EVIDENCE_TEXT = (
    "# CI Test — Core Activation (ephemeral, disposable worktree)\n\n"
    "Isolated Core activation created only to run the MVP planner happy-path tests.\n"
    "Committed transiently into a throwaway git worktree to satisfy activation provenance;\n"
    "the worktree and its commit are discarded. Not a real Thomas approval.\n"
)


def _run(args: list[str], cwd: Path = ROOT) -> str:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(args)}\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


def _value(output: str, prefix: str) -> str:
    for line in output.splitlines():
        if prefix in line:
            return line.split(prefix, 1)[1].strip()
    raise SystemExit(f"expected '{prefix}' in:\n{output}")


@contextmanager
def _disposable_worktree() -> Iterator[Path]:
    """A throwaway git worktree detached at the invoking tree's HEAD.

    Cleanup is best-effort and never masks the activation's own failure: the worktree
    metadata and directory are removed with plain subprocess calls, and a leftover is
    pruned rather than raised over.
    """
    parent = Path(tempfile.mkdtemp(prefix="thomas-ephemeral-core-"))
    worktree = parent / "tree"
    _run(["git", "worktree", "add", "--detach", str(worktree), "HEAD"])
    try:
        yield worktree
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(worktree)],
                       cwd=ROOT, capture_output=True)
        shutil.rmtree(parent, ignore_errors=True)
        subprocess.run(["git", "worktree", "prune"], cwd=ROOT, capture_output=True)


def _activate_in(worktree: Path) -> list[str]:
    """Approve → commit → activate inside the disposable worktree.

    Returns the repo-relative paths of the records the invoking tree needs back
    (approval, activation, evidence — the pointer is handled by the copy-back).
    """
    evidence = worktree / EVIDENCE_REL
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
    ], cwd=worktree)
    approval_rel = _value(approve_out, "Approval path:")

    # Commit the approval so activate's tracked-at-HEAD provenance check is honestly
    # satisfied — inside the worktree, where the commit is discarded with the tree.
    _run(["git", "add", "--force", approval_rel], cwd=worktree)
    _run(["git", "commit", "--no-verify", "-m", "ci: ephemeral test Core approval (not for push)"],
         cwd=worktree)

    activate_out = _run([
        sys.executable, "scripts/activate_core_release.py", "--activation-type", "activate",
        "--manifest", MANIFEST_REL, "--approval", approval_rel, "--activated-by", "Thomas",
        "--activation-ref", "ci-test", "--reason", "CI test Core activation.",
        "--source-type", "operator_decision_intake",
        "--source-id", EVIDENCE_REL, "--source-hash", source_hash, *common,
    ], cwd=worktree)
    activation_id = _value(activate_out, "Activation ID:")

    return [approval_rel, f"THOMAS_CORE/activations/{activation_id}.yaml", EVIDENCE_REL]


def _copy_back(worktree: Path, record_rels: Iterable[str], dest_root: Path) -> None:
    """Copy the activation's records out of the disposable worktree.

    The pointer lands last and atomically: until every record it references is in
    place, the destination never looks activated — a half-finished copy fails closed
    to CORE_NOT_ACTIVATED rather than to a pointer with dangling references.
    """
    for rel in record_rels:
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(worktree / rel, dst)
    pointer_dst = dest_root / POINTER_REL
    pointer_dst.parent.mkdir(exist_ok=True)
    staged = pointer_dst.with_name(pointer_dst.name + ".tmp")
    shutil.copy2(worktree / IN_TREE_POINTER_REL, staged)
    staged.replace(pointer_dst)


def foreign_root_run_refusal(owner_uid: int) -> str:
    """Why this run is refused, and the remedy — which is NOT the guard's usual one.

    ``state_guard.describe_foreign_owner`` tells the operator to re-run under
    ``docker exec thomas-scheduler``, and for the other seven state-writing scripts that is
    exactly right. Here it is a dead end in two independent ways: the container mounts
    ``THOMAS_CORE/approvals`` and ``THOMAS_CORE/activations`` **read-only**, and this script
    makes a git commit against a checkout the container does not have. Calling
    ``assert_not_foreign_root_run`` would therefore have produced a correct refusal with an
    impossible instruction — which is worse than the silence it replaced, because it stops a
    legitimate setup with nowhere to go.

    So the refusal is local, and the remedy is the one that actually works here: run it
    anyway, deliberately, and hand the pointer back afterwards.
    """
    return "\n".join([
        f"BLOCKED STATE_FOREIGN_ROOT_RUN: {STATE_DIR_REL}/ belongs to uid {owner_uid}, which is "
        f"the account the deployed services run as, and this process is root.",
        f"The pointer this writes would be root-owned, and `state_guard.assert_state_writable` "
        f"would then refuse to START either service — one run here bricks both containers at "
        f"their next restart.",
        "",
        "The usual remedy does NOT apply to this script: `docker exec thomas-scheduler` cannot "
        "run it, because THOMAS_CORE/{approvals,activations} are mounted read-only there and "
        "this script also makes a git commit.",
        "",
        "If this host-side root run is deliberate, say so and hand the state back afterwards:",
        f"  python scripts/{Path(__file__).name} --allow-foreign-root-run",
        f"  chown -R {owner_uid}:{owner_uid} {ROOT / STATE_DIR_REL}",
    ])


def _handover_reminder(owner_uid: int) -> str:
    """Printed when an escaped run finishes with the pointer still owned by root.

    Not a chown. ``state_guard`` is explicit that these paths never self-heal — "a script that
    quietly chowned governed state would be granting itself the very thing this repo makes
    explicit everywhere else" — so the script's job is to refuse to call itself done, not to
    fix it. The activation genuinely succeeded; the exit code says the work is not finished.
    """
    return "\n".join([
        f"INCOMPLETE: the Core is active, but {POINTER.relative_to(ROOT).as_posix()} is owned by "
        f"root and uid {owner_uid} cannot write it.",
        f"Finish the handover before starting either service:",
        f"  chown -R {owner_uid}:{owner_uid} {ROOT / STATE_DIR_REL}",
    ])


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ci_activate_core_for_tests",
        description="Activate an ephemeral Core Release for a CI/local test run.",
    )
    parser.add_argument(
        "--allow-foreign-root-run", action="store_true",
        help="explicit escape: run as root against a state directory owned by another uid "
             "(a deployment host). The run then reports INCOMPLETE until the state is chowned back.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Before the worktree, before anything reaches disk: a refused run must leave the
    # tree exactly as it found it.
    owner = foreign_state_owner(ROOT)
    if owner is not None and not args.allow_foreign_root_run:
        sys.stderr.write(foreign_root_run_refusal(owner) + "\n")
        return EXIT_BLOCKED

    if POINTER.is_file():
        print("Core already active; nothing to do.")
        return EXIT_OK

    with _disposable_worktree() as worktree:
        record_rels = _activate_in(worktree)
        _copy_back(worktree, record_rels, ROOT)

    print(f"Activated ephemeral CI Core; pointer at {POINTER.relative_to(ROOT).as_posix()}")

    # Re-read the owner from the pointer that now exists, rather than trusting the check at
    # the top: what matters is who owns the file that landed, and on CI (non-root, or Windows
    # where uids do not exist) there is nothing to check and this is a no-op.
    if owner is not None and _pointer_is_foreign(owner):
        sys.stderr.write(_handover_reminder(owner) + "\n")
        return EXIT_BLOCKED
    return EXIT_OK


def _pointer_is_foreign(owner_uid: int) -> bool:
    """True when the pointer just written cannot be rewritten by the state directory's owner."""
    try:
        return POINTER.stat().st_uid != owner_uid
    except (OSError, AttributeError):
        # No uids on this platform, or the file vanished: nothing to assert.
        return False


if __name__ == "__main__":
    raise SystemExit(main())
