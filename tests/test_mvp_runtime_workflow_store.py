"""The workflow store: one transaction accepts a plan or nothing does (sequence 2, P03).

Acceptance rows exercised here at the store level: A03 (a failure before commit leaves no row),
A04/A05 (the same request replays, a different plan under the same id conflicts, concurrent
submits accept one), A02 (dependents wait), A07 (a late result is fenced), A08 (expiry policy by
effect class), A11 (cancel is two-phase), A12 (a lapsed reservation is never refunded),
A19 (a stable event cursor), A24 (a consistent snapshot), and the Q19 read rule.
"""

from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from runtime.mvp_runtime import workflow as wf
from runtime.mvp_runtime import workflow_store as ws
from runtime.mvp_runtime.errors import PersistenceError, WorkflowBlocked

NOW = "2026-09-14T07:00:00Z"
LATER = "2026-09-14T07:05:00Z"
MUCH_LATER = "2026-09-14T07:20:00Z"


def _plan(**over):
    plan = {
        "schema_version": "workflow_plan.v0.1",
        "goal": "시장 조사",
        "steps": [{"id": "research", "capability": "research", "request": "키워드 조사", "reason": "브리핑",
                   "max_attempts": 2}],
        "budget": {"max_model_calls": 4},
    }
    plan.update(over)
    return plan


def _multi(**over):
    return _plan(steps=[
        {"id": "research", "capability": "research", "request": "조사", "reason": "r"},
        {"id": "draft1", "capability": "content", "request": "초안 1", "reason": "r",
         "depends_on": ["research"], "input_refs": ["research"]},
        {"id": "draft2", "capability": "content", "request": "초안 2", "reason": "r",
         "depends_on": ["research"], "input_refs": ["research"]},
        {"id": "review", "capability": "analysis", "request": "검토", "reason": "r",
         "depends_on": ["draft1", "draft2"], "input_refs": ["draft1", "draft2"]},
    ], budget={"max_model_calls": 10}, **over)


@pytest.fixture
def store(tmp_path):
    return ws.WorkflowStore(tmp_path)


def _submit(store, plan=None, *, request_id="hermes-1", now=NOW):
    return store.submit(principal="hermes", request_id=request_id, plan=plan or _plan(), now=now)


def _step(store, workflow_id, key):
    return next(s for s in store.status_view(workflow_id, now=NOW)["steps"] if s["key"] == key)


# --- accept, replay, conflict (A03–A05) -----------------------------------------------------

def test_submit_accepts_atomically_and_replays_the_same_request(store):
    first = _submit(store)
    assert not first.replayed and first.status == wf.W_VALIDATED and first.workflow_id.startswith("wf_")
    again = _submit(store)
    assert again.replayed and again.workflow_id == first.workflow_id
    assert store.find_request("hermes", "hermes-1")["workflow_id"] == first.workflow_id
    view = store.status_view(first.workflow_id, now=LATER)
    assert view["status"] == wf.W_VALIDATED and view["as_of"] == LATER and view["source"] == "workflow_store"
    assert [s["status"] for s in view["steps"]] == [wf.S_READY]        # no dependencies: ready at once
    events, _ = store.events_after(0)
    assert [e["to_status"] for e in events if e["entity"] == "workflow"] == [wf.W_RECEIVED, wf.W_VALIDATED]


def test_the_same_id_with_a_different_plan_is_a_conflict_not_a_second_run(store):
    first = _submit(store)
    with pytest.raises(WorkflowBlocked) as exc:
        _submit(store, _plan(goal="다른 목표"))
    assert exc.value.reason_code == "REQUEST_ID_CONFLICT"
    assert len(store.list_workflows()) == 1 and store.list_workflows()[0]["workflow_id"] == first.workflow_id


def test_a_refused_plan_leaves_no_row(store):
    with pytest.raises(WorkflowBlocked):
        _submit(store, _plan(steps=[{"id": "a", "capability": "analysis", "request": "x", "reason": "r",
                                     "depends_on": ["ghost"]}]))
    assert store.list_workflows() == [] and store.find_request("hermes", "hermes-1") is None
    assert store.events_after(0) == ([], 0)


def test_a_failure_before_commit_leaves_nothing_and_never_says_accepted(store, monkeypatch):
    """A03: the last insert of the transaction fails; the request mapping written before it must
    not survive — a replay of the same id would otherwise find an accepted workflow that has no
    steps."""
    real = wf.event_record
    calls = {"n": 0}

    def boom(**fields):
        calls["n"] += 1
        if fields.get("entity") == "step" and fields.get("to_status") == wf.S_READY:
            raise RuntimeError("disk full")
        return real(**fields)

    monkeypatch.setattr(wf, "event_record", boom)
    with pytest.raises(RuntimeError):
        _submit(store)
    assert calls["n"] >= 3
    assert store.list_workflows() == [] and store.find_request("hermes", "hermes-1") is None
    assert store.events_after(0) == ([], 0)
    monkeypatch.setattr(wf, "event_record", real)
    assert not _submit(store).replayed                     # the id was never burned


