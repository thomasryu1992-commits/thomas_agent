"""The policy lists the assistant's holdings read, and the runtime acts on it (policy 1.6.2)."""
from pathlib import Path

import yaml

from runtime.mvp_runtime import control, read_bridge

POLICY = Path(__file__).resolve().parents[1] / "governance" / "GOVERNANCE_POLICY.yaml"


def _read_clause():
    return yaml.safe_load(POLICY.read_text(encoding="utf-8"))["control_channel"]["assistant_read"]


def test_the_grant_lists_holdings_under_a_clause_that_still_grants_nothing():
    clause = _read_clause()
    assert "holdings_status" in clause["verbs"]
    assert clause["mutation_allowed"] is False and clause["gate_grants_authority"] is False


def test_the_runtime_reads_the_committed_grant():
    assert "holdings_status" in read_bridge.POLICY_GATED_READS
    assert "holdings_status" in control.granted_read_verbs()
