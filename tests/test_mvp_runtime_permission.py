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
from runtime.mvp_runtime import permission
from runtime.mvp_runtime.permission import (
    build_permission_decision,
    build_search_permission_decision,
    build_write_permission_decision,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXED_NOW = "2026-07-15T09:00:00Z"

from tests._helpers import requires_local_core


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
    assert rec["schema_version"] == "permission_decision.v0.4"
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


# --- R8: the first EXECUTE_AND_REPORT action -------------------------------------


@requires_local_core
def test_write_decision_is_execute_and_report_not_allow():
    """The runtime's first non-ALLOW action. The disposition is the canonical Governance
    Policy's, not a local choice, and the record still grants nothing."""
    rec = build_write_permission_decision(_bound_task(), role_permission_ceiling="P3", now=FIXED_NOW)
    assert rec["decision"]["permission_decision"] == "EXECUTE_AND_REPORT"
    assert rec["risk"]["policy_disposition"] == "EXECUTE_AND_REPORT"
    assert rec["fingerprint_payload"]["permission_scope"] == "WORKSPACE_REVERSIBLE_WRITE"
    assert rec["authority"]["required_permission_level"] == "P3"
    # EXECUTE_AND_REPORT acts without pre-approval — but grants nothing on its own.
    assert rec["approval"] == {"approval_required": False, "approval_id": None, "approval_status": "NOT_REQUIRED"}
    eff = rec["runtime_effect"]
    assert eff["mode"] == "REVIEW_ONLY"
    assert all(v is False for k, v in eff.items() if k != "mode")


@requires_local_core
def test_write_is_refused_below_the_p3_create_ceiling():
    """A P2 role cannot obtain a write grant: the authority invariant decides, not the
    disposition. This is what keeps the validator (P2) from ever writing."""
    with pytest.raises(PlannerBlocked) as exc:
        build_write_permission_decision(_bound_task(), role_permission_ceiling="P2", now=FIXED_NOW)
    assert exc.value.reason_code == "AUTHORITY_INSUFFICIENT"


@requires_local_core
def test_unimplemented_approval_required_scopes_stay_refused():
    """R9 lets an APPROVAL_REQUIRED decision be BUILT (it is the object an Approval Request
    binds to) — but only for the one scope the runtime can actually ask about. The rest name
    actions it has no implementation for, so a request for one would be an ask it could never
    honour."""
    for scope in ("PUBLICATION", "EXTERNAL_COMMUNICATION", "DESTRUCTIVE_CHANGE"):
        with pytest.raises(PlannerBlocked) as exc:
            _decide(_bound_task(), permission_scope=scope, required_permission_level="P3",
                    approval_id="approval_probe")
        assert exc.value.reason_code == "NOT_ALLOWED"


@requires_local_core
def test_approval_required_is_buildable_but_never_executable():
    """The R9 boundary: building the record is not acting on it. An APPROVAL_REQUIRED
    decision exists so Thomas can be asked; the runtime still has no path to perform it
    (approval consumption is gate-pinned unimplemented)."""
    assert "APPROVAL_REQUIRED" in permission._BUILDABLE_DISPOSITIONS
    assert "APPROVAL_REQUIRED" not in permission._EXECUTABLE_DISPOSITIONS


@requires_local_core
def test_unimplemented_execute_and_report_scopes_stay_refused():
    """Widening the disposition must not silently admit the OTHER scopes governance prices
    at EXECUTE_AND_REPORT and the runtime has no implementation for."""
    for scope in ("GIT_AGENT_BRANCH_CHANGE", "LOCAL_BUILD_TEST"):
        with pytest.raises(PlannerBlocked) as exc:
            _decide(_bound_task(), permission_scope=scope, required_permission_level="P3")
        assert exc.value.reason_code == "NOT_ALLOWED"


@requires_local_core
def test_write_is_a_distinct_action_from_analysis_and_search():
    bound = _bound_task()
    write = build_write_permission_decision(bound, role_permission_ceiling="P3", now=FIXED_NOW)
    search = build_search_permission_decision(bound, role_permission_ceiling="P3", now=FIXED_NOW)
    analysis = _decide(bound)
    ids = {write["permission_decision_id"], search["permission_decision_id"], analysis["permission_decision_id"]}
    fps = {write["action_fingerprint"], search["action_fingerprint"], analysis["action_fingerprint"]}
    assert len(ids) == 3
    assert len(fps) == 3


# --- LP4: live-order permission decision (P5, EXECUTE_AND_REPORT, grants nothing) ---
# These use a SYNTHETIC bound task (build_permission_decision only checks the ccb- prefix,
# not a real activated Core), so unlike the sibling tests above they run everywhere — the
# live-order path must be exercised on core-neutral CI, not only where a local Core exists.

from runtime.read_only_kernel import integrity
from runtime.mvp_runtime.permission import build_live_order_permission_decision

_SYNTH_TID = "task_live_order_unit_001"


def _synthetic_bound():
    return {
        "identity": {"task_id": _SYNTH_TID, "trace_id": "trace_live_order_unit_001",
                     "task_revision": 1},
        "context": {"core_context_binding_id": f"ccb-unit:{_SYNTH_TID}:r1"},
    }


def _order_fp(**over):
    seed = {"symbol": "BTCUSDT", "side": "SELL", "notional": "55.00", "stage": "live"}
    seed.update(over)
    return integrity.sha256_record(seed)


def _live_order(ceiling="P5", **over):
    params = dict(role_permission_ceiling=ceiling, symbol="BTCUSDT", side="SELL",
                  notional_usdt=55.0, order_fingerprint=_order_fp(), now=FIXED_NOW)
    params.update(over)
    return build_live_order_permission_decision(_synthetic_bound(), **params)


def test_live_order_decision_is_execute_and_report_at_p5():
    rec = _live_order()
    assert rec["schema_version"] == "permission_decision.v0.4"
    assert rec["fingerprint_payload"]["permission_scope"] == "FINANCIAL_APPROVED_TRADING_USE"
    assert rec["decision"]["permission_decision"] == "EXECUTE_AND_REPORT"
    assert rec["risk"]["policy_disposition"] == "EXECUTE_AND_REPORT"
    assert rec["authority"]["required_permission_level"] == "P5"
    assert rec["authority"]["authority_sufficient"] is True


def test_live_order_decision_grants_nothing():
    """Building the decision is REVIEW_ONLY evidence: every runtime-effect flag stays false.
    Nothing here sends an order — that is LP4's gated adapter, which does not exist yet."""
    effect = _live_order()["runtime_effect"]
    assert effect["mode"] == "REVIEW_ONLY"
    assert effect["external_execution_allowed"] is False
    assert effect["financial_execution_allowed"] is False
    assert all(v is False for k, v in effect.items() if k != "mode")


def test_live_order_refused_below_p5():
    """A P5 action needs a P5 actor. Every ordinary role (P3 or lower) is refused here by the
    authority invariant — which is why a dedicated execution.live_trader role is required."""
    for ceiling in ("P2", "P3", "P4"):
        with pytest.raises(PlannerBlocked) as exc:
            _live_order(ceiling=ceiling)
        assert exc.value.reason_code == "AUTHORITY_INSUFFICIENT"


def test_live_order_notional_is_a_decimal_string_not_a_float():
    """The action fingerprint forbids floats; the notional must canonicalize deterministically."""
    params = _live_order()["fingerprint_payload"]["normalized_parameters"]
    assert params["order_notional_usdt"] == "55.00"
    assert isinstance(params["order_notional_usdt"], str)


def test_live_order_binds_this_exact_order():
    """A different order (side, notional, or its own fingerprint) yields a different action
    fingerprint, so a decision cannot be reused for another order."""
    base = _live_order()["action_fingerprint"]
    assert _live_order(side="BUY")["action_fingerprint"] != base
    assert _live_order(notional_usdt=120.0)["action_fingerprint"] != base
    assert _live_order(order_fingerprint=_order_fp(symbol="ETHUSDT"))["action_fingerprint"] != base


def test_live_order_is_distinct_from_a_write():
    rec = _live_order()
    write = build_write_permission_decision(_synthetic_bound(), role_permission_ceiling="P5", now=FIXED_NOW)
    assert rec["action_fingerprint"] != write["action_fingerprint"]
    assert rec["fingerprint_payload"]["action_type"] == "exchange.order.place"
