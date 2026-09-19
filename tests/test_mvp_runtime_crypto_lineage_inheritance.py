"""PR3c-1 — an entry that replaced a retired rule's entries is judged on their record too (Thomas
decision 41).

The same rule is the same strategy. When the promotion door returns a retired rule to trading under
a new candidate (decision 40, PR3c-2), the new entry replaces the retired entries that held the rule
and records the lineage keys that named them (`candidate_identity.PREDECESSOR_KEYS_FIELD`, the
3b-3 seal rule: candidate and generation keys, the display-id key only when an entry has neither).
Every reader of an entry's record reads them with its own: the lifecycle, the router's realized
ranking, the live allowance, and the drawdown guard's routable set. Keys, not candidate ids: 41 of
the pool's 121 entries (2026-09-19) have no candidate id, and their record keys on `gen:`.

This half lands first and is inert: no entry carries the field until the door writes one.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import feedback, guards, pool
from runtime.mvp_runtime.crypto import risk_limits as rl
from runtime.mvp_runtime.crypto.candidate_identity import (
    PREDECESSOR_KEYS_FIELD,
    entry_attribution_keys,
    own_attribution_keys,
    precise_lineage_keys,
    predecessor_keys,
)
from runtime.mvp_runtime.crypto.lifecycle import run_lifecycle
from runtime.mvp_runtime.crypto.live_allowance import BREACH_CONSECUTIVE, evaluate_live_allowance
from runtime.mvp_runtime.crypto.paper import route_entries
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-09-19T12:00:00Z"
ROW = {"timestamp": "2026-07-22T00:00:00Z", "close": 105.0, "ma20": 100.0, "adx": 25.0, "atr": 2.0}


def _spec(sid):
    return {
        "schema_version": "strategy_spec.v1", "strategy_id": sid, "strategy_version": "1.0",
        "strategy_family": "breakout", "symbol_scope": ["BTCUSDT"], "timeframe": "1d", "direction": "long",
        "entry_rules": {"operator": "AND", "conditions": [
            {"feature": "close", "comparison": ">", "value_from": "ma20"},
            {"feature": "adx", "comparison": ">=", "value": 20.0}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 10},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }


def _entry(sid, cand, *, status="PAPER_ACTIVE", generation="GEN-9", score=0.5, inherits=None, **extra):
    entry = {"strategy_id": sid, "candidate_id": cand, "generation_id": generation,
             "strategy_rule_hash": f"hash-{cand}", "status": status, "champion_score": score,
             "strategy_spec": _spec(sid), **extra}
    if inherits is not None:
        entry[PREDECESSOR_KEYS_FIELD] = list(inherits)
    return entry


def _pool(*entries):
    return {"pool_version": "active_strategy_pool.v1", "active_strategies": list(entries)}


# The retired lineage A, as its successor records it.
A_KEYS = ["cand:cand_A", "gen:GEN-1:hash-cand_A"]


# --- the keys ------------------------------------------------------------------------------------

def test_an_entry_accepts_what_it_inherited():
    successor = _entry("S_B", "cand_B", inherits=A_KEYS)
    assert predecessor_keys(successor) == set(A_KEYS)
    assert own_attribution_keys(successor) == {"cand:cand_B", "gen:GEN-9:hash-cand_B", "sid:S_B"}
    assert entry_attribution_keys(successor) == own_attribution_keys(successor) | set(A_KEYS)


def test_an_entry_that_replaced_nothing_reads_exactly_as_before():
    """Inert until the door writes the field: every entry on the host today."""
    entry = _entry("S1", "cand_1")
    assert PREDECESSOR_KEYS_FIELD not in entry
    assert entry_attribution_keys(entry) == own_attribution_keys(entry) == {
        "cand:cand_1", "gen:GEN-9:hash-cand_1", "sid:S1"}


@pytest.mark.parametrize("value", ["cand:cand_A", ["S1", None, 5, "cand:"], {"cand:cand_A": 1}])
def test_a_value_that_names_no_lineage_is_not_read(value):
    assert predecessor_keys({**_entry("S_B", "cand_B"), PREDECESSOR_KEYS_FIELD: value}) == set()


def test_a_successor_records_what_names_a_lineage_and_what_it_inherited():
    """The 3b-3 seal rule, shared: candidate and generation keys, the display-id key only when an entry
    has neither — and, transitively, whatever the replaced entry had inherited."""
    assert precise_lineage_keys(_entry("S_A", "cand_A", generation="GEN-1")) == set(A_KEYS)
    imported = {"strategy_id": "S7", "status": "SUSPENDED", "generation_id": "GEN-0", "strategy_rule_hash": "h7"}
    assert precise_lineage_keys(imported) == {"gen:GEN-0:h7"}
    assert precise_lineage_keys({"strategy_id": "S8", "status": "SUSPENDED"}) == {"sid:S8"}
    middle = _entry("S_B", "cand_B", generation="GEN-2", inherits=A_KEYS)
    assert precise_lineage_keys(middle) == {"cand:cand_B", "gen:GEN-2:hash-cand_B", *A_KEYS}


# --- every reader of the record ------------------------------------------------------------------

def _losses(cand, n=30, r=-0.1):
    return [{"outcome_closed": True, "result_R": r, "candidate_id": cand, "strategy_id": "ignored",
             "created_at_utc": f"2026-09-{1 + i % 17:02d}T00:00:00Z"} for i in range(n)]


def _judged(decision):
    return {k: decision[k] for k in ("new_status", "consecutive_failures", "reasons", "status_changed")}


def test_the_lifecycle_judges_a_successor_on_its_predecessors_record():
    record = _losses("cand_A")
    [as_predecessor] = run_lifecycle(_pool(_entry("S_A", "cand_A", generation="GEN-1")), record, now=NOW)
    assert as_predecessor["status_changed"] is True                  # the record condemns the rule
    [successor] = run_lifecycle(_pool(_entry("S_B", "cand_B", inherits=A_KEYS)), record, now=NOW)
    assert _judged(successor) == _judged(as_predecessor)
    [fresh] = run_lifecycle(_pool(_entry("S_B", "cand_B")), record, now=NOW)
    assert fresh["status_changed"] is False and fresh["reasons"] == []


def _closed(cand, r, n):
    return [{"outcome_closed": True, "result_R": r, "strategy_id": "S_old", "candidate_id": cand,
             "provenance": "mvp_paper_kernel"} for _ in range(n)]


def _route(*entries, stats):
    return route_entries(_pool(*entries), ROW, symbol="BTCUSDT", timeframe="1d", now=NOW, realized_stats=stats)


def test_the_router_ranks_a_successor_on_its_predecessors_record():
    stats = feedback.realized_by_lineage(_closed("cand_A", 0.4, 12))
    rival = _entry("S_C", "cand_C", generation="GEN-3", score=0.9)
    route = _route(_entry("S_B", "cand_B", score=0.1, inherits=A_KEYS), rival, stats=stats)
    assert route["primary_strategy_id"] == "S_B" and route["primary_priority_basis"] == "realized_expectancy"
    route = _route(_entry("S_B", "cand_B", score=0.1), rival, stats=stats)
    assert route["primary_strategy_id"] == "S_C"


def test_a_successor_s_record_is_its_own_and_its_predecessors_as_one():
    """Neither half reaches the sample floor alone; together they do, weighted by rows."""
    stats = feedback.realized_by_lineage(_closed("cand_A", 0.5, 6) + _closed("cand_B", 0.1, 6))
    rival = _entry("S_C", "cand_C", generation="GEN-3", score=0.9)
    route = _route(_entry("S_B", "cand_B", score=0.1, inherits=A_KEYS), rival, stats=stats)
    assert route["primary_strategy_id"] == "S_B" and route["primary_priority_basis"] == "realized_expectancy"


def _live(r, cand):
    return {"outcome_closed": True, "result_R": r, "candidate_id": cand}


def test_the_live_allowance_charges_a_successor_its_predecessors_losses():
    armed = _entry("S_B", "cand_B", inherits=A_KEYS, live_tier=pool.LIVE_TIER_LIVE)
    result = evaluate_live_allowance([_live(-1.0, "cand_A"), _live(-1.0, "cand_A")],
                                     live_routable_strategy_ids={"S_B"}, pool=_pool(armed))
    [breach] = result["breached"]
    assert breach["strategy_id"] == "S_B" and breach["lineage"] == "cand:cand_B"
    assert BREACH_CONSECUTIVE in breach["reasons"]
    fresh = {k: v for k, v in armed.items() if k != PREDECESSOR_KEYS_FIELD}
    assert evaluate_live_allowance([_live(-1.0, "cand_A"), _live(-1.0, "cand_A")],
                                   live_routable_strategy_ids={"S_B"}, pool=_pool(fresh))["breached"] == []


def test_the_allowance_reads_a_successor_s_rows_in_the_order_they_closed():
    """A streak is a sequence. The predecessor's win came between the successor's two losses, so the
    streak is one loss long — read key by key, the two losses would be adjacent and read as two."""
    armed = _entry("S_B", "cand_B", inherits=A_KEYS, live_tier=pool.LIVE_TIER_LIVE)
    rows = [_live(-1.0, "cand_B"), _live(0.5, "cand_A"), _live(-1.0, "cand_B")]
    assert evaluate_live_allowance(rows, live_routable_strategy_ids={"S_B"}, pool=_pool(armed))["breached"] == []


def _loss(cand, sid="S_A"):
    return {"outcome_closed": True, "result_R": -1.0, "strategy_id": sid, "candidate_id": cand,
            "created_at_utc": "2026-09-10T00:00:00Z"}


def test_a_sealed_predecessor_s_losses_return_when_its_successor_routes():
    """The drawdown rebase lets a retired lineage's losses leave the window only while no routable
    entry accepts its key (PR3b-3). A successor accepts it: the rule is trading again, so its
    losses are back."""
    sealed = rl.seal_drawdown_exclusion(["S_A"], _pool(_entry("S_A", "cand_A", status="SUSPENDED",
                                                              generation="GEN-1")))
    rows = [_loss("cand_A")]
    retired_only = _pool(_entry("S_C", "cand_C"))
    kept, _ = guards.drawdown_baseline(rows, excluded=sealed, routable=pool.routable_strategy_ids(retired_only),
                                       routable_lineages=pool.routable_lineage_keys(retired_only))
    assert kept == []
    back = _pool(_entry("S_B", "cand_B", inherits=A_KEYS))
    kept, summary = guards.drawdown_baseline(rows, excluded=sealed, routable=pool.routable_strategy_ids(back),
                                             routable_lineages=pool.routable_lineage_keys(back))
    assert kept == rows and summary["rows_excluded"] == 0


def test_sealing_a_successor_seals_what_it_inherited():
    retired = _entry("S_B", "cand_B", status="SUSPENDED", inherits=A_KEYS)
    assert rl.seal_drawdown_exclusion(["S_B"], _pool(retired)) == sorted(
        ["cand:cand_B", "gen:GEN-9:hash-cand_B", *A_KEYS])


# --- the pool keeps one owner per lineage --------------------------------------------------------

def _install(tmp_path, *entries):
    return pool.install_active_pool(_pool(*entries), root=tmp_path)


def test_a_lineage_inherited_by_two_entries_is_refused(tmp_path):
    with pytest.raises(ToolError) as refused:
        _install(tmp_path, _entry("S_B", "cand_B", inherits=A_KEYS), _entry("S_C", "cand_C", inherits=["cand:cand_A"]))
    assert refused.value.reason_code == "STRATEGY_POOL_DUPLICATE" and "cand:cand_A" in str(refused.value)


def test_inheriting_a_lineage_another_entry_holds_is_refused(tmp_path):
    with pytest.raises(ToolError) as refused:
        _install(tmp_path, _entry("S_A", "cand_A", status="SUSPENDED", generation="GEN-1"),
                 _entry("S_B", "cand_B", inherits=A_KEYS))
    assert refused.value.reason_code == "STRATEGY_POOL_DUPLICATE" and "S_A" in str(refused.value)


@pytest.mark.parametrize("value", ["cand:cand_A", ["S_A"], ["cand:cand_A", None]])
def test_an_inheritance_that_is_not_a_list_of_lineage_keys_is_refused(tmp_path, value):
    with pytest.raises(ToolError) as refused:
        _install(tmp_path, {**_entry("S_B", "cand_B"), PREDECESSOR_KEYS_FIELD: value})
    assert refused.value.reason_code == "STRATEGY_POOL_INVALID"


def test_the_read_door_refuses_what_the_install_door_would(tmp_path):
    """A pool written around the install door (a restore, a hand edit) is refused on read."""
    import json

    _install(tmp_path, _entry("S_B", "cand_B", inherits=A_KEYS), _entry("S_C", "cand_C"))
    path = pool.pool_path(tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))
    written["active_strategies"][1][PREDECESSOR_KEYS_FIELD] = ["cand:cand_A"]
    path.write_text(json.dumps(written), encoding="utf-8")
    with pytest.raises(ToolError) as refused:
        pool.load_active_pool(tmp_path)
    assert refused.value.reason_code == "STRATEGY_POOL_DUPLICATE"


def test_an_entry_may_inherit_its_own_generation(tmp_path):
    """A row re-scored in the same generation replaces its twin: the twin's generation key is the
    successor's own. One owner, not two."""
    twin_keys = ["cand:cand_A", "gen:GEN-9:hash-shared"]
    successor = {**_entry("S_B", "cand_B", inherits=twin_keys), "strategy_rule_hash": "hash-shared"}
    assert _install(tmp_path, successor) == 1


def test_a_display_name_is_not_an_owner(tmp_path):
    """`sid:` keys are the one imprecise join: an entry that later takes a replaced entry's display
    name does not make the pool unreadable."""
    assert _install(tmp_path, _entry("S_B", "cand_B", inherits=["sid:S_A"]), _entry("S_A", "cand_C")) == 2


def test_the_pool_as_it_stands_still_loads(tmp_path):
    """Two entries' own keys are not compared: the host's pool holds S008 and S008-GEN-696, one rule
    installed twice, both SUSPENDED, sharing `gen:GEN-696:…` (left as they are, Thomas decision 42)."""
    pair = [{**_entry(sid, cand, status="SUSPENDED", generation="GEN-696"), "strategy_rule_hash": "hash-s008"}
            for sid, cand in (("S008", "cand_1c52"), ("S008-GEN-696", "cand_07b1"))]
    assert _install(tmp_path, *pair) == 2
    assert len(pool.load_active_pool(tmp_path)["active_strategies"]) == 2
