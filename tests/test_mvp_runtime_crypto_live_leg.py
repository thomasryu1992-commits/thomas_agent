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
from runtime.mvp_runtime.crypto import pre_order_gate
from runtime.mvp_runtime.crypto.live_execution import DryRunOrderAdapter
from runtime.mvp_runtime.crypto.live_order import (
    LIVE_CONFIRMATION_PHRASE,
    LiveOrderLimits,
    enrich_order_identity,
)
from runtime.mvp_runtime.errors import PersistenceError, SafetyGateBlocked, ToolError
from tests._helpers import FakeSnapshotStore, approved_snapshot


def _no_sleep(_seconds):
    """The confirm backoff is real seconds; a unit test must not wait them out."""


NOW = "2026-07-25T12:00:00Z"

LIMITS = LiveOrderLimits(
    max_order_notional_usdt=60.0, absolute_max_notional_usdt=200.0, max_daily_order_count=2,
    max_open_notional_usdt=120.0, daily_loss_limit_usdt=20.0,
    confirmation=LIVE_CONFIRMATION_PHRASE,
)

def _intent(**kw):
    """An entry intent with the identity its own fields produce, sealed by the pre-order gate
    (PR2b): what `live_route` hands the leg."""
    intent = enrich_order_identity({
        "status": "ORDER_INTENT_CREATED", "symbol": "BTCUSDT", "direction": "LONG", "side": "BUY",
        "order_type_exchange": "MARKET", "quantity": 0.001, "order_notional_usdt": 60.0,
        "stop_loss": 59000.0, "take_profit": 62000.0,
        "reduce_only": False, "connectivity_test": False, "created_at": NOW,
        "strategy_id": "S001", "candidate_id": "cand_1", "strategy_rule_hash": "deadbeef",
        **kw,
    })
    # Judged at NOW, and every send in this file is judged at NOW too (the fixture below): the
    # decisions here are sealed when the module loads, and a full run reaches them minutes later.
    return approved_snapshot(intent, decided_at=NOW)


INTENT, SNAPSHOT = _intent()

BRACKET = {
    "stop_loss": 59000.0, "take_profit": 62000.0, "risk_per_unit": 1000.0,
    "stop_side": "SELL", "take_profit_side": "SELL", "working_type": "MARK_PRICE",
    "tick_size": 0.1,
}

BAR = "2026-07-25T00:00:00Z"

DECISION = {
    "status": "READY", "ready": True, "symbol": "BTCUSDT",
    "guard": {"approved": True, "status": "READY"},
    "intent": INTENT, "bracket": BRACKET, "risk_snapshot": SNAPSHOT,
    "sizing": {"sizable": True, "quantity": 0.001, "notional_usdt": 60.0},
    # The bar the leg claims before it sends (PR2a), as `plan_live_entry` names it.
    "entry_bar": {"context_key": "BTCUSDT__1d", "symbol": "BTCUSDT", "timeframe": "1d",
                  "bar_time": BAR},
}

POSITION = {
    "symbol": "BTCUSDT", "direction": "LONG", "quantity": 0.001, "entry_price": 60000.0,
    "entry_quote_usdt": 60.0, "risk": 1.0, "position_id": "pos1", "opened_at_utc": NOW,
    "stop_client_order_id": "TAI_BTCUSDT_SL_x", "take_profit_client_order_id": "TAI_BTCUSDT_TP_y",
    "strategy_id": "S001", "candidate_id": "cand_1", "strategy_rule_hash": "deadbeef",
    "entry_exchange_order_id": 111,
}


# The order the leg touched its doubles in, across all of them — what "before the send" means.
EVENTS: list[str] = []


@pytest.fixture(autouse=True)
def _fresh_events():
    EVENTS.clear()
    yield
    EVENTS.clear()


@pytest.fixture(autouse=True)
def _sent_when_decided(monkeypatch):
    """The subject here is what the leg does after the gate; the decision's age has its own tests."""
    monkeypatch.setattr(pre_order_gate, "_send_clock", lambda: NOW)


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
        EVENTS.append("submit")
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
        self.cleared_ids: list = []
        self._error = error
        self._raises = raises
        self._clear_error = clear_error

    def save_position(self, position):
        if self._error:
            raise self._raises(self._error, "scripted store failure")
        EVENTS.append("book")
        self.saved.append(dict(position))

    def clear_position(self, symbol, *, position_id=None):
        if self._clear_error:
            raise self._raises(self._clear_error, "scripted store failure")
        self.cleared.append(symbol)
        self.cleared_ids.append(position_id)


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
    """The daily counter as the leg now spends it: a locked reserve-or-refuse before the send."""

    def __init__(self, error=None, raises=ToolError, count=0):
        self.count = count
        self.limits: list[int] = []
        self._error = error
        self._raises = raises

    def reserve_submission(self, *, limit, day=None):
        EVENTS.append("reserve")
        self.limits.append(limit)
        if self._error:
            raise self._raises(self._error, "scripted counter failure")
        if self.count >= limit:
            raise ToolError("LIVE_DAILY_ORDER_CAP_REACHED", "scripted cap")
        self.count += 1
        return self.count


