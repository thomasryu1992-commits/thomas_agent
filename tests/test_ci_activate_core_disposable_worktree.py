"""The ephemeral Core activation's disposable environment is the script's own.

`ci_activate_core_for_tests.py` used to commit the ephemeral approval into the invoking
checkout — honest on CI's throwaway clone, a trap everywhere else. The "not for push"
commit rode along on the next `git push` (leaked to main via #785/#787, removed by #788,
nearly again in #805), and a dirty invoking tree failed the clean-worktree provenance
check, leaving no pointer and ~195 tests silently skipping.

Now the script adds a disposable git worktree detached at HEAD, activates inside it, and
copies only the (gitignored) records back. These tests pin the two properties that make
that safe: the invoking branch and status are untouched by an activation, and the
copy-back lands the pointer last so a half-finished copy fails closed to
CORE_NOT_ACTIVATED instead of a pointer with dangling references.
"""

from __future__ import annotations

import subprocess

import pytest

from scripts import ci_activate_core_for_tests as activate


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=activate.ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_activation_leaves_the_invoking_branch_untouched():
    """The whole point of the disposable worktree, asserted by what it does: a real
    approve → commit → activate run, after which the invoking tree's HEAD, status, and
    worktree registrations are byte-for-byte what they were."""
    head_before = _git("rev-parse", "HEAD")
    status_before = _git("status", "--porcelain")
    worktrees_before = _git("worktree", "list", "--porcelain")

    with activate._disposable_worktree() as worktree:
        record_rels = activate._activate_in(worktree)
        for rel in record_rels:
            assert (worktree / rel).is_file(), f"activation did not produce {rel}"
        assert (worktree / activate.IN_TREE_POINTER_REL).is_file()
        left_behind = worktree

    assert not left_behind.exists(), "the disposable worktree survived its context"
    assert _git("rev-parse", "HEAD") == head_before, "the activation committed to the invoking tree"
    assert _git("status", "--porcelain") == status_before, "the activation dirtied the invoking tree"
    assert _git("worktree", "list", "--porcelain") == worktrees_before, "a worktree registration leaked"


def _fake_worktree(tmp_path, *, records, with_pointer=True):
    worktree = tmp_path / "wt"
    for rel in records:
        path = worktree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"record: {rel}\n", encoding="utf-8")
    if with_pointer:
        pointer = worktree / activate.IN_TREE_POINTER_REL
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text("pointer: yes\n", encoding="utf-8")
    return worktree


def test_copy_back_lands_every_record_and_the_pointer(tmp_path):
    records = ["THOMAS_CORE/approvals/a.yaml", "THOMAS_CORE/activations/b.yaml"]
    worktree = _fake_worktree(tmp_path, records=records)
    dest = tmp_path / "dest"

    activate._copy_back(worktree, records, dest)

    for rel in records:
        assert (dest / rel).read_text(encoding="utf-8") == f"record: {rel}\n"
    assert (dest / activate.POINTER_REL).read_text(encoding="utf-8") == "pointer: yes\n"
    staged = dest / (activate.POINTER_REL + ".tmp")
    assert not staged.exists(), "the pointer's staging file was left behind"


def test_a_half_finished_copy_fails_closed_to_no_pointer(tmp_path):
    """A missing record aborts the copy BEFORE the pointer lands: the destination stays
    CORE_NOT_ACTIVATED rather than becoming a pointer whose references dangle."""
    present = "THOMAS_CORE/approvals/a.yaml"
    missing = "THOMAS_CORE/activations/never-written.yaml"
    worktree = _fake_worktree(tmp_path, records=[present])
    dest = tmp_path / "dest"

    with pytest.raises(OSError):
        activate._copy_back(worktree, [present, missing], dest)

    assert not (dest / activate.POINTER_REL).exists(), "the pointer landed despite a missing record"
