"""LP5.3 step 3 — cycle routing. The caller the executing leg did not have.

Design record: ``docs/runtime-contracts/LP5_3_LIVE_LEG_DESIGN_V0.1.md``.

Every other piece of the live stack existed before this module: the decision
(``live_entry.plan_live_entry``), the send (``live_execution``), the executing leg
(``live_leg``), the book (``live_position``), the ledger (``live_pnl``), the governance
record (``live_governance``). What did not exist was a caller — so an autonomous run could
not reach any of it, and that absence was the safety story. **This module is that caller**,
and wiring it is the decision the design record sequenced last and separately.

**It is deliberately the only one.** ``crypto/cycle.py`` imports this module and nothing else
from the live stack; a test pins that, so "which code can start a live order" stays a question
with one answer. Adding a second caller is the same size of decision as adding the first.

What did **not** change, and is worth stating because a reader who knows this stack will look
for it: no switch is thrown, no flag is flipped, no phrase is set, no role is activated, and no
cap is widened. Every door LP3/LP4/LP5 built is still in the path, in the same order. What this
adds is a caller that walks up to those doors once per cycle instead of an operator doing it by
hand, and:

- **the gate comes first, and it is the whole switch.** :func:`select_live_gate` selects the
  order adapter through the Safety-Flag Gate. Without ``MVP_LIVE_TRADING=real`` that yields the
  inert dry-run adapter, and this module returns ``DISABLED`` having read no account, opened no
  socket and made no decision. A machine that has not been through the operator checklist
  behaves exactly as it did before this module existed;
- **reconcile before anything.** The venue is the truth. Anything but ``RECONCILED`` refuses
  entries for that symbol while closes stay allowed — being unable to see the account must
  never trap an open position;
- **route once, share the result.** The route comes from the paper step's own evaluation, so
  the live leg cannot disagree with the paper leg about what the strategies said this cycle;
- **a live failure is portfolio-level.** An unprotected position that will not close, a
  venue-side close this runtime cannot price, or a book that disagrees with the venue halts
  the whole fan-out (``halt: True``) rather than being filed as one context's skipped row.
  Paper failures stay per-context; these are about real money whose state is now uncertain,
  and continuing to open positions elsewhere under that uncertainty is the failure mode.

**Exits, and what this increment deliberately does not do.** The normal end of a live position
is the bracket already resting at the venue, not a close this runtime sends — so the cycle's
exit responsibility here is *protection and bookkeeping*, not timing:

1. the venue closed it (bracket triggered) → record the outcome from the leg that filled and
   clear the book (:func:`live_leg.settle_venue_closed_position`);
2. the position is open but its bracket is positively gone → close it
   (:func:`live_leg.execute_live_exit`), rule 2 applied continuously rather than only at entry;
3. the position is protected but out of time → close it at market
   (:func:`_time_exit_or_hold`), paper's ``max_hold`` rule;
4. otherwise → advance the holding count and hold.

**Rule 3 was absent until 2026-07-29 and its absence was a real gap**, recorded here rather
than deleted because the reasoning it replaced is what a future reader will otherwise re-derive.
This docstring used to say there was no time-based exit, because a live position record carried
no holding count and no timeframe — true, and the right call for the routing increment, since a
max-hold rule then would have been inventing state rather than reading it. What made it not
merely a difference: **the promotion evidence gating live trading was produced with the time
exit in force**, so a live leg without one is not running the strategy the evidence describes,
and the direction is unfavourable — a time exit mostly cuts losers, so live held them longer.
LP5.1's record now carries ``timeframe``, ``max_holding_bars`` and the deduped holding counter,
which is what rule 3 reads.

What still differs, and is not a defect: paper models the time exit at the bar's close, live
pays taker plus slippage on a reduceOnly market order. Same rule, different cost — ``r_basis``
keeps the two populations labelled, so do not read the residual gap as drift.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .. import timeutil
from ..approval_store import ApprovalStore
from ..audit import AuditError
from ..coerce import as_optional_float as _f
from ..control import ACTIVE, HALT_HARD, ControlStore
from ..errors import MvpRuntimeError, ToolError
from ..state_guard import assert_not_foreign_root_run
from ..store import LedgerStore
from . import live_execution, live_governance, live_leg, pool, pre_order_gate
from .account import read_account, select_account_feed
from .execution_stage import PURPOSE_AUTONOMOUS
from .live_entry import (
    STATUS_NO_ROUTE,
    gate_live_entry,
    narrow_entry_facts,
    plan_live_entry,
    risk_limits_moved,
)
from .live_filters import read_symbol_filters
from .market_data import ORDER_BOOK_LEVELS, PRICE_UNREADABLE, TIMEFRAMES, read_reference_quote
from .execution_stage import resolve_execution_stage
from .live_order import (
    ApiErrorRecordingAdapter,
    api_breaker_status,
    api_breaker_trip_lines,
    bracket_breaker_status,
    count_today,
    read_live_entry_marks,
    recorded_like,
    resolve_live_order_limits,
    select_live_api_breaker,
    select_live_bracket_breaker,
    select_live_entry_marks,
    select_live_order_counter,
    stop_cooldown_until,
)
from .live_pnl import STOP_EXIT_REASONS, live_risk_snapshot, select_live_ledger, venue_daily_realized_net
from .live_position import (
    DRIFT,
    DRIFT_MISSING_AT_VENUE,
    DRIFT_QUANTITY_MISMATCH,
    DRIFT_SIDE_MISMATCH,
    DRIFT_UNTRACKED_AT_VENUE,
    LIVE_POSITION_SLOT_TAKEN,
    RECONCILED,
    list_open_live_positions,
    position_symbol,
    reconcile_positions,
    select_live_position_store,
)
from . import paper
from .paper import build_entry_plan
from .promotion import live_arm_problem
from .risk_limits import resolve_risk_limits
from .venue_contract import entry_fact as read_venue_contract

LIVE_ROUTE_VERSION = "live_route.v0.1"

# What this cycle's live leg did. One value, reported on the cycle record.
ROUTE_DISABLED = "DISABLED"    # live trading is off here; nothing was read and nothing sent
ROUTE_BLOCKED = "BLOCKED"      # gated open, but a precondition refused before any venue action
ROUTE_HELD = "HELD"            # ran end to end; no entry and no exit was due
ROUTE_SETTLED = "SETTLED"      # a position closed and its outcome was recorded
ROUTE_OPENED = "OPENED"        # a position was opened and bracketed
ROUTE_INCIDENT = "INCIDENT"    # real money is in a state this runtime cannot account for

# Reason codes.
# The owner of a LEGACY position's clock — one opened before the record carried a timeframe.
# It has to match the fan-out's own default (``cycle.run_pool_cycle``'s ``default_timeframe``),
# because naming an owner is only safe if that context is guaranteed to run; both read this.
DEFAULT_TIMING_CONTEXT = "1d"

ROUTING_DISABLED = "LIVE_ROUTING_DISABLED"
ROUTING_PRECONDITION = "LIVE_ROUTING_PRECONDITION_FAILED"
ACCOUNT_UNREADABLE = "LIVE_ROUTING_ACCOUNT_UNREADABLE"
BOOK_DRIFT = "LIVE_ROUTING_BOOK_DRIFT"
AUDIT_NOT_RECORDED = "LIVE_ORDER_AUDIT_NOT_RECORDED"
# `LIVE_ROUTING_CANARY_HISTORY` is retired (2026-09-15, PR1r): it said the canary registry behind
# the promotion gate could not be verified, and the gate is gone. Old cycle rows still carry it;
# the string is never reused.
# The breaker's own state could not be updated after a leg that had something to tell it. By
# then the order is at the venue, so this is reported and never raised — but it is the one
# reason code meaning the count that bounds the naked-entry loop may now be short.
BRACKET_BREAKER_UNRECORDED = "LIVE_BRACKET_BREAKER_UNRECORDED"
# The API error breaker (PR2d-1): a signed call's outcome that could not be recorded (the call itself
# stands), and the pass in which the breaker latched (the operator is told once).
API_BREAKER_UNRECORDED = "LIVE_API_BREAKER_UNRECORDED"
API_BREAKER_JUST_TRIPPED = "LIVE_API_BREAKER_JUST_TRIPPED"
# This pass told the operator the breaker had latched (review of #889: told until it gets through).
API_BREAKER_TOLD = "LIVE_API_BREAKER_TOLD"
# A live stop-out whose cooldown could not be written (PR2a). The settlement itself stands; what
# is missing is the hold that keeps the context out for the next bars, so the operator hears it.
STOP_COOLDOWN_UNRECORDED = "LIVE_STOP_COOLDOWN_UNRECORDED"
# The pre-order gate refused an entry the decision had found READY (PR2b). The failed checks ride
# beside it, by name, so the ledger says which one.
PRE_ORDER_GATE_REFUSED = "LIVE_PRE_ORDER_GATE_REFUSED"
# The gate's re-read (PR2c-2a) could not be completed. The entry is held; nothing was spent. The
# failure's own reason code rides beside it.
PRE_ORDER_REREAD_FAILED = "LIVE_PRE_ORDER_REREAD_FAILED"
# This position is judged by the timeframe table rather than by the `max_holding_bars` its own
# backtest was built on, because it predates the record shape that carries one. Reported so a
# live/backtest R gap stays attributable instead of being rediscovered from a curve.
LIVE_MAX_HOLD_FALLBACK = "LIVE_ROUTING_MAX_HOLD_FALLBACK"
# The time exit was due and the close did not confirm. Not an incident — the bracket is still
# resting at the venue, so the position is protected, just held past its strategy's window.
LIVE_TIME_EXIT_DEFERRED = "LIVE_ROUTING_TIME_EXIT_DEFERRED"
# This context visited an open position it is not the timing authority for, so it settled and
# protected it (both are urgent at any resolution) and left the holding counter alone. Recorded
# rather than silent: "the counter did not move this cycle" and "this context may not move it"
# look identical in a record that says nothing.
LIVE_HOLD_NOT_TIMED_HERE = "LIVE_ROUTING_HOLD_NOT_TIMED_HERE"
# The position's time is up, but the venue holds a different position on its symbol than the book
# (PR2c-0 review): the time exit waits for the drift to be resolved, its stop still resting.
LIVE_TIME_EXIT_HELD_ON_DRIFT = "LIVE_ROUTING_TIME_EXIT_HELD_ON_DRIFT"

# The leg results that mean real money is in a state this runtime cannot account for. Each one
# is a fact about the venue, not a local error: an unprotected position that would not close, a
# position the venue closed at a price this runtime cannot read, or state that reached the venue
# and not the disk. Continuing to open positions in other contexts under any of these is the
# failure this halt exists to prevent.
_INCIDENT_STATUSES = frozenset({live_leg.ENTRY_NAKED_OPEN, live_leg.EXIT_UNSETTLEABLE})
_INCIDENT_REASONS = frozenset({
    live_leg.NAKED_CLOSE_FAILED,
    live_leg.VENUE_CLOSE_UNSETTLEABLE,
    live_leg.POSITION_PERSIST_FAILED,
    live_leg.OUTCOME_PERSIST_FAILED,
    # PR2b-2 review: an entry that outlived its symbol claim, and a book asked to replace or
    # clear another position's record. Either way two positions met on one symbol.
    live_leg.CLAIM_LOST,
    LIVE_POSITION_SLOT_TAKEN,
})


# --- the gate ------------------------------------------------------------------------

def select_live_gate(*, now: str, root: Path | None = None) -> tuple[Any | None, str | None]:
    """The order adapter if live routing is open on this machine, else ``(None, reason)``.

    Deliberately *the* gate rather than a second copy of it: ``select_order_adapter`` is
    ``safety_gate.select_env_gated``, which constructs the capable adapter only after the
    ``MVP_LIVE_TRADING=real`` opt-in is confirmed. Reading that env var here as well would be a
    second opinion about the same question, and the two could drift. That was worth saying when
    the gate also read a grant file; it matters *more* now that the gate is one env var, because
    re-reading it here looks trivially safe and is exactly how the second opinion gets added.

    Both refusals are the same answer — not open — and both are returned rather than raised:
    a cycle must complete and report, and "this machine does not trade live" is the ordinary
    case, not an error.
    """
    try:
        adapter = live_execution.select_order_adapter(now=now, root=root)
    except MvpRuntimeError as exc:
        # Used to be the reachable "opted in, but the grant is missing or expired" path. With
        # the grant gone (2026-07-28) selection itself no longer refuses, so nothing raises here
        # today. Kept, and kept returning rather than raising, because the contract this
        # function owes its caller is "a cycle must complete and report" — a future constructor
        # that can fail should surface as DISABLED-with-a-reason, not as a dead cycle.
        return None, exc.reason_code
    if not bool(getattr(adapter, "network_egress", False)):
        return None, ROUTING_DISABLED
    return adapter, None


def position_timing_context(position: Mapping[str, Any], *, default_timeframe: str) -> str:
    """The timeframe whose cycle is allowed to advance this position's holding counter.

    A live position is booked by **symbol** (the venue nets per symbol in one-way mode) while a
    cycle runs per ``(symbol, timeframe)``, so a symbol routed at four timeframes sends four
    cycles past the same position in one fan-out. `max_holding_bars` counts *bars*, and each of
    those cycles carries a different bar — so without an owner, one fan-out advanced the counter
    four times and a 24-bar position time-exited in six.

    The owner is the position's own timeframe: the one its `max_holding_bars` was backtested
    against. A **legacy** position that predates the field has no such number either
    (`trade_plan.position_max_hold` falls back to the timeframe table), so it is owned by the
    default context — an arbitrary but *single* owner, which is the whole property needed here.
    """
    stored = position.get("timeframe")
    return str(stored) if isinstance(stored, str) and stored else default_timeframe


def live_position_contexts(
    root: Path | None = None, *, default_timeframe: str
) -> list[tuple[str, str]]:
    """Every ``(symbol, timeframe)`` that must run so an open live position is fully serviced.

    ``cycle.pool_cycle_contexts`` needs this so a live position always gets a cycle that can
    settle it — a strategy demoted out of the routable set would otherwise leave its *real*
    position with no visitor, which is the live twin of the symbol-starved router. Exposed
    here rather than imported from ``live_position`` directly so the cycle keeps exactly one
    door into this stack.

    The **timeframe** is now part of the answer, and returned even when the symbol is already
    being visited at some other one. That is the counterpart to :func:`position_timing_context`:
    naming an owner is only safe if the owning context is guaranteed to run, and it is exactly
    the demoted-strategy case that would otherwise strand it — a 4h position on a symbol still
    routed at 15m used to be visited (by a 15m cycle counting 15m bars) and would now be visited
    by one that may not time it at all.
    """
    try:
        positions = list_open_live_positions(root)
    except MvpRuntimeError:
        return []  # a per-context cycle re-reads and records its own fail-closed reason
    return sorted({
        (position_symbol(p), position_timing_context(p, default_timeframe=default_timeframe))
        for p in positions
    })


# --- the leg -------------------------------------------------------------------------

def run_live_leg(
    *,
    route: Mapping[str, Any] | None,
    # #610 Part 1 — which strategies the pool says may open a REAL position. Threaded from the
    # cycle rather than read here: `cycle.py` already holds the pool it ran the ladder on, and the
    # decision is judged on that read. The gate reads the pool again (PR2c-2a) only to narrow: a
    # strategy disarmed since then is refused, and nothing the second read adds is admitted.
    # `None` refuses every entry (see `live_entry`'s door 2a); closes are decided above the entry
    # block and are never affected by it.
    live_routable_strategy_ids: set[str] | None,
    feature_row: Mapping[str, Any],
    verdict: Mapping[str, Any],
    symbol: str,
    collector: Any,
    now: str,
    timeframe: str = "",
    root: Path | None = None,
    control_store: ControlStore | None = None,
    timeout_seconds: int = 10,
    # PR2b, decision 17: which approval armed each LIVE strategy (`pool.live_arm_approvals`). Part
    # of the approved profile the pre-order gate requires; None — the pool could not be read, or a
    # caller that does not say — leaves every entry's profile incomplete, which refuses it.
    live_arm_approvals: Mapping[str, str | None] | None = None,
    # PR2d-2, decision 28: the optional data this context was judged on
    # (`cycle.optional_data_health`). None — a caller that does not say — refuses every entry;
    # closes are decided above the entry block and never read it.
    optional_data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One cycle's live leg: reconcile, settle, protect, maybe open. Returns a record.

    Never raises. A cycle that cannot complete its live leg still has to report what it did
    and did not do — a traceback here would be indistinguishable from "no live activity", and
    that is the one thing this record exists to make legible.

    ``route`` is the paper step's own routing result, shared rather than re-evaluated.

    ``timeframe`` is the calling cycle's own context, and it decides one thing only: whether
    this cycle may advance an open position's holding counter (:func:`position_timing_context`).
    Settling and protecting are urgent at every resolution and stay unconditional. It defaults
    to empty so a caller that does not state a context times nothing rather than timing
    everything — the same direction every other unknown in this stack fails.
    """
    record: dict[str, Any] = {
        "live_route_version": LIVE_ROUTE_VERSION,
        "live_route_status": ROUTE_DISABLED,
        "live_opened": None,
        "live_settled": None,
        "live_reason_codes": [],
        "halt": False,
        "symbol": symbol,
        "created_at": now,
        # The machine's execution stage as this leg read it, and judged its entry against (PR1b).
        # None when the gate never opened — this leg then read nothing, the stage included.
        "execution_stage": None,
        # The gate's two operator switches as this leg read them (PR5a). None when the gate never
        # opened, like the stage — and when the leg stopped before it read its limits (a foreign
        # root run, an unreadable budget: BLOCKED).
        "live_gate": None,
    }

    adapter, gate_reason = select_live_gate(now=now, root=root)
    if adapter is None:
        record["live_reason_codes"].append(gate_reason or ROUTING_DISABLED)
        return record

    recorder: ApiErrorRecordingAdapter | None = None
    try:
        # Every signed call this leg makes is recorded against the API error breaker (PR2d-1).
        # The wrapper forwards everything and adds no egress: it only counts what the venue
        # answered. A write it could not make is on the record the moment it happens, so any
        # notice this pass sends already carries it.
        recorder = ApiErrorRecordingAdapter(
            adapter, select_live_api_breaker(now=now, root=root),
            on_unrecorded=lambda code: _note_codes(record, API_BREAKER_UNRECORDED, code),
        )
        return _run_gated_live_leg(
            record,
            adapter=recorder,
            route=route,
            live_routable_strategy_ids=live_routable_strategy_ids,
            live_arm_approvals=live_arm_approvals,
            feature_row=feature_row,
            verdict=verdict,
            symbol=symbol,
            collector=collector,
            now=now,
            timeframe=timeframe,
            root=root,
            control_store=control_store,
            timeout_seconds=timeout_seconds,
            optional_data=optional_data,
        )
    except MvpRuntimeError as exc:
        # A typed refusal before or between venue calls (a foreign root run, an unreadable
        # budget, a locked book). Reported as BLOCKED rather than halting the fan-out: nothing
        # was sent, so no money is in an unknown state.
        record["live_route_status"] = ROUTE_BLOCKED
        record["live_reason_codes"].append(ROUTING_PRECONDITION)
        record["live_reason_codes"].append(exc.reason_code)
        return record
    except Exception as exc:  # noqa: BLE001 — breadth is the point, see below
        # Past this point an order may already be at the venue, and an unexpected exception
        # would report that as "nothing happened" — the single most expensive thing this
        # module can communicate wrongly (#228, one door over). Halt the fan-out and say so.
        record["live_route_status"] = ROUTE_INCIDENT
        record["live_reason_codes"].append(f"UNEXPECTED_{type(exc).__name__}")
        record["halt"] = True
        return record
    finally:
        # However the pass ended — returned, refused or broke — a latch reaches the operator, once
        # a message gets through (PR2d-1).
        if recorder is not None:
            _close_api_breaker_pass(record, recorder, root=root, now=now)


