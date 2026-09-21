"""C7 strategy pool state — the active pool the cycle routes against, and the
imported-candidate store the C8 promotion flow will consume.

Two files under the crypto state directory:

- ``active_strategy_pool.json`` — the single pointer the runtime *reads*. The cycle
  only ever loads it; installing or changing it is an **operator door** (the import
  script's explicit ``--activate-pool``, and later C8's approval flow) — never a
  runtime side effect. A missing pool is honestly empty (no strategies, no entries);
  a malformed or spec-invalid pool raises so the cycle can refuse to route on
  tampered data rather than trade on whatever half-parses.
- ``strategy_candidates.jsonl`` — append-only candidates (C7 import provenance now,
  C8 factory output later). Candidates never route; only the active pool does.

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

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from runtime.read_only_kernel import integrity

from .. import jsonl
from ..errors import ToolError
from ..filelock import locked
from . import market_data
from .candidate_identity import (
    LINEAGE_FIELDS, PREDECESSOR_KEYS_FIELD, entry_attribution_keys, is_lineage_key, lineage_of,
    outcome_attribution_key, own_attribution_keys,
)
from .candidate_identity import candidate_id, derive_candidate_id  # noqa: F401 — re-exported:
# this store's many callers read the id rule as `pool.candidate_id`, and the rule itself
# moved to a leaf so `factory` no longer needs a module-level edge into this store.
# The ranking view and the two comparability tiers moved to `candidate_ranking` (strategy) in crypto
# PR7e-1. This store's doors, backlog and routing read them, and every public name is re-exported
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
from .paper import OCCUPYING_STATUSES, state_dir
from .robustness import HOLDOUT_CONFIRMED, ROBUST
from .strategy import Direction, SpecParseError, StrategySpec, load_strategy_pool
# `admission_evidence` is re-exported: the promotion door, the signal probe and every replay read it
# as `pool.admission_evidence`. It moved to the artifact's leaf, which hashes the projection (PR3a).
from .strategy_artifact import (  # noqa: F401
    ARTIFACT_SHA256_FIELD, admission_evidence, assert_pool_artifacts,
)

POOL_FILENAME = "active_strategy_pool.json"
CANDIDATES_FILENAME = "strategy_candidates.jsonl"

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
    from .lifecycle import TERMINAL_STATUSES  # local: avoids a module cycle

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
    from .lifecycle import TERMINAL_STATUSES  # local: avoids a module cycle

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
    from .lifecycle import TERMINAL_STATUSES  # local: avoids a module cycle

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
    from .lifecycle import TERMINAL_STATUSES  # local: avoids a module cycle

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
    :func:`routable_contexts` enumerates and what ``paper.route_entries`` actually judges.
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
    from .paper import MAX_DIRECTIONAL_SKEW  # local: pool is imported by paper's callers

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


# --- candidate identity (single source) ----------------------------------------
# `candidate_identity.py` owns the id rule now — a leaf both this store and `factory`
# import, which is what dissolved their module cycle. Re-exported at the top of this file.


# --- candidate lineage (fusion groundwork) --------------------------------------

# Closed set. ``seeded_template`` is fresh generation from the template library
# (no parents); the parented types name how a fused/derived child was produced.
# The factory ops that MINT parented candidates are a separate increment — the
# store admits them so the schema is one authority, not per-writer convention.
DERIVATION_TYPES = frozenset({"seeded_template", "crossover", "mutation"})
_PARENT_COUNT_RULES = {"seeded_template": (0, 0), "mutation": (1, 1), "crossover": (2, None)}


def validate_candidate_lineage(record: Mapping[str, Any], known_ids: frozenset[str]) -> None:
    """Fail-closed lineage check for one candidate row, at the append door.

    Rows written before lineage existed carry neither field and pass untouched
    (the ``candidate_id`` legacy rule — the append-only store is never rewritten).
    A row that does claim a derivation must be coherent: a known type, parents as
    a duplicate-free list of non-empty strings whose count fits the type (seeded
    has none, a mutation has exactly one, a crossover at least two), and every
    parent already durable in this store — so a child can never cite evidence
    that does not exist."""
    has_type = "derivation_type" in record
    has_parents = "parent_candidate_ids" in record
    if not has_type and not has_parents:
        return  # legacy row
    derivation = record.get("derivation_type")
    if not has_type:
        raise ToolError("CANDIDATE_LINEAGE_INVALID", "parent_candidate_ids without a derivation_type")
    if derivation not in DERIVATION_TYPES:
        raise ToolError("CANDIDATE_LINEAGE_INVALID", f"unknown derivation_type: {derivation!r}")
    parents = record.get("parent_candidate_ids", [])
    if not isinstance(parents, list) or not all(isinstance(p, str) and p for p in parents):
        raise ToolError("CANDIDATE_LINEAGE_INVALID", "parent_candidate_ids must be a list of non-empty ids")
    if len(set(parents)) != len(parents):
        raise ToolError("CANDIDATE_LINEAGE_INVALID", "duplicate parent_candidate_ids")
    lo, hi = _PARENT_COUNT_RULES[derivation]
    if len(parents) < lo or (hi is not None and len(parents) > hi):
        raise ToolError(
            "CANDIDATE_LINEAGE_INVALID",
            f"derivation_type {derivation!r} admits {lo}{'+' if hi is None else f'..{hi}'} parents, got {len(parents)}",
        )
    unknown = [p for p in parents if p not in known_ids]
    if unknown:
        raise ToolError("UNKNOWN_PARENT_CANDIDATE", f"parents not in the candidate store: {unknown}")


# Which of those derivations a candidate may be PROMOTED on. Deliberately a separate literal
# rather than an alias of :data:`DERIVATION_TYPES`, and the difference is the whole point of
# the constant: that set answers "may the store hold this row", this one answers "may this row
# reach the live pool". Aliasing them would make every future addition to the closed set
# promotable on the day it was added — and the addition `docs/REMAINING_WORK.md` §I2 is
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


def pool_path(root: Path | None = None) -> Path:
    return state_dir(root) / POOL_FILENAME


def candidates_path(root: Path | None = None) -> Path:
    return state_dir(root) / CANDIDATES_FILENAME


def assert_pool_identity_unique(pool: Mapping[str, Any]) -> None:
    """No two active entries may share a ``strategy_id`` or a ``candidate_id``, and no lineage is
    inherited by two entries.

    Both are keys the runtime resolves by: ``strategy_id`` selects the champion and
    keys every lifecycle status update, ``candidate_id`` names the lineage an outcome
    is attributed to. A duplicate makes routing, demotion and attribution ambiguous —
    the pool would silently pick one entry and update the other. Fail-closed at both
    doors (install and read) so a duplicate can neither be written nor traded on.

    **Inherited lineages (PR3c, Thomas decision 41).** An entry's
    :data:`~candidate_identity.PREDECESSOR_KEYS_FIELD` must be a list of lineage keys, and a
    ``cand:`` or ``gen:`` key in it may name neither another entry's own lineage nor what another
    entry inherited: either way one lineage's record would judge two entries. ``sid:`` keys are
    display names, the one imprecise join, and stay out of the comparison. Two entries' OWN keys
    are not compared either: the pool holds a rule installed twice, S008 and S008-GEN-696 (both
    SUSPENDED, sharing ``gen:GEN-696:…``, left as they are by Thomas decision 42), and refusing
    that here would stop the pool. A rule is kept unique at the promotion door."""
    entries = [e for e in pool.get("active_strategies") or [] if isinstance(e, Mapping)]
    seen_strategy: set[str] = set()
    seen_candidate: set[str] = set()
    for entry in entries:
        strategy_id = entry.get("strategy_id")
        if isinstance(strategy_id, str) and strategy_id:
            if strategy_id in seen_strategy:
                raise ToolError("STRATEGY_POOL_DUPLICATE", f"duplicate strategy_id in the pool: {strategy_id}")
            seen_strategy.add(strategy_id)
        candidate_id = entry.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id:
            if candidate_id in seen_candidate:
                raise ToolError("STRATEGY_POOL_DUPLICATE", f"duplicate candidate_id in the pool: {candidate_id}")
            seen_candidate.add(candidate_id)

    # Entries are told apart by position, not display id: an entry need not carry one, and two that
    # do not must not read as one owner (review of PR3c-1).
    def named(index: int) -> str:
        return str(entries[index].get("strategy_id") or f"entry #{index}")

    holders: dict[str, set[int]] = {}
    for index, entry in enumerate(entries):
        for key in own_attribution_keys(entry):
            holders.setdefault(key, set()).add(index)
    inherited_by: dict[str, int] = {}
    for index, entry in enumerate(entries):
        keys = entry.get(PREDECESSOR_KEYS_FIELD)
        if keys is None:
            continue
        if not isinstance(keys, list) or not all(is_lineage_key(key) for key in keys):
            raise ToolError(
                "STRATEGY_POOL_INVALID",
                f"{named(index)}: {PREDECESSOR_KEYS_FIELD} is not a list of lineage keys",
            )
        for key in keys:
            if key.startswith("sid:"):
                continue
            others = sorted(holders.get(key, set()) - {index})
            if others:
                raise ToolError(
                    "STRATEGY_POOL_DUPLICATE",
                    f"{named(index)} inherits {key}, the lineage {named(others[0])} holds as its own",
                )
            if inherited_by.setdefault(key, index) != index:
                raise ToolError(
                    "STRATEGY_POOL_DUPLICATE",
                    f"{key} is inherited by both {named(inherited_by[key])} and {named(index)}",
                )


def _read_active_pool(root: Path | None, *, artifacts: bool) -> dict[str, Any]:
    path = pool_path(root)
    if not path.is_file():
        return {"active_strategies": []}
    try:
        pool = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError("STRATEGY_POOL_UNREADABLE", f"active strategy pool unreadable: {type(exc).__name__}") from exc
    try:
        load_strategy_pool(pool)  # fail-closed structural validation, one bad spec poisons
    except SpecParseError as exc:
        raise ToolError("STRATEGY_POOL_INVALID", f"active strategy pool failed validation: {exc}") from exc
    assert_pool_identity_unique(pool)
    if artifacts:
        assert_pool_artifacts(pool)
    return pool


def load_active_pool(root: Path | None = None) -> dict[str, Any]:
    """The active pool, validated spec-by-spec, identity-unique, and every stamped entry still its
    artifact (PR3a, decision 34). Missing = empty."""
    return _read_active_pool(root, artifacts=True)


def read_pool_to_disarm(root: Path | None = None) -> dict[str, Any]:
    """The active pool for the one door that may only narrow it: every check but the artifacts'.

    A pool whose stamp no longer holds routes nothing (decision 34), and an operator repairing it
    must be able to take an entry off the money path first — otherwise an entry still at LIVE is
    armed again the moment the repaired pool loads. Disarming writes only OBSERVATION and so needs
    no proof of what the entry is. Every other reader, and every other writer, reads through
    :func:`load_active_pool`."""
    return _read_active_pool(root, artifacts=False)


def routable_strategy_ids(pool: Mapping[str, Any]) -> set[str]:
    """Every strategy id the pool can still route, by ``OCCUPYING_STATUSES``.

    The set the drawdown baseline's re-check is evaluated against: an outcome may leave the
    breaker's window only if the lineage that produced it is **not** in here, so re-promoting a
    retired strategy returns its losses to the drawdown without anyone re-registering anything.

    Deliberately membership-by-status and not by ``strategy_spec``, unlike
    :func:`routable_context_map` beside it. That one answers "which slot does this compete for",
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


