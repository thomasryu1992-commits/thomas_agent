"""Claude's client for its own read door (Option C C6b prerequisite 1).

    python3 scripts/claude_read.py schedules
    python3 scripts/claude_read.py scheduler_events [ARGUMENT]

One request to the ``claude-read-bridge`` service's socket, the reply printed as the door sent it. The door
serves the ``claude`` profile only: two reads, projected replies, every answer logged on its side
(``docs/proposals/HOST_PRIVILEGE_SEPARATION_C6B_READ_DOOR_V0.1.md``). Holdings and readiness are not among
them; after C6b Thomas relays those (RD-1).

Runs as the ``claude`` account under the system ``python3`` from its own checkout. It imports only
``socket_door`` and ``errors`` from the repository, both standard library underneath, so no venv is needed.

Exit 0 on a reply the door marked ok, 2 on a refusal or when no door answered, 3 on a read this client does
not send. The read names are checked here first so a typo never reaches the door's log as a refusal; the door
checks them again regardless.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import socket_door  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402

SOCKET = Path("/var/lib/thomas-claude-door/sock/read.sock")
# The `claude` profile's reads (read_bridge.PROFILES). Spelled out rather than imported: read_bridge pulls in
# the stores and schema validation, which the system python3 this runs under does not carry. A test holds
# the two equal.
READS = ("schedules", "scheduler_events")
CLIENT_ID = "claude"
DEADLINE_S = 10.0
EXIT_OK, EXIT_BLOCKED, EXIT_USAGE = 0, 2, 3


def main(argv: list[str] | None = None, *, socket_path: Path = SOCKET) -> int:
    parser = argparse.ArgumentParser(prog="claude_read", description=__doc__.splitlines()[0])
    parser.add_argument("read", help=f"one of {', '.join(READS)}")
    parser.add_argument("argument", nargs="?", default=None, help="passed to the read as its argument")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    if args.read not in READS:
        print(f"claude_read: {args.read!r} is not a read this door serves; it carries {list(READS)}", file=sys.stderr)
        return EXIT_USAGE
    request: dict[str, object] = {"command": args.read, "client_id": CLIENT_ID}
    if args.argument is not None:
        request["argument"] = args.argument
    try:
        reply = socket_door.call_door(socket_path, request, deadline_seconds=DEADLINE_S)
    except MvpRuntimeError as exc:
        print(json.dumps({"ok": False, "reason_code": exc.reason_code, "reason": exc.reason}, ensure_ascii=False))
        return EXIT_BLOCKED
    print(json.dumps(reply, ensure_ascii=False, indent=1))
    return EXIT_OK if isinstance(reply, dict) and reply.get("ok") is True else EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
