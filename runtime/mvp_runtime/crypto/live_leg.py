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

**The crypto cycle now calls this**, through exactly one module: ``crypto/live_route.py``. That
wiring was the step the design record sequenced last, because it is the line which makes an
autonomous live order reachable — so it is deliberately a single chokepoint rather than a call
from the cycle itself, and a test pins that no entry point reaches this module by any other
route. What still stands between the wiring and an order is the gate: ``live_route`` runs
nothing at all unless the ``live_trading`` grant is active on the machine, and every door below
it (the guard, the phrase, the registered budget, the canary evidence) is unchanged.

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
  (``paper.settle_trade_plan`` returns the target price itself as the exit), so this closes a
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
from .live_execution import (
    CONDITIONAL_ORDER_TYPES,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_STOP_MARKET,
    TIME_IN_FORCE_GTC,
    fill_facts,
    submit_and_reconcile,
)
from .live_order import evaluate_live_close_guard, make_client_order_id, make_idempotency_key
from .live_pnl import build_live_outcome_record
from .live_position import build_live_position, position_risk_usdt
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
EXIT_UNSETTLEABLE = "EXIT_UNSETTLEABLE"        # the venue closed it and cannot say at what

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
BRACKET_IDS_MISSING = "LIVE_BRACKET_IDS_MISSING"
VENUE_CLOSE_UNSETTLEABLE = "LIVE_VENUE_CLOSE_UNSETTLEABLE"

# A conditional order rests at the venue until its trigger price is reached. Only that resting
# state counts as "the bracket is in place": anything else (a rejection, an instant trigger, an
# unknown status) means the position is not protected the way the decision assumed.
BRACKET_RESTING_STATUSES = frozenset({"NEW"})

# The leg's status when the venue ACCEPTED the submit and then could not find the order.
#
# Measured 2026-08-03T04:28:58Z, on the first live bracket after the Algo migration: the POST to
# `/fapi/v1/algoOrder` raised nothing — no code, no message — and the query that followed
# answered "does not exist", so the leg recorded `placed: false`, `error: null`,
# `error_detail: null`. A silent failure, and the worst kind: `_close_naked_position` cancels
# what PLACED, so the stop was never withdrawn. If that order was in fact resting, it is resting
# still, unowned, counting against the symbol's conditional cap and able to trigger on a
# position it was never meant to protect.
#
# The two sources disagreed and the code believed only one of them. This status is what that
# disagreement is called, so it can never again be filed as an ordinary "did not place".
BRACKET_QUERY_MISSING = "SUBMIT_CONFIRMED_QUERY_MISSING"

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

# Close reasons written onto the outcome record. The first two deliberately reuse paper's
# vocabulary (`paper.settle_trade_plan`) so a live result and a paper result of the same shape
# read identically to every consumer — the R statistics are compared across the two.
CLOSE_REASON_NAKED = "naked_position_close"
CLOSE_REASON_STOP = "stop_loss"
CLOSE_REASON_TARGET = "take_profit"
CLOSE_REASON_UNPROTECTED = "unprotected_position_close"
# Paper's vocabulary again, and here the shared name is the point rather than a courtesy: a
# live time exit and a paper time exit are the same strategy rule ending the same way, so they
# must aggregate into one bucket. What is NOT identical is the price — paper models the exit at
# the bar's close, live pays taker plus slippage on a reduceOnly market order. The rule matches;
# the cost does not, and `r_basis` keeps the two populations labelled.
CLOSE_REASON_TIME_EXIT = "time_exit"

# Whether this position's protective legs are still where the entry left them.
PROTECTED = "PROTECTED"
UNPROTECTED = "UNPROTECTED"
PROTECTION_UNKNOWN = "PROTECTION_UNKNOWN"

