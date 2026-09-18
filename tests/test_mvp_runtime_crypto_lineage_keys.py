"""PR3b-1 — the router, the shadow book and the live allowance key on the LINEAGE, never the display id.

`strategy_id` is a display name: the factory restarts it every generation and the promotion door
renames on a collision. Keyed by it, a lineage that reuses another's id inherits its realized
record, and a lineage that is renamed loses its own.

- Thomas decision 35: the realized ranking and the direction-conflict resolution read the lineage
  record (`candidate_identity.outcome_attribution_key`), and a pool entry accepts all three eras of
  keys (`candidate_identity.entry_attribution_keys`), as the lifecycle does. Ties go to the lineage.
- Decision 38: the supporting shadow's dedupe (when it opens, and in the book door) and the live
  allowance's fallback key follow.

Measured 2026-09-18: no display id in this machine's outcomes names two lineages, so none of this
changes a routing decision today; it closes the fail-open before one can happen.
"""

from __future__ import annotations

from tests.test_mvp_runtime_crypto_paper import NOW, ROW, _pool, _pool_entry, _spec_dict

from runtime.mvp_runtime.crypto import counterfactual, feedback, paper
from runtime.mvp_runtime.crypto.live_allowance import BREACH_CONSECUTIVE, evaluate_live_allowance
from runtime.mvp_runtime.crypto.paper import route_entries


def _entry(sid, cand, score=0.5, **kw):
    """A pool entry of lineage ``cand`` answering to display id ``sid``."""
    return {**_pool_entry(strategy_id=sid, champion_score=score, **kw), "candidate_id": cand}


def _closed(sid, cand, r, n=12, **extra):
    """``n`` closed own-paper rows of lineage ``cand``, recorded under display id ``sid``."""
    row = {"outcome_closed": True, "result_R": r, "strategy_id": sid, "provenance": "mvp_paper_kernel"}
    if cand is not None:
        row["candidate_id"] = cand
    return [{**row, **extra} for _ in range(n)]


def _route(*entries, stats):
    return route_entries(_pool(*entries), ROW, symbol="BTCUSDT", timeframe="1d", now=NOW, realized_stats=stats)


# --- decision 35: the realized record is the lineage's -------------------------------------------

def test_a_display_id_reused_by_another_lineage_does_not_inherit_its_record():
    """Lineage A proved an edge as "S_x"; A is gone and B answers to "S_x" now. B is untested, and
    ranks as untested: by score, behind the better-scored rival."""
    stats = feedback.realized_by_lineage(_closed("S_x", "cand_A", 0.4))
    route = _route(_entry("S_x", "cand_B", score=0.1), _entry("S_y", "cand_C", score=0.9), stats=stats)
    assert route["primary_strategy_id"] == "S_y"
    assert route["primary_priority_basis"] == "champion_score"


def test_a_renamed_lineage_keeps_its_record():
    """Lineage A proved an edge as "S_old" and was installed again as "S_new" (the door renames on a
    collision). It is the same strategy, and its record still ranks it first."""
    stats = feedback.realized_by_lineage(_closed("S_old", "cand_A", 0.4))
    route = _route(_entry("S_new", "cand_A", score=0.1), _entry("S_y", "cand_C", score=0.9), stats=stats)
    assert route["primary_strategy_id"] == "S_new"
    assert route["primary_priority_basis"] == "realized_expectancy"


def test_a_proven_loss_follows_its_lineage_through_a_rename():
    """The fail-open the other way: renamed, a proven loser would have ranked as a fresh hypothesis."""
    stats = feedback.realized_by_lineage(_closed("S_old", "cand_A", -0.3, n=15))
    route = _route(_entry("S_new", "cand_A", score=0.9), _entry("S_y", "cand_C", score=0.1), stats=stats)
    assert route["primary_strategy_id"] == "S_y"
    assert route["supporting_strategy_ids"] == ["S_new"]


