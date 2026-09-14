"""Gated workflow steps and the existing approval machinery (sequence 2, P07; acceptance A13).

A plan may ask for Thomas's approval before one step runs (`requires_approval`). The manager
mints the ask through the SAME machinery the switch door uses — a Core-bound task, an
APPROVAL_REQUIRED PermissionDecision, a PENDING approval request in the approval store — and
spends the grant through the SAME single-use ladder (`validate_spendable_approval`,
`spend_lock`, `build_consumed_record`). It never creates APPROVED: only Thomas's verified
decision does. What the grant signs is the step at one plan version with one request hash, so
a rejected, expired, spent, re-pointed or version-stale grant cannot release the step, and the
only thing that ever runs it is the consumption of the bound grant.

These bind real tasks to the repo's Core activation, like every other approval test, so they
skip on a checkout without one.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import approval as approval_mod, dispatch_bridge, permission, workflow as wf
from runtime.mvp_runtime.approval import Verification
from runtime.mvp_runtime.approval_store import ApprovalStore
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import WorkflowBlocked
from runtime.mvp_runtime.workflow_manager import WorkflowManager
from runtime.mvp_runtime.workflow_store import WorkflowStore
from tests._helpers import requires_local_core

NOW = "2026-09-14T10:00:00Z"
SOON = "2026-09-14T10:05:00Z"
LATER = "2026-09-14T10:20:00Z"
NEXT_DAY = "2026-09-15T12:00:00Z"

THOMAS = Verification(approved_by=approval_mod.REQUIRED_APPROVER, method=approval_mod.TELEGRAM_VERIFICATION_METHOD,
                      verification_ref="telegram:private_chat:registered-thomas:test")


def _plan(*, gated_request="초안을 게시용으로 정리", budget=6):
    return {
        "schema_version": "workflow_plan.v0.1", "goal": "시장 조사 후 초안 게시 준비",
        "steps": [
            {"id": "research", "capability": "research", "request": "조사", "reason": "r"},
            {"id": "publish_draft", "capability": "content", "request": gated_request, "reason": "r",
             "depends_on": ["research"], "input_refs": ["research"], "requires_approval": True},
        ],
        "budget": {"max_model_calls": budget},
    }


class _Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def _harness(tmp_path, *, control=None):
    frames: list[dict] = []
    logs: list[str] = []

    def call(path, frame, *, deadline_seconds):
        frames.append(frame)
        n = len(frames)
        return {"ok": True, "kind": frame["kind"], "task_id": f"task_{n}", "trace_id": f"trace_{n}",
                "registry_entry_id": f"treg_{n}", "final_response": "done", "actor": "assistant_bridge",
                "attempt_id": frame["attempt_id"], "workflow_id": frame["workflow_id"],
                "usage": {"model_calls": 1, "tokens_used": 100, "agent_invocations": 1, "revision_cycles": 0}}

    store = WorkflowStore(tmp_path)
    approvals = ApprovalStore(tmp_path)
    clock = _Clock()
    manager = WorkflowManager(
        store, control_store=control or ControlStore(tmp_path), worker_socket=tmp_path / "internal" / "pipeline.sock",
        call=call, door_is_live=lambda p: True, clock=clock, concurrency=2, synchronous=True, log=logs.append,
        approval_store=approvals,
    )
    manager.frames, manager.logs, manager.clock = frames, logs, clock
    return store, approvals, manager


def _to_the_gate(tmp_path, **kw):
    """Submit, run the research step, and reach the tick that mints the gated step's ask."""
    store, approvals, manager = _harness(tmp_path, **kw)
    wid = store.submit(principal="hermes", request_id="hermes-1", plan=_plan(), now=NOW).workflow_id
    assert manager.tick()["claimed"] == 1                                   # research
    report = manager.tick()
    assert report["asks_minted"] == 1 and report["claimed"] == 0
    step = next(s for s in store.status_view(wid, now=NOW)["steps"] if s["key"] == "publish_draft")
    return store, approvals, manager, wid, step


def _decide(approvals, approval_id, *, granted, now=SOON):
    record = approvals.get(approval_id)
    decision = approvals.get_permission_decision(record["permission_decision_id"])
    approvals.append([approval_mod.record_decision(record, decision, granted=granted, verification=THOMAS,
                                                   reason="테스트 결정", now=now)])


def _statuses(approvals, approval_id):
    return [r["status"] for r in approvals.read_all() if r["approval_id"] == approval_id]


# --- the ask -----------------------------------------------------------------------------------

