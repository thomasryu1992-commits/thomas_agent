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
- Each scheduled task runs through the **full pipeline** — same intake, planning, permission,
  budget, and audit as an operator request; the scheduler grants no new authority. Since the
  credential plane separation Phase 2 it runs that pipeline in the `pipeline-worker` service
  rather than in this process (`delegate_analysis_task`), so this service holds no model or
  search key; see that function for what the delegation does and does not change.
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
from typing import Any, Callable, Mapping, Sequence

from runtime.read_only_kernel import integrity

from . import jsonl, memory, retention, socket_door, task_registry, timeutil
from .events import stamped_event
from .control import ControlStore
from .errors import MvpRuntimeError, SchedulerBlocked, ToolBlocked
from .filelock import locked
from .paths import repo_root as _repo_root
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

# A due schedule the maintenance budget declined to START this pass (`MAINTENANCE_PASS_BUDGET_
# SECONDS`). Deliberately NOT in `TERMINAL_ACTIONS`: nothing was claimed, so there is no run for
# it to terminate — it is the record of an occurrence that has not happened yet, which is why it
# also carries no `schedule_run_id`. A test pins both.
ACTION_DEFERRED = "deferred"

# Mutations of the schedule SET, as opposed to the run lifecycle above. ``created`` was the
# only one recorded until 2026-08-04, so a schedule turned off left no trace anywhere: the
# store keeps no history (a disable rewrites `enabled` in place), the ledger held nothing,
# and `schedules.jsonl` is per-machine, so the repo had none either. Six disabled
# `crypto_factory` schedules could be explained only by reading the REPLACEMENT schedules'
# `reason` text and inferring from a gap in fire timestamps — an answer reconstructed from
# circumstance, in a runtime whose whole posture is that authority is recorded, not inferred.
#
# These carry no ``schedule_run_id``: they are not runs, and `find_abandoned_runs` pairs
# starts to terminals by that field alone, so a mutation event cannot be mistaken for either.
ACTION_CREATED = "created"
ACTION_ENABLED = "enabled"
ACTION_DISABLED = "disabled"
ACTION_REMOVED = "removed"
MUTATION_ACTIONS = frozenset({ACTION_CREATED, ACTION_ENABLED, ACTION_DISABLED, ACTION_REMOVED})
# The post-mutation state each action leaves behind. ``created`` is deliberately absent: it
# can leave EITHER state (`add --disabled`), so its status comes from the schedule itself.
_MUTATION_STATUS = {
    ACTION_ENABLED: "enabled",
    ACTION_DISABLED: "disabled",
    ACTION_REMOVED: "removed",
}

# Schedule kinds. A task template is a request string (analysis_task), a maintenance action
# (memory_prune), or a governed crypto cycle (crypto_pipeline) — never a shell command.
KIND_TASK = "analysis_task"
KIND_PRUNE = "memory_prune"
KIND_CRYPTO = "crypto_pipeline"
KIND_FACTORY = "crypto_factory"


def parse_factory_request(request: str) -> tuple[list[str], str]:
    """``"<symbol>[,<symbol>...] [<timeframe>]"`` → (symbols, timeframe).

    **A comma names a COHORT, and that is the whole of the pooled opt-in.**
    `factory.run_factory` mints one hypothesis across every leg it is handed at a timeframe in
    `factory.POOLED_TIMEFRAMES` (F9 shape B). What decides WHICH symbols is per-machine state
    rather than a constant in this file, and it has to be: `CROSS_SECTION_UNIVERSE` names six
    symbols and only five are mined, so a constant here would be a guess about the operator's
    schedules. The schedule already carries the symbol; a list is the same field saying more.

    **Nothing changes until `schedules.jsonl` does.** A request with no comma returns a
    one-symbol list and takes exactly today's path, so the migration F9 describes — five
    `(symbol, timeframe)` schedules becoming one per timeframe — is an edit to that file and is
    reversible by editing it back.

    Defaults match what the branch used before this existed, so an empty or malformed request
    fires the same BTCUSDT 1d run it always did rather than raising inside a scheduled tick.
    """
    parts = request.split()
    symbols = [s for s in (parts[0].split(",") if parts and parts[0] else []) if s]
    return (symbols or ["BTCUSDT"], parts[1] if len(parts) >= 2 else "1d")
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
# The live route's incident watch. Distinct from KIND_BREAKER_WATCH for the reason that one is
# distinct from KIND_REPORT: they answer different questions, and folding them together would
# make an operator read the headline twice to learn which fact moved. The breaker watch reports
# whether the live ENTRY GATE is open; this reports whether the runtime can still ACCOUNT for
# the money it already committed, and 2026-08-06 showed those are not the same state — the
# breaker read PASS for ten hours while ETHUSDT sat in an unaccountable book.
KIND_ROUTE_WATCH = "crypto_route_watch"
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
# Does a selected strategy's entry beat a coin flip through the same exits? Scheduled rather
# than run once because the only bars that are honestly out of sample for a spec are the ones
# after it was minted, and on the day this lands the newest cohort is one day old. Every fire
# re-reads the store and every spec's evaluable window is a day longer. See
# `crypto/null_control.py` for the measurement that made a one-shot version untrustworthy.
KIND_NULL_CONTROL = "crypto_null_control"
KINDS = frozenset({KIND_TASK, KIND_PRUNE, KIND_CRYPTO, KIND_FACTORY, KIND_REPORT,
                   KIND_PROPOSER, KIND_DATA_REVIEW, KIND_ROTATE,
                   KIND_BREAKER_WATCH, KIND_ROUTE_WATCH, KIND_CANDLE_ARCHIVE,
                   KIND_NULL_CONTROL})

