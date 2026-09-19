"""PR3a — the canonical strategy artifact (Thomas decisions 31-34).

One hash for what a strategy is. The candidate row, the approval Thomas answers, the pool entry the
router reads, and the arm that may spend money must all name the same one; and a pool entry whose
content no longer hashes to its stamp refuses the whole pool, as a spec that does not parse does.
"""

from __future__ import annotations

import json
import math

import pytest

import runtime.mvp_runtime.crypto.promotion as promotion_mod
import scripts.promote_strategy_candidates as promote_door
from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.approval_store import STORE_REL as APPROVAL_STORE_REL
from runtime.mvp_runtime.approval_store import ApprovalStore
from runtime.mvp_runtime.crypto import pool
from runtime.mvp_runtime.crypto import strategy_artifact as artifact_mod
from runtime.mvp_runtime.crypto.factory import NUMERIC_FEATURES
from runtime.mvp_runtime.crypto.strategy import StrategySpec
from runtime.mvp_runtime.errors import ApprovalBlocked, ToolError
from scripts.promote_strategy_candidates import run_promotion
from tests.test_mvp_runtime_crypto_promotion import (
    _current_bars_replayed, _current_cost_summary, _spec_dict,
)

NOW = timeutil.utc_now_iso()
SHA = artifact_mod.ARTIFACT_SHA256_FIELD
PARTS = artifact_mod.ARTIFACT_FIELD


@pytest.fixture(autouse=True)
def _machine_is_staged_for_arming(monkeypatch):
    """Arming LIVE reads the execution stage (PR1c); these tests are about the artifact."""
    from runtime.mvp_runtime.crypto import execution_stage as es

    staged = es.StageStatus(stage="LIVE_AUTONOMOUS", valid=True, reason_code=None,
                            recorded_stage="LIVE_AUTONOMOUS")
    for module in (promotion_mod, promote_door):
        monkeypatch.setattr(module, "resolve_execution_stage", lambda root=None, **kw: staged)


def _raw_spec():
    """A spec as a row may store it: an INTEGER condition value, which the parser makes a float."""
    return _spec_dict(entry_rules={"operator": "AND", "conditions": [
        {"feature": "close", "comparison": ">", "value": 3},
        {"feature": "rsi", "comparison": "<", "value": 0.1 + 0.2},
    ]})


def _row(**changes):
    """A candidate row with every part the artifact hashes, in values JSON does not round-trip for
    free: floats with long reprs, a tiny one, a negative one, an integer that must stay one."""
    spec = StrategySpec.from_dict(_raw_spec())
    evidence = {
        "closed_count": 60, "win_count": 21, "expectancy": 0.1 + 0.2,
        "robustness": {"verdict": "PROVISIONAL", "holdout_status": "CONFIRMED"},
        "bars_replayed": _current_bars_replayed(spec),
        "cost_summary": _current_cost_summary(),
        "regime_breakdown": {"per_regime": {
            "trend_up": {"trades": 40, "total_r": 1e-7},
            "range": {"trades": 20, "total_r": -0.30000000000000004},
        }},
        "distribution_reference": {"rsi": {"mean": 51.123456789012345, "std": 1e-7},
                                   "close": {"mean": 60000, "std": 0.1 + 0.2}},
        "entry_cost_door": {"applied": True, "max_entry_cost_r": 0.25, "refused_entries": 3},
        "liquidation_guard": {"applied": True, "assumed_leverage": 5,
                              "maintenance_margin_rate": 0.004, "refused_entries": 0},
        "cooldown_door": {"applied": True, "cooldown_bars": 2, "skipped_entries": 7},
    }
    row = {
        "strategy_id": spec.strategy_id, "strategy_rule_hash": spec.strategy_rule_hash,
        "generation_id": "GEN-001", "status": "BACKTESTED", "champion_score": 0.1 + 0.2,
        "strategy_spec": _raw_spec(), "backtest_evidence": evidence,
        "evidence_input_sha256": "sha256:test", "provenance": "mvp_factory",
    }
    for path, value in changes.items():
        target = row
        *parents, leaf = path.split(".")
        for key in parents:
            target = target[key]
        target[leaf] = value
    return row


def _seed(tmp_path, *rows):
    pool.append_candidates([json.loads(json.dumps(r)) for r in rows], root=tmp_path)
    return pool.read_candidates(tmp_path)


