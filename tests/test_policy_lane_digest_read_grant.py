"""The policy lists the assistant's lane digest read, and the runtime acts on it (policy 1.6.1)."""
from pathlib import Path

import yaml

from runtime.mvp_runtime import control, read_bridge

POLICY = Path(__file__).resolve().parents[1] / "governance" / "GOVERNANCE_POLICY.yaml"


def _read_clause():
    return yaml.safe_load(POLICY.read_text(encoding="utf-8"))["control_channel"]["assistant_read"]


def test_the_grant_lists_the_digest_under_a_clause_that_still_grants_nothing():
    clause = _read_clause()
    assert "lane_digest" in clause["verbs"]
    assert clause["mutation_allowed"] is False and clause["gate_grants_authority"] is False


def test_the_runtime_reads_the_committed_grant():
    assert "lane_digest" in read_bridge.POLICY_GATED_READS
    assert "lane_digest" in control.granted_read_verbs()
