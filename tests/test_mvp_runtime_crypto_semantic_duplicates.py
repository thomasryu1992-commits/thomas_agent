"""Semantic-duplicate guard — the same strategy under a different rule hash.

`strategy_rule_hash` covers the condition SEQUENCE, so reordering conditions or
appending one that never discriminates mints a "new" strategy that clears every
existing duplicate check. Under test: both exact tests catch what they claim, neither
fires on strategies that merely resemble each other, and the promotion and import
doors refuse a batch that would seat a clone.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import pool
from runtime.mvp_runtime.crypto.strategy import StrategySpec
from runtime.mvp_runtime.errors import ToolError

WINDOW = "sha256:window-a"
OTHER_WINDOW = "sha256:window-b"


def _spec(conditions, *, strategy_id="S1", operator="AND", stop=1.5, symbol="BTCUSDT"):
    return {
        "schema_version": "strategy_spec.v1",
        "strategy_id": strategy_id, "strategy_version": "1.0", "strategy_family": "breakout",
        "symbol_scope": [symbol], "timeframe": "1d", "direction": "long",
        "entry_rules": {"operator": operator, "conditions": conditions},
        "exit_rules": {"stop_model": "atr", "stop_atr": stop, "target_atr": 2.0,
                       "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }


def _record(spec_dict, *, window=WINDOW, closed=40, wins=20, expectancy=0.25,
            mdd=3.0, net=10.0, generation="GEN-001"):
    spec = StrategySpec.from_dict(spec_dict)
    return {
        "strategy_id": spec.strategy_id,
        "strategy_rule_hash": spec.strategy_rule_hash,
        "generation_id": generation,
        "strategy_spec": spec.to_dict(),
        "evidence_input_sha256": window,
        "backtest_evidence": {
            "closed_count": closed, "win_count": wins, "loss_count": closed - wins,
            "expectancy": expectancy, "max_drawdown": mdd,
            "cost_summary": {"total_net_r": net},
        },
    }


CLOSE_GT_MA20 = {"feature": "close", "comparison": ">", "value_from": "ma20"}
ADX_GE = {"feature": "adx", "comparison": ">=", "value": 20.0}
LOW_LT_HIGH = {"feature": "low", "comparison": "<", "value_from": "high"}


# --- rule form: order cannot change what a spec does --------------------------

def test_reordering_conditions_is_the_same_strategy():
    a = _record(_spec([CLOSE_GT_MA20, ADX_GE], strategy_id="S1"))
    b = _record(_spec([ADX_GE, CLOSE_GT_MA20], strategy_id="S2"), generation="GEN-002")
    assert a["strategy_rule_hash"] != b["strategy_rule_hash"], "premise: hashes differ"
    assert pool.canonical_rule_form(a) == pool.canonical_rule_form(b)
    groups = pool.semantic_duplicate_groups([a, b])
    assert len(groups) == 1 and groups[0]["match"] == "rule_form"


def test_rule_form_separates_genuinely_different_specs():
    a = _record(_spec([CLOSE_GT_MA20], strategy_id="S1"))
    for other in (
        _spec([CLOSE_GT_MA20, ADX_GE], strategy_id="S2"),          # an extra condition
        _spec([CLOSE_GT_MA20], strategy_id="S3", operator="OR"),   # a different operator
        _spec([CLOSE_GT_MA20], strategy_id="S4", stop=1.6),        # different exits
        _spec([CLOSE_GT_MA20], strategy_id="S5", symbol="ETHUSDT"),  # different scope
    ):
        assert pool.canonical_rule_form(a) != pool.canonical_rule_form(_record(other))


# --- behaviour: the padded-clone case -----------------------------------------

def test_a_padded_clone_is_caught_even_though_the_rules_differ():
    """The real 2026-07-29 case: base rule + an always-true condition."""
    base = _record(_spec([CLOSE_GT_MA20, ADX_GE], strategy_id="S2758"))
    padded = _record(_spec([CLOSE_GT_MA20, ADX_GE, LOW_LT_HIGH], strategy_id="S2955"),
                     generation="GEN-002")
    assert pool.canonical_rule_form(base) != pool.canonical_rule_form(padded), \
        "the rules genuinely differ — only the behaviour is identical"
    groups = pool.semantic_duplicate_groups([base, padded])
    assert len(groups) == 1 and groups[0]["match"] == "behaviour"
    assert groups[0]["strategy_ids"] == ["S2758", "S2955"]


def test_behaviour_is_only_comparable_within_one_window():
    a = _record(_spec([CLOSE_GT_MA20], strategy_id="S1"))
    b = _record(_spec([ADX_GE], strategy_id="S2"), window=OTHER_WINDOW, generation="GEN-002")
    assert pool.semantic_duplicate_groups([a, b]) == []


@pytest.mark.parametrize("field,value", [
    ("closed", 41), ("wins", 21), ("expectancy", 0.2500001), ("mdd", 3.0001), ("net", 10.0001),
])
def test_any_aggregate_difference_separates_them(field, value):
    a = _record(_spec([CLOSE_GT_MA20], strategy_id="S1"))
    b = _record(_spec([ADX_GE], strategy_id="S2"), generation="GEN-002", **{field: value})
    assert pool.semantic_duplicate_groups([a, b]) == [], f"{field} differs — not the same trades"


def test_no_trade_specs_do_not_all_collapse_into_one_group():
    """Zero-trade specs 'agree' trivially; treating that as identity would group every
    inert spec in the store into one enormous false duplicate."""
    a = _record(_spec([CLOSE_GT_MA20], strategy_id="S1"), closed=0, wins=0)
    b = _record(_spec([ADX_GE], strategy_id="S2"), closed=0, wins=0, generation="GEN-002")
    assert pool.behavioural_fingerprint(a) is None
    assert pool.semantic_duplicate_groups([a, b]) == []


def test_one_lineage_re_scored_is_not_a_duplicate_of_itself():
    """The store is append-only, so re-scoring a spec on a new window ADDS a row. Both
    rows carry the same rule hash — one strategy measured twice, which is the point of
    re-scoring, not two strategies. Observed on all 25 re-scored 1d lineages."""
    spec = _spec([CLOSE_GT_MA20, ADX_GE], strategy_id="S271")
    original = _record(spec)
    rescored = _record(spec, window=OTHER_WINDOW)
    rescored["candidate_id"] = "cand_rescored"
    assert original["strategy_rule_hash"] == rescored["strategy_rule_hash"]
    assert pool.canonical_rule_form(original) == pool.canonical_rule_form(rescored)
    assert pool.semantic_duplicate_groups([rescored], incumbents=[original]) == []
    pool.assert_no_semantic_duplicates([rescored], incumbents=[original])


def test_partial_evidence_says_nothing_behaviourally():
    """Two rows agreeing on the two aggregates they happen to record is not the same
    claim as agreeing on all of them — otherwise every sparsely-recorded row becomes
    everyone else's duplicate."""
    partial = {"closed_count": 20, "expectancy": 0.5}
    a = _record(_spec([CLOSE_GT_MA20], strategy_id="S1"))
    b = _record(_spec([ADX_GE], strategy_id="S2"), generation="GEN-002")
    a["backtest_evidence"] = dict(partial)
    b["backtest_evidence"] = dict(partial)
    assert pool.behavioural_fingerprint(a) is None
    assert pool.semantic_duplicate_groups([a, b]) == []