def test_concurrent_submits_of_one_request_accept_exactly_one(store):
    outcomes: list[ws.SubmitOutcome] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(4)

    def run():
        try:
            barrier.wait(5)
            outcomes.append(_submit(store))
        except BaseException as exc:  # noqa: BLE001 — collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors and len(outcomes) == 4
    assert len({o.workflow_id for o in outcomes}) == 1
    assert sum(1 for o in outcomes if not o.replayed) == 1
    assert len(store.list_workflows()) == 1


def test_missing_principal_or_request_id_is_refused_before_validation(store):
    with pytest.raises(WorkflowBlocked) as exc:
        store.submit(principal=" ", request_id="r", plan=_plan(), now=NOW)
    assert exc.value.reason_code == "MISSING_PRINCIPAL"
    with pytest.raises(WorkflowBlocked) as exc:
        store.submit(principal="hermes", request_id="", plan=_plan(), now=NOW)
    assert exc.value.reason_code == "MISSING_REQUEST_ID"


def test_the_server_ceiling_on_open_steps_refuses_the_plan_that_would_cross_it(store):
    ten = [{"id": f"s{i}", "capability": "analysis", "request": "x", "reason": "r"} for i in range(10)]
    _submit(store, _plan(steps=ten, budget={"max_model_calls": 20}), request_id="a")
    _submit(store, _plan(steps=ten, budget={"max_model_calls": 20}), request_id="b")
    assert store.open_step_count() == 20
    with pytest.raises(WorkflowBlocked) as exc:
        _submit(store, request_id="c")
    assert exc.value.reason_code == "CAPACITY_EXHAUSTED"


# --- claim, dependencies, results (A02) -----------------------------------------------------

def test_claim_opens_an_attempt_with_a_lease_and_reserves_the_budget(store):
    wid = _submit(store).workflow_id
    (claimed,) = store.claim_ready(now=NOW)
    assert claimed.workflow_id == wid and claimed.step_key == "research" and claimed.attempt_number == 1
    assert claimed.capability == "research" and claimed.effect_class == wf.EFFECT_NONE
    assert claimed.deadline_at == "2026-09-14T07:11:00Z" and claimed.input_refs == {}
    view = store.status_view(wid, now=NOW)
    assert view["status"] == wf.W_RUNNING
    step = view["steps"][0]
    assert step["status"] == wf.S_RUNNING and step["current_attempt_id"] == claimed.attempt_id
    assert view["budget"]["reserved_model_calls"] == 1 and view["budget"]["remaining_model_calls"] == 3
    assert store.claim_ready(now=NOW) == []                # nothing else is READY


def test_dependents_wait_for_every_dependency_and_receive_the_results_they_named(store):
    wid = _submit(store, _multi()).workflow_id
    assert [s["status"] for s in store.status_view(wid, now=NOW)["steps"]] == [wf.S_READY, wf.S_PENDING, wf.S_PENDING, wf.S_PENDING]
    (research,) = store.claim_ready(now=NOW, limit=5)
    store.record_result(research.attempt_id, now=LATER, succeeded=True, trace_id="trace_r",
                        result_ref="ledger:trace_r", model_calls=1, tokens=1200)
    drafts = store.claim_ready(now=LATER, limit=5)
    assert sorted(d.step_key for d in drafts) == ["draft1", "draft2"]
    assert all(d.input_refs == {"research": "ledger:trace_r"} for d in drafts)
    assert _step(store, wid, "review")["status"] == wf.S_PENDING
    store.record_result(drafts[0].attempt_id, now=LATER, succeeded=True, result_ref="ledger:d1", model_calls=1)
    assert _step(store, wid, "review")["status"] == wf.S_PENDING       # one of two dependencies
    store.record_result(drafts[1].attempt_id, now=LATER, succeeded=True, result_ref="ledger:d2", model_calls=1)
    assert _step(store, wid, "review")["status"] == wf.S_READY
    (review,) = store.claim_ready(now=LATER)
    assert review.input_refs == {"draft1": "ledger:d1", "draft2": "ledger:d2"}
    store.record_result(review.attempt_id, now=MUCH_LATER, succeeded=True, result_ref="ledger:rv", model_calls=1)
    view = store.status_view(wid, now=MUCH_LATER)
    assert view["status"] == wf.W_COMPLETED and all(s["status"] == wf.S_SUCCEEDED for s in view["steps"])
    assert view["budget"]["confirmed_model_calls"] == 4 and view["budget"]["confirmed_tokens"] == 1200


def test_a_failed_dependency_blocks_everything_downstream_and_the_workflow_waits_for_a_decision(store):
    wid = _submit(store, _multi()).workflow_id
    (research,) = store.claim_ready(now=NOW)
    out = store.record_result(research.attempt_id, now=LATER, succeeded=False, reason_code="PROVIDER_UNAVAILABLE")
    assert out["step_status"] == wf.S_FAILED and out["workflow_status"] == wf.W_WAITING_REPLAN
    assert store.status_view(wid, now=LATER)["last_reason_code"] == "PROVIDER_UNAVAILABLE"
    statuses = {s["key"]: (s["status"], s["last_reason_code"]) for s in store.status_view(wid, now=LATER)["steps"]}
    assert statuses["research"] == (wf.S_FAILED, "PROVIDER_UNAVAILABLE")
    assert statuses["draft1"] == statuses["draft2"] == statuses["review"] == (wf.S_BLOCKED, "DEPENDENCY_FAILED")


