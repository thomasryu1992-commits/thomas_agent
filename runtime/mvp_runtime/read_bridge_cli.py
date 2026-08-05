"""Run the read-only door (``read_bridge``) as a long-lived listener.

    python -m runtime.mvp_runtime.read_bridge_cli
    python -m runtime.mvp_runtime.read_bridge_cli --socket /path/to/read.sock

Separate from ``switch_bridge_cli`` for the reason the modules are separate: different
authority. It also means a read that wedges cannot wedge the stop path, which is the one
that matters when something is going wrong.

Holds no schedule, runs no pipeline, makes no outbound call, and writes nothing — it waits
on a socket and renders what the consoles render.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import read_bridge, socket_door
from .cli_common import EXIT_OK, force_utf8_io, report_block
from .control import ControlStore
from .errors import MvpRuntimeError
from .store import LedgerStore
from .task_registry import TaskRegistryStore
from .working_memory import WorkingMemoryStore


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="read_bridge_cli",
        description=(
            "Read-only console door on a unix socket. Serves the channel's read verbs by "
            "dispatching to the same appliers, so an answer here cannot disagree with the "
            "console. Nothing it serves can mutate."
        ),
    )
    parser.add_argument(
        "--socket", default=None,
        help="socket path (default: MVP_READ_BRIDGE_SOCKET, else the per-machine state dir)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    args = _parse_args(argv)
    path = Path(args.socket) if args.socket else read_bridge.socket_path()

    try:
        server = read_bridge.open_door(
            path,
            control_store=ControlStore.default(),
            ledger=LedgerStore.default(),
            registry=TaskRegistryStore.default(),
            working_memory=WorkingMemoryStore.default(),
        )
    except MvpRuntimeError as exc:
        return report_block(exc)
    except OSError as exc:
        sys.stderr.write(f"READ_BRIDGE: cannot listen on {path}: {exc}\n")
        return 1

    sys.stderr.write(
        f"READ_BRIDGE: listening on {path} (reads={sorted(read_bridge._READS)}, "
        f"{socket_door.describe_admission(server)})\n"
    )
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        path.unlink(missing_ok=True)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
