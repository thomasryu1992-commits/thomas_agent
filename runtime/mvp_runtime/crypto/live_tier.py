"""The live tier: which pool entries may spend real money, what each LIVE arm stands on, and the disarm
door, the one automatic writer of the tier, which can only take it away (crypto PR7e-8).

The tier is a field on a pool entry, beside its status (the comment above :data:`LIVE_TIER_FIELD` says
why). The promotion door writes it on every entry it installs, at LIVE or OBSERVATION, and the history
import writes OBSERVATION. Here it is read
(:func:`entry_live_tier`, :func:`live_routable_strategy_ids`, and :func:`live_arm_entries` with its
two verdicts), and taken away (:func:`disarm_live_tier`).

This was `pool`'s until crypto PR7e-8. It reads the stored pool through `pool_state`, and nothing else
of `pool`'s, so `pool` can import it: `pool` re-exports every public name here as the same object, and
its callers keep reading `pool.<name>`. The promotion door's gates (`pool_admission`, PR7e-10) import
the private :func:`_spec_rule_hash`, because their `rule_hashes_of`, which the rule-not-routed gate and
`replaced_entries` stand on, hashes a spec the same way. A patch on `pool` does not reach the functions here, which read this module's names: a test
that means to change what they see patches `live_tier` too, as the readiness test does for
:func:`live_arm_unsound`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..filelock import locked
from .paper import OCCUPYING_STATUSES
from .pool_state import pool_path, read_pool_to_disarm
from .strategy import StrategySpec
from .strategy_artifact import ARTIFACT_SHA256_FIELD

# --- the live tier (#610 Part 1) ---------------------------------------------------------
#
# Occupying a routing slot and being allowed to spend real money were **one fact** until now:
# `OCCUPYING_STATUSES` answered both, so installing a strategy into the pool armed it for live
# orders on the next 15-minute cycle. `live_entry`'s check order has no per-strategy question in
# it at all — slot 2b was Gate 0 and it was removed 2026-08-03 for being unsatisfiable — so every
# remaining live gate asks whether this RUNTIME may trade, never whether this STRATEGY may.
#
# **Why a field and not a status, which is what the proposal first said.** The lifecycle ladder
# recovers a WARNING strategy back to `PAPER_ACTIVE` (`lifecycle.evaluate_lifecycle`). Had the observation
# tier been a status, that recovery would move a strategy the operator deliberately kept off the
# money path INTO it — an automatic promotion into real money, arriving through the one mechanism
# this system says may only ever demote. The status write (`pool_transitions.apply_status_decisions`,
# which `update_statuses` wraps) writes **only** `status` and the `lifecycle_*` fields (see its
# docstring), so a separate field cannot be reached by the ladder at all: the property
# is structural rather than guarded, and there is no rank ordering anybody has to get right.
#
# Absence means OBSERVATION. Every entry promoted before this existed therefore stops being
# live-routable the moment this ships, which is the intended migration and the fail-closed
# direction: a pool that predates the distinction cannot assert the permissive half of it.
LIVE_TIER_FIELD = "live_tier"
LIVE_TIER_LIVE = "LIVE"
LIVE_TIER_OBSERVATION = "OBSERVATION"
LIVE_TIERS = frozenset({LIVE_TIER_LIVE, LIVE_TIER_OBSERVATION})
# The Thomas approval a LIVE entry was armed under (PR2b, decision 17). The promotion door writes it
# beside the tier; the disarm door removes it with the tier. The pre-order gate refuses an entry for
# a LIVE strategy that names none, so an entry armed by hand without an id is armed for nothing.
# Since PR2c-2b the gate also verifies the record the id names (`promotion.live_arm_problem`): an
# approval Thomas answered to arm this candidate at LIVE, whose window the entry was installed in.
LIVE_TIER_APPROVAL_FIELD = "live_tier_approval_id"


def entry_live_tier(entry: Mapping[str, Any]) -> str:
    """This entry's tier, defaulting to OBSERVATION — including for an unrecognised value.

    A tier this code does not know is not a reason to allow real money; it is a reason to refuse
    until somebody says what it means. Same direction as an absent field."""
    value = entry.get(LIVE_TIER_FIELD)
    return LIVE_TIER_LIVE if value == LIVE_TIER_LIVE else LIVE_TIER_OBSERVATION


def live_routable_strategy_ids(pool: Mapping[str, Any]) -> set[str]:
    """Every strategy id that may open a REAL position: occupying **and** in the live tier.

    Strictly narrower than :func:`pool.routable_strategy_ids`, and the two answer different
    questions — that one is "could this trade again at all", which the drawdown baseline needs
    and which an observation-tier strategy still answers YES to (it papers, and its live history
    stays attributable). This one is "may this spend money", and nothing infers it: the entry
    has to say so.

    A pool that cannot be read must never arrive here as an empty dict. Empty means "no strategy
    is live-routable", which refuses every entry — safe — but the caller still owes the
    distinction, because the same shape reaching :func:`pool.routable_strategy_ids` would release a
    drawdown exclusion instead."""
    return {
        str(entry.get("strategy_id"))
        for entry in (pool.get("active_strategies") or [])
        if isinstance(entry, Mapping)
        and entry.get("status") in OCCUPYING_STATUSES
        and entry.get("strategy_id")
        and entry_live_tier(entry) == LIVE_TIER_LIVE
    }


def _spec_rule_hash(spec: Any) -> str | None:
    """The rule hash of the spec an entry trades, or None when it does not parse."""
    try:
        return StrategySpec.from_dict(spec).strategy_rule_hash
    except Exception:  # noqa: BLE001 — a spec that does not parse arms nothing
        return None


def live_arm_entries(pool: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """``strategy_id -> what its LIVE arm stands on`` for every live-routable entry (PR2c-2b).

    The same membership as :func:`live_routable_strategy_ids`. Each value names:

    - the approval the entry was armed under (``None`` when it names none, which the pre-order
      gate refuses rather than infers);
    - the lineage it arms: its candidate, the rule hash its label names, and the hash of the spec
      it actually trades (``spec_rule_hash``, None when the spec does not parse);
    - the artifact it was installed as (``strategy_artifact_sha256``, PR3a), None when the entry
      predates the artifact. A pool that loaded has checked every stamp against the entry's content;
    - when the promotion door installed it, and when the disarm door last took the tier away
      (``disarmed_at``; the door never installs an entry that carries it)."""
    armed: dict[str, dict[str, Any]] = {}
    for entry in pool.get("active_strategies") or []:
        if not (isinstance(entry, Mapping) and entry.get("status") in OCCUPYING_STATUSES
                and entry.get("strategy_id") and entry_live_tier(entry) == LIVE_TIER_LIVE):
            continue
        approval = entry.get(LIVE_TIER_APPROVAL_FIELD)
        armed[str(entry.get("strategy_id"))] = {
            "approval_id": approval.strip() if isinstance(approval, str) and approval.strip() else None,
            "candidate_id": entry.get("candidate_id"),
            "strategy_rule_hash": entry.get("strategy_rule_hash"),
            "spec_rule_hash": _spec_rule_hash(entry.get("strategy_spec")),
            ARTIFACT_SHA256_FIELD: (entry.get(ARTIFACT_SHA256_FIELD)
                                    if isinstance(entry.get(ARTIFACT_SHA256_FIELD), str)
                                    and entry.get(ARTIFACT_SHA256_FIELD) else None),
            "promoted_at": entry.get("promoted_at"),
            "disarmed_at": entry.get("live_tier_updated_at"),
        }
    return armed


def live_arm_unsound(armed: Mapping[str, Any]) -> str | None:
    """Why one :func:`live_arm_entries` value arms nothing whatever approval it names, or None. Pure.

    - ``spec``: the spec it trades is not the rule its label names. The router trades the spec, and
      the approval is checked against the label, so the two must be one rule.
    - ``disarmed``: it carries the disarm door's trace, so it was put back in the tier by hand. The
      promotion door installs every entry fresh. Named before ``unbound``: it says someone edited
      the pool by hand, which is the more useful thing for an operator to read.
    - ``unbound``: it carries no artifact stamp (PR3a, decision 33). Only an entry the door
      installed as an artifact Thomas's approval names may spend money; an older entry papers."""
    if armed.get("spec_rule_hash") is None or armed.get("spec_rule_hash") != armed.get("strategy_rule_hash"):
        return "spec"
    if armed.get("disarmed_at") is not None:
        return "disarmed"
    if not armed.get(ARTIFACT_SHA256_FIELD):
        return "unbound"
    return None


