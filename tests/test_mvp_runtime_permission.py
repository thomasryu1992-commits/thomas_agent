"""R2.2 PermissionDecision tests.

The happy path needs a bound task (local Core activation), so it is skipped on a
core-neutral CI checkout; the unbound fail-closed case runs everywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL, bind_task_to_core
from runtime.mvp_runtime.errors import PlannerBlocked
from runtime.mvp_runtime.intake import build_task
from runtime.mvp_runtime.permission import (
    build_permission_decision,
    build_search_permission_decision,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_POINTER = REPO_ROOT / DEFAULT_POINTER_REL
FIXED_NOW = "2026-07-15T09:00:00Z"

requires_local_core = pytest.mark.skipif(
    not LOCAL_POINTER.is_file(), reason="no local Core activation"
)


def _bound_task():
    task = build_task("이 사업 아이디어를 분석해줘", now=FIXED_NOW)
    _, bound = bind_task_to_core(task, now=FIXED_NOW)
    return bound


def _decide(bound, **overrides):
    params = dict(
        permission_scope="INTERNAL_ANALYSIS",
        required_permission_level="P2",
        role_permission_ceiling="P3",
        now=FIXED_NOW,
    )
    params.update(overrides)
    return build_permission_decision(bound, **params)


def test_unbound_task_blocks_everywhere():
    task = build_task("분석해줘", now=FIXED_NOW)  # RECEIVED, unbound
    with pytest.raises(PlannerBlocked) as exc:
        _decide(task)
    assert exc.value.reason_code == "NOT_BOUND"


@requires_local_core
def test_allow_decision_is_schema_and_semantics_valid():
    rec = _decide(_bound_task())
    assert rec["schema_version"] == "permission_decision.v0.3"
    assert rec["permission_decision_id"].startswith("permdec_")
    assert rec["decision"]["permission_decision"] == "ALLOW"
    assert rec["risk"]["policy_disposition"] == "ALLOW"
    assert rec["authority"]["authority_sufficient"] is True
    assert rec["approval"]["approval_required"] is False


@requires_local_core
def test_allow_is_not_an_executor_token():
    rec = _decide(_bound_task())
    eff = rec["runtime_effect"]
    assert eff["mode"] == "REVIEW_ONLY"
    assert all(v is False for k, v in eff.items() if k != "mode")


@requires_local_core
def test_permission_decision_is_deterministic():
    bound = _bound_task()
    assert _decide(bound)["permission_decision_id"] == _decide(bound)["permission_decision_id"]


@requires_local_core
def test_unknown_scope_blocks():
    with pytest.raises(PlannerBlocked) as exc:
        _decide(_bound_task(), permission_scope="NONSENSE_SCOPE")
    assert exc.value.reason_code == "UNKNOWN_SCOPE"


@requires_local_core
@pytest.mark.parametrize("scope", ["EXTERNAL_COMMUNICATION", "PUBLICATION", "FINANCIAL_NEW_COMMITMENT", "AUTHORITY_ESCALATION"])
def test_non_allow_scope_fails_closed_explicitly(scope):
    # Non-ALLOW dispositions must be refused up front (not via a downstream schema accident).
    with pytest.raises(PlannerBlocked) as exc:
        _decide(_bound_task(), permission_scope=scope)
    assert exc.value.reason_code == "NOT_ALLOWED"


@requires_local_core
def test_insufficient_authority_blocks_explicitly():
    with pytest.raises(PlannerBlocked) as exc:
        _decide(_bound_task(), required_permission_level="P5", role_permission_ceiling="P3")
    assert exc.value.reason_code == "AUTHORITY_INSUFFICIENT"


@requires_local_core
def test_invalid_level_blocks():
    with pytest.raises(PlannerBlocked) as exc:
        _decide(_bound_task(), required_permission_level="P9")
    assert exc.value.reason_code == "INVALID_LEVEL"


# --- R3: read-only search action modelled as INTERNAL_READ ALLOW ------------

def test_search_unbound_task_blocks_everywhere():
    task = build_task("검색해줘", now=FIXED_NOW)  # RECEIVED, unbound
    with pytest.raises(PlannerBlocked) as exc:
        build_search_permission_decision(task, role_permission_ceiling="P3", now=FIXED_NOW)
    assert exc.value.reason_code == "NOT_BOUND"


@requires_local_core
def test_search_decision_is_allow_internal_read_at_p1():
    rec = build_search_permission_decision(_bound_task(), role_permission_ceiling="P3", now=FIXED_NOW)
    payload = rec["fingerprint_payload"]
    assert rec["decision"]["permission_decision"] == "ALLOW"
    assert rec["risk"]["policy_disposition"] == "ALLOW"
    assert payload["permission_scope"] == "INTERNAL_READ"
    assert payload["action_type"] == "internal.read.search"
    assert payload["tool_id"] == "search.readonly"
    assert payload["target_ref"].endswith(":search")
    assert rec["authority"]["required_permission_level"] == "P1"
    assert rec["authority"]["authority_sufficient"] is True


@requires_local_core
def test_search_decision_is_not_an_executor_token():
    rec = build_search_permission_decision(_bound_task(), role_permission_ceiling="P3", now=FIXED_NOW)
    eff = rec["runtime_effect"]
    assert eff["mode"] == "REVIEW_ONLY"
    assert all(v is False for k, v in eff.items() if k != "mode")


@requires_local_core
def test_search_and_analysis_are_distinct_actions():
    bound = _bound_task()
    search = build_search_permission_decision(bound, role_permission_ceiling="P3", now=FIXED_NOW)
    analysis = _decide(bound)
    # Different action identity => different fingerprint and decision id, even for one task.
    assert search["action_fingerprint"] != analysis["action_fingerprint"]
    assert search["permission_decision_id"] != analysis["permission_decision_id"]


@requires_local_core
def test_search_decision_is_deterministic():
    bound = _bound_task()
    a = build_search_permission_decision(bound, role_permission_ceiling="P3", now=FIXED_NOW)
    b = build_search_permission_decision(bound, role_permission_ceiling="P3", now=FIXED_NOW)
    assert a["permission_decision_id"] == b["permission_decision_id"]
    assert a["action_fingerprint"] == b["action_fingerprint"]
