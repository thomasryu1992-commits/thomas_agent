"""R7 Independent Validation Agent — the second agent of the minimal dynamic team.

A separate agent instance (its own role assignment, actor id, permission decision, budget,
and a fresh execution context) reviews the specialist's Agent Output and returns a
``validation_result.v0.1`` with ``validator_type: ROLE`` / ``validation_mode: INDEPENDENT``
and a PASS / REVISE / BLOCK verdict, per the active ``validation.independent`` role contract.

Independence (role contract + policy §3.4):
- The validator is a different actor instance in a new execution context; independence is
  **verified programmatically** (different actor, different assignment, different role than
  the output's creator) and recorded as ``independence_verified``.
- The prompt gives the validator the goal, the original request, and the output itself —
  not the specialist's prompt, working-memory context, or search context — so the review
  starts from goal/input/result rather than the creator's reasoning (anti-confirmation-bias).
- The validator never modifies the original output (``mutates_subject: false``).

Provider reuse (no new network surface): the validator asks the model for the same
12-key analysis JSON the providers already parse — the verdict rides in
``recommendation.action`` (exactly PASS / REVISE / BLOCK), findings in ``key_findings``,
required revisions in ``next_actions``, remaining risks in ``risks``. The gated provider
chokepoints (`select_provider`, Safety-Flag Gate, egress re-check) are untouched.

Fail-closed direction: a provider/transport failure means no review happened — that raises
(pipeline BLOCK). A response whose verdict is missing or not PASS/REVISE/BLOCK is a review
that failed to produce a usable judgement — per the role ("근거가 부족하면 BLOCK"), that
becomes a **BLOCK verdict**, never a silent PASS.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity, schema_validation
from runtime.read_only_kernel.integrity import IntegrityError
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from .authority import validation_result_permission_boundary, validation_result_runtime_effect
from .errors import ProviderError, WorkerBlocked
from .paths import repo_root as _repo_root
from .validation import VALIDATION_RESULT_SCHEMA_VERSION, ValidationError
from .worker import Provider, ProviderResult

VALIDATOR_WORKER_ID = "mvp.independent_validation.llm"
VALIDATOR_WORKER_VERSION = "0.1.0"
VALIDATOR_PROMPT_VERSION = "mvp_independent_validation.v1"

_VERDICTS = ("PASS", "REVISE", "BLOCK")
_SEVERITY = {"PASS": 0, "REVISE": 1, "BLOCK": 2}
_NEXT_STATE = {"PASS": "DELIVER_FINAL_RESPONSE", "REVISE": "REVISION_REQUIRED", "BLOCK": "BLOCKED_WITH_REASON"}


def stricter_result(a: str, b: str) -> str:
    """The stricter of two PASS/REVISE/BLOCK results (governance: stricter_rule_wins).
    An unknown value fails closed to BLOCK."""
    if a not in _SEVERITY or b not in _SEVERITY:
        return "BLOCK"
    return a if _SEVERITY[a] >= _SEVERITY[b] else b


class MockValidatorProvider:
    """Deterministic validator provider: no network, no real model. Returns a PASS verdict
    in the shared analysis JSON shape (verdict in ``recommendation.action``)."""

    model_id = "mock.validation"
    model_version = "0.1.0"
    network_egress = False  # deterministic, in-process; no outbound call

    def generate(self, prompt: str, *, max_output_tokens: int, timeout_seconds: int) -> ProviderResult:
        analysis = {
            "summary": "Deterministic mock independent review: the output addresses the goal, "
            "separates facts from inferences, and discloses uncertainty; not a real model judgement.",
            "key_findings": [
                "objective_alignment_check: output addresses the stated objective",
                "evidence_check: facts carry evidence references",
                "logic_check: inferences follow from the stated facts",
                "output_contract_check: required sections are present",
            ],
            "facts": [
                {"statement": "The reviewed output contains the required analysis sections.",
                 "evidence_refs": ["model:validation"]},
            ],
            "inferences": ["The output is internally consistent for its stated scope."],
            "assumptions": ["The supplied output is the exact artifact to review."],
            "uncertainty": ["Independent review cannot verify live external facts."],
            "risks": [],
            "recommendation": {"action": "PASS", "reason": "No blocking findings; quality criteria met."},
            "limitations": ["Mock review; no real model judgement was exercised."],
            "next_actions": [],
            "evidence_quality": "review_of_internal_artifact",
            "unresolved_questions": [],
        }
        return ProviderResult(
            analysis=analysis, model_id=self.model_id, model_version=self.model_version,
            input_tokens=min(len(prompt) // 4, max_output_tokens), output_tokens=120,
            latency_ms=0, finish_reason="stop",
        )


def _output_digest(agent_output: Mapping[str, Any]) -> str:
    """Render the reviewed output's content for the validator prompt (result, not reasoning)."""
    lines = [f"Summary: {agent_output.get('summary', '')}"]
    rso = agent_output.get("role_specific_output", {}) or {}
    for finding in rso.get("key_findings", []) or []:
        lines.append(f"- Finding: {finding}")
    for fact in agent_output.get("facts", []) or []:
        if isinstance(fact, Mapping):
            lines.append(f"- Fact: {fact.get('statement', '')} (evidence: {', '.join(fact.get('evidence_refs', []))})")
    for inference in agent_output.get("inferences", []) or []:
        statement = inference.get("statement") if isinstance(inference, Mapping) else inference
        lines.append(f"- Inference: {statement}")
    for key, label in (("assumptions", "Assumption"), ("uncertainty", "Uncertainty"),
                       ("risks", "Risk"), ("limitations", "Limitation")):
        for item in agent_output.get(key, []) or []:
            lines.append(f"- {label}: {item}")
    recommendation = agent_output.get("recommendation")
    if isinstance(recommendation, Mapping):
        lines.append(f"- Recommendation: {recommendation.get('action', '')} — {recommendation.get('reason', '')}")
    return "\n".join(lines)


