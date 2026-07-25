"""Per-context routing-freshness marks — evaluate a new entry at most once per closed candle.

A single empty-request ``crypto_pipeline`` schedule fans out over every context the
active pool trades, at the finest cadence (one 15-min tick). Without a gate that tick
would evaluate a 4h or 1d strategy for a NEW entry every 15 minutes — 16x / 96x more
often than its candle actually closes — and worse, it could re-open a position on the
very same candle moments after one settled. This store records, per ``(venue, symbol,
timeframe)`` book, the ``close_time`` of the last candle a new-entry evaluation ran on;
the cycle's paper step consults it and holds the open until a newer candle closes.

Settlement is never gated by this — an open position must always be able to close on
every tick. The mark governs only whether a NEW entry is evaluated.

State is local, per-machine, gitignored (like the paper book and the ledger): one small
JSON map at ``.runtime_governance_state/crypto/routing_marks.json``. Writes are
file-locked and atomic (tmp+replace). Fail-closed direction is deliberate and toward
*more* evaluation, never less: an unreadable/corrupt file reads as "no mark", so a
stale-but-present mark can never wedge a context permanently shut — the worst case is
one redundant evaluation, self-healed by the next successful ``record`` which rewrites
the whole map.

This module imports :func:`paper.state_dir` one-directionally. ``paper.run_paper_update``
never imports this module — it receives a store instance and calls it duck-typed — so
there is no import cycle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..filelock import locked
from .paper import state_dir

MARKS_FILENAME = "routing_marks.json"


def is_fresh(last_mark: str | None, candle_time: str | None) -> bool:
    """Whether a NEW entry should be evaluated for ``candle_time`` given the last mark.

    Fresh iff the candle is identifiable and strictly newer than the last one this
    context routed on. ``candle_time is None`` (a degraded/empty snapshot) is never
    fresh: an unidentifiable candle must not consume the slot. Comparison is a plain
    string comparison, correct because ``close_time`` is the canonical fixed-width UTC
    form (``timeutil.format_iso``) the rest of the runtime already sorts on."""
    if candle_time is None:
        return False
    return last_mark is None or candle_time > last_mark


class RoutingMarkStore:
    """Local JSON map of ``context.key -> last-routed candle close_time``."""

    def __init__(self, root: Path | None = None):
        self._root = root

    @property
    def path(self) -> Path:
        return state_dir(self._root) / MARKS_FILENAME

    def _load(self) -> dict[str, str]:
        path = self.path
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Fail-closed toward "no mark": a corrupt marks file must not wedge a
            # context shut. The worst case is one redundant evaluation, healed by the
            # next successful record() which rewrites the whole map.
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()}

    def last(self, context_key: str) -> str | None:
        return self._load().get(context_key)

    def is_fresh(self, context_key: str, candle_time: str | None) -> bool:
        return is_fresh(self.last(context_key), candle_time)

    def record(self, context_key: str, candle_time: str) -> None:
        """Mark ``candle_time`` as routed for this context. Locked read-modify-write.

        Never regresses a mark: a late tick carrying an older candle leaves the newer
        mark in place, so the gate can never be re-opened by out-of-order data."""
        state_dir(self._root).mkdir(parents=True, exist_ok=True)
        path = self.path
        with locked(path.with_suffix(".lock"), code="ROUTING_MARKS_LOCKED",
                    label="crypto routing marks"):
            marks = self._load()
            current = marks.get(context_key)
            if current is not None and candle_time <= current:
                return
            marks[context_key] = candle_time
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(marks, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)


__all__ = ["RoutingMarkStore", "is_fresh", "MARKS_FILENAME"]
