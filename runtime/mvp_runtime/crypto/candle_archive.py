"""Candle history the runtime retains itself, because this venue serves a rolling window.

``oi_store`` exists because a vendor keeps 84 days. This exists for the same reason one step
worse: Hyperliquid's ``candleSnapshot`` serves at most 5,000 candles per request and **nothing
behind them**, and the window rolls forward with time. Measured 2026-08-03: ``xyz:SP500``
listed 2026-03-18 and returns its full 139 days at 1h (3,326 rows) but only 52 days at 15m
(5,007 rows) — the older 15m history is not paged, it is *gone*.

The consequence is arithmetic. 5,000 candles is 52 days at 15m, 208 at 1h, 833 at 4h and
5,000 at 1d, while ``FACTORY_DEPTH_DAYS`` is 500. **At 15m and 1h the venue can never serve a
factory-depth window**, however old the market gets, because the ceiling moves with it. Those
two timeframes are reachable only by keeping what was served before it rolled away, and every
day not kept is permanently unreachable. That is what this module is for.

**It feeds nothing**, deliberately and on ``oi_store``'s precedent. Features read the
collector, exactly as they do today; this only accumulates and reports :func:`coverage`.
Re-basing a feature source under strategies that can route is an explicit change, not
something a depth threshold should do while nobody is looking.

Shape, and the reason for each:

- **Append-only JSONL, one file per ``(venue, symbol, timeframe)``, latest-wins on read.**
  A refresh overlaps the previous one by design, so a re-fetch has to be idempotent; keyed on
  ``open_time``, it is. A gap shorter than the venue's ceiling self-heals on the next run
  rather than needing a repair path — and a gap longer than it never heals, which is the whole
  argument for running this early.
- **Rows already held are dropped before the write, not after.** The read side would hide
  duplicates, and the file would grow by an overlap-worth of rows every run while doing so.
- **Rows carry ``record_sha256``** (the candidate-store precedent), so tamper evidence starts
  when a row becomes durable.
- **Reads never raise on damage.** A corrupt line is skipped and counted. This store's consumer
  is a coverage number, and reporting *less* coverage than exists is the safe direction; a
  store that refused to answer would take the board down with it.

State is local, per-machine, gitignored — like the paper book, the ledger and ``oi_store``.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from runtime.read_only_kernel import integrity

from .. import timeutil
from ..errors import ToolError
from ..filelock import locked
from .market_data import FACTORY_DEPTH_DAYS, TIMEFRAMES, TOOL_RATE_LIMITED
from .paper import state_dir

ARCHIVE_DIRNAME = "candle_archive"
RECORD_TYPE = "archived_candle.v0"

# The reason code a pass reports when archiving is simply OFF. Named rather than spelled at the
# caller, because the scheduler branches on it: off-on-purpose is a quiet completion and every
# other blocked reason is a FAILED fire that the operator is alerted about. A string literal in
# two files would eventually mean two different strings, and the failure of that drift is an
# outage reported as a normal day.
NOT_ENABLED_REASON = "ARCHIVE_NOT_ENABLED"

# The venue's per-request ceiling, and the reason this module exists. Not a page size: there is
# nothing behind it to page to.
VENUE_CANDLE_CEILING = 5_000

# Which builder dexes are archived. A DEX is a declared decision — a new one is a new deployer,
# a new oracle and a new counterparty (see the lane proposal's §6). The SYMBOLS inside it are
# not declared: the archive takes whatever the dex lists live, because a symbol that appears
# and is not archived loses history that cannot be recovered, and the cost of keeping one more
# is a few megabytes a year. Declaring the symbol list would trade an unrecoverable loss for a
# maintenance chore, in the wrong direction.
ARCHIVE_DEXES: tuple[str, ...] = ("xyz",)

# Every timeframe a strategy can be authored at (`strategy.ALLOWED_TIMEFRAMES`). The two the
# venue can still serve are kept as well as the two it cannot, because a book that is
# recoverable today stops being recoverable the moment the ceiling passes it, and the cost of
# keeping 4h and 1d is a rounding error against 15m.
ARCHIVE_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h", "1d")

# How long to wait between venue reads inside ONE pass.
#
# Measured on the first real pass, 2026-08-04T14:59:57Z: the loop issued its 352 reads as fast
# as it could, roughly 70 answered, and the remaining 282 came back `TOOL_RATE_LIMITED`. The
# fire still reported COMPLETED — `degraded != books` — so an 80% loss was carried in a summary
# string that reaches nobody. 88 symbols x 4 timeframes is simply more than this venue will
# answer in a burst, and no amount of retrying changes that.
#
# Sized off that measurement rather than off the venue's published weights, and deliberately
# under it: ~70 reads went through before the wall, so pacing below one per second keeps a full
# pass inside what was already demonstrated to work. A pass then takes ~6.5 minutes of an hourly
# cadence, which is cost this schedule can trivially afford — the alternative is not a faster
# pass, it is 282 books that never get archived at all.
ARCHIVE_REQUEST_INTERVAL_SECONDS = 1.1

# How many books ONE pass may attempt. Pacing alone would make a full 352-book pass take about
# 6.5 minutes, and that is not free time: `run_due` runs due schedules sequentially, so the pass
# holds the tick — the same tick the live leg's `_settle_or_protect` runs on (the arithmetic is
# spelled out on `PerRunFeedCache`). Trading a bounded archive delay for a 6.5-minute delay to
# position protection is the wrong direction, and it is not a trade this module gets to make
# silently.
#
# So a pass is bounded instead: 100 books x 1.1s is under two minutes of tick, and the rotating
# start offset below means the books this pass did not reach are the ones the next passes start
# from. Full coverage therefore takes a few passes rather than one — which costs nothing real
# here, because what the archive races is a window that rolls at 52 days (15m) and 208 (1h).
# Hours of latency against months of ceiling.
ARCHIVE_BOOKS_PER_PASS = 100

# A filename must round-trip the symbol, and `xyz:XLE` cannot be one on every filesystem.
# Substituted rather than stripped: `xyz:AVGO` and `para:AVGO` are different books, so the
# prefix has to survive into the name.
_COLON_SUB = "__"
_SAFE_NAME = re.compile(r"\A[A-Za-z0-9._-]+\Z")


def archive_dir(root: Path | None = None) -> Path:
    return state_dir(root) / ARCHIVE_DIRNAME


def archive_path(venue: str, symbol: str, timeframe: str, root: Path | None = None) -> Path:
    """The file one ``(venue, symbol, timeframe)`` book lives in.

    Fail-closed on anything that would escape the archive directory: the symbol arrives from a
    venue response, so it is input, and a name that is not plainly a name is refused rather
    than sanitised into something that silently addresses a different book.
    """
    # Each part on its own, not the joined stem: `.` is legal INSIDE a part, so an empty
    # symbol would slip through a whole-stem check as `venue..timeframe` — a name that looks
    # well-formed and addresses a book nothing meant to write.
    parts = (str(venue), str(symbol).replace(":", _COLON_SUB), str(timeframe))
    if not all(part and _SAFE_NAME.fullmatch(part) for part in parts):
        raise ToolError(
            "ARCHIVE_NAME_INVALID",
            f"cannot archive under venue={venue!r} symbol={symbol!r} timeframe={timeframe!r}",
        )
    return archive_dir(root) / f"{'.'.join(parts)}.jsonl"


def _require_timeframe(timeframe: Any) -> str:
    if timeframe not in TIMEFRAMES:
        raise ToolError("ARCHIVE_TIMEFRAME_INVALID", f"unknown timeframe {timeframe!r}")
    return str(timeframe)


def _scan(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """One book's rows plus what had to be discarded to produce them.

    Split from :func:`read_rows` so the damage can be *reported* without changing what every
    existing caller gets back. Discarding quietly and counting nothing is how a store that
    never raises becomes a store nobody can tell is broken.
    """
    damage = {"unreadable_rows": 0, "tampered_rows": 0}
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return [], damage
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                damage["unreadable_rows"] += 1
                continue
            if not isinstance(row, dict):
                damage["unreadable_rows"] += 1
                continue
            open_time = row.get("open_time")
            if not isinstance(open_time, str) or not open_time:
                damage["unreadable_rows"] += 1
                continue
            # The hash was written and never read, which made it a field rather than evidence:
            # a row whose `close` was edited came back as written. Checked here, and the row is
            # SKIPPED rather than raised on — `pool.read_candidates` raises `CANDIDATES_TAMPERED`
            # because it gates a promotion ask, while this store's consumer is a coverage number
            # and reporting less than exists is the safe direction (module docstring). A row
            # carrying no hash at all is a row from before this check and is kept: absence is
            # not a mismatch, and treating it as one would empty every book written until now.
            stored = row.get("record_sha256")
            if stored is not None:
                body = {k: v for k, v in row.items() if k != "record_sha256"}
                if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
                    damage["tampered_rows"] += 1
                    continue
            latest[open_time] = row  # later line wins
    return [latest[k] for k in sorted(latest)], damage


def read_rows(
    venue: str, symbol: str, timeframe: str, root: Path | None = None
) -> list[dict[str, Any]]:
    """Every archived candle for one book, oldest first, latest-wins per ``open_time``.

    Never raises on a damaged file — see the module docstring. A line that will not parse, that
    carries no usable ``open_time``, or whose ``record_sha256`` does not match its own body is
    skipped. :func:`coverage` reports how many of each, because a skip nobody counts is
    indistinguishable from a row that was never written.
    """
    rows, _ = _scan(archive_path(venue, symbol, _require_timeframe(timeframe), root))
    return rows


def _intact(row: Mapping[str, Any]) -> bool:
    """Whether this row still matches the hash it was written with.

    A row carrying no hash predates the check and is intact by definition — see :func:`_scan`
    for why absence cannot be treated as a mismatch.
    """
    stored = row.get("record_sha256")
    if stored is None:
        return True
    body = {k: v for k, v in row.items() if k != "record_sha256"}
    return isinstance(stored, str) and integrity.sha256_record(body) == stored


def _parsed(path: Path) -> list[tuple[str, dict[str, Any]]]:
    """Every readable row as ``(open_time, row)`` — parsed, deliberately **not** verified.

    Hashing every row costs about **3.6x** the parse on a year-long 15m book (measured
    2026-08-04: 35,040 rows, 14.8 MB, 0.68s verified against 0.19s parsed), and
    ``refresh_book`` reads a book twice on every pass. The callers below need a hash decision
    about a handful of rows, not about all of them, so they verify what they are about to
    trust and no more. :func:`read_rows` and :func:`coverage` still verify everything, because
    what they hand back is the data itself.
    """
    out: list[tuple[str, dict[str, Any]]] = []
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            open_time = row.get("open_time")
            if isinstance(open_time, str) and open_time:
                out.append((open_time, row))
    return out


def newest_open_time(
    venue: str, symbol: str, timeframe: str, root: Path | None = None
) -> str | None:
    """The newest archived bar's open time, or None on an empty book.

    This is what makes a refresh incremental: a caller asks the venue only for what it does not
    already hold, so steady-state runs return a handful of candles instead of five thousand.

    Verified from the newest candidate **downward**, stopping at the first row that is intact —
    one hash in the ordinary case instead of one per row. The direction is the safety-relevant
    part: trusting an unverified maximum would let a single edited row claim a future
    ``open_time``, and a refresh sized from that asks for one bar forever while the venue's
    window rolls past everything it is not asking for. Skipping down to the newest row that
    still matches its own hash costs an over-fetch at worst, which `append_candles` drops.

    The file is not globally sorted — a run that refills a gap appends older bars after newer
    ones — so this cannot be a tail read, which is why the scan stays whole and only the
    hashing is made proportional to what is actually used.
    """
    path = archive_path(venue, symbol, _require_timeframe(timeframe), root)
    for open_time, row in sorted(_parsed(path), key=lambda pair: pair[0], reverse=True):
        if _intact(row):
            return open_time
    return None


def append_candles(
    candles: Iterable[Mapping[str, Any]],
    *,
    venue: str,
    symbol: str,
    timeframe: str,
    root: Path | None = None,
) -> int:
    """Append candles for one book. Returns how many were actually written.

    Candles already held are dropped **before** the write: a refresh overlaps the previous one
    on purpose, so writing everything fetched would grow the file by the overlap every run
    while latest-wins hid it on read.
    """
    timeframe = _require_timeframe(timeframe)
    if not str(symbol).strip():
        raise ToolError("ARCHIVE_SYMBOL_MISSING", "an archived candle needs a symbol")
    path = archive_path(venue, symbol, timeframe, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    candles = list(candles)
    with locked(path.with_suffix(".lock"), code="ARCHIVE_LOCKED", label="candle archive"):
        # Only the incoming bars need a "do we already hold this" answer, and the fetch that
        # produced them is capped at `VENUE_CANDLE_CEILING` — so hash-verify that many rows at
        # most, rather than the whole book on every pass (see `_parsed`).
        #
        # A row that FAILS verification is deliberately not counted as known: it is skipped on
        # read anyway, so letting the re-fetch append a good copy lets latest-wins repair the
        # book instead of leaving a hole the archive can never fill.
        incoming = {c.get("open_time") for c in candles}
        known = {
            open_time for open_time, row in _parsed(path)
            if open_time in incoming and _intact(row)
        }
        fresh: list[dict[str, Any]] = []
        for candle in candles:
            open_time = candle.get("open_time")
            close_time = candle.get("close_time")
            if not isinstance(open_time, str) or not open_time:
                continue
            if not isinstance(close_time, str) or not close_time:
                continue
            if open_time in known:
                continue
            try:
                record = {
                    "record_type": RECORD_TYPE,
                    "venue": str(venue),
                    "symbol": str(symbol),
                    "timeframe": timeframe,
                    "open_time": open_time,
                    "close_time": close_time,
                    "open": float(candle["open"]),
                    "high": float(candle["high"]),
                    "low": float(candle["low"]),
                    "close": float(candle["close"]),
                    "volume": float(candle["volume"]),
                }
            except (KeyError, TypeError, ValueError):
                continue  # a malformed bar is dropped, never guessed at
            # Optional legs stay present-and-None rather than absent: "the venue reported
            # nothing" and "this row predates the leg" are different facts, and dropping the
            # key would make them identical on read. Hyperliquid reports none of the flow legs
            # and does report a trade count.
            for leg in ("quote_volume", "trade_count", "taker_buy_base", "taker_buy_quote"):
                value = candle.get(leg)
                record[leg] = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
            record["record_sha256"] = integrity.sha256_record(record)
            known.add(open_time)
            fresh.append(record)
        if not fresh:
            return 0
        fresh.sort(key=lambda r: r["open_time"])
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            for record in fresh:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return len(fresh)


def ceiling_days(timeframe: str) -> float:
    """Calendar days the venue's 5,000-candle ceiling covers at ``timeframe``.

    52 at 15m, 208 at 1h, 833 at 4h, 5000 at 1d. Below ``FACTORY_DEPTH_DAYS`` the venue can
    never serve a factory-depth window on its own — which is the whole reason for this store,
    and why :func:`coverage` reports it rather than leaving a reader to recompute it.
    """
    return TIMEFRAMES[_require_timeframe(timeframe)] * VENUE_CANDLE_CEILING / 1440.0


def _bar_index(open_time: str, minutes: int) -> int | None:
    """``open_time`` as a bar number, so two of them subtract into a bar count."""
    try:
        return int(timeutil.parse_iso(open_time).timestamp() // (minutes * 60))
    except Exception:  # noqa: BLE001 — an unparseable stamp is simply not measurable
        return None


def _gaps(rows: list[Mapping[str, Any]], minutes: int) -> dict[str, Any]:
    """Missing bars between the oldest and newest row held, and the worst single run of them.

    ``None`` rather than ``0`` on a book too small or too damaged to measure: "no gap" and "no
    measurement" are different answers, and the second must not read as the first on the number
    an operator watches to see that a hole is not forming.
    """
    indexes = [i for i in (_bar_index(str(r.get("open_time")), minutes) for r in rows) if i is not None]
    if len(indexes) < 2:
        return {"missing_bars": None, "largest_gap_bars": None}
    indexes.sort()
    largest = max(b - a - 1 for a, b in zip(indexes, indexes[1:]))
    expected = indexes[-1] - indexes[0] + 1
    return {"missing_bars": expected - len(indexes), "largest_gap_bars": largest}


def coverage(
    venue: str, symbol: str, timeframe: str, root: Path | None = None,
    *, now_ms: int | None = None,
) -> dict[str, Any]:
    """What this book holds, whether the venue could still supply it, and what is missing.

    ``venue_can_serve`` is the load-bearing field: where it is False the archive is the only
    path to factory depth, so a gap there is permanent and a coverage number that is merely
    "not yet deep enough" means something different from one that is still fillable.

    **The gap fields exist because the rest of this dict cannot see a hole.** ``rows``,
    ``oldest_open_time`` and ``newest_open_time`` are identical for a contiguous book and for
    one with a month missing out of its middle — so the failure this module was built to
    prevent was the one thing its report could not show. ``missing_bars`` counts every absent
    bar between the ends held; ``largest_gap_bars`` is the worst single run, which is the one
    that decides whether a refresh can still close it.

    ``unreachable_missing_bars`` needs ``now_ms`` and is ``None`` without it, because
    permanence is a question about *when*, not about size: the venue serves the newest
    ``VENUE_CANDLE_CEILING`` bars and nothing behind them, so a ten-bar hole from a year ago is
    as gone as a ten-thousand-bar one, while a large recent hole still refills. A single
    boolean derived from gap size alone would be wrong in the reassuring direction, which is
    why there is not one.
    """
    timeframe = _require_timeframe(timeframe)
    minutes = TIMEFRAMES[timeframe]
    rows, damage = _scan(archive_path(venue, symbol, timeframe, root))
    span = ceiling_days(timeframe)
    gaps = _gaps(rows, minutes)
    unreachable: int | None = None
    if now_ms is not None and gaps["missing_bars"] is not None:
        # The oldest bar the venue can still answer for. Everything missing before it is gone
        # whatever its size, and everything missing after it is what the next refresh recovers.
        floor = int(now_ms // (minutes * 60_000)) - VENUE_CANDLE_CEILING
        held = sorted(i for i in (_bar_index(str(r.get("open_time")), minutes) for r in rows) if i is not None)
        # A gap holds bars `a+1 .. b-1`; those below `floor` are the unreachable ones, so the
        # count is `min(b-1, floor-1) - a` clamped at zero.
        unreachable = sum(
            max(0, min(b - 1, floor - 1) - a) for a, b in zip(held, held[1:])
        )
    return {
        "venue": str(venue),
        "symbol": str(symbol),
        "timeframe": timeframe,
        "rows": len(rows),
        "oldest_open_time": rows[0]["open_time"] if rows else None,
        "newest_open_time": rows[-1]["open_time"] if rows else None,
        "ceiling_days": span,
        **gaps,
        "unreachable_missing_bars": unreachable,
        # Skipped on read and counted here, so a book that is quietly shrinking is visible as
        # something other than a book that was never filled.
        **damage,
        # False => the venue's rolling window is shallower than the factory needs, so whatever
        # is not archived before it rolls is unrecoverable.
        "venue_can_serve_factory_depth": span >= FACTORY_DEPTH_DAYS,
    }


# How much overlap a refresh asks for beyond what it already holds. Two bars rather than zero:
# the newest archived bar may have been the venue's newest at the time, and a request that
# starts exactly at it can return nothing on a venue that treats the bound as exclusive. The
# cost of the overlap is zero rows written (`append_candles` drops what is held) and the cost
# of not having it is a permanent one-bar hole.
REFRESH_OVERLAP_BARS = 2


def refresh_book(
    collector: Any,
    *,
    venue: str,
    symbol: str,
    timeframe: str,
    now_ms: int,
    timeout_seconds: int = 20,
    root: Path | None = None,
) -> dict[str, Any]:
    """Fetch what this book is missing and append it. Returns what happened.

    Incremental by construction: the request is sized from the newest archived bar, so a
    steady-state run asks for a handful of candles and a first run asks for the venue's whole
    window. Never asks for more than the venue can serve — beyond the ceiling there is nothing
    to ask for.

    **Degrades, never raises.** A collector failure returns ``written: 0`` with the reason
    attached, on ``degraded_market_data_record``'s precedent: an archive that stopped the
    caller on one unreachable symbol would take the other eighty-seven with it, and the run
    after this one refills the gap so long as it happens inside the ceiling.
    """
    timeframe = _require_timeframe(timeframe)
    minutes = TIMEFRAMES[timeframe]
    newest = newest_open_time(venue, symbol, timeframe, root)
    if newest is None:
        limit = VENUE_CANDLE_CEILING
    else:
        try:
            held_ms = timeutil.parse_iso(newest).timestamp() * 1000.0
        except Exception:
            held_ms = None
        if held_ms is None:
            limit = VENUE_CANDLE_CEILING
        else:
            missing = int((now_ms - held_ms) // (minutes * 60_000)) + REFRESH_OVERLAP_BARS
            limit = max(1, min(missing, VENUE_CANDLE_CEILING))
    try:
        snapshot = collector.collect(symbol, timeframe, limit=limit, timeout_seconds=timeout_seconds)
    except Exception as exc:  # noqa: BLE001 — every backend failure degrades identically here
        return {"symbol": symbol, "timeframe": timeframe, "requested": limit,
                "written": 0, "degraded": True,
                "reason_code": getattr(exc, "reason_code", type(exc).__name__)}
    # The selector's inert default is not the Mock precisely so this cannot happen — but a
    # caller can construct a collector directly, and a synthetic bar in this store is
    # indistinguishable from a real one a year later. An empty archive is recoverable by
    # turning archiving on; a poisoned one is not, so the check is here as well as there.
    if getattr(snapshot, "is_synthetic", False):
        return {"symbol": symbol, "timeframe": timeframe, "requested": limit,
                "returned": 0, "written": 0, "degraded": True,
                "reason_code": "ARCHIVE_REFUSES_SYNTHETIC"}
    candles = [
        {
            "open_time": c.open_time, "close_time": c.close_time,
            "open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume,
            "quote_volume": c.quote_volume, "trade_count": c.trade_count,
            "taker_buy_base": c.taker_buy_base, "taker_buy_quote": c.taker_buy_quote,
        }
        for c in getattr(snapshot, "candles", [])
    ]
    written = append_candles(candles, venue=venue, symbol=symbol, timeframe=timeframe, root=root)
    return {"symbol": symbol, "timeframe": timeframe, "requested": limit,
            "returned": len(candles), "written": written, "degraded": False}


def plan_pass(
    symbols: Sequence[str], timeframes: Sequence[str], *, venue: str, now_ms: int,
    root: Path | None = None,
) -> list[tuple[str, str]]:
    """The (symbol, timeframe) work list for one pass: first fills first, then a rotating sweep.

    A pass is bounded, so its ORDER decides what gets refreshed and what waits — and, if the
    order never changes, what waits *forever*.

    **A book with no file yet is the only kind that is losing history right now.** The venue
    serves a rolling window, so an unarchived 15m book sheds roughly twelve of its oldest bars
    every hour, permanently. An already-archived book that goes a few passes without a refresh
    sheds nothing: its next refresh sizes itself from the newest bar it holds and refills the
    gap, which is `run_candle_archive`'s "a gap shorter than the ceiling self-heals". So every
    first fill outranks every refresh — the loss is real on one side and zero on the other, and
    within the first fills the fast timeframes go first because only their clock is running.

    **The refreshes then ROTATE, and that half is a bug fix.** This function shipped ordering
    refreshes by timeframe too, which put 4h at work-list index 176 and 1d at 264 — permanently
    past a 100-book budget, whatever the symbol rotation did, because that rotation moves
    symbols inside a timeframe and never crosses the boundary between them. Measured on the
    live store 2026-08-05, straight from the deployed code:

        within budget    : {'15m': 88, '1h': 12}
        never attempted  : {'1h': 76, '4h': 88, '1d': 88}

    176 books would never have been refreshed again. That is the same defect a fixed order plus
    a finite budget produced on the symbol axis one PR earlier, rebuilt on the timeframe axis —
    and worse for being silent: the pass reports `degraded=0` and a `deferred` count that reads
    like "next time".

    Refreshes rotate as one list rather than being ranked, because **no refresh is near its
    ceiling.** 15m is the tightest and a refresh every fourth pass still accrues sixteen bars
    against fifty-two days. Ranking them would be optimising a quantity with no deadline while
    reintroducing the starvation this exists to prevent. The urgency ordering that does matter
    is kept exactly where the deadline is real: the first fills.

    The offset is ``minutes % len(refreshes)``. **While the universe is stable** that advances
    one book per minute, so consecutive windows of any cadence up to ``books_per_pass`` minutes
    overlap rather than leaving gaps, and a full sweep takes ``ceil(len / cadence_minutes)``
    passes — about six at the registered hourly cadence. Minutes rather than a per-pass counter
    for the reason the symbol rotation uses them: a counter would need state, and an
    hour-derived index is identical for every pass of any sub-hourly schedule.

    **The universe is not stable, and the sweep is therefore a rotation rather than a tiling.**
    ``len(refreshes)`` changes whenever the venue lists a symbol or a first fill becomes a
    refresh, and the modulus moves with it — a one-book change in ``len`` moves ``minutes %
    len`` by an arbitrary amount, because ``minutes`` is ~29.6 million. Measured 2026-08-05,
    when the venue listed three symbols in three hours (88 → 91): the observed windows were
    4h-heavy, then 15m/1h, then 1h, then 15m again, which is not the ``+cadence`` walk the
    paragraph above describes and which an earlier version of this docstring claimed without
    qualification.

    That is not a defect and it is deliberately not "fixed" — coverage is what this rotation
    owes. Enumerated against the live store the same day, every window of six consecutive
    hourly passes covers **364 of 364 books, 0 never reached**; twelve and twenty-four passes
    likewise. What the jumping costs is the *sequential* reading — successive passes are not
    adjacent slices, so "the next pass picks up where this one stopped" is not true and
    ``deferred`` must not be read that way.

    **Coverage is conditional, though, and the condition is a race worth naming.** A new symbol
    inserts its books into the 15m region and pushes every later book forward; the offset
    advances one book per minute. Coverage holds while the offset outruns the growth, and fails
    when it does not — measured, not assumed: at one listing per five minutes (0.8 books/min
    against 1.0) eight of eighty books went unreached across eighty passes. At the registered
    hourly cadence the margin is 60 books per pass against 4 per listing, and the observed rate
    is about one listing an hour — 15x. `test_coverage_survives_a_universe_that_keeps_growing`
    pins the realistic side; the failing rate is recorded here rather than in a test because
    what matters operationally is the margin, and the margin is a fact about the venue.

    **Do not measure this by watching a timeframe's candle count.** A 1d book refreshed at
    17:00 returns nothing because no 1d bar has closed since 00:00 UTC, so `1d: +0` is what
    both a healthy sweep and a starved one produce — that conflation cost a wrong "needs
    investigating" call on 2026-08-05. Enumerate ``plan_pass`` against the store instead; it
    answers directly and is what found the #544 defect in the first place.

    Existence is a `stat`, deliberately not `newest_open_time` — that parses and hashes a whole
    book, and 352 of them at the top of every pass would cost more than the pass.
    """
    urgency = {timeframe: index for index, timeframe in enumerate(timeframes)}
    first_fills: list[tuple[str, str]] = []
    refreshes: list[tuple[str, str]] = []
    for timeframe in timeframes:
        for symbol in symbols:
            target = archive_path(venue, symbol, _require_timeframe(timeframe), root)
            (refreshes if target.exists() else first_fills).append((symbol, timeframe))
    # Already timeframe-major, so this only restates the intent; stable, so the caller's
    # rotated symbol order survives inside each timeframe.
    first_fills.sort(key=lambda book: urgency[book[1]])
    if refreshes:
        offset = (now_ms // 60_000) % len(refreshes)
        refreshes = refreshes[offset:] + refreshes[:offset]
    return first_fills + refreshes


def run_candle_archive(
    collector: Any,
    *,
    venue: str,
    now_ms: int,
    dexes: Sequence[str] = ARCHIVE_DEXES,
    timeframes: Sequence[str] = ARCHIVE_TIMEFRAMES,
    timeout_seconds: int = 20,
    root: Path | None = None,
    request_interval_seconds: float = ARCHIVE_REQUEST_INTERVAL_SECONDS,
    books_per_pass: int = ARCHIVE_BOOKS_PER_PASS,
    sleep: Callable[[float], Any] | None = None,
) -> dict[str, Any]:
    """One archive pass: a bounded slice of the universe. Never raises.

    The universe is read from the venue rather than declared — see :data:`ARCHIVE_DEXES`. If
    that read fails there is nothing to iterate, so the pass reports why and writes nothing;
    a per-book failure only costs that book, because losing one symbol must not cost the
    other eighty-seven.

    **Paced, bounded, and it stops when the venue says stop.** Reads are spaced by
    ``request_interval_seconds`` and a pass attempts at most ``books_per_pass`` of them (both
    constants carry the measurement that sized them). A ``TOOL_RATE_LIMITED`` answer latches:
    the pass ends there rather than issuing the rest. That is `PerRunFeedCache`'s posture, for
    its reason — a 429 is not "try again", it is the step before a 418 ban, so continuing to
    knock is the one response that makes it worse.

    Three counts, because three different things happen to a book and only one of them is
    evidence about that book: ``degraded`` is "asked, and it did not answer", ``skipped`` is
    "inside this pass's budget but never asked, because the rate limit latched first", and
    ``deferred`` is "outside this pass's budget", which is the normal design and not a fault.
    Folding them together is what let an 80% loss read like a completed day.

    **The pass is ordered by what is perishing, not by symbol** — `plan_pass` decides, and
    carries the reasoning. Briefly: every first fill before every refresh, fast timeframes first
    inside the first fills, and then the refreshes as one ROTATING sweep so that no book can sit
    permanently past the budget. A bounded pass that walked symbol-major gave a quarter of the
    symbols all four of their timeframes and the rest nothing; ranking the refreshes too then
    put 4h and 1d past the budget forever. Both are the same failure — a fixed order and a
    finite budget — and `plan_pass` records the measurement for each.

    **Iteration starts at a rotating offset.** Every pass used to walk the venue's order from
    the top, so a pass that ended early always ended in the same place: the first real pass
    archived the first twenty symbols alphabetically and none of the other sixty-eight, and an
    hourly cadence would have repeated exactly that, forever. The tail was not slow to fill, it
    was unreachable. Rotating costs nothing — every book is independent — and converts a
    permanent starvation into a delay.

    Returns a summary rather than a record: this store feeds nothing, and what an operator
    needs from a fire is how much was kept and what did not answer.
    """
    # Resolved here rather than as a default argument, because a default binds `time.sleep` at
    # import and no later patch of it can be seen — which would make every test that reaches
    # this through the scheduler wait in real seconds.
    wait = sleep if sleep is not None else time.sleep
    try:
        symbols = collector.live_symbols(dexes=list(dexes), timeout_seconds=timeout_seconds)
    except Exception as exc:  # noqa: BLE001 — a universe read failure is a quiet no-op, not a crash
        return {"venue": venue, "symbols": 0, "books": 0, "written": 0, "degraded": 0,
                "skipped": 0, "deferred": 0, "rate_limited": False,
                "blocked": True, "reason_code": getattr(exc, "reason_code", type(exc).__name__)}

    # Minutes, not hours: the offset has to move between passes at whatever cadence this kind is
    # registered at, and an hour-derived offset would be identical for all four passes of a
    # 15-minute schedule — reintroducing the fixed order this exists to break.
    order = list(symbols)
    if order:
        offset = (now_ms // 60_000) % len(order)
        order = order[offset:] + order[:offset]

    work = plan_pass(order, timeframes, venue=venue, now_ms=now_ms, root=root)

    planned = min(len(work), max(1, books_per_pass))
    written = 0
    degraded: list[str] = []
    books = 0
    rate_limited = False
    for symbol, timeframe in work[:planned]:
        if books:  # pace between reads, never before the first
            wait(request_interval_seconds)
        books += 1
        result = refresh_book(
            collector, venue=venue, symbol=symbol, timeframe=timeframe,
            now_ms=now_ms, timeout_seconds=timeout_seconds, root=root,
        )
        written += int(result.get("written") or 0)
        if result.get("degraded"):
            degraded.append(f"{symbol}/{timeframe}:{result.get('reason_code')}")
        if result.get("reason_code") == TOOL_RATE_LIMITED:
            rate_limited = True
            break
    return {"venue": venue, "symbols": len(symbols), "books": books, "written": written,
            "degraded": len(degraded),
            "skipped": planned - books,                                  # budgeted, never asked
            "deferred": len(work) - planned,                             # next pass's slice
            "rate_limited": rate_limited, "blocked": False,
            # Bounded: a venue-wide outage would otherwise put 352 entries in a status line.
            "degraded_sample": degraded[:5]}
