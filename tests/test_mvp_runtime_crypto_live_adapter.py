"""LP4 live order adapter tests — gated, secret-safe, never-retrying, faithful.

The adapter is the first code in the runtime that CAN place a real order, so the properties
under test are the ones that keep it from doing so by accident, and from doing the wrong
thing when it is deliberately used:

* the default is a dry run that opens no socket and never claims an order exists;
* the env var alone fails closed, and a directly-constructed adapter still re-verifies its
  grant at egress;
* only the allowlisted live host may ever be signed against;
* ``reduce_only`` becomes the venue's ``reduceOnly`` flag — the translation the close
  path's shrink-only guarantee rests on (verification record §"forward-looking checkpoints");
* an ambiguous failure is classified as such and **never retried** — one POST per submit;
* no signature, key, or secret reaches a result, a record, or an error message.

Every transport is injected; no test opens a socket.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto.live_adapter import (
    CLASSIFICATION_AMBIGUOUS,
    CLASSIFICATION_REJECTED,
    DryRunLiveAdapter,
    LiveOrderAdapter,
    classify_exchange_error,
    redact_params,
    select_live_adapter,
    submission_record,
)
from runtime.mvp_runtime.crypto.live_pnl import (
    LIVE_TRADING_ENV,
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
    REAL_LIVE_TRADING,
)
from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolBlocked, ToolError
from runtime.mvp_runtime.safety_gate import Authorization

NOW = "2026-07-25T00:00:00Z"

_LIVE_AUTH = Authorization(
    flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID,
    activation_sha256="sha256:test", expires_at="2999-01-01T00:00:00Z",
    evidence_ref=".runtime_governance_state/evidence.md",
)

_INTENT = {
    "client_order_id": "mvp-btc-long-abc123",
    "symbol": "BTCUSDT",
    "side": "BUY",
    "order_type_exchange": "MARKET",
    "quantity": 0.002,
    "reduce_only": False,
}


def _creds(monkeypatch):
    monkeypatch.setenv("BINANCE_LIVE_API_KEY", "test-key")
    monkeypatch.setenv("BINANCE_LIVE_API_SECRET", "test-secret")


class _Recorder:
    """Injected transport that records every call and returns a scripted response."""

    def __init__(self, status=200, body=None, raises=None):
        self.calls: list[tuple] = []
        self._status, self._body, self._raises = status, body or {}, raises

    def __call__(self, method, url, params, headers, timeout):
        self.calls.append((method, url, dict(params), dict(headers), timeout))
        if self._raises is not None:
            raise self._raises
        return self._status, self._body


# --- the default is inert ------------------------------------------------------

def test_default_is_dry_run_and_sends_nothing(tmp_path):
    adapter = select_live_adapter(now=NOW, root=tmp_path)
    assert isinstance(adapter, DryRunLiveAdapter)
    assert adapter.network_egress is False
    result = adapter.submit_order(_INTENT)
    # It must not merely fail to send — it must not claim an order exists.
    assert result["submitted"] is False and result["dry_run"] is True
    assert result["exchange_order_id"] is None


def test_env_alone_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(LIVE_TRADING_ENV, REAL_LIVE_TRADING)
    with pytest.raises(SafetyGateBlocked) as exc:
        select_live_adapter(now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def test_unrelated_env_value_stays_inert(tmp_path, monkeypatch):
    monkeypatch.setenv(LIVE_TRADING_ENV, "paper")
    assert isinstance(select_live_adapter(now=NOW, root=tmp_path), DryRunLiveAdapter)


def test_egress_refused_without_authorization(monkeypatch):
    """A directly constructed adapter cannot bypass the gate."""
    _creds(monkeypatch)
    adapter = LiveOrderAdapter(authorization=None, transport=_Recorder())
    with pytest.raises(SafetyGateBlocked) as exc:
        adapter.submit_order(_INTENT)
    assert exc.value.reason_code == "NOT_AUTHORIZED"


def test_non_live_host_refused_at_construction():
    with pytest.raises(ToolBlocked) as exc:
        LiveOrderAdapter(authorization=_LIVE_AUTH, base_url="https://testnet.binancefuture.com")
    assert exc.value.reason_code == "HOST_NOT_ALLOWED"


def test_missing_credentials_report_names_only(monkeypatch):
    monkeypatch.delenv("BINANCE_LIVE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_LIVE_API_SECRET", raising=False)
    transport = _Recorder()
    adapter = LiveOrderAdapter(authorization=_LIVE_AUTH, transport=transport)
    with pytest.raises(ToolError) as exc:
        adapter.submit_order(_INTENT)
    assert exc.value.reason_code == "NO_API_KEY"
    assert "BINANCE_LIVE_API_KEY" in str(exc.value)
    assert transport.calls == []  # nothing was sent


# --- faithful translation ------------------------------------------------------

def test_submit_sends_one_signed_order(monkeypatch):
    _creds(monkeypatch)
    transport = _Recorder(200, {"orderId": 12345, "status": "FILLED"})
    adapter = LiveOrderAdapter(authorization=_LIVE_AUTH, transport=transport)
    result = adapter.submit_order(_INTENT)

    assert len(transport.calls) == 1  # exactly one POST
    method, url, params, headers, _ = transport.calls[0]
    assert method == "POST" and url.endswith("/fapi/v1/order")
    assert url.startswith("https://fapi.binance.com")
    assert params["symbol"] == "BTCUSDT" and params["side"] == "BUY"
    assert params["newClientOrderId"] == _INTENT["client_order_id"]
    assert "signature" in params and headers["X-MBX-APIKEY"] == "test-key"
    assert result["submitted"] is True and result["exchange_order_id"] == 12345


def test_reduce_only_becomes_the_venue_flag(monkeypatch):
    """The one translation the close path's shrink-only guarantee rests on."""
    _creds(monkeypatch)
    transport = _Recorder(200, {"orderId": 9})
    adapter = LiveOrderAdapter(authorization=_LIVE_AUTH, transport=transport)
    adapter.submit_order({**_INTENT, "reduce_only": True, "side": "SELL"})
    assert transport.calls[0][2]["reduceOnly"] == "true"

    # And an entry must NOT carry it — a reduceOnly entry would never open.
    transport2 = _Recorder(200, {"orderId": 10})
    LiveOrderAdapter(authorization=_LIVE_AUTH, transport=transport2).submit_order(_INTENT)
    assert "reduceOnly" not in transport2.calls[0][2]


