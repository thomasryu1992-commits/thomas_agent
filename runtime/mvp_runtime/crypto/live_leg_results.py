"""What a live leg's result says: its statuses and reason codes, and the functions that read a result.

Moved whole out of ``live_leg`` (crypto refactor plan PR-13). ``live_leg`` re-exports every name
defined here as the same object, so ``live_leg.ENTRY_OPENED``, ``live_leg.realized_pnl_usdt`` and the
rest still resolve.

- **the vocabulary**: the entry and exit outcomes, the leg's reason codes, the close reasons and exit
  sources written onto an outcome row, the protection states, and the two status sets that say when
  a bracket leg rests and when it executed;
- **the readers**: ``realized_pnl_usdt`` (the figure from the venue's actual fills),
  ``exit_fill_from_history`` (a close rebuilt from the account's fill list),
  ``bracket_error_detail``, ``legs_left_resting``, ``leg_status_line``, and the helpers the leg uses
  to say what a persist failure, a placed leg and a naked close were.

Nothing here calls an adapter, a store or the clock, and nothing here builds or sends a request: the
functions that do are ``live_leg``'s, unchanged. ``_record_naked_outcome`` writes onto the result
mapping it is handed and nowhere else. This module imports no module that sends an order, so a
reader of results does not have to import the leg. It does load ``live_order_stores``, for the one
reason code the two share, and ``live_settlement``, for the outcome row.
"""

from __future__ import annotations

from typing import Any, Mapping

from .. import timeutil
from ..coerce import as_optional_float as _f
from .live_order_stores import LIVE_ENTRY_CLAIM_LOST
from .live_settlement import build_live_outcome_record

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
# PR2a: the two durable facts an entry now spends BEFORE it is sent. No store, no order.
NO_ENTRY_MARKS = "LIVE_ENTRY_NO_MARK_STORE"
NO_ORDER_COUNTER = "LIVE_ENTRY_NO_ORDER_COUNTER"
# PR2b: the snapshot store is required to send, like the two above.
NO_SNAPSHOT_STORE = "LIVE_ENTRY_NO_SNAPSHOT_STORE"
# The entry's claim on its symbol could not be given back (PR2b-2). It expires on its own (Thomas
# decision 21) and holds only new entries on that symbol until then.
CLAIM_NOT_RELEASED = "LIVE_ENTRY_CLAIM_NOT_RELEASED"
# The claim was no longer this entry's when it went to give it back, after its order had left: the
# entry outlived its claim, and another entry may have taken the symbol meanwhile. An incident.
CLAIM_LOST = LIVE_ENTRY_CLAIM_LOST
# The protective orders the leg would place are not the ones the approved intent carries.
BRACKET_NOT_APPROVED = "LIVE_ENTRY_BRACKET_NOT_APPROVED"
# Orders still resting at the venue on the symbol an entry has just taken, or no answer about them
# (PR2c-3, Thomas decision 25). A leg left behind — a `closePosition` stop, a reduce-only target —
# can close or shrink the next position on that symbol. The entry is refused and the operator
# withdraws what rests (`scripts/list_resting_orders.py` shows it); nothing is cancelled here.
RESTING_ORDERS = "LIVE_ENTRY_RESTING_ORDERS"
RESTING_ORDERS_UNREADABLE = "LIVE_ENTRY_RESTING_ORDERS_UNREADABLE"
ENTRY_UNCONFIRMED = "LIVE_ENTRY_UNCONFIRMED"
BRACKET_FAILED = "LIVE_BRACKET_FAILED"
NAKED_POSITION_CLOSED = "LIVE_NAKED_POSITION_CLOSED"
NAKED_CLOSE_FAILED = "LIVE_NAKED_CLOSE_FAILED"
EXIT_UNCONFIRMED = "LIVE_EXIT_UNCONFIRMED"
BRACKET_CANCEL_FAILED = "LIVE_BRACKET_CANCEL_FAILED"
# Protective legs a close deliberately left at the venue (PR2c-0 review): after a naked close the
# venue did not confirm, or a close under book drift, where a resting stop may be what still
# protects the position. The operator withdraws them once the position is resolved.
BRACKET_LEFT_RESTING = "LIVE_BRACKET_LEFT_RESTING"
FILL_FACTS_MISSING = "LIVE_FILL_FACTS_MISSING"
POSITION_PERSIST_FAILED = "LIVE_POSITION_PERSIST_FAILED"
OUTCOME_PERSIST_FAILED = "LIVE_OUTCOME_PERSIST_FAILED"
# The ledger already held this settlement, so the settle wrote no new money row. The way this
# happens: a previous settle appended the outcome and then failed to clear the book, and this
# is the reconciliation-driven retry finishing the clear. Informational, never an error —
# the durable row is the FIRST attempt's, and this attempt's remaining job is the book.
OUTCOME_ALREADY_RECORDED = "LIVE_OUTCOME_ALREADY_RECORDED"
BRACKET_IDS_MISSING = "LIVE_BRACKET_IDS_MISSING"
VENUE_CLOSE_UNSETTLEABLE = "LIVE_VENUE_CLOSE_UNSETTLEABLE"
# The two ways the fill-history fallback declines, kept apart because they mean different
# things to whoever reads the record. UNAVAILABLE is "this runtime could not ask" — no account
# grant, or the read failed — and is fixed by configuration. INCONCLUSIVE is "it asked and the
# answer does not identify this position's close", which is a statement about the account's
# activity and is NOT fixed by asking again.
FILL_HISTORY_UNAVAILABLE = "LIVE_FILL_HISTORY_UNAVAILABLE"
FILL_HISTORY_INCONCLUSIVE = "LIVE_FILL_HISTORY_INCONCLUSIVE"

