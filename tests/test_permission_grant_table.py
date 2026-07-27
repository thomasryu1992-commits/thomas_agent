"""What this runtime may grant itself — asserted against the policy, not against itself.

`permission.py` declares a `*_PERMISSION_SCOPE` constant for every governed action it can
build a decision for, and gates them through three allowlists: `_BUILDABLE_DISPOSITIONS`,
`_EXECUTE_AND_REPORT_SCOPES`, `_APPROVAL_REQUIRED_SCOPES`. The allowlists are the whole
safety story — each entry is a recorded Thomas decision — but nothing checked that the
constants and the allowlists agree, so a new scope could be introduced whose disposition
the gate silently mishandles.

These tests close that: every scope the module names must exist in the canonical Governance
Policy, and must be admitted by the allowlist that matches its *policy* disposition. Adding
an EXECUTE_AND_REPORT or APPROVAL_REQUIRED scope therefore fails here until it is also added
to its allowlist — which is exactly the deliberate step that needs a decision behind it.

The `_FIXED_GRANTS` table is checked the same way: it is the one place that answers "what
can this runtime grant itself, at what level, under which scope", so it must not drift from
the constants it is built out of.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
import yaml

from runtime.mvp_runtime import permission as P
from runtime.mvp_runtime.authority import RISK_ORDER, rank_of, risk_floor_for_disposition
from runtime.mvp_runtime.errors import PlannerBlocked
from runtime.mvp_runtime.paths import repo_root
from runtime.mvp_runtime.planner import MVP_PERMISSION_SCOPE, MVP_REQUIRED_PERMISSION_LEVEL

POLICY = repo_root() / "governance" / "GOVERNANCE_POLICY.yaml"

BOUND = {
    "identity": {"task_id": "task_granttable_001", "trace_id": "trace_granttable_001",
                 "task_revision": 1},
    "context": {"core_context_binding_id": "ccb-unit:task_granttable_001:r1"},
}
NOW = "2026-07-26T12:00:00Z"


def _dispositions() -> dict[str, str]:
    policy = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    return P.scope_policy_map(policy)


def _declared_scopes() -> dict[str, str]:
    """Every ``*_PERMISSION_SCOPE`` constant the module declares, by constant name."""
    return {
        name: value
        for name, value in vars(P).items()
        if name.endswith("_PERMISSION_SCOPE") and isinstance(value, str)
    }


def test_the_module_declares_scopes_to_check():
    """A rename that emptied this introspection would make every test below vacuous."""
    declared = _declared_scopes()
    assert len(declared) >= 8, declared


def test_every_declared_scope_exists_in_the_governance_policy():
    """The policy is the authority on what a scope means. A constant naming a scope the
    policy does not define would be evaluated against nothing."""
    dispositions = _dispositions()
    for name, scope in _declared_scopes().items():
        assert scope in dispositions, f"{name} = {scope!r} has no disposition in the policy"


def test_every_declared_scope_is_admitted_by_the_allowlist_that_matches_its_disposition():
    """The check that makes forgetting impossible.

    A scope priced EXECUTE_AND_REPORT must be in `_EXECUTE_AND_REPORT_SCOPES`; one priced
    APPROVAL_REQUIRED must be in `_APPROVAL_REQUIRED_SCOPES`. Introducing such a scope
    without adding it to its allowlist fails here — and adding it to the allowlist is the
    deliberate act each of those entries records as a Thomas decision.
    """
    dispositions = _dispositions()
    for name, scope in _declared_scopes().items():
        disposition = dispositions[scope]
        assert disposition in P._BUILDABLE_DISPOSITIONS, (
            f"{name} = {scope!r} is priced {disposition}, which the gate refuses to build"
        )
        if disposition == P.EXECUTE_AND_REPORT:
            assert scope in P._EXECUTE_AND_REPORT_SCOPES, (
                f"{name} = {scope!r} is EXECUTE_AND_REPORT but is not in "
                "_EXECUTE_AND_REPORT_SCOPES; the builder would be refused at run time"
            )
        elif disposition == P.APPROVAL_REQUIRED:
            assert scope in P._APPROVAL_REQUIRED_SCOPES, (
                f"{name} = {scope!r} is APPROVAL_REQUIRED but is not in "
                "_APPROVAL_REQUIRED_SCOPES; the runtime cannot honour an ask it has no "
                "implementation for"
            )


def test_no_allowlist_admits_a_scope_the_policy_prices_differently():
    """The converse: an allowlist entry that the policy does not actually price that way
    would widen the gate for a scope nobody re-read."""
    dispositions = _dispositions()
    for scope in P._EXECUTE_AND_REPORT_SCOPES:
        assert dispositions.get(scope) == P.EXECUTE_AND_REPORT, scope
    for scope in P._APPROVAL_REQUIRED_SCOPES:
        assert dispositions.get(scope) == P.APPROVAL_REQUIRED, scope


# --- the fixed-grant table ------------------------------------------------------

def test_the_fixed_grant_table_matches_the_constants_it_is_built_from():
    """The table is the readable answer to "what can this runtime grant itself". If a row
    drifted from its constants, that answer would be wrong in the one place people look."""
    expected = {
        "search": (P.SEARCH_PERMISSION_SCOPE, P.SEARCH_REQUIRED_PERMISSION_LEVEL),
        "triage": (P.TRIAGE_PERMISSION_SCOPE, P.TRIAGE_REQUIRED_PERMISSION_LEVEL),
        "validation": (P.VALIDATION_PERMISSION_SCOPE, P.VALIDATION_REQUIRED_PERMISSION_LEVEL),
        "trial_work": (P.TRIAL_WORK_PERMISSION_SCOPE, P.TRIAL_WORK_REQUIRED_PERMISSION_LEVEL),
        "write": (P.WRITE_PERMISSION_SCOPE, P.WRITE_REQUIRED_PERMISSION_LEVEL),
    }
    assert set(P._FIXED_GRANTS) == set(expected)
    for key, (scope, level) in expected.items():
        row = P._FIXED_GRANTS[key]
        assert (row.scope, row.level) == (scope, level), key
        assert rank_of(row.level) is not None, key


def test_each_fixed_grant_has_its_own_action_type():
    """The permission_decision_id seed includes the action_type, so two grants sharing one
    would collide on the same task — the collision the R7.2 seed fix already closed once."""
    action_types = [row.action.action_type for row in P._FIXED_GRANTS.values()]
    assert len(set(action_types)) == len(action_types), action_types


def test_each_fixed_grant_builds_the_disposition_the_policy_prices():
    """End to end for every row: the decision the named builder returns carries the policy's
    disposition and the table's scope — no builder quietly using different constants."""
    dispositions = _dispositions()
    builders = {
        "search": (P.build_search_permission_decision, "P3"),
        "triage": (P.build_triage_permission_decision, "P3"),
        "validation": (P.build_validation_permission_decision, "P2"),
        "trial_work": (P.build_trial_work_permission_decision, "P3"),
        "write": (P.build_write_permission_decision, "P3"),
    }
    assert set(builders) == set(P._FIXED_GRANTS)
    for key, (builder, ceiling) in builders.items():
        record = builder(BOUND, role_permission_ceiling=ceiling, now=NOW)
        row = P._FIXED_GRANTS[key]
        assert record["fingerprint_payload"]["permission_scope"] == row.scope, key
        assert record["decision"]["permission_decision"] == dispositions[row.scope], key
        assert record["fingerprint_payload"]["action_type"] == row.action.action_type, key
        # Whatever the disposition, the record itself remains planning evidence.
        assert record["runtime_effect"]["mode"] == "REVIEW_ONLY", key


