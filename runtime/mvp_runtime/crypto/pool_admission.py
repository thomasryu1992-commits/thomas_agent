"""The promotion door's gates: what a candidate must satisfy to enter the pool, and how much the pool
may hold (crypto PR7e-10).

`promotion`'s roster (``PROMOTION_GATES``, the one gate list both doors run) calls these through `pool`:

- the two tier doors (:func:`assert_promotable_cost_basis`, :func:`assert_promotable_evidence_depth`)
  and the derivation door (:func:`assert_promotable_derivation`), with the sets they refuse on;
- the record-stamp door (:func:`assert_promotable_record_stamp`, 2026-09-23), which `promotion`
  imports from here directly rather than through `pool`;
- the checks against the pool's incumbents: semantic duplicates and behaviour-cluster siblings
  (:func:`assert_no_semantic_duplicates`, :func:`assert_no_cluster_siblings`). They take the
  incumbents' rows as an argument; the promotion door reads those rows with
  :func:`pool_candidate_records`, and only when a batch adds to the pool;
- the observation tier's entry bar and family cap (:func:`assert_observation_entry_bar`,
  :func:`assert_family_cap`);
- one routed rule per lineage (:func:`assert_rule_not_routed`) and the entries a promotion leaves
  behind (:func:`assert_no_silent_reactivation`);
- the size cap (:func:`assert_pool_within_size_cap`), with what it checks against: the per-context
  caps (``MAX_ROUTABLE_*``, ``FAST_ROUTING_TIMEFRAMES``, :func:`max_routable_per_context`), the slot
  each strategy competes for (:func:`routable_context_map`), and the lifecycle window
  (:data:`LIFECYCLE_MIN_WINDOW_TRADES`), which `promotion_backlog` reads too;
- :func:`routable_directional_capacity`, the per-direction capacity the dashboard and the promotion
  script report. Nothing refuses on it.

This was `pool`'s until crypto PR7e-10. It reads the stored pool through `pool_state` and the live
tier's rule hash through `live_tier`, and nothing of `pool`'s, so `pool` can import it: `pool`
re-exports every public name here as the same object, and its callers keep reading `pool.<name>`. A
patch on `pool` does not reach the functions here, which read this module's names.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..errors import ToolError
from .candidate_identity import candidate_id, outcome_attribution_key
from .candidate_ranking import (
    COST_BASIS_RANK_CONSERVATIVE, COST_BASIS_RANK_CURRENT, EVIDENCE_DEPTH_RANK_FULL,
    EVIDENCE_DEPTH_RANK_SHALLOW, candidate_quality, cost_basis_of, cost_basis_rank, current_cost_basis,
    current_evidence_depth, evidence_depth_rank,
)
from .live_tier import _spec_rule_hash
from .paper import OCCUPYING_STATUSES
from .pool_state import load_active_pool, read_candidates
from .strategy import Direction, StrategySpec

# --- the promotion door on the two comparability tiers ------------------------
#
# The tiers themselves (what a row PAID, `cost_basis_rank`, and how much market it was SHOWN,
# `evidence_depth_rank`) and the argument for which of them may back a promotion live with the
# ranking that orders on them, in `candidate_ranking` (strategy) since crypto PR7e-1. What stays
# here is the door: the promotable sets and the two refusals that turn a tier into a block.

# Which cost tiers may back a promotion. Optimistic and unrecorded evidence is refused at the
# door — the first inflates the number an operator reads, the second cannot even say whether
# it does. Conservative evidence promotes: its error runs against the candidate, so a lineage
# that clears the bar under it clears the bar under the real model too.
PROMOTABLE_COST_BASIS_RANKS = frozenset({COST_BASIS_RANK_CURRENT, COST_BASIS_RANK_CONSERVATIVE})


def assert_promotable_cost_basis(records: list[Mapping[str, Any]]) -> None:
    """Refuse a promotion backed by evidence scored more cheaply than the venue charges.

    The store is append-only and `backtest_evidence` is durable, so a stale basis can never be
    repaired in place — the only place it can be caught is the door where evidence turns into
    real money. `expectancy` alone is re-derivable at the current rates (`expectancy_at`), but
    win-rate, realized reward:risk and the robustness verdict all need per-trade signs the
    store does not keep, so a candidate cannot simply be re-read at today's model.

    Raises `CANDIDATE_COST_BASIS_STALE`, naming every offending candidate and its basis."""
    stale = [
        (candidate_id(record), cost_basis_of(record))
        for record in records
        if cost_basis_rank(record) not in PROMOTABLE_COST_BASIS_RANKS
    ]
    if stale:
        listed = ", ".join(f"{cid} ({basis})" for cid, basis in stale)
        raise ToolError(
            "CANDIDATE_COST_BASIS_STALE",
            f"scored under a cost model cheaper than the venue charges "
            f"({current_cost_basis()}), so their expectancy is overstated: {listed}. "
            f"Re-mint the lineage at the current model, or pass the explicit "
            f"--allow-stale-cost-basis escape.",
        )


# The depth door, and why it refuses one tier and not the other. The premise (which way a shallow
# window's error points, and why re-windowing is a different sample) opens `candidate_ranking`'s
# evidence-depth block; the argument that acts on it is this one.
#
# So A KNOWN-SHALLOW ROW RANKS, REPORTS AND IS RECORDED — it is not refused. Three reasons:
#   1. Every 1d row in the store was scored at the old window, so the day the floor lands a
#      refusal makes the escape hatch the normal door. The cost tiers already record that
#      lesson (equality was tried first and refused 90 of 359 rows on their own merits).
#   2. The error runs AGAINST the candidate: a shallow row that still shows a verdict cleared
#      a harder bar on the counted terms, and a shallow FRAGILE is absence of evidence rather
#      than evidence of absence — refusing it would discard the 12-of-25 that were real.
#   3. This door installs into the PAPER pool (`stage: paper`, `PAPER_ACTIVE`); live money has
#      its own gates. Paper routing is where thin evidence goes to get thicker, so refusing
#      shallow evidence here blocks the cheapest way to earn the depth it lacks.
# What replaces the refusal is attribution: the tier orders the list, the `--list` view names
# the split, and the depth each promoted row stood on rides onto the ledger beside its basis.
#
# AN UNRECORDED WINDOW IS REFUSED, AND ALL THREE OF THOSE REASONS COLLAPSE ON IT.
# (1) does not grow: every row the factory mints records its depth, so the unrecorded set is a
# closed legacy population that shrinks, not one the next policy change re-creates — the 41
# arrive through the C7 import, which copies an outside pool's entries verbatim. (2) is the
# argument that fails hardest: "the error runs against the candidate" is a claim about a
# window you can SEE. An unrecorded one has no known direction, which is precisely why
# `COST_BASIS_RANK_UNRECORDED` is refused rather than ranked. (3) proves nothing, because the
# cost door already refuses unrecorded evidence into this same paper pool.
#
# And unrecorded depth is WORSE than unrecorded cost, not merely equal to it. An unrecorded
# cost basis is partially repairable: `expectancy_at` re-derives the number that matters at
# today's rates, exactly, from what the row already stores. Nothing re-windows a candidate.
# The snapshot that produced the evidence is not kept — only `evidence_input_sha256`, its hash
# — so a row that cannot say how much market it replayed cannot be made to say it, ever, by
# any amount of arithmetic. The repo's standing rule for that case is not a ranking:
# missing / uncertain → BLOCK, never guess.
#
# The escape is its own flag rather than a widening of `--allow-stale-cost-basis`. Two doors
# that fail for different reasons must not open with one key.
#
# Which depth tiers may back a promotion. SHALLOW promotes because its error is known and runs
# against the candidate; UNRECORDED is refused because no direction can be read off it and,
# unlike an unrecorded cost basis, no part of it can be re-derived later.
PROMOTABLE_EVIDENCE_DEPTH_RANKS = frozenset({
    EVIDENCE_DEPTH_RANK_FULL, EVIDENCE_DEPTH_RANK_SHALLOW,
})


def assert_promotable_evidence_depth(records: list[Mapping[str, Any]]) -> None:
    """Refuse a promotion backed by evidence that cannot say how much market it replayed.

    Not a depth threshold — a KNOWN shallow window promotes, because a verdict scored on less
    market errs against the candidate and paper routing is how it earns the rest. This refuses
    only the rows that record no window at all, where "errs against the candidate" is not a
    claim anyone can make.

    Unrecoverable in a way an unrecorded cost basis is not: `expectancy_at` re-derives the
    number that matters at today's rates from what the row already stores, while nothing
    re-windows a candidate — the snapshot behind `evidence_input_sha256` is not kept, only its
    hash. So this cannot be repaired in place, on any later day, by any arithmetic.

    Raises `CANDIDATE_EVIDENCE_DEPTH_UNRECORDED`, naming every offending candidate."""
    unknown = [
        candidate_id(record)
        for record in records
        if evidence_depth_rank(record) not in PROMOTABLE_EVIDENCE_DEPTH_RANKS
    ]
    if unknown:
        listed = ", ".join(unknown)
        raise ToolError(
            "CANDIDATE_EVIDENCE_DEPTH_UNRECORDED",
            f"record no replay window, so how much market their verdict was earned on cannot "
            f"be read and cannot be re-derived: {listed}. A candidate minted now records it "
            f"({current_evidence_depth('1d')} at 1d) — re-mint the lineage through the "
            f"factory, or pass the explicit --allow-unrecorded-evidence-depth escape.",
        )


# --- semantic duplicates -------------------------------------------------------
#
# `strategy_rule_hash` covers the condition **sequence**, which makes it an identity
# for the record and a poor one for the strategy. Two ways to hold the same strategy
# under a different hash, both observed in this store on 2026-07-29:
#
#   1. Reorder the conditions. `evaluate_spec` folds them with `all()`/`any()` over the
#      full result list — no short-circuit — so order cannot change what a spec does.
#      One such pair was already in the store.
#   2. Append a condition that never discriminates. The rule miner minted four BNBUSDT
#      lineages that are one base rule plus `low < high` or `mark_index_basis_bps <= 0.0`
#      (a constant 0.0 in this runtime's feature builder). All five traded identically,
#      and the padded clones outscored the base rule, so the router picked a tautology.
#
# Both checks below are EXACT. Neither reasons about whether a condition "looks"
# redundant — proving a condition inert in general needs invariants this module does
# not have, and a guess in a fail-closed gate is worse than the gap it fills. What is
# provable is used. The tier below proof (`near_duplicate_groups`) is reported, and since
# Thomas's 5-2 decision (2026-08-11) it gates exactly one thing — two cluster members
# co-occupying routing slots (`assert_no_cluster_siblings`) — on the narrower claim that
# indistinguishable evidence cannot buy a second slot.

def canonical_rule_form(record: Mapping[str, Any]) -> tuple[Any, ...] | None:
    """What a spec DOES, independent of how its conditions happen to be ordered.

    Sound because evaluation is order-free (see above), so two specs with the same
    condition SET, operator, exits and scope are the same strategy by construction —
    no evidence required, which is what makes this the only check available at the
    import door, where rows can arrive with no backtest at all.
    """
    spec = record.get("strategy_spec")
    if not isinstance(spec, Mapping):
        return None
    entry = spec.get("entry_rules") or {}
    conditions = entry.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        return None
    return (
        str(entry.get("operator")),
        frozenset(
            (str(c.get("feature")), str(c.get("comparison")),
             str(c.get("value_from") or ""), repr(c.get("value")))
            for c in conditions if isinstance(c, Mapping)
        ),
        str(spec.get("direction")),
        str(spec.get("timeframe")),
        tuple(spec.get("symbol_scope") or ()),
        json.dumps(spec.get("exit_rules") or {}, sort_keys=True),
    )


def behavioural_fingerprint(record: Mapping[str, Any]) -> tuple[Any, ...] | None:
    """What a spec DID, on one exact window. ``None`` when it cannot say.

    Keyed on ``evidence_input_sha256`` first: comparing outcome aggregates across
    different windows compares two different questions. Two specs replayed over the
    SAME candles that closed the same number of trades, won and lost the same number,
    and landed on the same expectancy, drawdown and net R to eight decimals took the
    same trades — the aggregates agreeing by coincidence over ~100 trades is not a
    case worth designing for.

    A spec that traded zero times fingerprints as ``None``: no-trade specs all "agree"
    and would otherwise collapse into one enormous false group.

    So does a spec whose evidence is INCOMPLETE. Agreeing on the two aggregates a
    partial record happens to carry is not the same claim as agreeing on all of them,
    and treating it as one turns every sparsely-recorded row into everyone else's
    duplicate. Every term must be present or this says nothing.
    """
    evidence = record.get("backtest_evidence")
    window = record.get("evidence_input_sha256")
    if not isinstance(evidence, Mapping) or not isinstance(window, str) or not window:
        return None
    closed = evidence.get("closed_count")
    if not isinstance(closed, int) or closed <= 0:
        return None
    cost = evidence.get("cost_summary") or {}
    terms = (
        evidence.get("win_count"), evidence.get("loss_count"),
        evidence.get("expectancy"), evidence.get("max_drawdown"), cost.get("total_net_r"),
    )
    if any(term is None for term in terms):
        return None
    return (window, closed, *terms)


def semantic_duplicate_groups(
    records: list[Mapping[str, Any]], *, incumbents: list[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Groups of records that are the same strategy under either exact test.

    ``incumbents`` are compared against but never reported alone: a group surfaces only
    when it contains at least one of ``records``, so an existing pool that already holds
    a duplicate pair does not block every unrelated promotion until someone cleans it up.
    """
    pool_records = list(incumbents or [])
    buckets: dict[tuple[str, Any], list[Mapping[str, Any]]] = {}
    for record in [*records, *pool_records]:
        for kind, key in (("rule_form", canonical_rule_form(record)),
                          ("behaviour", behavioural_fingerprint(record))):
            if key is not None:
                buckets.setdefault((kind, key), []).append(record)

    incoming = {id(r) for r in records}
    groups: list[dict[str, Any]] = []
    seen: set[frozenset[str]] = set()
    for (kind, _key), members in buckets.items():
        if len(members) < 2 or not any(id(m) in incoming for m in members):
            continue
        ids = frozenset(candidate_id(m) for m in members)
        # "The same strategy under a DIFFERENT rule hash" is the whole claim. Members
        # sharing a hash are one rule measured more than once — a re-scored row and its
        # original are the obvious case, and the store is append-only precisely so both
        # can exist. One rule under two candidate ids is the promotion door's to refuse
        # or to replace (`assert_rule_not_routed`, `replaced_entries`, PR3c-2); this
        # function exists only for what those cannot see.
        if len({str(m.get("strategy_rule_hash")) for m in members}) < 2:
            continue
        if len(ids) < 2 or ids in seen:
            continue
        seen.add(ids)
        groups.append({
            "match": kind,
            "candidate_ids": sorted(ids),
            "strategy_ids": sorted({str(m.get("strategy_id")) for m in members}),
        })
    return groups


