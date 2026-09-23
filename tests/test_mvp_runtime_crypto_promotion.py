"""C8b promotion-approval tests — the ask binds the exact promotion; verify never spends.

Under test: the content hash changes on any material change (ids, rules, add-vs-
replace); the R9 request builds a schema-valid RUNTIME_GOVERNANCE decision + PENDING
approval; verification fails closed on pending/expired/wrong-action/content-mismatch
and on a candidate whose rules changed after approval; the operator door requires an
approval or the explicit escape; and the robustness scorer ranks candidates with the
source's veto semantics."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

import runtime.mvp_runtime.crypto.promotion as promotion_mod
import scripts.promote_strategy_candidates as promote_door
from runtime.mvp_runtime.approval_store import STORE_REL as APPROVAL_STORE_REL
from runtime.mvp_runtime.approval_store import ApprovalStore
from runtime.read_only_kernel import integrity
from runtime.mvp_runtime.crypto import cost, paper, pool
from runtime.mvp_runtime.crypto.factory import run_factory
from runtime.mvp_runtime.crypto.promotion import (
    PROMOTION_GATES,
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


@pytest.fixture(autouse=True)
def _machine_is_staged_for_arming(monkeypatch):
    """PR1c: arming LIVE reads the execution stage. These tests predate the ladder and are about
    other gates, so the machine stands at the rung that admits it; the stage gate's own refusals
    are tested in `test_arming_live_needs_the_execution_stage_that_admits_it`."""
    from runtime.mvp_runtime.crypto import execution_stage as es

    staged = es.StageStatus(stage="LIVE_AUTONOMOUS", valid=True, reason_code=None,
                            recorded_stage="LIVE_AUTONOMOUS")
    for module in (promotion_mod, promote_door):
        monkeypatch.setattr(module, "resolve_execution_stage", lambda root=None, **kw: staged)

# Sentinel: `cost_summary=None` / `bars_replayed=None` mean "seed a candidate that records no
# cost model / no replay window", which are cases under test, so neither can double as "caller
# said nothing".
_MISSING = object()


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


def _current_cost_summary():
    """Evidence scored under the cost model in force, because the promotion door now checks.

    These fixtures carry no cost model at all, which reads as `cost_model_unrecorded` — and an
    unrecorded basis cannot say whether its numbers are inflated, so `assert_promotable_cost_basis`
    refuses it. That refusal is correct and is tested directly below; here it is noise, because
    what these cases exercise is approval verification and display-id collision. Stamping the
    current model keeps each test about its own subject."""
    return {"total_net_r": 10.0, "total_fee_cost_r": 1.0, "total_maker_fee_cost_r": 0.2,
            "total_slippage_cost_r": 0.5, "cost_model": {
                "taker_fee_bps": cost.DEFAULT_TAKER_FEE_BPS,
                "maker_fee_bps": cost.DEFAULT_MAKER_FEE_BPS,
                "slippage_bps": cost.DEFAULT_SLIPPAGE_BPS,
                "stop_slippage_bps": cost.DEFAULT_STOP_SLIPPAGE_BPS,
                "funding_bps_per_interval": cost.DEFAULT_FUNDING_BPS_PER_INTERVAL,
                "funding_source": cost.FUNDING_SOURCE_VENUE,
            }}


def _current_bars_replayed(spec):
    """The replay window in force, because the promotion door now checks that too.

    Same shape as `_current_cost_summary` above and for the same reason: evidence that records
    no window cannot say how much market its verdict was earned on, so
    `assert_promotable_evidence_depth` refuses it. That refusal is correct and is tested in
    `test_mvp_runtime_crypto_evidence_depth.py`; here it is noise. Read from the factory's live
    target rather than written down, so these fixtures follow the window when it moves."""
    return pool.expected_replayed_bars(spec.timeframe)


def _seed_candidates(tmp_path, *specs, generation_id="GEN-001", cost_summary=_MISSING,
                     bars_replayed=_MISSING):
    records = []
    for spec_dict in specs:
        spec = StrategySpec.from_dict(spec_dict)
        # 60 closes and a THIN holdout block, so the fixture clears the 5-3 observation
        # entry bar the same way it clears the cost and depth gates: these tests are about
        # OTHER axes, and a fixture the door refuses on sight is noise in every one of them.
        # Stored CONFIRMED, so the fixture clears both the 5-3 entry bar and the 5-1 LIVE
        # confirmation gate: these tests are about OTHER axes.
        evidence = {"closed_count": 60, "expectancy": 0.5,
                    "robustness": {"verdict": "PROVISIONAL", "holdout_status": "CONFIRMED"}}
        bars = _current_bars_replayed(spec) if bars_replayed is _MISSING else bars_replayed
        if bars is not None:
            evidence["bars_replayed"] = bars
        summary = _current_cost_summary() if cost_summary is _MISSING else cost_summary
        if summary is not None:
            evidence["cost_summary"] = summary
        records.append({
            "strategy_id": spec.strategy_id,
            "strategy_rule_hash": spec.strategy_rule_hash,
            "generation_id": generation_id,
            "status": "BACKTESTED",
            "champion_score": 0.5,
            "strategy_spec": spec.to_dict(),
            "backtest_evidence": evidence,
            "evidence_input_sha256": "sha256:test",
            "provenance": "mvp_factory",
        })
    pool.append_candidates(records, root=tmp_path)
    return records


# --- content hash -------------------------------------------------------------

def test_content_hash_changes_on_any_material_change():
    base = promotion_content_sha256(["S1"], ["aaa"], keep_active=False, live_tier="LIVE", artifact_sha256s=["x"])
    assert promotion_content_sha256(["S1"], ["aaa"], keep_active=False, live_tier="LIVE", artifact_sha256s=["x"]) == base
    assert promotion_content_sha256(["S2"], ["aaa"], keep_active=False, live_tier="LIVE", artifact_sha256s=["x"]) != base
    assert promotion_content_sha256(["S1"], ["bbb"], keep_active=False, live_tier="LIVE", artifact_sha256s=["x"]) != base
    assert promotion_content_sha256(["S1"], ["aaa"], keep_active=True, live_tier="LIVE", artifact_sha256s=["x"]) != base  # add vs replace
    # v5 (PR3a): the artifact the candidate installs as.
    assert promotion_content_sha256(["S1"], ["aaa"], keep_active=False, live_tier="LIVE", artifact_sha256s=["y"]) != base


def test_content_hash_is_order_insensitive():
    assert promotion_content_sha256(["S1", "S2"], ["a", "b"], keep_active=False, live_tier="LIVE",
                                    artifact_sha256s=["x1", "x2"]) == \
        promotion_content_sha256(["S2", "S1"], ["b", "a"], keep_active=False, live_tier="LIVE",
                                 artifact_sha256s=["x2", "x1"])


def test_the_hash_pairs_each_artifact_with_its_candidate():
    """v4 sorted the ids and the rule hashes as two lists, so which belonged to which was not in the
    hash. The artifacts are pairs: the same two artifacts swapped between the candidates are another
    promotion."""
    kept = promotion_content_sha256(["c1", "c2"], ["a", "b"], keep_active=False, live_tier="LIVE",
                                    artifact_sha256s=["x1", "x2"])
    assert promotion_content_sha256(["c1", "c2"], ["a", "b"], keep_active=False, live_tier="LIVE",
                                    artifact_sha256s=["x2", "x1"]) != kept
    assert promotion_mod.artifact_pairs(["c2", "c1"], ["x2", "x1"]) == [["c1", "x1"], ["c2", "x2"]]


@pytest.mark.parametrize("artifacts", [[], ["x1"], ["x1", "x2", "x3"], ["x1", ""], ["x1", None], "x1x2"],
                         ids=["none", "short", "long", "empty", "not-a-string", "a-string"])
def test_a_promotion_names_exactly_one_artifact_per_candidate(artifacts):
    with pytest.raises(ApprovalBlocked) as exc:
        promotion_content_sha256(["c1", "c2"], ["a", "b"], keep_active=False, live_tier="LIVE",
                                 artifact_sha256s=artifacts)
    assert exc.value.reason_code == "PROMOTION_ARTIFACT_INVALID"


# --- the ask ------------------------------------------------------------------

@requires_local_core
def test_request_builds_decision_and_pending_approval(tmp_path):
    _seed_candidates(tmp_path, _spec_dict())
    prepared = request_promotion(["S1"], keep_active=False, live_tier="LIVE", now=NOW, candidates_root=tmp_path)
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
        request_promotion(["S_NOPE"], keep_active=False, live_tier="LIVE", now=NOW, candidates_root=tmp_path)
    assert exc.value.reason_code == "UNKNOWN_CANDIDATE"


# --- verification: fail closed ------------------------------------------------

def _fake_approval(tmp_path, *, status="APPROVED", content=None, action="crypto.strategy_pool.promotion",
                   expires="2999-01-01T00:00:00Z"):
    record = pool.resolve_candidates(["S1"], tmp_path)[0]
    artifacts = promotion_mod.candidate_artifacts([record])
    if content is None:
        content = promotion_content_sha256(
            [record["candidate_id"]], [record["strategy_rule_hash"]], keep_active=False, live_tier="LIVE",
            artifact_sha256s=artifacts,
        )
    return {
        "approval_id": "approval_test",
        "status": status,
        "validity": {"issued_at": NOW, "expires_at": expires},
        # A LIVE install must fall inside the window the gate will check (review of #887).
        "decision": {"decision_reason": "yes", "decided_at": NOW},
        "approved_action_snapshot": {"action_type": action, "content_sha256": content,
                                     "expires_at": expires,
                                     # The pairs the order-time check reads (PR3a).
                                     "normalized_parameters": {
                                         "artifacts": [[record["candidate_id"], artifacts[0]]]}},
    }


def test_verify_accepts_matching_approved(tmp_path):
    _seed_candidates(tmp_path, _spec_dict())
    verified = verify_promotion_approval(
        _fake_approval(tmp_path), selectors=["S1"], keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW,
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
            selectors=["S1"], keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW,
        )
    assert exc.value.reason_code == code


def test_verify_missing_approval_fails(tmp_path):
    _seed_candidates(tmp_path, _spec_dict())
    with pytest.raises(ApprovalBlocked) as exc:
        verify_promotion_approval(None, selectors=["S1"], keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW)
    assert exc.value.reason_code == "APPROVAL_MISSING"


def test_verify_rejects_mode_flip(tmp_path):
    # An approval for REPLACE cannot execute ADD — the mode rides in the hash.
    _seed_candidates(tmp_path, _spec_dict())
    with pytest.raises(ApprovalBlocked) as exc:
        verify_promotion_approval(
            _fake_approval(tmp_path), selectors=["S1"], keep_active=True, live_tier="LIVE", root=tmp_path, now=NOW,
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
        verify_promotion_approval(approval, selectors=["S1"], keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW)
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
        approval, selectors=[approved_cid], keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW,
    )
    assert verified["approval_id"] == "approval_test"
    # The new lineage's own cid does NOT satisfy the approval — it binds content.
    new_cid = next(pool.candidate_id(c) for c in pool.read_candidates(tmp_path)
                   if pool.candidate_id(c) != approved_cid)
    with pytest.raises(ApprovalBlocked) as exc:
        verify_promotion_approval(approval, selectors=[new_cid], keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW)
    assert exc.value.reason_code == "APPROVAL_CONTENT_MISMATCH"


# --- the operator door --------------------------------------------------------

def test_promotion_requires_approval_or_explicit_escape(tmp_path):
    _seed_candidates(tmp_path, _spec_dict())
    with pytest.raises(SystemExit) as exc:
        run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                      keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW)
    assert "--approval-id" in str(exc.value)
    assert pool.load_active_pool(tmp_path) == {"active_strategies": []}


def test_promotion_with_escape_is_audited_as_such(tmp_path):
    seeded = _seed_candidates(tmp_path, _spec_dict())
    summary = run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                            keep_active=False, live_tier="OBSERVATION", root=tmp_path, now=NOW, without_approval=True)
    assert summary["without_approval_escape"] is True and summary["approval_verified"] is False
    assert summary["promoted_candidate_ids"] == [pool.candidate_id(seeded[0])]
    entry = pool.load_active_pool(tmp_path)["active_strategies"][0]
    assert entry["strategy_id"] == "S1"
    assert entry["candidate_id"] == pool.candidate_id(seeded[0])  # lineage rides into the pool


# --- the cost-basis gate ------------------------------------------------------
#
# The candidate store holds evidence scored under models the venue no longer charges: on the
# live machine 224 of 359 rows paid 2.5 bps taker where it charges 5.0, and 45 recorded no
# model at all. `backtest_evidence` is durable and the store is append-only, so those numbers
# can never be repaired in place — the promotion door is the only place the mismatch can be
# caught before evidence turns into real money.

def _stale_summary(**model):
    return {"total_net_r": 10.0, "total_fee_cost_r": 1.0, "total_maker_fee_cost_r": 0.0,
            "total_slippage_cost_r": 0.5, "cost_model": model}


def test_promotion_refuses_evidence_scored_more_cheaply_than_the_venue_charges(tmp_path):
    _seed_candidates(tmp_path, _spec_dict(),
                     cost_summary=_stale_summary(taker_fee_bps=2.5, slippage_bps=3.0))
    with pytest.raises(SystemExit) as exc:
        run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                      keep_active=False, live_tier="OBSERVATION", root=tmp_path, now=NOW, without_approval=True)
    assert "CANDIDATE_COST_BASIS_STALE" in str(exc.value)
    assert pool.load_active_pool(tmp_path) == {"active_strategies": []}, "nothing installed"


def test_promotion_refuses_evidence_with_no_recorded_cost_model(tmp_path):
    """Worse than stale: it cannot even say which way its numbers err."""
    _seed_candidates(tmp_path, _spec_dict(), cost_summary=None)
    with pytest.raises(SystemExit) as exc:
        run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                      keep_active=False, live_tier="OBSERVATION", root=tmp_path, now=NOW, without_approval=True)
    assert "CANDIDATE_COST_BASIS_STALE" in str(exc.value)


def test_the_stale_basis_escape_promotes_and_is_recorded(tmp_path):
    """An escape that leaves no trace is an escape nobody can audit. The flag AND the basis
    each promoted candidate stood on both ride onto the ledger summary, because a pool entry
    outlives the argv that installed it."""
    _seed_candidates(tmp_path, _spec_dict(),
                     cost_summary=_stale_summary(taker_fee_bps=2.5, slippage_bps=3.0))
    summary = run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                            keep_active=False, live_tier="OBSERVATION", root=tmp_path, now=NOW, without_approval=True,
                            allow_stale_cost_basis=True)
    assert summary["stale_cost_basis_escape"] is True
    # The recorded basis names every axis the evidence is stale on, not just the one this test
    # set: a 2.5 bps model also predates the funding term, and the ledger has to say so.
    assert summary["cost_bases"] == [
        "net_of_fees_and_slippage:taker_2.5bps+slip_3.0bps+funding_uncharged"
    ]
    assert len(pool.load_active_pool(tmp_path)["active_strategies"]) == 1


def test_a_promotion_on_current_evidence_records_the_escape_as_unused(tmp_path):
    _seed_candidates(tmp_path, _spec_dict())
    summary = run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                            keep_active=False, live_tier="OBSERVATION", root=tmp_path, now=NOW, without_approval=True)
    assert summary["stale_cost_basis_escape"] is False
    assert summary["cost_bases"] == [pool.current_cost_basis()]


def test_the_ask_refuses_stale_evidence_too(tmp_path):
    """Checked at the ASK, not only the install. An approval Thomas answers for a promotion
    the next step was always going to refuse spends his attention on nothing.

    No `requires_local_core`, deliberately: the refusal lands in `run_promotion_gates`, before
    `build_task`/`bind_task_to_core` ever reach for a Core. A test gated on one would have been
    skipped on exactly the machines where the gate is cheapest to break."""
    _seed_candidates(tmp_path, _spec_dict(),
                     cost_summary=_stale_summary(taker_fee_bps=2.5, slippage_bps=3.0))
    with pytest.raises(ApprovalBlocked) as exc:
        request_promotion(["S1"], keep_active=False, live_tier="LIVE", now=NOW, candidates_root=tmp_path)
    assert exc.value.reason_code == "CANDIDATE_COST_BASIS_STALE"


# --- which door owns the quality gates, and which one owns the approval ---------
#
# `run_promotion` resolves the selection TWICE: once inside `verify_promotion_approval`, which
# re-derives the content hash from the CURRENT store, and once in its own guard block, which
# runs the quality gates. Only the second call can see the operator's escape flags — so while
# verification ran the gates too, an approval Thomas had explicitly answered for a promotion
# WITH an escape could never install: the escape was passed, the approval was valid, and the
# first resolve refused before the second one was reached.
#
# The fix is the division these tests pin: verification answers "does this approval authorize
# exactly this promotion", which is a question about ids, rules and add-vs-replace — the three
# fields in `promotion_content_sha256`. Whether the evidence behind those ids is believable is
# asked at the ASK (so Thomas is never asked a question that cannot execute) and at the
# INSTALL (where evidence turns into money), both of which honour the escapes.


def _store_approval(tmp_path, approval):
    """Put a real record in the store the door reads, not a hand-passed dict: the defect lives
    in `run_promotion`, which fetches its own approval by id."""
    ApprovalStore(tmp_path / APPROVAL_STORE_REL).append([approval])
    return approval["approval_id"]


def test_an_approved_promotion_can_still_use_the_stale_basis_escape(tmp_path):
    """The headline case. Thomas approved this exact promotion and the operator passed the
    documented escape; the door must install it, not refuse it in the half of itself that
    cannot see the flag."""
    _seed_candidates(tmp_path, _spec_dict(),
                     cost_summary=_stale_summary(taker_fee_bps=2.5, slippage_bps=3.0))
    approval_id = _store_approval(tmp_path, _fake_approval(tmp_path))
    summary = run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                            keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW,
                            approval_id=approval_id, allow_stale_cost_basis=True)
    assert summary["approval_verified"] is True and summary["stale_cost_basis_escape"] is True
    entries = pool.load_active_pool(tmp_path)["active_strategies"]
    assert len(entries) == 1
    # What an approved LIVE promotion is FOR: the entry is armed. Unpinned until the PR1c review —
    # the door could have installed every promotion at OBSERVATION and the suite stayed green.
    assert pool.entry_live_tier(entries[0]) == "LIVE"


def test_an_approved_promotion_can_still_use_the_unrecorded_depth_escape(tmp_path):
    """Second axis, same defect: the escape is per-axis, so each one needs its own pin."""
    _seed_candidates(tmp_path, _spec_dict(), bars_replayed=None)
    approval_id = _store_approval(tmp_path, _fake_approval(tmp_path))
    summary = run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                            keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW,
                            approval_id=approval_id, allow_unrecorded_evidence_depth=True)
    assert summary["approval_verified"] is True
    assert summary["unrecorded_evidence_depth_escape"] is True
    assert len(pool.load_active_pool(tmp_path)["active_strategies"]) == 1


def test_an_approved_promotion_can_still_use_the_quarantined_derivation_escape(tmp_path, monkeypatch):
    """Third axis, reached through the store's reader because it cannot be reached through its
    writer: `PROMOTABLE_DERIVATION_TYPES` equals the set the append door admits, so no row this
    gate refuses can be written into a real store today. That is the gate working as designed —
    it stands before the first row it must stop — and it is exactly why this axis would
    otherwise carry the defect silently until the day someone starts minting one."""
    spec = StrategySpec.from_dict(_spec_dict())
    row = {
        "strategy_id": spec.strategy_id, "strategy_rule_hash": spec.strategy_rule_hash,
        "generation_id": "GEN-001", "status": "BACKTESTED", "champion_score": 0.5,
        "strategy_spec": spec.to_dict(),
        "backtest_evidence": {"closed_count": 60, "expectancy": 0.5,
                              "robustness": {"verdict": "PROVISIONAL",
                                             "holdout_status": "CONFIRMED"},
                              "bars_replayed": _current_bars_replayed(spec),
                              "cost_summary": _current_cost_summary()},
        "evidence_input_sha256": "sha256:test", "provenance": "mvp_factory",
        "derivation_type": "trial_family",
    }
    # Stamped as the append door would, so this reaches the derivation gate rather than stopping
    # at the record-stamp gate (which reads the stamp's presence; the reader checks its value).
    row["record_sha256"] = integrity.sha256_record(row)
    monkeypatch.setattr(pool, "read_candidates", lambda root=None: [row])
    approval_id = _store_approval(tmp_path, _fake_approval(tmp_path))
    summary = run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                            keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW,
                            approval_id=approval_id, allow_quarantined_derivation=True)
    assert summary["approval_verified"] is True
    assert summary["quarantined_derivation_escape"] is True
    assert len(pool.load_active_pool(tmp_path)["active_strategies"]) == 1


def _unstamped_row():
    spec = StrategySpec.from_dict(_spec_dict())
    return {
        "strategy_id": spec.strategy_id, "strategy_rule_hash": spec.strategy_rule_hash,
        "generation_id": "GEN-001", "status": "BACKTESTED", "champion_score": 0.5,
        "strategy_spec": spec.to_dict(),
        "backtest_evidence": {"closed_count": 60, "expectancy": 0.5,
                              "robustness": {"verdict": "PROVISIONAL",
                                             "holdout_status": "CONFIRMED"},
                              "bars_replayed": _current_bars_replayed(spec),
                              "cost_summary": _current_cost_summary()},
        "evidence_input_sha256": "sha256:test", "provenance": "crypto_ai_system_import",
    }


def test_an_approved_promotion_refuses_an_unstamped_row(tmp_path, monkeypatch):
    """A row with no self-hash is refused at the install door, approval or not: the store cannot
    say it is the row it wrote. Reached through the reader, since the append door stamps every
    row it writes."""
    row = _unstamped_row()
    monkeypatch.setattr(pool, "read_candidates", lambda root=None: [row])
    approval_id = _store_approval(tmp_path, _fake_approval(tmp_path))
    with pytest.raises(SystemExit) as exc:
        run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r", keep_active=False,
                      live_tier="LIVE", root=tmp_path, now=NOW, approval_id=approval_id)
    assert "CANDIDATE_RECORD_UNSTAMPED" in str(exc.value)
    assert pool.load_active_pool(tmp_path).get("active_strategies") in (None, [])


def test_an_approved_promotion_can_use_the_unstamped_record_escape(tmp_path, monkeypatch):
    row = _unstamped_row()
    monkeypatch.setattr(pool, "read_candidates", lambda root=None: [row])
    approval_id = _store_approval(tmp_path, _fake_approval(tmp_path))
    summary = run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                            keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW,
                            approval_id=approval_id, allow_unstamped_record=True)
    assert summary["unstamped_record_escape"] is True
    assert summary["unstamped_records"] == summary["promoted_candidate_ids"]
    assert "record_stamp" in summary["reviews_skipped"]
    assert len(pool.load_active_pool(tmp_path)["active_strategies"]) == 1


@pytest.mark.parametrize("seed,code", [
    (dict(cost_summary=_stale_summary(taker_fee_bps=2.5, slippage_bps=3.0)),
     "CANDIDATE_COST_BASIS_STALE"),
    (dict(bars_replayed=None), "CANDIDATE_EVIDENCE_DEPTH_UNRECORDED"),
])
def test_an_approved_promotion_without_the_escape_still_refuses(tmp_path, seed, code):
    """The other half of the division, and the reason moving the gates off the verification
    path is safe: an approval is Thomas's yes to a set of ids, never a waiver of the evidence
    checks. Without the flag the install door refuses on its own authority."""
    _seed_candidates(tmp_path, _spec_dict(), **seed)
    approval_id = _store_approval(tmp_path, _fake_approval(tmp_path))
    with pytest.raises(SystemExit) as exc:
        run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                      keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW, approval_id=approval_id)
    assert code in str(exc.value)
    assert pool.load_active_pool(tmp_path) == {"active_strategies": []}, "nothing installed"


def test_verification_judges_the_approval_and_not_the_evidence(tmp_path):
    """Stated directly at the function, because `run_promotion` alone cannot distinguish "the
    gates moved" from "the gates ran and passed". Verification is a hash-identity check: the
    escapes are deliberately kept OUT of `promotion_content_sha256`, so a door that recomputes
    that hash has no business deciding whether the evidence behind it is believable."""
    _seed_candidates(tmp_path, _spec_dict(),
                     cost_summary=_stale_summary(taker_fee_bps=2.5, slippage_bps=3.0))
    verified = verify_promotion_approval(
        _fake_approval(tmp_path), selectors=["S1"], keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW,
    )
    assert verified["approval_id"] == "approval_test"


def test_promotion_derives_unique_display_id_on_collision(tmp_path):
    # The factory restarts strategy_id at S001 every generation, so two distinct lineages
    # can share a display name. The batch must NOT be refused: the collision gets a unique
    # {sid}-{generation} display id, candidate_id stays the lineage key, and the installed
    # pool passes the identity invariant (a re-load would raise otherwise).
    # The two sit in DIFFERENT contexts on purpose. A display-name collision has nothing to do
    # with the context — the factory's per-generation numbering collides across all of them —
    # but two routable strategies in ONE context is what `assert_pool_within_size_cap` refuses,
    # and that refusal is not this test's subject (it has its own tests below).
    a = _seed_candidates(tmp_path, _spec_dict(), generation_id="GEN-001")
    b = _seed_candidates(
        tmp_path,
        _spec_dict(symbol_scope=["ETHUSDT"],
                   entry_rules={"operator": "AND",
                                "conditions": [{"feature": "adx", "comparison": ">=", "value": 30.0}]}),
        generation_id="GEN-002",
    )
    cid_a, cid_b = pool.candidate_id(a[0]), pool.candidate_id(b[0])
    summary = run_promotion(selectors=[cid_a, cid_b], promoted_by="Thomas", reason="r",
                            keep_active=False, live_tier="OBSERVATION", root=tmp_path, now=NOW, without_approval=True)
    entries = pool.load_active_pool(tmp_path)["active_strategies"]  # re-load validates the invariant
    sids = [e["strategy_id"] for e in entries]
    assert len(set(sids)) == 2 and "S1" in sids                    # one bare, one derived
    assert any(s in ("S1-GEN-001", "S1-GEN-002") for s in sids)    # lineage-readable
    assert {e["candidate_id"] for e in entries} == {cid_a, cid_b}  # distinct lineages
    assert set(summary["promoted_display_ids"]) == set(sids)


def test_promotion_residual_collision_fails_closed(tmp_path):
    # Three distinct lineages sharing BOTH strategy_id and generation cannot all get a
    # unique {sid}-{generation} name — the third fails closed rather than silently
    # collapsing onto the second, and nothing is installed.
    specs = [
        _spec_dict(entry_rules={"operator": "AND",
                                "conditions": [{"feature": "close", "comparison": ">", "value": float(v)}]})
        for v in (1.0, 2.0, 3.0)
    ]
    seeded = _seed_candidates(tmp_path, *specs, generation_id="GEN-002")
    cids = [pool.candidate_id(s) for s in seeded]
    with pytest.raises(SystemExit) as exc:
        # Three same-family lineages in one batch now trip the 5-3 family cap first; escape
        # it explicitly, because the residual collision below is the failure under test.
        run_promotion(selectors=cids, promoted_by="Thomas", reason="r",
                      keep_active=False, live_tier="OBSERVATION", root=tmp_path, now=NOW,
                      without_approval=True, allow_family_overflow=True)
    assert "cannot assign a unique strategy_id" in str(exc.value)
    assert pool.load_active_pool(tmp_path) == {"active_strategies": []}


def test_promotion_with_bad_approval_refused(tmp_path):
    _seed_candidates(tmp_path, _spec_dict())
    with pytest.raises(SystemExit) as exc:
        run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                      keep_active=False, live_tier="LIVE", root=tmp_path, now=NOW, approval_id="approval_missing")
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
                      keep_active=False, live_tier="OBSERVATION", root=tmp_path, now=NOW, without_approval=True)
    assert "CANDIDATE_AMBIGUOUS" in str(exc.value)
    assert pool.load_active_pool(tmp_path) == {"active_strategies": []}

    old_cid = pool.candidate_id(old[0])
    summary = run_promotion(selectors=[old_cid], promoted_by="Thomas", reason="r",
                            keep_active=False, live_tier="OBSERVATION", root=tmp_path, now=NOW, without_approval=True)
    assert summary["promoted_candidate_ids"] == [old_cid]
    entry = pool.load_active_pool(tmp_path)["active_strategies"][0]
    assert entry["strategy_rule_hash"] == old[0]["strategy_rule_hash"]  # not the newest


def test_candidate_store_stamps_and_verifies_self_hash(tmp_path):
    _seed_candidates(tmp_path, _spec_dict())
    row = pool.read_candidates(tmp_path)[0]
    assert row["record_sha256"].startswith("sha256:")  # stamped at append time

    # Edit the durable row (inflate the score) — the read must refuse the store.
    path = pool.candidates_path(tmp_path)
    import json
    tampered = {**row, "champion_score": 0.99}
    path.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
    with pytest.raises(MvpRuntimeError) as exc:
        pool.read_candidates(tmp_path)
    assert exc.value.reason_code == "CANDIDATES_TAMPERED"


def test_a_corrupt_candidate_line_keeps_this_stores_error_class_and_line_number(tmp_path):
    """`read_candidates` streams through ``jsonl.iter_numbered`` now; what must not move is which
    class comes out. 47 sites in the runtime catch ToolError by name — five in ``promotion.py``,
    which reads this very store — so adopting jsonl's PersistenceError would fail *past* those
    handlers rather than at them. The line number is the operator's other handle, and it counts
    file lines: a blank row must not shift it."""
    import json

    from runtime.mvp_runtime.errors import PersistenceError, ToolError

    path = pool.candidates_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"strategy_id": "s1"}) + "\n\n{not json\n", encoding="utf-8")

    with pytest.raises(ToolError) as exc:
        pool.read_candidates(tmp_path)
    assert exc.value.reason_code == "CANDIDATES_UNREADABLE"
    assert not isinstance(exc.value, PersistenceError)
    assert "line 3" in str(exc.value)  # the file line, not the second object


def test_candidate_rows_without_hash_still_read(tmp_path):
    # Pre-stamping legacy rows have nothing to verify — they must keep resolving.
    import json
    path = pool.candidates_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = {"strategy_id": "S9", "strategy_rule_hash": "aaa", "generation_id": "GEN-001",
              "evidence_input_sha256": "sha256:test", "strategy_spec": _spec_dict(strategy_id="S9")}
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    assert pool.resolve_candidates(["S9"], tmp_path)[0]["strategy_id"] == "S9"


@pytest.mark.parametrize("dup_field", ["strategy_id", "candidate_id"])
def test_a_pool_with_duplicate_identity_is_refused_at_both_doors(tmp_path, dup_field):
    entry = {"strategy_id": "S1", "candidate_id": "c1", "status": "PAPER_ACTIVE",
             "champion_score": 0.5, "strategy_spec": _spec_dict()}
    other = {**entry, "strategy_id": "S2", "candidate_id": "c2"}
    other[dup_field] = entry[dup_field]                       # collide on one key
    bad = {"active_strategies": [entry, other]}

    with pytest.raises(MvpRuntimeError) as exc:
        pool.install_active_pool(bad, root=tmp_path)          # cannot be written
    assert exc.value.reason_code == "STRATEGY_POOL_DUPLICATE"

    import json
    path = pool.pool_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bad), encoding="utf-8")        # ...nor traded on if it is
    with pytest.raises(MvpRuntimeError) as exc:
        pool.load_active_pool(tmp_path)
    assert exc.value.reason_code == "STRATEGY_POOL_DUPLICATE"


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


# --- the pool-sizing cap (the promotion door's fourth guard) --------------------

def _routable(sid, *, symbol="BTCUSDT", timeframe="1d", status="PAPER_ACTIVE"):
    return {"strategy_id": sid, "candidate_id": f"cand_{sid}", "status": status,
            "champion_score": 0.5,
            "strategy_spec": _spec_dict(strategy_id=sid, symbol_scope=[symbol],
                                        timeframe=timeframe)}


SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "BNBUSDT"]


def _grid_slots():
    """Every routable slot the per-context caps admit on the 5-symbol grid, slow first."""
    slots = [(sym, tf) for tf in ("4h", "1d") for sym in SYMBOLS]
    for tf in ("15m", "1h"):
        for sym in SYMBOLS:
            slots.extend([(sym, tf)] * pool.MAX_ROUTABLE_PER_CONTEXT_FAST)
    return slots


def _at_cap(n):
    """``n`` routable entries filling grid slots in order — within every per-context cap."""
    return [_routable(f"S{i:03d}", symbol=sym, timeframe=tf)
            for i, (sym, tf) in enumerate(_grid_slots()[:n])]


def test_a_pool_at_the_cap_is_allowed():
    entries = _at_cap(pool.MAX_ROUTABLE_STRATEGIES)
    assert len(entries) == 30                          # the grid-implied sum: 5 * (2+2+1+1)
    pool.assert_pool_within_size_cap(entries)          # exactly at the cap is fine


def test_one_routable_strategy_too_many_is_refused():
    entries = _at_cap(pool.MAX_ROUTABLE_STRATEGIES)
    entries.append(_routable("S_EXTRA", symbol="BTCUSDT", timeframe="30m"))
    with pytest.raises(MvpRuntimeError) as exc:
        pool.assert_pool_within_size_cap(entries)
    assert exc.value.reason_code == "POOL_SIZE_CAP_EXCEEDED"
    assert "31" in exc.value.reason and "--allow-oversized-pool" in exc.value.reason


def _opposing(entry):
    entry["strategy_spec"] = {**entry["strategy_spec"], "direction": "short"}
    return entry


def test_a_slow_context_holds_two_of_one_direction_and_refuses_a_third():
    """Thomas 2026-09-02: the exclusive slow slot bought judgeability, which the shadow book
    and the per-lineage forward stream now supply regardless of routing — so two 4h lineages
    that agree on direction share the context. Exactly two: the third is where the routed
    book is split among more lineages than the router ranks on evidence."""
    two = [_routable("S1", symbol="BTCUSDT", timeframe="4h"),
           _routable("S2", symbol="BTCUSDT", timeframe="4h")]
    pool.assert_pool_within_size_cap(two)                # 2 longs, 28 slots spare: fine
    with pytest.raises(MvpRuntimeError) as exc:
        pool.assert_pool_within_size_cap(two + [_routable("S3", symbol="BTCUSDT", timeframe="4h")])
    assert exc.value.reason_code == "POOL_CONTEXT_CAP_EXCEEDED"
    assert "BTCUSDT 4h" in exc.value.reason and "S1, S2, S3" in exc.value.reason


def test_a_slow_context_refuses_an_opposing_second_occupant_and_names_both():
    """The condition the slow slot carries and the fast one does not: two opposing 1d
    lineages fail every unresolved bar closed until one is backed, and a 1d bar is a day of
    routing. Refused with the incumbent's direction on the record, so the operator can see
    which side to promote alongside — and under the same escape as the size cap."""
    entries = [_routable("S_LONG", symbol="ETHUSDT", timeframe="1d"),
               _opposing(_routable("S_SHORT", symbol="ETHUSDT", timeframe="1d"))]
    with pytest.raises(MvpRuntimeError) as exc:
        pool.assert_pool_within_size_cap(entries)
    assert exc.value.reason_code == "POOL_CONTEXT_DIRECTION_SPLIT"
    assert "ETHUSDT 1d" in exc.value.reason
    assert "S_LONG LONG" in exc.value.reason and "S_SHORT SHORT" in exc.value.reason
    assert "--allow-oversized-pool" in exc.value.reason


def test_a_fast_context_still_admits_an_opposing_pair():
    """Scope of the 2026-09-02 change: fast contexts keep the 2026-08-24 rule untouched. A
    blocked 15m bar costs minutes, and `routable_directional_capacity` reads a mixed fast
    pair as the flexible slot it is."""
    pool.assert_pool_within_size_cap([
        _routable("S1", symbol="BTCUSDT", timeframe="1h"),
        _opposing(_routable("S2", symbol="BTCUSDT", timeframe="1h")),
    ])


def test_a_fast_context_holds_two_and_refuses_a_third():
    """Thomas 2026-08-24: 15m/1h contexts refill a judgement window in weeks even split,
    so they hold two — and exactly two, because the third is where the split arithmetic
    starts to look like the slow-context failure again."""
    two = [_routable("S1", symbol="BTCUSDT", timeframe="15m"),
           _routable("S2", symbol="BTCUSDT", timeframe="15m")]
    pool.assert_pool_within_size_cap(two)
    with pytest.raises(MvpRuntimeError) as exc:
        pool.assert_pool_within_size_cap(two + [_routable("S3", symbol="BTCUSDT", timeframe="15m")])
    assert exc.value.reason_code == "POOL_CONTEXT_CAP_EXCEEDED"
    assert "BTCUSDT 15m" in exc.value.reason


def test_a_multi_symbol_strategy_occupies_every_symbol_it_is_scoped_to():
    """Scope, not primary symbol: `route_entries` judges it on each, so the cap must too — a
    pooled lineage shares (or splits) every context it is scoped to, not just its first."""
    wide = _routable("S_WIDE", timeframe="4h")
    wide["strategy_spec"] = _spec_dict(strategy_id="S_WIDE", timeframe="4h",
                                       symbol_scope=["BTCUSDT", "ETHUSDT"])
    pool.assert_pool_within_size_cap([wide, _routable("S_ETH", symbol="ETHUSDT", timeframe="4h")])
    # An opposing single-symbol lineage splits the pooled one's ETH context, not its BTC one.
    with pytest.raises(MvpRuntimeError) as exc:
        pool.assert_pool_within_size_cap(
            [wide, _opposing(_routable("S_ETH", symbol="ETHUSDT", timeframe="4h"))])
    assert exc.value.reason_code == "POOL_CONTEXT_DIRECTION_SPLIT"
    assert "ETHUSDT 4h" in exc.value.reason and "BTCUSDT" not in exc.value.reason
    # And a third occupant on that context is the cap, whichever way it points.
    with pytest.raises(MvpRuntimeError) as exc:
        pool.assert_pool_within_size_cap(
            [wide, _routable("S_ETH", symbol="ETHUSDT", timeframe="4h"),
             _routable("S_ETH2", symbol="ETHUSDT", timeframe="4h")])
    assert exc.value.reason_code == "POOL_CONTEXT_CAP_EXCEEDED"
    assert "ETHUSDT 4h" in exc.value.reason


def test_a_shared_raw_id_does_not_lend_one_lineage_the_other_s_direction():
    """The factory restarts strategy_id at S001 every generation, so a promotion batch
    routinely holds distinct lineages that share a raw id — and `predicted_pool_entries`
    hands them to this gate UNRESOLVED, because the install door derives `{id}-{generation}`
    only later. The direction check used to collect directions into one dict keyed by that
    id, so whichever lineage was read last lent its direction to every context the others
    occupied. Measured 2026-09-10 on the live pool: a BNB 4h pair that was long on both sides
    refused as "S006 SHORT", because a DOGE short in the same batch was also S006."""
    entries = [
        _routable("S6", symbol="BNBUSDT", timeframe="4h"),
        _routable("S7", symbol="BNBUSDT", timeframe="4h"),
        _opposing(_routable("S6", symbol="DOGEUSDT", timeframe="4h")),   # read last
    ]
    pool.assert_pool_within_size_cap(entries)        # BNB agrees; the DOGE short sits alone


def test_a_shared_raw_id_cannot_hide_a_real_split():
    """The dangerous direction of the same collision: a genuine split admitted. ETH 4h holds
    a long S6 against a short S8, but another S8 elsewhere is long and read last — under the
    old keying ETH read as two longs and the split went through. The gate has to read each
    occupant's own spec, so ETH refuses and names the short on the record."""
    entries = [
        _routable("S6", symbol="ETHUSDT", timeframe="4h"),
        _opposing(_routable("S8", symbol="ETHUSDT", timeframe="4h")),
        _routable("S8", symbol="SOLUSDT", timeframe="4h"),               # read last, long
    ]
    with pytest.raises(MvpRuntimeError) as exc:
        pool.assert_pool_within_size_cap(entries)
    assert exc.value.reason_code == "POOL_CONTEXT_DIRECTION_SPLIT"
    assert "ETHUSDT 4h" in exc.value.reason
    assert "S6 LONG" in exc.value.reason and "S8 SHORT" in exc.value.reason
    assert "SOLUSDT" not in exc.value.reason