# --- the risk floor (policy §10) ------------------------------------------------

def test_every_action_spec_declares_at_least_the_risk_its_disposition_implies():
    """The invariant that catches a decision contradicting itself.

    Policy §10 maps each risk level to a default disposition. Read backwards it gives a floor:
    an ALLOW action may be GREEN, but an EXECUTE_AND_REPORT one is at least YELLOW ("복구
    가능한 내부 영향"), APPROVAL_REQUIRED at least ORANGE, BLOCK at least RED. Every action
    spec sets `risk_level` by hand and nothing cross-checked them, which is how the R8
    workspace write shipped GREEN while carrying EXECUTE_AND_REPORT.

    This sweeps every spec the module defines, not just the `_FIXED_GRANTS` rows, so an action
    added anywhere in the module is covered the day it exists.
    """
    dispositions = _dispositions()
    scope_of = {row.action.action_type: row.scope for row in P._FIXED_GRANTS.values()}
    scope_of[P._ANALYSIS_ACTION.action_type] = MVP_PERMISSION_SCOPE

    checked = 0
    for name, spec in vars(P).items():
        if not (name.startswith("_") and isinstance(spec, P._ActionSpec)):
            continue
        scope = scope_of.get(spec.action_type)
        if scope is None:              # an action built through a factory, covered below
            continue
        floor = risk_floor_for_disposition(dispositions[scope])
        assert RISK_ORDER[spec.risk_level] >= RISK_ORDER[floor], (
            f"{name} declares {spec.risk_level} but {scope} is priced "
            f"{dispositions[scope]}, whose floor is {floor}"
        )
        checked += 1
    assert checked >= 5, f"the introspection stopped finding action specs: {checked}"


def test_the_workspace_write_is_yellow_not_green():
    """Pinned on its own because it is the one that was wrong, and because the value is what
    the task-level classification now derives from."""
    assert P.WORKSPACE_WRITE_RISK_LEVEL == "YELLOW"
    assert P._WRITE_ACTION.risk_level == "YELLOW"
    record = P.build_write_permission_decision(BOUND, role_permission_ceiling="P3", now=NOW)
    assert record["risk"]["risk_level"] == "YELLOW"
    assert record["decision"]["permission_decision"] == P.EXECUTE_AND_REPORT


def test_a_decision_below_its_floor_is_refused_at_the_construction_site():
    """The floor has to bite at build time, not only in a spec review — otherwise a future
    action added below its floor is caught by nothing."""
    understated = replace(P._WRITE_ACTION, risk_level="GREEN")
    with pytest.raises(PlannerBlocked) as exc:
        P.build_permission_decision(
            BOUND, permission_scope=P.WRITE_PERMISSION_SCOPE,
            required_permission_level=P.WRITE_REQUIRED_PERMISSION_LEVEL,
            role_permission_ceiling="P3", now=NOW, action=understated,
        )
    assert exc.value.reason_code == "RISK_BELOW_DISPOSITION_FLOOR"


def test_an_unknown_risk_level_is_refused_rather_than_ranked_low():
    with pytest.raises(PlannerBlocked) as exc:
        P.build_permission_decision(
            BOUND, permission_scope=MVP_PERMISSION_SCOPE,
            required_permission_level=MVP_REQUIRED_PERMISSION_LEVEL,
            role_permission_ceiling="P3", now=NOW,
            action=replace(P._ANALYSIS_ACTION, risk_level="MAUVE"),
        )
    assert exc.value.reason_code == "RISK_BELOW_DISPOSITION_FLOOR"
