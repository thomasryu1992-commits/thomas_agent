"""R2.2 PermissionDecision (governance step).

Build an immutable ``permission_decision.v0.4`` record for a bound task and have
governance judge it. The MVP mints five action specs across three dispositions: the
ALLOW tier (the specialist's ``INTERNAL_ANALYSIS``, the R3 read-only search's
``INTERNAL_READ``, the R7 validator's ``SIMULATION_VALIDATION``), the R8 workspace
write (``WORKSPACE_REVERSIBLE_WRITE``, EXECUTE_AND_REPORT), and the R9 memory promotion
(``SENSITIVE_MEMORY_GOVERNANCE``, APPROVAL_REQUIRED). Each record is validated twice —
against the closed schema and against the canonical Governance Policy semantics
(``validate_permission_record``) — and any issue fails closed. No decision is ever an
executor token: ``runtime_effect`` stays REVIEW_ONLY with every grant flag false, and
only implemented EXECUTE_AND_REPORT scopes are executable (APPROVAL_REQUIRED is
buildable for the one approval-flow scope; BLOCK stays refused).

The actions differ in their action-identity fields (scope, action_type, target, tool,
data scope, parameters) and human-readable reasons; the governance evaluation,
authority invariant, and REVIEW_ONLY guarantee are identical and handled once. A
read-only search is modelled as an ``INTERNAL_READ`` ALLOW action at P1 (READ) —
NOT a ``tool_request`` (that contract is an executor-handoff review packet, the wrong
shape for an internal read).

Reuses scripts helpers in-process: ``compute_action_fingerprint`` (action identity),
``validate_permission_record`` / ``scope_policy_map`` / ``POLICY_BINDING`` (governance
evaluation). scripts/ is added to sys.path (localized bridge) as in binding.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import yaml

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from . import schema_cache
from . import timeutil
from .authority import authority_invariant_holds, permission_decision_runtime_effect, rank_of
from .errors import PlannerBlocked
from .paths import repo_root as _repo_root
from .tools import SEARCH_TOOL_ID
from .workspace import WORKSPACE_REL, WRITE_TOOL_ID

from . import _scripts_bridge  # noqa: F401  (side effect: scripts/ on sys.path, once)

from lib.action_fingerprint import compute_action_fingerprint  # noqa: E402
from validate_permission_approval_contracts import (  # noqa: E402
    POLICY_BINDING,
    scope_policy_map,
    validate_permission_record,
)

# Bumped v0.3 -> v0.4 to add exactly one action scope to the closed enum:
# FINANCIAL_APPROVED_TRADING_USE (a live exchange order). v0.4 is a strict superset of v0.3,
# so every existing decision type is still a valid v0.4 record; the v0.3 schema is kept for
# the historical example/fixture records that declare it. Additive bump, the R10 precedent
# (approval.v0.1 -> v0.2).
PERMISSION_DECISION_SCHEMA_VERSION = "permission_decision.v0.4"
MVP_TTL_MINUTES = 30

# Governance scope + least-privilege authority level for the R3 read-only search action.
SEARCH_PERMISSION_SCOPE = "INTERNAL_READ"
SEARCH_REQUIRED_PERMISSION_LEVEL = "P1"  # READ — a read-only lookup, one level below ANALYZE

# Governance scope + level for the R7 independent validation action. A distinct ALLOW-tier
# scope (SIMULATION_VALIDATION) both matches the action semantically and keeps the validator's
# permission_decision_id distinct from the specialist's (the id seed includes the scope
# and the action_type).
VALIDATION_PERMISSION_SCOPE = "SIMULATION_VALIDATION"
VALIDATION_REQUIRED_PERMISSION_LEVEL = "P2"  # ANALYZE — read-only review of an internal output

# Governance scope + level for the R7.2 orchestrator triage: Prime's own short model call
# that judges whether a request is important enough to warrant the independent reviewer.
# It is internal analysis of the request — the same ALLOW-tier effect class as the
# specialist's analysis (a distinct action_type keeps the decision ids apart) — at P2
# ANALYZE, read-only, granting nothing.
TRIAGE_PERMISSION_SCOPE = "INTERNAL_ANALYSIS"
TRIAGE_REQUIRED_PERMISSION_LEVEL = "P2"  # ANALYZE — read-only importance assessment

# Governance scope + level for the R8 controlled write. The Governance Policy already
# prices WORKSPACE_REVERSIBLE_WRITE at EXECUTE_AND_REPORT, making this the runtime's first
# non-ALLOW action. P3 CREATE is the honest level for creating a new file: the policy's
# authority ladder is P3 CREATE / P4 INTERNAL_MODIFY, so create-only stays at P3 and within
# the specialist's ceiling. Modifying an existing file would be P4 — above that ceiling,
# and not implemented (see workspace.py: writes are create-only).
WRITE_PERMISSION_SCOPE = "WORKSPACE_REVERSIBLE_WRITE"
WRITE_REQUIRED_PERMISSION_LEVEL = "P3"  # CREATE — creates a new internal artifact

# Governance scope + level for the R9 memory-promotion action — the runtime's first
# APPROVAL_REQUIRED action. Promotion changes persistent VALIDATED memory, which Prime's
# conditional P4 explicitly excludes (THOMAS_PRIME_CHARTER §10: "Active Core, Policy,
# Validated Memory를 변경하지 않는다"), which is exactly why it needs Thomas. Prime's
# authority here is the authority to *prepare* the request for review; the decision is
# Thomas's. Mirrors examples/permission/permission_approval_required_v0.3.yaml.
MEMORY_PROMOTION_PERMISSION_SCOPE = "SENSITIVE_MEMORY_GOVERNANCE"
MEMORY_PROMOTION_REQUIRED_PERMISSION_LEVEL = "P4"  # INTERNAL_MODIFY — mutates validated memory

# Governance scope + level for the Candidate Role Trial authorization — the second
# APPROVAL_REQUIRED action (the Governance Policy prices candidate_role_trial at
# APPROVAL_REQUIRED, and the Candidate Trial Policy requires explicit Thomas approval).
# Approved by Thomas 2026-07-22: CANDIDATE_ROLE_TRIAL joins SENSITIVE_MEMORY_GOVERNANCE as
# an askable/consumable scope. P3 CREATE is the honest level: the trial creates new
# internal records (an assignment, an output, a report) and mutates nothing persistent.
TRIAL_PERMISSION_SCOPE = "CANDIDATE_ROLE_TRIAL"
TRIAL_REQUIRED_PERMISSION_LEVEL = "P3"  # CREATE — a one-shot isolated trial run + its records

# The trial run's own work is internal read-only analysis by the candidate role — the same
# ALLOW-tier effect class as the specialist's analysis, at P2 ANALYZE. A distinct
# action_type keeps its decision id apart from a normal analysis on the same task.
TRIAL_WORK_PERMISSION_SCOPE = "INTERNAL_ANALYSIS"
TRIAL_WORK_REQUIRED_PERMISSION_LEVEL = "P2"  # ANALYZE — read-only trial execution

# Governance scope + level for strategy-pool promotion (Crypto Pipeline C8b) — the third
# APPROVAL_REQUIRED action. Installing candidates into the active pool changes what the
# runtime trades, which the Governance Policy prices at APPROVAL_REQUIRED under
# RUNTIME_GOVERNANCE (already in the policy's scope list and TTL table — no policy edit).
# Approved by Thomas 2026-07-22 (crypto pipeline cutover conversation): RUNTIME_GOVERNANCE
# joins the askable scopes. P4 INTERNAL_MODIFY is the honest level: promotion REPLACES the
# active-pool pointer. Consumption stays UNIMPLEMENTED for this scope — the approved ask is
# verified (never spent) by the operator promotion door, the pre-R10 posture kept by design.
STRATEGY_PROMOTION_PERMISSION_SCOPE = "RUNTIME_GOVERNANCE"
STRATEGY_PROMOTION_REQUIRED_PERMISSION_LEVEL = "P4"  # INTERNAL_MODIFY — replaces the active pool

# Governance scope + level for Program registry registration (explicit Thomas decision
# 2026-07-22): adding a candidate entry (status: candidate, enabled: false) to the Program
# Registry. Like RUNTIME_GOVERNANCE, this scope has NO consumption implementation — an
# APPROVED registration ask is verified, never spent, by
# scripts/register_program_candidate.py (the pre-R10 operator door). P4 is the honest
# level: the registration modifies internal governance source (a registry index entry +
# its definition file); it enables and activates nothing.
REGISTRATION_PERMISSION_SCOPE = "TOOL_PROGRAM_GOVERNANCE"
REGISTRATION_REQUIRED_PERMISSION_LEVEL = "P4"  # INTERNAL_MODIFY — registry candidate entry

# Governance scope + level for a live exchange order (LP4; Thomas decision 2026-07-23, recorded
# in docs/runtime-contracts/LIVE_EXECUTION_GOVERNANCE_V0.1.md). Its OWN scope, distinct from the
# API-spend scope FINANCIAL_APPROVED_BUDGET_USE: an API invoice cannot lose more than it spends,
# a leveraged position can, so the two risks stay separately nameable, cappable, revocable, and
# auditable. Priced EXECUTE_AND_REPORT because the trading budget is approved in advance (Item 6);
# each order then executes and reports rather than asking per-order. **P5 EXTERNAL_ACTION** is the
# honest level — an order reaches a counterparty outside the system — which is exactly why it needs
# the P5 policy gate and a P5-capable actor that no ordinary role provides. Building this decision
# grants nothing: it is REVIEW_ONLY planning evidence like every other. Actually SENDING an order
# is LP4's gated adapter, which does not exist yet.
LIVE_ORDER_PERMISSION_SCOPE = "FINANCIAL_APPROVED_TRADING_USE"
LIVE_ORDER_REQUIRED_PERMISSION_LEVEL = "P5"  # EXTERNAL_ACTION — reaches a counterparty outside the system

EXECUTE_AND_REPORT = "EXECUTE_AND_REPORT"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
# Dispositions the MVP can ACT on: it has an implementation and a reporting path for each.
_EXECUTABLE_DISPOSITIONS = frozenset({"ALLOW", EXECUTE_AND_REPORT})
# Dispositions a RECORD may be built for. Building an APPROVAL_REQUIRED decision is not
# acting on it — the record is REVIEW_ONLY evidence that states an action needs Thomas, and
# it is the object an Approval Request binds to. Building the decision still performs nothing:
# an APPROVAL_REQUIRED action executes only when its APPROVED approval is later *consumed*
# (R10), a separate step gated behind the `approval_consumption` safety flag (see
# consumption.py) — never as a side effect of the decision. BLOCK is buildable ONLY as
# refusal evidence bound into a resource-request record (explicit Thomas decision
# 2026-07-22, program-request path): the Program/Tool Request contracts *require* the
# refused invocation to reference its refusing PermissionDecision, so for exactly the two
# resource-refusal scopes a BLOCK record documents "this invocation was evaluated and
# refused". Everywhere else BLOCK stays unbuildable: a BLOCK means do not, and there is
# nothing to record a request against. A BLOCK decision is never executable.
_BUILDABLE_DISPOSITIONS = frozenset({"ALLOW", EXECUTE_AND_REPORT, APPROVAL_REQUIRED, "BLOCK"})
_BLOCK_EVIDENCE_SCOPES = frozenset({"UNREGISTERED_RESOURCE_EXECUTION", "DISABLED_RESOURCE_EXECUTION"})
# The EXECUTE_AND_REPORT scopes the MVP actually implements. Kept as an explicit allowlist
# so widening the disposition gate does not silently admit the other scopes governance
# prices at EXECUTE_AND_REPORT (GIT_AGENT_BRANCH_CHANGE, LOCAL_BUILD_TEST, ...).
_EXECUTE_AND_REPORT_SCOPES = frozenset({WRITE_PERMISSION_SCOPE, LIVE_ORDER_PERMISSION_SCOPE})
# Likewise for APPROVAL_REQUIRED: only the scopes the runtime can actually ask about. The
# other APPROVAL_REQUIRED scopes (PUBLICATION, EXTERNAL_COMMUNICATION, FINANCIAL_*, ...)
# name actions the runtime has no implementation for, so a request record for one would
# assert an ask it could never honour. Refuse rather than record a fiction.
# CANDIDATE_ROLE_TRIAL joined by explicit Thomas decision (2026-07-22, research/translation
# trial rollout); its consumption implementation is trial.run_trial.
# RUNTIME_GOVERNANCE joined by explicit Thomas decision (2026-07-22, crypto pipeline C8b);
# it has NO consumption implementation — an APPROVED promotion ask is verified, never
# spent, by scripts/promote_strategy_candidates.py (the pre-R10 operator door, kept).
# TOOL_PROGRAM_GOVERNANCE joined by explicit Thomas decision (2026-07-22, program
# registration); likewise verified-never-spent, by scripts/register_program_candidate.py.
_APPROVAL_REQUIRED_SCOPES = frozenset({
    MEMORY_PROMOTION_PERMISSION_SCOPE, TRIAL_PERMISSION_SCOPE, STRATEGY_PROMOTION_PERMISSION_SCOPE,
    REGISTRATION_PERMISSION_SCOPE,
})


@dataclass(frozen=True)
class _ActionSpec:
    """The action-identity fields + reasons that distinguish one ALLOW action from
    another. Everything else about a PermissionDecision (governance evaluation, authority
    invariant, REVIEW_ONLY effect) is action-independent and built once."""

    action_type: str
    target_suffix: str            # target_ref = f"internal:{task_id}:{target_suffix}"
    tool_id: str | None
    data_scope: tuple[str, ...]
    normalized_parameters: dict[str, Any]
    risk_reason: str
    authority_reason: str
    decision_reason: str
    constraint: str
    # An action on something that is not a task-internal artifact names its own target
    # (e.g. a memory candidate) instead of the internal:{task_id}:{suffix} form.
    target_ref: str | None = None
    # A program-invocation action names the program it would invoke (resource-refusal
    # evidence); every other action leaves this None, keeping existing fingerprints stable.
    program_id: str | None = None
    # The exact content the action is bound to, when it has one. Part of the action
    # fingerprint, so the approved action cannot be swapped for a different payload.
    content_sha256: str | None = None
    # GREEN suits internal read/analysis work; an action needing Thomas is not GREEN.
    risk_level: str = "GREEN"


_ANALYSIS_ACTION = _ActionSpec(
    action_type="internal.analysis.create",
    target_suffix="analysis",
    tool_id=None,
    data_scope=("assigned.context", "task.request"),
    normalized_parameters={"output_format": "structured_markdown", "visibility": "internal"},
    risk_reason="Internal-only analysis with no external, financial, or runtime effect.",
    authority_reason="Internal analysis within the assigned Task scope and authority ceiling.",
    decision_reason="Authority is sufficient and the exact action is internal and reversible.",
    constraint="No external publication, tool/program execution, or runtime mutation.",
)

_SEARCH_ACTION = _ActionSpec(
    action_type="internal.read.search",
    target_suffix="search",
    tool_id=SEARCH_TOOL_ID,
    data_scope=("task.request", "web.public.read"),
    normalized_parameters={"result_scope": "web.public", "visibility": "internal"},
    risk_reason="Read-only public web search; no external write, publication, financial, or runtime effect.",
    authority_reason="Read-only information lookup within the assigned Task scope and authority ceiling.",
    decision_reason="Authority is sufficient and the search is a reversible, read-only information lookup.",
    constraint="Read-only search; no external write, publication, tool/program execution, or runtime mutation.",
)

_VALIDATION_ACTION = _ActionSpec(
    action_type="internal.validation.review",
    target_suffix="validation",
    tool_id=None,
    data_scope=("task.request", "agent_output.review"),
    normalized_parameters={"review_target": "agent_output", "visibility": "internal"},
    risk_reason="Read-only independent review of an internal output; no external, financial, or runtime effect.",
    authority_reason="Independent validation within the assigned Task scope and the validator role's ceiling.",
    decision_reason="Authority is sufficient and the review is a read-only assessment of an internal artifact.",
    constraint="Review only; the validator never modifies the original output and grants nothing.",
)

_TRIAGE_ACTION = _ActionSpec(
    action_type="internal.analysis.triage",
    target_suffix="triage",
    tool_id=None,
    data_scope=("task.request",),
    normalized_parameters={"assessment": "importance", "visibility": "internal"},
    risk_reason="Read-only importance assessment of the request; no external, financial, or runtime effect.",
    authority_reason="Orchestrator classification support within the assigned Task scope and authority ceiling.",
    decision_reason="Authority is sufficient and the triage is a read-only assessment of the request itself.",
    constraint="Assessment only; the verdict adds review, never removes any check, and grants nothing.",
)


_WRITE_ACTION = _ActionSpec(
    action_type="workspace.file.create",
    target_suffix="workspace_write",
    tool_id=WRITE_TOOL_ID,
    data_scope=("task.request", "workspace.internal"),
    normalized_parameters={"write_mode": "create_only", "workspace_root": WORKSPACE_REL, "visibility": "internal"},
    risk_reason="Create-only write into the approved internal workspace; no external, financial, or runtime effect.",
    authority_reason="Creating a new internal artifact within the assigned Task scope and authority ceiling.",
    decision_reason=(
        "Authority is sufficient and the write is confined, create-only, and reversible by "
        "deleting the created file; the outcome is reported."
    ),
    constraint=(
        "Create-only inside the approved workspace; never overwrites or deletes, and performs "
        "no publication, tool/program execution, or runtime mutation."
    ),
)


def build_live_order_action(
    *, symbol: str, side: str, notional_usdt: float, order_fingerprint: str
) -> "_ActionSpec":
    """The action-identity spec for one live exchange order.

    Unlike the fixed action specs, a live order's identity depends on its parameters, so this
    is built per order. The symbol, side, notional, and the order's own fingerprint go into
    ``normalized_parameters`` and ``content_sha256`` — and therefore into the action
    fingerprint — so a decision authorizing "sell 55 USDT of BTCUSDT" cannot be reused for a
    different or larger order. Notional rides in ``normalized_parameters`` rather than the
    fingerprint's ``amount_decimal``/``currency`` fields, whose currency pattern is a 3-letter
    ISO code that a stablecoin ticker (USDT) does not match.

    ``risk_level`` is RED: this is the only action in the runtime that reaches money and a
    counterparty outside the system. GREEN it is not.
    """
    return _ActionSpec(
        action_type="exchange.order.place",
        target_suffix="live_order",
        tool_id="crypto.live.order_adapter",
        data_scope=("crypto.live_order", "crypto.registered_trading_budget"),
        normalized_parameters={
            "symbol": symbol,
            "side": side,
            # A normalized decimal STRING, not a float: the action fingerprint forbids floats
            # (they do not canonicalize deterministically across languages/platforms).
            "order_notional_usdt": f"{float(notional_usdt):.2f}",
            "execution_stage": "live",
            "reduce_only": False,
        },
        risk_reason=(
            "Live exchange order: an external, financial action that reaches a counterparty "
            "outside the system and can lose more than its notional under leverage."
        ),
        authority_reason=(
            "Placing an order within a pre-registered trading budget and the P5 live-trading "
            "policy gate; the actor's ceiling must be P5 EXTERNAL_ACTION."
        ),
        decision_reason=(
            "Authority is sufficient at P5, the order is within the registered budget, and the "
            "outcome is reported; the order itself remains gated behind the live_trading "
            "safety flag and the final order guard."
        ),
        constraint=(
            "One order within the registered trading budget's caps; no budget overrun (that is "
            "FINANCIAL_NEW_COMMITMENT, APPROVAL_REQUIRED), no transfer, and no widening of the "
            "budget or the grant."
        ),
        content_sha256=order_fingerprint,
        risk_level="RED",
    )


def _parse_ts(value: str) -> datetime:
    try:
        return timeutil.parse_iso(value)
    except (AttributeError, ValueError) as exc:
        raise PlannerBlocked("INVALID_TIMESTAMP", f"now is not a valid RFC3339 date-time: {value!r}") from exc


def build_permission_decision(
    bound_task: Mapping[str, Any],
    *,
    permission_scope: str,
    required_permission_level: str,
    role_permission_ceiling: str,
    now: str,
    actor_id: str = "thomas.prime",
    ttl_minutes: int = MVP_TTL_MINUTES,
    repo_root: Path | None = None,
    action: "_ActionSpec | None" = None,
    approval_id: str | None = None,
) -> dict[str, Any]:
    """Build and fully validate a permission_decision.v0.4 for a bound task.

    ``action`` selects the action-identity fields + reasons (defaults to the internal
    analysis action). Fails closed (``PlannerBlocked``) if the task is unbound, the scope
    disposition is unknown, the authority invariant fails, the record violates the schema
    or the Governance Policy semantics, or the disposition is not ALLOW (the MVP only
    performs ALLOW-tier actions).
    """
    action = action if action is not None else _ANALYSIS_ACTION
    root = repo_root if repo_root is not None else _repo_root()
    identity = bound_task.get("identity", {})
    context = bound_task.get("context", {})
    task_id = identity.get("task_id")
    trace_id = identity.get("trace_id")
    revision = identity.get("task_revision")
    ccb = context.get("core_context_binding_id")
    if not (isinstance(ccb, str) and ccb.startswith("ccb-")):
        raise PlannerBlocked("NOT_BOUND", "task must be bound to a Core Release before a permission decision")

    if rank_of(required_permission_level) is None or rank_of(role_permission_ceiling) is None:
        raise PlannerBlocked("INVALID_LEVEL", "required level / role ceiling must be P0..P6")

    # Load the canonical Governance Policy and map the action scope to its disposition.
    try:
        policy = yaml.safe_load((root / "governance" / "GOVERNANCE_POLICY.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PlannerBlocked("POLICY_UNAVAILABLE", f"cannot load governance policy: {exc}") from exc
    disposition = scope_policy_map(policy).get(permission_scope)
    if disposition is None:
        raise PlannerBlocked("UNKNOWN_SCOPE", f"permission_scope {permission_scope!r} has no policy disposition")
    # Fail closed explicitly on anything the MVP cannot perform, before building the
    # record — do not rely on a downstream schema conditional to reject it. The MVP
    # performs ALLOW actions, the one EXECUTE_AND_REPORT action it has an implementation
    # and a reporting path for (R8; see _EXECUTABLE_DISPOSITIONS), and — since R9 — it can
    # BUILD (never execute) an APPROVAL_REQUIRED decision for the one scope the approval
    # flow serves (_APPROVAL_REQUIRED_SCOPES); acting on the grant is R10 consumption,
    # behind its own gate. BLOCK, and every unimplemented scope, stays refused.
    if disposition not in _BUILDABLE_DISPOSITIONS:
        raise PlannerBlocked(
            "NOT_ALLOWED",
            f"MVP cannot build a {disposition} decision; scope {permission_scope} disposition is {disposition}",
        )
    if disposition == EXECUTE_AND_REPORT and permission_scope not in _EXECUTE_AND_REPORT_SCOPES:
        # An EXECUTE_AND_REPORT scope the runtime has no implementation for (e.g.
        # GIT_AGENT_BRANCH_CHANGE, LOCAL_BUILD_TEST) must not ride in on R8's widening.
        raise PlannerBlocked(
            "NOT_ALLOWED",
            f"scope {permission_scope} is EXECUTE_AND_REPORT but the MVP implements no such action",
        )
    if disposition == APPROVAL_REQUIRED and permission_scope not in _APPROVAL_REQUIRED_SCOPES:
        # An APPROVAL_REQUIRED scope the runtime cannot perform even once approved must not
        # ride in on R9's widening — it could only ever produce an ask it can't honour.
        raise PlannerBlocked(
            "NOT_ALLOWED",
            f"scope {permission_scope} is APPROVAL_REQUIRED but the MVP implements no such action",
        )
    if disposition == "BLOCK" and permission_scope not in _BLOCK_EVIDENCE_SCOPES:
        # BLOCK records exist only as resource-refusal evidence (program/tool requests);
        # any other BLOCK scope stays a refusal raised, never a record built.
        raise PlannerBlocked(
            "NOT_ALLOWED",
            f"scope {permission_scope} is BLOCK; a BLOCK decision is only buildable as resource-refusal evidence",
        )

    created = _parse_ts(now)
    expires_at = timeutil.format_iso(created + timedelta(minutes=ttl_minutes))
    created_at = timeutil.format_iso(created)

    # Least privilege: required == effective == granted == the action's need; the role
    # ceiling is the upper bound the grant stays within.
    effective_level = required_permission_level
    granted_level = required_permission_level
    authority_sufficient = authority_invariant_holds(
        required_permission_level, effective_level, granted_level, role_permission_ceiling
    )
    if not authority_sufficient and disposition != "BLOCK":
        # For every performable disposition an insufficient authority refuses the build.
        # A BLOCK refusal-evidence record instead RECORDS the insufficiency: it is part of
        # why the invocation is refused, and the schema requires exactly this pairing
        # (authority_sufficient: false => permission_decision: BLOCK).
        raise PlannerBlocked(
            "AUTHORITY_INSUFFICIENT",
            f"authority invariant fails: required {required_permission_level} exceeds role ceiling {role_permission_ceiling}",
        )

    requester_ref = f"thomas_prime:{actor_id}"
    fingerprint_payload = {
        "schema_version": "action_fingerprint_payload.v0.1",
        "task_id": task_id,
        "task_revision": revision,
        "core_context_binding_id": ccb,
        "requester_ref": requester_ref,
        "permission_scope": permission_scope,
        "action_type": action.action_type,
        "target_ref": action.target_ref or f"internal:{task_id}:{action.target_suffix}",
        "tool_id": action.tool_id,
        "program_id": action.program_id,
        "data_scope": list(action.data_scope),
        "content_sha256": action.content_sha256,
        "amount_decimal": None,
        "currency": None,
        "normalized_parameters": dict(action.normalized_parameters),
        "expires_at": expires_at,
    }
    try:
        action_fingerprint = compute_action_fingerprint(fingerprint_payload)
    except ValueError as exc:
        raise PlannerBlocked("FINGERPRINT_FAILED", str(exc)) from exc

    # The Approval an APPROVAL_REQUIRED decision waits on is identified by the action
    # itself: derive its id from the action fingerprint so that ANY material change to the
    # action yields a different approval_id, exactly as the Governance Policy requires
    # (`action_identity.invalidated_by_any_material_field_change`). Deriving it here rather
    # than taking it from the caller also removes the footgun of pointing a decision at a
    # reused or unrelated approval.
    if disposition == APPROVAL_REQUIRED and approval_id is None:
        approval_id = integrity.short_id("approval", {"action_fingerprint": action_fingerprint})

    decision = disposition  # exactly the policy minimum for this scope
    # The id seed includes the action_type as well as the scope: two governed actions on
    # one task may legitimately share an effect class (R7.2's orchestrator triage is
    # INTERNAL_ANALYSIS exactly like the specialist's analysis), and "one decision per
    # action" must hold by construction, not by every scope being unique.
    permdec_id = integrity.short_id(
        "permdec",
        {"task_id": task_id, "task_revision": revision, "ccb": ccb, "scope": permission_scope,
         "action_type": action.action_type, "expires_at": expires_at},
    )

    record: dict[str, Any] = {
        "schema_version": PERMISSION_DECISION_SCHEMA_VERSION,
        "permission_decision_id": permdec_id,
        "trace_id": trace_id,
        "task_id": task_id,
        "task_revision": revision,
        "core_context_binding_id": ccb,
        "operating_policy": dict(POLICY_BINDING),
        "requested_by": {
            "actor_type": "thomas_prime",
            "actor_id": actor_id,
            "role_id": None,
            "role_version": None,
            "assignment_id": None,
        },
        "fingerprint_payload": fingerprint_payload,
        "action_fingerprint": action_fingerprint,
        "authority": {
            "required_permission_level": required_permission_level,
            "role_permission_ceiling": role_permission_ceiling,
            "assignment_granted_permission_level": granted_level,
            "effective_permission_level": effective_level,
            "authority_sufficient": bool(authority_sufficient),
            "authority_reasons": [action.authority_reason],
        },
        "risk": {
            "risk_level": action.risk_level,
            "risk_reasons": [action.risk_reason],
            "policy_disposition": disposition,
        },
        "decision": {
            "permission_decision": decision,
            "decision_reasons": [action.decision_reason],
            "constraints": [action.constraint],
        },
        # An APPROVAL_REQUIRED decision names the Approval it waits on and starts PENDING;
        # everything else carries no approval state at all (the schema enforces both).
        "approval": (
            {"approval_required": True, "approval_id": approval_id, "approval_status": "PENDING"}
            if disposition == APPROVAL_REQUIRED
            else {"approval_required": False, "approval_id": None, "approval_status": "NOT_REQUIRED"}
        ),
        "runtime_effect": permission_decision_runtime_effect(),
        "lifecycle": {
            "decision_status": "ACTIVE",
            "created_at": created_at,
            "expires_at": expires_at,
            "supersedes": [],
        },
        "audit_refs": [f"audit:permission:{permdec_id}"],
    }

    # Closed-schema validation, then canonical Governance Policy semantics.
    schema_path = root / "schemas" / f"{PERMISSION_DECISION_SCHEMA_VERSION}.schema.json"
    try:
        schema_cache.validate_against_schema(record, schema_path, "permission_decision")
    except RuntimeSchemaError as exc:
        raise PlannerBlocked("PERMISSION_SCHEMA_INVALID", str(exc)) from exc
    issues = validate_permission_record(record, policy)
    if issues:
        raise PlannerBlocked("PERMISSION_SEMANTICS_INVALID", "; ".join(issues[:5]))
    return record


def build_resource_refusal_permission_decision(
    bound_task: Mapping[str, Any],
    *,
    program_id: str,
    program_version: str,
    permission_scope: str,
    required_permission_level: str,
    role_permission_ceiling: str,
    target_ref: str,
    content_sha256: str | None,
    normalized_parameters: Mapping[str, Any],
    now: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the BLOCK PermissionDecision a resource request binds as refusal evidence.

    The Program Request contract requires the refused invocation to reference the
    PermissionDecision that refused it; this is the only door that builds a BLOCK record
    (scopes limited to ``_BLOCK_EVIDENCE_SCOPES``), and the record performs nothing — a
    BLOCK is never executable, and ``runtime_effect`` stays REVIEW_ONLY like every
    decision. ``bound_task`` may be a synthetic mapping carrying a REAL originating task's
    identity (the promotion-audit lineage precedent)."""
    if permission_scope not in _BLOCK_EVIDENCE_SCOPES:
        raise PlannerBlocked(
            "NOT_ALLOWED",
            f"refusal evidence is only buildable for {sorted(_BLOCK_EVIDENCE_SCOPES)}, got {permission_scope!r}",
        )
    action = _ActionSpec(
        action_type="resource.program.invoke",
        target_suffix="program_request",
        tool_id=None,
        program_id=f"{program_id}@{program_version}",
        data_scope=("programization.review",),
        normalized_parameters=dict(normalized_parameters),
        risk_reason="Requested Program invocation: unregistered/disabled resource execution is BLOCK by policy.",
        authority_reason="Authority evaluated for the record; resource eligibility is refused regardless.",
        decision_reason="Policy prices unregistered/disabled resource execution as BLOCK; the request is refusal evidence.",
        constraint="Refusal evidence only: no execution, enablement, registry mutation, or permission expansion.",
        target_ref=target_ref,
        content_sha256=content_sha256,
        risk_level="RED",
    )
    return build_permission_decision(
        bound_task,
        permission_scope=permission_scope,
        required_permission_level=required_permission_level,
        role_permission_ceiling=role_permission_ceiling,
        now=now,
        actor_id="mvp.programization.review",
        repo_root=repo_root,
        action=action,
    )


