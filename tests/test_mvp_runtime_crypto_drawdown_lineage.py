"""PR3b-3 — a drawdown rebase names lineages, sealed when it is registered (Thomas decisions 37, 39).

A rebase lets a retired strategy's losses leave the drawdown breaker's window. It named display ids,
and the guard read them when it judged: a lineage that later took a retired one's display id had
its losses forgotten too, although nobody named it, and a retired lineage installed again under
another name did not get its losses back. The record now names lineage keys
(`candidate_identity.outcome_attribution_key`), resolved from the pool at registration
(`risk_limits.seal_drawdown_exclusion`), in the same field (decision 39: the schema's check is
unchanged, and an older image reads the keys as ids no row carries, so it excludes nothing). The
guard re-checks them against the lineages the pool can still route (`pool.routable_lineage_keys`).
No record is registered on the host (2026-09-18), so nothing migrates.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import guards, pool
from runtime.mvp_runtime.crypto import risk_limits as rl
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-09-18T12:00:00Z"


def _entry(sid, cand, *, status="PAPER_ACTIVE", generation="GEN-1"):
    return {"strategy_id": sid, "candidate_id": cand, "generation_id": generation,
            "strategy_rule_hash": f"hash-{cand}", "status": status}


def _pool(*entries):
    return {"active_strategies": list(entries)}


def _loss(sid, cand, r=-1.0, at="2026-09-10T00:00:00Z"):
    return {"outcome_closed": True, "result_R": r, "strategy_id": sid, "candidate_id": cand,
            "created_at_utc": at}


def _limits(*excluded):
    return guards.RiskLimits(drawdown_excluded_strategy_ids=tuple(excluded))


# --- the seal ------------------------------------------------------------------------------------

def test_a_display_id_is_sealed_as_every_key_its_lineage_carries():
    retired = _entry("S1", "cand_A", status="SUSPENDED")
    assert rl.seal_drawdown_exclusion(["S1"], _pool(retired)) == [
        "cand:cand_A", "gen:GEN-1:hash-cand_A", "sid:S1"]


def test_a_lineage_key_passes_through_and_an_id_the_pool_does_not_hold_is_refused():
    assert rl.seal_drawdown_exclusion(["cand:gone"], _pool()) == ["cand:gone"]
    with pytest.raises(ToolError) as refused:
        rl.seal_drawdown_exclusion(["S404"], _pool(_entry("S1", "cand_A")))
    assert refused.value.reason_code == rl.LIMITS_INVALID and "S404" in str(refused.value)


def test_a_record_names_lineages_never_display_ids():
    from tests.test_mvp_runtime_crypto_risk_limits import _LIMITS

    def build(ids):
        return rl.build_risk_limits_record(
            limits=dict(_LIMITS), registered_by="thomas", registered_at=NOW,
            drawdown_baseline_rebase={"excluded_strategy_ids": ids, "reason": "retired"})

    with pytest.raises(ToolError) as refused:
        build(["S1", "cand:cand_B"])
    assert refused.value.reason_code == rl.LIMITS_INVALID and "S1" in str(refused.value)
    sealed = rl.seal_drawdown_exclusion(["S1"], _pool(_entry("S1", "cand_A", status="SUSPENDED")))
    record = build(sealed)
    assert record["drawdown_baseline_rebase"]["excluded_strategy_ids"] == sealed


# --- the guard -----------------------------------------------------------------------------------

def test_a_sealed_retired_lineage_leaves_the_window():
    sealed = rl.seal_drawdown_exclusion(["S1"], _pool(_entry("S1", "cand_A", status="SUSPENDED")))
    rows = [_loss("S1", "cand_A"), _loss("S2", "cand_C")]
    kept, summary = guards.drawdown_baseline(rows, excluded=sealed, routable={"S2"},
                                             routable_lineages=pool.routable_lineage_keys(_pool(_entry("S2", "cand_C"))))
    assert kept == [rows[1]] and summary["applied"] is True and summary["rows_excluded"] == 1


def test_a_lineage_that_took_the_retired_id_keeps_its_losses():
    """Lineage B was installed as "S1" after A retired, and retired in turn. Nobody named B: its
    losses stay. Named by display id, both went."""
    sealed = rl.seal_drawdown_exclusion(["S1"], _pool(_entry("S1", "cand_A", status="SUSPENDED")))
    rows = [_loss("S1", "cand_A"), _loss("S1", "cand_B")]
    kept, _ = guards.drawdown_baseline(rows, excluded=sealed, routable=set(), routable_lineages=set())
    assert kept == [rows[1]]
    by_id, _ = guards.drawdown_baseline(rows, excluded=["S1"], routable=set(), routable_lineages=set())
    assert by_id == []                                       # the older record's reading


def test_a_retired_lineage_installed_again_under_another_name_gets_its_losses_back():
    sealed = rl.seal_drawdown_exclusion(["S1"], _pool(_entry("S1", "cand_A", status="SUSPENDED")))
    back = _pool(_entry("S9", "cand_A"))                         # the door renamed it
    rows = [_loss("S1", "cand_A")]
    kept, summary = guards.drawdown_baseline(rows, excluded=sealed, routable=pool.routable_strategy_ids(back),
                                             routable_lineages=pool.routable_lineage_keys(back))
    assert kept == rows and "cand:cand_A" in summary["retained_because_routable"]


def test_an_unreadable_pool_releases_no_lineage():
    sealed = ["cand:cand_A"]
    rows = [_loss("S1", "cand_A")]
    kept, summary = guards.drawdown_baseline(rows, excluded=sealed, routable=set(), routable_lineages=None)
    assert kept == rows and summary.get("retained_because_pool_unreadable") is True


def test_an_older_record_is_still_judged_by_display_id():
    rows = [_loss("S1", "cand_A"), _loss("S2", "cand_C")]
    kept, summary = guards.drawdown_baseline(rows, excluded=["S1"], routable={"S2"}, routable_lineages=None)
    assert kept == [rows[1]] and summary["excluded_lineages"] == ["S1"]


def test_the_risk_guard_measures_the_drawdown_without_the_sealed_lineage():
    """A retired lineage's large loss would trip the drawdown; sealed and not routable, it leaves the
    window. The daily, weekly and consecutive breakers still count it (the rebase is the drawdown's)."""
    sealed = rl.seal_drawdown_exclusion(["S1"], _pool(_entry("S1", "cand_A", status="SUSPENDED")))
    rows = [_loss("S1", "cand_A", r=-30.0, at="2026-08-01T00:00:00Z"), _loss("S2", "cand_C", r=0.5)]
    live = _pool(_entry("S2", "cand_C"))
    judged = guards.run_risk_guard(rows, now=NOW, limits=_limits(*sealed),
                                   routable_strategy_ids=pool.routable_strategy_ids(live),
                                   routable_lineages=pool.routable_lineage_keys(live))
    unsealed = guards.run_risk_guard(rows, now=NOW, limits=_limits(*sealed),
                                     routable_strategy_ids=pool.routable_strategy_ids(live))
    assert "max_drawdown_proxy_breached" not in judged["problems"]
    assert "max_drawdown_proxy_breached" in unsealed["problems"]     # no lineages handed: nothing leaves


def test_the_routable_lineages_are_the_routable_entries_keys():
    keys = pool.routable_lineage_keys(_pool(_entry("S1", "cand_A"), _entry("S2", "cand_B", status="SUSPENDED")))
    assert keys == {"cand:cand_A", "gen:GEN-1:hash-cand_A", "sid:S1"}


# --- the callers hand the guard the routable lineages --------------------------------------------

def test_the_cycle_hands_the_live_risk_guard_the_routable_lineages(tmp_path, monkeypatch):
    from runtime.mvp_runtime.control import ControlStore
    from runtime.mvp_runtime.crypto import cycle as cycle_module
    from runtime.mvp_runtime.crypto.paper import DryRunPaperStore
    from tests.test_mvp_runtime_crypto_cycle import FakeExchangeCollector, _always_spec

    pool.install_active_pool({"active_strategies": [
        {**_entry("S_ALWAYS", "cand_A"), "champion_score": 0.5, "strategy_spec": _always_spec()},
    ]}, root=tmp_path)
    seen = {}
    real = cycle_module.run_risk_guard

    def _spy(*args, **kwargs):
        seen["routable_lineages"] = kwargs.get("routable_lineages")
        return real(*args, **kwargs)

    monkeypatch.setattr(cycle_module, "run_risk_guard", _spy)
    cycle_module.run_crypto_cycle(collector=FakeExchangeCollector(), store=DryRunPaperStore(),
                                  now="2026-07-22T12:00:00Z", root=tmp_path, control_store=ControlStore(tmp_path))
    assert seen["routable_lineages"] == pool.routable_lineage_keys(pool.load_active_pool(tmp_path))


def test_the_breaker_watch_hands_the_risk_guard_the_routable_lineages(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto import breaker_watch
    from tests.test_mvp_runtime_crypto_cycle import _always_spec

    pool.install_active_pool({"active_strategies": [
        {**_entry("S1", "cand_A"), "champion_score": 0.5, "strategy_spec": _always_spec("S1")},
    ]}, root=tmp_path)
    seen = {}
    real = guards.run_risk_guard

    def _spy(*args, **kwargs):
        seen["routable_lineages"] = kwargs.get("routable_lineages")
        return real(*args, **kwargs)

    monkeypatch.setattr(breaker_watch.guards, "run_risk_guard", _spy)
    breaker_watch.run_breaker_watch(tmp_path, now=NOW, persist=False)
    assert seen["routable_lineages"] == {"cand:cand_A", "gen:GEN-1:hash-cand_A", "sid:S1"}
