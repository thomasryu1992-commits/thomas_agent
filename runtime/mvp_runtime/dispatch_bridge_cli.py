"""Run the dispatch door (``dispatch_bridge``) as a long-lived listener.

    python -m runtime.mvp_runtime.dispatch_bridge_cli
    python -m runtime.mvp_runtime.dispatch_bridge_cli --socket /path/to/dispatch.sock

Its own service in the deployment, and for the same reason the other doors are: it carries a
different authority (it *starts* work; the halt door stops it), and a door that shares a
process with what it drives fails together with it. Unlike the halt and read doors it runs the
analysis pipeline, so it selects the analysis/validator/search providers through the
Safety-Flag Gate on **every request** and fails closed — a machine with no model grant runs
the deterministic mock pipeline rather than reaching a network provider by accident. It still
carries no venue/order key and no ``MVP_LIVE_*``: the kinds it dispatches never touch the
money path.

The socket is created on start and removed on exit. See ``dispatch_bridge`` for why the kind
set is four and why none of them can reach a live order.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import dispatch_bridge
from .cli_common import EXIT_OK, force_utf8_io, report_block
from .control import ControlStore
from .errors import MvpRuntimeError
from .programization import ProgramizationStore
from .providers import select_provider, select_validator_provider
from .store import LedgerStore
from .tools import select_search_tool
from .working_memory import WorkingMemoryStore


def _resolve_providers() -> dict[str, Any]:
    """Select the analysis/validator/search providers through the Safety-Flag Gate, fresh per
    request. Each fails closed to its deterministic mock when no local grant authorizes it."""
    return {
        "provider": select_provider(),
        "validator_provider": select_validator_provider(),
        "search_tool": select_search_tool(),
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dispatch_bridge_cli",
        description=(
            "Assistant dispatch door on a unix socket. Runs bounded analysis/research/"
            "translation/content tasks at permission P3; no trading kind, no money path."
        ),
    )
    parser.add_argument(
        "--socket", default=None,
        help="socket path (default: MVP_DISPATCH_BRIDGE_SOCKET, else the per-machine state dir)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    args = _parse_args(argv)
    path = Path(args.socket) if args.socket else dispatch_bridge.socket_path()

    try:
        server = dispatch_bridge.open_door(
            path,
            control_store=ControlStore.default(),
            ledger=LedgerStore.default(),
            working_memory=WorkingMemoryStore.default(),
            programization=ProgramizationStore.default(),
            resolve_providers=_resolve_providers,
        )
    except MvpRuntimeError as exc:
        return report_block(exc)
    except OSError as exc:
        sys.stderr.write(f"DISPATCH_BRIDGE: cannot listen on {path}: {exc}\n")
        return 1

    sys.stderr.write(
        f"DISPATCH_BRIDGE: listening on {path} "
        f"(kinds={sorted(dispatch_bridge._ALLOWED_KINDS)}, actor={dispatch_bridge.ASSISTANT_ACTOR})\n"
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
