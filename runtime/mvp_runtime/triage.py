"""R7.2 Orchestrator importance triage.

Thomas asked for the importance call to be the orchestrator's, not his: under the "auto"
validation policy, when neither the classification (GREEN risk) nor the operator (no
``!중요`` marker) has already decided the review, Prime spends one deliberately small model
call to judge whether the request is important enough to warrant the independent reviewer.

Governance seams (nothing rides for free):

- The call runs under its **own PermissionDecision** (``INTERNAL_ANALYSIS``, P2 ANALYZE,
  ALLOW — ``permission.build_triage_permission_decision``), built by Prime at plan time
  like the search and validation grants. A model call is a governed action, planner or not.
- The provider is the **same gated chokepoint** as everything else: the pipeline hands in
  the validator's provider (``MVP_VALIDATOR_PROVIDER``, typically the cheap quota) or the
  specialist's; with the mock specialist the deterministic :class:`MockTriageProvider`
  runs — no gate, no network, no new selection surface.
- The call is **budgeted** (``TRIAGE_TOKEN_ALLOWANCE`` on the task allocation, spend in
  ``budget_usage``) and **audited** (its own trail event referencing its decision).

Fail direction — degraded, not blocked, and recorded: the reviewer is an enhancement, so a
triage provider failure or an unparseable verdict must not block the analysis itself and
must not silently double every run's spend either. Both degrade to a NORMAL verdict with
the reason on the record and in the audit trail (``TRIAGE_DEGRADED``). ORANGE/RED risk
never reaches triage at all — governance-mandated review is decided before any model call.

Provider reuse (the R7 precedent): the verdict rides in the shared analysis JSON the
providers already parse — ``recommendation.action`` is exactly HIGH or NORMAL.
"""

from __future__ import annotations

from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from .budgets import TRIAGE_TIMEOUT_SECONDS, TRIAGE_TOKEN_ALLOWANCE
from .errors import ProviderError
from .worker import Provider, ProviderResult

TRIAGE_WORKER_ID = "mvp.orchestrator_triage.llm"
TRIAGE_WORKER_VERSION = "0.1.0"
TRIAGE_PROMPT_VERSION = "mvp_orchestrator_triage.v1"

VERDICT_HIGH = "HIGH"
VERDICT_NORMAL = "NORMAL"
# The model may reasonably answer with any priority word; fold each onto the two verdicts
# that exist. Anything else is not a usable judgement (degraded).
_VERDICT_FOLD = {"HIGH": VERDICT_HIGH, "URGENT": VERDICT_HIGH,
                 "NORMAL": VERDICT_NORMAL, "LOW": VERDICT_NORMAL}


