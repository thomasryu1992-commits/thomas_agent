#!/usr/bin/env python3
"""Operator tool: the strategy funnel (``crypto/strategy_funnel.py``). Reads only.

Where the candidate store's lineages stop at the promotion door, charged to the first axis of
``promotable_backlog``'s own chain, and how far the forward cohort's members have got. Both are split
by timeframe, strategy family and direction. Nothing is written and nothing is decided; the door
and the board are unchanged. ``--json`` prints the structure instead of the table::

    docker exec thomas-scheduler python -m scripts.strategy_funnel
    docker exec thomas-scheduler python -m scripts.strategy_funnel --json

A damaged candidate, pool or cohort store refuses the run (``EXIT_BLOCKED``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK  # noqa: E402
from runtime.mvp_runtime.crypto import strategy_funnel  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="strategy_funnel", description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print the structure instead of the table")
    args = parser.parse_args(argv)
    try:
        funnel = strategy_funnel.strategy_funnel(root)
    except MvpRuntimeError as exc:
        print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return EXIT_BLOCKED
    if args.json:
        print(json.dumps(funnel, indent=1, sort_keys=True))
    else:
        print("\n".join(strategy_funnel.render_text(funnel)))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
