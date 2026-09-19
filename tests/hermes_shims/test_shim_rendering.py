"""Render branches of the four shims, fed Answers directly — no socket, no FastMCP."""

from __future__ import annotations

import json

import pytest

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


def test_switch_tools_are_status_the_two_stops_the_halt_the_two_starts_and_the_close_ask():
    assert set(switch_shim.mcp.tools) == {"trading_switch_status", "stop_trading", "pause_trading", "halt_trading",
                                          "start_trading", "resume_runtime_only", "request_emergency_close"}


def test_the_emergency_close_request_sends_its_shape_under_a_request_id(monkeypatch):
    """Shim 2.14 (decision 49: the assistant only asks). A request_id, because the ask is minted, and a
    retry must not mint a second one."""
    seen = []
    monkeypatch.setattr(door, "ask", lambda d, p, **kw: seen.append((p, kw)) or _answer("switch", {"ok": True, "reply": ""}))
    switch_shim.request_emergency_close("거래소 장애"); switch_shim.request_emergency_close("r", request_id="hermes-7")
    assert seen[0][0] == {"command": "emergency_close", "reason": "거래소 장애", "domain": "crypto"}
    assert seen[0][1]["request_id"].startswith("hermes-") and seen[1][1]["request_id"] == "hermes-7"


_CLOSE_ASK = {"approval_id": "approval_e", "expires_at": "2026-09-19T12:15:00Z", "approve_with": "/approve approval_e",
              "confirm_with": "docker exec -u 10001 thomas-scheduler python -m scripts.emergency_close --confirm "
                              "--approval-id approval_e",
              "positions": [{"position_id": "live-btc", "symbol": "BTCUSDT", "direction": "LONG", "quantity": "0.002"}]}


def test_the_emergency_close_ask_says_nothing_closed_and_names_two_steps_neither_the_models():
    frame = {"ok": False, "reason_code": "APPROVAL_REQUIRED", "reason": "r", "action": "emergency_close",
             "halt": "the HARD halt placed by tg-1 at T", **_CLOSE_ASK}
    text = switch_shim._render(_answer("switch", frame), payload={"command": "emergency_close"},
                               retry_tool="request_emergency_close", request_id="hermes-1")
    assert text.startswith("NOT DONE — nothing has been closed and no order was sent")
    assert switch_shim.CONTROL_BOT_ID in text and "/approve approval_e" in text
    assert "--confirm --approval-id approval_e" in text and "the OPERATOR runs" in text
    assert "You cannot approve it and you cannot confirm it" in text and "Never say positions are closing" in text
    assert "BTCUSDT LONG 0.002" in text and "the HARD halt placed by tg-1 at T" in text
    # Not the re-arm ask's text: no scope, no restart, no spend by the model.
    assert "scope" not in text and "restart trading" not in text and "WITH that approval id" not in text


def test_a_replayed_emergency_close_ask_answers_from_the_record_without_a_scope():
    text = switch_shim._render(_answer("switch", {"ok": True, "replayed": True, "data": dict(_CLOSE_ASK)}),
                               payload={"command": "emergency_close"}, retry_tool="request_emergency_close",
                               request_id="hermes-1")
    assert text.startswith("NOT DONE (REPLAYED)") and "approval_e" in text and "scope" not in text
    assert "request_emergency_close again with a NEW request_id" in text and "DONE:" not in text.replace("NOT DONE", "")
    # The record is up to 24 hours old: it can say what that call did, never that nothing closed since
    # (review of #916). Re-asking is Thomas's to want, not the expiry's.
    assert "this call sent nothing" in text and "Nothing has been closed" not in text
    assert "no order was sent" not in text and "Thomas still wants the close" in text
    assert "request id  : hermes-1" in text


def test_the_emergency_close_ask_names_the_request_id_to_retry_with():
    frame = {"ok": False, "reason_code": "APPROVAL_REQUIRED", "reason": "r", "action": "emergency_close", **_CLOSE_ASK}
    text = switch_shim._render(_answer("switch", frame), payload={"command": "emergency_close"},
                               retry_tool="request_emergency_close", request_id="hermes-1")
    assert "request id  : hermes-1" in text and "Ask again only if Thomas still wants the close" in text


