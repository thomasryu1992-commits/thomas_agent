"""Single source for repo-root resolution and path-segment guards across the MVP runtime.

Every module previously carried its own two-line ``_repo_root()``; they all resolve
the same path. Centralizing it keeps one authority for "where is the repo root" and
removes the most-duplicated snippet in the package.
"""

from __future__ import annotations

from pathlib import Path

# Windows resolves these names to devices wherever they appear as a basename, with or
# without an extension, so a file created under one would not be a normal file. Every
# caller-supplied path segment (safety-flag provider ids, workspace write targets) refuses
# them by name rather than leaving them to fail in a confusing way at open() time.
# One authority — safety_gate.py and workspace.py used to carry identical copies.
RESERVED_BASENAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def repo_root() -> Path:
    """Repo root: ``runtime/mvp_runtime/<module>.py`` -> ``parents[2]``."""
    return Path(__file__).resolve().parents[2]
