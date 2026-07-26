"""LP5.3 — the executing leg. The wire between a decision that exists and a sender that exists.

``live_entry.plan_live_entry`` decides; ``live_execution.submit_and_reconcile`` sends. This
module is the short, consequential piece between them: it takes a ``READY`` decision, opens a
real position with a venue-side protective bracket, and later closes it and records the
realized result. Design: ``docs/runtime-contracts/LP5_3_LIVE_LEG_DESIGN_V0.1.md``.

**The adapter is injected, never selected here.** Every branch below — an unconfirmed entry, a
bracket that will not place, the naked-position close, the cancel-on-close, the realized P&L
from actual fills — is therefore exercised in tests with a fake adapter and **zero network**.
It also means this module cannot reach the venue on its own: a caller must hand it a capable
adapter, which only ``live_execution.select_order_adapter`` can build, and only behind the
``live_trading`` grant.

**Nothing autonomous calls this yet.** Wiring it into the crypto cycle is the separate step the
design record sequences last, because that is the line which makes an autonomous live order
reachable. Until then the only caller is a test or a deliberate operator script.

The three rules this leg owes, each implemented as a branch you can point at:

1. **Open only on ``RECONCILED``.** A submit that came back ``MISMATCH``, ``NOT_FOUND`` or
   ``UNRECONCILABLE`` creates no local position. The venue is the truth; an unconfirmed entry
   is not a position, it is an incident to surface.
2. **A naked position is closed, not warned about.** If the entry fills but a bracket leg
   cannot be placed, the position is closed immediately (reduceOnly MARKET). An unprotected
   live position is exactly what the bracket exists to prevent, so the fail-closed direction
   is *out*, not in.
3. **Cancel the surviving leg on close.** The venue documents no auto-cancel for conditional
   orders when a position closes, so a leftover leg is withdrawn explicitly. A stale
   ``closePosition`` leg cannot open anything — it can only reduce — so a failed cancel is
   reported, never treated as fatal.

**Both bracket legs are ``closePosition`` conditional orders**, not sized reduceOnly ones. The
venue treats ``closePosition`` as Close-All and forbids it from carrying a quantity, so the
bracket protects whatever is actually open even if the fill drifted from the intent, and the
two legs cannot reserve quantity against each other.

**A known limitation, stated rather than hidden:** realized P&L here is computed from the
venue's actual fill figures and is therefore **gross of fees and funding**. The honest
fee-inclusive figure is the account snapshot's ``realized_windows`` (which already buckets
commission and funding), but that is per-window, not per-position, so attributing it to one
trade is not sound while more than one position can be open. Every outcome records
``fees_included: False`` and both legs' quote amounts so a later reconciliation can correct it.
The direction of the error matters and is named: gross P&L understates a loss by roughly the
taker fee on both legs, which moves the daily-loss breaker the *permissive* way.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..errors import ToolError
from .live_execution import (
    ORDER_TYPE_MARKET,
    ORDER_TYPE_STOP_MARKET,
    ORDER_TYPE_TAKE_PROFIT_MARKET,
    submit_and_reconcile,
)
from .live_order import evaluate_live_close_guard, make_client_order_id, make_idempotency_key
from .live_pnl import build_live_outcome_record
from .live_position import build_live_position
from .live_promotion import RECONCILED

LIVE_LEG_VERSION = "live_leg.v0.1"

# Entry outcomes.
ENTRY_REFUSED = "ENTRY_REFUSED"                # the decision was not READY; nothing was sent
ENTRY_NOT_CONFIRMED = "ENTRY_NOT_CONFIRMED"    # submitted, but the venue did not confirm a fill
ENTRY_NAKED_CLOSED = "ENTRY_NAKED_CLOSED"      # filled, bracket failed, position closed again
ENTRY_NAKED_OPEN = "ENTRY_NAKED_OPEN"          # filled, bracket failed, AND the close failed
ENTRY_OPENED = "ENTRY_OPENED"                  # filled, bracketed, booked

# Exit outcomes.
EXIT_REFUSED = "EXIT_REFUSED"                  # the close guard refused; nothing was sent
EXIT_NOT_CONFIRMED = "EXIT_NOT_CONFIRMED"      # submitted, unconfirmed — the book stays OPEN
EXIT_CLOSED = "EXIT_CLOSED"                    # closed, brackets cancelled, outcome recorded

# Reason codes.
NOT_READY = "LIVE_ENTRY_NOT_READY"
NO_GOVERNANCE = "LIVE_ORDER_NO_GOVERNANCE_RECORD"
ENTRY_UNCONFIRMED = "LIVE_ENTRY_UNCONFIRMED"
BRACKET_FAILED = "LIVE_BRACKET_FAILED"
NAKED_POSITION_CLOSED = "LIVE_NAKED_POSITION_CLOSED"
NAKED_CLOSE_FAILED = "LIVE_NAKED_CLOSE_FAILED"
EXIT_UNCONFIRMED = "LIVE_EXIT_UNCONFIRMED"
BRACKET_CANCEL_FAILED = "LIVE_BRACKET_CANCEL_FAILED"
FILL_FACTS_MISSING = "LIVE_FILL_FACTS_MISSING"
POSITION_PERSIST_FAILED = "LIVE_POSITION_PERSIST_FAILED"
OUTCOME_PERSIST_FAILED = "LIVE_OUTCOME_PERSIST_FAILED"

# A conditional order rests at the venue until its trigger price is reached. Only that resting
# state counts as "the bracket is in place": anything else (a rejection, an instant trigger, an
# unknown status) means the position is not protected the way the decision assumed.
BRACKET_RESTING_STATUSES = frozenset({"NEW"})

# Close reasons written onto the outcome record.
CLOSE_REASON_NAKED = "naked_position_close"


def _f(value: Any) -> float | None:
    """A number, or None. Never a default — a missing fill figure must not read as zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --- the bracket ---------------------------------------------------------------

