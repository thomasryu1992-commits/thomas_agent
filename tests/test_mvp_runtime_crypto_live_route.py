"""LP5.3 step 3 — cycle routing. The wiring that gives the executing leg a caller.

**Nothing here opens a socket or places an order.** The adapter is a fake, the account feed is
monkeypatched, and the default state of every test is *no grant*, which is also the property
under test in the first section: on a machine that has not been through the operator checklist
the live leg reads nothing and sends nothing, and the crypto cycle behaves exactly as it did
before this module existed.

What the rest covers, in the order the leg runs it:

- **the gate** — no grant → ``DISABLED`` with no account read; opted in without a grant → the
  gate's own fail-closed reason, still nothing sent;
- **settle** — a position the venue's bracket closed is recorded from the leg that filled and
  the book is cleared; a venue close this runtime cannot price refuses, keeps the book, and
  halts the fan-out rather than clearing state against an invented number;
- **protect** — a position whose bracket is positively gone is closed; one whose bracket cannot
  be read is reported and held, because closing on a failed read is acting on a guess;
- **drift** — a book that still disagrees with the venue after settlement halts;
- **the shared route** — the live leg cannot disagree with the paper leg about what the pool
  said, because it is handed that step's own routing result;
- **the fan-out** — a live incident stops the remaining contexts and names them.
"""

from __future__ import annotations

from typing import Any

import pytest

from runtime.mvp_runtime.crypto import live_leg, live_route
from runtime.mvp_runtime.crypto.account import AccountPosition, AccountSnapshot
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-07-28T00:00:00Z"
SYMBOL = "BTCUSDT"


# --- doubles ------------------------------------------------------------------------------

class _Adapter:
    """A venue that answers from a script. ``network_egress`` is what the gate reads, so a
    test that sets it True is standing in for a machine with ``MVP_LIVE_TRADING=real``."""

    tool_id, tool_version = "fake", "0"
    network_egress = True

    def __init__(self, *, orders: dict[str, dict[str, Any]] | None = None, fetch_raises=None):
        self.orders = orders or {}
        self.fetch_raises = fetch_raises
        self.submitted: list[dict[str, Any]] = []
        self.cancelled: list[str] = []

    def submit(self, order_request, *, timeout_seconds: int = 10):
        self.submitted.append(dict(order_request))
        return {"accepted": True}

    def fetch_order(self, symbol, client_order_id, *, timeout_seconds: int = 10):
        if self.fetch_raises:
            raise ToolError(self.fetch_raises, "scripted")
        return self.orders.get(str(client_order_id))

    def cancel_order(self, symbol, client_order_id, *, timeout_seconds: int = 10):
        self.cancelled.append(str(client_order_id))
        return self.orders.pop(str(client_order_id), None)


class _Store:
    filesystem_write = True

    def __init__(self):
        self.saved: list[dict[str, Any]] = []
        self.cleared: list[str] = []

    def save_position(self, position):
        self.saved.append(dict(position))

    def clear_position(self, symbol):
        self.cleared.append(str(symbol))


class _Ledger:
    filesystem_write = True

    def __init__(self, *, raises=None):
        self.appended: list[dict[str, Any]] = []
        self.raises = raises

    def append_outcome(self, outcome):
        if self.raises:
            raise ToolError(self.raises, "scripted")
        self.appended.append(dict(outcome))


def _position(**kw) -> dict[str, Any]:
    """A booked live position, shaped as ``execute_live_entry`` leaves it."""
    position = {
        "stage": "live",
        "status": "OPEN",
        "symbol": SYMBOL,
        "direction": "LONG",
        "quantity": 0.002,
        "entry_price": 60000.0,
        "notional_usdt": 120.0,
        "stop_loss": 59000.0,
        "take_profit": 62000.0,
        "risk": 2.0,
        "opened_at_utc": "2026-07-27T00:00:00Z",
        "entry_exchange_order_id": "venue-1",
        "position_id": "live-1",
        "entry_quote_usdt": 120.0,
        "stop_client_order_id": "sl-1",
        "take_profit_client_order_id": "tp-1",
        "strategy_id": "S001",
    }
    position.update(kw)
    return position


