"""D1 — the exit can move the stop after entry, and the identity hash does not notice.

The exit model was three scalars decided at entry: a stop, a target, a bar count. Entry got 20
template families and a mutation operator; the exit got three numbers, which is a strange place
to stop when the exit is what turns a signal into a return. The store agrees — settled time
exits average +1.02R (n=13 native) and +1.31R in the counterfactual shadow (n=58), so the bar
counter is closing positions that were still ahead.

Two rules land here, both pure functions of the closed-bar price path: `breakeven_at_r` and
`trail_atr`. What these tests pin, in order of how much damage getting it wrong would do:

1. every spec already in the store keeps its `strategy_rule_hash` — `from_dict` VERIFIES that
   hash rather than re-minting it, so a fingerprint that gained a key unconditionally would
   make 1020 candidates and the whole active pool fail to load;
2. a moved stop reports the R it actually returned, not the -1.0 constant;
3. the ratchet cannot loosen a stop, cannot trail a wick, and cannot be tested against the
   bar that moved it;
4. the live door refuses a plan it cannot execute rather than degrading it to a fixed stop.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto.paper import advance_managed_stop, settle_trade_plan
from runtime.mvp_runtime.crypto.strategy import SpecParseError, StrategySpec


def _spec_dict(**exit_overrides):
    exit_rules = {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 3.0, "max_holding_bars": 10}
    exit_rules.update(exit_overrides)
    return {
        "schema_version": "strategy_spec.v1",
        "strategy_id": "S1", "strategy_version": "1.0", "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"], "timeframe": "1h", "direction": "long",
        "entry_rules": {"operator": "AND",
                        "conditions": [{"feature": "close", "comparison": ">", "value_from": "ma20"}]},
        "exit_rules": exit_rules,
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }


def _bar(close, high=None, low=None):
    return {"open": close, "high": high if high is not None else close,
            "low": low if low is not None else close, "close": close,
            "close_time": "2026-08-03T00:00:00Z"}


def _position(**overrides):
    """A LONG at 100 with a 10-wide stop: 1R = 10, stop 90, target 130."""
    position = {"direction": "LONG", "entry_price": 100.0, "stop_loss": 90.0,
                "take_profit": 130.0, "risk": 10.0, "holding_candles": 0}
    position.update(overrides)
    return position


# --- 1. the identity hash ------------------------------------------------------

def test_a_spec_without_the_new_rules_hashes_exactly_as_before():
    """The load-bearing one. This hash was minted before the fields existed; it is pinned
    literally rather than recomputed, because a test that recomputes both sides would pass
    just as happily if the fingerprint changed for everyone."""
    spec = StrategySpec.from_dict(_spec_dict())
    # Computed on the commit BEFORE this change (origin/main @ 9eb1f20) and pasted in.
    assert spec.strategy_rule_hash == (
        "b1970aed63d55d6f9be2b21a81c054489fdd0f45419997abc3ca453402b0e575"
    )


def test_off_and_predating_the_field_are_the_same_strategy():
    """A spec that declines the rule and one written before it existed must hash the same,
    or turning a rule off would re-mint an identity that never changed."""
    absent = StrategySpec.from_dict(_spec_dict())
    explicit_null = StrategySpec.from_dict(_spec_dict(breakeven_at_r=None, trail_atr=None))
    assert absent.strategy_rule_hash == explicit_null.strategy_rule_hash


def test_turning_a_rule_on_is_a_different_strategy():
    base = StrategySpec.from_dict(_spec_dict())
    managed = StrategySpec.from_dict(_spec_dict(trail_atr=1.0))
    breakeven = StrategySpec.from_dict(_spec_dict(breakeven_at_r=1.0))
    assert len({base.strategy_rule_hash, managed.strategy_rule_hash,
                breakeven.strategy_rule_hash}) == 3


def test_to_dict_round_trips_without_inventing_keys():
    """`to_dict` feeds pool writes. Emitting a null would rewrite every stored spec into a
    shape that no longer matches the hash it carries."""
    raw = _spec_dict()
    spec = StrategySpec.from_dict(raw)
    assert spec.to_dict()["exit_rules"] == raw["exit_rules"]
    assert StrategySpec.from_dict(spec.to_dict()).strategy_rule_hash == spec.strategy_rule_hash


def test_a_managed_spec_round_trips_with_its_rules():
    spec = StrategySpec.from_dict(_spec_dict(breakeven_at_r=1.0, trail_atr=2.0))
    reparsed = StrategySpec.from_dict(spec.to_dict())
    assert (reparsed.exit_rules.breakeven_at_r, reparsed.exit_rules.trail_atr) == (1.0, 2.0)
    assert reparsed.strategy_rule_hash == spec.strategy_rule_hash


@pytest.mark.parametrize("field", ["breakeven_at_r", "trail_atr"])
@pytest.mark.parametrize("bad", [0, -1.0, "wide", []])
def test_a_present_rule_must_be_a_usable_number(field, bad):
    """Zero is refused rather than read as off: a stop trailing by zero ATR is a stop at the
    last close, which is a very different trade from no trailing at all."""
    with pytest.raises(SpecParseError):
        StrategySpec.from_dict(_spec_dict(**{field: bad}))


def test_manages_stop_is_the_property_the_live_door_reads():
    assert not StrategySpec.from_dict(_spec_dict()).exit_rules.manages_stop()
    assert StrategySpec.from_dict(_spec_dict(trail_atr=1.0)).exit_rules.manages_stop()
    assert StrategySpec.from_dict(_spec_dict(breakeven_at_r=0.5)).exit_rules.manages_stop()


# --- 2. a moved stop is worth what it returned ---------------------------------

def test_an_unmanaged_stop_keeps_the_exact_minus_one_convention():
    """`build_outcome_record` names `settle_trade_plan` the authority on the gross number
    including this constant. Deriving it from prices would agree today and be a second thing
    to keep agreeing tomorrow."""
    reason, price, r = settle_trade_plan(_position(), _bar(89.0, high=89.0, low=88.0),
                                         89.0, 10, False)
    assert (reason, price, r) == ("stop_loss", 90.0, -1.0)


def test_a_breakeven_stop_that_is_hit_returns_zero_not_minus_one():
    """The whole point of moving it. Reported as -1.0 the rule would look like it changed
    nothing, and every backtest of it would be wrong by exactly 1R per rescued trade."""
    position = _position(breakeven_at_r=1.0)
    # Bar 1 closes +1R: survives, stop ratchets to entry.
    assert settle_trade_plan(position, _bar(110.0), 110.0, 10, False) == (None, None, None)
    assert position["stop_loss"] == 100.0
    # Bar 2 reverses through it.
    reason, price, r = settle_trade_plan(position, _bar(95.0, high=101.0, low=95.0),
                                         95.0, 10, False)
    assert (reason, price, r) == ("stop_loss", 100.0, 0.0)


def test_a_trailed_stop_that_is_hit_returns_a_win():
    position = _position(trail_distance=5.0)
    assert settle_trade_plan(position, _bar(120.0), 120.0, 10, False) == (None, None, None)
    assert position["stop_loss"] == 115.0
    reason, price, r = settle_trade_plan(position, _bar(114.0, high=120.0, low=114.0),
                                         114.0, 10, False)
    assert (reason, price) == ("stop_loss", 115.0)
    assert r == pytest.approx(1.5)          # (115 - 100) / 10


# --- 3. the ratchet ------------------------------------------------------------

def test_the_stop_never_loosens():
    position = _position(trail_distance=5.0)
    settle_trade_plan(position, _bar(120.0), 120.0, 10, False)
    assert position["stop_loss"] == 115.0
    settle_trade_plan(position, _bar(116.0, high=120.0, low=116.0), 116.0, 10, False)
    assert position["stop_loss"] == 115.0   # 116 - 5 = 111 is worse; ignored


def test_the_trail_follows_the_close_not_the_high():
    """Trailing the extreme would raise the stop on a wick the position never held through a
    close — an intrabar assumption of exactly the kind `resolve_intrabar_exit` exists to keep
    from being made silently."""
    position = _position(trail_distance=5.0)
    advance_managed_stop(position, _bar(105.0, high=140.0, low=104.0))
    assert position["stop_loss"] == 100.0   # 105 - 5, not 140 - 5


def test_the_stop_moved_on_a_bar_is_not_tested_against_that_bar():
    """Otherwise a bar whose close ratchets the stop above its own low would stop the trade
    out at a level it never rested at."""
    position = _position(trail_distance=5.0)
    # Low of 96 is below the stop this bar's close (110) would imply (105), and above the
    # standing stop (90). The bar must survive.
    assert settle_trade_plan(position, _bar(110.0, high=111.0, low=96.0), 110.0, 10, False) == (
        None, None, None)
    assert position["stop_loss"] == 105.0


def test_a_position_carrying_no_rules_never_moves():
    position = _position()
    assert advance_managed_stop(position, _bar(150.0)) is False
    assert position["stop_loss"] == 90.0
    assert "stop_moved" not in position


def test_breakeven_does_not_fire_before_its_threshold():
    position = _position(breakeven_at_r=2.0)
    assert advance_managed_stop(position, _bar(115.0)) is False      # +1.5R
    assert advance_managed_stop(position, _bar(120.0)) is True       # +2.0R
    assert position["stop_loss"] == 100.0


def test_both_rules_together_take_the_tighter_stop():
    position = _position(breakeven_at_r=1.0, trail_distance=5.0)
    advance_managed_stop(position, _bar(112.0))
    assert position["stop_loss"] == 107.0    # max(entry 100, 112 - 5)


def test_a_short_ratchets_downward():
    position = _position(direction="SHORT", stop_loss=110.0, take_profit=70.0,
                         trail_distance=5.0)
    advance_managed_stop(position, _bar(90.0))
    assert position["stop_loss"] == 95.0
    reason, price, r = settle_trade_plan(position, _bar(96.0, high=96.0, low=90.0),
                                         96.0, 10, False)
    assert (reason, price) == ("stop_loss", 95.0)
    assert r == pytest.approx(0.5)           # (100 - 95) / 10


@pytest.mark.parametrize("candle,risk", [(None, 10.0), (_bar(120.0), 0.0)])
def test_an_unpriceable_bar_moves_nothing(candle, risk):
    position = _position(trail_distance=5.0, risk=risk)
    assert advance_managed_stop(position, candle) is False
    assert position["stop_loss"] == 90.0


# --- 4. backtest / live parity -------------------------------------------------

# The live door's refusal is exercised through its own harness, in
# `test_mvp_runtime_crypto_live_entry.py` — that file already builds a decision with every
# other door open, which is the only way to prove THIS door is the one that closed.

def test_the_backtest_and_the_paper_plan_carry_the_same_two_fields():
    """`max_holding_bars` rides into the plan so live cannot hold longer than the evidence
    allowed. These ride for the same reason, and a divergence here is the one that would let a
    strategy trade an exit nobody scored."""
    import inspect

    from runtime.mvp_runtime.crypto import factory, paper

    plan_src = inspect.getsource(paper.build_entry_plan)
    replay_src = inspect.getsource(factory._replay)
    for field in ("breakeven_at_r", "trail_distance"):
        assert field in plan_src, f"build_entry_plan drops {field}"
        assert field in replay_src, f"the backtest replay drops {field}"
