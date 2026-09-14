"""stdio MCP shim — lets Hermes dispatch bounded work into the Thomas runtime (door API v2).

Companion to ``switch_bridge_mcp.py`` (turns execution on and off) and ``read_bridge_mcp.py``
(looks at it). This one *starts* work: it forwards a request to a unix socket served by
``runtime/mvp_runtime/dispatch_bridge.py``, which runs it through the ordinary analysis
pipeline and returns the delivered result.

It holds no authority of its own, and the socket's far side caps everything that matters —
this file cannot widen it. The kinds it can dispatch are a closed set (analysis / research /
translation / content); each runs at permission P3 on a non-trading role; the runtime refuses
any request key it does not name; the kill switch is checked before every run; and every task
is recorded as ``assistant_bridge`` with a registry entry of origin AGENT (visible in
task_list / task_history as `비서`, fetchable by task_result).

v2 (2026-09-04): every dispatch carries a ``request_id``. Re-sending the same id within 24 h
does NOT start a second run — the door answers with the first run's ``{task_id, status,
result}`` (``replayed``), or ``REQUEST_IN_FLIGHT`` while it is still running. That is what
turns the old "started but slow, never retry" dead end into a recoverable one: retry WITH the
same id and you get the result, never a duplicate.

``research`` and ``draft_content`` additionally accept ``naver_keywords``: comma-separated
Korean seeds the runtime turns into measured Naver demand ([K#] evidence). The far side
validates it like every other field.

P08 (2026-09-14): ``workflow_changes`` narrates by polling with a cursor kept on this side, and
``report_workflow_usage`` sends Hermes's own usage (read from this container's ``state.db``) as
the reported budget layer — shown beside the runtime's enforced budget, never added to it.

v3 (2026-09-14, sequence 2 P05): ``submit_workflow`` hands the runtime a PLAN — several steps
over the same four kinds, with dependencies and a model-call budget — and returns at once
with a ``workflow_id``. The runtime's workflow manager runs the steps and keeps the state;
``workflow_status`` / ``workflow_events`` read it, ``cancel_workflow`` stops it. A runtime whose
dispatch door runs without the manager refuses every v3 tool by name (``WORKFLOW_UNAVAILABLE``)
and nothing falls back to a v2 run; ``thomas_capabilities`` says beforehand which it is.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

import thomas_door_client as door

mcp = FastMCP("thomas-dispatch")

_DOOR = "dispatch"


def _slow_text(request_id: str) -> str:
    return (
        f"STARTED_BUT_SLOW: the dispatch was accepted and the run CONTINUES inside the Thomas "
        f"runtime; only the reply did not arrive within {int(door.DOORS[_DOOR].timeout_seconds)}s. "
        "It may already be complete. Do NOT start a new dispatch. To recover: call the same "
        f"tool again with request_id=\"{request_id}\" — the door then returns the finished "
        "result (replayed) or REQUEST_IN_FLIGHT while it still runs — or check task_list / "
        "task_result. Report it to Thomas as 'running or completed, recoverable by request_id'."
    )


def _render(answer: door.Answer, *, kind: str, request_id: str) -> str:
    if answer.failure == door.TIMEOUT_AFTER_SEND:
        return _slow_text(request_id)
    if answer.failure:
        text = answer.failure_text()
        return text if text.startswith("UNCLEAR") else f"{text} Nothing was started."
    data = answer.data
    if answer.ok and answer.replayed:
        # The same request_id again: the door did NOT run anything. It answers with the
        # first run's identity, its current status, and — when delivered — its result
        # re-rendered from the ledger (or `result_ref` when too large to carry inline).
        status = data.get("status") or "unknown"
        result = data.get("result")
        head = (
            f"REPLAYED (not re-run) — the request_id {request_id!r} was already applied: "
            f"task={data.get('task_id')} registry={data.get('registry_entry_id')} status={status}."
        )
        if result:
            return f"{head}\n\n{result}"
        if data.get("result_ref"):
            return (f"{head} The result is too large to replay inline; fetch it with "
                    f"task_result(\"{data.get('registry_entry_id') or data.get('task_id')}\").")
        return f"{head} No result is available yet — use task_result on that id to check again."
    if answer.ok:
        return (
            f"DONE ({answer.get('kind', kind)}, recorded as actor={answer.get('actor')}, "
            f"task={answer.get('task_id')}, registry={answer.get('registry_entry_id')}, "
            f"request_id={request_id}):\n\n{answer.get('final_response', '')}"
        )
    if answer.reason_code == door.REQUEST_IN_FLIGHT:
        return (
            f"IN_FLIGHT: the run for request_id {request_id!r} is still executing "
            f"(status={data.get('status') or 'RUNNING'}). Nothing new was started. Wait, then "
            "call again with the same request_id, or check task_list."
        )
    return answer.refused_text(suffix=" Nothing was delivered.")


def _dispatch_blocking(kind: str, request: str, reason: str, naver_keywords: str = "",
                       request_id: str = "") -> str:
    if not request or not request.strip():
        return "REFUSED: a request is required — say what to work on."
    if not reason or not reason.strip():
        return "REFUSED: a reason is required and is recorded on the task."
    seeds = (naver_keywords or "").strip()
    if len(seeds) > 200:
        return ("REFUSED: naver_keywords exceeds 200 characters. Send fewer seeds — "
                "3-5 comma-separated keywords is the intended use.")
    rid = (request_id or "").strip() or door.new_request_id()
    payload: dict[str, object] = {"request": request.strip(), "kind": kind, "reason": reason.strip()}
    if seeds:
        payload["naver_keywords"] = seeds
    return _render(door.ask(_DOOR, payload, request_id=rid), kind=kind, request_id=rid)


async def _dispatch(kind: str, request: str, reason: str, naver_keywords: str = "",
                    request_id: str = "") -> str:
    """Run the blocking socket exchange in a worker thread, so FastMCP's loop keeps answering
    the client's keepalive pings while a run takes the minutes it takes (2026-08-10)."""
    return await asyncio.to_thread(_dispatch_blocking, kind, request, reason, naver_keywords, request_id)


