"""Mechanical drift gates between the runtime's constants and the policy/validators.

Every finding in the 2026-07 QA review that took real work to notice had the same shape:
two places encode one fact, and nothing checks they still agree. These tests are the
cheap general answer — they fail the moment a list is edited in one place only.

Deliberately assertions about *agreement*, not about the values themselves: the values
are governance decisions and belong in the policy and the schemas. What belongs in a test
is that the copies have not drifted apart.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from runtime.mvp_runtime import authority
from runtime.mvp_runtime.paths import repo_root

import sys

SCRIPTS = repo_root() / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_permission_approval_contracts as contracts_validator  # noqa: E402
from gate_matrix import classify_ci_scopes  # noqa: E402


def _policy() -> dict:
    return yaml.safe_load((repo_root() / "governance" / "GOVERNANCE_POLICY.yaml").read_text(encoding="utf-8"))


# --- runtime_effect: the runtime's block vs the validator's expectation -------

def test_the_validators_false_field_list_matches_the_runtime_factory():
    """authority.py is the single authority for the runtime's no-grant effect block, but
    the contracts validator keeps its own list of the fields it requires to be false.
    Adding a flag to one and not the other leaves the new flag unchecked — fail-open on
    exactly the surface that exists to be pinned false."""
    factory_fields = set(authority.permission_decision_runtime_effect()) - {"mode"}
    assert set(contracts_validator.RUNTIME_FALSE_FIELDS) == factory_fields


def test_every_runtime_effect_flag_the_factory_emits_is_false():
    for name, factory in (
        ("permission_decision", authority.permission_decision_runtime_effect),
        ("validation_result", authority.validation_result_runtime_effect),
        ("audit_event", authority.audit_event_runtime_effect),
    ):
        block = factory()
        assert block["mode"] in (authority.REVIEW_ONLY, authority.EVIDENCE_ONLY), name
        assert all(value is False for key, value in block.items() if key != "mode"), name


def test_the_policy_runtime_effect_block_covers_every_field_the_validator_pins():
    """The validator asserts a set of policy fields are false; if the policy stopped
    carrying one, the assertion would pass vacuously on a missing key."""
    effect = _policy()["runtime_effect"]
    for field in contracts_validator.POLICY_RUNTIME_FALSE_FIELDS:
        assert field in effect, f"policy runtime_effect lost the field {field!r} the validator pins false"
        assert effect[field] is False, field


# --- CI scope classification: unmatched governed paths must not skip a Gate ---

def test_a_governed_path_matching_no_pattern_still_runs_the_deferred_gate():
    """The pattern list is a hand-maintained keyword allowlist. A new artifact under a
    name nobody anticipated used to match nothing and silently skip its Gate — a
    fail-open default in the one place whose job is deciding what gets checked."""
    for path in (
        "docs/runtime-contracts/SOMETHING_NOBODY_ANTICIPATED_V0.1.md",
        "schemas/brand_new_thing.v0.1.schema.json",
        "deferred/whatever/new_file.yaml",
        "05_REGISTRIES/A_NEW_REGISTRY.yaml",
    ):
        assert classify_ci_scopes([path])["deferred"] is True, path


def test_ordinary_source_changes_do_not_trigger_the_deferred_gate():
    """The fallback must not make every PR run every Gate — only governed directories."""
    result = classify_ci_scopes(["runtime/mvp_runtime/pipeline.py", "tests/test_x.py", "README.md"])
    assert result["deferred"] is False and result["legacy"] is False and result["full"] is False
    assert result["active"] is True


def test_shared_infrastructure_still_escalates_to_every_gate():
    result = classify_ci_scopes(["scripts/gate_matrix.py"])
    assert result["full"] and result["deferred"] and result["legacy"]
