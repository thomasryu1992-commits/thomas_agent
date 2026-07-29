"""Strategy-retirement tests — the narrow pool verb the promotion door cannot express.

Under test: the content hash binds slot AND lineage AND rules; the ask refuses what the
execution would refuse (unknown id, already-terminal); verification fails closed on
pending/expired/content-mismatch and on a PROMOTION approval; and — the property this
verb exists for — applying a retirement changes ``status`` on the named entries only,
leaving membership, other entries' statuses, and every spec untouched.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.crypto import pool
from runtime.mvp_runtime.crypto.lifecycle import operator_retirement_decision
from runtime.mvp_runtime.crypto.retirement import (
    RETIREMENT_ACTION_TYPE,
    apply_retirement,
    request_retirement,
    resolve_pool_entries,
    retirement_content_sha256,
    verify_retirement_approval,
)
from runtime.mvp_runtime.crypto.strategy import StrategySpec
from runtime.mvp_runtime.errors import ApprovalBlocked, MvpRuntimeError, ToolError

from tests._helpers import requires_local_core

NOW = timeutil.utc_now_iso()


def _spec_dict(strategy_id="S1", **overrides):
    base = {
        "schema_version": "strategy_spec.v1",
        "strategy_id": strategy_id, "strategy_version": "1.0", "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"], "timeframe": "1d", "direction": "long",
        "entry_rules": {"operator": "AND",
                        "conditions": [{"feature": "close", "comparison": ">", "value": 0.0}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    base.update(overrides)
    return base


def _entry(strategy_id, *, status="PAPER_ACTIVE", failures=0, family="breakout"):
    spec = StrategySpec.from_dict(_spec_dict(strategy_id, strategy_family=family))
    return {
        "strategy_id": strategy_id,
        "candidate_id": f"cand_{strategy_id}",
        "strategy_rule_hash": spec.strategy_rule_hash,
        "generation_id": "GEN-001",
        "status": status,
        "stage": "paper",
        "champion_score": 0.5,
        "strategy_spec": spec.to_dict(),
        "lifecycle_consecutive_failures": failures,
    }


def _seed_pool(tmp_path, *entries):
    pool.install_active_pool({
        "pool_version": "active_strategy_pool.v1",
        "stage": "paper",
        "active_strategies": list(entries),
        "updated_by": "test",
        "updated_at": NOW,
    }, root=tmp_path)


def _statuses(tmp_path):
    return {e["strategy_id"]: e["status"]
            for e in pool.load_active_pool(tmp_path)["active_strategies"]}


# --- content hash -------------------------------------------------------------

def test_content_hash_binds_slot_lineage_and_rules():
    base_entry = {"strategy_id": "S1", "candidate_id": "cand_a", "strategy_rule_hash": "aaa"}
    base = retirement_content_sha256([base_entry])
    assert retirement_content_sha256([dict(base_entry)]) == base
    # The display slot, the lineage that holds it, and the rules it runs each move the hash:
    # binding fewer of them would let an approval retire something it never named.
    assert retirement_content_sha256([{**base_entry, "strategy_id": "S2"}]) != base
    assert retirement_content_sha256([{**base_entry, "candidate_id": "cand_b"}]) != base
    assert retirement_content_sha256([{**base_entry, "strategy_rule_hash": "bbb"}]) != base


def test_content_hash_is_order_insensitive():
    a = {"strategy_id": "S1", "candidate_id": "c1", "strategy_rule_hash": "aaa"}
    b = {"strategy_id": "S2", "candidate_id": "c2", "strategy_rule_hash": "bbb"}
    assert retirement_content_sha256([a, b]) == retirement_content_sha256([b, a])


# --- selection: refuse at the ask what the install would refuse ---------------

def test_resolve_refuses_unknown_strategy(tmp_path):
    _seed_pool(tmp_path, _entry("S1"))
    with pytest.raises(ApprovalBlocked) as exc:
        resolve_pool_entries(["S_NOPE"], tmp_path)
    assert exc.value.reason_code == "LIFECYCLE_UNKNOWN_STRATEGY"


def test_resolve_refuses_already_terminal(tmp_path):
    _seed_pool(tmp_path, _entry("S1", status="SUSPENDED"))
    with pytest.raises(ApprovalBlocked) as exc:
        resolve_pool_entries(["S1"], tmp_path)
    assert exc.value.reason_code == "LIFECYCLE_TERMINAL_IMMUTABLE"


def test_resolve_refuses_empty_and_duplicate_selectors(tmp_path):
    _seed_pool(tmp_path, _entry("S1"))
    with pytest.raises(ApprovalBlocked) as exc:
        resolve_pool_entries([], tmp_path)
    assert exc.value.reason_code == "RETIREMENT_EMPTY"
    with pytest.raises(ApprovalBlocked) as exc:
        resolve_pool_entries(["S1", "S1"], tmp_path)
    assert exc.value.reason_code == "RETIREMENT_DUPLICATE_SELECTOR"


# --- the ask ------------------------------------------------------------------

@requires_local_core
def test_request_builds_decision_and_pending_approval(tmp_path):
    _seed_pool(tmp_path, _entry("S1"), _entry("S2"))
    prepared = request_retirement(["S1"], reason="duplicate of S2", now=NOW, pool_root=tmp_path)
    payload = prepared["permission_decision"]["fingerprint_payload"]
    request = prepared["approval_request"]
    assert payload["permission_scope"] == "RUNTIME_GOVERNANCE"
    assert payload["action_type"] == RETIREMENT_ACTION_TYPE
    assert payload["content_sha256"] == prepared["content_sha256"]
    assert request["status"] == "PENDING"
    assert request["approved_action_snapshot"]["content_sha256"] == prepared["content_sha256"]


@requires_local_core
def test_request_performs_nothing(tmp_path):
    _seed_pool(tmp_path, _entry("S1"))
    request_retirement(["S1"], reason="duplicate", now=NOW, pool_root=tmp_path)
    assert _statuses(tmp_path) == {"S1": "PAPER_ACTIVE"}


# --- verification: fail closed ------------------------------------------------

def _fake_approval(tmp_path, *, status="APPROVED", content=None,
                   action=RETIREMENT_ACTION_TYPE, expires="2999-01-01T00:00:00Z"):
    if content is None:
        content = retirement_content_sha256(resolve_pool_entries(["S1"], tmp_path))
    return {
        "approval_id": "approval_test",
        "status": status,
        "validity": {"issued_at": NOW, "expires_at": expires},
        "approved_action_snapshot": {"action_type": action, "content_sha256": content},
    }


def test_verify_accepts_matching_approved(tmp_path):
    _seed_pool(tmp_path, _entry("S1"))
    verified = verify_retirement_approval(
        _fake_approval(tmp_path), strategy_ids=["S1"], root=tmp_path, now=NOW)
    assert verified["approval_id"] == "approval_test"


@pytest.mark.parametrize("mutation,code", [
    (dict(status="PENDING"), "APPROVAL_NOT_APPROVED"),
    (dict(status="REJECTED"), "APPROVAL_NOT_APPROVED"),
    (dict(expires="2020-01-01T00:00:00Z"), "APPROVAL_EXPIRED"),
    (dict(content="sha256:wrong"), "APPROVAL_CONTENT_MISMATCH"),
    # A promotion approval must never execute here: the two doors have different effects
    # on the pool, and the action type is what keeps one from standing in for the other.
    (dict(action="crypto.strategy_pool.promotion"), "APPROVAL_WRONG_ACTION"),
])
def test_verify_fails_closed(tmp_path, mutation, code):
    _seed_pool(tmp_path, _entry("S1"))
    with pytest.raises(ApprovalBlocked) as exc:
        verify_retirement_approval(
            _fake_approval(tmp_path, **mutation), strategy_ids=["S1"], root=tmp_path, now=NOW)
    assert exc.value.reason_code == code


def test_verify_missing_approval(tmp_path):
    _seed_pool(tmp_path, _entry("S1"))
    with pytest.raises(ApprovalBlocked) as exc:
        verify_retirement_approval(None, strategy_ids=["S1"], root=tmp_path, now=NOW)
    assert exc.value.reason_code == "APPROVAL_MISSING"


def test_verify_refuses_when_the_slot_changed_hands(tmp_path):
    """An approval names a lineage, not just a display name."""
    _seed_pool(tmp_path, _entry("S1"))
    approval = _fake_approval(tmp_path)
    # Same display id, different strategy behind it — exactly the substitution the
    # lineage term in the hash exists to catch.
    _seed_pool(tmp_path, _entry("S1", family="trend_pullback"))
    with pytest.raises(ApprovalBlocked) as exc:
        verify_retirement_approval(approval, strategy_ids=["S1"], root=tmp_path, now=NOW)
    assert exc.value.reason_code == "APPROVAL_CONTENT_MISMATCH"


# --- the decision record ------------------------------------------------------

def test_decision_is_operator_provenanced_and_preserves_the_failure_count():
    decision = operator_retirement_decision(
        _entry("S1", failures=2), reason="duplicate", retired_by="Thomas", now=NOW)
    assert decision["new_status"] == "SUSPENDED"
    assert decision["reasons"] == ["operator_retired"]
    assert decision["retirement_reason"] == "duplicate"
    assert decision["retired_by"] == "Thomas"
    # Retiring a strategy says nothing about its record, so the streak carries over.
    assert decision["consecutive_failures"] == 2
    assert decision["requires_manual_reactivation"] is True


def test_decision_refuses_terminal_and_blank_reason():
    with pytest.raises(ToolError) as exc:
        operator_retirement_decision(
            _entry("S1", status="SUSPENDED"), reason="x", retired_by="Thomas", now=NOW)
    assert exc.value.reason_code == "LIFECYCLE_TERMINAL_IMMUTABLE"
    with pytest.raises(ToolError) as exc:
        operator_retirement_decision(_entry("S1"), reason="   ", retired_by="Thomas", now=NOW)
    assert exc.value.reason_code == "RETIREMENT_REASON_REQUIRED"


# --- the effect: narrow by construction ---------------------------------------

def test_apply_suspends_only_the_named_entries(tmp_path):
    _seed_pool(tmp_path, _entry("S1"), _entry("S2"), _entry("S3"))
    summary = apply_retirement(["S2"], reason="duplicate of S1", retired_by="Thomas",
                               root=tmp_path, now=NOW)
    assert summary["entries_changed"] == 1
    assert _statuses(tmp_path) == {"S1": "PAPER_ACTIVE", "S2": "SUSPENDED", "S3": "PAPER_ACTIVE"}


def test_apply_never_changes_membership_or_specs(tmp_path):
    _seed_pool(tmp_path, _entry("S1"), _entry("S2"))
    before = {e["strategy_id"]: e for e in pool.load_active_pool(tmp_path)["active_strategies"]}
    apply_retirement(["S1"], reason="duplicate", retired_by="Thomas", root=tmp_path, now=NOW)
    after = {e["strategy_id"]: e for e in pool.load_active_pool(tmp_path)["active_strategies"]}
    assert set(after) == set(before), "membership must not change"
    for sid in before:
        for field in ("strategy_spec", "strategy_rule_hash", "candidate_id",
                      "generation_id", "champion_score"):
            assert after[sid][field] == before[sid][field], f"{sid}.{field} moved"


def test_apply_leaves_terminal_entries_alone(tmp_path):
    """The regression this verb exists for.

    Retiring one strategy through the PROMOTION door means a replace-mode promotion,
    which rebuilds every entry with a hardcoded PAPER_ACTIVE and therefore reactivates
    strategies the lifecycle terminally suspended. This door must not.
    """
    _seed_pool(tmp_path, _entry("S1"), _entry("S2"), _entry("S_DEAD", status="SUSPENDED"))
    apply_retirement(["S2"], reason="duplicate of S1", retired_by="Thomas", root=tmp_path, now=NOW)
    assert _statuses(tmp_path) == {
        "S1": "PAPER_ACTIVE", "S2": "SUSPENDED", "S_DEAD": "SUSPENDED",
    }


def test_apply_is_all_or_nothing(tmp_path):
    """One bad id refuses the batch before anything is written."""
    _seed_pool(tmp_path, _entry("S1"), _entry("S2"))
    with pytest.raises(MvpRuntimeError):
        apply_retirement(["S1", "S_NOPE"], reason="duplicate", retired_by="Thomas",
                         root=tmp_path, now=NOW)
    assert _statuses(tmp_path) == {"S1": "PAPER_ACTIVE", "S2": "PAPER_ACTIVE"}


def test_apply_records_the_operator_as_the_pool_updater(tmp_path):
    _seed_pool(tmp_path, _entry("S1"))
    apply_retirement(["S1"], reason="duplicate", retired_by="Thomas", root=tmp_path, now=NOW)
    assert pool.load_active_pool(tmp_path)["updated_by"] == "Thomas"
