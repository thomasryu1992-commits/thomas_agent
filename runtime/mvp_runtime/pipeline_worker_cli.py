"""Run the pipeline worker (``pipeline_worker``) as a long-lived listener.

    python -m runtime.mvp_runtime.pipeline_worker_cli
    python -m runtime.mvp_runtime.pipeline_worker_cli --socket /path/to/pipeline.sock

The dispatch door's engine, in its own service, so the process that parses the assistant's
frames and the process that holds credentials are never the same one
(``docs/proposals/CREDENTIAL_PLANE_SEPARATION_V0.1.md``). It selects the analysis/validator/
search providers through the Safety-Flag Gate on **every request** and fails closed — a
machine with no model grant runs the deterministic mock pipeline rather than reaching a
network provider by accident. It carries no venue/order key and no ``MVP_LIVE_*``: the kinds
it runs never touch the money path.

The socket is created on start and removed on exit, under ``internal/`` — never ``bridge/``,
and the module refuses a deployment that has not named its permitted peer uids. See
``pipeline_worker`` for both refusals.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import pipeline_worker, socket_door
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
        prog="pipeline_worker_cli",
        description=(
            "Pipeline worker on an internal unix socket. Runs the dispatch door's bounded "
            "analysis/research/translation/content work at permission P3; no trading kind, "
            "no money path, no assistant-facing socket."
        ),
    )
    parser.add_argument(
        "--socket", default=None,
        help="socket path (default: MVP_PIPELINE_WORKER_SOCKET, else the per-machine state dir)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    args = _parse_args(argv)
    path = Path(args.socket) if args.socket else pipeline_worker.socket_path()

    try:
        server = pipeline_worker.open_door(
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
        sys.stderr.write(f"PIPELINE_WORKER: cannot listen on {path}: {exc}\n")
        return 1

    sys.stderr.write(
        f"PIPELINE_WORKER: listening on {path} "
        f"(kinds={sorted(pipeline_worker._ALLOWED_KINDS)}, "
        f"actor={pipeline_worker.ASSISTANT_ACTOR}, "
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
