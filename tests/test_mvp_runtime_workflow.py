"""The workflow model: what a plan may say, and which edges a lifecycle has (sequence 2, P03).

Pure functions, no store. Acceptance A01 (a bad plan is refused before anything exists) and A02
(a dependent waits for every dependency) at the model level; the store tests cover the same
under a transaction.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import dispatch_bridge, workflow as wf
from runtime.mvp_runtime.errors import WorkflowBlocked

NOW = "2026-09-14T07:00:00Z"


def _plan(**over):
    plan = {
        "schema_version": "workflow_plan.v0.1",
        "goal": "시장 조사 후 초안",
        "steps": [{"id": "research", "capability": "research", "request": "키워드 조사", "reason": "브리핑"}],
        "budget": {"max_model_calls": 4},
    }
    plan.update(over)
    return plan


def _multi():
    return _plan(steps=[
        {"id": "research", "capability": "research", "request": "조사", "reason": "r"},
        {"id": "draft1", "capability": "content", "request": "초안 1", "reason": "r",
         "depends_on": ["research"], "input_refs": ["research"]},
        {"id": "draft2", "capability": "content", "request": "초안 2", "reason": "r",
         "depends_on": ["research"], "input_refs": ["research"], "options": {"independent_validation": True}},
        {"id": "review", "capability": "analysis", "request": "검토", "reason": "r",
         "depends_on": ["draft1", "draft2"], "input_refs": ["draft1", "draft2"]},
    ], budget={"max_model_calls": 10})


# --- the closed capability set --------------------------------------------------------------

def test_the_capabilities_are_exactly_the_dispatch_doors_four_kinds_and_all_effect_none():
    assert set(wf.CAPABILITIES) == set(dispatch_bridge._ALLOWED_KINDS)
    assert {c.effect_class for c in wf.CAPABILITIES.values()} == {wf.EFFECT_NONE}


# --- validation (A01) ------------------------------------------------------------------------

def test_a_valid_plan_validates_with_a_topological_order_and_a_stable_hash():
    v = wf.validate_plan(_multi())
    assert v.order[0] == "research" and v.order[-1] == "review"
    assert set(v.order[1:3]) == {"draft1", "draft2"}
    assert v.plan_hash == wf.plan_hash(_multi()) and v.plan_hash.startswith("sha256:")
    assert v.step("draft2").calls_per_attempt == 2 and v.step("draft1").calls_per_attempt == 1
    assert v.step("review").effect_class == wf.EFFECT_NONE


def test_a_plan_cannot_name_an_effect_class_an_actor_or_an_unknown_key():
    with pytest.raises(WorkflowBlocked) as exc:
        wf.validate_plan(_plan(steps=[{"id": "s", "capability": "analysis", "request": "x", "reason": "r",
                                       "effect_class": "external"}]))
    assert exc.value.reason_code == "PLAN_INVALID"
    with pytest.raises(WorkflowBlocked) as exc:
        wf.validate_plan(_plan(actor="thomas"))
    assert exc.value.reason_code == "PLAN_INVALID"


@pytest.mark.parametrize("steps, needle", [
    ([{"id": "a", "capability": "analysis", "request": "x", "reason": "r", "depends_on": ["b"]},
      {"id": "b", "capability": "analysis", "request": "x", "reason": "r", "depends_on": ["a"]}], "cycle"),
    ([{"id": "a", "capability": "analysis", "request": "x", "reason": "r", "depends_on": ["ghost"]}], "unknown steps"),
    ([{"id": "a", "capability": "analysis", "request": "x", "reason": "r"},
      {"id": "a", "capability": "analysis", "request": "y", "reason": "r"}], "duplicate"),
    ([{"id": "a", "capability": "analysis", "request": "x", "reason": "r", "depends_on": ["a"]}], "itself"),
    ([{"id": "a", "capability": "analysis", "request": "x", "reason": "r"},
      {"id": "b", "capability": "analysis", "request": "x", "reason": "r", "input_refs": ["a"]}], "not among its dependencies"),
    ([{"id": "a", "capability": "analysis", "request": "   ", "reason": "r"}], "blank"),
])
def test_refusals_name_their_cause(steps, needle):
    with pytest.raises(WorkflowBlocked) as exc:
        wf.validate_plan(_plan(steps=steps, budget={"max_model_calls": 10}))
    assert exc.value.reason_code == "PLAN_INVALID" and needle in exc.value.reason


def test_a_capability_outside_the_four_is_refused_by_the_schema_before_anything_else():
    with pytest.raises(WorkflowBlocked) as exc:
        wf.validate_plan(_plan(steps=[{"id": "d", "capability": "development", "request": "x", "reason": "r"}]))
    assert exc.value.reason_code == "PLAN_INVALID"


def test_more_steps_than_the_ceiling_are_refused():
    steps = [{"id": f"s{i}", "capability": "analysis", "request": "x", "reason": "r"} for i in range(11)]
    with pytest.raises(WorkflowBlocked) as exc:
        wf.validate_plan(_plan(steps=steps, budget={"max_model_calls": 50}))
    assert exc.value.reason_code == "PLAN_INVALID"


def test_a_budget_smaller_than_one_attempt_of_every_step_is_refused():
    plan = _multi()
    plan["budget"] = {"max_model_calls": 4}       # 1 + 1 + 2 + 1 = 5 needed
    with pytest.raises(WorkflowBlocked) as exc:
        wf.validate_plan(plan)
    assert exc.value.reason_code == "BUDGET_EXHAUSTED"


def test_a_non_mapping_is_refused_not_crashed():
    with pytest.raises(WorkflowBlocked) as exc:
        wf.validate_plan(["not", "a", "plan"])
    assert exc.value.reason_code == "PLAN_INVALID"


# --- lifecycles ------------------------------------------------------------------------------

def test_transitions_are_forward_only_and_named():
    wf.assert_transition("workflow", wf.W_VALIDATED, wf.W_RUNNING, "wf_x")
    wf.assert_transition("step", wf.S_READY, wf.S_RUNNING, "wfs_x")
    wf.assert_transition("attempt", wf.A_RUNNING, wf.A_EXPIRED, "wfa_x")
    for entity, current, target in (("workflow", wf.W_COMPLETED, wf.W_RUNNING),
                                    ("step", wf.S_SUCCEEDED, wf.S_RUNNING),
                                    ("attempt", wf.A_SUCCEEDED, wf.A_RUNNING),
                                    ("step", wf.S_PENDING, wf.S_RUNNING)):
        with pytest.raises(WorkflowBlocked) as exc:
            wf.assert_transition(entity, current, target, "id")
        assert exc.value.reason_code == "TRANSITION_INVALID"
    with pytest.raises(WorkflowBlocked):
        wf.assert_transition("step", "NOPE", wf.S_READY, "id")


def test_every_terminal_status_has_no_outgoing_edge():
    assert all(not wf.WORKFLOW_TRANSITIONS[s] for s in wf.WORKFLOW_TERMINAL)
    assert all(not wf.STEP_TRANSITIONS[s] for s in wf.STEP_TERMINAL)
    assert all(not wf.ATTEMPT_TRANSITIONS[s] for s in wf.ATTEMPT_TERMINAL)


def test_ready_keys_waits_for_every_dependency():
    deps = {"research": (), "draft1": ("research",), "draft2": ("research",), "review": ("draft1", "draft2")}
    status = {"research": wf.S_SUCCEEDED, "draft1": wf.S_SUCCEEDED, "draft2": wf.S_FAILED, "review": wf.S_PENDING}
    assert wf.ready_keys(status, deps) == ()
    status["draft2"] = wf.S_SUCCEEDED
    assert wf.ready_keys(status, deps) == ("review",)


def _row(status, attempts=1, reason=None):
    return {"status": status, "attempts_opened": attempts, "last_reason_code": reason}


def test_workflow_status_follows_its_steps():
    ok, run = _row(wf.S_SUCCEEDED), _row(wf.S_RUNNING)
    assert wf.workflow_status_for([ok, run], cancelling=False) == wf.W_RUNNING
    assert wf.workflow_status_for([ok, run], cancelling=True) == wf.W_CANCELLING
    assert wf.workflow_status_for([ok, ok], cancelling=False) == wf.W_COMPLETED
    assert wf.workflow_status_for([ok, _row(wf.S_CANCELLED)], cancelling=True) == wf.W_CANCELLED
    assert wf.workflow_status_for([ok, _row(wf.S_WAITING_APPROVAL)], cancelling=False) == wf.W_WAITING_APPROVAL
    # a PENDING dependent behind the gate does not make the workflow RUNNING; a READY sibling does
    assert wf.workflow_status_for([_row(wf.S_WAITING_APPROVAL), _row(wf.S_PENDING)], cancelling=False) == wf.W_WAITING_APPROVAL
    assert wf.workflow_status_for([_row(wf.S_WAITING_APPROVAL), _row(wf.S_READY)], cancelling=False) == wf.W_RUNNING
    # a settled step a decision could still move waits for that decision
    failed_below_cap = _row(wf.S_FAILED, attempts=1)
    dep_blocked = _row(wf.S_BLOCKED, reason=wf.DEPENDENCY_FAILED)
    assert wf.workflow_status_for([ok, failed_below_cap, dep_blocked], cancelling=False) == wf.W_WAITING_REPLAN
    assert wf.workflow_status_for([_row(wf.S_BLOCKED, reason=wf.BUDGET_EXHAUSTED)], cancelling=False) == wf.W_WAITING_REPLAN
    assert wf.workflow_status_for([ok, _row(wf.S_NEEDS_RECONCILIATION)], cancelling=False) == wf.W_WAITING_REPLAN
    # at the hard cap the failure is final; a dependency-blocked step alone is BLOCKED
    assert wf.workflow_status_for([ok, _row(wf.S_FAILED, attempts=wf.MAX_ATTEMPTS_PER_STEP), dep_blocked], cancelling=False) == wf.W_FAILED
    assert wf.workflow_status_for([dep_blocked], cancelling=False) == wf.W_BLOCKED
    # a cancel decides the settled ones too
    assert wf.workflow_status_for([failed_below_cap, _row(wf.S_CANCELLED)], cancelling=True) == wf.W_CANCELLED


def test_settled_steps_are_retriable_by_decision_and_terminal_ones_are_not():
    assert wf.STEP_TERMINAL == {wf.S_SUCCEEDED, wf.S_CANCELLED} and wf.STEP_SETTLED == {wf.S_FAILED, wf.S_BLOCKED}
    wf.assert_transition("step", wf.S_FAILED, wf.S_READY, "x")
    wf.assert_transition("step", wf.S_BLOCKED, wf.S_PENDING, "x")
    for s in wf.STEP_TERMINAL:
        assert not wf.STEP_TRANSITIONS[s]


# --- identities and events -------------------------------------------------------------------

def test_ids_are_deterministic_and_prefixed():
    a = wf.workflow_id_for("hermes", "hermes-1", "sha256:x", NOW)
    assert a == wf.workflow_id_for("hermes", "hermes-1", "sha256:x", NOW) and a.startswith("wf_")
    assert a != wf.workflow_id_for("hermes", "hermes-2", "sha256:x", NOW)
    assert wf.step_id_for(a, "research").startswith("wfs_")
    assert wf.attempt_id_for(wf.step_id_for(a, "research"), 1).startswith("wfa_")


def test_an_event_record_is_closed_and_validated():
    wid = wf.workflow_id_for("hermes", "r", "h", NOW)
    record = wf.event_record(workflow_id=wid, entity="workflow", to_status=wf.W_RECEIVED, created_at=NOW)
    assert record["schema_version"] == wf.EVENT_SCHEMA_VERSION and record["step_id"] is None
    with pytest.raises(WorkflowBlocked) as exc:
        wf.event_record(workflow_id="not-an-id", entity="workflow", to_status=wf.W_RECEIVED, created_at=NOW)
    assert exc.value.reason_code == "EVENT_INVALID"
    with pytest.raises(WorkflowBlocked):
        wf.event_record(workflow_id=wid, entity="galaxy", to_status="x", created_at=NOW)