# Where an exit PRICE came from. On the settle result AND on the outcome record it produced —
# the second is the one that matters, because `live_outcomes.jsonl` is what the breakers and
# every R-denominated limit read, and a consumer holding only that row could otherwise infer
# the provenance solely from `close_reason`. That inference is wrong in both directions: a
# `venue_external_close` could in principle be priced by a leg, and a `stop_loss` priced from
# the fill history is exactly what `live_leg` now produces.
#
# All three paths name themselves, so ABSENT on a row means one thing — written before this
# field existed. That is the `lifecycle_*` provenance rule: absent is "an older runtime wrote
# this", a different answer from any value, never folded into the commonest one.
EXIT_SOURCE_RUNTIME_CLOSE = "runtime_close"    # this runtime sent the closing order
EXIT_SOURCE_BRACKET_LEG = "bracket_leg"        # a resting bracket leg triggered and filled
EXIT_SOURCE_FILL_HISTORY = "fill_history"      # rebuilt from the account's own fill list

# A conditional order rests at the venue until its trigger price is reached. Only a working
# state counts as "the bracket is in place": anything else (a rejection, an instant full
# trigger, an unknown status) means the position is not protected the way the decision assumed.
#
# PARTIALLY_FILLED is working, not lost. A partial fill of the sized reduceOnly LIMIT target
# is an ordinary market event: the remainder is still on the book, and the closePosition stop
# beside it covers whatever remains by construction. Until 2026-08-17 this set was {NEW}
# alone, so that ordinary event read as a lost bracket — rule 2 then force-closed a
# still-stop-protected position at the book's stale full quantity, reconciled MISMATCH
# against the venue's reduced position, and latched a portfolio-wide incident with the book
# still OPEN. The quantity drift a partial fill creates is reconciliation's fact to report
# (BOOK_DRIFT halts entries, fail-closed), not this classifier's to answer with a taker
# close that abandons the resting maker remainder.
BRACKET_RESTING_STATUSES = frozenset({"NEW", "PARTIALLY_FILLED"})

