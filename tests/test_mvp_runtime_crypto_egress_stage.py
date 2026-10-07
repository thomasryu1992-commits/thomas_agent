"""The execution stage at the mainnet adapter's egress (R2, Thomas 2026-10-07).

The decision layer judges the stage (`evaluate_live_order_guard`, `pre_order_gate`). The H0 review
found the adapter itself did not: a caller that reached ``submit`` without the guard would have met
only the control state. ``stage_refusal`` closes that, by exposure semantics rather than by stage
alone:

- an order whose built shape cannot add exposure (``reduceOnly`` true, or a Close-All
  ``closePosition``) is never refused here and never reads the stage, so a position can always be
  reduced (the close path is stage-free by design, EXECUTION_STAGE_V0.1);
- every other order — an entry, an increase, or a shape that does not prove it reduces — needs a
  stage that admits a live entry (LIVE_AUTONOMOUS). PAPER refuses it, and so does an unreadable stage.

R2 replaces no gate: the control state, the guard verdict and the rest still apply at every stage.
**Nothing here opens a socket to a venue**: ``urlopen`` is intercepted, and a refusal is proved by the
interceptor never being called.
"""

from __future__ import annotations

import json

import pytest
from tests._helpers import FakeSnapshotStore, approved_snapshot, gate_stage, make_gate_authorization

from runtime.mvp_runtime import control
from runtime.mvp_runtime.control import ACTIVE, KILLED, ControlState, ControlStore
from runtime.mvp_runtime.crypto import execution_stage as es
from runtime.mvp_runtime.crypto import live_execution as lx
from runtime.mvp_runtime.crypto.live_order import build_live_order_intent, enrich_order_identity
from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_FLAGS, LIVE_TRADING_PROVIDER_ID
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-10-07T17:00:00Z"
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
# A resting entry: a LIMIT that is not reduceOnly adds exposure when it fills.
LIMIT_ENTRY = lx.build_order_request(_intent(order_type_exchange="LIMIT", price=59000.0, time_in_force="GTC"))
CLOSE = lx.build_order_request(_intent(reduce_only=True))
STOP = lx.build_order_request(_intent(order_type_exchange="STOP_MARKET", stop_price=59000.0, close_position=True))
TARGET = lx.build_order_request(_intent(reduce_only=True, order_type_exchange="LIMIT", price=61000.0,
                                        time_in_force="GTC"))


def _stage(name):
    return es.StageStatus(stage=name, valid=True, reason_code=None, recorded_stage=name,
                          stage_id="stage_test", record_sha256="sha256:" + "5" * 64)


PAPER = _stage("PAPER")
SIGNED_TESTNET = _stage("SIGNED_TESTNET")


@pytest.fixture
def stage_reads(monkeypatch):
    """Set what the adapter's egress reads as the stage."""
    def _set(status):
        monkeypatch.setattr(lx, "resolve_execution_stage", lambda root=None, **_kw: status)
    return _set


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
    monkeypatch.setenv(lx.ORDER_API_KEY_ENV, "test-key")
    monkeypatch.setenv(lx.ORDER_API_SECRET_ENV, "test-secret")
    calls: list[tuple[str, str]] = []

    def fake_urlopen(request, timeout=None):
        calls.append((request.get_method(), request.full_url.split("?")[0]))
        return _Response({"orderId": 1, "algoId": 2, "status": "NEW"})

    monkeypatch.setattr(lx.urllib.request, "urlopen", fake_urlopen)
    return calls


def _active(tmp_path, **kw):
    ControlStore(tmp_path).save(ControlState(**{"mode": ACTIVE, "updated_by": "op", "updated_at": NOW,
                                                "reason": "r", "trading_armed": True, **kw}))


def _live(tmp_path):
    return lx.BinanceFuturesOrderAdapter(authorization=_LIVE_AUTH, root=tmp_path)


# --- PAPER: exposure-increasing orders are refused at egress ---------------------------------

