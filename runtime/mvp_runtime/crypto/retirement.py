"""Strategy retirement approval — ask Thomas before a pool member stops trading.

The R9 wiring for the one pool change the promotion door cannot express. Promotion
REPLACES the pool (or adds to it), and its replace mode rebuilds every entry with a
hardcoded ``PAPER_ACTIVE`` — so using it to drop one redundant strategy reactivates
every terminally SUSPENDED member as a side effect. Retirement is the narrow verb:
``status`` on the named entries, nothing else, through the same approval door.

Mirrors ``promotion.py`` exactly — a bound task anchors the request, the
PermissionDecision (scope ``RUNTIME_GOVERNANCE``, APPROVAL_REQUIRED) fingerprints the
exact retirement, and ``approval.build_approval_request`` turns it into the PENDING ask
Thomas answers with ``/approve``/``/reject`` on the verified control channel.

Same posture as promotion on the far side: this scope has **no consumption
implementation**. The operator door (``scripts/retire_strategies.py``) *verifies* the
APPROVED, unexpired approval against the same content hash and then acts under its own
authority. The approval is evidence of Thomas's yes, not a spendable grant.
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
from ..permission import build_strategy_retirement_permission_decision
from . import pool as pool_store
from .lifecycle import TERMINAL_STATUSES

RETIREMENT_ACTION_TYPE = "crypto.strategy_pool.retirement"
RETIREMENT_HASH_VERSION = "strategy_retirement.v1"


def retirement_content_sha256(entries: list[Mapping[str, Any]]) -> str:
    """The material identity of one retirement: which pool slots, holding which
    lineages, running which exact rules.

    All three, because ``update_statuses`` keys on the display ``strategy_id`` while
    the thing being retired is a lineage. Binding the id alone would let an approval
    retire whatever holds that name at execute time; binding the lineage alone would
    not name the slot the store actually mutates.
    """
    return integrity.sha256_value({
        "hash_version": RETIREMENT_HASH_VERSION,
        "entries": sorted(
            [str(e.get("strategy_id")), str(e.get("candidate_id") or ""), str(e.get("strategy_rule_hash") or "")]
            for e in entries
        ),
    })


def resolve_pool_entries(strategy_ids: list[str], root: Path | None = None) -> list[dict[str, Any]]:
    """The pool entries these display ids name, or fail closed.

    Refuses unknown ids and already-terminal entries at the ASK, not only at the
    install: an ask that cannot execute is worse than no ask, because it spends
    Thomas's answer on a change the next step was always going to block.
    """
    if not strategy_ids:
        raise ApprovalBlocked("RETIREMENT_EMPTY", "a retirement needs at least one strategy id")
    duplicates = sorted({s for s in strategy_ids if strategy_ids.count(s) > 1})
    if duplicates:
        raise ApprovalBlocked("RETIREMENT_DUPLICATE_SELECTOR", f"repeated strategy ids: {duplicates}")
    entries = {
        str(e.get("strategy_id")): e
        for e in (pool_store.load_active_pool(root).get("active_strategies") or [])
    }
    unknown = [s for s in strategy_ids if s not in entries]
    if unknown:
        raise ApprovalBlocked("LIFECYCLE_UNKNOWN_STRATEGY", f"not in the active pool: {unknown}")
    terminal = [s for s in strategy_ids if str(entries[s].get("status")) in TERMINAL_STATUSES]
    if terminal:
        raise ApprovalBlocked(
            "LIFECYCLE_TERMINAL_IMMUTABLE", f"already terminal, nothing to retire: {terminal}"
        )
    return [dict(entries[s]) for s in strategy_ids]


def request_retirement(
    strategy_ids: list[str],
    *,
    reason: str,
    now: str | None = None,
    ttl_minutes: int | None = None,
    repo_root: Path | None = None,
    pool_root: Path | None = None,
) -> dict[str, Any]:
    """Build the records that ASK Thomas for this retirement. Performs nothing.

    Returns ``{"entries", "task", "binding", "bound_task", "permission_decision",
    "approval_request", "content_sha256"}``; the caller persists the decision and
    request to the approval store and audits the ask, mirroring the promotion script.
    """
    now = now or timeutil.utc_now_iso()
    root = repo_root if repo_root is not None else _repo_root()
    # The pool may live under a different root only in tests (real Core for binding,
    # tmp state for stores); production passes one root.
    entries = resolve_pool_entries(strategy_ids, pool_root if pool_root is not None else root)
    content = retirement_content_sha256(entries)

    display = sorted(f"{e.get('strategy_id')}[{e.get('candidate_id') or 'no-lineage'}]" for e in entries)
    task = build_task(
        f"전략 은퇴 검토: {', '.join(display)} → SUSPENDED ({reason})",
        now=now, channel="manual", requester_type="real_thomas", requester_id="Thomas",
        authenticated=True, repo_root=root,
    )
    binding, bound = bind_task_to_core(task, repo_root=root, now=now)
    permission_decision = build_strategy_retirement_permission_decision(
        bound,
        strategy_ids=[str(e.get("strategy_id")) for e in entries],
        candidate_ids=[e.get("candidate_id") for e in entries],
        rule_hashes=[str(e.get("strategy_rule_hash") or "") for e in entries],
        reason=reason, content_sha256=content, now=now, repo_root=root,
    )
    approval_request = approval_mod.build_approval_request(
        permission_decision, now=now, ttl_minutes=ttl_minutes, repo_root=root,
    )
    return {
        "entries": entries,
        "task": task,
        "binding": binding,
        "bound_task": bound,
        "permission_decision": permission_decision,
        "approval_request": approval_request,
        "content_sha256": content,
    }


def verify_retirement_approval(
    approval: Mapping[str, Any] | None,
    *,
    strategy_ids: list[str],
    root: Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Verify an approval authorizes EXACTLY this retirement, or fail closed.

    Checks, each with its own reason code: the record exists, is APPROVED (not
    pending/rejected/expired/consumed), is inside its validity window, snapshots this
    action type — a promotion approval can never execute here — and its content hash
    matches the retirement re-derived from the CURRENT pool. A slot whose lineage or
    rules changed since Thomas answered mints a different hash and is refused.
    """
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
    if snapshot.get("action_type") != RETIREMENT_ACTION_TYPE:
        raise ApprovalBlocked(
            "APPROVAL_WRONG_ACTION", f"approval snapshots {snapshot.get('action_type')!r}, not a retirement"
        )
    if snapshot.get("content_sha256") != retirement_content_sha256(resolve_pool_entries(strategy_ids, root)):
        raise ApprovalBlocked(
            "APPROVAL_CONTENT_MISMATCH",
            "the approval binds a different retirement (slots, lineages, or rules changed)",
        )
    return dict(approval)