# --- the live tier (#610 Part 1) ---------------------------------------------------------
#
# Occupying a routing slot and being allowed to spend real money were **one fact** until now:
# `OCCUPYING_STATUSES` answered both, so installing a strategy into the pool armed it for live
# orders on the next 15-minute cycle. `live_entry`'s check order has no per-strategy question in
# it at all — slot 2b was Gate 0 and it was removed 2026-08-03 for being unsatisfiable — so every
# remaining live gate asks whether this RUNTIME may trade, never whether this STRATEGY may.
#
# **Why a field and not a status, which is what the proposal first said.** The lifecycle ladder
# recovers a WARNING strategy back to `PAPER_ACTIVE` (`lifecycle.py:292`). Had the observation
# tier been a status, that recovery would move a strategy the operator deliberately kept off the
# money path INTO it — an automatic promotion into real money, arriving through the one mechanism
# this system says may only ever demote. The status write (`apply_status_decisions`, which
# `update_statuses` wraps) writes **only** `status` and the `lifecycle_*` fields (see its
# docstring), so a separate field cannot be reached by the ladder at all: the property
# is structural rather than guarded, and there is no rank ordering anybody has to get right.
#
# Absence means OBSERVATION. Every entry promoted before this existed therefore stops being
# live-routable the moment this ships, which is the intended migration and the fail-closed
# direction: a pool that predates the distinction cannot assert the permissive half of it.
LIVE_TIER_FIELD = "live_tier"
LIVE_TIER_LIVE = "LIVE"
LIVE_TIER_OBSERVATION = "OBSERVATION"
LIVE_TIERS = frozenset({LIVE_TIER_LIVE, LIVE_TIER_OBSERVATION})
# The Thomas approval a LIVE entry was armed under (PR2b, decision 17). The promotion door writes it
# beside the tier; the disarm door removes it with the tier. The pre-order gate refuses an entry for
# a LIVE strategy that names none, so an entry armed by hand without an id is armed for nothing.
# Since PR2c-2b the gate also verifies the record the id names (`promotion.live_arm_problem`): an
# approval Thomas answered to arm this candidate at LIVE, whose window the entry was installed in.
LIVE_TIER_APPROVAL_FIELD = "live_tier_approval_id"


