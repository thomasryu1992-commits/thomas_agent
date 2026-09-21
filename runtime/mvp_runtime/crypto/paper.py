"""C5 paper position kernel — entry routing, simulated settlement, gated state.

Ports the source system's runtime entry router (``entry_strategy_router_agent.py``,
single-symbol form) and canonical paper position kernel
(``execution/paper_position_kernel.py``) onto this runtime's R8 pattern. The pure
parts (routing, entry plan, settlement math, outcome records) run at ALLOW tier; the
**only** effectful step — persisting paper state — is EXECUTE_AND_REPORT behind a new
``paper_trading`` safety-flag provider on the existing ``filesystem_write`` flag:
:class:`DryRunPaperStore` is the default (computes everything, persists nothing), the
real store is constructed solely through ``safety_gate.select_env_gated`` and re-asserts
its authorization at every mutating call, and the chokepoint :func:`run_paper_update`
is kill-switch bound (``kill_blocks: tool_write``) exactly like R8's ``run_write``.

Source rules kept verbatim: one order per cycle no matter how many strategies agree
(supporting ids ride along for attribution); same-symbol direction conflicts fail
closed (``BLOCK_STRATEGY_DIRECTION_CONFLICT``) rather than guessing; suspended and
archived strategies cannot open positions; settlement precedence is manual exit →
intrabar SL/TP (**pessimistic SL-first**) → time exit; ``holding_candles`` advances
once per distinct candle; the outcome's ``result_R`` is the entry-to-exit move over
the entry risk. Accounting is R-based only (no quantity/pnl fields) — R is what the
C4 risk guard and C6 feedback consume, and paper sizing added nothing but noise.

Paper state lives under the runtime's own gitignored governance-state directory —
private runtime state like the ledger, deliberately NOT the R8 ``workspace/`` (whose
create-only rule fits deliverables, not a position file that updates every cycle).
Reads (open position, outcome history) are ungated ALLOW-tier module functions; an
unreadable outcome history raises so the caller routes it into
``guards.risk_guard_unreadable`` — never silently an empty history.

Since crypto PR7c the pure trade-plan maths (the entry plan, settlement, the managed stop and the
outcome record) lives in ``trade_plan``, and the labels it stamps into records live in ``vocabulary``.
This module keeps the stateful kernel and the router, and re-exports both sets of names as the same
objects, so the factory backtest and the forward book no longer import the kernel to reach the maths.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from runtime.read_only_kernel import integrity

from .. import jsonl, safety_gate, timeutil
from ..control import ControlStore
from ..errors import ToolBlocked, ToolError
from ..filelock import locked
from ..paths import RESERVED_BASENAMES, repo_root as _repo_root
from ..safety_gate import FILESYSTEM_WRITE, Authorization
from .distribution_gate import distribution_admits
from .candidate_identity import PREDECESSOR_KEYS_FIELD, entry_attribution_keys, outcome_attribution_key
from .strategy import StrategySpec, evaluate_spec
from .strategy_artifact import ARTIFACT_SHA256_FIELD
# The trade-plan maths lives in `trade_plan` (strategy) and the record labels in `vocabulary` since
# crypto PR7c; both are re-exported here, as the same objects, for this module's many importers.
from .trade_plan import (  # noqa: F401
    ASSUMED_LEVERAGE,
    COOLDOWN_BARS_AFTER_STOPLOSS,
    DEFAULT_MAX_HOLD_BARS,
    ENTRY_COST_UNECONOMIC,
    MAINTENANCE_MARGIN_RATE,
    MAX_HOLD_BARS,
    MIN_REGIME_TRADES_TO_EXCLUDE,
    MIN_VOL_SIZE_MULTIPLIER,
    REGIME_EXCLUDED,
    STOP_BEYOND_LIQUIDATION,
    advance_holding,
    advance_managed_stop,
    build_entry_plan,
    build_outcome_record,
    entry_cost_refusal,
    liquidation_price,
    open_position,
    position_max_hold,
    regime_admits,
    resolve_intrabar_exit,
    settle_trade_plan,
    stop_beyond_liquidation_refusal,
    stop_is_beyond_liquidation,
    volatility_size_multiplier,
    touches as _touches,
)
from .vocabulary import (  # noqa: F401
    BLOCK_DIRECTION_CONFLICT,
    DEFAULT_VENUE,
    OCCUPYING_STATUSES,
    PAPER_KERNEL_VERSION,
    PAPER_PROVENANCE,
    STATUS_BLOCKED,
    STATUS_ENTRY_CANDIDATE,
    STATUS_NO_ENTRY,
)

PAPER_TOOL_ID = "crypto.paper.kernel"
PAPER_TOOL_VERSION = "0.1.0"
PAPER_TOOL_CLASS = "write"

PAPER_ENV = "MVP_PAPER_TRADING"
REAL_PAPER = "real"
PAPER_PROVIDER_ID = "paper_trading"
_WRITE_FLAGS = (FILESYSTEM_WRITE,)

from .state import STATE_REL, state_dir  # noqa: E402  (one root for both trading planes; re-exported for this module's many importers)
# See vocabulary.R_BASIS_* — paper R is measured on intended fills, now NET of fees and slippage.
from .vocabulary import R_BASIS_INTENT_NET  # noqa: E402  (constant only; no I/O at import)
# Outcomes carried in from the frozen crypto_AI_System (scripts/import_crypto_history.py).
# They are REAL closed trades, but produced by different code, so anything reporting "how is
# THIS runtime doing" must not silently blend them with its own (see split_by_provenance).
IMPORTED_PROVENANCE = "crypto_ai_system_import"

POSITION_FILENAME = "paper_position.json"  # legacy single-slot file (read-only path)
POSITIONS_DIRNAME = "positions"
OUTCOMES_FILENAME = "paper_outcomes.jsonl"


# Concurrency limits. Before context-keyed positions there was ONE global slot, so
# "at most one open position" was an accidental risk cap that nothing named.
# Splitting the key removed that cap, so the limit is named here.
#
# The derived value is:
#
#   abs(guards.DAILY_MAX_LOSS_R) = 2
#
# at the pool's standard ``max_risk_per_trade_R: 1.0`` — if every open position
# stops out on the same day, the realized loss equals the daily circuit breaker
# instead of blowing through it. The breaker reacts to *realized* losses only and
# cannot see open exposure, which is exactly why a pre-trade cap has to exist.
#
# **The live value deliberately breaks that derivation** (explicit Thomas decision
# 2026-07-23, paper-trading evaluation phase). At 20 concurrent positions the
# worst-case same-day realized loss is 20R against a 2R breaker, and because the
# breaker is reactive it cannot prevent that — it only stops the *next* entry after
# the losses land. This is accepted because the exposure is simulated: these caps
# bound the PAPER kernel only. Real orders go through ``live_order``/``live_pnl``,
# which neither read nor share this limit, so widening it cannot reach live money —
# and that separation is what has to hold for this number to stay acceptable.
#
# The cap is what makes outcome data accumulate fast enough to judge strategies at
# all: with 1d specs holding 14-39 bars, two slots yield roughly two outcomes a
# month. **Revert to the derived value before the paper book is ever used to size
# live exposure** — that is a separate explicit decision, and this comment is the
# record of what it undoes.
#
# Per-symbol raised 2 -> 4 (Thomas 2026-07-25) now that 15m/1h/4h/1d all route: a
# book is one position per (symbol, timeframe), so a symbol can hold at most one per
# active timeframe = 4. At 2 the cap silently blocked half a symbol's timeframes
# from ever holding a position at once; 4 lets each timeframe occupy its own slot
# without the per-symbol limit shadowing the natural per-context limit. The global
# MAX_CONCURRENT_POSITIONS (20 = 5 symbols x 4 timeframes) stays the portfolio bound,
# and this is still the PAPER kernel only (live_order/live_pnl never read it).
MAX_CONCURRENT_POSITIONS = 20
MAX_POSITIONS_PER_SYMBOL = 4


# Same-bar routing priority is decided on REALIZED evidence when there is enough of it.
# The floor reuses `feedback.HEALTHY_TRADES_PER_PARAMETER`'s judgement (10, "under ~5 it
# is noise"): below it a realized expectancy is a coin path, and the ranking falls back to
# `champion_score` exactly as before the fast-context cap (Thomas 2026-08-24) existed.
MIN_PRIORITY_SAMPLE_TRADES = 10

# Shadow-reason tags for the signals the router declined this bar. Each is its own
# per-reason bucket in the counterfactual registry, so gate calibration never mixes with
# routing-priority evidence — and a benched lineage still accrues judgeable outcomes.
ROUTED_BEHIND_PRIMARY = "ROUTED_BEHIND_PRIMARY"
DIRECTION_CONFLICT_LOST = "DIRECTION_CONFLICT_LOST"
DIRECTION_CONFLICT_UNRESOLVED = "DIRECTION_CONFLICT_UNRESOLVED"
SUPPORTING_SHADOW_REASONS = frozenset({
    ROUTED_BEHIND_PRIMARY, DIRECTION_CONFLICT_LOST, DIRECTION_CONFLICT_UNRESOLVED,
})
POSITION_CONTEXT_MISMATCH = "POSITION_CONTEXT_MISMATCH"

# Intrabar exit resolution: when a bar touches both the stop and the target it cannot
# say which came first. Finer bars can. 1m is the finest the venue serves and divides
# every authorable timeframe evenly.
INTRABAR_TIMEFRAME = "1m"
INTRABAR_RESOLUTION_DEGRADED = "INTRABAR_RESOLUTION_DEGRADED"
POSITION_LIMIT_PORTFOLIO = "POSITION_LIMIT_PORTFOLIO"
POSITION_LIMIT_SYMBOL = "POSITION_LIMIT_SYMBOL"
POSITION_LIMIT_DIRECTIONAL = "POSITION_LIMIT_DIRECTIONAL_SKEW"
SETTLEMENT_ALREADY_RECORDED = "SETTLEMENT_ALREADY_RECORDED"
SETTLEMENT_UNVERIFIABLE = "SETTLEMENT_UNVERIFIABLE"
SETTLEMENT_RACE_LOST = "SETTLEMENT_RACE_LOST"
# The freshness gate held a new entry: this context was already evaluated for the
# current closed candle, so a coarser-timeframe strategy is not re-entered every tick.
CANDLE_NOT_FRESH = "CANDLE_NOT_FRESH"
# A stop-loss just settled on this context and the cooldown window has not elapsed.
# The counterfactual tracker shadows the blocked signal under this reason so the
# gate's cost is measurable via `r_values_by_reason`.
STOP_LOSS_COOLDOWN = "STOP_LOSS_COOLDOWN"


LEGACY_MAX_HOLD_FALLBACK = "LEGACY_POSITION_MAX_HOLD_FALLBACK"


# --- entry routing (pure) -----------------------------------------------------


# --- the portfolio's net directional bet --------------------------------------

# How far the whole book may lean one way, counted in positions.
#
# **Derived, not chosen**: it is `MAX_POSITIONS_PER_SYMBOL`, so the rule has an English
# statement that does not mention a number — *the portfolio's net directional bet may never
# exceed one symbol's full allocation.* However many symbols the book holds, the part of it
# that is not hedged by an opposing position is never larger than what a single symbol could
# have contributed alone.
#
# **It is not a concurrency cap in disguise**, and that is what makes the derived value the
# right one rather than merely a tidy one: at 20 slots a book of 12 long + 8 short is net 4,
# so the full portfolio limit stays reachable. What the cap forbids is a *full book that is
# one-way*, not a full book.
MAX_DIRECTIONAL_SKEW = MAX_POSITIONS_PER_SYMBOL


def directional_skew_admits(
    open_positions: Sequence[tuple[Any, Mapping[str, Any]]],
    direction: Any,
) -> tuple[bool, dict[str, Any] | None]:
    """May the book take on one more position in ``direction``? ``(admitted, refusal)``.

    **The limit is read from the module global at call time and is deliberately not a
    parameter**, which is how its two sibling caps in ``run_paper_update`` already work. A
    ``cap=MAX_DIRECTIONAL_SKEW`` default argument would bind the number at import, so the
    constant and the enforced value could disagree — which is not hypothetical: it is what the
    first version of this function did, and the end-to-end test caught it opening a position the
    cap was set to refuse. One limit, one way to express it.

    **The gap this closes.** `MAX_CONCURRENT_POSITIONS` and `MAX_POSITIONS_PER_SYMBOL` bound
    how *many* positions exist and how many share a symbol. Neither bounds how many point the
    same way, so twenty simultaneous longs were permitted — and that is the F1 hazard
    `docs/TRADING_STRATEGY_REVIEW_RECORD.md` names: when the market drops, every open long
    loses at once, and no existing check could say so.

    **Why it matters here more than for money.** This is the PAPER book, which risks nothing —
    its product is *strategy evidence*. An all-long book turns one market move into twenty
    correlated verdicts about twenty different strategies, and promotion/demotion then reads
    those as twenty independent judgements. The cap protects what paper is for. (Live is
    deliberately untouched: at `live_position.MAX_LIVE_CONCURRENT_POSITIONS = 2` the live book's
    largest possible skew is 2, so a cap there is either inert or halves an already-tiny
    capacity — and live one-way risk is governed by the operator-registered
    `max_open_notional_usdt`, an authority that already exists and is Thomas's number.)

    **The sign convention is load-bearing — do not "simplify" it back to an absolute value.**
    Everything is counted *toward the proposal*: ``aligned`` positions push the same way this
    entry would, ``opposing`` push against it, and the lean is ``aligned - opposing``. The rule
    is then one comparison, and the corrective-safe property falls out of it for free: a lean
    that would exceed the cap after this entry can only be reached from a book that *already*
    leans this way, so a corrective entry — one that reduces an imbalance — is never declined.
    Writing the same rule as ``abs(new_skew) > cap`` looks equivalent and is not: once
    unsynchronised exits have pushed the book past the cap on its own, that form refuses the
    very trades that would bring it back. Measured on a 1,200-bar five-symbol simulation, the
    absolute form blocked corrective entries while gaining nothing (identical opened count,
    identical worst-case skew).

    **A direction that is not provably opposite counts as aligned**, which needs no special
    case because ``opposing`` is what is measured. A corrupted record therefore makes the cap
    bind *sooner*, never later — the same direction `list_open_positions` fails in when it
    cannot attribute a position at all.

    **What this cannot do, stated because the alternative is worse.** It is an entry-time gate,
    so it bounds the skew the runtime deliberately *takes on*, not the skew the market leaves
    it holding: positions expire on their own schedules, so a balanced 12/8 book becomes 12/0
    when the shorts time out. Measured, the cap still cut the worst observed lean from 8 to 6
    and the book sat over the cap 2.3% of the time. Bounding it *thereafter* would mean closing
    positions the strategies did not close — a new authority over exits, and one that would
    falsify the outcome record this book exists to produce.

    **Which context gets a scarce directional slot is not decided here, and the answer is worth
    knowing before trusting this.** Like the two caps beside it, this is evaluated per context as
    the fan-out reaches it, so the room under the cap goes to whoever asks first — and until
    ``cycle.pool_cycle_contexts`` started ordering by urgency and evidence, "first" meant
    *alphabetically*, which would have made the last directional slot an accident of spelling
    (the tiebreak-by-alphabet hazard this codebase rejects in the cross-sectional rank). It is
    now: live-holding contexts, then paper-holding ones, then best ``champion_score``
    descending. So a binding cap spends its remaining room on the best-evidenced context rather
    than the alphabetically luckiest. Neither change owns that property on its own, so a test
    pins the composition.

    What it still does not promise — and the ordering change says so itself — is the *globally
    best* entry: a score is not a signal, so the top-scoring context may propose nothing this
    bar. Arbitrating properly means evaluating every context before executing any, which is a
    second pass over the whole fan-out and a decision of its own.

    One-directional: it can only ever decline. It never opens a position, never flips one, and
    never admits an entry the existing caps would have refused — which is why, like
    :func:`regime_admits`, it needs no gate of its own.
    """
    proposed = str(direction or "").upper()
    if proposed not in ("LONG", "SHORT"):
        # Nothing to count toward. The caller has no entry to judge, and inventing a lean for
        # an unknown direction would decline on a guess.
        return True, None
    opposite = "SHORT" if proposed == "LONG" else "LONG"
    opposing = sum(
        1 for _context, position in open_positions
        if str((position or {}).get("direction") or "").upper() == opposite
    )
    aligned = len(open_positions) - opposing
    lean_after = aligned + 1 - opposing
    if lean_after <= MAX_DIRECTIONAL_SKEW:
        return True, None
    return False, {
        "reason_code": POSITION_LIMIT_DIRECTIONAL,
        "direction": proposed,
        "aligned": aligned,
        "opposing": opposing,
        "lean_after": lean_after,
        "limit": MAX_DIRECTIONAL_SKEW,
    }


def _realized_evidence(
    match: Mapping[str, Any], realized_stats: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[int, float] | None:
    """``(closed_count, expectancy)`` for this match's LINEAGE, or None below the floor.

    ``realized_stats`` is keyed by lineage (`feedback.realized_by_lineage`, Thomas decision 35).
    The match reads every key an outcome of its entry may carry across three eras of
    record-keeping, and those of the entries it replaced (PR3c, Thomas decision 41)
    (`candidate_identity.entry_attribution_keys`), as the lifecycle does, and the groups it finds
    are one record: an outcome carries exactly one key, and the cycle hands one row per trade of a
    rule (`feedback.distinct_trades`), so they never overlap.
    Keyed by the display id, a lineage that reused another's id inherited its record, and one
    renamed lost its own.

    Groups combine from their unrounded ``total_r`` into one mean, rounded once as each group is:
    the tiers compare it exactly (``> 0``, a dead-even ``==``), and a mean of rounded group means
    could turn an exact break-even into a proven edge (review of PR3b-1). A group without a total
    (stats built by hand) counts as its count times its mean.

    None means "no adequate realized sample", which is a different fact from a measured
    zero — the ranking treats the two differently on purpose."""
    if not realized_stats:
        return None
    groups: list[tuple[int, float, float]] = []
    for key in sorted(entry_attribution_keys(match)):
        stats = realized_stats.get(key)
        if not isinstance(stats, Mapping):
            continue
        closed = stats.get("closed_count")
        expectancy = stats.get("expectancy")
        if not isinstance(closed, (int, float)) or isinstance(closed, bool) or closed <= 0:
            continue
        if not isinstance(expectancy, (int, float)) or isinstance(expectancy, bool):
            continue
        total = stats.get("total_r")
        if not isinstance(total, (int, float)) or isinstance(total, bool):
            total = closed * expectancy
        groups.append((int(closed), float(expectancy), float(total)))
    closed_total = sum(group[0] for group in groups)
    if closed_total < MIN_PRIORITY_SAMPLE_TRADES:
        return None
    if len(groups) == 1:
        return groups[0][0], groups[0][1]
    return closed_total, round(sum(group[2] for group in groups) / closed_total, 8)


def _rank_matches(
    matches: list[dict[str, Any]], realized_stats: Mapping[str, Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Same-direction matches, best first: proven edge, then hypothesis, then proven no-edge.

    Tier 0 — positive realized expectancy over an adequate sample, best first: the evidence
    ``champion_score`` was supposed to proxy, measured instead of fitted (the score was
    ANTI-correlated with realized R on this machine, which is what made score-ranked slot
    sharing strictly negative under the flat cap). Tier 1 — no adequate realized sample:
    ``champion_score`` descending, the pre-evidence key, unchanged from the flat-cap router.
    Tier 2 — adequate sample, non-positive: a lineage that has PROVEN it has no edge here
    ranks below an untested hypothesis on purpose. The lineage key breaks every tie, then the
    display id, so the order never depends on store order and a rename does not reorder it, unless
    the entry's only key is its display id (decision 35). ``cand:`` sorts before ``gen:`` before
    ``sid:``, so on an exact tie a minted lineage routes before an imported one."""
    def key(m: dict[str, Any]) -> tuple[int, float, str, str]:
        evidence = _realized_evidence(m, realized_stats)
        champion = m["champion_score"] if m["champion_score"] is not None else -math.inf
        tie = (outcome_attribution_key(m), m["strategy_id"] or "")
        if evidence is not None and evidence[1] > 0:
            return (0, -evidence[1], *tie)
        if evidence is None:
            return (1, -champion, *tie)
        return (2, -evidence[1], *tie)
    return sorted(matches, key=key)


def _supporting_detail(match: Mapping[str, Any], shadow_reason: str) -> dict[str, Any]:
    """The identity a declined signal carries into the shadow book."""
    return {
        "strategy_id": match["strategy_id"],
        "candidate_id": match["candidate_id"],
        "strategy_rule_hash": match["strategy_rule_hash"],
        "strategy_generation_id": match["strategy_generation_id"],
        ARTIFACT_SHA256_FIELD: match.get(ARTIFACT_SHA256_FIELD),
        "direction": match["direction"],
        "champion_score": match["champion_score"],
        "spec": match["spec"],
        "shadow_reason": shadow_reason,
    }


def _resolve_direction_conflict(
    matches: list[dict[str, Any]], realized_stats: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]] | None:
    """The winning side of a two-direction bar, or None to fail closed as before.

    A side is BACKED when a member holds a positive realized expectancy over an adequate
    sample (``_realized_evidence``). Exactly one backed side wins outright; two backed
    sides go to the better best-expectancy, and a dead-even tie stays closed; no backed
    side keeps the flat-cap behaviour — fail closed — because there is no measured basis
    to pick a direction, and the unresolved shadows are what will create one.

    Returns ``(winning_matches, losing_matches, basis)`` or None."""
    by_direction: dict[str, list[dict[str, Any]]] = {}
    for m in matches:
        by_direction.setdefault(str(m["direction"]), []).append(m)
    backed: dict[str, tuple[dict[str, Any], float, int]] = {}
    for direction, side in by_direction.items():
        best: tuple[dict[str, Any], float, int] | None = None
        for m in side:
            evidence = _realized_evidence(m, realized_stats)
            if evidence is None or evidence[1] <= 0:
                continue
            if best is None or evidence[1] > best[1]:
                best = (m, evidence[1], evidence[0])
        if best is not None:
            backed[direction] = best
    if not backed:
        return None
    ranked = sorted(backed.items(), key=lambda kv: (-kv[1][1], kv[0]))
    if len(ranked) > 1 and ranked[0][1][1] == ranked[1][1][1]:
        return None  # dead even: no measured basis to pick a side
    winner = ranked[0][0]
    winning = by_direction.pop(winner)
    losing = [m for side in by_direction.values() for m in side]
    best_match, best_expectancy, best_closed = ranked[0][1]
    basis = {
        "strategy_id": best_match["strategy_id"],
        "lineage": outcome_attribution_key(best_match),
        "expectancy": best_expectancy,
        "closed_count": best_closed,
    }
    return winning, losing, basis


