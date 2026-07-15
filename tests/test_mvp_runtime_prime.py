"""R2.2 Thomas Prime orchestrator (plan_task) tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.errors import PlannerBlocked
from runtime.mvp_runtime.intake import build_task
from runtime.mvp_runtime.prime import plan_task
from runtime.read_only_kernel import schema_validation

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_POINTER = REPO_ROOT / DEFAULT_POINTER_REL
TASK_SCHEMA = REPO_ROOT / "schemas" / "task.v0.3.schema.json"
NOW = "2026-07-15T09:00:00Z"

requires_local_core = pytest.mark.skipif(not LOCAL_POINTER.is_file(), reason="no local Core activation")


def test_out_of_scope_task_blocks_before_binding():
    # classify runs first, so an out-of-scope task fails closed everywhere.
    task = build_task("분석해줘", now=NOW, constraints=["something_else"])
    with pytest.raises(PlannerBlocked) as exc:
        plan_task(task, now=NOW)
    assert exc.value.reason_code == "OUT_OF_MVP_SCOPE"


@requires_local_core
def test_plan_task_produces_planned_task_and_records():
    task = build_task("이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송", now=NOW)
    result = plan_task(task, now=NOW)

    planned = result["task"]
    schema_validation.validate_against_schema(planned, TASK_SCHEMA, "test")
    assert planned["lifecycle"]["status"] == "PLANNED"
    assert planned["classification"]["classification_status"] == "CLASSIFIED"
    assert planned["routing"]["selected_route"] == "ROLE"
    assert planned["routing"]["assigned_role_ids"] == ["general.specialist"]
    assert planned["permission"]["evaluation_status"] == "DECIDED"
    assert planned["permission"]["permission_decision"] == "ALLOW"

    # all planning records are present and reference the same task/permission.
    for key in ("binding", "permission_decision", "role_assignment", "decision", "role"):
        assert key in result
    a = result["role_assignment"]
    assert planned["routing"]["role_assignment_ids"] == [a["assignment_id"]]
    assert a["permission"]["permission_decision_ref"] == result["permission_decision"]["permission_decision_id"]


@requires_local_core
def test_plan_task_is_deterministic():
    a = plan_task(build_task("분석해줘", now=NOW), now=NOW)
    b = plan_task(build_task("분석해줘", now=NOW), now=NOW)
    assert a["role_assignment"]["assignment_id"] == b["role_assignment"]["assignment_id"]
    assert a["task"]["identity"]["task_id"] == b["task"]["identity"]["task_id"]
