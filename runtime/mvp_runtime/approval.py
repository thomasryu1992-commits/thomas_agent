"""R9 Approval flow — request an action from Thomas and record his verified decision.

The runtime's first governed *ask*. Everything the agent could do before this it decided
itself (ALLOW) or did-and-reported (EXECUTE_AND_REPORT, R8). An APPROVAL_REQUIRED action
is one it may not decide: it must ask Thomas, over the one channel that can prove it was
Thomas, and record the answer as tamper-evident evidence.

**The lifecycle this module owns** — the *ask* and Thomas's *answer*:

    PENDING --(/approve)--> APPROVED --(consume, R10)--> CONSUMED
            --(/reject )--> REJECTED
            --(ttl)-------> EXPIRED

Through R9 this module stopped at APPROVED: an approved grant authorized nothing on its own.
R10 adds the last transition — *consuming* the one-time grant to perform exactly its bound
action — but that step lives in `consumption.py`, behind the `approval_consumption` safety
flag, and stays deliberately narrow (a scoped `SENSITIVE_MEMORY_GOVERNANCE` promotion; no
executor handoff, no external or financial effect; the record stays REVIEW_ONLY). This
module still only *asks* and *records the answer*; it also builds the CONSUMED evidence
record (:func:`build_consumed_record`) that consumption appends once it has acted.
(`CONSUMPTION_PREVIEWED` remains unimplemented: its schema requires an
`execution_request.v0.1`, which belongs to the deferred executor family.)

**Identity is the whole point.** An approval is only worth the certainty that Thomas gave
it, so `record_decision` refuses anything the Governance Policy calls an invalid source —
a group, a channel, another user, a forwarded message, an ambiguous expression, or a code
that does not match the approval being decided. The `approval.v0.2` schema independently
requires `approved_by: Thomas` + `verification_status: VERIFIED` + a verification ref on
any decided record, so a decision recorded without verified identity cannot even be built.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

import yaml

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.schema_validation import RuntimeSchemaError

from . import schema_cache
from . import timeutil
from .authority import permission_decision_runtime_effect
from .control import command_verb
from .errors import ApprovalBlocked
from .paths import repo_root as _repo_root

from . import _scripts_bridge  # noqa: F401  (side effect: scripts/ on sys.path, once)

from validate_permission_approval_contracts import validate_approval_record  # noqa: E402

APPROVAL_SCHEMA_VERSION = "approval.v0.2"

# The only approver the schema will accept, and the only channel that can prove identity.
REQUIRED_APPROVER = "Thomas"
TELEGRAM_VERIFICATION_METHOD = "telegram_private_control_channel"

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_EXPIRED = "EXPIRED"
STATUS_CONSUMED = "CONSUMED"

# An in-memory PermissionDecision is referenced the way every other MVP record is; the
# schema only requires a non-empty string, not a file path.
def _permission_ref(permission_decision: Mapping[str, Any]) -> str:
    return f"in_memory:{permission_decision['permission_decision_id']}"


@dataclass(frozen=True)
class Verification:
    """Evidence that the decision came from Thomas on the verified control channel.

    ``verification_ref`` records *where* the decision was made (e.g.
    ``telegram:private_chat:registered-thomas:approval_x``) — never a token or secret.
    """

    approved_by: str
    method: str
    verification_ref: str


def _require_pending(approval: Mapping[str, Any]) -> None:
    status = approval.get("status")
    if status != STATUS_PENDING:
        # One-time use: a decided approval is final. Re-deciding it would be the reuse the
        # Governance Policy blocks (`approval_reuse_allowed: false`, BLOCK: APPROVAL_REUSE).
        raise ApprovalBlocked(
            "NOT_PENDING", f"approval is {status}; only a PENDING approval can be decided"
        )


def _policy(root: Path) -> dict[str, Any]:
    try:
        return yaml.safe_load((root / "governance" / "GOVERNANCE_POLICY.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ApprovalBlocked("POLICY_UNAVAILABLE", f"cannot load governance policy: {exc}") from exc


def _validate(approval: Mapping[str, Any], permission_decision: Mapping[str, Any], root: Path) -> None:
    """Closed-schema validation, then canonical Governance Policy semantics — the same
    two-step every MVP record gets. The semantic pass re-checks the full binding to the
    PermissionDecision (ids, fingerprint, snapshot, policy), so a mismatched pair fails."""
    schema_path = root / "schemas" / f"{APPROVAL_SCHEMA_VERSION}.schema.json"
    try:
        schema_cache.validate_against_schema(dict(approval), schema_path, "approval")
    except RuntimeSchemaError as exc:
        raise ApprovalBlocked("APPROVAL_SCHEMA_INVALID", str(exc)) from exc
    issues = validate_approval_record(
        dict(approval), {_permission_ref(permission_decision): dict(permission_decision)}, _policy(root)
    )
    if issues:
        raise ApprovalBlocked("APPROVAL_SEMANTICS_INVALID", "; ".join(issues[:5]))


def approval_ttl_minutes(permission_scope: str, *, root: Path | None = None) -> int:
    """The policy's maximum approval lifetime for a scope. Sensitive scopes get minutes,
    not the 30-minute default — the Governance Policy sets each one."""
    policy = _policy(root if root is not None else _repo_root())
    lifetime = policy.get("approval_lifetime", {})
    default = lifetime.get("default_approval_ttl_minutes", 30)
    return int(lifetime.get("scope_max_ttl_minutes", {}).get(permission_scope, default))


def build_approval_request(
    permission_decision: Mapping[str, Any],
    *,
    now: str,
    ttl_minutes: int | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the PENDING ``approval.v0.2`` request for an APPROVAL_REQUIRED decision.

    The approval snapshots the exact action (fingerprint + payload) so what Thomas sees is
    what he is deciding, and nothing else can later be substituted. Expiry is the earlier
    of the policy maximum for the scope and the PermissionDecision's own expiry — an
    approval must never outlive the decision it authorizes.

    Fails closed (``ApprovalBlocked``) if the decision is not APPROVAL_REQUIRED, carries no
    approval_id, has already expired, or the produced record fails schema/semantics.
    """
    root = repo_root if repo_root is not None else _repo_root()
    decision = permission_decision.get("decision", {}).get("permission_decision")
    if decision != "APPROVAL_REQUIRED":
        raise ApprovalBlocked(
            "NOT_APPROVAL_REQUIRED",
            f"an Approval may only be requested for an APPROVAL_REQUIRED decision, got {decision}",
        )
    approval_id = permission_decision.get("approval", {}).get("approval_id")
    if not (isinstance(approval_id, str) and approval_id.startswith("approval_")):
        raise ApprovalBlocked("NO_APPROVAL_ID", "the PermissionDecision carries no approval_id")

    scope = permission_decision["fingerprint_payload"]["permission_scope"]
    policy_max = approval_ttl_minutes(scope, root=root)
    requested = policy_max if ttl_minutes is None else int(ttl_minutes)
    if requested < 1:
        raise ApprovalBlocked("INVALID_TTL", "approval ttl must be at least 1 minute")
    if requested > policy_max:
        raise ApprovalBlocked(
            "TTL_EXCEEDS_POLICY",
            f"requested {requested}m exceeds the policy maximum for {scope} ({policy_max}m)",
        )

    issued = timeutil.parse_iso(now)
    decision_expiry = timeutil.parse_iso(permission_decision["lifecycle"]["expires_at"])
    # The approval can never outlive the decision it is bound to.
    expires = min(decision_expiry, issued + timedelta(minutes=requested))
    if expires <= issued:
        raise ApprovalBlocked("DECISION_EXPIRED", "the PermissionDecision has already expired")

    approval = {
        "schema_version": APPROVAL_SCHEMA_VERSION,
        "approval_id": approval_id,
        "permission_decision_id": permission_decision["permission_decision_id"],
        "permission_decision_ref": _permission_ref(permission_decision),
        "trace_id": permission_decision["trace_id"],
        "task_id": permission_decision["task_id"],
        "task_revision": permission_decision["task_revision"],
        "core_context_binding_id": permission_decision["core_context_binding_id"],
        "operating_policy": dict(permission_decision["operating_policy"]),
        "action_fingerprint": permission_decision["action_fingerprint"],
        "approved_action_snapshot": dict(permission_decision["fingerprint_payload"]),
        "approval_scope": "REVIEW_ONLY",
        "status": STATUS_PENDING,
        "approver": {
            "required_approver": REQUIRED_APPROVER,
            "approved_by": None,
            "verification_status": "NOT_VERIFIED",
            "identity_verification_method": None,
            "verification_ref": None,
        },
        "decision": {"decision_reason": None, "decided_at": None},
        "consumption": {
            "one_time_use": True,
            "consumption_status": "NOT_CONSUMED",
            "previewed_at": None,
            "preview_ref": None,
            "consumed_at": None,
            "consumption_ref": None,
        },
        "validity": {"issued_at": timeutil.format_iso(issued), "expires_at": timeutil.format_iso(expires)},
        # authority.py owns the no-grant effect block — no local copies (schema consts
        # still catch value drift; this keeps one construction site).
        "runtime_effect": permission_decision_runtime_effect(),
        "audit_refs": [f"audit:approval_request:{approval_id}"],
    }
    _validate(approval, permission_decision, root)
    return approval


