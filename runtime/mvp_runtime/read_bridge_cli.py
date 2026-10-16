"""Run the read-only door (``read_bridge``) as a long-lived listener.

    python -m runtime.mvp_runtime.read_bridge_cli
    python -m runtime.mvp_runtime.read_bridge_cli --socket /path/to/read.sock

Separate from ``switch_bridge_cli`` for the reason the modules are separate: different
authority. It also means a read that wedges cannot wedge the stop path, which is the one
that matters when something is going wrong.

Holds no schedule, runs no pipeline, makes no outbound call, and writes nothing — it waits
on a socket and renders what the consoles render. (One exception, only on a profiled door: it
appends one row per answer to its ``--request-log``.)

``--profile NAME`` opens the door for a narrower reader than the assistant
(why: ``docs/history/2026-10-16-claude-read-door-profile.md``): the profile fixes the reads and
the reply contract, and the door then requires ``--request-log`` and a non-empty
``MVP_BRIDGE_CLIENT_UID`` or refuses to listen. There are no per-protection flags to leave out.
Without ``--profile`` the door is the assistant's, unchanged; ``--request-log`` alone is refused.
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
    parser.add_argument("--profile", default=None,
                        help=f"open the door for a named reader ({', '.join(sorted(read_bridge.PROFILES))}); "
                             f"requires --request-log and {socket_door.CLIENT_UID_ENV}")
    parser.add_argument("--request-log", type=Path, default=None,
                        help="the profiled door's request log: one JSONL row per answer; a row that "
                             "cannot be written withholds the answer")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    args = _parse_args(argv)
    path = Path(args.socket) if args.socket else read_bridge.socket_path()

    def banner(server: object) -> str:
        profile = getattr(server, "profile", None)
        if profile is None:  # the assistant's door keeps its banner byte-for-byte (D-16 P1 reads it)
            return f"reads={sorted(read_bridge._READS)}, {socket_door.describe_admission(server)}"
        return (f"reads={sorted(profile.reads)}, {socket_door.describe_admission(server)}, "
                f"profile={profile.name}, projection={profile.projection}, request_log=on, "
                f"uid_allowlist=required")

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
            profile=args.profile,
            request_log=args.request_log,
        ),
        banner=banner,
    )


if __name__ == "__main__":
    raise SystemExit(main())
