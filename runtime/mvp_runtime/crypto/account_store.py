"""The account snapshot the read door serves — written by the lane that holds the keys.

Thomas's original goal for the assistant door was three things: current position, current
**funds**, and return (`docs/proposals/HERMES_AGENT_SWITCH_V0.1.md` §1). Two shipped. Funds did
not, and §7 of that proposal says why the obvious route is closed: the balance lives behind a
signed venue call, and the read door deliberately holds no venue credential. Giving it one would
undo the credential-plane separation — and on this host it would put an order-capable key in a
process the assistant can reach, because there is only one Binance key and order authority
derives from it.

So the door does not call the venue. The **scheduler** does, on its own cadence, from the lane
that already holds those keys, and writes what it saw here; the door renders this file. The
figure is therefore minutes old rather than live, which is a real cost and is why every render
carries its `as_of` and its age rather than leaving the reader to assume.

**Two files, not one.** The snapshot holds the last SUCCESSFUL read; the refresh mark holds the
last ATTEMPT. `orderbook_store` separates them for a reason that applies exactly here: fold them
together and one timeout overwrites a perfectly good balance with a degraded record, so the
answer gets worse precisely when the venue is having a bad minute. A failure moves the mark and
leaves the balance alone.

**Staleness shows the number, it does not hide it.** §7 asked for the `recorded_gate` treatment
— render the value and say plainly that it is not a statement about now — and that is what
`load_funds_board` does. Hiding the figure would make a stale board and a broken one look the
same to the operator, and only one of those is an emergency.

Nothing here reaches the venue except `refresh_snapshot`, which is only ever called from the
scheduler. `load_funds_board` opens no socket: it reads a file and formats it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..errors import ToolError
from ..filelock import locked
from .. import timeutil
from . import paper

SNAPSHOT_FILENAME = "account_snapshot.json"
REFRESH_MARK_FILENAME = "account_refresh.json"

RECORD_TYPE = "account_snapshot.v0"

# How often the scheduler asks. Two signed GETs against a 2400/min weight budget is nothing;
# what this actually bounds is how much venue latency the money path is asked to carry.
REFRESH_AFTER_SECONDS = 15 * 60

# When a rendered figure stops being a statement about now. Deliberately the same window
# `live_readiness` uses for its recorded gate — an operator should not have to hold two
# different ideas of "too old" for two boards read in the same minute.
STALE_AFTER_SECONDS = 2 * 60 * 60

ACCOUNT_SNAPSHOT_MISSING = "ACCOUNT_SNAPSHOT_MISSING"
ACCOUNT_SNAPSHOT_UNREADABLE = "ACCOUNT_SNAPSHOT_UNREADABLE"
ACCOUNT_FEED_NOT_CONFIGURED = "ACCOUNT_FEED_NOT_CONFIGURED"
ACCOUNT_SNAPSHOT_DEGRADED = "ACCOUNT_SNAPSHOT_DEGRADED"

__all__ = [
    "ACCOUNT_FEED_NOT_CONFIGURED",
    "ACCOUNT_SNAPSHOT_DEGRADED",
    "ACCOUNT_SNAPSHOT_MISSING",
    "ACCOUNT_SNAPSHOT_UNREADABLE",
    "REFRESH_AFTER_SECONDS",
    "STALE_AFTER_SECONDS",
    "is_due",
    "load_funds_board",
    "read_refresh_mark",
    "read_snapshot",
    "refresh_snapshot",
    "snapshot_path",
]


def snapshot_path(root: Path | None = None) -> Path:
    return paper.state_dir(root) / SNAPSHOT_FILENAME


def refresh_mark_path(root: Path | None = None) -> Path:
    return paper.state_dir(root) / REFRESH_MARK_FILENAME


def _read_json(path: Path) -> dict[str, Any] | None:
    """Parsed contents, or None for absent AND for damaged.

    Damage reads as absence on purpose: the caller's answer to "no snapshot" is a typed refusal
    either way, and a store that raises on a truncated write turns one bad file into a dead
    read door.
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_snapshot(root: Path | None = None) -> dict[str, Any] | None:
    return _read_json(snapshot_path(root))


def read_refresh_mark(root: Path | None = None) -> str | None:
    mark = _read_json(refresh_mark_path(root))
    if mark is None:
        return None
    attempted = mark.get("attempted_at")
    return attempted if isinstance(attempted, str) and attempted else None


def _age_seconds(stamp: str | None, now: str) -> float | None:
    if not stamp:
        return None
    try:
        return (timeutil.parse_iso(now) - timeutil.parse_iso(stamp)).total_seconds()
    except (ValueError, TypeError):
        return None


def is_due(last_attempt: str | None, now: str) -> bool:
    """Whether to ask the venue again, given when it was last ASKED — not last answered.

    Never attempted, or a mark this module cannot read, both read as due: state that cannot be
    parsed must not be able to stop the refresh, and being wrong costs one request.
    """
    age = _age_seconds(last_attempt, now)
    return age is None or age >= REFRESH_AFTER_SECONDS


def _write_json(path: Path, body: Mapping[str, Any], *, code: str, label: str) -> None:
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code=code, label=label):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(path)