@pytest.mark.parametrize("request_", [ENTRY, LIMIT_ENTRY], ids=["market-entry", "limit-entry"])
@pytest.mark.parametrize("status", [PAPER, SIGNED_TESTNET], ids=["paper", "signed-testnet"])
def test_an_exposure_adding_order_is_refused_below_live_and_nothing_is_sent(
        tmp_path, sent, stage_reads, request_, status):
    _active(tmp_path)
    stage_reads(status)
    with pytest.raises(ToolError) as exc:
        _live(tmp_path).submit(request_)
    assert exc.value.reason_code == lx.ORDER_STAGE_REFUSED
    assert f"reads {status.stage}" in str(exc.value) and "LIVE_AUTONOMOUS" in str(exc.value)
    assert sent == []


@pytest.mark.parametrize("ambiguous", [
    {**ENTRY, "reduceOnly": "true"},          # a caller's spelling, not the built request's
    {**ENTRY, "reduceOnly": 1},
    {k: v for k, v in ENTRY.items() if k != "reduceOnly"},
    {**ENTRY, "closePosition": True},         # the venue's flag is the string "true"
    {**ENTRY, "closePosition": "TRUE"},
    {},
], ids=["reduce-only-string", "reduce-only-int", "no-reduce-only", "close-position-bool",
        "close-position-upper", "empty"])
def test_a_shape_that_does_not_prove_it_reduces_is_refused(tmp_path, sent, stage_reads, ambiguous):
    """UNKNOWN -> REFUSE: only the built request's own protective spelling is let through."""
    _active(tmp_path)
    stage_reads(PAPER)
    with pytest.raises(ToolError) as exc:
        _live(tmp_path).submit(ambiguous)
    assert exc.value.reason_code == lx.ORDER_STAGE_REFUSED
    assert sent == []


@pytest.mark.parametrize("reason", [es.STAGE_RECORD_MISSING, es.STAGE_RECORD_TAMPERED])
def test_an_unreadable_stage_refuses(tmp_path, sent, stage_reads, reason):
    _active(tmp_path)
    stage_reads(es.StageStatus(stage="READ_ONLY", valid=False, reason_code=reason))
    with pytest.raises(ToolError) as exc:
        _live(tmp_path).submit(ENTRY)
    assert exc.value.reason_code == lx.ORDER_STAGE_REFUSED and reason in str(exc.value)
    assert sent == []


def test_a_stage_read_that_raises_refuses(tmp_path, sent, monkeypatch):
    _active(tmp_path)

    def _boom(*_a, **_k):
        raise RuntimeError("disk")

    monkeypatch.setattr(lx, "resolve_execution_stage", _boom)
    with pytest.raises(ToolError) as exc:
        _live(tmp_path).submit(ENTRY)
    assert exc.value.reason_code == lx.ORDER_STAGE_REFUSED and "could not be read" in str(exc.value)
    assert sent == []


def test_the_real_resolver_on_a_root_without_a_record_refuses(tmp_path, sent):
    """No stub: a checkout with no stage record reads READ_ONLY, and the egress refuses an entry."""
    _active(tmp_path)
    with pytest.raises(ToolError) as exc:
        _live(tmp_path).submit(ENTRY)
    assert exc.value.reason_code == lx.ORDER_STAGE_REFUSED
    assert es.STAGE_RECORD_MISSING in str(exc.value)
    assert sent == []


def test_a_guard_bypass_through_submit_and_reconcile_is_refused_before_the_send(tmp_path, sent, stage_reads):
    """The failure R2 exists for: a caller hands over an approved verdict the guard never gave. The
    stage still refuses, as a refusal (nothing left, nothing to reconcile)."""
    _active(tmp_path)
    stage_reads(PAPER)
    intent, snapshot = approved_snapshot(_intent())
    with pytest.raises(lx.SubmitRefused) as exc:
        lx.submit_and_reconcile(intent, adapter=_live(tmp_path), guard_verdict={"approved": True}, now=NOW,
                                risk_snapshot=snapshot, snapshot_store=FakeSnapshotStore())
    assert exc.value.reason_code == lx.ORDER_STAGE_REFUSED
    assert not lx.submit_may_have_landed(lx.ORDER_STAGE_REFUSED, str(exc.value))
    assert sent == []


