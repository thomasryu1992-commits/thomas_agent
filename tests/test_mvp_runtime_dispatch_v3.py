"""Door API v3 on the dispatch door: workflow commands beside the v2 dispatch (sequence 2, P05).

A frame with `command` is a workflow command and nothing else; a v2 frame never sees the v3
keys. Served only when the door was opened with the workflow store — otherwise refused by
name, never a v2 run in disguise (acceptance A18). The principal is the door's peer, not a
value in the frame.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import dispatch_bridge, socket_door, workflow as wf
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import ControlBlocked, WorkflowBlocked
from runtime.mvp_runtime.workflow_store import WorkflowStore

NOW = "2026-09-14T09:00:00Z"


def _plan(**over):
    plan = {"schema_version": "workflow_plan.v0.1", "goal": "목표",
            "steps": [{"id": "research", "capability": "research", "request": "조사", "reason": "r"}],
            "budget": {"max_model_calls": 3}}
    plan.update(over)
    return plan


@pytest.fixture
def captured_execute():
    calls: list[dict] = []

    def _execute(text, kind, reason, naver_keywords, client_id=None):
        calls.append({"request": text, "kind": kind})
        return {"ok": True, "kind": kind, "task_id": "task_1", "final_response": "ok", "actor": "assistant_bridge"}

    _execute.calls = calls
    return _execute


def _apply(request, tmp_path, *, store=None, execute=None, manager_enabled=True, control=None):
    return dispatch_bridge.apply_dispatch(
        request, control_store=control or ControlStore(tmp_path), execute=execute,
        workflow_store=store, manager_enabled=manager_enabled, now=NOW,
    )


def _submit(store, tmp_path, request_id="hermes-1", plan=None, **kw):
    return _apply({"command": "workflow.submit", "request_id": request_id, "plan": plan or _plan(), "proto": 2},
                  tmp_path, store=store, **kw)


# --- the surface -------------------------------------------------------------------------------

def test_capabilities_names_the_commands_the_kinds_the_schema_and_whether_the_manager_runs(tmp_path):
    out = _apply({"command": "capabilities", "proto": 2}, tmp_path, store=WorkflowStore(tmp_path))
    assert out["ok"] and out["data"]["commands"] == sorted(dispatch_bridge.V3_COMMANDS)
    assert out["data"]["kinds"] == sorted(dispatch_bridge._ALLOWED_KINDS) and out["data"]["proto"] == 2
    assert out["data"]["plan_schema"] == wf.PLAN_SCHEMA_VERSION and out["data"]["workflow_manager"] is True
    off = _apply({"command": "capabilities"}, tmp_path, store=None, manager_enabled=False)
    assert off["ok"] and off["data"]["workflow_manager"] is False and "off" in off["reply"]


def test_a_v3_command_on_a_v2_only_door_is_refused_by_name_and_never_runs_as_v2(tmp_path, captured_execute):
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "workflow.submit", "request_id": "hermes-1", "plan": _plan()}, tmp_path,
               store=None, execute=captured_execute, manager_enabled=False)
    assert exc.value.reason_code == "WORKFLOW_UNAVAILABLE" and not captured_execute.calls


def test_v3_and_v2_keys_do_not_mix(tmp_path, captured_execute):
    store = WorkflowStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "workflow.submit", "request_id": "r", "plan": _plan(), "request": "analyze"}, tmp_path,
               store=store, execute=captured_execute)
    assert exc.value.reason_code == "ARGUMENT_NOT_ACCEPTED" and not captured_execute.calls
    with pytest.raises(ControlBlocked) as exc:
        _apply({"request": "analyze this", "kind": "analysis", "reason": "r", "plan": _plan()}, tmp_path,
               store=store, execute=captured_execute)
    assert exc.value.reason_code == "ARGUMENT_NOT_ACCEPTED"
    assert "plan" not in dispatch_bridge._ALLOWED_KEYS and "command" not in dispatch_bridge._ALLOWED_KEYS


def test_an_unknown_command_is_refused(tmp_path):
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "workflow.delete", "workflow_id": "wf_" + "0" * 20}, tmp_path, store=WorkflowStore(tmp_path))
    assert exc.value.reason_code == "VERB_NOT_PERMITTED"


def test_a_v2_dispatch_is_unchanged_beside_v3(tmp_path, captured_execute):
    out = _apply({"request": "analyze this idea", "kind": "analysis", "reason": "asked"}, tmp_path,
                 store=WorkflowStore(tmp_path), execute=captured_execute)
    assert out["ok"] and captured_execute.calls == [{"request": "analyze this idea", "kind": "analysis"}]


# --- submit (A03–A05, A18) ---------------------------------------------------------------------

def test_submit_accepts_replays_and_conflicts_by_request_id(tmp_path):
    store = WorkflowStore(tmp_path)
    out = _submit(store, tmp_path)
    assert out["ok"] and out["replayed"] is False and out["reply"].startswith("ACCEPTED")
    wid = out["data"]["workflow_id"]
    assert out["data"]["status"] == wf.W_VALIDATED and out["request_id"] == "hermes-1" and out["proto"] == 2
    assert store.status_view(wid, now=NOW)["principal"] == socket_door.ASSISTANT_ACTOR
    again = _submit(store, tmp_path)
    assert again["replayed"] is True and again["data"]["workflow_id"] == wid and again["reply"].startswith("REPLAYED")
    with pytest.raises(WorkflowBlocked) as exc:
        _submit(store, tmp_path, plan=_plan(goal="다른 목표"))
    assert exc.value.reason_code == "REQUEST_ID_CONFLICT"


def test_submit_needs_a_request_id_and_a_plan_object(tmp_path):
    store = WorkflowStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "workflow.submit", "plan": _plan()}, tmp_path, store=store)
    assert exc.value.reason_code == "REQUEST_ID_REQUIRED"
    with pytest.raises(WorkflowBlocked) as exc:
        _apply({"command": "workflow.submit", "request_id": "r", "plan": ["not", "a", "plan"]}, tmp_path, store=store)
    assert exc.value.reason_code == "PLAN_INVALID"
    assert store.list_workflows() == []


def test_a_refused_plan_leaves_no_row_and_burns_no_id(tmp_path):
    store = WorkflowStore(tmp_path)
    bad = _plan(steps=[{"id": "a", "capability": "development", "request": "x", "reason": "r"}])
    with pytest.raises(WorkflowBlocked) as exc:
        _submit(store, tmp_path, plan=bad)
    assert exc.value.reason_code == "PLAN_INVALID"
    assert store.list_workflows() == [] and store.find_request(socket_door.ASSISTANT_ACTOR, "hermes-1") is None
    assert _submit(store, tmp_path)["replayed"] is False


def test_a_halted_runtime_accepts_no_workflow_but_still_answers_reads_and_cancels(tmp_path, monkeypatch):
    store = WorkflowStore(tmp_path)
    wid = _submit(store, tmp_path)["data"]["workflow_id"]
    control = ControlStore(tmp_path)

    class _Killed:
        mode = "KILLED"
        execution_allowed = False

        def refusal_reason_code(self):
            return "KILLED"

    monkeypatch.setattr(control, "load", lambda: _Killed())
    with pytest.raises(ControlBlocked) as exc:
        _submit(store, tmp_path, request_id="hermes-2", control=control)
    assert exc.value.reason_code == "KILLED" and len(store.list_workflows()) == 1
    assert _apply({"command": "workflow.status", "workflow_id": wid}, tmp_path, store=store, control=control)["ok"]
    view = _apply({"command": "workflow.status", "workflow_id": wid}, tmp_path, store=store, control=control)["data"]
    out = _apply({"command": "workflow.cancel", "workflow_id": wid, "expected_version": view["row_version"],
                  "reason": "halt"}, tmp_path, store=store, control=control)
    assert out["ok"] and out["data"]["status"] == wf.W_CANCELLED


# --- reads and cancel ---------------------------------------------------------------------------

def test_status_list_and_events_read_the_store_with_text_and_data(tmp_path):
    store = WorkflowStore(tmp_path)
    wid = _submit(store, tmp_path)["data"]["workflow_id"]
    status = _apply({"command": "workflow.status", "workflow_id": wid}, tmp_path, store=store)
    assert status["data"]["workflow_id"] == wid and status["data"]["as_of"] == NOW
    assert "research (research) 준비" in status["reply"] and "예산: 모델 호출 0/3" in status["reply"]
    listing = _apply({"command": "workflow.list", "limit": 5}, tmp_path, store=store)
    assert listing["data"]["count"] == 1 and listing["data"]["workflows"][0]["workflow_id"] == wid and wid in listing["reply"]
    events = _apply({"command": "workflow.events", "after_cursor": 0, "limit": 2}, tmp_path, store=store)
    assert events["data"]["count"] == 2 and events["data"]["next_cursor"] == 2 and "next_cursor=2" in events["reply"]
    more = _apply({"command": "workflow.events", "after_cursor": 2}, tmp_path, store=store)
    assert more["data"]["events"][0]["cursor"] == 3


def test_reads_refuse_a_malformed_id_or_cursor_and_an_unknown_workflow(tmp_path):
    store = WorkflowStore(tmp_path)
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "workflow.status", "workflow_id": "nope"}, tmp_path, store=store)
    assert exc.value.reason_code == "MALFORMED_REQUEST"
    with pytest.raises(WorkflowBlocked) as exc:
        _apply({"command": "workflow.status", "workflow_id": "wf_" + "0" * 20}, tmp_path, store=store)
    assert exc.value.reason_code == "WORKFLOW_NOT_FOUND"
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "workflow.events", "after_cursor": -1}, tmp_path, store=store)
    assert exc.value.reason_code == "MALFORMED_REQUEST"
    with pytest.raises(ControlBlocked):
        _apply({"command": "workflow.list", "limit": True}, tmp_path, store=store)


def test_cancel_needs_the_version_the_caller_read_and_a_reason(tmp_path):
    store = WorkflowStore(tmp_path)
    wid = _submit(store, tmp_path)["data"]["workflow_id"]
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "workflow.cancel", "workflow_id": wid, "expected_version": 1}, tmp_path, store=store)
    assert exc.value.reason_code == "REASON_REQUIRED"
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "workflow.cancel", "workflow_id": wid, "reason": "x"}, tmp_path, store=store)
    assert exc.value.reason_code == "MALFORMED_REQUEST"
    with pytest.raises(WorkflowBlocked) as exc:
        _apply({"command": "workflow.cancel", "workflow_id": wid, "expected_version": 9, "reason": "x"}, tmp_path, store=store)
    assert exc.value.reason_code == "VERSION_CONFLICT"
    out = _apply({"command": "workflow.cancel", "workflow_id": wid, "expected_version": 1, "reason": "그만"}, tmp_path, store=store)
    assert out["ok"] and out["reply"].startswith("CANCELLED") and out["data"]["status"] == wf.W_CANCELLED


def test_a_cancel_of_running_work_says_cancelling_not_cancelled(tmp_path):
    store = WorkflowStore(tmp_path)
    wid = _submit(store, tmp_path)["data"]["workflow_id"]
    store.claim_ready(now=NOW)
    version = store.status_view(wid, now=NOW)["row_version"]
    out = _apply({"command": "workflow.cancel", "workflow_id": wid, "expected_version": version, "reason": "x"},
                 tmp_path, store=store)
    assert out["reply"].startswith("CANCELLING") and out["data"]["status"] == wf.W_CANCELLING


def test_the_door_opened_with_the_store_serves_v3_and_the_flag_decides(tmp_path, monkeypatch):
    from runtime.mvp_runtime.store import LedgerStore

    monkeypatch.delenv(socket_door.CLIENT_GID_ENV, raising=False)
    monkeypatch.delenv(socket_door.CLIENT_UID_ENV, raising=False)
    if not socket_door.UNIX_SOCKETS_AVAILABLE:
        pytest.skip("the dispatch door listens on AF_UNIX")
    store = WorkflowStore(tmp_path)
    server = dispatch_bridge.open_door(
        tmp_path / "dispatch.sock", control_store=ControlStore(tmp_path), ledger=LedgerStore(tmp_path),
        worker_socket=tmp_path / "absent.sock", workflow_store=store, manager_enabled=True,
    )
    try:
        out = server.apply({"command": "capabilities", "proto": 2})
        assert out["data"]["workflow_manager"] is True
    finally:
        server.server_close()


# --- retry by decision (P06) --------------------------------------------------------------------

def test_retry_step_re_opens_a_settled_step_at_the_version_read(tmp_path):
    store = WorkflowStore(tmp_path)
    wid = _submit(store, tmp_path, plan=_plan(steps=[{"id": "research", "capability": "research", "request": "x",
                                                     "reason": "r", "max_attempts": 1}]))["data"]["workflow_id"]
    (attempt,) = store.claim_ready(now=NOW)
    store.record_result(attempt.attempt_id, now=NOW, succeeded=False, reason_code="PROVIDER_UNAVAILABLE")
    view = _apply({"command": "workflow.status", "workflow_id": wid}, tmp_path, store=store)["data"]
    assert view["status"] == wf.W_WAITING_REPLAN
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "workflow.retry_step", "workflow_id": wid, "expected_version": view["row_version"],
                "reason": "r"}, tmp_path, store=store)
    assert exc.value.reason_code == "MALFORMED_REQUEST"                              # no step_key
    out = _apply({"command": "workflow.retry_step", "workflow_id": wid, "step_key": "research",
                  "expected_version": view["row_version"], "reason": "공급자 복구"}, tmp_path, store=store)
    assert out["ok"] and out["reply"].startswith("RETRY OPENED") and out["data"]["status"] == wf.W_RUNNING
    assert out["data"]["steps"][0]["status"] == wf.S_READY
    with pytest.raises(WorkflowBlocked) as exc:
        _apply({"command": "workflow.retry_step", "workflow_id": wid, "step_key": "research",
                "expected_version": view["row_version"], "reason": "again"}, tmp_path, store=store)
    assert exc.value.reason_code in {"VERSION_CONFLICT", "RETRY_NOT_APPLICABLE"}
    assert "workflow.retry_step" in _apply({"command": "capabilities"}, tmp_path, store=store)["data"]["commands"]


def test_propose_update_replaces_the_plan_at_the_version_read(tmp_path):
    store = WorkflowStore(tmp_path)
    wid = _submit(store, tmp_path)["data"]["workflow_id"]
    view = _apply({"command": "workflow.status", "workflow_id": wid}, tmp_path, store=store)["data"]
    with pytest.raises(WorkflowBlocked) as exc:
        _apply({"command": "workflow.propose_update", "workflow_id": wid, "expected_version": view["row_version"],
                "reason": "r"}, tmp_path, store=store)
    assert exc.value.reason_code == "PLAN_INVALID"                                    # no plan
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "workflow.propose_update", "workflow_id": wid, "expected_version": view["row_version"],
                "plan": _plan()}, tmp_path, store=store)
    assert exc.value.reason_code == "REASON_REQUIRED"
    plan = _plan(goal="목표 (수정)")
    plan["steps"].append({"id": "summary", "capability": "analysis", "request": "요약", "reason": "r",
                          "depends_on": ["research"], "input_refs": ["research"]})
    out = _apply({"command": "workflow.propose_update", "workflow_id": wid, "expected_version": view["row_version"],
                  "plan": plan, "reason": "요약 단계 추가"}, tmp_path, store=store)
    assert out["ok"] and out["reply"].startswith("PLAN UPDATED to v2") and out["data"]["plan_version"] == 2
    assert [s["key"] for s in out["data"]["steps"]] == ["research", "summary"] and out["data"]["goal"] == "목표 (수정)"
    with pytest.raises(WorkflowBlocked) as exc:
        _apply({"command": "workflow.propose_update", "workflow_id": wid, "expected_version": view["row_version"],
                "plan": plan, "reason": "again"}, tmp_path, store=store)
    assert exc.value.reason_code == "VERSION_CONFLICT"
    assert "workflow.propose_update" in _apply({"command": "capabilities"}, tmp_path, store=store)["data"]["commands"]


def test_report_usage_records_the_reported_layer_and_the_reply_says_it_is_not_enforced(tmp_path):
    store = WorkflowStore(tmp_path)
    wid = _submit(store, tmp_path)["data"]["workflow_id"]
    with pytest.raises(ControlBlocked) as exc:
        _apply({"command": "workflow.report_usage", "workflow_id": wid}, tmp_path, store=store)
    assert exc.value.reason_code == "MALFORMED_REQUEST"
    with pytest.raises(WorkflowBlocked) as exc:
        _apply({"command": "workflow.report_usage", "workflow_id": wid, "reported_usage": {"tokens": 5}}, tmp_path, store=store)
    assert exc.value.reason_code == "REPORTED_USAGE_INVALID"
    out = _apply({"command": "workflow.report_usage", "workflow_id": wid, "reported_usage": {
        "input_tokens": 62456, "output_tokens": 1816, "estimated_cost_usd": 0.0024, "cost_status": "estimated",
        "source": "hermes:session_model_usage:session"}}, tmp_path, store=store)
    assert out["ok"] and out["reply"].startswith("USAGE REPORTED") and "not enforced" in out["reply"]
    assert out["data"]["budget"]["reported"]["input_tokens"] == 62456 and out["data"]["budget"]["reported"]["as_of"] == NOW
    status = _apply({"command": "workflow.status", "workflow_id": wid}, tmp_path, store=store)
    assert "보고된 사용량(Hermes, 강제 아님)" in status["reply"] and "62,456" in status["reply"]
    assert status["data"]["budget"]["reserved_model_calls"] == 0                      # untouched by the report
    assert "workflow.report_usage" in _apply({"command": "capabilities"}, tmp_path, store=store)["data"]["commands"]
