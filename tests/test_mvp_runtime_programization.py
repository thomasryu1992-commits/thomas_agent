"""Programization repetition counter tests.

The counter is pure local state + closed-schema records, so most tests need no Core.
The end-to-end checks (observation rides a real run's records; ten valid repetitions put
PROGRAMIZATION_REVIEW_TRIGGERED on the audit chain) run the pipeline, so they need a
local Core activation.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.errors import PersistenceError, ProgramizationBlocked
from runtime.mvp_runtime.programization import (
    OBSERVATIONS_FILE,
    REVIEW_TRIGGER_COUNT,
    ProgramizationStore,
    build_pattern_signature,
    observe_completed_run,
)

from tests._helpers import requires_local_core

NOW = "2026-07-16T09:00:00Z"
STEPS = ["intake", "core_binding", "prime_planning", "readonly_search",
         "analysis_worker", "automatic_validation"]


def _task(i: int, *, revision: int = 1, request: str | None = None, trace: str | None = None):
    return {
        "identity": {"task_id": f"task_{i}", "task_revision": revision,
                     "trace_id": trace or f"trace_{i}_{revision}"},
        "context": {"core_context_binding_id": "ccb-test-1"},
        "request": {"raw_request": request if request is not None else f"이 사업 아이디어를 분석해줘: 아이디어 {i}"},
    }


_ASSIGNMENT = {"role_id": "general.specialist"}


def _observe(store, task, *, synthetic=False, now=NOW):
    return observe_completed_run(store, task=task, assignment=_ASSIGNMENT,
                                 steps=STEPS, synthetic=synthetic, now=now)


# --- pattern signature -------------------------------------------------------

def test_signature_is_deterministic_and_step_sensitive():
    a = build_pattern_signature(_ASSIGNMENT, STEPS)
    b = build_pattern_signature(_ASSIGNMENT, STEPS)
    c = build_pattern_signature(_ASSIGNMENT, STEPS + ["independent_validation"])
    assert a == b
    assert a["ordered_step_signature_sha256"] != c["ordered_step_signature_sha256"]
    for key in ("input_schema_sha256", "ordered_step_signature_sha256", "output_schema_sha256"):
        assert a[key].startswith("sha256:")


# --- observation validity + counting ----------------------------------------

def test_first_observation_is_valid_and_counted(tmp_path):
    store = ProgramizationStore(tmp_path / "prog")
    observation, pattern, triggered = _observe(store, _task(1))
    assert observation["valid_for_programization_count"] is True
    assert all(v is False for v in observation["counting_flags"].values())
    assert pattern["valid_repetition_count"] == 1
    assert pattern["review_status"] == "NOT_TRIGGERED"
    assert triggered is False


def test_retry_of_same_task_revision_is_not_counted(tmp_path):
    store = ProgramizationStore(tmp_path / "prog")
    _observe(store, _task(1))
    observation, pattern, _ = _observe(store, _task(1, trace="trace_retry"))
    assert observation["counting_flags"]["retry_of_same_task_revision"] is True
    assert observation["valid_for_programization_count"] is False
    assert pattern["valid_repetition_count"] == 1        # unchanged


def test_duplicate_trace_replay_is_not_counted(tmp_path):
    store = ProgramizationStore(tmp_path / "prog")
    _observe(store, _task(1))
    observation, pattern, _ = _observe(store, _task(2, trace="trace_1_1"))
    assert observation["counting_flags"]["duplicate_replay"] is True
    assert pattern["valid_repetition_count"] == 1


def test_same_input_without_business_event_is_not_counted(tmp_path):
    store = ProgramizationStore(tmp_path / "prog")
    _observe(store, _task(1, request="같은 아이디어"))
    observation, pattern, _ = _observe(store, _task(2, request="같은 아이디어"))
    assert observation["counting_flags"]["same_input_without_independent_business_event"] is True
    assert pattern["valid_repetition_count"] == 1


def test_synthetic_run_is_observed_but_never_counted(tmp_path):
    store = ProgramizationStore(tmp_path / "prog")
    observation, pattern, triggered = _observe(store, _task(1), synthetic=True)
    assert observation["counting_flags"]["synthetic_test"] is True
    assert observation["valid_for_programization_count"] is False
    assert pattern["valid_repetition_count"] == 0
    assert triggered is False


def test_ten_valid_repetitions_trigger_review_exactly_once(tmp_path):
    store = ProgramizationStore(tmp_path / "prog")
    for i in range(1, REVIEW_TRIGGER_COUNT):
        _, pattern, triggered = _observe(store, _task(i))
        assert triggered is False and pattern["review_status"] == "NOT_TRIGGERED"

    _, pattern, triggered = _observe(store, _task(REVIEW_TRIGGER_COUNT))
    assert triggered is True                             # the tenth valid observation
    assert pattern["review_status"] == "TRIGGERED"
    assert pattern["valid_repetition_count"] == REVIEW_TRIGGER_COUNT

    # The eleventh keeps counting but never re-triggers: the review opportunity was raised.
    _, pattern, triggered = _observe(store, _task(REVIEW_TRIGGER_COUNT + 1))
    assert triggered is False
    assert pattern["review_status"] == "TRIGGERED"
    assert pattern["valid_repetition_count"] == REVIEW_TRIGGER_COUNT + 1


def test_observation_requires_bound_task_identity(tmp_path):
    store = ProgramizationStore(tmp_path / "prog")
    task = _task(1)
    task["context"]["core_context_binding_id"] = ""
    with pytest.raises(ProgramizationBlocked) as exc:
        _observe(store, task)
    assert exc.value.reason_code == "OBSERVATION_INCOMPLETE"


def test_corrupt_store_fails_closed(tmp_path):
    root = tmp_path / "prog"
    root.mkdir()
    (root / OBSERVATIONS_FILE).write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(PersistenceError) as exc:
        _observe(ProgramizationStore(root), _task(1))
    assert exc.value.reason_code == "PROGRAMIZATION_UNREADABLE"


def test_pattern_rows_are_latest_wins_and_not_churned_by_invalid(tmp_path):
    store = ProgramizationStore(tmp_path / "prog")
    _observe(store, _task(1))
    rows_after_valid = len(store.read_patterns())
    _observe(store, _task(1, trace="trace_retry"))       # invalid: retry — no state change
    assert len(store.read_patterns()) == rows_after_valid
    assert store.latest_patterns()[next(iter(store.latest_patterns()))]["valid_repetition_count"] == 1


# --- pipeline integration (needs a local Core) ------------------------------

@requires_local_core
def test_mock_run_records_synthetic_observation(tmp_path):
    from runtime.mvp_runtime.pipeline import run_task
    from runtime.mvp_runtime.worker import MockProvider
    store = ProgramizationStore(tmp_path / "prog")
    r = run_task("이 사업 아이디어를 분석해줘: 구독형 반려동물 사료",
                 provider=MockProvider(), programization=store, now=NOW)
    assert r["status"] == "COMPLETED" and "programization_error" not in r
    observation = r["records"]["programization_observation"]
    assert observation["counting_flags"]["synthetic_test"] is True   # MockProvider has no egress
    assert observation["valid_for_programization_count"] is False
    assert r["records"]["programization_pattern"]["valid_repetition_count"] == 0


@requires_local_core
def test_ten_real_runs_put_review_trigger_on_the_audit_chain(tmp_path):
    from runtime.mvp_runtime.pipeline import run_task
    from runtime.mvp_runtime.worker import MockProvider

    class _EgressMockProvider(MockProvider):
        network_egress = True                            # counts as a real (non-synthetic) run

    store = ProgramizationStore(tmp_path / "prog")
    last = None
    for i in range(REVIEW_TRIGGER_COUNT):
        last = run_task(f"이 사업 아이디어를 분석해줘: 아이디어 {i}",
                        provider=_EgressMockProvider(), programization=store,
                        now=f"2026-07-16T09:{i:02d}:00Z")
        assert last["status"] == "COMPLETED"

    pattern = last["records"]["programization_pattern"]
    assert pattern["review_status"] == "TRIGGERED"
    assert pattern["valid_repetition_count"] == REVIEW_TRIGGER_COUNT
    trigger_events = [e for e in last["records"]["audit_trail"]
                      if "PROGRAMIZATION_REVIEW_TRIGGERED" in e["event"]["reason_codes"]]
    assert len(trigger_events) == 1
    assert "NO_PROGRAM_CREATED" in trigger_events[0]["event"]["reason_codes"]
    # Trigger exactly once: earlier runs carried no trigger event (checked via the store —
    # only one pattern row is TRIGGERED and its count is exactly the threshold at trigger).
    triggered_rows = [p for p in store.read_patterns() if p["review_status"] == "TRIGGERED"]
    assert min(p["valid_repetition_count"] for p in triggered_rows) == REVIEW_TRIGGER_COUNT


@requires_local_core
def test_counter_failure_is_best_effort_never_blocks_delivery(tmp_path):
    from runtime.mvp_runtime.pipeline import run_task
    from runtime.mvp_runtime.worker import MockProvider
    root = tmp_path / "prog"
    root.mkdir()
    (root / OBSERVATIONS_FILE).write_text("{not json}\n", encoding="utf-8")
    r = run_task("이 사업 아이디어를 분석해줘: 구독형 반려동물 사료",
                 provider=MockProvider(), programization=ProgramizationStore(root), now=NOW)
    assert r["status"] == "COMPLETED" and r["delivered"] is True
    assert r["programization_error"] == "PROGRAMIZATION_UNREADABLE"
    assert "programization_observation" not in r["records"]