def build_bracket_intent(
    *, symbol: str, leg: str, side: str, stop_price: float, working_type: str, position_seed: str
) -> dict[str, Any]:
    """One protective leg as an order intent. Pure.

    ``closePosition`` rather than a sized ``reduceOnly``: the venue forbids the two together and
    treats ``closePosition`` as Close-All, so the leg protects whatever is actually open. That
    matters because the fill can differ from the intent, and a bracket sized to the *intent*
    would leave a sliver unprotected after a partial fill.

    The client order id folds ``leg`` into its seed, so the stop and the target get distinct
    idempotency keys and neither can be mistaken for the entry.
    """
    key = make_idempotency_key({"seed": position_seed, "leg": leg, "symbol": symbol})
    return {
        "status": "ORDER_INTENT_CREATED",
        "symbol": symbol,
        "side": side,
        "order_type_exchange": (
            ORDER_TYPE_STOP_MARKET if leg == "SL" else ORDER_TYPE_TAKE_PROFIT_MARKET
        ),
        "stop_price": float(stop_price),
        "working_type": working_type,
        # Close-All: no quantity, no reduceOnly — the venue rejects those alongside it.
        "close_position": True,
        "reduce_only": False,
        "client_order_id": make_client_order_id(symbol, leg, key),
        "idempotency_key": key,
        "connectivity_test": False,
    }


def place_bracket_leg(
    intent: Mapping[str, Any], *, adapter: Any, timeout_seconds: int = 10
) -> dict[str, Any]:
    """Submit one protective leg and confirm it is **resting** at the venue.

    Deliberately not ``submit_and_reconcile``: that function reconciles against ``status ==
    FILLED``, which is right for an entry or a close and wrong for a protective order. A
    correctly placed stop is ``NEW`` — it has not executed and must not. Reusing the entry's
    reconciler here would report every healthy bracket as a MISMATCH.

    Returns ``{placed, status, client_order_id, exchange_order_id, error}``. Never raises: a
    failure here has a defined consequence (close the position), so it is data, not an
    exception.
    """
    from .live_execution import build_order_request  # local: keeps the import surface honest

    client_order_id = str(intent.get("client_order_id") or "")
    result: dict[str, Any] = {
        "leg": intent.get("side"),
        "client_order_id": client_order_id,
        "order_type": intent.get("order_type_exchange"),
        "stop_price": intent.get("stop_price"),
        "placed": False,
        "status": None,
        "exchange_order_id": None,
        "error": None,
    }
    try:
        request = build_order_request(intent)
        adapter.submit(request, timeout_seconds=timeout_seconds)
    except ToolError as exc:
        # A rejection is informative but not conclusive — the order may still have landed, so
        # the venue is asked below rather than assumed. (The entry path's posture.)
        result["error"] = exc.reason_code

    try:
        venue_order = adapter.fetch_order(
            str(intent["symbol"]), client_order_id, timeout_seconds=timeout_seconds
        )
    except ToolError as exc:
        result["error"] = result["error"] or exc.reason_code
        result["status"] = "UNRECONCILABLE"
        return result

    if venue_order is None:
        result["status"] = "NOT_FOUND"
        return result
    status = str(venue_order.get("status") or "")
    result["status"] = status
    result["exchange_order_id"] = venue_order.get("orderId")
    result["placed"] = status in BRACKET_RESTING_STATUSES
    if result["placed"]:
        # The submit may have reported a duplicate-id rejection while the original was already
        # resting; the venue read is the truth, so a confirmed resting leg clears the error.
        result["error"] = None
    return result


