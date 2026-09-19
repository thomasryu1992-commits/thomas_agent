"""LP6 live-readiness board — every condition between here and a live order, in one read.

    python -m runtime.mvp_runtime.crypto.live_readiness
    python -m runtime.mvp_runtime.crypto.live_readiness --json

Read-only and ungated: it writes nothing, places nothing, and holds no authority of its own.
It answers one question — *what is still standing between this machine and an autonomous live
order* — by asking each gate directly rather than by reasoning about them from documentation,
so an answer here cannot drift from what the code actually enforces.

**"This machine" is the whole claim, and it is not the whole system.** Most rows are computed
from ``os.environ``, so this board answers for the process that runs it. That was invisible
while only the scheduler ran it and became a false statement the moment other surfaces did:
the operator console and the assistant read door run in containers that deliberately carry no
``MVP_LIVE_*``, and both rendered "live trading off" while the scheduler held an open gate
(#382). The env is not forwarded to fix that — keeping the money path out of those containers
is the point. Instead the board reports what the trading process itself last recorded
(``recorded_gate``, from the cycle ledger both already mount) and refuses to render a bare
"off" that its own environment is not entitled to claim. ``ready`` still means "can THIS
process trade", because the CLI exit code is documented as a script precondition.

It opens **one** socket, and only when the operator has already configured an account feed:
the daily-loss breaker measures against what the venue realized, because the local outcome
ledger cannot supply that figure (its only writer is the autonomous leg nothing may import,
and the canary door, removed 2026-09-15, was entry-only). Without a configured feed the board
makes no outbound call at all and the breaker row fails for want of a source — which is the
honest answer, not a degraded one. The read is the same gated, read-only `account` module the
dashboard uses; it cannot place, amend, or cancel anything.

The board opens and closes with the readiness state (crypto PR5a/5b): ``LIVE ENTRY POSSIBLE``
YES / NO / UNKNOWN, with what refuses an entry and what this process cannot see. That — not READY —
is whether an autonomous entry can open. READY is this process's own verdict, printed as
``THIS PROCESS: ...``: since LP4 landed (2026-07-25) an order path **does** exist, so a READY
process is one from which a real order could actually be placed. The board says both out loud
rather than letting a row of green ticks read as harmless, or a red row as "live trading off".

**Status claims live in computed rows, not in prose.** This module's whole purpose is that an
answer here cannot drift from what the code enforces — and it drifted anyway: for a day after the
LP5 executing leg shipped, this docstring still described it as missing. The computed rows were
right the whole time; the sentence a human reads before risking money was wrong. So whether an
autonomous path exists is now the `autonomous_routing_wired` row, derived from a constant that a
test pins to the actual import graph, and a second test asserts this prose makes no build claim.

Exit code is 0 only when every check passes — THIS process is READY — so it can be used as a
precondition in a script that runs where trading runs. It is not whether an entry can open;
``--json`` carries that as ``readiness`` (``live_entry_possible``, ``blocking``, ``unknown``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .. import timeutil
from ..cli_common import force_utf8_io
from ..control import ACTIVE, HALT_SOFT, KILLED, PAUSED, ControlStore
from ..errors import MvpRuntimeError
from ..paths import repo_root as _repo_root
from . import account_store, breaker_watch, pool, pre_order_gate
from .account import (
    ACCOUNT_API_KEY_ENV, ACCOUNT_API_SECRET_ENV, ACCOUNT_FEED_ENV, BINANCE_ACCOUNT,
    read_account,
)
from .cycle import LIVE_ALLOWANCE_SPENT, OPTIONAL_DATA_DEGRADED_CODES
from .dashboard import _read_cycle_records
from .live_position import compute_open_notional_usdt, list_open_live_positions
from .live_route import (
    ACCOUNT_UNREADABLE as ROUTING_ACCOUNT_UNREADABLE,
    ROUTE_BLOCKED,
    ROUTE_DISABLED,
    ROUTE_INCIDENT,
    ROUTING_PRECONDITION,
    verify_live_arm,
)
from .live_order import (
    API_CALL_CLASSES,
    CONFIRMATION_ENV,
    ENTRY_MARKS_FILENAME,
    MANUAL_KILL_SWITCH_ENV,
    api_breaker_status,
    bracket_breaker_status,
    claim_expires_at,
    count_today,
    evaluate_live_order_guard,
    read_live_entry_marks,
    resolve_live_order_limits,
    symbol_in_flight,
)
from .execution_stage import (
    PURPOSE_AUTONOMOUS,
    STAGE_ENFORCED,
    required_stage,
    resolve_execution_stage,
)
from .live_pnl import (
    LIVE_TRADING_ENV,
    REAL_LIVE_TRADING,
    LIVE_PNL_NO_SOURCE,
    LIVE_PNL_VENUE_FIGURE_MISSING,
    live_risk_snapshot,
    venue_daily_realized_net,
)
from .market_data import BINANCE_FUTURES, MARKET_DATA_ENV, TIMEFRAMES
from .risk_limits import limits_status as risk_limits_status
from .risk_limits import rebase_names as risk_rebase_names
from .venue_contract import (
    ENTRY_CONTRACT_STALE,
    ENTRY_CONTRACT_VERSION,
    FUTURE_SKEW_SECONDS,
    covers,
    read_refresh_mark,
    status_line,
    verification_status,
)

# LP4's order adapter exists (merged 2026-07-25): `live_execution.BinanceFuturesOrderAdapter`
# can sign, send, and reconcile an order. This is a constant rather than a computed check
# because it is a fact about the codebase, not about this machine — whether an order may
# actually be sent is the live-trading opt-in, the confirmation phrase, the registered budget
# and the kill switches, each of which the board checks on its own row.
# Kept in lockstep with the policy's `financial_transaction_execution_implemented`.
ORDER_PATH_IMPLEMENTED = True

# Whether any AUTONOMOUS entry point can reach the order path. TRUE since LP5.3 step 3
# (cycle routing): `crypto/cycle.py` runs a live leg through `crypto/live_route.py`, so a
# scheduled crypto fire on a machine whose environment sets `MVP_LIVE_TRADING=real` can open and
# close real positions. Pinned to the real import graph by
# `test_the_cycle_reaches_the_live_order_path_through_exactly_one_module`, so this constant and
# the code cannot disagree.
#
# It is still deliberately NOT part of `ready`. Wired is not permitted: every door below it —
# the opt-in, the confirmation phrase, the registered budget, both kill switches, the loss
# breaker — is unchanged, and each has its own row above.
AUTONOMOUS_ROUTING_WIRED = True

# The symbol the guard dry-run probes when no budget names one. Only reached on a machine with
# no usable budget — where the honest answer is a block either way — so it decides nothing; a
# registered budget supplies the probe symbol from its own allowlist.
DEFAULT_PROBE_SYMBOL = "BTCUSDT"

# How old the trading process's own record may be before this board stops treating it as a
# statement about now. Cycles land every few minutes, so two hours is far outside normal and
# means the scheduler stopped rather than that the gate changed.
RECORDED_GATE_STALE_AFTER_SECONDS = 2 * 60 * 60

# How many recent cycle records the board reads. The newest decides the recorded gate; every record
# stamped with the newest instant is the trading process's last fire (PR5a). A fire writes one record
# per routed context — thirteen on 2026-09-19, at most one per symbol and timeframe the pool routes —
# so this holds several fires. A fire larger than this would be judged on the part of it read.
RECORDED_FIRE_LOOKBACK = 128

# How long the last fire speaks for the next one (review of #906): three of the pipeline schedule's
# own intervals — two missed fires are a hiccup, three a scheduler that stopped or a schedule turned
# off. Measured from the schedule store; the default is its interval on this host, for a store that
# cannot be read.
RECENT_CYCLE_INTERVALS = 3
DEFAULT_CYCLE_INTERVAL_SECONDS = 15 * 60


def _claim_worth_showing(claim: Mapping[str, Any], now: str) -> bool:
    """A claim still holding its symbol, or one that expired less than a day ago."""
    try:
        shown_until = timeutil.plus_minutes(claim_expires_at(claim), 24 * 60)
        return timeutil.parse_iso(now) < timeutil.parse_iso(shown_until)
    except (TypeError, ValueError, OverflowError):
        return True


def _cooldown_holds_now(context: str, until: str, now: str) -> bool:
    """Whether a live stop-loss cooldown still holds the bar its context evaluates at ``now``.

    A context evaluates its last CLOSED bar, which opened one bar before the bar containing
    ``now``; the cooldown holds every bar opening before ``until``. A context whose bar length is
    unknown is reported as holding rather than hidden."""
    minutes = TIMEFRAMES.get(context.rsplit("__", 1)[-1])
    if not minutes:
        return True
    try:
        minute = int(timeutil.parse_iso(now).timestamp()) // 60
    except (TypeError, ValueError):
        return True
    last_closed_open = timeutil.plus_minutes(
        "1970-01-01T00:00:00Z", minute - minute % minutes - minutes)
    return last_closed_open < until


def _check(check_id: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"check": check_id, "ok": bool(ok), "detail": detail}


def _testnet_evidence(root: Path | None) -> dict[str, Any]:
    """What the evidence registry says, fail-soft for the board: an unreadable registry is named,
    never rendered as "none recorded"."""
    from .testnet_evidence import cycle_findings, read_cycles

    try:
        rows = read_cycles(root)
    except MvpRuntimeError as exc:
        return {"error": exc.reason_code, "recorded": None, "complete": []}
    return {
        "error": None,
        "recorded": len(rows),
        "complete": [str(r.get("cycle_id")) for r in rows if not cycle_findings(r)],
    }


def _venue_contract(root: Path | None, *, now: str) -> dict[str, Any]:
    """What the venue contract sentinel last decided (PR4a), fail-soft for the board: a record that
    cannot prove itself is named, never rendered as "none recorded"."""
    try:
        mark = read_refresh_mark(root)
        last_attempt = ({"at": mark.get("attempted_at"), "line": status_line(mark)}
                        if isinstance(mark, Mapping) else None)
    except Exception as exc:  # noqa: BLE001 — the board never raises; the attempt is a footnote
        last_attempt = {"at": None, "line": f"attempt mark unreadable ({type(exc).__name__})"}
    try:
        status = verification_status(root, now=now)
    except MvpRuntimeError as exc:
        return {"error": exc.reason_code, "last_attempt": last_attempt}
    except Exception as exc:  # noqa: BLE001 — the board never raises; the doors refuse on it too
        return {"error": type(exc).__name__, "last_attempt": last_attempt}
    return {"error": None, **status, "last_attempt": last_attempt}


def _age_seconds(now: str, stamp: str) -> float | None:
    try:
        return (timeutil.parse_iso(now) - timeutil.parse_iso(stamp)).total_seconds()
    except (MvpRuntimeError, ValueError, TypeError):
        return None


def _undatable(age: float | None) -> bool:
    """An age this board cannot place: a stamp that does not parse, or one dated past this clock by
    more than clocks disagree — the venue contract's rule (review of #906)."""
    return age is None or age < -FUTURE_SKEW_SECONDS


def _recent_cycles(root: Path) -> tuple[list[dict[str, Any]], str | None, str | None]:
    """The newest cycle records, the reader's warning, and the error that stopped the read — one pass
    over the ledger for every recorded fact on the board. Never raises. A record that is not an object
    stays in its place as an empty one, so a damaged newest row reads as a newest row that says nothing
    rather than handing "the last fire" to an older one (review of #906)."""
    try:
        records, warning = _read_cycle_records(root, RECORDED_FIRE_LOOKBACK)
    except Exception as exc:  # noqa: BLE001 — an observability row must not break the board
        return [], None, type(exc).__name__
    return [r if isinstance(r, Mapping) else {} for r in records], warning, None


def _cycle_window(root: Path) -> dict[str, Any]:
    """How long the last fire speaks for the next one: three of the shortest enabled pipeline schedule's
    interval (`RECENT_CYCLE_INTERVALS`). ``scheduled`` is False when no pipeline schedule is enabled —
    no fire is coming — and None when the store cannot be read, which falls back to the default
    interval and names the error. Never raises."""
    from ..scheduler import KIND_CRYPTO, ScheduleStore  # the core's store; read, never written

    try:
        schedules = ScheduleStore(root).list()
    except Exception as exc:  # noqa: BLE001 — a window it cannot read falls back, never breaks the board
        return {"scheduled": None, "interval_seconds": DEFAULT_CYCLE_INTERVAL_SECONDS,
                "window_seconds": RECENT_CYCLE_INTERVALS * DEFAULT_CYCLE_INTERVAL_SECONDS,
                "error": str(getattr(exc, "reason_code", None) or type(exc).__name__)}
    intervals = [s.interval_seconds for s in schedules
                 if s.kind == KIND_CRYPTO and s.enabled and isinstance(s.interval_seconds, int)
                 and s.interval_seconds > 0]
    interval = min(intervals) if intervals else DEFAULT_CYCLE_INTERVAL_SECONDS
    return {"scheduled": bool(intervals), "interval_seconds": interval,
            "window_seconds": RECENT_CYCLE_INTERVALS * interval, "error": None}


