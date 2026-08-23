"""The C4 breaker's transition watch — speak when the verdict CHANGES, not on a timer.

**Updated 2026-08-23.** The edge trigger below is unchanged and still ignores the numbers, but
it had a blind spot that follows from being an edge trigger fired on a schedule: a verdict that
moves and moves back BETWEEN two fires shows the same state at both ends, so the whole episode
was invisible on this channel. On 2026-08-21 the daily limit breached at 03:59:13Z, the limits
record went unusable at 04:14:00Z and a new record cleared it at 04:29:00Z — the 03:58 and
04:58 fires both read NORMAL and both stayed quiet, correctly by the rule and uselessly for the
operator. `transitions_since` reads the cycle ledger for the gap since the last ANNOUNCEMENT
(the mark advances only when something is said), and a gap that contains a transition is now
the one non-numeric reason to speak that did not exist before.

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
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

from .. import paths, timeutil
from ..store import LEDGER_REL, RECORDS_FILE
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


# How far back a fire will look for transitions it did not report. The mark advances only when
# something is ANNOUNCED, so a quiet month leaves a month-wide gap — and `ledger_rotate` will
# have moved most of it to an archive this reader does not open. Seven days is the honest reach:
# past it the answer would be "nothing found", which is not the same as "nothing happened" and
# must not be reported as though it were. `transition_coverage` says which one it is.
TRANSITION_WINDOW_SECONDS = 7 * 24 * 60 * 60

# One line per row is the memory bound, as in `dashboard._read_cycle_records`: the live ledger
# is tens of megabytes and this runs on a 3.8GB host beside the trading loop.
_CYCLE_HINT = '"crypto_cycle"'


def _verdict_key(record: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    """The part of a stored cycle this watch would have announced: allow, and why not."""
    problems = record.get("verdict_problems") or []
    return (
        str(record.get("verdict_status") or ""),
        tuple(sorted(str(p) for p in problems)),
    )


def transitions_since(
    root: Path | None, *, since: str | None, now: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Verdict transitions recorded in the cycle ledger since the last ANNOUNCEMENT.

    This is the blind spot the watch had, and it is structural rather than accidental. The
    watch is an edge trigger comparing now against the last state it *announced*, and it fires
    hourly. A breaker that trips and releases between two fires shows the same verdict at both
    ends, so the episode leaves no trace on this channel at all — on 2026-08-21 the daily limit
    breached at 03:59:13Z, the limits record went unusable at 04:14:00Z, and a new record
    cleared it at 04:29:00Z, entirely inside one hour. The 03:58 and 04:58 fires both read
    NORMAL and both stayed quiet, which was correct by the rule and useless to the operator.

    The cycle ledger already holds every verdict the runtime actually acted on, so the gap is
    readable after the fact. Reading it does not change what the watch decides — the numbers
    still do not trigger, which is the property `has_changed` exists to hold — it changes what
    the announcement CONTAINS when one happens, and adds the one new reason to announce:
    something changed while nobody was looking.

    Returns ``(transitions, coverage)``. Coverage is not decoration: a window this reader
    cannot cover must not read as an empty one.
    """
    coverage: dict[str, Any] = {"read": False, "rows": 0, "since": since,
                                "oldest_seen": None, "truncated": False}
    if not since:
        # No mark: `run_breaker_watch` already announces unconditionally on a first fire, and
        # a fresh deployment replaying a week of history into that first message is the burst
        # this module's docstring spends four paragraphs arguing against.
        return [], coverage
    try:
        floor = max(
            timeutil.parse_iso(since),
            timeutil.parse_iso(now) - timedelta(seconds=TRANSITION_WINDOW_SECONDS),
        )
        coverage["truncated"] = (
            timeutil.parse_iso(now) - timeutil.parse_iso(since)
        ).total_seconds() > TRANSITION_WINDOW_SECONDS
    except (ValueError, TypeError):
        return [], coverage

    # The ledger's own constants, not a path spelled again here: a second spelling is how a
    # reader ends up looking in the directory the writer stopped using.
    path = (root if root is not None else paths.repo_root()) / LEDGER_REL / RECORDS_FILE
    if not path.is_file():
        return [], coverage

    rows: list[dict[str, Any]] = []
    oldest: str | None = None
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if _CYCLE_HINT not in line:
                    continue
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue  # a torn row is not this reader's business
                if parsed.get("kind") != "crypto_cycle":
                    continue
                record = parsed.get("record") or {}
                stamp = str(record.get("created_at") or "")
                if not stamp:
                    continue
                if oldest is None or stamp < oldest:
                    oldest = stamp
                try:
                    if timeutil.parse_iso(stamp) < floor:
                        continue
                except (ValueError, TypeError):
                    continue
                rows.append(record)
    except OSError:
        return [], coverage

    coverage.update({"read": True, "rows": len(rows), "oldest_seen": oldest})
    rows.sort(key=lambda r: str(r.get("created_at") or ""))

    transitions: list[dict[str, Any]] = []
    previous: tuple[str, tuple[str, ...]] | None = None
    previous_limits: str | None = None
    for record in rows:
        key = _verdict_key(record)
        limits_id = str((record.get("risk_limits") or {}).get("limits_id") or "")
        if previous is not None and key != previous:
            transitions.append({
                "at": str(record.get("created_at")),
                "from_status": previous[0], "to_status": key[0],
                "from_problems": list(previous[1]), "to_problems": list(key[1]),
                "limits_id": limits_id or None,
            })
        # A limits record swap is its own event. It is how three of this year's breaker
        # releases actually happened — not the streak recovering, but the bar moving — and a
        # transition list that showed only the verdict would report the release with no cause.
        if previous_limits is not None and limits_id and limits_id != previous_limits:
            transitions.append({
                "at": str(record.get("created_at")),
                "limits_swapped_from": previous_limits, "limits_swapped_to": limits_id,
            })
        previous = key
        if limits_id:
            previous_limits = limits_id
    return transitions, coverage


