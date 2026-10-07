"""The holdings snapshot the doors serve — written by the lane that holds the broker key.

P1-b of ``docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md``, ``crypto/account_store.py``'s shape for its
reason: the doors (the operator's Telegram verb, the assistant's read bridge) hold no broker key and
must not, so they never call the broker. ``scheduler-maint`` holds the key (Thomas 2026-10-02: a
holdings read is maintenance — late costs freshness, never money — and keeping the key there keeps it
out of the container that holds the Binance order key). Its ``holdings_refresh`` fire reads the Toss
account (appendix B, 2026-10-07) and writes what it saw here, and the doors render this file. The
figure is therefore as old as the last fire, and every render says how old.

**Only the aggregate is ever written.** The file holds :func:`board.aggregate_view` and three stamps,
nothing else. So no symbol, name, per-symbol number or exchange rate exists on disk, and any door that
renders this file is inside the external-send boundary by construction. The terminal's full board is
a live read (``scripts/holdings_board.py --full``).

**Two files, as ``account_store`` keeps them.** The snapshot is the last SUCCESSFUL read, and the mark
is the last ATTEMPT. A failed read moves the mark and leaves the last good figure in place.

**This lane is the one token issuer.** Toss allows one valid token per client, and issuing one revokes
the previous one. The scheduler runs this kind in its own process (only factory fires fork), so the
capable feed — and its in-memory token — is kept here between fires and serves until the token nears
expiry. If something else issued a token for the same client meanwhile, the feed reissues once and
retries once (``toss_account``). The cache is dropped the moment the gate no longer selects the
capable feed. Revocation is unchanged: unset the variable and restart the container.

A state file on ``account_store``'s precedent, not a ledger record: no closed schema is owed, and
nothing judges by it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .. import timeutil
from ..errors import ToolError
from ..filelock import locked
from ..paths import repo_root as _repo_root
from .board import aggregate_view, render_view
from .toss_account import TossHoldingsFeed, read_holdings, select_holdings_feed

STATE_REL = ".runtime_governance_state/holdings"
SNAPSHOT_FILENAME = "holdings_snapshot.json"
REFRESH_MARK_FILENAME = "holdings_refresh.json"
RECORD_TYPE = "holdings_snapshot.v1"

# When a rendered figure stops being a statement about now. Three hourly fires missed in a row.
STALE_AFTER_SECONDS = 3 * 60 * 60

HOLDINGS_SNAPSHOT_MISSING = "HOLDINGS_SNAPSHOT_MISSING"
HOLDINGS_SNAPSHOT_UNREADABLE = "HOLDINGS_SNAPSHOT_UNREADABLE"

# The capable feed, kept between fires so its token is too (module docstring).
_cached_feed: TossHoldingsFeed | None = None


def state_dir(root: Path | None = None) -> Path:
    return (root if root is not None else _repo_root()) / STATE_REL


def snapshot_path(root: Path | None = None) -> Path:
    return state_dir(root) / SNAPSHOT_FILENAME


def refresh_mark_path(root: Path | None = None) -> Path:
    return state_dir(root) / REFRESH_MARK_FILENAME


def _write_json(path: Path, body: Mapping[str, Any], *, code: str, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code=code, label=label):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(path)


def _feed() -> Any:
    """The feed this fire reads through: the cached capable one while the gate still selects it."""
    global _cached_feed
    selected = select_holdings_feed()
    if not isinstance(selected, TossHoldingsFeed):
        _cached_feed = None
        return selected
    if _cached_feed is None:
        _cached_feed = selected
    return _cached_feed


def refresh_snapshot(*, now: str, root: Path | None = None, timeout_seconds: int = 10) -> str:
    """Read the broker once and store the aggregate. Returns a one-line status for the fire.

    Never raises: a holdings read is bookkeeping, and a failure becomes a status string and a moved
    mark. A failed read keeps the previous snapshot."""
    try:
        _write_json(refresh_mark_path(root), {"attempted_at": now},
                    code="HOLDINGS_REFRESH_MARK_LOCKED", label="holdings refresh mark")
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return f"holdings snapshot: mark not written ({type(exc).__name__})"
    try:
        snapshot, reason = read_holdings(timeout_seconds=timeout_seconds, feed=_feed())
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return f"holdings snapshot: read failed ({type(exc).__name__})"
    if snapshot is None:
        if reason == "NOT_CONFIGURED":
            return "holdings snapshot: no broker account configured"
        return f"holdings snapshot: degraded ({reason}); kept the previous one"
    body = dict(aggregate_view(snapshot))
    body["record_type"] = RECORD_TYPE
    body["as_of"] = snapshot.collected_at or now
    body["written_at"] = now
    try:
        _write_json(snapshot_path(root), body, code="HOLDINGS_SNAPSHOT_LOCKED", label="holdings snapshot")
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return f"holdings snapshot: not persisted ({type(exc).__name__})"
    return "holdings snapshot: refreshed"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise _unreadable(path) from None
    if not isinstance(body, dict):
        raise _unreadable(path)
    return body


def _unreadable(path: Path) -> ToolError:
    return ToolError(HOLDINGS_SNAPSHOT_UNREADABLE, f"{path.name} is not a readable snapshot")


def _age_seconds(stamp: Any, now: str) -> float | None:
    try:
        return (timeutil.parse_iso(now) - timeutil.parse_iso(str(stamp))).total_seconds()
    except (TypeError, ValueError):
        return None


def load_holdings_view(*, now: str, root: Path | None = None) -> tuple[str, dict[str, Any]]:
    """The board the doors render, and its data. Opens no socket: it reads a file.

    A missing snapshot is a normal state (no key yet, or no fire yet) and says so. An unreadable one
    raises: a board that rendered as empty because a file would not parse would be a false
    statement about money."""
    body = _read_json(snapshot_path(root))
    if body is None:
        mark = _read_json(refresh_mark_path(root))
        attempted = mark.get("attempted_at") if mark else None
        text = (
            "holdings    : no snapshot yet"
            + (f" (last attempt {attempted})" if attempted else " (no refresh has run)")
        )
        return text, {"available": False, "reason_code": HOLDINGS_SNAPSHOT_MISSING,
                      "last_attempt": attempted}
    age = _age_seconds(body.get("as_of"), now)
    stale = age is None or age > STALE_AFTER_SECONDS
    age_text = "unknown age" if age is None else f"{int(age // 60)} min old"
    lines = render_view(body, stamp_line=f"{'as of':12}: {body.get('as_of')} ({age_text})")
    if stale:
        lines.append(f"{'STALE':12}: older than {STALE_AFTER_SECONDS // 3600} h; not a statement about now")
    lines.extend(f"{'note':12}: {note}" for note in body.get("notes") or [])
    data = {key: body.get(key) for key in sorted(body) if key not in ("record_type", "written_at")}
    data.update({"available": True, "age_seconds": age, "stale": stale})
    return "\n".join(lines), data
