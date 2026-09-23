"""The policy's assistant_schedule clause and the runtime's delegation scope name the same limits."""
from pathlib import Path

import yaml

from runtime.mvp_runtime import schedule_delegation as sd, scheduler

POLICY = Path(__file__).resolve().parents[1] / "governance" / "GOVERNANCE_POLICY.yaml"


def _clause():
    return yaml.safe_load(POLICY.read_text(encoding="utf-8"))["control_channel"]["assistant_schedule"]


def test_the_clause_loads_as_the_scope_the_door_enforces():
    scope = sd.load_delegation()
    clause = _clause()
    assert scope is not None and scope.kinds == frozenset(clause["delegated_kinds"])
    assert scope.min_interval_seconds == clause["min_interval_seconds"] and scope.max_active == clause["max_active"]
    assert scope.max_validity_days == clause["max_validity_days"]
    assert scope.max_model_calls_per_run == clause["max_model_calls_per_run"]


def test_the_scope_reaches_no_financial_kind_and_grants_nothing_else():
    clause = _clause()
    assert clause["actor"] == "assistant_bridge" and clause["gate_grants_authority"] is False
    assert clause["financial_kinds_delegable"] is False and clause["out_of_scope"] == "proposal_only"
    assert not (set(clause["delegated_kinds"]) & sd.FINANCIAL_KINDS)
    assert set(clause["delegated_kinds"]) <= scheduler.MAINTENANCE_KINDS
