"""PROTECTION_UNKNOWN escalation: a clock per live position whose protective legs cannot be read.

Thomas 2026-09-30, ``docs/proposals/PROTECTION_UNKNOWN_ESCALATION_V0.1.md`` (D1–D4 as recommended).

``live_leg.read_bracket_legs`` answers ``PROTECTION_UNKNOWN`` when a leg's query failed or the record
carries no bracket id. The route holds such a position and never closes it on a guess, and that
stays. Until this module, holding was ALL it did: no message, no effect on other entries, for as
long as the read kept failing.

The API error breaker could not bound it. ``fetch_order`` is a read-class call, and reconciliation's
account read resets the read streak on every pass, so the streak never reaches its limit. A missing
bracket id makes no call at all.

So this keeps a wall-clock timer per position, in its own store (D4: not on the live position book,
whose write rules belong to PR2b-2), and the route escalates on it:

- **U0 observe:** the first UNKNOWN pass records ``unknown_since``. Hold and time exit, as before.
- **U1 notify and hold new entries:** UNKNOWN for ``PROTECTION_UNKNOWN_NOTIFY_MINUTES``, or at once
  for a record with no bracket id, since no retry can fix that. The operator gets one message, and
  no context opens a new live position while any watched position is at U1 or above.

  The design said "halt the pass" (``record["halt"]``). The cycle's fan-out halt skips every
  remaining context, which would also skip the settlement, protection re-check and time exit of
  positions on other symbols, pass after pass, for as long as the read failed. The same document
  requires that nothing here trap an open position. The entry refusal
  (:func:`entries_blocking`, read by the route before its entry decision) keeps the document's
  intent, no new exposure while one position's protection is unverified, without that cost.
- **U2 HARD halt:** UNKNOWN for ``PROTECTION_UNKNOWN_HARD_HALT_MINUTES``. The route tightens the
  control state to HARD through ``control.apply_command`` (D2, decision 47: raise-only, actor
  ``system:protection_watch``). Only the authenticated operator loosens it. Exits and protection
  still go out under HARD.

A definite read (PROTECTED or UNPROTECTED) clears a position's entry, and a position no longer on the
book is pruned. The HARD halt is not cleared by either: it is the operator's to lift.

Failure directions:
- **Unreadable store:** every UNKNOWN position reads as U1, never U2 on missing evidence, and new
  entries are held.
- **Failed write:** the pass still reads the position at U1 at least, because a clock that cannot
  be written would otherwise restart at U0 on every pass.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from .. import timeutil
from ..errors import MvpRuntimeError, ToolError
from ..filelock import locked
from .state import state_dir

PROTECTION_WATCH_VERSION = "live_protection_watch.v0.1"
WATCH_FILENAME = "live_protection_watch.json"
WATCH_LOCK_FILENAME = "live_protection_watch.lock"

# Thomas 2026-09-30, D1. Two 15-minute passes, so a single transport blip says nothing; four passes
# to the HARD halt, well inside any strategy's max-hold. Proposals, not measurements: the live leg
# has traded too few orders to measure an UNKNOWN duration against.
PROTECTION_UNKNOWN_NOTIFY_MINUTES = 30
PROTECTION_UNKNOWN_HARD_HALT_MINUTES = 60

LEVEL_OBSERVE = "U0"
LEVEL_NOTIFY = "U1"
LEVEL_HARD = "U2"
_LEVEL_RANK = {None: -1, LEVEL_OBSERVE: 0, LEVEL_NOTIFY: 1, LEVEL_HARD: 2}

KIND_READ_FAILED = "READ_FAILED"
KIND_IDS_MISSING = "IDS_MISSING"

ACTOR = "system:protection_watch"

PERSISTING = "LIVE_PROTECTION_UNKNOWN_PERSISTING"
HARD_HALT = "LIVE_PROTECTION_UNKNOWN_HARD_HALT"
HARD_HALT_UNAPPLIED = "LIVE_PROTECTION_HARD_HALT_UNAPPLIED"
WATCH_UNREADABLE = "LIVE_PROTECTION_WATCH_UNREADABLE"
WATCH_UNRECORDED = "LIVE_PROTECTION_WATCH_UNRECORDED"


def watch_path(root: Path | None = None) -> Path:
    return state_dir(root) / WATCH_FILENAME


def _lock_path(root: Path | None) -> Path:
    return state_dir(root) / WATCH_LOCK_FILENAME


def position_key(position: Mapping[str, Any]) -> str | None:
    value = position.get("position_id")
    return value if isinstance(value, str) and value else None


def unknown_kind(legs: Mapping[str, Any], *, ids_missing_code: str) -> str:
    """IDS_MISSING when any leg could not be queried because the record names no id; READ_FAILED
    otherwise. The first is a booking defect no retry fixes, so it escalates at once."""
    for leg in legs.get("legs") or ():
        if isinstance(leg, Mapping) and leg.get("error") == ids_missing_code:
            return KIND_IDS_MISSING
    return KIND_READ_FAILED


def level_for(entry: Mapping[str, Any], *, now: str) -> str:
    """The level an entry has reached at ``now``: wall time from ``unknown_since``, never a pass count,
    so a pass the scheduler skipped does not reset the clock."""
    minutes = _minutes_since(entry.get("unknown_since"), now)
    if minutes is not None and minutes >= PROTECTION_UNKNOWN_HARD_HALT_MINUTES:
        return LEVEL_HARD
    if entry.get("kind") == KIND_IDS_MISSING:
        return LEVEL_NOTIFY
    if minutes is not None and minutes >= PROTECTION_UNKNOWN_NOTIFY_MINUTES:
        return LEVEL_NOTIFY
    return LEVEL_OBSERVE


def _minutes_since(then: Any, now: str) -> float | None:
    try:
        return (timeutil.parse_iso(str(now)) - timeutil.parse_iso(str(then))).total_seconds() / 60.0
    except (TypeError, ValueError):
        return None


def read_watch(root: Path | None = None) -> dict[str, dict[str, Any]]:
    """The watched positions by key. Empty when the store does not exist; raises
    ``LIVE_PROTECTION_WATCH_UNREADABLE`` when it exists and cannot be read as one."""
    path = watch_path(root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(WATCH_UNREADABLE, f"the protection watch store is unreadable: {exc}") from None
    entries = data.get("entries") if isinstance(data, Mapping) else None
    if not isinstance(entries, Mapping) or data.get("version") != PROTECTION_WATCH_VERSION:
        raise ToolError(WATCH_UNREADABLE, "the protection watch store is not a watch record")
    return {str(k): dict(v) for k, v in entries.items() if isinstance(v, Mapping)}


def _write(root: Path | None, entries: Mapping[str, Mapping[str, Any]]) -> None:
    path = watch_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"version": PROTECTION_WATCH_VERSION, "entries": dict(entries)},
                              ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def observe_unknown(
    position: Mapping[str, Any], legs: Mapping[str, Any], *, now: str, root: Path | None,
    ids_missing_code: str,
) -> dict[str, Any]:
    """Advance one position's clock on an UNKNOWN read and say what the route owes it. Never raises.

    Returns ``{level, notify, hard, entry, reason_codes}``:
    - ``notify`` is true the first time the position reaches U1 or U2. It is edge-triggered and
      recorded as sent before the route sends, so a failed send is reported, not repeated every
      pass.
    - ``hard`` is true at U2 until :func:`mark_hard_applied` records the halt.
    """
    key = position_key(position)
    kind = unknown_kind(legs, ids_missing_code=ids_missing_code)
    reasons = sorted({str(leg.get("error")) for leg in legs.get("legs") or ()
                      if isinstance(leg, Mapping) and leg.get("error")})
    try:
        with locked(_lock_path(root), code=WATCH_UNRECORDED, label="the protection watch store"):
            try:
                entries = read_watch(root)
            except ToolError:
                return _unread(key, kind)
            entry = dict(entries.get(key or "") or {})
            entry.setdefault("unknown_since", now)
            entry["symbol"] = position.get("symbol")
            entry["kind"] = KIND_IDS_MISSING if kind == KIND_IDS_MISSING else entry.get("kind", kind)
            entry["last_seen"] = now
            entry["last_reasons"] = reasons
            entry["passes"] = int(entry.get("passes") or 0) + 1
            level = level_for(entry, now=now)
            notify = level != LEVEL_OBSERVE and _LEVEL_RANK[level] > _LEVEL_RANK.get(
                entry.get("notified_level"), -1)
            if notify:
                entry["notified_level"] = level
            entry["level"] = level
            hard = level == LEVEL_HARD and not entry.get("hard_halt_applied_at")
            if key is None:
                # A position with no id cannot be tracked across passes. It is held at U1 so its
                # clock does not silently restart, and it names why.
                return {"level": max(level, LEVEL_NOTIFY, key=_LEVEL_RANK.get), "notify": False,
                        "hard": False, "entry": entry, "reason_codes": [WATCH_UNRECORDED, PERSISTING]}
            entries[key] = entry
            _write(root, entries)
    except (MvpRuntimeError, OSError) as exc:
        return {"level": LEVEL_NOTIFY, "notify": False, "hard": False, "entry": None,
                "reason_codes": [WATCH_UNRECORDED, getattr(exc, "reason_code", type(exc).__name__),
                                 PERSISTING]}
    codes = [PERSISTING] if level != LEVEL_OBSERVE else []
    if level == LEVEL_HARD:
        codes.append(HARD_HALT)
    return {"level": level, "notify": notify, "hard": hard, "entry": entry, "reason_codes": codes}


def _unread(key: str | None, kind: str) -> dict[str, Any]:
    return {"level": LEVEL_NOTIFY, "notify": False, "hard": False,
            "entry": {"key": key, "kind": kind}, "reason_codes": [WATCH_UNREADABLE, PERSISTING]}


def mark_hard_applied(position: Mapping[str, Any], *, now: str, root: Path | None) -> None:
    """Record that the HARD halt for this position's episode is in effect, so it is applied once.
    Raises on a failed write; the route reports it, and the next pass tries again."""
    key = position_key(position)
    if key is None:
        return
    with locked(_lock_path(root), code=WATCH_UNRECORDED, label="the protection watch store"):
        entries = read_watch(root)
        if key in entries:
            entries[key]["hard_halt_applied_at"] = now
            _write(root, entries)


def clear(position: Mapping[str, Any], *, root: Path | None) -> bool:
    """A definite read ends the episode. True when an entry was removed. Never raises; a store that
    cannot be read or written keeps its entry, which errs toward holding entries."""
    key = position_key(position)
    if key is None or not watch_path(root).is_file():
        return False  # nothing is watched: no lock taken, nothing written
    try:
        with locked(_lock_path(root), code=WATCH_UNRECORDED, label="the protection watch store"):
            entries = read_watch(root)
            if key not in entries:
                return False
            del entries[key]
            _write(root, entries)
            return True
    except (MvpRuntimeError, OSError):
        return False


def prune(open_keys: set[str], *, root: Path | None) -> list[str]:
    """Drop entries for positions no longer on the book, and return their keys. Never raises."""
    if not watch_path(root).is_file():
        return []  # nothing is watched: no lock taken, nothing written
    try:
        with locked(_lock_path(root), code=WATCH_UNRECORDED, label="the protection watch store"):
            entries = read_watch(root)
            gone = sorted(k for k in entries if k not in open_keys)
            if gone:
                for k in gone:
                    del entries[k]
                _write(root, entries)
            return gone
    except (MvpRuntimeError, OSError):
        return []


def entries_blocking(*, root: Path | None, now: str) -> dict[str, Any] | None:
    """Why new live entries are held, or None. Never raises.

    Held while any watched position is at U1 or above, and whenever the store cannot be read: a
    watch that cannot be seen is not evidence that nothing is watched. The level is recomputed at
    ``now`` rather than read back, so a context that runs before the watched symbol's own context
    in a pass holds on the same wall clock, not on the previous pass's reading."""
    try:
        entries = read_watch(root)
    except ToolError:
        return {"reason_code": WATCH_UNREADABLE, "positions": []}
    levels = {k: max(level_for(e, now=now), e.get("level") or LEVEL_OBSERVE, key=_LEVEL_RANK.get)
              for k, e in entries.items()}
    held = sorted(k for k, level in levels.items() if _LEVEL_RANK[level] >= _LEVEL_RANK[LEVEL_NOTIFY])
    if not held:
        return None
    return {"reason_code": PERSISTING,
            "positions": [{"position_id": k, "symbol": entries[k].get("symbol"), "level": levels[k],
                           "unknown_since": entries[k].get("unknown_since")} for k in held]}


__all__ = [
    "ACTOR", "HARD_HALT", "HARD_HALT_UNAPPLIED", "KIND_IDS_MISSING", "KIND_READ_FAILED",
    "LEVEL_HARD", "LEVEL_NOTIFY", "LEVEL_OBSERVE", "PERSISTING", "PROTECTION_UNKNOWN_HARD_HALT_MINUTES",
    "PROTECTION_UNKNOWN_NOTIFY_MINUTES", "PROTECTION_WATCH_VERSION", "WATCH_UNREADABLE",
    "WATCH_UNRECORDED", "clear", "entries_blocking", "level_for", "mark_hard_applied",
    "observe_unknown", "position_key", "prune", "read_watch", "unknown_kind", "watch_path",
]