# The kinds whose lateness costs money rather than freshness.
#
# **Why the distinction has to live in the loop rather than in schedule order.** `run_due` is one
# sequential pass in one process: a fire holds it until it returns, and the next tick cannot begin
# until the pass ends. So the kinds sharing a *due time* are not the only ones that can be
# delayed — a long pass delays whatever falls due *during* it, which lands in a later tick and is
# therefore invisible to any same-tick reading. That is why this went unseen: measured on this
# machine's ledger (2026-08-05), no `crypto_pipeline` fire has ever shared a pass with another
# kind, and it was still late.
#
# The measurement, recorded so a future reader can re-run it rather than trust it: fifteen
# `crypto_factory` schedules share a due time every morning at ~08:09Z and occupied a single pass
# for 600-664s — 74% of a pipeline period. On the three mornings where the pipeline's own
# occurrence fell inside that window it fired **116s, 240s and 349s late**: gaps of
# 1016/1140/1249s against a 900s cadence.
#
# `crypto_pipeline` carries position settlement, the protective-order re-assert, the holding-time
# exit and reconciliation. `crypto_breaker_watch` carries the C4 transition edge. Neither is
# exempt from the kill switch and neither becomes exempt here — this orders and budgets fires
# that are already permitted; it permits nothing.
#
# **What lateness does NOT cost, stated so this is not read as bigger than it is.** An open
# position is protected by exchange-resting orders whether this process runs or not: the stop is a
# `closePosition STOP_MARKET` and the target a `reduceOnly LIMIT` (`crypto/live_leg.py`). A late
# pass costs the holding-time exit, reconciliation, and the entry decision — real and bounded, and
# not the same thing as an unprotected position.
# `KIND_ROUTE_WATCH` belongs here for the same reason the breaker watch does, and arguably
# more: it is the only thing that reports a book the runtime cannot reconcile, and a report
# delayed behind an archive pass is a report about a state that has already lasted longer.
RISK_KINDS: frozenset[str] = frozenset({KIND_CRYPTO, KIND_BREAKER_WATCH, KIND_ROUTE_WATCH})

# The other half, named rather than inferred — and naming it is the point.
#
# **The gap this closes.** The budget used to ask `kind not in RISK_KINDS`, so a kind was
# deferrable by *default*: adding one to `KINDS` and forgetting this line made it deferrable
# silently, and if that kind touched the money path its lateness was the cost the budget exists
# to prevent, now caused by it. Nothing failed, nothing warned — a whitelist with a permissive
# complement asks a question only of the person who remembers there is one. Found 2026-08-09
# after `crypto_null_control` (#557) arrived and landed on the non-risk side by default. It
# belongs there — it is a backtest measurement, "no candidates, no orders", and 11 of its
# occurrences were deferred through the 04:36Z pass while `crypto_pipeline` held its cadence at
# 900/906/900/903/900 — but nobody decided that, which is the defect even when the default is
# right.
#
# **Why membership here rather than absence there.** The runtime test is now
# `kind in MAINTENANCE_KINDS`, which flips what an unclassified kind gets. It used to be
# "deferrable", the direction that costs money if the kind was a money kind. It is now "never
# deferred" — the budget under-applies, which costs a risk kind some latency and can never
# delay one *because of* the omission. Under-applying a latency bound is recoverable; deferring
# a settlement pass is not.
#
# A test asserts these two sets partition `KINDS` exactly, so an unclassified kind is a red
# suite rather than a silent default in either direction. That is where the judgement is
# forced; this constant is only where the answer is written down.
MAINTENANCE_KINDS: frozenset[str] = frozenset({
    KIND_TASK, KIND_PRUNE, KIND_FACTORY, KIND_REPORT, KIND_PROPOSER,
    KIND_DATA_REVIEW, KIND_ROTATE, KIND_CANDLE_ARCHIVE, KIND_NULL_CONTROL,
})

# How much of one pass the non-risk kinds may spend before it stops STARTING more of them.
#
# Not a timeout, and that difference is the whole design: a fire already running is never
# interrupted. Cutting a factory or an archive mid-write would trade a latency problem for a torn
# record, and this loop's at-most-once contract rests on a claimed occurrence either completing or
# being recorded as failed — not on being halved. So the guarantee is bounded rather than
# absolute: **at most one maintenance fire can stand between a due risk kind and its execution**,
# where before it was every maintenance fire that shared the pass.
#
# 60s is two tick intervals. Factory median is 24s, so the morning burst of fifteen now spreads
# over ~6 passes with the loop free between them instead of one 11-minute block. Cheap kinds
# (`ledger_rotate` at 0.2s, `crypto_report` at 2.2s) still batch normally — the budget only bites
# where the cost is, which is why it is a time budget and not a per-pass count.
#
# Whether 60 is still right is a ledger question rather than a guess: every deferral writes an
# `ACTION_DEFERRED` event carrying the pass spend and the budget then in effect, so
# `read_scheduler_events()` filtered to that action is the evidence — how often it binds, and
# whether the spend clusters at the boundary or far past it. See the deferral branch in `run_due`.
MAINTENANCE_PASS_BUDGET_SECONDS = 60.0

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

    def remove(self, schedule_id: str) -> Schedule | None:
        """Remove one schedule. Returns it as it was, or None if there was nothing to remove.

        Returns the record rather than a bool so the caller can audit what it destroyed —
        after the rewrite the only copy is gone, and an event naming just the id could not
        say what kind it was or whether it had ever run."""
        with self._lock():
            schedules = self.list()
            removed = next((s for s in schedules if s.schedule_id == schedule_id), None)
            if removed is None:
                return None
            self._save([s for s in schedules if s.schedule_id != schedule_id])
            return removed

    def set_enabled(self, schedule_id: str, enabled: bool) -> Schedule | None:
        """Flip one schedule's ``enabled``. Returns its state BEFORE the change, else None.

        The pre-state is read under the same lock that writes the new one, which is what
        makes it auditable: read outside the lock, the "was it already off?" an event
        records is a guess that the tick loop or a concurrent ``docker exec`` can have
        invalidated between the read and the write. A no-op (already in the target state)
        still returns the record — the caller decides whether that is worth recording,
        because refusing here would make an operator's repeated ``disable`` look like a
        missing schedule."""
        with self._lock():
            schedules = self.list()
            previous: Schedule | None = None
            updated: list[Schedule] = []
            for s in schedules:
                if s.schedule_id == schedule_id:
                    previous = s
                    updated.append(replace(s, enabled=enabled))
                else:
                    updated.append(s)
            if previous is not None:
                self._save(updated)
            return previous

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
    # actions: started | fired | failed | abandoned | skipped | deferred | gap_detected (run
    # lifecycle), created | enabled | disabled | removed (`MUTATION_ACTIONS`, the schedule set
    # changing). `deferred` is the one that records a NON-event — a due occurrence the budget
    # did not start and did not consume — so it carries no run id (see `ACTION_DEFERRED`).
    # run_id/extra are omitted when absent so non-run events keep their original shape.
    fields = dict(extra)
    if run_id is not None:
        fields["schedule_run_id"] = run_id
    return stamped_event(
        SCHEDULER_EVENT_TYPE, action=action,
        schedule_id=schedule.schedule_id, kind=schedule.kind, status=status, created_at=now,
        **fields,
    )


