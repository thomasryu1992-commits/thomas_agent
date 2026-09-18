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

from runtime.mvp_runtime import timeutil
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
            "strategy_artifact_sha256": "sha256:" + "a" * 64,
            "timeframe": "4h", "candle_time": "2026-09-17T00:00:00Z"}
    base.update(plan)
    return build_live_order_intent(base, symbol="BTCUSDT", quantity=0.001, notional_usdt=60.0, now=NOW)


# An arm the route verified against the approval store (PR2c-2b).
_VERIFIED_ARM = {"kind": g.AUTHORITY_LIVE_ARM, "strategy_id": "S001", "approval_id": "appr_arm",
                 "approval_fingerprint": "sha256:" + "f" * 64, g.LIVE_ARM_VERIFIED_FIELD: True,
                 "approval_problem": None}


def _profile(purpose=PURPOSE_AUTONOMOUS, **overrides):
    authority = {
        PURPOSE_AUTONOMOUS: _VERIFIED_ARM,
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
          venue=None, facts=None, decided_at=None):
    """Judged at the wall clock unless a test says otherwise, so a send right after is fresh."""
    intent = intent if intent is not None else _intent()
    return g.evaluate_pre_order_gate(
        intent, purpose=purpose, venue=venue or g.VENUE_FOR_PURPOSE.get(purpose, VENUE_MAINNET),
        checks=[g.check("door_ok", True)] if checks is None else checks,
        profile=profile if profile is not None else _profile(purpose),
        lineage=lineage if lineage is not None else _lineage(intent, purpose),
        facts=facts or {"spread_bps": 1.0}, now=NOW,
        decided_at=decided_at or timeutil.utc_now_iso(),
    )


# --- the gate ------------------------------------------------------------------------------------

def test_every_check_passing_approves_and_seals():
    snapshot = _gate()
    assert snapshot["approved"] is True and snapshot["failed_checks"] == []
    assert [c["check"] for c in snapshot["checks"]] == ["door_ok", *g.GATE_CHECK_IDS]
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
    assert {g.CHECK_LINEAGE, g.CHECK_PROFILE, g.CHECK_VENUE} <= set(snapshot["failed_checks"])


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


@pytest.mark.parametrize("change", [
    {g.LIVE_ARM_VERIFIED_FIELD: False, "approval_problem": "LIVE_ARM_APPROVAL_MISSING"},
    {g.LIVE_ARM_VERIFIED_FIELD: "true"},                 # a truthy string is not a verification
    {g.LIVE_ARM_VERIFIED_FIELD: None},
    {"approval_fingerprint": None},
    {"approval_fingerprint": ""},
], ids=["refused", "string", "none", "no-fingerprint", "blank-fingerprint"])
def test_an_arm_the_route_did_not_verify_authorizes_nothing(change):
    """PR2c-2b: an arming approval id is not enough; the route must have verified its record."""
    assert not g.profile_problems(_profile(authority=_VERIFIED_ARM))
    problems = g.profile_problems(_profile(authority={**_VERIFIED_ARM, **change}))
    assert problems and problems[0].startswith("the arming approval was not verified")
    assert ("LIVE_ARM_APPROVAL_MISSING" in problems[0]) is ("approval_problem" in change)


def test_an_arm_that_names_no_verification_is_refused_except_on_the_read_of_an_older_row():
    unnamed = {k: v for k, v in _VERIFIED_ARM.items()
               if k not in (g.LIVE_ARM_VERIFIED_FIELD, "approval_fingerprint", "approval_problem")}
    assert g.profile_problems(_profile(authority=unnamed))
    assert not g.profile_problems(_profile(authority=unnamed), legacy_read=True)
    # A row that names a verification must hold one, on any read — whichever of its fields it names
    # (review of #887: a row with the problem but not the flag was read as an older row).
    assert g.profile_problems(_profile(authority={**_VERIFIED_ARM, g.LIVE_ARM_VERIFIED_FIELD: False}),
                              legacy_read=True)
    for field, value in (("approval_problem", "LIVE_ARM_APPROVAL_MISSING"), ("approval_fingerprint", None),
                         ("approval_problem", None)):
        assert g.profile_problems(_profile(authority={**unnamed, field: value}), legacy_read=True), field
    assert g.profile_problems(_profile(authority={**unnamed, "approval_id": None}), legacy_read=True)


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
                     facts={"spread_bps": math.nan, "cost_r": math.inf, "levels": {1, 2}},
                     decided_at="2026-09-17T04:05:30Z")
    assert snapshot["facts"] == {"spread_bps": "nan", "cost_r": "inf", "levels": [1, 2],
                                 "decided_at": "2026-09-17T04:05:30Z"}
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
    (OSError(28, "No space left on device"), g.RISK_SNAPSHOT_STORE_UNWRITABLE),
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