class FakeMarks:
    def __init__(self, error=None, raises=ToolError, symbol_error=None, release_error=None):
        self.claims: list[dict] = []
        self.taken: list[dict] = []
        self.given_back: list[dict] = []
        self._error = error
        self._raises = raises
        self._symbol_error = symbol_error
        self._release_error = release_error

    def claim_symbol(self, **kw):
        EVENTS.append("take")
        if self._symbol_error:
            raise ToolError(self._symbol_error, "scripted symbol claim failure")
        self.taken.append(dict(kw))
        return {}

    def release_symbol(self, **kw):
        EVENTS.append("give")
        if self._release_error:
            raise self._release_error
        self.given_back.append(dict(kw))
        return {}

    def claim_bar(self, **kw):
        EVENTS.append("claim")
        if self._error:
            raise self._raises(self._error, "scripted claim failure")
        self.claims.append(dict(kw))
        return {}


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
        counter=kw.pop("counter", FakeCounter()),
        entry_marks=kw.pop("entry_marks", FakeMarks()),
        snapshot_store=kw.pop("snapshot_store", FakeSnapshotStore()),
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


def test_the_daily_slot_is_reserved_against_the_registered_cap():
    counter = FakeCounter()
    _entry(counter=counter)
    assert counter.count == 1
    assert counter.limits == [LIMITS.max_daily_order_count]


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


def test_the_naked_close_withdraws_a_leg_whose_submit_timed_out():
    """PR2c-0: a submit that got no answer can land after the read that did not find it."""
    adapter = FakeAdapter(submit_errors={"TP": "ORDER_TRANSPORT"}, missing={"TP"})
    result = _entry(adapter=adapter)
    cancels = result["naked_close"]["cancels"]
    assert [c["leg"] for c in cancels] == ["stop_client_order_id", "take_profit_client_order_id"]


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
    intent, snapshot = _intent(candle_time="2026-07-25T04:00:00Z")
    assert intent["client_order_id"] != INTENT["client_order_id"]
    return {**DECISION, "intent": intent, "risk_snapshot": snapshot}


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


def test_a_target_that_partially_fills_at_placement_still_counts_as_placed():
    """A GTC target crossing part of its size the moment it lands is still a working order —
    its remainder rests. Counting it as not-placed would run `_close_naked_position` against
    a position the target is actively closing at profit."""
    intent = ll.build_bracket_intent(symbol="BTCUSDT", leg="TP", side="SELL", price=62000.0,
                                     working_type="MARK_PRICE", position_seed="seed",
                                     quantity=0.002)
    result = ll.place_bracket_leg(intent, adapter=FakeAdapter(statuses={"TP": "PARTIALLY_FILLED"}),
                                  sleep=_no_sleep)
    assert result["placed"] is True


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


# --- the bar and the day's slot are spent BEFORE the send (PR2a) -----------------------------
#
# The counter used to be written between the entry submit and the bracket — the worst escape
# point in the leg, which is why its failure was only a reason code there. It is now reserved
# before anything leaves, so its failure is simply a refusal.

def test_the_bar_is_claimed_and_the_slot_reserved_before_the_entry_is_sent():
    marks = FakeMarks()
    result = _entry(entry_marks=marks)
    assert result["status"] == ll.ENTRY_OPENED
    assert EVENTS[:4] == ["take", "claim", "reserve", "submit"]
    assert marks.claims == [{"symbol": "BTCUSDT", "timeframe": "1d", "bar_time": BAR}]


def test_a_bar_already_entered_sends_nothing_and_spends_no_slot():
    counter, adapter = FakeCounter(), FakeAdapter()
    result = _entry(entry_marks=FakeMarks(error="LIVE_ENTRY_BAR_ALREADY_ENTERED"),
                    counter=counter, adapter=adapter)
    assert result["status"] == ll.ENTRY_REFUSED
    assert result["reason_codes"] == ["LIVE_ENTRY_BAR_ALREADY_ENTERED"]
    assert adapter.submitted == [] and counter.count == 0 and result["entry"] is None


def test_the_reservation_uses_the_registered_cap_as_it_is():
    """No floor under it: a cap of zero reserves nothing, even behind a decision that says ready
    (the guard would have refused first; the reservation must not depend on that)."""
    from dataclasses import replace

    adapter, counter = FakeAdapter(), FakeCounter()
    result = _entry(limits=replace(LIMITS, max_daily_order_count=0), counter=counter, adapter=adapter)
    assert result["reason_codes"] == ["LIVE_DAILY_ORDER_CAP_REACHED"]
    assert counter.limits == [0] and adapter.submitted == []


