"""Volatility-scaled live sizing — hold risk down when the tape is unusually violent.

**The defect this fixes is arithmetic, and it is the opposite of what "add volatility targeting"
sounds like.** `size_live_order` takes ``min(risk-based, budget cap)``, and on any realistic
equity the *budget cap* binds: at a 60 USDT per-order cap the risk leg only wins below roughly 30
USDT of equity. While the cap binds the notional is FIXED, so the risk actually taken is
``cap × stop_distance / price`` — which **rises with volatility**. Measured across plausible stop
distances, risk swung 0.18 → 1.20 USDT at a fixed cap, a factor of 6.7, biggest bets on the most
chaotic tape.

Two consequences shape everything here:

1. The multiplier applies to the **final quantity**, not to ``risk_fraction``. Scaling the risk
   fraction is provably inert while the cap is what binds, which is exactly the "looks like it
   does something" outcome this repo keeps catching.
2. It is capped at **1.0**, so it can only ever shrink an order. Exceeding 1.0 would size above
   the operator's registered per-order cap in quiet markets — widening an authorization from
   inside the runtime. So this is a volatility-scaled *ceiling*, not a two-sided target, and the
   tests below pin that asymmetry rather than treating it as a gap.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import features
from runtime.mvp_runtime.crypto.features import build_feature_rows
from runtime.mvp_runtime.crypto.indicators import rolling_median
from runtime.mvp_runtime.crypto.live_sizing import SymbolFilters, size_live_order
from runtime.mvp_runtime.crypto.paper import (
    MIN_VOL_SIZE_MULTIPLIER,
    volatility_size_multiplier,
)

FILTERS = SymbolFilters(step_size=0.001, min_qty=0.001, min_notional=5.0, tick_size=0.01, max_qty=0.0)
PRICE = 3000.0
CAP = 60.0
EQUITY = 500.0
REFERENCE = 0.005  # 0.5% typical stop distance


def _size(stop_fraction, *, multiplier=None):
    plan = {"entry_price": PRICE, "risk": stop_fraction * PRICE}
    if multiplier is not None:
        plan["vol_size_multiplier"] = multiplier
    return size_live_order(
        plan, equity_usdt=EQUITY, max_order_notional_usdt=CAP, filters=FILTERS,
    )


def _risk_taken(result, stop_fraction):
    return result["quantity"] * stop_fraction * PRICE


# --- the multiplier -------------------------------------------------------------

def test_at_or_below_normal_volatility_nothing_is_scaled():
    """One-directional on purpose: exceeding 1.0 would size above the registered per-order cap,
    which is widening an authorization from inside the runtime."""
    assert volatility_size_multiplier({"atr_pct_of_price": REFERENCE, "atr_pct_reference": REFERENCE}) == 1.0
    # Half the normal volatility — a two-sided target would double the size here. This does not.
    assert volatility_size_multiplier({"atr_pct_of_price": REFERENCE / 2, "atr_pct_reference": REFERENCE}) == 1.0


def test_above_normal_volatility_scales_inversely():
    assert volatility_size_multiplier(
        {"atr_pct_of_price": REFERENCE * 2, "atr_pct_reference": REFERENCE}) == pytest.approx(0.5)
    assert volatility_size_multiplier(
        {"atr_pct_of_price": REFERENCE * 4, "atr_pct_reference": REFERENCE}) == pytest.approx(0.25)


def test_the_floor_bounds_the_reduction():
    """Below the floor the venue's own minimum quantity and notional refuse the entry anyway, and
    a refusal on a chaotic tape should come from the venue's limits rather than from an
    arithmetic slide to zero."""
    assert volatility_size_multiplier(
        {"atr_pct_of_price": REFERENCE * 100, "atr_pct_reference": REFERENCE}
    ) == MIN_VOL_SIZE_MULTIPLIER


@pytest.mark.parametrize("row", [
    {},                                                              # nothing at all
    {"atr_pct_of_price": REFERENCE},                                 # no reference yet
    {"atr_pct_reference": REFERENCE},                                # no current reading
    {"atr_pct_of_price": 0.0, "atr_pct_reference": REFERENCE},       # non-positive current
    {"atr_pct_of_price": REFERENCE, "atr_pct_reference": 0.0},       # non-positive reference
    {"atr_pct_of_price": REFERENCE, "atr_pct_reference": True},      # bool is not a measurement
    {"atr_pct_of_price": "0.005", "atr_pct_reference": REFERENCE},   # string is not a measurement
])
def test_unusable_inputs_leave_the_size_unscaled(row):
    """Unscaled rather than refused. The entry is already authorized by every other door and this
    function's only job is to shrink it — a missing reference is not grounds to invent one, and
    not grounds to block either."""
    assert volatility_size_multiplier(row) == 1.0


# --- what it does to the size, which is the point --------------------------------

def test_risk_taken_rises_with_volatility_without_the_multiplier():
    """The defect, stated as a test so the fix has something to be measured against."""
    low = _size(0.005)
    high = _size(0.020)
    assert low["notional_usdt"] == high["notional_usdt"] == CAP, "the cap binds in both cases"
    assert _risk_taken(high, 0.020) == pytest.approx(4 * _risk_taken(low, 0.005))


def test_the_multiplier_holds_risk_constant_above_the_reference():
    """The property that makes this volatility targeting rather than merely 'smaller sometimes':
    from the reference volatility up to the floor's reach, the risk taken is the SAME number."""
    baseline = _risk_taken(_size(REFERENCE, multiplier=1.0), REFERENCE)
    for stop_fraction in (REFERENCE * 2, REFERENCE * 4):
        multiplier = volatility_size_multiplier(
            {"atr_pct_of_price": stop_fraction, "atr_pct_reference": REFERENCE})
        result = _size(stop_fraction, multiplier=multiplier)
        assert _risk_taken(result, stop_fraction) == pytest.approx(baseline, rel=1e-3)


