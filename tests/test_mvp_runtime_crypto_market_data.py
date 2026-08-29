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

from tests._helpers import FakeResp as _FakeResp, patch_urlopen as _patch_urlopen, make_gate_authorization

from runtime.mvp_runtime import safety_gate
from runtime.mvp_runtime.crypto import cycle, factory, market_data
from runtime.mvp_runtime.crypto.market_data import (
    BINANCE_FUTURES,
    FACTORY_DEPTH_DAYS,
    HYPERLIQUID,
    MARKET_DATA_ENV,
    MAX_CANDLES,
    MIN_FACTORY_BARS,
    TIMEFRAMES,
    BinanceFuturesCollector,
    Candle,
    MarketSnapshot,
    MockMarketDataCollector,
    PerRunFeedCache,
    collect_market_data,
    degraded_market_data_record,
    factory_candle_target,
    select_market_data_collector,
)
from runtime.mvp_runtime.crypto.strategy import ALLOWED_TIMEFRAMES
from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolBlocked, ToolError
from runtime.mvp_runtime.safety_gate import NETWORK_ACCESS, Authorization, build_activation_record

NOW = "2026-07-22T09:00:00Z"

# A granted egress authorization (as select_market_data_collector would produce).
_AUTH = make_gate_authorization(flags=(NETWORK_ACCESS,), provider_id=BINANCE_FUTURES)


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
    # A collection that failed still knows which venue it failed to reach, and the cycle's
    # degraded snapshot reads this field rather than defaulting.
    assert record["venue"] == market_data.BINANCE_FUTURES


# --- which venue the data came from ------------------------------------------

def test_the_snapshot_and_record_name_the_venue_not_just_the_collector():
    """`source` names the collector; `venue` names whose data it returned.

    Two different questions, and the factory needs the second: a spec mined from these
    candles may only reference what this venue serves. Until the snapshot carried it, that
    fact lived in an env var read at collector construction and reached nothing downstream —
    a hyperliquid collection fed a factory run that minted `binance_futures` specs.
    """
    snapshot, record = collect_market_data(
        "BTCUSDT", "1d", collector=MockMarketDataCollector(), now=NOW, limit=50
    )
    assert snapshot["source"] == "mock.market_data"
    assert snapshot["venue"] == market_data.BINANCE_FUTURES
    assert record["venue"] == snapshot["venue"]


def test_every_collector_speaks_for_a_declared_venue():
    """`provider_id` is reused as the venue rather than restated as a second field, so it
    has to keep naming something `VENUE_FEEDS` knows. A collector whose grant identity and
    venue identity drifted apart would mint against a vocabulary nobody described."""
    collectors = [
        value for value in vars(market_data).values()
        if isinstance(value, type) and value.__name__.endswith("Collector")
        and hasattr(value, "provider_id")
    ]
    assert collectors, "expected to find the collector classes"
    for collector in collectors:
        assert collector.provider_id in market_data.VENUE_FEEDS, collector.__name__


def test_a_collector_that_declares_nothing_reads_as_binance():
    # The migration fact, not a guess: every collector that predates the field was Binance's.
    class Bare:
        pass

    assert market_data.collector_venue(Bare()) == market_data.BINANCE_FUTURES


def test_a_venue_a_collector_invents_stops_at_the_factory():
    """`collector_venue`'s default is the fail-OPEN direction, bounded downstream instead.

    A collector naming a venue nobody declared does not get a silent empty vocabulary — it
    raises where the vocabulary is resolved, which is the only place that can tell the
    difference between "this venue serves nothing" and "nobody said what it serves".
    """
    class Inventive(MockMarketDataCollector):
        provider_id = "kraken_perps"

    snapshot, _ = collect_market_data(
        "BTCUSDT", "1d", collector=Inventive(), now=NOW, limit=50
    )
    assert snapshot["venue"] == "kraken_perps"
    with pytest.raises(ToolBlocked) as exc:
        factory.run_factory(snapshot, active_pool={"active_strategies": []},
                            existing_candidates=[], now=NOW, count=1)
    assert exc.value.reason_code == "UNKNOWN_VENUE"


def test_the_factory_mines_the_venue_the_snapshot_came_from():
    """End to end: collector → snapshot → minted spec, with nothing to pass by hand.

    **The HTF leg is attached because the snapshot has to support whichever block the rotation
    lands on**, and this test does not choose that block — `factory.context_rotation_phase`
    does. It used to land on `ma_cross_*`/`macd_cross_*`, which a bare snapshot mints, so the
    dependency was invisible; the rotation phase moved it to `htf_trend_*`/`htf_pullback_*` and
    every spec was refused with `UNSUPPLIABLE_FEATURE: htf_market_regime` — a snapshot the
    scheduler would never hand the factory, since it attaches this leg (and three more) before
    every fire. Attaching it here makes the fixture the shape production actually mines.

    If this fails again on a family whose leg is missing rather than on the venue, the rotation
    has moved once more: attach that leg too rather than pinning the block, which would make
    this test stop covering the families it happens to skip."""
    class HyperliquidLike(MockMarketDataCollector):
        provider_id = market_data.HYPERLIQUID
        source = "hyperliquid_public"

    collector = HyperliquidLike()
    snapshot, _ = collect_market_data(
        "BTCUSDT", "1h", collector=collector, now=NOW, limit=400
    )
    assert cycle.attach_htf(snapshot, collector=collector, now=NOW) is None, (
        "the higher-timeframe leg degraded, so this test would be asserting on a snapshot "
        "thinner than the one the scheduler mines"
    )
    result = factory.run_factory(snapshot, active_pool={"active_strategies": []},
                                 existing_candidates=[], now=NOW, count=4)
    assert result["candidates"], (
        f"nothing minted; rejections were "
        f"{[(r.get('strategy_family'), r.get('reason')) for r in result.get('rejected') or []]}"
    )
    assert {c["strategy_spec"]["venue"] for c in result["candidates"]} == {
        market_data.HYPERLIQUID}


