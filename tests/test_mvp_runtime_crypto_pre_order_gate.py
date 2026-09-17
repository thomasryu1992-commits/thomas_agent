"""PR2b — the pre-order gate, its snapshot, the binding at the venue door, and the store.

Thomas's directive: every real order passes one gate immediately before submission, the gate
re-verifies everything, and the order references the gate's immutable snapshot by hash. Decisions
17 (the approved profile is a composite of existing records), 19 (a per-venue append-only store)
and 20 (the testnet entry passes the same gate).

The doors' own re-derivations are pinned beside each door; this file pins the gate, the seal, the
binding inside `submit_and_reconcile`, and the store.
"""

from __future__ import annotations

import json
import math

import pytest
from tests._helpers import FakeSnapshotStore, approved_snapshot, gate_stage, make_gate_authorization

from runtime.mvp_runtime.crypto import live_execution as lx
from runtime.mvp_runtime.crypto import pre_order_gate as g
from runtime.mvp_runtime.crypto import testnet_execution as testnet
from runtime.mvp_runtime.crypto.execution_stage import (
    PURPOSE_AUTONOMOUS,
    PURPOSE_PROBE,
    PURPOSE_TESTNET,
    StageStatus,
)
from runtime.mvp_runtime.crypto.live_order import build_live_order_intent
from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_FLAGS, LIVE_TRADING_PROVIDER_ID
from runtime.mvp_runtime.crypto.state import VENUE_MAINNET, VENUE_TESTNET, venue_state_dir
from runtime.mvp_runtime.errors import MvpRuntimeError, PersistenceError, ToolError

NOW = "2026-09-17T04:05:00Z"
LIVE_AUTH = make_gate_authorization(flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID)


def _intent(**plan):
    base = {"direction": "LONG", "entry_price": 60000.0, "stop_loss": 59000.0,
            "take_profit": 62000.0, "strategy_id": "S001", "candidate_id": "cand_1",
            "strategy_rule_hash": "h1", "strategy_generation_id": "gen_1",
            "timeframe": "4h", "candle_time": "2026-09-17T00:00:00Z"}
    base.update(plan)
    return build_live_order_intent(base, symbol="BTCUSDT", quantity=0.001, notional_usdt=60.0, now=NOW)


def _profile(purpose=PURPOSE_AUTONOMOUS, **overrides):
    authority = {
        PURPOSE_AUTONOMOUS: {"kind": g.AUTHORITY_LIVE_ARM, "strategy_id": "S001", "approval_id": "appr_arm"},
        PURPOSE_PROBE: {"kind": g.AUTHORITY_PROBE_PLAN, "batch_id": "b1", "approval_id": "appr_probe"},
        PURPOSE_TESTNET: {"kind": g.AUTHORITY_TESTNET_CAPS, "max_order_notional_usdt": 50.0,
                          "max_daily_orders": 10},
    }[purpose]
    kw = dict(
        purpose=purpose, stage=gate_stage(), authority=authority,
        budget=({"valid": True, "budget_id": "budget_1", "record_sha256": "sha256:" + "b" * 64}
                if purpose != PURPOSE_TESTNET else None),
        risk_limits=({"source": "default"} if purpose != PURPOSE_TESTNET else None),
    )
    kw.update(overrides)
    return g.approved_profile(**kw)


def _lineage(intent, purpose=PURPOSE_AUTONOMOUS):
    values = {**{k: intent.get(k) for k in g.LINEAGE_FIELDS[PURPOSE_AUTONOMOUS]},
              "batch_id": "b1", "cell_index": 0, "cycle_id": "cyc1"}
    return {k: values[k] for k in g.LINEAGE_FIELDS[purpose]}


def _gate(intent=None, *, checks=None, profile=None, lineage=None, purpose=PURPOSE_AUTONOMOUS,
          venue=VENUE_MAINNET, facts=None):
    intent = intent if intent is not None else _intent()
    return g.evaluate_pre_order_gate(
        intent, purpose=purpose, venue=venue,
        checks=[g.check("door_ok", True)] if checks is None else checks,
        profile=profile if profile is not None else _profile(purpose),
        lineage=lineage if lineage is not None else _lineage(intent, purpose),
        facts=facts or {"spread_bps": 1.0}, now=NOW,
    )


# --- the gate ------------------------------------------------------------------------------------

