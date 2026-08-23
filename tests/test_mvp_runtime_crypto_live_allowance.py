"""#615 §5 — the per-lineage LIVE allowance, and the two things it must never become.

It must never become a **verdict**: at this sample size nothing can say a strategy is bad, and a
device that pretended otherwise would retire healthy lineages on noise. What it says is that the
amount we were willing to risk on an unproven lineage is spent.

It must never become a way to **arm** one. `pool.disarm_live_tier` has no argument for a target
tier, so that is a property of the signature rather than of any caller — asserted here, because
the whole value of #610 Part 1 is that arming stays the operator door.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import pool as pool_store
from runtime.mvp_runtime.crypto.live_allowance import (
    BREACH_CONSECUTIVE,
    BREACH_CUMULATIVE_R,
    BREACH_HISTORY_UNREADABLE,
    evaluate_live_allowance,
)

NOW = "2026-08-09T12:00:00Z"


def _spec(sid):
    return {
        "schema_version": "strategy_spec.v1",
        "strategy_id": sid, "strategy_version": "1.0", "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"], "timeframe": "1d", "direction": "long",
        "entry_rules": {"operator": "AND",
                        "conditions": [{"feature": "close", "comparison": ">", "value": 0.0}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0,
                       "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }


def _entry(sid="S1", candidate_id="c1", tier=pool_store.LIVE_TIER_LIVE):
    return {
        "strategy_id": sid, "candidate_id": candidate_id, "status": "PAPER_ACTIVE",
        "strategy_spec": _spec(sid), "strategy_rule_hash": "h1", "generation_id": "GEN-1",
        pool_store.LIVE_TIER_FIELD: tier,
    }


def _pool(*entries):
    return {"active_strategies": list(entries)}


def _out(r, candidate_id="c1"):
    return {"outcome_closed": True, "result_R": r, "candidate_id": candidate_id}


def _armed(*sids):
    return set(sids)


# --- it fires when the allowance is spent -------------------------------------

def test_two_consecutive_live_losses_spend_the_allowance():
    res = evaluate_live_allowance(
        [_out(-1.0), _out(-0.5)], live_routable_strategy_ids=_armed("S1"), pool=_pool(_entry()),
    )
    assert [b["strategy_id"] for b in res["breached"]] == ["S1"]
    assert BREACH_CONSECUTIVE in res["breached"][0]["reasons"]


def test_a_win_between_losses_does_not():
    """The streak rule is the pool-wide breaker's own — newest-first until a non-loss — so the
    two controls cannot disagree about what a streak is."""
    res = evaluate_live_allowance(
        [_out(-1.0), _out(0.5), _out(-1.0)],
        live_routable_strategy_ids=_armed("S1"), pool=_pool(_entry()),
    )
    assert res["breached"] == []


def test_cumulative_loss_spends_it_even_without_a_streak():
    """Two different ways to spend the same allowance: quickly, or by bleeding."""
    res = evaluate_live_allowance(
        [_out(-1.2), _out(0.1), _out(-1.1)],
        live_routable_strategy_ids=_armed("S1"), pool=_pool(_entry()),
    )
    assert BREACH_CUMULATIVE_R in res["breached"][0]["reasons"]
    assert BREACH_CONSECUTIVE not in res["breached"][0]["reasons"]


def test_a_profitable_lineage_is_untouched():
    res = evaluate_live_allowance(
        [_out(1.0), _out(-0.4)], live_routable_strategy_ids=_armed("S1"), pool=_pool(_entry()),
    )
    assert res["breached"] == []


# --- and the ways it must not fire --------------------------------------------

def test_a_lineage_that_is_not_armed_cannot_spend_an_allowance():
    """Disarming what is already disarmed is noise, and a lineage that never had the permission
    never spent anything."""
    res = evaluate_live_allowance(
        [_out(-1.0), _out(-1.0)],
        live_routable_strategy_ids=set(),
        pool=_pool(_entry(tier=pool_store.LIVE_TIER_OBSERVATION)),
    )
    assert res["breached"] == [] and res["armed_count"] == 0


def test_another_lineages_losses_are_not_charged_here():
    """Attribution is by LINEAGE, never by display id — `strategy_id` restarts at S001 each
    generation, so counting by it would charge a fresh strategy for its predecessor's losses."""
    res = evaluate_live_allowance(
        [_out(-1.0, candidate_id="OTHER"), _out(-1.0, candidate_id="OTHER")],
        live_routable_strategy_ids=_armed("S1"), pool=_pool(_entry()),
    )
    assert res["breached"] == []


def test_an_unattributable_outcome_is_counted_against_nothing_and_reported():
    """Misattributing a live loss is worse than not attributing it."""
    res = evaluate_live_allowance(
        [{"outcome_closed": True, "result_R": -5.0}],
        live_routable_strategy_ids=_armed("S1"), pool=_pool(_entry()),
    )
    assert res["breached"] == [] and res["unattributed_outcomes"] == 1


def test_open_outcomes_are_not_counted():
    res = evaluate_live_allowance(
        [{"outcome_closed": False, "result_R": -9.0, "candidate_id": "c1"}],
        live_routable_strategy_ids=_armed("S1"), pool=_pool(_entry()),
    )
    assert res["breached"] == []


# --- the conservative direction -----------------------------------------------