@mcp.tool()
async def analyze(request: str, reason: str, request_id: str = "") -> str:
    """Dispatch a business/idea analysis to the Thomas runtime and return its reviewed report
    (full pipeline, P3, `general.specialist`; minutes). `reason` is recorded on the task.
    `request_id`: leave empty for a new run; pass the id from a STARTED_BUT_SLOW reply to
    recover that run instead of starting another."""
    return await _dispatch("analysis", request, reason, request_id=request_id)


@mcp.tool()
async def research(request: str, reason: str, naver_keywords: str = "", request_id: str = "") -> str:
    """Dispatch a research task (evidence collection + source comparison; P3,
    `research.general`; minutes). `naver_keywords`: optional comma-separated Korean SHORT
    keyword seeds (3-5, ≤200 chars) for measured Naver demand as [K#] evidence — blog topics
    and Korean market demand only. `request_id`: empty for a new run; the id from a
    STARTED_BUT_SLOW reply to recover that run."""
    return await _dispatch("research", request, reason, naver_keywords, request_id)


@mcp.tool()
async def translate(request: str, reason: str, request_id: str = "") -> str:
    """Dispatch a translation (with ambiguity disclosure; P3, `translation.general`). Put the
    text and the target language in `request`. `request_id` as in `analyze`."""
    return await _dispatch("translation", request, reason, request_id=request_id)


@mcp.tool()
async def draft_content(request: str, reason: str, naver_keywords: str = "", request_id: str = "") -> str:
    """Dispatch a content draft (planning + audience adaptation; P3, `content.general`).
    Describe the piece and its audience. `naver_keywords` as in `research`; `request_id` as
    in `analyze`."""
    return await _dispatch("content", request, reason, naver_keywords, request_id)


# --- door API v3: workflows -------------------------------------------------------------------

WORKFLOW_UNAVAILABLE = "WORKFLOW_UNAVAILABLE"
PLAN_SCHEMA_VERSION = "workflow_plan.v0.1"