@requires_local_core
def test_a_gated_step_asks_once_through_the_existing_machinery_and_waits(tmp_path):
    store, approvals, manager, wid, step = _to_the_gate(tmp_path)
    assert step["status"] == wf.S_WAITING_APPROVAL and step["approval_id"].startswith("approval_")
    assert step["approval_plan_version"] == 1
    assert store.status_view(wid, now=NOW)["status"] == wf.W_WAITING_APPROVAL
    ask = approvals.get(step["approval_id"])
    snapshot = ask["approved_action_snapshot"]
    assert ask["status"] == approval_mod.STATUS_PENDING
    assert snapshot["permission_scope"] == permission.TRADING_SWITCH_PERMISSION_SCOPE       # no new scope
    assert snapshot["target_ref"] == f"{permission.WORKFLOW_STEP_TARGET_PREFIX}{wid}:publish_draft"
    assert snapshot["normalized_parameters"] == wf.approval_content(
        workflow_id=wid, step_key="publish_draft", plan_version=1, capability="content", request="초안을 게시용으로 정리")
    decision = approvals.get_permission_decision(ask["permission_decision_id"])
    assert decision["decision"]["permission_decision"] == "APPROVAL_REQUIRED"
    assert "publish_draft" in json.dumps(decision, ensure_ascii=False)
    assert [a["approval_id"] for a in approvals.pending()] == [step["approval_id"]]
    # nothing more is minted and nothing is claimed while the ask stands
    for _ in range(3):
        report = manager.tick()
        assert report["asks_minted"] == 0 and report["claimed"] == 0 and report["approvals_spent"] == 0
    assert len(manager.frames) == 1
    assert _statuses(approvals, step["approval_id"]) == [approval_mod.STATUS_PENDING]


def test_a_plan_cannot_carry_an_approval_only_ask_for_one(tmp_path):
    plan = _plan()
    plan["steps"][1]["approval_id"] = "approval_forged"
    with pytest.raises(WorkflowBlocked) as exc:
        dispatch_bridge.apply_dispatch({"command": "workflow.submit", "request_id": "hermes-1", "plan": plan, "proto": 2},
                                       control_store=ControlStore(tmp_path), workflow_store=WorkflowStore(tmp_path),
                                       manager_enabled=True, now=NOW)
    assert exc.value.reason_code == "PLAN_INVALID"
    assert WorkflowStore(tmp_path).list_workflows() == []


# --- the grant, spent once ---------------------------------------------------------------------

@requires_local_core
def test_only_the_consumption_of_the_bound_grant_runs_the_step(tmp_path):
    store, approvals, manager, wid, step = _to_the_gate(tmp_path)
    _decide(approvals, step["approval_id"], granted=True)
    manager.clock.now = SOON
    report = manager.tick()
    assert report["approvals_spent"] == 1 and report["claimed"] == 1
    assert _statuses(approvals, step["approval_id"]) == [approval_mod.STATUS_PENDING, approval_mod.STATUS_APPROVED,
                                                         approval_mod.STATUS_CONSUMED]
    consumed = approvals.get(step["approval_id"])
    assert consumed["consumption"]["consumption_ref"] == f"workflow:{wid}:publish_draft:v1"
    view = store.status_view(wid, now=SOON)
    assert view["status"] == wf.W_COMPLETED
    gated = next(s for s in view["steps"] if s["key"] == "publish_draft")
    assert gated["status"] == wf.S_SUCCEEDED and gated["result_ref"] == "ledger:trace_2"
    assert manager.frames[1]["workflow_inputs"] == {"research": "ledger:trace_1"}
    events, _ = store.events_after(0, limit=500)
    codes = [e["reason_code"] for e in events if e["step_id"] == step["step_id"] and e["reason_code"]]
    assert codes[:2] == ["APPROVAL_REQUESTED", "APPROVAL_CONSUMED"]
    # the grant is gone: a second spend anywhere is ALREADY_CONSUMED
    with pytest.raises(approval_mod.ApprovalBlocked) as exc:
        approval_mod.validate_spendable_approval(
            approvals, step["approval_id"], now=SOON, control_state=ControlStore(tmp_path).load(),
            expected_scope=permission.TRADING_SWITCH_PERMISSION_SCOPE, kill_action="x", refusal_phrase="y", scope_refusal="z")
    assert exc.value.reason_code == "ALREADY_CONSUMED"


