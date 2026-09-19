"""The emergency close (crypto PR6c, Thomas decision 49): ask under the HARD halt -> Thomas approves
-> the operator spends once -> every booked position closes at market, reduceOnly.

**Nothing here opens a socket or places an order.** The venue is a fake behind the gate, the account
read is monkeypatched, and the book, the ledger and the approvals are real stores in a temp root.

What it pins, in the order the door runs:

- **the ask** binds the HARD halt in effect and every booked position (decimal quantities), and is
  refused without the HARD halt with the runtime ACTIVE, or with nothing booked;
- **before the spend**, every refusal that needs no venue leaves the approval APPROVED and sends
  nothing: a changed halt, a closed gate, no confirmation phrase, nothing still booked, an unreadable
  account, a stopped runtime, a grant that is not an emergency close;
- **after the spend**, each position is judged again just before its close and skipped, never resized,
  when the halt, the book or the venue moved; a position the venue holds that the book does not is
  never touched;
- **what goes out** is a reduceOnly MARKET close per position, which the HARD halt at the adapter
  (PR6b) never refuses, recorded as `emergency_close` and audited as such.
"""

from __future__ import annotations

from typing import Any

import pytest

from runtime.mvp_runtime import approval, permission
from runtime.mvp_runtime.approval_store import ApprovalStore
from runtime.mvp_runtime.control import ACTIVE, HALT_HARD, HALT_SOFT, KILLED, PAUSED, ControlState, ControlStore
from runtime.mvp_runtime.crypto import live_execution, live_governance, live_leg, live_route
from runtime.mvp_runtime.crypto.account import AccountPosition, AccountSnapshot
from runtime.mvp_runtime.crypto.live_order import LIVE_CONFIRMATION_PHRASE, LiveOrderLimits
from runtime.mvp_runtime.crypto.live_pnl import read_live_outcomes
from runtime.mvp_runtime.crypto.live_position import list_open_live_positions
from runtime.mvp_runtime.errors import MvpRuntimeError, PlannerBlocked, ToolError
from runtime.mvp_runtime.switch_bridge import stop_ref
from scripts import emergency_close as door
from tests._helpers import requires_local_core

NOW = "2026-09-19T12:00:00Z"
_GOVERNANCE = {"purpose": "emergency_close", "bound_task": {}, "permission_decision": {}, "order_fingerprint": "f"}


# --- doubles -------------------------------------------------------------------------------------

class _Venue:
    """The venue: a MARKET order fills at once, a resting leg rests until cancelled. ``on_submit``
    runs after each accepted order, so a test can move the control state between two closes."""

    tool_id, tool_version = "fake", "0"
    network_egress = True

    def __init__(self, *, on_submit=None, submit_raises=None):
        self.submitted: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.requests: dict[str, dict[str, Any]] = {}
        self.on_submit = on_submit
        self.submit_raises = submit_raises

    def submit(self, order_request, *, timeout_seconds: int = 10):
        if self.submit_raises is not None:
            raise self.submit_raises
        self.submitted.append(dict(order_request))
        self.requests[str(order_request.get("clientAlgoId") or order_request["newClientOrderId"])] = dict(order_request)
        if self.on_submit is not None:
            self.on_submit(order_request)
        return {"accepted": True}

    def fetch_order(self, symbol, client_order_id, *, timeout_seconds: int = 10, algo: bool = False):
        request = self.requests.get(str(client_order_id))
        if request is None:
            return None
        qty = float(request.get("quantity") or 0.0)
        return {"symbol": symbol, "side": request["side"], "status": "FILLED", "executedQty": qty,
                "reduceOnly": bool(request.get("reduceOnly")), "avgPrice": "61000.0",
                "cumQuote": str(round(qty * 61000.0, 8)), "orderId": "oid"}

    def cancel_order(self, symbol, client_order_id, *, timeout_seconds: int = 10, algo: bool = False):
        self.cancelled.append(str(client_order_id))
        return {"status": "CANCELED"}


