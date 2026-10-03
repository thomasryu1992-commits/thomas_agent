"""Append Binance-archive hourly OI to ``oi_store`` — step 2 of ``CRYPTO_ARCHIVE_BACKFILL_V0.1.md``
(Thomas 2026-10-03, R1: measure and backfill OI now; R3: fetch outside the runtime).

The rows arrive on stdin, made on the host by ``scripts/binance_archive.py export-oi`` from files whose
sha256 matched the archive's own checksum. This script runs INSIDE the container as the service user,
so the store keeps one owner::

    python3 scripts/binance_archive.py export-oi --dest /root/binance_archive \\
      | docker exec -i -u 10001 thomas-scheduler python -m scripts.import_archive_oi            # dry run
    ... | docker exec -i -u 10001 thomas-scheduler python -m scripts.import_archive_oi --confirm

**What it may write.** Only hours ``oi_store`` does not already hold: ``oi_store.append_rows`` drops a
held ``(symbol, hour)`` before the write, so where the vendor already answered, its row stands. Each row
is checked first:
- a symbol of the allowed list;
- an hour label (``…T..:00:00Z``) whose hour has closed;
- a finite, positive open interest.

A row that fails is counted and refused, never repaired. ``--confirm`` also appends one line to
``open_interest_1h.archive_imports.jsonl``: when, which input (sha256 of stdin), what was offered,
written and refused, per symbol. That says which rows came from the archive without changing the
store's own row shape.

**What it changes.** Nothing that decides anything. ``oi_store`` feeds no feature (its own module
says so), so this moves the board's OI coverage line and nothing else. Switching the OI feature
source is step 3, at the first cohort verdict, together with S1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE, force_utf8_io
from runtime.mvp_runtime.crypto import oi_store
from runtime.mvp_runtime.errors import MvpRuntimeError
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "DOGEUSDT")
SOURCE = "data.binance.vision futures/um daily metrics (sum_open_interest, on the hour, relabelled -1h)"
IMPORTS_FILENAME = "open_interest_1h.archive_imports.jsonl"
_HOUR_LABEL = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:00:00Z$")

REFUSED_SYMBOL = "symbol_not_allowed"
REFUSED_LABEL = "not_an_hour_label"
REFUSED_OPEN_HOUR = "hour_not_closed"
REFUSED_VALUE = "open_interest_not_positive"
REFUSED_SHAPE = "not_a_row"


def imports_path(root: Path | None = None) -> Path:
    return oi_store.oi_1h_path(root).with_name(IMPORTS_FILENAME)


def check_row(row: Any, *, allowed: frozenset[str], now: str) -> str | None:
    """Why this row may not be written, or None. Pure."""
    if not isinstance(row, Mapping):
        return REFUSED_SHAPE
    symbol = str(row.get("symbol") or "").strip().upper()
    if symbol not in allowed:
        return REFUSED_SYMBOL
    label = str(row.get("timestamp") or "")
    if not _HOUR_LABEL.match(label):
        return REFUSED_LABEL
    # The label is the hour's START: the hour has closed when start + 1h is not after now.
    if timeutil.plus_minutes(label, 60) > now:
        return REFUSED_OPEN_HOUR
    value = row.get("open_interest")
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or value <= 0):
        return REFUSED_VALUE
    return None


def plan(lines: Iterable[str], *, allowed: frozenset[str], now: str) -> dict[str, Any]:
    """Parse and check stdin. ``{"rows": {symbol: [row]}, "refused": {reason: n}, "offered": n,
    "sha256": ...}``."""
    digest = hashlib.sha256()
    rows: dict[str, list[dict[str, Any]]] = {}
    refused: dict[str, int] = {}
    offered = 0
    for line in lines:
        digest.update(line.encode("utf-8"))
        if not line.strip():
            continue
        offered += 1
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        reason = check_row(row, allowed=allowed, now=now)
        if reason is not None:
            refused[reason] = refused.get(reason, 0) + 1
            continue
        symbol = str(row["symbol"]).strip().upper()
        rows.setdefault(symbol, []).append(
            {"timestamp": str(row["timestamp"]), "open_interest": float(row["open_interest"])})
    return {"rows": rows, "refused": refused, "offered": offered, "sha256": "sha256:" + digest.hexdigest()}


def apply(planned: Mapping[str, Any], *, root: Path | None, now: str) -> dict[str, int]:
    """Append each symbol's rows through ``oi_store.append_rows`` (held hours dropped), then record
    the import. Returns rows written per symbol."""
    written = {symbol: oi_store.append_rows(rows, symbol=symbol, root=root)
               for symbol, rows in sorted(planned["rows"].items())}
    record = {
        "imported_at": now, "source": SOURCE, "input_sha256": planned["sha256"],
        "offered": planned["offered"], "refused": planned["refused"],
        "per_symbol": {s: {"checked": len(planned["rows"][s]), "written": n} for s, n in written.items()},
    }
    path = imports_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return written


def main(argv: Sequence[str] | None = None, *, root: Path | None = None, stdin: Any = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--confirm", action="store_true", help="write; without it, a dry run")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_OK
    allowed = frozenset(s.strip().upper() for s in args.symbols.split(",") if s.strip())
    now = timeutil.utc_now_iso()
    planned = plan(stdin if stdin is not None else sys.stdin, allowed=allowed, now=now)
    before = {s: oi_store.coverage(root, symbol=s) for s in sorted(allowed)}
    print(f"offered {planned['offered']} rows; refused {sum(planned['refused'].values())} "
          f"{planned['refused'] or ''}; input {planned['sha256']}")
    for symbol, rows in sorted(planned["rows"].items()):
        cov = before[symbol]
        print(f"  {symbol}: {len(rows)} checked rows {rows[0]['timestamp']} .. {rows[-1]['timestamp']}; "
              f"store now {cov['oldest']} .. {cov['newest']} ({cov['covered_days']}d)")
    if not args.confirm:
        print("DRY RUN — nothing written. Re-run with --confirm.")
        return EXIT_OK
    try:
        assert_not_foreign_root_run(root)
        written = apply(planned, root=root, now=now)
    except MvpRuntimeError as exc:
        print(f"REFUSED: {exc.reason_code}: {exc}", file=sys.stderr)
        return EXIT_BLOCKED
    for symbol, n in written.items():
        cov = oi_store.coverage(root, symbol=symbol)
        print(f"  {symbol}: wrote {n}; store now {cov['oldest']} .. {cov['newest']} "
              f"({cov['covered_days']}d, gap hours {cov['gap_hours']}, eligible {cov['eligible']})")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
