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

The tick loop selects a provider/search tool through the Safety-Flag Gate exactly like the
operator loop, and it will not run a scheduled task while the runtime is PAUSED/KILLED.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from . import scheduler, timeutil
from .cli_common import EXIT_BLOCKED, EXIT_OK, force_utf8_io, gate_banners, report_block
from .control import ControlStore
from .errors import MvpRuntimeError
from .providers import select_provider
from .scheduler import ScheduleStore
from .store import LedgerStore
from .tools import select_search_tool
from .working_memory import WorkingMemoryStore

LOCAL_ACTOR = "local_scheduler_cli"


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
    provider: Any | None = None,
    search_tool: Any | None = None,
    repo_root: Path | None = None,
    now: str | None = None,
    sleep: Any = time.sleep,
) -> int:
    """Run one scheduler command. Returns 0 on success, non-zero on a fail-closed block.
    Dependencies are injectable for tests; unset ones default to local state / the gate."""
    force_utf8_io()
    args = _parse_args(argv)
    store = store if store is not None else ScheduleStore.default()
    ledger = ledger if ledger is not None else LedgerStore.default()

    try:
        if args.command == "add":
            stamp = now or timeutil.utc_now_iso()
            sched = scheduler.build_schedule(
                kind=args.kind, request=args.request, interval_seconds=args.interval_seconds,
                created_by=LOCAL_ACTOR, now=stamp, reason=args.reason, enabled=not args.disabled,
            )
            store.add(sched)
            ledger.append_scheduler_event(
                scheduler._scheduler_event("created", sched, now=stamp,
                                           status="enabled" if sched.enabled else "disabled")
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
            if args.command == "remove":
                ok = store.remove(args.schedule_id)
            else:
                ok = store.set_enabled(args.schedule_id, args.command == "enable")
            if not ok:
                sys.stderr.write(f"BLOCKED NOT_FOUND: no schedule {args.schedule_id}\n")
                return EXIT_BLOCKED
            sys.stdout.write(f"{args.command}d {args.schedule_id}\n")
            return EXIT_OK

        # tick
        control_store = control_store if control_store is not None else ControlStore.default()
        working_memory = working_memory if working_memory is not None else WorkingMemoryStore.default()
        provider = provider if provider is not None else select_provider()
        search_tool = search_tool if search_tool is not None else select_search_tool()
        gate_banners(provider=provider, search_tool=search_tool)
        sys.stderr.write(f"SCHEDULER: ticking (ledger: {ledger.root}; control: {control_store.load().mode})\n")

        total_fired = 0
        total_skipped = 0
        tick = 0
        try:
            while args.max_ticks == 0 or tick < args.max_ticks:
                summary = scheduler.run_due(
                    store, now=now or timeutil.utc_now_iso(), control_store=control_store, ledger=ledger,
                    working_memory=working_memory, provider=provider, search_tool=search_tool, repo_root=repo_root,
                )
                total_fired += summary["fired"]
                total_skipped += summary["skipped"]
                for r in summary["results"]:
                    sys.stderr.write(f"  {r['action']} {r['schedule_id']} -> {r['status']}\n")
                tick += 1
                if args.interval_seconds > 0 and (args.max_ticks == 0 or tick < args.max_ticks):
                    sleep(args.interval_seconds)
        except KeyboardInterrupt:
            sys.stderr.write("\nSCHEDULER: stopped.\n")
        sys.stdout.write(f"fired {total_fired}, skipped {total_skipped} over {tick} tick(s)\n")
        return EXIT_OK

    except MvpRuntimeError as exc:
        return report_block(exc)


if __name__ == "__main__":
    raise SystemExit(main())