def _position_book(root: Path) -> dict[str, Any]:
    """The live book as the leg reads it before anything else (`list_open_live_positions`): a record it
    cannot read or attribute refuses the whole leg, every context (review of #906). Never raises."""
    try:
        positions = list_open_live_positions(root)
    except MvpRuntimeError as exc:
        return {"readable": False, "open": None, "error": exc.reason_code}
    except Exception as exc:  # noqa: BLE001 — the board never raises; the leg refuses on it too
        return {"readable": False, "open": None, "error": type(exc).__name__}
    return {"readable": True, "open": len(positions), "error": None}


def _arm_refusal(root: Path, strategy_id: str, entries: Mapping[str, Any],
                 approvals: Mapping[str, Any], held: set[str]) -> str | None:
    """Why the gate would refuse an entry by this armed strategy, or None.

    In the gate's order: an arm `pool.live_arm_unsound` names, whatever approval it carries (it
    predates the artifact, trades another rule, or was put back in the tier by hand) — said in the
    words this row has always used; an arm naming no approval, or one whose approval the gate's own
    check cannot verify (`live_route.verify_live_arm`, review of #906); and one the live allowance held
    back from the leg at the last fire (`LIVE_ALLOWANCE_SPENT`), which the next fire holds back again
    until the disarm it asks for lands."""
    entry = entries.get(strategy_id)
    unsound = pool.live_arm_unsound(entry) if isinstance(entry, Mapping) else None
    if unsound is not None:
        return unsound
    approval_id = approvals.get(strategy_id)
    if not isinstance(approval_id, str):
        return "no approval"
    try:
        arm = verify_live_arm(root=root, strategy_id=strategy_id, plan=entry, approval_id=approval_id,
                              armed=entries)
    except Exception as exc:  # noqa: BLE001 — an arm the board cannot verify is one the gate refuses
        return f"approval not verified ({type(exc).__name__})"
    if not arm.get("approval_verified"):
        return str(arm.get("approval_problem") or "approval not verified")
    if strategy_id in held:
        return LIVE_ALLOWANCE_SPENT
    return None


def _recorded_gate(recent: tuple[list[dict[str, Any]], str | None, str | None], *,
                   now: str) -> dict[str, Any]:
    """What the process that CAN trade last recorded about its own live gate.

    Every row this board computes from ``os.environ`` answers "can *this process* place a live
    order". That is the right question where the scheduler runs and a false one everywhere else:
    the operator console and the assistant read door run in containers that deliberately carry
    no ``MVP_LIVE_*``, so the env rows read FAIL there and the board states that live trading is
    off while the scheduler is placing real orders. Forwarding the env would "fix" it by putting
    the money path in the containers built to be without it — the wrong direction.

    So the board also reports the trading process's own answer. Every crypto cycle stamps
    ``live_route_status``, and ``DISABLED`` is written only when the gate refused to open, which
    makes the newest cycle record an authoritative "was the gate open". It is read from the
    ledger those containers already mount: no new writer, no new env, no second authority.

    Never raises and never guesses — an absent or unreadable ledger reports ``known: False``
    rather than an assumption in either direction.
    """
    unknown: dict[str, Any] = {
        "known": False, "open": None, "status": None,
        "recorded_at": None, "age_seconds": None, "stale": False, "error": None,
    }
    records, warning, error = recent
    if error is not None:
        return {**unknown, "error": error}
    if not records:
        return {**unknown, "error": warning}
    record = records[-1]
    status = record.get("live_route_status")
    recorded_at = record.get("created_at")
    if not isinstance(status, str) or not isinstance(recorded_at, str):
        return {**unknown, "error": "cycle record carries no live route status"}
    age = _age_seconds(now, recorded_at)
    return {
        "known": True,
        # DISABLED is the one status meaning the gate refused to open. Every other status was
        # reached through an opened gate, so the reading is positive rather than a guess.
        "open": status != ROUTE_DISABLED,
        "status": status,
        "recorded_at": recorded_at,
        "age_seconds": age,
        # An unparsable stamp counts as stale: an age this board cannot compute is not one it
        # may present as current. Nor is a stamp dated ahead of this clock (review of #906).
        "stale": _undatable(age) or age > RECORDED_GATE_STALE_AFTER_SECONDS,
        "error": warning,
    }


def _data_refusal(record: Mapping[str, Any]) -> str | None:
    """Why the live entry door refuses this context on its data — the first that applies — or None:
    a synthetic feed (the guard blocks trading on it), a degraded collection, candles the data health
    check refused (stale, gapped: review of #906), or optional data that is degraded, past its bound or
    missing from the bar (PR2d-2, decision 28).

    The health check is read off ``paper_verdict_status``, the paper leg's verdict, which is the
    health verdict alone (`guards.paper_trade_verdict`); the live verdict merges it, so the entry door
    refuses on it too. Not ``verdict_status``, which also carries the loss breakers `risk_ready`
    names. A record written before the paper/live split carries neither, and counts nothing here."""
    collection = record.get("collection")
    if isinstance(collection, Mapping) and collection.get("is_synthetic"):
        return "SYNTHETIC"
    if record.get("degraded"):
        return "DEGRADED"
    paper = record.get("paper_verdict_status")
    if isinstance(paper, str) and paper != "ALLOW":
        return "DATA_HEALTH"
    codes = record.get("reason_codes")
    if record.get("optional_data_stale") or record.get("optional_data_missing") or (
            isinstance(codes, list)
            and any(isinstance(code, str) and code in OPTIONAL_DATA_DEGRADED_CODES for code in codes)):
        return "OPTIONAL_DATA"
    return None


def _live_codes(record: Mapping[str, Any]) -> list[str]:
    codes = record.get("live_reason_codes")
    return [c for c in codes if isinstance(c, str)] if isinstance(codes, list) else []


def _blocked_code(record: Mapping[str, Any]) -> str | None:
    """The refusal a BLOCKED leg recorded after its precondition marker (`live_route.run_live_leg`),
    or None when it names none."""
    codes = _live_codes(record)
    if ROUTING_PRECONDITION in codes:
        after = codes[codes.index(ROUTING_PRECONDITION) + 1:]
        if after:
            return after[0]
    return None


def _recorded_fire(recent: tuple[list[dict[str, Any]], str | None, str | None], *,
                   now: str, window_seconds: int) -> dict[str, Any]:
    """The trading process's last fire as its cycle records tell it (PR5a): every record stamped with
    the newest instant, one per context the fire routed. Whether it is recent enough to speak for the
    next fire; how many contexts the entry door would refuse on their data, and why; how many legs
    could not read the account or were refused whole before any venue action; which contexts halted
    on money the runtime cannot account for; the gate switches the live leg read; and the strategies
    the live allowance held back.

    For the readiness state, which needs more of the trading process's last word than the recorded
    gate's one status. Never raises and never guesses: an absent or unreadable ledger reports
    ``known: False``, and a newest record this board cannot date reports ``recent: None``.

    The fire is every record at the newest instant, so where a single-context pipeline schedule is
    registered beside the pool fan-out, its fire would be read as the fire too: this reading does not
    tell the two apart.
    """
    records, warning, error = recent
    newest = records[-1].get("created_at") if records else None
    if error is not None or not isinstance(newest, str):
        return {
            "known": False, "created_at": None, "age_seconds": None, "recent": None,
            "window_seconds": window_seconds, "contexts": 0, "synthetic": 0, "degraded": 0,
            "data_health": 0, "optional_data": 0, "account_unreadable": 0, "blocked": 0,
            "blocked_code": None, "incident": [], "gate": None, "allowance_held": [],
            "error": error or warning or ("cycle record carries no instant" if records else None),
        }
    fire = [r for r in records if r.get("created_at") == newest]
    age = _age_seconds(now, newest)
    refusals = [_data_refusal(r) for r in fire]
    # One process wrote the fire, so its contexts read the same switches; folded the strict way anyway.
    switches = [r["live_gate"] for r in fire if isinstance(r.get("live_gate"), Mapping)]
    blocked = [r for r in fire if r.get("live_route_status") == ROUTE_BLOCKED]
    blocked_codes = [code for code in (_blocked_code(r) for r in blocked) if code is not None]
    held: set[str] = set()
    for record in fire:
        allowance = record.get("live_allowance")
        spent = allowance.get("blocked_from_live_this_cycle") if isinstance(allowance, Mapping) else None
        if isinstance(spent, list):
            held.update(sid for sid in spent if isinstance(sid, str))
    return {
        "known": True,
        "created_at": newest,
        "age_seconds": age,
        # Within the window the pipeline schedule sets (`_cycle_window`); None when undatable.
        "recent": None if _undatable(age) else age <= window_seconds,
        "window_seconds": window_seconds,
        "contexts": len(fire),
        # Each context counted once, under the first reason its data is refused.
        "synthetic": refusals.count("SYNTHETIC"),
        "degraded": refusals.count("DEGRADED"),
        "data_health": refusals.count("DATA_HEALTH"),
        "optional_data": refusals.count("OPTIONAL_DATA"),
        # The leg could not read the account, so that context's entry was refused. The snapshot store
        # keeps its previous snapshot on a failed read, so this — not the snapshot's age — is what says
        # the trading process cannot see its account (review of #906).
        "account_unreadable": sum(1 for r in fire if ROUTING_ACCOUNT_UNREADABLE in _live_codes(r)),
        # BLOCKED: gated open, and a precondition refused the whole leg before any venue action — no
        # settlement, no reconciliation, no entry (review of #906). With the commonest refusal named.
        "blocked": len(blocked),
        "blocked_code": (max(sorted(set(blocked_codes)), key=blocked_codes.count)
                         if blocked_codes else None),
        # INCIDENT, or a halt: the pass stopped its fan-out on a state it cannot account for, and the
        # next pass halts again until the book and the venue agree.
        "incident": sorted(f"{r.get('symbol')}__{r.get('timeframe')}" for r in fire
                           if r.get("live_route_status") == ROUTE_INCIDENT or r.get("live_halt")),
        "gate": ({"confirmation_present": all(bool(g.get("confirmation_present")) for g in switches),
                  "manual_kill_switch": any(bool(g.get("manual_kill_switch")) for g in switches)}
                 if switches else None),
        # The armed strategies the live allowance held back from the leg this fire (cycle step 4c).
        "allowance_held": sorted(held),
        "error": warning,
    }


def _recorded_account(root: Path, *, now: str, limit_usdt: float) -> dict[str, Any]:
    """The account as the trading process last read it (PR5a), for a process that does not read it
    itself: the snapshot the scheduler stores every fifteen minutes, dated, with today's loss judged
    from the figure it carries by the entry door's own rule — the stricter of the calendar day and the
    rolling 24 hours, a missing figure a trip (``LIVE_PNL_VENUE_FIGURE_MISSING``, named in
    ``loss_error``; review of #906). Too old to speak for now, or dated ahead of this clock, the loss is
    not judged at all. Never raises: a snapshot that cannot be read reads ``recorded: False``.
    """
    unknown: dict[str, Any] = {
        "source": SOURCE_RECORDED, "configured": False, "recorded": False, "as_of": None,
        "age_seconds": None, "stale": True, "realized_net": None, "daily_loss_breached": None,
        "loss_error": None,
    }
    try:
        body = account_store.read_snapshot(root)
    except Exception:  # noqa: BLE001 — the store reads damage as absence; this is the same answer
        body = None
    if not isinstance(body, Mapping):
        return unknown
    as_of = body.get("as_of")
    age = _age_seconds(now, as_of) if isinstance(as_of, str) else None
    stale = _undatable(age) or age > account_store.STALE_AFTER_SECONDS
    net = venue_daily_realized_net(body.get("realized_windows"))
    breached: bool | None = None
    loss_error: str | None = None
    if not stale:
        try:
            risk = live_risk_snapshot(
                limit_usdt=limit_usdt, root=root, now=now,
                venue_realized_pnl_usdt=net, venue_required=True,
            )
            breached = bool(risk["daily_loss_limit_breached"])
            loss_error = risk.get("history_error")
        except Exception as exc:  # noqa: BLE001 — unjudged, never a comfortable False
            breached, loss_error = None, str(getattr(exc, "reason_code", None) or type(exc).__name__)
    return {**unknown, "recorded": True, "as_of": as_of, "age_seconds": age, "stale": stale,
            "realized_net": net, "daily_loss_breached": breached, "loss_error": loss_error}


def _c4_verdict(root: Path, *, now: str) -> dict[str, Any]:
    """The C4 loss breakers' verdict on a live entry now, in the composition the live leg judges
    (`breaker_watch.live_risk_verdict`). Never raises: a verdict that cannot be computed is
    ``allow: None`` with its reason."""
    try:
        verdict = breaker_watch.live_risk_verdict(root, now=now)
    except MvpRuntimeError as exc:
        return {"allow": None, "problems": [], "error": exc.reason_code}
    except Exception as exc:  # noqa: BLE001 — the board never raises
        return {"allow": None, "problems": [], "error": type(exc).__name__}
    return {"allow": bool(verdict.get("allow_new_position")),
            "problems": [str(p) for p in verdict.get("problems") or ()], "error": None}


