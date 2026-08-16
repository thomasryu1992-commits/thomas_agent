"""LP5.3 tests — the executing leg. Fake adapter, zero network, zero venue.

The adapter is injected precisely so these branches are reachable in a test, and each of them
is a rule the design record names:

1. **Open only on RECONCILED** — an unconfirmed entry creates no local position.
2. **A naked position is closed, not warned about** — a bracket that will not place costs the
   position, immediately, in the *out* direction.
3. **Cancel the surviving leg on close** — the venue auto-cancels nothing.

Plus the two that decide whether the money is recorded truthfully: realized P&L comes from the
venue's actual fills (never the intended numbers), and the book is cleared only after the
outcome is durably recorded.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import live_leg as ll
from runtime.mvp_runtime.crypto.live_execution import DryRunOrderAdapter
from runtime.mvp_runtime.crypto.live_order import LIVE_CONFIRMATION_PHRASE, LiveOrderLimits
from runtime.mvp_runtime.errors import PersistenceError, SafetyGateBlocked, ToolError


def _no_sleep(_seconds):
    """The confirm backoff is real seconds; a unit test must not wait them out."""


NOW = "2026-07-25T12:00:00Z"

LIMITS = LiveOrderLimits(
    max_order_notional_usdt=60.0, absolute_max_notional_usdt=200.0, max_daily_order_count=2,
    max_open_notional_usdt=120.0, daily_loss_limit_usdt=20.0, min_clean_canary_orders=3,
    confirmation=LIVE_CONFIRMATION_PHRASE,
)

INTENT = {
    "status": "ORDER_INTENT_CREATED", "symbol": "BTCUSDT", "direction": "LONG", "side": "BUY",
    "order_type_exchange": "MARKET", "quantity": 0.001, "order_notional_usdt": 60.0,
    "reduce_only": False, "connectivity_test": False, "client_order_id": "TAI_BTCUSDT_LONG_abc",
    "strategy_id": "S001", "candidate_id": "cand_1", "strategy_rule_hash": "deadbeef",
}

BRACKET = {
    "stop_loss": 59000.0, "take_profit": 62000.0, "risk_per_unit": 1000.0,
    "stop_side": "SELL", "take_profit_side": "SELL", "working_type": "MARK_PRICE",
    "tick_size": 0.1,
}

DECISION = {
    "status": "READY", "ready": True, "symbol": "BTCUSDT",
    "guard": {"approved": True, "status": "READY"},
    "intent": INTENT, "bracket": BRACKET,
    "sizing": {"sizable": True, "quantity": 0.001, "notional_usdt": 60.0},
}

POSITION = {
    "symbol": "BTCUSDT", "direction": "LONG", "quantity": 0.001, "entry_price": 60000.0,
    "entry_quote_usdt": 60.0, "risk": 1.0, "position_id": "pos1", "opened_at_utc": NOW,
    "stop_client_order_id": "TAI_BTCUSDT_SL_x", "take_profit_client_order_id": "TAI_BTCUSDT_TP_y",
    "strategy_id": "S001", "candidate_id": "cand_1", "strategy_rule_hash": "deadbeef",
    "entry_exchange_order_id": 111,
}


class FakeAdapter:
    """Records every request and answers from a scripted table. No sockets, no state machine
    beyond what a test needs to steer one branch."""

    def __init__(self, *, fills=None, submit_errors=None, statuses=None, cancel_errors=None,
                 missing=()):
        self.submitted: list[dict] = []
        self.cancelled: list[str] = []
        self._requests: dict[str, dict] = {}
        self._fills = fills or {}
        self._submit_errors = submit_errors or {}
        self._statuses = statuses or {}
        self._cancel_errors = cancel_errors or {}
        self._missing = set(missing)

    def _kind(self, client_order_id: str) -> str:
        if "_SL_" in client_order_id:
            return "SL"
        if "_TP_" in client_order_id:
            return "TP"
        if "_CLOSE_" in client_order_id:
            return "CLOSE"
        return "ENTRY"

    def submit(self, order_request, *, timeout_seconds=10):
        self.submitted.append(dict(order_request))
        self._requests[str((order_request.get("clientAlgoId") or order_request["newClientOrderId"]))] = dict(order_request)
        kind = self._kind(str((order_request.get("clientAlgoId") or order_request["newClientOrderId"])))
        error = self._submit_errors.get(kind)
        if error is not None:
            raise ToolError(error, f"scripted {kind} rejection")
        return {"accepted": True}

    def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
        kind = self._kind(str(client_order_id))
        if kind in self._missing:
            return None
        if isinstance(self._statuses.get(kind), ToolError):
            raise self._statuses[kind]
        default_status = "NEW" if kind in {"SL", "TP"} else "FILLED"
        status = self._statuses.get(kind, default_status)
        fill = self._fills.get(kind, {})
        # Faithful to the venue: an order that has not FILLED has executed nothing, and one that
        # filled fully executed exactly what was submitted. A fake that reported filled quantity
        # on a NEW order would invent exposure that cannot exist; one that reported a quantity
        # other than the request's would fabricate a partial fill nobody asked for.
        requested = self._requests.get(str(client_order_id), {}).get("quantity", 0.001)
        default_qty = requested if status == "FILLED" else 0.0
        return {
            "symbol": symbol, "side": "BUY" if kind == "ENTRY" else "SELL",
            "status": status,
            "executedQty": fill.get("executedQty", default_qty),
            # The target leg is a sized reduceOnly LIMIT, so it echoes the flag too — only the
            # closePosition stop carries neither quantity nor reduceOnly.
            "reduceOnly": kind in {"CLOSE", "TP"},
            "avgPrice": fill.get("avgPrice", 60000.0 if kind == "ENTRY" else 61000.0),
            "cumQuote": fill.get("cumQuote", 60.0 if kind == "ENTRY" else 61.0),
            "orderId": f"oid-{kind}",
        }

    def cancel_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
        kind = self._kind(str(client_order_id))
        if kind in self._cancel_errors:
            raise ToolError(self._cancel_errors[kind], "scripted cancel failure")
        self.cancelled.append(str(client_order_id))
        return None if kind in self._missing else {"status": "CANCELED"}


class FakeStore:
    """``raises`` defaults to ToolError but the REAL store never raises it — it fails through
    `filelock.locked()` (PersistenceError), the gate re-check (SafetyGateBlocked) or the write
    itself (OSError). The persist-failure tests script those, because the leg's catch has to be
    proven against what the store actually throws, not the exception a fake finds convenient."""

    def __init__(self, error=None, raises=ToolError, clear_error=None):
        self.saved: list[dict] = []
        self.cleared: list[str] = []
        self._error = error
        self._raises = raises
        self._clear_error = clear_error

    def save_position(self, position):
        if self._error:
            raise self._raises(self._error, "scripted store failure")
        self.saved.append(dict(position))

    def clear_position(self, symbol):
        if self._clear_error:
            raise self._raises(self._clear_error, "scripted store failure")
        self.cleared.append(symbol)


class FakeLedger:
    def __init__(self, error=None, raises=ToolError):
        self.appended: list[dict] = []
        self._error = error
        self._raises = raises

    def append_outcome(self, record):
        if self._error:
            raise self._raises(self._error, "scripted ledger failure")
        self.appended.append(dict(record))


class FakeCounter:
    def __init__(self, error=None, raises=ToolError):
        self.count = 0
        self._error = error
        self._raises = raises

    def record_submission(self, *, day=None):
        if self._error:
            raise self._raises(self._error, "scripted counter failure")
        self.count += 1
        return self.count


GOVERNANCE = {
    "purpose": "autonomous",
    "order_fingerprint": "sha256:abc",
    "bound_task": {"identity": {"task_id": "T1"}, "context": {"core_context_binding_id": "CCB1"}},
    "permission_decision": {"permission_decision_id": "permdec_1"},
}


def _entry(**kw):
    return ll.execute_live_entry(
        kw.pop("decision", DECISION),
        sleep=kw.pop("sleep", _no_sleep),
        adapter=kw.pop("adapter", FakeAdapter()),
        position_store=kw.pop("position_store", FakeStore()),
        counter=kw.pop("counter", None),
        governance=kw.pop("governance", GOVERNANCE),
        gate_open=kw.pop("gate_open", True),
        limits=kw.pop("limits", LIMITS),
        now=kw.pop("now", NOW),
        **kw,
    )


def _exit(**kw):
    return ll.execute_live_exit(
        kw.pop("position", POSITION),
        adapter=kw.pop("adapter", FakeAdapter()),
        position_store=kw.pop("position_store", FakeStore()),
        ledger=kw.pop("ledger", FakeLedger()),
        gate_open=kw.pop("gate_open", True),
        limits=kw.pop("limits", LIMITS),
        close_reason=kw.pop("close_reason", "time_exit"),
        now=kw.pop("now", NOW),
        **kw,
    )


def _settle(**kw):
    """A venue-closed settle whose stop leg reports FILLED, so it prices and settles cleanly
    unless the test scripts a failure into a store."""
    return ll.settle_venue_closed_position(
        kw.pop("position", POSITION),
        adapter=kw.pop("adapter", FakeAdapter(statuses={"SL": "FILLED"})),
        position_store=kw.pop("position_store", FakeStore()),
        ledger=kw.pop("ledger", FakeLedger()),
        now=kw.pop("now", NOW),
        **kw,
    )


# --- the happy path -------------------------------------------------------------

def test_a_ready_decision_opens_a_bracketed_position():
    store, adapter = FakeStore(), FakeAdapter()
    result = _entry(adapter=adapter, position_store=store)
    assert result["status"] == ll.ENTRY_OPENED
    assert result["reason_codes"] == []
    assert len(store.saved) == 1
    # Entry + both bracket legs, in that order.
    assert [r["type"] for r in adapter.submitted] == ["MARKET", "STOP_MARKET", "LIMIT"]


def test_the_position_is_booked_from_the_ACTUAL_fill_not_the_intent():
    """Slippage is normal; a book that records the intent would be wrong from the first trade."""
    adapter = FakeAdapter(fills={"ENTRY": {"avgPrice": 60123.45, "executedQty": 0.001}})
    store = FakeStore()
    _entry(adapter=adapter, position_store=store)
    assert store.saved[0]["entry_price"] == 60123.45
    assert store.saved[0]["notional_usdt"] == pytest.approx(60.12345)


def test_a_partial_fill_is_unconfirmed_AND_the_real_exposure_is_closed():
    """The dangerous middle. LP4 reconciles a partial fill as MISMATCH, so the entry is refused
    — but the venue still filled real quantity, and that position has no bracket. Rule 2 says an
    unprotected position is closed, not warned about, so it is closed even though the entry as a
    whole failed."""
    store = FakeStore()
    adapter = FakeAdapter(fills={"ENTRY": {"executedQty": 0.0009, "avgPrice": 60000.0}})
    result = _entry(adapter=adapter, position_store=store)
    assert result["status"] == ll.ENTRY_NAKED_CLOSED
    assert ll.ENTRY_UNCONFIRMED in result["reason_codes"]
    assert store.saved == []
    close = [r for r in adapter.submitted if r.get("reduceOnly")]
    # Closed for what the venue actually filled, not for what was asked for.
    assert len(close) == 1 and close[0]["quantity"] == 0.0009


def test_an_unreconcilable_entry_sends_no_blind_close():
    """No venue answer means no reported exposure. Acting on a guess would be sending a real
    order against an unknown account state; the next cycle's reconciliation handles it."""
    adapter = FakeAdapter(statuses={"ENTRY": ToolError("ORDER_TRANSPORT", "x")})
    result = _entry(adapter=adapter)
    assert result["status"] == ll.ENTRY_NOT_CONFIRMED
    assert [r for r in adapter.submitted if r.get("reduceOnly")] == []


