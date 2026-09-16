"""The history import's pool activation cannot arm a strategy (PR1c review, 2026-09-16).

`--activate-pool --confirm` installs a pool file wholesale, and that file can say `live_tier: LIVE`.
It was the fourth way into the LIVE tier — no approval, no execution stage, none of the promotion
door's gates — and the only one left after PR1c closed the other three."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import pool
from scripts import import_crypto_history as door


def _spec(sid):
    return {
        "schema_version": "strategy_spec.v1",
        "strategy_id": sid, "strategy_version": "1.0", "strategy_family": "breakout",
        "symbol_scope": ["BTCUSDT"], "timeframe": "1d", "direction": "long",
        "entry_rules": {"operator": "AND",
                        "conditions": [{"feature": "close", "comparison": ">", "value": 0.0}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0,
                       "max_holding_bars": 10},
    }


def _source(tmp_path, tier):
    registries = tmp_path / "source" / "storage" / "registries"
    registries.mkdir(parents=True, exist_ok=True)
    (registries / "outcome_feedback_registry.jsonl").write_text("", encoding="utf-8")
    (registries / "counterfactual_outcome_registry.jsonl").write_text("", encoding="utf-8")
    latest = tmp_path / "source" / "storage" / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    (latest / "active_strategy_pool.json").write_text(json.dumps({"active_strategies": [
        {"strategy_id": "S1", "candidate_id": "cand_s1", "status": "PAPER_ACTIVE",
         "live_tier": tier, "strategy_spec": _spec("S1")},
    ]}), encoding="utf-8")
    return tmp_path / "source"


@pytest.mark.parametrize("tier", ["LIVE", "OBSERVATION"])
def test_an_activated_import_installs_at_observation_whatever_the_file_says(tmp_path, tier):
    state = tmp_path / "state"
    summary = door.run_import(source=_source(tmp_path, tier), root=state,
                              activate_pool=True, confirm=True)
    assert summary["pool_activated"]
    installed = pool.load_active_pool(state)["active_strategies"]
    assert [pool.entry_live_tier(e) for e in installed] == ["OBSERVATION"]
    assert pool.live_routable_strategy_ids({"active_strategies": installed}) == set()
