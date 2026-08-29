"""Hourly open-interest history the runtime retains itself, because the vendor keeps 84 days.

The ``oi_*`` families are the strongest thing the factory currently mints, and every one of
them reads a **daily** OI series: ``CoinalyzeLiquidationFeed.open_interest_history`` requests
``interval="daily"``, and ``_open_interest_event_columns`` states the consequence plainly —
*"a change here is day against day regardless of the bar size the caller will align onto."*
So a 1h or 4h ``oi_*`` entry times an hour off a value that steps once a day.

Pointing that feed at ``1hour`` today would make it worse, not better. Measured on the vendor
2026-07-29, hourly history begins ~84 days back and windows older than that return empty — a
retention wall, not a page boundary (Binance's own endpoint is shallower still). Every
timeframe below 1d replays ``FACTORY_DEPTH_DAYS`` = 500 calendar days, and the backtest and the
live router must read the same feature source, so 83% of every replay window would arrive
indeterminate and the family would close too few trades to earn a verdict. The switch would
delete the evidence behind the strategies it was meant to improve.

The only way past a vendor's retention window is to stop depending on it. This module is that
store: seeded from the days the vendor still has, appended every cycle thereafter, and
**feeding nothing**. Features keep reading daily OI, so backtest and live stay identical while
the history builds. What it produces is :func:`coverage`, and what that is for is telling an
operator when a timeframe has become eligible — the flip itself stays an explicit change, since
re-basing the feature source under live-capable strategies is not something a threshold should
do while nobody is looking.

Shape and its reasons:

- Append-only JSONL, one row per ``(symbol, hour)``, **latest-wins on read**. A re-fetch is
  therefore idempotent and a gap shorter than the vendor's retention self-heals on the next
  read rather than needing a repair path.
- Writes are **throttled to once per hour per symbol**, not once per fire. The pool cycle fans
  out over 20 contexts at a 15-minute cadence; without the throttle this would issue 80 vendor
  requests an hour to record 5 new readings.
- Rows carry ``record_sha256`` (the candidate-store precedent) so tamper evidence starts when a
  row becomes durable.
- Reads never raise on damage. A corrupt line is skipped and counted, because this store's one
  consumer is a coverage number: reporting less coverage than exists is the safe direction, and
  a store that refuses to answer would take the daily board down with it.

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
from . import refresh_marks
from .market_data import FACTORY_DEPTH_DAYS
from .paper import state_dir

OI_1H_FILENAME = "open_interest_1h.jsonl"
OI_1H_INTERVAL = "1hour"
RECORD_TYPE = "open_interest_1h.v0"

# How much history one refresh asks the vendor for. Three days rather than one: a service that
# was down overnight must heal itself on the next tick without a separate backfill path, and
# three days of hourly rows is 72 — small enough that over-asking costs nothing.
REFRESH_DAYS = 3

# What a seed asks for. The vendor keeps ~84 days of hourly history; asking for more is free and
# means the seed takes whatever the retention wall actually is on the day it runs rather than
# whatever it was when this constant was written.
SEED_DAYS = 120

# A symbol is refreshed when its last refresh ATTEMPT is older than this. One hour is the series'
# own cadence — asking more often can only return a row already held.
#
# The first version measured from the newest stored READING and did not work. The vendor's newest
# complete hour is always at least an hour behind the clock (the forming hour is dropped), so
# "newest reading is over an hour old" was true for almost every minute of every hour and all
# four contexts of a symbol re-asked inside one fire — the exact thing the throttle was written
# to prevent, reported as four `appended` statuses with nothing appended. Measured on the first
# fire after it deployed. What bounded the requests in practice was `PerSymbolFeedCache`, which
# is a different mechanism with a different lifetime (one fan-out), so the two do not substitute
# for each other: the cache stops a fire from asking twice, and this stops the next fire from
# asking again fifteen minutes later.
REFRESH_AFTER_SECONDS = 3600

# Where the attempt marks live: a small JSON map, one entry per symbol, rewritten whole — the
# `routing_marks` idiom, for the same reason it was chosen there (a handful of short values that
# are read together and never grown).
REFRESH_MARKS_FILENAME = "open_interest_1h_refresh.json"

# Coverage a timeframe needs before its features could read this store instead of the daily
# series: the factory's own replay span. Not a new number — the same one `factory_candle_target`
# already replays, because eligibility means "the store can answer every bar the factory asks
# about", and any smaller threshold would flip the source onto a window with holes in it.
REQUIRED_COVERAGE_DAYS = FACTORY_DEPTH_DAYS


def oi_1h_path(root: Path | None = None) -> Path:
    return state_dir(root) / OI_1H_FILENAME


def refresh_marks_path(root: Path | None = None) -> Path:
    return state_dir(root) / REFRESH_MARKS_FILENAME


def read_refresh_marks(root: Path | None = None) -> dict[str, str]:
    """``{symbol: last attempt}``. Damage reads as no marks, which means "ask again".

    Fail-open toward asking rather than toward skipping, and that direction is the opposite of
    the one this store's *reads* take. A corrupt marks file that read as "recently attempted"
    would silently stop accumulation with the board still reporting whatever coverage it had —
    a store that quietly stops growing is worse than one that asks the vendor once too often."""
    return refresh_marks.read_marks(refresh_marks_path(root))


def record_refresh_attempt(symbol: str, *, now: str, root: Path | None = None) -> None:
    """Mark that the vendor was asked about ``symbol``, whatever the answer was.

    Recorded on failure too, deliberately. The failure this store already caused once was a
    request cap, and a marker written only on success turns a refusing vendor into four asks per
    fire — sixteen an hour per symbol, which is how the cap was reached. A persistent failure now
    costs one ask an hour, and a transient one delays the self-heal by at most that long, which
    the vendor's ~84-day retention absorbs."""
    name = str(symbol).strip().upper()
    if not name:
        return
    refresh_marks.record_attempt(
        refresh_marks_path(root), name, now=now,
        lock_code="OI_1H_MARKS_LOCKED", label="hourly OI refresh marks",
    )


