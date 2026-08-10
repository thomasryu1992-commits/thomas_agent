"""R2.7 Single-Agent End-to-End pipeline.

``run_task`` wires the whole MVP together for one user request:

    intake -> plan (classify/bind/permission/assign) -> worker (model) ->
    output validation -> audit trail -> final response

It returns a uniform structured result. A normal task COMPLETES and delivers a
rendered response; any fail-closed condition (invalid input, planning/binding/permission
failure, provider error/timeout, budget breach, invalid output) yields a BLOCKED result
with a reason instead of raising. A validation result of REVISE/BLOCK also withholds
delivery with the validator's reasons.

Every terminal outcome is audited, not just success: a run that fails after binding
produces a TASK_CREATED -> TASK_STATE_CHANGED(BLOCKED) trail; a run that fails before a
Core binding exists (which cannot be an ``audit_event.v0.1``) produces a durable block
ledger entry. When a ``store`` is supplied, all produced records and the hash-chained
audit trail are persisted append-only, chained onto the previous run's last event; a
completed run whose evidence cannot be persisted is not delivered (no durable audit =>
no trust). The run performs no external write, no network (with the default
MockProvider), and no tool/program execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from runtime.read_only_kernel import integrity

from . import timeutil
from .budgets import recorded_usage_budget
from .events import stamped_event
from .audit import build_blocked_audit, build_pipeline_audit
from .errors import (
    MvpRuntimeError, PersistenceError, ProgramizationBlocked, ToolBlocked, WorkerBlocked,
)
from .intake import build_task
from .memory import (
    LEARNING_SOURCE_REVISION,
    build_correction_candidate,
    build_learning_event,
    candidate_type_for,
    retrieve_validated_memory,
    retrieve_working_memory,
)
from .prime import plan_task
from .programization import ProgramizationStore, observe_completed_run
from .store import LedgerStore
from . import naver_research
from .tools import MockSearchTool, SearchTool, degraded_search_record, run_search
from .triage import MockTriageProvider, VERDICT_HIGH, run_triage
from .validation import validate_agent_output
from .validator import MockValidatorProvider, run_validation_worker, stricter_result
from .planner import DEFAULT_SPECIALIST_ROLE_ID, role_output_spec
from .worker import (
    MockProvider, MockTrialProvider, Provider, build_role_prompt, run_analysis_worker,
)
from .working_memory import WorkingMemoryStore
from .workspace import DryRunWriter, WorkspaceWriter, run_write

# R7.1: the selective-validation policy value for ``independent_validation`` — validate
# only when the task's classification requires it (see independent_validation_required).
AUTO_VALIDATION = "auto"

# The run's steps, named once. Two consumers read these and they must not drift: the
# programization observer records WHICH steps ran (repetition detection keys on the list), and
# the progress reporter says which one is running NOW. They are the same concepts, so a rename
# has to move both — which is only true if there is one spelling.
STEP_INTAKE = "intake"
STEP_CORE_BINDING = "core_binding"
STEP_PRIME_PLANNING = "prime_planning"
STEP_SEARCH = "readonly_search"
STEP_MEMORY_RETRIEVAL = "memory_retrieval"
STEP_ANALYSIS_WORKER = "analysis_worker"
STEP_AUTOMATIC_VALIDATION = "automatic_validation"
STEP_INDEPENDENT_VALIDATION = "independent_validation"
STEP_CONTROLLED_WRITE = "controlled_write"
# M3's regeneration is a step the observer's list does not carry (it records the shape of a
# run for repetition detection, and a revision is an exception, not part of the shape) — but
# it is a second full model call, so a run that silently sat in it for another 30 seconds is
# exactly the silence this reporting exists to remove.
STEP_REVISION = "revision"

# The subset :func:`run_task` actually reports, in order. Deliberately NOT every step: intake,
# core binding and prime planning are sub-second, so announcing them would cost three channel
# round-trips to tell Thomas about nothing, and Telegram rate-limits edits to a message. What
# is announced is what can make him wait — a network search, a model call, a review, a write.
PROGRESS_STAGES: tuple[str, ...] = (
    STEP_SEARCH,
    STEP_ANALYSIS_WORKER,
    STEP_AUTOMATIC_VALIDATION,
    STEP_INDEPENDENT_VALIDATION,
    STEP_REVISION,
    STEP_CONTROLLED_WRITE,
)


def _progress(on_progress: Any, stage: str) -> None:
    """Report one stage, and never let reporting cost the run.

    Every exception is swallowed, not just the typed ones: the callback reaches a network
    channel, and the whole point of the run is the analysis at the end of it. A progress
    notice that could raise would make this feature able to destroy the thing it narrates —
    the ack and delivery-pointer precedent, applied to something that fires six times a run
    instead of once.
    """
    if on_progress is None:
        return
    try:
        on_progress(stage)
    except Exception:      # noqa: BLE001 — deliberate: see the docstring
        pass


# §8.5: the Role that was actually selected decides the prompt, the output mapping and the
# validation requirement. `general.specialist` keeps the business-analysis path byte-identical
# (its own contract is what `_role_specific_output(None)` already emits, perspectives included);
# any other routable Role runs against ITS declared contract instead. Returned as the two
# arguments the worker and the validator each need, so no call site re-derives them.
def _role_output_spec(plan: Mapping[str, Any]) -> dict[str, str] | None:
    """The selected Role's declared output contract, or None for the business analyst
    (whose contract is what the worker already emits by default)."""
    definition = plan.get("role_definition")
    if not definition or definition.get("role_id") == DEFAULT_SPECIALIST_ROLE_ID:
        return None
    return role_output_spec(definition)


def _role_execution(plan: Mapping[str, Any], **prompt_context) -> tuple[str | None, list[str] | None]:
    """``(prompt_override, role_output_keys)`` for the selected Role — (None, None) for the
    business analyst, whose prompt and output shape are the worker's defaults."""
    spec = _role_output_spec(plan)
    if spec is None:
        return None, None
    prompt = build_role_prompt(
        plan["task"], plan["role_assignment"], plan["role_definition"], spec, **prompt_context)
    return prompt, list(spec)


