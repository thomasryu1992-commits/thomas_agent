"""The 5-1 forward confirmation rule (Thomas 2026-08-11) and the LIVE gate that consumes it.

Under test: the judge mirrors `robustness.holdout_status` branch for branch on forward
paper outcomes (trade floor, spread, the simple interval whose failure is CONTRADICTED,
the slice test at the holdout's own width); empty slices are dropped, never zero-filled;
attribution is lineage-exact (`sid:` history can never confirm anybody); and the arming
door refuses LIVE for a lineage confirmed nowhere unseen, naming both statuses and the
observed-cohort size."""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import forward_confirmation as fc
from runtime.mvp_runtime.errors import ToolError

DAY = "2026-07-{:02d}T12:00:00Z"


def _record(cid="cand_f", timeframe="1d"):
    return {
        "candidate_id": cid, "strategy_id": "S1", "generation_id": "GEN-001",
        "strategy_rule_hash": "hash-f",
        "strategy_spec": {"strategy_family": "breakout", "symbol_scope": ["BTCUSDT"],
                          "timeframe": timeframe},
        "backtest_evidence": {
            "closed_count": 60, "expectancy": 0.2,
            "robustness": {"verdict": "PROVISIONAL", "holdout_status": "INSUFFICIENT"},
        },
    }


def _outcome(when, net, *, cid="cand_f"):
    """A settled forward row that prices itself exactly: already-net basis, no carry."""
    return {"outcome_closed": True, "candidate_id": cid, "result_R": net,
            "r_basis": "intent_net_of_costs", "created_at_utc": when}


def _spread_outcomes(*, slices=9, per_slice=3, base=0.3, cid="cand_f", width_days=60.0,
                     start="2026-01-01T00:00:00Z"):
    """`slices` active windows of `per_slice` trades each, sums deliberately unequal."""
    from runtime.mvp_runtime import timeutil
    from datetime import timedelta
    t0 = timeutil.parse_iso(start)
    rows = []
    for s in range(slices):
        for k in range(per_slice):
            moment = t0 + timedelta(days=s * width_days + k * 3 + 0.5)
            rows.append(_outcome(timeutil.format_iso(moment),
                                 round(base + 0.05 * (k - 1) + 0.01 * s, 4), cid=cid))
    return rows


# --- the slice width is the holdout's own -----------------------------------------------------

def test_the_slice_width_is_the_holdout_derivation():
    """tail/10 of the CALENDAR span replayed: 30 days wherever the factory reaches its full
    `FACTORY_DEPTH_DAYS`. Literal on purpose: if the factory window ever moves, this test
    moving with it is the visibility we want — and deriving instead of hand-writing already
    caught two stale figures (the proposal doc said 42/21, from the 700-day window that no
    longer exists)."""
    assert fc.slice_width_days("1d") == 30.0
    assert fc.slice_width_days("4h") == 30.0
    assert fc.slice_width_days("1h") == 30.0
    assert fc.slice_width_days("2w") is None  # an untargeted timeframe cannot be sliced


def test_the_bar_floor_does_not_widen_1d_but_the_candle_ceiling_still_narrows_1m():
    """`factory_candle_target` clamps both ways and only one clamp belongs on a calendar gate.

    1d's target is floored to MIN_FACTORY_BARS (2,000 bars = 2,000 days) to buy the BACKTEST
    scorer trades; carrying that here would claim a 1d regime lasts twice a 4h one. 1m and 5m
    are truncated by MAX_CANDLES to 83 and 417 days of real history, and that IS the span they
    replay — a flat 30 would cut slices wider than 1m's entire window."""
    assert fc.slice_width_days("1d") == fc.slice_width_days("4h")  # floor removed
    # approx: 120,000/1440 is not binary-exact, and the verdict rounds the width anyway.
    assert fc.slice_width_days("1m") == pytest.approx(2.5)   # 120,000 bars = 83.3d of history
    assert fc.slice_width_days("5m") == pytest.approx(12.5)  # 120,000 bars = 416.7d
    assert fc.slice_width_days("1m") < fc.slice_width_days("5m") < fc.slice_width_days("15m")


# --- the judge --------------------------------------------------------------------------------

def test_a_consistent_spread_forward_record_confirms():
    verdict = fc.judge_forward(_record(), _spread_outcomes())
    assert verdict["status"] == fc.FORWARD_CONFIRMED
    assert verdict["priceable_count"] == 27
    assert verdict["active_slices"] == 9


def test_a_negative_forward_record_contradicts():
    rows = _spread_outcomes(base=-0.3)
    assert fc.judge_forward(_record(), rows)["status"] == fc.FORWARD_CONTRADICTED


def test_a_thin_forward_record_is_insufficient_not_a_pass():
    rows = _spread_outcomes(slices=4, per_slice=3)  # 12 closes
    assert fc.judge_forward(_record(), rows)["status"] == fc.FORWARD_INSUFFICIENT


def test_1d_uses_lower_trade_floor():
    """1d timeframe has a 10-trade floor (Thomas 2026-08-21), not 25."""
    assert fc.min_forward_trades("1d") == fc.MIN_FORWARD_TRADES_1D == 10
    assert fc.min_forward_trades("4h") == fc.MIN_HOLDOUT_TRADES == 25
    assert fc.min_forward_trades("1h") == 25
    assert fc.min_forward_trades(None) == 25


