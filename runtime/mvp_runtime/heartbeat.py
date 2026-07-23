"""Service liveness heartbeats — proof a loop is still turning, not merely alive.

The deployed healthcheck used to run ``console_cli status``, which answers as long as the
control state is readable. That is a property of the *state file*, not of the process: a
tick loop wedged on a provider call, an operator poll that stopped returning, a scheduler
that has not fired in hours — all of them kept reporting healthy, which is exactly the
class of silent stall the runtime has already been bitten by.

Each long-running loop stamps a heartbeat once per pass. A heartbeat is deliberately
self-describing: it carries the loop's OWN cadence, so the freshness threshold is derived
from the writer rather than duplicated into compose and drifting from it. Reading is pure
and never mutates, so a probe can run while the runtime is PAUSED or KILLED — a halted
runtime is stopped, not unhealthy, and its loop still turns.

State is per-machine and gitignored, like every other runtime state file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import timeutil
from .paths import repo_root as _repo_root

HEARTBEATS_REL = ".runtime_governance_state/heartbeats"

SCHEDULER_SERVICE = "scheduler"
OPERATOR_SERVICE = "operator"

# A loop is late only when it has missed several passes: one slow pass (a long-poll that
# held open, a pipeline run that took minutes) is normal operation, not a stall.
STALE_INTERVAL_MULTIPLIER = 3
# ...but a fast cadence must not make the probe hair-trigger. A 30s tick would otherwise
# be called stale after 90s, which one ordinary pipeline run can exceed.
STALE_FLOOR_SECONDS = 300

FRESH = "FRESH"
STALE = "STALE"
MISSING = "MISSING"
UNREADABLE = "UNREADABLE"


def heartbeats_dir(root: Path | None = None) -> Path:
    return (root if root is not None else _repo_root()) / HEARTBEATS_REL


def heartbeat_path(service: str, root: Path | None = None) -> Path:
    return heartbeats_dir(root) / f"{service}.json"


def write_heartbeat(
    service: str, *, interval_seconds: float, now: str | None = None, root: Path | None = None
) -> dict[str, Any]:
    """Stamp one pass of ``service``'s loop. Best-effort by contract — see the callers.

    Written whole via tmp+replace so a probe never reads a half-written record."""
    record = {
        "service": service,
        "heartbeat_at": now or timeutil.utc_now_iso(),
        "interval_seconds": float(interval_seconds),
        "pid": os.getpid(),
    }
    path = heartbeat_path(service, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return record


def stale_after_seconds(interval_seconds: float) -> float:
    """How long a loop of this cadence may go quiet before it is considered stalled."""
    return max(float(interval_seconds) * STALE_INTERVAL_MULTIPLIER, STALE_FLOOR_SECONDS)


def check_heartbeat(service: str, *, now: str | None = None, root: Path | None = None) -> dict[str, Any]:
    """Report ``service``'s liveness. Pure read; never raises, never writes.

    A probe that blew up on a malformed record would take down the very check meant to
    tell you something is wrong, so every failure is a reported status instead."""
    stamp = now or timeutil.utc_now_iso()
    path = heartbeat_path(service, root)
    result: dict[str, Any] = {"service": service, "checked_at": stamp, "age_seconds": None}
    if not path.is_file():
        return {**result, "status": MISSING,
                "detail": f"no heartbeat at {HEARTBEATS_REL}/{service}.json"}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        heartbeat_at = record["heartbeat_at"]
        interval = float(record.get("interval_seconds") or 0.0)
        age = (timeutil.parse_iso(stamp) - timeutil.parse_iso(heartbeat_at)).total_seconds()
    except Exception as exc:  # noqa: BLE001 — a broken record is a finding, not a crash
        return {**result, "status": UNREADABLE, "detail": f"{type(exc).__name__}: {exc}"}

    limit = stale_after_seconds(interval)
    return {
        **result,
        "status": FRESH if age <= limit else STALE,
        "age_seconds": round(age, 3),
        "stale_after_seconds": limit,
        "heartbeat_at": heartbeat_at,
        "interval_seconds": interval,
        "pid": record.get("pid"),
        "detail": f"last pass {age:.0f}s ago (stale after {limit:.0f}s)",
    }


__all__ = [
    "FRESH", "MISSING", "OPERATOR_SERVICE", "SCHEDULER_SERVICE", "STALE", "UNREADABLE",
    "check_heartbeat", "heartbeat_path", "heartbeats_dir", "stale_after_seconds",
    "write_heartbeat",
]
