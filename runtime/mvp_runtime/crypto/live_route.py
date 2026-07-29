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
3. otherwise → hold.

There is **no time-based exit** (paper's ``max_hold``). A live position record carries no
holding count and no timeframe, so a max-hold rule here would be inventing state rather than
reading it; adding it is LP5.1's record shape, not this increment's routing. Stated rather than
left to be discovered: a live trade ends at its stop or its target, where a paper trade of the
same strategy may also end on time, and the two R populations differ by exactly that.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..audit import AuditError
from ..coerce import as_optional_float as _f
from ..control import ControlStore
from ..errors import MvpRuntimeError, ToolError
from ..state_guard import assert_not_foreign_root_run
from ..store import LedgerStore
from . import live_execution, live_governance, live_leg, live_promotion
from .account import read_account
from .live_entry import STATUS_NO_ROUTE, plan_live_entry
from .live_filters import read_symbol_filters
from .live_order import count_today, resolve_live_order_limits, select_live_order_counter
from .live_pnl import live_risk_snapshot, select_live_ledger, venue_daily_realized_net
from .live_position import (
    DRIFT,
    DRIFT_MISSING_AT_VENUE,
    list_open_live_positions,
    position_symbol,
    reconcile_positions,
    select_live_position_store,
)
from .paper import build_entry_plan

LIVE_ROUTE_VERSION = "live_route.v0.1"

# What this cycle's live leg did. One value, reported on the cycle record.
ROUTE_DISABLED = "DISABLED"    # live trading is off here; nothing was read and nothing sent
ROUTE_BLOCKED = "BLOCKED"      # gated open, but a precondition refused before any venue action
ROUTE_HELD = "HELD"            # ran end to end; no entry and no exit was due
ROUTE_SETTLED = "SETTLED"      # a position closed and its outcome was recorded
ROUTE_OPENED = "OPENED"        # a position was opened and bracketed
ROUTE_INCIDENT = "INCIDENT"    # real money is in a state this runtime cannot account for

# Reason codes.
ROUTING_DISABLED = "LIVE_ROUTING_DISABLED"
ROUTING_PRECONDITION = "LIVE_ROUTING_PRECONDITION_FAILED"
ACCOUNT_UNREADABLE = "LIVE_ROUTING_ACCOUNT_UNREADABLE"
BOOK_DRIFT = "LIVE_ROUTING_BOOK_DRIFT"
AUDIT_NOT_RECORDED = "LIVE_ORDER_AUDIT_NOT_RECORDED"
CANARY_HISTORY = "LIVE_ROUTING_CANARY_HISTORY"

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


def live_position_symbols(root: Path | None = None) -> list[str]:
    """Every symbol holding an open live position. A read; reaches no order path.

    ``cycle.pool_cycle_contexts`` needs this so a live position always gets a cycle that can
    settle it — a strategy demoted out of the routable set would otherwise leave its *real*
    position with no visitor, which is the live twin of the symbol-starved router. Exposed
    here rather than imported from ``live_position`` directly so the cycle keeps exactly one
    door into this stack.
    """
    try:
        return sorted({position_symbol(p) for p in list_open_live_positions(root)})
    except MvpRuntimeError:
        return []  # a per-context cycle re-reads and records its own fail-closed reason


# --- the leg -------------------------------------------------------------------------

def run_live_leg(
    *,
    route: Mapping[str, Any] | None,
    feature_row: Mapping[str, Any],
    verdict: Mapping[str, Any],
    symbol: str,
    collector: Any,
    now: str,
    root: Path | None = None,
    control_store: ControlStore | None = None,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    """One cycle's live leg: reconcile, settle, protect, maybe open. Returns a record.

    Never raises. A cycle that cannot complete its live leg still has to report what it did
    and did not do — a traceback here would be indistinguishable from "no live activity", and
    that is the one thing this record exists to make legible.

    ``route`` is the paper step's own routing result, shared rather than re-evaluated.
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
    }

    adapter, gate_reason = select_live_gate(now=now, root=root)
    if adapter is None:
        record["live_reason_codes"].append(gate_reason or ROUTING_DISABLED)
        return record

    try:
        return _run_gated_live_leg(
            record,
            adapter=adapter,
            route=route,
            feature_row=feature_row,
            verdict=verdict,
            symbol=symbol,
            collector=collector,
            now=now,
            root=root,
            control_store=control_store,
            timeout_seconds=timeout_seconds,
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


def _run_gated_live_leg(
    record: dict[str, Any],
    *,
    adapter: Any,
    route: Mapping[str, Any] | None,
    feature_row: Mapping[str, Any],
    verdict: Mapping[str, Any],
    symbol: str,
    collector: Any,
    now: str,
    root: Path | None,
    control_store: ControlStore | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    """The leg proper, once the gate is open. Split out so every exit path above is one
    ``except`` rather than a ``try`` wrapped around two hundred lines."""
    # 0. A host-side root run would leave this cycle's book, ledger and audit rows owned by a
    #    uid the services cannot write again. Before the venue, for the canary path's reason:
    #    afterwards the only options are a broken registry or a real position with no record.
    assert_not_foreign_root_run(root)

    # 1. The facts, each read once and shared by every door below — so the guard, the sizing
    #    and the record cannot disagree about what was true this cycle.
    limits, budget = resolve_live_order_limits(root, now=now)
    # `.default(root)` rather than `ControlStore(root)`: the constructor takes a Path, so the
    # bare form crashes on the `root=None` every ordinary run passes.
    control = control_store if control_store is not None else ControlStore.default(root)
    runtime_active = control.load().execution_allowed

    snapshot, account_use = read_account(timeout_seconds=timeout_seconds, root=root)
    if snapshot is None:
        record["live_reason_codes"].append(ACCOUNT_UNREADABLE)
        record["account_degraded_reason_code"] = account_use.get("degraded_reason_code")

    local_positions = list_open_live_positions(root)
    reconciliation = reconcile_positions(local_positions, snapshot, now=now)
    record["live_reconcile_status"] = reconciliation["status"]

    position_store = select_live_position_store(now=now, root=root)
    ledger = select_live_ledger(now=now, root=root)

    # 2. Settle and protect BEFORE anything else. Closing is risk-reducing and is never gated
    #    on reconciliation, the verdict, or the kill switch — a halt that traps an open
    #    position is worse than what the halt prevents.
    open_here = [p for p in local_positions if position_symbol(p) == symbol]
    for position in open_here:
        _settle_or_protect(
            record, position,
            adapter=adapter, position_store=position_store, ledger=ledger,
            reconciliation=reconciliation, limits=limits, now=now, root=root,
            timeout_seconds=timeout_seconds,
        )

    # A book that still disagrees with the venue AFTER settlement is the dangerous kind: the
    # normal bracket-closed drift resolved itself just above, so what remains is a position
    # this runtime cannot account for. Entries are already refused per symbol; the halt is
    # what stops the *other* contexts from opening under the same uncertainty.
    if reconciliation["status"] == DRIFT and record["live_settled"] is None:
        record["live_reason_codes"].append(BOOK_DRIFT)
        record["halt"] = True

    if record["halt"]:
        record["live_route_status"] = ROUTE_INCIDENT
        return record

    # 3. The entry decision. Everything above this line can run with no route at all.
    if record["live_settled"] is not None:
        # A position closed this cycle. Do not re-enter on the same pass: the book was cleared
        # a few lines ago and the venue read predates it, so every exposure figure the guard
        # would judge is now stale. The next cycle sees a consistent picture.
        record["live_route_status"] = ROUTE_SETTLED
        return record

    plan = build_entry_plan(route, feature_row, now=now) if isinstance(route, Mapping) else None

    clean_canaries, canary_error = live_promotion.clean_canary_order_count(root)
    if canary_error:
        record["live_reason_codes"].append(CANARY_HISTORY)
        record["live_reason_codes"].append(canary_error)

    filters, filters_reason = read_symbol_filters(collector, symbol, timeout_seconds=timeout_seconds)

    # The breaker reads the VENUE's realized figure, not the local ledger: on a machine whose
    # live positions close at the venue the local ledger lags a cycle, and a loss breaker that
    # measures late is a breaker that does not bound today (#247).
    risk = live_risk_snapshot(
        limit_usdt=limits.daily_loss_limit_usdt, root=root, now=now,
        venue_realized_pnl_usdt=(
            venue_daily_realized_net(snapshot.realized_windows) if snapshot is not None else None
        ),
    )

    decision = plan_live_entry(
        plan,
        symbol=symbol,
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
        budget_registered=bool(budget.get("valid")),
        gate_open=True,  # the adapter above IS the grant; nothing else selects a capable one
        runtime_active=runtime_active,
        daily_loss_breached=bool(risk["daily_loss_limit_breached"]),
        clean_canary_orders=clean_canaries,
        submitted_today=count_today(root),
        # Unknown equity sizes nothing: `size_live_order` refuses rather than defaulting, so an
        # unreadable account cannot produce a position.
        equity_usdt=_f(getattr(snapshot, "available_balance", None)) or 0.0,
        verdict=verdict,
        now=now,
    )
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
        governance=governance,
        gate_open=True,
        limits=limits,
        now=now,
        timeout_seconds=timeout_seconds,
    )
    record["live_opened"] = entry
    record["live_reason_codes"].extend(entry["reason_codes"])
    if entry.get("entry") is not None:
        _report(record, governance, entry["entry"], guard=decision["guard"], now=now, root=root)

    if _is_incident(entry):
        record["live_route_status"] = ROUTE_INCIDENT
        record["halt"] = True
    elif entry["status"] == live_leg.ENTRY_OPENED:
        record["live_route_status"] = ROUTE_OPENED
    else:
        record["live_route_status"] = ROUTE_HELD
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
    now: str,
    root: Path | None,
    timeout_seconds: int,
) -> None:
    """Close the loop on one open live position: settle it, protect it, or leave it."""
    symbol = position_symbol(position)
    book = (reconciliation.get("books") or {}).get(symbol) or {}
    reasons = list(book.get("reasons") or [])

    if DRIFT_MISSING_AT_VENUE in reasons:
        # The venue's own bracket closed it. Nothing to send — read what it filled at.
        settled = live_leg.settle_venue_closed_position(
            position, adapter=adapter, position_store=position_store, ledger=ledger,
            now=now, timeout_seconds=timeout_seconds,
        )
        record["live_settled"] = settled
        record["live_reason_codes"].extend(settled["reason_codes"])
        if _is_incident(settled):
            record["halt"] = True
        return

    legs = live_leg.read_bracket_legs(position, adapter=adapter, timeout_seconds=timeout_seconds)
    record["live_protection"] = legs
    if legs["status"] != live_leg.UNPROTECTED:
        # PROTECTED holds; PROTECTION_UNKNOWN reports and holds — closing on a failed read
        # would be acting on a guess, and the bracket is probably still doing its job.
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
    )
    record["live_settled"] = closed
    record["live_reason_codes"].extend(closed["reason_codes"])
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


def _is_incident(result: Mapping[str, Any]) -> bool:
    """Does this leg result mean real money is in a state this runtime cannot account for?"""
    if result.get("status") in _INCIDENT_STATUSES:
        return True
    return any(reason in _INCIDENT_REASONS for reason in (result.get("reason_codes") or []))


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
    "CANARY_HISTORY",
    "LIVE_ROUTE_VERSION",
    "ROUTE_BLOCKED",
    "ROUTE_DISABLED",
    "ROUTE_HELD",
    "ROUTE_INCIDENT",
    "ROUTE_OPENED",
    "ROUTE_SETTLED",
    "ROUTING_DISABLED",
    "ROUTING_PRECONDITION",
    "live_position_symbols",
    "live_route_status_line",
    "run_live_leg",
    "select_live_gate",
]
