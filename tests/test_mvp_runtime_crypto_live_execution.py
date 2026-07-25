"""LP4 order-adapter tests (increment 1 — the skeleton).

Under test: the intent→request mapping (reduceOnly carried faithfully, MARKET only); the
reconcile comparison and its status vocabulary; the submit+reconcile orchestration
(reconcile-first, never blind-retry; guard-approval belt-and-suspenders); the Safety-Flag gate
selection (inert by default, env alone fails closed); and that the real send path does NOT exist
yet (the gated adapter is a stub). Nothing here opens a socket.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import live_execution as lx
from runtime.mvp_runtime.crypto.live_order import (
    build_live_order_intent,
    enrich_order_identity,
)
from runtime.mvp_runtime.crypto.live_pnl import (
    LIVE_TRADING_ENV,
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
    REAL_LIVE_TRADING,
)
from runtime.mvp_runtime.crypto.live_promotion import RECONCILED, build_canary_order_record
from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolError
from runtime.mvp_runtime.safety_gate import Authorization

NOW = "2026-07-25T00:00:00Z"
APPROVED = {"approved": True}

_LIVE_AUTH = Authorization(
    flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID, activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z", evidence_ref=".runtime_governance_state/evidence.md",
)


def _intent(*, reduce_only=False, **kw):
    plan = {"direction": kw.pop("direction", "LONG")}
    intent = build_live_order_intent(
        plan, symbol=kw.pop("symbol", "BTCUSDT"), quantity=kw.pop("quantity", 0.001),
        notional_usdt=kw.pop("notional_usdt", 55.0), now=NOW, reduce_only=reduce_only,
        close_reason="stop_loss" if reduce_only else None,
    )
    return enrich_order_identity(intent)


class _FakeAdapter:
    """A scripted transport: returns a fixed venue order (or raises) so the orchestration's
    reconcile branches can be exercised without a venue."""
    tool_id, tool_version = "fake", "0"

    def __init__(self, *, venue_order=None, submit_raises=None, fetch_raises=None):
        self.venue_order, self.submit_raises, self.fetch_raises = venue_order, submit_raises, fetch_raises
        self.submitted = None

    def submit(self, order_request, *, timeout_seconds=10):
        self.submitted = dict(order_request)
        if self.submit_raises:
            raise ToolError(self.submit_raises, "submit boom")
        return {"accepted": True}

    def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10):
        if self.fetch_raises:
            raise ToolError(self.fetch_raises, "fetch boom")
        return self.venue_order


def _venue_order_from(intent, **overrides):
    order = {
        "symbol": intent["symbol"], "side": intent["side"], "status": "FILLED",
        "executedQty": intent["quantity"], "reduceOnly": intent["reduce_only"],
        "orderId": "venue-123",
    }
    order.update(overrides)
    return order


# --- build_order_request -----------------------------------------------------

def test_request_maps_the_intent_and_is_market():
    req = lx.build_order_request(_intent())
    assert req["side"] == "BUY" and req["type"] == "MARKET"
    assert req["reduceOnly"] is False and req["newClientOrderId"].startswith("TAI_")


def test_request_carries_reduce_only_from_the_intent():
    """The structural close boundary: a reduceOnly intent produces a reduceOnly request."""
    req = lx.build_order_request(_intent(reduce_only=True))
    assert req["reduceOnly"] is True and req["side"] == "SELL"   # closing a LONG sells


@pytest.mark.parametrize("mutate, why", [
    (lambda i: {**i, "symbol": ""}, "no symbol"),
    (lambda i: {**i, "side": "LONG"}, "bad side"),
    (lambda i: {**i, "quantity": 0}, "zero qty"),
    (lambda i: {**i, "client_order_id": ""}, "no client_order_id"),
    (lambda i: {**i, "order_type_exchange": "LIMIT"}, "not market"),
])
def test_malformed_intent_is_refused(mutate, why):
    with pytest.raises(ToolError) as exc:
        lx.build_order_request(mutate(_intent()))
    assert exc.value.reason_code == lx.MALFORMED_INTENT, why


# --- reconcile_order ---------------------------------------------------------

def test_reconcile_matching_order_is_reconciled():
    intent = _intent()
    status, problems = lx.reconcile_order(intent, _venue_order_from(intent))
    assert status == RECONCILED and problems == []


def test_reconcile_missing_order_is_not_found():
    status, problems = lx.reconcile_order(_intent(), None)
    assert status == lx.NOT_FOUND and problems


@pytest.mark.parametrize("override, needle", [
    ({"symbol": "ETHUSDT"}, "symbol"),
    ({"side": "SELL"}, "side"),
    ({"status": "NEW"}, "status"),
    ({"executedQty": 0.002}, "executedQty"),
    ({"reduceOnly": True}, "reduceOnly"),
])
def test_reconcile_names_every_divergence(override, needle):
    intent = _intent()
    status, problems = lx.reconcile_order(intent, _venue_order_from(intent, **override))
    assert status == lx.MISMATCH and any(needle in p for p in problems)


# --- submit_and_reconcile orchestration --------------------------------------

def test_dry_run_reconciles_end_to_end():
    res = lx.submit_and_reconcile(_intent(), adapter=lx.DryRunOrderAdapter(), guard_verdict=APPROVED, now=NOW)
    assert res["reconcile_status"] == RECONCILED and res["mismatches"] == []
    assert res["submit_error"] is None and res["exchange_order_id"].startswith("dryrun-")


def test_refuses_an_unapproved_guard_verdict():
    for verdict in ({"approved": False}, {}, None):
        with pytest.raises(ToolError) as exc:
            lx.submit_and_reconcile(_intent(), adapter=lx.DryRunOrderAdapter(), guard_verdict=verdict, now=NOW)
        assert exc.value.reason_code == lx.GUARD_NOT_APPROVED


def test_a_lost_submit_reconciles_to_not_found():
    intent = _intent()
    res = lx.submit_and_reconcile(intent, adapter=_FakeAdapter(venue_order=None), guard_verdict=APPROVED, now=NOW)
    assert res["reconcile_status"] == lx.NOT_FOUND


def test_an_ambiguous_submit_that_landed_still_reconciles():
    """The reconcile-first rule: a submit that raised (timeout) but actually landed is learned
    from the venue read, not assumed lost."""
    intent = _intent()
    adapter = _FakeAdapter(venue_order=_venue_order_from(intent), submit_raises="TOOL_TRANSPORT")
    res = lx.submit_and_reconcile(intent, adapter=adapter, guard_verdict=APPROVED, now=NOW)
    assert res["submit_error"] == "TOOL_TRANSPORT" and res["reconcile_status"] == RECONCILED


def test_a_failed_reconcile_query_is_unreconcilable():
    res = lx.submit_and_reconcile(
        _intent(), adapter=_FakeAdapter(fetch_raises="TOOL_TRANSPORT"), guard_verdict=APPROVED, now=NOW)
    assert res["reconcile_status"] == lx.UNRECONCILABLE and res["exchange_order_id"] is None


def test_a_mismatched_fill_is_surfaced():
    intent = _intent()
    adapter = _FakeAdapter(venue_order=_venue_order_from(intent, executedQty=0.002))
    res = lx.submit_and_reconcile(intent, adapter=adapter, guard_verdict=APPROVED, now=NOW)
    assert res["reconcile_status"] == lx.MISMATCH


def test_a_reconciled_result_makes_a_clean_canary_record():
    """The whole point: a RECONCILED result with no mismatch is a clean canary; anything else is not."""
    intent = _intent()
    res = lx.submit_and_reconcile(intent, adapter=lx.DryRunOrderAdapter(), guard_verdict=APPROVED, now=NOW)
    record = build_canary_order_record(
        reconcile_status=res["reconcile_status"], symbol=res["symbol"],
        exchange_order_id=res["exchange_order_id"], client_order_id=res["client_order_id"],
        mismatches=res["mismatches"], notional_usdt=55.0, now=NOW)
    assert record["clean"] is True


# --- the gate: inert by default, real path not implemented -------------------

def test_selection_is_inert_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv(LIVE_TRADING_ENV, raising=False)
    assert isinstance(lx.select_order_adapter(now=NOW, root=tmp_path), lx.DryRunOrderAdapter)


def test_env_alone_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(LIVE_TRADING_ENV, REAL_LIVE_TRADING)
    with pytest.raises(SafetyGateBlocked) as exc:
        lx.select_order_adapter(now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def test_real_adapter_refuses_without_authorization():
    with pytest.raises(SafetyGateBlocked):
        lx.BinanceFuturesOrderAdapter(authorization=None).submit({"newClientOrderId": "x"})


def test_real_send_path_does_not_exist_yet():
    """Increment 1: even fully authorized, the real adapter cannot send — the order path is a
    stub, so ORDER_PATH_IMPLEMENTED stays honestly False."""
    from runtime.mvp_runtime.crypto.live_readiness import ORDER_PATH_IMPLEMENTED
    assert ORDER_PATH_IMPLEMENTED is False
    adapter = lx.BinanceFuturesOrderAdapter(authorization=_LIVE_AUTH)
    with pytest.raises(ToolError) as exc:
        adapter.submit({"newClientOrderId": "x"})
    assert exc.value.reason_code == lx.ORDER_PATH_NOT_IMPLEMENTED


def test_real_adapter_rejects_a_disallowed_host():
    with pytest.raises(ToolError) as exc:
        lx.BinanceFuturesOrderAdapter(base_url="https://evil.example.com", authorization=_LIVE_AUTH)
    assert exc.value.reason_code == "ORDER_HOST_NOT_ALLOWED"
