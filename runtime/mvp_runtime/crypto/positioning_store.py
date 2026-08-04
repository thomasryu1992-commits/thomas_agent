"""Positioning history the runtime retains itself, because the vendor keeps 30 days.

**What this collects and why it is worth collecting.** Every column the feature row carries today
answers a question about price, or about the aggressor side of trades, or about how much position
is outstanding in aggregate. None of them answers *who is holding it*. Binance publishes three
`/futures/data/` series that do — the position bias of the top 20% of accounts by margin balance,
the account bias of that same cohort, and the account bias of everyone counted one each — and the
information is in the **disagreement** between them: large capital leaning one way while account
headcount leans the other is a situation no existing column can distinguish from agreement.

**What it does NOT do, and this is the whole shape of the increment: it feeds nothing.** No
feature reads it, no family mints against it, `snapshot` is never touched. That is the
``oi_store`` posture applied for a stronger reason than depth-parity — here there is no history
to backtest *at all*. The endpoints keep 30 days; the factory replays
``FACTORY_DEPTH_DAYS`` = 500. Wiring a feature to a series that is 94% indeterminate over every
replay window would mint families that cannot close enough trades to earn a verdict, and the
robustness scorer would then be judging the retention wall rather than the strategy.

So the honest sequence is: accumulate now, decide later. The reason to start today rather than
when a family wants the data is that **retroactive collection is impossible** — a day not
recorded is gone, and 500 days of depth is 500 days away whenever it starts. Waiting costs
exactly the thing that is scarce. :func:`coverage` is what reports progress toward eligibility;
the flip to actually reading the store stays an explicit change, because re-basing a feature
source under live-capable strategies is not something a threshold should do unattended.

Shape and its reasons, all inherited from ``oi_store`` because the problem is the same one:

- Append-only JSONL, one row per ``(symbol, series, period start)``, **latest-wins on read**, so
  a re-fetch is idempotent and a gap shorter than the retention window self-heals on the next
  refresh instead of needing a repair path.
- Requests are **throttled to once per period per (symbol, series)** on a last-ATTEMPT marker,
  not once per fire and not on the age of the newest stored row. The pool cycle fans out over 20
  contexts at a 15-minute cadence; unthrottled that is 240 vendor requests an hour (twenty
  contexts × three series × four fires) to record fifteen new readings. Measuring from the stored
  row instead is the version ``oi_store`` shipped first and had to fix — the newest complete
  period is always a period behind the clock, so it read as due every time.
- Rows carry ``record_sha256`` so tamper evidence starts when a row becomes durable.
- Reads never raise on damage. A corrupt line is skipped and counted: the only consumer is a
  coverage number, under-reporting coverage is the safe direction, and a store that refused to
  answer would take the daily board down with it. (Contrast ``pool.read_candidates``, which does
  raise — that store backs a promotion an operator signs.)
- **Each series degrades independently.** One endpoint refusing must not cost the other two their
  readings, so the refresh reports per-series status and writes whatever it got.

State is local, per-machine, gitignored, like the paper book and the ledger.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from runtime.read_only_kernel import integrity

from .. import timeutil
from ..errors import ToolError
from ..filelock import locked
from .market_data import (
    FACTORY_DEPTH_DAYS,
    POSITIONING_PERIOD_SECONDS,
    POSITIONING_SERIES,
)
from .paper import state_dir

POSITIONING_FILENAME = "positioning_history.jsonl"
RECORD_TYPE = "positioning_ratio.v0"

# The cadence the store keeps. 1h matches ``oi_store``'s hourly series and is the finest period
# whose 30-day retention window a two-page seed can actually retrieve; it is also fine enough to
# align onto every timeframe the pool trades without a feature having to interpolate.
POSITIONING_PERIOD = "1h"

# The FLOOR on how much history one refresh asks for. Three days rather than one, for
# ``oi_store``'s reason: a service down overnight must heal on the next tick without a separate
# backfill path, and 72 hourly rows is small enough that over-asking costs nothing.
#
# A floor rather than the answer, because as the answer it did not deliver the guarantee this
# module's docstring states — "a gap shorter than the retention window self-heals on the next
# refresh". It healed gaps shorter than *72 hours*; anything longer left rows the refresh could
# never reach again, and being append-only with latest-wins on read, never reaching them once is
# never reaching them at all. Measured on the live host 2026-08-04: ``BNBUSDT`` stopped being
# recorded on 2026-07-31T08:00Z and was found 91 hours later, so a 72-row refresh would have
# closed 72 of those hours and orphaned 19 — inside the retention window, and permanently lost.
# :func:`refresh_rows_for` sizes the ask to the gap instead, and this is where it starts.
REFRESH_ROWS = 3 * 24

# What a seed asks for. The venue keeps 30 days; asking for the full window means the seed takes
# whatever the retention wall actually is on the day it runs rather than whatever it was when this
# constant was written. 720 rows is two pages at the 500-row cap.
SEED_ROWS = 30 * 24

# A (symbol, series) is refreshed when its last refresh ATTEMPT is older than this — the series'
# own cadence, since asking sooner can only return a row already held.
#
# From the last ATTEMPT, not from the newest stored reading, and that distinction is not
# theoretical: ``oi_store`` shipped the reading-age version first and it never skipped. The
# vendor's newest complete period is always at least one period behind the clock (the forming one
# is dropped), so "newest reading is over an hour old" is true for almost every minute of every
# hour, and all four contexts of a symbol re-asked inside one fire — the exact thing the throttle
# exists to prevent, reported as four ``appended`` statuses with nothing appended. This store
# would have reproduced it exactly, three times over for three series.
REFRESH_AFTER_SECONDS = POSITIONING_PERIOD_SECONDS[POSITIONING_PERIOD]

# Where the attempt marks live: a small JSON map, one entry per (symbol, series), rewritten whole
# — the ``oi_store`` / ``routing_marks`` idiom, for the reason it was chosen there (a handful of
# short values read together and never grown).
REFRESH_MARKS_FILENAME = "positioning_refresh.json"

# Coverage a series needs before a feature could read it: the factory's own replay span. Not a new
# number — eligibility means "the store can answer every bar the factory asks about", and any
# smaller threshold would flip a feature source onto a window with holes in it.
REQUIRED_COVERAGE_DAYS = FACTORY_DEPTH_DAYS


def positioning_path(root: Path | None = None) -> Path:
    return state_dir(root) / POSITIONING_FILENAME


def refresh_marks_path(root: Path | None = None) -> Path:
    return state_dir(root) / REFRESH_MARKS_FILENAME


def _mark_key(symbol: str, series: str) -> str:
    return f"{str(symbol).strip().upper()}|{series}"


def read_refresh_marks(root: Path | None = None) -> dict[str, str]:
    """``{"SYMBOL|series": last attempt}``. Damage reads as no marks, which means "ask again".

    Fail-open toward asking rather than toward skipping — the opposite direction from this
    store's row *reads*, and deliberately so. A corrupt marks file that read as "recently
    attempted" would silently stop a 500-day accumulation while the board went on reporting
    whatever coverage it already had, and a store that quietly stops growing is worse than one
    that asks the vendor once too often.
    """
    path = refresh_marks_path(root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def record_refresh_attempt(symbol: str, series: str, *, now: str, root: Path | None = None) -> None:
    """Mark that the venue was asked about this (symbol, series), whatever the answer was.

    Recorded on failure too. A marker written only on success turns a refusing endpoint into one
    ask per context per fire — with three series and twenty contexts that is 240 asks an hour to
    record nothing. A persistent failure now costs three asks an hour, and a transient one delays
    the self-heal by at most that long, which the 30-day retention window absorbs easily.
    """
    name = str(symbol).strip().upper()
    if not name or series not in POSITIONING_SERIES:
        return
    directory = state_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = refresh_marks_path(root)
    with locked(path.with_suffix(".lock"), code="POSITIONING_MARKS_LOCKED",
                label="positioning refresh marks"):
        marks = read_refresh_marks(root)
        marks[_mark_key(name, series)] = now
        path.write_text(json.dumps(marks, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _row_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("symbol") or ""),
        str(row.get("series") or ""),
        str(row.get("timestamp") or ""),
    )


def read_rows(
    root: Path | None = None, *, symbol: str | None = None, series: str | None = None
) -> list[dict[str, Any]]:
    """Every stored reading, oldest first, latest-wins per ``(symbol, series, period)``.

    Damage is skipped, never raised — see the module docstring for why this store answers with
    less rather than refusing to answer.
    """
    path = positioning_path(root)
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        key = _row_key(row)
        if not all(key):
            continue
        latest[key] = row

    wanted_symbol = str(symbol).strip().upper() if symbol is not None else None
    wanted_series = str(series) if series is not None else None
    selected = [
        r for k, r in latest.items()
        if (wanted_symbol is None or k[0] == wanted_symbol)
        and (wanted_series is None or k[1] == wanted_series)
    ]
    return sorted(selected, key=lambda r: (str(r.get("symbol")), str(r.get("series")), str(r.get("timestamp"))))


def newest_timestamp(root: Path | None = None, *, symbol: str, series: str) -> str | None:
    rows = read_rows(root, symbol=symbol, series=series)
    return str(rows[-1]["timestamp"]) if rows else None


def append_rows(
    rows: Iterable[Mapping[str, Any]], *, symbol: str, series: str, root: Path | None = None
) -> int:
    """Append readings for one (symbol, series). Returns the count written.

    Rows already held are dropped BEFORE the write, not after: the store is append-only and a
    refresh overlaps the previous one by design, so writing every fetched row would grow the file
    by 72 duplicates an hour while the read-side latest-wins hid it.

    A ratio outside ``(0, 1)`` is refused rather than stored. These are proportions of a split
    that sums to one, so a value at or beyond the bounds is a broken payload, and this store's
    rows are durable evidence — the moment to reject them is before they become permanent.
    """
    name = str(symbol).strip().upper()
    if not name:
        raise ToolError("POSITIONING_SYMBOL_MISSING", "a positioning row needs a symbol")
    if series not in POSITIONING_SERIES:
        raise ToolError("POSITIONING_SERIES_UNKNOWN", f"unknown positioning series {series!r}")

    path = positioning_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code="POSITIONING_LOCKED", label="positioning history"):
        known = {_row_key(r) for r in read_rows(root, symbol=name, series=series)}
        fresh: list[dict[str, Any]] = []
        for row in rows:
            timestamp = str(row.get("timestamp") or "")
            long_ratio, short_ratio = row.get("long_ratio"), row.get("short_ratio")
            if not timestamp or (name, series, timestamp) in known:
                continue
            if not _is_proportion(long_ratio) or not _is_proportion(short_ratio):
                continue
            known.add((name, series, timestamp))
            record = {
                "record_type": RECORD_TYPE,
                "symbol": name,
                "series": series,
                "period": POSITIONING_PERIOD,
                "timestamp": timestamp,
                "long_ratio": float(long_ratio),
                "short_ratio": float(short_ratio),
            }
            record["record_sha256"] = integrity.sha256_record(record)
            fresh.append(record)
        if not fresh:
            return 0
        fresh.sort(key=lambda r: r["timestamp"])
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            for record in fresh:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return len(fresh)


def _is_proportion(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0.0 < float(value) < 1.0
    )


def is_due(last_attempt: str | None, now: str) -> bool:
    """Whether this (symbol, series) should be asked about again, given when it was last ASKED.

    Not when it was last successfully written — see :data:`REFRESH_AFTER_SECONDS` for why
    measuring from the newest stored reading could never skip. Never attempted → due.
    Unparseable mark → due, because a marker that cannot be read must not be able to stop
    accumulation; the append is idempotent, so being wrong costs one redundant request.
    """
    if not last_attempt:
        return True
    try:
        elapsed = (timeutil.parse_iso(now) - timeutil.parse_iso(last_attempt)).total_seconds()
    except (ValueError, TypeError):
        return True
    return elapsed >= REFRESH_AFTER_SECONDS


def refresh_rows_for(newest: str | None, now: str) -> int:
    """How many rows one refresh asks for, given the newest reading already held.

    The gap decides, bounded on both sides. :data:`REFRESH_ROWS` is the floor, so the ordinary
    hourly refresh keeps asking for the same cheap three-day overlap it always did.
    :data:`SEED_ROWS` is the ceiling, because it *is* the vendor's retention window — asking
    past it buys nothing, and a gap that long cannot be closed by any request.

    Never attempted (no rows) is a seed. So is a timestamp that cannot be parsed, which is the
    :func:`read_refresh_marks` posture for the same reason: state this store cannot read must
    never be able to make it ask for *less* than it would otherwise, because under-asking is
    the failure that is permanent.

    The two-period margin is what keeps the boundary case from being short by one. The venue's
    newest complete period is always a period behind the clock (the forming one is dropped), so
    a request sized to the raw elapsed time reaches back to exactly the newest row already held
    rather than past it, and one row of overlap is what proves the join was contiguous. Costing
    two extra rows to know that is the cheapest evidence in this module.
    """
    if not newest:
        return SEED_ROWS
    try:
        elapsed = (timeutil.parse_iso(now) - timeutil.parse_iso(newest)).total_seconds()
    except (ValueError, TypeError):
        return SEED_ROWS
    periods = int(max(0.0, elapsed) // REFRESH_AFTER_SECONDS) + 2
    return max(REFRESH_ROWS, min(SEED_ROWS, periods))


def record_positioning(
    *,
    symbol: str,
    collector: Any,
    now: str,
    root: Path | None = None,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    """One throttled refresh of all three series for one symbol. Never raises.

    Called from the cycle's feed step, which runs once per context — twenty times per fire on the
    shipped fan-out. The throttle is what makes that safe: only the first context after the period
    turns reaches the venue, and the rest return ``skipped_fresh`` having opened no socket. It
    measures from the last ATTEMPT, recorded whatever the answer was, because measuring from the
    newest stored reading could never skip (see :data:`REFRESH_AFTER_SECONDS`).

    Per-series status, because a partial outage is the normal case and must stay legible: one
    endpoint refusing does not cost the other two their readings. ``status`` for the call as a
    whole is the worst of them, so a caller that only looks at one field is not told everything
    was fine.
    """
    name = str(symbol).strip().upper()
    if collector is None or not hasattr(collector, "positioning_history"):
        return {"symbol": name, "status": "skipped_no_feed", "written": 0, "series": {}}

    marks = read_refresh_marks(root)
    per_series: dict[str, dict[str, Any]] = {}
    for series in sorted(POSITIONING_SERIES):
        last_attempt = marks.get(_mark_key(name, series))
        if not is_due(last_attempt, now):
            per_series[series] = {
                "status": "skipped_fresh", "written": 0, "last_attempt": last_attempt,
            }
            continue
        # Whether this is a seed is a property of the STORE, not of the marks: a machine whose
        # marks file was deleted must still refresh rather than re-seed 30 days it already holds.
        newest = newest_timestamp(root, symbol=name, series=series)
        seeding = newest is None
        # Stamped BEFORE the request, so a fetch that hangs or raises still counts as an attempt.
        record_refresh_attempt(name, series, now=now, root=root)
        try:
            rows = collector.positioning_history(
                name, series=series, period=POSITIONING_PERIOD,
                limit=refresh_rows_for(newest, now),
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 — a collection miss must not fail a cycle
            reason = getattr(exc, "reason_code", None) or type(exc).__name__
            per_series[series] = {"status": "degraded", "written": 0, "reason": reason}
            continue
        try:
            written = append_rows(rows, symbol=name, series=series, root=root)
        except ToolError as exc:
            per_series[series] = {"status": "degraded", "written": 0, "reason": exc.reason_code}
            continue
        per_series[series] = {
            "status": "seeded" if seeding else "appended",
            "written": written,
            "newest": newest_timestamp(root, symbol=name, series=series),
        }

    statuses = {entry["status"] for entry in per_series.values()}
    # Worst-first, so "one series failed" never reports as success.
    for candidate in ("degraded", "seeded", "appended", "skipped_fresh"):
        if candidate in statuses:
            overall = candidate
            break
    else:
        overall = "skipped_no_feed"
    return {
        "symbol": name,
        "status": overall,
        "written": sum(int(entry["written"]) for entry in per_series.values()),
        "series": per_series,
    }


def coverage(
    root: Path | None = None, *, symbol: str, series: str, now: str | None = None
) -> dict[str, Any]:
    """How much history this store holds for one (symbol, series), and whether it is enough.

    ``covered_days`` is the span from oldest to newest reading — **not** a row count divided by
    24. A gap in the middle would flatter a row count into claiming more coverage than a replay
    could actually use, and gaps are exactly what the self-healing refresh exists to close;
    ``gap_periods`` reports how many periods inside that span are missing, so the span is never
    read as a completeness claim on its own.
    """
    rows = read_rows(root, symbol=symbol, series=series)
    name = str(symbol).strip().upper()
    if not rows:
        return {
            "symbol": name, "series": series, "rows": 0, "covered_days": 0.0,
            "required_days": REQUIRED_COVERAGE_DAYS, "eligible": False,
            "oldest": None, "newest": None, "gap_periods": 0,
        }
    oldest, newest = str(rows[0]["timestamp"]), str(rows[-1]["timestamp"])
    period_seconds = POSITIONING_PERIOD_SECONDS[POSITIONING_PERIOD]
    try:
        span_seconds = (timeutil.parse_iso(newest) - timeutil.parse_iso(oldest)).total_seconds()
    except (ValueError, TypeError):
        span_seconds = 0.0
    span_seconds = max(span_seconds, 0.0)
    covered_days = round(span_seconds / 86400, 2)
    expected = int(round(span_seconds / period_seconds)) + 1
    return {
        "symbol": name,
        "series": series,
        "rows": len(rows),
        "covered_days": covered_days,
        "required_days": REQUIRED_COVERAGE_DAYS,
        "eligible": covered_days >= REQUIRED_COVERAGE_DAYS,
        "oldest": oldest,
        "newest": newest,
        "gap_periods": max(0, expected - len(rows)),
    }


def coverage_summary(root: Path | None = None, *, symbols: Iterable[str]) -> dict[str, Any]:
    """Coverage across every traded symbol and every series, plus the weakest cell.

    The minimum rather than the mean, for ``oi_store``'s reason: a source flip would be per
    series across every symbol the pool trades, so the weakest cell is the one that decides. And
    the pair is what the data is FOR — a feature reading the divergence between ``top_position``
    and ``global_account`` needs both, so one series being ready is not readiness.
    """
    wanted = sorted({str(s).strip().upper() for s in symbols if s})
    cells = [
        coverage(root, symbol=name, series=series)
        for name in wanted
        for series in sorted(POSITIONING_SERIES)
    ]
    if not cells:
        return {"cells": [], "min_covered_days": 0.0,
                "required_days": REQUIRED_COVERAGE_DAYS, "eligible": False}
    return {
        "cells": cells,
        "min_covered_days": min(c["covered_days"] for c in cells),
        "required_days": REQUIRED_COVERAGE_DAYS,
        "eligible": all(c["eligible"] for c in cells),
    }


__all__ = [
    "POSITIONING_FILENAME", "POSITIONING_PERIOD", "RECORD_TYPE", "REFRESH_MARKS_FILENAME",
    "REQUIRED_COVERAGE_DAYS", "append_rows", "coverage", "coverage_summary", "is_due",
    "newest_timestamp", "positioning_path", "read_refresh_marks", "read_rows",
    "record_positioning", "record_refresh_attempt", "refresh_marks_path", "refresh_rows_for",
]
