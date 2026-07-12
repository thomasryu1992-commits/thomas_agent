from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path


class GitProvenanceError(ValueError):
    pass


GIT_EXECUTABLE = os.environ.get("THOMAS_GIT") or shutil.which("git") or "git"


def git_output(root: Path, *args: str) -> str:
    proc = subprocess.run(
        [GIT_EXECUTABLE, *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if proc.returncode != 0:
        raise GitProvenanceError(
            (proc.stdout + proc.stderr).strip()
            or f"Git command failed: git {' '.join(args)}"
        )

    return proc.stdout.strip()


def require_clean_worktree(root: Path) -> None:
    status = git_output(root, "status", "--porcelain")
    if status:
        raise GitProvenanceError(
            "Git working tree must be clean before this operation."
        )


def head_commit(root: Path) -> str:
    value = git_output(root, "rev-parse", "HEAD")

    if (
        len(value) != 40
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise GitProvenanceError("Unable to resolve a valid 40-character HEAD SHA")

    return value


def require_file_tracked_at_head(
    root: Path,
    path: Path,
) -> dict[str, str]:
    root = root.resolve()
    path = path.resolve()

    try:
        rel = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise GitProvenanceError(
            f"Path is outside Repository root: {path}"
        ) from exc

    git_output(root, "ls-files", "--error-unmatch", "--", rel)

    local_bytes = path.read_bytes()

    proc = subprocess.run(
        [GIT_EXECUTABLE, "show", f"HEAD:{rel}"],
        cwd=root,
        capture_output=True,
        timeout=60,
    )

    if proc.returncode != 0:
        raise GitProvenanceError(
            proc.stderr.decode("utf-8", errors="replace").strip()
            or f"Unable to read HEAD:{rel}"
        )

    head_bytes = proc.stdout

    if head_bytes != local_bytes:
        raise GitProvenanceError(
            f"Working-tree file differs from HEAD: {rel}"
        )

    blob_sha = git_output(root, "rev-parse", f"HEAD:{rel}")

    return {
        "path": rel,
        "blob_sha": blob_sha,
        "sha256": "sha256:" + hashlib.sha256(local_bytes).hexdigest(),
    }


def require_tree_tracked_at_head(
    root: Path,
    directory: Path,
) -> list[dict[str, str]]:
    root = root.resolve()
    directory = directory.resolve()

    try:
        rel_dir = directory.relative_to(root).as_posix()
    except ValueError as exc:
        raise GitProvenanceError(
            f"Directory is outside Repository root: {directory}"
        ) from exc

    files = [
        path
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    ]

    if not files:
        raise GitProvenanceError(f"Tracked tree is empty: {rel_dir}")

    return [
        require_file_tracked_at_head(root, path)
        for path in files
    ]
