"""A bracket leg the venue accepted and then could not find.

Measured on the live account 2026-08-03T04:28:58Z, on the first real bracket after the Algo
migration. The POST to ``/fapi/v1/algoOrder`` raised nothing — no code, no message — and the
query that followed answered "does not exist". So the leg recorded:

    placed: false   status: NOT_FOUND   error: null   error_detail: null

A silent failure, and the worst kind. ``_close_naked_position`` cancels what PLACED, so the stop
was never withdrawn: if that order was in fact resting it is resting still, unowned, counting
against the symbol's conditional cap and able to trigger on a position it was never meant to
protect.

The cause of the disagreement is not settled — that needs the venue's own answer, which is why
the submit response is now recorded rather than discarded. What these tests pin is that the
disagreement can never again be filed as an ordinary miss, and that the close withdraws an order
that might exist.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import live_leg
from runtime.mvp_runtime.crypto.live_leg import (
    BRACKET_QUERY_MISSING,
    _placed_id,
    build_bracket_intent,
    place_bracket_leg,
)
from runtime.mvp_runtime.errors import ToolError


class _Adapter:
    """Submit answers one way, the query answers another — the shape of the incident."""

    def __init__(self, *, submit_response=None, submit_error=None, venue_order=None):
        self._submit_response = submit_response if submit_response is not None else {}
        self._submit_error = submit_error
        self._venue_order = venue_order
        self.cancelled: list[str] = []

    def submit(self, order_request, *, timeout_seconds=10):
        if self._submit_error:
            raise ToolError(self._submit_error, "scripted rejection")
        return self._submit_response

    def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
        return self._venue_order

    def cancel_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
        self.cancelled.append(str(client_order_id))
        return {"status": "CANCELED"}


def _leg(**kw):
    intent = build_bracket_intent(symbol="ETHUSDT", leg="SL", side="BUY", price=1887.86,
                                  working_type="MARK_PRICE", position_seed="seed")
    # No wall clock in a unit test: the confirm backoff is real seconds and this suite
    # exercises the missing-order path deliberately.
    return place_bracket_leg(intent, adapter=_Adapter(**kw), timeout_seconds=10,
                             sleep=lambda _seconds: None)


# --- the submit's answer is kept ------------------------------------------------

def test_the_submit_response_is_recorded():
    """It was being discarded while the query was treated as the only truth. On the Algo
    endpoints it carries `algoId` — direct evidence the order exists — so throwing it away threw
    away the only thing that could have caught 2026-08-03."""
    result = _leg(submit_response={"algoId": 4242, "algoStatus": "NEW"},
                  venue_order={"status": "NEW", "orderId": 4242})
    assert result["submit_response"] == {"algoId": 4242, "algoStatus": "NEW"}
    assert result["submitted_order_id"] == 4242


def test_the_order_endpoints_id_is_read_too():
    result = _leg(submit_response={"orderId": 99}, venue_order={"status": "NEW", "orderId": 99})
    assert result["submitted_order_id"] == 99


def test_a_submit_that_raised_records_no_response():
    result = _leg(submit_error="ORDER_REJECTED", venue_order=None)
    assert result["submit_response"] is None
    assert result["submitted_order_id"] is None


# --- the disagreement ------------------------------------------------------------

def test_accepted_then_not_found_is_not_an_ordinary_miss():
    """The 2026-08-03 shape, exactly: no error from the submit, nothing from the query."""
    result = _leg(submit_response={"algoId": 8389766247673980099, "algoStatus": "NEW"},
                  venue_order=None)
    assert result["status"] == BRACKET_QUERY_MISSING
    assert result["may_be_resting"] is True
    assert result["error"] == BRACKET_QUERY_MISSING
    assert "may be resting" in result["error_detail"]


def test_it_still_refuses_to_claim_protection():
    """`placed` stays False so the naked-position close still runs. An unconfirmed stop is not
    a stop, and the safe direction is out."""
    result = _leg(submit_response={"algoId": 1}, venue_order=None)
    assert result["placed"] is False


def test_a_submit_that_named_nothing_is_still_a_plain_not_found():
    """No id means the venue created nothing to worry about — the ordinary miss stays ordinary,
    so this cannot turn every rejection into a suspected stray order."""
    result = _leg(submit_response={}, venue_order=None)
    assert result["status"] == "NOT_FOUND"
    assert result["may_be_resting"] is False


def test_a_confirmed_resting_leg_is_unchanged():
    result = _leg(submit_response={"algoId": 5, "algoStatus": "NEW"},
                  venue_order={"status": "NEW", "orderId": 5})
    assert result["placed"] is True
    assert result["may_be_resting"] is False
    assert result["error"] is None


# --- and the close withdraws it ---------------------------------------------------

@pytest.mark.parametrize("leg,expected", [
    ({"placed": True, "client_order_id": "A"}, "A"),
    ({"may_be_resting": True, "client_order_id": "B"}, "B"),
    ({"placed": False, "may_be_resting": False, "client_order_id": "C"}, None),
    ({}, None),
])
def test_the_close_targets_anything_that_may_exist(leg, expected):
    """Cancelling an order that does not exist costs a -2011 the adapter already reads as
    'already gone'. NOT cancelling one that does leaves an unowned protective order resting.
    The two mistakes are not the same size."""
    assert _placed_id([leg], 0) == expected


def test_an_index_past_the_end_is_none():
    assert _placed_id([], 0) is None
    assert _placed_id([{"placed": True, "client_order_id": "A"}], 1) is None


def test_the_incident_leg_would_now_be_cancelled():
    """End to end on the recorded shape: submit accepted with an id, query missing, and the
    naked close now has an id to withdraw where before it had None."""
    result = _leg(submit_response={"algoId": 8389766247673980099}, venue_order=None)
    assert _placed_id([result], 0) == result["client_order_id"]


def test_the_status_is_named_so_it_cannot_be_filed_as_a_miss():
    """A distinct constant rather than reusing NOT_FOUND: the two have different consequences
    and only one of them leaves something at the venue."""
    assert BRACKET_QUERY_MISSING != "NOT_FOUND"
    assert BRACKET_QUERY_MISSING not in live_leg.BRACKET_RESTING_STATUSES