def test_every_check_passing_approves_and_seals():
    snapshot = _gate()
    assert snapshot["approved"] is True and snapshot["failed_checks"] == []
    assert {c["check"] for c in snapshot["checks"]} >= {
        "door_ok", g.CHECK_DOOR_CHECKS, g.CHECK_OPENS_EXPOSURE, g.CHECK_INTENT_IDENTITY,
        g.CHECK_LINEAGE, g.CHECK_PROFILE}
    assert snapshot["risk_gate_id"] == g.GATE_ID and snapshot["snapshot_version"] == g.SNAPSHOT_VERSION
    assert snapshot["risk_snapshot_sha256"].startswith("sha256:")
    assert snapshot["approved_profile_sha256"].startswith("sha256:")


def test_one_failed_door_check_refuses_and_is_named():
    snapshot = _gate(checks=[g.check("door_ok", True), g.check("spread_within_limit", False, 51.0)])
    assert snapshot["approved"] is False
    assert snapshot["failed_checks"] == ["spread_within_limit"]


def test_a_door_that_re_derived_nothing_verified_nothing():
    snapshot = _gate(checks=[])
    assert snapshot["approved"] is False
    assert snapshot["failed_checks"] == [g.CHECK_DOOR_CHECKS]


def test_a_reduce_only_order_is_not_this_gates_to_approve():
    intent = build_live_order_intent({"direction": "LONG", "strategy_id": "S001"}, symbol="BTCUSDT",
                                     quantity=0.001, notional_usdt=60.0, now=NOW, reduce_only=True)
    snapshot = _gate(intent, lineage={k: "x" for k in g.LINEAGE_FIELDS[PURPOSE_AUTONOMOUS]})
    assert g.CHECK_OPENS_EXPOSURE in snapshot["failed_checks"]


@pytest.mark.parametrize("field", ["client_order_id", "idempotency_key", "order_intent_id"])
def test_an_identifier_that_does_not_follow_from_the_intent_refuses(field):
    intent = {**_intent(), field: "forged"}
    assert g.CHECK_INTENT_IDENTITY in _gate(intent)["failed_checks"]


def test_an_identity_that_hides_a_changed_bar_refuses():
    """The bar is in the identity (decision 16): an intent moved to another bar keeping its old id
    is not the order the id names."""
    intent = {**_intent(), "candle_time": "2026-09-17T04:00:00Z"}
    assert g.CHECK_INTENT_IDENTITY in _gate(intent)["failed_checks"]


@pytest.mark.parametrize("purpose", [PURPOSE_AUTONOMOUS, PURPOSE_PROBE, PURPOSE_TESTNET])
def test_each_purpose_needs_its_whole_lineage(purpose):
    intent = _intent()
    for field in g.LINEAGE_FIELDS[purpose]:
        lineage = {**_lineage(intent, purpose), field: None}
        snapshot = _gate(intent, purpose=purpose, lineage=lineage, profile=_profile(purpose),
                         venue=VENUE_TESTNET if purpose == PURPOSE_TESTNET else VENUE_MAINNET)
        assert g.CHECK_LINEAGE in snapshot["failed_checks"], field


def test_a_probe_cell_zero_is_a_named_cell():
    intent = _intent()
    snapshot = _gate(intent, purpose=PURPOSE_PROBE, profile=_profile(PURPOSE_PROBE),
                     lineage={**_lineage(intent, PURPOSE_PROBE), "cell_index": 0})
    assert snapshot["approved"] is True


def test_an_unknown_purpose_approves_nothing():
    snapshot = _gate(purpose="canary", profile=_profile(), lineage={"strategy_id": "S001"})
    assert {g.CHECK_LINEAGE, g.CHECK_PROFILE} <= set(snapshot["failed_checks"])


@pytest.mark.parametrize("order,authority", [
    (PURPOSE_AUTONOMOUS, PURPOSE_PROBE),
    (PURPOSE_PROBE, PURPOSE_AUTONOMOUS),
    (PURPOSE_AUTONOMOUS, PURPOSE_TESTNET),
])
def test_a_profile_authorizes_only_the_kind_of_order_it_was_built_for(order, authority):
    """A probe plan's approval is not an arming approval: a complete profile for one purpose
    refuses an order of another."""
    intent = _intent()
    snapshot = _gate(intent, purpose=order, profile=_profile(authority), lineage=_lineage(intent, order))
    assert g.CHECK_PROFILE in snapshot["failed_checks"]


