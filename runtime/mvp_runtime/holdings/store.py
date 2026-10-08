"""The holdings snapshot the doors serve — written by the lane that holds the broker key.

P1-b of ``docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md``, ``crypto/account_store.py``'s shape for its
reason: the doors (the operator's Telegram verb, the assistant's read bridge) hold no broker key and
must not, so they never call the broker. ``scheduler-maint`` holds the key (Thomas 2026-10-02: a
holdings read is maintenance — late costs freshness, never money — and keeping the key there keeps it
out of the container that holds the Binance order key). Its ``holdings_refresh`` fire reads the Toss
account (appendix B, 2026-10-07) and writes what it saw here, and the doors render this file. The
figure is therefore as old as the last fire, and every render says how old.

**Only aggregates are ever written, in two files split by who reads them** (H3, Thomas 2026-10-08,
``disclosure``). No symbol, name, per-symbol number or exchange rate exists on disk in either.

- ``holdings_snapshot.json`` — what the doors render. One table (the allocation's asset classes against
  the NAV) with the single-holding rule applied, and the status words. :func:`disclosure.external_view`
  makes it; :data:`disclosure.LOCAL_ONLY_TOP_KEYS` and its siblings name what it leaves out.
- ``holdings_local.json`` (:data:`LOCAL_FILENAME`) — the whole fire: Toss by market, Binance by wallet
  and sub-class, every class amount. Read by ``scripts/holdings_board.py --local`` on the account
  holder's terminal and by nothing else; a test pins that no door module names it.

The terminal's per-symbol board is a live read (``scripts/holdings_board.py --full``).

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
from . import allocation, binance_wallet, combined, disclosure
from .board import aggregate_view, render_view
from .toss_account import TossHoldingsFeed, read_holdings, select_holdings_feed

STATE_REL = ".runtime_governance_state/holdings"
SNAPSHOT_FILENAME = "holdings_snapshot.json"
LOCAL_FILENAME = "holdings_local.json"
LOCAL_RECORD_TYPE = "holdings_local.v1"
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


def local_path(root: Path | None = None) -> Path:
    return state_dir(root) / LOCAL_FILENAME


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


def refresh(*, now: str, root: Path | None = None, timeout_seconds: int = 10) -> dict[str, Any]:
    """Read the broker once, combine it with the Binance snapshot file (P2), store the aggregate.

    Returns ``{"status": <one line>, "alert": (state, text) | None}``. The alert is the drawdown edge
    since the last DELIVERED message (``combined.alert``); the caller sends it and only then records it
    with :func:`mark_told`, so a message the channel did not take is offered again next fire.

    Never raises: a holdings read is bookkeeping, and a failure becomes a status string and a moved
    mark. A failed read keeps the previous snapshot."""
    try:
        _write_json(refresh_mark_path(root), {"attempted_at": now},
                    code="HOLDINGS_REFRESH_MARK_LOCKED", label="holdings refresh mark")
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return {"status": f"holdings snapshot: mark not written ({type(exc).__name__})", "alert": None}
    try:
        snapshot, reason = read_holdings(timeout_seconds=timeout_seconds, feed=_feed())
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return {"status": f"holdings snapshot: read failed ({type(exc).__name__})", "alert": None}
    if snapshot is None:
        if reason == "NOT_CONFIGURED":
            return {"status": "holdings snapshot: no broker account configured", "alert": None}
        return {"status": f"holdings snapshot: degraded ({reason}); kept the previous one", "alert": None}
    body = dict(aggregate_view(snapshot))
    body["record_type"] = RECORD_TYPE
    body["as_of"] = snapshot.collected_at or now
    body["written_at"] = now
    base = root if root is not None else _repo_root()
    # The Binance spot wallet and Simple Earn (appendix C), behind their own gate. A failure here costs
    # the combined total its completeness, never the Toss snapshot.
    try:
        wallet, wallet_reason = binance_wallet.read_wallet()
    except Exception as exc:  # noqa: BLE001 — see the docstring
        wallet, wallet_reason = None, type(exc).__name__
    wallet_status = (combined.WALLET_NOT_CONFIGURED if wallet is None and wallet_reason == "NOT_CONFIGURED"
                     else f"failed ({wallet_reason})" if wallet is None else combined.WALLET_OK)
    try:
        block = combined.combine(body, usd_krw_rate=snapshot.usd_krw_rate, root=base, now=now,
                                 state_dir=state_dir(root), wallet=wallet, wallet_status=wallet_status,
                                 toss_warnings=len(snapshot.warnings), toss_as_of=snapshot.collected_at,
                                 toss_reconciliation_failures=combined.toss_reconciliation(snapshot))
    except Exception as exc:  # noqa: BLE001 — the combined total must not cost the Toss snapshot
        block = None
        combined_note = f"combined not computed ({type(exc).__name__})"
    else:
        combined_note = (f"combined {block['drawdown_state']}"
                         + ("" if block["portfolio_nav_complete"] else "; portfolio NAV incomplete"))
    body["combined"] = block
    # Q9 (TOTAL_ASSET_ALLOCATION §11.1): class totals against the target, display only. Same rule as
    # the combined total: a failure costs the board this block, never the Toss snapshot.
    parts: dict[str, Any] = {}
    try:
        body["allocation"], parts = allocation.allocate_with_parts(snapshot, body, block, wallet)
    except Exception as exc:  # noqa: BLE001 — see the comment above
        body["allocation"] = None
        combined_note += f"; allocation not computed ({type(exc).__name__})"
    # H3: the doors get one table under the single-holding rule; the whole fire goes to the local file.
    # Without the allocation's makeup there is no table to judge, so nothing leaves but status words.
    try:
        outward = disclosure.external_view(body, parts)
        if body.get("allocation") is None and isinstance(outward.get("combined"), dict):
            outward["combined"].update({"combined_total_krw": None, "peak_total_krw": None})
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return {"status": f"holdings snapshot: not disclosed ({type(exc).__name__})", "alert": None}
    try:
        _write_json(local_path(root), {**body, "record_type": LOCAL_RECORD_TYPE},
                    code="HOLDINGS_LOCAL_LOCKED", label="holdings local board")
        _write_json(snapshot_path(root), outward, code="HOLDINGS_SNAPSHOT_LOCKED", label="holdings snapshot")
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return {"status": f"holdings snapshot: not persisted ({type(exc).__name__})", "alert": None}
    edge = None
    if block is not None:
        # The alert is a Telegram message, so it is built from what may leave.
        edge = combined.alert(outward["combined"], told=combined.read_told(state_dir(root)), as_of=body["as_of"])
    return {"status": f"holdings snapshot: refreshed; {combined_note}", "alert": edge}


def mark_told(state: str, *, now: str, root: Path | None = None) -> None:
    """Record the drawdown state Thomas was told — called only after the message was delivered."""
    combined.write_told(state_dir(root), state, now=now)


def refresh_snapshot(*, now: str, root: Path | None = None, timeout_seconds: int = 10) -> str:
    """:func:`refresh`'s status line alone, for callers that send no alert."""
    return refresh(now=now, root=root, timeout_seconds=timeout_seconds)["status"]


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


def load_local_view(*, now: str, root: Path | None = None) -> str:
    """The whole last fire, for the account holder's terminal only (``holdings_board --local``). Never
    route this text to a door, a channel or a model prompt: that is what the stored snapshot is for."""
    body = _read_json(local_path(root))
    if body is None:
        return "holdings    : no local board yet (written by the next holdings_refresh fire)"
    age = _age_seconds(body.get("as_of"), now)
    age_text = "unknown age" if age is None else f"{int(age // 60)} min old"
    lines = ["LOCAL BOARD - terminal only; do not paste into a chat, a model prompt or a channel",
             *render_view(body, stamp_line=f"{'as of':12}: {body.get('as_of')} ({age_text})")]
    lines.extend(f"{'note':12}: {note}" for note in body.get("notes") or [])
    return "\n".join(lines)


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
