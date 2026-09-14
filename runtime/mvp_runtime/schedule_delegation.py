"""Schedule changes as one service, and the assistant's pre-delegated scope (sequence 2, P09).

V0.2 §1.3 (invariant 3, conditional amendment): the assistant may change a schedule through the
dispatch door's ``schedule.propose_change`` **only inside a pre-delegated scope** the governance
policy names — non-financial kinds, a minimum interval, a ceiling on active delegated schedules,
a validity period, a per-run budget. A change inside the scope is applied and recorded like an
operator's; a change outside it is recorded as a **proposal** and not applied; a financial
schedule (every ``crypto_*`` kind, and the risk lane in particular) is refused outright — no
delegation reaches it, and no proposal record is written for it either, because the answer is
not "ask Thomas", it is "not through this door".

The scope is read from ``control_channel.assistant_schedule`` in ``governance/GOVERNANCE_POLICY.yaml``.
**Until that clause exists (policy 1.6.0, applied by Thomas), the door refuses every change:**
``SCHEDULE_DELEGATION_DISABLED``. The clause is the switch; this module is what it switches.

The container CLI (``scheduler_cli add/enable/disable/remove``, ``LOCAL_ACTOR``) runs the same
:func:`apply_change` unbounded — the validation and the recording are one code path, the scope is
the assistant's alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from . import scheduler, timeutil
from .errors import ControlBlocked, SchedulerBlocked
from .paths import repo_root as _repo_root
from .socket_door import ASSISTANT_ACTOR

POLICY_REL = "governance/GOVERNANCE_POLICY.yaml"
CLAUSE = "assistant_schedule"
ACTIONS = frozenset({"create", "enable", "disable", "remove"})
_CHANGE_KEYS = frozenset({"action", "kind", "request", "interval_seconds", "schedule_id", "reason"})
MAX_REASON_CHARS = 500
MAX_REQUEST_CHARS = 20_000

# Financial: every crypto kind, not only the risk lane. V0.2 §1.3 names the risk lane's
# `crypto_*` as the floor; the maintenance lane's crypto kinds (factory, report, proposer, data
# review, null control) and the candle archive shape what the money path trades on, so they
# are outside any delegation too. The delegated set is a closed allowlist in the policy; this
# set is the part of the complement that is refused rather than proposed.
FINANCIAL_KINDS: frozenset[str] = scheduler.RISK_KINDS | frozenset(
    k for k in scheduler.KINDS if k.startswith("crypto_") or k == scheduler.KIND_CANDLE_ARCHIVE
)


@dataclass(frozen=True)
class Delegation:
    """The scope the policy delegates: what the assistant may change without asking."""

    kinds: frozenset[str]
    min_interval_seconds: int
    max_active: int
    max_validity_days: int
    max_model_calls_per_run: int

    @classmethod
    def from_clause(cls, clause: Mapping[str, Any]) -> "Delegation":
        """Fail-closed: a clause that does not say what it delegates delegates nothing."""
        if not isinstance(clause, Mapping) or clause.get("mutation_allowed") is not True:
            raise ControlBlocked("SCHEDULE_DELEGATION_INVALID", f"{CLAUSE} does not set mutation_allowed: true")
        kinds = clause.get("delegated_kinds")
        if not isinstance(kinds, list) or not kinds or not all(isinstance(k, str) for k in kinds):
            raise ControlBlocked("SCHEDULE_DELEGATION_INVALID", f"{CLAUSE}.delegated_kinds must list the kinds")
        unknown = set(kinds) - scheduler.KINDS
        if unknown:
            raise ControlBlocked("SCHEDULE_DELEGATION_INVALID", f"{CLAUSE}.delegated_kinds names unknown kinds {sorted(unknown)}")
        financial = set(kinds) & FINANCIAL_KINDS
        if financial:
            raise ControlBlocked("SCHEDULE_DELEGATION_INVALID",
                                 f"{CLAUSE}.delegated_kinds names financial kinds {sorted(financial)}; no delegation reaches them")
        numbers = {}
        for key, floor in (("min_interval_seconds", scheduler.MIN_INTERVAL_SECONDS), ("max_active", 1),
                           ("max_validity_days", 1), ("max_model_calls_per_run", 1)):
            value = clause.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < floor:
                raise ControlBlocked("SCHEDULE_DELEGATION_INVALID", f"{CLAUSE}.{key} must be an integer >= {floor}")
            numbers[key] = value
        return cls(kinds=frozenset(kinds), **numbers)


@dataclass(frozen=True)
class InvalidDelegation:
    """A clause that is present but does not load: every change is refused with the reason, and
    the door stays up (review of P09, 2026-09-14: the error used to escape at bridge start and
    restart-loop the whole dispatch door)."""

    reason: str


def load_delegation_safely(root: Path | None = None) -> "Delegation | InvalidDelegation | None":
    try:
        return load_delegation(root)
    except ControlBlocked as exc:
        return InvalidDelegation(f"{exc.reason_code}: {exc}")


def load_delegation(root: Path | None = None) -> Delegation | None:
    """The delegated scope the committed policy names, or None while the policy has no
    ``assistant_schedule`` clause (1.5.0 and earlier): dormant, every change refused."""
    path = (root if root is not None else _repo_root()) / POLICY_REL
    try:
        policy = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ControlBlocked("POLICY_UNREADABLE", f"the governance policy cannot be read: {exc}") from exc
    clause = ((policy or {}).get("control_channel") or {}).get(CLAUSE)
    if clause is None:
        return None
    return Delegation.from_clause(clause)


@dataclass(frozen=True)
class ChangeRequest:
    action: str
    reason: str
    kind: str | None = None
    request: str = ""
    interval_seconds: int | None = None
    schedule_id: str | None = None
    enabled: bool = True                 # create only; the door always creates enabled

    @classmethod
    def parse(cls, raw: Any) -> "ChangeRequest":
        """The closed shape a change arrives in (the door's ``change`` object). Typed refusals."""
        if not isinstance(raw, Mapping):
            raise ControlBlocked("SCHEDULE_CHANGE_INVALID", "'change' must be an object")
        unexpected = set(raw) - _CHANGE_KEYS
        if unexpected:
            raise ControlBlocked("SCHEDULE_CHANGE_INVALID", f"'change' accepts {sorted(_CHANGE_KEYS)} only, not {sorted(unexpected)}")
        action = raw.get("action")
        if not isinstance(action, str) or action.strip().lower() not in ACTIONS:
            raise ControlBlocked("SCHEDULE_CHANGE_INVALID", f"'change.action' is one of {sorted(ACTIONS)}")
        action = action.strip().lower()
        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_REASON_CHARS:
            raise ControlBlocked("SCHEDULE_CHANGE_INVALID", f"'change.reason' is required (at most {MAX_REASON_CHARS} characters); it is recorded")
        if action == "create":
            kind, request, interval = raw.get("kind"), raw.get("request", ""), raw.get("interval_seconds")
            if not isinstance(kind, str) or not kind.strip():
                raise ControlBlocked("SCHEDULE_CHANGE_INVALID", "'change.kind' is required to create a schedule")
            if not isinstance(request, str) or len(request) > MAX_REQUEST_CHARS:
                raise ControlBlocked("SCHEDULE_CHANGE_INVALID", f"'change.request' must be a string of at most {MAX_REQUEST_CHARS} characters")
            if isinstance(interval, bool) or not isinstance(interval, int):
                raise ControlBlocked("SCHEDULE_CHANGE_INVALID", "'change.interval_seconds' must be an integer to create a schedule")
            if raw.get("schedule_id") is not None:
                raise ControlBlocked("SCHEDULE_CHANGE_INVALID", "'change.schedule_id' is not accepted on create — the id is minted")
            return cls(action=action, reason=reason.strip(), kind=kind.strip(), request=request, interval_seconds=interval)
        schedule_id = raw.get("schedule_id")
        if not isinstance(schedule_id, str) or not schedule_id.strip():
            raise ControlBlocked("SCHEDULE_CHANGE_INVALID", f"'change.schedule_id' is required to {action} a schedule")
        for key in ("kind", "request", "interval_seconds"):
            if raw.get(key) not in (None, ""):
                raise ControlBlocked("SCHEDULE_CHANGE_INVALID", f"'change.{key}' is not accepted on {action}; a schedule's shape is fixed")
        return cls(action=action, reason=reason.strip(), schedule_id=schedule_id.strip())


@dataclass(frozen=True)
class ChangeOutcome:
    applied: bool
    proposed: bool
    schedule: scheduler.Schedule | None
    reasons: tuple[str, ...]
    event: dict[str, Any] | None

    @property
    def verdict(self) -> str:
        return "APPLIED" if self.applied else "PROPOSED" if self.proposed else "REFUSED"


def proposal_event(change: ChangeRequest, existing: scheduler.Schedule | None, *, actor: str, now: str,
                   reasons: tuple[str, ...]) -> dict[str, Any]:
    """The record of a change asked for outside the delegated scope — what Thomas reads to
    decide, and what proves nothing was applied. Not a schedule-set mutation."""
    from .events import stamped_event
    return stamped_event(
        scheduler.SCHEDULER_EVENT_TYPE, action=scheduler.ACTION_PROPOSED,
        schedule_id=existing.schedule_id if existing is not None else None,
        kind=change.kind if existing is None else existing.kind, status="proposed", created_at=now,
        proposed_by=actor, change_action=change.action, request=change.request or (existing.request if existing else ""),
        interval_seconds=change.interval_seconds if existing is None else existing.interval_seconds,
        reason=change.reason, out_of_scope=list(reasons),
    )


def _out_of_scope(change: ChangeRequest, existing: scheduler.Schedule | None, *, store: scheduler.ScheduleStore,
                  actor: str, delegation: Delegation, now: str) -> tuple[str, ...]:
    """Why a change is outside the delegated scope; empty when it is inside. The ceiling on active
    delegated schedules is not checked here: it is enforced atomically with the write."""
    reasons: list[str] = []
    kind = change.kind if existing is None else existing.kind
    if kind not in delegation.kinds:
        reasons.append(f"kind {kind!r} is not delegated (delegated: {sorted(delegation.kinds)})")
    if existing is not None and existing.created_by != actor:
        reasons.append(f"schedule {existing.schedule_id} was created by {existing.created_by!r}, not by the assistant")
    validity_seconds = delegation.max_validity_days * 86_400
    # Renewal is Thomas's (review of P09): an expired delegated schedule is not re-enabled, and
    # the same schedule is not re-created once its validity ran out, by the assistant.
    if change.action == "enable" and existing is not None and existing.expires_at and existing.expires_at <= now:
        reasons.append(f"schedule {existing.schedule_id} expired at {existing.expires_at}; renewing it is Thomas's decision")
    if change.action == "create":
        if change.interval_seconds is not None and change.interval_seconds < delegation.min_interval_seconds:
            reasons.append(f"interval {change.interval_seconds}s is below the delegated minimum {delegation.min_interval_seconds}s")
        if change.interval_seconds is not None and change.interval_seconds >= validity_seconds:
            reasons.append(f"interval {change.interval_seconds}s would first fire after the {delegation.max_validity_days}-day "
                           "validity ends; it could never run")
        expired_twin = next((r for r in store.list() if r.created_by == actor and r.kind == kind
                             and r.request == change.request and r.expires_at and r.expires_at <= now), None)
        if expired_twin is not None:
            reasons.append(f"the same schedule ({expired_twin.schedule_id}) expired at {expired_twin.expires_at}; "
                           "renewing it is Thomas's decision")
        if kind == scheduler.KIND_WORKFLOW:
            try:
                plan = scheduler.workflow_plan_of(change.request)
            except SchedulerBlocked:
                plan = None                       # build_schedule refuses it by name below
            if plan is not None and int(plan["budget"]["max_model_calls"]) > delegation.max_model_calls_per_run:
                reasons.append(f"the plan's budget ({plan['budget']['max_model_calls']} calls) exceeds the delegated "
                               f"{delegation.max_model_calls_per_run} per run")
    return tuple(reasons)


def _ceiling_check(actor: str, delegation: Delegation, *, excluding: str | None = None):
    def check(rows: list[scheduler.Schedule]) -> list[str]:
        active = [r for r in rows if r.enabled and r.created_by == actor and r.schedule_id != excluding]
        if len(active) >= delegation.max_active:
            return [f"{len(active)} delegated schedule(s) are already active; the delegated ceiling is {delegation.max_active}"]
        return []
    return check


def apply_change(
    store: scheduler.ScheduleStore, ledger: Any, change: ChangeRequest, *, actor: str, now: str,
    delegation: Delegation | None = None, bounded: bool = False,
) -> ChangeOutcome:
    """Validate, then apply or propose one change, recording it either way.

    ``bounded=True`` (the door) applies the delegated scope: no ``delegation`` refuses everything
    (``SCHEDULE_DELEGATION_DISABLED``), a financial kind is refused (``FINANCIAL_SCHEDULE_REFUSED``),
    an out-of-scope change is recorded as a proposal and not applied. ``bounded=False`` (the
    container CLI, Thomas's own console) applies unconditionally, as it always has.
    """
    existing: scheduler.Schedule | None = None
    if change.action != "create":
        existing = next((s for s in store.list() if s.schedule_id == change.schedule_id), None)
        if existing is None:
            raise SchedulerBlocked("SCHEDULE_NOT_FOUND", f"no schedule {change.schedule_id}")
    kind = change.kind if existing is None else existing.kind
    expires_at: str | None = None
    if bounded:
        if isinstance(delegation, InvalidDelegation):
            raise ControlBlocked(
                "SCHEDULE_DELEGATION_INVALID",
                f"the governance policy's assistant_schedule clause does not load ({delegation.reason}); "
                "nothing was changed — it delegates nothing until it is corrected",
            )
        if delegation is None:
            raise ControlBlocked(
                "SCHEDULE_DELEGATION_DISABLED",
                "the governance policy delegates no schedule changes to the assistant (no assistant_schedule "
                "clause); nothing was changed — schedule changes stay in scheduler_cli",
            )
        if kind in FINANCIAL_KINDS:
            raise ControlBlocked(
                "FINANCIAL_SCHEDULE_REFUSED",
                f"{kind!r} is a financial schedule kind; no delegation reaches it and nothing was changed",
            )
        if change.action == "create" and kind not in scheduler.KINDS:
            raise SchedulerBlocked("UNKNOWN_KIND", f"schedule kind must be one of {sorted(scheduler.KINDS)}")
        reasons = _out_of_scope(change, existing, store=store, actor=actor, delegation=delegation, now=now)
        if reasons:
            return _propose(ledger, change, existing, actor=actor, now=now, reasons=reasons)
        expires_at = timeutil.plus_seconds(now, delegation.max_validity_days * 86_400) if change.action == "create" else None

    def _propose_ceiling(reasons: list[str]) -> ChangeOutcome:
        return _propose(ledger, change, existing, actor=actor, now=now, reasons=tuple(reasons))

    if change.action == "create":
        if bounded:
            # A retry of a create whose reply was lost must not make a second schedule: the same
            # enabled schedule by the same actor is the answer (review of P09, 2026-09-14).
            twin = next((r for r in store.list() if r.enabled and r.created_by == actor and r.kind == kind
                         and r.request == change.request and r.interval_seconds == change.interval_seconds), None)
            if twin is not None:
                return ChangeOutcome(applied=True, proposed=False, schedule=twin, reasons=(), event=None)
        sched = scheduler.build_schedule(
            kind=kind, request=change.request, interval_seconds=int(change.interval_seconds or 0),
            created_by=actor, now=now, reason=change.reason, enabled=change.enabled, expires_at=expires_at,
        )
        if bounded:
            reasons = store.add_checked(sched, _ceiling_check(actor, delegation))
            if reasons:
                return _propose_ceiling(reasons)
        else:
            store.add(sched)
        event = scheduler.mutation_event(scheduler.ACTION_CREATED, sched, now=now)
        if ledger is not None:
            ledger.append_scheduler_event(event)
        return ChangeOutcome(applied=True, proposed=False, schedule=sched, reasons=(), event=event)

    assert existing is not None
    if change.action == "remove":
        affected = store.remove(existing.schedule_id)
        action = scheduler.ACTION_REMOVED
    elif change.action == "enable" and bounded:
        affected, reasons = store.set_enabled_checked(existing.schedule_id, True,
                                                      _ceiling_check(actor, delegation, excluding=existing.schedule_id))
        if reasons:
            return _propose_ceiling(reasons)
        action = scheduler.ACTION_ENABLED
    else:
        affected = store.set_enabled(existing.schedule_id, change.action == "enable")
        action = scheduler.ACTION_ENABLED if change.action == "enable" else scheduler.ACTION_DISABLED
    if affected is None:
        raise SchedulerBlocked("SCHEDULE_NOT_FOUND", f"no schedule {existing.schedule_id}")
    event = scheduler.mutation_event(action, affected, now=now, previously_enabled=affected.enabled)
    if ledger is not None:
        ledger.append_scheduler_event(event)
    after = affected if change.action == "remove" else next(
        (s for s in store.list() if s.schedule_id == existing.schedule_id), affected)
    return ChangeOutcome(applied=True, proposed=False, schedule=after, reasons=(), event=event)


def _propose(ledger: Any, change: ChangeRequest, existing: scheduler.Schedule | None, *, actor: str, now: str,
             reasons: tuple[str, ...]) -> ChangeOutcome:
    event = proposal_event(change, existing, actor=actor, now=now, reasons=reasons)
    if ledger is not None:
        ledger.append_scheduler_event(event)
    return ChangeOutcome(applied=False, proposed=True, schedule=existing, reasons=reasons, event=event)


def render_outcome(change: ChangeRequest, outcome: ChangeOutcome) -> str:
    sched = outcome.schedule
    if outcome.applied:
        if change.action == "create" and sched is not None:
            tail = f" until {sched.expires_at}" if sched.expires_at else ""
            return (f"APPLIED: created schedule {sched.schedule_id} ({sched.kind}, every {sched.interval_seconds}s, "
                    f"next {sched.next_run_at}{tail})")
        return f"APPLIED: {change.action}d schedule {change.schedule_id}"
    lines = [f"PROPOSED (not applied): {change.action} " + (f"schedule {change.schedule_id}" if change.schedule_id else f"a {change.kind} schedule")
             + " is outside the delegated scope; recorded for Thomas's decision"]
    lines += [f"  - {r}" for r in outcome.reasons]
    lines.append("  Thomas applies it, if at all, with scheduler_cli in the container (§5 상신 양식).")
    return "\n".join(lines)


def is_assistant(actor: str) -> bool:
    return actor == ASSISTANT_ACTOR
