"""One vendor read per symbol per fan-out, because forty to read ten broke the last symbols.

Both Coinalyze series are per-SYMBOL — `liquidation_history` and `open_interest_history` take
a symbol and a day count and nothing else. But `cycle.attach_feeds` runs once per (symbol,
TIMEFRAME), so the shipped 20-context fan-out asked for each symbol's series four times. The
redundancy was invisible downstream: the series is aligned onto each timeframe's candles after
the fetch, so four identical responses produced four correct frames.

It stopped being invisible when the hourly OI store added five more requests per fire.
Measured on the first fire after that deployed, in context order: BNB/BTC/DOGE returned `ok`
and ETH/SOL came back `degraded` on **every** Coinalyze series — including the daily open
interest and the liquidations the router already depended on. Contexts are visited in a fixed
order, so a per-window request cap always lands on whoever is last, and the early symbols look
healthy while the late ones silently lose features.

Under test: the cache collapses a symbol's repeated reads to one, does not leak across fires,
re-raises a refusal identically for every context, and does not re-ask after one.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto.market_data import (
    OI_INTERVAL_1H,
    OI_INTERVAL_DAILY,
    PerSymbolFeedCache,
)
from runtime.mvp_runtime.errors import ToolError

TIMEFRAMES = ("15m", "1h", "4h", "1d")
SYMBOLS = ("BNBUSDT", "BTCUSDT", "DOGEUSDT", "ETHUSDT", "SOLUSDT")


class _Vendor:
    """Counts reads, and can refuse a named symbol the way a request cap does."""

    feed_id = "coinalyze_market_data"
    network_egress = True

    def __init__(self, *, refuse=()):
        self.liq_calls = []
        self.oi_calls = []
        self._refuse = set(refuse)

    def liquidation_history(self, symbol, *, days, timeout_seconds):
        self.liq_calls.append(symbol)
        if symbol in self._refuse:
            raise ToolError("TOOL_TRANSPORT", "liquidation request failed or timed out")
        return [{"timestamp": "2026-07-29T00:00:00Z", "long_liquidation": 1.0, "short_liquidation": 2.0}]

    def open_interest_history(self, symbol, *, days, timeout_seconds, interval=OI_INTERVAL_DAILY):
        self.oi_calls.append((symbol, interval))
        if symbol in self._refuse:
            raise ToolError("TOOL_TRANSPORT", "open-interest request failed or timed out")
        return [{"timestamp": "2026-07-29T00:00:00Z", "open_interest": 100.0}]


def _fan_out(cache):
    """What a pool fire actually does: every timeframe of every symbol, in order."""
    for symbol in SYMBOLS:
        for _ in TIMEFRAMES:
            cache.liquidation_history(symbol, days=520, timeout_seconds=10)
            cache.open_interest_history(symbol, days=520, timeout_seconds=10)


def test_a_twenty_context_fan_out_reads_each_symbol_once():
    """The regression in one number: 40 vendor requests become 10."""
    vendor = _Vendor()
    cache = PerSymbolFeedCache(vendor)
    _fan_out(cache)

    assert len(vendor.liq_calls) == len(SYMBOLS) == 5
    assert len(vendor.oi_calls) == len(SYMBOLS) == 5
    assert sorted(vendor.liq_calls) == sorted(SYMBOLS)
    assert cache.calls == 10


def test_every_context_still_gets_the_series():
    """Cheaper must not mean thinner: the 4h context reads the same rows the 15m one did."""
    cache = PerSymbolFeedCache(_Vendor())
    first = cache.open_interest_history("BTCUSDT", days=520, timeout_seconds=10)
    again = cache.open_interest_history("BTCUSDT", days=520, timeout_seconds=10)
    assert again == first
    assert first[0]["open_interest"] == 100.0


def test_the_hourly_series_is_cached_apart_from_the_daily_one():
    """Same symbol, different question. Collapsing them would feed the store daily rows."""
    vendor = _Vendor()
    cache = PerSymbolFeedCache(vendor)
    cache.open_interest_history("BTCUSDT", days=520, timeout_seconds=10)
    cache.open_interest_history("BTCUSDT", days=3, timeout_seconds=10, interval=OI_INTERVAL_1H)
    assert vendor.oi_calls == [("BTCUSDT", OI_INTERVAL_DAILY), ("BTCUSDT", OI_INTERVAL_1H)]


def test_a_refusal_reaches_every_context_and_is_asked_once():
    """A vendor that just refused this symbol will refuse it again in the same second, and
    re-asking three more times per fire is how the cap was reached. But each context must
    still record its own reason code, so the refusal is re-raised unchanged."""
    vendor = _Vendor(refuse={"SOLUSDT"})
    cache = PerSymbolFeedCache(vendor)
    for _ in TIMEFRAMES:
        with pytest.raises(ToolError) as exc:
            cache.liquidation_history("SOLUSDT", days=520, timeout_seconds=10)
        assert exc.value.reason_code == "TOOL_TRANSPORT"
    assert vendor.liq_calls == ["SOLUSDT"], "one ask, four refusals"


def test_one_symbol_refused_does_not_poison_the_others():
    vendor = _Vendor(refuse={"SOLUSDT"})
    cache = PerSymbolFeedCache(vendor)
    assert cache.liquidation_history("BTCUSDT", days=520, timeout_seconds=10)
    with pytest.raises(ToolError):
        cache.liquidation_history("SOLUSDT", days=520, timeout_seconds=10)
    assert cache.liquidation_history("ETHUSDT", days=520, timeout_seconds=10)


def test_a_fresh_cache_asks_again():
    """The cache is per fan-out. Carrying it across fires would serve a stale day."""
    vendor = _Vendor()
    _fan_out(PerSymbolFeedCache(vendor))
    _fan_out(PerSymbolFeedCache(vendor))
    assert len(vendor.liq_calls) == 10, "each fire reads each symbol once"


def test_the_pool_fan_out_actually_wraps_the_feed(tmp_path):
    """The wiring, not just the wrapper. A cache nobody constructs caches nothing, and the
    symptom of removing this line is invisible until a vendor starts refusing the tail."""
    from runtime.mvp_runtime.control import ControlStore
    from runtime.mvp_runtime.crypto.cycle import run_pool_cycle
    from runtime.mvp_runtime.crypto.market_data import MockMarketDataCollector
    from runtime.mvp_runtime.crypto.paper import DryRunPaperStore

    vendor = _Vendor()
    # No pool installed: the fan-out falls back to its single default context, which is enough
    # to prove the wrap exists — the four reads of that one context collapse to one.
    for _ in range(2):
        run_pool_cycle(
            collector=MockMarketDataCollector(), store=DryRunPaperStore(),
            now="2026-07-30T06:00:00Z", root=tmp_path,
            control_store=ControlStore(tmp_path), liquidation_feed=vendor,
        )
    # Two fires, one default context each, one liquidation read per fire.
    assert len(vendor.liq_calls) == 2, vendor.liq_calls


def test_the_wrapper_is_transparent_to_the_feed_checks_callers_make():
    """`attach_feeds` and the store both gate on `feed_id != "none"`, so a wrapper that hid
    it would silently turn the feed off rather than cache it."""
    cache = PerSymbolFeedCache(_Vendor())
    assert cache.feed_id == "coinalyze_market_data"
    assert cache.network_egress is True
    assert hasattr(cache, "open_interest_history")
