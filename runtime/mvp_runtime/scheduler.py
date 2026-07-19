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
        return cls(
            schedule_id=str(r["schedule_id"]), kind=str(r["kind"]), request=str(r.get("request", "")),
            interval_seconds=int(r["interval_seconds"]), enabled=bool(r.get("enabled", True)),
            created_by=str(r.get("created_by", "unknown")), created_at=str(r.get("created_at", "")),
            next_run_at=str(r["next_run_at"]), reason=str(r.get("reason", "")),
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
    """Local JSONL store of schedules. Mutations rewrite the file atomically (small N)."""

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

    def _save(self, schedules: list[Schedule]) -> None:
        jsonl.write_objects(self._path, [s.as_record() for s in schedules],
                            write_code="SCHEDULES_WRITE_FAILED", label="the schedule store")

    def add(self, schedule: Schedule) -> None:
        self._save([*self.list(), schedule])

    def remove(self, schedule_id: str) -> bool:
        schedules = self.list()
        kept = [s for s in schedules if s.schedule_id != schedule_id]
        if len(kept) == len(schedules):
            return False
        self._save(kept)
        return True

    def set_enabled(self, schedule_id: str, enabled: bool) -> bool:
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

    def replace_all(self, schedules: list[Schedule]) -> None:
        self._save(schedules)


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
    a burst. Executed schedules run sequentially (overlap-safe). Returns a summary
    ``{fired, skipped, results}``. Fail-closed on an unreadable schedule store."""
    schedules = store.list()
    execution_allowed = control_store.load().execution_allowed if control_store is not None else True

    fired = 0
    skipped = 0
    results: list[dict[str, Any]] = []
    updated: list[Schedule] = list(schedules)

    for index, schedule in enumerate(schedules):
        due = schedule.enabled and schedule.next_run_at <= now
        if not due:
            continue

        if not execution_allowed:
            # kill_blocks: scheduler_execution — skip, drop the occurrence, advance cadence.
            skipped += 1
            status = "skipped_not_active"
            if ledger is not None:
                ledger.append_scheduler_event(_scheduler_event("skipped", schedule, now=now, status=status))
            updated[index] = replace(schedule, next_run_at=timeutil.plus_seconds(now, schedule.interval_seconds))
            store.replace_all(updated)
            results.append({"schedule_id": schedule.schedule_id, "action": "skipped", "status": status})
            continue

        # Claim the occurrence durably BEFORE executing. Deferring all state to one
        # end-of-batch write meant a failure on a LATER schedule left this one's
        # next_run_at un-advanced — it re-fired (a duplicate full pipeline run, duplicate
        # model call) on the next tick. Claim-first is the at-most-once direction, matching
        # the kill-skip rule above: a crash drops the occurrence, never doubles it.
        updated[index] = replace(schedule, next_run_at=timeutil.plus_seconds(now, schedule.interval_seconds))
        store.replace_all(updated)

        status = _execute(schedule, now=now, ledger=ledger, working_memory=working_memory,
                          provider=provider, search_tool=search_tool, repo_root=repo_root, executor=executor)
        fired += 1
        if ledger is not None:
            ledger.append_scheduler_event(_scheduler_event("fired", schedule, now=now, status=status))
        updated[index] = replace(updated[index], last_run_at=now, last_status=status)
        store.replace_all(updated)
        results.append({"schedule_id": schedule.schedule_id, "action": "fired", "status": status})

    return {"fired": fired, "skipped": skipped, "results": results}
