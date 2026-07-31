"""The C4 breaker's transition watch — speak when the verdict CHANGES, not on a timer.

`crypto_report` already renders the dashboard daily, and the breaker's current state is one
line in it. That answers *"is it blocked right now?"* for anyone who reads that morning's
message. It does not answer *"when did it release?"*, which is the question an operator
actually waits on: a level reported every day buries the one day it flipped among the days it
did not, and the flip is what changes whether the runtime is trading.

So this watch is an EDGE trigger. It evaluates the same guard the cycle does, compares the
verdict against the last one it announced, and notifies only on a change. A quiet run is the
normal run — a watch that speaks every fire is a watch an operator learns to skip, which is the
failure mode this exists to avoid.

Three properties worth stating:

- **It decides nothing.** No gate, no store write beyond its own last-seen marker, no effect on
  routing. It reads what the cycle would read and reports the difference. A breaker that this
  module disagreed with would be this module's bug, so it runs the real
  ``guards.run_risk_guard`` against the real ``risk_limits`` rather than re-deriving anything.
- **The FIRST fire always announces.** With no marker on disk there is no "previous" to differ
  from, and staying silent would mean a freshly deployed watch says nothing until the state
  happens to change — indistinguishable from a watch that is not running.
- **It reports the r_basis mix.** Paper R became net of costs on 2026-07-30 while the rows
  before it stayed gross, so a window spanning that day under-states its losses. The breaker's
  number is only as honest as its window's basis, and an operator reading "-4.8R, clear" should
  be able to see how much of that window predates the change.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path
from typing import Any, Mapping

from ..errors import ToolError
from ..filelock import locked
from . import guards, pool
from .live_pnl import live_outcomes_for_analysis, read_live_outcomes
from .paper import read_outcomes, split_by_provenance, state_dir
from .risk_limits import resolve_risk_limits

WATCH_VERSION = "crypto_breaker_watch.v0.1"
MARK_FILENAME = "breaker_watch_mark.json"


def mark_path(root: Path | None = None) -> Path:
    """The last-announced verdict, so a quiet run can tell "unchanged" from "first run"."""
    return state_dir(root) / MARK_FILENAME


def read_mark(root: Path | None = None) -> dict[str, Any] | None:
    """The last announced state, or None when nothing has been announced yet.

    Fail-open toward None on a corrupt file, deliberately and unlike this repo's usual
    posture: the worst case is one redundant announcement, and the alternative — refusing to
    watch because the marker is unreadable — silences the thing whose whole job is to speak.
    Nothing here authorizes anything, so there is no permission to fail closed on."""
    path = mark_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_mark(state: Mapping[str, Any], *, root: Path | None = None) -> Path:
    path = mark_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code="BREAKER_WATCH_LOCKED", label="breaker watch mark"):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(state), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        tmp.replace(path)
    return path


def evaluate(root: Path | None = None, *, now: str) -> dict[str, Any]:
    """Today's breaker state, read the way the cycle reads it.

    Deliberately the same composition as ``cycle.run_crypto_cycle``'s guard step — own paper
    outcomes plus the bridged live ones, judged by the registered limits — because a watch that
    assembled its inputs differently would eventually report a state the runtime is not in.
    An unusable limits record propagates as a ``ToolError`` for the caller to surface: the
    cycle refuses entries in that state, so a watch must not report it as merely normal.
    """
    own, _imported = split_by_provenance(read_outcomes(root))
    live, live_excluded = live_outcomes_for_analysis(read_live_outcomes(root))
    limits = resolve_risk_limits(root, now=now)
    # The routable set the drawdown baseline is re-checked against, read the way the cycle reads
    # it — including the distinction that carries the fail-closed property: an unreadable pool is
    # `None` (cannot verify, so no lineage leaves the window), never the empty set (every named
    # lineage confirmed retired). A watch that collapsed those two would announce a released
    # breaker on the strength of a failed read.
    try:
        routable = pool.routable_strategy_ids(pool.load_active_pool(root))
    except ToolError:
        routable = None
    verdict = guards.run_risk_guard(own + live, now=now, limits=limits, routable_strategy_ids=routable)

    closed = [r for r in own if r.get("outcome_closed") is True]
    basis = collections.Counter(str(r.get("r_basis") or "unlabelled") for r in closed)
    return {
        "watch_version": WATCH_VERSION,
        "created_at_utc": now,
        "status": verdict["status"],
        "allow_new_position": bool(verdict["allow_new_position"]),
        "problems": list(verdict["problems"]),
        "daily_pnl_r": verdict["daily_pnl_r"],
        "weekly_pnl_r": verdict["weekly_pnl_r"],
        "consecutive_losses": verdict["consecutive_losses"],
        "drawdown_r": verdict["drawdown_r"],
        "limits": verdict["limits"],
        "r_basis_counts": dict(sorted(basis.items())),
        # Present on every state, `applied: False` on almost all of them — the drawdown number
        # above means something different when it was measured over a narrowed population, and an
        # operator reading a release has to be able to tell which kind of release it was.
        "drawdown_baseline": verdict["drawdown_baseline"],
        "live_outcomes_excluded": bool(live_excluded),
    }


def has_changed(current: Mapping[str, Any], previous: Mapping[str, Any] | None) -> bool:
    """Whether this state is worth announcing.

    Keyed on the VERDICT — allow/refuse and which breakers are named — not on the R numbers,
    which move every settlement. A watch that fired on every number change would be a trade
    feed, and the operator already has one."""
    if previous is None:
        return True
    return (
        bool(current.get("allow_new_position")) != bool(previous.get("allow_new_position"))
        or sorted(current.get("problems") or []) != sorted(previous.get("problems") or [])
    )


def render_text(current: Mapping[str, Any], previous: Mapping[str, Any] | None) -> str:
    """The operator-facing message. ASCII only, like every other channel render."""
    limits = current.get("limits") or {}
    if previous is None:
        headline = "CRYPTO BREAKER - first report"
    elif current.get("allow_new_position") and not previous.get("allow_new_position"):
        headline = "CRYPTO BREAKER RELEASED - new positions allowed again"
    elif not current.get("allow_new_position") and previous.get("allow_new_position"):
        headline = "CRYPTO BREAKER TRIPPED - new positions refused"
    else:
        headline = "CRYPTO BREAKER - reasons changed"

    lines = [
        headline,
        f"  status   : {current.get('status')} (allow={current.get('allow_new_position')})",
        f"  daily    : {current.get('daily_pnl_r'):+.2f}R  (limit {limits.get('daily_max_loss_r')})",
        f"  weekly   : {current.get('weekly_pnl_r'):+.2f}R  (limit {limits.get('weekly_max_loss_r')})",
        f"  streak   : {current.get('consecutive_losses')}  (limit {limits.get('max_consecutive_losses')})",
        f"  drawdown : {current.get('drawdown_r'):+.2f}R  (limit -{limits.get('drawdown_limit_r')})",
        f"  problems : {', '.join(current.get('problems') or []) or 'none'}",
        f"  limits   : {limits.get('source')}",
    ]
    if previous is not None:
        was = ', '.join(previous.get("problems") or []) or 'none'
        lines.append(f"  previous : {previous.get('status')} [{was}]")
    counts = current.get("r_basis_counts") or {}
    if counts:
        mix = ", ".join(f"{k} {v}" for k, v in counts.items())
        lines.append(f"  r_basis  : {mix}")
        # Named only when it actually applies: a window still holding pre-2026-07-30 rows is
        # measuring part of its loss without the costs that were charged on the rest.
        if len(counts) > 1:
            lines.append("  NOTE: mixed R bases - the gross rows understate their share of the loss")
    if current.get("live_outcomes_excluded"):
        lines.append("  NOTE: at least one live outcome had no honest R and was not counted")
    return "\n".join(lines)


def run_breaker_watch(root: Path | None = None, *, now: str, persist: bool = True) -> dict[str, Any]:
    """Evaluate, decide whether to speak, and record what was said. Pure of transport.

    Returns ``{"state", "changed", "text", "previous"}``; sending is the caller's job, so this
    is testable with no channel and the scheduler keeps the one place that talks to Telegram.
    ``persist=False`` runs the whole comparison and writes nothing — what a dry run needs, and
    what a caller that failed to deliver should use so the next fire retries the announcement.
    """
    previous = read_mark(root)
    state = evaluate(root, now=now)
    changed = has_changed(state, previous)
    if changed and persist:
        write_mark(state, root=root)
    return {
        "state": state,
        "previous": previous,
        "changed": changed,
        "text": render_text(state, previous) if changed else "",
    }


def status_line(result: Mapping[str, Any]) -> str:
    """One line for the scheduler's run record."""
    state = result.get("state") or {}
    if not result.get("changed"):
        return f"breaker_unchanged status={state.get('status')} weekly={state.get('weekly_pnl_r')}"
    return (
        f"breaker_changed status={state.get('status')} "
        f"allow={state.get('allow_new_position')} weekly={state.get('weekly_pnl_r')} "
        f"problems={','.join(state.get('problems') or []) or 'none'}"
    )
