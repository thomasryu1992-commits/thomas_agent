"""C8b promotion-approval tests — the ask binds the exact promotion; verify never spends.

Under test: the content hash changes on any material change (ids, rules, add-vs-
replace); the R9 request builds a schema-valid RUNTIME_GOVERNANCE decision + PENDING
approval; verification fails closed on pending/expired/wrong-action/content-mismatch
and on a candidate whose rules changed after approval; the operator door requires an
approval or the explicit escape; and the robustness scorer ranks candidates with the
source's veto semantics."""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import pool
from runtime.mvp_runtime.crypto.factory import run_factory
from runtime.mvp_runtime.crypto.promotion import (
    promotion_content_sha256,
    request_promotion,
    verify_promotion_approval,
)
from runtime.mvp_runtime.crypto.robustness import (
    CRITICAL_TRADES_PER_PARAMETER,
    FRAGILE,
    score_robustness,
)
from runtime.mvp_runtime.crypto.strategy import StrategySpec
from runtime.mvp_runtime.errors import ApprovalBlocked, MvpRuntimeError
from runtime.mvp_runtime import timeutil

from scripts.promote_strategy_candidates import run_promotion
from tests._helpers import requires_local_core

NOW = timeutil.utc_now_iso()


def _spec_dict(**overrides):
    base = {
        "schema_version": "strategy_spec.v1",
        "strategy_id": "S1", "strategy_version": "1.0", "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"], "timeframe": "1d", "direction": "long",
        "entry_rules": {"operator": "AND",
                        "conditions": [{"feature": "close", "comparison": ">", "value": 0.0}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    base.update(overrides)
    return base


def _seed_candidates(tmp_path, *specs, generation_id="GEN-001"):
    records = []
    for spec_dict in specs:
        spec = StrategySpec.from_dict(spec_dict)
        records.append({
            "strategy_id": spec.strategy_id,
            "strategy_rule_hash": spec.strategy_rule_hash,
            "generation_id": generation_id,
            "status": "BACKTESTED",
            "champion_score": 0.5,
            "strategy_spec": spec.to_dict(),
            "evidence_input_sha256": "sha256:test",
            "provenance": "mvp_factory",
        })
    pool.append_candidates(records, root=tmp_path)
    return records


# --- content hash -------------------------------------------------------------

def test_content_hash_changes_on_any_material_change():
    base = promotion_content_sha256(["S1"], ["aaa"], keep_active=False)
    assert promotion_content_sha256(["S1"], ["aaa"], keep_active=False) == base
    assert promotion_content_sha256(["S2"], ["aaa"], keep_active=False) != base
    assert promotion_content_sha256(["S1"], ["bbb"], keep_active=False) != base
    assert promotion_content_sha256(["S1"], ["aaa"], keep_active=True) != base  # add vs replace


def test_content_hash_is_order_insensitive():
    assert promotion_content_sha256(["S1", "S2"], ["a", "b"], keep_active=False) == \
        promotion_content_sha256(["S2", "S1"], ["b", "a"], keep_active=False)


# --- the ask ------------------------------------------------------------------

@requires_local_core
def test_request_builds_decision_and_pending_approval(tmp_path):
    _seed_candidates(tmp_path, _spec_dict())
    prepared = request_promotion(["S1"], keep_active=False, now=NOW, candidates_root=tmp_path)
    decision = prepared["permission_decision"]
    request = prepared["approval_request"]
    payload = decision["fingerprint_payload"]
    assert payload["permission_scope"] == "RUNTIME_GOVERNANCE"
    assert payload["action_type"] == "crypto.strategy_pool.promotion"
    assert payload["content_sha256"] == prepared["content_sha256"]
    assert request["status"] == "PENDING"
    assert request["approved_action_snapshot"]["content_sha256"] == prepared["content_sha256"]


@requires_local_core
def test_request_refuses_unknown_candidate(tmp_path):
    with pytest.raises(MvpRuntimeError) as exc:
        request_promotion(["S_NOPE"], keep_active=False, now=NOW, candidates_root=tmp_path)
    assert exc.value.reason_code == "UNKNOWN_CANDIDATE"


# --- verification: fail closed ------------------------------------------------

def _fake_approval(tmp_path, *, status="APPROVED", content=None, action="crypto.strategy_pool.promotion",
                   expires="2999-01-01T00:00:00Z"):
    if content is None:
        record = pool.resolve_candidates(["S1"], tmp_path)[0]
        content = promotion_content_sha256(
            [record["candidate_id"]], [record["strategy_rule_hash"]], keep_active=False
        )
    return {
        "approval_id": "approval_test",
        "status": status,
        "validity": {"issued_at": NOW, "expires_at": expires},
        "approved_action_snapshot": {"action_type": action, "content_sha256": content},
    }


def test_verify_accepts_matching_approved(tmp_path):
    _seed_candidates(tmp_path, _spec_dict())
    verified = verify_promotion_approval(
        _fake_approval(tmp_path), selectors=["S1"], keep_active=False, root=tmp_path, now=NOW,
    )
    assert verified["approval_id"] == "approval_test"


@pytest.mark.parametrize("mutation,code", [
    (dict(status="PENDING"), "APPROVAL_NOT_APPROVED"),
    (dict(status="REJECTED"), "APPROVAL_NOT_APPROVED"),
    (dict(status="CONSUMED"), "APPROVAL_NOT_APPROVED"),
    (dict(expires="2020-01-01T00:00:00Z"), "APPROVAL_EXPIRED"),
    (dict(action="memory.promotion"), "APPROVAL_WRONG_ACTION"),
])
def test_verify_fails_closed(tmp_path, mutation, code):
    _seed_candidates(tmp_path, _spec_dict())
    with pytest.raises(ApprovalBlocked) as exc:
        verify_promotion_approval(
            _fake_approval(tmp_path, **mutation),
            selectors=["S1"], keep_active=False, root=tmp_path, now=NOW,
        )
    assert exc.value.reason_code == code


def test_verify_missing_approval_fails(tmp_path):
    _seed_candidates(tmp_path, _spec_dict())
    with pytest.raises(ApprovalBlocked) as exc:
        verify_promotion_approval(None, selectors=["S1"], keep_active=False, root=tmp_path, now=NOW)
    assert exc.value.reason_code == "APPROVAL_MISSING"


def test_verify_rejects_mode_flip(tmp_path):
    # An approval for REPLACE cannot execute ADD — the mode rides in the hash.
    _seed_candidates(tmp_path, _spec_dict())
    with pytest.raises(ApprovalBlocked) as exc:
        verify_promotion_approval(
            _fake_approval(tmp_path), selectors=["S1"], keep_active=True, root=tmp_path, now=NOW,
        )
    assert exc.value.reason_code == "APPROVAL_CONTENT_MISMATCH"


def test_verify_refuses_ambiguous_strategy_id_after_regeneration(tmp_path):
    # Approval taken by strategy_id, then a NEW lineage with the same display id
    # lands in the store: the selector no longer names one candidate — refused
    # outright (never silently the newest, the pre-candidate_id last-wins bug).
    _seed_candidates(tmp_path, _spec_dict())
    approval = _fake_approval(tmp_path)
    changed = _spec_dict(entry_rules={"operator": "AND", "conditions": [
        {"feature": "adx", "comparison": ">=", "value": 30.0}]})
    _seed_candidates(tmp_path, changed, generation_id="GEN-002")
    with pytest.raises(ApprovalBlocked) as exc:
        verify_promotion_approval(approval, selectors=["S1"], keep_active=False, root=tmp_path, now=NOW)
    assert exc.value.reason_code == "CANDIDATE_AMBIGUOUS"


def test_verify_by_candidate_id_survives_regeneration(tmp_path):
    # The same scenario selected by candidate_id: the approved lineage is unchanged,
    # so the approval still verifies — the new same-named candidate cannot ride it,
    # and the old one is not orphaned by the newcomer.
    seeded = _seed_candidates(tmp_path, _spec_dict())
    approved_cid = pool.candidate_id(seeded[0])
    approval = _fake_approval(tmp_path)
    changed = _spec_dict(entry_rules={"operator": "AND", "conditions": [
        {"feature": "adx", "comparison": ">=", "value": 30.0}]})
    _seed_candidates(tmp_path, changed, generation_id="GEN-002")
    verified = verify_promotion_approval(
        approval, selectors=[approved_cid], keep_active=False, root=tmp_path, now=NOW,
    )
    assert verified["approval_id"] == "approval_test"
    # The new lineage's own cid does NOT satisfy the approval — it binds content.
    new_cid = next(pool.candidate_id(c) for c in pool.read_candidates(tmp_path)
                   if pool.candidate_id(c) != approved_cid)
    with pytest.raises(ApprovalBlocked) as exc:
        verify_promotion_approval(approval, selectors=[new_cid], keep_active=False, root=tmp_path, now=NOW)
    assert exc.value.reason_code == "APPROVAL_CONTENT_MISMATCH"


# --- the operator door --------------------------------------------------------

def test_promotion_requires_approval_or_explicit_escape(tmp_path):
    _seed_candidates(tmp_path, _spec_dict())
    with pytest.raises(SystemExit) as exc:
        run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                      keep_active=False, root=tmp_path, now=NOW)
    assert "--approval-id" in str(exc.value)
    assert pool.load_active_pool(tmp_path) == {"active_strategies": []}


def test_promotion_with_escape_is_audited_as_such(tmp_path):
    seeded = _seed_candidates(tmp_path, _spec_dict())
    summary = run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                            keep_active=False, root=tmp_path, now=NOW, without_approval=True)
    assert summary["without_approval_escape"] is True and summary["approval_verified"] is False
    assert summary["promoted_candidate_ids"] == [pool.candidate_id(seeded[0])]
    entry = pool.load_active_pool(tmp_path)["active_strategies"][0]
    assert entry["strategy_id"] == "S1"
    assert entry["candidate_id"] == pool.candidate_id(seeded[0])  # lineage rides into the pool