# The two stored bracket ids, and the close reason each one's fill means.
# Which stored id belongs to which leg, its close reason, and whether the venue keeps it on the
# ALGO endpoints. The third field mirrors `build_bracket_intent`'s shapes — the stop is a
# conditional STOP_MARKET and therefore an algo order since the 2025 migration; the target has
# been a plain resting LIMIT since 2026-07-28 and is not. A test pins the two in sync, because
# reading a leg on the wrong endpoint answers "no such order", which this module would take as a
# stop that never landed when it may be resting perfectly well.
_BRACKET_LEGS = (
    ("stop_client_order_id", CLOSE_REASON_STOP, True),
    ("take_profit_client_order_id", CLOSE_REASON_TARGET, False),
)


def _exit_terms(decision: Mapping[str, Any]) -> Mapping[str, Any]:
    """The plan's exit terms as the decision recorded them, or empty.

    Empty is a legitimate answer, not a defect: a decision built before `exit_terms` existed
    carries none, and `build_live_position` stores `None` for both. What that produces is a
    *legacy* position, which `paper.position_max_hold` already knows how to judge — timeframe
    table fallback, with the fallback reported so the gap stays attributable.
    """
    terms = decision.get("exit_terms")
    return terms if isinstance(terms, Mapping) else {}


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
    governance: Mapping[str, Any] | None = None,
    gate_open: bool,
    limits: Any,
    now: str,
    timeout_seconds: int = 10,
    sleep: Any = time.sleep,
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
                identity=_naked_close_identity(
                    decision, intent, entry, quantity=filled_qty, entry_price=fill_price
                ),
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
        return _close_naked_position(
            result,
            symbol=symbol,
            direction=str(intent["direction"]),
            quantity=filled_qty,
            entry_price=fill_price,
            identity=_naked_close_identity(
                decision, intent, entry, quantity=filled_qty, entry_price=fill_price
            ),
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
        strategy_generation_id=intent.get("strategy_generation_id"),
        # From the decision, not from a spec re-read — see `exit_terms` in live_entry.
        timeframe=_exit_terms(decision).get("timeframe"),
        max_holding_bars=_exit_terms(decision).get("max_holding_bars"),
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
    """The client order id of a bracket leg that may be at the venue, else None.

    ``placed`` OR ``may_be_resting``, and the second half is the fix for 2026-08-03: a leg whose
    submit was accepted and whose query then missed it was cancelled by nobody, because this
    function asked only whether it had been CONFIRMED. Cancelling an order that does not exist
    costs a "-2011 unknown order" the adapter already reads as *already gone*; NOT cancelling one
    that does exist leaves an unowned protective order resting at the venue. The two mistakes
    are not the same size."""
    if index >= len(placements):
        return None
    leg = placements[index]
    if not (leg.get("placed") or leg.get("may_be_resting")):
        return None
    return leg.get("client_order_id")


def _naked_close_identity(
    decision: Mapping[str, Any],
    intent: Mapping[str, Any],
    entry: Mapping[str, Any],
    *,
    quantity: float,
    entry_price: float,
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
    """
    return {
        "strategy_id": intent.get("strategy_id"),
        "candidate_id": (decision.get("sizing") or {}).get("candidate_id")
        or intent.get("candidate_id"),
        "strategy_rule_hash": intent.get("strategy_rule_hash"),
        "strategy_generation_id": intent.get("strategy_generation_id"),
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
        # Nothing to record: the position is still OPEN at the venue, so there is no realized
        # figure. `ENTRY_NAKED_OPEN` is the loud state and reconciliation is what resolves it.
        result["status"] = ENTRY_NAKED_OPEN
        result["reason_codes"].append(NAKED_CLOSE_FAILED)
    return result


def _record_naked_outcome(
    result: dict[str, Any],
    *,
    close: Mapping[str, Any],
    symbol: str,
    direction: str,
    quantity: float,
    entry_price: float,
    identity: Mapping[str, Any],
    now: str,
) -> None:
    """Build the outcome row for a naked close onto ``result["outcome"]``.

    ``realized_pnl_usdt`` is reused rather than re-derived — it reads only quantity, entry
    price, entry quote and direction, all of which this path has, so the position mapping it
    wants is assembled instead of a second copy of the arithmetic being written.

    An uncomputable figure records NOTHING and says so with `FILL_FACTS_MISSING`. The
    position is closed at the venue either way, so unlike `execute_live_exit` there is no
    book to keep — but inventing a number to fill the row would put a fiction into the
    breaker's accounting, which is worse than the gap this function exists to close.
    """
    pnl, pnl_detail = realized_pnl_usdt(
        {
            "quantity": quantity,
            "entry_price": entry_price,
            "entry_quote_usdt": identity.get("entry_quote_usdt"),
            "direction": direction,
        },
        close.get("fill") or {},
    )
    result["pnl_detail"] = pnl_detail
    if pnl is None:
        result["reason_codes"].append(FILL_FACTS_MISSING)
        return
    result["outcome"] = build_live_outcome_record(
        realized_pnl_usdt=pnl,
        symbol=symbol,
        side="SELL" if direction.upper() == "LONG" else "BUY",
        quantity=quantity,
        entry_price=entry_price,
        exit_price=pnl_detail["exit_price"],
        entry_order_id=identity.get("entry_exchange_order_id"),
        exit_order_id=close.get("exchange_order_id"),
        strategy_id=identity.get("strategy_id"),
        # No position was booked, so there is no position_id to carry. The settlement id is
        # derived from the identity it does have; a naked close happens once per entry.
        position_id=None,
        close_reason=CLOSE_REASON_NAKED,
        opened_at_utc=now,
        risk_usdt=identity.get("risk_usdt"),
        candidate_id=identity.get("candidate_id"),
        strategy_rule_hash=identity.get("strategy_rule_hash"),
        strategy_generation_id=identity.get("strategy_generation_id"),
        now=now,
    )


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
        leg["filled"] = status == "FILLED" and (_f(facts.get("executed_qty")) or 0.0) > 0
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
    if filled is None:
        result["reason_codes"].append(VENUE_CLOSE_UNSETTLEABLE)
        return result

    pnl, pnl_detail = realized_pnl_usdt(position, filled["fill"])
    result["pnl_detail"] = pnl_detail
    result["exit"] = filled
    if pnl is None:
        result["reason_codes"].append(FILL_FACTS_MISSING)
        result["reason_codes"].append(VENUE_CLOSE_UNSETTLEABLE)
        return result

    close_reason = str(filled["close_reason"])
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
        exit_order_id=filled.get("exchange_order_id"),
        strategy_id=position.get("strategy_id"),
        position_id=position.get("position_id"),
        close_reason=close_reason,
        opened_at_utc=position.get("opened_at_utc"),
        risk_usdt=_f(position.get("risk")),
        candidate_id=position.get("candidate_id"),
        strategy_rule_hash=position.get("strategy_rule_hash"),
        strategy_generation_id=position.get("strategy_generation_id"),
        now=now,
    )
    result["outcome"] = outcome

    # Ledger before book, for the reason `execute_live_exit` gives: an outcome that never
    # lands is a loss the breaker will never see.
    try:
        ledger.append_outcome(outcome)
    except ToolError as exc:
        result["reason_codes"].append(OUTCOME_PERSIST_FAILED)
        result["reason_codes"].append(exc.reason_code)
        return result

    try:
        position_store.clear_position(str(position.get("symbol") or ""))
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
    "BRACKET_IDS_MISSING",
    "BRACKET_RESTING_STATUSES",
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
    "OUTCOME_PERSIST_FAILED",
    "POSITION_PERSIST_FAILED",
    "PROTECTED",
    "PROTECTION_UNKNOWN",
    "UNPROTECTED",
    "VENUE_CLOSE_UNSETTLEABLE",
    "build_bracket_intent",
    "cancel_bracket_legs",
    "execute_live_entry",
    "execute_live_exit",
    "leg_status_line",
    "place_bracket_leg",
    "read_bracket_legs",
    "realized_pnl_usdt",
    "settle_venue_closed_position",
]
