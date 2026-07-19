"""R2.4 Read-only Model Invocation — the specialist worker.

``run_analysis_worker`` runs the single specialist model call for a planned, bound,
routed task and maps the result into a schema-valid ``agent_output.v0.2`` with status
``needs_validation`` (R2.5 validates it; the worker never returns a "final" output).

Model invocation is **provider-abstracted**. A ``Provider`` returns a structured
analysis plus invocation metadata (model id/version, token usage, latency). The
``MockProvider`` is deterministic and needs no network or model — it lets the whole
worker→output→(validation)→(audit) pipeline run and be tested *before* the Safety-Flag
Gate. A real hosted provider is added only behind that gate (explicit Thomas approval +
versioned governance update + audit to enable model_invocation/network_access).

The worker enforces the assignment's execution budget (one model call, token cap,
timeout) and fails closed (``WorkerBlocked``) on any provider error, timeout, budget
breach, or an output that violates the Agent Output contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from . import schema_cache
from .errors import ProviderError, WorkerBlocked
from .memory import build_memory_candidates
from .paths import repo_root as _repo_root

WORKER_ID = "mvp.business_analysis.llm"
WORKER_VERSION = "0.1.0"
PROMPT_VERSION = "mvp_business_analysis.v1"
AGENT_OUTPUT_SCHEMA_VERSION = "agent_output.v0.2"

# Business-idea evaluation priorities (Core MVP_RULE_005); the worker asks the model
# to reason in this order and records them for auditability.
EVALUATION_PRIORITIES = (
    "revenue_potential",
    "risk_adjusted_expected_value",
    "scalability",
    "automatability",
    "long_term_growth",
)


@dataclass
class ProviderResult:
    """A provider's structured analysis + invocation metadata.

    ``analysis`` is an internal payload (not a separate governed contract) that the
    worker maps onto agent_output.v0.2. Required keys: ``summary`` (str), ``key_findings``
    (list[str]), ``facts`` (list[{statement, evidence_refs}]), ``inferences`` (list[str]),
    ``risks`` (list[str]), ``recommendation`` ({action, reason} | None), ``evidence_quality``
    (str), ``unresolved_questions`` (list[str]). Optional: assumptions, uncertainty,
    limitations, next_actions.
    """

    analysis: dict[str, Any]
    model_id: str
    model_version: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    finish_reason: str = "stop"


@runtime_checkable
class Provider(Protocol):
    def generate(self, prompt: str, *, max_output_tokens: int, timeout_seconds: int) -> ProviderResult: ...


class MockProvider:
    """Deterministic provider: no network, no real model. Returns a fixed structured
    analysis shaped for the business-idea use case. For tests and pre-gate pipeline runs."""

    model_id = "mock.analysis"
    model_version = "0.1.0"
    network_egress = False  # deterministic, in-process; no outbound call

    def generate(self, prompt: str, *, max_output_tokens: int, timeout_seconds: int) -> ProviderResult:
        analysis = {
            "summary": "Deterministic mock analysis of the supplied business idea across the "
            "standard priorities; not a real model judgement.",
            "key_findings": [
                "revenue_potential: recurring-revenue model with plausible early cash flow",
                "risk_adjusted_expected_value: moderate, dominated by acquisition cost",
                "scalability: constrained by fulfilment/logistics",
                "automatability: ordering and reordering are automatable",
                "long_term_growth: compounding via retention and brand",
            ],
            "facts": [
                {"statement": "The idea targets a recurring-purchase category.", "evidence_refs": ["model:analysis"]},
            ],
            "inferences": [
                "Recurring purchases suggest subscription mechanics improve lifetime value.",
            ],
            "assumptions": ["Demand and unit economics were not independently verified."],
            "uncertainty": ["Customer acquisition cost is unknown."],
            "risks": ["Thin margins if logistics are not optimised."],
            "recommendation": {
                "action": "Run a small validation before committing capital.",
                "reason": "Cash-flow and CAC assumptions dominate the risk-adjusted value.",
            },
            "limitations": ["Read-only analysis; figures are illustrative, not researched."],
            "next_actions": ["Estimate CAC and payback with a small paid test."],
            "evidence_quality": "low_illustrative",
            "unresolved_questions": ["What is the realistic CAC and retention curve?"],
        }
        return ProviderResult(
            analysis=analysis,
            model_id=self.model_id,
            model_version=self.model_version,
            input_tokens=min(len(prompt) // 4, max_output_tokens),
            output_tokens=180,
            latency_ms=0,
            finish_reason="stop",
        )


def _search_context(search_hits: list[Mapping[str, Any]] | None) -> str:
    """A read-only search-results block appended to the prompt. Empty when no search ran."""
    if not search_hits:
        return ""
    lines = ["\nRead-only web search results (use as supporting evidence; cite by [S#]):"]
    for index, hit in enumerate(search_hits, start=1):
        lines.append(f"[S{index}] {hit.get('title', '')} — {hit.get('url', '')}: {hit.get('snippet', '')}")
    return "\n".join(lines) + "\n"


def _memory_context(memory_entries: list[Mapping[str, Any]] | None) -> str:
    """A prior-working-memory block appended to the prompt. Empty when none was retrieved."""
    if not memory_entries:
        return ""
    lines = ["\nRelevant prior working memory (candidates only — unverified, do not over-rely):"]
    for index, entry in enumerate(memory_entries, start=1):
        lines.append(f"[M{index}] ({entry.get('candidate_type', 'memory')}) {entry.get('content', '')}")
    return "\n".join(lines) + "\n"


def build_prompt(
    task: Mapping[str, Any],
    assignment: Mapping[str, Any],
    search_hits: list[Mapping[str, Any]] | None = None,
    memory_entries: list[Mapping[str, Any]] | None = None,
) -> str:
    scope = task.get("scope", {})
    role_scope = assignment.get("role_scope", {})
    rules = ", ".join(task.get("context", {}).get("active_core_rule_ids", []))
    outputs = "; ".join(scope.get("expected_outputs", []))
    priorities = " > ".join(EVALUATION_PRIORITIES)
    return (
        f"Role objective: {role_scope.get('role_objective', '')}\n"
        f"Task: {scope.get('primary_objective', '')}\n"
        f"Request: {task.get('request', {}).get('raw_request', '')}\n"
        f"Expected outputs: {outputs}\n"
        f"Active Core rules in scope: {rules}\n"
        f"Evaluate the business idea in this priority order: {priorities}.\n"
        f"{_memory_context(memory_entries)}"
        f"{_search_context(search_hits)}"
        "Return a structured, read-only analysis. Separate facts (with evidence) from "
        "inferences, disclose assumptions and uncertainty, and do not propose external actions."
    )


def _build_evidence(
    search_hits: list[Mapping[str, Any]] | None,
    memory_entries: list[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Evidence backing the output: the model's own reasoning, each read-only search hit
    (source-attributed), and each prior working-memory candidate the run drew on — so what the
    output leaned on is auditable."""
    evidence: list[dict[str, Any]] = [{"ref": "model:analysis", "type": "model_reasoning"}]
    for index, hit in enumerate(search_hits or [], start=1):
        url = hit.get("url")
        if not isinstance(url, str) or not url:
            continue
        evidence.append({
            "ref": f"search:{hit.get('source', 'search')}:{index}",
            "type": "web_search",
            "url": url,
            "title": hit.get("title", ""),
        })
    for entry in memory_entries or []:
        candidate_id = entry.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            continue
        evidence.append({
            "ref": f"working_memory:{candidate_id}",
            "type": "working_memory",
            "candidate_type": entry.get("candidate_type", ""),
        })
    return evidence


