"""Run the halt-only door (``halt_bridge``) as a long-lived listener.

    python -m runtime.mvp_runtime.halt_bridge_cli
    python -m runtime.mvp_runtime.halt_bridge_cli --socket /path/to/halt.sock

Its own service in the deployment rather than a thread inside the operator or scheduler
loop, for one reason: those two processes are what a halt is meant to stop, and a door
that lives inside the thing it closes is a door that can stop answering exactly when it is
needed. It holds no schedule, runs no pipeline, and makes no outbound call — it waits on a
socket and applies one of two verbs.

The socket is created on start and removed on exit. See ``halt_bridge`` for why the verb
set is two and why ``resume`` is not one of them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import halt_bridge
from .cli_common import EXIT_OK, force_utf8_io, report_block
from .control import ControlStore
from .errors import MvpRuntimeError
from .store import LedgerStore


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="halt_bridge_cli",
        description=(
            "Halt-only control door on a unix socket. Serves 'kill' and 'pause'; 'resume' is "
            "refused by construction and needs the operator on an authenticated channel."
        ),
    )
    parser.add_argument(
        "--socket", default=None,
        help="socket path (default: MVP_HALT_BRIDGE_SOCKET, else the per-machine state dir)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    args = _parse_args(argv)
    path = Path(args.socket) if args.socket else halt_bridge.socket_path()

    try:
        server = halt_bridge.HaltBridgeServer(
            path, control_store=ControlStore.default(), ledger=LedgerStore.default(),
        )
    except MvpRuntimeError as exc:
        return report_block(exc)
    except OSError as exc:
        sys.stderr.write(f"HALT_BRIDGE: cannot listen on {path}: {exc}\n")
        return 1

    sys.stderr.write(
        f"HALT_BRIDGE: listening on {path} "
        f"(verbs={sorted(halt_bridge._ALLOWED)}, actor={halt_bridge.ASSISTANT_ACTOR})\n"
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