# --- the approved profile (decision 17) --------------------------------------------------------

def _stage(**kw):
    base = dict(stage="LIVE_AUTONOMOUS", valid=True, reason_code=None, recorded_stage="LIVE_AUTONOMOUS",
                stage_id="s1", record_sha256="sha256:" + "5" * 64, approval_id="appr_stage")
    base.update(kw)
    return StageStatus(**base)


@pytest.mark.parametrize("stage", [
    _stage(valid=False, stage="READ_ONLY"),
    _stage(stage_id=None),
    _stage(record_sha256=None),
    _stage(approval_id=None),
    _stage(approval_id="   "),
], ids=["not-binding", "no-id", "no-hash", "no-approval", "blank-approval"])
def test_a_stage_record_without_its_approval_authorizes_nothing(stage):
    assert g.profile_problems(_profile(stage=stage))


@pytest.mark.parametrize("budget", [
    None,
    {"valid": False, "budget_id": "b", "record_sha256": "sha256:x"},
    {"valid": True, "budget_id": None, "record_sha256": "sha256:x"},
    {"valid": True, "budget_id": "b", "record_sha256": None},
])
@pytest.mark.parametrize("purpose", [PURPOSE_AUTONOMOUS, PURPOSE_PROBE])
def test_a_money_order_needs_a_registered_budget(budget, purpose):
    profile = _profile(purpose, budget=budget)
    if budget is None:
        profile = {**profile, "budget": None}
    assert any("budget" in p for p in g.profile_problems(profile))


def test_registered_risk_limits_must_name_their_record_and_defaults_need_none():
    assert not g.profile_problems(_profile(risk_limits={"source": "default"}))
    assert g.profile_problems(_profile(risk_limits={"source": "registered", "limits_id": "l1"}))
    assert not g.profile_problems(_profile(risk_limits={"source": "registered", "limits_id": "l1",
                                                        "record_sha256": "sha256:" + "1" * 64}))
    assert g.profile_problems(_profile(risk_limits={}))


@pytest.mark.parametrize("authority", [
    {"kind": g.AUTHORITY_LIVE_ARM, "strategy_id": "S001", "approval_id": None},
    {"kind": g.AUTHORITY_LIVE_ARM, "strategy_id": None, "approval_id": "a"},
    {"kind": g.AUTHORITY_PROBE_PLAN, "batch_id": "b1", "approval_id": "a"},   # wrong kind
    {},
])
def test_an_autonomous_entry_needs_the_approval_that_armed_its_strategy(authority):
    assert g.profile_problems(_profile(authority=authority))


@pytest.mark.parametrize("authority", [
    {"kind": g.AUTHORITY_PROBE_PLAN, "batch_id": "b1", "approval_id": None},
    {"kind": g.AUTHORITY_PROBE_PLAN, "batch_id": None, "approval_id": "a"},
    {"kind": g.AUTHORITY_LIVE_ARM, "strategy_id": "S001", "approval_id": "a"},
])
def test_a_probe_needs_its_plans_approval(authority):
    assert g.profile_problems(_profile(PURPOSE_PROBE, authority=authority))


@pytest.mark.parametrize("authority", [
    {"kind": g.AUTHORITY_TESTNET_CAPS, "max_order_notional_usdt": 0, "max_daily_orders": 10},
    {"kind": g.AUTHORITY_TESTNET_CAPS, "max_order_notional_usdt": 50.0, "max_daily_orders": True},
    {"kind": g.AUTHORITY_TESTNET_CAPS, "max_order_notional_usdt": "50", "max_daily_orders": 10},
])
def test_a_testnet_order_needs_its_stated_caps_and_no_budget(authority):
    assert g.profile_problems(_profile(PURPOSE_TESTNET, authority=authority))
    assert not g.profile_problems(_profile(PURPOSE_TESTNET))


def test_the_profile_hash_moves_with_any_component():
    base = _gate()["approved_profile_sha256"]
    other = _gate(profile=_profile(stage=_stage(approval_id="appr_other")))["approved_profile_sha256"]
    assert base != other


# --- the seal and the binding --------------------------------------------------------------------

def test_the_same_decision_seals_to_the_same_snapshot():
    assert _gate() == _gate()


