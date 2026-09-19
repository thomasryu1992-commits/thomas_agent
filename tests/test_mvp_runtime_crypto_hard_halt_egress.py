"""The HARD halt at the order adapter (crypto PR6b, Thomas decision 47, 2026-09-19).

A HARD halt, or a PAUSED or KILLED runtime, refuses at egress every order that is neither
reduceOnly nor closePosition — mainnet and testnet — and never an exit or a protection. The SOFT
halt is refused where entries are decided (`trading_allowed`), not here. **Nothing here opens a
socket**: ``urlopen`` is intercepted, and a refusal is proved by the interceptor never being called.
"""

from __future__ import annotations

import json

import pytest
from tests._helpers import make_gate_authorization

from runtime.mvp_runtime import control, safety_gate
from runtime.mvp_runtime.control import ACTIVE, KILLED, PAUSED, ControlState, ControlStore
from runtime.mvp_runtime.crypto import live_execution as lx
from runtime.mvp_runtime.crypto import live_order, testnet_execution
from runtime.mvp_runtime.crypto.live_order import build_live_order_intent, enrich_order_identity
from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_FLAGS, LIVE_TRADING_PROVIDER_ID
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-09-19T00:00:00Z"
_LIVE_AUTH = make_gate_authorization(flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID)


def _intent(*, reduce_only=False, **kw):
    intent = build_live_order_intent(
        {"direction": "LONG"}, symbol="BTCUSDT", quantity=0.001, notional_usdt=55.0, now=NOW,
        reduce_only=reduce_only, close_reason="stop_loss" if reduce_only else None,
    )
    intent = dict(enrich_order_identity(intent))
    intent.update(kw)
    return intent


ENTRY = lx.build_order_request(_intent())
CLOSE = lx.build_order_request(_intent(reduce_only=True))
STOP = lx.build_order_request(_intent(order_type_exchange="STOP_MARKET", stop_price=59000.0,
                                      close_position=True))
TARGET = lx.build_order_request(_intent(reduce_only=True, order_type_exchange="LIMIT", price=61000.0,
                                        time_in_force="GTC"))


def _state(tmp_path, **kw):
    store = ControlStore(tmp_path)
    store.save(ControlState(**{"mode": ACTIVE, "updated_by": "op", "updated_at": NOW, "reason": "r",
                               "trading_armed": True, **kw}))
    return store


HARD = dict(trading_armed=False, halt_level=control.HALT_HARD)
SOFT = dict(trading_armed=False, halt_level=control.HALT_SOFT)


