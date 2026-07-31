"""Where a Core-lifecycle lock lives, and why the answer had to stop being a guess.

The four scripts that approve, activate, deactivate and revoke a Core Release serialize
themselves on a lock file, and each built its path as ``ROOT / ".git/thomas_agent_locks/…"``.
``.git`` is a directory in a clone and a **file** in a ``git worktree`` checkout, so
``mkdir`` under it raised a bare ``NotADirectoryError`` from inside ``pathlib`` — naming
neither git, nor the worktree, nor what to do about it.

That is worse than an ordinary portability bug because of *where* it lands. When live
services are writing this tree's state directory, the suite refuses to run and tells the
operator to use a worktree (``tests/conftest.py``); CI parity there needs a Core, because
~200 tests are gated on one. So the repository's own documented recovery path ended in a
stack trace, and the fix for "I cannot run the tests here" was itself untestable.

The logic to resolve both forms already existed one directory over, privately, in
``runtime_promotion_readiness``. One concept, two implementations, and the copy that
governed Core activation was the wrong one.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from runtime.mvp_runtime import _scripts_bridge  # noqa: F401  (puts scripts/ on sys.path)
from runtime.mvp_runtime.paths import repo_root

from lib.safe_io import LOCK_DIR_NAME, SafeIOError, exclusive_lock, git_directory, repo_lock_dir

CORE_LIFECYCLE_SCRIPTS = (
    "activate_core_release.py",
    "approve_core_release.py",
    "deactivate_core_release.py",
    "revoke_core_approval.py",
)


# --- resolving the git directory -------------------------------------------------

def test_a_clone_resolves_to_its_own_git_directory(tmp_path):
    (tmp_path / ".git").mkdir()
    assert git_directory(tmp_path) == (tmp_path / ".git").resolve()


def test_a_worktree_resolves_through_its_gitdir_marker(tmp_path):
    """The form that broke: one line naming the worktree's git directory elsewhere."""
    elsewhere = tmp_path / "main" / ".git" / "worktrees" / "w1"
    elsewhere.mkdir(parents=True)
    checkout = tmp_path / "w1"
    checkout.mkdir()
    (checkout / ".git").write_text(f"gitdir: {elsewhere}\n", encoding="utf-8")
    assert git_directory(checkout) == elsewhere


def test_a_relative_gitdir_resolves_against_the_checkout(tmp_path):
    """Git writes a relative marker for submodules; resolving it against the process's
    working directory instead of the checkout would silently point somewhere else."""
    checkout = tmp_path / "w2"
    (checkout / "gitdir_target").mkdir(parents=True)
    (checkout / ".git").write_text("gitdir: gitdir_target\n", encoding="utf-8")
    assert git_directory(checkout) == (checkout / "gitdir_target").resolve()


@pytest.mark.parametrize("marker", ["not a gitdir line\n", "gitdir:\n", "gitdir:   \n"])
def test_an_unusable_marker_is_a_typed_error_not_a_stack_trace(tmp_path, marker):
    """Fail-closed with something that names the problem. The old path did not fail here at
    all — it failed later, in ``pathlib``, on a `mkdir` that had no idea it was about git."""
    checkout = tmp_path / "w3"
    checkout.mkdir()
    (checkout / ".git").write_text(marker, encoding="utf-8")
    with pytest.raises(SafeIOError):
        git_directory(checkout)


def test_no_git_metadata_at_all_is_a_typed_error(tmp_path):
    with pytest.raises(SafeIOError):
        git_directory(tmp_path)


# --- the lock the four scripts take ----------------------------------------------

def test_the_lock_directory_sits_inside_the_resolved_git_directory(tmp_path):
    """Inside ``.git`` on purpose: a lock is per-machine state that must never be
    committable, and ``.git`` is the one place in a checkout that is structurally
    uncommittable rather than merely ignored."""
    (tmp_path / ".git").mkdir()
    assert repo_lock_dir(tmp_path) == (tmp_path / ".git").resolve() / LOCK_DIR_NAME


