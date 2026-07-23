"""C10 lifecycle tests — auto-demotion ladder, terminal immutability, cycle wiring.

The source rules under test: a full window is required before any escalation (young
strategies never degraded on thin data), suspension needs 2 consecutive failing
evaluations and archive 3, terminal states are immutable here (reactivation is the
approval door), recovery is only ever WARNING/PROBATION → ACTIVE, and the one effect
— pool status updates — applies only through the real gated store."""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.crypto import paper, pool
from runtime.mvp_runtime.crypto.cycle import run_crypto_cycle
from runtime.mvp_runtime.crypto.lifecycle import (
    LifecycleThresholds,
    compute_metrics,
    compute_strategy_performance,
    evaluate_lifecycle,
    run_lifecycle,
)
from runtime.mvp_runtime.crypto.paper import DryRunPaperStore, RealPaperStore
from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.safety_gate import FILESYSTEM_WRITE, Authorization

NOW = "2026-07-22T12:00:00Z"

_AUTH = Authorization(
    flags=(FILESYSTEM_WRITE,), provider_id="paper_trading", activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z", evidence_ref=".runtime_governance_state/evidence.md",
)


def _outcomes(rs, strategy_id="S1"):
    return [
        {"result_R": r, "outcome_closed": True, "strategy_id": strategy_id,
         "created_at_utc": f"2026-07-{(i % 28) + 1:02d}T{i % 24:02d}:00:00Z", "outcome_id": f"o{i}"}
        for i, r in enumerate(rs)
    ]


def _perf(rs, **kwargs):
    return compute_strategy_performance("S1", _outcomes(rs), now=NOW, **kwargs)


# --- metrics ------------------------------------------------------------------

def test_empty_metrics_are_none_never_zero():
    m = compute_metrics([])
    assert m["win_rate"] is None and m["expectancy_r"] is None and m["profit_factor"] is None


def test_profit_factor_none_without_losses():
    m = compute_metrics(_outcomes([1.0, 2.0]))
    assert m["profit_factor"] is None  # an all-win window can never LOOK failing
    assert m["expectancy_r"] == 1.5


def test_window_full_flags():
    report = _perf([0.5] * 25)
    assert report["rolling_20"]["window_full"] is True
    assert report["rolling_30"]["window_full"] is False


# --- the ladder ---------------------------------------------------------------

def test_healthy_stays_active():
    decision = evaluate_lifecycle("PAPER_ACTIVE", _perf([1.0] * 60), now=NOW)
    assert decision["new_status"] == "PAPER_ACTIVE" and decision["status_changed"] is False
    assert decision["consecutive_failures"] == 0


def test_young_strategy_never_degraded_on_thin_data():
    decision = evaluate_lifecycle("PAPER_ACTIVE", _perf([-1.0] * 10), now=NOW)
    assert decision["new_status"] == "PAPER_ACTIVE"  # rolling_20 not full → no escalation


def test_warning_on_full_bad_20_window():
    rs = [1.0] * 30 + [-0.05] * 20  # lifetime fine, last 20 negative; last 30 > probation line
    decision = evaluate_lifecycle("PAPER_ACTIVE", _perf(rs), now=NOW)
    assert decision["new_status"] == "WARNING" and decision["is_escalation"] is True
    assert "rolling_20_below_warn_thresholds" in decision["reasons"]


def test_probation_on_full_bad_30_window():
    decision = evaluate_lifecycle("PAPER_ACTIVE", _perf([-0.1] * 30), now=NOW)
    assert decision["new_status"] == "PROBATION"


def test_suspend_needs_two_consecutive_failures():
    rs = [-0.1] * 50
    first = evaluate_lifecycle("PROBATION", _perf(rs), consecutive_failures=0, now=NOW)
    assert first["new_status"] == "PROBATION"  # metrics qualify, streak does not
    assert first["consecutive_failures"] == 1
    second = evaluate_lifecycle("PROBATION", _perf(rs), consecutive_failures=1, now=NOW)
    assert second["new_status"] == "SUSPENDED"
    assert second["requires_manual_reactivation"] is True


def test_archive_needs_lifetime_and_three_failures():
    rs = [-0.1] * 100
    decision = evaluate_lifecycle("PROBATION", _perf(rs), consecutive_failures=2, now=NOW)
    assert decision["new_status"] == "ARCHIVED"
    assert "archive_conditions_met" in decision["reasons"]


def test_terminal_is_untouched():
    decision = evaluate_lifecycle("SUSPENDED", _perf([1.0] * 60), now=NOW)
    assert decision["new_status"] == "SUSPENDED" and decision["status_changed"] is False
    assert decision["reasons"] == ["terminal_state_requires_manual_reactivation"]


