"""The protective stop, proved against the venue with nothing at risk.

The bracket has never once been exercised end to end against the real venue. The three clean
canaries the live-promotion gate required are entry-only by construction, so they proved
signing, submission and reconciliation — and on 2026-08-02 the thing that was broken was none
of those. `scripts/verify_algo_stop` closes that gap by placing the real leg and withdrawing it.

Its safety argument is short and these tests are mostly about the places it could quietly stop
being true:

- it cannot fill because there is no position for a Close-All to close, so FLAT is checked at
  the venue rather than assumed, and an unreadable account refuses;
- it runs the REAL place/cancel path, because a verification that exercises different code
  proves the wrong thing;
- whatever it places it withdraws, and a failed cancel exits non-zero — an order left resting
  at the venue must never read as a pass.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto.account import AccountPosition, AccountSnapshot
from runtime.mvp_runtime.errors import ToolError
from scripts import verify_algo_stop as verify

NOW = "2026-08-03T04:00:00Z"


class _Adapter:
    """A venue that accepts, rests and cancels — or fails exactly where a test wants it to."""

    network_egress = True

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
        return {"symbol": symbol, "status": self._status, "orderId": 1,
                "closePosition": True, "reduceOnly": False, "executedQty": 0.0}

    def cancel_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
        if self._cancel_error:
            raise ToolError(self._cancel_error, "scripted cancel failure")
        self.cancelled.append(str(client_order_id))
        return {"status": "CANCELED"}


_UNSET = object()


def _run(monkeypatch, *, adapter=None, price=2000.0, positions=(), argv=None,
         control_active=True, phrase=True, snapshot=_UNSET):
    adapter = adapter if adapter is not None else _Adapter()
    if snapshot is _UNSET:
        snapshot = AccountSnapshot(
            asset="USDT", wallet_balance=1000.0, margin_balance=1000.0,
            available_balance=1000.0, unrealized_pnl=0.0, positions=list(positions),
            realized_windows={}, source="test", collected_at=NOW,
        )
    monkeypatch.setattr(verify, "select_order_adapter", lambda **k: adapter)
    monkeypatch.setattr(verify, "select_market_data_collector", lambda **k: object())
    monkeypatch.setattr(verify, "read_reference_price",
                        lambda *a, **k: (price, None) if price else (None, "NO_PRICE"))
    monkeypatch.setattr(verify, "read_account", lambda **k: (snapshot, {}))
    monkeypatch.setattr(verify, "ControlStore",
                        lambda root: type("S", (), {
                            "load": lambda self: type("C", (), {
                                "execution_allowed": control_active, "mode": "ACTIVE"})()})())
    if phrase:
        monkeypatch.setenv(verify.CANARY_CONFIRMATION_ENV, verify.CANARY_CONFIRMATION_PHRASE)
    else:
        monkeypatch.delenv(verify.CANARY_CONFIRMATION_ENV, raising=False)
    return verify.main(argv or ["--symbol", "ETHUSDT"]), adapter


# --- the safety argument -------------------------------------------------------

def test_an_open_position_in_the_symbol_refuses(monkeypatch, capsys):
    """The one case the safety argument does not cover: with a position open, a closePosition
    stop CAN fill."""
    open_eth = AccountPosition(symbol="ETHUSDT", side="SHORT", quantity=0.031,
                               entry_price=2000.0, mark_price=2000.0, unrealized_pnl=0.0,
                               leverage=1.0, notional=62.0)
    rc, adapter = _run(monkeypatch, positions=[open_eth])
    assert rc == verify.EXIT_BLOCKED
    assert adapter.submitted == []
    assert "is open" in capsys.readouterr().out


def test_a_position_in_another_symbol_does_not_block(monkeypatch):
    other = AccountPosition(symbol="BTCUSDT", side="LONG", quantity=0.001, entry_price=60000.0,
                            mark_price=60000.0, unrealized_pnl=0.0, leverage=1.0, notional=60.0)
    rc, adapter = _run(monkeypatch, positions=[other])
    assert rc == verify.EXIT_OK
    assert len(adapter.submitted) == 1


def test_an_unreadable_account_refuses_rather_than_assuming_flat(monkeypatch, capsys):
    rc, adapter = _run(monkeypatch, snapshot=None)
    assert rc == verify.EXIT_BLOCKED
    assert adapter.submitted == []
    assert "could not be read" in capsys.readouterr().out


def test_a_halted_runtime_refuses(monkeypatch):
    rc, adapter = _run(monkeypatch, control_active=False)
    assert rc == verify.EXIT_BLOCKED
    assert adapter.submitted == []


def test_the_confirmation_phrase_is_required_to_reach_the_venue(monkeypatch, capsys):
    rc, adapter = _run(monkeypatch, phrase=False)
    assert rc == verify.EXIT_BLOCKED
    assert adapter.submitted == []
    assert verify.CANARY_CONFIRMATION_ENV in capsys.readouterr().out


def test_no_price_refuses_rather_than_skipping_the_check(monkeypatch, capsys):
    rc, adapter = _run(monkeypatch, price=None)
    assert rc == verify.EXIT_BLOCKED
    assert adapter.submitted == []
    assert "no reference price" in capsys.readouterr().out


@pytest.mark.parametrize("pct", [0, 1.0, -5])
def test_a_trigger_too_close_to_the_market_is_refused(monkeypatch, pct):
    rc, adapter = _run(monkeypatch, argv=["--symbol", "ETHUSDT", "--distance-pct", str(pct)])
    assert rc == verify.EXIT_USAGE
    assert adapter.submitted == []


# --- it exercises the real path ------------------------------------------------

def test_it_sends_the_bracket_leg_the_money_path_sends(monkeypatch):
    """A verification that exercises different code proves the wrong thing."""
    rc, adapter = _run(monkeypatch)
    assert rc == verify.EXIT_OK
    sent = adapter.submitted[0]
    assert sent["type"] == "STOP_MARKET"
    assert sent["algoType"] == "CONDITIONAL"        # routed to the Algo endpoint
    assert sent["closePosition"] == "true"
    assert "quantity" not in sent and "reduceOnly" not in sent
    assert sent["workingType"] == "MARK_PRICE"


def test_the_trigger_sits_the_requested_distance_above_the_market(monkeypatch):
    _rc, adapter = _run(monkeypatch, price=2000.0,
                        argv=["--symbol", "ETHUSDT", "--distance-pct", "10"])
    assert adapter.submitted[0]["triggerPrice"] == pytest.approx(2200.0)


# --- it never leaves anything behind -------------------------------------------

def test_a_placed_stop_is_withdrawn(monkeypatch, capsys):
    rc, adapter = _run(monkeypatch)
    assert rc == verify.EXIT_OK
    assert adapter.cancelled == [adapter.submitted[0]["clientAlgoId"]]
    assert "was withdrawn" in capsys.readouterr().out


def test_a_cancel_that_fails_exits_non_zero_and_names_the_order(monkeypatch, capsys):
    """An order this tool left resting must never read as a pass — it counts against the
    venue's per-symbol conditional cap until someone withdraws it."""
    rc, adapter = _run(monkeypatch, adapter=_Adapter(cancel_error="ORDER_REJECTED"))
    assert rc == verify.EXIT_LEFT_BEHIND
    out = capsys.readouterr().out
    assert "LEFT BEHIND" in out
    assert adapter.submitted[0]["clientAlgoId"] in out