def test_promotion_with_bad_approval_refused(tmp_path):
    _seed_candidates(tmp_path, _spec_dict())
    with pytest.raises(SystemExit) as exc:
        run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                      keep_active=False, root=tmp_path, now=NOW, approval_id="approval_missing")
    assert "APPROVAL_MISSING" in str(exc.value)


def test_promotion_ambiguous_strategy_id_refused(tmp_path):
    # Two generations both named S1: a bare strategy id must refuse, and the
    # explicit candidate_id must promote EXACTLY the selected lineage's rules.
    old = _seed_candidates(tmp_path, _spec_dict())
    changed = _spec_dict(entry_rules={"operator": "AND", "conditions": [
        {"feature": "adx", "comparison": ">=", "value": 30.0}]})
    _seed_candidates(tmp_path, changed, generation_id="GEN-002")

    with pytest.raises(SystemExit) as exc:
        run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                      keep_active=False, root=tmp_path, now=NOW, without_approval=True)
    assert "CANDIDATE_AMBIGUOUS" in str(exc.value)
    assert pool.load_active_pool(tmp_path) == {"active_strategies": []}

    old_cid = pool.candidate_id(old[0])
    summary = run_promotion(selectors=[old_cid], promoted_by="Thomas", reason="r",
                            keep_active=False, root=tmp_path, now=NOW, without_approval=True)
    assert summary["promoted_candidate_ids"] == [old_cid]
    entry = pool.load_active_pool(tmp_path)["active_strategies"][0]
    assert entry["strategy_rule_hash"] == old[0]["strategy_rule_hash"]  # not the newest


