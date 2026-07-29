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
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from ..errors import ToolError
from ..filelock import locked
from . import market_data
from .cost import (
    DEFAULT_FUNDING_BPS_PER_INTERVAL,
    DEFAULT_MAKER_FEE_BPS,
    DEFAULT_SLIPPAGE_BPS,
    DEFAULT_TAKER_FEE_BPS,
    FUNDING_SOURCE_UNCHARGED,
    FUNDING_SOURCE_VENUE,
)
from .paper import OCCUPYING_STATUSES, state_dir
from .robustness import HOLDOUT_CONFIRMED, ROBUST, classify_verdict, verdict_rank
from .strategy import SpecParseError, StrategySpec, load_strategy_pool

POOL_FILENAME = "active_strategy_pool.json"
CANDIDATES_FILENAME = "strategy_candidates.jsonl"

# The basis of every R in a candidate's quality view.
#
# These figures come from `backtest_evidence`, and the factory backtest charges costs:
# `factory.backtest_spec` runs every closed trade through `cost.apply_cost_model` and states
# that `result_R` — and therefore `expectancy` and `champion_score` — is the NET R after fees
# and slippage, with `gross_R` alongside. The holdout aggregates are built the same way.
#
# The previous value here said the opposite. It came from reading `robustness.py`'s "the cost
# model was not ported" as a statement about R; it is a statement about the scorer's
# cost-ROBUSTNESS term — whether the edge is stable ACROSS cost assumptions — which is a
# different property from whether costs were charged at all.
EDGE_COST_BASIS_NET = "net_of_fees_and_slippage"

# ...and at WHICH rates, because that is no longer one answer for the whole store. The taker
# default moved from the ported 2.5 bps to the venue's measured 5.0, and `backtest_evidence`
# is durable — candidates scored before the change keep the numbers they were scored with.
# Ranking them against newer ones is comparing a cheaper venue to the real one, so the basis
# has to travel WITH each candidate rather than be assumed for the view.
EDGE_COST_BASIS_UNRECORDED = "cost_model_unrecorded"