def test_a_cancel_is_attempted_even_when_the_submit_reported_failure(monkeypatch):
    """A submit reported as failed may still have landed, so the withdrawal is attempted on
    the strength of the VENUE's answer rather than the submit's."""
    adapter = _Adapter(submit_error="ORDER_REJECTED", missing=True)
    rc, adapter = _run(monkeypatch, adapter=adapter)
    assert rc == verify.EXIT_BLOCKED          # the venue confirms it did not land
    assert adapter.cancelled                  # ...and it still tried to withdraw


def test_a_rejected_submit_that_actually_landed_counts_as_placed(monkeypatch):
    """`place_bracket_leg`'s posture, and worth pinning here: a rejection is informative but
    not conclusive, so the venue is asked. A stop that is resting IS protection, whatever the
    submit call returned."""
    adapter = _Adapter(submit_error="ORDER_REJECTED")   # ...but fetch finds it resting
    rc, adapter = _run(monkeypatch, adapter=adapter)
    assert rc == verify.EXIT_OK
    assert adapter.cancelled


def test_a_refused_stop_reports_what_the_venue_said(monkeypatch, capsys):
    rc, _ = _run(monkeypatch, adapter=_Adapter(submit_error="ORDER_REJECTED", missing=True))
    assert rc == verify.EXIT_BLOCKED
    assert "did NOT place" in capsys.readouterr().out


def test_a_stop_that_does_not_rest_is_not_a_pass(monkeypatch):
    """Only NEW means the bracket is in place. Anything else and the position would not have
    been protected the way the decision assumed."""
    rc, _ = _run(monkeypatch, adapter=_Adapter(status="EXPIRED"))
    assert rc == verify.EXIT_BLOCKED