def _position(symbol="BTCUSDT", position_id="live-btc", direction="LONG", quantity=0.002, **kw) -> dict[str, Any]:
    position = {
        "stage": "live", "status": "OPEN", "symbol": symbol, "direction": direction, "quantity": quantity,
        "entry_price": 60000.0, "notional_usdt": round(quantity * 60000.0, 2), "stop_loss": 59000.0,
        "take_profit": 62000.0, "risk": 2.0, "opened_at_utc": "2026-09-19T08:00:00Z",
        "entry_exchange_order_id": f"venue-{position_id}", "position_id": position_id,
        "entry_quote_usdt": 120.0, "stop_client_order_id": f"sl-{position_id}",
        "take_profit_client_order_id": f"tp-{position_id}", "strategy_id": "S001",
    }
    position.update(kw)
    return position


_BTC = _position()
_ETH = _position(symbol="ETHUSDT", position_id="live-eth", direction="SHORT", quantity=0.05)


def _held(position, *, side=None, quantity=None) -> AccountPosition:
    qty = float(position["quantity"] if quantity is None else quantity)
    return AccountPosition(symbol=position["symbol"], side=side or position["direction"], quantity=qty,
                           entry_price=60000.0, mark_price=61000.0, unrealized_pnl=0.0, leverage=5.0,
                           notional=qty * 61000.0)


def _snapshot(*held) -> AccountSnapshot:
    return AccountSnapshot(asset="USDT", wallet_balance=500.0, margin_balance=500.0, available_balance=400.0,
                           unrealized_pnl=0.0, positions=list(held), realized_windows={}, source="fake",
                           collected_at=NOW)


def _limits(*, phrase=LIVE_CONFIRMATION_PHRASE) -> LiveOrderLimits:
    return LiveOrderLimits(max_order_notional_usdt=60.0, max_daily_order_count=3, max_open_notional_usdt=120.0,
                           daily_loss_limit_usdt=20.0, confirmation=phrase)


class _Wired:
    def __init__(self, root, venue, control):
        self.root, self.venue, self.control = root, venue, control
        self.reports: list[dict[str, Any]] = []
        self.purposes: list[str] = []


def _wire(tmp_path, monkeypatch, *, booked=(_BTC, _ETH), held=None, venue=None, gate=None, limits=None,
          account=None, halt=HALT_HARD, mode=ACTIVE) -> _Wired:
    """A machine with the live-trading opt-in (real book, real ledger, real API breaker in the temp
    root), a HARD halt, the positions in ``booked`` on the book and ``held`` at the venue (default:
    exactly the book)."""
    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    venue = venue if venue is not None else _Venue()
    monkeypatch.setattr(live_route, "select_live_gate", gate or (lambda **kw: (venue, None)))
    snapshot = _snapshot(*(_held(p) for p in booked)) if held is None else _snapshot(*held)
    monkeypatch.setattr(live_route, "read_account", account or (lambda **kw: (snapshot, {})))
    chosen = limits or _limits()
    monkeypatch.setattr(live_route, "resolve_live_order_limits", lambda root, now=None: (chosen, {"valid": True}))
    wired = _Wired(tmp_path, venue, ControlStore(tmp_path))

    def _prepare(intent, *, purpose, **kw):
        wired.purposes.append(purpose)
        return dict(_GOVERNANCE, purpose=purpose)

    monkeypatch.setattr(live_governance, "prepare_live_order_governance", _prepare)
    monkeypatch.setattr(live_route, "_report", lambda record, governance, submit, **kw: wired.reports.append(
        {"purpose": governance["purpose"], "submit": dict(submit)}))
    store = live_route.select_live_position_store(now=NOW, root=tmp_path)
    for position in booked:
        store.save_position(position)
    wired.control.save(ControlState(mode=mode, updated_by="op", updated_at=NOW, reason="only exits",
                                    trading_armed=False, halt_level=halt))
    return wired


