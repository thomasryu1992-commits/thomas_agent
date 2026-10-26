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

``lane_digest`` (review D8, 2026-09-26) is the one read here that is not a store the console
never rendered but a fold over the run ledger: the per-lane counts ``scripts.lane_digest`` prints,
so the assistant can hand Thomas the weekly lane evidence the lane-removal rule reads. It is
carried dormant — the read door refuses it until the committed policy lists it
(``read_bridge.POLICY_GATED_READS``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from . import approval, control, heartbeat, lane_digest, scheduler, timeutil
from .approval_store import ApprovalStore
from .control import parse_count_arg, with_note
from .errors import ControlBlocked, MvpRuntimeError
from .scheduler import ScheduleStore
from .store import LedgerStore

SCHEDULES = "SCHEDULES"
SCHEDULER_EVENTS = "SCHEDULER_EVENTS"
HEARTBEAT = "HEARTBEAT"
APPROVAL_STATUS = "APPROVAL_STATUS"
LANE_DIGEST = "LANE_DIGEST"

DEFAULT_EVENTS = 20
MAX_EVENTS = 100
# A week is the digest's cadence; a month is the widest window this door scans. The scan holds the
# record ledger's append lock for its length (``iter_records_with_archive``), and the assistant can
# call it whenever it likes, so the ceiling bounds what one call can cost a writer. The 60-day D8
# verdict reads the CLI (``scripts.lane_digest --days 60``), which runs where Thomas runs it.
DEFAULT_DIGEST_DAYS = 7
MAX_DIGEST_DAYS = 31
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


def read_schedules(schedules: ScheduleStore | None, *, now: str, projection: str | None = None) -> dict[str, Any]:
    """Every enabled schedule row, the lane it belongs to, and which ones are overdue.

    ``projection="v1"`` answers with the closed reply contract instead (`_projected_schedules`)."""
    if schedules is None:
        raise ControlBlocked("SCHEDULES_UNAVAILABLE", "this door was opened without the schedule store")
    rows = schedules.list()
    if projection is not None:
        return _projected_schedules(rows, now=now, projection=projection)
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


def read_scheduler_events(ledger: LedgerStore | None, argument: str | None, *, now: str,
                          projection: str | None = None) -> dict[str, Any]:
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
    if projection is not None:
        return _projected_scheduler_events(tail, total=total, note=note, now=now, projection=projection)
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


# --- reply projection v1 (D-11 c1; why: docs/history/2026-10-16-claude-read-door-profile.md) ---
#
# Decision state: c1 confirmed by Thomas on 2026-10-16 for the repository implementation only. Implemented, not
# approved for any door in operation.
#
# A door that serves a less trusted reader than the assistant answers `schedules` and
# `scheduler_events` from rows rebuilt out of closed lists, never from the stored rows themselves.
# What is NOT handed on: the stored `status` (a factory child's failure carries `"<type>: <message>"`
# there, `scheduler._collect_factory_child`), a schedule's `request`/`reason` text, a removal event's
# `last_status`, a proposal's `proposed_by`, integrity fields, and any key this list does not name —
# including keys added after this was written. The projection limits what LEAVES the door; the ledger
# keeps what it stored (that is decision e, not this). Off unless the door is opened with it, so the
# assistant's own door answers exactly as before.
#
# Where the protection is: in building each row out of closed lists (`project_scheduler_event`,
# `project_last_status`). The JSON Schemas below check the SHAPE of what was built — closed keys,
# enums, anchored patterns, lengths — and refuse a projection defect whole. A schema cannot tell
# that a well-formed value is the wrong one, so it is a defect detector behind the projection, not
# a second projection. Patterns end in `\Z`, not `$`, which would also match before a final newline.

PROJECTION_V1 = "v1"
PROJECTIONS: frozenset[str] = frozenset({PROJECTION_V1})
PROJECTION_PARTIAL = "v1-partial"

# Closed lists, read off the scheduler (pinned against its constants by test).
EVENT_ACTIONS: frozenset[str] = frozenset({
    scheduler.ACTION_STARTED, "fired", "failed", scheduler.ACTION_ABANDONED, "skipped",
    scheduler.ACTION_DEFERRED, "gap_detected", scheduler.ACTION_CREATED, scheduler.ACTION_ENABLED,
    scheduler.ACTION_DISABLED, scheduler.ACTION_REMOVED, scheduler.ACTION_EXPIRED, scheduler.ACTION_PROPOSED,
})
_MUTATION_ACTIONS = frozenset({scheduler.ACTION_CREATED, scheduler.ACTION_ENABLED, scheduler.ACTION_DISABLED,
                               scheduler.ACTION_REMOVED, scheduler.ACTION_EXPIRED})
_MUTATION_STATUSES = frozenset({"enabled", "disabled", "removed"})
CHANGE_ACTIONS: frozenset[str] = frozenset({"create", "enable", "disable", "remove"})
_FIXED_OUTCOMES: dict[tuple[str, str], str] = {
    (scheduler.ACTION_STARTED, "running"): "started",
    (scheduler.ACTION_ABANDONED, "abandoned_mid_run"): "abandoned",
    ("skipped", "skipped_not_active"): "skipped:not_active",
    (scheduler.ACTION_DEFERRED, "deferred_pass_budget"): "deferred:pass_budget",
    (scheduler.ACTION_DEFERRED, "deferred_factory_child_running"): "deferred:factory_child_running",
    (scheduler.ACTION_PROPOSED, "proposed"): "proposed",
}
_SCHEDULE_ID = re.compile(r"^schedule_[0-9a-f]{20}\Z")
_RUN_ID = re.compile(r"^srun_[0-9a-f]{20}\Z")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}\Z")
_FAILED = re.compile(r"^failed:([A-Z][A-Z0-9_]{2,63})(?::.*)?\Z", re.S)
_OVERDUE = re.compile(r"^overdue_seconds=(\d{1,10})\Z")
_INT_FIELDS = ("duration_ms", "interval_seconds", "pass_elapsed_ms", "pass_budget_ms")
_MAX_OUT_OF_SCOPE = 10


