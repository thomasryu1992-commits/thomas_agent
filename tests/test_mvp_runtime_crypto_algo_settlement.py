"""A venue-closed position the runtime could not price, because the fill was in other fields.

Brackets moved to `/fapi/v1/algoOrder` on 2026-08-03. Two things about that endpoint were not
carried across, and together they made settling a venue-closed position impossible:

1. its terminal state is ``FINISHED``, not ``FILLED`` — so `read_bracket_legs` never marked a
   triggered stop as filled;
2. its fill facts are ``actualPrice``/``actualQty``, not ``avgPrice``/``executedQty`` — so even
   forcing (1) would have priced the exit at ``None``.

`settle_venue_closed_position` refuses rather than invents, so it left the book OPEN, which kept
reconciliation refusing entries on that symbol. Correct behaviour on bad input, and the input was
bad for three days before anyone looked.

The payloads below are **verbatim** from the venue on 2026-08-06T04:25Z, for the two positions
that were actually stuck (ETHUSDT `live_position_2b438729...`, DOGEUSDT `live_position_f3dcee...`).
Synthetic fixtures would have encoded the same assumption that caused this — that a bracket leg
answers in order-endpoint field names — so the regression is pinned against what Binance really
sent.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto.live_execution import fill_facts, normalize_algo_order
from runtime.mvp_runtime.crypto.live_leg import (
    BRACKET_RESTING_STATUSES,
    FILLED_STATUSES,
    read_bracket_legs,
    realized_pnl_usdt,
)

# Verbatim, 2026-08-06T04:25Z. The stop that closed ETHUSDT at 17:38Z the previous day.
ETH_STOP_FINISHED = {
    "actualOrderId": "8389766249347349014",
    "actualPrice": "1904.96000",
    "actualQty": "0.022",
    "actualType": "MARKET",
    "algoId": 2000001334434488,
    "algoStatus": "FINISHED",
    "algoType": "CONDITIONAL",
    "clientAlgoId": "TAI_ETHUSDT_SL_78074a7099ec76a6ea",
    "closePosition": True,
    "orderType": "STOP_MARKET",
    "quantity": "0.022",
    "reduceOnly": True,
    "side": "BUY",
    "stopPrice": "1900.50",
    "symbol": "ETHUSDT",
    "triggerPrice": "1900.50",
    "triggerTime": 1785951506061,
    "workingType": "MARK_PRICE",
}

# Same shape, the DOGEUSDT stop. Kept because it is the one with a full entry record, so it is
# the case that exercises the P&L arithmetic end to end.
DOGE_STOP_FINISHED = {
    "algoId": 2000001334434489,
    "algoStatus": "FINISHED",
    "algoType": "CONDITIONAL",
    "actualPrice": "0.070540",
    "actualQty": "1075",
    "clientAlgoId": "TAI_DOGEUSDT_SL_50fa0900c0a73d7a03",
    "orderType": "STOP_MARKET",
    "quantity": "1075",
    "reduceOnly": True,
    "side": "BUY",
    "symbol": "DOGEUSDT",
    "triggerPrice": "0.07054",
    "triggerTime": 1785962699065,
}

DOGE_POSITION = {
    "position_id": "live_position_f3dceea2631ae9e57915",
    "symbol": "DOGEUSDT",
    "direction": "SHORT",
    "quantity": 1075.0,
    "entry_price": 0.06975,
    "entry_quote_usdt": 74.98125,
}


# --- the translation ----------------------------------------------------------

def test_the_fill_facts_survive_the_algo_translation():
    """The half that was missing. Before this, both came back None and the exit had no price."""
    facts = fill_facts(normalize_algo_order(ETH_STOP_FINISHED))
    assert facts["avg_price"] == pytest.approx(1904.96)
    assert facts["executed_qty"] == pytest.approx(0.022)


def test_the_venue_keys_are_kept_alongside_the_aliases():
    """`normalize_algo_order` is additive on purpose — the raw response is what an incident is
    reconstructed from, and this one was."""
    out = normalize_algo_order(ETH_STOP_FINISHED)
    assert out["actualPrice"] == "1904.96000"
    assert out["actualQty"] == "0.022"
    assert out["avgPrice"] == "1904.96000"
    assert out["executedQty"] == "0.022"


def test_an_alias_is_not_invented_when_the_venue_did_not_send_one():
    """A cancelled algo order carries no `actualQty`. It must stay missing rather than become a
    confident zero — a fabricated quantity is what turns "cannot price it" into a free trade."""
    cancelled = {k: v for k, v in ETH_STOP_FINISHED.items()
                 if k not in ("actualPrice", "actualQty")}
    out = normalize_algo_order(cancelled)
    assert "avgPrice" not in out
    assert "executedQty" not in out
    assert fill_facts(out)["executed_qty"] is None


def test_cum_quote_is_not_synthesised():
    """`realized_pnl_usdt` derives it; writing it into a venue-named field would make a computed
    value indistinguishable from a reported one."""
    out = normalize_algo_order(ETH_STOP_FINISHED)
    assert "cumQuote" not in out
    assert fill_facts(out)["cum_quote"] is None


# --- the status vocabulary ----------------------------------------------------

def test_both_spellings_of_executed_are_terminal():
    assert {"FILLED", "FINISHED"} <= FILLED_STATUSES


def test_finished_is_not_a_resting_state():
    """The bug's mirror image: if FINISHED ever read as resting, a closed position would look
    protected and the runtime would leave it alone forever."""
    assert "FINISHED" not in BRACKET_RESTING_STATUSES
    assert "NEW" in BRACKET_RESTING_STATUSES


# --- the line that was actually wrong -----------------------------------------

class _StubAdapter:
    """Answers the two bracket queries the way the venue did on 2026-08-06T04:25Z: the stop
    FINISHED with its fill, the take-profit already aged out of the query window.

    It also records which endpoint each leg was read on. The two legs are deliberately not the
    same kind — the stop is a conditional STOP_MARKET and therefore an algo order, the target
    has been a plain resting LIMIT since 2026-07-28 — and reading either on the wrong endpoint
    answers "no such order", which this module would take as a leg that never landed.
    """

    def __init__(self, stop_payload):
        self._stop = stop_payload
        self.queried: dict[str, bool] = {}

    def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
        self.queried[client_order_id] = algo
        if client_order_id == self._stop["clientAlgoId"]:
            return normalize_algo_order(self._stop) if algo else None
        return None


def test_read_bracket_legs_marks_a_triggered_algo_stop_as_filled():
    """The regression, at the line that carried it. Before the fix `filled` was False here and
    `settle_venue_closed_position` had nothing to price the exit from."""
    position = {
        "symbol": "ETHUSDT",
        "stop_client_order_id": ETH_STOP_FINISHED["clientAlgoId"],
        "take_profit_client_order_id": "TAI_ETHUSDT_TP_e39057cc20b1d20646",
    }
    adapter = _StubAdapter(ETH_STOP_FINISHED)
    read = read_bracket_legs(position, adapter=adapter)
    stop = next(leg for leg in read["legs"] if leg["leg"] == "stop_client_order_id")
    assert stop["status"] == "FINISHED"
    assert stop["filled"] is True
    assert stop["fill"]["avg_price"] == pytest.approx(1904.96)
    assert stop["fill"]["executed_qty"] == pytest.approx(0.022)
    # And the position is not reported as still covered.
    assert read["status"] != "PROTECTED"
    # Each leg on its own endpoint: the stop is an algo order, the target is not.
    assert adapter.queried[ETH_STOP_FINISHED["clientAlgoId"]] is True
    assert adapter.queried["TAI_ETHUSDT_TP_e39057cc20b1d20646"] is False


def test_a_finished_leg_with_no_quantity_is_not_filled():
    """FINISHED is also what a cancelled algo order reports. Terminal is not executed, and the
    `executed_qty > 0` half of the check is what keeps the two apart."""
    cancelled = {k: v for k, v in ETH_STOP_FINISHED.items()
                 if k not in ("actualPrice", "actualQty")}
    position = {"symbol": "ETHUSDT", "stop_client_order_id": cancelled["clientAlgoId"]}
    read = read_bracket_legs(position, adapter=_StubAdapter(cancelled))
    stop = next(leg for leg in read["legs"] if leg["leg"] == "stop_client_order_id")
    assert stop["status"] == "FINISHED"
    assert stop["filled"] is False


# --- the arithmetic that was blocked ------------------------------------------

def test_a_venue_closed_short_now_prices_its_exit():
    """The number that could not be computed for 42 cycles. SHORT: quote in minus quote out."""
    facts = fill_facts(normalize_algo_order(DOGE_STOP_FINISHED))
    pnl, detail = realized_pnl_usdt(DOGE_POSITION, facts)
    assert pnl is not None
    # 74.98125 - (1075 * 0.070540) = -0.84925
    assert pnl == pytest.approx(-0.84925, abs=1e-5)
    assert detail is not None


def test_a_fill_that_will_not_price_still_refuses():
    """Unchanged, and it must stay that way: the fix widens what can be read, never what can be
    assumed. No quantity means no P&L, and the caller keeps the position open."""
    facts = fill_facts(normalize_algo_order(
        {k: v for k, v in DOGE_STOP_FINISHED.items() if k != "actualQty"}))
    pnl, _ = realized_pnl_usdt(DOGE_POSITION, facts)
    assert pnl is None