def test_1d_forward_confirms_at_exactly_10_trades():
    """The 1d floor is reachable at its own value, not merely above it.

    Ten closes, one per slice — the shape a 1d lineage actually produces, where a slice
    holding two trades is the exception. A test that passed 18 would not have shown that
    the floor `min_forward_trades` returns is a floor anything can stand on."""
    rows = _spread_outcomes(slices=10, per_slice=1, base=0.3)
    verdict = fc.judge_forward(_record(timeframe="1d"), rows)
    assert verdict["priceable_count"] == fc.MIN_FORWARD_TRADES_1D == 10
    assert verdict["active_slices"] == 10
    assert verdict["status"] == fc.FORWARD_CONFIRMED


def test_1d_forward_is_insufficient_one_trade_below_its_floor():
    """Nine closes refuse — the floor binds, rather than the slice count carrying it."""
    rows = _spread_outcomes(slices=9, per_slice=1, base=0.3)
    verdict = fc.judge_forward(_record(timeframe="1d"), rows)
    assert verdict["priceable_count"] == 9
    assert verdict["status"] == fc.FORWARD_INSUFFICIENT


def test_4h_forward_insufficient_at_12_trades():
    """A 4h record with 12 priceable closes is still below the 25-trade floor."""
    rows = _spread_outcomes(slices=4, per_slice=3)  # 12 closes
    verdict = fc.judge_forward(_record(timeframe="4h"), rows)
    assert verdict["status"] == fc.FORWARD_INSUFFICIENT


def test_enough_trades_in_too_few_slices_is_insufficient():
    """Slice concentration is the regime-lottery shape the period rule exists to refuse."""
    rows = _spread_outcomes(slices=3, per_slice=9)  # 27 closes, 3 active slices
    verdict = fc.judge_forward(_record(), rows)
    assert verdict["status"] == fc.FORWARD_INSUFFICIENT
    assert verdict["active_slices"] == 3


def test_empty_slices_are_dropped_never_zero_filled():
    """Eight active slices scattered across a long gap still judge on eight: an idle window
    is absence of evidence, and zero-filling it would shrink the spread with data nobody
    measured (the holdout's own rule)."""
    from runtime.mvp_runtime import timeutil
    from datetime import timedelta
    t0 = timeutil.parse_iso("2026-01-01T00:00:00Z")
    rows = []
    for s in range(8):
        for k in range(4):
            moment = t0 + timedelta(days=s * 3 * 60.0 + k * 5 + 1)  # every third window
            rows.append(_outcome(timeutil.format_iso(moment),
                                 round(0.3 + 0.05 * (k - 1) + 0.01 * s, 4)))
    verdict = fc.judge_forward(_record(), rows)
    assert verdict["priceable_count"] == 32
    assert verdict["active_slices"] == 8
    assert verdict["status"] == fc.FORWARD_CONFIRMED


def test_display_name_history_can_never_confirm():
    """`sid:` attribution is imprecise by construction; confirmation is lineage-exact."""
    rows = [dict(_outcome(DAY.format(2 + i % 26), 0.5), candidate_id=None,
                 strategy_id="S1") for i in range(30)]
    verdict = fc.judge_forward(_record(), rows)
    assert verdict["closed_count"] == 0
    assert verdict["status"] == fc.FORWARD_INSUFFICIENT


def test_an_unpriceable_row_is_not_evidence():
    rows = _spread_outcomes()
    rows.append({"outcome_closed": True, "candidate_id": "cand_f", "result_R": 99.0,
                 "r_basis": "intent_net_of_costs", "created_at_utc": "not a time"})
    verdict = fc.judge_forward(_record(), rows)
    assert verdict["closed_count"] == 28
    assert verdict["priceable_count"] == 27


# --- the LIVE gate ----------------------------------------------------------------------------

def test_the_gate_refuses_a_lineage_confirmed_nowhere():
    with pytest.raises(ToolError) as exc:
        fc.assert_live_tier_confirmed([_record()], outcomes=[], observed_lineages=11)
    assert exc.value.reason_code == "CANDIDATE_UNCONFIRMED_FOR_LIVE"
    message = str(exc.value)
    assert "holdout=INSUFFICIENT" in message
    assert "forward=FORWARD_INSUFFICIENT" in message
    assert "11 observed lineage" in message


def test_a_forward_confirmed_lineage_passes_the_gate():
    fc.assert_live_tier_confirmed(
        [_record()], outcomes=_spread_outcomes(), observed_lineages=11,
    )


def test_a_confirmed_holdout_still_passes_without_any_forward_record():
    record = _record()
    record["backtest_evidence"]["robustness"]["holdout_status"] = "CONFIRMED"
    fc.assert_live_tier_confirmed([record], outcomes=[], observed_lineages=11)


def test_a_contradicted_forward_record_does_not_pass_as_merely_unconfirmed():
    with pytest.raises(ToolError) as exc:
        fc.assert_live_tier_confirmed(
            [_record()], outcomes=_spread_outcomes(base=-0.3), observed_lineages=11,
        )
    assert "forward=FORWARD_CONTRADICTED" in str(exc.value)
