"""C7 strategy pool — the public face of the active pool the cycle routes against and of the
candidate store the C8 promotion flow consumes. What is decided here: the routing views, the
resolution of an operator's candidate selectors, and the backlog.

The pool's other roles live beside it, and every public name of theirs is re-exported here as the same
object, so callers keep reading them as ``pool.<name>``:

- :mod:`pool_state` (crypto PR7e-7): the two files' paths and reads, the pool's install door, the
  candidates' append door, and what each of them checks;
- :mod:`live_tier` (PR7e-8): the live tier, its disarm door included;
- :mod:`pool_transitions` (PR7e-9): the status transitions;
- :mod:`pool_admission` (PR7e-10): the promotion door's gates and the size cap.

The status transitions and the live tier's disarm door are the two writers that rewrite the stored
pool in the cycle.

**Some fields on a stored row are SNAPSHOTS, not answers.** Both stores are append-only —
a candidate row's ``record_sha256`` covers the whole row, so correcting a label in place
would not merely violate a convention, it would read as tampering. The consequence is that
any judgment written at write time is frozen at the rule that produced it, and this runtime
has changed those rules more than once. Measured 2026-08-08:

- Stored ``robustness.verdict`` / ``robustness.holdout_status`` across 1,841 candidate rows
  read ROBUST=83 and CONFIRMED=237. Recomputed under today's rule: **0 and 0.**
- The 41 import rows carry ``status`` PAPER_ACTIVE=25 / SUSPENDED=16, snapshotted at import.
  The pool — the authority on membership — has all 41 of those members SUSPENDED.
- Of 94 pool entries, the 34 carrying ``robustness_verdict: ROBUST`` are import-lineage
  (no ``candidate_id``) and every one is SUSPENDED, from a label two rule vintages old. The
  53 factory-promoted entries — including all 5 that are PAPER_ACTIVE — carry **no**
  robustness field at all.

None of that is a live defect, and the reason is worth stating because it is the thing a
future change can break: **no runtime decision reads those fields.**
:func:`candidate_ranking.candidate_quality` recomputes the verdict and the holdout status from
the stored COMPONENTS on every read, membership comes from the pool rather than from a
candidate row's ``status``, and the promotion door deliberately copies raw per-regime numbers
onto a pool entry instead of a derived label — see the comment at the entry construction in
``scripts/promote_strategy_candidates.py``, which names this exact defect as one
`candidate_quality` already had to fix once.

So the pool entries that carry no robustness fields are the CORRECT ones, and the 34 that do
are the residue of a one-time import. Copying a verdict onto a routable entry would not fill
a gap; it would reintroduce the defect. What was missing is that this held by accident —
nothing checked it — which `tests/test_mvp_runtime_crypto_stored_snapshots.py` now does.

:data:`STORED_SNAPSHOT_FIELDS` names them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..errors import ToolError
from . import market_data
from .candidate_identity import entry_attribution_keys
from .candidate_identity import candidate_id, derive_candidate_id  # noqa: F401 — re-exported:
# `pool`'s many callers read the id rule as `pool.candidate_id`, and the rule itself
# moved to a leaf so `factory` no longer needs a module-level edge into `pool`.
# The ranking view and the two comparability tiers moved to `candidate_ranking` (strategy) in crypto
# PR7e-1. This module's backlog and routing read them (the doors in `pool_admission` import them
# directly since PR7e-10), and every public name is re-exported
# here, as the same object, for the callers that read them as `pool.<name>`. Of the private helpers,
# only the two this module calls are imported: a patch on `pool` for any of the others would miss the
# ranking that reads it, and without the name here such a patch fails loudly instead.
from .candidate_ranking import (  # noqa: F401
    COST_BASIS_RANK_CONSERVATIVE, COST_BASIS_RANK_CURRENT, COST_BASIS_RANK_OPTIMISTIC,
    COST_BASIS_RANK_UNRECORDED, EDGE_COST_BASIS_NET, EDGE_COST_BASIS_UNRECORDED,
    EVIDENCE_DEPTH_RANK_FULL, EVIDENCE_DEPTH_RANK_SHALLOW, EVIDENCE_DEPTH_RANK_UNRECORDED,
    EVIDENCE_DEPTH_REPLAYED, EVIDENCE_DEPTH_TOLERANCE, EVIDENCE_DEPTH_UNRECORDED, _as_float,
    _is_number, attempt_context_key, attempts_by_context, candidate_quality, cost_basis_of,
    cost_basis_rank, current_cost_basis, current_evidence_depth, evidence_depth_of,
    evidence_depth_rank, expectancy_at, expected_replayed_bars, pooled_context_keys,
    rank_candidates, search_context_key,
)
# The live tier (which entries may spend real money, what each LIVE arm stands on, and the disarm door,
# the one automatic writer of the tier) moved to `live_tier` (decision) in crypto PR7e-8. Every public
# name is re-exported here, as the same object, for the callers that read them as `pool.<name>`. Its
# private helper `_spec_rule_hash` was imported here for `rule_hashes_of` until that moved to
# `pool_admission` (PR7e-10), which imports it itself; nothing here calls it now, so it stays off. A
# patch on `pool` for any of these names reaches only the code that reads it through `pool`. The tier's
# own functions read `live_tier`'s names, so a test that means to reach them patches `live_tier` too.
from .live_tier import (  # noqa: F401
    LIVE_TIER_APPROVAL_FIELD, LIVE_TIER_FIELD, LIVE_TIER_LIVE, LIVE_TIER_OBSERVATION, LIVE_TIERS,
    disarm_live_tier, entry_live_tier, live_arm_approvals, live_arm_entries, live_arm_unsound,
    live_routable_strategy_ids,
)
from .paper import OCCUPYING_STATUSES
# The promotion door's gates (the tier and derivation doors, the checks against the incumbents, the
# observation tier's bar and cap, one routed rule per lineage, the entries a promotion leaves behind)
# and the size cap with the capacity it stands on moved to `pool_admission` (decision) in crypto
# PR7e-10, with the lifecycle window the backlog below reads too. Every public name is re-exported
# here, as the same object, for the callers that read them as `pool.<name>`; the backlog reads the
# promotable sets and the window through these bindings. The private `_observation_holdout_term` is
# not imported: nothing here calls it, and a patch on `pool` for it would miss the entry bar.
from .pool_admission import (  # noqa: F401
    FAST_ROUTING_TIMEFRAMES, LIFECYCLE_MIN_WINDOW_TRADES, MAX_ROUTABLE_PER_CONTEXT,
    MAX_ROUTABLE_PER_CONTEXT_FAST, MAX_ROUTABLE_STRATEGIES, OBSERVATION_FAMILY_CAP,
    OBSERVATION_MIN_BACKTEST_CLOSED, POOL_RULE_ALREADY_ROUTED, PROMOTABLE_COST_BASIS_RANKS,
    PROMOTABLE_DERIVATION_TYPES, PROMOTABLE_EVIDENCE_DEPTH_RANKS, assert_family_cap,
    assert_no_cluster_siblings, assert_no_semantic_duplicates, assert_no_silent_reactivation,
    assert_observation_entry_bar, assert_pool_within_size_cap, assert_promotable_cost_basis,
    assert_promotable_derivation, assert_promotable_evidence_depth, assert_rule_not_routed,
    behavioural_fingerprint, canonical_rule_form, max_routable_per_context, near_duplicate_groups,
    pool_candidate_records, reactivated_candidate_ids, replaced_entries, routable_context_map,
    routable_directional_capacity, rule_hashes_of, same_rule_entries, semantic_duplicate_groups,
    silent_reactivations,
)
# The two files' paths and reads, the install and append doors, and what each of them checks moved
# to `pool_state` (decision) in crypto PR7e-7, so that the roles still here can move out without an
# import cycle. Every public name is re-exported here, as the same object, for the callers that read
# them as `pool.<name>`, and the code still in this module reads them through these bindings, so a
# patch on `pool` reaches that code. It does not reach the code that left: `pool_state`'s own functions,
# the live tier's disarm door and the status transitions read their own modules' names. Neither private
# name is imported: a patch on `pool` for either would miss the code in `pool_state` that reads it, and
# without the name here it fails loudly.
from .pool_state import (  # noqa: F401
    CANDIDATES_FILENAME, DERIVATION_TYPES, POOL_FILENAME, append_candidates, assert_pool_identity_unique,
    candidates_path, install_active_pool, load_active_pool, pool_path, read_candidates,
    read_pool_to_disarm, validate_candidate_lineage,
)
# The status transitions (the lifecycle's decisions written onto the stored pool, and the stale-decision
# rule they apply) moved to `pool_transitions` (decision) in crypto PR7e-9. The three public names are
# re-exported here, as the same objects, for the callers that read them as `pool.<name>`. The private
# `_stale_decision` is not imported: a patch on `pool` for it would miss the write that reads it, and
# without the name here it fails loudly.
from .pool_transitions import LIFECYCLE_DECISION_STALE, apply_status_decisions, update_statuses  # noqa: F401
from .robustness import HOLDOUT_CONFIRMED, ROBUST
from .strategy import StrategySpec
# `admission_evidence` is re-exported: the promotion door, the signal probe and every replay read it
# as `pool.admission_evidence`. It moved to the artifact's leaf, which hashes the projection (PR3a).
# `ARTIFACT_SHA256_FIELD` is kept for its readers too: since the live tier moved out (PR7e-8) nothing
# here uses it, but `live_route` and two tests read it as `pool.ARTIFACT_SHA256_FIELD`.
from .strategy_artifact import ARTIFACT_SHA256_FIELD, admission_evidence  # noqa: F401


# Pool-entry fields that are PROVENANCE — what a rule said when the row was written — which
# no decision surface may read as an input. See the module docstring for what each currently
# says and what it should say.
#
# Deliberately NOT here: ``robustness_score`` and ``trades_per_parameter``. Those are
# MEASUREMENTS, and a measurement survives a rule change — `candidate_quality` feeds
# them back into `classify_verdict` on every read, which is the whole mechanism that makes
# the labels disposable. Listing them would forbid the recompute it is protecting.
#
# The candidate-row equivalents live nested under ``backtest_evidence.robustness`` and are
# reachable only through `candidate_ranking.candidate_quality`, which recomputes rather than
# reads them back. That is why it is a function and not a dictionary lookup at each call site,
# and `test_only_candidate_quality_reads_the_stored_robustness_block` pins it as the sole reader
# of that block.
STORED_SNAPSHOT_FIELDS = frozenset({
    "robustness_verdict",
    "robustness_warnings",
})


# --- candidate identity (single source) ----------------------------------------
# `candidate_identity.py` owns the id rule now — a leaf both this module and `factory`
# import, which is what dissolved their module cycle. Re-exported at the top of this file.


def resolve_candidates(selectors: list[str], root: Path | None = None) -> list[dict[str, Any]]:
    """Resolve operator selectors to candidate records, fail-closed.

    A selector is a ``candidate_id`` (exact) or a ``strategy_id`` (convenience). A
    strategy_id matching candidates from more than one lineage refuses with
    ``CANDIDATE_AMBIGUOUS`` — never silently the newest — and an unmatched selector
    refuses with ``UNKNOWN_CANDIDATE``. Returned records are stamped with their
    ``candidate_id``; re-appends of the same lineage collapse latest-wins."""
    by_cid: dict[str, dict[str, Any]] = {}
    for record in read_candidates(root):
        cid = candidate_id(record)
        by_cid[cid] = {**record, "candidate_id": cid}

    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    ambiguous: dict[str, list[str]] = {}
    for selector in selectors:
        if selector in by_cid:
            resolved.append(by_cid[selector])
            continue
        matches = [r for r in by_cid.values() if r.get("strategy_id") == selector]
        if not matches:
            missing.append(selector)
        elif len(matches) > 1:
            ambiguous[selector] = sorted(r["candidate_id"] for r in matches)
        else:
            resolved.append(matches[0])
    if missing:
        raise ToolError("UNKNOWN_CANDIDATE", f"unknown candidate selectors: {missing}")
    if ambiguous:
        raise ToolError(
            "CANDIDATE_AMBIGUOUS",
            f"strategy ids matching multiple lineages, use candidate ids: {ambiguous}",
        )
    seen: set[str] = set()
    for record in resolved:
        if record["candidate_id"] in seen:
            raise ToolError("DUPLICATE_SELECTOR", f"candidate selected twice: {record['candidate_id']}")
        seen.add(record["candidate_id"])
    return resolved


def routable_strategy_ids(pool: Mapping[str, Any]) -> set[str]:
    """Every strategy id the pool can still route, by ``OCCUPYING_STATUSES``.

    The set the drawdown baseline's re-check is evaluated against: an outcome may leave the
    breaker's window only if the lineage that produced it is **not** in here, so re-promoting a
    retired strategy returns its losses to the drawdown without anyone re-registering anything.

    Deliberately membership-by-status and not by ``strategy_spec``, unlike
    :func:`pool_admission.routable_context_map`. That one answers "which slot does this compete for",
    which needs the spec; this one answers "could this trade again at all", and a spec-less entry
    that is still PAPER_ACTIVE has to count as YES — the safe direction here is whichever one
    keeps a loss inside the baseline. An empty pool honestly returns an empty set; **"the pool
    could not be read" is the caller's to represent and must never arrive as this**, because an
    empty set means every named lineage is retired and would release the whole exclusion."""
    return {
        str(entry.get("strategy_id"))
        for entry in (pool.get("active_strategies") or [])
        if isinstance(entry, Mapping)
        and entry.get("status") in OCCUPYING_STATUSES
        and entry.get("strategy_id")
    }


def routable_lineage_keys(pool: Mapping[str, Any]) -> set[str]:
    """Every lineage key an entry the pool can still route accepts
    (`candidate_identity.entry_attribution_keys`): :func:`routable_strategy_ids` by lineage, the set
    a lineage-sealed drawdown rebase is re-checked against (PR3b-3). The same membership, and the
    same rule: "the pool could not be read" is the caller's to represent, never an empty set."""
    return {
        key
        for entry in (pool.get("active_strategies") or [])
        if isinstance(entry, Mapping)
        and entry.get("status") in OCCUPYING_STATUSES
        and entry.get("strategy_id")
        for key in entry_attribution_keys(entry)
    }