def build_readiness(root: Path | None = None, *, now: str | None = None) -> dict[str, Any]:
    """Ask every gate and collect the answers. Never raises: an unreadable input is a
    failed check with its reason, not a crashed board."""
    root = root if root is not None else _repo_root()
    now = now or timeutil.utc_now_iso()
    # The caps come from the registered budget now, not the environment; confirmation + manual
    # kill still come from env (resolve_live_order_limits folds both in). budget["valid"] is the
    # authoritative "a budget backs these caps" fact the guard needs.
    limits, budget = resolve_live_order_limits(root, now=now)
    checks: list[dict[str, Any]] = []

    # 1. The switch itself — the environment, and only the environment (Thomas, 2026-07-28).
    #    The per-machine grant this row used to also require is gone; the row is renamed with it,
    #    because a board that still said "grant" while checking an env var would be the most
    #    misleading line on the page. The name change is intentional and load-bearing: the whole
    #    point of this board is that its rows mean what they say.
    opted_in = os.environ.get(LIVE_TRADING_ENV, "").strip().lower() == REAL_LIVE_TRADING
    checks.append(_check(
        "live_trading_opt_in",
        opted_in,
        f"{LIVE_TRADING_ENV}={REAL_LIVE_TRADING}" if opted_in
        else f"{LIVE_TRADING_ENV} is not {REAL_LIVE_TRADING!r} (live trading off)",
    ))

    # 2. The confirmation phrase (presence and exact match, never echoed).
    checks.append(_check(
        "confirmation_phrase",
        limits.confirmation_present(),
        "present and correct" if limits.confirmation_present()
        else f"{CONFIRMATION_ENV} missing or does not match the live-trading phrase",
    ))

    # 3. The registered trading budget (step 6b). The caps now come FROM this record, and a
    #    valid budget is schema-guaranteed to carry positive caps within the hard ceiling —
    #    so this one row subsumes the old env-caps check. A missing / tampered / invalid budget,
    #    or a legacy one outside the window it was registered with, fails here, and the caps
    #    fall back to the blocking defaults so the guard refuses too.
    #    The detail says when the budget ends. One registered since 2026-09-15 carries no window
    #    (PR1r) and reads "no expiry"; one registered before still names the end of its window,
    #    because it is still held to it. ASCII only: this text is rendered to a terminal board.
    if budget.get("valid"):
        budget_detail = (
            f"registered {budget['budget_id']} "
            f"(order<={limits.max_order_notional_usdt}, {limits.max_daily_order_count}/day, "
            f"open<={limits.max_open_notional_usdt}, loss<={limits.daily_loss_limit_usdt}), "
            + (f"valid until {budget.get('valid_until')}" if budget.get("valid_until") else "no expiry")
        )
    elif budget.get("registered"):
        budget_detail = f"registered but invalid: {budget['error']}"
        if budget.get("valid_until"):
            budget_detail += (
                f" (registered for {budget.get('valid_from')} .. {budget.get('valid_until')}; "
                "a budget re-registered today has no expiry)"
            )
    else:
        budget_detail = ("no live-trading budget registered "
                         "(register with scripts/register_live_trading_budget.py)")
    checks.append(_check("registered_budget", bool(budget.get("valid")), budget_detail))

    # 3b. The C4 breaker limits. Unlike every other row this one is GREEN when nothing is
    #     registered: the guards.py defaults are the supported steady state, not a gap, so a
    #     fresh machine must not read as unready over a record it is not expected to have. It
    #     goes red only for a record that exists and cannot be used — tampered, out of bounds,
    #     or a legacy record outside the window it carries — which is exactly the state in which
    #     the C4 guard refuses every live entry and the probe (paper never reads these limits).
    #     Without this row that refusal would be invisible here and the operator would find it
    #     in a cycle record instead.
    #     The detail says when the record ends. One registered since 2026-09-15 carries no window
    #     (PR1r) and reads "no expiry"; one registered before still names the end of its window,
    #     because it is still held to it. A drawdown baseline rebase is named with its count: it
    #     stands exactly as long as the numbers do.
    risk_status = risk_limits_status(root, now=now)
    effective = risk_status.get("effective") or {}
    # ASCII only, like every other row: this text is rendered to a terminal board.
    risk_numbers = (
        f"daily {effective.get('daily_max_loss_r')}R, weekly {effective.get('weekly_max_loss_r')}R, "
        f"consecutive {effective.get('max_consecutive_losses')}, "
        f"drawdown {effective.get('max_drawdown_pct')}%"
    )
    if not risk_status["registered"]:
        risk_detail = f"none registered - guard uses the defaults ({risk_numbers})"
    elif risk_status["valid"]:
        risk_detail = (
            f"registered {risk_status['limits_id']} ({risk_numbers}), "
            f"registered_at {risk_status.get('registered_at')}, "
            + (f"valid until {risk_status.get('valid_until')}" if risk_status.get("valid_until")
               else "no expiry")
        )
        rebase_count = risk_status.get("drawdown_rebase_excluded_count")
        if rebase_count:
            risk_detail += f", drawdown baseline rebase excludes {risk_rebase_names(risk_status)}"
    else:
        risk_detail = (
            f"registered but unusable: {risk_status['error']} - the C4 guard REFUSES new "
            "positions until it is re-registered or deleted "
            "(scripts/register_crypto_risk_limits.py --show)"
        )
        if risk_status.get("valid_until"):
            risk_detail += (
                f" (registered for {risk_status.get('valid_from')} .. {risk_status.get('valid_until')}; "
                "limits re-registered today have no expiry)"
            )
    checks.append(_check("risk_limits_record", bool(risk_status["valid"]), risk_detail))

    # 4. The manual halt.
    manual_halt = limits.manual_kill_switch
    checks.append(_check(
        "manual_kill_switch",
        not manual_halt,
        "clear" if not manual_halt else f"{MANUAL_KILL_SWITCH_ENV} is engaged",
    ))

    # 5. The runtime kill switch (kill_blocks: external_execution).
    try:
        state = ControlStore(root).load()
        runtime_active, runtime_detail = state.execution_allowed, f"runtime is {state.mode}"
        trading_armed = state.trading_armed
        # The same state as facts, for the readiness state (PR5a).
        control: dict[str, Any] = {"mode": state.mode, "trading_armed": bool(state.trading_armed),
                                   "fail_closed": bool(state.fail_closed), "error": None,
                                   "halt_level": state.halt_level}
        halt = f"{state.halt_level} halt: " if state.halt_level else ""
        armed_detail = (
            "armed" if trading_armed else
            f"DISARMED ({halt}{state.reason}) - new live entries are refused"
            + ("; the runtime is ACTIVE, so open positions are still managed and closed and paper "
               "is unaffected" if state.execution_allowed else
               f"; the runtime is {state.mode}, so position management is stopped too until /resume")
        )
    except MvpRuntimeError as exc:
        runtime_active, runtime_detail = False, f"control state unreadable ({exc.reason_code})"
        trading_armed, armed_detail = False, f"control state unreadable ({exc.reason_code})"
        control = {"mode": None, "trading_armed": False, "fail_closed": False, "error": exc.reason_code,
                   "halt_level": None}
    checks.append(_check("runtime_active", runtime_active, runtime_detail))
    # 5b. The arm, as its own row. Folding it into `runtime_active` would make one FAIL mean two
    # different situations with different fixes — a kill needs a resume, a disarm needs a
    # re-arm — and this board exists so an operator does not have to guess which.
    checks.append(_check("trading_armed", trading_armed, armed_detail))

    # 5c. The armed strategies — 5b's question again, one level down. `trading_armed` says
    #     whether this RUNTIME's entries are armed; this row says how many STRATEGIES are, and
    #     the two are independent doors on the same path. Since #610 Part 1 (#648) an entry may
    #     open a real position only if its pool entry is occupying AND carries
    #     `live_tier: LIVE`, and absence reads as OBSERVATION — so the migration deliberately
    #     disarmed every entry promoted before the field existed. On 2026-08-10 that left this
    #     board READY over an EMPTY armed set: every row was green, no strategy could open
    #     anything, and the operator found out from the absence of positions. A fact that
    #     decides whether READY ever produces an entry belongs on the board.
    #
    #     Zero armed is informational; an unreadable pool FAILS. That is the split the entry
    #     door itself makes with two reason codes — "no strategy is armed" is the expected
    #     steady state, "the pool could not be read" is a fault whose fix is elsewhere — and
    #     the board keeps them apart for the door's own reason: an operator chasing one should
    #     not be handed the other. `ready` keeps meaning "may this RUNTIME trade", because
    #     arming is a per-strategy operator decision at the promotion door, and a board that
    #     failed on zero would alarm on the deliberate safe state. Fail-closed both ways: an
    #     unreadable pool reports UNREADABLE, never a comfortable "0 armed".
    #
    #     The trading process's last fire is read first: the live allowance it applied is one of the
    #     reasons an armed strategy cannot trade.
    recent = _recent_cycles(root)
    cycle_window = _cycle_window(root)
    fire = _recorded_fire(recent, now=now, window_seconds=int(cycle_window["window_seconds"]))
    try:
        active_pool = pool.load_active_pool(root)
    except MvpRuntimeError as exc:
        pool_error = getattr(exc, "reason_code", "UNKNOWN")
        armed_strategies: dict[str, Any] = {
            "known": False, "armed": None, "occupying": None, "error": pool_error,
        }
        armed_strategies_ok = False
        armed_strategies_detail = (
            f"UNREADABLE ({pool_error}) - the entry door refuses on this too, so no strategy "
            "can open a live position until the pool is repaired"
        )
        tradable_armed: int | None = None
    else:
        armed_ids = pool.live_routable_strategy_ids(active_pool)
        occupying_ids = pool.routable_strategy_ids(active_pool)
        armed_strategies = {
            "known": True, "armed": len(armed_ids), "occupying": len(occupying_ids),
            "error": None,
        }
        armed_strategies_ok = True
        # The armed entries the gate would accept (PR5a): the count an entry actually needs.
        tradable_armed = len(armed_ids)
        if armed_ids:
            armed_strategies_detail = (
                f"{len(armed_ids)} armed of {len(occupying_ids)} occupying (live_tier=LIVE)"
            )
            # The tier says LIVE, but the gate refuses an arm that is unsound, stands on no approval
            # it can verify, or that the live allowance holds back (`_arm_refusal`). Said here so
            # "armed" is not read as "can trade".
            entries = pool.live_arm_entries(active_pool)
            approvals = pool.live_arm_approvals(active_pool)
            held = set(fire["allowance_held"])
            cannot_trade = [
                (sid, _arm_refusal(root, sid, entries, approvals, held)) for sid in sorted(entries)
            ]
            cannot_trade = [(sid, why) for sid, why in cannot_trade if why is not None]
            refused = {sid for sid, _why in cannot_trade}
            tradable_armed = sum(1 for sid in armed_ids if sid not in refused)
            if cannot_trade:
                armed_strategies_detail += (
                    f"; {len(cannot_trade)} of them cannot trade: "
                    + ", ".join(f"{sid} ({why})" for sid, why in cannot_trade)
                )
        else:
            armed_strategies_detail = (
                f"0 armed of {len(occupying_ids)} occupying - NO strategy may open a real "
                "position; arming is the operator promotion door "
                "(scripts/promote_strategy_candidates.py --live-tier LIVE)"
            )
    checks.append(_check("live_armed_strategies", armed_strategies_ok, armed_strategies_detail))

    # Whether an account feed is configured at all. Computed here rather than at row 8 because
    # row 6's loss breaker needs the same answer first: it is what decides whether this board
    # reads the venue or stays offline.
    account_configured = (
        os.environ.get(ACCOUNT_FEED_ENV, "").strip().lower() == BINANCE_ACCOUNT
        and bool(os.environ.get(ACCOUNT_API_KEY_ENV, "").strip())
        and bool(os.environ.get(ACCOUNT_API_SECRET_ENV, "").strip())
    )

    # 6. Today's realized loss.
    # The snapshot already folds in the unconfigured-limit rule (no limit reads as breached)
    # and fails closed on an unverifiable history, so this one value covers every case.
    # The breaker needs a figure the local ledger structurally cannot supply (see below), so
    # the board reads the account for it — but ONLY when the operator has already configured
    # one. That keeps the surprise out: an unconfigured machine still opens no socket and the
    # row still fails, exactly as before. A configured read that fails degrades the same way —
    # to no figure, and therefore to a FAILING row. A loss breaker is the one place where
    # "could not measure" must never soften into "nothing to report".
    venue_realized = None
    account_error = None
    snapshot = None
    if account_configured:
        try:
            snapshot, _ = read_account(root=root)
            venue_realized = (
                venue_daily_realized_net(snapshot.realized_windows) if snapshot else None
            )
            if venue_realized is None:
                account_error = "account read returned no realized figure"
        except MvpRuntimeError as exc:      # degrade, never block — the R3 posture
            account_error = exc.reason_code
    # The account for the readiness state (PR5a): this process's own read where it has a feed,
    # otherwise the trading process's stored snapshot.
    account_fact = (
        {"source": SOURCE_THIS_PROCESS, "configured": True, "readable": snapshot is not None,
         "error": account_error}
        if account_configured else
        _recorded_account(root, now=now, limit_usdt=limits.daily_loss_limit_usdt)
    )
    risk = live_risk_snapshot(
        limit_usdt=limits.daily_loss_limit_usdt, root=root, now=now,
        venue_realized_pnl_usdt=venue_realized,
        # Once the board has read the account for the breaker it answers the question the entry
        # paths ask, with their rule: a snapshot that carries no figure is a trip. An unconfigured
        # machine keeps the local branch and its NO DATA SOURCE row — it opened no socket.
        venue_required=account_configured and snapshot is not None,
    )
    breached = bool(risk["daily_loss_limit_breached"])
    # A breaker with nothing to measure is not a passing check, however comfortable its number
    # looks. The local outcome ledger is written only by `live_leg.execute_live_exit` — the
    # autonomous leg nothing may import — and the canary door (removed 2026-09-15) was
    # entry-only, so on this board the figure below has no source at all. It read `realized
    # today 0.0 USDT` and PASSED while the venue reported a real realized loss for the same day.
    # Reporting that as ready is the failure `cycle.py` names: a breaker that cannot trip is not
    # a breaker.
    no_source = risk.get("history_error") == LIVE_PNL_NO_SOURCE
    # BREACHED is reported ahead of NO DATA SOURCE, and the order matters: an unconfigured limit
    # already reads as breached ("zero means not configured, never unlimited"), and that is the
    # stronger statement of the two. Letting the newer message win would have downgraded it.
    if risk.get("history_error") == LIVE_PNL_VENUE_FIGURE_MISSING:
        detail = (
            f"TRIPPED - the account read carried no realized P&L figure for today "
            f"({account_error or 'no figure'}), so the {risk['daily_loss_limit_usdt']} USDT limit "
            f"cannot be measured and every entry path refuses ({LIVE_PNL_VENUE_FIGURE_MISSING})"
        )
    elif breached:
        detail = (
            f"BREACHED (realized {risk['daily_realized_pnl_usdt']}, "
            f"limit {risk['daily_loss_limit_usdt']}"
            + (f", history_error={risk['history_error']}" if risk["history_error"] else "") + ")"
        )
    elif no_source:
        detail = (
            f"NO DATA SOURCE - the local outcome ledger has no closed trade for today, so the "
            f"{risk['daily_loss_limit_usdt']} USDT limit currently bounds nothing. The venue "
            "knows the figure; a caller that reads the account can pass it in."
        )
    else:
        detail = (
            f"realized today {risk['daily_realized_pnl_usdt']} USDT, "
            f"limit {risk['daily_loss_limit_usdt']} (source={risk.get('pnl_source')})"
        )
    checks.append({
        **_check("daily_loss_breaker", not breached and not no_source, detail),
        # NO DATA SOURCE only because this process reads no account is a fact about this process —
        # the readiness state reads the trading process's snapshot instead — and a process without
        # the live-trading environment says so on the row (`_row`, review of #907). BREACHED is a fact
        # about the limit, and is never scoped.
        "env_scoped": no_source and not breached and not account_configured,
    })

    # 6b. The bracket breaker. Unlike the loss breaker above it always has a source: it counts
    # what this runtime's own leg did, so it reads zero only when zero is true. It is on the
    # board because a tripped breaker means live entries are shut off for a reason no other row
    # would show — the account is fine, the limits are fine, and nothing will trade.
    try:
        bracket = bracket_breaker_status(root)
    except MvpRuntimeError as exc:
        # An unreadable record must not take the board down, and must not read as clear either:
        # the entry path refuses on this same error, so the board reports what it reports.
        bracket = None
        bracket_detail = (
            f"UNREADABLE ({getattr(exc, 'reason_code', 'UNKNOWN')}) - the entry path refuses on "
            "this too, so live entries are blocked until the record is repaired or removed"
        )
    else:
        if bracket["tripped"]:
            bracket_detail = (
                f"TRIPPED - {bracket['consecutive']} consecutive entries filled and could not be "
                f"protected (limit {bracket['limit']}), last {bracket['last_symbol']} at "
                f"{bracket['last_failure_at']}. New entries are refused until an operator clears "
                "it: python -m scripts.clear_bracket_breaker --cleared-by ... --reason ..."
            )
        else:
            bracket_detail = (
                f"{bracket['consecutive']}/{bracket['limit']} consecutive bracket failures"
                + (f" ({bracket['total']} total, last {bracket['last_failure_at']})"
                   if bracket["total"] else "")
            )
    checks.append(
        _check("bracket_breaker", bracket is not None and not bracket["tripped"], bracket_detail)
    )

    # 6b-2. The API error breaker (PR2d-1). Five signed calls of one class in a row that the venue
    # refused or could not answer, and new live entries stay shut until an operator clears it —
    # a state no other row would show, because every other fact can read fine meanwhile.
    try:
        api = api_breaker_status(root)
    except MvpRuntimeError as exc:
        api = None
        api_detail = (
            f"UNREADABLE ({getattr(exc, 'reason_code', 'UNKNOWN')}) - the entry path refuses on "
            "this too, so live entries are blocked until the record is repaired (removing it "
            "clears every count, with no name or reason on the record)"
        )
    else:
        if api["tripped"]:
            # A streak at the limit with no stamp (a limit lowered since) names its longest class.
            name = api["tripped_class"] or max(API_CALL_CLASSES, key=lambda n: api[n]["consecutive"])
            counts = api.get(str(name)) or {}
            told = (f"The operator was told at {api['told_at']}." if api.get("told_at")
                    else "The operator has NOT been told yet; the live cycle keeps trying.")
            api_detail = (
                f"TRIPPED at {api['tripped_at']} - {name} calls failed "
                f"{counts.get('consecutive')} times in a row (limit {api['limit']}), last "
                f"{counts.get('last_call')} {counts.get('last_reason_code')}. {told} New entries "
                "are refused until an operator clears it: python -m scripts.clear_api_breaker "
                "--cleared-by ... --reason ..."
            )
        else:
            api_detail = (
                f"write {api['write']['consecutive']}/{api['limit']}, "
                f"read {api['read']['consecutive']}/{api['limit']} consecutive signed-call failures"
            )
    checks.append(_check("api_breaker", api is not None and not api["tripped"], api_detail))

    # 6c. The live entry marks (PR2a): the last bar each context sent an entry on, and the
    # contexts a live stop-out still holds. On the board for the bracket breaker's reason — an
    # unreadable file refuses every live entry while every other row can read green.
    try:
        marks = read_live_entry_marks(root)
    except MvpRuntimeError as exc:
        marks = None
        marks_detail = (
            f"UNREADABLE ({getattr(exc, 'reason_code', 'UNKNOWN')}) - every live entry is refused "
            f"until {ENTRY_MARKS_FILENAME} is repaired. Do not just delete it: an empty file "
            "re-opens bars an order may already have been sent on. Rewrite 'entered' with each "
            "routed context's current bar (open time), keep 'cooldown', then check this row again"
        )
    else:
        holding = sorted(
            f"{context} until the {until} bar" for context, until in marks["cooldown"].items()
            if _cooldown_holds_now(context, until, now)
        )
        # PR2b-2: the symbols an entry has taken and not yet given back. One whose claim expired
        # was left by an entry that did not finish, or kept on purpose after an outcome the venue
        # did not confirm; either way the book and the venue deserve a look. It holds nothing, so
        # it is shown for the day after it expired rather than for ever.
        in_flight = sorted(
            f"{symbol} ({claim['door']} since {claim['claimed_at']}"
            + ("" if symbol_in_flight(marks, symbol, now=now)
               else "; EXPIRED unreleased - check the book against the venue") + ")"
            for symbol, claim in marks["in_flight"].items()
            if _claim_worth_showing(claim, now)
        )
        marks_detail = (
            f"{len(marks['entered'])} context(s) have sent an entry; "
            + (f"stop-loss cooldown: {', '.join(holding)}" if holding else "no stop-loss cooldown active")
            + (f"; entries in flight: {', '.join(in_flight)}" if in_flight else "")
        )
    checks.append(_check("entry_marks", marks is not None, marks_detail))

    # 6d. The mainnet pre-order snapshot record (PR2b). On the board for the entry marks' reason: the
    # store will not append past a damaged line, so every live entry is refused while every other row
    # can read green. A row that parses but fails its seal, the schema or the gate's requirements
    # fails this row as well — entries go on, but the record no longer proves why past orders left.
    snapshots = pre_order_gate.snapshots_status(root)
    if snapshots["readable"]:
        snapshots_detail = (
            f"{snapshots['count']} recorded, latest {snapshots['last_created_at']}" if snapshots["count"]
            else "none recorded (no order has left under the gate)"
        )
    else:
        snapshots_detail = (
            f"UNREADABLE ({snapshots['error']}) - the record of why past orders were allowed no longer "
            "proves itself. A damaged line refuses every live entry: move "
            f"{pre_order_gate.SNAPSHOT_FILENAME} aside (keep it) and check this row again. A row that "
            "fails its seal was edited: find out by whom before trusting the record"
        )
    checks.append(_check("pre_order_snapshots", snapshots["readable"], snapshots_detail))

    # 7. Retired 2026-09-15 (PR1r): the `canary_evidence` row, with the promotion gate it reported.
    #    The frozen canary history is still readable on its own board:
    #    python -m runtime.mvp_runtime.crypto.live_promotion

    # 8. The account read (LP1) — not required to place an order, but going live without
    #    being able to see the account is flying blind, so it is reported. `account_configured`
    #    is computed above, because row 6's breaker depends on the same feed.
    checks.append(_check(
        "account_visibility",
        account_configured,
        "live account read configured" if account_configured
        else f"{ACCOUNT_FEED_ENV} / {ACCOUNT_API_KEY_ENV} / {ACCOUNT_API_SECRET_ENV} not all set",
    ))

    # 8b. Market data — a live PRECONDITION, not a nicety. Without the opt-in the collector is
    #     the synthesised mock: the slippage probe's reference price refuses it
    #     (`REFERENCE_PRICE_SYNTHETIC`), so no probe can fire, and the cycle's feed is marked
    #     synthetic, which data health will not trade on. (It was first a row for the canary
    #     door's declared-notional check, which went with the door on 2026-09-15.) Checked as
    #     the env opt-in alone since 2026-08-10 — this feed kept its per-machine grant longer
    #     than live trading did (2026-07-28), and the grant went when Thomas retired grant
    #     renewal outright; the env var selects.
    #     Stated here because #201's lesson was that a precondition only a document knows about
    #     is discovered by an operator standing at a terminal with real keys.
    market_data_ready = (
        os.environ.get(MARKET_DATA_ENV, "").strip().lower() == BINANCE_FUTURES
    )
    checks.append(_check(
        "market_data_visibility",
        market_data_ready,
        "live market data configured (the probe and the cycle read the venue's prices)"
        if market_data_ready
        else (f"{MARKET_DATA_ENV} unset "
              f"- the feed is the synthetic mock, so the probe refuses and nothing trades on it"),
    ))

    # 8c. The execution stage (crypto PR1a, enforced by the entry doors since PR1b). A real check:
    #     below LIVE_AUTONOMOUS the guard refuses every new entry, so a board that read PASS here
    #     would be the all-PASS-with-nothing-armed board the audit started from.
    stage = resolve_execution_stage(root, now=now)
    stage_admits = stage.allows(PURPOSE_AUTONOMOUS)
    checks.append(_check(
        "execution_stage",
        stage_admits,
        f"{stage.stage}"
        + ("" if stage.valid else f" (reads READ_ONLY: {stage.reason_code}"
           + (f"; recorded {stage.recorded_stage}" if stage.recorded_stage else "") + ")")
        + ("" if stage_admits else
           f" - a live entry needs {required_stage(PURPOSE_AUTONOMOUS)}; register a transition with "
           "scripts/register_execution_stage.py (Thomas approves it). Closing is never gated by the stage"),
    ))
    # 8d. The venue contract (PR4b, Thomas decision 46). A real check for the stage row's reason: every
    #     mainnet entry, autonomous or probe, is decided on the sentinel's usable PASS for its symbol,
    #     so a board reading PASS while the doors refuse would be the board the audit started from. A
    #     budget symbol the PASS did not cover fails the row too: entries there are refused until a
    #     verification covers it. The symbols go on the record beside it, because the door judges each
    #     entry's own symbol and admits a covered one (`readiness_state`, review of #906).
    contract = _venue_contract(root, now=now)
    budget_symbols = [str(s) for s in (budget.get("symbol_allowlist") or ())]
    uncovered = [s for s in budget_symbols if not covers(contract.get("symbols"), s)]
    contract = {**contract, "budget_symbols": budget_symbols, "uncovered": uncovered}
    checks.append(_check(
        "venue_contract",
        contract.get("error") is None and bool(contract.get("usable")) and not uncovered,
        _venue_contract_detail(contract, uncovered=uncovered),
    ))
    # 9. The order path itself.
    checks.append(_check(
        "order_path_implemented",
        ORDER_PATH_IMPLEMENTED,
        "implemented (LP4 adapter + LP5.3 executing leg)" if ORDER_PATH_IMPLEMENTED
        else "NOT IMPLEMENTED - no module can send an order (LP4 pending governance)",
    ))

    # 10. Whether anything autonomous can reach it. Reported as its own row rather than asserted
    #     in prose, because this is the fact most likely to go stale — and the one that decides
    #     whether READY means "an operator can place an order" or "this machine can trade on its
    #     own". It is deliberately NOT part of `ready`: an unwired runtime is the safe state, so
    #     failing the board on it would invert the meaning of every other row.
    checks.append(_check(
        "autonomous_routing_wired",
        True,  # informational: neither state is a failure
        "WIRED - a scheduled run can place live orders" if AUTONOMOUS_ROUTING_WIRED
        else "not wired - the only door is scripts/run_slippage_probe.py --fire, one probe per run",
    ))

    # A dry-run of the real guard against a representative order at the configured cap.
    # This is the authoritative answer: whatever the rows above say, this is what would
    # actually happen. Nothing is sent — the guard is pure.
    #
    # It is only authoritative if it is given what the real doors are given. Until 2026-07-31
    # this call omitted `allowed_symbols`, whose default is EMPTY — and an empty allowlist
    # blocks every symbol — so the dry-run reported "no symbol allowlist backs this order" on
    # every machine, forever, however the budget was registered. Both real callers
    # (`live_route.plan_live_entry`, `scripts/run_slippage_probe.py`) read the scope off the
    # same budget the caps come from; so does this now. The board under-reported what the
    # machine could do, which is the dangerous direction for a line an operator reads before
    # deciding whether live trading is stopped.
    #
    # The probe symbol comes from that allowlist rather than a hardcoded BTCUSDT: a machine
    # budgeted for ETHUSDT alone is correctly configured, and probing a symbol its own budget
    # excludes would answer a question nobody asked. With no allowlist the probe keeps the old
    # constant — the block it then reports is the true one.
    allowed_symbols = tuple(budget.get("symbol_allowlist") or ())
    probe_symbol = allowed_symbols[0] if allowed_symbols else DEFAULT_PROBE_SYMBOL
    try:
        submitted_today, counter_error = count_today(root), None
    except MvpRuntimeError as exc:
        submitted_today, counter_error = 0, exc.reason_code
    guard = evaluate_live_order_guard(
        {
            "status": "ORDER_INTENT_CREATED",
            "symbol": probe_symbol,
            "quantity": 0.001,
            "order_notional_usdt": limits.max_order_notional_usdt,
            "reduce_only": False,
            "connectivity_test": False,
        },
        allowed_symbols=allowed_symbols,
        gate_open=opted_in,
        runtime_active=runtime_active,
        daily_loss_breached=breached,
        submitted_today=submitted_today,
        # LP5.3: the board now reads the account for the loss breaker, so the same snapshot
        # answers the exposure question — and the honest block-at-cap can finally lift on a
        # machine that can see its own account. `compute_open_notional_usdt` still fails
        # closed: `snapshot is None` (no feed configured, or the read degraded) reports AT
        # the cap, never zero. What changed is that a configured, readable account now
        # reports what it actually holds rather than the worst case, so a dry-run BLOCK here
        # means real exposure, not merely an unconfigured board.
        current_open_notional_usdt=compute_open_notional_usdt(
            snapshot, at_cap=limits.max_open_notional_usdt
        ),
        budget_registered=bool(budget.get("valid")),
        limits=limits,
        # The same stage row 8c reports, so the board's dry-run refuses exactly where the real
        # door would (PR1b).
        execution_stage=stage,
    )

    return {
        "created_at": now,
        # Deliberately still "can THIS process trade": the CLI's exit code is documented as a
        # script precondition, and a script asking that question runs where trading runs. The
        # recorded gate below is reported ALONGSIDE the checks, never folded into them, so a
        # board read off the scheduler cannot flip this verdict.
        "ready": all(c["ok"] for c in checks),
        "checks": checks,
        "recorded_gate": _recorded_gate(recent, now=now),
        # The armed set as data, beside its check row — the render needs the count (the WIRED
        # note below qualifies itself on it), and a JSON consumer should not parse prose.
        "live_armed_strategies": armed_strategies,
        "guard_dry_run": guard,
        # Which order the dry-run judged. A symbol-scoped block is unreadable without it: the
        # operator cannot tell "my budget excludes this symbol" from "this symbol is blocked".
        "guard_dry_run_symbol": probe_symbol,
        "submitted_today": submitted_today,
        "counter_error": counter_error,
        "order_path_implemented": ORDER_PATH_IMPLEMENTED,
        "autonomous_routing_wired": AUTONOMOUS_ROUTING_WIRED,
        # The machine's execution stage as data (PR1a), with whether any door enforces it yet.
        "execution_stage": {**stage.as_dict(), "enforced": STAGE_ENFORCED,
                            "admits_entry": stage_admits},
        # The signed testnet cycles this machine has earned (PR1d-2). Read here rather than at
        # `resolve_execution_stage`, whose contract with the live leg is that it never raises.
        "testnet_evidence": _testnet_evidence(root),
        # What the venue contract sentinel last decided (PR4a); the `venue_contract` check row above
        # reads the same value (PR4b).
        "venue_contract": contract,
        # The pre-order snapshots real orders left under (PR2b), verified; the `pre_order_snapshots`
        # check row above reads the same value.
        "pre_order_snapshots": snapshots,
        # What the readiness state reads beside the rows above (PR5a) — see `readiness_state`.
        "readiness_inputs": {
            "runtime_control": control,
            "tradable_armed": tradable_armed,
            "cycle_window": cycle_window,
            "recorded_fire": fire,
            "position_book": _position_book(root),
            "account": account_fact,
            "c4": _c4_verdict(root, now=now),
            "daily_order_cap": {"submitted_today": submitted_today,
                                "cap": limits.max_daily_order_count, "error": counter_error},
        },
    }


