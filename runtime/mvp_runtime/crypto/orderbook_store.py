"""Resting-liquidity history the runtime retains itself, because the venue keeps NONE.

``oi_store`` exists because a vendor keeps 84 days, ``positioning_store`` because one keeps 30,
``candle_archive`` because a venue serves a rolling 5,000-candle window. This exists for the same
reason taken to its limit: ``/fapi/v1/depth`` answers with the book as it is right now and there
is no endpoint, free or paid on the terms this runtime accepts, that will say what it was an hour
ago. **Retention is zero.** Every other store in this package could be switched on late and still
seed itself; this one records only what it is running for.

**Why the resting book is worth the wait.** Every column the feature row carries describes a
trade that happened or a position outstanding: OHLCV and the taker aggressor split are executions,
funding and the premium index are the cost of holding, open interest and the positioning ratios
are inventory. None of them is an order that is still *waiting*. So nothing in the vocabulary can
distinguish a move into a thin book from the same move into a deep one, and that is the whole
question a liquidity-aware entry asks. The data-gap review named it independently on 2026-08-15
(``order_book_imbalance``), and it was the one suggestion of five that no collected series covers.

**What this costs and when it can pay — both measured before it was written.** 576 rows a day over
the six-member cohort, 0.17 GB across the full window, and 1,152 request-weight a day against a
2,400-per-MINUTE cap. The cost is not the constraint. ``FACTORY_DEPTH_DAYS`` is 1000 and this
starts at zero, so nothing may mint against it until **2029-05-11** — against ``oi_store`` at 100
days and ``positioning_store`` at 46 on the day this shipped, both of which bought their head
start from a vendor's retention window. There is no equivalent purchase available here, which is
precisely the argument for starting now rather than later: the 1000 days are 1000 days away
whenever collection begins, and today is the earliest that clock can start.

**It feeds nothing**, deliberately, on the precedent of all three stores above. No feature reads
it, no family mints against it, ``snapshot`` is untouched — so the features, the backtest and the
live router are byte-identical to what they were without it. :func:`coverage` reports progress and
the flip stays an explicit change, because re-basing a feature source under strategies that can
route is not something a threshold should do while nobody is looking.

Shape, and where it departs from the stores it copies:

- **Append-only JSONL, one row per ``(symbol, period start)``, latest-wins on read** — the shared
  idiom, and rows carry ``record_sha256`` so tamper evidence starts when a row becomes durable.
- **A period already held is never re-written, so the FIRST snapshot in a period wins.** The other
  stores are indifferent to this because they re-fetch a value the venue considers settled; here
  the second snapshot of a period is a genuinely different book, and "first" is chosen only
  because it is the one that is deterministic. What it is not is representative — see
  :data:`ORDERBOOK_PERIOD`.
- **A gap NEVER self-heals.** The refresh machinery the other stores carry — ``refresh_rows_for``,
  ``SEED_ROWS``, a request sized to the hole — has no analogue and its absence is not an
  oversight: there is nothing to ask for. A period missed is gone, so :func:`coverage` reports
  ``gap_periods`` as a permanent fact rather than a queue of work.
- **Derived scalars are stored, never the levels.** Keeping the top-20 levels raw was priced at
  12.96 GB per 1000 days against 0.17 GB for this, and declined. The consequence is stated at
  :data:`~.market_data.ORDER_BOOK_LEVELS`: the depth band is frozen at collection time and no
  later reader can re-cut it.
- **Reads never raise on damage.** A corrupt line is skipped and counted, because this store's one
  consumer is a coverage number and reporting less coverage than exists is the safe direction.

State is local, per-machine, gitignored — like the paper book, the ledger and the other stores.
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
    ORDER_BOOK_LEVELS,
    POSITIONING_PERIOD_SECONDS,
)
from .paper import state_dir

ORDERBOOK_FILENAME = "orderbook_depth.jsonl"
RECORD_TYPE = "orderbook_depth.v0"

# The cadence the store keeps, and the one number here that is a compromise rather than a
# measurement. 15m is the pool's own fan-out cadence, so this rides an existing fire and needs no
# new schedule — the alternative priced in the estimate was a 60s schedule, the scheduler's floor
# and a cadence class nothing on this host runs, to buy intra-period averaging.
#
# **What 15m gives up, on the record.** One snapshot per 15 minutes is a point sample of a
# quantity that moves continuously, so a row is an unbiased draw from the period and NOT the
# period's mean. A family reading a single row is reading that noise; a family reading a
# multi-row window is averaging it down at the usual rate. That is a real limit on what this
# series can ever support, it is knowable now rather than in 2029, and it is written here because
# the cheapest moment to discover it is before 1000 days of rows exist in this shape.
ORDERBOOK_PERIOD = "15m"
PERIOD_SECONDS = POSITIONING_PERIOD_SECONDS[ORDERBOOK_PERIOD]

# A symbol is asked about again when its last ATTEMPT is older than one period.
#
# From the last ATTEMPT, not from the newest stored row, and the distinction is inherited rather
# than rediscovered: ``oi_store`` shipped the reading-age version first and it never skipped,
# ``positioning_store`` documented why. Here it is sharper still — the newest stored row is
# stamped to the CURRENT period the moment the first snapshot lands, so a reading-age throttle
# would read as "fresh" for the rest of the period, which is right, and then as "due" the instant
# the period turned even if the attempt had already failed, which is how a refusing endpoint
# becomes twenty knocks a fire.
REFRESH_AFTER_SECONDS = PERIOD_SECONDS

# Where the attempt marks live: a small JSON map, one entry per symbol, rewritten whole — the
# ``oi_store`` / ``positioning_store`` / ``routing_marks`` idiom.
REFRESH_MARKS_FILENAME = "orderbook_refresh.json"

# Coverage before a feature could read this: the factory's own replay span, as with every other
# accumulator. Not a new number — eligibility means "the store can answer every bar the factory
# asks about", and any smaller threshold would flip a feature source onto a window with holes.
REQUIRED_COVERAGE_DAYS = FACTORY_DEPTH_DAYS

# The band the imbalance is summed over. Re-exported from `market_data` rather than restated,
# because a store whose rows claim a band and a collector that fetches another would be
# undetectable in the data and permanent in the file.
DEPTH_LEVELS = ORDER_BOOK_LEVELS


def orderbook_path(root: Path | None = None) -> Path:
    return state_dir(root) / ORDERBOOK_FILENAME


def refresh_marks_path(root: Path | None = None) -> Path:
    return state_dir(root) / REFRESH_MARKS_FILENAME


def period_start(now: str) -> str:
    """The 15-minute boundary ``now`` falls in, as the row's timestamp.

    The snapshot is taken at an arbitrary instant; the row is keyed to the period containing it.
    That is what makes a re-ask inside one period a duplicate rather than a second row, and what
    lets :func:`coverage` count periods against a grid instead of counting rows.

    An unparseable ``now`` raises rather than defaulting. Every other read in this module degrades,
    and this one must not: a row stamped to a guessed period is durable, wrong, and
    indistinguishable afterwards from a correct one — the failure the store's own reads degrade in
    order to avoid making.
    """
    try:
        moment = timeutil.parse_iso(now)
    except (ValueError, TypeError):
        raise ToolError("ORDERBOOK_TIMESTAMP_INVALID", "an order-book row needs a parseable time") from None
    # Floored on the wall clock, not on the epoch: the 15-minute grid every other series in this
    # runtime uses is the candle grid, whose boundaries are :00/:15/:30/:45 of the hour. Those
    # coincide with epoch-modulo boundaries today and would stop coinciding the moment a period
    # that does not divide the hour were introduced, which is exactly when a silent one-row-per-
    # period drift would be hardest to see.
    floored = moment.replace(
        minute=(moment.minute // (PERIOD_SECONDS // 60)) * (PERIOD_SECONDS // 60),
        second=0,
        microsecond=0,
    )
    return timeutil.format_iso(floored)


def summarize_book(book: Mapping[str, Any]) -> dict[str, float]:
    """The scalars one snapshot becomes. Pure, so the arithmetic is testable without a network.

    Returns ``bid_depth`` / ``ask_depth`` (summed quantity over the band, base units),
    ``imbalance`` in ``[-1, 1]``, ``spread_bps`` and ``mid``.

    ``imbalance`` is ``(bid - ask) / (bid + ask)`` — positive means resting bids outweigh resting
    asks. Bounded by construction, which is what makes it storable as durable evidence: a ratio
    would be unbounded and a difference would not compare across symbols whose books differ by
    three orders of magnitude in size.

    The interval is CLOSED, and the reason is the rounding rather than the arithmetic. Both sides
    are positive here — an empty one raises below — so the quotient is strictly inside the open
    interval, but ``round(..., 8)`` reaches ``±1.0`` once one side outweighs the other by more
    than about 10^8. That is a real book (one side of the band effectively empty) rather than a
    defect, so it is stored as the endpoint rather than nudged off it; what would be a defect is
    a docstring promising an open interval that the stored rows do not honour.

    Raises on a book this cannot describe rather than returning a sentinel. An empty side, or a
    crossed one where the best bid is at or above the best ask, is not a thin market — it is a
    payload that does not mean what this function would claim it means, and the caller writes
    what this returns straight into an append-only file.
    """
    bids = list(book.get("bids") or ())
    asks = list(book.get("asks") or ())
    if not bids or not asks:
        raise ToolError("ORDERBOOK_SIDE_EMPTY", "an order-book snapshot needs both sides")
    best_bid, best_ask = float(bids[0][0]), float(asks[0][0])
    if not best_bid < best_ask:
        raise ToolError("ORDERBOOK_CROSSED", "order-book best bid was not below best ask")

    bid_depth = sum(float(quantity) for _price, quantity in bids)
    ask_depth = sum(float(quantity) for _price, quantity in asks)
    total = bid_depth + ask_depth
    if total <= 0.0:
        raise ToolError("ORDERBOOK_DEPTH_EMPTY", "order-book snapshot held no resting quantity")

    mid = (best_bid + best_ask) / 2.0
    return {
        "bid_depth": round(bid_depth, 8),
        "ask_depth": round(ask_depth, 8),
        "imbalance": round((bid_depth - ask_depth) / total, 8),
        "spread_bps": round((best_ask - best_bid) / mid * 10_000, 6),
        "mid": round(mid, 8),
    }


def _mark_key(symbol: str) -> str:
    return str(symbol).strip().upper()


def read_refresh_marks(root: Path | None = None) -> dict[str, str]:
    """``{"SYMBOL": last attempt}``. Damage reads as no marks, which means "ask again".

    Fail-open toward asking, the opposite direction from this store's row reads and for
    ``positioning_store``'s reason: a corrupt marks file that read as "recently attempted" would
    silently stop the accumulation while the board reported whatever coverage it already had. Here
    the asymmetry is starker than it was there — an over-ask costs one request and is discarded as
    a duplicate; an under-ask costs a period that no later run can recover.
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