def test_a_snapshot_without_a_venue_still_mines_as_binance():
    # Snapshots are passed between processes and a stored one predates the field. Same
    # migration fact `StrategySpec.from_dict` uses, one layer up.
    snapshot, _ = collect_market_data(
        "BTCUSDT", "1h", collector=MockMarketDataCollector(), now=NOW, limit=400
    )
    del snapshot["venue"]
    # A batch wide enough that the assertion cannot be silently vacuous. This is a bare OHLCV
    # snapshot, so a family reading an HTF or open-interest column is refused by
    # `unsuppliable_features` — and the rotation picks a CONSECUTIVE block, so a batch of four
    # can land entirely inside one such run and mint nothing at all. It did on 2026-08-04, when
    # retiring two families shifted every later index by two: the block became htf_* + oi_* and
    # the venue set compared empty-to-nonempty rather than failing on a venue.
    result = factory.run_factory(snapshot, active_pool={"active_strategies": []},
                                 existing_candidates=[], now=NOW, count=8)
    assert result["candidates"], (
        f"nothing minted; rejections were "
        f"{[(r.get('strategy_family'), r.get('reason')) for r in result.get('rejected') or []]}"
    )
    assert {c["strategy_spec"]["venue"] for c in result["candidates"]} == {
        market_data.BINANCE_FUTURES}


# --- Safety-Flag Gate wiring in select_market_data_collector -----------------

def test_select_defaults_to_mock(monkeypatch):
    monkeypatch.delenv(MARKET_DATA_ENV, raising=False)
    assert isinstance(select_market_data_collector(), MockMarketDataCollector)


def test_select_real_collector_env_alone_returns_binance(monkeypatch, tmp_path):
    # The environment is the gate (Thomas 2026-08-10): a public endpoint needs no API
    # key, so the opt-in is the ONLY barrier — and the opt-in alone now selects the
    # capable collector. The fail-closed direction that remains: an unset or typo'd
    # value reaches only the Mock (test_an_unknown_venue_selects_the_inert_mock).
    monkeypatch.setenv(MARKET_DATA_ENV, BINANCE_FUTURES)
    collector = select_market_data_collector(now="2026-07-22T00:00:00Z", root=tmp_path)
    assert isinstance(collector, market_data.BinanceFuturesCollector)


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
    # The calendar span alone governs every timeframe deeper than the bar floor.
    # Doubled with `FACTORY_DEPTH_DAYS` 500 -> 1,000 on 2026-08-04; 15m at 96k is what
    # forced `MAX_CANDLES` up in the same change rather than being clamped to 625 days.
    assert factory_candle_target("4h") == 6_000
    assert factory_candle_target("1h") == 24_000
    assert factory_candle_target("15m") == 96_000


def test_factory_candle_target_floors_the_slowest_timeframe():
    # 500 days of 1d is 500 bars — ~350 after the holdout split, ~16 trades, under the
    # scorer's critical trades-per-parameter. The floor is what makes a 1d verdict mean
    # anything; without it every 1d lineage is FRAGILE by sample size alone.
    assert factory_candle_target("1d") == MIN_FACTORY_BARS
    assert FACTORY_DEPTH_DAYS * 1440 // TIMEFRAMES["1d"] < MIN_FACTORY_BARS


def test_factory_candle_target_floor_never_shortens_a_window():
    # One-sided: the floor may only lengthen. A timeframe whose calendar span already
    # exceeds it must come back unchanged, or the fast-timeframe fix this floor sits on
    # top of would be silently undone.
    for timeframe in ("15m", "1h", "4h"):
        calendar_bars = FACTORY_DEPTH_DAYS * 1440 // TIMEFRAMES[timeframe]
        assert factory_candle_target(timeframe) == min(calendar_bars, MAX_CANDLES)
        assert factory_candle_target(timeframe) >= MIN_FACTORY_BARS


def test_factory_candle_target_clamps_the_fastest_timeframes():
    # 500 days of 5m is 144k bars; the ceiling is what bounds egress and memory.
    assert factory_candle_target("5m") == MAX_CANDLES
    assert factory_candle_target("1m") == MAX_CANDLES


