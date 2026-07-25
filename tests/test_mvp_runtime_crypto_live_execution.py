"""LP4 order-adapter tests (increments 1 + 2a).

Under test: the intent→request mapping (reduceOnly carried faithfully, the conditional bracket
types, the venue's closePosition/quantity/reduceOnly exclusivity and client-order-id charset);
the reconcile comparison and its status vocabulary; the submit+reconcile orchestration
(reconcile-first, never blind-retry; guard-approval belt-and-suspenders); the Safety-Flag gate
selection (inert by default, env alone fails closed); the **signed transport** (signature and
timestamp present, secret never in the URL, -2013 is NOT_FOUND rather than an error, any other
rejection raises so it becomes UNRECONCILABLE, a transport failure never leaks the signed URL);
and the actual-fill facts LP5 needs. **Nothing here opens a socket** — ``urlopen`` is intercepted.
"""

from __future__ import annotations

import io
import json

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


def test_no_autonomous_entry_point_reaches_the_live_order_path():
    """Increment 2b flipped the flags, so what keeps an order from happening autonomously is
    structural, not a missing implementation.

    The property under test is about the **autonomous entry points** — the crypto cycle, the
    scheduler, the pipeline, and the operator loop. LP5's own modules (``live_position``) may of
    course reach the adapter; that is what they are for. What must stay true is that no scheduled
    or operator-triggered run routes there, so the only door remains the deliberate canary script.
    Wiring the cycle to live is a separate, explicit decision — and this test is what makes that
    wiring impossible to do by accident."""
    from pathlib import Path

    from runtime.mvp_runtime.paths import repo_root

    entry_points = [
        "runtime/mvp_runtime/crypto/cycle.py",
        "runtime/mvp_runtime/scheduler.py",
        "runtime/mvp_runtime/pipeline.py",
        "runtime/mvp_runtime/operator.py",
    ]
    live_order_surface = ("live_execution", "live_position", "select_order_adapter",
                          "submit_and_reconcile")
    offenders = []
    for rel in entry_points:
        path = Path(repo_root()) / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        hits = [needle for needle in live_order_surface if needle in text]
        if hits:
            offenders.append(f"{rel}: {hits}")
    assert offenders == [], (
        "an autonomous entry point now reaches the live order path — that is a deliberate "
        f"go-live decision, not a refactor: {offenders}"
    )


def test_real_adapter_refuses_without_credentials(monkeypatch):
    """Authorized by the gate but no order key configured => refuses by NAME, never a value,
    and never opens a socket."""
    monkeypatch.delenv(lx.ORDER_API_KEY_ENV, raising=False)
    monkeypatch.delenv(lx.ORDER_API_SECRET_ENV, raising=False)
    adapter = lx.BinanceFuturesOrderAdapter(authorization=_LIVE_AUTH)
    with pytest.raises(ToolError) as exc:
        adapter.submit(lx.build_order_request(_intent()))
    assert exc.value.reason_code == lx.NO_ORDER_API_KEY
    assert lx.ORDER_API_KEY_ENV in exc.value.reason        # the name is reportable
    assert "secret" not in exc.value.reason.lower().replace("api_secret", "")


def test_real_adapter_rejects_a_disallowed_host():
    with pytest.raises(ToolError) as exc:
        lx.BinanceFuturesOrderAdapter(base_url="https://evil.example.com", authorization=_LIVE_AUTH)
    assert exc.value.reason_code == "ORDER_HOST_NOT_ALLOWED"


# --- increment 2a: conditional order types (the LP5 bracket) ------------------

def _cond_intent(order_type, **kw):
    intent = dict(_intent())
    intent["order_type_exchange"] = order_type
    intent.setdefault("stop_price", 59000.0)
    intent.update(kw)
    return intent


@pytest.mark.parametrize("order_type", ["STOP_MARKET", "TAKE_PROFIT_MARKET"])
def test_conditional_order_carries_the_stop_price(order_type):
    req = lx.build_order_request(_cond_intent(order_type, reduce_only=True))
    assert req["type"] == order_type and req["stopPrice"] == 59000.0
    assert req["reduceOnly"] is True and req["quantity"] > 0