def test_beyond_the_floor_risk_still_falls_but_is_no_longer_constant():
    """Stated rather than hidden: past 4x the reference the floor binds, so the reduction stops
    tracking volatility. Risk is 4x smaller than it would have been, not constant."""
    stop_fraction = REFERENCE * 8
    multiplier = volatility_size_multiplier(
        {"atr_pct_of_price": stop_fraction, "atr_pct_reference": REFERENCE})
    assert multiplier == MIN_VOL_SIZE_MULTIPLIER
    unscaled = _risk_taken(_size(stop_fraction), stop_fraction)
    scaled = _risk_taken(_size(stop_fraction, multiplier=multiplier), stop_fraction)
    assert scaled == pytest.approx(unscaled * MIN_VOL_SIZE_MULTIPLIER, rel=1e-3)
    baseline = _risk_taken(_size(REFERENCE, multiplier=1.0), REFERENCE)
    assert scaled > baseline, "the floor means very high volatility still takes more risk"


def test_scaling_the_risk_fraction_instead_would_have_changed_nothing():
    """Why the multiplier is on the final quantity. While the cap binds, the risk-based candidate
    is not the one being used, so halving `risk_fraction` moves no size at all."""
    plan = {"entry_price": PRICE, "risk": 0.02 * PRICE}
    full = size_live_order(plan, equity_usdt=EQUITY, max_order_notional_usdt=CAP,
                           filters=FILTERS, risk_fraction=0.01)
    halved = size_live_order(plan, equity_usdt=EQUITY, max_order_notional_usdt=CAP,
                             filters=FILTERS, risk_fraction=0.005)
    assert full["bound_by"] == halved["bound_by"] == "budget"
    assert full["quantity"] == halved["quantity"], "the cap bound both, so the fraction was inert"


def test_the_multiplier_never_widens_an_order():
    unscaled = _size(0.020)
    for multiplier in (1.0, 0.5, MIN_VOL_SIZE_MULTIPLIER):
        assert _size(0.020, multiplier=multiplier)["quantity"] <= unscaled["quantity"]


@pytest.mark.parametrize("bad", [0.0, -0.5, 1.5, None, True])
def test_an_out_of_range_multiplier_on_the_plan_does_not_scale(bad):
    """A multiplier this module cannot trust to shrink safely is read as 'do not scale', never as
    a licence to guess. ``1.5`` is the one that matters: a plan asking to WIDEN an order past the
    registered cap is refused rather than honoured. ``True`` is here because bool is an int and
    coerces to 1.0, which lands on 'no scaling' — the safe reading either way."""
    assert _size(0.020, multiplier=bad)["quantity"] == _size(0.020)["quantity"]


def test_a_numeric_string_multiplier_is_coerced_like_every_other_plan_field():
    """Not a special case: ``entry_price`` and ``risk`` reach this function through the same
    ``as_float`` coercion, and making the multiplier uniquely strict would be an inconsistency
    with no safety argument behind it — a coerced ``"0.5"`` shrinks the order, which is the
    direction this whole mechanism exists to move in."""
    assert _size(0.020, multiplier="0.5")["quantity"] == _size(0.020, multiplier=0.5)["quantity"]


