"""Append-only JSONL primitives shared by the runtime's local stores.

The durable ledger (``store.py``) and the working-memory store (``working_memory.py``)
both need the same thing: append one JSON object per line, fail closed on any write
error, and read the file back as a list of objects (or ``[]`` if absent), failing
closed on corruption. That logic lived twice; it lives here now. Callers pass their
own ``PersistenceError`` ``reason_code`` and a short label so the fail-closed error
still names the store it came from.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .errors import MvpRuntimeError, PersistenceError


def append_lines(path: Path, objects: Iterable[Mapping[str, Any]], *, write_code: str, label: str) -> None:
    """Append each object as one JSON line under ``path`` (creating parents). Fail-closed.

    Deterministic on disk (``sort_keys=True``); a corrupt object or an unwritable path
    raises ``PersistenceError(write_code, ...)``.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for obj in objects:
                fh.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")
    except (OSError, TypeError, ValueError) as exc:
        raise PersistenceError(write_code, f"could not append {label}: {exc}") from exc


def _screened(
    numbered: Iterator[tuple[int, str]], must_contain: tuple[str, ...]
) -> Iterator[tuple[int, str]]:
    """Drop lines that cannot carry what the caller asked for, without parsing them.

    Allocation-free on the dropped path, which is most of the win: rows in the record ledger
    average ~5 KB, so testing a `line.strip()` copy would have re-copied the whole store to
    avoid decoding it. Containment reads the raw line — a trailing newline changes no
    substring answer.

    A line is skipped only if it also still LOOKS whole. `endswith("}")` alone is not that
    test: a torn append often lands just after a nested object and ends in a brace it does not
    own, so the brace counts must balance too. Braces inside string values can only unbalance
    a line that is in fact fine, which costs one parse and never a wrong answer; a truncated
    line cannot balance, so it reaches the parser and fails closed there.
    """
    for lineno, line in numbered:
        if not any(token in line for token in must_contain):
            if (line[:1] == "{"
                    and (line.endswith("}\n") or line.rstrip().endswith("}"))
                    and line.count("{") == line.count("}")):
                continue
        yield lineno, line


