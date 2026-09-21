"""The order's shape at the venue, both ways: the request built from an intent, and the verdict on what
the venue says it did (crypto PR7e-4).

`build_order_request` turns a guard-approved intent into the venue's order params (the entry, the
reduce-only close, the conditional stop and target, the resting take-profit LIMIT) and refuses an intent
it cannot express rather than guess a field. `is_algo_request` says which of those belong on the Algo
endpoints, and `is_protective_request` which can only reduce or close a position. On the way back,
`normalize_algo_order` puts an algo order in the field names the rest of the runtime speaks,
`reconcile_order` compares the venue's order with the intent and names every field that differs, and
`fill_facts` reads the venue's own fill numbers. The vocabulary they share is here too: the order,
working and time-in-force types, the client-order-id charset, the reconcile verdicts, and the two
refusals this code raises itself.

All of it is pure: no network, no clock, no state. It lived in `live_execution` beside the adapters that
sign and send. That module keeps the egress (the adapters, their selection, the halt backstop) and the
send-and-reconcile loop that joins the two sides, and re-exports every name here as the same object.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from ..errors import ToolError

# The only `algoType` the venue defines for these; sent on every place, and the discriminator
# `is_algo_request` reads to pick the endpoint. Using the venue's own field rather than a local
# flag means the request cannot say one thing and be routed by another.
ALGO_TYPE_CONDITIONAL = "CONDITIONAL"

# Order types LP4 can express. MARKET is the entry/close; the two conditional types are the
# LP5 protective bracket. Verified against the venue's New Order contract (2026-07-25):
# a conditional type carries a ``stopPrice``, and ``workingType`` selects the trigger price.
#
# LIMIT joined them on 2026-07-28, for the take-profit leg only. A ``TAKE_PROFIT_MARKET``
# triggers into a market order and therefore always pays the TAKER rate plus adverse
# slippage, while a target is by construction a price the market has to come TO — the one
# exit that can rest as a maker. See ``cost.DEFAULT_MAKER_FEE_BPS`` for what that is worth
# and ``live_leg`` for the shape change it forces (a target leg can no longer be Close-All).
ORDER_TYPE_MARKET = "MARKET"
ORDER_TYPE_LIMIT = "LIMIT"
ORDER_TYPE_STOP_MARKET = "STOP_MARKET"
ORDER_TYPE_TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"
CONDITIONAL_ORDER_TYPES = frozenset({ORDER_TYPE_STOP_MARKET, ORDER_TYPE_TAKE_PROFIT_MARKET})
SUPPORTED_ORDER_TYPES = frozenset({ORDER_TYPE_MARKET, ORDER_TYPE_LIMIT}) | CONDITIONAL_ORDER_TYPES
# Types that REST at the venue rather than executing on submission. A conditional order waits
# for its trigger; a LIMIT waits for the market to reach its price. Neither reconciles as
# FILLED at placement time, which is what ``place_bracket_leg`` and the dry-run echo both
# depend on.
RESTING_ORDER_TYPES = frozenset({ORDER_TYPE_LIMIT}) | CONDITIONAL_ORDER_TYPES
WORKING_TYPE_MARK_PRICE = "MARK_PRICE"
WORKING_TYPE_CONTRACT_PRICE = "CONTRACT_PRICE"
WORKING_TYPES = frozenset({WORKING_TYPE_MARK_PRICE, WORKING_TYPE_CONTRACT_PRICE})

# ``timeInForce``, mandatory on a LIMIT at this venue. GTC is what the take-profit leg uses:
# it rests as a maker while the target is away from the market, and if the market is already
# through the target it crosses and pays taker — which is exactly what the
# ``TAKE_PROFIT_MARKET`` it replaces would have done, so this branch is never WORSE than the
# order it replaces. GTX (post-only) would guarantee the maker rate but adds a rejection
# branch on a target that is already through, so it is deliberately not the default.
TIME_IN_FORCE_GTC = "GTC"
TIMES_IN_FORCE = frozenset({TIME_IN_FORCE_GTC, "IOC", "FOK", "GTX"})

# The venue's own charset rule for newClientOrderId, verified from its New Order contract:
# ``^[\.A-Z\:/a-z0-9_-]{1,36}$``. ``make_client_order_id`` already complies; validating here
# means a hand-built intent cannot get rejected at the venue for a character.
CLIENT_ORDER_ID_PATTERN = re.compile(r"\A[.A-Z:/a-z0-9_-]{1,36}\Z")

# reconcile_status vocabulary. RECONCILED is also what the historical canary rows derived `clean` from
# (clean iff RECONCILED and no mismatch). It was defined in live_promotion, beside those rows, until
# crypto PR7b-2 moved it into live_execution, below the ledger that reads it; since crypto PR7e-4 it is
# defined here and live_execution re-exports it. live_promotion and the live leg import it through
# live_execution, so the rows and this vocabulary still agree by construction, and
# scripts/run_slippage_probe.py reads it through live_promotion.
RECONCILED = "RECONCILED"
MISMATCH = "MISMATCH"
NOT_FOUND = "NOT_FOUND"
UNRECONCILABLE = "UNRECONCILABLE"

# The two refusals this module's code raises: an intent the builder cannot turn into a request
# (nothing is sent), and an open-orders answer `_order_rows` cannot read. `ORDER_MALFORMED_RESULT` is
# shared: the adapters in `live_execution` raise it too, for any venue answer they cannot parse. It is
# defined here because `_order_rows` raises it and this module must not import `live_execution`, which
# would be a cycle.
MALFORMED_INTENT = "MALFORMED_LIVE_ORDER_INTENT"
ORDER_MALFORMED_RESULT = "ORDER_MALFORMED_RESULT"


def is_protective_request(order_request: Mapping[str, Any]) -> bool:
    """Whether a built order request can only reduce or close a position: ``reduceOnly``, or a
    Close-All ``closePosition`` conditional. The one spelling of the shape every halt lets through
    — the bracket leg's own check (`live_leg.place_bracket_leg`) and the adapter's egress refusal
    (`control_refusal`) read it here."""
    return order_request.get("reduceOnly") is True or order_request.get("closePosition") == "true"


def build_order_request(intent: Mapping[str, Any]) -> dict[str, Any]:
    """The venue order params from a guard-approved intent. Fail-closed on a malformed intent.

    ``reduceOnly`` is set **from the intent** — the structural boundary the close guard relies
    on: a "close" that dropped this flag could open a position, so LP4 carries it faithfully.

    Supported types (``order_type_exchange``): ``MARKET`` for an entry or a close, ``STOP_MARKET``
    / ``TAKE_PROFIT_MARKET`` for a conditional bracket leg, and ``LIMIT`` for the resting
    take-profit leg. A conditional type **requires** a positive ``stop_price``: the venue lists
    ``stopPrice`` as optional across all types, but a conditional order without one is
    meaningless, so it is required here rather than sent empty and rejected at the venue. A
    ``LIMIT`` likewise requires a positive ``price`` and an explicit ``time_in_force`` — the
    venue makes both mandatory, and defaulting a time-in-force would be this module choosing how
    long real money rests at a price.

    Three venue constraints are enforced rather than discovered at run time (verified against
    the New Order contract, 2026-07-25):

    - ``closePosition=true`` is **mutually exclusive with both ``quantity`` and ``reduceOnly``**,
      and is only valid on a conditional type. So a close-all bracket leg sends neither.
    - ``closePosition`` is documented for ``STOP_MARKET``/``TAKE_PROFIT_MARKET`` **only**, so a
      ``LIMIT`` take-profit leg cannot be Close-All and must carry an explicit quantity. That is
      not a detail: it is why ``live_leg`` sizes the target leg from the actual entry fill.
    - ``newClientOrderId`` must match the venue's charset (``CLIENT_ORDER_ID_PATTERN``).
    """
    symbol = intent.get("symbol")
    side = intent.get("side")
    client_order_id = intent.get("client_order_id")
    order_type = intent.get("order_type_exchange")
    close_position = bool(intent.get("close_position"))

    if not (isinstance(symbol, str) and symbol):
        raise ToolError(MALFORMED_INTENT, "order intent is missing a symbol")
    if side not in ("BUY", "SELL"):
        raise ToolError(MALFORMED_INTENT, f"order intent side must be BUY or SELL, got {side!r}")
    if not (isinstance(client_order_id, str) and CLIENT_ORDER_ID_PATTERN.match(client_order_id)):
        raise ToolError(
            MALFORMED_INTENT,
            "order intent needs a client_order_id matching the venue charset "
            "(1-36 of A-Z a-z 0-9 . : / _ -); run enrich_order_identity",
        )
    if order_type not in SUPPORTED_ORDER_TYPES:
        raise ToolError(
            MALFORMED_INTENT,
            f"order_type_exchange must be one of {sorted(SUPPORTED_ORDER_TYPES)}, got {order_type!r}",
        )

    algo = order_type in CONDITIONAL_ORDER_TYPES
    request: dict[str, Any] = {
        "symbol": symbol,
        "side": side,
        "type": order_type,
    }
    # The identity field is named differently on the two endpoints, and it is the SAME id: the
    # idempotency key the caller already minted. Only the spelling changes with the endpoint.
    request["clientAlgoId" if algo else "newClientOrderId"] = client_order_id

    if algo:
        request["algoType"] = ALGO_TYPE_CONDITIONAL
        stop_price = intent.get("stop_price")
        if not (isinstance(stop_price, (int, float)) and stop_price > 0):
            raise ToolError(
                MALFORMED_INTENT,
                f"{order_type} needs a positive stop_price (a conditional order without a "
                "trigger is meaningless)",
            )
        # `stopPrice` on the old endpoint, `triggerPrice` on this one. The intent field keeps its
        # name — it describes the strategy's stop, not the venue's spelling of it.
        request["triggerPrice"] = float(stop_price)
        working_type = intent.get("working_type")
        if working_type is not None:
            if working_type not in WORKING_TYPES:
                raise ToolError(
                    MALFORMED_INTENT,
                    f"working_type must be one of {sorted(WORKING_TYPES)}, got {working_type!r}",
                )
            request["workingType"] = working_type
    elif close_position:
        # closePosition is a Close-All conditional-order behaviour; on a MARKET or LIMIT order it
        # is not a thing the venue accepts, so refuse rather than send something that would be
        # rejected. A LIMIT take-profit leg therefore carries a real quantity, which is the whole
        # reason ``live_leg`` has to know the filled size before it can build one.
        raise ToolError(
            MALFORMED_INTENT,
            f"close_position is only valid on {sorted(CONDITIONAL_ORDER_TYPES)}, not {order_type}",
        )

    if order_type == ORDER_TYPE_LIMIT:
        price = intent.get("price")
        if not (isinstance(price, (int, float)) and price > 0):
            raise ToolError(
                MALFORMED_INTENT,
                "LIMIT needs a positive price (a resting order without one is meaningless)",
            )
        request["price"] = float(price)
        time_in_force = intent.get("time_in_force")
        if time_in_force not in TIMES_IN_FORCE:
            raise ToolError(
                MALFORMED_INTENT,
                f"time_in_force must be one of {sorted(TIMES_IN_FORCE)}, got {time_in_force!r}",
            )
        request["timeInForce"] = time_in_force

    if close_position:
        # Mutually exclusive with quantity AND reduceOnly — send neither.
        request["closePosition"] = "true"
    else:
        quantity = intent.get("quantity")
        if not (isinstance(quantity, (int, float)) and quantity > 0):
            raise ToolError(MALFORMED_INTENT, "order intent needs a positive quantity")
        request["quantity"] = float(quantity)
        request["reduceOnly"] = bool(intent.get("reduce_only"))
    return request


def is_algo_request(order_request: Mapping[str, Any]) -> bool:
    """Whether this request belongs on the Algo Order endpoints.

    Reads the venue's own ``algoType`` rather than a flag this repo invented, so a request
    cannot be shaped one way and routed the other — the failure that would turn a fixed stop
    into a silently-refused one all over again."""
    return bool(order_request.get("algoType"))


def normalize_algo_order(venue_order: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """An algo order in the field names the rest of this runtime already speaks.

    The Algo endpoints answer with ``algoId``/``algoStatus``/``triggerPrice`` where the order
    endpoints answer with ``orderId``/``status``/``stopPrice``. Translated ONCE, at the wire
    boundary (the adapters in ``live_execution`` call this on every Algo answer), so
    `reconcile_order`, `place_bracket_leg` and `read_bracket_legs` keep one
    vocabulary — teaching every caller two spellings is how one of them ends up checking the
    wrong field and reading a refused stop as a resting one.

    **The fill facts are here too, and that half was missing.** A CONDITIONAL algo order that
    triggered reports what actually executed under ``actualPrice``/``actualQty`` — the child
    order's average price and filled quantity — where the order endpoint says
    ``avgPrice``/``executedQty``. Those two were not translated, so ``fill_facts`` read an algo
    fill as all-``None`` and `live_leg` could not price an exit the venue had already made.

    Measured 2026-08-05T17:38Z on ETHUSDT: the stop triggered, the venue answered
    ``algoStatus: FINISHED, actualPrice: 1904.96, actualQty: 0.022``, and settlement refused for
    42 consecutive cycles because the numbers it needed were sitting in fields nothing read.
    The position stayed OPEN in the local book against a venue that no longer had it, which
    held every ETHUSDT entry behind a reconciliation DRIFT.

    ``cumQuote`` is deliberately NOT synthesised from the two. ``realized_pnl_usdt`` already
    falls back to ``qty * price`` when the quote is absent, and a computed value written into a
    field the venue names would be indistinguishable from one the venue sent — which is exactly
    the confusion the "never lossy" rule below exists to prevent.

    **Additive, never lossy.** The venue's own keys are kept alongside the aliases, because the
    raw response is what lands in the ledger and an incident is reconstructed from it — this
    session reconstructed the 2026-08-02 request byte-for-byte only because nothing had been
    helpfully tidied away first. An alias is written only when the venue supplied the source
    field, so a missing value stays missing rather than becoming a confident default."""
    if venue_order is None:
        return None
    out = dict(venue_order)
    for source, alias in (("algoId", "orderId"), ("algoStatus", "status"),
                          ("triggerPrice", "stopPrice"),
                          ("actualPrice", "avgPrice"), ("actualQty", "executedQty")):
        if source in out and alias not in out:
            out[alias] = out[source]
    return out


def _order_rows(body: Any, label: str) -> list[dict[str, Any]]:
    """A venue order list, or ``ORDER_MALFORMED_RESULT``. A body that is not a list of objects is
    not "nothing is resting": a caller deciding whether an entry may go must not read it as empty
    (review of #888)."""
    if not (isinstance(body, list) and all(isinstance(row, dict) for row in body)):
        raise ToolError(ORDER_MALFORMED_RESULT, f"the {label} query returned something other than a list of orders")
    return list(body)


def reconcile_order(
    intent: Mapping[str, Any], venue_order: Mapping[str, Any] | None
) -> tuple[str, list[str]]:
    """Compare the venue's order state against the intent → ``(reconcile_status, mismatches)``.

    ``NOT_FOUND`` when the venue has no such order (the submit did not land). Otherwise
    ``RECONCILED`` iff symbol, side, filled quantity, the reduceOnly flag, and ``status ==
    FILLED`` all match; else ``MISMATCH`` with each divergence named. A wrong-size or wrong-side
    fill is a stop-everything signal, so it is surfaced, never smoothed over."""
    if venue_order is None:
        return NOT_FOUND, ["venue has no order for this client_order_id"]
    problems: list[str] = []
    if str(venue_order.get("symbol")) != str(intent.get("symbol")):
        problems.append(f"symbol {venue_order.get('symbol')!r} != {intent.get('symbol')!r}")
    if str(venue_order.get("side")) != str(intent.get("side")):
        problems.append(f"side {venue_order.get('side')!r} != {intent.get('side')!r}")
    status = str(venue_order.get("status"))
    if status != "FILLED":
        problems.append(f"status {status!r} != FILLED")
    try:
        filled = float(venue_order.get("executedQty"))
        wanted = float(intent.get("quantity"))
        if abs(filled - wanted) > 1e-9:
            problems.append(f"executedQty {filled} != intent quantity {wanted}")
    except (TypeError, ValueError):
        problems.append("executedQty missing or non-numeric")
    if bool(venue_order.get("reduceOnly")) != bool(intent.get("reduce_only")):
        problems.append("reduceOnly flag does not match the intent")
    return (RECONCILED if not problems else MISMATCH), problems


def _intended_price(intent: Mapping[str, Any]) -> float | None:
    """The plan's own entry price, or None. Never a substitute figure.

    A plan that carried no entry price makes its fill unmeasurable, and that is the honest
    record — filling in the venue's own fill would make every such row read as zero slippage,
    which is the flattering direction and exactly the shape `cost.outcome_net_r` refuses in its
    own domain. A non-positive value is treated as absent for the same reason.
    """
    value = intent.get("entry_price")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def fill_facts(venue_order: Mapping[str, Any] | None) -> dict[str, Any]:
    """The venue's own fill numbers, coerced to floats where they parse.

    Public because LP5.3's cycle routing settles a position the venue's own bracket closed:
    the exit facts then come from querying the *bracket leg*, not from a submit this runtime
    made, and reading them through anything but this one coercion would be a second opinion
    about what the venue said.

    ``avgPrice`` is the real average fill price and ``cumQuote`` the filled notional — the two
    figures a truthful ``realized_pnl_usdt`` has to come from. A field that will not parse is
    reported as ``None`` rather than zero: a missing fill price must not read as a free trade."""
    if not isinstance(venue_order, Mapping):
        return {"avg_price": None, "executed_qty": None, "cum_quote": None}

    def _num(key: str) -> float | None:
        try:
            return float(venue_order.get(key))
        except (TypeError, ValueError):
            return None

    return {
        "avg_price": _num("avgPrice"),
        "executed_qty": _num("executedQty"),
        "cum_quote": _num("cumQuote"),
    }