def assert_no_semantic_duplicates(
    records: list[Mapping[str, Any]], *, incumbents: list[Mapping[str, Any]] | None = None,
) -> None:
    """Refuse a batch that would put the same strategy in the pool twice.

    Not cosmetic. The router picks ONE strategy per context, so a duplicate does not
    double a position — it takes the slot and then never trades, which means the
    lifecycle collects no outcomes for it and can never demote it. A clone entering
    the pool is a clone staying in the pool.

    Raises ``CANDIDATE_SEMANTIC_DUPLICATE``, naming each group and which test matched.
    """
    groups = semantic_duplicate_groups(records, incumbents=incumbents)
    if not groups:
        return
    listed = "; ".join(
        f"{'/'.join(g['strategy_ids'])} [{', '.join(g['candidate_ids'])}] matched on {g['match']}"
        for g in groups
    )
    raise ToolError(
        "CANDIDATE_SEMANTIC_DUPLICATE",
        f"these are the same strategy under a different rule hash: {listed}. "
        f"Promote one of each group, or pass the explicit --allow-duplicates escape.",
    )


def reactivated_candidate_ids(
    candidate_ids: Sequence[str], *, keep_active: bool, root: Path | None = None,
    candidates: Sequence[Mapping[str, Any]] = (),
) -> list[str]:
    """Lineages this promotion would return to trading, sorted. One pool read.

    The id-keyed form of :func:`silent_reactivations`, and the one the promotion CONTENT HASH
    is a function of — the hash is computed from selectors at the ask, long before entries are
    assembled, so it cannot use the entry-keyed view.

    Sorted, so the hash does not depend on selector order, for the same reason
    ``candidate_ids`` and ``rule_hashes`` are sorted beside it.

    Two ways back, named apart:

    - **A terminal member re-listed under its own candidate id**, named by that bare id. Only
      REPLACE rebuilds every entry, and that is where a terminal member re-listed alongside the
      rest returns as PAPER_ACTIVE; ADD mode keeps the incumbents' status and refuses a candidate
      already in the pool.
    - **A retired rule returned under another candidate id** (PR3c-2, Thomas decision 40), in
      either mode: every terminal entry holding a candidate's rule (:func:`replaced_entries`),
      which the install replaces. Named by its lineage key (`candidate_identity.
      outcome_attribution_key`: ``cand:``, ``gen:`` or ``sid:``), so the approval says which
      retired lineage the rule comes back from. Read off the candidate rows (``candidates``),
      which carry the rule; the two doors pass them.
    """
    from .lifecycle import TERMINAL_STATUSES  # local: it once avoided a module cycle; none remains

    entries = load_active_pool(root).get("active_strategies") or []
    returned: set[str] = set()
    if not keep_active:
        current = {str(e.get("candidate_id")): e for e in entries if e.get("candidate_id")}
        returned.update(
            cid for cid in {str(c) for c in candidate_ids if c}
            if cid in current and str(current[cid].get("status")) in TERMINAL_STATUSES
        )
    for candidate in candidates:
        returned.update(outcome_attribution_key(entry) for entry in replaced_entries(candidate, entries))
    return sorted(returned)


