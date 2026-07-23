"""C2 crypto market-data collection tests.

MockMarketDataCollector needs no network. The real ``BinanceFuturesCollector`` is behind
the Safety-Flag Gate: collect() refuses to open a socket without a valid Authorization,
and select_market_data_collector() fails closed unless a local activation record
authorizes the network capability. The HTTP path is exercised with a supplied
Authorization and a fully mocked urlopen (no real network)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse

import pytest

from runtime.mvp_runtime import safety_gate
from runtime.mvp_runtime.crypto.market_data import (
    BINANCE_FUTURES,
    MARKET_DATA_ENV,
    MAX_CANDLES,
    BinanceFuturesCollector,
    MarketSnapshot,
    MockMarketDataCollector,
    collect_market_data,
    degraded_market_data_record,
    factory_candle_target,
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


# --- deep replay window: per-timeframe depth + backward paging -----------------


def test_factory_candle_target_is_calendar_depth_not_a_bar_count():
    # 1d keeps the flat 500 the constant used to be — the timeframe already in
    # production must not shift when depth becomes timeframe-aware.
    assert factory_candle_target("1d") == 500
    assert factory_candle_target("4h") == 3_000
    assert factory_candle_target("1h") == 12_000
    assert factory_candle_target("15m") == 48_000


def test_factory_candle_target_clamps_the_fastest_timeframes():
    # 500 days of 5m is 144k bars; the ceiling is what bounds egress and memory.
    assert factory_candle_target("5m") == MAX_CANDLES
    assert factory_candle_target("1m") == MAX_CANDLES


def test_factory_candle_target_rejects_unknown_timeframe():
    with pytest.raises(ToolBlocked) as exc:
        factory_candle_target("7m")
    assert exc.value.reason_code == "INVALID_TIMEFRAME"


class _FakeVenue:
    """Serves klines off a fixed past grid, honoring ``limit`` and inclusive ``endTime``.

    The grid sits far in the past so no bar is ever mistaken for still-forming.
    """

    STEP_MS = 86_400_000
    FIRST_MS = 1_000_000_000_000

    def __init__(self, bars: int):
        # The whole grid must sit in the past: a bar closing at or after "now" is
        # correctly dropped as still-forming, so a future-dated grid serves empty
        # pages and the collector stops after one call for the wrong reason.
        assert self.FIRST_MS + bars * self.STEP_MS < time.time() * 1000, "grid runs into the future"
        self.bars = bars
        self.calls: list[dict[str, str]] = []

    def _opens(self) -> list[int]:
        return [self.FIRST_MS + i * self.STEP_MS for i in range(self.bars)]

    def __call__(self, request, timeout):
        query = {k: v[0] for k, v in
                 urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query).items()}
        self.calls.append(query)
        opens = self._opens()
        if "endTime" in query:
            opens = [o for o in opens if o <= int(query["endTime"])]
        opens = opens[-int(query["limit"]):]
        return _FakeResp(json.dumps(
            [_kline(o, o + self.STEP_MS, 100.0 + i) for i, o in enumerate(opens)]
        ))


def test_binance_pages_backward_to_fill_a_deep_window(monkeypatch):
    venue = _FakeVenue(bars=4_000)
    monkeypatch.setattr("urllib.request.urlopen", venue)
    monkeypatch.setattr(BinanceFuturesCollector, "PAGE_LIMIT", 1_500)

    result = BinanceFuturesCollector(authorization=_AUTH).collect(
        "BTCUSDT", "1d", limit=3_000, timeout_seconds=5
    )

    assert len(result.candles) == 3_000
    assert len(venue.calls) > 1, "a window deeper than one page must page"
    # Assembled oldest-first, contiguous, no duplicates across the page seams.
    times = [c.open_time for c in result.candles]
    assert times == sorted(times)
    assert len(set(times)) == len(times)
    # Only the first page is unbounded; every later page walks endTime backward.
    assert "endTime" not in venue.calls[0]
    ends = [int(c["endTime"]) for c in venue.calls[1:]]
    assert ends == sorted(ends, reverse=True)


def test_binance_deep_window_flows_through_the_evidence_record(monkeypatch):
    venue = _FakeVenue(bars=4_000)
    monkeypatch.setattr("urllib.request.urlopen", venue)

    snapshot, record = collect_market_data(
        "BTCUSDT", "1d", collector=BinanceFuturesCollector(authorization=_AUTH),
        now=NOW, limit=2_500,
    )
    assert record["candle_count"] == snapshot["candle_count"] == 2_500
    assert record["network_egress"] is True


def test_binance_stops_when_the_venue_runs_out_of_history(monkeypatch):
    venue = _FakeVenue(bars=800)  # asked for more than exists
    monkeypatch.setattr("urllib.request.urlopen", venue)

    result = BinanceFuturesCollector(authorization=_AUTH).collect(
        "BTCUSDT", "1d", limit=5_000, timeout_seconds=5
    )
    # Returns the whole available history and stops — a short venue is not an error.
    assert len(result.candles) == 800


class _StuckVenue(_FakeVenue):
    """A venue that ignores endTime — always replies with the same newest page."""

    def __call__(self, request, timeout):
        self.calls.append({})
        opens = self._opens()[-10:]
        return _FakeResp(json.dumps(
            [_kline(o, o + self.STEP_MS, 100.0 + i) for i, o in enumerate(opens)]
        ))


def test_binance_refuses_to_spin_without_backward_progress(monkeypatch):
    venue = _StuckVenue(bars=4_000)
    monkeypatch.setattr("urllib.request.urlopen", venue)

    result = BinanceFuturesCollector(authorization=_AUTH).collect(
        "BTCUSDT", "1d", limit=3_000, timeout_seconds=5
    )
    # Second page makes no backward progress, so paging stops there rather than
    # looping to the page budget.
    assert len(venue.calls) == 2
    assert len(result.candles) == 10


def test_binance_page_budget_bounds_one_collection(monkeypatch):
    venue = _FakeVenue(bars=1_000)
    monkeypatch.setattr("urllib.request.urlopen", venue)
    monkeypatch.setattr(BinanceFuturesCollector, "PAGE_LIMIT", 10)
    monkeypatch.setattr(BinanceFuturesCollector, "MAX_PAGES", 3)

    result = BinanceFuturesCollector(authorization=_AUTH).collect(
        "BTCUSDT", "1d", limit=5_000, timeout_seconds=5
    )
    assert len(venue.calls) == 3
    assert len(result.candles) <= 30