def test_a_worktree_can_actually_take_the_lock(tmp_path):
    """The regression itself, end to end. Against the old path the lock could not be taken at
    all, and with it went Core activation — and therefore the ~200 Core-gated tests — for
    every worktree."""
    elsewhere = tmp_path / "main" / ".git" / "worktrees" / "w4"
    elsewhere.mkdir(parents=True)
    checkout = tmp_path / "w4"
    checkout.mkdir()
    (checkout / ".git").write_text(f"gitdir: {elsewhere}\n", encoding="utf-8")

    # The old path construction, kept here so this test states the bug rather than merely
    # asserting the cure. `.git` is a file, so creating a directory under it cannot work.
    #
    # Asserted as `OSError` and *not* `SafeIOError` rather than as a specific subclass,
    # because which one you get is the whole complaint: Linux raises `NotADirectoryError`,
    # Windows raises `FileNotFoundError` or `FileExistsError` depending on how far `mkdir`
    # got. Pinning the Linux spelling is what turned this test red on windows-latest after it
    # merged — the assertion was more specific than the thing it describes. An untyped OS
    # error whose identity varies by platform is exactly why this path owed a typed one.
    with pytest.raises(OSError) as raised:
        with exclusive_lock(checkout / ".git" / LOCK_DIR_NAME / "core_activation.lock"):
            pass
    assert not isinstance(raised.value, SafeIOError)

    lock = repo_lock_dir(checkout) / "core_activation.lock"
    with exclusive_lock(lock):
        assert lock.is_file()
    assert not lock.exists()                      # released, not merely created


def test_two_worktrees_do_not_serialize_against_each_other(tmp_path):
    """Per checkout, not per repository. A worktree has its own ``THOMAS_CORE/activations/``
    and its own ``.runtime_governance_state/``, so two of them activating a Core touch
    disjoint files — sharing one lock would serialize work that never conflicts."""
    locks = []
    for name in ("w5", "w6"):
        elsewhere = tmp_path / "main" / ".git" / "worktrees" / name
        elsewhere.mkdir(parents=True)
        checkout = tmp_path / name
        checkout.mkdir()
        (checkout / ".git").write_text(f"gitdir: {elsewhere}\n", encoding="utf-8")
        locks.append(repo_lock_dir(checkout))
    assert locks[0] != locks[1]


# --- no second implementation ----------------------------------------------------

def _module(name: str) -> ast.Module:
    return ast.parse((repo_root() / "scripts" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("script", CORE_LIFECYCLE_SCRIPTS)
def test_no_core_lifecycle_script_builds_a_git_path_by_hand(script):
    """The shape that broke, refused at the source. Any string literal naming a path inside
    ``.git`` is one of these four assuming the directory form again."""
    offenders = [
        node.value for node in ast.walk(_module(script))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value.startswith(".git/")
    ]
    assert offenders == [], f"{script} builds a .git path by hand: {offenders}"


@pytest.mark.parametrize("script", CORE_LIFECYCLE_SCRIPTS)
def test_every_core_lifecycle_script_routes_through_the_one_resolver(script):
    imported = {
        alias.name
        for node in ast.walk(_module(script))
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("safe_io")
        for alias in node.names
    }
    assert "repo_lock_dir" in imported, f"{script} does not use the shared lock resolver"


def test_the_readiness_module_kept_no_private_copy():
    """It had the worktree-aware logic all along, one directory from the scripts that broke
    without it. It now delegates, so the two cannot drift back apart."""
    from lib import runtime_promotion_readiness as rpr

    source = ast.parse(Path(rpr.__file__).read_text(encoding="utf-8"))
    resolver = next(
        node for node in ast.walk(source)
        if isinstance(node, ast.FunctionDef) and node.name == "_git_directory"
    )
    calls = {
        node.func.id for node in ast.walk(resolver)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "git_directory" in calls
