"""R2.B persistence + fail-closed audit tests — the append-only runtime ledger.

Covers the store in isolation (no Core needed) and the pipeline's durability +
blocked-run auditing (full runs need a local Core activation).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.errors import PersistenceError, ProviderError
from runtime.mvp_runtime.pipeline import run_task
from runtime.mvp_runtime.store import AUDIT_FILE, BLOCKS_FILE, RECORDS_FILE, LedgerStore
from runtime.mvp_runtime.worker import MockProvider

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_POINTER = REPO_ROOT / DEFAULT_POINTER_REL
NOW = "2026-07-15T09:00:00Z"
REQUEST = "이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송"

requires_local_core = pytest.mark.skipif(not LOCAL_POINTER.is_file(), reason="no local Core activation")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


class _ErrorProvider:
    model_id, model_version, network_egress = "err", "0.1.0", False

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        raise ProviderError("BOOM", "provider exploded")


# --- LedgerStore in isolation (no Core) -------------------------------------

def test_last_audit_hash_none_when_empty(tmp_path):
    assert LedgerStore(tmp_path / "ledger").last_audit_hash() is None


def test_append_audit_events_and_read_tip(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    events = [
        {"integrity": {"event_sha256": "sha256:" + "a" * 64}},
        {"integrity": {"event_sha256": "sha256:" + "b" * 64}},
    ]
    store.append_audit_events(events)
    assert store.last_audit_hash() == "sha256:" + "b" * 64
    assert len(_read_jsonl(store.root / AUDIT_FILE)) == 2


def test_append_records_only_known_kinds(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    store.append_records("trace-1", {"task": {"x": 1}, "not_a_kind": {"y": 2}, "audit_trail": []})
    rows = _read_jsonl(store.root / RECORDS_FILE)
    assert [r["kind"] for r in rows] == ["task"]
    assert rows[0]["trace_id"] == "trace-1"


def test_corrupt_ledger_tip_fails_closed(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    (store.root).mkdir(parents=True)
    (store.root / AUDIT_FILE).write_text("{not json\n", encoding="utf-8")
    with pytest.raises(PersistenceError) as exc:
        store.last_audit_hash()
    assert exc.value.reason_code == "LEDGER_UNREADABLE"


def test_unwritable_ledger_fails_closed(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")  # a file where a directory parent is expected
    store = LedgerStore(blocker / "ledger")
    with pytest.raises(PersistenceError) as exc:
        store.append_block({"record_type": "run_block.v0"})
    assert exc.value.reason_code == "LEDGER_WRITE_FAILED"


# --- Pipeline durability: pre-binding blocks (no Core) ----------------------

def test_empty_request_persists_block_entry(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    r = run_task("", now=NOW, store=store)
    assert r["status"] == "BLOCKED" and r["block"]["reason_code"] == "EMPTY_REQUEST"
    blocks = _read_jsonl(store.root / BLOCKS_FILE)
    assert len(blocks) == 1 and blocks[0]["reason_code"] == "EMPTY_REQUEST"
    assert blocks[0]["request_sha256"].startswith("sha256:")
    assert not (store.root / AUDIT_FILE).exists()  # no bound task => no audit_event


def test_out_of_scope_persists_block_entry(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    r = run_task("분석해줘", now=NOW, constraints=["something_else"], store=store)
    assert r["block"]["reason_code"] == "OUT_OF_MVP_SCOPE"
    blocks = _read_jsonl(store.root / BLOCKS_FILE)
    assert len(blocks) == 1 and blocks[0]["stage"] == "pre_binding"


# --- Pipeline durability: full runs (need a Core) ---------------------------

@requires_local_core
def test_completed_run_persists_records_and_audit(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    r = run_task(REQUEST, provider=MockProvider(), now=NOW, store=store)
    assert r["status"] == "COMPLETED"
    audit = _read_jsonl(store.root / AUDIT_FILE)
    assert len(audit) == 6
    assert [e["event_type"] for e in audit][2:4] == ["OTHER", "OTHER"]  # TOOL_USED, MODEL_INVOKED
    kinds = {row["kind"] for row in _read_jsonl(store.root / RECORDS_FILE)}
    assert {"received_task", "task", "permission_decision", "search_permission_decision",
            "tool_use", "agent_output", "validation_result"} <= kinds


@requires_local_core
def test_audit_chain_spans_runs(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    run_task(REQUEST, provider=MockProvider(), now=NOW, store=store)
    run_task(REQUEST, provider=MockProvider(), now=NOW, store=store)
    audit = _read_jsonl(store.root / AUDIT_FILE)
    assert len(audit) == 12  # two 6-event runs
    # The second run's first event chains onto the first run's last event.
    assert audit[6]["lineage"]["previous_event_sha256"] == audit[5]["integrity"]["event_sha256"]


@requires_local_core
def test_blocked_run_after_binding_is_audited(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    r = run_task(REQUEST, provider=_ErrorProvider(), now=NOW, store=store)
    assert r["status"] == "BLOCKED" and r["block"]["reason_code"] == "PROVIDER_ERROR"
    audit = _read_jsonl(store.root / AUDIT_FILE)
    assert [e["event_type"] for e in audit] == ["TASK_CREATED", "TASK_STATE_CHANGED"]
    assert audit[1]["event"]["outcome"] == "BLOCKED"
    assert "PROVIDER_ERROR" in audit[1]["event"]["reason_codes"]


@requires_local_core
def test_completed_run_not_delivered_if_persistence_fails(tmp_path):
    class _BrokenStore(LedgerStore):
        def append_records(self, trace_id, records):
            raise PersistenceError("LEDGER_WRITE_FAILED", "disk full")

    r = run_task(REQUEST, provider=MockProvider(), now=NOW, store=_BrokenStore(tmp_path / "ledger"))
    assert r["status"] == "BLOCKED" and r["delivered"] is False
    assert r["block"]["stage"] == "persistence"
    assert r["block"]["reason_code"] == "LEDGER_WRITE_FAILED"