def _is_number(value: Any) -> bool:
    """A real number, not a bool — ``isinstance(True, int)`` is True and would rescale on it."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def expectancy_at(
    record: Mapping[str, Any], *, taker_fee_bps: float, maker_fee_bps: float | None = None,
) -> float | None:
    """This candidate's expectancy re-derived at different fee rates. Exact, or None.

    Raising the taker default split the store: 224 candidates on this machine keep numbers
    scored at 2.5 bps while the venue charges 5.0, and `backtest_evidence` is durable so
    nothing re-scores them. Re-running the backtest is not available either — the snapshot
    that produced the evidence is not stored, only its hash.

    But the conversion needs neither. In `cost.apply_cost_model` the taker term

        taker_fee_cost_r = (taker-charged fills) * taker_fee_bps / 10000 / risk

    is **linear in the rate**, and the fills depend only on slippage. So changing the taker
    rate alone scales the recorded taker fee cost and leaves everything else untouched:

        total_net_r(new) = total_net_r(old) - total_taker_fee_cost_r(old) * (new/old - 1)

    Both terms are already in `cost_summary`. The result is exact, not an estimate — a test
    pins it against a real backtest re-run at the new rate rather than asserting the algebra.

    **The two legs scale independently.** Since 2026-07-28 a take-profit exit rests as a maker
    LIMIT and is charged the maker rate, so `total_fee_cost_r` is a mixture. Scaling the whole
    mixture by a taker ratio would charge the maker leg a rate it never faced — so the maker
    share is separated first, read from `total_maker_fee_cost_r`, and each share is scaled by
    its own ratio:

        total_net_r(new) = total_net_r(old)
                         - taker_share * (new_taker/old_taker - 1)
                         - maker_share * (new_maker/old_maker - 1)

    The maker term exists because the maker rate is the one figure here that is **not
    measured**: `DEFAULT_MAKER_FEE_BPS` is Binance's published standard rate, and no maker fill
    has been placed yet to check it against. Its error direction is the unsafe one — a real rate
    above 2.0 bps means this model reports an edge better than reality. Making the maker leg
    rescalable *before* the first candidate is scored under it is what keeps that eventual
    measurement from splitting the store a third time: every candidate scored at the published
    rate converts exactly to the measured one, the same way the taker change already converts.

    ``maker_fee_bps=None`` leaves the maker share alone — the caller is asking only about the
    taker axis. A record whose maker share is zero is unaffected either way: a model with no
    maker leg has nothing on that axis to rescale, and that is arithmetic, not an assumption.
    A record with a maker share but no recorded maker rate refuses, because the ratio's
    denominator would be a guess.

    Returns None when the record predates `cost_summary`, or carries no closed trades, or
    was scored at a rate of zero (nothing to scale). Never guesses.
    """
    evidence = record.get("backtest_evidence") or {}
    summary = evidence.get("cost_summary") or {}
    model = summary.get("cost_model") or {}
    old_rate = model.get("taker_fee_bps")
    net, fee = summary.get("total_net_r"), summary.get("total_fee_cost_r")
    closed = evidence.get("closed_count")
    if not all(_is_number(v) for v in (old_rate, net, fee, closed)):
        return None
    if not old_rate or not closed:
        return None
    maker_fee = summary.get("total_maker_fee_cost_r", 0.0)
    if not _is_number(maker_fee):
        # Present but unreadable is not the same as absent: absent means all-taker, unreadable
        # means the split is unknown and any rescale would be a guess about real money.
        return None
    adjusted = net - (fee - maker_fee) * (taker_fee_bps / old_rate - 1.0)
    if maker_fee_bps is not None and maker_fee:
        old_maker = model.get("maker_fee_bps")
        if not _is_number(old_maker) or not old_maker:
            return None
        adjusted -= maker_fee * (maker_fee_bps / old_maker - 1.0)
    return round(adjusted / closed, 8)


def cost_basis_of(record: Mapping[str, Any]) -> str:
    """The cost model one candidate was actually scored under, from its own evidence.

    `factory.backtest_spec` records it in `cost_summary.cost_model`, so this reads what the
    scoring used rather than what the module currently defaults to. A record predating that
    field reports UNRECORDED — not the current default, which would claim a candidate had
    paid a rate it never faced.

    The maker rate joins the string only when the record carries one. That is deliberate: a
    candidate scored before the maker take-profit exit (2026-07-28) keeps the exact basis
    string it has always reported, so the split in the store stays legible as two bases rather
    than every old candidate silently acquiring a third term it was never scored under.

    The funding term follows the same rule and is the sharper case: a record with no
    ``funding_source`` was scored on a PERPETUAL with no carry at all, and the string says
    ``+funding_uncharged`` rather than omitting it. An omitted term reads as "this basis has
    one fewer axis"; a named one reads as "this basis is missing the cost that dominates a
    multi-week hold", which is what it is.
    """
    summary = (record.get("backtest_evidence") or {}).get("cost_summary") or {}
    model = summary.get("cost_model") or {}
    taker, slip = model.get("taker_fee_bps"), model.get("slippage_bps")
    if not isinstance(taker, (int, float)) or not isinstance(slip, (int, float)):
        return EDGE_COST_BASIS_UNRECORDED
    maker = model.get("maker_fee_bps")
    maker_term = f"+maker_{maker}bps" if isinstance(maker, (int, float)) else ""
    funding = model.get("funding_bps_per_interval")
    if isinstance(funding, (int, float)) and not isinstance(funding, bool):
        source = model.get("funding_source") or FUNDING_SOURCE_VENUE
        funding_term = f"+funding_{funding}bps/8h({source})"
    else:
        funding_term = f"+funding_{FUNDING_SOURCE_UNCHARGED}"
    return f"{EDGE_COST_BASIS_NET}:taker_{taker}bps{maker_term}+slip_{slip}bps{funding_term}"


def current_cost_basis() -> str:
    """The basis a candidate minted right now would carry.

    Formatted by `cost_basis_of` over a synthetic record rather than by a second format
    string, so the "what the store holds" and "what the model charges" sides cannot drift
    into two spellings of the same rates."""
    return cost_basis_of({"backtest_evidence": {"cost_summary": {"cost_model": {
        "taker_fee_bps": DEFAULT_TAKER_FEE_BPS,
        "maker_fee_bps": DEFAULT_MAKER_FEE_BPS,
        "slippage_bps": DEFAULT_SLIPPAGE_BPS,
        "funding_bps_per_interval": DEFAULT_FUNDING_BPS_PER_INTERVAL,
        "funding_source": FUNDING_SOURCE_VENUE,
    }}}})


# How one candidate's basis stands against the model the venue charges today. Ordered, because
# the ONLY thing that matters about a stale basis is which way its error points.
#
# Equality is the wrong test and was the first thing tried. On this machine 90 of 359 candidates
# are scored at taker 5.0 with no maker leg: their take-profit exit paid taker 5.0 plus adverse
# slippage where the current model charges maker 2.0 and no slippage at all. Those numbers are
# too PESSIMISTIC, not too generous — refusing them would have made the escape hatch the normal
# door, and a gate everyone escapes is not a gate.
COST_BASIS_RANK_CURRENT = 0       # scored under exactly this model
COST_BASIS_RANK_CONSERVATIVE = 1  # every rate at or above the current one: understates the edge
COST_BASIS_RANK_OPTIMISTIC = 2    # some rate BELOW the current one: overstates the edge
COST_BASIS_RANK_UNRECORDED = 3    # no cost model recorded: the direction is unknown

# Which of those may back a promotion. Optimistic and unrecorded evidence is refused at the
# door — the first inflates the number an operator reads, the second cannot even say whether
# it does. Conservative evidence promotes: its error runs against the candidate, so a lineage
# that clears the bar under it clears the bar under the real model too.
PROMOTABLE_COST_BASIS_RANKS = frozenset({COST_BASIS_RANK_CURRENT, COST_BASIS_RANK_CONSERVATIVE})


def cost_basis_rank(record: Mapping[str, Any]) -> int:
    """Which `COST_BASIS_RANK_*` tier this candidate's evidence falls in.

    One authority for two consumers: `rank_candidates` orders by it so cheap-venue rows stop
    outranking real ones, and `assert_promotable_cost_basis` refuses on it at the promotion
    door. A single rule means the list an operator reads and the gate that stops them can
    never disagree about which rows are believable."""
    model = ((record.get("backtest_evidence") or {}).get("cost_summary") or {}).get("cost_model") or {}
    taker, slip = model.get("taker_fee_bps"), model.get("slippage_bps")
    if not _is_number(taker) or not _is_number(slip):
        return COST_BASIS_RANK_UNRECORDED
    funding = model.get("funding_bps_per_interval")
    if not _is_number(funding):
        # No carry charged at all, on a PERPETUAL. Unlike the maker case below there is no
        # "what it actually paid" to fall back on — the model simply had no such axis, and a
        # missing cost cannot be read as a cost of zero on an instrument that charges every 8
        # hours. `_EXIT_PARAMS` allows a 1d spec to hold 12-48 days, so what is missing is 36
        # to 144 settlements: several times the fee legs this basis does record.
        #
        # OPTIMISTIC rather than UNRECORDED, and the distinction is the honest one. The
        # direction IS knowable here for the case that matters: the venue's base rate is
        # positive and the historical mean is positive, so an omitted carry overstates every
        # LONG lineage. It understates shorts, and refusing those too is a cost accepted with
        # open eyes — the alternative is a tier whose meaning depends on the spec's direction,
        # which is a property of the trade and not of the cost model this function ranks.
        return COST_BASIS_RANK_OPTIMISTIC
    maker = model.get("maker_fee_bps")
    if (taker == DEFAULT_TAKER_FEE_BPS and maker == DEFAULT_MAKER_FEE_BPS
            and slip == DEFAULT_SLIPPAGE_BPS and funding == DEFAULT_FUNDING_BPS_PER_INTERVAL):
        return COST_BASIS_RANK_CURRENT
    # A record with no maker rate charged its exit at the TAKER rate — that model had no maker
    # leg at all, so the honest comparison against today's maker rate is what the exit actually
    # paid, not a missing field treated as zero (which would read every legacy row as optimistic).
    maker_charged = maker if _is_number(maker) else taker
    if (taker >= DEFAULT_TAKER_FEE_BPS and maker_charged >= DEFAULT_MAKER_FEE_BPS
            and slip >= DEFAULT_SLIPPAGE_BPS and funding >= DEFAULT_FUNDING_BPS_PER_INTERVAL):
        return COST_BASIS_RANK_CONSERVATIVE
    return COST_BASIS_RANK_OPTIMISTIC


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


# --- evidence window depth -----------------------------------------------------
#
# The second axis on which two candidates can be incomparable. The cost basis above asks what
# a row PAID; this asks how much market it was shown. `backtest_evidence.bars_replayed` has
# recorded it since the factory's first version and nothing has ever read it, so a row scored
# over 350 bars and a row scored over 1400 sat in one list looking equally examined.
#
# It became load-bearing when the factory grew a bar-count floor beneath its calendar window
# (`market_data.MIN_FACTORY_BARS` under `FACTORY_DEPTH_DAYS`), taking 1d from 500 collected
# bars to 2000. Re-scoring the same 25 specs at the deeper window moved them from
# 0 ROBUST / 20 FRAGILE to 12 ROBUST / 12 PROVISIONAL / 1 FRAGILE — so the verdict beside a
# row is partly a statement about that row's window.
#
# The store does not hold two depths; counted on the live machine (Thomas, reviewing the
# floor on PR #339) it already holds FOUR, and the floor adds a fifth: 25 rows replayed 1400
# bars, 12 replayed 500, 18 replayed 350 — and 41 record no depth at all. That last group is
# the one that decides the shape of this tier, so it is answered first, below.
#
# WHICH WAY THE ERROR POINTS — the question the cost tiers are ordered by, asked of depth.
# Three of the scorer's five terms and both of its verdict gates are counted over TRADES, and
# a shorter window produces fewer of them:
#   - `sample_adequacy` (weight 0.30) is trades-per-parameter, and beneath
#     `CRITICAL_TRADES_PER_PARAMETER` it vetoes straight to FRAGILE whatever else scored;
#   - `temporal_consistency` (0.25) reads a walk-forward pass rate that stays None — scoring
#     zero — until the replay's slices each hold `MIN_TRADES_PER_WINDOW` trades;
#   - ROBUST additionally requires a holdout of at least `MIN_HOLDOUT_TRADES` closed trades,
#     so a thin tail puts the top verdict out of reach however good the edge is.
# A shallow row's verdict is therefore a FLOOR: it withholds credit the strategy may deserve,
# which is exactly what the 500 → 2000 re-score demonstrated.
#
# IT IS NOT AIRTIGHT THE WAY THE COST TIERS ARE, and that difference decides the door.
# `regime_breadth` (0.20) is a RATIO — profitable regimes over regimes traded — so a window
# short enough to hold one regime can score it 1.0 where a longer window would show the edge
# failing in half of them. Depth cuts both ways on that term. More fundamentally: re-pricing
# a candidate's trades at a new fee rate is the SAME sample seen differently, which is why
# "clears the bar under a conservative basis ⇒ clears it under the real one" is a theorem and
# can back a refusal. Re-running a spec over a longer window is a DIFFERENT sample. A shallow
# row is not a deep row carrying a handicap; it is a smaller one.
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

EVIDENCE_DEPTH_REPLAYED = "replayed"
EVIDENCE_DEPTH_UNRECORDED = "evidence_depth_unrecorded"

# The tier means **at least** the current window, and the name now says so. It was
# `..._CURRENT`, and the `--list` view printed `CURRENT` beside four different depth strings
# under a header reading "these rows were NOT shown the same market" — the table contradicting
# the sentence above it. Renaming was the fix rather than adding a `DEEPER` tier, because a
# fourth rank has to sort somewhere and every position is wrong: below FULL demotes the
# better-supported row, above it demotes every freshly minted candidate to second place
# forever. Adequacy is one fact; the exact window is already printed next to it.
EVIDENCE_DEPTH_RANK_FULL = 0        # replayed at least the window the factory collects today
EVIDENCE_DEPTH_RANK_SHALLOW = 1     # a shorter window: less market, and a verdict that reflects it
EVIDENCE_DEPTH_RANK_UNRECORDED = 2  # no bar count, or no timeframe to read one in: span unknown

# Which of those may back a promotion. SHALLOW promotes because its error is known and runs
# against the candidate; UNRECORDED is refused because no direction can be read off it and,
# unlike an unrecorded cost basis, no part of it can be re-derived later.
PROMOTABLE_EVIDENCE_DEPTH_RANKS = frozenset({
    EVIDENCE_DEPTH_RANK_FULL, EVIDENCE_DEPTH_RANK_SHALLOW,
})

# FULL is "at or above" rather than "exactly at". A deeper row is better supported, not
# incomparable, and giving depth its own top tier would sort every freshly minted candidate
# beneath the legacy rows this tier exists to flag.
#
# The tolerance absorbs COLLECTION shortfall, not policy: a venue gap or an unclosed final
# candle returns the window a bar or two short, and marking those SHALLOW forever would make
# the tier noise. It cannot hide a policy change — the only one on record is 4x. A symbol
# younger than the window falls short by far more than this, and is correctly SHALLOW.
EVIDENCE_DEPTH_TOLERANCE = 0.95


def _replayed_window(record: Mapping[str, Any]) -> tuple[int, str] | None:
    """This row's ``(bars_replayed, timeframe)``, or None if either is missing.

    Both are required because neither means anything alone: a bar count is a span only once
    you know how long a bar is."""
    bars = (record.get("backtest_evidence") or {}).get("bars_replayed")
    timeframe = (record.get("strategy_spec") or {}).get("timeframe")
    if not _is_number(bars) or bars <= 0 or timeframe not in market_data.TIMEFRAMES:
        return None
    return int(bars), str(timeframe)


def evidence_depth_of(record: Mapping[str, Any]) -> str:
    """The window one candidate was actually replayed over, from its own evidence.

    Bars AND calendar span, because neither alone is the property in question: 2000 bars is
    5.5 years at 1d and three weeks at 15m, and the regimes an edge has faced are a calendar
    fact. A row with no ``bars_replayed`` — or no timeframe to read one in — reports
    UNRECORDED rather than the current window, the same rule `cost_basis_of` applies to a
    missing cost model."""
    window = _replayed_window(record)
    if window is None:
        return EVIDENCE_DEPTH_UNRECORDED
    bars, timeframe = window
    days = round(bars * market_data.TIMEFRAMES[timeframe] / 1440)
    return f"{EVIDENCE_DEPTH_REPLAYED}:{bars}bars_{timeframe}_{days}d"


def expected_replayed_bars(timeframe: str) -> int | None:
    """How many bars a candidate minted right now at ``timeframe`` would be SCORED over.

    The collector's target for that timeframe, put through the factory's holdout split —
    `bars_replayed` records the scored window, not the collected one, so both sides of the
    comparison have to be the same fraction of it.

    Reads the live `market_data.factory_candle_target`, so the day the factory's window moves
    every stored row is re-tiered against the new one with no second constant to keep in step.
    `current_cost_basis` reads the live fee defaults for exactly this reason. Called through
    the module rather than a bound name so the read follows the policy wherever it moves —
    the target is a calendar span, a bar floor and a clamp, and which of the three binds is
    itself subject to change.

    None for a timeframe the collector has no window for — a junk value on a durable row is
    an unknown span, not an error to raise through a reporting path."""
    if timeframe not in market_data.TIMEFRAMES:
        return None
    # Deferred: `factory` imports this module for candidate ids, so the split rule can only be
    # read at call time. Read rather than copied — a second 0.70 here would drift the day the
    # holdout fraction moves, and the two sides would silently stop meaning the same window.
    from .factory import holdout_split_index

    return holdout_split_index(market_data.factory_candle_target(timeframe))


def current_evidence_depth(timeframe: str) -> str:
    """The depth a candidate minted right now at ``timeframe`` would carry.

    Formatted by `evidence_depth_of` over a synthetic record rather than by a second format
    string, so "what the store holds" and "what the factory collects" cannot drift into two
    spellings of the same window — same construction as `current_cost_basis`."""
    expected = expected_replayed_bars(timeframe)
    if expected is None:
        return EVIDENCE_DEPTH_UNRECORDED
    return evidence_depth_of({
        "strategy_spec": {"timeframe": timeframe},
        "backtest_evidence": {"bars_replayed": expected},
    })


def evidence_depth_rank(record: Mapping[str, Any]) -> int:
    """Which `EVIDENCE_DEPTH_RANK_*` tier this candidate's window falls in.

    One authority for three consumers, like `cost_basis_rank`: `rank_candidates` orders on it,
    the `--list` view reports it, and `assert_promotable_evidence_depth` refuses on it — so
    the ordering an operator reads, the block that explains it and the gate that stops them
    can never disagree.

    Note which tier the gate acts on: SHALLOW is ranked and surfaced, never refused. Only
    UNRECORDED is a door. See the block above for why the two cases part company."""
    window = _replayed_window(record)
    if window is None:
        return EVIDENCE_DEPTH_RANK_UNRECORDED
    bars, timeframe = window
    expected = expected_replayed_bars(timeframe)
    if expected is None:
        return EVIDENCE_DEPTH_RANK_UNRECORDED
    return (EVIDENCE_DEPTH_RANK_FULL if bars >= expected * EVIDENCE_DEPTH_TOLERANCE
            else EVIDENCE_DEPTH_RANK_SHALLOW)


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
# provable is used, and the rest is reported rather than refused (`near_duplicate_groups`).

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
        # sharing a hash are one lineage measured more than once — a re-scored row and
        # its original are the obvious case, and the store is append-only precisely so
        # both can exist. Duplicate LINEAGES are already refused by
        # `assert_pool_identity_unique` and the promotion door's candidate_id check;
        # this function exists only for what those cannot see.
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
    """Same window, same trade counts, but not identical R — reported, never refused.

    The pair this exists for: two SOLUSDT lineages that closed 82 trades each with the
    same 45/37 split and the same drawdown, differing in net R by 0.0016. Almost
    certainly one strategy wearing two rules, but "almost certainly" is a judgement,
    and this module refuses only on what it can prove. So an operator gets told.
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


