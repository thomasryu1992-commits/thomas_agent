"""LP5.3 — the executing leg. The wire between a decision that exists and a sender that exists.

``live_entry.plan_live_entry`` decides; ``live_execution.submit_and_reconcile`` sends. This
module is the short, consequential piece between them: it takes a ``READY`` decision, opens a
real position with a venue-side protective bracket, and later closes it and records the
realized result. Design: ``docs/runtime-contracts/LP5_3_LIVE_LEG_DESIGN_V0.1.md``.

What a result says (the outcomes, reason codes and close reasons) and the functions that read one
without the venue, the realized P&L among them, live in ``live_leg_results`` and are re-exported
here. What is said below about the realized P&L describes ``live_leg_results.realized_pnl_usdt``.

**The adapter is injected, never selected here.** Every branch below — an unconfirmed entry, a
bracket that will not place, the naked-position close, the cancel-on-close, the realized P&L
from actual fills — is therefore exercised in tests with a fake adapter and **zero network**.
It also means this module cannot reach the venue on its own: a caller must hand it a capable
adapter, which only ``live_execution.select_order_adapter`` can build, and only behind the
``live_trading`` grant.

**The crypto cycle now calls this**, through exactly one module: ``crypto/live_route.py``. That
wiring was the step the design record sequenced last, because it is the line which makes an
autonomous live order reachable — so it is deliberately a single chokepoint rather than a call
from the cycle itself, and a test pins that no entry point reaches this module by any other
route. What still stands between the wiring and an order is the gate: ``live_route`` runs
nothing at all unless the ``live_trading`` grant is active on the machine, and every door below
it (the guard, the phrase, the registered budget) is unchanged.

The three rules this leg owes, each implemented as a branch you can point at:

1. **Open only on ``RECONCILED``.** A submit that came back ``MISMATCH``, ``NOT_FOUND`` or
   ``UNRECONCILABLE`` creates no local position. The venue is the truth; an unconfirmed entry
   is not a position, it is an incident to surface.
2. **A naked position is closed, not warned about.** If the entry fills but a bracket leg
   cannot be placed, the position is closed immediately (reduceOnly MARKET). An unprotected
   live position is exactly what the bracket exists to prevent, so the fail-closed direction
   is *out*, not in.
3. **Cancel the surviving leg on close.** The venue documents no auto-cancel for conditional
   orders when a position closes, and a resting ``reduceOnly`` LIMIT is not cancelled either, so
   a leftover leg of either shape is withdrawn explicitly. Neither can open anything — Close-All
   and reduceOnly both only ever reduce — so a failed cancel is reported, never treated as fatal.

**The two bracket legs have deliberately different shapes** (changed 2026-07-28):

- **The stop is a ``closePosition`` ``STOP_MARKET``.** The venue treats ``closePosition`` as
  Close-All and forbids it from carrying a quantity, so the stop protects whatever is actually
  open even if the fill drifted from the intent. This is the leg that defines the risk, and it
  stays a market order on purpose: a stop that can fail to fill is not a stop.
- **The target is a sized ``reduceOnly`` ``LIMIT``.** A ``TAKE_PROFIT_MARKET`` triggers into a
  market order, so it pays the taker rate plus adverse slippage to exit at a price the market
  had to come to anyway. A resting LIMIT at the same price earns the maker rate and fills at
  the target exactly — which is also what the backtest has always assumed the target does
  (``trade_plan.settle_trade_plan`` returns the target price itself as the exit), so this closes a
  model-versus-reality gap rather than opening one.

The cost of that asymmetry is stated rather than hidden: ``closePosition`` is documented for
the two ``_MARKET`` conditional types only, so the target leg **cannot** be Close-All and must
carry a quantity. It is therefore sized from the ACTUAL entry fill, not the intent. If the
position later grows (nothing here does that) the target leg would cover only the original
size, while the stop would still cover all of it — the asymmetry runs in the safe direction.
A ``reduceOnly`` order can only ever reduce, so neither leg can open anything.

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

import time
from typing import Any, Mapping

from ..coerce import as_optional_float as _f
from ..errors import ToolError
from . import pre_order_gate
from .live_execution import (
    SubmitRefused,
    fill_facts,
    is_protective_request,
    submit_and_reconcile,
    submit_may_have_landed,
    submit_refused_outright,
)
from .order_request import (
    CONDITIONAL_ORDER_TYPES,
    NOT_FOUND,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_STOP_MARKET,
    TIME_IN_FORCE_GTC,
)
from .execution_stage import PURPOSE_AUTONOMOUS
from .live_order import (
    evaluate_live_close_guard,
)
from .live_order_stores import (
    LIVE_ENTRY_CLAIM_LOST,
)
from .order_identity import (
    make_client_order_id,
    make_idempotency_key,
)
from .live_settlement import build_live_outcome_record
from .live_position import build_live_position, position_risk_usdt, unbooked_position_id
from .order_request import RECONCILED
# What a leg's result says lives in `live_leg_results.py` (moved whole, refactor plan PR-13): the
# outcomes, reason codes and close reasons, and the functions that read a result without calling an
# adapter or a store. Re-exported here as the same objects. Everything that sends, reads the venue or
# writes a store is below, unchanged.
from .live_leg_results import (  # noqa: F401
    BRACKET_CANCEL_FAILED, BRACKET_FAILED, BRACKET_IDS_MISSING, BRACKET_LEFT_RESTING,
    BRACKET_LEG_NOT_PROTECTIVE, BRACKET_NOT_APPROVED, BRACKET_QUERY_MISSING,
    BRACKET_RESTING_STATUSES, CLAIM_LOST, CLAIM_NOT_RELEASED, CLOSE_REASON_EMERGENCY,
    CLOSE_REASON_NAKED, CLOSE_REASON_STOP, CLOSE_REASON_TARGET, CLOSE_REASON_TIME_EXIT,
    CLOSE_REASON_UNPROTECTED, CLOSE_REASON_VENUE_EXTERNAL, ENTRY_NAKED_CLOSED, ENTRY_NAKED_OPEN,
    ENTRY_NOT_CONFIRMED, ENTRY_OPENED, ENTRY_REFUSED, ENTRY_UNCONFIRMED, EXIT_CLOSED,
    EXIT_NOT_CONFIRMED, EXIT_REFUSED, EXIT_SOURCE_BRACKET_LEG, EXIT_SOURCE_FILL_HISTORY,
    EXIT_SOURCE_RUNTIME_CLOSE, EXIT_UNCONFIRMED, EXIT_UNSETTLEABLE, FILLED_STATUSES,
    FILL_FACTS_MISSING, FILL_HISTORY_INCONCLUSIVE, FILL_HISTORY_UNAVAILABLE, NAKED_CLOSE_FAILED,
    NAKED_POSITION_CLOSED, NOT_READY, NO_ENTRY_MARKS, NO_GOVERNANCE, NO_ORDER_COUNTER,
    NO_SNAPSHOT_STORE, OUTCOME_ALREADY_RECORDED, OUTCOME_PERSIST_FAILED, POSITION_PERSIST_FAILED,
    PROTECTED, PROTECTION_UNKNOWN, RESTING_ORDERS, RESTING_ORDERS_UNREADABLE, UNPROTECTED,
    VENUE_CLOSE_UNSETTLEABLE, _BRACKET_LEGS, _history_start_ms, _persist_failure_reason, _placed_id,
    _record_naked_outcome, bracket_error_detail, exit_fill_from_history, leg_status_line,
    legs_left_resting, realized_pnl_usdt,
)

LIVE_LEG_VERSION = "live_leg.v0.1"


# --- the read-after-write race, and what it cost ---------------------------------------------
#
# **The bracket worked and this module destroyed it.** Measured on the live account
# 2026-08-03T04:28:58Z, and confirmed from the venue's own algo-order history:
#
#   algoId 2000001331951220  clientAlgoId TAI_ETHUSDT_SL_764f36ae2ac555eeeb
#   STOP_MARKET closePosition=true triggerPrice=1887.86  createTime 04:29:01.793Z
#   algoStatus EXPIRED
#
# The POST created the order. The query that followed it — milliseconds later — answered "does
# not exist". This leg recorded `placed: false`, `execute_live_entry` read that as an
# unprotected position and closed it, and the stop then EXPIRED because a `closePosition` order
# has nothing to close once its position is gone. The same query run later finds the order
# perfectly well.
#
# **Conditional orders live on a SEPARATE service** (that is why they have their own endpoints
# at all), and a write there is not immediately readable. The whole entry-to-close sequence
# completes inside ~500ms, which lands squarely in that window.
#
# So a single immediate "not found" is not evidence of anything. It is asked again, with a
# backoff, before this leg is willing to say a protective stop is missing — because the cost of
# being wrong is asymmetric and nothing in the earlier design priced that. Being slow to notice
# a genuinely failed placement delays the naked close by about a second, during which the
# position is unprotected either way. Being fast and WRONG closes a protected position and
# throws away the stop that was protecting it.
#
# The attempt budget is a first bounded probe, not a measurement: nothing here knows the
# service's real lag. `confirm_attempts` is recorded so the next occurrence says what it
# actually took, the same way `stdev_r` and `submit_response` were recorded before they were
# relied on.
BRACKET_CONFIRM_ATTEMPTS = 3
BRACKET_CONFIRM_BACKOFF_SECONDS = (0.5, 1.0)


def _exit_terms(decision: Mapping[str, Any]) -> Mapping[str, Any]:
    """The plan's exit terms as the decision recorded them, or empty.

    Empty is a legitimate answer, not a defect: a decision built before `exit_terms` existed
    carries none, and `build_live_position` stores `None` for both. What that produces is a
    *legacy* position, which `trade_plan.position_max_hold` already knows how to judge — timeframe
    table fallback, with the fallback reported so the gap stays attributable.
    """
    terms = decision.get("exit_terms")
    return terms if isinstance(terms, Mapping) else {}


def resting_orders(adapter: Any, symbol: str, *, timeout_seconds: int) -> list[str]:
    """The ids of every order resting at the venue on ``symbol``, plain and conditional (PR2c-3).

    Raises ``ToolError(RESTING_ORDERS_UNREADABLE)`` when either list cannot be read: "nothing
    rests" and "the venue did not say" must never be the same answer before an entry."""
    try:
        plain = adapter.open_orders(symbol, timeout_seconds=timeout_seconds)
        conditional = adapter.algo_open_orders(symbol, timeout_seconds=timeout_seconds)
    except Exception as exc:  # noqa: BLE001 — any failure to read is an unread list
        raise ToolError(RESTING_ORDERS_UNREADABLE,
                        f"resting orders on {symbol} could not be read ({type(exc).__name__})") from exc
    if not (isinstance(plain, list) and isinstance(conditional, list)):
        raise ToolError(RESTING_ORDERS_UNREADABLE, f"resting orders on {symbol} came back malformed")
    # The client id the runtime and `scripts/list_resting_orders.py` name an order by: a conditional
    # order's is `clientAlgoId` (the algo number is aliased to `orderId`, so it comes after).
    return [str(o.get("clientOrderId") or o.get("clientAlgoId") or o.get("orderId") or "?")
            if isinstance(o, Mapping) else "?"
            for o in (*plain, *conditional)]