def test_an_unreadable_history_breaches_every_armed_lineage():
    """The asymmetry is deliberate: being wrong here costs paper evidence for a while, while
    being wrong the other way spends real money against a limit nobody could verify."""
    res = evaluate_live_allowance(
        None, live_routable_strategy_ids=_armed("S1", "S2"),
        pool=_pool(_entry("S1", "c1"), _entry("S2", "c2")),
    )
    assert [b["strategy_id"] for b in res["breached"]] == ["S1", "S2"]
    assert all(b["reasons"] == [BREACH_HISTORY_UNREADABLE] for b in res["breached"])


def test_an_unreadable_history_with_nothing_armed_is_silent():
    """Nothing to protect, nothing to report — the conservative branch must not manufacture
    breaches for lineages that could not have traded anyway."""
    assert evaluate_live_allowance(None, live_routable_strategy_ids=set())["breached"] == []


# --- the writer, and what it structurally cannot do ---------------------------

def _write_pool(tmp_path, *entries):
    d = tmp_path / ".runtime_governance_state" / "crypto"
    d.mkdir(parents=True, exist_ok=True)
    (d / "active_strategy_pool.json").write_text(
        json.dumps({"active_strategies": list(entries)}), encoding="utf-8"
    )


def test_disarming_moves_the_tier_and_leaves_the_status_alone(tmp_path):
    """A tier move, not a demotion. The lineage keeps its slot and keeps papering — which is the
    whole point: it stays able to accrue the evidence the ladder will one day judge."""
    _write_pool(tmp_path, _entry())
    assert pool_store.disarm_live_tier(["S1"], root=tmp_path, now=NOW, reasons=["x"]) == 1
    entry = pool_store.load_active_pool(tmp_path)["active_strategies"][0]
    assert pool_store.entry_live_tier(entry) == pool_store.LIVE_TIER_OBSERVATION
    assert entry["status"] == "PAPER_ACTIVE"
    assert entry["live_tier_reasons"] == ["x"] and entry["live_tier_updated_at"] == NOW
    # ...and it is no longer live-routable, which is the effect that matters.
    assert pool_store.live_routable_strategy_ids(pool_store.load_active_pool(tmp_path)) == set()


def test_the_writer_has_no_way_to_express_arming():
    """The property #610 Part 1 bought, and the one this must not spend. Asserted on the
    signature rather than on the body: a body can be edited, but a caller cannot pass an
    argument that does not exist."""
    import inspect

    params = inspect.signature(pool_store.disarm_live_tier).parameters
    assert "live_tier" not in params and "tier" not in params
    # It may READ the live constant (that is how it decides whether there is anything to move);
    # what it must not do is ASSIGN it. One assignment to the field, and it writes OBSERVATION.
    source = inspect.getsource(pool_store.disarm_live_tier)
    writes = [ln.strip() for ln in source.splitlines()
              if f"[{pool_store.LIVE_TIER_FIELD}]" in ln or "LIVE_TIER_FIELD]" in ln]
    assert len(writes) == 1, writes
    assert "LIVE_TIER_OBSERVATION" in writes[0] and "LIVE_TIER_LIVE" not in writes[0]


def test_disarming_is_idempotent_and_silent_on_unknown_ids(tmp_path):
    """A caller judging live outcomes may name a lineage the pool no longer holds. Refusing there
    would turn a stale name into a cycle failure, and the lineage is already not routable."""
    _write_pool(tmp_path, _entry(tier=pool_store.LIVE_TIER_OBSERVATION))
    assert pool_store.disarm_live_tier(["S1"], root=tmp_path, now=NOW) == 0
    assert pool_store.disarm_live_tier(["GHOST"], root=tmp_path, now=NOW) == 0
    assert pool_store.disarm_live_tier([], root=tmp_path, now=NOW) == 0


def test_disarming_one_lineage_leaves_the_others_armed(tmp_path):
    """Targeted, which is the entire difference from the pool-wide breaker."""
    _write_pool(tmp_path, _entry("S1", "c1"), _entry("S2", "c2"))
    assert pool_store.disarm_live_tier(["S1"], root=tmp_path, now=NOW) == 1
    assert pool_store.live_routable_strategy_ids(pool_store.load_active_pool(tmp_path)) == {"S2"}


def test_the_pool_wide_breaker_keeps_its_numbers():
    """#615 §5.3, pinned. The obvious follow-on — "targeted stops mean the portfolio brake can
    relax" — would leave MORE trading running than today, which is a money-door widening and a
    separate approval. This device is additive."""
    from runtime.mvp_runtime.crypto import guards

    assert guards.MAX_CONSECUTIVE_LOSSES == 3
    assert guards.DAILY_MAX_LOSS_R == -2.0
    assert guards.WEEKLY_MAX_LOSS_R == -5.0


@pytest.mark.parametrize("limit,expected", [(1, True), (5, False)])
def test_the_limits_are_arguments_rather_than_constants(limit, expected):
    """The draft values have no calibration behind them and the proposal says so. Making them
    parameters is how that stays honest — a future measurement moves a number, not a rewrite."""
    res = evaluate_live_allowance(
        [_out(-0.1)], live_routable_strategy_ids=_armed("S1"), pool=_pool(_entry()),
        max_consecutive=limit, max_cumulative_r=-99.0,
    )
    assert bool(res["breached"]) is expected
