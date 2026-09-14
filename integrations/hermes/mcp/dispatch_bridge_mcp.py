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
"""

from __future__ import annotations

import asyncio

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


if __name__ == "__main__":
    mcp.run()