def _install(tmp_path, **kw):
    """The real door, at OBSERVATION with no approval unless told otherwise."""
    args = dict(selectors=["S1"], promoted_by="Thomas", reason="r", keep_active=False,
                live_tier="OBSERVATION", root=tmp_path, now=NOW, without_approval=True)
    args.update(kw)
    return run_promotion(**args)


# --- the hash ------------------------------------------------------------------------------------

def test_the_row_and_the_entry_the_door_builds_are_one_artifact(tmp_path):
    [row] = _seed(tmp_path, _row())
    summary = _install(tmp_path)
    [entry] = pool.load_active_pool(tmp_path)["active_strategies"]
    assert artifact_mod.from_pool_entry(entry) == artifact_mod.from_candidate(row)
    assert entry[SHA] == artifact_mod.candidate_artifact_sha256(row)
    # The ledger names the artifact each candidate was installed as, and the tier it went into.
    assert summary["promoted_artifacts"] == [[pool.candidate_id(row), entry[SHA]]]
    assert summary["live_tier"] == "OBSERVATION"


def test_the_pool_file_round_trip_keeps_every_awkward_float(tmp_path):
    """The deploy-safety test. Nothing on the host exercises the recompute until the first stamped
    promotion, and under decision 34 a repr that drifts between the door and the next read would
    stop routing on every context. So: through the real door, onto disk, read back."""
    [row] = _seed(tmp_path, _row())
    _install(tmp_path)
    on_disk = json.loads(pool.pool_path(tmp_path).read_text(encoding="utf-8"))
    [entry] = on_disk["active_strategies"]
    assert artifact_mod.entry_artifact_problem(entry) is None
    assert artifact_mod.from_pool_entry(entry) == artifact_mod.from_candidate(row)
    # The integer condition value was stored as the row gave it, and both sides read it as a float.
    assert entry["strategy_spec"]["entry_rules"]["conditions"][0]["value"] == 3
    assert artifact_mod.from_pool_entry(entry)["spec_fingerprint"]["entry_rules"]["conditions"][0]["value"] == 3.0
    assert pool.load_active_pool(tmp_path) == on_disk


def test_the_spec_is_hashed_as_the_parser_reads_it():
    """The door copies the row's spec verbatim, so today both sides hash the same bytes. A writer
    that re-serializes it — the parser's own form, floats and its rule hash included — has not
    changed the strategy, and must not change the artifact."""
    row = _row()
    reparsed = {**row, "strategy_spec": StrategySpec.from_dict(row["strategy_spec"]).to_dict()}
    assert reparsed["strategy_spec"] != row["strategy_spec"]
    assert artifact_mod.candidate_artifact_sha256(reparsed) == artifact_mod.candidate_artifact_sha256(row)


def test_what_the_artifact_leaves_out_does_not_move_it(tmp_path):
    """The display id is not identity (decision 31): the door renames on a collision, and the same
    strategy under either name is the same artifact. Nor are the fields the pool's other writers own."""
    _seed(tmp_path, _row())
    _install(tmp_path)
    [entry] = pool.load_active_pool(tmp_path)["active_strategies"]
    moved = {**entry, "strategy_id": "S1-GEN-001", "status": "SUSPENDED",
             "lifecycle_consecutive_failures": 3, "lifecycle_reasons": ["x"],
             pool.LIVE_TIER_FIELD: pool.LIVE_TIER_LIVE, pool.LIVE_TIER_APPROVAL_FIELD: "appr_1",
             "live_tier_updated_at": NOW, "promoted_by": "someone", "promoted_at": NOW,
             # What the entry inherited is the pool's fact, bound by the approval (PR3c).
             "predecessor_lineage_keys": ["cand:cand_retired", "gen:GEN-000:deadbeef"]}
    assert artifact_mod.entry_artifact_problem(moved) is None


@pytest.mark.parametrize("path,value", [
    ("champion_score", 0.31),
    ("strategy_spec.risk_constraints.max_risk_per_trade_R", 0.5),
    ("strategy_spec.exit_rules.target_atr", 2.5),
    ("backtest_evidence.expectancy", 0.29),                      # the evidence, by its hash
    ("evidence_input_sha256", "sha256:other"),                  # a new candle window: a new instance
    ("generation_id", "GEN-002"),
], ids=lambda v: str(v).rsplit(".", 1)[-1])
def test_the_row_s_own_fields_move_its_artifact(path, value):
    base = artifact_mod.candidate_artifact_sha256(_row())
    changed = _row(**{path: value})
    if path.startswith("strategy_spec."):
        changed["strategy_rule_hash"] = StrategySpec.from_dict(changed["strategy_spec"]).strategy_rule_hash
    assert artifact_mod.candidate_artifact_sha256(changed) != base


