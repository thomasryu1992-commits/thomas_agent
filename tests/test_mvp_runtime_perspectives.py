"""§10.4 perspective separation — judged separately, then integrated.

Organization Architecture §10.4 describes a complex strategy task as several perspectives
reaching their own judgement before an integrated decision. §10.4 also permits the cheap form
for early MVP — "one Agent may separate these perspectives internally" — and §13's criteria for
splitting them into three Agents are not met, so this is prompt-level separation with a
declared output shape and a validation check.

What the check is for, stated once: a single blended narrative can let a weak revenue case be
smoothed over by an enthusiastic research case, with the reader unable to see it happened.
Both conditions below are compliance rather than analytical judgement — neither reads prose.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from runtime.mvp_runtime import validation
from runtime.mvp_runtime.paths import repo_root
from runtime.mvp_runtime.worker import (
    PERSPECTIVE_VERDICTS,
    PERSPECTIVES,
    MockProvider,
    _perspectives,
    _role_specific_output,
)

ROLE_PATH = Path(repo_root()) / "03_ROLE_CONTRACTS/ROLES/ACTIVE/GENERAL_SPECIALIST_ROLE.md"


def _front_matter(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    return yaml.safe_load(raw.split("---", 2)[1])


def _entry(name="research", verdict="POSITIVE", basis="because"):
    return {"perspective": name, "verdict": verdict, "basis": basis}


NOW = "2026-07-27T09:00:00Z"
TASK_ID, TRACE_ID, CCB = "task_persp_001", "trace_persp_001", "ccb-unit:task_persp_001:r1"
ASSIGNMENT_ID, ACTOR_ID = "ra-persp-001", "actor-persp-001"


@pytest.fixture
def planned_output_record():
    """Validate a hand-built specialist output. Deliberately not routed through `plan_task`:
    these tests are about one check, and binding to an activated Core would make them skip on
    a machine that has no local Core pointer — i.e. silently vacuous exactly where someone is
    most likely to be reading them."""
    def _record(*, perspectives=None, risks=None, required_role_output_keys=None):
        if perspectives is None:
            perspectives = [_entry("research"), _entry("revenue"), _entry("risk")]
        task = {
            "identity": {"task_id": TASK_ID, "trace_id": TRACE_ID, "task_revision": 1},
            "context": {"core_context_binding_id": CCB},
            "classification": {"risk_level": "GREEN"},
        }
        assignment = {"assignment_id": ASSIGNMENT_ID, "role_id": "general.specialist",
                      "actor_instance_id": ACTOR_ID}
        output = {
            "schema_version": "agent_output.v0.2",
            "agent_output_id": "ao-persp-001",
            "trace_id": TRACE_ID, "task_id": TASK_ID, "core_context_binding_id": CCB,
            "assignment_id": ASSIGNMENT_ID, "actor_instance_id": ACTOR_ID,
            "role_id": "general.specialist", "role_version": "0.4.0",
            "status": "needs_validation",
            "goal": "Evaluate the idea.",
            "summary": "A summary.",
            "facts": [{"statement": "A fact.", "evidence_refs": ["model:analysis"]}],
            "evidence": [{"ref": "model:analysis", "type": "model_reasoning"}],
            "inferences": ["An inference."],
            "assumptions": ["An assumption."],
            "uncertainty": ["Something unknown."],
            "risks": ["A risk."] if risks is None else risks,
            "recommendation": {"action": "Validate first.", "reason": "Assumptions dominate."},
            "limitations": ["Read-only."],
            "validation_recommended": True,
            "permission_request_refs": [],
            "next_actions": ["Estimate CAC."],
            "memory_candidates": [],
            "escalation_required": False,
            "role_specific_output": {
                "key_findings": ["A finding."],
                "evidence_quality": "low_illustrative",
                "unresolved_questions": ["A question."],
                "perspectives": perspectives,
            },
            "created_at": NOW,
        }
        return validation.validate_agent_output(
            output, task, assignment, now=NOW,
            required_role_output_keys=required_role_output_keys,
        )
    return _record


# --- normalization: never repaired ------------------------------------------

def test_a_well_formed_block_survives_in_declared_order():
    scrambled = [_entry("risk"), _entry("revenue"), _entry("research")]
    assert [e["perspective"] for e in _perspectives(scrambled)] == list(PERSPECTIVES)


def test_a_malformed_entry_is_dropped_rather_than_coerced():
    """"The model did not answer this perspective" and "answered it badly" both mean the
    separation did not happen. Normalizing a broken entry into a plausible one would hide the
    exact case the check exists to catch."""
    for broken in (
        {"perspective": "vibes", "verdict": "POSITIVE", "basis": "b"},   # unknown perspective
        {"perspective": "risk", "verdict": "GOOD", "basis": "b"},        # unknown verdict
        {"perspective": "risk", "verdict": "POSITIVE", "basis": "  "},   # empty basis
        {"perspective": "risk", "verdict": "POSITIVE"},                  # no basis at all
        "risk: fine",                                                     # not an object
    ):
        assert _perspectives([broken]) == [], broken


def test_a_repeated_perspective_cannot_overwrite_its_own_verdict():
    out = _perspectives([_entry("risk", "NEGATIVE", "first"), _entry("risk", "POSITIVE", "second")])
    assert [(e["verdict"], e["basis"]) for e in out] == [("NEGATIVE", "first")]


def test_a_non_list_is_not_an_error_it_is_simply_absent():
    for value in (None, "research: fine", {"research": "fine"}, 3):
        assert _perspectives(value) == []


def test_a_long_basis_is_bounded():
    out = _perspectives([_entry("risk", "MIXED", "x" * 5000)])
    assert len(out[0]["basis"]) < 5000


# --- the role contract drives the shape -------------------------------------

def test_the_role_contract_declares_exactly_what_the_worker_emits():
    """The pin that was missing. Nothing previously compared the general.specialist output
    contract with what `_role_specific_output` actually returns, so the two could drift — and
    adding `perspectives` to one without the other is precisely how that starts."""
    declared = set(_front_matter(ROLE_PATH)["output_contract"]["role_specific_output"])
    emitted = set(_role_specific_output(MockProvider().generate(
        "p", max_output_tokens=100, timeout_seconds=5).analysis, None))
    assert declared == emitted


def test_the_role_version_was_bumped_with_the_contract():
    """`semantic_versioning_required: true` in the contract's own change_control."""
    front = _front_matter(ROLE_PATH)
    assert front["role_version"] == "0.4.0"
    registry = yaml.safe_load(
        (Path(repo_root()) / "03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml").read_text(encoding="utf-8"))
    entry = next(r for r in registry["roles"] if r["role_id"] == "general.specialist")
    assert entry["version"] == front["role_version"]


