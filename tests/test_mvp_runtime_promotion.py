"""R5 memory-candidate promotion tests (CANDIDATE -> VALIDATED).

Promotion is an explicit operator action only: the run pipeline never promotes
(automatic_runtime_promotion_allowed is false), so these are pure/store-level and need no
Core, except the no-auto-promotion end-to-end check.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.errors import MemoryBlocked
from runtime.mvp_runtime.memory import (
    CANDIDATE_STATUS,
    VALIDATED_SCOPE,
    VALIDATED_STATUS,
    promote_candidate,
)
from runtime.mvp_runtime.store import AUDIT_FILE, LedgerStore
from runtime.mvp_runtime.working_memory import WorkingMemoryStore
from scripts.promote_memory_candidate import main as promote_main


def _read_audit(ledger: LedgerStore) -> list[dict]:
    path = ledger.root / AUDIT_FILE
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]

from tests._helpers import requires_local_core
NOW = "2026-07-16T09:00:00Z"

# Originating-task provenance the pipeline now stamps on every candidate; promotion is audited
# against it (R5.4). data_sensitivity must be an audit-schema sensitivity enum value.
_ORIGIN = {
    "task_id": "task_origin1", "task_revision": 1, "trace_id": "trace_origin1",
    "core_context_binding_id": "ccb-origin1", "data_sensitivity": "INTERNAL",
}


def _candidate(**overrides):
    c = {
        "candidate_id": "memcand_x1", "candidate_type": "reusable_knowledge",
        "scope": "task_working_memory", "status": CANDIDATE_STATUS, "validated": False,
        "promotable": False, "content": "recurring-revenue model works", "created_at": NOW,
        "origin": dict(_ORIGIN),
    }
    c.update(overrides)
    return c


def test_promote_produces_validated_entry():
    v = promote_candidate(_candidate(), promoted_by="Thomas", reason="confirmed", now=NOW)
    assert v["status"] == VALIDATED_STATUS and v["scope"] == VALIDATED_SCOPE
    assert v["disposition"] == "EXECUTE_AND_REPORT"
    assert v["source_candidate_id"] == "memcand_x1"
    assert v["promoted_by"] == "Thomas" and v["promotion_reason"] == "confirmed"
    assert v["validated_memory_id"].startswith("valmem_")


@pytest.mark.parametrize("bad, code", [
    ({"status": "VALIDATED"}, "NOT_A_CANDIDATE"),        # already validated
    ({"scope": "related_validated_memory"}, "NOT_A_CANDIDATE"),
    ({"content": ""}, "INVALID_CANDIDATE"),
])
def test_promote_rejects_non_candidate(bad, code):
    with pytest.raises(MemoryBlocked) as exc:
        promote_candidate(_candidate(**bad), promoted_by="Thomas", reason="x", now=NOW)
    assert exc.value.reason_code == code


def test_promote_requires_operator_and_reason():
    with pytest.raises(MemoryBlocked) as exc:
        promote_candidate(_candidate(), promoted_by="  ", reason="x", now=NOW)
    assert exc.value.reason_code == "MISSING_OPERATOR"
    with pytest.raises(MemoryBlocked) as exc:
        promote_candidate(_candidate(), promoted_by="Thomas", reason="", now=NOW)
    assert exc.value.reason_code == "MISSING_REASON"


def test_validated_store_is_separate_from_candidates(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([_candidate()])
    store.append_validated([promote_candidate(_candidate(), promoted_by="Thomas", reason="ok", now=NOW)])
    assert [c["status"] for c in store.read_all()] == [CANDIDATE_STATUS]
    assert [v["status"] for v in store.read_validated()] == [VALIDATED_STATUS]


# --- operator promotion tool ------------------------------------------------

def test_operator_tool_promotes_and_audits(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    ledger = LedgerStore(tmp_path / "ledger")
    store.append([_candidate()])
    rc = promote_main(
        ["--candidate-id", "memcand_x1", "--promoted-by", "Thomas", "--reason", "confirmed"],
        store=store, ledger=ledger, now=NOW,
    )
    assert rc == 0
    validated = store.read_validated()
    assert len(validated) == 1 and validated[0]["source_candidate_id"] == "memcand_x1"

    # R5.4: the promotion is reported as its own OTHER/MEMORY_PROMOTED audit event, anchored to
    # the originating task and chained onto the ledger (genesis tip is None on a fresh ledger).
    events = _read_audit(ledger)
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "OTHER"
    assert set(["MEMORY_PROMOTED", "EXECUTE_AND_REPORT", "SOURCE_CANDIDATE_memcand_x1"]) <= set(
        ev["event"]["reason_codes"])
    assert ev["event"]["outcome"] == "RECORDED"
    assert ev["actor"] == {"actor_type": "thomas", "actor_id": "Thomas",
                           "role_id": None, "role_version": None, "assignment_id": None}
    assert ev["task_id"] == _ORIGIN["task_id"] and ev["trace_id"] == _ORIGIN["trace_id"]
    assert ev["subject"]["subject_id"] == validated[0]["validated_memory_id"]
    assert ev["lineage"]["previous_event_sha256"] is None


def test_operator_tool_chains_onto_ledger_tip(tmp_path):
    """A second promotion chains onto the first event's hash — tamper-evident across actions."""
    store = WorkingMemoryStore(tmp_path / "wm")
    ledger = LedgerStore(tmp_path / "ledger")
    store.append([_candidate(), _candidate(candidate_id="memcand_x2", content="a second finding")])
    assert promote_main(["--candidate-id", "memcand_x1", "--promoted-by", "Thomas", "--reason", "a"],
                        store=store, ledger=ledger, now=NOW) == 0
    assert promote_main(["--candidate-id", "memcand_x2", "--promoted-by", "Thomas", "--reason", "b"],
                        store=store, ledger=ledger, now=NOW) == 0
    events = _read_audit(ledger)
    assert len(events) == 2
    assert events[1]["lineage"]["previous_event_sha256"] == events[0]["integrity"]["event_sha256"]


