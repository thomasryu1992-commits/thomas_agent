"""The bracket worked and this module destroyed it.

Measured on the live account 2026-08-03T04:28:58Z, and confirmed from the venue's own algo-order
history:

    algoId 2000001331951220  clientAlgoId TAI_ETHUSDT_SL_764f36ae2ac555eeeb
    STOP_MARKET  closePosition=true  triggerPrice=1887.86
    createTime 04:29:01.793Z   algoStatus EXPIRED

The POST created the order. The query that followed it — milliseconds later — answered "does not
exist". `place_bracket_leg` recorded `placed: false`, `execute_live_entry` read that as an
unprotected position and closed it, and the stop then EXPIRED because a `closePosition` order
has nothing to close once its position is gone. **The same query run later finds the order
perfectly well**, which is how the race was identified at all.

Conditional orders live on a separate service — that is why they have their own endpoints — and
a write there is not immediately readable. The whole entry-to-close sequence completes inside
~500ms, squarely inside that window.

So a single immediate "not found" is not evidence. The cost of being wrong is asymmetric and
nothing priced that: being slow to notice a genuinely failed placement delays the naked close by
about a second, during which the position is unprotected either way; being fast and WRONG closes
a protected position and throws away the stop that was protecting it.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto.live_leg import (
    BRACKET_CONFIRM_ATTEMPTS,
    BRACKET_CONFIRM_BACKOFF_SECONDS,
    BRACKET_QUERY_MISSING,
    build_bracket_intent,
    place_bracket_leg,
)
from runtime.mvp_runtime.errors import ToolError


class _Venue:
    """A venue whose write becomes readable only after ``visible_on`` reads."""

    def __init__(self, *, visible_on=1, submit_response=None, submit_error=None):
        self.reads = 0
        self.algo_reads = 0
        self._visible_on = visible_on
        self._submit_response = {"algoId": 2000001331951220} if submit_response is None else submit_response
        self._submit_error = submit_error

    def submit(self, order_request, *, timeout_seconds=10):
        if self._submit_error:
            raise ToolError(self._submit_error, "scripted rejection")
        return self._submit_response

    def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
        self.reads += 1
        if algo:
            self.algo_reads += 1
        if self.reads < self._visible_on:
            return None
        return {"symbol": symbol, "status": "NEW", "orderId": 2000001331951220,
                "closePosition": True, "executedQty": 0.0}


def _stop(adapter, sleeper=None):
    intent = build_bracket_intent(symbol="ETHUSDT", leg="SL", side="BUY", price=1887.86,
                                 working_type="MARK_PRICE", position_seed="seed")
    return place_bracket_leg(intent, adapter=adapter,
                             sleep=sleeper or (lambda _seconds: None))


def _target(adapter, sleeper=None):
    intent = build_bracket_intent(symbol="ETHUSDT", leg="TP", side="BUY", price=1791.55,
                                  working_type="MARK_PRICE", position_seed="seed", quantity=0.032)
    return place_bracket_leg(intent, adapter=adapter,
                             sleep=sleeper or (lambda _seconds: None))


# --- the race, and that it is now survived --------------------------------------

@pytest.mark.parametrize("visible_on", range(1, BRACKET_CONFIRM_ATTEMPTS + 1))
def test_a_stop_that_becomes_readable_late_is_still_confirmed(visible_on):
    """The 2026-08-03 shape at every lag the budget covers. Before this, every one of them
    closed a protected position."""
    venue = _Venue(visible_on=visible_on)
    result = _stop(venue)
    assert result["placed"] is True
    assert result["status"] == "NEW"
    assert result["confirm_attempts"] == visible_on


def test_the_number_of_reads_it_took_is_recorded():
    """A first bounded probe, not a measurement — nothing here knows the service's real lag, so
    the record says what it actually took."""
    assert _stop(_Venue(visible_on=2))["confirm_attempts"] == 2


def test_an_order_that_never_appears_still_fails_closed():
    """The budget is bounded: a genuinely failed placement must still reach the naked close."""
    venue = _Venue(visible_on=99)
    result = _stop(venue)
    assert result["placed"] is False
    assert result["status"] == BRACKET_QUERY_MISSING
    assert result["may_be_resting"] is True
    assert venue.algo_reads == BRACKET_CONFIRM_ATTEMPTS


def test_it_stops_asking_as_soon_as_the_answer_arrives():
    venue = _Venue(visible_on=1)
    _stop(venue)
    assert venue.reads == 1


def test_it_waits_between_reads_and_not_after_the_last():
    """One fewer sleep than reads: waiting after the final attempt buys nothing and delays the
    close of a position that is genuinely unprotected."""
    waited: list[float] = []
    _stop(_Venue(visible_on=99), sleeper=waited.append)
    assert len(waited) == BRACKET_CONFIRM_ATTEMPTS - 1
    assert waited == list(BRACKET_CONFIRM_BACKOFF_SECONDS)[:len(waited)]


# --- and only where the evidence is ---------------------------------------------

def test_the_plain_target_leg_is_asked_exactly_once():
    """The entry and the resting LIMIT have never shown this. Widening a delay into a path that
    does not need it would slow every cycle to fix a problem it does not have."""
    venue = _Venue(visible_on=99)
    result = _target(venue)
    assert result["confirm_attempts"] == 1
    assert venue.reads == 1
    assert result["placed"] is False


def test_a_plain_leg_never_waits():
    waited: list[float] = []
    _target(_Venue(visible_on=99), sleeper=waited.append)
    assert waited == []


# --- the rest of the contract is unchanged --------------------------------------

def test_a_query_that_raises_is_still_unreconcilable():
    class _Down(_Venue):
        def fetch_order(self, *a, **k):
            raise ToolError("ORDER_TRANSPORT", "timed out")

    assert _stop(_Down())["status"] == "UNRECONCILABLE"


def test_a_submit_that_named_nothing_is_still_a_plain_not_found():
    """Retried all the same — the race is about the READ, not about what the submit said — but
    with no id to point at, an exhausted budget is an ordinary miss and not a suspected stray."""
    venue = _Venue(visible_on=99, submit_response={})
    result = _stop(venue)
    assert result["status"] == "NOT_FOUND"
    assert result["may_be_resting"] is False
    assert venue.algo_reads == BRACKET_CONFIRM_ATTEMPTS
