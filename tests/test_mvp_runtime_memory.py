"""R5.1 Memory candidate creation tests.

The builder is pure and needs no Core (governance gate + shape). The end-to-end check that
the worker attaches candidates to the output runs the pipeline, so it needs a local Core.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.memory import CANDIDATE_STATUS, MAX_CANDIDATES, build_memory_candidates

LOCAL_POINTER = Path(__file__).resolve().parents[1] / DEFAULT_POINTER_REL
requires_local_core = pytest.mark.skipif(not LOCAL_POINTER.is_file(), reason="no local Core activation")
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
