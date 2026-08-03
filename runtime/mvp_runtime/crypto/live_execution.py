"""LP4 order adapter — submit + reconcile one guard-approved live order.

**LP4 is complete.** The skeleton (inert ``DryRunOrderAdapter`` default, Safety-Flag gate
selection, ``submit_and_reconcile``, the ``reconcile_status`` vocabulary) landed in increment 1;
increment 2a added the real Binance **signed POST + reconcile GET** and the conditional order
types the LP5 protective bracket needs. Every venue semantic here was verified against the
venue's own New Order / Query Order / error-code references (2026-07-25) rather than written
from memory.

**This module can place a real order.** Increment 2b flipped
``live_readiness.ORDER_PATH_IMPLEMENTED`` and ``financial_transaction_execution_implemented``
to **true** in lockstep (2026-07-25, with the replay-bundle regeneration), so a READY readiness
board now means a real order can be placed on that machine. Read the flags, not this sentence:
``python -m runtime.mvp_runtime.crypto.live_readiness`` computes the answer for the machine
you are on.

What still stands between this code and an **autonomous** order is structural, not missing
implementation:

* **no autonomous entry point may import this module** — ``live_leg`` and this adapter are both
  covered by ``test_no_autonomous_entry_point_reaches_the_live_order_path``, which fails loudly
  if one does. Today the only caller is the deliberate ``scripts/place_canary_order.py``, one
  canary at a time;
* ``financial_executor_enabled`` is ``false``, and cycle routing (LP5.3's last piece) is
  deliberately unbuilt — building it *is* the decision to relax that tripwire;
* reaching the venue at all still requires the operator's ``MVP_LIVE_TRADING=real`` opt-in,
  the order key, the confirmation phrase and a registered budget — none of which this code can
  create.

Design: ``docs/runtime-contracts/LP4_ORDER_ADAPTER_DESIGN_V0.1.md``. LP4 is the narrow, and only,
code that can send an order — it takes one **guard-approved** MARKET intent (LP3), submits it,
reconciles the result against the venue, and returns the ``exchange_order_id`` +
``reconcile_status`` + mismatches that feed the LP6 canary record (clean iff ``RECONCILED``) and
the LP2 P&L ledger. It does not size, decide, or manage positions.

The gate is the established chokepoint: the real adapter is constructed only behind the one
live-trading switch via ``safety_gate.select_env_gated``, and it re-asserts that switch at every
egress, so clearing ``MVP_LIVE_TRADING`` stops a running process rather than only the next one.
The order-capable key is its **own** env, distinct from the read-only account key.

That switch was a per-machine grant record until 2026-07-28, when Thomas removed it and made
``MVP_LIVE_TRADING=real`` the whole gate. The trade is real and recorded in
``docs/BUILD_HISTORY.md``: what a machine can do is now decided entirely by the environment the
operator hands the container, with no artifact on disk to inspect afterwards.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Protocol

from .. import safety_gate, timeutil
from ..errors import ToolError
from ..safety_gate import Authorization
from .live_pnl import (
    LIVE_TRADING_ENV,
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
    REAL_LIVE_TRADING,
)
from .live_promotion import RECONCILED

ORDER_ADAPTER_TOOL_ID = "crypto.live.order_adapter"
ORDER_ADAPTER_TOOL_VERSION = "0.1.0"

# The order-capable key — its OWN env, DISTINCT from account.py's read-only key
# (MVP_LIVE_ORDER_* vs BINANCE_ACCOUNT_*). Futures enabled, withdrawals disabled,
# IP-whitelisted; separate blast radius, separately revocable (design decision 2026-07-25).
ORDER_API_KEY_ENV = "MVP_LIVE_ORDER_API_KEY"
ORDER_API_SECRET_ENV = "MVP_LIVE_ORDER_API_SECRET"
ORDER_BASE_URL = "https://fapi.binance.com"
ORDER_PATH = "/fapi/v1/order"

# --- the Algo Order API: where every conditional order now lives ----------------------------
#
# **The venue moved them and this runtime did not notice for eight months.** Effective
# 2025-11-06 (announced) / 2025-12-09 (enforced), Binance USD-M migrated conditional order
# types off `POST /fapi/v1/order`, which now REFUSES them with `-4120: Order type not supported
# for this endpoint. Please use the Algo Order API endpoints instead.`
#
# Measured on this account 2026-08-03 through `scripts/diagnose_bracket_leg`, which is the whole
# reason that tool exists: the exact request the runtime sent on 2026-08-02 comes back -4120,
# while the take-profit LIMIT beside it is accepted. That is the cause of both autonomous
# entries filling and being closed again for want of a protective stop, and of the runtime being
# unable to hold a live position at all.
#
# The bracket DESIGN was never wrong — a venue-side stop placed at entry is the right shape. One
# transport fact went stale, and every check this repo had pointed at its own model of the
# venue: closed schemas, tick sizes, filters, request shape. All of them passed. None of them
# could catch "the venue stopped accepting this type", because that fact lives only at the
# venue. The `Verified against the venue's New Order contract (2026-07-25)` note below this is
# the trap in miniature — the parameter table was read correctly, eight months after the type
# had been moved off the endpoint the table described.
#
# It is NOT only a path change, which is why guessing would have produced a second broken
# release: two request parameters and three response fields are renamed.
#
#   place    POST   /fapi/v1/order      ->  POST   /fapi/v1/algoOrder  + algoType=CONDITIONAL
#   query    GET    /fapi/v1/order      ->  GET    /fapi/v1/algoOrder
#   cancel   DELETE /fapi/v1/order      ->  DELETE /fapi/v1/algoOrder
#   trigger  stopPrice                  ->  triggerPrice
#   send id  newClientOrderId           ->  clientAlgoId
#   query id origClientOrderId          ->  clientAlgoId
#   order id orderId                    ->  algoId
#   state    status                     ->  algoStatus
ALGO_ORDER_PATH = "/fapi/v1/algoOrder"
# The only `algoType` the venue defines for these; sent on every place, and the discriminator
# `is_algo_request` reads to pick the endpoint. Using the venue's own field rather than a local
# flag means the request cannot say one thing and be routed by another.
ALGO_TYPE_CONDITIONAL = "CONDITIONAL"
# The venue's own validator: identical parameters and signing, **no order is ever created**.
#
# It exists here because on 2026-08-02 the runtime's first two autonomous entries had their
# `closePosition` STOP_MARKET protective leg refused, both times, and the record kept only
# `error: ORDER_REJECTED` — the same string for every rejection there is. #426 now records the
# venue's own code and message, but only on the NEXT rejection, and reaching one costs a real
# entry and a real close, only happens when a strategy routes, and is capped at 2 orders a day.
#
# This turns that into a free, repeatable question. A validation request cannot fill, cannot
# rest, and cannot be cancelled because there is nothing to cancel — so the one experiment that
# answers "why did the venue say no" carries none of the risk of the event that raised it.
#
# What it cannot answer is stated so a null result is not misread: the endpoint validates the
# REQUEST, so a rejection that depends on account state at that instant (no position to reduce,
# an algo-order count, a margin condition) can pass here and still fail on the real path. An
# ACCEPTED result is therefore evidence the shape is right, never evidence the order will land.
ORDER_TEST_PATH = "/fapi/v1/order/test"
# Read-only: what is RESTING at the venue right now. `fetch_order` can only answer about an id
# the runtime already knows, which leaves everything it does not know invisible — including the
# orders that decide whether a new one is even accepted.
OPEN_ORDERS_PATH = "/fapi/v1/openOrders"
ALLOWED_ORDER_HOSTS = frozenset({"fapi.binance.com"})
# Venue cap is 60000; mirror account.py's conservative value.
RECV_WINDOW_MS = 5000

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

# reconcile_status vocabulary. RECONCILED is reused from live_promotion so the canary record's
# clean-derivation (clean iff RECONCILED and no mismatch) matches this by construction.
MISMATCH = "MISMATCH"
NOT_FOUND = "NOT_FOUND"
UNRECONCILABLE = "UNRECONCILABLE"

GUARD_NOT_APPROVED = "GUARD_NOT_APPROVED"
MALFORMED_INTENT = "MALFORMED_LIVE_ORDER_INTENT"
NO_ORDER_API_KEY = "NO_ORDER_API_KEY"
ORDER_REJECTED = "ORDER_REJECTED"
ORDER_TRANSPORT = "ORDER_TRANSPORT"
ORDER_MALFORMED_RESULT = "ORDER_MALFORMED_RESULT"

# Venue error codes, verified from its error-code reference (2026-07-25).
VENUE_ORDER_DOES_NOT_EXIST = -2013      # a queried order is genuinely absent => NOT_FOUND
VENUE_DUPLICATE_CLIENT_ORDER_ID = -4116  # the idempotency key already landed => reconcile finds it
VENUE_UNKNOWN_ORDER = -2011             # a cancelled/filled/never-placed order => already gone


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
    endpoints answer with ``orderId``/``status``/``stopPrice``. Translated ONCE, here at the
    wire boundary, so `reconcile_order`, `place_bracket_leg` and `read_bracket_legs` keep one
    vocabulary — teaching every caller two spellings is how one of them ends up checking the
    wrong field and reading a refused stop as a resting one.

    **Additive, never lossy.** The venue's own keys are kept alongside the aliases, because the
    raw response is what lands in the ledger and an incident is reconstructed from it — this
    session reconstructed the 2026-08-02 request byte-for-byte only because nothing had been
    helpfully tidied away first. An alias is written only when the venue supplied the source
    field, so a missing value stays missing rather than becoming a confident default."""
    if venue_order is None:
        return None
    out = dict(venue_order)
    for source, alias in (("algoId", "orderId"), ("algoStatus", "status"),
                          ("triggerPrice", "stopPrice")):
        if source in out and alias not in out:
            out[alias] = out[source]
    return out


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


