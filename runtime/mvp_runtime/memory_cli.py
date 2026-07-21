"""R5 Working-memory maintenance CLI — retention (prune) + status.

Policy §12.4: working memory expires; expired candidates are deleted (or moved to memory
review). Retrieval already refuses to serve an expired candidate as context; this CLI performs
the physical deletion and audits it (policy §15). A scheduler (R6) can call ``prune`` on an
interval; an operator can run it by hand.

    python -m runtime.mvp_runtime.memory_cli status
    python -m runtime.mvp_runtime.memory_cli prune --reason "daily retention"

``prune`` is EXECUTE_AND_REPORT: it deletes only already-expired candidates and records a
tamper-evident retention event to the durable ledger. Promoted VALIDATED memory is never touched.
"""

from __future__ import annotations

import argparse
import sys

from . import memory, timeutil
from .cli_common import EXIT_BLOCKED, EXIT_OK, report_block
from .control import ControlStore
from .errors import MvpRuntimeError
from .store import LedgerStore
from .working_memory import WorkingMemoryStore


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="memory_cli", description="Working-memory maintenance (status/prune).")
    parser.add_argument("command", choices=["status", "prune"], help="the maintenance command")
    parser.add_argument("--reason", default="", help="operator reason recorded in the retention event")
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    store: WorkingMemoryStore | None = None,
    ledger: LedgerStore | None = None,
    control_store: ControlStore | None = None,
    now: str | None = None,
) -> int:
    """Run one maintenance command. Returns 0 on success, non-zero on a fail-closed block.
    Stores are injectable for tests; unset ones default to the local per-machine state."""
    args = _parse_args(argv)
    store = store if store is not None else WorkingMemoryStore.default()
    ledger = ledger if ledger is not None else LedgerStore.default()
    stamp = now or timeutil.utc_now_iso()

    try:
        if args.command == "status":
            candidates = store.read_all()
            expired = [e for e in candidates if memory.is_expired(e, stamp)]
            validated = store.read_validated()
            sys.stdout.write(
                f"working-memory candidates: {len(candidates)} "
                f"(expired as of now: {len(expired)}); validated memory: {len(validated)}\n"
            )
            return EXIT_OK

        # Kill-switch binding: prune deletes data (EXECUTE_AND_REPORT), and kill_allows
        # lists only read_only_status/audit_read — a PAUSED/KILLED runtime may report
        # status above but must not delete. Same door rule as the scheduler and R8 write.
        control_store = control_store if control_store is not None else ControlStore.default()
        state = control_store.load()
        if not state.execution_allowed:
            sys.stderr.write(
                f"BLOCKED {state.refusal_reason_code()}: runtime is {state.mode}; "
                "prune deletes data and is refused while not ACTIVE\n"
            )
            return EXIT_BLOCKED

        summary = memory.prune_working_memory(store, ledger, now=stamp, reason=args.reason)
    except MvpRuntimeError as exc:
        return report_block(exc)

    sys.stdout.write(f"pruned {summary['removed_count']} expired working-memory candidate(s)\n")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
