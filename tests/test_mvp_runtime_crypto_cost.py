"""C12 cost model tests — fee/slippage decomposition, and its wiring into the factory.

The R-space formula in ``cost.py`` was derived algebraically from the source's
qty-based ``settle_trade`` (quantity cancels out of every R ratio since
``risk_amount = qty * risk_per_unit``) and verified numerically against the real
source module before porting. These tests re-verify: the decomposition is internally
consistent (net = gross - fee - slippage), fees/slippage are always a cost (never
free money), and the factory backtest now scores costed R while never touching the
live paper kernel."""

from __future__ import annotations

import math

import pytest

from runtime.mvp_runtime.crypto import market_data
from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.crypto.cost import (
    COST_MODEL_UNMEASURED,
    COST_MODEL_VENUE_UNKNOWN,
    DEFAULT_FUNDING_BPS_PER_INTERVAL,
    VENUE_COST_DECLARATIONS,
    cost_model_for,
    worst_case_carry_r,
    FUNDING_SOURCE_FALLBACK,
    FUNDING_SOURCE_PARTIAL,
    FUNDING_SOURCE_VENUE,
    CostModel,
    apply_cost_model,
)
from runtime.mvp_runtime.crypto.factory import backtest_spec
from runtime.mvp_runtime.crypto.paper import settle_trade_plan
from runtime.mvp_runtime.crypto.strategy import StrategySpec

# Reference values computed by RUNNING the source's qty-based settle_trade
# (crypto_AI_System/backtesting/cost_model.py) at the default 2.5bps fee / 3.0bps
# slippage, for entry=100, exit=108 (LONG) and entry=100, exit=104 (SHORT), risk=4.
_SOURCE_LONG = {"net_r": 1.97140015, "fee_cost_r": 0.01299985, "slippage_cost_r": 0.01560000}
_SOURCE_SHORT = {"net_r": -1.02805008, "fee_cost_r": 0.01275007, "slippage_cost_r": 0.01530000}

# The source's rates, passed EXPLICITLY. The module default is now the venue's measured
# 5.0 bps taker rather than the ported 2.5, and these two tests are about the algebra
# matching the source implementation — a property of the formula, not of the rate it is fed.
# Pinning them to the default would have made a deliberate rate change look like a port
# regression, and left the parity claim untestable at any other rate.
_SOURCE_RATES = CostModel(taker_fee_bps=2.5, slippage_bps=3.0)


def _close(a: float, b: float, tol: float = 1e-6) -> bool:
    return math.isclose(a, b, abs_tol=tol)


def test_long_matches_source_settle_trade_reference():
    result = apply_cost_model("LONG", 100.0, 108.0, 4.0, cost=_SOURCE_RATES)
    assert _close(result.net_r, _SOURCE_LONG["net_r"])
    assert _close(result.fee_cost_r, _SOURCE_LONG["fee_cost_r"])
    assert _close(result.slippage_cost_r, _SOURCE_LONG["slippage_cost_r"])


def test_short_matches_source_settle_trade_reference():
    result = apply_cost_model("SHORT", 100.0, 104.0, 4.0, cost=_SOURCE_RATES)
    assert _close(result.net_r, _SOURCE_SHORT["net_r"], tol=1e-5)
    assert _close(result.fee_cost_r, _SOURCE_SHORT["fee_cost_r"])
    assert _close(result.slippage_cost_r, _SOURCE_SHORT["slippage_cost_r"])


def test_net_equals_gross_minus_costs():
    result = apply_cost_model("LONG", 100.0, 110.0, 5.0)
    assert math.isclose(result.net_r, result.gross_r - result.fee_cost_r - result.slippage_cost_r, abs_tol=1e-9)


def test_costs_are_always_nonnegative_never_free_money():
    for direction, entry, exit_ in (("LONG", 100.0, 110.0), ("SHORT", 100.0, 90.0),
                                    ("LONG", 100.0, 90.0), ("SHORT", 100.0, 110.0)):
        result = apply_cost_model(direction, entry, exit_, risk=5.0)
        assert result.fee_cost_r > 0  # a fee is charged on every fill, every direction
        assert result.slippage_cost_r > 0  # a taker is always adverse, every direction