@pytest.mark.parametrize("failure", [door.TIMEOUT_AFTER_SEND, door.EMPTY_REPLY, door.UNPARSEABLE])
def test_an_emergency_close_frame_sent_without_an_answer_is_unconfirmed_and_names_the_id(failure):
    """M1: the door can mint the ask after the client stops waiting. "Nothing was changed" and the
    Telegram advice were both false here, and the model, never shown its id, retried under a new one."""
    text = switch_shim._render(_answer("switch", failure=failure, sent=True, detail="timed out"),
                               payload={"command": "emergency_close"}, retry_tool="request_emergency_close",
                               request_id="hermes-9")
    assert text.startswith("UNCONFIRMED:") and "MAY HAVE BEEN MINTED" in text
    assert 'request_id="hermes-9"' in text and "Do NOT call again under a new id" in text
    assert "Nothing was changed" not in text and "Telegram" not in text
    assert "scripts.emergency_close --show" in text


@pytest.mark.parametrize("failure", [door.NOT_SENT, door.NO_SOCKET])
def test_an_emergency_close_frame_that_never_left_minted_nothing_and_points_to_the_operator(failure):
    text = switch_shim._render(_answer("switch", failure=failure, detail="refused"),
                               payload={"command": "emergency_close"}, retry_tool="request_emergency_close",
                               request_id="hermes-9")
    assert text.startswith("UNAVAILABLE:") and "No ask was minted" in text
    assert "scripts.emergency_close --request" in text and "Telegram" not in text


def test_an_emergency_close_refusal_is_a_refusal():
    text = switch_shim._render(
        _answer("switch", {"ok": False, "reason_code": "CONTROL_VERB_NOT_GRANTED", "reason": "not granted"}),
        payload={"command": "emergency_close"}, retry_tool="request_emergency_close", request_id="hermes-1")
    assert text.startswith("REFUSED [CONTROL_VERB_NOT_GRANTED]") and "Nothing was changed" in text


def test_the_halt_sends_soft_or_hard_and_no_request_id(monkeypatch):
    """Shim 2.13: the door has carried `disable mode=soft|hard` since PR6a; no tool sent it."""
    seen = []
    monkeypatch.setattr(door, "ask", lambda d, p, **kw: seen.append((p, kw)) or _answer("switch", {"ok": True, "reply": ""}))
    switch_shim.halt_trading("why"); switch_shim.halt_trading("why", hard=True)
    assert [p for p, _kw in seen] == [{"command": "disable", "mode": "soft", "reason": "why", "domain": "crypto"},
                                      {"command": "disable", "mode": "hard", "reason": "why", "domain": "crypto"}]
    assert all(kw == {"request_id": None} for _p, kw in seen)


def _disable(mode, frame):
    return switch_shim._render(_answer("switch", {"ok": True, "reply": "runtime says", "action": "halt_trading",
                                                  "actor": "assistant_bridge", "domain": "crypto", **frame}),
                               payload={"command": "disable", "mode": mode}, retry_tool="start_trading", request_id=None)


def test_a_halt_on_an_active_runtime_says_positions_are_still_managed():
    """F11 (review of PR6a): the stop's note — "dropped the scheduler's due cycles ... NOT being settled" —
    was rendered for every disable, and is false for a halt, which leaves the runtime ACTIVE."""
    text = _disable("hard", {"mode": "ACTIVE", "changed": True})
    assert "runtime stays ACTIVE" in text and "settled, protected, time-exited and reconciled" in text
    assert "NOT being settled" not in text and "due cycles" not in text and "runtime says" in text


def test_a_halt_on_a_stopped_runtime_says_the_stop_stays_and_the_halt_is_recorded_under_it():
    text = _disable("soft", {"mode": "KILLED", "changed": True})
    assert "still KILLED" in text and "cannot release a stop" in text and "recorded under the stop" in text
    assert "NOT being settled" in text and "runtime stays ACTIVE" not in text


