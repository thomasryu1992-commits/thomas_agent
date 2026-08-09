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
from ..errors import ApprovalBlocked, ToolError
from ..intake import build_task
from ..paths import repo_root as _repo_root
from ..permission import build_strategy_promotion_permission_decision
from . import pool as pool_store

PROMOTION_ACTION_TYPE = "crypto.strategy_pool.promotion"
# v3 — the live tier joins the hash (#610 Part 1). It has to: installing a strategy that may
# spend real money and installing one that may only paper are different asks, and an approval
# granted for the second must not be spendable on the first. Leaving the tier out would let the
# same APPROVED hash cover either, which is the material-field rule this hash exists to enforce.
PROMOTION_HASH_VERSION = "strategy_promotion.v3"


def promotion_content_sha256(
    candidate_ids: list[str], rule_hashes: list[str], keep_active: bool, live_tier: str,
) -> str:
    """The material identity of one promotion: which candidate lineages, which exact
    rules, add or replace, **and into which tier**. Any change mints a different hash — and
    therefore a different approval (``invalidated_by_any_material_field_change``). v2: keyed by
    globally unique ``candidate_id``, never the per-generation ``strategy_id``. v3: the tier.

    ``live_tier`` is required rather than defaulted. A default would be a decision about real
    money made by an argument list, and the one direction a default could fail in silently is
    the permissive one."""
    if live_tier not in pool_store.LIVE_TIERS:
        raise ApprovalBlocked(
            "PROMOTION_TIER_INVALID",
            f"live_tier {live_tier!r} is not one of {sorted(pool_store.LIVE_TIERS)}",
        )
    return integrity.sha256_value({
        "hash_version": PROMOTION_HASH_VERSION,
        "candidate_ids": sorted(candidate_ids),
        "rule_hashes": sorted(rule_hashes),
        "keep_active": bool(keep_active),
        "live_tier": live_tier,
    })


def _resolve_identity(selectors: list[str], root: Path | None) -> list[dict[str, Any]]:
    """Selector resolution via the store's single authority, as approval refusals.

    Exactly what ``promotion_content_sha256`` is a function of, and nothing else: which
    lineages the selectors name, and the rule hash each one carries. The hash check is not a
    quality gate — an unhashed row cannot be hashed at all, so both callers need it.
    """
    try:
        resolved = pool_store.resolve_candidates(selectors, root)
    except ToolError as exc:
        raise ApprovalBlocked(exc.reason_code, str(exc)) from exc
    for c in resolved:
        if not (isinstance(c.get("strategy_rule_hash"), str) and c["strategy_rule_hash"]):
            raise ApprovalBlocked("CANDIDATE_UNHASHED", f"candidate {c['candidate_id']} has no rule hash")
    return resolved


def _resolve_candidates(
    selectors: list[str], root: Path | None, *, allow_stale_cost_basis: bool = False,
    allow_unrecorded_evidence_depth: bool = False,
    allow_quarantined_derivation: bool = False,
) -> list[dict[str, Any]]:
    """Identity resolution plus the quality gates the ASK owes the install door.

    Backs ``request_promotion`` only. Verification deliberately resolves identity alone —
    see ``verify_promotion_approval``.
    """
    resolved = _resolve_identity(selectors, root)
    # Checked at the ASK, not only at the install. The execution door refuses stale-basis
    # evidence too, and an ask that cannot execute is worse than no ask: it spends Thomas's
    # answer on a promotion the next step was always going to block.
    if not allow_stale_cost_basis:
        try:
            pool_store.assert_promotable_cost_basis(resolved)
        except ToolError as exc:
            raise ApprovalBlocked(exc.reason_code, str(exc)) from exc
    # Same rule for evidence that cannot say what window it was scored over, for the same
    # reason: the execution door refuses it, so asking first only spends the answer.
    if not allow_unrecorded_evidence_depth:
        try:
            pool_store.assert_promotable_evidence_depth(resolved)
        except ToolError as exc:
            raise ApprovalBlocked(exc.reason_code, str(exc)) from exc
    # And the one axis that is not about the evidence at all: HOW the row was made. Asked at
    # the ask for the same reason as the two above — the execution door refuses it, so an ask
    # that cannot execute only spends Thomas's answer — and asked LAST because it is the one
    # that refuses nothing on today's store, so an operator reading a refusal should meet the
    # actionable ones first.
    if not allow_quarantined_derivation:
        try:
            pool_store.assert_promotable_derivation(resolved)
        except ToolError as exc:
            raise ApprovalBlocked(exc.reason_code, str(exc)) from exc
    return resolved


