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
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from runtime.read_only_kernel import integrity

from .. import timeutil
from ..errors import ToolError
from ..filelock import locked
from .market_data import FACTORY_DEPTH_DAYS, TIMEFRAMES
from .paper import state_dir

ARCHIVE_DIRNAME = "candle_archive"
RECORD_TYPE = "archived_candle.v0"

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


def read_rows(
    venue: str, symbol: str, timeframe: str, root: Path | None = None
) -> list[dict[str, Any]]:
    """Every archived candle for one book, oldest first, latest-wins per ``open_time``.

    Never raises on a damaged file — see the module docstring. A line that will not parse, or
    that carries no usable ``open_time``, is skipped.
    """
    path = archive_path(venue, symbol, _require_timeframe(timeframe), root)
    if not path.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
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
            if not isinstance(open_time, str) or not open_time:
                continue
            latest[open_time] = row  # later line wins
    return [latest[k] for k in sorted(latest)]


def newest_open_time(
    venue: str, symbol: str, timeframe: str, root: Path | None = None
) -> str | None:
    """The newest archived bar's open time, or None on an empty book.

    This is what makes a refresh incremental: a caller asks the venue only for what it does not
    already hold, so steady-state runs return a handful of candles instead of five thousand.
    """
    rows = read_rows(venue, symbol, timeframe, root)
    return rows[-1]["open_time"] if rows else None


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
    with locked(path.with_suffix(".lock"), code="ARCHIVE_LOCKED", label="candle archive"):
        known = {row["open_time"] for row in read_rows(venue, symbol, timeframe, root)}
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


def coverage(
    venue: str, symbol: str, timeframe: str, root: Path | None = None
) -> dict[str, Any]:
    """What this book holds, and whether the venue could still supply it.

    ``venue_can_serve`` is the load-bearing field: where it is False the archive is the only
    path to factory depth, so a gap there is permanent and a coverage number that is merely
    "not yet deep enough" means something different from one that is still fillable.
    """
    timeframe = _require_timeframe(timeframe)
    rows = read_rows(venue, symbol, timeframe, root)
    span = ceiling_days(timeframe)
    return {
        "venue": str(venue),
        "symbol": str(symbol),
        "timeframe": timeframe,
        "rows": len(rows),
        "oldest_open_time": rows[0]["open_time"] if rows else None,
        "newest_open_time": rows[-1]["open_time"] if rows else None,
        "ceiling_days": span,
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


def run_candle_archive(
    collector: Any,
    *,
    venue: str,
    now_ms: int,
    dexes: Sequence[str] = ARCHIVE_DEXES,
    timeframes: Sequence[str] = ARCHIVE_TIMEFRAMES,
    timeout_seconds: int = 20,
    root: Path | None = None,
) -> dict[str, Any]:
    """One archive pass: every live symbol on ``dexes``, every timeframe. Never raises.

    The universe is read from the venue rather than declared — see :data:`ARCHIVE_DEXES`. If
    that read fails there is nothing to iterate, so the pass reports why and writes nothing;
    a per-book failure only costs that book, because losing one symbol must not cost the
    other eighty-seven.

    Returns a summary rather than a record: this store feeds nothing, and what an operator
    needs from a fire is how much was kept and what did not answer.
    """
    try:
        symbols = collector.live_symbols(dexes=list(dexes), timeout_seconds=timeout_seconds)
    except Exception as exc:  # noqa: BLE001 — a universe read failure is a quiet no-op, not a crash
        return {"venue": venue, "symbols": 0, "books": 0, "written": 0, "degraded": 0,
                "blocked": True, "reason_code": getattr(exc, "reason_code", type(exc).__name__)}

    written = 0
    degraded: list[str] = []
    books = 0
    for symbol in symbols:
        for timeframe in timeframes:
            books += 1
            result = refresh_book(
                collector, venue=venue, symbol=symbol, timeframe=timeframe,
                now_ms=now_ms, timeout_seconds=timeout_seconds, root=root,
            )
            written += int(result.get("written") or 0)
            if result.get("degraded"):
                degraded.append(f"{symbol}/{timeframe}:{result.get('reason_code')}")
    return {"venue": venue, "symbols": len(symbols), "books": books, "written": written,
            "degraded": len(degraded), "blocked": False,
            # Bounded: a venue-wide outage would otherwise put 352 entries in a status line.
            "degraded_sample": degraded[:5]}