def test_a_non_finite_fact_cannot_stop_the_record_of_a_refusal():
    snapshot = _gate(checks=[g.check("spread_within_limit", False, math.nan)],
                     facts={"spread_bps": math.nan, "cost_r": math.inf, "levels": {1, 2}})
    assert snapshot["facts"] == {"spread_bps": "nan", "cost_r": "inf", "levels": [1, 2]}
    assert snapshot["failed_checks"] == ["spread_within_limit"]
    assert snapshot["risk_snapshot_sha256"].startswith("sha256:")


def test_binding_leaves_the_order_and_its_fingerprint_unchanged():
    intent = _intent()
    snapshot = _gate(intent)
    bound = g.bind_intent(intent, snapshot)
    assert bound["client_order_id"] == intent["client_order_id"]
    assert g.intent_fingerprint(bound) == g.intent_fingerprint(intent) == snapshot["intent_fingerprint"]
    assert g.verify_snapshot(bound, snapshot) == snapshot["risk_snapshot_sha256"]


@pytest.mark.parametrize("change", [
    {"quantity": 0.002}, {"order_notional_usdt": 120.0}, {"symbol": "ETHUSDT"}, {"side": "SELL"},
    {"stop_loss": 58000.0}, {"take_profit": 63000.0}, {"entry_price": 61000.0},
    {"strategy_id": "S002"}, {"candidate_id": "cand_2"}, {"reduce_only": True},
])
def test_an_order_changed_after_the_gate_is_not_the_one_it_approved(change):
    intent = _intent()
    snapshot = _gate(intent)
    bound = {**g.bind_intent(intent, snapshot), **change}
    with pytest.raises(ToolError) as refused:
        g.verify_snapshot(bound, snapshot)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_INTENT_MISMATCH


def test_an_order_that_does_not_name_its_snapshot_is_refused():
    intent = _intent()
    snapshot = _gate(intent)
    with pytest.raises(ToolError) as refused:
        g.verify_snapshot(intent, snapshot)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_INTENT_MISMATCH


@pytest.mark.parametrize("edit", [
    lambda s: s.update(approved=True, failed_checks=[]) or s["checks"].append(g.check("x", False)),
    lambda s: s.update(facts={"spread_bps": 0.0}),
    lambda s: s["approved_profile"]["stage"].update(approval_id="forged"),
    lambda s: s.update(venue=VENUE_TESTNET),
], ids=["checks", "facts", "profile", "venue"])
def test_a_snapshot_edited_after_sealing_is_refused(edit):
    intent = _intent()
    snapshot = json.loads(json.dumps(_gate(intent)))
    edit(snapshot)
    with pytest.raises(ToolError) as refused:
        g.verify_snapshot(g.bind_intent(intent, snapshot), snapshot)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_TAMPERED


def test_a_refused_snapshot_never_authorizes_even_when_intact():
    intent = _intent()
    snapshot = _gate(intent, checks=[g.check("spread_within_limit", False)])
    with pytest.raises(ToolError) as refused:
        g.verify_snapshot(g.bind_intent(intent, snapshot), snapshot)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_NOT_APPROVED


@pytest.mark.parametrize("snapshot", [None, {}, "sha256:abc", []])
def test_no_snapshot_no_order(snapshot):
    with pytest.raises(ToolError) as refused:
        g.verify_snapshot(_intent(), snapshot)
    assert refused.value.reason_code in {g.RISK_SNAPSHOT_MISSING, g.RISK_SNAPSHOT_TAMPERED}


def test_the_binding_writes_to_the_snapshots_own_venue_only():
    intent, snapshot = approved_snapshot(_intent())
    with pytest.raises(ToolError) as refused:
        g.verify_and_persist(intent, snapshot, store=None)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_NO_STORE
    with pytest.raises(ToolError) as refused:
        g.verify_and_persist(intent, snapshot, store=FakeSnapshotStore(VENUE_TESTNET))
    assert refused.value.reason_code == g.RISK_SNAPSHOT_VENUE_MISMATCH
    store = FakeSnapshotStore()
    assert g.verify_and_persist(intent, snapshot, store=store) == snapshot["risk_snapshot_sha256"]
    assert [s["risk_snapshot_sha256"] for s in store.appended] == [snapshot["risk_snapshot_sha256"]]


def test_a_store_that_records_something_else_is_refused():
    class _Liar(FakeSnapshotStore):
        def append(self, snapshot):
            return "sha256:" + "0" * 64

    intent, snapshot = approved_snapshot(_intent())
    with pytest.raises(ToolError) as refused:
        g.verify_and_persist(intent, snapshot, store=_Liar())
    assert refused.value.reason_code == g.RISK_SNAPSHOT_TAMPERED