def request_promotion(
    selectors: list[str],
    *,
    keep_active: bool,
    # #610 Part 1 — which tier this promotion installs into. No default: the whole point of the
    # split is that arming a strategy for real money is a decision somebody makes, and a default
    # would make it for them. OBSERVATION installs a strategy that occupies its slot and papers;
    # LIVE additionally lets it open real positions.
    live_tier: str,
    now: str | None = None,
    ttl_minutes: int | None = None,
    repo_root: Path | None = None,
    candidates_root: Path | None = None,
    allow_stale_cost_basis: bool = False,
    allow_unrecorded_evidence_depth: bool = False,
    allow_duplicates: bool = False,
    allow_quarantined_derivation: bool = False,
) -> dict[str, Any]:
    """Build the records that ASK Thomas for this promotion. Performs nothing.

    ``selectors`` are candidate ids (preferred) or unambiguous strategy ids; the ask
    binds the resolved ``candidate_id`` lineages, so a later same-named candidate can
    never ride an earlier approval. Returns ``{"candidates", "task", "binding",
    "bound_task", "permission_decision", "approval_request", "content_sha256"}``; the
    caller persists the decision and request to the approval store and audits the ask
    (the script does, mirroring ``trial_cli``)."""
    now = now or timeutil.utc_now_iso()
    root = repo_root if repo_root is not None else _repo_root()
    # Candidates may live under a different root only in tests (the trial-test split:
    # real Core for binding, tmp state for stores); production passes one root.
    store_root = candidates_root if candidates_root is not None else root
    candidates = _resolve_candidates(
        selectors, store_root,
        allow_stale_cost_basis=allow_stale_cost_basis,
        allow_unrecorded_evidence_depth=allow_unrecorded_evidence_depth,
        allow_quarantined_derivation=allow_quarantined_derivation,
    )
    # Same rule again, one door earlier. Incumbents matter only when the batch ADDS to
    # the pool: a replace promotion installs exactly this batch, so yesterday's pool
    # cannot collide with it. Kept out of ``_resolve_candidates`` because it reads state
    # the selectors do not name — the pool as it stands right now — where the three gates
    # in that helper are pure functions of the rows being promoted. Both are ask-and-install
    # checks either way; neither belongs on the verification path.
    if not allow_duplicates:
        try:
            pool_store.assert_no_semantic_duplicates(
                candidates,
                incumbents=pool_store.pool_candidate_records(store_root) if keep_active else None,
            )
        except ToolError as exc:
            raise ApprovalBlocked(exc.reason_code, str(exc)) from exc
    candidate_ids = [c["candidate_id"] for c in candidates]
    rule_hashes = [c["strategy_rule_hash"] for c in candidates]
    content = promotion_content_sha256(candidate_ids, rule_hashes, keep_active, live_tier)

    display = sorted(f"{c.get('strategy_id')}[{c['candidate_id']}]" for c in candidates)
    task = build_task(
        f"전략 승격 검토: {', '.join(display)} ({'add' if keep_active else 'replace'})",
        now=now, channel="manual", requester_type="real_thomas", requester_id="Thomas",
        authenticated=True, repo_root=root,
    )
    binding, bound = bind_task_to_core(task, repo_root=root, now=now)
    permission_decision = build_strategy_promotion_permission_decision(
        bound, candidate_ids=candidate_ids,
        strategy_ids=[str(c.get("strategy_id")) for c in candidates], rule_hashes=rule_hashes,
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
    selectors: list[str],
    keep_active: bool,
    live_tier: str,
    root: Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Verify an approval authorizes EXACTLY this promotion, or fail closed.

    Checks, each with its own reason code: the record exists, is APPROVED (not
    pending/rejected/expired/consumed), is inside its validity window, snapshots this
    action type, and its content hash matches the promotion re-derived from the
    CURRENT candidate store — a candidate whose rules changed since Thomas approved
    mints a different hash and is refused (the R10 hot-path revalidation posture,
    without the spend). Returns the verified approval.

    Resolves identity ONLY — no cost-basis, evidence-depth or derivation gate. Those are
    checked at the ask (``request_promotion``, so Thomas is never asked a question the next
    step was always going to block) and at the install (``promote_strategy_candidates.run_promotion``,
    where evidence turns into money), both of which honour the operator's escapes. This door
    honours none, because it is answering a different question: the escapes are deliberately
    kept OUT of ``promotion_content_sha256``, so a check that recomputes that hash cannot also
    be a place where an escape applies. Running them here refused every promotion Thomas
    explicitly approved WITH an escape: verification resolved first, without the flag, and the
    install door's own guard block — which has the flag — was never reached. That is the same
    argument that already kept ``assert_no_semantic_duplicates`` out of the shared resolve, and
    it generalises: a gate belongs at the ask and the install, never at the identity check
    between them."""
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
    candidates = _resolve_identity(selectors, root)
    expected = promotion_content_sha256(
        [c["candidate_id"] for c in candidates],
        [c["strategy_rule_hash"] for c in candidates],
        keep_active,
        live_tier,
    )
    if snapshot.get("content_sha256") != expected:
        raise ApprovalBlocked(
            "APPROVAL_CONTENT_MISMATCH",
            "the approval binds a different promotion (ids, rules, or add/replace mode changed)",
        )
    return dict(approval)
