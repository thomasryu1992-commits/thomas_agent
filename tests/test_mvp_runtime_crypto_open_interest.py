"""Open interest — the positioning leg beside funding and liquidations.

Funding says what a position costs to hold; liquidations say what was forcibly closed;
open interest says how much is **outstanding**. It rides the Coinalyze feed the runtime
already authorizes, so this widens what is read, never what may be reached.

The rules under test: raw OI never becomes a mintable threshold (it is venue-scale and
means nothing across symbols), the normalized derivatives do, and an absent or failed
feed leaves every column indeterminate — the liquidation posture, so no oi_* condition
can match on a fabricated value."""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import factory
from runtime.mvp_runtime.crypto.cycle import attach_feeds
from runtime.mvp_runtime.crypto.features import latest_feature_row
from runtime.mvp_runtime.crypto.market_data import (
    OPEN_INTEREST_DEGRADED,
    MockMarketDataCollector,
    NoLiquidationFeed,
)
from runtime.mvp_runtime.crypto.strategy import StrategySpec, evaluate_spec
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-07-25T12:00:00Z"

OI_COLUMNS = ("open_interest", "open_interest_change_pct", "open_interest_zscore")


def _candles(n=40):
    return [{
        "open_time": f"2026-06-{d:02d}T00:00:00Z" if d <= 30 else f"2026-07-{d - 30:02d}T00:00:00Z",
        "close_time": f"2026-06-{d + 1:02d}T00:00:00Z" if d < 30 else f"2026-07-{d - 29:02d}T00:00:00Z",
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0,
    } for d in range(1, n + 1)]


def _snapshot(oi_series=None, **extra):
    snap = {"symbol": "BTCUSDT", "timeframe": "1d", "candles": _candles(), **extra}
    if oi_series is not None:
        snap["open_interest"] = oi_series
    return snap


def _oi_series(values):
    out = []
    for i, v in enumerate(values, start=1):
        day = i if i <= 30 else i - 30
        month = 6 if i <= 30 else 7
        out.append({"timestamp": f"2026-{month:02d}-{day:02d}T00:00:00Z", "open_interest": float(v)})
    return out


# --- features -----------------------------------------------------------------

def test_normalized_derivatives_are_computed_from_the_series():
    row = latest_feature_row(_snapshot(_oi_series([1000 + 50 * i for i in range(40)])))
    assert row["open_interest"] == pytest.approx(1000 + 50 * 39)
    assert row["open_interest_change_pct"] > 0          # OI still climbing
    assert row["open_interest_zscore"] is not None


def test_a_falling_series_reports_a_negative_change():
    row = latest_feature_row(_snapshot(_oi_series([2000 - 20 * i for i in range(40)])))
    assert row["open_interest_change_pct"] < 0


@pytest.mark.parametrize("snapshot_kwargs,case", [
    ({}, "feed absent"),
    ({"oi_series": []}, "fetch failed: key present, empty"),
])
def test_no_usable_series_leaves_every_column_indeterminate(snapshot_kwargs, case):
    """Never a constant. A fabricated zero would let `change_pct <= x` match on nothing
    — the liquidation_spike_ratio hazard this deliberately does not repeat."""
    snapshot = _snapshot(**snapshot_kwargs) if snapshot_kwargs else _snapshot()
    row = latest_feature_row(snapshot)
    assert all(row[col] is None for col in OI_COLUMNS), case


def _oi_spec(conditions, **overrides):
    base = {
        "schema_version": "strategy_spec.v1",
        "strategy_id": "S1", "strategy_version": "1.0", "strategy_family": "oi_squeeze_long",
        "symbol_scope": ["BTCUSDT"], "timeframe": "1d", "direction": "long",
        "entry_rules": {"operator": "AND", "conditions": conditions},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 3.0, "max_holding_bars": 12},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("feature", ["open_interest_change_pct", "open_interest_zscore"])
