"""The disarm door (Thomas decision 10, 2026-09-15; PR1c).

Arming a strategy for real money is Thomas's approved decision; taking that permission back is
nobody's approval to wait for. What is pinned here: it needs no approval and applies at once, it
can only ever write OBSERVATION, it records what it did, and it changes nothing about positions
that are already open."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import pool
from runtime.mvp_runtime.store import CONTROL_FILE, LEDGER_REL
from scripts import disarm_live_strategies as door

NOW = "2026-09-16T00:00:00Z"


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


def _pool(tmp_path, tiers):
    entries = [
        {"strategy_id": sid, "candidate_id": f"cand_{sid}", "status": "PAPER_ACTIVE",
         "live_tier": tier, "strategy_spec": _spec(sid)}
        for sid, tier in tiers.items()
    ]
    path = pool.pool_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"active_strategies": entries}), encoding="utf-8")


def _tiers(tmp_path):
    return {e["strategy_id"]: pool.entry_live_tier(e)
            for e in pool.load_active_pool(tmp_path)["active_strategies"]}


def test_it_disarms_the_named_strategies_and_leaves_the_rest(tmp_path):
    _pool(tmp_path, {"S1": "LIVE", "S2": "LIVE", "S3": "OBSERVATION"})
    out = door.run_disarm(strategy_ids=["S1"], disarmed_by="thomas", reason="review",
                          root=tmp_path, now=NOW)
    assert out["disarmed"] == 1
    assert _tiers(tmp_path) == {"S1": "OBSERVATION", "S2": "LIVE", "S3": "OBSERVATION"}
    assert out["armed_before"] == ["S1", "S2"] and out["armed_after"] == ["S2"]
    # The entry says who took the permission away and why — it outlives the argv.
    entry = next(e for e in pool.load_active_pool(tmp_path)["active_strategies"]
                 if e["strategy_id"] == "S1")
    assert entry["live_tier_updated_at"] == NOW
    assert "thomas" in entry["live_tier_reasons"][0] and "review" in entry["live_tier_reasons"][0]


def test_it_needs_no_approval_and_says_so_on_the_ledger(tmp_path):
    _pool(tmp_path, {"S1": "LIVE"})
    out = door.run_disarm(strategy_ids=["S1"], disarmed_by="thomas", reason="stop",
                          root=tmp_path, now=NOW)
    assert out["approval_required"] is False and out["approval_id"] is None
    rows = [json.loads(line) for line in
            (tmp_path / LEDGER_REL / CONTROL_FILE).read_text(encoding="utf-8").splitlines()]
    events = [r for r in rows if r.get("record_type") == door.DISARM_EVENT_TYPE]
    assert len(events) == 1 and events[0]["disarmed"] == 1
    assert events[0]["disarmed_by"] == "thomas"


def test_the_door_cannot_arm_anything(tmp_path):
    """The property is in the signature it calls, not in this door's care: `pool.disarm_live_tier`
    takes no target tier, so no argument here can be made to arm a strategy."""
    import inspect

    assert "tier" not in {p for p in inspect.signature(pool.disarm_live_tier).parameters
                          if p not in ("strategy_ids", "root", "now", "reasons")}
    source = door.__file__
    text = open(source, encoding="utf-8").read()
    assert "LIVE_TIER_LIVE" not in text and 'live_tier"] = ' not in text


def test_all_disarms_every_armed_strategy(tmp_path, capsys):
    _pool(tmp_path, {"S1": "LIVE", "S2": "LIVE", "S3": "OBSERVATION"})
    assert door.main(["--all", "--disarmed-by", "thomas", "--reason", "halt",
                      "--root", str(tmp_path)]) == door.EXIT_OK
    assert set(_tiers(tmp_path).values()) == {"OBSERVATION"}
    assert "armed now: none" in capsys.readouterr().out


def test_list_reads_and_changes_nothing(tmp_path, capsys):
    _pool(tmp_path, {"S1": "LIVE", "S2": "OBSERVATION"})
    assert door.main(["--list", "--root", str(tmp_path)]) == door.EXIT_OK
    assert "S1" in capsys.readouterr().out
    assert _tiers(tmp_path) == {"S1": "LIVE", "S2": "OBSERVATION"}
    assert not (tmp_path / LEDGER_REL).exists(), "a read wrote a ledger event"


@pytest.mark.parametrize("argv", [
    ["--strategy-ids", "S1", "--disarmed-by", "t"],           # no reason
    ["--strategy-ids", "S1", "--reason", "r"],                # no operator
    ["--disarmed-by", "t", "--reason", "r"],                  # nothing named
    ["--all", "--strategy-ids", "S1", "--disarmed-by", "t", "--reason", "r"],
])
def test_an_incomplete_invocation_writes_nothing(tmp_path, argv):
    _pool(tmp_path, {"S1": "LIVE"})
    assert door.main([*argv, "--root", str(tmp_path)]) == door.EXIT_USAGE
    assert _tiers(tmp_path) == {"S1": "LIVE"}
