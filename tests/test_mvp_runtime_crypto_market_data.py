"""C2 crypto market-data collection tests.

MockMarketDataCollector needs no network. The real ``BinanceFuturesCollector`` is behind
the Safety-Flag Gate: collect() refuses to open a socket without a valid Authorization,
and select_market_data_collector() fails closed unless a local activation record
authorizes the network capability. The HTTP path is exercised with a supplied
Authorization and a fully mocked urlopen (no real network)."""

from __future__ import annotations

import json
import urllib.error

import pytest

from runtime.mvp_runtime import safety_gate
from runtime.mvp_runtime.crypto.market_data import (
    BINANCE_FUTURES,
    MARKET_DATA_ENV,
    BinanceFuturesCollector,
    MarketSnapshot,
    MockMarketDataCollector,
    collect_market_data,
    degraded_market_data_record,
    select_market_data_collector,
)
from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolBlocked, ToolError
from runtime.mvp_runtime.safety_gate import NETWORK_ACCESS, Authorization, build_activation_record

NOW = "2026-07-22T09:00:00Z"

# A granted egress authorization (as select_market_data_collector would produce).
_AUTH = Authorization(
    flags=(NETWORK_ACCESS,),
    provider_id=BINANCE_FUTURES,
    activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z",
    evidence_ref=".runtime_governance_state/evidence.md",
)


class _ErrorCollector:
    tool_id, tool_version = "crypto.market_data.readonly", "0.1.0"

    def collect(self, symbol, timeframe, *, limit, timeout_seconds):
        raise ToolError("BOOM", "exchange backend unavailable")


class _TimeoutCollector:
    tool_id, tool_version = "crypto.market_data.readonly", "0.1.0"

    def collect(self, symbol, timeframe, *, limit, timeout_seconds):
        raise TimeoutError("deadline exceeded")


def test_collect_returns_snapshot_and_evidence_record():
    snapshot, record = collect_market_data(
        "BTCUSDT", "1d", collector=MockMarketDataCollector(), now=NOW
    )
    assert snapshot["symbol"] == "BTCUSDT" and snapshot["timeframe"] == "1d"
    assert snapshot["candle_count"] == len(snapshot["candles"]) > 0
    assert snapshot["is_synthetic"] is True  # mock data must never look trade-eligible
    assert snapshot["last_close"] == snapshot["candles"][-1]["close"]
    assert all(
        {"open_time", "open", "high", "low", "close", "volume", "close_time"} <= set(c)
        for c in snapshot["candles"]
    )
    assert record["tool_id"] == "crypto.market_data.readonly" and record["tool_class"] == "read"
    assert record["read_only"] is True and record["external_action"] is False
    assert record["candle_count"] == snapshot["candle_count"]
    assert record["input_sha256"].startswith("sha256:") and record["output_sha256"].startswith("sha256:")


def test_deterministic():
    a = collect_market_data("BTCUSDT", "1h", collector=MockMarketDataCollector(), now=NOW)
    b = collect_market_data("BTCUSDT", "1h", collector=MockMarketDataCollector(), now=NOW)
    assert a == b


def test_candles_are_ohlc_consistent():
    snapshot, _ = collect_market_data("ETHUSDT", "4h", collector=MockMarketDataCollector(), now=NOW)
    for c in snapshot["candles"]:
        assert c["low"] <= min(c["open"], c["close"]) <= max(c["open"], c["close"]) <= c["high"]


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_empty_symbol_blocks(bad):
    with pytest.raises(ToolBlocked) as exc:
        collect_market_data(bad, "1d", collector=MockMarketDataCollector(), now=NOW)
    assert exc.value.reason_code == "EMPTY_SYMBOL"


@pytest.mark.parametrize("bad", ["btcusdt", "BTC-USDT", "BTC", "X" * 21])
def test_invalid_symbol_blocks(bad):
    with pytest.raises(ToolBlocked) as exc:
        collect_market_data(bad, "1d", collector=MockMarketDataCollector(), now=NOW)
    assert exc.value.reason_code == "INVALID_SYMBOL"


@pytest.mark.parametrize("bad", ["2h", "1w", "", None])
def test_invalid_timeframe_blocks(bad):
    with pytest.raises(ToolBlocked) as exc:
        collect_market_data("BTCUSDT", bad, collector=MockMarketDataCollector(), now=NOW)
    assert exc.value.reason_code == "INVALID_TIMEFRAME"


def test_collector_error_fails_closed():
    with pytest.raises(ToolBlocked) as exc:
        collect_market_data("BTCUSDT", "1d", collector=_ErrorCollector(), now=NOW)
    assert exc.value.reason_code == "TOOL_ERROR"


def test_collector_timeout_fails_closed():
    with pytest.raises(ToolBlocked) as exc:
        collect_market_data("BTCUSDT", "1d", collector=_TimeoutCollector(), now=NOW)
    assert exc.value.reason_code == "TOOL_ERROR"


def test_limit_bounds_candles():
    snapshot, _ = collect_market_data(
        "BTCUSDT", "1d", collector=MockMarketDataCollector(), now=NOW, limit=7
    )
    assert snapshot["candle_count"] == 7


def test_mock_evidence_records_no_network_egress():
    _, record = collect_market_data("BTCUSDT", "1d", collector=MockMarketDataCollector(), now=NOW)
    assert record["network_egress"] is False


