"""C7 strategy pool — the public face of the active pool the cycle routes against and of the
candidate store the C8 promotion flow consumes. What stays here: the routing views, the resolution of
an operator's candidate selectors, and a candidate seen as the entry it would become (for replay).

The pool's other roles live beside it, and every public name of theirs is re-exported here as the same
object, so callers keep reading them as ``pool.<name>``:

- :mod:`pool_state` (crypto PR7e-7): the two files' paths and reads, the pool's install door, the
  candidates' append door, and what each of them checks;
- :mod:`live_tier` (PR7e-8): the live tier, its disarm door included;
- :mod:`pool_transitions` (PR7e-9): the status transitions;
- :mod:`pool_admission` (PR7e-10): the promotion door's gates and the size cap;
- :mod:`promotion_backlog` (PR7e-11): the backlog, which reports and decides nothing.

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
from .candidate_identity import entry_attribution_keys
from .candidate_identity import candidate_id, derive_candidate_id  # noqa: F401 — re-exported:
# `pool`'s many callers read the id rule as `pool.candidate_id`, and the rule itself
# moved to a leaf so `factory` no longer needs a module-level edge into `pool`.
# The ranking view and the two comparability tiers moved to `candidate_ranking` (strategy) in crypto
# PR7e-1. Nothing left in this module reads them: `pool_admission` and `promotion_backlog` import them
# directly (PR7e-10, PR7e-11). Every public name is re-exported here, as the same object, for the
# callers that read them as `pool.<name>`. Of the private helpers only `_as_float`, which
# `context_scores` calls, is imported: a patch on `pool` for any other would miss the ranking or the
# backlog that reads it, and without the name here such a patch fails loudly instead.
from .candidate_ranking import (  # noqa: F401
    COST_BASIS_RANK_CONSERVATIVE, COST_BASIS_RANK_CURRENT, COST_BASIS_RANK_OPTIMISTIC,
    COST_BASIS_RANK_UNRECORDED, EDGE_COST_BASIS_NET, EDGE_COST_BASIS_UNRECORDED,
    EVIDENCE_DEPTH_RANK_FULL, EVIDENCE_DEPTH_RANK_SHALLOW, EVIDENCE_DEPTH_RANK_UNRECORDED,
    EVIDENCE_DEPTH_REPLAYED, EVIDENCE_DEPTH_TOLERANCE, EVIDENCE_DEPTH_UNRECORDED, _as_float,
    attempt_context_key, attempts_by_context, candidate_quality, cost_basis_of,
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
# and the size cap with the caps and slot map it checks against moved to `pool_admission` (decision)
# in crypto PR7e-10, with the per-direction capacity the dashboard reports and the lifecycle window,
# which `promotion_backlog` reads too. Every public name is re-exported here, as the same object, for
# the callers that read them as `pool.<name>`. The private `_observation_holdout_term` is not
# imported: nothing here calls it, and a patch on `pool` for it would miss the entry bar.
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
# The two files' paths and reads, the install and append doors, and what each of them checks moved to
# `pool_state` (decision) in crypto PR7e-7, so that the roles still here can move out without an
# import cycle. Every public name is re-exported here, as the same object, for the callers that read
# them as `pool.<name>`, and the code still in this module reads them through these bindings, so a
# patch on `pool` reaches that code. It does not reach the code that left: `pool_state`'s own
# functions, the live tier's disarm door, the status transitions, and the gates that read the pool or
# the candidates (`reactivated_candidate_ids`, `silent_reactivations`, `assert_rule_not_routed`,
# `pool_candidate_records`), and the backlog, read their own modules' names. Neither private name is
# imported: a patch on `pool` for either would miss the code in `pool_state` that reads it, and
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
# The backlog (how many lineages an operator could promote now, and why the rest cannot) moved to
# `promotion_backlog` (decision) in crypto PR7e-11, the last of this module's roles to leave. Its public
# names are re-exported here, as the same objects, for the daily board and the tests that read them as
# `pool.<name>`. The private `_lineage_key` is not imported: nothing here calls it.
from .promotion_backlog import (  # noqa: F401
    BACKLOG_REFUSAL_AXES, MAX_DAYS_TO_LIFECYCLE_WINDOW, PROMOTION_BACKLOG_ALERT_THRESHOLD,
    days_to_lifecycle_window, promotable_backlog,
)
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
