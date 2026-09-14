"""Render branches of the four shims, fed Answers directly — no socket, no FastMCP."""

from __future__ import annotations

import thomas_door_client as door
import read_bridge_mcp as read_shim
import dispatch_bridge_mcp as dispatch_shim
import switch_bridge_mcp as switch_shim
import knowledge_bridge_mcp as knowledge_shim


def _answer(name, frame=None, failure=None, sent=False, detail=""):
    return door.Answer(door=door.DOORS[name], frame=frame, failure=failure, sent=sent, detail=detail)


# --- read ---------------------------------------------------------------------------------

def test_read_tools_are_the_thirteen_read_verbs_and_nothing_mutating():
    assert set(read_shim.mcp.tools) == {
        "trading_status", "trading_readiness", "paper_performance", "runtime_status", "task_list",
        "task_history", "task_result", "current_funds", "memory_candidates",
        "schedules", "scheduler_events", "heartbeat", "approval_status",
    }


def test_read_stamps_a_success_and_renders_a_refusal_plainly():
    ok = read_shim._render(_answer("read", {"ok": True, "reply": "board", "data": {}}))
    assert ok.startswith("[SNAPSHOT ") and ok.endswith("board")
    no = read_shim._render(_answer("read", {"ok": False, "reason_code": "VERB_NOT_PERMITTED", "reason": "x"}))
    assert no == "REFUSED [VERB_NOT_PERMITTED]: x." and "SNAPSHOT" not in no
    down = read_shim._render(_answer("read", failure=door.NO_SOCKET))
    assert down.startswith("UNAVAILABLE: no read door")


def test_read_forwards_the_argument_only_when_given(monkeypatch):
    seen = []
    monkeypatch.setattr(door, "ask", lambda d, p, **kw: seen.append((d, p, kw)) or _answer("read", {"ok": True, "reply": "r"}))
    read_shim.scheduler_events("5"); read_shim.heartbeat(); read_shim.approval_status("approval_x")
    assert seen[0][1] == {"command": "scheduler_events", "argument": "5"}
    assert seen[1][1] == {"command": "heartbeat"}
    assert seen[2][1] == {"command": "approval_status", "argument": "approval_x"}
    assert all(kw == {} for _, _, kw in seen)          # no request_id on reads


# --- dispatch -----------------------------------------------------------------------------

def test_dispatch_fresh_replayed_in_flight_and_slow_read_differently():
    fresh = dispatch_shim._render(
        _answer("dispatch", {"ok": True, "kind": "analysis", "actor": "assistant_bridge", "task_id": "task_1",
                             "registry_entry_id": "treg_1", "final_response": "the report", "data": {}}),
        kind="analysis", request_id="hermes-1")
    assert fresh.startswith("DONE (analysis") and "request_id=hermes-1" in fresh and fresh.endswith("the report")

    replayed = dispatch_shim._render(
        _answer("dispatch", {"ok": True, "replayed": True, "data": {"task_id": "task_1", "registry_entry_id": "treg_1",
                                                                     "status": "DELIVERED", "result": "the report"}}),
        kind="analysis", request_id="hermes-1")
    assert replayed.startswith("REPLAYED (not re-run)") and "status=DELIVERED" in replayed and replayed.endswith("the report")

    big = dispatch_shim._render(
        _answer("dispatch", {"ok": True, "replayed": True, "data": {"task_id": "task_1", "registry_entry_id": "treg_1",
                                                                     "status": "DELIVERED", "result": None, "result_ref": "ledger:x"}}),
        kind="analysis", request_id="hermes-1")
    assert 'task_result("treg_1")' in big

    running = dispatch_shim._render(
        _answer("dispatch", {"ok": False, "reason_code": "REQUEST_IN_FLIGHT", "reason": "r", "data": {"status": "RUNNING"}}),
        kind="analysis", request_id="hermes-1")
    assert running.startswith("IN_FLIGHT") and "Nothing new was started" in running

    slow = dispatch_shim._render(_answer("dispatch", failure=door.TIMEOUT_AFTER_SEND, sent=True, detail="timed out"),
                                 kind="analysis", request_id="hermes-1")
    assert slow.startswith("STARTED_BUT_SLOW") and 'request_id="hermes-1"' in slow and "Do NOT start a new dispatch" in slow

    dead = dispatch_shim._render(_answer("dispatch", failure=door.NOT_SENT, detail="refused"), kind="analysis", request_id="hermes-1")
    assert dead.startswith("UNAVAILABLE") and dead.endswith("Nothing was started.")


