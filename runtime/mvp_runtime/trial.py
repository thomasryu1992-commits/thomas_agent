"""Candidate Role Trial — request, and (once Thomas approved) run, one isolated trial.

The Candidate Trial Policy (MVP_DYNAMIC_ROLE_CONTRACT §14, mirrored in every candidate
Role Definition) allows a non-routable candidate role to run exactly once when ALL of:
explicit Thomas approval, the exact candidate role version, an explicit ``candidate_trial``
assignment, an isolated trial context, no external action, no persistent runtime change, a
numeric execution budget, independent validation, and a full audit record. This module is
that policy made executable, out of existing parts:

- **The ask** (:func:`request_trial`) is an R9 approval request: a ``CANDIDATE_ROLE_TRIAL``
  APPROVAL_REQUIRED PermissionDecision whose fingerprint binds the exact role id, role
  version, definition hash, AND trial task text. Thomas answers ``/approve``/``/reject``
  on the verified control channel exactly as for a memory promotion.
- **The spend** (:func:`run_trial`) is an R10-style consumption: single-use, hot-path
  revalidated (the role definition and trial text must still hash to what Thomas saw),
  kill-switch bound, and gated behind the same ``approval_consumption`` safety flag —
  an APPROVED trial authorizes nothing until deliberately spent on a gated machine.
  The grant is marked CONSUMED **before** the trial executes, so a partial failure leaves
  it spent-but-unrun (ask again), never re-spendable.
- **The run** reuses the R2 pipeline parts under trial constraints: the candidate role is
  selected explicitly (:func:`planner.select_candidate_role` — normal routing still never
  sees candidates), the assignment is ``assignment_mode: candidate_trial`` with a closed
  memory scope, there is no search, no memory context, no workspace write, and the R7
  independent validator ALWAYS reviews the output (skipped only when the automatic checks
  already BLOCK — the trial has failed regardless). Every leg is audited onto the ledger.

A trial never activates or promotes anything: the outcome is a durable
``candidate_trial_report.v0`` record — evidence for a separate promotion decision that
remains Thomas's, per the registry's change control.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from runtime import registry_resolution
from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from . import approval as approval_mod
from . import audit, safety_gate, schema_cache, timeutil
from .approval_store import ApprovalStore
from .assignment import build_role_assignment
from .binding import bind_task_to_core
from .budgets import recorded_usage_budget
from .consumption import ENV_VAR, OPT_IN_VALUE, PROVIDER_ID
from .control import ControlStore
from .errors import ApprovalBlocked, MvpRuntimeError, PersistenceError, PlannerBlocked
from .events import stamped_event
from .filelock import locked
from .intake import build_task
from .paths import repo_root as _repo_root
from .permission import (
    MVP_TTL_MINUTES,
    TRIAL_PERMISSION_SCOPE,
    TRIAL_WORK_PERMISSION_SCOPE,
    TRIAL_WORK_REQUIRED_PERMISSION_LEVEL,
    build_trial_permission_decision,
    build_trial_work_permission_decision,
    build_validation_permission_decision,
    trial_content_sha256,
)
from .pipeline import render_response
from .planner import (
    VALIDATOR_REQUIRED_CAPABILITIES,
    VALIDATOR_REQUIRED_PERMISSION_LEVEL,
    load_resolved_roles,
    select_candidate_role,
    select_role,
)
from .prime import TASK_SCHEMA_VERSION, _apply_plan_to_task
from .safety_gate import APPROVAL_CONSUMPTION
from .store import LedgerStore
from .validation import validate_agent_output
from .validator import MockValidatorProvider, run_validation_worker, stricter_result
from .worker import Provider, ProviderResult, run_analysis_worker

from . import _scripts_bridge  # noqa: F401  (side effect: scripts/ on sys.path, once)

from lib.action_fingerprint import compute_action_fingerprint  # noqa: E402

TRIAL_WORKER_ID = "mvp.candidate_trial.llm"
TRIAL_PROMPT_VERSION = "mvp_candidate_trial.v2"
TRIAL_REPORT_RECORD_TYPE = "candidate_trial_report.v0"

_TARGET_PREFIX = "candidate_role:"


class _DryRunTrialRunner:
    """The inert runner returned when the ``approval_consumption`` flag is not opted in.

    Spending a trial grant is the same deliberate, gated action as spending a promotion
    grant — one flag governs both — so an un-gated attempt is a fail-closed BLOCK, never
    a silent no-op that looks like a trial happened.
    """

    def authorize_spend(self, *, now: str) -> None:
        raise ApprovalBlocked(
            "CONSUMPTION_DISABLED",
            "approval consumption is OFF on this machine; set "
            f"{ENV_VAR}={OPT_IN_VALUE} and activate the {PROVIDER_ID!r} safety flag "
            "(scripts/activate_safety_flag.py) to run an approved trial",
        )


class _CapableTrialRunner:
    """Built only behind the Safety-Flag Gate (it is handed the ``Authorization``, so it
    cannot exist before the gate opened). It re-verifies the grant at the moment of the
    spend — deleting the activation record is a live revocation here as everywhere."""

    def __init__(self, authorization: safety_gate.Authorization):
        self._authorization = authorization

    def authorize_spend(self, *, now: str) -> None:
        safety_gate.assert_authorization(
            self._authorization, required_flags=[APPROVAL_CONSUMPTION],
            provider_id=PROVIDER_ID, now=now,
        )


def select_trial_runner(*, now: str, root: Path) -> Any:
    """The gate chokepoint for the trial spend (mirrors ``consumption.select_consumer``)."""
    return safety_gate.select_gated(
        env_var=ENV_VAR, opt_in_value=OPT_IN_VALUE,
        flags=[APPROVAL_CONSUMPTION], provider_id=PROVIDER_ID,
        default_factory=_DryRunTrialRunner, gated_factory=_CapableTrialRunner,
        now=now, root=root,
    )


class MockTrialProvider:
    """Deterministic trial provider: no network, no real model. Returns the common
    analysis shape plus a synthesized non-empty value for each declared role output key,
    so the whole trial pipeline runs and is testable before any hosted provider."""

    model_id = "mock.trial"
    model_version = "0.1.0"
    network_egress = False  # deterministic, in-process; no outbound call

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


def _load_definition(root: Path, role: Mapping[str, Any]) -> dict[str, Any]:
    """The hash-verified full Role Definition (the resolved registry view deliberately
    carries only routing fields). One loader for the whole runtime — assignment.py uses
    the same one — so the trial cannot read a definition the registry does not vouch for."""
    try:
        return registry_resolution.load_markdown_yaml_front_matter(
            path=root / str(role["definition_path"]),
            expected_hash=role.get("definition_sha256"),
        )
    except registry_resolution.RegistryResolutionError as exc:
        raise PlannerBlocked("ROLE_DEFINITION_INVALID", str(exc)) from exc


def role_output_spec(definition: Mapping[str, Any]) -> dict[str, str]:
    """The role's declared ``role_specific_output`` contract: {field: type}. The single
    source for the trial prompt, the worker's output mapping, and the validation
    requirement — the Role Definition, not this module, owns what the role must return."""
    contract = definition.get("output_contract", {}).get("role_specific_output", {})
    if not isinstance(contract, Mapping) or not contract:
        raise PlannerBlocked(
            "NO_ROLE_OUTPUT_CONTRACT",
            f"role {definition.get('role_id')} declares no role_specific_output contract",
        )
    return {str(k): str(v) for k, v in contract.items()}


def build_trial_prompt(task: Mapping[str, Any], assignment: Mapping[str, Any],
                       definition: Mapping[str, Any]) -> str:
    """The role-generic trial prompt: the role's own purpose and output contract, the
    trial task, and the isolation constraints — never the business-analysis priorities."""
    contract = role_output_spec(definition)
    keys_desc = ", ".join(f"{key} ({kind})" for key, kind in contract.items())
    capabilities = ", ".join(assignment.get("role_scope", {}).get("assigned_capabilities", []))
    quality = ", ".join(str(item) for item in definition.get("quality_criteria", []) if item)
    quality_line = f"Quality criteria this role must satisfy: {quality}.\n" if quality else ""
    return (
        f"Candidate role trial: {definition.get('role_id')}@{definition.get('role_version')} "
        "(isolated, read-only, single run).\n"
        f"Role purpose: {definition.get('purpose', '')}\n"
        f"Assigned capabilities: {capabilities}\n"
        f"{quality_line}"
        f"Task: {task.get('scope', {}).get('primary_objective', '')}\n"
        f"Request: {task.get('request', {}).get('raw_request', '')}\n"
        "You have no tools, no web access, and no memory context in this trial. Rely only on "
        "the request text and general knowledge, and disclose what could not be verified.\n"
        f"In the SAME JSON object, additionally include these role-specific keys: {keys_desc}.\n"
        "Separate facts (with evidence) from inferences, disclose assumptions and uncertainty, "
        "and do not propose external actions."
    )


def request_trial(
    role_id: str,
    trial_request: str,
    *,
    now: str | None = None,
    ttl_minutes: int | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the records that ASK Thomas for one candidate-role trial. Performs nothing.

    Returns ``{"role", "task", "binding", "bound_task", "permission_decision",
    "approval_request"}``. The permission decision's fingerprint binds the exact role
    version + definition hash + trial task text, so the eventual approval cannot be spent
    on anything Thomas did not see. Fails closed (``PlannerBlocked``/``ApprovalBlocked``)
    on an unknown/active role or an invalid request. The caller persists the decision and
    request to the approval store and audits the ask (the CLI does).
    """
    now = now or timeutil.utc_now_iso()
    root = repo_root if repo_root is not None else _repo_root()

    resolved = load_resolved_roles(root)
    role = select_candidate_role(resolved, role_id=role_id)

    # The ask anchors to a real, bound task (the approval_cli precedent) — the trial task
    # text itself rides in the action fingerprint and is re-supplied at run time from the
    # approved snapshot, never from the caller.
    task = build_task(
        f"후보 역할 트라이얼 검토: {role_id}@{role['version']}",
        now=now, channel="manual", requester_type="real_thomas", requester_id="Thomas",
        authenticated=True, repo_root=root,
    )
    binding, bound = bind_task_to_core(task, repo_root=root, now=now)
    permission_decision = build_trial_permission_decision(
        bound, role, trial_request=trial_request, now=now, repo_root=root,
    )
    approval_request = approval_mod.build_approval_request(
        permission_decision, now=now, ttl_minutes=ttl_minutes, repo_root=root,
    )
    return {
        "role": role,
        "task": task,
        "binding": binding,
        "bound_task": bound,
        "permission_decision": permission_decision,
        "approval_request": approval_request,
    }


