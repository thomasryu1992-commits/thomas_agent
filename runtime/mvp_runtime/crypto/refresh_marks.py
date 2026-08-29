"""The throttled-attempt marks fabric every retention store copied.

``oi_store`` shipped it first; ``positioning_store`` and ``orderbook_store`` inherited the
shape by copy, and the copies drifted where copies do: the marks WRITE was atomic
(tmp + replace) in one store and a bare ``write_text`` in the other two. A torn write in
those two reads as "no marks" and costs one redundant ask — survivable — but the drift is
the defect this package keeps re-learning: a fix that lands in one store and not its
siblings. This module is the one copy; every store's public marks functions stay as thin
wrappers, so their callers and their tests see no change.

The boundary is deliberate, and narrower than "extract the stores' skeleton". §G records
three times the obvious consolidation was wrong until measured, and what differs between
these stores differs for measured reasons:

- :func:`attempt_is_due` carries only the elapsed-attempt idiom the hourly stores share
  byte-for-byte. ``orderbook_store`` keeps its own period-identity due — its measured scar
  (~30% of periods permanently lost under the copied elapsed throttle, 2026-08-16) stays
  argued in that module, never hidden behind a flag here.
- Append/dedup and row reads stay per-store: latest-wins multi-row (``oi_store``,
  ``positioning_store``) and first-wins single-snapshot (``orderbook_store``) are different
  settlement rules, and each store's fsync and lock posture is argued beside its own code.
- ``coverage`` stays per-store: the field names are each store's public report shape.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import timeutil
from ..filelock import locked


def read_marks(path: Path) -> dict[str, str]:
    """``{key: last attempt}``. Damage reads as no marks, which means "ask again".

    Fail-open toward asking rather than toward skipping — the opposite direction from the
    stores' row reads, because a corrupt marks file that read as "recently attempted" would
    silently stop a long accumulation while the board went on reporting whatever coverage
    it already had. A store that quietly stops growing is worse than one that asks the
    vendor once too often.
    """
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def record_attempt(path: Path, key: str, *, now: str, lock_code: str, label: str) -> None:
    """Mark that the vendor was asked about ``key``, whatever the answer was.

    Recorded on failure too: a marker written only on success turns a refusing endpoint
    into one ask per context per fire — the request-cap failure ``oi_store`` already caused
    once. Written atomically (tmp + replace), the posture ``oi_store`` had and the copies
    in its siblings had lost.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code=lock_code, label=label):
        marks = read_marks(path)
        marks[str(key)] = now
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(marks, ensure_ascii=False, sort_keys=True, indent=1),
            encoding="utf-8",
        )
        tmp.replace(path)


def attempt_is_due(last_attempt: str | None, now: str, *, refresh_after_seconds: float) -> bool:
    """Whether to ask again, measured from the last ATTEMPT.

    Never from the newest stored reading — that version could never skip: the vendor's
    newest complete period is always behind the clock, so "the newest row is stale" is true
    for almost every minute of every period. Never attempted → due. Unparseable mark → due,
    because a marker that cannot be read must not be able to stop accumulation; the appends
    are idempotent, so being wrong costs one redundant request.
    """
    if not last_attempt:
        return True
    try:
        elapsed = (timeutil.parse_iso(now) - timeutil.parse_iso(last_attempt)).total_seconds()
    except (ValueError, TypeError):
        return True
    return elapsed >= refresh_after_seconds