def test_dispatch_mints_a_request_id_or_reuses_the_given_one(monkeypatch):
    seen = []
    monkeypatch.setattr(door, "ask", lambda d, p, **kw: seen.append((p, kw)) or _answer("dispatch", {"ok": True, "data": {}}))
    dispatch_shim._dispatch_blocking("research", "q", "why", "a, b")
    dispatch_shim._dispatch_blocking("research", "q", "why", "", "hermes-given")
    assert seen[0][0] == {"request": "q", "kind": "research", "reason": "why", "naver_keywords": "a, b"}
    assert seen[0][1]["request_id"].startswith("hermes-") and seen[1][1]["request_id"] == "hermes-given"
    assert dispatch_shim._dispatch_blocking("research", " ", "why").startswith("REFUSED: a request is required")


# --- switch -------------------------------------------------------------------------------

def test_switch_approval_required_names_both_bots_and_the_mirror_and_never_the_scope_special_case():
    frame = {"ok": False, "reason_code": "APPROVAL_REQUIRED", "reason": "r", "approval_id": "approval_a",
             "expires_at": "2026-09-04T00:15:00Z", "domain": "crypto", "scope": "runtime", "mode": "ACTIVE",
             "clears": "no stop", "approve_with": "/approve approval_a"}
    text = switch_shim._render(_answer("switch", frame), payload={"command": "enable", "scope": "runtime"},
                               retry_tool="resume_runtime_only", request_id="hermes-9")
    assert text.startswith("NOT DONE") and switch_shim.CONTROL_BOT_ID in text and switch_shim.ASSISTANT_BOT_ID in text
    assert "[알림 사본]" in text and "approval_status(<id>)" in text
    # the spend needs its own request_id — the ask's is named, but only for retrying the ask
    assert "resume_runtime_only again WITH that approval id and request_id EMPTY" in text
    assert 'This ask\'s request_id was "hermes-9"' in text and "REQUEST_ID_REUSED" in text
    # an unknown-key refusal is a refusal, not "an older runtime" (the old special case is gone)
    unknown = switch_shim._render(
        _answer("switch", {"ok": False, "reason_code": "ARGUMENT_NOT_ACCEPTED", "reason": "keys: approval_id, scope"}),
        payload={"command": "enable"}, retry_tool="start_trading", request_id=None)
    assert unknown.startswith("REFUSED [ARGUMENT_NOT_ACCEPTED]") and "older than this tool" not in unknown


