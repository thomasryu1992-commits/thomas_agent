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

from runtime.mvp_runtime import approval, control, permission, switch_bridge
from runtime.mvp_runtime.approval_store import ApprovalStore
from runtime.mvp_runtime.control import ACTIVE, HALT_HARD, HALT_SOFT, KILLED, PAUSED, ControlState, ControlStore
from runtime.mvp_runtime.crypto import live_route
from runtime.mvp_runtime.errors import ControlBlocked, MvpRuntimeError, ToolError
from runtime.mvp_runtime.store import LEDGER_REL, LedgerStore
from tests import test_mvp_runtime_crypto_emergency_close as operator_close
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

    def ask(self, request, *, now=NOW):
        return switch_bridge.apply_switch(request, control_store=self.control, ledger=self.ledger,
                                          approval_store=self.approvals, now=now, repo_root=self.root)

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


# --- after review (#916) -----------------------------------------------------------------------------

LATER = "2026-09-19T12:05:00Z"      # inside the first ask's 15 minutes
AFTER_EXPIRY = "2026-09-19T12:16:00Z"
_THOMAS = approval.Verification(approved_by="Thomas", method="telegram_private_control_channel",
                                verification_ref="telegram:private_chat:registered-thomas:x")


def _answer(machine, approval_id, *, granted):
    record = machine.approvals.get(approval_id)
    decision = machine.approvals.get_permission_decision(record["permission_decision_id"])
    machine.approvals.append([approval.record_decision(record, decision, granted=granted, verification=_THOMAS,
                                                       reason="r", now=NOW)])


def _message(machine, approval_id) -> str:
    record = machine.approvals.get(approval_id)
    return approval.request_message(record, machine.approvals.get_permission_decision(record["permission_decision_id"]))


@requires_local_core
def test_the_ask_thomas_reads_says_the_assistant_asked_and_its_reason_is_unverified(machine, granted):
    """H1: without these lines the RED ask reached Thomas exactly as one he had minted himself, and the
    decision recorded that he asked."""
    out = machine.ask(dict(REQUEST))
    record = machine.approvals.get(out["approval_id"])
    decision = machine.approvals.get_permission_decision(record["permission_decision_id"])
    assert decision["authority"]["authority_reasons"] == [
        "The assistant (assistant_bridge) asks for the emergency close; its stated reason is its own and "
        "unverified. Only Thomas may authorize it."]
    text = _message(machine, out["approval_id"])
    assert "요청자: 어시스턴트(assistant_bridge) — Thomas나 운영자가 만든 요청이 아닙니다" in text
    assert "어시스턴트가 적은 사유(검증되지 않은 입력): 거래소 장애, 전부 정리" in text


@requires_local_core
def test_the_operators_own_ask_still_says_thomas_asked(machine):
    asked = operator_close.door.run_request(root=machine.root, now=NOW, requested_by="thomas", reason="venue incident")
    record = machine.approvals.get(asked["approval_id"])
    decision = machine.approvals.get_permission_decision(record["permission_decision_id"])
    assert decision["authority"]["authority_reasons"] == [
        "Thomas asks for the emergency close; only Thomas may authorize it."]
    text = _message(machine, asked["approval_id"])
    assert "요청자: thomas (운영자 요청, scripts/emergency_close.py --request)" in text
    assert "요청자가 적은 사유: venue incident" in text and "어시스턴트" not in text


@requires_local_core
@pytest.mark.parametrize("answer", ["pending", "approved"])
def test_no_second_ask_while_one_is_open_whoever_minted_it(machine, granted, answer):
    """M1/L7: a retry under a NEW id (a reply that outlived the client's timeout) or a flood of asks put
    more than one RED ask in front of Thomas for one intent. One stays open; the refusal names it."""
    first = operator_close.door.run_request(root=machine.root, now=NOW, requested_by="thomas", reason="r")
    if answer == "approved":
        _answer(machine, first["approval_id"], granted=True)
    with pytest.raises(ControlBlocked) as exc:
        machine.ask({**REQUEST, "request_id": "hermes-new"}, now=LATER)
    assert exc.value.reason_code == switch_bridge.EMERGENCY_CLOSE_ASK_OPEN
    assert first["approval_id"] in str(exc.value) and answer.upper() in str(exc.value)
    assert [a["approval_id"] for a in machine.approvals.current().values()] == [first["approval_id"]]


