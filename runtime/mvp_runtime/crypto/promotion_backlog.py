"""The promotion backlog: how many lineages an operator could promote right now and has not, and why the
rest cannot (crypto PR7e-11).

:func:`promotable_backlog` applies the promotion door's own chain to the candidate store (the promotable
sets from `pool_admission`, the ranking from `candidate_ranking`), then a filter of its own that the door
does not apply, the lifecycle window (from `pool_admission`), and reports the lineages left. Each refused row is charged to the first axis in
:data:`BACKLOG_REFUSAL_AXES` that drops it. The daily board (`dashboard`) reads it, and raises a line
once the count reaches :data:`PROMOTION_BACKLOG_ALERT_THRESHOLD`. The backlog reads and reports; it
refuses and decides nothing.

This was `pool`'s until crypto PR7e-11, the last of its roles to leave. It reads the stored pool and the
candidates through `pool_state` and the door's sets through `pool_admission`, and nothing of `pool`'s,
so `pool` can import it: `pool` re-exports every public name here as the same object, and its callers
keep reading `pool.<name>`. A patch on `pool` does not reach the functions here, which read this
module's names.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from . import market_data
from .candidate_identity import candidate_id
from .candidate_ranking import (
    _is_number, attempt_context_key, attempts_by_context, candidate_quality, pooled_context_keys,
    rank_candidates,
)
from .pool_admission import (
    LIFECYCLE_MIN_WINDOW_TRADES, PROMOTABLE_COST_BASIS_RANKS, PROMOTABLE_DERIVATION_TYPES,
    PROMOTABLE_EVIDENCE_DEPTH_RANKS, RECORD_STAMP_FIELD,
)
from .pool_state import load_active_pool, read_candidates
from .robustness import HOLDOUT_CONFIRMED, ROBUST

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
    "unstamped",
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
        # The door's other axis about the row rather than its evidence
        # (`pool_admission.assert_promotable_record_stamp`), counted here on the same rule: a row
        # the ask will refuse must not be advertised. Zero on today's store, where the 41
        # unstamped rows are all `already_active`.
        if record.get(RECORD_STAMP_FIELD) is None:
            refused["unstamped"] += 1
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