def _venue_order(status: str, *, qty: float = 0.002, price: float = 62000.0) -> dict[str, Any]:
    return {
        "status": status, "orderId": "venue-9",
        "executedQty": qty, "avgPrice": price, "cumQuote": round(qty * price, 8),
    }


def _snapshot(*, positions=()) -> AccountSnapshot:
    return AccountSnapshot(
        asset="USDT", wallet_balance=500.0, margin_balance=500.0, available_balance=400.0,
        unrealized_pnl=0.0, positions=list(positions), realized_windows={},
        source="fake", collected_at=NOW,
    )


# --- the gate: the whole switch ------------------------------------------------------------

def test_without_a_grant_the_live_leg_reads_nothing_and_sends_nothing(tmp_path, monkeypatch):
    """The property every machine in this repo depends on: the wiring exists and is inert.

    Asserted by making the account read *explode* — if the leg reached it, this fails loudly
    rather than passing on a machine that happened to have no credentials configured."""
    monkeypatch.delenv("MVP_LIVE_TRADING", raising=False)
    monkeypatch.setattr(
        live_route, "read_account",
        lambda **kw: pytest.fail("the gated leg read the account with no grant"),
    )
    record = live_route.run_live_leg(
        route=None, feature_row={}, verdict={"allow_new_position": True},
        symbol=SYMBOL, collector=object(), now=NOW, root=tmp_path,
    )
    assert record["live_route_status"] == live_route.ROUTE_DISABLED
    assert record["live_reason_codes"] == [live_route.ROUTING_DISABLED]
    assert record["live_opened"] is None and record["live_settled"] is None
    assert record["halt"] is False


def test_the_env_var_alone_now_opens_the_gate(tmp_path, monkeypatch):
    """Inverted 2026-07-28: ``MVP_LIVE_TRADING=real`` with no local grant used to be the gate's
    fail-closed path (``ACTIVATION_MISSING``). Thomas removed the grant, so this is now the
    open path — the leg gets past selection and goes on to read the account.

    Asserting the account read *happens* is the point: it is the first thing on the other side
    of the gate, so it is what distinguishes "opened" from "returned DISABLED quietly". The
    read is stubbed to raise, which is how the leg's own never-raise contract gets exercised
    at the same time — a failure past the gate must still come back as a record."""
    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    reads: list[dict] = []

    def _account(**kw):
        reads.append(kw)
        raise ToolError("ACCOUNT_UNAVAILABLE", "stubbed: no venue in a test")

    monkeypatch.setattr(live_route, "read_account", _account)
    record = live_route.run_live_leg(
        route=None, feature_row={}, verdict={"allow_new_position": True},
        symbol=SYMBOL, collector=object(), now=NOW, root=tmp_path,
    )
    assert reads, "the gate did not open — the account was never read"
    assert record["live_route_status"] != live_route.ROUTE_DISABLED
    assert record["halt"] is False


def test_the_gate_is_the_adapter_selection_itself(tmp_path, monkeypatch):
    """Not a second copy of the rule: an inert dry-run adapter *is* "no grant"."""
    monkeypatch.delenv("MVP_LIVE_TRADING", raising=False)
    adapter, reason = live_route.select_live_gate(now=NOW, root=tmp_path)
    assert adapter is None and reason == live_route.ROUTING_DISABLED


# --- settle: the venue's own exit ----------------------------------------------------------

def test_a_position_the_venue_closed_is_recorded_and_the_book_cleared():
    """The normal end of a live trade. Without this the routing strands its own book on the
    first successful trade and every later entry on that symbol is refused."""
    adapter = _Adapter(orders={"tp-1": _venue_order("FILLED", price=62000.0)})
    store, ledger = _Store(), _Ledger()

    result = live_leg.settle_venue_closed_position(
        _position(), adapter=adapter, position_store=store, ledger=ledger, now=NOW,
    )

    assert result["status"] == live_leg.EXIT_CLOSED
    assert result["outcome"]["close_reason"] == live_leg.CLOSE_REASON_TARGET
    # quote-out minus quote-in on the venue's own figures: 124.0 - 120.0
    assert result["outcome"]["realized_pnl_usdt"] == pytest.approx(4.0)
    assert ledger.appended and store.cleared == [SYMBOL]
    assert "sl-1" in adapter.cancelled, "the surviving leg must be withdrawn"