@pytest.mark.parametrize("edit", [
    lambda row: row["facts"].update(equity_usdt=10_000_000.0),
    lambda row: row["checks"][0].update(detail="edited"),
    lambda row: row["lineage"].update(strategy_id="S999"),
    lambda row: row["approved_profile"]["authority"].update(approval_id="appr_forged"),
], ids=["fact", "check-detail", "lineage", "authority"])
def test_an_edit_the_schema_still_accepts_fails_the_seal_on_read(tmp_path, edit):
    """The schema says a row is recordable; only the seal says it is the row that was recorded."""
    _, snapshot = approved_snapshot(_intent())
    _store(tmp_path).append(snapshot)
    path = g.snapshot_path(tmp_path)
    row = json.loads(path.read_text(encoding="utf-8"))
    edit(row)
    assert g._schema_problem(row) is None      # the schema alone would let this row through
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as refused:
        g.read_snapshots(tmp_path)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_STORE_TAMPERED
    assert g.snapshots_status(tmp_path)["error"] == g.RISK_SNAPSHOT_STORE_TAMPERED


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


# --- PR2b review: the gate seals only what the door would accept, and the door re-checks it ------

@pytest.mark.parametrize("purpose,venue", [
    (PURPOSE_TESTNET, VENUE_MAINNET), (PURPOSE_AUTONOMOUS, VENUE_TESTNET), (PURPOSE_PROBE, VENUE_TESTNET),
])
def test_a_purpose_is_sealed_only_for_its_own_venue(purpose, venue):
    """A testnet cycle's caps authorize nothing on mainnet — measured by the review: without this,
    a testnet-purpose snapshot for mainnet was sealed, recorded and sent."""
    assert _gate(purpose=purpose, venue=venue)["failed_checks"] == [g.CHECK_VENUE]


@pytest.mark.parametrize("handed", [
    {"check": "spread_within_limit", "ok": "false"},     # a truthy string is not a pass
    ("spread_within_limit", False),                      # not a mapping: it would vanish
    {"ok": True},                                        # no name
    {"check": g.CHECK_PROFILE, "ok": True},              # a name the gate keeps for itself
], ids=["string-ok", "tuple", "unnamed", "reserved-name"])
def test_a_malformed_door_check_refuses(handed):
    snapshot = _gate(checks=[g.check("door_ok", True), handed])
    assert snapshot["approved"] is False
    assert g.CHECK_DOOR_CHECKS in snapshot["failed_checks"]


def test_a_check_that_is_not_true_itself_is_recorded_as_failed():
    snapshot = _gate(checks=[g.check("door_ok", True), {"check": "spread_within_limit", "ok": "false"}])
    assert "spread_within_limit" in snapshot["failed_checks"]


@pytest.mark.parametrize("change", [{"side": "SELL"}, {"close_position": True}, {"direction": "FLAT"}],
                         ids=["side", "close-position", "direction"])
def test_an_order_that_does_not_open_its_own_direction_is_not_sealed(change):
    intent = {**_intent(), **change}
    if "direction" in change:      # the identity follows the direction; keep it consistent
        from runtime.mvp_runtime.crypto.live_order import enrich_order_identity
        intent = enrich_order_identity(intent)
    lineage = {**_lineage(intent)}
    assert g.CHECK_OPENS_EXPOSURE in _gate(intent, lineage=lineage)["failed_checks"]


def test_binding_names_all_three_references():
    intent, snapshot = approved_snapshot(_intent())
    assert {field: intent[field] for field in g.SNAPSHOT_REFERENCE_FIELDS} == {
        "pre_order_risk_snapshot_id": snapshot["pre_order_risk_snapshot_id"],
        "risk_gate_id": g.GATE_ID,
        "risk_snapshot_sha256": snapshot["risk_snapshot_sha256"],
    }


def _reprofiled(snapshot, profile):
    return _resealed(snapshot, approved_profile=profile,
                     approved_profile_sha256=__import__("runtime.read_only_kernel.integrity",
                                                        fromlist=["x"]).sha256_record(profile))


