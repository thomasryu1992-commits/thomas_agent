"""Tests for the feature distribution shift gate (⑩ DI regime gate)."""

from __future__ import annotations

import math

import pytest

from runtime.mvp_runtime.crypto.distribution_gate import (
    DI_FEATURES,
    DI_THRESHOLD,
    DISTRIBUTION_SHIFT,
    MIN_REFERENCE_BARS,
    compute_distribution_reference,
    dissimilarity_index,
    distribution_admits,
)


# ---------------------------------------------------------------------------
# compute_distribution_reference
# ---------------------------------------------------------------------------

class TestComputeDistributionReference:
    def test_computes_mean_and_std_for_each_feature(self):
        rows = [{"atr": 1.0, "adx": 20.0} for _ in range(50)]
        ref = compute_distribution_reference(rows, features=("atr", "adx"))
        assert "atr" in ref
        assert "adx" in ref
        assert ref["atr"]["mean"] == 1.0
        assert ref["atr"]["std"] == 0.0
        assert ref["atr"]["n"] == 50

    def test_varied_values_produce_nonzero_std(self):
        rows = [{"atr": float(i)} for i in range(100)]
        ref = compute_distribution_reference(rows, features=("atr",))
        assert ref["atr"]["std"] > 0

    def test_too_few_observations_omits_feature(self):
        rows = [{"atr": 1.0} for _ in range(MIN_REFERENCE_BARS - 1)]
        ref = compute_distribution_reference(rows, features=("atr",))
        assert "atr" not in ref

    def test_missing_feature_values_skipped(self):
        rows = [{"atr": 1.0}] * 40 + [{"adx": 5.0}] * 40
        ref = compute_distribution_reference(rows, features=("atr", "adx"))
        assert "atr" in ref
        assert "adx" in ref

    def test_non_numeric_values_skipped(self):
        rows = [{"atr": "bad"}] * 50
        ref = compute_distribution_reference(rows, features=("atr",))
        assert "atr" not in ref

    def test_nan_and_inf_values_skipped(self):
        rows = [{"atr": float("nan")}] * 30 + [{"atr": 1.0}] * 40
        ref = compute_distribution_reference(rows, features=("atr",))
        assert ref["atr"]["n"] == 40


# ---------------------------------------------------------------------------
# dissimilarity_index
# ---------------------------------------------------------------------------

class TestDissimilarityIndex:
    def test_zero_when_current_matches_reference(self):
        ref = {"atr": {"mean": 10.0, "std": 2.0, "n": 100}}
        di = dissimilarity_index({"atr": 10.0}, ref)
        assert di == 0.0

    def test_one_when_one_std_away(self):
        ref = {"atr": {"mean": 10.0, "std": 2.0, "n": 100}}
        di = dissimilarity_index({"atr": 12.0}, ref)
        assert di == 1.0

    def test_averages_across_features(self):
        ref = {
            "atr": {"mean": 10.0, "std": 2.0, "n": 100},
            "adx": {"mean": 25.0, "std": 5.0, "n": 100},
        }
        # atr: |z| = 1.0, adx: |z| = 3.0 → mean = 2.0
        di = dissimilarity_index({"atr": 12.0, "adx": 40.0}, ref)
        assert di == 2.0

    def test_returns_none_when_no_features_match(self):
        ref = {"atr": {"mean": 10.0, "std": 2.0, "n": 100}}
        assert dissimilarity_index({"rsi": 50.0}, ref) is None

    def test_skips_zero_std_features(self):
        ref = {"atr": {"mean": 10.0, "std": 0.0, "n": 100}}
        assert dissimilarity_index({"atr": 15.0}, ref) is None

    def test_missing_current_value_skipped(self):
        ref = {
            "atr": {"mean": 10.0, "std": 2.0, "n": 100},
            "adx": {"mean": 25.0, "std": 5.0, "n": 100},
        }
        di = dissimilarity_index({"atr": 12.0}, ref)
        assert di == 1.0