def mutation_event(
    action: str, schedule: Schedule, *, now: str, previously_enabled: bool | None = None,
) -> dict[str, Any]:
    """One ``MUTATION_ACTIONS`` event: the schedule SET changed, and what it was before.

    ``status`` is the state the schedule is in AFTER the mutation, which is the same
    convention the run events use. ``previously_enabled`` is carried alongside rather than
    folded into it, because only the pair distinguishes a real transition from an operator
    re-issuing a command that was already in effect — and a record that cannot tell those
    apart invites the reader to infer, which is the failure this event exists to end.

    ``request`` and ``reason`` ride along on every mutation because they are what actually
    identifies a schedule to a human: ``crypto_factory`` alone does not separate
    ``BTCUSDT 15m`` from the generic factory it replaced. For ``removed`` the last run is
    carried too — after the rewrite this event is the only surviving copy of that record.
    """
    if action not in MUTATION_ACTIONS:
        raise SchedulerBlocked(
            "SCHEDULER_EVENT_INVALID",
            f"{action!r} is not a schedule-set mutation; expected one of {sorted(MUTATION_ACTIONS)}",
        )
    status = _MUTATION_STATUS.get(action) or ("enabled" if schedule.enabled else "disabled")
    extra: dict[str, Any] = {"request": schedule.request, "reason": schedule.reason}
    if previously_enabled is not None:
        extra["previously_enabled"] = previously_enabled
    if action == ACTION_REMOVED:
        extra["last_run_at"] = schedule.last_run_at
        extra["last_status"] = schedule.last_status
    return _scheduler_event(action, schedule, now=now, status=status, **extra)


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


# How long a delegated fire waits for the worker's answer. A real analysis on the shared
# free-tier chain is minute-plus, and a scheduled fire has no client timeout behind it — the
# tick is the only thing waiting. Generous rather than tight for that reason: a deadline that
# expires mid-run abandons a task that still completes and lands in the ledger, which is the
# confusing half of a timeout rather than the useful half.
WORKER_DEADLINE_SECONDS = 900.0

# The kind a scheduled `analysis_task` runs as. `run_task(request_kind=None)` and
# `request_kind="analysis"` resolve to the SAME capabilities and therefore the same Role
# (`planner.classify_task` returns the analysis set for both), so naming it explicitly here
# changes no routing — it is what lets the request cross a socket whose kind set is closed.
# A schedule wanting a different Role is a new schedule kind, not a field on this one.
DELEGATED_REQUEST_KIND = "analysis"