def _provider_for_role(provider: Provider, spec: Mapping[str, str] | None) -> Provider:
    """The provider that can actually answer the selected Role's output contract.

    Two honest outcomes, no third:

    * the deterministic path — swap the business-analysis ``MockProvider`` for a role-shaped
      one built from the Role's own declared keys. That is the trial path's existing
      ``MockTrialProvider``, reused rather than reinvented: "a mock that answers exactly this
      role's contract" is the same idea both times.
    * a **bindable** provider — ask it for the Role's keys as well. The hosted schemas are
      closed over the analysis key set (OpenAI strict via ``additionalProperties: false``,
      Google via the enforced ``required`` list), so a hosted model asked for
      ``translated_text`` returns without it; ``bind_role_output_keys`` re-derives the schema
      with the Role's keys folded in. Binding returns a COPY — the provider is selected once
      per process and one run's Role must not change what the next run asks for.
    * a network provider that cannot bind — **refuse**, by name. Left alone it would come back
      missing the key and fail the run's own output check, reading like a model quality
      problem when it is a schema problem.
    """
    if spec is None:
        return provider
    binder = getattr(provider, "bind_role_output_keys", None)
    if binder is not None:
        return binder(spec)
    if getattr(provider, "network_egress", False):
        raise WorkerBlocked(
            "ROLE_OUTPUT_CONTRACT_UNSUPPORTED_BY_PROVIDER",
            f"{provider.model_id} cannot be bound to this Role's output contract "
            f"({sorted(spec)}); a network provider that cannot ask for the Role's keys would "
            "return without them and fail the run's own output check",
        )
    return MockTrialProvider(dict(spec))


def render_response(
    agent_output: dict[str, Any],
    *,
    independently_validated: bool = False,
    search_hits: list[dict[str, Any]] | None = None,
) -> str:
    """Render a human-readable final response from a validated Agent Output.

    ``independently_validated`` states what actually happened to THIS run: a delivered
    response only exists when the stricter merged outcome PASSed, so if the independent
    reviewer ran, it passed — and the footer must say so. The old constant footer told
    the reader "not independently verified" on exactly the runs where a second agent had
    reviewed and passed the analysis (the architecture review's P1-3).

    ``search_hits`` renders a Sources section: the analysis cites evidence as [S1]..[SN]
    (the worker numbers the hits that way in its prompt), and without this section the
    reader received citations that resolved to nothing. Same order as the prompt, so the
    numbers line up by construction. Mock hits render too — an honest display of what the
    evidence actually was."""
    rso = agent_output.get("role_specific_output", {})
    lines = [f"# {agent_output.get('goal', 'Analysis')}", "", agent_output.get("summary", ""), ""]
    findings = rso.get("key_findings", [])
    if findings:
        lines.append("## Key findings")
        lines += [f"- {f}" for f in findings]
        lines.append("")
    # §10.4: the separated judgements are rendered, not merely recorded. Keeping them in the
    # ledger alone would leave the reader with the same single blended narrative the
    # separation exists to break up — the disagreement has to be visible where it is read,
    # and it is placed BEFORE the recommendation so it is read as input to it rather than as
    # a footnote to a conclusion already accepted.
    perspectives = rso.get("perspectives", [])
    if perspectives:
        lines.append("## Perspectives")
        lines += [
            f"- **{p.get('perspective', '')}** ({p.get('verdict', '')}): {p.get('basis', '')}"
            for p in perspectives
        ]
        lines.append("")
    rec = agent_output.get("recommendation")
    if rec:
        lines += ["## Recommendation", f"{rec['action']} — {rec['reason']}", ""]
    if agent_output.get("uncertainty"):
        lines += ["## Uncertainty", *[f"- {u}" for u in agent_output["uncertainty"]], ""]
    if search_hits:
        lines.append("## Sources")
        lines += [
            f"- [S{index}] {hit.get('title', '')} — {hit.get('url', '')}"
            for index, hit in enumerate(search_hits, start=1)
        ]
        lines.append("")
    lines.append(
        "_Read-only analysis; automatically validated and independently reviewed (PASS)._"
        if independently_validated else
        "_Read-only analysis; automatically validated, not independently verified._"
    )
    return "\n".join(lines).strip()


def _block_record(*, stage: str, reason_code: str, message: str, raw_request: str,
                  received_task: dict[str, Any] | None, now: str) -> dict[str, Any]:
    """A minimal, durable block entry for a run that failed before a Core binding exists.

    Not an ``audit_event.v0.1`` — that schema requires a bound task — so this records the
    stage, reason, request fingerprint (never the raw text beyond a hash), and trace id."""
    trace = received_task["identity"]["trace_id"] if received_task else None
    # stamped_event gives block entries the same tamper-evident self-hash as the other
    # standalone ledger events (this builder was the one that had forgotten it).
    return stamped_event(
        "run_block.v0", stage=stage, reason_code=reason_code, message=message,
        request_sha256=integrity.sha256_record({"raw_request": raw_request}),
        trace_id=trace, created_at=now,
    )


def _write_report(write_use: Mapping[str, Any]) -> dict[str, Any]:
    """The operator-facing report of one controlled write (the REPORT of EXECUTE_AND_REPORT).

    Built the moment the write happens so every return path carries it — a write that
    succeeded and then hit a persistence failure is still a file on disk, and saying so is
    the whole point of the disposition."""
    return {
        "relative_path": write_use["relative_path"],
        "target_ref": write_use["target_ref"],
        "bytes_written": write_use["bytes_written"],
        "content_sha256": write_use["content_sha256"],
        "filesystem_write": write_use["filesystem_write"],
        "disposition": "EXECUTE_AND_REPORT",
    }


def _persist(store: LedgerStore, records: dict[str, Any]) -> None:
    """Append all produced records + audit trail + any block entry (fail-closed)."""
    trace = records.get("received_task", {}).get("identity", {}).get("trace_id")
    store.append_records(trace, records)
    if records.get("audit_trail"):
        store.append_audit_events(records["audit_trail"])
    if records.get("block_record"):
        store.append_block(records["block_record"])


def _finalize_block(
    result: dict[str, Any], records: dict[str, Any], exc: MvpRuntimeError, *,
    raw_request: str, received_task: dict[str, Any] | None, now: str,
    genesis: str | None, store: LedgerStore | None, repo_root: Path | None,
) -> dict[str, Any]:
    """Record and persist a blocked outcome. The original block reason is always
    preserved; a secondary audit/persist failure is noted, never masked."""
    result["block"] = {"stage": "pipeline", "reason_code": exc.reason_code, "message": exc.reason}
    bound_task = records.get("task")
    try:
        if bound_task is not None:
            records["audit_trail"] = build_blocked_audit(
                bound_task, stage="pipeline", reason_code=exc.reason_code, now=now,
                genesis_previous_hash=genesis, repo_root=repo_root,
            )
        else:
            records["block_record"] = _block_record(
                stage="pre_binding", reason_code=exc.reason_code, message=exc.reason,
                raw_request=raw_request, received_task=received_task, now=now,
            )
        if store is not None:
            _persist(store, records)
    except MvpRuntimeError as secondary:
        # Both places: the block detail keeps the local context, and the run-level field is
        # THE signal a caller reads to answer "is this run's evidence durable?" — the CLI
        # used to check only the block stage and printed "LEDGER: recorded" over this path.
        result["block"]["persist_error"] = secondary.reason_code
        result["persist_error"] = secondary.reason_code
    return result


