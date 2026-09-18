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

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.crypto import live_leg, live_route, pre_order_gate
from runtime.mvp_runtime.crypto.account import AccountPosition, AccountSnapshot
from runtime.mvp_runtime.crypto.live_order import LIVE_CONFIRMATION_PHRASE, LiveOrderLimits
from runtime.mvp_runtime.errors import ToolError
from tests._helpers import (
    deep_order_book, gate_stage, healthy_optional_data, live_arm_approval, stamped_pool_entry,
)

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

    def fetch_order(self, symbol, client_order_id, *, timeout_seconds: int = 10, algo: bool = False):
        if self.fetch_raises:
            raise ToolError(self.fetch_raises, "scripted")
        return self.orders.get(str(client_order_id))

    def cancel_order(self, symbol, client_order_id, *, timeout_seconds: int = 10, algo: bool = False):
        self.cancelled.append(str(client_order_id))
        return self.orders.pop(str(client_order_id), None)


class _Store:
    filesystem_write = True

    def __init__(self):
        self.saved: list[dict[str, Any]] = []
        self.cleared: list[str] = []

    def save_position(self, position):
        self.saved.append(dict(position))

    def clear_position(self, symbol, *, position_id=None):
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
        live_routable_strategy_ids={"S1"},
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
        live_routable_strategy_ids={"S1"},
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


def test_a_partially_filled_target_reads_as_protected_not_lost():
    """A partial fill of the sized reduceOnly LIMIT target is an ordinary market event: the
    remainder is still working on the book, and the closePosition stop beside it covers
    whatever remains by construction. Reading it as UNPROTECTED sent rule 2's taker
    force-close at the book's stale full quantity against a still-stop-protected position —
    guaranteed MISMATCH, EXIT_NOT_CONFIRMED, and a portfolio-wide halt with the book OPEN.
    The quantity drift a partial fill creates is reconciliation's fact to report
    (BOOK_DRIFT), not this classifier's to punish."""
    adapter = _Adapter(orders={
        "sl-1": _venue_order("NEW"),
        "tp-1": _venue_order("PARTIALLY_FILLED", qty=0.001),
    })
    read = live_leg.read_bracket_legs(_position(), adapter=adapter)
    assert read["status"] == live_leg.PROTECTED
    target = next(leg for leg in read["legs"] if leg["client_order_id"] == "tp-1")
    assert target["resting"] is True
    # A partial fill is not "this leg executed": the settle path must not price an exit off
    # a position that is still open at the venue.
    assert target["filled"] is False


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


# --- the naked close reaches the ledger ------------------------------------------------------
#
# `live_leg`'s entry path takes no ledger: it builds the row, this module persists it. The same
# split `_record_bracket_outcome` already uses, and the reason the recording could be added
# without threading a ledger through the entry signature.

def test_a_naked_close_outcome_is_persisted():
    ledger = _Ledger()
    record = {"live_reason_codes": []}
    live_route._record_entry_outcome(
        record, {"outcome": {"settlement_id": "s1", "realized_pnl_usdt": -0.12}}, ledger=ledger
    )
    assert [o["settlement_id"] for o in ledger.appended] == ["s1"]
    assert record["live_reason_codes"] == []


def test_an_entry_with_no_outcome_appends_nothing():
    # Every other entry result carries `outcome: None` — an opened position is settled later by
    # `execute_live_exit`, which writes its own row.
    ledger = _Ledger()
    record = {"live_reason_codes": []}
    live_route._record_entry_outcome(record, {"outcome": None}, ledger=ledger)
    assert ledger.appended == []


def test_an_outcome_that_will_not_persist_is_reported_and_never_raised():
    """The order is already at the venue and the money has already moved, so the choice is
    between a recorded failure and an unrecorded one — never between recording and raising."""
    ledger = _Ledger(raises="LEDGER_UNWRITABLE")
    record = {"live_reason_codes": []}
    live_route._record_entry_outcome(
        record, {"outcome": {"settlement_id": "s1"}}, ledger=ledger
    )
    assert live_leg.OUTCOME_PERSIST_FAILED in record["live_reason_codes"]
    assert "LEDGER_UNWRITABLE" in record["live_reason_codes"]


# --- the time exit (2026-07-29) --------------------------------------------------------------
#
# Paper has always enforced `max_holding_bars`; live did not, and the promotion evidence gating
# live trading was built WITH it in force. These pin the rule and, more importantly, the two
# properties that make the counter trustworthy: it advances when nothing closes, and one bar
# counts once however often a cycle re-runs inside it.

def _timed(**kw) -> dict[str, Any]:
    """A position carrying the exit terms `build_live_position` now stores."""
    base = {"timeframe": "1d", "max_holding_bars": 3, "holding_candles": 0,
            "last_counted_candle_ts": None}
    base.update(kw)
    return _position(**base)


class _ClosingAdapter(_Adapter):
    """Both bracket legs resting, and any *other* order id answers FILLED.

    The two behaviours together are what a time exit needs: the brackets must still look
    resting (or protection would fail first and the unprotected branch would close it for a
    different reason), while the close this test is about must confirm."""

    def fetch_order(self, symbol, client_order_id, *, timeout_seconds: int = 10, algo: bool = False):
        if self.fetch_raises:
            raise ToolError(self.fetch_raises, "scripted")
        known = self.orders.get(str(client_order_id))
        if known is not None:
            return known
        # The close. Shaped to satisfy `reconcile_order` exactly — symbol, side, FILLED,
        # the intent's quantity, and reduceOnly — because a close that does not reconcile
        # is a different test (`..._will_not_close...`), not this one.
        return {
            "status": "FILLED", "orderId": "venue-close", "symbol": SYMBOL, "side": "SELL",
            "executedQty": 0.002, "avgPrice": 61000.0, "cumQuote": 122.0, "reduceOnly": True,
        }


def _protected_adapter() -> _Adapter:
    """Both bracket legs resting — so protection holds and the time exit is the only door."""
    return _ClosingAdapter(orders={"sl-1": {"status": "NEW"}, "tp-1": {"status": "NEW"}})


# The close guard needs the confirmation phrase from the resolved limits — a time exit is a
# close, so it passes the same door every other close does. Present here so these tests exercise
# the exit rather than the phrase check.
_LIMITS = LiveOrderLimits(
    max_order_notional_usdt=60.0, absolute_max_notional_usdt=200.0, max_daily_order_count=2,
    max_open_notional_usdt=120.0, daily_loss_limit_usdt=20.0,
    confirmation=LIVE_CONFIRMATION_PHRASE,
)


def _settle(position, *, adapter=None, candle_ts="2026-07-28T00:00:00Z", store=None, ledger=None,
            context_timeframe=None):
    """One context's pass over one open position.

    ``context_timeframe`` defaults to the position's OWN timeframe — i.e. the context that owns
    its clock — so the tests below read as "the cycle that opened this position visits it".
    Passing a different one stands in for a sibling timeframe of the same symbol, which is the
    case the ownership rule exists for."""
    if context_timeframe is None:
        context_timeframe = str(position.get("timeframe") or live_route.DEFAULT_TIMING_CONTEXT)
    record = {"live_reason_codes": [], "live_settled": None, "halt": False,
              "live_protection": None}
    live_route._settle_or_protect(
        record, position,
        adapter=adapter or _protected_adapter(),
        position_store=store or _Store(), ledger=ledger or _Ledger(),
        reconciliation={"status": "RECONCILED", "books": {}},
        limits=_LIMITS, candle_ts=candle_ts, context_timeframe=context_timeframe,
        now=NOW, root=None, timeout_seconds=10,
    )
    return record


def test_holding_advances_on_a_cycle_that_closes_nothing():
    """The counter is the rule. If it only moved when something closed, time would never pass
    and the exit would never fire — so the store write is unconditional."""
    store = _Store()
    record = _settle(_timed(), store=store)
    assert record["live_settled"] is None                    # held, as expected
    assert store.saved, "the advanced counter was never persisted"
    assert store.saved[-1]["holding_candles"] == 1
    assert record["live_holding"]["holding_candles"] == 1
    assert record["live_holding"]["max_holding_bars"] == 3


def test_one_bar_counts_once_however_often_the_cycle_reruns():
    """A re-run inside the same interval must not accelerate the exit — paper learned this from
    the source system and live shares the rule via `paper.advance_holding`, not a second copy."""
    position = _timed()
    for _ in range(4):
        store = _Store()
        record = _settle(position, candle_ts="2026-07-28T00:00:00Z", store=store)
        position = store.saved[-1]
        assert record["live_settled"] is None
    assert position["holding_candles"] == 1


def test_the_position_closes_at_market_when_its_time_is_up():
    store, ledger = _Store(), _Ledger()
    record = _settle(_timed(holding_candles=2), store=store, ledger=ledger)
    settled = record["live_settled"]
    assert settled is not None, "the time exit did not fire at max_holding_bars"
    assert settled["intent"]["reduce_only"] is True          # can only shrink, never open
    assert settled["intent"]["close_reason"] == live_leg.CLOSE_REASON_TIME_EXIT
    assert settled["status"] == live_leg.EXIT_CLOSED
    assert ledger.appended, "a closed live position must reach the P&L ledger"
    assert store.cleared == [SYMBOL], "the book must be cleared on a confirmed close"


def test_the_close_reason_is_papers_word_so_the_two_populations_aggregate():
    """A live time exit and a paper time exit are the same strategy rule ending the same way.
    The PRICE differs (paper models the bar close, live pays taker + slippage) and `r_basis`
    carries that; the reason must not differ too, or the buckets split."""
    assert live_leg.CLOSE_REASON_TIME_EXIT == "time_exit"


def test_a_legacy_position_falls_back_and_says_so():
    """A position opened before the record carried exit terms is judged by the timeframe table.
    That is a live/backtest gap, so it is named — never inferred later from a divergent curve."""
    legacy = _position()                                     # no timeframe, no max_holding_bars
    record = _settle(legacy)
    assert record["live_holding"]["legacy_max_hold_fallback"] is True
    assert live_route.LIVE_MAX_HOLD_FALLBACK in record["live_reason_codes"]


def test_a_time_exit_that_will_not_close_is_reported_and_not_a_halt():
    """The one survivable failed close in this module. An UNPROTECTED position that will not
    close is an incident — no stop on real exposure. A time-exit position still has its bracket
    resting, so it is protected, just held too long: report, retry next cycle, do not escalate."""
    adapter = _ClosingAdapter(orders={"sl-1": {"status": "NEW"}, "tp-1": {"status": "NEW"}},
                              fetch_raises="TOOL_TRANSPORT")
    record = _settle(_timed(holding_candles=9), adapter=adapter)
    assert record["halt"] is False
    assert live_route.LIVE_TIME_EXIT_DEFERRED in record["live_reason_codes"]


def test_the_counter_survives_a_close_that_did_not_confirm():
    """The bar that passed still passed. A counter that advanced only on a successful exit would
    reset the clock every time the venue was unreachable, and the position would never time out."""
    adapter = _ClosingAdapter(orders={"sl-1": {"status": "NEW"}, "tp-1": {"status": "NEW"}},
                              fetch_raises="TOOL_TRANSPORT")
    store = _Store()
    _settle(_timed(holding_candles=9), adapter=adapter, store=store)
    assert store.saved[0]["holding_candles"] == 10


# --- one context owns the clock ---------------------------------------------------------------
#
# The live book is keyed by SYMBOL (the venue nets per symbol in one-way mode) while a cycle runs
# per (symbol, timeframe). A symbol routed at four timeframes therefore sends four cycles past the
# same position in one fan-out, each carrying a DIFFERENT bar timestamp — so `advance_holding`'s
# dedup, which asks "have I counted this bar?", answered honestly four times and the counter moved
# four bars. A 24-bar position time-exited in six fan-outs. Paper never had this because its book
# is keyed by (symbol, timeframe), so exactly one context can ever reach a given position.

def test_a_sibling_timeframe_does_not_advance_the_clock():
    """The 15m cycle of a symbol whose live position was opened at 1d still settles and protects
    it — both are urgent at any resolution — but must not count a 15m bar against a 1d rule."""
    store = _Store()
    record = _settle(_timed(holding_candles=1), context_timeframe="15m", store=store)
    assert not store.saved, "a non-owning context wrote the counter"
    assert live_route.LIVE_HOLD_NOT_TIMED_HERE in record["live_reason_codes"]
    assert record["live_holding"]["timed_here"] is False
    assert record["live_holding"]["timed_by"] == "1d"
    assert record["live_holding"]["holding_candles"] == 1  # reported, just not advanced


def test_one_fanout_over_four_timeframes_advances_the_clock_once():
    """The regression, at the resolution it actually bit: five symbols x four timeframes is the
    live pool shape, and one pass of it used to age a position by four bars."""
    position = _timed(max_holding_bars=24, holding_candles=0)
    # Each context passes ITS OWN bar close, which is what made four distinct dedup keys.
    for timeframe, candle_ts in (("15m", "2026-07-28T00:15:00Z"), ("1h", "2026-07-28T01:00:00Z"),
                                 ("4h", "2026-07-28T04:00:00Z"), ("1d", "2026-07-29T00:00:00Z")):
        store = _Store()
        _settle(position, context_timeframe=timeframe, candle_ts=candle_ts, store=store)
        if store.saved:
            position = store.saved[-1]
    assert position["holding_candles"] == 1


def test_the_owning_context_still_times_it_out():
    """The ownership rule must not become a way for a position to never time out at all."""
    store, ledger = _Store(), _Ledger()
    record = _settle(_timed(holding_candles=2), context_timeframe="1d", store=store, ledger=ledger)
    assert record["live_settled"] is not None
    assert record["live_settled"]["status"] == live_leg.EXIT_CLOSED


def test_a_legacy_position_is_owned_by_the_default_context():
    """A position opened before the record carried a timeframe has no owner to name, so it gets
    the fan-out's default — arbitrary, but SINGLE, which is the whole property. `cycle` adds that
    context for every open live position, so the owner is always a context that runs."""
    legacy = _position(holding_candles=0, last_counted_candle_ts=None)
    assert "timeframe" not in legacy
    store = _Store()
    record = _settle(legacy, context_timeframe=live_route.DEFAULT_TIMING_CONTEXT, store=store)
    assert store.saved[-1]["holding_candles"] == 1
    assert record["live_holding"]["timed_here"] is True
    # ...and every other context leaves it alone.
    store = _Store()
    _settle(legacy, context_timeframe="15m", store=store)
    assert not store.saved


def test_a_caller_that_names_no_context_times_nothing():
    """The fail-closed direction. An unknown context must not become "time everything", which is
    the behaviour that produced the bug — every unknown in this stack fails toward doing less."""
    store = _Store()
    record = _settle(_timed(holding_candles=2), context_timeframe="", store=store)
    assert not store.saved
    assert record["live_settled"] is None
    assert live_route.LIVE_HOLD_NOT_TIMED_HERE in record["live_reason_codes"]


# --- the operator hears about real money ----------------------------------------

def _notified(record, monkeypatch, *, fail=None):
    """Drive `_notify_operator` and return what would have been sent."""
    from runtime.mvp_runtime import operator as operator_mod
    from runtime.mvp_runtime.crypto import live_route

    sent: list[str] = []

    def _send(channel, text, *, repo_root=None):
        if fail:
            raise fail
        sent.append(text)

    monkeypatch.setattr(operator_mod, "select_operator_channel", lambda **kw: object())
    monkeypatch.setattr(operator_mod, "notify_operator", _send)
    live_route._notify_operator(record, now="2026-07-29T08:00:00Z", root=None)
    return sent


def _opened_record(status):
    return {
        "live_route_status": status,
        "live_reason_codes": [],
        "live_opened": {"status": "ENTRY_OPENED", "position": {
            "symbol": "BTCUSDT", "direction": "LONG", "quantity": 0.001,
            "entry_price": 64512.0, "stop_price": 63000.0, "target_price": 67000.0}},
    }


def test_an_opened_position_reaches_the_operator(monkeypatch):
    """Real money moved with nobody watching. The message carries what an operator needs to
    check the venue against: symbol, side, size, and both bracket legs."""
    sent = _notified(_opened_record("OPENED"), monkeypatch)
    assert len(sent) == 1
    for fragment in ("[LIVE]", "BTCUSDT", "LONG", "0.001", "64512.0", "63000.0", "67000.0"):
        assert fragment in sent[0]


def test_an_incident_says_so_and_names_the_halt(monkeypatch):
    """`INCIDENT` means money is somewhere the runtime cannot account for. That message must
    not read like the routine one, and it must carry the verb that stops new entries."""
    record = _opened_record("INCIDENT")
    record["live_reason_codes"] = ["ENTRY_NAKED_OPEN"]
    sent = _notified(record, monkeypatch)
    assert "[LIVE INCIDENT]" in sent[0]
    assert "ENTRY_NAKED_OPEN" in sent[0]
    # The notice names the halt that acts on THIS policy (review of H2), and kill never goes out
    # without its caveat — during an incident a kill also stops the settle/protect step.
    assert "console_cli" in sent[0] and "position management" in sent[0]


