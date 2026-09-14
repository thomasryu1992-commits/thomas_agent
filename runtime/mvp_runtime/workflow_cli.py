"""Inspect and snapshot the workflow store from inside a container.

    python -m runtime.mvp_runtime.workflow_cli list [--limit N]
    python -m runtime.mvp_runtime.workflow_cli inspect <workflow_id>
    python -m runtime.mvp_runtime.workflow_cli events [--after CURSOR] [--limit N]
    python -m runtime.mvp_runtime.workflow_cli snapshot [--dest DIR]
    python -m runtime.mvp_runtime.workflow_cli verify --snapshot PATH     # P10: a copy's schema, cursor, counts
    python -m runtime.mvp_runtime.workflow_cli drain                      # P10: what is still in flight, both paths

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
import sqlite3
import sys
from pathlib import Path
from typing import Any

from . import state_guard, timeutil
from .paths import repo_root as _repo_root
from .cli_common import force_utf8_io
from .errors import MvpRuntimeError, PersistenceError
from .workflow_store import SCHEMA_VERSION, WorkflowStore

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
    p_verify = sub.add_parser("verify", help="open a snapshot copy read-only and report what it holds (P10 restore rehearsal)")
    p_verify.add_argument("--snapshot", required=True, help="path of a workflow-<stamp>.db copy")
    sub.add_parser("drain", help="what is still in flight on both paths: legacy AGENT rows and workflow attempts (P10 cutover)")
    return parser.parse_args(argv)


def verify_snapshot(path: Path) -> dict[str, Any]:
    """What a snapshot copy holds, read from the copy alone: the schema version it was written
    under, its highest event cursor, its row counts, SQLite's own integrity verdict, and whether
    the manifest beside it (when present) agrees. Never opens the live store."""
    if not path.is_file():
        raise PersistenceError("WORKFLOW_SNAPSHOT_MISSING", f"no snapshot at {path}")
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            version = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
            max_cursor = conn.execute("SELECT COALESCE(MAX(cursor), 0) FROM events").fetchone()[0]
            counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("workflows", "steps", "attempts", "events", "deliveries", "reported_usage")}
            with_results = conn.execute("SELECT COUNT(*) FROM steps WHERE result_ref IS NOT NULL").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise PersistenceError("WORKFLOW_SNAPSHOT_UNREADABLE", f"the snapshot cannot be read: {exc}") from exc
    report: dict[str, Any] = {
        "snapshot": path.as_posix(), "integrity": integrity, "schema_version": int(version),
        "current_schema_version": SCHEMA_VERSION, "max_event_cursor": int(max_cursor), "counts": counts,
        "steps_with_results": int(with_results), "manifest": None, "manifest_agrees": None,
    }
    manifest_path = path.with_name(path.name.replace(".db", ".manifest.json"))
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PersistenceError("WORKFLOW_SNAPSHOT_UNREADABLE", f"the manifest cannot be read: {exc}") from exc
        report["manifest"] = manifest
        report["manifest_agrees"] = (manifest.get("max_event_cursor") == int(max_cursor)
                                     and manifest.get("schema_version") == int(version)
                                     and manifest.get("snapshot") == path.name)
    report["ok"] = (integrity == "ok" and int(version) <= SCHEMA_VERSION and int(version) >= 1
                    and report["manifest_agrees"] is not False)
    return report


def drain_status(root: Path | None, *, now: str) -> dict[str, Any]:
    """What is still in flight on both paths, for a cutover or a rollback (P10, A22/A25): the
    legacy path's RUNNING registry rows by origin, and the workflow store's running attempts.
    Reads only; ``drained`` is True when nothing is in flight anywhere."""
    from .task_registry import TaskRegistryStore
    registry = TaskRegistryStore.default(root)
    running = [e for e in registry.latest() if e.status == "RUNNING"]
    by_origin: dict[str, list[str]] = {}
    for entry in running:
        by_origin.setdefault(entry.origin, []).append(entry.registry_entry_id)
    attempts: list[dict[str, Any]] = []
    store_present = WorkflowStore.exists(root)
    if store_present:
        store = WorkflowStore.default(root, readonly=True)
        attempts = [{"attempt_id": a["attempt_id"], "workflow_id": a["workflow_id"], "deadline_at": a["deadline_at"]}
                    for a in store.running_attempts()]
    return {"as_of": now, "legacy_running": {k: sorted(v) for k, v in sorted(by_origin.items())},
            "workflow_attempts_running": attempts, "workflow_store": "present" if store_present else "absent",
            "drained": not running and not attempts}


def main(argv: list[str] | None = None, *, repo_root: Path | None = None, now: str | None = None) -> int:
    force_utf8_io()
    try:
        args = _parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code not in (0, None) else EXIT_OK
    stamp = now or timeutil.utc_now_iso()
    store = WorkflowStore.default(repo_root, readonly=True)          # opened on first read; absent is a typed refusal
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
            if not WorkflowStore.exists(root):
                # No store means the manager never ran here: nothing to copy, and not a failure —
                # the backup script records `workflow-snapshot=absent` on this line.
                print("snapshot skipped: no workflow store")
                return EXIT_OK
            state_guard.assert_not_foreign_root_run(root)
            dest = Path(args.dest) if args.dest else None
            target = store.snapshot(dest, now=stamp)
            print(f"snapshot written: {target}")
            return EXIT_OK
        if args.command == "verify":
            report = verify_snapshot(Path(args.snapshot))
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return EXIT_OK if report["ok"] else EXIT_BLOCKED
        if args.command == "drain":
            report = drain_status(repo_root, now=stamp)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return EXIT_OK
    except MvpRuntimeError as exc:
        sys.stderr.write(f"BLOCKED [{exc.reason_code}]: {exc}\n")
        return EXIT_BLOCKED
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