def _without_check(snapshot, name):
    return _resealed(snapshot, checks=[c for c in snapshot["checks"] if c["check"] != name])


_UNSUPPORTED = {
    "testnet-caps-on-mainnet": lambda s, _t: _resealed(_t, venue=VENUE_MAINNET),
    "profile-incomplete": lambda s, _t: _reprofiled(
        s, _profile(authority={"kind": g.AUTHORITY_LIVE_ARM, "strategy_id": "S001", "approval_id": None})),
    "profile-for-a-probe": lambda s, _t: _reprofiled(s, _profile(PURPOSE_PROBE)),
    "arm-not-verified": lambda s, _t: _reprofiled(
        s, _profile(authority={**_VERIFIED_ARM, g.LIVE_ARM_VERIFIED_FIELD: False})),
    "profile-hash": lambda s, _t: _resealed(s, approved_profile_sha256="sha256:" + "0" * 64),
    "gate-check-missing": lambda s, _t: _without_check(s, g.CHECK_VENUE),
    "gate-check-twice": lambda s, _t: _resealed(
        s, checks=[*s["checks"], {"check": g.CHECK_PROFILE, "ok": True, "detail": None}]),
    "no-door-check": lambda s, _t: _resealed(
        s, checks=[c for c in s["checks"] if c["check"] in g.GATE_CHECK_IDS]),
    "lineage-incomplete": lambda s, _t: _resealed(
        s, lineage={k: v for k, v in s["lineage"].items() if k != "candidate_id"}),
    "lineage-names-another-order": lambda s, _t: _resealed(
        s, lineage={**s["lineage"], "order_intent_id": "live_intent_" + "0" * 20}),
}


@pytest.mark.parametrize("forge", list(_UNSUPPORTED.values()), ids=list(_UNSUPPORTED))
def test_an_intact_snapshot_that_does_not_support_its_approval_sends_nothing(forge):
    """The seal is a plain hash: a snapshot can say `approved` and be intact without the gate ever
    having sealed it. The door re-checks what the gate requires of every snapshot it approves."""
    base_intent = _intent()
    snapshot = _gate(base_intent)
    testnet_snapshot = _gate(base_intent, purpose=PURPOSE_TESTNET)
    forged = forge(snapshot, testnet_snapshot)
    assert forged["approved"] is True and g._schema_problem(forged) is None
    intent = g.bind_intent(base_intent, forged)
    events: list[str] = []
    adapter = _Adapter(events)
    with pytest.raises(lx.SubmitRefused) as refused:
        lx.submit_and_reconcile(intent, adapter=adapter, guard_verdict=APPROVED, now=NOW,
                                risk_snapshot=forged,
                                snapshot_store=_RecordingStore(events, venue=forged["venue"]))
    assert refused.value.reason_code == g.RISK_SNAPSHOT_UNSUPPORTED
    assert adapter.submitted == [] and events == []