def delegate_analysis_task(
    request_text: str,
    *,
    source_ref: str,
    now: str | None = None,
    repo_root: Path | None = None,
    **_unused: Any,
) -> dict[str, Any]:
    """Run one scheduled analysis in the pipeline worker and answer in ``run_task``'s shape.

    The default ``executor`` for `run_due`. It keeps that seam's signature — including the
    ``provider``/``search_tool``/store arguments it now ignores — so every test that injects a
    fake executor keeps working, and so the KIND_TASK branch below reads the same as before.
    The ignored arguments are the point of the change rather than an oversight: this service no
    longer selects a provider or a search tool, because it no longer holds the keys for either
    (`docs/proposals/CREDENTIAL_PLANE_SEPARATION_PHASE2_V0.1.md`). The worker selects both,
    through the same Safety-Flag Gate, from the same per-machine grants.

    **What does not change.** The run is still the full pipeline at the same P3 ceiling, still
    attributed ``mvp.scheduler`` with ``source_ref = scheduler:<schedule_id>`` (the worker
    stamps it from this call's own ``source_ref``, verbatim), and still synchronous — the fire
    holds the tick for its duration exactly as an in-process run did, so the pass budget,
    deferral, and at-most-once semantics are untouched.

    **No fallback, deliberately.** A worker that cannot be reached raises, which `run_due`
    records as a failed fire. Falling back to an in-process run would need this service to hold
    a provider key again — and with no key it would silently run the deterministic mock and
    report COMPLETED, which is the "reads as configured, does nothing" failure this deployment
    has closed twice (#512, #650).
    """
    from . import pipeline_worker

    reply = socket_door.call_door(
        pipeline_worker.socket_path(repo_root),
        {
            "request": request_text,
            "kind": DELEGATED_REQUEST_KIND,
            "reason": source_ref,
            "actor_profile": pipeline_worker.SCHEDULER_PROFILE,
        },
        deadline_seconds=WORKER_DEADLINE_SECONDS,
    )
    if not isinstance(reply, dict):
        raise SchedulerBlocked(
            "WORKER_UNAVAILABLE", "the pipeline worker's reply was not an object",
        )
    if "kind" not in reply and not reply.get("ok"):
        # A refusal envelope, not a run — the worker's own kill-switch or shape re-check, or
        # the transport's BRIDGE_BUSY. Nothing ran, so this is a failed fire rather than a
        # BLOCKED result: the distinction is the same one the dispatch door draws, and it is
        # what keeps "the runtime refused this work" separate from "the work never started".
        raise SchedulerBlocked(
            str(reply.get("reason_code") or "WORKER_UNAVAILABLE"),
            str(reply.get("reason") or "the pipeline worker refused this fire"),
        )
    # Answer in the shape the caller reads: status plus the identity it closes its
    # task-registry entry with. Deliberately not the whole `run_task` result — the report is
    # in the ledger, and copying it back through a socket to be discarded is the payload a
    # bridge reply should never carry.
    identity = {"task_id": reply.get("task_id"), "trace_id": reply.get("trace_id")}
    if reply.get("ok"):
        return {"status": "COMPLETED", "records": {"received_task": {"identity": identity}}}
    return {
        "status": "BLOCKED",
        "records": {"received_task": {"identity": identity}},
        "block": {
            "reason_code": reply.get("reason_code", "BLOCKED"),
            "message": reply.get("reason", ""),
        },
    }


def delegate_data_review(
    inventory: dict[str, Any], *, now: str | None, repo_root: Path | None,
) -> dict[str, Any]:
    """Run the data-gap review's model call in the pipeline worker and return its record.

    The narrowest possible cut, and the reason it is narrow: everything this fire does EXCEPT
    the model call needs no credentials and belongs where it already is. The inventory is
    built from the ledger and the pool here; the record is appended, rendered and judged here.
    Only the one step that needs a provider key crosses, which is what takes an LLM response
    out of the process holding the Binance account and order keys.

    **This removes no credential**, and saying so is the honest accounting: `crypto_propose`
    still calls a model in this process, so `MVP_VALIDATOR_PROVIDER` and its key stay on the
    scheduler either way. What it buys is one fewer model response parsed beside them
    (`docs/proposals/CREDENTIAL_PLANE_SEPARATION_PHASE2_V0.1.md` §7, D5).

    Fail-closed with no fallback, exactly as `delegate_analysis_task`: an unreachable worker
    raises and `run_due` records a failed fire. The in-process fallback that used to stand
    here — a `MockDataReviewProvider` when no provider was authorized — now lives in the
    worker, so a degraded review is still a real record and an absent worker is still a
    visible failure. Those two must not collapse into each other.
    """
    from . import pipeline_worker

    reply = socket_door.call_door(
        pipeline_worker.socket_path(repo_root),
        {"job": pipeline_worker.JOB_DATA_REVIEW, "inventory": inventory,
         "reason": "scheduler:crypto_data_review"},
        deadline_seconds=WORKER_DEADLINE_SECONDS,
    )
    if not isinstance(reply, dict) or not reply.get("ok"):
        raise SchedulerBlocked(
            str((reply or {}).get("reason_code") or "WORKER_UNAVAILABLE")
            if isinstance(reply, dict) else "WORKER_UNAVAILABLE",
            str((reply or {}).get("reason") or "the pipeline worker did not run the data review")
            if isinstance(reply, dict) else "the pipeline worker's reply was not an object",
        )
    record = reply.get("record")
    if not isinstance(record, dict) or not record.get("review_id"):
        # A reply that is "ok" but carries no usable record would otherwise be appended to the
        # ledger as evidence of a review that did not happen.
        raise SchedulerBlocked(
            "WORKER_UNAVAILABLE",
            "the pipeline worker returned no data-review record; nothing was appended",
        )
    return record


def delegate_proposal_generation(
    *, existing_families: Sequence[str], focus: str | None, repo_root: Path | None,
) -> dict[str, Any]:
    """Run the family proposer's model call in the pipeline worker and return its output.

    The last model call this service made. With it delegated, nothing here selects a provider
    any more — which is what lets the scheduler drop `MVP_VALIDATOR_PROVIDER` and its key and
    hold no model credential at all beside the Binance ones.

    Only the model half crosses. The proposals are judged here by `assemble_proposal_record`,
    against the market snapshot this process collected, so the "one feature source" rule is
    untouched: the frame never leaves, and no second collector exists to disagree with it.

    Fail-closed with no fallback, like the other two delegations: the `MockProposerProvider`
    that used to stand in when no provider was authorized now lives in the worker, so a
    degraded proposal is still a real record while an unreachable worker is a failed fire.
    """
    from . import pipeline_worker

    reply = socket_door.call_door(
        pipeline_worker.socket_path(repo_root),
        {"job": pipeline_worker.JOB_CRYPTO_PROPOSE,
         "proposal_inputs": {"existing_families": list(existing_families), "focus": focus},
         "reason": "scheduler:crypto_propose"},
        deadline_seconds=WORKER_DEADLINE_SECONDS,
    )
    if not isinstance(reply, dict) or not reply.get("ok"):
        raise SchedulerBlocked(
            str(reply.get("reason_code") or "WORKER_UNAVAILABLE")
            if isinstance(reply, dict) else "WORKER_UNAVAILABLE",
            str(reply.get("reason") or "the pipeline worker did not run the proposer")
            if isinstance(reply, dict) else "the pipeline worker's reply was not an object",
        )
    generation = reply.get("generation")
    if not isinstance(generation, dict) or "raw" not in generation:
        raise SchedulerBlocked(
            "WORKER_UNAVAILABLE",
            "the pipeline worker returned no proposer output; nothing was judged",
        )
    return generation