def test_switch_status_is_stamped_and_a_replayed_enable_says_so():
    status = switch_shim._render(_answer("switch", {"ok": True, "domain": "crypto", "mode": "ACTIVE", "reply": "r"}),
                                 payload={"command": "status"}, retry_tool="start_trading", request_id=None)
    assert status.startswith("[SNAPSHOT ") and "Nothing was changed" in status
    # A replayed SPEND carries the first application in `data` (the door's recorded outcome),
    # not in the top-level keys — the 2026-09-04 drill rendered "None applied to None" before.
    done = switch_shim._render(
        _answer("switch", {"ok": True, "replayed": True, "request_id": "hermes-9", "reason": "already applied",
                           "outcome": {"action": "resume", "mode": "ACTIVE", "domain": "crypto", "approval_id": "approval_a",
                                       "scope": "runtime", "trading_armed": False},
                           "data": {"action": "resume", "mode": "ACTIVE", "domain": "crypto", "approval_id": "approval_a",
                                    "scope": "runtime", "trading_armed": False}}),
        payload={"command": "enable", "approval_id": "approval_a"}, retry_tool="resume_runtime_only", request_id="hermes-9")
    assert done.startswith("DONE: resume applied to crypto. Runtime mode is now ACTIVE (changed=n/a (replay)")
    assert "(REPLAYED:" in done and "remain DISARMED" in done and "None" not in done
    # A replayed ASK: the ask was the effect; nothing new is minted, and the id may be dead by now.
    asked = switch_shim._render(
        _answer("switch", {"ok": True, "replayed": True, "data": {"approval_id": "approval_a", "expires_at": "2026-09-04T11:20:02Z",
                                                                   "approve_with": "/approve approval_a", "domain": "crypto",
                                                                   "stop_ref": None, "scope": "runtime"}}),
        payload={"command": "enable", "scope": "runtime"}, retry_tool="resume_runtime_only", request_id="hermes-9")
    assert asked.startswith("NOT DONE (REPLAYED)") and "approval_a" in asked and switch_shim.CONTROL_BOT_ID in asked
    assert "DONE:" not in asked.replace("NOT DONE", "") and "resume_runtime_only again with a NEW request_id" in asked


def test_switch_enable_carries_a_request_id_and_disable_does_not(monkeypatch):
    seen = []
    monkeypatch.setattr(door, "ask", lambda d, p, **kw: seen.append((p, kw)) or _answer("switch", {"ok": True, "reply": ""}))
    switch_shim.stop_trading("why"); switch_shim.start_trading("why"); switch_shim.resume_runtime_only("why", request_id="hermes-2")
    assert seen[0][0]["command"] == "disable" and seen[0][1] == {"request_id": None}
    assert seen[1][0] == {"command": "enable", "reason": "why", "domain": "crypto"} and seen[1][1]["request_id"].startswith("hermes-")
    assert seen[2][0] == {"command": "enable", "reason": "why", "domain": "crypto", "scope": "runtime"} and seen[2][1]["request_id"] == "hermes-2"


# --- knowledge ----------------------------------------------------------------------------

def test_knowledge_ask_returns_the_frame_or_a_sentence(monkeypatch):
    monkeypatch.setattr(door, "ask", lambda d, p, **kw: _answer("knowledge", {"ok": True, "stats": {"documents": 1}}))
    assert knowledge_shim._ask({"command": "stats"}) == {"ok": True, "stats": {"documents": 1}}
    monkeypatch.setattr(door, "ask", lambda d, p, **kw: _answer("knowledge", failure=door.FRAME_TOO_LARGE, detail="too big"))
    assert knowledge_shim._ask({"command": "add_document"}) == "REFUSED: too big"
    monkeypatch.setattr(door, "ask", lambda d, p, **kw: _answer("knowledge", {"ok": False, "reason_code": "KILLED", "reason": "k"}))
    assert knowledge_shim._ask({"command": "add_document"}) == "REFUSED [KILLED]: k."
    assert set(knowledge_shim.mcp.tools) == {"file_document", "file_pdf", "search_knowledge", "knowledge_stats"}


# --- the read shim's [data] line (v2.3, sequence 2 P02) -----------------------------------

def test_read_carries_the_data_line_only_where_asked_and_verbatim():
    frame = {"ok": True, "reply": "board", "data": {
        "infrastructure_ready": True, "live_entry_possible": False,
        "live_armed_strategies": {"armed": 0, "known": True}, "recorded_gate": {"stale": True}}}
    plain = read_shim._render(_answer("read", frame))
    assert plain.endswith("board") and "[data]" not in plain
    rich = read_shim._render(_answer("read", frame), with_data=True)
    assert "\n\n[data] " in rich and rich.index("board") < rich.index("[data]")
    for needle in ('"infrastructure_ready":true', '"live_entry_possible":false', '"armed":0', '"stale":true'):
        assert needle in rich, needle
    # refusals and failures never grow a data line, and an empty `data` grows none either
    assert "[data]" not in read_shim._render(_answer("read", {"ok": False, "reason_code": "X", "reason": "r", "data": {"k": 1}}), with_data=True)
    assert "[data]" not in read_shim._render(_answer("read", {"ok": True, "reply": "r", "data": {}}), with_data=True)