# --- what the sizing cap and the directional cap do to each other ----------------
#
# An interaction, not a standalone idea, and it exists because a context holds one strategy
# (now up to two, and on slow timeframes only of ONE direction — Thomas 2026-09-02), while a
# spec's `direction` is fixed at promotion
# time — so WHICH directions the book can ever hold became a property of the POOL, where it
# used to be a property of the template library (balanced 16 long / 16 short). Neither cap
# says so on its own, which is the whole reason this is reported.

def _directional_contexts(longs, shorts):
    """One routable entry per context, ``longs + shorts`` distinct contexts, so entry
    composition IS context composition — the shape the arithmetic is stated over."""
    slots = [(sym, tf) for tf in ("4h", "1d", "15m", "1h") for sym in SYMBOLS]
    entries = [_routable(f"S{i:03d}", symbol=sym, timeframe=tf)
               for i, (sym, tf) in enumerate(slots[:longs + shorts])]
    for entry in entries[longs:]:
        entry["strategy_spec"] = {**entry["strategy_spec"], "direction": "short"}
    return entries


GRID_CONTEXTS = 20  # 5 symbols x 4 timeframes — the distinct contexts a full grid routes


def test_a_balanced_pool_can_fill_every_slot_it_routes():
    """The claim the directional cap is sold on — "not a concurrency cap in disguise" — and
    under the per-context cap it is CONDITIONAL, so it needs pinning at the composition where
    it holds rather than being assumed everywhere."""
    shorts = (GRID_CONTEXTS - paper.MAX_DIRECTIONAL_SKEW) // 2
    capacity = pool.routable_directional_capacity(
        _directional_contexts(GRID_CONTEXTS - shorts, shorts)
    )
    assert capacity["routable_contexts"] == GRID_CONTEXTS
    assert capacity["reachable_book"] == GRID_CONTEXTS
    assert capacity["cap_binds"] is False