def test_the_change_grants_nothing():
    """An output-contract change must not have quietly widened the role. These are the fields
    governance prices APPROVAL_REQUIRED (authority_ceiling_change / allowlist_change)."""
    front = _front_matter(ROLE_PATH)
    assert front["permission_ceiling"] == "P3"
    assert front["external_action_allowed"] is False
    assert front["allowed_program_ids"] == []
    assert front["allowed_tool_ids"] == []
    assert front["memory_policy"]["direct_core_write_allowed"] is False


# --- the provider contract has to carry it ----------------------------------

def test_both_vendor_schemas_require_perspectives():
    """The OpenAI dialect is strict (`additionalProperties: false`), so a key the schema does
    not name is *rejected*, not merely unrequested. Asking the specialist for a field the
    transport would refuse would make every hosted run fail its own perspective check."""
    from runtime.mvp_runtime.providers import _ANALYSIS_JSON_SCHEMA, _ANALYSIS_RESPONSE_SCHEMA

    for schema in (_ANALYSIS_JSON_SCHEMA, _ANALYSIS_RESPONSE_SCHEMA):
        assert "perspectives" in schema["required"]
        item = schema["properties"]["perspectives"]["items"]
        assert set(item["required"]) == {"perspective", "verdict", "basis"}


def test_the_shared_shape_does_not_force_the_validator_to_invent_perspectives():
    """The key is required; a non-empty value is not. The validator and triage ride the same
    analysis JSON and judge rather than analyze — the same reason `facts` carries no minItems."""
    from runtime.mvp_runtime.providers import _ANALYSIS_JSON_SCHEMA

    assert "minItems" not in _ANALYSIS_JSON_SCHEMA["properties"]["perspectives"]


# --- the check ---------------------------------------------------------------

def _check_named(record, name):
    return next(c for c in record["validation"]["checks"] if c["check_id"] == name)


def test_the_mock_run_passes_the_separation_check(planned_output_record):
    record = planned_output_record()
    assert _check_named(record, "perspective_separation")["result"] == "PASS"


def test_a_missing_perspective_is_a_revise(planned_output_record):
    record = planned_output_record(perspectives=[_entry("research"), _entry("revenue")])
    check = _check_named(record, "perspective_separation")
    assert check["result"] == "REVISE"
    assert "risk" in check["notes"]


def test_a_negative_perspective_with_no_stated_risk_is_a_revise(planned_output_record):
    """The blend §10.4 separates perspectives to prevent: a perspective judged NEGATIVE while
    the output states no risk at all has absorbed it into the summary."""
    record = planned_output_record(
        perspectives=[_entry("research"), _entry("revenue", "NEGATIVE"), _entry("risk")],
        risks=[],
    )
    check = _check_named(record, "perspective_separation")
    assert check["result"] == "REVISE"
    assert "revenue" in check["notes"]


def test_a_negative_perspective_with_a_stated_risk_passes(planned_output_record):
    """Disagreement is not the failure — hiding it is. A NEGATIVE verdict is fine as long as
    the risk reaches the reader."""
    record = planned_output_record(
        perspectives=[_entry("research"), _entry("revenue", "NEGATIVE"), _entry("risk")],
        risks=["Revenue case does not close at the assumed CAC."],
    )
    assert _check_named(record, "perspective_separation")["result"] == "PASS"


def test_the_check_is_scoped_to_roles_that_promise_perspectives(planned_output_record):
    """A candidate-role trial passes its own declared output keys, so the role contract — not a
    hardcoded role id — decides whether the check applies. A role that never promised
    perspectives must not be judged on them."""
    record = planned_output_record(perspectives=[], required_role_output_keys=["key_findings"])
    assert all(c["check_id"] != "perspective_separation"
               for c in record["validation"]["checks"])


def test_the_verdict_vocabulary_is_shared_not_duplicated():
    """`validation` imports the list rather than keeping its own, so "what was asked for" and
    "what is checked" cannot drift into two."""
    assert validation.PERSPECTIVES is PERSPECTIVES
    assert set(PERSPECTIVE_VERDICTS) == {"POSITIVE", "MIXED", "NEGATIVE"}