def test_operator_tool_without_origin_fails_closed(tmp_path):
    """A candidate lacking origin provenance cannot be audited, so promotion fails closed and
    writes nothing — neither a validated entry nor an audit event."""
    store = WorkingMemoryStore(tmp_path / "wm")
    ledger = LedgerStore(tmp_path / "ledger")
    store.append([_candidate(origin=None)])
    rc = promote_main(
        ["--candidate-id", "memcand_x1", "--promoted-by", "Thomas", "--reason", "confirmed"],
        store=store, ledger=ledger, now=NOW,
    )
    assert rc == 1
    assert store.read_validated() == []
    assert _read_audit(ledger) == []


def test_operator_tool_unknown_candidate_fails(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    ledger = LedgerStore(tmp_path / "ledger")
    rc = promote_main(
        ["--candidate-id", "nope", "--promoted-by", "Thomas", "--reason", "x"],
        store=store, ledger=ledger, now=NOW,
    )
    assert rc == 2
    assert store.read_validated() == []
    assert _read_audit(ledger) == []


def test_operator_tool_requires_all_fields(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    ledger = LedgerStore(tmp_path / "ledger")
    store.append([_candidate()])
    rc = promote_main(["--candidate-id", "memcand_x1"], store=store, ledger=ledger, now=NOW)
    assert rc == 2
    assert store.read_validated() == []
    assert _read_audit(ledger) == []


# --- no auto-promotion (governance) -----------------------------------------

@requires_local_core
def test_pipeline_never_promotes(tmp_path):
    from runtime.mvp_runtime.pipeline import run_task
    from runtime.mvp_runtime.worker import MockProvider

    wm = WorkingMemoryStore(tmp_path / "wm")
    run_task("이 사업 아이디어를 분석해줘: 구독형 반려동물 사료", provider=MockProvider(), working_memory=wm, now=NOW)
    assert wm.read_all()            # candidates were created
    assert wm.read_validated() == []  # but the run never promoted anything (no auto-promotion)
