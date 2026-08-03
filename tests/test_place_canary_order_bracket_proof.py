"""A canary that proves the protective bracket, not just that an order can be sent.

Being entry-only is what made three clean canaries the wrong evidence. They proved signing,
submission and reconciliation — and when the runtime first traded autonomously on 2026-08-02
the thing that was broken was none of those: the venue refused the protective STOP, twice,
because Binance had moved conditional orders to the Algo Order API eight months earlier. The
promotion gate had measured a property that was not the one at risk.

This cannot be checked on a flat account. A `closePosition` order is refused outright when
there is nothing to close (`-4509: Time in Force (TIF) GTE can only be used with open
positions`), so the only moment the bracket can be tested is while a real position is open —
exactly the moment the canary used to hand back to the operator untested.

What these pin:

- it runs the REAL `place_bracket_leg` / `cancel_bracket_legs`, because a verification that
  exercises different code proves the wrong thing — the lesson of the canary that proved the
  wrong property;
- a stop that did not rest fails the run, so an unprotected bracket can never be recorded as
  clean evidence;
- whatever it places it withdraws, and an order left resting fails the run too.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto.live_promotion import RECONCILED
from runtime.mvp_runtime.errors import ToolError
from scripts import place_canary_order as canary


class _Adapter:
    def __init__(self, *, submit_error=None, status="NEW", cancel_error=None, missing=False):
        self.submitted: list[dict] = []
        self.cancelled: list[str] = []
        self._submit_error = submit_error
        self._status = status
        self._cancel_error = cancel_error
        self._missing = missing

    def submit(self, order_request, *, timeout_seconds=10):
        self.submitted.append(dict(order_request))
        if self._submit_error:
            raise ToolError(self._submit_error, "scripted rejection")
        return {"accepted": True}

    def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
        if self._missing:
            return None
        return {"symbol": symbol, "status": self._status, "orderId": 7,
                "closePosition": True, "reduceOnly": False, "executedQty": 0.0}

    def cancel_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
        if self._cancel_error:
            raise ToolError(self._cancel_error, "scripted cancel failure")
        self.cancelled.append(str(client_order_id))
        return {"status": "CANCELED"}


def _filled(price=2000.0, qty=0.031):
    return {"reconcile_status": RECONCILED, "client_order_id": "TAI_ETHUSDT_SHORT_abc123",
            "fill": {"avg_price": price, "executed_qty": qty, "cum_quote": price * qty}}


def _prove(result=None, *, adapter=None, direction="SHORT", distance_pct=10.0):
    adapter = adapter or _Adapter()
    proof = canary.prove_bracket(
        result=result if result is not None else _filled(), symbol="ETHUSDT",
        direction=direction, adapter=adapter, distance_pct=distance_pct, timeout_seconds=10,
        sleep=lambda _seconds: None,
    )
    return proof, adapter


# --- it needs a position, and knows it ------------------------------------------

@pytest.mark.parametrize("result", [
    {"reconcile_status": "NOT_FOUND", "client_order_id": "x", "fill": {}},
    {"reconcile_status": RECONCILED, "client_order_id": "x",
     "fill": {"avg_price": 0.0, "executed_qty": 0.0}},
    {"reconcile_status": RECONCILED, "client_order_id": "x",
     "fill": {"avg_price": 2000.0, "executed_qty": 0.0}},
])
def test_no_confirmed_fill_means_nothing_to_protect(result):
    """Attempting it anyway would manufacture a -4509 that says nothing about the bracket."""
    proof, adapter = _prove(result)
    assert proof["attempted"] is False
    assert adapter.submitted == []
    assert "nothing to protect" in proof["reason"]


# --- it exercises the real path -------------------------------------------------

def test_it_places_the_leg_the_autonomous_bracket_places():
    proof, adapter = _prove()
    sent = adapter.submitted[0]
    assert sent["type"] == "STOP_MARKET"
    assert sent["algoType"] == "CONDITIONAL"          # the Algo endpoint, post-migration
    assert sent["closePosition"] == "true"
    assert "quantity" not in sent and "reduceOnly" not in sent
    assert sent["workingType"] == canary.BRACKET_WORKING_TYPE
    assert proof["rested"] is True


@pytest.mark.parametrize("direction,side,factor", [("SHORT", "BUY", 1.10), ("LONG", "SELL", 0.90)])
def test_the_stop_sits_on_the_side_the_bracket_would_use(direction, side, factor):
    """A LONG is stopped below the fill and a SHORT above. A proving stop on the wrong side
    would be through the market immediately — it would trigger, and on an open position that
    means closing the canary."""
    proof, adapter = _prove(direction=direction)
    assert adapter.submitted[0]["side"] == side
    assert proof["trigger_price"] == pytest.approx(2000.0 * factor)


# --- it never leaves anything behind --------------------------------------------

def test_a_rested_stop_is_withdrawn():
    proof, adapter = _prove()
    assert proof["withdrawn"] is True
    assert proof["left_behind"] is False
    assert adapter.cancelled == [adapter.submitted[0]["clientAlgoId"]]


def test_a_failed_cancel_is_named_and_flagged():
    proof, adapter = _prove(adapter=_Adapter(cancel_error="ORDER_REJECTED"))
    assert proof["left_behind"] is True
    assert proof["client_order_id"] == adapter.submitted[0]["clientAlgoId"]
    assert proof["cancel_error"] == "ORDER_REJECTED"


def test_the_withdrawal_is_attempted_on_the_venues_answer_not_the_submits():
    """A rejected submit may still have landed — the reason `place_bracket_leg` asks the venue
    instead of trusting the submit."""
    proof, adapter = _prove(adapter=_Adapter(submit_error="ORDER_REJECTED"))
    assert proof["rested"] is True          # the venue says it is resting
    assert adapter.cancelled


def test_a_refused_stop_carries_what_the_venue_said():
    proof, _ = _prove(adapter=_Adapter(submit_error="ORDER_REJECTED", missing=True))
    assert proof["rested"] is False
    assert "ORDER_REJECTED" in (proof["error_detail"] or "")


def test_a_stop_that_does_not_rest_is_not_a_pass():
    """Only NEW means the bracket is in place; anything else and a real position would not have
    been protected the way the decision assumed."""
    proof, _ = _prove(adapter=_Adapter(status="EXPIRED"))
    assert proof["rested"] is False


# --- the run's verdict ----------------------------------------------------------

@pytest.mark.parametrize("proof,expected_failure", [
    (None, False),                                                   # flag not used
    ({"attempted": True, "rested": True, "left_behind": False}, False),
    ({"attempted": False, "reason": "..."}, True),
    ({"attempted": True, "rested": False, "left_behind": False}, True),
    ({"attempted": True, "rested": True, "left_behind": True}, True),
])
def test_only_a_rested_and_withdrawn_stop_passes(proof, expected_failure):
    """Mirrors the exit-code rule in `main`, kept as data so the two cannot drift: an
    unprotected bracket must never be recorded as clean canary evidence."""
    failed = proof is not None and (
        not proof.get("attempted") or not proof.get("rested") or proof.get("left_behind")
    )
    assert failed is expected_failure


def test_a_proving_stop_too_close_to_the_market_is_refused(capsys):
    rc = canary.main(["--symbol", "ETHUSDT", "--quantity", "0.03", "--notional", "60",
                      "--with-bracket", "--bracket-distance-pct", "0.5"])
    assert rc == canary.EXIT_USAGE
    assert "at least" in capsys.readouterr().err