_REQUIRED_ANALYSIS_KEYS = ("summary", "key_findings", "facts", "inferences", "risks", "recommendation",
                           "evidence_quality", "unresolved_questions")


def _require_analysis(analysis: Any) -> dict[str, Any]:
    if not isinstance(analysis, Mapping):
        raise WorkerBlocked("MALFORMED_ANALYSIS", "provider analysis must be a mapping")
    missing = [k for k in _REQUIRED_ANALYSIS_KEYS if k not in analysis]
    if missing:
        raise WorkerBlocked("MALFORMED_ANALYSIS", f"provider analysis missing keys: {missing}")
    summary = analysis.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise WorkerBlocked("MALFORMED_ANALYSIS", "analysis.summary must be a non-empty string")
    facts = analysis.get("facts")
    if not isinstance(facts, list):
        raise WorkerBlocked("MALFORMED_ANALYSIS", "analysis.facts must be a list")
    return dict(analysis)


def _str_list(value: Any) -> list[str]:
    return [x for x in value if isinstance(x, str) and x.strip()] if isinstance(value, list) else []


def _normalize_facts(value: Any) -> list[dict[str, Any]]:
    """Coerce model-provided facts to the strict {statement, evidence_refs>=1} shape,
    dropping unexpected keys and defaulting missing evidence to the model reference. Real
    models do not perfectly follow the schema; malformed facts are normalized, not trusted."""
    out: list[dict[str, Any]] = []
    for fact in value if isinstance(value, list) else []:
        if not isinstance(fact, dict):
            continue
        statement = fact.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            continue
        refs = _str_list(fact.get("evidence_refs")) or ["model:analysis"]
        out.append({"statement": statement, "evidence_refs": refs})
    return out