def _nonneg_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _failed_outcome(status: Any) -> str | None:
    match = _FAILED.match(status) if isinstance(status, str) else None
    return f"failed:{match.group(1)}" if match else None


def project_scheduler_event(raw: Any) -> dict[str, Any]:
    """One stored scheduler event as the v1 contract row: closed fields, a normalised `outcome`,
    `projection` naming whether anything the contract carries had to be dropped."""
    if not isinstance(raw, Mapping):
        return {"action": "UNKNOWN", "kind": "UNKNOWN", "outcome": "UNKNOWN", "projection": PROJECTION_PARTIAL}
    partial = False
    action = raw.get("action") if raw.get("action") in EVENT_ACTIONS else None
    kind = raw.get("kind") if raw.get("kind") in scheduler.KINDS else None
    row: dict[str, Any] = {"action": action or "UNKNOWN", "kind": kind or "UNKNOWN"}
    status = raw.get("status")
    if action is None or kind is None:
        outcome = "UNKNOWN"
    elif action == "fired":
        outcome = "fired"                                  # the per-kind summary never leaves
    elif action == "failed":
        outcome = _failed_outcome(status) or "failed:UNKNOWN"
    elif action == "gap_detected":
        outcome = "gap_detected"
        match = _OVERDUE.match(status) if isinstance(status, str) else None
        if match:
            row["overdue_seconds"] = int(match.group(1))
        else:
            partial = True
    elif action in _MUTATION_ACTIONS:
        outcome = action if status in _MUTATION_STATUSES else f"{action}:UNKNOWN"
    else:
        outcome = _FIXED_OUTCOMES.get((action, status)) if isinstance(status, str) else None
        outcome = outcome or f"{action}:UNKNOWN"
    partial = partial or outcome.endswith("UNKNOWN")
    row["outcome"] = outcome

    def keep(name: str, ok: bool, value: Any) -> None:
        nonlocal partial
        if name not in raw:
            return
        if ok:
            row[name] = value
        else:
            partial = True

    for name, pattern in (("schedule_id", _SCHEDULE_ID), ("schedule_run_id", _RUN_ID)):
        value = raw.get(name)
        keep(name, isinstance(value, str) and bool(pattern.match(value)), value)
    for name in ("created_at", "started_at"):
        value = raw.get(name)
        keep(name, isinstance(value, str) and bool(_ISO.match(value)), value)
    for name in _INT_FIELDS:
        value = _nonneg_int(raw.get(name))
        keep(name, value is not None, value)
    if "previously_enabled" in raw:
        keep("previously_enabled", isinstance(raw["previously_enabled"], bool), raw["previously_enabled"])
    if action == scheduler.ACTION_PROPOSED:
        keep("change_action", raw.get("change_action") in CHANGE_ACTIONS, raw.get("change_action"))
        if "out_of_scope" in raw:
            codes = raw["out_of_scope"]
            good = ([c for c in codes if isinstance(c, str) and _CODE.match(c)]
                    if isinstance(codes, list) else [])
            if not isinstance(codes, list) or len(good) != len(codes) or len(good) > _MAX_OUT_OF_SCOPE:
                partial = True
            row["out_of_scope"] = good[:_MAX_OUT_OF_SCOPE]
    row["projection"] = PROJECTION_PARTIAL if partial else PROJECTION_V1
    return row