@pytest.mark.parametrize("forge", list(_UNSUPPORTED.values()), ids=list(_UNSUPPORTED))
def test_a_recorded_row_that_does_not_support_its_approval_fails_the_verified_read(tmp_path, forge):
    base_intent = _intent()
    forged = forge(_gate(base_intent), _gate(base_intent, purpose=PURPOSE_TESTNET))
    path = g.snapshot_path(tmp_path, venue=forged["venue"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(forged) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as refused:
        g.read_snapshots(tmp_path, venue=forged["venue"])
    assert refused.value.reason_code == g.RISK_SNAPSHOT_STORE_TAMPERED


def _forged_for(intent):
    """``intent`` with a snapshot resealed to match it — what a snapshot not made by the gate looks
    like when every field it copies agrees with the order."""
    snapshot = _gate()
    forged = _resealed(snapshot, **{field: intent.get(field) for field in (
        "symbol", "side", "client_order_id", "idempotency_key", "order_intent_id")},
        intent_fingerprint=g.intent_fingerprint(intent))
    return g.bind_intent(intent, forged), forged


@pytest.mark.parametrize("change", [
    {"side": "SELL"}, {"reduce_only": True}, {"close_position": True},
    {"client_order_id": "TAI_BTCUSDT_LONG_forged"},
], ids=["side", "reduce-only", "close-position", "identity"])
def test_an_order_the_gate_would_not_seal_is_refused_even_under_a_matching_snapshot(change):
    intent, forged = _forged_for({**_intent(), **change})
    with pytest.raises(ToolError) as refused:
        g.verify_snapshot(intent, forged)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_INTENT_MISMATCH


@pytest.mark.parametrize("field", ["symbol", "side", "client_order_id", "order_intent_id"])
def test_a_snapshot_whose_copied_fields_are_not_the_orders_is_refused(field):
    intent, snapshot = approved_snapshot(_intent())
    value = {"symbol": "ETHUSDT", "side": "SELL", "client_order_id": "TAI_BTCUSDT_LONG_other",
             "order_intent_id": "live_intent_" + "1" * 20}[field]
    forged = _resealed(snapshot, **{field: value})
    with pytest.raises(ToolError) as refused:
        g.verify_snapshot(g.bind_intent(intent, forged), forged)
    assert refused.value.reason_code in {g.RISK_SNAPSHOT_INTENT_MISMATCH, g.RISK_SNAPSHOT_UNSUPPORTED}


@pytest.mark.parametrize("field", ["pre_order_risk_snapshot_id", "risk_gate_id"])
def test_an_order_that_does_not_name_its_snapshot_back_is_refused(field):
    intent, snapshot = approved_snapshot(_intent())
    with pytest.raises(ToolError) as refused:
        g.verify_snapshot({**intent, field: "other"}, snapshot)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_INTENT_MISMATCH


@pytest.mark.parametrize("field,value", [
    ("price", 1.0), ("time_in_force", "IOC"), ("stop_price", 1.0),
    ("working_type", "CONTRACT_PRICE"), ("close_position", True),
])
def test_every_request_field_is_bound(field, value):
    intent = _intent()
    assert g.intent_fingerprint({**intent, field: value}) != g.intent_fingerprint(intent)


def test_the_fingerprint_binds_every_field_the_venue_request_is_built_from():
    """Structural: a field `build_order_request` sends that the fingerprint does not bind is a way
    for one snapshot to authorize two different requests (review finding 6)."""
    import ast
    import inspect
    import textwrap

    read: set[str] = set()
    for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(lx.build_order_request)))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "intent"
                and node.args and isinstance(node.args[0], ast.Constant)):
            read.add(node.args[0].value)
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id == "intent" and isinstance(node.slice, ast.Constant)):
            read.add(node.slice.value)
    assert {"symbol", "side", "quantity", "reduce_only"} <= read     # the scan sees the reads
    assert read <= set(g.INTENT_BOUND_FIELDS), sorted(read - set(g.INTENT_BOUND_FIELDS))


class _CapableAdapter(_Adapter):
    network_egress = True


def test_a_capable_adapter_never_sends_with_a_store_that_records_nothing():
    events: list[str] = []
    intent, snapshot = approved_snapshot(_intent())
    adapter = _CapableAdapter(events)
    with pytest.raises(lx.SubmitRefused) as refused:
        lx.submit_and_reconcile(intent, adapter=adapter, guard_verdict=APPROVED, now=NOW,
                                risk_snapshot=snapshot, snapshot_store=g.DryRunPreOrderSnapshotStore())
    assert refused.value.reason_code == g.RISK_SNAPSHOT_NO_STORE
    assert adapter.submitted == []


def test_a_capable_adapter_sends_with_a_store_that_writes():
    events: list[str] = []
    intent, snapshot = approved_snapshot(_intent())
    adapter = _CapableAdapter(events)
    result = lx.submit_and_reconcile(intent, adapter=adapter, guard_verdict=APPROVED, now=NOW,
                                     risk_snapshot=snapshot, snapshot_store=_RecordingStore(events))
    assert events == ["record", "submit"] and result["risk_snapshot_sha256"] == snapshot["risk_snapshot_sha256"]


# --- PR2b review: the store's lines -------------------------------------------------------------

def _snapshot_with_facts(facts, **plan):
    intent = _intent(**plan)
    return _gate(intent, facts=facts)


def test_rows_are_written_as_ascii_whatever_a_field_holds(tmp_path):
    """A line separator inside a string split the writer's view of the file from the reader's, so
    a retry was written twice and a conflicting id was accepted (review finding 2)."""
    snapshot = _snapshot_with_facts({"note": "a\u2028b\u2029c\u0085d é"})
    store = _store(tmp_path)
    store.append(snapshot)
    store.append(snapshot)
    data = g.snapshot_path(tmp_path).read_bytes()
    assert data.isascii() and data.count(b"\n") == 1
    assert g.read_snapshots(tmp_path) == [snapshot]
    with pytest.raises(ToolError) as refused:
        store.append({**snapshot, "risk_snapshot_sha256": "sha256:" + "f" * 64})
    assert refused.value.reason_code == g.RISK_SNAPSHOT_ID_CONFLICT


