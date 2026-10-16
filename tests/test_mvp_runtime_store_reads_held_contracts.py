"""Reply contracts v1 for two reads no door profile serves yet: heartbeat and runtime_status.

Why: ``docs/history/2026-10-16-claude-read-door-profile.md`` (review R3-1, R3-2). Implemented under Thomas's
2026-10-15 instruction for the repository only: neither read is added to any profile, and these tests
passing is not an approval to add one. Every store is a synthetic tmp_path; every secret-looking string
is a fake marker.
"""
from __future__ import annotations

import json
import os
import types
from pathlib import Path

import pytest

from runtime.mvp_runtime import control, heartbeat, read_bridge, scheduler, store_reads
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import ControlBlocked
from runtime.mvp_runtime.store import LedgerStore

NOW = "2026-10-15T03:00:00Z"
FAKE = "FAKE-SECRET-7f3a"
FAKE_PATH = "/root/fake-state/holdings/x.json"
FAKE_ID = "tg-999000111"
MARKERS = (FAKE, FAKE_PATH, FAKE_ID)


def _clean(obj):
    blob = json.dumps(obj, ensure_ascii=False)
    return not any(m in blob for m in MARKERS)


def _tree(root: Path):
    return sorted((str(p.relative_to(root)), p.stat().st_size) for p in root.rglob("*"))


# --- R3-1: heartbeat ------------------------------------------------------------------------------------------

def _stamp(root, service, record):
    path = heartbeat.heartbeat_path(service, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record if isinstance(record, str) else json.dumps(record), encoding="utf-8")


def _fixture(root):
    _stamp(root, heartbeat.OPERATOR_SERVICE, {"service": "operator", "heartbeat_at": FAKE_PATH,
                                              "interval_seconds": 30, "pid": 4242, "token": FAKE})
    _stamp(root, heartbeat.SCHEDULER_RISK_SERVICE, {
        "service": "scheduler-risk", "heartbeat_at": "2026-10-15T02:59:30Z", "interval_seconds": 30, "pid": 4243,
        "busy": {"kind": scheduler.KIND_FACTORY, "schedule_id": "schedule_" + "a" * 20,
                 "schedule_run_id": "srun_" + "b" * 20, "started_at": "2026-10-15T02:59:00Z",
                 "deadline_at": "2026-10-15T03:10:00Z", "secret": FAKE}})
    _stamp(root, heartbeat.SCHEDULER_MAINTENANCE_SERVICE, {
        "service": "scheduler-maintenance", "heartbeat_at": "2026-10-15T02:59:30Z", "interval_seconds": 30,
        "busy": {"kind": FAKE, "started_at": FAKE_PATH}})


def test_r31_inspect_heartbeat_returns_the_unchanged_report_and_a_failure_code(tmp_path):
    _fixture(tmp_path)
    for service, code in ((heartbeat.OPERATOR_SERVICE, heartbeat.RECORD_UNREADABLE),
                          (heartbeat.SCHEDULER_RISK_SERVICE, None),
                          (heartbeat.SCHEDULER_MAINTENANCE_SERVICE, heartbeat.BUSY_MARK_UNREADABLE)):
        report, failure = heartbeat.inspect_heartbeat(service, now=NOW, root=tmp_path)
        assert report == heartbeat.check_heartbeat(service, now=NOW, root=tmp_path) and failure == code
    missing, failure = heartbeat.inspect_heartbeat(heartbeat.SCHEDULER_SERVICE, now=NOW, root=tmp_path)
    assert missing["status"] == heartbeat.MISSING and failure is None
    assert "failure" not in json.dumps(heartbeat.check_heartbeat(heartbeat.OPERATOR_SERVICE, now=NOW, root=tmp_path))


def test_r31_the_projected_heartbeat_carries_no_detail_pid_or_busy_extras(tmp_path):
    _fixture(tmp_path)
    raw = store_reads.read_heartbeat(now=NOW, repo_root=tmp_path)
    assert FAKE_PATH in json.dumps(raw) and FAKE in json.dumps(raw), "the raw read really carries them"
    before = _tree(tmp_path)
    out = store_reads.read_heartbeat(now=NOW, repo_root=tmp_path, projection=store_reads.PROJECTION_V1)
    assert _tree(tmp_path) == before, "a projected read writes nothing"
    assert _clean(out) and "4242" not in json.dumps(out) and "pid" not in json.dumps(out)
    op, risk, maint = out["data"]["services"]
    assert op == {"service": "operator", "status": "UNREADABLE", "unreadable_part": "record", "projection": "v1"}
    assert risk["busy"] == {"kind": scheduler.KIND_FACTORY, "overrun": False,
                            "started_at": "2026-10-15T02:59:00Z", "deadline_at": "2026-10-15T03:10:00Z"}
    assert maint["status"] == "UNREADABLE" and maint["unreadable_part"] == "busy_mark"
    assert out["data"]["all_fresh"] is False and out["action"] == "HEARTBEAT_CHECKED"
    assert set(out["data"]) == {"services", "all_fresh", "as_of", "projection"}


