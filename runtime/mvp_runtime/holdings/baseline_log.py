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

from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from . import chained_log

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


def verify(state_dir: Path) -> list[dict[str, Any]]:
    """Every line, after re-deriving the chain. Raises :data:`BASELINE_LOG_TAMPERED` on a break."""
    return chained_log.verify(path(state_dir), record_type=RECORD_TYPE, tamper_code=BASELINE_LOG_TAMPERED)


def last(state_dir: Path) -> dict[str, Any] | None:
    rows = verify(state_dir)
    return rows[-1] if rows else None


def append(state_dir: Path, event: str, *, at: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    """Append one event under the lock and return it. The id is derived from the event itself."""
    def build(previous: str | None) -> dict[str, Any]:
        return {"event": event, "at": at, **dict(fields),
                "baseline_id": integrity.short_id("baseline", {"event": event, "at": at, "prev": previous})}

    return chained_log.append(path(state_dir), record_type=RECORD_TYPE, tamper_code=BASELINE_LOG_TAMPERED,
                              lock_code="HOLDINGS_BASELINE_LOG_LOCKED", label="holdings baseline log",
                              build=build)[0]


def pending_reset(state_dir: Path) -> dict[str, Any] | None:
    """The reset request no baseline has answered yet, if the last event is one."""
    row = last(state_dir)
    return row if row and row.get("event") == EVENT_RESET_REQUESTED else None
