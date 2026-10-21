#!/usr/bin/env python3
"""Copy the crypto lane's research records into a snapshot the ``claude`` account can read.

``docs/proposals/RESEARCH_SANDBOX_V0.1.md`` §4.1 (Thomas 2026-10-20). The research sandbox computes
without secrets and without the live state; this is the one door its data comes through.

- **What leaves:** only the files named in :data:`ALLOWED`, from the top of the crypto state
  directory. Nothing else is copied, whatever else is there: accounts, live trading, budgets,
  execution stage, approvals, marks, locks, the testnet ``venues/`` tree, every directory outside
  ``crypto/`` (holdings among them), and any secret. It is an allowlist, never a denylist, so a new
  store stays out until a PR names it.
- **Where:** ``<dest>/<UTC id>/``, root:``claude``, files 0640 and directories 0750. A snapshot
  is written under a temporary name and renamed into place, so a reader never sees half of one.
  ``<dest>/current`` points at the newest. The newest ``--keep`` are kept.
- **What it reads:** regular files only. A symlink is not followed and is recorded as refused,
  so nothing outside the state directory can be pulled in by a link. The source is never written,
  and no lock is taken: a line the scheduler is appending may arrive cut in half, and the reader
  skips it.
- **Manifest:** ``manifest.json`` records per file its size, sha256 and source mtime, plus the
  snapshot id and source path. The sandbox records the id with every run.

Standard library only, because root's cron runs it with the host ``python3`` from a pinned copy,
not from the primary checkout (which can be far behind ``origin/main``). Install, as root::

    install -m 0755 -o root -g root scripts/ops/export_research_lake.py /usr/local/lib/thomas/export_research_lake.py
    17 * * * * /usr/bin/python3 /usr/local/lib/thomas/export_research_lake.py >> /var/log/thomas-research-lake.log 2>&1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

DEFAULT_SRC = "/root/thomas_agent/.runtime_governance_state/crypto"
DEFAULT_DEST = "/var/lib/thomas-measure/research-lake"
DEFAULT_GROUP = "claude"
DEFAULT_KEEP = 3
DEFAULT_MAX_BYTES = 2 * 1024 ** 3

# The research records (§4.1). Each name is the filename constant of the runtime module that owns it;
# `tests/test_ops_export_research_lake.py` holds the two lists to those constants.
ALLOWED: tuple[str, ...] = (
    "strategy_candidates.jsonl",
    "forward_cohorts.jsonl",
    "forward_cohort_outcomes.jsonl",
    "forward_cohort_positions.json",
    "forward_cohort_nulls.jsonl",
    "forward_cohort_null_outcomes.jsonl",
    "forward_cohort_null_positions.json",
    "forward_trial_outcomes.jsonl",
    "forward_trial_positions.json",
    "forward_trial_null_outcomes.jsonl",
    "forward_trial_null_positions.json",
    "hypothesis_trial_closes.jsonl",
    "forward_outcomes.jsonl",
    "forward_positions.json",
    "paper_outcomes.jsonl",
    "paper_position.json",
    "counterfactual_outcomes.jsonl",
    "counterfactual_positions.json",
    "open_interest_1h.jsonl",
    "positioning_history.jsonl",
    "orderbook_depth.jsonl",
)
SNAPSHOT_ID = re.compile(r"^\d{8}T\d{6}Z$")
MANIFEST = "manifest.json"
CURRENT = "current"
STATUS = "last_status.json"


def snapshot_id(now: float) -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def plan(src: Path) -> tuple[list[Path], dict[str, str]]:
    """The files that would leave, and why each allowed name that will not."""
    files: list[Path] = []
    skipped: dict[str, str] = {}
    for name in ALLOWED:
        path = src / name
        if path.is_symlink():
            skipped[name] = "symlink, not followed"
        elif not path.exists():
            skipped[name] = "absent"
        elif not path.is_file():
            skipped[name] = "not a regular file"
        else:
            files.append(path)
    return files, skipped


def _chown(path: Path, gid: int | None) -> None:
    if gid is not None:
        os.chown(path, 0, gid)


def export(src: Path, dest: Path, *, now: float, keep: int = DEFAULT_KEEP,
           max_bytes: int = DEFAULT_MAX_BYTES, group: str | None = DEFAULT_GROUP) -> dict:
    """Write one snapshot and return its status. Raises ValueError on a refused configuration."""
    src, dest = src.resolve(), Path(os.path.abspath(dest))
    if src.name != "crypto" or not src.is_dir():
        raise ValueError(f"source must be the crypto state directory, got {src}")
    if dest == src or src in dest.parents or dest in src.parents:
        raise ValueError("destination and source must not contain each other")
    if group:
        import grp  # Unix only; imported here so the module (and its tests) load anywhere
    gid = grp.getgrnam(group).gr_gid if group else None
    files, skipped = plan(src)
    total = sum(p.stat().st_size for p in files)
    sid = snapshot_id(now)
    status = {"snapshot": sid, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
              "files": len(files), "bytes": total, "skipped": skipped}
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o750)
    _chown(dest, gid)
    if total > max_bytes:
        status["result"] = f"skipped: {total} bytes over the {max_bytes} cap"
        _write_status(dest, status, gid)
        return status
    final = dest / sid
    if final.exists():
        status["result"] = "skipped: snapshot id already exists"
        _write_status(dest, status, gid)
        return status
    tmp = dest / f".{sid}.tmp-{os.getpid()}"
    tmp.mkdir(mode=0o750)
    try:
        manifest = {"snapshot": sid, "source": str(src), "exported_at": status["at"], "files": {},
                    "skipped": skipped}
        for path in files:
            target = tmp / path.name
            shutil.copyfile(path, target, follow_symlinks=False)
            os.chmod(target, 0o640)
            _chown(target, gid)
            manifest["files"][path.name] = {"bytes": target.stat().st_size, "sha256": _sha256(target),
                                            "source_mtime": int(path.stat().st_mtime)}
        (tmp / MANIFEST).write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
        os.chmod(tmp / MANIFEST, 0o640)
        _chown(tmp / MANIFEST, gid)
        os.chmod(tmp, 0o750)
        _chown(tmp, gid)
        os.rename(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    link = dest / f".{CURRENT}.tmp-{os.getpid()}"
    if link.is_symlink() or link.exists():
        link.unlink()
    os.symlink(sid, link)
    os.replace(link, dest / CURRENT)
    status["pruned"] = prune(dest, keep=keep)
    status["result"] = "ok"
    _write_status(dest, status, gid)
    return status


def prune(dest: Path, *, keep: int) -> list[str]:
    """Remove all but the newest ``keep`` snapshots. Only names shaped like a snapshot id are touched."""
    snapshots = sorted(p.name for p in dest.iterdir() if p.is_dir() and not p.is_symlink()
                       and SNAPSHOT_ID.match(p.name))
    removed = snapshots[:-keep] if keep > 0 else snapshots
    for name in removed:
        shutil.rmtree(dest / name)
    return removed


def _write_status(dest: Path, status: dict, gid: int | None) -> None:
    path = dest / STATUS
    tmp = dest / f".{STATUS}.tmp-{os.getpid()}"
    tmp.write_text(json.dumps(status, indent=1, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o640)
    _chown(tmp, gid)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="export_research_lake", description=__doc__.splitlines()[0])
    parser.add_argument("--src", default=DEFAULT_SRC)
    parser.add_argument("--dest", default=DEFAULT_DEST)
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--group", default=DEFAULT_GROUP,
                        help="group that may read the snapshot (empty: leave ownership as is)")
    parser.add_argument("--list", action="store_true", help="print what would leave and exit")
    args = parser.parse_args(argv)
    src = Path(args.src)
    if args.list:
        files, skipped = plan(src.resolve())
        for path in files:
            print(f"  copy  {path.name}  {path.stat().st_size} bytes")
        for name, why in skipped.items():
            print(f"  skip  {name}  ({why})")
        return 0
    if args.keep < 1:
        print("--keep must be at least 1", file=sys.stderr)
        return 2
    try:
        status = export(src, Path(args.dest), now=time.time(), keep=args.keep,
                        max_bytes=args.max_bytes, group=args.group or None)
    except (ValueError, KeyError, OSError) as exc:
        print(f"research lake: refused ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 1
    print(f"research lake {status['snapshot']}: {status['result']}, {status['files']} files, "
          f"{status['bytes']} bytes")
    return 0 if status["result"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