@requires_local_core
def test_a_halted_runtime_spends_no_grant_and_the_grant_waits(tmp_path, monkeypatch):
    control = ControlStore(tmp_path)
    store, approvals, manager, wid, step = _to_the_gate(tmp_path, control=control)
    _decide(approvals, step["approval_id"], granted=True)
    monkeypatch.setattr(control, "load", lambda: _Halted())
    report = manager.tick()
    assert report["approvals_deferred"] == 1 and report["approvals_spent"] == 0 and report["approvals_refused"] == 0
    assert report["skipped"].startswith("runtime is KILLED")
    assert approvals.get(step["approval_id"])["status"] == approval_mod.STATUS_APPROVED
    assert store.status_view(wid, now=NOW)["steps"][1]["status"] == wf.S_WAITING_APPROVAL
    monkeypatch.undo()
    assert manager.tick()["approvals_spent"] == 1                            # resumed: spent, run


class _Halted:
    mode = "KILLED"
    execution_allowed = False

    @staticmethod
    def refusal_reason_code():
        return "KILLED"


# --- everything a grant cannot do ----------------------------------------------------------------

@requires_local_core
def test_a_rejected_ask_blocks_the_step_and_a_retry_asks_again_with_a_new_id(tmp_path):
    store, approvals, manager, wid, step = _to_the_gate(tmp_path)
    _decide(approvals, step["approval_id"], granted=False)
    report = manager.tick()
    assert report["approvals_refused"] == 1 and report["claimed"] == 0
    view = store.status_view(wid, now=NOW)
    gated = next(s for s in view["steps"] if s["key"] == "publish_draft")
    assert gated["status"] == wf.S_BLOCKED and gated["last_reason_code"] == wf.APPROVAL_REJECTED
    assert view["status"] == wf.W_WAITING_REPLAN
    view = store.retry_step(wid, "publish_draft", expected_version=view["row_version"], reason="다시 요청", now=SOON)
    assert view["status"] == wf.W_WAITING_APPROVAL
    assert manager.tick()["asks_minted"] == 1
    fresh = next(s for s in store.status_view(wid, now=SOON)["steps"] if s["key"] == "publish_draft")
    # the same action fingerprints to the same id (the switch door's convention): the re-ask is a
    # new PENDING record under it, and the trail keeps the rejection
    assert fresh["approval_id"] == step["approval_id"] and fresh["status"] == wf.S_WAITING_APPROVAL
    assert _statuses(approvals, step["approval_id"]) == [approval_mod.STATUS_PENDING, approval_mod.STATUS_REJECTED,
                                                         approval_mod.STATUS_PENDING]
    assert approvals.get(step["approval_id"])["status"] == approval_mod.STATUS_PENDING
    assert len(manager.frames) == 1


@requires_local_core
def test_an_expired_ask_or_grant_never_runs_the_step(tmp_path):
    # an ask that expired unanswered
    store, approvals, manager, wid, step = _to_the_gate(tmp_path)
    manager.clock.now = NEXT_DAY
    report = manager.tick()
    assert report["approvals_refused"] == 1 and report["claimed"] == 0
    gated = next(s for s in store.status_view(wid, now=NEXT_DAY)["steps"] if s["key"] == "publish_draft")
    assert gated["status"] == wf.S_BLOCKED and gated["last_reason_code"] == wf.APPROVAL_EXPIRED
    # a grant that was approved in time but expired before the manager could spend it
    store2, approvals2, manager2, wid2, step2 = _to_the_gate(tmp_path / "two")
    _decide(approvals2, step2["approval_id"], granted=True)
    manager2.clock.now = NEXT_DAY
    report = manager2.tick()
    assert report["approvals_refused"] == 1 and report["claimed"] == 0
    assert approvals2.get(step2["approval_id"])["status"] == approval_mod.STATUS_APPROVED     # unspent, not consumed
    gated = next(s for s in store2.status_view(wid2, now=NEXT_DAY)["steps"] if s["key"] == "publish_draft")
    assert gated["status"] == wf.S_BLOCKED and gated["last_reason_code"] == wf.APPROVAL_EXPIRED
    assert len(manager.frames) == 1 and len(manager2.frames) == 1