def entry_live_tier(entry: Mapping[str, Any]) -> str:
    """This entry's tier, defaulting to OBSERVATION — including for an unrecognised value.

    A tier this code does not know is not a reason to allow real money; it is a reason to refuse
    until somebody says what it means. Same direction as an absent field."""
    value = entry.get(LIVE_TIER_FIELD)
    return LIVE_TIER_LIVE if value == LIVE_TIER_LIVE else LIVE_TIER_OBSERVATION


def live_routable_strategy_ids(pool: Mapping[str, Any]) -> set[str]:
    """Every strategy id that may open a REAL position: occupying **and** in the live tier.

    Strictly narrower than :func:`routable_strategy_ids`, and the two answer different
    questions — that one is "could this trade again at all", which the drawdown baseline needs
    and which an observation-tier strategy still answers YES to (it papers, and its live history
    stays attributable). This one is "may this spend money", and nothing infers it: the entry
    has to say so.

    A pool that cannot be read must never arrive here as an empty dict. Empty means "no strategy
    is live-routable", which refuses every entry — safe — but the caller still owes the
    distinction, because the same shape reaching :func:`routable_strategy_ids` would release a
    drawdown exclusion instead."""
    return {
        str(entry.get("strategy_id"))
        for entry in (pool.get("active_strategies") or [])
        if isinstance(entry, Mapping)
        and entry.get("status") in OCCUPYING_STATUSES
        and entry.get("strategy_id")
        and entry_live_tier(entry) == LIVE_TIER_LIVE
    }