def test_an_all_one_way_pool_can_fill_only_the_cap_and_says_so():
    """The failure this reports: twenty long strategies fill four of twenty slots. Nothing in
    the size cap or the directional cap surfaces that on its own — the book would simply stop
    growing, and an operator would read it as "no signals"."""
    capacity = pool.routable_directional_capacity(_directional_contexts(GRID_CONTEXTS, 0))
    assert capacity["routable_contexts"] == GRID_CONTEXTS
    assert capacity["reachable_book"] == paper.MAX_DIRECTIONAL_SKEW
    assert capacity["cap_binds"] is True


def test_each_opposing_strategy_buys_back_two_slots():
    """The arithmetic is the cap's own — an opposing position offsets an aligned one — so this
    pins that no second number crept in."""
    for shorts in range(0, 5):
        capacity = pool.routable_directional_capacity(
            _directional_contexts(GRID_CONTEXTS - shorts, shorts)
        )
        expected = min(GRID_CONTEXTS, 2 * shorts + paper.MAX_DIRECTIONAL_SKEW)
        assert capacity["reachable_book"] == expected, shorts


def test_a_shared_fast_context_counts_once_toward_the_book():
    """Under the fast-context cap two same-context strategies can never fill two book slots
    — a position is per context — so the capacity report counts DISTINCT contexts, not
    entries. Counting entries would have overstated the reachable book the day the first
    shared context was promoted."""
    entries = [_routable("S1", symbol="BTCUSDT", timeframe="15m"),
               _routable("S2", symbol="BTCUSDT", timeframe="15m")]
    capacity = pool.routable_directional_capacity(entries)
    assert capacity["routable_contexts"] == 1
    assert capacity["reachable_book"] == 1