# === the readiness state (model v2, crypto PR5a) ===================================================
#
# `ready` answers "can THIS process trade", and was read as "live trading is on" (the 2026-09-13
# review). `live_entry_possible` then answered "can a real position open" from four facts and missed
# the kill, the disarm and every breaker: a KILLED runtime drops every later fire, so its last record
# stayed OPEN and fresh — and the answer True — for two hours; a disarmed one keeps firing and records
# HELD, which the recorded gate reads as open, so the answer stayed True for as long as it ran (FO-10).
#
# v2 names every fact an entry needs as a component, and each is three-valued:
#
# * ``False`` — an entry door refuses on it. Only where a door would, so False is never a guess —
#   with two judgements that are coarser, and say so:
#   - the last fire's per-context refusals. Data the door refuses, an account the leg could not read,
#     and a leg refused whole before any venue action (BLOCKED) each read False when half or more of
#     the last fire's contexts were refused on it — the pipeline's stall rule — though a minority of
#     contexts may still enter;
#   - `trading_cycle_recent`: no enabled pipeline schedule, or no fire within three of its intervals.
#     No door refuses on that; nothing runs to enter. It errs toward refusal, the safe direction.
# * ``None`` — this process cannot observe it: no cycle on record yet, an unreadable ledger, a last
#   fire too old or undatable to speak for the next one, an account snapshot too old to speak for now.
#   Never to be read as possible.
# * ``True`` — observed, and admitting.
#
# `live_entry_possible` is their three-valued AND: any False is False, else any None is None, and only
# all True is True. `blocking` and `unknown` name each False and each None with its reason.
#
# Whose word a fact is depends on where the board runs. A process carrying the live-trading
# environment — the trading process itself — answers from its own switches and its own account read.
# Every other process (the operator console, the assistant's read door) answers from what the trading
# process recorded: its cycle records and its account snapshot, dated, and unknown once too old. The
# switches it reads are the ones the leg read at the last fire, so an env change on the trading
# process shows here at the next fire, one interval late.
#
# "Possible" means the autonomous leg may open a position at its next cycle. Entries happen only
# inside a cycle, so a trading process that has not fired within three of its schedule's intervals
# opens nothing — False, NO_RECENT_CYCLE, under `trading_cycle_recent` alone — while what that old
# fire saw is unknown, and so is its gate once the record is two hours old. A scheduler that stopped
# reads True for up to three intervals: the heartbeat stall alarm is the instrument for that, not
# this board (review of #906).
#
# Per-order capacity is not readiness, and is not here: open exposure against its cap, the concurrent-
# position caps, a symbol already in flight, a stop-loss cooldown. The guard decides those for the
# order at hand; `guard_dry_run_status` is this process's judgement of a representative one.