def _spec_rule_hash(spec: Any) -> str | None:
    """The rule hash of the spec an entry trades, or None when it does not parse."""
    try:
        return StrategySpec.from_dict(spec).strategy_rule_hash
    except Exception:  # noqa: BLE001 — a spec that does not parse arms nothing
        return None


def live_arm_entries(pool: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """``strategy_id -> what its LIVE arm stands on`` for every live-routable entry (PR2c-2b).

    The same membership as :func:`live_routable_strategy_ids`. Each value names:

    - the approval the entry was armed under (``None`` when it names none, which the pre-order
      gate refuses rather than infers);
    - the lineage it arms: its candidate, the rule hash its label names, and the hash of the spec
      it actually trades (``spec_rule_hash``, None when the spec does not parse);
    - the artifact it was installed as (``strategy_artifact_sha256``, PR3a), None when the entry
      predates the artifact. A pool that loaded has checked every stamp against the entry's content;
    - when the promotion door installed it, and when the disarm door last took the tier away
      (``disarmed_at``; the door never installs an entry that carries it)."""
    armed: dict[str, dict[str, Any]] = {}
    for entry in pool.get("active_strategies") or []:
        if not (isinstance(entry, Mapping) and entry.get("status") in OCCUPYING_STATUSES
                and entry.get("strategy_id") and entry_live_tier(entry) == LIVE_TIER_LIVE):
            continue
        approval = entry.get(LIVE_TIER_APPROVAL_FIELD)
        armed[str(entry.get("strategy_id"))] = {
            "approval_id": approval.strip() if isinstance(approval, str) and approval.strip() else None,
            "candidate_id": entry.get("candidate_id"),
            "strategy_rule_hash": entry.get("strategy_rule_hash"),
            "spec_rule_hash": _spec_rule_hash(entry.get("strategy_spec")),
            ARTIFACT_SHA256_FIELD: (entry.get(ARTIFACT_SHA256_FIELD)
                                    if isinstance(entry.get(ARTIFACT_SHA256_FIELD), str)
                                    and entry.get(ARTIFACT_SHA256_FIELD) else None),
            "promoted_at": entry.get("promoted_at"),
            "disarmed_at": entry.get("live_tier_updated_at"),
        }
    return armed


def live_arm_unsound(armed: Mapping[str, Any]) -> str | None:
    """Why one :func:`live_arm_entries` value arms nothing whatever approval it names, or None. Pure.

    - ``spec``: the spec it trades is not the rule its label names. The router trades the spec, and
      the approval is checked against the label, so the two must be one rule.
    - ``disarmed``: it carries the disarm door's trace, so it was put back in the tier by hand. The
      promotion door installs every entry fresh. Named before ``unbound``: it says someone edited
      the pool by hand, which is the more useful thing for an operator to read.
    - ``unbound``: it carries no artifact stamp (PR3a, decision 33). Only an entry the door
      installed as an artifact Thomas's approval names may spend money; an older entry papers."""
    if armed.get("spec_rule_hash") is None or armed.get("spec_rule_hash") != armed.get("strategy_rule_hash"):
        return "spec"
    if armed.get("disarmed_at") is not None:
        return "disarmed"
    if not armed.get(ARTIFACT_SHA256_FIELD):
        return "unbound"
    return None


def live_arm_approvals(pool: Mapping[str, Any]) -> dict[str, str | None]:
    """``strategy_id -> the approval it was armed LIVE under`` for every live-routable entry
    (:func:`live_arm_entries`). An entry :func:`live_arm_unsound` names reads as armed under none,
    so both reads an entry door makes refuse it (review of #887)."""
    return {sid: None if live_arm_unsound(armed) else armed["approval_id"]
            for sid, armed in live_arm_entries(pool).items()}


def disarm_live_tier(
    strategy_ids: Sequence[str], *, root: Path | None = None, now: str, reasons: Sequence[str] = (),
) -> int:
    """Move named entries OUT of the live tier. Locked. Returns how many actually moved.

    **The only automatic writer of ``live_tier``, and it can only ever write OBSERVATION.**
    That is a property of the signature rather than of the caller: there is no argument for a
    target tier, so no caller — present or future, correct or confused — can arm a strategy
    through here. Arming stays the operator promotion door, which is what #610 Part 1 bought
    and what this must not spend.

    It is the same asymmetry the lifecycle ladder already runs on ("auto-degradation is
    permitted; auto-reactivation is not"), applied to the tier instead of the status, and for
    the same reason: taking permission away on a machine's own judgement is safe in a way that
    handing it out is not.

    Deliberately **not** in `lifecycle`. The ladder is asserted never to name this field
    (`test_the_lifecycle_ladder_cannot_arm_a_strategy`), and that assertion is worth more than
    the convenience of one module: it means the recovery path `WARNING -> PAPER_ACTIVE` cannot
    touch the tier even by accident. A separate writer keeps the ladder's blast radius exactly
    where Part 1 put it.

    Silent on an id the pool does not hold. A caller judging live outcomes may legitimately name
    a lineage that has since been retired out of the pool entirely, and refusing there would
    turn a stale name into a cycle failure — the outcome has already been recorded, and the
    lineage is already not routable.

    Reads past an artifact stamp that no longer holds (:func:`read_pool_to_disarm`, PR3a): such a
    pool routes nothing, and the operator must be able to disarm before repairing it. The entries
    it does not name are written back exactly as read, so the pool stays refused until repaired.
    """
    ids = {str(s) for s in strategy_ids if isinstance(s, str) and s}
    if not ids:
        return 0
    path = pool_path(root)
    with locked(path.with_suffix(".lock"), code="STRATEGY_POOL_LOCKED", label="active strategy pool"):
        pool = read_pool_to_disarm(root)
        moved = 0
        for entry in pool.get("active_strategies") or []:
            if not isinstance(entry, Mapping) or str(entry.get("strategy_id")) not in ids:
                continue
            if entry_live_tier(entry) != LIVE_TIER_LIVE:
                continue
            entry[LIVE_TIER_FIELD] = LIVE_TIER_OBSERVATION
            # The approval that armed it goes with the tier: a later re-arm is a new approval.
            entry.pop(LIVE_TIER_APPROVAL_FIELD, None)
            entry["live_tier_updated_at"] = now
            entry["live_tier_reasons"] = [str(r) for r in reasons]
            moved += 1
        if moved:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)
        return moved


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


