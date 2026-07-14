from __future__ import annotations

from pathlib import Path
from typing import Any

from .integrity import scan_for_secret_bearing_keys
from .io import ReadBoundaryError, load_bundle, read_record_snapshot
from .types import ReadCounter


def load_runtime_inputs(
    *,
    repo_root: Path,
    bundle_path: Path,
    read_counter: ReadCounter,
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, str],
    Path,
]:
    resolved_bundle = bundle_path.resolve(strict=True)
    try:
        resolved_bundle.relative_to(repo_root)
    except ValueError as exc:
        raise ReadBoundaryError("input bundle must be located inside repo root") from exc

    bundle, _ = read_record_snapshot(resolved_bundle)
    read_counter.add(1)
    scan_for_secret_bearing_keys(bundle)

    records, actual_hashes, record_read_count = load_bundle(repo_root, bundle)
    read_counter.add(record_read_count)
    return bundle, records, actual_hashes, resolved_bundle