@pytest.mark.parametrize("comparison,value", [(">", -1e12), ("<", 1e12)])
def test_an_oi_condition_cannot_match_without_the_feed(feature, comparison, value):
    """Both permissive directions: indeterminate satisfies neither."""
    row = latest_feature_row(_snapshot())
    spec = StrategySpec.from_dict(_oi_spec([{"feature": feature, "comparison": comparison, "value": value}]))
    assert evaluate_spec(spec, row).matched is False


# --- factory vocabulary + families --------------------------------------------

def test_normalized_oi_features_are_mintable():
    spec = StrategySpec.from_dict(_oi_spec([
        {"feature": "open_interest_change_pct", "comparison": ">=", "value": 0.03},
        {"feature": "open_interest_zscore", "comparison": ">=", "value": 1.0},
    ]))
    assert factory.validate_strategy(spec)["approved_for_backtest"] is True


def test_raw_open_interest_stays_unmintable():
    """It is a venue-scale quantity: a threshold on it means something different on
    every symbol, and something different again after the venue grows. The row carries
    it as evidence; the factory may not build a rule on it."""
    assert "open_interest" not in factory.NUMERIC_FEATURES
    spec = StrategySpec.from_dict(_oi_spec([
        {"feature": "open_interest", "comparison": ">", "value": 1_000_000.0}]))
    assert "BLOCK_UNKNOWN_FEATURE" in factory.validate_strategy(spec)["block_reasons"]


def test_oi_families_are_present_and_generate_valid_specs():
    families = {t.family for t in factory.templates_for_timeframe("1h")}
    assert factory.OI_FAMILIES <= families
    batch = factory.generate_batch("GEN-001", seed=5, timeframe="1h")
    assert batch["accepted_count"] == batch["requested_count"]
    for spec_dict in batch["specs"]:
        assert factory.validate_strategy(StrategySpec.from_dict(spec_dict))["approved_for_backtest"] is True


# --- the feed seam ------------------------------------------------------------

class _FeedWithOI:
    feed_id = "coinalyze_market_data"

    def liquidation_history(self, symbol, *, days, timeout_seconds):
        return [{"timestamp": "2026-07-01T00:00:00Z", "long_liquidation": 1.0, "short_liquidation": 2.0}]

    def open_interest_history(self, symbol, *, days, timeout_seconds):
        return [{"timestamp": "2026-07-01T00:00:00Z", "open_interest": 1234.0}]


class _FeedWhoseOIFails(_FeedWithOI):
    def open_interest_history(self, symbol, *, days, timeout_seconds):
        raise ToolError("TOOL_TRANSPORT", "open-interest request failed or timed out")


def test_attach_feeds_collects_open_interest_through_the_same_feed():
    snapshot = _snapshot()
    reasons, status = attach_feeds(snapshot, collector=MockMarketDataCollector(),
                                   liquidation_feed=_FeedWithOI(), now=NOW)
    assert status["open_interest"] == "ok"
    assert snapshot["open_interest"] and OPEN_INTEREST_DEGRADED not in reasons


def test_a_failed_open_interest_fetch_degrades_only_itself():
    """Liquidations can be fine while OI is not — separate key, separate reason code."""
    snapshot = _snapshot()
    reasons, status = attach_feeds(snapshot, collector=MockMarketDataCollector(),
                                   liquidation_feed=_FeedWhoseOIFails(), now=NOW)
    assert status["liquidations"] == "ok" and status["open_interest"] == "degraded"
    assert OPEN_INTEREST_DEGRADED in reasons
    assert snapshot["open_interest"] == []          # series semantics: present, empty
    assert latest_feature_row(snapshot)["open_interest_change_pct"] is None


def test_the_null_feed_reports_open_interest_absent():
    snapshot = _snapshot()
    reasons, status = attach_feeds(snapshot, collector=MockMarketDataCollector(),
                                   liquidation_feed=NoLiquidationFeed(), now=NOW)
    assert status["open_interest"] == "absent"
    assert "open_interest" not in snapshot          # absent ≠ degraded
    assert OPEN_INTEREST_DEGRADED not in reasons