READINESS_MODEL = "readiness_state.v2"

# In the order a reader fixes them: what is installed, what the machine is allowed, what the operator
# switched, what is armed, what the venue backs, what risk admits, whether the pipeline still fires,
# and what its last fire saw.
READINESS_COMPONENTS = (
    "live_capability_installed",
    "execution_stage",
    "live_gate_open",
    "runtime_control",
    "armed_strategy_count",
    "venue_ready",
    "risk_ready",
    "account_ready",
    "trading_cycle_recent",
    "market_data_ready",
    "reconciliation_ready",
)

SOURCE_THIS_PROCESS = "this_process"
SOURCE_RECORDED = "recorded"
NOT_REPORTED = "NOT_REPORTED"

# The reasons the stall rule decides (`_majority`). A component refused on one is False for most of the
# last fire's contexts, and a minority may still enter (`minority_may_enter`, review of #907).
# Private: `live_route.ACCOUNT_UNREADABLE` is the leg's own code, with another value.
_MAJORITY = "MAJORITY_"
_LEG_BLOCKED = "LEG_BLOCKED"
_ACCOUNT_UNREADABLE = "ACCOUNT_UNREADABLE"

# The rows the entry door refuses on, read from state every process mounts, and what each is called
# when it refuses. The pre-order snapshot row is judged apart (`_risk_component`): it also fails on a
# record that no longer proves past orders, which the entry path does not refuse.
_RISK_ROWS = (
    ("registered_budget", "BUDGET_INVALID"),
    ("risk_limits_record", "RISK_LIMITS_UNUSABLE"),
    ("bracket_breaker", "BRACKET_BREAKER"),
    ("api_breaker", "API_BREAKER"),
    ("entry_marks", "ENTRY_MARKS_UNREADABLE"),
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _component(ok: bool | None, reason: str, **extra: Any) -> dict[str, Any]:
    return {"ok": ok, "reason": reason, **extra}


def _rows(status: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The board's check rows; anything else where they belong reads as none (review of #906)."""
    checks = status.get("checks")
    return [c for c in checks if isinstance(c, Mapping)] if isinstance(checks, (list, tuple)) else []


def _check_ok(status: Mapping[str, Any], name: str) -> bool | None:
    """The named row's verdict, or None when the board carries no such row."""
    for check in _rows(status):
        if check.get("check") == name:
            return bool(check.get("ok"))
    return None


def _capability_component(status: Mapping[str, Any]) -> dict[str, Any]:
    implemented = status.get("order_path_implemented")
    wired = status.get("autonomous_routing_wired")
    if implemented is None or wired is None:
        return _component(None, NOT_REPORTED)
    if not implemented:
        return _component(False, "ORDER_PATH_NOT_IMPLEMENTED")
    if not wired:
        return _component(False, "ROUTING_NOT_WIRED")
    return _component(True, "INSTALLED")


def _stage_component(status: Mapping[str, Any]) -> dict[str, Any]:
    stage = _mapping(status.get("execution_stage"))
    if "admits_entry" not in stage:
        return _component(None, NOT_REPORTED)
    name = str(stage.get("stage"))
    if stage.get("admits_entry"):
        return _component(True, f"STAGE_{name}", stage=name)
    # An unbound record reads READ_ONLY; its own reason says why, which the rung name would not.
    reason = str(stage["reason_code"]) if stage.get("valid") is False and stage.get("reason_code") \
        else f"STAGE_{name}"
    return _component(False, reason, stage=name)


def _gate_component(status: Mapping[str, Any], inputs: Mapping[str, Any], *,
                    opted: bool) -> dict[str, Any]:
    """The opt-in, the confirmation phrase and the manual kill switch — the gate and the two switches
    the guard refuses every autonomous entry on."""
    if opted:
        if _check_ok(status, "confirmation_phrase") is not True:
            return _component(False, "CONFIRMATION_MISSING", source=SOURCE_THIS_PROCESS)
        if _check_ok(status, "manual_kill_switch") is not True:
            return _component(False, "MANUAL_KILL_ENGAGED", source=SOURCE_THIS_PROCESS)
        return _component(True, "OPEN", source=SOURCE_THIS_PROCESS)
    # This process's env rows describe this container (#382); the trading process's record decides.
    recorded = _mapping(status.get("recorded_gate"))
    if not recorded:
        return _component(None, NOT_REPORTED, source=SOURCE_RECORDED)
    if not recorded.get("known"):
        return _component(None, "RECORD_UNREADABLE" if recorded.get("error") else "NO_RECORD",
                          source=SOURCE_RECORDED)
    if recorded.get("stale"):
        return _component(None, "RECORD_STALE", source=SOURCE_RECORDED)
    if not recorded.get("open"):
        return _component(False, "GATE_DISABLED", source=SOURCE_RECORDED)
    switches = _mapping(_mapping(inputs.get("recorded_fire")).get("gate"))
    if not switches:
        # A record written before the leg stamped its switches: the opt-in is known, the rest is not.
        return _component(None, "SWITCHES_NOT_RECORDED", source=SOURCE_RECORDED)
    if not switches.get("confirmation_present"):
        return _component(False, "CONFIRMATION_MISSING", source=SOURCE_RECORDED)
    if switches.get("manual_kill_switch"):
        return _component(False, "MANUAL_KILL_ENGAGED", source=SOURCE_RECORDED)
    return _component(True, "OPEN", source=SOURCE_RECORDED)


def _control_component(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """The runtime control state every entry reads (`ControlState.trading_allowed`): ACTIVE, armed, and
    no halt placed."""
    control = _mapping(inputs.get("runtime_control"))
    if not control:
        return _component(None, NOT_REPORTED)
    if control.get("fail_closed"):
        return _component(False, "CONTROL_FAIL_CLOSED")
    mode = control.get("mode")
    if mode == KILLED:
        return _component(False, "RUNTIME_KILLED")
    if mode == PAUSED:
        return _component(False, "RUNTIME_PAUSED")
    if mode != ACTIVE:
        # No mode: the state could not be read, and the leg's own read would refuse (BLOCKED).
        return _component(False, "CONTROL_UNREADABLE")
    # A named halt before the arm, as `ControlState.trading_allowed` refuses on either (PR6). Any
    # level but SOFT reads HARD, the way `control.halt_level_of` reads the file.
    level = control.get("halt_level")
    if level is not None:
        return _component(False, "SOFT_HALT" if level == HALT_SOFT else "HARD_HALT")
    if not control.get("trading_armed"):
        return _component(False, "TRADING_DISARMED")
    return _component(True, "ACTIVE_ARMED")


def _armed_component(status: Mapping[str, Any], inputs: Mapping[str, Any]) -> dict[str, Any]:
    """How many armed strategies the gate would accept. An unreadable pool is refused by the entry
    door, so it is False, not unknown."""
    armed = _mapping(status.get("live_armed_strategies"))
    if not armed:
        return _component(None, NOT_REPORTED, count=None, armed=None)
    if not armed.get("known"):
        return _component(False, "POOL_UNREADABLE", count=None, armed=None)
    raw = _count(armed.get("armed"))
    tradable = _count(inputs.get("tradable_armed"))
    if tradable is None:
        return _component(None, NOT_REPORTED, count=None, armed=raw)
    if tradable > 0:
        return _component(True, "ARMED", count=tradable, armed=raw)
    return _component(False, "ARMED_CANNOT_TRADE" if raw else "NONE_ARMED", count=0, armed=raw)


def _venue_component(status: Mapping[str, Any]) -> dict[str, Any]:
    """The venue contract (PR4b): a usable PASS. The door judges each entry's own symbol, so a PASS
    that covers some of the budget's symbols admits those (review of #906): True, naming the rest in
    ``uncovered``, where the check row — "every budget symbol covered" — fails. False only when the
    PASS is not usable or covers none of them."""
    row = _check_ok(status, "venue_contract")
    if row is None:
        return _component(None, NOT_REPORTED)
    if row:
        return _component(True, "USABLE")
    contract = _mapping(status.get("venue_contract"))
    if contract.get("error"):
        return _component(False, "CONTRACT_UNREADABLE")
    if not contract.get("usable"):
        return _component(False, str(contract.get("refusal") or "CONTRACT_NOT_USABLE"))
    budget_symbols, uncovered = contract.get("budget_symbols"), contract.get("uncovered")
    if (isinstance(budget_symbols, list) and isinstance(uncovered, list)
            and 0 < len(uncovered) < len(budget_symbols)):
        return _component(True, "USABLE_PARTIAL", uncovered=[str(s) for s in uncovered])
    return _component(False, "BUDGET_SYMBOL_UNCOVERED")


def _risk_component(status: Mapping[str, Any], inputs: Mapping[str, Any], *,
                    opted: bool) -> dict[str, Any]:
    """Every breaker and record the entry door refuses on, the C4 loss breakers, today's realized loss
    and the daily order cap. Several can refuse at once, and each is named."""
    refused: list[str] = []
    unknown: list[str] = []
    for row, code in _RISK_ROWS:
        ok = _check_ok(status, row)
        if ok is None:
            unknown.append(NOT_REPORTED)
        elif not ok:
            refused.append(code)
    # The entry path appends to the pre-order snapshot record and refuses when it cannot: a damaged
    # line. A row that parses but fails its seal fails the check row — the record no longer proves why
    # past orders left — and refuses no entry (review of #906).
    ok = _check_ok(status, "pre_order_snapshots")
    if ok is None:
        unknown.append(NOT_REPORTED)
    elif not ok and _mapping(status.get("pre_order_snapshots")).get("appendable") is not True:
        refused.append("PRE_ORDER_SNAPSHOTS_UNREADABLE")
    c4 = _mapping(inputs.get("c4"))
    if not c4:
        unknown.append(NOT_REPORTED)
    elif c4.get("allow") is None:
        unknown.append("C4_UNREADABLE")
    elif not c4.get("allow"):
        refused.append("C4_BREAKER")
    account = _mapping(inputs.get("account"))
    if opted or account.get("source") == SOURCE_THIS_PROCESS:
        # Measured here, from this process's own account read — the row. The trading process always,
        # and any process holding the account feed (review of #906).
        ok = _check_ok(status, "daily_loss_breaker")
        if ok is None:
            unknown.append(NOT_REPORTED)
        elif not ok:
            refused.append("DAILY_LOSS")
    else:
        breached = account.get("daily_loss_breached")
        if breached is None:
            unknown.append("DAILY_LOSS_UNMEASURED")
        elif breached:
            refused.append("DAILY_LOSS_FIGURE_MISSING"
                           if account.get("loss_error") == LIVE_PNL_VENUE_FIGURE_MISSING
                           else "DAILY_LOSS_BREACHED")
    cap = _mapping(inputs.get("daily_order_cap"))
    submitted, limit = _count(cap.get("submitted_today")), _count(cap.get("cap"))
    if not cap:
        unknown.append(NOT_REPORTED)
    elif cap.get("error"):
        refused.append("ORDER_COUNTER_UNREADABLE")
    elif submitted is not None and limit is not None and limit > 0 and submitted >= limit:
        # A cap of zero is a budget that backs nothing, which BUDGET_INVALID already names.
        refused.append("DAILY_ORDER_CAP")
    if refused:
        return _component(False, "+".join(dict.fromkeys(refused)))
    if unknown:
        return _component(None, "+".join(dict.fromkeys(unknown)))
    return _component(True, "CLEAR")


def _fire_unknown(fire: Mapping[str, Any]) -> str | None:
    """Why the last fire cannot be read, or None when it can."""
    if not fire:
        return NOT_REPORTED
    if not fire.get("known") or not _count(fire.get("contexts")):
        return "RECORD_UNREADABLE" if fire.get("error") else "NO_RECORD"
    return None


def _fire_unseen(fire: Mapping[str, Any]) -> str | None:
    """Why the last fire cannot speak for the next one, or None when it can: none readable, or one too
    old or undatable — which `trading_cycle_recent` judges, and names as the refusal."""
    missing = _fire_unknown(fire)
    if missing is not None:
        return missing
    if fire.get("recent") is None:
        return "RECORD_UNDATED"
    if not fire.get("recent"):
        return "NO_RECENT_CYCLE"
    return None


def _majority(fire: Mapping[str, Any], count: Any) -> bool:
    """The pipeline's stall rule (`pool_cycle_is_stalled`, Thomas 2026-08-22), for every refusal the
    last fire recorded per context: half or more of its contexts. One flaky context of thirteen does
    not make a component False; the other twelve still enter."""
    contexts, count = _count(fire.get("contexts")) or 0, _count(count) or 0
    return contexts > 0 and count > 0 and 2 * count >= contexts


def _account_component(inputs: Mapping[str, Any], *, opted: bool) -> dict[str, Any]:
    account = _mapping(inputs.get("account"))
    if not account:
        return _component(None, NOT_REPORTED)
    if opted and not account.get("configured"):
        # The leg reads the account before it enters and refuses without it.
        return _component(False, "ACCOUNT_FEED_NOT_CONFIGURED", source=SOURCE_THIS_PROCESS)
    if account.get("source") == SOURCE_THIS_PROCESS:
        if account.get("readable"):
            return _component(True, "READ", source=SOURCE_THIS_PROCESS)
        return _component(False, _ACCOUNT_UNREADABLE, source=SOURCE_THIS_PROCESS)
    # The trading process's own reads at its last fire decide before its snapshot does: the store keeps
    # the previous snapshot when a read fails, so a fresh snapshot is no evidence the account reads now
    # (review of #906).
    fire = _mapping(inputs.get("recorded_fire"))
    if _fire_unseen(fire) is None and _majority(fire, fire.get("account_unreadable")):
        return _component(False, _ACCOUNT_UNREADABLE, source=SOURCE_RECORDED)
    if not account.get("recorded"):
        return _component(None, "SNAPSHOT_MISSING", source=SOURCE_RECORDED)
    if account.get("stale"):
        return _component(None, "SNAPSHOT_STALE", source=SOURCE_RECORDED)
    return _component(True, "SNAPSHOT_FRESH", source=SOURCE_RECORDED)


def _cycle_component(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Whether the trading process is still firing: entries happen only inside a cycle (review of
    #906). False when no pipeline schedule is enabled, or when the last fire is older than three of the
    schedule's intervals — a scheduler that stopped, or a schedule turned off. No door refuses on it:
    nothing runs to enter, and False is the safe direction (the model's second coarser judgement)."""
    window = _mapping(inputs.get("cycle_window"))
    if not window:
        return _component(None, NOT_REPORTED)
    if window.get("scheduled") is False:
        return _component(False, "PIPELINE_DISABLED")
    fire = _mapping(inputs.get("recorded_fire"))
    unseen = _fire_unseen(fire)
    if unseen == "NO_RECENT_CYCLE":
        return _component(False, unseen)
    if unseen is not None:
        return _component(None, unseen)
    return _component(True, "RECENT")


# Counted per context under the first reason its data is refused, in this order.
_DATA_REFUSALS = (("SYNTHETIC", "synthetic"), ("DEGRADED", "degraded"), ("DATA_HEALTH", "data_health"),
                  ("OPTIONAL_DATA", "optional_data"))


def _market_data_component(status: Mapping[str, Any], inputs: Mapping[str, Any], *,
                           opted: bool) -> dict[str, Any]:
    """Whether the last fire's contexts had data the entry door trades on, by the stall rule
    (`_majority`) — though a minority may still enter. The reason names the commonest refusal."""
    if opted and _check_ok(status, "market_data_visibility") is False:
        return _component(False, "MARKET_DATA_NOT_CONFIGURED")
    fire = _mapping(inputs.get("recorded_fire"))
    unseen = _fire_unseen(fire)
    if unseen is not None:
        return _component(None, unseen)
    refused = {kind: _count(fire.get(key)) or 0 for kind, key in _DATA_REFUSALS}
    if _majority(fire, sum(refused.values())):
        commonest = max(refused, key=lambda kind: refused[kind])
        return _component(False, f"{_MAJORITY}{commonest}")
    return _component(True, "FRESH")


def _reconciliation_component(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Whether the leg can reconcile the book with the venue. The book itself, read now as the leg reads
    it first: a record it cannot read or attribute refuses the whole leg. Then the last fire: an
    INCIDENT or a halt there halts the next pass too, until the book and the venue agree; and legs
    refused whole before any venue action (BLOCKED), by the stall rule, name the commonest refusal —
    they settled, protected and entered nothing (review of #906)."""
    if _mapping(inputs.get("position_book")).get("readable") is False:
        return _component(False, "POSITION_BOOK_UNREADABLE")
    fire = _mapping(inputs.get("recorded_fire"))
    unseen = _fire_unseen(fire)
    if unseen is not None:
        return _component(None, unseen)
    if fire.get("incident"):
        return _component(False, "INCIDENT")
    if _majority(fire, fire.get("blocked")):
        code = fire.get("blocked_code")
        return _component(False, f"{_LEG_BLOCKED}_{code}" if isinstance(code, str) and code else _LEG_BLOCKED)
    return _component(True, "CLEAR")


def _stall_rule_refusal(name: str, component: Mapping[str, Any]) -> bool:
    """Whether this refusal is one of the stall rule's (`_majority`): False for most of the last fire's
    contexts rather than for every entry. The account read counts only as the trading process recorded
    it; this process's own failed read refuses its every entry."""
    reason = component.get("reason")
    if not isinstance(reason, str):
        return False
    if name == "market_data_ready":
        return reason.startswith(_MAJORITY)
    if name == "reconciliation_ready":
        return reason == _LEG_BLOCKED or reason.startswith(_LEG_BLOCKED + "_")
    if name == "account_ready":
        return reason == _ACCOUNT_UNREADABLE and component.get("source") == SOURCE_RECORDED
    return False


def minority_may_enter(state: Mapping[str, Any]) -> bool:
    """True when the answer is False by the stall rule alone: most of the last fire's contexts were
    refused, and the rest may still open a position at the next cycle. Any other refusal refuses every
    entry, and then this is False (review of #907: "no autonomous entry can open now" overclaimed)."""
    components = _mapping(state.get("components"))
    refused = [(name, c) for name, c in components.items() if isinstance(c, Mapping) and c.get("ok") is False]
    return bool(refused) and all(_stall_rule_refusal(name, c) for name, c in refused)


def readiness_state(status: Mapping[str, Any]) -> dict[str, Any]:
    """The readiness state (model v2): each component with its value and reason, and
    `live_entry_possible` as their three-valued AND. Pure over a board, and never raises: a fact the
    board does not carry is ``None`` (``NOT_REPORTED``), never a guess."""
    inputs = _mapping(status.get("readiness_inputs"))
    # A process that carries the live-trading environment is the trading process: its own switches
    # and its own account read decide. Everywhere else the trading process's record does.
    opted = _check_ok(status, "live_trading_opt_in") is True
    components = {
        "live_capability_installed": _capability_component(status),
        "execution_stage": _stage_component(status),
        "live_gate_open": _gate_component(status, inputs, opted=opted),
        "runtime_control": _control_component(inputs),
        "armed_strategy_count": _armed_component(status, inputs),
        "venue_ready": _venue_component(status),
        "risk_ready": _risk_component(status, inputs, opted=opted),
        "account_ready": _account_component(inputs, opted=opted),
        "trading_cycle_recent": _cycle_component(inputs),
        "market_data_ready": _market_data_component(status, inputs, opted=opted),
        "reconciliation_ready": _reconciliation_component(inputs),
    }
    blocking = [f"{name}:{c['reason']}" for name, c in components.items() if c["ok"] is False]
    unknown = [f"{name}:{c['reason']}" for name, c in components.items() if c["ok"] is None]
    possible: bool | None = False if blocking else (None if unknown else True)
    return {
        "model": READINESS_MODEL,
        "live_entry_possible": possible,
        "blocking": blocking,
        "unknown": unknown,
        "components": components,
    }


def readiness_data(status: Mapping[str, Any]) -> dict[str, Any]:
    """The board's structured view — the facts a reader must keep apart, as fields a JSON
    consumer reads instead of parsing the rendered rows (sequence 2, P02).

    One word cannot carry what this board says. These fields keep the meanings apart:

    * ``infrastructure_ready`` — every check row ok, i.e. "can THIS process place a live
      order". Read through the assistant door it describes that door's container, which
      carries no ``MVP_LIVE_*``; ``env_scope`` and ``env_out_of_scope`` say so.
    * ``live_armed_strategies`` — how many occupying strategies are ``live_tier=LIVE``.
      Zero armed beside ``ready: true`` is this host's normal state (2026-09-13 review) and
      the one a summariser turns into "live trading is on".
    * ``recorded_gate`` — the trading process's own last word about its gate, dated, with
      the ``stale`` verdict the text banner uses.
    * ``live_entry_possible`` — whether the autonomous leg may open a real position at its next
      cycle: the three-valued AND of the readiness state's components (model v2, PR5a — see
      `readiness_state`). ``False`` names what refuses in ``readiness.blocking``; ``None`` names
      what this process cannot see in ``readiness.unknown``, and is never a guess in either
      direction. Until PR5a this was four facts — armed, the recorded gate, the stage, the
      contract — and read True under a kill or a disarm (FO-10).
    * ``readiness`` — the components themselves, each ``{ok: true|false|null, reason}``.
    """
    armed = status.get("live_armed_strategies") or {}
    gate = status.get("recorded_gate") or {}
    state = readiness_state(status)
    guard = status.get("guard_dry_run") or {}
    return {
        "as_of": status.get("created_at"),
        "infrastructure_ready": bool(status.get("ready")),
        "env_scope": "this_process",
        "env_out_of_scope": env_out_of_scope(status),
        "checks": [
            {"check": check.get("check"), "ok": bool(check.get("ok"))} for check in _rows(status)
        ],
        "live_armed_strategies": {
            "known": bool(armed.get("known")),
            "armed": armed.get("armed"),
            "occupying": armed.get("occupying"),
            "error": armed.get("error"),
        },
        "recorded_gate": {
            "known": bool(gate.get("known")),
            "open": gate.get("open"),
            "status": gate.get("status"),
            "recorded_at": gate.get("recorded_at"),
            "age_seconds": gate.get("age_seconds"),
            "stale": bool(gate.get("stale")),
        },
        "live_entry_possible": state["live_entry_possible"],
        "readiness": {key: state[key] for key in ("model", "blocking", "unknown", "components")},
        # The stage record's own answer: which rung, whether it binds, whether it admits a new
        # entry, and that the doors read it (crypto PR1a/PR1b).
        "execution_stage": status.get("execution_stage"),
        # The venue contract sentinel's last decided answer (PR4a); `usable` is what the mainnet doors
        # require of every entry (PR4b), for the entry's own symbol among `symbols`.
        "venue_contract": {
            key: (status.get("venue_contract") or {}).get(key)
            for key in ("error", "recorded", "status", "verified_at", "age_seconds", "stale",
                        "version_current", "usable", "failed_checks", "symbols")
        },
        "guard_dry_run_status": guard.get("status"),
        "submitted_today": status.get("submitted_today"),
    }


def _opted_in(status: Mapping[str, Any]) -> bool:
    return _check_ok(status, "live_trading_opt_in") is True


def contradicts_recorded_gate(status: Mapping[str, Any]) -> bool:
    """True when this board's env rows say "off" and the trading process says otherwise.

    The single state this board must never render as a plain "live trading off": the reader is
    on a console that cannot see the money path, and the process that can was trading when it
    last wrote. A stale or unknown record does NOT qualify — an old record is not evidence about
    now, and inventing a contradiction from one would trade this false negative for a false
    positive.
    """
    recorded = status.get("recorded_gate") or {}
    return (
        not _opted_in(status)
        and bool(recorded.get("known"))
        and bool(recorded.get("open"))
        and not recorded.get("stale")
    )


def env_out_of_scope(status: Mapping[str, Any]) -> bool:
    """True when this board's env rows describe only this container (crypto PR5b, FC-10).

    A process without the live-trading environment cannot see the trading process's, so its env
    rows are a fact about itself. The one exception is the trading process's own fresh record of a
    CLOSED gate: then "live trading off" is true of the system as well, and the rows say it.

    Until PR5b only :func:`contradicts_recorded_gate` qualified — a fresh record of an OPEN gate —
    on the argument that absence of evidence is no licence to soften a row. That was right while
    these rows were the board's conclusion. They are not any more: whether a live entry can open is
    the readiness state's answer (PR5a), at the top of the board and in its last line. What was
    left was the harm: a console whose last record had gone stale — the state a kill leaves behind,
    since a killed runtime writes no more cycles — printed "MVP_LIVE_TRADING is not 'real' (live
    trading off)" off its own empty environment. Right by accident after a kill, for the wrong
    reason, in the one sentence a summariser lifts (the 2026-08-10 misreport). ``ready`` and
    ``checks`` are untouched: this decides what the board SAYS, never what it permits.
    """
    recorded = status.get("recorded_gate") or {}
    closed_now = bool(recorded.get("known")) and not recorded.get("stale") and not recorded.get("open")
    return not _opted_in(status) and not closed_now


# The rows computed from ``os.environ``. On a process that carries no live-trading environment
# every one of them fails for a single reason — the environment is absent — and the failure is a
# fact about that container, never about the system. Named here so the renderer can say so on the
# ROW, which is the part a reader keeps.
ENV_SCOPED_CHECKS = frozenset({
    "live_trading_opt_in",
    "confirmation_phrase",
    "account_visibility",
    "market_data_visibility",
})

# What those rows are marked instead of FAIL while the banner is up (`env_out_of_scope`). Four characters, so the
# columns still line up under `[PASS]` / `[FAIL]` on an 80-column console.
OUT_OF_SCOPE_MARK = "n/a "

# ...and what they say instead of their own detail. The detail is REPLACED, not annotated: the
# text `live_trading_opt_in` carries out of here is "MVP_LIVE_TRADING is not 'real' (live trading
# off)", which is the exact sentence that reached the operator as a system claim. A suffix leaves
# it in the row for a summariser to lift; substitution does not. The env var names are dropped
# with it and that is the right trade — they describe a container that is not the money path, and
# the banner above names the command that renders the one that is.
OUT_OF_SCOPE_DETAIL = "not observable from this container - see the banner and live_gate_recorded"

# The loss breaker's own stand-in, for a row that fails NO DATA SOURCE only because this process reads
# no account (`env_scoped` on the row): "the limit currently bounds nothing" read as a claim about the
# system while the trading process measured it (review of #907).
OUT_OF_SCOPE_LOSS_DETAIL = ("not observable from this container (no account feed) - risk_ready above "
                            "reads the trading process's own snapshot")
_OUT_OF_SCOPE_DETAILS = {"daily_loss_breaker": OUT_OF_SCOPE_LOSS_DETAIL}


def _row(check: Mapping[str, Any], *, env_out_of_scope: bool) -> tuple[str, str]:
    """``(mark, detail)`` — ``n/a`` plus a scoped detail for an env row this process cannot see.

    Only ever downgrades a **failure**, and only while :func:`env_out_of_scope` holds — this process
    carries no live-trading environment and no fresh record of the trading process says its gate
    was closed. The rows are :data:`ENV_SCOPED_CHECKS`, and any row that says its failure is this
    process's own (``env_scoped``: the loss breaker without an account feed). Where the money path
    actually runs the env is present, the predicate is False, and every row reads exactly as it did
    before. ``status["ready"]`` and ``status["checks"]`` are untouched: this is what the board SAYS,
    not what it permits, and a console that cannot see the environment still is not READY.
    """
    if check["ok"]:
        return "PASS", check["detail"]
    if env_out_of_scope and (check["check"] in ENV_SCOPED_CHECKS or check.get("env_scoped") is True):
        return OUT_OF_SCOPE_MARK, _OUT_OF_SCOPE_DETAILS.get(check["check"], OUT_OF_SCOPE_DETAIL)
    return "FAIL", check["detail"]


# Decision 50 (2026-09-19): the manual kill switch is the secondary control — it refuses new entries
# only, and the process reads it at restart. The primary control is the control store, which the
# runtime_active and trading_armed rows read (halt_trading, kill, pause, resume). Said on the row.
MANUAL_KILL_SECONDARY = "secondary: entries only, read at restart; the control store is primary"


def _manual_kill_row(check: Mapping[str, Any], status: Mapping[str, Any]) -> tuple[str, str]:
    """``(mark, detail)`` for the manual kill switch (decision 50).

    In the trading process the row is its own switch. Anywhere else this process's environment says
    nothing about the system, and the row read PASS "clear" off a console that carries no live-trading
    environment at all. It now shows the trading process's last record of the switch — the record
    ``live_gate_open`` already decides on, under the same conditions (fresh, and stamped by a leg that
    records its switches) — or n/a when there is no such record. Rendering only: ``checks`` and
    ``ready`` are untouched."""
    if _opted_in(status):
        mark, detail = _row(check, env_out_of_scope=False)
        return mark, f"{detail} ({MANUAL_KILL_SECONDARY})"
    recorded = _mapping(status.get("recorded_gate"))
    fire = _mapping(_mapping(status.get("readiness_inputs")).get("recorded_fire"))
    switches = _mapping(fire.get("gate"))
    if recorded.get("known") and not recorded.get("stale") and switches:
        seen = f"as the trading process recorded it at {fire.get('created_at')}"
        if switches.get("manual_kill_switch"):
            return "FAIL", f"{MANUAL_KILL_SWITCH_ENV} is engaged, {seen} ({MANUAL_KILL_SECONDARY})"
        return "PASS", f"clear, {seen} ({MANUAL_KILL_SECONDARY})"
    # No usable record of the switch. The banner is pointed at only while it is up: a fresh record of a
    # CLOSED gate takes it down (`env_out_of_scope`), and a gate that never opened stamped no switches
    # (review of PR6d).
    if env_out_of_scope(status):
        return OUT_OF_SCOPE_MARK, f"{OUT_OF_SCOPE_DETAIL} ({MANUAL_KILL_SECONDARY})"
    return OUT_OF_SCOPE_MARK, ("not observable from this container - the trading process last recorded its "
                               f"gate closed, which records no switch ({MANUAL_KILL_SECONDARY})")


def _testnet_evidence_line(status: Mapping[str, Any]) -> str:
    """What the SIGNED_TESTNET -> LIVE_AUTONOMOUS climb would find. Informational: it gates no
    check here, because the ladder's own door is where it decides anything (PR1d-2)."""
    evidence = status.get("testnet_evidence") or {}
    if evidence.get("error"):
        return f"[----] {'testnet_evidence':24} UNREADABLE - {evidence['error']}"
    complete = evidence.get("complete") or []
    if not complete:
        recorded = evidence.get("recorded") or 0
        detail = (f"{recorded} recorded, none complete" if recorded else "none recorded")
        return (f"[----] {'testnet_evidence':24} {detail} - a LIVE_AUTONOMOUS climb needs one "
                "(scripts/run_signed_testnet_cycle.py --run)")
    return (f"[----] {'testnet_evidence':24} {len(complete)} complete cycle(s), latest "
            f"{complete[-1]} - name it with --testnet-cycle on the stage ask")


def _venue_contract_detail(contract: Mapping[str, Any], *, uncovered: list[str]) -> str:
    """The venue contract row's detail (PR4b): what the sentinel last decided, and what the operator
    does about it."""
    last = contract.get("last_attempt") or {}
    tail = f"; last attempt {last.get('at')}: {last.get('line')}" if last else ""
    if contract.get("error"):
        return (f"UNREADABLE ({contract['error']}) - every mainnet entry is refused until the record proves "
                f"itself; find out who changed it before the next fire rewrites it{tail}")
    if not contract.get("recorded"):
        return ("none recorded - every mainnet entry is refused until the pipeline fire records a PASS "
                f"(it asks while live trading is opted in; python -m scripts.venue_contract --show){tail}")
    age = contract.get("age_seconds")
    age_text = f"{int(age // 60)}m old" if isinstance(age, (int, float)) else "age unknown"
    # The judge's own reason, in the judge's order: a stale record under another version reads as the
    # doors refuse it.
    verdict = "usable" if contract.get("usable") else {
        ENTRY_CONTRACT_VERSION: "other contract version", ENTRY_CONTRACT_STALE: "STALE",
    }.get(contract.get("refusal"), "not usable")
    failed = contract.get("failed_checks") or []
    failed_text = f" (failed: {', '.join(failed)})" if failed else ""
    detail = f"{contract.get('status')}{failed_text} at {contract.get('verified_at')}, {age_text} - {verdict}"
    if contract.get("usable") and uncovered:
        detail += f", but not for {', '.join(uncovered)} (the budget names it; the verification did not)"
    elif not contract.get("usable"):
        detail += " - every mainnet entry is refused"
    return detail + tail


def _recorded_gate_line(status: Mapping[str, Any]) -> str:
    recorded = status.get("recorded_gate") or {}
    if not recorded.get("known"):
        detail = recorded.get("error") or "no crypto cycle has been recorded yet"
        return f"[----] {'live_gate_recorded':24} UNKNOWN - {detail}"
    state = "OPEN" if recorded.get("open") else "CLOSED"
    age = recorded.get("age_seconds")
    when = recorded.get("recorded_at")
    suffix = " [STALE - not a statement about now]" if recorded.get("stale") else ""
    minutes = f", {int(age // 60)}m ago" if isinstance(age, (int, float)) else ""
    return (
        f"[----] {'live_gate_recorded':24} trading process recorded the gate {state} "
        f"at {when} ({recorded.get('status')}{minutes}){suffix}"
    )


def _record_age(recorded: Mapping[str, Any]) -> str:
    """Why a record of the gate speaks for no now, by kind: the recorded gate's ``stale`` covers a
    stamp over two hours old, one dated ahead of this clock and one that does not parse, and only
    the first is an age (review of #907)."""
    age = recorded.get("age_seconds")
    if not isinstance(age, (int, float)) or isinstance(age, bool):
        return "cannot be dated"
    if age < -FUTURE_SKEW_SECONDS:
        return "is dated ahead of this clock"
    return "is over two hours old"


def _named(names: list[str]) -> list[str]:
    """``component:REASON`` entries as ``component (REASON)``, for a reader."""
    return ["{} ({})".format(*entry.split(":", 1)) if ":" in entry else entry for entry in names]


def _wrap(head: str, items: list[str], *, width: int = 80) -> list[str]:
    """``items`` after ``head``, comma-separated and wrapped at ``width`` between items — never inside
    one, so a component and its reason stay on one line for a reader and for grep. An item wider than
    the room left keeps a line of its own."""
    lines: list[str] = []
    line, filled = head, False
    for index, item in enumerate(items):
        piece = item + ("," if index < len(items) - 1 else "")
        if filled and len(line) + 1 + len(piece) > width:
            lines.append(line)
            line = " " * len(head) + piece
        else:
            line = f"{line} {piece}" if filled else line + piece
        filled = True
    lines.append(line.rstrip())
    return lines


def _readiness_lines(state: Mapping[str, Any]) -> list[str]:
    """The readiness state as the head of the board (PR5b): the answer first, then what refuses it,
    what cannot be seen from here, and what admits. Placed above the rows because the rows are this
    process's checks, and the conclusion a reader takes away must not be built from them."""
    possible = state.get("live_entry_possible")
    components = state.get("components") or {}
    admitting = [name for name, c in components.items() if c.get("ok") is True]
    if possible is True:
        head = "YES - the next cycle may open a REAL position"
    elif possible is False and minority_may_enter(state):
        # The stall rule's judgements alone: most contexts refused, not every one (review of #907).
        head = "NO - for most contexts; a minority may still enter"
    elif possible is False:
        head = "NO - no autonomous entry can open now"
    else:
        head = "UNKNOWN - this process cannot see every fact it needs"
    lines = ["LIVE ENTRY POSSIBLE: " + head]
    if state.get("blocking"):
        lines += _wrap("  blocked by : ", _named(list(state["blocking"])))
    if state.get("unknown"):
        lines += _wrap("  unknown    : ", _named(list(state["unknown"])))
    if admitting:
        lines += _wrap("  admitting  : ", admitting)
    lines.append("  The rows below are THIS process's own checks, and so is the THIS PROCESS line")
    lines.append("  under them - neither says whether the system trades.")
    return lines


def _live_entry_verdict(state: Mapping[str, Any]) -> str:
    """The board's last line: the answer, and why — the line a hurried reader keeps. ONE line however
    long, the one line the board does not wrap (review of #907): wrapped, the board ended on a bare
    `armed_strategy_count (NONE_ARMED)`. A console and Telegram wrap it for the eye; grep and a
    reader that keeps the last line get all of it."""
    possible = state.get("live_entry_possible")
    if possible is True:
        return "LIVE ENTRY POSSIBLE: YES - the next cycle may open a REAL position"
    if possible is False:
        return ("LIVE ENTRY POSSIBLE: NO - blocked by " + ", ".join(_named(list(state.get("blocking") or [])))
                + ("; a minority of contexts may still enter" if minority_may_enter(state) else ""))
    return "LIVE ENTRY POSSIBLE: UNKNOWN - this process cannot see " + ", ".join(
        _named(list(state.get("unknown") or [])))


def render_readiness_text(status: dict[str, Any]) -> str:
    """ASCII-only board. Windows consoles are cp949."""
    lines = ["=== live trading readiness ==="]
    state = readiness_state(status)
    lines += _readiness_lines(state)
    lines.append("")
    # Before the rows, not after: the rows are what mislead, so a reader must meet the warning
    # first. #382 — the operator console and the assistant read door both ran this board in
    # containers with no MVP_LIVE_*, and both told a reader live trading was off while the
    # scheduler held an open gate.
    #
    # The banner was not enough. Measured 2026-08-10, three times in one hour: the assistant read
    # this exact board — banner at the top, "(THIS PROCESS ONLY)" in the verdict, and a tool
    # description telling it how to read both — and still reported "MVP_LIVE_TRADING is not 'real'
    # so live trading is disabled" to the operator while the scheduler held an OPEN gate. Prose
    # around a row does not beat the row: `[FAIL] live_trading_opt_in ... (live trading off)` is
    # the thing a summariser carries out, so the scope now lives ON it. Prose is what a careful
    # reader reads; the mark is what every reader takes.
    out_of_scope = env_out_of_scope(status)
    recorded = status.get("recorded_gate") or {}
    if out_of_scope:
        lines.append("!! THIS PROCESS CANNOT SEE THE LIVE-TRADING ENVIRONMENT")
        # What IS known about the trading process's gate, in the three states the rows cannot
        # speak for (FC-10): a fresh OPEN record, an old record, and none. "Old" says which kind
        # of old (`_record_age`): a record dated ahead of this clock is not two hours old.
        if contradicts_recorded_gate(status):
            lines.append(f"   the trading process recorded the gate OPEN at {recorded.get('recorded_at')}")
        elif recorded.get("known"):
            lines.append(f"   the trading process's last record ({recorded.get('recorded_at')})")
            lines.append(f"   {_record_age(recorded)} - not a statement about now")
        else:
            lines.append("   no record of the trading process's gate is readable here")
        lines.append("   the env rows below describe THIS container, not the system")
        lines.append("   authoritative board:")
        lines.append("     docker exec thomas-scheduler python -m runtime.mvp_runtime.crypto"
                     ".live_readiness")
        lines.append("")
    for check in status["checks"]:
        if check["check"] == "manual_kill_switch":
            mark, detail = _manual_kill_row(check, status)
        else:
            mark, detail = _row(check, env_out_of_scope=out_of_scope)
        lines.append(f"[{mark}] {check['check']:24} {detail}")
    lines.append(_recorded_gate_line(status))
    lines.append(_testnet_evidence_line(status))
    guard = status["guard_dry_run"]
    lines.append("")
    probe = status.get("guard_dry_run_symbol") or DEFAULT_PROBE_SYMBOL
    lines.append(f"guard dry-run ({probe} at the configured cap): {guard['status']}")
    if out_of_scope:
        # The dry-run puts the REAL guard against THIS process's environment, so its blocks read
        # back the absent env as a refusal — "live trading is not enabled (MVP_LIVE_TRADING is
        # not 'real')", the same sentence the rows above just stopped printing. Listing them here
        # would hand it straight back. The blocks are withheld rather than filtered because
        # deciding which of them is env-derived means matching on their wording, and a message
        # that gets reworded would silently start leaking again.
        blocked = len(guard.get("blocks") or ()) + len(guard.get("repairs") or ())
        lines.append(f"  SCOPE  : this container cannot run the guard for real - {blocked} "
                     "line(s) withheld; they")
        lines.append("           describe this process's environment, not the system. For the "
                     "real dry-run")
        lines.append("           use the authoritative board named above.")
    else:
        for block in guard.get("blocks") or []:
            lines.append(f"  BLOCK  : {block}")
        for repair in guard.get("repairs") or []:
            lines.append(f"  REPAIR : {repair}")
    if status.get("counter_error"):
        lines.append(f"WARNING : daily order counter unreadable ({status['counter_error']})")
    lines.append("")
    # THIS process's verdict, named as such (PR5b). It is what the CLI's exit code follows, and it
    # is not the system's answer — that is the last line.
    if status["ready"]:
        lines.append("THIS PROCESS: READY - every check above passes")
    elif out_of_scope:
        # Unqualified "NOT READY" here would be the same false statement the banner exists to
        # prevent. The second line states what IS known — the trading process's own record.
        lines.append("THIS PROCESS: NOT READY (THIS PROCESS ONLY) - no live-trading environment here;")
        if contradicts_recorded_gate(status):
            lines.append("           the system's own gate was recorded OPEN at "
                         f"{recorded.get('recorded_at')}")
        elif recorded.get("known"):
            lines.append(f"           the system's last record of its gate ({recorded.get('recorded_at')})")
            lines.append(f"           {_record_age(recorded)}")
        else:
            lines.append("           no record of the system's own gate is readable here")
    else:
        lines.append("THIS PROCESS: NOT READY - every FAIL above must clear first")
    if status["order_path_implemented"]:
        # READY is no longer an abstract "configured" — say what it now means.
        lines.append("NOTE  : an order path EXISTS; from a READY process a real order can be placed")
        if status.get("autonomous_routing_wired"):
            # The loudest line the board has, and it earns it: this is the one state in which
            # nobody is standing at a terminal when the order goes out. It says how to stop it
            # too — an operator reading a board they do not like should not have to go and find
            # the runbook first.
            lines.append("NOTE  : autonomous routing is WIRED - a scheduled crypto run on this")
            lines.append("        machine opens and closes REAL positions while LIVE ENTRY POSSIBLE")
            lines.append("        reads YES")
            # The sentence above is the one a reader believed on 2026-08-10 while the armed set
            # was empty. When nothing is armed the qualification goes HERE, where the promise is
            # made — a PASS row further up does not outweigh a NOTE that says positions will
            # appear.
            armed_fact = status.get("live_armed_strategies") or {}
            if armed_fact.get("known") and armed_fact.get("armed") == 0:
                lines.append("NOTE  : ...but 0 strategies are armed - no autonomous entry will OPEN")
                lines.append("        whatever else clears; open positions still close. To arm")
                lines.append("        one: python -m scripts.promote_strategy_candidates ... --live-tier LIVE")
            # The stop instruction changed with the gate (2026-07-28): there is no grant file to
            # delete any more. `console_cli kill` is what replaces it and is strictly the better
            # instruction — it writes control state, so it lands on the RUNNING scheduler at its
            # next guard rather than at the next restart, and the close path is exempt from it.
            # Both env-based alternatives are worse: MVP_LIVE_MANUAL_KILL_SWITCH needs a restart
            # (and until 2026-09-07 it reached no service at all, so it did nothing whatsoever —
            # forwarded since, which is what makes this line's advice merely worse, not wrong),
            # and clearing MVP_LIVE_TRADING needs a restart AND strands open positions, because
            # the close guard still requires the opt-in. Named in that order, because this line
            # is read in a hurry.
            from .live_route import halt_advice  # one wording for the board and the incident notice
            lines.append("NOTE  : " + halt_advice())
            lines.append("        Do NOT clear MVP_LIVE_TRADING to halt - it needs a restart and it")
            lines.append("        shuts the close path too")
        else:
            lines.append("NOTE  : autonomous routing is NOT wired - the only door is")
            lines.append("        scripts/run_slippage_probe.py --fire, one deliberate probe")
    else:
        lines.append("NOTE  : no order path exists yet; this board cannot report READY until LP4 lands")
    # Last, because it is the line a reader keeps: the system's answer, not this process's.
    lines.append("")
    lines.append(_live_entry_verdict(state))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(
        description="Live-trading readiness board (read-only: no network, no writes, no orders)."
    )
    parser.add_argument("--json", action="store_true",
                        help="emit the full status as JSON, with `live_entry_possible` and `readiness` "
                             "as the [data] line carries them")
    args = parser.parse_args(argv)
    status = build_readiness()
    if args.json:
        # The readiness state in the [data] line's shape (`readiness_data`): the answer at the top
        # level, its components and lists under `readiness` (review of #907).
        state = readiness_state(status)
        sys.stdout.write(json.dumps({
            **status,
            "live_entry_possible": state["live_entry_possible"],
            "readiness": {key: state[key] for key in ("model", "blocking", "unknown", "components")},
        }, ensure_ascii=False, indent=1) + "\n")
    else:
        sys.stdout.write(render_readiness_text(status) + "\n")
    return 0 if status["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