# --- the binding inside the venue door -----------------------------------------------------------

class _Adapter:
    network_egress = False
    tool_id, tool_version = "fake", "0"

    def __init__(self, events):
        self.events = events
        self.submitted = []

    def submit(self, request, *, timeout_seconds=10):
        self.events.append("submit")
        self.submitted.append(dict(request))
        return {"accepted": True}

    def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
        request = self.submitted[-1] if self.submitted else {}
        qty = float(request.get("quantity") or 0.0)
        return {"symbol": symbol, "status": "FILLED", "executedQty": qty, "side": request.get("side"),
                "reduceOnly": bool(request.get("reduceOnly")), "avgPrice": "60000", "cumQuote": "60",
                "orderId": 1}


class _RecordingStore(FakeSnapshotStore):
    def __init__(self, events, **kw):
        super().__init__(**kw)
        self.events = events

    def append(self, snapshot):
        self.events.append("record")
        return super().append(snapshot)


APPROVED = {"approved": True}


def test_an_entry_is_recorded_before_it_is_sent_and_names_its_snapshot():
    events: list[str] = []
    intent, snapshot = approved_snapshot(_intent())
    adapter, store = _Adapter(events), _RecordingStore(events)
    result = lx.submit_and_reconcile(intent, adapter=adapter, guard_verdict=APPROVED, now=NOW,
                                     risk_snapshot=snapshot, snapshot_store=store)
    assert events == ["record", "submit"]
    assert result["risk_snapshot_sha256"] == snapshot["risk_snapshot_sha256"]


@pytest.mark.parametrize("kw,code", [
    ({}, g.RISK_SNAPSHOT_MISSING),
    ({"snapshot_store": None}, g.RISK_SNAPSHOT_MISSING),
    ({"risk_snapshot": "valid", "snapshot_store": None}, g.RISK_SNAPSHOT_NO_STORE),
    ({"risk_snapshot": "other"}, g.RISK_SNAPSHOT_INTENT_MISMATCH),
    ({"risk_snapshot": "valid", "snapshot_store": "testnet"}, g.RISK_SNAPSHOT_VENUE_MISMATCH),
])
def test_an_entry_without_its_recorded_snapshot_is_never_sent(kw, code):
    events: list[str] = []
    intent, snapshot = approved_snapshot(_intent())
    _, other = approved_snapshot(_intent(candle_time="2026-09-17T04:00:00Z"))
    kw = dict(kw)
    if kw.get("risk_snapshot") == "valid":
        kw["risk_snapshot"] = snapshot
    elif kw.get("risk_snapshot") == "other":
        kw["risk_snapshot"] = other
        kw.setdefault("snapshot_store", FakeSnapshotStore())
    if kw.get("snapshot_store") == "testnet":
        kw["snapshot_store"] = FakeSnapshotStore(VENUE_TESTNET)
    adapter = _Adapter(events)
    with pytest.raises(lx.SubmitRefused) as refused:
        lx.submit_and_reconcile(intent, adapter=adapter, guard_verdict=APPROVED, now=NOW, **kw)
    assert refused.value.reason_code == code
    assert adapter.submitted == [] and events == []


@pytest.mark.parametrize("error,code", [
    (PersistenceError("PRE_ORDER_SNAPSHOTS_LOCKED", "scripted"), "PRE_ORDER_SNAPSHOTS_LOCKED"),
    (OSError(28, "No space left on device"), g.RISK_SNAPSHOT_STORE_UNREADABLE),
])
def test_a_snapshot_that_cannot_be_recorded_sends_nothing(error, code):
    events: list[str] = []
    intent, snapshot = approved_snapshot(_intent())
    adapter = _Adapter(events)
    with pytest.raises(lx.SubmitRefused) as refused:
        lx.submit_and_reconcile(intent, adapter=adapter, guard_verdict=APPROVED, now=NOW,
                                risk_snapshot=snapshot, snapshot_store=FakeSnapshotStore(error=error))
    assert refused.value.reason_code == code
    assert adapter.submitted == []