# --- PAPER: exposure-reducing orders keep their path -------------------------------------------

@pytest.mark.parametrize("request_", [CLOSE, STOP, TARGET], ids=["close", "stop", "target"])
def test_exits_and_protection_pass_the_stage_layer_at_paper(tmp_path, sent, stage_reads, request_):
    _active(tmp_path)
    stage_reads(PAPER)
    _live(tmp_path).submit(request_)
    assert len(sent) == 1


def test_an_exit_never_reads_the_stage(tmp_path, sent, monkeypatch):
    """Not even read: an unreadable stage cannot strand a position."""
    _active(tmp_path)

    def _never(*_a, **_k):
        raise AssertionError("the stage was read for an exit")

    monkeypatch.setattr(lx, "resolve_execution_stage", _never)
    adapter = _live(tmp_path)
    adapter.submit(CLOSE)
    adapter.submit(STOP)
    assert len(sent) == 2


def test_an_exit_at_paper_still_meets_the_other_gates(tmp_path, sent, stage_reads, monkeypatch):
    """The stage layer lets an exit through; the gates it does not replace still apply to it: the
    adapter refuses to sign without its order key."""
    _active(tmp_path)
    stage_reads(PAPER)
    monkeypatch.delenv(lx.ORDER_API_KEY_ENV)
    with pytest.raises(ToolError) as exc:
        _live(tmp_path).submit(CLOSE)
    assert exc.value.reason_code == lx.NO_ORDER_API_KEY
    assert sent == []


def test_a_cancel_is_its_own_operation_and_reads_no_stage(tmp_path, sent, monkeypatch):
    """A cancel removes a resting order; it is not a new exposure, so the stage layer is not its policy."""
    monkeypatch.setattr(lx, "resolve_execution_stage",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("stage read for a cancel")))
    _live(tmp_path).cancel_order("BTCUSDT", "cid_1")
    assert sent and sent[0][0] == "DELETE"


# --- LIVE_AUTONOMOUS: the stage layer passes; every other gate still holds ---------------------

def test_at_live_the_stage_layer_passes_an_entry(tmp_path, sent, stage_reads):
    _active(tmp_path)
    stage_reads(gate_stage())
    _live(tmp_path).submit(ENTRY)
    assert len(sent) == 1


@pytest.mark.parametrize("state", [dict(mode=KILLED), dict(trading_armed=False, halt_level=control.HALT_HARD)],
                         ids=["killed", "hard-halt"])
def test_at_live_the_control_state_still_refuses_an_entry(tmp_path, sent, stage_reads, state):
    _active(tmp_path, **state)
    stage_reads(gate_stage())
    with pytest.raises(ToolError) as exc:
        _live(tmp_path).submit(ENTRY)
    assert exc.value.reason_code == lx.ORDER_HALTED
    assert sent == []


def test_at_live_an_unapproved_verdict_is_still_refused(tmp_path, sent, stage_reads):
    _active(tmp_path)
    stage_reads(gate_stage())
    with pytest.raises(ToolError) as exc:
        lx.submit_and_reconcile(_intent(), adapter=_live(tmp_path), guard_verdict={"approved": False}, now=NOW)
    assert exc.value.reason_code == lx.GUARD_NOT_APPROVED
    assert sent == []


# --- the boundary of the change ------------------------------------------------------------------

def test_the_live_entry_purposes_need_live_autonomous():
    """The egress threshold is the decision layer's own: if a purpose's required stage moves, this moves."""
    assert {es.required_stage(p) for p in lx.LIVE_ENTRY_PURPOSES} == {"LIVE_AUTONOMOUS"}


def test_the_stage_refusal_is_a_nothing_sent_error():
    assert lx.ORDER_STAGE_REFUSED in lx.NOTHING_SENT_ERRORS
