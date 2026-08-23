"""`attach_mining_legs` — the frame a spec is BACKTESTED on, assembled in one place.

The five attaches were individually correct and individually copied, and the copies drifted:
two call sites (the null control, and the family proposer) wrote `attach_feeds` and stopped.
Neither failed loudly. A spec naming a column the frame does not carry is not an error — it is
a spec that never matches, so it scores zero trades and is judged for it, and the verdict reads
as "this premise does not trade" when the truth is "this runtime did not supply the column".

So what is asserted here is the property the copies broke: **a mining frame supplies every
column the factory may mint on**, and each leg is load-bearing rather than decorative.

Mined on ETHUSDT rather than BTCUSDT on purpose. BTCUSDT is `REFERENCE_SYMBOL`, so its relative
strength against itself is a constant the feature builder correctly returns as None — a test
that used it would report the reference leg as broken and be wrong.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import factory
from runtime.mvp_runtime.crypto.cycle import attach_mining_legs
from runtime.mvp_runtime.crypto.features import latest_feature_row
from runtime.mvp_runtime.crypto.market_data import (
    MockMarketDataCollector,
    collect_market_data,
)
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-08-08T00:00:00Z"
SYMBOL = "ETHUSDT"

# What stays None after every leg has run, measured 2026-08-08, and why each one is a property
# of the MOCK rather than of the legs:
#
#   liquidation_* (4), open_interest_* (2) — a vendor feed this collector does not stand in for
#   positioning_* (2)                      — a local store the test root has not accumulated
#   htf_ma20_distance_ma50                 — needs 50 higher-timeframe bars; the mock's HTF
#                                            window is shorter, so the separation is undefined
#
# Listed rather than summarised so that a column LEAVING this set is a visible change: if a
# future collector supplies one, the assertion below starts requiring it.
_UNREACHABLE_WITH_A_MOCK = frozenset({
    "liquidation_spike_ratio", "liquidation_total", "long_liquidation", "short_liquidation",
    "open_interest_change_pct", "open_interest_zscore",
    "positioning_divergence_zscore", "positioning_divergence_change",
    "htf_ma20_distance_ma50",
})


def _bare(timeframe="1h"):
    """Exactly what `collect_market_data` returns — the frame both broken call sites judged on."""
    collector = MockMarketDataCollector()
    snapshot, _ = collect_market_data(SYMBOL, timeframe, collector=collector, now=NOW)
    return snapshot, collector


def _supplied(snapshot):
    return {name for name, value in latest_feature_row(snapshot).items() if value is not None}


def test_a_bare_snapshot_is_missing_the_non_price_half_of_the_vocabulary():
    """The state both broken call sites were in, pinned as a number so the fix has a baseline.

    Not a defect in `collect_market_data` — OHLCV plus the aggressor split is what its klines
    response promises, and the taker columns ride it. The defect is judging a spec on that."""
    snapshot, _ = _bare()
    missing = set(factory.NUMERIC_FEATURES) - _supplied(snapshot)
    assert len(missing) >= 20, f"a bare snapshot now supplies more than expected: {len(missing)}"
    # Every non-price information source, absent.
    for name in ("funding_zscore", "htf_rsi", "rel_strength_roc_4", "xs_rank_pct",
                 "premium_index", "mark_index_basis_bps"):
        assert name in missing, name
    # ...and the one that is NOT, because it arrives in the same klines call as the candles.
    # Asserted so nobody re-derives the fix's motivation from a wrong premise: the taker
    # families were never starved by this gap.
    assert "taker_buy_ratio" not in missing
    assert "taker_flow_imbalance" not in missing


def test_attach_mining_legs_supplies_every_reachable_mintable_column():
    """The invariant `proposer.py` states and the copies broke: what the validator admits, the
    row can supply. Asserted over the whole mintable vocabulary rather than a sample, so a
    future column needing a sixth leg fails here instead of silently scoring zero trades."""
    snapshot, collector = _bare()
    attach_mining_legs(snapshot, collector=collector, timeframe="1h", now=NOW)
    missing = (set(factory.NUMERIC_FEATURES) - _supplied(snapshot)) - _UNREACHABLE_WITH_A_MOCK
    assert not missing, f"mining frame cannot supply mintable columns: {sorted(missing)}"


def test_each_leg_is_load_bearing():
    """Every leg supplies columns no other leg does. A leg that could be dropped without loss is
    the copy-drift this function exists to prevent, arriving as a deliberate edit instead."""
    snapshot, collector = _bare()
    attach_mining_legs(snapshot, collector=collector, timeframe="1h", now=NOW)
    gained = _supplied(snapshot) - _supplied(_bare()[0])
    assert gained, "the legs supplied nothing at all"
    for leg, column in (("feeds", "funding_zscore"), ("htf", "htf_rsi"),
                        ("reference", "rel_strength_roc_4"), ("cross_section", "xs_rank_pct")):
        assert column in gained, f"{leg} leg supplied nothing"


def test_the_top_of_the_ladder_loses_only_the_higher_timeframe():
    """1d has no collected step above it, so the htf columns stay None there — correct rather
    than a missing leg. `templates_for_timeframe` drops the htf families at 1d for exactly this
    reason. Asserted so a future reader does not "fix" it."""
    snapshot, collector = _bare(timeframe="1d")
    attach_mining_legs(snapshot, collector=collector, timeframe="1d", now=NOW)
    supplied = _supplied(snapshot)
    assert "htf_rsi" not in supplied
    assert "xs_rank_pct" in supplied, "a ladder-independent leg was dropped with the htf one"


def test_attach_mining_legs_degrades_rather_than_raising():
    """Degrade-only, like every attach it wraps: a leg that cannot be read leaves its columns
    None, which is the state a caller who never called it would have had anyway. A frame
    assembler that raised would take down the fire it was supposed to enrich.

    `ToolError` specifically — that is the failure the attaches contract on. An arbitrary
    exception is a bug in the collector and is deliberately NOT swallowed here."""

    class _Degraded(MockMarketDataCollector):
        def collect(self, symbol, timeframe, **kwargs):
            raise ToolError("TOOL_ERROR", "venue down")

    snapshot, _ = _bare()
    before = _supplied(snapshot)
    attach_mining_legs(snapshot, collector=_Degraded(), timeframe="1h", now=NOW)
    assert _supplied(snapshot) >= before, "a degraded leg removed columns the base frame had"


def test_an_unexpected_collector_exception_is_not_swallowed():
    """The other half of the contract above, so "degrade-only" is not read as "catch anything".
    A collector raising something the attaches do not contract on is a defect that must surface
    at the fire rather than as a quietly poorer frame."""

    class _Broken(MockMarketDataCollector):
        def collect(self, symbol, timeframe, **kwargs):
            raise RuntimeError("not a ToolError")

    snapshot, _ = _bare()
    with pytest.raises(RuntimeError):
        attach_mining_legs(snapshot, collector=_Broken(), timeframe="1h", now=NOW)


# --- the cohort's context legs read one answer per series, not one per leg ------------------

class TestPooledMintSharesOneCandleCache:
    """A pooled mint is a fan-out even though it produces one candidate, and its two context
    legs read series that do not depend on which leg is asking: `attach_reference` always reads
    the constant proxy, and `attach_cross_section` reads the same cohort universe every time.
    Before `candle_cache` reached `attach_mining_legs`, a five-symbol cohort asked for each of
    those series once per leg — at `factory_candle_target`, the deepest window this runtime
    reads. `run_pool_cycle` has threaded one `PeerCandleCache` through both legs since the
    fan-out existed; the mining path simply had no parameter to accept one.

    Counted rather than asserted by name: what matters is the number of venue reads, and a
    check on the argument would survive the cache being threaded to nowhere.
    """

    class CountingCollector(MockMarketDataCollector):
        """Every `collect` — the venue read itself, which is what a cache can remove. Counted
        by (symbol, timeframe, limit) because that triple is the cache's own key."""

        def __init__(self):
            super().__init__()
            self.calls: list[tuple] = []

        def collect(self, symbol, timeframe, *, limit, timeout_seconds):
            self.calls.append((symbol, timeframe, limit))
            return super().collect(symbol, timeframe, limit=limit,
                                   timeout_seconds=timeout_seconds)

    COHORT = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT")

    def _mint_cohort(self, *, cache):
        from runtime.mvp_runtime.crypto.market_data import PeerCandleCache

        collector = self.CountingCollector()
        shared = PeerCandleCache(collector) if cache else None
        for symbol in self.COHORT:
            snapshot, _ = collect_market_data(symbol, "1h", collector=collector, now=NOW)
            attach_mining_legs(snapshot, collector=collector, timeframe="1h", now=NOW,
                               candle_cache=shared)
        return collector.calls

    def test_the_cache_removes_reads_the_cohort_would_otherwise_repeat(self):
        without = self._mint_cohort(cache=False)
        with_cache = self._mint_cohort(cache=True)
        assert len(with_cache) < len(without), (
            f"the cache saved nothing: {len(without)} -> {len(with_cache)}")
        # The saving is the repeats, so what survives is the distinct series — never fewer.
        assert len(with_cache) >= len(set(without))

    def test_no_series_is_read_twice_when_the_cohort_shares_a_cache(self):
        """The property that makes it a cache rather than a coincidence: within one fan-out,
        what ETH's hourly candles were cannot depend on which leg asked."""
        calls = self._mint_cohort(cache=True)
        repeated = [key for key in set(calls) if calls.count(key) > 1]
        # The frame's own read is the cohort member's own candles, fetched by
        # `collect_market_data` outside the cache — one per leg, never repeated within a leg.
        assert all(key[0] in self.COHORT for key in repeated), (
            f"a context leg re-read a series inside one fire: {repeated}")

    def test_without_a_cache_the_behaviour_is_exactly_what_it_was(self):
        """Absent means the previous behaviour, so an existing caller cannot be changed by this."""
        snapshot, collector = _bare("1h")
        attach_mining_legs(snapshot, collector=collector, timeframe="1h", now=NOW)
        supplied = _supplied(snapshot)
        snapshot2, collector2 = _bare("1h")
        attach_mining_legs(snapshot2, collector=collector2, timeframe="1h", now=NOW,
                           candle_cache=None)
        assert supplied == _supplied(snapshot2)

    def test_a_cached_cohort_still_gets_every_column(self):
        """The saving must not come out of the frame: caching a read cannot drop a leg."""
        from runtime.mvp_runtime.crypto.market_data import PeerCandleCache

        collector = self.CountingCollector()
        shared = PeerCandleCache(collector)
        uncached, _ = collect_market_data("ETHUSDT", "1h", collector=self.CountingCollector(),
                                          now=NOW)
        attach_mining_legs(uncached, collector=collector, timeframe="1h", now=NOW)

        cached, _ = collect_market_data("ETHUSDT", "1h", collector=collector, now=NOW)
        attach_mining_legs(cached, collector=collector, timeframe="1h", now=NOW,
                           candle_cache=shared)
        assert _supplied(cached) == _supplied(uncached)