@requires_local_core
@pytest.mark.parametrize("closed_by", ["rejected", "expired"])
def test_an_ask_that_is_no_longer_open_does_not_hold_the_next(machine, granted, closed_by):
    first = machine.ask({**REQUEST, "request_id": "hermes-1"})
    if closed_by == "rejected":
        _answer(machine, first["approval_id"], granted=False)
    now = LATER if closed_by == "rejected" else AFTER_EXPIRY
    # The refused id above was never spent, and a fresh one asks.
    out = machine.ask({**REQUEST, "request_id": "hermes-2"}, now=now)
    assert out["reason_code"] == "APPROVAL_REQUIRED" and out["approval_id"] != first["approval_id"]


@requires_local_core
def test_an_audit_failure_after_the_ask_is_stored_is_a_warning_and_the_id_stays_spent(machine, granted, monkeypatch):
    """L1: an untyped exception after the store used to release the id and read "nothing was changed",
    so a same-id retry minted a second ask."""
    def _boom(*a, **kw):
        raise RuntimeError("audit ledger torn")
    monkeypatch.setattr(switch_bridge, "build_approval_request_audit", _boom)
    out = machine.ask({**REQUEST, "request_id": "hermes-3"})
    assert out["reason_code"] == "APPROVAL_REQUIRED"
    assert out["warnings"] == ["the request audit was not written (RuntimeError); the ask stands"]
    again = machine.ask({**REQUEST, "request_id": "hermes-3"}, now=LATER)
    assert again.get("replayed") is True and again["data"]["approval_id"] == out["approval_id"]
    assert len(machine.pending()) == 1


def test_the_close_is_bound_to_the_crypto_domain(machine, granted, monkeypatch):
    """L6: a second domain on this door must not mint the crypto close under its name."""
    monkeypatch.setattr(switch_bridge, "_ALLOWED_DOMAINS", frozenset({"crypto", "prediction"}))
    with pytest.raises(ControlBlocked) as exc:
        machine.ask({**REQUEST, "domain": "prediction"})
    assert exc.value.reason_code == "DOMAIN_EFFECT_MISMATCH" and machine.pending() == []


@pytest.mark.parametrize("disposition,granted_it", [
    ("approval_required_always", True), ("refused", False), (None, False), ("fail_safe_immediate", False),
])
def test_the_policy_grants_the_verb_only_under_the_disposition_the_door_implements(tmp_path, disposition, granted_it):
    """L2: key presence alone used to grant, so `emergency_close: refused` granted it."""
    policy = tmp_path / control.POLICY_REL
    policy.parent.mkdir(parents=True)
    value = "null" if disposition is None else disposition
    policy.write_text(
        "control_channel:\n  assistant_switch:\n    verbs:\n      status: read_only\n"
        f"      disable: fail_safe_immediate\n      enable: approval_required_always\n      emergency_close: {value}\n",
        encoding="utf-8")
    verbs = control.granted_switch_verbs(root=tmp_path)
    assert {"status", "disable", "enable"} <= verbs
    assert ("emergency_close" in verbs) is granted_it


@pytest.mark.parametrize("mode", [KILLED, PAUSED])
def test_a_stopped_runtime_refuses_the_ask_even_under_a_hard_halt_and_keeps_the_id(machine, granted, mode):
    machine.control.save(ControlState(mode=mode, updated_by="tg-1", updated_at=NOW, reason="r",
                                      trading_armed=False, halt_level=HALT_HARD))
    with pytest.raises(ToolError) as exc:
        machine.ask({**REQUEST, "request_id": "hermes-4"})
    assert exc.value.reason_code == live_route.EMERGENCY_CLOSE_NEEDS_HARD_HALT and machine.pending() == []