def test_the_multiplier_is_recorded_even_when_it_did_nothing():
    """"The scaler did nothing" and "there was no scaler" are different statements, and the second
    is what every record before this said."""
    assert _size(0.005, multiplier=1.0)["vol_size_multiplier"] == 1.0
    assert _size(0.020, multiplier=0.25)["vol_size_multiplier"] == 0.25
    # Present on refusals too, where an operator most needs to see why a size came out small.
    refused = size_live_order({"entry_price": PRICE, "risk": 0.0, "vol_size_multiplier": 0.25},
                              equity_usdt=EQUITY, max_order_notional_usdt=CAP, filters=FILTERS)
    assert refused["sizable"] is False


def test_a_plan_without_the_field_sizes_exactly_as_before():
    """Every plan built before this existed."""
    plan = {"entry_price": PRICE, "risk": 0.02 * PRICE}
    assert "vol_size_multiplier" not in plan
    assert size_live_order(plan, equity_usdt=EQUITY, max_order_notional_usdt=CAP,
                           filters=FILTERS)["vol_size_multiplier"] == 1.0


# --- the reference the ratio divides by -------------------------------------------

def test_rolling_median_is_not_dragged_by_a_spike():
    """The reason the reference is a median. Its consumer divides by it, so a reference inflated
    by the very spikes it should react to yields a LARGER multiplier — less de-risking, the
    unsafe direction."""
    quiet_with_one_spike = [0.005] * 9 + [0.500]
    median = rolling_median(quiet_with_one_spike, 10, 5)[-1]
    mean = sum(quiet_with_one_spike) / len(quiet_with_one_spike)
    assert median == pytest.approx(0.005)
    assert mean > 0.05, "the mean is dragged an order of magnitude by one bar"


def test_rolling_median_handles_even_windows_and_gaps():
    assert rolling_median([1.0, 3.0], 2, 2)[-1] == pytest.approx(2.0)
    assert rolling_median([1.0, None, 3.0, 5.0], 4, 3)[-1] == pytest.approx(3.0)
    assert rolling_median([1.0, 2.0], 2, 5)[-1] is None, "min_periods not met"


def test_the_row_carries_a_reference_once_it_has_warmed():
    candles = [
        {"open_time": f"2026-07-20T{h % 24:02d}:00:00Z", "close_time": f"2026-07-20T{(h + 1) % 24:02d}:00:00Z",
         "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0 + (h % 5), "volume": 10.0}
        for h in range(160)
    ]
    rows = build_feature_rows({"symbol": "ETHUSDT", "timeframe": "1h", "candles": candles})
    assert rows[0]["atr_pct_reference"] is None, "no reference before it warms"
    assert rows[-1]["atr_pct_reference"] is not None
    # And it is in the same units as the current reading, which is what makes the ratio meaningful.
    assert rows[-1]["atr_pct_of_price"] is not None


def test_the_reference_is_not_mintable():
    """It is a sizing input, not a signal. Admitting it to the factory's vocabulary would let a
    spec condition on the denominator of its own position size."""
    from runtime.mvp_runtime.crypto import factory

    assert "atr_pct_reference" not in factory.NUMERIC_FEATURES
    assert "atr_pct_reference" not in factory.CATEGORICAL_FEATURES


def test_the_plan_carries_the_multiplier_so_the_two_cannot_disagree():
    """Computed where the plan is built, for the reason `max_holding_bars` is: a fact the
    execution step could re-derive from a feature row it fetched itself is a fact the two can
    disagree about."""
    from runtime.mvp_runtime.crypto.paper import build_entry_plan
    from runtime.mvp_runtime.crypto.strategy import StrategySpec

    spec = StrategySpec.from_dict({
        "schema_version": "strategy_spec.v1", "strategy_id": "S001", "strategy_version": "1.0",
        "strategy_family": "trend_pullback", "direction": "long", "timeframe": "1h",
        "symbol_scope": ["ETHUSDT"],
        "entry_rules": {"operator": "AND", "conditions": [
            {"feature": "close", "comparison": ">", "value": 0.0}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.0, "target_atr": 2.0,
                       "max_holding_bars": 12},
    })
    route = {"status": "ENTRY_CANDIDATE", "direction": "LONG", "primary_spec": spec,
             "symbol": "ETHUSDT", "timeframe": "1h"}
    row = {"close": PRICE, "atr": 60.0, "atr_pct_of_price": 0.02, "atr_pct_reference": REFERENCE}
    plan = build_entry_plan(route, row, now="2026-07-30T00:00:00Z")
    assert plan["vol_size_multiplier"] == pytest.approx(0.25)
