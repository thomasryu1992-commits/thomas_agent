"""stdio MCP shim — exposes the Thomas runtime's read-only door to Hermes (door API v2).

Companion to `switch_bridge_mcp.py`, and separate for the same reason the doors are separate:
that one turns the runtime's execution on and off, this one only looks at it.

Like its siblings it holds no authority. Every call is forwarded to a unix socket served by
`runtime/mvp_runtime/read_bridge.py`, which dispatches to the same appliers the Telegram
control channel uses. So what comes back is the console's own rendering, not a summary of a
file — an answer from here cannot disagree with `/crypto status`, because it is `/crypto status`.

There is no tool here that changes anything. The door refuses every mutating verb (registry
CANCEL, memory PROMOTE, kill/pause/resume, and every schedule mutation — enable/disable/remove
live in the scheduler CLI and nowhere a socket reaches), so adding one here would produce a
refusal rather than an effect.

**Every reply is a snapshot, and this file stamps it as one** (measured 2026-08-10: a board
read two days earlier was replayed as current, prefaced by "직접 조회하여 확인한 결과"). The
stamp cannot stop a replay, but it makes one visible.

v2 (2026-09-04): frames go through `thomas_door_client` (`proto: 2`, `client_id`), every reply
carries `data`, and four reads the console never rendered arrive — `schedules`,
`scheduler_events`, `heartbeat`, `approval_status`. The normative rules live in SOUL.md and the
thomas-ops skill; the docstrings below are deliberately short because `tool_search` is off and
every docstring rides along on every turn.

v2.3 (2026-09-14, sequence 2 P02): the reads whose numbers must not travel without their
`as_of` / `stale` / scope — readiness, funds, heartbeat, approval status, runtime status — carry
the reply's structured `data` as one `[data]` line after the board. Until now the shim dropped
`data` on the floor and the model re-parsed prose (the 2026-09-13 review: `ready` read as
"live trading is on" with zero armed strategies). Capped, so a large view cannot crowd the reply.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

import thomas_door_client as door

mcp = FastMCP("thomas-read")

_DOOR = "read"


def _render(answer: door.Answer, *, with_data: bool = False) -> str:
    if answer.failure:
        return answer.failure_text()
    if answer.ok:
        return door.stamp(answer.reply) + (door.data_line(answer) if with_data else "")
    return answer.refused_text()


def _ask(command: str, argument: str | None = None, *, with_data: bool = False) -> str:
    payload: dict[str, object] = {"command": command}
    if argument:
        payload["argument"] = argument
    return _render(door.ask(_DOOR, payload), with_data=with_data)


@mcp.tool()
def trading_status() -> str:
    """Crypto trading board — call it fresh every time. Open positions (PAPER book unless
    trading_readiness says otherwise), strategy state, recent activity. Local ledger only."""
    return _ask("crypto_status")


@mcp.tool()
def trading_readiness() -> str:
    """Live-trading readiness board — call it fresh every time. Every gate between this machine
    and a live order, today's realized P&L against the limit. Rendered in YOUR container: env
    rows always FAIL here; read the conclusion from `live_gate_recorded` and
    `live_armed_strategies`, and never say live trading is disabled because of an env row.
    The `[data]` line keeps four things apart — say which one you mean: `infrastructure_ready`
    (this process's checks), `live_armed_strategies.armed`, `recorded_gate` (with `stale`), and
    `live_entry_possible` (armed > 0 AND gate open AND not stale; `null` = unknown). `ready`
    alone never means "live trading is on"."""
    return _ask("crypto_readiness", with_data=True)


@mcp.tool()
def paper_performance() -> str:
    """Paper-trading performance — outcomes and win rate for simulated trades. Call it fresh."""
    return _ask("crypto_paper")


@mcp.tool()
def runtime_status() -> str:
    """Runtime ACTIVE/PAUSED/KILLED and why — call it fresh, and always after a stop to confirm
    it landed. `[data]` carries the mode and reason as fields."""
    return _ask("runtime_status", with_data=True)


@mcp.tool()
def task_list() -> str:
    """Tasks queued or running right now (yours show as `비서`). Call it fresh."""
    return _ask("tasks")


@mcp.tool()
def task_history(limit: str = "") -> str:
    """Recently completed tasks, newest first. `limit` is an optional count, e.g. "10"."""
    return _ask("history", limit or None)


@mcp.tool()
def task_result(task_id: str) -> str:
    """The delivered result of one run, by any of its ids — `task_…`, `trace_…` or the
    registry id `treg_…` (from task_history, or from a dispatch reply / its `request_id`
    replay). A run that has not delivered yet answers with its status instead of a result."""
    return _ask("result", task_id)


@mcp.tool()
def current_funds() -> str:
    """Account balance and realized return — a 15-minute snapshot written by the scheduler,
    never a live exchange call. Say the `as of` time with every number; a `!! STALE` banner
    means say that first. `REFUSED [ACCOUNT_SNAPSHOT_MISSING]` = no snapshot yet; never invent
    a number. Read `net` (after fees and funding). The `[data]` line carries `as_of`,
    `age_seconds`, `stale` and the figures; a stale snapshot is stale there too."""
    return _ask("crypto_funds", with_data=True)


@mcp.tool()
def memory_candidates() -> str:
    """Working-memory candidates awaiting Thomas's promotion decision. Read-only."""
    return _ask("memory")


@mcp.tool()
def schedules() -> str:
    """The scheduler's registered rows: enabled ones with lane (risk / maintenance), interval,
    next run, and how far overdue; disabled ones as a count only. Read-only — there is no
    tool anywhere to enable, disable or remove a row."""
    return _ask("schedules")


@mcp.tool()
def scheduler_events(limit: str = "") -> str:
    """What the scheduler actually did, newest first — fired / started / skipped_not_active
    and their status. `limit` up to 100 (default 20). Cheap on the runtime side (a tail
    read); the cost is your own context, so ask for what you will actually read."""
    return _ask("scheduler_events", limit or None)


@mcp.tool()
def heartbeat() -> str:
    """Whether the three loops are alive — operator, scheduler-risk, scheduler-maintenance —
    each FRESH / STALE / MISSING with its age. `all_fresh` is the one-word answer (on the
    `[data]` line as a field)."""
    return _ask("heartbeat", with_data=True)


@mcp.tool()
def approval_status(approval_id: str) -> str:
    """Where one approval stands: recorded status and the EFFECTIVE status with the clock
    applied (a PENDING ask past `expires_at` reads EXPIRED here even though the file still
    says PENDING). Never the ask's content or fingerprint, and this cannot decide anything —
    the decision is Thomas's `/approve` on the control bot. `[data]` carries the recorded and
    effective status and `expires_at` as fields."""
    return _ask("approval_status", approval_id, with_data=True)


if __name__ == "__main__":
    mcp.run()