def _planned_allocation(
    *, auto_policy: bool, independent_validation: bool | str, revise: bool
) -> tuple[int, int]:
    """``(planned_agents, planned_triage_calls)`` — the ceiling this run is planned under.

    The allocation must cover every agent the plan MAY invoke, because the contract forbids an
    assignment from exceeding its parent task's remaining budget: two assignments each granted
    one model call under a task allocated exactly one is the
    ``subtasks_and_new_assignments_cannot_increase_parent_remaining_budget`` breach, which the
    runtime silently committed on every validated run until this arithmetic existed.

    M3: an armed revision loop pre-allocates its retry here — a regenerated specialist call
    plus a re-verify — so a revision can never spend past what the run was planned under
    (over budget => no loop). A ceiling, not a claim: a run that passes first time simply
    spends less than it allocated (the triage precedent).
    """
    base_agents = 2 if (auto_policy or independent_validation) else 1
    return base_agents + (2 if revise else 0), (1 if auto_policy else 0)


def _record_plan(plan: Mapping[str, Any], records: dict[str, Any], *, wrote_path: bool) -> None:
    """Put the plan's governance records on the run's record set.

    The conditional halves are the point: a validator assignment exists only when one was
    planned, and the write grant is recorded only when a write was asked for — the ledger must
    hold the decision an action was taken under, not merely the audit event reporting it.
    """
    records.update({
        "task": plan["task"], "binding": plan["binding"],
        "permission_decision": plan["permission_decision"],
        "search_permission_decision": plan["search_permission_decision"],
        "role_assignment": plan["role_assignment"],
    })
    if "validator_assignment" in plan:
        records["validator_permission_decision"] = plan["validator_permission_decision"]
        records["validator_assignment"] = plan["validator_assignment"]
    if wrote_path:
        records["write_permission_decision"] = plan["write_permission_decision"]


def _settle_reviewer(
    plan: Mapping[str, Any],
    records: dict[str, Any],
    *,
    auto_policy: bool,
    independent_validation: bool | str,
    triage_provider: Provider,
    now: str,
) -> tuple[bool, dict[str, Any] | None, dict[str, Any] | None]:
    """R7.2: decide whether the PLANNED reviewer actually runs.

    Returns ``(validate_run, triage_result, triage_invocation)``. Two paths, and they write
    different records, which is why this is worth a name of its own:

    * the classification already decided — an important priority or ORANGE/RED risk means no
      triage is owed, so none was planned and none is called;
    * otherwise the orchestrator's own governed triage call judges the request, and its
      permission decision, verdict and invocation all go on the record.

    Without the auto policy there is nothing to settle: the caller's flag is the answer.
    """
    if not auto_policy:
        return bool(independent_validation), None, None
    triage_permdec = plan.get("triage_permission_decision")
    if triage_permdec is None:
        return True, None, None     # classification already required the review
    records["triage_permission_decision"] = triage_permdec
    triage_result, triage_invocation = run_triage(
        plan["task"], provider=triage_provider, created_at=now,
    )
    records["triage_result"] = triage_result
    if triage_invocation is not None:
        records["triage_invocation"] = triage_invocation
    return triage_result["verdict"] == VERDICT_HIGH, triage_result, triage_invocation


def _select_specialist_provider(
    provider: Provider,
    records: dict[str, Any],
    *,
    tiered_provider_selector: Callable[[str], tuple[Provider, dict[str, Any]]] | None,
    triage_result: Mapping[str, Any] | None,
) -> Provider:
    """M2: let the M1 difficulty pick the specialist's model tier, or keep the base provider.

    Only when triage produced a difficulty AND a selector is wired — so the default and every
    mock run are unchanged. A missing tier grant degrades to the base chain (TIER_DEGRADED),
    recorded, never blocking. The tiered model serves the SPECIALIST only; the validator and
    the triage keep their own provider.
    """
    if tiered_provider_selector is None or triage_result is None:
        return provider
    specialist_provider, tier_selection = tiered_provider_selector(triage_result["difficulty"])
    records["model_tier_selection"] = tier_selection
    return specialist_provider


@dataclass(frozen=True)
class _RevisionSpend:
    """What the M3 revision loop's FIRST attempt cost, on top of the final one.

    The usage block reads the FINAL ``invocation`` / ``validator_invocation``, so a revised run
    has to add the attempt those replaced. Four loose ``rev_*`` locals carried that across 120
    lines of ``run_task``; as one value the rule sits next to the arithmetic, and the eventual
    revision-loop extraction has something to return.
    """

    agent_invocations: int = 0
    model_calls: int = 0
    tokens: int = 0
    retries: int = 0

    @classmethod
    def from_first_attempt(
        cls, invocation: Mapping[str, Any], validator_invocation: Mapping[str, Any] | None
    ) -> "_RevisionSpend":
        """The spend of the attempt a revision replaced.

        Only the FIRST run's calls are extra: the regenerated specialist and its re-verify ARE
        the final invocation pair the usage block already counts.
        """
        agents = 1 + (1 if validator_invocation is not None else 0)
        return cls(
            agent_invocations=agents,
            model_calls=agents,
            tokens=(int(invocation.get("tokens_used", 0))
                    + int((validator_invocation or {}).get("tokens_used", 0))),
            retries=(int(invocation.get("retry_count", 0))
                     + int((validator_invocation or {}).get("retry_count", 0))),
        )


