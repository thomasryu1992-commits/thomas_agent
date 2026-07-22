"""Program-request path tests: invocation evidence, never invocation.

Pure local state + closed schemas — no Core needed. The request resolves the real
committed PROGRAM_REGISTRY (candidate programs, none active), so the honest verdict is
always fail-closed BLOCK while no Program is active.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.errors import PlannerBlocked, ProgramizationBlocked
from runtime.mvp_runtime.program_request import create_program_request

from tests.test_mvp_runtime_programization import (
    NOW,
    _draft_candidate,
    _shadow,
    _t,
)


def _accepted_candidate(tmp_path):
    store, cid = _draft_candidate(tmp_path)
    _t(store, cid, "ready")
    _t(store, cid, "validate")
    _shadow(store, cid, "PASS")
    _t(store, cid, "accept")
    return store, cid


def _request(store, cid, **kw):
    kw.setdefault("program_id", "schema.validator")
    kw.setdefault("program_version", "0.1.0")
    kw.setdefault("requested_by", "thomas")
    kw.setdefault("reason", "request the reviewed slice as a program")
    kw.setdefault("now", NOW)
    return create_program_request(store, cid, **kw)


def test_request_for_registered_candidate_program_is_block_evidence(tmp_path):
    store, cid = _accepted_candidate(tmp_path)
    request = _request(store, cid)

    assert request["validation"]["review_result"] == "BLOCK"
    assert request["lifecycle"]["review_status"] == "BLOCKED"          # schema allOf holds
    assert request["permission"]["permission_decision"] == "BLOCK"
    reasons = request["validation"]["block_reasons"]
    assert "program_not_active_and_enabled" in reasons
    assert "runtime_implementation_unavailable" in reasons
    assert "assignment_program_call_budget_is_zero" in reasons
    # Registered candidate program: the snapshot is the real registry state.
    assert request["resource"]["registry_status"] == "candidate"
    assert request["resource"]["registry_enabled"] is False
    assert request["invocation"]["permission_scope"] == "DISABLED_RESOURCE_EXECUTION"
    # The runtime-effect guards are schema constants — nothing here can execute.
    assert request["runtime_effect"]["program_execution_allowed"] is False
    assert request["runtime_effect"]["mode"] == "REVIEW_ONLY"


def test_request_binds_a_real_block_permission_decision(tmp_path):
    store, cid = _accepted_candidate(tmp_path)
    request = _request(store, cid)
    wrapper = store.read_requests()[0]
    decision = wrapper["permission_decision"]
    assert decision["decision"]["permission_decision"] == "BLOCK"
    assert decision["risk"]["policy_disposition"] == "BLOCK"
    assert request["permission"]["permission_decision_id"] == decision["permission_decision_id"]
    assert request["permission"]["action_fingerprint"] == decision["action_fingerprint"]
    # The decision anchors to the REAL originating task of the pattern's last valid
    # observation — the same lineage the request itself carries.
    assert decision["task_id"] == request["task_id"]
    assert decision["core_context_binding_id"] == request["core_context_binding_id"]


def test_request_for_unregistered_program(tmp_path):
    store, cid = _accepted_candidate(tmp_path)
    request = _request(store, cid, program_id="business.analysis.pipeline", program_version="0.1.0")
    assert request["resource"]["registry_status"] == "unregistered"
    assert request["validation"]["registry_match"] is False
    assert "program_not_registered" in request["validation"]["block_reasons"]
    assert request["invocation"]["permission_scope"] == "UNREGISTERED_RESOURCE_EXECUTION"
    assert request["validation"]["review_result"] == "BLOCK"


def test_request_version_mismatch_is_unregistered(tmp_path):
    store, cid = _accepted_candidate(tmp_path)
    request = _request(store, cid, program_version="9.9.9")
    assert request["resource"]["registry_status"] == "unregistered"
    assert "program_not_registered" in request["validation"]["block_reasons"]


def test_request_requires_accepted_candidate(tmp_path):
    store, cid = _draft_candidate(tmp_path)                # DRAFT
    with pytest.raises(ProgramizationBlocked) as exc:
        _request(store, cid)
    assert exc.value.reason_code == "REQUEST_REQUIRES_ACCEPTED"


def test_request_is_one_per_candidate_and_fail_closed_on_inputs(tmp_path):
    store, cid = _accepted_candidate(tmp_path)
    _request(store, cid)
    with pytest.raises(ProgramizationBlocked) as exc:
        _request(store, cid)
    assert exc.value.reason_code == "REQUEST_EXISTS"
    with pytest.raises(ProgramizationBlocked) as exc:
        create_program_request(store, "progcand_missing", program_id="x.y", program_version="0.1.0",
                               requested_by="thomas", reason="r", now=NOW)
    assert exc.value.reason_code == "CANDIDATE_NOT_FOUND"
    with pytest.raises(ProgramizationBlocked) as exc:
        _request(store, cid, requested_by=" ")
    assert exc.value.reason_code == "MISSING_OPERATOR"


def test_block_decision_stays_scope_limited(tmp_path):
    """The permission widening admits BLOCK records only as resource-refusal evidence —
    any other BLOCK scope stays unbuildable (refusal raised, never recorded)."""
    from runtime.mvp_runtime.permission import build_resource_refusal_permission_decision
    task = {"identity": {"task_id": "task_x", "task_revision": 1, "trace_id": "trace_x"},
            "context": {"core_context_binding_id": "ccb-test-1"}}
    with pytest.raises(PlannerBlocked) as exc:
        build_resource_refusal_permission_decision(
            task, program_id="x.y", program_version="0.1.0",
            permission_scope="AUDIT_TAMPERING",            # BLOCK scope, but not refusal evidence
            required_permission_level="P1", role_permission_ceiling="P3",
            target_ref="t", content_sha256=None, normalized_parameters={}, now=NOW,
        )
    assert exc.value.reason_code == "NOT_ALLOWED"


def test_cli_request_flow_and_ledger_event(tmp_path, capsys):
    import json

    from runtime.mvp_runtime.control import ControlStore
    from runtime.mvp_runtime.programization_cli import main
    from runtime.mvp_runtime.store import LedgerStore
    store, cid = _accepted_candidate(tmp_path)
    ledger = LedgerStore(tmp_path / "ledger")
    control = ControlStore(tmp_path)

    assert main(["request", cid, "--program-id", "schema.validator", "--program-version", "0.1.0",
                 "--by", "thomas", "--reason", "reviewed slice"],
                store=store, ledger=ledger, control_store=control, now=NOW) == 0
    out = capsys.readouterr().out
    assert "BLOCK" in out and "APPROVAL_REQUIRED" in out

    events = [json.loads(line) for line in
              (ledger.root / "programization_events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["action"] for e in events] == ["program_request_created"]
    assert events[0]["review_result"] == "BLOCK"
    assert events[0]["candidate_id"] == cid

    # Missing program identity is refused before anything persists.
    assert main(["request", cid, "--by", "thomas", "--reason", "r"],
                store=store, ledger=ledger, control_store=control, now=NOW) != 0