def _render_workflow(answer: door.Answer, *, command: str, request_id: str | None = None) -> str:
    if answer.failure == door.TIMEOUT_AFTER_SEND and command == "workflow.submit":
        return (
            f"SUBMITTED_BUT_UNCONFIRMED: the submit frame was sent and no reply arrived. The plan may "
            f"be accepted. Do NOT submit it again under a new id — call submit_workflow again with "
            f"request_id=\"{request_id}\" (the door replays an accepted workflow, never accepts it twice), "
            "or workflow_status if you already have the workflow_id."
        )
    if answer.failure:
        text = answer.failure_text()
        return text if text.startswith("UNCLEAR") else f"{text} Nothing was started."
    if answer.ok:
        head = str(answer.reply or "")
        if command == "workflow.submit":
            head = f"{head}\nKeep this request_id; the same plan under it replays instead of re-running."
        return head + door.data_line(answer)
    if answer.reason_code == WORKFLOW_UNAVAILABLE:
        return (
            f"REFUSED [{WORKFLOW_UNAVAILABLE}]: this runtime's dispatch door runs without the workflow "
            "manager, so workflows are not served here — nothing was started. Use analyze / research / "
            "translate / draft_content one at a time, or tell Thomas the runtime needs the manager enabled."
        )
    return answer.refused_text(suffix=" Nothing was changed.")


def _workflow_ask(payload: dict[str, object], *, request_id: str | None = None) -> str:
    return _render_workflow(door.ask(_DOOR, payload, request_id=request_id),
                            command=str(payload.get("command")), request_id=request_id)


@mcp.tool()
def thomas_capabilities() -> str:
    """What this runtime's dispatch door serves: the v3 workflow commands, the four kinds, the
    plan schema, and whether the workflow manager is running (`workflow_manager` on the `[data]`
    line). Call it once before the first submit_workflow of a session."""
    return _workflow_ask({"command": "capabilities"})


@mcp.tool()
def submit_workflow(plan_json: str, request_id: str = "") -> str:
    """Submit a multi-step PLAN and return at once with its workflow_id (the runtime runs it;
    minutes to hours). `plan_json` is a JSON object: {"schema_version": "workflow_plan.v0.1",
    "goal": "...", "steps": [{"id": "research", "capability": "research"|"analysis"|"translation"|
    "content", "request": "...", "reason": "...", "depends_on": ["..."], "input_refs": ["..."],
    "naver_keywords": "optional", "max_attempts": 1-3, "options": {"independent_validation": bool,
    "revise": bool}}], "budget": {"max_model_calls": N}} — at most 10 steps, a DAG over `depends_on`,
    `input_refs` ⊆ `depends_on`, and no other key (the door refuses an effect class, an actor or a
    permission). `request_id`: empty for a new plan (one is minted and returned — keep it); the
    same id with the same plan replays the accepted workflow; the same id with a different plan is
    refused. Read the reply's `[data]` for workflow_id and status."""
    raw = (plan_json or "").strip()
    if not raw:
        return "REFUSED: plan_json is required — the plan object as JSON text."
    try:
        plan = json.loads(raw)
    except ValueError as exc:
        return f"REFUSED: plan_json is not valid JSON ({exc}). Nothing was sent."
    if not isinstance(plan, dict):
        return "REFUSED: plan_json must be a JSON object. Nothing was sent."
    plan.setdefault("schema_version", PLAN_SCHEMA_VERSION)
    rid = (request_id or "").strip() or door.new_request_id()
    return _workflow_ask({"command": "workflow.submit", "plan": plan}, request_id=rid)


@mcp.tool()
def workflow_status(workflow_id: str) -> str:
    """One workflow: its status, every step with attempts and result reference, the budget. The
    `[data]` line carries `row_version` (needed by cancel_workflow) and each step's `result_ref`
    — fetch a delivered step's text with task_result on that reference's trace id."""
    wid = (workflow_id or "").strip()
    if not wid:
        return "REFUSED: workflow_id is required."
    return _workflow_ask({"command": "workflow.status", "workflow_id": wid})


@mcp.tool()
def workflow_list(limit: str = "") -> str:
    """Recent workflows, newest first. `limit` is an optional count (default 20, max 50)."""
    payload: dict[str, object] = {"command": "workflow.list"}
    if limit and limit.strip().isdigit():
        payload["limit"] = int(limit.strip())
    return _workflow_ask(payload)


@mcp.tool()
def workflow_events(after_cursor: str = "0", limit: str = "") -> str:
    """Coordination events after a cursor — what changed since you last looked. Remember the
    reply's `next_cursor` and pass it back; the same cursor returns the same rows. Cheap on the
    runtime side; the cost is your own context, so ask for what you will read."""
    payload: dict[str, object] = {"command": "workflow.events",
                                  "after_cursor": int(after_cursor) if (after_cursor or "").strip().isdigit() else 0}
    if limit and limit.strip().isdigit():
        payload["limit"] = int(limit.strip())
    return _workflow_ask(payload)


