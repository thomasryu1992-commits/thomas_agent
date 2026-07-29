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

import collections
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
from runtime.mvp_runtime.crypto import cost as cost_mod  # noqa: E402
from runtime.mvp_runtime.crypto import pool as pool_store  # noqa: E402
from runtime.mvp_runtime.crypto import promotion as promotion_mod  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.events import stamped_event  # noqa: E402
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run  # noqa: E402
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore  # noqa: E402

PROMOTION_EVENT_TYPE = "crypto_strategy_promotion_event.v0"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3


def run_request(*, selectors: list[str], keep_active: bool, root: Path | None = None,
                now: str | None = None, allow_stale_cost_basis: bool = False) -> dict:
    """Build + store + audit the R9 ask for this promotion (the trial_cli pattern)."""
    now = now or timeutil.utc_now_iso()
    prepared = promotion_mod.request_promotion(
        selectors, keep_active=keep_active, now=now, repo_root=root,
        allow_stale_cost_basis=allow_stale_cost_basis,
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
    allow_stale_cost_basis: bool = False,
) -> dict:
    """Install the selected candidates into the active pool. Fail-closed.

    ``selectors`` are candidate ids (preferred) or unambiguous strategy ids — a
    strategy id shared by several generations refuses (``CANDIDATE_AMBIGUOUS``)
    instead of silently promoting the newest. C8b: requires either an APPROVED,
    unexpired, content-matching approval id or the explicit ``without_approval``
    escape; the door used is recorded on the ledger.

    Evidence scored under a cost model cheaper than the venue charges refuses with
    ``CANDIDATE_COST_BASIS_STALE`` unless ``allow_stale_cost_basis`` says otherwise —
    see ``pool.assert_promotable_cost_basis``. The escape stays out of
    ``promotion_content_sha256``: the candidate ids are already in the hash and the
    check is a pure function of them, so the same approval can never need the escape
    in one execution and not another."""
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
        # After the approval check, not before: a promotion that Thomas never approved should
        # say so first. Both refusals are absolute; this is only about which one an operator
        # reads when a selection fails on both counts.
        if not allow_stale_cost_basis:
            pool_store.assert_promotable_cost_basis(candidates)
    except MvpRuntimeError as exc:
        raise SystemExit(f"BLOCKED {exc.reason_code}: {exc.reason}")

    entries = []
    if keep_active:
        entries.extend(pool_store.load_active_pool(root).get("active_strategies") or [])
    existing_ids = {e.get("strategy_id") for e in entries}
    existing_cids = {e.get("candidate_id") for e in entries}
    display_ids: list[str] = []
    for c in candidates:
        if c["candidate_id"] in existing_cids:
            raise SystemExit(f"BLOCKED: candidate {c['candidate_id']} is already in the active pool")
        # The pool invariant requires a UNIQUE strategy_id per entry, but the FACTORY
        # restarts strategy_id at S001 every generation — so one promotion batch (or the
        # existing pool) routinely holds several distinct lineages that share a display
        # name. candidate_id is the true lineage key (routing records it, lifecycle groups
        # on it), so on a display-name collision we derive a unique, lineage-readable
        # id ``{strategy_id}-{generation_id}`` rather than refuse the whole batch;
        # globally-unique ids (the C7 import's) never collide and keep their name. A
        # residual collision — the same strategy_id AND generation twice, or no generation
        # to disambiguate — still fails closed rather than silently picking one entry.
        display_sid = c.get("strategy_id")
        if display_sid in existing_ids:
            gen = c.get("generation_id")
            derived = f"{display_sid}-{gen}" if gen else None
            if derived is None or derived in existing_ids:
                raise SystemExit(
                    f"BLOCKED: cannot assign a unique strategy_id for {display_sid} "
                    f"(candidate {c['candidate_id']}); {derived or display_sid} is already in the pool"
                )
            display_sid = derived
        existing_cids.add(c["candidate_id"])
        existing_ids.add(display_sid)
        display_ids.append(display_sid)
        entries.append({
            "strategy_id": display_sid,
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
        # The display ids actually installed — equal to the candidate strategy_ids except
        # where a cross-generation collision forced a unique ``{sid}-{generation}`` name.
        "promoted_display_ids": display_ids,
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
        # Recorded whether or not it fired, and with the basis each promoted candidate was
        # actually scored under. A pool entry outlives the argv that installed it, so "which
        # cost model is this lineage's evidence standing on" has to be answerable from the
        # ledger later rather than reconstructed from whoever ran the command.
        "stale_cost_basis_escape": bool(allow_stale_cost_basis),
        "cost_bases": [pool_store.cost_basis_of(c) for c in candidates],
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
    parser.add_argument("--allow-stale-cost-basis", action="store_true",
                        help="explicit escape: promote evidence scored under a cost model cheaper "
                             "than the venue charges (its expectancy is overstated; recorded as such)")
    parser.add_argument("--confirm", action="store_true", help="actually install; refused without it")
    args = parser.parse_args(argv)

    if args.list:
        try:
            candidates = pool_store.read_candidates(None)
        except MvpRuntimeError as exc:
            print(f"BLOCKED {exc.reason_code}: {exc.reason}")
            return EXIT_BLOCKED
        # Stated before the numbers, not after: this is the surface an operator reads to
        # decide a promotion, and the promotion gate is that operator — there is no
        # automated statistical threshold behind it.
        #
        # The bases are counted rather than assumed. The taker default moved from the ported
        # 2.5 bps to the venue's measured 5.0 and the take-profit exit became a maker fill, and
        # `backtest_evidence` is durable, so this store holds candidates scored under all of
        # them. Each is reported with the DIRECTION of its error, because that is the only
        # thing an operator can act on: an optimistic row's number is inflated, a conservative
        # row's is not, and the two need opposite amounts of suspicion.
        bases: dict[tuple[int, str], int] = collections.Counter(
            (q["cost_basis_rank"], q["cost_basis"])
            for q in (pool_store.candidate_quality(c) for c in candidates)
        )
        rank_label = {
            pool_store.COST_BASIS_RANK_CURRENT: "CURRENT     ",
            pool_store.COST_BASIS_RANK_CONSERVATIVE: "conservative",
            pool_store.COST_BASIS_RANK_OPTIMISTIC: "OPTIMISTIC  ",
            pool_store.COST_BASIS_RANK_UNRECORDED: "UNRECORDED  ",
        }
        print(f"NOTE: every R below is NET of costs, charged on both legs of every closed trade.")
        print(f"      Current model: {cost_mod.DEFAULT_TAKER_FEE_BPS} bps taker + "
              f"{cost_mod.DEFAULT_SLIPPAGE_BPS} bps slippage per fill (5.0 bps taker measured on")
        print("      this account 2026-07-26: 0.1291 USDT over ~258 USDT of fills), and "
              f"{cost_mod.DEFAULT_MAKER_FEE_BPS} bps")
        print("      maker on a take-profit exit — that one is Binance's PUBLISHED rate, not "
              "measured here.")
        if len(bases) > 1:
            print("      MIXED BASES in this list — these rows were NOT scored alike:")
            for (rank, basis), count in sorted(bases.items(), key=lambda kv: (kv[0][0], -kv[1])):
                print(f"        {count:4d}  {rank_label[rank]}  {basis}")
            print("      Rows are ranked by this tier FIRST, so cheaper-venue evidence sorts")
            print("      below evidence that paid the real rate regardless of its score.")
            blocked = sum(n for (rank, _), n in bases.items()
                          if rank not in pool_store.PROMOTABLE_COST_BASIS_RANKS)
            if blocked:
                print(f"      {blocked} row(s) are REFUSED at the promotion door "
                      "(CANDIDATE_COST_BASIS_STALE);")
                print("      re-mint the lineage at the current model, or pass "
                      "--allow-stale-cost-basis.")
        # The mixing is reported, but it is also fixable for the number that matters most:
        # the fee term is linear in the rate, so a candidate's expectancy at the CURRENT rate
        # is exactly derivable from what its evidence already records. `exp@` below is that
        # figure. What it cannot repair is named rather than glossed: win-rate, reward:risk
        # and the holdout verdict all need per-trade signs, and the store keeps aggregates.
        flipped = [
            c for c in candidates
            if (pool_store.candidate_quality(c)["expectancy_at_current_costs"] or 0) <= 0
            < (c.get("backtest_evidence") or {}).get("expectancy", 0)
        ]
        if flipped:
            print(f"      {len(flipped)} candidate(s) show a POSITIVE stored expectancy that is "
                  f"negative at the current rates (marked FLIPS below).")
            print("      win_rate and rr are NOT re-derivable — those still reflect the old rate.")
        print()
        # M4a: robustness stays the first-pass filter; within a verdict tier the
        # ranking then orders by win-rate + realized reward:risk, so the strongest
        # believable edges surface first for the promotion decision.
        for c in pool_store.rank_candidates(candidates):
            spec = c.get("strategy_spec") or {}
            evidence = c.get("backtest_evidence") or {}
            q = pool_store.candidate_quality(c)
            rr = "inf" if q["all_wins"] else ("-" if q["reward_risk"] is None else f"{q['reward_risk']:.2f}")
            at_now = q["expectancy_at_current_costs"]
            stored_exp = evidence.get("expectancy")
            if at_now is None:
                exp_now = " exp@now=-"
            elif isinstance(stored_exp, (int, float)) and stored_exp > 0 >= at_now:
                exp_now = f" exp@now={at_now:+.4f} FLIPS"
            else:
                exp_now = f" exp@now={at_now:+.4f}"
            # The tier rides on every row, not just in the header count: it is the FIRST sort
            # key, so a row sitting below a visibly weaker one has to be able to say why.
            basis_mark = (
                "" if q["cost_basis_rank"] == pool_store.COST_BASIS_RANK_CURRENT
                else f" basis={rank_label[q['cost_basis_rank']].strip()}"
            )
            print(f"{pool_store.candidate_id(c):26} {c.get('strategy_id'):8} "
                  f"{c.get('generation_id') or '-':8} "
                  f"{spec.get('strategy_family') or '-':26} score={c.get('champion_score')} "
                  f"verdict={q['verdict'] or '-':11} "
                  f"oos={q['holdout_status']:12} "
                  f"win_rate={q['win_rate']:.2f} rr={rr}({q['reward_risk_basis']}) "
                  f"closed={evidence.get('closed_count')} provenance={c.get('provenance')}"
                  f"{exp_now}{basis_mark}")
        return EXIT_OK

    if not args.strategy_ids:
        print("BLOCKED: --candidate-ids is required (or use --list)")
        return EXIT_USAGE
    selectors = [s.strip() for s in args.strategy_ids.split(",") if s.strip()]

    # Past `--list`, every remaining branch writes: `--request` stores an approval and audits it,
    # `--confirm` rewrites the active pool. Placed here rather than at the top of main so a
    # read-only listing stays runnable from anywhere.
    try:
        assert_not_foreign_root_run()
    except MvpRuntimeError as exc:
        print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return EXIT_BLOCKED

    if args.request:
        try:
            prepared = run_request(selectors=selectors, keep_active=args.keep_active,
                                   allow_stale_cost_basis=args.allow_stale_cost_basis)
        except MvpRuntimeError as exc:
            print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
            return EXIT_BLOCKED
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
        allow_stale_cost_basis=args.allow_stale_cost_basis,
    )
    door = summary["approval_id"] or "WITHOUT-APPROVAL ESCAPE"
    print(f"PROMOTED: {summary['promoted_candidate_ids']} "
          f"({summary['promoted_strategy_ids']}) -> active pool "
          f"({summary['pool_size']} strategies) [door: {door}]")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