def live_arm_approvals(pool: Mapping[str, Any]) -> dict[str, str | None]:
    """``strategy_id -> the approval it was armed LIVE under`` for every live-routable entry
    (:func:`live_arm_entries`). An entry :func:`live_arm_unsound` names reads as armed under none,
    so both reads an entry door makes refuse it (review of #887)."""
    return {sid: None if live_arm_unsound(armed) else armed["approval_id"]
            for sid, armed in live_arm_entries(pool).items()}


def disarm_live_tier(
    strategy_ids: Sequence[str], *, root: Path | None = None, now: str, reasons: Sequence[str] = (),
) -> int:
    """Move named entries OUT of the live tier. Locked. Returns how many actually moved.

    **The only automatic writer of ``live_tier``, and it can only ever write OBSERVATION.**
    That is a property of the signature rather than of the caller: there is no argument for a
    target tier, so no caller — present or future, correct or confused — can arm a strategy
    through here. Arming stays the operator promotion door, which is what #610 Part 1 bought
    and what this must not spend.

    It is the same asymmetry the lifecycle ladder already runs on ("auto-degradation is
    permitted; auto-reactivation is not"), applied to the tier instead of the status, and for
    the same reason: taking permission away on a machine's own judgement is safe in a way that
    handing it out is not.

    Deliberately **not** in `lifecycle`. The ladder is asserted never to name this field
    (`test_the_lifecycle_ladder_cannot_arm_a_strategy`), and that assertion is worth more than
    the convenience of one module: it means the recovery path `WARNING -> PAPER_ACTIVE` cannot
    touch the tier even by accident. A separate writer keeps the ladder's blast radius exactly
    where Part 1 put it.

    Silent on an id the pool does not hold. A caller judging live outcomes may legitimately name
    a lineage that has since been retired out of the pool entirely, and refusing there would
    turn a stale name into a cycle failure — the outcome has already been recorded, and the
    lineage is already not routable.

    Reads past an artifact stamp that no longer holds (:func:`read_pool_to_disarm`, PR3a): such a
    pool routes nothing, and the operator must be able to disarm before repairing it. The entries
    it does not name are written back exactly as read, so the pool stays refused until repaired.
    """
    ids = {str(s) for s in strategy_ids if isinstance(s, str) and s}
    if not ids:
        return 0
    path = pool_path(root)
    with locked(path.with_suffix(".lock"), code="STRATEGY_POOL_LOCKED", label="active strategy pool"):
        pool = read_pool_to_disarm(root)
        moved = 0
        for entry in pool.get("active_strategies") or []:
            if not isinstance(entry, Mapping) or str(entry.get("strategy_id")) not in ids:
                continue
            if entry_live_tier(entry) != LIVE_TIER_LIVE:
                continue
            entry[LIVE_TIER_FIELD] = LIVE_TIER_OBSERVATION
            # The approval that armed it goes with the tier: a later re-arm is a new approval.
            entry.pop(LIVE_TIER_APPROVAL_FIELD, None)
            entry["live_tier_updated_at"] = now
            entry["live_tier_reasons"] = [str(r) for r in reasons]
            moved += 1
        if moved:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)
        return moved