def claim_exposure(decision: Mapping[str, Any], limits: Any) -> tuple[Any, dict[str, Any] | None]:
    """``(notional_usdt, exposure)`` for the symbol claim (PR2c-3): the notional the decision's guard
    judged, and the exposure it judged it against, capped by the limits the gate judged. None for
    what the decision does not carry; the claim then refuses as malformed."""
    guard = decision.get("guard") if isinstance(decision.get("guard"), Mapping) else {}
    seen = decision.get("exposure_seen")
    if not isinstance(seen, Mapping):
        return guard.get("notional_usdt"), None
    return guard.get("notional_usdt"), {
        "open_notional_usdt": seen.get("open_notional_usdt"),
        "position_ids": seen.get("position_ids"),
        "cap_usdt": getattr(limits, "max_open_notional_usdt", None),
    }


# --- the bracket ---------------------------------------------------------------

def build_bracket_intent(
    *,
    symbol: str,
    leg: str,
    side: str,
    price: float,
    working_type: str,
    position_seed: str,
    quantity: float | None = None,
) -> dict[str, Any]:
    """One protective leg as an order intent. Pure. ``price`` is the level the leg acts at —
    the stop trigger for ``SL``, the resting limit price for ``TP``.

    The two legs are shaped differently on purpose; the module docstring says why. In short:

    - ``SL`` → ``closePosition`` ``STOP_MARKET``. Close-All, so it protects whatever is actually
      open even when the fill differs from the intent. No quantity, no ``reduceOnly`` — the venue
      rejects both alongside ``closePosition``.
    - ``TP`` → sized ``reduceOnly`` ``LIMIT`` (GTC), which earns the maker rate instead of paying
      taker plus slippage. ``closePosition`` is not available on a LIMIT at this venue, so this
      leg **requires** a positive ``quantity``, and the caller must pass the ACTUAL filled size.
      Refused rather than defaulted: a target leg sized from the intent after a partial fill
      would try to reduce more than exists.

    The client order id folds ``leg`` into its seed, so the stop and the target get distinct
    idempotency keys and neither can be mistaken for the entry.
    """
    if leg not in ("SL", "TP"):
        raise ToolError("MALFORMED_BRACKET_LEG", f"bracket leg must be SL or TP, got {leg!r}")
    key = make_idempotency_key({"seed": position_seed, "leg": leg, "symbol": symbol})
    intent: dict[str, Any] = {
        "status": "ORDER_INTENT_CREATED",
        "symbol": symbol,
        "side": side,
        "client_order_id": make_client_order_id(symbol, leg, key),
        "idempotency_key": key,
        "connectivity_test": False,
    }
    if leg == "SL":
        intent.update({
            "order_type_exchange": ORDER_TYPE_STOP_MARKET,
            "stop_price": float(price),
            "working_type": working_type,
            # Close-All: no quantity, no reduceOnly — the venue rejects those alongside it.
            "close_position": True,
            "reduce_only": False,
        })
        return intent
    if not (isinstance(quantity, (int, float)) and quantity > 0):
        raise ToolError(
            "MISSING_BRACKET_QUANTITY",
            "a LIMIT take-profit leg cannot be closePosition at this venue, so it needs the "
            "actual filled quantity — never the intent's requested size",
        )
    intent.update({
        "order_type_exchange": ORDER_TYPE_LIMIT,
        "price": float(price),
        "time_in_force": TIME_IN_FORCE_GTC,
        "quantity": float(quantity),
        "close_position": False,
        "reduce_only": True,
    })
    return intent


