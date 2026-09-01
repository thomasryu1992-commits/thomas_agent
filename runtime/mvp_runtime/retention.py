"""Ledger retention — bound the active files by ARCHIVING, never by deleting.

The record ledger reached 56MB on the live host and only grows. That is a real operational
problem: it is read on every dashboard build and every ``/result``, and an append-only file
with no story ends up either unreadable or quietly truncated by whoever runs out of disk.

But this is an **evidence store in a governance-first runtime**, so "retention" cannot mean
"delete the old part". The policy here is one sentence: *rotation moves rows out of the
active file and into an archive file beside it; nothing is ever destroyed.* An archived row
is still on disk, still byte-identical, still readable. That is what makes this safe to
automate at all — a scheduled job that can only ever move bytes cannot conceal anything,
and ``audit_concealment`` is a BLOCK-tier prohibited action in the Governance Policy.

**Two ledgers may never be rotated, and the reasons are different:**

- ``audit_events.jsonl`` — the hash chain. Front truncation IS detected (the first event
  must be a genesis with a null previous hash), so rotating the front would make an honest
  ledger verify as tampered. Worse in the other direction: a **prefix** of a valid chain is
  itself valid, which is exactly why tail truncation is the chain's documented blind spot.
  A tool that routinely rewrites this file is a tool that makes the one signal of tampering
  ordinary. It is excluded outright rather than made careful.
- ``control_events.jsonl`` — the kill switch's recovery source. ``ControlStore.load()``
  consults it when the state file is missing, precisely so that deleting the state file
  cannot silently resume a KILLED runtime. Rotating this file is a way to lose the last
  KILLED event, which turns a safety stop into an unauthenticated resume. It is small
  besides; there is nothing to gain and a kill switch to lose.

Rotation streams. It never parses and never loads the file: rows move as raw lines, so
archived evidence is byte-identical to what was written, and the memory cost is one line
regardless of a 56MB ledger (the same lesson the dashboard learned the hard way).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import timeutil
from .errors import PersistenceError
from .events import stamped_event
from .store import (
    ARCHIVE_DIR,
    AUDIT_FILE,
    BLOCKS_FILE,
    BRIDGE_REQUESTS_FILE,
    CONTROL_FILE,
    FEEDBACK_FILE,
    MEMORY_FILE,
    PROGRAMIZATION_FILE,
    RECORDS_FILE,
    RECORD_KINDS,
    archive_index_path,
    SCHEDULER_FILE,
)

RETENTION_EVENT_TYPE = "ledger_retention.v0"

# How many rows stay in the active file. The point is a bounded file, not a small one: the
# recent tail is what every reader actually wants (the dashboard's 12 cycles, /result's
# current runs), and anything older is a question for the archive.
DEFAULT_KEEP_ROWS = 2000

# Rotating one of these moves rows into an archive beside the active file.
#
# `bridge_requests.jsonl` is rotatable and the decision is worth stating, because for this one
# file rotation and correctness interact. Its rows are the doors' at-most-once bookkeeping, and
# a row that leaves the active file becomes invisible to the check — which would make a spent
# `request_id` replayable. That is safe here and only here: every row carries an `expires_at`
# that `bridge_idempotency` enforces on read, so a row is already inert long before 2000 rows
# of door traffic can push it out. Rotation is bounding a file whose contents have a shorter
# life than the bound. If the TTL ever grows past the rotation horizon this belongs in
# PROTECTED_FILES instead, and `bridge_idempotency` says so beside the TTL.
ROTATABLE_FILES = (
    RECORDS_FILE,
    BLOCKS_FILE,
    SCHEDULER_FILE,
    MEMORY_FILE,
    PROGRAMIZATION_FILE,
    FEEDBACK_FILE,
    BRIDGE_REQUESTS_FILE,
)

# Never rotated. The reason is per file, not a blanket rule — see the module docstring.
PROTECTED_FILES: dict[str, str] = {
    AUDIT_FILE: (
        "the audit hash chain: front truncation makes an honest ledger verify as tampered, "
        "and tail truncation is the chain's documented blind spot — a tool that routinely "
        "rewrites it makes the one signal of tampering ordinary"
    ),
    CONTROL_FILE: (
        "the kill switch's recovery source: ControlStore.load() consults it when the state "
        "file is missing, so losing its last KILLED event turns a safety stop into an "
        "unauthenticated resume"
    ),
}


def archive_dir(root: Path) -> Path:
    return Path(root) / ARCHIVE_DIR


def _count_rows(path: Path) -> int:
    """Rows in the file, streamed. Blank lines are not rows (jsonl readers skip them)."""
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def write_archive_index(archive: Path) -> frozenset[str]:
    """Record which record kinds a just-closed archive can contain, beside it.

    Written WITHOUT parsing, on purpose: the rotation below moves lines verbatim so an
    archived row stays byte-identical to the one that was written, and re-decoding megabytes
    to label them would trade that property away. The index is built by testing the
    known-kind tokens against the archive's bytes, which makes it a SUPERSET of the kinds
    actually present — a payload quoting another kind's name adds an entry and costs one
    needless file read later. The unsafe direction (missing a kind that IS present) cannot
    happen: rows carry the kind as an ASCII JSON string, so a present kind's token is there.

    Chunked with an overlap so a token cannot be split across reads — archives reach tens of
    MB and this host runs in swap.
    """
    tokens = {kind: f'"{kind}"'.encode("utf-8") for kind in RECORD_KINDS}
    overlap = max(len(tok) for tok in tokens.values())
    found: set[str] = set()
    try:
        with archive.open("rb") as handle:
            tail = b""
            while True:
                # 256 KB, not 1 MB: the rotation above is O(one line) in memory and
                # `test_memory_stays_bounded_on_a_large_ledger` pins that at 2 MB peak — an
                # indexer that reads in megabyte windows spends the whole budget on a
                # convenience. Measured: this keeps the rotation's peak where it was.
                chunk = handle.read(1 << 18)
                if not chunk:
                    break
                window = tail + chunk
                for kind, token in tokens.items():
                    if kind not in found and token in window:
                        found.add(kind)
                if len(found) == len(tokens):
                    break
                tail = window[-overlap:]
        index = archive_index_path(archive)
        tmp = index.with_name(index.name + ".tmp")
        tmp.write_text("".join(f"{kind}\n" for kind in sorted(found)), encoding="utf-8")
        os.replace(tmp, index)
    except OSError as exc:
        raise PersistenceError(
            "LEDGER_ARCHIVE_INDEX_FAILED", f"could not index {archive.name}: {exc}"
        ) from exc
    return frozenset(found)


def rotate_file(
    store: Any,
    filename: str,
    *,
    keep_rows: int = DEFAULT_KEEP_ROWS,
    now: str | None = None,
) -> dict[str, Any]:
    """Move all but the newest ``keep_rows`` rows of one ledger into a dated archive.

    Returns a summary ``{filename, rotated, kept, archive}``; ``rotated: 0`` when the file
    is absent or already within the limit (a no-op is not a failure).

    Runs under the SAME per-file lock appends take, so a rotation can never interleave with
    a write — the file a reader sees is either the pre- or post-rotation one, never a torn
    middle. Fail-closed: a protected ledger, a nonsensical ``keep_rows``, or any IO problem
    raises ``PersistenceError`` rather than proceeding on a guess.

    Ordering is deliberate: the archive is written and flushed FIRST, and the active file is
    replaced only after. A crash between the two leaves rows in both places — duplicated,
    never lost — and readers only read the active file, so the duplication is invisible and
    the next rotation cleans it up. The opposite order could lose evidence.
    """
    stamp = now or timeutil.utc_now_iso()
    if filename in PROTECTED_FILES:
        raise PersistenceError(
            "LEDGER_PROTECTED_FROM_ROTATION",
            f"{filename} is never rotated: {PROTECTED_FILES[filename]}",
        )
    if filename not in ROTATABLE_FILES:
        raise PersistenceError(
            "LEDGER_UNKNOWN_FILE",
            f"unknown ledger {filename!r}; rotatable: {sorted(ROTATABLE_FILES)}",
        )
    if not (isinstance(keep_rows, int) and keep_rows > 0):
        raise PersistenceError(
            "LEDGER_INVALID_KEEP", f"keep_rows must be a positive int, got {keep_rows!r}"
        )

    root = Path(store.root)
    path = root / filename
    with store.file_lock(filename):
        if not path.is_file():
            return {"filename": filename, "rotated": 0, "kept": 0, "archive": None}
        try:
            total = _count_rows(path)
            if total <= keep_rows:
                return {"filename": filename, "rotated": 0, "kept": total, "archive": None}

            cut = total - keep_rows
            destination = archive_dir(root)
            destination.mkdir(parents=True, exist_ok=True)
            # The timestamp is the rotation moment, not a row's — one archive per rotation,
            # so a second rotation in the same second cannot silently overwrite the first.
            archive_name = f"{filename.removesuffix('.jsonl')}.{stamp.replace(':', '')}.jsonl"
            archive_path = destination / archive_name
            suffix = 1
            while archive_path.exists():
                archive_path = destination / f"{archive_name[:-6]}.{suffix}.jsonl"
                suffix += 1

            retained_tmp = path.with_suffix(path.suffix + ".rotate")
            index = 0
            # Lines move verbatim: no parse, no re-serialize, so an archived row is
            # byte-identical to the one that was written, and memory is one line.
            with path.open(encoding="utf-8") as source, \
                    archive_path.open("w", encoding="utf-8") as archive, \
                    retained_tmp.open("w", encoding="utf-8") as retained:
                for line in source:
                    if not line.strip():
                        continue
                    index += 1
                    (archive if index <= cut else retained).write(line)
                archive.flush()
                os.fsync(archive.fileno())
                retained.flush()
                os.fsync(retained.fileno())
            os.replace(retained_tmp, path)
            if filename == RECORDS_FILE:
                write_archive_index(archive_path)
        except OSError as exc:
            raise PersistenceError(
                "LEDGER_ROTATION_FAILED", f"could not rotate {filename}: {exc}"
            ) from exc

    return {"filename": filename, "rotated": cut, "kept": keep_rows,
            "archive": str(archive_path.relative_to(root))}


def rotate_all(
    store: Any,
    *,
    keep_rows: int = DEFAULT_KEEP_ROWS,
    now: str | None = None,
    ledger: Any = None,
) -> dict[str, Any]:
    """Rotate every rotatable ledger, recording what moved.

    One file's failure does not stop the others — the point is to bound growth, and a
    single unreadable ledger should not leave the rest unbounded — but every failure is
    reported in the summary rather than swallowed.

    The retention event goes on the block ledger (not the audit chain): moving bytes
    between files is bookkeeping about the store, not a governed action on a task, and the
    ``/status`` precedent is that a maintenance read/move does not enter the trail it
    maintains.
    """
    stamp = now or timeutil.utc_now_iso()
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for filename in ROTATABLE_FILES:
        try:
            results.append(rotate_file(store, filename, keep_rows=keep_rows, now=stamp))
        except PersistenceError as exc:
            failures.append({"filename": filename, "reason_code": exc.reason_code})

    summary = {
        "rotated_rows": sum(r["rotated"] for r in results),
        "files": [r for r in results if r["rotated"]],
        "failures": failures,
        "keep_rows": keep_rows,
        "created_at": stamp,
    }
    if summary["rotated_rows"] or failures:
        target = ledger if ledger is not None else store
        try:
            target.append_block(stamped_event(
                RETENTION_EVENT_TYPE, action="ledger_rotated",
                rotated_rows=summary["rotated_rows"],
                files=[{"filename": r["filename"], "rotated": r["rotated"],
                        "archive": r["archive"]} for r in summary["files"]],
                failures=failures, keep_rows=keep_rows, created_at=stamp,
            ))
        except PersistenceError as exc:
            # The rows are already moved and nothing is lost; say the note failed rather
            # than claim the rotation did.
            summary["event_error"] = exc.reason_code
    return summary
