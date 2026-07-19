"""R2.4 specialist worker tests (MockProvider — no network, no real model)."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.errors import ProviderError, WorkerBlocked
from runtime.mvp_runtime.intake import build_task
from runtime.mvp_runtime.prime import plan_task
from runtime.mvp_runtime.worker import MockProvider, ProviderResult, run_analysis_worker
from runtime.read_only_kernel import schema_validation

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_OUTPUT_SCHEMA = REPO_ROOT / "schemas" / "agent_output.v0.2.schema.json"
NOW = "2026-07-15T09:00:00Z"

from tests._helpers import requires_local_core


def _planned():
    task = build_task("이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송", now=NOW)
    plan = plan_task(task, now=NOW)
    return plan["task"], plan["role_assignment"]


class _ErrorProvider:
    model_id, model_version = "err", "0.1.0"

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        raise ProviderError("BOOM", "provider exploded")


class _TimeoutProvider:
    model_id, model_version = "slow", "0.1.0"

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        raise TimeoutError("deadline exceeded")


class _HugeTokenProvider(MockProvider):
    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        r = super().generate(prompt, max_output_tokens=max_output_tokens, timeout_seconds=timeout_seconds)
        r.output_tokens = 10 ** 9
        return r


class _BadAnalysisProvider:
    model_id, model_version = "bad", "0.1.0"

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        return ProviderResult(analysis={"summary": "x"}, model_id="bad", model_version="0.1.0",
                              input_tokens=1, output_tokens=1, latency_ms=0)


class _MessyButValidProvider(MockProvider):
    """A real-model-like provider whose facts/inferences don't perfectly match the schema
    (extra keys, missing evidence_refs, object inferences) — the worker must normalize them."""

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        r = super().generate(prompt, max_output_tokens=max_output_tokens, timeout_seconds=timeout_seconds)
        r.analysis = {
            **r.analysis,
            "facts": [
                {"statement": "no evidence key here", "evidence_regularity": "oops"},  # missing evidence_refs + extra key
                {"statement": "grounded", "evidence_refs": ["e1"]},
                "not even a dict",  # dropped
            ],
            "inferences": [{"statement": "object inference"}, "string inference", 42],
        }
        return r


# --- fail-closed (runs everywhere) ------------------------------------------

def test_unbound_task_blocks_everywhere():
    task = build_task("분석해줘", now=NOW)  # RECEIVED, unbound
    with pytest.raises(WorkerBlocked) as exc:
        run_analysis_worker(task, {"execution_budget": {"limits": {"max_model_calls": 1}}},
                            provider=MockProvider(), created_at=NOW)
    assert exc.value.reason_code == "NOT_BOUND"


# --- happy path + fail-closed needing a planned task (local Core) ------------

@requires_local_core
def test_worker_produces_schema_valid_output():
    task, assignment = _planned()
    out, meta = run_analysis_worker(task, assignment, provider=MockProvider(), created_at=NOW)
    schema_validation.validate_against_schema(out, AGENT_OUTPUT_SCHEMA, "test")
    assert out["schema_version"] == "agent_output.v0.2"
    assert out["status"] == "needs_validation"  # never "final" from the worker
    assert out["role_id"] == "general.specialist"
    assert out["task_id"] == task["identity"]["task_id"]
    assert out["assignment_id"] == assignment["assignment_id"]
    assert out["core_context_binding_id"] == task["context"]["core_context_binding_id"]
    assert out["validation_recommended"] is True
    assert meta["model_id"] == "mock.analysis" and meta["tokens_used"] > 0


@requires_local_core
def test_worker_is_deterministic_with_mock():
    task, assignment = _planned()
    a, _ = run_analysis_worker(task, assignment, provider=MockProvider(), created_at=NOW)
    b, _ = run_analysis_worker(task, assignment, provider=MockProvider(), created_at=NOW)
    assert a == b


@requires_local_core
def test_no_model_budget_blocks():
    task, assignment = _planned()
    assignment = {**assignment, "execution_budget": {"limits": {**assignment["execution_budget"]["limits"], "max_model_calls": 0}}}
    with pytest.raises(WorkerBlocked) as exc:
        run_analysis_worker(task, assignment, provider=MockProvider(), created_at=NOW)
    assert exc.value.reason_code == "NO_MODEL_BUDGET"


@requires_local_core
def test_provider_error_blocks():
    task, assignment = _planned()
    with pytest.raises(WorkerBlocked) as exc:
        run_analysis_worker(task, assignment, provider=_ErrorProvider(), created_at=NOW)
    assert exc.value.reason_code == "PROVIDER_ERROR"


@requires_local_core
def test_provider_timeout_blocks():
    task, assignment = _planned()
    with pytest.raises(WorkerBlocked) as exc:
        run_analysis_worker(task, assignment, provider=_TimeoutProvider(), created_at=NOW)
    assert exc.value.reason_code == "PROVIDER_ERROR"


@requires_local_core
def test_token_budget_exceeded_blocks():
    task, assignment = _planned()
    with pytest.raises(WorkerBlocked) as exc:
        run_analysis_worker(task, assignment, provider=_HugeTokenProvider(), created_at=NOW)
    assert exc.value.reason_code == "TOKEN_BUDGET_EXCEEDED"


@requires_local_core
def test_malformed_analysis_blocks():
    task, assignment = _planned()
    with pytest.raises(WorkerBlocked) as exc:
        run_analysis_worker(task, assignment, provider=_BadAnalysisProvider(), created_at=NOW)
    assert exc.value.reason_code == "MALFORMED_ANALYSIS"


@requires_local_core
def test_worker_normalizes_imperfect_model_output():
    # A real model may emit facts with extra/missing keys and mixed inference types; the
    # worker normalizes them into a schema-valid Agent Output instead of failing.
    task, assignment = _planned()
    out, _ = run_analysis_worker(task, assignment, provider=_MessyButValidProvider(), created_at=NOW)
    schema_validation.validate_against_schema(out, AGENT_OUTPUT_SCHEMA, "test")
    # Missing evidence defaulted; the non-dict fact dropped; both real facts kept.
    assert len(out["facts"]) == 2
    assert out["facts"][0]["evidence_refs"] == ["model:analysis"]
    assert all(isinstance(i, dict) and "statement" in i for i in out["inferences"])
    assert len(out["inferences"]) == 2  # the integer inference dropped
