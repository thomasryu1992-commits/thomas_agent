"""Bounded schedule delegation and scheduled workflow submissions (sequence 2, P09; V0.2 §1.3;
acceptance A20, A21).

A21 — the assistant may change a schedule only inside the scope the policy delegates: inside,
the change is applied and recorded like an operator's; outside, it is recorded as a proposal
and nothing changes; a financial kind is refused outright; with no clause in the policy every
change is refused. A20 — a `workflow_plan` schedule submits its plan under the occurrence's
`schedule_run_id`: one acceptance per occurrence across duplicate ticks, clock jumps and a
restart; a re-fire of the same occurrence replays; a missed occurrence is dropped, never
caught up.

The committed policy (1.5.0) has no `assistant_schedule` clause, so these tests inject the
scope the 1.6.0 draft names (`docs/runtime-contracts/POLICY_1_6_0_DRAFT.md`) — the code is
the same either way; the clause is the switch.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from runtime.mvp_runtime import dispatch_bridge, schedule_delegation as sd, scheduler, workflow as wf
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import ControlBlocked, SchedulerBlocked
from runtime.mvp_runtime.scheduler import KIND_TASK, KIND_WORKFLOW, ScheduleStore, build_schedule, run_due
from runtime.mvp_runtime.scheduler_cli import LOCAL_ACTOR, main as scheduler_main
from runtime.mvp_runtime.socket_door import ASSISTANT_ACTOR
from runtime.mvp_runtime.store import LedgerStore
from runtime.mvp_runtime.workflow_store import WorkflowStore

T0 = "2026-09-15T09:00:00Z"
T1 = "2026-09-15T10:00:00Z"        # T0 + 1h
T3 = "2026-09-15T12:00:00Z"        # T0 + 3h
LATER_DAY = "2026-09-20T09:00:00Z"

SCOPE = {
    "actor": "assistant_bridge", "mutation_allowed": True,
    "delegated_kinds": ["analysis_task", "workflow_plan"],
    "min_interval_seconds": 3600, "max_active": 2, "max_validity_days": 30, "max_model_calls_per_run": 6,
    "financial_kinds_delegable": False, "out_of_scope": "proposal_only", "gate_grants_authority": False,
}


def _plan(calls=3):
    return {"schema_version": "workflow_plan.v0.1", "goal": "주간 시장 조사",
            "steps": [{"id": "research", "capability": "research", "request": "조사", "reason": "r"}],
            "budget": {"max_model_calls": calls}}


def _events(ledger):
    text = (ledger.root / "scheduler_events.jsonl").read_text(encoding="utf-8").strip()
    return [json.loads(line) for line in text.splitlines() if line]


@pytest.fixture
def stores(tmp_path):
    return ScheduleStore(tmp_path), LedgerStore(tmp_path / "ledger")


@pytest.fixture
def delegation():
    return sd.Delegation.from_clause(SCOPE)


def _change(**kw):
    raw = {"action": "create", "reason": "테스트", "kind": KIND_TASK, "request": "조사", "interval_seconds": 7200}
    raw.update(kw)
    return sd.ChangeRequest.parse(raw)


# --- the clause is the switch ------------------------------------------------------------------------

def _bump_script():
    import importlib.util
    import pathlib as _pl

    path = _pl.Path(__file__).resolve().parents[1] / "scripts" / "ops" / "policy_bump_1_6_0.py"
    spec = importlib.util.spec_from_file_location("policy_bump_1_6_0", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_policy_and_the_draft_clause_agree_with_the_runtime():
    """Before the bump the committed policy has no clause and the door is dormant; after it the
    scope the door loads IS the clause. Either way the draft block the bump script writes loads as
    a scope that reaches no financial kind — checked here, not first at bump time (review of P09)."""
    import yaml

    policy = yaml.safe_load((pathlib.Path(__file__).resolve().parents[1] / "governance" / "GOVERNANCE_POLICY.yaml")
                            .read_text(encoding="utf-8"))
    clause = (policy.get("control_channel") or {}).get(sd.CLAUSE)
    if clause is None:
        assert sd.load_delegation() is None
    else:
        assert sd.load_delegation() == sd.Delegation.from_clause(clause)
    draft = sd.Delegation.from_clause(yaml.safe_load(_bump_script().SCHEDULE_BLOCK)[sd.CLAUSE])
    assert not (draft.kinds & sd.FINANCIAL_KINDS) and draft.kinds <= scheduler.MAINTENANCE_KINDS
    assert draft.min_interval_seconds >= 3600 and draft.max_validity_days * 86_400 > draft.min_interval_seconds
    assert sd.Delegation.from_clause(SCOPE).kinds == frozenset({KIND_TASK, KIND_WORKFLOW})


@pytest.mark.parametrize("bad", [
    {**SCOPE, "mutation_allowed": False},
    {**SCOPE, "delegated_kinds": []},
    {**SCOPE, "delegated_kinds": ["analysis_task", "crypto_pipeline"]},        # financial: never delegable
    {**SCOPE, "delegated_kinds": ["analysis_task", "crypto_report"]},
    {**SCOPE, "delegated_kinds": ["nope"]},
    {**SCOPE, "min_interval_seconds": 10},
    {**SCOPE, "max_active": 0},
    {**SCOPE, "max_validity_days": True},
])
def test_a_clause_that_does_not_say_what_it_delegates_delegates_nothing(bad):
    with pytest.raises(ControlBlocked) as exc:
        sd.Delegation.from_clause(bad)
    assert exc.value.reason_code == "SCHEDULE_DELEGATION_INVALID"


def test_without_a_clause_every_assistant_change_is_refused_and_nothing_is_recorded(stores):
    store, ledger = stores
    with pytest.raises(ControlBlocked) as exc:
        sd.apply_change(store, ledger, _change(), actor=ASSISTANT_ACTOR, now=T0, delegation=None, bounded=True)
    assert exc.value.reason_code == "SCHEDULE_DELEGATION_DISABLED"
    assert store.list() == [] and not (ledger.root / "scheduler_events.jsonl").exists()


def test_the_financial_kinds_are_every_crypto_kind_and_the_risk_lane():
    assert scheduler.RISK_KINDS <= sd.FINANCIAL_KINDS
    assert {k for k in scheduler.KINDS if k.startswith("crypto_")} <= sd.FINANCIAL_KINDS
    assert "candle_archive" in sd.FINANCIAL_KINDS and KIND_TASK not in sd.FINANCIAL_KINDS and KIND_WORKFLOW not in sd.FINANCIAL_KINDS


# --- A21: inside the scope applied, outside proposed, financial refused ---------------------------------

def test_a_change_inside_the_scope_is_applied_recorded_and_ends_itself(stores, delegation):
    store, ledger = stores
    outcome = sd.apply_change(store, ledger, _change(), actor=ASSISTANT_ACTOR, now=T0, delegation=delegation, bounded=True)
    assert outcome.applied and outcome.verdict == "APPLIED"
    (row,) = store.list()
    assert row.created_by == ASSISTANT_ACTOR and row.kind == KIND_TASK and row.interval_seconds == 7200
    assert row.expires_at == "2026-10-15T09:00:00Z" and row.enabled
    (event,) = _events(ledger)
    assert event["action"] == "created" and event["schedule_id"] == row.schedule_id and event["reason"] == "테스트"
    # the assistant may disable and re-enable what it created, and remove it
    for action in ("disable", "enable", "remove"):
        out = sd.apply_change(store, ledger, _change(action=action, schedule_id=row.schedule_id, kind=None, request=None,
                                                   interval_seconds=None),
                              actor=ASSISTANT_ACTOR, now=T1, delegation=delegation, bounded=True)
        assert out.applied
    assert store.list() == [] and [e["action"] for e in _events(ledger)] == ["created", "disabled", "enabled", "removed"]


@pytest.mark.parametrize("change_kw, phrase", [
    ({"interval_seconds": 600}, "below the delegated minimum"),
    ({"kind": "memory_prune", "request": ""}, "is not delegated"),
    ({"kind": KIND_WORKFLOW, "request": json.dumps(_plan(calls=9))}, "exceeds the delegated 6 per run"),
])
def test_a_change_outside_the_scope_is_proposed_not_applied(stores, delegation, change_kw, phrase):
    store, ledger = stores
    outcome = sd.apply_change(store, ledger, _change(**change_kw), actor=ASSISTANT_ACTOR, now=T0,
                              delegation=delegation, bounded=True)
    assert outcome.proposed and not outcome.applied and outcome.verdict == "PROPOSED"
    assert any(phrase in r for r in outcome.reasons)
    assert store.list() == []
    (event,) = _events(ledger)
    assert event["action"] == "proposed" and event["proposed_by"] == ASSISTANT_ACTOR and event["out_of_scope"] == list(outcome.reasons)
    assert "PROPOSED (not applied)" in sd.render_outcome(_change(**change_kw), outcome)


def test_the_ceiling_on_active_delegated_schedules_holds_and_thomas_s_own_rows_are_out_of_scope(stores, delegation):
    store, ledger = stores
    for i in range(2):
        assert sd.apply_change(store, ledger, _change(request=f"조사 {i}"), actor=ASSISTANT_ACTOR, now=T0,
                               delegation=delegation, bounded=True).applied
    third = sd.apply_change(store, ledger, _change(request="조사 3"), actor=ASSISTANT_ACTOR, now=T0,
                            delegation=delegation, bounded=True)
    assert third.proposed and any("delegated ceiling is 2" in r for r in third.reasons)
    # a schedule Thomas created (the container CLI) is not the assistant's to disable — proposed
    mine = build_schedule(kind=KIND_TASK, request="Thomas's", interval_seconds=3600, created_by=LOCAL_ACTOR, now=T0)
    store.add(mine)
    out = sd.apply_change(store, ledger, _change(action="disable", schedule_id=mine.schedule_id, kind=None, request=None,
                                                interval_seconds=None),
                          actor=ASSISTANT_ACTOR, now=T1, delegation=delegation, bounded=True)
    assert out.proposed and any("not by the assistant" in r for r in out.reasons)
    assert next(s for s in store.list() if s.schedule_id == mine.schedule_id).enabled


@pytest.mark.parametrize("kind", sorted(sd.FINANCIAL_KINDS))
def test_a_financial_schedule_is_refused_by_name_and_never_proposed(stores, delegation, kind):
    store, ledger = stores
    with pytest.raises(ControlBlocked) as exc:
        sd.apply_change(store, ledger, _change(kind=kind, request="BTCUSDT 4h"), actor=ASSISTANT_ACTOR, now=T0,
                        delegation=delegation, bounded=True)
    assert exc.value.reason_code == "FINANCIAL_SCHEDULE_REFUSED"
    # nor may the assistant touch an existing financial row, even one it somehow owns
    row = build_schedule(kind=kind, request="BTCUSDT 4h", interval_seconds=3600, created_by=ASSISTANT_ACTOR, now=T0)
    store.add(row)
    with pytest.raises(ControlBlocked) as exc:
        sd.apply_change(store, ledger, _change(action="disable", schedule_id=row.schedule_id, kind=None, request=None,
                                              interval_seconds=None),
                        actor=ASSISTANT_ACTOR, now=T1, delegation=delegation, bounded=True)
    assert exc.value.reason_code == "FINANCIAL_SCHEDULE_REFUSED"
    assert store.list()[0].enabled and not (ledger.root / "scheduler_events.jsonl").exists()


def test_the_container_cli_is_unbounded_and_shares_the_validation(stores, capsys):
    store, ledger = stores
    assert scheduler_main(["add", "--kind", "crypto_report", "--interval-seconds", "60"], store=store, ledger=ledger, now=T0) == 0
    (row,) = store.list()
    assert row.created_by == LOCAL_ACTOR and row.expires_at is None                     # no scope, no expiry
    assert scheduler_main(["add", "--kind", "workflow_plan", "--request", "{bad json", "--interval-seconds", "3600"],
                          store=store, ledger=ledger, now=T0) == 2
    assert "INVALID_PLAN" in capsys.readouterr().err and len(store.list()) == 1


@pytest.mark.parametrize("raw, phrase", [
    ("x", "must be an object"),
    ({"action": "update", "reason": "r"}, "change.action"),
    ({"action": "create", "reason": "", "kind": KIND_TASK, "interval_seconds": 3600}, "change.reason"),
    ({"action": "create", "reason": "r", "interval_seconds": 3600}, "change.kind"),
    ({"action": "create", "reason": "r", "kind": KIND_TASK, "interval_seconds": "3600"}, "interval_seconds"),
    ({"action": "create", "reason": "r", "kind": KIND_TASK, "interval_seconds": 3600, "schedule_id": "x"}, "not accepted on create"),
    ({"action": "enable", "reason": "r"}, "schedule_id"),
    ({"action": "enable", "reason": "r", "schedule_id": "s", "kind": KIND_TASK}, "not accepted on enable"),
    ({"action": "enable", "reason": "r", "schedule_id": "s", "extra": 1}, "accepts"),
])
def test_a_malformed_change_is_refused_before_anything_is_read(raw, phrase):
    with pytest.raises(ControlBlocked) as exc:
        sd.ChangeRequest.parse(raw)
    assert exc.value.reason_code == "SCHEDULE_CHANGE_INVALID" and phrase in str(exc.value)


# --- the door ----------------------------------------------------------------------------------------

def _door(request, tmp_path, *, schedule_store, ledger, delegation, control=None):
    return dispatch_bridge.apply_dispatch(
        request, control_store=control or ControlStore(tmp_path), workflow_store=WorkflowStore(tmp_path),
        manager_enabled=True, schedule_store=schedule_store, ledger=ledger, delegation=delegation, now=T0,
    )


def test_the_door_applies_proposes_and_refuses_by_the_same_rules(tmp_path, stores, delegation):
    store, ledger = stores
    change = {"action": "create", "reason": "주간 조사", "kind": KIND_TASK, "request": "조사", "interval_seconds": 7200}
    out = _door({"command": "schedule.propose_change", "change": change, "proto": 2}, tmp_path,
                schedule_store=store, ledger=ledger, delegation=delegation)
    assert out["ok"] and out["reply"].startswith("APPLIED: created schedule") and out["data"]["verdict"] == "APPLIED"
    assert out["data"]["schedule"]["created_by"] == ASSISTANT_ACTOR and out["proto"] == 2
    out = _door({"command": "schedule.propose_change", "change": {**change, "interval_seconds": 60}}, tmp_path,
                schedule_store=store, ledger=ledger, delegation=delegation)
    assert out["data"]["verdict"] == "PROPOSED" and out["reply"].startswith("PROPOSED (not applied)")
    with pytest.raises(ControlBlocked) as exc:
        _door({"command": "schedule.propose_change", "change": {**change, "kind": "crypto_pipeline"}}, tmp_path,
              schedule_store=store, ledger=ledger, delegation=delegation)
    assert exc.value.reason_code == "FINANCIAL_SCHEDULE_REFUSED"
    with pytest.raises(ControlBlocked) as exc:
        _door({"command": "schedule.propose_change", "change": change}, tmp_path,
              schedule_store=store, ledger=ledger, delegation=None)
    assert exc.value.reason_code == "SCHEDULE_DELEGATION_DISABLED"
    with pytest.raises(ControlBlocked) as exc:
        _door({"command": "schedule.propose_change", "change": change}, tmp_path,
              schedule_store=None, ledger=ledger, delegation=delegation)
    assert exc.value.reason_code == "SCHEDULES_UNAVAILABLE"
    assert len(store.list()) == 1
    assert "schedule.propose_change" in _door({"command": "capabilities"}, tmp_path, schedule_store=store, ledger=ledger,
                                              delegation=delegation)["data"]["commands"]


def test_a_halted_runtime_changes_no_schedule(tmp_path, stores, delegation, monkeypatch):
    store, ledger = stores
    control = ControlStore(tmp_path)

    class _Halted:
        mode = "KILLED"
        execution_allowed = False

        @staticmethod
        def refusal_reason_code():
            return "KILLED"

    monkeypatch.setattr(control, "load", lambda: _Halted())
    with pytest.raises(ControlBlocked) as exc:
        _door({"command": "schedule.propose_change", "change": {"action": "create", "reason": "r", "kind": KIND_TASK,
                                                                 "request": "x", "interval_seconds": 7200}},
              tmp_path, schedule_store=store, ledger=ledger, delegation=delegation, control=control)
    assert exc.value.reason_code == "KILLED" and store.list() == []


# --- A20: a workflow_plan schedule submits once per occurrence ------------------------------------------

def test_a_workflow_plan_fire_submits_once_per_occurrence_and_a_re_fire_replays(tmp_path, stores):
    store, ledger = stores
    workflows = WorkflowStore(tmp_path)
    sched = build_schedule(kind=KIND_WORKFLOW, request=json.dumps(_plan()), interval_seconds=3600,
                           created_by=ASSISTANT_ACTOR, now=T0)
    store.add(sched)
    control = ControlStore(tmp_path)
    summary = run_due(store, now=T1, control_store=control, ledger=ledger, workflow_store=workflows)
    assert summary["fired"] == 1 and summary["failed"] == 0
    (result,) = summary["results"]
    run_id = result["schedule_run_id"]
    assert result["status"].startswith("workflow:wf_") and result["status"].endswith(":VALIDATED")
    (row,) = workflows.list_workflows()
    assert row["principal"] == scheduler.WORKFLOW_PRINCIPAL and row["status"] == wf.W_VALIDATED
    # the same occurrence re-fired (a retry after a crash between submit and record) replays
    outcome = workflows.submit(principal=scheduler.WORKFLOW_PRINCIPAL, request_id=f"{sched.schedule_id}:{run_id}",
                               plan=_plan(), now=T1)
    assert outcome.replayed and outcome.workflow_id == row["workflow_id"] and len(workflows.list_workflows()) == 1
    # a duplicate tick at the same instant claims nothing: one occurrence, one acceptance
    again = run_due(store, now=T1, control_store=control, ledger=ledger, workflow_store=workflows)
    assert again["fired"] == 0 and len(workflows.list_workflows()) == 1
    started = [e for e in _events(ledger) if e["action"] == "started"]
    assert len(started) == 1 and started[0]["schedule_run_id"] == run_id


def test_a_clock_jump_fires_once_and_a_restart_never_catches_up(tmp_path, stores):
    store, ledger = stores
    workflows = WorkflowStore(tmp_path)
    sched = build_schedule(kind=KIND_WORKFLOW, request=json.dumps(_plan()), interval_seconds=3600,
                           created_by=ASSISTANT_ACTOR, now=T0)
    store.add(sched)
    control = ControlStore(tmp_path)
    # three hours pass in one go (a stopped process, a clock moved forward): one fire, not three
    summary = run_due(store, now=T3, control_store=control, ledger=ledger, workflow_store=workflows)
    assert summary["fired"] == 1 and len(workflows.list_workflows()) == 1
    assert store.list()[0].next_run_at == "2026-09-15T13:00:00Z"                    # the grid, past now
    # a "restart" — a fresh store handle over the same files — sees nothing due and fires nothing
    fresh = ScheduleStore(tmp_path)
    assert run_due(fresh, now=T3, control_store=control, ledger=ledger, workflow_store=WorkflowStore(tmp_path))["fired"] == 0
    assert len(WorkflowStore(tmp_path).list_workflows()) == 1


def test_a_workflow_plan_fire_without_a_workflow_store_fails_by_name(tmp_path, stores):
    store, ledger = stores
    store.add(build_schedule(kind=KIND_WORKFLOW, request=json.dumps(_plan()), interval_seconds=3600,
                             created_by=LOCAL_ACTOR, now=T0))
    summary = run_due(store, now=T1, control_store=ControlStore(tmp_path), ledger=ledger, workflow_store=None)
    assert summary["failed"] == 1 and summary["results"][0]["status"] == "failed:WORKFLOW_UNAVAILABLE"
    assert [e["action"] for e in _events(ledger)] == ["started", "failed"]


def test_a_workflow_plan_schedule_validates_its_plan_at_registration():
    for bad in ("{", "[]", json.dumps({"schema_version": "workflow_plan.v0.1", "goal": "g", "steps": [], "budget": {"max_model_calls": 1}})):
        with pytest.raises(SchedulerBlocked) as exc:
            build_schedule(kind=KIND_WORKFLOW, request=bad, interval_seconds=3600, created_by=LOCAL_ACTOR, now=T0)
        assert exc.value.reason_code == "INVALID_PLAN"


def test_an_expired_delegated_schedule_is_disabled_at_its_next_occurrence_and_never_fires(tmp_path, stores, delegation):
    store, ledger = stores
    workflows = WorkflowStore(tmp_path)
    sd.apply_change(store, ledger, _change(kind=KIND_WORKFLOW, request=json.dumps(_plan()), interval_seconds=3600),
                    actor=ASSISTANT_ACTOR, now=T0, delegation=delegation, bounded=True)
    (row,) = store.list()
    assert row.expires_at == "2026-10-15T09:00:00Z"
    summary = run_due(store, now="2026-10-16T09:00:00Z", control_store=ControlStore(tmp_path), ledger=ledger,
                      workflow_store=workflows)
    assert summary["fired"] == 0 and summary["results"] == [{"schedule_id": row.schedule_id, "action": "expired", "status": "disabled"}]
    assert store.list()[0].enabled is False and workflows.list_workflows() == []
    assert [e["action"] for e in _events(ledger)] == ["created", "expired"]
    assert _events(ledger)[-1]["previously_enabled"] is True
    # nothing fires afterwards either
    assert run_due(store, now="2026-10-17T09:00:00Z", control_store=ControlStore(tmp_path), ledger=ledger,
                   workflow_store=workflows)["fired"] == 0


def test_the_workflow_kind_belongs_to_the_maintenance_lane_and_the_kind_sets_stay_partitioned():
    assert KIND_WORKFLOW in scheduler.MAINTENANCE_KINDS and KIND_WORKFLOW not in scheduler.RISK_KINDS
    assert scheduler.RISK_KINDS | scheduler.MAINTENANCE_KINDS == scheduler.KINDS
    assert not (scheduler.RISK_KINDS & scheduler.MAINTENANCE_KINDS)



# --- independent review of P09 (2026-09-14) ----------------------------------------------------------

def test_renewing_an_expired_delegated_schedule_is_proposed_not_applied(stores, delegation):
    store, ledger = stores
    first = sd.apply_change(store, ledger, _change(), actor=ASSISTANT_ACTOR, now=T0, delegation=delegation, bounded=True)
    run_due(store, now="2026-10-16T09:00:00Z", control_store=ControlStore(store.path.parents[1]), ledger=ledger)
    assert store.list()[0].enabled is False                                          # expired by the tick
    again = sd.apply_change(store, ledger, _change(), actor=ASSISTANT_ACTOR, now="2026-10-16T10:00:00Z",
                            delegation=delegation, bounded=True)
    assert again.proposed and any("renewing it is Thomas's decision" in r for r in again.reasons)
    enable = sd.apply_change(store, ledger, _change(action="enable", schedule_id=first.schedule.schedule_id, kind=None,
                                                   request=None, interval_seconds=None),
                             actor=ASSISTANT_ACTOR, now="2026-10-16T10:00:00Z", delegation=delegation, bounded=True)
    assert enable.proposed and len(store.list()) == 1 and store.list()[0].enabled is False


def test_an_interval_that_could_never_fire_inside_the_validity_is_proposed(stores, delegation):
    store, ledger = stores
    out = sd.apply_change(store, ledger, _change(interval_seconds=30 * 86_400), actor=ASSISTANT_ACTOR, now=T0,
                          delegation=delegation, bounded=True)
    assert out.proposed and any("could never run" in r for r in out.reasons) and store.list() == []


def test_a_retried_create_returns_the_schedule_the_lost_reply_made(stores, delegation):
    store, ledger = stores
    first = sd.apply_change(store, ledger, _change(), actor=ASSISTANT_ACTOR, now=T0, delegation=delegation, bounded=True)
    retry = sd.apply_change(store, ledger, _change(), actor=ASSISTANT_ACTOR, now=T1, delegation=delegation, bounded=True)
    assert retry.applied and retry.schedule.schedule_id == first.schedule.schedule_id and len(store.list()) == 1
    assert [e["action"] for e in _events(ledger)] == ["created"]


def test_concurrent_creates_cannot_pass_the_ceiling_together(stores, delegation):
    import threading

    store, ledger = stores
    barrier = threading.Barrier(6)
    outcomes = []

    def create(i):
        barrier.wait()
        outcomes.append(sd.apply_change(store, None, _change(request=f"조사 {i}"), actor=ASSISTANT_ACTOR, now=T0,
                                        delegation=delegation, bounded=True))

    threads = [threading.Thread(target=create, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sum(1 for o in outcomes if o.applied) == delegation.max_active == 2
    assert sum(1 for s in store.list() if s.enabled) == 2 and sum(1 for o in outcomes if o.proposed) == 4


def test_an_invalid_clause_refuses_every_change_by_name_and_does_not_take_the_door_down(tmp_path, stores, monkeypatch):
    store, ledger = stores
    policy = tmp_path / "governance" / "GOVERNANCE_POLICY.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text("control_channel:\n  assistant_schedule:\n    mutation_allowed: true\n    delegated_kinds: [some_future_kind]\n",
                      encoding="utf-8")
    loaded = sd.load_delegation_safely(tmp_path)
    assert isinstance(loaded, sd.InvalidDelegation) and "SCHEDULE_DELEGATION_INVALID" in loaded.reason
    with pytest.raises(ControlBlocked) as exc:
        _door({"command": "schedule.propose_change", "change": {"action": "create", "reason": "r", "kind": KIND_TASK,
                                                                 "request": "x", "interval_seconds": 7200}},
              tmp_path, schedule_store=store, ledger=ledger, delegation=loaded)
    assert exc.value.reason_code == "SCHEDULE_DELEGATION_INVALID" and store.list() == []


def test_a_workflow_plan_occurrence_is_dropped_while_the_previous_one_is_still_open(tmp_path, stores):
    store, ledger = stores
    workflows = WorkflowStore(tmp_path)
    store.add(build_schedule(kind=KIND_WORKFLOW, request=json.dumps(_plan()), interval_seconds=3600,
                             created_by=LOCAL_ACTOR, now=T0))
    control = ControlStore(tmp_path)
    first = run_due(store, now=T1, control_store=control, ledger=ledger, workflow_store=workflows)
    assert first["results"][0]["status"].endswith(":VALIDATED")
    # no manager runs it; the next hour's occurrence must not pile up behind it
    second = run_due(store, now="2026-09-15T11:00:00Z", control_store=control, ledger=ledger, workflow_store=workflows)
    assert second["results"][0]["status"].startswith("workflow:skipped:previous occurrence wf_")
    assert len(workflows.list_workflows()) == 1
    # once it ends, the next occurrence submits again
    (att,) = workflows.claim_ready(now="2026-09-15T11:30:00Z")
    workflows.record_result(att.attempt_id, now="2026-09-15T11:30:00Z", succeeded=True, result_ref="ledger:t", model_calls=1)
    third = run_due(store, now="2026-09-15T12:00:00Z", control_store=control, ledger=ledger, workflow_store=workflows)
    assert third["results"][0]["status"].endswith(":VALIDATED") and len(workflows.list_workflows()) == 2