@pytest.mark.parametrize("order_type", ["STOP_MARKET", "TAKE_PROFIT_MARKET"])
def test_conditional_order_without_a_stop_price_is_refused(order_type):
    intent = _cond_intent(order_type)
    intent["stop_price"] = 0
    with pytest.raises(ToolError) as exc:
        lx.build_order_request(intent)
    assert exc.value.reason_code == lx.MALFORMED_INTENT


def test_working_type_is_validated_and_passed_through():
    req = lx.build_order_request(_cond_intent("STOP_MARKET", working_type="MARK_PRICE"))
    assert req["workingType"] == "MARK_PRICE"
    with pytest.raises(ToolError):
        lx.build_order_request(_cond_intent("STOP_MARKET", working_type="LAST_PRICE"))


def test_close_position_excludes_quantity_and_reduce_only():
    """Venue rule: closePosition=true is mutually exclusive with BOTH quantity and reduceOnly."""
    req = lx.build_order_request(_cond_intent("STOP_MARKET", close_position=True, reduce_only=True))
    assert req["closePosition"] == "true"
    assert "quantity" not in req and "reduceOnly" not in req


def test_close_position_is_refused_on_a_market_order():
    """closePosition is a Close-All conditional behaviour; on MARKET the venue would reject it."""
    with pytest.raises(ToolError) as exc:
        lx.build_order_request({**_intent(), "close_position": True})
    assert exc.value.reason_code == lx.MALFORMED_INTENT


def test_unsupported_order_type_is_refused():
    with pytest.raises(ToolError) as exc:
        lx.build_order_request({**_intent(), "order_type_exchange": "LIMIT"})
    assert exc.value.reason_code == lx.MALFORMED_INTENT


def test_client_order_id_charset_is_enforced():
    """The venue's own charset rule — a bad character would be rejected at the venue."""
    with pytest.raises(ToolError) as exc:
        lx.build_order_request({**_intent(), "client_order_id": "bad id!"})
    assert exc.value.reason_code == lx.MALFORMED_INTENT
    # 37 chars exceeds the venue's 36 limit.
    with pytest.raises(ToolError):
        lx.build_order_request({**_intent(), "client_order_id": "A" * 37})


# --- increment 2a: the signed transport (urlopen intercepted, no network) -----

class _FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code, msg):
    import urllib.error
    body = json.dumps({"code": code, "msg": msg}).encode("utf-8")
    return urllib.error.HTTPError("https://redacted", 400, msg, {}, io.BytesIO(body))


@pytest.fixture
def order_creds(monkeypatch):
    monkeypatch.setenv(lx.ORDER_API_KEY_ENV, "test-key")
    monkeypatch.setenv(lx.ORDER_API_SECRET_ENV, "test-secret")


def _adapter():
    return lx.BinanceFuturesOrderAdapter(authorization=_LIVE_AUTH)


def test_submit_signs_the_request_and_never_sends_the_secret(monkeypatch, order_creds):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["method"] = request.method
        seen["headers"] = dict(request.headers)
        return _FakeResponse({"orderId": 99, "status": "NEW"})

    monkeypatch.setattr(lx.urllib.request, "urlopen", fake_urlopen)
    body = _adapter().submit(lx.build_order_request(_intent()))
    assert body["orderId"] == 99
    assert seen["method"] == "POST" and "/fapi/v1/order?" in seen["url"]
    # Signed, timestamped, and the key rides in the header — never the secret anywhere.
    assert "signature=" in seen["url"] and "timestamp=" in seen["url"]
    assert seen["headers"].get("X-mbx-apikey") == "test-key"
    assert "test-secret" not in seen["url"]


