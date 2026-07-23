"""LP1 live-account tests — read-only by construction, gated, secret-safe, degradable.

Under test: the account feed cannot place an order because no such method exists (the
load-bearing property, asserted structurally rather than trusted); the env var alone fails
closed and the capable feed re-verifies its grant at egress; a signed URL and an API secret
never reach an error message; balances survive a failed P&L read; and the arithmetic the
operator actually reads (open positions, windowed net P&L, return basis) is correct.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.parse

import pytest

from runtime.mvp_runtime.crypto import account as account_mod
from runtime.mvp_runtime.crypto.account import (
    ACCOUNT_API_KEY_ENV,
    ACCOUNT_API_SECRET_ENV,
    ACCOUNT_DATA_DEGRADED,
    ACCOUNT_FEED_ENV,
    BINANCE_ACCOUNT,
    AccountSnapshot,
    BinanceFuturesAccountFeed,
    NoAccountFeed,
    bucket_income,
    parse_positions,
    read_account,
    render_account_text,
    return_pct,
    select_account_feed,
    snapshot_record,
)
from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolBlocked, ToolError
from runtime.mvp_runtime.safety_gate import NETWORK_ACCESS, Authorization

NOW = "2026-07-23T12:00:00Z"
_SECRET = "super-secret-value-never-logged"

_ACCOUNT_AUTH = Authorization(
    flags=(NETWORK_ACCESS,), provider_id=BINANCE_ACCOUNT, activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z", evidence_ref=".runtime_governance_state/evidence.md",
)


class _FakeResp:
    def __init__(self, payload: str):
        self._payload = payload.encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _creds(monkeypatch):
    monkeypatch.setenv(ACCOUNT_API_KEY_ENV, "test-api-key")
    monkeypatch.setenv(ACCOUNT_API_SECRET_ENV, _SECRET)


def _account_payload(**overrides):
    payload = {
        "totalWalletBalance": "1000.50",
        "totalMarginBalance": "1042.25",
        "availableBalance": "880.10",
        "totalUnrealizedProfit": "41.75",
        "positions": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


def _patch_urlopen(monkeypatch, responses):
    """`responses` is a list of payload strings or Exceptions, consumed in call order."""
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        payload = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(payload, Exception):
            raise payload
        return _FakeResp(payload)

    monkeypatch.setattr(account_mod.urllib.request, "urlopen", fake_urlopen)
    return calls


# --- the structural safety property -------------------------------------------------

def test_account_feed_has_no_order_capability():
    """The reason this module is safe is that the capability is ABSENT, not disabled.

    If someone later adds `submit_order` here, this test fails and they must instead put
    it behind the live-order gate where the guards live.
    """
    forbidden = ("order", "submit", "cancel", "close", "trade", "transfer", "withdraw")
    for cls in (BinanceFuturesAccountFeed, NoAccountFeed):
        for name in dir(cls):
            if name.startswith("_"):
                continue
            assert not any(word in name.lower() for word in forbidden), (
                f"{cls.__name__}.{name} looks like an execution capability; "
                "the account feed must stay read-only by construction"
            )


# --- the gate -----------------------------------------------------------------------

def test_env_unset_returns_inert_feed(tmp_path, monkeypatch):
    monkeypatch.delenv(ACCOUNT_FEED_ENV, raising=False)
    feed = select_account_feed(now=NOW, root=tmp_path)
    assert isinstance(feed, NoAccountFeed)
    assert feed.network_egress is False
    assert feed.account_snapshot(timeout_seconds=1) is None


def test_env_alone_fails_closed(tmp_path, monkeypatch):
    """Opting in without a local grant must refuse, not open a socket."""
    monkeypatch.setenv(ACCOUNT_FEED_ENV, BINANCE_ACCOUNT)
    with pytest.raises(SafetyGateBlocked) as exc:
        select_account_feed(now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def test_unrelated_env_value_stays_inert(tmp_path, monkeypatch):
    monkeypatch.setenv(ACCOUNT_FEED_ENV, "something_else")
    assert isinstance(select_account_feed(now=NOW, root=tmp_path), NoAccountFeed)


def test_egress_refused_without_authorization(monkeypatch):
    """A directly constructed feed cannot bypass the gate: egress re-verifies."""
    _creds(monkeypatch)
    feed = BinanceFuturesAccountFeed(authorization=None)
    with pytest.raises(SafetyGateBlocked) as exc:
        feed.account_snapshot(timeout_seconds=1)
    assert exc.value.reason_code == "NOT_AUTHORIZED"


def test_non_live_host_refused_at_construction():
    with pytest.raises(ToolBlocked) as exc:
        BinanceFuturesAccountFeed(authorization=_ACCOUNT_AUTH, base_url="https://evil.example.com")
    assert exc.value.reason_code == "HOST_NOT_ALLOWED"


# --- secret safety ------------------------------------------------------------------

def test_missing_credentials_names_the_env_var_not_a_value(monkeypatch):
    monkeypatch.delenv(ACCOUNT_API_KEY_ENV, raising=False)
    monkeypatch.delenv(ACCOUNT_API_SECRET_ENV, raising=False)
    feed = BinanceFuturesAccountFeed(authorization=_ACCOUNT_AUTH)
    with pytest.raises(ToolError) as exc:
        feed.account_snapshot(timeout_seconds=1)
    assert exc.value.reason_code == "NO_API_KEY"
    assert ACCOUNT_API_KEY_ENV in exc.value.reason


def test_transport_failure_never_echoes_the_signed_url(monkeypatch):
    """The query string carries the HMAC signature, so it must not reach any message."""
    _creds(monkeypatch)
    _patch_urlopen(monkeypatch, [urllib.error.URLError("boom https://fapi.binance.com/x?signature=deadbeef")])
    feed = BinanceFuturesAccountFeed(authorization=_ACCOUNT_AUTH)
    with pytest.raises(ToolError) as exc:
        feed.account_snapshot(timeout_seconds=1)
    assert exc.value.reason_code == "TOOL_TRANSPORT"
    assert "signature" not in exc.value.reason
    assert _SECRET not in exc.value.reason


def test_request_is_signed_and_carries_the_key_header(monkeypatch):
    _creds(monkeypatch)
    calls = _patch_urlopen(monkeypatch, [_account_payload(), "[]"])
    feed = BinanceFuturesAccountFeed(authorization=_ACCOUNT_AUTH)
    feed.account_snapshot(timeout_seconds=1)

    request = calls[0]
    assert request.get_header("X-mbx-apikey") == "test-api-key"
    assert request.get_method() == "GET"
    query = urllib.parse.urlparse(request.full_url).query
    unsigned, _, signature = query.rpartition("&signature=")
    expected = hmac.new(_SECRET.encode("utf-8"), unsigned.encode("utf-8"), hashlib.sha256).hexdigest()
    assert signature == expected
    # The secret proves the signature; it is never itself transmitted.
    assert _SECRET not in request.full_url


def test_snapshot_record_carries_no_credentials(monkeypatch):
    _creds(monkeypatch)
    _patch_urlopen(monkeypatch, [_account_payload(), "[]"])
    feed = BinanceFuturesAccountFeed(authorization=_ACCOUNT_AUTH)
    snapshot = feed.account_snapshot(timeout_seconds=1)
    record = snapshot_record(snapshot, feed=feed, now=NOW)
    blob = json.dumps(record)
    assert _SECRET not in blob and "test-api-key" not in blob and "signature" not in blob
    assert record["read_only"] is True
    assert record["external_action"] is False
    assert record["network_egress"] is True


# --- degradation --------------------------------------------------------------------

def test_failed_pnl_read_keeps_balances(monkeypatch):
    """Losing the income history must narrow the answer, not discard the working part."""
    _creds(monkeypatch)
    _patch_urlopen(monkeypatch, [_account_payload(), urllib.error.URLError("down")])
    feed = BinanceFuturesAccountFeed(authorization=_ACCOUNT_AUTH)
    snapshot = feed.account_snapshot(timeout_seconds=1)
    assert snapshot.wallet_balance == pytest.approx(1000.50)
    assert snapshot.realized_windows["7d"]["net"] == 0.0
    assert any("TOOL_TRANSPORT" in w for w in snapshot.warnings)


def test_read_account_degrades_instead_of_raising(tmp_path, monkeypatch):
    monkeypatch.setenv(ACCOUNT_FEED_ENV, BINANCE_ACCOUNT)
    monkeypatch.setattr(
        account_mod, "select_account_feed",
        lambda **_: BinanceFuturesAccountFeed(authorization=_ACCOUNT_AUTH),
    )
    monkeypatch.delenv(ACCOUNT_API_KEY_ENV, raising=False)
    monkeypatch.delenv(ACCOUNT_API_SECRET_ENV, raising=False)
    snapshot, record = read_account(timeout_seconds=1)
    assert snapshot is None
    assert record["degraded"] is True
    assert record["degraded_reason_code"] == ACCOUNT_DATA_DEGRADED
    assert record["error_reason_code"] == "NO_API_KEY"


def test_malformed_body_is_typed(monkeypatch):
    _creds(monkeypatch)
    _patch_urlopen(monkeypatch, ["not json at all"])
    feed = BinanceFuturesAccountFeed(authorization=_ACCOUNT_AUTH)
    with pytest.raises(ToolError) as exc:
        feed.account_snapshot(timeout_seconds=1)
    assert exc.value.reason_code == "MALFORMED_RESULT"


# --- the arithmetic the operator reads ----------------------------------------------

def test_parse_positions_drops_flat_rows_and_derives_side():
    rows = [
        {"symbol": "BTCUSDT", "positionAmt": "0.0", "entryPrice": "0"},
        {"symbol": "ETHUSDT", "positionAmt": "-1.5", "entryPrice": "3000",
         "markPrice": "2900", "unrealizedProfit": "150", "leverage": "5", "notional": "-4350"},
        {"symbol": "SOLUSDT", "positionAmt": "10", "entryPrice": "100",
         "markPrice": "105", "unrealizedProfit": "50", "leverage": "3", "notional": "1050"},
    ]
    positions = parse_positions(rows)
    assert [p.symbol for p in positions] == ["ETHUSDT", "SOLUSDT"]
    assert positions[0].side == "SHORT" and positions[0].quantity == pytest.approx(1.5)
    assert positions[0].notional == pytest.approx(4350.0)  # absolute, never negative
    assert positions[1].side == "LONG"


def test_parse_positions_survives_garbage():
    assert parse_positions(None) == []
    assert parse_positions(["nope", {"positionAmt": "abc"}]) == []


def test_bucket_income_windows_and_net():
    now_ms = 1_700_000_000_000
    day = 86_400_000
    rows = [
        {"incomeType": "REALIZED_PNL", "asset": "USDT", "income": "100", "time": now_ms - day // 2},
        {"incomeType": "COMMISSION", "asset": "USDT", "income": "-4", "time": now_ms - day // 2},
        {"incomeType": "FUNDING_FEE", "asset": "USDT", "income": "-1", "time": now_ms - day // 2},
        {"incomeType": "REALIZED_PNL", "asset": "USDT", "income": "50", "time": now_ms - 5 * day},
        {"incomeType": "REALIZED_PNL", "asset": "USDT", "income": "999", "time": now_ms - 40 * day},
        {"incomeType": "TRANSFER", "asset": "USDT", "income": "5000", "time": now_ms - day // 2},
        {"incomeType": "REALIZED_PNL", "asset": "BNB", "income": "7", "time": now_ms - day // 2},
    ]
    windows = bucket_income(rows, now_ms=now_ms)
    assert windows["1d"] == {"realized": 100.0, "commission": -4.0, "funding": -1.0, "net": 95.0}
    assert windows["7d"]["realized"] == pytest.approx(150.0)
    assert windows["7d"]["net"] == pytest.approx(145.0)
    # 40 days old falls outside every window; a deposit and a non-USDT row never count.
    assert windows["30d"]["realized"] == pytest.approx(150.0)


def test_bucket_income_survives_garbage():
    windows = bucket_income(None, now_ms=0)
    assert windows["1d"]["net"] == 0.0


def test_return_pct_uses_the_starting_balance():
    # Ended at 1100 after making 100 -> started at 1000 -> +10%.
    assert return_pct(100.0, 1100.0) == pytest.approx(10.0)
    assert return_pct(-100.0, 900.0) == pytest.approx(-10.0)


def test_return_pct_refuses_a_nonpositive_basis():
    """An account that ended at exactly its profit has no meaningful basis; say so."""
    assert return_pct(100.0, 100.0) is None
    assert return_pct(100.0, 50.0) is None


def test_render_is_ascii_only():
    """Windows consoles are cp949; a non-ASCII glyph kills the process mid-report."""
    snapshot = AccountSnapshot(
        asset="USDT", wallet_balance=1000.0, margin_balance=1042.0, available_balance=880.0,
        unrealized_pnl=42.0,
        positions=parse_positions([
            {"symbol": "BTCUSDT", "positionAmt": "0.01", "entryPrice": "60000",
             "markPrice": "61000", "unrealizedProfit": "10", "leverage": "5", "notional": "610"},
        ]),
        realized_windows=bucket_income([], now_ms=0),
        source="binance_futures_account", collected_at=NOW,
    )
    text = render_account_text(snapshot)
    text.encode("ascii")  # raises if a non-ASCII character slipped in
    assert "BTCUSDT" in text and "positions   : 1 open" in text


def test_render_handles_no_account():
    assert "not configured" in render_account_text(None)
