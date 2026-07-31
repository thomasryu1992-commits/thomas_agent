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

from .errors import PersistenceError


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


def iter_objects(path: Path, *, read_code: str, label: str) -> Iterator[dict[str, Any]]:
    """Yield every JSON object in ``path`` (one per line); nothing if the file is absent.

    The streaming half of :func:`read_objects`, for stores that outgrew being held in memory.
    Peak cost is one line, whatever the file's size.

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
    if not path.is_file():
        return
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                yield json.loads(line)
    except (OSError, ValueError) as exc:
        raise PersistenceError(read_code, f"could not read {label}: {exc}") from exc


def read_objects(path: Path, *, read_code: str, label: str) -> list[dict[str, Any]]:
    """Return every JSON object in ``path`` (one per line), or ``[]`` if it does not exist.

    A corrupt/unparseable file fails closed with ``PersistenceError(read_code, ...)``
    rather than silently returning partial data: the list is built before it is returned,
    so a bad line anywhere raises instead of yielding a truncated store.
    """
    return list(iter_objects(path, read_code=read_code, label=label))


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
