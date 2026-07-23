"""C8 factory tests — seeded generation, validation, backtest evidence, promotion door.

The source rules under test: same seed → same batch; the validator fails closed on
unknown features and bad risk:reward; the backtest uses the shared evaluator + exit
math; the factory only ever creates candidates; and the active pool changes only
through the explicit operator door (kill-switch bound, audited)."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest

from runtime.mvp_runtime import control, timeutil
from runtime.mvp_runtime.control import ControlState, ControlStore
from runtime.mvp_runtime.crypto import pool
from runtime.mvp_runtime.crypto.factory import (
    generate_batch,
    backtest_spec,
    fuse_specs,
    mutate_params,
    next_generation_id,
    rank_fusion_parents,
    run_factory,
    templates_for_timeframe,
    validate_strategy,
    FusionRefused,
    ParamSpec,
)
from runtime.mvp_runtime.crypto.strategy import StrategySpec
from runtime.mvp_runtime.scheduler import KIND_FACTORY, ScheduleStore, build_schedule, run_due
from runtime.mvp_runtime.store import LEDGER_REL, RECORDS_FILE, LedgerStore

from scripts.promote_strategy_candidates import run_promotion

NOW = "2026-07-22T12:00:00Z"
NOW_DT = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)


def _spec_dict(**overrides):
    base = {
        "schema_version": "strategy_spec.v1",
        "strategy_id": "S1", "strategy_version": "1.0", "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"], "timeframe": "1d", "direction": "long",
        "entry_rules": {"operator": "AND",
                        "conditions": [{"feature": "close", "comparison": ">", "value_from": "ma20"}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    base.update(overrides)
    return base


def _trending_snapshot(n=200):
    """Rising closes with a mid-series dip: entries and both exit kinds occur."""
    step = timedelta(days=1)
    last_close = NOW_DT - timedelta(hours=1)
    candles = []
    price = 100.0
    for i in range(n):
        drift = 1.0 if (i // 20) % 3 != 2 else -1.5  # two up-blocks, one down-block
        price = max(10.0, price + drift)
        close_time = last_close - (n - 1 - i) * step
        candles.append({
            "open_time": timeutil.format_iso(close_time - step),
            "open": price - drift, "high": price + 1.5, "low": price - 1.5,
            "close": price, "volume": 10.0 + (i % 7),
            "close_time": timeutil.format_iso(close_time),
        })
    return {"symbol": "BTCUSDT", "timeframe": "1d", "candles": candles, "is_synthetic": False}


# --- generator ----------------------------------------------------------------

def test_same_seed_same_batch():
    a = generate_batch("GEN-001", seed=42, timeframe="1d")
    b = generate_batch("GEN-001", seed=42, timeframe="1d")
    assert a == b  # the source's reproducibility rule
    assert a["accepted_count"] == a["requested_count"] == 4
    assert all(s["created_by"] == "mvp_factory" for s in a["specs"])


def test_different_seed_different_params():
    a = generate_batch("GEN-001", seed=1, timeframe="1d")
    b = generate_batch("GEN-001", seed=2, timeframe="1d")
    assert a["specs"] != b["specs"]


def test_known_hashes_are_never_reminted():
    a = generate_batch("GEN-001", seed=7, timeframe="1d")
    hashes = frozenset(s["strategy_rule_hash"] for s in a["specs"])
    b = generate_batch("GEN-002", seed=7, timeframe="1d", known_rule_hashes=hashes)
    assert not hashes & {s["strategy_rule_hash"] for s in b["specs"]}
    assert any(r.get("reason") == "duplicate_rule_hash" for r in b["rejected"])


def test_mutation_respects_bounds():
    rng = random.Random(0)
    space = {"x": ParamSpec(1.0, 2.0), "n": ParamSpec(5, 10, integer=True)}
    for _ in range(50):
        out = mutate_params({"x": 1.5, "n": 7}, space, rng)
        assert 1.0 <= out["x"] <= 2.0
        assert isinstance(out["n"], int) and 5 <= out["n"] <= 10


def test_generated_specs_carry_generation_lineage():
    batch = generate_batch("GEN-042", seed=9, timeframe="1d")
    assert all(s["generation_id"] == "GEN-042" for s in batch["specs"])


# --- validator ----------------------------------------------------------------

def test_unknown_feature_blocks():
    # open_interest is a real source column whose feed was never ported — it must
    # stay unmintable (funding_zscore graduated INTO the registry with C9).
    spec = StrategySpec.from_dict(_spec_dict(entry_rules={
        "operator": "AND", "conditions": [{"feature": "open_interest", "comparison": ">", "value": 1.0}],
    }))
    verdict = validate_strategy(spec)
    assert verdict["approved_for_backtest"] is False
    assert "BLOCK_UNKNOWN_FEATURE" in verdict["block_reasons"]


def test_bad_reward_risk_blocks():
    spec = StrategySpec.from_dict(_spec_dict(
        exit_rules={"stop_model": "atr", "stop_atr": 2.0, "target_atr": 1.0, "max_holding_bars": 10}))
    assert "BLOCK_INVALID_RISK_REWARD" in validate_strategy(spec)["block_reasons"]


def test_categorical_ordering_comparison_blocks():
    spec = StrategySpec.from_dict(_spec_dict(entry_rules={
        "operator": "AND", "conditions": [{"feature": "market_regime", "comparison": ">", "value": "RANGE"}],
    }))
    assert "BLOCK_INVALID_COMPARISON" in validate_strategy(spec)["block_reasons"]


def test_valid_spec_approved():
    spec = StrategySpec.from_dict(_spec_dict())
    verdict = validate_strategy(spec)
    assert verdict["approved_for_backtest"] is True and verdict["block_reasons"] == []


def test_all_ported_templates_validate():
    for template in templates_for_timeframe("1d"):
        batch = generate_batch("GEN-001", seed=3, count=1, timeframe="1d")
        assert batch["accepted_count"] == 1  # every family passes its own validator


# --- backtest -----------------------------------------------------------------

def test_backtest_is_deterministic_and_produces_outcomes():
    spec = StrategySpec.from_dict(_spec_dict())
    snapshot = _trending_snapshot()
    a = backtest_spec(spec, snapshot)
    b = backtest_spec(spec, snapshot)
    assert a == b
    assert a["closed_count"] > 0  # the trending fixture must actually trade
    assert a["score_basis"] == "robustness_score_v1"  # C8b: anti-overfit score
    assert a["champion_score"] == a["robustness"]["robustness_score"]
    assert a["robustness"]["verdict"] in {"ROBUST", "PROVISIONAL", "FRAGILE"}
    assert a["bars_replayed"] == 200


def test_backtest_never_enters_on_indeterminate_features():
    # ma50 needs 50 bars; a 30-bar window leaves it indeterminate on every row,
    # so a spec referencing it can never enter (the evaluator's fail-closed rule).
    spec = StrategySpec.from_dict(_spec_dict(entry_rules={
        "operator": "AND", "conditions": [{"feature": "close", "comparison": ">", "value_from": "ma50"}],
    }))
    result = backtest_spec(spec, _trending_snapshot(30))
    assert result["closed_count"] == 0


# --- run_factory --------------------------------------------------------------

def test_run_factory_produces_evidence_backed_candidates():
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=[], now=NOW)
    assert result["generation_id"] == "GEN-001"
    assert result["accepted_count"] == len(result["candidates"]) == 4
    for c in result["candidates"]:
        assert c["provenance"] == "mvp_factory" and c["status"] == "BACKTESTED"
        assert c["backtest_evidence"]["strategy_rule_hash"] == c["strategy_rule_hash"]
        assert c["evidence_input_sha256"].startswith("sha256:")
        # Stored candidate_id equals the lineage-derived one (the promotion key).
        assert c["candidate_id"] == pool.derive_candidate_id(c)
    assert len({c["candidate_id"] for c in result["candidates"]}) == len(result["candidates"])


def test_run_factory_is_reproducible_from_inputs():
    snapshot = _trending_snapshot()
    a = run_factory(snapshot, active_pool={"active_strategies": []}, existing_candidates=[], now=NOW)
    b = run_factory(snapshot, active_pool={"active_strategies": []}, existing_candidates=[], now=NOW)
    assert a == b  # seed derives from the candle window, not the clock


def test_generation_number_advances_past_pool_and_candidates():
    existing = [{"generation_id": "GEN-068"}, {"strategy_spec": {"generation_id": "GEN-070"}}]
    assert next_generation_id(existing) == "GEN-071"
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=existing, now=NOW)
    assert result["generation_id"] == "GEN-071"


# --- scheduler template -------------------------------------------------------

def test_scheduler_factory_fire_appends_candidates_and_ledgers(tmp_path, monkeypatch):
    monkeypatch.delenv("MVP_MARKET_DATA", raising=False)
    schedule = build_schedule(kind=KIND_FACTORY, request="", interval_seconds=86400,
                              created_by="op", now="2026-07-22T10:00:00Z")
    store = ScheduleStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.add(schedule)
    ledger = LedgerStore(tmp_path / LEDGER_REL)
    summary = run_due(store, now="2026-07-23T11:00:00Z", control_store=ControlStore(tmp_path),
                      ledger=ledger, repo_root=tmp_path)
    assert summary["fired"] == 1
    assert summary["results"][0]["status"].startswith("generated=")
    candidates = pool.read_candidates(tmp_path)
    assert len(candidates) == 4  # mock collector data is fine for MINING (not trading)
    rows = [json.loads(line) for line in
            (tmp_path / LEDGER_REL / RECORDS_FILE).read_text(encoding="utf-8").splitlines()]
    assert any(r["kind"] == "crypto_factory" for r in rows)


# --- the promotion door -------------------------------------------------------

def _seed_candidates(tmp_path):
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=[], now=NOW)
    pool.append_candidates(result["candidates"], root=tmp_path)
    return [c["strategy_id"] for c in result["candidates"]]


def test_promotion_installs_selected_candidates(tmp_path):
    ids = _seed_candidates(tmp_path)
    summary = run_promotion(selectors=ids[:2], promoted_by="Thomas", reason="reviewed",
                            keep_active=False, root=tmp_path, now=NOW, without_approval=True)
    assert summary["pool_size"] == 2
    active = pool.load_active_pool(tmp_path)
    assert [e["strategy_id"] for e in active["active_strategies"]] == ids[:2]
    assert all(e["status"] == "PAPER_ACTIVE" for e in active["active_strategies"])
    # Audited on the control ledger.
    control_text = (tmp_path / LEDGER_REL / "control_events.jsonl").read_text(encoding="utf-8")
    assert "crypto_strategy_promotion_event.v0" in control_text


def test_promotion_keep_active_adds(tmp_path):
    ids = _seed_candidates(tmp_path)
    run_promotion(selectors=ids[:1], promoted_by="Thomas", reason="r",
                  keep_active=False, root=tmp_path, now=NOW, without_approval=True)
    run_promotion(selectors=ids[1:2], promoted_by="Thomas", reason="r",
                  keep_active=True, root=tmp_path, now=NOW, without_approval=True)
    active = pool.load_active_pool(tmp_path)
    assert len(active["active_strategies"]) == 2


def test_promotion_refused_while_killed(tmp_path):
    ids = _seed_candidates(tmp_path)
    store = ControlStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps(ControlState(mode=control.KILLED, updated_by="op", updated_at=NOW, reason="t").as_record()),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        run_promotion(selectors=ids[:1], promoted_by="Thomas", reason="r",
                      keep_active=False, root=tmp_path, now=NOW)
    assert "BLOCKED" in str(exc.value)
    assert pool.load_active_pool(tmp_path) == {"active_strategies": []}


def test_promotion_refuses_unknown_candidate(tmp_path):
    _seed_candidates(tmp_path)
    with pytest.raises(SystemExit):
        run_promotion(selectors=["S_NOPE"], promoted_by="Thomas", reason="r",
                      keep_active=False, root=tmp_path, now=NOW)


# --- candidate lineage (fusion groundwork) ------------------------------------

def _lineage_row(parent_ids, derivation="mutation", seed_tag="x"):
    """A minimal candidate row claiming a derivation from ``parent_ids``."""
    return {
        "strategy_id": "S900",
        "strategy_rule_hash": f"sha256:{seed_tag * 8}",
        "generation_id": "GEN-900",
        "evidence_input_sha256": "sha256:" + "e" * 8,
        "derivation_type": derivation,
        "parent_candidate_ids": parent_ids,
    }


def test_factory_records_are_seeded_with_empty_parents():
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=[], now=NOW)
    assert result["candidates"]
    for c in result["candidates"]:
        assert c["derivation_type"] == "seeded_template"
        assert c["parent_candidate_ids"] == []


def test_parented_row_roundtrips_when_parents_are_durable(tmp_path):
    _seed_candidates(tmp_path)
    parents = [pool.candidate_id(r) for r in pool.read_candidates(tmp_path)[:2]]
    pool.append_candidates([_lineage_row(parents, derivation="crossover")], root=tmp_path)
    rows = pool.read_candidates(tmp_path)
    assert rows[-1]["parent_candidate_ids"] == parents
    assert rows[-1]["derivation_type"] == "crossover"


def test_legacy_rows_without_lineage_fields_still_append(tmp_path):
    row = _lineage_row([], seed_tag="l")
    del row["derivation_type"], row["parent_candidate_ids"]
    assert pool.append_candidates([row], root=tmp_path) == 1


def test_unknown_parent_refused_and_nothing_written(tmp_path):
    _seed_candidates(tmp_path)
    before = len(pool.read_candidates(tmp_path))
    with pytest.raises(Exception) as exc:
        pool.append_candidates([_lineage_row(["cand-nope"])], root=tmp_path)
    assert "UNKNOWN_PARENT_CANDIDATE" in str(exc.value)
    assert len(pool.read_candidates(tmp_path)) == before  # all-or-nothing


@pytest.mark.parametrize("mutant, reason", [
    ({"derivation_type": "alien"}, "unknown derivation_type"),
    ({"derivation_type": "seeded_template"}, "admits 0..0 parents"),          # parents ride along
    ({"parent_candidate_ids": None}, "list of non-empty ids"),
    ({"parent_candidate_ids": ["p", "p"]}, "duplicate"),
    ({"parent_candidate_ids": ["only-one"], "derivation_type": "crossover"}, "admits 2+"),
])
def test_incoherent_lineage_refused(tmp_path, mutant, reason):
    _seed_candidates(tmp_path)
    known = pool.candidate_id(pool.read_candidates(tmp_path)[0])
    row = {**_lineage_row([known]), **mutant}
    with pytest.raises(Exception) as exc:
        pool.append_candidates([row], root=tmp_path)
    assert "CANDIDATE_LINEAGE_INVALID" in str(exc.value)
    assert reason.split()[0].lower() in str(exc.value).lower()


def test_parents_without_derivation_type_refused(tmp_path):
    _seed_candidates(tmp_path)
    known = pool.candidate_id(pool.read_candidates(tmp_path)[0])
    row = _lineage_row([known])
    del row["derivation_type"]
    with pytest.raises(Exception) as exc:
        pool.append_candidates([row], root=tmp_path)
    assert "CANDIDATE_LINEAGE_INVALID" in str(exc.value)


# --- fusion: crossover of two proven lineages ---------------------------------

def _parent_spec(strategy_id, conditions, **overrides):
    return StrategySpec.from_dict(_spec_dict(
        strategy_id=strategy_id,
        entry_rules={"operator": "AND", "conditions": conditions},
        **overrides,
    ))


_CLOSE_OVER_MA20 = {"feature": "close", "comparison": ">", "value_from": "ma20"}
_MA20_OVER_MA50 = {"feature": "ma20", "comparison": ">", "value_from": "ma50"}


def test_fusion_is_order_independent():
    a = _parent_spec("S1", [_CLOSE_OVER_MA20])
    b = _parent_spec("S2", [{"feature": "adx", "comparison": ">=", "value": 25.0}])
    left = fuse_specs(a, b, strategy_id="S9", generation_id="GEN-9")
    right = fuse_specs(b, a, strategy_id="S9", generation_id="GEN-9")
    assert left.to_dict() == right.to_dict()
    assert left.strategy_rule_hash == right.strategy_rule_hash


def test_fused_entry_is_the_deduplicated_union_under_and():
    a = _parent_spec("S1", [_CLOSE_OVER_MA20, {"feature": "adx", "comparison": ">=", "value": 25.0}])
    b = _parent_spec("S2", [_CLOSE_OVER_MA20, {"feature": "rsi", "comparison": "<=", "value": 55.0}])
    child = fuse_specs(a, b, strategy_id="S9", generation_id="GEN-9")
    assert child.entry_rules.operator == "AND"
    # The shared condition appears once; the child is strictly more selective.
    assert sorted(c.feature for c in child.entry_rules.conditions) == ["adx", "close", "rsi"]
    assert len(child.entry_rules.conditions) == 3
    assert child.strategy_family == "breakout"  # shared family collapses


@pytest.mark.parametrize("overrides, reason", [
    ({"direction": "short"}, "direction_mismatch"),
    ({"timeframe": "4h"}, "timeframe_mismatch"),
    ({"symbol_scope": ["ETHUSDT"]}, "symbol_scope_mismatch"),
])
def test_fusion_refuses_parents_that_disagree_on_context(overrides, reason):
    a = _parent_spec("S1", [_CLOSE_OVER_MA20])
    b = _parent_spec("S2", [_MA20_OVER_MA50], **overrides)
    with pytest.raises(FusionRefused) as exc:
        fuse_specs(a, b, strategy_id="S9", generation_id="GEN-9")
    assert exc.value.reason == reason


def test_fusion_refuses_an_or_parent():
    a = _parent_spec("S1", [_CLOSE_OVER_MA20])
    b = StrategySpec.from_dict(_spec_dict(
        strategy_id="S2",
        entry_rules={"operator": "OR", "conditions": [_MA20_OVER_MA50, _CLOSE_OVER_MA20]},
    ))
    with pytest.raises(FusionRefused) as exc:
        fuse_specs(a, b, strategy_id="S9", generation_id="GEN-9")
    assert exc.value.reason == "non_and_parent"  # AND-union would change what OR meant


def test_fusion_refuses_when_the_union_exceeds_the_condition_cap():
    feats_a = ["close", "ma20", "ma50", "adx", "rsi"]
    feats_b = ["macd", "atr", "volume", "roc_4"]
    a = _parent_spec("S1", [{"feature": f, "comparison": ">=", "value": 1.0} for f in feats_a])
    b = _parent_spec("S2", [{"feature": f, "comparison": ">=", "value": 1.0} for f in feats_b])
    with pytest.raises(FusionRefused) as exc:
        fuse_specs(a, b, strategy_id="S9", generation_id="GEN-9")
    assert exc.value.reason == "too_many_conditions"


def test_fusion_validates_the_child_even_when_a_parent_never_was():
    """An imported parent may sit outside the validator's bounds; the blend is
    checked, never clamped."""
    a = _parent_spec("S1", [_CLOSE_OVER_MA20])
    b = _parent_spec("S2", [_MA20_OVER_MA50],
                     exit_rules={"stop_model": "atr", "stop_atr": 12.0,
                                 "target_atr": 12.0, "max_holding_bars": 10})
    with pytest.raises(FusionRefused) as exc:
        fuse_specs(a, b, strategy_id="S9", generation_id="GEN-9")
    assert "BLOCK_INVALID_PARAMETER_RANGE" in exc.value.reason


def test_rank_fusion_parents_orders_by_score_and_skips_the_unscorable():
    spec = _parent_spec("S1", [_CLOSE_OVER_MA20]).to_dict()
    records = [
        {"candidate_id": "cand-low", "champion_score": 0.1, "strategy_spec": spec},
        {"candidate_id": "cand-high", "champion_score": 0.9, "strategy_spec": spec},
        {"candidate_id": "cand-unscored", "strategy_spec": spec},          # no evidence to pass on
        {"candidate_id": "cand-specless", "champion_score": 0.5},          # nothing to fuse
    ]
    ranked = rank_fusion_parents(records)
    assert [r["candidate_id"] for r in ranked] == ["cand-high", "cand-low"]


# --- fusion through the factory door ------------------------------------------

def _durable_parent(tmp_path, candidate_id, spec, score):
    record = {
        "candidate_id": candidate_id, "strategy_id": spec.strategy_id,
        "strategy_rule_hash": spec.strategy_rule_hash, "generation_id": "GEN-000",
        "status": "BACKTESTED", "champion_score": score, "strategy_spec": spec.to_dict(),
        "backtest_evidence": {"closed_count": 9}, "evidence_input_sha256": "sha256:parentwindow",
        "provenance": "test", "created_at_utc": NOW,
    }
    pool.append_candidates([record], root=tmp_path)
    return record


def _two_durable_parents(tmp_path):
    _durable_parent(tmp_path, "cand-aaa", _parent_spec("S1", [_CLOSE_OVER_MA20]), 99.0)
    _durable_parent(tmp_path, "cand-bbb", _parent_spec("S2", [_MA20_OVER_MA50]), 98.0)
    return pool.read_candidates(tmp_path)


def test_factory_default_mints_nothing_fused():
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=[], now=NOW)
    assert result["fused_count"] == 0
    assert result["fusion_rejected"] == []
    assert all(c["derivation_type"] == "seeded_template" for c in result["candidates"])


def test_fused_child_carries_lineage_own_evidence_and_appends(tmp_path):
    parents = _two_durable_parents(tmp_path)
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=parents, now=NOW, count=1, fusion_pairs=1)
    fused = [c for c in result["candidates"] if c["derivation_type"] == "crossover"]
    assert result["fused_count"] == 1 and len(fused) == 1
    child = fused[0]
    assert child["parent_candidate_ids"] == ["cand-aaa", "cand-bbb"]  # sorted, order-independent
    assert child["provenance"] == "mvp_factory_fusion"
    # Its own evidence window and its own score — a parent's 99.0 is not inherited.
    assert child["evidence_input_sha256"] == result["evidence_input_sha256"] != "sha256:parentwindow"
    assert child["champion_score"] != 99.0
    assert child["backtest_evidence"]["closed_count"] > 0
    # The store's lineage guard accepts it: both parents were durable beforehand.
    assert pool.append_candidates(result["candidates"], root=tmp_path) == len(result["candidates"])


def test_fusion_is_deterministic(tmp_path):
    parents = _two_durable_parents(tmp_path)
    kwargs = dict(active_pool={"active_strategies": []}, existing_candidates=parents,
                  now=NOW, count=1, fusion_pairs=1)
    assert run_factory(_trending_snapshot(), **kwargs) == run_factory(_trending_snapshot(), **kwargs)


def test_unsatisfiable_union_is_refused_rather_than_stored(tmp_path):
    """rsi <= 5 AND rsi >= 95 parses, validates, and can never trade."""
    _durable_parent(tmp_path, "cand-lo",
                    _parent_spec("S1", [{"feature": "rsi", "comparison": "<=", "value": 5.0}]), 9.0)
    _durable_parent(tmp_path, "cand-hi",
                    _parent_spec("S2", [{"feature": "rsi", "comparison": ">=", "value": 95.0}]), 8.0)
    result = run_factory(_trending_snapshot(), active_pool={"active_strategies": []},
                         existing_candidates=pool.read_candidates(tmp_path), now=NOW,
                         count=1, fusion_pairs=1)
    assert result["fused_count"] == 0
    assert result["fusion_rejected"] == [
        {"parent_candidate_ids": ["cand-hi", "cand-lo"], "reason": "no_trades"}
    ]