def test_every_authorable_timeframe_gets_its_full_depth():
    """Drift gate: collection accepts more timeframes than a strategy can be authored at.

    ``strategy.ALLOWED_TIMEFRAMES`` is the narrower vocabulary — a spec at 5m is
    rejected at parse, so a 5m factory schedule mines candidates that can never be
    accepted. Every timeframe that IS authorable must fit under MAX_CANDLES, or the
    clamp would silently shorten a window the factory depends on. Widening either
    vocabulary without re-checking the other trips this.

    The calendar span is a floor on the window, not an equality: ``MIN_FACTORY_BARS``
    lengthens 1d past it. What must hold for every authorable timeframe is that the
    window is never SHORTER than the span — that is the direction a clamp could break.
    """
    assert ALLOWED_TIMEFRAMES < set(TIMEFRAMES), "strategy vocabulary must stay a subset"
    for timeframe in ALLOWED_TIMEFRAMES:
        bars = factory_candle_target(timeframe)
        assert bars < MAX_CANDLES, f"{timeframe} is clamped below its full depth"
        assert bars * TIMEFRAMES[timeframe] // 1440 >= FACTORY_DEPTH_DAYS


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


# === the reference price the declaration is checked against ==========================

class _Snapshot:
    """Stands in for a collector: returns whatever candles the test hands it."""

    def __init__(self, candles, *, synthetic=False, raises=None):
        self._candles, self._synthetic, self._raises = candles, synthetic, raises
        self.tool_id, self.tool_version = "test.collector", "0.1.0"

    def collect(self, symbol, timeframe, *, limit, timeout_seconds):
        if self._raises is not None:
            raise self._raises
        return market_data.MarketSnapshot(
            symbol=symbol, timeframe=timeframe, candles=self._candles,
            source="test", is_synthetic=self._synthetic,
        )


def _candle(close, close_time):
    return market_data.Candle(
        open_time=close_time, open=close, high=close, low=close, close=close,
        volume=1.0, close_time=close_time,
    )


NOW_PRICE = "2026-07-27T12:00:00Z"


class TestReferencePrice:
    """Every uncertain answer is a refusal.

    This price bounds a live order, and one that reads LOW is exactly what lets a bigger
    position past a cap — so "I am not sure" and "here is a number" must never be confused.
    """

    def test_a_fresh_real_close_is_returned(self):
        price, error = market_data.read_reference_price(
            "BTCUSDT", collector=_Snapshot([_candle(64_512.0, "2026-07-27T11:59:00Z")]),
            now=NOW_PRICE)
        assert (price, error) == (64_512.0, None)

    def test_a_synthetic_price_is_refused(self):
        """`MockMarketDataCollector` synthesises a hash-derived walk: a plausible-looking
        number with no relationship to the venue, which is worse than no number at all."""
        _, error = market_data.read_reference_price(
            "BTCUSDT", collector=_Snapshot([_candle(50_000.0, "2026-07-27T11:59:00Z")],
                                           synthetic=True), now=NOW_PRICE)
        assert error == market_data.PRICE_SYNTHETIC

    def test_no_candles_is_refused(self):
        _, error = market_data.read_reference_price(
            "BTCUSDT", collector=_Snapshot([]), now=NOW_PRICE)
        assert error == market_data.PRICE_ABSENT

    def test_a_stale_close_is_refused(self):
        """A price from before whatever moved the market since is not this order's price."""
        _, error = market_data.read_reference_price(
            "BTCUSDT", collector=_Snapshot([_candle(64_512.0, "2026-07-27T11:00:00Z")]),
            now=NOW_PRICE)
        assert error == market_data.PRICE_STALE

    def test_a_close_inside_the_freshness_window_is_kept(self):
        price, error = market_data.read_reference_price(
            "BTCUSDT", collector=_Snapshot([_candle(64_512.0, "2026-07-27T11:56:00Z")]),
            now=NOW_PRICE)
        assert (price, error) == (64_512.0, None)

    @pytest.mark.parametrize("close", [0.0, -5.0])
    def test_a_non_positive_close_is_refused(self, close):
        _, error = market_data.read_reference_price(
            "BTCUSDT", collector=_Snapshot([_candle(close, "2026-07-27T11:59:00Z")]),
            now=NOW_PRICE)
        assert error == market_data.PRICE_UNREADABLE

    def test_a_collector_failure_is_refused_not_raised(self):
        """The caller reports this beside its other refusals, so it returns rather than raises."""
        _, error = market_data.read_reference_price(
            "BTCUSDT",
            collector=_Snapshot([], raises=ToolError("FEED_DOWN", "connection reset")),
            now=NOW_PRICE)
        assert error == market_data.PRICE_UNREADABLE

    def test_the_real_mock_collector_is_refused_end_to_end(self):
        """Not a hand-built stub: the collector a default machine actually selects."""
        _, error = market_data.read_reference_price(
            "BTCUSDT", collector=market_data.MockMarketDataCollector(), now=NOW_PRICE)
        assert error == market_data.PRICE_SYNTHETIC


