"""The search sampled the same neighbourhood for a hundred generations.

`mutate_params` draws `base ± (hi − lo) × 0.35` around `template.base_params`, and that base is
a CONSTANT. Nothing a generation learned changed where the next one looked: repeated sampling,
not evolution.

Moving the centre has a hazard that has to be named or it is a worse bug than the one it closes.
Hill-climbing on a noisy fitness surface converges on the noise — which is the failure this
store already exhibits, expectancy falling toward the cost of trading as sample size grows. So
the centre follows ROBUSTNESS rather than expectancy (centring on the highest expectancy is
precisely the mechanism that produced a store of maxima), and half the draws stay on the
template's own base so the search cannot collapse onto one point.

These pin the parts that are easy to get subtly wrong: which candidate wins, when the centre
refuses to move at all, and that the split is deterministic rather than random.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto.factory import (
    ELITE_EVIDENCE_MIN_TRADES,
    elite_base_params,
)

FALLBACK = {"adx_min": 22.0, "rsi_max": 55.0}


def _candidate(*, family="trend_pullback", symbol="BTCUSDT", timeframe="1h",
               params=None, closed=100, score=0.7):
    return {
        "champion_score": score,
        "mint_params": params if params is not None else {"adx_min": 30.0, "rsi_max": 40.0},
        "backtest_evidence": {"closed_count": closed},
        "strategy_spec": {"strategy_family": family, "timeframe": timeframe,
                          "symbol_scope": [symbol]},
    }


def _centre(candidates, **over):
    kw = {"family": "trend_pullback", "symbol": "BTCUSDT", "timeframe": "1h",
          "fallback": FALLBACK}
    kw.update(over)
    return elite_base_params(candidates, **kw)


# --- when the centre moves ------------------------------------------------------

def test_the_most_robust_candidate_sets_the_centre():
    weak = _candidate(score=0.4, params={"adx_min": 18.0, "rsi_max": 60.0})
    strong = _candidate(score=0.9, params={"adx_min": 28.0, "rsi_max": 45.0})
    assert _centre([weak, strong]) == {"adx_min": 28.0, "rsi_max": 45.0}


def test_expectancy_does_not_decide_it():
    """Centring on the highest expectancy is the mechanism that produced a store whose
    expectancy converges on the cost of trading. `champion_score` is the anti-overfit score."""
    lucky = _candidate(score=0.3, params={"adx_min": 15.0, "rsi_max": 65.0})
    lucky["backtest_evidence"]["expectancy"] = 9.99
    solid = _candidate(score=0.8, params={"adx_min": 25.0, "rsi_max": 50.0})
    assert _centre([lucky, solid])["adx_min"] == 25.0


def test_a_tie_keeps_the_earlier_record():
    """`>` not `>=`, so the centre does not drift with store order among equals."""
    first = _candidate(score=0.7, params={"adx_min": 20.0, "rsi_max": 50.0})
    second = _candidate(score=0.7, params={"adx_min": 29.0, "rsi_max": 41.0})
    assert _centre([first, second])["adx_min"] == 20.0


# --- when it refuses to ---------------------------------------------------------

def test_nothing_to_learn_from_keeps_the_template_base():
    assert _centre([]) == FALLBACK


def test_a_thin_record_does_not_move_the_centre():
    """A centre moved on three trades is not a lesson."""
    thin = _candidate(closed=ELITE_EVIDENCE_MIN_TRADES - 1, params={"adx_min": 30.0})
    assert _centre([thin]) == FALLBACK


def test_a_candidate_without_recorded_params_is_skipped():
    """Every candidate in the store today. The first generation after this still draws around
    the template base; the one after it has something to move toward."""
    legacy = _candidate()
    del legacy["mint_params"]
    assert _centre([legacy]) == FALLBACK


@pytest.mark.parametrize("over", [
    {"family": "breakout"}, {"timeframe": "4h"}, {"symbol": "ETHUSDT"},
])
def test_another_context_teaches_this_one_nothing(over):
    """A parameter that worked on BTC 1h is not evidence about ETH 4h, and the review's own
    finding is that cost as a share of R differs twelvefold across timeframes."""
    assert _centre([_candidate()], **over) == FALLBACK


def test_a_score_that_is_not_a_number_is_skipped():
    broken = _candidate()
    broken["champion_score"] = None
    assert _centre([broken]) == FALLBACK


# --- the shape of what comes back ----------------------------------------------

def test_only_keys_the_template_still_declares_come_through():
    """A param the family dropped must not be resurrected from an old record."""
    stale = _candidate(params={"adx_min": 30.0, "rsi_max": 40.0, "removed_param": 1.0})
    assert set(_centre([stale])) == set(FALLBACK)


def test_a_param_the_family_gained_comes_from_the_template():
    older = _candidate(params={"adx_min": 30.0})
    assert _centre([older]) == {"adx_min": 30.0, "rsi_max": FALLBACK["rsi_max"]}


def test_the_fallback_is_never_mutated():
    before = dict(FALLBACK)
    _centre([_candidate()])
    _centre([])
    assert FALLBACK == before


# --- and half the draws stay home ----------------------------------------------

def test_the_split_is_the_index_not_a_coin_flip():
    """Deterministic, so a replay reproduces the same batch — and so the search keeps exploring
    the region the template was written for however good the elite point looks."""
    import inspect

    from runtime.mvp_runtime.crypto import factory

    source = inspect.getsource(factory.generate_batch)
    assert "len(accepted) % 2 == 0" in source
    assert "rng" not in source.split("elite =")[1].split("params = mutate_params")[0]