def install_active_pool(pool: dict[str, Any], *, root: Path | None = None) -> int:
    """Install (replace) the active pool — the OPERATOR door, not a runtime call.

    Validates every spec, the identity invariant and every artifact stamp first (fail-closed),
    then writes atomically: a pool the read door would refuse is never written. Returns the number
    of strategies installed. Callers are operator scripts acting on an explicit confirmation (the
    pre-R10 promotion posture); the runtime cycle never calls this."""
    specs = load_strategy_pool(pool)
    assert_pool_identity_unique(pool)
    assert_pool_artifacts(pool)
    path = pool_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code="STRATEGY_POOL_LOCKED", label="active strategy pool"):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    return len(specs)


# A lifecycle decision the pool write did not apply (PR3b-2, Thomas decision 36): its display id no
# longer names the lineage it judged, in the status it judged, or no longer names an entry at all.
LIFECYCLE_DECISION_STALE = "LIFECYCLE_DECISION_STALE"


def _stale_decision(decision: Mapping[str, Any], entry: Mapping[str, Any]) -> str | None:
    """Why ``decision`` may not move ``entry``, or None. A decision is about what it judged — a
    lineage, in a status — never the display id: the pool can change between the read the decision
    was made on and this locked write. The id may then name another lineage (a promotion installed
    one in its place), or the same lineage installed again fresh, or one another writer moved."""
    if not all(field in decision for field in (*LINEAGE_FIELDS, "previous_status")):
        return "the decision does not name the lineage and status it judged"
    judged, holding = lineage_of(decision), lineage_of(entry)
    if judged != holding:
        return f"judged {judged}, the pool now holds {holding}"
    current = str(entry.get("status") or "PAPER_ACTIVE")
    if str(decision.get("previous_status")) != current:
        return f"judged it {decision.get('previous_status')}, it is {current} now"
    return None


