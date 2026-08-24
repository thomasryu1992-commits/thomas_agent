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
from typing import Any, Mapping, Protocol, Sequence

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from . import schema_cache
from .budgets import (
    MAX_MEMORY_CONTENT_CHARS,
    MAX_PERSPECTIVE_BASIS_CHARS,
    MAX_SEARCH_SNIPPET_CHARS,
    clip_for_prompt,
    output_allowance,
    response_was_truncated,
)
from .errors import ProviderError, WorkerBlocked
from .memory import build_memory_candidates
from .paths import repo_root as _repo_root

WORKER_ID = "mvp.business_analysis.llm"
WORKER_VERSION = "0.1.0"
# v2 adds the explicit acceptance criteria (see ACCEPTANCE_CRITERIA below); v3 adds the
# §10.4 perspective separation. The prompt version is recorded on every invocation, so a
# changed prompt gets a new version — the ledger must not claim two different prompts were
# the same one.
PROMPT_VERSION = "mvp_business_analysis.v3"
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

# The three REVISE conditions in ``validation.validate_agent_output`` (required_sections,
# evidence_grounding, calibration), stated to the model as acceptance criteria rather than
# left implicit. They are the checks that withhold delivery, and every one of them is a
# compliance requirement — a present-but-empty field — not a question of analytical skill.
# A model that is never told the bar cannot aim at it, and the run pays for the analysis
# either way, so the cheapest place to raise the pass rate is the prompt.
#
# Deliberately scoped to the specialist prompt, NOT to the shared ``_RESPONSE_INSTRUCTION``
# in providers.py: the independent validator and the orchestrator triage speak the same
# analysis JSON but legitimately return empty facts/key_findings (they judge, they do not
# analyze), so a shared criterion would ask them to invent content.

# Organization Architecture §10.4 — the complex-strategy pattern: judge from separate
# perspectives, then integrate. §10.4 permits the cheap form for early MVP ("one Agent may
# separate these perspectives internally") and §13's separation criteria for making them
# three Agents are not met, so this is prompt-level separation with a declared output shape.
#
# The failure it exists to prevent is specific: a single blended narrative in which a weak
# revenue case is smoothed over by an enthusiastic research case, and the reader cannot see
# that it happened. Forcing a per-perspective verdict makes the disagreement visible, and
# makes the integration checkable — `validation` refuses an analysis that skips a perspective
# and one that reports a NEGATIVE perspective while stating no risk at all.
#
# The three are §10.4's own list. Revenue and risk-adjusted value are also the top two of
# EVALUATION_PRIORITIES (Core MVP_RULE_005) — that constant fixes the order to weigh them in,
# this one fixes that each is answered on its own before they are weighed at all.
PERSPECTIVES = ("research", "revenue", "risk")
PERSPECTIVE_VERDICTS = ("POSITIVE", "MIXED", "NEGATIVE")

PERSPECTIVE_INSTRUCTION = (
    "Before the integrated answer, judge the idea from each of these perspectives "
    "separately and report them in `perspectives` as objects "
    "{perspective, verdict, basis}: "
    f"{', '.join(PERSPECTIVES)}. `verdict` is exactly one of "
    f"{', '.join(PERSPECTIVE_VERDICTS)}; `basis` states what that perspective is judging on. "
    "Reach each verdict on that perspective's own merits - do not let a strong perspective "
    "soften a weak one. Your summary and recommendation must then be consistent with them: "
    "if any perspective is NEGATIVE, the risk must appear in `risks` rather than being "
    "absorbed into an optimistic summary."
)