def test_degraded_record_shape():
    record = degraded_market_data_record(
        MockMarketDataCollector(), "BTCUSDT", "1d", "MARKET_DATA_DEGRADED", now=NOW
    )
    assert record["degraded"] is True
    assert record["degraded_reason_code"] == "MARKET_DATA_DEGRADED"
    assert record["candle_count"] == 0 and record["last_close"] is None
    assert record["read_only"] is True and record["external_action"] is False


# --- Safety-Flag Gate wiring in select_market_data_collector -----------------

def test_select_defaults_to_mock(monkeypatch):
    monkeypatch.delenv(MARKET_DATA_ENV, raising=False)
    assert isinstance(select_market_data_collector(), MockMarketDataCollector)


def test_select_real_collector_without_activation_fails_closed(monkeypatch, tmp_path):
    # A public endpoint needs no API key, so the gate is the ONLY barrier: the env var
    # alone must NOT open a network path.
    monkeypatch.setenv(MARKET_DATA_ENV, BINANCE_FUTURES)
    with pytest.raises(SafetyGateBlocked) as exc:
        select_market_data_collector(now="2026-07-22T00:00:00Z", root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def test_select_real_collector_with_activation_returns_binance(monkeypatch, tmp_path):
    state = tmp_path / ".runtime_governance_state"
    state.mkdir()
    evidence_rel = ".runtime_governance_state/market_data_gate_approval.md"
    (tmp_path / evidence_rel).write_text("operator decision evidence", encoding="utf-8")
    record = build_activation_record(
        flags=[NETWORK_ACCESS],
        provider_id=BINANCE_FUTURES,
        activated_at="2026-07-01T00:00:00Z",
        expires_at="2026-12-31T23:59:59Z",
        evidence_ref=evidence_rel,
        authority_level="P1",
    )
    path = safety_gate.activation_path(tmp_path, BINANCE_FUTURES)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")

    monkeypatch.setenv(MARKET_DATA_ENV, BINANCE_FUTURES)
    collector = select_market_data_collector(now="2026-07-22T00:00:00Z", root=tmp_path)
    assert isinstance(collector, BinanceFuturesCollector)


# --- Egress self-guard + HTTP parsing in BinanceFuturesCollector -------------

def test_binance_without_authorization_fails_closed():
    with pytest.raises(SafetyGateBlocked) as exc:
        BinanceFuturesCollector().collect("BTCUSDT", "1d", limit=10, timeout_seconds=5)
    assert exc.value.reason_code == "NOT_AUTHORIZED"


class _FakeResp:
    def __init__(self, payload: str):
        self._payload = payload.encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_urlopen(monkeypatch, payload_or_exc):
    def fake_urlopen(request, timeout):
        if isinstance(payload_or_exc, Exception):
            raise payload_or_exc
        return _FakeResp(payload_or_exc)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def _kline(open_ms: int, close_ms: int, price: float) -> list:
    return [open_ms, str(price), str(price * 1.01), str(price * 0.99), str(price), "123.4", close_ms]


# Two closed candles + one whose close_time is far in the future (still forming).
_KLINES_RESPONSE = json.dumps([
    _kline(1_000_000, 2_000_000, 100.0),
    _kline(2_000_000, 3_000_000, 101.0),
    _kline(3_000_000, 99_999_999_999_999, 102.0),
])


def test_binance_happy_path_drops_forming_candle(monkeypatch):
    _patch_urlopen(monkeypatch, _KLINES_RESPONSE)
    result = BinanceFuturesCollector(authorization=_AUTH).collect(
        "BTCUSDT", "1d", limit=10, timeout_seconds=5
    )
    assert isinstance(result, MarketSnapshot)
    assert result.is_synthetic is False
    # The forming candle (close 102.0, close_time in the future) must be dropped.
    assert [c.close for c in result.candles] == [100.0, 101.0]


def test_binance_integrates_with_collect_evidence(monkeypatch):
    _patch_urlopen(monkeypatch, _KLINES_RESPONSE)
    snapshot, record = collect_market_data(
        "BTCUSDT", "1d", collector=BinanceFuturesCollector(authorization=_AUTH), now=NOW
    )
    assert record["network_egress"] is True
    assert record["is_synthetic"] is False
    assert record["candle_count"] == snapshot["candle_count"] == 2


def test_binance_transport_error_fails_closed(monkeypatch):
    _patch_urlopen(monkeypatch, urllib.error.URLError("connection refused"))
    with pytest.raises(ToolError) as exc:
        BinanceFuturesCollector(authorization=_AUTH).collect("BTCUSDT", "1d", limit=10, timeout_seconds=5)
    assert exc.value.reason_code == "TOOL_TRANSPORT"


@pytest.mark.parametrize("payload", ["not json", '{"a": 1}', "[[1, 2]]", '[["x","1","2","3","4","5",6]]'])
def test_binance_malformed_response_fails_closed(monkeypatch, payload):
    _patch_urlopen(monkeypatch, payload)
    with pytest.raises(ToolError) as exc:
        BinanceFuturesCollector(authorization=_AUTH).collect("BTCUSDT", "1d", limit=10, timeout_seconds=5)
    assert exc.value.reason_code == "MALFORMED_RESULT"