def test_a_failure_retries_while_attempts_remain_and_then_fails(store):
    wid = _submit(store).workflow_id                        # max_attempts 2
    (first,) = store.claim_ready(now=NOW)
    out = store.record_result(first.attempt_id, now=LATER, succeeded=False, reason_code="TIMEOUT")
    assert out["step_status"] == wf.S_READY and out["workflow_status"] == wf.W_RUNNING
    (second,) = store.claim_ready(now=LATER)
    assert second.attempt_number == 2 and second.attempt_id != first.attempt_id
    out = store.record_result(second.attempt_id, now=MUCH_LATER, succeeded=False, reason_code="TIMEOUT")
    assert out["step_status"] == wf.S_FAILED and out["workflow_status"] == wf.W_WAITING_REPLAN   # 2 < the cap of 3
    assert store.status_view(wid, now=MUCH_LATER)["last_reason_code"] == "TIMEOUT"


def test_a_step_at_the_hard_attempt_cap_ends_the_workflow_failed(store):
    wid = _submit(store, _plan(steps=[{"id": "research", "capability": "research", "request": "x", "reason": "r",
                                       "max_attempts": 3}], budget={"max_model_calls": 3})).workflow_id
    for stamp in (NOW, LATER, MUCH_LATER):
        (a,) = store.claim_ready(now=stamp)
        out = store.record_result(a.attempt_id, now=stamp, succeeded=False, reason_code="X")
    assert out["workflow_status"] == wf.W_FAILED
    with pytest.raises(WorkflowBlocked) as exc:
        store.retry_step(wid, "research", expected_version=store.status_view(wid, now=NOW)["row_version"], reason="again", now=NOW)
    assert exc.value.reason_code == "WORKFLOW_TERMINAL"


# --- the fence and the lease (A07, A08, A12) -------------------------------------------------

def test_a_late_result_from_a_lapsed_attempt_is_recorded_and_not_applied(store):
    wid = _submit(store).workflow_id
    (first,) = store.claim_ready(now=NOW)
    expired = store.expire_overdue(now=MUCH_LATER)          # 20 min > the 11 min lease
    assert [e["attempt_id"] for e in expired] == [first.attempt_id]
    assert _step(store, wid, "research")["status"] == wf.S_READY   # effect none, one attempt left
    (second,) = store.claim_ready(now=MUCH_LATER)
    with pytest.raises(WorkflowBlocked) as exc:
        store.record_result(first.attempt_id, now=MUCH_LATER, succeeded=True, result_ref="ledger:late")
    assert exc.value.reason_code == "ATTEMPT_FENCED"
    step = _step(store, wid, "research")
    assert step["status"] == wf.S_RUNNING and step["current_attempt_id"] == second.attempt_id and step["result_ref"] is None
    events, _ = store.events_after(0)
    late = [e for e in events if e["reason_code"] == "LATE_RESULT"]
    assert len(late) == 1 and late[0]["attempt_id"] == first.attempt_id
    store.record_result(second.attempt_id, now=MUCH_LATER, succeeded=True, result_ref="ledger:ok", model_calls=1)
    assert _step(store, wid, "research")["result_ref"] == "ledger:ok"


def test_expiry_policy_follows_the_effect_class(store, monkeypatch):
    # none, attempts remaining -> READY again (above). none, exhausted -> FAILED:
    wid = _submit(store, _plan(steps=[{"id": "research", "capability": "research", "request": "x", "reason": "r",
                                       "max_attempts": 1}])).workflow_id
    (a,) = store.claim_ready(now=NOW)
    store.expire_overdue(now=MUCH_LATER)
    assert _step(store, wid, "research")["status"] == wf.S_FAILED
    assert store.status_view(wid, now=MUCH_LATER)["status"] == wf.W_WAITING_REPLAN   # one attempt of three: a decision may retry
    # external -> NEEDS_RECONCILIATION, never a silent retry:
    monkeypatch.setitem(wf.CAPABILITIES, "research", wf.Capability("research", wf.EFFECT_EXTERNAL))
    wid2 = _submit(store, request_id="hermes-2").workflow_id
    (b,) = store.claim_ready(now=NOW)
    assert b.effect_class == wf.EFFECT_EXTERNAL
    store.expire_overdue(now=MUCH_LATER)
    step = _step(store, wid2, "research")
    assert step["status"] == wf.S_NEEDS_RECONCILIATION and step["last_reason_code"] == "ATTEMPT_EXPIRED"
    assert store.claim_ready(now=MUCH_LATER) == []          # nothing is re-dispatched on its own


