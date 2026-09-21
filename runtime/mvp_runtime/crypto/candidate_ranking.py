"""Candidate ranking: how good one candidate's evidence is, and the order to consider them in
(crypto PR7e-1).

`candidate_quality` is the ranking view of one candidate. It recomputes the robustness verdict and the
holdout status from the stored components, reads the realized win rate and reward:risk, measures how
far the expectancy sits from zero and how much of that is selection given how many candidates were
scored against the same bars, and places the row on two comparability tiers: what it PAID
(`cost_basis_rank`, its cost model against the one the venue charges today) and how much market it
was SHOWN (`evidence_depth_rank`, its replayed window against the one the factory collects today).
`rank_candidates` orders the store for the promotion decision on those, and `expectancy_at`
re-derives a row's expectancy at other fee rates, exactly.

This is judging, not holding: it reads candidate records and the cost model and returns a view. It
keeps no state and refuses nothing. It lived in `pool`, beside the store and its doors, which put
`forward_confirmation`'s read of the recomputed holdout status upward into the decision layer. The
doors that turn a tier into a refusal (`assert_promotable_cost_basis`,
`assert_promotable_evidence_depth`) and the sets they refuse on stay in `pool`, which re-exports, as
the same objects, the names its callers read here.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from . import market_data
from .candidate_identity import candidate_id
from .cost import (
    DEFAULT_FUNDING_BPS_PER_INTERVAL,
    DEFAULT_MAKER_FEE_BPS,
    DEFAULT_SLIPPAGE_BPS,
    DEFAULT_STOP_SLIPPAGE_BPS,
    DEFAULT_TAKER_FEE_BPS,
    FUNDING_SOURCE_UNCHARGED,
    FUNDING_SOURCE_VENUE,
)
from .robustness import (
    classify_verdict, expectancy_t, holdout_status, selection_adjusted_z, selection_rank, verdict_rank,
)

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


# Also read by `pool`: `days_to_lifecycle_window` decides the lifecycle window with it, so an edit
# here moves that too.
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
    # Present-only, the maker rule: a record scored before the stop split keeps the exact
    # basis string it has always reported, and a stamped one says what its stops paid —
    # which is the axis on which `cost_basis_rank` now ranks a row OPTIMISTIC, and the promotion
    # door refuses it.
    stop = model.get("stop_slippage_bps")
    stop_term = f"+stop_{stop}bps" if isinstance(stop, (int, float)) else ""
    funding = model.get("funding_bps_per_interval")
    if isinstance(funding, (int, float)) and not isinstance(funding, bool):
        source = model.get("funding_source") or FUNDING_SOURCE_VENUE
        funding_term = f"+funding_{funding}bps/8h({source})"
    else:
        funding_term = f"+funding_{FUNDING_SOURCE_UNCHARGED}"
    return f"{EDGE_COST_BASIS_NET}:taker_{taker}bps{maker_term}+slip_{slip}bps{stop_term}{funding_term}"


def current_cost_basis() -> str:
    """The basis a candidate minted right now would carry.

    Formatted by `cost_basis_of` over a synthetic record rather than by a second format
    string, so the "what the store holds" and "what the model charges" sides cannot drift
    into two spellings of the same rates."""
    return cost_basis_of({"backtest_evidence": {"cost_summary": {"cost_model": {
        "taker_fee_bps": DEFAULT_TAKER_FEE_BPS,
        "maker_fee_bps": DEFAULT_MAKER_FEE_BPS,
        "slippage_bps": DEFAULT_SLIPPAGE_BPS,
        "stop_slippage_bps": DEFAULT_STOP_SLIPPAGE_BPS,
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
    # The stop leg joined the comparison on Thomas's 2026-08-11 direction, the same evening
    # the seam re-priced 3.0 -> 12.0: a row scored with cheaper stops overstates its edge on
    # every stop exit exactly the way a cheaper taker did, and the safest treatment of the
    # existing store is NOT a forced re-price but this rank — cheaper-than-current reads
    # OPTIMISTIC, is refused at the door, and the lineage re-mints at the current model.
    # A record with no `stop_slippage_bps` charged its stops at its own GENERAL slippage
    # (the pre-#683 identity), so that is the figure it is judged on — the maker rule again:
    # what the leg actually paid, never a missing field read as the current rate.
    stop_charged = model.get("stop_slippage_bps")
    if not _is_number(stop_charged):
        stop_charged = slip
    if (taker == DEFAULT_TAKER_FEE_BPS and maker == DEFAULT_MAKER_FEE_BPS
            and slip == DEFAULT_SLIPPAGE_BPS and stop_charged == DEFAULT_STOP_SLIPPAGE_BPS
            and funding == DEFAULT_FUNDING_BPS_PER_INTERVAL):
        return COST_BASIS_RANK_CURRENT
    # A record with no maker rate charged its exit at the TAKER rate — that model had no maker
    # leg at all, so the honest comparison against today's maker rate is what the exit actually
    # paid, not a missing field treated as zero (which would read every legacy row as optimistic).
    maker_charged = maker if _is_number(maker) else taker
    if (taker >= DEFAULT_TAKER_FEE_BPS and maker_charged >= DEFAULT_MAKER_FEE_BPS
            and slip >= DEFAULT_SLIPPAGE_BPS and stop_charged >= DEFAULT_STOP_SLIPPAGE_BPS
            and funding >= DEFAULT_FUNDING_BPS_PER_INTERVAL):
        return COST_BASIS_RANK_CONSERVATIVE
    return COST_BASIS_RANK_OPTIMISTIC


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
#   - ROBUST additionally requires a holdout of at least `MIN_HOLDOUT_TRADES` closed trades
#     whose mean clears `CONFIDENCE_Z` standard errors, so a thin tail puts the top verdict
#     out of reach however good the edge is — and a wide one does too, which is the point:
#     the tail has to be able to fail.
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
# Which of these tiers may back a promotion, and why a known-shallow row ranks where an unrecorded
# one is refused, is argued beside the door that acts on it (`pool.PROMOTABLE_EVIDENCE_DEPTH_RANKS`).

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
    # Read at call time rather than copied — a second 0.70 here would drift the day the
    # holdout fraction moves, and the two sides would silently stop meaning the same window.
    # Function-local for import weight: the split rule lives beside the replay engine's own
    # constants, and the ranking should not pull the whole miner into its import graph to read
    # one pure function. (Both are strategy; while this lived in the pool it was local by
    # layering too.)
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


# --- candidate ranking (M4a): robustness first-pass, win-rate + reward:risk second -

# A payoff ratio a losing-free backtest can't divide out. It floats an all-wins
# lineage to the top of its robustness tier for the sort only; the displayed
# reward:risk stays honest (None → "∞"), so this cap is never shown as a real ratio.
_ALL_WINS_RR_SORT = float("inf")


# Also read by `pool`: `context_scores` orders the live leg's context visits with it, so an edit here
# moves the routing order too, not only the ranking.
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


def search_context_key(spec: Mapping[str, Any]) -> tuple[Any, ...]:
    """What makes two candidates two ATTEMPTS at the same question: one market, one timeframe.

    Coarser than :func:`pool._lineage_key` on purpose, and the difference is the whole correction.
    A lineage key includes the family, but which of the 20 templates to mint is itself a
    searched degree of freedom — counting attempts per family would divide the multiple-testing
    burden by the very choice that creates it. Two candidates on BTCUSDT 1h were scored against
    the same bars whatever family they came from, so they are two draws from one distribution.
    See ``robustness.SELECTION_CONTEXT``."""
    return (tuple(spec.get("symbol_scope") or ()), spec.get("timeframe"))


def _lattice_attempts(record: Mapping[str, Any]) -> int:
    """How many hypotheses this row charges its context with — its ablation lattice size
    when it carries one, else 1.

    An ablation winner registered alone, but its ``lattice_size - 1`` unregistered siblings
    were each scored against the same bars (``factory.ablate_hypothesis`` replays every
    non-empty subset of the drawn conjunction); uncharged, they would be exactly the escape
    :func:`attempts_by_context`'s docstring forbids — attempts that raise no bar. The
    division of labour is deliberate: ``lattice_size`` is stored FACT (how many members
    were tried, true at mint and forever), while the attempt COUNT stays computed at read
    time here. An absent or unreadable block charges 1 — the pre-ablation meaning every
    stored row already has — and never 0, which would be a row escaping its own charge."""
    evidence = record.get("backtest_evidence")
    if not isinstance(evidence, Mapping):
        return 1
    ablation = evidence.get("ablation")
    if not isinstance(ablation, Mapping):
        return 1
    size = ablation.get("lattice_size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        return 1
    return size


def _evidence_symbols(record: Mapping[str, Any]) -> int:
    """How many symbols this record's evidence was actually scored over, from the record.

    Reads the holdout's ``symbols`` first (stamped by the pooled replay), then the top-level
    ``symbols_replayed``. Returns 1 whenever the evidence does not positively say "pooled" —
    absence must stay the single-symbol reading every pre-F9 row already has."""
    evidence = record.get("backtest_evidence")
    if not isinstance(evidence, Mapping):
        return 1
    holdout = evidence.get("holdout")
    for value in (
        holdout.get("symbols") if isinstance(holdout, Mapping) else None,
        evidence.get("symbols_replayed"),
    ):
        if isinstance(value, int) and not isinstance(value, bool) and value > 1:
            return value
    return 1


def pooled_context_keys(
    records: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, int], tuple[Any, ...]]:
    """The store's multi-symbol context key per ``(timeframe, cohort size)`` — when unambiguous.

    Read off the store rather than off a constant, because the row that needs it cannot name
    its own cohort (that is the defect being compensated). A slot where two different cohorts
    of the same size share a timeframe is dropped: an ambiguous reattribution would move an
    attempt onto bars it may never have been scored against, and the fallback (charge the
    stored key) is the behavior the store already had."""
    keys: dict[tuple[str, int], tuple[Any, ...]] = {}
    ambiguous: set[tuple[str, int]] = set()
    for record in records:
        key = search_context_key(record.get("strategy_spec") or {})
        scope, timeframe = key
        if len(scope) > 1:
            slot = (str(timeframe), len(scope))
            if slot in keys and keys[slot] != key:
                ambiguous.add(slot)
            keys[slot] = key
    return {slot: key for slot, key in keys.items() if slot not in ambiguous}


def attempt_context_key(
    record: Mapping[str, Any], *, pooled_keys: Mapping[tuple[str, int], tuple[Any, ...]],
) -> tuple[Any, ...]:
    """The context whose BARS this record's attempt was scored against — evidence-aware.

    For almost every row this is :func:`search_context_key` unchanged. The exception is the
    F9 topup omission (56 rows, 2026-08-10..17, code fixed by #712): specs minted with a
    single-symbol ``symbol_scope`` whose evidence was scored pooled over the whole cohort.
    An attempt charges the bars it was scored against, so those rows charge — and are judged
    against — their timeframe's pooled context, provided the store can name exactly one
    cohort of that size (``pooled_keys``) and the stored symbol is a member of it. Anything
    less provable falls back to the stored key, which is the pre-existing behavior and the
    conservative direction: the single-symbol keys carry the larger counts today, so a row
    left behind faces the higher bar, never a lower one."""
    key = search_context_key(record.get("strategy_spec") or {})
    scope, timeframe = key
    symbols = _evidence_symbols(record)
    if len(scope) == 1 and symbols > 1:
        pooled = pooled_keys.get((str(timeframe), symbols))
        if pooled is not None and scope[0] in pooled[0]:
            return pooled
    return key


def attempts_by_context(records: Sequence[Mapping[str, Any]]) -> dict[tuple[Any, ...], int]:
    """How many candidates have been scored against each market/timeframe's bars.

    Counted at READ time over the store, never stored on the record, and that is the lesson
    from the holdout label rather than a preference: an attempt count written at mint is wrong
    by every candidate minted after it, and this one only ever grows. The replay window is a
    rolling 500 days, so two generations minted a day apart overlap 99.8% — which is why "the
    same bars" is a property of the context and not of the day.

    Distinct candidates, not rows: the store re-appends a lineage and every append would
    otherwise raise the bar for candidates that never moved.

    A row minted through the ablation lattice counts as its whole lattice
    (:func:`_lattice_attempts`): one winner registered, but every member was scored against
    this context's bars, and the burden follows the scoring, not the storing."""
    seen: set[str] = set()
    distinct: list[Mapping[str, Any]] = []
    for record in records:
        cid = candidate_id(record)
        if cid in seen:
            continue
        seen.add(cid)
        distinct.append(record)
    # Two passes because the reattribution needs the whole population first: which cohort a
    # scope-mismatched row charges is read off the store (`pooled_context_keys`), not off the
    # row, and a single pass would answer differently depending on store order.
    pooled_keys = pooled_context_keys(distinct)
    counts: dict[tuple[Any, ...], int] = {}
    for record in distinct:
        key = attempt_context_key(record, pooled_keys=pooled_keys)
        counts[key] = counts.get(key, 0) + _lattice_attempts(record)
    return counts


def candidate_quality(
    record: Mapping[str, Any], *, attempts: int | None = None
) -> dict[str, Any]:
    """The ranking view of one candidate: robustness tier + realized performance.

    First-pass ``verdict_rank`` (ROBUST < PROVISIONAL < FRAGILE < unknown) never
    changes with performance — the anti-overfit filter stays authoritative. The
    second-pass axes are ``win_rate`` and the realized ``reward_risk`` (avg_win_R /
    avg_loss_R); ``edge_quality = win_rate * reward_risk`` combines them so a lineage
    strong on *both* outranks one strong on either alone. A candidate with no losing
    trades has an undefined ratio (``reward_risk`` None, ``all_wins`` True); one
    predating the realized evidence falls back to the designed target/stop ratio
    (``reward_risk_basis`` ``"designed"``).

    ``attempts`` is how many candidates were scored against this one's bars — the number that
    decides how much of its t-statistic is selection (:func:`attempts_by_context` computes it,
    ``rank_candidates`` injects it). A caller that does not have the store omits it, and every
    candidate then reads ``SELECTION_UNMEASURED``: uniform, so the ordering such a caller sees
    is exactly the one it saw before this existed. Unknown must never be the *cheaper* answer,
    which is why it sorts last rather than falling back to the uncorrected threshold."""
    evidence = record.get("backtest_evidence") or {}
    robustness = evidence.get("robustness") or {}
    # Out-of-sample status rides into the ranking view so the promotion door can show
    # it: with ROBUST now gated on it, "PROVISIONAL because unconfirmed" and
    # "PROVISIONAL because it failed forward" are very different things to promote.
    #
    # RECOMPUTED from the stored block, for the reason spelled out for the verdict below and
    # discovered the same way. `holdout_status` is a label a rule produced at mint time, and
    # the rule changed twice now — most recently when a confirmation stopped being `total_R > 0`
    # on three trades and became an interval over `MIN_HOLDOUT_TRADES`. Reading the label back
    # meant that change reached newly minted candidates only: measured on this machine, all 236
    # stored CONFIRMED labels survived a rule under which 1 of them qualifies, and they gate
    # ROBUST, which gates the live-promotion door. Recomputing the verdict from a stale holdout
    # label fixed half of a two-part staleness and left the half that decides it.
    #
    # A record with no holdout block keeps its stored label — the same rule the verdict follows.
    # Every candidate minted before the holdout existed lands there, and their stored label is
    # UNCONFIRMED, so the fallback grants nothing it should not.
    stored_holdout_state = str(robustness.get("holdout_status") or "UNCONFIRMED")
    holdout_block = evidence.get("holdout")
    holdout_state = (
        holdout_status(holdout_block) if isinstance(holdout_block, Mapping) and holdout_block
        else stored_holdout_state
    )
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
    # How far this candidate's expectancy sits from zero in its own standard errors, and
    # whether that survives being the best of `attempts` tries. Both None/UNMEASURED on
    # evidence minted before `stdev_r` existed — the t is not reconstructed from the payoff
    # legs, which would understate the spread and overstate every t derived from it.
    t_stat = expectancy_t(evidence.get("expectancy"), evidence.get("stdev_r"), closed)
    return {
        "candidate_id": candidate_id(record),
        "verdict": verdict,
        "verdict_rank": verdict_rank(verdict),
        "holdout_status": holdout_state,
        "expectancy_t": round(t_stat, 6) if t_stat is not None else None,
        "attempts_in_context": attempts,
        # The bar this t had to clear given how many candidates it was drawn from. Reported
        # beside the rank so the surface an operator reads can say WHY a believable-looking
        # edge ranks below one with a smaller t on a less-searched context.
        "selection_adjusted_z": (
            round(selection_adjusted_z(int(attempts)), 6) if attempts is not None else None
        ),
        "selection_rank": selection_rank(t_stat, attempts),
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
    they are.

    The SELECTION tier sits directly after the verdict, and it is the only key in this order
    that knows a candidate has siblings. Everything above it judges one row against a standard;
    this judges it against the number of rows tried on the same bars, which is the property
    that made a store of 979 produce a top-ranked candidate with one closed trade. It ranks
    below the verdict because the verdict carries the FRAGILE veto and the out-of-sample
    confirmation — admission-shaped rules — and above depth because a deeper window on an edge
    that is selection is more of the same thing. Evidence with no recorded spread reads
    UNMEASURED and sorts last within its verdict tier: the same rule the verdict itself
    follows, applied to the same kind of absence."""
    by_cid: dict[str, dict[str, Any]] = {}
    for record in records:
        cid = candidate_id(record)
        by_cid[cid] = {**record, "candidate_id": cid}
    attempts = attempts_by_context(list(by_cid.values()))
    # The lookup follows the same evidence-aware key the counting used: a row judged against
    # a key it does not charge would face a bar computed without its own attempt in it.
    pooled_keys = pooled_context_keys(list(by_cid.values()))

    def _key(record: Mapping[str, Any]) -> tuple[int, int, int, int, float, float, str]:
        q = candidate_quality(
            record, attempts=attempts.get(attempt_context_key(record, pooled_keys=pooled_keys))
        )
        return (q["cost_basis_rank"], q["verdict_rank"], q["selection_rank"],
                q["evidence_depth_rank"], -q["edge_quality"], -q["expectancy"],
                str(record["candidate_id"]))

    return sorted(by_cid.values(), key=_key)