def test_a_close_needs_no_snapshot():
    """A reduce-only order cannot add exposure, and a gate that could refuse it could trap a
    position — the close guard alone judges it."""
    events: list[str] = []
    close = build_live_order_intent({"direction": "LONG", "strategy_id": "S001"}, symbol="BTCUSDT",
                                    quantity=0.001, notional_usdt=60.0, now=NOW, reduce_only=True)
    adapter = _Adapter(events)
    result = lx.submit_and_reconcile(close, adapter=adapter, guard_verdict=APPROVED, now=NOW)
    assert events == ["submit"] and result["risk_snapshot_sha256"] is None


def test_every_refusal_before_the_adapter_is_a_submit_refused():
    assert issubclass(lx.SubmitRefused, ToolError)
    with pytest.raises(lx.SubmitRefused) as refused:
        lx.submit_and_reconcile(_intent(), adapter=_Adapter([]), guard_verdict={"approved": False}, now=NOW)
    assert refused.value.reason_code == lx.GUARD_NOT_APPROVED
    with pytest.raises(lx.SubmitRefused):
        lx.submit_and_reconcile({"symbol": "BTCUSDT"}, adapter=_Adapter([]), guard_verdict=APPROVED, now=NOW)


# --- the store -----------------------------------------------------------------------------------

def _store(tmp_path, **kw):
    return g.PreOrderSnapshotStore(root=tmp_path, authorization=LIVE_AUTH, venue=VENUE_MAINNET,
                                   provider_id=LIVE_TRADING_PROVIDER_ID, flags=LIVE_TRADING_FLAGS, **kw)


def test_the_store_records_once_and_reads_back_verified(tmp_path):
    _, snapshot = approved_snapshot(_intent())
    store = _store(tmp_path)
    assert store.append(snapshot) == snapshot["risk_snapshot_sha256"]
    assert store.append(snapshot) == snapshot["risk_snapshot_sha256"]  # a retry writes nothing
    rows = g.read_snapshots(tmp_path)
    assert rows == [snapshot]
    assert g.find_snapshot(snapshot["risk_snapshot_sha256"], tmp_path) == snapshot
    assert g.snapshots_status(tmp_path)["count"] == 1


def test_different_content_under_a_recorded_id_is_refused(tmp_path):
    _, snapshot = approved_snapshot(_intent())
    store = _store(tmp_path)
    store.append(snapshot)
    forged = {**snapshot, "risk_snapshot_sha256": "sha256:" + "f" * 64}
    with pytest.raises(ToolError) as refused:
        store.append(forged)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_ID_CONFLICT


def test_the_store_syncs_each_row_before_returning(tmp_path, monkeypatch):
    synced: list[int] = []
    real = g.os.fsync
    monkeypatch.setattr(g.os, "fsync", lambda fd: synced.append(fd) or real(fd))
    _, snapshot = approved_snapshot(_intent())
    _store(tmp_path).append(snapshot)
    assert len(synced) == 1


def test_a_torn_row_is_no_row_and_does_not_swallow_the_next(tmp_path):
    """A row is synced before its order leaves, so a line the writer never finished had no order.
    The next append ends that line first, or its own row would fuse into it."""
    path = g.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"pre_order_risk_snapshot_id": "torn', encoding="utf-8")
    _, snapshot = approved_snapshot(_intent())
    _store(tmp_path).append(snapshot)
    assert g.read_snapshots(tmp_path) == [snapshot]
    _, second = approved_snapshot(_intent(candle_time="2026-09-17T04:00:00Z"))
    _store(tmp_path).append(second)
    assert [r["risk_snapshot_sha256"] for r in g.read_snapshots(tmp_path)] == [
        snapshot["risk_snapshot_sha256"], second["risk_snapshot_sha256"]]


def test_a_tampered_row_fails_the_verified_read_and_the_board(tmp_path):
    _, snapshot = approved_snapshot(_intent())
    _store(tmp_path).append(snapshot)
    path = g.snapshot_path(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace('"approved": true', '"approved": false'),
                    encoding="utf-8")
    with pytest.raises(ToolError) as refused:
        g.read_snapshots(tmp_path)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_STORE_TAMPERED
    assert g.snapshots_status(tmp_path) == {"readable": False, "error": g.RISK_SNAPSHOT_STORE_TAMPERED,
                                            "count": None, "last_created_at": None}


def test_an_unreadable_store_refuses_the_append(tmp_path):
    g.snapshot_path(tmp_path).mkdir(parents=True)   # a directory where the file should be
    _, snapshot = approved_snapshot(_intent())
    with pytest.raises(ToolError) as refused:
        _store(tmp_path).append(snapshot)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_STORE_UNREADABLE


