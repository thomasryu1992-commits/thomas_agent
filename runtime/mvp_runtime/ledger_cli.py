"""Ledger retention CLI — show what the ledgers cost, and rotate them.

    python -m runtime.mvp_runtime.ledger_cli status
    python -m runtime.mvp_runtime.ledger_cli rotate [--keep 2000] [--file records.jsonl]

Rotation **archives**; it never deletes. Rows move out of the active file into
``runtime_ledger/archive/`` beside it, byte-identical, still on disk. The audit chain and
the control-event ledger are refused outright — see ``retention.PROTECTED_FILES`` for the
separate reason each is excluded.

``status`` is read-only and answers in any runtime mode: knowing what the store costs is
the same class of question as ``/status``, and an operator asking it is usually asking
because something is already wrong.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import retention
from .cli_common import EXIT_BLOCKED, EXIT_OK, force_utf8_io, report_block
from .errors import MvpRuntimeError
from .store import LedgerStore


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect and rotate the runtime ledgers.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show each ledger's size and row count (read-only)")
    rotate = sub.add_parser("rotate", help="archive all but the newest rows (never deletes)")
    rotate.add_argument("--keep", type=int, default=retention.DEFAULT_KEEP_ROWS,
                        help=f"rows to leave in the active file (default {retention.DEFAULT_KEEP_ROWS})")
    rotate.add_argument("--file", default=None,
                        help="rotate only this ledger (default: every rotatable one)")
    return parser.parse_args(argv)


def _status_lines(store: LedgerStore) -> list[str]:
    root = Path(store.root)
    lines = [f"ledger root: {root}"]
    for filename in retention.ROTATABLE_FILES + tuple(retention.PROTECTED_FILES):
        protected = "  PROTECTED" if filename in retention.PROTECTED_FILES else ""
        path = root / filename
        if not path.is_file():
            lines.append(f"  {filename:28} {'(absent)':>19}{protected}")
            continue
        size_mb = path.stat().st_size / 1024 / 1024
        with path.open(encoding="utf-8") as handle:
            rows = sum(1 for line in handle if line.strip())
        lines.append(f"  {filename:28} {size_mb:8.1f} MB  {rows:>7} rows{protected}")
    archive = retention.archive_dir(root)
    if archive.is_dir():
        files = sorted(archive.glob("*.jsonl"))
        total = sum(p.stat().st_size for p in files) / 1024 / 1024
        lines.append(f"  archive/{'':20} {total:8.1f} MB  {len(files):>7} files")
    for filename, reason in retention.PROTECTED_FILES.items():
        lines.append(f"\n{filename} is never rotated —\n  {reason}")
    return lines


def main(argv: list[str] | None = None, *, store: LedgerStore | None = None,
         repo_root: Path | None = None, now: str | None = None) -> int:
    force_utf8_io()
    args = _parse_args(argv)
    store = store if store is not None else LedgerStore.default(repo_root)

    try:
        if args.command == "status":
            sys.stdout.write("\n".join(_status_lines(store)) + "\n")
            return EXIT_OK

        if args.file:
            summary: dict[str, Any] = retention.rotate_file(
                store, args.file, keep_rows=args.keep, now=now)
            sys.stdout.write(
                f"{summary['filename']}: archived {summary['rotated']} row(s), "
                f"kept {summary['kept']}"
                + (f" -> {summary['archive']}" if summary["archive"] else " (nothing to do)")
                + "\n")
            return EXIT_OK

        summary = retention.rotate_all(store, keep_rows=args.keep, now=now)
        if not summary["rotated_rows"]:
            sys.stdout.write(f"nothing to rotate (every ledger is within {args.keep} rows)\n")
        for entry in summary["files"]:
            sys.stdout.write(f"{entry['filename']}: archived {entry['rotated']} row(s) "
                             f"-> {entry['archive']}\n")
        for failure in summary["failures"]:
            sys.stderr.write(f"FAILED {failure['filename']}: {failure['reason_code']}\n")
        if summary.get("event_error"):
            sys.stderr.write(f"WARNING: rows moved but the retention event was not "
                             f"recorded ({summary['event_error']})\n")
        return EXIT_BLOCKED if summary["failures"] else EXIT_OK
    except MvpRuntimeError as exc:
        return report_block(exc)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