def test_a_halt_that_changed_nothing_claims_nothing():
    """Refused to loosen, already at that level, or the state moved while it was applied: the reply says
    which, and the shim must not claim entries are halted."""
    text = _disable("soft", {"mode": "ACTIVE", "changed": False})
    assert "Nothing changed" in text and "trading_switch_status" in text
    assert "New live entries are refused" not in text and "recorded under the stop" not in text


def test_kill_and_pause_keep_the_stop_note():
    for mode in ("kill", "pause"):
        text = _disable(mode, {"mode": "KILLED" if mode == "kill" else "PAUSED", "changed": True})
        assert "dropped the scheduler's due cycles" in text and "runtime stays ACTIVE" not in text


def test_the_halt_notes_carry_the_sentences_the_model_must_repeat():
    """Review of #915: the branch was pinned, the sentences were not. Each one below is a claim the model
    relays to Thomas, and each is true only in its own branch."""
    hard = _disable("hard", {"mode": "ACTIVE", "changed": True})
    soft = _disable("soft", {"mode": "ACTIVE", "changed": True})
    assert "under the HARD halt" in hard and "At HARD the order adapter also refuses every order" in hard
    assert "under the SOFT halt" in soft and "At HARD" not in soft
    for text in (hard, soft):
        assert "resume_runtime_only keeps it" in text and "needs Thomas's start_trading approval" in text
    under = _disable("hard", {"mode": "PAUSED", "changed": True})
    assert "resume_runtime_only comes back to that halt, not to live entries" in under
    assert "The HARD halt is recorded under the stop" in under and "keeps it" not in under


@pytest.mark.parametrize("runtime_mode", ["ACTIVE", "KILLED", "PAUSED"])
def test_a_disable_that_changed_nothing_opens_with_not_changed_and_warns_under_a_stop(runtime_mode):
    """Review of #915: a no-op opened with "DONE: halt_trading applied", and on a stopped runtime said
    nothing about positions going unmanaged."""
    for mode in ("soft", "hard", "pause"):
        text = _disable(mode, {"mode": runtime_mode, "changed": False})
        assert text.startswith("NOT CHANGED: halt_trading left crypto as it was") and "applied" not in text
        assert "positions keep being settled" not in text and "recorded under the stop" not in text
        assert ("NOT being settled" in text) is (runtime_mode != "ACTIVE")
        assert text.endswith("Runtime reply: runtime says")


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