@pytest.mark.parametrize("granted", [True, False])
def test_the_halt_advice_names_only_a_verb_that_will_act(monkeypatch, granted):
    from runtime.mvp_runtime import control

    verbs = {"kill", "pause"} | ({control.CMD_HALT_TRADING} if granted else set())
    monkeypatch.setattr(control, "granted_emergency_controls", lambda root=None: frozenset(verbs))
    advice = live_route.halt_advice()
    if granted:
        assert advice.startswith("To stop new entries and keep managing positions: console_cli halt_trading")
    else:
        assert "console_cli kill" in advice and "stops position management" in advice
        assert "acts once policy 1.5.1 grants it" in advice


@pytest.mark.parametrize("status", ["HELD", "SETTLED", "BLOCKED", "DISABLED", None])
def test_a_quiet_cycle_says_nothing(status):
    """A channel that pings every fifteen minutes is one nobody reads by the second day, and
    the one message that matters would arrive in a stream the operator has learned to skip."""
    from runtime.mvp_runtime.crypto import live_route

    record = {"live_route_status": status, "live_reason_codes": [], "live_opened": {}}
    # No monkeypatching at all: if it tried to send, selecting a channel would be attempted.
    live_route._notify_operator(record, now="2026-07-29T08:00:00Z", root=None)
    assert record["live_reason_codes"] == []


def test_a_failed_send_is_recorded_and_never_raised(monkeypatch):
    """By the time this runs the order is at the venue. A notification failure is reported on
    the record — the audit-append precedent one function up — and never allowed to raise."""
    from runtime.mvp_runtime.crypto import live_route
    from runtime.mvp_runtime.errors import MvpRuntimeError

    record = _opened_record("OPENED")
    _notified(record, monkeypatch, fail=MvpRuntimeError("TELEGRAM_DOWN", "no"))
    assert live_route.NOTIFY_FAILED in record["live_reason_codes"]


def test_an_unexpected_error_is_caught_too(monkeypatch):
    """The money path must not die because a transport raised something nobody typed."""
    from runtime.mvp_runtime.crypto import live_route

    record = _opened_record("OPENED")
    _notified(record, monkeypatch, fail=RuntimeError("boom"))
    assert live_route.NOTIFY_FAILED in record["live_reason_codes"]
    assert "UNEXPECTED_RuntimeError" in record["live_reason_codes"]


# --- the reversed entry: the outcome that used to look like nothing --------------

def _reversed_record(*, detail="venue rejected the order (code -2021): Order would immediately trigger."):
    """What the 2026-08-02 attempts actually produced: a real fill, a refused protective stop,
    and rule 2 closing the position again. `live_route_status` stays HELD."""
    from runtime.mvp_runtime.crypto import live_leg

    return {
        "live_route_status": live_route.ROUTE_HELD,
        "symbol": "ETHUSDT",
        "live_reason_codes": [live_leg.BRACKET_FAILED, live_leg.NAKED_POSITION_CLOSED],
        "live_opened": {
            "status": live_leg.ENTRY_NAKED_CLOSED,
            "position": {"symbol": "ETHUSDT", "direction": "SHORT", "quantity": 0.031,
                         "entry_price": 1876.35, "stop_price": 1907.98, "target_price": 1808.7},
            "bracket": [
                {"order_type": "STOP_MARKET", "placed": False,
                 "error": "ORDER_REJECTED", "error_detail": detail},
                {"order_type": "LIMIT", "placed": True, "error": None, "error_detail": None},
            ],
        },
    }


def test_a_reversed_entry_reaches_the_operator(monkeypatch):
    """The gap this closes. The position opened, so nothing was merely held — and the runtime
    handled it, so it is not an incident. `live_route_status` stays HELD, which is why keying on
    status alone missed it.

    Measured 2026-08-02: two ETHUSDT entries filled for real, both lost their protective stop to
    a venue rejection, both were force-closed, and no message was sent for either. They were
    found because a watch happened to be running."""
    sent = _notified(_reversed_record(), monkeypatch)
    assert len(sent) == 1
    assert "could not be protected" in sent[0]
    assert "ETHUSDT" in sent[0] and "1876.35" in sent[0]


def test_the_message_carries_the_venue_reason_for_the_refused_leg(monkeypatch):
    """Without it the message says a protective order was refused and cannot say why — the
    position the operator was left in on 2026-08-02, and the reason it took a day to narrow."""
    sent = _notified(_reversed_record(), monkeypatch)
    assert "STOP_MARKET ORDER_REJECTED" in sent[0]
    assert "-2021" in sent[0] and "immediately trigger" in sent[0]
    # The leg that placed cleanly is not listed — only what refused.
    assert sent[0].count("refused  :") == 1


def test_a_refused_leg_with_no_detail_still_names_the_gap(monkeypatch):
    """A record written before the detail was captured must not render an empty line that reads
    like the venue said nothing."""
    sent = _notified(_reversed_record(detail=None), monkeypatch)
    assert "no detail recorded" in sent[0]


def test_a_held_cycle_with_no_entry_still_says_nothing(monkeypatch):
    """The edge trigger survives: this must not become a message every fifteen minutes."""
    quiet = {"live_route_status": live_route.ROUTE_HELD, "live_reason_codes": [], "symbol": "ETHUSDT"}
    assert _notified(quiet, monkeypatch) == []


# --- the daily loss breaker needs the venue's own figure on the entry path (2026-09-15) ------

def _entry_decision_inputs(tmp_path, monkeypatch, *, realized_windows, bar=NOW, route=None):
    """Drive the gated leg up to the entry decision and hand back what it was judged on.

    Everything before the planner is real except the venue reads: a configured 20 USDT daily
    limit, no open position, and an account snapshot whose realized windows are the variable."""
    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    snapshot = AccountSnapshot(
        asset="USDT", wallet_balance=500.0, margin_balance=500.0, available_balance=400.0,
        unrealized_pnl=0.0, positions=[], realized_windows=realized_windows,
        source="fake", collected_at=NOW,
    )
    monkeypatch.setattr(live_route, "read_account", lambda **kw: (snapshot, {}))
    monkeypatch.setattr(live_route, "list_open_live_positions", lambda root: [])
    limits = LiveOrderLimits(
        max_order_notional_usdt=60.0, max_daily_order_count=2, max_open_notional_usdt=120.0,
        daily_loss_limit_usdt=20.0, confirmation=LIVE_CONFIRMATION_PHRASE,
    )
    monkeypatch.setattr(live_route, "resolve_live_order_limits",
                        lambda root, now=None: (limits, {"valid": True, "symbol_allowlist": [SYMBOL]}))
    seen: dict[str, Any] = {}

    def _plan(plan, **kw):
        seen.update(kw)
        return {"status": "REFUSED", "ready": False, "reasons": ["stubbed"]}

    monkeypatch.setattr(live_route, "plan_live_entry", _plan)
    record = live_route.run_live_leg(
        live_routable_strategy_ids={"S1"}, route=route, feature_row={"timestamp": bar},
        verdict={"allow_new_position": True}, symbol=SYMBOL, collector=object(), now=NOW,
        root=tmp_path,
    )
    return seen, record


def test_an_account_read_with_no_realized_figure_trips_the_entry_breaker(tmp_path, monkeypatch):
    """The verified fail-open: balances read, income did not, and the leg judged the entry on
    the local ledger's 0.0. The planner must now see a tripped breaker, and the cycle record
    must say why — not "limit reached"."""
    from runtime.mvp_runtime.crypto.live_pnl import LIVE_PNL_VENUE_FIGURE_MISSING

    seen, record = _entry_decision_inputs(tmp_path, monkeypatch, realized_windows={})
    assert seen["daily_loss_breached"] is True
    assert LIVE_PNL_VENUE_FIGURE_MISSING in record["live_reason_codes"]


def test_a_venue_figure_under_the_limit_leaves_the_entry_breaker_clear(tmp_path, monkeypatch):
    """The companion: the rule trips on a MISSING figure only, never on a present one."""
    from runtime.mvp_runtime.crypto.live_pnl import LIVE_PNL_VENUE_FIGURE_MISSING

    seen, record = _entry_decision_inputs(
        tmp_path, monkeypatch, realized_windows={"today": {"net": -5.0}, "1d": {"net": -5.0}})
    assert seen["daily_loss_breached"] is False
    assert LIVE_PNL_VENUE_FIGURE_MISSING not in record["live_reason_codes"]


# --- a soft-halted runtime still manages what it holds (audit M-6, decision 7) ---------------

def test_a_soft_halted_runtime_manages_open_positions_and_refuses_entries(tmp_path, monkeypatch):
    """The halt the soft-halt verb produces: ACTIVE with live entries disarmed. The leg must still
    settle and protect the open position, and the entry decision must be told the runtime may not
    open one. Nothing pinned `trading_allowed` on this path before (a regression to
    `execution_allowed` would have passed every test)."""
    from runtime.mvp_runtime.control import ACTIVE, ControlState, ControlStore

    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    store = ControlStore(tmp_path)
    store.save(ControlState(mode=ACTIVE, updated_by="op", updated_at=NOW, reason="soft halt",
                            trading_armed=False))
    monkeypatch.setattr(live_route, "read_account", lambda **kw: (_snapshot(), {}))
    monkeypatch.setattr(live_route, "list_open_live_positions",
                        lambda root: [{"symbol": SYMBOL, "position_id": "p1", "status": "OPEN"}])
    # The book agrees with the venue, so the leg reaches the entry decision after managing.
    monkeypatch.setattr(live_route, "reconcile_positions",
                        lambda local, snapshot, now: {"status": "RECONCILED", "books": {}})
    managed: list[str] = []
    monkeypatch.setattr(live_route, "_settle_or_protect",
                        lambda record, position, **kw: managed.append(position["position_id"]))
    seen: dict[str, Any] = {}

    def _plan(plan, **kw):
        seen.update(kw)
        return {"status": "REFUSED", "ready": False, "reasons": ["stubbed"]}

    monkeypatch.setattr(live_route, "plan_live_entry", _plan)
    live_route.run_live_leg(
        live_routable_strategy_ids={"S1"}, route=None, feature_row={"timestamp": NOW},
        verdict={"allow_new_position": True}, symbol=SYMBOL, collector=object(), now=NOW,
        root=tmp_path, control_store=store,
    )
    assert managed == ["p1"], "a soft halt must not stop position management"
    assert seen["runtime_active"] is False


# --- the retired budget fields cannot reach the settle/protect ordering (2026-09-15, PR1r) ---

# shape -> (the legacy window it carries, or None for none; whether the budget is usable at NOW)
_ROUTE_BUDGET_SHAPES = {
    "built_today": (None, True),
    "legacy_inside_its_window": (("2026-07-25T00:00:00Z", "2027-08-30T00:00:00Z"), True),
    "legacy_past_its_window": (("2026-06-01T00:00:00Z", "2026-06-30T00:00:00Z"), False),
    "legacy_with_half_a_window": (("2026-07-25T00:00:00Z", None), False),
}


@pytest.mark.parametrize("shape", list(_ROUTE_BUDGET_SHAPES))
def test_every_budget_shape_on_disk_still_manages_open_positions(tmp_path, monkeypatch, shape):
    """The ordering the retirements had to respect. `resolve_live_order_limits` is the first read
    of the leg, before settle/protect, and anything it raises there is an INCIDENT halt that
    leaves every open position unmanaged. A budget built today omits `min_clean_canary_orders`
    and the validity window; one registered before carries both inside its self-hash, and is
    still held to its window. Each is written to disk and read by the real resolver — a stubbed
    one would prove nothing about the subscripts that used to be there. A budget that cannot back
    an entry (lapsed, or half a window) must still let the leg close what it holds."""
    import json

    from runtime.read_only_kernel import integrity
    from runtime.mvp_runtime.crypto import live_budget

    window, usable = _ROUTE_BUDGET_SHAPES[shape]
    record = live_budget.build_live_trading_budget_record(
        caps=dict(max_order_notional_usdt=60.0, absolute_max_notional_usdt=200.0,
                  max_daily_order_count=2, max_open_notional_usdt=120.0, daily_loss_limit_usdt=20.0),
        symbol_allowlist=[SYMBOL], registered_by="thomas", registered_at="2026-07-25T00:00:00Z",
    )
    assert "valid_from" not in record and "valid_until" not in record
    if window is not None:
        record = {k: v for k, v in record.items() if k != "record_sha256"}
        record["caps"] = {**record["caps"], "min_clean_canary_orders": 4}
        record["valid_from"] = window[0]
        if window[1] is not None:
            record["valid_until"] = window[1]
        record["record_sha256"] = integrity.sha256_record(record)
    path = live_budget.budget_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")

    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    monkeypatch.setattr(live_route, "read_account", lambda **kw: (_snapshot(), {}))
    monkeypatch.setattr(live_route, "list_open_live_positions",
                        lambda root: [{"symbol": SYMBOL, "position_id": "p1", "status": "OPEN"}])
    monkeypatch.setattr(live_route, "reconcile_positions",
                        lambda local, snapshot, now: {"status": "RECONCILED", "books": {}})
    managed: list[tuple[str, LiveOrderLimits]] = []
    monkeypatch.setattr(live_route, "_settle_or_protect",
                        lambda record, position, **kw: managed.append((position["position_id"], kw["limits"])))
    seen: dict[str, Any] = {}

    def _plan(plan, **kw):
        seen.update(kw)
        return {"status": "REFUSED", "ready": False, "reasons": ["stubbed"]}

    monkeypatch.setattr(live_route, "plan_live_entry", _plan)
    out = live_route.run_live_leg(
        live_routable_strategy_ids={"S1"}, route=None, feature_row={"timestamp": NOW},
        verdict={"allow_new_position": True}, symbol=SYMBOL, collector=object(), now=NOW,
        root=tmp_path,
    )

    assert [position_id for position_id, _ in managed] == ["p1"], "the open position went unmanaged"
    assert out["live_route_status"] != live_route.ROUTE_INCIDENT
    assert not any(code.startswith("UNEXPECTED_") for code in out["live_reason_codes"])
    assert "clean_canary_orders" not in seen
    assert seen["budget_registered"] is usable
    if usable:
        assert managed[0][1].max_order_notional_usdt == 60.0, "settled against the registered caps"
    else:
        # No budget backs an entry: the caps are the blocking defaults, and the position is
        # managed against them all the same.
        assert managed[0][1].max_order_notional_usdt == 0.0 and managed[0][1].max_daily_order_count == 0


@pytest.mark.parametrize("poison", ['{"caps": {"max_order_notional_usdt": NaN}, "record_sha256": "sha256:0"}',
                                    '{"api_secret": "x", "record_sha256": "sha256:0"}'])
def test_an_unhashable_budget_on_disk_still_manages_open_positions(tmp_path, monkeypatch, poison):
    """Review of #873: a NaN or a secret-shaped key in the budget raised a bare ValueError /
    IntegrityError out of the leg's first read, before settle/protect — an INCIDENT halt with every
    open position unmanaged. It is now the budget's typed refusal: blocking caps, positions managed."""
    from runtime.mvp_runtime.crypto import live_budget

    path = live_budget.budget_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(poison, encoding="utf-8")
    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    monkeypatch.setattr(live_route, "read_account", lambda **kw: (_snapshot(), {}))
    monkeypatch.setattr(live_route, "list_open_live_positions",
                        lambda root: [{"symbol": SYMBOL, "position_id": "p1", "status": "OPEN"}])
    monkeypatch.setattr(live_route, "reconcile_positions",
                        lambda local, snapshot, now: {"status": "RECONCILED", "books": {}})
    managed: list[str] = []
    monkeypatch.setattr(live_route, "_settle_or_protect",
                        lambda record, position, **kw: managed.append(position["position_id"]))
    monkeypatch.setattr(live_route, "plan_live_entry",
                        lambda plan, **kw: {"status": "REFUSED", "ready": False, "reasons": ["stubbed"]})
    out = live_route.run_live_leg(
        live_routable_strategy_ids={"S1"}, route=None, feature_row={"timestamp": NOW},
        verdict={"allow_new_position": True}, symbol=SYMBOL, collector=object(), now=NOW,
        root=tmp_path,
    )
    assert managed == ["p1"], "the open position went unmanaged"
    assert out["live_route_status"] != live_route.ROUTE_INCIDENT
    assert not any(code.startswith("UNEXPECTED_") for code in out["live_reason_codes"])