def test_a_context_holding_both_directions_is_flexible_and_aligns_with_the_scarce_side():
    """A shared fast context whose two strategies disagree in direction can fill its slot
    either way — so five one-way contexts plus one flexible reach all six, where six one-way
    ones reach only the skew cap."""
    assert pool.routable_directional_capacity(
        _directional_contexts(6, 0))["cap_binds"] is True

    capacity = pool.routable_directional_capacity(_directional_contexts(5, 0) + [
        _routable("S_FLEX_L", symbol="BTCUSDT", timeframe="15m"),
        {**_routable("S_FLEX_S", symbol="BTCUSDT", timeframe="15m"),
         "strategy_spec": _spec_dict(strategy_id="S_FLEX_S", symbol_scope=["BTCUSDT"],
                                     timeframe="15m", direction="short")},
    ])
    assert capacity["flexible_contexts"] == 1 and capacity["routable_contexts"] == 6
    assert capacity["cap_binds"] is False
    assert capacity["reachable_book"] == 6


def test_a_pool_no_larger_than_the_cap_is_never_reported_as_bound():
    """Small pools must not raise the note: four one-way strategies fill four slots, which is
    the cap exactly. A line that fired on every young pool would train the reader to skip it."""
    capacity = pool.routable_directional_capacity(
        _directional_contexts(paper.MAX_DIRECTIONAL_SKEW, 0))
    assert capacity["cap_binds"] is False


def test_the_capacity_report_refuses_nothing():
    """Deliberately a report and not a fifth guard. The directional cap can only DECLINE, so a
    lopsided pool trades less rather than unsafely — and refusing here would forbid assembling
    a pool in any order but alternating, since five longs before the first short is ordinary."""
    lopsided = _directional_contexts(GRID_CONTEXTS, 0)
    assert pool.routable_directional_capacity(lopsided)["cap_binds"] is True
    pool.assert_pool_within_size_cap(lopsided)  # the door still admits it


