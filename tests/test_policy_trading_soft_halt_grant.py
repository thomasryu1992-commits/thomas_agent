"""The policy grants the Trading Soft Halt, and the runtime acts on the grant (policy 1.5.1)."""
from pathlib import Path

import yaml

from runtime.mvp_runtime import control, switch_bridge

POLICY = Path(__file__).resolve().parents[1] / "governance" / "GOVERNANCE_POLICY.yaml"


def _policy():
    return yaml.safe_load(POLICY.read_text(encoding="utf-8"))


def test_the_grant_names_the_soft_halt_on_both_lists():
    policy = _policy()
    assert "halt_trading" in policy["control_channel"]["local_operator_console"]["emergency_controls_allowed"]
    assert "/halt_trading" in policy["kill_switch"]["commands"]


def test_the_runtime_reads_the_committed_grant():
    assert control.CMD_HALT_TRADING in control.granted_emergency_controls()
    assert switch_bridge._DISABLE_MODES["soft"] == control.CMD_HALT_TRADING


def test_the_soft_halt_acts_and_keeps_the_runtime_active(tmp_path):
    store = control.ControlStore(tmp_path)
    store.save(control.ControlState(mode=control.ACTIVE, updated_by="op", updated_at="2026-09-15T00:00:00Z",
                                    reason="armed", trading_armed=True))
    out = control.apply_command(store, control.CMD_HALT_TRADING, actor="op", now="2026-09-15T00:00:00Z")
    assert out["changed"] is True
    assert (store.load().mode, store.load().trading_armed) == (control.ACTIVE, False)
