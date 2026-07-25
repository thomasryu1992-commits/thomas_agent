"""Out-of-sample holdout — the gate that makes ROBUST mean something.

Before this, every number the evidence carried was computed on the same bars the
candidate was mined on, and promotion picks the highest scorer out of a growing store.
Selecting the maximum over many in-sample scores is how noise gets promoted, and no
in-sample statistic can detect it. These tests pin the two halves of the fix: the split
is real (the score never sees the tail) and ROBUST is unreachable without out-of-sample
confirmation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.crypto import factory
from runtime.mvp_runtime.crypto.robustness import (
    HOLDOUT_CONFIRMED,
    HOLDOUT_CONTRADICTED,
    HOLDOUT_INSUFFICIENT,
    HOLDOUT_UNCONFIRMED,
    MIN_HOLDOUT_TRADES,
    PROVISIONAL,
    ROBUST,
    holdout_status,
    score_robustness,
)
from runtime.mvp_runtime.crypto.strategy import StrategySpec

NOW_DT = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)


def _spec_dict(**overrides):
    base = {
        "schema_version": "strategy_spec.v1",
        "strategy_id": "S1", "strategy_version": "1.0", "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"], "timeframe": "1d", "direction": "long",
        "entry_rules": {"operator": "AND",
                        "conditions": [{"feature": "close", "comparison": ">", "value_from": "ma20"}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    base.update(overrides)
    return base


def _snapshot(prices):
    step = timedelta(days=1)
    last_close = NOW_DT - timedelta(hours=1)
    n = len(prices)
    candles = []
    for i, price in enumerate(prices):
        close_time = last_close - (n - 1 - i) * step
        candles.append({
            "open_time": timeutil.format_iso(close_time - step),
            "open": price, "high": price + 1.5, "low": price - 1.5,
            "close": price, "volume": 10.0 + (i % 7),
            "close_time": timeutil.format_iso(close_time),
        })
    return {"symbol": "BTCUSDT", "timeframe": "1d", "candles": candles, "is_synthetic": False}


# --- the split ----------------------------------------------------------------

def test_split_is_deterministic_and_leaves_a_real_tail():
    assert factory.holdout_split_index(200) == 140
    assert factory.holdout_split_index(200) == factory.holdout_split_index(200)
    assert 0 < factory.holdout_split_index(100) < 100


def test_a_window_too_short_holds_nothing_out():
    """Rather than pretend a two-bar tail proved something: everything trains, and the
    verdict layer then reports the holdout as unusable."""
    short = factory.MIN_BARS_FOR_HOLDOUT - 1
    assert factory.holdout_split_index(short) == short


def test_the_score_never_sees_the_holdout_bars():
    """The load-bearing property. Changing ONLY the tail must leave every scored number
    identical — if it moves, the holdout is not out-of-sample."""
    rising = [100.0 + i for i in range(200)]
    a = factory.backtest_spec(StrategySpec.from_dict(_spec_dict()), _snapshot(rising))

    tail_crashed = list(rising)
    for i in range(factory.holdout_split_index(200), 200):   # rewrite the tail only
        tail_crashed[i] = 50.0
    b = factory.backtest_spec(StrategySpec.from_dict(_spec_dict()), _snapshot(tail_crashed))

    scored = ("closed_count", "expectancy", "champion_score", "bars_replayed", "walk_forward")
    for field in scored:
        assert a[field] == b[field], f"{field} moved when only the holdout changed"
    assert a["holdout"] != b["holdout"]                      # ...but the holdout noticed


# --- the gate -----------------------------------------------------------------

@pytest.mark.parametrize("holdout,expected", [
    (None, HOLDOUT_UNCONFIRMED),
    ({}, HOLDOUT_UNCONFIRMED),
    ({"closed_count": MIN_HOLDOUT_TRADES - 1, "total_R": 9.0}, HOLDOUT_INSUFFICIENT),
    ({"closed_count": MIN_HOLDOUT_TRADES, "total_R": 1.5}, HOLDOUT_CONFIRMED),
    ({"closed_count": MIN_HOLDOUT_TRADES, "total_R": 0.0}, HOLDOUT_CONTRADICTED),
    ({"closed_count": 20, "total_R": -4.0}, HOLDOUT_CONTRADICTED),
    ({"closed_count": "many", "total_R": 5.0}, HOLDOUT_INSUFFICIENT),   # unusable shape
])
def test_holdout_status_classification(holdout, expected):
    assert holdout_status(holdout) == expected


def _strong_inputs():
    """Inputs that clear ROBUST_SCORE_THRESHOLD on the in-sample components alone."""
    spec = StrategySpec.from_dict(_spec_dict())
    metrics = {"trade_count": 400, "total_net_r": 100.0, "fee_cost_r": 1.0, "slippage_cost_r": 1.0}
    walk_forward = {"walk_forward_pass_rate": 1.0, "temporal_stability": 1.0}
    regimes = {"regimes_traded": ["TREND_UP", "RANGE"], "profitable_regime_count": 2}
    return spec, metrics, walk_forward, regimes


@pytest.mark.parametrize("holdout,expected_verdict", [
    ({"closed_count": 10, "total_R": 6.0}, ROBUST),          # confirmed out-of-sample
    ({"closed_count": 10, "total_R": -6.0}, PROVISIONAL),    # scored well, failed forward
    ({"closed_count": 1, "total_R": 2.0}, PROVISIONAL),      # too thin to confirm
    (None, PROVISIONAL),                                     # never evaluated
])
def test_robust_requires_out_of_sample_confirmation(holdout, expected_verdict):
    spec, metrics, walk_forward, regimes = _strong_inputs()
    record = score_robustness(spec, metrics, walk_forward, regimes, holdout=holdout)
    assert record["verdict"] == expected_verdict


def test_the_holdout_gates_the_verdict_without_moving_the_score():
    """The number stays comparable across candidates; only the tier gets stricter."""
    spec, metrics, walk_forward, regimes = _strong_inputs()
    confirmed = score_robustness(spec, metrics, walk_forward, regimes,
                                 holdout={"closed_count": 10, "total_R": 6.0})
    contradicted = score_robustness(spec, metrics, walk_forward, regimes,
                                    holdout={"closed_count": 10, "total_R": -6.0})
    assert confirmed["robustness_score"] == contradicted["robustness_score"]
    assert (confirmed["verdict"], contradicted["verdict"]) == (ROBUST, PROVISIONAL)
    assert "holdout_contradicts_in_sample_edge" in contradicted["warnings"]
    assert "holdout_contradicts_in_sample_edge" not in confirmed["warnings"]


def test_candidates_predating_the_holdout_are_not_silently_promoted():
    """Every stored candidate was scored before this existed. None may read as ROBUST
    on that old evidence — absence of out-of-sample proof is not proof."""
    spec, metrics, walk_forward, regimes = _strong_inputs()
    record = score_robustness(spec, metrics, walk_forward, regimes)  # no holdout argument
    assert record["holdout_status"] == HOLDOUT_UNCONFIRMED
    assert record["verdict"] == PROVISIONAL
    assert "no_out_of_sample_evidence" in record["warnings"]


def test_full_backtest_reports_a_holdout_block():
    rising = [100.0 + i for i in range(200)]
    evidence = factory.backtest_spec(StrategySpec.from_dict(_spec_dict()), _snapshot(rising))
    assert evidence["holdout"]["bars"] == 60
    assert evidence["robustness"]["holdout_status"] in {
        HOLDOUT_CONFIRMED, HOLDOUT_CONTRADICTED, HOLDOUT_INSUFFICIENT}
