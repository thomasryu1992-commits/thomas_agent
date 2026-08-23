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
from .pipeline import AUTO_VALIDATION
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
    # The two assurance policies the operator loop has always had and this one did not. Same
    # spelling as `operator_cli`'s so an operator does not have to learn two, and OFF by
    # default here as there: switching one on is a line on this service's compose command,
    # which makes it a deployment decision with a diff rather than a behaviour that arrived
    # with a rebuild.
    parser.add_argument("--independent-validation", nargs="?", const="always",
                        choices=("always", "auto"), default=None,
                        help="R7: add the independent validation agent to forwarded work (a "
                             "second reviewer; the stricter verdict decides delivery). Bare "
                             "flag or 'always' = every request; 'auto' = ORANGE/RED-risk only")
    parser.add_argument("--revise", action="store_true",
                        help="M3: on a REVISE verdict, allow ONE bounded regeneration before "
                             "the run is finalised (hard cap 1). Off by default: it spends a "
                             "second specialist call on the tasks that need it most")
    return parser.parse_args(argv)


def _validation_policy(arg: str | None) -> bool | str:
    """Map the CLI argument onto ``run_task``'s ``independent_validation`` value.

    A local four-liner rather than a shared helper. `operator_cli` has the same function and
    a test pins it there BY NAME, and the obvious common home — `cli_common` — is imported by
    fifteen CLIs including `heartbeat_cli`, which the container healthcheck runs every sixty
    seconds. Hoisting `AUTO_VALIDATION`'s import into that module would put the pipeline import
    on the health probe's path. Two four-line copies is the cheaper trade, and the drift they
    could suffer is bounded by both being pinned.
    """
    if arg is None:
        return False
    return True if arg == "always" else AUTO_VALIDATION


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
            independent_validation=_validation_policy(args.independent_validation),
            revise=bool(args.revise),
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
        f"validation={args.independent_validation or 'automatic-only'}, "
        f"revise={'on' if args.revise else 'off'}, "
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
