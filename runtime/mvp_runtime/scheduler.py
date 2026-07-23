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

import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from runtime.read_only_kernel import integrity

from . import jsonl, memory, timeutil
from .events import stamped_event
from .control import ControlStore
from .errors import MvpRuntimeError, SchedulerBlocked, ToolBlocked
from .filelock import locked
from .paths import repo_root as _repo_root
from .pipeline import run_task
from .store import LedgerStore
from .working_memory import WorkingMemoryStore

SCHEDULES_REL = ".runtime_governance_state/schedules.jsonl"
SCHEDULER_EVENT_TYPE = "scheduler_event.v0"
RECORD_TYPE = "schedule.v0"

# The status prefix a raised fire records (L3a). Also the recovery signal: a fire that
# succeeds while the PREVIOUS status carried this prefix is a schedule coming back.
FAILED_PREFIX = "failed:"

# Run lifecycle on the scheduler event stream. A fire writes ``started`` BEFORE it runs
# and exactly one terminal event after, both carrying the same ``schedule_run_id``.
# ``abandoned`` is the terminal a fire killed mid-flight never got to write — supplied by
# the next startup, which is the only vantage point that can see it.
ACTION_STARTED = "started"
ACTION_ABANDONED = "abandoned"
TERMINAL_ACTIONS = frozenset({"fired", "failed", ACTION_ABANDONED})

# Schedule kinds. A task template is a request string (analysis_task), a maintenance action
# (memory_prune), or a governed crypto cycle (crypto_pipeline) — never a shell command.
KIND_TASK = "analysis_task"
KIND_PRUNE = "memory_prune"
KIND_CRYPTO = "crypto_pipeline"
KIND_FACTORY = "crypto_factory"
KINDS = frozenset({KIND_TASK, KIND_PRUNE, KIND_CRYPTO, KIND_FACTORY})

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


def overdue_schedules(schedules: list[Schedule], *, now: str) -> list[tuple[Schedule, int]]:
    """Enabled schedules whose due time is more than one full interval in the past.

    A running tick loop advances ``next_run_at`` at every claim, so a schedule can only
    fall a whole interval behind if the scheduler itself was NOT RUNNING — process dead,
    Docker daemon down, host asleep. That is the one failure the loop cannot report while
    it is happening: it reports it on the way back up. Returns ``(schedule,
    seconds_overdue)``, most overdue first. Timestamps are the store's validated canonical
    form (``SCHEDULE_RECORD_INVALID`` rejects anything else), so parsing cannot surprise us.
    """
    late: list[tuple[Schedule, int]] = []
    current = timeutil.parse_iso(now)
    for schedule in schedules:
        if not schedule.enabled:
            continue
        overdue = int((current - timeutil.parse_iso(schedule.next_run_at)).total_seconds())
        if overdue > schedule.interval_seconds:
            late.append((schedule, overdue))
    late.sort(key=lambda item: item[1], reverse=True)
    return late


def _notify_status_change(
    notifier: Callable[[str, str], None],
    schedule: Schedule,
    *,
    previous_status: str | None,
    status: str,
    failed: bool,
    now: str,
) -> None:
    """Tell the operator when a schedule STARTS failing, or recovers. Best-effort.

    Only transitions are worth a message: a steady green schedule says nothing, and the
    de-dup lives in the notifier so a schedule failing every interval does not spam the
    control channel. The ledger event is the record of truth — this is an extra delivery
    attempt on top of it, which is why a broken notifier is swallowed here rather than
    allowed to take down the scheduling it was only supposed to report on."""
    if failed:
        text = (
            f"[스케줄 실패] {schedule.kind}\n"
            f"schedule_id: {schedule.schedule_id}\n"
            f"status: {status}\n"
            f"시각: {now}\n"
            f"이 회차는 유실됐습니다(at-most-once). "
            f"다음 실행: {timeutil.plus_seconds(now, schedule.interval_seconds)}"
        )
    elif (previous_status or "").startswith(FAILED_PREFIX):
        text = (
            f"[스케줄 복구] {schedule.kind}\n"
            f"schedule_id: {schedule.schedule_id}\n"
            f"직전 실패: {previous_status}\n"
            f"현재 status: {status}\n"
            f"시각: {now}"
        )
    else:
        return
    try:
        notifier(schedule.schedule_id, text)
    except Exception:  # noqa: BLE001 — last-resort guard; the notifier reports its own failures
        pass


def schedule_run_id(schedule: Schedule, *, claimed_at: str) -> str:
    """The id linking one occurrence's ``started`` event to its terminal one.

    Derived from (schedule_id, claim time), so it needs no counter and cannot collide:
    a claim advances ``next_run_at`` past ``now``, so one schedule cannot be claimed
    twice at the same instant."""
    return integrity.short_id("srun", {"schedule_id": schedule.schedule_id, "claimed_at": claimed_at})