# --- candidate identity (single source) ----------------------------------------

def derive_candidate_id(record: Mapping[str, Any]) -> str:
    """The globally unique id of one candidate: its lineage, not its display name.

    ``strategy_id`` restarts at S001 every factory generation, so it can never key a
    lookup. The id derives from (generation_id, strategy_rule_hash,
    evidence_input_sha256) — the exact strategy content in its exact generation with
    its exact evidence window — so legacy rows without a stored ``candidate_id``
    derive the same id on every read and the append-only store is never rewritten."""
    return integrity.short_id("cand", {
        "generation_id": record.get("generation_id"),
        "strategy_rule_hash": record.get("strategy_rule_hash"),
        "evidence_input_sha256": record.get("evidence_input_sha256"),
    })


def candidate_id(record: Mapping[str, Any]) -> str:
    stored = record.get("candidate_id")
    if isinstance(stored, str) and stored:
        return stored
    return derive_candidate_id(record)


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
    """No two active entries may share a ``strategy_id`` or a ``candidate_id``.

    Both are keys the runtime resolves by: ``strategy_id`` selects the champion and
    keys every lifecycle status update, ``candidate_id`` names the lineage an outcome
    is attributed to. A duplicate makes routing, demotion and attribution ambiguous —
    the pool would silently pick one entry and update the other. Fail-closed at both
    doors (install and read) so a duplicate can neither be written nor traded on."""
    seen_strategy: set[str] = set()
    seen_candidate: set[str] = set()
    for entry in pool.get("active_strategies") or []:
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


