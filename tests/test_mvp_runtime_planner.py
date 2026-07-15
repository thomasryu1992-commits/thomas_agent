"""R2.2 Prime Planner tests — classification + role selection (first increment)."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.errors import PlannerBlocked
from runtime.mvp_runtime.intake import build_task
from runtime.mvp_runtime.planner import (
    classify_task,
    load_resolved_roles,
    select_role,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXED_NOW = "2026-07-15T09:00:00Z"


def _received_task(**overrides):
    params = dict(raw_request="이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송", now=FIXED_NOW)
    params.update(overrides)
    return build_task(**params)


# --- classification ---------------------------------------------------------

def test_classify_received_task():
    decision = classify_task(_received_task())
    c = decision["classification"]
    assert c["classification_status"] == "CLASSIFIED"
    assert c["execution_mode"] == "AGENT"
    assert c["risk_level"] == "GREEN"
    assert c["priority"] == "NORMAL"
    assert decision["authority"]["required_permission_level"] == "P2"
    assert decision["required_capabilities"] == ["research", "analysis"]
    assert decision["permission_scope"] == "INTERNAL_ANALYSIS"


def test_classify_preserves_priority():
    # intake defaults priority NORMAL; the classifier must carry whatever intake set.
    task = _received_task()
    task["classification"]["priority"] = "HIGH"
    assert classify_task(task)["classification"]["priority"] == "HIGH"


def test_classify_blocks_already_classified():
    task = _received_task()
    task["classification"]["classification_status"] = "CLASSIFIED"
    with pytest.raises(PlannerBlocked) as exc:
        classify_task(task)
    assert exc.value.reason_code == "ALREADY_CLASSIFIED"


def test_classify_blocks_non_received():
    task = _received_task()
    task["lifecycle"]["status"] = "PLANNED"
    with pytest.raises(PlannerBlocked) as exc:
        classify_task(task)
    assert exc.value.reason_code == "NOT_RECEIVED"


def test_classify_blocks_out_of_scope_without_readonly_constraint():
    # A task intaken without the no_external_action constraint is out of MVP scope.
    task = _received_task(constraints=["some_other_constraint"])
    with pytest.raises(PlannerBlocked) as exc:
        classify_task(task)
    assert exc.value.reason_code == "OUT_OF_MVP_SCOPE"


# --- role selection (real registry) -----------------------------------------

def test_select_role_picks_general_specialist():
    resolved = load_resolved_roles(REPO_ROOT)
    role = select_role(
        resolved,
        required_capabilities=["research", "analysis"],
        required_permission_level="P3",
    )
    assert role["role_id"] == "general.specialist"
    assert role["permission_ceiling"] == "P3"
    assert role["routable"] is True
    assert {"research", "analysis"}.issubset(set(role["capabilities"]))


def test_select_role_blocks_when_no_capability_match():
    resolved = load_resolved_roles(REPO_ROOT)
    with pytest.raises(PlannerBlocked) as exc:
        select_role(
            resolved,
            required_capabilities=["independent_validation"],
            required_permission_level="P3",
        )
    assert exc.value.reason_code == "NO_ROUTABLE_ROLE"


def test_select_role_blocks_when_ceiling_too_low():
    # Requiring P4 excludes general.specialist (ceiling P3) -> no routable role.
    resolved = load_resolved_roles(REPO_ROOT)
    with pytest.raises(PlannerBlocked) as exc:
        select_role(
            resolved,
            required_capabilities=["research", "analysis"],
            required_permission_level="P4",
        )
    assert exc.value.reason_code == "NO_ROUTABLE_ROLE"


def test_select_role_ignores_candidate_roles():
    # business.analysis is a candidate (routable:false) and must never be selected,
    # even though its capabilities would otherwise match.
    resolved = load_resolved_roles(REPO_ROOT)
    role = select_role(
        resolved,
        required_capabilities=["research", "analysis"],
        required_permission_level="P3",
    )
    assert role["role_id"] != "business.analysis"


def test_select_role_rejects_invalid_level():
    resolved = load_resolved_roles(REPO_ROOT)
    with pytest.raises(PlannerBlocked) as exc:
        select_role(resolved, required_capabilities=["research"], required_permission_level="P9")
    assert exc.value.reason_code == "INVALID_REQUIRED_LEVEL"


def test_end_to_end_classify_then_select():
    task = _received_task()
    decision = classify_task(task)
    resolved = load_resolved_roles(REPO_ROOT)
    role = select_role(
        resolved,
        required_capabilities=decision["required_capabilities"],
        required_permission_level=decision["authority"]["required_permission_level"],
    )
    assert role["role_id"] == "general.specialist"
