"""LP5.2 tests — how large a live order may be. Pure arithmetic, no venue, no network.

The properties under test are the ones that decide whether a real order is the size the
operator's registered budget permits: the smaller of the two candidates always wins,
rounding is DOWN so a lot can never be gained, the venue's own minimums are honoured, and
every missing input refuses instead of producing a confident wrong number.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import live_sizing as ls

# A representative BTCUSDT-shaped filter set. Values are supplied by the test, never by
# the module — the design forbids implementing venue filters from memory, and that rule
# applies to fixtures too: these are a plausible shape, not an authority.
FILTERS = ls.SymbolFilters(step_size=0.001, min_qty=0.001, min_notional=5.0, tick_size=0.1)

# entry 60000, stop 1000 away -> risk 1000 quote per base unit.
PLAN = {"entry_price": 60000.0, "risk": 1000.0}


def _size(**kw):
    return ls.size_live_order(
        kw.pop("plan", PLAN),
        equity_usdt=kw.pop("equity_usdt", 100_000.0),
        max_order_notional_usdt=kw.pop("max_order_notional_usdt", 60.0),
        filters=kw.pop("filters", FILTERS),
        **kw,
    )


# --- min-of-two ---------------------------------------------------------------

def test_budget_binds_when_it_is_the_smaller_candidate():
    # equity 100k * 1% = 1000 risk budget / 1000 per unit = 1.0 BTC -> 60,000 USDT,
    # far above the 60 USDT per-order cap, so the budget must bind.
    result = _size()
    assert result["sizable"] is True
    assert result["bound_by"] == "budget"
    assert result["notional_usdt"] <= 60.0


def test_risk_binds_when_it_is_the_smaller_candidate():
    # equity 60 * 1% = 0.6 risk budget / 1000 per unit = 0.0006 BTC. Below the venue's
    # 0.001 step, so this refuses — the honest outcome for an account too small to take
    # the intended risk in tradeable increments.
    result = _size(equity_usdt=60.0)
    assert result["bound_by"] == "risk"
    assert result["sizable"] is False
    assert ls.ROUNDS_TO_ZERO in result["reasons"]


def test_a_wider_stop_produces_a_smaller_size():
    """The risk candidate is the whole point: the same account risks the same amount, so
    a stop twice as far away buys half as much."""
    # A cap high enough that the RISK candidate is the smaller one in both cases —
    # otherwise the budget binds and the comparison says nothing about risk sizing.
    tight = _size(plan={"entry_price": 60000.0, "risk": 500.0}, max_order_notional_usdt=1_000_000.0)
    wide = _size(plan={"entry_price": 60000.0, "risk": 1000.0}, max_order_notional_usdt=1_000_000.0)
    assert tight["bound_by"] == wide["bound_by"] == "risk"
    assert tight["quantity"] == pytest.approx(2 * wide["quantity"], rel=1e-6)


def test_the_registered_cap_is_never_exceeded():
    """The property the guard independently re-verifies. Swept across awkward caps so a
    rounding direction error cannot hide in one convenient number."""
    for cap in (5.0, 17.3, 60.0, 61.7, 119.99, 200.0):
        result = _size(max_order_notional_usdt=cap)
        if result["sizable"]:
            assert result["notional_usdt"] <= cap, f"cap {cap} exceeded"


# --- rounding -----------------------------------------------------------------

def test_rounding_is_down_never_up():
    # budget 61 / 60000 = 0.0010166... -> floors to 0.001, never 0.002 (which would be
    # 120 USDT, double the cap).
    result = _size(max_order_notional_usdt=61.0)
    assert result["quantity"] == 0.001


def test_floor_to_step_survives_binary_float_artefacts():
    """0.3 / 0.1 is 2.9999... in binary floating point; a naive floor loses a whole lot."""
    assert ls.floor_to_step(0.3, 0.1) == pytest.approx(0.3)
    assert ls.floor_to_step(0.7, 0.1) == pytest.approx(0.7)
    assert ls.floor_to_step(1.23456, 0.001) == pytest.approx(1.234)


def test_floor_to_step_refuses_nonsense_inputs():
    assert ls.floor_to_step(1.0, 0.0) == 0.0
    assert ls.floor_to_step(-1.0, 0.1) == 0.0


def test_price_tick_rounding_both_directions():
    assert ls.round_price_to_tick(60000.07, 0.1, mode="down") == pytest.approx(60000.0)
    assert ls.round_price_to_tick(60000.07, 0.1, mode="up") == pytest.approx(60000.1)
    # Unknown tick returns 0.0 so the caller must refuse rather than send an unrounded price.
    assert ls.round_price_to_tick(60000.07, 0.0) == 0.0


def _decimals(value: float) -> int:
    text = repr(float(value))
    assert "e" not in text and "E" not in text, f"scientific notation reaches the venue as-is: {text}"
    return len(text.split(".")[1]) if "." in text else 0


@pytest.mark.parametrize("increment", [0.1, 0.01, 0.001, 1.0])
def test_rounded_values_serialise_inside_the_increment_s_precision(increment):
    """What the venue reads is the REPR, and `pytest.approx` is blind to it.

    Measured on the live account 2026-08-18T14:28:52Z. `round(n * tick, 12)` — the old ending of
    both helpers — left `640001 * 0.1` as `64000.100000000006`; `urlencode` sent that repr, and
    the venue refused the BTCUSDT `STOP_MARKET` leg with **-1111 Precision is over the maximum
    defined for this asset**. The entry had already filled, so `live_leg` rule 2 closed the
    position naked and the bracket-failure breaker moved to 1 of 2.

    Every assertion above this one uses `pytest.approx`, which is why the defect lived: the wrong
    value equals the right one to within any tolerance anyone would write. Only the text differs,
    so the text is what this pins.

    The sweep runs ABOVE `1e-12 * 2**52` (~4.5e3) on purpose — that magnitude is where the
    residue climbs inside the twelfth decimal. Below it the old code passed, which is exactly why
    ETHUSDT and SOLUSDT never showed the fault and BTCUSDT always could.
    """
    allowed = _decimals(increment)
    for n in range(45_000, 45_400):
        value = n * increment
        assert _decimals(ls.floor_to_step(value, increment)) <= allowed
        for mode in ("up", "down"):
            assert _decimals(ls.round_price_to_tick(value, increment, mode=mode)) <= allowed


def test_the_btcusdt_stop_that_was_refused_now_serialises():
    """The exact shape of the 2026-08-18 rejection, pinned as itself."""
    assert repr(ls.round_price_to_tick(64000.13, 0.1, mode="down")) == "64000.1"
    assert repr(ls.round_price_to_tick(64847.94, 0.1, mode="down")) == "64847.9"
    # Rounding DIRECTION is unchanged by the fix — a stop must not drift past what was planned.
    assert ls.round_price_to_tick(64000.13, 0.1, mode="up") == pytest.approx(64000.2)


# --- refuse rather than default -----------------------------------------------

@pytest.mark.parametrize("kwargs,reason", [
    ({"equity_usdt": 0.0}, ls.NO_EQUITY),
    ({"max_order_notional_usdt": 0.0}, ls.NO_BUDGET_CAP),
    ({"filters": None}, ls.NO_FILTERS),
    ({"plan": {"entry_price": 60000.0, "risk": 0.0}}, ls.NO_RISK_DISTANCE),
    ({"plan": {"entry_price": 0.0, "risk": 1000.0}}, ls.NO_ENTRY_PRICE),
])
def test_every_missing_input_refuses(kwargs, reason):
    result = _size(**kwargs)
    assert result["sizable"] is False
    assert result["quantity"] == 0.0 and result["notional_usdt"] == 0.0
    assert reason in result["reasons"]


def test_a_zero_cap_means_unconfigured_and_blocks():
    """0 is 'not configured' everywhere in the live stack, and 0 blocks — it is never
    read as 'no limit'."""
    assert _size(max_order_notional_usdt=0.0)["sizable"] is False


def test_incomplete_filters_are_not_usable():
    for bad in (
        ls.SymbolFilters(step_size=0.0, min_qty=0.001, min_notional=5.0),
        ls.SymbolFilters(step_size=0.001, min_qty=0.0, min_notional=5.0),
        ls.SymbolFilters(step_size=0.001, min_qty=0.001, min_notional=0.0),
    ):
        assert bad.valid() is False
        assert ls.NO_FILTERS in _size(filters=bad)["reasons"]


def test_all_missing_inputs_are_reported_together():
    """The guard's accumulate-never-short-circuit posture: the operator sees every reason
    at once rather than fixing them one refusal at a time."""
    result = ls.size_live_order(
        {"entry_price": 0.0, "risk": 0.0}, equity_usdt=0.0,
        max_order_notional_usdt=0.0, filters=None,
    )
    assert set(result["reasons"]) == {
        ls.NO_ENTRY_PRICE, ls.NO_RISK_DISTANCE, ls.NO_EQUITY, ls.NO_BUDGET_CAP, ls.NO_FILTERS,
    }


# --- the venue's own minimums --------------------------------------------------

def test_below_min_quantity_refuses():
    filters = ls.SymbolFilters(step_size=0.001, min_qty=0.01, min_notional=5.0)
    result = _size(max_order_notional_usdt=60.0, filters=filters)
    assert result["sizable"] is False
    assert ls.BELOW_MIN_QTY in result["reasons"]


def test_below_min_notional_refuses():
    filters = ls.SymbolFilters(step_size=0.001, min_qty=0.001, min_notional=100.0)
    result = _size(max_order_notional_usdt=60.0, filters=filters)
    assert result["sizable"] is False
    assert ls.BELOW_MIN_NOTIONAL in result["reasons"]


# --- equity ---------------------------------------------------------------------

def test_usable_equity_is_the_conservative_available_balance():
    class _Snap:
        wallet_balance = 1000.0
        margin_balance = 900.0
        available_balance = 250.0

    assert ls.usable_equity_usdt(_Snap()) == 250.0


def test_unreadable_account_yields_zero_equity_which_refuses():
    assert ls.usable_equity_usdt(None) == 0.0
    assert _size(equity_usdt=ls.usable_equity_usdt(None))["sizable"] is False


def test_negative_equity_is_floored_to_zero():
    class _Snap:
        available_balance = -50.0

    assert ls.usable_equity_usdt(_Snap()) == 0.0


# --- the record itself ----------------------------------------------------------

def test_a_refusal_still_reports_both_candidates():
    """An operator reading a refusal must be able to see which constraint bound and by how
    much, not just that the answer was no."""
    result = _size(equity_usdt=60.0)
    assert result["sizable"] is False
    assert result["risk_quantity"] > 0 and result["budget_quantity"] > 0
    assert result["bound_by"] == "risk"
    assert result["equity_usdt"] == 60.0


def test_sizing_is_pure_and_deterministic():
    assert _size() == _size()


def test_module_cannot_send_anything():
    surface = " ".join(dir(ls)).lower()
    for forbidden in ("submit", "cancel", "adapter", "post", "http"):
        assert forbidden not in surface