# --- one fan-out, one request per distinct question (2026-07-29) -------------------------------
#
# A cycle is keyed by (symbol, timeframe); funding, liquidations, open interest and exchangeInfo
# are keyed by SYMBOL. So a pool over 5 symbols x 4 timeframes asked the venue each symbol-scoped
# question four times a fire with identical arguments. `oi_store` had already solved this for the
# hourly OI series and named the arithmetic in its own docstring; the other three kept fanning out.

class _CountingCollector:
    """A collector that records every call it is actually asked to make."""

    tool_id, tool_version = "counting", "0"
    provider_id, source = "binance_futures", "counting"
    network_egress = True

    def __init__(self, *, fail: set[str] | None = None):
        self.calls: list[tuple] = []
        self.fail = fail or set()

    def collect(self, symbol, timeframe, *, limit, timeout_seconds):
        self.calls.append(("collect", symbol, timeframe, limit))
        candles = [
            Candle(open_time=f"2026-07-{(i % 28) + 1:02d}T00:00:00Z", open=1.0, high=2.0,
                   low=0.5, close=1.5, volume=1.0,
                   close_time=f"2026-07-{(i % 28) + 1:02d}T23:59:59Z")
            for i in range(limit)
        ]
        return MarketSnapshot(symbol=symbol, timeframe=timeframe, candles=candles,
                              source=self.source, is_synthetic=False,
                              collector_version=self.tool_version, latency_ms=1)

    def funding_history(self, symbol, *, records, timeout_seconds):
        self.calls.append(("funding_history", symbol, records))
        if "funding_history" in self.fail:
            raise ToolError("TOOL_TRANSPORT", "scripted")
        return [{"timestamp": "2026-07-01T00:00:00Z", "funding_rate": 0.0001}]

    def exchange_info(self, *, timeout_seconds):
        self.calls.append(("exchange_info",))
        return {"symbols": []}


def _counts(collector, name):
    return sum(1 for c in collector.calls if c[0] == name)


def test_a_symbol_scoped_read_is_made_once_per_fan_out():
    """The headline: four timeframes of one symbol, one funding request."""
    inner = _CountingCollector()
    cache = PerRunFeedCache(inner)
    for _ in range(4):
        cache.funding_history("BTCUSDT", records=1600, timeout_seconds=10)
    assert _counts(inner, "funding_history") == 1
    assert cache.requests == 1 and cache.hits == 3


def test_a_different_symbol_is_a_different_question():
    inner = _CountingCollector()
    cache = PerRunFeedCache(inner)
    cache.funding_history("BTCUSDT", records=1600, timeout_seconds=10)
    cache.funding_history("ETHUSDT", records=1600, timeout_seconds=10)
    assert _counts(inner, "funding_history") == 2


def test_different_arguments_are_a_different_question():
    """The memo keys on the arguments, so a deeper window is never served from a shallower one."""
    inner = _CountingCollector()
    cache = PerRunFeedCache(inner)
    cache.funding_history("BTCUSDT", records=1600, timeout_seconds=10)
    cache.funding_history("BTCUSDT", records=200, timeout_seconds=10)
    assert _counts(inner, "funding_history") == 2


def test_a_failure_is_memoized_and_re_raised_to_every_context():
    """Funding that fails at 15m fails at 1h. Retrying is three more timeouts on a scheduler that
    runs everything sequentially — but each context must still see the raise, because each records
    its own reason code."""
    inner = _CountingCollector(fail={"funding_history"})
    cache = PerRunFeedCache(inner)
    for _ in range(4):
        with pytest.raises(ToolError) as excinfo:
            cache.funding_history("BTCUSDT", records=1600, timeout_seconds=10)
        assert excinfo.value.reason_code == "TOOL_TRANSPORT"
    assert _counts(inner, "funding_history") == 1, "a known-failing read was retried"


def test_a_deeper_candle_window_serves_a_shallower_request():
    """`attach_htf` fetches one step up at 240 bars and that timeframe's own context fetches 120.
    Both end at the same last closed candle, so the tail of the deeper window IS the shallower
    answer."""
    inner = _CountingCollector()
    cache = PerRunFeedCache(inner)
    deep = cache.collect("BTCUSDT", "1h", limit=240, timeout_seconds=10)
    shallow = cache.collect("BTCUSDT", "1h", limit=120, timeout_seconds=10)
    assert _counts(inner, "collect") == 1
    assert len(shallow.candles) == 120
    assert shallow.candles == deep.candles[-120:], "the shallower answer is the deeper one's tail"


def test_a_shallower_window_never_serves_a_deeper_request():
    """Serving 120 bars to a 240-bar request would silently shorten an indicator's warm-up."""
    inner = _CountingCollector()
    cache = PerRunFeedCache(inner)
    cache.collect("BTCUSDT", "1h", limit=120, timeout_seconds=10)
    deep = cache.collect("BTCUSDT", "1h", limit=240, timeout_seconds=10)
    assert _counts(inner, "collect") == 2
    assert len(deep.candles) == 240


