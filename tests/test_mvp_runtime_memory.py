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
    CANDIDATE_SCOPE,
    CANDIDATE_STATUS,
    LEARNING_EVENT_TYPE,
    LEARNING_SOURCE_FEEDBACK,
    LEARNING_SOURCE_REVISION,
    MAX_CANDIDATES,
    MAX_CORRECTION_CHARS,
    VALIDATED_STATUS,
    build_correction_candidate,
    build_learning_event,
    build_memory_candidates,
    candidate_type_for,
    retrieve_validated_memory,
    retrieve_working_memory,
)
from runtime.mvp_runtime.working_memory import ENTRIES_FILE, VALIDATED_FILE, WorkingMemoryStore

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


# --- VALIDATED-memory retrieval (the read leg of the promotion loop) --------


def _validated_entry(vid, content, *, scope="related_validated_memory",
                     status=VALIDATED_STATUS, promoted_at=NOW):
    return {"validated_memory_id": vid, "source_candidate_id": "memcand_src",
            "candidate_type": "reusable_knowledge", "scope": scope, "status": status,
            "disposition": "EXECUTE_AND_REPORT", "content": content,
            "evidence_refs": ["working_memory:memcand_src"], "promoted_by": "thomas",
            "promotion_reason": "test", "promoted_at": promoted_at}