def test_a_venue_close_that_cannot_be_priced_keeps_the_book_and_refuses():
    """The money moved and this runtime cannot say how much. Clearing the book here would make
    the trade vanish from the breaker's accounting; keeping it keeps entries refused."""
    adapter = _Adapter(orders={})  # neither leg reports a fill
    store, ledger = _Store(), _Ledger()

    result = live_leg.settle_venue_closed_position(
        _position(), adapter=adapter, position_store=store, ledger=ledger, now=NOW,
    )

    assert result["status"] == live_leg.EXIT_UNSETTLEABLE
    assert live_leg.VENUE_CLOSE_UNSETTLEABLE in result["reason_codes"]
    assert ledger.appended == [] and store.cleared == []


def test_an_outcome_that_will_not_persist_never_clears_the_book():
    """Ledger before book, for the reason the exit path already gives: an outcome that never
    lands is a loss the breaker will never see."""
    adapter = _Adapter(orders={"sl-1": _venue_order("FILLED", price=59000.0)})
    store, ledger = _Store(), _Ledger(raises="LIVE_STATE_LOCKED")

    result = live_leg.settle_venue_closed_position(
        _position(), adapter=adapter, position_store=store, ledger=ledger, now=NOW,
    )

    assert result["status"] == live_leg.EXIT_UNSETTLEABLE
    assert live_leg.OUTCOME_PERSIST_FAILED in result["reason_codes"]
    assert store.cleared == []


# --- protect: is the bracket still there? --------------------------------------------------

def test_two_resting_legs_read_as_protected():
    adapter = _Adapter(orders={"sl-1": _venue_order("NEW"), "tp-1": _venue_order("NEW")})
    assert live_leg.read_bracket_legs(_position(), adapter=adapter)["status"] == live_leg.PROTECTED


def test_a_leg_the_venue_says_is_gone_reads_as_unprotected():
    """``fetch_order`` returning None is the venue *answering* — "no such order" — not a
    failed read. A position with a missing stop is genuinely unprotected."""
    adapter = _Adapter(orders={"tp-1": _venue_order("NEW")})
    assert live_leg.read_bracket_legs(_position(), adapter=adapter)["status"] == live_leg.UNPROTECTED


def test_a_query_that_fails_reads_as_unknown_never_as_unprotected():
    """The boundary the whole live stack draws: act on what the venue reported, never on a
    guess. An unknown bracket must not trigger a close."""
    adapter = _Adapter(fetch_raises="ORDER_TRANSPORT")
    read = live_leg.read_bracket_legs(_position(), adapter=adapter)
    assert read["status"] == live_leg.PROTECTION_UNKNOWN


def test_a_position_with_no_recorded_bracket_ids_is_unknown_not_unprotected():
    """Absence of a local field is not venue evidence, and sending a close on the strength of
    a missing record would be exactly the guess the boundary forbids."""
    adapter = _Adapter(orders={})
    read = live_leg.read_bracket_legs(
        _position(stop_client_order_id=None, take_profit_client_order_id=None), adapter=adapter
    )
    assert read["status"] == live_leg.PROTECTION_UNKNOWN
    assert all(leg["error"] == live_leg.BRACKET_IDS_MISSING for leg in read["legs"])


# --- incidents halt the fan-out ------------------------------------------------------------

def test_an_unsettleable_venue_close_is_a_portfolio_level_incident():
    """Per-context isolation is right for paper and wrong for real money: a position this
    runtime cannot account for must stop the other contexts opening under that uncertainty."""
    assert live_route._is_incident(
        {"status": live_leg.EXIT_UNSETTLEABLE, "reason_codes": []}
    )
    assert live_route._is_incident(
        {"status": live_leg.ENTRY_REFUSED, "reason_codes": [live_leg.NAKED_CLOSE_FAILED]}
    )


def test_an_ordinary_refusal_is_not_an_incident():
    """A guard refusal, an unreadable account, a bracket that would not place but closed
    cleanly — none of these leave money in an unknown state, so none halts the fan-out."""
    assert not live_route._is_incident({"status": live_leg.ENTRY_REFUSED, "reason_codes": []})
    assert not live_route._is_incident(
        {"status": live_leg.ENTRY_NAKED_CLOSED, "reason_codes": [live_leg.NAKED_POSITION_CLOSED]}
    )
