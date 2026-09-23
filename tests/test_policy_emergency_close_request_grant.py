"""The policy lists the assistant's ask for the emergency close, and the runtime acts on it (policy 1.5.2)."""
from pathlib import Path

import yaml

from runtime.mvp_runtime import control, switch_bridge

POLICY = Path(__file__).resolve().parents[1] / "governance" / "GOVERNANCE_POLICY.yaml"


def _policy():
    return yaml.safe_load(POLICY.read_text(encoding="utf-8"))


def test_the_grant_lists_the_ask_as_approval_required():
    verbs = _policy()["control_channel"]["assistant_switch"]["verbs"]
    assert verbs["emergency_close"] == "approval_required_always"
    assert set(verbs) == {"status", "disable", "enable", "emergency_close"}


def test_the_runtime_reads_the_committed_grant():
    assert switch_bridge.CMD_EMERGENCY_CLOSE in switch_bridge.POLICY_GATED_COMMANDS
    assert switch_bridge.CMD_EMERGENCY_CLOSE in control.granted_switch_verbs()