def project_last_status(last_status: Any) -> tuple[str | None, bool]:
    """`(last_outcome, partial)`. `last_status` is written only when a run ends (a failure, the
    factory child's running marker, or a fired run's free-form summary), so any other string is a
    success summary and folds to ``fired``."""
    if last_status is None:
        return None, False
    if not isinstance(last_status, str):
        return "UNKNOWN", True
    if last_status.startswith("failed:"):
        outcome = _failed_outcome(last_status)
        return (outcome, False) if outcome else ("failed:UNKNOWN", True)
    if last_status == scheduler.STATUS_FACTORY_RUNNING:
        return "running", False
    return "fired", False


_TIME_SCHEMA = {"type": "string", "pattern": _ISO.pattern}
_EVENT_ROW_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["action", "kind", "outcome", "projection"],
    "properties": {
        "action": {"enum": sorted(EVENT_ACTIONS) + ["UNKNOWN"]},
        "kind": {"enum": sorted(scheduler.KINDS) + ["UNKNOWN"]},
        "outcome": {"type": "string", "maxLength": 72, "pattern": (
            r"^(fired|started|abandoned|proposed|gap_detected|skipped:not_active|deferred:pass_budget"
            r"|deferred:factory_child_running|created|enabled|disabled|removed|expired|UNKNOWN"
            r"|failed:[A-Z][A-Z0-9_]{2,63}|[a-z_]+:UNKNOWN)\Z")},
        "projection": {"enum": [PROJECTION_V1, PROJECTION_PARTIAL]},
        "schedule_id": {"type": "string", "pattern": _SCHEDULE_ID.pattern},
        "schedule_run_id": {"type": "string", "pattern": _RUN_ID.pattern},
        "created_at": _TIME_SCHEMA, "started_at": _TIME_SCHEMA,
        **{name: {"type": "integer", "minimum": 0} for name in (*_INT_FIELDS, "overdue_seconds")},
        "previously_enabled": {"type": "boolean"},
        "change_action": {"enum": sorted(CHANGE_ACTIONS)},
        "out_of_scope": {"type": "array", "maxItems": _MAX_OUT_OF_SCOPE,
                         "items": {"type": "string", "pattern": _CODE.pattern}},
    },
}
_EVENTS_DATA_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["events", "count", "active_file_total", "as_of", "projection"],
    "properties": {
        "events": {"type": "array", "maxItems": MAX_EVENTS, "items": _EVENT_ROW_SCHEMA},
        "count": {"type": "integer", "minimum": 0, "maximum": MAX_EVENTS},
        "active_file_total": {"type": "integer", "minimum": 0},
        "as_of": _TIME_SCHEMA,
        "projection": {"enum": [PROJECTION_V1, PROJECTION_PARTIAL]},
    },
}
_SCHEDULE_ROW_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["kind", "last_outcome"],
    "properties": {
        "kind": {"enum": sorted(scheduler.KINDS) + ["UNKNOWN"]},
        "schedule_id": {"type": "string", "pattern": _SCHEDULE_ID.pattern},
        "lane": {"enum": [scheduler.LANE_RISK, scheduler.LANE_MAINTENANCE]},
        "interval_seconds": {"type": "integer", "minimum": 0},
        "next_run_at": _TIME_SCHEMA, "last_run_at": _TIME_SCHEMA,
        "last_outcome": {"anyOf": [{"type": "null"}, {"type": "string", "pattern":
                         r"^(fired|running|UNKNOWN|failed:UNKNOWN|failed:[A-Z][A-Z0-9_]{2,63})\Z"}]},
    },
}
_SCHEDULES_DATA_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["enabled", "disabled_count", "as_of", "projection"],
    "properties": {
        "enabled": {"type": "array", "items": _SCHEDULE_ROW_SCHEMA},
        "disabled_count": {"type": "integer", "minimum": 0},
        "as_of": _TIME_SCHEMA,
        "projection": {"enum": [PROJECTION_V1, PROJECTION_PARTIAL]},
    },
}