def test_an_unfinished_line_is_cut_off_before_the_next_row(tmp_path):
    first = _snapshot_with_facts({"n": 1})
    second = _snapshot_with_facts({"n": 2}, candle_time="2026-09-17T04:00:00Z")
    store = _store(tmp_path)
    store.append(first)
    path = g.snapshot_path(tmp_path)
    complete = path.read_bytes()
    with open(path, "ab") as handle:
        handle.write('{"note": "\u00e9'.encode("utf-8")[:-1])     # cut inside a character
    assert g.read_snapshots(tmp_path) == [first]                      # still readable
    assert g.snapshots_status(tmp_path)["readable"] is True
    store.append(second)
    assert path.read_bytes() == complete + (json.dumps(second, sort_keys=True) + "\n").encode("ascii")
    assert g.read_snapshots(tmp_path) == [first, second]


@pytest.mark.parametrize("damage", [
    lambda line: line[:40] + b"\n",                  # a row cut short, then ended
    lambda line: line.replace(b'"', b"#", 1),         # one byte of damage
    lambda line: b"[1, 2]\n",                        # parses, but is not a record
], ids=["cut", "byte", "not-a-record"])
def test_a_damaged_row_refuses_the_store_and_the_read(tmp_path, damage):
    """A row is the record of an order that may have left; one that no longer parses is refused
    rather than skipped (review finding 3), and the writer will not append past it."""
    first = _snapshot_with_facts({"n": 1})
    second = _snapshot_with_facts({"n": 2}, candle_time="2026-09-17T04:00:00Z")
    store = _store(tmp_path)
    store.append(first)
    path = g.snapshot_path(tmp_path)
    path.write_bytes(damage(path.read_bytes()))
    for attempt in (lambda: g.read_snapshots(tmp_path), lambda: store.append(second)):
        with pytest.raises(ToolError) as refused:
            attempt()
        assert refused.value.reason_code == g.RISK_SNAPSHOT_STORE_TAMPERED
    assert g.snapshots_status(tmp_path)["error"] == g.RISK_SNAPSHOT_STORE_TAMPERED


def test_blank_lines_carry_nothing(tmp_path):
    snapshot = _snapshot_with_facts({"n": 1})
    path = g.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\n" + (json.dumps(snapshot, sort_keys=True) + "\n\n").encode("ascii"))
    assert g.read_snapshots(tmp_path) == [snapshot]


