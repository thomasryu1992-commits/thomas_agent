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

from . import jsonl, memory, timeutil
from .filelock import locked
from .paths import repo_root as _repo_root

WORKING_MEMORY_REL = ".runtime_governance_state/working_memory"
ENTRIES_FILE = "candidates.jsonl"
VALIDATED_FILE = "validated.jsonl"      # R5: promoted (validated) memory — separate from candidates
# The fourth rung (policy §12.6): proposed Core changes. Its own file for the same reason
# VALIDATED has one — retention compacts only `candidates.jsonl`, so a rung that must never be
# aged out simply is not in the file the prune rewrites.
CORE_CANDIDATES_FILE = "core_candidates.jsonl"
_CANDIDATES_LOCK = ".candidates.lock"   # serializes candidate appends vs. the prune rewrite


def _dedup_key(entry: Mapping[str, Any]) -> tuple[str, str] | None:
    """The identity of an analysis candidate for duplicate purposes, or None to skip the check.

    None for everything that is not a live analysis proposal: the promotion marker (same
    content, ``status=PROMOTED``), the front desk's session turns (their own scope), and any
    row with no content to compare. Whitespace is collapsed because the same finding arrives
    wrapped differently from different runs; nothing else is normalised, so two findings that
    differ by a word stay two findings."""
    if entry.get("scope") != memory.CANDIDATE_SCOPE:
        return None
    if entry.get("status") != memory.CANDIDATE_STATUS:
        return None
    content = " ".join(str(entry.get("content") or "").split())
    if not content:
        return None
    return (str(entry.get("candidate_type") or ""), content)


def _reference_now(entries: list[Mapping[str, Any]]) -> str:
    """The instant to judge "still live" against, taken from the batch itself when it says.

    Callers stamp every candidate with the run's ``now``, so using it keeps the decision
    deterministic and replayable instead of reaching for the wall clock mid-write."""
    stamps = [str(e.get("created_at")) for e in entries if e.get("created_at")]
    return max(stamps) if stamps else timeutil.utc_now_iso()


