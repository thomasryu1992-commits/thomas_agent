"""The stop leg's slippage: split from the general rate, and measured at source.

Why this exists (REMAINING_WORK §C): the first two live stops ever taken filled 23.5 bps and
0.0 bps past their triggers, against a cost model that prices every market exit at an
inherited, unmeasured 3.0 — and both figures had to be reconstructed BY HAND from the resting
order, because nothing stamped the trigger onto the outcome row.

Two properties are pinned here, and they pull in opposite directions on purpose:

- **The split is a seam, not a re-decision.** `DEFAULT_STOP_SLIPPAGE_BPS` equals the general
  rate today, so every number the model produces is byte-identical to before the split. A
  test asserts the equality: diverging the two is a deliberate act that must cite the sample.
- **The sample now accumulates itself.** Every stop close computes `stop_slippage_bps`
  against its own trigger (`realized_stop_slippage_bps`), and
  `stop_slippage_observations` is the read that a future re-decision averages.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import cost, live_leg, live_pnl
from runtime.mvp_runtime.crypto.cost import CostModel, apply_cost_model, cost_model_for
from runtime.mvp_runtime.crypto.live_pnl import (
    STOP_EXIT_REASONS,
    build_live_outcome_record,
    realized_stop_slippage_bps,
    stop_slippage_observations,
)

NOW = "2026-08-10T15:00:00Z"


# --- the split itself ---------------------------------------------------------------------


def test_the_split_is_a_seam_not_a_repricing():
    """Re-priced 2026-08-21 from 12.0 to 1.4 bps after the stop-slippage probe reached
    n=10 (median 1.07, upper quartile 1.60; Thomas chose 1.4 as the round figure).
    Pinned: still dearer than the general slippage rate (fail-closed direction)."""
    assert cost.DEFAULT_STOP_SLIPPAGE_BPS == 1.4
    assert cost.DEFAULT_STOP_SLIPPAGE_BPS > cost.DEFAULT_SLIPPAGE_BPS


def test_a_stop_exit_prices_dearer_than_a_market_exit_at_the_default_rates():
    """The inverse of the pre-2026-08-11 pin: the interim rate makes the stop leg pay more
    than a time exit on the same move — never less."""
    stop = apply_cost_model("LONG", 100.0, 97.0, 3.0, close_reason="stop_loss")
    market = apply_cost_model("LONG", 100.0, 97.0, 3.0, close_reason="time_exit")
    assert stop.net_r < market.net_r


def test_a_raised_stop_rate_reprices_only_the_stop_exit():
    """The seam working: the stop leg moves, and every other leg is untouched to the digit."""
    default = CostModel()
    raised = CostModel(stop_slippage_bps=23.5)

    for reason in ("time_exit", "take_profit", None):
        assert (
            apply_cost_model("LONG", 100.0, 103.0, 3.0, cost=raised, close_reason=reason)
            == apply_cost_model("LONG", 100.0, 103.0, 3.0, cost=default, close_reason=reason)
        ), f"{reason!r} must not pay the stop rate"

    stop_raised = apply_cost_model("LONG", 100.0, 97.0, 3.0, cost=raised, close_reason="stop_loss")
    stop_default = apply_cost_model("LONG", 100.0, 97.0, 3.0, cost=default, close_reason="stop_loss")
    assert stop_raised.net_r < stop_default.net_r
    assert stop_raised.slippage_cost_r > stop_default.slippage_cost_r
    # The entry leg still pays the GENERAL rate under the raised model: the whole delta is the
    # exit side — the fill moved by (23.5 - default) bps of the exit price, minus the taker fee
    # no longer charged on the price the fill gave up. Symbolic in the default, so this test
    # measures the seam and not whichever interim the constant holds. Nothing else may move.
    delta_fill = 97.0 * (23.5 - cost.DEFAULT_STOP_SLIPPAGE_BPS) / 10000.0
    expected_delta = delta_fill * (1.0 - cost.DEFAULT_TAKER_FEE_BPS / 10000.0) / 3.0
    assert stop_default.net_r - stop_raised.net_r == pytest.approx(expected_delta, abs=1e-6)


def test_a_short_stop_pays_the_raised_rate_on_the_adverse_side():
    """A SHORT's stop exit fills ABOVE the trigger; the raised rate must cost it, not pay it."""
    raised = CostModel(stop_slippage_bps=23.5)
    stop = apply_cost_model("SHORT", 100.0, 103.0, 3.0, cost=raised, close_reason="stop_loss")
    default = apply_cost_model("SHORT", 100.0, 103.0, 3.0, close_reason="stop_loss")
    assert stop.net_r < default.net_r


