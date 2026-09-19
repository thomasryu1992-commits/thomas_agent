"""The assistant asks for the emergency close, and only asks (crypto PR6e, Thomas decision 49).

The switch door's `emergency_close` verb mints the ask `scripts.emergency_close --request` mints —
every booked live position, closed at market and reduceOnly, under the HARD halt in effect — with the
assistant as the requester. What must hold:

- **dormant until the policy names it:** it refuses by name while the committed policy's
  `control_channel.assistant_switch.verbs` does not list it, and asks nothing;
- **it never spends:** an `approval_id` beside it is refused, and nothing it does reads a venue or
  sends an order; the spend is the operator's `--confirm` in the scheduler container;
- **the same ask as the operator's:** the same content, scope, risk and 15 minutes, bound to the HARD
  halt's `stop_ref` and the book, and refused by the same codes without them;
- **one ask per intent:** a retried `request_id` answers from the record.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import control, permission, switch_bridge
from runtime.mvp_runtime.approval_store import ApprovalStore
from runtime.mvp_runtime.control import ACTIVE, HALT_HARD, HALT_SOFT, ControlState, ControlStore
from runtime.mvp_runtime.crypto import live_route
from runtime.mvp_runtime.errors import ControlBlocked, ToolError
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore
from tests._helpers import requires_local_core

NOW = "2026-09-19T12:00:00Z"


def _position(symbol, position_id, direction, quantity):
    return {
        "stage": "live", "status": "OPEN", "symbol": symbol, "direction": direction, "quantity": quantity,
        "entry_price": 60000.0, "notional_usdt": round(quantity * 60000.0, 2), "stop_loss": 59000.0,
        "take_profit": 62000.0, "risk": 2.0, "opened_at_utc": "2026-09-19T08:00:00Z",
        "entry_exchange_order_id": f"venue-{position_id}", "position_id": position_id,
        "entry_quote_usdt": 120.0, "stop_client_order_id": f"sl-{position_id}",
        "take_profit_client_order_id": f"tp-{position_id}", "strategy_id": "S001",
    }


class _Machine:
    def __init__(self, root):
        self.root = root
        self.control = ControlStore(root)
        self.ledger = LedgerStore(root / LEDGER_REL)
        self.approvals = ApprovalStore.default(root)

    def ask(self, request):
        return switch_bridge.apply_switch(request, control_store=self.control, ledger=self.ledger,
                                          approval_store=self.approvals, now=NOW, repo_root=self.root)

    def pending(self):
        return [a for a in self.approvals.pending()]


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A HARD halt with the runtime ACTIVE and two positions on the book. The venue is never read: the
    live gate and the account read fail the test if anything reaches them."""
    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    monkeypatch.setattr(live_route, "select_live_gate", lambda **kw: pytest.fail("the ask reads no venue"))
    monkeypatch.setattr(live_route, "read_account", lambda **kw: pytest.fail("the ask reads no account"))
    store = live_route.select_live_position_store(now=NOW, root=tmp_path)
    store.save_position(_position("BTCUSDT", "live-btc", "LONG", 0.002))
    store.save_position(_position("ETHUSDT", "live-eth", "SHORT", 0.05))
    m = _Machine(tmp_path)
    m.control.save(ControlState(mode=ACTIVE, updated_by="tg-1", updated_at=NOW, reason="venue incident",
                                trading_armed=False, halt_level=HALT_HARD))
    return m


@pytest.fixture
def granted(monkeypatch):
    """The committed policy lists the verb (what policy 1.5.2 writes)."""
    monkeypatch.setattr(control, "granted_switch_verbs",
                        lambda root=None: frozenset({"status", "disable", "enable", "emergency_close"}))


REQUEST = {"command": "emergency_close", "reason": "거래소 장애, 전부 정리", "domain": "crypto"}


def test_the_verb_refuses_by_name_while_the_policy_does_not_list_it(machine, monkeypatch):
    """Policy 1.5.1 lists status, disable and enable (stubbed here, so this holds after 1.5.2 too; the
    bump's own tests read the committed policy). Until the policy lists it the verb is dormant."""
    monkeypatch.setattr(control, "granted_switch_verbs",
                        lambda root=None: frozenset({"status", "disable", "enable"}))
    with pytest.raises(ControlBlocked) as exc:
        machine.ask(dict(REQUEST))
    assert exc.value.reason_code == control.VERB_NOT_GRANTED
    assert "scripts.emergency_close --request" in str(exc.value)
    assert machine.pending() == []


def test_an_unreadable_policy_grants_it_nothing_and_never_takes_disable_away(machine, monkeypatch, tmp_path):
    monkeypatch.setattr(control, "_repo_root", lambda: tmp_path / "no-such-repo")
    assert control.granted_switch_verbs() == frozenset()
    with pytest.raises(ControlBlocked):
        machine.ask(dict(REQUEST))
    out = machine.ask({"command": "disable", "mode": "kill", "reason": "r", "domain": "crypto"})
    assert out["changed"] is True and machine.control.load().mode == control.KILLED