def test_an_entry_reads_its_record_across_the_three_eras():
    """An imported entry (no candidate id) whose history is split between rows that carry its
    generation and rule hash and older rows that carry only its name: one record, as the lifecycle
    reads it. Neither half alone reaches the sample floor; together they do."""
    half = paper.MIN_PRIORITY_SAMPLE_TRADES // 2 + 1
    rows = (_closed("S_imp", None, 0.5, n=half, strategy_generation_id="GEN-001", strategy_rule_hash="deadbeef")
            + _closed("S_imp", None, 0.1, n=half))
    stats = feedback.realized_by_lineage(rows)
    assert sorted(stats) == ["gen:GEN-001:deadbeef", "sid:S_imp"]
    imported = _pool_entry(strategy_id="S_imp", champion_score=0.1)          # GEN-001 / deadbeef
    evidence = paper._realized_evidence(
        {**imported, "strategy_generation_id": imported["generation_id"]}, stats)
    assert evidence is not None and evidence[0] == 2 * half
    assert abs(evidence[1] - 0.3) < 1e-9
    route = _route(imported, _entry("S_y", "cand_C", score=0.9), stats=stats)
    assert route["primary_strategy_id"] == "S_imp"


def test_a_row_naming_no_lineage_feeds_nothing():
    rows = [{"outcome_closed": True, "result_R": 1.0}] * 20 + _closed("S_x", "cand_A", 0.2, n=3)
    assert feedback.realized_by_lineage(rows) == {
        "cand:cand_A": {"closed_count": 3, "win_count": 3, "loss_count": 0, "expectancy": 0.2}}


def test_the_lineage_summary_is_the_reports_arithmetic():
    """Where each display id names one lineage, the lineage summary is `by_strategy` re-keyed."""
    rows = _closed("S_a", "cand_a", 0.5, n=4) + _closed("S_a", "cand_a", -1.0, n=2) + _closed("S_b", "cand_b", 0.25, n=3)
    by_strategy = feedback.summarize_outcomes(rows)["by_strategy"]
    assert feedback.realized_by_lineage(rows) == {
        "cand:cand_a": by_strategy["S_a"], "cand:cand_b": by_strategy["S_b"]}


def test_a_tie_goes_to_the_lineage_and_a_rename_does_not_reorder_it():
    """Two untested entries with the same score: the lineage breaks the tie, not the display id."""
    first, second = _entry("S_b", "cand_a"), _entry("S_a", "cand_z")
    assert _route(second, first, stats=None)["primary_strategy_id"] == "S_b"
    renamed = {**first, "strategy_id": "S_zz", "strategy_spec": _spec_dict(strategy_id="S_zz")}
    assert _route(second, renamed, stats=None)["primary_strategy_id"] == "S_zz"


def test_a_conflict_is_not_resolved_on_a_record_the_side_did_not_earn():
    """The long side answers to the id a proven long lineage once held; it proved nothing itself,
    so no side is backed and the bar stays closed, as it would for two fresh hypotheses."""
    short_spec = _spec_dict(strategy_id="S_short", direction="short", entry_rules={
        "operator": "AND", "conditions": [{"feature": "adx", "comparison": ">=", "value": 20.0}]})
    stats = feedback.realized_by_lineage(_closed("S_long", "cand_gone", 0.35))
    route = _route(_entry("S_long", "cand_new"), _entry("S_short", "cand_s", spec=short_spec), stats=stats)
    assert route["status"] == paper.STATUS_BLOCKED
    assert route["block_reason"] == paper.BLOCK_DIRECTION_CONFLICT
    earned = feedback.realized_by_lineage(_closed("S_long", "cand_new", 0.35))
    resolved = _route(_entry("S_long", "cand_new"), _entry("S_short", "cand_s", spec=short_spec), stats=earned)
    assert resolved["direction_conflict"]["basis"]["lineage"] == "cand:cand_new"