def test_a_lapsed_reservation_is_never_refunded_and_exhaustion_blocks(store):
    wid = _submit(store, _plan(budget={"max_model_calls": 2},
                               steps=[{"id": "research", "capability": "research", "request": "x", "reason": "r",
                                       "max_attempts": 3}])).workflow_id
    (a,) = store.claim_ready(now=NOW)
    store.expire_overdue(now=MUCH_LATER)
    budget = store.budget_summary(wid)
    assert budget["reserved_model_calls"] == 1 and budget["unconfirmed_model_calls"] == 1 and budget["remaining_model_calls"] == 1
    (b,) = store.claim_ready(now=MUCH_LATER)
    store.expire_overdue(now="2026-09-14T08:00:00Z")
    assert store.budget_summary(wid)["remaining_model_calls"] == 0
    assert store.claim_ready(now="2026-09-14T08:00:00Z") == []
    step = _step(store, wid, "research")
    assert step["status"] == wf.S_BLOCKED and step["last_reason_code"] == wf.BUDGET_EXHAUSTED
    assert store.status_view(wid, now=NOW)["status"] == wf.W_WAITING_REPLAN   # a budget decision could still move it
    with pytest.raises(WorkflowBlocked) as exc:
        store.retry_step(wid, "research", expected_version=store.status_view(wid, now=NOW)["row_version"], reason="r", now=NOW)
    assert exc.value.reason_code == wf.BUDGET_EXHAUSTED


def test_an_unconfirmed_success_keeps_its_reservation_unconfirmed(store):
    wid = _submit(store).workflow_id
    (a,) = store.claim_ready(now=NOW)
    store.record_result(a.attempt_id, now=LATER, succeeded=True, result_ref="ledger:x")   # no usage reported
    budget = store.budget_summary(wid)
    assert budget["unconfirmed_model_calls"] == 1 and budget["confirmed_model_calls"] == 0


# --- cancel (A11) ----------------------------------------------------------------------------

def test_cancel_marks_waiting_steps_now_and_a_running_step_only_when_it_stops(store):
    wid = _submit(store, _multi()).workflow_id
    (research,) = store.claim_ready(now=NOW)
    version = store.status_view(wid, now=NOW)["row_version"]
    view = store.request_cancel(wid, expected_version=version, reason="사용자 취소", now=LATER)
    statuses = {s["key"]: s["status"] for s in view["steps"]}
    assert view["status"] == wf.W_CANCELLING and statuses["research"] == wf.S_CANCEL_REQUESTED
    assert statuses["draft1"] == statuses["draft2"] == statuses["review"] == wf.S_CANCELLED
    assert store.claim_ready(now=LATER) == []
    view = store.confirm_cancelled(research.attempt_id, now=MUCH_LATER)
    assert view["status"] == wf.W_CANCELLED and all(s["status"] == wf.S_CANCELLED for s in view["steps"])
    assert store.attempt(research.attempt_id)["status"] == wf.A_CANCELLED


def test_a_result_that_lands_during_cancellation_is_honoured_and_the_workflow_still_ends_cancelled(store):
    wid = _submit(store, _multi()).workflow_id
    (research,) = store.claim_ready(now=NOW)
    version = store.status_view(wid, now=NOW)["row_version"]
    store.request_cancel(wid, expected_version=version, reason="x", now=LATER)
    out = store.record_result(research.attempt_id, now=MUCH_LATER, succeeded=True, result_ref="ledger:r", model_calls=1)
    assert out["step_status"] == wf.S_SUCCEEDED and out["workflow_status"] == wf.W_CANCELLED


def test_cancel_needs_the_version_the_caller_saw_and_refuses_a_finished_workflow(store):
    wid = _submit(store).workflow_id
    version = store.status_view(wid, now=NOW)["row_version"]
    with pytest.raises(WorkflowBlocked) as exc:
        store.request_cancel(wid, expected_version=version + 5, reason="x", now=LATER)
    assert exc.value.reason_code == "VERSION_CONFLICT"
    view = store.request_cancel(wid, expected_version=version, reason="x", now=LATER)
    assert view["status"] == wf.W_CANCELLED and view["cancel_reason"] == "x"
    with pytest.raises(WorkflowBlocked) as exc:
        store.request_cancel(wid, expected_version=view["row_version"], reason="again", now=LATER)
    assert exc.value.reason_code == "WORKFLOW_TERMINAL"
    with pytest.raises(WorkflowBlocked) as exc:
        store.request_cancel("wf_" + "0" * 20, expected_version=1, reason="x", now=LATER)
    assert exc.value.reason_code == "WORKFLOW_NOT_FOUND"


# --- events, reads, snapshot (A19, A24, Q19) -------------------------------------------------

def test_the_event_cursor_is_stable_and_resumable(store):
    wid = _submit(store, _multi()).workflow_id
    page1, next1 = store.events_after(0, limit=3)
    page1_again, _ = store.events_after(0, limit=3)
    assert page1 == page1_again and len(page1) == 3 and next1 == page1[-1]["cursor"]
    page2, next2 = store.events_after(next1, limit=100)
    assert page2 and page2[0]["cursor"] == next1 + 1 and next2 >= next1
    assert all(e["workflow_id"] == wid for e in page1 + page2)
    assert store.events_after(next2, limit=10) == ([], next2)


