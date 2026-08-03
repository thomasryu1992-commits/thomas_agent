"""Asking the venue why it refused a protective stop, without creating an order.

On 2026-08-02 the runtime's first two autonomous entries both filled and both had their
``closePosition`` STOP_MARKET leg refused. The safety path worked — the surviving target was
cancelled and the exposure closed, twice — but the record kept only ``error: ORDER_REJECTED``,
the same string for every rejection there is, so the runtime cannot hold a live position and
nobody knows why.

Reaching the next rejection costs a real entry and a real close, only happens when a strategy
routes, and is capped at two orders a day. ``POST /fapi/v1/order/test`` asks the identical
question for free.

What these pin is mostly about not producing a confident wrong answer:

- the validated request is built through the SAME path the money path uses, so a shape that
  passes here is the shape that would be sent;
- a rejection is RETURNED, not raised — putting the finding in an exception's text is exactly
  how the cause was lost the first time;
- "the venue refused it", "I could not ask", and "nothing was asked because this is a dry run"
  are three different answers and never collapse into one.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import live_execution as lx
from runtime.mvp_runtime.crypto.live_execution import (
    DryRunOrderAdapter,
    build_order_request,
)
from runtime.mvp_runtime.crypto.live_leg import build_bracket_intent
from runtime.mvp_runtime.errors import MvpRuntimeError
from scripts import diagnose_bracket_leg as diag


# --- the request is the real one -----------------------------------------------

def test_the_validated_request_is_what_the_money_path_would_send():
    """A hand-rolled dict would test a request the runtime does not make — worse than not
    testing, because it answers confidently and wrongly."""
    args = diag.argparse.Namespace(**diag.INCIDENT)
    legs = diag._legs(args)
    stop = build_order_request(legs[0])
    expected = build_order_request(build_bracket_intent(
        symbol="ETHUSDT", leg="SL", side="BUY", price=1907.98,
        working_type="MARK_PRICE", position_seed=stop["newClientOrderId"].rsplit("_", 1)[0],
    ))
    assert {k: v for k, v in stop.items() if k != "newClientOrderId"} == {
        k: v for k, v in expected.items() if k != "newClientOrderId"}


def test_the_incident_request_is_reproduced_field_for_field():
    """The literal request the venue refused, from cycle crypto_cycle_2e06c80e7e96ee7d20ac.
    If any of these drift, the diagnosis stops being about the thing that failed."""
    stop = build_order_request(diag._legs(diag.argparse.Namespace(**diag.INCIDENT))[0])
    stop.pop("newClientOrderId")
    assert stop == {
        "symbol": "ETHUSDT", "side": "BUY", "type": "STOP_MARKET",
        "stopPrice": 1907.98, "workingType": "MARK_PRICE", "closePosition": "true",
    }


def test_the_target_leg_rides_along_as_the_control():
    """Both legs refused says something about the account or the key; only the stop refused
    says something about the stop. Without the control the two are indistinguishable."""
    legs = diag._legs(diag.argparse.Namespace(**diag.INCIDENT))
    assert [leg["order_type_exchange"] for leg in legs] == ["STOP_MARKET", "LIMIT"]


def test_a_long_closes_with_a_sell_on_both_legs():
    args = diag.argparse.Namespace(**{**diag.INCIDENT, "direction": "LONG"})
    assert {leg["side"] for leg in diag._legs(args)} == {"SELL"}


# --- a rejection is data, not an exception -------------------------------------

class _Venue:
    """The adapter's HTTP seam: `_signed_request` returns (body, code)."""

    network_egress = True

    def __init__(self, body, code):
        self._answer = (body, code)
        self.paths: list[str] = []

    def _signed_request(self, method, path, params, *, timeout_seconds=10):
        self.paths.append(path)
        return self._answer


def _adapter(body, code):
    adapter = lx.BinanceFuturesOrderAdapter.__new__(lx.BinanceFuturesOrderAdapter)
    venue = _Venue(body, code)
    adapter._signed_request = venue._signed_request  # type: ignore[method-assign]
    return adapter, venue


