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

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from runtime.read_only_kernel import integrity

from .. import approval as approval_mod
from .. import timeutil
from ..binding import bind_task_to_core
from ..errors import ApprovalBlocked, MvpRuntimeError, ToolError
from ..intake import build_task
from ..paths import repo_root as _repo_root
from ..permission import (
    STRATEGY_POOL_LIVE_TARGET_REF,
    STRATEGY_POOL_TIER_LIVE,
    build_strategy_promotion_permission_decision,
)
from . import execution_stage as execution_stage_mod
from . import forward_book, forward_confirmation
from . import paper as paper_store
from .execution_stage import StageStatus, resolve_execution_stage
from . import pool as pool_store
from . import strategy_artifact as artifact_mod

PROMOTION_ACTION_TYPE = "crypto.strategy_pool.promotion"
# v4, and the jump past v3 is deliberate. TWO material fields landed independently and both
# called themselves v3: the reactivation set (#619, merged first) and the live tier (#610 Part 1,
# this one). A hash version that means two different things is worse than either change, so the
# merged shape takes the next number and carries both.
#
# The tier has to be in here for the same reason the reactivation set does: installing a strategy
# that may spend real money and installing one that may only paper are different asks, and an
# approval granted for the second must not be spendable on the first.
#
# v5 (PR3a, decisions 31-34): each candidate paired with the artifact the door will install for it.
# Until v4 the approval named ids and rule hashes as two separately sorted lists and nothing the
# router reads beside the spec, so an approval could not say which admission evidence, score or cost
# basis it was answering. A v4 approval verifies nothing after this: its hash lacks the pairs.
PROMOTION_HASH_VERSION = "strategy_promotion.v5"


def promotion_content_sha256(
    candidate_ids: list[str], rule_hashes: list[str], keep_active: bool, live_tier: str,
    reactivated_candidate_ids: Sequence[str] = (),
    *,
    artifact_sha256s: Sequence[str],
) -> str:
    """The material identity of one promotion: which candidate lineages, which exact rules, add
    or replace, **into which tier**, and **who comes back from a terminal status**. Any change
    mints a different hash — and therefore a different approval
    (``invalidated_by_any_material_field_change``).

    v2: keyed by globally unique ``candidate_id``, never the per-generation ``strategy_id``.

    **The reactivation set, because the signature was honest about a smaller effect than the one
    it authorized.** Replace mode rebuilds every entry with a hardcoded ``PAPER_ACTIVE``, so
    re-listing the incumbents to drop one returns everything the lifecycle had terminated —
    `BUILD_HISTORY` records it simulated against a copy of the real pool on 2026-07-29: 16
    reactivated, 57 lifecycle counters reset. The door was made to refuse it
    (`pool.assert_no_silent_reactivation`), which makes the act deliberate; this makes the
    approval NAME it. Empty is the ordinary answer and costs nothing.

    **The live tier, because arming a strategy for real money is a different ask.** Required
    rather than defaulted: a default would be a decision about real money made by an argument
    list, and the one direction it could fail in silently is the permissive one.

    Both are facts about the LIVE POOL rather than about the selectors, so a change between the
    ask and the execution invalidates the approval. That is intended — the effect being
    authorized really did change.

    **The artifacts, paired with their candidates (v5, PR3a).** ``artifact_sha256s[i]`` is the
    artifact the door will install for ``candidate_ids[i]`` (:mod:`strategy_artifact`): the spec,
    the admission evidence, the score, the cost basis, the risk assumptions and the evidence, in one
    hash. Required rather than defaulted, like the tier: an empty default would be the one way to
    ask for a promotion that binds no content.
    """
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
        "reactivated_candidate_ids": sorted(reactivated_candidate_ids),
        "artifacts": artifact_pairs(candidate_ids, artifact_sha256s),
    })


def artifact_pairs(candidate_ids: Sequence[str], artifact_sha256s: Sequence[str]) -> list[list[str]]:
    """``[[candidate_id, artifact_sha256], ...]`` sorted by candidate: which artifact is whose.

    The form the content hash and the signed parameters both carry. Refuses lists that are not
    one artifact per candidate, each a non-empty string."""
    if (isinstance(artifact_sha256s, (str, bytes)) or len(artifact_sha256s) != len(candidate_ids)
            or not all(isinstance(a, str) and a for a in artifact_sha256s)):
        raise ApprovalBlocked(
            "PROMOTION_ARTIFACT_INVALID",
            "a promotion names exactly one artifact hash per candidate",
        )
    return sorted([str(c), str(a)] for c, a in zip(candidate_ids, artifact_sha256s))