def test_every_event_row_satisfies_the_closed_schema(store):
    wid = _submit(store, _multi()).workflow_id
    (research,) = store.claim_ready(now=NOW)
    store.record_result(research.attempt_id, now=LATER, succeeded=False, reason_code="X")
    events, _ = store.events_after(0, limit=500)
    assert len(events) > 8
    for e in events:
        wf.event_record(workflow_id=e["workflow_id"], step_id=e["step_id"], attempt_id=e["attempt_id"],
                        entity=e["entity"], from_status=e["from_status"], to_status=e["to_status"],
                        reason_code=e["reason_code"], detail=e["detail"], created_at=e["created_at"])


def test_a_read_only_handle_reads_and_refuses_writes(tmp_path):
    with pytest.raises(PersistenceError) as exc:
        ws.WorkflowStore(tmp_path, readonly=True).list_workflows()
    assert exc.value.reason_code == "WORKFLOW_STORE_UNAVAILABLE"
    writer = ws.WorkflowStore(tmp_path)
    wid = _submit(writer).workflow_id
    reader = ws.WorkflowStore(tmp_path, readonly=True)
    assert reader.status_view(wid, now=NOW)["workflow_id"] == wid
    with pytest.raises(WorkflowBlocked) as exc:
        _submit(reader, request_id="other")
    assert exc.value.reason_code == "STORE_READ_ONLY"
    # WAL is the journal mode the read rule is written for
    conn = sqlite3.connect(str(writer.path))
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


def test_a_snapshot_is_a_consistent_copy_with_a_manifest(store, tmp_path):
    wid = _submit(store, _multi()).workflow_id
    (research,) = store.claim_ready(now=NOW)
    target = store.snapshot(tmp_path / "snap", now=LATER)
    assert target.is_file() and target.name.startswith("workflow-")
    copy = sqlite3.connect(str(target))
    assert copy.execute("SELECT COUNT(*) FROM workflows").fetchone()[0] == 1
    assert copy.execute("SELECT status FROM steps WHERE step_key='research'").fetchone()[0] == wf.S_RUNNING
    copied_events = copy.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    copy.close()
    manifest = json.loads((tmp_path / "snap" / target.name.replace(".db", ".manifest.json")).read_text())
    assert manifest["schema_version"] == ws.SCHEMA_VERSION and manifest["max_event_cursor"] == copied_events
    # the live store keeps working after the copy
    store.record_result(research.attempt_id, now=MUCH_LATER, succeeded=True, result_ref="ledger:r", model_calls=1)
    assert _step(store, wid, "research")["status"] == wf.S_SUCCEEDED


def test_the_worker_frame_carries_what_the_worker_must_echo(store):
    wid = _submit(store, _plan(steps=[{"id": "research", "capability": "research", "request": "x", "reason": "why",
                                       "naver_keywords": "사장님, 마케팅",
                                       "options": {"independent_validation": True, "revise": True}}],
                               budget={"max_model_calls": 6})).workflow_id
    (claimed,) = store.claim_ready(now=NOW)
    assert claimed.attempt_id.startswith("wfa_") and claimed.step_id.startswith("wfs_")
    assert claimed.options == {"independent_validation": True, "revise": True}
    assert claimed.naver_keywords == "사장님, 마케팅" and claimed.reason == "why"
    assert store.budget_summary(wid)["reserved_model_calls"] == 3


# --- retry by decision (P06; acceptance A16) ---------------------------------------------------

def test_retry_step_re_opens_one_failed_step_and_nothing_already_delivered_runs_again(store):
    """Research succeeds, draft1 fails (one attempt), draft2 succeeds: the workflow waits for a
    decision with review blocked behind draft1. A retry re-opens draft1 only; review follows."""
    plan = _multi()
    plan["steps"][1]["max_attempts"] = 1
    wid = _submit(store, plan).workflow_id
    (research,) = store.claim_ready(now=NOW)
    store.record_result(research.attempt_id, now=NOW, succeeded=True, result_ref="ledger:r", model_calls=1)
    drafts = {d.step_key: d for d in store.claim_ready(now=NOW, limit=5)}
    store.record_result(drafts["draft1"].attempt_id, now=LATER, succeeded=False, reason_code="PROVIDER_UNAVAILABLE")
    assert store.status_view(wid, now=LATER)["status"] == wf.W_RUNNING              # draft2 still running
    store.record_result(drafts["draft2"].attempt_id, now=LATER, succeeded=True, result_ref="ledger:d2", model_calls=1)
    view = store.status_view(wid, now=LATER)
    statuses = {s["key"]: s["status"] for s in view["steps"]}
    assert view["status"] == wf.W_WAITING_REPLAN
    assert statuses == {"research": wf.S_SUCCEEDED, "draft1": wf.S_FAILED, "draft2": wf.S_SUCCEEDED, "review": wf.S_BLOCKED}
    with pytest.raises(WorkflowBlocked) as exc:
        store.retry_step(wid, "review", expected_version=view["row_version"], reason="r", now=LATER)
    assert exc.value.reason_code == "RETRY_NOT_APPLICABLE"                            # blocked by draft1, not its own failure
    with pytest.raises(WorkflowBlocked) as exc:
        store.retry_step(wid, "draft1", expected_version=view["row_version"] + 1, reason="r", now=LATER)
    assert exc.value.reason_code == "VERSION_CONFLICT"
    with pytest.raises(WorkflowBlocked) as exc:
        store.retry_step(wid, "research", expected_version=view["row_version"], reason="r", now=LATER)
    assert exc.value.reason_code == "RETRY_NOT_APPLICABLE"                            # succeeded: never re-run
    view = store.retry_step(wid, "draft1", expected_version=view["row_version"], reason="공급자 복구됨", now=MUCH_LATER)
    statuses = {s["key"]: s["status"] for s in view["steps"]}
    assert view["status"] == wf.W_RUNNING
    assert statuses == {"research": wf.S_SUCCEEDED, "draft1": wf.S_READY, "draft2": wf.S_SUCCEEDED, "review": wf.S_PENDING}
    (retry,) = store.claim_ready(now=MUCH_LATER, limit=5)                            # only draft1 is dispatched
    assert retry.step_key == "draft1" and retry.attempt_number == 2 and retry.input_refs == {"research": "ledger:r"}
    store.record_result(retry.attempt_id, now=MUCH_LATER, succeeded=True, result_ref="ledger:d1", model_calls=1)
    (review,) = store.claim_ready(now=MUCH_LATER, limit=5)
    assert review.step_key == "review" and review.input_refs == {"draft1": "ledger:d1", "draft2": "ledger:d2"}
    store.record_result(review.attempt_id, now=MUCH_LATER, succeeded=True, result_ref="ledger:rv", model_calls=1)
    final = store.status_view(wid, now=MUCH_LATER)
    assert final["status"] == wf.W_COMPLETED
    assert {s["key"]: s["attempts_opened"] for s in final["steps"]} == {"research": 1, "draft1": 2, "draft2": 1, "review": 1}
    events, _ = store.events_after(0, limit=500)
    assert [e for e in events if e["reason_code"] == "RETRY_REQUESTED"][0]["detail"] == "공급자 복구됨"


