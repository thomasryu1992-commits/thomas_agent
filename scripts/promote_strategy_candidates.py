#!/usr/bin/env python3
"""Operator tool: promote strategy candidates into the ACTIVE pool (C8, pre-R10 door).

The factory (and the C7 import) only ever produce **candidates**. What the runtime
actually trades — the active pool — changes exclusively through this explicit operator
action, mirroring ``promote_memory_candidate.py``: kill-switch checked first, an
explicit operator identity and reason required, the installed pool validated
spec-by-spec (fail-closed), and the promotion recorded on the control ledger with the
selected ids, their rule hashes, and their backtest evidence hashes. A good backtest
is never auto-promotion — this script is where the human judgment lands.

    python scripts/promote_strategy_candidates.py --list
    python scripts/promote_strategy_candidates.py \\
        --strategy-ids S001,S003 --keep-active \\
        --promoted-by Thomas --reason "GEN-069 backtest reviewed" --confirm

``--keep-active`` keeps the current pool members and adds the selected candidates;
without it the selected candidates REPLACE the pool. The R9 approval-request wiring
for this action is a separate increment (C8b) — until then this door plus the durable
control-ledger record is the promotion authority, exactly like memory promotion
before R10.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.control import ControlStore  # noqa: E402
from runtime.mvp_runtime.crypto import pool as pool_store  # noqa: E402
from runtime.mvp_runtime.events import stamped_event  # noqa: E402
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore  # noqa: E402

PROMOTION_EVENT_TYPE = "crypto_strategy_promotion_event.v0"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3


def run_promotion(
    *, strategy_ids: list[str], promoted_by: str, reason: str,
    keep_active: bool, root: Path | None = None, now: str | None = None,
) -> dict:
    """Install the selected candidates into the active pool. Fail-closed."""
    now = now or timeutil.utc_now_iso()

    # Kill switch first: promotion mutates what the runtime trades.
    state = ControlStore(root if root is not None else ROOT).load()
    if not state.execution_allowed:
        raise SystemExit(f"BLOCKED: runtime is {state.mode}; promotion refused ({state.refusal_reason_code()})")

    candidates = {c.get("strategy_id"): c for c in pool_store.read_candidates(root)}
    missing = [s for s in strategy_ids if s not in candidates]
    if missing:
        raise SystemExit(f"BLOCKED: unknown candidate strategy ids: {missing}")

    entries = []
    if keep_active:
        entries.extend(pool_store.load_active_pool(root).get("active_strategies") or [])
    existing_ids = {e.get("strategy_id") for e in entries}
    for strategy_id in strategy_ids:
        if strategy_id in existing_ids:
            raise SystemExit(f"BLOCKED: {strategy_id} is already in the active pool")
        c = candidates[strategy_id]
        entries.append({
            "strategy_id": strategy_id,
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
        "promoted_strategy_ids": strategy_ids,
        "promoted_rule_hashes": [candidates[s].get("strategy_rule_hash") for s in strategy_ids],
        "evidence_hashes": [candidates[s].get("evidence_input_sha256") for s in strategy_ids],
        "kept_active": keep_active,
        "pool_size": installed,
        "promoted_by": promoted_by,
        "reason": reason,
        "created_at": now,
    }
    ledger = LedgerStore((root if root is not None else ROOT) / LEDGER_REL)
    ledger.append_control(stamped_event(PROMOTION_EVENT_TYPE, **summary))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Promote strategy candidates into the active pool.")
    parser.add_argument("--list", action="store_true", help="list candidates and exit")
    parser.add_argument("--strategy-ids", help="comma-separated candidate strategy ids to promote")
    parser.add_argument("--keep-active", action="store_true", help="keep current pool members (add, not replace)")
    parser.add_argument("--promoted-by", help="operator identity")
    parser.add_argument("--reason", help="operator reason (the report)")
    parser.add_argument("--confirm", action="store_true", help="actually install; refused without it")
    args = parser.parse_args(argv)

    if args.list:
        for c in pool_store.read_candidates(None):
            spec = c.get("strategy_spec") or {}
            evidence = c.get("backtest_evidence") or {}
            print(f"{c.get('strategy_id'):8} {c.get('generation_id') or '-':8} "
                  f"{spec.get('strategy_family') or '-':26} score={c.get('champion_score')} "
                  f"closed={evidence.get('closed_count')} provenance={c.get('provenance')}")
        return EXIT_OK

    if not (args.strategy_ids and args.promoted_by and args.reason):
        print("BLOCKED: --strategy-ids, --promoted-by and --reason are required (or use --list)")
        return EXIT_USAGE
    if not args.confirm:
        print("BLOCKED: promotion requires --confirm (a good backtest is never auto-promotion)")
        return EXIT_BLOCKED

    summary = run_promotion(
        strategy_ids=[s.strip() for s in args.strategy_ids.split(",") if s.strip()],
        promoted_by=args.promoted_by, reason=args.reason, keep_active=args.keep_active,
    )
    print(f"PROMOTED: {summary['promoted_strategy_ids']} -> active pool ({summary['pool_size']} strategies)")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