class WorkingMemoryStore:
    """Append-only JSONL store of working-memory candidates, rooted at a directory."""

    def __init__(self, root: Path):
        self._root = Path(root)

    @classmethod
    def default(cls, root: Path | None = None) -> "WorkingMemoryStore":
        return cls((root if root is not None else _repo_root()) / WORKING_MEMORY_REL)

    @property
    def root(self) -> Path:
        return self._root

    def append(self, entries: list[Mapping[str, Any]], *, now: str | None = None
               ) -> list[Mapping[str, Any]]:
        """Append candidate entries, skipping ones already live. Returns what was written.

        Held under the candidates lock: the prune compaction is a read-modify-replace, so
        an unlocked append racing it (operator loop vs. a scheduled ``memory_prune`` in
        another process) would be silently discarded by the rewrite. The duplicate check
        reads inside that same section, so it cannot decide against a store another process
        is mid-rewrite.

        **Why a duplicate check at all.** The store was append-only with none, and
        ``candidate_id`` is derived from the originating task, so the SAME finding proposed
        by twenty different runs stored twenty rows with twenty ids — nothing could see they
        were one fact. Measured on this host 2026-08-24: 316 rows, 129 distinct texts, with
        five of them repeated **38 times each** for 170 of the rows. Pruning that is a
        counter reset; the store fills back up because nothing here ever compared two
        candidates.

        **Live, not ever.** A candidate is a duplicate of an entry that has not expired. A
        finding re-derived after its TTL ran out is a fact being observed again, and it gets
        a fresh row with a fresh TTL — which is what the TTL is for. Deduplicating against
        expired rows would let a store nobody has pruned silently suppress new observations.

        **Only the analysis rung.** The check applies to ``task_working_memory``-scoped rows
        with ``status=CANDIDATE`` — where the duplication is. Two neighbours look identical
        and must not be touched: ``mark_promoted`` writes a copy of the candidate
        that differs only in ``status``, and the front desk's session turns are a different
        scope whose repeated lines are conversation, not knowledge.

        Returns the entries actually written, so a caller that cares can see the difference
        between "stored" and "already knew that"."""
        if not entries:
            return []
        with self._candidates_lock():
            reference = now or _reference_now(entries)
            live = {
                _dedup_key(e) for e in self.read_all()
                if _dedup_key(e) is not None and not memory.is_expired(e, reference)
            }
            fresh: list[Mapping[str, Any]] = []
            for entry in entries:
                key = _dedup_key(entry)
                if key is not None:
                    if key in live:
                        continue
                    live.add(key)       # a batch can carry the same finding twice
                fresh.append(entry)
            if not fresh:
                return []
            self._append(ENTRIES_FILE, fresh, "WORKING_MEMORY_WRITE_FAILED", "working memory")
        return fresh

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

    def append_core_candidates(self, entries: list[Mapping[str, Any]]) -> None:
        """Append Core Candidate proposals and decision rows (policy §12.6). Append-only, so a
        decision is a new row rather than an edit. Only the explicit operator door writes here —
        `automatic_runtime_promotion_allowed` is false, so nothing in the run pipeline does."""
        self._append(CORE_CANDIDATES_FILE, entries, "CORE_CANDIDATE_WRITE_FAILED", "core candidates")

    def read_core_candidates(self) -> list[dict[str, Any]]:
        """Return every Core Candidate row, proposals and decisions alike. Fail-closed."""
        return self._read(CORE_CANDIDATES_FILE, "CORE_CANDIDATE_UNREADABLE", "core candidates")

    def prune_expired(self, now: str) -> list[dict[str, Any]]:
        """Delete expired candidates (policy §12.4 retention) and return the removed entries.

        Compacts ``candidates.jsonl`` to only the entries still live as of ``now`` (atomic
        rewrite). Entries without an ``expires_at`` are kept (see ``memory.is_expired``). Only
        the candidate store is pruned — promoted VALIDATED memory has its own longer retention
        and is never touched here. Returns the removed entries so the caller can audit the
        deletion (policy §15). Fail-closed on a read/rewrite error (``PersistenceError``)."""
        # The read and the replacing rewrite are one critical section: without the lock, a
        # candidate appended by another process between them vanishes without trace — a
        # deletion the retention event never records.
        with self._candidates_lock():
            entries = self.read_all()
            kept = [e for e in entries if not memory.is_expired(e, now)]
            removed = [e for e in entries if memory.is_expired(e, now)]
            if removed:
                jsonl.write_objects(self._root / ENTRIES_FILE, kept,
                                    write_code="WORKING_MEMORY_WRITE_FAILED", label="working memory")
        return removed

    def _candidates_lock(self):
        return locked(self._root / _CANDIDATES_LOCK,
                      code="WORKING_MEMORY_WRITE_FAILED", label="working memory")

    def _append(self, filename: str, entries: list[Mapping[str, Any]], code: str, label: str) -> None:
        if not entries:
            return
        jsonl.append_lines(self._root / filename, entries, write_code=code, label=label)

    def _read(self, filename: str, code: str, label: str) -> list[dict[str, Any]]:
        return jsonl.read_objects(self._root / filename, read_code=code, label=label)


def find_candidate(store: WorkingMemoryStore, candidate_id: str) -> dict[str, Any] | None:
    """The live working-memory CANDIDATE with this id, or None.

    THE candidate lookup — the approval ask (approval_cli) and the approval spend
    (consumption) must resolve "the candidate" identically, or the ask can bind content
    the spend then rejects. Only an un-promoted candidate in the working-memory scope is
    eligible, and **latest-wins across every entry for the id, whatever its status**: the
    store is append-only, so the last entry is the candidate's current state. Revalidation
    must run against current content, not a superseded earlier copy — otherwise a candidate
    re-appended with tampered content after the approval would slip past the content-hash
    check at consume time. A candidate whose latest entry is the PROMOTED retirement marker
    (:func:`mark_promoted`) is no longer live: promotion consumes the candidate, so a second
    ask/spend for the same id resolves to nothing instead of re-promoting the same content."""
    latest: dict[str, Any] | None = None
    for entry in store.read_all():
        if (isinstance(entry, dict)
                and entry.get("candidate_id") == candidate_id
                and entry.get("scope") == memory.CANDIDATE_SCOPE):
            latest = entry
    if latest is None or latest.get("status") != memory.CANDIDATE_STATUS:
        return None
    return latest


