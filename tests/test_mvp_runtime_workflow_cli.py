"""The workflow CLI reads and snapshots; it never writes coordination state (sequence 2, P04)."""

from __future__ import annotations

import json
import sqlite3

from runtime.mvp_runtime import workflow_cli
from runtime.mvp_runtime.workflow_store import WorkflowStore

NOW = "2026-09-14T08:00:00Z"


def _plan():
    return {"schema_version": "workflow_plan.v0.1", "goal": "목표",
            "steps": [{"id": "a", "capability": "analysis", "request": "x", "reason": "r"}],
            "budget": {"max_model_calls": 2}}


def test_list_inspect_and_events_read_the_store(tmp_path, capsys):
    assert workflow_cli.main(["list"], repo_root=tmp_path, now=NOW) == workflow_cli.EXIT_BLOCKED   # no store yet
    store = WorkflowStore(tmp_path)
    wid = store.submit(principal="hermes", request_id="r1", plan=_plan(), now=NOW).workflow_id
    capsys.readouterr()
    assert workflow_cli.main(["list"], repo_root=tmp_path, now=NOW) == 0
    out = capsys.readouterr().out
    assert wid in out and "VALIDATED" in out and "(1 workflow(s))" in out
    assert workflow_cli.main(["inspect", wid], repo_root=tmp_path, now=NOW) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["workflow_id"] == wid and view["steps"][0]["status"] == "READY" and view["as_of"] == NOW
    assert workflow_cli.main(["events", "--limit", "2"], repo_root=tmp_path, now=NOW) == 0
    out = capsys.readouterr().out
    assert "RECEIVED" in out and "next_cursor=2" in out
    assert workflow_cli.main(["inspect", "wf_" + "0" * 20], repo_root=tmp_path, now=NOW) == workflow_cli.EXIT_BLOCKED


def test_snapshot_writes_a_consistent_copy_and_a_manifest(tmp_path, capsys):
    store = WorkflowStore(tmp_path)
    store.submit(principal="hermes", request_id="r1", plan=_plan(), now=NOW)
    assert workflow_cli.main(["snapshot", "--dest", str(tmp_path / "out")], repo_root=tmp_path, now=NOW) == 0
    line = capsys.readouterr().out.strip()
    target = line.split(": ", 1)[1]
    conn = sqlite3.connect(target)
    assert conn.execute("SELECT COUNT(*) FROM workflows").fetchone()[0] == 1
    conn.close()
    assert (tmp_path / "out" / "workflow-20260914-080000.manifest.json").is_file()


def test_the_cli_opens_the_store_read_only(tmp_path):
    store = WorkflowStore(tmp_path)
    store.submit(principal="hermes", request_id="r1", plan=_plan(), now=NOW)
    before = store.path.stat().st_mtime_ns
    assert workflow_cli.main(["list"], repo_root=tmp_path, now=NOW) == 0
    assert store.path.stat().st_mtime_ns == before


def test_usage_errors_exit_64(tmp_path):
    assert workflow_cli.main(["nonsense"], repo_root=tmp_path) == workflow_cli.EXIT_USAGE