def test_a_full_day_sends_nothing():
    """Two processes read a count under the cap; only the reservation can say which one sends."""
    adapter = FakeAdapter()
    result = _entry(counter=FakeCounter(count=LIMITS.max_daily_order_count), adapter=adapter)
    assert result["status"] == ll.ENTRY_REFUSED
    assert result["reason_codes"] == ["LIVE_DAILY_ORDER_CAP_REACHED"]
    assert adapter.submitted == []


@pytest.mark.parametrize("raises", [ToolError, PersistenceError, SafetyGateBlocked, OSError])
def test_a_counter_that_cannot_reserve_refuses_before_the_send(raises):
    """Whatever the real counter raises — a lock, the gate re-check, the write — nothing has
    left yet, so it is a refusal, never an escape to `run_live_leg` (which would call a leg that
    sent nothing an INCIDENT)."""
    adapter = FakeAdapter()
    result = _entry(counter=FakeCounter(error="LIVE_COUNTER_LOCKED", raises=raises), adapter=adapter)
    assert result["status"] == ll.ENTRY_REFUSED
    assert adapter.submitted == []
    expected = "UNEXPECTED_OSError" if raises is OSError else "LIVE_COUNTER_LOCKED"
    assert result["reason_codes"] == [expected]


@pytest.mark.parametrize("raises", [PersistenceError, SafetyGateBlocked, OSError])
def test_a_mark_store_that_cannot_claim_refuses_before_the_send(raises):
    adapter, counter = FakeAdapter(), FakeCounter()
    result = _entry(entry_marks=FakeMarks(error="LIVE_ENTRY_MARKS_LOCKED", raises=raises),
                    adapter=adapter, counter=counter)
    assert result["status"] == ll.ENTRY_REFUSED
    assert adapter.submitted == [] and counter.count == 0


@pytest.mark.parametrize("missing,reason", [
    ({"entry_marks": None}, ll.NO_ENTRY_MARKS),
    ({"counter": None}, ll.NO_ORDER_COUNTER),
])
def test_no_mark_store_or_no_counter_no_order(missing, reason):
    adapter = FakeAdapter()
    result = _entry(adapter=adapter, **missing)
    assert result["status"] == ll.ENTRY_REFUSED
    assert result["reason_codes"] == [reason]
    assert adapter.submitted == []


def test_the_real_stores_let_one_bar_send_once(tmp_path, monkeypatch):
    """End to end on the durable stores: the same decision executed twice is one order."""
    from runtime.mvp_runtime.crypto.live_order import (
        count_today,
        read_live_entry_marks,
        select_live_entry_marks,
        select_live_order_counter,
    )

    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    adapter = FakeAdapter()

    def _once():
        return _entry(adapter=adapter, counter=select_live_order_counter(root=tmp_path),
                      entry_marks=select_live_entry_marks(root=tmp_path))

    first, second = _once(), _once()
    assert first["status"] == ll.ENTRY_OPENED
    assert second["status"] == ll.ENTRY_REFUSED
    assert second["reason_codes"] == ["LIVE_ENTRY_BAR_ALREADY_ENTERED"]
    assert [r["type"] for r in adapter.submitted].count("MARKET") == 1
    assert count_today(tmp_path) == 1
    assert read_live_entry_marks(tmp_path)["entered"] == {"BTCUSDT__1d": BAR}


