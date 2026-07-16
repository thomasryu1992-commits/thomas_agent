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
from pathlib import Path
from typing import Any, Iterable, Mapping

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


def read_objects(path: Path, *, read_code: str, label: str) -> list[dict[str, Any]]:
    """Return every JSON object in ``path`` (one per line), or ``[]`` if it does not exist.

    A corrupt/unparseable file fails closed with ``PersistenceError(read_code, ...)``
    rather than silently returning partial data.
    """
    if not path.is_file():
        return []
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return [json.loads(ln) for ln in lines]
    except (OSError, ValueError) as exc:
        raise PersistenceError(read_code, f"could not read {label}: {exc}") from exc