def test_the_store_writes_nothing_without_its_venues_authorization(tmp_path):
    _, snapshot = approved_snapshot(_intent())
    with pytest.raises(MvpRuntimeError):
        g.PreOrderSnapshotStore(root=tmp_path, authorization=None, venue=VENUE_MAINNET,
                                provider_id=LIVE_TRADING_PROVIDER_ID, flags=LIVE_TRADING_FLAGS).append(snapshot)
    # The live authorization cannot write the testnet store.
    with pytest.raises(MvpRuntimeError):
        testnet.testnet_snapshot_store(root=tmp_path, authorization=LIVE_AUTH).append(snapshot)
    assert not g.snapshot_path(tmp_path).exists()
    assert not g.snapshot_path(tmp_path, venue=VENUE_TESTNET).exists()


def test_each_venue_keeps_its_own_snapshots(tmp_path):
    testnet_auth = make_gate_authorization(flags=testnet.TESTNET_TRADING_FLAGS,
                                           provider_id=testnet.TESTNET_PROVIDER_ID)
    _, snapshot = approved_snapshot(_intent(), purpose=PURPOSE_TESTNET)
    testnet.testnet_snapshot_store(root=tmp_path, authorization=testnet_auth).append(snapshot)
    assert g.read_snapshots(tmp_path) == []
    assert g.read_snapshots(tmp_path, venue=VENUE_TESTNET) == [snapshot]
    assert g.snapshot_path(tmp_path, venue=VENUE_TESTNET).parent == venue_state_dir(tmp_path, venue=VENUE_TESTNET)


def test_the_selector_is_the_live_switch(tmp_path, monkeypatch):
    monkeypatch.delenv("MVP_LIVE_TRADING", raising=False)
    inert = lx.select_pre_order_snapshot_store(root=tmp_path)
    assert inert.filesystem_write is False and inert.venue == VENUE_MAINNET
    _, snapshot = approved_snapshot(_intent())
    assert inert.append(snapshot) == snapshot["risk_snapshot_sha256"]
    assert not g.snapshot_path(tmp_path).exists()
    monkeypatch.setenv("MVP_LIVE_TRADING", "real")
    real = lx.select_pre_order_snapshot_store(root=tmp_path)
    assert real.filesystem_write is True and real.venue == VENUE_MAINNET
    real.append(snapshot)
    assert g.read_snapshots(tmp_path) == [snapshot]


def test_a_machine_with_no_orders_reads_an_empty_record(tmp_path):
    assert g.read_snapshots(tmp_path) == []
    assert g.snapshots_status(tmp_path) == {"readable": True, "error": None, "count": 0,
                                            "last_created_at": None}


# --- the closed schema ---------------------------------------------------------------------------

def _resealed(snapshot, **changes):
    body = {k: v for k, v in {**snapshot, **changes}.items() if k != "risk_snapshot_sha256"}
    from runtime.read_only_kernel import integrity

    return {**body, "risk_snapshot_sha256": integrity.sha256_record(body)}


@pytest.mark.parametrize("changes", [
    {"unexpected": 1},                                   # the record is closed
    {"venue": "binance_spot"},                           # a venue this runtime has no store for
    {"purpose": "canary"},
    {"created_at": "2026-09-17 04:05"},
], ids=["extra-key", "venue", "purpose", "timestamp"])
def test_an_intact_approved_snapshot_the_schema_does_not_describe_is_not_recordable(changes):
    intent, snapshot = approved_snapshot(_intent())
    forged = _resealed(snapshot, **changes)
    with pytest.raises(ToolError) as refused:
        g.verify_snapshot(g.bind_intent(intent, forged), forged)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_INVALID


def test_a_recorded_row_the_schema_does_not_describe_fails_the_verified_read(tmp_path):
    _, snapshot = approved_snapshot(_intent())
    path = g.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_resealed(snapshot, unexpected=1)) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as refused:
        g.read_snapshots(tmp_path)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_STORE_TAMPERED


@pytest.mark.parametrize("purpose", [PURPOSE_AUTONOMOUS, PURPOSE_PROBE, PURPOSE_TESTNET])
def test_every_purposes_snapshot_is_one_the_schema_describes(purpose):
    _, snapshot = approved_snapshot(_intent(), purpose=purpose)
    assert g._schema_problem(snapshot) is None