def test_the_bracket_ids_ride_on_the_stored_position():
    """The exit has to cancel exactly these orders and nothing else."""
    store = FakeStore()
    _entry(position_store=store)
    saved = store.saved[0]
    assert saved["stop_client_order_id"] and saved["take_profit_client_order_id"]
    assert saved["stop_client_order_id"] != saved["take_profit_client_order_id"]


def test_the_daily_counter_is_incremented():
    counter = FakeCounter()
    _entry(counter=counter)
    assert counter.count == 1


def test_an_ambiguous_submit_still_consumes_daily_budget():
    """An order that may have reached the venue must consume budget, or a flapping connection
    could spend the daily cap many times over."""
    counter = FakeCounter()
    adapter = FakeAdapter(submit_errors={"ENTRY": "ORDER_TRANSPORT"}, missing={"ENTRY"})
    result = _entry(adapter=adapter, counter=counter)
    assert result["status"] == ll.ENTRY_NOT_CONFIRMED
    assert counter.count == 1


# --- rule 1: open only on RECONCILED --------------------------------------------

@pytest.mark.parametrize("adapter_kw,expected_reason", [
    ({"missing": {"ENTRY"}}, ll.ENTRY_UNCONFIRMED),                       # NOT_FOUND
    ({"statuses": {"ENTRY": "NEW"}}, ll.ENTRY_UNCONFIRMED),               # MISMATCH
    ({"statuses": {"ENTRY": ToolError("ORDER_REJECTED", "x")}}, ll.ENTRY_UNCONFIRMED),  # UNRECONCILABLE
])
def test_an_unconfirmed_entry_creates_no_position(adapter_kw, expected_reason):
    store, adapter = FakeStore(), FakeAdapter(**adapter_kw)
    result = _entry(adapter=adapter, position_store=store)
    assert result["status"] == ll.ENTRY_NOT_CONFIRMED
    assert expected_reason in result["reason_codes"]
    assert store.saved == []
    assert result["position"] is None
    # And no bracket was attempted on a position that may not exist.
    assert result["bracket"] == []


