"""Liveness probe for a deployed service — the container healthcheck.

    python -m runtime.mvp_runtime.heartbeat_cli scheduler
    python -m runtime.mvp_runtime.heartbeat_cli operator

Exits 0 when the named loop stamped a heartbeat recently enough for its own cadence,
non-zero when it has gone quiet, its record is unreadable, or it never started one.

Deliberately NOT a console command: ``control.COMMANDS`` is pinned to the governance
policy's emergency-control list by a drift gate, and a liveness probe is neither an
emergency control nor a state change. This reads only, so it answers while the runtime
is PAUSED or KILLED — halted is not unhealthy.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import heartbeat
from .cli_common import EXIT_BLOCKED, EXIT_OK, force_utf8_io

EXIT_STALE = 1


def main(argv: list[str] | None = None, *, root: Path | None = None, now: str | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(
        prog="heartbeat_cli", description="Check whether a deployed service loop is still turning.")
    parser.add_argument("service", choices=[heartbeat.SCHEDULER_SERVICE, heartbeat.OPERATOR_SERVICE])
    args = parser.parse_args(argv)

    report = heartbeat.check_heartbeat(args.service, now=now, root=root)
    line = f"{report['service']}: {report['status']} — {report['detail']}"
    if report["status"] == heartbeat.FRESH:
        sys.stdout.write(line + "\n")
        return EXIT_OK
    sys.stderr.write(line + "\n")
    return EXIT_STALE if report["status"] != heartbeat.UNREADABLE else EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