def test_r31_unknown_malformed_and_unexplained_values_are_marked_not_guessed():
    weird = store_reads.project_heartbeat_check(
        {"service": FAKE, "status": "SPARKLING", "age_seconds": -1, "interval_seconds": float("nan"),
         "heartbeat_at": NOW + "\n", "new_field": FAKE, "detail": FAKE})
    assert weird == {"service": "UNKNOWN", "status": "UNKNOWN", "projection": "v1-partial"}
    unexplained = store_reads.project_heartbeat_check({"service": "operator", "status": "UNREADABLE"}, None)
    assert "unreadable_part" not in unexplained and unexplained["projection"] == "v1-partial"
    bad_busy = store_reads.project_heartbeat_check({"service": "operator", "status": "FRESH", "busy": FAKE})
    assert "busy" not in bad_busy and bad_busy["projection"] == "v1-partial"
    assert store_reads.project_heartbeat_check("not a dict")["projection"] == "v1-partial"


def test_r31_missing_stamps_are_missing_and_complete(tmp_path):
    out = store_reads.read_heartbeat(now=NOW, repo_root=tmp_path, projection="v1")
    assert [r["status"] for r in out["data"]["services"]] == ["MISSING"] * 3
    assert out["data"]["projection"] == "v1" and out["data"]["all_fresh"] is False


def test_r31_the_schema_refuses_a_field_or_value_outside_the_contract():
    row = {"service": "operator", "status": "FRESH", "projection": "v1"}
    for bad in ({**row, "detail": FAKE}, {**row, "pid": 1}, {**row, "heartbeat_at": NOW + "\n"},
                {**row, "busy": {"overrun": False, "secret": FAKE}}):
        with pytest.raises(ControlBlocked) as exc:
            store_reads._checked({"services": [bad], "all_fresh": False, "as_of": NOW, "projection": "v1"},
                                 store_reads._HEARTBEAT_DATA_SCHEMA)
        assert exc.value.reason_code == "REPLY_SCHEMA_INVALID" and FAKE not in str(exc.value)


def test_r31_the_unprojected_heartbeat_is_the_assistants_unchanged(tmp_path):
    _fixture(tmp_path)
    raw = store_reads.read_heartbeat(now=NOW, repo_root=tmp_path)
    checks = [heartbeat.check_heartbeat(s, now=NOW, root=tmp_path) for s in store_reads.HEARTBEAT_SERVICES]
    assert raw["data"]["services"] == checks


# --- R3-2: runtime_status from ControlState ---------------------------------------------------------------------

def _state_file(root, body):
    store = ControlStore(root)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    return store


def test_r32_runtime_status_carries_the_facts_never_who_or_why(tmp_path):
    store = _state_file(tmp_path, {"mode": "PAUSED", "updated_by": FAKE_ID, "updated_at": "2026-10-15T02:00:00Z",
                                   "reason": f"/pause {FAKE} {FAKE_PATH}", "stop_requested_task_ids": ["t1", FAKE],
                                   "trading_armed": False, "halt_level": "SOFT", "extra": FAKE})
    assert FAKE_ID in control.status_lines(store.load(), ledger=None), "the raw /status text carries them"
    before = _tree(tmp_path)
    out = store_reads.read_runtime_status(store, now=NOW, projection="v1")
    assert _tree(tmp_path) == before, "a projected read writes nothing"
    assert _clean(out) and "t1" not in json.dumps(out)
    assert out["data"] == {"mode": "PAUSED", "execution_allowed": False, "trading_armed": False, "halt_level": "SOFT",
                           "fail_closed": False, "stop_requested_count": 2, "updated_at": "2026-10-15T02:00:00Z",
                           "as_of": NOW, "projection": "v1"}


def test_r32_an_unreadable_state_reads_fail_closed_without_its_text(tmp_path):
    out = store_reads.read_runtime_status(_state_file(tmp_path, "{not json " + FAKE), now=NOW, projection="v1")
    assert out["data"]["mode"] == "KILLED" and out["data"]["fail_closed"] is True and _clean(out)
    assert "updated_at" not in out["data"]                    # the fail-closed state has none


