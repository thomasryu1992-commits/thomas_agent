#!/usr/bin/env python3
"""Operator tool: promote strategy candidates into the ACTIVE pool (C8 door + C8b ask).

The factory (and the C7 import) only ever produce **candidates**. What the runtime
actually trades — the active pool — changes exclusively through this explicit operator
action, mirroring ``promote_memory_candidate.py``: kill-switch checked first, an
explicit operator identity and reason required, the installed pool validated
spec-by-spec (fail-closed), and the promotion recorded on the control ledger with the
selected ids, their rule hashes, and their backtest evidence hashes. A good backtest
is never auto-promotion — this script is where the human judgment lands.

C8b wiring (approved by Thomas 2026-07-22): promotion goes through the R9 ask first.

    # 1) List candidates (first column = candidate_id), choose, then ASK Thomas
    #    (stores the PENDING approval). strategy_id restarts at S001 every generation,
    #    so selection keys on the globally unique candidate_id; a bare strategy id is
    #    accepted only while it matches exactly one lineage.
    python scripts/promote_strategy_candidates.py --list
    python scripts/promote_strategy_candidates.py --request --candidate-ids cand_ab12,cand_cd34

    # 2) Thomas answers /approve <id> (or /reject) on the verified control channel.

    # 3) Execute the approved promotion (the approval is VERIFIED, never consumed):
    python scripts/promote_strategy_candidates.py \\
        --candidate-ids cand_ab12,cand_cd34 --approval-id approval_abc123 \\
        --promoted-by Thomas --reason "GEN-069 robustness reviewed" --confirm

``--keep-active`` keeps the current pool members and adds the selected candidates;
without it the selected candidates REPLACE the pool (the mode is part of the approval's
content hash — an approval for one mode cannot execute the other). ``--without-approval``
is the explicit legacy escape (pre-C8b posture); which door was used is recorded on the
control ledger either way.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.approval_store import STORE_REL as APPROVAL_STORE_REL  # noqa: E402
from runtime.mvp_runtime.approval_store import ApprovalStore  # noqa: E402
from runtime.mvp_runtime.audit import build_approval_request_audit  # noqa: E402
from runtime.mvp_runtime.control import ControlStore  # noqa: E402
from runtime.mvp_runtime.crypto import pool as pool_store  # noqa: E402
from runtime.mvp_runtime.crypto import promotion as promotion_mod  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.events import stamped_event  # noqa: E402
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore  # noqa: E402

PROMOTION_EVENT_TYPE = "crypto_strategy_promotion_event.v0"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3


def run_request(*, selectors: list[str], keep_active: bool, root: Path | None = None,
                now: str | None = None) -> dict:
    """Build + store + audit the R9 ask for this promotion (the trial_cli pattern)."""
    now = now or timeutil.utc_now_iso()
    prepared = promotion_mod.request_promotion(
        selectors, keep_active=keep_active, now=now, repo_root=root,
    )
    store = ApprovalStore(root / APPROVAL_STORE_REL) if root is not None else ApprovalStore.default()
    store.append_permission_decision(prepared["permission_decision"])
    store.append([prepared["approval_request"]])

    ledger = LedgerStore((root if root is not None else ROOT) / LEDGER_REL)
    try:
        ledger.append_audit_events(build_approval_request_audit(
            prepared["approval_request"], now=now, genesis_previous_hash=ledger.last_audit_hash(),
        ))
    except MvpRuntimeError as exc:
        sys.stderr.write(f"WARNING: request audit failed ({exc.reason_code}); the request stands\n")
    return prepared


def run_promotion(
    *, selectors: list[str], promoted_by: str, reason: str,
    keep_active: bool, root: Path | None = None, now: str | None = None,
    approval_id: str | None = None, without_approval: bool = False,
) -> dict:
    """Install the selected candidates into the active pool. Fail-closed.

    ``selectors`` are candidate ids (preferred) or unambiguous strategy ids — a
    strategy id shared by several generations refuses (``CANDIDATE_AMBIGUOUS``)
    instead of silently promoting the newest. C8b: requires either an APPROVED,
    unexpired, content-matching approval id or the explicit ``without_approval``
    escape; the door used is recorded on the ledger."""
    now = now or timeutil.utc_now_iso()

    # Kill switch first: promotion mutates what the runtime trades.
    state = ControlStore(root if root is not None else ROOT).load()
    if not state.execution_allowed:
        raise SystemExit(f"BLOCKED: runtime is {state.mode}; promotion refused ({state.refusal_reason_code()})")

    if approval_id is None and not without_approval:
        raise SystemExit(
            "BLOCKED: promotion needs --approval-id (ask first with --request) or the "
            "explicit --without-approval escape"
        )
    verified_approval = None
    if approval_id is not None:
        approval_store = ApprovalStore(root / APPROVAL_STORE_REL) if root is not None else ApprovalStore.default()
        try:
            verified_approval = promotion_mod.verify_promotion_approval(
                approval_store.get(approval_id),
                selectors=selectors, keep_active=keep_active, root=root, now=now,
            )
        except MvpRuntimeError as exc:
            raise SystemExit(f"BLOCKED {exc.reason_code}: {exc.reason}")

    try:
        candidates = pool_store.resolve_candidates(selectors, root)
    except MvpRuntimeError as exc:
        raise SystemExit(f"BLOCKED {exc.reason_code}: {exc.reason}")

    entries = []
    if keep_active:
        entries.extend(pool_store.load_active_pool(root).get("active_strategies") or [])
    existing_ids = {e.get("strategy_id") for e in entries}
    existing_cids = {e.get("candidate_id") for e in entries}
    for c in candidates:
        if c["candidate_id"] in existing_cids:
            raise SystemExit(f"BLOCKED: candidate {c['candidate_id']} is already in the active pool")
        if c.get("strategy_id") in existing_ids:
            # Pool invariant: strategy_id keys routing/lifecycle, so display names
            # stay unique in the pool even across lineages.
            raise SystemExit(f"BLOCKED: {c.get('strategy_id')} is already in the active pool")
        entries.append({
            "strategy_id": c.get("strategy_id"),
            "candidate_id": c["candidate_id"],
            "status": "PAPER_ACTIVE",
            "champion_score": c.get("champion_score"),
            "strategy_rule_hash": c.get("strategy_rule_hash"),
            "generation_id": c.get("generation_id"),
            "strategy_spec": c.get("strategy_spec"),
            "promoted_by": promoted_by,
            "promoted_at": now,
        })

    new_pool = {
        "pool_version": "active_strategy_pool.v1",
        "stage": "paper",
        "active_strategies": entries,
        "updated_by": promoted_by,
        "updated_at": now,
    }
    installed = pool_store.install_active_pool(new_pool, root=root)  # validates fail-closed

    summary = {
        "promoted_candidate_ids": [c["candidate_id"] for c in candidates],
        "promoted_strategy_ids": [c.get("strategy_id") for c in candidates],
        "promoted_rule_hashes": [c.get("strategy_rule_hash") for c in candidates],
        "evidence_hashes": [c.get("evidence_input_sha256") for c in candidates],
        "kept_active": keep_active,
        "pool_size": installed,
        "promoted_by": promoted_by,
        "reason": reason,
        # C8b: which door authorized this — a verified approval, or the explicit escape.
        "approval_id": approval_id,
        "approval_verified": verified_approval is not None,
        "without_approval_escape": bool(without_approval and approval_id is None),
        "created_at": now,
    }
    ledger = LedgerStore((root if root is not None else ROOT) / LEDGER_REL)
    ledger.append_control(stamped_event(PROMOTION_EVENT_TYPE, **summary))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Promote strategy candidates into the active pool.")
    parser.add_argument("--list", action="store_true", help="list candidates and exit")
    parser.add_argument("--request", action="store_true",
                        help="ASK Thomas: build + store + audit the R9 approval request, then exit")
    parser.add_argument("--candidate-ids", "--strategy-ids", dest="strategy_ids",
                        help="comma-separated candidate ids (preferred; see --list) or "
                             "unambiguous strategy ids — an id shared by several "
                             "generations is refused, never resolved newest-wins")
    parser.add_argument("--keep-active", action="store_true", help="keep current pool members (add, not replace)")
    parser.add_argument("--promoted-by", help="operator identity")
    parser.add_argument("--reason", help="operator reason (the report)")
    parser.add_argument("--approval-id", help="APPROVED approval id from the /approve answer (verified, never consumed)")
    parser.add_argument("--without-approval", action="store_true",
                        help="explicit legacy escape: promote without an approval record (audited as such)")
    parser.add_argument("--confirm", action="store_true", help="actually install; refused without it")
    args = parser.parse_args(argv)

    if args.list:
        try:
            candidates = pool_store.read_candidates(None)
        except MvpRuntimeError as exc:
            print(f"BLOCKED {exc.reason_code}: {exc.reason}")
            return EXIT_BLOCKED
        for c in candidates:
            spec = c.get("strategy_spec") or {}
            evidence = c.get("backtest_evidence") or {}
            robustness = (evidence.get("robustness") or {})
            print(f"{pool_store.candidate_id(c):26} {c.get('strategy_id'):8} "
                  f"{c.get('generation_id') or '-':8} "
                  f"{spec.get('strategy_family') or '-':26} score={c.get('champion_score')} "
                  f"verdict={robustness.get('verdict') or '-':11} "
                  f"closed={evidence.get('closed_count')} provenance={c.get('provenance')}")
        return EXIT_OK

    if not args.strategy_ids:
        print("BLOCKED: --candidate-ids is required (or use --list)")
        return EXIT_USAGE
    selectors = [s.strip() for s in args.strategy_ids.split(",") if s.strip()]

    if args.request:
        prepared = run_request(selectors=selectors, keep_active=args.keep_active)
        request = prepared["approval_request"]
        from runtime.mvp_runtime import approval as approval_mod  # noqa: E402 (message renderer)
        print(approval_mod.request_message(request, prepared["permission_decision"], history=None))
        print(f"\nSTORED: {request['approval_id']} is PENDING until {request['validity']['expires_at']}.")
        print("Thomas answers /approve <id> or /reject <id> on the verified control channel; then re-run "
              f"with --approval-id {request['approval_id']} --confirm.")
        return EXIT_OK

    if not (args.promoted_by and args.reason):
        print("BLOCKED: --promoted-by and --reason are required to execute a promotion")
        return EXIT_USAGE
    if not args.confirm:
        print("BLOCKED: promotion requires --confirm (a good backtest is never auto-promotion)")
        return EXIT_BLOCKED

    summary = run_promotion(
        selectors=selectors,
        promoted_by=args.promoted_by, reason=args.reason, keep_active=args.keep_active,
        approval_id=args.approval_id, without_approval=args.without_approval,
    )
    door = summary["approval_id"] or "WITHOUT-APPROVAL ESCAPE"
    print(f"PROMOTED: {summary['promoted_candidate_ids']} "
          f"({summary['promoted_strategy_ids']}) -> active pool "
          f"({summary['pool_size']} strategies) [door: {door}]")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
