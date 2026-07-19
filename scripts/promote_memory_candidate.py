#!/usr/bin/env python3
"""Operator tool: promote a working-memory CANDIDATE to VALIDATED memory (R5).

Governance: promoting validated low-risk operational knowledge is EXECUTE_AND_REPORT, and
``automatic_runtime_promotion_allowed`` is false — so promotion never happens on the run
path. This is the ONLY entry point that promotes, and it requires an explicit operator
identity and reason (the "report"). It reads the local working-memory store, finds the
named candidate, promotes it via ``memory.promote_candidate``, appends the VALIDATED entry
to the local (gitignored, per-machine) validated store, and — R5.4 — records the promotion
as its own audit event in the durable ledger (the machine-readable "report"): an ``OTHER``
event chained onto the ledger tip, anchored to the originating task via the candidate's
provenance.

Hardened to the R10 consumption path's guards (QA wave 6d) — the same physical action must
carry the same protections whichever door it comes through: the candidate is resolved with
the shared **latest-wins** lookup (``working_memory.find_candidate``, never a stale first
match), an **expired** candidate is refused exactly as consumption refuses it, and the
**kill switch** is checked first (promotion mutates VALIDATED memory; ``kill_allows`` is
read-only only). Write order is validated entry -> PROMOTED retirement marker -> audit
event. Unlike consumption there is no single-use grant to spend first, and retiring the
candidate before promoting would strand its content (retired but never written) on a
mid-sequence failure; each later leg failing is reported as exactly what it is
(PROMOTED_NOT_RETIRED / PROMOTED_UNAUDITED), never masked as "nothing happened".

Example:

    python scripts/promote_memory_candidate.py \\
        --candidate-id memcand_abc123 --promoted-by Thomas \\
        --reason "Reusable finding confirmed across analyses."

Lists candidates first with ``--list``. Nothing here runs the agent or touches the network.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime.audit import build_promotion_audit  # noqa: E402
from runtime.mvp_runtime.control import ControlStore  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.memory import is_expired, promote_candidate  # noqa: E402
from runtime.mvp_runtime.paths import repo_root as _repo_root  # noqa: E402
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore  # noqa: E402
from runtime.mvp_runtime.working_memory import WorkingMemoryStore, find_candidate, mark_promoted  # noqa: E402


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidate-id", help="candidate_id of the working-memory candidate to promote")
    parser.add_argument("--promoted-by", help="operator identity performing the promotion (e.g. Thomas)")
    parser.add_argument("--reason", help="operator reason for the promotion (EXECUTE_AND_REPORT)")
    parser.add_argument("--list", action="store_true", help="list current working-memory candidates and exit")
    parser.add_argument("--root", type=Path, default=None, help="repo root (default: this repo)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, store: WorkingMemoryStore | None = None,
         ledger: LedgerStore | None = None, control_store: ControlStore | None = None,
         now: str | None = None) -> int:
    args = _parse_args(argv)
    if store is None:
        store = WorkingMemoryStore(args.root / ".runtime_governance_state/working_memory") if args.root \
            else WorkingMemoryStore.default()
    if ledger is None:
        ledger = LedgerStore(args.root / LEDGER_REL) if args.root else LedgerStore.default()
    if control_store is None:
        control_store = ControlStore(args.root if args.root else _repo_root())

    try:
        candidates = store.read_all()
    except MvpRuntimeError as exc:
        print(f"ERROR {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return 2

    if args.list:
        for cand in candidates:
            print(f"{cand.get('candidate_id')}\t{cand.get('candidate_type')}\t{cand.get('content', '')[:70]}")
        print(f"({len(candidates)} candidate(s))")
        return 0

    if not (args.candidate_id and args.promoted_by and args.reason):
        print("ERROR: --candidate-id, --promoted-by and --reason are required to promote", file=sys.stderr)
        return 2

    stamp = now or _utc_now_iso()

    # Kill-switch first (kill_allows is read-only only): promotion mutates VALIDATED
    # memory, so a PAUSED/KILLED runtime must refuse — exactly as the R10 consume does.
    state = control_store.load()
    if not state.execution_allowed:
        print(f"ERROR KILL_SWITCH_ACTIVE: runtime is {state.mode}; "
              "promotion mutates validated memory and is refused while not ACTIVE", file=sys.stderr)
        return 1

    # THE candidate lookup (latest-wins, live-CANDIDATE-only) — shared with the R9 ask and
    # the R10 spend. A first-match scan promoted the oldest copy of a re-appended id, and a
    # candidate already retired by a promotion stayed promotable here.
    match = find_candidate(store, args.candidate_id)
    if match is None:
        print(f"ERROR: no live working-memory candidate with id {args.candidate_id!r} "
              "(unknown, already promoted, or pruned)", file=sys.stderr)
        return 2
    # Retention (§12.4) holds on every write path that makes content permanent: an
    # expired-but-not-yet-pruned candidate is refused, exactly as consumption refuses it.
    if is_expired(match, stamp):
        print(f"ERROR CANDIDATE_EXPIRED: candidate expired at {match.get('expires_at')}; "
              "an expired candidate cannot be promoted", file=sys.stderr)
        return 1

    try:
        validated = promote_candidate(
            match, promoted_by=args.promoted_by, reason=args.reason, now=stamp
        )
        # Build the promotion's audit event before persisting anything: a promotion that
        # cannot be audited (e.g. a candidate with no origin provenance) fails closed here,
        # with nothing yet written. The event chains onto the durable ledger's tip.
        audit_event, _sha = build_promotion_audit(
            match, validated, promoted_by=args.promoted_by, reason=args.reason,
            now=stamp, previous_hash=ledger.last_audit_hash(),
        )
        store.append_validated([validated])
    except MvpRuntimeError as exc:
        print(f"ERROR {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return 1
    # Partial failures after the validated write are reported as what they ARE — the
    # promotion is durable — never masked as a clean failure (the old single try block
    # printed one generic ERROR whether nothing, half, or all-but-audit had been written).
    try:
        mark_promoted(store, match, validated_memory_id=validated["validated_memory_id"], now=stamp)
    except MvpRuntimeError as exc:
        print(f"ERROR PROMOTED_NOT_RETIRED: the promotion is written but the candidate's "
              f"PROMOTED marker failed ({exc.reason_code}); the candidate remains visible "
              "to a future promotion — investigate", file=sys.stderr)
        return 1
    try:
        ledger.append_audit_events([audit_event])
    except MvpRuntimeError as exc:
        print(f"ERROR PROMOTED_UNAUDITED: the promotion is written but its ledger event "
              f"failed ({exc.reason_code}); an unaudited validated-memory mutation is on "
              "disk — investigate the ledger before anything else", file=sys.stderr)
        return 1

    print(f"Promoted {args.candidate_id} -> {validated['validated_memory_id']} (VALIDATED, {validated['scope']})")
    print(f"  by {validated['promoted_by']}: {validated['promotion_reason']}")
    print(f"  audited as {audit_event['audit_event_id']} (OTHER / MEMORY_PROMOTED) in the durable ledger")
    print("Validated memory and ledger are local, gitignored, per-machine. Never committed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