def test_a_reconciling_step_can_be_retried_or_cancelled_by_decision(store, monkeypatch):
    monkeypatch.setitem(wf.CAPABILITIES, "research", wf.Capability("research", wf.EFFECT_EXTERNAL))
    wid = _submit(store).workflow_id
    (a,) = store.claim_ready(now=NOW)
    store.expire_overdue(now=MUCH_LATER)
    view = store.status_view(wid, now=MUCH_LATER)
    assert view["status"] == wf.W_WAITING_REPLAN and view["steps"][0]["status"] == wf.S_NEEDS_RECONCILIATION
    view = store.retry_step(wid, "research", expected_version=view["row_version"], reason="확인함: 효과 없음", now=MUCH_LATER)
    assert view["status"] == wf.W_RUNNING and view["steps"][0]["status"] == wf.S_READY
    view = store.request_cancel(wid, expected_version=view["row_version"], reason="그만", now=MUCH_LATER)
    assert view["status"] == wf.W_CANCELLED


# --- gated steps (P07; acceptance A13 at the store) ----------------------------------------------

def _gated(**over):
    plan = _plan(steps=[{"id": "research", "capability": "research", "request": "조사", "reason": "r"},
                        {"id": "publish_draft", "capability": "content", "request": "초안", "reason": "r",
                         "depends_on": ["research"], "requires_approval": True}],
                 budget={"max_model_calls": 6})
    plan.update(over)
    return plan


def test_a_gated_step_waits_for_approval_instead_of_becoming_ready(store):
    wid = _submit(store, _gated()).workflow_id
    (research,) = store.claim_ready(now=NOW)
    store.record_result(research.attempt_id, now=NOW, succeeded=True, result_ref="ledger:r", model_calls=1)
    view = store.status_view(wid, now=NOW)
    gated = _step(store, wid, "publish_draft")
    assert gated["status"] == wf.S_WAITING_APPROVAL and gated["requires_approval"] is True and gated["approval_id"] is None
    assert view["status"] == wf.W_WAITING_APPROVAL
    assert store.claim_ready(now=NOW) == []                                   # the manager never claims it
    (waiting,) = store.waiting_approval_steps()
    assert waiting["step_key"] == "publish_draft" and waiting["workflow_plan_version"] == 1 and waiting["goal"] == "시장 조사"
    # an independent gated step waits from the moment it is accepted
    wid2 = _submit(store, _plan(steps=[{"id": "a", "capability": "analysis", "request": "x", "reason": "r",
                                        "requires_approval": True}]), request_id="hermes-2").workflow_id
    assert _step(store, wid2, "a")["status"] == wf.S_WAITING_APPROVAL


def test_a_plan_cannot_carry_an_approval_only_ask_for_one():
    for forged in ({"approval_id": "approval_x"}, {"approved": True}, {"approval": {"status": "APPROVED"}}):
        with pytest.raises(WorkflowBlocked) as exc:
            wf.validate_plan(_plan(steps=[{"id": "a", "capability": "analysis", "request": "x", "reason": "r", **forged}]))
        assert exc.value.reason_code == "PLAN_INVALID"


