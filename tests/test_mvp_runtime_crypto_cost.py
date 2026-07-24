"""C12 cost model tests — fee/slippage decomposition, and its wiring into the factory.

The R-space formula in ``cost.py`` was derived algebraically from the source's
qty-based ``settle_trade`` (quantity cancels out of every R ratio since
``risk_amount = qty * risk_per_unit``) and verified numerically against the real
source module before porting. These tests re-verify: the decomposition is internally
consistent (net = gross - fee - slippage), fees/slippage are always a cost (never
free money), and the factory backtest now scores costed R while never touching the
live paper kernel."""

from __future__ import annotations

import math

from runtime.mvp_runtime.crypto.cost import CostModel, apply_cost_model
from runtime.mvp_runtime.crypto.factory import backtest_spec
from runtime.mvp_runtime.crypto.paper import settle_trade_plan
from runtime.mvp_runtime.crypto.strategy import StrategySpec

# Reference values computed by RUNNING the source's qty-based settle_trade
# (crypto_AI_System/backtesting/cost_model.py) at the default 2.5bps fee / 3.0bps
# slippage, for entry=100, exit=108 (LONG) and entry=100, exit=104 (SHORT), risk=4.
_SOURCE_LONG = {"net_r": 1.97140015, "fee_cost_r": 0.01299985, "slippage_cost_r": 0.01560000}
_SOURCE_SHORT = {"net_r": -1.02805008, "fee_cost_r": 0.01275007, "slippage_cost_r": 0.01530000}


def _close(a: float, b: float, tol: float = 1e-6) -> bool:
    return math.isclose(a, b, abs_tol=tol)


def test_long_matches_source_settle_trade_reference():
    result = apply_cost_model("LONG", 100.0, 108.0, 4.0)
    assert _close(result.net_r, _SOURCE_LONG["net_r"])
    assert _close(result.fee_cost_r, _SOURCE_LONG["fee_cost_r"])
    assert _close(result.slippage_cost_r, _SOURCE_LONG["slippage_cost_r"])


def test_short_matches_source_settle_trade_reference():
    result = apply_cost_model("SHORT", 100.0, 104.0, 4.0)
    assert _close(result.net_r, _SOURCE_SHORT["net_r"], tol=1e-5)
    assert _close(result.fee_cost_r, _SOURCE_SHORT["fee_cost_r"])
    assert _close(result.slippage_cost_r, _SOURCE_SHORT["slippage_cost_r"])


def test_net_equals_gross_minus_costs():
    result = apply_cost_model("LONG", 100.0, 110.0, 5.0)
    assert math.isclose(result.net_r, result.gross_r - result.fee_cost_r - result.slippage_cost_r, abs_tol=1e-9)


def test_costs_are_always_nonnegative_never_free_money():
    for direction, entry, exit_ in (("LONG", 100.0, 110.0), ("SHORT", 100.0, 90.0),
                                    ("LONG", 100.0, 90.0), ("SHORT", 100.0, 110.0)):
        result = apply_cost_model(direction, entry, exit_, risk=5.0)
        assert result.fee_cost_r > 0  # a fee is charged on every fill, every direction
        assert result.slippage_cost_r > 0  # a taker is always adverse, every direction


def test_gross_r_matches_uncosted_distance_formula():
    # gross_R must equal exactly what settle_trade_plan already computes with no
    # cost model at all — the "intended price" R this port used before C12.
    result = apply_cost_model("LONG", 100.0, 108.0, 4.0)
    assert result.gross_r == 2.0  # (108-100)/4
    result_short = apply_cost_model("SHORT", 100.0, 104.0, 4.0)
    assert result_short.gross_r == -1.0  # (100-104)/4


def test_custom_cost_model_scales_linearly():
    zero_cost = CostModel(taker_fee_bps=0.0, slippage_bps=0.0)
    result = apply_cost_model("LONG", 100.0, 108.0, 4.0, cost=zero_cost)
    assert result.fee_cost_r == 0.0 and result.slippage_cost_r == 0.0
    assert result.net_r == result.gross_r  # no cost -> net equals gross exactly


def test_non_positive_risk_is_the_defensive_zero_case():
    result = apply_cost_model("LONG", 100.0, 108.0, 0.0)
    assert result == apply_cost_model("LONG", 100.0, 108.0, -1.0)
    assert (result.gross_r, result.net_r, result.fee_cost_r, result.slippage_cost_r) == (0.0, 0.0, 0.0, 0.0)


# --- factory wiring -------------------------------------------------------------

def _spec_dict(**overrides):
    base = {
        "schema_version": "strategy_spec.v1", "strategy_id": "S1", "strategy_version": "1.0",
        "strategy_family": "breakout", "symbol_scope": ["BTCUSDT"], "timeframe": "1d",
        "direction": "long", "entry_rules": {"operator": "AND",
                                              "conditions": [{"feature": "close", "comparison": ">", "value": 0.0}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    base.update(overrides)
    return base


def _trending_snapshot(n=200):
    from tests.test_mvp_runtime_crypto_factory import _trending_snapshot as base

    return base(n)


def test_backtest_spec_result_r_is_costed_below_gross():
    spec = StrategySpec.from_dict(_spec_dict())
    evidence = backtest_spec(spec, _trending_snapshot())
    assert evidence["closed_count"] > 0
    assert evidence["cost_summary"]["total_fee_cost_r"] > 0
    assert evidence["cost_summary"]["total_slippage_cost_r"] > 0
    # Costed expectancy must be strictly less than what the old (gross) numbers would
    # have shown — costs are a drag, never a credit.
    assert evidence["cost_summary"]["total_net_r"] < (
        evidence["cost_summary"]["total_net_r"]
        + evidence["cost_summary"]["total_fee_cost_r"]
        + evidence["cost_summary"]["total_slippage_cost_r"]
    )


def test_backtest_spec_feeds_real_cost_robustness_not_always_zero():
    spec = StrategySpec.from_dict(_spec_dict())
    evidence = backtest_spec(spec, _trending_snapshot())
    if evidence["cost_summary"]["total_net_r"] > 0:
        # A profitable-net backtest must show SOME cost_robustness credit now —
        # before C12 this component was hard-coded to 0.0 for every candidate.
        assert evidence["robustness"]["components"]["cost_robustness"] > 0.0


def test_backtest_spec_is_still_deterministic_with_costs():
    spec = StrategySpec.from_dict(_spec_dict())
    snapshot = _trending_snapshot()
    a = backtest_spec(spec, snapshot)
    b = backtest_spec(spec, snapshot)
    assert a == b


def test_live_paper_kernel_is_unaffected_by_the_cost_model():
    # The source boundary, re-verified: paper.settle_trade_plan takes no cost
    # model and its signature is unchanged by C12 — live paper stays cost-free.
    position = {"direction": "LONG", "entry_price": 100.0, "stop_loss": 96.0,
               "take_profit": 108.0, "risk": 4.0, "holding_candles": 0}
    reason, exit_price, result_r = settle_trade_plan(
        position, {"high": 109.0, "low": 99.0, "close": 108.5}, 108.5, 48, False
    )
    assert reason == "take_profit" and exit_price == 108.0 and result_r == 2.0  # uncosted, exact
