"""C8b promotion approval — ask Thomas before the pool changes; verify, never spend.

The R9 wiring for strategy promotion (Crypto Pipeline C8b, approved by Thomas
2026-07-22). Mirrors the trial's ask path exactly: a real bound task anchors the
request, the PermissionDecision (scope ``RUNTIME_GOVERNANCE``, APPROVAL_REQUIRED)
fingerprints the exact promotion — candidate ids, rule hashes, add-vs-replace — and
``approval.build_approval_request`` turns it into the PENDING ask Thomas answers with
``/approve``/``/reject`` on the verified control channel.

What is deliberately different from the trial: this scope has **no consumption
implementation**. The operator promotion door (``scripts/promote_strategy_candidates.py``)
*verifies* the APPROVED, unexpired approval against the same content hash and then acts
under its own authority — the approval is evidence of Thomas's yes, not a spendable
grant. Widening R10 consumption to this scope stays a separate explicit decision.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from .. import approval as approval_mod
from .. import timeutil
from ..binding import bind_task_to_core
from ..errors import ApprovalBlocked
from ..intake import build_task
from ..paths import repo_root as _repo_root
from ..permission import build_strategy_promotion_permission_decision
from . import pool as pool_store

PROMOTION_ACTION_TYPE = "crypto.strategy_pool.promotion"
PROMOTION_HASH_VERSION = "strategy_promotion.v1"


def promotion_content_sha256(strategy_ids: list[str], rule_hashes: list[str], keep_active: bool) -> str:
    """The material identity of one promotion: which strategies, which exact rules,
    add or replace. Any change mints a different hash — and therefore a different
    approval (``invalidated_by_any_material_field_change``)."""
    return integrity.sha256_value({
        "hash_version": PROMOTION_HASH_VERSION,
        "strategy_ids": sorted(strategy_ids),
        "rule_hashes": sorted(rule_hashes),
        "keep_active": bool(keep_active),
    })


def _resolve_candidates(strategy_ids: list[str], root: Path | None) -> list[dict[str, Any]]:
    candidates = {c.get("strategy_id"): c for c in pool_store.read_candidates(root)}
    missing = [s for s in strategy_ids if s not in candidates]
    if missing:
        raise ApprovalBlocked("UNKNOWN_CANDIDATE", f"unknown candidate strategy ids: {missing}")
    resolved = [candidates[s] for s in strategy_ids]
    for c in resolved:
        if not (isinstance(c.get("strategy_rule_hash"), str) and c["strategy_rule_hash"]):
            raise ApprovalBlocked("CANDIDATE_UNHASHED", f"candidate {c.get('strategy_id')} has no rule hash")
    return resolved


def request_promotion(
    strategy_ids: list[str],
    *,
    keep_active: bool,
    now: str | None = None,
    ttl_minutes: int | None = None,
    repo_root: Path | None = None,
    candidates_root: Path | None = None,
) -> dict[str, Any]:
    """Build the records that ASK Thomas for this promotion. Performs nothing.

    Returns ``{"candidates", "task", "binding", "bound_task", "permission_decision",
    "approval_request", "content_sha256"}``; the caller persists the decision and
    request to the approval store and audits the ask (the script does, mirroring
    ``trial_cli``)."""
    now = now or timeutil.utc_now_iso()
    root = repo_root if repo_root is not None else _repo_root()
    # Candidates may live under a different root only in tests (the trial-test split:
    # real Core for binding, tmp state for stores); production passes one root.
    candidates = _resolve_candidates(strategy_ids, candidates_root if candidates_root is not None else root)
    rule_hashes = [c["strategy_rule_hash"] for c in candidates]
    content = promotion_content_sha256(strategy_ids, rule_hashes, keep_active)

    task = build_task(
        f"전략 승격 검토: {', '.join(sorted(strategy_ids))} ({'add' if keep_active else 'replace'})",
        now=now, channel="manual", requester_type="real_thomas", requester_id="Thomas",
        authenticated=True, repo_root=root,
    )
    binding, bound = bind_task_to_core(task, repo_root=root, now=now)
    permission_decision = build_strategy_promotion_permission_decision(
        bound, strategy_ids=list(strategy_ids), rule_hashes=rule_hashes,
        keep_active=keep_active, content_sha256=content, now=now, repo_root=root,
    )
    approval_request = approval_mod.build_approval_request(
        permission_decision, now=now, ttl_minutes=ttl_minutes, repo_root=root,
    )
    return {
        "candidates": candidates,
        "task": task,
        "binding": binding,
        "bound_task": bound,
        "permission_decision": permission_decision,
        "approval_request": approval_request,
        "content_sha256": content,
    }


def verify_promotion_approval(
    approval: Mapping[str, Any] | None,
    *,
    strategy_ids: list[str],
    keep_active: bool,
    root: Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Verify an approval authorizes EXACTLY this promotion, or fail closed.

    Checks, each with its own reason code: the record exists, is APPROVED (not
    pending/rejected/expired/consumed), is inside its validity window, snapshots this
    action type, and its content hash matches the promotion re-derived from the
    CURRENT candidate store — a candidate whose rules changed since Thomas approved
    mints a different hash and is refused (the R10 hot-path revalidation posture,
    without the spend). Returns the verified approval."""
    now = now or timeutil.utc_now_iso()
    if approval is None:
        raise ApprovalBlocked("APPROVAL_MISSING", "no approval record with that id")
    status = approval.get("status")
    if status != "APPROVED":
        raise ApprovalBlocked("APPROVAL_NOT_APPROVED", f"approval status is {status}, not APPROVED")
    expires_at = (approval.get("validity") or {}).get("expires_at")
    if not isinstance(expires_at, str) or timeutil.parse_iso(expires_at) <= timeutil.parse_iso(now):
        raise ApprovalBlocked("APPROVAL_EXPIRED", "the approval's validity window has passed")

    snapshot = approval.get("approved_action_snapshot") or {}
    if snapshot.get("action_type") != PROMOTION_ACTION_TYPE:
        raise ApprovalBlocked(
            "APPROVAL_WRONG_ACTION", f"approval snapshots {snapshot.get('action_type')!r}, not a promotion"
        )
    candidates = _resolve_candidates(strategy_ids, root)
    expected = promotion_content_sha256(
        strategy_ids, [c["strategy_rule_hash"] for c in candidates], keep_active
    )
    if snapshot.get("content_sha256") != expected:
        raise ApprovalBlocked(
            "APPROVAL_CONTENT_MISMATCH",
            "the approval binds a different promotion (ids, rules, or add/replace mode changed)",
        )
    return dict(approval)
