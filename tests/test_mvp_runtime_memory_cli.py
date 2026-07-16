"""R5 Working-memory maintenance CLI tests (status / prune)."""

from __future__ import annotations

import json

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
    rc = main(["prune", "--reason", "daily"], store=store, ledger=ledger, now=NOW)
    assert rc == 0
    assert "pruned 1" in capsys.readouterr().out
    assert [e["candidate_id"] for e in store.read_all()] == ["fresh"]
    event = json.loads((ledger.root / "memory_events.jsonl").read_text(encoding="utf-8").strip())
    assert event["removed_candidate_ids"] == ["stale"]


def test_prune_with_nothing_expired(tmp_path, capsys):
    store, ledger = _stores(tmp_path)
    store.append([_entry("fresh", FUTURE)])
    rc = main(["prune"], store=store, ledger=ledger, now=NOW)
    assert rc == 0
    assert "pruned 0" in capsys.readouterr().out