def test_suspended_entries_are_not_counted():
    """The cap is on the ROUTABLE set. After a retirement the pool file legitimately holds
    far more rows than the cap — 89 rows and 5 routable, the day this landed."""
    entries = [*_at_cap(5),
               *[_routable(f"X{i}", status="SUSPENDED") for i in range(200)]]
    pool.assert_pool_within_size_cap(entries)


def test_a_spec_less_entry_contributes_nothing():
    entries = [_routable("S1", symbol="BTCUSDT", timeframe="15m"),
               {"strategy_id": "S2", "candidate_id": "c2", "status": "PAPER_ACTIVE"}]
    pool.assert_pool_within_size_cap(entries)


def test_warning_and_probation_still_occupy_a_slot():
    """They keep routing, so the cap must see them. Since two aligned 4h lineages may share a
    context, the proof is the direction rule: an OPPOSING lineage on WARNING still counts as
    the occupant it is, and the context is refused as split — not admitted as if S2 had
    already left."""
    entries = [_routable("S1", symbol="BTCUSDT", timeframe="4h"),
               _opposing(_routable("S2", symbol="BTCUSDT", timeframe="4h", status="WARNING"))]
    with pytest.raises(MvpRuntimeError) as exc:
        pool.assert_pool_within_size_cap(entries)
    assert exc.value.reason_code == "POOL_CONTEXT_DIRECTION_SPLIT"
    assert "S2 SHORT" in exc.value.reason


def test_the_context_map_reports_who_competes_for_each_slot():
    entries = [_routable("S1", symbol="BTCUSDT", timeframe="15m"),
               _routable("S2", symbol="BTCUSDT", timeframe="15m"),
               _routable("S3", symbol="ETHUSDT", timeframe="4h"),
               _routable("S4", status="SUSPENDED")]
    assert pool.routable_context_map(entries) == {
        ("BTCUSDT", "15m"): ["S1", "S2"],
        ("ETHUSDT", "4h"): ["S3"],
    }


# --- the routable set the drawdown baseline is checked against (#405) ----------

def test_routable_strategy_ids_is_membership_by_status():
    entries = [
        {"strategy_id": "A", "status": "PAPER_ACTIVE"},
        {"strategy_id": "B", "status": "WARNING"},
        {"strategy_id": "C", "status": "PROBATION"},
        {"strategy_id": "D", "status": "SUSPENDED"},
        {"strategy_id": "E", "status": "RETIRED"},
    ]
    assert pool.routable_strategy_ids({"active_strategies": entries}) == {"A", "B", "C"}


def test_a_spec_less_entry_still_counts_as_routable():
    """Unlike `routable_context_map`, which needs the spec to say WHICH slot it competes for.
    This answers "could this trade again at all", and the safe direction is whichever keeps a
    loss inside the drawdown window — so a spec-less PAPER_ACTIVE row counts as yes."""
    assert pool.routable_strategy_ids(
        {"active_strategies": [{"strategy_id": "A", "status": "PAPER_ACTIVE"}]}
    ) == {"A"}


def test_an_empty_pool_is_an_empty_set_not_a_failure():
    """And the caller — never this function — is what represents "the pool could not be read":
    an empty set releases every exclusion, so the two must not be able to arrive as one value."""
    assert pool.routable_strategy_ids({"active_strategies": []}) == set()
    assert pool.routable_strategy_ids({}) == set()


# --- the effect the approval cannot name --------------------------------------

def _suspend(tmp_path, strategy_id="S1"):
    """The lifecycle suspending ``strategy_id``, as `run_lifecycle` names it: with its lineage and
    the status it judged."""
    from runtime.mvp_runtime.crypto.candidate_identity import lineage_of

    [entry] = [e for e in pool.load_active_pool(tmp_path)["active_strategies"]
               if e.get("strategy_id") == strategy_id]
    pool.update_statuses(
        [{"strategy_id": strategy_id, "previous_status": str(entry.get("status") or "PAPER_ACTIVE"),
          "new_status": "SUSPENDED", "consecutive_failures": 3,
          "created_at_utc": NOW, "reasons": ["metric_suspension"], **lineage_of(entry)}],
        root=tmp_path,
    )


def test_replace_mode_refuses_to_reactivate_a_terminal_member(tmp_path):
    """Replace mode rebuilds every entry with a hardcoded PAPER_ACTIVE, so re-listing the
    incumbents to drop one brings back everything the lifecycle terminated. `BUILD_HISTORY`
    records it simulated against a copy of the real pool on 2026-07-29: 16 reactivated, 57
    lifecycle counters reset. The retirement verb removed the REASON to reach for replace
    mode; it did not close the path.

    The authority is what makes it a refusal rather than a note. `promotion_content_sha256`
    is a function of candidate ids, rule hashes and keep_active — nothing about status — so
    the approval Thomas signs says "install these lineages" and cannot say "and un-suspend
    these". The signature is honest about a smaller effect than the one it authorizes.
    """
    _seed_candidates(tmp_path, _spec_dict())
    run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r", keep_active=False, live_tier="OBSERVATION",
                  root=tmp_path, now=NOW, without_approval=True)
    _suspend(tmp_path)

    with pytest.raises(SystemExit) as exc:
        run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r", keep_active=False, live_tier="OBSERVATION",
                      root=tmp_path, now=NOW, without_approval=True)
    assert "POOL_SILENT_REACTIVATION" in str(exc.value)
    assert "SUSPENDED" in str(exc.value), "the refusal must name what it is coming back from"
    # Refused means refused: the pool is untouched, so the member is still terminal.
    entry = pool.load_active_pool(tmp_path)["active_strategies"][0]
    assert entry["status"] == "SUSPENDED"


def test_the_reactivation_escape_promotes_and_records_who_came_back(tmp_path):
    """The escape exists because reactivation IS a legitimate operator act — `lifecycle`
    calls it the manual re-validation path. What it may not be is a side effect."""
    _seed_candidates(tmp_path, _spec_dict())
    run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r", keep_active=False, live_tier="OBSERVATION",
                  root=tmp_path, now=NOW, without_approval=True)
    _suspend(tmp_path)

    summary = run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                            keep_active=False, live_tier="OBSERVATION", root=tmp_path, now=NOW, without_approval=True,
                            allow_reactivation=True)
    assert summary["reactivation_escape"] is True
    assert summary["reactivated"] == [
        {"candidate_id": summary["promoted_candidate_ids"][0], "strategy_id": "S1",
         "from_status": "SUSPENDED"}
    ]
    assert "silent_reactivation" in summary["reviews_skipped"]
    assert pool.load_active_pool(tmp_path)["active_strategies"][0]["status"] == "PAPER_ACTIVE"


def test_an_ordinary_promotion_records_no_reactivation(tmp_path):
    """The inverse pin. `reactivated` is written whether or not the escape fired, so an empty
    list is a statement rather than an absent key — and the escape reads as unused."""
    _seed_candidates(tmp_path, _spec_dict())
    summary = run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                            keep_active=False, live_tier="OBSERVATION", root=tmp_path, now=NOW, without_approval=True)
    assert summary["reactivated"] == [] and summary["reactivation_escape"] is False
    # Not empty: this promotion really did skip Thomas. That is the point of the field —
    # every escape was already recorded separately, and no record answered "what survived".
    assert summary["reviews_skipped"] == ["thomas_approval"]


def test_reviews_skipped_names_every_review_stepped_around(tmp_path):
    """The composition finding. Each escape is individually defensible and individually
    recorded; what nobody could read off the ledger was the total. With the approval escaped
    too, a promotion can reach the pool having met nothing but pool structural validation and
    read exactly like one that cleared everything."""
    _seed_candidates(tmp_path, _spec_dict(),
                     cost_summary=_stale_summary(taker_fee_bps=2.5, slippage_bps=3.0),
                     bars_replayed=None)
    summary = run_promotion(
        selectors=["S1"], promoted_by="Thomas", reason="r", keep_active=False, live_tier="OBSERVATION",
        root=tmp_path, now=NOW, without_approval=True,
        allow_stale_cost_basis=True, allow_unrecorded_evidence_depth=True,
        allow_duplicates=True, allow_cluster_siblings=True,
        allow_below_entry_bar=True, allow_family_overflow=True,
        allow_oversized_pool=True, allow_quarantined_derivation=True,
    )
    assert summary["reviews_skipped"] == [
        "thomas_approval", "cost_basis", "evidence_depth", "semantic_duplicates",
        "cluster_siblings", "observation_entry_bar", "family_cap",
        "pool_size_cap", "quarantined_derivation",
    ]
    # `silent_reactivation` is absent because nothing was reactivated — an unused escape is
    # not a skipped review, or every promotion would read as having skipped everything.
    assert "silent_reactivation" not in summary["reviews_skipped"]


# --- v3: the hash names who comes back ----------------------------------------

def test_the_reactivation_set_is_material_to_the_hash():
    """The gap v3 closes: the signature was honest about a smaller effect than the one it
    authorized. `BUILD_HISTORY` records replace mode simulated against a copy of the real pool
    on 2026-07-29 — 16 reactivated, 57 lifecycle counters reset — while the hash was a function
    of ids, rules and mode alone."""
    x = dict(artifact_sha256s=["x"])
    base = promotion_content_sha256(["c1"], ["aaa"], keep_active=False, live_tier="LIVE", **x)
    assert promotion_content_sha256(["c1"], ["aaa"], keep_active=False, live_tier="LIVE", reactivated_candidate_ids=[], **x) == base
    assert promotion_content_sha256(["c1"], ["aaa"], keep_active=False, live_tier="LIVE", reactivated_candidate_ids=["c1"], **x) != base
    # Order-insensitive, like the two lists beside it.
    assert promotion_content_sha256(["c1"], ["aaa"], False, "LIVE", ["c2", "c1"], **x) == \
        promotion_content_sha256(["c1"], ["aaa"], False, "LIVE", ["c1", "c2"], **x)


def test_a_promotion_that_reactivates_nothing_hashes_as_stably_as_before():
    """The cost of v3 is zero for every promotion that returns nobody: an empty set is the
    default, so a fresh install's hash does not move with the pool underneath it."""
    empty = promotion_content_sha256(["c1"], ["aaa"], keep_active=False, live_tier="LIVE", reactivated_candidate_ids=[],
                                     artifact_sha256s=["x"])
    assert empty == promotion_content_sha256(["c1"], ["aaa"], keep_active=False, live_tier="LIVE", artifact_sha256s=["x"])


def test_add_mode_reactivates_nothing_by_construction(tmp_path):
    """Add mode keeps the incumbents' own status and the door refuses a candidate already in
    the pool, so nothing can come back — answered without reading the pool at all."""
    _seed_candidates(tmp_path, _spec_dict())
    run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r", keep_active=False, live_tier="OBSERVATION",
                  root=tmp_path, now=NOW, without_approval=True)
    _suspend(tmp_path)
    cid = pool.load_active_pool(tmp_path)["active_strategies"][0]["candidate_id"]
    assert pool.reactivated_candidate_ids([cid], keep_active=True, root=tmp_path) == []
    assert pool.reactivated_candidate_ids([cid], keep_active=False, root=tmp_path) == [cid]


def test_the_hash_input_and_the_door_guard_cannot_drift(tmp_path):
    """Two views of one fact — the hash reads it by candidate id at the ask, the door reads it
    off assembled entries at the install. They must name the same lineages or the approval
    binds one thing and the guard refuses another."""
    _seed_candidates(tmp_path, _spec_dict())
    run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r", keep_active=False, live_tier="OBSERVATION",
                  root=tmp_path, now=NOW, without_approval=True)
    _suspend(tmp_path)
    entry = pool.load_active_pool(tmp_path)["active_strategies"][0]
    cid = entry["candidate_id"]

    by_id = pool.reactivated_candidate_ids([cid], keep_active=False, root=tmp_path)
    by_entry = pool.silent_reactivations(
        [{**entry, "status": "PAPER_ACTIVE"}], root=tmp_path,
    )
    assert by_id == sorted(r["candidate_id"] for r in by_entry) == [cid]