def cancel_bracket_legs(
    position: Mapping[str, Any], *, adapter: Any, timeout_seconds: int = 10
) -> list[dict[str, Any]]:
    """Withdraw both bracket legs after a close. Never raises.

    The venue documents no auto-cancel, so the leg that did *not* trigger is still resting. The
    leg that did trigger answers "unknown order", which the adapter reports as ``None`` — an
    expected result, not a failure. A cancel that fails for any other reason is reported so the
    operator can clear it by hand; it is not fatal, because a ``closePosition`` leg can only
    ever reduce a position, never open one.
    """
    symbol = str(position.get("symbol") or "")
    results: list[dict[str, Any]] = []
    for key in ("stop_client_order_id", "take_profit_client_order_id"):
        client_order_id = position.get(key)
        if not isinstance(client_order_id, str) or not client_order_id:
            continue
        entry: dict[str, Any] = {"leg": key, "client_order_id": client_order_id, "error": None}
        try:
            response = adapter.cancel_order(symbol, client_order_id, timeout_seconds=timeout_seconds)
        except ToolError as exc:
            entry["cancelled"] = False
            entry["error"] = exc.reason_code
        else:
            # None = the venue had nothing to cancel (already triggered or already gone), which
            # is a successful outcome for this operation, not a miss.
            entry["cancelled"] = True
            entry["already_gone"] = response is None
        results.append(entry)
    return results


# --- the entry ------------------------------------------------------------------