# Terminal states that mean "this leg executed". Two spellings for one fact, because a bracket
# leg is a CONDITIONAL algo order and the Algo endpoint has its own vocabulary: the order
# endpoint says ``FILLED``, the Algo endpoint says ``FINISHED``.
#
# This check was written 2026-07-28 against the order endpoint and was not revisited when
# brackets moved to `/fapi/v1/algoOrder` on 2026-08-03, so from that day a venue-closed position
# could never be settled: the stop triggered, the venue said FINISHED, and `== "FILLED"` was
# false forever. It cost 42 cycles on ETHUSDT before anything looked.
#
# The `executed_qty > 0` conjunction at the use site is what keeps this honest — FINISHED is
# also what a cancelled algo order reports, and that one carries no `actualQty`. Terminal is
# not the same fact as executed, and only the pair means the leg filled.
FILLED_STATUSES = frozenset({"FILLED", "FINISHED"})

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
# A "protective leg" whose request could add exposure. Only `submit_and_reconcile` sends an order
# that opens exposure, under its pre-order snapshot (PR2b); this door places reducing legs only.
BRACKET_LEG_NOT_PROTECTIVE = "BRACKET_LEG_NOT_PROTECTIVE"

# Close reasons written onto the outcome record. The first two deliberately reuse paper's
# vocabulary (`trade_plan.settle_trade_plan`) so a live result and a paper result of the same shape
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

# A close this runtime did not send and no bracket leg performed — the operator flattened the
# position at the venue. Deliberately NOT one of the names above: those describe a strategy rule
# ending, and aggregating an external close into `stop_loss` or `time_exit` would put a human
# decision into the population the R statistics use to judge a strategy. It is a real outcome
# and it is recorded as one; it is simply not the strategy's.
CLOSE_REASON_VENUE_EXTERNAL = "venue_external_close"
# An operator's emergency close (PR6c, Thomas decision 49): every booked position closed at market
# under the HARD halt, on a single-use approval. A human decision again, so it is kept out of the
# strategy rules' names for the reason above; its own name, not `venue_external_close`, because this
# runtime sent it and knows exactly why.
CLOSE_REASON_EMERGENCY = "emergency_close"

# Whether this position's protective legs are still where the entry left them.
PROTECTED = "PROTECTED"
UNPROTECTED = "UNPROTECTED"
PROTECTION_UNKNOWN = "PROTECTION_UNKNOWN"

# The two stored bracket ids, and the close reason each one's fill means.
# Which stored id belongs to which leg, its close reason, and whether the venue keeps it on the
# ALGO endpoints. The third field mirrors `build_bracket_intent`'s shapes — the stop is a
# conditional STOP_MARKET and therefore an algo order since the 2025 migration; the target has
# been a plain resting LIMIT since 2026-07-28 and is not. A test pins the two in sync, because
# reading a leg on the wrong endpoint answers "no such order", which `live_leg` would take as a
# stop that never landed when it may be resting perfectly well.
_BRACKET_LEGS = (
    ("stop_client_order_id", CLOSE_REASON_STOP, True),
    ("take_profit_client_order_id", CLOSE_REASON_TARGET, False),
)


def _persist_failure_reason(exc: Exception) -> str:
    """The reason code a failed persist reports, whatever the store actually raised.

    Every post-venue persist site in `live_leg` catches broadly and reports through this, because the
    REAL stores do not fail with ``ToolError``: ``RealLivePositionStore`` and the live ledger
    fail through ``filelock.locked()`` (``PersistenceError``), through the gate re-check
    (``SafetyGateBlocked``), or through the write itself (a raw ``OSError``) — the first two
    are *siblings* of ``ToolError`` under ``MvpRuntimeError``, not subclasses. A narrow
    ``except ToolError`` therefore let the stores' actual failure modes escape to
    ``run_live_leg``'s ``MvpRuntimeError`` handler, which stamps ``ROUTE_BLOCKED`` — a status
    whose contract is "nothing was sent" — on a leg where money had already moved, and whose
    escape point could sit *before* bracket placement or the naked close, leaving a filled
    entry unprotected and unbooked while reported as a pre-venue refusal. Past the venue call
    the choice is between a recorded persist failure and an unrecorded one, so breadth is the
    point — the same posture ``live_route._record_entry_outcome`` takes on the route side.
    """
    return getattr(exc, "reason_code", None) or f"UNEXPECTED_{type(exc).__name__}"


