#!/usr/bin/env python3
"""Operator tool: promote a working-memory CANDIDATE to VALIDATED memory (R5).

Governance: promoting validated low-risk operational knowledge is EXECUTE_AND_REPORT, and
``automatic_runtime_promotion_allowed`` is false — so promotion never happens on the run
path. This is the ONLY entry point that promotes, and it requires an explicit operator
identity and reason (the "report"). It reads the local working-memory store, finds the
named candidate, promotes it via ``memory.promote_candidate``, and appends the VALIDATED
entry to the local (gitignored, per-machine) validated store.

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

from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.memory import promote_candidate  # noqa: E402
from runtime.mvp_runtime.working_memory import WorkingMemoryStore  # noqa: E402


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


def main(argv: list[str] | None = None, *, store: WorkingMemoryStore | None = None, now: str | None = None) -> int:
    args = _parse_args(argv)
    if store is None:
        store = WorkingMemoryStore(args.root / ".runtime_governance_state/working_memory") if args.root \
            else WorkingMemoryStore.default()

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

    match = next((c for c in candidates if c.get("candidate_id") == args.candidate_id), None)
    if match is None:
        print(f"ERROR: no working-memory candidate with id {args.candidate_id!r}", file=sys.stderr)
        return 2

    try:
        validated = promote_candidate(
            match, promoted_by=args.promoted_by, reason=args.reason, now=now or _utc_now_iso()
        )
        store.append_validated([validated])
    except MvpRuntimeError as exc:
        print(f"ERROR {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return 1

    print(f"Promoted {args.candidate_id} -> {validated['validated_memory_id']} (VALIDATED, {validated['scope']})")
    print(f"  by {validated['promoted_by']}: {validated['promotion_reason']}")
    print("Validated memory is local, gitignored, per-machine. Never committed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
