"""Single source for repo-root resolution across the MVP runtime.

Every module previously carried its own two-line ``_repo_root()``; they all resolve
the same path. Centralizing it keeps one authority for "where is the repo root" and
removes the most-duplicated snippet in the package.
"""

from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    """Repo root: ``runtime/mvp_runtime/<module>.py`` -> ``parents[2]``."""
    return Path(__file__).resolve().parents[2]
