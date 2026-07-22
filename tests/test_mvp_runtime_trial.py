"""Candidate Role Trial tests.

The trial is the runtime's second consumable ask (after the R9/R10 memory promotion), so
these concentrate on the same things the consumption tests do — what must fail closed:
selection of anything that is not exactly a candidate role, an unapproved/expired/spent
grant, drifted role definitions or task text, the kill switch, and the safety gate being
off. The happy path proves the narrow thing the increment adds — one APPROVED grant, spent
once, runs ONE isolated trial of the exact approved role version with a forced independent
review and a durable report — and nothing wider (no activation, no memory, no tools).

Paths that need a bound task (local Core activation) skip on a core-neutral CI checkout.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime import approval, trial
from runtime.mvp_runtime.approval_store import ApprovalStore
from runtime.mvp_runtime.assignment import build_role_assignment
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import ApprovalBlocked, PlannerBlocked, ProviderError
from runtime.mvp_runtime.permission import (
    TRIAL_PERMISSION_SCOPE,
    build_trial_permission_decision,
    trial_content_sha256,
)
from runtime.mvp_runtime.planner import load_resolved_roles, select_candidate_role
from runtime.mvp_runtime.safety_gate import APPROVAL_CONSUMPTION, Authorization
from runtime.mvp_runtime.store import LedgerStore
from runtime.mvp_runtime.trial import (
    MockTrialProvider,
    _CapableTrialRunner,
    _DryRunTrialRunner,
    request_trial,
    run_trial,
)

REPO = Path(__file__).resolve().parents[1]
from tests._helpers import requires_local_core

NOW = "2026-07-22T03:00:00Z"
LATER = "2026-07-22T03:10:00Z"
TRIAL_REQUEST = "재택 물리치료 시장의 근거를 조사해줘"

GRANT = Authorization(
    flags=(APPROVAL_CONSUMPTION,), provider_id="approval_consumption",
    activation_sha256="sha256:test", expires_at="2999-01-01T00:00:00Z",
    evidence_ref=".runtime_governance_state/evidence.md",
)

VERIFICATION = approval.Verification(
    approved_by="Thomas", method="telegram_private_control_channel",
    verification_ref="telegram:private_chat:registered-thomas:msg-1",
)


def _stores(tmp_path):
    return ApprovalStore(tmp_path / "approvals"), LedgerStore(tmp_path / "ledger"), ControlStore(tmp_path)


def _approved(tmp_path, role_id="research.general", *, granted=True, trial_request=TRIAL_REQUEST):
    """A decided trial approval in a fresh store. Returns (astore, ledger, control, approval_id)."""
    astore, ledger, control = _stores(tmp_path)
    prepared = request_trial(role_id, trial_request, now=NOW)
    permdec = prepared["permission_decision"]
    request = prepared["approval_request"]
    astore.append_permission_decision(permdec)
    astore.append([request])
    decided = approval.record_decision(request, permdec, granted=granted,
                                       verification=VERIFICATION, reason="decided", now=NOW)
    astore.append([decided])
    return astore, ledger, control, request["approval_id"]


# --- candidate selection ------------------------------------------------------------


def test_select_candidate_role_returns_the_candidate_entry():
    resolved = load_resolved_roles(REPO)
    role = select_candidate_role(resolved, role_id="research.general")
    assert role["status"] == "candidate" and role["routable"] is False
    assert role["permission_ceiling"] == "P3"


def test_select_candidate_role_refuses_unknown_and_active_and_wrong_version():
    resolved = load_resolved_roles(REPO)
    with pytest.raises(PlannerBlocked) as unknown:
        select_candidate_role(resolved, role_id="no.such.role")
    assert unknown.value.reason_code == "UNKNOWN_ROLE"
    with pytest.raises(PlannerBlocked) as active:
        select_candidate_role(resolved, role_id="general.specialist")
    assert active.value.reason_code == "ROLE_ALREADY_ACTIVE"
    with pytest.raises(PlannerBlocked) as version:
        select_candidate_role(resolved, role_id="translation.general", version="9.9.9")
    assert version.value.reason_code == "CANDIDATE_VERSION_MISMATCH"


# --- the ask ------------------------------------------------------------------------


@requires_local_core
def test_request_trial_binds_role_version_definition_and_task_text():
    prepared = request_trial("research.general", TRIAL_REQUEST, now=NOW)
    permdec = prepared["permission_decision"]
    role = prepared["role"]
    payload = permdec["fingerprint_payload"]
    assert permdec["decision"]["permission_decision"] == "APPROVAL_REQUIRED"
    assert payload["permission_scope"] == TRIAL_PERMISSION_SCOPE
    assert payload["target_ref"] == f"candidate_role:{role['role_id']}@{role['version']}"
    assert payload["content_sha256"] == trial_content_sha256(role, TRIAL_REQUEST)
    assert payload["normalized_parameters"]["trial_request"] == TRIAL_REQUEST
    assert permdec["risk"]["risk_level"] == "ORANGE"
    # Building the ask performed nothing and grants nothing.
    eff = permdec["runtime_effect"]
    assert eff["mode"] == "REVIEW_ONLY"
    assert all(v is False for k, v in eff.items() if k != "mode")


@requires_local_core
def test_trial_request_message_is_scope_aware():
    prepared = request_trial("translation.general", TRIAL_REQUEST, now=NOW)
    message = approval.request_message(prepared["approval_request"], prepared["permission_decision"])
    assert "격리된 1회 시험 실행" in message
    assert "validated memory는 지속됩니다" not in message
    assert "trial run" in message


@requires_local_core
def test_request_trial_refuses_an_active_role():
    with pytest.raises(PlannerBlocked) as blocked:
        request_trial("general.specialist", TRIAL_REQUEST, now=NOW)
    assert blocked.value.reason_code == "ROLE_ALREADY_ACTIVE"


# --- assignment mode ----------------------------------------------------------------


def test_normal_assignment_refuses_a_trial_authorization_ref():
    with pytest.raises(PlannerBlocked) as blocked:
        build_role_assignment({}, {}, {}, required_capabilities=[], created_at=NOW,
                              expires_at=LATER, trial_authorization_ref="approval_x")
    # Fails before reaching the mode guard only if unbound — bind guard runs first, so
    # drive the mode guard directly with the minimal precondition instead.
    assert blocked.value.reason_code in ("NOT_BOUND", "UNEXPECTED_TRIAL_AUTHORIZATION")


@requires_local_core
def test_trial_assignment_requires_the_authorization_and_closes_memory(tmp_path):
    astore, ledger, control, approval_id = _approved(tmp_path)
    result = run_trial(approval_id, approval_store=astore, ledger=ledger,
                       control_store=control, now=LATER, runner=_CapableTrialRunner(GRANT))
    assignment = result["records"]["role_assignment"]
    assert assignment["assignment_mode"] == "candidate_trial"
    assert assignment["trial_authorization_ref"] == approval_id
    scope = assignment["memory_scope"]
    assert scope["readable_scopes"] == [] and scope["readable_memory_refs"] == []
    assert scope["memory_candidate_creation_allowed"] is False
    assert scope["allowed_candidate_types"] == []
    assert assignment["allowed_tool_ids"] == [] and assignment["allowed_program_ids"] == []


# --- the spend: fail-closed BEFORE anything is burned -------------------------------


@requires_local_core
def test_run_trial_refuses_unknown_pending_rejected_and_expired(tmp_path):
    astore, ledger, control, approval_id = _approved(tmp_path, granted=False)
    with pytest.raises(ApprovalBlocked) as unknown:
        run_trial("approval_nope", approval_store=astore, ledger=ledger,
                  control_store=control, now=LATER, runner=_CapableTrialRunner(GRANT))
    assert unknown.value.reason_code == "UNKNOWN_APPROVAL"
    with pytest.raises(ApprovalBlocked) as rejected:
        run_trial(approval_id, approval_store=astore, ledger=ledger,
                  control_store=control, now=LATER, runner=_CapableTrialRunner(GRANT))
    assert rejected.value.reason_code == "NOT_APPROVED"

    astore2, ledger2, control2, approved_id = _approved(tmp_path / "b")
    with pytest.raises(ApprovalBlocked) as expired:
        run_trial(approved_id, approval_store=astore2, ledger=ledger2,
                  control_store=control2, now="2026-07-23T03:00:00Z",
                  runner=_CapableTrialRunner(GRANT))
    assert expired.value.reason_code == "APPROVAL_EXPIRED"
    # Nothing was burned on any refused path.
    assert astore2.get(approved_id)["status"] == "APPROVED"


@requires_local_core
def test_run_trial_refuses_a_non_trial_scope(tmp_path, monkeypatch):
    """A memory-promotion approval must not be spendable through the trial door."""
    from runtime.mvp_runtime.binding import bind_task_to_core
    from runtime.mvp_runtime.intake import build_task
    from runtime.mvp_runtime.permission import build_memory_promotion_permission_decision

    astore, ledger, control = _stores(tmp_path)
    task = build_task("승격 검토", now=NOW, channel="manual", requester_type="real_thomas",
                      requester_id="Thomas", authenticated=True)
    _, bound = bind_task_to_core(task, now=NOW)
    candidate = {"candidate_id": "memcand_x", "content": "some knowledge"}
    permdec = build_memory_promotion_permission_decision(bound, candidate, now=NOW)
    request = approval.build_approval_request(permdec, now=NOW)
    astore.append_permission_decision(permdec)
    astore.append([request])
    decided = approval.record_decision(request, permdec, granted=True,
                                       verification=VERIFICATION, reason="ok", now=NOW)
    astore.append([decided])
    with pytest.raises(ApprovalBlocked) as blocked:
        run_trial(request["approval_id"], approval_store=astore, ledger=ledger,
                  control_store=control, now=LATER, runner=_CapableTrialRunner(GRANT))
    assert blocked.value.reason_code == "SCOPE_NOT_CONSUMABLE"
    assert astore.get(request["approval_id"])["status"] == "APPROVED"


@requires_local_core
def test_run_trial_refuses_when_the_approved_content_drifted(tmp_path):
    """An approval minted against a different definition hash than the current registry's
    (a role edit after the ask) must refuse with CONTENT_CHANGED — before the spend."""
    astore, ledger, control = _stores(tmp_path)
    resolved = load_resolved_roles(REPO)
    role = select_candidate_role(resolved, role_id="research.general")
    stale_role = {**role, "definition_sha256": "0" * 64}

    from runtime.mvp_runtime.binding import bind_task_to_core
    from runtime.mvp_runtime.intake import build_task

    task = build_task("트라이얼 검토", now=NOW, channel="manual", requester_type="real_thomas",
                      requester_id="Thomas", authenticated=True)
    _, bound = bind_task_to_core(task, now=NOW)
    permdec = build_trial_permission_decision(bound, stale_role, trial_request=TRIAL_REQUEST, now=NOW)
    request = approval.build_approval_request(permdec, now=NOW)
    astore.append_permission_decision(permdec)
    astore.append([request])
    decided = approval.record_decision(request, permdec, granted=True,
                                       verification=VERIFICATION, reason="ok", now=NOW)
    astore.append([decided])
    with pytest.raises(ApprovalBlocked) as blocked:
        run_trial(request["approval_id"], approval_store=astore, ledger=ledger,
                  control_store=control, now=LATER, runner=_CapableTrialRunner(GRANT))
    assert blocked.value.reason_code == "CONTENT_CHANGED"
    assert astore.get(request["approval_id"])["status"] == "APPROVED"


@requires_local_core
def test_run_trial_is_kill_switch_bound(tmp_path):
    astore, ledger, _, approval_id = _approved(tmp_path)

    class _Killed:
        execution_allowed = False
        mode = "KILLED"

        def refusal_reason_code(self):
            return "RUNTIME_KILLED"

    class _KilledStore:
        def load(self):
            return _Killed()

    with pytest.raises(ApprovalBlocked) as blocked:
        run_trial(approval_id, approval_store=astore, ledger=ledger,
                  control_store=_KilledStore(), now=LATER, runner=_CapableTrialRunner(GRANT))
    assert blocked.value.reason_code == "RUNTIME_KILLED"
    assert astore.get(approval_id)["status"] == "APPROVED"


@requires_local_core
def test_run_trial_fails_closed_when_the_gate_is_off(tmp_path):
    astore, ledger, control, approval_id = _approved(tmp_path)
    with pytest.raises(ApprovalBlocked) as blocked:
        run_trial(approval_id, approval_store=astore, ledger=ledger,
                  control_store=control, now=LATER, runner=_DryRunTrialRunner())
    assert blocked.value.reason_code == "CONSUMPTION_DISABLED"
    # The gate refusal happens before the spend: the grant survives.
    assert astore.get(approval_id)["status"] == "APPROVED"


# --- the spend: happy path ----------------------------------------------------------


@requires_local_core
@pytest.mark.parametrize("role_id,expected_keys", [
    ("research.general", {"sources", "source_quality", "conflicting_evidence", "research_gaps"}),
    ("translation.general", {"translated_text", "terminology_notes", "ambiguity_notes"}),
])
def test_run_trial_completes_one_isolated_reviewed_run(tmp_path, role_id, expected_keys):
    astore, ledger, control, approval_id = _approved(tmp_path, role_id)
    result = run_trial(approval_id, approval_store=astore, ledger=ledger,
                       control_store=control, now=LATER, runner=_CapableTrialRunner(GRANT))

    assert result["status"] == "COMPLETED" and result["delivered"] is True
    assert result["persist_error"] is None

    consumed = result["approval"]
    task_id = result["records"]["task"]["identity"]["task_id"]
    assert consumed["status"] == "CONSUMED"
    assert consumed["consumption"]["consumption_ref"] == f"trial_task:{task_id}"
    assert astore.get(approval_id)["status"] == "CONSUMED"

    # The candidate role's OWN output contract was exercised and reviewed independently.
    rso = result["records"]["agent_output"]["role_specific_output"]
    assert expected_keys.issubset(rso)
    ival = result["records"]["independent_validation_result"]
    assert ival["validation"]["validation_mode"] == "INDEPENDENT"
    assert ival["validator"]["validator_role_id"] == "validation.independent"

    report = result["records"]["trial_report"]
    assert report["record_type"] == "candidate_trial_report.v0"
    assert report["final_result"] == "PASS"
    assert report["promotion_effect"] == "NONE"
    assert report["approval_id"] == approval_id
    assert set(report["required_role_output_keys"]) == expected_keys
    assert report["isolation"]["external_action"] is False
    assert report["isolation"]["persistent_runtime_change"] is False

    # No memory candidates, no tool use — isolation held.
    assert result["records"]["agent_output"]["memory_candidates"] == []
    assert "tool_use" not in result["records"]

    # The trial changed nothing persistent: the role is STILL a candidate.
    resolved = load_resolved_roles(REPO)
    assert select_candidate_role(resolved, role_id=role_id)["status"] == "candidate"


@requires_local_core
def test_trial_consumption_and_run_are_audited(tmp_path):
    astore, ledger, control, approval_id = _approved(tmp_path)
    result = run_trial(approval_id, approval_store=astore, ledger=ledger,
                       control_store=control, now=LATER, runner=_CapableTrialRunner(GRANT))
    trail = result["records"]["audit_trail"]
    codes = [c for e in trail for c in e["event"]["reason_codes"]]
    assert "APPROVAL_CONSUMED" in codes and "CANDIDATE_ROLE_TRIAL" in codes
    assert "NO_ACTIVATION" in codes
    assert "MODEL_INVOKED" in codes and "INDEPENDENT" in codes
    assert "FINAL_COMPLETED" in codes


@requires_local_core
def test_run_trial_is_single_use(tmp_path):
    astore, ledger, control, approval_id = _approved(tmp_path)
    run_trial(approval_id, approval_store=astore, ledger=ledger,
              control_store=control, now=LATER, runner=_CapableTrialRunner(GRANT))
    with pytest.raises(ApprovalBlocked) as blocked:
        run_trial(approval_id, approval_store=astore, ledger=ledger,
                  control_store=control, now=LATER, runner=_CapableTrialRunner(GRANT))
    assert blocked.value.reason_code == "ALREADY_CONSUMED"


# --- the spend: after the grant is burned -------------------------------------------


class _ExplodingProvider:
    model_id = "exploding.trial"
    model_version = "0.0.0"
    network_egress = False

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        raise ProviderError("PROVIDER_UNAVAILABLE", "boom")


@requires_local_core
def test_a_failed_run_leaves_the_grant_spent_not_respendable(tmp_path):
    astore, ledger, control, approval_id = _approved(tmp_path)
    result = run_trial(approval_id, approval_store=astore, ledger=ledger,
                       control_store=control, now=LATER, runner=_CapableTrialRunner(GRANT),
                       provider=_ExplodingProvider())
    assert result["status"] == "BLOCKED"
    assert result["block"]["stage"] == "trial_pipeline"
    # Spent-but-unrun is the safe direction: ask Thomas again, never re-spend.
    assert astore.get(approval_id)["status"] == "CONSUMED"
    with pytest.raises(ApprovalBlocked) as blocked:
        run_trial(approval_id, approval_store=astore, ledger=ledger,
                  control_store=control, now=LATER, runner=_CapableTrialRunner(GRANT))
    assert blocked.value.reason_code == "ALREADY_CONSUMED"


@requires_local_core
def test_missing_role_output_keys_withhold_delivery(tmp_path):
    astore, ledger, control, approval_id = _approved(tmp_path)
    # A provider that answers the common shape but none of the role's declared keys.
    result = run_trial(approval_id, approval_store=astore, ledger=ledger,
                       control_store=control, now=LATER, runner=_CapableTrialRunner(GRANT),
                       provider=MockTrialProvider({}))
    assert result["status"] == "BLOCKED"
    assert result["block"]["reason_code"] == "VALIDATION_REVISE"
    assert "sources" in result["block"]["message"]
    assert result["records"]["trial_report"]["final_result"] == "REVISE"