@requires_local_core
def test_a_grant_spent_elsewhere_is_reuse_and_runs_nothing(tmp_path):
    store, approvals, manager, wid, step = _to_the_gate(tmp_path)
    _decide(approvals, step["approval_id"], granted=True)
    record = approvals.get(step["approval_id"])
    decision = approvals.get_permission_decision(record["permission_decision_id"])
    approvals.append([approval_mod.build_consumed_record(record, decision, consumed_at=SOON, consumption_ref="elsewhere:x")])
    manager.clock.now = SOON
    report = manager.tick()
    assert report["approvals_refused"] == 1 and report["claimed"] == 0
    gated = next(s for s in store.status_view(wid, now=SOON)["steps"] if s["key"] == "publish_draft")
    assert gated["status"] == wf.S_BLOCKED and gated["last_reason_code"] == wf.APPROVAL_REUSED
    assert len(manager.frames) == 1


@requires_local_core
def test_a_plan_change_re_asks_and_the_old_grant_cannot_release_the_changed_step(tmp_path):
    store, approvals, manager, wid, step = _to_the_gate(tmp_path)
    _decide(approvals, step["approval_id"], granted=True)                   # Thomas approved v1's request
    view = store.status_view(wid, now=NOW)
    changed = _plan(gated_request="초안을 게시용으로 정리하고 제목을 세 개 제안")
    view = store.propose_update(wid, expected_version=view["row_version"], plan=changed, reason="요청 보강", now=SOON)
    gated = next(s for s in view["steps"] if s["key"] == "publish_draft")
    assert view["plan_version"] == 2 and gated["status"] == wf.S_WAITING_APPROVAL and gated["approval_id"] is None
    manager.clock.now = SOON
    report = manager.tick()
    assert report["asks_minted"] == 1 and report["approvals_spent"] == 0 and report["claimed"] == 0
    fresh = next(s for s in store.status_view(wid, now=SOON)["steps"] if s["key"] == "publish_draft")
    new_ask = approvals.get(fresh["approval_id"])
    assert fresh["approval_id"] != step["approval_id"] and fresh["approval_plan_version"] == 2
    assert new_ask["approved_action_snapshot"]["normalized_parameters"]["plan_version"] == 2
    assert (new_ask["approved_action_snapshot"]["normalized_parameters"]["request_sha256"]
            != approvals.get(step["approval_id"])["approved_action_snapshot"]["normalized_parameters"]["request_sha256"])
    assert approvals.get(step["approval_id"])["status"] == approval_mod.STATUS_APPROVED     # v1's grant stays unspent
    # someone re-points v1's grant at the v2 step: the spend compares the snapshot and refuses
    store.bind_approval(fresh["step_id"], approval_id=step["approval_id"], plan_version=2, now=SOON)
    report = manager.tick()
    assert report["approvals_refused"] == 1 and report["claimed"] == 0
    stale = next(s for s in store.status_view(wid, now=SOON)["steps"] if s["key"] == "publish_draft")
    assert stale["status"] == wf.S_BLOCKED and stale["last_reason_code"] == wf.APPROVAL_STALE
    assert approvals.get(step["approval_id"])["status"] == approval_mod.STATUS_APPROVED     # still not spent
    assert len(manager.frames) == 1


@requires_local_core
def test_a_stale_binding_to_an_unknown_or_foreign_grant_blocks_the_step(tmp_path):
    store, approvals, manager, wid, step = _to_the_gate(tmp_path)
    store.bind_approval(step["step_id"], approval_id="approval_" + "0" * 12, plan_version=1, now=NOW)
    report = manager.tick()
    assert report["approvals_refused"] == 1
    gated = next(s for s in store.status_view(wid, now=NOW)["steps"] if s["key"] == "publish_draft")
    assert gated["status"] == wf.S_BLOCKED and gated["last_reason_code"] == wf.APPROVAL_STALE
    assert len(manager.frames) == 1
    assert not any("Traceback" in line for line in manager.logs)


@requires_local_core
def test_the_ask_is_recorded_only_as_metadata_and_never_as_a_grant(tmp_path):
    """The approval store and the permission ledger carry the ask, the decision and the spend —
    the manager wrote PENDING and CONSUMED, Thomas wrote APPROVED, and nothing else."""
    store, approvals, manager, wid, step = _to_the_gate(tmp_path)
    _decide(approvals, step["approval_id"], granted=True)
    manager.clock.now = SOON
    manager.tick()
    rows = [json.loads(line) for line in approvals.path.read_text(encoding="utf-8").splitlines()]
    mine = [r for r in rows if r["approval_id"] == step["approval_id"]]
    assert [r["status"] for r in mine] == ["PENDING", "APPROVED", "CONSUMED"]
    assert mine[1]["approver"]["approved_by"] == approval_mod.REQUIRED_APPROVER
    assert mine[1]["approver"]["identity_verification_method"] == approval_mod.TELEGRAM_VERIFICATION_METHOD


