from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .types import KernelContext


class LoadError(RuntimeError):
    pass


def load_context(
    *,
    repo_root: Path,
    input_bundle: Mapping[str, Any],
    created_at: str,
) -> KernelContext:
    resolved_root = repo_root.resolve()
    if not resolved_root.exists() or not resolved_root.is_dir():
        raise LoadError(f"invalid repository root: {resolved_root}")

    return KernelContext(
        repo_root=str(resolved_root),
        created_at=created_at,
        input_bundle=deepcopy(dict(input_bundle)),
    )
