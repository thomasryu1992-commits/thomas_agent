"""Backup and restore of the workflow store (sequence 2, P10; V0.2 Q28; acceptance A24).

The live database is a WAL-mode SQLite file and is never tarred; what the archive carries is a
copy made with the backup API while the manager may be writing. These rehearse the whole loop
in an isolated root with no network and no credentials: snapshot under concurrent writes, verify
the copy from the copy alone, restore it into a fresh root, and prove the restored store keeps
its results, its cursor and its schema — and keeps working.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sqlite3
import threading

import pytest

from runtime.mvp_runtime import workflow as wf, workflow_cli
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.workflow_manager import WorkflowManager
from runtime.mvp_runtime.workflow_store import SCHEMA_VERSION, WORKFLOW_DIR_REL, WorkflowStore

NOW = "2026-09-14T13:00:00Z"
LATER = "2026-09-14T13:05:00Z"


def _plan(goal="목표"):
    return {"schema_version": "workflow_plan.v0.1", "goal": goal,
            "steps": [{"id": "a", "capability": "analysis", "request": "x", "reason": "r"}],
            "budget": {"max_model_calls": 2}}


def _complete(store, request_id):
    wid = store.submit(principal="hermes", request_id=request_id, plan=_plan(f"작업 {request_id}"), now=NOW).workflow_id
    (att,) = store.claim_ready(now=NOW)
    store.record_result(att.attempt_id, now=NOW, succeeded=True, result_ref=f"ledger:trace_{request_id}", model_calls=1)
    return wid


def test_a_snapshot_taken_under_concurrent_writes_is_consistent_and_verifiable(tmp_path):
    store = WorkflowStore(tmp_path)
    done = [_complete(store, f"r{i}") for i in range(3)]
    stop = threading.Event()
    written: list[str] = []

    def writer():
        n = 0
        while not stop.is_set():
            n += 1
            written.append(store.submit(principal="hermes", request_id=f"live-{n}", plan=_plan(), now=LATER).workflow_id)

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        copies = [store.snapshot(tmp_path / "snaps" / f"s{i}", now=LATER) for i in range(3)]
    finally:
        stop.set()
        thread.join(timeout=10)
    assert written                                             # the writer was really writing
    for copy in copies:
        report = workflow_cli.verify_snapshot(copy)
        assert report["ok"] and report["integrity"] == "ok" and report["schema_version"] == SCHEMA_VERSION
        assert report["manifest_agrees"] is True and report["counts"]["workflows"] >= 3
        assert report["steps_with_results"] == 3
        # every workflow in the copy is whole: its steps and its events are there
        conn = sqlite3.connect(f"file:{copy.as_posix()}?mode=ro", uri=True)
        try:
            for (wid,) in conn.execute("SELECT workflow_id FROM workflows").fetchall():
                assert conn.execute("SELECT COUNT(*) FROM steps WHERE workflow_id=?", (wid,)).fetchone()[0] == 1
                assert conn.execute("SELECT COUNT(*) FROM events WHERE workflow_id=?", (wid,)).fetchone()[0] >= 2
        finally:
            conn.close()
    assert set(done) <= {r for c in copies for r in _workflow_ids(c)}


def _workflow_ids(copy):
    conn = sqlite3.connect(f"file:{copy.as_posix()}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute("SELECT workflow_id FROM workflows").fetchall()]
    finally:
        conn.close()


def test_a_restored_snapshot_keeps_results_cursor_and_schema_and_keeps_working(tmp_path):
    source_root, restored_root = tmp_path / "source", tmp_path / "restored"
    store = WorkflowStore(source_root)
    wids = [_complete(store, f"r{i}") for i in range(2)]
    waiting = store.submit(principal="hermes", request_id="waiting", plan=_plan("아직"), now=NOW).workflow_id
    copy = store.snapshot(tmp_path / "snap", now=LATER)
    manifest = json.loads(copy.with_name(copy.name.replace(".db", ".manifest.json")).read_text(encoding="utf-8"))
    # the restore: the copy becomes the live file in a fresh root, with no -wal/-shm beside it
    target = restored_root / WORKFLOW_DIR_REL / "workflow.db"
    target.parent.mkdir(parents=True)
    shutil.copy(copy, target)
    assert not target.with_name("workflow.db-wal").exists() and not target.with_name("workflow.db-shm").exists()
    restored = WorkflowStore(restored_root)
    assert restored.max_event_cursor() == manifest["max_event_cursor"]
    for wid in wids:
        view = restored.status_view(wid, now=LATER)
        assert view["status"] == wf.W_COMPLETED and view["steps"][0]["result_ref"].startswith("ledger:trace_")
        assert view["budget"]["confirmed_model_calls"] == 1
    assert restored.status_view(waiting, now=LATER)["status"] == wf.W_VALIDATED
    conn = sqlite3.connect(str(target))
    try:
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == SCHEMA_VERSION
    finally:
        conn.close()
    # the restored store keeps working: the waiting workflow runs to completion on a manager
    manager = WorkflowManager(restored, control_store=ControlStore(restored_root),
                              worker_socket=restored_root / "internal" / "pipeline.sock",
                              call=lambda p, f, *, deadline_seconds: {"ok": True, "kind": f["kind"], "task_id": "task_9",
                                                                       "trace_id": "trace_9", "registry_entry_id": "treg_9",
                                                                       "final_response": "done", "actor": "assistant_bridge",
                                                                       "attempt_id": f["attempt_id"], "workflow_id": f["workflow_id"]},
                              door_is_live=lambda p: True, clock=lambda: LATER, synchronous=True, log=lambda line: None)
    assert manager.tick()["claimed"] == 1
    assert restored.status_view(waiting, now=LATER)["status"] == wf.W_COMPLETED
    new = restored.submit(principal="hermes", request_id="after-restore", plan=_plan(), now=LATER)
    assert not new.replayed and len(restored.list_workflows()) == 4


def test_the_cli_snapshot_lands_in_a_per_stamp_directory_verifies_and_skips_an_absent_store(tmp_path, capsys):
    assert workflow_cli.main(["snapshot", "--dest", str(tmp_path / "out")], repo_root=tmp_path, now=NOW) == 0
    assert capsys.readouterr().out.strip() == "snapshot skipped: no workflow store"       # absent, not failed
    assert not (tmp_path / "out").exists()
    store = WorkflowStore(tmp_path)
    _complete(store, "r1")
    dest = tmp_path / ".runtime_governance_state" / "workflow" / "snapshots" / "20260914-1300"
    assert workflow_cli.main(["snapshot", "--dest", str(dest)], repo_root=tmp_path, now=NOW) == 0
    line = capsys.readouterr().out.strip()
    copy = line.split(": ", 1)[1]
    assert pathlib.Path(copy).parent == dest and (dest / "workflow-20260914-130000.manifest.json").is_file()
    assert workflow_cli.main(["verify", "--snapshot", copy], repo_root=tmp_path, now=NOW) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and report["schema_version"] == SCHEMA_VERSION and report["counts"]["workflows"] == 1
    assert report["manifest_agrees"] is True and report["steps_with_results"] == 1
    assert workflow_cli.main(["verify", "--snapshot", str(tmp_path / "nope.db")], repo_root=tmp_path, now=NOW) == workflow_cli.EXIT_BLOCKED
    assert "WORKFLOW_SNAPSHOT_MISSING" in capsys.readouterr().err


def test_verify_refuses_a_copy_whose_manifest_disagrees_or_a_file_that_is_not_a_store(tmp_path, capsys):
    store = WorkflowStore(tmp_path)
    _complete(store, "r1")
    copy = store.snapshot(tmp_path / "snap", now=NOW)
    manifest_path = copy.with_name(copy.name.replace(".db", ".manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["max_event_cursor"] += 5
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report = workflow_cli.verify_snapshot(copy)
    assert report["manifest_agrees"] is False and report["ok"] is False
    bogus = tmp_path / "bogus.db"
    bogus.write_text("not a database", encoding="utf-8")
    assert workflow_cli.main(["verify", "--snapshot", str(bogus)], repo_root=tmp_path, now=NOW) == workflow_cli.EXIT_BLOCKED
    assert "WORKFLOW_SNAPSHOT_UNREADABLE" in capsys.readouterr().err


# --- the backup script and the watch (static pins; the script itself needs docker) ---------------------

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "ops" / "harness_backup.sh"


def test_the_backup_script_snapshots_the_store_as_its_owner_excludes_the_live_file_and_marks_the_log():
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "docker exec -u 10001 thomas-dispatch-bridge python -m runtime.mvp_runtime.workflow_cli snapshot" in text
    assert '--exclude="$WF_DIR/workflow.db" --exclude="$WF_DIR/workflow.db-*"' in text
    assert 'WF_DIR="$THOMAS/.runtime_governance_state/workflow"' in text
    for marker in ("workflow-snapshot=ok", "workflow-snapshot=FAILED", "workflow-snapshot=absent"):
        assert marker in text
    assert 'SNAP_NOTE="$SNAP_NOTE $WF_NOTE"' in text                  # the marker reaches both log lines
    # the snapshot directory is inside the tarred root, so the archive carries the copy
    assert "/app/.runtime_governance_state/workflow/snapshots/$STAMP" in text


def test_the_manifest_describes_the_copy_even_when_the_source_moves_after_the_backup(tmp_path, monkeypatch):
    """The race CI caught (P10): the manifest's cursor used to be read from the source after the
    backup, so a commit landing in between named events the copy does not hold. Forced here by
    committing from inside the backup call, after the pages were copied."""
    import sqlite3 as _sqlite3

    store = WorkflowStore(tmp_path)
    _complete(store, "r1")
    real_connect = store._connect

    class _Src:
        def __init__(self, conn):
            self._conn = conn

        def backup(self, dst):
            self._conn.backup(dst)
            store.submit(principal="hermes", request_id="between", plan=_plan("사이"), now=LATER)   # the source moves on

        def __getattr__(self, name):
            return getattr(self._conn, name)

    monkeypatch.setattr(store, "_connect", lambda: _Src(real_connect()))
    copy = store.snapshot(tmp_path / "snap", now=LATER)
    report = workflow_cli.verify_snapshot(copy)
    assert report["manifest_agrees"] is True and report["ok"] is True
    assert report["counts"]["workflows"] == 1                               # the copy predates the late commit
    assert store.max_event_cursor() > report["max_event_cursor"]



def test_a_failed_workflow_snapshot_keeps_the_last_good_one(tmp_path):
    """Review of P10 (2026-09-14): the backup script used to prune snapshot directories whether or
    not today's snapshot succeeded, deleting the only good copy. Run the script's snapshot block
    with a docker stub that fails, against a scratch host root."""
    import os
    import subprocess
    import sys

    if sys.platform == "win32":
        pytest.skip("harness_backup.sh is a bash script")
    host = tmp_path / "host"
    wf_dir = host / "thomas_agent" / ".runtime_governance_state" / "workflow"
    good = wf_dir / "snapshots" / "20260913-0745"
    good.mkdir(parents=True)
    (good / "workflow-20260913-074500.db").write_text("good", encoding="utf-8")
    (wf_dir / "workflow.db").write_text("live", encoding="utf-8")
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "docker").write_text("#!/bin/bash\nexit 2\n", encoding="utf-8")
    (stub / "docker").chmod(0o755)
    block = _SCRIPT.read_text(encoding="utf-8")
    start = block.index('    WF_DIR="$THOMAS/.runtime_governance_state/workflow"')
    end = block.index('    SNAP_NOTE="$SNAP_NOTE $WF_NOTE"')
    snippet = "set -u\nHOST_ROOT=%s\nTHOMAS=thomas_agent\nSTAMP=20260914-0745\nSNAP_NOTE=\n" % host + block[start:end] + 'echo "$WF_NOTE"\n'
    (wf_dir / "snapshots" / "20260914-0745").mkdir()                     # what a partial run leaves behind
    out = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True, timeout=30,
                         env={**os.environ, "PATH": f"{stub}:{os.environ['PATH']}"})
    assert out.stdout.strip() == "workflow-snapshot=FAILED", out
    assert (good / "workflow-20260913-074500.db").is_file()
    assert not (wf_dir / "snapshots" / "20260914-0745").exists()
