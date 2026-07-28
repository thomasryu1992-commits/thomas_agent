"""A gate's verdict needs a sample, exactly as the performance headline does.

The board rated `MAX_CONSECUTIVE_LOSS_GATE_BLOCKED` as costing money — 🔴, and a headline
line telling the operator two gates need review — off **two** blocked trades that happened to
return the same number. Two observations cannot estimate a spread, and identical ones produce
a zero-width interval that excludes zero by construction. The mean's sign was being read as a
verdict about a risk gate.

Same test as the headline, applied to the same kind of claim, with one addition the headline
did not need: a floor below which no interval is produced at all.
"""

from __future__ import annotations

from runtime.mvp_runtime.crypto.counterfactual import (
    r_values_by_reason,
    summarize_counterfactuals,
)
from runtime.mvp_runtime.crypto.dashboard import (
    _GATE_COSTING,
    _GATE_EARNING,
    _GATE_UNDECIDED,
    _gate_rows,
    sample_verdict,
)


def _status(buckets: dict, verdicts: dict) -> dict:
    return {"counterfactual_by_reason": buckets, "counterfactual_verdicts": verdicts}


def _bucket(expectancy: float, count: int) -> dict:
    return {"expectancy_R": expectancy, "closed_count": count,
            "missed_opportunity": count, "avoided_loss": 0}


def test_two_identical_blocked_trades_are_not_a_verdict():
    """The live row: +2.00R × 2건, both the same value, rated 🔴 costing money."""
    values = [2.0, 2.0]
    sv = sample_verdict(values)
    assert not sv["distinguishable_from_zero"]
    assert sv["ci_low"] is None, "below the floor there should be no interval at all"

    rows = _gate_rows(_status({"g": _bucket(2.0, 2)}, {"g": sv}))
    assert rows[0][0] == _GATE_UNDECIDED


def test_identical_values_above_the_floor_are_still_not_certainty():
    """A zero-width interval is absence of observed variation, not evidence of none."""
    sv = sample_verdict([0.5] * 20)
    assert sv["stdev_r"] == 0
    assert not sv["distinguishable_from_zero"]


def test_a_gate_with_a_real_sample_keeps_its_verdict():
    values = [-0.9] * 60 + [0.4] * 10
    sv = sample_verdict(values)
    assert sv["distinguishable_from_zero"]
    rows = _gate_rows(_status({"g": _bucket(sv["mean_r"], len(values))}, {"g": sv}))
    assert rows[0][0] == _GATE_EARNING


def test_a_measured_but_inconclusive_gate_is_not_read_as_a_pass():
    """`ci_low is None` means measured-and-unmeasurable, not never-measured.

    Reading the second as the first is what left a 2-trade gate rated as costing money: the
    verdict was computed, said "cannot tell", and the classifier fell through to the mean.
    """
    thin = sample_verdict([2.0, 2.0])          # below the floor -> no interval
    wide = sample_verdict([2.0] * 10 + [-2.0] * 10)  # measured, spans zero
    for sv in (thin, wide):
        rows = _gate_rows(_status({"g": _bucket(1.0, sv["count"])}, {"g": sv}))
        assert rows[0][0] == _GATE_UNDECIDED


def test_a_gate_never_measured_keeps_the_mean_sign_reading():
    """An older status document has no verdict map; it must not all turn undecided."""
    rows = _gate_rows(_status({"g": _bucket(2.0, 2)}, {}))
    assert rows[0][0] == _GATE_COSTING


def test_undecided_sorts_below_both_decided_kinds():
    """Acting on the ones that cannot say is the mistake, so they rank last."""
    status = _status(
        {"cost": _bucket(0.5, 40), "earn": _bucket(-0.5, 40), "unk": _bucket(9.0, 2)},
        # Varied, not constant: a constant series is undecided under the zero-width rule,
        # which is the point of the test two above this one.
        {"cost": sample_verdict([0.6, 0.4] * 20), "earn": sample_verdict([-0.4, -0.6] * 20),
         "unk": sample_verdict([9.0, 9.0])},
    )
    order = [reason for _v, reason, _b in _gate_rows(status)]
    assert order.index("unk") == len(order) - 1


def test_the_r_values_group_exactly_as_the_summary_does():
    """One record carries several reasons and lands in every bucket. If these two functions
    disagreed, the interval would describe a different set of trades than the mean it
    qualifies."""
    records = [
        {"outcome_closed": True, "result_R": 1.0, "block_reasons": ["a", "b"]},
        {"outcome_closed": True, "result_R": -2.0, "block_reasons": ["b"]},
        {"outcome_closed": False, "result_R": 99.0, "block_reasons": ["a"]},  # ignored
        {"outcome_closed": True, "result_R": 0.5},                            # unattributed
    ]
    summary = summarize_counterfactuals(records)
    values = r_values_by_reason(records)
    assert set(summary) == set(values)
    for reason, bucket in summary.items():
        assert bucket["closed_count"] == len(values[reason])
        assert abs(bucket["expectancy_R"] - sum(values[reason]) / len(values[reason])) < 1e-9
