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
NOW = "2026-07-15T09:00:00Z"
REQUEST = "이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송"

from tests._helpers import requires_local_core


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


class _ErrorProvider:
    model_id, model_version, network_egress = "err", "0.1.0", False

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        raise ProviderError("BOOM", "provider exploded")


# --- LedgerStore in isolation (no Core) -------------------------------------

def test_last_audit_hash_none_when_empty(tmp_path):
    assert LedgerStore(tmp_path / "ledger").last_audit_hash() is None


def _chainable_event(seq: int, previous: str | None) -> dict:
    """A structurally minimal audit event rechain can re-anchor (payload + lineage + hash)."""
    from runtime.read_only_kernel import integrity

    payload = {"audit_event_id": f"audit_{seq}", "event_summary": "e",
               "previous_event_sha256": previous, "sequence_number": seq}
    return {
        "audit_event_id": f"audit_{seq}",
        "event": {"event_summary": "e"},
        "lineage": {"previous_event_sha256": previous, "sequence_number": seq},
        "integrity": {"event_fingerprint_payload": payload,
                      "event_sha256": integrity.sha256_value(payload)},
    }


def test_append_audit_events_and_read_tip(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    first = _chainable_event(1, None)
    second = _chainable_event(2, first["integrity"]["event_sha256"])
    store.append_audit_events([first, second])
    assert store.last_audit_hash() == second["integrity"]["event_sha256"]
    assert len(_read_jsonl(store.root / AUDIT_FILE)) == 2


def test_append_rechains_a_segment_built_against_a_stale_tip(tmp_path):
    """Two runs that both read the same tip before appending (multi-process deployment)
    must not fork the chain: the second segment is re-anchored under the ledger lock, so
    the persisted ledger is one continuous chain and the caller's dicts match it."""
    store = LedgerStore(tmp_path / "ledger")
    batch_a = [_chainable_event(1, None)]
    batch_b = [_chainable_event(1, None)]  # built against the SAME stale (empty) tip
    store.append_audit_events(batch_a)
    store.append_audit_events(batch_b)

    rows = _read_jsonl(store.root / AUDIT_FILE)
    assert len(rows) == 2
    # The second event now links to the first — no fork — and its hash was recomputed
    # over the rewritten payload (in place, so batch_b sees the persisted values).
    assert rows[1]["lineage"]["previous_event_sha256"] == rows[0]["integrity"]["event_sha256"]
    assert rows[1]["integrity"]["event_sha256"] == batch_b[0]["integrity"]["event_sha256"]
    from runtime.mvp_runtime.audit import verify_audit_chain
    assert verify_audit_chain([
        {**row, "integrity": row["integrity"]} for row in rows
    ])["checked"] == 2
    assert store.last_audit_hash() == rows[1]["integrity"]["event_sha256"]


def test_append_records_rejects_unknown_kinds(tmp_path):
    """An unrecognized kind fails closed instead of being silently dropped — the silent-drop
    path once swallowed the R8 write records while their audit events survived."""
    store = LedgerStore(tmp_path / "ledger")
    with pytest.raises(PersistenceError) as exc:
        store.append_records("trace-1", {"task": {"x": 1}, "not_a_kind": {"y": 2}, "audit_trail": []})
    assert exc.value.reason_code == "LEDGER_UNKNOWN_RECORD_KIND"
    assert not (store.root / RECORDS_FILE).exists()  # nothing partial was written


def test_append_records_persists_known_kinds_and_skips_non_record_keys(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    store.append_records("trace-1", {
        "task": {"x": 1}, "write_permission_decision": {"d": 1}, "write_use": {"w": 1},
        "audit_trail": [], "block_record": None, "memory_retrieved": [],
        "validated_memory_retrieved": [],
    })
    rows = _read_jsonl(store.root / RECORDS_FILE)
    assert [r["kind"] for r in rows] == ["task", "write_permission_decision", "write_use"]
    assert rows[0]["trace_id"] == "trace-1"


def test_read_records_returns_rows_in_append_order(tmp_path):
    """M4b reads this stream to count the unreviewed proposal backlog."""
    store = LedgerStore(tmp_path / "ledger")
    assert store.read_records() == []                       # empty stream reads clean
    store.append_records("trace-1", {"crypto_strategy_proposal": {"proposals": []}})
    store.append_records("trace-2", {"task": {"x": 1}})
    rows = store.read_records()
    assert [r["kind"] for r in rows] == ["crypto_strategy_proposal", "task"]
    assert rows[0]["trace_id"] == "trace-1" and rows[0]["record"] == {"proposals": []}


def test_corrupt_ledger_tip_fails_closed(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    (store.root).mkdir(parents=True)
    (store.root / AUDIT_FILE).write_text("{not json\n", encoding="utf-8")
    with pytest.raises(PersistenceError) as exc:
        store.last_audit_hash()
    assert exc.value.reason_code == "LEDGER_UNREADABLE"


# --- the shared JSONL primitive --------------------------------------------

def test_the_streaming_reader_and_the_list_reader_agree(tmp_path):
    """``read_objects`` is now ``list(iter_objects(...))``. It used to ``read_text()`` the whole
    file into one string and ``splitlines()`` it into a second full copy before parsing a row,
    so opening a store cost several times its own size. The crypto board OOM-killed on that
    shape and was repaired with a private streaming reader, which left the shape at the
    primitive for the next store to grow into — and the PM1 observation store did."""
    from runtime.mvp_runtime import jsonl

    path = tmp_path / "s.jsonl"
    rows = [{"i": i, "note": "x"} for i in range(5)]
    jsonl.append_lines(path, rows, write_code="W", label="s")
    assert jsonl.read_objects(path, read_code="R", label="s") == rows
    assert list(jsonl.iter_objects(path, read_code="R", label="s")) == rows
    # Absent file: empty, not an error — the same answer from both doors.
    missing = tmp_path / "nope.jsonl"
    assert jsonl.read_objects(missing, read_code="R", label="s") == []
    assert list(jsonl.iter_objects(missing, read_code="R", label="s")) == []


def test_a_bad_line_fails_the_list_reader_wholesale_and_the_stream_where_it_sits(tmp_path):
    """The one behavioural difference, stated rather than discovered: a list either completes
    or raises, so a caller that materializes still gets all-or-nothing. A caller that streams
    has already seen the rows before the bad one, and must treat a partial read as partial."""
    from runtime.mvp_runtime import jsonl

    path = tmp_path / "s.jsonl"
    path.write_text('{"i": 0}\n{"i": 1}\n{not json\n', encoding="utf-8")

    with pytest.raises(PersistenceError) as exc:
        jsonl.read_objects(path, read_code="R", label="s")
    assert exc.value.reason_code == "R"

    stream = jsonl.iter_objects(path, read_code="R", label="s")
    assert [next(stream), next(stream)] == [{"i": 0}, {"i": 1}]
    with pytest.raises(PersistenceError):
        next(stream)


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
    assert len(audit) == 7
    assert [e["event_type"] for e in audit][2:5] == ["OTHER", "OTHER", "MEMORY_CANDIDATE_CREATED"]
    kinds = {row["kind"] for row in _read_jsonl(store.root / RECORDS_FILE)}
    assert {"received_task", "task", "permission_decision", "search_permission_decision",
            "tool_use", "agent_output", "validation_result"} <= kinds


@requires_local_core
def test_audit_chain_spans_runs(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    run_task(REQUEST, provider=MockProvider(), now=NOW, store=store)
    run_task(REQUEST, provider=MockProvider(), now=NOW, store=store)
    audit = _read_jsonl(store.root / AUDIT_FILE)
    assert len(audit) == 14  # two 7-event runs
    # The second run's first event chains onto the first run's last event.
    assert audit[7]["lineage"]["previous_event_sha256"] == audit[6]["integrity"]["event_sha256"]


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