def build_validator_prompt(task: Mapping[str, Any], agent_output: Mapping[str, Any]) -> str:
    """The independent review prompt: goal + original request + the output under review.
    Deliberately excludes the specialist's prompt/search/memory context (fresh look)."""
    scope = task.get("scope", {})
    return (
        "You are an independent validation reviewer. Review the output below against the goal "
        "and the original request. Do not redo the task; judge the output.\n"
        f"Goal: {scope.get('primary_objective', '')}\n"
        f"Original request: {task.get('request', {}).get('raw_request', '')}\n"
        "--- OUTPUT UNDER REVIEW ---\n"
        f"{_output_digest(agent_output)}\n"
        "--- END OUTPUT ---\n"
        "Check: objective alignment, evidence for factual claims, logical consistency, "
        "omissions, undisclosed uncertainty, risks, and output completeness.\n"
        "Render your verdict in recommendation.action as exactly one of PASS, REVISE, or BLOCK "
        "(REVISE = fixable deficiencies; BLOCK = unusable or unsupported by evidence). "
        "Put your findings in key_findings, actionable revision requests in next_actions, "
        "and remaining risks in risks."
    )


def _str_list(value: Any) -> list[str]:
    return [x for x in value if isinstance(x, str) and x.strip()] if isinstance(value, list) else []


def _verdict_of(analysis: Mapping[str, Any]) -> tuple[str, str, bool]:
    """Extract (verdict, reason, parseable) from the provider analysis. Fail-closed:
    a missing/unknown verdict is a BLOCK with the unparseable reason recorded."""
    recommendation = analysis.get("recommendation")
    action = recommendation.get("action") if isinstance(recommendation, Mapping) else None
    verdict = action.strip().upper() if isinstance(action, str) else ""
    if verdict in _VERDICTS:
        reason = recommendation.get("reason") if isinstance(recommendation, Mapping) else ""
        return verdict, (reason if isinstance(reason, str) and reason.strip()
                         else f"Independent review verdict: {verdict}."), True
    return "BLOCK", "Independent review did not produce a usable PASS/REVISE/BLOCK verdict (fail-closed).", False