@mcp.tool()
def cancel_workflow(workflow_id: str, expected_version: str, reason: str) -> str:
    """Stop a workflow. `expected_version` is the `row_version` you read from workflow_status —
    a stale version is refused (VERSION_CONFLICT: read again). Waiting steps are cancelled at
    once; a running step stops at its next boundary or its lease, and the reply says CANCELLING
    until then. Never report CANCELLED before the reply does."""
    wid = (workflow_id or "").strip()
    if not wid:
        return "REFUSED: workflow_id is required."
    if not (expected_version or "").strip().isdigit():
        return "REFUSED: expected_version must be the row_version read from workflow_status."
    if not (reason or "").strip():
        return "REFUSED: a reason is required and is recorded."
    return _workflow_ask({"command": "workflow.cancel", "workflow_id": wid,
                          "expected_version": int(expected_version.strip()), "reason": reason.strip()})


@mcp.tool()
def retry_workflow_step(workflow_id: str, step_key: str, expected_version: str, reason: str) -> str:
    """Re-open ONE settled step of a workflow that is WAITING_REPLAN — a step that FAILED below the
    attempt cap, was blocked by the budget, or needs reconciliation. Steps that succeeded are never
    re-run; steps blocked by that step wait for it again. `expected_version` is the `row_version`
    from workflow_status. Refused on a step blocked by a failed dependency (retry the dependency),
    at the attempt cap (ATTEMPTS_EXHAUSTED), or beyond the budget (BUDGET_EXHAUSTED)."""
    wid = (workflow_id or "").strip()
    if not wid or not (step_key or "").strip():
        return "REFUSED: workflow_id and step_key are required."
    if not (expected_version or "").strip().isdigit():
        return "REFUSED: expected_version must be the row_version read from workflow_status."
    if not (reason or "").strip():
        return "REFUSED: a reason is required and is recorded."
    return _workflow_ask({"command": "workflow.retry_step", "workflow_id": wid, "step_key": step_key.strip(),
                          "expected_version": int(expected_version.strip()), "reason": reason.strip()})


@mcp.tool()
def propose_workflow_update(workflow_id: str, expected_version: str, plan_json: str, reason: str) -> str:
    """Replace a workflow's plan with a NEW VERSION (the whole plan as JSON, same shape as
    submit_workflow). Allowed: change steps that have not started, add steps, drop unstarted steps,
    change the goal, raise the budget (never below what is reserved). Refused (PLAN_CONFLICT): a
    running or delivered step's request, kind, dependencies or inputs; a cancelled step's key; a
    budget below reservations. A changed gated step asks Thomas again; a budget-blocked step the
    new budget covers is released. Nothing delivered is re-run. `expected_version` is the
    `row_version` from workflow_status."""
    wid = (workflow_id or "").strip()
    if not wid:
        return "REFUSED: workflow_id is required."
    if not (expected_version or "").strip().isdigit():
        return "REFUSED: expected_version must be the row_version read from workflow_status."
    if not (reason or "").strip():
        return "REFUSED: a reason is required and is recorded with the version."
    raw = (plan_json or "").strip()
    if not raw:
        return "REFUSED: plan_json is required — the whole new plan as JSON text."
    try:
        plan = json.loads(raw)
    except ValueError as exc:
        return f"REFUSED: plan_json is not valid JSON ({exc}). Nothing was sent."
    if not isinstance(plan, dict):
        return "REFUSED: plan_json must be a JSON object. Nothing was sent."
    plan.setdefault("schema_version", PLAN_SCHEMA_VERSION)
    return _workflow_ask({"command": "workflow.propose_update", "workflow_id": wid,
                          "expected_version": int(expected_version.strip()), "plan": plan, "reason": reason.strip()})


# --- P08: narration by polling, and the reported budget layer ----------------------------------

CURSOR_FILE_ENV = "THOMAS_WORKFLOW_CURSOR_FILE"
STATE_DB_ENV = "HERMES_STATE_DB"
DEFAULT_STATE_DB = "/opt/data/state.db"
USAGE_SOURCE = "hermes:session_model_usage"


def _cursor_path() -> Path:
    return Path(os.environ.get(CURSOR_FILE_ENV) or Path(__file__).with_name(".workflow_cursor.json"))