def test_recovery_from_warning():
    decision = evaluate_lifecycle("WARNING", _perf([1.0] * 25), now=NOW)
    assert decision["new_status"] == "PAPER_ACTIVE" and decision["is_recovery"] is True
    assert "recovered_to_active" in decision["reasons"]


def test_win_rate_drop_forces_probation():
    rs = [1.0] * 10 + [-0.01] * 10  # win rate 0.5, healthy-ish window
    decision = evaluate_lifecycle(
        "PAPER_ACTIVE", _perf(rs, backtest_win_rate=0.9), now=NOW,
    )
    assert decision["new_status"] == "PROBATION"
    assert "live_win_rate_dropped_below_backtest" in decision["reasons"]


# --- run_lifecycle + pool application -----------------------------------------

def _spec_dict(strategy_id="S1"):
    return {
        "schema_version": "strategy_spec.v1", "strategy_id": strategy_id,
        "strategy_version": "1.0", "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"], "timeframe": "1d", "direction": "long",
        "entry_rules": {"operator": "AND",
                        "conditions": [{"feature": "close", "comparison": ">", "value": 0.0}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }


def _install_pool(root, *entries):
    pool.install_active_pool({"active_strategies": list(entries)}, root=root)


def _entry(strategy_id="S1", status="PAPER_ACTIVE", **extra):
    return {"strategy_id": strategy_id, "status": status, "champion_score": 0.5,
            "strategy_spec": _spec_dict(strategy_id), **extra}


def test_run_lifecycle_skips_terminal_and_unattributed():
    active = {"active_strategies": [_entry("S1"), _entry("S2", status="SUSPENDED")]}
    decisions = run_lifecycle(active, _outcomes([-0.1] * 30, strategy_id="S_OTHER"), now=NOW)
    assert [d["strategy_id"] for d in decisions] == ["S1"]  # terminal not even evaluated
    assert decisions[0]["new_status"] == "PAPER_ACTIVE"  # unattributed outcomes feed nothing


# --- lineage attribution (the mid-review P0: generations must not mix) ---------

def _lineage_outcomes(rs, *, candidate_id=None, generation_id=None, rule_hash=None,
                      strategy_id="S1"):
    rows = _outcomes(rs, strategy_id=strategy_id)
    for i, row in enumerate(rows):
        row["outcome_id"] = f"{candidate_id or generation_id or strategy_id}-{i}"
        if candidate_id:
            row["candidate_id"] = candidate_id
        if generation_id:
            row["strategy_generation_id"] = generation_id
        if rule_hash:
            row["strategy_rule_hash"] = rule_hash
    return rows


_GEN2 = dict(candidate_id="cand_gen2", generation_id="GEN-002", strategy_rule_hash="rule2")


def _only_decision(outcomes):
    active = {"active_strategies": [_entry("S1", **_GEN2)]}
    decisions = run_lifecycle(active, outcomes, now=NOW)
    assert len(decisions) == 1
    return decisions[0]


def test_a_replaced_strategy_does_not_inherit_its_predecessors_losses():
    """The bug: GEN-002's S1 is a different strategy that happens to reuse the display
    name. Grouping by strategy_id fed it GEN-001's 30 losses and demoted it on day one."""
    decision = _only_decision(_lineage_outcomes([-0.5] * 30, candidate_id="cand_gen1"))
    assert decision["new_status"] == "PAPER_ACTIVE"            # judged on its OWN record
    assert decision["status_changed"] is False


def test_a_strategy_is_still_judged_on_its_own_lineage():
    decision = _only_decision(_lineage_outcomes([-0.5] * 30, candidate_id="cand_gen2"))
    assert decision["new_status"] == "PROBATION"               # its own losses do count


def test_pre_lineage_outcomes_join_on_generation_and_rule_hash():
    # Outcomes written before candidate_id reached the trading path carry no
    # candidate_id; the entry still attributes them via the generation+rule pair.
    legacy = _lineage_outcomes([-0.5] * 30, generation_id="GEN-002", rule_hash="rule2")
    assert _only_decision(legacy)["new_status"] == "PROBATION"
    # ...but the SAME display name in another generation still stays out.
    other = _lineage_outcomes([-0.5] * 30, generation_id="GEN-001", rule_hash="rule1")
    assert _only_decision(other)["new_status"] == "PAPER_ACTIVE"


def test_imported_history_still_attaches_by_display_name():
    # Pre-lineage imports carry only a strategy_id. Refusing them would zero out the
    # lifecycle input of strategies still trading on imported history — the live pool
    # is exactly this shape today. Documented as the one imprecise join.
    legacy = _outcomes([-0.5] * 30, strategy_id="S1")          # no lineage fields at all
    assert _only_decision(legacy)["new_status"] == "PROBATION"


def test_attribution_key_precedence():
    from runtime.mvp_runtime.crypto.lifecycle import outcome_attribution_key

    assert outcome_attribution_key({"candidate_id": "c1", "strategy_id": "S1"}) == "cand:c1"
    assert outcome_attribution_key(
        {"strategy_generation_id": "GEN-001", "strategy_rule_hash": "r", "strategy_id": "S1"}
    ) == "gen:GEN-001:r"
    assert outcome_attribution_key({"strategy_id": "S1"}) == "sid:S1"
    assert outcome_attribution_key({}) == ""                   # unattributed feeds nothing


def test_update_statuses_applies_and_guards(tmp_path):
    _install_pool(tmp_path, _entry("S1"), _entry("S2"))
    decisions = run_lifecycle(pool.load_active_pool(tmp_path), _outcomes([-0.1] * 30), now=NOW)
    changed = pool.update_statuses(decisions, root=tmp_path)
    assert changed == 1
    updated = {e["strategy_id"]: e for e in pool.load_active_pool(tmp_path)["active_strategies"]}
    assert updated["S1"]["status"] == "PROBATION"
    assert updated["S1"]["lifecycle_consecutive_failures"] == 1
    assert updated["S2"]["status"] == "PAPER_ACTIVE"
    assert updated["S1"]["strategy_spec"] == _spec_dict("S1")  # spec untouched


def test_update_statuses_terminal_immutable(tmp_path):
    _install_pool(tmp_path, _entry("S1", status="SUSPENDED"))
    with pytest.raises(ToolError) as exc:
        pool.update_statuses([{"strategy_id": "S1", "new_status": "PAPER_ACTIVE",
                               "consecutive_failures": 0}], root=tmp_path)
    assert exc.value.reason_code == "LIFECYCLE_TERMINAL_IMMUTABLE"


def test_update_statuses_unknown_strategy_refused(tmp_path):
    _install_pool(tmp_path, _entry("S1"))
    with pytest.raises(ToolError) as exc:
        pool.update_statuses([{"strategy_id": "S_NOPE", "new_status": "WARNING",
                               "consecutive_failures": 1}], root=tmp_path)
    assert exc.value.reason_code == "LIFECYCLE_UNKNOWN_STRATEGY"


# --- cycle wiring ---------------------------------------------------------------

def _seed_outcomes(root, rs, strategy_id="S1"):
    state = paper.state_dir(root)
    state.mkdir(parents=True, exist_ok=True)
    import json as _json

    with open(state / "paper_outcomes.jsonl", "a", encoding="utf-8") as handle:
        for o in _outcomes(rs, strategy_id=strategy_id):
            handle.write(_json.dumps(o) + "\n")


def _run_cycle(root, store):
    from tests.test_mvp_runtime_crypto_cycle import FakeExchangeCollector

    return run_crypto_cycle(collector=FakeExchangeCollector(), store=store, now=NOW,
                            root=root, control_store=ControlStore(root))


def test_dry_run_records_decisions_but_persists_nothing(tmp_path):
    _install_pool(tmp_path, _entry("S1"))
    _seed_outcomes(tmp_path, [-0.1] * 30)
    record = _run_cycle(tmp_path, DryRunPaperStore())
    changed = [d for d in record["lifecycle_decisions"] if d["status_changed"]]
    assert changed and changed[0]["new_status"] == "PROBATION"
    assert record["lifecycle_applied"] == 0
    assert "LIFECYCLE_TRANSITION" in record["reason_codes"]
    assert pool.load_active_pool(tmp_path)["active_strategies"][0]["status"] == "PAPER_ACTIVE"


def test_real_store_applies_demotion_and_it_sticks(tmp_path):
    _install_pool(tmp_path, _entry("S1"))
    _seed_outcomes(tmp_path, [-0.1] * 30)
    record = _run_cycle(tmp_path, RealPaperStore(root=tmp_path, authorization=_AUTH))
    assert record["lifecycle_applied"] == 1
    assert "lifecycle: S1 PAPER_ACTIVE -> PROBATION" in record["report_text"]
    assert pool.load_active_pool(tmp_path)["active_strategies"][0]["status"] == "PROBATION"


def test_suspended_strategy_stops_routing_next_cycle(tmp_path):
    _install_pool(tmp_path, _entry("S1", status="PROBATION", lifecycle_consecutive_failures=1))
    _seed_outcomes(tmp_path, [-0.1] * 50)
    record = _run_cycle(tmp_path, RealPaperStore(root=tmp_path, authorization=_AUTH))
    assert pool.load_active_pool(tmp_path)["active_strategies"][0]["status"] == "SUSPENDED"
    assert "(manual reactivation required)" in record["report_text"]
    # Next cycle: the suspended strategy no longer occupies a routing slot.
    record2 = _run_cycle(tmp_path, RealPaperStore(root=tmp_path, authorization=_AUTH))
    assert record2["route_status"] == "NO_ENTRY"
