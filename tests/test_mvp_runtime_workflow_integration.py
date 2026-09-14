"""A composite workflow end to end through the real worker function (sequence 2, P07; A15).

Research, three drafts that read it, one review that reads the drafts — driven by the manager's
tick, executed by `pipeline_worker.apply_work` in-process with the model call faked (the "mock
provider"). What the acceptance asks to see is the linkage: every step's plan entry, the inputs
it was handed, the run it became (registry row of origin WORKFLOW carrying the attempt id), and
the result reference the next step read — all queryable from the one workflow.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import pipeline_worker, task_registry, workflow as wf
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.task_registry import TaskRegistryStore
from runtime.mvp_runtime.workflow_manager import WorkflowManager
from runtime.mvp_runtime.workflow_store import WorkflowStore

NOW = "2026-09-14T11:00:00Z"


def _plan():
    drafts = [{"id": f"draft{i}", "capability": "content", "request": f"초안 {i}", "reason": "r",
               "depends_on": ["research"], "input_refs": ["research"]} for i in (1, 2, 3)]
    return {
        "schema_version": "workflow_plan.v0.1", "goal": "시장 조사 → 초안 셋 → 검토",
        "steps": [{"id": "research", "capability": "research", "request": "조사", "reason": "r"}, *drafts,
                  {"id": "review", "capability": "analysis", "request": "세 초안 검토", "reason": "r",
                   "depends_on": ["draft1", "draft2", "draft3"], "input_refs": ["draft1", "draft2", "draft3"]}],
        "budget": {"max_model_calls": 8},
    }


@pytest.fixture
def mock_provider(monkeypatch):
    """`run_task` replaced by a fake that answers like the pipeline — ids in the real shape and a
    spend row — one distinct trace per call."""
    calls: list[dict] = []

    def _fake(raw_request, **kwargs):
        calls.append({"raw_request": raw_request, **kwargs})
        n = len(calls)
        return {"status": "COMPLETED", "final_response": f"결과 {n}",
                "records": {"received_task": {"identity": {"task_id": f"task_{n}", "trace_id": f"trace_{n}"}},
                            "budget_usage": {"model_calls": 1, "tokens_used": 50, "agent_invocations": 1,
                                             "revision_cycles": 0}}}

    monkeypatch.setattr(pipeline_worker, "run_task", _fake)
    return calls


def test_research_three_drafts_and_a_review_run_through_the_worker_and_stay_linked(tmp_path, mock_provider):
    store, registry, control = WorkflowStore(tmp_path), TaskRegistryStore(tmp_path), ControlStore(tmp_path)
    frames: list[dict] = []

    def call(path, frame, *, deadline_seconds):            # the manager's socket call, in-process
        frames.append(frame)
        return pipeline_worker.apply_work(frame, control_store=control, registry=registry, now=NOW)

    manager = WorkflowManager(store, control_store=control, worker_socket=tmp_path / "internal" / "pipeline.sock",
                              call=call, door_is_live=lambda p: True, clock=lambda: NOW, concurrency=2,
                              synchronous=True, registry=registry, log=lambda line: None)
    wid = store.submit(principal="hermes", request_id="hermes-1", plan=_plan(), now=NOW).workflow_id

    reports = [manager.tick() for _ in range(6)]
    assert [r["claimed"] for r in reports] == [1, 2, 1, 1, 0, 0]          # research; two drafts; one; review
    assert all(r["error"] is None for r in reports)
    view = store.status_view(wid, now=NOW)
    assert view["status"] == wf.W_COMPLETED

    # the order and the inputs each run was handed
    order = [f["request"] for f in frames]
    assert order[0] == "조사" and order[-1] == "세 초안 검토" and sorted(order[1:4]) == ["초안 1", "초안 2", "초안 3"]
    assert "workflow_inputs" not in frames[0]
    for draft in frames[1:4]:
        assert draft["workflow_inputs"] == {"research": "ledger:trace_1"}
    by_key = {s["key"]: s for s in view["steps"]}
    assert frames[4]["workflow_inputs"] == {k: by_key[k]["result_ref"] for k in ("draft1", "draft2", "draft3")}
    assert by_key["review"]["inputs"] == frames[4]["workflow_inputs"] and by_key["review"]["input_refs"] == ["draft1", "draft2", "draft3"]
    assert by_key["research"]["inputs"] == {} and by_key["research"]["result_ref"] == "ledger:trace_1"

    # the runs: one registry row per attempt, origin WORKFLOW, closed DELIVERED, the attempt id on it
    for step in view["steps"]:
        entry = registry.find_by_attempt(step["current_attempt_id"])
        assert entry is not None and entry.origin == task_registry.WORKFLOW_ORIGIN and entry.status == task_registry.DELIVERED
        assert entry.result_ref == step["result_ref"] and step["status"] == wf.S_SUCCEEDED
        assert store.attempt(step["current_attempt_id"])["registry_entry_id"] == entry.registry_entry_id
    assert len({s["result_ref"] for s in view["steps"]}) == 5

    # the task record of the review names what it was given
    review_call = next(c for c in mock_provider if c["raw_request"] == "세 초안 검토")
    assert review_call["source_ref"].endswith(
        " inputs=" + ",".join(f"{k}={by_key[k]['result_ref']}" for k in ("draft1", "draft2", "draft3")))
    assert review_call["created_by"] == f"assistant_bridge:workflow:{wid}"
    assert mock_provider[0]["source_ref"].endswith(": r")                  # research: no inputs, nothing appended

    # the budget: five reservations, five confirmed calls, nothing unconfirmed
    budget = view["budget"]
    assert budget["reserved_model_calls"] == 5 and budget["confirmed_model_calls"] == 5
    assert budget["unconfirmed_model_calls"] == 0 and budget["remaining_model_calls"] == 3