def bracket_error_detail(entry: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """What the venue said — or did not say — about each protective leg that failed.

    `error` alone is the same string for every rejection there is, and reading only it was what
    made the first two naked entries uninvestigable once the container holding the logs was
    recreated. `error_detail` (PR #426) is the venue's numeric code and text, carried onto the
    breaker record here because that record outlives the cycle, the logs and the container.

    **A leg fails in two ways and only one of them talks.** A rejection carries a code and a
    message. A leg that never came to rest carries nothing at all — no error, no exchange id,
    just a status that is not `NEW`. The first version of this function looked only for an
    error, so the third live failure (2026-08-03T04:28:58Z, the first one after #447 moved
    conditional orders to the Algo API) recorded `last_error_detail: null`: the breaker counted
    it and could not say one word about it, which is the same gap #426 closed for the other
    half. The cycle record still held `placed: False, status: NOT_FOUND`. The durable record
    that exists to outlive the cycle did not.

    Membership is decided by `BRACKET_RESTING_STATUSES` rather than by looking for an
    error, because that frozenset is what `live_leg` itself uses to decide the bracket is in
    place. One predicate, one answer — a second definition of "this leg is fine" is how two
    files drift into disagreeing about whether a position is protected. Moved here from
    `live_route` in PR2a, when the probe door started counting its stop failures on the same
    breaker.
    """
    legs = entry.get("bracket")
    if not isinstance(legs, list):
        return None
    failed = []
    for leg in legs:
        if not isinstance(leg, Mapping):
            continue
        spoke = bool(leg.get("error") or leg.get("error_detail"))
        if leg.get("status") in BRACKET_RESTING_STATUSES and not spoke:
            continue
        failed.append({
            "leg": leg.get("leg"),
            "order_type": leg.get("order_type"),
            "placed": leg.get("placed"),
            "status": leg.get("status"),
            "error": leg.get("error"),
            "error_detail": leg.get("error_detail"),
        })
    return failed or None


def legs_left_resting(result: Mapping[str, Any]) -> list[str]:
    """The client order ids of protective legs a close left at the venue (PR2c-0): legs whose
    cancel failed, and legs kept on purpose (``left_resting``). Pure; empty when none."""
    naked = result.get("naked_close") if isinstance(result.get("naked_close"), Mapping) else {}
    ids: list[str] = []
    for cancels in (result.get("cancels"), naked.get("cancels")):
        for cancel in cancels or ():
            if isinstance(cancel, Mapping) and cancel.get("error") and cancel.get("client_order_id"):
                ids.append(str(cancel["client_order_id"]))
    for kept in (result.get("left_resting"), naked.get("left_resting")):
        ids.extend(str(leg_id) for leg_id in kept or () if leg_id)
    return sorted(set(ids))


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
        # No position was booked, but the row still needs an identity: `outcome_id` is derived
        # from it, and a None collides across two naked closes on one symbol in one cycle.
        position_id=identity.get("position_id"),
        close_reason=CLOSE_REASON_NAKED,
        exit_source=EXIT_SOURCE_RUNTIME_CLOSE,
        opened_at_utc=now,
        risk_usdt=identity.get("risk_usdt"),
        candidate_id=identity.get("candidate_id"),
        strategy_rule_hash=identity.get("strategy_rule_hash"),
        strategy_generation_id=identity.get("strategy_generation_id"),
        strategy_artifact_sha256=identity.get("strategy_artifact_sha256"),
        risk_snapshot_sha256=identity.get("risk_snapshot_sha256"),
        now=now,
    )