def refresh_snapshot(*, now: str, root: Path | None = None, timeout_seconds: int = 4) -> str:
    """Ask the venue once and store the answer. Returns a one-line status for the fire.

    **Never raises.** This is called from the crypto fire, which is a RISK kind — an exception
    here would take down the loop that manages real positions in order to fail at bookkeeping.
    Every failure becomes a status string and a moved refresh mark.

    The default timeout is 4s and not `read_account`'s 10s because this rides a money-path fire
    that is never deferred: two signed GETs at the library default is up to 20 seconds of
    latency pushed onto every other kind due in the same pass, which is the shape of delay that
    `MAINTENANCE_PASS_BUDGET_SECONDS` exists to bound.

    A degraded read moves the mark and leaves the last good snapshot in place — see the module
    docstring.
    """
    try:
        _write_json(refresh_mark_path(root), {"attempted_at": now},
                    code="ACCOUNT_REFRESH_MARK_LOCKED", label="account refresh mark")
    except Exception as exc:  # noqa: BLE001 — bookkeeping must not fail a money fire
        return f"account snapshot: mark not written ({type(exc).__name__})"

    try:
        from .account import read_account

        snapshot, record = read_account(timeout_seconds=timeout_seconds, root=root)
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return f"account snapshot: read failed ({type(exc).__name__})"

    if snapshot is None or record.get("degraded"):
        reason = record.get("error_reason_code") or record.get("degraded_reason_code")
        if not record.get("configured", False):
            return "account snapshot: no account feed configured"
        return f"account snapshot: degraded ({reason}); kept the previous one"

    body = dict(record)
    body["record_type"] = RECORD_TYPE
    body["as_of"] = record.get("collected_at") or now
    body["written_at"] = now
    try:
        _write_json(snapshot_path(root), body,
                    code="ACCOUNT_SNAPSHOT_LOCKED", label="account snapshot")
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return f"account snapshot: not persisted ({type(exc).__name__})"
    return "account snapshot: refreshed"


def _fmt(value: Any) -> str:
    return "n/a" if value is None else f"{value}"


def render_funds_text(body: Mapping[str, Any], *, now: str) -> str:
    """ASCII-only funds board. Windows consoles are cp949 and die on fancy dashes.

    Deliberately NOT `account.render_account_text`: the stored record carries
    `open_position_count` and not the positions themselves, so reviving an `AccountSnapshot`
    from it would render "positions: none" for an account that has three — a false statement
    about live exposure, produced by reusing a renderer that expects a richer input.
    """
    as_of = str(body.get("as_of") or "")
    age = _age_seconds(as_of, now)
    stale = age is None or age > STALE_AFTER_SECONDS
    if age is None:
        age_text = "age unknown"
    elif age < 90:
        age_text = f"{int(age)}s ago"
    else:
        age_text = f"{int(age // 60)}m ago"

    lines = [f"as of {as_of or 'unknown'} ({age_text})"]
    if stale:
        lines += [
            "!! STALE - this is the last figure the trading process recorded, NOT a statement",
            f"   about now. Anything older than {STALE_AFTER_SECONDS // 3600}h means the "
            "snapshot writer stopped;",
            "   check that the scheduler is running before acting on these numbers.",
        ]
    asset = body.get("asset") or "USDT"
    lines += [
        "",
        f"wallet    : {_fmt(body.get('wallet_balance'))} {asset}",
        f"margin    : {_fmt(body.get('margin_balance'))} {asset}",
        f"available : {_fmt(body.get('available_balance'))} {asset}",
        f"unrealized: {_fmt(body.get('unrealized_pnl'))} {asset}",
    ]
    windows = body.get("realized_windows")
    if isinstance(windows, Mapping) and windows:
        lines.append("")
        for key in sorted(windows, key=str):
            value = windows[key]
            # `account` renders "n/a (income history withheld)" when a window is absent because
            # the venue's income page filled; a bare 0.00 there would assert "no loss today" to
            # the very breaker that measures the day. Keep the absence visible.
            lines.append(f"realized {key}: "
                         f"{'n/a (income history withheld)' if value is None else value} {asset}")
    count = body.get("open_position_count")
    lines += [
        "",
        f"positions : {_fmt(count)} open - detail: crypto_status",
    ]
    warnings = body.get("warnings")
    if isinstance(warnings, list) and warnings:
        lines.append("")
        lines += [f"warning   : {w}" for w in warnings]
    return "\n".join(lines)


def load_funds_board(*, now: str, root: Path | None = None) -> str:
    """The funds board, or a typed refusal. Reads a file; opens no socket.

    Refuses rather than renders zeros when there is nothing to render — a board of zeros is
    indistinguishable from an emptied account, which is the one reading that must never be
    produced by accident.
    """
    body = read_snapshot(root)
    if body is None:
        if snapshot_path(root).is_file():
            raise ToolError(
                ACCOUNT_SNAPSHOT_UNREADABLE,
                f"{SNAPSHOT_FILENAME} exists but will not parse; the scheduler rewrites it on "
                f"its next crypto fire",
            )
        raise ToolError(
            ACCOUNT_SNAPSHOT_MISSING,
            f"no account snapshot yet - the scheduler writes one on its crypto fire, at most "
            f"{REFRESH_AFTER_SECONDS // 60}m apart. If this persists the scheduler is not "
            f"running or has no account feed configured",
        )
    if not body.get("configured", False):
        raise ToolError(
            ACCOUNT_FEED_NOT_CONFIGURED,
            "the trading process has no account feed configured, so no balance was ever read",
        )
    if body.get("degraded"):
        raise ToolError(
            ACCOUNT_SNAPSHOT_DEGRADED,
            f"the last stored account read was degraded "
            f"({body.get('error_reason_code') or body.get('degraded_reason_code')})",
        )
    return render_funds_text(body, now=now)
