"""R5 memory maintenance CLI — retention (prune) + status, and the §12.6 Core Candidate door.

Policy §12.4: working memory expires; expired candidates are deleted (or moved to memory
review). Retrieval already refuses to serve an expired candidate as context; this CLI performs
the physical deletion and audits it (policy §15). A scheduler (R6) can call ``prune`` on an
interval; an operator can run it by hand.

    python -m runtime.mvp_runtime.memory_cli status
    python -m runtime.mvp_runtime.memory_cli prune --reason "daily retention"

``prune`` is EXECUTE_AND_REPORT: it deletes only already-expired candidates and records a
tamper-evident retention event to the durable ledger. Promoted VALIDATED memory is never touched.

The ``core-*`` verbs are the operator door to the memory ladder's fourth rung (policy §12.6,
architecture §8.8): propose that a VALIDATED memory entry implies a change to Thomas Core, then
accept or reject that proposal. This door **only records proposals and decisions**. It cannot
change Thomas Core — that is the Core release lifecycle, and `runtime_effect.core_activation_allowed`
is false.

    python -m runtime.mvp_runtime.memory_cli core-list
    python -m runtime.mvp_runtime.memory_cli core-propose --validated-id valmem-... \
        --type risk_preference_change --rationale "..." --by thomas
    python -m runtime.mvp_runtime.memory_cli core-accept --candidate-id corecand-... \
        --by thomas --reason "carry into the next Core release"
"""

from __future__ import annotations

import argparse
import sys

from . import memory, timeutil, working_memory
from .cli_common import EXIT_BLOCKED, EXIT_OK, force_utf8_io, report_block
from .control import ControlStore
from .errors import MvpRuntimeError
from .store import LedgerStore
from .working_memory import WorkingMemoryStore


COMMANDS = ("status", "prune", "core-list", "core-propose", "core-accept", "core-reject")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory_cli",
        description="Memory maintenance (status/prune) and Core Candidate review (policy §12.6).",
    )
    parser.add_argument("command", choices=list(COMMANDS), help="the maintenance command")
    parser.add_argument("--reason", default="", help="operator reason recorded in the event")
    parser.add_argument("--validated-id", default="", help="core-propose: source VALIDATED memory id")
    parser.add_argument("--type", dest="candidate_type", default="",
                        help=f"core-propose: one of {', '.join(memory.CORE_CANDIDATE_TYPES)}")
    parser.add_argument("--rationale", default="",
                        help="core-propose: why this VALIDATED knowledge implies a Core change")
    parser.add_argument("--by", dest="operator", default="", help="operator identity recorded on the record")
    parser.add_argument("--candidate-id", default="", help="core-accept/core-reject: the Core Candidate id")
    return parser.parse_args(argv)


def _format_core_listing(live: list[dict]) -> str:
    if not live:
        return "no open Core Candidates\n"
    lines = [f"open Core Candidates: {len(live)}"]
    for entry in live:
        lines.append(
            f"  {entry.get('core_candidate_id')}  [{entry.get('candidate_type')}]"
            f"  from {entry.get('source_validated_memory_id')}"
            f"  by {entry.get('proposed_by')}\n"
            f"      {entry.get('content', '')}\n"
            f"      rationale: {entry.get('rationale', '')}"
        )
    lines.append("ACCEPTED records a decision only — it changes no Core file "
                 "(runtime_effect.core_activation_allowed is false).")
    return "\n".join(lines) + "\n"


def _core_propose(args, store: WorkingMemoryStore, ledger: LedgerStore, stamp: str) -> int:
    source = working_memory.find_validated(store, args.validated_id)
    if source is None:
        sys.stderr.write(
            f"BLOCKED VALIDATED_MEMORY_NOT_FOUND: no VALIDATED memory entry {args.validated_id!r}; "
            "a Core Candidate may only be proposed from the rung below it\n"
        )
        return EXIT_BLOCKED
    candidate = memory.build_core_candidate(
        source, candidate_type=args.candidate_type, rationale=args.rationale,
        proposed_by=args.operator, now=stamp,
    )
    store.append_core_candidates([candidate])
    ledger.append_memory_event(memory.build_core_candidate_event(
        candidate, action=memory.CORE_CANDIDATE_PROPOSE_ACTION, now=stamp, operator_id=args.operator.strip(),
    ))
    sys.stdout.write(
        f"proposed Core Candidate {candidate['core_candidate_id']} "
        f"[{candidate['candidate_type']}] from validated_memory:{candidate['source_validated_memory_id']}\n"
        "This is a proposal. It grants nothing and applies nothing to Thomas Core.\n"
    )
    return EXIT_OK


def _core_decide(args, store: WorkingMemoryStore, ledger: LedgerStore, stamp: str, decision: str) -> int:
    candidate = working_memory.find_core_candidate(store, args.candidate_id)
    if candidate is None:
        sys.stderr.write(
            f"BLOCKED CORE_CANDIDATE_NOT_FOUND: no open Core Candidate {args.candidate_id!r} "
            "(unknown id, or it was already decided — decisions are terminal)\n"
        )
        return EXIT_BLOCKED
    decided = memory.decide_core_candidate(
        candidate, decision=decision, decided_by=args.operator, reason=args.reason, now=stamp,
    )
    store.append_core_candidates([decided])
    ledger.append_memory_event(memory.build_core_candidate_event(
        decided, action=memory.CORE_CANDIDATE_DECIDE_ACTION, now=stamp, operator_id=args.operator.strip(),
    ))
    sys.stdout.write(f"Core Candidate {decided['core_candidate_id']} -> {decision}\n")
    if decision == memory.CORE_CANDIDATE_ACCEPTED:
        sys.stdout.write(
            "ACCEPTED is a review milestone: no Core file changed. Carrying it into Thomas Core "
            "is the separate Core release lifecycle (build -> approve -> activate).\n"
        )
    return EXIT_OK


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
    force_utf8_io()
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

        if args.command == "core-list":
            sys.stdout.write(_format_core_listing(working_memory.live_core_candidates(store)))
            return EXIT_OK

        # Kill-switch binding: every command below this line writes durable state, and
        # kill_allows lists only read_only_status/audit_read — a PAUSED/KILLED runtime may
        # report status and list proposals above, but must not delete or record. Same door rule
        # as the scheduler, the R8 write and R10 consumption. The check sits before the command
        # fan-out on purpose: a later verb added below inherits the gate instead of having to
        # remember it.
        control_store = control_store if control_store is not None else ControlStore.default()
        state = control_store.load()
        if not state.execution_allowed:
            sys.stderr.write(
                f"BLOCKED {state.refusal_reason_code()}: runtime is {state.mode}; "
                f"'{args.command}' writes durable state and is refused while not ACTIVE\n"
            )
            return EXIT_BLOCKED

        if args.command == "core-propose":
            return _core_propose(args, store, ledger, stamp)
        if args.command == "core-accept":
            return _core_decide(args, store, ledger, stamp, memory.CORE_CANDIDATE_ACCEPTED)
        if args.command == "core-reject":
            return _core_decide(args, store, ledger, stamp, memory.CORE_CANDIDATE_REJECTED)

        summary = memory.prune_working_memory(store, ledger, now=stamp, reason=args.reason)
    except MvpRuntimeError as exc:
        return report_block(exc)

    sys.stdout.write(f"pruned {summary['removed_count']} expired working-memory candidate(s)\n")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