def update_statuses(
    decisions: list[dict[str, Any]], *, root: Path | None = None,
    updated_by: str = "lifecycle_agent", now: str | None = None,
) -> int:
    """:func:`apply_status_decisions`, all or nothing: returns how many entries changed status, and
    refuses the whole batch rather than skip a decision silently."""
    return apply_status_decisions(decisions, root=root, updated_by=updated_by, now=now,
                                  all_or_nothing=True)["changed"]


def apply_status_decisions(
    decisions: list[dict[str, Any]], *, root: Path | None = None,
    updated_by: str = "lifecycle_agent", now: str | None = None, all_or_nothing: bool = False,
) -> dict[str, Any]:
    """Apply lifecycle status transitions to the active pool (C10). Locked, guarded.

    Returns ``{"changed": int, "stale": [...]}``. A decision is applied only to what it judged
    (PR3b-2, Thomas decision 36): the lineage its display id names now, in the status it judged.
    One whose id names another lineage or another status by now, or no entry at all, or that does
    not say what it judged, is stale:

    - by default (the cycle's lifecycle) it is skipped and reported in ``stale``, and the rest of
      the batch is applied — a demotion held back for one stale decision would be the less safe
      outcome, and the next cycle judges the entry again;
    - ``all_or_nothing`` (an operator retirement, approved as a set) refuses the whole batch and
      writes nothing: ``LIFECYCLE_UNKNOWN_STRATEGY`` for an id no entry holds,
      :data:`LIFECYCLE_DECISION_STALE` for the rest.

    Nothing is written when no decision was applied.

    The narrowest possible pool mutation: only ``status``, the running
    ``lifecycle_consecutive_failures``, and the ``lifecycle_*`` provenance of named
    strategies change — specs, hashes, scores and membership are untouched, so this can
    never smuggle a promotion. Guards, in order, each fail-closed: a transition record that isn't
    an evaluate_lifecycle decision shape is refused; a stale decision (above); a CURRENTLY terminal
    entry is immutable (reactivation is the approval door, never this). ``changed`` counts the
    entries whose status actually changed.

    The provenance fields are written only on a status CHANGE, alongside
    ``lifecycle_updated_at`` and ``lifecycle_decision_id``, so they always describe the
    transition that produced the status the entry is currently in. Entries transitioned
    before these fields existed carry none: absent means "written by an older runtime",
    which is a different answer from an empty list and is why the reader treats it as
    unknown rather than as no reason.

    **The pool header is stamped as a PAIR.** This used to set ``updated_by`` and leave
    ``updated_at`` alone, which is worse than setting neither: the two fields describe one
    event, so a reader gets a current writer against a timestamp from whenever the
    promotion door last ran. Measured on the live host 2026-08-08 — the file was rewritten
    at 11:29 by the 15-minute pool cycle and its ``updated_at`` read ``2026-07-31T10:04:33Z``,
    earlier even than the last transition it had itself recorded (10:07:15Z). "Nothing has
    happened since Jul 31" and "eight days of cycles have run" are indistinguishable.

    ``now`` is that stamp. It defaults to the newest ``created_at_utc`` among ``decisions``,
    which is not a fallback but the better answer in the ordinary case: it is the moment this
    write's transitions were DECIDED, so the header agrees with the ``lifecycle_updated_at``
    the same call wrote onto the entries instead of being independently sourced. Every
    decision shape this accepts carries one; a caller with its own clock may pass ``now`` and
    override. The stamp lands on every write, like ``updated_by`` — "when was this file last
    written, and by whom" is the question the pair answers, and per-entry
    ``lifecycle_updated_at`` remains the one that says when a given strategy last moved."""
    from .lifecycle import TERMINAL_STATUSES  # local: avoids a module cycle

    if not decisions:
        return {"changed": 0, "stale": []}
    path = pool_path(root)
    with locked(path.with_suffix(".lock"), code="STRATEGY_POOL_LOCKED", label="active strategy pool"):
        pool = load_active_pool(root)
        entries = {e.get("strategy_id"): e for e in pool.get("active_strategies") or []}
        changed = applied = 0
        stale: list[dict[str, Any]] = []
        for decision in decisions:
            strategy_id = decision.get("strategy_id")
            new_status = decision.get("new_status")
            if not (isinstance(strategy_id, str) and strategy_id and isinstance(new_status, str)):
                raise ToolError("LIFECYCLE_DECISION_INVALID", "transition lacks strategy_id/new_status")
            entry = entries.get(strategy_id)
            problem = (f"no pool entry for {strategy_id}" if entry is None
                       else _stale_decision(decision, entry))
            if problem is not None:
                if all_or_nothing and entry is None:
                    raise ToolError("LIFECYCLE_UNKNOWN_STRATEGY", f"no pool entry for {strategy_id}")
                if all_or_nothing:
                    raise ToolError(LIFECYCLE_DECISION_STALE, f"{strategy_id}: {problem}")
                # Before the terminal check: a decision about another lineage or status says
                # nothing about the entry the id holds now, terminal or not.
                stale.append({"strategy_id": strategy_id, "new_status": new_status, "problem": problem})
                continue
            if str(entry.get("status")) in TERMINAL_STATUSES:
                raise ToolError(
                    "LIFECYCLE_TERMINAL_IMMUTABLE",
                    f"{strategy_id} is terminal; reactivation is the approval door, not a transition",
                )
            applied += 1
            entry["lifecycle_consecutive_failures"] = int(decision.get("consecutive_failures") or 0)
            if new_status != entry.get("status"):
                entry["status"] = new_status
                entry["lifecycle_updated_at"] = decision.get("created_at_utc")
                entry["lifecycle_decision_id"] = decision.get("strategy_lifecycle_decision_id")
                # WHY, on the entry, because the entry is what anyone reads first. Without it a
                # SUSPENDED row shows `lifecycle_consecutive_failures: 0` and nothing else, and
                # 0 means two opposite things: a performance suspension always carries a streak
                # of at least `suspend_consecutive`, while an operator retirement carries the
                # count **forward untouched** — so a strategy retired for being a duplicate
                # looks exactly like one the metrics condemned, and the difference lives only
                # in the control ledger. Measured here: five 1d entries read as demoted on zero
                # failures, and only `reasons: ["operator_retired"]` in the ledger said they
                # were duplicate rules an operator removed on purpose.
                reasons = decision.get("reasons")
                entry["lifecycle_reasons"] = (
                    [str(r) for r in reasons] if isinstance(reasons, list) else []
                )
                # Attribution when there is one. `retired_by` exists only on an operator
                # retirement, so its presence is itself the discriminator — and "who" is the
                # question a reader asks immediately after "why".
                retired_by = decision.get("retired_by")
                if isinstance(retired_by, str) and retired_by:
                    entry["lifecycle_retired_by"] = retired_by
                changed += 1
        if not applied:
            # Every decision was stale: the pool stands as read, header included.
            return {"changed": 0, "stale": stale}
        pool["updated_by"] = updated_by
        # Never widen to `or ""`: an empty stamp would overwrite a true timestamp with a
        # blank, which is the one outcome worse than the stale one being fixed here.
        stamp = now or max(
            (str(d.get("created_at_utc")) for d in decisions
             if isinstance(d.get("created_at_utc"), str) and d.get("created_at_utc")),
            default="",
        )
        if stamp:
            pool["updated_at"] = stamp
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        return {"changed": changed, "stale": stale}