def _approve(root, approval_id):
    store = ApprovalStore.default(root)
    record = store.get(approval_id)
    decision = store.get_permission_decision(record["permission_decision_id"])
    verification = approval.Verification(
        approved_by="Thomas", method="telegram_private_control_channel",
        verification_ref=f"telegram:private_chat:registered-thomas:{approval_id}")
    store.append([approval.record_decision(record, decision, granted=True, verification=verification,
                                           reason="Approved.", now=NOW)])


def _granted(wired) -> str:
    asked = door.run_request(root=wired.root, now=NOW, requested_by="thomas", reason="venue incident")
    _approve(wired.root, asked["approval_id"])
    return asked["approval_id"]


def _status(root, approval_id) -> str:
    return ApprovalStore.default(root).get(approval_id)["status"]


def _refused(wired, approval_id, code):
    with pytest.raises(MvpRuntimeError) as exc:
        door.run_confirm(root=wired.root, now=NOW, approval_id=approval_id)
    assert exc.value.reason_code == code
    assert _status(wired.root, approval_id) == "APPROVED", "a refusal before the spend leaves the grant"
    assert wired.venue.submitted == [], "a refusal before the spend sends nothing"
    return exc.value


def _rows(report) -> dict[str, dict[str, Any]]:
    return {row["position_id"]: row for row in report["positions"]}


# --- the ask -------------------------------------------------------------------------------------

@requires_local_core
def test_the_ask_binds_the_hard_halt_and_every_booked_position(tmp_path, monkeypatch):
    wired = _wire(tmp_path, monkeypatch, gate=lambda **kw: pytest.fail("the ask reads no venue"))
    asked = door.run_request(root=tmp_path, now=NOW, requested_by="thomas", reason="venue incident")
    record = ApprovalStore.default(tmp_path).get(asked["approval_id"])
    snapshot = record["approved_action_snapshot"]
    assert snapshot["permission_scope"] == permission.EMERGENCY_CLOSE_PERMISSION_SCOPE == "RUNTIME_GOVERNANCE"
    assert snapshot["target_ref"].startswith(permission.EMERGENCY_CLOSE_TARGET_PREFIX)
    assert snapshot["normalized_parameters"] == {
        "halt_ref": stop_ref(wired.control.load()),
        "positions": [
            {"position_id": "live-btc", "symbol": "BTCUSDT", "direction": "LONG", "quantity": "0.002"},
            {"position_id": "live-eth", "symbol": "ETHUSDT", "direction": "SHORT", "quantity": "0.05"},
        ],
        "requested_by": "thomas", "reason": "venue incident",
    }
    assert record["status"] == "PENDING"
    # Decision 49's 15 minutes: the RUNTIME_GOVERNANCE ceiling, below the decision's own expiry.
    assert record["validity"]["expires_at"] == "2026-09-19T12:15:00Z"
    assert wired.venue.submitted == [] and len(list_open_live_positions(tmp_path)) == 2


@pytest.mark.parametrize("mode,halt", [(ACTIVE, None), (ACTIVE, HALT_SOFT), (KILLED, HALT_HARD), (PAUSED, HALT_HARD)])
def test_no_ask_without_the_hard_halt_with_the_runtime_active(tmp_path, monkeypatch, mode, halt):
    _wire(tmp_path, monkeypatch, halt=halt, mode=mode)
    with pytest.raises(ToolError) as exc:
        door.run_request(root=tmp_path, now=NOW, requested_by="thomas", reason="r")
    assert exc.value.reason_code == live_route.EMERGENCY_CLOSE_NEEDS_HARD_HALT
    assert ApprovalStore.default(tmp_path).pending() == []


def test_no_ask_when_nothing_is_booked(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, booked=())
    with pytest.raises(ToolError) as exc:
        door.run_request(root=tmp_path, now=NOW, requested_by="thomas", reason="r")
    assert exc.value.reason_code == live_route.EMERGENCY_CLOSE_NOTHING_BOOKED