def _row_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (str(row.get("symbol") or ""), str(row.get("timestamp") or ""))


def read_rows(root: Path | None = None, *, symbol: str | None = None) -> list[dict[str, Any]]:
    """Every stored reading, oldest first, latest-wins per ``(symbol, hour)``.

    Damage is skipped, never raised: an unparseable line means one hour is missing from a
    coverage count, and a coverage count that under-reports is the safe direction. Compare
    ``pool.read_candidates``, which *does* raise — that store backs a promotion an operator
    signs, and this one backs a number on a board.
    """
    path = oi_1h_path(root)
    if not path.is_file():
        return []
    # Streams the handle rather than `read_text().splitlines()` — see `positioning_store` for the
    # measurement; this store is 2.8 MB and the shape was identical. Deliberately NOT
    # `jsonl.iter_objects`: that fails closed on a bad line and this reader must DEGRADE.
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                key = _row_key(row)
                if not key[0] or not key[1]:
                    continue
                latest[key] = row
    except OSError:
        return []
    if symbol is not None:
        wanted = str(symbol).strip().upper()
        return sorted(
            (r for k, r in latest.items() if k[0] == wanted),
            key=lambda r: str(r.get("timestamp")),
        )
    return sorted(latest.values(), key=lambda r: (str(r.get("symbol")), str(r.get("timestamp"))))


def newest_timestamp(root: Path | None = None, *, symbol: str) -> str | None:
    rows = read_rows(root, symbol=symbol)
    return str(rows[-1]["timestamp"]) if rows else None