def silent_reactivations(
    entries: list[Mapping[str, Any]], *, root: Path | None = None,
) -> list[dict[str, Any]]:
    """Members this install would move OUT of a terminal status, keyed by lineage.

    Pure-ish (one pool read), returns ``[{candidate_id, strategy_id, from_status}]`` oldest
    first. Empty is the ordinary answer; a non-empty list is the promotion door about to do
    something its approval never named.

    Since PR3c-2 (Thomas decision 40) also a NEW entry whose rule only retired entries hold under
    other lineages: the rule returns to trading, and the entry replaces them. Its row carries
    ``replaces`` — each replaced entry's display id, candidate id, status and lineage key — and
    ``from_status`` names their statuses."""
    from .lifecycle import TERMINAL_STATUSES  # local: it once avoided a module cycle; none remains

    on_disk = load_active_pool(root).get("active_strategies") or []
    current = {e.get("candidate_id"): e for e in on_disk if e.get("candidate_id")}
    found = []
    for entry in entries:
        cid = entry.get("candidate_id")
        if str(entry.get("status")) in TERMINAL_STATUSES or not cid:
            continue
        was = current.get(cid)
        if was is not None and str(was.get("status")) not in TERMINAL_STATUSES:
            continue                                   # an incumbent that is trading: nothing returns
        replaced = replaced_entries(entry, on_disk)
        if was is None and not replaced:
            continue                                   # a new lineage of a new rule
        statuses = {str(r.get("status")) for r in replaced} | ({str(was.get("status"))} if was else set())
        row: dict[str, Any] = {
            "candidate_id": str(cid),
            "strategy_id": str(entry.get("strategy_id")),
            "from_status": "/".join(sorted(statuses)),
        }
        if replaced:
            row["replaces"] = [
                {"strategy_id": str(r.get("strategy_id")), "candidate_id": r.get("candidate_id"),
                 "status": str(r.get("status")), "lineage": outcome_attribution_key(r),
                 "lifecycle_reasons": r.get("lifecycle_reasons")}
                for r in replaced
            ]
        found.append(row)
    return found


def assert_no_silent_reactivation(
    entries: list[Mapping[str, Any]], *, root: Path | None = None,
) -> None:
    """Refuse an install that un-suspends a member nobody asked to un-suspend.

    **The promotion door's replace mode rebuilds every entry with a hardcoded
    ``PAPER_ACTIVE``.** So an operator dropping one redundant strategy — by re-listing the
    rest — brings back every terminally SUSPENDED member with them. `BUILD_HISTORY` records
    this simulated against a copy of the real pool on 2026-07-29: **16 reactivated, 57
    lifecycle counters reset.** The answer then was the narrow `retirement` verb, which
    removed the REASON to reach for replace mode; it did not close the path.

    What makes it worth a guard rather than a note is where the authority sits.
    ``promotion_content_sha256`` is a function of candidate ids, rule hashes and
    ``keep_active`` — nothing about lifecycle status — so the approval Thomas signs says
    "install these lineages" and cannot say "and un-suspend sixteen of them". The signature
    is honest about a smaller effect than the one it authorizes. And the lifecycle's own
    rule is that this direction is never automatic: `evaluate_lifecycle` degrades only, and
    a terminal entry refuses at the status write precisely so reactivation stays deliberate.
    Coming back through a membership operation is that rule being routed around.

    So the door fails closed and the operator has to name it, in the idiom the four evidence
    escapes already use: refuse, list exactly who and from what, and let an explicit
    ``--allow-reactivation`` through — recorded on the ledger, because an escape that leaves
    no trace is indistinguishable later from a door that never refused.

    The approval names the reactivation: its content hash carries the set
    (:func:`reactivated_candidate_ids`, since #619), and since PR3c-2 the ask signs it and says so.
    This guard makes performing it deliberate and leaves it on the ledger.

    Since PR3c-2 a retired RULE returned under another candidate id is a reactivation too
    (Thomas decision 40), refused here the same way.

    Raises ``POOL_SILENT_REACTIVATION``.
    """
    found = silent_reactivations(entries, root=root)
    if not found:
        return
    listed = "; ".join(
        f"{f['strategy_id']} [{f['candidate_id']}] {f['from_status']}"
        + (" (replaces " + ", ".join(f"{r['strategy_id']} [{r['candidate_id'] or r['lineage']}]"
                                     for r in f["replaces"]) + ")" if f.get("replaces") else "")
        for f in found
    )
    raise ToolError(
        "POOL_SILENT_REACTIVATION",
        f"this install returns {len(found)} terminal member(s) to trading: {listed}. A return is an "
        f"operator's explicit act: pass --allow-reactivation, at the ask and at the install (the "
        f"approval names what returns).",
    )