# Each part of the body, changed alone on the entry that carries it. On the row most of these live
# inside `backtest_evidence`, whose whole hash is in the artifact too, so a row-side change cannot
# show that the part itself is hashed; the entry carries each one separately.
@pytest.mark.parametrize("path,value", [
    ("regime_evidence.range.total_r", 0.5),
    ("distribution_reference.rsi.mean", 50.0),
    ("champion_score", 0.31),
    ("strategy_rule_hash", "h_other"),
    ("candidate_id", "cand_other"),
    ("generation_id", "GEN-002"),
    (f"{PARTS}.cost_basis.taker_fee_bps", 6.0),
    (f"{PARTS}.risk_assumptions.entry_cost_door.max_entry_cost_r", 0.3),
    (f"{PARTS}.risk_assumptions.liquidation_guard.assumed_leverage", 10),
    (f"{PARTS}.risk_assumptions.liquidation_guard.maintenance_margin_rate", 0.005),
    (f"{PARTS}.risk_assumptions.cooldown_door.cooldown_bars", 3),
    (f"{PARTS}.evidence.evidence_input_sha256", "sha256:other"),
    (f"{PARTS}.evidence.backtest_evidence_sha256", "sha256:other"),
    (f"{PARTS}.evidence.closed_count", 61),
    (f"{PARTS}.evidence.backtest_win_rate", 0.4),
], ids=lambda v: str(v).rsplit(".", 1)[-1])
def test_each_part_of_the_entry_is_in_the_hash(tmp_path, path, value):
    _seed(tmp_path, _row())
    _install(tmp_path)
    [entry] = pool.load_active_pool(tmp_path)["active_strategies"]
    base = artifact_mod.artifact_sha256(artifact_mod.from_pool_entry(entry))
    changed = json.loads(json.dumps(entry))
    _edit(changed, path, value)
    assert artifact_mod.artifact_sha256(artifact_mod.from_pool_entry(changed)) != base


def test_a_spec_condition_is_in_the_hash_through_the_fingerprint(tmp_path):
    _seed(tmp_path, _row())
    _install(tmp_path)
    [entry] = pool.load_active_pool(tmp_path)["active_strategies"]
    changed = json.loads(json.dumps(entry))
    changed["strategy_spec"]["entry_rules"]["conditions"][1]["value"] = 0.4
    assert artifact_mod.artifact_sha256(artifact_mod.from_pool_entry(changed)) != entry[SHA]


def test_what_the_spec_carries_beside_its_rules_does_not_move_it(monkeypatch):
    """The review of #894: the body used `StrategySpec.to_dict()`, which has no stability rule —
    #461 added `venue` to it unconditionally. A change like that would have moved every stamp on
    disk at the next deploy and refused the pool on every context. The spec is hashed as its rule
    fingerprint, which every stored rule hash already pins."""
    base = artifact_mod.candidate_artifact_sha256(_row())
    original = StrategySpec.to_dict
    monkeypatch.setattr(StrategySpec, "to_dict", lambda self: {**original(self), "added_later": 1})
    assert artifact_mod.candidate_artifact_sha256(_row()) == base
    row = _row()
    row["strategy_spec"] = {**row["strategy_spec"], "strategy_version": "9.9", "venue": "binance_futures",
                            "created_by": "someone else", "status": "PAPER_ACTIVE"}
    assert artifact_mod.candidate_artifact_sha256(row) == base


# --- the format is frozen ------------------------------------------------------------------------
#
# Every read recomputes the stamps on disk with the code deployed then. A change to the v1 format is
# therefore a writer that moves every stamp at once, and decision 34 turns that into no routing on any
# context. These literals are the format: if one fails, do not update it — a new format is a new
# version, and v1 stamps must keep verifying (see the module docstring).