def test_read_data_line_is_capped_and_says_so():
    text = read_shim._render(_answer("read", {"ok": True, "reply": "r", "data": {"rows": ["x" * 100] * 100}}), with_data=True)
    assert "[data truncated at 4000 chars]" in text
    assert len(text) < 4000 + 300


def test_the_five_structured_reads_ask_for_data_and_the_others_do_not(monkeypatch):
    monkeypatch.setattr(door, "ask", lambda d, p, **kw: _answer("read", {"ok": True, "reply": "r", "data": {"k": 1}}))
    with_data = [read_shim.trading_readiness(), read_shim.current_funds(), read_shim.heartbeat(),
                 read_shim.approval_status("approval_a"), read_shim.runtime_status()]
    assert all("[data] {\"k\":1}" in text for text in with_data)
    without = [read_shim.trading_status(), read_shim.task_list(), read_shim.schedules(),
               read_shim.task_history(), read_shim.paper_performance(), read_shim.memory_candidates()]
    assert all("[data]" not in text for text in without)


# --- the dispatch shim's door API v3 tools (v2.4, sequence 2 P05) ---------------------------------

_WID = "wf_" + "1" * 20


def test_dispatch_tools_are_the_four_dispatches_plus_the_seven_workflow_tools():
    assert set(dispatch_shim.mcp.tools) == {
        "analyze", "research", "translate", "draft_content",
        "thomas_capabilities", "submit_workflow", "workflow_status", "workflow_list", "workflow_events",
        "cancel_workflow", "retry_workflow_step",
    }


def test_submit_sends_the_plan_under_a_request_id_and_renders_accepted_or_replayed(monkeypatch):
    seen = []

    def ask(d, p, **kw):
        seen.append((p, kw))
        return _answer("dispatch", {"ok": True, "command": "workflow.submit", "reply": "ACCEPTED: workflow " + _WID,
                                    "replayed": False, "data": {"workflow_id": _WID, "status": "VALIDATED", "replayed": False}})

    monkeypatch.setattr(door, "ask", ask)
    out = dispatch_shim.submit_workflow('{"goal": "g", "steps": [], "budget": {"max_model_calls": 2}}')
    assert out.startswith("ACCEPTED") and "Keep this request_id" in out and '"workflow_id":"' + _WID + '"' in out
    payload, kw = seen[0]
    assert payload["command"] == "workflow.submit" and payload["plan"]["schema_version"] == "workflow_plan.v0.1"
    assert kw["request_id"].startswith("hermes-")
    dispatch_shim.submit_workflow('{"goal": "g", "steps": [], "budget": {"max_model_calls": 2}}', request_id="hermes-given")
    assert seen[1][1]["request_id"] == "hermes-given"