@pytest.mark.parametrize("stage,valid", [("PAPER", True), ("READ_ONLY", False)])
def test_a_stage_that_admits_no_entry_still_settles_and_protects(tmp_path, monkeypatch, stage, valid):
    """PR1b at the leg: below LIVE_AUTONOMOUS the entry door refuses, and everything that manages
    what is already open runs exactly as before. The stage the leg stamps is the one the entry was
    judged against — one read, not two."""
    from runtime.mvp_runtime.crypto import execution_stage as es

    status = es.StageStatus(stage=stage if valid else "READ_ONLY", valid=valid,
                            reason_code=None if valid else es.STAGE_RECORD_MISSING,
                            recorded_stage=stage if valid else None)
    reads: list[str] = []

    def _resolve(root=None, **kw):
        reads.append(str(root))
        return status

    monkeypatch.setattr(live_route, "resolve_execution_stage", _resolve)
    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    monkeypatch.setattr(live_route, "read_account", lambda **kw: (_snapshot(), {}))
    monkeypatch.setattr(live_route, "list_open_live_positions",
                        lambda root: [{"symbol": SYMBOL, "position_id": "p1", "status": "OPEN"}])
    monkeypatch.setattr(live_route, "reconcile_positions",
                        lambda local, snapshot, now: {"status": "RECONCILED", "books": {}})
    managed: list[str] = []
    monkeypatch.setattr(live_route, "_settle_or_protect",
                        lambda record, position, **kw: managed.append(position["position_id"]))
    seen: dict[str, Any] = {}

    def _plan(plan, **kw):
        seen.update(kw)
        return {"status": "REFUSED", "ready": False, "reasons": ["stubbed"]}

    monkeypatch.setattr(live_route, "plan_live_entry", _plan)
    out = live_route.run_live_leg(
        live_routable_strategy_ids={"S1"}, route=None, feature_row={"timestamp": NOW},
        verdict={"allow_new_position": True}, symbol=SYMBOL, collector=object(), now=NOW,
        root=tmp_path,
    )
    assert managed == ["p1"], "a stage that admits no entry must not stop position management"
    assert out["live_route_status"] != live_route.ROUTE_INCIDENT
    assert seen["execution_stage"] is status, "the entry was judged against a second, unstamped read"
    assert len(reads) == 1, "the leg reads the stage once: the stamp and the judgement are one answer"
    assert out["execution_stage"]["stage"] == status.stage


# --- settle and enter are mutually exclusive within one cycle -------------------------------

def test_a_cycle_that_settles_never_also_enters(tmp_path, monkeypatch):
    """The short-circuit at the top of the entry block, pinned as a property rather than as a
    status string — the `if record["live_settled"] is not None: return` above step 3.

    It is load-bearing well outside this module. `cycle.py` evaluates the per-lineage live
    allowance (#615 §5) against the outcome history as read at the START of the cycle, and the
    honesty of that ordering rests entirely on this: an outcome settled by THIS leg cannot
    influence THIS leg's entry, because there is no entry after a settlement. The next cycle
    re-reads the history with the new row in it and decides permission before running the leg
    again. Break this and a lineage gets to close a losing trade and open another on the same
    pass, with its allowance judged on the state before either.

    Asserted by making the entry planner explode. If the branch is ever removed or inverted,
    this fails loudly instead of passing on a leg that quietly re-entered."""
    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    monkeypatch.setattr(live_route, "read_account", lambda **kw: (_snapshot(), {}))
    monkeypatch.setattr(
        live_route, "list_open_live_positions",
        lambda root: [{"symbol": SYMBOL, "position_id": "p1", "status": "OPEN"}],
    )

    def _settled(record, position, **kw):
        record["live_settled"] = {
            "status": "SETTLED", "outcome": {"result_R": -1.0}, "reason_codes": [],
        }

    monkeypatch.setattr(live_route, "_settle_or_protect", _settled)
    monkeypatch.setattr(
        live_route, "plan_live_entry",
        lambda *a, **kw: pytest.fail("the leg planned an entry on the same pass it settled"),
    )

    record = live_route.run_live_leg(
        live_routable_strategy_ids={"S1"},
        route={"strategy_id": "S1"}, feature_row={"timestamp": NOW},
        verdict={"allow_new_position": True},
        symbol=SYMBOL, collector=object(), now=NOW, root=tmp_path,
    )
    assert record["live_settled"] is not None
    assert record["live_opened"] is None, "a settling cycle opened a position"


def test_a_venue_closed_position_settles_from_whichever_context_runs_first(tmp_path, monkeypatch):
    """The 2026-08-18 deadlock, pinned: an operator hand-closed BTCUSDT while an ETH probe
    was in flight. The probe's `timeframe: None` put the ETH 1d context first in the
    fan-out, the account-wide drift halted the pass THERE, and the BTCUSDT context — the
    only one that settled its drift — never ran. #631's one-cycle settle only ever worked
    because the drifted symbol's context happened to run first.

    The property now: a MISSING_AT_VENUE position is settled by whichever context runs
    first, whatever its symbol — so the drift clears, the pass continues, and the halt
    stays what it means (a disagreement settlement could NOT repair)."""
    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    eth_local = {"symbol": "ETHUSDT", "position_id": "probe-eth", "status": "OPEN",
                 "direction": "LONG", "quantity": 0.002}
    btc_local = {"symbol": "BTCUSDT", "position_id": "s005-btc", "status": "OPEN",
                 "direction": "SHORT", "quantity": 0.003}
    eth_venue = AccountPosition(symbol="ETHUSDT", side="LONG", quantity=0.002,
                                entry_price=4600.0, mark_price=4600.0, unrealized_pnl=0.0,
                                leverage=1.0, notional=9.2)
    monkeypatch.setattr(live_route, "read_account",
                        lambda **kw: (_snapshot(positions=[eth_venue]), {}))
    monkeypatch.setattr(live_route, "list_open_live_positions",
                        lambda root: [eth_local, btc_local])

    settled_positions = []

    def _settle(record, position, **kw):
        settled_positions.append(position["position_id"])
        if position["symbol"] == "BTCUSDT":
            # The MISSING branch settles it (sends nothing) and the record says so.
            record["live_settled"] = {
                "status": "SETTLED", "outcome": {"result_R": 0.4}, "reason_codes": [],
            }

    monkeypatch.setattr(live_route, "_settle_or_protect", _settle)

    record = live_route.run_live_leg(
        live_routable_strategy_ids={"S1"},
        route=None, feature_row={"timestamp": NOW}, verdict={"allow_new_position": True},
        symbol="ETHUSDT", collector=object(), now=NOW, root=tmp_path,
    )
    assert "s005-btc" in settled_positions, (
        "the ETH context never settled the venue-closed BTC position — the deadlock is back")
    assert record["halt"] is False
    assert live_route.BOOK_DRIFT not in record["live_reason_codes"]


def test_drift_that_bookkeeping_cannot_repair_still_halts_from_any_context(tmp_path, monkeypatch):
    """The other half of the fix's scope: a quantity mismatch is a venue state settlement
    cannot repair, so a context that is not the drifted symbol's own must NOT touch the
    position — and the halt still fires exactly as before."""
    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    btc_local = {"symbol": "BTCUSDT", "position_id": "s005-btc", "status": "OPEN",
                 "direction": "SHORT", "quantity": 0.003}
    btc_venue = AccountPosition(symbol="BTCUSDT", side="SHORT", quantity=0.005,
                                entry_price=64000.0, mark_price=64000.0, unrealized_pnl=0.0,
                                leverage=1.0, notional=320.0)
    monkeypatch.setattr(live_route, "read_account",
                        lambda **kw: (_snapshot(positions=[btc_venue]), {}))
    monkeypatch.setattr(live_route, "list_open_live_positions", lambda root: [btc_local])
    monkeypatch.setattr(
        live_route, "_settle_or_protect",
        lambda record, position, **kw: pytest.fail(
            "a foreign context touched a position whose drift bookkeeping cannot repair"),
    )

    record = live_route.run_live_leg(
        live_routable_strategy_ids={"S1"},
        route=None, feature_row={"timestamp": NOW}, verdict={"allow_new_position": True},
        symbol="ETHUSDT", collector=object(), now=NOW, root=tmp_path,
    )
    assert record["halt"] is True
    assert live_route.BOOK_DRIFT in record["live_reason_codes"]
    assert record["live_route_status"] == live_route.ROUTE_INCIDENT


# --- one entry per bar, and the stop-loss cooldown, on the live leg (PR2a) --------------------
#
# The hazard, as the 15-minute fan-out produced it: the route a 4h context hands this leg is the
# same ENTRY_CANDIDATE on every tick of the bar. While a position is open the per-symbol cap hides
# that. Once the venue's stop closes it inside the bar, the next tick settles, and the tick after
# that entered again on the same signal — with a fresh client order id, because the id was keyed
# on the wall clock. Paper holds both the bar and a two-bar cooldown; the route reached this leg
# before either rule ran.

BAR_00 = "2026-07-28T00:00:00Z"
SL_FILL = 59000.0
def _traded_spec(**overrides):
    """A spec with its own rule hash: an armed entry must trade the rule its label names."""
    from runtime.mvp_runtime.crypto.strategy import StrategySpec
    from tests.test_mvp_runtime_crypto_evidence_depth import _spec_dict

    return StrategySpec.from_dict(_spec_dict(**overrides)).to_dict()


_SPEC = _traded_spec()
_OTHER_SPEC = _traded_spec(direction="short")      # another rule, another hash
_PLAN = {
    "symbol": SYMBOL, "timeframe": "4h", "direction": "LONG",
    "entry_price": 60000.0, "stop_loss": SL_FILL, "take_profit": 62000.0, "risk": 1000.0,
    "strategy_id": "S001", "candidate_id": "cand_1", "strategy_rule_hash": _SPEC["strategy_rule_hash"],
    "strategy_generation_id": "gen_1", "max_holding_bars": 12, "vol_size_multiplier": 1.0,
    "breakeven_at_r": None, "trail_distance": None, "supporting_strategy_ids": [],
}
_GOVERNANCE = {
    "purpose": "autonomous", "order_fingerprint": "sha256:pr2a",
    "bound_task": {"identity": {"task_id": "T-pr2a"}, "context": {"core_context_binding_id": "CCB1"}},
    "permission_decision": {"permission_decision_id": "permdec_pr2a"},
}


class _Venue:
    """The venue for a whole trade: the entry fills, the bracket rests, and a test says when a
    leg has filled. Faithful where it matters — a resting leg has executed nothing."""

    tool_id, tool_version = "fake", "0"
    network_egress = True

    def __init__(self):
        self.submitted: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.requests: dict[str, dict[str, Any]] = {}
        self.filled: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _id(request):
        return str(request.get("clientAlgoId") or request["newClientOrderId"])

    def entries(self):
        return [r for r in self.submitted if r["type"] == "MARKET" and not r.get("reduceOnly")]

    def submit(self, order_request, *, timeout_seconds: int = 10):
        self.submitted.append(dict(order_request))
        self.requests[self._id(order_request)] = dict(order_request)
        return {"accepted": True}

    def fetch_order(self, symbol, client_order_id, *, timeout_seconds: int = 10, algo: bool = False):
        cid = str(client_order_id)
        request = self.requests.get(cid)
        if request is None:
            return None
        if cid in self.filled:
            return dict(self.filled[cid])
        resting = request["type"] != "MARKET"
        qty = 0.0 if resting else float(request.get("quantity") or 0.0)
        return {"symbol": symbol, "side": request["side"], "status": "NEW" if resting else "FILLED",
                "executedQty": qty, "reduceOnly": bool(request.get("reduceOnly")),
                "avgPrice": "60000.0", "cumQuote": str(round(qty * 60000.0, 8)), "orderId": "oid"}

    def cancel_order(self, symbol, client_order_id, *, timeout_seconds: int = 10, algo: bool = False):
        self.cancelled.append(str(client_order_id))
        return {"status": "CANCELED"}

    def _resting(self, symbol, *, conditional):
        """What rests on ``symbol``: a leg that neither filled nor was withdrawn (PR2c-3)."""
        return [
            {"clientOrderId": cid, "symbol": r["symbol"]} for cid, r in self.requests.items()
            if r["type"] != "MARKET" and cid not in self.filled and cid not in self.cancelled
            and bool(r.get("clientAlgoId")) is conditional and (symbol is None or r["symbol"] == symbol)
        ]

    def open_orders(self, symbol=None, *, timeout_seconds: int = 10):
        return self._resting(symbol, conditional=False)

    def algo_open_orders(self, symbol=None, *, timeout_seconds: int = 10):
        return self._resting(symbol, conditional=True)


class _Collector:
    """The market data a pass reads: a deep book, in hand at the pass's own clock (PR2d-3)."""

    def __init__(self, clock=None):
        self._clock = clock or (lambda: NOW)

    def order_book(self, symbol, *, limit, timeout_seconds):
        return deep_order_book(received_at=self._clock())


# The approval Thomas answered to arm the plan's lineage LIVE (PR2c-2b). The pass runs hours after
# it expired, as every arm does: the arm outlives the ask.
_ARMED_AT = "2026-07-27T23:55:00Z"


def _armed_entry():
    from runtime.mvp_runtime.crypto import pool as pool_store

    return {
        "strategy_id": "S001", "status": "PAPER_ACTIVE",
        pool_store.LIVE_TIER_FIELD: pool_store.LIVE_TIER_LIVE,
        "candidate_id": _PLAN["candidate_id"], "strategy_rule_hash": _PLAN["strategy_rule_hash"],
        "strategy_spec": _SPEC, "promoted_at": _ARMED_AT,
    }


# The artifact the door installed the armed entry as (PR3a), which the approval pairs with its candidate.
_ARMED_STAMP = stamped_pool_entry(_armed_entry())["strategy_artifact_sha256"]
_ARM = live_arm_approval((_PLAN["candidate_id"],), (_PLAN["strategy_rule_hash"],), artifacts=(_ARMED_STAMP,))


def _armed_pool(approval_id=None, **entry):
    from runtime.mvp_runtime.crypto import pool as pool_store

    # Stamped as the door installed it, then the test's changes: a change to a hashed field leaves
    # the stamp behind, which a real pool read refuses (decision 34) and the read these tests stand
    # in for does not check.
    return {"active_strategies": [{
        **stamped_pool_entry(_armed_entry()),
        pool_store.LIVE_TIER_APPROVAL_FIELD: approval_id or _ARM["approval_id"], **entry,
    }]}


def _wire_whole_leg(tmp_path, monkeypatch, venue, *, approval=_ARM, armed_entry=None):
    """Everything real inside the leg — the book, the ledger, the counter, the marks, the
    planner, the guard, the executing leg — and a double only where the venue or the Core would
    be reached. The gate is pinned to the scripted venue, so no real adapter can be selected.

    ``approval`` is the record in the approval store (None: the store holds none) and the id both
    pool reads name; ``armed_entry`` changes the fresh read's pool entry."""
    from runtime.mvp_runtime.approval_store import ApprovalStore
    from runtime.mvp_runtime.control import ACTIVE, ControlState, ControlStore
    from runtime.mvp_runtime.crypto import live_governance
    from runtime.mvp_runtime.crypto.live_sizing import SymbolFilters

    arm_id = (approval or _ARM)["approval_id"]
    if approval is not None:
        ApprovalStore.default(tmp_path).append([approval])

    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    monkeypatch.setattr(live_route, "select_live_gate", lambda **kw: (venue, None))
    monkeypatch.setattr(live_route, "select_account_feed", lambda **kw: None)
    clock = {"now": NOW}

    def _flat(**kw):
        # Read at the pass's own clock (PR2c-1: an entry is refused on an account read over a minute
        # before its decision).
        return AccountSnapshot(
            asset="USDT", wallet_balance=500.0, margin_balance=500.0, available_balance=400.0,
            unrealized_pnl=0.0, positions=[],
            realized_windows={"today": {"net": 0.0}, "1d": {"net": 0.0}},
            source="fake", collected_at=clock["now"],
        ), {}

    monkeypatch.setattr(live_route, "read_account", _flat)
    # The market a minute before the pass, at the plan's own entry; the decision, the gate and the
    # venue door all judge at the pass's clock.
    monkeypatch.setattr(live_route, "_read_reference_quote", lambda collector, symbol, **kw: {
        "price": _PLAN["entry_price"], "close_time": timeutil.plus_seconds(clock["now"], -60),
        "timeframe": "1m", "reason": None})
    monkeypatch.setattr(live_route, "_entry_clock", lambda: clock["now"])
    monkeypatch.setattr(pre_order_gate, "_send_clock", lambda: clock["now"])
    # The pool the gate re-reads (PR2c-2a): S001 armed LIVE under the approval the cycle handed over.
    monkeypatch.setattr(live_route.pool, "load_active_pool",
                        lambda root=None: _armed_pool(arm_id, **(armed_entry or {})))
    limits = LiveOrderLimits(
        max_order_notional_usdt=60.0, max_daily_order_count=3, max_open_notional_usdt=120.0,
        daily_loss_limit_usdt=20.0, confirmation=LIVE_CONFIRMATION_PHRASE,
    )
    monkeypatch.setattr(live_route, "resolve_live_order_limits", lambda root, now=None: (limits, {
        "valid": True, "symbol_allowlist": [SYMBOL],
        "budget_id": "budget_pr2a", "record_sha256": "sha256:" + "b" * 64}))
    monkeypatch.setattr(live_route, "resolve_execution_stage", lambda root=None, **kw: gate_stage())
    monkeypatch.setattr(live_route, "build_entry_plan",
                        lambda route, row, now: {**_PLAN, "created_at_utc": now})
    filters = SymbolFilters(step_size=0.001, min_qty=0.001, min_notional=5.0, tick_size=0.1)
    monkeypatch.setattr(live_route, "read_symbol_filters", lambda collector, symbol, **kw: (filters, None))
    monkeypatch.setattr(live_governance, "prepare_live_order_governance", lambda intent, **kw: _GOVERNANCE)
    monkeypatch.setattr(live_route, "_report", lambda *a, **kw: None)
    monkeypatch.setattr(live_route, "_notify_operator", lambda record, **kw: None)
    control = ControlStore(tmp_path)
    control.save(ControlState(mode=ACTIVE, updated_by="op", updated_at=NOW, reason="test",
                              trading_armed=True))
    monkeypatch.setattr(live_route, "_settle_clock", lambda: clock["now"])

    def _pass(now, bar, *, wall=None):
        # The wall clock at a settlement: the pass's own `now` unless a test says otherwise.
        clock["now"] = wall or now
        return live_route.run_live_leg(
            live_routable_strategy_ids={"S001"}, route={"status": "ENTRY_CANDIDATE"},
            feature_row={"timestamp": bar},
            verdict={"allow_new_position": True, "problems": [],
                     "risk_guard": {"limits": {"source": "default"}}},
            symbol=SYMBOL, collector=_Collector(lambda: clock["now"]), now=now, timeframe="4h",
            root=tmp_path, control_store=control,
            live_arm_approvals={"S001": arm_id},
            optional_data=healthy_optional_data(bar),
        )

    return _pass