def test_the_memo_cannot_answer_for_a_capability_the_collector_lacks():
    """`attach_feeds` asks hasattr() to decide whether a collector HAS a feed, and `exchange_info`
    is deliberately absent from the mock so a mock run refuses to size rather than sizing on
    invented lot steps. A wrapper that declared these methods would answer yes for a collector
    that cannot — the one thing it must never do."""
    class _Bare:
        tool_id, tool_version, network_egress = "bare", "0", False

        def collect(self, symbol, timeframe, *, limit, timeout_seconds):
            raise AssertionError("not called")

    cache = PerRunFeedCache(_Bare())
    assert not hasattr(cache, "funding_history")
    assert not hasattr(cache, "exchange_info")
    assert not hasattr(cache, "liquidation_history")
    assert hasattr(PerRunFeedCache(_CountingCollector()), "funding_history")


def test_the_memo_forwards_the_gate_attributes_unchanged():
    """It wraps an already-gated collector and can make no call the wrapped object could not, so
    every attribute the gate and the audit read has to arrive intact."""
    inner = _CountingCollector()
    cache = PerRunFeedCache(inner)
    assert cache.network_egress is inner.network_egress
    assert cache.provider_id == inner.provider_id
    assert cache.tool_id == inner.tool_id and cache.tool_version == inner.tool_version


def test_a_new_fire_asks_again():
    """The memo lives for ONE fire. Nothing here makes a 15-minute cadence stale — it makes one
    cadence cost one request."""
    inner = _CountingCollector()
    PerRunFeedCache(inner).funding_history("BTCUSDT", records=1600, timeout_seconds=10)
    PerRunFeedCache(inner).funding_history("BTCUSDT", records=1600, timeout_seconds=10)
    assert _counts(inner, "funding_history") == 2


# --- a rate limit is not a timeout (2026-07-29) ------------------------------------------------
#
# `urllib.error.HTTPError` is a SUBCLASS of `URLError`, so every read here caught a 429 and
# reported it as "failed or timed out". A timeout says "try again"; a 429 at this venue says
# "stop, and if you do not, this becomes a 418 ban". The runtime was reporting the second as the
# first, and then doing the thing that escalates it.

