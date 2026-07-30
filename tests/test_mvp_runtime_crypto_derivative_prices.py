"""Derivative price series (mark / index / premium) and cross-asset reference context.

Two feature groups, one shared theme: both replace a **fabricated constant** with a measured
value, and both must go indeterminate rather than back to a constant when the measurement is
unavailable.

- ``mark_price``/``index_price``/``mark_index_basis_bps`` carried the source system's no-feed
  fallbacks (close, close, a hardcoded ``0.0``) while ``mark_index_basis_bps`` sat in the
  factory's mintable vocabulary. A literal condition on an always-zero column does not select
  on anything — it is decided at mint time — so the column was worse than absent.
- ``rel_strength_roc_4``/``ref_correlation`` would be exactly ``0.0`` and ``1.0`` for the
  market proxy measured against itself, so those columns are empty for the proxy rather than
  trivially true.

The alignment rules get their own tests because there are now **three** of them in this module
and picking the wrong one is silent: exact open-time join for same-grid series (here),
backward as-of for own-cadence feeds (funding, OI), and close-time keying for the coarser HTF
grid. A test that only checked "a value arrived" would pass under all three.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import factory, features
from runtime.mvp_runtime.crypto.cycle import attach_feeds, attach_reference
from runtime.mvp_runtime.crypto.features import build_feature_rows
from runtime.mvp_runtime.crypto.indicators import rolling_correlation
from runtime.mvp_runtime.crypto.market_data import (
    REFERENCE_SYMBOL,
    BinanceFuturesCollector,
    MockMarketDataCollector,
    collect_market_data,
)
from runtime.mvp_runtime.crypto.strategy import StrategySpec, evaluate_spec

NOW = "2026-07-30T00:00:00Z"


def _candle(hour, close=100.0, *, day="2026-07-20"):
    return {
        "open_time": f"{day}T{hour:02d}:00:00Z", "close_time": f"{day}T{hour + 1:02d}:00:00Z",
        "open": close, "high": close + 1.0, "low": close - 1.0, "close": close, "volume": 10.0,
    }


def _spec(feature, comparison, value, *, family="x"):
    return StrategySpec.from_dict({
        "schema_version": "strategy_spec.v1", "strategy_id": "S001", "strategy_version": "1.0",
        "strategy_family": family, "direction": "long", "timeframe": "1h",
        "symbol_scope": ["ETHUSDT"],
        "entry_rules": {"operator": "AND", "conditions": [
            {"feature": feature, "comparison": comparison, "value": value},
        ]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.0, "target_atr": 2.0,
                       "max_holding_bars": 12},
    })


# --- the collector ------------------------------------------------------------

def test_derivative_page_reads_only_open_close_and_close_time():
    """These rows reuse the kline shape but the venue fills the volume slots with zeros,
    so only indices 0, 4 and 6 may be trusted."""
    raw = ('[[1750000000000,"100.0","110.0","90.0","104.5",'
           '"0",1750003599999,"0",0,"0","0","0"]]')
    page = BinanceFuturesCollector()._parse_derivative_page(raw, kind="mark", now_ms=1750003600000)
    assert page == {1750000000000: 104.5}


def test_still_forming_derivative_bar_is_dropped():
    raw = '[[1750000000000,"100.0","110.0","90.0","104.5","0",1750003599999,"0",0,"0","0","0"]]'
    assert BinanceFuturesCollector()._parse_derivative_page(raw, kind="mark", now_ms=1) == {}


def test_negative_premium_is_kept():
    """The premium index is legitimately negative when the perpetual trades under its index.
    Rejecting or flooring it would erase exactly the half of the distribution a fade family
    on the long side needs."""
    raw = '[[1750000000000,"0","0","0","-0.00042","0",1750003599999,"0",0,"0","0","0"]]'
    page = BinanceFuturesCollector()._parse_derivative_page(raw, kind="premium", now_ms=1750003600000)
    assert page == {1750000000000: -0.00042}


def test_unknown_derivative_kind_refuses():
    from runtime.mvp_runtime.errors import ToolError

    with pytest.raises(ToolError):
        MockMarketDataCollector().derivative_price_klines(
            "BTCUSDT", "1h", kind="funding", limit=5, timeout_seconds=1
        )


def test_mock_derivative_series_shares_the_ohlcv_grid():
    """A mock whose derivative bars landed on different open times would make the join look
    broken in tests that are actually testing the join."""
    collector = MockMarketDataCollector()
    candles = collector.collect("ETHUSDT", "1h", limit=20, timeout_seconds=1).candles
    marks = collector.derivative_price_klines("ETHUSDT", "1h", kind="mark", limit=20, timeout_seconds=1)
    assert [c.open_time for c in candles] == [row["timestamp"] for row in marks]


# --- alignment: exact open-time join, not as-of --------------------------------

def test_same_grid_join_is_exact_and_does_not_carry_forward():
    """A bar the venue did not report has no mark price — it does NOT inherit the previous
    bar's. Carrying forward is right for funding (its own 8h cadence) and wrong here, where a
    gap means the venue was silent about that bar and a substituted value would fabricate a
    basis for it."""
    candles = [_candle(h) for h in range(4)]
    snapshot = {
        "symbol": "ETHUSDT", "timeframe": "1h", "candles": candles,
        # Bars 0 and 3 only — 1 and 2 are absent from the series.
        "mark_prices": [
            {"timestamp": candles[0]["open_time"], "value": 100.5},
            {"timestamp": candles[3]["open_time"], "value": 103.5},
        ],
        "index_prices": [{"timestamp": c["open_time"], "value": 100.0} for c in candles],
    }
    rows = build_feature_rows(snapshot)
    assert [r["mark_price"] for r in rows] == [100.5, None, None, 103.5]
    # And the basis needs both legs, so it is None exactly where the mark is.
    assert [r["mark_index_basis_bps"] is None for r in rows] == [False, True, True, False]


def test_basis_is_computed_in_bps_from_both_legs():
    candles = [_candle(0)]
    rows = build_feature_rows({
        "symbol": "ETHUSDT", "timeframe": "1h", "candles": candles,
        "mark_prices": [{"timestamp": candles[0]["open_time"], "value": 100.10}],
        "index_prices": [{"timestamp": candles[0]["open_time"], "value": 100.00}],
    })
    assert rows[0]["mark_index_basis_bps"] == pytest.approx(10.0)  # 0.10/100 = 10 bps


def test_non_positive_index_price_is_indeterminate_not_a_giant_basis():
    candles = [_candle(0)]
    rows = build_feature_rows({
        "symbol": "ETHUSDT", "timeframe": "1h", "candles": candles,
        "mark_prices": [{"timestamp": candles[0]["open_time"], "value": 100.0}],
        "index_prices": [{"timestamp": candles[0]["open_time"], "value": 0.0}],
    })
    assert rows[0]["mark_index_basis_bps"] is None


# --- absence: indeterminate, never the old constants ---------------------------

def test_absent_series_yield_none_not_close_and_not_zero():
    rows = build_feature_rows({"symbol": "ETHUSDT", "timeframe": "1h",
                              "candles": [_candle(h) for h in range(6)]})
    for column in ("mark_price", "index_price", "mark_index_basis_bps",
                   "premium_index", "premium_index_zscore"):
        assert all(r[column] is None for r in rows), f"{column} fabricated a value"


def test_a_basis_spec_cannot_enter_without_the_series():
    """The end-to-end consequence of removing the 0.0 fallback. This threshold matches any
    real basis, so a match here would prove the column was fabricated."""
    spec = _spec("mark_index_basis_bps", ">=", -1e9)
    row = features.latest_feature_row({"symbol": "ETHUSDT", "timeframe": "1h",
                                       "candles": [_candle(h) for h in range(6)]})
    assert evaluate_spec(spec, row).matched is False


def test_degraded_fetch_is_present_and_empty_and_reason_coded():
    class _BrokenDerivatives(MockMarketDataCollector):
        def derivative_price_klines(self, symbol, timeframe, *, kind, limit, timeout_seconds):
            from runtime.mvp_runtime.errors import ToolError

            raise ToolError("TOOL_TRANSPORT", "boom")

    snapshot = {"symbol": "ETHUSDT", "timeframe": "1h", "candles": [_candle(0)]}
    reasons, status = attach_feeds(
        snapshot, collector=_BrokenDerivatives(), liquidation_feed=None, now=NOW,
    )
    assert {"MARK_PRICE_DEGRADED", "INDEX_PRICE_DEGRADED", "PREMIUM_INDEX_DEGRADED"} <= set(reasons)
    assert snapshot["mark_prices"] == [] and snapshot["premium_index"] == []
    assert status["mark_prices"] == "degraded"


def test_attach_feeds_requests_the_series_at_the_candle_depth():
    """Depth is derived from the candle count rather than a constant, which is what keeps the
    factory's deep replay and the live cycle's short window on one code path."""
    seen: list[int] = []

    class _RecordingCollector(MockMarketDataCollector):
        def derivative_price_klines(self, symbol, timeframe, *, kind, limit, timeout_seconds):
            seen.append(limit)
            return super().derivative_price_klines(
                symbol, timeframe, kind=kind, limit=limit, timeout_seconds=timeout_seconds
            )

    snapshot, _ = collect_market_data(
        "ETHUSDT", "1h", collector=_RecordingCollector(), now=NOW, limit=37
    )
    attach_feeds(snapshot, collector=_RecordingCollector(), liquidation_feed=None, now=NOW)
    assert seen == [37, 37, 37]


# --- premium index at bar resolution ------------------------------------------

def test_premium_index_moves_every_bar_where_funding_steps():
    """The reason the series is worth three requests. `funding_rate` is an 8h event carried
    forward, so on an hourly frame it repeats; the premium index is measured on each bar."""
    collector = MockMarketDataCollector()
    snapshot, _ = collect_market_data("ETHUSDT", "1h", collector=collector, now=NOW, limit=48)
    attach_feeds(snapshot, collector=collector, liquidation_feed=None, now=NOW)
    rows = build_feature_rows(snapshot)

    funding = [r["funding_rate"] for r in rows if r["funding_rate"] is not None]
    premium = [r["premium_index"] for r in rows if r["premium_index"] is not None]
    assert len(premium) >= 40, "the premium series did not populate"
    # Distinct values per populated bar: the premium changes far more often than funding.
    assert len(set(premium)) > len(set(funding))


def test_premium_columns_are_mintable_and_the_raw_prices_stay_ordered_comparisons():
    assert {"premium_index", "premium_index_zscore"} <= factory.NUMERIC_FEATURES
    # mark/index were already mintable and stay so — they are only meaningful against each
    # other or against close, which `value_from` expresses.
    assert {"mark_price", "index_price", "mark_index_basis_bps"} <= factory.NUMERIC_FEATURES


# --- cross-asset reference ----------------------------------------------------

def test_rolling_correlation_matches_hand_computed_values():
    # Perfectly co-moving and perfectly opposed pairs.
    assert rolling_correlation([1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0], 4, 2)[-1] == pytest.approx(1.0)
    assert rolling_correlation([1.0, 2.0, 3.0, 4.0], [8.0, 6.0, 4.0, 2.0], 4, 2)[-1] == pytest.approx(-1.0)


def test_rolling_correlation_is_none_where_undefined():
    # Zero variance on a leg: no correlation exists, and 0.0 would claim independence.
    assert rolling_correlation([1.0, 1.0, 1.0], [1.0, 2.0, 3.0], 3, 2)[-1] is None
    # Too few usable pairs.
    assert rolling_correlation([1.0, None, 3.0], [1.0, 2.0, None], 3, 2)[-1] is None


def test_rolling_correlation_counts_pairs_not_observations():
    """A bar the reference is missing contributes nothing, rather than half an observation."""
    left = [1.0, 2.0, None, 4.0, 5.0]
    right = [2.0, 4.0, 6.0, None, 10.0]
    out = rolling_correlation(left, right, 5, 3)
    assert out[-1] == pytest.approx(1.0)  # pairs at 0, 1, 4 only — all perfectly co-moving


def test_relative_strength_is_the_excess_over_the_reference():
    candles = [_candle(h, close=100.0 + 2.0 * h) for h in range(8)]
    reference = [_candle(h, close=100.0 + 1.0 * h) for h in range(8)]
    rows = build_feature_rows({
        "symbol": "ETHUSDT", "timeframe": "1h", "candles": candles,
        "reference_candles": reference, "reference_symbol": REFERENCE_SYMBOL,
    })
    last = rows[-1]
    assert last["ref_roc_4"] is not None and last["roc_4"] is not None
    assert last["rel_strength_roc_4"] == pytest.approx(last["roc_4"] - last["ref_roc_4"])
    assert last["rel_strength_roc_4"] > 0, "the faster series must read as outperforming"


def test_reference_columns_are_empty_for_the_reference_symbol_itself():
    """Against itself the benchmark's excess is exactly 0.0 and its correlation exactly 1.0 —
    fabricated constants a mined condition could match on. Undefined, not trivially true."""
    candles = [_candle(h, close=100.0 + h) for h in range(8)]
    rows = build_feature_rows({
        "symbol": REFERENCE_SYMBOL, "timeframe": "1h", "candles": candles,
        "reference_candles": candles, "reference_symbol": REFERENCE_SYMBOL,
    })
    for column in features.REFERENCE_COLUMNS:
        assert all(r[column] is None for r in rows), f"{column} was fabricated for the proxy"


def test_reference_join_is_exact_on_open_time():
    candles = [_candle(h, close=100.0 + h) for h in range(6)]
    reference = [_candle(h, close=100.0 + h) for h in (0, 1, 5)]  # bars 2-4 absent
    rows = build_feature_rows({
        "symbol": "ETHUSDT", "timeframe": "1h", "candles": candles,
        "reference_candles": reference, "reference_symbol": REFERENCE_SYMBOL,
    })
    assert [r["ref_market_regime"] is None for r in rows] == [False, False, True, True, True, False]


def test_absent_reference_leaves_every_column_none():
    rows = build_feature_rows({"symbol": "ETHUSDT", "timeframe": "1h",
                              "candles": [_candle(h) for h in range(6)]})
    for column in features.REFERENCE_COLUMNS:
        assert all(r[column] is None for r in rows)


def test_attach_reference_skips_the_proxy_and_never_raises():
    class _BrokenCollector(MockMarketDataCollector):
        def collect(self, symbol, timeframe, *, limit, timeout_seconds):
            from runtime.mvp_runtime.errors import ToolError

            raise ToolError("TOOL_TRANSPORT", "boom")

    # The proxy itself: no fetch, no reason code, no key.
    proxy = {"symbol": REFERENCE_SYMBOL, "timeframe": "1h", "candles": [_candle(0)]}
    assert attach_reference(proxy, collector=_BrokenCollector(), now=NOW) is None
    assert "reference_candles" not in proxy

    # Another symbol, failing fetch: degrade with a code, key stays absent.
    other = {"symbol": "ETHUSDT", "timeframe": "1h", "candles": [_candle(0)]}
    assert attach_reference(other, collector=_BrokenCollector(), now=NOW) == "REFERENCE_DEGRADED"
    assert "reference_candles" not in other

    # Working collector: the series lands.
    ok = {"symbol": "ETHUSDT", "timeframe": "1h", "candles": [_candle(0)]}
    assert attach_reference(ok, collector=MockMarketDataCollector(), now=NOW) is None
    assert ok["reference_symbol"] == REFERENCE_SYMBOL and ok["reference_candles"]


def test_reference_families_are_not_minted_for_the_reference_symbol():
    """The htf_*/session_* rule applied to the third column group that goes None by context —
    here by SYMBOL rather than by timeframe, which is why the gate takes one."""
    for_proxy = {t.family for t in factory.templates_for_timeframe("1h", symbol=REFERENCE_SYMBOL)}
    for_other = {t.family for t in factory.templates_for_timeframe("1h", symbol="ETHUSDT")}
    assert not (factory.REFERENCE_FAMILIES & for_proxy)
    assert factory.REFERENCE_FAMILIES <= for_other
    # A caller that names no symbol is asking for the library, not a mintable set.
    assert factory.REFERENCE_FAMILIES <= {t.family for t in factory.templates_for_timeframe("1h")}


def test_generate_batch_honours_the_symbol_gate():
    minted = set()
    for n in range(16):
        for spec in factory.generate_batch(
            f"GEN-{n:03d}", seed=n, symbol=REFERENCE_SYMBOL, timeframe="1h"
        )["specs"]:
            minted.add(spec["strategy_family"])
    assert not (factory.REFERENCE_FAMILIES & minted)


def test_the_new_families_mint_and_backtest_on_a_reference_enriched_frame():
    collector = MockMarketDataCollector()
    snapshot, _ = collect_market_data("ETHUSDT", "1h", collector=collector, now=NOW, limit=400)
    attach_feeds(snapshot, collector=collector, liquidation_feed=None, now=NOW)
    attach_reference(snapshot, collector=collector, now=NOW, limit=400)

    minted: dict[str, dict] = {}
    for n in range(16):
        for spec in factory.generate_batch(
            f"GEN-{n:03d}", seed=n, symbol="ETHUSDT", timeframe="1h"
        )["specs"]:
            minted.setdefault(spec["strategy_family"], spec)

    wanted = factory.REFERENCE_FAMILIES | {"premium_fade_long", "premium_fade_short"}
    assert wanted <= set(minted), f"never minted: {sorted(wanted - set(minted))}"
    for family in sorted(wanted):
        evidence = factory.backtest_spec(StrategySpec.from_dict(minted[family]), snapshot)
        assert evidence["bars_replayed"] > 0