# ---------------------------------------------------------------------------
# distribution_admits
# ---------------------------------------------------------------------------

class TestDistributionAdmits:
    def _entry(self, reference=None):
        entry = {"strategy_id": "S001"}
        if reference is not None:
            entry["distribution_reference"] = reference
        return entry

    def test_no_reference_is_admitted(self):
        admitted, reason, di = distribution_admits(self._entry(), {"atr": 10.0})
        assert admitted is True
        assert reason is None
        assert di is None

    def test_empty_reference_is_admitted(self):
        admitted, reason, di = distribution_admits(self._entry({}), {"atr": 10.0})
        assert admitted is True

    def test_within_threshold_is_admitted(self):
        ref = {"atr": {"mean": 10.0, "std": 2.0, "n": 100}}
        admitted, reason, di = distribution_admits(self._entry(ref), {"atr": 12.0})
        assert admitted is True
        assert di == 1.0

    def test_beyond_threshold_is_refused(self):
        ref = {"atr": {"mean": 10.0, "std": 1.0, "n": 100}}
        # |z| = 5.0 > DI_THRESHOLD (3.0)
        admitted, reason, di = distribution_admits(
            self._entry(ref), {"atr": 15.0}
        )
        assert admitted is False
        assert reason == DISTRIBUTION_SHIFT
        assert di == 5.0

    def test_at_threshold_is_admitted(self):
        ref = {"atr": {"mean": 10.0, "std": 1.0, "n": 100}}
        # |z| = 3.0 = DI_THRESHOLD
        admitted, reason, di = distribution_admits(
            self._entry(ref), {"atr": 13.0}
        )
        assert admitted is True


# ---------------------------------------------------------------------------
# Integration: backtest produces a reference
# ---------------------------------------------------------------------------

class TestBacktestDistributionReference:
    def test_backtest_result_carries_distribution_reference(self):
        from runtime.mvp_runtime.crypto.factory import backtest_spec_pooled
        from runtime.mvp_runtime.crypto.strategy import StrategySpec

        spec = StrategySpec.from_dict({
            "schema_version": "strategy_spec.v1",
            "strategy_id": "test_di",
            "strategy_version": "1.0",
            "strategy_family": "breakout",
            "strategy_rule_hash": "",
            "generation_id": "g",
            "direction": "long",
            "symbol_scope": ["BTCUSDT"],
            "timeframe": "1d",
            "entry_rules": {"conditions": [
                {"feature": "rsi", "comparison": "<", "value": 90},
            ]},
            "exit_rules": {"stop_model": "atr", "stop_atr": 2.0, "target_atr": 3.0, "max_holding_bars": 10},
            "risk_constraints": {"max_risk_per_trade_R": 1.0},
        })
        candles = [
            {
                "open_time": f"2025-01-{i+1:02d}T00:00:00Z",
                "close_time": f"2025-01-{i+1:02d}T23:59:59Z",
                "open": 1000 + i, "high": 1010 + i, "low": 990 + i,
                "close": 1000 + i, "volume": 100.0,
            }
            for i in range(200)
        ]
        snapshot = {
            "snapshot_version": "0.1",
            "symbol": "BTCUSDT",
            "timeframe": "1d",
            "candles": candles,
            "candle_count": len(candles),
            "last_close": candles[-1]["close"],
            "last_candle_time": candles[-1]["close_time"],
            "source": "test",
            "venue": "binance",
            "is_synthetic": False,
            "created_at": "2025-01-01T00:00:00Z",
        }
        result = backtest_spec_pooled(spec, [snapshot])
        assert "distribution_reference" in result
        ref = result["distribution_reference"]
        assert isinstance(ref, dict)
        if ref:
            for stats in ref.values():
                assert "mean" in stats
                assert "std" in stats
                assert "n" in stats