def require_projection(projection: str) -> None:
    """Refuse a projection name this module does not define (the one place that raises it)."""
    if projection not in PROJECTIONS:
        raise ControlBlocked("PROJECTION_UNKNOWN", f"no reply projection named {projection!r}; nothing was read")


def _checked(data: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """The last gate before a projected reply leaves: its closed shape. A projection defect is
    refused whole and the refusal names no field and no value."""
    import jsonschema  # local: only a projecting door pays for it

    if not jsonschema.Draft202012Validator(schema).is_valid(data):
        raise ControlBlocked("REPLY_SCHEMA_INVALID",
                             "the projected reply did not match its closed shape; nothing was returned")
    return data


def _projected_scheduler_events(tail: list[Any], *, total: int, note: str | None, now: str,
                                projection: str) -> dict[str, Any]:
    require_projection(projection)
    rows = [project_scheduler_event(e) for e in tail]
    partial = any(r["projection"] == PROJECTION_PARTIAL for r in rows)
    data = _checked({"events": rows, "count": len(rows), "active_file_total": total, "as_of": now,
                     "projection": PROJECTION_PARTIAL if partial else PROJECTION_V1}, _EVENTS_DATA_SCHEMA)
    if not rows:
        text = "기록된 스케줄러 이벤트가 없습니다."
    else:
        lines = [f"최근 스케줄러 이벤트 {len(rows)}개 (활성 파일 {total}개):"]
        lines += [f"• {r.get('created_at', '-')} {r['kind']} {r['action']} — {r['outcome']}" for r in rows]
        text = "\n".join(lines)
    return {"reply": with_note(text, note), "action": "SCHEDULER_EVENTS_LISTED", "data": data}


def _projected_schedules(rows: list[Any], *, now: str, projection: str) -> dict[str, Any]:
    require_projection(projection)
    enabled: list[dict[str, Any]] = []
    partial = False
    for s in sorted(rows, key=lambda s: (s.kind, s.next_run_at)):
        if not s.enabled:
            continue
        outcome, lossy = project_last_status(s.last_status)
        kind = s.kind if s.kind in scheduler.KINDS else "UNKNOWN"
        partial = partial or lossy or kind == "UNKNOWN"
        row: dict[str, Any] = {"kind": kind, "last_outcome": outcome}
        if isinstance(s.schedule_id, str) and _SCHEDULE_ID.match(s.schedule_id):
            row["schedule_id"] = s.schedule_id
        else:
            partial = True
        lane = _lane(s.kind)
        if lane is not None:
            row["lane"] = lane
        interval = _nonneg_int(s.interval_seconds)
        if interval is not None:
            row["interval_seconds"] = interval
        for name in ("next_run_at", "last_run_at"):
            value = getattr(s, name)
            if isinstance(value, str) and _ISO.match(value):
                row[name] = value
            elif value is not None:
                partial = True
        enabled.append(row)
    data = _checked({"enabled": enabled, "disabled_count": len(rows) - len(enabled), "as_of": now,
                     "projection": PROJECTION_PARTIAL if partial else PROJECTION_V1}, _SCHEDULES_DATA_SCHEMA)
    lines = [f"스케줄 {len(enabled)}개 실행 중"]
    lines += [f"• {r['kind']} — {r['last_outcome'] or '-'}" for r in enabled]
    return {"reply": "\n".join(lines), "action": "SCHEDULES_LISTED", "data": data}


# --- reply contracts v1 for heartbeat and runtime_status (review R3-1/R3-2; why: docs/history/2026-10-16-claude-read-door-profile.md) ---
# Thomas 2026-10-15: implement in the repository, do NOT add either read to any profile.
#
# Same discipline as the scheduler projection above: each reply is rebuilt from a closed allowlist over a
# typed source, checked against a closed schema, and refused whole on a defect. Neither read is in any
# door profile (`read_bridge._PROFILE_HELD_READS`): these functions exist so that admitting one later is a
# profile decision, not a contract still to write.

HEARTBEAT_STATUSES: frozenset[str] = frozenset({heartbeat.FRESH, heartbeat.STALE, heartbeat.MISSING,
                                                heartbeat.UNREADABLE})
_UNREADABLE_PART = {heartbeat.RECORD_UNREADABLE: "record", heartbeat.BUSY_MARK_UNREADABLE: "busy_mark"}
_HEARTBEAT_NUMBERS = ("age_seconds", "stale_after_seconds", "interval_seconds")
_NUMBER_SCHEMA = {"type": "number", "minimum": 0}
_HEARTBEAT_ROW_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["service", "status", "projection"],
    "properties": {
        "service": {"enum": [*HEARTBEAT_SERVICES, "UNKNOWN"]},
        "status": {"enum": [*sorted(HEARTBEAT_STATUSES), "UNKNOWN"]},
        "unreadable_part": {"enum": sorted(_UNREADABLE_PART.values())},
        **{name: _NUMBER_SCHEMA for name in _HEARTBEAT_NUMBERS},
        "heartbeat_at": _TIME_SCHEMA,
        "busy": {"type": "object", "additionalProperties": False, "required": ["overrun"],
                 "properties": {"kind": {"enum": sorted(scheduler.KINDS)}, "overrun": {"type": "boolean"},
                                "started_at": _TIME_SCHEMA, "deadline_at": _TIME_SCHEMA}},
        "projection": {"enum": [PROJECTION_V1, PROJECTION_PARTIAL]},
    },
}
_HEARTBEAT_DATA_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["services", "all_fresh", "as_of", "projection"],
    "properties": {
        "services": {"type": "array", "maxItems": len(HEARTBEAT_SERVICES), "items": _HEARTBEAT_ROW_SCHEMA},
        "all_fresh": {"type": "boolean"}, "as_of": _TIME_SCHEMA,
        "projection": {"enum": [PROJECTION_V1, PROJECTION_PARTIAL]},
    },
}