def test_a_confirmed_entry_with_no_fill_PRICE_is_closed_not_booked():
    """A position that cannot state its own entry price must not be booked — but the venue did
    report filled quantity, so the exposure is real and gets closed rather than left naked."""
    store = FakeStore()
    adapter = FakeAdapter(fills={"ENTRY": {"avgPrice": None, "executedQty": 0.001}})
    result = _entry(adapter=adapter, position_store=store)
    assert result["status"] == ll.ENTRY_NAKED_CLOSED
    assert ll.FILL_FACTS_MISSING in result["reason_codes"]
    assert store.saved == []


def test_a_decision_that_is_not_ready_sends_nothing():
    adapter = FakeAdapter()
    result = _entry(decision={**DECISION, "ready": False}, adapter=adapter)
    assert result["status"] == ll.ENTRY_REFUSED
    assert result["reason_codes"] == [ll.NOT_READY]
    assert adapter.submitted == []


@pytest.mark.parametrize("governance", [None, {}, {"permission_decision": None}, "nope"])
def test_no_governance_record_means_no_order(governance):
    """`p5_policy_gate` requires a post-action report, which is impossible without the P5
    decision the order was placed under. An order that could not be audited afterwards is not
    sent at all."""
    adapter = FakeAdapter()
    result = _entry(adapter=adapter, governance=governance)
    assert result["status"] == ll.ENTRY_REFUSED
    assert result["reason_codes"] == [ll.NO_GOVERNANCE]
    assert adapter.submitted == []


def test_the_permission_decision_id_rides_into_the_result():
    """So the audit event and the order can be tied together afterwards."""
    assert _entry()["permission_decision_id"] == "permdec_1"


def test_a_ready_flag_cannot_override_a_refusing_guard():
    """Belt-and-suspenders: this is the function that turns a plan into money."""
    adapter = FakeAdapter()
    result = _entry(decision={**DECISION, "guard": {"approved": False}}, adapter=adapter)
    assert result["status"] == ll.ENTRY_REFUSED
    assert adapter.submitted == []


# --- rule 2: a naked position is closed, not warned about ------------------------

@pytest.mark.parametrize("failure", [
    {"submit_errors": {"SL": "ORDER_REJECTED"}, "missing": {"SL"}},
    {"missing": {"TP"}},
    {"statuses": {"SL": "REJECTED"}},
    {"statuses": {"TP": ToolError("ORDER_TRANSPORT", "x")}},
])
def test_a_bracket_failure_closes_the_position_immediately(failure):
    store, adapter = FakeStore(), FakeAdapter(**failure)
    result = _entry(adapter=adapter, position_store=store)
    assert result["status"] == ll.ENTRY_NAKED_CLOSED
    assert ll.BRACKET_FAILED in result["reason_codes"]
    assert ll.NAKED_POSITION_CLOSED in result["reason_codes"]
    # Nothing was booked: the position does not exist any more.
    assert store.saved == []
    # And the close was a reduceOnly MARKET. Matched on the type too: now that the target leg is
    # a sized reduceOnly LIMIT, the reduceOnly flag alone no longer identifies the close.
    close = [r for r in adapter.submitted if r.get("reduceOnly") and r["type"] == "MARKET"]
    assert len(close) == 1


def test_the_naked_close_withdraws_whichever_leg_did_place():
    """Half a bracket resting against a position that no longer exists is exactly the litter
    cancel-on-close exists to avoid."""
    adapter = FakeAdapter(missing={"TP"})
    result = _entry(adapter=adapter)
    cancels = result["naked_close"]["cancels"]
    assert [c["leg"] for c in cancels] == ["stop_client_order_id"]
    assert adapter.cancelled


def test_a_failed_naked_close_is_reported_as_loudly_as_possible():
    """The one branch with no good outcome: a real, unprotected position that would not close."""
    adapter = FakeAdapter(missing={"TP", "CLOSE"})
    result = _entry(adapter=adapter)
    assert result["status"] == ll.ENTRY_NAKED_OPEN
    assert ll.NAKED_CLOSE_FAILED in result["reason_codes"]