class MockTriageProvider:
    """Deterministic triage provider: no network, no real model. Returns a NORMAL verdict
    in the shared analysis JSON shape — the deterministic default keeps mock runs at one
    (mock) specialist call, exactly like the R7 mock pairing."""

    model_id = "mock.triage"
    model_version = "0.1.0"
    network_egress = False  # deterministic, in-process; no outbound call

    def generate(self, prompt: str, *, max_output_tokens: int, timeout_seconds: int) -> ProviderResult:
        analysis = {
            "summary": "Deterministic mock triage: no importance signal assessed; not a real judgement.",
            "key_findings": [], "facts": [], "inferences": [], "assumptions": [],
            "uncertainty": [], "risks": [],
            "recommendation": {"action": VERDICT_NORMAL,
                               "reason": "Mock triage default: routine analysis, no independent review required."},
            "limitations": ["Mock triage; no real model judgement was exercised."],
            "next_actions": [], "evidence_quality": "not_assessed", "unresolved_questions": [],
        }
        return ProviderResult(
            analysis=analysis, model_id=self.model_id, model_version=self.model_version,
            input_tokens=min(len(prompt) // 4, max_output_tokens), output_tokens=30,
            latency_ms=0, finish_reason="stop",
        )


def build_triage_prompt(task: Mapping[str, Any]) -> str:
    """The importance question: the goal and the raw request, nothing else — the triage
    judges the ask, it never performs it."""
    scope = task.get("scope", {})
    return (
        "You are the orchestrator of an analysis agent, deciding how carefully one request "
        "should be handled. Judge ONLY the importance of the request below — do not answer it.\n"
        f"Goal: {scope.get('primary_objective', '')}\n"
        f"Request: {task.get('request', {}).get('raw_request', '')}\n"
        "Mark it HIGH when a wrong or shallow analysis would be costly to act on: significant "
        "money, contracts or legal exposure, hiring, irreversible or long-term commitments, or "
        "an explicit wish for extra care. Mark it NORMAL for routine, exploratory, or low-stakes "
        "questions.\n"
        "Render your verdict in recommendation.action as exactly HIGH or NORMAL, and a one-line "
        "reason in recommendation.reason."
    )


def _verdict_of(analysis: Mapping[str, Any]) -> tuple[str, str, bool]:
    """Extract ``(verdict, reason, degraded)``. An unusable answer degrades to NORMAL —
    never a crash, never a silent doubling of every run's spend."""
    recommendation = analysis.get("recommendation")
    action = recommendation.get("action") if isinstance(recommendation, Mapping) else None
    verdict = _VERDICT_FOLD.get(action.strip().upper()) if isinstance(action, str) else None
    if verdict is not None:
        reason = recommendation.get("reason") if isinstance(recommendation, Mapping) else ""
        return verdict, (reason if isinstance(reason, str) and reason.strip()
                         else f"Triage verdict: {verdict}."), False
    return VERDICT_NORMAL, "Triage produced no usable HIGH/NORMAL verdict; degraded to NORMAL.", True


def run_triage(
    task: Mapping[str, Any],
    *,
    provider: Provider,
    created_at: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Run the orchestrator importance triage. Returns ``(triage_result, invocation)``.

    ``invocation`` is None when the provider failed before answering — the degraded
    result then records the failure (``TRIAGE_DEGRADED`` + reason) instead of raising:
    a broken triage provider must not block the analysis the triage merely decorates.
    """
    identity = task.get("identity", {})
    prompt = build_triage_prompt(task)

    invocation: dict[str, Any] | None = None
    try:
        result = provider.generate(
            prompt, max_output_tokens=TRIAGE_TOKEN_ALLOWANCE, timeout_seconds=TRIAGE_TIMEOUT_SECONDS
        )
    except (ProviderError, TimeoutError) as exc:
        verdict, reason, degraded = VERDICT_NORMAL, f"Triage provider failed: {exc}", True
    else:
        analysis = result.analysis if isinstance(result.analysis, Mapping) else {}
        verdict, reason, degraded = _verdict_of(analysis)
        invocation = {
            "worker_id": TRIAGE_WORKER_ID,
            "worker_version": TRIAGE_WORKER_VERSION,
            "model_id": result.model_id,
            "model_version": result.model_version,
            "prompt_version": TRIAGE_PROMPT_VERSION,
            "input_tokens": int(result.input_tokens),
            "output_tokens": int(result.output_tokens),
            "tokens_used": int(result.input_tokens) + int(result.output_tokens),
            "latency_ms": int(result.latency_ms),
            "finish_reason": result.finish_reason,
            "network_egress": bool(getattr(provider, "network_egress", False)),
        }

    seed = {
        "task_id": identity.get("task_id"),
        "task_revision": identity.get("task_revision"),
        "kind": "triage",
    }
    triage_result = {
        "triage_id": integrity.short_id("triage", seed),
        "trace_id": identity.get("trace_id"),
        "task_id": identity.get("task_id"),
        "task_revision": identity.get("task_revision"),
        "verdict": verdict,
        "reason": reason,
        "degraded": degraded,
        "prompt_version": TRIAGE_PROMPT_VERSION,
        "created_at": created_at,
    }
    return triage_result, invocation