def append_rows(rows: Iterable[Mapping[str, Any]], *, symbol: str, root: Path | None = None) -> int:
    """Append readings for one symbol. Returns the count written.

    Rows already held are dropped before the write rather than after: the store is append-only
    and a refresh overlaps the last one by design, so writing every fetched row would grow the
    file by 72 duplicates an hour while the read-side latest-wins hid it.
    """
    name = str(symbol).strip().upper()
    if not name:
        raise ToolError("OI_SYMBOL_MISSING", "an open-interest row needs a symbol")
    path = oi_1h_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code="OI_1H_LOCKED", label="hourly open interest"):
        known = {_row_key(r) for r in read_rows(root, symbol=name)}
        fresh: list[dict[str, Any]] = []
        for row in rows:
            timestamp = str(row.get("timestamp") or "")
            value = row.get("open_interest")
            if not timestamp or not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if (name, timestamp) in known:
                continue
            known.add((name, timestamp))
            record = {
                "record_type": RECORD_TYPE,
                "symbol": name,
                "timestamp": timestamp,
                "open_interest": float(value),
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


def is_due(last_attempt: str | None, now: str) -> bool:
    """Whether this symbol should be asked about again, given when it was last ASKED.

    Not when it was last successfully written: see ``REFRESH_AFTER_SECONDS`` for why measuring
    from the newest stored reading could never skip. Never attempted → due. Unparseable mark →
    due, because a marker that cannot be read should not be able to stop accumulation; the
    append is idempotent, so the cost of being wrong is one redundant request."""
    return refresh_marks.attempt_is_due(last_attempt, now, refresh_after_seconds=REFRESH_AFTER_SECONDS)


def record_intraday_oi(
    *,
    symbol: str,
    feed: Any,
    now: str,
    root: Path | None = None,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    """One throttled refresh for one symbol. Degrade-only: never raises, always reports.

    Called from the cycle's feed step, which runs once per context — twenty times per fire on
    the shipped fan-out. The throttle is what makes that safe: only the first context after the
    hour turns reaches the vendor, and the rest return ``skipped_fresh`` having opened no socket.
    It measures from the last ATTEMPT, recorded whatever the answer was, because measuring from
    the newest stored reading could never skip (see ``REFRESH_AFTER_SECONDS``).

    ``status`` is one of ``seeded`` / ``appended`` / ``skipped_fresh`` / ``skipped_no_feed`` /
    ``degraded``. A vendor failure is ``degraded`` with the reason recorded and nothing written,
    because this store's whole purpose is a long accumulation — a missed hour heals on the next
    refresh, and a raise here would take down a cycle that has real work to do.
    """
    name = str(symbol).strip().upper()
    if feed is None or getattr(feed, "feed_id", "none") == "none":
        return {"symbol": name, "status": "skipped_no_feed", "written": 0}
    if not hasattr(feed, "open_interest_history"):
        return {"symbol": name, "status": "skipped_no_feed", "written": 0}

    last_attempt = read_refresh_marks(root).get(name)
    if not is_due(last_attempt, now):
        return {"symbol": name, "status": "skipped_fresh", "written": 0,
                "last_attempt": last_attempt}

    newest = newest_timestamp(root, symbol=name)
    seeding = newest is None
    days = SEED_DAYS if seeding else REFRESH_DAYS
    # Stamped BEFORE the request, so a fetch that hangs or raises still counts as an attempt. A
    # marker written after a successful return only would leave the failing path unthrottled,
    # which is the path that most needs the throttle.
    record_refresh_attempt(name, now=now, root=root)
    try:
        rows = list(feed.open_interest_history(
            name, days=days, timeout_seconds=timeout_seconds, interval=OI_1H_INTERVAL,
        ))
    except Exception as exc:  # noqa: BLE001 — a collection miss must not fail a cycle
        reason = getattr(exc, "reason_code", None) or type(exc).__name__
        return {"symbol": name, "status": "degraded", "written": 0, "reason": reason}
    try:
        written = append_rows(rows, symbol=name, root=root)
    except ToolError as exc:
        return {"symbol": name, "status": "degraded", "written": 0, "reason": exc.reason_code}
    # The status's "newest" without a third full parse of the store: the newest storable
    # fetched timestamp. A fetched row dropped as a duplicate is a row the store already held
    # at that timestamp, so the maximum over held-newest and fetched-storable is the stored
    # newest either way.
    fetched_newest = max(
        (str(r.get("timestamp") or "") for r in rows
         if str(r.get("timestamp") or "")
         and isinstance(r.get("open_interest"), (int, float))
         and not isinstance(r.get("open_interest"), bool)),
        default="",
    )
    after = [t for t in (newest, fetched_newest) if t]
    return {
        "symbol": name,
        "status": "seeded" if seeding else "appended",
        "written": written,
        "newest": max(after) if after else None,
    }


def coverage(root: Path | None = None, *, symbol: str, now: str | None = None) -> dict[str, Any]:
    """How much hourly history this store holds for one symbol, and whether that is enough.

    ``covered_days`` is the span from the oldest stored hour to the newest — **not** a row
    count divided by 24. A gap in the middle would flatter a row count into reporting more
    coverage than the factory could actually replay, and gaps are exactly what this store's
    self-healing refresh is there to close; ``gap_hours`` reports how many of the hours in that
    span are missing so the span is never read as a completeness claim on its own.
    """
    return _coverage_from(str(symbol).strip().upper(), read_rows(root, symbol=symbol))


def _coverage_from(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "symbol": name, "rows": 0, "covered_days": 0.0,
            "required_days": REQUIRED_COVERAGE_DAYS, "eligible": False,
            "oldest": None, "newest": None, "gap_hours": 0,
        }
    oldest, newest = str(rows[0]["timestamp"]), str(rows[-1]["timestamp"])
    try:
        span_hours = (timeutil.parse_iso(newest) - timeutil.parse_iso(oldest)).total_seconds() / 3600
    except (ValueError, TypeError):
        span_hours = 0.0
    covered_days = round(max(span_hours, 0.0) / 24, 2)
    expected = int(round(span_hours)) + 1
    return {
        "symbol": name,
        "rows": len(rows),
        "covered_days": covered_days,
        "required_days": REQUIRED_COVERAGE_DAYS,
        "eligible": covered_days >= REQUIRED_COVERAGE_DAYS,
        "oldest": oldest,
        "newest": newest,
        "gap_hours": max(0, expected - len(rows)),
    }


def coverage_summary(root: Path | None = None, *, symbols: Iterable[str]) -> dict[str, Any]:
    """Coverage for every traded symbol, plus the weakest one — the number that gates a flip.

    The minimum rather than the mean: a source switch is per timeframe across every symbol the
    pool trades, so the symbol with the least history is the one that decides.
    """
    wanted = sorted({str(s).strip().upper() for s in symbols if s})
    # One parse for the whole board: per-symbol `coverage` reads used to cost one full parse
    # of the same file per symbol.
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in read_rows(root):
        grouped.setdefault(str(row.get("symbol") or ""), []).append(row)
    per_symbol = [_coverage_from(name, grouped.get(name, [])) for name in wanted]
    if not per_symbol:
        return {"symbols": [], "min_covered_days": 0.0,
                "required_days": REQUIRED_COVERAGE_DAYS, "eligible": False}
    return {
        "symbols": per_symbol,
        "min_covered_days": min(c["covered_days"] for c in per_symbol),
        "required_days": REQUIRED_COVERAGE_DAYS,
        "eligible": all(c["eligible"] for c in per_symbol),
    }


__all__ = [
    "OI_1H_FILENAME", "OI_1H_INTERVAL", "REFRESH_MARKS_FILENAME",
    "REQUIRED_COVERAGE_DAYS", "RECORD_TYPE",
    "append_rows", "coverage", "coverage_summary", "is_due", "newest_timestamp",
    "oi_1h_path", "read_refresh_marks", "read_rows", "record_intraday_oi",
    "record_refresh_attempt", "refresh_marks_path",
]