def test_gross_r_matches_uncosted_distance_formula():
    # gross_R must equal exactly what settle_trade_plan already computes with no
    # cost model at all — the "intended price" R this port used before C12.
    result = apply_cost_model("LONG", 100.0, 108.0, 4.0, cost=_SOURCE_RATES)
    assert result.gross_r == 2.0  # (108-100)/4
    result_short = apply_cost_model("SHORT", 100.0, 104.0, 4.0)
    assert result_short.gross_r == -1.0  # (100-104)/4


def test_custom_cost_model_scales_linearly():
    zero_cost = CostModel(taker_fee_bps=0.0, slippage_bps=0.0)
    result = apply_cost_model("LONG", 100.0, 108.0, 4.0, cost=zero_cost)
    assert result.fee_cost_r == 0.0 and result.slippage_cost_r == 0.0
    assert result.net_r == result.gross_r  # no cost -> net equals gross exactly


def test_non_positive_risk_is_the_defensive_zero_case():
    result = apply_cost_model("LONG", 100.0, 108.0, 0.0)
    assert result == apply_cost_model("LONG", 100.0, 108.0, -1.0)
    assert (result.gross_r, result.net_r, result.fee_cost_r, result.slippage_cost_r) == (0.0, 0.0, 0.0, 0.0)


# --- factory wiring -------------------------------------------------------------