def test_a_decision_that_names_no_bar_cannot_be_claimed(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto.live_order import select_live_entry_marks, select_live_order_counter

    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    adapter = FakeAdapter()
    decision = {k: v for k, v in DECISION.items() if k != "entry_bar"}
    result = _entry(decision=decision, adapter=adapter,
                    counter=select_live_order_counter(root=tmp_path),
                    entry_marks=select_live_entry_marks(root=tmp_path))
    assert result["reason_codes"] == ["LIVE_ENTRY_BAR_UNKNOWN"]
    assert adapter.submitted == []


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


# --- the pre-order gate's snapshot at the leg (PR2b) ----------------------------------------------

class _EventStore(FakeSnapshotStore):
    def append(self, snapshot):
        EVENTS.append("record")
        return super().append(snapshot)


def test_the_snapshot_is_checked_first_and_recorded_after_the_bar_and_the_slot():
    store = _EventStore()
    result = _entry(snapshot_store=store)
    assert result["status"] == ll.ENTRY_OPENED
    # record, then the venue door's own idempotent re-bind (a second append that writes nothing).
    assert EVENTS[:5] == ["take", "claim", "reserve", "record", "record"] and EVENTS[5] == "submit"
    assert [s["risk_snapshot_sha256"] for s in store.appended] == [SNAPSHOT["risk_snapshot_sha256"]]
    assert result["risk_snapshot_sha256"] == SNAPSHOT["risk_snapshot_sha256"]
    assert result["position"]["risk_snapshot_sha256"] == SNAPSHOT["risk_snapshot_sha256"]


@pytest.mark.parametrize("decision,reason", [
    ({**DECISION, "risk_snapshot": None}, "RISK_SNAPSHOT_MISSING"),
    ({k: v for k, v in DECISION.items() if k != "risk_snapshot"}, "RISK_SNAPSHOT_MISSING"),
    ({**DECISION, "risk_snapshot": _intent(candle_time="2026-07-25T08:00:00Z")[1]},
     "RISK_SNAPSHOT_INTENT_MISMATCH"),
    ({**DECISION, "intent": {**INTENT, "quantity": 0.002}}, "RISK_SNAPSHOT_INTENT_MISMATCH"),
    ({**DECISION, "risk_snapshot": {**SNAPSHOT, "approved": False}}, "RISK_SNAPSHOT_TAMPERED"),
], ids=["none", "absent", "another-order", "changed-size", "edited"])
def test_a_decision_without_its_own_snapshot_spends_nothing_and_sends_nothing(decision, reason):
    marks, counter, adapter, store = FakeMarks(), FakeCounter(), FakeAdapter(), FakeSnapshotStore()
    result = _entry(decision=decision, entry_marks=marks, counter=counter, adapter=adapter,
                    snapshot_store=store)
    assert result["status"] == ll.ENTRY_REFUSED
    assert result["reason_codes"] == [reason]
    assert marks.claims == [] and counter.count == 0 and store.appended == [] and adapter.submitted == []


_SHORT_INTENT, _SHORT_SNAPSHOT = _intent(direction="SHORT", side="SELL")


@pytest.mark.parametrize("decision", [
    {**DECISION, "bracket": {**BRACKET, "stop_loss": 58000.0}},
    {**DECISION, "bracket": {**BRACKET, "take_profit": 63000.0}},
    {**DECISION, "bracket": {**BRACKET, "stop_side": "BUY"}},
    {**DECISION, "bracket": {**BRACKET, "take_profit_side": "BUY"}},
    {**DECISION, "bracket": None},
    {**DECISION, "intent": _SHORT_INTENT, "risk_snapshot": _SHORT_SNAPSHOT},
], ids=["moved-stop", "moved-target", "stop-adds", "target-adds", "no-bracket", "long-legs-on-a-short"])
def test_protection_that_is_not_the_approved_one_spends_nothing_and_sends_nothing(decision):
    """The snapshot seals the intent's stop and target, and the legs are placed from the bracket:
    a bracket that disagrees — in price, or on a side that would add to the position — is not the
    protection that was approved."""
    marks, counter, adapter, store = FakeMarks(), FakeCounter(), FakeAdapter(), FakeSnapshotStore()
    result = _entry(decision=decision, entry_marks=marks, counter=counter, adapter=adapter,
                    snapshot_store=store)
    assert result["status"] == ll.ENTRY_REFUSED
    assert result["reason_codes"] == [ll.BRACKET_NOT_APPROVED]
    assert marks.claims == [] and counter.count == 0 and store.appended == [] and adapter.submitted == []


def test_no_snapshot_store_no_order():
    adapter = FakeAdapter()
    result = _entry(snapshot_store=None, adapter=adapter)
    assert result["reason_codes"] == [ll.NO_SNAPSHOT_STORE] and adapter.submitted == []


@pytest.mark.parametrize("error,reason", [
    (PersistenceError("PRE_ORDER_SNAPSHOTS_LOCKED", "scripted"), "PRE_ORDER_SNAPSHOTS_LOCKED"),
    (OSError(28, "No space left on device"), "UNEXPECTED_OSError"),
])
def test_a_snapshot_that_cannot_be_recorded_sends_nothing(error, reason):
    adapter = FakeAdapter()
    result = _entry(snapshot_store=FakeSnapshotStore(error=error), adapter=adapter)
    assert result["status"] == ll.ENTRY_REFUSED
    assert result["reason_codes"] == [reason]
    assert adapter.submitted == []


def test_a_naked_close_records_which_snapshot_its_entry_left_under():
    outcome = _entry(adapter=FakeAdapter(missing={"TP"}))["outcome"]
    assert outcome["risk_snapshot_sha256"] == SNAPSHOT["risk_snapshot_sha256"]


def test_a_booked_position_hands_its_snapshot_to_the_exit_outcome():
    ledger = FakeLedger()
    result = _exit(position={**POSITION, "risk_snapshot_sha256": "sha256:" + "a" * 64}, ledger=ledger)
    assert result["status"] == ll.EXIT_CLOSED
    assert ledger.appended[0]["risk_snapshot_sha256"] == "sha256:" + "a" * 64


# --- PR2b review: the protective door places protective orders only -------------------------------

@pytest.mark.parametrize("intent", [
    # An opening MARKET order handed to the leg door (the review's 3 BTC case).
    {"symbol": "BTCUSDT", "side": "BUY", "order_type_exchange": "MARKET", "quantity": 3.0,
     "reduce_only": False, "client_order_id": "TAI_BTCUSDT_SL_open"},
    # A target LIMIT that lost its reduce-only flag.
    {"symbol": "BTCUSDT", "side": "SELL", "order_type_exchange": "LIMIT", "quantity": 0.001,
     "price": 62000.0, "time_in_force": "GTC", "reduce_only": False,
     "client_order_id": "TAI_BTCUSDT_TP_open"},
], ids=["market-entry", "limit-without-reduce-only"])
def test_a_leg_that_could_add_exposure_is_never_sent(intent):
    adapter = FakeAdapter()
    placed = ll.place_bracket_leg(intent, adapter=adapter, sleep=_no_sleep)
    assert placed["placed"] is False and placed["may_be_resting"] is False
    assert placed["error"] == ll.BRACKET_LEG_NOT_PROTECTIVE
    assert adapter.submitted == [] and EVENTS == []


@pytest.mark.parametrize("leg", ["SL", "TP"])
def test_the_real_legs_are_protective(leg):
    intent = ll.build_bracket_intent(symbol="BTCUSDT", leg=leg, side="SELL", price=59000.0,
                                     working_type="MARK_PRICE", position_seed="seed", quantity=0.001)
    adapter = FakeAdapter()
    placed = ll.place_bracket_leg(intent, adapter=adapter, sleep=_no_sleep)
    assert placed["placed"] is True and len(adapter.submitted) == 1



# --- PR2b-2: the symbol is taken before anything is spent, and given back only once the book says
# what the venue holds ------------------------------------------------------------------------------

_CLAIM = {"symbol": "BTCUSDT", "client_order_id": INTENT["client_order_id"]}


def test_the_symbol_is_taken_first_and_given_back_once_the_position_is_booked():
    marks = FakeMarks()
    result = _entry(entry_marks=marks)
    assert result["status"] == ll.ENTRY_OPENED
    assert marks.taken == [{**_CLAIM, "door": "autonomous", "now": NOW}]
    assert marks.given_back == [_CLAIM]
    assert EVENTS[0] == "take" and EVENTS.index("book") < EVENTS.index("give")


def test_an_entry_in_flight_on_the_symbol_costs_nothing():
    marks, counter, adapter, store = (FakeMarks(symbol_error="LIVE_ENTRY_SYMBOL_IN_FLIGHT"),
                                      FakeCounter(), FakeAdapter(), FakeSnapshotStore())
    result = _entry(entry_marks=marks, counter=counter, adapter=adapter, snapshot_store=store)
    assert result["status"] == ll.ENTRY_REFUSED
    assert result["reason_codes"] == ["LIVE_ENTRY_SYMBOL_IN_FLIGHT"]
    assert marks.claims == [] and counter.count == 0 and store.appended == [] and adapter.submitted == []
    assert marks.given_back == []          # it never held the symbol


class _SecondAppendFails(FakeSnapshotStore):
    """Records once (the leg), then refuses the venue door's re-bind: a SubmitRefused."""

    def append(self, snapshot):
        if self.appended:
            raise PersistenceError("PRE_ORDER_SNAPSHOTS_LOCKED", "scripted re-bind failure")
        return super().append(snapshot)


@pytest.mark.parametrize("kw,reason", [
    ({"entry_marks": FakeMarks(error="LIVE_ENTRY_BAR_ALREADY_ENTERED")}, "LIVE_ENTRY_BAR_ALREADY_ENTERED"),
    ({"counter": FakeCounter(count=LIMITS.max_daily_order_count)}, "LIVE_DAILY_ORDER_CAP_REACHED"),
    ({"snapshot_store": FakeSnapshotStore(error=PersistenceError("PRE_ORDER_SNAPSHOTS_LOCKED", "x"))},
     "PRE_ORDER_SNAPSHOTS_LOCKED"),
    ({"snapshot_store": _SecondAppendFails()}, "PRE_ORDER_SNAPSHOTS_LOCKED"),
], ids=["bar-spent", "day-full", "snapshot-not-recorded", "refused-at-the-venue-door"])
def test_a_refusal_after_the_symbol_was_taken_gives_it_back(kw, reason):
    marks = kw.pop("entry_marks", FakeMarks())
    adapter = FakeAdapter()
    result = _entry(entry_marks=marks, adapter=adapter, **kw)
    assert result["status"] == ll.ENTRY_REFUSED and result["reason_codes"] == [reason]
    assert adapter.submitted == []
    assert marks.given_back == [_CLAIM]


def test_an_entry_the_venue_did_not_confirm_keeps_the_symbol():
    """No fill reported is not "nothing is working": the order may still fill, so the symbol stays
    taken until its claim expires."""
    marks = FakeMarks()
    result = _entry(entry_marks=marks, adapter=FakeAdapter(statuses={"ENTRY": "NEW"}))
    assert result["status"] == ll.ENTRY_NOT_CONFIRMED
    assert marks.taken and marks.given_back == []


def test_a_naked_close_the_venue_confirmed_gives_the_symbol_back():
    marks = FakeMarks()
    result = _entry(entry_marks=marks, adapter=FakeAdapter(missing={"TP"}))
    assert result["status"] == ll.ENTRY_NAKED_CLOSED
    assert marks.given_back == [_CLAIM]


def test_a_naked_close_that_did_not_confirm_keeps_the_symbol():
    marks = FakeMarks()
    adapter = FakeAdapter(missing={"TP"}, statuses={"CLOSE": ToolError("VENUE_TIMEOUT", "scripted")})
    result = _entry(entry_marks=marks, adapter=adapter)
    assert result["status"] == ll.ENTRY_NAKED_OPEN
    assert marks.given_back == []


def test_a_position_the_book_could_not_hold_keeps_the_symbol():
    marks = FakeMarks()
    result = _entry(entry_marks=marks, position_store=FakeStore(error="LIVE_STATE_LOCKED",
                                                                raises=PersistenceError))
    assert ll.POSITION_PERSIST_FAILED in result["reason_codes"]
    assert marks.given_back == []


@pytest.mark.parametrize("error", [PersistenceError("LIVE_ENTRY_MARKS_LOCKED", "x"), OSError(28, "full")])
def test_a_claim_that_cannot_be_given_back_is_reported_never_raised(error):
    result = _entry(entry_marks=FakeMarks(release_error=error))
    assert result["status"] == ll.ENTRY_OPENED
    assert result["reason_codes"] == [ll.CLAIM_NOT_RELEASED]


def test_the_real_marks_give_the_symbol_back_and_the_book_refuses_the_next_entry(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto.live_order import (
        LIVE_ENTRY_SYMBOL_OCCUPIED,
        read_live_entry_marks,
        select_live_entry_marks,
        select_live_order_counter,
    )
    from runtime.mvp_runtime.crypto.live_position import select_live_position_store

    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    marks = select_live_entry_marks(root=tmp_path)
    result = _entry(entry_marks=marks, counter=select_live_order_counter(root=tmp_path),
                    position_store=select_live_position_store(root=tmp_path))
    assert result["status"] == ll.ENTRY_OPENED, result["reason_codes"]
    assert read_live_entry_marks(tmp_path)["in_flight"] == {}
    with pytest.raises(ToolError) as refused:
        marks.claim_symbol(symbol="BTCUSDT", door="probe", client_order_id="TAI_BTCUSDT_LONG_next",
                           now=NOW)
    assert refused.value.reason_code == LIVE_ENTRY_SYMBOL_OCCUPIED


# --- PR2b-2 review ---------------------------------------------------------------------------------

def test_the_exits_clear_only_their_own_positions_record():
    """The book is one record per symbol: a close names the position it closed, so a record the
    symbol holds for another position by then is left alone (the store refuses)."""
    POS = {**POSITION, "position_id": "pos1"}
    exit_store, settle_store = FakeStore(), FakeStore()
    assert _exit(position=POS, position_store=exit_store)["status"] == ll.EXIT_CLOSED
    assert _settle(position=POS, position_store=settle_store)["status"] == ll.EXIT_CLOSED
    assert exit_store.cleared_ids == ["pos1"] and settle_store.cleared_ids == ["pos1"]


def test_a_settle_whose_symbol_now_holds_another_position_leaves_that_record(tmp_path):
    from runtime.mvp_runtime.crypto.live_position import (
        LIVE_POSITION_SLOT_TAKEN,
        RealLivePositionStore,
        build_live_position,
        load_open_live_position,
    )
    from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_FLAGS, LIVE_TRADING_PROVIDER_ID
    from tests._helpers import make_gate_authorization

    auth = make_gate_authorization(flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID)
    store = RealLivePositionStore(root=tmp_path, authorization=auth)
    other = build_live_position(symbol="BTCUSDT", direction="LONG", quantity=0.002, entry_price=61000.0,
                                opened_at="2026-07-25T13:00:00Z", strategy_id="S002")
    store.save_position(other)
    result = _settle(position=POSITION, position_store=store)
    assert result["status"] == ll.EXIT_CLOSED
    assert LIVE_POSITION_SLOT_TAKEN in result["reason_codes"]
    assert load_open_live_position("BTCUSDT", tmp_path)["position_id"] == other["position_id"]


@pytest.mark.parametrize("marks,expected", [
    (FakeMarks(release_error=ToolError("LIVE_ENTRY_CLAIM_LOST", "scripted")), [ll.CLAIM_LOST]),
    (FakeMarks(error="LIVE_ENTRY_BAR_ALREADY_ENTERED",
               release_error=ToolError("LIVE_ENTRY_CLAIM_LOST", "scripted")),
     ["LIVE_ENTRY_BAR_ALREADY_ENTERED"]),
], ids=["after-the-send", "before-the-send"])
def test_a_lost_claim_is_an_incident_only_once_the_order_left(marks, expected):
    result = _entry(entry_marks=marks)
    assert result["reason_codes"] == expected


@pytest.mark.parametrize("submit_error,found,given_back", [
    ("ORDER_REJECTED", False, True),    # the venue refused it with its own code, and has no such order
    ("ORDER_TRANSPORT", False, False),  # a timeout: the request may still land after the read
    ("ORDER_REJECTED", True, False),    # a duplicate id: the original order is there, and working
])
def test_an_order_the_venue_does_not_have_gives_the_symbol_back_only_if_it_refused_it(
        submit_error, found, given_back):
    marks = FakeMarks()
    adapter = (FakeAdapter(submit_errors={"ENTRY": submit_error}, statuses={"ENTRY": "NEW"}) if found
               else FakeAdapter(submit_errors={"ENTRY": submit_error}, missing={"ENTRY"}))
    result = _entry(entry_marks=marks, adapter=adapter)
    assert result["status"] == ll.ENTRY_NOT_CONFIRMED
    assert (result["entry"]["reconcile_status"] == "NOT_FOUND") is not found
    assert (marks.given_back == [_CLAIM]) is given_back


def test_a_partial_fill_keeps_the_symbol_even_once_its_close_confirmed():
    """A partial fill is not terminal: what fills after the reported part was closed is exposure
    nobody booked, so the symbol stays claimed until it expires."""
    marks = FakeMarks()
    adapter = FakeAdapter(statuses={"ENTRY": "PARTIALLY_FILLED"},
                          fills={"ENTRY": {"executedQty": 0.0005}})
    result = _entry(entry_marks=marks, adapter=adapter)
    assert result["status"] == ll.ENTRY_NAKED_CLOSED
    assert marks.taken and marks.given_back == []


# --- PR2c-0: the close path never unprotects an open position, and never forgets a leg ----------

def test_an_unconfirmed_naked_close_keeps_the_stop_that_rests():
    """The stop placed, the target did not, and the naked close could not be confirmed. Before
    PR2c-0 the legs were withdrawn before the close was checked, so the one stop the possibly
    still-open position had was cancelled."""
    adapter = FakeAdapter(missing={"TP"},
                          statuses={"CLOSE": ToolError("VENUE_TIMEOUT", "scripted close read failure")})
    result = _entry(adapter=adapter)
    assert result["status"] == ll.ENTRY_NAKED_OPEN
    assert result["naked_close"]["cancels"] == []
    assert adapter.cancelled == []


def test_a_confirmed_naked_close_whose_cancel_fails_says_so_and_keeps_the_symbol():
    marks = FakeMarks()
    adapter = FakeAdapter(missing={"TP"}, cancel_errors={"SL": "ORDER_TRANSPORT"})
    result = _entry(adapter=adapter, entry_marks=marks)
    assert result["status"] == ll.ENTRY_NAKED_CLOSED
    assert ll.BRACKET_CANCEL_FAILED in result["reason_codes"]
    assert marks.taken and marks.given_back == []


def _leg(adapter, leg="SL"):
    intent = ll.build_bracket_intent(symbol="BTCUSDT", leg=leg, side="SELL", price=59000.0,
                                     working_type="MARK_PRICE", position_seed="seed", quantity=0.001)
    return ll.place_bracket_leg(intent, adapter=adapter, sleep=_no_sleep)


@pytest.mark.parametrize("adapter,may_rest", [
    # The read itself failed: nobody can say the leg is absent.
    (FakeAdapter(statuses={"SL": ToolError("VENUE_TIMEOUT", "scripted read failure")}), True),
    # The submit timed out and the read found nothing: the request may still land.
    (FakeAdapter(submit_errors={"SL": "ORDER_TRANSPORT"}, missing={"SL"}), True),
    # The venue refused it with its own code, and has no such order: certainly absent.
    (FakeAdapter(submit_errors={"SL": "ORDER_REJECTED"}, missing={"SL"}), False),
    # Accepted without naming an order, and not found: the ordinary miss stays ordinary.
    (FakeAdapter(missing={"SL"}), False),
], ids=["read-failed", "submit-timed-out", "refused-outright", "accepted-unnamed"])
def test_a_leg_whose_absence_is_not_certain_may_be_resting(adapter, may_rest):
    placed = _leg(adapter)
    assert placed["placed"] is False
    assert placed["may_be_resting"] is may_rest


def test_a_duplicate_refusal_is_not_an_outright_one():
    """-4116 means the original order already landed; a read that cannot find it is not proof it
    is gone."""
    class _Duplicate(FakeAdapter):
        def submit(self, order_request, *, timeout_seconds=10):
            self.submitted.append(dict(order_request))
            raise ToolError("ORDER_REJECTED", "duplicate client order id (-4116) — the original order "
                                              "already landed; reconcile decides the outcome")

    placed = _leg(_Duplicate(missing={"SL"}))
    assert placed["may_be_resting"] is True


def test_a_naked_close_withdraws_a_stop_whose_confirmation_read_failed():
    adapter = FakeAdapter(statuses={"SL": ToolError("VENUE_TIMEOUT", "scripted read failure")})
    result = _entry(adapter=adapter)
    assert result["status"] == ll.ENTRY_NAKED_CLOSED
    assert "stop_client_order_id" in [c["leg"] for c in result["naked_close"]["cancels"]]


def test_a_confirmed_close_that_cannot_be_priced_still_withdraws_its_legs():
    """A booked position keeps its record for a settle retry, but the legs protect nothing once the
    venue confirmed the close — and a probe's never-booked position has no retry at all."""
    adapter = FakeAdapter(fills={"CLOSE": {"cumQuote": None, "avgPrice": None, "executedQty": 0.001}})
    store = FakeStore()
    result = _exit(adapter=adapter, position_store=store)
    assert result["status"] == ll.EXIT_NOT_CONFIRMED
    assert ll.FILL_FACTS_MISSING in result["reason_codes"]
    assert len(adapter.cancelled) == 2 and store.cleared == []


# --- PR2c-0 review ---------------------------------------------------------------------------------

def test_two_naked_closes_of_one_symbol_in_one_pass_never_share_an_id():
    """Two entries of one symbol, one fire's `now`, the same size: keyed on symbol, time and size,
    their naked closes shared a client id, the second was refused as a duplicate, and its read
    reconciled against the FIRST close — while the second position lost its stop."""
    ids = []
    for bar in ("2026-07-25T00:00:00Z", "2026-07-25T08:00:00Z"):
        intent, snapshot = _intent(candle_time=bar)
        decision = {**DECISION, "intent": intent, "risk_snapshot": snapshot,
                    "entry_bar": {**DECISION["entry_bar"], "bar_time": bar}}
        result = _entry(decision=decision, adapter=FakeAdapter(missing={"TP"}))
        assert result["status"] == ll.ENTRY_NAKED_CLOSED
        ids.append(result["naked_close"]["result"]["client_order_id"])
    assert ids[0] != ids[1]


def test_an_unconfirmed_naked_close_names_the_legs_it_left():
    adapter = FakeAdapter(missing={"TP"},
                          statuses={"CLOSE": ToolError("VENUE_TIMEOUT", "scripted close read failure")})
    result = _entry(adapter=adapter)
    assert result["status"] == ll.ENTRY_NAKED_OPEN
    stop_id = result["bracket"][0]["client_order_id"]
    assert result["naked_close"]["left_resting"] == [stop_id]
    assert ll.BRACKET_LEFT_RESTING in result["reason_codes"]
    assert ll.legs_left_resting(result) == [stop_id]


def test_the_legs_left_are_the_failed_cancels_and_the_ones_kept():
    result = {"cancels": [{"client_order_id": "a", "error": "X"}, {"client_order_id": "b", "error": None}],
              "left_resting": ["c"],
              "naked_close": {"cancels": [{"client_order_id": "d", "error": "Y"}], "left_resting": ["a"]}}
    assert ll.legs_left_resting(result) == ["a", "c", "d"]
    assert ll.legs_left_resting({}) == []


def test_an_exit_asked_to_keep_its_legs_closes_and_names_them():
    adapter, store = FakeAdapter(), FakeStore()
    result = _exit(adapter=adapter, position_store=store, withdraw_legs=False)
    assert result["status"] == ll.EXIT_CLOSED
    assert adapter.cancelled == []
    assert result["left_resting"] == [POSITION["stop_client_order_id"], POSITION["take_profit_client_order_id"]]
    assert ll.BRACKET_LEFT_RESTING in result["reason_codes"]
    assert store.cleared == ["BTCUSDT"]


class _DuplicateEntry(FakeAdapter):
    def submit(self, order_request, *, timeout_seconds=10):
        if "_SL_" in str(order_request.get("clientAlgoId") or "") or "_TP_" in str(order_request.get("newClientOrderId") or ""):
            return super().submit(order_request, timeout_seconds=timeout_seconds)
        self.submitted.append(dict(order_request))
        raise ToolError("ORDER_REJECTED", "duplicate client order id (-4116) — the original order "
                                          "already landed; reconcile decides the outcome")


@pytest.mark.parametrize("adapter", [
    FakeAdapter(submit_errors={"ENTRY": "ORDER_OUTCOME_UNKNOWN"}, missing={"ENTRY"}),
    _DuplicateEntry(missing={"ENTRY"}),
], ids=["outcome-unknown", "duplicate"])
def test_an_entry_the_venue_may_still_hold_keeps_the_symbol(adapter):
    """Neither a code that leaves the outcome unknown nor a duplicate refusal proves the order is
    absent, whatever the read says."""
    marks = FakeMarks()
    result = _entry(entry_marks=marks, adapter=adapter)
    assert result["status"] == ll.ENTRY_NOT_CONFIRMED
    assert marks.taken and marks.given_back == []


def test_a_leg_whose_outcome_the_venue_cannot_state_may_be_resting():
    placed = _leg(FakeAdapter(submit_errors={"SL": "ORDER_OUTCOME_UNKNOWN"}, missing={"SL"}))
    assert placed["may_be_resting"] is True
