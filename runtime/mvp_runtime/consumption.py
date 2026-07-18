"""R10 Approval consumption — spend an APPROVED, single-use grant to perform its one action.

Through R9 an APPROVED approval authorized nothing: it was proof Thomas had been asked and
what he answered, and promotion stayed a separate operator action. R10 closes the loop with
the narrowest possible step — *consuming* the grant performs exactly the one action it was
bound to (promoting the exact working-memory candidate it snapshotted to VALIDATED memory)
and nothing else.

**Why this is safe to add now.** Consumption is scoped, gated, and fail-closed at every
layer:

- **Scoped.** Only a ``SENSITIVE_MEMORY_GOVERNANCE`` promotion is consumable. The record
  stays ``approval_scope: REVIEW_ONLY`` with every ``runtime_effect`` flag false — consuming
  performs an internal governed memory write (the R8 precedent: an EXECUTE_AND_REPORT effect
  under REVIEW_ONLY), never an executor handoff, external call, or financial move.
- **Gated.** The capable consumer is only constructed behind the ``approval_consumption``
  safety flag via :func:`safety_gate.select_gated`. Without the operator's opt-in *and* a
  local, integrity-checked activation record, the gate hands back an inert consumer that
  refuses — an env var alone consumes nothing.
- **Hot-path revalidated.** Before acting it re-derives the action fingerprint from the
  snapshot and re-hashes the *current* candidate content, refusing (``FINGERPRINT_MISMATCH`` /
  ``CONTENT_CHANGED``) if either drifted since Thomas approved. He can only ever spend a grant
  on exactly what he saw.
- **One-time use.** A CONSUMED approval is terminal; a compare-and-set re-read of the stored
  status immediately before acting refuses a second consume (``ALREADY_CONSUMED``). The store
  is append-only, so the spend is itself tamper-evident evidence.

Everything here fails closed: any doubt about identity, freshness, content, or single-use
refuses rather than performs an action Thomas did not exactly authorize.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from . import approval as approval_mod
from . import audit, safety_gate, timeutil
from .approval_store import ApprovalStore
from .errors import ApprovalBlocked, MvpRuntimeError
from .memory import CANDIDATE_SCOPE, CANDIDATE_STATUS, promote_candidate
from .paths import repo_root as _repo_root
from .permission import MEMORY_PROMOTION_PERMISSION_SCOPE
from .safety_gate import APPROVAL_CONSUMPTION
from .store import LedgerStore
from .working_memory import WorkingMemoryStore

_SCRIPTS_DIR = str(_repo_root() / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.action_fingerprint import compute_action_fingerprint  # noqa: E402

# Opt-in + gate coordinates. The provider_id is the safety-flag activation index; the env var
# is the operator's per-run opt-in. Both are required, and even both together are not enough —
# the gate still verifies a real, unexpired, evidence-backed activation record.
ENV_VAR = "MVP_APPROVAL_CONSUMPTION"
OPT_IN_VALUE = "on"
PROVIDER_ID = "approval_consumption"

_CANDIDATE_TARGET_PREFIX = "memory_candidate:"


class _DryRunConsumer:
    """The inert consumer returned when the ``approval_consumption`` flag is not opted in.

    It performs nothing and refuses loudly: consuming a grant is a deliberate, gated action,
    so an un-gated attempt is a fail-closed BLOCK, not a silent no-op that looks like success.
    """

    capable = False

    def consume(self, candidate: Mapping[str, Any], **_: Any) -> dict[str, Any]:
        raise ApprovalBlocked(
            "CONSUMPTION_DISABLED",
            "approval consumption is OFF on this machine; set "
            f"{ENV_VAR}={OPT_IN_VALUE} and activate the {PROVIDER_ID!r} safety flag "
            "(scripts/activate_safety_flag.py) to spend an approval",
        )


class _CapableConsumer:
    """The consumer built only after the Safety-Flag Gate authorized ``approval_consumption``.

    It is handed the :class:`safety_gate.Authorization` (so it cannot exist before the gate
    opened) and performs the bound promotion. It computes the VALIDATED entry only; persistence
    and audit stay with :func:`consume_approval`, which orders them so an unauditable
    consumption fails closed before anything is written.
    """

    capable = True

    def __init__(self, authorization: safety_gate.Authorization):
        self._authorization = authorization

    def consume(self, candidate: Mapping[str, Any], *, promoted_by: str, reason: str,
                now: str) -> dict[str, Any]:
        # Re-verify the authorization at the moment of acting (defense in depth), then promote.
        safety_gate.assert_authorization(
            self._authorization, required_flags=[APPROVAL_CONSUMPTION],
            provider_id=PROVIDER_ID, now=now,
        )
        return promote_candidate(candidate, promoted_by=promoted_by, reason=reason, now=now)


def _find_candidate(store: WorkingMemoryStore, candidate_id: str) -> dict[str, Any] | None:
    """The live working-memory CANDIDATE with this id, or None. Only an un-promoted candidate
    in the working-memory scope is eligible — a promotion consumes a candidate, not a validated
    entry.

    Latest-wins: the working-memory store is append-only, so the *last* entry for an id is its
    current state. Consuming must revalidate against current content, not a superseded earlier
    copy — otherwise a candidate re-appended with tampered content after the approval would slip
    past the content-hash check below."""
    latest: dict[str, Any] | None = None
    for entry in store.read_all():
        if (isinstance(entry, dict)
                and entry.get("candidate_id") == candidate_id
                and entry.get("status") == CANDIDATE_STATUS
                and entry.get("scope") == CANDIDATE_SCOPE):
            latest = entry
    return latest


def select_consumer(*, now: str, root: Path) -> Any:
    """The gate chokepoint: return the capable consumer only behind the ``approval_consumption``
    safety flag, else the inert one. Split out (like ``workspace.select_writer``) so the
    production path always routes through the gate while tests can inject a consumer directly."""
    return safety_gate.select_gated(
        env_var=ENV_VAR, opt_in_value=OPT_IN_VALUE,
        flags=[APPROVAL_CONSUMPTION], provider_id=PROVIDER_ID,
        default_factory=_DryRunConsumer, gated_factory=_CapableConsumer,
        now=now, root=root,
    )


def consume_approval(
    approval_id: str,
    *,
    approval_store: ApprovalStore | None = None,
    working_memory_store: WorkingMemoryStore | None = None,
    ledger: LedgerStore | None = None,
    now: str | None = None,
    repo_root: Path | None = None,
    consumer: Any | None = None,
) -> dict[str, Any]:
    """Spend an APPROVED approval to perform its one bound promotion, or fail closed.

    Returns ``{"approval", "validated", "audit"}`` — the CONSUMED approval, the VALIDATED
    memory it produced, and the consumption audit event(s). Raises :class:`ApprovalBlocked`
    (a fail-closed BLOCK) on any of: unknown/expired/not-APPROVED/already-CONSUMED approval,
    a missing bound PermissionDecision, a drifted action fingerprint or candidate content, a
    scope other than the memory-promotion scope, a vanished candidate, or the safety flag being
    off. :class:`SafetyGateBlocked` propagates when opted in without a valid activation record.

    ``consumer`` is a test seam (mirroring ``workspace.run_write``): production callers leave
    it None and the capability is selected through the Safety-Flag Gate. ``repo_root`` locates
    the schemas/policy the produced record is validated against (default: this repo).
    """
    now = now or timeutil.utc_now_iso()
    root = repo_root if repo_root is not None else _repo_root()
    approval_store = approval_store or ApprovalStore.default()
    working_memory_store = working_memory_store or WorkingMemoryStore.default()
    ledger = ledger or LedgerStore.default()

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
    # Hot-path revalidation 1: the snapshot must still fingerprint to the bound fingerprint.
    try:
        recomputed_fp = compute_action_fingerprint(snapshot)
    except ValueError as exc:
        raise ApprovalBlocked("FINGERPRINT_UNCOMPUTABLE", str(exc)) from exc
    if recomputed_fp != approval_rec.get("action_fingerprint"):
        raise ApprovalBlocked(
            "FINGERPRINT_MISMATCH",
            "the approved action no longer fingerprints to its recorded value; consuming is refused",
        )

    # Only the memory-promotion scope is consumable — the one action the runtime can perform.
    if snapshot.get("permission_scope") != MEMORY_PROMOTION_PERMISSION_SCOPE:
        raise ApprovalBlocked(
            "SCOPE_NOT_CONSUMABLE",
            f"scope {snapshot.get('permission_scope')} has no consumption implementation",
        )

    target_ref = str(snapshot.get("target_ref", ""))
    if not target_ref.startswith(_CANDIDATE_TARGET_PREFIX):
        raise ApprovalBlocked("TARGET_NOT_CANDIDATE", f"approval target {target_ref!r} is not a memory candidate")
    candidate_id = target_ref[len(_CANDIDATE_TARGET_PREFIX):]

    candidate = _find_candidate(working_memory_store, candidate_id)
    if candidate is None:
        raise ApprovalBlocked(
            "CANDIDATE_GONE",
            f"working-memory candidate {candidate_id} is not on record (promoted, pruned, or expired)",
        )
    content = candidate.get("content")
    if not (isinstance(content, str) and content.strip()):
        raise ApprovalBlocked("CANDIDATE_EMPTY", "candidate has no content to promote")
    # Hot-path revalidation 2: the candidate's current content must still match what was approved.
    if integrity.sha256_record({"content": content}) != snapshot.get("content_sha256"):
        raise ApprovalBlocked(
            "CONTENT_CHANGED",
            "candidate content changed since the approval was granted; consuming is refused",
        )

    # Select the gated consumer. Fail-closed: the inert consumer refuses, and an opt-in without
    # a valid activation makes the gate raise SafetyGateBlocked before any consumer is built.
    if consumer is None:
        consumer = select_consumer(now=now, root=root)

    # Atomic single-use compare-and-set: re-read the stored status immediately before acting, so
    # a second consume of the same grant (or one that raced ahead) is refused. The MVP runs a
    # single process sequentially, so this closes the realistic window; the append-only store
    # then makes the spend itself durable evidence.
    latest = approval_store.get(approval_id)
    if latest is None or latest.get("status") != approval_mod.STATUS_APPROVED:
        raise ApprovalBlocked(
            "ALREADY_CONSUMED", "approval is no longer APPROVED (a concurrent consume won); refusing"
        )

    promoted_by = (approval_rec.get("approver", {}) or {}).get("approved_by") or approval_mod.REQUIRED_APPROVER
    reason = (
        f"Consumed approval {approval_id} — "
        f"{approval_rec.get('decision', {}).get('decision_reason') or 'approved by Thomas on the verified channel'}"
    )
    validated = consumer.consume(candidate, promoted_by=promoted_by, reason=reason, now=now)

    consumption_ref = f"validated_memory:{validated['validated_memory_id']}"
    consumed = approval_mod.build_consumed_record(
        approval_rec, permission_decision,
        consumed_at=now, consumption_ref=consumption_ref, now=now, repo_root=root,
    )

    # Build the audit BEFORE persisting anything: a consumption that cannot be audited fails
    # closed here, leaving no half-written state (mirrors the R5 promotion ordering).
    consumption_audit = audit.build_approval_consumption_audit(
        consumed, validated, now=now, genesis_previous_hash=ledger.last_audit_hash(), repo_root=root,
    )
    working_memory_store.append_validated([validated])
    approval_store.append([consumed])
    ledger.append_audit_events(consumption_audit)

    return {"approval": consumed, "validated": validated, "audit": consumption_audit}