def load_active_pool(root: Path | None = None) -> dict[str, Any]:
    """The active pool, validated spec-by-spec and identity-unique. Missing = empty."""
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
    return pool


def routable_contexts(pool: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Distinct ``(symbol, timeframe)`` pairs the active pool can route on.

    One pair per ``(symbol_scope entry, timeframe)`` — every symbol a strategy is
    scoped to, exactly what :func:`paper.route_entries` now matches on — so a
    fan-out proposes a cycle for every context a strategy could fire in (a
    multi-symbol strategy contributes each of its symbols) and none where it never
    could. Non-occupying or spec-less entries contribute nothing. Deduplicated and
    sorted for a stable, deterministic cycle order."""
    contexts: set[tuple[str, str]] = set()
    for entry in pool.get("active_strategies") or []:
        if entry.get("status") not in OCCUPYING_STATUSES or not entry.get("strategy_spec"):
            continue
        spec = StrategySpec.from_dict(entry["strategy_spec"])
        for scoped_symbol in spec.symbol_scope:
            contexts.add((str(scoped_symbol), str(spec.timeframe)))
    return sorted(contexts)


def install_active_pool(pool: dict[str, Any], *, root: Path | None = None) -> int:
    """Install (replace) the active pool — the OPERATOR door, not a runtime call.

    Validates every spec and the identity invariant first (fail-closed), then writes
    atomically. Returns the number of strategies installed. Callers are operator
    scripts acting on an explicit confirmation (the pre-R10 promotion posture); the
    runtime cycle never calls this."""
    specs = load_strategy_pool(pool)
    assert_pool_identity_unique(pool)
    path = pool_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code="STRATEGY_POOL_LOCKED", label="active strategy pool"):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    return len(specs)


def update_statuses(
    decisions: list[dict[str, Any]], *, root: Path | None = None, updated_by: str = "lifecycle_agent"
) -> int:
    """Apply lifecycle status transitions to the active pool (C10). Locked, guarded.

    The narrowest possible pool mutation: only ``status`` and the running
    ``lifecycle_consecutive_failures`` of named strategies change — specs, hashes,
    scores and membership are untouched, so this can never smuggle a promotion.
    Guards, each fail-closed: unknown strategy id refused; a CURRENTLY terminal
    entry is immutable (reactivation is the approval door, never this); and a
    transition record that isn't an evaluate_lifecycle decision shape is refused.
    Returns the number of entries whose status actually changed."""
    from .lifecycle import TERMINAL_STATUSES  # local: avoids a module cycle

    if not decisions:
        return 0
    path = pool_path(root)
    with locked(path.with_suffix(".lock"), code="STRATEGY_POOL_LOCKED", label="active strategy pool"):
        pool = load_active_pool(root)
        entries = {e.get("strategy_id"): e for e in pool.get("active_strategies") or []}
        changed = 0
        for decision in decisions:
            strategy_id = decision.get("strategy_id")
            new_status = decision.get("new_status")
            if not (isinstance(strategy_id, str) and strategy_id and isinstance(new_status, str)):
                raise ToolError("LIFECYCLE_DECISION_INVALID", "transition lacks strategy_id/new_status")
            entry = entries.get(strategy_id)
            if entry is None:
                raise ToolError("LIFECYCLE_UNKNOWN_STRATEGY", f"no pool entry for {strategy_id}")
            if str(entry.get("status")) in TERMINAL_STATUSES:
                raise ToolError(
                    "LIFECYCLE_TERMINAL_IMMUTABLE",
                    f"{strategy_id} is terminal; reactivation is the approval door, not a transition",
                )
            entry["lifecycle_consecutive_failures"] = int(decision.get("consecutive_failures") or 0)
            if new_status != entry.get("status"):
                entry["status"] = new_status
                entry["lifecycle_updated_at"] = decision.get("created_at_utc")
                entry["lifecycle_decision_id"] = decision.get("strategy_lifecycle_decision_id")
                changed += 1
        pool["updated_by"] = updated_by
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        return changed


def read_candidates(root: Path | None = None) -> list[dict[str, Any]]:
    """All candidate rows, oldest first — a VERIFIED read.

    Any row carrying a ``record_sha256`` (everything :func:`append_candidates` has
    written since the store began stamping) must recompute it exactly; a mismatch
    raises ``CANDIDATES_TAMPERED`` so promotion asks/executions fail closed rather
    than binding Thomas's approval to silently edited evidence. Rows persisted
    before stamping existed have no hash to check — documented gap, closed for
    every new row."""
    path = candidates_path(root)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ToolError("CANDIDATES_UNREADABLE", f"strategy candidates unreadable: {exc.strerror}") from exc
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ToolError("CANDIDATES_UNREADABLE", f"strategy candidates line {i + 1} is not valid JSON") from exc
        if not isinstance(record, dict):
            continue
        stored = record.get("record_sha256")
        if stored is not None:
            body = {k: v for k, v in record.items() if k != "record_sha256"}
            if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
                raise ToolError(
                    "CANDIDATES_TAMPERED", f"strategy candidates line {i + 1} fails its self-hash"
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


# --- candidate ranking (M4a): robustness first-pass, win-rate + reward:risk second -

# A payoff ratio a losing-free backtest can't divide out. It floats an all-wins
# lineage to the top of its robustness tier for the sort only; the displayed
# reward:risk stays honest (None → "∞"), so this cap is never shown as a real ratio.
_ALL_WINS_RR_SORT = float("inf")


def _as_float(value: Any) -> float:
    try:
        return float(value) if value is not None and not isinstance(value, bool) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _designed_reward_risk(record: Mapping[str, Any]) -> float | None:
    """target_atr / stop_atr from the spec — the legacy fallback when a candidate
    predates the realized avg_win_R/avg_loss_R evidence. None if it can't be read."""
    exit_rules = ((record.get("strategy_spec") or {}).get("exit_rules")) or {}
    stop = _as_float(exit_rules.get("stop_atr"))
    target = _as_float(exit_rules.get("target_atr"))
    return round(target / stop, 8) if stop > 0 and target > 0 else None


def candidate_quality(record: Mapping[str, Any]) -> dict[str, Any]:
    """The ranking view of one candidate: robustness tier + realized performance.

    First-pass ``verdict_rank`` (ROBUST < PROVISIONAL < FRAGILE < unknown) never
    changes with performance — the anti-overfit filter stays authoritative. The
    second-pass axes are ``win_rate`` and the realized ``reward_risk`` (avg_win_R /
    avg_loss_R); ``edge_quality = win_rate * reward_risk`` combines them so a lineage
    strong on *both* outranks one strong on either alone. A candidate with no losing
    trades has an undefined ratio (``reward_risk`` None, ``all_wins`` True); one
    predating the realized evidence falls back to the designed target/stop ratio
    (``reward_risk_basis`` ``"designed"``)."""
    evidence = record.get("backtest_evidence") or {}
    robustness = evidence.get("robustness") or {}
    # Out-of-sample status rides into the ranking view so the promotion door can show
    # it: with ROBUST now gated on it, "PROVISIONAL because unconfirmed" and
    # "PROVISIONAL because it failed forward" are very different things to promote.
    holdout_state = str(robustness.get("holdout_status") or "UNCONFIRMED")
    # The verdict is RECOMPUTED from the stored components, never read back as a label.
    #
    # It used to be read: `robustness.get("verdict")`. Verdicts are written once, at mint
    # time, under whatever rule was current then — and the rule changed when ROBUST became
    # gated on out-of-sample survival. Candidates minted before that kept a stored ROBUST
    # while their holdout read UNCONFIRMED, a pair the rule can no longer produce. Measured
    # on this machine: 12 of 269 candidates, and because `rank_candidates` orders by verdict
    # tier FIRST, all 12 sorted above the 13 PROVISIONAL+CONFIRMED lineages that had actually
    # survived unseen bars. The shortlist was inverted on exactly the property the holdout
    # rule was added to enforce.
    #
    # `classify_verdict` is the one authority for the rule, so a later change to it cannot
    # leave stale labels behind again. A record missing the components keeps its stored
    # verdict — recomputing from absent inputs would invent a rating, not correct one.
    stored_verdict = robustness.get("verdict")
    score = robustness.get("robustness_score")
    tpp = robustness.get("trades_per_parameter")
    if isinstance(score, (int, float)) and isinstance(tpp, (int, float)):
        verdict = classify_verdict(float(score), float(tpp), holdout_state)
    else:
        verdict = stored_verdict
    closed = int(_as_float(evidence.get("closed_count")))
    win_count = int(_as_float(evidence.get("win_count")))
    win_rate = round(win_count / closed, 8) if closed else 0.0

    all_wins = False
    if "avg_win_R" in evidence or "avg_loss_R" in evidence:
        basis = "realized"
        avg_win = _as_float(evidence.get("avg_win_R"))
        avg_loss = _as_float(evidence.get("avg_loss_R"))
        if avg_loss > 0:
            reward_risk: float | None = round(avg_win / avg_loss, 8)
        elif avg_win > 0:
            reward_risk, all_wins = None, True  # no losses to divide by
        else:
            reward_risk = 0.0
    else:
        reward_risk = _designed_reward_risk(record)
        basis = "designed" if reward_risk is not None else "none"

    rr_sort = _ALL_WINS_RR_SORT if all_wins else (reward_risk or 0.0)
    return {
        "candidate_id": candidate_id(record),
        "verdict": verdict,
        "verdict_rank": verdict_rank(verdict),
        "holdout_status": holdout_state,
        "robustness_score": round(_as_float(record.get("champion_score")), 8),
        "win_rate": win_rate,
        "reward_risk": reward_risk,
        "reward_risk_basis": basis,
        "all_wins": all_wins,
        "expectancy": round(_as_float(evidence.get("expectancy")), 8),
        "closed_count": closed,
        "edge_quality": win_rate * rr_sort,
        # What every R above does NOT include. Paper settlement models no fee, slippage or
        # funding by design ("Accounting is R-based only... paper sizing added nothing but
        # noise"), and the robustness scorer withholds its cost term for the same reason
        # ("the cost model was not ported, so cost_robustness inputs are withheld"). Both
        # are honest about it in their own docstrings; the promotion surface — the one an
        # operator actually reads before putting real money behind a lineage — said nothing,
        # so a cost-free expectancy arrived looking like a net one.
        #
        # A field rather than a printed sentence because it is a property OF the number: a
        # later cost-adjusted basis becomes a different value here, and any consumer that
        # compares two candidates can refuse to compare across bases.
        "cost_basis": cost_basis_of(record),
        # ...and which way that basis errs against the model in force today. The string says
        # WHAT this row paid; this says whether reading it next to a current row flatters it.
        "cost_basis_rank": cost_basis_rank(record),
        # The other half of "were these two rows measured alike": how much market this one
        # replayed. The verdict above is counted over trades, and a shorter window has fewer
        # of them — so the window is a property OF the verdict, in the same way the cost basis
        # is a property of the expectancy, and it travels with the row for the same reason.
        "evidence_depth": evidence_depth_of(record),
        "evidence_depth_rank": evidence_depth_rank(record),
        # The same expectancy at the rates the venue actually charges, so a candidate scored
        # under the old default can be read against a new one instead of merely flagged as
        # incomparable. None when it cannot be derived — never the stored number relabelled.
        #
        # Both axes, not just the taker one: the maker rate is published rather than measured,
        # so the day it IS measured this view converts every maker-scored candidate rather than
        # stranding it. Records with no maker leg are untouched by the maker argument.
        #
        # Alongside `expectancy` rather than replacing it: the stored figure is what the
        # durable evidence says, and overwriting it would make the record and the view
        # disagree about what was measured.
        "expectancy_at_current_costs": expectancy_at(
            record, taker_fee_bps=DEFAULT_TAKER_FEE_BPS, maker_fee_bps=DEFAULT_MAKER_FEE_BPS,
        ),
    }


def rank_candidates(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Candidates ordered for the promotion decision, latest-wins per lineage.

    Deterministic total order: **cost basis tier first**, then robustness verdict tier (the
    anti-overfit first-pass), then the **evidence depth tier**, then ``edge_quality``
    (win-rate × realized reward:risk) descending, then ``expectancy`` descending, then
    ``candidate_id`` ascending so a tie never depends on store order. Re-appends of a lineage
    collapse to the latest row.

    The basis tier leads because every key after it is a number scored under that basis, and
    269 of 359 rows on this machine were scored under a cheaper one. `verdict` is recomputed
    but from a `robustness_score` fitted at the old rate; `edge_quality` and `expectancy` are
    read straight off the old evidence. Sorting those together put candidates that never paid
    the real fee above candidates that did, and the surface said so in a printed warning while
    the ranking underneath went on mixing them — the same shape as the stored-verdict bug
    below, which was also fixed by ordering on the property rather than describing it.

    The DEPTH tier sits after the verdict rather than before it, which is the opposite of
    where the cost tier sits, and the asymmetry is the point. A cheap cost basis FLATTERS the
    numbers beside it, so it has to lead or the flattery survives the sort. A shallow window
    does the reverse — it depresses the very verdict the row is already being sorted by — so
    leading with it would charge one row twice for a single shortfall. As a tiebreak WITHIN a
    verdict tier it says the one thing left to say: same verdict, more market behind it. It
    ranks above ``edge_quality`` for the reason the tier exists at all — a win rate over 12
    trades and one over 120 are not the same measurement, and the sort should not pretend
    they are."""
    by_cid: dict[str, dict[str, Any]] = {}
    for record in records:
        cid = candidate_id(record)
        by_cid[cid] = {**record, "candidate_id": cid}

    def _key(record: Mapping[str, Any]) -> tuple[int, int, int, float, float, str]:
        q = candidate_quality(record)
        return (q["cost_basis_rank"], q["verdict_rank"], q["evidence_depth_rank"],
                -q["edge_quality"], -q["expectancy"], str(record["candidate_id"]))

    return sorted(by_cid.values(), key=_key)


# How many promotable lineages may wait before the daily board says so. Mirrors the
# proposer's unreviewed-family cap (M4b) in intent and differs in effect: that cap makes
# a fire SKIP, this one only speaks. Nothing here refuses anything — the promotion door
# stays exactly as manual as it was.
PROMOTION_BACKLOG_ALERT_THRESHOLD = 5


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
    """
    records = candidates if candidates is not None else read_candidates(root)
    pool_doc = active_pool if active_pool is not None else load_active_pool(root)
    active_entries = pool_doc.get("active_strategies") or []
    active_hashes = {entry.get("strategy_rule_hash") for entry in active_entries}
    seen_lineages: set[tuple[Any, ...]] = {
        _lineage_key(entry.get("strategy_spec") or {}) for entry in active_entries
    }

    candidate_ids: list[str] = []
    for record in rank_candidates(list(records)):
        if record.get("strategy_rule_hash") in active_hashes:
            continue
        quality = candidate_quality(record)
        if quality["cost_basis_rank"] not in PROMOTABLE_COST_BASIS_RANKS:
            continue
        # The same rule for the other axis the door refuses on. It was missing here for seven
        # minutes' worth of merge ordering — the depth gate landed just after this counter —
        # and the omission is the exact failure the line above is written to prevent: a row the
        # ask will refuse must not be advertised as work an operator could do. Latent when
        # found (no row passed every other filter AND failed this one) but not hypothetical:
        # the store holds 41 rows the depth gate refuses, and one of them becoming ROBUST is a
        # matter of time rather than of possibility.
        if quality["evidence_depth_rank"] not in PROMOTABLE_EVIDENCE_DEPTH_RANKS:
            continue
        if quality["verdict"] != ROBUST or quality["holdout_status"] != HOLDOUT_CONFIRMED:
            continue
        expectancy = quality["expectancy_at_current_costs"]
        if not isinstance(expectancy, (int, float)) or expectancy <= 0:
            continue
        lineage = _lineage_key(record.get("strategy_spec") or {})
        if lineage in seen_lineages:
            continue
        seen_lineages.add(lineage)
        candidate_ids.append(candidate_id(record))

    return {
        "count": len(candidate_ids),
        "threshold": PROMOTION_BACKLOG_ALERT_THRESHOLD,
        "candidate_ids": candidate_ids,
    }
