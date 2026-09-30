"""Re-pricing a candidate's backtest at a slippage rate it was not scored under (review C2).

`REMAINING_WORK.md` §F8 gave the arithmetic on 2026-08-06: slippage cost in R is linear in the rate,
so ``net - slippage * (r / 3 - 1)`` re-prices a candidate with no replay. Five days later the stop leg
got its own rate, and ``cost_summary.total_slippage_cost_r`` became a sum over two rates whose split
is not recorded. `candidate_ranking.net_at_slippage` therefore answers with one figure for a record
scored at one rate and with a range for a record scored at two.

These tests do not assert that algebra. They charge the same trades again through
`cost.apply_cost_model` at the new rate and require the answer to sit where the function says.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto.candidate_ranking import (
    STRESS_SLIPPAGE_BPS, net_at_slippage, slippage_breakeven_bps,
)
from runtime.mvp_runtime.crypto.cost import CostModel, apply_cost_model

# Fees and carry are charged on the fills, which move with slippage. At 23.5 bps that is about 0.2%
# of a fee cost of a few hundredths of an R: well under this, and far under the ranges below.
SECOND_ORDER = 5e-4

STOP, TARGET, TIME = "stop_loss", "take_profit", "time_exit"


def _trade(direction, entry, risk, reason, time_r=0.4):
    """One trade leaving at its stop (one risk unit against it), at a 2R target, or on time."""
    sign = 1.0 if direction == "LONG" else -1.0
    move = {STOP: -1.0, TARGET: 2.0, TIME: time_r}[reason]
    return direction, entry, entry + sign * move * risk, risk, reason


MIXED = [
    _trade("LONG", 100.0, 0.8, STOP), _trade("SHORT", 250.0, 1.5, STOP), _trade("LONG", 40.0, 0.2, TARGET),
    _trade("SHORT", 3000.0, 21.0, TIME), _trade("LONG", 62000.0, 410.0, STOP), _trade("LONG", 1.8, 0.012, TIME, -0.3),
    _trade("SHORT", 95.0, 0.6, TARGET), _trade("LONG", 140.0, 1.1, STOP),
]
ALL_STOPS = [_trade(d, e, r, STOP) for d, e, r, _ in
             [("LONG", 100.0, 0.8, 0), ("SHORT", 250.0, 1.5, 0), ("SHORT", 3000.0, 21.0, 0), ("LONG", 140.0, 1.1, 0)]]
NO_STOPS = [t for t in MIXED if t[4] != STOP]
# A book that makes money before slippage: four targets, two stops, one time exit.
WINNING = [
    _trade("LONG", 100.0, 0.8, TARGET), _trade("SHORT", 250.0, 1.5, TARGET), _trade("LONG", 40.0, 0.2, TARGET),
    _trade("SHORT", 95.0, 0.6, TARGET), _trade("LONG", 62000.0, 410.0, STOP), _trade("SHORT", 3000.0, 21.0, STOP),
    _trade("LONG", 140.0, 1.1, TIME),
]


def _charged(trades, cost):
    return [apply_cost_model(d, entry, exit_, risk, cost=cost, close_reason=reason)
            for d, entry, exit_, risk, reason in trades]


def _record(trades, *, slippage=3.0, stop=None):
    """The evidence a backtest of ``trades`` records. ``stop=None`` is a record from before the
    split: its stops paid the general rate and it names no stop rate."""
    cost = CostModel(slippage_bps=slippage, stop_slippage_bps=slippage if stop is None else stop)
    legs = _charged(trades, cost)
    model = {"taker_fee_bps": cost.taker_fee_bps, "slippage_bps": slippage}
    if stop is not None:
        model["stop_slippage_bps"] = stop
    return {"candidate_id": "cand_x", "backtest_evidence": {
        "closed_count": len(trades), "expectancy": sum(leg.net_r for leg in legs) / len(trades),
        "cost_summary": {"total_net_r": sum(leg.net_r for leg in legs),
                         "total_slippage_cost_r": sum(leg.slippage_cost_r for leg in legs),
                         "cost_model": model}}}


def _recharged(trades, rate):
    """The same trades with every market leg paying ``rate``: the figure being re-derived."""
    legs = _charged(trades, CostModel(slippage_bps=rate, stop_slippage_bps=rate))
    return sum(leg.net_r for leg in legs) / len(trades)


@pytest.mark.parametrize("rate", STRESS_SLIPPAGE_BPS)
def test_a_record_scored_at_one_rate_is_repriced_to_one_figure(rate):
    record = _record(MIXED)
    low, high = net_at_slippage(record, slippage_bps=rate)
    assert low == high == pytest.approx(_recharged(MIXED, rate), abs=SECOND_ORDER)
    # and it is §F8's own formula, per trade
    summary = record["backtest_evidence"]["cost_summary"]
    assert low == pytest.approx(
        (summary["total_net_r"] - summary["total_slippage_cost_r"] * (rate / 3.0 - 1.0)) / len(MIXED), abs=1e-8)


def test_repricing_at_the_scored_rate_is_the_stored_expectancy():
    record = _record(MIXED)
    assert net_at_slippage(record, slippage_bps=3.0)[0] == pytest.approx(
        record["backtest_evidence"]["expectancy"], abs=1e-8)


@pytest.mark.parametrize("stop", [1.4, 12.0])
@pytest.mark.parametrize("trades", [MIXED, ALL_STOPS, NO_STOPS], ids=["mixed", "all_stops", "no_stops"])
@pytest.mark.parametrize("rate", STRESS_SLIPPAGE_BPS)
def test_a_record_scored_at_two_rates_is_bracketed(stop, trades, rate):
    low, high = net_at_slippage(_record(trades, stop=stop), slippage_bps=rate)
    assert low - SECOND_ORDER <= _recharged(trades, rate) <= high + SECOND_ORDER


def test_the_bracket_is_a_range_and_its_ends_are_the_two_books():
    """Every trade a stop sits at one end and no stop exit at the other, so neither end is slack."""
    rate = 23.5
    low, high = net_at_slippage(_record(MIXED, stop=1.4), slippage_bps=rate)
    assert high - low > 0.1
    # a cheaper stop rate hides exposure: the more stops, the more the re-price costs
    all_low, _ = net_at_slippage(_record(ALL_STOPS, stop=1.4), slippage_bps=rate)
    assert all_low == pytest.approx(_recharged(ALL_STOPS, rate), abs=rate / 10000.0 + SECOND_ORDER)
    _, none_high = net_at_slippage(_record(NO_STOPS, stop=1.4), slippage_bps=rate)
    assert none_high == pytest.approx(_recharged(NO_STOPS, rate), abs=SECOND_ORDER)


def test_the_f8_formula_alone_would_flatter_a_two_rate_record():
    """Why the range exists: scaling the whole total by the entry ratio is its optimistic end."""
    record = _record(ALL_STOPS, stop=1.4)
    summary = record["backtest_evidence"]["cost_summary"]
    naive = (summary["total_net_r"] - summary["total_slippage_cost_r"] * (23.5 / 3.0 - 1.0)) / len(ALL_STOPS)
    low, high = net_at_slippage(record, slippage_bps=23.5)
    assert naive == pytest.approx(high, abs=1e-8)
    assert naive - _recharged(ALL_STOPS, 23.5) > 0.1


def test_a_higher_rate_never_improves_either_end():
    record = _record(MIXED, stop=1.4)
    ends = [net_at_slippage(record, slippage_bps=rate) for rate in STRESS_SLIPPAGE_BPS]
    assert all(a[0] > b[0] and a[1] > b[1] for a, b in zip(ends, ends[1:]))


@pytest.mark.parametrize("stop", [None, 1.4])
def test_the_breakeven_is_where_each_end_nets_zero(stop):
    record = _record(WINNING, stop=stop)
    low, high = slippage_breakeven_bps(record)
    assert 3.0 < low <= high
    assert net_at_slippage(record, slippage_bps=low)[0] == pytest.approx(0.0, abs=1e-4)
    assert net_at_slippage(record, slippage_bps=high)[1] == pytest.approx(0.0, abs=1e-4)
    assert (low == high) is (stop is None)
    # and the same trades really do cross zero inside it
    assert _recharged(WINNING, low) > -SECOND_ORDER and _recharged(WINNING, high) < SECOND_ORDER


def test_a_record_that_loses_before_slippage_has_no_positive_breakeven():
    losing = [_trade("LONG", 100.0, 0.8, STOP), _trade("SHORT", 250.0, 1.5, STOP)]
    low, high = slippage_breakeven_bps(_record(losing))
    assert high <= 0


def _without(record, *path):
    copy = {**record, "backtest_evidence": {**record["backtest_evidence"], "cost_summary": {
        **record["backtest_evidence"]["cost_summary"],
        "cost_model": dict(record["backtest_evidence"]["cost_summary"]["cost_model"])}}}
    node = copy["backtest_evidence"]
    for key in path[:-1]:
        node = node[key]
    node.pop(path[-1])
    return copy


def test_a_record_that_cannot_say_is_none_never_a_figure():
    record = _record(MIXED, stop=1.4)
    unreadable = [
        {}, {"backtest_evidence": {"closed_count": 8, "expectancy": 0.2}},
        {"backtest_evidence": "gone"}, {"backtest_evidence": {"closed_count": 8, "cost_summary": [1.0]}},
        {"backtest_evidence": {"closed_count": 8, "cost_summary": {"total_net_r": 1.0, "cost_model": 3.0}}},
        _without(record, "cost_summary", "total_slippage_cost_r"),
        _without(record, "cost_summary", "total_net_r"),
        _without(record, "cost_summary", "cost_model", "slippage_bps"),
        _without(record, "closed_count"),
    ]
    zero_trades = _record(MIXED)
    zero_trades["backtest_evidence"]["closed_count"] = 0
    no_slippage = _record(MIXED)
    no_slippage["backtest_evidence"]["cost_summary"]["total_slippage_cost_r"] = 0.0
    bad_stop = _record(MIXED, stop=1.4)
    bad_stop["backtest_evidence"]["cost_summary"]["cost_model"]["stop_slippage_bps"] = "1.4"
    for case in [*unreadable, zero_trades, no_slippage, bad_stop]:
        assert net_at_slippage(case, slippage_bps=10.0) is None
        assert slippage_breakeven_bps(case) is None
    assert net_at_slippage(record, slippage_bps=True) is None


# --- against a real backtest, the way `expectancy_at` is checked ---------------------------------

def _backtested(slippage, stop):
    from runtime.mvp_runtime.crypto.backtest import backtest_spec
    from runtime.mvp_runtime.crypto.strategy import StrategySpec

    from tests.test_mvp_runtime_crypto_cost import _spec_dict, _trending_snapshot

    evidence = backtest_spec(StrategySpec.from_dict(_spec_dict()), _trending_snapshot(),
                             cost=CostModel(slippage_bps=slippage, stop_slippage_bps=stop))
    return {"candidate_id": f"cand_{slippage}_{stop}", "backtest_evidence": evidence}


@pytest.mark.parametrize("rate", [10.0, 23.5])
def test_the_repriced_figure_is_a_real_rerun_at_that_rate(rate):
    """Holds while the re-run takes the same trades: the entry-cost door refuses none here."""
    rerun = _backtested(rate, rate)["backtest_evidence"]
    one_rate, two_rates = _backtested(3.0, 3.0), _backtested(3.0, 1.4)
    assert rerun["closed_count"] == one_rate["backtest_evidence"]["closed_count"] > 0
    assert rerun["entry_cost_door"]["refused_entries"] == 0
    low, high = net_at_slippage(one_rate, slippage_bps=rate)
    assert low == high == pytest.approx(rerun["expectancy"], abs=SECOND_ORDER)
    low, high = net_at_slippage(two_rates, slippage_bps=rate)
    assert low < rerun["expectancy"] < high
