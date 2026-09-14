"""Inspect and snapshot the workflow store from inside a container.

    python -m runtime.mvp_runtime.workflow_cli list [--limit N]
    python -m runtime.mvp_runtime.workflow_cli inspect <workflow_id>
    python -m runtime.mvp_runtime.workflow_cli events [--after CURSOR] [--limit N]
    python -m runtime.mvp_runtime.workflow_cli snapshot [--dest DIR]

Sequence 2, P04. Every command opens the store **read-only** (the Q19 read rule: same uid,
same RW mount, ``mode=ro``) — this CLI writes no coordination state; the manager loop is the
one writer. ``snapshot`` copies the database with the SQLite backup API into
``<state>/workflow/snapshots/`` (or ``--dest``) beside a manifest, which is what
``scripts/ops/harness_backup.sh`` tars (P10) — a file copy of the live WAL database is not a
backup. Like every state-touching CLI it refuses a host-side root run, because the files it
would leave behind belong to uid 10001 (``state_guard``); run it as
``docker exec thomas-dispatch-bridge python -m runtime.mvp_runtime.workflow_cli …``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import state_guard, timeutil
from .paths import repo_root as _repo_root
from .cli_common import force_utf8_io
from .errors import MvpRuntimeError
from .workflow_store import WorkflowStore

EXIT_OK = 0
EXIT_BLOCKED = 2
EXIT_USAGE = 64


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="workflow_cli", description="Read or snapshot the workflow store.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_list = sub.add_parser("list", help="workflows, newest first")
    p_list.add_argument("--limit", type=int, default=20)
    p_inspect = sub.add_parser("inspect", help="one workflow's structured view (JSON)")
    p_inspect.add_argument("workflow_id")
    p_events = sub.add_parser("events", help="coordination events after a cursor")
    p_events.add_argument("--after", type=int, default=0)
    p_events.add_argument("--limit", type=int, default=50)
    p_snap = sub.add_parser("snapshot", help="a consistent copy of the database via the backup API")
    p_snap.add_argument("--dest", default=None, help="directory for the copy (default: <state>/workflow/snapshots)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, repo_root: Path | None = None, now: str | None = None) -> int:
    force_utf8_io()
    try:
        args = _parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code not in (0, None) else EXIT_OK
    stamp = now or timeutil.utc_now_iso()
    store = WorkflowStore.default(repo_root, readonly=True)
    try:
        if args.command == "list":
            rows = store.list_workflows(limit=args.limit)
            if not rows:
                print("no workflows")
                return EXIT_OK
            for r in rows:
                print(f"{r['workflow_id']}  {r['status']:<18} v{r['row_version']:<3} {r['created_at']}  {r['goal'][:60]}")
            print(f"({len(rows)} workflow(s))")
            return EXIT_OK
        if args.command == "inspect":
            view = store.status_view(args.workflow_id, now=stamp)
            print(json.dumps(view, ensure_ascii=False, indent=2))
            return EXIT_OK
        if args.command == "events":
            events, next_cursor = store.events_after(args.after, limit=args.limit)
            for e in events:
                where = e["step_id"] or e["workflow_id"]
                print(f"{e['cursor']:>6}  {e['created_at']}  {e['entity']:<8} {where}  "
                      f"{e['from_status'] or '-'} -> {e['to_status']}"
                      + (f"  [{e['reason_code']}]" if e["reason_code"] else ""))
            print(f"(next_cursor={next_cursor})")
            return EXIT_OK
        if args.command == "snapshot":
            root = repo_root if repo_root is not None else _repo_root()
            state_guard.assert_not_foreign_root_run(root)
            dest = Path(args.dest) if args.dest else None
            target = store.snapshot(dest, now=stamp)
            print(f"snapshot written: {target}")
            return EXIT_OK
    except MvpRuntimeError as exc:
        sys.stderr.write(f"BLOCKED [{exc.reason_code}]: {exc}\n")
        return EXIT_BLOCKED
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