def find_validated(store: WorkingMemoryStore, validated_memory_id: str) -> dict[str, Any] | None:
    """The VALIDATED memory entry with this id, or None.

    Latest-wins for the same reason :func:`find_candidate` is: the store is append-only, so the
    last row for an id is its current state."""
    latest: dict[str, Any] | None = None
    for entry in store.read_validated():
        if (isinstance(entry, dict)
                and entry.get("validated_memory_id") == validated_memory_id
                and entry.get("scope") == memory.VALIDATED_SCOPE
                and entry.get("status") == memory.VALIDATED_STATUS):
            latest = entry
    return latest


def find_core_candidate(store: WorkingMemoryStore, core_candidate_id: str) -> dict[str, Any] | None:
    """The **live** (undecided) Core Candidate with this id, or None.

    Latest-wins across every row for the id, whatever its status — the append-only decision row
    is what retires a proposal, exactly as the PROMOTED marker retires a working-memory
    candidate. A decided proposal resolves to nothing, so a second decision cannot be recorded
    against it and ACCEPTED/REJECTED stay terminal."""
    latest: dict[str, Any] | None = None
    for entry in store.read_core_candidates():
        if (isinstance(entry, dict)
                and entry.get("core_candidate_id") == core_candidate_id
                and entry.get("scope") == memory.CORE_CANDIDATE_SCOPE):
            latest = entry
    if latest is None or latest.get("status") != memory.CORE_CANDIDATE_STATUS:
        return None
    return latest


def live_core_candidates(store: WorkingMemoryStore) -> list[dict[str, Any]]:
    """Every Core Candidate still awaiting a decision, oldest first.

    Resolved by the same latest-wins rule as :func:`find_core_candidate` rather than by
    filtering rows on status: filtering would list a proposal *and* its decision row, and would
    keep showing a decided proposal as open."""
    latest: dict[str, dict[str, Any]] = {}
    for entry in store.read_core_candidates():
        if isinstance(entry, dict) and entry.get("scope") == memory.CORE_CANDIDATE_SCOPE:
            candidate_id = entry.get("core_candidate_id")
            if isinstance(candidate_id, str) and candidate_id:
                latest[candidate_id] = entry
    live = [e for e in latest.values() if e.get("status") == memory.CORE_CANDIDATE_STATUS]
    live.sort(key=lambda e: (str(e.get("created_at", "")), str(e.get("core_candidate_id", ""))))
    return live


def mark_promoted(
    store: WorkingMemoryStore,
    candidate: Mapping[str, Any],
    *,
    validated_memory_id: str,
    now: str,
) -> dict[str, Any]:
    """Append the PROMOTED retirement marker for a just-promoted candidate.

    The candidate store is append-only, so promotion cannot edit the CANDIDATE row in
    place; the marker is the append-only equivalent of retiring it — the id's latest entry
    stops being CANDIDATE, so :func:`find_candidate` stops returning it. Without this,
    nothing ever consumed the candidate: the same content could be asked-for and promoted
    repeatedly (each round needing a fresh approval, but still yielding duplicate VALIDATED
    entries), and CANDIDATE_GONE's "promoted or pruned" message was only half true. The
    marker copies the candidate (same ``expires_at``), so retention prunes the pair
    together. Retrieval-as-context is deliberately unchanged: the original CANDIDATE row
    still serves until it expires — promotion narrows *promotability*, not context."""
    marker = {
        **dict(candidate),
        "status": memory.PROMOTED_STATUS,
        "promoted_at": now,
        "promotion_ref": f"validated_memory:{validated_memory_id}",
    }
    store.append([marker])
    return marker