# --- the same rule is the same strategy (PR3c-2, Thomas decisions 40 and 42) -----------
#
# The candidate store re-scores a rule under a new candidate id (348 rule hashes carry two or
# more, 2026-09-18), and every identity check above compares candidate ids. So a rule the pool
# routes could be installed a second time, and a retired rule could return to trading under a
# new id as a fresh hypothesis, the record it was retired on left with an id no entry holds.
# Replayed 2026-09-19 on the running image: of the 70 rules the pool holds with another id in
# the store, none could be installed that way that day, but only because another gate or a
# display-id collision happened to refuse each one (on 2026-09-10 the collision alone did).
#
# Decision 40: a rule the pool routes is never installed again under another id. A rule only
# retired entries hold comes back as a REACTIVATION — named in the approval, behind
# `--allow-reactivation` — and the new entry replaces those entries and inherits their record
# (decision 41, `candidate_identity.PREDECESSOR_KEYS_FIELD`). One rule, one entry, from then on.
# The pool read does not enforce it: S008 and S008-GEN-696 hold one rule today, both retired, and
# stay (decision 42); a promotion of their rule replaces both.

# A promotion would route a rule the pool already routes, or one rule twice. No escape.
POOL_RULE_ALREADY_ROUTED = "POOL_RULE_ALREADY_ROUTED"


def rule_hashes_of(record: Mapping[str, Any]) -> set[str]:
    """The rule hashes ``record`` (a candidate row or a pool entry) answers to: its label and the
    hash of the spec it trades. They agree on every entry and row on the host (2026-09-19); both
    are read, so a label that parts from its spec cannot hide a rule."""
    return {value for value in (record.get("strategy_rule_hash"), _spec_rule_hash(record.get("strategy_spec")))
            if isinstance(value, str) and value}