def test_the_quantity_is_one_decimal_spelling_at_the_ask_and_the_spend():
    assert [live_route.emergency_quantity_text(q) for q in (0.002, 1e-05, 100.0, "0.05", None)] == [
        "0.002", "0.00001", "100.0", "0.05", ""]


@requires_local_core
def test_the_builder_refuses_an_ask_that_does_not_name_what_it_closes():
    from runtime.mvp_runtime.binding import bind_task_to_core
    from runtime.mvp_runtime.intake import build_task

    _, bound = bind_task_to_core(build_task("긴급 청산 검토", now=NOW, channel="manual", requester_id="Thomas"), now=NOW)
    good = {"halt_ref": "stop_x", "positions": [
        {"position_id": "p", "symbol": "BTCUSDT", "direction": "LONG", "quantity": "0.002"}],
        "requested_by": "thomas", "reason": "r"}
    for bad in ({**good, "halt_ref": ""}, {**good, "positions": []},
                {**good, "positions": [{**good["positions"][0], "quantity": 0.002}]},
                {**good, "reason": " "}):
        with pytest.raises(PlannerBlocked) as exc:
            permission.build_emergency_close_permission_decision(bound, content=bad, now=NOW)
        assert exc.value.reason_code == "INVALID_EMERGENCY_CLOSE"
    one = permission.build_emergency_close_permission_decision(bound, content=good, now=NOW)
    other = permission.build_emergency_close_permission_decision(
        bound, content={**good, "positions": [{**good["positions"][0], "quantity": "0.003"}]}, now=NOW)
    assert one["fingerprint_payload"]["target_ref"] != other["fingerprint_payload"]["target_ref"]
    assert one["action_fingerprint"] != other["action_fingerprint"], "another set is another grant"
    assert one["fingerprint_payload"]["permission_scope"] == "RUNTIME_GOVERNANCE"