def iter_numbered(
    path: Path,
    *,
    read_code: str,
    label: str,
    exc_type: type[MvpRuntimeError] = PersistenceError,
    must_contain: tuple[str, ...] | None = None,
) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield ``(file line number, object)`` for every non-blank line; nothing if ``path`` is absent.

    The numbering half of :func:`iter_objects`, for readers whose *later* checks report positions.
    The crypto outcome stores each verify a per-record hash and a unique id after the parse, and
    both messages name the offending line — so a reader that only received objects would have to
    count them itself, and an object counter is **not** a line counter the moment a blank line
    appears. The number here is the true 1-based file line, blanks included, which is what an
    operator greps for.

    ``exc_type`` lets a caller keep its own error class rather than adopt ``PersistenceError``.
    That is not a style knob: those readers are reached through tool chokepoints that catch
    ``ToolError``, and a reader that started raising a sibling class would fail *past* its caller
    instead of at it. Both classes carry ``reason_code``, so the fail-closed contract is unchanged
    — only which except-clause sees it.

    ``must_contain`` is an OPTIONAL PRESCREEN, not a filter: a line containing none of the
    substrings is skipped **without being parsed**. It exists because the parse is the whole
    cost of a large store — the record ledger is 23 MB of which 97.6% is one kind, so a reader
    after a rare kind was paying to decode ~5.3k objects it discarded (measured 2026-08-31:
    0.17 s for the active file, 1.12 s once archives join, all of it under the appender's lock).

    Two properties make it safe to skip a parse this way, and both are load-bearing:

    * **No false negatives.** Rows are written by :func:`append_objects` with
      ``json.dumps(..., ensure_ascii=False)``, and every token a caller screens on is ASCII, so
      a row that really carries the value contains the quoted substring verbatim. False
      POSITIVES are fine and expected (a payload may quote the word) — the screen only decides
      what to parse, never what a caller accepts, so the caller's own check stays the authority.
    * **Corruption still raises.** A skipped line must first look like a complete object
      (``{`` … ``}``), which is precisely what a torn append — this store's real corruption
      mode, an interrupted write leaving a truncated final line — does not. What the prescreen
      does give up, stated rather than discovered: a byte-flip *inside* a well-formed line of a
      kind the caller did not ask for is no longer detected by that caller. Unfiltered readers
      (the audit chain, every all-or-nothing load) are unchanged.
    """
    if not path.is_file():
        return
    try:
        with path.open(encoding="utf-8") as handle:
            numbered = enumerate(handle, start=1)
            # The screen is applied by WRAPPING the line source, never by a test inside the
            # loop below: an unscreened read — which is every existing caller — must execute
            # the same instructions it did before this parameter existed, and a per-line
            # branch on a loop-invariant is exactly the kind of "free" check that shows up as
            # a few percent on a 200 MB store.
            if must_contain is not None:
                numbered = _screened(numbered, must_contain)
            for lineno, line in numbered:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError as exc:
                    # Name the line. The decoder's own "line 1 column 7" is relative to the
                    # one line it was handed, so on a 100k-line store it points at nothing.
                    raise exc_type(
                        read_code, f"could not read {label}: line {lineno} is not valid JSON"
                    ) from exc
                yield lineno, obj
    except OSError as exc:
        raise exc_type(read_code, f"could not read {label}: {exc}") from exc


def iter_objects(
    path: Path,
    *,
    read_code: str,
    label: str,
    exc_type: type[MvpRuntimeError] = PersistenceError,
    must_contain: tuple[str, ...] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield every JSON object in ``path`` (one per line); nothing if the file is absent.

    The streaming half of :func:`read_objects`, for stores that outgrew being held in memory.
    Peak cost is one line, whatever the file's size. Callers that need the line number a later
    check will quote take :func:`iter_numbered` instead; this is that generator with the number
    dropped, so there is one reader and not two.

    ``read_objects`` used to ``read_text()`` the whole file into one string, ``splitlines()``
    it into a second full copy, and only then parse — with both copies alive while the objects
    were built, so opening a store cost several times its own size before a caller had touched
    a row. That is not hypothetical: the crypto board OOM-killed on exactly this shape and was
    repaired with a private streaming reader (``crypto/dashboard.py``), which left the shape
    itself in place for the next store to grow into. It did: the PM1 observation store reached
    290 MB in the first four days of a fourteen-day window. This is that repair at the
    primitive rather than one caller further down.

    Fail-closed identically — a corrupt or unparseable line raises
    ``PersistenceError(read_code, ...)``. **The raise arrives mid-iteration**, once earlier rows
    have already been yielded, because a generator cannot judge what it has not read. Callers
    needing all-or-nothing get it by materializing (:func:`read_objects` does, and a list either
    completes or raises); a caller that streams is choosing to see a prefix of a store whose
    tail may not parse, and must treat a partial consumption as partial.
    """
    for _lineno, obj in iter_numbered(path, read_code=read_code, label=label,
                                      exc_type=exc_type, must_contain=must_contain):
        yield obj


def read_objects(
    path: Path,
    *,
    read_code: str,
    label: str,
    exc_type: type[MvpRuntimeError] = PersistenceError,
) -> list[dict[str, Any]]:
    """Return every JSON object in ``path`` (one per line), or ``[]`` if it does not exist.

    A corrupt/unparseable file fails closed with ``PersistenceError(read_code, ...)``
    rather than silently returning partial data: the list is built before it is returned,
    so a bad line anywhere raises instead of yielding a truncated store.
    """
    return list(iter_objects(path, read_code=read_code, label=label, exc_type=exc_type))


_TAIL_CHUNK = 64 * 1024


def tail_objects(
    path: Path,
    limit: int,
    *,
    read_code: str,
    label: str,
    exc_type: type[MvpRuntimeError] = PersistenceError,
) -> list[dict[str, Any]]:
    """The newest ``limit`` objects of an append-only store, read from the END of the file.

    Cost is the tail's size, not the file's: the file is read backwards in 64 KiB chunks
    until ``limit`` complete lines are in hand, so a 2.4 MB scheduler ledger answers "the
    last 20" in ~0.1 ms where parsing every line took ~20 ms (measured 2026-09-04) — and the
    number keeps its shape as the active file grows through the day toward rotation.

    Takes NO lock, deliberately, and the two facts that make that honest are stated here:

    * **Appends are whole lines under the writer's lock, and rotation replaces the inode.**
      So the only inconsistency a lock-free reader can see is the line being written right
      now, and only at the tail — a final line without its ``\n``. That line is dropped, not
      parsed: the reader reports the store as it was one append ago, which is what "newest"
      means at any instant anyway. A rotation mid-read is invisible: the file is opened once
      and the old inode stays readable until closed.
    * **A complete line that does not parse still raises** ``exc_type(read_code, ...)`` —
      fail-closed like :func:`read_objects`. What this reader gives up is corruption it never
      looked at: a bad line above the tail is not seen. Callers wanting the whole-store
      guarantee keep using :func:`read_objects`.

    Blank lines are skipped, as everywhere; ``limit <= 0`` or an absent file gives ``[]``.
    """
    if limit <= 0 or not path.exists():
        return []
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            pos = fh.tell()
            buf = b""
            # One more newline than lines wanted, so the first kept line is known complete
            # (it is preceded by a newline or by the start of the file).
            while pos > 0 and buf.count(b"\n") <= limit + 1:
                step = min(_TAIL_CHUNK, pos)
                pos -= step
                fh.seek(pos)
                buf = fh.read(step) + buf
    except OSError as exc:
        raise exc_type(read_code, f"could not read {label}: {exc}") from exc
    if not buf.endswith(b"\n"):
        # A torn append, or a writer mid-line: everything after the last newline is not a row.
        buf = buf[: buf.rfind(b"\n") + 1]
    lines = [line for line in buf.split(b"\n") if line.strip()]
    if pos > 0 and lines:
        # The first line of the buffer may start mid-row (the chunk boundary landed inside it)
        # unless the buffer begins at the file's start; the loop read one extra line for it.
        lines = lines[1:]
    objects: list[dict[str, Any]] = []
    for raw in lines[-limit:]:
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise exc_type(read_code, f"{label} has a corrupt line in its tail: {exc}") from exc
        if not isinstance(obj, dict):
            raise exc_type(read_code, f"{label} has a non-object line in its tail")
        objects.append(obj)
    return objects


def count_lines(path: Path, *, read_code: str, label: str,
                exc_type: type[MvpRuntimeError] = PersistenceError) -> int:
    """How many rows an append-only store holds — newline count, no parse, no lock.

    The companion to :func:`tail_objects` for a reader that wants to say "the newest 20 of
    5,120": counting newlines in 2.4 MB is ~1 ms; decoding 5,120 objects to take ``len()``
    was ~20 ms, and both are lock-free for the reason given there. A trailing unterminated
    line is not a row and is not counted. Absent file: 0.
    """
    if not path.exists():
        return 0
    total = 0
    try:
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                total += chunk.count(b"\n")
    except OSError as exc:
        raise exc_type(read_code, f"could not read {label}: {exc}") from exc
    return total


def write_objects(path: Path, objects: Iterable[Mapping[str, Any]], *, write_code: str, label: str) -> None:
    """Atomically **overwrite** ``path`` with exactly ``objects`` (one JSON line each).

    Used by compaction/retention that must rewrite a JSONL store rather than append (temp file +
    ``os.replace``, so a crash never leaves a half-written store). Fail-closed on write error.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for obj in objects:
                fh.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError) as exc:
        raise PersistenceError(write_code, f"could not rewrite {label}: {exc}") from exc
