"""R4 Local operator emergency console.

A direct, host-level control that works **without** Telegram — the emergency stop an operator
reaches for over SSH when the channel is down or a run must be halted now. It reads and writes
the same local control state the operator loop enforces
(`.runtime_governance_state/operator_control_state.json`) and records each action to the durable
ledger, so a `kill` issued here immediately blocks the loop's next task.

    python -m runtime.mvp_runtime.console_cli status
    python -m runtime.mvp_runtime.console_cli pause  --reason "investigating a bad run"
    python -m runtime.mvp_runtime.console_cli kill   --reason "halt now"
    python -m runtime.mvp_runtime.console_cli resume --reason "cleared"
    python -m runtime.mvp_runtime.console_cli stop <task_id>

The actor is recorded as ``local_console``: physical/SSH access to the host is the authentication
for the local console, which is why ``resume`` is available here (the governance requires the
authenticated operator to resume; it never lets the agent/runtime self-clear a kill).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import control
from .cli_common import EXIT_BLOCKED, EXIT_OK, report_block
from .control import ControlStore
from .errors import MvpRuntimeError
from .store import LedgerStore

LOCAL_ACTOR = "local_console"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="console_cli",
        description=(
            "Local operator emergency console. status/audit/recovery are read-only and keep "
            "working while PAUSED or KILLED; pause/kill/resume/stop change the control state."
        ),
    )
    parser.add_argument("command", choices=sorted(control.COMMANDS), help="the console command to apply")
    parser.add_argument("task_id", nargs="?", default=None,
                        help="task id (required for 'stop'); event count (optional for 'audit')")
    parser.add_argument("--reason", default="", help="operator reason recorded in the control event")
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    control_store: ControlStore | None = None,
    ledger: LedgerStore | None = None,
    now: str | None = None,
) -> int:
    """Apply one console command. Returns 0 on success, non-zero on a fail-closed block.
    Stores are injectable for tests; unset ones default to the local per-machine state."""
    args = _parse_args(argv)
    control_store = control_store if control_store is not None else ControlStore.default()
    ledger = ledger if ledger is not None else LedgerStore.default()

    try:
        outcome = control.apply_command(
            control_store, args.command, actor=LOCAL_ACTOR, now=now, reason=args.reason,
            arg=args.task_id, ledger=ledger,
        )
    except MvpRuntimeError as exc:
        return report_block(exc)

    sys.stdout.write(outcome["reply"] + "\n")
    sys.stderr.write(f"CONTROL: mode={outcome['mode']} action={outcome['action']} changed={outcome['changed']}\n")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