def test_evidence_free_records_fall_back_to_rule_form_only():
    a = {"strategy_id": "S1", "generation_id": "GEN-001", "strategy_rule_hash": "aaa",
         "strategy_spec": _spec([CLOSE_GT_MA20, ADX_GE], strategy_id="S1")}
    b = {"strategy_id": "S2", "generation_id": "GEN-002", "strategy_rule_hash": "bbb",
         "strategy_spec": _spec([ADX_GE, CLOSE_GT_MA20], strategy_id="S2")}
    assert pool.behavioural_fingerprint(a) is None
    groups = pool.semantic_duplicate_groups([a, b])
    assert len(groups) == 1 and groups[0]["match"] == "rule_form"


# --- incumbents ---------------------------------------------------------------

def test_an_incumbent_clone_blocks_the_promotion():
    incumbent = _record(_spec([CLOSE_GT_MA20, ADX_GE], strategy_id="S_OLD"))
    incoming = _record(_spec([CLOSE_GT_MA20, ADX_GE, LOW_LT_HIGH], strategy_id="S_NEW"),
                       generation="GEN-002")
    groups = pool.semantic_duplicate_groups([incoming], incumbents=[incumbent])
    assert len(groups) == 1 and groups[0]["strategy_ids"] == ["S_NEW", "S_OLD"]