def route_entries(
    pool: Mapping[str, Any], feature_row: Mapping[str, Any], *, symbol: str, timeframe: str,
    now: str, realized_stats: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate every routable pool strategy against this cycle's feature row.

    A spec is evaluable here when this cycle's ``symbol`` is in its ``symbol_scope``
    and its timeframe matches — a strategy scoped to several symbols is judged on
    each of them (on that symbol's own cycle), not only its primary one. A spec
    scoped elsewhere is recorded as unevaluable and cannot match — an ETH daily
    strategy is never judged on a BTC hourly row (the source's ``feature_rows`` rule,
    single-snapshot form). The router only proposes.

    ``realized_stats`` (lineage key -> ``{"closed_count", "expectancy"}``,
    `feedback.realized_by_lineage` over this runtime's own paper rows plus the supporting-shadow
    settlements) is what lets a shared fast
    context be ranked on measured evidence: with it, a same-direction tie goes to the
    proven edge before ``champion_score`` (``_rank_matches``) and a direction conflict can
    resolve toward a backed side instead of always failing closed
    (``_resolve_direction_conflict``). Without it — every caller under the flat cap, and
    every cycle whose stats read degraded — behaviour is exactly as before.
    """
    entries = [
        e for e in (pool.get("active_strategies") or [])
        if e.get("status") in OCCUPYING_STATUSES and e.get("strategy_spec")
    ]
    evaluations: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []

    for entry in entries:
        spec = StrategySpec.from_dict(entry["strategy_spec"])
        if symbol not in spec.symbol_scope or spec.timeframe != timeframe or not feature_row:
            evaluations.append({
                "strategy_id": entry.get("strategy_id"),
                "matched": False,
                "direction": None,
                "unevaluable": f"no feature row for {'/'.join(spec.symbol_scope)} {spec.timeframe}",
            })
            continue
        result = evaluate_spec(spec, feature_row)
        # The regime filter runs on a MATCH, not before evaluation, so the record still shows
        # that the rules fired and says separately that the regime declined it. Filtering
        # earlier would make an excluded strategy indistinguishable from one whose conditions
        # simply were not met, and those are different things for an operator to read.
        admitted, regime_reason = (
            regime_admits(entry, feature_row.get("market_regime")) if result.matched else (True, None)
        )
        di_admitted, di_reason, di_score = (
            distribution_admits(entry, feature_row) if result.matched and admitted else (True, None, None)
        )
        evaluation = {
            "strategy_id": entry.get("strategy_id"),
            "matched": result.matched,
            "direction": result.direction,
        }
        if regime_reason is not None:
            evaluation["regime_excluded"] = regime_reason
            evaluation["regime"] = feature_row.get("market_regime")
        if di_reason is not None:
            evaluation["distribution_excluded"] = di_reason
            evaluation["dissimilarity_index"] = di_score
        evaluations.append(evaluation)
        if result.matched and admitted and di_admitted:
            matches.append({
                "strategy_id": entry.get("strategy_id"),
                "candidate_id": entry.get("candidate_id"),
                "strategy_rule_hash": entry.get("strategy_rule_hash"),
                "strategy_generation_id": entry.get("generation_id") or entry.get("strategy_spec", {}).get("generation_id"),
                # The artifact the entry was installed as (PR3a); None for an entry that predates
                # it. It rides beside the three lineage fields to every record the signal writes —
                # plan, position, outcome, shadow, forward, the live order and its snapshot — so a
                # result names the strategy that produced it whole, not only its rule (PR3a-2).
                ARTIFACT_SHA256_FIELD: entry.get(ARTIFACT_SHA256_FIELD),
                # The lineages this entry replaced (PR3c, Thomas decision 41): the realized record
                # it is ranked on is theirs too. Read by `_realized_evidence` alone; no record
                # carries it — an outcome names its own lineage.
                PREDECESSOR_KEYS_FIELD: entry.get(PREDECESSOR_KEYS_FIELD),
                "direction": result.direction,
                "champion_score": entry.get("champion_score"),
                "spec": spec,
            })

    base = {
        "strategies_evaluated": len(entries),
        "evaluations": evaluations,
        "matched_strategy_ids": sorted(m["strategy_id"] for m in matches if m["strategy_id"] is not None),
        # Named separately from `matched_strategy_ids` because "the rules fired and the regime
        # declined it" is the one outcome an operator would otherwise have to infer from an
        # absence. A route that entered nothing because every match was regime-excluded reads
        # identically to one where nothing matched, and those want different responses.
        "regime_excluded_strategy_ids": sorted(
            str(e["strategy_id"]) for e in evaluations
            if e.get("regime_excluded") and e.get("strategy_id") is not None
        ),
        "distribution_excluded_strategy_ids": sorted(
            str(e["strategy_id"]) for e in evaluations
            if e.get("distribution_excluded") and e.get("strategy_id") is not None
        ),
        "regime": feature_row.get("market_regime") if feature_row else None,
        # The traded context, so a plan books under the symbol this cycle actually
        # ran — not the spec's primary symbol_scope[0], which for a multi-symbol
        # strategy on a non-primary symbol would mis-book the position.
        "symbol": symbol,
        "timeframe": timeframe,
        "created_at_utc": now,
        "direction": None,
    }
    if not matches:
        return {**base, "status": STATUS_NO_ENTRY}

    directions = {m["direction"] for m in matches}
    conflict_note: dict[str, Any] | None = None
    losing: list[dict[str, Any]] = []
    if len(directions) > 1:
        resolution = _resolve_direction_conflict(matches, realized_stats)
        if resolution is None:
            # Single-symbol cycle, no side backed by measured evidence (or dead even):
            # fail closed exactly as the flat-cap router did — but hand BOTH sides to the
            # shadow book, because an unresolved conflict must be able to END: the shadows
            # are the evidence a later bar's resolution will read. Without them two fresh
            # strategies that disagree would stay blocked forever.
            return {
                **base,
                "status": STATUS_BLOCKED,
                "block_reason": BLOCK_DIRECTION_CONFLICT,
                "conflicting_directions": sorted(d for d in directions if d),
                "conflict_matches": [
                    _supporting_detail(m, DIRECTION_CONFLICT_UNRESOLVED) for m in matches
                ],
            }
        matches, losing, basis = resolution
        conflict_note = {
            "resolved_direction": str(matches[0]["direction"]),
            "basis": basis,
            "losing_strategy_ids": sorted(str(m["strategy_id"]) for m in losing),
        }

    ranked = _rank_matches(matches, realized_stats)
    primary = ranked[0]
    primary_evidence = _realized_evidence(primary, realized_stats)
    result = {
        **base,
        "status": STATUS_ENTRY_CANDIDATE,
        "direction": primary["direction"],
        "primary_strategy_id": primary["strategy_id"],
        "primary_candidate_id": primary["candidate_id"],
        "primary_strategy_rule_hash": primary["strategy_rule_hash"],
        "primary_strategy_generation_id": primary["strategy_generation_id"],
        "primary_strategy_artifact_sha256": primary.get(ARTIFACT_SHA256_FIELD),
        "primary_spec": primary["spec"],
        # Which key actually picked the primary, so a reader of the route can tell a
        # measured decision from the pre-evidence fallback without re-deriving it.
        "primary_priority_basis": (
            "realized_expectancy"
            if primary_evidence is not None and primary_evidence[1] > 0
            else "champion_score"
        ),
        "supporting_strategy_ids": [m["strategy_id"] for m in ranked[1:]],
        # Full identity for the shadow book: the bench behind the primary, plus a resolved
        # conflict's losing side. `supporting_strategy_ids` above keeps its original
        # same-direction meaning for the plan record.
        "supporting": [_supporting_detail(m, ROUTED_BEHIND_PRIMARY) for m in ranked[1:]]
        + [_supporting_detail(m, DIRECTION_CONFLICT_LOST) for m in losing],
    }
    if conflict_note is not None:
        result["direction_conflict"] = conflict_note
    return result


# --- settlement (pure; source math verbatim) ----------------------------------


def intrabar_ambiguous(position: Mapping[str, Any], candle: Mapping[str, Any] | None) -> bool:
    """True when this candle touches BOTH the stop and the target.

    The bar says both happened and cannot say which came first, so the settlement
    has to either assume (pessimistic SL-first) or look at finer bars. Callers use
    this to decide whether a finer collection is worth making: when only one side is
    touched there is nothing to resolve, so the common case costs no extra request.
    """
    if candle is None or float(position.get("risk") or 0.0) <= 0:
        return False
    hit_stop, hit_target = _touches(str(position["direction"]), candle, float(position["stop_loss"]),
                                    float(position["take_profit"]))
    return hit_stop and hit_target


def collect_intrabar_candles(
    collector: Any,
    position: Mapping[str, Any],
    candle: Mapping[str, Any],
    *,
    timeframe: str,
) -> list[dict[str, Any]]:
    """Fine bars covering ``candle``'s span — the evidence for which level hit first.

    The window is the ONE bar being settled, not the whole holding period: only that
    bar's ordering is in question. Because a collection returns the most recent bars,
    the request covers two bar-spans (the settling bar may have closed anywhere
    between "just now" and one span ago) and the result is filtered to the bar's own
    [open_time, close_time). Raises on a collector failure — the caller degrades.
    """
    from .market_data import TIMEFRAMES, collect_market_data

    minutes = TIMEFRAMES.get(str(timeframe))
    fine_minutes = TIMEFRAMES[INTRABAR_TIMEFRAME]
    if not minutes or minutes <= fine_minutes:
        return []  # already at the finest resolution — nothing further to observe
    span = minutes // fine_minutes
    snapshot, _ = collect_market_data(
        str(position.get("symbol") or ""),
        INTRABAR_TIMEFRAME,
        collector=collector,
        now=timeutil.utc_now_iso(),
        limit=2 * span + 2,
    )
    start = str(candle.get("open_time") or "")
    end = str(candle.get("close_time") or "")
    return [c for c in snapshot["candles"] if start <= str(c.get("open_time") or "") < end]


# --- state: ungated reads, gated writes ---------------------------------------
# (`state_dir` lives in `state.py` now — one root for both trading planes.)


# --- position contexts: one slot per (venue, symbol, timeframe) ----------------

# Each part becomes a path segment, so it is pattern-checked like a provider id.
# Symbols are upper-case (BTCUSDT), timeframes lower (1d) — both admitted.
_CONTEXT_PART_PATTERN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,31}\Z")
_CONTEXT_SEP = "__"


@dataclass(frozen=True)
class PositionContext:
    """The book one position belongs to. Its identity IS its storage location.

    Before this existed the runtime had a single global position file, so a cycle
    for one symbol could find a position opened by another; the cross-context guard
    (PR #109) had to refuse those, which also meant an occupied slot blocked every
    other symbol from ever opening. Keying the slot by context makes that structural:
    a cycle only ever sees its own book."""

    venue: str
    symbol: str
    timeframe: str

    def __post_init__(self) -> None:
        for field_name, value in (("venue", self.venue), ("symbol", self.symbol),
                                  ("timeframe", self.timeframe)):
            if not (isinstance(value, str) and _CONTEXT_PART_PATTERN.match(value)):
                raise ToolError(
                    POSITION_CONTEXT_MISMATCH,
                    f"position context {field_name}={value!r} is not a valid book name",
                )
            if value.split(".", 1)[0].upper() in RESERVED_BASENAMES:
                raise ToolError(
                    POSITION_CONTEXT_MISMATCH,
                    f"position context {field_name}={value!r} is a reserved device name on Windows",
                )

    @property
    def key(self) -> str:
        return _CONTEXT_SEP.join((self.venue, self.symbol, self.timeframe))

    @staticmethod
    def from_snapshot(snapshot: Mapping[str, Any]) -> "PositionContext":
        return PositionContext(
            venue=str(snapshot.get("venue") or DEFAULT_VENUE),
            symbol=str(snapshot.get("symbol") or ""),
            timeframe=str(snapshot.get("timeframe") or ""),
        )

    @staticmethod
    def from_position(position: Mapping[str, Any]) -> "PositionContext":
        """The book a stored position belongs to, or fail closed.

        ``venue`` defaults for positions written before venues existed; ``symbol``
        and ``timeframe`` are never guessed — a position that cannot say what it
        trades cannot be attributed to any book (the PR #109 rule)."""
        return PositionContext(
            venue=str(position.get("venue") or DEFAULT_VENUE),
            symbol=str(position.get("symbol") or ""),
            timeframe=str(position.get("timeframe") or ""),
        )


def positions_dir(root: Path | None = None) -> Path:
    return state_dir(root) / POSITIONS_DIRNAME


def position_path(context: PositionContext, root: Path | None = None) -> Path:
    """Where this context's position lives — validated and containment-checked.

    The context parts are caller-influenced path segments (a schedule's request
    line reaches here), so the resolved path must stay inside the positions
    directory: a context can name a book, never a location."""
    base = positions_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    resolved_base = base.resolve()
    path = (resolved_base / f"{context.key}.json").resolve()
    if path.parent != resolved_base:
        raise ToolError(
            POSITION_CONTEXT_MISMATCH, f"position context {context.key!r} resolves outside the positions directory"
        )
    return path


def _read_position_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # An unreadable position file cannot honestly mean "no position": refuse the
        # cycle's paper step rather than double-opening over a live position.
        raise ToolError("POSITION_STATE_UNREADABLE", f"paper position file unreadable: {type(exc).__name__}") from exc
    if isinstance(data, dict) and data.get("status") == "OPEN":
        return data
    return None


def legacy_position_path(root: Path | None = None) -> Path:
    return state_dir(root) / POSITION_FILENAME


def load_open_position(context: PositionContext, root: Path | None = None) -> dict[str, Any] | None:
    """This context's OPEN position, or None. ALLOW-tier read of private state.

    The legacy single-slot file is still honoured for reads so a position opened
    before context keying can still be settled — but only by its own context, and
    only if it says what that context is. An OPEN legacy position without symbol or
    timeframe is unattributable; :func:`list_open_positions` refuses on it so it can
    neither be settled on a guess nor silently excluded from the exposure count."""
    stored = _read_position_file(position_path(context, root))
    if stored is not None:
        if PositionContext.from_position(stored) != context:
            raise ToolError(
                POSITION_CONTEXT_MISMATCH,
                f"book {context.key} holds a position for a different context",
            )
        return stored
    legacy = _read_position_file(legacy_position_path(root))
    if legacy is not None and str(legacy.get("symbol") or "") and str(legacy.get("timeframe") or ""):
        if PositionContext.from_position(legacy) == context:
            return legacy
    return None


def unattributable_legacy_position(root: Path | None = None) -> dict[str, Any] | None:
    """An OPEN legacy position that cannot say which book it belongs to, or None.

    Its risk is real but unplaceable: it can neither be settled (no cycle can prove
    the candles are its own — the PR #109 rule) nor counted toward the concurrency
    caps. Callers treat it as a hard stop rather than trading around a position they
    cannot measure."""
    legacy = _read_position_file(legacy_position_path(root))
    if legacy is None:
        return None
    if str(legacy.get("symbol") or "") and str(legacy.get("timeframe") or ""):
        return None
    return legacy


def list_open_positions(root: Path | None = None) -> list[tuple[PositionContext, dict[str, Any]]]:
    """Every open position across every book — the exposure the caps are counted on.

    Fails closed on an unattributable legacy position: counting exposure without one
    would understate what is at stake."""
    found: list[tuple[PositionContext, dict[str, Any]]] = []
    blocker = unattributable_legacy_position(root)
    if blocker is not None:
        raise ToolError(
            POSITION_CONTEXT_MISMATCH,
            f"legacy position {blocker.get('position_id')!r} is OPEN but names no symbol/timeframe",
        )
    legacy = _read_position_file(legacy_position_path(root))
    if legacy is not None:
        found.append((PositionContext.from_position(legacy), legacy))
    directory = positions_dir(root)
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            position = _read_position_file(path)
            if position is None:
                continue
            context = PositionContext.from_position(position)
            if any(context == seen for seen, _ in found):
                continue  # the legacy file is the same book, already counted
            found.append((context, position))
    return found


def read_outcomes(root: Path | None = None) -> list[dict[str, Any]]:
    """All persisted outcomes, oldest first — a VERIFIED read. Missing store =
    honestly empty; any unreadable, tampered, or duplicated record raises so the
    caller fails the risk guard closed (``guards.risk_guard_unreadable``) — the
    guard and feedback never learn from a history that cannot prove itself.

    Verification per record: a native record (provenance ``mvp_paper_kernel``, whose
    self-hash :func:`build_outcome_record` wrote) must recompute its
    ``record_sha256`` exactly; ``outcome_id`` and ``settlement_id`` must be unique
    across the whole store (a settlement_id duplicate is the double-settlement
    signature). Imported records carry the source's own hash over pre-import fields,
    so their tamper evidence is the audited import batch, not a per-record recompute."""
    path = state_dir(root) / OUTCOMES_FILENAME
    outcomes: list[dict[str, Any]] = []
    seen_outcome_ids: set[str] = set()
    seen_settlement_ids: set[str] = set()
    # Reads only. The append below keeps its own fsync, which append_lines does not do.
    for lineno, record in jsonl.iter_numbered(
        path,
        read_code="OUTCOME_HISTORY_UNREADABLE",
        label="paper outcomes",
        exc_type=ToolError,
    ):
        if not isinstance(record, dict):
            continue
        if record.get("provenance") == PAPER_PROVENANCE:
            stored = record.get("record_sha256")
            body = {k: v for k, v in record.items() if k != "record_sha256"}
            if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
                raise ToolError(
                    "OUTCOME_HISTORY_TAMPERED", f"paper outcomes line {lineno} fails its self-hash"
                )
        outcome_id = record.get("outcome_id")
        if isinstance(outcome_id, str) and outcome_id:
            if outcome_id in seen_outcome_ids:
                raise ToolError("OUTCOME_HISTORY_DUPLICATE", f"duplicate outcome_id: {outcome_id}")
            seen_outcome_ids.add(outcome_id)
        settlement_id = record.get("settlement_id")
        if isinstance(settlement_id, str) and settlement_id:
            if settlement_id in seen_settlement_ids:
                raise ToolError(
                    "OUTCOME_HISTORY_DUPLICATE", f"duplicate settlement_id: {settlement_id}"
                )
            seen_settlement_ids.add(settlement_id)
        outcomes.append(record)
    return outcomes


def split_by_provenance(
    outcomes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(own, imported)`` — this runtime's own paper outcomes vs the imported history.

    The store deliberately holds both: ``import_crypto_history.py`` carried the frozen
    crypto_AI_System's closed outcomes in so the risk guard and feedback would have history
    from day one rather than a cold start. They are real trades, but produced by **different
    code**, so any answer to "how is THIS runtime performing" has to separate them — blended,
    the imported set dominates by count and the runtime's own record disappears inside it.

    Deliberately a filter the *caller* applies rather than a change to ``read_outcomes``:
    idempotency and settlement de-duplication must keep seeing every record, and whether the
    risk guard should count imported history is a governance decision, not a reporting one."""
    own = [r for r in outcomes if isinstance(r, dict) and r.get("provenance") == PAPER_PROVENANCE]
    imported = [r for r in outcomes if isinstance(r, dict) and r.get("provenance") != PAPER_PROVENANCE]
    return own, imported


def already_settled(position_id: str, root: Path | None = None) -> bool:
    """Whether an outcome for this position is already durably recorded.

    The settlement dup check: a crash between outcome-append and position-clear
    leaves an OPEN position whose outcome exists — re-settling it would double the
    trade in the history the risk guard and feedback read. Raises (via
    :func:`read_outcomes`) when the history is unreadable: unverifiable is never
    treated as not-settled."""
    return any(o.get("position_id") == position_id for o in read_outcomes(root))


class PaperStore(Protocol):
    tool_id: str
    tool_version: str

    def save_position(self, position: Mapping[str, Any]) -> None: ...
    def clear_position(self, context: PositionContext) -> None: ...
    def append_outcome(self, record: Mapping[str, Any]) -> None: ...
    def settle_position(self, outcome: Mapping[str, Any]) -> None: ...


class DryRunPaperStore:
    """Default store: accepts every mutation and persists nothing.

    The R8 ``DryRunWriter`` analog — the full paper path (routing, plan, settlement,
    outcome, audit) runs on the default path without the runtime gaining durable
    paper state. ``filesystem_write=False`` rides into every record."""

    tool_id = PAPER_TOOL_ID
    tool_version = f"{PAPER_TOOL_VERSION}-dryrun"
    filesystem_write = False

    def save_position(self, position: Mapping[str, Any]) -> None:
        return None

    def clear_position(self, context: PositionContext) -> None:
        return None

    def append_outcome(self, record: Mapping[str, Any]) -> None:
        return None

    def settle_position(self, outcome: Mapping[str, Any]) -> None:
        return None


class RealPaperStore:
    """Durable paper state under ``.runtime_governance_state/crypto/``.

    Constructed only behind the Safety-Flag Gate (``paper_trading`` provider on the
    ``filesystem_write`` flag); re-asserts its authorization at every mutating call so
    a directly-constructed store cannot bypass the gate. File-locked like the ledger:
    the CLI and the scheduler may both settle in principle, and a lost update here is
    a lost trade outcome."""

    tool_id = PAPER_TOOL_ID
    tool_version = PAPER_TOOL_VERSION
    provider_id = PAPER_PROVIDER_ID
    filesystem_write = True

    def __init__(self, *, root: Path | None = None, authorization: Authorization | None = None):
        self._root = root
        self._authorization = authorization

    def _dir(self) -> Path:
        target = state_dir(self._root)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _assert(self) -> None:
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_WRITE_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )

    def save_position(self, position: Mapping[str, Any]) -> None:
        self._assert()
        self._dir()
        context = PositionContext.from_position(position)
        path = position_path(context, self._root)
        with locked(path.with_suffix(".lock"), code="PAPER_STATE_LOCKED", label="paper position"):
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(dict(position), ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)

    def clear_position(self, context: PositionContext) -> None:
        """Close this context's book — and the legacy file when it held that book.

        A position opened before context keying lives in the legacy single-slot
        file; settling it must close it *there*, or the next cycle would read the
        same OPEN position back and settle it twice."""
        self._assert()
        self._dir()
        closed = json.dumps({"status": "CLOSED"})
        path = position_path(context, self._root)
        with locked(path.with_suffix(".lock"), code="PAPER_STATE_LOCKED", label="paper position"):
            tmp = path.with_suffix(".tmp")
            tmp.write_text(closed, encoding="utf-8")
            tmp.replace(path)
        legacy = legacy_position_path(self._root)
        stale = _read_position_file(legacy)
        try:
            stale_context = PositionContext.from_position(stale) if stale is not None else None
        except ToolError:
            stale_context = None  # unattributable: not this book's to close
        if stale is not None and stale_context == context:
            with locked(legacy.with_suffix(".lock"), code="PAPER_STATE_LOCKED", label="paper position"):
                tmp = legacy.with_suffix(".tmp")
                tmp.write_text(closed, encoding="utf-8")
                tmp.replace(legacy)

    def append_outcome(self, record: Mapping[str, Any]) -> None:
        self._assert()
        path = self._dir() / OUTCOMES_FILENAME
        with locked(path.with_suffix(".lock"), code="PAPER_STATE_LOCKED", label="paper outcomes"):
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(dict(record), ensure_ascii=False) + "\n")
                # A trade outcome is the one record the risk guard and feedback learn
                # from; leaving it in an OS buffer means a power loss can drop a trade
                # that the position file already says is closed.
                handle.flush()
                os.fsync(handle.fileno())

    def settle_position(self, outcome: Mapping[str, Any]) -> None:
        """Append the outcome and clear the position as ONE serialized, revalidated step.

        The chokepoint's ``already_settled`` check necessarily runs BEFORE the lock, so
        by the time we hold it another settler (a manual ``docker exec`` cycle racing the
        scheduler) may have finished the same position — both would have passed that
        check. Everything is therefore re-derived here, under the lock:

        - the position must still be OPEN and still be the one this outcome was computed
          for (``SETTLEMENT_RACE_LOST`` otherwise: the loser writes nothing);
        - the outcome is appended only if its ``settlement_id`` is not already recorded,
          so finishing an interrupted settlement completes it instead of doubling it.

        The clear runs in both surviving cases — a settlement whose outcome already
        exists is precisely the crash window this closes. Lock order is position →
        outcomes, the only nesting in this module."""
        self._assert()
        self._dir()
        context = PositionContext.from_position(outcome)
        path = position_path(context, self._root)
        with locked(path.with_suffix(".lock"), code="PAPER_STATE_LOCKED", label="paper position"):
            expected_id = outcome.get("position_id")
            current = load_open_position(context, self._root)
            if current is None or current.get("position_id") != expected_id:
                raise ToolError(
                    SETTLEMENT_RACE_LOST,
                    f"position {expected_id} is no longer the open position; another settler won",
                )
            settlement_id = outcome.get("settlement_id")
            recorded = any(o.get("settlement_id") == settlement_id for o in read_outcomes(self._root))
            if not recorded:
                self.append_outcome(outcome)
            closed = json.dumps({"status": "CLOSED"})
            tmp = path.with_suffix(".tmp")
            tmp.write_text(closed, encoding="utf-8")
            tmp.replace(path)
            # The position may still live in the legacy single-slot file; leaving it
            # OPEN there would hand the next cycle the same position to settle again.
            legacy = legacy_position_path(self._root)
            stale = _read_position_file(legacy)
            if stale is not None and stale.get("position_id") == expected_id:
                tmp = legacy.with_suffix(".tmp")
                tmp.write_text(closed, encoding="utf-8")
                tmp.replace(legacy)


def select_paper_store(*, now: str | None = None, root: Path | None = None) -> PaperStore:
    """Choose the paper store — the enforced Safety-Flag Gate chokepoint.

    Defaults to :class:`DryRunPaperStore`. The durable store is returned ONLY behind
    the caller's opt-in ``MVP_PAPER_TRADING=real`` — the environment is the gate
    (Thomas 2026-08-10); anything else selects the dry-run store, never a write path."""
    del now  # the environment is the gate (Thomas 2026-08-10); root still feeds the store
    return safety_gate.select_env_gated(
        env_var=PAPER_ENV,
        opt_in_value=REAL_PAPER,
        flags=_WRITE_FLAGS,
        provider_id=PAPER_PROVIDER_ID,
        default_factory=DryRunPaperStore,
        gated_factory=lambda authorization: RealPaperStore(root=root, authorization=authorization),
    )


# --- the cycle chokepoint -----------------------------------------------------

def run_paper_update(
    snapshot: Mapping[str, Any],
    feature_row: Mapping[str, Any],
    pool: Mapping[str, Any],
    verdict: Mapping[str, Any],
    *,
    store: PaperStore,
    now: str,
    root: Path | None = None,
    control_store: ControlStore | None = None,
    manual_exit: bool = False,
    intrabar_collector: Any | None = None,
    routing_marks: Any | None = None,
    cooldown_marks: Any | None = None,
    realized_stats: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One cycle's paper step: settle the open position, then maybe open one.

    Returns ``(summary, records)`` — records are the audit-ready events (settle /
    open), each carrying the store's ``filesystem_write`` capability flag. Fails
    closed (``ToolBlocked``, mode-aware reason) when the runtime is PAUSED/KILLED
    (``kill_blocks: tool_write``); a no-trade verdict skips the open, never the
    settlement — an already-open position must always be able to close.

    Positions are keyed by ``(venue, symbol, timeframe)``, so this cycle reads and
    writes only its own book: another symbol's open position is invisible here
    rather than refused, and it no longer blocks this book from opening. What still
    refuses (``POSITION_CONTEXT_MISMATCH``) is state that cannot be attributed —
    a legacy position naming no symbol/timeframe, or a book holding a position
    belonging to a different context.

    Opening is additionally capped: ``MAX_POSITIONS_PER_SYMBOL`` per symbol and
    ``MAX_CONCURRENT_POSITIONS`` across every book, counted under a portfolio lock
    so a manual cycle racing the scheduler cannot slip past the ceiling. Settlement
    is idempotent: a position whose outcome is already durable (a crash between
    append and clear) is recovered — cleared without a second outcome
    (``SETTLEMENT_ALREADY_RECORDED``) — and an unreadable history refuses the
    settlement (``SETTLEMENT_UNVERIFIABLE``).
    """
    control = control_store if control_store is not None else ControlStore(root or _repo_root())
    state = control.load()
    if not state.execution_allowed:
        raise ToolBlocked(
            state.refusal_reason_code(),
            f"runtime is {state.mode}; kill_blocks tool_write forbids the paper update",
        )

    candles = snapshot.get("candles") or []
    last_candle = candles[-1] if candles else None
    last_close = last_candle.get("close") if isinstance(last_candle, Mapping) else None
    timeframe = str(snapshot.get("timeframe") or "")
    symbol = str(snapshot.get("symbol") or "")
    context = PositionContext.from_snapshot(snapshot)

    records: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "settled": None, "opened": None, "route_status": None,
        "settle_refused": None, "settle_recovered": None, "open_refused": None,
        "open_skipped": None,
        # The routing result itself, not just its status. Routing is evaluated once per cycle
        # and read by three consumers — this step, the counterfactual shadow, and (LP5.3) the
        # live leg — and three evaluations of the same strategies against the same feature row
        # is three chances to disagree about what the pool said. Deliberately NOT copied into
        # the cycle record: it carries live `StrategySpec` objects, and the record is JSON.
        "route": None,
    }

    def _event(operation: str, detail: dict[str, Any]) -> dict[str, Any]:
        return {
            "tool_id": store.tool_id,
            "tool_version": store.tool_version,
            "tool_class": PAPER_TOOL_CLASS,
            "operation": operation,
            "read_only": False,
            "external_action": False,  # paper only — no exchange is ever touched
            "reversible": True,
            "filesystem_write": bool(getattr(store, "filesystem_write", False)),
            "created_at": now,
            **detail,
        }

    # 1) Settle. Runs regardless of the verdict: closing is risk-reducing — but only
    #    in the position's own book. Since positions are keyed by context, a cycle
    #    simply cannot reach another context's position: the load is scoped, so a
    #    foreign position is invisible here rather than refused. What still refuses
    #    is state that cannot be attributed at all — a legacy position naming no
    #    symbol/timeframe, or a book holding someone else's position.
    position: dict[str, Any] | None = None
    attribution_blocked = False
    orphan = unattributable_legacy_position(root)
    if orphan is not None:
        attribution_blocked = True
        refusal = {
            "reason_code": POSITION_CONTEXT_MISMATCH,
            "position_id": orphan.get("position_id"),
            "position_symbol": orphan.get("symbol"),
            "position_timeframe": orphan.get("timeframe"),
            "snapshot_symbol": symbol,
            "snapshot_timeframe": timeframe,
        }
        summary["settle_refused"] = refusal
        records.append(_event("settle_refused", {**refusal, "read_only": True}))
    else:
        try:
            position = load_open_position(context, root)
        except ToolError as exc:
            if exc.reason_code != POSITION_CONTEXT_MISMATCH:
                raise
            attribution_blocked = True  # a book holding another context's position
            refusal = {
                "reason_code": POSITION_CONTEXT_MISMATCH,
                "detail": str(exc),
                "snapshot_symbol": symbol,
                "snapshot_timeframe": timeframe,
            }
            summary["settle_refused"] = refusal
            records.append(_event("settle_refused", {**refusal, "read_only": True}))
    if position is not None:
        position_id = position.get("position_id")
        # Idempotency first: a crash between outcome-append and position-clear left
        # this position OPEN with its outcome already durable. Finish the
        # interrupted settlement (clear only, never a second outcome) before any
        # settlement math — a settled corpse must not advance holding or re-settle.
        # An unreadable history refuses instead: unverifiable is never not-settled.
        recovered = refused = False
        if isinstance(position_id, str) and position_id and getattr(store, "filesystem_write", False):
            try:
                recovered = already_settled(position_id, root)
            except ToolError as exc:
                refusal = {
                    "reason_code": SETTLEMENT_UNVERIFIABLE,
                    "cause_reason_code": exc.reason_code,
                    "position_id": position_id,
                }
                summary["settle_refused"] = refusal
                records.append(_event("settle_refused", {**refusal, "read_only": True}))
                refused = True
        if recovered:
            store.clear_position(context)
            recovery = {"reason_code": SETTLEMENT_ALREADY_RECORDED, "position_id": position_id}
            summary["settle_recovered"] = recovery
            records.append(_event("settle_recovered", recovery))
            position = None  # the slot is honestly free again
        elif not refused:
            max_hold, legacy_fallback = position_max_hold(position, timeframe)
            # Only an ambiguous bar is worth a second collection, and only if the
            # caller supplied a collector. A failure here DEGRADES to the assumption
            # (the MARKET_DATA_DEGRADED posture): a settlement must never be blocked
            # by the exchange being slow about a precision refinement.
            fine_candles = None
            if intrabar_collector is not None and intrabar_ambiguous(position, last_candle):
                try:
                    fine_candles = collect_intrabar_candles(
                        intrabar_collector, position, last_candle, timeframe=timeframe
                    )
                except (ToolError, ToolBlocked) as exc:
                    degraded = {
                        "reason_code": INTRABAR_RESOLUTION_DEGRADED,
                        "cause_reason_code": getattr(exc, "reason_code", "TOOL_ERROR"),
                        "position_id": position_id,
                    }
                    summary["intrabar_degraded"] = degraded
                    records.append(_event("intrabar_degraded", {**degraded, "read_only": True}))
            reason, exit_price, result_r = settle_trade_plan(
                position, last_candle, last_close, max_hold, manual_exit, fine_candles=fine_candles
            )
            if reason is not None:
                outcome = build_outcome_record(position, reason, exit_price, result_r, now=now)
                try:
                    store.settle_position(outcome)
                except ToolError as exc:
                    if exc.reason_code != SETTLEMENT_RACE_LOST:
                        raise
                    # A concurrent settler finished this position between our check and
                    # the lock. Its outcome stands; ours was never written. Report the
                    # loss rather than pretend this cycle settled anything — and open
                    # nothing: the winner is mid-cycle on the same book, and two writers
                    # racing to fill one position slot is exactly what this closes.
                    refusal = {"reason_code": SETTLEMENT_RACE_LOST, "position_id": position_id}
                    summary["settle_refused"] = refusal
                    records.append(_event("settle_refused", {**refusal, "read_only": True}))
                    return summary, records
                summary["settled"] = {
                    "position_id": position_id,
                    "close_reason": reason,
                    "result_R": outcome["result_R"],
                    "outcome_id": outcome["outcome_id"],
                }
                records.append(_event("settle", {
                    "position_id": position_id,
                    "close_reason": reason,
                    "result_R": outcome["result_R"],
                    "outcome_id": outcome["outcome_id"],
                    "settlement_id": outcome["settlement_id"],
                    "outcome_sha256": outcome["record_sha256"],
                    "max_hold": max_hold,
                    # Present only when the spec value was absent: the settlement ran on
                    # the legacy timeframe default, so a backtest/paper gap on this
                    # trade is attributable to the fallback, not to the strategy.
                    **({"max_hold_fallback": LEGACY_MAX_HOLD_FALLBACK} if legacy_fallback else {}),
                }))
                position = None
                # Record cooldown when the exit was a stop-loss, so this context
                # is blocked from re-entry for COOLDOWN_BARS_AFTER_STOPLOSS bars.
                if reason == "stop_loss" and cooldown_marks is not None and getattr(store, "filesystem_write", False):
                    candle_close = last_candle.get("close_time") if isinstance(last_candle, Mapping) else None
                    if candle_close is not None:
                        from .market_data import TIMEFRAMES
                        tf_minutes = TIMEFRAMES.get(timeframe, 60)
                        expiry = timeutil.plus_minutes(candle_close, COOLDOWN_BARS_AFTER_STOPLOSS * tf_minutes)
                        cooldown_marks.record_stoploss(context.key, expiry)
            else:
                store.save_position(position)  # persist advanced holding_candles

    # 2) Maybe open — only with this book free, an allowing verdict, and room under
    #    both concurrency caps. Counting and opening happen under ONE portfolio lock:
    #    the caps are a property of every book together, so two cycles that each
    #    counted before either wrote would both see room that only one of them had.
    route = route_entries(
        pool, feature_row, symbol=symbol, timeframe=timeframe, now=now,
        realized_stats=realized_stats,
    )
    summary["route_status"] = route["status"]
    summary["route"] = route
    if position is None and not attribution_blocked and bool(verdict.get("allow_new_position")):
        # Freshness gate (optional). Evaluate a NEW entry at most once per closed candle
        # per context, so one 15-min fan-out schedule does not re-enter a 4h/1d strategy
        # every tick — nor re-open on the very same candle right after a settle. The
        # settlement above is never gated. With no marks store injected (unit tests,
        # backtest) the gate is a no-op and behaviour is exactly as before.
        candle_time = last_candle.get("close_time") if isinstance(last_candle, Mapping) else None
        if routing_marks is not None and not routing_marks.is_fresh(context.key, candle_time):
            skip = {
                "reason_code": CANDLE_NOT_FRESH,
                "candle_time": candle_time,
                "last_routed": routing_marks.last(context.key),
            }
            summary["open_skipped"] = skip
            records.append(_event("open_skipped", {**skip, "read_only": True}))
        elif cooldown_marks is not None and cooldown_marks.is_cooling_down(context.key, candle_time):
            refusal = {
                "reason_code": STOP_LOSS_COOLDOWN,
                "candle_time": candle_time,
                "cooldown_expiry": cooldown_marks.expiry(context.key),
            }
            summary["open_refused"] = refusal
            records.append(_event("open_refused", {**refusal, "read_only": True}))
        else:
            plan = build_entry_plan(route, feature_row, now=now)
            # The signals the router declined THIS candle — the bench behind the primary, a
            # resolved conflict's losing side, or both sides of an unresolved one — become
            # shadow-book candidates for the cycle to hand to `counterfactual`. Built inside
            # the freshness gate on purpose: one shadow per closed candle, the same pacing
            # as the real entry, never one per tick. When a position is already open this
            # branch never runs and nothing is booked — the bench is measured only against
            # bars the context could actually have traded.
            declined = list(route.get("supporting") or []) \
                + list(route.get("conflict_matches") or [])
            supporting_plans: list[dict[str, Any]] = []
            for bench in declined:
                shadow = build_entry_plan({
                    "status": STATUS_ENTRY_CANDIDATE,
                    "direction": bench["direction"],
                    "symbol": route.get("symbol"),
                    "timeframe": route.get("timeframe"),
                    "primary_spec": bench["spec"],
                    "primary_strategy_id": bench["strategy_id"],
                    "primary_candidate_id": bench["candidate_id"],
                    "primary_strategy_rule_hash": bench["strategy_rule_hash"],
                    "primary_strategy_generation_id": bench["strategy_generation_id"],
                    "primary_strategy_artifact_sha256": bench.get(ARTIFACT_SHA256_FIELD),
                }, feature_row, now=now)
                if shadow is None:
                    continue
                supporting_plans.append({**shadow, "shadow_reason": bench["shadow_reason"]})
            if supporting_plans:
                summary["supporting_plans"] = supporting_plans
            # The economics door, before the portfolio lock: it is pure arithmetic on the plan
            # and takes no state, so making every cycle contend for the lock to learn the trade
            # was uneconomic would serialise cycles on a question none of them needed shared
            # state to answer.
            cost_refusal = entry_cost_refusal(plan) if plan is not None else None
            liq_refusal = stop_beyond_liquidation_refusal(plan) if plan is not None and cost_refusal is None else None
            if cost_refusal is not None:
                summary["open_refused"] = cost_refusal
                records.append(_event("open_refused", {**cost_refusal, "read_only": True}))
            elif liq_refusal is not None:
                summary["open_refused"] = liq_refusal
                records.append(_event("open_refused", {**liq_refusal, "read_only": True}))
            elif plan is not None:
                portfolio_lock = positions_dir(root) / "portfolio.lock"
                with locked(portfolio_lock, code="PAPER_STATE_LOCKED", label="paper portfolio"):
                    open_books = list_open_positions(root)
                    same_symbol = sum(1 for ctx, _ in open_books if ctx.symbol == context.symbol)
                    refusal = None
                    if len(open_books) >= MAX_CONCURRENT_POSITIONS:
                        refusal = {
                            "reason_code": POSITION_LIMIT_PORTFOLIO,
                            "open_positions": len(open_books),
                            "limit": MAX_CONCURRENT_POSITIONS,
                        }
                    elif same_symbol >= MAX_POSITIONS_PER_SYMBOL:
                        refusal = {
                            "reason_code": POSITION_LIMIT_SYMBOL,
                            "symbol": context.symbol,
                            "open_positions": same_symbol,
                            "limit": MAX_POSITIONS_PER_SYMBOL,
                        }
                    else:
                        # The third cap, and the first that reads the book's SHAPE rather than
                        # its size: the two above permit twenty simultaneous longs. Counted
                        # from the `open_books` already in hand, under the same lock and for
                        # the same reason — a lean is a property of every book together, so two
                        # cycles that each counted before either wrote would both see room only
                        # one of them had.
                        _admitted, skew_refusal = directional_skew_admits(
                            open_books, plan.get("direction")
                        )
                        refusal = skew_refusal
                    if refusal is not None:
                        summary["open_refused"] = refusal
                        records.append(_event("open_refused", {**refusal, "read_only": True}))
                    else:
                        opened = open_position({**plan, "venue": context.venue}, now=now)
                        store.save_position(opened)
                        summary["opened"] = {
                            "position_id": opened["position_id"],
                            "direction": opened["direction"],
                            "strategy_id": opened.get("strategy_id"),
                        }
                        records.append(_event("open", {
                            "position_id": opened["position_id"],
                            "direction": opened["direction"],
                            "entry_price": opened["entry_price"],
                            "stop_loss": opened["stop_loss"],
                            "take_profit": opened["take_profit"],
                            "strategy_id": opened.get("strategy_id"),
                            "strategy_rule_hash": opened.get("strategy_rule_hash"),
                            ARTIFACT_SHA256_FIELD: opened.get(ARTIFACT_SHA256_FIELD),
                        }))
            # This candle has now been evaluated for a new entry (matched or not, capped
            # or not); record it so the same candle is not re-evaluated next tick. Only
            # persisted when the paper store is live — a dry run keeps no durable state,
            # so it also keeps no marks (and behaves as an ungated dry run, as before).
            if routing_marks is not None and candle_time is not None and getattr(store, "filesystem_write", False):
                routing_marks.record(context.key, candle_time)
    return summary, records
