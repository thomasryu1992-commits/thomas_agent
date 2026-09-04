"""Read-only views over the stores the console never rendered — the read door's fourth family.

Door API v2 (design record ``DOOR_API_V2_DESIGN_V0.1.md``, proposal 4): an orchestrator on the
far side of the read door needs to see what is scheduled, what the scheduler did, whether the
loops are alive, and where an approval it asked for stands. None of those had a renderer —
schedules had a Telegram summary (``scheduler.render_schedule_summary``), heartbeats a CLI
probe, approvals nothing but the operator's own ``/approve``. This module renders each as
text **and** as structured ``data``, and changes nothing: every function here is a read,
takes no lock the writer does not already tolerate, and refuses with a typed reason when the
store it needs was not opened for it.

What is deliberately NOT here: any schedule mutation (the scheduler CLI keeps ``add`` /
``enable`` / ``disable`` / ``remove``), and the approval record's body — the read door's rule
is that ``approvals/`` is never exposed raw, so ``approval_status`` renders a summary and
never the action snapshot or the fingerprint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import approval, heartbeat, scheduler, timeutil
from .approval_store import ApprovalStore
from .control import parse_count_arg, with_note
from .errors import ControlBlocked, MvpRuntimeError
from .scheduler import ScheduleStore
from .store import LedgerStore

SCHEDULES = "SCHEDULES"
SCHEDULER_EVENTS = "SCHEDULER_EVENTS"
HEARTBEAT = "HEARTBEAT"
APPROVAL_STATUS = "APPROVAL_STATUS"

DEFAULT_EVENTS = 20
MAX_EVENTS = 100
_STATUS_CHARS = 60

# The loops that write a heartbeat in the shipped deployment (docker-compose.yml): one
# operator, two scheduler lanes. The pre-split `scheduler` name is a retired file and is not
# probed — a MISSING there would read as an outage that is not one.
HEARTBEAT_SERVICES = (
    heartbeat.OPERATOR_SERVICE,
    heartbeat.SCHEDULER_RISK_SERVICE,
    heartbeat.SCHEDULER_MAINTENANCE_SERVICE,
)


def _lane(kind: str) -> str | None:
    """Derived, not stored: a schedule row has no lane; the partition of kinds does."""
    if kind in scheduler.RISK_KINDS:
        return scheduler.LANE_RISK
    if kind in scheduler.MAINTENANCE_KINDS:
        return scheduler.LANE_MAINTENANCE
    return None


def _clip(text: str | None) -> str | None:
    if not text:
        return None
    flat = " ".join(str(text).split())
    return flat[:_STATUS_CHARS] + ("…" if len(flat) > _STATUS_CHARS else "")


def read_schedules(schedules: ScheduleStore | None, *, now: str) -> dict[str, Any]:
    """Every enabled schedule row, the lane it belongs to, and which ones are overdue."""
    if schedules is None:
        raise ControlBlocked("SCHEDULES_UNAVAILABLE", "this door was opened without the schedule store")
    rows = schedules.list()
    late = {s.schedule_id: seconds for s, seconds in scheduler.overdue_schedules(rows, now=now)}
    enabled = [
        {
            "schedule_id": s.schedule_id, "kind": s.kind, "lane": _lane(s.kind),
            "interval_seconds": s.interval_seconds, "next_run_at": s.next_run_at,
            "last_run_at": s.last_run_at, "last_status": _clip(s.last_status),
            "overdue_seconds": late.get(s.schedule_id),
        }
        for s in sorted(rows, key=lambda s: (s.kind, s.next_run_at)) if s.enabled
    ]
    return {
        "reply": scheduler.render_schedule_summary(rows, now=now),
        "action": "SCHEDULES_LISTED",
        "data": {"enabled": enabled, "disabled_count": len(rows) - len(enabled),
                 "overdue_count": len(late), "as_of": now},
    }


def read_scheduler_events(ledger: LedgerStore | None, argument: str | None, *, now: str) -> dict[str, Any]:
    """The newest N scheduler events, read from the END of the active file and without the
    appender's lock (``LedgerStore.read_scheduler_events_tail``): the cost is the N rows, not
    the file, and the tick loop is never made to wait for a poller. The active file only —
    rotation keeps ~2000 rows and the archives are a question for the CLI."""
    if ledger is None or not hasattr(ledger, "read_scheduler_events_tail"):
        raise ControlBlocked("SCHEDULER_LEDGER_UNAVAILABLE", "this door was opened without the scheduler ledger")
    limit, note = parse_count_arg(
        argument, default=DEFAULT_EVENTS, maximum=MAX_EVENTS, usage="scheduler_events [개수]",
    )
    tail = ledger.read_scheduler_events_tail(limit)
    total = ledger.count_scheduler_events()
    if not tail:
        text = "기록된 스케줄러 이벤트가 없습니다."
    else:
        lines = [f"최근 스케줄러 이벤트 {len(tail)}개 (활성 파일 {total}개):"]
        for e in tail:
            lines.append(
                f"• {e.get('created_at', '?')} {e.get('kind', '?')} {e.get('action', '?')}"
                f" — {_clip(str(e.get('status', ''))) or ''}"
            )
        text = "\n".join(lines)
    return {
        "reply": with_note(text, note),
        "action": "SCHEDULER_EVENTS_LISTED",
        "data": {"events": tail, "count": len(tail), "active_file_total": total, "as_of": now},
    }


def read_heartbeat(*, now: str, repo_root: Path | None) -> dict[str, Any]:
    """Liveness of the three loops, from the files they stamp. Pure read; never raises."""
    checks = [heartbeat.check_heartbeat(s, now=now, root=repo_root) for s in HEARTBEAT_SERVICES]
    lines = [f"{c['service']}: {c['status']} — {c['detail']}" for c in checks]
    return {
        "reply": "\n".join(lines),
        "action": "HEARTBEAT_CHECKED",
        "data": {"services": checks, "as_of": now,
                 "all_fresh": all(c["status"] == heartbeat.FRESH for c in checks)},
    }


# Every key `read_approval_status` puts in `data`, by name, so the policy's
# `assistant_read.approval_status_exposes` (1.5.0 draft) can be pinned to it by test. The
# record's own body — snapshot, fingerprint, permission_decision_id — is deliberately absent.
APPROVAL_STATUS_FIELDS: tuple[str, ...] = (
    "approval_id", "status_recorded", "status_effective", "issued_at", "expires_at",
    "target_prefix", "permission_scope", "decided_at", "consumed_at", "as_of",
)


def read_approval_status(
    approval_store: ApprovalStore | None, argument: str | None, *, now: str,
) -> dict[str, Any]:
    """Where one approval stands — a summary, never the record.

    The store never writes EXPIRED on its own (that transition is appended only when Thomas
    answers a lapsed ask), so ``status_effective`` applies the clock here; ``status_recorded``
    is what the file says. Nothing is written back: a read that appended would be the one
    place this door mutates."""
    if approval_store is None:
        raise ControlBlocked("APPROVALS_UNAVAILABLE", "this door was opened without the approval store")
    approval_id = (argument or "").split()[0] if (argument or "").strip() else ""
    if not approval_id:
        raise ControlBlocked("USAGE", "approval_status needs an approval id: approval_status <approval_id>")
    try:
        record = approval_store.get(approval_id)
    except MvpRuntimeError as exc:
        raise ControlBlocked(exc.reason_code, str(exc)) from None
    if record is None:
        raise ControlBlocked("APPROVAL_NOT_FOUND", f"no approval {approval_id!r} at this door")
    recorded = str(record.get("status") or "")
    validity = record.get("validity") if isinstance(record.get("validity"), dict) else {}
    expires_at = validity.get("expires_at")
    effective = recorded
    if recorded == approval.STATUS_PENDING and isinstance(expires_at, str):
        try:
            if approval.is_expired(record, now=now):
                effective = approval.STATUS_EXPIRED
        except (MvpRuntimeError, ValueError, KeyError):
            pass
    snapshot = record.get("approved_action_snapshot") if isinstance(record.get("approved_action_snapshot"), dict) else {}
    target_ref = str(snapshot.get("target_ref") or "")
    consumption = record.get("consumption") if isinstance(record.get("consumption"), dict) else {}
    decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
    data = {
        "approval_id": approval_id,
        "status_recorded": recorded,
        "status_effective": effective,
        "issued_at": validity.get("issued_at"),
        "expires_at": expires_at,
        "target_prefix": target_ref.split(":", 1)[0] if target_ref else None,
        "permission_scope": snapshot.get("permission_scope"),
        "decided_at": decision.get("decided_at"),
        "consumed_at": consumption.get("consumed_at"),
        "as_of": now,
    }
    remaining = ""
    if effective == approval.STATUS_PENDING and isinstance(expires_at, str):
        try:
            seconds = int((timeutil.parse_iso(expires_at) - timeutil.parse_iso(now)).total_seconds())
            remaining = f", {max(seconds, 0) // 60}분 남음"
        except (MvpRuntimeError, ValueError):
            remaining = ""
    reply = (f"승인 {approval_id}: {effective}"
             + (f" (기록상 {recorded})" if effective != recorded else "")
             + (f" — 만료 {expires_at}{remaining}" if expires_at else ""))
    return {"reply": reply, "action": "APPROVAL_STATUS_READ", "data": data}
