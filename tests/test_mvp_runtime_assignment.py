"""R2.2 Role Assignment tests + full planner-pipeline coherence.

Happy path needs a bound task + permission decision (local Core activation), so it
skips on a core-neutral CI checkout; the unbound fail-closed case runs everywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.assignment import build_role_assignment
from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL, bind_task_to_core
from runtime.mvp_runtime.errors import PlannerBlocked
from runtime.mvp_runtime.intake import build_task
from runtime.mvp_runtime.permission import build_permission_decision
from runtime.mvp_runtime.planner import classify_task, load_resolved_roles, select_role

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_POINTER = REPO_ROOT / DEFAULT_POINTER_REL
NOW = "2026-07-15T09:00:00Z"
EXPIRES = "2026-07-15T09:30:00Z"

requires_local_core = pytest.mark.skipif(not LOCAL_POINTER.is_file(), reason="no local Core activation")


def _pipeline():
    """Run the full planner pipeline and return every record."""
    task = build_task("이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송", now=NOW)
    decision = classify_task(task)
    _, bound = bind_task_to_core(task, now=NOW)
    resolved = load_resolved_roles(REPO_ROOT)
    role = select_role(
        resolved,
        required_capabilities=decision["required_capabilities"],
        required_permission_level=decision["authority"]["required_permission_level"],
    )
    pd = build_permission_decision(
        bound,
        permission_scope=decision["permission_scope"],
        required_permission_level=decision["authority"]["required_permission_level"],
        role_permission_ceiling=role["permission_ceiling"],
        now=NOW,
    )
    assignment = build_role_assignment(
        bound, role, pd,
        required_capabilities=decision["required_capabilities"],
        created_at=NOW, expires_at=EXPIRES,
    )
    return bound, role, pd, assignment


def test_unbound_task_blocks_everywhere():
    task = build_task("분석해줘", now=NOW)  # unbound
    with pytest.raises(PlannerBlocked) as exc:
        build_role_assignment(
            task, {"role_id": "general.specialist"}, {"decision": {}},
            required_capabilities=["analysis"], created_at=NOW, expires_at=EXPIRES,
        )
    assert exc.value.reason_code == "NOT_BOUND"


@requires_local_core
def test_assignment_is_schema_valid_and_role_bound():
    _, role, pd, a = _pipeline()
    assert a["schema_version"] == "role_assignment.v0.2"
    assert a["role_id"] == "general.specialist"
    assert a["role_version"] == "0.3.0"
    assert a["assignment_status"] == "ASSIGNED"
    assert a["escalation_target"] == "thomas_prime"


@requires_local_core
def test_authority_invariant_and_no_tools_programs():
    _, _, _, a = _pipeline()
    auth = a["authority"]
    order = ["P0", "P1", "P2", "P3", "P4", "P5", "P6"]
    r, e, g, c = (auth["required_permission_level"], auth["effective_permission_level"],
                  auth["assignment_granted_permission_level"], auth["role_permission_ceiling"])
    assert order.index(r) <= order.index(e) <= order.index(g) <= order.index(c)
    assert a["allowed_tool_ids"] == [] and a["allowed_program_ids"] == []
    limits = a["execution_budget"]["limits"]
    assert limits["max_tool_calls"] == 0 and limits["max_program_calls"] == 0
    assert a["memory_scope"]["validated_memory_write_allowed"] is False
    assert a["memory_scope"]["core_memory_write_allowed"] is False


@requires_local_core
def test_pipeline_records_are_coherent():
    bound, _, pd, a = _pipeline()
    tid = bound["identity"]["task_id"]
    ccb = bound["context"]["core_context_binding_id"]
    # task_id / ccb are byte-identical across task, permission decision, and assignment.
    assert pd["task_id"] == tid and a["task_id"] == tid
    assert pd["core_context_binding_id"] == ccb and a["core_context_binding_id"] == ccb
    # the assignment references exactly this permission decision.
    assert a["permission"]["permission_decision_ref"] == pd["permission_decision_id"]


@requires_local_core
def test_malformed_role_blocks():
    bound, _, pd, _ = _pipeline()
    with pytest.raises(PlannerBlocked) as exc:
        build_role_assignment(
            bound, {"role_id": "x"}, pd,  # missing version / definition_path
            required_capabilities=["research"], created_at=NOW, expires_at=EXPIRES,
        )
    assert exc.value.reason_code == "INVALID_ROLE"


@requires_local_core
def test_capability_exceeding_role_blocks():
    bound, role, pd, _ = _pipeline()
    with pytest.raises(PlannerBlocked) as exc:
        build_role_assignment(
            bound, role, pd,
            required_capabilities=["independent_validation"],  # not a general.specialist capability
            created_at=NOW, expires_at=EXPIRES,
        )
    assert exc.value.reason_code == "CAPABILITY_EXCEEDS_ROLE"


@requires_local_core
def test_non_allow_permission_blocks():
    bound, role, pd, _ = _pipeline()
    tampered = dict(pd)
    tampered["decision"] = {"permission_decision": "APPROVAL_REQUIRED"}
    with pytest.raises(PlannerBlocked) as exc:
        build_role_assignment(
            bound, role, tampered,
            required_capabilities=["research", "analysis"], created_at=NOW, expires_at=EXPIRES,
        )
    assert exc.value.reason_code == "PERMISSION_NOT_ALLOW"