def evaluate(
    root: Path | None = None, *, now: str, since: str | None = None,
) -> dict[str, Any]:
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
    missed, coverage = transitions_since(root, since=since, now=now)
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
        # What happened between the last announcement and now. Empty on almost every fire, and
        # empty is the answer this watch is designed to produce — see `transitions_since`.
        "missed_transitions": missed,
        "transition_coverage": coverage,
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
        # The one reason added 2026-08-23, and it is still not a number: a verdict that moved
        # and moved back between two fires leaves both ends equal, so the three keys above all
        # say "unchanged" about an hour in which the door shut and reopened. This asks the
        # cycle ledger whether that happened rather than asking the current numbers, which is
        # why `test_moving_numbers_alone_do_not_announce` stays green.
        or bool(current.get("missed_transitions"))
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

    # What happened while nobody was looking. Printed last because it is history and the block
    # above is the present, and an operator reading a shut door wants the door first.
    missed = current.get("missed_transitions") or []
    if missed:
        lines.append("")
        lines.append(f"  MISSED between announcements ({len(missed)}) - this watch fires hourly "
                     "and these")
        lines.append("  moved and settled inside a gap, so both ends read the same:")
        for item in missed:
            if item.get("limits_swapped_to"):
                lines.append(
                    f"    {item.get('at')}  limits record swapped "
                    f"{item.get('limits_swapped_from')} -> {item.get('limits_swapped_to')}")
                continue
            was = ",".join(item.get("from_problems") or []) or "none"
            became = ",".join(item.get("to_problems") or []) or "none"
            lines.append(f"    {item.get('at')}  {item.get('from_status')} [{was}]"
                         f" -> {item.get('to_status')} [{became}]")
    coverage = current.get("transition_coverage") or {}
    if coverage.get("truncated"):
        lines.append(f"  NOTE: only the last {TRANSITION_WINDOW_SECONDS // 86400} days of "
                     "cycles were searched; anything older is not")
        lines.append("        'nothing happened', it is 'not looked at'")
    elif coverage.get("since") and not coverage.get("read"):
        lines.append("  NOTE: the cycle ledger could not be read, so a transition in the gap "
                     "would not appear here")
    return "\n".join(lines)


def run_breaker_watch(root: Path | None = None, *, now: str, persist: bool = True) -> dict[str, Any]:
    """Evaluate, decide whether to speak, and record what was said. Pure of transport.

    Returns ``{"state", "changed", "text", "previous"}``; sending is the caller's job, so this
    is testable with no channel and the scheduler keeps the one place that talks to Telegram.
    ``persist=False`` runs the whole comparison and writes nothing — what a dry run needs, and
    what a caller that failed to deliver should use so the next fire retries the announcement.
    """
    previous = read_mark(root)
    # The mark's stamp is when this watch last SPOKE, not when it last ran — `write_mark` is
    # called only on an announcement. That is exactly the window a missed transition hides in.
    state = evaluate(root, now=now, since=(previous or {}).get("created_at_utc"))
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
    limits = state.get("limits") or {}
    missed = state.get("missed_transitions") or []
    # Every fire's numbers, on every fire — this string lands in the scheduler's `fired` event,
    # so writing them here gives the breakers an hourly time series at no storage cost and with
    # no new store. It is NOT a change to what announces: `has_changed` still ignores numbers,
    # and this line is read after the fact by someone asking "when did that move?", which on
    # 2026-08-21 could only be answered by re-deriving the guard from the ledger by hand.
    numbers = (
        f"daily={state.get('daily_pnl_r')} weekly={state.get('weekly_pnl_r')} "
        f"streak={state.get('consecutive_losses')}/{limits.get('max_consecutive_losses')} "
        f"dd={state.get('drawdown_r')}/{limits.get('drawdown_limit_r')} "
        f"judged={state.get('judged_rows')} limits={limits.get('limits_id')}"
    )
    gap = f" missed={len(missed)}" if missed else ""
    if not result.get("changed"):
        return f"breaker_unchanged status={state.get('status')} {numbers}{gap}"
    return (
        f"breaker_changed status={state.get('status')} "
        f"allow={state.get('allow_new_position')} {numbers}{gap} "
        f"problems={','.join(state.get('problems') or []) or 'none'}"
    )
