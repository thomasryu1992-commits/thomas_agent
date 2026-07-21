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

from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from . import timeutil
from .budgets import recorded_usage_budget
from .events import stamped_event
from .audit import build_blocked_audit, build_pipeline_audit
from .errors import MvpRuntimeError, PersistenceError
from .intake import build_task
from .memory import retrieve_working_memory
from .prime import plan_task
from .store import LedgerStore
from .tools import MockSearchTool, SearchTool, run_search
from .triage import MockTriageProvider, VERDICT_HIGH, run_triage
from .validation import validate_agent_output
from .validator import MockValidatorProvider, run_validation_worker, stricter_result
from .worker import MockProvider, Provider, run_analysis_worker
from .working_memory import WorkingMemoryStore
from .workspace import DryRunWriter, WorkspaceWriter, run_write

# R7.1: the selective-validation policy value for ``independent_validation`` — validate
# only when the task's classification requires it (see independent_validation_required).
AUTO_VALIDATION = "auto"


def render_response(agent_output: dict[str, Any]) -> str:
    """Render a human-readable final response from a validated Agent Output."""
    rso = agent_output.get("role_specific_output", {})
    lines = [f"# {agent_output.get('goal', 'Analysis')}", "", agent_output.get("summary", ""), ""]
    findings = rso.get("key_findings", [])
    if findings:
        lines.append("## Key findings")
        lines += [f"- {f}" for f in findings]
        lines.append("")
    rec = agent_output.get("recommendation")
    if rec:
        lines += ["## Recommendation", f"{rec['action']} — {rec['reason']}", ""]
    if agent_output.get("uncertainty"):
        lines += ["## Uncertainty", *[f"- {u}" for u in agent_output["uncertainty"]], ""]
    lines.append("_Read-only analysis; automatically validated, not independently verified._")
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


