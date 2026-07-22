"""Program registry registration tests: ask, verify, apply — verified, never spent.

Definition building, approval verification, and the working-tree apply need no Core
(apply runs against a tmp fixture root carrying a copy of the real registry). The ask
path builds a real bound task, so it needs a local Core activation.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from runtime.mvp_runtime.errors import ApprovalBlocked, ProgramizationBlocked
from runtime.mvp_runtime.registration import (
    REGISTRATION_ACTION_TYPE,
    _lineage,
    apply_registration,
    build_program_definition,
    definition_content_sha256,
    definition_rel_path,
    request_registration,
    verify_registration_approval,
)

from tests._helpers import requires_local_core
from tests.test_mvp_runtime_program_request import _accepted_candidate, _request
from tests.test_mvp_runtime_programization import NOW

REPO_ROOT = Path(__file__).resolve().parents[1]

_FAKE_REQUEST = {
    "program_request_id": "progreq_test_001",
    "resource": {"program_id": "business.analyzer", "program_version": "0.1.0",
                 "required_permission_level": "P2"},
}

_DEFINITION_INPUT = {
    "purpose": "Deterministic slice of the reviewed business-analysis pattern.",
    "inputs": ["analysis_request"],
    "outputs": ["structured_analysis"],
}


def _definition():
    return build_program_definition(_FAKE_REQUEST, _DEFINITION_INPUT)


def _approval(*, status="APPROVED", expires="2026-07-16T10:00:00Z",
              action_type=REGISTRATION_ACTION_TYPE, content=None):
    content = content if content is not None else f"sha256:{definition_content_sha256(_definition())}"
    return {"approval_id": "approval_test", "status": status,
            "validity": {"expires_at": expires},
            "approved_action_snapshot": {"action_type": action_type, "content_sha256": content}}


# --- definition building -----------------------------------------------------

def test_definition_pins_the_load_bearing_fields():
    d = _definition()
    assert d["status"] == "candidate"
    assert d["runtime"] == {"implementation_available": False, "enabled": False}
    assert d["effects"] == {"external_action": False, "filesystem_write": False, "network_access": False}
    assert d["deterministic"] is True
    assert d["required_permission_level"] == "P2"          # from the program request
    assert d["program_id"] == "business.analyzer" and d["version"] == "0.1.0"


def test_definition_input_is_fail_closed():
    for broken in (
        {**_DEFINITION_INPUT, "purpose": " "},
        {**_DEFINITION_INPUT, "inputs": []},
        {**_DEFINITION_INPUT, "outputs": [""]},
        "not a mapping",
    ):
        with pytest.raises(ProgramizationBlocked) as exc:
            build_program_definition(_FAKE_REQUEST, broken)  # type: ignore[arg-type]
        assert exc.value.reason_code == "DEFINITION_INPUT_INVALID"


def test_definition_hash_is_content_sensitive():
    a = definition_content_sha256(_definition())
    b = definition_content_sha256(build_program_definition(
        _FAKE_REQUEST, {**_DEFINITION_INPUT, "purpose": "different"}))
    assert a != b and len(a) == 64                          # bare hex, registry-shaped


# --- approval verification (verified, never spent) ---------------------------

def test_verify_registration_approval_happy_path():
    verified = verify_registration_approval(_approval(), definition=_definition(), now=NOW)
    assert verified["status"] == "APPROVED"


def test_verify_registration_approval_fail_closed_paths():
    d = _definition()
    cases = [
        (None, "APPROVAL_MISSING"),
        (_approval(status="PENDING"), "APPROVAL_NOT_APPROVED"),
        (_approval(expires="2026-07-16T08:00:00Z"), "APPROVAL_EXPIRED"),
        (_approval(action_type="crypto.strategy_pool.promotion"), "APPROVAL_WRONG_ACTION"),
        (_approval(content="sha256:" + "0" * 64), "APPROVAL_CONTENT_MISMATCH"),
    ]
    for approval, code in cases:
        with pytest.raises(ApprovalBlocked) as exc:
            verify_registration_approval(approval, definition=d, now=NOW)
        assert exc.value.reason_code == code


# --- working-tree apply (tmp fixture root) -----------------------------------

def _fixture_root(tmp_path):
    root = tmp_path / "repo"
    (root / "05_REGISTRIES").mkdir(parents=True)
    (root / "programs" / "definitions").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "05_REGISTRIES" / "PROGRAM_REGISTRY.yaml", root / "05_REGISTRIES")
    for path in (REPO_ROOT / "programs" / "definitions").glob("*.yaml"):
        shutil.copy(path, root / "programs" / "definitions")
    return root


def test_apply_writes_definition_and_resolvable_registry(tmp_path):
    root = _fixture_root(tmp_path)
    d = _definition()
    applied = apply_registration(d, repo_root=root)
    assert applied["definition_path"] == definition_rel_path("business.analyzer")
    entry = applied["entry"]
    assert entry["status"] == "candidate" and entry["enabled"] is False
    assert entry["runtime_implementation_available"] is False
    assert entry["definition_sha256"] == definition_content_sha256(d)

    registry = yaml.safe_load((root / "05_REGISTRIES" / "PROGRAM_REGISTRY.yaml").read_text(encoding="utf-8"))
    ids = [(e["program_id"], e["version"]) for e in registry["programs"]]
    assert ("business.analyzer", "0.1.0") in ids
    assert ("schema.validator", "0.1.0") in ids            # existing entries preserved
    written = yaml.safe_load((root / applied["definition_path"]).read_text(encoding="utf-8"))
    assert written == d                                     # the hash-checked content


def test_apply_is_create_only(tmp_path):
    root = _fixture_root(tmp_path)
    apply_registration(_definition(), repo_root=root)
    with pytest.raises(ProgramizationBlocked) as exc:       # id+version already registered
        apply_registration(_definition(), repo_root=root)
    assert exc.value.reason_code == "ALREADY_REGISTERED"

    other = build_program_definition(
        {**_FAKE_REQUEST, "resource": {**_FAKE_REQUEST["resource"], "program_version": "0.2.0"}},
        _DEFINITION_INPUT)
    with pytest.raises(ProgramizationBlocked) as exc:       # same path, different version
        apply_registration(other, repo_root=root)
    assert exc.value.reason_code == "DEFINITION_PATH_EXISTS"


def test_apply_refuses_registering_an_existing_program(tmp_path):
    root = _fixture_root(tmp_path)
    existing = build_program_definition(
        {"program_request_id": "x", "resource": {"program_id": "schema.validator",
         "program_version": "0.1.0", "required_permission_level": "P1"}},
        _DEFINITION_INPUT)
    with pytest.raises(ProgramizationBlocked) as exc:
        apply_registration(existing, repo_root=root)
    assert exc.value.reason_code == "ALREADY_REGISTERED"


# --- script apply door (tmp fixture root; approval seeded) --------------------

def test_script_apply_verifies_approval_and_records_event(tmp_path):
    import json

    from runtime.mvp_runtime.approval_store import STORE_REL as APPROVAL_STORE_REL
    from runtime.mvp_runtime.approval_store import ApprovalStore
    from scripts.register_program_candidate import run_apply
    root = _fixture_root(tmp_path)
    store, cid = _accepted_candidate(tmp_path)
    _request(store, cid, program_id="business.analyzer")    # lineage: request must exist

    # The candidate's request records required_permission_level P4 (unregistered default),
    # so re-derive the expected content from the SAME lineage the apply door re-reads.
    _, request = _lineage(store, cid)
    definition = build_program_definition(request, _DEFINITION_INPUT)
    approval_store = ApprovalStore(root / APPROVAL_STORE_REL)
    approval_store.append([{**_approval(content=f"sha256:{definition_content_sha256(definition)}"),
                            "approval_id": "approval_reg_1"}])

    applied = run_apply(candidate_id=cid, definition_input=_DEFINITION_INPUT,
                        approval_id="approval_reg_1", registered_by="thomas",
                        reason="reviewed", root=root, store=store, now=NOW)
    assert applied["entry"]["program_id"] == "business.analyzer"
    events = [json.loads(line) for line in
              (root / ".runtime_governance_state/runtime_ledger/programization_events.jsonl")
              .read_text(encoding="utf-8").splitlines()]
    assert [e["action"] for e in events] == ["program_registered"]
    assert events[0]["approval_id"] == "approval_reg_1"
    assert events[0]["registry_enabled"] is False


def test_script_apply_refuses_wrong_approval(tmp_path):
    from runtime.mvp_runtime.approval_store import STORE_REL as APPROVAL_STORE_REL
    from runtime.mvp_runtime.approval_store import ApprovalStore
    from scripts.register_program_candidate import run_apply
    root = _fixture_root(tmp_path)
    store, cid = _accepted_candidate(tmp_path)
    _request(store, cid, program_id="business.analyzer")
    ApprovalStore(root / APPROVAL_STORE_REL).append([
        {**_approval(status="PENDING"), "approval_id": "approval_reg_2"}])
    with pytest.raises(SystemExit) as exc:
        run_apply(candidate_id=cid, definition_input=_DEFINITION_INPUT,
                  approval_id="approval_reg_2", registered_by="thomas",
                  reason="r", root=root, store=store, now=NOW)
    assert "APPROVAL_NOT_APPROVED" in str(exc.value)


# --- the ask path (needs a local Core for the real binding) -------------------

@requires_local_core
def test_request_registration_builds_the_approval_ask(tmp_path):
    store, cid = _accepted_candidate(tmp_path)
    _request(store, cid, program_id="business.analyzer")
    prepared = request_registration(store, cid, _DEFINITION_INPUT, now=NOW)
    decision = prepared["permission_decision"]
    assert decision["decision"]["permission_decision"] == "APPROVAL_REQUIRED"
    assert decision["fingerprint_payload"]["permission_scope"] == "TOOL_PROGRAM_GOVERNANCE"
    assert decision["action_fingerprint"].startswith("sha256:")
    request = prepared["approval_request"]
    assert request["status"] == "PENDING"
    assert prepared["content_sha256"] == f"sha256:{definition_content_sha256(prepared['definition'])}"


@requires_local_core
def test_request_registration_refuses_already_registered(tmp_path):
    store, cid = _accepted_candidate(tmp_path)
    _request(store, cid)                                    # default: schema.validator@0.1.0 (registered)
    with pytest.raises(ProgramizationBlocked) as exc:
        request_registration(store, cid, _DEFINITION_INPUT, now=NOW)
    assert exc.value.reason_code == "ALREADY_REGISTERED"


@requires_local_core
def test_request_registration_requires_lineage(tmp_path):
    from tests.test_mvp_runtime_program_request import _draft_candidate
    store, cid = _draft_candidate(tmp_path)                 # DRAFT, no request
    with pytest.raises(ProgramizationBlocked) as exc:
        request_registration(store, cid, _DEFINITION_INPUT, now=NOW)
    assert exc.value.reason_code == "REGISTRATION_REQUIRES_ACCEPTED"
