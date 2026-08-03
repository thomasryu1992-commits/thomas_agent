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
from runtime.mvp_runtime.crypto.live_order import LIVE_CONFIRMATION_PHRASE, LiveOrderLimits
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
    max_open_notional_usdt=120.0, daily_loss_limit_usdt=20.0, min_clean_canary_orders=3,
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
    assert "console_cli kill" in sent[0]


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
