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

``--workflow-manager`` (sequence 2, P04) runs the workflow manager loop in this process beside
the door: accepted workflows' READY steps are handed to the same worker socket, results land
in the workflow store, and a loop failure is a door failure and nothing more. OFF by default,
like the worker's ``--revise``: switching it on is a line on this service's compose command —
a deployment decision with a diff, not a behaviour that arrives with a rebuild. With the flag
off this process is byte-for-byte the v2 door it was.
"""

from __future__ import annotations

import os

import argparse
from pathlib import Path

from . import dispatch_bridge, pipeline_worker, socket_door
from .cli_common import force_utf8_io, serve_door_forever
from .approval_store import ApprovalStore
from .control import ControlStore
from .store import LedgerStore
from .task_registry import TaskRegistryStore
from .workflow_manager import DEFAULT_CONCURRENCY, DEFAULT_POLL_SECONDS, WorkflowManager
from .workflow_store import WorkflowStore
from . import schedule_delegation

# P10: the entry-point cutover switch. `closed` on the compose command line (or this variable)
# refuses new single dispatches by name and leaves everything else the door does untouched.
V2_INTAKE_ENV = "MVP_DISPATCH_V2_INTAKE"
from .scheduler import ScheduleStore


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
    parser.add_argument("--workflow-manager", action="store_true",
                        help="P04: run the workflow manager loop in this process (accepted workflows' "
                             "READY steps go to the worker socket; results land in the workflow store). "
                             "Off by default: a deployment decision, made on the compose command line")
    parser.add_argument("--workflow-poll-seconds", type=float, default=DEFAULT_POLL_SECONDS,
                        help=f"how often the manager loop looks for work (default {DEFAULT_POLL_SECONDS}s)")
    parser.add_argument("--workflow-concurrency", type=int, default=DEFAULT_CONCURRENCY,
                        help=f"attempts in flight at once (default {DEFAULT_CONCURRENCY}, the worker's own ceiling)")
    intake_default = os.environ.get(V2_INTAKE_ENV, "open")
    parser.add_argument("--v2-intake", choices=("open", "closed"), default=intake_default,
                        help="P10 cutover: 'closed' refuses NEW single dispatches (V2_INTAKE_CLOSED) while v3 commands, "
                             f"reads and replays keep working (default: ${V2_INTAKE_ENV}, else open)")
    args = parser.parse_args(argv)
    if args.v2_intake not in ("open", "closed"):
        parser.error(f"{V2_INTAKE_ENV} must be 'open' or 'closed', not {args.v2_intake!r}")
    return args


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    args = _parse_args(argv)
    path = Path(args.socket) if args.socket else dispatch_bridge.socket_path()
    worker_socket = (
        Path(args.worker_socket) if args.worker_socket else pipeline_worker.socket_path()
    )

    manager: WorkflowManager | None = None
    workflow_store: WorkflowStore | None = None
    if args.workflow_manager:
        workflow_store = WorkflowStore.default()
        manager = WorkflowManager(
            workflow_store, control_store=ControlStore.default(), worker_socket=worker_socket,
            concurrency=args.workflow_concurrency, poll_seconds=args.workflow_poll_seconds,
            # The worker's rows are how a reply lost between it and this process is recovered
            # (P06): the manager reads them and closes only its own origin.
            registry=TaskRegistryStore.default(),
            # Gated steps (P07): the manager mints their asks into, and spends their grants from,
            # the same approval store the operator decides in. It never writes APPROVED.
            approval_store=ApprovalStore.default(),
        )
        manager.start()

    try:
        return _serve(path, worker_socket, manager, workflow_store, v2_intake=(args.v2_intake == "open"))
    finally:
        if manager is not None:
            manager.stop()


def _serve(path: Path, worker_socket: Path, manager: WorkflowManager | None,
           workflow_store: WorkflowStore | None, *, v2_intake: bool = True) -> int:
    # P09: the schedule store and the policy's delegated scope ride with the v3 surface. The
    # scope is read once at start (None = no clause = every change refused); a policy change is
    # a redeploy, which is how every other policy-read service here behaves.
    schedule_store = ScheduleStore.default() if workflow_store is not None else None
    delegation = schedule_delegation.load_delegation() if workflow_store is not None else None
    return serve_door_forever(
        label="DISPATCH_BRIDGE", path=path,
        open_server=lambda: dispatch_bridge.open_door(
            path,
            control_store=ControlStore.default(),
            ledger=LedgerStore.default(),
            worker_socket=worker_socket,
            # Read-only here: the worker writes the entries; this door reads one back to
            # answer an idempotent replay with the run's status and result (door API v2).
            registry=TaskRegistryStore.default(),
            # The same store the manager loop writes: v3 commands are served from it, and only
            # when the loop runs here — otherwise they are refused by name (A18).
            workflow_store=workflow_store,
            schedule_store=schedule_store, delegation=delegation, v2_intake=v2_intake,
            manager_enabled=manager is not None,
        ),
        banner=lambda server: (
            f"kinds={sorted(dispatch_bridge._ALLOWED_KINDS)}, "
            f"forwarding to {worker_socket}, "
            f"workflow-manager={'on' if manager is not None else 'off'}, "
            f"{socket_door.describe_admission(server)}"
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