def same_rule_entries(
    record: Mapping[str, Any], entries: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """The entries holding ``record``'s rule under another lineage: another candidate id, or none."""
    wanted = rule_hashes_of(record)
    own = record.get("candidate_id")
    return [
        entry for entry in entries
        if isinstance(entry, Mapping) and not (own and entry.get("candidate_id") == own)
        and wanted & rule_hashes_of(entry)
    ]


def replaced_entries(
    record: Mapping[str, Any], entries: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """The retired entries (SUSPENDED, ARCHIVED) a promotion of ``record`` replaces: every one
    holding its rule under another lineage (decision 40), when the rule is RETURNING.

    It is not when ``record``'s own entry is trading: a replace-mode restate of a routed entry
    brings nothing back, and a retired twin beside it (a rule installed twice before PR3c-2) goes
    the way replace mode has always taken what it does not re-list. Naming it would put a return
    that is not happening in the approval, and in front of the ask's real-money warning, while the
    reactivation guard rightly saw none (review of PR3c-2)."""
    from .lifecycle import TERMINAL_STATUSES  # local: it once avoided a module cycle; none remains

    own = record.get("candidate_id")
    if own and any(isinstance(entry, Mapping) and entry.get("candidate_id") == own
                   and str(entry.get("status")) not in TERMINAL_STATUSES for entry in entries):
        return []
    return [entry for entry in same_rule_entries(record, entries)
            if str(entry.get("status")) in TERMINAL_STATUSES]


def assert_rule_not_routed(candidates: Sequence[Mapping[str, Any]], *, root: Path | None = None) -> None:
    """Refuse a promotion that would put a rule the pool routes in again under another lineage,
    or one rule in twice (decision 40). One pool read; no escape.

    "Routes" is any status but a terminal one, so a status this code does not know refuses too.
    Checked against the pool as it stands in both modes: a replace-mode batch that drops the
    routed lineage and lists its twin is the same rule changing lineage without a retirement.

    Raises :data:`POOL_RULE_ALREADY_ROUTED`."""
    from .lifecycle import TERMINAL_STATUSES  # local: it once avoided a module cycle; none remains

    entries = load_active_pool(root).get("active_strategies") or []
    for index, candidate in enumerate(candidates):
        for other in candidates[index + 1:]:
            if rule_hashes_of(candidate) & rule_hashes_of(other):
                raise ToolError(
                    POOL_RULE_ALREADY_ROUTED,
                    f"{candidate.get('strategy_id')} [{candidate.get('candidate_id')}] and "
                    f"{other.get('strategy_id')} [{other.get('candidate_id')}] are one rule; promote one",
                )
        routed = [entry for entry in same_rule_entries(candidate, entries)
                  if str(entry.get("status")) not in TERMINAL_STATUSES]
        if routed:
            listed = "; ".join(f"{e.get('strategy_id')} [{e.get('candidate_id') or '-'}] {e.get('status')}"
                               for e in routed)
            raise ToolError(
                POOL_RULE_ALREADY_ROUTED,
                f"{candidate.get('strategy_id')} [{candidate.get('candidate_id')}] is a rule the pool "
                f"routes: {listed}. The same rule is the same strategy; to move it to this candidate, "
                "retire it first and promote this one as a reactivation.",
            )


# --- pool sizing -------------------------------------------------------------------
#
# The caps the promotion door enforces, and why these numbers.
#
# The duplicate guard above already states the mechanism for its own case: *the router
# picks ONE strategy per context*, so a surplus entry takes a slot and then never trades.
# That is not special to duplicates. Measured 2026-07-30, with 67 routable strategies over
# 20 contexts: 86 own outcomes had scattered across 29 lineages, the largest holding 13.
# The lifecycle's cheapest rule needs a FULL rolling window of 20 before it evaluates at
# all (`lifecycle._full_window`), so **not one strategy in the pool was eligible for any
# demotion rule**, and `run_lifecycle` returned PAPER_ACTIVE for all 67 with empty reasons.
# Auto-demotion was not mis-tuned; it was unreachable. A -13.25R family sat in the pool for
# a week because nothing could ever accumulate the evidence to demote it.
#
# So the pool is capped on the ROUTABLE set, not on membership: a SUSPENDED entry neither
# routes nor splits the evidence, and after a retirement the pool file legitimately holds
# far more rows than these numbers (89 rows, 5 routable, the day this landed).
#
# MAX_ROUTABLE_PER_CONTEXT: 2 on every timeframe since Thomas 2026-09-02, with one condition
# the slow timeframes carry and the fast ones do not — a slow context's two occupants must
# agree on DIRECTION (`assert_pool_within_size_cap`, `POOL_CONTEXT_DIRECTION_SPLIT`). Before
# that: 1 slow / 2 fast (Thomas 2026-08-24), revising the flat 1 of Thomas 5-2, 2026-08-11.
# The cluster-sibling rule from that decision is untouched: near-identical lineages still get
# one slot TOTAL; this cap is about DISTINCT strategies sharing a context.
#
# **Why the slow slot opened, on the record.** The exclusive slow slot bought judgeability
# (the arithmetic below), and judgeability stopped depending on routing exclusivity in two
# steps: `counterfactual` shadow-books every signal the router declines, and #807 gives each
# pool member its own forward stream settled by the paper kernel regardless of who routed —
# so a benched slow lineage accrues judgeable evidence at full rate. What an exclusive slot
# still bought was the routed paper book, and there the cost is asymmetric: two SAME-direction
# occupants only ever change which of them routes a bar (`_rank_matches`, on realized evidence
# first), while two OPPOSING ones fail the bar closed until one side is backed
# (`_resolve_direction_conflict`) — on 1d that is a whole day of routing lost per unresolved
# bar, at 0.16 outcomes/day. Hence the condition: the second slow occupant is admitted only
# alongside its own direction. Fast contexts keep the 2026-08-24 rule unchanged (a mixed pair
# is admitted and reads as "flexible" in `routable_directional_capacity`), because a blocked
# 15m/1h bar costs minutes, not days.
#
# What forced it: pooled minting (five-symbol scope) against a one-per-context door meant the
# 1d tier could hold exactly ONE strategy at a time. Measured 2026-08-30 and again 2026-09-02:
# 13 of 14, then 4 of 4 entry-bar passers were refused on `POOL_CONTEXT_CAP_EXCEEDED` alone,
# every one of them colliding with a single incumbent across all five contexts.
#
# The flat cap rested on "a second entry cannot add routing capacity (`route_entries`
# returns `ranked[0]`)" — true per BAR (the book holds one position per context) but not
# across time: the router ranks the strategies whose conditions FIRED, so a second strategy
# with disjoint entries adds bars the context can trade. What the flat cap actually bought
# was judgeability, and that is per-timeframe arithmetic, not a universal:
#
#   outcomes/day/context (measured): 15m ~2.0, 1h ~0.7, 4h ~0.6, 1d ~0.16
#
# On 15m/1h a context split two ways still fills a 20-trade lifecycle window in weeks; on
# 4h/1d a single lineage already needs ~33/~122 days, and splitting it puts auto-demotion
# out of reach — the 2026-07-30 failure (67 routable, nothing demotable, a -13.25R family
# living for a week) was exactly that arithmetic. So fast contexts took 2 and slow ones
# kept the exclusive slot until 2026-09-02 (see the note above for what changed and why).
# Three router changes rode with the 2026-08-24 decision (`paper.route_entries`): a
# same-bar tie is resolved on REALIZED expectancy before champion_score (whose anti-
# correlation with realized R is what made score-ranked sharing strictly negative), a
# direction conflict resolves toward a proven edge instead of always failing closed, and
# the signals the router declines are shadow-booked (`counterfactual`) so a benched
# lineage still accrues judgeable evidence at full rate.
#
# MAX_ROUTABLE_STRATEGIES = 30 is the grid-implied sum on today's 5-symbol grid:
# 5 * (2 + 2 + 1 + 1). It is not implied by the per-context caps: it is what still binds
# when the symbol set grows, so widening coverage becomes a deliberate change to this
# number rather than a silent return to a pool nothing can judge.
#
# What these caps deliberately do NOT fix: a 1d lineage produced 0.16 outcomes/day per
# context, so even at an exclusive slot it needs ~122 days to fill a 20-trade window (1h
# needs ~29, 4h ~33, 15m ~10). That is a property of the timeframe, not of the pool size,
# and no cap here can reach it — a 1d strategy is un-demotable by arithmetic. Naming it
# here so the next reader does not mistake this guard for a complete answer.
# 30 is unchanged although 5 * (2 + 2 + 2 + 2) is now 40: the note above says this number is
# its own decision and binds when the symbol set grows, and today's pool routes 10. Raising it
# is a separate change with its own argument, not a side effect of the slow cap moving.
MAX_ROUTABLE_STRATEGIES = 30
MAX_ROUTABLE_PER_CONTEXT = 2
MAX_ROUTABLE_PER_CONTEXT_FAST = 2
FAST_ROUTING_TIMEFRAMES = frozenset({"15m", "1h"})


def max_routable_per_context(timeframe: str) -> int:
    """The routable-slot cap for one ``(symbol, timeframe)`` context.

    Fast contexts refill a 20-trade lifecycle window in days-to-weeks, so they can afford
    to split it; slow ones cannot (the arithmetic above). Keyed on the timeframe alone —
    the symbol does not change how often a bar closes."""
    return MAX_ROUTABLE_PER_CONTEXT_FAST if str(timeframe) in FAST_ROUTING_TIMEFRAMES \
        else MAX_ROUTABLE_PER_CONTEXT


def routable_context_map(entries: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], list[str]]:
    """``(symbol, timeframe)`` → the routable strategy ids competing for that slot.

    A multi-symbol strategy occupies each of its symbols, matching what
    :func:`pool.routable_contexts` enumerates and what ``paper.route_entries`` actually judges.
    Non-occupying or spec-less entries contribute nothing."""
    contexts: dict[tuple[str, str], list[str]] = {}
    for entry in entries:
        if entry.get("status") not in OCCUPYING_STATUSES or not entry.get("strategy_spec"):
            continue
        spec = StrategySpec.from_dict(entry["strategy_spec"])
        for scoped_symbol in spec.symbol_scope:
            contexts.setdefault((str(scoped_symbol), str(spec.timeframe)), []).append(
                str(entry.get("strategy_id"))
            )
    return contexts


def routable_directional_capacity(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """How large a book this pool can actually fill under ``paper.MAX_DIRECTIONAL_SKEW``.

    **Reported, never refused, and that boundary is the point.** The directional cap is
    one-directional — it can only decline — so a lopsided pool trades *less*, which is safe
    but under-utilised. Turning that into a promotion refusal would add a new blocking
    authority over the operator's pool for a non-safety concern, and it would make an
    incremental build impossible: promoting five longs before any short is a perfectly
    ordinary way to assemble a pool, and a refusal would forbid every order but alternating.
    So this states the consequence and leaves the judgement where it belongs.

    **Why it is needed at all is an interaction, not a standalone idea.** A spec's
    ``direction`` is fixed at promotion time, so *which directions the book can ever hold is
    a property of the pool*, where it used to be a property of the template library (which
    is balanced 16/16). A pool of twenty long strategies can fill four of its twenty slots
    and no more, and nothing in either cap says so on its own.

    Counted over DISTINCT contexts, because a position is per context — under the fast-context
    cap two same-context strategies can never fill two book slots, so counting entries would
    overstate the reachable book. A context holding both directions is *flexible*: it can
    align with either side, so it joins whichever side is scarcer. The arithmetic is the
    cap's own: opposing positions buy back a slot each, so the reachable book is
    ``2 * min(long + flexible, short + flexible) + cap``, bounded by the context count.
    """
    from .paper import MAX_DIRECTIONAL_SKEW  # local, as it was in `pool`; `paper` is imported above too

    directions_by_context: dict[tuple[str, str], set[str]] = {}
    for entry in entries:
        if entry.get("status") not in OCCUPYING_STATUSES or not entry.get("strategy_spec"):
            continue
        spec = StrategySpec.from_dict(entry["strategy_spec"])
        # `spec.direction` is a Direction ENUM, not a string — `str()` on it yields
        # "Direction.SHORT", which silently matches neither bucket and reports every pool as
        # perfectly one-way. `strategy.evaluate_spec` normalises the same way (`is
        # Direction.SHORT`), so this reads the spec exactly as the router does.
        direction = "SHORT" if spec.direction is Direction.SHORT else "LONG"
        for scoped_symbol in spec.symbol_scope:
            directions_by_context.setdefault(
                (str(scoped_symbol), str(spec.timeframe)), set()
            ).add(direction)
    contexts = len(directions_by_context)
    long_contexts = sum(1 for d in directions_by_context.values() if d == {"LONG"})
    short_contexts = sum(1 for d in directions_by_context.values() if d == {"SHORT"})
    flexible_contexts = contexts - long_contexts - short_contexts
    effective = min(long_contexts + flexible_contexts, short_contexts + flexible_contexts)
    reachable = min(contexts, 2 * effective + MAX_DIRECTIONAL_SKEW)
    return {
        "long_contexts": long_contexts,
        "short_contexts": short_contexts,
        "flexible_contexts": flexible_contexts,
        "routable_contexts": contexts,
        "reachable_book": reachable,
        "skew_cap": MAX_DIRECTIONAL_SKEW,
        # True when the pool's own composition — not the market — is what holds the book below
        # the number of contexts it routes.
        "cap_binds": reachable < contexts,
    }


def assert_pool_within_size_cap(entries: Sequence[Mapping[str, Any]]) -> None:
    """Refuse a pool whose ROUTABLE set is too large to be judged. Fail-closed.

    ``entries`` is the pool as it WOULD be after the change — the caller merges first, so
    an add-mode promotion is judged on incumbents plus the batch rather than on the batch
    alone. Refused, never truncated: silently dropping the overflow would install a pool
    nobody chose, and which of the entries got dropped would be an accident of ordering.

    Raises ``POOL_SIZE_CAP_EXCEEDED`` / ``POOL_CONTEXT_CAP_EXCEEDED``, each naming what is
    over and by how much. Both name the escape, because an operator who has read the
    reasoning above may still have a reason this once."""
    occupying = [
        e for e in entries
        if e.get("status") in OCCUPYING_STATUSES and e.get("strategy_spec")
    ]
    if len(occupying) > MAX_ROUTABLE_STRATEGIES:
        raise ToolError(
            "POOL_SIZE_CAP_EXCEEDED",
            f"this promotion would leave {len(occupying)} routable strategies, above the cap of "
            f"{MAX_ROUTABLE_STRATEGIES}. A pool this size splits its outcomes across more "
            f"lineages than any of them can fill a {LIFECYCLE_MIN_WINDOW_TRADES}-trade lifecycle "
            "window with, so nothing in it can be auto-demoted. Retire first "
            "(scripts/retire_strategies.py), or pass "
            "the explicit --allow-oversized-pool escape.",
        )

    contexts = routable_context_map(occupying)
    over = {ctx: ids for ctx, ids in contexts.items()
            if len(ids) > max_routable_per_context(ctx[1])}
    if over:
        listed = "; ".join(
            f"{sym} {tf}: {len(ids)} over a cap of {max_routable_per_context(tf)} "
            f"({', '.join(sorted(ids))})"
            for (sym, tf), ids in sorted(over.items())
        )
        raise ToolError(
            "POOL_CONTEXT_CAP_EXCEEDED",
            f"more routable strategies than the context's timeframe can judge — {listed}. "
            "A context over its cap splits its routed book across more lineages than the "
            "router can rank on evidence (two per context, on every timeframe). Retire an "
            "incumbent first, or pass the explicit --allow-oversized-pool escape.",
        )

    # The slow-context condition (Thomas 2026-09-02): two occupants of a 4h/1d context must
    # agree on direction. Read the spec the way the router does (`Direction.SHORT` is an enum,
    # so `str()` on it matches nothing — see `routable_directional_capacity`).
    #
    # Each occupant's direction is paired with it where its own spec is read, and never looked
    # up by id afterwards. The ask hands this gate `promotion.predicted_pool_entries`, whose
    # batch rows still carry the factory's RAW strategy_id — the factory restarts at S001
    # every generation, and only the install door derives a unique `{id}-{generation}` — so
    # two lineages in one batch routinely share an id. A dict keyed by that id let whichever
    # was read last lend its direction to every context the others occupied: a false refusal
    # one way, and the other way a real split admitted (measured 2026-09-10, both directions
    # pinned by `test_a_shared_raw_id_*`). The contexts are enumerated exactly as
    # `routable_context_map` enumerates them for the cap above — scope times timeframe.
    occupants: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for entry in occupying:
        spec = StrategySpec.from_dict(entry["strategy_spec"])
        direction = "SHORT" if spec.direction is Direction.SHORT else "LONG"
        for scoped_symbol in spec.symbol_scope:
            occupants.setdefault((str(scoped_symbol), str(spec.timeframe)), []).append(
                (str(entry.get("strategy_id")), direction)
            )
    split = {
        (sym, tf): pairs for (sym, tf), pairs in occupants.items()
        if str(tf) not in FAST_ROUTING_TIMEFRAMES and len({d for _, d in pairs}) > 1
    }
    if split:
        listed = "; ".join(
            f"{sym} {tf}: " + ", ".join(f"{i} {d}" for i, d in sorted(pairs))
            for (sym, tf), pairs in sorted(split.items())
        )
        raise ToolError(
            "POOL_CONTEXT_DIRECTION_SPLIT",
            f"a slow context's two occupants disagree on direction — {listed}. On 4h/1d an "
            "unresolved conflict fails the bar closed until one side is backed by realized "
            "evidence, and a bar there is a day of routing. Promote alongside the incumbent's "
            "direction, retire it first, or pass the explicit --allow-oversized-pool escape.",
        )


def pool_candidate_records(root: Path | None = None) -> list[dict[str, Any]]:
    """The candidate rows behind the entries currently in the pool.

    The pool entry carries the spec but not the evidence, and the behavioural test
    needs evidence — so an incumbent has to be read back through its lineage. An entry
    whose ``candidate_id`` resolves to nothing (a pre-lineage import) simply
    contributes its spec, which is all the rule-form test needs anyway.
    """
    entries = load_active_pool(root).get("active_strategies") or []
    wanted = {e.get("candidate_id") for e in entries if e.get("candidate_id")}
    by_id = {candidate_id(c): c for c in read_candidates(root)}
    resolved = [by_id[cid] for cid in wanted if cid in by_id]
    unresolved = [e for e in entries if not e.get("candidate_id") or e["candidate_id"] not in by_id]
    return [*resolved, *unresolved]


def near_duplicate_groups(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Same window, same trade counts, but not identical R — a cluster, reported here.

    The pair this exists for: two SOLUSDT lineages that closed 82 trades each with the
    same 45/37 split and the same drawdown, differing in net R by 0.0016. Almost
    certainly one strategy wearing two rules, but "almost certainly" is a judgement,
    and this module refuses only on what it can prove. So an operator gets told —
    existence in the store and a single promotion stay unrefused.

    What IS refused, since Thomas's 5-2 decision (2026-08-11), is two members of one
    cluster CO-OCCUPYING routing slots — :func:`assert_no_cluster_siblings` below, which
    rests on the one thing this grouping does prove: on every recorded axis the two
    evidence rows are indistinguishable, so a second slot buys no independent evidence.
    """
    buckets: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for record in records:
        evidence = record.get("backtest_evidence")
        window = record.get("evidence_input_sha256")
        if not isinstance(evidence, Mapping) or not isinstance(window, str) or not window:
            continue
        closed = evidence.get("closed_count")
        if not isinstance(closed, int) or closed <= 0:
            continue
        buckets.setdefault(
            (window, closed, evidence.get("win_count"), evidence.get("loss_count")), []
        ).append(record)
    groups = []
    for members in buckets.values():
        ids = sorted({candidate_id(m) for m in members})
        if len(ids) > 1 and len({behavioural_fingerprint(m) for m in members}) > 1:
            groups.append({
                "candidate_ids": ids,
                "strategy_ids": sorted({str(m.get("strategy_id")) for m in members}),
            })
    return groups


def assert_no_cluster_siblings(
    records: list[Mapping[str, Any]], *, incumbents: list[Mapping[str, Any]] | None = None,
) -> None:
    """One routing slot per behaviour cluster (Thomas 5-2, 2026-08-11).

    The softer twin of :func:`assert_no_semantic_duplicates`, one tier down and with a
    narrower claim. That gate proves two rows are the SAME strategy; this one proves only
    that their recorded trading is indistinguishable (:func:`near_duplicate_groups` — same
    window, same trade counts, aggregates apart in the last decimals). Indistinguishable
    evidence cannot justify a second slot: the router picks one strategy per context, the
    forward record the second member would accrue is the measurement the first is already
    making, and OBSERVATION slots exist to buy independent forward evidence. So the store
    keeps both rows, either may be promoted — what is refused is promoting BOTH, or
    promoting one whose cluster already holds an occupying incumbent.

    ``incumbents`` is the pool's own candidate records (add mode); replace mode passes
    ``None`` and only the batch itself is checked, mirroring the semantic gate. A cluster
    made ENTIRELY of incumbents is not this batch's to answer for and does not refuse.

    Raises ``CANDIDATE_BEHAVIOUR_CLUSTER_OCCUPIED`` naming each conflicting cluster.
    """
    pool_records = list(incumbents or [])
    incoming_ids = {candidate_id(r) for r in records}
    incumbent_ids = {candidate_id(r) for r in pool_records} - incoming_ids
    conflicts: list[str] = []
    for group in near_duplicate_groups(list(records) + pool_records):
        ids = set(group["candidate_ids"])
        selected = sorted(ids & incoming_ids)
        occupied = sorted(ids & incumbent_ids)
        if len(selected) >= 2 or (selected and occupied):
            conflicts.append(
                f"{'/'.join(group['strategy_ids'])} [selected: {', '.join(selected)}"
                + (f"; occupying: {', '.join(occupied)}" if occupied else "")
                + "]"
            )
    if not conflicts:
        return
    raise ToolError(
        "CANDIDATE_BEHAVIOUR_CLUSTER_OCCUPIED",
        "these lineages traded indistinguishably on the recorded axes, and a behaviour "
        f"cluster gets one routing slot: {'; '.join(conflicts)}. Promote one member per "
        "cluster, or pass the explicit --allow-cluster-siblings escape.",
    )


# --- the OBSERVATION entry bar (Thomas 5-3, 2026-08-11) ---------------------------------------
#
# The bar selects for MEASURABILITY, not winners: in a population whose judgeable rows all
# CONTRADICTED, a positive-expectancy pick is selection noise by construction, so what a slot
# can honestly buy is forward evidence from a candidate whose backtest is honestly scored,
# current, and capable of being disconfirmed. Applied to ENTRANTS only — a restatement of what
# already occupies a slot is not an entry (the `assert_no_cluster_siblings` grandfathering
# rule, one gate over), or every re-arm of the pre-bar pool would need two waivers forever.

# Where the store's own expectancy-vs-sample table stops being inflated (`robustness.py`:
# <20 closes +0.114R, 20-49 +0.093R, 50-199 -0.003R — the estimate converges on the truth
# around fifty). Below it, a positive expectancy is mostly the small-sample lottery.
OBSERVATION_MIN_BACKTEST_CLOSED = 50

# Two occupying members per strategy_family. Forward evidence from a third sibling of one
# family is mostly correlated with the first two (F1: no correlation control exists), so the
# third slot buys diversity nowhere and multiple-testing debt everywhere.
OBSERVATION_FAMILY_CAP = 2


def _observation_holdout_term(record: Mapping[str, Any]) -> str | None:
    """None when the holdout admits observation entry; else the failure, named.

    Admitted: CONFIRMED (beyond the bar); INSUFFICIENT that is merely THIN — a block with
    fewer than `MIN_HOLDOUT_TRADES` closes; and UNDERPOWERED. Refused: CONTRADICTED
    (evidence against), no block at all, and the vintage INSUFFICIENT shapes (enough closes
    but no dispersion, or no period data) — those rows cannot state their own uncertainty and
    their path back in is a re-mint, not a slot. Branch order mirrors
    `robustness.holdout_status` so "thin" here is exactly the status function's closed-count
    branch.

    **UNDERPOWERED is admitted on the argument that already admits THIN** (Thomas 2026-08-29).
    A thin tail is let in because a slot's whole product is forward evidence, and a tail too
    small to judge is the case that needs it. An underpowered tail is the same case carrying
    MORE trades: its expectancy leans WITH the edge and its interval cannot resolve the lean
    from zero. Refusing it while admitting the thinner row is backwards — see
    `robustness.HOLDOUT_UNDERPOWERED` for the arithmetic (the median holdout runs 95 trades
    against the ~553 a 0.10R edge would need, so this is the common case, not the corner).

    The bar's other three terms are untouched and do the discriminating: FULL depth,
    `OBSERVATION_MIN_BACKTEST_CLOSED` closes, and a positive expectancy AT CURRENT COSTS. A
    row admitted here still has to clear all three, and CONTRADICTED — a tail that measured
    against the edge — is still refused.
    """
    from .robustness import (
        HOLDOUT_CONFIRMED, HOLDOUT_INSUFFICIENT, HOLDOUT_UNDERPOWERED, MIN_HOLDOUT_TRADES,
    )

    quality_status = candidate_quality(record)["holdout_status"]
    if quality_status in (HOLDOUT_CONFIRMED, HOLDOUT_UNDERPOWERED):
        return None
    if quality_status != HOLDOUT_INSUFFICIENT:
        return f"holdout={quality_status}"
    evidence = record.get("backtest_evidence")
    block = evidence.get("holdout") if isinstance(evidence, Mapping) else None
    if not isinstance(block, Mapping):
        return "holdout=INSUFFICIENT(no block)"
    closed = block.get("closed_count", block.get("closed"))
    if isinstance(closed, (int, float)) and not isinstance(closed, bool) \
            and closed < MIN_HOLDOUT_TRADES:
        return None  # thin: the shape observation exists to feed
    return f"holdout=INSUFFICIENT(vintage: {closed} closes, undispersed)"


def assert_observation_entry_bar(
    records: list[Mapping[str, Any]], *, occupying_candidate_ids: frozenset[str] = frozenset(),
) -> None:
    """Every ENTRANT clears the 5-3 bar, and a refusal names every failing term.

    Terms, all required: FULL depth among the RECORDED depths (SHALLOW is ranked and
    promotable elsewhere, but a slot is spent on the current window or not at all), at
    least `OBSERVATION_MIN_BACKTEST_CLOSED` backtest closes, positive expectancy at the
    CURRENT rates, and a holdout that is thin or better (`_observation_holdout_term`).
    Rows whose ``candidate_id`` is already occupying a slot are restatements, not entrants,
    and pass untouched.

    Deliberately NOT here: the cost-basis and unrecorded-depth axes, which their dedicated
    gates already refuse with their own audited escapes. Restating them would double-charge
    every use of those escapes — two waivers for one named decision — so this bar carries
    only the strictness that is NEW with it.

    Raises ``CANDIDATE_BELOW_OBSERVATION_ENTRY_BAR`` listing every failing candidate with
    every failing term — a bar that reports one term per run is a bar an operator meets
    four times.
    """
    failures: list[str] = []
    for record in records:
        cid = candidate_id(record)
        if cid in occupying_candidate_ids:
            continue
        quality = candidate_quality(record)
        terms: list[str] = []
        if quality["evidence_depth_rank"] == EVIDENCE_DEPTH_RANK_SHALLOW:
            terms.append(f"depth={quality['evidence_depth']}")
        closed = quality["closed_count"]
        if not isinstance(closed, (int, float)) or closed < OBSERVATION_MIN_BACKTEST_CLOSED:
            terms.append(f"closed={closed}<{OBSERVATION_MIN_BACKTEST_CLOSED}")
        exp_now = quality["expectancy_at_current_costs"]
        if not isinstance(exp_now, (int, float)) or not exp_now > 0:
            terms.append(f"expectancy_at_current={exp_now}")
        holdout_term = _observation_holdout_term(record)
        if holdout_term is not None:
            terms.append(holdout_term)
        if terms:
            failures.append(f"{quality['candidate_id']} ({record.get('strategy_id')}): "
                            + ", ".join(terms))
    if not failures:
        return
    raise ToolError(
        "CANDIDATE_BELOW_OBSERVATION_ENTRY_BAR",
        "an OBSERVATION slot buys forward evidence, and these entrants cannot honestly "
        f"supply it: {'; '.join(failures)}. Re-mint the lineage at the current window, or "
        "pass the explicit --allow-below-entry-bar escape.",
    )


def assert_family_cap(
    records: list[Mapping[str, Any]], *, occupying_entries: Sequence[Mapping[str, Any]] = (),
) -> None:
    """No strategy_family holds more than `OBSERVATION_FAMILY_CAP` occupied slots.

    Charged to ENTRANTS only, the same grandfathering as the entry bar: a family already
    over the cap among incumbents alone blocks nothing until a batch tries to ADD to it —
    the door must never refuse the promotion that is moving the pool AWAY from a
    concentration. Occupying incumbents count toward the base; a restated incumbent in the
    batch is counted once as base, never as an entrant.

    Raises ``CANDIDATE_FAMILY_CAP_EXCEEDED`` naming each family over its cap.
    """
    occupying_ids = {
        str(e.get("candidate_id")) for e in occupying_entries if e.get("candidate_id")
    }
    base: dict[str, int] = {}
    for entry in occupying_entries:
        family = str((entry.get("strategy_spec") or {}).get("strategy_family") or "?")
        base[family] = base.get(family, 0) + 1
    entrants: dict[str, list[str]] = {}
    for record in records:
        cid = candidate_id(record)
        family = str((record.get("strategy_spec") or {}).get("strategy_family") or "?")
        if cid in occupying_ids:
            continue  # a restatement is already in the base
        entrants.setdefault(family, []).append(cid)
    over = [
        f"{family}: {base.get(family, 0)} occupying + {len(cids)} entering "
        f"[{', '.join(cids)}] > {OBSERVATION_FAMILY_CAP}"
        for family, cids in sorted(entrants.items())
        if base.get(family, 0) + len(cids) > OBSERVATION_FAMILY_CAP
    ]
    if not over:
        return
    raise ToolError(
        "CANDIDATE_FAMILY_CAP_EXCEEDED",
        f"a family gets {OBSERVATION_FAMILY_CAP} occupied slots — the third sibling's "
        f"forward record is correlated with the first two, not independent: {'; '.join(over)}. "
        "Promote into another family, or pass the explicit --allow-family-overflow escape.",
    )


# Which of the derivations the store admits (:data:`pool_state.DERIVATION_TYPES`) a candidate may be
# PROMOTED on. Deliberately a separate literal rather than an alias of that set, and the difference
# is the whole point of the constant: that set answers "may the store hold this row", this one
# answers "may this row reach the live pool". Aliasing them would make every future addition to the
# closed set promotable on the day it was added — and the addition `docs/REMAINING_WORK.md` §I2 is
# designed around is a TRIAL family, whose rows exist to accrue evidence and must never trade.
# Widening the door is therefore its own edit here, in a diff that says so, rather than a side
# effect of widening the store.
PROMOTABLE_DERIVATION_TYPES = frozenset({"seeded_template", "crossover", "mutation"})


def assert_promotable_derivation(records: list[Mapping[str, Any]]) -> None:
    """Refuse a promotion of rows the store admits but the live pool must not take.

    Absence passes, on the same legacy rule :func:`validate_candidate_lineage` applies — 402
    rows in this store predate the field, and refusing them would be a schema vintage acting
    as a quality judgement (the `candidate_id` legacy rule, again). What is refused is a row
    that DOES name a derivation and names one outside
    :data:`PROMOTABLE_DERIVATION_TYPES`.

    **Today this refuses nothing**, because every derivation the store admits is also
    promotable. It is written now so that ceasing to be true takes an explicit edit at this
    door rather than an addition somewhere else: a quarantine is only load-bearing if it is
    already standing before the first row it must stop is minted, and the alternative — adding
    it in the same increment that starts minting them — is the shape of change that has
    historically been merged with one of its two halves missing.

    Raises `CANDIDATE_DERIVATION_NOT_PROMOTABLE`, naming every offending candidate."""
    refused = [
        (candidate_id(record), record.get("derivation_type"))
        for record in records
        if "derivation_type" in record
        and record.get("derivation_type") not in PROMOTABLE_DERIVATION_TYPES
    ]
    if refused:
        listed = ", ".join(f"{cid} ({kind!r})" for cid, kind in refused)
        raise ToolError(
            "CANDIDATE_DERIVATION_NOT_PROMOTABLE",
            f"were minted by a derivation the live pool does not take: {listed}. Promotable "
            f"derivations are {sorted(PROMOTABLE_DERIVATION_TYPES)} — re-mint the lineage "
            f"through the ordinary factory rotation, or pass the explicit "
            f"--allow-quarantined-derivation escape.",
        )



# The field the candidate store stamps on every row it appends (`pool_state.append_candidates`), and
# that `pool_state.read_candidates` recomputes on every read. Named here, where the door reads it.
RECORD_STAMP_FIELD = "record_sha256"


def assert_promotable_record_stamp(records: list[Mapping[str, Any]]) -> None:
    """Refuse a promotion of rows that carry no self-hash.

    ``read_candidates`` checks a stamped row's hash and refuses one that does not recompute
    (``CANDIDATES_TAMPERED``), but it cannot check a row that has no stamp: such a row reads as
    legacy and passes. That is also what a stamped row looks like once its ``record_sha256`` is
    removed. This door makes the missing stamp a refusal at the one place a row becomes a pool
    entry, so that removing the field buys nothing there.

    **Why absence is refused here, when :func:`assert_promotable_derivation` lets it pass.** A
    missing ``derivation_type`` is a schema vintage: 402 rows predate that field, and it says
    nothing about the row's integrity. A missing stamp is different. ``append_candidates`` has
    stamped every row it has written since the store began stamping, and the rows without one
    are a closed set that cannot grow through the store's own door: on this machine, measured
    2026-09-23, the first 41 of 3,243 lines, one import batch (``crypto_ai_system_import``,
    2026-07-16), every one of them already an active pool member and also refused on its
    unrecorded cost basis. A new unstamped row means a writer went around the append door or a
    stamp was removed.

    **Today this refuses nothing a promotion could reach**: the 41 are already in the pool (the
    backlog counts them as ``already_active``). A legacy rule that must come back (a retired one,
    say) can be passed with the explicit ``--allow-unstamped-record`` escape, which the ledger
    records, or re-minted through the factory, which stamps it.

    **What it does not do.** The self-hash carries no secret, so a writer who can edit the store
    can also recompute a stamp; this closes the downgrade that needs no recomputation, and no more.
    An explicit ``record_sha256: null`` is refused like an absent one: ``read_candidates`` skips
    verification on null, so null is exactly the downgrade case.

    Raises `CANDIDATE_RECORD_UNSTAMPED`, naming every offending candidate."""
    refused = [candidate_id(record) for record in records if record.get(RECORD_STAMP_FIELD) is None]
    if refused:
        raise ToolError(
            "CANDIDATE_RECORD_UNSTAMPED",
            f"carry no {RECORD_STAMP_FIELD}, so the store cannot say they are the rows it wrote: "
            f"{', '.join(refused)}. Re-mint the lineage through the factory (every appended row is "
            f"stamped), or pass the explicit --allow-unstamped-record escape.",
        )

# The smallest rolling window any lifecycle rule can act on (`lifecycle.DEFAULT_WINDOWS[0]`).
# Restated rather than imported. The reason recorded here was that `lifecycle` reached back into
# `pool`; it no longer does (checked in crypto PR7e-10), but the literal stays, and a test pins the
# two equal so a change to the ladder cannot leave this stale.
LIFECYCLE_MIN_WINDOW_TRADES = 20
