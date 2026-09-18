"""PR3b-2 — a lifecycle decision moves only the lineage it judged (Thomas decision 36).

The lifecycle judges the pool the cycle read; the pool write happens later, under the lock, on the
pool as it is then. A promotion in between can put another lineage under the same display id, and
the write applied decisions by display id: A's demotion could land on B. Each decision now names
the lineage it judged (candidate, generation, rule hash). The write skips a decision whose display
id names another lineage by then, or that names none, reports it, and applies the rest: a
demotion held back for one stale decision would be the less safe outcome.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import pool
from runtime.mvp_runtime.crypto.candidate_identity import LINEAGE_FIELDS, lineage_of
from runtime.mvp_runtime.crypto.lifecycle import operator_retirement_decision, run_lifecycle
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-09-18T12:00:00Z"


def _spec(strategy_id):
    return {
        "schema_version": "strategy_spec.v1", "strategy_id": strategy_id, "strategy_version": "1.0",
        "strategy_family": "breakout", "symbol_scope": ["BTCUSDT"], "timeframe": "4h", "direction": "long",
        "entry_rules": {"operator": "AND", "conditions": [{"feature": "close", "comparison": ">", "value": 0.0}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }


def _entry(strategy_id, cand, *, status="PAPER_ACTIVE", generation="GEN-001"):
    return {"strategy_id": strategy_id, "candidate_id": cand, "generation_id": generation,
            "strategy_rule_hash": f"hash-{cand}", "status": status, "champion_score": 0.5,
            "strategy_spec": _spec(strategy_id)}


def _install(root, *entries):
    pool.install_active_pool({"active_strategies": list(entries)}, root=root)


def _entries(root):
    return {e["strategy_id"]: e for e in pool.load_active_pool(root)["active_strategies"]}


def _decision(entry, new_status="WARNING", failures=1):
    """A lifecycle decision about ``entry`` in `evaluate_lifecycle`'s shape, naming its lineage as
    `run_lifecycle` does."""
    terminal = new_status in ("SUSPENDED", "ARCHIVED")
    return {"strategy_id": entry["strategy_id"], "previous_status": entry["status"], "new_status": new_status,
            "status_changed": True, "is_escalation": True, "is_recovery": False,
            "consecutive_failures": failures, "new_entry_blocked": terminal,
            "requires_manual_reactivation": terminal, "reasons": ["metric_warning"],
            "created_at_utc": NOW, "strategy_lifecycle_decision_id": f"d-{entry['strategy_id']}",
            **lineage_of(entry)}


def _losses(cand, n=30):
    return [{"outcome_closed": True, "result_R": -0.1, "candidate_id": cand, "strategy_id": "ignored",
             "created_at_utc": f"2026-09-{1 + i % 17:02d}T00:00:00Z"} for i in range(n)]


def test_a_lifecycle_decision_names_the_lineage_it_judged():
    active = {"active_strategies": [_entry("S1", "cand_A")]}
    [decision] = run_lifecycle(active, _losses("cand_A"), now=NOW)
    assert {f: decision[f] for f in LINEAGE_FIELDS} == {
        "candidate_id": "cand_A", "strategy_generation_id": "GEN-001", "strategy_rule_hash": "hash-cand_A"}


def test_an_operator_retirement_names_all_three():
    decision = operator_retirement_decision(_entry("S1", "cand_A"), reason="duplicate", retired_by="Thomas", now=NOW)
    assert {f: decision[f] for f in LINEAGE_FIELDS} == {
        "candidate_id": "cand_A", "strategy_generation_id": "GEN-001", "strategy_rule_hash": "hash-cand_A"}


def test_a_decision_about_a_lineage_the_id_no_longer_names_is_skipped_and_the_rest_applied(tmp_path):
    judged_a = _entry("S1", "cand_A")
    judged_c = _entry("S2", "cand_C")
    # Between the lifecycle's read and the write, a promotion put lineage B under "S1".
    _install(tmp_path, _entry("S1", "cand_B"), judged_c)
    result = pool.apply_status_decisions(
        [_decision(judged_a, new_status="SUSPENDED", failures=3), _decision(judged_c)], root=tmp_path)
    assert result["changed"] == 1
    [stale] = result["stale"]
    assert stale["strategy_id"] == "S1" and stale["new_status"] == "SUSPENDED"
    assert "cand_A" in stale["problem"] and "cand_B" in stale["problem"]
    entries = _entries(tmp_path)
    b = entries["S1"]
    assert b["status"] == "PAPER_ACTIVE" and b["candidate_id"] == "cand_B"
    assert "lifecycle_consecutive_failures" not in b and "lifecycle_reasons" not in b
    assert entries["S2"]["status"] == "WARNING"


@pytest.mark.parametrize("field,value", [
    ("candidate_id", "cand_other"), ("strategy_generation_id", "GEN-002"), ("strategy_rule_hash", "hash-other"),
])
def test_each_lineage_field_must_match(tmp_path, field, value):
    entry = _entry("S1", "cand_A")
    _install(tmp_path, entry)
    result = pool.apply_status_decisions([{**_decision(entry), field: value}], root=tmp_path)
    assert result["changed"] == 0 and [s["strategy_id"] for s in result["stale"]] == ["S1"]
    assert _entries(tmp_path)["S1"]["status"] == "PAPER_ACTIVE"


def test_a_decision_that_names_no_lineage_is_not_applied(tmp_path):
    """Every producer names the lineage it judged. One that names none cannot be checked, and is
    held back rather than trusted — the other decisions still apply."""
    entry, other = _entry("S1", "cand_A"), _entry("S2", "cand_C")
    _install(tmp_path, entry, other)
    anonymous = {k: v for k, v in _decision(entry).items() if k not in LINEAGE_FIELDS}
    result = pool.apply_status_decisions([anonymous, _decision(other)], root=tmp_path)
    assert result["changed"] == 1
    [stale] = result["stale"]
    assert stale["problem"] == "the decision does not name the lineage it judged"
    assert _entries(tmp_path)["S1"]["status"] == "PAPER_ACTIVE"


def test_a_stale_decision_about_a_now_terminal_entry_does_not_hold_back_the_batch(tmp_path):
    """The id now names another lineage that is terminal: the decision says nothing about it, so it
    is skipped, not refused as an attempt to move a terminal entry — which would drop the batch."""
    _install(tmp_path, _entry("S1", "cand_B", status="SUSPENDED"), _entry("S2", "cand_C"))
    result = pool.apply_status_decisions(
        [_decision(_entry("S1", "cand_A")), _decision(_entry("S2", "cand_C"))], root=tmp_path)
    assert result["changed"] == 1 and [s["strategy_id"] for s in result["stale"]] == ["S1"]
    assert _entries(tmp_path)["S1"]["status"] == "SUSPENDED"


def test_a_decision_about_the_terminal_entry_itself_is_still_refused(tmp_path):
    entry = _entry("S1", "cand_A", status="SUSPENDED")
    _install(tmp_path, entry)
    with pytest.raises(ToolError) as refused:
        pool.apply_status_decisions([_decision(entry, new_status="PAPER_ACTIVE")], root=tmp_path)
    assert refused.value.reason_code == "LIFECYCLE_TERMINAL_IMMUTABLE"


def test_update_statuses_still_counts_what_changed(tmp_path):
    judged_a, judged_c = _entry("S1", "cand_A"), _entry("S2", "cand_C")
    _install(tmp_path, _entry("S1", "cand_B"), judged_c)
    assert pool.update_statuses([_decision(judged_a), _decision(judged_c)], root=tmp_path) == 1


def test_the_cycle_reports_a_stale_decision_and_applies_the_rest(tmp_path, monkeypatch):
    from runtime.mvp_runtime.control import ControlStore
    from runtime.mvp_runtime.crypto import cycle as cycle_module
    from runtime.mvp_runtime.crypto.paper import RealPaperStore
    from tests.test_mvp_runtime_crypto_cycle import _AUTH, FakeExchangeCollector

    _install(tmp_path, _entry("S1", "cand_B"), _entry("S2", "cand_C"))
    # The lifecycle judged a pool in which "S1" was still lineage A.
    monkeypatch.setattr(cycle_module, "run_lifecycle", lambda active_pool, outcomes, now: [
        _decision(_entry("S1", "cand_A"), new_status="SUSPENDED", failures=3),
        _decision(_entry("S2", "cand_C"))])
    record = cycle_module.run_crypto_cycle(
        collector=FakeExchangeCollector(), store=RealPaperStore(root=tmp_path, authorization=_AUTH),
        now="2026-07-22T12:00:00Z", root=tmp_path, control_store=ControlStore(tmp_path), paper_outcomes=[])
    assert pool.LIFECYCLE_DECISION_STALE in record["reason_codes"]
    assert [s["strategy_id"] for s in record["lifecycle_stale"]] == ["S1"]
    assert record["lifecycle_applied"] == 1
    entries = _entries(tmp_path)
    assert entries["S1"]["status"] == "PAPER_ACTIVE" and entries["S2"]["status"] == "WARNING"
    lines = {line.split()[1]: line for line in record["report_text"].splitlines() if line.startswith("lifecycle: ")}
    assert lines["S1"].endswith("(not applied: the pool now holds another lineage under this id)")
    assert "not applied" not in lines["S2"]


def test_a_retirement_does_not_retire_a_lineage_it_did_not_name(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto import retirement as retirement_mod

    _install(tmp_path, _entry("S1", "cand_B"), _entry("S2", "cand_C"))
    # The operator named "S1" when it was lineage A; the pool holds B there by the write.
    monkeypatch.setattr(retirement_mod, "resolve_pool_entries",
                        lambda strategy_ids, root=None: [_entry("S1", "cand_A"), _entry("S2", "cand_C")])
    summary = retirement_mod.apply_retirement(["S1", "S2"], reason="duplicates", retired_by="Thomas",
                                              root=tmp_path, now=NOW)
    assert summary["entries_changed"] == 1
    assert [s["strategy_id"] for s in summary["entries_skipped"]] == ["S1"]
    entries = _entries(tmp_path)
    assert entries["S1"]["status"] == "PAPER_ACTIVE" and entries["S2"]["status"] == "SUSPENDED"


def test_the_retirement_script_names_what_it_did_not_retire(monkeypatch, capsys):
    import scripts.retire_strategies as script

    monkeypatch.setattr(script, "assert_not_foreign_root_run", lambda: None)
    monkeypatch.setattr(script, "run_retirement", lambda **kw: {
        "entries_changed": 1, "strategy_ids": ["S1", "S2"], "previous_statuses": ["PAPER_ACTIVE", "PAPER_ACTIVE"],
        "pool_size": 2, "approval_verified": True, "approval_id": "appr_1",
        "entries_skipped": [{"strategy_id": "S1", "new_status": "SUSPENDED",
                             "problem": "judged cand_A, the pool now holds cand_B"}]})
    assert script.main(["--strategy-ids", "S1,S2", "--reason", "duplicates", "--retired-by", "Thomas",
                        "--approval-id", "appr_1", "--confirm"]) == script.EXIT_OK
    out = capsys.readouterr().out
    assert "RETIRED 1 entry" in out
    assert "NOT RETIRED: S1 — judged cand_A, the pool now holds cand_B" in out
