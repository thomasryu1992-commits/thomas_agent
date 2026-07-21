"""R6 Scheduler — recurring tasks bound to the kill switch.

The scheduler runs a stored task **template** (a request string or a maintenance action — never
a shell command, per `scheduler_plan_review.v0.1`) on a fixed interval cadence. It is a thin
live MVP scheduler, not an activation of the deferred review-only scheduler schema.

Governance and safety:
- **Kill-switch bound** (`governance/GOVERNANCE_POLICY.yaml` `kill_switch.kill_blocks:
  scheduler_execution`): before each fire, the runtime control state is checked; while PAUSED or
  KILLED a due schedule is **skipped, never run** — and its next run advances so a kill drops the
  occurrence rather than queueing a burst for resume.
- **Overlap-safe:** the MVP is single-process and `run_due` executes due schedules sequentially,
  each at most once per tick, so a schedule can never overlap itself. Real cross-process
  concurrency control is a later increment.
- Each scheduled task runs through the **full pipeline** (`run_task`) — same intake, planning,
  permission, budget, and audit as an operator request; the scheduler grants no new authority.
- Every fire (or kill-skip) is recorded to the durable ledger.

State is local, per-machine, gitignored (like the ledger, control state, and working memory):
schedules live in `.runtime_governance_state/schedules.jsonl`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from runtime.read_only_kernel import integrity

from . import jsonl, memory, timeutil
from .events import stamped_event
from .control import ControlStore
from .errors import SchedulerBlocked
from .filelock import locked
from .paths import repo_root as _repo_root
from .pipeline import run_task
from .store import LedgerStore
from .working_memory import WorkingMemoryStore

SCHEDULES_REL = ".runtime_governance_state/schedules.jsonl"
SCHEDULER_EVENT_TYPE = "scheduler_event.v0"
RECORD_TYPE = "schedule.v0"

# Schedule kinds. A task template is a request string (analysis_task) or a maintenance action
# (memory_prune) — never a shell command.
KIND_TASK = "analysis_task"
KIND_PRUNE = "memory_prune"
KINDS = frozenset({KIND_TASK, KIND_PRUNE})

# Guard against runaway cadences; a scheduled analysis task is not a tight loop.
MIN_INTERVAL_SECONDS = 60

# The one timestamp form `next_run_at <= now` is a correct time comparison for —
# single authority in timeutil (anchor rationale documented there).
_TIMESTAMP_PATTERN = timeutil.FIXED_UTC_PATTERN


@dataclass(frozen=True)
class Schedule:
    """One recurring schedule. Immutable; a tick produces an updated copy."""

    schedule_id: str
    kind: str
    request: str
    interval_seconds: int
    enabled: bool
    created_by: str
    created_at: str
    next_run_at: str
    reason: str = ""
    last_run_at: str | None = None
    last_status: str | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "record_type": RECORD_TYPE,
            "schedule_id": self.schedule_id,
            "kind": self.kind,
            "request": self.request,
            "interval_seconds": self.interval_seconds,
            "enabled": self.enabled,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "next_run_at": self.next_run_at,
            "reason": self.reason,
            "last_run_at": self.last_run_at,
            "last_status": self.last_status,
        }

    @classmethod
    def from_record(cls, r: Mapping[str, Any]) -> "Schedule":
        """Rebuild a Schedule from its stored row, or fail closed with a typed error.

        Two failure modes this guards, both from a hand-edited or partially-written
        schedules file. A missing/garbage field used to escape as a raw KeyError/ValueError,
        past scheduler_cli's ``except MvpRuntimeError``, so the CLI died with a traceback
        instead of a BLOCK. Worse, ``next_run_at: null`` became the string ``"None"``,
        which sorts ABOVE every real timestamp — so ``next_run_at <= now`` was never true
        and the schedule silently never fired, with no error anywhere. A dormant schedule
        that looks healthy is the worst of the two; both are refused here."""
        try:
            schedule_id = str(r["schedule_id"])
            kind = str(r["kind"])
            interval_seconds = int(r["interval_seconds"])
            next_run_at = r["next_run_at"]
        except (KeyError, TypeError, ValueError) as exc:
            raise SchedulerBlocked(
                "SCHEDULE_RECORD_INVALID",
                f"stored schedule is missing or has a malformed required field: {exc}",
            ) from exc
        if not (isinstance(next_run_at, str) and _TIMESTAMP_PATTERN.match(next_run_at)):
            raise SchedulerBlocked(
                "SCHEDULE_RECORD_INVALID",
                f"schedule {schedule_id} has next_run_at={next_run_at!r}; it must be the "
                "fixed UTC form YYYY-MM-DDThh:mm:ssZ, which is the only form the due "
                "comparison is correct for",
            )
        return cls(
            schedule_id=schedule_id, kind=kind, request=str(r.get("request", "")),
            interval_seconds=interval_seconds, enabled=bool(r.get("enabled", True)),
            created_by=str(r.get("created_by", "unknown")), created_at=str(r.get("created_at", "")),
            next_run_at=next_run_at, reason=str(r.get("reason", "")),
            last_run_at=r.get("last_run_at"), last_status=r.get("last_status"),
        )


def build_schedule(
    *, kind: str, request: str, interval_seconds: int, created_by: str, now: str,
    reason: str = "", enabled: bool = True,
) -> Schedule:
    """Validate inputs and build a new Schedule (deterministic id). Fail-closed."""
    if kind not in KINDS:
        raise SchedulerBlocked("UNKNOWN_KIND", f"schedule kind must be one of {sorted(KINDS)}")
    if not (isinstance(interval_seconds, int) and interval_seconds >= MIN_INTERVAL_SECONDS):
        raise SchedulerBlocked("INVALID_INTERVAL", f"interval_seconds must be an int >= {MIN_INTERVAL_SECONDS}")
    request = request.strip() if isinstance(request, str) else ""
    if kind == KIND_TASK and not request:
        raise SchedulerBlocked("MISSING_REQUEST", "an analysis_task schedule requires a non-empty request")
    if not (isinstance(created_by, str) and created_by.strip()):
        raise SchedulerBlocked("MISSING_CREATOR", "a schedule requires a created_by identity")
    schedule_id = integrity.short_id(
        "schedule", {"kind": kind, "request": request, "interval": interval_seconds,
                     "created_by": created_by, "created_at": now}
    )
    return Schedule(
        schedule_id=schedule_id, kind=kind, request=request, interval_seconds=interval_seconds,
        enabled=enabled, created_by=created_by.strip(), created_at=now,
        next_run_at=timeutil.plus_seconds(now, interval_seconds), reason=reason,
    )


class ScheduleStore:
    """Local JSONL store of schedules. Mutations rewrite the file atomically (small N).

    Every read-modify-write runs under a cross-process sidecar lock: the tick loop and a
    ``docker exec`` operator command (disable/remove) share this file, and an unlocked
    full-file rewrite silently reverted whichever of them wrote first — an operator's
    disable could vanish mid-batch and the schedule kept firing with no trace."""

    def __init__(self, root: Path):
        self._path = Path(root) / SCHEDULES_REL

    @classmethod
    def default(cls) -> "ScheduleStore":
        return cls(_repo_root())

    @property
    def path(self) -> Path:
        return self._path

    def list(self) -> list[Schedule]:
        rows = jsonl.read_objects(self._path, read_code="SCHEDULES_UNREADABLE", label="the schedule store")
        return [Schedule.from_record(r) for r in rows]

    def _lock(self):
        return locked(self._path.with_name(".schedules.lock"),
                      code="SCHEDULES_WRITE_FAILED", label="the schedule store")

    def _save(self, schedules: list[Schedule]) -> None:
        jsonl.write_objects(self._path, [s.as_record() for s in schedules],
                            write_code="SCHEDULES_WRITE_FAILED", label="the schedule store")

    def add(self, schedule: Schedule) -> None:
        with self._lock():
            self._save([*self.list(), schedule])

    def remove(self, schedule_id: str) -> bool:
        with self._lock():
            schedules = self.list()
            kept = [s for s in schedules if s.schedule_id != schedule_id]
            if len(kept) == len(schedules):
                return False
            self._save(kept)
            return True

    def set_enabled(self, schedule_id: str, enabled: bool) -> bool:
        with self._lock():
            schedules = self.list()
            found = False
            updated: list[Schedule] = []
            for s in schedules:
                if s.schedule_id == schedule_id:
                    found = True
                    updated.append(replace(s, enabled=enabled))
                else:
                    updated.append(s)
            if found:
                self._save(updated)
            return found

    def claim_due(self, schedule_id: str, *, now: str) -> Schedule | None:
        """Atomically re-check and claim one due occurrence.

        Under the store lock: re-read the schedule's CURRENT state; only if it still
        exists, is enabled, and is due does its ``next_run_at`` advance. Returns the
        claimed (pre-advance) schedule, else None — a concurrent operator disable/remove,
        or another process's claim, wins instead of being reverted by a stale batch
        rewrite. This is the per-schedule replacement for the old whole-list
        ``replace_all`` the tick loop used to blind-write mid-batch."""
        with self._lock():
            schedules = self.list()
            for index, s in enumerate(schedules):
                if s.schedule_id != schedule_id:
                    continue
                if not (s.enabled and s.next_run_at <= now):
                    return None
                schedules[index] = replace(s, next_run_at=timeutil.plus_seconds(now, s.interval_seconds))
                self._save(schedules)
                return s
            return None

    def record_result(self, schedule_id: str, *, last_run_at: str, last_status: str) -> None:
        """Record a fire's outcome on the schedule's CURRENT state (no-op if removed).

        Touches only ``last_run_at``/``last_status`` — never ``enabled`` or
        ``next_run_at`` — so it cannot revert a concurrent operator action."""
        with self._lock():
            schedules = self.list()
            for index, s in enumerate(schedules):
                if s.schedule_id == schedule_id:
                    schedules[index] = replace(s, last_run_at=last_run_at, last_status=last_status)
                    self._save(schedules)
                    return


def _scheduler_event(action: str, schedule: Schedule, *, now: str, status: str) -> dict[str, Any]:
    return stamped_event(
        SCHEDULER_EVENT_TYPE, action=action,    # "fired" | "skipped"
        schedule_id=schedule.schedule_id, kind=schedule.kind, status=status, created_at=now,
    )


def _execute(
    schedule: Schedule, *, now: str, ledger: Any, working_memory: Any, provider: Any,
    search_tool: Any, repo_root: Path | None, executor: Callable[..., dict[str, Any]],
) -> str:
    """Execute one due schedule and return a short status string."""
    if schedule.kind == KIND_PRUNE:
        if working_memory is None:
            return "skipped_no_memory_store"
        summary = memory.prune_working_memory(working_memory, ledger, now=now, reason=f"scheduled:{schedule.schedule_id}")
        return f"pruned:{summary['removed_count']}"
    # KIND_TASK: run the request through the full pipeline as a scheduler-initiated task.
    result = executor(
        schedule.request, provider=provider, search_tool=search_tool, working_memory=working_memory,
        now=now, store=ledger, repo_root=repo_root, channel="scheduler", requester_type="scheduler",
        requester_id="mvp.scheduler", authenticated=True, source_ref=f"scheduler:{schedule.schedule_id}",
    )
    return str(result.get("status", "UNKNOWN"))


def run_due(
    store: ScheduleStore,
    *,
    now: str,
    control_store: ControlStore | None = None,
    ledger: LedgerStore | None = None,
    working_memory: WorkingMemoryStore | None = None,
    provider: Any | None = None,
    search_tool: Any | None = None,
    repo_root: Path | None = None,
    executor: Callable[..., dict[str, Any]] = run_task,
) -> dict[str, Any]:
    """Fire every enabled schedule whose ``next_run_at`` is at or before ``now``. Kill-switch bound.

    While the runtime is PAUSED/KILLED (per ``control_store``), each due schedule is skipped and
    recorded; its ``next_run_at`` still advances so a kill drops the occurrence instead of queueing
    a burst. The control state is re-read **before each fire**, not once per batch: a schedule can
    hold the tick for minutes (a full pipeline run), and a kill issued mid-batch must stop the
    schedules behind it, not just the next tick. When no ``control_store`` is injected, the
    per-machine one under ``repo_root`` is used — absent state means ACTIVE, but the check never
    silently defaults to allowed (the old ``else True`` was the fail-open direction). Executed
    schedules run sequentially (overlap-safe). Returns a summary ``{fired, skipped, results}``.
    Fail-closed on an unreadable schedule store.

    The batch snapshot below is for iteration only. Every state change goes through the
    store's per-schedule, locked operations (``claim_due`` / ``record_result``) against the
    file's CURRENT content — the old pattern kept mutating the stale snapshot and
    ``replace_all``-ing it back, which silently reverted an operator's concurrent
    disable/remove and kept the schedule firing with no trace."""
    schedules = store.list()
    if control_store is None:
        control_store = ControlStore(repo_root if repo_root is not None else _repo_root())

    fired = 0
    skipped = 0
    results: list[dict[str, Any]] = []

    for schedule in schedules:
        if not (schedule.enabled and schedule.next_run_at <= now):
            continue

        if not control_store.load().execution_allowed:
            # kill_blocks: scheduler_execution — skip, drop the occurrence, advance cadence.
            if store.claim_due(schedule.schedule_id, now=now) is None:
                continue                    # removed/disabled meanwhile: nothing to skip
            skipped += 1
            status = "skipped_not_active"
            if ledger is not None:
                ledger.append_scheduler_event(_scheduler_event("skipped", schedule, now=now, status=status))
            results.append({"schedule_id": schedule.schedule_id, "action": "skipped", "status": status})
            continue

        # Claim the occurrence durably BEFORE executing (at-most-once: a crash drops the
        # occurrence, never doubles it). claim_due re-checks the current state under the
        # store lock, so an operator disable/remove that landed after the snapshot wins
        # here instead of being run anyway.
        claimed = store.claim_due(schedule.schedule_id, now=now)
        if claimed is None:
            continue

        status = _execute(claimed, now=now, ledger=ledger, working_memory=working_memory,
                          provider=provider, search_tool=search_tool, repo_root=repo_root, executor=executor)
        fired += 1
        if ledger is not None:
            ledger.append_scheduler_event(_scheduler_event("fired", claimed, now=now, status=status))
        store.record_result(claimed.schedule_id, last_run_at=now, last_status=status)
        results.append({"schedule_id": claimed.schedule_id, "action": "fired", "status": status})

    return {"fired": fired, "skipped": skipped, "results": results}
