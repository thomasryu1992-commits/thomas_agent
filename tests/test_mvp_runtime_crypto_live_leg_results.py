"""What a live leg's result says is its own module, and ``live_leg`` still offers it under the old names
(crypto refactor plan PR-13; ``docs/proposals/CRYPTO_REFACTOR_AND_MODULARIZATION_PLAN_V0.1.md`` §L-4).

The leg's result vocabulary and its adapter-free result readers moved out of ``live_leg`` whole. What the
leg does with them is pinned where it always was (``test_mvp_runtime_crypto_live_leg.py``,
``…_external_close_settlement.py``, ``…_live_route.py``), through ``live_leg``. This file pins what the
move itself has to keep true:

- every name the results module defines is the same object on ``live_leg``, so a status the leg writes
  and the one a reader compares against cannot become two;
- the results module imports no module that sends an order, which is what lets a reader of results
  import it without importing the leg.

The last section pins seven rules of the readers that no test stated before the move. A mutation pass
over the moved code found them unpinned: each could be changed with the suite still green. They are
the current behaviour, written down, not a change to it.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.crypto import live_leg, live_leg_results
from tests._helpers import defined_names
from tests.test_mvp_runtime_crypto_layers import EGRESS_MODULES, _edges, _reach


def test_live_leg_offers_every_name_the_results_module_defines_as_the_same_object():
    names = defined_names(live_leg_results)
    assert {"ENTRY_OPENED", "EXIT_CLOSED", "CLOSE_REASON_STOP", "BRACKET_RESTING_STATUSES", "FILLED_STATUSES",
            "PROTECTED", "realized_pnl_usdt", "exit_fill_from_history", "legs_left_resting",
            "_record_naked_outcome"} <= set(names), "the scan lost the module's own names: it broke, not the module"
    different = sorted(n for n in names if getattr(live_leg, n, None) is not getattr(live_leg_results, n))
    assert different == [], f"live_leg holds a different object, or none, for: {different}"


def test_the_results_module_reaches_no_module_that_sends():
    """Directly or through anything it imports, function-local imports included."""
    reached = _reach(_edges()).get("live_leg_results", set())
    assert reached, "the results module imports nothing at all: the scan broke"
    assert sorted(reached & EGRESS_MODULES) == []


# --- rules of the readers that nothing pinned -----------------------------------------------------------

OPENED = "2026-08-08T08:00:00Z"
POSITION = {"symbol": "BTCUSDT", "direction": "LONG", "quantity": 0.001, "entry_price": 65000.0,
            "entry_quote_usdt": 65.0, "opened_at_utc": OPENED}


def _ms(iso):
    return int(timeutil.parse_iso(iso).timestamp() * 1000)


def _fill(*, qty=0.001, quote=65.5, at="2026-08-08T09:00:00Z", order_id=777):
    return {"side": "SELL", "qty": qty, "quoteQty": quote, "time": _ms(at), "orderId": order_id}


def test_a_resting_leg_that_said_something_is_still_described():
    """A leg can rest and have been refused once on the way (a duplicate id that turned out to be its
    own order). What the venue said stays on the record; a resting leg that said nothing is left out."""
    detail = live_leg_results.bracket_error_detail({"bracket": [
        {"leg": "stop", "status": "NEW", "placed": True, "error": "ORDER_DUPLICATE", "error_detail": {"code": -4116}},
        {"leg": "target", "status": "NEW", "placed": True},
    ]})
    assert [leg["leg"] for leg in detail] == ["stop"]
    assert detail[0]["error_detail"] == {"code": -4116}


@pytest.mark.parametrize("direction,side", [("LONG", "SELL"), ("SHORT", "BUY")])
def test_a_naked_close_outcome_names_the_closing_side(direction, side):
    result = {"reason_codes": []}
    fill = {"avg_price": 66000.0, "cum_quote": 66.0, "executed_qty": 0.001}
    live_leg_results._record_naked_outcome(
        result, close={"fill": fill, "exchange_order_id": 9}, symbol="BTCUSDT", direction=direction,
        quantity=0.001, entry_price=65000.0, now="2026-08-08T09:00:00Z",
        identity={"entry_quote_usdt": 65.0, "position_id": "unbooked_1", "risk_usdt": 1.0},
    )
    assert result["reason_codes"] == []
    assert result["outcome"]["side"] == side


@pytest.mark.parametrize("direction", ["", "FLAT", None])
def test_a_position_with_no_known_direction_is_not_priced(direction):
    pnl, detail = live_leg_results.realized_pnl_usdt(
        {**POSITION, "direction": direction}, {"avg_price": 66000.0, "cum_quote": 66.0, "executed_qty": 0.001})
    assert pnl is None
    assert detail["exit_quote_usdt"] == 66.0   # the figures were read; it is the direction that is missing


def test_the_fill_query_starts_a_minute_before_the_open():
    assert live_leg_results._history_start_ms(POSITION) == _ms(OPENED) - 60_000
    assert live_leg_results._history_start_ms({"opened_at_utc": "not a time"}) == 0
    assert live_leg_results._history_start_ms({}) == 0


def test_one_malformed_row_refuses_the_whole_history_even_beside_a_fill_that_fits():
    """The fill that fits would price the close on its own. A row that cannot be read means the list is
    not what it looks like, so nothing is priced from it."""
    good = _fill()
    assert live_leg_results.exit_fill_from_history(POSITION, [good])[0] is not None
    bad = {"side": "SELL", "qty": None, "quoteQty": 1.0, "time": _ms("2026-08-08T09:00:01Z"), "orderId": 778}
    for rows in ([good, bad], [bad, good]):
        assert live_leg_results.exit_fill_from_history(POSITION, rows) == (None, None)


def test_a_close_made_of_two_orders_names_neither():
    one = [_fill(qty=0.0006, quote=39.0), _fill(qty=0.0004, quote=26.0, at="2026-08-08T09:00:01Z")]
    assert live_leg_results.exit_fill_from_history(POSITION, one)[1] == 777
    two = [_fill(qty=0.0006, quote=39.0), _fill(qty=0.0004, quote=26.0, at="2026-08-08T09:00:01Z", order_id=778)]
    fill, order_id = live_leg_results.exit_fill_from_history(POSITION, two)
    assert fill is not None and order_id is None


def test_the_status_line_carries_the_reason_codes_and_the_result():
    line = live_leg_results.leg_status_line({
        "symbol": "BTCUSDT", "status": live_leg_results.ENTRY_NAKED_CLOSED,
        "reason_codes": [live_leg_results.BRACKET_FAILED, live_leg_results.NAKED_POSITION_CLOSED],
        "outcome": {"realized_pnl_usdt": -0.4, "result_R": -0.4},
    })
    assert line == ("live_leg BTCUSDT: ENTRY_NAKED_CLOSED (LIVE_BRACKET_FAILED,LIVE_NAKED_POSITION_CLOSED) "
                    "pnl=-0.4 R=-0.4")
    bare = live_leg_results.leg_status_line({"symbol": "BTCUSDT", "status": "EXIT_CLOSED"})
    assert bare == "live_leg BTCUSDT: EXIT_CLOSED"