def _load_cursor() -> int | None:
    """The cursor the last narration advanced to; None when this side has never narrated (or
    the file is unreadable — then the tail is adopted rather than the whole history replayed)."""
    try:
        raw = json.loads(_cursor_path().read_text(encoding="utf-8"))
        cursor = raw.get("cursor")
        return int(cursor) if isinstance(cursor, int) and not isinstance(cursor, bool) and cursor >= 0 else None
    except (OSError, ValueError, AttributeError):
        return None


def _save_cursor(cursor: int) -> None:
    path = _cursor_path()
    path.write_text(json.dumps({"cursor": int(cursor), "updated_at": _now_iso()}), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@mcp.tool()
def workflow_changes(limit: str = "") -> str:
    """What changed across all workflows since the LAST call of this tool — the cursor lives on
    this side (a file beside the shim), so a cron job narrates without remembering anything.
    Returns '변화 없음' when nothing moved: then stay silent. The first call ever adopts the
    current position and narrates nothing. This is narration only — the delivery of core events
    (a workflow ending, or waiting for a decision) to Thomas is the runtime operator's job and
    is guaranteed there, not here; never claim to have delivered anything."""
    cursor = _load_cursor()
    if cursor is None:
        probe = door.ask(_DOOR, {"command": "workflow.events", "after_cursor": 0, "limit": 1})
        if not probe.ok:
            return _render_workflow(probe, command="workflow.events")
        tail = _tail_cursor(probe)
        _save_cursor(tail)
        return f"커서 초기화 (cursor={tail}); 이번에는 서술할 것이 없다. 변화 없음."
    payload: dict[str, object] = {"command": "workflow.events", "after_cursor": cursor}
    if limit and limit.strip().isdigit():
        payload["limit"] = int(limit.strip())
    answer = door.ask(_DOOR, payload)
    if not answer.ok:
        return _render_workflow(answer, command="workflow.events")
    events = answer.data.get("events") or []
    next_cursor = answer.data.get("next_cursor", cursor)
    if not events or not isinstance(next_cursor, int):
        return f"변화 없음 (cursor={cursor})."
    _save_cursor(next_cursor)
    return (f"{answer.reply}\n(cursor {cursor} → {next_cursor}; narrate only what changed — a workflow's ending or "
            "waiting for a decision was already pushed to Thomas by the runtime operator)" + door.data_line(answer))


def _tail_cursor(probe: door.Answer) -> int:
    """The highest cursor the door knows: `workflow.events` answers `next_cursor` for the page it
    returned, so ask again from there until the page is empty (bounded: each page is ≥ 1 row)."""
    cursor = 0
    for _ in range(10_000):
        events = probe.data.get("events") or []
        next_cursor = probe.data.get("next_cursor")
        if not events or not isinstance(next_cursor, int) or next_cursor <= cursor:
            return cursor
        cursor = next_cursor
        probe = door.ask(_DOOR, {"command": "workflow.events", "after_cursor": cursor, "limit": 200})
        if not probe.ok:
            return cursor
    return cursor


@mcp.tool()
def report_workflow_usage(workflow_id: str, session_id: str = "", since: str = "") -> str:
    """Report Hermes's OWN model usage for a workflow to the runtime — the reported budget layer.
    Reads this container's state.db (`session_model_usage`) and sends the totals; the runtime shows
    them beside its enforced budget (model-call reservations) and never adds them to it. Nothing is
    enforced by this and it blocks nothing. Give `session_id` (exact: the session that planned and
    narrated the workflow) or `since` (an ISO timestamp — every session's usage since then, a
    coarse upper bound). Call it when a workflow ends or when Thomas asks what it cost."""
    wid = (workflow_id or "").strip()
    if not wid:
        return "REFUSED: workflow_id is required."
    sid, since_iso = (session_id or "").strip(), (since or "").strip()
    if bool(sid) == bool(since_iso):
        return "REFUSED: give exactly one of session_id or since. Nothing was sent."
    try:
        usage = _read_hermes_usage(session_id=sid or None, since=since_iso or None)
    except (sqlite3.Error, OSError, ValueError) as exc:
        return f"REFUSED: cannot read Hermes usage from {_state_db_path()} ({exc}). Nothing was sent."
    if usage is None:
        return f"REFUSED: no usage rows for {'session ' + sid if sid else 'sessions since ' + since_iso}. Nothing was sent."
    return _workflow_ask({"command": "workflow.report_usage", "workflow_id": wid, "reported_usage": usage})


def _state_db_path() -> Path:
    return Path(os.environ.get(STATE_DB_ENV) or DEFAULT_STATE_DB)


def _read_hermes_usage(*, session_id: str | None, since: str | None) -> dict[str, object] | None:
    """Sum `session_model_usage` for one session, or for every session seen since a timestamp.
    Read-only (`mode=ro`), and the file is never created. `cost_status`: observed when every row
    carries an actual cost, estimated when the rows are Hermes's estimates, unmeasured when the
    rows carry tokens but no cost."""
    path = _state_db_path()
    if not path.is_file():
        raise OSError(f"no state database at {path}")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        if session_id is not None:
            rows = conn.execute(
                "SELECT input_tokens, output_tokens, estimated_cost_usd, actual_cost_usd, cost_status"
                " FROM session_model_usage WHERE session_id=?", (session_id,)).fetchall()
            source = f"{USAGE_SOURCE}:session"
        else:
            epoch = datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp()
            rows = conn.execute(
                "SELECT input_tokens, output_tokens, estimated_cost_usd, actual_cost_usd, cost_status"
                " FROM session_model_usage WHERE last_seen >= ?", (epoch,)).fetchall()
            source = f"{USAGE_SOURCE}:since"
    finally:
        conn.close()
    if not rows:
        return None
    input_tokens = sum(int(r[0] or 0) for r in rows)
    output_tokens = sum(int(r[1] or 0) for r in rows)
    actual = sum(float(r[3] or 0.0) for r in rows)
    estimated = sum(float(r[2] or 0.0) for r in rows)
    if actual > 0 and all(float(r[3] or 0.0) > 0 for r in rows):
        cost, status = actual, "observed"
    elif estimated > 0:
        cost, status = estimated, "estimated"
    else:
        cost, status = 0.0, "unmeasured"
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "estimated_cost_usd": round(cost, 6),
            "cost_status": status, "source": source, "as_of": _now_iso()}