class OrderAdapter(Protocol):
    """Transport for one order: submit it, then fetch its state for reconciliation. The
    comparison lives in ``reconcile_order`` (a pure function), so an adapter is only transport."""

    tool_id: str
    tool_version: str

    def submit(self, order_request: Mapping[str, Any], *, timeout_seconds: int = 10) -> dict[str, Any]: ...

    def fetch_order(
        self, symbol: str, client_order_id: str, *, timeout_seconds: int = 10,
        algo: bool = False,
    ) -> dict[str, Any] | None: ...

    def cancel_order(
        self, symbol: str, client_order_id: str, *, timeout_seconds: int = 10,
        algo: bool = False,
    ) -> dict[str, Any] | None: ...


class DryRunOrderAdapter:
    """Default, inert adapter: records what WOULD be sent and reports a synthetic FILLED order,
    opening no socket. Lets the whole submit+reconcile orchestration run and be tested with zero
    network and zero venue. A dry run reconciles to ``RECONCILED`` because the synthetic order
    echoes the submitted request — proving the happy path end to end without risking anything."""

    tool_id = ORDER_ADAPTER_TOOL_ID
    tool_version = f"{ORDER_ADAPTER_TOOL_VERSION}-dryrun"
    network_egress = False

    def __init__(self) -> None:
        self._submitted: dict[str, dict[str, Any]] = {}

    def submit(self, order_request: Mapping[str, Any], *, timeout_seconds: int = 10) -> dict[str, Any]:
        req = dict(order_request)
        # The id field is spelled `clientAlgoId` on an algo request and `newClientOrderId`
        # otherwise. Reading only one of them made the inert adapter — the default everywhere,
        # and the one every test runs through — raise KeyError on exactly the order type the
        # Algo migration moved.
        client_id = str(req.get("clientAlgoId") or req["newClientOrderId"])
        self._submitted[client_id] = req
        return {"dry_run": True, "accepted": True, "clientOrderId": client_id}

    def validate_order(
        self, order_request: Mapping[str, Any], *, timeout_seconds: int = 10
    ) -> dict[str, Any]:
        """Accepts everything, and says which adapter answered.

        A dry run has no venue to ask, so ``accepted: True`` here means "nothing was checked",
        not "the venue is happy". ``dry_run`` rides on the result so a caller printing it cannot
        report an unasked question as a passing one — the whole failure mode this tool exists
        to avoid.

        The algo refusal is mirrored, and deliberately: "conditional orders have no test
        endpoint" is a fact about the API surface, not a venue answer, so a dry run that
        reported ACCEPTED there would promise a check the real adapter is going to decline."""
        if is_algo_request(order_request):
            return {
                "accepted": None, "code": None, "msg": None, "supported": False, "dry_run": True,
                "detail": "conditional orders live on the Algo API, which has no test endpoint",
            }
        return {"accepted": True, "code": None, "msg": None, "dry_run": True}

    def open_orders(
        self, symbol: str | None = None, *, timeout_seconds: int = 10
    ) -> list[dict[str, Any]]:
        """What this dry run has "placed" and not withdrawn, filtered by symbol like the venue."""
        return [
            dict(req) for req in self._submitted.values()
            if symbol is None or req.get("symbol") == symbol
        ]

    def cancel_order(
        self, symbol: str, client_order_id: str, *, timeout_seconds: int = 10,
        algo: bool = False,
    ) -> dict[str, Any] | None:
        """Forget the order, as a cancel would. ``None`` when there was nothing to cancel.

        ``algo`` is accepted and ignored: this adapter has one store and no endpoints, so the
        routing the real one does has nothing to select here. Kept in the signature so a caller
        that forgets it fails against the REAL adapter's default rather than against this one."""
        req = self._submitted.pop(str(client_order_id), None)
        if req is None:
            return None
        return {"symbol": req["symbol"], "clientOrderId": client_order_id,
                "status": "CANCELED", "dry_run": True}

    def fetch_order(
        self, symbol: str, client_order_id: str, *, timeout_seconds: int = 10,
        algo: bool = False,
    ) -> dict[str, Any] | None:
        req = self._submitted.get(str(client_order_id))
        if req is None:
            return None
        # A closePosition bracket leg carries neither quantity nor reduceOnly (they are mutually
        # exclusive with it at the venue), so the synthetic echo mirrors that shape too.
        # A RESTING order rests as NEW — a conditional one until its trigger price is reached, a
        # LIMIT until the market reaches its price. Neither fills on submission. Echoing FILLED
        # for one would let a dry run "confirm" a protective order in a state the venue would
        # never report, which is exactly the confidence a dry run must not manufacture.
        return {
            "symbol": req["symbol"],
            "side": req["side"],
            "status": "NEW" if req["type"] in RESTING_ORDER_TYPES else "FILLED",
            "executedQty": req.get("quantity", 0.0),
            "reduceOnly": req.get("reduceOnly", False),
            "closePosition": req.get("closePosition") == "true",
            "orderId": f"dryrun-{str(client_order_id)[:16]}",
            # The algo spellings too, so a dry run exercises the same normalization the real
            # answer goes through instead of a shape only this adapter ever produces.
            **({"algoId": f"dryrun-{str(client_order_id)[:16]}",
                "algoStatus": "NEW" if req["type"] in RESTING_ORDER_TYPES else "FILLED",
                "triggerPrice": req.get("triggerPrice")} if req.get("algoType") else {}),
            "dry_run": True,
        }