def realized_pnl_usdt(
    position: Mapping[str, Any], exit_fill: Mapping[str, Any]
) -> tuple[float | None, dict[str, Any]]:
    """Realized P&L from the venue's ACTUAL fills, gross of fees. Pure.

    Quote-in vs quote-out, never ``(exit - entry) * intended_qty``: a partial fill or slippage
    makes the intended numbers a fiction. Returns ``(pnl, detail)`` with ``pnl`` None when the
    figures are not there — refusing to compute is the honest answer, and the caller keeps the
    position rather than recording an invented result.

    Fees and funding are **not** included; see `live_leg`'s module docstring for why, and for which way
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

    # The two sides of the subtraction below come from different places — the exit quote from
    # the venue's fill, the entry quote from this runtime's book — and until 2026-08-22 nothing
    # asked whether they describe the same amount. They did not, once: a `closePosition`
    # STOP_MARKET is Close-All, so it closes whatever is actually open, and on
    # 2026-08-21T09:03:43Z it closed 0.002 BTC against a book that held 0.001. The difference
    # priced as a 77.5357 USDT profit on a trade that lost 0.1728, and `result_R` carried
    # +398.03 into the risk guard's weekly sum. `0.002 * 77708.50 - 0.001 * 77881.30` is that
    # number to the cent.
    #
    # Both sibling exit paths already refuse this: `order_request.reconcile_order` on
    # `abs(filled - wanted) > 1e-9` for a runtime-sent exit, and `exit_fill_from_history` on a
    # fill that overshoots the remaining quantity. The invariant is not new here — it was
    # present twice and absent once, and the once produced every `stop_loss` sample.
    #
    # Refusing returns the caller to its documented behaviour for figures it does not have:
    # `settle_venue_closed_position` leaves the book OPEN and reports `EXIT_UNSETTLEABLE`,
    # reconciliation then refuses new entries on the symbol, and an operator resolves it. That
    # is the correct consequence of not knowing what closed, and this function's own docstring
    # already says so. Tolerance mirrors `exit_fill_from_history`: what a lot-step rounding can
    # leave behind, not a licence to accept a different size. A missing `executed_qty` is left
    # alone rather than refused — it is a pre-existing gap, not this defect, and closing it here
    # would refuse fills that price correctly today.
    if exit_qty is not None and quantity > 0:
        if abs(exit_qty - quantity) > max(quantity * 1e-9, 1e-9):
            detail["quantity_mismatch"] = {"book": quantity, "venue_filled": exit_qty}
            return None, detail

    direction = str(position.get("direction") or "").upper()
    if direction == "LONG":
        pnl = exit_quote - entry_quote
    elif direction == "SHORT":
        pnl = entry_quote - exit_quote
    else:
        return None, detail
    return round(pnl, 8), detail


def _history_start_ms(position: Mapping[str, Any]) -> int:
    """Where to start the venue's fill query for this position.

    The open, minus a minute. The margin is for clock skew between this runtime's recorded
    open and the venue's own stamp on the entry fill — a start time a few hundred milliseconds
    late would drop the very fills being looked for. Widening it costs nothing: everything
    before the open is filtered out again in :func:`exit_fill_from_history`, which compares
    against the open itself rather than against this bound.
    """
    opened = position.get("opened_at_utc")
    if isinstance(opened, str) and opened:
        try:
            return int(timeutil.parse_iso(opened).timestamp() * 1000) - 60_000
        except (ValueError, TypeError, OSError):
            pass
    return 0


def exit_fill_from_history(
    position: Mapping[str, Any], rows: Any
) -> tuple[dict[str, Any] | None, str | None]:
    """The closing fill, rebuilt from the account's own fill history. Pure.

    Returns ``(fill_facts, exit_order_id)`` in the shape :func:`fill_facts` produces, so the
    caller prices it through the same :func:`realized_pnl_usdt` every other exit uses — an
    externally closed trade and a bracket-closed one become the same kind of record. Returns
    ``(None, None)`` whenever the history cannot answer **unambiguously**, which is most of the
    ways it can fail.

    This exists because the bracket legs cannot answer at all when the position was closed by
    something other than the bracket. An operator flattening at the venue leaves both legs
    EXPIRED with no fill, so :func:`settle_venue_closed_position` had nothing to price and the
    book stayed OPEN forever — reconciliation then refuses new entries on that symbol and the
    incident halts live routing everywhere. The venue's own fill list is the authority that was
    always there and was never read.

    **This is not the "invents rather than refuses" path the caller warns about.** Every number
    here comes from the venue's record of what it filled. What is added is a rule for deciding
    WHICH fills closed this position, and that rule refuses instead of guessing:

    - only fills on the CLOSING side (SELL closes a LONG), and only at or after the open;
    - taken oldest-first, accumulating until they exactly cover the position's quantity;
    - a fill that would overshoot the remaining quantity refuses the whole answer — it cannot
      be part of this position's close, and its presence means the symbol saw activity this
      function cannot attribute;
    - a total that never reaches the quantity refuses — a partial close is still an open
      position, and clearing the book on one would hide real exposure.

    ``rows`` is what ``AccountFeed.fill_history`` returned: ``None`` means no feed (a different
    statement from an empty list, which honestly means the account has no fills) and both
    refuse here, for different reasons that the caller records separately.
    """
    if not isinstance(rows, list) or not rows:
        return None, None
    quantity = _f(position.get("quantity")) or 0.0
    if quantity <= 0:
        return None, None
    closing_side = "SELL" if str(position.get("direction") or "").upper() == "LONG" else "BUY"

    opened_ms = None
    opened = position.get("opened_at_utc")
    if isinstance(opened, str) and opened:
        try:
            opened_ms = int(timeutil.parse_iso(opened).timestamp() * 1000)
        except (ValueError, TypeError, OSError):
            return None, None
    if opened_ms is None:
        return None, None

    candidates = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("side") or "").upper() != closing_side:
            continue
        stamp = _f(row.get("time"))
        if stamp is None or stamp < opened_ms:
            continue
        qty = _f(row.get("qty"))
        quote = _f(row.get("quoteQty"))
        if qty is None or qty <= 0 or quote is None or quote < 0:
            return None, None  # a malformed row makes the whole history unusable, not skippable
        candidates.append((stamp, qty, quote, row.get("orderId")))
    if not candidates:
        return None, None

    candidates.sort(key=lambda c: c[0])
    remaining = quantity
    taken: list[tuple[float, float, float, Any]] = []
    # Quantities are decimal strings on the wire and floats here; the tolerance is what a
    # lot-step rounding can leave behind, not a licence to accept a different size.
    tolerance = max(quantity * 1e-9, 1e-9)
    for entry in candidates:
        if entry[1] > remaining + tolerance:
            return None, None  # overshoot: not this position's close
        taken.append(entry)
        remaining -= entry[1]
        if remaining <= tolerance:
            break
    if remaining > tolerance:
        return None, None  # never covered the position

    executed_qty = sum(t[1] for t in taken)
    cum_quote = sum(t[2] for t in taken)
    if executed_qty <= 0 or cum_quote <= 0:
        return None, None
    order_ids = {t[3] for t in taken if t[3] is not None}
    return (
        {
            "avg_price": round(cum_quote / executed_qty, 8),
            "cum_quote": round(cum_quote, 8),
            "executed_qty": round(executed_qty, 8),
        },
        next(iter(order_ids)) if len(order_ids) == 1 else None,
    )


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
