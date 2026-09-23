"""The 1.5.2 draft (the assistant's emergency-close ask), checked before anyone runs it.

The bump script is Thomas's to apply (decision Q2); these tests make sure that when he does, its anchor
is where it expects, the policy it writes still parses, the runtime reads the written grant as switching
the ask on, and the schedule bump (1.6.0) still applies after it — without touching the committed
policy."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from runtime.mvp_runtime import control, policy_fingerprint, switch_bridge

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str):
    path = ROOT / "scripts" / "ops" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _at_baseline(bump) -> bool:
    return f"policy_version: {bump.OLD}\n" in (ROOT / bump.POLICY_REL).read_text(encoding="utf-8")


def _applied_policy_text(bump) -> str:
    policy = (ROOT / bump.POLICY_REL).read_text(encoding="utf-8")
    policy = policy.replace(f"policy_version: {bump.OLD}\n", f"policy_version: {bump.NEW}\n", 1)
    return policy.replace(bump.GRANT_ANCHOR_OLD, bump.GRANT_ANCHOR_NEW, 1)


def test_the_anchor_the_bump_edits_is_where_it_expects_while_the_policy_is_at_the_baseline():
    bump = _script("policy_bump_1_5_2")
    if not _at_baseline(bump):
        return  # already bumped: the pin test the bump writes takes over
    policy = (ROOT / bump.POLICY_REL).read_text(encoding="utf-8")
    assert policy.count(bump.GRANT_ANCHOR_OLD) == 1
    assert "emergency_close:" not in policy


def test_the_written_policy_parses_and_the_runtime_reads_the_grant(tmp_path):
    bump = _script("policy_bump_1_5_2")
    if not _at_baseline(bump):
        return
    written = _applied_policy_text(bump)
    # The comment correction rides along: the door has carried `hard` since PR6a.
    assert "disable: fail_safe_immediate        # kill|pause|soft|hard, reason required" in written
    assert "# kill|pause|soft, reason required" not in written
    policy = yaml.safe_load(written)
    before = yaml.safe_load((ROOT / bump.POLICY_REL).read_text(encoding="utf-8"))
    assert policy["policy_version"] == bump.NEW
    after_switch, before_switch = policy["control_channel"]["assistant_switch"], before["control_channel"]["assistant_switch"]
    assert after_switch["verbs"] == {**before_switch["verbs"], "emergency_close": "approval_required_always"}
    # Nothing else in the door's clause, or elsewhere in the policy, moved.
    assert {k: v for k, v in after_switch.items() if k != "verbs"} == {
        k: v for k, v in before_switch.items() if k != "verbs"}
    assert {k: v for k, v in policy.items() if k not in ("policy_version", "control_channel")} == {
        k: v for k, v in before.items() if k not in ("policy_version", "control_channel")}
    assert {k: v for k, v in policy["control_channel"].items() if k != "assistant_switch"} == {
        k: v for k, v in before["control_channel"].items() if k != "assistant_switch"}

    (tmp_path / "governance").mkdir()
    (tmp_path / bump.POLICY_REL).write_text(written, encoding="utf-8")
    assert switch_bridge.CMD_EMERGENCY_CLOSE in control.granted_switch_verbs(tmp_path)
    assert switch_bridge.CMD_EMERGENCY_CLOSE not in control.granted_switch_verbs()


def test_the_safety_fingerprint_moves_so_a_rebind_follows(tmp_path):
    """`assistant_switch` is a safety section: the execution stage reads READ_ONLY after the deploy until
    Thomas approves a REBIND. The draft says so; this keeps it true."""
    bump = _script("policy_bump_1_5_2")
    if not _at_baseline(bump):
        return
    assert ("control_channel", "assistant_switch") in policy_fingerprint.SAFETY_SECTIONS
    (tmp_path / "governance").mkdir()
    (tmp_path / bump.POLICY_REL).write_text(_applied_policy_text(bump), encoding="utf-8")
    now, then = policy_fingerprint.policy_safety_identity(tmp_path), policy_fingerprint.policy_safety_identity()
    assert now["policy_version"] == bump.NEW and then["policy_version"] == bump.OLD
    assert now["policy_safety_sha256"] != then["policy_safety_sha256"]


def test_the_pin_test_the_bump_writes_is_valid_python():
    compile(_script("policy_bump_1_5_2").PIN_TEST, "pin_test", "exec")


def test_neither_pending_bump_finds_a_stray_version_literal():
    """1.6.0's --check refused over the 1.5.1 baseline for two test literals that pin nothing, and the
    1.5.1 test never noticed: it checked the anchors, not the stray scan (PR6e)."""
    for name in ("policy_bump_1_5_2", "policy_bump_1_6_0"):
        bump = _script(name)
        if not _at_baseline(bump):
            continue
        stray = bump._stray_sites(set(bump._literal_sites()))
        assert stray == [], f"{name}: {[p.relative_to(ROOT).as_posix() for p in stray]}"


def test_the_schedule_bump_applies_after_this_one():
    """1.5.2 first, then 1.6.0: the schedule bump accepts the 1.5.2 baseline, and every anchor it edits
    survives the text 1.5.2 writes."""
    schedule = _script("policy_bump_1_6_0")
    assert "1.5.2" in schedule.BASELINES
    bump = _script("policy_bump_1_5_2")
    if not _at_baseline(bump):
        return
    written = _applied_policy_text(bump)
    assert f"policy_version: {bump.NEW}\n" in written
    assert written.count(schedule.MIRROR_TAIL + "\n" + schedule.LIFETIME_HEAD) == 1
    assert written.count(schedule.READ_CLAUSE_COMMENT_OLD) == 1
    assert "assistant_schedule:" not in written