def record_refresh_attempt(symbol: str, *, now: str, root: Path | None = None) -> None:
    """Mark that the venue was asked about this symbol, whatever the answer was.

    Recorded on failure too, for ``positioning_store``'s reason: a marker written only on success
    turns a refusing endpoint into one ask per context per fire.
    """
    name = _mark_key(symbol)
    if not name:
        return
    directory = state_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = refresh_marks_path(root)
    with locked(path.with_suffix(".lock"), code="ORDERBOOK_MARKS_LOCKED",
                label="order-book refresh marks"):
        marks = read_refresh_marks(root)
        marks[name] = now
        path.write_text(json.dumps(marks, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _row_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (str(row.get("symbol") or ""), str(row.get("timestamp") or ""))


def read_rows(root: Path | None = None, *, symbol: str | None = None) -> list[dict[str, Any]]:
    """Every stored snapshot, oldest first, latest-wins per ``(symbol, period)``.

    Damage is skipped, never raised — see the module docstring. Streams the handle rather than
    ``read_text().splitlines()``, which ``positioning_store`` measured at 9.71 MB peak against
    0.16 MB on a 4.5 MB store. Deliberately not ``jsonl.iter_objects``: that fails closed on a bad
    line and this reader must degrade.
    """
    path = orderbook_path(root)
    if not path.is_file():
        return []
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
                if not all(key):
                    continue
                latest[key] = row
    except OSError:
        return []

    wanted = str(symbol).strip().upper() if symbol is not None else None
    selected = [r for k, r in latest.items() if wanted is None or k[0] == wanted]
    return sorted(selected, key=lambda r: (str(r.get("symbol")), str(r.get("timestamp"))))


def newest_timestamp(root: Path | None = None, *, symbol: str) -> str | None:
    rows = read_rows(root, symbol=symbol)
    return str(rows[-1]["timestamp"]) if rows else None


def append_snapshot(
    summary: Mapping[str, Any], *, symbol: str, timestamp: str, root: Path | None = None
) -> int:
    """Append one period's snapshot. Returns 1 if written, 0 if the period was already held.

    Zero rather than an error on a duplicate, because a duplicate is the throttle's expected
    outcome under overlap (the cohort sweep and the operator's single-symbol cycle can both reach
    one symbol in one fire) and not a fault. The period already held wins — see the module
    docstring on why "first" and not "latest".
    """
    name = str(symbol).strip().upper()
    if not name:
        raise ToolError("ORDERBOOK_SYMBOL_MISSING", "an order-book row needs a symbol")
    stamp = str(timestamp or "")
    if not stamp:
        raise ToolError("ORDERBOOK_TIMESTAMP_MISSING", "an order-book row needs a timestamp")

    path = orderbook_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code="ORDERBOOK_LOCKED", label="order-book history"):
        known = {_row_key(r) for r in read_rows(root, symbol=name)}
        if (name, stamp) in known:
            return 0
        record = {
            "record_type": RECORD_TYPE,
            "symbol": name,
            "period": ORDERBOOK_PERIOD,
            "timestamp": stamp,
            "levels": int(DEPTH_LEVELS),
            "bid_depth": float(summary["bid_depth"]),
            "ask_depth": float(summary["ask_depth"]),
            "imbalance": float(summary["imbalance"]),
            "spread_bps": float(summary["spread_bps"]),
            "mid": float(summary["mid"]),
        }
        record["record_sha256"] = integrity.sha256_record(record)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return 1


def is_due(last_attempt: str | None, now: str) -> bool:
    """Whether this symbol should be asked about again, given when it was last ASKED.

    Never attempted → due. Unparseable mark → due, because state this store cannot read must never
    be able to stop accumulation; the append is idempotent, so being wrong costs one request.
    """
    if not last_attempt:
        return True
    try:
        elapsed = (timeutil.parse_iso(now) - timeutil.parse_iso(last_attempt)).total_seconds()
    except (ValueError, TypeError):
        return True
    return elapsed >= REFRESH_AFTER_SECONDS


def record_orderbook(
    *,
    symbol: str,
    collector: Any,
    now: str,
    root: Path | None = None,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    """One throttled snapshot for one symbol. Never raises.

    Called from the cycle's cohort sweep, and from the operator's single-symbol cycle when it opts
    into accumulation. The throttle is what makes that safe: only the first caller after the period
    turns reaches the venue, and the rest return ``skipped_fresh`` having opened no socket.

    A collector without the capability is ``skipped_no_feed``, not a failure — that is how a mock
    run, a degraded selection, and a venue this endpoint does not exist on all report the same
    honest thing.
    """
    name = _mark_key(symbol)
    if collector is None or not hasattr(collector, "order_book"):
        return {"symbol": name, "status": "skipped_no_feed", "written": 0}

    last_attempt = read_refresh_marks(root).get(name)
    if not is_due(last_attempt, now):
        return {"symbol": name, "status": "skipped_fresh", "written": 0,
                "last_attempt": last_attempt}

    try:
        stamp = period_start(now)
    except ToolError as exc:
        return {"symbol": name, "status": "degraded", "written": 0, "reason": exc.reason_code}

    # Stamped BEFORE the request, so a fetch that hangs or raises still counts as an attempt.
    record_refresh_attempt(name, now=now, root=root)
    try:
        book = collector.order_book(name, limit=DEPTH_LEVELS, timeout_seconds=timeout_seconds)
    except Exception as exc:  # noqa: BLE001 — a collection miss must not fail a cycle
        reason = getattr(exc, "reason_code", None) or type(exc).__name__
        return {"symbol": name, "status": "degraded", "written": 0, "reason": reason}

    try:
        summary = summarize_book(book if isinstance(book, Mapping) else {})
        written = append_snapshot(summary, symbol=name, timestamp=stamp, root=root)
    except ToolError as exc:
        return {"symbol": name, "status": "degraded", "written": 0, "reason": exc.reason_code}

    return {
        "symbol": name,
        "status": "appended" if written else "skipped_duplicate",
        "written": written,
        "timestamp": stamp,
        "imbalance": summary["imbalance"],
    }


def coverage(root: Path | None = None, *, symbol: str, now: str | None = None) -> dict[str, Any]:
    """How much history this store holds for one symbol, and whether it is enough.

    ``covered_days`` is the span from oldest to newest snapshot — not a row count divided by 96.
    ``gap_periods`` reports how many periods inside that span hold no row, so the span is never
    read as a completeness claim on its own.

    The gap number means something different here than it does in the other stores, and the
    difference is the point: there it is a queue of work the next refresh closes, here it is a
    permanent hole. Same field name, and that is a hazard worth naming — a reader who learned the
    word on ``positioning_store.coverage`` will read this one as recoverable. It is not.
    """
    rows = read_rows(root, symbol=symbol)
    name = str(symbol).strip().upper()
    if not rows:
        return {
            "symbol": name, "rows": 0, "covered_days": 0.0,
            "required_days": REQUIRED_COVERAGE_DAYS, "eligible": False,
            "oldest": None, "newest": None, "gap_periods": 0,
        }
    oldest, newest = str(rows[0]["timestamp"]), str(rows[-1]["timestamp"])
    try:
        span_seconds = (timeutil.parse_iso(newest) - timeutil.parse_iso(oldest)).total_seconds()
    except (ValueError, TypeError):
        span_seconds = 0.0
    span_seconds = max(span_seconds, 0.0)
    expected = int(round(span_seconds / PERIOD_SECONDS)) + 1
    return {
        "symbol": name,
        "rows": len(rows),
        "covered_days": round(span_seconds / 86400, 2),
        "required_days": REQUIRED_COVERAGE_DAYS,
        "eligible": round(span_seconds / 86400, 2) >= REQUIRED_COVERAGE_DAYS,
        "oldest": oldest,
        "newest": newest,
        "gap_periods": max(0, expected - len(rows)),
    }


def coverage_summary(root: Path | None = None, *, symbols: Iterable[str]) -> dict[str, Any]:
    """Coverage across every symbol the store must cover, plus the weakest cell.

    The minimum rather than the mean, for the reason ``oi_store`` gives: a source flip would be
    across every symbol the pool trades, so the weakest cell is the one that decides.
    """
    wanted = sorted({str(s).strip().upper() for s in symbols if s})
    cells = [coverage(root, symbol=name) for name in wanted]
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
    "DEPTH_LEVELS",
    "ORDERBOOK_FILENAME",
    "ORDERBOOK_PERIOD",
    "RECORD_TYPE",
    "REFRESH_MARKS_FILENAME",
    "REQUIRED_COVERAGE_DAYS",
    "append_snapshot",
    "coverage",
    "coverage_summary",
    "is_due",
    "newest_timestamp",
    "orderbook_path",
    "period_start",
    "read_refresh_marks",
    "read_rows",
    "record_orderbook",
    "record_refresh_attempt",
    "refresh_marks_path",
    "summarize_book",
]