ACCEPTANCE_CRITERIA = (
    "Acceptance criteria - an answer failing any of these is rejected and never reaches "
    "the reader, so satisfy all three: (1) at least one entry in key_findings; (2) at "
    "least one entry in facts, each carrying a non-empty evidence_refs; (3) at least one "
    "entry in uncertainty or assumptions - an analysis disclosing neither reads as "
    "over-confident. If the request is too thin to support a finding, say exactly that in "
    "these fields; leaving them empty is the one answer that cannot be delivered."
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
    # False when the provider returned no usage metadata at all. Token accounting is
    # post-hoc and provider-self-reported, so absent usage silently reads as "0 tokens"
    # and every budget check passes trivially — the record must say which it was rather
    # than let an unmetered call look like a free one.
    usage_reported: bool = True
    # How many transient-status retries (503 overloaded / 429 throttled) this result took.
    # The budget contract allows max_retry_count: 1 and its usage must be recorded — a
    # retried call that reads as first-try would hide provider instability from the ledger.
    retries: int = 0


class Provider(Protocol):
    def generate(self, prompt: str, *, max_output_tokens: int, timeout_seconds: int) -> ProviderResult: ...


class MockTrialProvider:
    """Deterministic ROLE-SHAPED provider: no network, no real model. Returns the common
    analysis shape plus a synthesized non-empty value for each of a Role's declared output
    keys, so a Role whose contract is not the business analyst's runs end-to-end without a
    hosted provider.

    Originally the candidate-trial provider; §8.5 gave the normal routing path the same need
    (a translation run must answer ``translated_text``, not ``key_findings``), so it lives here
    beside :class:`MockProvider` rather than being copied. The wording still says "trial" in the
    generated content because that content is what the trial reports assert on; the class is not
    trial-specific."""

    model_id = "mock.trial"
    model_version = "0.1.0"
    network_egress = False  # deterministic, in-process; no outbound call
    # Holds a model_id for record-keeping but reaches no model. Declared explicitly
    # because gate_banners announces anything carrying a model_id: silence is the thing
    # that must be opted into, so a real capability can never go unannounced. Added on
    # main (#260-era) while this branch was moving the class here — carried across the
    # move rather than lost to it.
    model_invocation = False

    def __init__(self, role_output_spec: Mapping[str, str]):
        self._role_output_spec = dict(role_output_spec)

    def generate(self, prompt: str, *, max_output_tokens: int, timeout_seconds: int) -> ProviderResult:
        analysis: dict[str, Any] = {
            "summary": "Deterministic mock trial output for the assigned candidate role; "
            "not a real model judgement.",
            "key_findings": ["trial task addressed within the isolated, read-only trial scope"],
            "facts": [
                {"statement": "The trial ran with no tools, no memory, and no external action.",
                 "evidence_refs": ["model:analysis"]},
            ],
            "inferences": ["The candidate role's output contract can be exercised end-to-end."],
            "assumptions": ["The trial request text fully describes the trial task."],
            "uncertainty": ["Mock output; the role's real quality was not exercised."],
            "risks": [],
            "recommendation": {"action": "Review the trial report before any promotion decision.",
                               "reason": "A trial run is evidence, never an activation."},
            "limitations": ["Deterministic mock trial; no real model judgement."],
            "next_actions": [],
            "evidence_quality": "mock_trial",
            "unresolved_questions": [],
        }
        for key, kind in self._role_output_spec.items():
            analysis[key] = (
                f"Mock {key} content for the candidate-role trial."
                if kind == "string" else [f"mock {key} entry"]
            )
        return ProviderResult(
            analysis=analysis, model_id=self.model_id, model_version=self.model_version,
            input_tokens=min(len(prompt) // 4, max_output_tokens), output_tokens=150,
            latency_ms=0, finish_reason="stop",
        )


class MockProvider:
    """Deterministic provider: no network, no real model. Returns a fixed structured
    analysis shaped for the business-idea use case. For tests and pre-gate pipeline runs."""

    model_id = "mock.analysis"
    model_version = "0.1.0"
    network_egress = False  # deterministic, in-process; no outbound call
    # Holds a model_id for record-keeping but reaches no model. Declared explicitly
    # because gate_banners now announces anything carrying a model_id: silence is the
    # thing that must be opted into, so a real capability can never go unannounced.
    model_invocation = False

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
            # §10.4. MIXED throughout on purpose: the mock must not look like a model that
            # reached a confident verdict, and a mock that answered POSITIVE everywhere would
            # make the NEGATIVE-without-stated-risk branch untested by the pipeline runs.
            "perspectives": [
                {"perspective": "research", "verdict": "MIXED",
                 "basis": "Category demand is plausible but unverified in this run."},
                {"perspective": "revenue", "verdict": "MIXED",
                 "basis": "Recurring mechanics help lifetime value; CAC is unknown."},
                {"perspective": "risk", "verdict": "MIXED",
                 "basis": "Margins depend on logistics that were not analysed."},
            ],
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
        # The snippet is a web page's content — sized by the page, not by us.
        snippet = clip_for_prompt(hit.get("snippet", ""), MAX_SEARCH_SNIPPET_CHARS)
        lines.append(f"[S{index}] {hit.get('title', '')} — {hit.get('url', '')}: {snippet}")
    return "\n".join(lines) + "\n"


def _keyword_context(keyword_rows: list[Mapping[str, Any]] | None) -> str:
    """Measured Naver keyword demand appended to the prompt. Empty when no brief ran.

    Numbers only, no advice: the rows are evidence the specialist cites ([K#]), and the
    block states their provenance so the model treats them as measurements rather than
    suggestions. ``competing_posts`` renders only where the lookup landed — an absent
    column stays absent, because "0 competing posts" is a claim this block must not invent.
    """
    if not keyword_rows:
        return ""
    # Mock rows (env gate closed -> deterministic fixtures) must announce themselves HERE,
    # where the model reads, not only in the ledger record. Before this label the block's
    # header asserted "Measured ... demand" over fabricated volumes, and the model had no
    # way to know the difference — the one surviving sliver of an external review's
    # mock/degraded claim (2026-08-10). Mock and live never mix within one brief (one tool
    # serves all rows), so the label is per-block, not per-row.
    mock = any(str(row.get("source", "")).startswith("mock.") for row in keyword_rows)
    if mock:
        lines = ["\nMOCK keyword fixtures (research gate closed — deterministic test data, "
                 "NOT measured demand; never present these numbers as real; cite by [K#]):"]
    else:
        lines = ["\nMeasured Naver keyword demand (monthly search counts; cite by [K#]):"]
    for index, row in enumerate(keyword_rows, start=1):
        competing = (
            f", competing blog posts {row['competing_posts']:,}"
            if "competing_posts" in row else ""
        )
        low = " (low-volume estimate)" if row.get("low_volume") else ""
        lines.append(
            f"[K{index}] {row.get('keyword', '')} — total {row.get('monthly_total', 0):,}/mo "
            f"(PC {row.get('monthly_pc', 0):,} / mobile {row.get('monthly_mobile', 0):,}), "
            f"ad competition {row.get('competition', 'unknown')}{competing}{low}"
        )
    return "\n".join(lines) + "\n"


def _memory_context(memory_entries: list[Mapping[str, Any]] | None) -> str:
    """A prior-working-memory block appended to the prompt. Empty when none was retrieved."""
    if not memory_entries:
        return ""
    lines = ["\nRelevant prior working memory (candidates only — unverified, do not over-rely):"]
    for index, entry in enumerate(memory_entries, start=1):
        content = clip_for_prompt(entry.get("content", ""), MAX_MEMORY_CONTENT_CHARS)
        lines.append(f"[M{index}] ({entry.get('candidate_type', 'memory')}) {content}")
    return "\n".join(lines) + "\n"


def _validated_context(validated_entries: list[Mapping[str, Any]] | None) -> str:
    """An operator-validated-memory block appended to the prompt. Empty when none exists.

    Framed differently from the candidate block: VALIDATED entries were explicitly
    promoted by the operator, so the model may rely on them (cite by [V#]).

    M5c: a validated entry carrying a ``learning_source`` is a promoted correction — the
    operator's "for a request like this, do it this way." It is framed distinctly so the
    specialist *applies* it (starts closer to the accepted answer) rather than treating it as
    one more reference fact; a closing instruction tells the model to prefer such guidance."""
    if not validated_entries:
        return ""
    lines = ["\nValidated memory (operator-approved reusable knowledge; cite by [V#]):"]
    has_correction = False
    for index, entry in enumerate(validated_entries, start=1):
        content = clip_for_prompt(entry.get("content", ""), MAX_MEMORY_CONTENT_CHARS)
        if entry.get("learning_source"):
            has_correction = True
            lines.append(
                f"[V{index}] (operator-approved correction — apply to this similar request) "
                f"{content}"
            )
        else:
            lines.append(f"[V{index}] ({entry.get('candidate_type', 'memory')}) {content}")
    if has_correction:
        lines.append("Where a [V#] is marked a correction, prefer its guidance over your default approach.")
    return "\n".join(lines) + "\n"


def _revision_context(revision_requests: list[str] | None) -> str:
    """The M3 revision block: the required fixes a prior version failed on, fed back into
    this one regeneration. Empty on a first attempt. This is guidance to address, not new
    facts to invent — a missing input is still a limitation to disclose, never fabricate."""
    if not revision_requests:
        return ""
    lines = ["\nA prior version of this analysis did not pass validation. Produce a revised "
             "version that addresses EACH required revision below; where a revision asks for "
             "data you do not have, disclose it as a limitation rather than inventing it:"]
    for index, req in enumerate(revision_requests, start=1):
        lines.append(f"[R{index}] {req}")
    return "\n".join(lines) + "\n"


def build_role_prompt(
    task: Mapping[str, Any],
    assignment: Mapping[str, Any],
    definition: Mapping[str, Any],
    output_spec: Mapping[str, str],
    search_hits: list[Mapping[str, Any]] | None = None,
    memory_entries: list[Mapping[str, Any]] | None = None,
    validated_entries: list[Mapping[str, Any]] | None = None,
    revision_requests: list[str] | None = None,
    keyword_rows: list[Mapping[str, Any]] | None = None,
) -> str:
    """The prompt for a routable Role that is NOT the business analyst (§8.5).

    :func:`build_prompt` is the business-analysis prompt: it names EVALUATION_PRIORITIES,
    the §10.4 perspectives and acceptance criteria that only make sense for judging an idea.
    Sending it to a translator would ask for revenue potential on a paragraph of text — so a
    Role that is not ``general.specialist`` gets its own purpose and its own declared output
    contract instead, read from the Role Definition rather than restated here.

    Distinct from ``trial.build_trial_prompt``, which additionally tells the model it has no
    tools, no web access and no memory — true in an isolated trial and false here. A normal
    routed run gets the same context blocks every specialist run gets, so the two prompts
    must not be merged just because they look alike.
    """
    role_scope = assignment.get("role_scope", {})
    scope = task.get("scope", {})
    keys_desc = ", ".join(f"{key} ({kind})" for key, kind in output_spec.items())
    capabilities = ", ".join(role_scope.get("assigned_capabilities", []))
    quality = ", ".join(str(item) for item in definition.get("quality_criteria", []) if item)
    quality_line = f"Quality criteria this role must satisfy: {quality}.\n" if quality else ""
    rules = ", ".join(task.get("context", {}).get("active_core_rule_ids", []))
    return (
        f"Role: {definition.get('role_id')}@{definition.get('role_version')}\n"
        f"Role purpose: {definition.get('purpose', '')}\n"
        f"Assigned capabilities: {capabilities}\n"
        f"{quality_line}"
        f"Role objective: {role_scope.get('role_objective', '')}\n"
        f"Task: {scope.get('primary_objective', '')}\n"
        f"Request: {task.get('request', {}).get('raw_request', '')}\n"
        f"Active Core rules in scope: {rules}\n"
        f"{_validated_context(validated_entries)}"
        f"{_memory_context(memory_entries)}"
        f"{_search_context(search_hits)}"
        f"{_keyword_context(keyword_rows)}"
        f"{_revision_context(revision_requests)}"
        f"In the SAME JSON object, additionally include these role-specific keys: {keys_desc}.\n"
        "Separate facts (with evidence) from inferences, disclose assumptions and uncertainty, "
        "and do not propose external actions.\n"
    )


def build_prompt(
    task: Mapping[str, Any],
    assignment: Mapping[str, Any],
    search_hits: list[Mapping[str, Any]] | None = None,
    memory_entries: list[Mapping[str, Any]] | None = None,
    validated_entries: list[Mapping[str, Any]] | None = None,
    revision_requests: list[str] | None = None,
    keyword_rows: list[Mapping[str, Any]] | None = None,
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
        f"{PERSPECTIVE_INSTRUCTION}\n"
        f"{_validated_context(validated_entries)}"
        f"{_memory_context(memory_entries)}"
        f"{_search_context(search_hits)}"
        f"{_keyword_context(keyword_rows)}"
        f"{_revision_context(revision_requests)}"
        "Return a structured, read-only analysis. Separate facts (with evidence) from "
        "inferences, disclose assumptions and uncertainty, and do not propose external actions.\n"
        f"{ACCEPTANCE_CRITERIA}"
    )


def _build_evidence(
    search_hits: list[Mapping[str, Any]] | None,
    memory_entries: list[Mapping[str, Any]] | None = None,
    validated_entries: list[Mapping[str, Any]] | None = None,
    keyword_rows: list[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Evidence backing the output: the model's own reasoning, each read-only search hit
    (source-attributed), each prior working-memory candidate, each VALIDATED memory
    entry, and each measured keyword-demand row the run drew on — so what the output
    leaned on is auditable."""
    evidence: list[dict[str, Any]] = [{"ref": "model:analysis", "type": "model_reasoning"}]
    for entry in validated_entries or []:
        validated_id = entry.get("validated_memory_id")
        if not isinstance(validated_id, str) or not validated_id:
            continue
        evidence.append({
            "ref": f"validated_memory:{validated_id}",
            "type": "validated_memory",
            "candidate_type": entry.get("candidate_type", ""),
        })
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
    for index, row in enumerate(keyword_rows or [], start=1):
        keyword = row.get("keyword")
        if not isinstance(keyword, str) or not keyword:
            continue
        evidence.append({
            "ref": f"keyword:{row.get('source', 'naver')}:{index}",
            "type": "keyword_demand",
            "keyword": keyword,
            "monthly_total": row.get("monthly_total", 0),
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


def _perspectives(value: Any) -> list[dict[str, str]]:
    """The §10.4 perspective block, normalized. Never repaired.

    Keeps only well-formed entries — a known perspective, a known verdict, a non-empty basis —
    and drops anything else rather than coercing it. A malformed or missing entry must reach
    validation as *absent*, because "the model did not answer this perspective" and "the model
    answered it badly" both mean the separation did not happen; normalizing a broken entry into
    a plausible-looking one would hide exactly the case the check exists to catch. First entry
    wins per perspective, so a repeated perspective cannot overwrite its own earlier verdict."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        name = item.get("perspective")
        verdict = item.get("verdict")
        basis = item.get("basis")
        if name not in PERSPECTIVES or name in seen:
            continue
        if verdict not in PERSPECTIVE_VERDICTS:
            continue
        if not (isinstance(basis, str) and basis.strip()):
            continue
        seen.add(name)
        out.append({"perspective": name, "verdict": verdict,
                    "basis": clip_for_prompt(basis.strip(), MAX_PERSPECTIVE_BASIS_CHARS)})
    # Deterministic order regardless of what the model emitted, so two runs with the same
    # verdicts produce the same record.
    return sorted(out, key=lambda entry: PERSPECTIVES.index(entry["perspective"]))


def _role_specific_output(analysis: Mapping[str, Any], role_output_keys: Sequence[str] | None) -> dict[str, Any]:
    """The role's own output block. Default (None) keeps the business-analysis shape
    byte-identical; a trial passes the candidate role's declared output-contract keys, whose
    values come from the provider analysis as-is (missing key -> None; presence is judged by
    validation, not silently patched here). ``key_findings`` is always included — the
    independent validator's review digest reads it for every role."""
    if role_output_keys is None:
        return {
            "key_findings": _str_list(analysis.get("key_findings")),
            "evidence_quality": analysis["evidence_quality"] if isinstance(analysis.get("evidence_quality"), str) else "",
            "unresolved_questions": _str_list(analysis.get("unresolved_questions")),
            "perspectives": _perspectives(analysis.get("perspectives")),
        }
    output: dict[str, Any] = {"key_findings": _str_list(analysis.get("key_findings"))}
    for key in role_output_keys:
        output[key] = analysis.get(key)
    return output


def run_analysis_worker(
    task: Mapping[str, Any],
    assignment: Mapping[str, Any],
    *,
    provider: Provider,
    created_at: str,
    search_hits: list[Mapping[str, Any]] | None = None,
    memory_entries: list[Mapping[str, Any]] | None = None,
    validated_entries: list[Mapping[str, Any]] | None = None,
    keyword_rows: list[Mapping[str, Any]] | None = None,
    repo_root: Path | None = None,
    prompt_override: str | None = None,
    revision_requests: list[str] | None = None,
    role_output_keys: Sequence[str] | None = None,
    worker_id: str = WORKER_ID,
    prompt_version: str = PROMPT_VERSION,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one specialist model call and return ``(agent_output, invocation_metadata)``.

    ``search_hits`` (read-only web results), ``memory_entries`` (prior working-memory
    candidates), and ``validated_entries`` (operator-promoted VALIDATED memory) are context
    the specialist may use: all are added to the prompt and recorded as evidence on the
    output (``web_search`` / ``working_memory`` / ``validated_memory`` types).

    ``prompt_override`` / ``role_output_keys`` / ``worker_id`` / ``prompt_version`` let a
    non-analysis role run through the same worker (the candidate-role trial): the prompt is
    supplied by the caller, and ``role_specific_output`` carries the role's own declared
    output fields (taken from the provider analysis by key) alongside ``key_findings``.
    Defaults keep the business-analysis behavior byte-identical.

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

    prompt = prompt_override if prompt_override is not None else build_prompt(
        task, assignment, search_hits, memory_entries, validated_entries, revision_requests,
        keyword_rows,
    )
    try:
        result = provider.generate(
            prompt,
            # NOT the whole token_budget: that figure caps input+output and is checked
            # below, so handing all of it to the provider as an output allowance made an
            # obedient answer breach by the size of the prompt (see budgets.py).
            max_output_tokens=output_allowance(token_budget),
            timeout_seconds=int(timeout_seconds),
        )
    except (ProviderError, TimeoutError) as exc:
        raise WorkerBlocked("PROVIDER_ERROR", str(exc)) from exc

    if response_was_truncated(result.finish_reason):
        # Say what went wrong. A response cut off at the cap reaches _require_analysis as
        # a malformed one and used to be reported as malformed — naming the symptom and
        # sending the operator looking for a bad model instead of a small allowance.
        raise WorkerBlocked(
            "RESPONSE_TRUNCATED",
            f"provider stopped at the output cap ({result.finish_reason}); the answer is "
            "incomplete, not malformed",
        )

    tokens_used = int(result.input_tokens) + int(result.output_tokens)
    if token_budget and tokens_used > int(token_budget):
        raise WorkerBlocked("TOKEN_BUDGET_EXCEEDED", f"used {tokens_used} tokens > budget {token_budget}")

    analysis = _require_analysis(result.analysis)

    seed = {
        "task_id": identity.get("task_id"),
        "task_revision": identity.get("task_revision"),
        "assignment_id": assignment.get("assignment_id"),
        "worker_id": worker_id,
        "worker_version": WORKER_VERSION,
        "model_id": result.model_id,
        "prompt_version": prompt_version,
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
        "evidence": _build_evidence(search_hits, memory_entries, validated_entries, keyword_rows),
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
        "role_specific_output": _role_specific_output(analysis, role_output_keys),
        "created_at": created_at,
    }

    schema_path = root / "schemas" / f"{AGENT_OUTPUT_SCHEMA_VERSION}.schema.json"
    try:
        schema_cache.validate_against_schema(agent_output, schema_path, "agent_output")
    except RuntimeSchemaError as exc:
        raise WorkerBlocked("OUTPUT_SCHEMA_INVALID", str(exc)) from exc

    invocation_metadata = {
        "worker_id": worker_id,
        "worker_version": WORKER_VERSION,
        "model_id": result.model_id,
        "model_version": result.model_version,
        "prompt_version": prompt_version,
        "input_tokens": int(result.input_tokens),
        "output_tokens": int(result.output_tokens),
        "tokens_used": tokens_used,
        # False => the provider reported no usage; tokens_used is a floor of 0, not a
        # measurement, and the budget check below it passed vacuously.
        "usage_reported": bool(result.usage_reported),
        # Transient-status retries (503/429) this call took; the budget usage records it.
        "retry_count": int(result.retries),
        "latency_ms": int(result.latency_ms),
        "finish_reason": result.finish_reason,
        # Whether this invocation crossed the network boundary (audited downstream).
        "network_egress": bool(getattr(provider, "network_egress", False)),
        # ...and whether it reached a model at all. `select_provider` fails closed to the
        # deterministic mock when `MVP_HOSTED_PROVIDER` is unset, which is correct — but the
        # run that follows still produces `key_findings`, still proposes working-memory
        # candidates, and reads downstream exactly like an analysis. It is not one: the mock
        # answers every prompt with the same fixture. 190 of the 316 rows in this host's
        # candidate store are five canned strings, 38 copies each, all from July, all
        # byte-identical to `MockProvider`'s output — written while the env var was unset and
        # nothing in the record said the runs had reached no model.
        #
        # The env var IS the gate (Thomas 2026-08-10), so losing it is a silent downgrade in
        # the same shape `MVP_BRIDGE_CLIENT_UID` had before it got a default. This does not
        # re-close that gate; it makes the downgrade legible per run. The rule is the one
        # `gate_banners` announces on (`cli_common`), so one fact answers both.
        "model_invocation": bool(getattr(
            provider, "model_invocation", getattr(provider, "model_id", None) is not None
        )),
    }
    return agent_output, invocation_metadata