def _spec_dict(**overrides):
    base = {
        "schema_version": "strategy_spec.v1", "strategy_id": "S1", "strategy_version": "1.0",
        "strategy_family": "breakout", "symbol_scope": ["BTCUSDT"], "timeframe": "1d",
        "direction": "long", "entry_rules": {"operator": "AND",
                                              "conditions": [{"feature": "close", "comparison": ">", "value": 0.0}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    base.update(overrides)
    return base


def _trending_snapshot(n=200):
    """The bare form, deliberately: these tests are about what an UNSOURCED carry costs.

    The base fixture carries a funding series by default since 2026-08-05 — an absent feed no
    longer fabricates `funding_rate = 0.0`, so a bare snapshot stopped being able to mint the
    `funding_fade_*` half of its rotation block. Nothing about that is a claim on the cost
    model, and `FUNDING_SOURCE_FALLBACK` is exactly what several tests below pin.
    """
    from tests.test_mvp_runtime_crypto_factory import _trending_snapshot as base

    return base(n, funding=False)


def test_backtest_spec_result_r_is_costed_below_gross():
    spec = StrategySpec.from_dict(_spec_dict())
    evidence = backtest_spec(spec, _trending_snapshot())
    assert evidence["closed_count"] > 0
    assert evidence["cost_summary"]["total_fee_cost_r"] > 0
    assert evidence["cost_summary"]["total_slippage_cost_r"] > 0
    # Costed expectancy must be strictly less than what the old (gross) numbers would
    # have shown — costs are a drag, never a credit.
    assert evidence["cost_summary"]["total_net_r"] < (
        evidence["cost_summary"]["total_net_r"]
        + evidence["cost_summary"]["total_fee_cost_r"]
        + evidence["cost_summary"]["total_slippage_cost_r"]
    )


def test_backtest_spec_feeds_real_cost_robustness_not_always_zero():
    spec = StrategySpec.from_dict(_spec_dict())
    evidence = backtest_spec(spec, _trending_snapshot())
    if evidence["cost_summary"]["total_net_r"] > 0:
        # A profitable-net backtest must show SOME cost_robustness credit now —
        # before C12 this component was hard-coded to 0.0 for every candidate.
        assert evidence["robustness"]["components"]["cost_robustness"] > 0.0


def test_backtest_spec_is_still_deterministic_with_costs():
    spec = StrategySpec.from_dict(_spec_dict())
    snapshot = _trending_snapshot()
    a = backtest_spec(spec, snapshot)
    b = backtest_spec(spec, snapshot)
    assert a == b


def test_live_paper_kernel_is_unaffected_by_the_cost_model():
    # The source boundary, re-verified: paper.settle_trade_plan takes no cost
    # model and its signature is unchanged by C12 — live paper stays cost-free.
    position = {"direction": "LONG", "entry_price": 100.0, "stop_loss": 96.0,
               "take_profit": 108.0, "risk": 4.0, "holding_candles": 0}
    reason, exit_price, result_r = settle_trade_plan(
        position, {"high": 109.0, "low": 99.0, "close": 108.5}, 108.5, 48, False
    )
    assert reason == "take_profit" and exit_price == 108.0 and result_r == 2.0  # uncosted, exact


# --- the maker take-profit exit (2026-07-28) -------------------------------------

def test_a_target_exit_pays_the_maker_rate_and_no_slippage():
    """`live_leg` rests the target as a maker LIMIT, so it fills AT the target price. Charging
    it taker plus adverse slippage would describe an order the venue is no longer sent."""
    args = ("LONG", 100.0, 108.0, 4.0)
    cost = CostModel(taker_fee_bps=5.0, slippage_bps=3.0, maker_fee_bps=2.0)
    maker = apply_cost_model(*args, cost=cost, close_reason="take_profit")
    taker = apply_cost_model(*args, cost=cost, close_reason="stop_loss")

    # The exit leg carries no adverse slippage, so only the entry's remains.
    entry_only_slippage = apply_cost_model(
        *args, cost=CostModel(taker_fee_bps=5.0, slippage_bps=3.0, maker_fee_bps=2.0),
        close_reason="take_profit",
    ).slippage_cost_r
    assert maker.slippage_cost_r == entry_only_slippage
    assert maker.slippage_cost_r < taker.slippage_cost_r
    assert maker.fee_cost_r < taker.fee_cost_r
    assert maker.net_r > taker.net_r
    # Same gross either way: the cost model never moves the intended-price result.
    assert maker.gross_r == taker.gross_r


def test_only_a_target_exit_is_charged_as_a_maker():
    """A stop and a time exit both leave at market. Only the target rests."""
    args = ("LONG", 100.0, 108.0, 4.0)
    for reason in ("stop_loss", "time_exit", "manual_exit", None):
        assert apply_cost_model(*args, close_reason=reason).maker_fee_cost_r == 0.0
    assert apply_cost_model(*args, close_reason="take_profit").maker_fee_cost_r > 0.0


def test_an_unknown_close_reason_is_charged_the_pessimistic_way():
    """A cost model that got cheaper when told nothing would be the wrong way round."""
    args = ("LONG", 100.0, 108.0, 4.0)
    assert apply_cost_model(*args, close_reason="something_new") == apply_cost_model(
        *args, close_reason="stop_loss"
    )


def test_the_maker_share_is_a_share_of_the_total_fee_not_an_extra():
    """`expectancy_at` subtracts this from `fee_cost_r` to isolate the taker portion, so it has
    to be contained in it — an additive figure would corrupt every rescale."""
    breakdown = apply_cost_model("LONG", 100.0, 108.0, 4.0, close_reason="take_profit")
    assert 0.0 < breakdown.maker_fee_cost_r < breakdown.fee_cost_r


def test_net_is_still_gross_minus_fees_and_slippage_on_a_maker_exit():
    """The decomposition invariant the whole module rests on, re-checked on the new branch."""
    b = apply_cost_model("SHORT", 100.0, 92.0, 4.0, close_reason="take_profit")
    assert math.isclose(b.net_r, b.gross_r - b.fee_cost_r - b.slippage_cost_r, abs_tol=1e-8)


# --- funding: the cost a perpetual charges for TIME (2026-07-29) ------------------------------
#
# Until this section existed the model charged fees and slippage and nothing else, which prices
# a perpetual as though holding it were free. `_EXIT_PARAMS` lets a 1d spec hold 12-48 days —
# 36 to 144 settlements at ~1 bp each against a modelled ~10 bps of fees. These pin the three
# properties that make the term trustworthy: it is signed, it is linear in the rates, and it is
# the only cost here that can pay the trader.

def test_funding_is_a_cost_to_a_long_and_a_credit_to_a_short():
    """The asymmetry fees do not have, and the reason the factory could not rank long and short
    specs on one scale before this. A positive rate means longs pay shorts."""
    rates = 0.0001 * 30  # thirty settlements at the venue's 1 bp base rate
    long = apply_cost_model("LONG", 100.0, 108.0, 4.0, funding_rate_sum=rates)
    short = apply_cost_model("SHORT", 100.0, 92.0, 4.0, funding_rate_sum=rates)
    assert long.funding_cost_r > 0.0
    assert short.funding_cost_r < 0.0
    # Equal and opposite to within the slippage the two entries paid — NOT exactly, because the
    # carry is charged on the ENTRY FILL and a long buys above the mid while a short sells below.
    # The notional actually opened differs by twice the slippage, so the carry does too. Pinned
    # loosely on purpose: exact antisymmetry here would mean the model had gone back to costing
    # a mid it never traded at.
    assert math.isclose(long.funding_cost_r, -short.funding_cost_r, rel_tol=1e-3)
    assert long.funding_cost_r > -short.funding_cost_r, "a long's entry fill is the higher one"


def test_the_carry_is_charged_on_the_fill_not_the_mid():
    """Stated as its own case because the asymmetry above is easy to read as a sign bug."""
    free = CostModel(taker_fee_bps=0.0, slippage_bps=0.0, maker_fee_bps=0.0)
    long = apply_cost_model("LONG", 100.0, 108.0, 4.0, cost=free, funding_rate_sum=0.003)
    short = apply_cost_model("SHORT", 100.0, 92.0, 4.0, cost=free, funding_rate_sum=0.003)
    # With no slippage both entries fill at the mid, and the two are exactly opposite.
    assert math.isclose(long.funding_cost_r, -short.funding_cost_r, abs_tol=1e-9)


def test_a_negative_funding_regime_reverses_it():
    """Funding rates go negative in a bearish basis, and a model that could only ever charge
    would be describing a different instrument."""
    assert apply_cost_model("LONG", 100.0, 108.0, 4.0, funding_rate_sum=-0.001).funding_cost_r < 0
    assert apply_cost_model("SHORT", 100.0, 92.0, 4.0, funding_rate_sum=-0.001).funding_cost_r > 0


def test_net_is_gross_minus_fees_slippage_and_funding():
    """The decomposition invariant, extended. Funding subtracts like every other term — the
    only difference is that its sign is not fixed."""
    for direction, exit_price, rates in (("LONG", 108.0, 0.002), ("SHORT", 92.0, 0.002),
                                         ("LONG", 108.0, -0.002)):
        b = apply_cost_model(direction, 100.0, exit_price, 4.0, funding_rate_sum=rates)
        assert math.isclose(
            b.net_r, b.gross_r - b.fee_cost_r - b.slippage_cost_r - b.funding_cost_r, abs_tol=1e-8
        )


def test_funding_scales_with_the_holding_window():
    """Linear in the summed rates, which is what makes summing the settlements first exact
    rather than an approximation."""
    one = apply_cost_model("LONG", 100.0, 108.0, 4.0, funding_rate_sum=0.0001).funding_cost_r
    ten = apply_cost_model("LONG", 100.0, 108.0, 4.0, funding_rate_sum=0.001).funding_cost_r
    assert math.isclose(ten, one * 10, rel_tol=1e-6)


def test_a_trade_that_crosses_no_settlement_is_charged_nothing():
    assert apply_cost_model("LONG", 100.0, 108.0, 4.0, funding_rate_sum=0.0).funding_cost_r == 0.0


def test_the_carry_dominates_the_fee_legs_on_a_multi_week_hold():
    """The measurement that motivated the whole change, as an assertion rather than a comment.

    A 24-day hold at the venue's base rate is 72 bps of carry against ~10 bps of fees and
    slippage. Against a book measured at +0.08R with a 95% interval of [-0.32, +0.48], that is
    not a refinement — so if this ever stops being true, the sizing of the omission should be
    re-argued rather than quietly inherited."""
    entry, risk = 60000.0, 60000.0 * 0.03      # stop at ~3% of price, the 1d ATR case
    carry = 24 * 3 * 0.0001                    # 24 days x 3 settlements x 1 bp
    b = apply_cost_model("LONG", entry, entry * 1.05, risk, close_reason="take_profit",
                         funding_rate_sum=carry)
    assert b.funding_cost_r > 5 * (b.fee_cost_r + b.slippage_cost_r)
    assert b.funding_cost_r > 0.2, "the omitted term is R-material, not a rounding difference"


def test_a_zero_risk_position_is_still_all_zeros():
    """The source's division guard, on the new branch too."""
    b = apply_cost_model("LONG", 100.0, 108.0, 0.0, funding_rate_sum=0.01)
    assert b.funding_cost_r == 0.0 and b.net_r == 0.0


# --- the per-bar carry series -----------------------------------------------------------------
#
# `funding_charges_per_bar` is where the venue's real settlements meet the replay's bars. It sums
# rather than carries-forward, which is the one place it differs from `features._asof_align`: a
# feature asks "what is the rate now", a cost asks "what was charged while I held".

def _bars(opens: list[str]) -> list[dict]:
    return [{"open_time": t, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
             "volume": 1.0, "close_time": t} for t in opens]


def _events(pairs) -> list[dict]:
    return [{"timestamp": t, "funding_rate": r} for t, r in pairs]


def test_a_bar_sums_every_settlement_inside_it():
    """Three 8h settlements land in one 1d bar. Carrying the LAST one forward — the feature
    rule — would charge a third of the day."""
    from runtime.mvp_runtime.crypto.factory import funding_charges_per_bar

    charges, source = funding_charges_per_bar(
        _bars(["2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z"]),
        _events([("2026-07-01T00:00:00Z", 0.0001), ("2026-07-01T08:00:00Z", 0.0002),
                 ("2026-07-01T16:00:00Z", 0.0003), ("2026-07-02T00:00:00Z", 0.0004)]),
        timeframe="1d", cost=CostModel(),
    )
    assert source == FUNDING_SOURCE_VENUE
    assert math.isclose(charges[0], 0.0006)   # the three inside 07-01
    assert math.isclose(charges[1], 0.0004)   # the one at the 07-02 open


def test_a_settlement_at_a_bar_open_belongs_to_that_bar():
    """Half-open bars, so `charges[entry+1:exit+1]` bills exactly the intervals a position
    opened at bar `entry`'s CLOSE actually sat through — no double count at a boundary."""
    from runtime.mvp_runtime.crypto.factory import funding_charges_per_bar

    # The leading event only puts bar 0 inside the series' coverage, so this stays a test about
    # the boundary rather than about the partial-coverage fallback below.
    charges, _ = funding_charges_per_bar(
        _bars(["2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z", "2026-07-03T00:00:00Z"]),
        _events([("2026-06-30T16:00:00Z", 0.0), ("2026-07-02T00:00:00Z", 0.0005)]),
        timeframe="1d", cost=CostModel(),
    )
    assert charges == [0.0, 0.0005, 0.0]
    assert math.isclose(sum(charges), 0.0005), "a settlement is billed once, to one bar"


def test_bars_before_the_series_starts_are_charged_the_modelled_rate():
    """**They used to be free, and a deeper replay window is what makes that expensive.**
    A series that starts inside the window leaves the earlier bars UNMEASURED, and the
    bucketing scores unmeasured as zero — free carry on exactly the deep history a longer
    window is bought for. `market_data.DERIVATIVE_HISTORY_DAYS` is 520, so a 1500-day window
    would charge nothing on roughly two thirds of its bars.

    Same number the empty-series case charges, and the label says the difference: a partial
    series measured part of the window and is silent about the rest."""
    from runtime.mvp_runtime.crypto.factory import funding_charges_per_bar

    charges, source = funding_charges_per_bar(
        _bars(["2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z", "2026-07-03T00:00:00Z"]),
        _events([("2026-07-03T00:00:00Z", 0.0005)]),
        timeframe="1d", cost=CostModel(),
    )
    assert source == FUNDING_SOURCE_PARTIAL
    assert charges[0] > 0.0 and charges[1] > 0.0, "uncovered bars are not free"
    assert charges[0] == charges[1], "the modelled rate, not a per-bar guess"
    assert math.isclose(charges[2], 0.0005), "covered bars still come from the venue"

    modelled, _ = funding_charges_per_bar(
        _bars(["2026-07-01T00:00:00Z"]), None, timeframe="1d", cost=CostModel(),
    )
    assert math.isclose(charges[0], modelled[0]), "one fallback rate, not two"


def test_a_fully_covered_series_is_not_labelled_partial():
    """The label has to be able to say no, or it says nothing. Today's 500-day window sits
    inside the ~533 days `DEFAULT_FUNDING_RECORDS` buys, so every candidate minted now must
    still read `venue_history` — this guard is latent until the window is deepened."""
    from runtime.mvp_runtime.crypto.factory import funding_charges_per_bar

    _, source = funding_charges_per_bar(
        _bars(["2026-07-02T00:00:00Z", "2026-07-03T00:00:00Z"]),
        _events([("2026-07-01T00:00:00Z", 0.0001), ("2026-07-02T08:00:00Z", 0.0002)]),
        timeframe="1d", cost=CostModel(),
    )
    assert source == FUNDING_SOURCE_VENUE


def test_a_missing_series_falls_back_to_the_base_rate_not_to_zero():
    """The direction that matters. A missing series means UNMEASURED, and charging nothing for
    it is exactly the omission this whole change closes."""
    from runtime.mvp_runtime.crypto.factory import funding_charges_per_bar

    charges, source = funding_charges_per_bar(
        _bars(["2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z"]), None,
        timeframe="1d", cost=CostModel(),
    )
    assert source == FUNDING_SOURCE_FALLBACK
    assert all(c > 0.0 for c in charges)
    # One day is three settlements at the base rate.
    assert math.isclose(charges[0], 3 * DEFAULT_FUNDING_BPS_PER_INTERVAL / 10000.0)


def test_the_fallback_prices_a_fast_timeframe_as_a_fraction_of_an_interval():
    """A 15m bar sits through 1/32 of an 8h interval. Charging a whole one per bar would price
    a scalper like a swing trader — and 15m is the deepest factory window (48k bars)."""
    from runtime.mvp_runtime.crypto.factory import funding_charges_per_bar

    fast, _ = funding_charges_per_bar(_bars(["2026-07-01T00:00:00Z"]), [],
                                      timeframe="15m", cost=CostModel())
    slow, _ = funding_charges_per_bar(_bars(["2026-07-01T00:00:00Z"]), [],
                                      timeframe="1d", cost=CostModel())
    assert fast[0] < slow[0]
    assert math.isclose(slow[0] / fast[0], 96.0)   # 1440 minutes / 15


# --- the carry reaching the evidence ----------------------------------------------------------

def _funded_snapshot(rate=0.0001):
    """The trending snapshot with the venue's own settlements attached — three per day, which is
    the cadence `/fapi/v1/fundingRate` actually returns and the cycle already collects."""
    from datetime import timedelta

    from runtime.mvp_runtime import timeutil

    snapshot = dict(_trending_snapshot())
    candles = snapshot["candles"]
    first = timeutil.parse_iso(candles[0]["open_time"])
    last = timeutil.parse_iso(candles[-1]["open_time"])
    events, at = [], first
    while at <= last:
        events.append({"timestamp": timeutil.format_iso(at), "funding_rate": rate})
        at += timedelta(hours=8)
    snapshot["funding"] = events
    return snapshot


def test_the_backtest_charges_the_venues_own_settlements_when_it_has_them():
    """The whole point of charging real history rather than a constant: the series is already
    fetched every cycle for `funding_rate`/`funding_zscore`, so inventing a number would mean
    ignoring one the repo has."""
    spec = StrategySpec.from_dict(_spec_dict())
    evidence = backtest_spec(spec, _funded_snapshot())
    model = evidence["cost_summary"]["cost_model"]
    assert model["funding_source"] == FUNDING_SOURCE_VENUE
    assert evidence["cost_summary"]["total_funding_cost_r"] > 0.0


def test_a_long_book_scores_lower_once_the_carry_is_charged():
    """The measurement that motivates the change, end to end: the SAME spec on the SAME bars,
    with and without the carry the venue actually charges."""
    spec = StrategySpec.from_dict(_spec_dict())
    free = CostModel(funding_bps_per_interval=0.0)
    charged = backtest_spec(spec, _funded_snapshot())
    uncharged = backtest_spec(spec, {**_trending_snapshot(), "funding": []}, cost=free)
    assert charged["closed_count"] == uncharged["closed_count"], "same trades, different costing"
    assert charged["expectancy"] < uncharged["expectancy"]


def test_a_short_book_is_paid_to_hold_in_a_positive_regime():
    """The direction fees cannot express. If this ever reads as a cost, the sign is wrong and
    the factory is ranking shorts as though they paid what longs pay."""
    spec = StrategySpec.from_dict(_spec_dict(direction="short"))
    evidence = backtest_spec(spec, _funded_snapshot())
    if evidence["closed_count"]:
        assert evidence["cost_summary"]["total_funding_cost_r"] < 0.0


def test_the_holdout_is_costed_on_the_same_basis_as_the_scored_window():
    """A holdout charged differently from the window it confirms proves nothing about it —
    the same reason `_replay` is shared rather than reimplemented."""
    spec = StrategySpec.from_dict(_spec_dict())
    evidence = backtest_spec(spec, _funded_snapshot())
    assert "funding_cost_r" in evidence["holdout"]
    assert evidence["holdout"]["cost_model"]["funding_source"] == (
        evidence["cost_summary"]["cost_model"]["funding_source"]
    )
    assert evidence["holdout"]["cost_model"]["funding_bps_per_interval"] == (
        evidence["cost_summary"]["cost_model"]["funding_bps_per_interval"]
    )


def test_a_backtest_over_a_snapshot_with_no_funding_says_it_fell_back():
    """Evidence has to state which quality it is. "Charged the venue's settlements" and
    "charged a constant because there were none" are not the same claim."""
    spec = StrategySpec.from_dict(_spec_dict())
    evidence = backtest_spec(spec, _trending_snapshot())
    assert evidence["cost_summary"]["cost_model"]["funding_source"] == FUNDING_SOURCE_FALLBACK
    assert evidence["cost_summary"]["total_funding_cost_r"] > 0.0, "never silently free"


def test_charging_the_carry_is_still_deterministic():
    spec = StrategySpec.from_dict(_spec_dict())
    snapshot = _funded_snapshot()
    assert backtest_spec(spec, snapshot) == backtest_spec(spec, snapshot)


# --- venue-scoped cost: S2(a), and the refusals that are the point of it ----------------------

def test_binance_is_exactly_what_it_was_before_the_registry_existed():
    """The registry must not move the venue every existing number was measured on."""
    assert cost_model_for(market_data.BINANCE_FUTURES) == CostModel()


def test_an_unmeasured_venue_refuses_instead_of_inheriting_binances_rates():
    """The failure this exists to prevent is invisible: a net R computed at the wrong taker
    rate looks exactly like one computed at the right one.

    Hyperliquid's base taker/maker are volume-tiered and `userFees` answers only for a real
    address, so they need an account on that venue. Until then the model does not exist.
    """
    with pytest.raises(ToolError) as exc:
        cost_model_for(market_data.HYPERLIQUID)
    assert exc.value.reason_code == COST_MODEL_UNMEASURED
    # It names WHICH fields, so filling them is a lookup rather than a re-investigation.
    assert "taker_fee_bps" in str(exc.value)


def test_an_undeclared_venue_refuses_too():
    with pytest.raises(ToolError) as exc:
        cost_model_for("some_new_dex")
    assert exc.value.reason_code == COST_MODEL_VENUE_UNKNOWN


def test_what_was_measured_is_kept_even_though_no_model_can_be_built_from_it():
    """The point of a declaration over a bare absence: 24 settlements a day and the 0.5
    multiplier were measured against the venue, and losing them would mean measuring again."""
    declared = VENUE_COST_DECLARATIONS[market_data.HYPERLIQUID]
    assert declared.funding_intervals_per_day == 24        # against Binance's 3
    assert declared.funding_multiplier == 0.5
    assert declared.deployer_fee_scale == 1.0


def test_the_settlement_cadence_now_rides_on_the_model():
    """It was a module constant because there was one venue. A carry computed from a fixed 3
    on a venue that settles 24 times a day is wrong by 8x, and quietly."""
    entry, risk = 100.0, 1.0
    slow = worst_case_carry_r("LONG", entry, risk, timeframe="1h", max_holding_bars=24,
                              cost=CostModel())
    fast = worst_case_carry_r("LONG", entry, risk, timeframe="1h", max_holding_bars=24,
                              cost=CostModel(funding_intervals_per_day=24))
    assert slow is not None and fast is not None
    assert fast == pytest.approx(slow * 8.0)
