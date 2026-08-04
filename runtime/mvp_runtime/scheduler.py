"""R6 Scheduler — recurring tasks bound to the kill switch.

The scheduler runs a stored task **template** (a request string or a maintenance action — never
a shell command, per `scheduler_plan_review.v0.1`) on a fixed interval cadence. It is a thin
live MVP scheduler, not an activation of the deferred review-only scheduler schema.

Governance and safety:
- **Kill-switch bound** (`governance/GOVERNANCE_POLICY.yaml` `kill_switch.kill_blocks:
  scheduler_execution`): before each fire, the runtime control state is checked; while PAUSED or
  KILLED a due schedule is **skipped, never run** — and its next run advances so a kill drops the
  occurrence rather than queueing a burst for resume.
- **Overlap-safe**, and not because the runtime is single-process — it is not. The shipped
  deployment runs an `operator` service alongside this one on the same state volume, and the
  compose contract is "at most ONE scheduler per volume" precisely because that is a deployment
  guarantee rather than a code one. What actually prevents a schedule overlapping itself is
  `claim_due`: the find-and-claim happens inside one cross-process lock acquisition and
  advances `next_run_at` before the fire, so a second claimant finds nothing due. Saying
  "single-process" instead named a premise that had stopped being true — the same stale premise
  let `reconcile_stale_running` abandon this service's live runs from the operator's startup.
- Each scheduled task runs through the **full pipeline** (`run_task`) — same intake, planning,
  permission, budget, and audit as an operator request; the scheduler grants no new authority.
- Maintenance kinds do only what their module already allows unattended: `memory_prune`
  deletes expired working-memory candidates (R5 retention), `ledger_rotate` archives ledger
  rows and can delete nothing at all (LEDGER_RETENTION_V0.1).
- Every fire (or kill-skip) is recorded to the durable ledger.

State is local, per-machine, gitignored (like the ledger, control state, and working memory):
schedules live in `.runtime_governance_state/schedules.jsonl`.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from runtime.read_only_kernel import integrity

from . import jsonl, memory, retention, task_registry, timeutil
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
KIND_REPORT = "crypto_report"
KIND_PROPOSER = "crypto_propose"
KIND_DATA_REVIEW = "crypto_data_review"
# Ledger retention (LEDGER_RETENTION_V0.1). Safe to run unattended for one reason only:
# rotation ARCHIVES, it never deletes — a scheduled job that can only move bytes between
# files cannot conceal anything, and the audit chain and control-event ledger are refused
# by the retention module itself, not by this caller remembering to skip them.
KIND_ROTATE = "ledger_rotate"
# A `pm_scan` kind lived here — PM1's prediction-market observation scan, two cadences under
# one kind. Removed 2026-08-02: prediction-market trading is not a lane this project may
# operate under Korean domestic regulation, so the capability is deleted rather than
# disabled (`docs/BUILD_HISTORY.md` records why, and the PM1 window's outcome).
# A stored schedule naming a kind this set no longer contains still LOADS — `_from_record`
# reads `kind` as a string and does not check it against `KINDS` — and is then refused at
# dispatch, which is the fail-closed direction. The two live rows were removed from the
# store in the same change rather than left for a fire that can no longer run.
# The C4 breaker's transition watch. Distinct from KIND_REPORT rather than folded into it
# because the two answer different questions: the report renders the current LEVEL every day,
# this fires on the EDGE. "Is it blocked right now" is one line in a daily digest; "it released
# at 04:00 on Monday" is the fact an operator is actually waiting for, and a level reported
# daily buries the day it flipped among the days it did not. It speaks only on a change, so a
# quiet run is the normal run — see `crypto/breaker_watch.py`.
KIND_BREAKER_WATCH = "crypto_breaker_watch"
# Keeps candles the equity venue will stop serving. Its own kind rather than a leg of
# `crypto_pipeline`, because it reads a DIFFERENT venue on a different cadence, and a per-book
# failure has to cost that book alone — losing one symbol must not cost the other eighty-seven.
#
# **It is NOT exempt from the kill switch, and the comment here used to say it was.** `run_due`
# skips every due schedule while PAUSED/KILLED and *drops* the occurrence rather than queueing
# it; there is no per-kind exemption and this kind does not have one. The original rationale —
# "must keep running when the pipeline is paused, because the data it races is lost by the
# clock" — described a property nothing implements, which is worse than not claiming it: an
# operator reading it would issue a halt believing archiving continued.
#
# What is true is that the exposure is BOUNDED by the same ceiling the archive exists for. A
# refresh sizes its request from the newest bar it holds and may ask for up to
# `VENUE_CANDLE_CEILING`, so a halt shorter than that window costs nothing — the next fire
# refills it, which is `candle_archive`'s own "a gap shorter than the ceiling self-heals". Only
# a halt outlasting **52 days at 15m or 208 at 1h** loses bars permanently.
#
# Exempting it would be a change to `run_due` and a different safety claim — a kill switch with
# an exception is not a kill switch — so it is argued there or not at all. A test pins that this
# kind stops with everything else, so the exemption cannot arrive by comment.
KIND_CANDLE_ARCHIVE = "candle_archive"
KINDS = frozenset({KIND_TASK, KIND_PRUNE, KIND_CRYPTO, KIND_FACTORY, KIND_REPORT,
                   KIND_PROPOSER, KIND_DATA_REVIEW, KIND_ROTATE,
                   KIND_BREAKER_WATCH, KIND_CANDLE_ARCHIVE})

# Guard against runaway cadences; a scheduled analysis task is not a tight loop.
MIN_INTERVAL_SECONDS = 60

# Scheduled factory fires also cross the best-scoring durable lineages (factory
# fusion, Thomas 2026-07-25): up to this many parent pairs per fire. The fusion
# machinery shipped with `run_factory(fusion_pairs=...)` but every scheduled call
# left the default 0, so no fused child was ever minted (0/109 candidates carried
# a parent). Children are backtested on their own evidence, refused when they
# close no trades, and de-duplicated by rule hash — so the steady-state output is
# small and re-fusing the same top parents is a no-op, not a pile-up.
#
# Raised 2 -> 4 on 2026-07-31, on the store's own record. Across the 594 candidates
# carrying a derivation, crossover beat the seeded rotation on every measure that
# gates promotion:
#
#   seeded_template  n=440  median expectancy -0.133  p90 +0.136  ROBUST  24 (5.5%)
#   crossover        n=154  median expectancy -0.042  p90 +0.268  ROBUST  21 (13.6%)
#
# and the highest-expectancy lineages in the store are all fusions of a price family
# with a feed family (`htf_pullback_long+oi_squeeze_long` +2.71R,
# `bollinger_breakout+oi_squeeze_long` +1.15R). Read with the bias stated: parents come
# from `rank_fusion_parents`, i.e. the top-scoring lineages, so a child starts from
# better rules than a fresh seed does. That is a reason the mechanism works, not a
# reason the comparison is fake — children are scored on their own backtest and inherit
# no parent evidence.
#
# 4 rather than higher because a batch is `DEFAULT_BATCH_SIZE` seeded specs and this many
# fused ones, so 4 makes the fire half-crossover; past that the seeded rotation — the only
# path by which a NEWLY ADDED family ever enters the store — starts losing its share of
# each fire. Supply is not the binding constraint: `FUSION_PARENT_POOL` is 6 per bucket,
# so `combinations` offers 15 distinct pairs per bucket before a second bucket is touched.
FACTORY_FUSION_PAIRS = 4

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


def next_occurrence(due_at: str, interval_seconds: int, *, now: str) -> str:
    """The next occurrence after ``due_at``, on the schedule's own grid, strictly after ``now``.

    Advancing from ``due_at`` rather than from the claim time is what keeps a cadence a
    cadence. The claim happens at the first tick at or after the due time, which is always
    a little late — the loop polls, and a fire that runs long pushes the poll after it. When
    the next occurrence was computed as ``claim_time + interval`` that lateness became the
    new anchor and **compounded on every cycle**: the `pm_scan` kind, registered at 120s,
    settled at a steady measured 140s (p50 over 2,264 fires) because its ~20s scan pushed
    each claim past the tick grid and the grid then became the cadence. A 17% shortfall in
    the sample rate of a measurement window whose exit artifact divided by the readings.
    That kind was removed the same day for reasons of its own (see the note above `KINDS`);
    the measurement is kept because it is the evidence this function exists on, and it
    applies to every kind that outlives it — `crypto_pipeline` runs the same 900s/30s shape.

    Anchoring to ``due_at`` makes the lateness per-occurrence jitter (bounded by the tick
    interval) instead of accumulated drift, so the long-run rate is the registered one.

    Whole intervals are stepped over, never partial ones, so the grid stays the one the
    schedule was created on. And the result is strictly after ``now``, which preserves the
    two properties the rest of this module builds on:

    - **at-most-once, no catch-up burst.** A scheduler down for a day does not owe 720
      occurrences of a 2-minute schedule; it advances past ``now`` in one claim and fires
      once. The same "a kill drops the occurrence rather than queueing a burst" rule the
      kill path states.
    - **a claim always advances past ``now``**, so a second claimant finds nothing due
      (``claim_due``'s overlap guarantee) and ``schedule_run_id`` cannot collide.

    Integer arithmetic, not a loop: a schedule left behind by a long outage would otherwise
    step one interval at a time over an unbounded gap.
    """
    anchor = timeutil.parse_iso(due_at)
    behind = (timeutil.parse_iso(now) - anchor).total_seconds()
    # Strictly after `now`: `floor(behind/interval) + 1` steps, which is >= 1 even when the
    # claim is exactly on time (behind == 0) or somehow early (a negative behind floors to
    # -1 and yields 0 steps -- so clamp, rather than hand back a due time already passed).
    steps = max(1, int(behind // interval_seconds) + 1)
    return timeutil.format_iso(anchor + timedelta(seconds=steps * interval_seconds))


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
    def default(cls, root: Path | None = None) -> "ScheduleStore":
        return cls((root if root is not None else _repo_root()))

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
        ``replace_all`` the tick loop used to blind-write mid-batch.

        The advance is ``next_occurrence`` — the schedule's own grid, stepped past ``now``
        — and not ``now + interval``, which re-anchored the cadence to whenever the claim
        happened to land and let a late tick become a permanently slower schedule."""
        with self._lock():
            schedules = self.list()
            for index, s in enumerate(schedules):
                if s.schedule_id != schedule_id:
                    continue
                if not (s.enabled and s.next_run_at <= now):
                    return None
                schedules[index] = replace(
                    s, next_run_at=next_occurrence(s.next_run_at, s.interval_seconds, now=now))
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


# A chat message is not a host console. `scheduler_cli list` prints each schedule's full
# last_status, which for a crypto pipeline fire is a multi-line dump of every evaluated
# context — unreadable in Telegram and past its send limit. This renders the same
# schedules for the other audience: grouped by kind, one line each, status truncated.
# Two renderers for two audiences, not two authorities: both read the same store.
_SUMMARY_STATUS_CHARS = 60
_SUMMARY_MAX_PER_KIND = 3


def render_schedule_summary(schedules: list[Schedule], *, now: str) -> str:
    """A compact, chat-sized report of what is scheduled and when it next runs."""
    if not schedules:
        return "등록된 스케줄이 없습니다."
    enabled = [s for s in schedules if s.enabled]
    disabled = len(schedules) - len(enabled)
    by_kind: dict[str, list[Schedule]] = {}
    for schedule in sorted(enabled, key=lambda s: (s.kind, s.next_run_at)):
        by_kind.setdefault(schedule.kind, []).append(schedule)

    lines = [f"스케줄 {len(enabled)}개 실행 중" + (f" (비활성 {disabled}개)" if disabled else "")]
    for kind, group in by_kind.items():
        count = f" ×{len(group)}" if len(group) > 1 else ""
        interval = group[0].interval_seconds
        cadence = (f"{interval // 3600}시간" if interval >= 3600
                   else f"{interval // 60}분" if interval >= 60 else f"{interval}초")
        lines.append(f"\n• {kind}{count} — {cadence}마다")
        for schedule in group[:_SUMMARY_MAX_PER_KIND]:
            status = " ".join(str(schedule.last_status or "").split())
            if len(status) > _SUMMARY_STATUS_CHARS:
                status = status[:_SUMMARY_STATUS_CHARS] + "…"
            overdue = " ⚠ 지연" if schedule.next_run_at <= now else ""
            lines.append(f"  다음 {schedule.next_run_at}{overdue}")
            if status:
                lines.append(f"  마지막: {status}")
        if len(group) > _SUMMARY_MAX_PER_KIND:
            lines.append(f"  …외 {len(group) - _SUMMARY_MAX_PER_KIND}개")
    return "\n".join(lines)


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
            # `schedule` is the pre-advance claim, so its `next_run_at` is the occurrence
            # that just failed — the same anchor `claim_due` advanced from. Recomputing it
            # the store's way keeps the operator's "다음 실행" and the stored one identical;
            # `now + interval` was a second opinion, and after the grid-anchored advance it
            # would have been the wrong one.
            f"다음 실행: {next_occurrence(schedule.next_run_at, schedule.interval_seconds, now=now)}"
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
    registry: Any = None,
) -> str:
    """Execute one due schedule and return a short status string.

    ``registry`` (F1, opt-in) records an ``analysis_task`` fire as a coordination entry, so
    an unattended scheduled run is visible to ``/tasks`` and ``/history`` like an operator's
    own request. Only that kind is recorded — the maintenance and crypto kinds are not
    task-shaped and have their own scheduler events — and the recording is best-effort, so
    bookkeeping can never fail a fire."""
    if schedule.kind == KIND_PRUNE:
        if working_memory is None:
            return "skipped_no_memory_store"
        summary = memory.prune_working_memory(working_memory, ledger, now=now, reason=f"scheduled:{schedule.schedule_id}")
        return f"pruned:{summary['removed_count']}"
    if schedule.kind == KIND_ROTATE:
        if ledger is None:
            return "skipped_no_ledger"
        # `request` optionally carries the row limit ("500"); anything unparseable falls
        # back to the module default rather than guessing a smaller, lossier number.
        keep = retention.DEFAULT_KEEP_ROWS
        raw = schedule.request.split()
        if raw:
            try:
                keep = int(raw[0])
            except ValueError:
                pass
        summary = retention.rotate_all(ledger, keep_rows=keep, now=now)
        detail = f"rotated={summary['rotated_rows']} keep={keep}"
        if summary["failures"]:
            # Reported in the fire's status, not swallowed: a ledger that could not be
            # rotated is one that keeps growing, and the operator should see which.
            detail += " failed=" + ",".join(f["filename"] for f in summary["failures"])
        if summary.get("event_error"):
            detail += f" unrecorded={summary['event_error']}"
        return detail
    if schedule.kind == KIND_BREAKER_WATCH:
        # Read the C4 breaker the way the cycle reads it and speak only when the verdict
        # changed. Same delivery posture as KIND_REPORT below — channel selected at fire time,
        # transport failure reported never raised — with one addition: an undelivered
        # announcement does NOT persist its marker, so the next fire retries it instead of
        # going quiet about a transition nobody was told about.
        from . import operator as operator_mod
        from .crypto import breaker_watch

        try:
            result = breaker_watch.run_breaker_watch(repo_root, now=now, persist=False)
        except MvpRuntimeError as exc:
            # An unusable risk-limits record is exactly the state the cycle refuses entries in,
            # so it is reported rather than swallowed into a comfortable "unchanged".
            return f"breaker_watch_unavailable:{exc.reason_code}"
        if not result["changed"]:
            return breaker_watch.status_line(result)
        try:
            channel = operator_mod.select_operator_channel(now=now, root=repo_root)
            operator_mod.notify_operator(channel, result["text"], repo_root=repo_root)
        except MvpRuntimeError as exc:
            return f"breaker_changed_not_sent:{exc.reason_code}"
        except Exception as exc:  # noqa: BLE001 — transport must not stop scheduling
            return f"breaker_changed_not_sent:{type(exc).__name__}"
        breaker_watch.write_mark(result["state"], root=repo_root)
        return breaker_watch.status_line(result)
    if schedule.kind == KIND_REPORT:
        # C13: render the read-only dashboard and push it to the ONE registered
        # operator chat. Pure reads + one notify — no gate of its own beyond the
        # channel's (selected at fire time, so a revoked telegram grant silently
        # degrades this to "rendered, not sent" rather than breaking the tick).
        # Delivery failure is reported in the status, never raised: a report that
        # cannot be sent must not stop the schedules behind it.
        from . import operator as operator_mod
        from .crypto.dashboard import build_status, render_status_text

        status = build_status(repo_root, now=now)
        text = render_status_text(status)
        try:
            channel = operator_mod.select_operator_channel(now=now, root=repo_root)
            operator_mod.notify_operator(channel, text, repo_root=repo_root)
        except MvpRuntimeError as exc:
            return f"report_rendered_not_sent:{exc.reason_code}"
        except Exception as exc:  # noqa: BLE001 — transport must not stop scheduling
            return f"report_rendered_not_sent:{type(exc).__name__}"
        warned = len(status.get("warnings") or [])
        return f"report_sent cycles={status.get('cycles_seen')} warnings={warned}"
    if schedule.kind == KIND_CRYPTO:
        # Governed crypto cycles (C7). The collector and paper store are selected at
        # fire time through their Safety-Flag chokepoints, so a deleted grant is a
        # live revocation here exactly as everywhere else.
        #
        # The optional request field is "SYMBOL [TIMEFRAME]". With a symbol named, it
        # is an explicit operator override: one cycle for exactly that context. With
        # the request empty, the fire fans OUT over every context the pool trades (+
        # every open position) so no strategy is symbol-starved — the default that
        # actually covers the pool. Each sub-cycle rides the ledger on its own id.
        from .crypto.cycle import (
            cycle_status_line,
            pool_cycle_status_line,
            run_crypto_cycle,
            run_pool_cycle,
        )
        from .crypto.market_data import (
            PerRunFeedCache,
            select_liquidation_feed,
            select_market_data_collector,
        )
        from .crypto.paper import select_paper_store
        from .crypto.routing_marks import RoutingMarkStore

        # Wrapped for the length of THIS fire only. A fan-out asks the venue the same
        # symbol-scoped questions once per timeframe; the memo makes one cadence cost one
        # request without making any cycle read older data. Dropped when the fire ends.
        collector = PerRunFeedCache(select_market_data_collector(now=now, root=repo_root))
        store = select_paper_store(now=now, root=repo_root)
        liquidation_feed = PerRunFeedCache(select_liquidation_feed(now=now, root=repo_root))
        # Freshness marks — local per-machine bookkeeping (no gate of its own): a new
        # entry is evaluated at most once per closed candle per context, so one 15-min
        # fan-out schedule covers 15m/1h/4h/1d without re-entering coarse timeframes
        # every tick. Marks persist only when the paper store is live (dry run keeps
        # none), so this changes nothing for a dry-run cycle.
        routing_marks = RoutingMarkStore(repo_root)

        parts = schedule.request.split()
        if parts and parts[0]:
            kwargs: dict[str, Any] = {"symbol": parts[0]}
            if len(parts) >= 2:
                kwargs["timeframe"] = parts[1]
            record = run_crypto_cycle(
                collector=collector, store=store, liquidation_feed=liquidation_feed,
                now=now, root=repo_root, routing_marks=routing_marks, **kwargs,
            )
            if ledger is not None:
                ledger.append_records(record["cycle_id"], {"crypto_cycle": record})
            return cycle_status_line(record)

        summary = run_pool_cycle(
            collector=collector, store=store, liquidation_feed=liquidation_feed,
            now=now, root=repo_root, routing_marks=routing_marks,
        )
        if ledger is not None:
            for record in summary["cycles"]:
                ledger.append_records(record["cycle_id"], {"crypto_cycle": record})
        return pool_cycle_status_line(summary)
    if schedule.kind == KIND_FACTORY:
        # One factory run (C8): generate + backtest candidates over a deep candle
        # window, append them to the candidates store. ALLOW-tier record creation —
        # the factory can never touch the active pool (promotion is the operator
        # door). A degraded backend simply skips the run: candidates mined from no
        # data would be evidence-free noise.
        from .crypto import market_data
        from .crypto import pool as crypto_pool
        from .crypto import positioning_store
        from .crypto.cycle import (
            attach_cross_section,
            attach_feeds,
            attach_htf,
            attach_positioning,
            attach_reference,
        )
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
                     liquidation_feed=select_liquidation_feed(now=now, root=repo_root), now=now,
                     root=repo_root)
        # The same rule for the HTF leg: mining htf_* families over a frame with no
        # higher timeframe would score every one of them as a no-trade spec. The
        # window must cover the replay span, so the depth is the higher timeframe's
        # own factory target, not the live default.
        higher = market_data.HIGHER_TIMEFRAME.get(timeframe)
        attach_htf(snapshot, collector=collector, now=now,
                   limit=factory_candle_target(higher) if higher else None)
        # The same rule once more for the cross-asset leg: a rel_strength_* family mined over
        # a frame with no reference series would score as a no-trade spec, so the reference
        # window has to cover the replay span rather than the live default. Same timeframe as
        # the frame being mined, so the same depth.
        attach_reference(snapshot, collector=collector, now=now,
                         limit=factory_candle_target(timeframe))
        # And once more for the cross-sectional leg, at the same depth and for the same
        # reason. This is the most expensive of the four: the cohort is five peers at the
        # replay span, so it pages roughly five times what the frame itself did. Paid on the
        # factory's own schedule rather than the 15-minute one, and the alternative is
        # scoring xs_* families over a frame where every rank is None — which does not
        # produce a cheap verdict, it produces a wrong one (no trades, FRAGILE, retired).
        attach_cross_section(snapshot, collector=collector, now=now,
                             limit=factory_candle_target(timeframe))
        # Positioning: a LOCAL read of what this runtime has accumulated — no request, no grant.
        # Attached unconditionally because the columns are honest at any coverage (absent = None);
        # the eligibility measured below is what decides whether a family may be MINTED against
        # them, which is a different question and the one that can go wrong silently.
        attach_positioning(snapshot, root=repo_root)
        # The store's own answer to "can you cover the window the factory replays". Read here
        # rather than inside the factory because `run_factory` is pure. A store that cannot be
        # read at all reports not-eligible, which is the safe direction: no data, no family.
        positioning_eligible = bool(positioning_store.coverage_summary(
            repo_root, symbols=[symbol],
        )["eligible"])
        result = run_factory(
            snapshot,
            active_pool=crypto_pool.load_active_pool(repo_root),
            existing_candidates=crypto_pool.read_candidates(repo_root),
            now=now,
            fusion_pairs=FACTORY_FUSION_PAIRS,
            positioning_eligible=positioning_eligible,
        )
        crypto_pool.append_candidates(result["candidates"], root=repo_root)
        if ledger is not None:
            ledger.append_records(result["generation_id"], {"crypto_factory": result})
        return (f"generated={result['accepted_count']} fused={result.get('fused_count', 0)} "
                f"gen={result['generation_id']}")
    if schedule.kind == KIND_PROPOSER:
        # M4b: the LLM strategy-family proposer on a schedule — reversing the "manual CLI
        # only" decision, so it is gated on the unreviewed-backlog cap. Once too many
        # distinct accepted-but-uninstalled families are already waiting, a fire SKIPS
        # (audited via the returned status) instead of piling on more the reviewer cannot
        # keep up with; installing a family (Thomas's code change) clears it, and old
        # proposals age out of the window on their own. The per-run proposal cap
        # (MAX_PROPOSALS_PER_RUN) already bounds one fire. Two gated reads reuse existing
        # chokepoints (market data + the validator provider); a degraded backend skips the
        # fire rather than proposing over no candles. ALLOW-tier: the record installs nothing.
        from .crypto import factory as crypto_factory
        from .crypto import proposer as crypto_proposer
        from .crypto.market_data import collect_market_data, select_market_data_collector
        from .providers import select_validator_provider

        installed = [t.family for t in crypto_factory.TEMPLATES]
        # A malformed ledger must not stop the scheduler: an unreadable record stream
        # degrades to "backlog unknown = 0" (the fire proceeds) rather than failing closed —
        # the backlog cap is a courtesy throttle, not a safety gate.
        try:
            backlog = crypto_proposer.count_unreviewed_backlog(
                ledger.iter_records() if ledger is not None else [], installed, now=now,
            )
        except MvpRuntimeError:
            backlog = 0
        if backlog >= crypto_proposer.MAX_UNREVIEWED_BACKLOG:
            return f"skipped_backlog_full:{backlog}"

        parts = schedule.request.split()
        symbol = parts[0] if parts and parts[0] else "BTCUSDT"
        timeframe = parts[1] if len(parts) >= 2 else "1h"
        focus = parts[2] if len(parts) >= 3 else None
        collector = select_market_data_collector(now=now, root=repo_root)
        try:
            snapshot, _ = collect_market_data(symbol, timeframe, collector=collector, now=now)
        except ToolBlocked as exc:
            if exc.reason_code == "TOOL_ERROR":
                return "skipped_market_data_degraded"
            raise
        provider = (select_validator_provider(now=now, root=repo_root)
                    or crypto_proposer.MockProposerProvider())
        record = crypto_proposer.propose_strategy_families(
            snapshot, provider=provider, now=now, existing_families=installed, focus=focus,
        )
        if ledger is not None:
            ledger.append_records(record["proposal_id"], {crypto_proposer.PROPOSAL_LEDGER_KIND: record})
        return (f"proposed={record['accepted_count']}/{record['proposed_count']} "
                f"backlog={backlog} prop={record['proposal_id']}")
    if schedule.kind == KIND_DATA_REVIEW:
        # Loop ① of the three review loops: a periodic, budgeted review of the pipeline's
        # DATA inputs (the M4b proposer posture applied one layer down). Deterministic
        # inventory — sources, mintable vocabulary, live feed status, per-timeframe paper
        # performance — plus one budgeted model call suggesting additional data worth
        # collecting. ALLOW-tier: the record installs and collects nothing; adding a
        # source stays a Thomas decision + a gated code change. The sheet is pushed to
        # the operator best-effort (the crypto_report delivery posture — a failed send
        # never fails the fire), and the record rides the ledger either way.
        from . import operator as operator_mod
        from .crypto import data_review as crypto_data_review
        from .crypto import pool as crypto_pool
        from .crypto.cycle import pool_cycle_contexts
        from .crypto.paper import read_outcomes
        from .providers import select_validator_provider

        try:
            # A bounded window over a stream, not a filtered copy of the whole ledger.
            # This is the shape `crypto/dashboard.py` was rewritten into after materializing
            # this same file OOM-killed the board; it needs 40 rows and the ledger is ~23 MB.
            window: deque[dict[str, Any]] = deque(maxlen=40)
            for row in (ledger.iter_records() if ledger is not None else []):
                if row.get("kind") == "crypto_cycle":
                    window.append(row)
            cycle_rows = list(window)
        except MvpRuntimeError:
            cycle_rows = []  # a malformed ledger degrades the inventory, never the fire
        try:
            outcomes = read_outcomes(repo_root)
        except MvpRuntimeError:
            outcomes = []
        inventory = crypto_data_review.build_data_inventory(
            cycle_rows, outcomes, contexts=pool_cycle_contexts(repo_root),
        )
        provider = (select_validator_provider(now=now, root=repo_root)
                    or crypto_data_review.MockDataReviewProvider())
        record = crypto_data_review.review_data_gaps(inventory, provider=provider, now=now)
        if ledger is not None:
            ledger.append_records(
                record["review_id"], {crypto_data_review.DATA_REVIEW_LEDGER_KIND: record})
        delivery = ""
        try:
            channel = operator_mod.select_operator_channel(now=now, root=repo_root)
            operator_mod.notify_operator(
                channel, crypto_data_review.format_review_report(record), repo_root=repo_root)
        except MvpRuntimeError as exc:
            delivery = f" sheet_not_sent:{exc.reason_code}"
        except Exception as exc:  # noqa: BLE001 — transport must not stop scheduling
            delivery = f" sheet_not_sent:{type(exc).__name__}"
        return (f"data_review={record['accepted_count']}/{record['suggested_count']} "
                f"review={record['review_id']}{delivery}")
    if schedule.kind == KIND_CANDLE_ARCHIVE:
        # Read-only, and the archive feeds nothing — so this fire cannot change what the
        # runtime trades. What it can do is fail to keep a bar that will not be offered
        # again: at 15m the venue's window is 52 days deep and moving, so a pass that does
        # not run is a hole no later pass can fill.
        #
        # Off unless the archive's own env names the venue (`select_env_gated` — the second
        # env-only exception, argued where it is written). Not opted in, the selector returns
        # the inert collector — deliberately not the Mock, whose synthetic bars would be
        # indistinguishable from real ones a year later.
        #
        # **OFF and BROKEN are reported differently, and that distinction is the point.**
        # `_notify_status_change` alerts on a FAILED fire and on recovery from one; a summary
        # string is a COMPLETED fire and reaches nobody. Returning "blocked" for both states —
        # which this did until 2026-08-04 — meant an archive that stopped working announced it
        # only inside a completion nobody is told about, while the window it races kept
        # rolling. Off on purpose is quiet; on and not working RAISES, so the existing failure
        # alert carries it within one cadence.
        from .crypto import candle_archive
        from .crypto.market_data import HYPERLIQUID, select_candle_archive_collector

        collector = select_candle_archive_collector(now=now, root=repo_root)
        summary = candle_archive.run_candle_archive(
            collector, venue=HYPERLIQUID, now_ms=int(time.time() * 1000), root=repo_root,
        )
        if summary["blocked"]:
            if summary["reason_code"] == candle_archive.NOT_ENABLED_REASON:
                return "candle_archive=off"  # the normal disabled state, not an incident
            raise SchedulerBlocked("ARCHIVE_UNIVERSE_UNREADABLE", (
                f"candle archiving is on but could not read the universe: {summary['reason_code']}"
            ))
        if summary["books"] and summary["degraded"] == summary["books"]:
            # Enabled, the universe answered, and not one book did. That is an outage rather
            # than a slow day, and a silent one would cost exactly what this store exists to
            # prevent.
            raise SchedulerBlocked("ARCHIVE_ALL_BOOKS_DEGRADED", (
                f"candle archiving reached no book of {summary['books']}: "
                f"{', '.join(summary['degraded_sample'])}"
            ))
        return (f"candle_archive symbols={summary['symbols']} books={summary['books']} "
                f"kept={summary['written']} degraded={summary['degraded']}")
    if schedule.kind != KIND_TASK:
        # Every branch above tests one kind, and this used to be a bare fall-through: a
        # schedule of ANY unrecognised kind ran the analysis pipeline below — a real model
        # call — with its `request` text as the prompt. `build_schedule` guards `KINDS` at
        # registration, so nothing could reach here while the code that wrote the store and
        # the code that read it were the same version. **Removing a kind is exactly when
        # they stop being.** The two live `pm_scan` rows were deleted with this change, but
        # a machine restored from an older state directory still has them, and on this code
        # the 2-minute one would have billed an LLM analysis of the string "watch" — quietly,
        # and reported as a normal COMPLETED fire. Refused by name instead: an unknown kind
        # is the fail-closed case this repo's own rule already covers.
        raise SchedulerBlocked(
            "UNKNOWN_KIND",
            f"schedule {schedule.schedule_id} names kind {schedule.kind!r}, which this "
            f"runtime does not execute; registrable kinds are {sorted(KINDS)}",
        )
    # KIND_TASK: run the request through the full pipeline as a scheduler-initiated task.
    entry = task_registry.record_submission(
        registry, request_text=schedule.request, origin="SCHEDULER",
        requester_id=f"mvp.scheduler:{schedule.schedule_id}", now=now,
    )
    result = executor(
        schedule.request, provider=provider, search_tool=search_tool, working_memory=working_memory,
        programization=programization,
        now=now, store=ledger, repo_root=repo_root, channel="scheduler", requester_type="scheduler",
        requester_id="mvp.scheduler", authenticated=True, source_ref=f"scheduler:{schedule.schedule_id}",
    )
    status = str(result.get("status", "UNKNOWN"))
    identity = result.get("records", {}).get("received_task", {}).get("identity", {})
    trace_id = identity.get("trace_id")
    # A scheduled run has no operator waiting on a send, so COMPLETED *is* its terminal:
    # the deliverable exists in the ledger and /result can re-render it on demand.
    task_registry.close_entry(
        registry, entry,
        status=task_registry.DELIVERED if status == "COMPLETED" else task_registry.BLOCKED,
        now=now, task_id=identity.get("task_id"), trace_id=trace_id,
        result_ref=f"ledger:{trace_id}" if trace_id else None,
        reason_code=None if status == "COMPLETED" else (result.get("block") or {}).get("reason_code", "BLOCKED"),
    )
    return status


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
    registry: Any = None,
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
                              programization=programization, registry=registry,
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