def apply_retirement(
    strategy_ids: list[str],
    *,
    reason: str,
    retired_by: str,
    root: Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Move the named entries to SUSPENDED through ``pool.update_statuses``. Locked.

    The store's guards are the enforcement, not this function: unknown id refused,
    terminal entry immutable, and — the property that makes this verb narrow —
    ``update_statuses`` writes ``status`` and the failure counter on named entries
    only. Membership, specs, hashes and scores are untouched by construction, so a
    retirement cannot smuggle a promotion however it is called.
    """
    from .lifecycle import operator_retirement_decision  # local: keeps the import graph flat

    now = now or timeutil.utc_now_iso()
    entries = resolve_pool_entries(strategy_ids, root)
    decisions = [
        operator_retirement_decision(e, reason=reason, retired_by=retired_by, now=now)
        for e in entries
    ]
    try:
        changed = pool_store.update_statuses(decisions, root=root, updated_by=retired_by)
    except ToolError as exc:
        raise ApprovalBlocked(exc.reason_code, str(exc)) from exc
    return {
        "retirement_version": RETIREMENT_HASH_VERSION,
        "strategy_ids": [str(e.get("strategy_id")) for e in entries],
        "candidate_ids": [e.get("candidate_id") for e in entries],
        "rule_hashes": [str(e.get("strategy_rule_hash") or "") for e in entries],
        "previous_statuses": [str(e.get("status")) for e in entries],
        "new_status": "SUSPENDED",
        "entries_changed": changed,
        "decisions": decisions,
        "reason": reason,
        "retired_by": retired_by,
        "created_at": now,
    }