_GOLDEN_SPEC = {
    "schema_version": "strategy_spec.v1", "strategy_id": "S1", "strategy_version": "1.0",
    "strategy_family": "breakout", "symbol_scope": ["BTCUSDT"], "timeframe": "4h", "direction": "long",
    "entry_rules": {"operator": "AND", "conditions": [
        {"feature": "close", "comparison": ">", "value": 3},
        {"feature": "rsi", "comparison": "<", "value": 0.30000000000000004}]},
    "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 10},
    "risk_constraints": {"max_risk_per_trade_R": 1.0},
}
_GOLDEN_ROW = {
    "strategy_id": "S1", "strategy_rule_hash": "7c6d3f81aec1bed26d9c2ef68e4e648e61ad340e5b809987afe8bb8c2abc9088",
    "generation_id": "GEN-GOLDEN", "status": "BACKTESTED", "champion_score": 0.30000000000000004,
    "strategy_spec": _GOLDEN_SPEC,
    "backtest_evidence": {
        "closed_count": 60, "win_count": 21, "expectancy": 0.125,
        "cost_summary": {"cost_model": {"taker_fee_bps": 5.0, "maker_fee_bps": 2.0, "slippage_bps": 3.0,
                                         "stop_slippage_bps": 1.4, "funding_bps_per_interval": 1.0,
                                         "funding_source": "venue_history"}},
        "regime_breakdown": {"per_regime": {"trend_up": {"trades": 40, "total_r": 1e-07},
                                            "range": {"trades": 20, "total_r": -0.30000000000000004}}},
        "distribution_reference": {"rsi": {"mean": 51.123456789012345, "std": 1e-07}},
        "entry_cost_door": {"applied": True, "max_entry_cost_r": 0.25, "refused_entries": 3},
        "liquidation_guard": {"applied": True, "assumed_leverage": 5, "maintenance_margin_rate": 0.004,
                              "refused_entries": 0},
        "cooldown_door": {"applied": True, "cooldown_bars": 2, "skipped_entries": 7},
    },
    "evidence_input_sha256": "sha256:golden", "provenance": "mvp_factory",
}
_GOLDEN_SHA = "sha256:05fa07fb5c776a20bf8fdeaf5e7f1dcc60035804eb9afc70c343a5dc73963cbe"
# As the door installs it, written as the pool file would hold it.
_GOLDEN_ENTRY = {
    "strategy_id": "S1-GEN-GOLDEN", "candidate_id": "cand_2dda9c51245fcab9414f", "status": "PAPER_ACTIVE",
    "live_tier": "OBSERVATION", "champion_score": 0.30000000000000004,
    "strategy_rule_hash": "7c6d3f81aec1bed26d9c2ef68e4e648e61ad340e5b809987afe8bb8c2abc9088",
    "generation_id": "GEN-GOLDEN", "strategy_spec": _GOLDEN_SPEC,
    "regime_evidence": {"trend_up": {"trades": 40, "total_r": 1e-07},
                        "range": {"trades": 20, "total_r": -0.30000000000000004}},
    "distribution_reference": {"rsi": {"mean": 51.123456789012345, "std": 1e-07}},
    "strategy_artifact": {
        "version": "strategy_artifact.v1",
        "cost_basis": {"taker_fee_bps": 5.0, "maker_fee_bps": 2.0, "slippage_bps": 3.0,
                       "stop_slippage_bps": 1.4, "funding_bps_per_interval": 1.0,
                       "funding_source": "venue_history"},
        "risk_assumptions": {"entry_cost_door": {"applied": True, "max_entry_cost_r": 0.25},
                             "liquidation_guard": {"applied": True, "assumed_leverage": 5,
                                                   "maintenance_margin_rate": 0.004},
                             "cooldown_door": {"applied": True, "cooldown_bars": 2}},
        "evidence": {"evidence_input_sha256": "sha256:golden",
                     "backtest_evidence_sha256":
                         "sha256:f94bf93316ba036f9bfd03bccf6e29b3aa557fa719bde39eaea533c66b99f534",
                     "closed_count": 60, "backtest_win_rate": 0.35},
    },
    "strategy_artifact_sha256": _GOLDEN_SHA,
    "promoted_by": "Thomas", "promoted_at": "2026-09-18T00:00:00Z",
}


def test_the_v1_format_of_a_row_is_frozen():
    assert artifact_mod.STRATEGY_ARTIFACT_VERSION == "strategy_artifact.v1"
    assert artifact_mod.candidate_artifact_sha256(_GOLDEN_ROW) == _GOLDEN_SHA