class _Response:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def sent(monkeypatch):
    """Every request that reached the transport, by path."""
    monkeypatch.setenv(lx.ORDER_API_KEY_ENV, "test-key")
    monkeypatch.setenv(lx.ORDER_API_SECRET_ENV, "test-secret")
    monkeypatch.setenv(testnet_execution.TESTNET_API_KEY_ENV, "test-key")
    monkeypatch.setenv(testnet_execution.TESTNET_API_SECRET_ENV, "test-secret")
    paths: list[str] = []

    def fake_urlopen(request, timeout=None):
        paths.append(request.full_url.split("?")[0])
        return _Response({"orderId": 1, "algoId": 2, "status": "NEW"})

    monkeypatch.setattr(lx.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(testnet_execution.urllib.request, "urlopen", fake_urlopen)
    return paths


def _live(tmp_path):
    return lx.BinanceFuturesOrderAdapter(authorization=_LIVE_AUTH, root=tmp_path)


def _testnet(tmp_path, monkeypatch):
    monkeypatch.setenv(testnet_execution.TESTNET_TRADING_ENV, testnet_execution.REAL_TESTNET_TRADING)
    auth = safety_gate.env_only_authorization(
        flags=testnet_execution.TESTNET_TRADING_FLAGS, provider_id=testnet_execution.TESTNET_PROVIDER_ID,
        env_var=testnet_execution.TESTNET_TRADING_ENV, opt_in_value=testnet_execution.REAL_TESTNET_TRADING)
    return testnet_execution.BinanceTestnetOrderAdapter(authorization=auth, root=tmp_path)


# --- the shape every halt lets through ------------------------------------------------------

def test_the_protective_shapes_are_reduce_only_and_close_position():
    assert [lx.is_protective_request(r) for r in (ENTRY, CLOSE, STOP, TARGET)] == [False, True, True, True]
    assert lx.is_protective_request({"reduceOnly": "true"}) is False, "only the built request's own spelling"


# --- mainnet ---------------------------------------------------------------------------------

def test_an_entry_is_refused_at_the_adapter_under_a_hard_halt_and_nothing_is_sent(tmp_path, sent):
    _state(tmp_path, **HARD)
    with pytest.raises(ToolError) as exc:
        _live(tmp_path).submit(ENTRY)
    assert exc.value.reason_code == lx.ORDER_HALTED
    assert "HARD halt" in str(exc.value)
    assert sent == []


@pytest.mark.parametrize("request_", [CLOSE, STOP, TARGET], ids=["close", "stop", "target"])
def test_exits_and_protection_go_out_under_a_hard_halt(tmp_path, sent, request_):
    _state(tmp_path, **HARD)
    _live(tmp_path).submit(request_)
    assert len(sent) == 1


def test_a_reduce_only_close_goes_out_under_a_corrupt_store(tmp_path, sent):
    """The risk the investigation named: failing closed on the store must never strand a position."""
    store = ControlStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{damaged", encoding="utf-8")
    adapter = _live(tmp_path)
    adapter.submit(CLOSE)
    adapter.submit(STOP)
    assert len(sent) == 2
    with pytest.raises(ToolError) as exc:
        adapter.submit(ENTRY)
    assert exc.value.reason_code == lx.ORDER_HALTED and len(sent) == 2
    # A corrupt store reads KILLED under a HARD halt; the refusal names the stop (review of #912).
    assert "the runtime is KILLED" in str(exc.value) and "fail-closed" in str(exc.value)


def test_an_exit_never_reads_the_control_state(tmp_path, sent, monkeypatch):
    def unreadable(self):
        raise RuntimeError("scripted")

    monkeypatch.setattr(ControlStore, "load", unreadable)
    adapter = _live(tmp_path)
    adapter.submit(CLOSE)
    assert len(sent) == 1
    with pytest.raises(ToolError) as exc:
        adapter.submit(ENTRY)
    assert exc.value.reason_code == lx.ORDER_HALTED and "could not be read" in str(exc.value)
    assert len(sent) == 1


@pytest.mark.parametrize("mode", [KILLED, PAUSED])
def test_a_stopped_runtime_refuses_an_entry_at_the_adapter(tmp_path, sent, mode):
    """A stop refuses at least what a HARD halt does."""
    _state(tmp_path, mode=mode, trading_armed=False)
    with pytest.raises(ToolError) as exc:
        _live(tmp_path).submit(ENTRY)
    assert exc.value.reason_code == lx.ORDER_HALTED and mode in str(exc.value)
    assert sent == []


@pytest.mark.parametrize("kw", [SOFT, dict(trading_armed=False), dict(trading_armed=True)],
                         ids=["soft", "bare-disarm", "armed"])
def test_the_soft_halt_is_refused_where_entries_are_decided_not_here(tmp_path, sent, kw):
    """SOFT stops new entries at the entry gate (`trading_allowed`); HARD also stops them where they
    leave. That is the line between the two levels."""
    _state(tmp_path, **kw)
    _live(tmp_path).submit(ENTRY)
    assert len(sent) == 1


def test_the_halt_is_read_at_every_submit(tmp_path, sent):
    """An adapter built before the halt must see it."""
    store = _state(tmp_path)
    adapter = _live(tmp_path)
    adapter.submit(ENTRY)
    store.save(ControlState(mode=ACTIVE, updated_by="op", updated_at=NOW, reason="r", **HARD))
    with pytest.raises(ToolError):
        adapter.submit(ENTRY)
    store.save(ControlState(mode=ACTIVE, updated_by="op", updated_at=NOW, reason="resumed", trading_armed=True))
    adapter.submit(ENTRY)
    assert len(sent) == 2


def test_validation_and_cancels_are_not_refused_under_a_hard_halt(tmp_path, sent):
    """`/order/test` creates nothing, and a cancel removes one named order; neither adds exposure."""
    _state(tmp_path, **HARD)
    adapter = _live(tmp_path)
    adapter.validate_order(ENTRY)
    adapter.cancel_order("BTCUSDT", "TAI_X")
    assert [p.rsplit("/", 1)[-1] for p in sent] == ["test", "order"]


def test_the_refusal_is_nothing_sent_and_not_an_api_failure(tmp_path, sent):
    _state(tmp_path, **HARD)
    with pytest.raises(ToolError) as exc:
        _live(tmp_path).submit(ENTRY)
    assert exc.value.reason_code in lx.NOTHING_SENT_ERRORS
    assert lx.submit_may_have_landed(exc.value.reason_code, str(exc.value)) is False
    assert live_order.api_error_counts(exc.value) is False
    assert not (exc.value.data or {}).get("venue_code") and not (exc.value.data or {}).get("http_status")


def test_the_selector_hands_the_adapter_its_root(tmp_path, monkeypatch, sent):
    monkeypatch.setenv(lx.LIVE_TRADING_ENV, lx.REAL_LIVE_TRADING)
    adapter = lx.select_order_adapter(now=NOW, root=tmp_path)
    assert isinstance(adapter, lx.BinanceFuturesOrderAdapter)
    _state(tmp_path, **HARD)
    with pytest.raises(ToolError) as exc:
        adapter.submit(ENTRY)
    assert exc.value.reason_code == lx.ORDER_HALTED and sent == []


# --- submit_and_reconcile and the entry leg --------------------------------------------------

class _Refusing:
    tool_id, tool_version = "fake", "0"

    def __init__(self):
        self.fetched = 0

    def submit(self, order_request, *, timeout_seconds=10):
        raise ToolError(lx.ORDER_HALTED, "order not sent: scripted")

    def fetch_order(self, *a, **k):
        self.fetched += 1
        return None


def test_submit_and_reconcile_reports_the_refusal_as_nothing_sent_and_asks_the_venue_nothing():
    from tests._helpers import FakeSnapshotStore, approved_snapshot

    intent, snapshot = approved_snapshot(_intent())
    adapter = _Refusing()
    with pytest.raises(lx.SubmitRefused) as exc:
        lx.submit_and_reconcile(intent, adapter=adapter, guard_verdict={"approved": True}, now=NOW,
                                risk_snapshot=snapshot, snapshot_store=FakeSnapshotStore())
    assert exc.value.reason_code == lx.ORDER_HALTED
    assert adapter.fetched == 0


# --- testnet ----------------------------------------------------------------------------------

def test_the_testnet_entry_is_refused_under_a_hard_halt(tmp_path, sent, monkeypatch):
    _state(tmp_path, **HARD)
    adapter = _testnet(tmp_path, monkeypatch)
    with pytest.raises(ToolError) as exc:
        adapter.submit(ENTRY)
    assert exc.value.reason_code == lx.ORDER_HALTED and sent == []
    adapter.submit(CLOSE)
    assert len(sent) == 1, "the rehearsal's own exit still goes out"


def test_the_soft_halt_keeps_the_testnet_rehearsal_running(tmp_path, sent, monkeypatch):
    _state(tmp_path, **SOFT)
    _testnet(tmp_path, monkeypatch).submit(ENTRY)
    assert len(sent) == 1


def test_the_testnet_selector_hands_the_adapter_its_root(tmp_path, monkeypatch, sent):
    monkeypatch.setenv(testnet_execution.TESTNET_TRADING_ENV, testnet_execution.REAL_TESTNET_TRADING)
    adapter = testnet_execution.select_testnet_order_adapter(now=NOW, root=tmp_path)
    _state(tmp_path, **HARD)
    with pytest.raises(ToolError):
        adapter.submit(ENTRY)
    assert sent == []


def test_the_testnet_guard_refuses_the_entry_under_a_hard_halt_and_not_the_exit():
    from runtime.mvp_runtime.crypto import execution_stage as es

    stage = es.StageStatus(stage="SIGNED_TESTNET", valid=True, reason_code=None, recorded_stage="SIGNED_TESTNET",
                           stage_id="s", record_sha256="sha256:" + "5" * 64, approval_id="a")
    kwargs = dict(gate_open=True, runtime_active=True, manual_kill_switch=False, submitted_today=0,
                  execution_stage=stage, hard_halt=True)
    entry = testnet_execution.evaluate_testnet_order_guard(
        {"symbol": "BTCUSDT", "quantity": 0.001, "order_notional_usdt": 50.0, "reduce_only": False}, **kwargs)
    assert any("HARD halt" in b for b in entry["blocks"])
    assert {c["check"]: c["ok"] for c in entry["checks"]}["runtime_active"] is False
    exit_ = testnet_execution.evaluate_testnet_order_guard(
        {"symbol": "BTCUSDT", "quantity": 0.001, "order_notional_usdt": 0.0, "reduce_only": True}, **kwargs)
    assert not any("HARD halt" in b for b in exit_["blocks"])
    # A closePosition stop is protective too, spelled as the adapter spells it (review of #912).
    from runtime.mvp_runtime.crypto import live_leg

    stop = live_leg.build_bracket_intent(symbol="BTCUSDT", leg="SL", side="SELL", price=49000.0,
                                         working_type="MARK_PRICE", position_seed="seed")
    assert stop["close_position"] is True and stop["reduce_only"] is False
    assert lx.is_protective_request(lx.build_order_request(stop))
    guarded = testnet_execution.evaluate_testnet_order_guard({**stop, "order_notional_usdt": 0.0}, **kwargs)
    assert not any("HARD halt" in b for b in guarded["blocks"])


def test_the_sealed_testnet_facts_carry_the_hard_halt():
    """The pre-order snapshot seals what the guard judged: a HARD refusal must not read as a runtime
    that was ACTIVE and nothing else (review of #912)."""
    from runtime.mvp_runtime.crypto import execution_stage as es
    from scripts import run_signed_testnet_cycle as door

    stage = es.StageStatus(stage="SIGNED_TESTNET", valid=True, reason_code=None, recorded_stage="SIGNED_TESTNET",
                           stage_id="s", record_sha256="sha256:" + "5" * 64, approval_id="a")
    intent = door._entry_intent(symbol="BTCUSDT", quantity=0.001, price=50000.0, now=NOW)
    for hard in (True, False):
        kwargs = dict(gate_open=True, runtime_active=True, manual_kill_switch=False, submitted_today=0,
                      execution_stage=stage, hard_halt=hard)
        snap = testnet_execution.gate_testnet_order(intent, expected_intent=intent, guard_kwargs=kwargs,
                                                    stage=stage, cycle_id="c", now=NOW, decided_at=NOW)
        assert snap["facts"]["hard_halt"] is hard
        assert ("runtime_active" in snap["failed_checks"]) is hard


def test_the_testnet_door_plans_no_cycle_under_a_hard_halt(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto import execution_stage as es
    from scripts import run_signed_testnet_cycle as door

    stage = es.StageStatus(stage="SIGNED_TESTNET", valid=True, reason_code=None, recorded_stage="SIGNED_TESTNET",
                           stage_id="s", record_sha256="sha256:" + "5" * 64, approval_id="a")
    monkeypatch.setattr(door.testnet, "select_testnet_order_adapter",
                        lambda **kw: testnet_execution.DryRunTestnetOrderAdapter())
    monkeypatch.setattr(door, "_price", lambda symbol, **kw: 50000.0)
    monkeypatch.setattr(door, "resolve_execution_stage", lambda root=None, **kw: stage)
    _state(tmp_path, **SOFT)
    soft = door.plan_cycle(symbol="BTCUSDT", quantity=0.001, root=tmp_path, now=NOW)
    assert not any("HARD halt" in b for b in soft["verdict"]["blocks"])
    _state(tmp_path, **HARD)
    hard = door.plan_cycle(symbol="BTCUSDT", quantity=0.001, root=tmp_path, now=NOW)
    assert any("HARD halt" in b for b in hard["verdict"]["blocks"])


def test_the_status_line_says_what_the_hard_halt_refuses_at_the_adapter():
    text = control.status_lines(ControlState(mode=ACTIVE, **HARD))
    assert "every order that is neither reduceOnly nor closePosition, testnet included" in text


def test_the_testnet_door_names_the_hard_halt_even_without_a_price(tmp_path, monkeypatch):
    """The posture is judged when the reference price cannot be read, too (review of #877)."""
    from runtime.mvp_runtime.crypto import execution_stage as es
    from runtime.mvp_runtime.errors import MvpRuntimeError
    from scripts import run_signed_testnet_cycle as door

    def no_price(symbol, **kw):
        raise MvpRuntimeError("TESTNET_PRICE_UNREADABLE", "scripted")

    stage = es.StageStatus(stage="SIGNED_TESTNET", valid=True, reason_code=None, recorded_stage="SIGNED_TESTNET",
                           stage_id="s", record_sha256="sha256:" + "5" * 64, approval_id="a")
    monkeypatch.setattr(door.testnet, "select_testnet_order_adapter",
                        lambda **kw: testnet_execution.DryRunTestnetOrderAdapter())
    monkeypatch.setattr(door, "_price", no_price)
    monkeypatch.setattr(door, "resolve_execution_stage", lambda root=None, **kw: stage)
    _state(tmp_path, **HARD)
    planned = door.plan_cycle(symbol="BTCUSDT", quantity=0.001, root=tmp_path, now=NOW)
    assert any("HARD halt" in b for b in planned["verdict"]["blocks"])