def test_a_suspension_between_the_ask_and_the_execution_invalidates_the_approval(tmp_path):
    """The brittleness, asserted as the intended behaviour rather than avoided.

    Measured 2026-08-09 before making this change: 0 lifecycle status transitions across
    15,157 cycle records over 19 days, against an ask-to-approve window with a median of 1.9
    minutes. So this refusal is rare — and when it fires, the set of members being returned to
    trading really did change, which is exactly what the approval is supposed to bind.
    """
    _seed_candidates(tmp_path, _spec_dict())
    run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r", keep_active=False, live_tier="OBSERVATION",
                  root=tmp_path, now=NOW, without_approval=True)
    cid = pool.load_active_pool(tmp_path)["active_strategies"][0]["candidate_id"]

    artifacts = promotion_mod.candidate_artifacts(pool.resolve_candidates([cid], tmp_path))
    # Asked while the member is live: nothing is being reactivated.
    asked = promotion_content_sha256(
        [cid], [pool.load_active_pool(tmp_path)["active_strategies"][0]["strategy_rule_hash"]],
        False, "LIVE", pool.reactivated_candidate_ids([cid], keep_active=False, root=tmp_path),
        artifact_sha256s=artifacts,
    )
    _suspend(tmp_path)          # the lifecycle moves underneath the pending approval
    now_hash = promotion_content_sha256(
        [cid], [pool.load_active_pool(tmp_path)["active_strategies"][0]["strategy_rule_hash"]],
        False, "LIVE", pool.reactivated_candidate_ids([cid], keep_active=False, root=tmp_path),
        artifact_sha256s=artifacts,
    )
    assert asked != now_hash, "the approval would still verify against a changed effect"


# --- one roster, two doors ------------------------------------------------------
#
# The gates used to live as two hand-synced lists — one in `request_promotion`, one in the
# operator script — and they drifted: the size cap and the reactivation guard ran at the
# install alone, so `--request` won Thomas's approval for promotions `--confirm` then
# refused. `promotion.PROMOTION_GATES` is now the only list either door runs; these tests
# pin the wiring so the next gate cannot land one-sided by accident.


def test_the_ask_and_install_doors_consume_one_gate_roster():
    roster = [g.escape_flag for g in PROMOTION_GATES if g.escape_flag]
    assert len(roster) == len(set(roster)), "an escape flag names exactly one gate"
    escapable = set(roster)
    ask = {name for name in inspect.signature(request_promotion).parameters
           if name.startswith("allow_")}
    install = {name for name in inspect.signature(run_promotion).parameters
               if name.startswith("allow_")}
    assert ask == escapable, "the ask door must expose exactly the roster's escapes"
    assert install == escapable, "the install door must expose exactly the roster's escapes"


def test_the_execution_stage_gate_has_no_escape_and_cannot_grow_one():
    """PR1c: every other gate on the roster is an operator call with a flag. The stage gate is
    the ladder, and the ladder is Thomas's — a flag here would be the escape PR1c retired, one
    door over. PR3c-2 adds the one other: a rule the pool routes is never installed twice (Thomas
    decision 40). The empty escape name matches no key in `escapes`, so both always run."""
    unescapable = sorted(g.check.__name__ for g in PROMOTION_GATES if not g.escape_flag)
    assert unescapable == ["_gate_execution_stage", "_gate_rule_not_routed"]
    source = Path(promote_door.__file__).read_text(encoding="utf-8")
    assert "--allow-unstaged" not in source and "allow_execution_stage" not in source
    assert "allow_routed" not in source and "allow_same_rule" not in source


def test_every_roster_escape_is_reachable_from_the_operator_argv():
    """A gate whose escape the argv cannot spell is a gate nobody can deliberately step
    around — its refusal would read as a dead end. The spelling is pinned against the
    script's own source because argparse builds the parser inside `main`."""
    source = Path(promote_door.__file__).read_text(encoding="utf-8")
    argv_flags = {m.replace("-", "_") for m in re.findall(r'"--(allow-[a-z-]+)"', source)}
    # The stage gate is deliberately un-escapable (its flag name is empty), so it is not here.
    assert argv_flags == {g.escape_flag for g in PROMOTION_GATES if g.escape_flag}


def _seed_second_lineage(tmp_path, spec_dict, *, window_sha, expectancy=0.6):
    """A second lineage the duplicate/cluster gates cannot mistake for the first: its own
    evidence window and its own expectancy. `_seed_candidates` stamps every row with the
    same `sha256:test` window, and the cluster bucket keys on exactly that."""
    spec = StrategySpec.from_dict(spec_dict)
    pool.append_candidates([{
        "strategy_id": spec.strategy_id,
        "strategy_rule_hash": spec.strategy_rule_hash,
        "generation_id": "GEN-002",
        "status": "BACKTESTED",
        "champion_score": 0.5,
        "strategy_spec": spec.to_dict(),
        "backtest_evidence": {
            "closed_count": 60, "expectancy": expectancy,
            "robustness": {"verdict": "PROVISIONAL", "holdout_status": "CONFIRMED"},
            "bars_replayed": _current_bars_replayed(spec),
            "cost_summary": _current_cost_summary(),
        },
        "evidence_input_sha256": window_sha,
        "provenance": "mvp_factory",
    }], root=tmp_path)
    return spec


def test_the_ask_refuses_a_promotion_the_size_cap_would_refuse(tmp_path):
    """The recorded operational trap, closed: `--request` approved a promotion whose install
    the size cap then refused, spending Thomas's answer on nothing. Same deliberate shape as
    `test_the_ask_refuses_stale_evidence_too`: no `requires_local_core`, because the refusal
    lands in the gate roster before `bind_task_to_core` reaches for a Core."""
    _seed_candidates(tmp_path, _spec_dict())
    run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r", keep_active=False,
                  live_tier="OBSERVATION", root=tmp_path, now=NOW, without_approval=True)
    # Opposing the incumbent, because two aligned 1d lineages may now share the context
    # (Thomas 2026-09-02) — the split rule is the cap-family refusal an add-mode ask on this
    # fixture reaches, and it is the same roster the install runs.
    _seed_second_lineage(
        tmp_path,
        _spec_dict(strategy_id="S2", direction="short",
                   entry_rules={"operator": "AND", "conditions": [
                       {"feature": "adx", "comparison": ">=", "value": 30.0}]}),
        window_sha="sha256:test-second-window",
    )
    with pytest.raises(ApprovalBlocked) as exc:
        request_promotion(["S2"], keep_active=True, live_tier="OBSERVATION", now=NOW,
                          candidates_root=tmp_path)
    assert exc.value.reason_code == "POOL_CONTEXT_DIRECTION_SPLIT"


def test_the_ask_refuses_a_reactivation_the_install_would_refuse(tmp_path):
    """Same trap, other gate: a replace-mode ask re-listing a suspended member sailed
    through `--request` and refused only at `--confirm`. No `requires_local_core`, same
    reason as above."""
    _seed_candidates(tmp_path, _spec_dict())
    run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r", keep_active=False,
                  live_tier="OBSERVATION", root=tmp_path, now=NOW, without_approval=True)
    _suspend(tmp_path)
    with pytest.raises(ApprovalBlocked) as exc:
        request_promotion(["S1"], keep_active=False, live_tier="OBSERVATION", now=NOW,
                          candidates_root=tmp_path)
    assert exc.value.reason_code == "POOL_SILENT_REACTIVATION"
    assert "SUSPENDED" in exc.value.reason, "the ask names what would come back, like the install"


@requires_local_core
def test_the_ask_honours_the_reactivation_escape(tmp_path):
    """The escape releases the ask exactly as it releases the install, so a deliberate
    reactivation is still askable rather than unrequestable."""
    _seed_candidates(tmp_path, _spec_dict())
    run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r", keep_active=False,
                  live_tier="OBSERVATION", root=tmp_path, now=NOW, without_approval=True)
    _suspend(tmp_path)
    prepared = request_promotion(["S1"], keep_active=False, live_tier="OBSERVATION", now=NOW,
                                 candidates_root=tmp_path, allow_reactivation=True)
    assert prepared["approval_request"]["status"] == "PENDING"


@requires_local_core
def test_the_ask_honours_the_oversized_pool_escape(tmp_path):
    _seed_candidates(tmp_path, _spec_dict())
    run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r", keep_active=False,
                  live_tier="OBSERVATION", root=tmp_path, now=NOW, without_approval=True)
    _seed_second_lineage(
        tmp_path,
        _spec_dict(strategy_id="S2", entry_rules={"operator": "AND", "conditions": [
            {"feature": "adx", "comparison": ">=", "value": 30.0}]}),
        window_sha="sha256:test-second-window",
    )
    prepared = request_promotion(["S2"], keep_active=True, live_tier="OBSERVATION", now=NOW,
                                 candidates_root=tmp_path, allow_oversized_pool=True)
    assert prepared["approval_request"]["status"] == "PENDING"


# --- arming LIVE is Thomas's, every time (PR1c, 2026-09-16) ---------------------
#
# The execution-authority audit (2026-09-15) found three ways a strategy reached the LIVE tier
# without Thomas seeing what he was answering: `--without-approval` skipped the record entirely,
# `--allow-unconfirmed-holdout` armed on evidence never confirmed on unseen data, and the ask he
# did see described a LIVE promotion as a paper-stage change with "no order capability".

def _stage(monkeypatch, *, stage="READ_ONLY", valid=False, reason="EXECUTION_STAGE_RECORD_MISSING"):
    from runtime.mvp_runtime.crypto import execution_stage as es

    status = es.StageStatus(stage=stage, valid=valid, reason_code=reason,
                            recorded_stage=stage if valid else None)
    for module in (promotion_mod, promote_door):
        monkeypatch.setattr(module, "resolve_execution_stage", lambda root=None, **kw: status)
    return status


def test_arming_live_needs_the_execution_stage_that_admits_it(tmp_path, monkeypatch):
    """The ladder is the machine's one answer to "may this open a real position", so an arming
    promotion cannot outrun it — and a demotion (which needs no approval) takes the permission
    back at the next install."""
    _seed_candidates(tmp_path, _spec_dict())
    _stage(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r", keep_active=False,
                      live_tier="LIVE", root=tmp_path, now=NOW, without_approval=True)
    assert "LIVE_ARM_ESCAPE_RETIRED" in str(exc.value)

    _stage(monkeypatch, stage="PAPER", valid=True, reason=None)
    with pytest.raises(ApprovalBlocked) as blocked:
        promotion_mod.run_promotion_gates(
            [], keep_active=False, live_tier="LIVE", entries=[], store_root=tmp_path,
            escapes={}, execution_stage=_stage(monkeypatch, stage="PAPER", valid=True, reason=None),
        )
    assert blocked.value.reason_code == "EXECUTION_STAGE_TOO_LOW_TO_ARM"
    assert "LIVE_AUTONOMOUS" in blocked.value.reason and "OBSERVATION" in blocked.value.reason
    assert pool.load_active_pool(tmp_path) == {"active_strategies": []}


def test_an_observation_promotion_never_reads_the_stage(tmp_path, monkeypatch):
    """The tier that cannot spend money is not gated by the ladder: a machine at READ_ONLY still
    papers, and that is the path a demoted machine keeps using."""
    _seed_candidates(tmp_path, _spec_dict())
    _stage(monkeypatch)
    summary = run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r", keep_active=False,
                            live_tier="OBSERVATION", root=tmp_path, now=NOW, without_approval=True)
    assert summary["promoted_candidate_ids"]
    entry = pool.load_active_pool(tmp_path)["active_strategies"][0]
    assert pool.entry_live_tier(entry) == "OBSERVATION"


