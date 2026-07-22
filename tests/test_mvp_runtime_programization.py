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
    create_program_candidate,
    observe_completed_run,
    record_shadow_result,
    transition_candidate,
    transition_review,
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


def test_threshold_valid_repetitions_trigger_review_exactly_once(tmp_path):
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


def test_v01_pattern_rows_migrate_forward_on_next_touch(tmp_path):
    """A pre-amendment v0.1 row (threshold 10) migrates to v0.2/5 on its next touch: the
    next valid observation re-evaluates the trigger at 5, and an operator transition
    normalizes the row. Historical v0.1 rows stay untouched in the file."""
    store = ProgramizationStore(tmp_path / "prog")
    # Seed enough valid observations to sit between the new (5) and old (10) thresholds,
    # then rewrite the latest pattern row as a legacy v0.1 row (as a pre-amendment store
    # would have left it: NOT_TRIGGERED at count 6 under threshold 10).
    for i in range(1, 7):
        _, pattern, _ = _observe(store, _task(i))
    legacy = {**pattern, "schema_version": "programization_pattern.v0.1",
              "review_trigger_count": 10, "review_status": "NOT_TRIGGERED"}
    store.append_pattern(legacy)

    _, migrated, triggered = _observe(store, _task(7))
    assert triggered is True                              # 7 >= 5 under the new threshold
    assert migrated["schema_version"] == "programization_pattern.v0.2"
    assert migrated["review_trigger_count"] == REVIEW_TRIGGER_COUNT
    assert migrated["review_status"] == "TRIGGERED"

    p = transition_review(store, migrated["pattern_id"], "UNDER_REVIEW",
                          reviewed_by="thomas", reason="r", now=NOW)
    assert p["schema_version"] == "programization_pattern.v0.2"


def test_pattern_rows_are_latest_wins_and_not_churned_by_invalid(tmp_path):
    store = ProgramizationStore(tmp_path / "prog")
    _observe(store, _task(1))
    rows_after_valid = len(store.read_patterns())
    _observe(store, _task(1, trace="trace_retry"))       # invalid: retry — no state change
    assert len(store.read_patterns()) == rows_after_valid
    assert store.latest_patterns()[next(iter(store.latest_patterns()))]["valid_repetition_count"] == 1


# --- review handling (operator transitions + candidate) ----------------------

def _triggered_store(tmp_path):
    store = ProgramizationStore(tmp_path / "prog")
    for i in range(1, REVIEW_TRIGGER_COUNT + 1):
        _, pattern, _ = _observe(store, _task(i))
    return store, pattern["pattern_id"]


_REVIEW_INPUT = {
    "deterministic_slice": ["normalize_input", "score", "render_report"],
    "agent_retained_responsibilities": ["interpretation", "strategy", "material_exception"],
    "defined_exceptions": ["unknown_input_schema"],
    "rollback_procedure_ref": "rollback-analysis-draft-001",
    "baseline_metrics": {"latency_ms": 2000},
}


def test_review_transitions_forward_only(tmp_path):
    store, pattern_id = _triggered_store(tmp_path)
    p = transition_review(store, pattern_id, "UNDER_REVIEW", reviewed_by="thomas", reason="r", now=NOW)
    assert p["review_status"] == "UNDER_REVIEW"
    p = transition_review(store, pattern_id, "CLOSED", reviewed_by="thomas", reason="r", now=NOW)
    assert p["review_status"] == "CLOSED"
    with pytest.raises(ProgramizationBlocked) as exc:      # CLOSED is terminal
        transition_review(store, pattern_id, "UNDER_REVIEW", reviewed_by="thomas", reason="r", now=NOW)
    assert exc.value.reason_code == "INVALID_REVIEW_TRANSITION"


def test_review_requires_triggered_pattern(tmp_path):
    store = ProgramizationStore(tmp_path / "prog")
    _, pattern, _ = _observe(store, _task(1))              # NOT_TRIGGERED
    with pytest.raises(ProgramizationBlocked) as exc:
        transition_review(store, pattern["pattern_id"], "UNDER_REVIEW",
                          reviewed_by="thomas", reason="r", now=NOW)
    assert exc.value.reason_code == "INVALID_REVIEW_TRANSITION"


