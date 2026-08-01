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
- **It reports where the window's rows came from.** The live gate meters own paper outcomes plus
  bridged live ones, and live has traded nothing — so every figure below is currently a
  simulation's, deciding a real-money door. That is the intended design (paper evidence is what
  says whether live may start) and it is also exactly the sentence an operator should not have
  to reconstruct from two other consoles.
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
from . import guards, live_candidate_ack, pool
from .live_pnl import live_outcomes_for_analysis, read_live_outcomes
from . import feedback
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
    """Today's breaker state, read the way the LIVE leg reads it.

    Deliberately the same composition as the guard step behind ``cycle.run_crypto_cycle``'s
    ``live_verdict`` — own paper outcomes plus the bridged live ones, judged by the registered
    limits — because a watch that assembled its inputs differently would eventually report a
    state the runtime is not in. There is no paper equivalent to mirror: after the verdict split
    the paper leg consults no loss breaker at all, so this is the only leg with a transition to
    watch. An unusable limits record propagates as a ``ToolError`` for the caller to surface: the
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

    # Gate 0's two answers — what the evidence computes, and what an operator signed. Read here
    # rather than left to the cycle, for this module's founding reason: a watch that assembled
    # its inputs differently would eventually report a state the runtime is not in.
    ack = live_candidate_ack.resolve_ack(root, now=now, routable_strategy_ids=routable)
    try:
        report, _text = feedback.run_paper_performance_report(
            now=now, root=root, routable_strategy_ids=routable
        )
        gate0 = {"eligible": bool(report["live_candidate_eligible"]), "sample": report["sample_size"]}
    except ToolError:
        # An unreadable paper store is exactly what Gate 0 refuses on, so the watch reports the
        # refusal rather than going quiet about a door it could not see.
        gate0 = {"eligible": False, "sample": None}

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
        # **Gate 0, the OTHER lock on the same door.** The breaker above answers "is the money
        # losing right now"; this answers "may it start at all", and an operator watching only
        # one of them is watching half a door. It matters most at the moment it SHUTS: an
        # operator acknowledgement expires on its own window and voids itself the instant the
        # routable pool changes, and both of those happen without anybody doing anything — so
        # they are exactly the transitions nobody would otherwise notice.
        "gate0_eligible": bool(gate0["eligible"]),
        "gate0_sample": gate0["sample"],
        "gate0_ack_applies": bool(ack["applies"]),
        "gate0_ack_reason": ack["reason"],
        "gate0_ack_id": (ack.get("record") or {}).get("acknowledgement_id"),
        "gate0_ack_valid_until": (ack.get("record") or {}).get("valid_until"),
        # What the two locks add up to. Recorded rather than re-derived by every reader, because
        # "the breaker released" and "the door opened" stopped being the same sentence.
        "live_entry_open": bool(verdict["allow_new_position"])
        and (bool(gate0["eligible"]) or bool(ack["applies"])),
    }


def has_changed(current: Mapping[str, Any], previous: Mapping[str, Any] | None) -> bool:
    """Whether this state is worth announcing.

    Keyed on the VERDICT — allow/refuse and which breakers are named — not on the R numbers,
    which move every settlement. A watch that fired on every number change would be a trade
    feed, and the operator already has one.

    **Gate 0 is on the key too, and that is the half nobody would otherwise see.** A breaker
    trips because something happened. An operator acknowledgement stops applying because time
    passed, or because the ladder demoted a strategy — neither of which anybody DOES, so neither
    produces a moment anyone is watching. `live_entry_open` carries the combined state and
    `gate0_ack_reason` carries which of the several ways it stopped applying, because "expired"
    and "the pool changed under it" want different responses: one is re-signed, the other is
    re-judged."""
    if previous is None:
        return True
    return (
        bool(current.get("allow_new_position")) != bool(previous.get("allow_new_position"))
        or sorted(current.get("problems") or []) != sorted(previous.get("problems") or [])
        or bool(current.get("live_entry_open")) != bool(previous.get("live_entry_open"))
        or current.get("gate0_ack_reason") != previous.get("gate0_ack_reason")
        or bool(current.get("gate0_eligible")) != bool(previous.get("gate0_eligible"))
    )


def render_text(current: Mapping[str, Any], previous: Mapping[str, Any] | None) -> str:
    """The operator-facing message. ASCII only, like every other channel render."""
    limits = current.get("limits") or {}
    # Every headline names the LIVE leg. The unqualified wording these replaced — "new positions
    # refused" — was true when one verdict gated both legs and became false the moment paper
    # stopped consulting the breakers: the message an operator would act on said the runtime was
    # stopped while paper kept opening positions. Naming the leg is the whole fix; the state
    # being reported is unchanged.
    # The headline reports the DOOR, not either lock on its own. An operator told "breaker
    # released" while Gate 0 still refuses has been told something true and useless; told
    # nothing at all when an acknowledgement lapsed, they would find out from the absence of
    # trades. The door shutting is the message this channel exists to carry.
    was_open = bool(previous.get("live_entry_open")) if previous is not None else None
    now_open = bool(current.get("live_entry_open"))
    if previous is None:
        headline = "CRYPTO LIVE ENTRY - first report"
    elif now_open and not was_open:
        headline = "CRYPTO LIVE ENTRY OPEN - real orders can now be placed"
    elif was_open and not now_open:
        headline = "CRYPTO LIVE ENTRY CLOSED - real orders refused again"
    elif current.get("gate0_ack_reason") != previous.get("gate0_ack_reason"):
        headline = "CRYPTO LIVE ENTRY - the operator acknowledgement changed"
    elif bool(current.get("allow_new_position")) != bool(previous.get("allow_new_position")):
        headline = ("CRYPTO LIVE BREAKER RELEASED" if current.get("allow_new_position")
                    else "CRYPTO LIVE BREAKER TRIPPED")
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
    # The Gate 0 block. Both readings, always — what the evidence computes and what a person
    # signed — because a line that showed only the effective answer could not tell a pool that
    # earned the gate from one that was waved through it.
    ack_reason = current.get("gate0_ack_reason")
    if ack_reason is not None:
        sample = current.get("gate0_sample")
        lines.append(
            f"  gate 0   : evidence={current.get('gate0_eligible')} "
            f"(sample {'?' if sample is None else sample}) | operator={current.get('gate0_ack_applies')}"
        )
        if current.get("gate0_ack_applies"):
            lines.append(f"  signed   : {current.get('gate0_ack_id')} until {current.get('gate0_ack_valid_until')}")
        elif ack_reason != "not_registered":
            # The transition this channel exists for. An acknowledgement stops applying because
            # time passed or the pool moved — nobody DOES either, so nobody is watching.
            lines.append(f"  NOTE: the operator acknowledgement no longer applies ({ack_reason})")
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
