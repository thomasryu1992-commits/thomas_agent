#!/usr/bin/env python3
"""Write the kind index beside record archives that predate it — one-time backfill.

Rotation indexes every archive it closes from 2026-09-01 on. Archives closed before that
have no index, and an unindexed archive is always opened by a filtered read (fail-closed:
"cannot see" must never render as "nothing there"), so the ones already on disk keep costing
what they cost. This walks them once.

    python -m scripts.build_ledger_archive_index --list     # what is unindexed, read-only
    python -m scripts.build_ledger_archive_index --apply

Safe to re-run: an archive that already has an index is skipped, and the index itself is
written atomically beside a file that is immutable by design. Nothing here reads or writes a
ledger row — the archives are opened read-only, in binary, and only the sidecars are created.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.retention import write_archive_index  # noqa: E402
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run  # noqa: E402
from runtime.mvp_runtime.store import (  # noqa: E402
    ARCHIVE_DIR,
    RECORDS_FILE,
    LedgerStore,
    archive_index_path,
)


def unindexed(store: LedgerStore) -> list[Path]:
    directory = Path(store.root) / ARCHIVE_DIR
    if not directory.is_dir():
        return []
    stem = RECORDS_FILE.removesuffix(".jsonl")
    return [p for p in sorted(directory.glob(f"{stem}.*.jsonl"))
            if not archive_index_path(p).is_file()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill record-archive kind indexes.")
    parser.add_argument("--apply", action="store_true", help="write the missing indexes")
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)

    root = args.root.resolve()
    store = LedgerStore.default(root)
    missing = unindexed(store)
    if not missing:
        print("every record archive is indexed")
        return 0
    total_mb = sum(p.stat().st_size for p in missing) / 1e6
    if not args.apply:
        print(f"{len(missing)} archives without an index ({total_mb:.0f} MB):")
        for path in missing[:10]:
            print(f"  {path.name}")
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")
        print("re-run with --apply to write them")
        return 0

    # Writing under the service uid matters as much here as anywhere: a root-written sidecar
    # is one the rotating service could not replace, and rotation is what keeps it true.
    try:
        assert_not_foreign_root_run(root)
    except MvpRuntimeError as exc:
        print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return 3

    for path in missing:
        kinds = write_archive_index(path)
        print(f"{path.name}: {len(kinds)} kinds -> {', '.join(sorted(kinds)) or '(none)'}")
    print(f"indexed {len(missing)} archives ({total_mb:.0f} MB read once)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