def test_round_trip_cost_is_untouched_by_the_stop_rate():
    """The entry door prices the plain taker exit (`close_reason=None`), deliberately — a plan
    is refused on the friction every trade pays, not on the stop's worst case."""
    assert cost.round_trip_cost_r("LONG", 100.0, 3.0, cost=CostModel(stop_slippage_bps=99.0)) \
        == cost.round_trip_cost_r("LONG", 100.0, 3.0)


def test_a_close_reason_cannot_be_both_maker_and_stop():
    assert not (cost.MAKER_EXIT_REASONS & STOP_EXIT_REASONS)
    assert live_leg.CLOSE_REASON_STOP in STOP_EXIT_REASONS
    assert live_leg.CLOSE_REASON_TARGET not in STOP_EXIT_REASONS


# --- the venue registry keeps its own denomination ----------------------------------------


def test_a_declared_venue_stop_rate_inherits_that_venues_general_slippage(monkeypatch):
    """A venue with no separate stop figure pays ITS OWN general slippage on the stop leg —
    never the module default, which since 2026-08-11 is Binance's measured interim wearing a
    neutral name. Binance itself now DECLARES that figure, so the fallback is pinned on a
    synthetic venue instead of on the one venue that no longer exercises it."""
    model = cost_model_for(cost.market_data.BINANCE_FUTURES)
    assert model.stop_slippage_bps == cost.DEFAULT_STOP_SLIPPAGE_BPS

    declared = cost.VenueCostDeclaration(
        venue="fakevenue", taker_fee_bps=4.0, slippage_bps=7.0, maker_fee_bps=1.0,
        funding_bps_per_interval=1.0, funding_intervals_per_day=3,
    )
    assert declared.missing() == ()
    monkeypatch.setitem(cost.VENUE_COST_DECLARATIONS, "fakevenue", declared)
    fake = cost_model_for("fakevenue")
    assert fake.stop_slippage_bps == 7.0  # its own general rate — never Binance's 12


def test_an_explicit_venue_stop_rate_is_carried_through(monkeypatch):
    declaration = cost.VenueCostDeclaration(
        venue="fakevenue", taker_fee_bps=4.0, slippage_bps=7.0, maker_fee_bps=1.0,
        funding_bps_per_interval=1.0, funding_intervals_per_day=3, stop_slippage_bps=12.0,
    )
    monkeypatch.setitem(cost.VENUE_COST_DECLARATIONS, "fakevenue", declaration)
    model = cost_model_for("fakevenue")
    assert model.stop_slippage_bps == 12.0 and model.slippage_bps == 7.0

    without = cost.VenueCostDeclaration(
        venue="fakevenue2", taker_fee_bps=4.0, slippage_bps=7.0, maker_fee_bps=1.0,
        funding_bps_per_interval=1.0, funding_intervals_per_day=3,
    )
    monkeypatch.setitem(cost.VENUE_COST_DECLARATIONS, "fakevenue2", without)
    assert cost_model_for("fakevenue2").stop_slippage_bps == 7.0


# --- the measurement ----------------------------------------------------------------------


def test_the_first_real_stop_reproduces_the_hand_measured_figure():
    """ETHUSDT 2026-08-06: SHORT closed by a BUY, stop rested at 1900.5, filled 1904.96 —
    §C measured 23.5 bps by hand. The stamp must agree with the hand."""
    assert realized_stop_slippage_bps("BUY", 1900.5, 1904.96) == pytest.approx(23.4675, abs=1e-3)


def test_a_fill_exactly_at_the_trigger_is_zero_not_none():
    """DOGEUSDT's stop filled at its trigger: 0.0 is a measurement, not an absence."""
    assert realized_stop_slippage_bps("BUY", 0.07054, 0.07054) == 0.0


def test_adverse_is_positive_on_both_sides():
    # SELL closes a LONG: adverse is BELOW the trigger.
    assert realized_stop_slippage_bps("SELL", 100.0, 99.9) > 0
    assert realized_stop_slippage_bps("SELL", 100.0, 100.1) < 0
    # BUY closes a SHORT: adverse is ABOVE the trigger.
    assert realized_stop_slippage_bps("BUY", 100.0, 100.1) > 0
    assert realized_stop_slippage_bps("BUY", 100.0, 99.9) < 0


@pytest.mark.parametrize("side,stop,exit_price", [
    ("HOLD", 100.0, 99.0),      # unrecognised side
    ("SELL", None, 99.0),       # no trigger recorded
    ("SELL", 0.0, 99.0),        # non-positive trigger
    ("SELL", 100.0, None),      # no exit price
    ("SELL", True, 99.0),       # bool is not a price
])
def test_an_uncomputable_slippage_is_none_never_a_guess(side, stop, exit_price):
    assert realized_stop_slippage_bps(side, stop, exit_price) is None


