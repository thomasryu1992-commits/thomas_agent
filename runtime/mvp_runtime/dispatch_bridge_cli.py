"""Run the dispatch door (``dispatch_bridge``) as a long-lived listener.

    python -m runtime.mvp_runtime.dispatch_bridge_cli
    python -m runtime.mvp_runtime.dispatch_bridge_cli --socket /path/to/dispatch.sock

Its own service in the deployment, and for the same reason the other doors are: it carries a
different authority (it *starts* work; the switch door's `disable` stops it), and a door that
shares a process with what it drives fails together with it. Since the plane separation
(``docs/proposals/CREDENTIAL_PLANE_SEPARATION_V0.1.md``) it does not run the pipeline either:
it validates and forwards to the ``pipeline_worker`` service over an internal socket, so this
process — the one that parses the assistant's frames — needs no provider key, no search key,
and no Naver variable, and its compose environment carries none. It has never carried a
venue/order key or ``MVP_LIVE_*``: the kinds it dispatches never touch the money path.

The socket is created on start and removed on exit. See ``dispatch_bridge`` for why the kind
set is four and why none of them can reach a live order; see ``pipeline_worker`` for the
engine side.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import dispatch_bridge, pipeline_worker, socket_door
from .cli_common import force_utf8_io, serve_door_forever
from .control import ControlStore
from .store import LedgerStore


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dispatch_bridge_cli",
        description=(
            "Assistant dispatch door on a unix socket. Validates bounded analysis/research/"
            "translation/content requests (permission P3; no trading kind, no money path) and "
            "forwards them to the pipeline worker."
        ),
    )
    parser.add_argument(
        "--socket", default=None,
        help="socket path (default: MVP_DISPATCH_BRIDGE_SOCKET, else the per-machine state dir)",
    )
    parser.add_argument(
        "--worker-socket", default=None,
        help=(
            "pipeline worker socket to forward to (default: MVP_PIPELINE_WORKER_SOCKET, else "
            "the per-machine state dir)"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    args = _parse_args(argv)
    path = Path(args.socket) if args.socket else dispatch_bridge.socket_path()
    worker_socket = (
        Path(args.worker_socket) if args.worker_socket else pipeline_worker.socket_path()
    )

    return serve_door_forever(
        label="DISPATCH_BRIDGE", path=path,
        open_server=lambda: dispatch_bridge.open_door(
            path,
            control_store=ControlStore.default(),
            ledger=LedgerStore.default(),
            worker_socket=worker_socket,
        ),
        banner=lambda server: (
            f"kinds={sorted(dispatch_bridge._ALLOWED_KINDS)}, "
            f"forwarding to {worker_socket}, "
            f"{socket_door.describe_admission(server)}"
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