def _record_spend(
    limits: Mapping[str, Any],
    *,
    invocation: Mapping[str, Any],
    validator_invocation: Mapping[str, Any] | None,
    triage_invocation: Mapping[str, Any] | None,
    revision_spend: _RevisionSpend,
    independently_validated: bool,
    revised: bool,
) -> dict[str, Any]:
    """What the run actually spent, against the allocation it ran under.

    The task and assignment records are allocations built BEFORE execution, so their zeroed
    usage can never answer "what did this run spend?" — this record is what satisfies the
    contract's ``usage_must_be_recorded_for_audit``.

    Two counting rules live here rather than in the caller's locals:

    * the R7.2 triage is Prime's model call, not an agent's, so it counts toward
      ``model_calls`` and ``tokens_used`` but never toward ``agent_invocations``;
    * a revision's first attempt is additive to the final spend (see :class:`_RevisionSpend`),
      and ``revision_cycles`` surfaces the retry on its own rather than hiding it inside a
      larger ``model_calls``.
    """
    agents = 2 if validator_invocation is not None else 1
    return recorded_usage_budget(
        limits,
        agent_invocations=agents + revision_spend.agent_invocations,
        model_calls=(agents + (1 if triage_invocation is not None else 0)
                     + revision_spend.model_calls),
        tokens_used=(
            int(invocation.get("tokens_used", 0))
            + int((validator_invocation or {}).get("tokens_used", 0))
            + int((triage_invocation or {}).get("tokens_used", 0))
            + revision_spend.tokens
        ),
        validation_cycles=2 if independently_validated else 1,
        revision_cycles=1 if revised else 0,
        # Every model call the run made, matching what tokens_used sums. It counted only the
        # specialist and the reviewer, so a run whose triage retried a 503 — or whose
        # pre-revision calls did — reported 0 and read as a clean first try.
        retry_count=(
            int(invocation.get("retry_count", 0))
            + int((validator_invocation or {}).get("retry_count", 0))
            + int((triage_invocation or {}).get("retry_count", 0))
            + revision_spend.retries
        ),
    )


# --- enrichment seams -----------------------------------------------------------------
#
# Three things a completed run does BESIDES producing its answer: capture what a revision
# taught, persist that capture, and count the run toward a repetition pattern. They share one
# rule, and it is the reason they are named here instead of sitting inline in `run_task`:
#
#     **enrichment never fails a delivered run.**
#
# Each catches its own typed failures and reports them on the result (`learning_error`,
# `working_memory_error`, `programization_error`) rather than raising. That matters
# structurally: all three run INSIDE `run_task`'s outer `except MvpRuntimeError`, which turns
# any escape into a full BLOCK — so an un-caught enrichment failure would withhold an analysis
# that was already produced and durably audited. Extracted as functions so the rule is stated
# once and each seam's inputs are visible, rather than being three unlabelled stretches of a
# 500-line function.