def test_a_venue_rejection_comes_back_as_a_value_with_its_code_and_message():
    adapter, _ = _adapter({"code": -2021, "msg": "Order would immediately trigger."}, -2021)
    verdict = adapter.validate_order({"symbol": "ETHUSDT"})
    assert verdict == {"accepted": False, "code": -2021, "msg": "Order would immediately trigger."}


def test_an_accepted_request_says_so_without_a_code():
    adapter, _ = _adapter({}, None)
    assert adapter.validate_order({"symbol": "ETHUSDT"}) == {
        "accepted": True, "code": None, "msg": None}


def test_it_asks_the_validation_endpoint_and_never_the_order_endpoint():
    """The load-bearing safety property: this cannot create an order because it does not talk
    to the endpoint that creates one."""
    adapter, venue = _adapter({}, None)
    adapter.validate_order({"symbol": "ETHUSDT"})
    assert venue.paths == [lx.ORDER_TEST_PATH]
    assert lx.ORDER_PATH not in venue.paths


def test_a_transport_failure_still_raises():
    """'The venue refused it' and 'I could not ask' must not arrive as the same value."""
    adapter = lx.BinanceFuturesOrderAdapter.__new__(lx.BinanceFuturesOrderAdapter)

    def _boom(*a, **k):
        raise MvpRuntimeError(lx.ORDER_TRANSPORT, "live order request failed or timed out")

    adapter._signed_request = _boom  # type: ignore[method-assign]
    with pytest.raises(MvpRuntimeError):
        adapter.validate_order({"symbol": "ETHUSDT"})


# --- the dry run cannot be misread ---------------------------------------------

def test_the_inert_adapter_marks_its_answer_as_unasked():
    """`accepted: True` from a dry run means nothing was checked. Without the flag a caller
    printing this would report an unasked question as a passing one."""
    verdict = DryRunOrderAdapter().validate_order({"symbol": "ETHUSDT"})
    assert verdict["accepted"] is True
    assert verdict["dry_run"] is True


def test_the_cli_says_nothing_was_asked_when_the_gate_is_shut(monkeypatch, capsys):
    monkeypatch.delenv(lx.LIVE_TRADING_ENV, raising=False)
    assert diag.main([]) == diag.EXIT_OK
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "nothing was asked" in out
    assert "not the venue's" in out


def test_the_cli_defaults_to_the_incident_and_reports_both_legs(monkeypatch, capsys):
    monkeypatch.delenv(lx.LIVE_TRADING_ENV, raising=False)
    diag.main([])
    out = capsys.readouterr().out
    assert "STOP_MARKET" in out and "LIMIT" in out
    assert "'stopPrice': 1907.98" in out
    assert "'closePosition': 'true'" in out


def test_a_rejection_exits_non_zero(monkeypatch, capsys):
    """So a scheduled or scripted run cannot report a refusal as success."""
    class _Rejecting:
        network_egress = True

        def validate_order(self, request, *, timeout_seconds=10):
            return {"accepted": False, "code": -4136, "msg": "stop price less than..."}

    monkeypatch.setattr(diag, "select_order_adapter", lambda: _Rejecting())
    assert diag.main([]) == diag.EXIT_REJECTED
    assert "-4136" in capsys.readouterr().out


def test_an_unreachable_venue_is_neither_accepted_nor_rejected(monkeypatch, capsys):
    class _Down:
        network_egress = True

        def validate_order(self, request, *, timeout_seconds=10):
            raise MvpRuntimeError(lx.ORDER_TRANSPORT, "timed out")

    monkeypatch.setattr(diag, "select_order_adapter", lambda: _Down())
    assert diag.main([]) == diag.EXIT_OK      # not a rejection — nothing was learned
    assert "UNREACHABLE" in capsys.readouterr().out