def test_the_bound_grant_releases_the_step_and_anything_else_blocks_it_for_a_decision(store):
    wid = _submit(store, _plan(steps=[{"id": "a", "capability": "analysis", "request": "x", "reason": "r",
                                       "requires_approval": True}])).workflow_id
    (step,) = store.status_view(wid, now=NOW)["steps"]
    store.bind_approval(step["step_id"], approval_id="approval_1", plan_version=1, now=NOW)
    assert _step(store, wid, "a")["approval_id"] == "approval_1"
    with pytest.raises(WorkflowBlocked) as exc:
        store.approve_step(step["step_id"], approval_id="approval_other", now=NOW)
    assert exc.value.reason_code == "APPROVAL_NOT_BOUND"
    view = store.approve_step(step["step_id"], approval_id="approval_1", now=LATER)
    assert view["status"] == wf.W_RUNNING and view["steps"][0]["status"] == wf.S_READY
    (claimed,) = store.claim_ready(now=LATER)
    assert claimed.step_key == "a"
    # a refused grant blocks the step and the workflow waits for a decision; a retry asks again
    wid2 = _submit(store, _plan(steps=[{"id": "b", "capability": "analysis", "request": "y", "reason": "r",
                                        "requires_approval": True}]), request_id="hermes-2").workflow_id
    (b,) = store.status_view(wid2, now=NOW)["steps"]
    store.bind_approval(b["step_id"], approval_id="approval_2", plan_version=1, now=NOW)
    view = store.refuse_step_approval(b["step_id"], reason_code=wf.APPROVAL_EXPIRED, detail="expired", now=LATER)
    assert view["status"] == wf.W_WAITING_REPLAN and view["steps"][0]["status"] == wf.S_BLOCKED
    assert view["steps"][0]["last_reason_code"] == wf.APPROVAL_EXPIRED
    view = store.retry_step(wid2, "b", expected_version=view["row_version"], reason="다시 요청", now=LATER)
    assert view["steps"][0]["status"] == wf.S_WAITING_APPROVAL and view["steps"][0]["approval_id"] is None
    assert view["status"] == wf.W_WAITING_APPROVAL


# --- plan versions (P07) ----------------------------------------------------------------------------

def test_a_new_version_changes_unstarted_steps_adds_and_drops_them_and_leaves_delivered_ones_alone(store):
    wid = _submit(store, _multi()).workflow_id
    (research,) = store.claim_ready(now=NOW)
    store.record_result(research.attempt_id, now=NOW, succeeded=True, result_ref="ledger:r", model_calls=1)
    v1 = store.status_view(wid, now=NOW)
    assert v1["plan_version"] == 1
    plan = _multi()
    plan["goal"] = "시장 조사 (수정)"
    plan["steps"][1]["request"] = "초안 1 — 톤 변경"                       # draft1: not started, may change
    plan["steps"] = [s for s in plan["steps"] if s["id"] != "draft2"]      # draft2 dropped
    plan["steps"][-1]["depends_on"] = ["draft1"]; plan["steps"][-1]["input_refs"] = ["draft1"]
    plan["steps"].append({"id": "summary", "capability": "analysis", "request": "요약", "reason": "r",
                          "depends_on": ["review"], "input_refs": ["review"]})
    plan["budget"] = {"max_model_calls": 12}
    view = store.propose_update(wid, expected_version=v1["row_version"], plan=plan, reason="초안 하나로 줄이고 요약 추가", now=LATER)
    assert view["plan_version"] == 2 and view["goal"] == "시장 조사 (수정)" and view["budget"]["max_model_calls"] == 12
    statuses = {s["key"]: s["status"] for s in view["steps"]}
    assert statuses == {"research": wf.S_SUCCEEDED, "draft1": wf.S_READY, "draft2": wf.S_CANCELLED,
                        "review": wf.S_PENDING, "summary": wf.S_PENDING}
    assert next(s for s in view["steps"] if s["key"] == "draft2")["last_reason_code"] == wf.PLAN_UPDATED
    assert next(s for s in view["steps"] if s["key"] == "review")["depends_on"] == ["draft1"]
    (draft1,) = store.claim_ready(now=LATER)
    assert draft1.request == "초안 1 — 톤 변경" and draft1.input_refs == {"research": "ledger:r"}
    events, _ = store.events_after(0, limit=500)
    assert any(e["reason_code"] == wf.PLAN_UPDATED and "plan v2" in (e["detail"] or "") for e in events)


