"""Programization review CLI — the operator's half of the repetition counter.

The counter (pipeline seam) raises the review trigger; this CLI is how Thomas acts on it
(Programization Review Policy; explicit Thomas decision 2026-07-22):

    python -m runtime.mvp_runtime.programization_cli status
    python -m runtime.mvp_runtime.programization_cli review <pattern_id> --by thomas --reason "..."
    python -m runtime.mvp_runtime.programization_cli candidate <pattern_id> --input review.yaml --by thomas --reason "..."
    python -m runtime.mvp_runtime.programization_cli close <pattern_id> --by thomas --reason "..."

``review`` moves a TRIGGERED pattern UNDER_REVIEW; ``candidate`` drafts the
``programization_candidate.v0.1`` the review produced (input file supplies the §5 substance:
``deterministic_slice``, ``agent_retained_responsibilities``, ``defined_exceptions``,
``rollback_procedure_ref``, optional metrics); ``close`` ends the review. Transitions are
forward-only and every mutation is an explicit operator action (identity + reason) recorded
as a tamper-evident event on the programization ledger stream.

A candidate grants **nothing**: ``activation_eligibility`` stays
``candidate_only_pending_program_registry_and_permission_policy`` and
``permission_expansion`` stays false. Program registration/activation remain
APPROVAL_REQUIRED and are not reachable from this CLI.

Kill-switch bound: ``status`` answers while PAUSED/KILLED (read-only), the mutating
commands are refused — the same door rule as memory prune and the R8 write.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from . import timeutil
from .cli_common import EXIT_BLOCKED, EXIT_OK, force_utf8_io, report_block
from .control import ControlStore
from .errors import MvpRuntimeError, ProgramizationBlocked
from .programization import (
    ProgramizationStore,
    build_review_event,
    create_program_candidate,
    transition_review,
)
from .store import LedgerStore

_TRANSITION_TARGET = {"review": "UNDER_REVIEW", "close": "CLOSED"}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="programization_cli", description="Programization review handling (status/review/candidate/close).")
    parser.add_argument("command", choices=["status", "review", "candidate", "close"])
    parser.add_argument("pattern_id", nargs="?", default=None, help="the pattern to act on")
    parser.add_argument("--by", default="", help="operator identity recorded on the review event")
    parser.add_argument("--reason", default="", help="operator reason recorded on the review event")
    parser.add_argument("--input", default=None, help="candidate only: YAML/JSON file with the review substance")
    return parser.parse_args(argv)


def _load_review_input(path_str: str | None) -> dict:
    if not path_str:
        raise ProgramizationBlocked("CANDIDATE_INPUT_INVALID", "candidate requires --input FILE")
    path = Path(path_str)
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProgramizationBlocked("CANDIDATE_INPUT_INVALID", f"cannot read review input: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ProgramizationBlocked("CANDIDATE_INPUT_INVALID", "review input must be a mapping")
    return loaded


def main(
    argv: list[str] | None = None,
    *,
    store: ProgramizationStore | None = None,
    ledger: LedgerStore | None = None,
    control_store: ControlStore | None = None,
    now: str | None = None,
) -> int:
    """Run one review command. Returns 0 on success, non-zero on a fail-closed block.
    Stores are injectable for tests; unset ones default to the local per-machine state."""
    force_utf8_io()
    args = _parse_args(argv)
    store = store if store is not None else ProgramizationStore.default()
    ledger = ledger if ledger is not None else LedgerStore.default()
    stamp = now or timeutil.utc_now_iso()

    try:
        if args.command == "status":
            patterns = store.latest_patterns()
            candidates = store.read_candidates()
            if not patterns:
                sys.stdout.write("no programization patterns\n")
            for pattern_id, p in sorted(patterns.items()):
                sys.stdout.write(
                    f"{pattern_id}  {p.get('review_status'):<13} "
                    f"valid={p.get('valid_repetition_count')}/{p.get('review_trigger_count')}  "
                    f"updated={p.get('last_updated_at_utc')}\n"
                )
            sys.stdout.write(f"candidates: {len(candidates)}\n")
            return EXIT_OK

        # Mutating commands change governed review state: same kill-switch door rule as
        # memory prune — a PAUSED/KILLED runtime may report status but must not mutate.
        control_store = control_store if control_store is not None else ControlStore.default()
        state = control_store.load()
        if not state.execution_allowed:
            sys.stderr.write(
                f"BLOCKED {state.refusal_reason_code()}: runtime is {state.mode}; "
                "review actions mutate governed state and are refused while not ACTIVE\n"
            )
            return EXIT_BLOCKED
        if not args.pattern_id:
            raise ProgramizationBlocked("PATTERN_NOT_FOUND", f"{args.command} requires a pattern_id")

        if args.command in _TRANSITION_TARGET:
            before = store.latest_patterns().get(args.pattern_id, {})
            pattern = transition_review(
                store, args.pattern_id, _TRANSITION_TARGET[args.command],
                reviewed_by=args.by, reason=args.reason, now=stamp,
            )
            event = build_review_event(
                pattern, action=f"review_{args.command}", from_status=str(before.get("review_status")),
                reviewed_by=args.by.strip(), reason=args.reason.strip(), now=stamp,
            )
            ledger.append_programization_event(event)
            sys.stdout.write(f"{args.pattern_id}: {before.get('review_status')} -> {pattern['review_status']}\n")
            return EXIT_OK

        # candidate: draft the review's outcome record. The pattern stays UNDER_REVIEW —
        # closing the review is its own explicit decision.
        review_input = _load_review_input(args.input)
        candidate = create_program_candidate(
            store, args.pattern_id, review_input,
            created_by=args.by, reason=args.reason, now=stamp,
        )
        pattern = store.latest_patterns()[args.pattern_id]
        event = build_review_event(
            pattern, action="candidate_drafted", from_status="UNDER_REVIEW",
            reviewed_by=args.by.strip(), reason=args.reason.strip(), now=stamp,
            candidate_id=candidate["candidate_id"],
        )
        ledger.append_programization_event(event)
        sys.stdout.write(
            f"drafted candidate {candidate['candidate_id']} for {args.pattern_id} "
            "(DRAFT; grants nothing — registry/activation stay APPROVAL_REQUIRED)\n"
        )
        return EXIT_OK
    except MvpRuntimeError as exc:
        return report_block(exc)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