def _mint_revision_correction(
    task: Mapping[str, Any],
    assignment: Mapping[str, Any],
    *,
    raw_request: str,
    revision_requests: list[str],
    now: str,
    working_memory: WorkingMemoryStore | None,
    outcome: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """M5a: the correction a revision taught, as a working-memory CANDIDATE + its audit event.

    Returns ``(candidate, event, error_code)`` — all None when there is nothing to learn: no
    store wired, the revision did not recover to PASS, or the assignment does not permit
    candidate creation. CANDIDATE-status only, so even Thomas's own correction is captured
    rather than trusted; promotion stays his explicit step.
    """
    if working_memory is None or outcome != "PASS":
        return None, None, None
    ctype = candidate_type_for(assignment)
    if ctype is None:
        return None, None, None
    identity = task.get("identity", {})
    ctx = task.get("context", {})
    goal = task.get("request", {}).get("normalized_goal") or raw_request
    delta = "; ".join(dict.fromkeys(revision_requests))
    content = (
        f"교정 학습: «{goal[:200]}» 유형의 요청에서 첫 분석이 다음을 "
        f"수정한 뒤에야 통과했습니다 — {delta}"
    )
    try:
        correction = build_correction_candidate(
            content, source=LEARNING_SOURCE_REVISION, now=now, candidate_type=ctype,
            seed={"trace_id": identity.get("trace_id"), "task_id": identity.get("task_id")},
            origin={
                "task_id": identity.get("task_id"),
                "task_revision": identity.get("task_revision"),
                "trace_id": identity.get("trace_id"),
                "core_context_binding_id": ctx.get("core_context_binding_id"),
                "data_sensitivity": ctx.get("data_sensitivity"),
            },
            correction_ref=f"trace:{identity.get('trace_id')}",
        )
    except MvpRuntimeError as exc:
        # A secret-bearing delta or a bad origin loses the capture, never the run.
        return None, None, exc.reason_code
    if correction is None:
        return None, None, None
    return correction, build_learning_event(
        correction, source=LEARNING_SOURCE_REVISION, now=now,
        trace_id=identity.get("trace_id"),
    ), None


def _persist_learning(
    *,
    working_memory: WorkingMemoryStore | None,
    store: LedgerStore | None,
    agent_output: Mapping[str, Any],
    learning_candidates: list[dict[str, Any]],
    learning_events: list[dict[str, Any]],
) -> tuple[bool, str | None, str | None]:
    """Accumulate this run's memory candidates and audit any minted correction.

    Returns ``(stored, working_memory_error, learning_error)``. The audit is gated on the
    append having SUCCEEDED: the event's own contract is that a candidate "was captured", so
    emitting it after a failed append would put a tamper-evident claim on the ledger for a
    candidate that is in no store — and ``/promote`` on that id would refuse CANDIDATE_GONE
    while the audit said it existed.
    """
    if working_memory is None:
        return False, None, None
    try:
        working_memory.append(
            list(agent_output.get("memory_candidates", [])) + learning_candidates
        )
    except PersistenceError as exc:
        # Nothing landed: the correction rides the same single append as the ordinary
        # candidates, so a failure loses both.
        return False, exc.reason_code, (exc.reason_code if learning_candidates else None)
    if store is not None and learning_events:
        try:
            for event in learning_events:
                store.append_memory_event(event)
        except PersistenceError as exc:
            return True, None, exc.reason_code
    return True, None, None


def _observe_repetition(
    programization: ProgramizationStore | None,
    *,
    task: Mapping[str, Any],
    assignment: Mapping[str, Any],
    outcome: str,
    working_memory: WorkingMemoryStore | None,
    independently_validated: bool,
    wrote: bool,
    specialist_provider: Any,
    now: str,
    repo_root: Path | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool, str | None]:
    """Count one COMPLETED+PASS run toward its repetition pattern (Programization Policy).

    Returns ``(observation, pattern, triggered, error_code)``. Threshold-many valid
    independent repetitions raise a **review trigger only** — no Program is created or
    activated here or anywhere.
    """
    if programization is None or outcome != "PASS":
        return None, None, False, None
    steps_run = [STEP_INTAKE, STEP_CORE_BINDING, STEP_PRIME_PLANNING, STEP_SEARCH]
    if working_memory is not None:
        steps_run.append(STEP_MEMORY_RETRIEVAL)
    steps_run += [STEP_ANALYSIS_WORKER, STEP_AUTOMATIC_VALIDATION]
    if independently_validated:
        steps_run.append(STEP_INDEPENDENT_VALIDATION)
    if wrote:
        steps_run.append(STEP_CONTROLLED_WRITE)
    try:
        observation, pattern, triggered = observe_completed_run(
            programization, task=task, assignment=assignment, steps=steps_run,
            # A provider with no network egress is an in-process mock: a synthetic run,
            # observed but never counted (policy §4). The specialist provider is what
            # actually produced this run's output (tiered when M2 selected one).
            synthetic=not bool(getattr(specialist_provider, "network_egress", False)),
            now=now, repo_root=repo_root,
        )
    except (PersistenceError, ProgramizationBlocked) as exc:
        return None, None, False, exc.reason_code
    return observation, pattern, triggered, None


@dataclass(frozen=True)
class _RevisionAttempt:
    """What the one governed regeneration replaced, and what it produced.

    Eleven values that used to be assignments into ``run_task``'s scope. As one returned value
    the caller's rebinding is a visible list rather than a block of statements buried mid-
    function, and the M3 rule — a revision REPLACES the first attempt's output, validation and
    review — is legible from the field names.
    """

    agent_output: dict[str, Any]
    invocation: dict[str, Any]
    validation: dict[str, Any]
    independent_validation_result: dict[str, Any] | None
    validator_invocation: dict[str, Any] | None
    outcome: str
    revision: dict[str, Any]
    spend: _RevisionSpend
    correction: dict[str, Any] | None
    correction_event: dict[str, Any] | None
    correction_error: str | None


def _revise_once(
    plan: Mapping[str, Any],
    records: dict[str, Any],
    *,
    raw_request: str,
    validation: Mapping[str, Any],
    independent_validation_result: Mapping[str, Any] | None,
    invocation: Mapping[str, Any],
    validator_invocation: Mapping[str, Any] | None,
    validate_run: bool,
    specialist_provider: Provider,
    validator_provider: Provider,
    search_hits: list[dict[str, Any]],
    memory_entries: list[dict[str, Any]],
    validated_entries: list[dict[str, Any]],
    keyword_rows: list[dict[str, Any]],
    working_memory: WorkingMemoryStore | None,
    now: str,
    repo_root: Path | None,
) -> _RevisionAttempt:
    """M3: spend the ONE pre-allocated regeneration a validation REVISE earns.

    The required revisions — the automatic reasons plus the independent reviewer's — are fed
    back to the specialist, the revised output is re-validated under the same rules, and the
    stricter outcome stands. A hard cap of one: this is called from a single ``if``, never a
    loop, so a revised output that still REVISEs is not revised again, it BLOCKs
    (REVISION_EXHAUSTED). BLOCK is never revised at all (unusable, not fixable).

    ``records`` is written **incrementally**, as each step completes, and that is deliberate
    rather than a leaked parameter: if the re-verify raises, ``run_task``'s handler persists
    whatever the revision had already produced, so a blocked run's trail still shows the
    regenerated output and its validation. Returning everything at the end and letting the
    caller record it would lose exactly that on the failure path.
    """
    revision_requests = list(validation["validation"]["result_reasons"])
    if independent_validation_result is not None:
        revision_requests += list(independent_validation_result["validation"].get("result_reasons") or [])
    first_invocation = invocation
    spend = _RevisionSpend.from_first_attempt(invocation, validator_invocation)

    role_prompt, role_output_keys = _role_execution(
        plan, search_hits=search_hits, memory_entries=memory_entries,
        validated_entries=validated_entries, revision_requests=revision_requests,
        keyword_rows=keyword_rows)
    agent_output, invocation = run_analysis_worker(
        plan["task"], plan["role_assignment"], provider=specialist_provider, created_at=now,
        search_hits=search_hits, memory_entries=memory_entries,
        validated_entries=validated_entries, keyword_rows=keyword_rows, repo_root=repo_root,
        revision_requests=revision_requests,
        prompt_override=role_prompt, role_output_keys=role_output_keys,
    )
    records["agent_output"] = agent_output
    records["invocation"] = invocation

    validation = validate_agent_output(
        agent_output, plan["task"], plan["role_assignment"], now=now, repo_root=repo_root,
        required_role_output_keys=role_output_keys)
    records["validation_result"] = validation

    independent_validation_result = validator_invocation = None
    if validate_run and validation["validation"]["result"] == "PASS":
        independent_validation_result, validator_invocation = run_validation_worker(
            plan["task"], plan["validator_assignment"], agent_output,
            provider=validator_provider, created_at=now, repo_root=repo_root,
        )
        records["independent_validation_result"] = independent_validation_result
        records["validator_invocation"] = validator_invocation

    outcome = validation["validation"]["result"]
    if independent_validation_result is not None:
        outcome = stricter_result(outcome, independent_validation_result["validation"]["result"])
    revision = {
        "attempted": True, "exhausted": outcome != "PASS",
        "requests": revision_requests, "first_invocation": first_invocation,
    }
    records["revision"] = revision

    # M5a: a revision that recovered a REVISE to a PASS is a correction — captured for later
    # similar requests. Enrichment: see the seam functions above.
    correction, correction_event, correction_error = _mint_revision_correction(
        plan["task"], plan["role_assignment"],
        raw_request=raw_request, revision_requests=revision_requests, now=now,
        working_memory=working_memory, outcome=outcome,
    )
    return _RevisionAttempt(
        agent_output=agent_output, invocation=invocation, validation=validation,
        independent_validation_result=independent_validation_result,
        validator_invocation=validator_invocation, outcome=outcome,
        revision=revision, spend=spend,
        correction=correction, correction_event=correction_event,
        correction_error=correction_error,
    )


def run_task(
    raw_request: str,
    *,
    provider: Provider | None = None,
    search_tool: SearchTool | None = None,
    keyword_seeds: str | None = None,
    working_memory: WorkingMemoryStore | None = None,
    programization: ProgramizationStore | None = None,
    now: str | None = None,
    repo_root: Path | None = None,
    store: LedgerStore | None = None,
    independent_validation: bool | str = False,
    request_kind: str | None = None,
    validator_provider: Provider | None = None,
    tiered_provider_selector: Callable[[str], tuple[Provider, dict[str, Any]]] | None = None,
    revise: bool = False,
    write_path: str | None = None,
    writer: WorkspaceWriter | None = None,
    on_progress: Callable[[str], None] | None = None,
    **intake_kwargs: Any,
) -> dict[str, Any]:
    """Run one task end-to-end. Returns a structured result; never raises for a
    fail-closed condition (those become ``status == "BLOCKED"``). Pass ``store`` to
    persist the records + audit trail durably (the CLI does).

    ``on_progress`` (opt-in) is called with one of :data:`PROGRESS_STAGES` as each
    time-consuming stage begins. It is a **notification, not a hook**: it receives a stage
    name and nothing else, it cannot influence the run, and every exception it raises is
    swallowed (see :func:`_progress`) — a run must never die narrating itself. It is called
    only on stages that actually run, so a caller learns the shape of this run rather than a
    plan of it: no search-and-then-nothing when the search degraded, no independent review
    announced for a run that skipped it.

    ``search_tool`` runs a read-only web search whose hits become source-attributed
    evidence on the output (default ``MockSearchTool`` — deterministic, no network; a real
    network tool is chosen via the Safety-Flag Gate by the caller).

    ``keyword_seeds`` (opt-in) runs one Naver keyword brief — measured monthly demand,
    competition counts, and a trend series for the given comma-separated seeds — whose rows
    become ``[K#]`` evidence the same way search hits become ``[S#]``. Explicit rather than
    derived from the request text: the Search Ad API wants short keyword seeds, and a
    sentence-shaped goal fed to it would measure garbage while looking like research. Same
    enrichment posture as search — the brief's tools come from the env-only gate
    (deterministic Mocks until ``MVP_NAVER_RESEARCH=enabled``), a backend failure degrades
    the run rather than blocking it, and the use is recorded (``keyword_research``) either
    way. Omitted (None) = no brief, byte-identical to before the lane existed.

    ``working_memory`` (opt-in) makes prior working-memory candidates and operator-promoted
    VALIDATED memory available as context and accumulates this run's candidates for later
    runs. Omitting it keeps the run pure and deterministic — memory only accumulates when a
    caller supplies the store.

    ``programization`` (opt-in) records one repetition observation per COMPLETED+PASS run
    and folds it into the pattern counter (Programization Review Policy). Threshold-many
    valid independent repetitions raise a **review trigger only** — audited as
    PROGRAMIZATION_REVIEW_TRIGGERED, never creating or activating a Program. Best-effort:
    counting is enrichment, so a counter failure is noted on the result
    (``programization_error``) but never withholds a delivered run.

    ``independent_validation`` (R7, opt-in) adds the second agent of the minimal dynamic
    team: an independent validator (``validation.independent``) reviews the specialist's
    output in a fresh context and the **stricter** of the automatic and independent results
    decides delivery. The validator is skipped when the automatic checks already BLOCK
    (the outcome is decided; no model call is spent). ``validator_provider`` defaults to the
    deterministic ``MockValidatorProvider`` when the specialist uses the mock provider, and
    to the same (gated) provider otherwise — one Safety-Flag Gate governs both agents.

    ``independent_validation`` also accepts :data:`AUTO_VALIDATION` (``"auto"``): the
    reviewer runs only when the task warrants it. The classification decides first —
    ORANGE/RED risk mandates the review (policy §3.4), an operator-marked important
    priority (HIGH/URGENT) requests it. When neither decides (R7.2), the **orchestrator
    judges**: Prime plans a governed triage action and one deliberately small model call
    on the (cheap) validator/triage provider returns HIGH or NORMAL. Everyday NORMAL
    verdicts spend the small triage call instead of a full second review; a triage
    failure degrades to NORMAL and is recorded (``TRIAGE_DEGRADED``), never a block —
    the reviewer is an enhancement, and a broken triage must neither stop the analysis
    nor silently double every run's spend.

    ``write_path`` (R8, opt-in) additionally creates the rendered response as a file at
    that workspace-relative path — the runtime's first EXECUTE_AND_REPORT action. Supplying
    it plans a WORKSPACE_REVERSIBLE_WRITE grant; the write itself happens only if validation
    PASSES, is create-only and confined to ``workspace/``, and is reported (audited, plus
    ``result["write"]``). ``writer`` defaults to the ``DryRunWriter``, which computes the
    write without touching disk; a real writer is chosen via the Safety-Flag Gate by the
    caller (``workspace.select_writer``)."""
    provider = provider if provider is not None else MockProvider()
    search_tool = search_tool if search_tool is not None else MockSearchTool()
    if validator_provider is None:
        validator_provider = MockValidatorProvider() if isinstance(provider, MockProvider) else provider
    # R7.2: the orchestrator triage rides the validator's (cheap) provider; the mock
    # pairing stays mock so default runs remain deterministic and network-free.
    triage_provider = (
        MockTriageProvider() if isinstance(validator_provider, MockValidatorProvider)
        else validator_provider
    )
    now = now if now is not None else timeutil.utc_now_iso()

    # R7.2: under "auto" the reviewer decision may come from the orchestrator's triage,
    # which runs only after planning — so the allocation covers the LARGEST team this
    # plan may invoke (an allocation is a ceiling, not a claim; the spend is recorded in
    # budget_usage), and `validate_run` is settled before the specialist runs.
    auto_policy = independent_validation == AUTO_VALIDATION
    if not auto_policy:
        independent_validation = bool(independent_validation)
    result: dict[str, Any] = {
        "status": "BLOCKED",
        "delivered": False,
        "now": now,
        "final_response": None,
        "block": None,
        # THE run-level answer to "is this run's evidence durable?" — None means every
        # record and audit event this run produced was persisted (or no store was given).
        # Every persistence failure path sets it, so a caller never has to know which of
        # the four failure shapes it is looking at to report the truth.
        "persist_error": None,
        "records": {},
    }
    records = result["records"]
    # M5a: a correction that turned this run around — the M3 revision loop recovering a
    # REVISE to a PASS — is captured as a working-memory candidate for later similar
    # requests. Initialized before the try so the delivered-path accumulation below always
    # has them defined, even on the runs that mint none.
    learning_candidates: list[dict[str, Any]] = []
    learning_events: list[dict[str, Any]] = []

    # Chain this run's audit onto the ledger tip. A corrupt/unreadable ledger fails closed.
    try:
        genesis = store.last_audit_hash() if store is not None else None
    except PersistenceError as exc:
        result["block"] = {"stage": "persistence", "reason_code": exc.reason_code, "message": exc.reason}
        result["persist_error"] = exc.reason_code
        return result

    received_task: dict[str, Any] | None = None
    try:
        # The task allocation must cover every agent the plan will invoke: with R7 on,
        # two assignments each granted one model call under a task allocated exactly one
        # is the contract's "an assignment cannot exceed the parent's remaining budget"
        # breach, which the runtime was silently committing on every validated run.
        # M3: when the one-shot revision loop is armed, pre-allocate its retry up front —
        # a regenerated specialist call plus a re-verify — so a revision can never spend
        # past the allocation the run was planned under (over budget => no loop). The
        # allocation is a ceiling, not a claim: a run that passes first time simply spends
        # less than it allocated (the triage precedent).
        planned_agents, planned_triage_calls = _planned_allocation(
            auto_policy=auto_policy, independent_validation=independent_validation, revise=revise,
        )
        task = build_task(
            raw_request, now=now,
            planned_agents=planned_agents, planned_triage_calls=planned_triage_calls,
            **intake_kwargs,
        )
        # Set BEFORE planning can fail: `_finalize_block` reads it for the block record's
        # trace_id, so a run that dies in `plan_task` still names which request it was.
        received_task = task
        records["received_task"] = task

        plan = plan_task(
            task, now=now, repo_root=repo_root,
            independent_validation=AUTO_VALIDATION if auto_policy else independent_validation,
            controlled_write=write_path is not None,
            request_kind=request_kind,
        )
        _record_plan(plan, records, wrote_path=write_path is not None)

        validate_run, triage_result, triage_invocation = _settle_reviewer(
            plan, records, auto_policy=auto_policy,
            independent_validation=independent_validation,
            triage_provider=triage_provider, now=now,
        )
        specialist_provider = _select_specialist_provider(
            provider, records,
            tiered_provider_selector=tiered_provider_selector, triage_result=triage_result,
        )
        # §8.5: a Role that is not the business analyst needs a provider that can answer ITS
        # declared contract. Refuses by name rather than producing a confusing REVISE.
        specialist_provider = _provider_for_role(specialist_provider, _role_output_spec(plan))

        # R3: run the authorized read-only search (mock by default; gated real tool).
        # Its hits become source-attributed evidence; the use is recorded + audited.
        # Search is enrichment, not the task: a backend failure (quota exhausted,
        # transport, malformed response) DEGRADES the run to no live evidence — recorded
        # and audited (SEARCH_DEGRADED), never a blocked analysis. The R7.2
        # triage-degradation precedent, decided with the Tavily rollout.
        query = plan["task"].get("request", {}).get("normalized_goal") or raw_request
        _progress(on_progress, STEP_SEARCH)
        try:
            search_hits, tool_use = run_search(query, tool=search_tool, now=now)
        except ToolBlocked as exc:
            search_hits = []
            tool_use = degraded_search_record(search_tool, query, exc.reason_code, now=now)
        records["tool_use"] = tool_use

        # The Naver keyword brief (opt-in, blog content lane): measured demand as [K#]
        # evidence, exactly the search tool's posture — enrichment that degrades, never
        # blocks, and is recorded either way. Unlike run_search it does NOT raise on
        # failure: every leg (including an unusable seed) degrades internally and the
        # record's `degraded_legs` says which failed and why, so there is no except arm
        # here on purpose — one would be dead code guarding a contract the brief already
        # keeps.
        keyword_rows: list[dict[str, Any]] = []
        if keyword_seeds is not None:
            keyword_rows, keyword_record = naver_research.run_keyword_brief(
                keyword_seeds, now=now)
            records["keyword_research"] = keyword_record

        # R5: retrieve prior working-memory candidates as context (opt-in; read-only, scoped).
        # A corrupt store fails closed here (BLOCK), like the ledger.
        memory_entries = (
            retrieve_working_memory(plan["role_assignment"], working_memory, now=now)
            if working_memory is not None else []
        )
        records["memory_retrieved"] = memory_entries

        # Operator-promoted VALIDATED memory feeds back too — the read leg of the loop the
        # role's memory_policy already grants (readable scope: related_validated_memory).
        validated_entries = (
            retrieve_validated_memory(plan["role_assignment"], working_memory)
            if working_memory is not None else []
        )
        records["validated_memory_retrieved"] = validated_entries

        role_prompt, role_output_keys = _role_execution(
            plan, search_hits=search_hits, memory_entries=memory_entries,
            validated_entries=validated_entries, keyword_rows=keyword_rows)
        _progress(on_progress, STEP_ANALYSIS_WORKER)
        agent_output, invocation = run_analysis_worker(
            plan["task"], plan["role_assignment"], provider=specialist_provider, created_at=now,
            search_hits=search_hits, memory_entries=memory_entries,
            validated_entries=validated_entries, keyword_rows=keyword_rows, repo_root=repo_root,
            prompt_override=role_prompt, role_output_keys=role_output_keys,
        )
        records["agent_output"] = agent_output
        records["invocation"] = invocation

        _progress(on_progress, STEP_AUTOMATIC_VALIDATION)
        validation = validate_agent_output(agent_output, plan["task"], plan["role_assignment"],
                                           now=now, repo_root=repo_root,
                                           required_role_output_keys=role_output_keys)
        records["validation_result"] = validation

        # R7 (opt-in): the independent validator reviews the output in a fresh context.
        # Run ONLY when the automatic checks PASSed. The merge below is stricter-wins and
        # delivery requires PASS, so once the automatic result is REVISE or BLOCK the
        # reviewer's verdict cannot change this run's outcome — the call would spend the
        # run's most expensive tokens (the reviewer is typically the higher-tier provider)
        # on an already-decided result. Previously only BLOCK short-circuited, so every
        # REVISE paid for a review it could not act on.
        #
        # What is given up: the reviewer's findings used to be appended to a withheld run's
        # reasons. The automatic checks already name every failing check, so the run still
        # reports why it was withheld — it just no longer buys extra prose at model prices.
        #
        # The candidate-role trial deliberately keeps the wider `!= "BLOCK"` condition:
        # there the review is evidence for a later promotion decision, not a delivery gate,
        # and a trial's `independent_result` is worth more than the tokens it costs.
        independent_validation_result = validator_invocation = None
        if validate_run and validation["validation"]["result"] == "PASS":
            _progress(on_progress, STEP_INDEPENDENT_VALIDATION)
            independent_validation_result, validator_invocation = run_validation_worker(
                plan["task"], plan["validator_assignment"], agent_output,
                provider=validator_provider, created_at=now, repo_root=repo_root,
            )
            records["independent_validation_result"] = independent_validation_result
            records["validator_invocation"] = validator_invocation

        outcome = validation["validation"]["result"]
        if independent_validation_result is not None:
            outcome = stricter_result(outcome, independent_validation_result["validation"]["result"])

        # M3 (opt-in `revise`): a validation REVISE earns exactly ONE regeneration. The
        # required revisions (automatic reasons + the independent reviewer's) are fed back to
        # the specialist, the revised output is re-validated under the same rules, and the
        # stricter outcome stands. Hard cap of one: this is a single `if`, not a loop — a
        # revised output that still REVISEs is not revised again, it BLOCKs
        # (REVISION_EXHAUSTED). BLOCK is never revised (unusable, not fixable). The retry was
        # pre-allocated above; the worker still fails closed on an exhausted allocation, so
        # the loop cannot overspend even if this guard drifted.
        revision = None
        revision_spend = _RevisionSpend()
        if revise and outcome == "REVISE":
            _progress(on_progress, STEP_REVISION)
            attempt = _revise_once(
                plan, records,
                raw_request=raw_request, validation=validation,
                independent_validation_result=independent_validation_result,
                invocation=invocation, validator_invocation=validator_invocation,
                validate_run=validate_run,
                specialist_provider=specialist_provider, validator_provider=validator_provider,
                search_hits=search_hits, memory_entries=memory_entries,
                validated_entries=validated_entries, keyword_rows=keyword_rows,
                working_memory=working_memory,
                now=now, repo_root=repo_root,
            )
            # A revision REPLACES the first attempt's output, validation and review.
            agent_output = attempt.agent_output
            invocation = attempt.invocation
            validation = attempt.validation
            independent_validation_result = attempt.independent_validation_result
            validator_invocation = attempt.validator_invocation
            outcome = attempt.outcome
            revision = attempt.revision
            revision_spend = attempt.spend
            if attempt.correction_error is not None:
                result.setdefault("learning_error", attempt.correction_error)
            if attempt.correction is not None:
                learning_candidates.append(attempt.correction)
                learning_events.append(attempt.correction_event)

        # R8 (opt-in): the controlled write — the runtime's first EXECUTE_AND_REPORT action.
        # Only a PASSING result is written: a rejected analysis must not leave an artifact
        # behind, so the write is gated on the same stricter outcome that gates delivery.
        # A write failure fails the run closed (BLOCKED) rather than delivering a result
        # that claims a file exists when it does not.
        write_use = None
        if write_path is not None and outcome == "PASS":
            _progress(on_progress, STEP_CONTROLLED_WRITE)
            _write_result, write_use = run_write(
                write_path,
                render_response(
                    agent_output,
                    independently_validated=independent_validation_result is not None,
                    search_hits=search_hits,
                ),
                writer=writer if writer is not None else DryRunWriter(),
                now=now, root=repo_root,
            )
            records["write_use"] = write_use
            # Report the write the moment it happens, not only on the delivered path. The
            # file is already on disk; if persistence fails after this, the run BLOCKs and
            # used to return no `write` key at all — an artifact existed that nothing
            # reported and nothing audited. The REPORT half of EXECUTE_AND_REPORT cannot
            # be conditional on what happens next.
            result["write"] = _write_report(write_use)

        # Programization repetition counter (opt-in, PASS only): one observation per
        # completed run, folded into its pattern. Threshold-many valid repetitions raise
        # a review trigger ONLY (audited below) — no Program is created or activated.
        # Best-effort like working-memory accumulation: the counter is enrichment, so a
        # failure is noted on the result, never blocks a delivered run.
        observation, programization_pattern, programization_triggered, obs_error = _observe_repetition(
            programization,
            task=plan["task"], assignment=plan["role_assignment"], outcome=outcome,
            working_memory=working_memory,
            independently_validated=independent_validation_result is not None,
            wrote=write_use is not None,
            specialist_provider=specialist_provider, now=now, repo_root=repo_root,
        )
        # Same shape as the try/except this replaced: on success the observation and its
        # pattern are recorded, on failure only the reason code is.
        if obs_error is not None:
            result.setdefault("programization_error", obs_error)
        elif observation is not None:
            records["programization_observation"] = observation
            records["programization_pattern"] = programization_pattern

        records["budget_usage"] = _record_spend(
            plan["task"].get("execution_budget", {}).get("limits", {}),
            invocation=invocation,
            validator_invocation=validator_invocation,
            triage_invocation=triage_invocation,
            revision_spend=revision_spend,
            independently_validated=independent_validation_result is not None,
            revised=revision is not None,
        )

        records["audit_trail"] = build_pipeline_audit(
            plan["task"], plan["permission_decision"], validation, agent_output, invocation,
            now=now, tool_use=tool_use, search_permission_decision=plan["search_permission_decision"],
            triage_result=triage_result,
            triage_invocation=triage_invocation,
            triage_permission_decision=plan.get("triage_permission_decision"),
            independent_validation_result=independent_validation_result,
            validator_invocation=validator_invocation,
            validator_permission_decision=plan.get("validator_permission_decision"),
            write_use=write_use,
            write_permission_decision=plan.get("write_permission_decision"),
            programization_pattern=programization_pattern,
            programization_triggered=programization_triggered,
            revision=revision,
            genesis_previous_hash=genesis, repo_root=repo_root,
        )
    except MvpRuntimeError as exc:
        return _finalize_block(
            result, records, exc, raw_request=raw_request, received_task=received_task,
            now=now, genesis=genesis, store=store, repo_root=repo_root,
        )

    if outcome == "PASS":
        # Fail-closed on persistence: a completed run with no durable audit is not delivered.
        if store is not None:
            try:
                _persist(store, records)
            except PersistenceError as exc:
                result["block"] = {"stage": "persistence", "reason_code": exc.reason_code, "message": exc.reason}
                result["persist_error"] = exc.reason_code
                return result
        # Accumulate this run's candidates into working memory for later runs. Best-effort:
        # working memory is enrichment, not the audit of record, so a write failure is noted
        # but does not withhold a delivered, durably-audited result. M5a's correction
        # candidate (if the revision loop minted one) rides the same append.
        stored, wm_error, learn_error = _persist_learning(
            working_memory=working_memory, store=store, agent_output=agent_output,
            learning_candidates=learning_candidates, learning_events=learning_events,
        )
        if wm_error is not None:
            result.setdefault("working_memory_error", wm_error)
        if learn_error is not None:
            result.setdefault("learning_error", learn_error)
        if stored and learning_candidates:
            result["learning_candidates"] = learning_candidates
        result["status"] = "COMPLETED"
        result["delivered"] = True
        result["final_response"] = render_response(
            agent_output,
            independently_validated=independent_validation_result is not None,
            search_hits=search_hits,
        )
    else:
        # Validation withheld delivery; the trail already concludes BLOCKED. Persist best-effort.
        if store is not None:
            try:
                _persist(store, records)
            except PersistenceError as exc:
                result.setdefault("persist_error", exc.reason_code)
        reasons = list(validation["validation"]["result_reasons"])
        if independent_validation_result is not None:
            reasons.extend(independent_validation_result["validation"]["result_reasons"])
        result["status"] = "BLOCKED"
        result["block"] = {
            "stage": "validation",
            "reason_code": f"VALIDATION_{outcome}",
            "message": "; ".join(dict.fromkeys(reasons)),
        }
    return result