def test_dispatch_tools_are_the_four_dispatches_plus_the_eleven_workflow_tools():
    assert set(dispatch_shim.mcp.tools) == {
        "analyze", "research", "translate", "draft_content",
        "thomas_capabilities", "submit_workflow", "workflow_status", "workflow_list", "workflow_events",
        "cancel_workflow", "retry_workflow_step", "propose_workflow_update",
        "workflow_changes", "report_workflow_usage", "propose_schedule_change",
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


def test_propose_update_sends_the_whole_plan_at_the_version_read(monkeypatch):
    seen = []
    monkeypatch.setattr(door, "ask", lambda d, p, **kw: seen.append(p) or _answer(
        "dispatch", {"ok": True, "command": p["command"], "reply": "PLAN UPDATED to v2", "data": {"plan_version": 2}}))
    out = dispatch_shim.propose_workflow_update(_WID, "3", '{"goal": "g", "steps": [], "budget": {"max_model_calls": 9}}', "예산 증액")
    assert out.startswith("PLAN UPDATED") and '"plan_version":2' in out
    assert seen[0]["command"] == "workflow.propose_update" and seen[0]["expected_version"] == 3
    assert seen[0]["plan"]["schema_version"] == "workflow_plan.v0.1" and seen[0]["reason"] == "예산 증액"
    assert dispatch_shim.propose_workflow_update(_WID, "3", "{bad", "r").startswith("REFUSED: plan_json is not valid JSON")
    assert dispatch_shim.propose_workflow_update(_WID, "x", "{}", "r").startswith("REFUSED: expected_version")
    assert len(seen) == 1


# --- P08: narration by polling, usage reporting ----------------------------------------------------

def _events_answer(events, next_cursor):
    return _answer("dispatch", {"ok": True, "command": "workflow.events", "reply": f"{len(events)} event(s)",
                                "data": {"events": events, "next_cursor": next_cursor, "count": len(events)}})


def test_workflow_changes_keeps_its_own_cursor_and_is_silent_when_nothing_moved(monkeypatch, tmp_path):
    monkeypatch.setenv(dispatch_shim.CURSOR_FILE_ENV, str(tmp_path / "cursor.json"))
    pages = {0: _events_answer([{"cursor": 41}], 41), 41: _events_answer([], 41)}
    seen = []
    monkeypatch.setattr(door, "ask", lambda d, p, **kw: seen.append(p) or pages.get(p["after_cursor"], _events_answer([], p["after_cursor"])))
    # first call ever: adopt the tail, narrate nothing
    first = dispatch_shim.workflow_changes()
    assert first.startswith("커서 초기화 (cursor=41)") and "변화 없음" in first
    assert json.loads((tmp_path / "cursor.json").read_text())["cursor"] == 41
    # nothing new: silent, cursor untouched
    assert dispatch_shim.workflow_changes() == "변화 없음 (cursor=41)."
    assert seen[-1] == {"command": "workflow.events", "after_cursor": 41}
    # something new: rendered, cursor advanced
    pages[41] = _events_answer([{"cursor": 42, "entity": "workflow", "to_status": "COMPLETED"}], 42)
    out = dispatch_shim.workflow_changes("5")
    assert out.startswith("1 event(s)") and "cursor 41 → 42" in out and '"next_cursor":42' in out
    assert "pushed to Thomas by the runtime operator" in out
    assert seen[-1] == {"command": "workflow.events", "after_cursor": 41, "limit": 5}
    assert json.loads((tmp_path / "cursor.json").read_text())["cursor"] == 42
    # a door that is down: rendered as such, cursor untouched
    monkeypatch.setattr(door, "ask", lambda d, p, **kw: _answer("dispatch", failure=door.NO_SOCKET))
    assert dispatch_shim.workflow_changes().startswith("UNAVAILABLE: no dispatch door")
    assert json.loads((tmp_path / "cursor.json").read_text())["cursor"] == 42


def test_a_corrupt_cursor_file_adopts_the_tail_instead_of_replaying_history(monkeypatch, tmp_path):
    monkeypatch.setenv(dispatch_shim.CURSOR_FILE_ENV, str(tmp_path / "cursor.json"))
    (tmp_path / "cursor.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(door, "ask", lambda d, p, **kw: _events_answer([{"cursor": 9}], 9) if p["after_cursor"] == 0 else _events_answer([], 9))
    assert dispatch_shim.workflow_changes().startswith("커서 초기화 (cursor=9)")


def _state_db(path, rows):
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE session_model_usage (session_id TEXT NOT NULL, model TEXT NOT NULL,
        billing_provider TEXT NOT NULL DEFAULT '', billing_base_url TEXT NOT NULL DEFAULT '', billing_mode TEXT NOT NULL DEFAULT '',
        task TEXT NOT NULL DEFAULT '', api_call_count INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0, cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens INTEGER NOT NULL DEFAULT 0, estimated_cost_usd REAL NOT NULL DEFAULT 0, actual_cost_usd REAL NOT NULL DEFAULT 0,
        cost_status TEXT, cost_source TEXT, first_seen REAL, last_seen REAL)""")
    conn.executemany("INSERT INTO session_model_usage (session_id, model, input_tokens, output_tokens, estimated_cost_usd,"
                     " actual_cost_usd, cost_status, last_seen) VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit(); conn.close()


def test_report_workflow_usage_reads_this_containers_accounting_and_sends_the_reported_layer(monkeypatch, tmp_path):
    db = tmp_path / "state.db"
    _state_db(db, [
        ("cron_b8_20260914_080024", "qwen/qwen3.7-flash", 62456, 1816, 0.002393, 0.0, "estimated", 1789372870.0),
        ("cron_b8_20260914_080024", "google/gemini-2.5-pro", 10000, 500, 0.01, 0.0, "estimated", 1789372900.0),
        ("other_session", "qwen/qwen3.7-flash", 999, 99, 0.001, 0.0, "estimated", 1789372950.0),
    ])
    monkeypatch.setenv(dispatch_shim.STATE_DB_ENV, str(db))
    seen = []
    monkeypatch.setattr(door, "ask", lambda d, p, **kw: seen.append(p) or _answer(
        "dispatch", {"ok": True, "command": "workflow.report_usage", "reply": "USAGE REPORTED", "data": {"budget": {}}}))
    out = dispatch_shim.report_workflow_usage(_WID, session_id="cron_b8_20260914_080024")
    assert out.startswith("USAGE REPORTED")
    (payload,) = seen
    usage = payload["reported_usage"]
    assert payload["command"] == "workflow.report_usage" and payload["workflow_id"] == _WID
    assert usage["input_tokens"] == 72456 and usage["output_tokens"] == 2316 and usage["estimated_cost_usd"] == 0.012393
    assert usage["cost_status"] == "estimated" and usage["source"] == "hermes:session_model_usage:session"
    assert usage["as_of"].endswith("Z")
    # `since`: every session after the stamp — an upper bound, named as such by its source
    out = dispatch_shim.report_workflow_usage(_WID, since="2026-09-14T08:00:00Z")
    assert out.startswith("USAGE REPORTED") and seen[-1]["reported_usage"]["source"] == "hermes:session_model_usage:since"
    assert seen[-1]["reported_usage"]["input_tokens"] == 73455
    # refusals: no rows, both or neither selector, no database — nothing sent
    assert dispatch_shim.report_workflow_usage(_WID, session_id="nope").startswith("REFUSED: no usage rows")
    assert dispatch_shim.report_workflow_usage(_WID).startswith("REFUSED: give exactly one")
    assert dispatch_shim.report_workflow_usage(_WID, session_id="a", since="b").startswith("REFUSED: give exactly one")
    monkeypatch.setenv(dispatch_shim.STATE_DB_ENV, str(tmp_path / "missing.db"))
    assert dispatch_shim.report_workflow_usage(_WID, session_id="a").startswith("REFUSED: cannot read Hermes usage")
    assert not (tmp_path / "missing.db").exists() and len(seen) == 2


def test_the_reported_cost_status_follows_the_rows(monkeypatch, tmp_path):
    db = tmp_path / "state.db"
    _state_db(db, [("s1", "m", 10, 5, 0.0, 0.5, "actual", 1.0), ("s1", "m2", 10, 5, 0.0, 0.25, "actual", 2.0),
                   ("s2", "m", 10, 5, 0.0, 0.0, None, 3.0)])
    monkeypatch.setenv(dispatch_shim.STATE_DB_ENV, str(db))
    assert dispatch_shim._read_hermes_usage(session_id="s1", since=None) | {"as_of": "-"} == {
        "input_tokens": 20, "output_tokens": 10, "estimated_cost_usd": 0.75, "cost_status": "observed",
        "source": "hermes:session_model_usage:session", "as_of": "-"}
    assert dispatch_shim._read_hermes_usage(session_id="s2", since=None)["cost_status"] == "unmeasured"


# --- P09: schedule changes inside the delegated scope ----------------------------------------------

def test_propose_schedule_change_sends_the_closed_change_and_refuses_locally_what_the_door_would(monkeypatch):
    seen = []
    monkeypatch.setattr(door, "ask", lambda d, p, **kw: seen.append(p) or _answer(
        "dispatch", {"ok": True, "command": "schedule.propose_change", "reply": "APPLIED: created schedule schedule_1",
                     "data": {"verdict": "APPLIED"}}))
    out = dispatch_shim.propose_schedule_change("create", "주간 시장 조사", kind="analysis_task", request="조사", interval_seconds="604800")
    assert out.startswith("APPLIED: created schedule") and '"verdict":"APPLIED"' in out
    assert seen[0] == {"command": "schedule.propose_change",
                       "change": {"action": "create", "reason": "주간 시장 조사", "kind": "analysis_task", "request": "조사",
                                  "interval_seconds": 604800}}
    dispatch_shim.propose_schedule_change("disable", "더 필요 없음", schedule_id="schedule_1")
    assert seen[1] == {"command": "schedule.propose_change", "change": {"action": "disable", "reason": "더 필요 없음", "schedule_id": "schedule_1"}}
    assert dispatch_shim.propose_schedule_change("update", "r").startswith("REFUSED: action must be one of")
    assert dispatch_shim.propose_schedule_change("create", "", kind="analysis_task", interval_seconds="60").startswith("REFUSED: a reason")
    assert dispatch_shim.propose_schedule_change("create", "r", kind="", interval_seconds="60").startswith("REFUSED: kind is required")
    assert dispatch_shim.propose_schedule_change("create", "r", kind="analysis_task", interval_seconds="1h").startswith("REFUSED: interval_seconds")
    assert dispatch_shim.propose_schedule_change("remove", "r").startswith("REFUSED: schedule_id is required")
    assert len(seen) == 2
    refused = dispatch_shim._render_workflow(_answer("dispatch", {"ok": False, "reason_code": "SCHEDULE_DELEGATION_DISABLED",
                                                                  "reason": "no clause"}), command="schedule.propose_change")
    assert refused.startswith("REFUSED [SCHEDULE_DELEGATION_DISABLED]") and "Nothing was changed" in refused


def test_billed_and_estimated_rows_keep_both_parts_of_the_cost(monkeypatch, tmp_path):
    db = tmp_path / "state.db"
    _state_db(db, [("s1", "paid", 10, 5, 0.10, 0.40, "actual", 1.0), ("s1", "free", 10, 5, 0.02, 0.0, "estimated", 2.0),
                   ("s2", "free", 10, 5, 0.0, 0.0, None, 3.0)])
    monkeypatch.setenv(dispatch_shim.STATE_DB_ENV, str(db))
    mixed = dispatch_shim._read_hermes_usage(session_id="s1", since=None)
    assert mixed["estimated_cost_usd"] == 0.42 and mixed["cost_status"] == "estimated"
    assert dispatch_shim._read_hermes_usage(session_id="s2", since=None)["cost_status"] == "unmeasured"


def test_the_first_narration_walks_every_page_to_the_real_tail(monkeypatch, tmp_path):
    monkeypatch.setenv(dispatch_shim.CURSOR_FILE_ENV, str(tmp_path / "cursor.json"))
    tail = 437
    asked = []

    def paged(d, p, **kw):                                              # a door that honours after_cursor AND limit
        asked.append((p["after_cursor"], p.get("limit")))
        start = p["after_cursor"] + 1
        end = min(tail, p["after_cursor"] + int(p.get("limit") or 50))
        rows = [{"cursor": c} for c in range(start, end + 1)]
        return _events_answer(rows, rows[-1]["cursor"] if rows else p["after_cursor"])

    monkeypatch.setattr(door, "ask", paged)
    assert dispatch_shim.workflow_changes().startswith(f"커서 초기화 (cursor={tail})")
    assert json.loads((tmp_path / "cursor.json").read_text())["cursor"] == tail
    assert len(asked) >= 3                                               # more than one page was read



def test_a_mutation_sent_without_a_reply_is_unconfirmed_never_nothing_started():
    for command in ("schedule.propose_change", "workflow.cancel", "workflow.retry_step", "workflow.propose_update"):
        out = dispatch_shim._render_workflow(_answer("dispatch", failure=door.TIMEOUT_AFTER_SEND, sent=True), command=command)
        assert out.startswith("UNCONFIRMED") and "MAY HAVE BEEN APPLIED" in out and "Nothing was started" not in out
    down = dispatch_shim._render_workflow(_answer("dispatch", failure=door.NO_SOCKET), command="schedule.propose_change")
    assert down.startswith("UNAVAILABLE") and "Nothing was started" in down                # not sent: the honest answer
