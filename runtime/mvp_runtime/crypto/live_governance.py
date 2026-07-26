"""The governance record a live order owes. Closes the gap the P5 policy gate already named.

``governance/GOVERNANCE_POLICY.yaml`` defines ``p5_policy_gate`` for
``FINANCIAL_APPROVED_TRADING_USE`` and lists its requirements. Five of the six were enforced in
code before this module existed — the grant, the confirmation phrase, the registered budget, the
kill switch, and ``pre_action_final_guard`` (``evaluate_live_order_guard``). The sixth,
**``post_action_report_and_audit``**, was not: no live-order path built a P5 PermissionDecision,
and ``audit.py`` had no financial builder, so the only trace a real order left was one self-hashed
canary-registry row. Meanwhile ``financial_transaction_execution_implemented`` had already been
flipped to ``true``.

That is the gap this closes, and it is worth naming plainly: a repository that audits a memory
promotion and a workspace file write was about to move real money with no audit event at all.

Two records, both reusing what exists — **no new contract, schema, registry, or gate**:

- the **P5 PermissionDecision** (``permission.build_live_order_permission_decision``, which had
  no production caller), bound to this exact order by its fingerprint;
- the **audit event** (``audit.build_live_order_audit``), ``OTHER``-typed with the subtype in
  ``reason_codes`` — the precedent set by memory promotion and the R8 workspace write, both of
  which are EXECUTE_AND_REPORT actions with no dedicated ``event_type``.

**A live order requires an active approved Core.** The task is bound through
``binding.bind_task_to_core``, which fails closed with ``CORE_NOT_ACTIVATED`` when no Core is
active on this machine. That is not incidental: every other governed action in this runtime is
performed under a bound Core, and an order that moves real money is the last place to make an
exception. Governance is prepared **before** the order is sent, so a refusal here costs nothing;
the audit event is written **after**, because EXECUTE_AND_REPORT is a report of what happened.

Building either record grants nothing and sends nothing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .. import audit as audit_module
from ..binding import bind_task_to_core
from ..intake import build_task
from ..permission import build_live_order_permission_decision

LIVE_GOVERNANCE_VERSION = "live_governance.v0.1"

# The role whose ceiling admits P5. It is a **candidate, non-routable** role: activating it is a
# separate ROLE_GOVERNANCE approval, and naming it here neither activates nor routes to it. The
# decision records the authority the action requires; the safety-flag grant, the confirmation
# phrase and the guard are what actually permit it.
LIVE_TRADER_ROLE_CEILING = "P5"

# Why a canary and an autonomous order are recorded distinctly: they are authorized by different
# confirmation phrases and the canary is exempt from the promotion gate, so a reader of the audit
# trail must be able to tell which capability was exercised.
PURPOSE_CANARY = "canary"
PURPOSE_AUTONOMOUS = "autonomous"


def order_fingerprint(intent: Mapping[str, Any]) -> str:
    """The identity this decision is bound to. Any material change makes a different order.

    Built over the intent's own idempotency key — the value the venue dedupes on — plus the
    order's material terms, so the governance record and the order the venue sees cannot drift
    apart, and a changed size or side is a different fingerprint rather than the same one.

    A **full** SHA-256, not the truncated idempotency key: ``permission_decision``'s closed
    schema requires ``^sha256:[0-9a-f]{64}$``, and it was right to — a 24-character prefix is an
    id, not a content hash.
    """
    key = intent.get("idempotency_key") or intent.get("client_order_id")
    if not (isinstance(key, str) and key):
        raise ValueError("live order intent has no idempotency key to fingerprint")
    blob = json.dumps(
        {
            "idempotency_key": key,
            "symbol": intent.get("symbol"),
            "side": intent.get("side"),
            "quantity": intent.get("quantity"),
            "order_notional_usdt": intent.get("order_notional_usdt"),
            "reduce_only": bool(intent.get("reduce_only")),
        },
        sort_keys=True, default=str,
    )
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def prepare_live_order_governance(
    intent: Mapping[str, Any],
    *,
    purpose: str,
    now: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """The pre-order half: a Core-bound task and its P5 PermissionDecision. No I/O beyond
    reading the local Core pointer; nothing is sent, nothing is written.

    Returns ``{bound_task, permission_decision, order_fingerprint, purpose}``. Raises rather than
    returning a partial record — a live order whose governance could not be prepared must not
    proceed, which is why callers run this *before* the adapter.
    """
    if purpose not in (PURPOSE_CANARY, PURPOSE_AUTONOMOUS):
        raise ValueError(f"live order purpose must be one of {(PURPOSE_CANARY, PURPOSE_AUTONOMOUS)}")

    fingerprint = order_fingerprint(intent)
    symbol = str(intent.get("symbol") or "")
    side = str(intent.get("side") or "")
    notional = float(intent.get("order_notional_usdt") or 0.0)

    task = build_task(
        f"Place a {purpose} live {side} order on {symbol} "
        f"({notional} USDT notional, client order id {intent.get('client_order_id')}).",
        requester_id="thomas",
        channel="api",
        # Real money leaving the account is not routine internal traffic.
        data_sensitivity="SENSITIVE",
        priority="HIGH",
        now=now,
        repo_root=repo_root,
    )
    # Fails closed with CORE_NOT_ACTIVATED when no Core is active on this machine.
    _binding, bound_task = bind_task_to_core(task, repo_root=repo_root, now=now)

    decision = build_live_order_permission_decision(
        bound_task,
        role_permission_ceiling=LIVE_TRADER_ROLE_CEILING,
        symbol=symbol,
        side=side,
        notional_usdt=notional,
        order_fingerprint=fingerprint,
        now=now,
        repo_root=repo_root,
    )
    return {
        "live_governance_version": LIVE_GOVERNANCE_VERSION,
        "purpose": purpose,
        "order_fingerprint": fingerprint,
        "bound_task": bound_task,
        "permission_decision": decision,
        "created_at": now,
    }


def report_live_order(
    governance: Mapping[str, Any],
    submit_result: Mapping[str, Any],
    *,
    guard_verdict: Mapping[str, Any],
    now: str,
    previous_hash: str | None = None,
    previous_audit_id: str | None = None,
    repo_root: Path | None = None,
) -> tuple[dict[str, Any], str]:
    """The post-order half: the audit event. ``post_action_report_and_audit``, implemented.

    Reports what the **venue** said, not what was intended — the reconcile status and the actual
    fill ride into the event, so a MISMATCH or an UNRECONCILABLE order is as auditable as a clean
    one. Returns ``(record, event_sha256)``; the caller appends it to the durable ledger.
    """
    return audit_module.build_live_order_audit(
        governance["bound_task"],
        governance["permission_decision"],
        submit_result,
        guard_verdict=guard_verdict,
        purpose=str(governance.get("purpose") or PURPOSE_AUTONOMOUS),
        now=now,
        previous_hash=previous_hash,
        previous_audit_id=previous_audit_id,
        repo_root=repo_root,
    )


__all__ = [
    "LIVE_GOVERNANCE_VERSION",
    "LIVE_TRADER_ROLE_CEILING",
    "PURPOSE_AUTONOMOUS",
    "PURPOSE_CANARY",
    "order_fingerprint",
    "prepare_live_order_governance",
    "report_live_order",
]
