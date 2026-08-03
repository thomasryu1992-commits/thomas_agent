"""The factory can finally mint the hypothesis the vocabulary could not express.

#456 gave a condition a ``lag``; nothing used it. These are the first families that do, and
they exist as PAIRS with the state families they mirror — `ma_cross_up` beside `trend_pullback`,
`macd_cross_up` beside the same filters — so that if a crossover scores differently the
difference is attributable to the one thing that changed: state versus event.

A state family is eligible on every bar of a trend, so which bar it actually enters on is
decided by whichever bar its other conditions happen to clear. A crossover asks for the bar the
relationship changed. That is a different hypothesis, and until now the search could not see it.

And a lag is a searched degree of freedom, so it is counted as one — otherwise
``trades_per_parameter`` gets quietly more optimistic at exactly the moment the space got
bigger, and that ratio drives both ``sample_adequacy`` and the FRAGILE veto.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto.factory import TEMPLATES
from runtime.mvp_runtime.crypto.robustness import EXIT_FREE_PARAMETERS, count_free_parameters
from runtime.mvp_runtime.crypto.strategy import StrategySpec

CROSSOVER_FAMILIES = ("ma_cross_up", "ma_cross_down", "macd_cross_up", "macd_cross_down")


def _template(family):
    return next(t for t in TEMPLATES if t.family == family)


def _spec(family):
    template = _template(family)
    return StrategySpec.from_dict({
        "schema_version": "strategy_spec.v1", "strategy_id": "S1", "strategy_version": "1.0",
        "strategy_family": family, "symbol_scope": ["BTCUSDT"], "timeframe": "1h",
        "direction": template.direction,
        "entry_rules": {"operator": "AND",
                        "conditions": template.entry_builder(dict(template.base_params))},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.45, "target_atr": 3.0,
                       "max_holding_bars": 24},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    })


# --- the families exist and are shaped like crossovers -------------------------

@pytest.mark.parametrize("family", CROSSOVER_FAMILIES)
def test_each_crossover_family_is_registered(family):
    assert _template(family).family == family


@pytest.mark.parametrize("family", CROSSOVER_FAMILIES)
def test_a_crossover_asks_for_now_and_for_one_bar_ago(family):
    """Two conditions on the same pair of series, opposite comparisons, one lagged. Anything
    else is a state test wearing a crossover's name."""
    conditions = _spec(family).entry_rules.conditions
    lagged = [c for c in conditions if c.lag]
    assert len(lagged) == 1, "exactly one condition looks back"
    now = next(c for c in conditions if not c.lag and c.value_from == lagged[0].value_from)
    assert now.feature == lagged[0].feature
    assert {now.comparison, lagged[0].comparison} in (
        {">", "<="}, {"<", ">="},
    ), "the lagged leg must NEGATE the current one, or it is not a crossing"


@pytest.mark.parametrize("family,direction", [
    ("ma_cross_up", "long"), ("macd_cross_up", "long"),
    ("ma_cross_down", "short"), ("macd_cross_down", "short"),
])
def test_the_direction_matches_which_way_it_crosses(family, direction):
    assert _template(family).direction == direction
    now = next(c for c in _spec(family).entry_rules.conditions if not c.lag and c.value_from)
    assert now.comparison == (">" if direction == "long" else "<")


def test_the_pair_keeps_the_state_familys_filters():
    """The comparison is the point: one change, attributable. A crossover that also brought new
    filters would score differently for reasons nobody could separate."""
    cross = {c.feature for c in _spec("ma_cross_up").entry_rules.conditions if c.value is not None}
    state = {c.feature for c in _spec("trend_pullback").entry_rules.conditions if c.value is not None}
    assert cross <= state


# --- and the accounting keeps up ------------------------------------------------

def test_a_lag_is_counted_as_a_degree_of_freedom():
    """Looking back is a decision the search made about where to look, not a property of the
    market. Uncounted, `trades_per_parameter` gets more optimistic as the space gets bigger."""
    conditions = _spec("ma_cross_up").entry_rules.conditions
    literals = sum(1 for c in conditions if c.value is not None)
    lags = sum(1 for c in conditions if c.lag)
    assert lags == 1
    assert count_free_parameters(_spec("ma_cross_up")) == literals + lags + EXIT_FREE_PARAMETERS


def test_an_unlagged_spec_is_charged_exactly_what_it_was_before():
    """Charging every condition for the OPTION of a lag would move free_parameters on every spec
    ever scored — a re-rating of the whole store dressed up as an accounting fix."""
    spec = _spec("trend_pullback")
    literals = sum(1 for c in spec.entry_rules.conditions if c.value is not None)
    assert count_free_parameters(spec) == literals + EXIT_FREE_PARAMETERS


def test_two_lags_cost_two():
    raw = _spec("ma_cross_up").to_dict()
    raw["entry_rules"]["conditions"].append(
        {"feature": "rsi", "comparison": "<=", "value": 60.0, "lag": 1})
    # The stored hash describes the rules BEFORE that append, and `from_dict` verifies rather
    # than re-mints — so it must be dropped, not carried. That refusal is the invariant working:
    # a spec whose rules changed under a hash that did not is exactly what it exists to catch.
    raw.pop("strategy_rule_hash")
    spec = StrategySpec.from_dict(raw)
    literals = sum(1 for c in spec.entry_rules.conditions if c.value is not None)
    assert count_free_parameters(spec) == literals + 2 + EXIT_FREE_PARAMETERS


# --- it still hashes and evaluates ---------------------------------------------

@pytest.mark.parametrize("family", CROSSOVER_FAMILIES)
def test_a_minted_crossover_round_trips(family):
    spec = _spec(family)
    assert StrategySpec.from_dict(spec.to_dict()).strategy_rule_hash == spec.strategy_rule_hash


def test_a_crossover_family_fires_on_the_change_not_the_state():
    from runtime.mvp_runtime.crypto.features import attach_previous_bar
    from runtime.mvp_runtime.crypto.strategy import evaluate_spec

    spec = _spec("ma_cross_up")
    adx = 99.0  # clear the filter so only the crossing decides
    rows = attach_previous_bar([
        {"ma20": 9.0, "ma50": 10.0, "adx": adx},
        {"ma20": 11.0, "ma50": 10.0, "adx": adx},
        {"ma20": 12.0, "ma50": 10.0, "adx": adx},
    ])
    assert [evaluate_spec(spec, r).matched for r in rows] == [False, True, False]