def test_r32_no_state_file_and_no_ledger_is_the_default_and_writes_nothing(tmp_path):
    before = _tree(tmp_path)
    out = store_reads.read_runtime_status(ControlStore(tmp_path), now=NOW, projection="v1")
    assert _tree(tmp_path) == before
    assert out["data"]["mode"] == "ACTIVE" and out["data"]["execution_allowed"] is True


def test_r32_a_state_with_unknown_values_or_new_fields_is_marked_and_not_forwarded():
    odd = types.SimpleNamespace(mode="WEIRD", halt_level="MEDIUM", stop_requested_task_ids=FAKE,
                                trading_armed="yes", fail_closed=1, updated_at=f"{FAKE}", updated_by=FAKE_ID,
                                reason=FAKE, new_secret_field=FAKE)
    data = store_reads.project_runtime_status(odd, now=NOW)
    assert data == {"mode": "UNKNOWN", "execution_allowed": False, "trading_armed": False, "halt_level": None,
                    "fail_closed": False, "stop_requested_count": 0, "as_of": NOW, "projection": "v1-partial"}
    with pytest.raises(ControlBlocked) as exc:
        store_reads.read_runtime_status(None, now=NOW, projection="v1")
    assert exc.value.reason_code == "CONTROL_STORE_UNAVAILABLE"
    with pytest.raises(ControlBlocked) as exc:
        store_reads.read_runtime_status(ControlStore(Path("/nonexistent")), now=NOW, projection="v9")
    assert exc.value.reason_code == "PROJECTION_UNKNOWN"


def test_r32_the_schema_refuses_who_and_why():
    data = {"mode": "ACTIVE", "execution_allowed": True, "trading_armed": True, "halt_level": None, "fail_closed": False,
            "stop_requested_count": 0, "as_of": NOW, "projection": "v1"}
    for extra in ({"updated_by": FAKE_ID}, {"reason": FAKE}, {"stop_requested_task_ids": [FAKE]}):
        with pytest.raises(ControlBlocked):
            store_reads._checked({**data, **extra}, store_reads._RUNTIME_STATUS_SCHEMA)


# --- neither read is served by a profile; the wiring is ready if one ever is --------------------------------------

def test_both_reads_stay_held_out_of_every_profile(tmp_path):
    claude = read_bridge.PROFILES[read_bridge.CLAUDE_PROFILE]
    assert {"heartbeat", "runtime_status"} <= read_bridge._PROFILE_HELD_READS
    assert not {"heartbeat", "runtime_status"} & (claude.reads | read_bridge._PROFILE_ADMISSIBLE_READS)
    for verb in ("heartbeat", "runtime_status"):
        with pytest.raises(ControlBlocked) as exc:
            read_bridge.apply_read({"command": verb}, control_store=ControlStore(tmp_path), ledger=None, now=NOW,
                                   repo_root=tmp_path, profile=claude)
        assert exc.value.reason_code == "VERB_NOT_PERMITTED"


def test_the_applier_routes_a_projecting_door_to_the_contracts(tmp_path, monkeypatch):
    # A test-only profile, patched in for this test alone, to show what apply_read would do IF a profile
    # carried these reads. It registers nothing: PROFILES and the admissible set are restored after.
    trial = read_bridge.DoorProfile(name="trial", reads=frozenset({"heartbeat", "runtime_status"}), projection="v1")
    monkeypatch.setattr(read_bridge, "PROFILES", {"trial": trial})
    monkeypatch.setattr(read_bridge, "_PROFILE_ADMISSIBLE_READS", frozenset({"heartbeat", "runtime_status"}))
    _fixture(tmp_path)
    store = _state_file(tmp_path, {"mode": "ACTIVE", "updated_by": FAKE_ID, "reason": FAKE})
    for verb in ("heartbeat", "runtime_status"):
        out = read_bridge.apply_read({"command": verb}, control_store=store, ledger=LedgerStore(tmp_path), now=NOW,
                                     repo_root=tmp_path, profile=trial)
        assert out["ok"] is True and out["data"]["projection"] in ("v1", "v1-partial") and _clean(out), verb


def test_without_a_profile_runtime_status_is_the_assistants_unchanged(tmp_path):
    store = _state_file(tmp_path, {"mode": "PAUSED", "updated_by": FAKE_ID, "reason": "operator text"})
    out = read_bridge.apply_read({"command": "runtime_status"}, control_store=store, ledger=None, now=NOW,
                                 repo_root=tmp_path)
    assert FAKE_ID in out["reply"] and out["data"] == {"mode": "PAUSED", "changed": False, "action": "status"}