def _http_error(status, *, retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError("https://redacted", status, "rate limited", headers, None)


def test_a_429_is_classified_as_a_rate_limit_not_a_transport_failure():
    err = market_data.classify_transport_error(_http_error(429), "market-data")
    assert err.reason_code == market_data.TOOL_RATE_LIMITED
    assert "429" in str(err)


def test_a_418_ban_is_a_rate_limit_too():
    """418 is where this venue escalates a 429 that kept knocking. Reading it as anything softer
    than a full stop is what turns minutes of throttling into days of ban."""
    assert market_data.classify_transport_error(_http_error(418), "market-data").reason_code == (
        market_data.TOOL_RATE_LIMITED)


def test_the_retry_after_the_venue_asked_for_is_reported():
    """"Rate limited" and "rate limited, come back in 120 seconds" are different operational
    facts, and only one of them tells an operator when the next fire can succeed."""
    assert "120" in str(market_data.classify_transport_error(_http_error(429, retry_after="120"),
                                                             "market-data"))


def test_an_ordinary_timeout_is_still_a_transport_failure():
    assert market_data.classify_transport_error(TimeoutError(), "market-data").reason_code == (
        "TOOL_TRANSPORT")
    assert market_data.classify_transport_error(
        urllib.error.URLError("unreachable"), "market-data").reason_code == "TOOL_TRANSPORT"


def test_a_transport_error_never_echoes_the_url():
    """A signed request carries its signature in the query string, and this classifier is shared
    with paths that sign."""
    for exc in (_http_error(429), _http_error(500), TimeoutError()):
        assert "redacted" not in str(market_data.classify_transport_error(exc, "market-data"))


class _RateLimitedCollector:
    tool_id, tool_version = "limited", "0"
    provider_id, source, network_egress = "binance_futures", "limited", True

    def __init__(self, *, status=429):
        self.attempts = 0
        self.status = status

    def _raise(self):
        self.attempts += 1
        raise market_data.classify_transport_error(_http_error(self.status), "market-data")

    def collect(self, symbol, timeframe, *, limit, timeout_seconds):
        self._raise()

    def funding_history(self, symbol, *, records, timeout_seconds):
        self._raise()


def test_the_memo_stops_knocking_once_the_venue_says_stop():
    """The response that does not escalate. A fan-out that keeps going after the first refusal
    makes twenty more requests, which is exactly how a throttle becomes a ban."""
    inner = _RateLimitedCollector()
    cache = PerRunFeedCache(inner)
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
        with pytest.raises(ToolError) as excinfo:
            cache.collect(symbol, "1d", limit=120, timeout_seconds=10)
        assert excinfo.value.reason_code == market_data.TOOL_RATE_LIMITED
    assert inner.attempts == 1, f"kept knocking after a 429 ({inner.attempts} attempts)"
    assert cache.rate_limited is not None


def test_the_latch_covers_every_feed_not_just_the_one_that_tripped_it():
    """Being rate limited is a property of the IP, not of the endpoint that reported it."""
    inner = _RateLimitedCollector()
    cache = PerRunFeedCache(inner)
    with pytest.raises(ToolError):
        cache.funding_history("BTCUSDT", records=1600, timeout_seconds=10)
    with pytest.raises(ToolError) as excinfo:
        cache.collect("ETHUSDT", "1d", limit=120, timeout_seconds=10)
    assert excinfo.value.reason_code == market_data.TOOL_RATE_LIMITED
    assert inner.attempts == 1


def test_an_ordinary_failure_does_not_latch_the_whole_fire():
    """Only a rate limit means "stop asking". A symbol that times out must not silence the rest —
    that would turn one bad symbol into a blind fan-out."""
    class _OneBadSymbol(_RateLimitedCollector):
        def collect(self, symbol, timeframe, *, limit, timeout_seconds):
            self.attempts += 1
            raise ToolError("TOOL_TRANSPORT", "timed out")

    inner = _OneBadSymbol()
    cache = PerRunFeedCache(inner)
    for symbol in ("BTCUSDT", "ETHUSDT"):
        with pytest.raises(ToolError):
            cache.collect(symbol, "1d", limit=120, timeout_seconds=10)
    assert inner.attempts == 2
    assert cache.rate_limited is None


def test_the_latch_cannot_reach_the_order_adapter_or_the_account_read():
    """The scope that matters most. A rate-limited fire must still settle, protect and CLOSE an
    open live position — those run on separate objects and separate endpoints, and neither is
    wrapped here. What the latch stops is opening new positions, which is the right posture for
    a runtime that cannot currently see the market."""
    from runtime.mvp_runtime.crypto import account, live_execution, live_route

    inner = _RateLimitedCollector()
    cache = PerRunFeedCache(inner)
    with pytest.raises(ToolError):
        cache.collect("BTCUSDT", "1d", limit=120, timeout_seconds=10)

    # Neither the adapter nor the account feed is reachable from the wrapper: they are not
    # attributes of a collector, so a latched cache cannot answer for them at all.
    for name in ("submit", "fetch_order", "cancel_order", "read_account"):
        assert not hasattr(cache, name)
    assert live_execution.select_order_adapter is not None      # separate selector
    assert account.read_account is not None                     # separate endpoint
    assert live_route.run_live_leg is not None


# --- S1: the Hyperliquid collector (HIP-3 equity perps) ----------------------
#
# Same gate posture as the Binance collector above, so the tests that matter are the ones
# where this venue DIFFERS: `dex:ticker` symbols, an object-shaped candle whose OHLCV are
# strings, three flow legs that structurally do not exist, and a hard per-request ceiling
# reported rather than papered over.

_HL_AUTH = make_gate_authorization(flags=(NETWORK_ACCESS,), provider_id=HYPERLIQUID)


def _hl_candle(open_ms: int, close_ms: int, price: float) -> dict:
    return {"t": open_ms, "T": close_ms, "s": "xyz:XLE", "i": "1h",
            "o": str(price), "c": str(price), "h": str(price * 1.01),
            "l": str(price * 0.99), "v": "5221.48", "n": 365}


# Two closed candles + one whose close is far in the future (still forming).
_HL_RESPONSE = json.dumps([
    _hl_candle(1_000_000, 2_000_000, 100.0),
    _hl_candle(2_000_000, 3_000_000, 101.0),
    _hl_candle(3_000_000, 99_999_999_999_999, 102.0),
])


def test_hyperliquid_drops_the_forming_candle(monkeypatch):
    # The venue returns the still-forming bar (measured 2026-08-03), so dropping it is the
    # adapter's job — a downstream indicator must never see a close that is still moving.
    _patch_urlopen(monkeypatch, _HL_RESPONSE)
    result = market_data.HyperliquidCollector(authorization=_HL_AUTH).collect(
        "xyz:XLE", "1h", limit=10, timeout_seconds=5
    )
    assert [c.close for c in result.candles] == [100.0, 101.0]
    assert result.is_synthetic is False
    assert result.source == "hyperliquid_public"


def test_hyperliquid_absent_flow_legs_are_none_not_derived(monkeypatch):
    # This venue's candle carries no quote volume and no taker split. They must stay
    # indeterminate: `volume * close` would be a plausible-looking fabrication that a
    # mined threshold could match on. `trade_count` IS reported and must survive.
    _patch_urlopen(monkeypatch, _HL_RESPONSE)
    candle = market_data.HyperliquidCollector(authorization=_HL_AUTH).collect(
        "xyz:XLE", "1h", limit=10, timeout_seconds=5
    ).candles[0]
    assert candle.quote_volume is None
    assert candle.taker_buy_base is None
    assert candle.taker_buy_quote is None
    assert candle.trade_count == 365.0


def test_hyperliquid_reports_a_window_shallower_than_requested(monkeypatch):
    # The ceiling is silent at the venue: ask for more than it holds and it answers with
    # what it has and no error. `depth_capped` is the only thing that says so, and without
    # it a single-regime window is indistinguishable from a full one.
    _patch_urlopen(monkeypatch, _HL_RESPONSE)
    collector = market_data.HyperliquidCollector(authorization=_HL_AUTH)
    deep = collector.collect("xyz:XLE", "1h", limit=5_000, timeout_seconds=5)
    assert deep.depth_capped is True
    shallow = collector.collect("xyz:XLE", "1h", limit=2, timeout_seconds=5)
    assert shallow.depth_capped is False


def test_depth_capped_reaches_the_snapshot_and_the_record(monkeypatch):
    _patch_urlopen(monkeypatch, _HL_RESPONSE)
    snapshot, record = collect_market_data(
        "xyz:XLE", "1h",
        collector=market_data.HyperliquidCollector(authorization=_HL_AUTH),
        now=NOW, limit=5_000,
    )
    assert snapshot["depth_capped"] is True
    assert record["depth_capped"] is True


def test_binance_records_a_full_window_as_not_capped(monkeypatch):
    # The flag is additive, not a behaviour change: an existing collector that filled the
    # window still reports False, so nothing that reads the record starts meaning something
    # different.
    _patch_urlopen(monkeypatch, _KLINES_RESPONSE)
    snapshot, record = collect_market_data(
        "BTCUSDT", "1d", collector=BinanceFuturesCollector(authorization=_AUTH), now=NOW, limit=2
    )
    assert snapshot["depth_capped"] is False
    assert record["depth_capped"] is False


@pytest.mark.parametrize("symbol", ["xyz:XLE", "para:AVGO", "mkts:US500"])
def test_hip3_symbols_are_valid_for_this_venue_only(symbol):
    collector = market_data.HyperliquidCollector(authorization=_HL_AUTH)
    assert market_data._require_symbol(symbol, pattern=collector.symbol_pattern) == symbol
    # ...and the SAME name is refused for the Binance-shaped venue, which is the point of
    # a per-venue pattern rather than one permissive union.
    with pytest.raises(ToolBlocked) as exc:
        market_data._require_symbol(symbol)
    assert exc.value.reason_code == "INVALID_SYMBOL"


@pytest.mark.parametrize("symbol", ["BTCUSDT", "XLE", "XYZ:XLE", "xyz:", ":XLE", "xyz:xle"])
def test_non_hip3_symbols_are_refused_by_the_hyperliquid_venue(symbol):
    # Includes `BTCUSDT`: a name that is well-formed for the OTHER venue must not reach
    # this one's endpoint. Bare `XLE` too — the prefix is what separates `xyz:AVGO` from
    # `para:AVGO`, so a stripped name is ambiguous rather than convenient.
    collector = market_data.HyperliquidCollector(authorization=_HL_AUTH)
    with pytest.raises(ToolBlocked) as exc:
        market_data._require_symbol(symbol, pattern=collector.symbol_pattern)
    assert exc.value.reason_code == "INVALID_SYMBOL"


def test_hyperliquid_refuses_egress_without_authorization():
    # Constructing the collector directly must not bypass the gate.
    with pytest.raises(SafetyGateBlocked) as exc:
        market_data.HyperliquidCollector(authorization=None).collect(
            "xyz:XLE", "1h", limit=10, timeout_seconds=5
        )
    assert exc.value.reason_code == "NOT_AUTHORIZED"


def test_select_hyperliquid_by_its_own_opt_in_value(monkeypatch, tmp_path):
    # One env var, two venues, still a CHOICE (env-only since 2026-08-10): the value
    # names the venue, and each value opens only its own collector — the per-venue scope
    # the one-file-per-provider grants used to carry now lives in the opt-in VALUE.
    monkeypatch.setenv(MARKET_DATA_ENV, HYPERLIQUID)
    collector = select_market_data_collector(now="2026-08-03T00:00:00Z", root=tmp_path)
    assert isinstance(collector, market_data.HyperliquidCollector)

    monkeypatch.setenv(MARKET_DATA_ENV, BINANCE_FUTURES)
    collector = select_market_data_collector(now="2026-08-03T00:00:00Z", root=tmp_path)
    assert isinstance(collector, market_data.BinanceFuturesCollector)


def test_an_unknown_venue_selects_the_inert_mock(monkeypatch):
    # A typo reaches nothing rather than raising: the same fail-closed direction the
    # single-venue form had.
    monkeypatch.setenv(MARKET_DATA_ENV, "hyperliquid_typo")
    assert isinstance(select_market_data_collector(), MockMarketDataCollector)


@pytest.mark.parametrize("payload", ["not json", '{"a": 1}', "[[1, 2]]", '[{"t": 1}]', '[{"t":1,"T":2,"o":"x","c":"1","h":"1","l":"1","v":"1","n":1}]'])
def test_hyperliquid_malformed_response_fails_closed(monkeypatch, payload):
    _patch_urlopen(monkeypatch, payload)
    with pytest.raises(ToolError) as exc:
        market_data.HyperliquidCollector(authorization=_HL_AUTH).collect(
            "xyz:XLE", "1h", limit=10, timeout_seconds=5
        )
    assert exc.value.reason_code == "MALFORMED_RESULT"


def test_hyperliquid_transport_error_fails_closed(monkeypatch):
    _patch_urlopen(monkeypatch, urllib.error.URLError("connection refused"))
    with pytest.raises(ToolError) as exc:
        market_data.HyperliquidCollector(authorization=_HL_AUTH).collect(
            "xyz:XLE", "1h", limit=10, timeout_seconds=5
        )
    assert exc.value.reason_code == "TOOL_TRANSPORT"


def test_hyperliquid_sends_one_post_with_a_time_window(monkeypatch):
    # No paging: one request, and the window is a time range rather than a count, so the
    # `limit` -> `startTime` conversion is what the venue actually receives.
    seen: list = []

    def fake_urlopen(request, timeout):
        seen.append(request)
        return _FakeResp(_HL_RESPONSE)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    market_data.HyperliquidCollector(authorization=_HL_AUTH).collect(
        "xyz:XLE", "1h", limit=100, timeout_seconds=5
    )
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.full_url == "https://api.hyperliquid.xyz/info"
    body = json.loads(request.data.decode("utf-8"))
    assert body["type"] == "candleSnapshot"
    assert body["req"]["coin"] == "xyz:XLE"
    assert body["req"]["interval"] == "1h"
    # 101 hourly bars of span (the extra one covers the forming bar that gets dropped).
    assert body["req"]["endTime"] - body["req"]["startTime"] == 101 * 60 * 60_000


# === the no-backward-progress guard, pinned on every paging walk =====================
#
# `collect` has carried its stuck-venue pin since the guard landed; the other three
# backward-paging walks carried the same two guard lines with the same comment and no
# pin — exactly the shape a copy loses silently. Restructuring the four walks into one
# helper was considered and declined: their accumulate shapes, page params and transport
# postures genuinely differ (two classify, two plain-ToolError by the R3 rule), so the
# guard's PROPERTY is what gets pinned instead, per walk.


class _StuckFundingVenue:
    """Ignores endTime — always the same newest funding page."""

    def __init__(self):
        self.calls = 0

    def __call__(self, request, timeout):
        self.calls += 1
        return _FakeResp(json.dumps([
            {"fundingTime": 1_000_000_000_000 + i * 28_800_000, "fundingRate": "0.0001"}
            for i in range(10)
        ]))


def test_funding_refuses_to_spin_without_backward_progress(monkeypatch):
    venue = _StuckFundingVenue()
    monkeypatch.setattr("urllib.request.urlopen", venue)
    rows = BinanceFuturesCollector(authorization=_AUTH).funding_history(
        "BTCUSDT", records=30, timeout_seconds=5
    )
    assert venue.calls == 2, "second identical page must stop the walk, not the page budget"
    assert len(rows) == 10


class _StuckKlineVenue:
    """Ignores endTime — always the same newest kline page."""

    def __init__(self):
        self.calls = 0

    def __call__(self, request, timeout):
        self.calls += 1
        base = 1_000_000_000_000
        return _FakeResp(json.dumps([
            _kline(base + i * 86_400_000, base + (i + 1) * 86_400_000, 100.0 + i)
            for i in range(10)
        ]))


def test_derivative_klines_refuse_to_spin_without_backward_progress(monkeypatch):
    venue = _StuckKlineVenue()
    monkeypatch.setattr("urllib.request.urlopen", venue)
    rows = BinanceFuturesCollector(authorization=_AUTH).derivative_price_klines(
        "BTCUSDT", "1d", kind="mark", limit=30, timeout_seconds=5
    )
    assert venue.calls == 2, "second identical page must stop the walk, not the page budget"
    assert len(rows) == 10


class _StuckKlineVenue:
    """Ignores endTime — always the same newest kline page."""

    def __init__(self):
        self.calls = 0

    def __call__(self, request, timeout):
        self.calls += 1
        base = 1_000_000_000_000
        return _FakeResp(json.dumps([
            _kline(base + i * 86_400_000, base + (i + 1) * 86_400_000, 100.0 + i)
            for i in range(10)
        ]))


def test_derivative_klines_refuse_to_spin_without_backward_progress(monkeypatch):
    venue = _StuckKlineVenue()
    monkeypatch.setattr("urllib.request.urlopen", venue)
    rows = BinanceFuturesCollector(authorization=_AUTH).derivative_price_klines(
        "BTCUSDT", "1d", kind="mark", limit=30, timeout_seconds=5
    )
    assert venue.calls == 2, "second identical page must stop the walk, not the page budget"
    assert len(rows) == 10


class _StuckPositioningVenue:
    """Ignores endTime — always the same newest positioning page."""

    def __init__(self):
        self.calls = 0

    def __call__(self, request, timeout):
        self.calls += 1
        return _FakeResp(json.dumps([
            {"timestamp": 1_000_000_000_000 + i * 3_600_000,
             "longAccount": "0.6", "shortAccount": "0.4"}
            for i in range(10)
        ]))


def test_positioning_refuses_to_spin_without_backward_progress(monkeypatch):
    venue = _StuckPositioningVenue()
    monkeypatch.setattr("urllib.request.urlopen", venue)
    rows = BinanceFuturesCollector(authorization=_AUTH).positioning_history(
        "BTCUSDT", series="top_position", period="1h", limit=30, timeout_seconds=5
    )
    assert venue.calls == 2, "second identical page must stop the walk, not the page budget"
    assert len(rows) == 10