def test_a_v1_stamp_written_today_verifies_on_every_later_read():
    entry = json.loads(json.dumps(_GOLDEN_ENTRY))
    assert artifact_mod.entry_artifact_problem(entry) is None
    assert artifact_mod.from_pool_entry(entry) == artifact_mod.from_candidate(_GOLDEN_ROW)
    assert artifact_mod.carried_parts(_GOLDEN_ROW) == _GOLDEN_ENTRY["strategy_artifact"]


def test_a_row_older_than_the_evidence_it_would_summarize_still_has_one():
    """The 41 imported rows carry no backtest evidence and no cost model. Their parts are None, the
    same None on both sides, and the artifact is still one hash."""
    row = _row()
    row.pop("backtest_evidence")
    row.pop("evidence_input_sha256")
    body = artifact_mod.from_candidate(row)
    assert body["cost_basis"] is None and body["admission"] == {
        "regime_evidence": None, "distribution_reference": None}
    assert body["risk_assumptions"] == {"entry_cost_door": None, "liquidation_guard": None,
                                        "cooldown_door": None}
    assert body["evidence"] == {"evidence_input_sha256": None, "backtest_evidence_sha256": None,
                                "closed_count": None, "backtest_win_rate": None}
    assert artifact_mod.artifact_sha256(body).startswith("sha256:")


def test_the_feature_vocabulary_the_evidence_is_keyed_by_hashes():
    """The kernel's canonical JSON refuses secret-bearing key names. The distribution reference is
    keyed by feature names, so every feature the factory can mine on must pass."""
    reference = {name: {"mean": 1.5, "std": 0.5} for name in sorted(NUMERIC_FEATURES)}
    row = _row(**{"backtest_evidence.distribution_reference": reference})
    assert artifact_mod.candidate_artifact_sha256(row).startswith("sha256:")


@pytest.mark.parametrize("change", [
    {"backtest_evidence.regime_breakdown.per_regime.range.total_r": math.nan},
    {"backtest_evidence.distribution_reference.rsi.std": math.inf},
    {"strategy_spec": {"x": 1}},
    {"strategy_spec": None},
], ids=["nan", "inf", "spec-does-not-parse", "no-spec"])
def test_what_canonical_json_refuses_is_unhashable(change):
    with pytest.raises(ToolError) as exc:
        artifact_mod.candidate_artifact_sha256(_row(**change))
    assert exc.value.reason_code == artifact_mod.STRATEGY_ARTIFACT_UNHASHABLE


def test_the_win_rate_stays_inside_the_artifact(tmp_path):
    """The lifecycle reads `backtest_win_rate` at the entry's top level, and a value there switches
    on the win-rate probation rule — a separate decision (investigation §11)."""
    _seed(tmp_path, _row())
    _install(tmp_path)
    [entry] = pool.load_active_pool(tmp_path)["active_strategies"]
    assert "backtest_win_rate" not in entry
    assert entry[PARTS]["evidence"]["backtest_win_rate"] == 21 / 60


# --- the pool read: decision 34 ------------------------------------------------------------------

def _stamped_pool(tmp_path):
    """One entry the door installed and one that predates the artifact."""
    _seed(tmp_path, _row())
    _install(tmp_path)
    installed = pool.load_active_pool(tmp_path)
    legacy = {"strategy_id": "S0", "status": "SUSPENDED", "champion_score": 0.2,
              "strategy_spec": _spec_dict(strategy_id="S0", direction="short")}
    installed["active_strategies"].append(legacy)
    pool.install_active_pool(installed, root=tmp_path)
    return installed


def _edit(entry, path, value):
    target = entry
    *parents, leaf = path.split(".")
    for key in parents:
        target = target[key]
    if value is _DROP:
        target.pop(leaf)
    else:
        target[leaf] = value


_DROP = object()