def _stop_fills(venue, tmp_path):
    """The venue's stop triggers: the leg reports FILLED and the account is flat again."""
    from runtime.mvp_runtime.crypto.live_position import list_open_live_positions

    [position] = list_open_live_positions(tmp_path)
    qty = float(position["quantity"])
    venue.filled[position["stop_client_order_id"]] = {
        "symbol": SYMBOL, "side": "SELL", "status": "FILLED", "executedQty": qty,
        "reduceOnly": True, "avgPrice": str(SL_FILL), "cumQuote": str(round(qty * SL_FILL, 8)),
        "orderId": "oid-sl",
    }


def test_a_stop_inside_the_bar_does_not_buy_a_second_entry_on_that_bar(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto.live_order import (
        LIVE_ENTRY_BAR_ALREADY_ENTERED,
        LIVE_ENTRY_STOP_LOSS_COOLDOWN,
        count_today,
        read_live_entry_marks,
    )

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)

    # 04:05 — the 00:00 bar has closed and routes: one entry, bracketed.
    opened = run("2026-07-28T04:05:00Z", BAR_00)
    assert opened["live_route_status"] == live_route.ROUTE_OPENED, opened["live_reason_codes"]
    assert len(venue.entries()) == 1
    assert read_live_entry_marks(tmp_path)["entered"] == {"BTCUSDT__4h": BAR_00}

    # 04:20 — the stop filled at the venue; this tick settles it and enters nothing.
    _stop_fills(venue, tmp_path)
    settled = run("2026-07-28T04:20:00Z", BAR_00)
    assert settled["live_route_status"] == live_route.ROUTE_SETTLED
    assert settled["live_settled"]["outcome"]["close_reason"] == "stop_loss"
    # The stop fell in the bar that opened at 04:00, so the two bars paper would hold are 04:00
    # and 08:00, and 12:00 is the first that may enter again.
    assert settled["live_stop_cooldown"] == {
        "symbol": SYMBOL, "timeframe": "4h", "until_bar": "2026-07-28T12:00:00Z",
        "close_reason": "stop_loss", "anchored_at": "2026-07-28T04:20:00Z"}
    assert read_live_entry_marks(tmp_path)["cooldown"] == {"BTCUSDT__4h": "2026-07-28T12:00:00Z"}

    # 04:35 — the book is flat and the venue agrees, the route is the same candidate: held, and
    # held for both reasons. This is the tick that used to send the second order.
    again = run("2026-07-28T04:35:00Z", BAR_00)
    assert again["live_route_status"] == live_route.ROUTE_HELD
    assert again["live_decision"]["reasons"] == [
        LIVE_ENTRY_BAR_ALREADY_ENTERED, LIVE_ENTRY_STOP_LOSS_COOLDOWN]
    assert len(venue.entries()) == 1

    # 08:05 and 12:05 — new bars, still inside the cooldown.
    for now, bar in (("2026-07-28T08:05:00Z", "2026-07-28T04:00:00Z"),
                     ("2026-07-28T12:05:00Z", "2026-07-28T08:00:00Z")):
        held = run(now, bar)
        assert held["live_decision"]["reasons"] == [LIVE_ENTRY_STOP_LOSS_COOLDOWN], now
    assert len(venue.entries()) == 1

    # 16:05 — the 12:00 bar is the bound: it enters, under an id of its own.
    reentered = run("2026-07-28T16:05:00Z", "2026-07-28T12:00:00Z")
    assert reentered["live_route_status"] == live_route.ROUTE_OPENED, reentered["live_reason_codes"]
    first, second = venue.entries()
    assert first["newClientOrderId"] != second["newClientOrderId"]
    assert count_today(tmp_path) == 2


def test_a_refusal_before_the_send_leaves_the_bar_open_for_the_next_tick(tmp_path, monkeypatch):
    """The mark is taken when an order is about to leave, not when the bar is evaluated — the
    one deliberate difference from paper. A transient refusal (here, the order book) sends
    nothing, so the next tick of the same bar may still enter."""
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    reads = iter([ToolError("TOOL_TRANSPORT", "scripted: the book did not answer"), None])

    def _book_then_fine(self, symbol, *, limit, timeout_seconds):
        failure = next(reads)
        if failure is not None:
            raise failure
        return deep_order_book(received_at=self._clock())

    monkeypatch.setattr(_Collector, "order_book", _book_then_fine)

    refused = run("2026-07-28T04:05:00Z", BAR_00)
    assert refused["live_route_status"] == live_route.ROUTE_HELD
    assert "LIVE_ENTRY_ORDERBOOK_UNREADABLE" in refused["live_decision"]["reasons"]
    assert venue.entries() == []

    opened = run("2026-07-28T04:20:00Z", BAR_00)
    assert opened["live_route_status"] == live_route.ROUTE_OPENED, opened["live_reason_codes"]