def _parse_target(target_ref: str) -> tuple[str, str]:
    if not target_ref.startswith(_TARGET_PREFIX) or "@" not in target_ref:
        raise ApprovalBlocked(
            "TARGET_NOT_CANDIDATE_ROLE",
            f"approval target {target_ref!r} is not a candidate role",
        )
    role_id, _, version = target_ref[len(_TARGET_PREFIX):].partition("@")
    if not role_id or not version:
        raise ApprovalBlocked(
            "TARGET_NOT_CANDIDATE_ROLE",
            f"approval target {target_ref!r} does not name role_id@version",
        )
    return role_id, version


def _plan_trial_run(
    root: Path,
    role: Mapping[str, Any],
    definition: Mapping[str, Any],
    trial_request: str,
    approval_id: str,
    *,
    now: str,
) -> dict[str, Any]:
    """Plan the isolated trial run (records only; nothing executes here).

    Mirrors ``prime.plan_task`` under the trial constraints: the candidate role is
    pre-selected (no capability routing — candidates are not routable), the work runs
    under its own ALLOW decision at P2, the assignment is ``candidate_trial`` mode bound
    to the consumed approval, and the independent validator is ALWAYS planned.
    """
    task = build_task(
        trial_request, now=now, channel="manual", requester_type="real_thomas",
        requester_id="Thomas", authenticated=True, planned_agents=2, repo_root=root,
    )
    _, bound = bind_task_to_core(task, repo_root=root, now=now)

    decision = {
        "classification": {
            "classification_status": "CLASSIFIED",
            "execution_mode": "AGENT",
            "complexity": "NORMAL",
            "priority": "NORMAL",
            "risk_level": "GREEN",
            "classification_reasons": [
                "candidate_role_trial",
                "isolated_read_only_trial_execution",
            ],
        },
        "authority": {
            "required_permission_level": TRIAL_WORK_REQUIRED_PERMISSION_LEVEL,
            "authority_reason": "Isolated candidate-role trial: internal read-only analysis "
                                "within the Thomas-approved trial scope.",
        },
        "required_capabilities": list(role.get("capabilities", [])),
        "permission_scope": TRIAL_WORK_PERMISSION_SCOPE,
    }

    work_permdec = build_trial_work_permission_decision(
        bound, role_permission_ceiling=role["permission_ceiling"], now=now, repo_root=root,
    )
    expires_at = timeutil.plus_minutes(now, MVP_TTL_MINUTES)
    assignment = build_role_assignment(
        bound, role, work_permdec,
        required_capabilities=list(role.get("capabilities", [])),
        created_at=now, expires_at=expires_at, repo_root=root,
        assignment_mode="candidate_trial", trial_authorization_ref=approval_id,
    )

    # Independent validation is a trial REQUIREMENT, not an option: the reviewer is
    # always planned, from the same active validator role every governed run uses.
    resolved = load_resolved_roles(root)
    validator_role = select_role(
        resolved,
        required_capabilities=VALIDATOR_REQUIRED_CAPABILITIES,
        required_permission_level=VALIDATOR_REQUIRED_PERMISSION_LEVEL,
    )
    validator_permdec = build_validation_permission_decision(
        bound, role_permission_ceiling=validator_role["permission_ceiling"], now=now, repo_root=root,
    )
    validator_assignment = build_role_assignment(
        bound, validator_role, validator_permdec,
        required_capabilities=list(VALIDATOR_REQUIRED_CAPABILITIES),
        created_at=now, expires_at=expires_at, repo_root=root,
    )

    planned = _apply_plan_to_task(
        bound, decision, role, work_permdec, assignment,
        now=now, validator_assignment=validator_assignment,
    )
    schema_path = root / "schemas" / f"{TASK_SCHEMA_VERSION}.schema.json"
    try:
        schema_cache.validate_against_schema(planned, schema_path, "planned_task")
    except RuntimeSchemaError as exc:
        raise PlannerBlocked("PLANNED_TASK_INVALID", str(exc)) from exc

    return {
        "task": planned,
        "received_task": task,
        "permission_decision": work_permdec,
        "role_assignment": assignment,
        "validator_role": validator_role,
        "validator_permission_decision": validator_permdec,
        "validator_assignment": validator_assignment,
    }