@pytest.mark.parametrize("path,value", [
    ("regime_evidence.range.total_r", 5.0),
    ("regime_evidence", None),                      # the #743 shape: the evidence went missing
    ("distribution_reference.rsi.mean", 1.0),
    ("distribution_reference", _DROP),
    ("champion_score", 9.0),
    ("strategy_spec.entry_rules.conditions.0.value", 4),
    ("strategy_rule_hash", "h_other"),
    ("candidate_id", "cand_other"),
    ("generation_id", "GEN-999"),
    (f"{PARTS}.cost_basis.taker_fee_bps", 1.0),
    (f"{PARTS}.evidence.backtest_evidence_sha256", "sha256:other"),
    (f"{PARTS}.risk_assumptions.cooldown_door", None),
    (f"{PARTS}.version", "strategy_artifact.v0"),
    (SHA, "sha256:" + "0" * 64),
    (SHA, _DROP),                                   # half a stamp
    (PARTS, _DROP),
], ids=lambda v: "drop" if v is _DROP else str(v)[:32])
def test_a_stamped_entry_whose_content_moved_refuses_the_whole_pool(tmp_path, path, value):
    installed = _stamped_pool(tmp_path)
    tampered = json.loads(json.dumps(installed))
    entry = tampered["active_strategies"][0]
    if path.startswith("strategy_spec.entry_rules.conditions.0."):
        entry["strategy_spec"]["entry_rules"]["conditions"][0]["value"] = value
    else:
        _edit(entry, path, value)
    pool.pool_path(tmp_path).write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        pool.load_active_pool(tmp_path)
    assert exc.value.reason_code == artifact_mod.STRATEGY_POOL_ARTIFACT_MISMATCH
    # The install door refuses to write what the read door would refuse.
    before = pool.pool_path(tmp_path).read_text(encoding="utf-8")
    with pytest.raises(ToolError):
        pool.install_active_pool(tampered, root=tmp_path)
    assert pool.pool_path(tmp_path).read_text(encoding="utf-8") == before


def test_an_entry_that_predates_the_artifact_loads_as_before(tmp_path):
    installed = _stamped_pool(tmp_path)
    loaded = pool.load_active_pool(tmp_path)
    assert loaded == installed
    legacy = loaded["active_strategies"][1]
    assert not artifact_mod.entry_is_bound(legacy) and artifact_mod.entry_artifact_problem(legacy) is None


def test_the_disarm_door_still_narrows_a_pool_the_read_refuses(tmp_path):
    """The review of #894: a refused pool routes nothing, but an operator repairing it must be able
    to take an entry off the money path first, or it is armed again the moment the pool loads."""
    import scripts.disarm_live_strategies as disarm_door

    installed = _stamped_pool(tmp_path)
    armed = json.loads(json.dumps(installed))
    armed["active_strategies"][0][pool.LIVE_TIER_FIELD] = pool.LIVE_TIER_LIVE
    armed["active_strategies"][0]["champion_score"] = 9.0          # the stamp no longer holds
    pool.pool_path(tmp_path).write_text(json.dumps(armed), encoding="utf-8")
    with pytest.raises(ToolError):
        pool.load_active_pool(tmp_path)
    summary = disarm_door.run_disarm(strategy_ids=["S1"], disarmed_by="Thomas", reason="repair",
                                     root=tmp_path, now=NOW)
    assert summary["armed_before"] == ["S1"] and summary["armed_after"] == [] and summary["disarmed"] == 1
    # Narrowed, and still refused: nothing else was rewritten.
    reread = json.loads(pool.pool_path(tmp_path).read_text(encoding="utf-8"))["active_strategies"][0]
    assert reread[pool.LIVE_TIER_FIELD] == pool.LIVE_TIER_OBSERVATION and reread["champion_score"] == 9.0
    with pytest.raises(ToolError) as exc:
        pool.load_active_pool(tmp_path)
    assert exc.value.reason_code == artifact_mod.STRATEGY_POOL_ARTIFACT_MISMATCH


def test_adding_to_a_pool_the_read_refuses_is_refused_as_blocked(tmp_path):
    _stamped_pool(tmp_path)
    tampered = json.loads(pool.pool_path(tmp_path).read_text(encoding="utf-8"))
    tampered["active_strategies"][0]["champion_score"] = 9.0
    pool.pool_path(tmp_path).write_text(json.dumps(tampered), encoding="utf-8")
    _seed(tmp_path, _row(**{"strategy_spec": _spec_dict(strategy_id="S2", direction="short"),
                            "strategy_id": "S2"}))
    with pytest.raises(SystemExit) as exc:
        _install(tmp_path, selectors=["S2"], keep_active=True)
    assert "BLOCKED STRATEGY_POOL_ARTIFACT_MISMATCH" in str(exc.value)