def _run_gated_live_leg(
    record: dict[str, Any],
    *,
    adapter: Any,
    route: Mapping[str, Any] | None,
    live_routable_strategy_ids: set[str] | None,
    feature_row: Mapping[str, Any],
    verdict: Mapping[str, Any],
    symbol: str,
    collector: Any,
    now: str,
    timeframe: str,
    root: Path | None,
    control_store: ControlStore | None,
    timeout_seconds: int,
    live_arm_approvals: Mapping[str, str | None] | None = None,
    optional_data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The leg proper, once the gate is open. Split out so every exit path above is one
    ``except`` rather than a ``try`` wrapped around two hundred lines."""
    # 0. A host-side root run would leave this cycle's book, ledger and audit rows owned by a
    #    uid the services cannot write again. Before the venue, because afterwards the only
    #    options are a book the services cannot rewrite or a real position with no record.
    assert_not_foreign_root_run(root)

    # 1. The facts, each read once and shared by every door of the decision — so the guard, the
    #    sizing and the record cannot disagree about what was true this cycle. The gate reads the
    #    ones another writer can move again (step 3a, PR2c-2a), only to narrow.
    limits, budget = resolve_live_order_limits(root, now=now)
    # The confirmation phrase and the manual kill switch, from the same `limits` the guard judges
    # (PR5a). Both are this process's environment, which the console and the assistant's read door
    # are built without; stamped so the readiness board there reads the trading process's own
    # switches instead of guessing from rows computed in a container that cannot see them.
    record["live_gate"] = {
        "confirmation_present": limits.confirmation_present(),
        "manual_kill_switch": bool(limits.manual_kill_switch),
    }
    # The execution stage, read once beside the budget (PR1a) and enforced at the entry guard
    # since PR1b. Stamped on the record so the ledger shows the rung the trading process itself
    # saw, and passed to `plan_live_entry` below so the stamp and the judgement are one read.
    # Settlement, protection and the close path below never consult it.
    stage = resolve_execution_stage(root, now=now)
    record["execution_stage"] = stage.as_dict()
    # `.default(root)` rather than `ControlStore(root)`: the constructor takes a Path, so the
    # bare form crashes on the `root=None` every ordinary run passes.
    control = control_store if control_store is not None else ControlStore.default(root)
    # `trading_allowed`, not `execution_allowed`: BOTH must hold for an entry. The runtime can
    # now be ACTIVE with live entries held down — that is what an assistant `enable scope=runtime`
    # leaves behind, and it is the one state where the two answers differ. Reading the weaker
    # flag here would make the arm decorative on the exact path it exists to gate.
    #
    # This gates ENTRIES only, and deliberately: the value is consumed by the entry decision in
    # step 3, after step 2 has settled and protected, so a disarmed runtime that is still ACTIVE
    # closes what it holds. (A PAUSED or KILLED runtime never gets here — the scheduler drops the
    # fire — which is why the soft halt, `control.CMD_HALT_TRADING`, exists.)
    runtime_active = control.load().trading_allowed

    snapshot, account_use = read_account(timeout_seconds=timeout_seconds, root=root)
    if snapshot is None:
        record["live_reason_codes"].append(ACCOUNT_UNREADABLE)
        record["account_degraded_reason_code"] = account_use.get("degraded_reason_code")
    # The account is a signed read; the breaker counts it with the adapter's own (PR2d-1). By
    # the feed's own code: `degraded_reason_code` is one word for every way the read can fail.
    adapter.record_account(readable=snapshot is not None,
                           reason_code=account_use.get("error_reason_code"))

    local_positions = list_open_live_positions(root)
    reconciliation = reconcile_positions(local_positions, snapshot, now=now)
    record["live_reconcile_status"] = reconciliation["status"]

    position_store = select_live_position_store(now=now, root=root)
    ledger = select_live_ledger(now=now, root=root)

    # 2. Settle and protect BEFORE anything else. Closing is risk-reducing and is never gated
    #    on reconciliation, the verdict, or the kill switch — a halt that traps an open
    #    position is worse than what the halt prevents.
    # The bar this cycle is acting on: the feature row's `timestamp`, which is the bar's OPEN
    # time (`features.build_feature_rows`). Paper keys the same bars on `close_time`; the live
    # counter dedups on its own key, and one key per bar is all the time exit needs. It is also
    # the bar an entry below is claimed on (PR2a).
    candle_ts = feature_row.get("timestamp") if isinstance(feature_row, Mapping) else None
    open_here = [p for p in local_positions if position_symbol(p) == symbol]
    for position in open_here:
        _settle_or_protect(
            record, position,
            adapter=adapter, position_store=position_store, ledger=ledger,
            reconciliation=reconciliation, limits=limits, candle_ts=candle_ts,
            context_timeframe=timeframe,
            now=now, root=root, timeout_seconds=timeout_seconds,
        )

    # A position the venue has ALREADY closed settles in whichever context runs first,
    # whatever its symbol. That settlement sends nothing — the MISSING_AT_VENUE branch of
    # `_settle_or_protect` reads the fill and writes the book — and it is the only thing
    # that can clear the drift judged below. Scoping it to the position's own context
    # deadlocked on 2026-08-18 (~02:59Z): a probe position (`timeframe: None`) put ITS
    # symbol's 1d context first in the fan-out, the account-wide drift halted the pass
    # there, and the drifted symbol's context — the only one that would have settled it —
    # never ran. #631's one-cycle settle had only ever worked because the drifted symbol's
    # context happened to run before anything halted; this makes that property structural.
    # Other drift kinds (side, quantity, untracked-at-venue) are deliberately NOT visited
    # here: they are venue states bookkeeping cannot repair, and the halt below is still
    # exactly what they mean. Empty candle_ts/context: a foreign context holds no timing
    # authority and must not touch the position's clock (`_time_exit_or_hold` already
    # times nothing for a caller that names no context) — unreachable on the MISSING
    # branch, load-bearing if these reasons ever widen.
    books = reconciliation.get("books") or {}
    for position in local_positions:
        other = position_symbol(position)
        if other == symbol:
            continue  # settled above, with the context's full leg semantics
        if DRIFT_MISSING_AT_VENUE in ((books.get(other) or {}).get("reasons") or ()):
            _settle_or_protect(
                record, position,
                adapter=adapter, position_store=position_store, ledger=ledger,
                reconciliation=reconciliation, limits=limits, candle_ts=None,
                context_timeframe="",
                now=now, root=root, timeout_seconds=timeout_seconds,
            )

    # A book that still disagrees with the venue AFTER settlement is the dangerous kind: the
    # normal bracket-closed drift resolved itself just above, so what remains is a position
    # this runtime cannot account for. Entries are already refused per symbol; the halt is
    # what stops the *other* contexts from opening under the same uncertainty. Only a
    # MISSING_AT_VENUE drift is one a settle can resolve; any other kind halts even when this pass
    # settled something (PR2c-0 review) — an exit sized from the book does not reconcile the venue.
    unresolvable = any(
        reason != DRIFT_MISSING_AT_VENUE
        for book in books.values() for reason in ((book or {}).get("reasons") or ())
    )
    if reconciliation["status"] == DRIFT and (record["live_settled"] is None or unresolvable):
        record["live_reason_codes"].append(BOOK_DRIFT)
        record["halt"] = True

    if record["halt"]:
        record["live_route_status"] = ROUTE_INCIDENT
        # A close that left a leg resting still has to reach the operator on a halted pass.
        if record.get("live_legs_left"):
            _notify_operator(record, now=now, root=root)
        return record

    # 3. The entry decision. Everything above this line can run with no route at all.
    if record["live_settled"] is not None:
        # A position closed this cycle. Do not re-enter on the same pass: the book was cleared
        # a few lines ago and the venue read predates it, so every exposure figure the guard
        # would judge is now stale. The next cycle sees a consistent picture.
        record["live_route_status"] = ROUTE_SETTLED
        # A leg a close left resting may act on the next position on its symbol (PR2c-0): the one
        # settled outcome the operator has to act on.
        if record.get("live_legs_left"):
            _notify_operator(record, now=now, root=root)
        return record

    plan = build_entry_plan(route, feature_row, now=now) if isinstance(route, Mapping) else None

    filters, filters_reason = read_symbol_filters(collector, symbol, timeout_seconds=timeout_seconds)

    # The book the spread and the market impact are judged on (PR2d-3), handed down whole: the
    # decision derives the spread from it and walks it for the order's size once that is known.
    order_book: Mapping[str, Any] | None = None
    try:
        raw_book = collector.order_book(symbol, limit=ORDER_BOOK_LEVELS, timeout_seconds=timeout_seconds)
        order_book = raw_book if isinstance(raw_book, Mapping) else None
    except Exception:  # noqa: BLE001 — degrade here, refuse at the entry decision: the route
        # itself must not raise (settle/protect already ran above); the None it hands down is
        # what plan_live_entry refuses fail-closed (Thomas 2026-08-30).
        record["live_reason_codes"].append("LIVE_ENTRY_ORDERBOOK_UNREADABLE")

    # The market's price now (PR2c-1, decision 24): the plan is priced on its bar's close, and the
    # decision checks that close against this. Read only when there is a plan to check — a context
    # with no route asks the venue nothing more — and degraded like the book: the None-priced quote
    # it hands down is what the decision refuses.
    reference_quote = (
        _read_reference_quote(collector, symbol, now=now, timeout_seconds=timeout_seconds)
        if plan is not None else None
    )
    if isinstance(reference_quote, Mapping) and reference_quote.get("reason"):
        # Why, beside the decision's own refusal — as the book's read failure is recorded above.
        record["live_reason_codes"].append(str(reference_quote["reason"]))

    # The breaker reads the VENUE's realized figure, not the local ledger: on a machine whose
    # live positions close at the venue the local ledger lags a cycle, and a loss breaker that
    # measures late is a breaker that does not bound today (#247).
    venue_realized = (
        venue_daily_realized_net(snapshot.realized_windows) if snapshot is not None else None
    )
    risk = live_risk_snapshot(
        limit_usdt=limits.daily_loss_limit_usdt, root=root, now=now,
        venue_realized_pnl_usdt=venue_realized,
        # This leg opens positions: a snapshot with no venue figure is a tripped breaker, not the
        # local ledger. No snapshot at all is already ACCOUNT_UNREADABLE and refuses on its own.
        venue_required=snapshot is not None,
    )
    if risk.get("history_error"):
        # Say WHY the breaker reads tripped. The guard's own refusal only knows a bool and would
        # call this "limit reached", which is the wrong fix for an operator to go looking for.
        record["live_reason_codes"].append(risk["history_error"])

    # How many entries in a row filled and could not be protected. Read here with every other
    # runtime fact, and read even when it is zero, so the decision below is judged against the
    # same state the readiness board shows rather than a second opinion of it.
    breaker = bracket_breaker_status(root)
    record["live_bracket_breaker"] = {
        "consecutive": breaker["consecutive"],
        "limit": breaker["limit"],
        "tripped": breaker["tripped"],
    }
    # And how many signed calls in a row the venue would not answer (PR2d-1), read beside it for
    # the same reason: one state, read once, judged by the decision and shown on the board. A
    # breaker that cannot count this pass (its record cannot be written) is not clear either: an
    # entry sent then would leave with nothing to count its failure.
    api_breaker_before = api_breaker_status(root)
    api_unwritable = _api_breaker_unwritable(adapter)
    record["live_api_breaker"] = {
        "consecutive": api_breaker_before["consecutive"],
        "limit": api_breaker_before["limit"],
        "tripped": api_breaker_before["tripped"],
        "tripped_class": api_breaker_before["tripped_class"],
        "unwritable": api_unwritable,
    }
    # And what the venue contract sentinel last decided (PR4b, Thomas decision 46), read here with the
    # breakers for their reason. The reader never raises: a record that cannot prove itself is a
    # refusal the decision names, not an exception on the path that has just settled and protected.
    venue_contract = read_venue_contract(root)
    record["live_venue_contract"] = _contract_summary(venue_contract)

    # Which bars this venue has already sent an entry on, and which contexts a stop-out still
    # holds (PR2a). Read here, after settle/protect, so a corrupt file can only hold entries: the
    # decision refuses on the None, and the record says why.
    try:
        entry_marks = read_live_entry_marks(root)
    except MvpRuntimeError as exc:
        entry_marks = None
        record["live_reason_codes"].append(exc.reason_code)

    # Every fact the decision is judged on, in one mapping: the decision reads it now, and the
    # pre-order gate re-derives the decision from this mapping, narrowed by its re-read (PR2b,
    # PR2c-2a), before anything is sent.
    decision_kwargs = dict(
        plan=plan,
        symbol=symbol,
        live_routable_strategy_ids=live_routable_strategy_ids,
        reconciliation=reconciliation,
        # The book as read at the top of this leg, not a second read. Every path that could
        # have changed it returned above (anything that settled, or failed to), so a re-read
        # would return the same rows — and this module's whole posture is that one fact is
        # read once and shared, because two reads are two chances to disagree.
        local_positions=local_positions,
        snapshot=snapshot,
        filters=filters,
        filters_reason=filters_reason,
        limits=limits,
        execution_stage=stage,
        entry_bar_time=candle_ts,
        entry_marks=entry_marks,
        budget_registered=bool(budget.get("valid")),
        # The scope half of the same budget the caps come from. Read here rather than inside
        # the guard for the reason every other runtime fact is: one read, one set of numbers,
        # no second door able to disagree about which budget is in force.
        allowed_symbols=budget.get("symbol_allowlist") or (),
        gate_open=True,  # the adapter above IS the grant; nothing else selects a capable one
        runtime_active=runtime_active,
        daily_loss_breached=bool(risk["daily_loss_limit_breached"]),
        bracket_failures_consecutive=breaker["consecutive"],
        api_breaker_tripped=bool(api_breaker_before["tripped"]) or api_unwritable,
        venue_contract=venue_contract,
        optional_data=optional_data,
        submitted_today=count_today(root),
        # Unknown equity sizes nothing: `size_live_order` refuses rather than defaulting, so an
        # unreadable account cannot produce a position.
        equity_usdt=_f(getattr(snapshot, "available_balance", None)) or 0.0,
        verdict=verdict,
        now=now,
        order_book=order_book,
        reference_quote=reference_quote,
        # Last, after every read above: the moment the decision (and the gate, on this same
        # mapping) judges these facts at (PR2c-1).
        clock=_entry_clock(),
    )
    decision = plan_live_entry(**decision_kwargs)
    record["live_decision"] = {
        "status": decision["status"],
        "ready": decision["ready"],
        "reasons": decision["reasons"],
    }
    if not decision["ready"]:
        record["live_route_status"] = ROUTE_HELD
        # A REFUSED decision names a door that closed and belongs in the cycle's reasons. A
        # NO_ROUTE one does not: "the strategies proposed nothing this cycle" is the ordinary
        # case, and stamping it on every ledger row would bury the refusals that matter under
        # the ones that never do. It stays readable in `live_decision` either way.
        if decision["status"] != STATUS_NO_ROUTE:
            record["live_reason_codes"].extend(decision["reasons"])
        return record

    # 3a. The re-read (PR2c-2a). Between the first read and here, another writer can halt or disarm
    #     the runtime, re-register the budget or the risk limits, demote the stage or the tier, spend
    #     the day's orders or trip the bracket breaker. Those facts are read again and folded in
    #     only to narrow (`live_entry.narrow_entry_facts`); the gate below re-derives the decision on
    #     the result. The arming approval both reads name is verified against the record Thomas
    #     answered (PR2c-2b). A re-read that fails holds the entry and never the fan-out.
    strategy_id = str(plan.get("strategy_id") or "") if isinstance(plan, Mapping) else ""
    judged_limits = (((verdict or {}).get("risk_guard") or {}).get("limits")
                     if isinstance(verdict, Mapping) else None)
    try:
        fresh = reread_entry_facts(root=root, now=now, clock=decision_kwargs["clock"],
                                   control=control, judged_limits=judged_limits,
                                   venue_realized_pnl_usdt=venue_realized,
                                   venue_required=snapshot is not None)
        # The arming approval as both reads name it: a strategy re-armed in between is not the one
        # the decision was made for.
        first_approval = (live_arm_approvals or {}).get(strategy_id)
        fresh_approval = (fresh["live_arm_approvals"] or {}).get(strategy_id)
        live_arm = verify_live_arm(
            root=root, strategy_id=strategy_id, plan=plan,
            approval_id=first_approval if first_approval == fresh_approval else None,
            armed=fresh["live_arm_entries"],
        )
        if first_approval != fresh_approval and live_arm["approval_problem"] is None:
            # Armed again, or disarmed, between the reads: a promotion through the door names a
            # new approval. The arm is not verified either way; this says why (review of PR3a-2).
            live_arm["approval_problem"] = LIVE_ARM_ENTRY_CHANGED
    except Exception as exc:  # noqa: BLE001 — before the venue: a hold, never an escape
        record["live_route_status"] = ROUTE_HELD
        record["live_reason_codes"].extend(
            [PRE_ORDER_REREAD_FAILED, getattr(exc, "reason_code", type(exc).__name__)])
        return record
    # A breaker write that failed since the first read counts as it did there.
    fresh = {**fresh, "api_breaker_tripped": bool(fresh["api_breaker_tripped"])
             or _api_breaker_unwritable(adapter)}
    gate_kwargs = narrow_entry_facts(decision_kwargs, fresh)
    record["live_pre_order_reread"] = {
        "execution_stage": fresh["execution_stage"].stage,
        "runtime_active": fresh["runtime_active"],
        "budget_registered": fresh["budget_registered"],
        "submitted_today": fresh["submitted_today"],
        "daily_loss_breached": fresh["daily_loss_breached"],
        "bracket_failures_consecutive": fresh["bracket_failures_consecutive"],
        "api_breaker_tripped": fresh["api_breaker_tripped"],
        "risk_limits_problem": fresh["risk_limits_problem"],
        "venue_contract": _contract_summary(fresh["venue_contract"]),
        "live_arm": live_arm,
    }

    # 3b. The pre-order gate (PR2b): the decision re-derived from the re-read facts, the order
    #     checked against it, the approved profile checked whole — sealed into the snapshot the order
    #     will name. A refusal here sends nothing and spends nothing.
    profile = pre_order_gate.approved_profile(
        purpose=PURPOSE_AUTONOMOUS, stage=gate_kwargs["execution_stage"],
        budget={**fresh["budget"], "valid": gate_kwargs["budget_registered"]},
        risk_limits=judged_limits,
        authority={
            "kind": pre_order_gate.AUTHORITY_LIVE_ARM,
            "strategy_id": strategy_id or None,
            "candidate_id": plan.get("candidate_id") if isinstance(plan, Mapping) else None,
            **live_arm,
        },
    )
    # Not `snapshot`: that name is the account snapshot this leg read above.
    risk_snapshot = gate_live_entry(decision["intent"], bracket=decision.get("bracket"),
                                    decision_kwargs=gate_kwargs, profile=profile, now=now)
    record["live_pre_order_gate"] = {
        "approved": risk_snapshot["approved"],
        "failed_checks": risk_snapshot["failed_checks"],
        "pre_order_risk_snapshot_id": risk_snapshot["pre_order_risk_snapshot_id"],
        "risk_snapshot_sha256": risk_snapshot["risk_snapshot_sha256"],
    }
    if not risk_snapshot["approved"]:
        record["live_route_status"] = ROUTE_HELD
        record["live_reason_codes"].append(PRE_ORDER_GATE_REFUSED)
        record["live_reason_codes"].extend(risk_snapshot["failed_checks"])
        # Why the arm was not verified, where an operator reads first (review of #887).
        if live_arm.get("approval_problem"):
            record["live_reason_codes"].append(live_arm["approval_problem"])
        return record
    decision = {
        **decision,
        "intent": pre_order_gate.bind_intent(decision["intent"], risk_snapshot),
        "risk_snapshot": risk_snapshot,
    }

    # 4. The order. Governance first — a governance failure must cost nothing, so it refuses
    #    before any money moves rather than leaving an unauditable order behind.
    governance = live_governance.prepare_live_order_governance(
        decision["intent"], purpose=live_governance.PURPOSE_AUTONOMOUS, now=now, repo_root=root,
    )
    entry = live_leg.execute_live_entry(
        decision,
        adapter=adapter,
        position_store=position_store,
        counter=select_live_order_counter(now=now, root=root),
        entry_marks=select_live_entry_marks(now=now, root=root),
        snapshot_store=live_execution.select_pre_order_snapshot_store(now=now, root=root),
        governance=governance,
        gate_open=True,
        # The re-read caps: the day's slot is reserved against the limit in force now.
        limits=gate_kwargs["limits"],
        now=now,
        timeout_seconds=timeout_seconds,
    )
    record["live_opened"] = entry
    record["live_reason_codes"].extend(entry["reason_codes"])
    _note_legs_left(record, entry, symbol=symbol)
    # Before the audit report and the operator notice, both of which can fail on their own: this
    # is the state that stops the next cycle re-entering, and it is the one thing here that must
    # be durable even if everything after it goes wrong.
    _record_bracket_outcome(record, entry, symbol=symbol, now=now, root=root)
    _record_entry_outcome(record, entry, ledger=ledger)
    if entry.get("entry") is not None:
        _report(record, governance, entry["entry"], guard=decision["guard"], now=now, root=root)

    if _is_incident(entry):
        record["live_route_status"] = ROUTE_INCIDENT
        record["halt"] = True
    elif entry["status"] == live_leg.ENTRY_OPENED:
        record["live_route_status"] = ROUTE_OPENED
    else:
        record["live_route_status"] = ROUTE_HELD
    # Last, so the message describes the record as it will be stored — including any audit
    # failure recorded above, which is exactly the kind of thing the operator must hear about.
    _notify_operator(record, now=now, root=root)
    return record


def _settle_or_protect(
    record: dict[str, Any],
    position: Mapping[str, Any],
    *,
    adapter: Any,
    position_store: Any,
    ledger: Any,
    reconciliation: Mapping[str, Any],
    limits: Any,
    candle_ts: Any,
    context_timeframe: str = "",
    now: str,
    root: Path | None,
    timeout_seconds: int,
) -> None:
    """Close the loop on one open live position: settle it, protect it, time it out, or hold."""
    symbol = position_symbol(position)
    book = (reconciliation.get("books") or {}).get(symbol) or {}
    reasons = list(book.get("reasons") or [])
    # The venue holds a different position on this symbol than the book (PR2c-0 review). A close
    # is sized from the book, so it would leave the rest open — and withdrawing the closePosition
    # stop would leave that rest unprotected.
    drifted = any(reason in (DRIFT_QUANTITY_MISMATCH, DRIFT_SIDE_MISMATCH) for reason in reasons)

    if DRIFT_MISSING_AT_VENUE in reasons:
        # Something at the venue closed it. Nothing to send — read what it filled at.
        #
        # `account_feed` is the fallback for the case the bracket cannot answer: the position
        # was flattened by something other than its own legs (an operator closing at the venue),
        # so both legs EXPIRE unfilled and there is nothing to price. Resolved here rather than
        # held on the route, because it is only ever needed on this branch and the gate returns
        # an inert feed when live account reads are not authorized — which then reads as
        # `LIVE_FILL_HISTORY_UNAVAILABLE`, the honest answer, rather than as a missing argument.
        settled = live_leg.settle_venue_closed_position(
            position, adapter=adapter, position_store=position_store, ledger=ledger,
            # The fill history is a signed read of this pass too: the API error breaker counts it
            # with the adapter's own (PR2d-1).
            account_feed=recorded_like(adapter, select_account_feed(now=now, root=root)),
            now=now, timeout_seconds=timeout_seconds,
        )
        record["live_settled"] = settled
        record["live_reason_codes"].extend(settled["reason_codes"])
        _note_legs_left(record, settled, symbol=symbol)
        _record_stop_cooldown(record, position, settled, now=now, root=root)
        if _is_incident(settled):
            record["halt"] = True
        return

    legs = live_leg.read_bracket_legs(position, adapter=adapter, timeout_seconds=timeout_seconds)
    record["live_protection"] = legs
    if legs["status"] != live_leg.UNPROTECTED:
        # PROTECTED holds; PROTECTION_UNKNOWN reports and holds — closing on a failed read
        # would be acting on a guess, and the bracket is probably still doing its job.
        #
        # ...unless the position has run out of time. Ordered AFTER protection on purpose: an
        # unprotected position is the more urgent close and has its own branch below, and a
        # position whose protection could not be READ still deserves its time exit — the bracket
        # is a price rule, the max-hold is a time rule, and neither substitutes for the other.
        _time_exit_or_hold(
            record, position,
            adapter=adapter, position_store=position_store, ledger=ledger,
            limits=limits, candle_ts=candle_ts, context_timeframe=context_timeframe,
            now=now, root=root, timeout_seconds=timeout_seconds, drifted=drifted,
        )
        return

    # Rule 2, applied to a position already on the books: an unprotected live position is
    # closed, not warned about.
    closed = live_leg.execute_live_exit(
        position,
        adapter=adapter,
        position_store=position_store,
        ledger=ledger,
        gate_open=True,
        limits=limits,
        close_reason=live_leg.CLOSE_REASON_UNPROTECTED,
        now=now,
        timeout_seconds=timeout_seconds,
        # Still closed — an unprotected position is the more urgent risk — but under drift the legs
        # stay: one of them may be the stop that protects what the book does not know about.
        withdraw_legs=not drifted,
    )
    record["live_settled"] = closed
    record["live_reason_codes"].extend(closed["reason_codes"])
    _note_legs_left(record, closed, symbol=symbol)
    if closed["status"] == live_leg.EXIT_CLOSED and isinstance(closed.get("intent"), Mapping):
        # Audited AFTER the fact, unlike the entry, and the asymmetry is deliberate: refusing
        # to close because governance could not be prepared would trap a position that is
        # already unprotected. The obligation is still met — the event names the same venue
        # result — it is simply not allowed to become a reason not to close.
        try:
            governance = live_governance.prepare_live_order_governance(
                closed["intent"], purpose=live_governance.PURPOSE_AUTONOMOUS,
                now=now, repo_root=root,
            )
        except (MvpRuntimeError, ValueError) as exc:
            record["live_reason_codes"].append(AUDIT_NOT_RECORDED)
            record["live_reason_codes"].append(getattr(exc, "reason_code", type(exc).__name__))
        else:
            _report(record, governance, closed["exit"], guard=closed["close_guard"], now=now, root=root)
    if closed["status"] != live_leg.EXIT_CLOSED:
        # An unprotected position this runtime could not close is exactly the portfolio-level
        # incident: real exposure, no stop, and no way to remove it from here.
        record["halt"] = True


def _time_exit_or_hold(
    record: dict[str, Any],
    position: Mapping[str, Any],
    *,
    adapter: Any,
    position_store: Any,
    ledger: Any,
    limits: Any,
    candle_ts: Any,
    context_timeframe: str = "",
    now: str,
    root: Path | None,
    timeout_seconds: int,
    drifted: bool = False,
) -> None:
    """Advance this position's holding count and close it if the strategy's time is up.

    ``drifted``: the venue holds a different position on this symbol than the book. The time exit
    then waits (PR2c-0 review): it is sized from the book and would withdraw the stop that still
    protects the rest, and the pass halts on the drift anyway.

    The rule paper has always enforced and live did not (added 2026-07-29). Why it had to be
    added rather than left as a documented difference: the promotion evidence gating live
    trading was produced **with** the time exit in force, so a live leg without one is not
    running the strategy the evidence describes — and the direction is unfavourable, because a
    time exit mostly cuts losers, so live held them longer than the backtest ever did.

    Three properties carried over from paper deliberately, because the counter is the whole rule:

    * **the count advances even when nothing closes** — that is what makes time pass at all,
      and it is why the store write below is unconditional rather than only on the exit;
    * **one bar counts once**, deduped on the candle timestamp by ``trade_plan.advance_holding``, so
      a cycle that re-runs inside one interval cannot accelerate the exit;
    * **one context owns the clock** — the position's own timeframe
      (:func:`position_timing_context`). Paper gets this for free because its book is keyed by
      ``(symbol, timeframe)``, so exactly one context can ever reach a given position. The live
      book is keyed by symbol alone, so every timeframe a symbol routes at reaches the same
      position with a *different* bar timestamp — four distinct keys, four increments, one
      fan-out. That is the shape of the bug this guard closes: the dedup above was never wrong,
      it was answering "have I counted this bar?" for a bar the position does not trade.

    A non-owning context still settles and protects (above); it just leaves the clock alone and
    says so with ``LIVE_HOLD_NOT_TIMED_HERE``.

    ``max_holding_bars`` comes from ``trade_plan.position_max_hold``, the same authority paper uses,
    which falls back to the timeframe table for a **legacy** position — one opened before this
    record shape existed — and says so, so the fallback is attributable rather than silent.
    """
    timeframe = str(position.get("timeframe") or "")
    owner = position_timing_context(position, default_timeframe=DEFAULT_TIMING_CONTEXT)
    if str(context_timeframe) != owner:
        # Not this context's clock. Reported with the same shape the timing path writes, so a
        # reader gets the counter's current value either way and can tell WHY it did not move.
        held_now = int(position.get("holding_candles") or 0)
        max_hold_now, legacy_now = paper.position_max_hold(position, timeframe)
        record["live_holding"] = {
            "symbol": position_symbol(position), "holding_candles": held_now,
            "max_holding_bars": max_hold_now, "timeframe": timeframe or None,
            "legacy_max_hold_fallback": legacy_now,
            "timed_by": owner, "timed_here": False,
        }
        record["live_reason_codes"].append(LIVE_HOLD_NOT_TIMED_HERE)
        return

    updated = dict(position)
    paper.advance_holding(updated, candle_ts)
    max_hold, legacy = paper.position_max_hold(updated, timeframe)
    held = int(updated.get("holding_candles") or 0)

    # Persist the advanced counter before deciding anything. If the close below fails, the bar
    # that passed still passed — a counter that only advanced on successful exits would reset
    # the clock every time the venue was unreachable.
    position_store.save_position(updated)
    record["live_holding"] = {
        "symbol": position_symbol(updated), "holding_candles": held,
        "max_holding_bars": max_hold, "timeframe": timeframe or None,
        "legacy_max_hold_fallback": legacy,
        "timed_by": owner, "timed_here": True,
    }
    if legacy:
        # Named rather than inferred from a divergent R curve later: this position is being
        # judged by the timeframe table, not by the number its own backtest was built on.
        record["live_reason_codes"].append(LIVE_MAX_HOLD_FALLBACK)
    if held < max_hold:
        return
    if drifted:
        record["live_reason_codes"].append(LIVE_TIME_EXIT_HELD_ON_DRIFT)
        return

    closed = live_leg.execute_live_exit(
        updated,
        adapter=adapter,
        position_store=position_store,
        ledger=ledger,
        gate_open=True,
        limits=limits,
        close_reason=live_leg.CLOSE_REASON_TIME_EXIT,
        now=now,
        timeout_seconds=timeout_seconds,
    )
    record["live_settled"] = closed
    record["live_reason_codes"].extend(closed["reason_codes"])
    _note_legs_left(record, closed, symbol=position_symbol(updated))
    if closed["status"] == live_leg.EXIT_CLOSED and isinstance(closed.get("intent"), Mapping):
        try:
            governance = live_governance.prepare_live_order_governance(
                closed["intent"], purpose=live_governance.PURPOSE_AUTONOMOUS,
                now=now, repo_root=root,
            )
        except (MvpRuntimeError, ValueError) as exc:
            record["live_reason_codes"].append(AUDIT_NOT_RECORDED)
            record["live_reason_codes"].append(getattr(exc, "reason_code", type(exc).__name__))
        else:
            _report(record, governance, closed["exit"], guard=closed["close_guard"], now=now, root=root)
    if closed["status"] != live_leg.EXIT_CLOSED:
        # Deliberately NOT a halt, and this is the one place this module treats a failed close
        # as survivable. An unprotected position that will not close is an incident because the
        # exposure has no stop; a time-exit position that will not close still has its bracket
        # resting at the venue, so it is protected — just held longer than the strategy wanted.
        # Reported, retried next cycle (the counter is already past the threshold), not escalated.
        record["live_reason_codes"].append(LIVE_TIME_EXIT_DEFERRED)


def _note_legs_left(record: dict[str, Any], result: Mapping[str, Any], *, symbol: str) -> None:
    """Keep, by symbol, the protective legs a close left at the venue (PR2c-0), for the notice.
    A pass can settle other symbols than its own, so the symbol is the result's, never the pass's."""
    left = live_leg.legs_left_resting(result)
    if left:
        record.setdefault("live_legs_left", []).append(
            {"symbol": str(result.get("symbol") or symbol), "client_order_ids": left})


def _report(
    record: dict[str, Any],
    governance: Mapping[str, Any],
    submit_result: Mapping[str, Any],
    *,
    guard: Mapping[str, Any],
    now: str,
    root: Path | None,
) -> None:
    """The report half of EXECUTE_AND_REPORT: one audit event on the durable chain.

    Best-effort, and loudly so. The money has already moved by the time this runs, so a
    failure is reported rather than raised — but it is never swallowed: ``p5_policy_gate``
    requires ``post_action_report_and_audit``, so an unrecorded order leaves a governance
    obligation unmet, which the cycle record has to say.
    """
    try:
        event, _sha = live_governance.report_live_order(
            governance, submit_result, guard_verdict=guard, now=now, repo_root=root,
        )
        LedgerStore.default(root).append_audit_events([event])
    except (MvpRuntimeError, AuditError, ToolError) as exc:
        record["live_reason_codes"].append(AUDIT_NOT_RECORDED)
        record["live_reason_codes"].append(getattr(exc, "reason_code", type(exc).__name__))
    except Exception as exc:  # noqa: BLE001 — the order is at the venue; report, never raise
        record["live_reason_codes"].append(AUDIT_NOT_RECORDED)
        record["live_reason_codes"].append(f"UNEXPECTED_{type(exc).__name__}")


# Notification vocabulary. Recorded on the cycle record when the send fails, so a silent
# operator is distinguishable from a quiet market.
NOTIFY_FAILED = "LIVE_NOTIFY_FAILED"


def halt_advice() -> str:
    """The halt to name in a message read in a hurry — the one that will act on THIS policy.

    The soft halt is policy-gated (`control.POLICY_GATED_COMMANDS`): naming it before the policy
    grants it sends an operator in an incident to a refusal first (review of H2). So the grant is
    read when the message is built, and the text says what each verb does to open positions."""
    from ..control import CMD_HALT_TRADING, granted_emergency_controls

    if CMD_HALT_TRADING in granted_emergency_controls():
        return ("To stop new entries and keep managing positions: console_cli halt_trading --reason ... "
                "(console_cli kill stops position management too).")
    return ("To stop new entries: console_cli kill --reason ... - it also stops position management "
            "(settle, protect, time exit) until resume. The entries-only halt, halt_trading, acts once "
            "policy 1.5.1 grants it.")


def _notify_operator(record: dict[str, Any], *, now: str, root: Path | None) -> None:
    """Tell Thomas that real money moved, or that it is somewhere this runtime cannot account for.

    Sent for exactly three outcomes — a position opened, an entry that filled and had to be
    closed again because it could not be protected, and an incident. Everything else is the
    cycle doing nothing, and a channel that pings every fifteen minutes is a channel nobody
    reads by the second day; the one message that matters would arrive in a stream the operator
    has learned to skip. `crypto_report` already carries the routine picture daily.

    The middle one was missing until 2026-08-02, and it is the case that looks like nothing from
    the outside: `live_route_status` stays HELD because the position did not survive the cycle,
    so a check on status alone skipped it. Two ETHUSDT entries filled for real, both lost their
    protective stop to a venue rejection, both were force-closed — and no message was sent for
    either.

    **Best-effort, and never in the money path's way.** By the time this runs the order is at
    the venue: a send failure is recorded on the cycle record and never raised, exactly like
    the audit append above it. The destination is not caller-supplied — `notify_operator` sends
    only to the one registered private chat — and the channel is selected at fire time, so a
    revoked telegram grant degrades this to "not sent" rather than breaking the cycle.
    """
    status = record.get("live_route_status")
    reasons = [r for r in (record.get("live_reason_codes") or [])]
    # A REVERSED entry is the third outcome worth a message, and it used to fall between the two
    # above: the position opened, so nothing was merely held — and the runtime handled it, so it
    # is not an incident. `live_route_status` stays HELD, which is why keying on status alone
    # missed it.
    #
    # Measured 2026-08-02: two ETHUSDT entries filled for real, both had their protective
    # STOP_MARKET refused, both were force-closed by rule 2, and **no message was sent** for
    # either. They were found because a watch happened to be running; without it ~12 cents left
    # the account and the operator's only trace was a line in the next morning's dashboard.
    # Money moving and being reversed is not "the cycle doing nothing".
    reversed_entry = live_leg.NAKED_POSITION_CLOSED in reasons
    # And protective orders a close left at the venue (PR2c-0): a cancel that failed, or legs kept
    # on purpose while the position may still be open. A closePosition stop left resting closes
    # whatever its symbol holds when it triggers, and no book record will withdraw it. Named by
    # the symbol each close was for, which on a settle need not be this pass's own.
    legs_left = [entry for entry in record.get("live_legs_left") or () if isinstance(entry, Mapping)]
    if status not in (ROUTE_OPENED, ROUTE_INCIDENT) and not reversed_entry and not legs_left:
        return
    opened = record.get("live_opened") or {}
    position = opened.get("position") or {}
    if status == ROUTE_INCIDENT:
        head = "[LIVE INCIDENT] real money is in a state the runtime cannot account for"
    elif reversed_entry:
        head = "[LIVE] entry filled but could not be protected - position was closed again"
    elif legs_left and status != ROUTE_OPENED:
        head = "[LIVE] a position closed, but protective orders may still rest at the venue"
    else:
        head = "[LIVE] position opened and bracketed"
    lines = [head, f"symbol   : {position.get('symbol') or record.get('symbol')}"]
    if position:
        lines += [
            f"side     : {position.get('direction') or position.get('side')}",
            f"quantity : {position.get('quantity')}",
            f"entry    : {position.get('entry_price')}",
            f"stop     : {position.get('stop_loss', position.get('stop_price'))}",
            f"target   : {position.get('take_profit', position.get('target_price'))}",
        ]
    if opened:
        lines.append(f"status   : {opened.get('status')}")
    lines.append(f"at       : {now}")
    if reasons:
        lines.append("reasons  : " + ",".join(str(r) for r in reasons[:8]))
    if reversed_entry:
        # The venue's own words, which #426 started recording. Without them this message says a
        # protective order was refused and cannot say why — which is the position the operator
        # was left in on 2026-08-02, and the reason it took a day and two round trips to narrow.
        for leg in opened.get("bracket") or []:
            if isinstance(leg, Mapping) and leg.get("error"):
                lines.append(
                    f"refused  : {leg.get('order_type')} {leg.get('error')} "
                    f"- {leg.get('error_detail') or 'no detail recorded'}"
                )
        lines.append("")
        lines.append("The account is flat for this attempt; the daily order cap bounds a repeat.")
    if legs_left:
        lines.append("")
        lines.append("Protective orders a close left at the venue. A resting closePosition stop closes the")
        lines.append("next position on its symbol. Once the symbol holds no position, withdraw them by hand:")
        for entry in legs_left:
            lines.append(f"  {entry.get('symbol')}: {', '.join(str(i) for i in entry.get('client_order_ids') or ())}")
        for leg_symbol in sorted({str(entry.get("symbol")) for entry in legs_left}):
            lines.append("  docker exec thomas-scheduler python -m scripts.list_resting_orders "
                         f"--symbol {leg_symbol}")
    if status == ROUTE_INCIDENT:
        lines.append("")
        lines.append("Check the venue. " + halt_advice())
    _send_operator_text(record, lines, root=root, now=now)


def _send_operator_text(record: dict[str, Any], lines: list[str], *, root: Path | None, now: str) -> bool:
    """Send one message to the registered operator chat. Best-effort: a failure is recorded on the
    cycle record and never raised. True when the channel took the message."""
    try:
        # Imported here, not at module scope: `operator` imports back into this package, and
        # the scheduler's crypto_report seam already takes this shape for the same reason.
        from .. import operator as operator_mod

        channel = operator_mod.select_operator_channel(now=now, root=root)
        operator_mod.notify_operator(channel, "\n".join(lines), repo_root=root)
    except MvpRuntimeError as exc:
        record["live_reason_codes"].append(NOTIFY_FAILED)
        record["live_reason_codes"].append(getattr(exc, "reason_code", "UNKNOWN"))
        return False
    except Exception as exc:  # noqa: BLE001 — the order is at the venue; report, never raise
        record["live_reason_codes"].append(NOTIFY_FAILED)
        record["live_reason_codes"].append(f"UNEXPECTED_{type(exc).__name__}")
        return False
    # The inert channel takes every message and tells nobody.
    return bool(getattr(channel, "network_egress", False))


_BRACKET_FAILURE_STATUSES = frozenset({live_leg.ENTRY_NAKED_CLOSED, live_leg.ENTRY_NAKED_OPEN})


def _record_entry_outcome(record: dict[str, Any], entry: Mapping[str, Any], *, ledger: Any) -> None:
    """Persist the outcome a naked close produced. Only that branch makes one.

    `live_leg`'s entry path takes no ledger — it builds the row and this persists it, the same
    split `_record_bracket_outcome` above already uses for the other durable entry-side fact.

    A failure to append is reported and never raised, for the reason the breaker recording
    gives: the order is already at the venue and the money has already moved, so the choice is
    between a recorded failure and an unrecorded one.
    """
    outcome = entry.get("outcome")
    if not isinstance(outcome, Mapping):
        return
    try:
        ledger.append_outcome(dict(outcome))
    except Exception as exc:  # noqa: BLE001 — report, never raise; see the docstring
        record["live_reason_codes"].append(live_leg.OUTCOME_PERSIST_FAILED)
        record["live_reason_codes"].append(
            getattr(exc, "reason_code", None) or f"UNEXPECTED_{type(exc).__name__}"
        )


def _record_bracket_outcome(
    record: dict[str, Any], entry: Mapping[str, Any], *, symbol: str, now: str, root: Path | None
) -> None:
    """Tell the breaker what this entry proved about the protective path.

    Only two results say anything. A bracket that rested proves the path works, so the streak
    ends. One that did not adds to it. ``ENTRY_REFUSED`` and ``ENTRY_NOT_CONFIRMED`` say nothing
    — no bracket was attempted — and must not clear a streak they never tested, which is the
    whole reason this is a status match rather than "not a failure means success".
    """
    status = entry.get("status")
    if status != live_leg.ENTRY_OPENED and status not in _BRACKET_FAILURE_STATUSES:
        return
    try:
        breaker = select_live_bracket_breaker(now=now, root=root)
        if status == live_leg.ENTRY_OPENED:
            breaker.record_success()
        else:
            breaker.record_failure(
                symbol=symbol,
                status=str(status),
                at=now,
                reason_codes=entry.get("reason_codes") or [],
                error_detail=live_leg.bracket_error_detail(entry),
            )
    except Exception as exc:  # noqa: BLE001 — the order is at the venue; report, never raise
        record["live_reason_codes"].append(BRACKET_BREAKER_UNRECORDED)
        record["live_reason_codes"].append(
            getattr(exc, "reason_code", None) or f"UNEXPECTED_{type(exc).__name__}"
        )


# The exits that start a live cooldown. `stop_loss` is paper's rule. `venue_external_close` is the
# label a settlement falls back to when no leg answered and the fill history priced the exit —
# which a stop whose leg query failed also lands on (review of #880: reproduced, the next bar
# entered again). Its cause is unknown by definition, so it is held like a stop: a liquidation or a
# hand-close is no better a moment to re-enter. This can only refuse more than paper would.
_COOLDOWN_CLOSE_REASONS = STOP_EXIT_REASONS | {live_leg.CLOSE_REASON_VENUE_EXTERNAL}


def _note_codes(record: dict[str, Any], *codes: str) -> None:
    """Put ``codes`` on the pass's record, each once."""
    for code in codes:
        if code not in record["live_reason_codes"]:
            record["live_reason_codes"].append(code)


def _api_breaker_unwritable(adapter: Any) -> bool:
    """Whether the API error breaker cannot count this pass (PR2d-1, review of #889). A bare
    adapter (a caller that records nothing) has nothing to count with either way."""
    return isinstance(adapter, ApiErrorRecordingAdapter) and adapter.breaker_unwritable()


def _close_api_breaker_pass(
    record: dict[str, Any], recorder: ApiErrorRecordingAdapter, *, root: Path | None, now: str,
) -> None:
    """What the API error breaker saw of this pass, on the record, and the operator told of a latch
    nobody has been told of yet (PR2d-1). Never raises."""
    try:
        if recorder.tripped is not None:
            record["live_reason_codes"].append(API_BREAKER_JUST_TRIPPED)
        _tell_api_breaker(record, root=root, now=now)
    except Exception as exc:  # noqa: BLE001 — the leg's own outcome stands; report, never raise
        record["live_reason_codes"].append(f"UNEXPECTED_{type(exc).__name__}")


def _tell_api_breaker(record: dict[str, Any], *, root: Path | None, now: str) -> None:
    """Tell the operator the API error breaker latched — once a message gets through (decision 27;
    review of #889).

    The door stays shut until someone clears it by hand, and a machine that has stopped opening
    positions is exactly the state nobody discovers from a quiet channel. Whichever pass comes
    first after the latch claims the notice in the breaker's own record (so the probe and the
    leg do not both send), sends, and stamps ``told_at`` only when the channel took it. A send
    that fails — often for the very outage that tripped the breaker — is tried again on a pass
    ``API_BREAKER_NOTICE_RETRY_SECONDS`` later, not on every context of this fan-out."""
    breaker = select_live_api_breaker(now=now, root=root)
    try:
        claimed = breaker.claim_notice(at=_notice_clock())
    except MvpRuntimeError:
        return  # an unreadable or unwritable record: the door refuses on it, and says so
    if claimed is None:
        return
    if not _send_operator_text(record, api_breaker_trip_lines(claimed), root=root, now=now):
        return
    record["live_reason_codes"].append(API_BREAKER_TOLD)
    try:
        breaker.mark_told(at=_notice_clock(), tripped_at=claimed["tripped_at"])
    except MvpRuntimeError as exc:
        # Told, but not recorded as told: a later pass says it again, which is the safe way round.
        _note_codes(record, API_BREAKER_UNRECORDED, exc.reason_code)


def _notice_clock() -> str:
    """The wall clock for the API breaker's notice. Its own function so tests can set it."""
    return timeutil.utc_now_iso()


def _settle_clock() -> str:
    """The wall clock when a settlement is recorded. Its own function so tests can set it."""
    return timeutil.utc_now_iso()


def reread_entry_facts(
    *, root: Path | None, now: str, clock: str, control: Any, judged_limits: Any,
    venue_realized_pnl_usdt: float | None, venue_required: bool, with_pool: bool = True,
) -> dict[str, Any]:
    """What another writer can move between an entry door's first read and its gate, read again
    (PR2c-2a), in the shape `live_entry.narrow_entry_facts` / `narrow_guard_facts` take.

    A legacy validity window (a budget or risk limits registered before PR1r) is judged at both the
    fire's ``now`` and ``clock``, the moment the door judged: a record that expired in between no
    longer backs the order. Today's loss is judged again against the fresh limit, on the realized
    figure the door already read (the account is not read again). ``with_pool=False`` (the probe,
    which no pool entry authorizes) leaves the pool unread. The venue contract is read again as well
    (PR4b); its reader never raises, and the gate refuses on what it says. Raises on anything else it
    cannot read; the caller refuses."""
    limits, budget = resolve_live_order_limits(root, now=now)
    _, budget_at_clock = resolve_live_order_limits(root, now=clock)
    active_pool = pool.load_active_pool(root) if with_pool else None
    breaker = bracket_breaker_status(root)
    risk = live_risk_snapshot(limit_usdt=limits.daily_loss_limit_usdt, root=root, now=now,
                              venue_realized_pnl_usdt=venue_realized_pnl_usdt,
                              venue_required=venue_required)
    try:
        in_force = [resolve_risk_limits(root, now=at).as_record() for at in (now, clock)]
        risk_problem = risk_limits_moved(judged_limits, in_force)
    except MvpRuntimeError as exc:
        risk_problem = exc.reason_code
    return {
        "execution_stage": resolve_execution_stage(root, now=now),
        "runtime_active": control.load().trading_allowed,
        "limits": limits,
        "budget": budget,
        "budget_registered": bool(budget.get("valid")) and bool(budget_at_clock.get("valid"))
                             and budget.get("record_sha256") == budget_at_clock.get("record_sha256"),
        "allowed_symbols": budget.get("symbol_allowlist") or (),
        "live_routable_strategy_ids": (
            pool.live_routable_strategy_ids(active_pool) if active_pool is not None else None),
        "live_arm_approvals": pool.live_arm_approvals(active_pool) if active_pool is not None else None,
        "live_arm_entries": pool.live_arm_entries(active_pool) if active_pool is not None else None,
        "submitted_today": count_today(root),
        # PR2d-1: a breaker that latched since the first read shuts this entry too.
        "api_breaker_tripped": bool(api_breaker_status(root)["tripped"]),
        "daily_loss_breached": bool(risk["daily_loss_limit_breached"]),
        "risk": risk,
        "bracket_failures_consecutive": int(breaker["consecutive"]),
        "bracket_breaker_tripped": bool(breaker["tripped"]),
        "risk_limits_problem": risk_problem,
        # PR4b: the verification a verification-writer (the fire, `--run`) may have replaced since.
        "venue_contract": read_venue_contract(root),
    }


def _contract_summary(fact: Any) -> dict[str, Any] | None:
    """What the cycle record keeps of a venue contract read: enough to say which verification an
    entry was judged on, or why none could be read."""
    if not isinstance(fact, Mapping):
        return None
    return {key: fact.get(key) for key in ("recorded", "error", "status", "verified_at", "contract_version")}


# Why the gate cannot verify the arming approval an entry names (PR2c-2b), beside the reasons
# `promotion.live_arm_problem` gives for the record itself.
LIVE_ARM_ENTRY_CHANGED = "LIVE_ARM_ENTRY_CHANGED"
LIVE_ARM_APPROVAL_UNREADABLE = "LIVE_ARM_APPROVAL_UNREADABLE"
# The entry arms nothing whatever it names (`pool.live_arm_unsound`, review of #887).
LIVE_ARM_SPEC_NOT_ITS_RULE = "LIVE_ARM_SPEC_NOT_ITS_RULE"
# The entry carries no artifact stamp: it predates the artifact, and only a stamped entry may spend
# money (PR3a, decision 33).
LIVE_ARM_ENTRY_UNBOUND = "LIVE_ARM_ENTRY_UNBOUND"
LIVE_ARM_REARMED_OUTSIDE_THE_DOOR = "LIVE_ARM_REARMED_OUTSIDE_THE_DOOR"
_UNSOUND_ARM = {"spec": LIVE_ARM_SPEC_NOT_ITS_RULE, "unbound": LIVE_ARM_ENTRY_UNBOUND,
                "disarmed": LIVE_ARM_REARMED_OUTSIDE_THE_DOOR}


def verify_live_arm(
    *, root: Path | None, strategy_id: str, plan: Any, approval_id: str | None,
    armed: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The arming approval behind an autonomous entry, verified at the gate (PR2c-2b), as the
    approved profile's ``live_arm`` authority carries it.

    ``approval_id`` is the id both pool reads name, or None. ``armed`` is the fresh read's
    `pool.live_arm_entries`:

    - the entry must be sound (`pool.live_arm_unsound`: the spec it trades is its labelled rule, it
      was installed as an artifact (PR3a), and it was not put back in the tier by hand). Named even
      when no id was agreed, because an unsound entry is why `pool.live_arm_approvals` names none;
    - it must arm the lineage the plan was made from: its candidate, its rule and, since PR3a-2,
      its artifact (`LIVE_ARM_ENTRY_CHANGED`). The door cannot produce an artifact mismatch here:
      it installs a new artifact only under a new approval, which the two reads then disagree on
      (the route reports that with the same code). The comparison guards a pool edited outside
      the door between the two reads;
    - the approval store must hold the record Thomas answered to arm it, pairing the entry's
      artifact with its candidate (`promotion.live_arm_problem`).

    Reported, never raised for a problem: the gate refuses an unverified arm
    (`approved_profile_complete`) and the fan-out goes on."""
    arm: dict[str, Any] = {"approval_id": approval_id, "approval_fingerprint": None,
                           "approval_verified": False, "approval_problem": None}
    entry = armed.get(strategy_id) if isinstance(armed, Mapping) else None
    unsound = pool.live_arm_unsound(entry) if isinstance(entry, Mapping) else None
    if unsound is not None:
        arm["approval_problem"] = _UNSOUND_ARM[unsound]
        return arm
    if approval_id is None:
        return arm
    lineage = plan if isinstance(plan, Mapping) else {}
    if not (isinstance(entry, Mapping) and entry.get("approval_id") == approval_id
            and entry.get("candidate_id") == lineage.get("candidate_id")
            and entry.get("strategy_rule_hash") == lineage.get("strategy_rule_hash")
            and entry.get(pool.ARTIFACT_SHA256_FIELD) == lineage.get(pool.ARTIFACT_SHA256_FIELD)):
        arm["approval_problem"] = LIVE_ARM_ENTRY_CHANGED
        return arm
    try:
        approval = ApprovalStore.default(root).get(approval_id)
    except Exception:  # noqa: BLE001 — a store that cannot be read verifies nothing
        arm["approval_problem"] = LIVE_ARM_APPROVAL_UNREADABLE
        return arm
    problem = live_arm_problem(
        approval, approval_id=approval_id, candidate_id=entry.get("candidate_id"),
        strategy_rule_hash=entry.get("strategy_rule_hash"),
        strategy_artifact_sha256=entry.get(pool.ARTIFACT_SHA256_FIELD),
        promoted_at=entry.get("promoted_at"),
    )
    if problem is not None:
        arm["approval_problem"] = problem
        return arm
    arm["approval_fingerprint"] = approval.get("action_fingerprint")
    arm["approval_verified"] = True
    return arm


def _entry_clock() -> str:
    """The wall clock an entry decision is judged at (PR2c-1). Its own function so tests can set it."""
    return timeutil.utc_now_iso()


def _read_reference_quote(collector: Any, symbol: str, *, now: str, timeout_seconds: int) -> dict[str, Any]:
    """`market_data.read_reference_quote`, degraded rather than raised: a collector that fails in a
    way the reader does not type must cost this context its entry, never the fan-out."""
    try:
        return read_reference_quote(symbol, collector=collector, now=now, timeout_seconds=timeout_seconds)
    except Exception as exc:  # noqa: BLE001 — refuse at the decision, never halt the route
        return {"price": None, "close_time": None, "timeframe": "1m", "reason": PRICE_UNREADABLE,
                "error": type(exc).__name__}


def _record_stop_cooldown(
    record: dict[str, Any], position: Mapping[str, Any], settled: Mapping[str, Any], *,
    now: str, root: Path | None,
) -> None:
    """Hold the position's own context for paper's cooldown after a live stop-out (PR2a).

    The context is the position's timeframe, the one its entry was routed on; a position that names
    none (a probe, a legacy record) belongs to no context and cools nothing.

    **The anchor is the later of ``now`` and the wall clock at this call.** ``now`` alone is not
    enough: it is the fan-out's start, one value for every context, and a stop can fill after it
    and still be settled in this pass (review of #880). The wall clock here is after the venue read
    that saw the fill, so the anchor is never before the fill and the window is never shorter than
    paper's. It is one bar longer when the settlement lands in a later bar than the fill — on 15m,
    the usual case — which errs toward holding. Reported, never raised: the settlement it follows is
    already written."""
    outcome = settled.get("outcome")
    if settled.get("status") != live_leg.EXIT_CLOSED or not isinstance(outcome, Mapping):
        return
    if outcome.get("close_reason") not in _COOLDOWN_CLOSE_REASONS:
        return
    timeframe = position.get("timeframe")
    minutes = TIMEFRAMES.get(timeframe) if isinstance(timeframe, str) else None
    if not minutes:
        return
    try:
        anchor = max(str(now), _settle_clock())
        until = stop_cooldown_until(
            anchor, timeframe_minutes=minutes, bars=paper.COOLDOWN_BARS_AFTER_STOPLOSS,
        )
        select_live_entry_marks(now=now, root=root).record_stop_cooldown(
            symbol=position_symbol(position), timeframe=timeframe, until=until,
        )
    except Exception as exc:  # noqa: BLE001 — the settlement stands; report, never raise
        record["live_reason_codes"].append(STOP_COOLDOWN_UNRECORDED)
        record["live_reason_codes"].append(
            getattr(exc, "reason_code", None) or f"UNEXPECTED_{type(exc).__name__}"
        )
        return
    record["live_stop_cooldown"] = {
        "symbol": position_symbol(position), "timeframe": timeframe, "until_bar": until,
        "close_reason": outcome.get("close_reason"), "anchored_at": anchor,
    }


def _is_incident(result: Mapping[str, Any]) -> bool:
    """Does this leg result mean real money is in a state this runtime cannot account for?"""
    if result.get("status") in _INCIDENT_STATUSES:
        return True
    return any(reason in _INCIDENT_REASONS for reason in (result.get("reason_codes") or []))


# --- the emergency close (PR6c, Thomas decision 49) ------------------------------------------
#
# The operator's close of every booked live position at market under the HARD halt, spent from one
# single-use approval (`scripts/emergency_close.py`). It lives in the chokepoint because it is the
# close every runtime exit already is — `live_leg.execute_live_exit`, reduceOnly, behind the same
# gate, the same API error breaker recording and the same post-order audit — and not a second way
# to the venue. What it adds is the order of events: everything that can refuse without the venue,
# then the spend, then each position judged again just before its close.

# Refusals before the spend: nothing was sent, and the approval stays APPROVED.
EMERGENCY_CLOSE_NEEDS_HARD_HALT = "EMERGENCY_CLOSE_NEEDS_HARD_HALT"
EMERGENCY_CLOSE_HALT_CHANGED = "EMERGENCY_CLOSE_HALT_CHANGED"
EMERGENCY_CLOSE_NOTHING_BOOKED = "EMERGENCY_CLOSE_NOTHING_BOOKED"
EMERGENCY_CLOSE_GATE_CLOSED = "EMERGENCY_CLOSE_GATE_CLOSED"
EMERGENCY_CLOSE_NO_CONFIRMATION = "EMERGENCY_CLOSE_NO_CONFIRMATION"
EMERGENCY_CLOSE_ACCOUNT_UNREADABLE = "EMERGENCY_CLOSE_ACCOUNT_UNREADABLE"
# Every approved position would be skipped right now (review of #913): spending would close nothing.
EMERGENCY_CLOSE_NOTHING_CLOSABLE = "EMERGENCY_CLOSE_NOTHING_CLOSABLE"
# The book holds a record an emergency close cannot name (no id, side or quantity): no ask is made.
EMERGENCY_CLOSE_BOOK_INCOMPLETE = "EMERGENCY_CLOSE_BOOK_INCOMPLETE"
# Each approved position's result, after the spend. Only CLOSED ends the position here; every
# SKIPPED and NOT_ATTEMPTED sent nothing for it, and neither did REFUSED (the close guard) or BLOCKED
# (a typed refusal before the send).
EMERGENCY_CLOSED = "CLOSED"
EMERGENCY_NOT_CONFIRMED = "NOT_CONFIRMED"
EMERGENCY_REFUSED = "REFUSED"
EMERGENCY_BLOCKED = "BLOCKED"
# An unexpected error while closing: an order may be at the venue, so the rest are not attempted.
EMERGENCY_INCIDENT = "INCIDENT"
EMERGENCY_SKIPPED_NOT_BOOKED = "SKIPPED_NOT_BOOKED"
EMERGENCY_SKIPPED_BOOK_CHANGED = "SKIPPED_BOOK_CHANGED"
EMERGENCY_SKIPPED_CLOSED_AT_VENUE = "SKIPPED_CLOSED_AT_VENUE"
EMERGENCY_SKIPPED_VENUE_MISMATCH = "SKIPPED_VENUE_MISMATCH"
EMERGENCY_NOT_ATTEMPTED = "NOT_ATTEMPTED"
_EMERGENCY_ROW_KEYS = ("position_id", "symbol", "direction", "quantity")
# The results that leave no exposure the runtime knows of: closed here, or already gone (the book no
# longer holds it, or the venue holds nothing on its symbol). Every other result leaves some, and the
# close is INCOMPLETE.
_EMERGENCY_SETTLED = frozenset({EMERGENCY_CLOSED, EMERGENCY_SKIPPED_NOT_BOOKED, EMERGENCY_SKIPPED_CLOSED_AT_VENUE})


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def emergency_quantity_text(value: Any) -> str:
    """A booked quantity as the decimal string an emergency close binds. The action fingerprint
    forbids floats, and one spelling at the ask and at the spend is what makes the two comparable."""
    quantity = _f(value)
    return "" if quantity is None else format(Decimal(repr(quantity)), "f")


def emergency_close_rows(positions: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """The booked positions as an emergency close names them — id, symbol, side, quantity — in one
    order, so the ask and the spend describe the same set the same way."""
    return sorted(
        ({"position_id": str(p.get("position_id") or ""), "symbol": position_symbol(p),
          "direction": str(p.get("direction") or "").upper(),
          "quantity": emergency_quantity_text(p.get("quantity"))} for p in positions),
        key=lambda row: (row["symbol"], row["position_id"]),
    )


def emergency_halt_problem(state: Any, halt_ref: str | None) -> str | None:
    """Why an emergency close may not run against ``state``, or None. It runs only under the HARD
    halt with the runtime ACTIVE (decision 49), and once asked for, only under the very halt Thomas
    approved: ``halt_ref`` is the switch door's ``stop_ref`` of it, which any control write moves."""
    from ..switch_bridge import stop_ref  # local: the door module is not part of the live stack

    if state.mode != ACTIVE or state.halt_level != HALT_HARD:
        halt = f"a {state.halt_level} halt" if state.halt_level else "no halt"
        return (f"the runtime is {state.mode} with {halt}; an emergency close runs only under the HARD "
                "halt with the runtime ACTIVE (halt_trading hard)")
    if halt_ref is not None and stop_ref(state) != halt_ref:
        return (f"the HARD halt in effect ({stop_ref(state)}) is not the one this close was approved "
                f"under ({halt_ref}): the control state was written since the ask")
    return None


def emergency_halt_summary(state: Any) -> str:
    """The halt an emergency close is bound to, for the human who signs it: who placed it, when, why."""
    return (f"the {state.halt_level} halt placed by {state.updated_by} at "
            f"{state.updated_at or 'an unrecorded time'}, stated reason: {state.reason}")


def emergency_book_problem(rows: Sequence[Mapping[str, str]]) -> str | None:
    """Why the book cannot be named in an ask, or None: every row needs its id, a side and a quantity
    (review of #913 — one such record used to surface as the builder's generic refusal)."""
    bad = [row.get("position_id") or row.get("symbol") or "?" for row in rows
           if not (row.get("position_id") and row.get("symbol") and row.get("quantity")
                   and row.get("direction") in ("LONG", "SHORT"))]
    if bad:
        return (f"the book holds a record with no id, side or quantity ({', '.join(sorted(bad))}); "
                "close it at the venue, or repair the record, then ask again")
    return None


def emergency_close_content(
    *, root: Path | None, requested_by: str, reason: str, control_store: ControlStore | None = None,
) -> dict[str, Any]:
    """What an emergency-close ask binds (``permission.build_emergency_close_permission_decision``):
    the HARD halt in effect and every position the book holds. Reads the control state and the book;
    opens no socket and changes nothing."""
    from ..switch_bridge import stop_ref

    control = control_store if control_store is not None else ControlStore.default(root)
    state = control.load()
    problem = emergency_halt_problem(state, None)
    if problem is not None:
        raise ToolError(EMERGENCY_CLOSE_NEEDS_HARD_HALT, problem)
    rows = emergency_close_rows(list_open_live_positions(root))
    if not rows:
        raise ToolError(EMERGENCY_CLOSE_NOTHING_BOOKED,
                        "no live position is booked on this machine, so there is nothing to close")
    incomplete = emergency_book_problem(rows)
    if incomplete is not None:
        raise ToolError(EMERGENCY_CLOSE_BOOK_INCOMPLETE, incomplete)
    return {"halt_ref": stop_ref(state), "halt_summary": emergency_halt_summary(state), "positions": rows,
            "requested_by": requested_by, "reason": reason}


def run_emergency_close(
    approved: Sequence[Mapping[str, Any]],
    *,
    halt_ref: str,
    spend: Callable[[], Any],
    now: str,
    root: Path | None = None,
    control_store: ControlStore | None = None,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    """Close the approved positions at market, reduceOnly: the effect of a spent emergency-close grant.

    **Everything that can refuse without sending refuses before ``spend``,** so a refusal there leaves
    the approval APPROVED and sends nothing:
    - the HARD halt it was approved under;
    - the live gate (a dry-run adapter would spend the grant and close nothing);
    - the confirmation phrase the close guard needs;
    - at least one approved position still booked;
    - a readable account;
    - and at least one approved position that would be closed now (review of #913: a grant spent on
      a set that is all skips closes nothing).
    ``spend`` is the caller's single-use compare-and-set; it runs once, and a spend that loses raises
    before anything is sent.

    **The account is read once, just before the spend.** After the spend each position is judged
    again just before its close, against that read and a fresh read of the halt and the book, and
    skipped, never resized, when anything moved: the halt (the rest are then not attempted), the
    book (the position is gone, or holds another side or quantity), or the venue as read (it had
    closed the position, or held a different one). A position the venue holds that the book does not
    is never touched (decision 49). Between the read and a close the venue can still move — a bracket
    fills, or the scheduler, which keeps managing positions under the HARD halt, closes first. Then
    the reduceOnly close meets a flat position and the venue rejects it (NOT_CONFIRMED), or the book
    has already been cleared (SKIPPED_NOT_BOOKED). Nothing can be opened either way; under the HARD
    halt the adapter refuses anything that is not reduceOnly (PR6b).

    **Every order that reached the venue is audited,** closed or not: the event reports what the venue
    answered, a partial fill or an unanswered status query included. The report names each order
    (client and venue ids, reconcile status, what filled). Its ``status`` is COMPLETE when no approved
    position is left open as far as the runtime knows (each closed here or already gone), else
    INCOMPLETE. ``untracked_at_venue`` names what the venue holds that the book does not, and
    ``booked_not_in_grant`` what the book holds that the grant does not name: neither is closed here,
    and the operator flattening in an emergency must know both are there.

    Returns the report. Raises only before the spend, or when the spend itself refuses."""
    assert_not_foreign_root_run(root)
    control = control_store if control_store is not None else ControlStore.default(root)
    problem = emergency_halt_problem(control.load(), halt_ref)
    if problem is not None:
        raise ToolError(EMERGENCY_CLOSE_HALT_CHANGED, f"{problem}; nothing was spent")
    adapter, gate_reason = select_live_gate(now=now, root=root)
    if adapter is None:
        raise ToolError(EMERGENCY_CLOSE_GATE_CLOSED,
                        f"live routing is not open in this process ({gate_reason}), so no close could be "
                        "sent; nothing was spent")
    limits, _budget = resolve_live_order_limits(root, now=now)
    if not limits.confirmation_present():
        raise ToolError(EMERGENCY_CLOSE_NO_CONFIRMATION,
                        "the live confirmation phrase is not set in this process, and the close guard "
                        "refuses every close without it; nothing was spent")
    wanted = {str(row.get("position_id")) for row in approved}
    if not any(str(p.get("position_id")) in wanted for p in list_open_live_positions(root)):
        raise ToolError(EMERGENCY_CLOSE_NOTHING_BOOKED,
                        "none of the approved positions is booked any more; nothing was spent")
    record: dict[str, Any] = {
        "live_route_version": LIVE_ROUTE_VERSION,
        "status": None,
        "halt_ref": halt_ref,
        "positions": [],
        "untracked_at_venue": [],
        "booked_not_in_grant": [],
        "live_reason_codes": [],
        "created_at": now,
    }
    recorder = ApiErrorRecordingAdapter(
        adapter, select_live_api_breaker(now=now, root=root),
        on_unrecorded=lambda code: _note_codes(record, API_BREAKER_UNRECORDED, code),
    )
    try:
        snapshot, account_use = read_account(timeout_seconds=timeout_seconds, root=root)
        recorder.record_account(readable=snapshot is not None, reason_code=account_use.get("error_reason_code"))
        if snapshot is None:
            raise ToolError(EMERGENCY_CLOSE_ACCOUNT_UNREADABLE,
                            f"the account could not be read ({account_use.get('degraded_reason_code')}), so "
                            "no position can be checked against the venue; nothing was spent")
        booked = list_open_live_positions(root)
        books = reconcile_positions(booked, snapshot, now=now).get("books") or {}
        skips = [_emergency_skip(row, booked, books) for row in approved]
        if all(skip is not None for skip in skips):
            why = "; ".join(f"{row.get('symbol')} {skip[0]}" for row, skip in zip(approved, skips))
            raise ToolError(EMERGENCY_CLOSE_NOTHING_CLOSABLE,
                            f"no approved position would be closed now ({why}); nothing was spent")
        record["untracked_at_venue"] = _untracked_at_venue(books, snapshot)
        record["booked_not_in_grant"] = [row for row in emergency_close_rows(booked)
                                         if row["position_id"] not in wanted]
        spend()
        _close_emergency_positions(
            record, approved, adapter=recorder, snapshot=snapshot, limits=limits, control=control,
            halt_ref=halt_ref, now=now, root=root, timeout_seconds=timeout_seconds,
        )
    finally:
        _close_api_breaker_pass(record, recorder, root=root, now=now)
    settled = sum(1 for row in record["positions"] if row["status"] in _EMERGENCY_SETTLED)
    record["status"] = "COMPLETE" if settled == len(record["positions"]) else "INCOMPLETE"
    return record


def _emergency_skip(row: Mapping[str, Any], booked: Sequence[Mapping[str, Any]],
                    books: Mapping[str, Any]) -> tuple[str, str, list[str]] | None:
    """Why this approved position is not closed now — ``(status, detail, reason codes)`` — or None when
    it may be. The same judgement before the spend (is anything closable?) and after it (per position)."""
    position = next((p for p in booked if str(p.get("position_id")) == str(row.get("position_id"))), None)
    if position is None:
        return (EMERGENCY_SKIPPED_NOT_BOOKED,
                "no longer booked: closed since the ask (the scheduler, or the venue's bracket)", [])
    now_row = emergency_close_rows([position])[0]
    if now_row != {key: str(row.get(key) or "") for key in _EMERGENCY_ROW_KEYS}:
        return (EMERGENCY_SKIPPED_BOOK_CHANGED,
                f"the book now holds {now_row['symbol']} {now_row['direction']} {now_row['quantity']}; "
                "refused, not resized", [])
    book = _as_mapping(books.get(now_row["symbol"]))
    reasons = [str(r) for r in (book.get("reasons") or ())]
    if DRIFT_MISSING_AT_VENUE in reasons:
        return (EMERGENCY_SKIPPED_CLOSED_AT_VENUE,
                "the venue held no position on this symbol: it closed there, and the scheduler settles it", [])
    if book.get("status") != RECONCILED:
        return (EMERGENCY_SKIPPED_VENUE_MISMATCH,
                f"book and venue disagree ({', '.join(reasons) or book.get('status')}; the venue holds "
                f"{book.get('venue_quantity')}); refused, not resized", reasons)
    return None


def _untracked_at_venue(books: Mapping[str, Any], snapshot: Any) -> list[dict[str, Any]]:
    """What the venue holds on a symbol the book does not, as read before the spend (after it, the
    closes would read as untracked too). Reported, never closed: decision 49 closes booked positions
    only."""
    sides = {p.symbol: p.side for p in getattr(snapshot, "positions", ()) if getattr(p, "symbol", None)}
    return [{"symbol": symbol, "side": sides.get(symbol), "venue_quantity": _as_mapping(book).get("venue_quantity")}
            for symbol, book in sorted(books.items())
            if DRIFT_UNTRACKED_AT_VENUE in (_as_mapping(book).get("reasons") or ())]


def _close_emergency_positions(
    record: dict[str, Any],
    approved: Sequence[Mapping[str, Any]],
    *,
    adapter: Any,
    snapshot: Any,
    limits: Any,
    control: ControlStore,
    halt_ref: str,
    now: str,
    root: Path | None,
    timeout_seconds: int,
) -> None:
    """Each approved position in turn, judged again just before its close (see
    :func:`run_emergency_close`). Never raises: every position gets a result on the record."""
    position_store = select_live_position_store(now=now, root=root)
    ledger = select_live_ledger(now=now, root=root)
    stopped: str | None = None
    for wanted in approved:
        row: dict[str, Any] = {key: str(wanted.get(key) or "") for key in _EMERGENCY_ROW_KEYS}
        row.update(status=None, reason_codes=[], detail=None, outcome_id=None, order=None)
        record["positions"].append(row)
        if stopped is not None:
            row.update(status=EMERGENCY_NOT_ATTEMPTED, detail=stopped)
            continue
        try:
            problem = emergency_halt_problem(control.load(), halt_ref)
            if problem is not None:
                stopped = problem
                row.update(status=EMERGENCY_NOT_ATTEMPTED, detail=problem)
                _note_codes(record, EMERGENCY_CLOSE_HALT_CHANGED)
                continue
            booked = list_open_live_positions(root)
            skip = _emergency_skip(row, booked, reconcile_positions(booked, snapshot, now=now).get("books") or {})
            if skip is not None:
                status, detail, codes = skip
                row["reason_codes"].extend(codes)
                row.update(status=status, detail=detail)
                continue
            position = next(p for p in booked if str(p.get("position_id")) == row["position_id"])
            closed = live_leg.execute_live_exit(
                position, adapter=adapter, position_store=position_store, ledger=ledger, gate_open=True,
                limits=limits, close_reason=live_leg.CLOSE_REASON_EMERGENCY, now=now,
                timeout_seconds=timeout_seconds,
            )
            row["reason_codes"].extend(closed["reason_codes"])
            row["status"] = {live_leg.EXIT_CLOSED: EMERGENCY_CLOSED,
                             live_leg.EXIT_NOT_CONFIRMED: EMERGENCY_NOT_CONFIRMED}.get(closed["status"],
                                                                                    EMERGENCY_REFUSED)
            if row["status"] == EMERGENCY_REFUSED:
                row["detail"] = "; ".join((closed.get("close_guard") or {}).get("blocks") or ()) or None
            outcome = closed.get("outcome")
            row["outcome_id"] = outcome.get("outcome_id") if isinstance(outcome, Mapping) else None
            sent = closed.get("exit")
            if isinstance(sent, Mapping):
                # The order reached the venue: say which one and what the venue answered, and audit it
                # whatever the answer was (review of #913 — a partial fill left no trace but a line).
                row["order"] = {
                    "client_order_id": sent.get("client_order_id"),
                    "exchange_order_id": sent.get("exchange_order_id"),
                    "reconcile_status": sent.get("reconcile_status"),
                    "mismatches": list(sent.get("mismatches") or []),
                    "executed_qty": _as_mapping(sent.get("fill")).get("executed_qty"),
                }
                if isinstance(closed.get("intent"), Mapping):
                    _audit_emergency_close(record, closed, now=now, root=root)
        except MvpRuntimeError as exc:
            # A typed refusal before or between venue calls (an unreadable book, a refused send):
            # reported on this position, and the next one is still judged on its own.
            row["reason_codes"].append(exc.reason_code)
            row.update(status=EMERGENCY_BLOCKED, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 — an order may be at the venue: report it, and stop
            row["reason_codes"].append(f"UNEXPECTED_{type(exc).__name__}")
            row.update(status=EMERGENCY_INCIDENT, detail=str(exc)[:300])
            stopped = "not attempted after an unexpected error on an earlier position"


def _audit_emergency_close(record: dict[str, Any], closed: Mapping[str, Any], *, now: str,
                           root: Path | None) -> None:
    """Audited after the fact, as every runtime close is (`_settle_or_protect`): refusing to close
    because governance could not be prepared would keep open the position the operator asked to
    close. Never raises (review of #913): a failure here is on the record, never a reason the close
    did not happen, and never a reason the next position is not attempted."""
    try:
        governance = live_governance.prepare_live_order_governance(
            closed["intent"], purpose=live_governance.PURPOSE_EMERGENCY_CLOSE, now=now, repo_root=root,
        )
    except Exception as exc:  # noqa: BLE001 — the order is at the venue; report, never raise
        _note_codes(record, AUDIT_NOT_RECORDED, getattr(exc, "reason_code", f"UNEXPECTED_{type(exc).__name__}"))
    else:
        _report(record, governance, closed["exit"], guard=closed["close_guard"], now=now, root=root)


def live_route_status_line(record: Mapping[str, Any]) -> str:
    """One ASCII line for the cycle's status (Windows consoles are cp949)."""
    # `halt` on this module's own record, `live_halt` once the cycle record has folded it in —
    # read both rather than making one caller translate, so a status line can never silently
    # drop the one word that says the fan-out stopped.
    parts = [f"live={record.get('live_route_status')}"]
    if record.get("halt") or record.get("live_halt"):
        parts.append("HALT")
    opened = record.get("live_opened")
    if isinstance(opened, Mapping):
        parts.append(f"opened={opened.get('status')}")
    settled = record.get("live_settled")
    if isinstance(settled, Mapping):
        outcome = settled.get("outcome") or {}
        parts.append(f"settled={settled.get('status')}({outcome.get('result_R')}R)")
    return " ".join(parts)


__all__ = [
    "ACCOUNT_UNREADABLE",
    "AUDIT_NOT_RECORDED",
    "BOOK_DRIFT",
    "DEFAULT_TIMING_CONTEXT",
    "EMERGENCY_CLOSE_ACCOUNT_UNREADABLE",
    "EMERGENCY_CLOSE_GATE_CLOSED",
    "EMERGENCY_CLOSE_HALT_CHANGED",
    "EMERGENCY_CLOSE_NEEDS_HARD_HALT",
    "EMERGENCY_CLOSE_NOTHING_BOOKED",
    "EMERGENCY_CLOSE_BOOK_INCOMPLETE",
    "EMERGENCY_CLOSE_NOTHING_CLOSABLE",
    "EMERGENCY_CLOSE_NO_CONFIRMATION",
    "EMERGENCY_CLOSED",
    "LIVE_HOLD_NOT_TIMED_HERE",
    "LIVE_ROUTE_VERSION",
    "ROUTE_BLOCKED",
    "ROUTE_DISABLED",
    "ROUTE_HELD",
    "ROUTE_INCIDENT",
    "ROUTE_OPENED",
    "ROUTE_SETTLED",
    "ROUTING_DISABLED",
    "ROUTING_PRECONDITION",
    "emergency_close_content",
    "emergency_book_problem",
    "emergency_close_rows",
    "emergency_halt_problem",
    "emergency_halt_summary",
    "emergency_quantity_text",
    "live_position_contexts",
    "live_route_status_line",
    "position_timing_context",
    "run_emergency_close",
    "run_live_leg",
    "select_live_gate",
]