def test_fetch_order_queries_by_client_order_id(monkeypatch, order_creds):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["method"] = request.method
        return _FakeResponse({"orderId": 7, "status": "FILLED", "executedQty": "0.001"})

    monkeypatch.setattr(lx.urllib.request, "urlopen", fake_urlopen)
    order = _adapter().fetch_order("BTCUSDT", "TAI_X")
    assert order["orderId"] == 7 and seen["method"] == "GET"
    assert "origClientOrderId=TAI_X" in seen["url"]


def test_order_does_not_exist_is_none_not_an_error(monkeypatch, order_creds):
    """Venue code -2013 is a truthful NOT_FOUND, so it must be None — not UNRECONCILABLE."""
    monkeypatch.setattr(lx.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(-2013, "Order does not exist.")))
    assert _adapter().fetch_order("BTCUSDT", "TAI_X") is None


def test_any_other_query_rejection_raises_so_it_becomes_unreconcilable(monkeypatch, order_creds):
    monkeypatch.setattr(lx.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(-1021, "Timestamp out of recvWindow.")))
    with pytest.raises(ToolError) as exc:
        _adapter().fetch_order("BTCUSDT", "TAI_X")
    assert exc.value.reason_code == lx.ORDER_REJECTED


def test_a_rejected_submit_raises_with_the_venue_reason(monkeypatch, order_creds):
    monkeypatch.setattr(lx.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(-2019, "Margin is insufficient.")))
    with pytest.raises(ToolError) as exc:
        _adapter().submit(lx.build_order_request(_intent()))
    assert exc.value.reason_code == lx.ORDER_REJECTED and "-2019" in exc.value.reason


def test_a_duplicate_client_order_id_is_reported_as_already_landed(monkeypatch, order_creds):
    """Idempotency working as designed: the original landed, so reconcile decides the outcome."""
    monkeypatch.setattr(lx.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(-4116, "clientOrderId is duplicated")))
    with pytest.raises(ToolError) as exc:
        _adapter().submit(lx.build_order_request(_intent()))
    assert "already landed" in exc.value.reason


def test_a_transport_failure_never_leaks_the_signed_url(monkeypatch, order_creds):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.URLError("connection reset to https://fapi.binance.com/...signature=SECRETSIG")

    monkeypatch.setattr(lx.urllib.request, "urlopen", boom)
    with pytest.raises(ToolError) as exc:
        _adapter().submit(lx.build_order_request(_intent()))
    assert exc.value.reason_code == lx.ORDER_TRANSPORT
    assert "signature" not in exc.value.reason and "SECRETSIG" not in exc.value.reason


def test_an_unparseable_body_is_refused(monkeypatch, order_creds):
    class _Bad(_FakeResponse):
        def __init__(self):
            self._raw = b"not json"

    monkeypatch.setattr(lx.urllib.request, "urlopen", lambda *a, **k: _Bad())
    with pytest.raises(ToolError) as exc:
        _adapter().submit(lx.build_order_request(_intent()))
    assert exc.value.reason_code == lx.ORDER_MALFORMED_RESULT


# --- increment 2a: the fill facts LP5 needs ----------------------------------

def test_reconcile_result_carries_the_actual_fill():
    intent = _intent()
    adapter = _FakeAdapter(venue_order={**_venue_order_from(intent),
                                        "avgPrice": "60000.5", "cumQuote": "60.0005"})
    res = lx.submit_and_reconcile(intent, adapter=adapter, guard_verdict=APPROVED, now=NOW)
    assert res["fill"]["avg_price"] == 60000.5 and res["fill"]["cum_quote"] == 60.0005
    assert res["order_type"] == "MARKET"


def test_an_unreconciled_order_reports_an_unknown_fill_not_a_free_trade():
    """None, never 0.0 — a missing fill price must not read as a costless trade."""
    res = lx.submit_and_reconcile(
        _intent(), adapter=_FakeAdapter(fetch_raises="TOOL_TRANSPORT"),
        guard_verdict=APPROVED, now=NOW)
    assert res["reconcile_status"] == lx.UNRECONCILABLE
    assert res["fill"] == {"avg_price": None, "executed_qty": None, "cum_quote": None}
