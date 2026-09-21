"""The 1.5.1 draft (the Trading Soft Halt grant), checked before anyone runs it.

The bump script is Thomas's to apply (decision Q2); these tests make sure that when he does, its
anchors are where it expects them, the policy it writes still parses, and the runtime reads the
written grant as switching the soft halt on — without touching the committed policy."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import yaml

from runtime.mvp_runtime import control

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str):
    path = ROOT / "scripts" / "ops" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _applied_policy_text(bump) -> str:
    policy = (ROOT / bump.POLICY_REL).read_text(encoding="utf-8")
    policy = policy.replace(f"policy_version: {bump.OLD}\n", f"policy_version: {bump.NEW}\n", 1)
    for old, new in ((bump.GRANT_ANCHOR_OLD, bump.GRANT_ANCHOR_NEW), (bump.COMMANDS_ANCHOR_OLD, bump.COMMANDS_ANCHOR_NEW),
                     (bump.P5_COMMENT_OLD, bump.P5_COMMENT_NEW), (bump.STOP_COMMENT_OLD, bump.STOP_COMMENT_NEW),
                     (bump.SWITCH_COMMENT_OLD, bump.SWITCH_COMMENT_NEW)):
        policy = policy.replace(old, new, 1)
    return policy


def test_every_anchor_the_bump_edits_is_where_it_expects_while_the_policy_is_at_the_baseline():
    bump = _script("policy_bump_1_5_1")
    policy = (ROOT / bump.POLICY_REL).read_text(encoding="utf-8")
    if f"policy_version: {bump.OLD}\n" not in policy:
        return  # already bumped: the pin test the bump writes takes over
    for anchor in (bump.GRANT_ANCHOR_OLD, bump.COMMANDS_ANCHOR_OLD, bump.P5_COMMENT_OLD,
                   bump.STOP_COMMENT_OLD, bump.SWITCH_COMMENT_OLD):
        assert policy.count(anchor) == 1, anchor[:60]
    assert "halt_trading" not in policy


def test_the_written_policy_parses_and_the_runtime_reads_the_grant(tmp_path):
    bump = _script("policy_bump_1_5_1")
    if f"policy_version: {bump.OLD}\n" not in (ROOT / bump.POLICY_REL).read_text(encoding="utf-8"):
        return
    written = _applied_policy_text(bump)
    policy = yaml.safe_load(written)
    assert policy["policy_version"] == bump.NEW
    assert "halt_trading" in policy["control_channel"]["local_operator_console"]["emergency_controls_allowed"]
    assert "/halt_trading" in policy["kill_switch"]["commands"]
    # Nothing else in the two lists moved.
    before = yaml.safe_load((ROOT / bump.POLICY_REL).read_text(encoding="utf-8"))
    assert (set(policy["control_channel"]["local_operator_console"]["emergency_controls_allowed"])
            - set(before["control_channel"]["local_operator_console"]["emergency_controls_allowed"])) == {"halt_trading"}
    assert policy["kill_switch"]["kill_blocks"] == before["kill_switch"]["kill_blocks"]

    (tmp_path / "governance").mkdir()
    (tmp_path / bump.POLICY_REL).write_text(written, encoding="utf-8")
    assert control.CMD_HALT_TRADING in control.granted_emergency_controls(tmp_path)


def test_the_pin_test_the_bump_writes_is_valid_python():
    compile(_script("policy_bump_1_5_1").PIN_TEST, "pin_test", "exec")


def test_the_schedule_bump_applies_over_either_baseline():
    """1.5.1 first, then 1.6.0: the schedule bump must accept the soft-halt baseline, AND every
    anchor it edits must survive the text 1.5.1 writes (review of H2: this used to assert only the
    constant, so a later edit could break 1.6.0 --check after 1.5.1 with this test still green)."""
    schedule = _script("policy_bump_1_6_0")
    assert schedule.BASELINES == ("1.5.0", "1.5.1", "1.5.2")
    assert schedule.OLD in schedule.BASELINES
    soft = _script("policy_bump_1_5_1")
    if f"policy_version: {soft.OLD}\n" not in (ROOT / soft.POLICY_REL).read_text(encoding="utf-8"):
        return
    written = _applied_policy_text(soft)
    assert "policy_version: 1.5.1\n" in written
    assert written.count(schedule.MIRROR_TAIL + "\n" + schedule.LIFETIME_HEAD) == 1
    assert written.count(schedule.READ_CLAUSE_COMMENT_OLD) == 1
    assert "assistant_schedule:" not in written