def _normalize_inferences(value: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in value if isinstance(value, list) else []:
        statement = item if isinstance(item, str) else (item.get("statement") if isinstance(item, dict) else None)
        if isinstance(statement, str) and statement.strip():
            out.append({"statement": statement})
    return out


def _normalize_recommendation(value: Any) -> dict[str, str] | None:
    if isinstance(value, dict):
        action, reason = value.get("action"), value.get("reason")
        if isinstance(action, str) and action.strip() and isinstance(reason, str) and reason.strip():
            return {"action": action, "reason": reason}
    return None


def run_analysis_worker(
    task: Mapping[str, Any],
    assignment: Mapping[str, Any],
    *,
    provider: Provider,
    created_at: str,
    search_hits: list[Mapping[str, Any]] | None = None,
    memory_entries: list[Mapping[str, Any]] | None = None,
    repo_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one specialist model call and return ``(agent_output, invocation_metadata)``.

    ``search_hits`` (read-only web results) and ``memory_entries`` (prior working-memory
    candidates) are context the specialist may use: both are added to the prompt and recorded
    as evidence on the output (``web_search`` / ``working_memory`` types).

    Fails closed (``WorkerBlocked``) on missing model budget, provider error/timeout,
    token-budget breach, malformed analysis, or a schema-invalid Agent Output.
    """
    root = repo_root if repo_root is not None else _repo_root()
    identity = task.get("identity", {})
    context = task.get("context", {})
    ccb = context.get("core_context_binding_id")
    if not (isinstance(ccb, str) and ccb.startswith("ccb-")):
        raise WorkerBlocked("NOT_BOUND", "task must be bound before worker invocation")

    limits = assignment.get("execution_budget", {}).get("limits", {})
    max_model_calls = limits.get("max_model_calls", 0)
    token_budget = limits.get("token_budget", 0)
    timeout_seconds = limits.get("max_runtime_seconds", 0)
    if not isinstance(max_model_calls, int) or max_model_calls < 1:
        raise WorkerBlocked("NO_MODEL_BUDGET", "assignment grants no model call")

    prompt = build_prompt(task, assignment, search_hits, memory_entries)
    try:
        result = provider.generate(prompt, max_output_tokens=int(token_budget), timeout_seconds=int(timeout_seconds))
    except (ProviderError, TimeoutError) as exc:
        raise WorkerBlocked("PROVIDER_ERROR", str(exc)) from exc

    tokens_used = int(result.input_tokens) + int(result.output_tokens)
    if token_budget and tokens_used > int(token_budget):
        raise WorkerBlocked("TOKEN_BUDGET_EXCEEDED", f"used {tokens_used} tokens > budget {token_budget}")

    analysis = _require_analysis(result.analysis)

    seed = {
        "task_id": identity.get("task_id"),
        "task_revision": identity.get("task_revision"),
        "assignment_id": assignment.get("assignment_id"),
        "worker_id": WORKER_ID,
        "worker_version": WORKER_VERSION,
        "model_id": result.model_id,
        "prompt_version": PROMPT_VERSION,
    }
    # R5: propose working-memory candidates from the analysis, honoring the assignment's
    # memory scope (creation gate + allowed types). Proposals only — never promoted.
    memory_candidates = build_memory_candidates(
        analysis, assignment, now=created_at,
        seed={"task_id": identity.get("task_id"), "task_revision": identity.get("task_revision"),
              "assignment_id": assignment.get("assignment_id")},
        # R5.4: stamp each candidate with the originating task's identity so an explicit,
        # off-run-path promotion can be audited against the real task that produced it.
        origin={"task_id": identity.get("task_id"), "task_revision": identity.get("task_revision"),
                "trace_id": identity.get("trace_id"), "core_context_binding_id": ccb,
                "data_sensitivity": context.get("data_sensitivity")},
    )
    agent_output = {
        "schema_version": AGENT_OUTPUT_SCHEMA_VERSION,
        "agent_output_id": integrity.short_id("agentout", seed),
        "trace_id": identity.get("trace_id"),
        "task_id": identity.get("task_id"),
        "core_context_binding_id": ccb,
        "assignment_id": assignment.get("assignment_id"),
        "actor_instance_id": assignment.get("actor_instance_id"),
        "role_id": assignment.get("role_id"),
        "role_version": assignment.get("role_version"),
        "status": "needs_validation",
        "goal": task.get("scope", {}).get("primary_objective") or task.get("request", {}).get("normalized_goal", ""),
        "summary": analysis["summary"],
        "facts": _normalize_facts(analysis.get("facts")),
        "evidence": _build_evidence(search_hits, memory_entries),
        "inferences": _normalize_inferences(analysis.get("inferences")),
        "assumptions": _str_list(analysis.get("assumptions")),
        "uncertainty": _str_list(analysis.get("uncertainty")),
        "risks": _str_list(analysis.get("risks")),
        "recommendation": _normalize_recommendation(analysis.get("recommendation")),
        "limitations": [*_str_list(analysis.get("limitations")), "Read-only analysis; not independently validated."],
        "validation_recommended": True,
        "permission_request_refs": [],
        "next_actions": _str_list(analysis.get("next_actions")),
        "memory_candidates": memory_candidates,
        "escalation_required": False,
        "role_specific_output": {
            "key_findings": _str_list(analysis.get("key_findings")),
            "evidence_quality": analysis["evidence_quality"] if isinstance(analysis.get("evidence_quality"), str) else "",
            "unresolved_questions": _str_list(analysis.get("unresolved_questions")),
        },
        "created_at": created_at,
    }

    schema_path = root / "schemas" / f"{AGENT_OUTPUT_SCHEMA_VERSION}.schema.json"
    try:
        schema_cache.validate_against_schema(agent_output, schema_path, "agent_output")
    except RuntimeSchemaError as exc:
        raise WorkerBlocked("OUTPUT_SCHEMA_INVALID", str(exc)) from exc

    invocation_metadata = {
        "worker_id": WORKER_ID,
        "worker_version": WORKER_VERSION,
        "model_id": result.model_id,
        "model_version": result.model_version,
        "prompt_version": PROMPT_VERSION,
        "input_tokens": int(result.input_tokens),
        "output_tokens": int(result.output_tokens),
        "tokens_used": tokens_used,
        "latency_ms": int(result.latency_ms),
        "finish_reason": result.finish_reason,
        # Whether this invocation crossed the network boundary (audited downstream).
        "network_egress": bool(getattr(provider, "network_egress", False)),
    }
    return agent_output, invocation_metadata