def _nonneg_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
        return None
    return float(value)


def project_heartbeat_check(report: Any, failure: Any = None) -> dict[str, Any]:
    """One ``heartbeat.inspect_heartbeat`` result as the v1 contract row.

    Never carried: ``detail`` (its text holds the exception's message), ``pid``, ``checked_at``, any
    key of the busy mark beyond kind/started_at/deadline_at, any key added later. Which part of an
    UNREADABLE stamp failed comes from ``failure``, the structured code — never from ``detail``."""
    if not isinstance(report, Mapping):
        return {"service": "UNKNOWN", "status": "UNKNOWN", "projection": PROJECTION_PARTIAL}
    service = report.get("service") if report.get("service") in HEARTBEAT_SERVICES else "UNKNOWN"
    status = report.get("status") if report.get("status") in HEARTBEAT_STATUSES else "UNKNOWN"
    partial = "UNKNOWN" in (service, status)
    row: dict[str, Any] = {"service": service, "status": status}
    if status == heartbeat.UNREADABLE:
        part = _UNREADABLE_PART.get(failure) if isinstance(failure, str) else None
        if part is None:
            partial = True
        else:
            row["unreadable_part"] = part
    for name in _HEARTBEAT_NUMBERS:
        if report.get(name) is not None:
            value = _nonneg_number(report.get(name))
            if value is None:
                partial = True
            else:
                row[name] = value
    if report.get("heartbeat_at") is not None:
        value = report.get("heartbeat_at")
        if isinstance(value, str) and _ISO.match(value):
            row["heartbeat_at"] = value
        else:
            partial = True
    busy = report.get("busy")
    if busy is not None:
        if not isinstance(busy, Mapping) or not isinstance(report.get("overrun"), bool):
            partial = True
        else:
            out: dict[str, Any] = {"overrun": report["overrun"]}
            if busy.get("kind") in scheduler.KINDS:
                out["kind"] = busy["kind"]
            else:
                partial = True
            for name in ("started_at", "deadline_at"):
                value = busy.get(name)
                if isinstance(value, str) and _ISO.match(value):
                    out[name] = value
                else:
                    partial = True
            row["busy"] = out
    row["projection"] = PROJECTION_PARTIAL if partial else PROJECTION_V1
    return row


