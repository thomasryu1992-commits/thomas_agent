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
