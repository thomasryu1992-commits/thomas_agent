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
from pathlib import Path

from . import read_bridge, socket_door
from .cli_common import force_utf8_io, serve_door_forever
from .control import ControlStore
from .approval_store import ApprovalStore
from .scheduler import ScheduleStore
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
    return serve_door_forever(
        label="READ_BRIDGE", path=path,
        open_server=lambda: read_bridge.open_door(
            path,
            control_store=ControlStore.default(),
            ledger=LedgerStore.default(),
            registry=TaskRegistryStore.default(),
            working_memory=WorkingMemoryStore.default(),
            schedules=ScheduleStore.default(),
            approval_store=ApprovalStore.default(),
        ),
        banner=lambda server: (
            f"reads={sorted(read_bridge._READS)}, {socket_door.describe_admission(server)}"
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
