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


# --- execution budget: the factory vs the canonical contract structure --------


def _budget_contract() -> dict:
    return yaml.safe_load(
        (repo_root() / "docs" / "runtime-contracts" / "EXECUTION_BUDGET_SCHEMA.yaml")
        .read_text(encoding="utf-8")
    )


def test_budget_factory_emits_exactly_the_canonical_field_sets():
    """budgets.py, the contract YAML, the operating policy, and the closed schema all
    restate the execution-budget shape — the one active-path constant duplication with no
    drift gate (2026-07-21 architecture review P2-7). The contract's canonical_object is
    the structure authority: a field added or renamed in one place only must fail here."""
    from runtime.mvp_runtime.budgets import default_execution_budget, recorded_usage_budget

    canonical = _budget_contract()["canonical_object"]
    allocation = default_execution_budget()
    assert set(allocation["limits"]) == set(canonical["limits"])
    assert set(allocation["usage"]) == set(canonical["usage"])

    usage = recorded_usage_budget(
        allocation["limits"], agent_invocations=1, model_calls=1, tokens_used=10,
    )
    # recorded usage deliberately omits runtime_seconds/cost_used measurements but must
    # still CARRY every canonical usage field (zeroed), so readers see one shape.
    assert set(usage["usage"]) == set(canonical["usage"])
    assert set(usage["limits"]) == set(canonical["limits"])


def test_budget_factory_stays_within_the_policy_ceilings():
    """MVP_OPERATING_POLICY §13.4 states the policy ceilings (legacy field names mapped
    by the contract's legacy_mapping). Lower is explicitly allowed ('Task 특성에 따라 더
    낮은 한도를 설정할 수 있다'); exceeding one silently is the drift this pins."""
    from runtime.mvp_runtime.budgets import default_execution_budget

    limits = default_execution_budget()["limits"]
    assert limits["max_retry_count"] <= 3            # §13.4 max_retry_count_per_step
    assert limits["max_runtime_seconds"] <= 30 * 60  # §13.4 max_task_runtime_minutes
    assert limits["max_parallel_workers"] <= 1       # MVP runs agents sequentially