def place_bracket_leg(
    intent: Mapping[str, Any], *, adapter: Any, timeout_seconds: int = 10,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """Submit one protective leg and confirm it is **resting** at the venue.

    Deliberately not ``submit_and_reconcile``: that function reconciles against ``status ==
    FILLED``, which is right for an entry or a close and wrong for a protective order. A
    correctly placed stop is ``NEW`` — it has not executed and must not, and so is a correctly
    placed target LIMIT, which rests until the market reaches it. Reusing the entry's reconciler
    here would report every healthy bracket as a MISMATCH.

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
        # The level the leg acts at, under whichever field its type carries it in: a trigger for
        # the conditional stop, a resting price for the target LIMIT. Recorded under one name so
        # an audit reader does not have to know the leg's shape to read its price.
        "stop_price": intent.get("stop_price"),
        "price": intent.get("stop_price") if intent.get("price") is None else intent.get("price"),
        "quantity": intent.get("quantity"),
        "placed": False,
        "status": None,
        "exchange_order_id": None,
        # The venue's answer to the SUBMIT, kept rather than discarded. It was being thrown away
        # while the query was treated as the only truth — which is defensible while the two
        # agree and is exactly how a placed order became invisible when they did not. On the
        # Algo endpoints this response carries `algoId` and `algoStatus`, i.e. direct evidence
        # the order exists, so discarding it threw away the only thing that could have caught
        # 2026-08-03. Recorded, never TRUSTED: `placed` still comes from the read.
        "submit_response": None,
        "submitted_order_id": None,
        # Whether an order might be resting at the venue that this result cannot confirm. The
        # cancel path reads it, because "might exist" and "does not exist" have the same correct
        # treatment on close — withdraw it — and only one of them leaves litter if you are wrong.
        "may_be_resting": False,
        # How many reads it took to see the order. 1 is the ordinary case; more than 1 is the
        # algo service's write lag being measured rather than guessed at.
        "confirm_attempts": 0,
        "error": None,
        # WHY the venue said no, not just that it did. `error` is the reason CODE and it is the
        # same string for every rejection there is; the venue's own numeric code and message ride
        # inside the exception's text and were being dropped on the floor.
        #
        # Measured 2026-08-02 on the first real bracket failure: the record said
        # `error: ORDER_REJECTED` and nothing else, so the cause of a protective stop being
        # refused — the one leg whose absence forces a naked position closed — had to be guessed
        # from the surrounding fields. A rejection that cannot say why is a rejection that has to
        # happen twice before anyone can act on it.
        "error_detail": None,
    }
    try:
        request = build_order_request(intent)
        if not is_protective_request(request):
            # Nothing was sent, so nothing can be resting: `placed` stays False and the caller
            # closes the position this leg was meant to protect.
            result["status"] = BRACKET_LEG_NOT_PROTECTIVE
            result["error"] = BRACKET_LEG_NOT_PROTECTIVE
            result["error_detail"] = "the leg is neither reduce-only nor close-position; not sent"
            return result
        response = adapter.submit(request, timeout_seconds=timeout_seconds)
        if isinstance(response, Mapping):
            result["submit_response"] = dict(response)
            # `algoId` on the Algo endpoints, `orderId` on the order endpoint. Either one means
            # the venue created something and named it.
            for key in ("algoId", "orderId"):
                if response.get(key) is not None:
                    result["submitted_order_id"] = response[key]
                    break
    except ToolError as exc:
        # A rejection is informative but not conclusive — the order may still have landed, so
        # the venue is asked below rather than assumed. (The entry path's posture.)
        result["error"] = exc.reason_code
        result["error_detail"] = str(exc)

    # From the intent's own type, not from the leg label: this is the same request that was just
    # submitted, so the endpoint that took it is the endpoint that knows it.
    algo = str(intent.get("order_type_exchange")) in CONDITIONAL_ORDER_TYPES
    # Retried for ALGO legs only. That is where the evidence is, and it keeps the plain path's
    # timing exactly as it was: the entry and the resting LIMIT target have never once shown
    # this, and widening a delay into a path that does not need it would slow every cycle to
    # fix a problem it does not have.
    attempts = BRACKET_CONFIRM_ATTEMPTS if algo else 1
    venue_order = None
    try:
        for attempt in range(attempts):
            venue_order = adapter.fetch_order(
                str(intent["symbol"]), client_order_id, timeout_seconds=timeout_seconds,
                algo=algo,
            )
            result["confirm_attempts"] = attempt + 1
            if venue_order is not None:
                break
            if attempt + 1 < attempts:
                sleep(BRACKET_CONFIRM_BACKOFF_SECONDS[
                    min(attempt, len(BRACKET_CONFIRM_BACKOFF_SECONDS) - 1)])
    except ToolError as exc:
        result["error"] = result["error"] or exc.reason_code
        # Only when the submit did not already explain itself: the submit's own message is the
        # more specific of the two, and a fetch failure after it is a second symptom rather than
        # the cause.
        result["error_detail"] = result["error_detail"] or str(exc)
        result["status"] = "UNRECONCILABLE"
        # Nobody could look, so nobody can say it is absent: a close withdraws it too (PR2c-0).
        # Cancelling an order that does not exist costs one "unknown order" answer.
        result["may_be_resting"] = True
        return result

    if venue_order is None:
        # A submit that named an order and a query that cannot find it is a CONTRADICTION, not a
        # clean miss. `placed` stays False — this leg cannot be claimed as protection, so the
        # naked-position close still runs, which is the safe half. What changes is that the leg
        # is now known to be possibly-resting, so the close withdraws it too.
        if result["submitted_order_id"] is not None:
            result["status"] = BRACKET_QUERY_MISSING
            result["may_be_resting"] = True
            result["error"] = result["error"] or BRACKET_QUERY_MISSING
            result["error_detail"] = result["error_detail"] or (
                f"the venue accepted the submit and named order {result['submitted_order_id']}, "
                "then answered that it does not exist; it may be resting"
            )
            return result
        result["status"] = "NOT_FOUND"
        # A submit that failed without the venue refusing it (a timeout) can still land after the
        # read, and one refused as a duplicate means the original already landed: both may rest,
        # so the close withdraws them too (PR2c-0). An accepted submit that named nothing, and an
        # outright refusal, created nothing to withdraw — the ordinary miss stays ordinary.
        result["may_be_resting"] = submit_may_have_landed(result["error"], result["error_detail"])
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
    operator can clear it by hand; it is not fatal, because neither leg shape can open anything —
    a ``closePosition`` stop is Close-All and a ``reduceOnly`` target can only reduce.
    """
    symbol = str(position.get("symbol") or "")
    results: list[dict[str, Any]] = []
    # Iterated from `_BRACKET_LEGS` rather than a second hardcoded tuple: this loop needs each
    # leg's endpoint, and two lists of the same legs is how one of them keeps the old answer.
    for key, _close_reason, algo in _BRACKET_LEGS:
        client_order_id = position.get(key)
        if not isinstance(client_order_id, str) or not client_order_id:
            continue
        entry: dict[str, Any] = {
            "leg": key, "client_order_id": client_order_id, "error": None, "error_detail": None,
        }
        try:
            response = adapter.cancel_order(
                symbol, client_order_id, timeout_seconds=timeout_seconds, algo=algo
            )
        except ToolError as exc:
            entry["cancelled"] = False
            entry["error"] = exc.reason_code
            entry["error_detail"] = str(exc)
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
    entry_marks: Any = None,
    snapshot_store: Any = None,
    governance: Mapping[str, Any] | None = None,
    gate_open: bool,
    limits: Any,
    now: str,
    timeout_seconds: int = 10,
    sleep: Any = time.sleep,
    cycle_id: str | None = None,
) -> dict[str, Any]:
    """Open one live position from a ``READY`` decision, protected or not at all.

    ``sleep`` is threaded through to :func:`place_bracket_leg`'s confirm backoff so the whole
    entry path stays testable with zero wall-clock — the same reason the adapter is injected.

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

    ``entry_marks`` (``live_order.select_live_entry_marks``) and ``counter`` are required to send
    for the same reason (PR2a): the bar is claimed and the day's order slot reserved before the
    order leaves, each under its own lock, so neither a second entry on one bar nor a second
    process under the daily cap can get an order out. A refusal from either sends nothing.

    ``decision["risk_snapshot"]`` and ``snapshot_store`` are the pre-order gate's (PR2b): the
    snapshot is checked against the intent before anything is spent, written to the store once the
    bar and the slot are, and bound again inside ``submit_and_reconcile``. Without either, nothing
    is sent.

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
        # Present on every entry result, `None` on almost all of them. Only a naked CLOSE
        # produces one here — an entry that opens is settled later by `execute_live_exit`,
        # which writes its own. `live_route` persists whichever it finds.
        "outcome": None,
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

    # 0. Spend the bar, then the day's slot — both before the send (PR2a). Nothing has left yet,
    #    so any failure here, typed or not, is a refusal: the breadth is `_persist_failure_reason`'s,
    #    pointed the safe way. The bar goes first: a claim that is then refused a slot costs a bar
    #    on a day that has no orders left, and the reverse would spend a slot on a bar that
    #    already had its order.
    if entry_marks is None:
        result["reason_codes"] = [NO_ENTRY_MARKS]
        return result
    if counter is None:
        result["reason_codes"] = [NO_ORDER_COUNTER]
        return result
    if snapshot_store is None:
        result["reason_codes"] = [NO_SNAPSHOT_STORE]
        return result
    # The snapshot first, because checking it spends nothing: a decision whose snapshot does not
    # approve exactly this intent must not cost a bar or a slot.
    risk_snapshot = decision.get("risk_snapshot")
    try:
        pre_order_gate.verify_snapshot(intent, risk_snapshot)
    except Exception as exc:  # noqa: BLE001 — before the venue: a refusal, never an escape
        result["reason_codes"] = [_persist_failure_reason(exc)]
        return result
    # The snapshot binds the intent's stop and target; the legs are placed from `bracket`. They must
    # be the same prices, on the sides the direction closes on, or the protection that rests is not
    # the protection that was approved. (The snapshot check has already refused a direction with no
    # side; the None case stays refused here so this check does not depend on that order.)
    closing_side = {"LONG": "SELL", "SHORT": "BUY"}.get(str(intent.get("direction") or "").upper())
    if not (closing_side is not None and isinstance(bracket, Mapping)
            and bracket.get("stop_loss") == intent.get("stop_loss")
            and bracket.get("take_profit") == intent.get("take_profit")
            and bracket.get("stop_side") == closing_side
            and bracket.get("take_profit_side") == closing_side):
        result["reason_codes"] = [BRACKET_NOT_APPROVED]
        return result
    result["risk_snapshot_sha256"] = intent.get("risk_snapshot_sha256")
    entry_bar = decision.get("entry_bar") if isinstance(decision.get("entry_bar"), Mapping) else {}
    # PR2b-2: the symbol before the bar. An entry another door has in flight here costs this one
    # nothing, not even its bar, which it may then take later in the bar. The claim is given back
    # only where the book says what the venue holds; anywhere else it is kept until it expires.
    claim = {"symbol": intent.get("symbol"), "client_order_id": intent.get("client_order_id")}
    notional_usdt, exposure = claim_exposure(decision, limits)
    claimed = False
    try:
        # PR2c-3: the claim also judges the global caps again, against every other entry in flight.
        entry_marks.claim_symbol(door=PURPOSE_AUTONOMOUS, now=now, notional_usdt=notional_usdt,
                                 exposure=exposure, **claim)
        claimed = True
        # PR2c-3: nothing may rest at the venue on the symbol just taken. Read before the bar and the
        # slot, so a refusal here spends neither.
        left = resting_orders(adapter, str(claim["symbol"]), timeout_seconds=timeout_seconds)
        if left:
            result["resting_orders"] = left
            raise ToolError(RESTING_ORDERS, f"{claim['symbol']} has orders resting at the venue: {left}")
        # Those two venue reads can take their timeouts: the decision is judged again for its age
        # before anything is spent, so a slow read costs no bar and no slot (review of #888).
        pre_order_gate.verify_snapshot(intent, risk_snapshot)
        entry_marks.claim_bar(
            symbol=entry_bar.get("symbol"), timeframe=entry_bar.get("timeframe"),
            bar_time=entry_bar.get("bar_time"),
        )
        # The slot is reserved even if the send below fails: an order that may have reached the
        # venue must consume daily budget, or a flapping connection could spend the cap many
        # times over (LiveOrderCounter's own rule, now applied before the send).
        counter.reserve_submission(limit=int(getattr(limits, "max_daily_order_count", 0) or 0))
        # And the reason the order is allowed, on the disk before the order is at the venue.
        pre_order_gate.verify_and_persist(intent, risk_snapshot, store=snapshot_store)
    except Exception as exc:  # noqa: BLE001 — before the venue: a refusal, never an escape
        result["reason_codes"] = [_persist_failure_reason(exc)]
        if claimed:
            _give_back_symbol(result, entry_marks, sent=False, **claim)
        return result

    # 1. The entry. `submit_and_reconcile` binds the snapshot again; the second write is a no-op.
    try:
        entry = submit_and_reconcile(
            intent, adapter=adapter, guard_verdict=guard, now=now, timeout_seconds=timeout_seconds,
            risk_snapshot=risk_snapshot, snapshot_store=snapshot_store,
        )
    except SubmitRefused as exc:
        # Raised only before anything is sent (a refusal before the adapter, or the adapter's own
        # refusal before its send, PR6b): nothing left.
        result["reason_codes"] = [exc.reason_code]
        _give_back_symbol(result, entry_marks, sent=False, **claim)
        return result
    result["entry"] = entry

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
        # The symbol stays claimed on this branch, even after a close the venue confirmed: an
        # entry that is not confirmed may still be filling (a partial fill is not a terminal
        # state), and what fills later is exposure nobody has booked (PR2b-2 review).
        if filled_qty > 0:
            result["reason_codes"].append(BRACKET_FAILED)
            return _close_naked_position(
                result,
                symbol=str(intent["symbol"]),
                direction=str(intent["direction"]),
                quantity=filled_qty,
                entry_price=fill_price,
                placements=[],
                identity=_naked_close_identity(
                    decision, intent, entry, quantity=filled_qty, entry_price=fill_price, now=now
                ),
                adapter=adapter,
                gate_open=gate_open,
                limits=limits,
                now=now,
                timeout_seconds=timeout_seconds,
            )
        result["status"] = ENTRY_NOT_CONFIRMED
        # The one outcome that is not open: the venue refused the submit with its own code, and
        # then answered that the order does not exist. A timeout that then reads NOT_FOUND is
        # not that — a request still on the wire can land after the read.
        if (submit_refused_outright(entry.get("submit_error"), entry.get("submit_error_detail"))
                and entry["reconcile_status"] == NOT_FOUND):
            _give_back_symbol(result, entry_marks, sent=False, **claim)
        return result

    # 2. The protective bracket, before anything is booked.
    symbol = str(intent["symbol"])
    seed = str(entry["client_order_id"])
    legs = [
        build_bracket_intent(
            symbol=symbol, leg="SL", side=bracket["stop_side"],
            price=bracket["stop_loss"], working_type=bracket["working_type"],
            position_seed=seed,
        ),
        build_bracket_intent(
            symbol=symbol, leg="TP", side=bracket["take_profit_side"],
            price=bracket["take_profit"], working_type=bracket["working_type"],
            position_seed=seed,
            # The ACTUAL fill, never the intent: the target leg is a sized reduceOnly LIMIT
            # (closePosition is unavailable on a LIMIT here), so a partial fill sized from the
            # intent would rest asking to reduce more than the position holds.
            quantity=filled_qty,
        ),
    ]
    placements = [
        place_bracket_leg(leg, adapter=adapter, timeout_seconds=timeout_seconds, sleep=sleep)
        for leg in legs
    ]
    result["bracket"] = placements

    if not all(p["placed"] for p in placements):
        # Rule 2: an unprotected live position is closed immediately, not reported and left open.
        result["reason_codes"].append(BRACKET_FAILED)
        # The entry itself was confirmed FILLED, so nothing more of it can arrive; a close the
        # venue confirmed leaves the symbol flat and unbooked.
        return _released_if_flat(_close_naked_position(
            result,
            symbol=symbol,
            direction=str(intent["direction"]),
            quantity=filled_qty,
            entry_price=fill_price,
            identity=_naked_close_identity(
                decision, intent, entry, quantity=filled_qty, entry_price=fill_price, now=now
            ),
            placements=placements,
            adapter=adapter,
            gate_open=gate_open,
            limits=limits,
            now=now,
            timeout_seconds=timeout_seconds,
        ), entry_marks, claim)

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
        strategy_generation_id=intent.get("strategy_generation_id"),
        strategy_artifact_sha256=intent.get("strategy_artifact_sha256"),
        # From the decision, not from a spec re-read — see `exit_terms` in live_entry.
        timeframe=_exit_terms(decision).get("timeframe"),
        max_holding_bars=_exit_terms(decision).get("max_holding_bars"),
        risk_snapshot_sha256=intent.get("risk_snapshot_sha256"),
        # The cycle that opened it (refactor plan J-5.1); None from a caller that does not say.
        cycle_id=cycle_id,
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
        # The book now holds the position, and the book is what refuses the next entry here.
        _give_back_symbol(result, entry_marks, sent=True, **claim)
    except Exception as exc:  # noqa: BLE001 — see _persist_failure_reason
        # The position is real and bracketed; only the local book failed. Say so loudly rather
        # than reporting a clean open — the venue and the book now disagree, and the next
        # cycle's reconciliation will refuse entries on this symbol, which is correct.
        # POSITION_PERSIST_FAILED is also what halts the fan-out (`live_route._INCIDENT_REASONS`),
        # so this catch must see the real store's failures, not just ToolError.
        result["reason_codes"].append(POSITION_PERSIST_FAILED)
        result["reason_codes"].append(_persist_failure_reason(exc))

    result["status"] = ENTRY_OPENED
    result["position"] = position
    return result


def _give_back_symbol(result: dict[str, Any], entry_marks: Any, *, sent: bool, symbol: Any,
                      client_order_id: Any) -> None:
    """Release the entry's symbol claim (PR2b-2). A failure is reported, never raised: the claim
    then expires on its own, and until it does it holds only new entries on this symbol.

    A claim that is no longer this entry's is a lost claim. Before anything was sent it costs
    nothing. After a send it means the entry outlived its claim (``sent``): an incident."""
    try:
        entry_marks.release_symbol(symbol=symbol, client_order_id=client_order_id)
    except ToolError as exc:
        if exc.reason_code != LIVE_ENTRY_CLAIM_LOST:
            result["reason_codes"].append(CLAIM_NOT_RELEASED)
        elif sent:
            result["reason_codes"].append(CLAIM_LOST)
    except Exception:  # noqa: BLE001 — the entry's outcome stands either way
        result["reason_codes"].append(CLAIM_NOT_RELEASED)


def _released_if_flat(result: dict[str, Any], entry_marks: Any, claim: Mapping[str, Any]) -> dict[str, Any]:
    """A naked close that the venue confirmed leaves nothing open and nothing booked: the symbol
    goes back. A close that failed leaves a position the book does not hold, so the claim stays."""
    if result.get("status") == ENTRY_NAKED_CLOSED and BRACKET_CANCEL_FAILED not in result["reason_codes"]:
        _give_back_symbol(result, entry_marks, sent=True, **claim)
    return result


def _naked_close_identity(
    decision: Mapping[str, Any],
    intent: Mapping[str, Any],
    entry: Mapping[str, Any],
    *,
    quantity: float,
    entry_price: float,
    now: str,
) -> dict[str, Any]:
    """Who this trade belonged to, for the outcome a naked close has to record.

    The same fields `build_live_position` reads, taken from the same places — this path runs
    BEFORE the position is booked (there is no record to read yet), so the facts are gathered
    rather than looked up. Kept as one function so the two call sites cannot drift into
    attributing the same trade differently.

    ``risk_usdt`` is **computed**, through the same :func:`position_risk_usdt` the booked path
    uses, and not read off the sizing record. It was read from ``sizing["risk_usdt"]`` when
    this landed, and nothing in the runtime has ever written that key — ``size_live_order``
    emits ``risk_per_unit``, ``risk_budget_usdt`` and ``risk_quantity``, none of them a
    position's quote risk. So the lookup returned None on every naked close, and a None here is
    not a cosmetic gap: ``build_live_outcome_record`` turns it into ``result_R: None``, and
    ``cycle.live_outcomes_for_analysis`` then DROPS R-less live rows before the weekly,
    drawdown and consecutive-loss breakers ever see them. The row reached the ledger and was
    discarded one layer above it — the same three breakers this path was reconnected for.
    Computing it from the filled price closes that, and matches the risk the exit paths record
    for a position that was booked.

    ``position_id`` is minted rather than left None, for the reason
    :func:`unbooked_position_id` gives: the outcome's ``outcome_id`` is derived from it, and a
    None made two naked closes on one symbol in one cycle collide into an id
    ``read_live_outcomes`` refuses — taking the whole live history down with it.
    """
    return {
        "position_id": unbooked_position_id(
            symbol=str(intent.get("symbol") or ""),
            entry_client_order_id=entry.get("client_order_id"),
            opened_at=now,
        ),
        "strategy_id": intent.get("strategy_id"),
        "candidate_id": (decision.get("sizing") or {}).get("candidate_id")
        or intent.get("candidate_id"),
        "strategy_rule_hash": intent.get("strategy_rule_hash"),
        "strategy_generation_id": intent.get("strategy_generation_id"),
        "strategy_artifact_sha256": intent.get("strategy_artifact_sha256"),
        "risk_snapshot_sha256": intent.get("risk_snapshot_sha256"),
        "entry_exchange_order_id": entry.get("exchange_order_id"),
        "entry_quote_usdt": _f((entry.get("fill") or {}).get("cum_quote")),
        "risk_usdt": position_risk_usdt(
            entry_price=entry_price,
            stop_loss=(decision.get("bracket") or {}).get("stop_loss"),
            quantity=quantity,
        ),
    }


def _close_naked_position(
    result: dict[str, Any],
    *,
    symbol: str,
    direction: str,
    quantity: float,
    entry_price: float,
    placements: list[dict[str, Any]],
    identity: Mapping[str, Any],
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

    **A close here is a closed trade and is recorded as one.** Until 2026-08-03 it was not:
    this branch set ``ENTRY_NAKED_CLOSED`` and returned, so a position that filled, failed to
    get its bracket and was closed again moved real money and produced no outcome row at all.
    Measured that day — two such entries moved the venue's realized P&L by -0.1196 USDT while
    ``live_closed`` read 0, so the weekly, drawdown and consecutive-loss breakers judged zero
    rows and reported NORMAL. `execute_live_exit` states the rule this branch was missing:
    a result "that cannot be recorded, or the trade would vanish from the breaker's
    accounting". This is the path most likely to be producing losses when it fires, so it is
    the worst one to be invisible.

    The record is built here and persisted by `live_route`, beside the bracket-breaker
    recording — this function takes no ledger for the same reason `execute_live_entry` takes
    none, and adding one to the entry path to serve this branch would be a wider change than
    the defect.
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
        # Keyed on the entry this close undoes (PR2c-0 review). Keyed on the symbol, the cycle's
        # time and the quantity, two naked closes of one symbol in one fan-out shared an id: the
        # second was refused as a duplicate, its read found the FIRST close and reconciled, and the
        # second position lost its stop while reported flat.
        "client_order_id": make_client_order_id(
            symbol, "CLOSE",
            make_idempotency_key({"naked": True, "position_id": identity.get("position_id"),
                                  "symbol": symbol, "at": now, "qty": quantity}),
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
    result["naked_close"] = {"guard": close_guard, "submitted": True, "result": close, "cancels": []}

    if close["reconcile_status"] == RECONCILED:
        # Withdraw whichever legs may be resting, and only now (PR2c-0): before the close was
        # confirmed, a cancel could take the one stop a still-open position had. `placements` is
        # empty when the entry itself was never confirmed, so nothing was attempted to withdraw.
        placed = {
            "stop_client_order_id": _placed_id(placements, 0),
            "take_profit_client_order_id": _placed_id(placements, 1),
            "symbol": symbol,
        }
        result["naked_close"]["cancels"] = cancel_bracket_legs(
            placed, adapter=adapter, timeout_seconds=timeout_seconds
        )
        if any(c.get("error") for c in result["naked_close"]["cancels"]):
            # A leg that would not come off may still rest: a closePosition stop closes whatever
            # the symbol holds next. Said on the record, and the symbol stays claimed.
            result["reason_codes"].append(BRACKET_CANCEL_FAILED)
        result["status"] = ENTRY_NAKED_CLOSED
        result["reason_codes"].append(NAKED_POSITION_CLOSED)
        _record_naked_outcome(
            result,
            close=close,
            symbol=symbol,
            direction=direction,
            quantity=quantity,
            entry_price=entry_price,
            identity=identity,
            now=now,
        )
    else:
        # Nothing to record: the position may still be OPEN at the venue, so there is no realized
        # figure. `ENTRY_NAKED_OPEN` is the loud state and reconciliation is what resolves it. The
        # legs stay where they are: a stop that rests is still protecting whatever is open. They
        # are named, because no book record will ever withdraw them.
        result["status"] = ENTRY_NAKED_OPEN
        result["reason_codes"].append(NAKED_CLOSE_FAILED)
        left = [leg_id for leg_id in (_placed_id(placements, 0), _placed_id(placements, 1)) if leg_id]
        result["naked_close"]["left_resting"] = left
        if left:
            result["reason_codes"].append(BRACKET_LEFT_RESTING)
    return result


# --- the exit -------------------------------------------------------------------

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
    withdraw_legs: bool = True,
) -> dict[str, Any]:
    """Close one open live position, withdraw its bracket, and record the realized outcome.

    ``withdraw_legs=False`` closes the position and leaves its legs resting (PR2c-0 review). The
    route asks for that when the venue holds more on the symbol than the book: the close covers the
    book's quantity only, and the resting stop is what still protects the rest.

    The close guard is deliberately narrower than the entry guard: a reduceOnly close is exempt
    from the loss breaker, the caps, the daily count and both kill switches,
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
        # The intent this leg built, so a caller can audit against exactly what was sent
        # rather than reconstructing it. `p5_policy_gate`'s post-action report needs the
        # order's own identity, and a second construction of it is a second chance to differ.
        "intent": None,
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

    result["intent"] = close_intent
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

    # Rule 3: withdraw the surviving leg. After the close, so a cancel can never unprotect a
    # position that is still open — and before the pricing below, which a confirmed close does
    # not need: a close that cannot be priced leaves the book for a retry, but a position that
    # was never booked (a probe's naked close) has no retry to withdraw its legs (PR2c-0).
    if withdraw_legs:
        result["cancels"] = cancel_bracket_legs(position, adapter=adapter, timeout_seconds=timeout_seconds)
        if any(c.get("error") for c in result["cancels"]):
            result["reason_codes"].append(BRACKET_CANCEL_FAILED)
    else:
        result["left_resting"] = [position[key] for key, _reason, _algo in _BRACKET_LEGS
                                  if isinstance(position.get(key), str) and position.get(key)]
        if result["left_resting"]:
            result["reason_codes"].append(BRACKET_LEFT_RESTING)

    pnl, pnl_detail = realized_pnl_usdt(position, exit_result["fill"])
    result["pnl_detail"] = pnl_detail
    if pnl is None:
        # Confirmed closed at the venue but not computable: do NOT clear the book on a result
        # that cannot be recorded, or the trade would vanish from the breaker's accounting.
        result["status"] = EXIT_NOT_CONFIRMED
        result["reason_codes"].append(FILL_FACTS_MISSING)
        return result

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
        exit_source=EXIT_SOURCE_RUNTIME_CLOSE,
        opened_at_utc=position.get("opened_at_utc"),
        # The resting trigger, so a stop close measures its own fill against it
        # (`stop_slippage_bps`) instead of the figure being reconstructed by hand later.
        stop_price=_f(position.get("stop_loss")),
        # LP5.4's bridge: without the recorded risk there is no honest R, and the bridge
        # excludes an R-less row rather than letting it read as a breakeven.
        risk_usdt=_f(position.get("risk")),
        candidate_id=position.get("candidate_id"),
        strategy_rule_hash=position.get("strategy_rule_hash"),
        strategy_generation_id=position.get("strategy_generation_id"),
        strategy_artifact_sha256=position.get("strategy_artifact_sha256"),
        risk_snapshot_sha256=position.get("risk_snapshot_sha256"),
        now=now,
    )
    result["outcome"] = outcome

    # Record the money BEFORE clearing the book: an outcome that never lands is a loss the
    # breaker will never see. The reverse failure — outcome durable, clear below fails, book
    # stays OPEN — is recoverable ONLY because the ledger append is idempotent on
    # settlement_id: the next fire's reconciliation reports the drift, re-settles, the ledger
    # skips the row it already holds, and the clear gets its retry. Without that skip the
    # retry appended a duplicate, and one duplicate settlement_id fails every verified read
    # of the history — breaker, risk guard, promotion — until an operator hand-repairs the
    # money ledger. The stale book is benign; the retry it triggers had to be made so.
    try:
        appended = ledger.append_outcome(outcome)
    except Exception as exc:  # noqa: BLE001 — see _persist_failure_reason
        result["reason_codes"].append(OUTCOME_PERSIST_FAILED)
        result["reason_codes"].append(_persist_failure_reason(exc))
        result["status"] = EXIT_NOT_CONFIRMED
        return result
    if appended is False:
        # This settle is the retry: the money row was durable before it started, so nothing
        # new was written and its remaining job is the book-clear below.
        result["reason_codes"].append(OUTCOME_ALREADY_RECORDED)

    try:
        # Only this position's record: the symbol may already hold another one (PR2b-2 review).
        position_store.clear_position(symbol, position_id=position.get("position_id"))
    except Exception as exc:  # noqa: BLE001 — see _persist_failure_reason
        result["reason_codes"].append(_persist_failure_reason(exc))

    result["status"] = EXIT_CLOSED
    return result