def _projected_heartbeat(*, now: str, repo_root: Path | None, projection: str) -> dict[str, Any]:
    require_projection(projection)
    rows = [project_heartbeat_check(*heartbeat.inspect_heartbeat(s, now=now, root=repo_root))
            for s in HEARTBEAT_SERVICES]
    partial = any(r["projection"] == PROJECTION_PARTIAL for r in rows)
    data = _checked({"services": rows, "all_fresh": all(r["status"] == heartbeat.FRESH for r in rows),
                     "as_of": now, "projection": PROJECTION_PARTIAL if partial else PROJECTION_V1},
                    _HEARTBEAT_DATA_SCHEMA)
    lines = [f"{r['service']}: {r['status']}" + (f" ({r['unreadable_part']} unreadable)" if "unreadable_part" in r else "")
             for r in rows]
    return {"reply": "\n".join(lines), "action": "HEARTBEAT_CHECKED", "data": data}


_CONTROL_MODES: frozenset[str] = frozenset({control.ACTIVE, control.PAUSED, control.KILLED})
_RUNTIME_STATUS_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["mode", "execution_allowed", "trading_armed", "halt_level", "fail_closed",
                 "stop_requested_count", "as_of", "projection"],
    "properties": {
        "mode": {"enum": [*sorted(_CONTROL_MODES), "UNKNOWN"]},
        "execution_allowed": {"type": "boolean"}, "trading_armed": {"type": "boolean"},
        "halt_level": {"enum": [*sorted(control.HALT_LEVELS), None]},
        "fail_closed": {"type": "boolean"}, "stop_requested_count": {"type": "integer", "minimum": 0},
        "updated_at": _TIME_SCHEMA, "as_of": _TIME_SCHEMA,
        "projection": {"enum": [PROJECTION_V1, PROJECTION_PARTIAL]},
    },
}


def project_runtime_status(state: Any, *, now: str) -> dict[str, Any]:
    """A ``control.ControlState`` as the v1 contract: the stop/arm/halt facts, never who or why.

    Read off the typed state, never the rendered ``/status`` text: ``updated_by`` (an operator id),
    ``reason`` (operator free text, or the fail-closed explanation), the stop-requested task ids and
    any field added to the state later are not carried. ``execution_allowed`` is recomputed from
    ``mode`` here rather than trusted from the object."""
    partial = False
    mode = getattr(state, "mode", None)
    if mode not in _CONTROL_MODES:
        mode, partial = "UNKNOWN", True
    halt = getattr(state, "halt_level", None)
    if halt is not None and halt not in control.HALT_LEVELS:
        halt, partial = None, True
    ids = getattr(state, "stop_requested_task_ids", ())
    if not isinstance(ids, (tuple, list)):
        ids, partial = (), True
    data: dict[str, Any] = {
        "mode": mode,
        "execution_allowed": mode == control.ACTIVE,
        "trading_armed": getattr(state, "trading_armed", None) is True,
        "halt_level": halt,
        "fail_closed": getattr(state, "fail_closed", None) is True,
        "stop_requested_count": len(ids),
        "as_of": now,
    }
    updated_at = getattr(state, "updated_at", None)
    if isinstance(updated_at, str) and _ISO.match(updated_at):
        data["updated_at"] = updated_at
    elif updated_at:
        partial = True
    data["projection"] = PROJECTION_PARTIAL if partial else PROJECTION_V1
    return _checked(data, _RUNTIME_STATUS_SCHEMA)


def read_runtime_status(control_store: Any, *, now: str, projection: str) -> dict[str, Any]:
    """``runtime_status`` under a reply contract: ``ControlStore.load()`` (a read — it writes nothing,
    and with no state file it reads the control-event ledger, also without writing), projected."""
    require_projection(projection)
    if control_store is None:
        raise ControlBlocked("CONTROL_STORE_UNAVAILABLE", "this door was opened without the control store")
    data = project_runtime_status(control_store.load(), now=now)
    text = (f"mode: {data['mode']}\nexecution: {'allowed' if data['execution_allowed'] else 'BLOCKED'}\n"
            f"live entries: {'armed' if data['trading_armed'] else 'DISARMED'}"
            + (f"\nhalt: {data['halt_level']}" if data["halt_level"] else "")
            + ("\nstate: fail-closed" if data["fail_closed"] else ""))
    return {"reply": text, "action": control.CMD_STATUS, "data": data}