def _trial_report(
    role: Mapping[str, Any], approval_id: str, planned_task: Mapping[str, Any],
    trial_request: str, required_keys: list[str],
    automatic_result: str, independent_result: str | None, final_result: str,
    *, now: str,
) -> dict[str, Any]:
    """The durable trial verdict — evidence for a later promotion decision, never an
    activation. Self-hashed like every standalone ledger record."""
    return stamped_event(
        TRIAL_REPORT_RECORD_TYPE,
        role_id=role.get("role_id"),
        role_version=role.get("version"),
        definition_sha256=role.get("definition_sha256"),
        approval_id=approval_id,
        task_id=planned_task["identity"]["task_id"],
        trace_id=planned_task["identity"]["trace_id"],
        trial_request_sha256=integrity.sha256_record({"trial_request": trial_request}),
        required_role_output_keys=list(required_keys),
        automatic_result=automatic_result,
        independent_result=independent_result,
        final_result=final_result,
        isolation={
            "external_action": False,
            "live_search": False,
            "memory_reads": 0,
            "memory_candidates_created": 0,
            "workspace_write": False,
            "persistent_runtime_change": False,
        },
        promotion_effect="NONE",
        created_at=now,
    )


def run_trial(
    approval_id: str,
    *,
    provider: Provider | None = None,
    validator_provider: Provider | None = None,
    approval_store: ApprovalStore | None = None,
    ledger: LedgerStore | None = None,
    control_store: ControlStore | None = None,
    now: str | None = None,
    repo_root: Path | None = None,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Spend one APPROVED CANDIDATE_ROLE_TRIAL grant to run its bound trial exactly once.

    Everything BEFORE the spend raises fail-closed (``ApprovalBlocked``/``PlannerBlocked``)
    and leaves the grant untouched: unknown/expired/not-APPROVED approval, missing bound
    decision, fingerprint or role-definition drift, kill switch, gate off. Once the grant
    is marked CONSUMED (compare-and-set under the cross-process consume lock, BEFORE the
    model runs — a partial failure leaves it spent-but-unrun, the safe direction), the
    outcome is always returned as a structured result: ``status`` COMPLETED/BLOCKED,
    ``approval`` (the CONSUMED record), ``records`` (incl. the trial report), and
    ``persist_error`` honesty exactly like ``pipeline.run_task``.

    ``runner`` is a test seam (mirroring ``consumption.consume_approval``'s ``consumer``);
    production callers leave it None so the spend goes through the Safety-Flag Gate.
    ``provider`` defaults to the deterministic :class:`MockTrialProvider` — a hosted
    provider is the caller's (gated) choice, exactly as in the main CLI.
    """
    now = now or timeutil.utc_now_iso()
    root = repo_root if repo_root is not None else _repo_root()
    approval_store = approval_store or ApprovalStore.default()
    ledger = ledger or LedgerStore.default()

    # kill_blocks: new_execution — a PAUSED or KILLED runtime must not start a trial.
    control = control_store if control_store is not None else ControlStore(root)
    state = control.load()
    if not state.execution_allowed:
        raise ApprovalBlocked(
            state.refusal_reason_code(),
            f"runtime is {state.mode}; kill_blocks forbids running a trial",
        )

    approval_rec = approval_store.get(approval_id)
    if approval_rec is None:
        raise ApprovalBlocked("UNKNOWN_APPROVAL", f"no approval with id {approval_id}")
    status = approval_rec.get("status")
    if status == approval_mod.STATUS_CONSUMED:
        raise ApprovalBlocked("ALREADY_CONSUMED", "approval has already been consumed (one-time use)")
    if status != approval_mod.STATUS_APPROVED:
        raise ApprovalBlocked(
            "NOT_APPROVED", f"only an APPROVED approval can be consumed; this one is {status}"
        )
    if approval_mod.is_expired(approval_rec, now=now):
        raise ApprovalBlocked(
            "APPROVAL_EXPIRED",
            f"approval expired at {approval_rec['validity']['expires_at']}; it can no longer be consumed",
        )
    permission_decision = approval_store.get_permission_decision(approval_rec["permission_decision_id"])
    if permission_decision is None:
        raise ApprovalBlocked(
            "PERMISSION_DECISION_MISSING",
            f"the decision {approval_rec['permission_decision_id']} this approval binds to is not on record",
        )

    snapshot = approval_rec["approved_action_snapshot"]
    # Hot-path revalidation 1: the snapshot must still fingerprint to the bound value.
    try:
        recomputed_fp = compute_action_fingerprint(snapshot)
    except ValueError as exc:
        raise ApprovalBlocked("FINGERPRINT_UNCOMPUTABLE", str(exc)) from exc
    if recomputed_fp != approval_rec.get("action_fingerprint"):
        raise ApprovalBlocked(
            "FINGERPRINT_MISMATCH",
            "the approved action no longer fingerprints to its recorded value; the trial is refused",
        )
    if snapshot.get("permission_scope") != TRIAL_PERMISSION_SCOPE:
        raise ApprovalBlocked(
            "SCOPE_NOT_CONSUMABLE",
            f"scope {snapshot.get('permission_scope')} is not a candidate-role trial",
        )
    role_id, version = _parse_target(str(snapshot.get("target_ref", "")))
    trial_request = (snapshot.get("normalized_parameters") or {}).get("trial_request")
    if not (isinstance(trial_request, str) and trial_request.strip()):
        raise ApprovalBlocked(
            "TRIAL_REQUEST_MISSING", "the approved snapshot carries no trial task text"
        )

    # Hot-path revalidation 2: the CURRENT registry entry must still be the exact
    # candidate Thomas approved — same version, same definition bytes, same task text.
    # Registry resolution itself fails closed on a definition-hash mismatch, and an
    # activated/retired role no longer selects as a candidate.
    resolved = load_resolved_roles(root)
    role = select_candidate_role(resolved, role_id=role_id, version=version)
    if trial_content_sha256(role, trial_request) != snapshot.get("content_sha256"):
        raise ApprovalBlocked(
            "CONTENT_CHANGED",
            "the role definition or trial task changed since Thomas approved; the trial is refused",
        )
    definition = _load_definition(root, role)
    output_spec = role_output_spec(definition)
    required_keys = list(output_spec)

    # Plan the run before spending: planning is free and deterministic (records only),
    # and a plan that cannot even be built must not cost the grant.
    plan = _plan_trial_run(root, role, definition, trial_request, approval_id, now=now)
    planned_task = plan["task"]
    task_id = planned_task["identity"]["task_id"]

    # The gate: same flag, same activation record, same live-revocation semantics as the
    # memory-promotion spend. An env var alone runs nothing.
    if runner is None:
        runner = select_trial_runner(now=now, root=root)

    try:
        genesis = ledger.last_audit_hash()
    except PersistenceError as exc:
        # A corrupt/unreadable ledger fails closed BEFORE the spend — no grant is burned
        # on a run whose evidence could never be recorded.
        raise ApprovalBlocked(
            "LEDGER_UNAVAILABLE", f"cannot read the audit ledger tip ({exc.reason_code}); the trial is refused"
        ) from exc

    # Single-use compare-and-set under the cross-process consume lock, then SPEND FIRST:
    # the CONSUMED record and its audit event are built before anything is written, and
    # the grant is durably spent before the model runs (consumption.py's ordering).
    with locked(approval_store.root / ".consume.lock",
                code="APPROVAL_WRITE_FAILED", label="the approval store"):
        latest = approval_store.get(approval_id)
        if latest is None or latest.get("status") != approval_mod.STATUS_APPROVED:
            raise ApprovalBlocked(
                "ALREADY_CONSUMED", "approval is no longer APPROVED (a concurrent consume won); refusing"
            )
        runner.authorize_spend(now=now)
        consumed = approval_mod.build_consumed_record(
            approval_rec, permission_decision,
            consumed_at=now, consumption_ref=f"trial_task:{task_id}", repo_root=root,
        )
        consumption_audit = audit.build_trial_consumption_audit(
            consumed, trial_task_id=task_id, now=now,
            genesis_previous_hash=genesis, repo_root=root,
        )
        approval_store.append([consumed])

    result: dict[str, Any] = {
        "status": "BLOCKED",
        "delivered": False,
        "now": now,
        "final_response": None,
        "block": None,
        "persist_error": None,
        "approval": consumed,
        "records": {},
    }
    records = result["records"]
    records.update({
        "received_task": plan["received_task"],
        "task": planned_task,
        "permission_decision": plan["permission_decision"],
        "role_assignment": plan["role_assignment"],
        "validator_permission_decision": plan["validator_permission_decision"],
        "validator_assignment": plan["validator_assignment"],
    })
    trace_id = planned_task["identity"]["trace_id"]

    if provider is None:
        provider = MockTrialProvider(output_spec)
    if validator_provider is None:
        validator_provider = (
            MockValidatorProvider() if isinstance(provider, MockTrialProvider) else provider
        )

    try:
        prompt = build_trial_prompt(planned_task, plan["role_assignment"], definition)
        agent_output, invocation = run_analysis_worker(
            planned_task, plan["role_assignment"], provider=provider, created_at=now,
            repo_root=root, prompt_override=prompt, role_output_keys=required_keys,
            worker_id=TRIAL_WORKER_ID, prompt_version=TRIAL_PROMPT_VERSION,
        )
        records["agent_output"] = agent_output
        records["invocation"] = invocation

        validation = validate_agent_output(
            agent_output, planned_task, plan["role_assignment"], now=now, repo_root=root,
            required_role_output_keys=required_keys,
        )
        records["validation_result"] = validation

        # The independent review is a trial requirement. It is skipped only when the
        # automatic checks already BLOCK — the trial has failed and a model call would
        # be spent on a decided outcome (the R7 precedent).
        independent_validation_result = validator_invocation = None
        if validation["validation"]["result"] != "BLOCK":
            independent_validation_result, validator_invocation = run_validation_worker(
                planned_task, plan["validator_assignment"], agent_output,
                provider=validator_provider, created_at=now, repo_root=root,
            )
            records["independent_validation_result"] = independent_validation_result
            records["validator_invocation"] = validator_invocation

        outcome = validation["validation"]["result"]
        if independent_validation_result is not None:
            outcome = stricter_result(outcome, independent_validation_result["validation"]["result"])

        records["budget_usage"] = recorded_usage_budget(
            planned_task.get("execution_budget", {}).get("limits", {}),
            agent_invocations=2 if validator_invocation is not None else 1,
            model_calls=2 if validator_invocation is not None else 1,
            tokens_used=(
                int(invocation.get("tokens_used", 0))
                + int((validator_invocation or {}).get("tokens_used", 0))
            ),
            validation_cycles=2 if independent_validation_result is not None else 1,
            retry_count=(
                int(invocation.get("retry_count", 0))
                + int((validator_invocation or {}).get("retry_count", 0))
            ),
        )
        records["trial_report"] = _trial_report(
            role, approval_id, planned_task, trial_request, required_keys,
            validation["validation"]["result"],
            (independent_validation_result or {}).get("validation", {}).get("result"),
            outcome, now=now,
        )
        records["audit_trail"] = consumption_audit + audit.build_pipeline_audit(
            planned_task, plan["permission_decision"], validation, agent_output, invocation,
            now=now,
            independent_validation_result=independent_validation_result,
            validator_invocation=validator_invocation,
            validator_permission_decision=plan["validator_permission_decision"],
            genesis_previous_hash=genesis, repo_root=root,
        )
    except MvpRuntimeError as exc:
        # The grant is already durably spent; report the truth and audit what happened.
        result["block"] = {"stage": "trial_pipeline", "reason_code": exc.reason_code, "message": exc.reason}
        try:
            blocked_trail = consumption_audit + audit.build_blocked_audit(
                planned_task, stage="trial_pipeline", reason_code=exc.reason_code, now=now,
                genesis_previous_hash=genesis, repo_root=root,
            )
            ledger.append_records(trace_id, records)
            ledger.append_audit_events(blocked_trail)
        except MvpRuntimeError as secondary:
            result["persist_error"] = secondary.reason_code
        return result

    # Persist fail-closed: a trial whose evidence is not durable is not a delivered trial.
    try:
        ledger.append_records(trace_id, records)
        ledger.append_audit_events(records["audit_trail"])
    except PersistenceError as exc:
        result["block"] = {"stage": "persistence", "reason_code": exc.reason_code, "message": exc.reason}
        result["persist_error"] = exc.reason_code
        return result

    header = (
        f"# Candidate role trial: {role.get('role_id')}@{role.get('version')}\n"
        f"Final result: {outcome} (automatic {validation['validation']['result']}"
        + (f", independent {independent_validation_result['validation']['result']}"
           if independent_validation_result is not None else ", independent —")
        + ")\n\n"
    )
    if outcome == "PASS":
        result["status"] = "COMPLETED"
        result["delivered"] = True
        result["final_response"] = header + render_response(
            agent_output,
            independently_validated=independent_validation_result is not None,
        )
    else:
        reasons = list(validation["validation"]["result_reasons"])
        if independent_validation_result is not None:
            reasons.extend(independent_validation_result["validation"]["result_reasons"])
        result["block"] = {
            "stage": "validation",
            "reason_code": f"VALIDATION_{outcome}",
            "message": "; ".join(dict.fromkeys(reasons)),
        }
    return result