# --- the venue's own exit ---------------------------------------------------------
#
# The normal way a live position ends is not a close this runtime sends: it is the bracket
# already resting at the venue. LP5.3's executing leg covers the *decided* close; these two
# cover the one the venue makes on its own, which the cycle can only ever observe.
#
# Without them the routing would strand its own book on the first successful trade — the
# position is gone at the venue, the local record still says OPEN, reconciliation reports
# DRIFT forever, and that symbol refuses every later entry. A protective mechanism working
# exactly as designed would look identical to a fault.

def read_bracket_legs(
    position: Mapping[str, Any], *, adapter: Any, timeout_seconds: int = 10
) -> dict[str, Any]:
    """What the venue says about this position's two protective legs. Never raises.

    Returns ``{status, legs}`` where ``status`` is:

    - ``PROTECTED`` — both legs are resting (``NEW``), i.e. the position is covered;
    - ``UNPROTECTED`` — the venue positively answered and at least one leg is not resting;
    - ``PROTECTION_UNKNOWN`` — a query failed, or the record carries no bracket ids, so the
      venue said nothing about at least one leg.

    The three are kept distinct because they have different consequences and only one of
    them may send an order. Acting on ``PROTECTION_UNKNOWN`` would be acting on a guess —
    the same boundary ``_close_naked_position`` draws between exposure the venue *reported*
    and exposure merely suspected.
    """
    symbol = str(position.get("symbol") or "")
    legs: list[dict[str, Any]] = []
    unknown = False
    for key, close_reason, algo in _BRACKET_LEGS:
        client_order_id = position.get(key)
        leg: dict[str, Any] = {
            "leg": key,
            "close_reason": close_reason,
            "client_order_id": client_order_id if isinstance(client_order_id, str) else None,
            "status": None,
            "resting": False,
            "filled": False,
            "fill": None,
            "exchange_order_id": None,
            "error": None,
            "error_detail": None,
        }
        if not isinstance(client_order_id, str) or not client_order_id:
            leg["error"] = BRACKET_IDS_MISSING
            unknown = True
            legs.append(leg)
            continue
        try:
            venue_order = adapter.fetch_order(
                symbol, client_order_id, timeout_seconds=timeout_seconds, algo=algo
            )
        except ToolError as exc:
            leg["error"] = exc.reason_code
            leg["error_detail"] = str(exc)
            unknown = True
            legs.append(leg)
            continue
        if venue_order is None:
            # The venue answered, and its answer is "no such order": a leg that triggered,
            # was cancelled, or never landed. That is a fact, not a failed read.
            leg["status"] = "NOT_FOUND"
            legs.append(leg)
            continue
        status = str(venue_order.get("status") or "")
        leg["status"] = status
        leg["exchange_order_id"] = venue_order.get("orderId")
        leg["resting"] = status in BRACKET_RESTING_STATUSES
        facts = fill_facts(venue_order)
        leg["fill"] = facts
        leg["filled"] = status in FILLED_STATUSES and (_f(facts.get("executed_qty")) or 0.0) > 0
        legs.append(leg)

    if unknown:
        status = PROTECTION_UNKNOWN
    elif all(leg["resting"] for leg in legs):
        status = PROTECTED
    else:
        status = UNPROTECTED
    return {"status": status, "legs": legs}