def test_a_new_version_cannot_touch_a_running_or_delivered_step_or_starve_the_reservation(store):
    wid = _submit(store, _multi()).workflow_id
    (research,) = store.claim_ready(now=NOW)
    version = store.status_view(wid, now=NOW)["row_version"]
    running_changed = _multi(); running_changed["steps"][0]["request"] = "다른 조사"
    with pytest.raises(WorkflowBlocked) as exc:
        store.propose_update(wid, expected_version=version, plan=running_changed, reason="r", now=LATER)
    assert exc.value.reason_code == "PLAN_CONFLICT" and "research" in exc.value.reason
    dropped = _multi(); dropped["steps"] = dropped["steps"][1:]
    for s in dropped["steps"]:
        s["depends_on"] = [d for d in s.get("depends_on", []) if d != "research"]
        s["input_refs"] = [d for d in s.get("input_refs", []) if d != "research"]
    with pytest.raises(WorkflowBlocked) as exc:
        store.propose_update(wid, expected_version=version, plan=dropped, reason="r", now=LATER)
    assert exc.value.reason_code == "PLAN_CONFLICT"
    starved = _multi(); starved["budget"] = {"max_model_calls": 5}
    store.record_result(research.attempt_id, now=NOW, succeeded=True, result_ref="ledger:r", model_calls=1)
    version = store.status_view(wid, now=NOW)["row_version"]
    with pytest.raises(WorkflowBlocked) as exc:
        store.propose_update(wid, expected_version=version + 1, plan=_multi(), reason="r", now=LATER)
    assert exc.value.reason_code == "VERSION_CONFLICT"
    view = store.propose_update(wid, expected_version=version, plan=starved, reason="r", now=LATER)   # 5 >= 1 reserved
    assert view["plan_version"] == 2 and view["steps"][0]["status"] == wf.S_SUCCEEDED


def test_a_new_version_re_asks_a_changed_gated_step_and_releases_a_budget_blocked_one(store):
    wid = _submit(store, _plan(steps=[{"id": "a", "capability": "analysis", "request": "x", "reason": "r",
                                       "requires_approval": True}], budget={"max_model_calls": 1})).workflow_id
    (a,) = store.status_view(wid, now=NOW)["steps"]
    store.bind_approval(a["step_id"], approval_id="approval_v1", plan_version=1, now=NOW)
    changed = _plan(steps=[{"id": "a", "capability": "analysis", "request": "x (수정)", "reason": "r",
                            "requires_approval": True}], budget={"max_model_calls": 1})
    view = store.propose_update(wid, expected_version=store.status_view(wid, now=NOW)["row_version"],
                                plan=changed, reason="요청 수정", now=LATER)
    a2 = view["steps"][0]
    assert a2["status"] == wf.S_WAITING_APPROVAL and a2["approval_id"] is None and a2["approval_plan_version"] is None
    (waiting,) = store.waiting_approval_steps()
    assert waiting["workflow_plan_version"] == 2
    # the gate can be lifted by a new version: the step becomes READY
    lifted = _plan(steps=[{"id": "a", "capability": "analysis", "request": "x (수정)", "reason": "r"}], budget={"max_model_calls": 1})
    view = store.propose_update(wid, expected_version=view["row_version"], plan=lifted, reason="게이트 해제", now=LATER)
    assert view["steps"][0]["status"] == wf.S_READY and view["status"] == wf.W_RUNNING
    # budget: a second step the budget cannot cover is blocked; a bigger budget releases it
    two = _plan(steps=[{"id": "a", "capability": "analysis", "request": "x", "reason": "r", "max_attempts": 2},
                       {"id": "b", "capability": "analysis", "request": "y", "reason": "r", "depends_on": ["a"]}],
                budget={"max_model_calls": 2})
    wid2 = _submit(store, two, request_id="hermes-2").workflow_id
    (a1,) = store.claim_ready(now=LATER)
    store.record_result(a1.attempt_id, now=LATER, succeeded=False, reason_code="PROVIDER_UNAVAILABLE", model_calls=1)
    (a2,) = store.claim_ready(now=LATER)                                     # the retry reserves the second call
    assert a2.step_key == "a" and a2.attempt_number == 2
    store.record_result(a2.attempt_id, now=LATER, succeeded=True, result_ref="ledger:a", model_calls=1)
    assert store.claim_ready(now=LATER) == []                                # b: 2 reserved, 0 remaining
    assert _step(store, wid2, "b")["status"] == wf.S_BLOCKED and store.status_view(wid2, now=NOW)["status"] == wf.W_WAITING_REPLAN
    bigger = dict(two); bigger["budget"] = {"max_model_calls": 3}
    view = store.propose_update(wid2, expected_version=store.status_view(wid2, now=NOW)["row_version"],
                                plan=bigger, reason="예산 증액", now=MUCH_LATER)
    assert {s["key"]: s["status"] for s in view["steps"]} == {"a": wf.S_SUCCEEDED, "b": wf.S_READY} and view["status"] == wf.W_RUNNING
    (b,) = store.claim_ready(now=MUCH_LATER)
    assert b.step_key == "b"


def test_the_view_shows_what_each_step_reads_and_what_it_resolved_to(store):
    wid = _submit(store, _multi()).workflow_id
    by_key = {s["key"]: s for s in store.status_view(wid, now=NOW)["steps"]}
    assert by_key["research"]["input_refs"] == [] and by_key["research"]["inputs"] == {}
    assert by_key["draft1"]["input_refs"] == ["research"] and by_key["draft1"]["inputs"] == {"research": None}
    (research,) = store.claim_ready(now=NOW)
    store.record_result(research.attempt_id, now=NOW, succeeded=True, result_ref="ledger:trace_r", model_calls=1)
    by_key = {s["key"]: s for s in store.status_view(wid, now=NOW)["steps"]}
    assert by_key["draft1"]["inputs"] == {"research": "ledger:trace_r"}
    (draft,) = store.claim_ready(now=NOW, limit=1)
    assert draft.input_refs == by_key[draft.step_key]["inputs"]
