"""R6 Scheduler CLI — manage schedules and run the tick loop.

    # Manage (creating a schedule is EXECUTE_AND_REPORT — recorded to the ledger):
    python -m runtime.mvp_runtime.scheduler_cli add --kind analysis_task \
        --request "이 사업 아이디어를 분석해줘: ..." --interval-seconds 3600
    python -m runtime.mvp_runtime.scheduler_cli add --kind memory_prune --interval-seconds 86400
    python -m runtime.mvp_runtime.scheduler_cli list
    python -m runtime.mvp_runtime.scheduler_cli disable <schedule_id>
    python -m runtime.mvp_runtime.scheduler_cli remove <schedule_id>

    # Run the tick loop (respects the kill switch; one tick by default):
    python -m runtime.mvp_runtime.scheduler_cli tick --max-ticks 0 --interval-seconds 60

The tick loop runs a scheduled `analysis_task` in the `pipeline-worker` service rather than
in this process, so it selects no model provider and no search tool — that service holds
those keys and passes them through the same Safety-Flag Gate
(`docs/proposals/CREDENTIAL_PLANE_SEPARATION_PHASE2_V0.1.md`). It will not run a scheduled
task while the runtime is PAUSED/KILLED.

It also NOTIFIES the registered operator about scheduling that went wrong: a schedule that
starts failing, one that recovers, and — on startup — schedules left overdue while the loop
was not running at all, plus occurrences that started and never finished because the
process was killed mid-fire (the two failures a dead process can never report itself,
recovered by pairing each ``started`` event against its terminal one). Alerting needs
the operator registration plus an authorized channel; when either is missing the loop says
so at startup and runs with alerts off, because a silently disabled alerter is the very
failure this reports. Alerts never gate scheduling: the ledger event is the record of truth.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from . import heartbeat, operator, scheduler, task_registry, timeutil
from .cli_common import EXIT_BLOCKED, EXIT_OK, force_utf8_io, gate_banners, report_block
from .control import ControlStore
from .errors import MvpRuntimeError
from .programization import ProgramizationStore
from .scheduler import ScheduleStore
from .state_guard import assert_state_writable
from .store import LedgerStore
from .task_registry import TaskRegistryStore
from .working_memory import WorkingMemoryStore

LOCAL_ACTOR = "local_scheduler_cli"
GAP_ALERT_KEY = "startup_gap"
ABANDONED_ALERT_KEY = "startup_abandoned"


class OperatorAlerter:
    """Best-effort operator notifications for the tick loop.

    De-dups per key: an identical alert for the same schedule is sent once, so a schedule
    failing every interval does not spam the control channel — a CHANGED message (a new
    failure reason, or a recovery) alerts again. **Never raises**: alerting exists to
    report failures, so it must not become one. A failed send is counted and printed, and
    the durable scheduler event remains the record of truth.

    Sends go through ``operator.notify_operator``, which addresses the ONE registered
    private chat — this cannot message anyone but Thomas — over whatever transport the
    Safety-Flag Gate authorized (the mock channel notifies nobody and opens no socket)."""

    def __init__(self, channel: Any, *, repo_root: Path | None = None) -> None:
        self._channel = channel
        self._repo_root = repo_root
        self._last: dict[str, str] = {}
        self.sent = 0
        self.failed = 0

    def __call__(self, key: str, text: str) -> None:
        if self._last.get(key) == text:
            return
        try:
            operator.notify_operator(self._channel, text, repo_root=self._repo_root)
        except MvpRuntimeError as exc:
            self.failed += 1
            sys.stderr.write(f"ALERT FAILED ({exc.reason_code}): operator not notified; "
                             f"the scheduler event stands\n")
        except Exception as exc:  # noqa: BLE001 — transport errors must not stop scheduling
            self.failed += 1
            sys.stderr.write(f"ALERT FAILED ({type(exc).__name__}): operator not notified; "
                             f"the scheduler event stands\n")
        else:
            self._last[key] = text
            self.sent += 1


def build_alerter(*, repo_root: Path | None, now: str | None) -> OperatorAlerter | None:
    """The tick loop's operator alerter, or None when alerting cannot work.

    Both doors are checked UP FRONT and the outcome is announced, because a silently
    disabled alerter is exactly the failure this feature exists to prevent: no
    registration (nobody to tell) or a refused channel (no authorized transport) means
    the loop runs with alerts off, and says so."""
    try:
        operator.load_operator_registration(repo_root)
        channel = operator.select_operator_channel(now=now, root=repo_root)
    except MvpRuntimeError as exc:
        sys.stderr.write(f"SCHEDULER: operator alerts DISABLED ({exc.reason_code})\n")
        return None
    egress = bool(getattr(channel, "network_egress", True))
    sys.stderr.write(f"SCHEDULER: operator alerts enabled "
                     f"({'real channel' if egress else 'mock channel — notifies nobody'})\n")
    return OperatorAlerter(channel, repo_root=repo_root)


def remaining_period(interval_seconds: float, elapsed_seconds: float) -> float:
    """How long to sleep after a pass that took ``elapsed_seconds`` of its own period.

    The pass belongs to the period it ran in, so what is left to wait is the REMAINDER.
    Sleeping a fresh full interval on top of the work made the poll grid ``work + interval``
    wide — a 20s pm_scan on a 30s interval polled every 50s — and since a schedule can only
    be claimed on a poll line, the grid became the cadence rather than the registered
    interval. That is the loop half of the drift; ``scheduler.next_occurrence`` is the
    store half, and neither alone is enough.

    Clamped at zero: a pass longer than its period (a 190s ``crypto_factory`` fire on a 30s
    interval) polls again immediately rather than borrowing from the next period, and
    ``sleep`` is never handed a negative number. It does **not** catch up beyond that — this
    function paces polling only. Which occurrences are owed is ``claim_due``'s to say, and
    it owes at most one.
    """
    return max(0.0, interval_seconds - elapsed_seconds)


def report_startup_gap(
    store: ScheduleStore, *, now: str, ledger: LedgerStore | None, alerter: OperatorAlerter | None
) -> list[tuple[Any, int]]:
    """Detect and report scheduling the loop missed while it was NOT RUNNING.

    A schedule more than a full interval overdue means nothing ticked it — the one
    failure mode an in-process guard can never catch (a dead process reports nothing).
    Recorded durably as ``gap_detected`` scheduler events so the downtime is evidence on
    the ledger, not just a Telegram message that could fail to send."""
    late = scheduler.overdue_schedules(store.list(), now=now)
    if not late:
        return []
    for schedule, overdue in late:
        sys.stderr.write(f"SCHEDULER: gap detected — {schedule.kind} ({schedule.schedule_id}) "
                         f"overdue by {overdue}s\n")
        if ledger is not None:
            ledger.append_scheduler_event(scheduler._scheduler_event(
                "gap_detected", schedule, now=now, status=f"overdue_seconds={overdue}"))
    if alerter is not None:
        lines = "\n".join(
            f"- {s.kind} ({s.schedule_id}): {overdue // 60}분 지연" for s, overdue in late
        )
        alerter(GAP_ALERT_KEY,
                "[스케줄 공백 감지] 스케줄러가 실행되지 않은 구간이 있습니다.\n"
                f"{lines}\n\n"
                f"재시작 시각: {now}\n"
                "이 구간의 회차들은 유실됐습니다(at-most-once).")
    return late


def report_abandoned_runs(
    *, ledger: LedgerStore | None, now: str, alerter: OperatorAlerter | None
) -> list[dict[str, Any]]:
    """Close out occurrences whose process died mid-fire, and report them.

    A fire killed between its claim and its outcome writes no terminal event: the
    schedule just skipped a beat with no explanation. The orphaned ``started`` event is
    the evidence, and this is the only vantage point that can pair it — a dead process
    diagnoses nothing. Each orphan gets its honest ending (an ``abandoned`` event, which
    also makes this idempotent across restarts) and one operator alert.

    An unreadable scheduler ledger only skips the scan, loudly: the tick loop must still
    start. This is diagnosis, not a gate."""
    if ledger is None:
        return []
    try:
        events = ledger.read_scheduler_events()
    except MvpRuntimeError as exc:
        sys.stderr.write(f"SCHEDULER: abandoned-run scan SKIPPED ({exc.reason_code})\n")
        return []
    abandoned = scheduler.find_abandoned_runs(events)
    for started in abandoned:
        sys.stderr.write(f"SCHEDULER: abandoned run — {started.get('kind')} "
                         f"({started.get('schedule_id')}) started {started.get('created_at')} "
                         f"never finished\n")
        ledger.append_scheduler_event(scheduler.abandoned_event(started, now=now))
    if abandoned and alerter is not None:
        lines = "\n".join(
            f"- {e.get('kind')} ({e.get('schedule_id')}): {e.get('created_at')} 시작"
            for e in abandoned
        )
        alerter(ABANDONED_ALERT_KEY,
                "[스케줄 중단] 실행 도중 프로세스가 종료된 회차가 있습니다.\n"
                f"{lines}\n\n"
                f"확인 시각: {now}\n"
                "해당 회차는 완료되지 않았고 재시도하지 않습니다(at-most-once).")
    return abandoned


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="scheduler_cli", description="Manage schedules and run the tick loop.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="add a schedule")
    p_add.add_argument("--kind", choices=sorted(scheduler.KINDS), required=True)
    p_add.add_argument("--request", default="", help="task request (required for analysis_task)")
    p_add.add_argument("--interval-seconds", type=int, required=True)
    p_add.add_argument("--reason", default="")
    p_add.add_argument("--disabled", action="store_true", help="create the schedule disabled")

    sub.add_parser("list", help="list schedules")
    for verb in ("remove", "enable", "disable"):
        p = sub.add_parser(verb, help=f"{verb} a schedule")
        p.add_argument("schedule_id")

    p_tick = sub.add_parser("tick", help="run due schedules")
    p_tick.add_argument("--max-ticks", type=int, default=1, help="ticks to run; 0 = until interrupted (default 1)")
    p_tick.add_argument("--interval-seconds", type=float, default=60.0, help="sleep between ticks (default 60)")
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    store: ScheduleStore | None = None,
    ledger: LedgerStore | None = None,
    control_store: ControlStore | None = None,
    working_memory: WorkingMemoryStore | None = None,
    repo_root: Path | None = None,
    now: str | None = None,
    sleep: Any = time.sleep,
    monotonic: Any = time.monotonic,
    alerter: OperatorAlerter | None = None,
) -> int:
    """Run one scheduler command. Returns 0 on success, non-zero on a fail-closed block.
    Dependencies are injectable for tests; unset ones default to local state / the gate.

    ``monotonic`` is injected alongside ``sleep`` because the pair is one mechanism: the
    loop measures its own pass and sleeps the remainder of the period. It is read at
    exactly two sites (around ``run_due``), so an injected clock controls the measured
    elapsed time without sharing a call count with ``run_due``'s own ``time.monotonic``
    reads for ``duration_ms``. Timing a real pass cannot pin the ordering: on Windows
    ``time.monotonic`` has ~15.6ms granularity and a small pass finishes inside one tick,
    measuring exactly 0.0 (#439)."""
    force_utf8_io()
    args = _parse_args(argv)
    store = store if store is not None else ScheduleStore.default(repo_root)
    ledger = ledger if ledger is not None else LedgerStore.default(repo_root)

    try:
        # The tick loop's equivalent of the operator's startup guard: a scheduler that
        # cannot write state would claim occurrences it can never record the outcome of,
        # which is the one failure at-most-once execution must not have.
        assert_state_writable(repo_root)
        if args.command == "add":
            stamp = now or timeutil.utc_now_iso()
            sched = scheduler.build_schedule(
                kind=args.kind, request=args.request, interval_seconds=args.interval_seconds,
                created_by=LOCAL_ACTOR, now=stamp, reason=args.reason, enabled=not args.disabled,
            )
            store.add(sched)
            ledger.append_scheduler_event(
                scheduler.mutation_event(scheduler.ACTION_CREATED, sched, now=stamp)
            )
            sys.stdout.write(f"added schedule {sched.schedule_id} ({sched.kind}, every {sched.interval_seconds}s, "
                             f"next {sched.next_run_at})\n")
            return EXIT_OK

        if args.command == "list":
            schedules = store.list()
            if not schedules:
                sys.stdout.write("no schedules\n")
            for s in schedules:
                state = "enabled" if s.enabled else "disabled"
                sys.stdout.write(f"{s.schedule_id}  {s.kind:<14} every {s.interval_seconds}s  {state}  "
                                 f"next={s.next_run_at}  last={s.last_run_at or '-'}({s.last_status or '-'})\n")
            return EXIT_OK

        if args.command in ("remove", "enable", "disable"):
            # Mutate, then record — the same order `add` above uses. A ledger write that
            # fails after the store one leaves the gap this whole change closes, so the
            # ordering is only defensible because `assert_state_writable` above already
            # refused the unwritable case at the door, for the store and ledger alike.
            stamp = now or timeutil.utc_now_iso()
            enabling = args.command == "enable"
            if args.command == "remove":
                affected = store.remove(args.schedule_id)
            else:
                affected = store.set_enabled(args.schedule_id, enabling)
            if affected is None:
                sys.stderr.write(f"BLOCKED NOT_FOUND: no schedule {args.schedule_id}\n")
                return EXIT_BLOCKED
            # `affected` is the PRE-mutation record, so its own `enabled` is the previous
            # state — carried explicitly because a re-issued command is a real operator
            # action that changed nothing, and the event has to be able to say so.
            action = {"remove": scheduler.ACTION_REMOVED,
                      "enable": scheduler.ACTION_ENABLED,
                      "disable": scheduler.ACTION_DISABLED}[args.command]
            ledger.append_scheduler_event(scheduler.mutation_event(
                action, affected, now=stamp, previously_enabled=affected.enabled))
            sys.stdout.write(f"{args.command}d {args.schedule_id}\n")
            return EXIT_OK

        # tick
        control_store = control_store if control_store is not None else ControlStore.default(repo_root)
        working_memory = working_memory if working_memory is not None else WorkingMemoryStore.default(repo_root)
        programization = ProgramizationStore.default(repo_root)
        # No analysis provider or search tool is selected here any more, and the absence is
        # the change rather than an omission: an `analysis_task` fire runs in the
        # `pipeline-worker` service, which holds those keys and selects both through the same
        # Safety-Flag Gate (`docs/proposals/CREDENTIAL_PLANE_SEPARATION_PHASE2_V0.1.md`).
        # Selecting them here would print a gate banner describing a capability this process
        # no longer uses — and after the compose change it would print the MOCK banner while
        # delegated fires ran real analyses, which is worse than saying nothing.
        gate_banners()
        sys.stderr.write(f"SCHEDULER: ticking (ledger: {ledger.root}; control: {control_store.load().mode})\n")
        if alerter is None:
            alerter = build_alerter(repo_root=repo_root, now=now)
        # Before the first tick, report what went wrong while this loop was NOT running:
        # occurrences nothing ticked (gap), and occurrences that started but never
        # finished (abandoned). After the first tick both are current by construction.
        startup_stamp = now or timeutil.utc_now_iso()
        report_startup_gap(store, now=startup_stamp, ledger=ledger, alerter=alerter)
        report_abandoned_runs(ledger=ledger, now=startup_stamp, alerter=alerter)
        # ...and the coordination view of the same thing. A KIND_TASK fire opens a RUNNING
        # registry entry (`scheduler.py`), so a scheduler killed mid-analysis strands it — and
        # nothing closed it, because only the operator service reconciled and it (correctly, as
        # of this change) no longer touches another service's origins. Scoped to SCHEDULER for
        # the same reason: an operator request in flight is not this process's to abandon.
        # Best-effort, like the operator's: bookkeeping must not stop the service.
        try:
            stranded = task_registry.reconcile_stale_running(
                TaskRegistryStore.default(repo_root), now=startup_stamp,
                origins=task_registry.SCHEDULER_ORIGINS,
            )
            if stranded:
                sys.stderr.write(
                    f"SCHEDULER: {len(stranded)} interrupted scheduled task(s) marked RUN_ABANDONED\n"
                )
        except MvpRuntimeError as exc:
            sys.stderr.write(f"SCHEDULER: task registry not reconciled ({exc.reason_code})\n")

        # Stamp once before the first tick so a probe has an answer from the moment the
        # service is up, and once per completed pass thereafter. Best-effort: a heartbeat
        # write that fails must not stop the scheduling it only observes.
        def _beat() -> None:
            try:
                heartbeat.write_heartbeat(
                    heartbeat.SCHEDULER_SERVICE,
                    interval_seconds=args.interval_seconds, now=now, root=repo_root,
                )
            except OSError as exc:
                sys.stderr.write(f"SCHEDULER: heartbeat not written ({type(exc).__name__})\n")

        _beat()
        total_fired = 0
        total_skipped = 0
        total_failed = 0
        total_deferred = 0
        tick = 0
        try:
            while args.max_ticks == 0 or tick < args.max_ticks:
                pass_started = monotonic()
                summary = scheduler.run_due(
                    store, now=now or timeutil.utc_now_iso(), control_store=control_store, ledger=ledger,
                    working_memory=working_memory, programization=programization,
                    repo_root=repo_root,
                    notifier=alerter,
                    # F1: scheduled analysis runs join the same coordination view the
                    # operator's own requests appear in — otherwise /tasks would show
                    # nothing while an unattended run held the tick.
                    registry=TaskRegistryStore.default(repo_root),
                )
                total_fired += summary["fired"]
                total_skipped += summary["skipped"]
                total_failed += summary["failed"]
                total_deferred += summary["deferred"]
                for r in summary["results"]:
                    sys.stderr.write(f"  {r['action']} {r['schedule_id']} -> {r['status']}\n")
                # The maintenance budget, said out loud on the pass that spent it.
                #
                # The summary at the bottom of this function already counts `deferred`, and its
                # comment argues that a budget which drops work off the end of a line "reads as
                # nothing was due". True - and in the deployment that matters the line never
                # prints at all: the service runs `--max-ticks 0`, so the `while` never exits
                # and the writer below is unreachable until SIGTERM, which does not raise
                # KeyboardInterrupt either. `total_deferred` accumulated for the life of the
                # container and then died with it.
                #
                # What this is NOT, corrected before it shipped because the first draft of this
                # comment claimed it and the four lines directly above refute it: the budget was
                # never silent. `results` carries a `deferred` entry per schedule and the loop
                # prints each one, so every deferral has always been in this log — 20 of them in
                # the running container when this was measured (2026-08-06). What was missing was
                # the aggregate, and the aggregate lived on a line the service cannot reach.
                #
                # So this adds a per-pass count and a running total: one greppable line saying
                # the budget bound, instead of a reader counting per-schedule lines to find out.
                #
                # Durability is a separate question and is answered elsewhere - `run_due` writes
                # an `ACTION_DEFERRED` scheduler event (#596), which is what survives the
                # container this log dies with. Neither replaces the other: the event is how you
                # count binds over weeks, this is how you see one while reading the log.
                #
                # Quiet by construction - `deferred` is 0 on a pass that did not bind, and a
                # burst inside one pass is one line, not one per schedule.
                if summary["deferred"]:
                    sys.stderr.write(
                        f"SCHEDULER: maintenance budget bound this pass - deferred "
                        f"{summary['deferred']} (running total {total_deferred} "
                        f"over {tick + 1} tick(s))\n"
                    )
                tick += 1
                _beat()
                if args.interval_seconds > 0 and (args.max_ticks == 0 or tick < args.max_ticks):
                    sleep(remaining_period(args.interval_seconds, monotonic() - pass_started))
        except KeyboardInterrupt:
            sys.stderr.write("\nSCHEDULER: stopped.\n")
        alerts = f", alerts {alerter.sent} sent/{alerter.failed} failed" if alerter is not None else ""
        # `deferred` is APPENDED rather than woven in: `.github/workflows/docker-image.yml`
        # asserts a clean tick by grepping the literal "fired 0, skipped 0, failed 0", so the
        # three counters have to stay adjacent and in that order. A pass that bounded itself must
        # still say so — a budget that silently drops work off the end of a summary line reads as
        # "nothing was due".
        sys.stdout.write(f"fired {total_fired}, skipped {total_skipped}, failed {total_failed}, "
                         f"deferred {total_deferred} over {tick} tick(s){alerts}\n")
        return EXIT_OK

    except MvpRuntimeError as exc:
        return report_block(exc)


if __name__ == "__main__":
    raise SystemExit(main())
