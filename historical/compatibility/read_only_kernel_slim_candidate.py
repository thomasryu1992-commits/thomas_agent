from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from runtime.kernel_slim import run_read_only_kernel

from .worker import execute_contract_inspection_worker


def run_slim_candidate(
    *,
    repo_root: Path,
    input_bundle: Mapping[str, Any],
    governance_policy: Mapping[str, Any],
    created_at: str,
) -> dict[str, Any]:
    """Run the decomposed non-authoritative candidate with the existing worker."""
    return run_read_only_kernel(
        repo_root=repo_root,
        input_bundle=input_bundle,
        governance_policy=governance_policy,
        created_at=created_at,
        worker=execute_contract_inspection_worker,
    )