@requires_local_core
def test_granted_it_mints_the_operators_ask_with_the_assistant_as_the_requester(machine, granted):
    out = machine.ask(dict(REQUEST))
    assert (out["ok"], out["reason_code"], out["action"]) == (False, "APPROVAL_REQUIRED", "emergency_close")
    record = machine.approvals.get(out["approval_id"])
    snapshot = record["approved_action_snapshot"]
    assert record["status"] == "PENDING" and record["validity"]["expires_at"] == "2026-09-19T12:15:00Z"
    assert snapshot["permission_scope"] == permission.EMERGENCY_CLOSE_PERMISSION_SCOPE
    assert snapshot["target_ref"].startswith(permission.EMERGENCY_CLOSE_TARGET_PREFIX)
    assert snapshot["normalized_parameters"] == {
        "halt_ref": switch_bridge.stop_ref(machine.control.load()),
        "halt_summary": "the HARD halt placed by tg-1 at 2026-09-19T12:00:00Z, stated reason: venue incident",
        "positions": [
            {"position_id": "live-btc", "symbol": "BTCUSDT", "direction": "LONG", "quantity": "0.002"},
            {"position_id": "live-eth", "symbol": "ETHUSDT", "direction": "SHORT", "quantity": "0.05"},
        ],
        "requested_by": switch_bridge.ASSISTANT_ACTOR, "reason": "거래소 장애, 전부 정리",
    }
    decision = machine.approvals.get_permission_decision(record["permission_decision_id"])
    assert decision["risk"]["risk_level"] == "RED"
    # The reply says who does what next, and that nothing was closed.
    assert out["approve_with"] == f"/approve {out['approval_id']}"
    assert out["confirm_with"].endswith(f"--confirm --approval-id {out['approval_id']}")
    assert "thomas-scheduler" in out["confirm_with"] and "nothing has been closed" in out["reason"]
    assert [p["position_id"] for p in out["data"]["positions"]] == ["live-btc", "live-eth"]
    # Nothing else moved: the halt is still the one bound, the book still holds both.
    assert machine.control.load().halt_level == HALT_HARD
    assert len(live_route.list_open_live_positions(machine.root)) == 2


@pytest.mark.parametrize("extra", [{"approval_id": "approval_x"}, {"mode": "hard"}, {"scope": "trading"}])
def test_it_only_asks_and_refuses_what_would_mean_more(machine, granted, extra):
    """An `approval_id` beside it is a caller believing this door can close positions. It cannot: the
    spend is the operator's `--confirm` in the scheduler container."""
    with pytest.raises(ControlBlocked) as exc:
        machine.ask({**REQUEST, **extra})
    assert exc.value.reason_code == "ARGUMENT_NOT_ACCEPTED" and "--confirm" in str(exc.value)
    assert machine.pending() == []


@pytest.mark.parametrize("halt,code", [(HALT_SOFT, live_route.EMERGENCY_CLOSE_NEEDS_HARD_HALT),
                                       (None, live_route.EMERGENCY_CLOSE_NEEDS_HARD_HALT)])
def test_it_refuses_by_the_operators_codes_without_the_hard_halt(machine, granted, halt, code):
    machine.control.save(ControlState(mode=ACTIVE, updated_by="tg-1", updated_at=NOW, reason="r",
                                      trading_armed=False, halt_level=halt))
    with pytest.raises(ToolError) as exc:
        machine.ask(dict(REQUEST))
    assert exc.value.reason_code == code and machine.pending() == []


def test_it_refuses_with_nothing_booked(tmp_path, monkeypatch, granted):
    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    m = _Machine(tmp_path)
    m.control.save(ControlState(mode=ACTIVE, updated_by="tg-1", updated_at=NOW, reason="r",
                                trading_armed=False, halt_level=HALT_HARD))
    with pytest.raises(ToolError) as exc:
        m.ask(dict(REQUEST))
    assert exc.value.reason_code == live_route.EMERGENCY_CLOSE_NOTHING_BOOKED and m.pending() == []


@requires_local_core
def test_a_retried_request_id_answers_from_the_record_and_mints_no_second_ask(machine, granted):
    first = machine.ask({**REQUEST, "request_id": "hermes-1"})
    again = machine.ask({**REQUEST, "request_id": "hermes-1"})
    assert again.get("replayed") is True and again["data"]["approval_id"] == first["approval_id"]
    assert [a["approval_id"] for a in machine.pending()] == [first["approval_id"]]


@requires_local_core
def test_a_refused_ask_does_not_spend_its_request_id(machine, granted):
    """Nothing was minted, so the id names nothing: the same id may ask again once the halt is right."""
    machine.control.save(ControlState(mode=ACTIVE, updated_by="tg-1", updated_at=NOW, reason="r",
                                      trading_armed=False, halt_level=HALT_SOFT))
    with pytest.raises(ToolError):
        machine.ask({**REQUEST, "request_id": "hermes-2"})
    machine.control.save(ControlState(mode=ACTIVE, updated_by="tg-1", updated_at=NOW, reason="venue incident",
                                      trading_armed=False, halt_level=HALT_HARD))
    out = machine.ask({**REQUEST, "request_id": "hermes-2"})
    assert out["reason_code"] == "APPROVAL_REQUIRED" and not out.get("replayed")