def test_a_refusing_close_guard_leaves_the_position_naked_and_says_so():
    adapter = FakeAdapter(missing={"TP"})
    result = _entry(adapter=adapter, gate_open=False)
    assert result["status"] == ll.ENTRY_NAKED_OPEN
    assert ll.NAKED_CLOSE_FAILED in result["reason_codes"]
    assert result["naked_close"]["submitted"] is False


# --- a naked close is a closed trade, and is recorded as one ----------------------

def test_a_naked_close_records_an_outcome():
    """It did not, until 2026-08-03. A position that filled, missed its bracket and was closed
    again moved real money and produced no outcome row — so the weekly, drawdown and
    consecutive-loss breakers judged zero rows and read NORMAL while the money moved. This is
    the path most likely to be losing when it fires, so it is the worst one to be invisible."""
    result = _entry(adapter=FakeAdapter(missing={"TP"}))
    assert result["status"] == ll.ENTRY_NAKED_CLOSED
    outcome = result["outcome"]
    assert outcome is not None, "a closed position must produce a closed-trade row"
    assert outcome["close_reason"] == ll.CLOSE_REASON_NAKED
    assert outcome["realized_pnl_usdt"] is not None
    # Attributed, or the ladder cannot judge the strategy it belonged to.
    assert outcome["symbol"] == result["symbol"]
    assert outcome["settlement_id"]


def test_a_naked_outcome_carries_a_real_R_and_not_None():
    """Writing the row was only half the reconnection. `risk_usdt` was read from
    `sizing["risk_usdt"]`, a key nothing in the runtime writes, so every naked row came out
    R-less — and an R-less live row is DROPPED by `cycle.live_outcomes_for_analysis` before the
    weekly, drawdown and consecutive-loss breakers see it. The row reached the ledger and was
    discarded one layer above it."""
    outcome = _entry(adapter=FakeAdapter(missing={"TP"}))["outcome"]
    # |60000 - 59000| * 0.001, off the FILLED price and the decision's own stop.
    assert outcome["risk_usdt"] == 1.0
    assert outcome["result_R"] == 1.0


def test_a_naked_outcome_reaches_the_breakers_it_was_recorded_for():
    """The behaviour the field exists for, pinned end to end rather than by proxy: the bridge
    that feeds the ledger-based breakers must keep this row, not drop it."""
    from runtime.mvp_runtime.crypto.cycle import live_outcomes_for_analysis

    outcome = _entry(adapter=FakeAdapter(missing={"TP"}))["outcome"]
    readable, excluded = live_outcomes_for_analysis([outcome])
    assert len(readable) == 1
    assert excluded == []


def test_a_naked_close_states_the_same_risk_the_booked_path_would_have():
    """Two ways to compute one trade's risk is how the R in this row stops being comparable to
    the R in the rows beside it."""
    from runtime.mvp_runtime.crypto.live_position import build_live_position

    booked = build_live_position(
        symbol="BTCUSDT", direction="LONG", quantity=0.001, entry_price=60000.0,
        stop_loss=BRACKET["stop_loss"], opened_at=NOW,
    )
    outcome = _entry(adapter=FakeAdapter(missing={"TP"}))["outcome"]
    assert outcome["risk_usdt"] == booked["risk"]


def _second_entry_decision():
    """A second, distinct entry on the same symbol — a different order, so a different id."""
    return {**DECISION, "intent": {**INTENT, "client_order_id": "TAI_BTCUSDT_LONG_def"}}


def test_a_naked_outcome_names_the_position_that_briefly_existed():
    outcome = _entry(adapter=FakeAdapter(missing={"TP"}))["outcome"]
    assert outcome["position_id"]


def test_two_naked_closes_on_one_symbol_do_not_mint_the_same_id():
    """`outcome_id` is derived from `{position_id, closed_at, symbol}`, and a naked close
    carried `position_id=None`. Two of them on one symbol sharing a cycle timestamp therefore
    derived the SAME id — nothing about the trades had to be alike."""
    a = _entry(adapter=FakeAdapter(missing={"TP"}))["outcome"]
    b = _entry(decision=_second_entry_decision(), adapter=FakeAdapter(missing={"TP"}))["outcome"]
    # The two fields the old id had left: identical, as they would be in one cycle.
    assert a["symbol"] == b["symbol"]
    assert a["closed_at_utc"] == b["closed_at_utc"]
    assert a["outcome_id"] != b["outcome_id"]
    assert a["settlement_id"] != b["settlement_id"]


def test_two_naked_closes_leave_a_live_history_that_still_reads(tmp_path):
    """The failure the id prevents, at the level it bites. `read_live_outcomes` raises
    LIVE_HISTORY_DUPLICATE rather than return a history it cannot prove, so one duplicate does
    not lose one row — it makes the whole live history unreadable, and every risk decision that
    reads it fails closed. On a duplicate this runtime minted itself."""
    import json

    from runtime.mvp_runtime.crypto.live_pnl import (
        LIVE_OUTCOMES_FILENAME,
        read_live_outcomes,
        state_dir,
    )

    rows = [
        _entry(adapter=FakeAdapter(missing={"TP"}))["outcome"],
        _entry(decision=_second_entry_decision(), adapter=FakeAdapter(missing={"TP"}))["outcome"],
    ]
    path = state_dir(tmp_path) / LIVE_OUTCOMES_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    assert len(read_live_outcomes(tmp_path)) == 2


def test_the_unbooked_position_id_is_deterministic_and_per_order():
    """Deterministic, so a replay derives the same id; per-order, so two entries never share
    one. Both halves matter: the first is what makes the record replayable, the second is what
    keeps `read_live_outcomes` from refusing the file."""
    from runtime.mvp_runtime.crypto.live_position import unbooked_position_id

    kw = {"symbol": "BTCUSDT", "entry_client_order_id": "TAI_BTCUSDT_LONG_abc", "opened_at": NOW}
    assert unbooked_position_id(**kw) == unbooked_position_id(**kw)
    assert unbooked_position_id(**{**kw, "entry_client_order_id": "TAI_BTCUSDT_LONG_def"}) != \
        unbooked_position_id(**kw)


