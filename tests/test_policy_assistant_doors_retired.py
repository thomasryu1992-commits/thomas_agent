"""The policy no longer describes the assistant's retired doors, and the runtime reads none of them (1.7.0)."""
from pathlib import Path

import yaml

from runtime.mvp_runtime import control, policy_fingerprint

POLICY = Path(__file__).resolve().parents[1] / "governance" / "GOVERNANCE_POLICY.yaml"


def _policy():
    return yaml.safe_load(POLICY.read_text(encoding="utf-8"))


def test_the_retired_doors_clauses_are_gone_and_the_read_lane_stays():
    policy = _policy()
    assert "assistant_dispatch_gate" not in policy["authority"]
    for key in ("assistant_dispatch", "assistant_switch", "approval_notification_mirror", "assistant_schedule"):
        assert key not in policy["control_channel"], key
    read = policy["control_channel"]["assistant_read"]
    assert {"holdings_status", "lane_digest"} <= set(read["verbs"])
    assert read["mutation_allowed"] is False and read["gate_grants_authority"] is False


def test_the_runtime_reads_no_removed_clause():
    assert not hasattr(control, "granted_switch_verbs")
    assert ("control_channel", "assistant_switch") not in policy_fingerprint.SAFETY_SECTIONS
    assert {"holdings_status", "lane_digest"} <= control.granted_read_verbs()
