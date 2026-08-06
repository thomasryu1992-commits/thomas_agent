"""The live route's incident watch — speak when the runtime stops being able to account for money.

``live_route`` has a status for the worst thing that can happen to it: ``ROUTE_INCIDENT``, whose
own definition is *"real money is in a state this runtime cannot account for"*. Reaching it sets
``halt: True`` and refuses entries across the fan-out, which is the right response — and until
this module, that was the whole of the response. **Nothing told anyone.** Grep for
``ROUTE_INCIDENT`` before this file existed and it appears in exactly one module, the one that
raises it.

Measured 2026-08-06 on the live host: an ETHUSDT settlement failure held INCIDENT for **44
consecutive cycles, ten and a quarter hours**, while the live outcome ledger stayed empty and the
local book claimed two positions the venue did not have. It was found by someone reading a
readiness row during an unrelated deploy. The fail-closed half worked perfectly the entire time;
the half that says so did not exist.

**This is `breaker_watch` pointed at a second fact**, deliberately down to the shape: an edge
trigger with a marker on disk, a first fire that always announces, no gate and no store write
beyond its own marker, and an undelivered announcement that does not persist its marker so the
next fire retries. Two watches over one mechanism is a duplication worth paying — the alternative
is one module reporting two unrelated facts, and an operator who cannot tell from the headline
which one moved.

**Why no consecutive-N threshold**, which was the first design and was wrong. The intuition was
that a one-cycle blip should not page. The history says there is no blip population to filter:
across 4,882 live cycles there has been exactly **one** INCIDENT run, and it was the 44-cycle
one. A threshold would have delayed the only signal that has ever mattered and suppressed
nothing, so the trigger is the plain edge — the set of contexts in incident changing.

**It watches contexts, not symbols.** A fan-out runs per ``(symbol, timeframe)`` and they fail
independently: ETHUSDT 4h was in incident while ETHUSDT 1d was HELD in the same batch. Reporting
at symbol granularity would have to pick one of those to be true.

**It decides nothing.** No gate, no routing effect, no write outside its marker. It reads the
records the cycle already wrote — never re-deriving a verdict — because a watch that assembled
its own inputs would eventually report a state the runtime is not in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..filelock import locked
from ..paths import repo_root as _repo_root
from .dashboard import _read_cycle_records
from .live_route import ROUTE_INCIDENT
from .paper import state_dir

WATCH_VERSION = "crypto_route_watch.v0.1"
MARK_FILENAME = "route_watch_mark.json"

# How many cycle records to read back. A fan-out is one record per routed context, and the live
# host runs ~6; 400 covers roughly the last 60 fan-outs, which is far more than the "what is true
# now" question needs and enough to measure how long a standing incident has run. Reading is
# bounded and streaming (`_read_cycle_records` does not hold the ledger in memory), so the cost of
# the margin is small and the cost of being short is a run-length that reads as capped.
RECORD_LOOKBACK = 400

# Statuses that mean the live leg ran and reported. `DISABLED` is not one: it means the gate was
# shut, so the context has nothing to say about money and must not dilute the ordering below.
_REPORTED = "reported"


def mark_path(root: Path | None = None) -> Path:
    """The last-announced incident set, so a quiet run can tell "unchanged" from "first run"."""
    return state_dir(root) / MARK_FILENAME


def read_mark(root: Path | None = None) -> dict[str, Any] | None:
    """The last announced state, or None when nothing has been announced yet.

    Fail-open toward None on a corrupt file, the same choice `breaker_watch` makes and for the
    same reason: the worst case is one redundant announcement, while refusing to watch because
    the marker is unreadable silences the thing whose whole job is to speak. Nothing here
    authorizes anything, so there is no permission to fail closed on.
    """
    path = mark_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_mark(state: Mapping[str, Any], *, root: Path | None = None) -> Path:
    path = mark_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code="ROUTE_WATCH_LOCKED", label="route watch mark"):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(state), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        tmp.replace(path)
    return path


def _context_key(record: Mapping[str, Any]) -> str:
    return f"{record.get('symbol')} {record.get('timeframe')}"


def evaluate(root: Path | None = None, *, now: str) -> dict[str, Any]:
    """Which routed contexts are in incident right now, and for how long.

    Reads the cycle ledger the cycle itself writes. For each context the **latest** record
    decides whether it is currently in incident; the run length is then counted backwards
    through that context's own records until a non-incident one, so a context that recovered and
    relapsed reports the current run rather than a total.

    Records whose ``live_route_status`` is absent or ``DISABLED`` are skipped entirely rather
    than treated as recoveries: the gate being shut is not evidence that a book disagreement was
    resolved, and letting it clear an incident would make a halted runtime look healthy.
    """
    records, warning = _read_cycle_records(
        root if root is not None else _repo_root(), RECORD_LOOKBACK
    )
    by_context: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        status = record.get("live_route_status")
        if not status or status == "DISABLED":
            continue
        by_context.setdefault(_context_key(record), []).append(record)

    contexts: dict[str, dict[str, Any]] = {}
    for key, rows in by_context.items():
        latest = rows[-1]
        if latest.get("live_route_status") != ROUTE_INCIDENT:
            continue
        run = 0
        for row in reversed(rows):
            if row.get("live_route_status") != ROUTE_INCIDENT:
                break
            run += 1
        contexts[key] = {
            "since": rows[-run].get("created_at"),
            "latest": latest.get("created_at"),
            "cycles": run,
            "capped": run == len(rows),
            "reason_codes": list(latest.get("live_reason_codes") or []),
            "reconcile": latest.get("live_reconcile_status"),
        }

    return {
        "watch_version": WATCH_VERSION,
        "created_at": now,
        "contexts": contexts,
        "in_incident": sorted(contexts),
        "records_read": len(records),
        "ledger_warning": warning,
    }


def has_changed(current: Mapping[str, Any], previous: Mapping[str, Any] | None) -> bool:
    """True when the set of contexts in incident differs, or on the first fire.

    The **set**, not the run lengths: a standing incident lengthens by one every cycle, and
    announcing that would be a timer, which is what this watch exists not to be. Entering and
    clearing both announce — a resolved incident is exactly as much news as a new one, and an
    operator who was told about the first and not the second is left believing the worse state.
    """
    if previous is None:
        return True
    return sorted(current.get("in_incident") or []) != sorted(previous.get("in_incident") or [])


def render_text(current: Mapping[str, Any], previous: Mapping[str, Any] | None) -> str:
    """The operator-facing message. ASCII only, like every other channel render."""
    now_set = set(current.get("in_incident") or [])
    was_set = set((previous or {}).get("in_incident") or [])
    entered = sorted(now_set - was_set)
    cleared = sorted(was_set - now_set)

    lines: list[str] = []
    if now_set:
        lines.append("[CRYPTO] LIVE ROUTE INCIDENT")
        lines.append("real money is in a state the runtime cannot account for.")
    else:
        lines.append("[CRYPTO] live route incident CLEARED")
    lines.append("")

    for key in entered:
        detail = (current.get("contexts") or {}).get(key) or {}
        codes = ", ".join(detail.get("reason_codes") or []) or "(none recorded)"
        run = detail.get("cycles")
        span = f"{run}+ cycles" if detail.get("capped") else f"{run} cycles"
        lines.append(f"  NEW  {key}")
        lines.append(f"       since {detail.get('since')} ({span})")
        lines.append(f"       reason: {codes}")
        if detail.get("reconcile"):
            lines.append(f"       reconcile: {detail.get('reconcile')}")
    for key in cleared:
        lines.append(f"  DONE {key} is no longer in incident")

    standing = sorted(now_set - set(entered))
    if standing:
        lines.append("")
        lines.append("  still standing: " + ", ".join(standing))

    lines.append("")
    if now_set:
        # What an operator can do about it, because "incident" alone is a status and not an
        # instruction. Entries are already refused for these contexts by the route itself.
        lines.append("Entries are already refused for the contexts above; closes stay allowed.")
        lines.append("Check the book against the venue:")
        lines.append("  python -m runtime.mvp_runtime.crypto.live_readiness")
    else:
        lines.append("Entries are no longer held by an incident.")
    return "\n".join(lines)


def run_route_watch(
    root: Path | None = None, *, now: str, persist: bool = True
) -> dict[str, Any]:
    """Evaluate, compare against the marker, and report whether anything changed.

    ``persist=False`` lets the caller withhold the marker until the announcement has actually
    been delivered — the scheduler does exactly that, so an undelivered transition is retried on
    the next fire instead of being recorded as told.
    """
    state = evaluate(root, now=now)
    previous = read_mark(root)
    changed = has_changed(state, previous)
    if changed and persist:
        write_mark(state, root=root)
    return {
        "watch_version": WATCH_VERSION,
        "state": state,
        "previous": previous,
        "changed": changed,
        "text": render_text(state, previous) if changed else "",
    }


def status_line(result: Mapping[str, Any]) -> str:
    """One line for the scheduler's own record on a quiet run."""
    state = result.get("state") or {}
    now_set = state.get("in_incident") or []
    if not now_set:
        return "route_watch:clear"
    return f"route_watch:unchanged:{len(now_set)}:{','.join(now_set)}"
