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

**What it speaks for is the LIVE entry gate, and only that.** It used to speak for the whole
runtime, because one merged verdict gated both legs. `guards.paper_trade_verdict` split them:
paper answers to data health and the `lifecycle` ladder, live answers to the loss breakers this
watch reads. So "refused" here has stopped meaning "the runtime is stopped" — paper trades
straight through a tripped breaker now, by design. The headline says which leg it is talking
about, because the alternative is a channel that reports the machine as idle while it is
working, and an operator has no way to catch that from the outside.

Four properties worth stating:

- **It decides nothing.** No gate, no store write beyond its own last-seen marker, no effect on
  routing. It reads what the live leg's guard step reads and reports the difference. A breaker
  that this module disagreed with would be this module's bug, so it runs the real
  ``guards.run_risk_guard`` against the real ``risk_limits`` rather than re-deriving anything.
- **The FIRST fire always announces.** With no marker on disk there is no "previous" to differ
  from, and staying silent would mean a freshly deployed watch says nothing until the state
  happens to change — indistinguishable from a watch that is not running.
- **It reports where the window's rows came from.** #411 took the paper record off the live gate:
  the breakers meter this runtime's LIVE outcomes and nothing else. Live has traded nothing, so
  every figure below is zero *because there is nothing to judge* — the breakers are INERT, not
  satisfied, and those are different facts about a brake. That is exactly the sentence an
  operator should not have to reconstruct from two other consoles, so the render states it
  outright instead of leaving it to be inferred from a count of zero.
- **It reports the r_basis mix of the PAPER rows.** Paper R became net of costs on 2026-07-30
  while the rows before it stayed gross, so a count spanning that day mixes two meanings of R.
  These rows no longer feed the breakers, so this qualifies the `rows` line beside it rather than
  the verdict above it. It stays because an operator who remembers a paper-driven `-19.35R`
  needs to see what that number was made of, and the basis mix is the first thing to check when
  it does not match a memory.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path
from typing import Any, Mapping

from .. import timeutil
from ..errors import ToolError
from ..filelock import locked
from . import guards, pool
from .live_pnl import live_outcomes_for_analysis, read_live_outcomes
from . import feedback
from .paper import read_outcomes, split_by_provenance, state_dir
from .risk_limits import resolve_risk_limits

WATCH_VERSION = "crypto_breaker_watch.v0.1"
MARK_FILENAME = "breaker_watch_mark.json"

# `ACK_EXPIRY_WARNING_HOURS` and `_ack_expiring_soon` lived here until 2026-08-03. They warned
# that Gate 0's operator acknowledgement was about to lapse, which mattered because the lapse
# shut the live door with nobody doing anything. With Gate 0 gone there is no acknowledgement,
# so there is nothing to lapse and no warning to give.


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
    """Today's breaker state, read the way the LIVE leg reads it.

    Deliberately the same composition as the RISK half of the guard step behind
    ``cycle.run_crypto_cycle``'s ``live_verdict`` — the bridged live outcomes, judged by the
    registered limits — because a watch that assembled its inputs differently would eventually
    report a state the runtime is not in. There is no paper equivalent to mirror: after the
    verdict split the paper leg consults no loss breaker at all, so this is the only leg with a
    transition to watch.

    **The risk half, and only that**, which is the one place this watch is knowingly coarser than
    the thing it reports on. ``live_verdict`` is ``merge_trade_verdict(health, risk)`` and allows
    only when BOTH halves do; data health is per-context — it needs a symbol, a timeframe and
    that context's candles — and this watch fires hourly with none of them. The omission is
    one-directional: the door reported here is a superset of the one the cycle opens, so it can
    read OPEN while a given context is held on stale data, and it can never read shut while the
    cycle would enter. A watch that erred the other way could go quiet about a door the runtime
    had opened, which is the failure this module exists to prevent; a false alarm is not.

    An unusable limits record propagates as a ``ToolError`` for the caller to surface: the
    live gate refuses entries in that state, so a watch must not report it as merely normal.
    """
    # Paper is read for the r_basis mix and the row split below, never for the verdict: the
    # breakers judge live outcomes only now, so a watch that still blended the two would report
    # a state the runtime is not in — the exact failure this module's docstring forbids.
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
    verdict = guards.run_risk_guard(live, now=now, limits=limits, routable_strategy_ids=routable)

    # Gate 0 was read here until 2026-08-03, as the second lock on this door. It is gone — see
    # `live_entry`'s docstring and `docs/proposals/GATE0_CANNOT_BE_SATISFIED_V0.1.md` — so this
    # watch speaks for the breaker alone, which is the whole of the door it can still see.
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
        # How the judged window splits between the two provenances. Both counts, not a ratio and
        # not only the live one: "0 live" is the load-bearing half of the sentence, and it is
        # meaningless without the paper count standing next to it.
        "own_closed": len(closed),
        "live_closed": sum(1 for r in live if r.get("outcome_closed") is True),
        # Present on every state, `applied: False` on almost all of them — the drawdown number
        # above means something different when it was measured over a narrowed population, and an
        # operator reading a release has to be able to tell which kind of release it was.
        "drawdown_baseline": verdict["drawdown_baseline"],
        # Zero here means the breakers are INERT, not satisfied. It is the load-bearing number on
        # this channel until live has traded, and "clear" without it reads as reassurance.
        "judged_rows": verdict["judged_rows"],
        "live_outcomes_excluded": bool(live_excluded),
        # The door. It had two locks and now has one, so this currently equals
        # `allow_new_position` — kept as its own field rather than collapsed into it because
        # they answer different questions ("is the breaker clear" vs "may an entry happen"),
        # and because this is where a second lock would compose back in if one is ever added.
        # A reader keying on the door should not have to know how many locks it has today.
        "live_entry_open": bool(verdict["allow_new_position"]),
    }


