"""Whether the board's headline answers "how much data" or "what does it show".

It used to answer the first while sounding like the second. `MIN_MEANINGFUL_SAMPLE = 30` made
the verdict flip from "표본 부족" to "검토 가능" on the 30th closed trade — and on the live
machine that happened at +0.08R over 60 trades with an R standard deviation of 1.56, i.e. 0.4
standard errors from zero. The count was met; the sign was still unknown.

A count threshold and a significance test diverge exactly when variance is large relative to
the edge, which is the permanent condition of trade returns. So the board computes the
interval.
"""

from __future__ import annotations

from runtime.mvp_runtime.crypto.dashboard import sample_verdict


def test_a_thin_edge_in_noisy_returns_is_not_distinguishable():
    """The live case: a real positive mean that a reader must not act on."""
    # The live record's actual shape: 60 trades, ~33% win rate, wins ~+2.0R, losses ~-0.9R.
    # Mean lands near +0.07R and the spread swamps it.
    values = [2.0] * 20 + [-0.9] * 40
    v = sample_verdict(values)
    assert v["mean_r"] > 0
    assert not v["distinguishable_from_zero"]
    assert v["ci_low"] < 0 < v["ci_high"]


def test_a_strong_consistent_edge_is_distinguishable():
    v = sample_verdict([0.5] * 40 + [0.4] * 40)
    assert v["distinguishable_from_zero"]
    assert v["ci_low"] > 0


def test_a_consistent_loss_is_distinguishable_too():
    """The test is about separation from zero, not about being profitable."""
    v = sample_verdict([-0.5] * 40 + [-0.4] * 40)
    assert v["distinguishable_from_zero"]
    assert v["ci_high"] < 0


def test_more_of_the_same_data_narrows_the_interval():
    """The property that makes `trades_needed` meaningful rather than decorative."""
    small = sample_verdict([1.9, -1.0] * 15)
    large = sample_verdict([1.9, -1.0] * 150)
    assert large["stderr_r"] < small["stderr_r"]
    assert (large["ci_high"] - large["ci_low"]) < (small["ci_high"] - small["ci_low"])


def test_trades_needed_scales_quadratically_as_the_edge_shrinks():
    """Halving the observed edge roughly quadruples the sample it would take to see it."""
    strong = sample_verdict([1.0, -0.6] * 50)   # mean +0.20
    weak = sample_verdict([1.0, -0.8] * 50)     # mean +0.10
    assert weak["trades_needed"] > strong["trades_needed"] * 3


def test_a_sample_too_small_to_measure_says_so_rather_than_guessing():
    for values in ([], [0.5]):
        v = sample_verdict(values)
        assert v["ci_low"] is None
        assert v["distinguishable_from_zero"] is False
        assert v["trades_needed"] is None


def test_a_zero_mean_needs_no_impossible_sample_size():
    """An exactly-zero mean has no effect to detect; `trades_needed` must not divide by it."""
    v = sample_verdict([1.0, -1.0] * 20)
    assert v["mean_r"] == 0
    assert v["trades_needed"] is None
    assert not v["distinguishable_from_zero"]
