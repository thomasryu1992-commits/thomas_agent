"""R5.1 Memory candidate creation tests.

The builder is pure and needs no Core (governance gate + shape). The end-to-end check that
the worker attaches candidates to the output runs the pipeline, so it needs a local Core.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.errors import PersistenceError
from runtime.mvp_runtime.memory import (
    CANDIDATE_STATUS,
    MAX_CANDIDATES,
    build_memory_candidates,
    retrieve_working_memory,
)
from runtime.mvp_runtime.working_memory import ENTRIES_FILE, WorkingMemoryStore

from tests._helpers import requires_local_core
NOW = "2026-07-16T09:00:00Z"

_ALLOWED = ["reusable_knowledge", "project_learning", "workflow_improvement"]


def _assignment(**memory_scope):
    scope = dict(memory_candidate_creation_allowed=True, allowed_candidate_types=list(_ALLOWED))
    scope.update(memory_scope)
    return {"memory_scope": scope}


def _analysis(findings):
    return {"key_findings": findings}


def test_candidates_created_when_allowed():
    cands = build_memory_candidates(_analysis(["a", "b"]), _assignment(), now=NOW, seed={"task_id": "t"})
    assert len(cands) == 2
    for c in cands:
        assert c["status"] == CANDIDATE_STATUS
        assert c["validated"] is False and c["promotable"] is False
        assert c["candidate_type"] in _ALLOWED
        assert c["scope"] == "task_working_memory"
        assert c["candidate_id"].startswith("memcand_")


def test_prefers_reusable_knowledge_type():
    cands = build_memory_candidates(_analysis(["x"]), _assignment(), now=NOW, seed={"task_id": "t"})
    assert cands[0]["candidate_type"] == "reusable_knowledge"


def test_uses_only_allowed_types():
    a = _assignment(allowed_candidate_types=["workflow_improvement"])
    cands = build_memory_candidates(_analysis(["x"]), a, now=NOW, seed={"task_id": "t"})
    assert cands[0]["candidate_type"] == "workflow_improvement"


def test_no_candidates_when_creation_not_allowed():
    a = _assignment(memory_candidate_creation_allowed=False)
    assert build_memory_candidates(_analysis(["a", "b"]), a, now=NOW, seed={"task_id": "t"}) == []


def test_no_candidates_when_no_allowed_types():
    a = _assignment(allowed_candidate_types=[])
    assert build_memory_candidates(_analysis(["a"]), a, now=NOW, seed={"task_id": "t"}) == []


def test_empty_findings_yields_no_candidates():
    assert build_memory_candidates(_analysis([]), _assignment(), now=NOW, seed={"task_id": "t"}) == []


def test_capped_at_max():
    findings = [f"finding {i}" for i in range(MAX_CANDIDATES + 4)]
    cands = build_memory_candidates(_analysis(findings), _assignment(), now=NOW, seed={"task_id": "t"})
    assert len(cands) == MAX_CANDIDATES


def test_deterministic_ids():
    a = build_memory_candidates(_analysis(["a", "b"]), _assignment(), now=NOW, seed={"task_id": "t"})
    b = build_memory_candidates(_analysis(["a", "b"]), _assignment(), now=NOW, seed={"task_id": "t"})
    assert [c["candidate_id"] for c in a] == [c["candidate_id"] for c in b]


# --- R5.2: working-memory store + retrieval ---------------------------------

def _readable_assignment(**memory_scope):
    scope = dict(
        readable_scopes=["task_working_memory", "related_validated_memory"],
        prohibited_scopes=["unrelated_private_memory", "restricted_memory"],
    )
    scope.update(memory_scope)
    return {"memory_scope": scope}


def _entry(cid, content, *, scope="task_working_memory", status=CANDIDATE_STATUS, created_at=NOW):
    return {"candidate_id": cid, "candidate_type": "reusable_knowledge", "scope": scope,
            "status": status, "validated": False, "promotable": False, "content": content,
            "evidence_refs": ["model:analysis"], "created_at": created_at}


def test_store_append_and_read(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    assert store.read_all() == []                        # empty store reads clean
    store.append([_entry("memcand_a", "alpha")])
    store.append([_entry("memcand_b", "beta")])
    assert [e["candidate_id"] for e in store.read_all()] == ["memcand_a", "memcand_b"]


def test_store_corrupt_read_fails_closed(tmp_path):
    root = tmp_path / "wm"
    root.mkdir()
    (root / ENTRIES_FILE).write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(PersistenceError) as exc:
        WorkingMemoryStore(root).read_all()
    assert exc.value.reason_code == "WORKING_MEMORY_UNREADABLE"


def test_retrieve_reads_scoped_candidates(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([
        _entry("memcand_1", "keep me", created_at="2026-07-16T09:00:00Z"),
        _entry("memcand_2", "wrong scope", scope="related_validated_memory"),
        _entry("memcand_3", "not a candidate", status="VALIDATED"),
        _entry("memcand_4", "keep me too", created_at="2026-07-16T10:00:00Z"),
    ])
    got = retrieve_working_memory(_readable_assignment(), store)
    ids = [e["candidate_id"] for e in got]
    assert ids == ["memcand_1", "memcand_4"]             # only task_working_memory CANDIDATEs, recency order


def test_retrieve_none_when_scope_not_readable(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([_entry("memcand_1", "x")])
    a = _readable_assignment(readable_scopes=["related_validated_memory"])  # task_working_memory not readable
    assert retrieve_working_memory(a, store) == []


def test_retrieve_none_when_scope_prohibited(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([_entry("memcand_1", "x")])
    a = _readable_assignment(prohibited_scopes=["task_working_memory"])
    assert retrieve_working_memory(a, store) == []


def test_retrieve_capped_and_recent(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([_entry(f"memcand_{i}", f"c{i}", created_at=f"2026-07-16T09:{i:02d}:00Z") for i in range(9)])
    got = retrieve_working_memory(_readable_assignment(), store, limit=3)
    assert [e["candidate_id"] for e in got] == ["memcand_6", "memcand_7", "memcand_8"]  # 3 most recent


@requires_local_core
def test_worker_attaches_candidates_to_output():
    from runtime.mvp_runtime.intake import build_task
    from runtime.mvp_runtime.prime import plan_task
    from runtime.mvp_runtime.worker import MockProvider, run_analysis_worker

    task = build_task("이 사업 아이디어를 분석해줘: 구독형 반려동물 사료", now=NOW)
    plan = plan_task(task, now=NOW)
    out, _ = run_analysis_worker(plan["task"], plan["role_assignment"], provider=MockProvider(), created_at=NOW)
    cands = out["memory_candidates"]
    assert cands and all(c["status"] == CANDIDATE_STATUS and c["validated"] is False for c in cands)
    # Candidates are proposals only — the assignment grants no validated/core write.
    assert plan["role_assignment"]["memory_scope"]["validated_memory_write_allowed"] is False
    assert plan["role_assignment"]["memory_scope"]["core_memory_write_allowed"] is False
