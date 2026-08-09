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

from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
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



def run_request(*, selectors: list[str], keep_active: bool, root: Path | None = None,
                now: str | None = None, allow_stale_cost_basis: bool = False,
                allow_duplicates: bool = False,
                allow_unrecorded_evidence_depth: bool = False,
                allow_quarantined_derivation: bool = False) -> dict:
    """Build + store + audit the R9 ask for this promotion (the trial_cli pattern)."""
    now = now or timeutil.utc_now_iso()
    prepared = promotion_mod.request_promotion(
        selectors, keep_active=keep_active, now=now, repo_root=root,
        allow_stale_cost_basis=allow_stale_cost_basis,
        allow_unrecorded_evidence_depth=allow_unrecorded_evidence_depth,
        allow_duplicates=allow_duplicates,
        allow_quarantined_derivation=allow_quarantined_derivation,
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
    allow_stale_cost_basis: bool = False, allow_unrecorded_evidence_depth: bool = False,
    allow_duplicates: bool = False, allow_oversized_pool: bool = False,
    allow_quarantined_derivation: bool = False, allow_reactivation: bool = False,
) -> dict:
    """Install the selected candidates into the active pool. Fail-closed.

    ``selectors`` are candidate ids (preferred) or unambiguous strategy ids — a
    strategy id shared by several generations refuses (``CANDIDATE_AMBIGUOUS``)
    instead of silently promoting the newest. C8b: requires either an APPROVED,
    unexpired, content-matching approval id or the explicit ``without_approval``
    escape; the door used is recorded on the ledger.

    Evidence scored under a cost model cheaper than the venue charges refuses with
    ``CANDIDATE_COST_BASIS_STALE`` unless ``allow_stale_cost_basis`` says otherwise —
    see ``pool.assert_promotable_cost_basis``. Evidence that cannot say how much market
    it replayed refuses with ``CANDIDATE_EVIDENCE_DEPTH_UNRECORDED`` unless
    ``allow_unrecorded_evidence_depth`` says otherwise; a KNOWN shallow window is ranked,
    never refused (see ``pool.assert_promotable_evidence_depth``). A batch that would put
    the same strategy in the pool twice under different rule hashes refuses with
    ``CANDIDATE_SEMANTIC_DUPLICATE`` unless ``allow_duplicates`` says otherwise — see
    ``pool.assert_no_semantic_duplicates``. A candidate minted by a derivation the live pool
    does not take refuses with ``CANDIDATE_DERIVATION_NOT_PROMOTABLE`` unless
    ``allow_quarantined_derivation`` says otherwise; a row that names no derivation at all is
    legacy and passes — see ``pool.assert_promotable_derivation``. A promotion that would leave the pool with more
    routable strategies than the lifecycle can ever judge refuses with
    ``POOL_SIZE_CAP_EXCEEDED`` / ``POOL_CONTEXT_CAP_EXCEEDED`` unless ``allow_oversized_pool``
    says otherwise — see ``pool.assert_pool_within_size_cap``. Every escape stays out of
    ``promotion_content_sha256``: the candidate ids are already in the hash and each check
    is a pure function of them, so the same approval can never need an escape in one
    execution and not another.

    The sizing cap is a pure function of the ids for the same reason the others are, with one
    wrinkle worth stating: in add mode it also reads the CURRENT pool, so a promotion approved
    while the pool had room can refuse later if the pool grew in between. That is the check
    working — the approval authorizes these candidates, never a pool shape measured at ask
    time — and the fix is to retire first, not to re-ask."""
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
        #
        # And this is the ONLY place they run on the execution path. `verify_promotion_approval`
        # above resolves identity alone — it recomputes the approval's content hash and judges
        # nothing else — because the escapes below are deliberately not part of that hash, so a
        # door that recomputes it cannot honour them. While it ran these too, every promotion
        # approved WITH an escape refused there before reaching this block. The gates live at
        # the ask (`promotion.request_promotion`) and here; not in between.
        if not allow_stale_cost_basis:
            pool_store.assert_promotable_cost_basis(candidates)
        # After the basis, because a row that records neither is more usefully reported as the
        # cheaper failure first: "scored under an unknown cost model" is the one an operator
        # can act on by re-minting, and re-minting fixes both. A KNOWN shallow window is not
        # checked here at all — only an unreadable one.
        if not allow_unrecorded_evidence_depth:
            pool_store.assert_promotable_evidence_depth(candidates)
        # Last, because it is the only one of the three that is about the POOL rather than
        # about the evidence: the other two ask whether a number can be believed, this one
        # asks whether the pool already holds this strategy under another rule hash.
        if not allow_duplicates:
            pool_store.assert_no_semantic_duplicates(
                candidates,
                incumbents=pool_store.pool_candidate_records(root) if keep_active else None,
            )
        # And last of all, the axis that is about neither the evidence nor the pool but about
        # the ROW: what minted it. Ordered last because it is the only one of the four that
        # refuses nothing today — it stands here so that an experimental derivation cannot
        # reach the live pool through the ordinary door on the day someone starts minting one.
        if not allow_quarantined_derivation:
            pool_store.assert_promotable_derivation(candidates)
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
            # Per-regime backtest figures, copied onto the entry for the same reason
            # `champion_score` is: the ROUTER must be able to read them, and the router reads the
            # pool. Making it look candidates up by `candidate_id` instead would put a 359-row
            # store — one whose reader deliberately RAISES on damage, because it backs a
            # promotion an operator signs — on the path of every 15-minute cycle, so a corrupt
            # candidate line would stop routing. The pool is the authority on what trades; this
            # keeps it self-contained.
            #
            # The raw numbers, never a derived exclusion list. `paper.regime_admits` owns the
            # rule and applies it at read time, so moving its threshold later cannot leave stale
            # labels behind — the defect `pool.candidate_quality` already had to fix once, where
            # verdicts written at mint time survived the rule that produced them.
            "regime_evidence": ((c.get("backtest_evidence") or {}).get("regime_breakdown") or {}).get("per_regime"),
            "promoted_by": promoted_by,
            "promoted_at": now,
        })

    # The pool-sizing cap, checked on the MERGED result rather than on the batch: in add
    # mode the incumbents are what make a two-candidate promotion oversized, so judging the
    # batch alone would pass every promotion that ever mattered. Last of the four guards
    # because it is the only one about the pool's SHAPE — the others ask whether a candidate
    # is believable, this one asks whether the pool it would join can still be judged.
    if not allow_oversized_pool:
        try:
            pool_store.assert_pool_within_size_cap(entries)
        except MvpRuntimeError as exc:
            raise SystemExit(f"BLOCKED {exc.reason_code}: {exc.reason}")

    # Beside the size cap because it is the other question about the ASSEMBLED pool rather
    # than about a candidate — and the last one, because it is the only guard here that is
    # about the entries this install would leave BEHIND rather than the ones it adds.
    # Replace mode rebuilds every entry with a hardcoded PAPER_ACTIVE, so re-listing the
    # incumbents to drop one brings back everything the lifecycle had terminated. Recorded
    # either way below, and read `pool.assert_no_silent_reactivation` for why the approval
    # cannot cover this and the operator therefore must.
    reactivations = pool_store.silent_reactivations(entries, root=root)
    if not allow_reactivation:
        try:
            pool_store.assert_no_silent_reactivation(entries, root=root)
        except MvpRuntimeError as exc:
            raise SystemExit(f"BLOCKED {exc.reason_code}: {exc.reason}")

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
        # How large a book the pool it just installed can actually fill under the directional
        # cap. On the LEDGER, not only on stdout: with one routable strategy per context a
        # spec's direction is fixed here, so this promotion is the moment the book's achievable
        # composition was decided — and a record that cannot answer "why did the book stop at
        # eight" is one an operator has to reconstruct from the pool file by hand.
        "directional_capacity": pool_store.routable_directional_capacity(entries),
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
        "duplicate_escape": bool(allow_duplicates),
        "oversized_pool_escape": bool(allow_oversized_pool),
        "cost_bases": [pool_store.cost_basis_of(c) for c in candidates],
        # The window each promoted row's evidence stands on, recorded for the same reason as
        # the basis beside it — and here it carries more weight, because a SHALLOW row is
        # deliberately not refused. A shallow verdict is a floor rather than an inflated
        # number, so it is attributed instead of blocked (see `pool`'s depth block), and
        # attribution that is not written down is just a listing nobody kept.
        "evidence_depths": [pool_store.evidence_depth_of(c) for c in candidates],
        "unrecorded_evidence_depth_escape": bool(allow_unrecorded_evidence_depth),
        # How each promoted row was minted, and whether the door that reads it was stepped
        # around. Recorded for the same reason the basis and the depth are: an escape that
        # leaves no trace is indistinguishable, a month later, from a door that never refused.
        "derivations": [c.get("derivation_type") for c in candidates],
        "quarantined_derivation_escape": bool(allow_quarantined_derivation),
        # Who came back from a terminal status, and from which. Recorded whether or not the
        # escape fired, like the bases and depths above: the reactivation is the part of the
        # effect the approval's content hash cannot name, so the ledger is the only place it
        # is written down at all.
        "reactivation_escape": bool(allow_reactivation),
        "reactivated": reactivations,
        # Every review this promotion did NOT get, in one field.
        #
        # Each escape above is already recorded on its own, and individually each is a
        # defensible operator call. What no record answered is the question an auditor
        # actually asks — *what survived* — because answering it meant knowing all eight
        # flags exist and reading them together. The statistical judgment (verdict, holdout,
        # selection tier, expectancy) is deliberately not a gate here at all: it is the
        # operator reading the ranked `--list` surface plus Thomas's approval of the exact
        # candidate ids. So with the approval escaped as well, a promotion can reach the pool
        # having met nothing but pool structural validation — and read, in the ledger,
        # exactly like one that cleared everything.
        "reviews_skipped": [
            name for name, skipped in (
                ("thomas_approval", without_approval and approval_id is None),
                ("cost_basis", allow_stale_cost_basis),
                ("evidence_depth", allow_unrecorded_evidence_depth),
                ("semantic_duplicates", allow_duplicates),
                ("pool_size_cap", allow_oversized_pool),
                ("quarantined_derivation", allow_quarantined_derivation),
                ("silent_reactivation", allow_reactivation and bool(reactivations)),
            ) if skipped
        ],
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
    parser.add_argument("--allow-duplicates", action="store_true",
                        help="explicit escape: promote a candidate that is the same strategy as "
                             "another selected candidate or an incumbent under a different rule hash")
    parser.add_argument("--allow-oversized-pool", action="store_true",
                        help="explicit escape: install a pool above the routable-strategy or "
                             "per-context cap (a pool nothing in it can be auto-demoted from)")
    parser.add_argument("--allow-stale-cost-basis", action="store_true",
                        help="explicit escape: promote evidence scored under a cost model cheaper "
                             "than the venue charges (its expectancy is overstated; recorded as such)")
    parser.add_argument("--allow-unrecorded-evidence-depth", action="store_true",
                        help="explicit escape: promote evidence that records no replay window, so "
                             "how much market its verdict was earned on cannot be read and cannot "
                             "be re-derived (recorded as such). A KNOWN shallow window needs no "
                             "escape — it is ranked, not refused.")
    parser.add_argument("--allow-quarantined-derivation", action="store_true",
                        help="explicit escape: promote a candidate minted by a derivation the "
                             "live pool does not take (recorded as such). Refuses nothing on "
                             "today's store — every derivation minted so far is promotable.")
    parser.add_argument("--allow-reactivation", action="store_true",
                        help="explicit escape: let this install return terminally SUSPENDED / "
                             "ARCHIVED members to trading (recorded, with who and from what). "
                             "Replace mode rebuilds every entry as PAPER_ACTIVE, so re-listing "
                             "the incumbents to drop one reactivates the rest; the approval's "
                             "content hash cannot name that, which is why the operator must.")
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
        # The second axis on which two rows can be incomparable: the WINDOW each replayed.
        # `bars_replayed` has been in the evidence all along and nothing read it, so a row
        # scored over 500 bars and one scored over 2000 sat here looking equally examined —
        # while the verdict beside them is counted over trades, and a shorter window has
        # fewer. Reported rather than gated, because the error runs against the candidate:
        # see `pool.EVIDENCE_DEPTH_RANK_*` for why this tier ranks instead of refusing.
        depths: dict[tuple[int, str], int] = collections.Counter(
            (q["evidence_depth_rank"], q["evidence_depth"])
            for q in (pool_store.candidate_quality(c) for c in candidates)
        )
        depth_label = {
            pool_store.EVIDENCE_DEPTH_RANK_FULL: "FULL      ",
            pool_store.EVIDENCE_DEPTH_RANK_SHALLOW: "SHALLOW   ",
            pool_store.EVIDENCE_DEPTH_RANK_UNRECORDED: "UNRECORDED",
        }
        timeframes = sorted({
            str((c.get("strategy_spec") or {}).get("timeframe"))
            for c in candidates
            if (c.get("strategy_spec") or {}).get("timeframe")
        })
        print("NOTE: each verdict below is partly a statement about how much market that row "
              "replayed —")
        print("      sample adequacy, walk-forward consistency and holdout confirmation are all "
              "counted")
        print("      over trades, and a shorter window has fewer of them.")
        for timeframe in timeframes:
            print(f"      A candidate minted now at {timeframe} would carry: "
                  f"{pool_store.current_evidence_depth(timeframe)}")
        if len(depths) > 1:
            print("      MIXED WINDOW DEPTHS in this list — these rows were NOT shown the same "
                  "market:")
            for (rank, depth), count in sorted(depths.items(), key=lambda kv: (kv[0][0], -kv[1])):
                print(f"        {count:4d}  {depth_label[rank]}  {depth}")
            print("      A SHALLOW row's verdict is a FLOOR, not a judgement on the strategy: "
                  "re-scoring")
            print("      25 specs from 500 to 2000 bars moved them from 0 ROBUST / 20 FRAGILE to "
                  "12 ROBUST /")
            print("      12 PROVISIONAL / 1 FRAGILE. So shallow rows are NOT refused at the "
                  "promotion door —")
            print("      they rank below equal verdicts, and the depth behind every promoted row "
                  "is recorded")
            print("      on the ledger. Re-mint the lineage at the current window before reading "
                  "a shallow")
            print("      FRAGILE as a no.")
        # The one depth that IS a door, counted like the stale-basis block above it. Refused
        # rather than ranked because "a shallow verdict errs against the candidate" is a claim
        # about a window you can see, and no arithmetic can recover one that was never written
        # down — unlike a cost basis, where `exp@` above repairs the number that matters.
        unreadable = sum(n for (rank, _), n in depths.items()
                         if rank not in pool_store.PROMOTABLE_EVIDENCE_DEPTH_RANKS)
        if unreadable:
            print(f"      {unreadable} row(s) record NO window at all and are REFUSED at the "
                  "promotion door")
            print("      (CANDIDATE_EVIDENCE_DEPTH_UNRECORDED); re-mint the lineage through the "
                  "factory, or")
            print("      pass --allow-unrecorded-evidence-depth. A merely SHALLOW row needs no "
                  "escape.")
        # The axis that refuses on how a row was MADE rather than on what it measured, reported
        # here for the same reason as the one above: an operator picking from this list should
        # never meet a refusal for the first time at the door. Silent on today's store, where
        # every derivation minted so far is promotable.
        quarantined = [
            pool_store.candidate_id(c) for c in candidates
            if "derivation_type" in c
            and c.get("derivation_type") not in pool_store.PROMOTABLE_DERIVATION_TYPES
        ]
        if quarantined:
            print(f"      {len(quarantined)} row(s) were minted by a derivation the live pool "
                  "does NOT take and are")
            print("      REFUSED at the promotion door (CANDIDATE_DERIVATION_NOT_PROMOTABLE); "
                  "these rows exist to")
            print("      accrue evidence, not to trade. Pass --allow-quarantined-derivation "
                  "only deliberately.")
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
        # Clones the promotion door WILL refuse, said here so the refusal is not the first
        # an operator hears of it — they are picking from this list.
        dupes = pool_store.semantic_duplicate_groups(candidates)
        if dupes:
            print(f"      {len(dupes)} group(s) are the SAME strategy under different rule hashes;")
            print("      the promotion door refuses these (CANDIDATE_SEMANTIC_DUPLICATE):")
            for g in dupes[:8]:
                print(f"        {'/'.join(g['strategy_ids'])}  matched on {g['match']}")
        # And the ones it will NOT refuse, because they are a judgement rather than a proof:
        # same window, same trade counts, R differing in the last decimals. Almost certainly
        # one strategy wearing two rules — but "almost" is why this reports instead of gating.
        near = pool_store.near_duplicate_groups(candidates)
        if near:
            print(f"      {len(near)} group(s) traded ALMOST identically (same window, same "
                  "trade counts,")
            print("      R differing only in the last decimals) — not refused, worth a look:")
            # Collapsed by display name: strategy_id restarts every generation, so several
            # distinct lineage PAIRS routinely render as the same "S005/S006" line. Printing
            # it four times reads like a bug; the count says what is actually there.
            by_name = collections.Counter("/".join(g["strategy_ids"]) for g in near)
            for name, count in by_name.most_common(8):
                print(f"        {name}" + (f"  ({count} lineage pairs)" if count > 1 else ""))
        print()
        # M4a: robustness stays the first-pass filter; within a verdict tier the
        # ranking then orders by win-rate + realized reward:risk, so the strongest
        # believable edges surface first for the promotion decision.
        # Counted once over the population being listed, and handed to every row — the same
        # number `rank_candidates` just sorted on. Reading it per row would count a store the
        # order did not use.
        attempts = pool_store.attempts_by_context(candidates)
        for c in pool_store.rank_candidates(candidates):
            spec = c.get("strategy_spec") or {}
            evidence = c.get("backtest_evidence") or {}
            q = pool_store.candidate_quality(
                c, attempts=attempts.get(pool_store.search_context_key(spec))
            )
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
            # Same rule for the window, and for the same reason: the depth tier is a sort key,
            # so a row sitting below a same-verdict neighbour has to be able to say why.
            depth_mark = (
                "" if q["evidence_depth_rank"] == pool_store.EVIDENCE_DEPTH_RANK_FULL
                else f" depth={depth_label[q['evidence_depth_rank']].strip()}"
            )
            # And for the sort key that knows this row has siblings. `t` alone would read as a
            # strength; it is only a strength relative to the bar N attempts demand, so both
            # numbers go on the row or neither is actionable. `sel=unmeasured` is every
            # candidate minted before `stdev_r` — an absent spread, not a weak one.
            if q["expectancy_t"] is None or q["attempts_in_context"] is None:
                sel_mark = " sel=unmeasured"
            else:
                sel_mark = (f" t={q['expectancy_t']:+.2f}/{q['selection_adjusted_z']:.2f}"
                            f"(n={q['attempts_in_context']})")
            print(f"{pool_store.candidate_id(c):26} {c.get('strategy_id'):8} "
                  f"{c.get('generation_id') or '-':8} "
                  f"{spec.get('strategy_family') or '-':26} score={c.get('champion_score')} "
                  f"verdict={q['verdict'] or '-':11} "
                  f"oos={q['holdout_status']:12} "
                  f"win_rate={q['win_rate']:.2f} rr={rr}({q['reward_risk_basis']}) "
                  f"closed={evidence.get('closed_count')} provenance={c.get('provenance')}"
                  f"{exp_now}{basis_mark}{depth_mark}{sel_mark}")
        return EXIT_OK

    if not args.strategy_ids:
        print("USAGE: --candidate-ids is required (or use --list)")
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
            prepared = run_request(
                selectors=selectors, keep_active=args.keep_active,
                allow_stale_cost_basis=args.allow_stale_cost_basis,
                allow_unrecorded_evidence_depth=args.allow_unrecorded_evidence_depth,
                allow_duplicates=args.allow_duplicates,
                allow_quarantined_derivation=args.allow_quarantined_derivation)
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
        print("USAGE: --promoted-by and --reason are required to execute a promotion")
        return EXIT_USAGE
    if not args.confirm:
        print("BLOCKED: promotion requires --confirm (a good backtest is never auto-promotion)")
        return EXIT_BLOCKED

    summary = run_promotion(
        selectors=selectors,
        promoted_by=args.promoted_by, reason=args.reason, keep_active=args.keep_active,
        approval_id=args.approval_id, without_approval=args.without_approval,
        allow_stale_cost_basis=args.allow_stale_cost_basis,
        allow_unrecorded_evidence_depth=args.allow_unrecorded_evidence_depth,
        allow_duplicates=args.allow_duplicates,
        allow_oversized_pool=args.allow_oversized_pool,
        allow_quarantined_derivation=args.allow_quarantined_derivation,
        allow_reactivation=args.allow_reactivation,
    )
    door = summary["approval_id"] or "WITHOUT-APPROVAL ESCAPE"
    print(f"PROMOTED: {summary['promoted_candidate_ids']} "
          f"({summary['promoted_strategy_ids']}) -> active pool "
          f"({summary['pool_size']} strategies) [door: {door}]")
    # The ledger has carried every escape separately for a while; what nobody could read off
    # it was the total. Printed at the moment of the install rather than only recorded,
    # because the operator holding the argv is the last reader who can still stop.
    if summary["reactivated"]:
        print(f"NOTE: returned {len(summary['reactivated'])} terminal member(s) to trading — "
              + ", ".join(f"{r['strategy_id']} (was {r['from_status']})"
                          for r in summary["reactivated"]))
    if summary["reviews_skipped"]:
        print(f"NOTE: this promotion skipped {len(summary['reviews_skipped'])} review(s): "
              + ", ".join(summary["reviews_skipped"])
              + ". Recorded on the ledger; the statistical judgment (verdict, holdout, "
                "selection tier, expectancy) is never a gate here — it is the ranked --list "
                "surface plus Thomas's approval of the exact candidate ids.")
    # Said here because this is the moment the pool's directional composition changes, and
    # because the consequence is invisible everywhere else until positions fail to open: with
    # one routable strategy per context, a spec's direction is fixed at promotion time, so a
    # lopsided pool cannot fill its own slots under the directional cap. NOT a refusal — the cap
    # only ever declines, so the outcome is under-utilisation rather than risk, and blocking the
    # promotion would forbid assembling a pool in any order but alternating.
    capacity = summary.get("directional_capacity")
    if isinstance(capacity, dict) and capacity.get("cap_binds"):
        print(f"NOTE: this pool routes {capacity['routable_contexts']} contexts "
              f"({capacity['long_contexts']} long / {capacity['short_contexts']} short) but the "
              f"directional cap (±{capacity['skew_cap']}) lets at most "
              f"{capacity['reachable_book']} positions be open at once. Promote the other "
              f"direction to use the rest.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