def test_a_naked_close_that_cannot_be_priced_records_nothing_and_says_so():
    """The position is closed at the venue either way, so unlike `execute_live_exit` there is
    no book to keep. Inventing a figure to fill the row would put a fiction into the breaker's
    accounting, which is worse than the gap this recording exists to close."""
    adapter = FakeAdapter(missing={"TP"}, fills={"CLOSE": {"cumQuote": None, "avgPrice": None}})
    result = _entry(adapter=adapter)
    assert result["status"] == ll.ENTRY_NAKED_CLOSED  # it still closed
    assert result["outcome"] is None
    assert ll.FILL_FACTS_MISSING in result["reason_codes"]


def test_a_naked_position_that_stayed_open_records_no_outcome():
    """The inverse pin: nothing was realized, so there is nothing to record. A row here would
    report a closed trade against a position the venue still holds."""
    result = _entry(adapter=FakeAdapter(missing={"TP", "CLOSE"}))
    assert result["status"] == ll.ENTRY_NAKED_OPEN
    assert result["outcome"] is None


# --- the bracket legs themselves -------------------------------------------------

def test_the_stop_is_closePosition_never_sized():
    """closePosition protects whatever is actually open, so a partial fill cannot leave a
    sliver unprotected — and the venue forbids quantity/reduceOnly alongside it. This is the leg
    that defines the risk, so it keeps the Close-All shape."""
    adapter = FakeAdapter()
    _entry(adapter=adapter)
    sl = adapter.submitted[1]
    assert sl["closePosition"] == "true"
    assert "quantity" not in sl and "reduceOnly" not in sl


def test_the_target_is_a_sized_reduce_only_limit_at_the_planned_price():
    """The target rests as a maker instead of triggering into a market order. closePosition is
    unavailable on a LIMIT at this venue, so this leg carries a real quantity — and it must be
    the ACTUAL filled size, or it would rest asking to reduce more than the position holds."""
    adapter = FakeAdapter(fills={"ENTRY": {"executedQty": 0.001, "avgPrice": 60000.0}})
    _entry(adapter=adapter)
    tp = adapter.submitted[2]
    assert tp["type"] == "LIMIT"
    assert tp["price"] == BRACKET["take_profit"]
    assert tp["timeInForce"] == "GTC"
    assert tp["reduceOnly"] is True
    assert tp["quantity"] == 0.001
    assert "closePosition" not in tp
    # No trigger and no workingType: it is not a conditional order, it is resting in the book.
    assert "stopPrice" not in tp and "workingType" not in tp


def test_the_stop_triggers_on_the_mark_price_at_the_planned_price():
    adapter = FakeAdapter()
    _entry(adapter=adapter)
    sl = adapter.submitted[1]
    assert sl["triggerPrice"] == BRACKET["stop_loss"] and sl["workingType"] == "MARK_PRICE"


def test_the_target_leg_is_sized_from_the_actual_fill_not_the_intent():
    """The partial-fill case the sized leg has to get right. The intent asked for 0.001; if the
    venue filled less, a target sized from the intent would try to reduce quantity that is not
    there."""
    adapter = FakeAdapter()
    _entry(adapter=adapter)
    entry_qty = adapter.submitted[0]["quantity"]
    assert adapter.submitted[2]["quantity"] == entry_qty


def test_a_resting_leg_is_placed_a_filled_one_is_not():
    """A protective order that already executed is not protection — it is a closed position."""
    intent = ll.build_bracket_intent(symbol="BTCUSDT", leg="SL", side="SELL", price=59000.0,
                                     working_type="MARK_PRICE", position_seed="seed")
    assert ll.place_bracket_leg(intent, adapter=FakeAdapter(), sleep=_no_sleep)["placed"] is True
    assert ll.place_bracket_leg(intent, adapter=FakeAdapter(statuses={"SL": "FILLED"}),
                                 sleep=_no_sleep)["placed"] is False


def test_the_two_legs_get_distinct_idempotency_keys():
    sl = ll.build_bracket_intent(symbol="BTCUSDT", leg="SL", side="SELL", price=59000.0,
                                 working_type="MARK_PRICE", position_seed="seed")
    tp = ll.build_bracket_intent(symbol="BTCUSDT", leg="TP", side="SELL", price=62000.0,
                                 working_type="MARK_PRICE", position_seed="seed", quantity=0.001)
    assert sl["client_order_id"] != tp["client_order_id"]
    assert sl["idempotency_key"] != tp["idempotency_key"]


def test_a_leg_confirmed_resting_clears_a_duplicate_id_rejection():
    """The submit may be rejected as a duplicate while the original is already resting; the
    venue read is the truth."""
    intent = ll.build_bracket_intent(symbol="BTCUSDT", leg="SL", side="SELL", price=59000.0,
                                     working_type="MARK_PRICE", position_seed="seed")
    result = ll.place_bracket_leg(intent, adapter=FakeAdapter(submit_errors={"SL": "ORDER_REJECTED"}),
                                  sleep=_no_sleep)
    assert result["placed"] is True and result["error"] is None


# --- the exit --------------------------------------------------------------------

def test_a_confirmed_close_records_the_outcome_and_clears_the_book():
    store, ledger, adapter = FakeStore(), FakeLedger(), FakeAdapter()
    result = _exit(position_store=store, ledger=ledger, adapter=adapter)
    assert result["status"] == ll.EXIT_CLOSED
    assert len(ledger.appended) == 1
    assert store.cleared == ["BTCUSDT"]


def test_the_close_is_reduce_only():
    """The structural boundary that lets the close path be exempt from the halts: it can only
    ever shrink a position, never open one."""
    adapter = FakeAdapter()
    _exit(adapter=adapter)
    assert adapter.submitted[0]["reduceOnly"] is True


def test_rule_3_the_surviving_bracket_leg_is_cancelled():
    adapter = FakeAdapter()
    _exit(adapter=adapter)
    assert set(adapter.cancelled) == {
        POSITION["stop_client_order_id"], POSITION["take_profit_client_order_id"]
    }