def test_review_requires_operator_identity_and_reason(tmp_path):
    store, pattern_id = _triggered_store(tmp_path)
    with pytest.raises(ProgramizationBlocked) as exc:
        transition_review(store, pattern_id, "UNDER_REVIEW", reviewed_by="", reason="r", now=NOW)
    assert exc.value.reason_code == "MISSING_OPERATOR"
    with pytest.raises(ProgramizationBlocked) as exc:
        transition_review(store, pattern_id, "UNDER_REVIEW", reviewed_by="thomas", reason=" ", now=NOW)
    assert exc.value.reason_code == "MISSING_REASON"
    with pytest.raises(ProgramizationBlocked) as exc:
        transition_review(store, "progpat_missing", "UNDER_REVIEW", reviewed_by="thomas", reason="r", now=NOW)
    assert exc.value.reason_code == "PATTERN_NOT_FOUND"


def test_counter_keeps_counting_during_review_without_touching_status(tmp_path):
    store, pattern_id = _triggered_store(tmp_path)
    transition_review(store, pattern_id, "UNDER_REVIEW", reviewed_by="thomas", reason="r", now=NOW)
    _, pattern, triggered = _observe(store, _task(REVIEW_TRIGGER_COUNT + 1))
    assert triggered is False
    assert pattern["review_status"] == "UNDER_REVIEW"      # the operator owns this now
    assert pattern["valid_repetition_count"] == REVIEW_TRIGGER_COUNT + 1


def test_candidate_requires_under_review(tmp_path):
    store, pattern_id = _triggered_store(tmp_path)
    with pytest.raises(ProgramizationBlocked) as exc:      # still TRIGGERED
        create_program_candidate(store, pattern_id, _REVIEW_INPUT,
                                 created_by="thomas", reason="r", now=NOW)
    assert exc.value.reason_code == "CANDIDATE_REQUIRES_REVIEW"


def test_candidate_created_with_schema_constants(tmp_path):
    store, pattern_id = _triggered_store(tmp_path)
    transition_review(store, pattern_id, "UNDER_REVIEW", reviewed_by="thomas", reason="r", now=NOW)
    candidate = create_program_candidate(store, pattern_id, _REVIEW_INPUT,
                                         created_by="thomas", reason="r", now=NOW)
    assert candidate["status"] == "DRAFT"
    assert candidate["permission_expansion"] is False
    assert candidate["activation_eligibility"] == "candidate_only_pending_program_registry_and_permission_policy"
    assert candidate["valid_repetition_count"] == REVIEW_TRIGGER_COUNT
    assert candidate["shadow_validation"] == {"status": "NOT_STARTED", "comparison_ref": None, "result": None}
    assert store.read_candidates() == [candidate]          # persisted

    with pytest.raises(ProgramizationBlocked) as exc:      # one candidate per pattern
        create_program_candidate(store, pattern_id, _REVIEW_INPUT,
                                 created_by="thomas", reason="r", now=NOW)
    assert exc.value.reason_code == "CANDIDATE_EXISTS"


def test_candidate_input_is_fail_closed(tmp_path):
    store, pattern_id = _triggered_store(tmp_path)
    transition_review(store, pattern_id, "UNDER_REVIEW", reviewed_by="thomas", reason="r", now=NOW)
    for broken in (
        {**_REVIEW_INPUT, "deterministic_slice": []},
        {**_REVIEW_INPUT, "rollback_procedure_ref": ""},
        "not a mapping",
    ):
        with pytest.raises(ProgramizationBlocked) as exc:
            create_program_candidate(store, pattern_id, broken,  # type: ignore[arg-type]
                                     created_by="thomas", reason="r", now=NOW)
        assert exc.value.reason_code == "CANDIDATE_INPUT_INVALID"
    with pytest.raises(ProgramizationBlocked) as exc:
        create_program_candidate(store, pattern_id,
                                 {**_REVIEW_INPUT, "baseline_metrics": {"api_key": "x"}},
                                 created_by="thomas", reason="r", now=NOW)
    assert exc.value.reason_code == "SECRET_IN_CANDIDATE"
    assert store.read_candidates() == []                   # nothing persisted


# --- candidate shadow-validation path ----------------------------------------

def _draft_candidate(tmp_path):
    store, pattern_id = _triggered_store(tmp_path)
    transition_review(store, pattern_id, "UNDER_REVIEW", reviewed_by="thomas", reason="r", now=NOW)
    candidate = create_program_candidate(store, pattern_id, _REVIEW_INPUT,
                                         created_by="thomas", reason="r", now=NOW)
    return store, candidate["candidate_id"]