# --- independent review of P07 (2026-09-14) ----------------------------------------------------------

@requires_local_core
def test_a_budget_only_version_asks_again_and_only_the_new_grant_runs_the_step(tmp_path):
    store, approvals, manager, wid, step = _to_the_gate(tmp_path)
    old_id = step["approval_id"]
    view = store.status_view(wid, now=NOW)
    store.propose_update(wid, expected_version=view["row_version"], plan=_plan(budget=7), reason="예산만", now=SOON)
    assert next(s for s in store.status_view(wid, now=SOON)["steps"] if s["key"] == "publish_draft")["approval_id"] == old_id
    manager.clock.now = SOON
    report = manager.tick()
    assert report["asks_minted"] == 1 and report["claimed"] == 0                 # v2 is asked for, not the v1 ask spent
    fresh = next(s for s in store.status_view(wid, now=SOON)["steps"] if s["key"] == "publish_draft")
    assert fresh["approval_id"] != old_id and fresh["approval_plan_version"] == 2
    _decide(approvals, fresh["approval_id"], granted=True)
    report = manager.tick()
    assert report["approvals_spent"] == 1 and report["claimed"] == 1
    assert approvals.get(old_id)["status"] == approval_mod.STATUS_PENDING         # the v1 ask was never spent


def test_a_mint_that_fails_with_an_untyped_error_is_backed_off_and_other_workflows_still_run(tmp_path, monkeypatch):
    from runtime.mvp_runtime import workflow_manager as wm

    store, approvals, manager = _harness(tmp_path)
    gated = {"schema_version": "workflow_plan.v0.1", "goal": "게이트",
             "steps": [{"id": "g", "capability": "content", "request": "y", "reason": "r", "requires_approval": True}],
             "budget": {"max_model_calls": 2}}
    plain = {"schema_version": "workflow_plan.v0.1", "goal": "일반",
             "steps": [{"id": "a", "capability": "analysis", "request": "x", "reason": "r"}], "budget": {"max_model_calls": 2}}
    store.submit(principal="hermes", request_id="hermes-g", plan=gated, now=NOW)
    plain_id = store.submit(principal="hermes", request_id="hermes-p", plan=plain, now=NOW).workflow_id
    calls = []

    def broken(*a, **k):
        calls.append(1)
        raise KeyError("identity")

    monkeypatch.setattr(wm, "build_task", broken)
    report = manager.tick()
    assert report["error"] is None and report["claimed"] == 1 and report["asks_minted"] == 0
    assert store.status_view(plain_id, now=NOW)["status"] == wf.W_COMPLETED
    assert any("KeyError" in line for line in manager.logs)
    for _ in range(3):
        manager.tick()
    assert len(calls) == 1                                                       # backed off, not retried every tick


@requires_local_core
def test_a_plan_update_between_the_read_and_the_spend_spends_nothing(tmp_path, monkeypatch):
    store, approvals, manager, wid, step = _to_the_gate(tmp_path)
    _decide(approvals, step["approval_id"], granted=True)
    real = approval_mod.validate_spendable_approval

    def racing(*a, **k):
        out = real(*a, **k)
        view = store.status_view(wid, now=SOON)                                   # the assistant updates the plan mid-spend
        store.propose_update(wid, expected_version=view["row_version"], plan=_plan(budget=9), reason="동시 수정", now=SOON)
        return out

    monkeypatch.setattr(approval_mod, "validate_spendable_approval", racing)
    manager.clock.now = SOON
    report = manager.tick()
    assert report["approvals_spent"] == 0 and report["claimed"] == 0
    assert approvals.get(step["approval_id"])["status"] == approval_mod.STATUS_APPROVED      # the grant was not spent
    monkeypatch.undo()
    assert manager.tick()["asks_minted"] == 1                                    # the new version is asked for


@requires_local_core
def test_the_ask_tells_thomas_that_approving_runs_the_step_and_never_that_it_runs_nothing(tmp_path):
    store, approvals, manager, wid, step = _to_the_gate(tmp_path)
    ask = approvals.get(step["approval_id"])
    text = approval_mod.request_message(ask, approvals.get_permission_decision(ask["permission_decision_id"]))
    assert "다음 패스에서 이 승인을 1회 소비하고 이 단계 하나를 실행합니다" in text
    assert "REVIEW_ONLY" not in text and "validated memory" not in text and "approval_consumption" not in text
    assert f"/approve {step['approval_id']}" in text and "APPROVAL_STALE" in text