def test_a_pool_that_already_holds_a_clone_does_not_block_unrelated_work():
    """Otherwise every promotion is hostage to a pre-existing duplicate nobody has
    cleaned up yet — the guard would punish the wrong batch."""
    clone_a = _record(_spec([CLOSE_GT_MA20, ADX_GE], strategy_id="S_OLD1"))
    clone_b = _record(_spec([CLOSE_GT_MA20, ADX_GE, LOW_LT_HIGH], strategy_id="S_OLD2"),
                      generation="GEN-002")
    unrelated = _record(_spec([ADX_GE], strategy_id="S_NEW"), generation="GEN-003",
                        closed=17, wins=9, expectancy=0.9, mdd=1.1, net=2.2)
    assert pool.semantic_duplicate_groups([unrelated], incumbents=[clone_a, clone_b]) == []


# --- the refusal --------------------------------------------------------------

def test_assert_raises_with_a_stable_reason_code():
    base = _record(_spec([CLOSE_GT_MA20, ADX_GE], strategy_id="S2758"))
    padded = _record(_spec([CLOSE_GT_MA20, ADX_GE, LOW_LT_HIGH], strategy_id="S2955"),
                     generation="GEN-002")
    with pytest.raises(ToolError) as exc:
        pool.assert_no_semantic_duplicates([base, padded])
    assert exc.value.reason_code == "CANDIDATE_SEMANTIC_DUPLICATE"
    assert "S2758" in str(exc.value) and "S2955" in str(exc.value)
    assert "--allow-duplicates" in str(exc.value), "the escape must be named in the refusal"


def test_assert_passes_a_clean_batch():
    a = _record(_spec([CLOSE_GT_MA20], strategy_id="S1"))
    b = _record(_spec([ADX_GE], strategy_id="S2"), generation="GEN-002",
                closed=31, wins=12, expectancy=0.11, mdd=2.2, net=3.4)
    pool.assert_no_semantic_duplicates([a, b])  # must not raise


# --- near duplicates: reported, never refused ---------------------------------

def test_near_duplicates_are_reported_but_do_not_refuse():
    """The real SOLUSDT pair: same window, same 82 trades and 45/37 split, net R
    differing by 0.0016. A judgement, so it is surfaced rather than gated."""
    a = _record(_spec([CLOSE_GT_MA20], strategy_id="S3094"),
                closed=82, wins=45, expectancy=0.51907863, net=42.56444761)
    b = _record(_spec([ADX_GE], strategy_id="S2965"), generation="GEN-002",
                closed=82, wins=45, expectancy=0.51905943, net=42.56287318)
    pool.assert_no_semantic_duplicates([a, b])  # must not raise
    near = pool.near_duplicate_groups([a, b])
    assert len(near) == 1 and near[0]["strategy_ids"] == ["S2965", "S3094"]


def test_exact_duplicates_are_not_also_reported_as_near():
    a = _record(_spec([CLOSE_GT_MA20], strategy_id="S1"))
    b = _record(_spec([ADX_GE], strategy_id="S2"), generation="GEN-002")
    assert pool.near_duplicate_groups([a, b]) == []