class BinanceFuturesOrderAdapter:
    """The real adapter — constructed only behind the live-trading opt-in, host-allowlisted,
    re-asserting authorization at every egress.

    **This is the repository's first WRITE network egress.** Every other network path
    (``account``, ``market_data``) is GET-only. The credential posture mirrors ``account.py``
    exactly, because the signature travels in the query string:

    - the key is read from its **own** env at call time (never stored on the instance), and a
      missing credential is reported **by name only**;
    - a transport failure raises a deliberately **generic** error — the signed URL never reaches
      a message, a log, or a record;
    - authorization is re-asserted before every request, so clearing ``MVP_LIVE_TRADING`` is a
      live revocation mid-flight — weaker than the grant file it replaced, since a running
      container's environment does not change under it, but not nothing.

    It still sends nothing on its own: ``submit_and_reconcile`` refuses unless the final guard
    PASSed, and the whole adapter is only reachable once the operator has set
    ``MVP_LIVE_TRADING=real`` and the order key."""

    tool_id = ORDER_ADAPTER_TOOL_ID
    tool_version = ORDER_ADAPTER_TOOL_VERSION
    provider_id = LIVE_TRADING_PROVIDER_ID
    network_egress = True

    def __init__(self, *, base_url: str = ORDER_BASE_URL, authorization: Authorization | None = None):
        host = (urllib.parse.urlparse(base_url).hostname or "").lower()
        if host not in ALLOWED_ORDER_HOSTS:
            # A URL typo must fail loudly rather than sign a request to an unexpected host.
            raise ToolError("ORDER_HOST_NOT_ALLOWED", "order base URL is not an allowed live host")
        self._base_url = base_url.rstrip("/")
        self._authorization = authorization

    def _assert(self) -> None:
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=LIVE_TRADING_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )

    def _signed_request(
        self, method: str, path: str, params: Mapping[str, Any], *, timeout_seconds: int
    ) -> tuple[Any, int | None]:
        """One signed request. Returns ``(parsed_body, venue_error_code)``.

        A venue-side rejection (HTTP 4xx) is returned as ``(body, code)`` rather than raised, so
        the caller decides what it means: for a query, "order does not exist" is a legitimate
        NOT_FOUND; for a submit, a duplicate client id means the original already landed. Only a
        transport failure or an unparseable body raises."""
        self._assert()
        api_key = os.environ.get(ORDER_API_KEY_ENV, "").strip()
        api_secret = os.environ.get(ORDER_API_SECRET_ENV, "").strip()
        if not api_key or not api_secret:
            # Names only — the absence of a credential is reportable, its value never is.
            raise ToolError(
                NO_ORDER_API_KEY,
                f"live order credentials are not configured "
                f"({ORDER_API_KEY_ENV}/{ORDER_API_SECRET_ENV})",
            )
        query = {k: v for k, v in params.items() if v is not None}
        query.setdefault("recvWindow", RECV_WINDOW_MS)
        query["timestamp"] = int(time.time() * 1000)
        encoded = urllib.parse.urlencode(query)
        signature = hmac.new(
            api_secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        request = urllib.request.Request(
            f"{self._base_url}{path}?{encoded}&signature={signature}",
            method=method,
            headers={"Accept": "application/json", "X-MBX-APIKEY": api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            # The venue puts its reason in the body ({"code": -2013, "msg": "..."}). Read it, but
            # never echo the request URL — it carries the signature.
            try:
                body = json.loads(exc.read().decode("utf-8"))
                code = int(body.get("code")) if isinstance(body, dict) else None
            except Exception:  # noqa: BLE001 — an unreadable error body must not mask the failure
                body, code = None, None
            if code is None:
                raise ToolError(
                    ORDER_TRANSPORT, f"live order request rejected (HTTP {exc.code})"
                ) from None
            return body, code
        except (TimeoutError, urllib.error.URLError):
            # Deliberately generic (the account.py / market-data transport posture).
            raise ToolError(ORDER_TRANSPORT, "live order request failed or timed out") from None
        try:
            return json.loads(raw), None
        except ValueError:
            raise ToolError(
                ORDER_MALFORMED_RESULT, "live order endpoint returned an unparseable response"
            ) from None

    def submit(self, order_request: Mapping[str, Any], *, timeout_seconds: int = 10) -> dict[str, Any]:
        """Send one order. Raises ``ToolError`` on a venue rejection or a transport failure.

        A rejection is raised rather than returned because a rejected submit is not an outcome the
        caller can act on directly — ``submit_and_reconcile`` catches it and asks the venue what
        actually happened. The one rejection that is *informative* is a duplicate client order id:
        it means this exact order already landed, so the reconcile read will find it."""
        body, code = self._signed_request(
            "POST",
            ALGO_ORDER_PATH if is_algo_request(order_request) else ORDER_PATH,
            dict(order_request),
            timeout_seconds=timeout_seconds,
        )
        if code is not None:
            if code == VENUE_DUPLICATE_CLIENT_ORDER_ID:
                # Idempotency did its job: the original submission is already at the venue.
                raise ToolError(
                    ORDER_REJECTED,
                    f"duplicate client order id ({code}) — the original order already landed; "
                    "reconcile decides the outcome",
                )
            msg = body.get("msg") if isinstance(body, dict) else None
            raise ToolError(ORDER_REJECTED, f"venue rejected the order (code {code}): {msg}")
        return body if isinstance(body, dict) else {}

    def validate_order(
        self, order_request: Mapping[str, Any], *, timeout_seconds: int = 10
    ) -> dict[str, Any]:
        """Ask the venue whether it would ACCEPT this request. Creates nothing.

        Returns ``{"accepted": bool, "code": int | None, "msg": str | None}`` and — unlike
        :meth:`submit` — a rejection is **returned, not raised**. The rejection is the answer
        this method exists to obtain, so making the caller catch it would put the finding in an
        exception's text, which is exactly how the 2026-08-02 cause was lost the first time.

        Transport failures still raise: "the venue refused it" and "I could not ask" are
        different answers and must not arrive as the same value.

        **An algo request cannot be validated here, and saying so is the point.** The test
        endpoint belongs to the order API, which is precisely the door conditional orders were
        moved off — sending one there would return the -4120 this method was built to discover,
        forever, as an artefact of asking the wrong endpoint rather than a fact about the
        request. The venue publishes no counterpart under the Algo API, so the answer is
        ``supported: False`` and the only proof for a conditional leg is a real placement. A
        ``closePosition`` stop placed while FLAT and far from the market is the cheap version of
        that: nothing to close means nothing to fill, and it cancels.
        """
        if is_algo_request(order_request):
            return {
                "accepted": None, "code": None, "msg": None, "supported": False,
                "detail": "conditional orders live on the Algo API, which has no test endpoint",
            }
        body, code = self._signed_request(
            "POST", ORDER_TEST_PATH, dict(order_request), timeout_seconds=timeout_seconds
        )
        if code is not None:
            return {
                "accepted": False,
                "code": code,
                "msg": body.get("msg") if isinstance(body, dict) else None,
            }
        return {"accepted": True, "code": None, "msg": None}

    def fetch_order(
        self, symbol: str, client_order_id: str, *, timeout_seconds: int = 10,
        algo: bool = False,
    ) -> dict[str, Any] | None:
        """The order's state at the venue, or ``None`` when the venue has no such order.

        ``None`` is the venue's own "order does not exist" answer (code -2013) — a truthful
        NOT_FOUND, meaning the submit did not land. Any other rejection raises, so an ambiguous
        query becomes UNRECONCILABLE rather than being read as "no position". Queried by
        ``origClientOrderId``, which is the idempotency key.

        ``algo`` selects the Algo Order endpoint, where the same id is spelled ``clientAlgoId``
        and the answer comes back in the algo field names — :func:`normalize_algo_order` puts it
        back into the one vocabulary the callers speak. It has NO default that guesses: a
        conditional order queried on the order endpoint answers "does not exist", which reads as
        a stop that never landed when it may be resting perfectly well. False is the correct
        default only because every non-bracket caller here places plain orders.

        Retention caveat (venue-documented): an order that was cancelled/expired with no fill
        stops being queryable after 3 days, so ``None`` is only a reliable "did not land" signal
        near the time of the submit — which is exactly when reconcile runs."""
        params = (
            {"clientAlgoId": client_order_id} if algo
            else {"symbol": symbol, "origClientOrderId": client_order_id}
        )
        body, code = self._signed_request(
            "GET", ALGO_ORDER_PATH if algo else ORDER_PATH, params,
            timeout_seconds=timeout_seconds,
        )
        if code is not None:
            if code == VENUE_ORDER_DOES_NOT_EXIST:
                return None
            msg = body.get("msg") if isinstance(body, dict) else None
            raise ToolError(ORDER_REJECTED, f"venue refused the order query (code {code}): {msg}")
        if not isinstance(body, dict):
            return None
        return normalize_algo_order(body) if algo else body

    def open_orders(
        self, symbol: str | None = None, *, timeout_seconds: int = 10
    ) -> list[dict[str, Any]]:
        """Everything RESTING at the venue right now. Read-only: places nothing, cancels nothing.

        The gap this closes is a blind spot rather than a missing convenience. :meth:`fetch_order`
        answers about an id the runtime already holds, so anything it does not know about is
        invisible to it — and the venue's own limits are counted over exactly that population. A
        conditional order is capped per symbol, so a bracket leg can be refused by orders this
        runtime never placed and cannot see, which is the state it was left guessing at on
        2026-08-02 when a protective stop was rejected twice with no way to count what was
        already there.

        It also answers the question every close path raises and none could check: after
        ``cancel_bracket_orders`` runs, is anything still resting? A leg left behind cannot open a
        position — a ``closePosition`` stop is Close-All and a ``reduceOnly`` target only reduces
        — but it can still trigger against a position opened later, and until now nothing could
        tell an operator whether the venue agreed the book was clean.

        ``symbol=None`` asks for every symbol, which the venue rates far more expensively (weight
        40 against 1); pass a symbol unless the whole account is genuinely the question. Raises
        rather than returning an empty list on a refusal — "nothing is resting" and "I could not
        find out" must never arrive as the same answer to a question about live exposure.
        """
        params: dict[str, Any] = {} if symbol is None else {"symbol": symbol}
        body, code = self._signed_request(
            "GET", OPEN_ORDERS_PATH, params, timeout_seconds=timeout_seconds
        )
        if code is not None:
            msg = body.get("msg") if isinstance(body, dict) else None
            raise ToolError(
                ORDER_REJECTED, f"venue refused the open-orders query (code {code}): {msg}"
            )
        return [o for o in body if isinstance(o, dict)] if isinstance(body, list) else []

    def cancel_order(
        self, symbol: str, client_order_id: str, *, timeout_seconds: int = 10,
        algo: bool = False,
    ) -> dict[str, Any] | None:
        """Cancel a resting order. ``None`` when the venue says there was nothing to cancel.

        Needed because the venue documents **no** auto-cancel of a conditional order when the
        position it protects closes (verified 2026-07-25): after an exit, the surviving bracket
        leg is still resting and has to be withdrawn explicitly.

        ``-2011`` (unknown order) is the venue's own "already gone" — filled, already cancelled,
        or never placed — and is the expected answer for the leg that just triggered the close.
        It is therefore ``None``, not an error. Any other rejection raises, so a cancel that
        failed for a real reason is surfaced rather than assumed done.

        **This can only remove a resting order, never place one.** A cancel of a protective leg
        while its position is still open would *increase* risk, which is why the only caller is
        the exit path, after the position is confirmed closed.

        ``algo`` picks the Algo Order endpoint, and here the wrong value is worse than a wrong
        query: cancelling a conditional order on the order endpoint answers "unknown order",
        which this function reads as *already gone* — so a stop that is still resting would be
        reported as withdrawn, and the next entry would meet the venue's per-symbol conditional
        cap with a leg nobody knows about."""
        body, code = self._signed_request(
            "DELETE",
            ALGO_ORDER_PATH if algo else ORDER_PATH,
            {"clientAlgoId": client_order_id} if algo
            else {"symbol": symbol, "origClientOrderId": client_order_id},
            timeout_seconds=timeout_seconds,
        )
        if code is not None:
            if code in (VENUE_UNKNOWN_ORDER, VENUE_ORDER_DOES_NOT_EXIST):
                return None
            msg = body.get("msg") if isinstance(body, dict) else None
            raise ToolError(ORDER_REJECTED, f"venue refused the cancel (code {code}): {msg}")
        return body if isinstance(body, dict) else None


def select_order_adapter(*, now: str | None = None, root: Path | None = None) -> OrderAdapter:
    """Return the real order adapter if live trading is opted in, else the inert one.

    ``select_env_gated``, not ``select_gated``: Thomas removed the per-machine grant for this
    capability on 2026-07-28, so ``MVP_LIVE_TRADING=real`` IS the gate. The construction order is
    unchanged — the capable adapter is built only after the opt-in is confirmed and receives its
    ``Authorization``, so it still cannot exist before the gate opens, and it still re-verifies at
    every egress. See the block comment above ``select_env_gated`` for the decision and its cost.

    ``now``/``root`` are kept in the signature and unused: every caller passes them, they cost
    nothing, and restoring the grant should be a one-line change here rather than a change at
    each call site."""
    return safety_gate.select_env_gated(
        env_var=LIVE_TRADING_ENV,
        opt_in_value=REAL_LIVE_TRADING,
        flags=LIVE_TRADING_FLAGS,
        provider_id=LIVE_TRADING_PROVIDER_ID,
        default_factory=DryRunOrderAdapter,
        gated_factory=lambda authorization: BinanceFuturesOrderAdapter(authorization=authorization),
    )


def submit_and_reconcile(
    intent: Mapping[str, Any],
    *,
    adapter: OrderAdapter,
    guard_verdict: Mapping[str, Any],
    now: str,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    """Submit one guard-approved order and reconcile it against the venue.

    **Belt-and-suspenders:** refuses to open a socket unless ``guard_verdict['approved'] is
    True`` — LP4 never trusts a caller's claim that an order was approved. The caller runs the
    final guard (``evaluate_live_order_guard``) on live facts and passes the verdict.

    **Reconcile-first, never blind-retry:** whatever the submit returns (accepted, rejected, or
    ambiguous/timed-out), the truth comes from a read — ``fetch_order`` by ``client_order_id``.
    A rejected or lost submit reconciles to ``NOT_FOUND``; an ambiguous submit that actually
    landed reconciles to ``RECONCILED``/``MISMATCH``; a failed reconcile query is
    ``UNRECONCILABLE`` (fail closed, surfaced). LP4 never resubmits — and could not open a second
    position if it did, because ``newClientOrderId`` is the idempotency key the venue dedupes on.

    Returns a result dict carrying ``reconcile_status`` + ``mismatches`` + ``exchange_order_id``,
    from which the caller builds the canary record (clean iff ``RECONCILED`` and no mismatch)."""
    if not (isinstance(guard_verdict, Mapping) and guard_verdict.get("approved") is True):
        raise ToolError(GUARD_NOT_APPROVED, "LP4 refuses to submit an order the final guard did not approve")

    request = build_order_request(intent)
    submit_error: str | None = None
    submit_response: dict[str, Any] | None = None
    try:
        submit_response = adapter.submit(request, timeout_seconds=timeout_seconds)
    except ToolError as exc:
        # A rejected OR ambiguous submit: do not assume nothing landed and do not blind-retry.
        # Reconcile by client_order_id below to learn the truth from the venue.
        submit_error = exc.reason_code

    try:
        venue_order = adapter.fetch_order(
            request["symbol"], request["newClientOrderId"], timeout_seconds=timeout_seconds
        )
    except ToolError as exc:
        # No venue answer at all: the fill is unknown, NOT empty — `fill_facts(None)` reports
        # every figure as None so a caller can never read an unreconciled order as a free trade.
        venue_order = None
        status, mismatches, exchange_order_id = (
            UNRECONCILABLE,
            [f"reconcile query failed: {exc.reason_code}"],
            None,
        )
    else:
        status, mismatches = reconcile_order(intent, venue_order)
        exchange_order_id = (venue_order or {}).get("orderId")

    return {
        "reconcile_status": status,
        "mismatches": mismatches,
        "exchange_order_id": exchange_order_id,
        "client_order_id": request["newClientOrderId"],
        "symbol": request["symbol"],
        # A closePosition leg carries no reduceOnly (mutually exclusive at the venue).
        "reduce_only": bool(request.get("reduceOnly")),
        "order_type": request["type"],
        # The ACTUAL fill, straight from the venue — what LP5 must compute realized PnL from,
        # never the modelled entry/exit the plan carried (the venue reports these as strings).
        "fill": fill_facts(venue_order),
        "submit_error": submit_error,
        "submit_response": submit_response,
        "created_at": now,
    }


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