def test_submit_refuses_bad_json_locally_and_sends_nothing(monkeypatch):
    monkeypatch.setattr(door, "ask", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")))
    assert dispatch_shim.submit_workflow("").startswith("REFUSED: plan_json is required")
    assert dispatch_shim.submit_workflow("{not json").startswith("REFUSED: plan_json is not valid JSON")
    assert dispatch_shim.submit_workflow("[1, 2]").startswith("REFUSED: plan_json must be a JSON object")


def test_a_v2_only_runtime_and_an_unconfirmed_submit_read_differently():
    unavailable = dispatch_shim._render_workflow(
        _answer("dispatch", {"ok": False, "reason_code": "WORKFLOW_UNAVAILABLE", "reason": "r"}),
        command="workflow.submit", request_id="hermes-1")
    assert unavailable.startswith("REFUSED [WORKFLOW_UNAVAILABLE]") and "nothing was started" in unavailable
    assert "analyze / research / translate / draft_content" in unavailable
    slow = dispatch_shim._render_workflow(_answer("dispatch", failure=door.TIMEOUT_AFTER_SEND, sent=True, detail="t"),
                                          command="workflow.submit", request_id="hermes-1")
    assert slow.startswith("SUBMITTED_BUT_UNCONFIRMED") and 'request_id="hermes-1"' in slow and "new id" in slow
    dead = dispatch_shim._render_workflow(_answer("dispatch", failure=door.NOT_SENT, detail="refused"),
                                          command="workflow.status", request_id=None)
    assert dead.startswith("UNAVAILABLE") and dead.endswith("Nothing was started.")
    refused = dispatch_shim._render_workflow(
        _answer("dispatch", {"ok": False, "reason_code": "VERSION_CONFLICT", "reason": "v"}), command="workflow.cancel")
    assert refused == "REFUSED [VERSION_CONFLICT]: v. Nothing was changed."


def test_status_list_events_and_cancel_send_their_shapes_and_carry_data(monkeypatch):
    seen = []
    monkeypatch.setattr(door, "ask", lambda d, p, **kw: seen.append((p, kw)) or _answer(
        "dispatch", {"ok": True, "command": p["command"], "reply": "r", "data": {"row_version": 3}}))
    assert '[data] {"row_version":3}' in dispatch_shim.workflow_status(_WID)
    dispatch_shim.workflow_list("7"); dispatch_shim.workflow_events("12", "5"); dispatch_shim.workflow_events()
    dispatch_shim.cancel_workflow(_WID, "3", "그만")
    assert seen[0][0] == {"command": "workflow.status", "workflow_id": _WID}
    assert seen[1][0] == {"command": "workflow.list", "limit": 7}
    assert seen[2][0] == {"command": "workflow.events", "after_cursor": 12, "limit": 5}
    assert seen[3][0] == {"command": "workflow.events", "after_cursor": 0}
    assert seen[4][0] == {"command": "workflow.cancel", "workflow_id": _WID, "expected_version": 3, "reason": "그만"}
    assert all(kw == {"request_id": None} for _, kw in seen)          # only a submit carries an id
    assert dispatch_shim.cancel_workflow(_WID, "three", "x").startswith("REFUSED: expected_version")
    assert dispatch_shim.cancel_workflow(_WID, "3", " ").startswith("REFUSED: a reason")
    assert dispatch_shim.workflow_status(" ").startswith("REFUSED: workflow_id")
    assert len(seen) == 5


def test_the_data_line_is_one_helper_shared_by_the_shims():
    answer = _answer("read", {"ok": True, "reply": "r", "data": {"b": 1, "a": [1, 2]}})
    assert door.data_line(answer) == '\n\n[data] {"a":[1,2],"b":1}'
    assert door.data_line(_answer("read", {"ok": True, "reply": "r", "data": {}})) == ""
    assert read_shim._render(answer, with_data=True).endswith('[data] {"a":[1,2],"b":1}')
    big = door.data_line(_answer("read", {"ok": True, "reply": "r", "data": {"rows": ["x" * 100] * 100}}), max_chars=500)
    assert "[data truncated at 500 chars]" in big


def test_retry_step_sends_its_shape_and_refuses_locally_what_the_door_would(monkeypatch):
    seen = []
    monkeypatch.setattr(door, "ask", lambda d, p, **kw: seen.append(p) or _answer(
        "dispatch", {"ok": True, "command": p["command"], "reply": "RETRY OPENED", "data": {"status": "RUNNING"}}))
    out = dispatch_shim.retry_workflow_step(_WID, "draft1", "4", "공급자 복구")
    assert out.startswith("RETRY OPENED") and '"status":"RUNNING"' in out
    assert seen == [{"command": "workflow.retry_step", "workflow_id": _WID, "step_key": "draft1",
                     "expected_version": 4, "reason": "공급자 복구"}]
    assert dispatch_shim.retry_workflow_step(_WID, "", "4", "r").startswith("REFUSED: workflow_id and step_key")
    assert dispatch_shim.retry_workflow_step(_WID, "draft1", "four", "r").startswith("REFUSED: expected_version")
    assert len(seen) == 1
