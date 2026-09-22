"""The status transitions: the lifecycle's decisions written onto the stored pool (crypto PR7e-9).

:func:`apply_status_decisions` applies a batch of status decisions to the active pool under its lock.
A decision moves an entry only while the pool still holds what it judged, the lineage in that status
(:func:`_stale_decision`). The cycle calls it in its default mode, which skips a stale decision and
applies the rest. The operator's retirement door calls it with ``all_or_nothing``, which refuses the
whole batch: ``LIFECYCLE_UNKNOWN_STRATEGY`` for an id no entry holds, :data:`LIFECYCLE_DECISION_STALE`
for the rest. :func:`update_statuses` wraps that mode and returns only the count; only tests call it.
The write changes an entry's ``status`` and ``lifecycle_*`` fields and stamps the pool header, nothing
else: never a spec, a hash, a score, membership or the live tier, which is why the lifecycle ladder
cannot arm a strategy.

This was `pool`'s until crypto PR7e-9. It reads the stored pool through `pool_state` and nothing of
`pool`'s, so `pool` can import it: `pool` re-exports the three public names as the same objects, and
their callers (the cycle, the retirement door, the tests) keep reading `pool.<name>`. A patch on
`pool` does not reach the functions here, which read this module's names.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..errors import ToolError
from ..filelock import locked
from .candidate_identity import LINEAGE_FIELDS, lineage_of
from .pool_state import load_active_pool, pool_path

# A lifecycle decision the pool write did not apply (PR3b-2, Thomas decision 36): its display id no
# longer names the lineage it judged, in the status it judged, or no longer names an entry at all.
LIFECYCLE_DECISION_STALE = "LIFECYCLE_DECISION_STALE"


def _stale_decision(decision: Mapping[str, Any], entry: Mapping[str, Any]) -> str | None:
    """Why ``decision`` may not move ``entry``, or None. A decision is about what it judged — a
    lineage, in a status — never the display id: the pool can change between the read the decision
    was made on and this locked write. The id may then name another lineage (a promotion installed
    one in its place), or the same lineage installed again fresh, or one another writer moved."""
    if not all(field in decision for field in (*LINEAGE_FIELDS, "previous_status")):
        return "the decision does not name the lineage and status it judged"
    judged, holding = lineage_of(decision), lineage_of(entry)
    if judged != holding:
        return f"judged {judged}, the pool now holds {holding}"
    current = str(entry.get("status") or "PAPER_ACTIVE")
    if str(decision.get("previous_status")) != current:
        return f"judged it {decision.get('previous_status')}, it is {current} now"
    return None


def update_statuses(
    decisions: list[dict[str, Any]], *, root: Path | None = None,
    updated_by: str = "lifecycle_agent", now: str | None = None,
) -> int:
    """:func:`apply_status_decisions`, all or nothing: returns how many entries changed status, and
    refuses the whole batch rather than skip a decision silently."""
    return apply_status_decisions(decisions, root=root, updated_by=updated_by, now=now,
                                  all_or_nothing=True)["changed"]


def apply_status_decisions(
    decisions: list[dict[str, Any]], *, root: Path | None = None,
    updated_by: str = "lifecycle_agent", now: str | None = None, all_or_nothing: bool = False,
) -> dict[str, Any]:
    """Apply lifecycle status transitions to the active pool (C10). Locked, guarded.

    Returns ``{"changed": int, "stale": [...]}``. A decision is applied only to what it judged
    (PR3b-2, Thomas decision 36): the lineage its display id names now, in the status it judged.
    One whose id names another lineage or another status by now, or no entry at all, or that does
    not say what it judged, is stale:

    - by default (the cycle's lifecycle) it is skipped and reported in ``stale``, and the rest of
      the batch is applied — a demotion held back for one stale decision would be the less safe
      outcome, and the next cycle judges the entry again;
    - ``all_or_nothing`` (an operator retirement, approved as a set) refuses the whole batch and
      writes nothing: ``LIFECYCLE_UNKNOWN_STRATEGY`` for an id no entry holds,
      :data:`LIFECYCLE_DECISION_STALE` for the rest.

    Nothing is written when no decision was applied.

    The narrowest possible pool mutation: only ``status``, the running
    ``lifecycle_consecutive_failures``, and the ``lifecycle_*`` provenance of named
    strategies change — specs, hashes, scores and membership are untouched, so this can
    never smuggle a promotion. Guards, in order, each fail-closed: a transition record that isn't
    an evaluate_lifecycle decision shape is refused; a stale decision (above); a CURRENTLY terminal
    entry is immutable (reactivation is the approval door, never this). ``changed`` counts the
    entries whose status actually changed.

    The provenance fields are written only on a status CHANGE, alongside
    ``lifecycle_updated_at`` and ``lifecycle_decision_id``, so they always describe the
    transition that produced the status the entry is currently in. Entries transitioned
    before these fields existed carry none: absent means "written by an older runtime",
    which is a different answer from an empty list and is why the reader treats it as
    unknown rather than as no reason.

    **The pool header is stamped as a PAIR.** This used to set ``updated_by`` and leave
    ``updated_at`` alone, which is worse than setting neither: the two fields describe one
    event, so a reader gets a current writer against a timestamp from whenever the
    promotion door last ran. Measured on the live host 2026-08-08 — the file was rewritten
    at 11:29 by the 15-minute pool cycle and its ``updated_at`` read ``2026-07-31T10:04:33Z``,
    earlier even than the last transition it had itself recorded (10:07:15Z). "Nothing has
    happened since Jul 31" and "eight days of cycles have run" are indistinguishable.

    ``now`` is that stamp. It defaults to the newest ``created_at_utc`` among ``decisions``,
    which is not a fallback but the better answer in the ordinary case: it is the moment this
    write's transitions were DECIDED, so the header agrees with the ``lifecycle_updated_at``
    the same call wrote onto the entries instead of being independently sourced. Every
    decision shape this accepts carries one; a caller with its own clock may pass ``now`` and
    override. The stamp lands on every write, like ``updated_by`` — "when was this file last
    written, and by whom" is the question the pair answers, and per-entry
    ``lifecycle_updated_at`` remains the one that says when a given strategy last moved."""
    from .lifecycle import TERMINAL_STATUSES  # local: it once avoided a module cycle; none remains

    if not decisions:
        return {"changed": 0, "stale": []}
    path = pool_path(root)
    with locked(path.with_suffix(".lock"), code="STRATEGY_POOL_LOCKED", label="active strategy pool"):
        pool = load_active_pool(root)
        entries = {e.get("strategy_id"): e for e in pool.get("active_strategies") or []}
        changed = applied = 0
        stale: list[dict[str, Any]] = []
        for decision in decisions:
            strategy_id = decision.get("strategy_id")
            new_status = decision.get("new_status")
            if not (isinstance(strategy_id, str) and strategy_id and isinstance(new_status, str)):
                raise ToolError("LIFECYCLE_DECISION_INVALID", "transition lacks strategy_id/new_status")
            entry = entries.get(strategy_id)
            problem = (f"no pool entry for {strategy_id}" if entry is None
                       else _stale_decision(decision, entry))
            if problem is not None:
                if all_or_nothing and entry is None:
                    raise ToolError("LIFECYCLE_UNKNOWN_STRATEGY", f"no pool entry for {strategy_id}")
                if all_or_nothing:
                    raise ToolError(LIFECYCLE_DECISION_STALE, f"{strategy_id}: {problem}")
                # Before the terminal check: a decision about another lineage or status says
                # nothing about the entry the id holds now, terminal or not.
                stale.append({"strategy_id": strategy_id, "new_status": new_status, "problem": problem})
                continue
            if str(entry.get("status")) in TERMINAL_STATUSES:
                raise ToolError(
                    "LIFECYCLE_TERMINAL_IMMUTABLE",
                    f"{strategy_id} is terminal; reactivation is the approval door, not a transition",
                )
            applied += 1
            entry["lifecycle_consecutive_failures"] = int(decision.get("consecutive_failures") or 0)
            if new_status != entry.get("status"):
                entry["status"] = new_status
                entry["lifecycle_updated_at"] = decision.get("created_at_utc")
                entry["lifecycle_decision_id"] = decision.get("strategy_lifecycle_decision_id")
                # WHY, on the entry, because the entry is what anyone reads first. Without it a
                # SUSPENDED row shows `lifecycle_consecutive_failures: 0` and nothing else, and
                # 0 means two opposite things: a performance suspension always carries a streak
                # of at least `suspend_consecutive`, while an operator retirement carries the
                # count **forward untouched** — so a strategy retired for being a duplicate
                # looks exactly like one the metrics condemned, and the difference lives only
                # in the control ledger. Measured here: five 1d entries read as demoted on zero
                # failures, and only `reasons: ["operator_retired"]` in the ledger said they
                # were duplicate rules an operator removed on purpose.
                reasons = decision.get("reasons")
                entry["lifecycle_reasons"] = (
                    [str(r) for r in reasons] if isinstance(reasons, list) else []
                )
                # Attribution when there is one. `retired_by` exists only on an operator
                # retirement, so its presence is itself the discriminator — and "who" is the
                # question a reader asks immediately after "why".
                retired_by = decision.get("retired_by")
                if isinstance(retired_by, str) and retired_by:
                    entry["lifecycle_retired_by"] = retired_by
                changed += 1
        if not applied:
            # Every decision was stale: the pool stands as read, header included.
            return {"changed": 0, "stale": stale}
        pool["updated_by"] = updated_by
        # Never widen to `or ""`: an empty stamp would overwrite a true timestamp with a
        # blank, which is the one outcome worse than the stale one being fixed here.
        stamp = now or max(
            (str(d.get("created_at_utc")) for d in decisions
             if isinstance(d.get("created_at_utc"), str) and d.get("created_at_utc")),
            default="",
        )
        if stamp:
            pool["updated_at"] = stamp
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        return {"changed": changed, "stale": stale}