def settle_venue_closed_position(
    position: Mapping[str, Any],
    *,
    adapter: Any,
    position_store: Any,
    ledger: Any,
    legs: Mapping[str, Any] | None = None,
    account_feed: Any | None = None,
    now: str,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    """Record the outcome of a position the venue's own bracket already closed.

    Sends **no order** — there is nothing left to close. The exit facts come from whichever
    bracket leg filled, read through the same ``fill_facts`` coercion every other exit uses,
    so a venue-closed trade and a runtime-closed one are the same kind of record.

    Refuses rather than invents. If no leg reports a fill, or the fill will not price, the
    book is left OPEN and the result says ``EXIT_UNSETTLEABLE``: the money moved and this
    runtime cannot say how much, which an operator must resolve. Leaving the book open keeps
    reconciliation refusing new entries on that symbol, which is the correct consequence of
    not knowing — and far better than clearing it against a fabricated result.
    """
    result: dict[str, Any] = {
        "live_leg_version": LIVE_LEG_VERSION,
        "status": EXIT_UNSETTLEABLE,
        "symbol": position.get("symbol"),
        "position_id": position.get("position_id"),
        "reason_codes": [],
        "exit": None,
        "cancels": [],
        "outcome": None,
        "venue_closed": True,
        "created_at": now,
    }
    read = dict(legs) if isinstance(legs, Mapping) else read_bracket_legs(
        position, adapter=adapter, timeout_seconds=timeout_seconds
    )
    result["bracket"] = read

    filled = next((leg for leg in read.get("legs") or [] if leg.get("filled")), None)
    exit_order_id = filled.get("exchange_order_id") if filled is not None else None
    close_reason = str(filled["close_reason"]) if filled is not None else CLOSE_REASON_VENUE_EXTERNAL
    exit_fill = filled["fill"] if filled is not None else None
    exit_source = EXIT_SOURCE_BRACKET_LEG

    pnl, pnl_detail = realized_pnl_usdt(position, exit_fill) if exit_fill else (None, {})
    if pnl is None:
        # No leg filled, or one did and its payload will not price. Both mean the same thing at
        # this point — the bracket cannot say what the exit was — and the venue's own fill list
        # can. Read it before giving up, because the alternative is a book that stays OPEN
        # forever: reconciliation then refuses new entries on this symbol and the incident halts
        # live routing everywhere, with no path back that does not involve inventing a price.
        rows = None
        if account_feed is not None:
            try:
                rows = account_feed.fill_history(
                    str(position.get("symbol") or ""),
                    start_ms=_history_start_ms(position),
                    timeout_seconds=timeout_seconds,
                )
            except ToolError as exc:
                result["reason_codes"].append(exc.reason_code)
                rows = None
        if rows is None:
            result["reason_codes"].append(FILL_HISTORY_UNAVAILABLE)
        else:
            history_fill, history_order_id = exit_fill_from_history(position, rows)
            if history_fill is None:
                result["reason_codes"].append(FILL_HISTORY_INCONCLUSIVE)
            else:
                pnl, pnl_detail = realized_pnl_usdt(position, history_fill)
                if pnl is not None:
                    exit_fill = history_fill
                    exit_order_id = history_order_id
                    # Only when NO leg filled. The fallback runs on two different facts —
                    # "the bracket did not close this" and "a leg closed it but its payload
                    # will not price" — and the second is still a strategy exit. Overwriting
                    # the reason there would take a real `stop_loss` OUT of the population the
                    # R statistics judge the strategy on, which is this label's own purpose
                    # inverted. A leg query that merely FAILED lands here too, so the test is
                    # "did a leg say it filled", never "did the bracket answer".
                    if filled is None:
                        close_reason = CLOSE_REASON_VENUE_EXTERNAL
                    exit_source = EXIT_SOURCE_FILL_HISTORY

    result["pnl_detail"] = pnl_detail
    result["exit"] = filled if filled is not None else (
        {"fill": exit_fill, "close_reason": close_reason} if exit_fill else None
    )
    if pnl is None:
        if exit_fill is not None:
            result["reason_codes"].append(FILL_FACTS_MISSING)
        result["reason_codes"].append(VENUE_CLOSE_UNSETTLEABLE)
        # Deliberately no `exit_source` on this return: nothing priced the exit, so naming a
        # source would assert a provenance for a number that does not exist.
        return result
    result["exit_source"] = exit_source
    # Rule 3 again: the leg that did NOT trigger is still resting against a position that no
    # longer exists. `cancel_bracket_legs` treats an already-gone order as a success, so the
    # triggered leg costs nothing here.
    result["cancels"] = cancel_bracket_legs(position, adapter=adapter, timeout_seconds=timeout_seconds)
    if any(c.get("error") for c in result["cancels"]):
        result["reason_codes"].append(BRACKET_CANCEL_FAILED)

    outcome = build_live_outcome_record(
        realized_pnl_usdt=pnl,
        symbol=str(position.get("symbol") or ""),
        side="SELL" if str(position.get("direction") or "").upper() == "LONG" else "BUY",
        quantity=_f(position.get("quantity")) or 0.0,
        entry_price=_f(position.get("entry_price")),
        exit_price=pnl_detail["exit_price"],
        entry_order_id=position.get("entry_exchange_order_id"),
        exit_order_id=exit_order_id,
        strategy_id=position.get("strategy_id"),
        position_id=position.get("position_id"),
        close_reason=close_reason,
        exit_source=exit_source,
        opened_at_utc=position.get("opened_at_utc"),
        # This is the path the first two real stops settled through (a leg fill, or the fill
        # history), and the path whose slippage had to be reconstructed by hand in §C — the
        # trigger rides on the row from here on so `stop_slippage_bps` is measured at source.
        stop_price=_f(position.get("stop_loss")),
        risk_usdt=_f(position.get("risk")),
        candidate_id=position.get("candidate_id"),
        strategy_rule_hash=position.get("strategy_rule_hash"),
        strategy_generation_id=position.get("strategy_generation_id"),
        strategy_artifact_sha256=position.get("strategy_artifact_sha256"),
        risk_snapshot_sha256=position.get("risk_snapshot_sha256"),
        now=now,
    )
    result["outcome"] = outcome

    # Ledger before book, for the reason `execute_live_exit` gives — and this is the path
    # the retry actually rides: a settle whose clear failed leaves the book OPEN, the next
    # fire's reconciliation reports DRIFT_MISSING_AT_VENUE, and the re-settle lands here
    # having rebuilt the SAME settlement_id (the fill-history fallback recovers the same
    # venue order). The ledger skips the duplicate (`append_outcome` returns False) and the
    # clear below gets its retry — that skip is what keeps this loop from poisoning the
    # history it reports to.
    try:
        appended = ledger.append_outcome(outcome)
    except Exception as exc:  # noqa: BLE001 — see _persist_failure_reason
        result["reason_codes"].append(OUTCOME_PERSIST_FAILED)
        result["reason_codes"].append(_persist_failure_reason(exc))
        return result
    if appended is False:
        result["reason_codes"].append(OUTCOME_ALREADY_RECORDED)

    try:
        position_store.clear_position(str(position.get("symbol") or ""),
                                      position_id=position.get("position_id"))
    except Exception as exc:  # noqa: BLE001 — see _persist_failure_reason
        result["reason_codes"].append(_persist_failure_reason(exc))

    result["status"] = EXIT_CLOSED
    return result


__all__ = [
    "BRACKET_CANCEL_FAILED",
    "BRACKET_LEFT_RESTING",
    "BRACKET_FAILED",
    "BRACKET_IDS_MISSING",
    "BRACKET_RESTING_STATUSES",
    "CLAIM_NOT_RELEASED",
    "CLOSE_REASON_NAKED",
    "CLOSE_REASON_STOP",
    "CLOSE_REASON_TARGET",
    "CLOSE_REASON_TIME_EXIT",
    "CLOSE_REASON_UNPROTECTED",
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
    "EXIT_UNSETTLEABLE",
    "FILL_FACTS_MISSING",
    "LIVE_LEG_VERSION",
    "NAKED_CLOSE_FAILED",
    "NAKED_POSITION_CLOSED",
    "NOT_READY",
    "NO_GOVERNANCE",
    "OUTCOME_ALREADY_RECORDED",
    "OUTCOME_PERSIST_FAILED",
    "POSITION_PERSIST_FAILED",
    "PROTECTED",
    "PROTECTION_UNKNOWN",
    "UNPROTECTED",
    "VENUE_CLOSE_UNSETTLEABLE",
    "bracket_error_detail",
    "build_bracket_intent",
    "cancel_bracket_legs",
    "execute_live_entry",
    "execute_live_exit",
    "leg_status_line",
    "legs_left_resting",
    "place_bracket_leg",
    "read_bracket_legs",
    "CLOSE_REASON_VENUE_EXTERNAL",
    "CLOSE_REASON_EMERGENCY",
    "EXIT_SOURCE_BRACKET_LEG",
    "EXIT_SOURCE_FILL_HISTORY",
    "EXIT_SOURCE_RUNTIME_CLOSE",
    "FILL_HISTORY_INCONCLUSIVE",
    "FILL_HISTORY_UNAVAILABLE",
    "realized_pnl_usdt",
    "exit_fill_from_history",
    "settle_venue_closed_position",
]
