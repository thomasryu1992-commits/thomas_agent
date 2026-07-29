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


def _candidate_at(taker: float, maker: float = 2.0) -> dict:
    spec = StrategySpec.from_dict(_spec_dict())
    evidence = backtest_spec(spec, _trending_snapshot(),
                             cost=CostModel(taker_fee_bps=taker, slippage_bps=3.0,
                                            maker_fee_bps=maker))
    return {"candidate_id": f"cand_{taker}_{maker}", "backtest_evidence": evidence}


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


# --- the maker split (2026-07-28) ------------------------------------------------

def test_the_rescale_ignores_the_maker_portion():
    """Since the target exit became a maker LIMIT, `total_fee_cost_r` is a mixture. Scaling the
    whole mixture by a taker ratio would charge the maker leg a rate it never faced."""
    candidate = _candidate_at(2.5)
    summary = candidate["backtest_evidence"]["cost_summary"]
    assert summary["total_maker_fee_cost_r"] > 0.0, "fixture must actually exercise a maker exit"

    naive = (
        summary["total_net_r"] - summary["total_fee_cost_r"] * (5.0 / 2.5 - 1.0)
    ) / candidate["backtest_evidence"]["closed_count"]
    assert not math.isclose(expectancy_at(candidate, taker_fee_bps=5.0), naive, abs_tol=1e-9)


def test_a_record_without_the_maker_field_is_read_as_all_taker():
    """Every pre-2026-07-28 candidate. Absent is not unknown: that model had no maker leg, so
    the whole fee was taker by construction and the old algebra is exactly right for it."""
    candidate = _candidate_at(2.5)
    summary = candidate["backtest_evidence"]["cost_summary"]
    summary["total_fee_cost_r"] -= summary.pop("total_maker_fee_cost_r")

    expected = (
        summary["total_net_r"] - summary["total_fee_cost_r"] * (5.0 / 2.5 - 1.0)
    ) / candidate["backtest_evidence"]["closed_count"]
    assert math.isclose(expectancy_at(candidate, taker_fee_bps=5.0), expected, abs_tol=1e-9)


def test_an_unreadable_maker_share_refuses_rather_than_guesses():
    """Present but not a number means the split is unknown, and any rescale would be a guess
    about real money. Fail-closed: None, not a best effort."""
    candidate = _candidate_at(2.5)
    candidate["backtest_evidence"]["cost_summary"]["total_maker_fee_cost_r"] = "unknown"
    assert expectancy_at(candidate, taker_fee_bps=5.0) is None


# --- rescaling the maker leg too -------------------------------------------------
#
# `DEFAULT_MAKER_FEE_BPS` is Binance's PUBLISHED standard rate, not a figure measured on this
# account — no maker fill has been placed yet. Its error direction is the unsafe one, so the
# first live maker fill has to be able to replace it. These pin that the replacement converts
# existing candidates exactly, rather than splitting the store a third time.

def test_the_derived_expectancy_at_a_new_maker_rate_equals_a_real_rerun():
    """The same measured-not-argued check as the taker axis, on the leg that is unmeasured."""
    scored_low = _candidate_at(5.0, maker=2.0)
    assert scored_low["backtest_evidence"]["cost_summary"]["total_maker_fee_cost_r"] > 0.0

    derived = expectancy_at(scored_low, taker_fee_bps=5.0, maker_fee_bps=4.0)
    measured = _candidate_at(5.0, maker=4.0)["backtest_evidence"]["expectancy"]
    assert derived is not None
    assert math.isclose(derived, measured, abs_tol=1e-8), f"{derived} != {measured}"


def test_both_legs_rescale_together():
    """Neither axis is privileged: change both rates at once and the result still equals a
    re-run at both. A per-axis fix that only worked one at a time would pass every test above
    and fail the day the venue's two rates move together."""
    scored = _candidate_at(2.5, maker=2.0)
    derived = expectancy_at(scored, taker_fee_bps=5.0, maker_fee_bps=3.5)
    measured = _candidate_at(5.0, maker=3.5)["backtest_evidence"]["expectancy"]
    assert derived is not None
    assert math.isclose(derived, measured, abs_tol=1e-8), f"{derived} != {measured}"


def test_a_higher_maker_rate_never_improves_the_edge():
    """The direction that makes this worth building: if the real maker rate is above the
    published 2.0, the honest number is lower than the one on file."""
    scored = _candidate_at(5.0, maker=2.0)
    assert (expectancy_at(scored, taker_fee_bps=5.0, maker_fee_bps=4.0)
            < scored["backtest_evidence"]["expectancy"])


def test_omitting_the_maker_rate_leaves_that_leg_alone():
    """Back-compatible by construction: the taker-only call is still the taker-only answer."""
    scored = _candidate_at(2.5, maker=2.0)
    assert math.isclose(
        expectancy_at(scored, taker_fee_bps=5.0),
        expectancy_at(scored, taker_fee_bps=5.0, maker_fee_bps=2.0), abs_tol=1e-10,
    )


def test_a_record_with_no_maker_leg_is_untouched_by_a_maker_rate():
    """Every pre-2026-07-28 candidate. A zero maker share has nothing to rescale, so the answer
    is the taker-only one — arithmetic, not an assumption about what that model would have paid."""
    candidate = _candidate_at(2.5)
    summary = candidate["backtest_evidence"]["cost_summary"]
    summary["total_fee_cost_r"] -= summary.pop("total_maker_fee_cost_r")
    summary["cost_model"].pop("maker_fee_bps")

    assert math.isclose(
        expectancy_at(candidate, taker_fee_bps=5.0, maker_fee_bps=9.9),
        expectancy_at(candidate, taker_fee_bps=5.0), abs_tol=1e-10,
    )


def test_a_maker_share_with_no_recorded_maker_rate_refuses():
    """A ratio needs its denominator. Fees were charged on a maker leg at a rate the record
    does not name, so rescaling it would invent the rate it had paid."""
    candidate = _candidate_at(2.5, maker=2.0)
    assert candidate["backtest_evidence"]["cost_summary"]["total_maker_fee_cost_r"] > 0.0
    candidate["backtest_evidence"]["cost_summary"]["cost_model"].pop("maker_fee_bps")
    assert expectancy_at(candidate, taker_fee_bps=5.0, maker_fee_bps=4.0) is None
    # ...and the taker-only question is still answerable: that axis lost nothing.
    assert expectancy_at(candidate, taker_fee_bps=5.0) is not None
