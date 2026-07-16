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
from typing import Any

from runtime.read_only_kernel import integrity

from . import timeutil
from .audit import build_blocked_audit, build_pipeline_audit
from .errors import MvpRuntimeError, PersistenceError
from .intake import build_task
from .memory import retrieve_working_memory
from .prime import plan_task
from .store import LedgerStore
from .tools import MockSearchTool, SearchTool, run_search
from .validation import validate_agent_output
from .worker import MockProvider, Provider, run_analysis_worker
from .working_memory import WorkingMemoryStore


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
    return {
        "record_type": "run_block.v0",
        "stage": stage,
        "reason_code": reason_code,
        "message": message,
        "request_sha256": integrity.sha256_record({"raw_request": raw_request}),
        "trace_id": trace,
        "created_at": now,
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
        result["block"]["persist_error"] = secondary.reason_code
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
    deterministic — memory only accumulates when a caller supplies the store."""
    provider = provider if provider is not None else MockProvider()
    search_tool = search_tool if search_tool is not None else MockSearchTool()
    now = now if now is not None else timeutil.utc_now_iso()
    result: dict[str, Any] = {
        "status": "BLOCKED",
        "delivered": False,
        "now": now,
        "final_response": None,
        "block": None,
        "records": {},
    }
    records = result["records"]

    # Chain this run's audit onto the ledger tip. A corrupt/unreadable ledger fails closed.
    try:
        genesis = store.last_audit_hash() if store is not None else None
    except PersistenceError as exc:
        result["block"] = {"stage": "persistence", "reason_code": exc.reason_code, "message": exc.reason}
        return result

    received_task: dict[str, Any] | None = None
    try:
        task = build_task(raw_request, now=now, **intake_kwargs)
        received_task = task
        records["received_task"] = task

        plan = plan_task(task, now=now, repo_root=repo_root)
        records.update({
            "task": plan["task"], "binding": plan["binding"],
            "permission_decision": plan["permission_decision"],
            "search_permission_decision": plan["search_permission_decision"],
            "role_assignment": plan["role_assignment"],
        })

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

        records["audit_trail"] = build_pipeline_audit(
            plan["task"], plan["permission_decision"], validation, agent_output, invocation,
            now=now, tool_use=tool_use, search_permission_decision=plan["search_permission_decision"],
            genesis_previous_hash=genesis, repo_root=repo_root,
        )
    except MvpRuntimeError as exc:
        return _finalize_block(
            result, records, exc, raw_request=raw_request, received_task=received_task,
            now=now, genesis=genesis, store=store, repo_root=repo_root,
        )

    outcome = validation["validation"]["result"]
    if outcome == "PASS":
        # Fail-closed on persistence: a completed run with no durable audit is not delivered.
        if store is not None:
            try:
                _persist(store, records)
            except PersistenceError as exc:
                result["block"] = {"stage": "persistence", "reason_code": exc.reason_code, "message": exc.reason}
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
        result["status"] = "BLOCKED"
        result["block"] = {
            "stage": "validation",
            "reason_code": f"VALIDATION_{outcome}",
            "message": "; ".join(validation["validation"]["result_reasons"]),
        }
    return result
