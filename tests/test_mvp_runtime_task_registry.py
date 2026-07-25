"""F1 Task Registry tests — the coordination ledger and its store semantics.

What must hold: the lifecycle is forward-only (a status in this file can be believed), a
malformed row is a typed refusal rather than a traceback, the recording seam never costs a
run, and an entry stranded by a dead process gets an honest terminal instead of claiming to
still be running.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import task_registry
from runtime.mvp_runtime.errors import TaskRegistryBlocked
from runtime.mvp_runtime.task_registry import (
    BLOCKED,
    CANCELLED,
    DELIVERED,
    FAILED,
    QUEUED,
    RUNNING,
    TaskRegistryStore,
    build_entry,
    reconcile_stale_running,
)

NOW = "2026-07-25T09:00:00Z"
LATER = "2026-07-25T09:05:00Z"


def _store(tmp_path) -> TaskRegistryStore:
    return TaskRegistryStore(tmp_path)


def _entry(**overrides):
    kwargs = {
        "request_text": "이 사업 아이디어를 분석해줘: 구독형 세차",
        "origin": "TELEGRAM",
        "requester_id": "tg-12345",
        "now": NOW,
    }
    kwargs.update(overrides)
    return build_entry(**kwargs)


# --- building ----------------------------------------------------------------

def test_build_entry_is_deterministic_and_schema_valid(tmp_path):
    first = _entry()
    second = _entry()
    assert first.registry_entry_id == second.registry_entry_id
    assert first.registry_entry_id.startswith("treg_")
    # Persisting validates against the closed schema; a bad record would raise here.
    _store(tmp_path).submit(first)


def test_same_request_at_a_different_moment_is_a_different_entry():
    """The registry logs submissions, not distinct texts — asking twice is two entries."""
    assert _entry(now=NOW).registry_entry_id != _entry(now=LATER).registry_entry_id


@pytest.mark.parametrize("overrides, code", [
    ({"request_text": "   "}, "EMPTY_REQUEST"),
    ({"origin": "SLACK"}, "UNKNOWN_ORIGIN"),
    ({"requester_id": ""}, "MISSING_REQUESTER"),
    ({"status": DELIVERED}, "INVALID_INITIAL_STATUS"),
])
def test_build_entry_fails_closed(overrides, code):
    with pytest.raises(TaskRegistryBlocked) as exc:
        _entry(**overrides)
    assert exc.value.reason_code == code


def test_unknown_flag_is_refused_not_dropped():
    """A silently-dropped flag would make history explain a run by options it never had."""
    with pytest.raises(TaskRegistryBlocked) as exc:
        _entry(flags={"important": True, "turbo": True})
    assert exc.value.reason_code == "UNKNOWN_FLAG"


# --- lifecycle ---------------------------------------------------------------

def test_forward_lifecycle_to_delivered(tmp_path):
    store = _store(tmp_path)
    entry = store.submit(_entry())
    store.transition(entry.registry_entry_id, RUNNING, now=NOW)
    final = store.transition(
        entry.registry_entry_id, DELIVERED, now=LATER,
        task_id="task_abc", trace_id="trace_abc", result_ref="ledger:trace_abc",
    )
    assert (final.status, final.started_at, final.finished_at) == (DELIVERED, NOW, LATER)
    assert final.trace_id == "trace_abc"
    assert final.is_terminal


@pytest.mark.parametrize("first, second", [
    (QUEUED, DELIVERED),          # a delivered result that never ran is a bookkeeping lie
    (QUEUED, FAILED),
    (RUNNING, CANCELLED),         # stopping in-flight work is the kill switch's authority
    (RUNNING, QUEUED),            # no going backwards
])
def test_illegal_transitions_are_refused(tmp_path, first, second):
    store = _store(tmp_path)
    entry = store.submit(_entry(status=QUEUED))
    if first != QUEUED:
        store.transition(entry.registry_entry_id, first, now=NOW)
    with pytest.raises(TaskRegistryBlocked) as exc:
        store.transition(entry.registry_entry_id, second, now=LATER)
    assert exc.value.reason_code == "TRANSITION_INVALID"


def test_terminal_is_final(tmp_path):
    store = _store(tmp_path)
    entry = store.submit(_entry(status=QUEUED))
    store.transition(entry.registry_entry_id, CANCELLED, now=NOW)
    for target in (RUNNING, DELIVERED, BLOCKED):
        with pytest.raises(TaskRegistryBlocked):
            store.transition(entry.registry_entry_id, target, now=LATER)


def test_transition_never_erases_identity_it_did_not_learn(tmp_path):
    """A later transition that does not know the trace must not blank the one recorded."""
    store = _store(tmp_path)
    entry = store.submit(_entry(status=RUNNING))
    store.transition(entry.registry_entry_id, DELIVERED, now=LATER, trace_id="trace_keep")
    assert store.find(entry.registry_entry_id).trace_id == "trace_keep"


def test_transition_on_unknown_entry_fails_closed(tmp_path):
    with pytest.raises(TaskRegistryBlocked) as exc:
        _store(tmp_path).transition("treg_missing", RUNNING, now=NOW)
    assert exc.value.reason_code == "ENTRY_NOT_FOUND"


# --- store semantics ---------------------------------------------------------

def test_latest_row_wins_and_history_is_kept(tmp_path):
    store = _store(tmp_path)
    entry = store.submit(_entry(status=RUNNING))
    store.transition(entry.registry_entry_id, DELIVERED, now=LATER)
    assert len(store.latest()) == 1
    assert store.latest()[0].status == DELIVERED
    # Append-only: the sequence of rows IS the entry's history.
    rows = [json.loads(line) for line in store.path.read_text(encoding="utf-8").splitlines()]
    assert [r["status"] for r in rows] == [RUNNING, DELIVERED]


def test_find_accepts_an_unambiguous_prefix_and_refuses_an_ambiguous_one(tmp_path):
    store = _store(tmp_path)
    entry = store.submit(_entry())
    assert store.find(entry.registry_entry_id[:10]).registry_entry_id == entry.registry_entry_id
    assert store.find("treg_nope") is None
    store.submit(_entry(now=LATER))
    with pytest.raises(TaskRegistryBlocked) as exc:
        store.find("treg_")
    assert exc.value.reason_code == "AMBIGUOUS_ENTRY_ID"


def test_malformed_row_is_a_typed_refusal_not_a_traceback(tmp_path):
    """The SCHEDULE_RECORD_INVALID lesson: a hand-edited row must not kill the loop."""
    store = _store(tmp_path)
    store.submit(_entry())
    store.path.write_text(json.dumps({"registry_entry_id": "treg_x", "status": "RUNNING"}) + "\n",
                          encoding="utf-8")
    with pytest.raises(TaskRegistryBlocked) as exc:
        store.latest()
    assert exc.value.reason_code == "REGISTRY_RECORD_INVALID"


def test_unknown_status_in_a_row_is_refused(tmp_path):
    store = _store(tmp_path)
    entry = store.submit(_entry())
    row = json.loads(store.path.read_text(encoding="utf-8").splitlines()[0])
    row["status"] = "ALMOST_DONE"
    store.path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(TaskRegistryBlocked) as exc:
        store.latest()
    assert exc.value.reason_code == "REGISTRY_RECORD_INVALID"
    assert entry.registry_entry_id  # the id existed; the status is what was rejected


def test_null_optional_fields_never_become_the_string_none(tmp_path):
    """The exact coercion that made a scheduler row sort above every timestamp."""
    store = _store(tmp_path)
    entry = store.submit(_entry())
    reloaded = store.find(entry.registry_entry_id)
    assert reloaded.trace_id is None and reloaded.finished_at is None


# --- reconciliation ----------------------------------------------------------

def test_stranded_running_entry_gets_an_honest_terminal(tmp_path):
    store = _store(tmp_path)
    running = store.submit(_entry(status=RUNNING))
    done = store.submit(_entry(now=LATER, status=RUNNING))
    store.transition(done.registry_entry_id, DELIVERED, now=LATER)

    reconciled = reconcile_stale_running(store, now="2026-07-25T10:00:00Z")

    assert [e.registry_entry_id for e in reconciled] == [running.registry_entry_id]
    stranded = store.find(running.registry_entry_id)
    assert stranded.status == FAILED
    assert stranded.last_reason_code == task_registry.ABANDONED_REASON_CODE
    # A finished entry is never touched.
    assert store.find(done.registry_entry_id).status == DELIVERED


# --- the best-effort recording seam ------------------------------------------

def test_recording_seam_returns_none_rather_than_costing_a_run(tmp_path):
    """A bookkeeping failure must never take down the analysis it only observes."""
    assert task_registry.record_submission(
        None, request_text="x", origin="TELEGRAM", requester_id="tg", now=NOW) is None
    # An invalid submission degrades to "unrecorded", not to a raised error on the run path.
    assert task_registry.record_submission(
        _store(tmp_path), request_text="  ", origin="TELEGRAM", requester_id="tg", now=NOW) is None


def test_close_entry_swallows_an_illegal_transition(tmp_path):
    store = _store(tmp_path)
    entry = store.submit(_entry(status=QUEUED))
    # QUEUED -> DELIVERED is illegal; the seam must not raise into the run path.
    task_registry.close_entry(store, entry, status=DELIVERED, now=LATER)
    assert store.find(entry.registry_entry_id).status == QUEUED