def test_the_pool_s_other_writers_touch_no_hashed_field(tmp_path):
    """`update_statuses` runs every cycle and `disarm_live_tier` on every allowance breach. Either
    touching a hashed field would refuse the pool on the next read (decision 34)."""
    installed = _stamped_pool(tmp_path)
    stamp = installed["active_strategies"][0][SHA]
    from runtime.mvp_runtime.crypto.candidate_identity import lineage_of

    judged = installed["active_strategies"][0]
    pool.update_statuses([{"strategy_id": "S1", "previous_status": judged["status"], "new_status": "WARNING",
                           "consecutive_failures": 1, "created_at_utc": NOW, "reasons": ["metric_warning"],
                           **lineage_of(judged)}], root=tmp_path)
    reread = pool.load_active_pool(tmp_path)["active_strategies"][0]
    assert reread["status"] == "WARNING" and reread[SHA] == stamp
    # Armed by hand only to give the disarm door something to move.
    armed = pool.load_active_pool(tmp_path)
    armed["active_strategies"][0][pool.LIVE_TIER_FIELD] = pool.LIVE_TIER_LIVE
    pool.install_active_pool(armed, root=tmp_path)
    assert pool.disarm_live_tier(["S1"], root=tmp_path, now=NOW) == 1
    reread = pool.load_active_pool(tmp_path)["active_strategies"][0]
    assert reread[pool.LIVE_TIER_FIELD] == pool.LIVE_TIER_OBSERVATION and reread[SHA] == stamp


# --- the door: candidate = approval = entry ------------------------------------------------------

def _approval(tmp_path, candidates, *, live_tier, pairs=True, content=None, approval_id=None):
    """An APPROVED record binding ``candidates`` as they stand, stored where the door reads it."""
    artifacts = promotion_mod.candidate_artifacts(candidates)
    snapshot = {
        "action_type": promotion_mod.PROMOTION_ACTION_TYPE,
        "content_sha256": content or promotion_mod.content_sha256_of(
            candidates, keep_active=False, live_tier=live_tier, root=tmp_path),
        "expires_at": "2999-01-01T00:00:00Z",
        "normalized_parameters": {"live_tier": live_tier, **({"artifacts": promotion_mod.artifact_pairs(
            [c["candidate_id"] for c in candidates], artifacts)} if pairs else {})},
    }
    record = {"approval_id": approval_id or f"approval_{live_tier.lower()}_{int(pairs)}", "status": "APPROVED",
              "validity": {"issued_at": NOW, "expires_at": "2999-01-01T00:00:00Z"},
              "decision": {"decision_reason": "yes", "decided_at": NOW},
              "approved_action_snapshot": snapshot}
    ApprovalStore(tmp_path / APPROVAL_STORE_REL).append([record])
    return record["approval_id"]


def test_a_row_appended_under_the_same_id_after_the_ask_does_not_install(tmp_path):
    """`resolve_candidates` keeps the latest row per candidate id, and a re-score over the same
    candles mints the same id. Until v5 the approval bound the id alone, so the row installed could
    be one Thomas never saw. The artifact makes the content part of what the approval binds."""
    [row] = _seed(tmp_path, _row())
    approval_id = _approval(tmp_path, pool.resolve_candidates(["S1"], tmp_path), live_tier="OBSERVATION")
    _seed(tmp_path, _row(champion_score=0.9))        # same generation, rule and candles: same id
    assert pool.resolve_candidates(["S1"], tmp_path)[0]["candidate_id"] == pool.candidate_id(row)
    with pytest.raises(SystemExit) as exc:
        _install(tmp_path, without_approval=False, approval_id=approval_id)
    assert "APPROVAL_CONTENT_MISMATCH" in str(exc.value)
    assert pool.load_active_pool(tmp_path) == {"active_strategies": []}


