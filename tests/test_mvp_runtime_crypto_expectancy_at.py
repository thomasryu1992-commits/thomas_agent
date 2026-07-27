"""Re-deriving a candidate's expectancy at a taker rate it was not scored under.

Raising the taker default from the ported 2.5 bps to the venue's measured 5.0 split the
candidate store: 224 rows on the live machine keep numbers scored at the old rate, and
`backtest_evidence` is durable so nothing re-scores them. Re-running the backtest is not
available either — the snapshot that produced the evidence is not stored, only its hash.

The conversion needs neither, because `cost.apply_cost_model` computes the fee as

    fee_cost_r = (entry_fill + exit_fill) * taker_fee_bps / 10000 / risk

which is linear in the rate, with fills that depend only on slippage.

These tests do not assert that algebra — they check it, by running the same backtest at both
rates and requiring the derived number to equal the measured one. An argument about linearity
is exactly the kind of thing that is convincing and wrong.
"""

from __future__ import annotations

import math

from runtime.mvp_runtime.crypto.cost import CostModel
from runtime.mvp_runtime.crypto.factory import backtest_spec
from runtime.mvp_runtime.crypto.pool import expectancy_at
from runtime.mvp_runtime.crypto.strategy import StrategySpec

from tests.test_mvp_runtime_crypto_cost import _spec_dict, _trending_snapshot


def _candidate_at(taker: float) -> dict:
    spec = StrategySpec.from_dict(_spec_dict())
    evidence = backtest_spec(spec, _trending_snapshot(),
                             cost=CostModel(taker_fee_bps=taker, slippage_bps=3.0))
    return {"candidate_id": f"cand_{taker}", "backtest_evidence": evidence}


def test_the_derived_expectancy_equals_a_real_rerun_at_that_rate():
    """The load-bearing claim, measured rather than argued."""
    scored_low = _candidate_at(2.5)
    scored_high = _candidate_at(5.0)
    assert scored_low["backtest_evidence"]["closed_count"] > 0

    derived = expectancy_at(scored_low, taker_fee_bps=5.0)
    measured = scored_high["backtest_evidence"]["expectancy"]
    assert derived is not None
    assert math.isclose(derived, measured, abs_tol=1e-8), f"{derived} != {measured}"


def test_it_holds_in_the_other_direction_too():
    """Down as well as up — the relation is a scaling, not a one-way correction."""
    scored_high = _candidate_at(5.0)
    derived = expectancy_at(scored_high, taker_fee_bps=2.5)
    measured = _candidate_at(2.5)["backtest_evidence"]["expectancy"]
    assert derived is not None
    assert math.isclose(derived, measured, abs_tol=1e-8)


def test_deriving_at_the_same_rate_is_the_stored_number():
    scored = _candidate_at(2.5)
    assert math.isclose(
        expectancy_at(scored, taker_fee_bps=2.5),
        scored["backtest_evidence"]["expectancy"], abs_tol=1e-8,
    )


def test_a_higher_rate_never_improves_the_edge():
    scored = _candidate_at(2.5)
    assert expectancy_at(scored, taker_fee_bps=5.0) <= scored["backtest_evidence"]["expectancy"]


def test_a_record_without_a_cost_summary_derives_nothing():
    """None, never the stored number relabelled as if it had been converted."""
    assert expectancy_at({"backtest_evidence": {"expectancy": 0.5, "closed_count": 10}},
                         taker_fee_bps=5.0) is None
    assert expectancy_at({}, taker_fee_bps=5.0) is None


def test_a_zero_rate_basis_derives_nothing():
    """Nothing to scale from, so scaling would be a division by zero dressed as a figure."""
    record = {"backtest_evidence": {
        "closed_count": 10,
        "cost_summary": {"total_net_r": 1.0, "total_fee_cost_r": 0.0,
                         "cost_model": {"taker_fee_bps": 0.0, "slippage_bps": 3.0}},
    }}
    assert expectancy_at(record, taker_fee_bps=5.0) is None


def test_no_closed_trades_derives_nothing():
    record = {"backtest_evidence": {
        "closed_count": 0,
        "cost_summary": {"total_net_r": 0.0, "total_fee_cost_r": 0.0,
                         "cost_model": {"taker_fee_bps": 2.5, "slippage_bps": 3.0}},
    }}
    assert expectancy_at(record, taker_fee_bps=5.0) is None