def test_retrieve_validated_reads_scoped_entries(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append_validated([
        _validated_entry("valmem_1", "keep me", promoted_at="2026-07-16T09:00:00Z"),
        _validated_entry("valmem_2", "wrong scope", scope="task_working_memory"),
        _validated_entry("valmem_3", "wrong status", status=CANDIDATE_STATUS),
        _validated_entry("valmem_4", "keep me too", promoted_at="2026-07-16T10:00:00Z"),
    ])
    got = retrieve_validated_memory(_readable_assignment(), store)
    assert [e["validated_memory_id"] for e in got] == ["valmem_1", "valmem_4"]


def test_retrieve_validated_none_when_scope_not_readable(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append_validated([_validated_entry("valmem_1", "x")])
    a = _readable_assignment(readable_scopes=["task_working_memory"])  # validated scope not readable
    assert retrieve_validated_memory(a, store) == []


def test_retrieve_validated_none_when_scope_prohibited(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append_validated([_validated_entry("valmem_1", "x")])
    a = _readable_assignment(prohibited_scopes=["related_validated_memory"])
    assert retrieve_validated_memory(a, store) == []


def test_retrieve_validated_capped_and_recent(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append_validated([
        _validated_entry(f"valmem_{i}", f"v{i}", promoted_at=f"2026-07-16T09:{i:02d}:00Z")
        for i in range(9)
    ])
    got = retrieve_validated_memory(_readable_assignment(), store, limit=3)
    assert [e["validated_memory_id"] for e in got] == ["valmem_6", "valmem_7", "valmem_8"]


def test_retrieve_validated_empty_store_reads_clean(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    assert retrieve_validated_memory(_readable_assignment(), store) == []


def test_retrieve_validated_corrupt_store_fails_closed(tmp_path):
    root = tmp_path / "wm"
    root.mkdir()
    (root / VALIDATED_FILE).write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(PersistenceError) as exc:
        retrieve_validated_memory(_readable_assignment(), WorkingMemoryStore(root))
    assert exc.value.reason_code == "VALIDATED_MEMORY_UNREADABLE"


def test_prompt_carries_validated_block_distinct_from_candidates():
    from runtime.mvp_runtime.worker import build_prompt
    prompt = build_prompt({}, {}, None,
                          [_entry("memcand_1", "unverified hunch")],
                          [_validated_entry("valmem_1", "trusted fact")])
    assert "[V1]" in prompt and "trusted fact" in prompt
    assert "[M1]" in prompt and "unverified hunch" in prompt
    assert "operator-approved" in prompt          # validated is framed as reliable
    assert "unverified" in prompt                 # candidates stay framed as unverified


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


# --- M5a: correction-learning candidates -------------------------------------

def test_candidate_type_for_prefers_reusable_knowledge():
    assert candidate_type_for(_assignment()) == "reusable_knowledge"
    # Absent the preferred type, the first allowed (sorted) is chosen.
    assert candidate_type_for(_assignment(allowed_candidate_types=["zeta", "alpha"])) == "alpha"
    # Creation disallowed or no types => None (caller mints nothing, fail-closed).
    assert candidate_type_for(_assignment(memory_candidate_creation_allowed=False)) is None
    assert candidate_type_for(_assignment(allowed_candidate_types=[])) is None
    assert candidate_type_for({}) is None


def test_build_correction_candidate_shape():
    c = build_correction_candidate(
        "  prefer D over B  ", source=LEARNING_SOURCE_REVISION, now=NOW,
        seed={"trace_id": "tr1"}, correction_ref="trace:tr1",
    )
    assert c["status"] == CANDIDATE_STATUS and c["scope"] == CANDIDATE_SCOPE
    assert c["validated"] is False and c["promotable"] is False
    assert c["content"] == "prefer D over B"          # stripped
    assert c["learning_source"] == LEARNING_SOURCE_REVISION
    assert c["correction_ref"] == "trace:tr1"
    assert c["candidate_type"] == "reusable_knowledge"  # default
    assert c["evidence_refs"] == ["correction:revision"]
    assert c["expires_at"] > c["created_at"]            # retention stamp present


def test_build_correction_candidate_empty_delta_is_none():
    assert build_correction_candidate("", source=LEARNING_SOURCE_FEEDBACK, now=NOW) is None
    assert build_correction_candidate("   ", source=LEARNING_SOURCE_FEEDBACK, now=NOW) is None
    assert build_correction_candidate(None, source=LEARNING_SOURCE_FEEDBACK, now=NOW) is None


def test_build_correction_candidate_caps_length():
    c = build_correction_candidate("x" * (MAX_CORRECTION_CHARS + 500),
                                   source=LEARNING_SOURCE_FEEDBACK, now=NOW)
    assert len(c["content"]) == MAX_CORRECTION_CHARS


def test_correction_candidate_id_is_deterministic_and_content_sensitive():
    a = build_correction_candidate("same", source=LEARNING_SOURCE_REVISION, now=NOW, seed={"trace_id": "t"})
    b = build_correction_candidate("same", source=LEARNING_SOURCE_REVISION, now=NOW, seed={"trace_id": "t"})
    diff = build_correction_candidate("other", source=LEARNING_SOURCE_REVISION, now=NOW, seed={"trace_id": "t"})
    assert a["candidate_id"] == b["candidate_id"]
    assert a["candidate_id"] != diff["candidate_id"]


def test_correction_candidate_is_retrievable_and_promotable():
    """A correction candidate must ride the ordinary retrieval + promotion machinery so the
    learning loop reuses R5 wholesale — no separate path."""
    from runtime.mvp_runtime.memory import promote_candidate
    c = build_correction_candidate("prefer a table", source=LEARNING_SOURCE_FEEDBACK, now=NOW,
                                   seed={"trace_id": "t"})
    # Retrieval selects it like any CANDIDATE in scope.
    class _Store:
        def read_all(self_inner):
            return [c]
    got = retrieve_working_memory(_readable_assignment(), _Store(), now=NOW)
    assert [e["candidate_id"] for e in got] == [c["candidate_id"]]
    # Promotion accepts it (operator-authored guidance can still become VALIDATED via M5b).
    v = promote_candidate(c, promoted_by="op", reason="useful", now=NOW)
    assert v["status"] == VALIDATED_STATUS and v["content"] == "prefer a table"


def test_build_learning_event_shape():
    c = build_correction_candidate("d", source=LEARNING_SOURCE_REVISION, now=NOW, seed={"trace_id": "t"})
    ev = build_learning_event(c, source=LEARNING_SOURCE_REVISION, now=NOW, trace_id="tr1")
    assert ev["record_type"] == LEARNING_EVENT_TYPE
    assert ev["candidate_id"] == c["candidate_id"]
    assert ev["learning_source"] == LEARNING_SOURCE_REVISION and ev["trace_id"] == "tr1"
    assert ev["integrity"]["event_sha256"]     # self-hashed like every stamped event


# --- M5c: a promoted correction feeds back as a correction --------------------

def _corr_candidate_with_origin():
    """A revision-path correction candidate — it carries full origin, so it is promotable
    (the audited promotion path requires complete provenance)."""
    return build_correction_candidate(
        "유사 요청에서는 표로 정리하라", source=LEARNING_SOURCE_REVISION, now=NOW,
        seed={"trace_id": "trace_c"},
        origin={"task_id": "task_c", "task_revision": 1, "trace_id": "trace_c",
                "core_context_binding_id": "ccb-abc", "data_sensitivity": "internal"},
        correction_ref="trace:trace_c",
    )


def test_promotion_preserves_the_correction_marker():
    from runtime.mvp_runtime.memory import promote_candidate
    v = promote_candidate(_corr_candidate_with_origin(), promoted_by="thomas",
                          reason="반복되는 교정", now=NOW)
    assert v["status"] == VALIDATED_STATUS
    assert v["learning_source"] == LEARNING_SOURCE_REVISION      # marker carried forward
    assert v["correction_ref"] == "trace:trace_c"
    assert v["source_origin"]["task_id"] == "task_c"            # lineage still carried (R5.4)


def test_promotion_of_a_plain_candidate_has_no_correction_marker():
    from runtime.mvp_runtime.memory import promote_candidate
    plain = build_memory_candidates(_analysis(["ordinary knowledge"]), _assignment(), now=NOW,
                                    seed={"task_id": "t"},
                                    origin={"task_id": "t", "task_revision": 1, "trace_id": "tr",
                                            "core_context_binding_id": "ccb-x",
                                            "data_sensitivity": "internal"})[0]
    v = promote_candidate(plain, promoted_by="thomas", reason="reuse", now=NOW)
    assert "learning_source" not in v and "correction_ref" not in v


# --- a run that reached no model says so -------------------------------------

@requires_local_core
def test_invocation_metadata_says_whether_a_model_was_reached():
    """`select_provider` fails closed to the mock when `MVP_HOSTED_PROVIDER` is unset, and the
    run that follows produces findings and proposes candidates exactly like an analysis. It is
    not one. 190 of this host's 316 candidate rows are the mock's five canned strings, written
    in July while the var was unset, and nothing in the record said so at the time."""
    from runtime.mvp_runtime.intake import build_task
    from runtime.mvp_runtime.prime import plan_task
    from runtime.mvp_runtime.worker import MockProvider, run_analysis_worker

    class _RealEnough(MockProvider):
        """The mock, declaring the one thing that separates a fixture from a judgement."""
        model_invocation = True

    plan = plan_task(build_task("이 사업 아이디어를 분석해줘: 구독형 반려동물 사료", now=NOW), now=NOW)
    task, assignment = plan["task"], plan["role_assignment"]

    _out, meta = run_analysis_worker(task, assignment, provider=MockProvider(), created_at=NOW)
    assert meta["model_invocation"] is False
    assert meta["network_egress"] is False        # recorded beside it, and for the same reason

    _out, meta = run_analysis_worker(task, assignment, provider=_RealEnough(), created_at=NOW)
    assert meta["model_invocation"] is True