def test_the_live_escapes_are_retired_and_observation_keeps_them(tmp_path, monkeypatch):
    _seed_candidates(tmp_path, _spec_dict())
    _stage(monkeypatch, stage="LIVE_AUTONOMOUS", valid=True, reason=None)
    with pytest.raises(ApprovalBlocked) as blocked:
        promotion_mod.run_promotion_gates(
            [], keep_active=False, live_tier="LIVE", entries=[], store_root=tmp_path,
            escapes={"allow_unconfirmed_holdout": True},
            execution_stage=_stage(monkeypatch, stage="LIVE_AUTONOMOUS", valid=True, reason=None),
        )
    assert blocked.value.reason_code == "LIVE_ARM_ESCAPE_RETIRED"
    # OBSERVATION keeps the flag: nothing it installs can open a position.
    summary = run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r", keep_active=False,
                            live_tier="OBSERVATION", root=tmp_path, now=NOW, without_approval=True,
                            allow_unconfirmed_holdout=True)
    assert summary["promoted_candidate_ids"]


def test_the_install_door_resolves_the_stage_itself_rather_than_trusting_the_ask(tmp_path):
    """A demotion needs no approval and applies at once, so an approval won at LIVE_AUTONOMOUS
    must not install on a machine that has since stepped down."""
    source = Path(promote_door.__file__).read_text(encoding="utf-8")
    assert "execution_stage=resolve_execution_stage(root, now=now)" in source


def test_the_ask_for_a_live_promotion_says_it_arms_real_money(monkeypatch):
    """Audit FO-5b: every LIVE promotion was announced as a paper-stage change with "no order
    capability, no live/testnet effect" — in the one text Thomas reads before answering."""
    import unittest.mock as mock

    from runtime.mvp_runtime import approval as approval_mod
    from runtime.mvp_runtime import permission

    captured = {}

    def fake_build(bound, **kw):
        captured.update(kw)
        return {"ok": True}

    common = dict(candidate_ids=["cand_a"], strategy_ids=["S1"], rule_hashes=["sha256:r"],
                  artifact_sha256s=["sha256:x"], keep_active=False, content_sha256="sha256:c", now=NOW)
    with mock.patch.object(permission, "build_permission_decision", fake_build):
        permission.build_strategy_promotion_permission_decision({}, live_tier="OBSERVATION", **common)
        assert captured["action"].risk_level == "ORANGE"
        assert captured["action"].target_ref == permission.STRATEGY_POOL_PAPER_TARGET_REF
        assert "cannot open a real position" in captured["action"].risk_reason

        permission.build_strategy_promotion_permission_decision({}, live_tier="LIVE", **common)
        assert captured["action"].risk_level == "RED"
        assert captured["action"].target_ref == permission.STRATEGY_POOL_LIVE_TARGET_REF
        assert "ARMS THESE STRATEGIES FOR REAL MONEY" in captured["action"].risk_reason
        assert "LIVE_AUTONOMOUS" in captured["action"].risk_reason        # what still has to hold
        assert "no order capability" not in captured["action"].constraint
        assert captured["action"].normalized_parameters["live_tier"] == "LIVE"
        # Signed as pairs (PR3a): which artifact is whose.
        assert captured["action"].normalized_parameters["artifacts"] == [["cand_a", "sha256:x"]]

    ask = {
        "approval_id": "approval_x", "task_id": "task_x",
        "validity": {"expires_at": "2026-09-17T00:00:00Z"}, "action_fingerprint": "sha256:x",
        "approved_action_snapshot": {"action_type": "crypto.strategy_pool.promotion",
                                     "permission_scope": "RUNTIME_GOVERNANCE",
                                     "target_ref": permission.STRATEGY_POOL_LIVE_TARGET_REF},
    }
    text = approval_mod.format_request(ask)
    assert "실주문이 다시 나갈 수 있게 되므로" in text          # priced as arming, not "없음"
    assert "무장 해제" in text and "승인 없이 즉시" in text     # and the reversal named
    paper = {**ask, "approved_action_snapshot": {**ask["approved_action_snapshot"],
                                                 "target_ref": permission.STRATEGY_POOL_PAPER_TARGET_REF}}
    assert "예상 비용: 없음" in approval_mod.format_request(paper)


def test_the_tier_and_the_stage_are_arguments_a_caller_cannot_omit():
    """Both are fail-closed facts: a default would let a future caller arm by omission — the ask
    would print the paper text (FO-5b again) while the content hash still bound LIVE, or a door
    would install without the ladder ever being read."""
    from runtime.mvp_runtime import permission

    tier = inspect.signature(permission.build_strategy_promotion_permission_decision).parameters["live_tier"]
    stage = inspect.signature(promotion_mod.run_promotion_gates).parameters["execution_stage"]
    for name, param in (("live_tier", tier), ("execution_stage", stage)):
        assert param.default is inspect.Parameter.empty, f"{name} must not have a default"
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, name


def test_a_gate_that_names_no_escape_cannot_be_escaped_by_one(tmp_path, monkeypatch):
    """Structural, not conventional: passing the empty flag must not wave the stage gate through."""
    with pytest.raises(ApprovalBlocked) as blocked:
        promotion_mod.run_promotion_gates(
            [], keep_active=False, live_tier="LIVE", entries=[], store_root=tmp_path,
            escapes={"": True},
            execution_stage=_stage(monkeypatch, stage="PAPER", valid=True, reason=None),
        )
    assert blocked.value.reason_code == "EXECUTION_STAGE_TOO_LOW_TO_ARM"


@pytest.mark.parametrize("artifacts", [[], ["sha256:x", "sha256:y"], [""], [None], "sha256:x"],
                         ids=["none", "too-many", "empty", "not-a-string", "a-string"])
def test_the_ask_names_the_artifact_of_every_candidate(artifacts):
    """PR3a: the signed parameters pair each candidate with the artifact it installs as, and the
    order-time check of a LIVE arm reads those pairs. An ask that cannot pair every candidate is
    refused before anything is signed."""
    from runtime.mvp_runtime import permission
    from runtime.mvp_runtime.errors import PlannerBlocked

    with pytest.raises(PlannerBlocked) as exc:
        permission.build_strategy_promotion_permission_decision(
            {}, candidate_ids=["cand_a"], strategy_ids=["S1"], rule_hashes=["sha256:r"],
            artifact_sha256s=artifacts, keep_active=False, live_tier="LIVE",
            content_sha256="sha256:c", now=NOW)
    assert exc.value.reason_code == "INVALID_PROMOTION"
    assert "artifact" in exc.value.reason


# --- the arming approval at order time (PR2c-2b) ----------------------------------------------

_THOMAS = dict(approved_by="Thomas", method="telegram_private_control_channel",
               verification_ref="telegram:private_chat:test:msg-1")


@requires_local_core
def test_an_arm_the_door_installs_under_thomas_s_answer_is_verified_at_order_time(tmp_path):
    """End to end: the approval the ask builds and Thomas answers, and the entry the promotion door
    installs under it, are exactly what the order-time verifier accepts. The verifier takes no
    clock: the ask's own expiry does not end the arm."""
    from runtime.mvp_runtime import approval as approval_mod

    _seed_candidates(tmp_path, _spec_dict())
    asked_at = timeutil.utc_now_iso()
    prepared = request_promotion(["S1"], keep_active=False, live_tier="LIVE", now=asked_at,
                                 candidates_root=tmp_path)
    approved = approval_mod.record_decision(
        prepared["approval_request"], prepared["permission_decision"], granted=True,
        verification=approval_mod.Verification(**_THOMAS), reason="arm it",
        now=timeutil.plus_seconds(asked_at, 30),
    )
    store = ApprovalStore(tmp_path / APPROVAL_STORE_REL)
    store.append([approved])
    installed_at = timeutil.plus_seconds(asked_at, 60)
    run_promotion(selectors=["S1"], promoted_by="Thomas", reason="arm", keep_active=False,
                  live_tier="LIVE", root=tmp_path, now=installed_at, approval_id=approved["approval_id"])

    installed = pool.load_active_pool(tmp_path)
    [(strategy_id, armed)] = pool.live_arm_entries(installed).items()
    assert armed["approval_id"] == approved["approval_id"] and armed["promoted_at"] == installed_at
    # The entry the door writes is sound: it trades its labelled rule and carries no disarm trace.
    assert pool.live_arm_unsound(armed) is None
    assert pool.live_arm_approvals(installed) == {strategy_id: approved["approval_id"]}
    assert promotion_mod.live_arm_problem(
        store.get(armed["approval_id"]), approval_id=armed["approval_id"],
        candidate_id=armed["candidate_id"], strategy_rule_hash=armed["strategy_rule_hash"],
        strategy_artifact_sha256=armed["strategy_artifact_sha256"], promoted_at=armed["promoted_at"],
    ) is None
    # The artifact the door installed is the one Thomas's answer pairs with the candidate (PR3a).
    signed = store.get(armed["approval_id"])["approved_action_snapshot"]["normalized_parameters"]
    assert signed["artifacts"] == [[armed["candidate_id"], armed["strategy_artifact_sha256"]]]


@requires_local_core
@pytest.mark.parametrize("install_after_answer,installed", [(-1, False), (0, True)],
                         ids=["before-the-answer", "at-the-answer"])
def test_the_door_installs_no_arm_the_gate_would_refuse(tmp_path, install_after_answer, installed):
    """Review of #887: the door took its clock before it read the approval, so an answer recorded in
    between installed an arm that no order could ever leave under. It now refuses what the gate
    would refuse."""
    from runtime.mvp_runtime import approval as approval_mod

    _seed_candidates(tmp_path, _spec_dict())
    asked_at = timeutil.utc_now_iso()
    answered_at = timeutil.plus_seconds(asked_at, 5)
    prepared = request_promotion(["S1"], keep_active=False, live_tier="LIVE", now=asked_at,
                                 candidates_root=tmp_path)
    approved = approval_mod.record_decision(
        prepared["approval_request"], prepared["permission_decision"], granted=True,
        verification=approval_mod.Verification(**_THOMAS), reason="arm it", now=answered_at,
    )
    ApprovalStore(tmp_path / APPROVAL_STORE_REL).append([approved])
    promote = lambda: run_promotion(  # noqa: E731
        selectors=["S1"], promoted_by="Thomas", reason="arm", keep_active=False, live_tier="LIVE",
        root=tmp_path, now=timeutil.plus_seconds(answered_at, install_after_answer),
        approval_id=approved["approval_id"])
    if installed:
        promote()
        assert pool.live_arm_approvals(pool.load_active_pool(tmp_path))
    else:
        with pytest.raises(SystemExit) as exc:
            promote()
        assert "APPROVAL_OUTSIDE_ARM_WINDOW" in str(exc.value)
        assert pool.load_active_pool(tmp_path) == {"active_strategies": []}


@pytest.mark.parametrize("approval,live_tier,refused", [
    (dict(decision={"decided_at": timeutil.plus_seconds(NOW, 1)}), "LIVE", True),
    (dict(decision={}), "LIVE", True),
    (dict(signed_expiry=NOW), "LIVE", True),                    # the signed window already ended
    (dict(signed_expiry=None), "LIVE", True),
    (dict(decision={}, signed_expiry=None), "OBSERVATION", False),   # a paper change has no window
    ({}, "LIVE", False),
], ids=["answered-later", "no-answer-time", "signed-expiry-passed", "no-signed-expiry",
        "observation-ignores-the-window", "inside"])
