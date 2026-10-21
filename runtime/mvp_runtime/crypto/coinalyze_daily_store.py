"""The daily Coinalyze series the runtime already fetches, kept instead of thrown away.

``docs/proposals/RESEARCH_SANDBOX_V0.1.md`` step 3 (Thomas 2026-10-20). Every factory fire and live cycle
asks Coinalyze for the daily open interest and liquidations (``feed_assembly.attach_feeds``), uses them for
the frame, and keeps nothing. That made the keyed series the one input a research sandbox without the key
could never replay. This module stores what was fetched:

- **No new request.** :func:`record` takes the rows the caller already holds; it never touches the feed.
- **Opt-in per caller.** ``attach_feeds(record_vendor_daily=True)`` is passed only by the scheduler's factory
  and the live cycle. Operator and measurement scripts do not pass it, so they stay read-only.
- **Throttled.** One write attempt per symbol per :data:`REFRESH_AFTER_SECONDS`. The vendor's daily series
  moves once a day, and a 15-minute live cycle would otherwise re-read the store on every visit.
- **Append-only, deduplicated before the write, rows self-hashed.** Same shape as ``oi_store``. Rows keep
  the vendor's label (the period's start) and value (its close); a reader must align them by when they are
  known (``features.OI_READING_KNOWN_AFTER_HOURS``), never by the label.
- **Degrade-only.** :func:`record` never raises. It reports what happened.

It feeds nothing in the runtime: features keep reading the live fetch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from runtime.read_only_kernel import integrity

from ..errors import ToolError
from ..filelock import locked
from . import refresh_marks
from .state import state_dir

OI_1D_FILENAME = "coinalyze_open_interest_1d.jsonl"
LIQUIDATIONS_1D_FILENAME = "coinalyze_liquidations_1d.jsonl"
REFRESH_MARKS_FILENAME = "coinalyze_daily_refresh.json"
OI_RECORD_TYPE = "coinalyze_open_interest_1d.v0"
LIQUIDATIONS_RECORD_TYPE = "coinalyze_liquidations_1d.v0"
REFRESH_AFTER_SECONDS = 6 * 3600

# series -> (filename, record type, the value fields a row must carry)
SERIES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "open_interest": (OI_1D_FILENAME, OI_RECORD_TYPE, ("open_interest",)),
    "liquidations": (LIQUIDATIONS_1D_FILENAME, LIQUIDATIONS_RECORD_TYPE, ("long_liquidation", "short_liquidation")),
}


def path(series: str, root: Path | None = None) -> Path:
    return state_dir(root) / SERIES[series][0]


def refresh_marks_path(root: Path | None = None) -> Path:
    return state_dir(root) / REFRESH_MARKS_FILENAME


def read_rows(series: str, *, symbol: str | None = None, directory: Path | None = None,
              root: Path | None = None) -> list[dict[str, Any]]:
    """Latest-wins on ``(symbol, timestamp)``, oldest first. A damaged line or a row failing its own hash is
    skipped, never repaired. ``directory`` reads a copy elsewhere (the research lake), ``root`` the state."""
    filename, record_type, _ = SERIES[series]
    source = (directory / filename) if directory is not None else path(series, root)
    if not source.is_file():
        return []
    keyed: dict[tuple[str, str], dict[str, Any]] = {}
    with source.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("record_type") != record_type:
                continue
            body = {k: v for k, v in row.items() if k != "record_sha256"}
            if row.get("record_sha256") != integrity.sha256_record(body):
                continue
            if symbol is not None and row.get("symbol") != symbol:
                continue
            keyed[(str(row.get("symbol")), str(row.get("timestamp")))] = row
    return [keyed[key] for key in sorted(keyed, key=lambda k: (k[0], k[1]))]


def append_rows(series: str, rows: Iterable[Mapping[str, Any]], *, symbol: str, root: Path | None = None) -> int:
    """Append the rows not already held for ``symbol``. Returns the count written."""
    filename, record_type, fields = SERIES[series]
    name = str(symbol).strip().upper()
    if not name:
        raise ToolError("COINALYZE_DAILY_SYMBOL_MISSING", "a daily vendor row needs a symbol")
    target = path(series, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with locked(target.with_suffix(".lock"), code="COINALYZE_DAILY_LOCKED", label=f"daily {series}"):
        known = {row["timestamp"] for row in read_rows(series, symbol=name, root=root)}
        fresh: list[dict[str, Any]] = []
        for row in rows:
            timestamp = str(row.get("timestamp") or "")
            values = {field: row.get(field) for field in fields}
            if not timestamp or timestamp in known:
                continue
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values.values()):
                continue
            known.add(timestamp)
            record = {"record_type": record_type, "symbol": name, "timestamp": timestamp,
                      **{field: float(value) for field, value in values.items()}}
            record["record_sha256"] = integrity.sha256_record(record)
            fresh.append(record)
        if not fresh:
            return 0
        fresh.sort(key=lambda r: r["timestamp"])
        with open(target, "a", encoding="utf-8", newline="\n") as handle:
            for record in fresh:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return len(fresh)


def record(symbol: str, *, open_interest: Iterable[Mapping[str, Any]] | None,
           liquidations: Iterable[Mapping[str, Any]] | None, now: str,
           root: Path | None = None) -> dict[str, Any]:
    """Keep the rows a caller already fetched. ``None`` for a series the caller could not fetch. Never raises."""
    name = str(symbol).strip().upper()
    marks_path = refresh_marks_path(root)
    try:
        if not refresh_marks.attempt_is_due(refresh_marks.read_marks(marks_path).get(name), now,
                                            refresh_after_seconds=REFRESH_AFTER_SECONDS):
            return {"status": "skipped_fresh"}
        refresh_marks.record_attempt(marks_path, name, now=now, lock_code="COINALYZE_DAILY_MARKS_LOCKED",
                                     label="daily vendor refresh marks")
        written = {
            series: append_rows(series, rows, symbol=name, root=root)
            for series, rows in (("open_interest", open_interest), ("liquidations", liquidations))
            if rows is not None
        }
    except Exception as exc:  # noqa: BLE001 — degrade-only: a store failure must not cost the frame
        return {"status": "degraded", "reason": type(exc).__name__}
    return {"status": "recorded", "written": written}