# --- P09: schedule changes inside the delegated scope --------------------------------------------

SCHEDULE_ACTIONS = ("create", "enable", "disable", "remove")


@mcp.tool()
def propose_schedule_change(action: str, reason: str, kind: str = "", request: str = "",
                            interval_seconds: str = "", schedule_id: str = "") -> str:
    """Change ONE schedule inside the scope the governance policy delegates to you, or record a
    proposal for Thomas when the change is outside it. `action`: create (with `kind`, `request`,
    `interval_seconds`) | enable | disable | remove (with `schedule_id` from `schedules`). Inside
    the delegated scope (non-financial kinds only, a minimum interval, a ceiling on active
    delegated schedules, a validity period) the reply says APPLIED and the schedule exists;
    outside it the reply says PROPOSED and nothing changed — put it in a [상신] for Thomas, who
    applies it in the container. A crypto/financial schedule is REFUSED and never proposed here.
    While the policy delegates nothing (SCHEDULE_DELEGATION_DISABLED) every change is refused:
    then schedules are Thomas's alone, as before. `reason` is required and recorded."""
    act = (action or "").strip().lower()
    if act not in SCHEDULE_ACTIONS:
        return f"REFUSED: action must be one of {', '.join(SCHEDULE_ACTIONS)}. Nothing was sent."
    if not (reason or "").strip():
        return "REFUSED: a reason is required and is recorded. Nothing was sent."
    change: dict[str, object] = {"action": act, "reason": reason.strip()}
    if act == "create":
        if not (kind or "").strip():
            return "REFUSED: kind is required to create a schedule. Nothing was sent."
        if not (interval_seconds or "").strip().isdigit():
            return "REFUSED: interval_seconds must be a whole number of seconds. Nothing was sent."
        change.update({"kind": kind.strip(), "request": request or "", "interval_seconds": int(interval_seconds.strip())})
    else:
        if not (schedule_id or "").strip():
            return f"REFUSED: schedule_id is required to {act} a schedule (see schedules). Nothing was sent."
        change["schedule_id"] = schedule_id.strip()
    return _workflow_ask({"command": "schedule.propose_change", "change": change})


if __name__ == "__main__":
    mcp.run()