def as_pool_entry_for_replay(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """A candidate seen as the pool entry it would become, for replay ONLY.

    Carries the admission evidence and nothing else a promotion would add — no status, no
    lifecycle counters, no ``promoted_at``. It is an argument to a pure walk, never a row to
    write. An entry that already carries its own evidence is returned untouched, so probing
    a pooled lineage measures the pool, not a reconstruction of it."""
    if candidate.get("regime_evidence") is not None or candidate.get("distribution_reference") is not None:
        return dict(candidate)
    return {**candidate, **admission_evidence(candidate)}


def read_candidates(root: Path | None = None) -> list[dict[str, Any]]:
    """All candidate rows, oldest first — a VERIFIED read.

    Any row carrying a ``record_sha256`` (everything :func:`append_candidates` has
    written since the store began stamping) must recompute it exactly; a mismatch
    raises ``CANDIDATES_TAMPERED`` so promotion asks/executions fail closed rather
    than binding Thomas's approval to silently edited evidence. Rows persisted
    before stamping existed have no hash to check — documented gap, closed for
    every new row."""
    path = candidates_path(root)
    rows: list[dict[str, Any]] = []
    # Streams rather than materializing the store twice, and keeps ToolError: 47 sites in the
    # runtime catch it by name — five of them in `promotion.py`, which reads this very store —
    # so raising jsonl's PersistenceError here would fail past those handlers, not at them.
    for lineno, record in jsonl.iter_numbered(
        path,
        read_code="CANDIDATES_UNREADABLE",
        label="strategy candidates",
        exc_type=ToolError,
    ):
        if not isinstance(record, dict):
            continue
        stored = record.get("record_sha256")
        if stored is not None:
            body = {k: v for k, v in record.items() if k != "record_sha256"}
            if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
                raise ToolError(
                    "CANDIDATES_TAMPERED", f"strategy candidates line {lineno} fails its self-hash"
                )
        rows.append(record)
    return rows


def append_candidates(records: list[dict[str, Any]], *, root: Path | None = None) -> int:
    """Append candidate records (operator/import door). Returns the count written.

    The store stamps each row's ``record_sha256`` at append time (over the full row,
    import marks included), so tamper evidence starts the moment a row becomes
    durable — provenance-independent, unlike the outcomes store's build-time hash.

    Lineage is validated under the same lock, against the rows durable BEFORE this
    batch — a parent must already exist in the store, never in the batch that cites
    it (fusion reads its parents from the store first). All-or-nothing: one invalid
    row refuses the whole batch before anything is written."""
    path = candidates_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code="CANDIDATES_LOCKED", label="strategy candidates"):
        known_ids = frozenset(candidate_id(r) for r in read_candidates(root))
        for record in records:
            validate_candidate_lineage(record, known_ids)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            for record in records:
                row = dict(record)
                if "record_sha256" not in row:
                    row["record_sha256"] = integrity.sha256_record(row)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(records)


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

# The smallest rolling window any lifecycle rule can act on (`lifecycle.DEFAULT_WINDOWS[0]`).
# Restated rather than imported because `lifecycle` reaches back into this module and the
# existing precedent here is a local import inside the one function that needs it; a test pins
# the two equal so a change to the ladder cannot leave this stale.
LIFECYCLE_MIN_WINDOW_TRADES = 20

# How long a lineage may take to reach that window before the backlog stops advertising it.
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

    - evidence at a basis the door accepts (:data:`PROMOTABLE_COST_BASIS_RANKS`); an
      OPTIMISTIC row is refused at the ask, so counting it would advertise work that
      cannot be done
    - and at a depth it accepts (:data:`PROMOTABLE_EVIDENCE_DEPTH_RANKS`), for exactly
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
        # second. Zero on today's store — see `assert_promotable_derivation` for why the axis
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