def test_the_cycle_hands_the_router_the_record_by_lineage(tmp_path, monkeypatch):
    from runtime.mvp_runtime.control import ControlStore
    from runtime.mvp_runtime.crypto import cycle as cycle_module
    from runtime.mvp_runtime.crypto import pool as pool_store
    from runtime.mvp_runtime.crypto.paper import DryRunPaperStore
    from tests.test_mvp_runtime_crypto_cycle import FakeExchangeCollector, _always_spec

    pool_store.install_active_pool({"active_strategies": [
        {"strategy_id": "S_NEW", "candidate_id": "cand_A", "status": "PAPER_ACTIVE", "champion_score": 0.5,
         "strategy_spec": _always_spec("S_NEW")},
    ]}, root=tmp_path)
    seen = {}
    real = cycle_module.run_paper_update

    def _spy(*args, **kwargs):
        seen["realized_stats"] = kwargs.get("realized_stats")
        return real(*args, **kwargs)

    monkeypatch.setattr(cycle_module, "run_paper_update", _spy)
    cycle_module.run_crypto_cycle(
        collector=FakeExchangeCollector(), store=DryRunPaperStore(), now="2026-07-22T12:00:00Z",
        root=tmp_path, control_store=ControlStore(tmp_path),
        paper_outcomes=_closed("S_OLD", "cand_A", 0.4, n=3))
    assert seen["realized_stats"] == {
        "cand:cand_A": {"closed_count": 3, "win_count": 3, "loss_count": 0, "expectancy": 0.4}}


# --- decision 38: the shadow book and the allowance ----------------------------------------------

def _splan(sid, cand):
    return {"symbol": "BTCUSDT", "timeframe": "1d", "direction": "LONG", "entry_price": 100.0,
            "stop_loss": 97.0, "take_profit": 104.0, "risk": 3.0, "strategy_id": sid,
            "candidate_id": cand, "strategy_rule_hash": "aaa", "shadow_reason": paper.ROUTED_BEHIND_PRIMARY}


def _open_supporting(tmp_path, plans, now):
    return counterfactual.run_counterfactual_update(
        blocked_plan=None, block_reasons=[], last_candle=None, last_close=None,
        symbol="BTCUSDT", timeframe="1d", now=now, root=tmp_path, supporting_plans=plans)


def test_a_reused_id_does_not_suppress_another_lineages_shadow(tmp_path):
    _open_supporting(tmp_path, [_splan("S_x", "cand_A")], "2026-07-22T12:00:00Z")
    result = _open_supporting(tmp_path, [_splan("S_x", "cand_B")], "2026-07-22T16:00:00Z")
    assert len(result["supporting_opened"]) == 1 and result["supporting_skipped"] == 0
    assert result["open_count"] == 2


def test_a_renamed_lineage_does_not_book_its_signal_twice(tmp_path):
    _open_supporting(tmp_path, [_splan("S_old", "cand_A")], "2026-07-22T12:00:00Z")
    result = _open_supporting(tmp_path, [_splan("S_new", "cand_A")], "2026-07-22T16:00:00Z")
    assert result["supporting_opened"] == [] and result["supporting_skipped"] == 1
    assert result["open_count"] == 1


def test_the_book_door_dedupes_supporting_shadows_by_lineage():
    def _row(cf_id, sid, cand, opened):
        return {"status": "OPEN", "counterfactual_id": cf_id, "symbol": "BTCUSDT", "timeframe": "1d",
                "opened_at_utc": opened, "strategy_id": sid, "candidate_id": cand,
                "shadow_kind": counterfactual.SHADOW_KIND_SUPPORTING}

    rows = [_row("cf-a1", "S_x", "cand_A", "2026-07-22T12:00:00Z"),
            _row("cf-b", "S_x", "cand_B", "2026-07-22T13:00:00Z"),          # reused id: kept
            _row("cf-a2", "S_new", "cand_A", "2026-07-22T14:00:00Z")]       # renamed: a duplicate
    assert [d["counterfactual_id"] for d in counterfactual.context_duplicates(rows)] == ["cf-a2"]


def test_an_armed_id_with_no_pool_entry_is_charged_its_named_losses():
    """With no pool entry to name its lineage, the display id is the key, and it must be the key an
    outcome naming only that id carries (`sid:`). It was `id:`, which no outcome key equals, so the
    losses were never charged and the arm read as clean."""
    losses = [{"outcome_closed": True, "result_R": -1.0, "strategy_id": "S9"}] * 2
    for pool in (None, _pool()):
        result = evaluate_live_allowance(losses, live_routable_strategy_ids={"S9"}, pool=pool)
        [breach] = result["breached"]
        assert breach["strategy_id"] == "S9" and breach["lineage"] == "sid:S9"
        assert BREACH_CONSECUTIVE in breach["reasons"]
