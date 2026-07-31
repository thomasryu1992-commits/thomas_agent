"""C8b promotion-approval tests — the ask binds the exact promotion; verify never spends.

Under test: the content hash changes on any material change (ids, rules, add-vs-
replace); the R9 request builds a schema-valid RUNTIME_GOVERNANCE decision + PENDING
approval; verification fails closed on pending/expired/wrong-action/content-mismatch
and on a candidate whose rules changed after approval; the operator door requires an
approval or the explicit escape; and the robustness scorer ranks candidates with the
source's veto semantics."""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import cost, paper, pool
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

# Sentinel: `cost_summary=None` means "seed a candidate with no recorded cost model", which is
# a case under test, so it cannot double as "caller said nothing".
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


def _seed_candidates(tmp_path, *specs, generation_id="GEN-001", cost_summary=_MISSING):
    records = []
    for spec_dict in specs:
        spec = StrategySpec.from_dict(spec_dict)
        evidence = {"closed_count": 20, "expectancy": 0.5,
                    "bars_replayed": _current_bars_replayed(spec)}
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
                      keep_active=False, root=tmp_path, now=NOW, without_approval=True)
    assert "CANDIDATE_COST_BASIS_STALE" in str(exc.value)
    assert pool.load_active_pool(tmp_path) == {"active_strategies": []}, "nothing installed"


def test_promotion_refuses_evidence_with_no_recorded_cost_model(tmp_path):
    """Worse than stale: it cannot even say which way its numbers err."""
    _seed_candidates(tmp_path, _spec_dict(), cost_summary=None)
    with pytest.raises(SystemExit) as exc:
        run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                      keep_active=False, root=tmp_path, now=NOW, without_approval=True)
    assert "CANDIDATE_COST_BASIS_STALE" in str(exc.value)


def test_the_stale_basis_escape_promotes_and_is_recorded(tmp_path):
    """An escape that leaves no trace is an escape nobody can audit. The flag AND the basis
    each promoted candidate stood on both ride onto the ledger summary, because a pool entry
    outlives the argv that installed it."""
    _seed_candidates(tmp_path, _spec_dict(),
                     cost_summary=_stale_summary(taker_fee_bps=2.5, slippage_bps=3.0))
    summary = run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r",
                            keep_active=False, root=tmp_path, now=NOW, without_approval=True,
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
                            keep_active=False, root=tmp_path, now=NOW, without_approval=True)
    assert summary["stale_cost_basis_escape"] is False
    assert summary["cost_bases"] == [pool.current_cost_basis()]


def test_the_ask_refuses_stale_evidence_too(tmp_path):
    """Checked at the ASK, not only the install. An approval Thomas answers for a promotion
    the next step was always going to refuse spends his attention on nothing.

    No `requires_local_core`, deliberately: the refusal lands in `_resolve_candidates`, before
    `build_task`/`bind_task_to_core` ever reach for a Core. A test gated on one would have been
    skipped on exactly the machines where the gate is cheapest to break."""
    _seed_candidates(tmp_path, _spec_dict(),
                     cost_summary=_stale_summary(taker_fee_bps=2.5, slippage_bps=3.0))
    with pytest.raises(ApprovalBlocked) as exc:
        request_promotion(["S1"], keep_active=False, now=NOW, candidates_root=tmp_path)
    assert exc.value.reason_code == "CANDIDATE_COST_BASIS_STALE"


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
                            keep_active=False, root=tmp_path, now=NOW, without_approval=True)
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
        run_promotion(selectors=cids, promoted_by="Thomas", reason="r",
                      keep_active=False, root=tmp_path, now=NOW, without_approval=True)
    assert "cannot assign a unique strategy_id" in str(exc.value)
    assert pool.load_active_pool(tmp_path) == {"active_strategies": []}


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