@requires_local_core
def test_the_request_message_says_the_close_cannot_be_undone(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    asked = door.run_request(root=tmp_path, now=NOW, requested_by="thomas", reason="venue incident")
    store = ApprovalStore.default(tmp_path)
    record = store.get(asked["approval_id"])
    text = approval.request_message(record, store.get_permission_decision(record["permission_decision_id"]))
    assert "되돌릴 수 있는가: 아니오 — 청산된 포지션은 되돌릴 수 없습니다" in text
    assert "scripts/emergency_close.py --confirm --approval-id" in text
    assert "EMERGENCY_CLOSE_HALT_CHANGED" in text and "예상 비용: 시장가 청산" in text
    assert "BTCUSDT LONG 0.002" in text


# --- before the spend: nothing sent, the grant stays APPROVED --------------------------------------

@requires_local_core
def test_a_halt_written_since_the_ask_refuses_before_the_spend(tmp_path, monkeypatch):
    """Any control write moves `stop_ref`: here the runtime was killed and moved back to HARD."""
    wired = _wire(tmp_path, monkeypatch)
    approval_id = _granted(wired)
    wired.control.save(ControlState(mode=ACTIVE, updated_by="op", updated_at="2026-09-19T12:05:00Z",
                                    reason="hard again", trading_armed=False, halt_level=HALT_HARD))
    _refused(wired, approval_id, live_route.EMERGENCY_CLOSE_HALT_CHANGED)


@requires_local_core
@pytest.mark.parametrize("halt", [None, HALT_SOFT])
def test_a_halt_lifted_or_loosened_since_the_ask_refuses_before_the_spend(tmp_path, monkeypatch, halt):
    wired = _wire(tmp_path, monkeypatch)
    approval_id = _granted(wired)
    wired.control.save(ControlState(mode=ACTIVE, updated_by="op", updated_at=NOW, reason="only exits",
                                    trading_armed=False, halt_level=halt))
    _refused(wired, approval_id, live_route.EMERGENCY_CLOSE_HALT_CHANGED)


@requires_local_core
def test_a_stopped_runtime_refuses_before_the_spend(tmp_path, monkeypatch):
    wired = _wire(tmp_path, monkeypatch)
    approval_id = _granted(wired)
    wired.control.save(ControlState(mode=KILLED, updated_by="op", updated_at=NOW, reason="stop",
                                    trading_armed=False, halt_level=HALT_HARD))
    with pytest.raises(MvpRuntimeError):
        door.run_confirm(root=tmp_path, now=NOW, approval_id=approval_id)
    assert _status(tmp_path, approval_id) == "APPROVED" and wired.venue.submitted == []


@requires_local_core
def test_a_closed_gate_refuses_before_the_spend(tmp_path, monkeypatch):
    """A dry-run adapter would spend the grant and close nothing."""
    wired = _wire(tmp_path, monkeypatch, gate=lambda **kw: (None, "LIVE_ROUTING_DISABLED"))
    error = _refused(wired, _granted(wired), live_route.EMERGENCY_CLOSE_GATE_CLOSED)
    assert "LIVE_ROUTING_DISABLED" in str(error)


@requires_local_core
def test_no_confirmation_phrase_refuses_before_the_spend(tmp_path, monkeypatch):
    """The close guard refuses every close without it, so a spend would close nothing."""
    wired = _wire(tmp_path, monkeypatch, limits=_limits(phrase=""))
    _refused(wired, _granted(wired), live_route.EMERGENCY_CLOSE_NO_CONFIRMATION)


@requires_local_core
def test_an_unreadable_account_refuses_before_the_spend(tmp_path, monkeypatch):
    wired = _wire(tmp_path, monkeypatch, account=lambda **kw: (None, {"degraded_reason_code": "ACCOUNT_DATA_DEGRADED"}))
    _refused(wired, _granted(wired), live_route.EMERGENCY_CLOSE_ACCOUNT_UNREADABLE)


@requires_local_core
def test_nothing_still_booked_refuses_before_the_spend(tmp_path, monkeypatch):
    wired = _wire(tmp_path, monkeypatch)
    approval_id = _granted(wired)
    store = live_route.select_live_position_store(now=NOW, root=tmp_path)
    for position in (_BTC, _ETH):
        store.clear_position(position["symbol"], position_id=position["position_id"])
    _refused(wired, approval_id, live_route.EMERGENCY_CLOSE_NOTHING_BOOKED)


@requires_local_core
def test_a_halt_that_moves_inside_the_spend_refuses_and_spends_nothing(tmp_path, monkeypatch):
    """The last re-read is inside the spend lock: a resume landing after every check before it, and
    before the CONSUMED record, still leaves the grant APPROVED and sends nothing."""
    wired = _wire(tmp_path, monkeypatch)
    approval_id = _granted(wired)
    snapshot = _snapshot(_held(_BTC), _held(_ETH))

    def _read_then_resume(**kw):
        wired.control.save(ControlState(mode=ACTIVE, updated_by="op", updated_at=NOW, reason="resumed",
                                        trading_armed=True))
        return snapshot, {}

    monkeypatch.setattr(live_route, "read_account", _read_then_resume)
    _refused(wired, approval_id, live_route.EMERGENCY_CLOSE_HALT_CHANGED)


@requires_local_core
def test_another_grant_is_not_spent_as_an_emergency_close(tmp_path, monkeypatch):
    """Same scope (RUNTIME_GOVERNANCE), another target: an execution-stage grant is refused by name."""
    wired = _wire(tmp_path, monkeypatch)
    from scripts import register_execution_stage as stage_door

    asked = stage_door.run_request(root=tmp_path, now=NOW, target="PAPER", registered_by="thomas",
                                   reason="initial", attestation="paper ledger")
    _approve(tmp_path, asked["approval_id"])
    _refused(wired, asked["approval_id"], door.NOT_A_CLOSE_GRANT)


# --- after the spend ----------------------------------------------------------------------------

@requires_local_core
def test_the_approved_positions_close_reduce_only_and_the_grant_is_spent_once(tmp_path, monkeypatch):
    wired = _wire(tmp_path, monkeypatch)
    approval_id = _granted(wired)
    out = door.run_confirm(root=tmp_path, now=NOW, approval_id=approval_id)
    report = out["report"]
    assert report["status"] == "COMPLETE"
    assert {pid: row["status"] for pid, row in _rows(report).items()} == {
        "live-btc": live_route.EMERGENCY_CLOSED, "live-eth": live_route.EMERGENCY_CLOSED}
    closes = wired.venue.submitted
    assert [(r["symbol"], r["side"], r["type"], r["reduceOnly"], float(r["quantity"])) for r in closes] == [
        ("BTCUSDT", "SELL", "MARKET", True, 0.002), ("ETHUSDT", "BUY", "MARKET", True, 0.05)]
    # PR6b: the HARD halt at the adapter never refuses these; they are the protective shape.
    for request in closes:
        assert live_execution.is_protective_request(request)
        assert live_execution.control_refusal(request, root=tmp_path) is None
    assert list_open_live_positions(tmp_path) == []
    assert sorted(wired.venue.cancelled) == ["sl-live-btc", "sl-live-eth", "tp-live-btc", "tp-live-eth"]
    outcomes = read_live_outcomes(tmp_path)
    assert {o["close_reason"] for o in outcomes} == {live_leg.CLOSE_REASON_EMERGENCY}
    assert wired.purposes == [live_governance.PURPOSE_EMERGENCY_CLOSE] * 2 and len(wired.reports) == 2
    spent = ApprovalStore.default(tmp_path).get(approval_id)
    assert spent["status"] == "CONSUMED"
    assert spent["consumption"]["consumption_ref"] == spent["approved_action_snapshot"]["target_ref"]
    with pytest.raises(MvpRuntimeError) as exc:
        door.run_confirm(root=tmp_path, now=NOW, approval_id=approval_id)
    assert exc.value.reason_code == "ALREADY_CONSUMED"
    assert len(wired.venue.submitted) == 2
    # The halt is not cleared: the runtime stays under it.
    assert wired.control.load().halt_level == HALT_HARD


@requires_local_core
@pytest.mark.parametrize("held,reason", [
    ((_held(_BTC), _held(_ETH, quantity=0.04)), "POSITION_QUANTITY_MISMATCH"),
    ((_held(_BTC), _held(_ETH, side="LONG")), "POSITION_SIDE_MISMATCH"),
])
def test_a_position_the_venue_disagrees_with_is_refused_and_reported(tmp_path, monkeypatch, held, reason):
    wired = _wire(tmp_path, monkeypatch, held=held)
    report = door.run_confirm(root=tmp_path, now=NOW, approval_id=_granted(wired))["report"]
    rows = _rows(report)
    assert rows["live-btc"]["status"] == live_route.EMERGENCY_CLOSED
    assert rows["live-eth"]["status"] == live_route.EMERGENCY_SKIPPED_VENUE_MISMATCH
    assert reason in rows["live-eth"]["reason_codes"] and "not resized" in rows["live-eth"]["detail"]
    assert [r["symbol"] for r in wired.venue.submitted] == ["BTCUSDT"]
    assert report["status"] == "INCOMPLETE"
    assert [p["position_id"] for p in list_open_live_positions(tmp_path)] == ["live-eth"]


@requires_local_core
def test_a_position_the_venue_already_closed_is_left_to_the_scheduler(tmp_path, monkeypatch):
    wired = _wire(tmp_path, monkeypatch, held=(_held(_BTC),))
    report = door.run_confirm(root=tmp_path, now=NOW, approval_id=_granted(wired))["report"]
    assert _rows(report)["live-eth"]["status"] == live_route.EMERGENCY_SKIPPED_CLOSED_AT_VENUE
    assert [r["symbol"] for r in wired.venue.submitted] == ["BTCUSDT"]


@requires_local_core
def test_a_venue_position_the_book_does_not_hold_is_never_touched(tmp_path, monkeypatch):
    """Decision 49: booked positions only. SOLUSDT is at the venue and not on the book."""
    stray = _position(symbol="SOLUSDT", position_id="stray", quantity=1.0)
    wired = _wire(tmp_path, monkeypatch, held=(_held(_BTC), _held(_ETH), _held(stray)))
    report = door.run_confirm(root=tmp_path, now=NOW, approval_id=_granted(wired))["report"]
    assert report["status"] == "COMPLETE" and sorted(_rows(report)) == ["live-btc", "live-eth"]
    assert "SOLUSDT" not in {r["symbol"] for r in wired.venue.submitted}


@requires_local_core
def test_a_booked_position_that_changed_since_the_ask_is_skipped_not_resized(tmp_path, monkeypatch):
    wired = _wire(tmp_path, monkeypatch, held=(_held(_BTC), _held(_ETH, quantity=0.03)))
    approval_id = _granted(wired)
    store = live_route.select_live_position_store(now=NOW, root=tmp_path)
    store.clear_position("ETHUSDT", position_id="live-eth")
    store.save_position({**_ETH, "quantity": 0.03})
    report = door.run_confirm(root=tmp_path, now=NOW, approval_id=approval_id)["report"]
    row = _rows(report)["live-eth"]
    assert row["status"] == live_route.EMERGENCY_SKIPPED_BOOK_CHANGED and "0.03" in row["detail"]
    assert [r["symbol"] for r in wired.venue.submitted] == ["BTCUSDT"]


@requires_local_core
def test_a_position_closed_since_the_ask_is_skipped(tmp_path, monkeypatch):
    wired = _wire(tmp_path, monkeypatch, held=(_held(_BTC),))
    approval_id = _granted(wired)
    live_route.select_live_position_store(now=NOW, root=tmp_path).clear_position("ETHUSDT", position_id="live-eth")
    report = door.run_confirm(root=tmp_path, now=NOW, approval_id=approval_id)["report"]
    assert _rows(report)["live-eth"]["status"] == live_route.EMERGENCY_SKIPPED_NOT_BOOKED
    assert _rows(report)["live-btc"]["status"] == live_route.EMERGENCY_CLOSED


@requires_local_core
def test_a_halt_lifted_between_two_closes_stops_the_rest(tmp_path, monkeypatch):
    wired = _wire(tmp_path, monkeypatch)

    def _resume_after_first(request):
        wired.control.save(ControlState(mode=ACTIVE, updated_by="op", updated_at=NOW, reason="resumed",
                                        trading_armed=True))

    wired.venue.on_submit = _resume_after_first
    report = door.run_confirm(root=tmp_path, now=NOW, approval_id=_granted(wired))["report"]
    rows = _rows(report)
    assert rows["live-btc"]["status"] == live_route.EMERGENCY_CLOSED
    assert rows["live-eth"]["status"] == live_route.EMERGENCY_NOT_ATTEMPTED
    assert live_route.EMERGENCY_CLOSE_HALT_CHANGED in report["live_reason_codes"]
    assert len(wired.venue.submitted) == 1


@requires_local_core
def test_an_unexpected_error_stops_the_rest_and_says_an_order_may_be_out(tmp_path, monkeypatch):
    wired = _wire(tmp_path, monkeypatch, venue=_Venue(submit_raises=RuntimeError("socket gone")))
    approval_id = _granted(wired)
    report = door.run_confirm(root=tmp_path, now=NOW, approval_id=approval_id)["report"]
    rows = _rows(report)
    assert rows["live-btc"]["status"] == live_route.EMERGENCY_INCIDENT
    assert "UNEXPECTED_RuntimeError" in rows["live-btc"]["reason_codes"]
    assert rows["live-eth"]["status"] == live_route.EMERGENCY_NOT_ATTEMPTED
    assert report["status"] == "INCOMPLETE" and _status(tmp_path, approval_id) == "CONSUMED"


@requires_local_core
def test_a_typed_refusal_on_one_position_does_not_stop_the_next(tmp_path, monkeypatch):
    """A refused send (nothing went out) is that position's BLOCKED; the next is judged on its own."""
    wired = _wire(tmp_path, monkeypatch)
    calls = {"n": 0}
    real_submit = wired.venue.submit

    def _first_refused(order_request, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ToolError(live_execution.ORDER_HALTED, "order not sent: scripted")
        return real_submit(order_request, **kw)

    wired.venue.submit = _first_refused
    report = door.run_confirm(root=tmp_path, now=NOW, approval_id=_granted(wired))["report"]
    rows = _rows(report)
    assert rows["live-btc"]["status"] == live_route.EMERGENCY_BLOCKED
    assert live_execution.ORDER_HALTED in rows["live-btc"]["reason_codes"]
    assert rows["live-eth"]["status"] == live_route.EMERGENCY_CLOSED
    assert [p["position_id"] for p in list_open_live_positions(tmp_path)] == ["live-btc"]


# --- the command line ---------------------------------------------------------------------------

def test_the_request_needs_who_and_why(capsys):
    assert door.main(["--request", "--requested-by", "thomas"]) == door.EXIT_USAGE
    assert door.main(["--confirm"]) == door.EXIT_USAGE


def test_show_says_why_no_ask_can_be_made(tmp_path, monkeypatch, capsys):
    _wire(tmp_path, monkeypatch, halt=None)
    assert door.main(["--show", "--root", str(tmp_path)]) == door.EXIT_OK
    out = capsys.readouterr().out
    assert "booked        : 2 position(s)" in out and "cannot be made - the runtime is ACTIVE with no halt" in out


@requires_local_core
def test_a_refused_confirm_exits_blocked_and_names_the_code(tmp_path, monkeypatch, capsys):
    wired = _wire(tmp_path, monkeypatch, gate=lambda **kw: (None, "LIVE_ROUTING_DISABLED"))
    approval_id = _granted(wired)
    monkeypatch.setattr(door.timeutil, "utc_now_iso", lambda: NOW)  # inside the ask's 15 minutes
    assert door.main(["--confirm", "--approval-id", approval_id, "--root", str(tmp_path)]) == door.EXIT_BLOCKED
    assert f"BLOCKED {live_route.EMERGENCY_CLOSE_GATE_CLOSED}" in capsys.readouterr().err


@requires_local_core
def test_a_complete_close_exits_ok_and_prints_each_position(tmp_path, monkeypatch, capsys):
    wired = _wire(tmp_path, monkeypatch)
    approval_id = _granted(wired)
    monkeypatch.setattr(door.timeutil, "utc_now_iso", lambda: NOW)
    assert door.main(["--confirm", "--approval-id", approval_id, "--root", str(tmp_path)]) == door.EXIT_OK
    out = capsys.readouterr().out
    assert "CLOSED                  BTCUSDT LONG 0.002 (live-btc)" in out
    assert "COMPLETE: 2 of 2 closed; the approval is spent" in out


@requires_local_core
def test_an_incomplete_close_exits_blocked(tmp_path, monkeypatch, capsys):
    wired = _wire(tmp_path, monkeypatch, held=(_held(_BTC),))
    approval_id = _granted(wired)
    monkeypatch.setattr(door.timeutil, "utc_now_iso", lambda: NOW)
    assert door.main(["--confirm", "--approval-id", approval_id, "--root", str(tmp_path)]) == door.EXIT_BLOCKED
    assert "INCOMPLETE: 1 of 2 closed" in capsys.readouterr().out