def read_heartbeat(*, now: str, repo_root: Path | None, projection: str | None = None) -> dict[str, Any]:
    """Liveness of the three loops, from the files they stamp. Pure read; never raises.

    ``projection="v1"`` answers with the closed reply contract instead (`_projected_heartbeat`)."""
    if projection is not None:
        return _projected_heartbeat(now=now, repo_root=repo_root, projection=projection)
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


def read_lane_digest(ledger: LedgerStore | None, argument: str | None, *, now: str) -> dict[str, Any]:
    """What each lane did over the last N days (default 7, at most 31) — ``lane_digest.render``'s
    text and the fold itself as ``data``. Reads the run records, archives included, so a week that
    rotation has moved out of the active file still counts. Changes nothing."""
    if ledger is None or not hasattr(ledger, "iter_records_with_archive"):
        raise ControlBlocked("RUN_LEDGER_UNAVAILABLE", "this door was opened without the run ledger")
    days, note = parse_count_arg(
        argument, default=DEFAULT_DIGEST_DAYS, maximum=MAX_DIGEST_DAYS, usage="lane_digest [일수]",
    )
    since = timeutil.plus_minutes(now, -days * 24 * 60)
    digest = lane_digest.lane_digest(ledger, since=since, until=now)
    return {
        "reply": with_note(lane_digest.render(digest, since=since, until=now), note),
        "action": "LANE_DIGEST_READ",
        "data": {"since": since, "until": now, "days": days, "lanes": digest, "as_of": now},
    }


# --- reply contract v1.1 for holdings_status (HOLDINGS_STATUS_READ_AND_AUTO_FLOWS R1–R3, Thomas 2026-10-26) ---
#
# The §4.19.2 v1 (freshness only) plus the H6 readiness checks by name and state, the open-exception count,
# the shadow progress and what the Toss check still needs. Rebuilt from a closed allowlist over the stored
# snapshot's ``cash_flows.readiness``: no amount, weight, holding, account, exception key, ``next_step`` or
# ``toss_note`` text, and no key the snapshot gains later. ``holdings`` is a domain package, so it is imported
# here, at the read, never at module level (tests/test_mvp_runtime_domain_isolation.py).

HOLDINGS_STATUS = "HOLDINGS_STATUS"
_HOLDINGS_MISSING = "HOLDINGS_SNAPSHOT_MISSING"


def _holdings_schema(checks: tuple[str, ...], statuses: tuple[str, ...], needs: tuple[str, ...]) -> dict[str, Any]:
    state = {"enum": [*statuses, "UNKNOWN"]}
    h6 = {"type": "object", "additionalProperties": False,
          "required": ["ready", "checks", "open_exceptions"],
          "properties": {
              "ready": {"type": "boolean"},
              "checks": {"type": "object", "additionalProperties": False,
                         "properties": {name: state for name in checks}},
              "open_exceptions": {"type": "integer", "minimum": 0},
              "shadow_days": _NUMBER_SCHEMA,
              "shadow_success_dates": {"type": "integer", "minimum": 0},
              "toss_needs": {"type": "array", "uniqueItems": True, "items": {"enum": list(needs)}},
          }}
    return {"type": "object", "additionalProperties": False, "required": ["available", "as_of", "projection"],
            "properties": {
                "available": {"type": "boolean"},
                "reason_code": {"enum": [_HOLDINGS_MISSING]},
                "snapshot_as_of": _TIME_SCHEMA, "age_seconds": _NUMBER_SCHEMA,
                "stale": {"type": "boolean"}, "partial": {"type": "boolean"},
                "h6": h6, "as_of": _TIME_SCHEMA,
                "projection": {"enum": [PROJECTION_V1, PROJECTION_PARTIAL]},
            }}


