"""R2.5 Output Validation tests."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.intake import build_task
from runtime.mvp_runtime.prime import plan_task
from runtime.mvp_runtime.validation import validate_agent_output
from runtime.mvp_runtime.worker import MockProvider, run_analysis_worker
from runtime.read_only_kernel import schema_validation

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATION_SCHEMA = REPO_ROOT / "schemas" / "validation_result.v0.1.schema.json"
NOW = "2026-07-15T09:00:00Z"

from tests._helpers import requires_local_core


def _output_and_plan():
    task = build_task("이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송", now=NOW)
    plan = plan_task(task, now=NOW)
    out, _ = run_analysis_worker(plan["task"], plan["role_assignment"], provider=MockProvider(), created_at=NOW)
    return out, plan["task"], plan["role_assignment"]


def _validate(out, task, assignment):
    return validate_agent_output(out, task, assignment, now=NOW)


@requires_local_core
def test_pass_result_is_schema_valid_and_grants_nothing():
    out, task, assignment = _output_and_plan()
    vr = _validate(out, task, assignment)
    schema_validation.validate_against_schema(vr, VALIDATION_SCHEMA, "test")
    assert vr["validation"]["result"] == "PASS"
    assert vr["validation"]["recommended_next_state"] == "DELIVER_FINAL_RESPONSE"
    # Validation grants no permission/authority/execution and does not mutate the subject.
    assert all(v is False for v in vr["permission_boundary"].values())
    assert vr["runtime_effect"]["mode"] == "REVIEW_ONLY"
    assert vr["subject"]["subject_fingerprint"].startswith("sha256:")


@requires_local_core
def test_deterministic():
    out, task, assignment = _output_and_plan()
    assert _validate(out, task, assignment) == _validate(out, task, assignment)


@requires_local_core
def test_overconfident_output_revises():
    out, task, assignment = _output_and_plan()
    out = deepcopy(out)
    out["uncertainty"], out["assumptions"] = [], []
    assert _validate(out, task, assignment)["validation"]["result"] == "REVISE"


@requires_local_core
def test_missing_sections_revises():
    out, task, assignment = _output_and_plan()
    out = deepcopy(out)
    out["role_specific_output"]["key_findings"] = []
    assert _validate(out, task, assignment)["validation"]["result"] == "REVISE"


@requires_local_core
def test_lineage_mismatch_blocks():
    out, task, assignment = _output_and_plan()
    out = deepcopy(out)
    out["assignment_id"] = "assignment_wrong"
    assert _validate(out, task, assignment)["validation"]["result"] == "BLOCK"


@requires_local_core
def test_permission_expansion_blocks():
    out, task, assignment = _output_and_plan()
    out = deepcopy(out)
    out["permission_request_refs"] = ["permreq_x"]
    assert _validate(out, task, assignment)["validation"]["result"] == "BLOCK"


@requires_local_core
def test_secret_bearing_key_blocks():
    out, task, assignment = _output_and_plan()
    out = deepcopy(out)
    out["role_specific_output"]["api_key"] = "leaked"
    assert _validate(out, task, assignment)["validation"]["result"] == "BLOCK"


# --- grounding: a cited source must be one the run issued (system review B3, 2026-09-25) --------

def _grounding_check(vr):
    return next(c for c in vr["validation"]["checks"] if c["check_id"] == "evidence_grounding")


@requires_local_core
def test_a_fact_citing_a_source_the_run_never_issued_revises():
    """The check was vacuous: a fact with no refs defaults to `model:analysis`, which is always in
    the evidence, so any output with one fact passed. A citation number the prompt never issued is
    a fabricated source."""
    out, task, assignment = _output_and_plan()
    out = deepcopy(out)
    out["facts"] = [{"statement": "Market size is 3조원.", "evidence_refs": ["[S7]"]}]
    vr = _validate(out, task, assignment)
    assert vr["validation"]["result"] == "REVISE"
    assert "[S7]" in _grounding_check(vr)["notes"]


@requires_local_core
def test_a_fact_citing_an_issued_source_passes_and_the_ratio_is_recorded():
    out, task, assignment = _output_and_plan()
    out = deepcopy(out)
    out["evidence"] = [*out["evidence"], {"ref": "search:tavily:1", "type": "web_search",
                                          "url": "https://example.invalid", "title": "t"}]
    out["facts"] = [{"statement": "A cited fact.", "evidence_refs": ["[S1]"]},
                    {"statement": "A reasoned fact.", "evidence_refs": ["model:analysis"]}]
    vr = _validate(out, task, assignment)
    check = _grounding_check(vr)
    assert check["result"] == "PASS"
    assert "1/2 cite a retrieved source" in check["notes"]


def test_the_census_resolves_markers_against_what_was_issued():
    from runtime.mvp_runtime.validation import grounding_census
    evidence = [{"ref": "model:analysis"}, {"ref": "search:tavily:1"}, {"ref": "search:tavily:2"},
                {"ref": "keyword:naver:1"}, {"ref": "working_memory:memcand_x"}]
    facts = [
        {"evidence_refs": ["S2"]},                       # bare marker resolves
        {"evidence_refs": ["[K1]"]},
        {"evidence_refs": ["working_memory:memcand_x"]},  # verbatim ref resolves
        {"evidence_refs": ["model:analysis"]},           # resolves, but is not a retrieved source
        {"evidence_refs": ["[S3]", "[K4]"]},             # numbers never issued
        {"evidence_refs": ["industry report"]},          # free text: neither sourced nor fabricated
    ]
    sourced, fabricated = grounding_census(facts, evidence)
    assert sourced == 3
    assert fabricated == {"[S3]", "[K4]"}
