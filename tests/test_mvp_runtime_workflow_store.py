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


def test_a_failed_dependency_blocks_everything_downstream_and_fails_the_workflow(store):
    wid = _submit(store, _multi()).workflow_id
    (research,) = store.claim_ready(now=NOW)
    out = store.record_result(research.attempt_id, now=LATER, succeeded=False, reason_code="PROVIDER_UNAVAILABLE")
    assert out["step_status"] == wf.S_FAILED and out["workflow_status"] == wf.W_FAILED
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
    assert out["step_status"] == wf.S_FAILED and out["workflow_status"] == wf.W_FAILED
    assert store.status_view(wid, now=MUCH_LATER)["last_reason_code"] == "TIMEOUT"


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
    assert store.status_view(wid, now=MUCH_LATER)["status"] == wf.W_FAILED
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
    assert step["status"] == wf.S_BLOCKED and step["last_reason_code"] == "BUDGET_EXHAUSTED"
    assert store.status_view(wid, now=NOW)["status"] == wf.W_BLOCKED


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