def test_legacy_candidate_rows_derive_a_stable_id(tmp_path):
    # Rows written before candidate_id existed derive the same id on every read —
    # the append-only store is never rewritten, and the derived id resolves.
    seeded = _seed_candidates(tmp_path, _spec_dict())
    assert "candidate_id" not in seeded[0]
    first, second = pool.candidate_id(seeded[0]), pool.candidate_id(seeded[0])
    assert first == second and first.startswith("cand")
    resolved = pool.resolve_candidates([first], tmp_path)
    assert resolved[0]["strategy_id"] == "S1" and resolved[0]["candidate_id"] == first


# --- robustness ranking (C8b scorer) ------------------------------------------

def _metrics(trade_count):
    return {"trade_count": trade_count}


def test_tiny_sample_is_fragile_regardless_of_score():
    spec = StrategySpec.from_dict(_spec_dict())
    record = score_robustness(
        spec, _metrics(3),
        {"walk_forward_pass_rate": 1.0, "temporal_stability": 1.0},
        {"regimes_traded": ["TREND_UP", "RANGE"], "profitable_regime_count": 2},
    )
    assert record["trades_per_parameter"] < CRITICAL_TRADES_PER_PARAMETER
    assert record["verdict"] == FRAGILE  # the veto is not a tiebreak
    assert "trades_per_parameter_below_critical" in record["warnings"]


def test_unmeasured_inputs_score_zero_not_full_credit():
    spec = StrategySpec.from_dict(_spec_dict())
    record = score_robustness(spec, _metrics(100), {"walk_forward_pass_rate": None,
                                                    "temporal_stability": None},
                              {"regimes_traded": [], "profitable_regime_count": 0})
    assert record["components"]["temporal_consistency"] == 0.0
    assert record["components"]["regime_breadth"] == 0.0
    assert record["components"]["cost_robustness"] == 0.0  # cost model not ported
    assert "insufficient_walk_forward_evidence" in record["warnings"]


def test_factory_candidates_carry_robustness_verdicts():
    from tests.test_mvp_runtime_crypto_factory import _trending_snapshot

    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=[], now="2026-07-22T12:00:00Z")
    for c in result["candidates"]:
        evidence = c["backtest_evidence"]
        assert evidence["score_basis"] == "robustness_score_v1"
        assert c["champion_score"] == evidence["robustness"]["robustness_score"]
        assert evidence["robustness"]["verdict"] in {"ROBUST", "PROVISIONAL", "FRAGILE"}
        assert 0.0 <= c["champion_score"] <= 1.0