def test_submit_refuses_without_a_client_order_id(monkeypatch):
    """Without a deterministic id an ambiguous submit could never be resolved."""
    _creds(monkeypatch)
    transport = _Recorder()
    adapter = LiveOrderAdapter(authorization=_LIVE_AUTH, transport=transport)
    with pytest.raises(ToolError) as exc:
        adapter.submit_order({k: v for k, v in _INTENT.items() if k != "client_order_id"})
    assert exc.value.reason_code == "MISSING_CLIENT_ORDER_ID"
    assert transport.calls == []  # refused before signing


# --- never retry, classify honestly --------------------------------------------

def test_transport_failure_is_ambiguous_and_not_retried(monkeypatch):
    _creds(monkeypatch)
    transport = _Recorder(raises=TimeoutError("timed out"))
    adapter = LiveOrderAdapter(authorization=_LIVE_AUTH, transport=transport)
    result = adapter.submit_order(_INTENT)

    assert len(transport.calls) == 1  # ONE attempt: the order may already be live
    assert result["submitted"] is False
    assert result["classification"] == CLASSIFICATION_AMBIGUOUS


def test_venue_rejection_is_not_ambiguous(monkeypatch):
    _creds(monkeypatch)
    transport = _Recorder(400, {"code": -1111, "msg": "Precision is over the maximum"})
    adapter = LiveOrderAdapter(authorization=_LIVE_AUTH, transport=transport)
    result = adapter.submit_order(_INTENT)
    assert result["submitted"] is False
    assert result["classification"] == CLASSIFICATION_REJECTED
    assert result["error"]["code"] == -1111


@pytest.mark.parametrize("status,expected", [
    (500, CLASSIFICATION_AMBIGUOUS), (502, CLASSIFICATION_AMBIGUOUS),
    (503, CLASSIFICATION_AMBIGUOUS), (429, CLASSIFICATION_AMBIGUOUS),
    (408, CLASSIFICATION_AMBIGUOUS), (400, CLASSIFICATION_REJECTED),
    (401, CLASSIFICATION_REJECTED), (404, CLASSIFICATION_REJECTED),
])
def test_status_classification(status, expected):
    assert classify_exchange_error(status_code=status) == expected


