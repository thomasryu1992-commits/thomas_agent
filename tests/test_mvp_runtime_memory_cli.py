"""R5 Working-memory maintenance CLI tests (status / prune)."""

from __future__ import annotations

import json

from runtime.mvp_runtime import control
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.memory import EXPIRES_AT
from runtime.mvp_runtime.memory_cli import main
from runtime.mvp_runtime.store import LedgerStore
from runtime.mvp_runtime.working_memory import WorkingMemoryStore

NOW = "2026-07-16T09:00:00Z"
PAST = "2026-07-16T08:00:00Z"
FUTURE = "2026-07-16T10:00:00Z"


def _entry(cid, expires_at):
    return {"candidate_id": cid, "candidate_type": "reusable_knowledge", "scope": "task_working_memory",
            "status": "CANDIDATE", "validated": False, "promotable": False, "content": cid,
            "evidence_refs": ["model:analysis"], "created_at": PAST, EXPIRES_AT: expires_at}


def _stores(tmp_path):
    return WorkingMemoryStore(tmp_path / "wm"), LedgerStore(tmp_path / "ledger")


def _control(tmp_path):
    return ControlStore(tmp_path)


def test_status_reports_counts(tmp_path, capsys):
    store, ledger = _stores(tmp_path)
    store.append([_entry("stale", PAST), _entry("fresh", FUTURE)])
    rc = main(["status"], store=store, ledger=ledger, now=NOW)
    assert rc == 0
    out = capsys.readouterr().out
    assert "candidates: 2" in out
    assert "expired as of now: 1" in out


def test_prune_deletes_expired_and_reports(tmp_path, capsys):
    store, ledger = _stores(tmp_path)
    store.append([_entry("stale", PAST), _entry("fresh", FUTURE)])
    rc = main(["prune", "--reason", "daily"], store=store, ledger=ledger,
              control_store=_control(tmp_path), now=NOW)
    assert rc == 0
    assert "pruned 1" in capsys.readouterr().out
    assert [e["candidate_id"] for e in store.read_all()] == ["fresh"]
    event = json.loads((ledger.root / "memory_events.jsonl").read_text(encoding="utf-8").strip())
    assert event["removed_candidate_ids"] == ["stale"]


def test_prune_with_nothing_expired(tmp_path, capsys):
    store, ledger = _stores(tmp_path)
    store.append([_entry("fresh", FUTURE)])
    rc = main(["prune"], store=store, ledger=ledger, control_store=_control(tmp_path), now=NOW)
    assert rc == 0
    assert "pruned 0" in capsys.readouterr().out


def test_prune_refused_while_killed_or_paused(tmp_path, capsys):
    """kill_allows lists only read_only_status/audit_read: prune deletes data and must be
    refused while the runtime is not ACTIVE — nothing is removed, no retention event."""
    store, ledger = _stores(tmp_path)
    store.append([_entry("stale", PAST)])
    control_store = _control(tmp_path)
    control.apply_command(control_store, "kill", actor="op", now=NOW)
    rc = main(["prune"], store=store, ledger=ledger, control_store=control_store, now=NOW)
    assert rc != 0
    assert "RUNTIME_KILLED" in capsys.readouterr().err
    assert [e["candidate_id"] for e in store.read_all()] == ["stale"]   # nothing deleted
    assert not (ledger.root / "memory_events.jsonl").exists()           # no event either


def test_status_still_answers_while_killed(tmp_path, capsys):
    store, ledger = _stores(tmp_path)
    store.append([_entry("fresh", FUTURE)])
    control_store = _control(tmp_path)
    control.apply_command(control_store, "kill", actor="op", now=NOW)
    rc = main(["status"], store=store, ledger=ledger, control_store=control_store, now=NOW)
    assert rc == 0                                                      # read-only: allowed