def has_changed(current: Mapping[str, Any], previous: Mapping[str, Any] | None) -> bool:
    """Whether this state is worth announcing.

    Keyed on the VERDICT — allow/refuse and which breakers are named — not on the R numbers,
    which move every settlement. A watch that fired on every number change would be a trade
    feed, and the operator already has one.

    Gate 0 was on the key too until 2026-08-03, because its operator acknowledgement stopped
    applying when time passed or the ladder demoted a strategy — transitions nobody DOES, so
    nobody would be watching. With Gate 0 removed the only thing that shuts this door is the
    breaker, and a breaker trips because something happened. `live_entry_open` stays on the key
    as the door itself rather than as a lock on it."""
    if previous is None:
        return True
    return (
        bool(current.get("allow_new_position")) != bool(previous.get("allow_new_position"))
        or sorted(current.get("problems") or []) != sorted(previous.get("problems") or [])
        or bool(current.get("live_entry_open")) != bool(previous.get("live_entry_open"))
    )


def render_text(current: Mapping[str, Any], previous: Mapping[str, Any] | None) -> str:
    """The operator-facing message. ASCII only, like every other channel render."""
    limits = current.get("limits") or {}
    # Every headline names the LIVE leg. The unqualified wording these replaced — "new positions
    # refused" — was true when one verdict gated both legs and became false the moment paper
    # stopped consulting the breakers: the message an operator would act on said the runtime was
    # stopped while paper kept opening positions. Naming the leg is the whole fix; the state
    # being reported is unchanged.
    # The headline reports the DOOR. It used to fall through to a "BREAKER TRIPPED/RELEASED"
    # branch when the breaker moved but the door did not — which happened whenever Gate 0 was
    # holding the door shut underneath a releasing breaker, and telling an operator "breaker
    # released" then would have been true and useless.
    #
    # With Gate 0 gone the door has one lock, so the breaker moving IS the door moving and that
    # branch became unreachable. Removed rather than left: an unreachable branch reads as a
    # state the system can be in, and the next person to touch this would have to prove it is
    # not. The two door lines below carry every transition the old four did.
    was_open = bool(previous.get("live_entry_open")) if previous is not None else None
    now_open = bool(current.get("live_entry_open"))
    if previous is None:
        headline = "CRYPTO LIVE ENTRY - first report"
    elif now_open and not was_open:
        headline = "CRYPTO LIVE ENTRY OPEN - real orders can now be placed"
    elif was_open and not now_open:
        headline = "CRYPTO LIVE ENTRY CLOSED - real orders refused again"
    else:
        headline = "CRYPTO LIVE ENTRY - reasons changed"

    lines = [
        headline,
        "  scope    : LIVE entries only - the paper leg answers to no loss breaker",
        f"  status   : {current.get('status')} (allow={current.get('allow_new_position')})",
        f"  daily    : {current.get('daily_pnl_r'):+.2f}R  (limit {limits.get('daily_max_loss_r')})",
        f"  weekly   : {current.get('weekly_pnl_r'):+.2f}R  (limit {limits.get('weekly_max_loss_r')})",
        f"  streak   : {current.get('consecutive_losses')}  (limit {limits.get('max_consecutive_losses')})",
        f"  drawdown : {current.get('drawdown_r'):+.2f}R  (limit -{limits.get('drawdown_limit_r')})",
        f"  problems : {', '.join(current.get('problems') or []) or 'none'}",
        f"  limits   : {limits.get('source')}",
    ]
    # The door, unconditionally. It used to print only when a Gate 0 reading existed, which was
    # right while the door had two locks and would now hide it whenever the one lock is the
    # whole answer.
    lines.append(f"  DOOR     : live entries {'OPEN' if current.get('live_entry_open') else 'REFUSED'}")
    own_closed, live_closed = current.get("own_closed"), current.get("live_closed")
    if own_closed is not None and live_closed is not None:
        # Both counts, and only one of them is the ruling. The breakers judge the LIVE rows; the
        # paper count stands beside it because it used to BE the ruling, and an operator who
        # remembers a `-19.35R` weekly needs to see where that number went rather than watch it
        # vanish between two reports.
        lines.append(f"  rows     : {live_closed} live closed (judged) | {own_closed} paper (not judged)")
        if live_closed == 0:
            # The load-bearing line until live has traded. "clear" and "clear because there is
            # nothing to judge" are different states of a brake, and only one of them is
            # reassuring — an operator reading the first when it is the second has been told the
            # opposite of the truth.
            lines.append("  NOTE: no live outcomes yet - the loss breakers are INERT, not satisfied")
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