def test_the_cancel_happens_only_after_the_close_is_confirmed():
    """Cancelling a protective leg while the position is still open would INCREASE risk."""
    adapter = FakeAdapter(missing={"CLOSE"})
    result = _exit(adapter=adapter)
    assert result["status"] == ll.EXIT_NOT_CONFIRMED
    assert adapter.cancelled == []


def test_an_already_gone_leg_is_not_a_failure():
    """The leg that triggered the close answers 'unknown order' — the expected result."""
    adapter = FakeAdapter(missing={"SL"})
    result = _exit(adapter=adapter)
    assert result["status"] == ll.EXIT_CLOSED
    assert ll.BRACKET_CANCEL_FAILED not in result["reason_codes"]


def test_a_failed_cancel_is_reported_but_not_fatal():
    """A stale closePosition leg can only reduce, never open — a nuisance, not a new risk."""
    adapter = FakeAdapter(cancel_errors={"TP": "ORDER_REJECTED"})
    result = _exit(adapter=adapter)
    assert result["status"] == ll.EXIT_CLOSED
    assert ll.BRACKET_CANCEL_FAILED in result["reason_codes"]


def test_an_unconfirmed_exit_leaves_the_position_open_locally():
    """The safe direction: the next cycle reconciles and refuses new entries on this symbol
    until the disagreement is resolved. Clearing the book would forget a real position."""
    store, ledger = FakeStore(), FakeLedger()
    result = _exit(adapter=FakeAdapter(missing={"CLOSE"}), position_store=store, ledger=ledger)
    assert result["status"] == ll.EXIT_NOT_CONFIRMED
    assert store.cleared == [] and ledger.appended == []


def test_a_refusing_close_guard_sends_nothing():
    adapter = FakeAdapter()
    result = _exit(adapter=adapter, gate_open=False)
    assert result["status"] == ll.EXIT_REFUSED
    assert adapter.submitted == []


def test_the_outcome_is_recorded_before_the_book_is_cleared():
    """An outcome that never lands is a loss the breaker will never see."""
    store, ledger = FakeStore(), FakeLedger(error="LIVE_STATE_LOCKED")
    result = _exit(position_store=store, ledger=ledger)
    assert result["status"] == ll.EXIT_NOT_CONFIRMED
    assert ll.OUTCOME_PERSIST_FAILED in result["reason_codes"]
    assert store.cleared == []   # the book still says OPEN, which reconciliation will catch


# --- realized P&L from actual fills ----------------------------------------------

def test_a_long_profit_is_quote_out_minus_quote_in():
    pnl, detail = ll.realized_pnl_usdt(POSITION, {"cum_quote": 61.0, "executed_qty": 0.001,
                                                  "avg_price": 61000.0})
    assert pnl == 1.0
    assert detail["fees_included"] is False


def test_a_short_profit_is_the_mirror():
    short = {**POSITION, "direction": "SHORT"}
    pnl, _ = ll.realized_pnl_usdt(short, {"cum_quote": 59.0, "executed_qty": 0.001,
                                          "avg_price": 59000.0})
    assert pnl == 1.0


def test_a_loss_is_negative_and_reaches_the_outcome_record():
    ledger = FakeLedger()
    adapter = FakeAdapter(fills={"CLOSE": {"cumQuote": 59.0, "avgPrice": 59000.0, "executedQty": 0.001}})
    _exit(adapter=adapter, ledger=ledger)
    outcome = ledger.appended[0]
    assert outcome["realized_pnl_usdt"] == -1.0
    # LP5.4: recorded risk -> an honest R, so the risk guard can read this loss.
    assert outcome["result_R"] == -1.0


def test_a_stop_close_measures_its_fill_against_the_position_trigger():
    """The executing leg stamps the resting trigger onto the outcome, so a stop's realized
    slippage (`stop_slippage_bps`, adverse-positive) is measured at source rather than
    reconstructed by hand — the §C gap. A time exit carries the trigger but never the figure."""
    ledger = FakeLedger()
    adapter = FakeAdapter(fills={"CLOSE": {"cumQuote": 58.99, "avgPrice": 58990.0, "executedQty": 0.001}})
    _exit(position={**POSITION, "stop_loss": 59000.0}, close_reason="stop_loss",
          adapter=adapter, ledger=ledger)
    outcome = ledger.appended[0]
    assert outcome["stop_price"] == 59000.0
    # LONG closed by a SELL 10 under its 59000 trigger: 10/59000 in bps.
    assert outcome["stop_slippage_bps"] == pytest.approx(10.0 / 59000.0 * 10000.0, abs=1e-3)

    ledger = FakeLedger()
    _exit(position={**POSITION, "stop_loss": 59000.0}, close_reason="time_exit",
          adapter=FakeAdapter(fills={"CLOSE": {"cumQuote": 58.99, "avgPrice": 58990.0,
                                               "executedQty": 0.001}}), ledger=ledger)
    assert ledger.appended[0]["stop_price"] == 59000.0
    assert ledger.appended[0]["stop_slippage_bps"] is None


def test_pnl_falls_back_to_price_times_quantity_when_quote_is_absent():
    pnl, _ = ll.realized_pnl_usdt(POSITION, {"cum_quote": None, "executed_qty": 0.001,
                                             "avg_price": 61000.0})
    assert pnl == pytest.approx(1.0)


@pytest.mark.parametrize("fill", [
    {"cum_quote": None, "executed_qty": None, "avg_price": None},
    {"cum_quote": None, "executed_qty": 0.001, "avg_price": None},
])
def test_uncomputable_pnl_refuses_rather_than_inventing_a_number(fill):
    pnl, detail = ll.realized_pnl_usdt(POSITION, fill)
    assert pnl is None
    assert detail["pnl_source"] == "venue_fills_gross"


def test_an_uncomputable_pnl_keeps_the_position_in_the_book():
    """A trade that vanished from the accounting is worse than a stale book entry."""
    store, ledger = FakeStore(), FakeLedger()
    adapter = FakeAdapter(fills={"CLOSE": {"cumQuote": None, "avgPrice": None, "executedQty": 0.001}})
    result = _exit(adapter=adapter, position_store=store, ledger=ledger)
    assert result["status"] == ll.EXIT_NOT_CONFIRMED
    assert ll.FILL_FACTS_MISSING in result["reason_codes"]
    assert store.cleared == [] and ledger.appended == []


