"""R5 Working-memory retention tests — expiry stamping, expiry-aware retrieval, prune, audit.

Policy §12.4: working memory expires; expired candidates are not served as context and are
deleted by the retention pass (§15 audits the deletion). None of this needs a local Core.
"""

from __future__ import annotations

import json

from runtime.mvp_runtime import memory
from runtime.mvp_runtime.memory import (
    EXPIRES_AT,
    WORKING_MEMORY_TTL_MINUTES,
    build_memory_candidates,
    is_expired,
    prune_working_memory,
    retrieve_working_memory,
)
from runtime.mvp_runtime.store import LedgerStore
from runtime.mvp_runtime.working_memory import ENTRIES_FILE, VALIDATED_FILE, WorkingMemoryStore

NOW = "2026-07-16T09:00:00Z"
PAST = "2026-07-16T08:00:00Z"
FUTURE = "2026-07-16T10:00:00Z"


def _analysis(findings):
    return {"key_findings": findings}


def _assignment():
    return {"memory_scope": {"memory_candidate_creation_allowed": True,
                             "allowed_candidate_types": ["reusable_knowledge"]}}


def _readable_assignment():
    return {"memory_scope": {"readable_scopes": ["task_working_memory"], "prohibited_scopes": []}}


def _entry(cid, *, expires_at=None, status="CANDIDATE", scope="task_working_memory", created_at=NOW):
    e = {"candidate_id": cid, "candidate_type": "reusable_knowledge", "scope": scope,
         "status": status, "validated": False, "promotable": False, "content": cid,
         "evidence_refs": ["model:analysis"], "created_at": created_at}
    if expires_at is not None:
        e[EXPIRES_AT] = expires_at
    return e


# --- stamping + is_expired --------------------------------------------------

def test_candidates_are_stamped_with_expiry():
    from runtime.mvp_runtime import timeutil
    cands = build_memory_candidates(_analysis(["a"]), _assignment(), now=NOW, seed={"task_id": "t"})
    assert cands[0][EXPIRES_AT] == timeutil.plus_minutes(NOW, WORKING_MEMORY_TTL_MINUTES)


def test_ttl_override_changes_expiry_not_id():
    a = build_memory_candidates(_analysis(["a"]), _assignment(), now=NOW, seed={"task_id": "t"}, ttl_minutes=10)
    b = build_memory_candidates(_analysis(["a"]), _assignment(), now=NOW, seed={"task_id": "t"}, ttl_minutes=99)
    assert a[0]["candidate_id"] == b[0]["candidate_id"]        # id is seed-derived, expiry-independent
    assert a[0][EXPIRES_AT] != b[0][EXPIRES_AT]


def test_is_expired_semantics():
    assert is_expired(_entry("c", expires_at=PAST), NOW) is True
    assert is_expired(_entry("c", expires_at=FUTURE), NOW) is False
    assert is_expired(_entry("c", expires_at=NOW), NOW) is True   # at-or-before
    assert is_expired(_entry("c", expires_at=None), NOW) is False  # legacy: never expires


# --- retrieval filters expired ----------------------------------------------

def test_retrieve_excludes_expired(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([
        _entry("fresh", expires_at=FUTURE),
        _entry("stale", expires_at=PAST),
        _entry("legacy", expires_at=None),
    ])
    got = [e["candidate_id"] for e in retrieve_working_memory(_readable_assignment(), store, now=NOW)]
    assert "stale" not in got
    assert set(got) == {"fresh", "legacy"}


# --- prune deletes expired, audits, leaves the rest -------------------------

def test_prune_removes_only_expired(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([
        _entry("fresh", expires_at=FUTURE),
        _entry("stale1", expires_at=PAST),
        _entry("stale2", expires_at=PAST),
        _entry("legacy", expires_at=None),
    ])
    removed = store.prune_expired(NOW)
    assert sorted(e["candidate_id"] for e in removed) == ["stale1", "stale2"]
    remaining = [e["candidate_id"] for e in store.read_all()]
    assert set(remaining) == {"fresh", "legacy"}


def test_prune_no_expired_is_noop(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([_entry("fresh", expires_at=FUTURE), _entry("legacy", expires_at=None)])
    assert store.prune_expired(NOW) == []
    assert len(store.read_all()) == 2


def test_prune_working_memory_audits_deletion(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    ledger = LedgerStore(tmp_path / "ledger")
    store.append([_entry("stale", expires_at=PAST), _entry("fresh", expires_at=FUTURE)])
    summary = prune_working_memory(store, ledger, now=NOW, reason="retention")
    assert summary["removed_count"] == 1
    rows = (ledger.root / "memory_events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    event = json.loads(rows[0])
    assert event["action"] == "prune_working_memory"
    assert event["removed_candidate_ids"] == ["stale"]
    assert event["integrity"]["event_sha256"].startswith("sha256:")


def test_prune_nothing_records_no_event(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    ledger = LedgerStore(tmp_path / "ledger")
    store.append([_entry("fresh", expires_at=FUTURE)])
    prune_working_memory(store, ledger, now=NOW, reason="retention")
    assert not (ledger.root / "memory_events.jsonl").is_file()


def test_prune_leaves_validated_memory_untouched(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([_entry("stale", expires_at=PAST)])
    store.append_validated([{"validated_memory_id": "v1", "scope": "related_validated_memory",
                             "status": "VALIDATED", "content": "keep"}])
    store.prune_expired(NOW)
    assert store.read_all() == []                        # expired candidate gone
    assert len(store.read_validated()) == 1              # validated memory untouched


def test_prune_rewrites_candidates_file(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([_entry("stale", expires_at=PAST), _entry("fresh", expires_at=FUTURE)])
    store.prune_expired(NOW)
    rows = (store.root / ENTRIES_FILE).read_text(encoding="utf-8").strip().splitlines()
    assert [json.loads(r)["candidate_id"] for r in rows] == ["fresh"]
    assert not (store.root / (ENTRIES_FILE + ".tmp")).exists()  # temp cleaned up by os.replace
