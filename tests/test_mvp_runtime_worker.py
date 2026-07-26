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


# --- the prompt states the bar it will be judged against --------------------

@requires_local_core
def test_prompt_states_the_acceptance_criteria():
    """The three checks that make validate_agent_output withhold delivery are all
    compliance requirements — an empty field, not a missing insight. A model never told
    the bar cannot aim at it, and the run pays for the analysis either way, so the
    criteria are stated in the prompt rather than discovered afterwards."""
    from runtime.mvp_runtime.worker import ACCEPTANCE_CRITERIA, build_prompt

    task, assignment = _planned()
    prompt = build_prompt(task, assignment)
    assert ACCEPTANCE_CRITERIA in prompt
    for field in ("key_findings", "facts", "evidence_refs", "uncertainty", "assumptions"):
        assert field in ACCEPTANCE_CRITERIA
    # ASCII only: these strings reach a Windows cp949 console via the CLI banner path.
    ACCEPTANCE_CRITERIA.encode("ascii")


def test_prompt_version_tracks_the_prompt_text():
    """The invocation record names the prompt version; a changed prompt must not ride
    under the old one (the mvp_independent_validation.v3 / mvp_candidate_trial.v2
    precedent)."""
    from runtime.mvp_runtime.worker import PROMPT_VERSION

    assert PROMPT_VERSION == "mvp_business_analysis.v2"


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
def test_an_obedient_provider_does_not_breach_its_own_allowance():
    """`token_budget` caps input+output and is checked AFTER the call, so it cannot also be
    the output allowance handed to the provider. It was both: a provider emitting exactly
    what it had been granted breached by the size of the prompt, and the run was withheld
    after the tokens were spent. A 200-token prompt was enough."""
    from runtime.mvp_runtime.budgets import output_allowance

    granted: list[int] = []

    class _Obedient(MockProvider):
        def generate(self, prompt, *, max_output_tokens, timeout_seconds):
            granted.append(max_output_tokens)
            r = super().generate(prompt, max_output_tokens=max_output_tokens,
                                 timeout_seconds=timeout_seconds)
            r.input_tokens = 200
            r.output_tokens = max_output_tokens      # used the whole allowance, as permitted
            return r

    task, assignment = _planned()
    budget = assignment["execution_budget"]["limits"]["token_budget"]
    output, _invocation = run_analysis_worker(task, assignment, provider=_Obedient(), created_at=NOW)
    assert output["schema_version"].startswith("agent_output")
    assert granted == [output_allowance(budget)]
    assert granted[0] < budget, "the output allowance must leave room for the prompt"


@requires_local_core
def test_a_truncated_response_is_named_truncated_not_malformed():
    """`finish_reason` was recorded on every invocation and read by nothing, so an answer cut
    off at the output cap arrived as a malformed analysis and was reported as one — naming the
    symptom and sending the operator after a bad model instead of a small allowance."""
    class _Truncated(MockProvider):
        def generate(self, prompt, *, max_output_tokens, timeout_seconds):
            r = super().generate(prompt, max_output_tokens=max_output_tokens,
                                 timeout_seconds=timeout_seconds)
            r.finish_reason = "length"        # OpenAI-compatible spelling
            return r

    task, assignment = _planned()
    with pytest.raises(WorkerBlocked) as exc:
        run_analysis_worker(task, assignment, provider=_Truncated(), created_at=NOW)
    assert exc.value.reason_code == "RESPONSE_TRUNCATED"
    assert "output cap" in exc.value.reason


@requires_local_core
def test_externally_sized_prompt_text_is_bounded():
    """A search snippet is a web page's content and a memory entry is whatever was stored:
    neither is sized by this runtime, and both are interpolated into a prompt whose input half
    is a bounded reserve. Measured before the caps: 1,012 chars with no context, 21,489 with
    five search hits and ten memory entries."""
    from runtime.mvp_runtime.budgets import MAX_SEARCH_SNIPPET_CHARS
    from runtime.mvp_runtime.worker import build_prompt

    task, assignment = _planned()
    hits = [{"title": "t", "url": "https://example.com/1", "snippet": "가" * 20_000}]
    mem = [{"candidate_type": "reusable_knowledge", "content": "나" * 20_000}]
    val = [{"candidate_type": "reusable_knowledge", "validated_memory_id": "v1",
            "content": "다" * 20_000}]
    prompt = build_prompt(task, assignment, hits, mem, val)
    assert len(prompt) < 5_000, f"prompt grew to {len(prompt)} chars"
    assert "가" * (MAX_SEARCH_SNIPPET_CHARS + 1) not in prompt
    # ...and the truncation is stated, so the model cannot cite a clipped source as whole.
    assert "chars omitted]" in prompt


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


# --- M5c: a promoted correction is framed as a correction to apply -----------

def test_validated_context_frames_a_correction_distinctly():
    from runtime.mvp_runtime.worker import _validated_context

    plain = {"candidate_type": "reusable_knowledge", "content": "generic fact"}
    correction = {"candidate_type": "reusable_knowledge", "content": "표로 정리하라",
                  "learning_source": "revision"}
    block = _validated_context([plain, correction])
    # The plain entry keeps the ordinary framing; the correction gets the apply-this framing.
    assert "[V1] (reusable_knowledge) generic fact" in block
    assert "[V2] (operator-approved correction — apply to this similar request) 표로 정리하라" in block
    # ...and the model is instructed to prefer a marked correction.
    assert "prefer its guidance over your default approach" in block


def test_validated_context_without_a_correction_is_unchanged():
    """No correction present => byte-identical to the pre-M5c rendering (no PROMPT_VERSION bump)."""
    from runtime.mvp_runtime.worker import _validated_context

    block = _validated_context([{"candidate_type": "reusable_knowledge", "content": "x"}])
    assert block == ("\nValidated memory (operator-approved reusable knowledge; cite by [V#]):"
                     "\n[V1] (reusable_knowledge) x\n")
    assert "prefer its guidance" not in block
