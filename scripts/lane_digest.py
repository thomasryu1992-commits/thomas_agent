#!/usr/bin/env python3
"""Operator tool: what each non-crypto lane did over the last N days (``runtime/mvp_runtime/lane_digest.py``).

Reads the record ledger, archives included, and writes nothing. One block per lane (the role a run
was assigned): runs, delivered versus withheld, which checks withheld them, which model answered,
and the specialist call's latency. ``--json`` prints the structure instead::

    docker exec thomas-scheduler python -m scripts.lane_digest
    docker exec thomas-scheduler python -m scripts.lane_digest --days 30 --json

A damaged ledger refuses the run (``EXIT_BLOCKED``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import lane_digest, timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.store import LedgerStore  # noqa: E402


def main(argv: list[str] | None = None, *, root: Path | None = None, now: str | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lane_digest", description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=7, help="window length in days (default 7)")
    parser.add_argument("--json", action="store_true", help="print the structure instead of the text")
    args = parser.parse_args(argv)
    stamp = now or timeutil.utc_now_iso()
    since = timeutil.plus_minutes(stamp, -abs(args.days) * 24 * 60)
    try:
        digest = lane_digest.lane_digest(LedgerStore.default(root), since=since, until=stamp)
    except MvpRuntimeError as exc:
        print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return EXIT_BLOCKED
    if args.json:
        print(json.dumps({"since": since, "until": stamp, "lanes": digest}, ensure_ascii=False, indent=2))
    else:
        print(lane_digest.render(digest, since=since, until=stamp))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