def execute_live_entry(
    decision: Mapping[str, Any],
    *,
    adapter: Any,
    position_store: Any,
    counter: Any = None,
    governance: Mapping[str, Any] | None = None,
    gate_open: bool,
    limits: Any,
    now: str,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    """Open one live position from a ``READY`` decision, protected or not at all.

    ``decision`` is ``live_entry.plan_live_entry``'s record. This refuses to send anything
    unless that decision is ``ready`` **and** carries an approved guard verdict — the same
    belt-and-suspenders ``submit_and_reconcile`` applies, restated here because this is the
    function that turns a plan into money.

    ``governance`` is ``live_governance.prepare_live_order_governance``'s record — the P5
    PermissionDecision this order is placed under. It is **required** to send: the policy's
    ``p5_policy_gate`` lists ``post_action_report_and_audit`` among its requirements, and an
    order with no governance record cannot satisfy it. Passing ``None`` therefore refuses rather
    than sending an unaudited order. (It is a keyword with a default only so the refusal is a
    reported ``ENTRY_REFUSED`` rather than a TypeError at the call site.)

    Returns a result record. ``position`` is non-None only on ``ENTRY_OPENED``.
    """
    result: dict[str, Any] = {
        "live_leg_version": LIVE_LEG_VERSION,
        "status": ENTRY_REFUSED,
        "symbol": decision.get("symbol"),
        "reason_codes": [],
        "entry": None,
        "bracket": [],
        "naked_close": None,
        "position": None,
        "created_at": now,
    }

    guard = decision.get("guard") if isinstance(decision, Mapping) else None
    if not (decision.get("ready") is True and isinstance(guard, Mapping) and guard.get("approved") is True):
        result["reason_codes"] = [NOT_READY]
        return result

    # No governance record, no order. The P5 policy gate requires a post-action report, which is
    # impossible without the decision the order is placed under — so this refuses here rather
    # than sending something that could not be audited afterwards.
    if not (isinstance(governance, Mapping) and governance.get("permission_decision")):
        result["reason_codes"] = [NO_GOVERNANCE]
        return result
    result["permission_decision_id"] = governance["permission_decision"].get("permission_decision_id")

    intent = decision["intent"]
    bracket = decision["bracket"]

    # 1. The entry. The counter is incremented for an ambiguous submit too — an order that may
    #    have reached the venue must consume daily budget, or a flapping connection could spend
    #    the cap many times over (LiveOrderCounter's own rule).
    entry = submit_and_reconcile(
        intent, adapter=adapter, guard_verdict=guard, now=now, timeout_seconds=timeout_seconds
    )
    result["entry"] = entry
    if counter is not None:
        try:
            counter.record_submission()
        except ToolError as exc:
            result["reason_codes"].append(exc.reason_code)

    filled_qty = _f(entry["fill"].get("executed_qty")) or 0.0
    fill_price = _f(entry["fill"].get("avg_price")) or 0.0
    confirmed = entry["reconcile_status"] == RECONCILED and filled_qty > 0 and fill_price > 0

    if not confirmed:
        # Rule 1: no local position for an unconfirmed entry. But "unconfirmed" is not the same
        # as "nothing happened", and the difference is the dangerous case:
        #
        # A **partial fill** reconciles as MISMATCH (LP4 compares executedQty against the intent),
        # and a fill whose price will not parse fails the check just above — yet in both the venue
        # reports real quantity filled. That is an open, UNPROTECTED position, which rule 2 says
        # is closed rather than warned about. So exposure the venue actually reports is closed
        # here, even though the entry as a whole is refused.
        #
        # The boundary: this acts on exposure the venue REPORTED. An UNRECONCILABLE result (no
        # answer at all) reports nothing, so nothing is assumed and nothing is sent — the next
        # cycle's reconciliation sees the drift and refuses new entries on this symbol, which is
        # the honest handling of "we do not know".
        result["reason_codes"].append(
            ENTRY_UNCONFIRMED if entry["reconcile_status"] != RECONCILED else FILL_FACTS_MISSING
        )
        if filled_qty > 0:
            result["reason_codes"].append(BRACKET_FAILED)
            return _close_naked_position(
                result,
                symbol=str(intent["symbol"]),
                direction=str(intent["direction"]),
                quantity=filled_qty,
                entry_price=fill_price,
                placements=[],
                adapter=adapter,
                gate_open=gate_open,
                limits=limits,
                now=now,
                timeout_seconds=timeout_seconds,
            )
        result["status"] = ENTRY_NOT_CONFIRMED
        return result

    # 2. The protective bracket, before anything is booked.
    symbol = str(intent["symbol"])
    seed = str(entry["client_order_id"])
    legs = [
        build_bracket_intent(
            symbol=symbol, leg="SL", side=bracket["stop_side"],
            stop_price=bracket["stop_loss"], working_type=bracket["working_type"],
            position_seed=seed,
        ),
        build_bracket_intent(
            symbol=symbol, leg="TP", side=bracket["take_profit_side"],
            stop_price=bracket["take_profit"], working_type=bracket["working_type"],
            position_seed=seed,
        ),
    ]
    placements = [place_bracket_leg(leg, adapter=adapter, timeout_seconds=timeout_seconds) for leg in legs]
    result["bracket"] = placements

    if not all(p["placed"] for p in placements):
        # Rule 2: an unprotected live position is closed immediately, not reported and left open.
        result["reason_codes"].append(BRACKET_FAILED)
        return _close_naked_position(
            result,
            symbol=symbol,
            direction=str(intent["direction"]),
            quantity=filled_qty,
            entry_price=fill_price,
            placements=placements,
            adapter=adapter,
            gate_open=gate_open,
            limits=limits,
            now=now,
            timeout_seconds=timeout_seconds,
        )

    # 3. Book the position from the ACTUAL fill — never the intent's requested numbers.
    position = build_live_position(
        symbol=symbol,
        direction=str(intent["direction"]),
        quantity=filled_qty,
        entry_price=fill_price,
        stop_loss=bracket["stop_loss"],
        take_profit=bracket["take_profit"],
        opened_at=now,
        entry_client_order_id=entry["client_order_id"],
        entry_exchange_order_id=entry["exchange_order_id"],
        strategy_id=intent.get("strategy_id"),
        candidate_id=decision.get("sizing", {}).get("candidate_id") or intent.get("candidate_id"),
        strategy_rule_hash=intent.get("strategy_rule_hash"),
    )
    # The bracket ids ride on the stored record (additive keys, so LP5.1's builder is untouched)
    # because the exit path has to cancel exactly these orders and nothing else.
    position = {
        **position,
        "stop_client_order_id": placements[0]["client_order_id"],
        "take_profit_client_order_id": placements[1]["client_order_id"],
        "entry_quote_usdt": _f(entry["fill"].get("cum_quote")),
    }
    try:
        position_store.save_position(position)
    except ToolError as exc:
        # The position is real and bracketed; only the local book failed. Say so loudly rather
        # than reporting a clean open — the venue and the book now disagree, and the next
        # cycle's reconciliation will refuse entries on this symbol, which is correct.
        result["reason_codes"].append(POSITION_PERSIST_FAILED)
        result["reason_codes"].append(exc.reason_code)

    result["status"] = ENTRY_OPENED
    result["position"] = position
    return result


def _placed_id(placements: list[dict[str, Any]], index: int) -> str | None:
    """The client order id of a bracket leg that actually placed, else None."""
    if index >= len(placements) or not placements[index].get("placed"):
        return None
    return placements[index].get("client_order_id")


def _close_naked_position(
    result: dict[str, Any],
    *,
    symbol: str,
    direction: str,
    quantity: float,
    entry_price: float,
    placements: list[dict[str, Any]],
    adapter: Any,
    gate_open: bool,
    limits: Any,
    now: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Close a position that filled but could not be protected. Rule 2, implemented.

    Also withdraws whichever bracket leg *did* place: leaving one half of a bracket resting
    against a position that no longer exists is exactly the litter the cancel-on-close rule
    exists to avoid.
    """
    close_intent = {
        "status": "ORDER_INTENT_CREATED",
        "symbol": symbol,
        "direction": direction,
        "side": "SELL" if direction == "LONG" else "BUY",
        "order_type_exchange": ORDER_TYPE_MARKET,
        "quantity": float(quantity),
        "order_notional_usdt": round(float(quantity) * float(entry_price), 2),
        "reduce_only": True,
        "close_reason": CLOSE_REASON_NAKED,
        "connectivity_test": False,
        "client_order_id": make_client_order_id(
            symbol, "CLOSE",
            make_idempotency_key({"symbol": symbol, "naked": True, "at": now, "qty": quantity}),
        ),
    }
    close_guard = evaluate_live_close_guard(close_intent, gate_open=gate_open, limits=limits)
    if not close_guard["approved"]:
        # The one branch with no good outcome: a real position is open and unprotected and the
        # close path itself refuses. It is reported as loudly as the vocabulary allows.
        result["status"] = ENTRY_NAKED_OPEN
        result["reason_codes"].append(NAKED_CLOSE_FAILED)
        result["naked_close"] = {"guard": close_guard, "submitted": False}
        return result

    close = submit_and_reconcile(
        close_intent, adapter=adapter, guard_verdict=close_guard, now=now,
        timeout_seconds=timeout_seconds,
    )
    result["naked_close"] = {"guard": close_guard, "submitted": True, "result": close}
    # Withdraw whichever legs placed, if any. `placements` is empty when the entry itself was
    # never confirmed, in which case no bracket was attempted and there is nothing to withdraw.
    placed = {
        "stop_client_order_id": _placed_id(placements, 0),
        "take_profit_client_order_id": _placed_id(placements, 1),
        "symbol": symbol,
    }
    result["naked_close"]["cancels"] = cancel_bracket_legs(
        placed, adapter=adapter, timeout_seconds=timeout_seconds
    )

    if close["reconcile_status"] == RECONCILED:
        result["status"] = ENTRY_NAKED_CLOSED
        result["reason_codes"].append(NAKED_POSITION_CLOSED)
    else:
        result["status"] = ENTRY_NAKED_OPEN
        result["reason_codes"].append(NAKED_CLOSE_FAILED)
    return result


# --- the exit -------------------------------------------------------------------

def realized_pnl_usdt(
    position: Mapping[str, Any], exit_fill: Mapping[str, Any]
) -> tuple[float | None, dict[str, Any]]:
    """Realized P&L from the venue's ACTUAL fills, gross of fees. Pure.

    Quote-in vs quote-out, never ``(exit - entry) * intended_qty``: a partial fill or slippage
    makes the intended numbers a fiction. Returns ``(pnl, detail)`` with ``pnl`` None when the
    figures are not there — refusing to compute is the honest answer, and the caller keeps the
    position rather than recording an invented result.

    Fees and funding are **not** included; see the module docstring for why, and for which way
    that error points.
    """
    quantity = _f(position.get("quantity")) or 0.0
    entry_price = _f(position.get("entry_price")) or 0.0
    entry_quote = _f(position.get("entry_quote_usdt"))
    if entry_quote is None and quantity > 0 and entry_price > 0:
        entry_quote = round(quantity * entry_price, 8)

    exit_quote = _f(exit_fill.get("cum_quote"))
    exit_qty = _f(exit_fill.get("executed_qty"))
    exit_price = _f(exit_fill.get("avg_price"))
    if exit_quote is None and exit_qty and exit_price:
        exit_quote = round(exit_qty * exit_price, 8)

    detail = {
        "entry_quote_usdt": entry_quote,
        "exit_quote_usdt": exit_quote,
        "exit_quantity": exit_qty,
        "exit_price": exit_price,
        "fees_included": False,
        "pnl_source": "venue_fills_gross",
    }
    if entry_quote is None or exit_quote is None:
        return None, detail

    direction = str(position.get("direction") or "").upper()
    if direction == "LONG":
        pnl = exit_quote - entry_quote
    elif direction == "SHORT":
        pnl = entry_quote - exit_quote
    else:
        return None, detail
    return round(pnl, 8), detail


def execute_live_exit(
    position: Mapping[str, Any],
    *,
    adapter: Any,
    position_store: Any,
    ledger: Any,
    gate_open: bool,
    limits: Any,
    close_reason: str,
    now: str,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    """Close one open live position, withdraw its bracket, and record the realized outcome.

    The close guard is deliberately narrower than the entry guard: a reduceOnly close is exempt
    from the loss breaker, the caps, the daily count, the promotion gate and both kill switches,
    because a halt that traps a losing position open is worse than the halt prevents. What
    survives is the structural boundary — the grant, the phrase, and ``reduce_only`` itself.

    **The book is cleared only on a confirmed close.** An unconfirmed exit leaves the position
    OPEN locally, which is the safe direction: the next cycle reconciles against the venue and
    refuses new entries on that symbol until the disagreement is resolved.
    """
    result: dict[str, Any] = {
        "live_leg_version": LIVE_LEG_VERSION,
        "status": EXIT_REFUSED,
        "symbol": position.get("symbol"),
        "position_id": position.get("position_id"),
        "reason_codes": [],
        "exit": None,
        "cancels": [],
        "outcome": None,
        "created_at": now,
    }

    symbol = str(position.get("symbol") or "")
    direction = str(position.get("direction") or "").upper()
    quantity = _f(position.get("quantity")) or 0.0
    close_intent = {
        "status": "ORDER_INTENT_CREATED",
        "symbol": symbol,
        "direction": direction,
        "side": "SELL" if direction == "LONG" else "BUY",
        "order_type_exchange": ORDER_TYPE_MARKET,
        "quantity": quantity,
        "order_notional_usdt": round(quantity * (_f(position.get("entry_price")) or 0.0), 2),
        "reduce_only": True,
        "close_reason": close_reason,
        "connectivity_test": False,
        "client_order_id": make_client_order_id(
            symbol, "CLOSE",
            make_idempotency_key({
                "position_id": position.get("position_id"), "reason": close_reason,
            }),
        ),
    }

    close_guard = evaluate_live_close_guard(close_intent, gate_open=gate_open, limits=limits)
    result["close_guard"] = close_guard
    if not close_guard["approved"]:
        return result

    exit_result = submit_and_reconcile(
        close_intent, adapter=adapter, guard_verdict=close_guard, now=now,
        timeout_seconds=timeout_seconds,
    )
    result["exit"] = exit_result
    if exit_result["reconcile_status"] != RECONCILED:
        result["status"] = EXIT_NOT_CONFIRMED
        result["reason_codes"].append(EXIT_UNCONFIRMED)
        return result

    pnl, pnl_detail = realized_pnl_usdt(position, exit_result["fill"])
    result["pnl_detail"] = pnl_detail
    if pnl is None:
        # Confirmed closed at the venue but not computable: do NOT clear the book on a result
        # that cannot be recorded, or the trade would vanish from the breaker's accounting.
        result["status"] = EXIT_NOT_CONFIRMED
        result["reason_codes"].append(FILL_FACTS_MISSING)
        return result

    # Rule 3: withdraw the surviving leg. After the close, so a cancel can never unprotect a
    # position that is still open.
    result["cancels"] = cancel_bracket_legs(position, adapter=adapter, timeout_seconds=timeout_seconds)
    if any(c.get("error") for c in result["cancels"]):
        result["reason_codes"].append(BRACKET_CANCEL_FAILED)

    outcome = build_live_outcome_record(
        realized_pnl_usdt=pnl,
        symbol=symbol,
        side=close_intent["side"],
        quantity=quantity,
        entry_price=_f(position.get("entry_price")),
        exit_price=pnl_detail["exit_price"],
        entry_order_id=position.get("entry_exchange_order_id"),
        exit_order_id=exit_result["exchange_order_id"],
        strategy_id=position.get("strategy_id"),
        position_id=position.get("position_id"),
        close_reason=close_reason,
        opened_at_utc=position.get("opened_at_utc"),
        # LP5.4's bridge: without the recorded risk there is no honest R, and the bridge
        # excludes an R-less row rather than letting it read as a breakeven.
        risk_usdt=_f(position.get("risk")),
        candidate_id=position.get("candidate_id"),
        strategy_rule_hash=position.get("strategy_rule_hash"),
        strategy_generation_id=position.get("strategy_generation_id"),
        now=now,
    )
    result["outcome"] = outcome

    # Record the money BEFORE clearing the book: an outcome that never lands is a loss the
    # breaker will never see, whereas a cleared book with a recorded outcome is merely stale
    # local state the next reconciliation catches.
    try:
        ledger.append_outcome(outcome)
    except ToolError as exc:
        result["reason_codes"].append(OUTCOME_PERSIST_FAILED)
        result["reason_codes"].append(exc.reason_code)
        result["status"] = EXIT_NOT_CONFIRMED
        return result

    try:
        position_store.clear_position(symbol)
    except ToolError as exc:
        result["reason_codes"].append(exc.reason_code)

    result["status"] = EXIT_CLOSED
    return result


def leg_status_line(result: Mapping[str, Any]) -> str:
    """One ASCII line for the console (Windows consoles are cp949)."""
    parts = [f"live_leg {result.get('symbol')}: {result.get('status')}"]
    reasons = result.get("reason_codes") or []
    if reasons:
        parts.append("(" + ",".join(str(r) for r in reasons) + ")")
    outcome = result.get("outcome")
    if isinstance(outcome, Mapping):
        parts.append(f"pnl={outcome.get('realized_pnl_usdt')} R={outcome.get('result_R')}")
    return " ".join(parts)


__all__ = [
    "BRACKET_CANCEL_FAILED",
    "BRACKET_FAILED",
    "BRACKET_RESTING_STATUSES",
    "ENTRY_NAKED_CLOSED",
    "ENTRY_NAKED_OPEN",
    "ENTRY_NOT_CONFIRMED",
    "ENTRY_OPENED",
    "ENTRY_REFUSED",
    "ENTRY_UNCONFIRMED",
    "EXIT_CLOSED",
    "EXIT_NOT_CONFIRMED",
    "EXIT_REFUSED",
    "EXIT_UNCONFIRMED",
    "FILL_FACTS_MISSING",
    "LIVE_LEG_VERSION",
    "NAKED_CLOSE_FAILED",
    "NAKED_POSITION_CLOSED",
    "NOT_READY",
    "NO_GOVERNANCE",
    "OUTCOME_PERSIST_FAILED",
    "POSITION_PERSIST_FAILED",
    "build_bracket_intent",
    "cancel_bracket_legs",
    "execute_live_entry",
    "execute_live_exit",
    "leg_status_line",
    "place_bracket_leg",
    "realized_pnl_usdt",
]
