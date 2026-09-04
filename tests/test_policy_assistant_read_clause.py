"""The policy's assistant_read clause and the read door's inventory name the same verbs."""
from pathlib import Path

import yaml

from runtime.mvp_runtime import read_bridge, store_reads

POLICY = Path(__file__).resolve().parents[1] / "governance" / "GOVERNANCE_POLICY.yaml"


def _control_channel():
    return yaml.safe_load(POLICY.read_text(encoding="utf-8"))["control_channel"]


def test_the_policy_verbs_and_the_read_doors_inventory_are_the_same_set_both_ways():
    verbs = set(_control_channel()["assistant_read"]["verbs"])
    assert verbs == set(read_bridge.READ_VERB_AUTHORITY) == set(read_bridge._READS)


def test_the_clause_grants_nothing_and_the_mirror_is_a_sink():
    channel = _control_channel()
    clause = channel["assistant_read"]
    assert clause["actor"] == "assistant_bridge"
    assert clause["mutation_allowed"] is False and clause["gate_grants_authority"] is False
    assert channel["approval_notification_mirror"]["decision_source"] is False
    assert "telegram_group" in channel["invalid_approval_sources"]      # unchanged by 1.5.0


def test_approval_status_exposes_only_what_store_reads_renders():
    exposed = set(_control_channel()["assistant_read"]["approval_status_exposes"])
    assert exposed <= set(store_reads.APPROVAL_STATUS_FIELDS)
    assert not ({"approved_action_snapshot", "action_fingerprint", "permission_decision_id"} & exposed)