def _one_per_context(n):
    """``n`` routable entries, each alone in its own context — the shape the cap allows."""
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "BNBUSDT"]
    timeframes = ["15m", "1h", "4h", "1d"]
    out = []
    for i in range(n):
        sym, tf = symbols[i % len(symbols)], timeframes[i // len(symbols)]
        out.append(_routable(f"S{i:03d}", symbol=sym, timeframe=tf))
    return out


def test_a_pool_at_the_cap_is_allowed():
    entries = _one_per_context(pool.MAX_ROUTABLE_STRATEGIES)
    assert len(entries) == 20
    pool.assert_pool_within_size_cap(entries)          # exactly at the cap is fine


def test_one_routable_strategy_too_many_is_refused():
    entries = _one_per_context(pool.MAX_ROUTABLE_STRATEGIES)
    entries.append(_routable("S_EXTRA", symbol="BTCUSDT", timeframe="30m"))
    with pytest.raises(MvpRuntimeError) as exc:
        pool.assert_pool_within_size_cap(entries)
    assert exc.value.reason_code == "POOL_SIZE_CAP_EXCEEDED"
    assert "21" in exc.value.reason and "--allow-oversized-pool" in exc.value.reason


def test_two_strategies_in_one_context_is_refused_even_well_under_the_global_cap():
    """The failure the global cap alone cannot see: 2 entries, 1 context, 18 slots spare."""
    entries = [_routable("S1", symbol="BTCUSDT", timeframe="15m"),
               _routable("S2", symbol="BTCUSDT", timeframe="15m")]
    with pytest.raises(MvpRuntimeError) as exc:
        pool.assert_pool_within_size_cap(entries)
    assert exc.value.reason_code == "POOL_CONTEXT_CAP_EXCEEDED"
    assert "BTCUSDT 15m" in exc.value.reason and "S1, S2" in exc.value.reason


def test_a_multi_symbol_strategy_occupies_every_symbol_it_is_scoped_to():
    """Scope, not primary symbol: `route_entries` judges it on each, so the cap must too."""
    wide = _routable("S_WIDE", timeframe="15m")
    wide["strategy_spec"] = _spec_dict(strategy_id="S_WIDE", timeframe="15m",
                                       symbol_scope=["BTCUSDT", "ETHUSDT"])
    entries = [wide, _routable("S_ETH", symbol="ETHUSDT", timeframe="15m")]
    with pytest.raises(MvpRuntimeError) as exc:
        pool.assert_pool_within_size_cap(entries)
    assert exc.value.reason_code == "POOL_CONTEXT_CAP_EXCEEDED"
    assert "ETHUSDT 15m" in exc.value.reason


# --- what the sizing cap and the directional cap do to each other ----------------
#
# An interaction, not a standalone idea, and it exists because MAX_ROUTABLE_PER_CONTEXT is 1:
# every context holds exactly one strategy, and a spec's `direction` is fixed at promotion
# time — so WHICH directions the book can ever hold became a property of the POOL, where it
# used to be a property of the template library (balanced 16 long / 16 short). Neither cap
# says so on its own, which is the whole reason this is reported.

def _directional(longs, shorts):
    """`longs + shorts` routable entries, one per context, with the given composition."""
    entries = _one_per_context(longs + shorts)
    for entry in entries[longs:]:
        entry["strategy_spec"] = {**entry["strategy_spec"], "direction": "short"}
    return entries


def test_a_balanced_pool_can_fill_every_slot_it_routes():
    """The claim the directional cap is sold on — "not a concurrency cap in disguise" — and
    under the per-context cap it is CONDITIONAL, so it needs pinning at the composition where
    it holds rather than being assumed everywhere."""
    total = pool.MAX_ROUTABLE_STRATEGIES
    shorts = (total - paper.MAX_DIRECTIONAL_SKEW) // 2
    capacity = pool.routable_directional_capacity(_directional(total - shorts, shorts))
    assert capacity["routable_contexts"] == total
    assert capacity["reachable_book"] == total
    assert capacity["cap_binds"] is False


def test_an_all_one_way_pool_can_fill_only_the_cap_and_says_so():
    """The failure this reports: twenty long strategies fill four of twenty slots. Nothing in
    the size cap or the directional cap surfaces that on its own — the book would simply stop
    growing, and an operator would read it as "no signals"."""
    capacity = pool.routable_directional_capacity(_directional(pool.MAX_ROUTABLE_STRATEGIES, 0))
    assert capacity["routable_contexts"] == pool.MAX_ROUTABLE_STRATEGIES
    assert capacity["reachable_book"] == paper.MAX_DIRECTIONAL_SKEW
    assert capacity["cap_binds"] is True


def test_each_opposing_strategy_buys_back_two_slots():
    """The arithmetic is the cap's own — an opposing position offsets an aligned one — so this
    pins that no second number crept in."""
    for shorts in range(0, 5):
        capacity = pool.routable_directional_capacity(
            _directional(pool.MAX_ROUTABLE_STRATEGIES - shorts, shorts)
        )
        expected = min(pool.MAX_ROUTABLE_STRATEGIES, 2 * shorts + paper.MAX_DIRECTIONAL_SKEW)
        assert capacity["reachable_book"] == expected, shorts


def test_a_pool_no_larger_than_the_cap_is_never_reported_as_bound():
    """Small pools must not raise the note: four one-way strategies fill four slots, which is
    the cap exactly. A line that fired on every young pool would train the reader to skip it."""
    capacity = pool.routable_directional_capacity(_directional(paper.MAX_DIRECTIONAL_SKEW, 0))
    assert capacity["cap_binds"] is False


def test_the_capacity_report_refuses_nothing():
    """Deliberately a report and not a fifth guard. The directional cap can only DECLINE, so a
    lopsided pool trades less rather than unsafely — and refusing here would forbid assembling
    a pool in any order but alternating, since five longs before the first short is ordinary."""
    lopsided = _directional(pool.MAX_ROUTABLE_STRATEGIES, 0)
    assert pool.routable_directional_capacity(lopsided)["cap_binds"] is True
    pool.assert_pool_within_size_cap(lopsided)  # the door still admits it


def test_suspended_entries_are_not_counted():
    """The cap is on the ROUTABLE set. After a retirement the pool file legitimately holds
    far more rows than the cap — 89 rows and 5 routable, the day this landed."""
    entries = [*_one_per_context(5),
               *[_routable(f"X{i}", status="SUSPENDED") for i in range(200)]]
    pool.assert_pool_within_size_cap(entries)


def test_a_spec_less_entry_contributes_nothing():
    entries = [_routable("S1", symbol="BTCUSDT", timeframe="15m"),
               {"strategy_id": "S2", "candidate_id": "c2", "status": "PAPER_ACTIVE"}]
    pool.assert_pool_within_size_cap(entries)


def test_warning_and_probation_still_occupy_a_slot():
    """They keep routing, so they keep splitting the window — the cap must see them."""
    entries = [_routable("S1", symbol="BTCUSDT", timeframe="15m"),
               _routable("S2", symbol="BTCUSDT", timeframe="15m", status="WARNING")]
    with pytest.raises(MvpRuntimeError) as exc:
        pool.assert_pool_within_size_cap(entries)
    assert exc.value.reason_code == "POOL_CONTEXT_CAP_EXCEEDED"


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