def _t(store, cid, action):
    return transition_candidate(store, cid, action, reviewed_by="thomas", reason="r")


def _shadow(store, cid, outcome, **kw):
    kw.setdefault("comparison_ref", "shadow-cmp-001")
    kw.setdefault("result", "limited comparison against the agent baseline")
    return record_shadow_result(store, cid, outcome, reviewed_by="thomas", reason="r", **kw)


def test_candidate_lifecycle_happy_path(tmp_path):
    store, cid = _draft_candidate(tmp_path)
    assert _t(store, cid, "ready")["status"] == "REVIEW_READY"
    c = _t(store, cid, "validate")
    assert c["status"] == "VALIDATING"
    assert c["shadow_validation"] == {"status": "RUNNING", "comparison_ref": None, "result": None}
    c = _shadow(store, cid, "PASS")
    assert c["status"] == "VALIDATING"                     # outcome recorded, decision separate
    assert c["shadow_validation"]["status"] == "PASS"
    c = _t(store, cid, "accept")
    assert c["status"] == "ACCEPTED"
    # ACCEPTED still grants nothing — the schema constants survived every transition.
    assert c["permission_expansion"] is False
    assert c["activation_eligibility"] == "candidate_only_pending_program_registry_and_permission_policy"
    # Terminal: nothing moves an ACCEPTED candidate.
    with pytest.raises(ProgramizationBlocked) as exc:
        _t(store, cid, "reject")
    assert exc.value.reason_code == "INVALID_CANDIDATE_TRANSITION"


def test_accept_requires_recorded_shadow_pass(tmp_path):
    store, cid = _draft_candidate(tmp_path)
    with pytest.raises(ProgramizationBlocked) as exc:      # from DRAFT: wrong status entirely
        _t(store, cid, "accept")
    assert exc.value.reason_code == "INVALID_CANDIDATE_TRANSITION"
    _t(store, cid, "ready")
    _t(store, cid, "validate")
    with pytest.raises(ProgramizationBlocked) as exc:      # VALIDATING but shadow still RUNNING
        _t(store, cid, "accept")
    assert exc.value.reason_code == "ACCEPT_REQUIRES_SHADOW_PASS"
    _shadow(store, cid, "FAIL")
    with pytest.raises(ProgramizationBlocked) as exc:      # a FAILed shadow can never be accepted
        _t(store, cid, "accept")
    assert exc.value.reason_code == "ACCEPT_REQUIRES_SHADOW_PASS"
    assert _t(store, cid, "reject")["status"] == "REJECTED"


def test_shadow_outcome_needs_running_shadow_and_evidence(tmp_path):
    store, cid = _draft_candidate(tmp_path)
    with pytest.raises(ProgramizationBlocked) as exc:      # DRAFT: no shadow started
        _shadow(store, cid, "PASS")
    assert exc.value.reason_code == "SHADOW_NOT_RUNNING"
    _t(store, cid, "ready")
    _t(store, cid, "validate")
    with pytest.raises(ProgramizationBlocked) as exc:
        _shadow(store, cid, "MAYBE")
    assert exc.value.reason_code == "SHADOW_OUTCOME_INVALID"
    for broken in ({"comparison_ref": " "}, {"result": ""}):
        with pytest.raises(ProgramizationBlocked) as exc:
            _shadow(store, cid, "PASS", **broken)
        assert exc.value.reason_code == "SHADOW_EVIDENCE_MISSING"
    _shadow(store, cid, "PASS")
    with pytest.raises(ProgramizationBlocked) as exc:      # outcome is single-shot: no re-recording
        _shadow(store, cid, "FAIL")
    assert exc.value.reason_code == "SHADOW_NOT_RUNNING"


def test_candidate_actions_fail_closed_on_identity_and_unknown_id(tmp_path):
    store, cid = _draft_candidate(tmp_path)
    with pytest.raises(ProgramizationBlocked) as exc:
        transition_candidate(store, cid, "ready", reviewed_by="", reason="r")
    assert exc.value.reason_code == "MISSING_OPERATOR"
    with pytest.raises(ProgramizationBlocked) as exc:
        transition_candidate(store, "progcand_missing", "ready", reviewed_by="thomas", reason="r")
    assert exc.value.reason_code == "CANDIDATE_NOT_FOUND"
    with pytest.raises(ProgramizationBlocked) as exc:
        transition_candidate(store, cid, "promote", reviewed_by="thomas", reason="r")
    assert exc.value.reason_code == "INVALID_CANDIDATE_TRANSITION"


