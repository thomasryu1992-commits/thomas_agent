"""Settling a position the OPERATOR closed at the venue.

The bracket cannot answer this one. An operator flattening at the venue leaves both protective
legs EXPIRED with no fill, so `settle_venue_closed_position` had nothing to price, left the book
OPEN, and the resulting `EXIT_UNSETTLEABLE` halted live routing everywhere with no path back
that did not involve inventing a price. Measured on this machine 2026-08-08: one manual close,
`live_halt: True`, and the outcome never recorded.

The venue's own fill list is the authority that was always there. What is asserted here is that
reading it does **not** become a licence to guess: every refusal path is pinned, because the
record this produces is what the loss breakers and every R-denominated limit are judged on, and
a wrong one is worse than none.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import live_leg
from runtime.mvp_runtime.crypto.live_leg import exit_fill_from_history

OPENED = "2026-08-07T00:13:51Z"
OPENED_MS = 1786_000_000_000  # replaced in _position(); the helper computes the real value


def _position(quantity=0.001, direction="LONG", entry=64270.0):
    return {
        "symbol": "BTCUSDT",
        "direction": direction,
        "quantity": quantity,
        "entry_price": entry,
        "entry_quote_usdt": round(quantity * entry, 8),
        "opened_at_utc": OPENED,
        "position_id": "live_position_test",
    }


def _ms(iso):
    from runtime.mvp_runtime import timeutil

    return int(timeutil.parse_iso(iso).timestamp() * 1000)


def _fill(side="SELL", qty=0.001, quote=65.0, at="2026-08-08T09:00:00Z", order_id=777):
    return {"side": side, "qty": qty, "quoteQty": quote, "time": _ms(at), "orderId": order_id}


# --- the answer it is supposed to give ----------------------------------------

def test_a_single_closing_fill_prices_the_exit():
    fill, order_id = exit_fill_from_history(_position(), [_fill(quote=65.5)])
    assert order_id == 777
    assert fill == {"avg_price": 65500.0, "cum_quote": 65.5, "executed_qty": 0.001}


def test_a_split_close_sums_into_one_fill():
    """A market close can come back as several fills under one order. They are the same exit."""
    rows = [
        _fill(qty=0.0006, quote=39.0, at="2026-08-08T09:00:00Z"),
        _fill(qty=0.0004, quote=26.0, at="2026-08-08T09:00:01Z"),
    ]
    fill, order_id = exit_fill_from_history(_position(), rows)
    assert fill["executed_qty"] == pytest.approx(0.001)
    assert fill["cum_quote"] == pytest.approx(65.0)
    assert order_id == 777


def test_a_short_position_closes_on_the_buy_side():
    fill, _ = exit_fill_from_history(
        _position(direction="SHORT"), [_fill(side="BUY", quote=63.0)]
    )
    assert fill["cum_quote"] == pytest.approx(63.0)


def test_the_oldest_covering_fills_are_the_close():
    """Later activity on the same symbol does not change what closed THIS position."""
    rows = [
        _fill(qty=0.001, quote=65.0, at="2026-08-08T09:00:00Z", order_id=1),
        _fill(qty=0.001, quote=70.0, at="2026-08-08T12:00:00Z", order_id=2),
    ]
    fill, order_id = exit_fill_from_history(_position(), rows)
    assert order_id == 1 and fill["cum_quote"] == pytest.approx(65.0)


# --- every way it must refuse -------------------------------------------------

@pytest.mark.parametrize("rows,why", [
    (None, "no feed at all"),
    ([], "a feed that reports no fills"),
    ([_fill(side="BUY")], "only opening-side fills"),
    ([_fill(at="2026-08-06T00:00:00Z")], "fills predating the open"),
    ([_fill(qty=0.0004)], "never covers the position quantity"),
    ([_fill(qty=0.004)], "one fill overshoots the position"),
    ([{"side": "SELL", "qty": None, "quoteQty": 65.0, "time": _ms("2026-08-08T09:00:00Z")}],
     "a malformed row"),
    ([_fill(quote=0.0)], "a zero-quote fill will not price"),
])
def test_it_refuses_rather_than_guessing(rows, why):
    assert exit_fill_from_history(_position(), rows) == (None, None), why


def test_an_overshooting_fill_refuses_even_when_an_exact_one_follows():
    """The overshoot is not skipped in favour of a fill that happens to fit. A fill larger than
    the position means the symbol saw activity this function cannot attribute, and picking the
    convenient row out of it would be the guess the whole design refuses."""
    rows = [
        _fill(qty=0.004, quote=260.0, at="2026-08-08T09:00:00Z"),
        _fill(qty=0.001, quote=65.0, at="2026-08-08T09:00:05Z"),
    ]
    assert exit_fill_from_history(_position(), rows) == (None, None)


def test_a_position_with_no_open_timestamp_refuses():
    """Without an open there is no way to tell a closing fill from an opening one."""
    position = _position()
    position.pop("opened_at_utc")
    assert exit_fill_from_history(position, [_fill()]) == (None, None)


# --- the settle path around it ------------------------------------------------

class _Adapter:
    """Reads bracket legs that expired unfilled — the shape a manual close leaves behind."""

    def get_order(self, **kwargs):
        return {"status": "EXPIRED", "orderId": 1, "executedQty": "0", "cumQuote": "0"}

    def cancel_order(self, **kwargs):
        return {"status": "CANCELED"}


class _Feed:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def fill_history(self, symbol, *, start_ms, timeout_seconds):
        self.calls += 1
        return self.rows


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


def _legs_unfilled():
    return {"status": live_leg.UNPROTECTED, "legs": [
        {"leg": "stop_client_order_id", "close_reason": live_leg.CLOSE_REASON_STOP,
         "filled": False, "resting": False, "status": "EXPIRED", "exchange_order_id": 1,
         "fill": {"avg_price": 0.0, "cum_quote": None, "executed_qty": None}},
    ]}


def test_the_settle_path_prices_an_external_close_from_the_history():
    ledger, store = _Ledger(), _Store()
    result = live_leg.settle_venue_closed_position(
        _position(), adapter=_Adapter(), position_store=store, ledger=ledger,
        legs=_legs_unfilled(), account_feed=_Feed([_fill(quote=65.0)]),
        now="2026-08-08T10:00:00Z",
    )
    assert result["status"] == live_leg.EXIT_CLOSED
    assert result["exit_source"] == live_leg.EXIT_SOURCE_FILL_HISTORY
    assert store.cleared == ["BTCUSDT"]
    outcome = ledger.outcomes[0]
    # The exit was a real profit: 0.001 BTC in at 64270 (64.27 quote) out at 65.00 quote.
    assert outcome["realized_pnl_usdt"] == pytest.approx(0.73, abs=1e-6)
    assert outcome["close_reason"] == live_leg.CLOSE_REASON_VENUE_EXTERNAL


def test_an_external_close_is_not_filed_as_a_strategy_exit():
    """`venue_external_close` is deliberately not one of the strategy close reasons. Aggregating
    an operator's decision into `stop_loss` or `time_exit` would put it in the population the R
    statistics use to judge a strategy."""
    assert live_leg.CLOSE_REASON_VENUE_EXTERNAL not in {
        live_leg.CLOSE_REASON_STOP, live_leg.CLOSE_REASON_TARGET,
        live_leg.CLOSE_REASON_TIME_EXIT, live_leg.CLOSE_REASON_NAKED,
        live_leg.CLOSE_REASON_UNPROTECTED,
    }


def test_no_account_feed_stays_unsettleable_and_says_which_kind():
    ledger, store = _Ledger(), _Store()
    result = live_leg.settle_venue_closed_position(
        _position(), adapter=_Adapter(), position_store=store, ledger=ledger,
        legs=_legs_unfilled(), account_feed=None, now="2026-08-08T10:00:00Z",
    )
    assert result["status"] == live_leg.EXIT_UNSETTLEABLE
    assert live_leg.FILL_HISTORY_UNAVAILABLE in result["reason_codes"]
    assert live_leg.VENUE_CLOSE_UNSETTLEABLE in result["reason_codes"]
    assert store.cleared == [] and ledger.outcomes == []


def test_an_inconclusive_history_stays_unsettleable_and_keeps_the_book():
    """The property that matters most: refusing leaves the position OPEN, which keeps
    reconciliation refusing entries on the symbol. Clearing it against a guess is the failure
    this whole path is shaped to avoid."""
    ledger, store = _Ledger(), _Store()
    result = live_leg.settle_venue_closed_position(
        _position(), adapter=_Adapter(), position_store=store, ledger=ledger,
        legs=_legs_unfilled(), account_feed=_Feed([_fill(qty=0.0004)]),
        now="2026-08-08T10:00:00Z",
    )
    assert result["status"] == live_leg.EXIT_UNSETTLEABLE
    assert live_leg.FILL_HISTORY_INCONCLUSIVE in result["reason_codes"]
    assert store.cleared == [] and ledger.outcomes == []


def test_a_filled_bracket_leg_still_wins_and_never_reads_the_history():
    """The fallback is a fallback. When the bracket answered, nothing else is consulted — the
    leg that filled IS the exit, and its close reason is the strategy's."""
    legs = {"status": live_leg.UNPROTECTED, "legs": [
        {"leg": "stop_client_order_id", "close_reason": live_leg.CLOSE_REASON_STOP,
         "filled": True, "resting": False, "status": "FILLED", "exchange_order_id": 42,
         "fill": {"avg_price": 63573.8, "cum_quote": 63.5738, "executed_qty": 0.001}},
    ]}
    feed = _Feed([_fill(quote=65.0)])
    ledger, store = _Ledger(), _Store()
    result = live_leg.settle_venue_closed_position(
        _position(), adapter=_Adapter(), position_store=store, ledger=ledger,
        legs=legs, account_feed=feed, now="2026-08-08T10:00:00Z",
    )
    assert result["status"] == live_leg.EXIT_CLOSED
    assert feed.calls == 0, "the history was read despite a filled leg"
    assert ledger.outcomes[0]["close_reason"] == live_leg.CLOSE_REASON_STOP
    assert "exit_source" not in result