def test_a_position_with_no_recorded_risk_yields_no_R_rather_than_a_breakeven():
    """LP5.4's rule reaching the executing leg: 0.0 would read as a breakeven and shorten a
    loss streak."""
    ledger = FakeLedger()
    _exit(position={**POSITION, "risk": 0.0}, ledger=ledger,
          adapter=FakeAdapter(fills={"CLOSE": {"cumQuote": 59.0, "avgPrice": 59000.0,
                                               "executedQty": 0.001}}))
    outcome = ledger.appended[0]
    assert outcome["result_R"] is None
    assert outcome["realized_pnl_usdt"] == -1.0   # the money is still fully recorded


def test_the_lineage_reaches_the_outcome():
    ledger = FakeLedger()
    _exit(ledger=ledger)
    outcome = ledger.appended[0]
    assert outcome["candidate_id"] == "cand_1"
    assert outcome["strategy_rule_hash"] == "deadbeef"


# --- the store failing is not the position disappearing ---------------------------

def test_a_book_write_failure_is_surfaced_not_swallowed():
    """The position is real and bracketed; only the local book failed. Reporting a clean open
    would hide a venue/book disagreement."""
    store = FakeStore(error="LIVE_STATE_LOCKED")
    result = _entry(position_store=store)
    assert result["status"] == ll.ENTRY_OPENED
    assert ll.POSITION_PERSIST_FAILED in result["reason_codes"]


# --- the REAL stores' failure modes do not escape the leg --------------------------
#
# The real position store and ledger never raise ToolError: they fail through
# `filelock.locked()` (PersistenceError), the gate re-check (SafetyGateBlocked), or the write
# itself (a raw OSError) — and the first two are SIBLINGS of ToolError under MvpRuntimeError.
# While the persist sites caught only ToolError, those failure modes escaped to
# `run_live_leg`'s MvpRuntimeError handler, which stamps ROUTE_BLOCKED — a status whose
# contract is "nothing was sent" — with no halt, on a leg where money had already moved; on
# the entry path the escape aborted BEFORE the bracket was placed or a naked fill closed,
# leaving a filled entry unprotected and unbooked while reported as a pre-venue refusal.

@pytest.mark.parametrize("raises,code,expected", [
    (PersistenceError, "LIVE_STATE_LOCKED", "LIVE_STATE_LOCKED"),
    (SafetyGateBlocked, "SAFETY_FLAG_DISABLED", "SAFETY_FLAG_DISABLED"),
    (OSError, "EACCES", "UNEXPECTED_OSError"),   # no reason_code to extract, so it is named
])
def test_a_real_book_write_failure_is_reported_never_raised(raises, code, expected):
    store = FakeStore(error=code, raises=raises)
    result = _entry(position_store=store)
    assert result["status"] == ll.ENTRY_OPENED
    assert ll.POSITION_PERSIST_FAILED in result["reason_codes"]
    assert expected in result["reason_codes"]


def test_a_real_book_write_failure_fires_the_incident_vocabulary_the_route_halts_on():
    """`live_route._INCIDENT_REASONS` halts the fan-out on POSITION_PERSIST_FAILED — but only
    if the leg survives to report it. An escaping PersistenceError produced no leg result at
    all, so the halt the reason code exists for was unreachable for the store's real failure."""
    from runtime.mvp_runtime.crypto import live_route

    result = _entry(position_store=FakeStore(error="LIVE_STATE_LOCKED", raises=PersistenceError))
    assert live_route._is_incident(result)


def test_a_counter_persist_failure_does_not_abort_before_the_bracket():
    """The counter is written between the entry submit and the bracket placement — the worst
    possible escape point in the whole leg. A PersistenceError there must cost a reason code,
    never the protective bracket or the booking."""
    counter = FakeCounter(error="LIVE_ORDER_COUNT_LOCKED", raises=PersistenceError)
    adapter, store = FakeAdapter(), FakeStore()
    result = _entry(adapter=adapter, position_store=store, counter=counter)
    assert result["status"] == ll.ENTRY_OPENED
    assert [r["type"] for r in adapter.submitted] == ["MARKET", "STOP_MARKET", "LIMIT"]
    assert len(store.saved) == 1
    assert "LIVE_ORDER_COUNT_LOCKED" in result["reason_codes"]


def test_a_counter_persist_failure_does_not_abort_before_the_naked_close():
    """Rule 2 must still run behind a failing counter store: a filled entry whose bracket was
    refused is closed, not stranded by an exception between the two."""
    counter = FakeCounter(error="LIVE_ORDER_COUNT_LOCKED", raises=PersistenceError)
    adapter = FakeAdapter(missing={"TP"})
    result = _entry(adapter=adapter, counter=counter)
    assert result["status"] == ll.ENTRY_NAKED_CLOSED
    close = [r for r in adapter.submitted if r.get("reduceOnly") and r["type"] == "MARKET"]
    assert len(close) == 1


@pytest.mark.parametrize("raises", [PersistenceError, SafetyGateBlocked, OSError])
def test_a_real_ledger_failure_on_exit_keeps_the_book_and_reports(raises):
    """The same rule the ToolError twin above pins, against the exceptions the real ledger
    actually raises: the book stays OPEN and OUTCOME_PERSIST_FAILED — an incident reason —
    reaches the record instead of the exception reaching `run_live_leg`."""
    from runtime.mvp_runtime.crypto import live_route

    store = FakeStore()
    ledger = FakeLedger(error="LIVE_STATE_LOCKED", raises=raises)
    result = _exit(position_store=store, ledger=ledger)
    assert result["status"] == ll.EXIT_NOT_CONFIRMED
    assert ll.OUTCOME_PERSIST_FAILED in result["reason_codes"]
    assert store.cleared == []
    assert live_route._is_incident(result)