def test_a_store_that_cannot_write_is_a_typed_refusal(tmp_path, monkeypatch):
    def fail(fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(g.os, "fsync", fail)
    with pytest.raises(ToolError) as refused:
        _store(tmp_path).append(_snapshot_with_facts({"n": 1}))
    assert refused.value.reason_code == g.RISK_SNAPSHOT_STORE_UNWRITABLE


def test_the_board_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "read_snapshots", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert g.snapshots_status(tmp_path) == {"readable": False, "error": "RuntimeError", "count": None,
                                            "last_created_at": None}


# --- the decision's age at the send (PR2c-1, Thomas decision 24) ---------------------------------

DECIDED = "2026-09-17T04:05:30Z"


@pytest.mark.parametrize("decided_at", [
    None, "", "soon", "2026-09-17 04:05:30", 1758081930,
    # Readable instants, but not the one form this runtime writes.
    "2026-09-17T04:05:30+00:00", "20260917T040530Z", "2026-09-17T04:05:30.5Z", "2026-09-17T04:05:30Z\n",
])
def test_a_door_that_does_not_say_when_it_judged_is_refused(decided_at):
    snapshot = g.evaluate_pre_order_gate(
        _intent(), purpose=PURPOSE_AUTONOMOUS, venue=VENUE_MAINNET, checks=[g.check("door_ok", True)],
        profile=_profile(), lineage=_lineage(_intent()), facts={}, now=NOW, decided_at=decided_at,
    )
    assert snapshot["approved"] is False
    assert snapshot["failed_checks"] == [g.CHECK_DECIDED_AT]


def test_the_gate_seals_when_the_door_judged_over_what_the_door_says():
    snapshot = _gate(facts={"decided_at": "2026-01-01T00:00:00Z", "spread_bps": 1.0}, decided_at=DECIDED)
    assert snapshot["facts"]["decided_at"] == DECIDED
    assert snapshot["created_at"] == NOW
    assert g.decision_age_seconds(snapshot, clock="2026-09-17T04:06:00Z") == 30.0


@pytest.mark.parametrize("clock,stale", [
    ("2026-09-17T04:05:30Z", False),     # sent the second it was judged
    ("2026-09-17T04:06:30Z", False),     # exactly a minute later
    ("2026-09-17T04:06:31Z", True),      # a second past it
    ("2026-09-17T04:05:29Z", True),      # judged after the send: the clock went back
    ("whenever", True),                  # a send time that cannot be read
    ("2026-09-17T04:05:40+00:00", True),  # nor one in a form this runtime does not write
])
def test_a_decision_may_wait_at_most_a_minute_for_its_send(clock, stale):
    intent, snapshot = approved_snapshot(_intent(), decided_at=DECIDED)
    if stale:
        with pytest.raises(ToolError) as refused:
            g.verify_snapshot(intent, snapshot, clock=clock)
        assert refused.value.reason_code == g.RISK_SNAPSHOT_STALE
    else:
        assert g.verify_snapshot(intent, snapshot, clock=clock) == snapshot["risk_snapshot_sha256"]


def test_the_send_is_judged_at_the_wall_clock_unless_told(monkeypatch):
    intent, snapshot = approved_snapshot(_intent(), decided_at=DECIDED)
    monkeypatch.setattr(g, "_send_clock", lambda: "2026-09-17T04:06:31Z")
    with pytest.raises(ToolError) as refused:
        g.verify_snapshot(intent, snapshot)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_STALE
    assert "61s before this send" in str(refused.value)
    monkeypatch.setattr(g, "_send_clock", lambda: "2026-09-17T04:06:00Z")
    assert g.verify_snapshot(intent, snapshot) == snapshot["risk_snapshot_sha256"]


def test_a_snapshot_whose_decision_time_was_removed_is_stale_not_unsupported():
    """Not a record the gate writes — but the seal only says nothing changed, so the send checks it."""
    intent, snapshot = approved_snapshot(_intent(), decided_at=DECIDED)
    stripped = _resealed(snapshot, facts={k: v for k, v in snapshot["facts"].items() if k != "decided_at"})
    stripped_intent = g.bind_intent(intent, stripped)
    with pytest.raises(ToolError) as refused:
        g.verify_snapshot(stripped_intent, stripped, clock=DECIDED)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_STALE
    assert "does not say when" in str(refused.value)


def test_a_snapshot_without_the_decision_time_check_does_not_support_its_approval():
    intent, snapshot = approved_snapshot(_intent(), decided_at=DECIDED)
    forged = _without_check(snapshot, g.CHECK_DECIDED_AT)
    with pytest.raises(ToolError) as refused:
        g.verify_snapshot(g.bind_intent(intent, forged), forged, clock=DECIDED)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_UNSUPPORTED


def test_a_stale_decision_is_neither_recorded_nor_sent(monkeypatch):
    events: list[str] = []
    intent, snapshot = approved_snapshot(_intent(), decided_at=DECIDED)
    monkeypatch.setattr(g, "_send_clock", lambda: "2026-09-17T04:07:00Z")
    adapter, store = _Adapter(events), _RecordingStore(events)
    with pytest.raises(lx.SubmitRefused) as refused:
        lx.submit_and_reconcile(intent, adapter=adapter, guard_verdict=APPROVED, now=NOW,
                                risk_snapshot=snapshot, snapshot_store=store)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_STALE
    assert events == [] and adapter.submitted == []


def test_the_binding_judges_the_send_at_the_clock_it_is_given(monkeypatch):
    events: list[str] = []
    intent, snapshot = approved_snapshot(_intent(), decided_at=DECIDED)
    store = _RecordingStore(events)
    monkeypatch.setattr(g, "_send_clock", lambda: "2026-09-17T04:05:40Z")    # the wall clock: fresh
    with pytest.raises(ToolError) as refused:
        g.verify_and_persist(intent, snapshot, store=store, clock="2026-09-17T04:07:00Z")
    assert refused.value.reason_code == g.RISK_SNAPSHOT_STALE and events == []
    monkeypatch.setattr(g, "_send_clock", lambda: "2026-09-17T04:07:00Z")    # the wall clock: stale
    assert g.verify_and_persist(intent, snapshot, store=store, clock="2026-09-17T04:05:40Z") == (
        snapshot["risk_snapshot_sha256"])
    assert events == ["record"]


def test_the_record_of_an_old_order_stays_readable(tmp_path):
    """Age bounds the send, not the record: a row written a year ago is still the order's reason."""
    _, snapshot = approved_snapshot(_intent(), decided_at="2025-09-17T04:05:30Z")
    _store(tmp_path).append(snapshot)
    assert g.read_snapshots(tmp_path) == [snapshot]
    assert g.snapshots_status(tmp_path)["readable"] is True


def _pr2b_row(snapshot):
    """A row as the PR2b gate sealed it: six gate checks and no decision time."""
    body = {k: v for k, v in snapshot.items() if k != "risk_snapshot_sha256"}
    body["checks"] = [c for c in body["checks"] if c["check"] != g.CHECK_DECIDED_AT]
    body["facts"] = {k: v for k, v in body["facts"].items() if k != "decided_at"}
    return _resealed(body)


def test_a_row_the_pr2b_gate_sealed_is_still_a_record_but_never_a_send(tmp_path):
    """Review of #885: adding the decision time check made every older row unreadable, and the
    readiness board would have called the record edited."""
    intent, snapshot = approved_snapshot(_intent(), decided_at=DECIDED)
    old = _pr2b_row(snapshot)
    path = g.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(old, sort_keys=True) + "\n", encoding="ascii")
    assert g.read_snapshots(tmp_path) == [old]
    assert g.snapshots_status(tmp_path)["readable"] is True
    assert g.find_snapshot(old["risk_snapshot_sha256"], tmp_path) == old
    with pytest.raises(ToolError) as refused:
        g.verify_snapshot(g.bind_intent(intent, old), old, clock=DECIDED)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_UNSUPPORTED


