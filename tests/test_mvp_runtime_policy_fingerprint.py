"""Q7-a: the policy this image runs under is recorded at startup and a change is announced —
never fail-closed. The file is baked into the image, so a change means a deploy or a rollback."""

from __future__ import annotations

import json

from runtime.mvp_runtime import policy_fingerprint as pf

NOW = "2026-09-04T13:00:00Z"
LATER = "2026-09-04T14:00:00Z"


def _policy(root, version="1.5.0", extra=""):
    path = root / "governance" / "GOVERNANCE_POLICY.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "schema_version: thomas_governance_policy.v1\n"
        "policy_id: thomas.governance.policy\n"
        f"policy_version: {version}\n"
        "control_channel:\n"
        "  assistant_read:\n"
        "    policy_version: not-the-top-level-one\n" + extra,
        encoding="utf-8")
    return path


def test_the_identity_is_the_files_hash_and_its_top_level_version(tmp_path):
    _policy(tmp_path, version="1.5.0")
    identity = pf.policy_identity(tmp_path)
    assert identity["policy_version"] == "1.5.0"          # not the indented one
    assert len(identity["sha256"]) == 64 and identity["bytes"] > 0
    _policy(tmp_path, version="1.5.0", extra="# a comment changes the bytes\n")
    assert pf.policy_identity(tmp_path)["sha256"] != identity["sha256"]


def test_a_missing_policy_is_reported_not_treated_as_unchanged(tmp_path):
    out = pf.check_and_record("operator", now=NOW, root=tmp_path)
    assert out["status"] == pf.POLICY_UNREADABLE and out["sha256"] is None
    assert "could not be read" in pf.banner(out)
    assert not pf.fingerprint_path("operator", tmp_path).exists()   # nothing to record


def test_first_start_records_and_says_so_then_a_second_start_is_unchanged(tmp_path):
    _policy(tmp_path)
    first = pf.check_and_record("operator", now=NOW, root=tmp_path)
    assert first["status"] == pf.FIRST_SEEN and first["recorded"] is True
    assert "first recording" in pf.banner(first)
    record = json.loads(pf.fingerprint_path("operator", tmp_path).read_text(encoding="utf-8"))
    assert record["policy_version"] == "1.5.0" and record["seen_at"] == NOW and record["pid"] > 0

    second = pf.check_and_record("operator", now=LATER, root=tmp_path)
    assert second["status"] == pf.UNCHANGED and "unchanged" in pf.banner(second)


def test_a_changed_policy_is_announced_once_and_names_both_versions(tmp_path):
    _policy(tmp_path, version="1.4.0")
    pf.check_and_record("operator", now=NOW, root=tmp_path)
    _policy(tmp_path, version="1.5.0")

    changed = pf.check_and_record("operator", now=LATER, root=tmp_path)

    assert changed["status"] == pf.CHANGED
    assert changed["previous_policy_version"] == "1.4.0" and changed["policy_version"] == "1.5.0"
    assert changed["previous_sha256"] != changed["sha256"]
    line = pf.banner(changed)
    assert line.startswith("POLICY CHANGED: 1.4.0") and "-> 1.5.0" in line
    notice = pf.change_notice(changed)
    assert "1.4.0" in notice and "1.5.0" in notice and "배포로만 바뀝니다" in notice
    # Recorded, so the next start is quiet: the point is to notice each change once.
    assert pf.check_and_record("operator", now=LATER, root=tmp_path)["status"] == pf.UNCHANGED


def test_a_rollback_reads_as_a_change_in_the_other_direction(tmp_path):
    """The failure this exists for: `docker tag rollback-pre-839 latest` puts the old policy
    back under services that were approving against the new one."""
    _policy(tmp_path, version="1.5.0")
    pf.check_and_record("scheduler-risk", now=NOW, root=tmp_path)
    _policy(tmp_path, version="1.4.0")
    out = pf.check_and_record("scheduler-risk", now=LATER, root=tmp_path)
    assert out["status"] == pf.CHANGED and out["previous_policy_version"] == "1.5.0"
    assert out["policy_version"] == "1.4.0"


def test_each_service_keeps_its_own_record_so_a_deploy_is_seen_by_all_of_them(tmp_path):
    """One shared pointer would let whichever process won the startup race record the new
    hash while the others read it back as UNCHANGED — the change announced by nobody."""
    _policy(tmp_path, version="1.4.0")
    services = ("operator", "scheduler-risk", "scheduler-maintenance", "pipeline-worker")
    for service in services:
        assert pf.check_and_record(service, now=NOW, root=tmp_path)["status"] == pf.FIRST_SEEN
    _policy(tmp_path, version="1.5.0")
    assert [pf.check_and_record(s, now=LATER, root=tmp_path)["status"] for s in services] == [pf.CHANGED] * 4
    assert sorted(p.stem for p in pf.fingerprints_dir(tmp_path).glob("*.json")) == sorted(services)


def test_an_unwritable_state_dir_still_compares_and_never_raises(tmp_path, monkeypatch):
    _policy(tmp_path)
    monkeypatch.setattr(pf.Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    out = pf.check_and_record("operator", now=NOW, root=tmp_path)
    assert out["status"] == pf.FIRST_SEEN and out["recorded"] is False and out["record_error"] == "OSError"
    assert pf.banner(out)          # still says which policy is running


def test_an_unreadable_previous_record_is_a_first_seen_not_a_crash(tmp_path):
    _policy(tmp_path)
    path = pf.fingerprint_path("operator", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert pf.read_fingerprint("operator", tmp_path) is None
    assert pf.check_and_record("operator", now=NOW, root=tmp_path)["status"] == pf.FIRST_SEEN