def test_the_leg_hands_the_decision_the_bar_it_evaluated_and_the_marks_it_read(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto.live_order import LiveEntryMarks, read_live_entry_marks
    from tests._helpers import make_gate_authorization
    from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_FLAGS, LIVE_TRADING_PROVIDER_ID

    auth = make_gate_authorization(flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID)
    LiveEntryMarks(root=tmp_path, authorization=auth).record_stop_cooldown(
        symbol=SYMBOL, timeframe="4h", until="2026-07-28T12:00:00Z")
    bar = "2026-07-27T20:00:00Z"   # the last closed 4h bar at NOW — not NOW itself
    seen, record = _entry_decision_inputs(tmp_path, monkeypatch, realized_windows={}, bar=bar)
    assert seen["entry_bar_time"] == bar
    assert seen["entry_marks"] == read_live_entry_marks(tmp_path)
    assert seen["entry_marks"]["cooldown"] == {"BTCUSDT__4h": "2026-07-28T12:00:00Z"}


def test_unreadable_marks_hold_entries_and_still_manage_positions(tmp_path, monkeypatch):
    """The opposite of paper's marks on purpose: a corrupt live file refuses entries. It is read
    after settle/protect, so it can never hold a position open."""
    from runtime.mvp_runtime.crypto.live_order import ENTRY_MARKS_FILENAME, LIVE_ENTRY_MARKS_UNREADABLE
    from runtime.mvp_runtime.crypto.state import venue_state_dir

    path = venue_state_dir(tmp_path) / ENTRY_MARKS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    monkeypatch.setattr(live_route, "read_account", lambda **kw: (_snapshot(), {}))
    monkeypatch.setattr(live_route, "list_open_live_positions",
                        lambda root: [{"symbol": SYMBOL, "position_id": "p1", "status": "OPEN"}])
    monkeypatch.setattr(live_route, "reconcile_positions",
                        lambda local, snapshot, now: {"status": "RECONCILED", "books": {}})
    managed: list[str] = []
    monkeypatch.setattr(live_route, "_settle_or_protect",
                        lambda record, position, **kw: managed.append(position["position_id"]))
    seen: dict[str, Any] = {}

    def _plan(plan, **kw):
        seen.update(kw)
        return {"status": "REFUSED", "ready": False, "reasons": ["stubbed"]}

    monkeypatch.setattr(live_route, "plan_live_entry", _plan)
    record = live_route.run_live_leg(
        live_routable_strategy_ids={"S1"}, route=None, feature_row={"timestamp": NOW},
        verdict={"allow_new_position": True}, symbol=SYMBOL, collector=object(), now=NOW,
        root=tmp_path,
    )
    assert managed == ["p1"]
    assert seen["entry_marks"] is None
    assert LIVE_ENTRY_MARKS_UNREADABLE in record["live_reason_codes"]
    assert record["live_route_status"] != live_route.ROUTE_INCIDENT


def _settle_venue_stop(tmp_path, monkeypatch, *, position, filled_leg="sl-1", price=59000.0,
                       now="2026-07-28T05:10:00Z", wall=None):
    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    monkeypatch.setattr(live_route, "select_account_feed", lambda **kw: None)
    monkeypatch.setattr(live_route, "_settle_clock", lambda: wall or now)
    adapter = _Adapter(orders={filled_leg: _venue_order("FILLED", price=price)})
    record: dict[str, Any] = {"live_reason_codes": [], "live_settled": None, "halt": False}
    live_route._settle_or_protect(
        record, position, adapter=adapter, position_store=_Store(), ledger=_Ledger(),
        reconciliation={"status": "DRIFT", "books": {SYMBOL: {"reasons": ["POSITION_MISSING_AT_VENUE"]}}},
        limits=LiveOrderLimits(), candle_ts=None, now=now, root=tmp_path,
        timeout_seconds=1,
    )
    return record


@pytest.mark.parametrize("timeframe,until", [
    ("15m", "2026-07-28T05:30:00Z"),
    ("1h", "2026-07-28T07:00:00Z"),
    ("4h", "2026-07-28T12:00:00Z"),
    ("1d", "2026-07-30T00:00:00Z"),
])
def test_a_venue_stop_cools_the_positions_own_context(tmp_path, monkeypatch, timeframe, until):
    """Settled at 05:10: the bound is two bars after the bar containing it, on every timeframe
    the runtime trades — the route passes paper's own bar count, not a number of its own."""
    from runtime.mvp_runtime.crypto.live_order import read_live_entry_marks

    record = _settle_venue_stop(tmp_path, monkeypatch, position=_position(timeframe=timeframe))
    assert record["live_settled"]["status"] == live_leg.EXIT_CLOSED
    assert read_live_entry_marks(tmp_path)["cooldown"] == {f"BTCUSDT__{timeframe}": until}


def test_the_cooldown_is_anchored_no_earlier_than_the_read_that_saw_the_fill(tmp_path, monkeypatch):
    """Review of #880: `now` is the fan-out's start, and a stop can fill after it and still be
    settled in the same pass. Here the pass started at 07:59:50 and the settlement was recorded at
    08:00:40 — the fill was in the 08:00 bar, so paper holds 08:00 and 12:00 and 16:00 is the
    first free bar. Anchoring on `now` would have freed 12:00."""
    from runtime.mvp_runtime.crypto.live_order import read_live_entry_marks

    record = _settle_venue_stop(tmp_path, monkeypatch, position=_position(timeframe="4h"),
                                now="2026-07-28T07:59:50Z", wall="2026-07-28T08:00:40Z")
    assert read_live_entry_marks(tmp_path)["cooldown"] == {"BTCUSDT__4h": "2026-07-28T16:00:00Z"}
    assert record["live_stop_cooldown"]["anchored_at"] == "2026-07-28T08:00:40Z"


def test_a_wall_clock_behind_the_pass_never_shortens_the_cooldown(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto.live_order import read_live_entry_marks

    _settle_venue_stop(tmp_path, monkeypatch, position=_position(timeframe="4h"),
                       now="2026-07-28T08:00:10Z", wall="2026-07-28T07:59:00Z")
    assert read_live_entry_marks(tmp_path)["cooldown"] == {"BTCUSDT__4h": "2026-07-28T16:00:00Z"}


@pytest.mark.parametrize("position,leg", [
    (_position(timeframe="4h"), "tp-1"),   # the target filled: not a stop-out
    (_position(), "sl-1"),                 # no timeframe (a probe, a legacy record): no context
    (_position(timeframe="2h"), "sl-1"),   # a timeframe this runtime has no bar length for
])
def test_only_a_stop_on_a_routed_context_starts_a_cooldown(tmp_path, monkeypatch, position, leg):
    from runtime.mvp_runtime.crypto.live_order import read_live_entry_marks

    record = _settle_venue_stop(tmp_path, monkeypatch, position=position, filled_leg=leg, price=60000.0)
    assert record["live_settled"]["status"] == live_leg.EXIT_CLOSED
    assert read_live_entry_marks(tmp_path)["cooldown"] == {}
    assert "live_stop_cooldown" not in record


@pytest.mark.parametrize("error,code", [
    ("persistence", "LIVE_ENTRY_MARKS_LOCKED"),
    ("os", "UNEXPECTED_OSError"),          # what the real store's mkdir/open/fsync/replace raise
    ("gate", "SAFETY_GATE_BLOCKED"),
])
def test_a_cooldown_that_cannot_be_written_is_reported_and_the_settlement_stands(
        tmp_path, monkeypatch, error, code):
    from runtime.mvp_runtime.errors import PersistenceError, SafetyGateBlocked

    class _Broken:
        def record_stop_cooldown(self, **kw):
            if error == "persistence":
                raise PersistenceError("LIVE_ENTRY_MARKS_LOCKED", "scripted")
            if error == "gate":
                raise SafetyGateBlocked("SAFETY_GATE_BLOCKED", "scripted")
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(live_route, "select_live_entry_marks", lambda **kw: _Broken())
    record = _settle_venue_stop(tmp_path, monkeypatch, position=_position(timeframe="4h"))
    assert record["live_settled"]["status"] == live_leg.EXIT_CLOSED
    assert record["live_reason_codes"][-2:] == [live_route.STOP_COOLDOWN_UNRECORDED, code]
    assert record["halt"] is False


class _History:
    """The account's fill list, as `AccountFeed.fill_history` returns it."""

    def __init__(self, rows):
        self.rows = rows

    def fill_history(self, symbol, *, start_ms, timeout_seconds):
        return list(self.rows)


def test_a_stop_settled_from_the_fill_history_still_starts_the_cooldown(tmp_path, monkeypatch):
    """Review of #880, the medium finding. The venue's stop filled, but the query for the stop leg
    failed on the settling tick, so the settlement priced the exit from the fill history and
    labelled it `venue_external_close`. The cooldown started only on `stop_loss`, and the next bar
    entered again. The external label now holds the context like a stop."""
    from runtime.mvp_runtime.crypto.live_order import (
        LIVE_ENTRY_STOP_LOSS_COOLDOWN,
        read_live_entry_marks,
    )
    from runtime.mvp_runtime.crypto.live_position import list_open_live_positions

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    opened = run("2026-07-28T04:05:00Z", BAR_00)
    assert opened["live_route_status"] == live_route.ROUTE_OPENED, opened["live_reason_codes"]

    [position] = list_open_live_positions(tmp_path)
    qty = float(position["quantity"])
    fill_ms = 1_785_211_800_000        # 2026-07-28T04:10:00Z
    monkeypatch.setattr(live_route, "select_account_feed", lambda **kw: _History([
        {"side": "SELL", "time": fill_ms, "qty": str(qty), "quoteQty": str(round(qty * SL_FILL, 8)),
         "orderId": 991},
    ]))
    real_fetch = venue.fetch_order

    def _stop_leg_unreadable(symbol, client_order_id, **kw):
        if client_order_id == position["stop_client_order_id"]:
            raise ToolError("ORDER_TRANSPORT", "scripted: the leg query failed")
        return real_fetch(symbol, client_order_id, **kw)

    monkeypatch.setattr(venue, "fetch_order", _stop_leg_unreadable)
    settled = run("2026-07-28T04:20:00Z", BAR_00)
    assert settled["live_route_status"] == live_route.ROUTE_SETTLED
    assert settled["live_settled"]["outcome"]["close_reason"] == live_leg.CLOSE_REASON_VENUE_EXTERNAL
    assert read_live_entry_marks(tmp_path)["cooldown"] == {"BTCUSDT__4h": "2026-07-28T12:00:00Z"}

    held = run("2026-07-28T08:05:00Z", "2026-07-28T04:00:00Z")
    assert held["live_decision"]["reasons"] == [LIVE_ENTRY_STOP_LOSS_COOLDOWN]
    assert len(venue.entries()) == 1, "the next bar entered again after a stop-out"



# --- the pre-order gate on the live leg (PR2b) ----------------------------------------------------

def test_an_entry_leaves_only_under_a_recorded_snapshot_the_book_names(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto import pre_order_gate
    from runtime.mvp_runtime.crypto.live_position import list_open_live_positions

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    opened = run("2026-07-28T04:05:00Z", BAR_00)
    assert opened["live_route_status"] == live_route.ROUTE_OPENED, opened["live_reason_codes"]
    [recorded] = pre_order_gate.read_snapshots(tmp_path)
    assert recorded["approved"] is True
    assert recorded["client_order_id"] == venue.entries()[0]["newClientOrderId"]
    authority = recorded["approved_profile"]["authority"]
    assert authority["approval_id"] == _ARM["approval_id"]
    # PR2c-2b: the arm the order leaves under is the record Thomas answered, verified at the gate.
    assert authority["approval_verified"] is True and authority["approval_problem"] is None
    assert authority["approval_fingerprint"] == _ARM["action_fingerprint"]
    assert opened["live_pre_order_reread"]["live_arm"] == {
        "approval_id": _ARM["approval_id"], "approval_fingerprint": _ARM["action_fingerprint"],
        "approval_verified": True, "approval_problem": None}
    assert recorded["approved_profile"]["stage"]["approval_id"] == "approval_stage_test"
    assert opened["live_pre_order_gate"]["risk_snapshot_sha256"] == recorded["risk_snapshot_sha256"]
    [position] = list_open_live_positions(tmp_path)
    assert position["risk_snapshot_sha256"] == recorded["risk_snapshot_sha256"]
    assert opened["live_opened"]["entry"]["risk_snapshot_sha256"] == recorded["risk_snapshot_sha256"]


@pytest.mark.parametrize("approvals", [None, {}, {"S001": None}, {"S002": "approval_other"}])
def test_a_strategy_without_its_arming_approval_is_held_before_anything_is_spent(
        tmp_path, monkeypatch, approvals):
    """Decision 17: the arming approval is part of the approved profile. An entry the decision
    found READY for a strategy that names none is held by the gate — no bar, no slot, no row."""
    from runtime.mvp_runtime.crypto import pre_order_gate
    from runtime.mvp_runtime.crypto.live_order import count_today, read_live_entry_marks

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    original = live_route.run_live_leg

    def without_approval(**kw):
        return original(**{**kw, "live_arm_approvals": approvals})

    monkeypatch.setattr(live_route, "run_live_leg", without_approval)
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_route_status"] == live_route.ROUTE_HELD
    assert held["live_decision"]["ready"] is True
    assert live_route.PRE_ORDER_GATE_REFUSED in held["live_reason_codes"]
    assert "approved_profile_complete" in held["live_reason_codes"]
    assert held["live_pre_order_gate"]["approved"] is False
    assert venue.submitted == []
    assert pre_order_gate.read_snapshots(tmp_path) == []
    assert read_live_entry_marks(tmp_path)["entered"] == {}
    assert count_today(tmp_path) == 0


def test_a_leg_whose_caller_names_no_approvals_opens_nothing(tmp_path, monkeypatch):
    """The parameter's default is the refusing one."""
    import inspect

    assert inspect.signature(live_route.run_live_leg).parameters["live_arm_approvals"].default is None


def test_a_venue_settled_trade_still_names_the_snapshot_it_opened_under():
    ledger = _Ledger()
    adapter = _Adapter(orders={"sl-1": _venue_order("FILLED", price=59000.0)})
    result = live_leg.settle_venue_closed_position(
        _position(risk_snapshot_sha256="sha256:" + "c" * 64), adapter=adapter,
        position_store=_Store(), ledger=ledger, now=NOW,
    )
    assert result["status"] == live_leg.EXIT_CLOSED
    assert ledger.appended[0]["risk_snapshot_sha256"] == "sha256:" + "c" * 64


@pytest.mark.parametrize("code", ["LIVE_ENTRY_CLAIM_LOST", "LIVE_POSITION_SLOT_TAKEN"])
def test_two_positions_meeting_on_one_symbol_halt_the_fan_out(code):
    """PR2b-2 review: an entry that outlived its claim, or a book asked to replace or clear another
    position's record, means two positions met on one symbol — an incident, like a failed book write."""
    assert live_route._is_incident({"reason_codes": [code]}) is True


# --- PR2c-0: a protective order left resting is the operator's to withdraw -------------------------

def _left_leg_record(status, *, symbol="BTCUSDT", left_symbol="BTCUSDT", **extra):
    return {"live_route_status": status, "symbol": symbol,
            "live_reason_codes": [live_leg.BRACKET_CANCEL_FAILED], "live_opened": None,
            "live_legs_left": [{"symbol": left_symbol, "client_order_ids": ["sl-1"]}], **extra}


def test_a_close_that_left_a_leg_resting_tells_the_operator_what_to_run(monkeypatch):
    sent = _notified(_left_leg_record(live_route.ROUTE_SETTLED), monkeypatch)
    assert len(sent) == 1
    assert sent[0].startswith("[LIVE] a position closed, but protective orders may still rest")
    assert "BTCUSDT: sl-1" in sent[0]
    assert "python -m scripts.list_resting_orders --symbol BTCUSDT" in sent[0]
    # No position was opened on this pass: no position lines of Nones.
    assert "side     :" not in sent[0] and "quantity :" not in sent[0]


def test_the_notice_names_the_symbol_the_close_was_for_not_the_passs_own(monkeypatch):
    """PR2c-0 review: an ETHUSDT pass settles a BTCUSDT position the venue closed; the leg left
    behind is BTCUSDT's."""
    [message] = _notified(_left_leg_record(live_route.ROUTE_SETTLED, symbol="ETHUSDT",
                                           left_symbol="BTCUSDT"), monkeypatch)
    assert "--symbol BTCUSDT" in message and "--symbol ETHUSDT" not in message
    assert "BTCUSDT: sl-1" in message


def test_an_incident_that_left_legs_names_them(monkeypatch):
    record = _left_leg_record(live_route.ROUTE_INCIDENT)
    record["live_reason_codes"] = [live_leg.NAKED_CLOSE_FAILED, live_leg.BRACKET_LEFT_RESTING]
    [message] = _notified(record, monkeypatch)
    assert message.startswith("[LIVE INCIDENT]")
    assert "BTCUSDT: sl-1" in message and "list_resting_orders --symbol BTCUSDT" in message
    assert "Once the symbol holds no position" in message


def test_a_booked_positions_stop_and_target_reach_the_notice(monkeypatch):
    """The book stores `stop_loss`/`take_profit`; the notice used to read keys it never has."""
    record = _opened_record("OPENED")
    position = record["live_opened"]["position"]
    position["stop_loss"], position["take_profit"] = position.pop("stop_price"), position.pop("target_price")
    [message] = _notified(record, monkeypatch)
    assert "stop     : 63000.0" in message and "target   : 67000.0" in message


def test_a_close_names_the_symbol_its_result_was_for():
    record: dict = {}
    live_route._note_legs_left(record, {"symbol": "BTCUSDT", "cancels": [
        {"client_order_id": "sl-1", "error": "ORDER_TRANSPORT"},
        {"client_order_id": "tp-1", "error": None}]}, symbol="ETHUSDT")
    live_route._note_legs_left(record, {"symbol": "SOLUSDT", "cancels": []}, symbol="ETHUSDT")
    assert record["live_legs_left"] == [{"symbol": "BTCUSDT", "client_order_ids": ["sl-1"]}]


def test_a_reversed_entry_that_left_a_leg_says_both(monkeypatch):
    record = _left_leg_record(live_route.ROUTE_HELD)
    record["live_opened"] = {"status": "ENTRY_NAKED_CLOSED", "bracket": []}
    record["live_reason_codes"].append(live_leg.NAKED_POSITION_CLOSED)
    [message] = _notified(record, monkeypatch)
    assert message.startswith("[LIVE] entry filled but could not be protected")
    assert "Protective orders a close left at the venue" in message and "BTCUSDT: sl-1" in message


def test_a_clean_settle_stays_quiet(monkeypatch):
    assert _notified({"live_route_status": live_route.ROUTE_SETTLED, "live_reason_codes": []},
                     monkeypatch) == []


class _CancelFails(_Venue):
    def cancel_order(self, symbol, client_order_id, *, timeout_seconds: int = 10, algo: bool = False):
        from runtime.mvp_runtime.errors import ToolError

        raise ToolError("ORDER_TRANSPORT", "scripted cancel failure")


@pytest.mark.parametrize("cancel_fails", [True, False])
def test_the_settle_pass_notifies_only_when_a_leg_would_not_come_off(tmp_path, monkeypatch, cancel_fails):
    venue = _CancelFails() if cancel_fails else _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    notified: list[str] = []
    monkeypatch.setattr(live_route, "_notify_operator",
                        lambda record, **kw: notified.append(record["live_route_status"]))
    opened = run("2026-07-28T04:05:00Z", BAR_00)
    assert opened["live_route_status"] == live_route.ROUTE_OPENED, opened["live_reason_codes"]
    notified.clear()
    _stop_fills(venue, tmp_path)
    settled = run("2026-07-28T04:20:00Z", BAR_00)
    assert settled["live_route_status"] == live_route.ROUTE_SETTLED
    assert (live_leg.BRACKET_CANCEL_FAILED in settled["live_reason_codes"]) is cancel_fails
    assert notified == ([live_route.ROUTE_SETTLED] if cancel_fails else [])


# --- PR2c-0 review: a close sized from the book never strips the rest of a drifted position ------

def _settle_drifted(position, *, adapter, store=None, ledger=None, reasons=("POSITION_QUANTITY_MISMATCH",)):
    record = {"live_reason_codes": [], "live_settled": None, "halt": False, "live_protection": None}
    live_route._settle_or_protect(
        record, position,
        adapter=adapter, position_store=store or _Store(), ledger=ledger or _Ledger(),
        reconciliation={"status": "DRIFT", "books": {SYMBOL: {"reasons": list(reasons)}}},
        limits=_LIMITS, candle_ts="2026-07-28T00:00:00Z",
        context_timeframe=str(position.get("timeframe") or live_route.DEFAULT_TIMING_CONTEXT),
        now=NOW, root=None, timeout_seconds=10,
    )
    return record


@pytest.mark.parametrize("reason", ["POSITION_QUANTITY_MISMATCH", "POSITION_SIDE_MISMATCH"])
def test_a_time_exit_waits_while_the_venue_holds_a_different_position(reason):
    adapter = _protected_adapter()
    record = _settle_drifted(_timed(holding_candles=2), adapter=adapter, reasons=(reason,))
    assert record["live_settled"] is None
    assert live_route.LIVE_TIME_EXIT_HELD_ON_DRIFT in record["live_reason_codes"]
    assert adapter.submitted == [] and adapter.cancelled == []


def test_a_time_exit_still_fires_on_a_drift_a_settle_resolves_elsewhere():
    """Only this symbol's own quantity or side drift holds it."""
    adapter = _protected_adapter()
    record = _settle_drifted(_timed(holding_candles=2), adapter=adapter, reasons=())
    assert record["live_settled"]["status"] == live_leg.EXIT_CLOSED


def test_an_unprotected_drifted_position_is_closed_but_keeps_its_legs():
    """The unprotected close is the more urgent risk and still happens; the stop that may be what
    protects the rest the book does not know about stays."""
    adapter = _ClosingAdapter(orders={"sl-1": {"status": "NEW"}, "tp-1": {"status": "CANCELED"}})
    record = _settle_drifted(_position(), adapter=adapter)
    closed = record["live_settled"]
    assert closed["status"] == live_leg.EXIT_CLOSED
    assert adapter.cancelled == []
    assert live_leg.BRACKET_LEFT_RESTING in record["live_reason_codes"]
    assert record["live_legs_left"] == [{"symbol": SYMBOL, "client_order_ids": ["sl-1", "tp-1"]}]


def _halting_pass(tmp_path, monkeypatch, *, legs_left):
    from runtime.mvp_runtime.crypto.live_position import DRIFT, DRIFT_QUANTITY_MISMATCH

    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    monkeypatch.setattr(live_route, "read_account", lambda **kw: (_snapshot(), {}))
    monkeypatch.setattr(live_route, "list_open_live_positions",
                        lambda root: [{"symbol": SYMBOL, "position_id": "p1", "status": "OPEN"}])
    monkeypatch.setattr(live_route, "reconcile_positions", lambda local, snapshot, now: {
        "status": DRIFT, "books": {SYMBOL: {"reasons": [DRIFT_QUANTITY_MISMATCH]}}})

    def _settled(record, position, **kw):
        record["live_settled"] = {"status": live_leg.EXIT_CLOSED, "reason_codes": []}
        if legs_left:
            record["live_legs_left"] = [{"symbol": SYMBOL, "client_order_ids": ["sl-1"]}]

    monkeypatch.setattr(live_route, "_settle_or_protect", _settled)
    notified: list = []
    monkeypatch.setattr(live_route, "_notify_operator",
                        lambda record, **kw: notified.append(record["live_route_status"]))
    record = live_route.run_live_leg(
        live_routable_strategy_ids={"S1"}, route={"strategy_id": "S1"},
        feature_row={"timestamp": NOW}, verdict={"allow_new_position": True},
        symbol=SYMBOL, collector=object(), now=NOW, root=tmp_path,
    )
    return record, notified


@pytest.mark.parametrize("legs_left", [False, True])
def test_a_drift_a_settle_cannot_resolve_halts_even_after_a_settle(tmp_path, monkeypatch, legs_left):
    """Only a position the venue already closed is resolved by settling it. A quantity drift is
    not, so the pass halts even though it settled something — and a leg left behind on the way is
    still reported on the halted pass."""
    record, notified = _halting_pass(tmp_path, monkeypatch, legs_left=legs_left)
    assert record["halt"] is True and record["live_route_status"] == live_route.ROUTE_INCIDENT
    assert live_route.BOOK_DRIFT in record["live_reason_codes"]
    assert notified == ([live_route.ROUTE_INCIDENT] if legs_left else [])


class _TargetRefusedCancelFails(_CancelFails):
    """The target leg is refused, so the entry is closed again; the stop that did rest will not
    come off."""

    def submit(self, order_request, *, timeout_seconds: int = 10):
        if order_request.get("type") == "LIMIT":
            from runtime.mvp_runtime.errors import ToolError

            self.submitted.append(dict(order_request))
            raise ToolError("ORDER_REJECTED", "venue rejected the order (code -2019): scripted")
        return super().submit(order_request, timeout_seconds=timeout_seconds)


def test_an_entry_that_left_a_leg_is_on_the_record_and_the_notice(tmp_path, monkeypatch):
    venue = _TargetRefusedCancelFails()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    sent: list[dict] = []
    monkeypatch.setattr(live_route, "_notify_operator", lambda record, **kw: sent.append(record))
    record = run("2026-07-28T04:05:00Z", BAR_00)
    assert record["live_opened"]["status"] == live_leg.ENTRY_NAKED_CLOSED
    stop_id = next(r["clientAlgoId"] for r in venue.submitted if r.get("clientAlgoId"))
    assert record["live_legs_left"] == [{"symbol": SYMBOL, "client_order_ids": [stop_id]}]
    assert sent and sent[-1]["live_legs_left"] == record["live_legs_left"]


class _ClosingCancelFails(_ClosingAdapter):
    def cancel_order(self, symbol, client_order_id, *, timeout_seconds: int = 10, algo: bool = False):
        raise ToolError("ORDER_TRANSPORT", "scripted cancel failure")


def test_a_time_exit_that_left_a_leg_is_on_the_record():
    adapter = _ClosingCancelFails(orders={"sl-1": {"status": "NEW"}, "tp-1": {"status": "NEW"}})
    record = _settle(_timed(holding_candles=2), adapter=adapter)
    assert record["live_settled"]["status"] == live_leg.EXIT_CLOSED
    assert record["live_legs_left"] == [{"symbol": SYMBOL, "client_order_ids": ["sl-1", "tp-1"]}]


# --- the entry is judged fresh at the moment it is decided (PR2c-1) ------------------------------

def test_a_context_with_no_route_asks_the_venue_for_no_price(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(live_route, "_read_reference_quote",
                        lambda *a, **kw: asked.append(a) or {"price": 1.0})
    seen, _record = _entry_decision_inputs(tmp_path, monkeypatch, realized_windows={})
    assert asked == [] and seen["reference_quote"] is None


def test_the_decision_is_handed_the_market_price_and_the_moment_it_is_judged(tmp_path, monkeypatch):
    quote = {"price": 60000.0, "close_time": "2026-07-27T23:59:00Z", "timeframe": "1m", "reason": None}
    asked = []

    def _quote(collector, symbol, *, now, timeout_seconds):
        asked.append((symbol, now))
        return quote

    monkeypatch.setattr(live_route, "_read_reference_quote", _quote)
    monkeypatch.setattr(live_route, "_entry_clock", lambda: "2026-07-28T00:00:09Z")
    monkeypatch.setattr(live_route, "build_entry_plan", lambda route, row, now: dict(_PLAN))
    seen, _record = _entry_decision_inputs(tmp_path, monkeypatch, realized_windows={}, route={"status": "X"})
    assert asked == [(SYMBOL, NOW)]
    assert seen["reference_quote"] is quote and seen["clock"] == "2026-07-28T00:00:09Z"


def test_a_market_price_that_cannot_be_read_costs_the_entry_not_the_fan_out():
    class _NoCandles:
        def order_book(self, symbol, *, limit, timeout_seconds):
            return {}

    quote = live_route._read_reference_quote(_NoCandles(), SYMBOL, now=NOW, timeout_seconds=5)
    assert quote["price"] is None and quote["reason"] == "REFERENCE_PRICE_UNREADABLE"
    assert quote["error"] == "AttributeError"


def test_a_market_that_moved_from_the_bar_close_holds_the_entry_and_spends_nothing(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto.live_entry import PRICE_DIVERGED
    from runtime.mvp_runtime.crypto.live_order import count_today, read_live_entry_marks

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    monkeypatch.setattr(live_route, "_read_reference_quote", lambda collector, symbol, **kw: {
        "price": _PLAN["entry_price"] * 1.006, "close_time": "2026-07-28T04:04:00Z",
        "timeframe": "1m", "reason": None})
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_route_status"] == live_route.ROUTE_HELD
    assert held["live_decision"]["reasons"] == [PRICE_DIVERGED]
    assert PRICE_DIVERGED in held["live_reason_codes"]
    assert venue.entries() == [] and count_today(tmp_path) == 0
    assert read_live_entry_marks(tmp_path)["entered"] == {}


def test_a_market_price_that_cannot_be_used_says_why_on_the_record(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto.live_entry import REFERENCE_PRICE_UNUSABLE

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    monkeypatch.setattr(live_route, "_read_reference_quote", lambda collector, symbol, **kw: {
        "price": None, "close_time": None, "timeframe": "1m", "reason": "REFERENCE_PRICE_SYNTHETIC"})
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_route_status"] == live_route.ROUTE_HELD
    assert held["live_decision"]["reasons"] == [REFERENCE_PRICE_UNUSABLE]
    assert {"REFERENCE_PRICE_SYNTHETIC", REFERENCE_PRICE_UNUSABLE} <= set(held["live_reason_codes"])
    assert venue.entries() == []


def test_a_decision_that_waited_over_a_minute_is_not_sent(tmp_path, monkeypatch):
    """The venue door judges the send at the wall clock: sixty-one seconds after the decision the
    leg refuses before it takes the symbol, the bar or the day's slot."""
    from runtime.mvp_runtime.crypto.live_order import count_today, read_live_entry_marks

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    monkeypatch.setattr(pre_order_gate, "_send_clock",
                        lambda: timeutil.plus_seconds("2026-07-28T04:05:00Z", 61))
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_pre_order_gate"]["approved"] is True
    assert held["live_opened"]["reason_codes"] == [pre_order_gate.RISK_SNAPSHOT_STALE]
    assert held["live_route_status"] == live_route.ROUTE_HELD
    assert venue.entries() == [] and count_today(tmp_path) == 0
    marks = read_live_entry_marks(tmp_path)
    assert marks["entered"] == {} and not marks.get("in_flight")

    # Sent within the minute, the same bar enters.
    monkeypatch.setattr(pre_order_gate, "_send_clock", lambda: "2026-07-28T04:20:30Z")
    opened = run("2026-07-28T04:20:00Z", BAR_00)
    assert opened["live_route_status"] == live_route.ROUTE_OPENED, opened["live_reason_codes"]


_JUDGED_READS = (
    "read_account", "resolve_live_order_limits", "resolve_execution_stage", "list_open_live_positions",
    "read_symbol_filters", "_read_reference_quote", "live_risk_snapshot",
    "bracket_breaker_status", "read_live_entry_marks", "count_today",
)


def test_the_decision_clock_is_read_after_every_fact_it_judges(tmp_path, monkeypatch):
    """Read before any of them, it would make that fact look younger than it is (PR2c-1)."""
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    order: list[str] = []

    def _tracked(name, read):
        def tracked(*args, **kwargs):
            order.append(name)
            return read(*args, **kwargs)
        return tracked

    for name in (*_JUDGED_READS, "_entry_clock"):
        monkeypatch.setattr(live_route, name, _tracked(name, getattr(live_route, name)))
    # The book is read through the collector, and the decision derives the spread from it (PR2d-3).
    monkeypatch.setattr(_Collector, "order_book", _tracked("order_book", _Collector.order_book))
    opened = run("2026-07-28T04:05:00Z", BAR_00)
    assert opened["live_route_status"] == live_route.ROUTE_OPENED, opened["live_reason_codes"]
    assert order.count("_entry_clock") == 1, order
    assert set(order[:order.index("_entry_clock")]) == {*_JUDGED_READS, "order_book"}, order


# --- the gate re-reads what another writer can move (PR2c-2a) ------------------------------------

def _nothing_spent(venue, tmp_path):
    from runtime.mvp_runtime.crypto.live_order import count_today, read_live_entry_marks

    marks = read_live_entry_marks(tmp_path)
    return venue.entries() == [] and count_today(tmp_path) == 0 and marks["entered"] == {} \
        and not marks.get("in_flight")


def test_a_strategy_disarmed_before_the_gate_is_held_and_spends_nothing(tmp_path, monkeypatch):
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    monkeypatch.setattr(live_route.pool, "load_active_pool", lambda root=None: {"active_strategies": []})
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_decision"]["ready"] is True
    assert held["live_route_status"] == live_route.ROUTE_HELD
    assert "strategy_armed_live" in held["live_pre_order_gate"]["failed_checks"]
    assert _nothing_spent(venue, tmp_path)


def test_a_strategy_re_armed_under_another_approval_is_held(tmp_path, monkeypatch):
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    monkeypatch.setattr(live_route.pool, "load_active_pool",
                        lambda root=None: _armed_pool("approval_arm_other"))
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_pre_order_gate"]["failed_checks"] == ["approved_profile_complete"]
    # The two reads name different approvals: neither is looked up.
    assert held["live_pre_order_reread"]["live_arm"] == {
        "approval_id": None, "approval_fingerprint": None, "approval_verified": False,
        "approval_problem": None}
    assert _nothing_spent(venue, tmp_path)


def test_a_soft_halt_before_the_gate_holds_the_entry(tmp_path, monkeypatch):
    from runtime.mvp_runtime.control import ACTIVE, ControlState, ControlStore

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    read_filters = live_route.read_symbol_filters

    def _halted_meanwhile(collector, symbol, **kw):
        ControlStore(tmp_path).save(ControlState(mode=ACTIVE, updated_by="op", updated_at=NOW,
                                                 reason="halt", trading_armed=False))
        return read_filters(collector, symbol, **kw)

    monkeypatch.setattr(live_route, "read_symbol_filters", _halted_meanwhile)
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_decision"]["ready"] is True, "the first read saw an armed runtime"
    assert held["live_pre_order_reread"]["runtime_active"] is False
    assert "runtime_active" in held["live_pre_order_gate"]["failed_checks"]
    assert _nothing_spent(venue, tmp_path)


def test_the_day_another_door_spent_before_the_gate_holds_the_entry(tmp_path, monkeypatch):
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    counts = iter([0, 3])
    monkeypatch.setattr(live_route, "count_today", lambda root=None: next(counts))
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_pre_order_reread"]["submitted_today"] == 3
    assert "daily_order_count_within_cap" in held["live_pre_order_gate"]["failed_checks"]
    assert venue.entries() == []


def test_a_re_read_that_fails_holds_the_entry_and_never_the_fan_out(tmp_path, monkeypatch):
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)

    def _unreadable(root=None):
        raise ToolError("STRATEGY_POOL_UNREADABLE", "scripted")

    monkeypatch.setattr(live_route.pool, "load_active_pool", _unreadable)
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_route_status"] == live_route.ROUTE_HELD and held["halt"] is False
    assert held["live_reason_codes"][-2:] == [live_route.PRE_ORDER_REREAD_FAILED, "STRATEGY_POOL_UNREADABLE"]
    assert "live_pre_order_gate" not in held
    assert _nothing_spent(venue, tmp_path)


@pytest.mark.parametrize("fresh_cap,reserved", [(2, 2), (5, 3)], ids=["lowered", "raised"])
def test_the_day_s_slot_is_reserved_against_the_stricter_cap(tmp_path, monkeypatch, fresh_cap, reserved):
    """A cap lowered before the gate binds the reservation; one raised does not widen it."""
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    first = live_route.resolve_live_order_limits(tmp_path, now=NOW)
    assert first[0].max_daily_order_count == 3
    changed = LiveOrderLimits(**{**first[0].__dict__, "max_daily_order_count": fresh_cap})
    reads = iter([first, (changed, first[1]), (changed, first[1])])
    monkeypatch.setattr(live_route, "resolve_live_order_limits", lambda root, now=None: next(reads))
    real_counter = live_route.select_live_order_counter
    reserved_limits: list[int] = []

    def _recording(now=None, root=None):
        counter = real_counter(now=now, root=root)
        reserve = counter.reserve_submission
        counter.reserve_submission = lambda *, limit, day=None: reserved_limits.append(limit) or reserve(limit=limit, day=day)
        return counter

    monkeypatch.setattr(live_route, "select_live_order_counter", _recording)
    opened = run("2026-07-28T04:05:00Z", BAR_00)
    assert opened["live_route_status"] == live_route.ROUTE_OPENED, opened["live_reason_codes"]
    assert reserved_limits == [reserved]


def test_today_s_loss_is_judged_again_against_a_limit_lowered_before_the_gate(tmp_path, monkeypatch):
    """Lost 10 today: under the first read's limit of 20, over the re-read's 5."""
    import dataclasses

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    read_account = live_route.read_account

    def _lost(**kw):
        snapshot, extra = read_account(**kw)
        return dataclasses.replace(
            snapshot, realized_windows={"today": {"net": -10.0}, "1d": {"net": -10.0}}), extra

    monkeypatch.setattr(live_route, "read_account", _lost)
    first = live_route.resolve_live_order_limits(tmp_path, now=NOW)
    assert first[0].daily_loss_limit_usdt == 20.0
    lowered = LiveOrderLimits(**{**first[0].__dict__, "daily_loss_limit_usdt": 5.0})
    reads = iter([first, (lowered, first[1]), (lowered, first[1])])
    monkeypatch.setattr(live_route, "resolve_live_order_limits", lambda root, now=None: next(reads))
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_decision"]["ready"] is True, "the first read's limit was not reached"
    assert held["live_pre_order_reread"]["daily_loss_breached"] is True
    assert "daily_loss_within_limit" in held["live_pre_order_gate"]["failed_checks"]
    assert _nothing_spent(venue, tmp_path)


def test_a_legacy_budget_window_is_judged_at_the_decision_too(tmp_path, monkeypatch):
    """Valid at the fire's start, expired by the moment the decision was judged."""
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    limits, budget = live_route.resolve_live_order_limits(tmp_path, now=NOW)
    window = {**budget, "valid_from": "2026-07-28T00:00:00Z", "valid_until": "2026-07-28T04:05:10Z"}

    def _windowed(root, now=None):
        inside = window["valid_from"] <= str(now) <= window["valid_until"]
        return limits, {**window, "valid": inside, "error": None if inside else "OUTSIDE_VALIDITY_WINDOW"}

    monkeypatch.setattr(live_route, "resolve_live_order_limits", _windowed)
    monkeypatch.setattr(live_route, "_entry_clock", lambda: "2026-07-28T04:05:30Z")
    monkeypatch.setattr(pre_order_gate, "_send_clock", lambda: "2026-07-28T04:05:30Z")
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_decision"]["ready"] is True
    assert held["live_pre_order_reread"]["budget_registered"] is False
    assert {"budget_registered", "approved_profile_complete"} <= set(held["live_pre_order_gate"]["failed_checks"])
    assert venue.entries() == []


def test_a_budget_re_registered_between_the_two_re_reads_does_not_back_the_order(tmp_path, monkeypatch):
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    limits, budget = live_route.resolve_live_order_limits(tmp_path, now=NOW)
    reads = iter([(limits, budget), (limits, budget),
                  (limits, {**budget, "budget_id": "budget_new", "record_sha256": "sha256:" + "c" * 64})])
    monkeypatch.setattr(live_route, "resolve_live_order_limits", lambda root, now=None: next(reads))
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_pre_order_reread"]["budget_registered"] is False
    assert "budget_registered" in held["live_pre_order_gate"]["failed_checks"]
    assert venue.entries() == []


def test_risk_limits_that_moved_or_expired_before_the_gate_refuse(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto import guards
    from runtime.mvp_runtime.crypto.live_entry import RISK_LIMITS_CHANGED

    control = type("C", (), {"load": lambda self: type("S", (), {"trading_allowed": True})()})()
    judged = guards.DEFAULT_RISK_LIMITS.as_record()
    assert live_route.reread_entry_facts(root=tmp_path, now=NOW, venue_realized_pnl_usdt=0.0, venue_required=True, clock=NOW, control=control,
                                         judged_limits=judged)["risk_limits_problem"] is None

    registered = guards.RiskLimits(**{**guards.DEFAULT_RISK_LIMITS.__dict__, "source": "registered",
                                      "limits_id": "limits_new", "record_sha256": "sha256:" + "9" * 64})
    monkeypatch.setattr(live_route, "resolve_risk_limits", lambda root=None, *, now: registered)
    assert live_route.reread_entry_facts(root=tmp_path, now=NOW, venue_realized_pnl_usdt=0.0, venue_required=True, clock=NOW, control=control,
                                         judged_limits=judged)["risk_limits_problem"] == RISK_LIMITS_CHANGED

    def _expired_at_clock(root=None, *, now):
        if now != NOW:
            raise ToolError("RISK_LIMITS_EXPIRED", "scripted")
        return guards.DEFAULT_RISK_LIMITS

    monkeypatch.setattr(live_route, "resolve_risk_limits", _expired_at_clock)
    assert live_route.reread_entry_facts(root=tmp_path, now=NOW, venue_realized_pnl_usdt=0.0, venue_required=True, clock="2026-07-28T00:00:30Z", control=control,
                                         judged_limits=judged)["risk_limits_problem"] == "RISK_LIMITS_EXPIRED"


def test_a_door_no_pool_authorizes_does_not_read_the_pool(tmp_path, monkeypatch):
    def _must_not_read(root=None):
        raise AssertionError("the pool was read")

    monkeypatch.setattr(live_route.pool, "load_active_pool", _must_not_read)
    control = type("C", (), {"load": lambda self: type("S", (), {"trading_allowed": False})()})()
    fresh = live_route.reread_entry_facts(root=tmp_path, now=NOW, venue_realized_pnl_usdt=0.0, venue_required=True, clock=NOW, control=control,
                                          judged_limits={"source": "default"}, with_pool=False)
    assert (fresh["live_routable_strategy_ids"], fresh["live_arm_approvals"]) == (None, None)
    assert fresh["runtime_active"] is False
    assert fresh["bracket_breaker_tripped"] is False and fresh["submitted_today"] == 0


# --- the arming approval is verified at the gate (PR2c-2b) -------------------------------------

def _other_snapshot(**content):
    snapshot = dict(_ARM["approved_action_snapshot"])
    snapshot["normalized_parameters"] = {**snapshot["normalized_parameters"], **content}
    return snapshot


_UNBACKED_ARMS = {
    "no-record": (None, {}, "LIVE_ARM_APPROVAL_MISSING"),
    "pending": ({**_ARM, "status": "PENDING"}, {}, "LIVE_ARM_APPROVAL_NOT_APPROVED"),
    "rejected": ({**_ARM, "status": "REJECTED"}, {}, "LIVE_ARM_APPROVAL_NOT_APPROVED"),
    "not-thomas": ({**_ARM, "approver": {**_ARM["approver"], "approved_by": "Hermes"}}, {},
                   "LIVE_ARM_APPROVER_UNVERIFIED"),
    "unverified": ({**_ARM, "approver": {**_ARM["approver"], "verification_status": "NOT_VERIFIED"}}, {},
                   "LIVE_ARM_APPROVER_UNVERIFIED"),
    "paper-promotion": (live_arm_approval((_PLAN["candidate_id"],), (_PLAN["strategy_rule_hash"],),
                                          live_tier="OBSERVATION"), {}, "LIVE_ARM_APPROVAL_NOT_AN_ARM"),
    "edited-after-the-answer": ({**_ARM, "approved_action_snapshot": _other_snapshot(
        candidate_ids=["cand_1", "cand_2"])}, {}, "LIVE_ARM_APPROVAL_ALTERED"),
    "another-candidate": (live_arm_approval(("cand_2",), (_PLAN["strategy_rule_hash"],)), {},
                          "LIVE_ARM_APPROVAL_OTHER_CANDIDATE"),
    "another-rule": (live_arm_approval((_PLAN["candidate_id"],), (_OTHER_SPEC["strategy_rule_hash"],)), {},
                     "LIVE_ARM_APPROVAL_OTHER_CANDIDATE"),
    "installed-before-the-answer": (_ARM, {"promoted_at": "2026-07-27T23:49:59Z"},
                                    "LIVE_ARM_INSTALLED_OUTSIDE_APPROVAL"),
    "installed-after-it-expired": (_ARM, {"promoted_at": _ARM["validity"]["expires_at"]},
                                   "LIVE_ARM_INSTALLED_OUTSIDE_APPROVAL"),
    "no-install-time": (_ARM, {"promoted_at": None}, "LIVE_ARM_INSTALLED_OUTSIDE_APPROVAL"),
    "entry-for-another-lineage": (_ARM, {"strategy_rule_hash": _OTHER_SPEC["strategy_rule_hash"],
                                         "strategy_spec": _OTHER_SPEC}, "LIVE_ARM_ENTRY_CHANGED"),
    # Armed under a real approval for its own lineage, which is not the lineage the plan was made from.
    "entry-armed-for-another-candidate": (live_arm_approval(("cand_2",), (_PLAN["strategy_rule_hash"],)),
                                          {"candidate_id": "cand_2"}, "LIVE_ARM_ENTRY_CHANGED"),
    "entry-armed-for-another-rule": (
        live_arm_approval((_PLAN["candidate_id"],), (_OTHER_SPEC["strategy_rule_hash"],)),
        {"strategy_rule_hash": _OTHER_SPEC["strategy_rule_hash"], "strategy_spec": _OTHER_SPEC},
        "LIVE_ARM_ENTRY_CHANGED"),
    # Review of #887: the rules traded are not the rule the label names, whatever the approval says.
    "spec-swapped-under-the-label": (_ARM, {"strategy_spec": {
        k: v for k, v in _OTHER_SPEC.items() if k != "strategy_rule_hash"}}, "LIVE_ARM_SPEC_NOT_ITS_RULE"),
    "spec-that-does-not-parse": (_ARM, {"strategy_spec": {"x": 1}}, "LIVE_ARM_SPEC_NOT_ITS_RULE"),
    "re-armed-by-hand-after-a-disarm": (_ARM, {"live_tier_updated_at": "2026-07-28T02:00:00Z",
                                               "live_tier_reasons": ["operator"]},
                                        "LIVE_ARM_REARMED_OUTSIDE_THE_DOOR"),
    # PR3a, decision 33: only an entry installed as an artifact, under an approval pairing that
    # artifact with its candidate, may spend money.
    "entry-predates-the-artifact": (_ARM, {"strategy_artifact_sha256": None, "strategy_artifact": None},
                                    "LIVE_ARM_ENTRY_UNBOUND"),
    "approval-asked-before-v5": (
        live_arm_approval((_PLAN["candidate_id"],), (_PLAN["strategy_rule_hash"],), artifacts=None), {},
        "LIVE_ARM_APPROVAL_UNBOUND"),
    "approval-pairs-another-artifact": (
        live_arm_approval((_PLAN["candidate_id"],), (_PLAN["strategy_rule_hash"],),
                          artifacts=("sha256:" + "e" * 64,)), {},
        "LIVE_ARM_APPROVAL_OTHER_ARTIFACT"),
}


@pytest.mark.parametrize("approval,entry,problem", list(_UNBACKED_ARMS.values()), ids=list(_UNBACKED_ARMS))
def test_an_arm_its_approval_does_not_back_is_held_before_anything_is_spent(
        tmp_path, monkeypatch, approval, entry, problem):
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue, approval=approval, armed_entry=entry)
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_decision"]["ready"] is True
    assert held["live_route_status"] == live_route.ROUTE_HELD
    assert held["live_pre_order_reread"]["live_arm"]["approval_problem"] == problem
    assert held["live_pre_order_reread"]["live_arm"]["approval_verified"] is False
    assert held["live_pre_order_gate"]["failed_checks"] == ["approved_profile_complete"]
    assert held["live_reason_codes"][-1] == problem
    assert _nothing_spent(venue, tmp_path)


def test_an_approval_store_that_cannot_be_read_holds_the_entry_and_never_the_fan_out(tmp_path, monkeypatch):
    from runtime.mvp_runtime.approval_store import ApprovalStore

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    ApprovalStore.default(tmp_path).path.write_text("{not json\n", encoding="utf-8")
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_route_status"] == live_route.ROUTE_HELD and held["halt"] is False
    assert held["live_pre_order_reread"]["live_arm"]["approval_problem"] == live_route.LIVE_ARM_APPROVAL_UNREADABLE
    assert held["live_pre_order_gate"]["failed_checks"] == ["approved_profile_complete"]
    assert _nothing_spent(venue, tmp_path)


def test_an_entry_that_names_another_approval_than_both_reads_is_not_verified(tmp_path):
    from runtime.mvp_runtime.approval_store import ApprovalStore

    ApprovalStore.default(tmp_path).append([_ARM])
    armed = {"S001": {"approval_id": _ARM["approval_id"], "candidate_id": _PLAN["candidate_id"],
                      "strategy_rule_hash": _PLAN["strategy_rule_hash"],
                      "spec_rule_hash": _PLAN["strategy_rule_hash"],
                      "strategy_artifact_sha256": _ARMED_STAMP, "promoted_at": _ARMED_AT,
                      "disarmed_at": None}}
    verify = lambda armed: live_route.verify_live_arm(  # noqa: E731
        root=tmp_path, strategy_id="S001", plan=_PLAN, approval_id=_ARM["approval_id"], armed=armed)
    assert verify(armed)["approval_verified"] is True
    other = {"S001": {**armed["S001"], "approval_id": "approval_other"}}
    assert verify(other)["approval_problem"] == live_route.LIVE_ARM_ENTRY_CHANGED
    assert verify({})["approval_problem"] == live_route.LIVE_ARM_ENTRY_CHANGED
    assert verify(None)["approval_problem"] == live_route.LIVE_ARM_ENTRY_CHANGED


def test_an_arm_both_reads_do_not_agree_on_is_not_looked_up(tmp_path, monkeypatch):
    from runtime.mvp_runtime.approval_store import ApprovalStore

    def _must_not_read(self, approval_id):
        raise AssertionError("the approval store was read")

    monkeypatch.setattr(ApprovalStore, "get", _must_not_read)
    arm = live_route.verify_live_arm(root=tmp_path, strategy_id="S001", plan=_PLAN, approval_id=None,
                                     armed=None)
    assert arm == {"approval_id": None, "approval_fingerprint": None, "approval_verified": False,
                   "approval_problem": None}


def test_a_failure_verifying_the_arm_holds_the_entry_as_a_failed_re_read(tmp_path, monkeypatch):
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)

    def _broken(**kw):
        raise RuntimeError("scripted")

    monkeypatch.setattr(live_route, "verify_live_arm", _broken)
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_route_status"] == live_route.ROUTE_HELD and held["halt"] is False
    assert held["live_reason_codes"][-2:] == [live_route.PRE_ORDER_REREAD_FAILED, "RuntimeError"]
    assert _nothing_spent(venue, tmp_path)


# --- PR2c-3: the global caps at the claim, and what rests at the venue ----------------------------

def _another_door_in_flight(tmp_path, *, notional):
    from runtime.mvp_runtime.crypto.live_order import LIVE_TRADING_FLAGS, LIVE_TRADING_PROVIDER_ID, \
        LiveEntryMarks
    from tests._helpers import make_gate_authorization

    auth = make_gate_authorization(flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID)
    LiveEntryMarks(root=tmp_path, authorization=auth).claim_symbol(
        symbol="SOLUSDT", door="probe", client_order_id="TAI_SOLUSDT_LONG_probe", now=NOW,
        notional_usdt=notional, exposure={"open_notional_usdt": 0.0, "position_ids": [], "cap_usdt": 120.0})


def test_an_entry_another_door_leaves_no_exposure_for_is_held_before_the_bar(tmp_path, monkeypatch):
    """Decision 26: the probe on SOL took 100 of the 120 cap after this leg read the account."""
    from runtime.mvp_runtime.crypto.live_order import LIVE_ENTRY_EXPOSURE_TAKEN, read_live_entry_marks

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    _another_door_in_flight(tmp_path, notional=100.0)
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_pre_order_gate"]["approved"] is True
    assert LIVE_ENTRY_EXPOSURE_TAKEN in held["live_reason_codes"]
    assert venue.entries() == []
    marks = read_live_entry_marks(tmp_path)
    assert set(marks["in_flight"]) == {"SOLUSDT"} and marks["entered"] == {}


def test_an_entry_the_other_door_leaves_room_for_opens(tmp_path, monkeypatch):
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    _another_door_in_flight(tmp_path, notional=10.0)
    opened = run("2026-07-28T04:05:00Z", BAR_00)
    assert opened["live_route_status"] == live_route.ROUTE_OPENED, opened["live_reason_codes"]


def test_a_stop_left_resting_on_the_symbol_holds_the_next_entry(tmp_path, monkeypatch):
    """Decision 25, end to end: a trade's stop that was never withdrawn is still at the venue when
    the next entry on the symbol comes; the entry is refused and nothing is withdrawn."""
    from runtime.mvp_runtime.crypto.live_order import read_live_entry_marks

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    venue.requests["TAI_BTCUSDT_SL_left"] = {"symbol": SYMBOL, "type": "STOP_MARKET", "side": "SELL",
                                             "clientAlgoId": "TAI_BTCUSDT_SL_left"}
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert live_leg.RESTING_ORDERS in held["live_reason_codes"]
    assert venue.entries() == [] and venue.cancelled == []
    assert read_live_entry_marks(tmp_path)["entered"] == {}
    assert read_live_entry_marks(tmp_path)["in_flight"] == {}, "the symbol was given back"


# --- the API error breaker in the leg (PR2d-1, Thomas decisions 18 and 27) ------------------------

def _api_breaker(tmp_path):
    from runtime.mvp_runtime.crypto.live_order import LiveApiErrorBreaker
    from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_FLAGS, LIVE_TRADING_PROVIDER_ID
    from tests._helpers import make_gate_authorization

    auth = make_gate_authorization(flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID)
    return LiveApiErrorBreaker(root=tmp_path, authorization=auth)


def _api_failures(tmp_path, count, call_class="write"):
    """Earlier failures, on a call this pass does not make — so the record's last call names the
    one this pass's own call latched."""
    breaker = _api_breaker(tmp_path)
    call = "cancel_order" if call_class == "write" else "open_orders"
    for _ in range(count):
        breaker.record_failure(call_class=call_class, call=call, at=NOW, reason_code="TOOL_RATE_LIMITED")
    return breaker


def _messages(monkeypatch, *, delivered=True):
    """What the operator's chat was handed; ``delivered`` is whether the channel took it."""
    sent: list[str] = []

    def _send(record, lines, **kw):
        sent.append("\n".join(lines))
        return delivered() if callable(delivered) else delivered

    monkeypatch.setattr(live_route, "_send_operator_text", _send)
    return sent


def _told(tmp_path):
    """The latch on record was told to the operator, as a pass before this test would have."""
    breaker = _api_breaker(tmp_path)
    claimed = breaker.claim_notice(at=NOW)
    breaker.mark_told(at=NOW, tripped_at=claimed["tripped_at"])


def test_a_tripped_api_breaker_holds_the_entry_and_spends_nothing(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto.live_entry import API_BREAKER_REFUSED
    from runtime.mvp_runtime.crypto.live_order import MAX_CONSECUTIVE_API_ERRORS

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    _api_failures(tmp_path, MAX_CONSECUTIVE_API_ERRORS)
    _told(tmp_path)
    sent = _messages(monkeypatch)
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_route_status"] == live_route.ROUTE_HELD
    assert held["live_decision"]["reasons"] == [API_BREAKER_REFUSED]
    assert held["live_api_breaker"] == {"consecutive": MAX_CONSECUTIVE_API_ERRORS,
                                        "limit": MAX_CONSECUTIVE_API_ERRORS, "tripped": True,
                                        "tripped_class": "write", "unwritable": False}
    assert _nothing_spent(venue, tmp_path)
    # Told when it latched: not again on every pass.
    assert sent == [] and live_route.API_BREAKER_JUST_TRIPPED not in held["live_reason_codes"]


def test_a_latch_nobody_was_told_of_is_told_by_the_next_pass(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto.live_order import MAX_CONSECUTIVE_API_ERRORS, api_breaker_status

    run = _wire_whole_leg(tmp_path, monkeypatch, _Venue())
    _api_failures(tmp_path, MAX_CONSECUTIVE_API_ERRORS)
    sent = _messages(monkeypatch)
    first = run("2026-07-28T04:05:00Z", BAR_00)
    assert len(sent) == 1 and live_route.API_BREAKER_TOLD in first["live_reason_codes"]
    assert api_breaker_status(tmp_path)["told_at"] is not None
    run("2026-07-28T08:05:00Z", "2026-07-28T04:00:00Z")
    assert len(sent) == 1


class _SendFails(_Venue):
    """The venue answers every read and cannot take a send."""

    def submit(self, order_request, *, timeout_seconds: int = 10):
        raise ToolError("ORDER_TRANSPORT", "live order request failed or timed out")


def test_the_pass_whose_send_latches_the_breaker_tells_the_operator_once(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto.live_entry import API_BREAKER_REFUSED
    from runtime.mvp_runtime.crypto.live_order import MAX_CONSECUTIVE_API_ERRORS, api_breaker_status

    venue = _SendFails()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    _api_failures(tmp_path, MAX_CONSECUTIVE_API_ERRORS - 1)
    sent = _messages(monkeypatch)

    failed = run("2026-07-28T04:05:00Z", BAR_00)
    assert live_route.API_BREAKER_JUST_TRIPPED in failed["live_reason_codes"]
    status = api_breaker_status(tmp_path)
    assert status["tripped"] is True and status["write"]["last_call"] == "submit"
    [message] = sent
    assert f"class    : write ({MAX_CONSECUTIVE_API_ERRORS}/{MAX_CONSECUTIVE_API_ERRORS} in a row)" in message
    assert "last     : submit ORDER_TRANSPORT" in message
    assert "scripts.clear_api_breaker" in message

    # The next bar: the door is shut, and the operator is not told a second time.
    held = run("2026-07-28T08:05:00Z", "2026-07-28T04:00:00Z")
    assert API_BREAKER_REFUSED in held["live_decision"]["reasons"]
    assert len(sent) == 1
    assert live_route.API_BREAKER_JUST_TRIPPED not in held["live_reason_codes"]


class _ReadsFail(_Venue):
    def fetch_order(self, symbol, client_order_id, *, timeout_seconds: int = 10, algo: bool = False):
        raise ToolError("ORDER_TRANSPORT", "live order request failed or timed out")


@pytest.mark.parametrize("ending", ["returns", "refuses", "breaks"])
def test_a_latch_reaches_the_operator_however_the_pass_ends(tmp_path, monkeypatch, ending):
    """The latch can come from any signed call of the pass — a protect check, a settle, the
    entry — and the pass can end any way after it. The operator hears it once either way: the
    next pass reads the breaker as already tripped and would never say so."""
    from runtime.mvp_runtime.crypto.live_order import MAX_CONSECUTIVE_API_ERRORS

    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    monkeypatch.setattr(live_route, "select_live_gate", lambda **kw: (_ReadsFail(), None))
    _api_failures(tmp_path, MAX_CONSECUTIVE_API_ERRORS - 1, "read")
    sent = _messages(monkeypatch)

    def _leg(record, *, adapter, **kw):
        with pytest.raises(ToolError):
            adapter.fetch_order(SYMBOL, "sl-1")   # a protect check: the read that latches it
        if ending == "refuses":
            raise ToolError("LIVE_POSITION_BOOK_LOCKED", "scripted")
        if ending == "breaks":
            raise RuntimeError("scripted")
        return record

    monkeypatch.setattr(live_route, "_run_gated_live_leg", _leg)
    record = live_route.run_live_leg(
        route=None, live_routable_strategy_ids=None, feature_row={}, verdict={}, symbol=SYMBOL,
        collector=None, now=NOW, root=tmp_path)
    assert live_route.API_BREAKER_JUST_TRIPPED in record["live_reason_codes"]
    [message] = sent
    assert "class    : read" in message and "last     : fetch_order ORDER_TRANSPORT" in message


def test_an_unreadable_api_breaker_holds_entries_and_still_manages_positions(tmp_path, monkeypatch):
    """Fail-closed like the bracket breaker's record: it is read after settle/protect, so it can
    only hold entries — and the signed calls it could not count are named on the record."""
    from runtime.mvp_runtime.crypto.live_order import API_BREAKER_FILENAME, LIVE_API_BREAKER_UNREADABLE
    from runtime.mvp_runtime.crypto.state import venue_state_dir

    path = venue_state_dir(tmp_path) / API_BREAKER_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{torn", encoding="utf-8")
    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    monkeypatch.setattr(live_route, "read_account", lambda **kw: (_snapshot(), {}))
    monkeypatch.setattr(live_route, "list_open_live_positions",
                        lambda root: [{"symbol": SYMBOL, "position_id": "p1", "status": "OPEN"}])
    monkeypatch.setattr(live_route, "reconcile_positions",
                        lambda local, snapshot, now: {"status": "RECONCILED", "books": {}})
    managed: list[str] = []
    monkeypatch.setattr(live_route, "_settle_or_protect",
                        lambda record, position, **kw: managed.append(position["position_id"]))
    monkeypatch.setattr(live_route, "plan_live_entry",
                        lambda plan, **kw: pytest.fail("an unreadable breaker must refuse before the decision"))
    record = live_route.run_live_leg(
        live_routable_strategy_ids={"S1"}, route=None, feature_row={"timestamp": NOW},
        verdict={"allow_new_position": True}, symbol=SYMBOL, collector=object(), now=NOW,
        root=tmp_path,
    )
    assert managed == ["p1"]
    assert record["live_route_status"] == live_route.ROUTE_BLOCKED
    codes = record["live_reason_codes"]
    at = codes.index(live_route.ROUTING_PRECONDITION)
    assert codes[at + 1] == LIVE_API_BREAKER_UNREADABLE
    # The account read went uncounted, and the record says so rather than reading as counted.
    assert live_route.API_BREAKER_UNRECORDED in codes
    assert path.read_text(encoding="utf-8") == "{torn"


def test_an_api_breaker_that_trips_before_the_gate_holds_the_entry(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto.live_order import api_breaker_status

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    reads = iter([False, True])

    def _status(root=None):
        return {**api_breaker_status(root), "tripped": next(reads), "tripped_class": "write"}

    monkeypatch.setattr(live_route, "api_breaker_status", _status)
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_decision"]["ready"] is True, "the first read saw a clear breaker"
    assert held["live_pre_order_reread"]["api_breaker_tripped"] is True
    assert "api_breaker_clear" in held["live_pre_order_gate"]["failed_checks"]
    assert _nothing_spent(venue, tmp_path)


def test_a_failed_account_read_is_counted_by_the_feeds_own_code(tmp_path, monkeypatch):
    """`read_account` sums every failure up as one word, ACCOUNT_DATA_DEGRADED; the breaker has
    to read the feed's own code underneath it, or no account read would ever count."""
    from runtime.mvp_runtime.crypto import account
    from runtime.mvp_runtime.crypto.live_order import api_breaker_status

    class _Down:
        feed_id, feed_version, network_egress = "binance", "test", True

        def account_snapshot(self, *, timeout_seconds):
            raise ToolError("TOOL_TRANSPORT", "live account request failed or timed out")

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    monkeypatch.setattr(account, "select_account_feed", lambda **kw: _Down())
    monkeypatch.setattr(live_route, "read_account", account.read_account)
    record = run("2026-07-28T04:05:00Z", BAR_00)
    assert record["account_degraded_reason_code"] == account.ACCOUNT_DATA_DEGRADED
    read = api_breaker_status(tmp_path)["read"]
    assert (read["consecutive"], read["last_call"], read["last_reason_code"]) == (1, "read_account", "TOOL_TRANSPORT")
    assert venue.entries() == []

    # And a readable account the pass after is the read class's success: the streak ends.
    monkeypatch.setattr(live_route, "read_account", lambda **kw: (_snapshot(), {}))
    run("2026-07-28T04:20:00Z", BAR_00)
    assert api_breaker_status(tmp_path)["read"]["consecutive"] == 0


def test_the_fill_history_a_settlement_falls_back_to_is_counted_with_the_pass(tmp_path, monkeypatch):
    """The settlement's fallback read goes through the account feed, not the adapter. It is a
    signed read of the pass all the same, and counts in the read class (PR2d-1)."""
    from runtime.mvp_runtime.crypto.live_order import api_breaker_status
    from runtime.mvp_runtime.crypto.live_position import list_open_live_positions

    class _HistoryDown(_History):
        network_egress = True

        def fill_history(self, symbol, *, start_ms, timeout_seconds):
            raise ToolError("TOOL_TRANSPORT", "live account request failed or timed out")

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    opened = run("2026-07-28T04:05:00Z", BAR_00)
    assert opened["live_route_status"] == live_route.ROUTE_OPENED, opened["live_reason_codes"]
    [position] = list_open_live_positions(tmp_path)
    monkeypatch.setattr(live_route, "select_account_feed", lambda **kw: _HistoryDown([]))
    # The venue no longer holds the position, and neither leg says it filled.
    monkeypatch.setattr(live_route, "read_account", lambda **kw: (_snapshot(), {}))
    run("2026-07-28T04:20:00Z", BAR_00)
    read = api_breaker_status(tmp_path)["read"]
    assert (read["last_call"], read["last_reason_code"]) == ("fill_history", "TOOL_TRANSPORT")
    assert read["consecutive"] == 1
    assert list_open_live_positions(tmp_path) == [position], "an unpriced close keeps the book"


def test_a_notice_that_did_not_get_through_is_tried_again_on_a_later_pass(tmp_path, monkeypatch):
    """The outage that trips the breaker often takes the chat down with it. The notice is tried
    again — on a pass the retry interval later, not on every context of the same fan-out, since
    each attempt can hold a pass for the channel's timeout (review of #889)."""
    from runtime.mvp_runtime import timeutil as tu
    from runtime.mvp_runtime.crypto.live_order import (
        API_BREAKER_NOTICE_RETRY_SECONDS,
        MAX_CONSECUTIVE_API_ERRORS,
        api_breaker_status,
    )

    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    monkeypatch.setattr(live_route, "select_live_gate", lambda **kw: (_Venue(), None))
    monkeypatch.setattr(live_route, "_run_gated_live_leg", lambda record, **kw: record)
    _api_failures(tmp_path, MAX_CONSECUTIVE_API_ERRORS)
    outcomes = iter([False, True])
    sent = _messages(monkeypatch, delivered=lambda: next(outcomes))
    clock = {"now": NOW}
    monkeypatch.setattr(live_route, "_notice_clock", lambda: clock["now"])

    def _pass(seconds):
        clock["now"] = tu.plus_seconds(NOW, seconds)
        return live_route.run_live_leg(
            route=None, live_routable_strategy_ids=None, feature_row={}, verdict={}, symbol=SYMBOL,
            collector=None, now=clock["now"], root=tmp_path)

    first = _pass(0)                                           # the chat is down
    assert len(sent) == 1 and live_route.API_BREAKER_TOLD not in first["live_reason_codes"]
    _pass(30)                                                  # the next context of the fan-out
    assert len(sent) == 1
    later = _pass(API_BREAKER_NOTICE_RETRY_SECONDS)            # a cycle later: tried again
    assert len(sent) == 2 and live_route.API_BREAKER_TOLD in later["live_reason_codes"]
    assert api_breaker_status(tmp_path)["told_at"] == clock["now"]
    _pass(3 * API_BREAKER_NOTICE_RETRY_SECONDS)                 # told: never again for this latch
    assert len(sent) == 2


def test_an_unwritable_api_breaker_holds_every_entry_and_says_why(tmp_path, monkeypatch):
    """The record stays readable but cannot be replaced. An entry sent then would fail with
    nothing to count it — so the breaker is not clear, on this pass and the next (review of #889)."""
    from runtime.mvp_runtime.crypto.live_entry import API_BREAKER_REFUSED
    from runtime.mvp_runtime.crypto.live_order import API_BREAKER_FILENAME
    from runtime.mvp_runtime.crypto.state import venue_state_dir

    venue = _SendFails()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    _api_failures(tmp_path, 1)
    (venue_state_dir(tmp_path) / API_BREAKER_FILENAME).with_suffix(".tmp").mkdir()
    for now, bar in (("2026-07-28T04:05:00Z", BAR_00), ("2026-07-28T08:05:00Z", "2026-07-28T04:00:00Z")):
        held = run(now, bar)
        assert held["live_decision"]["reasons"] == [API_BREAKER_REFUSED], now
        assert held["live_api_breaker"]["unwritable"] is True
        codes = set(held["live_reason_codes"])
        # What writing over a directory raises: IsADirectoryError on POSIX, PermissionError on Windows.
        assert live_route.API_BREAKER_UNRECORDED in codes
        assert codes & {"IsADirectoryError", "PermissionError"}
    assert _nothing_spent(venue, tmp_path)


class _SendDropped(_Venue):
    """A double that lets a dropped connection escape untyped, as the real adapter did before the
    review of #889."""

    def submit(self, order_request, *, timeout_seconds: int = 10):
        import http.client

        raise http.client.RemoteDisconnected("Remote end closed connection without response")


def test_a_dropped_send_counts_even_when_it_escapes_untyped(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto.live_entry import API_BREAKER_REFUSED
    from runtime.mvp_runtime.crypto.live_order import MAX_CONSECUTIVE_API_ERRORS, api_breaker_status

    venue = _SendDropped()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    _api_failures(tmp_path, MAX_CONSECUTIVE_API_ERRORS - 1)
    _messages(monkeypatch)
    dropped = run("2026-07-28T04:05:00Z", BAR_00)
    assert live_route.API_BREAKER_JUST_TRIPPED in dropped["live_reason_codes"]
    assert api_breaker_status(tmp_path)["write"]["last_reason_code"] == "RemoteDisconnected"
    held = run("2026-07-28T08:05:00Z", "2026-07-28T04:00:00Z")
    assert API_BREAKER_REFUSED in held["live_decision"]["reasons"]


@pytest.mark.parametrize("egress,told", [(False, False), (True, True)], ids=["inert-channel", "telegram"])
def test_only_a_channel_that_reaches_someone_counts_as_told(tmp_path, monkeypatch, egress, told):
    """The inert channel takes every message and tells nobody: sending through it is no notice, so
    the latch stays untold and the next cycle tries again."""
    from runtime.mvp_runtime import operator as operator_mod
    from runtime.mvp_runtime.crypto.live_order import MAX_CONSECUTIVE_API_ERRORS, api_breaker_status

    class _Channel:
        network_egress = egress

    handed: list[str] = []
    monkeypatch.setattr(operator_mod, "select_operator_channel", lambda now=None, root=None: _Channel())
    monkeypatch.setattr(operator_mod, "notify_operator",
                        lambda channel, text, repo_root=None: handed.append(text))
    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    monkeypatch.setattr(live_route, "select_live_gate", lambda **kw: (_Venue(), None))
    monkeypatch.setattr(live_route, "_run_gated_live_leg", lambda record, **kw: record)
    _api_failures(tmp_path, MAX_CONSECUTIVE_API_ERRORS)
    record = live_route.run_live_leg(
        route=None, live_routable_strategy_ids=None, feature_row={}, verdict={}, symbol=SYMBOL,
        collector=None, now=NOW, root=tmp_path)
    assert len(handed) == 1
    assert (api_breaker_status(tmp_path)["told_at"] is not None) is told
    assert (live_route.API_BREAKER_TOLD in record["live_reason_codes"]) is told


def test_a_breaker_that_cannot_count_by_the_gate_holds_the_entry(tmp_path, monkeypatch):
    """Writable when the decision was made, not by the gate: the gate judges it again."""
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    reads = iter([False, True])
    monkeypatch.setattr(live_route, "_api_breaker_unwritable", lambda adapter: next(reads))
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_decision"]["ready"] is True, "the decision saw a breaker that could count"
    assert held["live_pre_order_reread"]["api_breaker_tripped"] is True
    assert "api_breaker_clear" in held["live_pre_order_gate"]["failed_checks"]
    assert _nothing_spent(venue, tmp_path)



# --- the optional data door on the leg (PR2d-2) ------------------------------------------------------

def test_the_leg_judges_the_optional_data_it_was_handed_and_says_so(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto.live_entry import OPTIONAL_DATA_STALE

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    stale = {**healthy_optional_data(BAR_00), "stale": ["funding"],
             "feeds": {"funding": {"state": "stale", "age_hours": 20.0}}}
    real = live_route.run_live_leg
    monkeypatch.setattr(live_route, "run_live_leg",
                        lambda **kw: real(**{**kw, "optional_data": stale}))
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_decision"]["reasons"] == [OPTIONAL_DATA_STALE]
    assert _nothing_spent(venue, tmp_path)


def test_a_caller_that_hands_no_optional_data_opens_nothing(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto.live_entry import OPTIONAL_DATA_UNKNOWN

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    real = live_route.run_live_leg
    monkeypatch.setattr(live_route, "run_live_leg",
                        lambda **kw: real(**{k: v for k, v in kw.items() if k != "optional_data"}))
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_decision"]["reasons"] == [OPTIONAL_DATA_UNKNOWN]
    assert _nothing_spent(venue, tmp_path)


# --- the book the entry crosses (PR2d-3) ---------------------------------------------------------------

def test_the_leg_hands_the_decision_the_book_it_read(tmp_path, monkeypatch):
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    seen: dict[str, Any] = {}
    real = live_route.plan_live_entry

    def _spy(**kw):
        seen.update(kw)
        return real(**kw)

    monkeypatch.setattr(live_route, "plan_live_entry", _spy)
    opened = run("2026-07-28T04:05:00Z", BAR_00)
    assert opened["live_route_status"] == live_route.ROUTE_OPENED, opened["live_reason_codes"]
    assert seen["order_book"]["received_at"] == "2026-07-28T04:05:00Z"
    assert seen["order_book"]["asks"][0][0] > seen["order_book"]["bids"][0][0]


@pytest.mark.parametrize("book,code", [
    (lambda clock: deep_order_book(received_at=timeutil.plus_seconds(clock, -61)), "LIVE_ENTRY_ORDERBOOK_STALE"),
    (lambda clock: deep_order_book(received_at=clock, quantity=1e-6), "LIVE_ENTRY_BOOK_TOO_THIN"),
    (lambda clock: deep_order_book(received_at=clock, half_spread_bps=5.0), "LIVE_ENTRY_SLIPPAGE_ABOVE_MODEL"),
], ids=["stale", "thin", "dear"])
def test_a_book_the_order_should_not_cross_holds_the_entry_and_spends_nothing(tmp_path, monkeypatch, book, code):
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    monkeypatch.setattr(_Collector, "order_book",
                        lambda self, symbol, *, limit, timeout_seconds: book(self._clock()))
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_route_status"] == live_route.ROUTE_HELD
    assert held["live_decision"]["reasons"] == [code]
    assert _nothing_spent(venue, tmp_path)


def test_a_book_the_walk_cannot_read_holds_the_entry_and_never_halts_the_fan_out(tmp_path, monkeypatch):
    """Review of #893: a malformed deeper level used to raise out of the decision, which the route
    turned into an incident that halts every later context."""
    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)

    def _malformed(self, symbol, *, limit, timeout_seconds):
        book = deep_order_book(received_at=self._clock(), quantity=1e-6)
        book["asks"][1] = ("not a price", 1.0)
        return book

    monkeypatch.setattr(_Collector, "order_book", _malformed)
    held = run("2026-07-28T04:05:00Z", BAR_00)
    assert held["live_route_status"] == live_route.ROUTE_HELD and held["halt"] is False
    assert held["live_decision"]["reasons"] == ["LIVE_ENTRY_ORDERBOOK_UNREADABLE"]
    assert _nothing_spent(venue, tmp_path)


def test_through_the_fire_memo_a_book_read_early_in_the_fire_is_read_again(tmp_path, monkeypatch):
    """The per-fire memo shares one book among the symbol's contexts; a context deep in a long
    fire gets a fresh one rather than a book past the entry's minute (review of #893)."""
    from runtime.mvp_runtime.crypto.market_data import PerRunFeedCache

    venue = _Venue()
    run = _wire_whole_leg(tmp_path, monkeypatch, venue)
    stamps = iter(["2026-07-28T04:03:55Z", "2026-07-28T04:05:00Z"])   # 65 s before the pass, then now
    reads: list[str] = []

    class _Early:
        def order_book(self, symbol, *, limit, timeout_seconds):
            reads.append(symbol)
            return deep_order_book(received_at=next(stamps))

    memo = PerRunFeedCache(_Early())
    memo.order_book(SYMBOL, limit=20, timeout_seconds=10)       # the first context of the fire
    monkeypatch.setattr(timeutil, "utc_now_iso", lambda: "2026-07-28T04:05:00Z")
    monkeypatch.setattr(_Collector, "order_book",
                        lambda self, symbol, *, limit, timeout_seconds: memo.order_book(
                            symbol, limit=limit, timeout_seconds=timeout_seconds))
    opened = run("2026-07-28T04:05:00Z", BAR_00)
    assert opened["live_route_status"] == live_route.ROUTE_OPENED, opened["live_reason_codes"]
    assert reads == [SYMBOL, SYMBOL]
