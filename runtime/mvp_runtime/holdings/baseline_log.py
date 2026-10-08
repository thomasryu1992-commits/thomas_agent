"""H5b: why the drawdown baseline is what it is — an append-only, hash-chained log on this host.

Thomas 2026-10-08 (``docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md``, H5b decision). The peak the
drawdown is judged from (``combined.PEAK_FILENAME``) is replaced in three ways: Thomas resets it after a
deposit or withdrawal (``holdings_board --reset-peak --reason``), the declared scope changes, or there was
none. Before H5b each of these left only a changed file. Now each leaves a line here, so a baseline can
be explained later: what it was before, what replaced it, why, under which scope and classification.

**Two events.** ``reset_requested`` is written by the reset command, with the reason and the peak it
forgets. ``baseline_set`` is written by the fire that starts the new peak, with the cause
(:data:`CAUSES`) and, after a reset, the request it answers. The first fire that finds this log empty
while a peak of the current scope stands writes one ``baseline_set`` with cause ``bootstrap``: the
standing baseline is recorded, never reset.

**Append-only, chained.** Each line carries the previous line's hash and its own
(:func:`integrity.sha256_value` over the line without its hash). :func:`verify` re-derives the chain; a
broken one raises :data:`BASELINE_LOG_TAMPERED`. A line that cannot be appended raises, and the
combined block is then not computed (``store``): a baseline is never replaced without its line.

**Local only.** Lines carry KRW totals. No door reads this file (a test pins it), like the local board.
A state file, not a ledger record: nothing gates on it; it explains.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from ..errors import ToolError
from ..filelock import locked

FILENAME = "holdings_baselines.jsonl"
RECORD_TYPE = "holdings_baseline_event.v1"
EVENT_RESET_REQUESTED = "reset_requested"
EVENT_BASELINE_SET = "baseline_set"
CAUSE_BOOTSTRAP = "bootstrap"            # the baseline that stood when the log began
CAUSE_FIRST_COMPLETE = "first_complete"  # no peak, and no reset asked for one
CAUSE_AFTER_RESET = "after_reset"        # answers a reset_requested
CAUSE_SCOPE_CHANGE = "scope_change"      # the standing peak belonged to another scope
CAUSES = (CAUSE_BOOTSTRAP, CAUSE_FIRST_COMPLETE, CAUSE_AFTER_RESET, CAUSE_SCOPE_CHANGE)
BASELINE_LOG_TAMPERED = "HOLDINGS_BASELINE_LOG_TAMPERED"
BASELINE_RESET_REFUSED = "HOLDINGS_BASELINE_RESET_REFUSED"


def path(state_dir: Path) -> Path:
    return state_dir / FILENAME


def _lines(state_dir: Path) -> list[dict[str, Any]]:
    target = path(state_dir)
    if not target.exists():
        return []
    out = []
    for number, raw in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            raise ToolError(BASELINE_LOG_TAMPERED, f"{FILENAME} line {number} does not parse") from None
        if not isinstance(row, dict):
            raise ToolError(BASELINE_LOG_TAMPERED, f"{FILENAME} line {number} is not a record")
        out.append(row)
    return out


def _hash(row: Mapping[str, Any]) -> str:
    return integrity.sha256_value({key: value for key, value in row.items() if key != "sha256"})


def verify(state_dir: Path) -> list[dict[str, Any]]:
    """Every line, after re-deriving the chain. Raises :data:`BASELINE_LOG_TAMPERED` on a break."""
    rows = _lines(state_dir)
    previous = None
    for number, row in enumerate(rows, 1):
        if row.get("record_type") != RECORD_TYPE or row.get("prev_sha256") != previous or row.get("sha256") != _hash(row):
            raise ToolError(BASELINE_LOG_TAMPERED, f"{FILENAME} line {number} breaks the chain")
        previous = row["sha256"]
    return rows


def last(state_dir: Path) -> dict[str, Any] | None:
    rows = verify(state_dir)
    return rows[-1] if rows else None


def append(state_dir: Path, event: str, *, at: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    """Append one event under the lock and return it. The id is derived from the event itself."""
    target = path(state_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    with locked(target.with_suffix(".lock"), code="HOLDINGS_BASELINE_LOG_LOCKED", label="holdings baseline log"):
        rows = verify(state_dir)
        previous = rows[-1]["sha256"] if rows else None
        row: dict[str, Any] = {"record_type": RECORD_TYPE, "event": event, "at": at, **dict(fields),
                               "prev_sha256": previous}
        row["baseline_id"] = integrity.short_id("baseline", {"event": event, "at": at, "prev": previous})
        row["sha256"] = _hash(row)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row


def pending_reset(state_dir: Path) -> dict[str, Any] | None:
    """The reset request no baseline has answered yet, if the last event is one."""
    row = last(state_dir)
    return row if row and row.get("event") == EVENT_RESET_REQUESTED else None