def _pre_arm_verification_row(snapshot):
    """An autonomous row as the gate sealed it before PR2c-2b: its arm names no verification."""
    profile = dict(snapshot["approved_profile"])
    profile["authority"] = {k: v for k, v in profile["authority"].items()
                            if k not in (g.LIVE_ARM_VERIFIED_FIELD, "approval_fingerprint", "approval_problem")}
    return _reprofiled(snapshot, profile)


def test_a_row_sealed_before_arms_were_verified_is_still_a_record_but_never_a_send(tmp_path):
    intent, snapshot = approved_snapshot(_intent(), decided_at=DECIDED)
    old = _pre_arm_verification_row(snapshot)
    assert "approval_verified" not in json.dumps(old)
    path = g.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(old, sort_keys=True) + "\n", encoding="ascii")
    assert g.read_snapshots(tmp_path) == [old]
    assert g.find_snapshot(old["risk_snapshot_sha256"], tmp_path) == old
    with pytest.raises(ToolError) as refused:
        g.verify_snapshot(g.bind_intent(intent, old), old, clock=DECIDED)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_UNSUPPORTED
    assert "the arming approval was not verified" in str(refused.value)


_V1_BOUND_FIELDS = tuple(f for f in g.INTENT_BOUND_FIELDS if f != "strategy_artifact_sha256")


def _sealed_before_the_artifact(monkeypatch):
    """An autonomous order and its row as the gate sealed them before PR3a-2: an intent with no
    artifact, a v1 fingerprint over the fields bound then, and a lineage that does not name it."""
    with monkeypatch.context() as m:
        m.setattr(g, "INTENT_FINGERPRINT_VERSION", "pre_order_intent.v1")
        m.setattr(g, "INTENT_BOUND_FIELDS", _V1_BOUND_FIELDS)
        m.setattr(g, "LINEAGE_FIELDS", g.PRE_ARTIFACT_LINEAGE_FIELDS)
        intent = {k: v for k, v in _intent().items() if k != "strategy_artifact_sha256"}
        return approved_snapshot(intent, decided_at=DECIDED)


