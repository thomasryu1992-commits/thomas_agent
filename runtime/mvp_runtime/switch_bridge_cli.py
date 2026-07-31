"""Run the switch door (``switch_bridge``) as a long-lived listener.

    python -m runtime.mvp_runtime.switch_bridge_cli
    python -m runtime.mvp_runtime.switch_bridge_cli --socket /path/to/switch.sock

Its own service in the deployment, for the reason the halt door had before it absorbed it: the
scheduler is what a stop is meant to stop, so a door living inside it stops answering exactly
when it is needed. It carries no provider key, no venue key and no ``MVP_LIVE_*`` — flipping
the control switch reads and writes control state, and reaches no venue and no model.

``enable`` needs the approval store as well as the control store: the ask is written there and
Thomas's answer is read back from there. See ``switch_bridge`` for why starting is the only
verb that needs a signature.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import switch_bridge
from .approval_store import ApprovalStore
from .cli_common import EXIT_OK, force_utf8_io, report_block
from .control import ControlStore
from .errors import MvpRuntimeError
from .store import LedgerStore


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="switch_bridge_cli",
        description=(
            "Assistant trading-switch door on a unix socket. Stopping is free; starting "
            "requires a single-use approval Thomas grants on the control channel."
        ),
    )
    parser.add_argument(
        "--socket", default=None,
        help="socket path (default: MVP_SWITCH_BRIDGE_SOCKET, else the per-machine state dir)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    args = _parse_args(argv)
    path = Path(args.socket) if args.socket else switch_bridge.socket_path()

    try:
        server = switch_bridge.open_door(
            path,
            control_store=ControlStore.default(),
            ledger=LedgerStore.default(),
            approval_store=ApprovalStore.default(),
        )
    except MvpRuntimeError as exc:
        return report_block(exc)
    except OSError as exc:
        sys.stderr.write(f"SWITCH_BRIDGE: cannot listen on {path}: {exc}\n")
        return 1

    sys.stderr.write(
        f"SWITCH_BRIDGE: listening on {path} "
        f"(verbs={sorted(switch_bridge._ALLOWED_COMMANDS)}, "
        f"domains={sorted(switch_bridge._ALLOWED_DOMAINS)}, "
        f"actor={switch_bridge.ASSISTANT_ACTOR})\n"
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