def test_a_real_book_clear_failure_on_exit_still_reports_the_recorded_close():
    """The outcome landed; only clearing the book failed. That is stale local state the next
    reconciliation catches — reported on the record, never an exception that would rebrand a
    recorded close as ROUTE_BLOCKED."""
    store = FakeStore(clear_error="LIVE_STATE_LOCKED", raises=PersistenceError)
    ledger = FakeLedger()
    result = _exit(position_store=store, ledger=ledger)
    assert result["status"] == ll.EXIT_CLOSED
    assert len(ledger.appended) == 1
    assert "LIVE_STATE_LOCKED" in result["reason_codes"]


def test_a_real_ledger_failure_on_a_venue_close_keeps_the_book_and_reports():
    """`settle_venue_closed_position` owes the same posture: the venue already closed the
    position, so a PersistenceError from the ledger must leave EXIT_UNSETTLEABLE on the record
    (itself an incident status) rather than escape as a pre-venue refusal."""
    from runtime.mvp_runtime.crypto import live_route

    store = FakeStore()
    ledger = FakeLedger(error="LIVE_STATE_LOCKED", raises=PersistenceError)
    result = _settle(position_store=store, ledger=ledger)
    assert result["status"] == ll.EXIT_UNSETTLEABLE
    assert ll.OUTCOME_PERSIST_FAILED in result["reason_codes"]
    assert store.cleared == []
    assert live_route._is_incident(result)


def test_a_real_book_clear_failure_on_a_venue_close_still_settles():
    store = FakeStore(clear_error="LIVE_STATE_LOCKED", raises=PersistenceError)
    ledger = FakeLedger()
    result = _settle(position_store=store, ledger=ledger)
    assert result["status"] == ll.EXIT_CLOSED
    assert len(ledger.appended) == 1
    assert "LIVE_STATE_LOCKED" in result["reason_codes"]


# --- the module cannot reach the venue on its own ---------------------------------

def test_the_adapter_is_injected_never_selected():
    """The safety property that makes every branch above testable — and that stops this module
    building a capable adapter for itself."""
    import runtime.mvp_runtime.crypto.live_leg as module

    assert not hasattr(module, "select_order_adapter")
    assert not hasattr(module, "select_live_position_store")
    assert not hasattr(module, "select_live_ledger")


def test_the_dry_run_adapter_books_nothing_because_it_has_no_real_fill():
    """The inert adapter drives the whole flow with no grant, no key and no socket — and stops
    exactly where it should. Its synthetic order reports no fill PRICE, because inventing one
    would be the same class of mistake as a mock inventing a lot step: the book would carry a
    fabricated entry price into real accounting. So the dry run refuses to book, and the
    unconfirmed-exposure path handles the quantity it did report."""
    adapter = DryRunOrderAdapter()
    store = FakeStore()
    result = _entry(adapter=adapter, position_store=store)
    assert result["status"] in {ll.ENTRY_NAKED_CLOSED, ll.ENTRY_NOT_CONFIRMED}
    assert ll.FILL_FACTS_MISSING in result["reason_codes"]
    assert store.saved == []


def test_the_dry_run_adapter_rests_a_conditional_leg_rather_than_filling_it():
    """A protective order rests as NEW at the venue; a dry run that echoed FILLED would
    'confirm' a bracket in a state the venue never reports."""
    intent = ll.build_bracket_intent(symbol="BTCUSDT", leg="SL", side="SELL", price=59000.0,
                                     working_type="MARK_PRICE", position_seed="seed")
    result = ll.place_bracket_leg(intent, adapter=DryRunOrderAdapter(), sleep=_no_sleep)
    assert result["placed"] is True and result["status"] == "NEW"


def test_the_status_line_is_ascii():
    for result in (_entry(), _exit(), _entry(decision={**DECISION, "ready": False})):
        line = ll.leg_status_line(result)
        assert line.isascii() and line


# --- a rejection says what the venue said ---------------------------------------

def test_a_rejected_bracket_leg_records_what_the_venue_actually_said():
    """`error` is the reason CODE, and it is the same string for every rejection there is.

    Measured 2026-08-02 on the first real bracket failure: a protective stop was refused, the
    record said `error: ORDER_REJECTED` and nothing else, and the cause of the one leg whose
    absence forces a naked position closed had to be guessed from the surrounding fields. The
    venue's own code and message were already inside the exception `live_execution.submit`
    raises (`venue rejected the order (code -2021): ...`) — they were simply dropped when the
    leg record was written. A rejection that cannot say why has to happen twice before anyone
    can act on it."""
    adapter = FakeAdapter(submit_errors={"SL": "ORDER_REJECTED"}, missing={"SL"})
    result = _entry(adapter=adapter)
    sl = [leg for leg in result["bracket"] if leg["client_order_id"].split("_")[2] == "SL"]
    assert len(sl) == 1
    assert sl[0]["error"] == "ORDER_REJECTED"
    assert sl[0]["placed"] is False
    # The detail, which is the whole point — and it carries the scripted text, so a real venue
    # code would ride the same way.
    assert "scripted SL rejection" in (sl[0]["error_detail"] or "")


def test_a_leg_that_placed_cleanly_carries_no_detail():
    """Absent rather than an empty string: "the venue said nothing" and "the venue said ''" are
    different facts, and only one of them means the leg was fine."""
    result = _entry(adapter=FakeAdapter())
    for leg in result["bracket"]:
        assert leg["error"] is None and leg["error_detail"] is None


def test_the_submit_message_wins_over_a_later_fetch_failure():
    """Both can fail on one leg. The submit's message is the more specific of the two — a fetch
    failure after a rejection is a second symptom, not the cause — so it must not overwrite it."""
    adapter = FakeAdapter(
        submit_errors={"SL": "ORDER_REJECTED"},
        statuses={"SL": ToolError("ORDER_TRANSPORT", "fetch blew up")},
    )
    result = _entry(adapter=adapter)
    sl = [leg for leg in result["bracket"] if leg["client_order_id"].split("_")[2] == "SL"][0]
    assert sl["error"] == "ORDER_REJECTED"
    assert "scripted SL rejection" in (sl["error_detail"] or "")
    assert "fetch blew up" not in (sl["error_detail"] or "")