# --- the outcome row carries it -----------------------------------------------------------


def _outcome(**kw):
    return build_live_outcome_record(
        realized_pnl_usdt=kw.pop("realized_pnl_usdt", -1.0),
        symbol=kw.pop("symbol", "BTCUSDT"),
        side=kw.pop("side", "SELL"),
        quantity=kw.pop("quantity", 0.001),
        entry_price=kw.pop("entry_price", 60000.0),
        exit_price=kw.pop("exit_price", 58990.0),
        position_id=kw.pop("position_id", "pos1"),
        close_reason=kw.pop("close_reason", "stop_loss"),
        risk_usdt=kw.pop("risk_usdt", 1.0),
        now=kw.pop("now", NOW),
        **kw,
    )


def test_a_stop_close_stamps_trigger_and_slippage():
    row = _outcome(stop_price=59000.0)
    assert row["stop_price"] == 59000.0
    assert row["stop_slippage_bps"] == pytest.approx((59000.0 - 58990.0) / 59000.0 * 10000.0, abs=1e-3)
    assert row["record_sha256"]  # the new fields are inside the self-hash, not beside it


def test_a_non_stop_close_never_contributes_to_the_sample():
    """A time exit's distance from a trigger it never touched is not slippage."""
    row = _outcome(close_reason="time_exit", stop_price=59000.0)
    assert row["stop_price"] == 59000.0
    assert row["stop_slippage_bps"] is None


def test_a_stop_close_with_no_recorded_trigger_stays_unmeasured():
    row = _outcome()
    assert row["stop_price"] is None and row["stop_slippage_bps"] is None


# --- the accumulating read ----------------------------------------------------------------


def test_the_observation_read_collects_exactly_the_measured_stops():
    measured = _outcome(stop_price=59000.0)
    legacy_stop = {k: v for k, v in _outcome().items()
                   if k not in ("stop_price", "stop_slippage_bps")}   # pre-field row
    time_exit = _outcome(close_reason="time_exit", stop_price=59000.0)
    sample = stop_slippage_observations([legacy_stop, time_exit, measured, None, "junk"])
    assert [row["outcome_id"] for row in sample] == [measured["outcome_id"]]
    assert sample[0]["stop_slippage_bps"] == measured["stop_slippage_bps"]
    assert sample[0]["stop_price"] == 59000.0


# --- the settle path that priced the first two stops stamps it at source ------------------


class _Adapter:
    def cancel_order(self, **kwargs):
        return {"status": "CANCELED"}


class _Ledger:
    def __init__(self):
        self.outcomes = []

    def append_outcome(self, outcome):
        self.outcomes.append(outcome)


class _Store:
    def __init__(self):
        self.cleared = []

    def clear_position(self, symbol):
        self.cleared.append(symbol)


def test_a_venue_filled_stop_measures_its_own_slippage():
    """The §C path: the bracket's stop leg filled at the venue, past its trigger. The outcome
    row now carries the figure that had to be reconstructed by hand on 2026-08-06."""
    position = {
        "symbol": "ETHUSDT", "direction": "SHORT", "quantity": 0.022,
        "entry_price": 1859.14, "entry_quote_usdt": 40.90108,
        "stop_loss": 1900.5, "risk": 0.90992,
        "opened_at_utc": "2026-08-04T00:13:57Z", "position_id": "live_position_test",
    }
    legs = {"status": live_leg.PROTECTED, "legs": [{
        "leg": "stop_client_order_id", "close_reason": live_leg.CLOSE_REASON_STOP,
        "filled": True, "resting": False, "status": "FILLED", "exchange_order_id": 42,
        "fill": {"avg_price": 1904.96, "cum_quote": 41.90912, "executed_qty": 0.022},
    }]}
    ledger, store = _Ledger(), _Store()
    result = live_leg.settle_venue_closed_position(
        position, adapter=_Adapter(), position_store=store, ledger=ledger,
        legs=legs, now="2026-08-06T04:44:13Z",
    )
    assert result["status"] == live_leg.EXIT_CLOSED
    outcome = ledger.outcomes[0]
    assert outcome["close_reason"] == "stop_loss"
    assert outcome["stop_price"] == 1900.5
    assert outcome["stop_slippage_bps"] == pytest.approx(23.4675, abs=1e-3)
    # And the accumulating read sees it.
    assert stop_slippage_observations([outcome])[0]["stop_slippage_bps"] == outcome["stop_slippage_bps"]