def build_search_permission_decision(
    bound_task: Mapping[str, Any],
    *,
    role_permission_ceiling: str,
    now: str,
    actor_id: str = "thomas.prime",
    ttl_minutes: int = MVP_TTL_MINUTES,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the ALLOW PermissionDecision for the R3 read-only web search.

    A thin wrapper over :func:`build_permission_decision` fixing the search scope
    (``INTERNAL_READ``), least-privilege level (P1 READ), and the search action spec.
    Fails closed identically. Used by the pipeline to authorize the search before the
    specialist may use the (gated) search tool."""
    return build_permission_decision(
        bound_task,
        permission_scope=SEARCH_PERMISSION_SCOPE,
        required_permission_level=SEARCH_REQUIRED_PERMISSION_LEVEL,
        role_permission_ceiling=role_permission_ceiling,
        now=now,
        actor_id=actor_id,
        ttl_minutes=ttl_minutes,
        repo_root=repo_root,
        action=_SEARCH_ACTION,
    )


def build_triage_permission_decision(
    bound_task: Mapping[str, Any],
    *,
    role_permission_ceiling: str,
    now: str,
    actor_id: str = "thomas.prime",
    ttl_minutes: int = MVP_TTL_MINUTES,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the ALLOW PermissionDecision for the R7.2 orchestrator triage.

    A thin wrapper over :func:`build_permission_decision` fixing the triage scope
    (``INTERNAL_ANALYSIS``), least-privilege level (P2 ANALYZE), and the triage action
    spec. Fails closed identically. Prime's importance-judging model call acts under this
    decision — a governed action of its own, never a side effect of planning."""
    return build_permission_decision(
        bound_task,
        permission_scope=TRIAGE_PERMISSION_SCOPE,
        required_permission_level=TRIAGE_REQUIRED_PERMISSION_LEVEL,
        role_permission_ceiling=role_permission_ceiling,
        now=now,
        actor_id=actor_id,
        ttl_minutes=ttl_minutes,
        repo_root=repo_root,
        action=_TRIAGE_ACTION,
    )


def build_validation_permission_decision(
    bound_task: Mapping[str, Any],
    *,
    role_permission_ceiling: str,
    now: str,
    actor_id: str = "thomas.prime",
    ttl_minutes: int = MVP_TTL_MINUTES,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the ALLOW PermissionDecision for the R7 independent validation review.

    A thin wrapper over :func:`build_permission_decision` fixing the validation scope
    (``SIMULATION_VALIDATION``), the least-privilege level (P2 ANALYZE), and the review
    action spec. Fails closed identically. The validator agent acts under this decision,
    separate from the specialist's analysis grant."""
    return build_permission_decision(
        bound_task,
        permission_scope=VALIDATION_PERMISSION_SCOPE,
        required_permission_level=VALIDATION_REQUIRED_PERMISSION_LEVEL,
        role_permission_ceiling=role_permission_ceiling,
        now=now,
        actor_id=actor_id,
        ttl_minutes=ttl_minutes,
        repo_root=repo_root,
        action=_VALIDATION_ACTION,
    )


def build_memory_promotion_permission_decision(
    bound_task: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    role_permission_ceiling: str = MEMORY_PROMOTION_REQUIRED_PERMISSION_LEVEL,
    now: str,
    approval_id: str | None = None,
    actor_id: str = "thomas.prime",
    ttl_minutes: int = MVP_TTL_MINUTES,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the APPROVAL_REQUIRED PermissionDecision for promoting one working-memory
    candidate to VALIDATED — the runtime's first action that Thomas must decide.

    The action is bound to the exact candidate: its id is the target, and its content hash
    is in the fingerprint, so an approval of *this* promotion cannot be re-pointed at
    different content (any material change invalidates the fingerprint, per the Governance
    Policy's `action_identity.invalidated_by_any_material_field_change`).

    Building this record does not promote anything and never can: the decision is
    APPROVAL_REQUIRED, and approval consumption is gate-pinned unimplemented. Promotion
    remains what R5.3 made it — an explicit operator action.
    """
    candidate_id = candidate.get("candidate_id")
    content = candidate.get("content")
    if not (isinstance(candidate_id, str) and candidate_id):
        raise PlannerBlocked("INVALID_CANDIDATE", "candidate must carry a candidate_id")
    if not (isinstance(content, str) and content.strip()):
        raise PlannerBlocked("INVALID_CANDIDATE", "candidate must carry content to promote")

    action = _ActionSpec(
        action_type="memory.validated.promote",
        target_suffix="memory_promotion",
        tool_id=None,
        data_scope=(f"memory.candidate.{candidate_id}", "task.evidence"),
        normalized_parameters={"target_scope": "validated_memory", "promotion_mode": "manual_review"},
        risk_reason="Promotion changes persistent validated organizational memory.",
        authority_reason="Prime may prepare a validated-memory promotion for Thomas review.",
        decision_reason="Persistent validated-memory promotion requires exact Thomas approval.",
        constraint="Approval is review-only and cannot create executor handoff.",
        target_ref=f"memory_candidate:{candidate_id}",
        content_sha256=integrity.sha256_record({"content": content}),
        risk_level="ORANGE",
    )
    return build_permission_decision(
        bound_task,
        permission_scope=MEMORY_PROMOTION_PERMISSION_SCOPE,
        required_permission_level=MEMORY_PROMOTION_REQUIRED_PERMISSION_LEVEL,
        role_permission_ceiling=role_permission_ceiling,
        now=now,
        actor_id=actor_id,
        ttl_minutes=ttl_minutes,
        repo_root=repo_root,
        action=action,
        approval_id=approval_id,
    )


def trial_content_sha256(role: Mapping[str, Any], trial_request: str) -> str:
    """The content hash binding a trial approval to the EXACT role version + definition
    bytes + trial task text. Any drift in any of them after Thomas approved — a role
    edit, a version bump, a swapped task — changes this hash, so the fingerprint check
    at consumption time refuses the spend."""
    return integrity.sha256_record({
        "role_id": role.get("role_id"),
        "role_version": role.get("version"),
        "definition_sha256": role.get("definition_sha256"),
        "trial_request": trial_request,
    })


def build_trial_permission_decision(
    bound_task: Mapping[str, Any],
    role: Mapping[str, Any],
    *,
    trial_request: str,
    now: str,
    role_permission_ceiling: str = TRIAL_REQUIRED_PERMISSION_LEVEL,
    approval_id: str | None = None,
    actor_id: str = "thomas.prime",
    ttl_minutes: int = MVP_TTL_MINUTES,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the APPROVAL_REQUIRED PermissionDecision for one Candidate Role Trial —
    the ask that a specific candidate role may run ONE isolated trial task.

    The action is bound to the exact candidate: role id + version in the target, and the
    role version, definition hash, and trial task text in the content hash — so an
    approval of *this* trial cannot be re-pointed at a different role revision or task
    (``action_identity.invalidated_by_any_material_field_change``). The trial request text
    also rides in ``normalized_parameters`` so the consumption step can run exactly the
    approved task without a side channel.

    Building this record runs nothing: the decision is APPROVAL_REQUIRED, and acting on
    the eventual grant is ``trial.run_trial`` — a separate, gated, single-use consumption.
    """
    role_id = role.get("role_id")
    role_version = role.get("version")
    definition_sha256 = role.get("definition_sha256")
    if not (isinstance(role_id, str) and role_id and isinstance(role_version, str) and role_version):
        raise PlannerBlocked("INVALID_ROLE", "trial role must carry role_id and version")
    if not (isinstance(definition_sha256, str) and definition_sha256):
        raise PlannerBlocked("INVALID_ROLE", "trial role must carry its definition_sha256")
    if not (isinstance(trial_request, str) and trial_request.strip()):
        raise PlannerBlocked("INVALID_TRIAL_REQUEST", "a trial needs a non-empty trial task text")

    action = _ActionSpec(
        action_type="role.candidate.trial",
        target_suffix="candidate_trial",
        tool_id=None,
        data_scope=(f"role.candidate.{role_id}", "task.request"),
        normalized_parameters={
            "assignment_mode": "candidate_trial",
            "isolated_trial_context": True,
            "role_version": role_version,
            "trial_request": trial_request,
        },
        risk_reason="Runs a non-activated candidate role once; requires explicit Thomas approval per policy.",
        authority_reason="Prime may prepare a candidate-role trial for Thomas review; the decision is Thomas's.",
        decision_reason="candidate_role_trial is APPROVAL_REQUIRED; only Thomas may authorize a trial run.",
        constraint=(
            "Isolated single trial run: no external action, no memory read/write, no workspace "
            "write, no persistent runtime change; independent validation and full audit required."
        ),
        target_ref=f"candidate_role:{role_id}@{role_version}",
        content_sha256=trial_content_sha256(role, trial_request),
        risk_level="ORANGE",
    )
    return build_permission_decision(
        bound_task,
        permission_scope=TRIAL_PERMISSION_SCOPE,
        required_permission_level=TRIAL_REQUIRED_PERMISSION_LEVEL,
        role_permission_ceiling=role_permission_ceiling,
        now=now,
        actor_id=actor_id,
        ttl_minutes=ttl_minutes,
        repo_root=repo_root,
        action=action,
        approval_id=approval_id,
    )


def build_program_registration_permission_decision(
    bound_task: Mapping[str, Any],
    *,
    program_id: str,
    program_version: str,
    definition_sha256: str,
    candidate_id: str,
    program_request_id: str,
    now: str,
    actor_id: str = "thomas.prime",
    ttl_minutes: int = MVP_TTL_MINUTES,
    repo_root: Path | None = None,
    approval_id: str | None = None,
) -> dict[str, Any]:
    """Build the APPROVAL_REQUIRED PermissionDecision asking Thomas to register a
    candidate Program in the Program Registry (explicit Thomas decision 2026-07-22).

    The action binds the exact registration: program id + version, the canonical hash of
    the definition content that would be registered, and the review lineage (the ACCEPTED
    programization candidate and its program request). Any material change mints a
    different fingerprint and therefore a different approval. Building this record
    performs nothing; executing the approved registration is the operator door
    (``scripts/register_program_candidate.py``), which VERIFIES the approval against the
    same content hash and never consumes it. The registered entry stays
    ``status: candidate, enabled: false`` — activation is a separate approval.
    """
    if not (isinstance(definition_sha256, str) and definition_sha256.startswith("sha256:")):
        raise PlannerBlocked("INVALID_REGISTRATION", "registration needs the definition content hash")
    action = _ActionSpec(
        action_type="program.registry.registration",
        target_suffix="program_registration",
        tool_id=None,
        program_id=f"{program_id}@{program_version}",
        data_scope=("program.registry", "programization.review"),
        normalized_parameters={
            "program_id": program_id,
            "program_version": program_version,
            "candidate_id": candidate_id,
            "program_request_id": program_request_id,
        },
        risk_reason="Adds a candidate entry to the Program Registry; requires explicit Thomas approval per policy.",
        authority_reason="Prime may prepare a registration for Thomas review; the decision is Thomas's.",
        decision_reason="TOOL_PROGRAM_GOVERNANCE is APPROVAL_REQUIRED; only Thomas may authorize a registry change.",
        constraint=(
            "Candidate registration only: status candidate, enabled false, no runtime "
            "implementation — no execution, enablement, or activation capability."
        ),
        target_ref=f"program_registry:{program_id}@{program_version}",
        content_sha256=definition_sha256,
        risk_level="ORANGE",
    )
    return build_permission_decision(
        bound_task,
        permission_scope=REGISTRATION_PERMISSION_SCOPE,
        required_permission_level=REGISTRATION_REQUIRED_PERMISSION_LEVEL,
        role_permission_ceiling=REGISTRATION_REQUIRED_PERMISSION_LEVEL,
        now=now,
        actor_id=actor_id,
        ttl_minutes=ttl_minutes,
        repo_root=repo_root,
        action=action,
        approval_id=approval_id,
    )


def build_strategy_promotion_permission_decision(
    bound_task: Mapping[str, Any],
    *,
    candidate_ids: list[str],
    strategy_ids: list[str],
    rule_hashes: list[str],
    keep_active: bool,
    content_sha256: str,
    now: str,
    actor_id: str = "thomas.prime",
    ttl_minutes: int = MVP_TTL_MINUTES,
    repo_root: Path | None = None,
    approval_id: str | None = None,
) -> dict[str, Any]:
    """Build the APPROVAL_REQUIRED PermissionDecision asking Thomas to promote
    strategy candidates into the active pool (Crypto Pipeline C8b).

    The action is bound to the exact promotion: the candidate ids, their rule hashes,
    and the add-vs-replace mode all ride in the content hash, so an approval of THIS
    promotion cannot be re-pointed at different strategies or a different pool effect
    (``action_identity.invalidated_by_any_material_field_change``). Building this
    record performs nothing; executing the approved promotion is the operator door
    (``scripts/promote_strategy_candidates.py``), which VERIFIES the approval against
    this same content hash and never consumes it (no consumption implementation for
    this scope — the pre-R10 posture, kept by design).
    """
    if not candidate_ids or not all(isinstance(c, str) and c for c in candidate_ids):
        raise PlannerBlocked("INVALID_PROMOTION", "promotion needs at least one candidate id")
    if len(strategy_ids) != len(candidate_ids) or not all(isinstance(s, str) and s for s in strategy_ids):
        raise PlannerBlocked("INVALID_PROMOTION", "every promoted candidate must carry its display strategy id")
    if len(rule_hashes) != len(candidate_ids) or not all(isinstance(h, str) and h for h in rule_hashes):
        raise PlannerBlocked("INVALID_PROMOTION", "every promoted candidate must carry its rule hash")

    action = _ActionSpec(
        action_type="crypto.strategy_pool.promotion",
        target_suffix="strategy_pool_promotion",
        tool_id=None,
        data_scope=("crypto.strategy_candidates", "crypto.active_strategy_pool"),
        normalized_parameters={
            # candidate_ids are the binding identity; strategy_ids ride as display names.
            "candidate_ids": sorted(candidate_ids),
            "strategy_ids": sorted(strategy_ids),
            "rule_hashes": sorted(rule_hashes),
            "keep_active": bool(keep_active),
        },
        risk_reason="Changes what the runtime paper-trades; requires explicit Thomas approval per policy.",
        authority_reason="Prime may prepare a strategy-pool promotion for Thomas review; the decision is Thomas's.",
        decision_reason="RUNTIME_GOVERNANCE is APPROVAL_REQUIRED; only Thomas may authorize a pool change.",
        constraint=(
            "Paper-stage pool change only: no order capability, no live/testnet effect, no "
            "governance-state change beyond the active strategy pool; full audit required."
        ),
        target_ref="active_strategy_pool:paper",
        content_sha256=content_sha256,
        risk_level="ORANGE",
    )
    return build_permission_decision(
        bound_task,
        permission_scope=STRATEGY_PROMOTION_PERMISSION_SCOPE,
        required_permission_level=STRATEGY_PROMOTION_REQUIRED_PERMISSION_LEVEL,
        role_permission_ceiling=STRATEGY_PROMOTION_REQUIRED_PERMISSION_LEVEL,
        now=now,
        actor_id=actor_id,
        ttl_minutes=ttl_minutes,
        repo_root=repo_root,
        action=action,
        approval_id=approval_id,
    )


def build_trial_work_permission_decision(
    bound_task: Mapping[str, Any],
    *,
    role_permission_ceiling: str,
    now: str,
    actor_id: str = "thomas.prime",
    ttl_minutes: int = MVP_TTL_MINUTES,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the ALLOW PermissionDecision the candidate role's trial WORK runs under.

    The trial authorization above is Thomas's APPROVAL_REQUIRED grant to hold the trial at
    all; the work itself — one read-only internal analysis by the candidate role — is the
    same ALLOW-tier effect class as a normal specialist run, at P2 ANALYZE, and gets its
    own least-privilege decision exactly like the validator's or the triage's. A distinct
    action_type keeps the decision id apart from a normal analysis."""
    action = _ActionSpec(
        action_type="internal.analysis.candidate_trial",
        target_suffix="candidate_trial_work",
        tool_id=None,
        data_scope=("task.request",),
        normalized_parameters={"assignment_mode": "candidate_trial", "visibility": "internal"},
        risk_reason="Isolated internal trial analysis; no external, financial, or runtime effect.",
        authority_reason="Trial execution within the approved trial scope and the candidate role's ceiling.",
        decision_reason="Authority is sufficient and the trial work is internal, isolated, and read-only.",
        constraint="No external action, tool/program execution, memory access, or runtime mutation.",
    )
    return build_permission_decision(
        bound_task,
        permission_scope=TRIAL_WORK_PERMISSION_SCOPE,
        required_permission_level=TRIAL_WORK_REQUIRED_PERMISSION_LEVEL,
        role_permission_ceiling=role_permission_ceiling,
        now=now,
        actor_id=actor_id,
        ttl_minutes=ttl_minutes,
        repo_root=repo_root,
        action=action,
    )


def build_write_permission_decision(
    bound_task: Mapping[str, Any],
    *,
    role_permission_ceiling: str,
    now: str,
    actor_id: str = "thomas.prime",
    ttl_minutes: int = MVP_TTL_MINUTES,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the EXECUTE_AND_REPORT PermissionDecision for the R8 controlled write.

    A thin wrapper over :func:`build_permission_decision` fixing the write scope
    (``WORKSPACE_REVERSIBLE_WRITE``), the least-privilege level (P3 CREATE), and the write
    action spec. Fails closed identically — including on authority: a role whose ceiling is
    below P3 cannot obtain this grant.

    Unlike its siblings this decision is **EXECUTE_AND_REPORT, not ALLOW** — the runtime's
    first. The disposition comes from the canonical Governance Policy, not from here; the
    caller owes the "report" half (audit + operator report), which the pipeline provides."""
    return build_permission_decision(
        bound_task,
        permission_scope=WRITE_PERMISSION_SCOPE,
        required_permission_level=WRITE_REQUIRED_PERMISSION_LEVEL,
        role_permission_ceiling=role_permission_ceiling,
        now=now,
        actor_id=actor_id,
        ttl_minutes=ttl_minutes,
        repo_root=repo_root,
        action=_WRITE_ACTION,
    )


def build_live_order_permission_decision(
    bound_task: Mapping[str, Any],
    *,
    role_permission_ceiling: str,
    symbol: str,
    side: str,
    notional_usdt: float,
    order_fingerprint: str,
    now: str,
    actor_id: str = "thomas.prime",
    ttl_minutes: int = MVP_TTL_MINUTES,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the EXECUTE_AND_REPORT PermissionDecision for one live exchange order (LP4).

    A thin wrapper over :func:`build_permission_decision` fixing the live-order scope
    (``FINANCIAL_APPROVED_TRADING_USE``) and its least-privilege level (**P5 EXTERNAL_ACTION**),
    with a per-order action spec so the decision binds this exact order. Fails closed
    identically — including on authority: a role whose ceiling is below P5 cannot obtain this
    grant, which is why an ``execution.live_trader`` role (P5) is required and every ordinary
    role (P3 or lower) is refused here by the authority invariant.

    Like `WORKSPACE_REVERSIBLE_WRITE` this is **EXECUTE_AND_REPORT, not ALLOW**, and the
    disposition comes from the canonical Governance Policy, not from here. Building the decision
    grants nothing and sends nothing: the record is REVIEW_ONLY planning evidence. The order is
    only actually placed by LP4's adapter, behind the ``live_trading`` safety flag and the final
    order guard — neither of which this function is."""
    return build_permission_decision(
        bound_task,
        permission_scope=LIVE_ORDER_PERMISSION_SCOPE,
        required_permission_level=LIVE_ORDER_REQUIRED_PERMISSION_LEVEL,
        role_permission_ceiling=role_permission_ceiling,
        now=now,
        actor_id=actor_id,
        ttl_minutes=ttl_minutes,
        repo_root=repo_root,
        action=build_live_order_action(
            symbol=symbol, side=side, notional_usdt=notional_usdt,
            order_fingerprint=order_fingerprint,
        ),
    )