def _scheduler_event(
    action: str, schedule: Schedule, *, now: str, status: str,
    run_id: str | None = None, **extra: Any,
) -> dict[str, Any]:
    # actions: started | fired | failed | abandoned | skipped | created | gap_detected.
    # run_id/extra are omitted when absent so non-run events keep their original shape.
    fields = dict(extra)
    if run_id is not None:
        fields["schedule_run_id"] = run_id
    return stamped_event(
        SCHEDULER_EVENT_TYPE, action=action,
        schedule_id=schedule.schedule_id, kind=schedule.kind, status=status, created_at=now,
        **fields,
    )


def abandoned_event(started: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    """The terminal event a fire killed mid-flight never wrote, supplied on the way back up.

    Built from the orphaned ``started`` event rather than a live Schedule: the schedule
    may have been removed or disabled while the runtime was down, and the run still
    deserves an honest ending."""
    return stamped_event(
        SCHEDULER_EVENT_TYPE, action=ACTION_ABANDONED,
        schedule_id=started.get("schedule_id"), kind=started.get("kind"),
        status="abandoned_mid_run", created_at=now,
        schedule_run_id=started.get("schedule_run_id"), started_at=started.get("created_at"),
    )


def find_abandoned_runs(events: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """``started`` events that never got a terminal one — fires that died mid-flight.

    The one outcome no in-process guard can record: L3a catches a fire that RAISES, but a
    process killed between the claim and the outcome writes nothing at all, so the
    occurrence simply vanishes — ``next_run_at`` already advanced and no event explains
    why nothing happened. Pairing starts against terminals across the stream recovers
    exactly those. ``abandoned`` counts as terminal, so a run is reported once and a later
    scan stays quiet. Returns the orphaned ``started`` events in append order."""
    terminal: set[str] = set()
    started: dict[str, dict[str, Any]] = {}
    for event in events:
        run_id = event.get("schedule_run_id")
        if not (isinstance(run_id, str) and run_id):
            continue
        action = event.get("action")
        if action == ACTION_STARTED:
            started.setdefault(run_id, dict(event))
        elif action in TERMINAL_ACTIONS:
            terminal.add(run_id)
    return [event for run_id, event in started.items() if run_id not in terminal]


def _execute(
    schedule: Schedule, *, now: str, ledger: Any, working_memory: Any, programization: Any,
    provider: Any, search_tool: Any, repo_root: Path | None, executor: Callable[..., dict[str, Any]],
) -> str:
    """Execute one due schedule and return a short status string."""
    if schedule.kind == KIND_PRUNE:
        if working_memory is None:
            return "skipped_no_memory_store"
        summary = memory.prune_working_memory(working_memory, ledger, now=now, reason=f"scheduled:{schedule.schedule_id}")
        return f"pruned:{summary['removed_count']}"
    if schedule.kind == KIND_CRYPTO:
        # One governed crypto cycle (C7). The collector and paper store are selected
        # at fire time through their Safety-Flag chokepoints, so a deleted grant is a
        # live revocation here exactly as everywhere else. The optional request field
        # is "SYMBOL TIMEFRAME"; empty uses the cycle defaults.
        from .crypto.cycle import cycle_status_line, run_crypto_cycle
        from .crypto.market_data import select_liquidation_feed, select_market_data_collector
        from .crypto.paper import select_paper_store

        parts = schedule.request.split()
        kwargs: dict[str, Any] = {}
        if len(parts) >= 1 and parts[0]:
            kwargs["symbol"] = parts[0]
        if len(parts) >= 2:
            kwargs["timeframe"] = parts[1]
        record = run_crypto_cycle(
            collector=select_market_data_collector(now=now, root=repo_root),
            store=select_paper_store(now=now, root=repo_root),
            liquidation_feed=select_liquidation_feed(now=now, root=repo_root),
            now=now,
            root=repo_root,
            **kwargs,
        )
        if ledger is not None:
            ledger.append_records(record["cycle_id"], {"crypto_cycle": record})
        return cycle_status_line(record)
    if schedule.kind == KIND_FACTORY:
        # One factory run (C8): generate + backtest candidates over a deep candle
        # window, append them to the candidates store. ALLOW-tier record creation —
        # the factory can never touch the active pool (promotion is the operator
        # door). A degraded backend simply skips the run: candidates mined from no
        # data would be evidence-free noise.
        from .crypto import pool as crypto_pool
        from .crypto.cycle import attach_feeds
        from .crypto.factory import run_factory
        from .crypto.market_data import (
            collect_market_data,
            factory_candle_target,
            select_liquidation_feed,
            select_market_data_collector,
        )

        parts = schedule.request.split()
        symbol = parts[0] if parts and parts[0] else "BTCUSDT"
        timeframe = parts[1] if len(parts) >= 2 else "1d"
        collector = select_market_data_collector(now=now, root=repo_root)
        try:
            snapshot, _ = collect_market_data(
                symbol, timeframe, collector=collector, now=now,
                limit=factory_candle_target(timeframe),
            )
        except ToolBlocked as exc:
            if exc.reason_code == "TOOL_ERROR":
                return "skipped_market_data_degraded"
            raise
        # C9: the factory backtests on the same feed-enriched frame the router
        # evaluates — one feature source for backtest and live (the source rule).
        attach_feeds(snapshot, collector=collector,
                     liquidation_feed=select_liquidation_feed(now=now, root=repo_root), now=now)
        result = run_factory(
            snapshot,
            active_pool=crypto_pool.load_active_pool(repo_root),
            existing_candidates=crypto_pool.read_candidates(repo_root),
            now=now,
        )
        crypto_pool.append_candidates(result["candidates"], root=repo_root)
        if ledger is not None:
            ledger.append_records(result["generation_id"], {"crypto_factory": result})
        return f"generated={result['accepted_count']} gen={result['generation_id']}"
    # KIND_TASK: run the request through the full pipeline as a scheduler-initiated task.
    result = executor(
        schedule.request, provider=provider, search_tool=search_tool, working_memory=working_memory,
        programization=programization,
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
    programization: Any | None = None,
    provider: Any | None = None,
    search_tool: Any | None = None,
    repo_root: Path | None = None,
    executor: Callable[..., dict[str, Any]] = run_task,
    notifier: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Fire every enabled schedule whose ``next_run_at`` is at or before ``now``. Kill-switch bound.

    While the runtime is PAUSED/KILLED (per ``control_store``), each due schedule is skipped and
    recorded; its ``next_run_at`` still advances so a kill drops the occurrence instead of queueing
    a burst. The control state is re-read **before each fire**, not once per batch: a schedule can
    hold the tick for minutes (a full pipeline run), and a kill issued mid-batch must stop the
    schedules behind it, not just the next tick. When no ``control_store`` is injected, the
    per-machine one under ``repo_root`` is used — absent state means ACTIVE, but the check never
    silently defaults to allowed (the old ``else True`` was the fail-open direction). Executed
    schedules run sequentially (overlap-safe). Returns a summary ``{fired, skipped, failed,
    results}``. Fail-closed on an unreadable schedule store. A fire that RAISES is recorded —
    durable "failed" scheduler event + ``last_status`` — and the loop continues to the next
    schedule: one bad fire must neither kill the tick process (it schedules every other kind
    too) nor vanish untraced. Occurrences stay at-most-once by design (the claim precedes the
    execute); what changed is that a lost fire is now a *recorded* failure, never silence.
    With a ``notifier`` injected, a schedule that STARTS failing or recovers also notifies the
    operator — transitions only, the notifier de-dups and reports its own failures. The ledger
    event remains the record of truth, so a dropped alert loses no evidence.

    Each executed occurrence is bracketed on the ledger: a ``started`` event before the work
    and exactly one terminal event (``fired``/``failed``) after, sharing a ``schedule_run_id``
    and carrying the measured ``duration_ms``. A process killed mid-fire leaves the start
    unpaired — ``find_abandoned_runs`` recovers it on the next startup, which is the only
    place that can, since a dead process records nothing itself.

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
    failed = 0
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

        # One bad fire must not kill the tick loop (it schedules every OTHER kind
        # too) or vanish without a trace: the occurrence is already claimed
        # (at-most-once), so the honest record of a raised fire is a durable
        # "failed" event + last_status, not a dead process with nothing written.
        # KeyboardInterrupt/SystemExit still propagate (Exception excludes them).
        # Record that this occurrence STARTED, before any work happens. Written first on
        # purpose: if this append fails the loop dies having run nothing unrecorded, and
        # if the PROCESS dies mid-fire this orphaned event is the only evidence the
        # occurrence was ever attempted (find_abandoned_runs pairs it up next startup).
        run_id = schedule_run_id(claimed, claimed_at=now)
        if ledger is not None:
            ledger.append_scheduler_event(
                _scheduler_event(ACTION_STARTED, claimed, now=now, status="running", run_id=run_id))

        previous_status = claimed.last_status
        started_at = time.monotonic()
        try:
            status = _execute(claimed, now=now, ledger=ledger, working_memory=working_memory,
                              programization=programization,
                              provider=provider, search_tool=search_tool, repo_root=repo_root, executor=executor)
            action = "fired"
            fired += 1
        except MvpRuntimeError as exc:
            status = f"failed:{exc.reason_code}"
            action = "failed"
            failed += 1
        except Exception as exc:  # noqa: BLE001 — recorded, never swallowed
            status = f"failed:UNEXPECTED:{type(exc).__name__}"
            action = "failed"
            failed += 1
        duration_ms = int((time.monotonic() - started_at) * 1000)
        if ledger is not None:
            ledger.append_scheduler_event(_scheduler_event(
                action, claimed, now=now, status=status, run_id=run_id, duration_ms=duration_ms))
        store.record_result(claimed.schedule_id, last_run_at=now, last_status=status)
        results.append({"schedule_id": claimed.schedule_id, "action": action, "status": status,
                        "schedule_run_id": run_id, "duration_ms": duration_ms})
        if notifier is not None:
            _notify_status_change(notifier, claimed, previous_status=previous_status,
                                  status=status, failed=(action == "failed"), now=now)

    return {"fired": fired, "skipped": skipped, "failed": failed, "results": results}