def test_a_row_sealed_before_the_artifact_rode_on_the_order_is_still_a_record_but_never_a_send(
        tmp_path, monkeypatch):
    """PR3a-2 added the artifact to the bound fields and the autonomous lineage. A row sealed
    before then is still the record of its order, which the readiness board reads: its v1
    fingerprint is never recomputed. It can never authorize an order."""
    intent, old = _sealed_before_the_artifact(monkeypatch)
    assert "strategy_artifact_sha256" not in old["lineage"]
    assert old["intent_fingerprint"] != g.intent_fingerprint(intent)     # v1, not what v2 computes
    path = g.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(old, sort_keys=True) + "\n", encoding="ascii")
    assert g.read_snapshots(tmp_path) == [old]
    assert g.find_snapshot(old["risk_snapshot_sha256"], tmp_path) == old
    with pytest.raises(ToolError) as refused:
        g.verify_snapshot(intent, old, clock=DECIDED)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_UNSUPPORTED
    assert "the lineage is missing strategy_artifact_sha256" in str(refused.value)


@pytest.mark.parametrize("value", [None, "", "  "])
def test_a_stored_row_that_names_the_artifact_empty_fails_the_verified_read(tmp_path, value):
    """No gate ever approved such a row: the old one never wrote the key, and this one refuses an
    empty one at `lineage_complete`. The read takes the older shape only when the key is absent."""
    _, snapshot = approved_snapshot(_intent(), decided_at=DECIDED)
    body = {k: v for k, v in snapshot.items() if k != "risk_snapshot_sha256"}
    body["lineage"] = {**body["lineage"], "strategy_artifact_sha256": value}
    forged = _resealed(body)
    path = g.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(forged, sort_keys=True) + "\n", encoding="ascii")
    with pytest.raises(ToolError) as refused:
        g.read_snapshots(tmp_path)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_STORE_TAMPERED


def test_an_autonomous_order_that_names_no_artifact_is_refused_at_the_gate():
    """Decision 33 at the gate itself (PR3a-2): an order routed from an entry that predates the
    artifact names none, and the gate refuses it whatever the arm check before it said."""
    snapshot = _gate(_intent(strategy_artifact_sha256=None))
    assert snapshot["approved"] is False and snapshot["failed_checks"] == [g.CHECK_LINEAGE]
    [lineage] = [c for c in snapshot["checks"] if c["check"] == g.CHECK_LINEAGE]
    assert lineage["detail"] == "missing strategy_artifact_sha256"


def test_a_snapshot_sealed_for_one_artifact_does_not_send_the_order_as_another():
    """The artifact is bound (PR3a-2): the same order, named as another strategy after the gate, is
    not the order the snapshot approved — though its venue identity is unchanged."""
    intent, snapshot = approved_snapshot(_intent())
    moved = {**intent, "strategy_artifact_sha256": "sha256:" + "e" * 64}
    assert moved["client_order_id"] == intent["client_order_id"]
    g.verify_snapshot(intent, snapshot)
    with pytest.raises(ToolError) as refused:
        g.verify_snapshot(moved, snapshot)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_INTENT_MISMATCH


def test_a_stored_row_whose_arm_was_not_verified_fails_the_verified_read(tmp_path):
    _, snapshot = approved_snapshot(_intent(), decided_at=DECIDED)
    forged = _reprofiled(snapshot, _profile(authority={**_VERIFIED_ARM, g.LIVE_ARM_VERIFIED_FIELD: False}))
    path = g.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(forged, sort_keys=True) + "\n", encoding="ascii")
    with pytest.raises(ToolError) as refused:
        g.read_snapshots(tmp_path)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_STORE_TAMPERED


def test_a_row_that_names_the_decision_check_must_carry_every_current_check(tmp_path):
    _, snapshot = approved_snapshot(_intent(), decided_at=DECIDED)
    forged = _without_check(snapshot, g.CHECK_VENUE)
    path = g.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(forged, sort_keys=True) + "\n", encoding="ascii")
    with pytest.raises(ToolError) as refused:
        g.read_snapshots(tmp_path)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_STORE_TAMPERED


def test_a_pr2b_row_missing_one_of_its_own_checks_is_still_refused(tmp_path):
    _, snapshot = approved_snapshot(_intent(), decided_at=DECIDED)
    old = _without_check(_pr2b_row(snapshot), g.CHECK_LINEAGE)
    path = g.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(old, sort_keys=True) + "\n", encoding="ascii")
    with pytest.raises(ToolError) as refused:
        g.read_snapshots(tmp_path)
    assert refused.value.reason_code == g.RISK_SNAPSHOT_STORE_TAMPERED
