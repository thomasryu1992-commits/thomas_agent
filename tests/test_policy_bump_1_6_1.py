"""The 1.6.1 draft (the assistant's lane digest read, review D8), checked before anyone runs it.

The bump script is Thomas's to apply (decision Q2); these tests make sure that when he does, its anchors
are where it expects, the policy it writes still parses and moves nothing else, the runtime reads the
written grant as switching the read on, and the draft's REBIND sentence stays true — without touching
the committed policy."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from runtime.mvp_runtime import approval, control, policy_fingerprint, read_bridge
from runtime.mvp_runtime.approval_store import ApprovalStore
from runtime.mvp_runtime.control import ControlStore

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str = "policy_bump_1_6_1"):
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
    policy = policy.replace(bump.GRANT_ANCHOR_OLD, bump.GRANT_ANCHOR_NEW, 1)
    return policy.replace(bump.PIN_COMMENT_OLD, bump.PIN_COMMENT_NEW, 1)


def _written_root(tmp_path, bump) -> Path:
    (tmp_path / "governance").mkdir(exist_ok=True)
    (tmp_path / bump.POLICY_REL).write_text(_applied_policy_text(bump), encoding="utf-8")
    return tmp_path


def test_the_anchors_the_bump_edits_are_where_it_expects_while_the_policy_is_at_the_baseline():
    bump = _script()
    if not _at_baseline(bump):
        return  # already bumped: the pin test the bump writes takes over
    policy = (ROOT / bump.POLICY_REL).read_text(encoding="utf-8")
    assert policy.count(bump.GRANT_ANCHOR_OLD) == 1
    assert policy.count(bump.PIN_COMMENT_OLD) == 1
    assert "- lane_digest" not in policy


def test_the_written_policy_adds_the_one_verb_and_moves_nothing_else():
    bump = _script()
    if not _at_baseline(bump):
        return
    policy = yaml.safe_load(_applied_policy_text(bump))
    before = yaml.safe_load((ROOT / bump.POLICY_REL).read_text(encoding="utf-8"))
    assert policy["policy_version"] == bump.NEW
    after_read, before_read = policy["control_channel"]["assistant_read"], before["control_channel"]["assistant_read"]
    assert after_read["verbs"] == [*before_read["verbs"], "lane_digest"]
    assert {k: v for k, v in after_read.items() if k != "verbs"} == {
        k: v for k, v in before_read.items() if k != "verbs"}
    assert {k: v for k, v in policy.items() if k not in ("policy_version", "control_channel")} == {
        k: v for k, v in before.items() if k not in ("policy_version", "control_channel")}
    assert {k: v for k, v in policy["control_channel"].items() if k != "assistant_read"} == {
        k: v for k, v in before["control_channel"].items() if k != "assistant_read"}
    # The written list is exactly what the door serves: no read is left dormant after the grant.
    assert set(after_read["verbs"]) == set(read_bridge._READS)


def test_the_grant_switches_the_dormant_read_on_end_to_end(tmp_path):
    """Under the committed policy the door refuses the read by name; under the written one it answers."""
    bump = _script()
    if not _at_baseline(bump):
        return
    assert "lane_digest" not in control.granted_read_verbs()
    root = _written_root(tmp_path, bump)
    assert "lane_digest" in control.granted_read_verbs(root)

    class _Ledger:
        def iter_records_with_archive(self, **_kw):
            return iter(())

    out = read_bridge.apply_read({"command": "lane_digest"}, control_store=ControlStore(tmp_path),
                                 ledger=_Ledger(), now="2026-09-26T00:00:00Z", repo_root=root)
    assert out["ok"] is True and out["data"]["lanes"] == {} and out["data"]["days"] == 7


def test_the_safety_fingerprint_does_not_move_but_the_version_does_so_a_rebind_still_follows(tmp_path):
    """`assistant_read` is not a safety section, and the draft says so; the execution stage binds the
    version as well, so the REBIND sentence in the draft stays true."""
    bump = _script()
    if not _at_baseline(bump):
        return
    assert ("control_channel", "assistant_read") not in policy_fingerprint.SAFETY_SECTIONS
    now = policy_fingerprint.policy_safety_identity(_written_root(tmp_path, bump))
    then = policy_fingerprint.policy_safety_identity()
    assert now["policy_version"] == bump.NEW and then["policy_version"] == bump.OLD
    assert now["policy_safety_sha256"] == then["policy_safety_sha256"]


def test_the_pin_test_the_bump_writes_is_valid_python():
    compile(_script().PIN_TEST, "pin_test", "exec")


def test_the_bump_finds_no_stray_version_literal():
    bump = _script()
    if not _at_baseline(bump):
        return
    stray = bump._stray_sites(set(bump._literal_sites()))
    assert stray == [], [p.relative_to(ROOT).as_posix() for p in stray]


def _store_with(tmp_path, status: str, *, expires_at: str) -> Path:
    store = ApprovalStore.default(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("", encoding="utf-8")
    if status:
        store.append([{"approval_id": "approval_x", "status": status,
                       "validity": {"issued_at": "2026-09-26T00:00:00Z", "expires_at": expires_at}}])
    return tmp_path


def test_the_check_refuses_an_absent_store_and_any_live_pending_or_unspent_approval(tmp_path, monkeypatch):
    bump = _script()
    monkeypatch.setattr(bump, "STATE_ROOT", tmp_path / "nowhere")
    assert bump._outstanding_live()[1] is not None                      # absent is a problem, not zero

    for status in (approval.STATUS_PENDING, approval.STATUS_APPROVED):
        root = _store_with(tmp_path / status, status, expires_at="2999-01-01T00:00:00Z")
        monkeypatch.setattr(bump, "STATE_ROOT", root)
        outstanding, problem = bump._outstanding_live()
        assert problem is None and outstanding == [f"approval_x ({status})"]

    for status, expires_at in ((approval.STATUS_CONSUMED, "2999-01-01T00:00:00Z"),
                               (approval.STATUS_APPROVED, "2000-01-01T00:00:00Z"), ("", "")):
        root = _store_with(tmp_path / f"quiet-{status or 'empty'}-{expires_at[:4]}", status, expires_at=expires_at)
        monkeypatch.setattr(bump, "STATE_ROOT", root)
        assert bump._outstanding_live() == ([], None)