def test_transport_level_failure_defaults_to_ambiguous():
    # No status at all: the request may have been delivered and the response lost.
    assert classify_exchange_error(error_name="URLError") == CLASSIFICATION_AMBIGUOUS


def test_query_and_cancel_use_the_client_order_id(monkeypatch):
    _creds(monkeypatch)
    transport = _Recorder(200, {"status": "FILLED", "avgPrice": "50000"})
    adapter = LiveOrderAdapter(authorization=_LIVE_AUTH, transport=transport)
    adapter.query_order("BTCUSDT", "cid-1")
    adapter.cancel_order("BTCUSDT", "cid-1")
    assert transport.calls[0][0] == "GET" and transport.calls[0][2]["origClientOrderId"] == "cid-1"
    assert transport.calls[1][0] == "DELETE" and transport.calls[1][2]["origClientOrderId"] == "cid-1"


# --- secrets never surface ------------------------------------------------------

def test_no_secret_reaches_a_result_or_record(monkeypatch):
    _creds(monkeypatch)
    transport = _Recorder(200, {"orderId": 7})
    adapter = LiveOrderAdapter(authorization=_LIVE_AUTH, transport=transport)
    result = adapter.submit_order(_INTENT)
    record = submission_record(result, adapter=adapter, intent=_INTENT, now=NOW)

    blob = f"{result}{record}"
    assert "test-secret" not in blob and "test-key" not in blob
    assert result["request"]["signature"] == "***redacted***"


def test_transport_exception_message_is_generic(monkeypatch):
    """The exception text can embed the signed URL, so only the TYPE is reported."""
    _creds(monkeypatch)
    secret_url = "https://fapi.binance.com/fapi/v1/order?signature=DEADBEEFSECRET"
    transport = _Recorder(raises=RuntimeError(secret_url))
    adapter = LiveOrderAdapter(authorization=_LIVE_AUTH, transport=transport)
    result = adapter.submit_order(_INTENT)
    assert "DEADBEEFSECRET" not in str(result)
    assert "RuntimeError" in result["error"]["msg"]


def test_redact_params_removes_only_the_signature():
    redacted = redact_params({"signature": "abc", "symbol": "BTCUSDT", "quantity": 1})
    assert redacted["signature"] == "***redacted***"
    assert redacted["symbol"] == "BTCUSDT" and redacted["quantity"] == 1


def test_submission_record_is_metadata_only(monkeypatch):
    _creds(monkeypatch)
    transport = _Recorder(200, {"orderId": 55})
    adapter = LiveOrderAdapter(authorization=_LIVE_AUTH, transport=transport)
    record = submission_record(
        adapter.submit_order(_INTENT), adapter=adapter, intent=_INTENT, now=NOW)
    assert record["submitted"] is True and record["external_action"] is True
    assert record["exchange_order_id"] == 55
    assert record["network_egress"] is True
    assert "response" not in record  # no raw venue payload is copied in


def test_dry_run_record_reports_no_external_action():
    adapter = DryRunLiveAdapter()
    record = submission_record(
        adapter.submit_order(_INTENT), adapter=adapter, intent=_INTENT, now=NOW)
    assert record["external_action"] is False and record["network_egress"] is False
    assert record["dry_run"] is True


# --- the order path stays unreachable ------------------------------------------

def test_adapter_is_not_imported_by_any_autonomous_path():
    """LP4 ships the capability but wires it nowhere: no cycle, scheduler, pipeline or
    operator leg may import it until LP5 deliberately does (and flips the off-switches)."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "runtime" / "mvp_runtime"
    importers = [
        path.name for path in root.rglob("*.py")
        if path.name not in {"live_adapter.py"} and "live_adapter" in path.read_text(encoding="utf-8")
    ]
    assert importers == [], f"live_adapter is wired into: {importers}"


def test_order_path_off_switch_still_false():
    """The readiness board must not claim an order path exists until LP5 lands."""
    from runtime.mvp_runtime.crypto.live_readiness import ORDER_PATH_IMPLEMENTED

    assert ORDER_PATH_IMPLEMENTED is False