def _execute(
    schedule: Schedule, *, now: str, ledger: Any, working_memory: Any, programization: Any,
    repo_root: Path | None, executor: Callable[..., dict[str, Any]],
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
    if schedule.kind == KIND_NULL_CONTROL:
        # ALLOW-tier read: collects the same fed frame the factory mines, replays each stored
        # spec on the bars minted AFTER it against seeded null entries, and appends one record.
        # Touches no pool, no candidates, no orders.
        from .crypto import cycle as crypto_cycle
        from .crypto import null_control
        from .crypto.market_data import (
            collect_market_data,
            factory_candle_target,
            select_liquidation_feed,
            select_market_data_collector,
        )
        from .crypto.strategy import SpecParseError, StrategySpec
        from .crypto import factory as crypto_factory
        from .crypto import pool as crypto_pool

        parts = schedule.request.split()
        symbol = parts[0] if parts and parts[0] else "BTCUSDT"
        timeframe = parts[1] if len(parts) >= 2 else "4h"
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
        # All five legs, because the frame replayed here has to be the frame the spec was MINED
        # on. #601 established that inline; it now goes through `attach_mining_legs` with the
        # factory and the proposer, so the next caller cannot get four of five by copying.
        # What #601 measured, kept because it is the cost of NOT doing this: with `attach_feeds`
        # alone every htf_*, ref_*/rel_strength_*, xs_* and positioning_* column is None down the
        # whole window, so `measure_spec` answers `unsuppliable` permanently — 65 of the 798
        # selected specs (8.1%) read at least one such column, and they are the non-price
        # families the pool most needs judged. Invisible because `too_young` is checked first
        # and is still the answer for every cell; earliest measurable date ~2026-08-23.
        crypto_cycle.attach_mining_legs(
            snapshot, collector=collector, timeframe=timeframe, now=now, root=repo_root,
            liquidation_feed=select_liquidation_feed(now=now, root=repo_root),
            candle_target=factory_candle_target,
        )
        frame = crypto_factory.build_replay_frame(snapshot)
        # The seed is the window's own content hash, so a recorded fire replays identically —
        # the factory's rule, for the same reason.
        seed = int(integrity.sha256_record(
            {"candles": snapshot.get("candles") or []}).split(":", 1)[1][:8], 16)

        measurements: list[dict[str, Any]] = []
        for record in crypto_pool.read_candidates(repo_root):
            spec_dict = record.get("strategy_spec")
            if not isinstance(spec_dict, Mapping):
                continue
            if str(spec_dict.get("timeframe")) != timeframe:
                continue
            if symbol not in (spec_dict.get("symbol_scope") or []):
                continue
            # Selected strategies only. The store keeps every mint and roughly three quarters of
            # them were negative in their own scored window, so an unfiltered sample answers a
            # much weaker question — "does an arbitrary rule beat an arbitrary rule" — and that
            # is the error the first run of this measurement made.
            if float((record.get("backtest_evidence") or {}).get("expectancy") or 0.0) <= 0:
                continue
            try:
                spec = StrategySpec.from_dict(dict(spec_dict))
            except SpecParseError:
                continue
            outcome = null_control.measure_spec(
                spec, snapshot, frame,
                created_at_utc=str(record.get("created_at_utc") or ""), seed=seed,
                timeframe=timeframe,
            )
            if outcome is None:
                continue
            measurements.append({
                "candidate_id": record.get("candidate_id"),
                "strategy_family": spec.strategy_family,
                **outcome,
            })

        result = null_control.build_record(
            [{"symbol": symbol, "timeframe": timeframe, "measurements": measurements}],
            now=now, symbol=symbol, timeframe=timeframe,
        )
        if ledger is not None:
            ledger.append_records(
                f"NULLCTL-{integrity.short_id('null_control', {'at': now, 's': symbol, 't': timeframe})}",
                {null_control.LEDGER_KIND: result},
            )
        return null_control.status_line(result)

    if schedule.kind == KIND_ROUTE_WATCH:
        # Same delivery posture as the breaker watch below, including the part that matters
        # most: an undelivered announcement does NOT persist its marker, so the next fire
        # retries rather than going quiet about an incident nobody was told about. For this
        # watch that property is the whole point — the failure it exists to end was ten hours
        # of a runtime knowing something and saying nothing.
        from . import operator as operator_mod
        from .crypto import route_watch

        try:
            result = route_watch.run_route_watch(repo_root, now=now, persist=False)
        except MvpRuntimeError as exc:
            # An unreadable cycle ledger is exactly the state this watch must not swallow: it
            # cannot tell "no incident" from "cannot see", and reporting the second as the
            # first is the silence this module exists to prevent.
            return f"route_watch_unavailable:{exc.reason_code}"
        if not result["changed"]:
            return route_watch.status_line(result)
        try:
            channel = operator_mod.select_operator_channel(now=now, root=repo_root)
            operator_mod.notify_operator(channel, result["text"], repo_root=repo_root)
        except MvpRuntimeError as exc:
            return f"route_incident_not_sent:{exc.reason_code}"
        except Exception as exc:  # noqa: BLE001 — transport must not stop scheduling
            return f"route_incident_not_sent:{type(exc).__name__}"
        route_watch.write_mark(result["state"], root=repo_root)
        return f"route_watch_announced:{len(result['state'].get('in_incident') or [])}"

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
        from .crypto import cycle as crypto_cycle
        from .crypto import pool as crypto_pool
        from .crypto import positioning_store
        from .crypto.factory import run_factory
        from .crypto.market_data import (
            collect_market_data,
            factory_candle_target,
            select_liquidation_feed,
            select_market_data_collector,
        )

        # **A comma in the symbol field names a COHORT** — see `parse_factory_request`.
        symbols, timeframe = parse_factory_request(schedule.request)
        symbol = symbols[0]
        collector = select_market_data_collector(now=now, root=repo_root)
        liquidation_feed = select_liquidation_feed(now=now, root=repo_root)

        def _mining_snapshot(for_symbol: str):
            """One fully-attached leg. Returns None when the venue is degraded for it."""
            try:
                built, _ = collect_market_data(
                    for_symbol, timeframe, collector=collector, now=now,
                    limit=factory_candle_target(timeframe),
                )
            except ToolBlocked as exc:
                if exc.reason_code == "TOOL_ERROR":
                    return None
                raise
            # C9: the factory backtests on the same feed-enriched frame the router evaluates —
            # one feature source for backtest and live (the source rule). All five legs go
            # through `attach_mining_legs`, which is where that rule lives.
            crypto_cycle.attach_mining_legs(
                built, collector=collector, timeframe=timeframe, now=now, root=repo_root,
                liquidation_feed=liquidation_feed, candle_target=factory_candle_target,
            )
            return built

        snapshot = _mining_snapshot(symbol)
        if snapshot is None:
            return "skipped_market_data_degraded"
        # **A leg that cannot be fetched cancels the pooled fire rather than shrinking it.** A
        # four-leg pool reporting `symbols: 5` is the wrong-number failure this file's guards
        # exist for, and a cohort that silently changes size between fires is not one context.
        cohort = []
        for other in symbols[1:]:
            leg = _mining_snapshot(other)
            if leg is None:
                return f"skipped_cohort_leg_degraded:{other}"
            cohort.append(leg)
        # The store's own answer to "can you cover the window the factory replays". Read here
        # rather than inside the factory because `run_factory` is pure. A store that cannot be
        # read at all reports not-eligible, which is the safe direction: no data, no family.
        # Every mined leg, not just the primary: a family gated on positioning must be
        # suppliable everywhere the pooled spec claims to trade, and `coverage_summary` already
        # takes the list.
        positioning_eligible = bool(positioning_store.coverage_summary(
            repo_root, symbols=symbols or [symbol],
        )["eligible"])
        result = run_factory(
            snapshot,
            active_pool=crypto_pool.load_active_pool(repo_root),
            existing_candidates=crypto_pool.read_candidates(repo_root),
            now=now,
            fusion_pairs=FACTORY_FUSION_PAIRS,
            positioning_eligible=positioning_eligible,
            cohort_snapshots=cohort or None,
        )
        crypto_pool.append_candidates(result["candidates"], root=repo_root)
        if ledger is not None:
            ledger.append_records(result["generation_id"], {"crypto_factory": result})
        # `generated=N/M` rather than `generated=N`: a fire that fell short of what it asked for
        # recorded the shortfall in the ledger and said nothing here, so the only operator-facing
        # number read as a quantity. A stuck family is the one way to reach it (see
        # `factory._MAX_ATTEMPTS_PER_SPEC`) and it has never happened; the point is that it would
        # be legible if it did.
        # `topup=` because `requested_count` is no longer the constant `DEFAULT_BATCH_SIZE`:
        # it grows by whatever fusion left unspent, so `generated=8/8 fused=0` and
        # `generated=4/4 fused=4` are both complete fires and the denominator alone cannot say
        # which. Without it a shortfall draw that itself fell short would read as the stuck
        # family `_MAX_ATTEMPTS_PER_SPEC` exists to expose.
        # `ablated=` / `luck_filtered=` because the lattice multiplies replays per hypothesis
        # (factory.ablate_hypothesis) without any cadence change: the operator-facing line is
        # where that spend and its yield — full conjunctions that failed to beat their own
        # parts — become visible per fire, the same way `topup=` made the shortfall draw so.
        return (f"generated={result['accepted_count']}/{result['requested_count']} "
                f"fused={result.get('fused_count', 0)} "
                f"topup={result.get('seeded_topup_count', 0)} "
                f"ablated={result.get('ablated_count', 0)} "
                f"luck_filtered={result.get('luck_filtered_count', 0)} "
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
        from .crypto import cycle as crypto_cycle
        from .crypto import factory as crypto_factory
        from .crypto import proposer as crypto_proposer
        from .crypto.market_data import (
            collect_market_data,
            select_liquidation_feed,
            select_market_data_collector,
        )

        installed = [t.family for t in crypto_factory.TEMPLATES]
        # Archives included, because `BACKLOG_WINDOW_DAYS` is a SPAN and the active ledger's
        # horizon is a ROW COUNT. `iter_records` sees only the active file, so rotation was
        # silently deciding the window: measured 2026-08-08 on the live host, the documented
        # 30 days could see nine, four in-window proposal records having already rotated out,
        # and the backlog fell 15 -> 11 between 08-05 and 08-06 for that reason alone — a drop
        # that reads as families being installed and was the ledger being tidied. The error
        # direction is the bad one for a throttle: fewer counted, cap not reached, fires
        # proceed that the design meant to skip.
        window_start = timeutil.plus_minutes(
            now, -abs(crypto_proposer.BACKLOG_WINDOW_DAYS) * 24 * 60)
        # A malformed ledger must not stop the scheduler: an unreadable record stream
        # degrades to "backlog unknown = 0" (the fire proceeds) rather than failing closed —
        # the backlog cap is a courtesy throttle, not a safety gate.
        try:
            backlog = crypto_proposer.count_unreviewed_backlog(
                ledger.iter_records_with_archive(appended_since=window_start)
                if ledger is not None else [],
                installed, now=now,
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
        # The frame a proposal is JUDGED on has to be the frame a minted spec would be judged
        # on, and until now it was `collect_market_data` alone — OHLCV, nothing else. Measured
        # 2026-08-08 on a bare snapshot of this exact shape: **25 of the 56 mintable numeric
        # columns are None**, which is every funding, liquidation, open-interest, HTF, premium,
        # reference, cross-section and positioning column in the vocabulary.
        #
        # **NOT the taker columns**, and the exception is worth carrying because getting it
        # wrong is the easy mistake: `taker_buy_base` rides the same klines call as the candles,
        # so `taker_buy_ratio` / `taker_flow_*` are supplied on a bare frame. #621's first draft
        # measured a candle-archive snapshot, where that field is null because the DEX does not
        # send it, and concluded the taker families were starved. They are not —
        # `test_mvp_runtime_crypto_mining_frame` asserts it so the premise cannot be re-derived
        # from the wrong venue. Re-measured 2026-08-09 on the live collector: 0 of the 4 taker
        # columns are None on a bare Binance frame.
        #
        # `proposer.py`'s own docstring states the invariant this broke — the validator's
        # vocabulary "is a strict subset of the live feature row, so anything it accepts the row
        # can supply." True of the row the FACTORY builds; false of the one this block was
        # building. So the validator admitted a proposal naming `funding_zscore`, the backtest
        # scored it over a window where that column was None on every bar, and the proposal was
        # accepted with `closed_count: 0` and `expectancy: 0.0` — evidence that reads as "this
        # premise does not trade" when the truth is "this runtime did not supply the column."
        #
        # It is not a small share of the record: of the 26 accepted-but-uninstalled families in
        # the backlog, **22 score exactly zero trades, and 18 of those 22 name a column this
        # block did not supply.** The four that traded read price columns only. The four
        # zero-trade families starved by nothing — `macd_divergence_short`,
        # `taker_flow_mean_reversion`, `taker_flow_momentum`,
        # `volatility_expansion_breakout_long` — simply have tight or malformed conditions, and
        # two of them are taker families, which is the same draft error as above showing up in
        # the count. 18 of 22 is the correlation; 22 of 22 was never measured.
        #
        # `candle_target` is left None — the LIVE depths — because this block's base frame is
        # `collect_market_data`'s default window, not the factory's replay span. Legs pulled at
        # factory depth over a 120-bar frame would page data that the bar alignment then throws
        # away. That the proposal's backtest is therefore judged over a short window is a real
        # and separate question about the proposer's evidence depth; it is not this fix, and
        # widening it here would multiply the fire's request count for a reason nobody measured.
        crypto_cycle.attach_mining_legs(
            snapshot, collector=collector, timeframe=timeframe, now=now, root=repo_root,
            liquidation_feed=select_liquidation_feed(now=now, root=repo_root),
        )
        # The model call runs in `pipeline-worker` (Phase 2 D6); the verdicts stay here,
        # beside the market frame they are computed from. What crosses is the family list and
        # the focus — `build_proposal_prompt` never read the snapshot, which is why this
        # needed neither a second market-data path nor a serialized frame.
        generation = delegate_proposal_generation(
            existing_families=installed, focus=focus, repo_root=repo_root,
        )
        record = crypto_proposer.assemble_proposal_record(
            snapshot, generation=generation, focus=focus, now=now,
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
        # The model call runs in `pipeline-worker`, not here (Phase 2 D5). Everything around
        # it stays: this process builds the inventory (pure state reads), appends the record,
        # sends the operator's sheet, and judges the stall. What crosses is one frame of
        # ~3 KB and what comes back is the record — so an LLM response is parsed in a process
        # that holds no venue key. See `delegate_data_review` for the failure posture.
        record = delegate_data_review(inventory, now=now, repo_root=repo_root)
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
        status = (f"data_review={record['accepted_count']}/{record['suggested_count']} "
                  f"review={record['review_id']}{delivery}")
        # After the ledger append and after the sheet, never before: the record is the
        # evidence and the sheet is the operator's copy, and raising first would cost both to
        # report the same failure less usefully.
        if crypto_data_review.review_loop_is_stalled(record, schedule.last_status):
            raise SchedulerBlocked(crypto_data_review.DATA_REVIEW_STALLED, (
                f"the data-gap review has produced nothing for two consecutive fires; "
                f"this one: {record.get('degraded_reason')}. Previous status: "
                f"{schedule.last_status!r}. {status}"
            ))
        return status
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
        if summary.get("rate_limited"):
            # A pass the venue cut short. This is NOT covered by the all-degraded check above
            # and that gap is the whole reason this branch exists: on 2026-08-04 the first real
            # pass lost 282 of 352 books to rate limiting and reported COMPLETED, because 282 is
            # not 352. A summary string reaches nobody, so an 80% loss announced itself only to
            # a log line — while the window it races kept rolling. Raising puts it on the
            # existing failure alert within one cadence, which is what "on and not working"
            # is supposed to do here.
            raise SchedulerBlocked("ARCHIVE_RATE_LIMITED", (
                f"candle archiving was rate limited after {summary['books']} books; "
                f"{summary['skipped']} not attempted, kept={summary['written']}"
            ))
        return (f"candle_archive symbols={summary['symbols']} books={summary['books']} "
                f"kept={summary['written']} degraded={summary['degraded']} "
                f"deferred={summary.get('deferred', 0)}")
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
        schedule.request, working_memory=working_memory,
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
    repo_root: Path | None = None,
    executor: Callable[..., dict[str, Any]] = delegate_analysis_task,
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
    disable/remove and kept the schedule firing with no trace.

    **Order and budget.** Due schedules run risk kinds first (``RISK_KINDS``), then the rest in
    store order; and once a pass has spent ``MAINTENANCE_PASS_BUDGET_SECONDS`` it stops *starting*
    non-risk fires. A deferred occurrence is **not** claimed, so it stays due and the next pass
    runs it — deferral slips a cadence by one tick, it never drops an occurrence (unlike the
    kill-switch skip, which claims and drops by design). It is returned as ``deferred`` in the
    summary **and written to the ledger as an ``ACTION_DEFERRED`` event**, so a bounded pass
    survives the container it happened in — the summary and the CLI's per-result line do not.
    A fire already running is never
    interrupted, so the guarantee is that at most ONE maintenance fire can precede a due risk
    kind — see ``RISK_KINDS`` for the measurement that motivated it."""
    schedules = store.list()
    if control_store is None:
        control_store = ControlStore(repo_root if repo_root is not None else _repo_root())

    fired = 0
    skipped = 0
    failed = 0
    deferred = 0
    results: list[dict[str, Any]] = []

    # Risk kinds first, then everything else in store order. Within a pass this changes nothing
    # about WHICH fires happen — the same set is due — only the order, so a risk kind sharing a
    # due time no longer waits behind maintenance. `sorted` is stable, so the store's own order
    # survives inside each group and a schedule cannot change its position by being re-saved.
    due = [s for s in schedules if s.enabled and s.next_run_at <= now]
    due.sort(key=lambda s: 0 if s.kind in RISK_KINDS else 1)
    pass_started = time.monotonic()

    for schedule in due:
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

        # Budget: stop STARTING non-risk fires once this pass has spent its allowance.
        #
        # The occurrence is deliberately NOT claimed, and that is what separates this from the
        # kill-switch branch directly above, which claims and *drops*. Leaving it unclaimed leaves
        # it due, so the next pass — 30s later — runs it. Nothing is lost and no cadence is
        # skipped; the fire slips by a tick. Claiming here would silently discard maintenance
        # occurrences, which is the failure mode a budget must not introduce.
        #
        # After the kill-switch check on purpose: a halted runtime must still drop its due
        # occurrences exactly as before, so the budget cannot change what a kill does.
        # **And it is recorded on the ledger, like every other outcome of a due schedule.**
        # It was not, and the omission was mine: a deferral reached `results` (so the CLI's
        # per-result line prints it) and the run summary (so the tick line counts it), and
        # neither of those outlives the container. `docker-compose.yml` caps the service log at
        # 10m x 3 and a recreation discards it outright, which on this host happens several
        # times a day. Measured 2026-08-06: answering "how often does the budget bind?" — the
        # question that decides whether 60s is the right number — could only be done from
        # `docker logs`, and only for the current container's lifetime.
        #
        # No `run_id`, exactly like the kill-switch skip above, and that is load-bearing rather
        # than incidental: `find_abandoned_runs` pairs `started` against terminal events **by
        # `schedule_run_id`** and ignores every event without one. A deferral claims nothing and
        # starts nothing, so it must not carry a run id — with one it would be an unpaired
        # `started`-less row in a scan built to notice exactly that shape.
        #
        # Per schedule rather than per pass, which is the `skipped` precedent, and it composes:
        # grouping these by `created_at` gives passes-that-bound, which is the number to tune on.
        # The property that differs from `skipped` and is worth knowing before reading a count:
        # `skipped` claims and drops, so it appears once per occurrence, while a deferral leaves
        # the schedule due — so a schedule behind a long burst is deferred again on each pass
        # until it runs, and N rows mean N passes it waited, not N occurrences it lost.
        #
        # The row carries what the pass had SPENT and the budget it was spending against,
        # because the count alone does not settle the number it exists to tune. A pass that
        # bound at 61s and one that bound at 600s produce the same row otherwise, and they
        # argue opposite things: the first says 60 sits on the boundary and a larger value
        # would stop binding, the second says one fire is longer than any budget worth setting
        # and the number is not the lever. `pass_budget_ms` is recorded rather than assumed
        # from the constant, so raising it later does not silently re-scale the rows written
        # under the old one — the same reason `mutation_event` carries `previously_enabled`.
        #
        # Nested rather than one condition only so `elapsed` can be named: the clock is still
        # read for the kinds the budget can act on and no others, as the short-circuit did.
        # Membership, not absence: an unclassified kind is never deferred rather than always
        # deferrable. See `MAINTENANCE_KINDS` for why that direction is the recoverable one.
        if schedule.kind in MAINTENANCE_KINDS:
            elapsed = time.monotonic() - pass_started
            if elapsed > MAINTENANCE_PASS_BUDGET_SECONDS:
                deferred += 1
                status = "deferred_pass_budget"
                if ledger is not None:
                    ledger.append_scheduler_event(_scheduler_event(
                        ACTION_DEFERRED, schedule, now=now, status=status,
                        pass_elapsed_ms=int(elapsed * 1000),
                        pass_budget_ms=int(MAINTENANCE_PASS_BUDGET_SECONDS * 1000)))
                results.append({"schedule_id": schedule.schedule_id, "action": ACTION_DEFERRED,
                                "status": status})
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
                              repo_root=repo_root, executor=executor)
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

    return {"fired": fired, "skipped": skipped, "failed": failed, "deferred": deferred,
            "results": results}