def is_expired(approval: Mapping[str, Any], *, now: str) -> bool:
    return timeutil.parse_iso(now) >= timeutil.parse_iso(approval["validity"]["expires_at"])


def record_decision(
    approval: Mapping[str, Any],
    permission_decision: Mapping[str, Any],
    *,
    granted: bool,
    verification: Verification,
    reason: str,
    now: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Record Thomas's verified decision, returning the APPROVED/REJECTED approval.

    Fails closed (``ApprovalBlocked``) unless every one of these holds:

    - the approval is still PENDING (a decided approval is final — deciding it twice is the
      reuse the Governance Policy blocks);
    - it has not expired (an expired approval is dead, not decidable);
    - the approver is Thomas, verified through the Telegram private control channel, with a
      verification ref — anything else is one of the policy's ``invalid_approval_sources``.

    Returns a NEW record; the input is never mutated (approvals are append-only evidence).
    """
    root = repo_root if repo_root is not None else _repo_root()
    _require_pending(approval)
    if is_expired(approval, now=now):
        raise ApprovalBlocked(
            "APPROVAL_EXPIRED",
            f"approval expired at {approval['validity']['expires_at']}; it can no longer be decided",
        )
    if verification.approved_by != REQUIRED_APPROVER:
        raise ApprovalBlocked(
            "WRONG_APPROVER",
            f"only {REQUIRED_APPROVER} may decide an approval; got {verification.approved_by!r}",
        )
    if verification.method != TELEGRAM_VERIFICATION_METHOD:
        raise ApprovalBlocked(
            "UNVERIFIED_SOURCE",
            f"approval identity must be verified via {TELEGRAM_VERIFICATION_METHOD}, got {verification.method!r}",
        )
    if not (isinstance(verification.verification_ref, str) and verification.verification_ref.strip()):
        raise ApprovalBlocked("NO_VERIFICATION_REF", "a verified decision must record where it was made")
    if not (isinstance(reason, str) and reason.strip()):
        raise ApprovalBlocked("NO_DECISION_REASON", "a decision must record its reason")

    decided = dict(approval)
    decided["status"] = STATUS_APPROVED if granted else STATUS_REJECTED
    decided["approver"] = {
        "required_approver": REQUIRED_APPROVER,
        "approved_by": verification.approved_by,
        "verification_status": "VERIFIED",
        "identity_verification_method": verification.method,
        "verification_ref": verification.verification_ref,
    }
    decided["decision"] = {"decision_reason": reason, "decided_at": now}
    decided["audit_refs"] = list(dict.fromkeys([
        *approval.get("audit_refs", []), f"audit:approval_decision:{approval['approval_id']}"
    ]))
    _validate(decided, permission_decision, root)
    return decided


def expire(approval: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    """Mark a PENDING approval EXPIRED. Not a decision — nobody answered in time.

    Retirement, not a verdict: the record keeps an empty approver block, because nobody
    approved or rejected it. :func:`apply_command` calls this when Thomas answers an ask
    that has already timed out — the refusal stands, and the dead ask stops appearing in
    ``pending()`` forever after."""
    _require_pending(approval)
    if not is_expired(approval, now=now):
        raise ApprovalBlocked("NOT_EXPIRED", "approval has not reached its expiry")
    expired = dict(approval)
    expired["status"] = STATUS_EXPIRED
    return expired


def build_consumed_record(
    approval: Mapping[str, Any],
    permission_decision: Mapping[str, Any],
    *,
    consumed_at: str,
    consumption_ref: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the CONSUMED approval record — the tamper-evident evidence that this one-time
    grant was spent (R10).

    The input must be an APPROVED approval; anything else is refused, since only Thomas's
    verified APPROVED grant may be consumed. Returns a NEW record carrying the same verified
    approver and decision (a consumed approval is still the approval Thomas granted), with the
    consumption block advanced to CONSUMED. The record stays REVIEW_ONLY with every runtime
    flag false — consuming an approval performs its bound internal action; it does not widen
    the approval into an executor/external/financial capability.

    ``consumption_ref`` points at the durable outcome (the validated-memory id + audit event),
    so the trail links the spent grant to exactly what it produced. Fails closed on a
    non-APPROVED input, a missing ref, or a record that fails schema/semantics.
    """
    root = repo_root if repo_root is not None else _repo_root()
    status = approval.get("status")
    if status == STATUS_CONSUMED:
        raise ApprovalBlocked("ALREADY_CONSUMED", "approval has already been consumed (one-time use)")
    if status != STATUS_APPROVED:
        raise ApprovalBlocked(
            "NOT_APPROVED", f"only an APPROVED approval can be consumed; this one is {status}"
        )
    if not (isinstance(consumption_ref, str) and consumption_ref.strip()):
        raise ApprovalBlocked("NO_CONSUMPTION_REF", "a consumed approval must record what it produced")

    consumed = dict(approval)
    consumed["status"] = STATUS_CONSUMED
    consumed["consumption"] = {
        "one_time_use": True,
        "consumption_status": "CONSUMED",
        "previewed_at": None,
        "preview_ref": None,
        "consumed_at": consumed_at,
        "consumption_ref": consumption_ref,
    }
    consumed["audit_refs"] = list(dict.fromkeys([
        *approval.get("audit_refs", []), f"audit:approval_consumption:{approval['approval_id']}"
    ]))
    _validate(consumed, permission_decision, root)
    return consumed


def format_request(approval: Mapping[str, Any]) -> str:
    """Render the Approval Request for the control channel.

    Follows the Approval Request Format in `docs/MVP_OPERATING_POLICY.md` §11.5 so Thomas
    sees the exact action, its target, why, its risks, and how to answer. The action
    fingerprint is shown because the policy requires the answer to name the approval or
    the fingerprint code — it is what binds his reply to *this* action.
    """
    snapshot = approval["approved_action_snapshot"]
    permdec_reasons = approval.get("_decision_reasons") or []
    lines = [
        "Approval Request",
        "",
        f"Approval ID: {approval['approval_id']}",
        f"Task ID: {approval['task_id']}",
        f"요청 행동: {snapshot['action_type']} ({snapshot['permission_scope']})",
        f"정확한 대상: {snapshot['target_ref']}",
    ]
    if snapshot.get("content_sha256"):
        lines.append(f"내용 해시: {snapshot['content_sha256']}")
    lines += [
        f"요청 이유: {'; '.join(permdec_reasons) if permdec_reasons else '—'}",
        f"주요 위험: {'; '.join(approval.get('_risk_reasons', [])) or '—'}",
        "예상 비용: 없음",
        "되돌릴 수 있는가: 아니오 — validated memory는 지속됩니다",
        f"유효 시각: {approval['validity']['expires_at']} (UTC)",
        f"Action Fingerprint: {approval['action_fingerprint']}",
        "",
        f"가능한 선택:  /approve {approval['approval_id']}  |  /reject {approval['approval_id']}",
        "(id 뒤에 이유를 적으면 결정 기록에 남습니다 — 예: /reject "
        f"{approval['approval_id']} 근거 문서가 부족함)",
        "",
        "이 승인은 REVIEW_ONLY입니다. 승인만으로 런타임이 자동 실행하지 않습니다.",
        "승인 후 소비(consume)는 별도의 운영자 단계이며, approval_consumption 세이프티",
        "플래그가 켜진 기기에서만 이 승인 하나에 묶인 승격을 1회 수행합니다.",
    ]
    return "\n".join(lines)


def request_message(
    approval: Mapping[str, Any],
    permission_decision: Mapping[str, Any],
    *,
    history: Mapping[str, Any] | None = None,
) -> str:
    """``format_request`` with the decision's own reasons folded in for display.

    ``history`` (stage 2 of preference inference) appends a summary of Thomas's past
    decisions on the same action type — see :func:`decision_history`. Advisory display
    only: it informs the ask, it never answers it."""
    enriched = dict(approval)
    enriched["_decision_reasons"] = permission_decision.get("decision", {}).get("decision_reasons", [])
    enriched["_risk_reasons"] = permission_decision.get("risk", {}).get("risk_reasons", [])
    message = format_request(enriched)
    history_block = format_decision_history(history) if history is not None else ""
    return message + history_block


# Decided states that carry a preference signal. CONSUMED is an approval Thomas granted
# and later spent — for inference it counts as approved; EXPIRED carries no decision.
_HISTORY_APPROVED_STATUSES = frozenset({STATUS_APPROVED, STATUS_CONSUMED})
_HISTORY_DECIDED_STATUSES = _HISTORY_APPROVED_STATUSES | {STATUS_REJECTED}


def decision_history(store: "Any", approval: Mapping[str, Any], *, limit: int = 3) -> dict[str, Any]:
    """Summarize Thomas's past decisions on the SAME action type as this new ask.

    Stage 2 of preference inference: the append-only approval store already accumulates
    every decision with its reason (stage 1); this reads them back — a read of the
    runtime's own state, like ``pending()``/``show`` — so a new Approval Request can show
    the pattern next to the ask. Latest state per approval wins (the store's ``current()``
    semantics); the new request itself is excluded; boilerplate default reasons are
    flagged so a bare verdict is never mistaken for an articulated preference.
    """
    action_type = approval.get("approved_action_snapshot", {}).get("action_type")
    decided = [
        a for a in store.current().values()
        if a.get("approval_id") != approval.get("approval_id")
        and a.get("status") in _HISTORY_DECIDED_STATUSES
        and a.get("approved_action_snapshot", {}).get("action_type") == action_type
    ]
    decided.sort(key=lambda a: str(a.get("decision", {}).get("decided_at", "")), reverse=True)
    recent = [
        {
            "status": (STATUS_APPROVED if a["status"] in _HISTORY_APPROVED_STATUSES else STATUS_REJECTED),
            "decided_at": a.get("decision", {}).get("decided_at"),
            "reason": a.get("decision", {}).get("decision_reason"),
            "boilerplate": a.get("decision", {}).get("decision_reason") in _DEFAULT_DECISION_REASONS,
        }
        for a in decided[:limit]
    ]
    return {
        "action_type": action_type,
        "approved": sum(1 for a in decided if a["status"] in _HISTORY_APPROVED_STATUSES),
        "rejected": sum(1 for a in decided if a["status"] == STATUS_REJECTED),
        "recent": recent,
    }


def format_decision_history(history: Mapping[str, Any]) -> str:
    """Render :func:`decision_history` for the request message; empty when no history.

    Ends with an advisory line because the summary exists to inform Thomas, never to
    nudge an automatic outcome — inferred patterns must not become auto-approval."""
    approved = int(history.get("approved", 0))
    rejected = int(history.get("rejected", 0))
    if approved + rejected == 0:
        return ""
    lines = [
        "",
        f"과거 유사 결정 ({history.get('action_type')}): 승인 {approved} / 거절 {rejected}",
    ]
    for item in history.get("recent", []):
        stamp = str(item.get("decided_at") or "?")[:10]
        reason = item.get("reason")
        shown = "(이유 미기재)" if item.get("boilerplate") or not reason else str(reason)
        lines.append(f"- [{item.get('status')} {stamp}] {shown}")
    lines.append("(참고용 이력입니다 — 결정은 이 요청 자체로 판단해 주세요.)")
    return "\n" + "\n".join(lines)


# --- control-channel commands ------------------------------------------------------

CMD_APPROVE = "approve"
CMD_REJECT = "reject"
_COMMANDS = frozenset({CMD_APPROVE, CMD_REJECT})

# The boilerplate recorded when Thomas answers without his own reason. One authority:
# apply_command records these, and the decision-history summary uses the same strings to
# tell a real preference signal apart from a bare verdict.
DEFAULT_APPROVE_REASON = "Approved by Thomas on the verified control channel."
DEFAULT_REJECT_REASON = "Rejected by Thomas on the verified control channel."
_DEFAULT_DECISION_REASONS = frozenset({DEFAULT_APPROVE_REASON, DEFAULT_REJECT_REASON})


def parse_approval_command(text: Any) -> tuple[str, str | None, str | None] | None:
    """Parse ``/approve <approval_id> [reason...]`` / ``/reject <approval_id> [reason...]``.

    Returns ``(verb, approval_id, reason)``; None if the text is not an approval command.
    The id is mandatory in practice (see :func:`apply_command`) — the Governance Policy
    requires the answer to name the approval (``approval_id_or_fingerprint_code_required``),
    so a bare "/approve" is an ambiguous expression and must not decide anything.

    Everything after the id is Thomas's own free-text reason, recorded verbatim as the
    decision's ``decision_reason``. Without it the store accumulates only the boilerplate
    "decided on the verified channel" — capturing the *why* is what makes the decision
    history usable for later preference inference.
    """
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    head, _, rest = stripped.partition(" ")
    # One tokenizer with the console parser (control.command_verb): leading slash,
    # lowercasing, and the Telegram @botname suffix are handled identically there.
    verb = command_verb(head, slash_seen=stripped.startswith("/"))
    if verb not in _COMMANDS:
        return None
    approval_ref, _, reason = rest.strip().partition(" ")
    return verb, (approval_ref or None), (reason.strip() or None)


def apply_command(
    store: "Any",
    verb: str,
    approval_id: str | None,
    *,
    verification: Verification,
    reason: str | None = None,
    now: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Apply a verified ``/approve`` or ``/reject`` to a stored PENDING approval.

    Returns ``{"action", "approval", "reply"}``. Fails closed (``ApprovalBlocked``) on a
    missing/unknown id, an approval that is not PENDING, an expired one, or a decision whose
    bound PermissionDecision cannot be found — an answer that cannot be tied back to the
    exact action it authorizes is not evidence of anything.
    """
    if verb not in _COMMANDS:
        raise ApprovalBlocked("UNKNOWN_COMMAND", f"unknown approval command: {verb!r}")
    if not approval_id:
        # The policy forbids acting on an ambiguous expression: name the approval.
        raise ApprovalBlocked(
            "NO_APPROVAL_ID", f"/{verb} requires the approval id (e.g. /{verb} approval_abc123)"
        )
    approval = store.get(approval_id)
    if approval is None:
        raise ApprovalBlocked("UNKNOWN_APPROVAL", f"no approval with id {approval_id}")

    permission_decision = store.get_permission_decision(approval["permission_decision_id"])
    if permission_decision is None:
        raise ApprovalBlocked(
            "PERMISSION_DECISION_MISSING",
            f"the decision {approval['permission_decision_id']} this approval binds to is not on record",
        )

    # Answering a timed-out ask retires it. The refusal below is unchanged — an expired
    # approval is dead, not decidable — but the record now transitions to EXPIRED instead
    # of sitting PENDING forever, which is what the lifecycle has always claimed
    # (`--(ttl)--> EXPIRED`) and nothing ever performed. This is the one moment a retirement
    # is unambiguously right: an explicit operator action on that exact approval, so it is
    # never a write hidden inside a read.
    if approval.get("status") == STATUS_PENDING and is_expired(approval, now=now):
        store.append([expire(approval, now=now)])
        raise ApprovalBlocked(
            "APPROVAL_EXPIRED",
            f"approval expired at {approval['validity']['expires_at']}; it can no longer be "
            "decided (now recorded EXPIRED — ask again for a fresh one)",
        )

    granted = verb == CMD_APPROVE
    default_reason = DEFAULT_APPROVE_REASON if granted else DEFAULT_REJECT_REASON
    decided = record_decision(
        approval, permission_decision,
        granted=granted,
        verification=verification,
        reason=reason or default_reason,
        now=now,
        repo_root=repo_root,
    )
    store.append([decided])
    snapshot = decided["approved_action_snapshot"]
    reply = (
        f"{decided['status']}: {snapshot['action_type']} on {snapshot['target_ref']}\n"
        f"Approval {decided['approval_id']} is now {decided['status']} and is single-use."
    )
    if reason:
        # Echo the recorded reason so Thomas sees his own words made it into the record.
        reply += f"\nReason recorded: {decided['decision']['decision_reason']}"
    if granted:
        reply += (
            "\nThis approval is REVIEW_ONLY — the runtime will not act on it automatically. "
            "Consume it as a separate operator step (approval_cli consume <id>) to perform the "
            "single bound promotion; consumption is gated by the approval_consumption safety flag."
        )
    return {"action": decided["status"], "approval": decided, "reply": reply}
