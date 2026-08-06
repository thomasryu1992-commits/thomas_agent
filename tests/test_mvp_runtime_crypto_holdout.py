"""Out-of-sample holdout — the gate that makes ROBUST mean something.

Before this, every number the evidence carried was computed on the same bars the
candidate was mined on, and promotion picks the highest scorer out of a growing store.
Selecting the maximum over many in-sample scores is how noise gets promoted, and no
in-sample statistic can detect it. These tests pin the two halves of the fix: the split
is real (the score never sees the tail) and ROBUST is unreachable without out-of-sample
confirmation."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.crypto import factory
from runtime.mvp_runtime.crypto.robustness import (
    CONFIDENCE_Z,
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

# A zero-mean, unit-variance pattern for the period subtotals. Fixed rather than random so a
# fixture is reproducible, and shaped so the PERIOD interval reproduces the TRADE interval when
# the trades really are independent: a period holding n/P trades of per-trade spread s has a
# total of spread s*sqrt(n/P), and dividing that by sqrt(P) returns e*sqrt(n)/s — the same t.
# So these fixtures differ from the trade test only by the t-vs-z correction, which is the
# honest baseline: any further divergence in production is real period correlation.
_PERIOD_SHAPE = (-1.4863, -1.156, -0.8257, -0.4954, -0.1651, 0.1651, 0.4954, 0.8257, 1.156, 1.4863)


def _tail(closed=MIN_HOLDOUT_TRADES, expectancy=0.0, stdev=1.0, periods=True):
    """A holdout block shaped like ``factory._holdout_evidence`` writes them.

    ``periods=False`` is the pre-2026-08-04 shape — every block minted before the tail was
    subtotalled — which can never CONFIRM by design."""
    block = {"closed_count": closed, "expectancy": expectancy,
             "total_R": round(expectancy * closed, 8), "stdev_r": stdev}
    if not periods:
        return block
    count = len(_PERIOD_SHAPE)
    per_period = closed / count
    block["period_r"] = [
        round(expectancy * per_period + stdev * math.sqrt(per_period) * shape, 8)
        for shape in _PERIOD_SHAPE
    ]
    block["period_trades"] = [max(1, int(per_period))] * count
    return block


# An edge this far above zero clears CONFIDENCE_Z standard errors at the trade floor, and one
# this far below is refused for the mirror reason. Derived from the constants so that moving
# either one moves the fixtures with it rather than silently inverting a case.
_CLEARS = 3.0 * CONFIDENCE_Z / math.sqrt(MIN_HOLDOUT_TRADES)
_MARGINAL = 0.5 * CONFIDENCE_Z / math.sqrt(MIN_HOLDOUT_TRADES)


@pytest.mark.parametrize("holdout,expected", [
    (None, HOLDOUT_UNCONFIRMED),
    ({}, HOLDOUT_UNCONFIRMED),
    (_tail(closed=MIN_HOLDOUT_TRADES - 1, expectancy=9.0), HOLDOUT_INSUFFICIENT),
    (_tail(expectancy=_CLEARS), HOLDOUT_CONFIRMED),
    (_tail(expectancy=0.0), HOLDOUT_CONTRADICTED),
    (_tail(closed=200, expectancy=-0.2), HOLDOUT_CONTRADICTED),
    ({"closed_count": "many", "total_R": 5.0, "stdev_r": 1.0}, HOLDOUT_INSUFFICIENT),  # unusable
])
def test_holdout_status_classification(holdout, expected):
    assert holdout_status(holdout) == expected


def test_a_profitable_tail_that_cannot_clear_its_own_noise_is_not_a_confirmation():
    """The gate this replaces: ``total_R > 0`` on a tail whose spread swamps its mean.

    Both blocks are profitable and both clear the trade floor. Only one of them says
    anything — which is the entire difference between measuring an edge and measuring how
    many candidates were tried."""
    assert holdout_status(_tail(expectancy=_MARGINAL)) == HOLDOUT_CONTRADICTED
    assert holdout_status(_tail(expectancy=_CLEARS)) == HOLDOUT_CONFIRMED
    # ...and the loud one is only loud because its trades agree with each other.
    assert holdout_status(_tail(expectancy=_MARGINAL, stdev=0.05)) == HOLDOUT_CONFIRMED


def test_evidence_without_a_recorded_spread_cannot_confirm():
    """Every holdout block written before ``stdev_r`` existed — 847 of them on the machine
    this was measured on. Falling back to ``total_R > 0`` for them would let a record buy the
    weaker test by omitting a field; absence is not an opt-out."""
    legacy = {"closed_count": 400, "total_R": 90.0, "expectancy": 0.225}
    assert holdout_status(legacy) == HOLDOUT_INSUFFICIENT
    # The spread alone is no longer enough — the tail must also be subtotalled by period —
    # so the restored block is the full modern shape.
    restored = _tail(closed=400, expectancy=0.225, stdev=1.4)
    assert holdout_status(restored) == HOLDOUT_CONFIRMED


def test_one_lucky_period_cannot_buy_a_confirmation():
    """**The defect the period interval closes.** Trades are not independent draws — measured
    2026-08-04, mean pairwise correlation of per-block gross across the ten routed contexts is
    +0.459, so the same weeks are good or bad for everything at once. This block clears the
    trade-level bar comfortably and its entire edge sits in one slice of the tail."""
    lucky = {"closed_count": 100, "expectancy": 0.30, "total_R": 30.0, "stdev_r": 1.4,
             "period_r": [29.3, 0.1, -0.1, 0.1, 0.1, -0.1, 0.2, 0.1, 0.2, 0.1],
             "period_trades": [10] * 10}
    trade_t = 0.30 / (1.4 / math.sqrt(100))
    assert trade_t > CONFIDENCE_Z, "fixture must clear the OLD gate, or it proves nothing"
    assert holdout_status(lucky) == HOLDOUT_INSUFFICIENT


def test_a_block_minted_before_the_tail_was_subtotalled_cannot_confirm():
    """The ``stdev_r`` precedent, applied again: absence is not an opt-out. Letting a record
    buy the weaker test by omitting a field is the failure ``cost.outcome_net_r`` names in the
    other direction — so every block predating ``period_r`` reads INSUFFICIENT until the
    factory re-mints it, and `scripts/rescore_stale_holdout_candidates.py` is what does that."""
    assert holdout_status(_tail(expectancy=_CLEARS, periods=False)) == HOLDOUT_INSUFFICIENT
    assert holdout_status(_tail(expectancy=_CLEARS, periods=True)) == HOLDOUT_CONFIRMED


def test_a_failing_trade_interval_stays_contradicted_without_period_data():
    """Asymmetric on purpose. The period interval is strictly wider, so a block that cannot
    clear the trade one could never clear it — CONTRADICTED needs no re-mint to stay true, and
    the 371 blocks already carrying it keep the verdict they had."""
    assert holdout_status(_tail(expectancy=0.0, periods=False)) == HOLDOUT_CONTRADICTED
    assert holdout_status(_tail(closed=200, expectancy=-0.2, periods=False)) == HOLDOUT_CONTRADICTED


def test_a_period_that_traded_nothing_is_not_a_break_even_observation():
    """Absence of evidence, not evidence of break-even: counting an untraded slice as 0.0
    would shrink the spread with data nobody measured, which is the same free-precision the
    zero-width interval rule already refuses."""
    sparse = {"closed_count": 60, "expectancy": 0.35, "total_R": 21.0, "stdev_r": 1.0,
              "period_r": [3.5, 3.0, 0.0, 2.0, 2.0, 2.5, 3.0, 0.0, 2.5, 2.5],
              "period_trades": [8, 8, 0, 5, 5, 6, 8, 0, 5, 5]}
    # Eight traded slices remain, which is exactly the floor — dropping to seven refuses.
    assert holdout_status(sparse) == HOLDOUT_CONFIRMED
    thinner = {**sparse, "period_trades": [20, 20, 0, 10, 0]}
    assert holdout_status(thinner) == HOLDOUT_INSUFFICIENT


def test_the_small_sample_bar_is_the_t_not_the_normal():
    """Using 1.96 over four observations is the error the table exists to avoid: at df=3 the
    true two-sided bar is 3.182, 62% wider. Between rows it rounds UP, because a wider bar
    refuses more and that is the direction a fail-closed gate must err."""
    from runtime.mvp_runtime.crypto.robustness import t_critical_95

    assert t_critical_95(3) == 3.182
    assert t_critical_95(4) == 2.776
    assert t_critical_95(13) == t_critical_95(15)      # rounds to the wider tabulated row
    assert t_critical_95(1000) == CONFIDENCE_Z         # the large-sample limit
    assert t_critical_95(0) == float("inf")            # nothing to draw an interval over


def test_a_tail_with_no_spread_is_absence_of_variation_not_evidence_of_none():
    """A zero-width interval excludes zero for free — ``dashboard.sample_verdict`` refuses it
    for the same reason, and this is the same interval."""
    assert holdout_status(_tail(closed=40, expectancy=1.0, stdev=0.0)) == HOLDOUT_INSUFFICIENT


def _strong_inputs():
    """Inputs that clear ROBUST_SCORE_THRESHOLD on the in-sample components alone."""
    spec = StrategySpec.from_dict(_spec_dict())
    metrics = {"trade_count": 400, "total_net_r": 100.0, "fee_cost_r": 1.0, "slippage_cost_r": 1.0}
    walk_forward = {"walk_forward_pass_rate": 1.0, "temporal_stability": 1.0}
    regimes = {"regimes_traded": ["TREND_UP", "RANGE"], "profitable_regime_count": 2}
    return spec, metrics, walk_forward, regimes


@pytest.mark.parametrize("holdout,expected_verdict", [
    (_tail(expectancy=_CLEARS), ROBUST),                     # confirmed out-of-sample
    (_tail(expectancy=-_CLEARS), PROVISIONAL),               # scored well, failed forward
    (_tail(expectancy=_MARGINAL), PROVISIONAL),              # profitable, but says nothing
    (_tail(closed=1, expectancy=2.0), PROVISIONAL),          # too thin to confirm
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
                                 holdout=_tail(expectancy=_CLEARS))
    contradicted = score_robustness(spec, metrics, walk_forward, regimes,
                                    holdout=_tail(expectancy=-_CLEARS))
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


def test_the_holdout_block_carries_the_spread_its_gate_needs():
    """The field and the gate ship together: a block the factory writes must always be
    judgeable on its own contents, never INSUFFICIENT for a reason the writer could fix."""
    rising = [100.0 + i for i in range(400)]
    holdout = factory.backtest_spec(
        StrategySpec.from_dict(_spec_dict()), _snapshot(rising))["holdout"]
    assert "stdev_r" in holdout
    assert isinstance(holdout["stdev_r"], float)
    if holdout["closed_count"] >= 2:
        assert holdout["stdev_r"] >= 0.0
