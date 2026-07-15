"""R2.7 Single-Agent End-to-End pipeline.

``run_task`` wires the whole MVP together for one user request:

    intake -> plan (classify/bind/permission/assign) -> worker (model) ->
    output validation -> audit trail -> final response

It returns a uniform structured result. A normal task COMPLETES and delivers a
rendered response; any fail-closed condition (invalid input, planning/binding/permission
failure, provider error/timeout, budget breach, invalid output) yields a BLOCKED result
with a reason instead of raising. A validation result of REVISE/BLOCK also withholds
delivery with the validator's reasons. The run performs no external write, no network
(with the default MockProvider), and no tool/program execution.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit import build_pipeline_audit
from .errors import MvpRuntimeError
from .intake import build_task
from .prime import plan_task
from .validation import validate_agent_output
from .worker import MockProvider, Provider, run_analysis_worker


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def run_task(
    raw_request: str,
    *,
    provider: Provider | None = None,
    now: str | None = None,
    repo_root: Path | None = None,
    **intake_kwargs: Any,
) -> dict[str, Any]:
    """Run one task end-to-end. Returns a structured result; never raises for a
    fail-closed condition (those become ``status == "BLOCKED"``)."""
    provider = provider if provider is not None else MockProvider()
    now = now if now is not None else _utc_now_iso()
    result: dict[str, Any] = {
        "status": "BLOCKED",
        "delivered": False,
        "now": now,
        "final_response": None,
        "block": None,
        "records": {},
    }
    records = result["records"]
    try:
        task = build_task(raw_request, now=now, **intake_kwargs)
        records["received_task"] = task

        plan = plan_task(task, now=now, repo_root=repo_root)
        records.update({
            "task": plan["task"], "binding": plan["binding"],
            "permission_decision": plan["permission_decision"], "role_assignment": plan["role_assignment"],
        })

        agent_output, invocation = run_analysis_worker(
            plan["task"], plan["role_assignment"], provider=provider, created_at=now, repo_root=repo_root
        )
        records["agent_output"] = agent_output
        records["invocation"] = invocation

        validation = validate_agent_output(agent_output, plan["task"], plan["role_assignment"], now=now, repo_root=repo_root)
        records["validation_result"] = validation

        records["audit_trail"] = build_pipeline_audit(
            plan["task"], plan["permission_decision"], validation, now=now, repo_root=repo_root
        )
    except MvpRuntimeError as exc:
        result["block"] = {"stage": "pipeline", "reason_code": exc.reason_code, "message": exc.reason}
        return result

    outcome = validation["validation"]["result"]
    if outcome == "PASS":
        result["status"] = "COMPLETED"
        result["delivered"] = True
        result["final_response"] = render_response(agent_output)
    else:
        result["status"] = "BLOCKED"
        result["block"] = {
            "stage": "validation",
            "reason_code": f"VALIDATION_{outcome}",
            "message": "; ".join(validation["validation"]["result_reasons"]),
        }
    return result
