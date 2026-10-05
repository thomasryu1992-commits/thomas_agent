#!/usr/bin/env python3
"""Rank checkpoints for published blog packages — listed read-only, taken one at a time by hand.

Phase 4's first piece (`runtime/mvp_runtime/blog_rank.py`). Nothing here is scheduled and
nothing registers a schedule; the weekly content lane stays disabled (Thomas 2026-09-27).

    1) Which published packages have a checkpoint due (read-only, no network):
        python -m scripts.track_blog_rank --due
    2) Take one due checkpoint (one read-only blog search; appends one snapshot row):
        python -m scripts.track_blog_rank --check --package-id bcp_...

Only packages with a `published_url` (written by `scripts.record_published_url`) are tracked.
`--check` refuses a checkpoint that is not due or was already taken, and refuses to run on the
deterministic Mock: with the research gate closed (`MVP_NAVER_RESEARCH` unset) the "rank" would
be a fixture, and a fixture recorded as an observation is worse than no observation. Run it
where the gate is open — `docker exec thomas-pipeline-worker python -m scripts.track_blog_rank …`
— never on the host as root (the ledger is uid 10001's).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import blog_rank, naver_research  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError, ToolError  # noqa: E402
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run  # noqa: E402
from runtime.mvp_runtime.store import LedgerStore  # noqa: E402
from runtime.read_only_kernel.schema_validation import (  # noqa: E402
    RuntimeSchemaError,
    validate_against_schema,
)
from scripts.record_published_url import latest_packages  # noqa: E402

_ISO = "%Y-%m-%dT%H:%M:%SZ"
_SCHEMA_PATH = ROOT / "schemas" / f"{blog_rank.SNAPSHOT_SCHEMA_VERSION}.schema.json"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Blog rank checkpoints for published packages.")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--due", action="store_true", help="read-only: list due checkpoints")
    mode.add_argument("--check", action="store_true", help="take the due checkpoint of one package")
    p.add_argument("--package-id", help="the package to check (with --check)")
    p.add_argument("--root", type=Path, default=Path("."),
                   help="repo root holding .runtime_governance_state (default: cwd)")
    return p.parse_args(argv)


def _snapshot_rows(store: LedgerStore) -> list[dict]:
    return list(store.iter_records_with_archive(kinds=(blog_rank.SNAPSHOT_RECORD_KIND,)))


def due_list(store: LedgerStore, now: str) -> list[dict]:
    """One line of state per published package. Pure over the ledger."""
    rows = _snapshot_rows(store)
    out = []
    for package in sorted(latest_packages(store).values(), key=lambda r: str(r.get("created_at_utc"))):
        if not blog_rank.trackable(package):
            continue
        taken = blog_rank.taken_checkpoints(rows, package["package_id"])
        status = blog_rank.checkpoint_status(
            str(package.get("published_at_utc") or package["created_at_utc"]), now, taken)
        out.append({"package": package, "taken": taken, **status})
    return out


def main(argv: list[str] | None = None, *, tool=None, now: str | None = None) -> int:
    args = _parse_args(argv)
    root = args.root.resolve()
    store = LedgerStore.default(root)
    now = now or datetime.now(timezone.utc).strftime(_ISO)

    if args.due:
        entries = due_list(store, now)
        if not entries:
            print("no published blog packages (record one with scripts.record_published_url)")
            return 0
        for e in entries:
            p = e["package"]
            print(f"{p['package_id']}  due={e['due'] or '-'}  taken={','.join(e['taken']) or '-'}  "
                  f"missed={','.join(e['missed']) or '-'}  next={e['next'] or '-'}  "
                  f"{p.get('target_keyword')}  {p.get('published_url')}")
        return 0

    if not args.package_id:
        print("ERROR: --check needs --package-id", file=sys.stderr)
        return 2
    try:
        assert_not_foreign_root_run(root)
    except MvpRuntimeError as exc:
        print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return 3

    package = latest_packages(store).get(args.package_id)
    if package is None:
        print(f"ERROR PACKAGE_NOT_FOUND: {args.package_id}", file=sys.stderr)
        return 2
    if str(package.get("platform") or "naver") != "naver":
        print(f"ERROR {blog_rank.PLATFORM_NOT_TRACKED}: {args.package_id} is a "
              f"{package.get('platform')} package; this tracker reads Naver's blog search only",
              file=sys.stderr)
        return 2
    if not blog_rank.trackable(package):
        print(f"ERROR {blog_rank.PACKAGE_NOT_PUBLISHED}: {args.package_id} has no published_url",
              file=sys.stderr)
        return 2
    entry = next(e for e in due_list(store, now) if e["package"]["package_id"] == args.package_id)
    if entry["due"] is None:
        code = (blog_rank.CHECKPOINT_ALREADY_TAKEN if entry["latest"]
                else blog_rank.CHECKPOINT_NOT_DUE)
        print(f"ERROR {code}: nothing due for {args.package_id} (taken={entry['taken']}, "
              f"next={entry['next']})", file=sys.stderr)
        return 2

    tool = tool if tool is not None else naver_research.select_competition_tool()
    if not getattr(tool, "network_egress", False):
        print("ERROR RANK_SOURCE_MOCK: the research gate is closed here, so the lookup would be "
              "the deterministic Mock — run where MVP_NAVER_RESEARCH=enabled", file=sys.stderr)
        return 2
    try:
        record = blog_rank.check_rank(package, tool=tool, now=now, checkpoint=entry["due"])
        validate_against_schema(record, _SCHEMA_PATH, blog_rank.SNAPSHOT_RECORD_KIND)
        store.append_records(args.package_id, {blog_rank.SNAPSHOT_RECORD_KIND: record})
    except ToolError as exc:
        print(f"ERROR {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return 2
    except RuntimeSchemaError as exc:
        print(f"ERROR {blog_rank.RANK_SNAPSHOT_SCHEMA_INVALID}: {exc}", file=sys.stderr)
        return 2

    rank = record["rank_position"]
    print(f"recorded {record['checkpoint']} for {args.package_id}: "
          + (f"rank {rank} of {record['result_window']}" if rank else
             f"not in the top {record['result_window']}" if not record["degraded"] else
             f"lookup failed ({record['degraded_reason']})"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