def content_sha256_of(
    candidates: Sequence[Mapping[str, Any]], *, keep_active: bool, live_tier: str, root: Path | None,
) -> str:
    """The content hash of promoting these resolved rows now: their ids, rules and artifacts, the
    mode, the tier, and who the pool as it stands would return from a terminal status. What the
    verification and the install door both recompute."""
    candidate_ids = [c["candidate_id"] for c in candidates]
    return promotion_content_sha256(
        candidate_ids,
        [c["strategy_rule_hash"] for c in candidates],
        keep_active,
        live_tier,
        pool_store.reactivated_candidate_ids(candidate_ids, keep_active=keep_active, root=root),
        artifact_sha256s=candidate_artifacts(candidates),
    )


def candidate_artifacts(candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    """The artifact hash the door would install for each resolved candidate, in order.

    Derived from the rows themselves, so an approval and the install that verifies it bind the
    content of the rows, not only their ids (``resolve_candidates`` keeps the latest row per id)."""
    try:
        return [artifact_mod.candidate_artifact_sha256(c) for c in candidates]
    except ToolError as exc:
        raise ApprovalBlocked(exc.reason_code, exc.reason) from exc


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


@dataclass(frozen=True)
class _GateInput:
    """What one promotion gate may read, assembled once per door by ``run_promotion_gates``."""

    candidates: list[dict[str, Any]]
    keep_active: bool
    live_tier: str
    # The pool as it WOULD stand after this install. The two shape gates judge this, never
    # the batch alone — an add-mode promotion is oversized because of its incumbents.
    entries: list[Mapping[str, Any]]
    store_root: Path | None
    # Occupying pool members, read once per door: the entry bar, the family cap and the
    # LIVE confirmation all key on the same set.
    occupying: list[dict[str, Any]]
    # The machine's execution stage, resolved by the door from its own state root (PR1c). The
    # gate below refuses a LIVE arming under a stage that admits no live entry, so an approval
    # can never install an armed strategy on a machine whose ladder says PAPER.
    execution_stage: StageStatus

    @property
    def occupying_ids(self) -> frozenset[str]:
        return frozenset(
            str(e.get("candidate_id")) for e in self.occupying if e.get("candidate_id")
        )

    def incumbent_records(self) -> list[dict[str, Any]] | None:
        # Incumbents matter only when the batch ADDS to the pool: a replace promotion
        # installs exactly this batch, so yesterday's pool cannot collide with it.
        return pool_store.pool_candidate_records(self.store_root) if self.keep_active else None


def _gate_cost_basis(g: _GateInput) -> None:
    # Evidence scored under a cost model cheaper than the venue charges cannot be believed.
    pool_store.assert_promotable_cost_basis(g.candidates)


def _gate_evidence_depth(g: _GateInput) -> None:
    # Evidence that cannot say what window it was scored over. A KNOWN shallow window is
    # ranked, never refused — only an unreadable one lands here.
    pool_store.assert_promotable_evidence_depth(g.candidates)


def _gate_semantic_duplicates(g: _GateInput) -> None:
    # The same strategy under different rule hashes takes a slot and never trades.
    pool_store.assert_no_semantic_duplicates(g.candidates, incumbents=g.incumbent_records())


def _gate_cluster_siblings(g: _GateInput) -> None:
    # One tier down, same pool question: a behaviour cluster gets ONE routing slot
    # (Thomas 5-2, 2026-08-11) — the second member's forward record is the measurement
    # the first is already making.
    pool_store.assert_no_cluster_siblings(g.candidates, incumbents=g.incumbent_records())


def _gate_entry_bar(g: _GateInput) -> None:
    # The 5-3 entry bar (Thomas 2026-08-11), charged to ENTRANTS only: a restatement of an
    # occupying lineage is not an entry, so re-arming the pre-bar pool spends no waiver.
    pool_store.assert_observation_entry_bar(
        g.candidates, occupying_candidate_ids=g.occupying_ids,
    )


def _gate_family_cap(g: _GateInput) -> None:
    # The family cap (Thomas 2026-08-11), charged to entrants like the bar above. Add mode
    # keeps every incumbent, so all of them are base; a replace pool is the batch alone, so
    # only the RESTATED incumbents still occupy after it lands.
    selected = {str(c.get("candidate_id")) for c in g.candidates}
    base = g.occupying if g.keep_active else [
        e for e in g.occupying if str(e.get("candidate_id")) in selected
    ]
    pool_store.assert_family_cap(g.candidates, occupying_entries=base)


def _gate_execution_stage(g: _GateInput) -> None:
    """Arming LIVE needs the machine to be at the rung a live entry needs (PR1c, Thomas
    decisions 1/9). It has NO escape flag: the ladder is the one authority for "may this machine
    open a real position", and an operator flag that waved it through here would be the escape
    PR1c exists to retire, one door over. Disarming stays free (`scripts/disarm_live_strategies.py`),
    and a demotion after an arming disarms nothing — it stops the entry at the guard instead."""
    if g.live_tier != pool_store.LIVE_TIER_LIVE:
        return
    if g.execution_stage.allows(execution_stage_mod.PURPOSE_LIVE_ARM):
        return
    needs = execution_stage_mod.required_stage(execution_stage_mod.PURPOSE_LIVE_ARM)
    why = (f"reads {g.execution_stage.stage}" if g.execution_stage.valid
           else f"reads READ_ONLY ({g.execution_stage.reason_code})")
    raise ApprovalBlocked(
        "EXECUTION_STAGE_TOO_LOW_TO_ARM",
        f"the execution stage {why}; arming a strategy LIVE needs {needs}. Register a transition "
        "with scripts/register_execution_stage.py (Thomas approves it), or promote at OBSERVATION",
    )


def _gate_live_confirmation(g: _GateInput) -> None:
    # The 5-1 rule (Thomas 2026-08-11, forward source revised 2026-08-29): arming LIVE
    # needs a confirmation earned on unseen data — a CONFIRMED holdout or a
    # FORWARD_CONFIRMED record from the lineage's OWN forward stream. The stream moved
    # from the routed paper book to the per-strategy forward book because the routed book
    # holds one position per context: N strategies sharing a context split a fixed
    # evidence flow N ways, which made the forward requirement a queue rather than a test
    # (best lineage: 10 trades after a month; worst: zero). The virtual stream carries the
    # same accounting (paper-kernel settlement, intent-net basis) and the judge's
    # arithmetic is untouched — only where the rows come from changed. OBSERVATION
    # installs are exactly the tier this gate exists to protect; they pass untouched.
    if g.live_tier != "LIVE":
        return
    forward_confirmation.assert_live_tier_confirmed(
        g.candidates,
        outcomes=forward_book.read_forward_outcomes(g.store_root),
        observed_lineages=len(g.occupying),
    )


def _gate_derivation(g: _GateInput) -> None:
    # The axis about neither the evidence nor the pool but the ROW: what minted it. Ordered
    # late because it refuses nothing on today's store — an operator reading a refusal
    # should meet the actionable ones first.
    pool_store.assert_promotable_derivation(g.candidates)


def _gate_pool_size_cap(g: _GateInput) -> None:
    # A pool whose routable set outgrows what the lifecycle can judge; judged on the
    # MERGED result, since in add mode the incumbents are what make a batch oversized.
    pool_store.assert_pool_within_size_cap(g.entries)


def _gate_silent_reactivation(g: _GateInput) -> None:
    # The one effect the approval's content hash names but cannot escape-gate: an install
    # returning terminal members to trading must be the operator's explicit act.
    pool_store.assert_no_silent_reactivation(list(g.entries), root=g.store_root)


@dataclass(frozen=True)
class PromotionGate:
    """One quality gate on the promotion path, named by the escape flag that skips it.

    ``PROMOTION_GATES`` below is the ONLY gate list either door runs. The ask
    (``request_promotion``) and the install (``promote_strategy_candidates.run_promotion``)
    used to keep hand-synced parallel lists in two directory trees, and they drifted: the
    size cap and the reactivation guard ran at the install alone, so ``--request`` could
    win Thomas's approval for a promotion ``--confirm`` was always going to refuse — an
    ask that cannot execute spends his answer on nothing. A gate that should run at one
    door only is now a decision to record on this roster, never an omission to discover
    at the door.
    """

    escape_flag: str
    check: Callable[[_GateInput], None]


# Ordering is operator UX, not correctness — every gate below is absolute unless escaped.
# Evidence gates lead because they are the ones an operator can act on by re-minting; the
# two pool-shape gates run on the assembled entries and come last, with reactivation after
# the size cap because it is the only gate about the entries an install leaves BEHIND.
PROMOTION_GATES: tuple[PromotionGate, ...] = (
    PromotionGate("allow_stale_cost_basis", _gate_cost_basis),
    PromotionGate("allow_unrecorded_evidence_depth", _gate_evidence_depth),
    PromotionGate("allow_duplicates", _gate_semantic_duplicates),
    PromotionGate("allow_cluster_siblings", _gate_cluster_siblings),
    PromotionGate("allow_below_entry_bar", _gate_entry_bar),
    PromotionGate("allow_family_overflow", _gate_family_cap),
    # No escape flag: the roster loop skips the `escapes` lookup entirely for a gate that names
    # none, so this one always runs. The stage is not an operator's to wave through.
    PromotionGate("", _gate_execution_stage),
    PromotionGate("allow_unconfirmed_holdout", _gate_live_confirmation),
    PromotionGate("allow_quarantined_derivation", _gate_derivation),
    PromotionGate("allow_oversized_pool", _gate_pool_size_cap),
    PromotionGate("allow_reactivation", _gate_silent_reactivation),
)


def run_promotion_gates(
    candidates: list[dict[str, Any]],
    *,
    keep_active: bool,
    live_tier: str,
    entries: list[Mapping[str, Any]],
    store_root: Path | None,
    escapes: Mapping[str, bool],
    # No default, like every other fail-closed fact on this path: a door that does not state the
    # stage must not be able to arm a strategy by omission.
    execution_stage: StageStatus,
) -> None:
    """Run every non-escaped gate on the roster, or raise ``ApprovalBlocked`` on the first
    refusal.

    Both doors call this with the same roster; ``entries`` is each door's own view of the
    post-install pool — the install passes the entries it is about to write, the ask passes
    ``predicted_pool_entries``. An escape flag missing from ``escapes`` RUNS its gate: the
    fail-closed direction, so a door that forgets to wire a new flag refuses rather than
    silently waving the gate through.
    """
    occupying = [
        e for e in (pool_store.load_active_pool(store_root).get("active_strategies") or [])
        if e.get("status") in pool_store.OCCUPYING_STATUSES
    ]
    gate_input = _GateInput(
        candidates=candidates, keep_active=keep_active, live_tier=live_tier,
        entries=entries, store_root=store_root, occupying=occupying,
        execution_stage=execution_stage,
    )
    if live_tier == pool_store.LIVE_TIER_LIVE and escapes.get("allow_unconfirmed_holdout", False):
        # Retired for LIVE (PR1c, audit FO-5): arming real money on evidence that was never
        # confirmed on unseen data was one operator flag away, with no approval able to see it —
        # the flag is not in the approval's content hash. It stays available for OBSERVATION,
        # where nothing it admits can spend money.
        raise ApprovalBlocked(
            "LIVE_ARM_ESCAPE_RETIRED",
            "--allow-unconfirmed-holdout cannot arm a strategy LIVE (retired 2026-09-16, PR1c); "
            "promote at OBSERVATION, or earn the confirmation on unseen data",
        )
    for gate in PROMOTION_GATES:
        # `gate.escape_flag` first: a gate that names no flag cannot be escaped by a caller that
        # happens to pass `{"": True}`. Structural rather than conventional (PR1c review).
        if gate.escape_flag and escapes.get(gate.escape_flag, False):
            continue
        try:
            gate.check(gate_input)
        except MvpRuntimeError as exc:
            raise ApprovalBlocked(exc.reason_code, exc.reason) from exc


def predicted_pool_entries(
    candidates: list[dict[str, Any]], *, keep_active: bool, live_tier: str,
    root: Path | None,
) -> list[dict[str, Any]]:
    """The pool as the install door would assemble it, reduced to what the shape gates read.

    ``assert_pool_within_size_cap`` counts status plus spec; ``assert_no_silent_reactivation``
    compares candidate_id plus status against the pool on disk. The install door's fuller
    entries (display-id collision handling, evidence columns) change neither answer, so the
    ask can judge the same two gates without duplicating that assembly. Add mode keeps the
    incumbents exactly as they stand — a terminal member stays terminal, which is why add
    mode can never reactivate; replace mode is the batch alone, every row ``PAPER_ACTIVE``,
    which is exactly how replace mode can.
    """
    predicted = [
        {
            "strategy_id": c.get("strategy_id"),
            "candidate_id": c.get("candidate_id"),
            "status": "PAPER_ACTIVE",
            pool_store.LIVE_TIER_FIELD: live_tier,
            "strategy_spec": c.get("strategy_spec"),
        }
        for c in candidates
    ]
    if keep_active:
        return [*(pool_store.load_active_pool(root).get("active_strategies") or []), *predicted]
    return predicted


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
    allow_cluster_siblings: bool = False,
    allow_below_entry_bar: bool = False,
    allow_family_overflow: bool = False,
    allow_unconfirmed_holdout: bool = False,
    allow_quarantined_derivation: bool = False,
    allow_oversized_pool: bool = False,
    allow_reactivation: bool = False,
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
    candidates = _resolve_identity(selectors, store_root)
    # Every quality gate, from the one shared roster the install door also runs. Checked at
    # the ASK, not only at the install: the execution door refuses the same things, and an
    # ask that cannot execute is worse than no ask — it spends Thomas's answer on a
    # promotion the next step was always going to block. That argument was already written
    # beside eight of these gates while the size cap and the reactivation guard still ran
    # at the install alone; the roster is what makes it structural.
    run_promotion_gates(
        candidates,
        keep_active=keep_active,
        live_tier=live_tier,
        entries=predicted_pool_entries(
            candidates, keep_active=keep_active, live_tier=live_tier, root=store_root,
        ),
        store_root=store_root,
        # The stage as the machine reads it now. The install door resolves its own, so an ask
        # approved at a rung the machine has since left refuses there rather than installing.
        execution_stage=resolve_execution_stage(root, now=now),
        escapes={
            "allow_stale_cost_basis": allow_stale_cost_basis,
            "allow_unrecorded_evidence_depth": allow_unrecorded_evidence_depth,
            "allow_duplicates": allow_duplicates,
            "allow_cluster_siblings": allow_cluster_siblings,
            "allow_below_entry_bar": allow_below_entry_bar,
            "allow_family_overflow": allow_family_overflow,
            "allow_unconfirmed_holdout": allow_unconfirmed_holdout,
            "allow_quarantined_derivation": allow_quarantined_derivation,
            "allow_oversized_pool": allow_oversized_pool,
            "allow_reactivation": allow_reactivation,
        },
    )
    candidate_ids = [c["candidate_id"] for c in candidates]
    rule_hashes = [c["strategy_rule_hash"] for c in candidates]
    artifacts = candidate_artifacts(candidates)
    # Read here rather than passed in: the set is a fact about the pool as it stands, which is
    # exactly what the approval must bind. `store_root` is the candidates root, so the pool
    # comes from `root` — the same place the install door will read it from.
    reactivated = pool_store.reactivated_candidate_ids(
        candidate_ids, keep_active=keep_active, root=root,
    )
    content = promotion_content_sha256(
        candidate_ids, rule_hashes, keep_active, live_tier, reactivated,
        artifact_sha256s=artifacts,
    )

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
        artifact_sha256s=artifacts,
        keep_active=keep_active, live_tier=live_tier, content_sha256=content, now=now, repo_root=root,
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
    # A LIVE install stamps `promoted_at` with this `now`, and the gate refuses an arm installed
    # outside the approval's window (`live_arm_problem`). Refuse here what it would refuse there,
    # so the door never installs an arm that can never trade (review of #887).
    if live_tier == pool_store.LIVE_TIER_LIVE:
        answered, expires = _arm_window(approval)
        if answered is None or expires is None or not answered <= timeutil.parse_iso(now) < expires:
            raise ApprovalBlocked(
                "APPROVAL_OUTSIDE_ARM_WINDOW",
                "this install's clock is not inside the approval's window (answered at or before it, "
                "expiring after it); run the promotion again",
            )
    candidates = _resolve_identity(selectors, root)
    # Recomputed from the pool as it stands NOW, deliberately not read back off the snapshot:
    # a set carried over from the ask would let the reactivation change between the two and
    # still verify, which is the whole gap this field closes. A lifecycle transition in
    # between is therefore a refusal, and the right one — the effect being authorized changed.
    # The artifacts likewise come from the rows as they stand now (PR3a): a row whose content is
    # no longer what was asked about mints another artifact, and the approval does not verify.
    if snapshot.get("content_sha256") != content_sha256_of(
            candidates, keep_active=keep_active, live_tier=live_tier, root=root):
        raise ApprovalBlocked(
            "APPROVAL_CONTENT_MISMATCH",
            "the approval binds a different promotion (ids, rules, artifacts, add/replace mode, or "
            "which terminal members it returns to trading changed)",
        )
    if live_tier == pool_store.LIVE_TIER_LIVE:
        # The order-time check reads the pairs off the signed parameters, not the content hash
        # (`live_arm_problem`), so a LIVE install they do not name would arm nothing. Refused here,
        # like the window above: the door never installs an arm the gate would refuse.
        signed = (snapshot.get("normalized_parameters") or {}).get("artifacts")
        pairs = [p for p in signed if isinstance(p, list)] if isinstance(signed, list) else []
        unsigned = sorted(c["candidate_id"] for c, a in zip(candidates, candidate_artifacts(candidates))
                          if [c["candidate_id"], a] not in pairs)
        if unsigned:
            raise ApprovalBlocked(
                "APPROVAL_ARTIFACT_UNSIGNED",
                f"the approval's signed parameters do not pair these candidates with the artifacts "
                f"they install as: {unsigned}",
            )
    return dict(approval)


# --- the arming approval at order time (PR2c-2b) ------------------------------------------------

# Why a pool entry armed LIVE is not backed by the approval it names, in the order they are checked.
LIVE_ARM_APPROVAL_MISSING = "LIVE_ARM_APPROVAL_MISSING"
LIVE_ARM_APPROVAL_NOT_APPROVED = "LIVE_ARM_APPROVAL_NOT_APPROVED"
LIVE_ARM_APPROVER_UNVERIFIED = "LIVE_ARM_APPROVER_UNVERIFIED"
LIVE_ARM_APPROVAL_NOT_AN_ARM = "LIVE_ARM_APPROVAL_NOT_AN_ARM"
LIVE_ARM_APPROVAL_ALTERED = "LIVE_ARM_APPROVAL_ALTERED"
LIVE_ARM_APPROVAL_OTHER_CANDIDATE = "LIVE_ARM_APPROVAL_OTHER_CANDIDATE"
# PR3a (decision 33): the approval pairs no artifact with any candidate (it predates v5), or not
# this entry's artifact with this entry's candidate.
LIVE_ARM_APPROVAL_UNBOUND = "LIVE_ARM_APPROVAL_UNBOUND"
LIVE_ARM_APPROVAL_OTHER_ARTIFACT = "LIVE_ARM_APPROVAL_OTHER_ARTIFACT"
LIVE_ARM_INSTALLED_OUTSIDE_APPROVAL = "LIVE_ARM_INSTALLED_OUTSIDE_APPROVAL"


def _parse_instant(value: Any) -> Any:
    """``value`` as an aware instant, or None for anything else (a naive stamp included)."""
    if not isinstance(value, str):
        return None
    try:
        return timeutil.parse_iso(value)
    except (ValueError, TypeError, OverflowError):
        return None


def _arm_window(approval: Mapping[str, Any]) -> tuple[Any, Any]:
    """``(answered, expires)``: the window a LIVE arm must be installed in. Either is None when
    it cannot be read. The expiry is the earlier of the approval's own and the one its signed
    snapshot names, so an edited ``validity`` cannot stretch the window past what was fingerprinted."""
    decision = approval.get("decision") if isinstance(approval.get("decision"), Mapping) else {}
    validity = approval.get("validity") if isinstance(approval.get("validity"), Mapping) else {}
    snapshot = approval.get("approved_action_snapshot")
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    answered = _parse_instant(decision.get("decided_at"))
    ends = [_parse_instant(validity.get("expires_at")), _parse_instant(snapshot.get("expires_at"))]
    return answered, (None if None in ends else min(ends))


def _listed(value: Any) -> tuple[str, ...]:
    """The strings of a list; anything else lists nothing (a string is not a list of ids)."""
    return tuple(v for v in value if isinstance(v, str)) if isinstance(value, list) else ()


def live_arm_problem(
    approval: Mapping[str, Any] | None,
    *,
    approval_id: str,
    candidate_id: Any,
    strategy_rule_hash: Any,
    strategy_artifact_sha256: Any,
    promoted_at: Any,
) -> str | None:
    """Why ``approval`` does not back a pool entry armed LIVE under ``approval_id``, or None. Pure.

    The promotion door verified the approval when it installed the entry
    (:func:`verify_promotion_approval`). This is the order-time check that the entry still stands on
    that kind of approval (PR2c-2b). It does not re-derive the content hash: that hash names the
    members the promotion returned to trading, which is the pool as it stood at install and moves
    after it. It checks instead:

    - the record exists, is APPROVED, and names Thomas, verified on the private control channel.
      A promotion approval is verified and never consumed, and only a PENDING approval expires, so
      an approved one stays APPROVED;
    - it approves a promotion into the live tier;
    - its snapshot still fingerprints to the recorded value, and ``approval_id`` is the id that
      fingerprint derives. Neither the snapshot nor the id changed after the answer;
    - the entry's candidate and rule hash are among the ones it approved;
    - it pairs the entry's artifact with the entry's candidate (PR3a, decision 33). An approval
      that pairs none predates the artifact and arms nothing;
    - the entry was installed after the answer and before the approval expired
      (:func:`_arm_window`), as the promotion door requires. ``promoted_at`` is what the pool says;
      the door writes it, and so does anyone who writes the pool.

    Its validity window is not checked against now: the arm outlives the ask by design. What this
    cannot see is a pool edited to name an approval the lineage once had: a promotion approval is
    never consumed, so one granted before a disarm still verifies (the gate refuses an entry that
    carries the disarm door's trace, `pool.live_arm_approvals`)."""
    # `approval`, imported above, puts the repository's `lib/` on the path this helper lives on.
    from lib.action_fingerprint import compute_action_fingerprint

    if not isinstance(approval, Mapping):
        return LIVE_ARM_APPROVAL_MISSING
    if approval.get("status") != approval_mod.STATUS_APPROVED:
        return LIVE_ARM_APPROVAL_NOT_APPROVED
    approver = approval.get("approver") if isinstance(approval.get("approver"), Mapping) else {}
    if (approver.get("approved_by") != approval_mod.REQUIRED_APPROVER
            or approver.get("verification_status") != "VERIFIED"
            or approver.get("identity_verification_method") != approval_mod.TELEGRAM_VERIFICATION_METHOD):
        return LIVE_ARM_APPROVER_UNVERIFIED
    snapshot = approval.get("approved_action_snapshot")
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    content = snapshot.get("normalized_parameters")
    content = content if isinstance(content, Mapping) else {}
    if (snapshot.get("action_type") != PROMOTION_ACTION_TYPE
            or snapshot.get("target_ref") != STRATEGY_POOL_LIVE_TARGET_REF
            or content.get("live_tier") != STRATEGY_POOL_TIER_LIVE):
        return LIVE_ARM_APPROVAL_NOT_AN_ARM
    try:
        fingerprint = compute_action_fingerprint(dict(snapshot))
    except (ValueError, TypeError):
        return LIVE_ARM_APPROVAL_ALTERED
    if (fingerprint != approval.get("action_fingerprint")
            or approval.get("approval_id") != approval_id
            or approval_id != integrity.short_id("approval", {"action_fingerprint": fingerprint})):
        return LIVE_ARM_APPROVAL_ALTERED
    if (candidate_id not in _listed(content.get("candidate_ids"))
            or strategy_rule_hash not in _listed(content.get("rule_hashes"))):
        return LIVE_ARM_APPROVAL_OTHER_CANDIDATE
    pairs = content.get("artifacts")
    if not isinstance(pairs, list):
        return LIVE_ARM_APPROVAL_UNBOUND
    if not (isinstance(candidate_id, str) and isinstance(strategy_artifact_sha256, str)
            and strategy_artifact_sha256
            and [candidate_id, strategy_artifact_sha256] in [p for p in pairs if isinstance(p, list)]):
        return LIVE_ARM_APPROVAL_OTHER_ARTIFACT
    answered, expires = _arm_window(approval)
    installed = _parse_instant(promoted_at)
    if answered is None or expires is None or installed is None or not answered <= installed < expires:
        return LIVE_ARM_INSTALLED_OUTSIDE_APPROVAL
    return None