def test_cli_candidate_lifecycle_and_ledger_events(tmp_path, capsys):
    import json

    from runtime.mvp_runtime.control import ControlStore
    from runtime.mvp_runtime.programization_cli import main
    from runtime.mvp_runtime.store import LedgerStore
    store, cid = _draft_candidate(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    control = ControlStore(tmp_path)

    for argv in (
        ["ready", cid, "--by", "thomas", "--reason", "substance complete"],
        ["validate", cid, "--by", "thomas", "--reason", "start limited comparison"],
        ["shadow", cid, "--outcome", "PASS", "--comparison-ref", "shadow-cmp-001",
         "--result", "equivalent quality, lower cost", "--by", "thomas", "--reason", "comparison done"],
        ["accept", cid, "--by", "thomas", "--reason", "convincing"],
    ):
        assert main(argv, store=store, ledger=ledger, control_store=control, now=NOW) == 0

    latest = store.latest_candidates()[cid]
    assert latest["status"] == "ACCEPTED" and latest["shadow_validation"]["status"] == "PASS"
    events = [json.loads(line) for line in
              (ledger.root / "programization_events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["action"] for e in events] == [
        "candidate_ready", "candidate_validate", "shadow_pass", "candidate_accept"]
    assert all(e["candidate_id"] == cid for e in events)
    assert events[-1]["shadow_status"] == "PASS"           # the evidence state the decision saw
    assert main(["status"], store=store, ledger=ledger, control_store=control, now=NOW) == 0
    assert "ACCEPTED" in capsys.readouterr().out


# --- review CLI ---------------------------------------------------------------

def test_cli_review_flow_and_ledger_events(tmp_path, capsys):
    import json

    from runtime.mvp_runtime.control import ControlStore
    from runtime.mvp_runtime.programization_cli import main
    from runtime.mvp_runtime.store import LedgerStore
    store, pattern_id = _triggered_store(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    control = ControlStore(tmp_path)

    assert main(["status"], store=store, ledger=ledger, control_store=control, now=NOW) == 0
    assert "TRIGGERED" in capsys.readouterr().out

    assert main(["review", pattern_id, "--by", "thomas", "--reason", "worth a look"],
                store=store, ledger=ledger, control_store=control, now=NOW) == 0

    input_path = tmp_path / "review.yaml"
    input_path.write_text(
        "deterministic_slice: [normalize_input, score]\n"
        "agent_retained_responsibilities: [interpretation]\n"
        "defined_exceptions: [unknown_input_schema]\n"
        "rollback_procedure_ref: rollback-001\n",
        encoding="utf-8",
    )
    assert main(["candidate", pattern_id, "--input", str(input_path), "--by", "thomas", "--reason", "review outcome"],
                store=store, ledger=ledger, control_store=control, now=NOW) == 0
    assert main(["close", pattern_id, "--by", "thomas", "--reason", "done"],
                store=store, ledger=ledger, control_store=control, now=NOW) == 0

    assert store.latest_patterns()[pattern_id]["review_status"] == "CLOSED"
    assert len(store.read_candidates()) == 1
    events = [json.loads(line) for line in
              (ledger.root / "programization_events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["action"] for e in events] == ["review_review", "candidate_drafted", "review_close"]
    assert all(e["record_type"] == "programization_review_event.v0" for e in events)
    assert all(e["integrity"]["event_sha256"].startswith("sha256:") for e in events)


def test_cli_mutations_refused_while_killed(tmp_path, capsys):
    from runtime.mvp_runtime import control
    from runtime.mvp_runtime.control import ControlStore
    from runtime.mvp_runtime.programization_cli import main
    from runtime.mvp_runtime.store import LedgerStore
    store, pattern_id = _triggered_store(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    control_store = ControlStore(tmp_path)
    control.apply_command(control_store, "kill", actor="op", now=NOW)

    assert main(["review", pattern_id, "--by", "thomas", "--reason", "r"],
                store=store, ledger=ledger, control_store=control_store, now=NOW) != 0
    assert "RUNTIME_KILLED" in capsys.readouterr().err
    assert store.latest_patterns()[pattern_id]["review_status"] == "TRIGGERED"   # unchanged
    # status stays answerable while killed (read-only door)
    assert main(["status"], store=store, ledger=ledger, control_store=control_store, now=NOW) == 0


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
def test_threshold_real_runs_put_review_trigger_on_the_audit_chain(tmp_path):
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