def routable_contexts(pool: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Distinct ``(symbol, timeframe)`` pairs the active pool can route on.

    One pair per ``(symbol_scope entry, timeframe)`` — every symbol a strategy is
    scoped to, exactly what :func:`paper.route_entries` now matches on — so a
    fan-out proposes a cycle for every context a strategy could fire in (a
    multi-symbol strategy contributes each of its symbols) and none where it never
    could. Non-occupying or spec-less entries contribute nothing. Deduplicated and
    sorted for a stable, deterministic cycle order."""
    return sorted(context_scores(pool))


def context_scores(pool: Mapping[str, Any]) -> dict[tuple[str, str], float]:
    """``(symbol, timeframe)`` → the best ``champion_score`` any routable strategy there carries.

    The same set :func:`routable_contexts` returns, with the evidence attached. It exists because
    the live leg's slots are scarce — two open positions, one per symbol — while a fan-out visits
    twenty contexts, so the FIRST context to produce a route takes a slot and the rest are refused
    on capacity. Visiting in sorted order made that arbitration alphabetical: `BNBUSDT` before
    `BTCUSDT` before `ETHUSDT`, every fire, whatever any of them actually signalled.

    A score is not a signal — the strategy that scored best may propose nothing this bar — so this
    does not pick the winner, and nothing here promises the globally best entry wins. What it does
    is replace an ordering that correlates with nothing by one that correlates with the evidence
    the pool was promoted on. Picking the true best would mean evaluating every context before
    executing any of them, which is a second pass over the whole fan-out and its own decision.

    A missing or unreadable score reads as 0.0 rather than being dropped: the context must still
    be visited, it simply has nothing to argue for going first."""
    scores: dict[tuple[str, str], float] = {}
    for entry in pool.get("active_strategies") or []:
        if entry.get("status") not in OCCUPYING_STATUSES or not entry.get("strategy_spec"):
            continue
        spec = StrategySpec.from_dict(entry["strategy_spec"])
        score = _as_float(entry.get("champion_score"))
        for scoped_symbol in spec.symbol_scope:
            key = (str(scoped_symbol), str(spec.timeframe))
            scores[key] = max(scores.get(key, 0.0), score)
    return scores


def as_pool_entry_for_replay(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """A candidate seen as the pool entry it would become, for replay ONLY.

    Carries the admission evidence and nothing else a promotion would add — no status, no
    lifecycle counters, no ``promoted_at``. It is an argument to a pure walk, never a row to
    write. An entry that already carries its own evidence is returned untouched, so probing
    a pooled lineage measures the pool, not a reconstruction of it."""
    if candidate.get("regime_evidence") is not None or candidate.get("distribution_reference") is not None:
        return dict(candidate)
    return {**candidate, **admission_evidence(candidate)}


# How many promotable lineages may wait before the daily board says so. Mirrors the
# proposer's unreviewed-family cap (M4b) in intent and differs in effect: that cap makes
# a fire SKIP, this one only speaks. Nothing here refuses anything — the promotion door
# stays exactly as manual as it was.
PROMOTION_BACKLOG_ALERT_THRESHOLD = 5

# The axes `promotable_backlog` reports its refusals under, in the order the loop applies them.
#
# Ordered, and the order is the contract: each judged row is charged to the FIRST axis that
# drops it, so these buckets plus the promotable count partition `candidates_read` exactly.
# Read top to bottom, they are also the chain the promotion door applies, which is why a new
# filter must be added HERE as well as in the loop — an axis that refuses without a bucket is
# a row that leaves the partition, and the sum stops adding up with nothing saying which
# filter took it.
#
# `holdout_other` has no filter of its own: it catches a holdout status this list does not
# name, so a new state added to `robustness` surfaces as an unread bucket instead of being
# folded into a label that does not describe it.
BACKLOG_REFUSAL_AXES = (
    "already_active",
    "derivation",
    "cost_basis",
    "evidence_depth",
    "holdout_insufficient",
    "holdout_contradicted",
    "holdout_unconfirmed",
    "holdout_other",
    "verdict",
    "expectancy",
    "lineage_already_counted",
    "unjudgeable",
)


# How long a lineage may take to reach the lifecycle window
# (`pool_admission.LIFECYCLE_MIN_WINDOW_TRADES`) before the backlog stops advertising it.
#
# **This is a recorded judgement, not a derivation.** It was 14 until 2026-08-02, reproducing
# the operator's retirement of 2026-07-30 (`approval_c1b8eeb7681c08579648`, 63 strategies),
# which kept **one 15m lineage per symbol** and retired everything slower on exactly this
# argument — *"at current rates a 1d lineage needs 122d and a 1h lineage 29d to reach the
# 20-trade WARNING window, so neither can ever be auto-demoted."*
#
# **That value's premise was that 15m is the workhorse, and the cost model had killed it.**
# Measured over the 240 candidates carrying the current basis (2026-08-02), net per trade:
# **15m −0.1845R, 1h −0.0185R, 4h +0.0889R** — friction at 15m is 0.2768R against 0.0923R of
# gross edge, and gross is nearly flat across the ladder, so what separates the timeframes is
# the denominator (1R = `stop_atr` × ATR) and not the signal. A cap admitting only 15m
# therefore admitted only the timeframe that cannot pay for itself, and the board read
# `0 promotable` with 900 candidates on file.
#
# **Re-measured 2026-08-04 over 474 candidates, and the economics half of that has moved:**
# **15m −0.0866R, 1h +0.0241R, 4h +0.1083R** — 1h pays now and 15m's deficit has more than
# halved, because #420's `stop_atr` floor only reaches the numbers through the generations
# minted after it (15m friction 0.2625R → 0.1615R over four days of mints). It does **not**
# move this constant: 130 is anchored on the horizon the operator actually promoted at, not on
# which timeframe pays, and the two were never the same argument. What it does retire is the
# reading that this cap must lean slow — see `REMAINING_WORK.md` section F, which also measures
# why restating this horizon in TRADES would bind 4h hardest (median holdout 23 trades against
# `robustness.MIN_HOLDOUT_TRADES` = 25) rather than 15m.
#
# **What settles the new number is that the old one was hiding the operator's own decision.**
# Every one of the five lineages promoted 2026-07-31 sits above 14 days — 40.5, 50.4, 81.4,
# 107.7 and 127.3 — so the board was refusing to advertise the exact class of thing the
# operator was choosing to run. 130 is the slowest of those five, rounded up. The anchor is the
# same KIND as the one it replaces (a recorded operator decision, read off what was actually
# done); it is the decision that is newer. Change it by deciding again, not by re-deriving it.
#
# **The consequence, stated rather than smoothed over:** a lineage admitted at this horizon
# cannot be auto-demoted by the `lifecycle` ladder until it closes 20 trades, so retiring these
# is a MANUAL act for months. That is not created here — it is already true of all five pool
# members — and hiding them did not make it less true. What changes is that the board stops
# reporting a queue of zero while the operator promotes out of a queue it will not name.
#
# **What still bounds it:** 1d's own fastest quartile is 341.5 days, 2.6× this cap, so the
# timeframe the original argument was written against stays excluded. And economics are NOT
# encoded here — `promotable_backlog` already refuses on `expectancy_at_current_costs <= 0`
# per candidate, upstream of this line. This cap answers "can the runtime ever judge it", and
# only that.
#
# Why the backlog is the right place: `route_entries` picks ONE strategy per context, so adding
# a lineage to a context does not add trades — it splits the same trades across more lineages.
# A backlog that counted un-judgeable candidates was therefore advertising work whose only
# effect would be to dilute the outcome stream the lifecycle needs, which is how a pool of 89
# ended up with nothing in it the runtime could evaluate.
MAX_DAYS_TO_LIFECYCLE_WINDOW = 130


def days_to_lifecycle_window(record: Mapping[str, Any], *, window: int = LIFECYCLE_MIN_WINDOW_TRADES) -> float | None:
    """Calendar days before this lineage could close ``window`` trades, or None if unknowable.

    Read from the candidate's **own** evidence — ``closed_count`` over ``bars_replayed`` is its
    trades-per-bar, and the timeframe converts that to trades per day. No cross-store join and
    no live rate, so the number a reader sees is derived from the same row they are judging.

    None when the evidence cannot say (no bars recorded, no closed trades, an unknown
    timeframe). None is **not** "fast enough": the caller treats an unknowable rate as
    un-judgeable, because a lineage that closed nothing over its replay window is the clearest
    case of one the lifecycle will never get to evaluate."""
    evidence = record.get("backtest_evidence") or {}
    spec = record.get("strategy_spec") or {}
    timeframe = str(spec.get("timeframe") or "")
    minutes = market_data.TIMEFRAMES.get(timeframe)
    bars, closed = evidence.get("bars_replayed"), evidence.get("closed_count")
    if not minutes or not _is_number(bars) or not _is_number(closed) or bars <= 0 or closed <= 0:
        return None
    trades_per_day = (float(closed) / float(bars)) * (1440.0 / float(minutes))
    if trades_per_day <= 0:
        return None
    return round(window / trades_per_day, 1)


def _lineage_key(spec: Mapping[str, Any]) -> tuple[Any, ...]:
    """What makes two strategies the SAME promotion decision: one family on one context.

    Not the rule hash — the factory mints a fresh hash for every parameter tweak of the
    same family on the same symbol and timeframe, and an operator choosing between them is
    filling one slot, not making four decisions."""
    return (
        spec.get("strategy_family"),
        tuple(spec.get("symbol_scope") or ()),
        spec.get("timeframe"),
    )


def promotable_backlog(
    root: Path | None = None,
    *,
    candidates: list[Mapping[str, Any]] | None = None,
    active_pool: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """How many lineages an operator could promote right now and has not.

    Deliberately NOT the candidate count. The factory mints dozens a day, so that number
    only ever rises and a threshold on it fires every morning until it is ignored. What
    an operator can actually act on is the far smaller set that would clear the door
    TODAY — so this applies the same chain ``--list`` and the promotion gate apply, in
    the same order:

    - evidence at a basis the door accepts (:data:`pool_admission.PROMOTABLE_COST_BASIS_RANKS`); an
      OPTIMISTIC row is refused at the ask, so counting it would advertise work that
      cannot be done
    - and at a depth it accepts (:data:`pool_admission.PROMOTABLE_EVIDENCE_DEPTH_RANKS`), for exactly
      the same reason — the chain has to name every axis the door refuses on, or it
      drifts back into advertising refusals every time a new one is added
    - ROBUST on the *recomputed* verdict, and CONFIRMED out-of-sample
    - positive expectancy at the CURRENT rates, not at whatever rate it was scored under
    - one row per (family, symbol scope, timeframe), counting the active pool's own
      members first: the factory re-mints the same lineage every generation, so this
      collapses the re-mints AND drops the ones whose slot an operator already filled

    That last rule is one rule on purpose. Excluding pool members by ``strategy_rule_hash``
    while collapsing candidates by lineage mixes two granularities, and the backlog then
    never drains: promoting one re-mint leaves its siblings — same family, same context,
    different rule hash — to resurface as fresh backlog the next morning, forever. Measured
    here: 7 reported where 4 were waiting, the other 3 being siblings of rows promoted
    minutes earlier. The hash check stays as well, for pool entries whose spec cannot
    supply a lineage.

    Read-only, and it decides nothing: the count exists so the daily board can say a
    queue formed. Ids come back in :func:`rank_candidates` order, so the first one named
    is the first one an operator would read.

    **``refused`` says why the rest are not here, and exists because a count of zero was
    unreadable.** ``deferred_unjudgeable`` already named one axis — and that axis is the LAST
    one, so once an earlier filter took everything the deferred list went empty too and the
    board fell silent with a full store behind it. Measured 2026-08-04: 474 candidates at the
    current cost basis, ``count: 0``, ``deferred_unjudgeable: []``, and the reason (every one
    of them stopped at the holdout gate) appeared nowhere. That is the same failure the
    deferred list was added to prevent, one filter earlier — "nothing is waiting" rendered
    over "everything is waiting on one thing".

    Two properties make the breakdown readable rather than merely present:

    - **It is a partition.** Each judged row is counted exactly ONCE, at the FIRST axis that
      drops it, so ``sum(refused.values()) + count == candidates_read`` — pinned by a test.
      Overlapping tallies would be worse than no tally: two axes reporting 400 of 474 invites
      the reader to add them.
    - **``verdict`` and ``holdout`` are separate buckets** although one ``if`` refuses on both.
      They are different findings about the factory — "the score is not high enough" and "it
      scored well and did not reproduce forward" — and collapsing them is what let a store in
      which *nothing survives its own holdout* read as ordinary attrition.

    ``already_active`` and ``lineage_already_counted`` are collapses rather than quality
    refusals (the slot is filled, or a sibling re-mint of the same lineage is already in the
    list). They are in the same dict because the question it answers is "where did the store
    go", and a bucket missing from that answer is a number that does not add up.

    ``candidates_read`` is the count AFTER :func:`rank_candidates` collapses re-appends of a
    lineage to their latest row — the population this loop actually judged, so it is the
    denominator the partition sums to and not the store's line count.
    """
    records = candidates if candidates is not None else read_candidates(root)
    pool_doc = active_pool if active_pool is not None else load_active_pool(root)
    active_entries = pool_doc.get("active_strategies") or []
    # Counted over the same population `rank_candidates` sorts, so the tier an operator sees
    # in the backlog is the tier the ordering used. Recomputing it per record here instead
    # would count a different store than the one that produced the order.
    attempts = attempts_by_context(list(records))
    # Same evidence-aware key as the counting, for the reason `rank_candidates` states.
    pooled_keys = pooled_context_keys(list(records))
    active_hashes = {entry.get("strategy_rule_hash") for entry in active_entries}
    seen_lineages: set[tuple[Any, ...]] = {
        _lineage_key(entry.get("strategy_spec") or {}) for entry in active_entries
    }

    candidate_ids: list[str] = []
    deferred: list[dict[str, Any]] = []
    # Every key is present at zero on every call. A breakdown whose keys appear only when they
    # fire cannot be read as a partition — the absent axis and the axis that refused nothing
    # look identical, and the sum stops being checkable against `candidates_read`.
    refused: dict[str, int] = {axis: 0 for axis in BACKLOG_REFUSAL_AXES}
    judged = 0
    for record in rank_candidates(list(records)):
        judged += 1
        if record.get("strategy_rule_hash") in active_hashes:
            refused["already_active"] += 1
            continue
        # Before every evidence axis, because this is not an evidence question. A row whose
        # derivation the pool does not take cannot become promotable by scoring better, so
        # charging it to `cost_basis` or `verdict` would advertise a failure an operator could
        # act on. Present here rather than left for later on the rule the depth axis below
        # states: the chain has to name every axis the door refuses on, and that one was
        # missing for exactly one merge because it was added at the door first and counted
        # second. Zero on today's store — see `pool_admission.assert_promotable_derivation` for why the axis
        # exists before the rows it refuses do.
        if ("derivation_type" in record
                and record.get("derivation_type") not in PROMOTABLE_DERIVATION_TYPES):
            refused["derivation"] += 1
            continue
        quality = candidate_quality(
            record, attempts=attempts.get(attempt_context_key(record, pooled_keys=pooled_keys))
        )
        if quality["cost_basis_rank"] not in PROMOTABLE_COST_BASIS_RANKS:
            refused["cost_basis"] += 1
            continue
        # The same rule for the other axis the door refuses on. It was missing here for seven
        # minutes' worth of merge ordering — the depth gate landed just after this counter —
        # and the omission is the exact failure the line above is written to prevent: a row the
        # ask will refuse must not be advertised as work an operator could do. Latent when
        # found (no row passed every other filter AND failed this one) but not hypothetical:
        # the store holds 41 rows the depth gate refuses, and one of them becoming ROBUST is a
        # matter of time rather than of possibility.
        if quality["evidence_depth_rank"] not in PROMOTABLE_EVIDENCE_DEPTH_RANKS:
            refused["evidence_depth"] += 1
            continue
        # One condition in the original, split into buckets, and the HOLDOUT is asked first —
        # which is the opposite of the order the condition was written in, for a reason worth
        # stating. `candidate_quality` recomputes the verdict THROUGH the holdout state
        # (`classify_verdict` returns ROBUST only when it is CONFIRMED), so asking the verdict
        # first charges every forward failure to `verdict` and the holdout bucket becomes
        # unreachable — a breakdown that reports the collapse it was built to expose.
        #
        # The bucket key is the holdout's own status rather than a flat `holdout`, because the
        # three are different findings and the store currently holds all three: INSUFFICIENT is
        # "the tail cannot be judged" (too few trades, or minted before `stdev_r` existed),
        # CONTRADICTED is "judged, and it lost", UNCONFIRMED is "never evaluated". Reporting one
        # number over them would say attrition where the record says something specific.
        holdout_state = quality["holdout_status"]
        if holdout_state != HOLDOUT_CONFIRMED:
            key = f"holdout_{str(holdout_state).lower()}"
            # An unrecognised status is charged to its own axis rather than silently to a known
            # one: a new holdout state must show up as a bucket nobody has read yet, not as a
            # count under a label that no longer describes it.
            refused[key if key in refused else "holdout_other"] += 1
            continue
        # Reached only by a row that DID confirm forward — so this is "survived unseen bars,
        # still not ROBUST in-sample" (score or trades-per-parameter), plus the one case
        # `candidate_quality` documents where a record missing its components keeps a stale
        # stored verdict.
        if quality["verdict"] != ROBUST:
            refused["verdict"] += 1
            continue
        expectancy = quality["expectancy_at_current_costs"]
        if not isinstance(expectancy, (int, float)) or expectancy <= 0:
            refused["expectancy"] += 1
            continue
        lineage = _lineage_key(record.get("strategy_spec") or {})
        if lineage in seen_lineages:
            refused["lineage_already_counted"] += 1
            continue
        seen_lineages.add(lineage)
        # Last, and only after everything the door itself checks: can the runtime ever JUDGE
        # this? Every filter above asks whether the backtest is believable; none asked whether
        # a forward verdict is reachable. `route_entries` picks one strategy per context, so
        # promoting a slow lineage does not add trades — it splits the same trades across more
        # lineages, which is how a pool of 89 reached a state where no lineage had 13 trades and
        # nothing was eligible for any lifecycle rule. Deferred rather than dropped: the ids
        # come back so the board can say how many are waiting on a faster timeframe rather than
        # on an operator.
        days = days_to_lifecycle_window(record)
        if days is None or days > MAX_DAYS_TO_LIFECYCLE_WINDOW:
            deferred.append({"candidate_id": candidate_id(record),
                             "days_to_lifecycle_window": days})
            continue
        candidate_ids.append(candidate_id(record))

    refused["unjudgeable"] = len(deferred)
    return {
        "count": len(candidate_ids),
        "threshold": PROMOTION_BACKLOG_ALERT_THRESHOLD,
        "candidate_ids": candidate_ids,
        # Named, never silently dropped — a count that hid them would read as "nothing is
        # waiting" when what is true is "nothing the runtime could grade is waiting".
        "deferred_unjudgeable": deferred,
        "max_days_to_lifecycle_window": MAX_DAYS_TO_LIFECYCLE_WINDOW,
        # The denominator the breakdown partitions, and the one number that separates "the
        # store is empty" from "the store is full and every row was refused" — the two states
        # `count: 0` used to render identically.
        "candidates_read": judged,
        "refused": refused,
    }
