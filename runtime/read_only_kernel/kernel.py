from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .assembler import build_blocked_run
from .constants import (
    ALLOWED_PERMISSION_DECISIONS,
    AUTHORITY_ORDER,
    KERNEL_ID,
    KERNEL_VERSION,
    REPLAY_TRANSITIONS,
)
from .errors import KernelBlocked
from .integrity import IntegrityError
from .io import ReadBoundaryError
from .loader import load_runtime_inputs
from .orchestrator import run_loaded_replay
from .schema_validation import RuntimeSchemaError
from .types import ReadCounter


class ReadOnlyRuntimeKernel:
    """Thin public facade for the deterministic I0.5 read-only replay Runtime."""

    def __init__(self, repo_root: Path, *, now: str):
        self.repo_root = repo_root.resolve(strict=True)
        try:
            parsed_now = datetime.fromisoformat(now.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise ValueError("now must be an RFC3339 timestamp") from exc
        if parsed_now.tzinfo is None:
            raise ValueError("now must include an RFC3339 timezone")
        self.now = now
        self._filesystem_read_count = 0

    def run(self, bundle_path: Path) -> dict[str, Any]:
        read_counter = ReadCounter(self._filesystem_read_count)
        try:
            bundle, records, actual_hashes, resolved_bundle = load_runtime_inputs(
                repo_root=self.repo_root,
                bundle_path=bundle_path,
                read_counter=read_counter,
            )
            result = run_loaded_replay(
                repo_root=self.repo_root,
                now=self.now,
                read_counter=read_counter,
                bundle=bundle,
                records=records,
                actual_hashes=actual_hashes,
                bundle_path=resolved_bundle,
            )
            self._filesystem_read_count = read_counter.value
            return result
        except KernelBlocked as exc:
            self._filesystem_read_count = read_counter.value
            return build_blocked_run(
                repo_root=self.repo_root,
                now=self.now,
                filesystem_read_count=self._filesystem_read_count,
                bundle_path=bundle_path,
                reason_code=exc.reason_code,
                message=exc.message,
            )
        except (
            OSError,
            ReadBoundaryError,
            IntegrityError,
            RuntimeSchemaError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            self._filesystem_read_count = read_counter.value
            return build_blocked_run(
                repo_root=self.repo_root,
                now=self.now,
                filesystem_read_count=self._filesystem_read_count,
                bundle_path=bundle_path,
                reason_code="INPUT_BUNDLE_INVALID",
                message=str(exc),
            )


def run_bundle(repo_root: Path, bundle_path: Path, *, now: str) -> dict[str, Any]:
    return ReadOnlyRuntimeKernel(repo_root, now=now).run(bundle_path)
