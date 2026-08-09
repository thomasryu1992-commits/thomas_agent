#!/usr/bin/env python3
"""Operator tool: retire (SUSPEND) named strategies in the ACTIVE pool.

The narrow counterpart to ``promote_strategy_candidates.py``. That script is the only
door into the pool, and its replace mode rebuilds every entry with a hardcoded
``PAPER_ACTIVE`` — so using it to drop one redundant strategy silently reactivates
every terminally SUSPENDED member and resets the whole pool's lifecycle counters. This
door changes ``status`` on the named entries and nothing else.

Same approval flow as promotion (R9 ask first, verify never consume):

    # 1) See what is routable, choose, then ASK Thomas (stores the PENDING approval).
    python scripts/retire_strategies.py --list
    python scripts/retire_strategies.py --request --strategy-ids S3086,S2955 \\
        --reason "duplicate of S2758 (tautological conditions)"

    # 2) Thomas answers /approve <id> (or /reject) on the verified control channel.

    # 3) Execute the approved retirement (the approval is VERIFIED, never consumed):
    python scripts/retire_strategies.py --strategy-ids S3086,S2955 \\
        --approval-id approval_abc123 --retired-by Thomas \\
        --reason "duplicate of S2758 (tautological conditions)" --confirm

``--without-approval`` is the explicit escape, recorded on the ledger either way.
Selection is by the pool's display ``strategy_id`` because that is what the pool is
keyed by; the approval additionally binds each slot's lineage and rule hash, so an id
that changes hands between the ask and the execution refuses.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.approval_store import STORE_REL as APPROVAL_STORE_REL  # noqa: E402
from runtime.mvp_runtime.approval_store import ApprovalStore  # noqa: E402
from runtime.mvp_runtime.audit import build_approval_request_audit  # noqa: E402
from runtime.mvp_runtime.control import ControlStore  # noqa: E402
from runtime.mvp_runtime.crypto import pool as pool_store  # noqa: E402
from runtime.mvp_runtime.crypto import retirement as retirement_mod  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.events import stamped_event  # noqa: E402
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run  # noqa: E402
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore  # noqa: E402

RETIREMENT_EVENT_TYPE = "crypto_strategy_retirement_event.v0"



def run_request(*, strategy_ids: list[str], reason: str, root: Path | None = None,
                now: str | None = None) -> dict:
    """Build + store + audit the R9 ask for this retirement (the promotion pattern)."""
    now = now or timeutil.utc_now_iso()
    prepared = retirement_mod.request_retirement(
        strategy_ids, reason=reason, now=now, repo_root=root,
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


def run_retirement(
    *, strategy_ids: list[str], retired_by: str, reason: str,
    root: Path | None = None, now: str | None = None,
    approval_id: str | None = None, without_approval: bool = False,
) -> dict:
    """Suspend the named pool entries. Fail-closed.

    C8b posture, unchanged: requires either an APPROVED, unexpired, content-matching
    approval id or the explicit ``without_approval`` escape; the door used is recorded
    on the ledger.
    """
    now = now or timeutil.utc_now_iso()

    # Kill switch first: a retirement mutates what the runtime trades.
    state = ControlStore(root if root is not None else ROOT).load()
    if not state.execution_allowed:
        raise SystemExit(f"BLOCKED: runtime is {state.mode}; retirement refused ({state.refusal_reason_code()})")

    if approval_id is None and not without_approval:
        raise SystemExit(
            "BLOCKED: retirement needs --approval-id (ask first with --request) or the "
            "explicit --without-approval escape"
        )
    verified_approval = None
    if approval_id is not None:
        approval_store = ApprovalStore(root / APPROVAL_STORE_REL) if root is not None else ApprovalStore.default()
        try:
            verified_approval = retirement_mod.verify_retirement_approval(
                approval_store.get(approval_id), strategy_ids=strategy_ids, root=root, now=now,
            )
        except MvpRuntimeError as exc:
            raise SystemExit(f"BLOCKED {exc.reason_code}: {exc.reason}")

    try:
        applied = retirement_mod.apply_retirement(
            strategy_ids, reason=reason, retired_by=retired_by, root=root, now=now,
        )
    except MvpRuntimeError as exc:
        raise SystemExit(f"BLOCKED {exc.reason_code}: {exc.reason}")

    summary = {
        **applied,
        "approval_id": approval_id,
        "approval_verified": verified_approval is not None,
        "without_approval_escape": bool(without_approval and approval_id is None),
        "pool_size": len(pool_store.load_active_pool(root).get("active_strategies") or []),
    }
    ledger = LedgerStore((root if root is not None else ROOT) / LEDGER_REL)
    ledger.append_control(stamped_event(RETIREMENT_EVENT_TYPE, **summary))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retire (SUSPEND) active-pool strategies.")
    parser.add_argument("--list", action="store_true", help="list pool entries and exit")
    parser.add_argument("--request", action="store_true",
                        help="ask Thomas for this retirement (stores a PENDING approval)")
    parser.add_argument("--strategy-ids", dest="strategy_ids",
                        help="comma-separated pool display ids (e.g. S3086,S2955)")
    parser.add_argument("--retired-by", help="operator identity")
    parser.add_argument("--reason", help="operator reason (the report)")
    parser.add_argument("--approval-id", help="APPROVED approval id from the /approve answer")
    parser.add_argument("--without-approval", action="store_true",
                        help="explicit legacy escape: retire without an approval id")
    parser.add_argument("--confirm", action="store_true", help="actually suspend; refused without it")
    args = parser.parse_args(argv)

    if args.list:
        pool = pool_store.load_active_pool(None)
        entries = pool.get("active_strategies") or []
        counts = collections.Counter(str(e.get("status")) for e in entries)
        print(f"pool={len(entries)}  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        print("NOTE: only non-terminal entries can be retired; SUSPENDED/ARCHIVED are already terminal.")
        for e in sorted(entries, key=lambda e: (str(e.get("status")), -(e.get("champion_score") or 0))):
            spec = e.get("strategy_spec") or {}
            scope = (spec.get("symbol_scope") or ["?"])[0]
            print(f"{str(e.get('strategy_id')):<14} {str(e.get('status')):<13} {scope:<9} "
                  f"{str(spec.get('timeframe')):<4} {str(spec.get('direction')):<5} "
                  f"score={e.get('champion_score')} lineage={e.get('candidate_id') or 'none'}")
        return EXIT_OK

    strategy_ids = [s.strip() for s in (args.strategy_ids or "").split(",") if s.strip()]
    if not strategy_ids:
        parser.error("--strategy-ids is required")
    if not args.reason:
        parser.error("--reason is required")

    if args.request:
        try:
            prepared = run_request(strategy_ids=strategy_ids, reason=args.reason)
        except MvpRuntimeError as exc:
            print(f"BLOCKED {exc.reason_code}: {exc.reason}")
            return EXIT_BLOCKED
        request = prepared["approval_request"]
        print(f"PENDING approval stored: {request.get('approval_id')}")
        print(f"  strategies : {', '.join(strategy_ids)} -> SUSPENDED")
        print(f"  reason     : {args.reason}")
        print(f"  content    : {prepared['content_sha256']}")
        print(f"  expires    : {(request.get('validity') or {}).get('expires_at')}")
        print("Thomas answers /approve <id> or /reject <id> on the verified control channel.")
        return EXIT_OK

    if not args.retired_by:
        parser.error("--retired-by is required")
    if not args.confirm:
        print("REFUSED: pass --confirm to actually suspend these strategies")
        return EXIT_USAGE

    assert_not_foreign_root_run()
    summary = run_retirement(
        strategy_ids=strategy_ids, retired_by=args.retired_by, reason=args.reason,
        approval_id=args.approval_id, without_approval=args.without_approval,
    )
    print(f"RETIRED {summary['entries_changed']} entr{'y' if summary['entries_changed'] == 1 else 'ies'}: "
          f"{', '.join(summary['strategy_ids'])}")
    print(f"  from       : {', '.join(summary['previous_statuses'])} -> SUSPENDED")
    print(f"  pool size  : {summary['pool_size']} (unchanged — membership is not touched)")
    print(f"  door       : {'approval ' + str(summary['approval_id']) if summary['approval_verified'] else 'WITHOUT-APPROVAL ESCAPE'}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