def run_validation_worker(
    task: Mapping[str, Any],
    validator_assignment: Mapping[str, Any],
    agent_output: Mapping[str, Any],
    *,
    provider: Provider,
    created_at: str,
    repo_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one independent validation review and return ``(validation_result, invocation)``.

    The result is a schema-valid ``validation_result.v0.1`` with ``validator_type: ROLE`` and
    ``validation_mode: INDEPENDENT``. Raises ``WorkerBlocked`` on a missing budget, provider
    error/timeout, or token breach; raises ``ValidationError`` if a valid record cannot be
    built. A content-level unusable verdict becomes a BLOCK result, not an exception.
    """
    root = repo_root if repo_root is not None else _repo_root()
    identity = task.get("identity", {})
    ccb = task.get("context", {}).get("core_context_binding_id")
    if not (isinstance(ccb, str) and ccb.startswith("ccb-")):
        raise WorkerBlocked("NOT_BOUND", "task must be bound before validator invocation")
    if validator_assignment.get("role_id") == agent_output.get("role_id"):
        raise WorkerBlocked("NOT_INDEPENDENT", "validator role must differ from the output's creator role")

    limits = validator_assignment.get("execution_budget", {}).get("limits", {})
    max_model_calls = limits.get("max_model_calls", 0)
    token_budget = limits.get("token_budget", 0)
    timeout_seconds = limits.get("max_runtime_seconds", 0)
    if not isinstance(max_model_calls, int) or max_model_calls < 1:
        raise WorkerBlocked("NO_MODEL_BUDGET", "validator assignment grants no model call")

    prompt = build_validator_prompt(task, agent_output)
    try:
        result = provider.generate(prompt, max_output_tokens=int(token_budget), timeout_seconds=int(timeout_seconds))
    except (ProviderError, TimeoutError) as exc:
        # No review happened — fail closed (pipeline BLOCK), same as the specialist worker.
        raise WorkerBlocked("PROVIDER_ERROR", f"validator provider failed: {exc}") from exc

    tokens_used = int(result.input_tokens) + int(result.output_tokens)
    if token_budget and tokens_used > int(token_budget):
        raise WorkerBlocked("TOKEN_BUDGET_EXCEEDED", f"validator used {tokens_used} tokens > budget {token_budget}")

    analysis = result.analysis if isinstance(result.analysis, Mapping) else {}
    verdict, verdict_reason, parseable = _verdict_of(analysis)
    findings_list = _str_list(analysis.get("key_findings"))
    required_revisions = _str_list(analysis.get("next_actions"))
    remaining_risks = _str_list(analysis.get("risks"))

    # Programmatic independence verification: different actor, assignment, and role than
    # the output's creator (the validator did not create what it reviews).
    independence_verified = (
        validator_assignment.get("actor_instance_id") not in (None, agent_output.get("actor_instance_id"))
        and validator_assignment.get("assignment_id") != agent_output.get("assignment_id")
        and validator_assignment.get("role_id") != agent_output.get("role_id")
    )

    ref = f"in_memory:{agent_output.get('agent_output_id')}"
    try:
        integrity.scan_for_secret_bearing_keys(dict(agent_output))
        output_fingerprint = integrity.sha256_record(dict(agent_output))
    except IntegrityError:
        output_fingerprint = integrity.sha256_record(
            {"agent_output_id": agent_output.get("agent_output_id"), "content_withheld": "secret_bearing"}
        )

    checks = [{
        "check_id": "independent_role_review",
        "result": verdict,
        "evidence_refs": [ref, "model:validation"],
        "notes": verdict_reason,
    }]
    if not parseable:
        checks.append({
            "check_id": "verdict_parseable",
            "result": "BLOCK",
            "evidence_refs": ["model:validation"],
            "notes": "Provider response carried no usable verdict; failing closed per the validator role.",
        })

    result_reasons = [verdict_reason]
    result_reasons.extend(required_revisions or [])
    if verdict == "PASS" and len(result_reasons) == 1 and findings_list:
        result_reasons.append("No blocking findings across the independent checks.")

    seed = {
        "agent_output_id": agent_output.get("agent_output_id"),
        "task_id": identity.get("task_id"),
        "task_revision": identity.get("task_revision"),
        "validator_assignment_id": validator_assignment.get("assignment_id"),
    }
    record: dict[str, Any] = {
        "schema_version": VALIDATION_RESULT_SCHEMA_VERSION,
        "validation_result_id": integrity.short_id("valres", seed),
        "trace_id": identity.get("trace_id"),
        "task_id": identity.get("task_id"),
        "task_revision": identity.get("task_revision"),
        "core_context_binding_id": ccb,
        "subject": {
            "subject_type": "AGENT_OUTPUT",
            "subject_id": agent_output.get("agent_output_id"),
            "subject_ref": ref,
            "subject_fingerprint": output_fingerprint,
            "subject_created_by_actor_id": agent_output.get("actor_instance_id"),
        },
        "validator": {
            "validator_type": "ROLE",
            "validator_actor_id": validator_assignment.get("actor_instance_id"),
            "validator_role_id": validator_assignment.get("role_id"),
            "validator_role_version": validator_assignment.get("role_version"),
            "validator_execution_context_id": integrity.short_id("valctx", seed),
            "independent_required": task.get("classification", {}).get("risk_level") in {"ORANGE", "RED"},
            "independence_verified": bool(independence_verified),
        },
        "validation": {
            "validation_mode": "INDEPENDENT",
            "result": verdict,
            "acceptance_criteria": [
                "objective_alignment",
                "claims_supported_by_evidence",
                "logical_consistency",
                "material_omissions_absent",
                "output_contract_complete",
            ],
            "rejection_criteria": [
                "output_contradicts_goal_or_evidence",
                "unusable_or_unsupported_output",
                "unparseable_review_verdict",
            ],
            "checks": checks,
            "result_reasons": result_reasons,
            "recommended_next_state": _NEXT_STATE[verdict],
        },
        "findings": {
            "facts": [f.get("statement") for f in analysis.get("facts", []) or []
                      if isinstance(f, Mapping) and isinstance(f.get("statement"), str) and f.get("statement").strip()],
            "risks": remaining_risks,
            "omissions": _str_list(analysis.get("unresolved_questions")),
            "assumptions": _str_list(analysis.get("assumptions")),
            "limitations": _str_list(analysis.get("limitations")),
        },
        "evidence_refs": [ref, "model:validation"],
        "permission_boundary": validation_result_permission_boundary(),
        "runtime_effect": validation_result_runtime_effect(),
        "lifecycle": {"created_at": created_at, "supersedes": []},
        "audit_refs": [],
    }

    schema_path = root / "schemas" / f"{VALIDATION_RESULT_SCHEMA_VERSION}.schema.json"
    try:
        schema_validation.validate_against_schema(record, schema_path, "independent_validation_result")
    except RuntimeSchemaError as exc:
        raise ValidationError("VALIDATION_RESULT_INVALID", str(exc)) from exc

    invocation = {
        "worker_id": VALIDATOR_WORKER_ID,
        "worker_version": VALIDATOR_WORKER_VERSION,
        "model_id": result.model_id,
        "model_version": result.model_version,
        "prompt_version": VALIDATOR_PROMPT_VERSION,
        "input_tokens": int(result.input_tokens),
        "output_tokens": int(result.output_tokens),
        "tokens_used": tokens_used,
        "latency_ms": int(result.latency_ms),
        "finish_reason": result.finish_reason,
        "network_egress": bool(getattr(provider, "network_egress", False)),
    }
    return record, invocation
