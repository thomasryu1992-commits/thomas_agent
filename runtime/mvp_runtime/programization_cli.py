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

Candidate lifecycle (shadow-validation path; explicit Thomas decision 2026-07-22):

    python -m runtime.mvp_runtime.programization_cli ready    <candidate_id> --by thomas --reason "..."
    python -m runtime.mvp_runtime.programization_cli validate <candidate_id> --by thomas --reason "..."
    python -m runtime.mvp_runtime.programization_cli shadow   <candidate_id> --outcome PASS \
        --comparison-ref shadow-cmp-001 --result "..." --by thomas --reason "..."
    python -m runtime.mvp_runtime.programization_cli accept   <candidate_id> --by thomas --reason "..."
    python -m runtime.mvp_runtime.programization_cli reject   <candidate_id> --by thomas --reason "..."

DRAFT → REVIEW_READY → VALIDATING (shadow RUNNING) → shadow PASS/FAIL recorded with
evidence → ACCEPTED (requires shadow PASS) or REJECTED; ACCEPTED/REJECTED are terminal.
The runtime never runs the shadow — Programs are unregistered and unregistered execution
is BLOCK — it enforces and records the operator's limited comparison.

A candidate grants **nothing** at every stage, ACCEPTED included:
``activation_eligibility`` stays
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
    build_candidate_event,
    build_review_event,
    create_program_candidate,
    record_shadow_result,
    transition_candidate,
    transition_review,
)
from .store import LedgerStore

_TRANSITION_TARGET = {"review": "UNDER_REVIEW", "close": "CLOSED"}
_CANDIDATE_ACTIONS = ("ready", "validate", "accept", "reject")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="programization_cli",
        description="Programization review handling (status/review/candidate/close + candidate lifecycle).")
    parser.add_argument("command", choices=["status", "review", "candidate", "close",
                                            *_CANDIDATE_ACTIONS, "shadow"])
    parser.add_argument("target", nargs="?", default=None,
                        help="pattern_id (review/candidate/close) or candidate_id (lifecycle commands)")
    parser.add_argument("--by", default="", help="operator identity recorded on the review event")
    parser.add_argument("--reason", default="", help="operator reason recorded on the review event")
    parser.add_argument("--input", default=None, help="candidate only: YAML/JSON file with the review substance")
    parser.add_argument("--outcome", default=None, choices=["PASS", "FAIL"],
                        help="shadow only: the limited-comparison outcome")
    parser.add_argument("--comparison-ref", default="", help="shadow only: reference to the comparison evidence")
    parser.add_argument("--result", default="", help="shadow only: what the comparison showed")
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
            candidates = store.latest_candidates()
            if not patterns:
                sys.stdout.write("no programization patterns\n")
            for pattern_id, p in sorted(patterns.items()):
                sys.stdout.write(
                    f"{pattern_id}  {p.get('review_status'):<13} "
                    f"valid={p.get('valid_repetition_count')}/{p.get('review_trigger_count')}  "
                    f"updated={p.get('last_updated_at_utc')}\n"
                )
            for candidate_id, c in sorted(candidates.items()):
                shadow = c.get("shadow_validation", {})
                sys.stdout.write(
                    f"{candidate_id}  {c.get('status'):<13} shadow={shadow.get('status')}  "
                    f"pattern={c.get('pattern_id')}\n"
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
        if not args.target:
            raise ProgramizationBlocked("PATTERN_NOT_FOUND", f"{args.command} requires a pattern/candidate id")

        if args.command in _TRANSITION_TARGET:
            before = store.latest_patterns().get(args.target, {})
            pattern = transition_review(
                store, args.target, _TRANSITION_TARGET[args.command],
                reviewed_by=args.by, reason=args.reason, now=stamp,
            )
            event = build_review_event(
                pattern, action=f"review_{args.command}", from_status=str(before.get("review_status")),
                reviewed_by=args.by.strip(), reason=args.reason.strip(), now=stamp,
            )
            ledger.append_programization_event(event)
            sys.stdout.write(f"{args.target}: {before.get('review_status')} -> {pattern['review_status']}\n")
            return EXIT_OK

        if args.command in _CANDIDATE_ACTIONS or args.command == "shadow":
            before = store.latest_candidates().get(args.target, {})
            if args.command == "shadow":
                candidate = record_shadow_result(
                    store, args.target, args.outcome or "",
                    comparison_ref=args.comparison_ref, result=args.result,
                    reviewed_by=args.by, reason=args.reason,
                )
                action = f"shadow_{candidate['shadow_validation']['status'].lower()}"
            else:
                candidate = transition_candidate(
                    store, args.target, args.command,
                    reviewed_by=args.by, reason=args.reason,
                )
                action = f"candidate_{args.command}"
            event = build_candidate_event(
                candidate, action=action, from_status=str(before.get("status")),
                reviewed_by=args.by.strip(), reason=args.reason.strip(), now=stamp,
            )
            ledger.append_programization_event(event)
            shadow = candidate["shadow_validation"]
            sys.stdout.write(
                f"{args.target}: {before.get('status')} -> {candidate['status']} "
                f"(shadow={shadow.get('status')}; grants nothing — registry/activation stay APPROVAL_REQUIRED)\n"
            )
            return EXIT_OK

        # candidate: draft the review's outcome record. The pattern stays UNDER_REVIEW —
        # closing the review is its own explicit decision.
        review_input = _load_review_input(args.input)
        candidate = create_program_candidate(
            store, args.target, review_input,
            created_by=args.by, reason=args.reason, now=stamp,
        )
        pattern = store.latest_patterns()[args.target]
        event = build_review_event(
            pattern, action="candidate_drafted", from_status="UNDER_REVIEW",
            reviewed_by=args.by.strip(), reason=args.reason.strip(), now=stamp,
            candidate_id=candidate["candidate_id"],
        )
        ledger.append_programization_event(event)
        sys.stdout.write(
            f"drafted candidate {candidate['candidate_id']} for {args.target} "
            "(DRAFT; grants nothing — registry/activation stay APPROVAL_REQUIRED)\n"
        )
        return EXIT_OK
    except MvpRuntimeError as exc:
        return report_block(exc)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