def test_a_control_file_that_failed_closed_refuses_the_ask(machine, granted):
    machine.control.path.write_text("{torn", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        machine.ask(dict(REQUEST))
    assert exc.value.reason_code == live_route.EMERGENCY_CLOSE_NEEDS_HARD_HALT and machine.pending() == []


@requires_local_core
def test_a_torn_book_refuses_the_ask_and_the_same_id_asks_once_it_reads(machine, granted):
    (book,) = [p for p in machine.root.rglob("BTCUSDT.json")]
    whole = book.read_text(encoding="utf-8")
    book.write_text(whole[: len(whole) // 2], encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        machine.ask({**REQUEST, "request_id": "hermes-5"})
    assert exc.value.reason_code == "LIVE_POSITION_STATE_UNREADABLE" and machine.pending() == []
    book.write_text(whole, encoding="utf-8")
    assert machine.ask({**REQUEST, "request_id": "hermes-5"})["reason_code"] == "APPROVAL_REQUIRED"


@requires_local_core
def test_a_request_id_under_a_changed_reason_is_refused(machine, granted):
    machine.ask({**REQUEST, "request_id": "hermes-6"})
    with pytest.raises(MvpRuntimeError) as exc:
        machine.ask({**REQUEST, "reason": "다른 이유", "request_id": "hermes-6"}, now=LATER)
    assert exc.value.reason_code == "REQUEST_ID_REUSED" and len(machine.pending()) == 1


def test_a_verb_the_policy_does_not_grant_writes_nothing(machine, monkeypatch):
    monkeypatch.setattr(control, "granted_switch_verbs", lambda root=None: frozenset({"status", "disable", "enable"}))
    before = sorted(p for p in machine.root.rglob("*") if p.is_file())
    with pytest.raises(ControlBlocked):
        machine.ask({**REQUEST, "request_id": "hermes-7"})
    assert sorted(p for p in machine.root.rglob("*") if p.is_file()) == before


@requires_local_core
def test_enable_refuses_an_emergency_close_grant(machine, granted):
    out = machine.ask(dict(REQUEST))
    _answer(machine, out["approval_id"], granted=True)
    with pytest.raises(ControlBlocked) as exc:
        machine.ask({"command": "enable", "approval_id": out["approval_id"], "reason": "r", "domain": "crypto"})
    assert exc.value.reason_code == "TARGET_NOT_SWITCH"
    assert machine.approvals.get(out["approval_id"])["status"] == "APPROVED"


# The spend, end to end: the ask this door mints is the operator's ask. Wired like the operator's own
# tests (a scripted venue, the real book and ledger in the temp root).

@requires_local_core
def test_the_door_minted_ask_is_spent_by_the_operators_confirm(tmp_path, monkeypatch, granted):
    wired = operator_close._wire(tmp_path, monkeypatch)
    out = _Machine(tmp_path).ask(dict(REQUEST))
    operator_close._approve(tmp_path, out["approval_id"])
    report = operator_close.door.run_confirm(root=tmp_path, now=NOW, approval_id=out["approval_id"])["report"]
    assert report["status"] == "COMPLETE"
    assert [(r["symbol"], r["type"], r["reduceOnly"]) for r in wired.venue.submitted] == [
        ("BTCUSDT", "MARKET", True), ("ETHUSDT", "MARKET", True)]
    assert ApprovalStore.default(tmp_path).get(out["approval_id"])["status"] == "CONSUMED"
    assert ControlStore(tmp_path).load().halt_level == HALT_HARD


@requires_local_core
def test_a_replayed_ask_whose_halt_moved_is_refused_at_the_confirm(tmp_path, monkeypatch, granted):
    wired = operator_close._wire(tmp_path, monkeypatch)
    machine = _Machine(tmp_path)
    out = machine.ask({**REQUEST, "request_id": "hermes-8"})
    operator_close._approve(tmp_path, out["approval_id"])
    machine.control.save(ControlState(mode=ACTIVE, updated_by="tg-2", updated_at=LATER, reason="again",
                                      trading_armed=False, halt_level=HALT_HARD))
    replay = machine.ask({**REQUEST, "request_id": "hermes-8"}, now=LATER)
    assert replay.get("replayed") is True and replay["data"]["approval_id"] == out["approval_id"]
    operator_close._refused(wired, out["approval_id"], "EMERGENCY_CLOSE_HALT_CHANGED")