def test_verification_holds_a_live_install_to_the_arm_window(tmp_path, approval, live_tier, refused):
    _seed_candidates(tmp_path, _spec_dict())
    record = pool.resolve_candidates(["S1"], tmp_path)[0]
    content = promotion_content_sha256([record["candidate_id"]], [record["strategy_rule_hash"]],
                                       keep_active=False, live_tier=live_tier,
                                       artifact_sha256s=promotion_mod.candidate_artifacts([record]))
    fake = _fake_approval(tmp_path, content=content)
    change = dict(approval)
    if "decision" in change:
        fake["decision"] = change.pop("decision")
    if "signed_expiry" in change:
        fake["approved_action_snapshot"]["expires_at"] = change.pop("signed_expiry")
    verify = lambda: verify_promotion_approval(  # noqa: E731
        fake, selectors=["S1"], keep_active=False, live_tier=live_tier, root=tmp_path, now=NOW)
    if refused:
        with pytest.raises(ApprovalBlocked) as exc:
            verify()
        assert exc.value.reason_code == "APPROVAL_OUTSIDE_ARM_WINDOW"
    else:
        assert verify()["approval_id"] == "approval_test"


def _arm_problem(approval, **entry):
    from tests._helpers import live_arm_approval

    from tests._helpers import ARM_TEST_ARTIFACT

    base = live_arm_approval()
    facts = {"approval_id": base["approval_id"], "candidate_id": "cand_1",
             "strategy_rule_hash": "deadbeef", "strategy_artifact_sha256": ARM_TEST_ARTIFACT,
             "promoted_at": "2026-07-27T23:55:00Z", **entry}
    return promotion_mod.live_arm_problem(approval, **facts)


def _rebuilt(snapshot, **fields):
    """A record for ``snapshot`` whose fingerprint and id are its own: only what it approves differs."""
    from lib.action_fingerprint import compute_action_fingerprint
    from runtime.read_only_kernel import integrity
    from tests._helpers import live_arm_approval

    fingerprint = compute_action_fingerprint(snapshot)
    return {**live_arm_approval(), "approved_action_snapshot": snapshot, "action_fingerprint": fingerprint,
            "approval_id": integrity.short_id("approval", {"action_fingerprint": fingerprint}), **fields}


def _snapshot(**changes):
    from tests._helpers import live_arm_approval

    snapshot = dict(live_arm_approval()["approved_action_snapshot"])
    content = {**snapshot["normalized_parameters"], **changes.pop("content", {})}
    return {**snapshot, "normalized_parameters": content, **changes}


def test_the_approval_that_armed_the_entry_backs_it():
    from tests._helpers import live_arm_approval

    assert _arm_problem(live_arm_approval()) is None
    # The window's bounds: installed the moment Thomas answered, and not at the moment it expired.
    assert _arm_problem(live_arm_approval(), promoted_at="2026-07-27T23:50:00Z") is None
    assert _arm_problem(live_arm_approval(), promoted_at="2026-07-28T00:05:00Z") == \
        promotion_mod.LIVE_ARM_INSTALLED_OUTSIDE_APPROVAL


def _approval_cases():
    from tests._helpers import ARM_TEST_ARTIFACT, live_arm_approval

    unpaired = live_arm_approval(artifacts=None)
    garbled_pairs = _rebuilt(_snapshot(content={"artifacts": f"cand_1 {ARM_TEST_ARTIFACT}"}))
    other = "sha256:" + "b" * 64
    swapped = live_arm_approval(("cand_1", "cand_2"), ("deadbeef", "cafef00d"),
                                artifacts=(other, ARM_TEST_ARTIFACT))
    both = live_arm_approval(("cand_1", "cand_2"), ("deadbeef", "cafef00d"),
                             artifacts=(ARM_TEST_ARTIFACT, other))
    arm = live_arm_approval()
    unsigned = _rebuilt({k: v for k, v in _snapshot().items() if k != "expires_at"})
    by_string = {name: _rebuilt(_snapshot(content={field: value}))
                 for name, field, value in (("ids", "candidate_ids", "cand_1"),
                                            ("hashes", "rule_hashes", "deadbeef"),
                                            # One character: iterating the string yields the id itself.
                                            ("one-letter-id", "candidate_ids", "c"))}
    return {
        "none": (None, {}, promotion_mod.LIVE_ARM_APPROVAL_MISSING),
        "not-a-record": (["approval"], {}, promotion_mod.LIVE_ARM_APPROVAL_MISSING),
        **{f"status-{s.lower()}": ({**arm, "status": s}, {}, promotion_mod.LIVE_ARM_APPROVAL_NOT_APPROVED)
           for s in ("PENDING", "REJECTED", "EXPIRED", "CONSUMED")},
        "no-approver": ({**arm, "approver": None}, {}, promotion_mod.LIVE_ARM_APPROVER_UNVERIFIED),
        "approver-a-name": ({**arm, "approver": "Thomas"}, {}, promotion_mod.LIVE_ARM_APPROVER_UNVERIFIED),
        **{f"approver-{k}": ({**arm, "approver": {**arm["approver"], k: "other"}}, {},
                             promotion_mod.LIVE_ARM_APPROVER_UNVERIFIED)
           for k in ("approved_by", "verification_status", "identity_verification_method")},
        "another-action": ({**arm, "approved_action_snapshot": _snapshot(action_type="memory.promotion")}, {},
                           promotion_mod.LIVE_ARM_APPROVAL_NOT_AN_ARM),
        "paper-target": ({**arm, "approved_action_snapshot": _snapshot(target_ref="active_strategy_pool:paper")},
                         {}, promotion_mod.LIVE_ARM_APPROVAL_NOT_AN_ARM),
        "observation-tier": ({**arm, "approved_action_snapshot": _snapshot(content={"live_tier": "OBSERVATION"})},
                             {}, promotion_mod.LIVE_ARM_APPROVAL_NOT_AN_ARM),
        "no-content": ({**arm, "approved_action_snapshot": {**_snapshot(), "normalized_parameters": None}}, {},
                       promotion_mod.LIVE_ARM_APPROVAL_NOT_AN_ARM),
        "no-snapshot": ({**arm, "approved_action_snapshot": None}, {},
                        promotion_mod.LIVE_ARM_APPROVAL_NOT_AN_ARM),
        "snapshot-edited": ({**arm, "approved_action_snapshot": _snapshot(content={"candidate_ids": ["cand_1", "cand_9"]})},
                            {}, promotion_mod.LIVE_ARM_APPROVAL_ALTERED),
        "fingerprint-edited": ({**arm, "action_fingerprint": "sha256:" + "0" * 64}, {},
                               promotion_mod.LIVE_ARM_APPROVAL_ALTERED),
        "unfingerprintable": ({**arm, "approved_action_snapshot": _snapshot(content={"weight": 0.5})}, {},
                              promotion_mod.LIVE_ARM_APPROVAL_ALTERED),
        "stored-under-another-id": ({**arm, "approval_id": "approval_other"}, {},
                                    promotion_mod.LIVE_ARM_APPROVAL_ALTERED),
        "entry-names-another-id": (arm, {"approval_id": "approval_other"}, promotion_mod.LIVE_ARM_APPROVAL_ALTERED),
        "id-not-derived": ({**arm, "approval_id": "approval_other"}, {"approval_id": "approval_other"},
                           promotion_mod.LIVE_ARM_APPROVAL_ALTERED),
        "another-candidate": (arm, {"candidate_id": "cand_2"}, promotion_mod.LIVE_ARM_APPROVAL_OTHER_CANDIDATE),
        "another-rule": (arm, {"strategy_rule_hash": "cafef00d"}, promotion_mod.LIVE_ARM_APPROVAL_OTHER_CANDIDATE),
        # A string holds its own id as a substring; it still lists no id.
        **{f"{name}-as-a-string": (record, {"approval_id": record["approval_id"],
                                            **({"candidate_id": "c"} if name == "one-letter-id" else {})},
                                   promotion_mod.LIVE_ARM_APPROVAL_OTHER_CANDIDATE)
           for name, record in by_string.items()},
        "rebuilt-record": (_rebuilt(_snapshot()), {"approval_id": _rebuilt(_snapshot())["approval_id"]}, None),
        "before-the-answer": (arm, {"promoted_at": "2026-07-27T23:49:59Z"},
                              promotion_mod.LIVE_ARM_INSTALLED_OUTSIDE_APPROVAL),
        "after-the-expiry": (arm, {"promoted_at": "2026-07-28T00:05:01Z"},
                             promotion_mod.LIVE_ARM_INSTALLED_OUTSIDE_APPROVAL),
        "no-install-time": (arm, {"promoted_at": None}, promotion_mod.LIVE_ARM_INSTALLED_OUTSIDE_APPROVAL),
        "naive-install-time": (arm, {"promoted_at": "2026-07-27T23:55:00"},
                               promotion_mod.LIVE_ARM_INSTALLED_OUTSIDE_APPROVAL),
        "garbled-install-time": (arm, {"promoted_at": "yesterday"}, promotion_mod.LIVE_ARM_INSTALLED_OUTSIDE_APPROVAL),
        "no-answer-time": ({**arm, "decision": {"decision_reason": "x"}}, {},
                           promotion_mod.LIVE_ARM_INSTALLED_OUTSIDE_APPROVAL),
        "no-expiry": ({**arm, "validity": None}, {}, promotion_mod.LIVE_ARM_INSTALLED_OUTSIDE_APPROVAL),
        # Review of #887: the unsigned validity cannot stretch the window the snapshot signed.
        "validity-stretched": ({**arm, "validity": {**arm["validity"], "expires_at": "2026-07-28T01:00:00Z"}},
                               {"promoted_at": "2026-07-28T00:30:00Z"},
                               promotion_mod.LIVE_ARM_INSTALLED_OUTSIDE_APPROVAL),
        "no-signed-expiry": (unsigned, {"approval_id": unsigned["approval_id"]},
                             promotion_mod.LIVE_ARM_INSTALLED_OUTSIDE_APPROVAL),
        "pending-and-another-candidate": ({**arm, "status": "PENDING"}, {"candidate_id": "cand_2"},
                                          promotion_mod.LIVE_ARM_APPROVAL_NOT_APPROVED),
        # PR3a, decision 33: the approval pairs the entry's artifact with the entry's candidate.
        "asked-before-v5": (unpaired, {"approval_id": unpaired["approval_id"]},
                            promotion_mod.LIVE_ARM_APPROVAL_UNBOUND),
        "pairs-as-a-string": (garbled_pairs, {"approval_id": garbled_pairs["approval_id"]},
                              promotion_mod.LIVE_ARM_APPROVAL_UNBOUND),
        "another-artifact": (arm, {"strategy_artifact_sha256": "sha256:" + "e" * 64},
                             promotion_mod.LIVE_ARM_APPROVAL_OTHER_ARTIFACT),
        "no-artifact": (arm, {"strategy_artifact_sha256": None}, promotion_mod.LIVE_ARM_APPROVAL_OTHER_ARTIFACT),
        # Both candidates and both artifacts are in it, the way v4 listed ids and rules — not as pairs.
        "pairs-swapped": (swapped, {"approval_id": swapped["approval_id"]},
                          promotion_mod.LIVE_ARM_APPROVAL_OTHER_ARTIFACT),
        "paired-with-this-artifact": (both, {"approval_id": both["approval_id"]}, None),
    }


_CASES = _approval_cases()


@pytest.mark.parametrize("approval,entry,problem", list(_CASES.values()), ids=list(_CASES))
def test_an_approval_that_does_not_back_the_arm_is_named(approval, entry, problem):
    assert _arm_problem(approval, **entry) == problem