def project_holdings_status(view: Any, *, now: str) -> dict[str, Any]:
    """``holdings.store.load_holdings_view``'s data as the v1.1 contract. Refused whole on a schema defect."""
    from .holdings import store as holdings_store         # domain package: at the read, never at import

    checks_known, statuses, needs_known = (holdings_store.READINESS_CHECKS, holdings_store.READINESS_STATUSES,
                                           holdings_store.TOSS_NEEDS)
    schema = _holdings_schema(checks_known, statuses, needs_known)
    partial = False
    if not isinstance(view, Mapping) or view.get("available") is not True:
        missing = isinstance(view, Mapping) and view.get("reason_code") == _HOLDINGS_MISSING
        data: dict[str, Any] = {"available": False, "as_of": now}
        if missing:
            data["reason_code"] = _HOLDINGS_MISSING
        data["projection"] = PROJECTION_V1 if missing else PROJECTION_PARTIAL
        return _checked(data, schema)
    data = {"available": True}
    as_of = view.get("as_of")
    if isinstance(as_of, str) and _ISO.match(as_of):
        data["snapshot_as_of"] = as_of
    elif as_of is not None:
        partial = True
    age = _nonneg_number(view.get("age_seconds"))
    if age is not None:
        data["age_seconds"] = age
    for name in ("stale", "partial"):
        if isinstance(view.get(name), bool):
            data[name] = view[name]
    flows = view.get("cash_flows")
    readiness = flows.get("readiness") if isinstance(flows, Mapping) else None
    if isinstance(readiness, Mapping):
        h6: dict[str, Any] = {"ready": readiness.get("ready") is True}
        raw_checks = readiness.get("checks") if isinstance(readiness.get("checks"), Mapping) else {}
        checks: dict[str, str] = {}
        for name, value in raw_checks.items():
            if name not in checks_known:
                partial = True                            # a check this contract does not know is dropped
                continue
            checks[name] = value if value in statuses else "UNKNOWN"
            partial = partial or value not in statuses
        h6["checks"] = checks
        count = _nonneg_int(readiness.get("exceptions"))
        h6["open_exceptions"] = count if count is not None else 0
        partial = partial or count is None
        days = _nonneg_number(readiness.get("shadow_days"))
        if days is not None:
            h6["shadow_days"] = round(days, 1)
        dates = _nonneg_int(readiness.get("shadow_success_dates"))
        if dates is not None:
            h6["shadow_success_dates"] = dates
        raw_needs = readiness.get("toss_needs")
        if isinstance(raw_needs, (list, tuple)):
            kept = [n for n in dict.fromkeys(raw_needs) if n in needs_known]
            partial = partial or len(kept) != len(raw_needs)
            h6["toss_needs"] = kept
        data["h6"] = h6
    elif flows is not None:
        partial = True
    data["as_of"] = now
    data["projection"] = PROJECTION_PARTIAL if partial else PROJECTION_V1
    return _checked(data, schema)


def read_holdings_status(*, now: str, repo_root: Path | None, projection: str) -> dict[str, Any]:
    """The holdings snapshot's freshness and H6 check states for a projecting door (the Claude door)."""
    require_projection(projection)
    from .holdings import store as holdings_store         # domain package: at the read, never at import

    try:
        view = holdings_store.load_snapshot_status(now=now, root=repo_root)
    except MvpRuntimeError:
        raise ControlBlocked("READ_UNAVAILABLE", "the holdings snapshot could not be read") from None
    data = project_holdings_status(view, now=now)
    if not data["available"]:
        return {"reply": "holdings: no snapshot yet", "action": "HOLDINGS_STATUS_READ", "data": data}
    lines = [f"holdings snapshot: {data.get('snapshot_as_of', '?')}"
             + (" (stale)" if data.get("stale") else "")]
    h6 = data.get("h6")
    if h6 is not None:
        lines.append(f"H6 ready: {h6['ready']}  open exceptions: {h6['open_exceptions']}"
                     + (f"  shadow: {h6['shadow_days']} days" if "shadow_days" in h6 else ""))
        lines += [f"  {name}: {state}" for name, state in sorted(h6["checks"].items())]
        if "toss_needs" in h6:
            lines.append("toss needs: " + (", ".join(h6["toss_needs"]) or "nothing"))
    return {"reply": "\n".join(lines), "action": "HOLDINGS_STATUS_READ", "data": data}
