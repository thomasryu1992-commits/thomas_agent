"""R5.2 Working-memory store — durable, per-machine, read-only-by-policy.

R5.1 lets the specialist *propose* memory candidates; this store lets those candidates
**accumulate across runs** and be **retrieved** as context for a later task. It is a thin
append-only JSONL store under a local, gitignored directory (mirroring the ledger and the
Core pointer): working memory is machine state, not shared source.

Governance: only ``task_working_memory``-scoped candidates are read, only when the
assignment's ``readable_scopes`` admits that scope and the entry is not in a
``prohibited_scopes``; retrieval is read-only and never promotes anything. Working memory is
**opt-in** at the pipeline level (a store must be supplied) so a run with no store stays pure
and deterministic — accumulation only happens when a caller (the CLI) provides the store.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from . import jsonl, memory
from .paths import repo_root as _repo_root

WORKING_MEMORY_REL = ".runtime_governance_state/working_memory"
ENTRIES_FILE = "candidates.jsonl"
VALIDATED_FILE = "validated.jsonl"      # R5: promoted (validated) memory — separate from candidates


class WorkingMemoryStore:
    """Append-only JSONL store of working-memory candidates, rooted at a directory."""

    def __init__(self, root: Path):
        self._root = Path(root)

    @classmethod
    def default(cls) -> "WorkingMemoryStore":
        return cls(_repo_root() / WORKING_MEMORY_REL)

    @property
    def root(self) -> Path:
        return self._root

    def append(self, entries: list[Mapping[str, Any]]) -> None:
        """Append candidate entries (append-only; fail-closed on write error)."""
        self._append(ENTRIES_FILE, entries, "WORKING_MEMORY_WRITE_FAILED", "working memory")

    def read_all(self) -> list[dict[str, Any]]:
        """Return every stored candidate. A corrupt/unreadable store fails closed rather than
        silently returning partial memory."""
        return self._read(ENTRIES_FILE, "WORKING_MEMORY_UNREADABLE", "working memory")

    def append_validated(self, entries: list[Mapping[str, Any]]) -> None:
        """Append VALIDATED (promoted) memory entries to the separate validated store. Only the
        explicit operator promotion path writes here — nothing in the run pipeline does."""
        self._append(VALIDATED_FILE, entries, "VALIDATED_MEMORY_WRITE_FAILED", "validated memory")

    def read_validated(self) -> list[dict[str, Any]]:
        """Return every promoted (VALIDATED) memory entry. Fail-closed on a corrupt store."""
        return self._read(VALIDATED_FILE, "VALIDATED_MEMORY_UNREADABLE", "validated memory")

    def prune_expired(self, now: str) -> list[dict[str, Any]]:
        """Delete expired candidates (policy §12.4 retention) and return the removed entries.

        Compacts ``candidates.jsonl`` to only the entries still live as of ``now`` (atomic
        rewrite). Entries without an ``expires_at`` are kept (see ``memory.is_expired``). Only
        the candidate store is pruned — promoted VALIDATED memory has its own longer retention
        and is never touched here. Returns the removed entries so the caller can audit the
        deletion (policy §15). Fail-closed on a read/rewrite error (``PersistenceError``)."""
        entries = self.read_all()
        kept = [e for e in entries if not memory.is_expired(e, now)]
        removed = [e for e in entries if memory.is_expired(e, now)]
        if removed:
            jsonl.write_objects(self._root / ENTRIES_FILE, kept,
                                write_code="WORKING_MEMORY_WRITE_FAILED", label="working memory")
        return removed

    def _append(self, filename: str, entries: list[Mapping[str, Any]], code: str, label: str) -> None:
        if not entries:
            return
        jsonl.append_lines(self._root / filename, entries, write_code=code, label=label)

    def _read(self, filename: str, code: str, label: str) -> list[dict[str, Any]]:
        return jsonl.read_objects(self._root / filename, read_code=code, label=label)