def test_the_door_re_hashes_the_rows_it_copies(tmp_path, monkeypatch):
    """Verification reads the store, then the door reads it again to copy. A row landing between the
    two reads must not install content the approval does not bind."""
    _seed(tmp_path, _row())
    asked = pool.resolve_candidates(["S1"], tmp_path)
    approval_id = _approval(tmp_path, asked, live_tier="OBSERVATION")
    verified = ApprovalStore(tmp_path / APPROVAL_STORE_REL).get(approval_id)

    def _verify_then_a_row_lands(*a, **k):
        _seed(tmp_path, _row(champion_score=0.9))
        return verified

    monkeypatch.setattr(promote_door.promotion_mod, "verify_promotion_approval", _verify_then_a_row_lands)
    with pytest.raises(SystemExit) as exc:
        _install(tmp_path, without_approval=False, approval_id=approval_id)
    assert "APPROVAL_CONTENT_MISMATCH" in str(exc.value)
    assert pool.load_active_pool(tmp_path) == {"active_strategies": []}


def test_the_door_refuses_an_entry_that_is_not_its_candidate(tmp_path, monkeypatch):
    """The door copies the admission evidence through `pool.admission_evidence`; the artifact is
    computed from the row through the leaf's own. A door whose copy drifts refuses rather than
    installing an entry that hashes to nothing Thomas saw."""
    _seed(tmp_path, _row())
    monkeypatch.setattr(promote_door.pool_store, "admission_evidence",
                        lambda c: {"regime_evidence": None, "distribution_reference": None})
    with pytest.raises(SystemExit) as exc:
        _install(tmp_path)
    assert "STRATEGY_ARTIFACT_DIVERGED" in str(exc.value)
    assert not pool.pool_path(tmp_path).exists()


def test_a_live_install_needs_the_pairs_its_arm_will_be_checked_against(tmp_path):
    """The order-time check reads the pairs off the signed parameters (`live_arm_problem`). An
    approval whose content verifies but whose parameters pair nothing would install an arm no order
    could leave under; verification refuses it for LIVE and does not ask it of a paper change."""
    _seed(tmp_path, _row())
    candidates = pool.resolve_candidates(["S1"], tmp_path)
    store = ApprovalStore(tmp_path / APPROVAL_STORE_REL)
    for live_tier, refused in (("LIVE", True), ("OBSERVATION", False)):
        approval_id = _approval(tmp_path, candidates, live_tier=live_tier, pairs=False)
        verify = lambda: promotion_mod.verify_promotion_approval(  # noqa: E731
            store.get(approval_id), selectors=["S1"], keep_active=False, live_tier=live_tier,
            root=tmp_path, now=NOW)
        if refused:
            with pytest.raises(ApprovalBlocked) as exc:
                verify()
            assert exc.value.reason_code == "APPROVAL_ARTIFACT_UNSIGNED"
        else:
            assert verify()["approval_id"] == approval_id


def test_an_approval_asked_before_v5_verifies_nothing(tmp_path):
    """A v4 content hash lacks the pairs, so no promotion recomputed today matches it."""
    [row] = _seed(tmp_path, _row())
    v4_shaped = promotion_mod.integrity.sha256_value({
        "hash_version": "strategy_promotion.v4", "candidate_ids": [pool.candidate_id(row)],
        "rule_hashes": [row["strategy_rule_hash"]], "keep_active": False, "live_tier": "OBSERVATION",
        "reactivated_candidate_ids": [],
    })
    approval_id = _approval(tmp_path, pool.resolve_candidates(["S1"], tmp_path), live_tier="OBSERVATION",
                            content=v4_shaped)
    with pytest.raises(SystemExit) as exc:
        _install(tmp_path, without_approval=False, approval_id=approval_id)
    assert "APPROVAL_CONTENT_MISMATCH" in str(exc.value)


def test_a_live_install_is_armed_as_the_artifact_its_approval_pairs(tmp_path):
    [row] = _seed(tmp_path, _row())
    candidates = pool.resolve_candidates(["S1"], tmp_path)
    approval_id = _approval(tmp_path, candidates, live_tier="LIVE")
    _install(tmp_path, without_approval=False, approval_id=approval_id, live_tier="LIVE")
    installed = pool.load_active_pool(tmp_path)
    [(strategy_id, armed)] = pool.live_arm_entries(installed).items()
    assert armed[SHA] == artifact_mod.candidate_artifact_sha256(row)
    assert pool.live_arm_unsound(armed) is None
    assert pool.live_arm_approvals(installed) == {strategy_id: approval_id}
    signed = ApprovalStore(tmp_path / APPROVAL_STORE_REL).get(approval_id)[
        "approved_action_snapshot"]["normalized_parameters"]["artifacts"]
    assert [armed["candidate_id"], armed[SHA]] in signed
