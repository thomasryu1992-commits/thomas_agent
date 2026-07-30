"""A SUSPENDED entry says why it stopped trading, instead of leaving it to the ledger.

Measured on the live pool 2026-07-30: five 1d entries read `SUSPENDED` with
`lifecycle_consecutive_failures: 0` and nothing else. Zero means two opposite things there.
A performance suspension always carries a streak of at least `suspend_consecutive` — so zero
cannot be one — while `operator_retirement_decision` carries the count **forward untouched**,
which for a strategy that never failed is zero. The five turned out to be duplicate rules an
operator removed on purpose (`reasons: ["operator_retired"]`, plus a retirement_reason naming
them as one lineage padded with always-true conditions), and the only place that said so was
`control_events.jsonl`.

Under test: the transition records its reasons and its author on the entry, the two demotion
kinds are distinguishable without opening the ledger, the fields describe the transition that
produced the CURRENT status, and an entry written before the fields existed reports unknown
rather than "no reason".
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import pool
from runtime.mvp_runtime.crypto.dashboard import build_status, render_status_text
from runtime.mvp_runtime.crypto.lifecycle import operator_retirement_decision

NOW = "2026-07-30T08:00:00Z"


def _entry(strategy_id, *, status="PAPER_ACTIVE", failures=0, **extra):
    return {
        "strategy_id": strategy_id,
        "status": status,
        "stage": "paper",
        "champion_score": 0.8,
        "strategy_rule_hash": f"hash-{strategy_id}",
        "generation_id": "GEN-001",
        "lifecycle_consecutive_failures": failures,
        "strategy_spec": {
            "schema_version": "strategy_spec.v1",
            "strategy_id": strategy_id,
            "strategy_version": "1.0",
            "strategy_family": "trend_pullback",
            "symbol_scope": ["BTCUSDT"],
            "timeframe": "4h",
            "direction": "long",
            "entry_rules": {"operator": "AND",
                            "conditions": [{"feature": "close", "comparison": ">", "value": 0.0}]},
            "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0,
                           "max_holding_bars": 10},
            "risk_constraints": {"max_risk_per_trade_R": 1.0},
        },
        **extra,
    }


def _install(root, *entries):
    pool.install_active_pool({"active_strategies": list(entries)}, root=root)


def _read(root, strategy_id):
    entries = pool.load_active_pool(root)["active_strategies"]
    return next(e for e in entries if e["strategy_id"] == strategy_id)


def _metric_suspension(strategy_id):
    """The shape `evaluate_lifecycle` emits when the metrics condemn a strategy."""
    return {
        "strategy_id": strategy_id,
        "previous_status": "PAPER_ACTIVE",
        "new_status": "SUSPENDED",
        "status_changed": True,
        "consecutive_failures": 2,
        "reasons": ["suspend_conditions_met"],
        "created_at_utc": NOW,
        "strategy_lifecycle_decision_id": "strategy_lifecycle_decision_metric",
    }


def test_an_operator_retirement_says_so_on_the_entry(tmp_path):
    _install(tmp_path, _entry("S001"))
    decision = operator_retirement_decision(
        _read(tmp_path, "S001"), reason="duplicate rules", retired_by="Thomas", now=NOW)
    pool.update_statuses([decision], root=tmp_path)

    entry = _read(tmp_path, "S001")
    assert entry["status"] == "SUSPENDED"
    assert entry["lifecycle_reasons"] == ["operator_retired"]
    assert entry["lifecycle_retired_by"] == "Thomas"


def test_a_metric_suspension_says_something_different(tmp_path):
    _install(tmp_path, _entry("S001"))
    pool.update_statuses([_metric_suspension("S001")], root=tmp_path)

    entry = _read(tmp_path, "S001")
    assert entry["lifecycle_reasons"] == ["suspend_conditions_met"]
    assert "lifecycle_retired_by" not in entry, "nobody retired it; there is no author to name"


def test_the_two_kinds_are_told_apart_without_the_ledger(tmp_path):
    """The whole point. Both land on SUSPENDED, and a retirement carries the failure count
    forward untouched — so a strategy that never failed is retired at zero and reads exactly
    like one the metrics condemned, unless the entry says which."""
    _install(tmp_path, _entry("S_RETIRED"), _entry("S_DECAYED"))
    pool.update_statuses([
        operator_retirement_decision(
            _read(tmp_path, "S_RETIRED"), reason="duplicate rules", retired_by="Thomas", now=NOW),
    ], root=tmp_path)
    pool.update_statuses([_metric_suspension("S_DECAYED")], root=tmp_path)

    retired, decayed = _read(tmp_path, "S_RETIRED"), _read(tmp_path, "S_DECAYED")
    assert retired["status"] == decayed["status"] == "SUSPENDED"
    assert retired["lifecycle_consecutive_failures"] == 0
    assert retired["lifecycle_reasons"] != decayed["lifecycle_reasons"]


def test_the_reasons_describe_the_transition_that_produced_the_current_status(tmp_path):
    """Written only on a CHANGE, alongside the timestamp and decision id — so an entry whose
    status did not move keeps the reasons for the status it is actually in."""
    _install(tmp_path, _entry("S001"))
    no_change = {**_metric_suspension("S001"), "new_status": "PAPER_ACTIVE",
                 "status_changed": False, "reasons": ["recovered_to_active"],
                 "consecutive_failures": 0}
    pool.update_statuses([no_change], root=tmp_path)
    assert "lifecycle_reasons" not in _read(tmp_path, "S001")

    pool.update_statuses([_metric_suspension("S001")], root=tmp_path)
    assert _read(tmp_path, "S001")["lifecycle_reasons"] == ["suspend_conditions_met"]


def test_a_decision_with_no_reasons_records_an_empty_list_not_a_missing_field(tmp_path):
    """Empty is an answer ("the transition named none"); absent means an older runtime wrote
    it. The board renders those differently, so they must not collapse here."""
    _install(tmp_path, _entry("S001"))
    pool.update_statuses([{**_metric_suspension("S001"), "reasons": None}], root=tmp_path)
    assert _read(tmp_path, "S001")["lifecycle_reasons"] == []


def test_the_board_counts_demotions_by_reason(tmp_path):
    _install(tmp_path, _entry("S_RETIRED"), _entry("S_DECAYED"), _entry("S_LIVE"))
    pool.update_statuses([
        operator_retirement_decision(
            _read(tmp_path, "S_RETIRED"), reason="duplicate rules", retired_by="Thomas", now=NOW),
    ], root=tmp_path)
    pool.update_statuses([_metric_suspension("S_DECAYED")], root=tmp_path)

    status = build_status(tmp_path, now=NOW)
    assert status["demotions_by_reason"] == {"operator_retired": 1, "suspend_conditions_met": 1}
    assert "강등 사유" in render_status_text(status)


def test_an_entry_from_before_the_field_existed_reports_unrecorded(tmp_path):
    """The five on the live pool are in exactly this state. "unrecorded" is honest; counting
    them under a reason they never carried would be the board inventing one."""
    _install(tmp_path, _entry("S_OLD", status="SUSPENDED"))
    status = build_status(tmp_path, now=NOW)
    assert status["demotions_by_reason"] == {"unrecorded": 1}


def test_a_still_trading_entry_is_not_counted_as_a_demotion(tmp_path):
    """WARNING and PROBATION are degraded but still occupy a routing slot, so counting them
    would answer a question nobody asked — and hide the ones that actually stopped."""
    _install(tmp_path, _entry("S_WARN", status="WARNING"), _entry("S_PROB", status="PROBATION"),
             _entry("S_LIVE"))
    status = build_status(tmp_path, now=NOW)
    assert status["demotions_by_reason"] == {}


def test_the_transition_still_cannot_touch_anything_else(tmp_path):
    """The narrowness this mutation is trusted for. Adding provenance must not widen it."""
    _install(tmp_path, _entry("S001"))
    before = _read(tmp_path, "S001")
    pool.update_statuses([_metric_suspension("S001")], root=tmp_path)
    after = _read(tmp_path, "S001")

    for field in ("strategy_spec", "strategy_rule_hash", "champion_score", "generation_id"):
        assert after[field] == before[field]