def run_task(
    raw_request: str,
    *,
    provider: Provider | None = None,
    search_tool: SearchTool | None = None,
    working_memory: WorkingMemoryStore | None = None,
    now: str | None = None,
    repo_root: Path | None = None,
    store: LedgerStore | None = None,
    independent_validation: bool | str = False,
    validator_provider: Provider | None = None,
    write_path: str | None = None,
    writer: WorkspaceWriter | None = None,
    **intake_kwargs: Any,
) -> dict[str, Any]:
    """Run one task end-to-end. Returns a structured result; never raises for a
    fail-closed condition (those become ``status == "BLOCKED"``). Pass ``store`` to
    persist the records + audit trail durably (the CLI does).

    ``search_tool`` runs a read-only web search whose hits become source-attributed
    evidence on the output (default ``MockSearchTool`` — deterministic, no network; a real
    network tool is chosen via the Safety-Flag Gate by the caller).

    ``working_memory`` (opt-in) makes prior working-memory candidates available as context
    and accumulates this run's candidates for later runs. Omitting it keeps the run pure and
    deterministic — memory only accumulates when a caller supplies the store.

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
        task = build_task(
            raw_request, now=now,
            planned_agents=2 if (auto_policy or independent_validation) else 1,
            planned_triage_calls=1 if auto_policy else 0,
            **intake_kwargs,
        )
        received_task = task
        records["received_task"] = task

        plan = plan_task(
            task, now=now, repo_root=repo_root,
            independent_validation=AUTO_VALIDATION if auto_policy else independent_validation,
            controlled_write=write_path is not None,
        )
        records.update({
            "task": plan["task"], "binding": plan["binding"],
            "permission_decision": plan["permission_decision"],
            "search_permission_decision": plan["search_permission_decision"],
            "role_assignment": plan["role_assignment"],
        })
        if "validator_assignment" in plan:
            records["validator_permission_decision"] = plan["validator_permission_decision"]
            records["validator_assignment"] = plan["validator_assignment"]
        if write_path is not None:
            # Persist the grant that authorizes the write, not just the audit event that
            # reports it: the ledger must hold the decision the action was taken under.
            records["write_permission_decision"] = plan["write_permission_decision"]

        # R7.2: settle whether the planned reviewer RUNS. The classification decides
        # first (an important priority or ORANGE/RED risk = no triage owed); otherwise
        # the orchestrator's governed triage call judges the request itself.
        triage_result = triage_invocation = None
        if auto_policy:
            triage_permdec = plan.get("triage_permission_decision")
            if triage_permdec is None:
                validate_run = True     # classification already required the review
            else:
                records["triage_permission_decision"] = triage_permdec
                triage_result, triage_invocation = run_triage(
                    plan["task"], provider=triage_provider, created_at=now,
                )
                records["triage_result"] = triage_result
                if triage_invocation is not None:
                    records["triage_invocation"] = triage_invocation
                validate_run = triage_result["verdict"] == VERDICT_HIGH
        else:
            validate_run = bool(independent_validation)

        # R3: run the authorized read-only search (mock by default; gated real tool).
        # Its hits become source-attributed evidence; the use is recorded + audited.
        query = plan["task"].get("request", {}).get("normalized_goal") or raw_request
        search_hits, tool_use = run_search(query, tool=search_tool, now=now)
        records["tool_use"] = tool_use

        # R5: retrieve prior working-memory candidates as context (opt-in; read-only, scoped).
        # A corrupt store fails closed here (BLOCK), like the ledger.
        memory_entries = (
            retrieve_working_memory(plan["role_assignment"], working_memory, now=now)
            if working_memory is not None else []
        )
        records["memory_retrieved"] = memory_entries

        agent_output, invocation = run_analysis_worker(
            plan["task"], plan["role_assignment"], provider=provider, created_at=now,
            search_hits=search_hits, memory_entries=memory_entries, repo_root=repo_root,
        )
        records["agent_output"] = agent_output
        records["invocation"] = invocation

        validation = validate_agent_output(agent_output, plan["task"], plan["role_assignment"], now=now, repo_root=repo_root)
        records["validation_result"] = validation

        # R7 (opt-in): the independent validator reviews the output in a fresh context.
        # Skipped when the automatic checks already BLOCK — the outcome is decided and a
        # model call would be spent for nothing. The stricter result decides delivery.
        independent_validation_result = validator_invocation = None
        if validate_run and validation["validation"]["result"] != "BLOCK":
            independent_validation_result, validator_invocation = run_validation_worker(
                plan["task"], plan["validator_assignment"], agent_output,
                provider=validator_provider, created_at=now, repo_root=repo_root,
            )
            records["independent_validation_result"] = independent_validation_result
            records["validator_invocation"] = validator_invocation

        outcome = validation["validation"]["result"]
        if independent_validation_result is not None:
            outcome = stricter_result(outcome, independent_validation_result["validation"]["result"])

        # R8 (opt-in): the controlled write — the runtime's first EXECUTE_AND_REPORT action.
        # Only a PASSING result is written: a rejected analysis must not leave an artifact
        # behind, so the write is gated on the same stricter outcome that gates delivery.
        # A write failure fails the run closed (BLOCKED) rather than delivering a result
        # that claims a file exists when it does not.
        write_use = None
        if write_path is not None and outcome == "PASS":
            _write_result, write_use = run_write(
                write_path, render_response(agent_output),
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

        # What the run actually spent, against the allocation it ran under. The task and
        # assignment records are allocations built before execution, so their zeroed usage
        # can never answer this — the contract's usage_must_be_recorded_for_audit invariant
        # had no record satisfying it until this one.
        records["budget_usage"] = recorded_usage_budget(
            plan["task"].get("execution_budget", {}).get("limits", {}),
            agent_invocations=2 if validator_invocation is not None else 1,
            # The R7.2 triage is Prime's model call, not an agent's — it counts toward
            # model_calls/tokens but never toward agent_invocations.
            model_calls=(2 if validator_invocation is not None else 1)
                        + (1 if triage_invocation is not None else 0),
            tokens_used=(
                int(invocation.get("tokens_used", 0))
                + int((validator_invocation or {}).get("tokens_used", 0))
                + int((triage_invocation or {}).get("tokens_used", 0))
            ),
            validation_cycles=2 if independent_validation_result is not None else 1,
            retry_count=(
                int(invocation.get("retry_count", 0))
                + int((validator_invocation or {}).get("retry_count", 0))
            ),
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
        # but does not withhold a delivered, durably-audited result.
        if working_memory is not None:
            try:
                working_memory.append(agent_output.get("memory_candidates", []))
            except PersistenceError as exc:
                result.setdefault("working_memory_error", exc.reason_code)
        result["status"] = "COMPLETED"
        result["delivered"] = True
        result["final_response"] = render_response(agent_output)
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
