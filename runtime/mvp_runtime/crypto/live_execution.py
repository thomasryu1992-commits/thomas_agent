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
* reaching the venue at all still requires the operator's per-machine ``live_trading`` grant,
  the order key, the confirmation phrase and a registered budget — none of which this code can
  create.

Design: ``docs/runtime-contracts/LP4_ORDER_ADAPTER_DESIGN_V0.1.md``. LP4 is the narrow, and only,
code that can send an order — it takes one **guard-approved** MARKET intent (LP3), submits it,
reconciles the result against the venue, and returns the ``exchange_order_id`` +
``reconcile_status`` + mismatches that feed the LP6 canary record (clean iff ``RECONCILED``) and
the LP2 P&L ledger. It does not size, decide, or manage positions.

The gate is the established chokepoint: the real adapter is constructed only behind the one
``live_trading`` grant via ``safety_gate.select_gated`` (``MVP_LIVE_TRADING=real`` alone fails
closed), and it re-asserts authorization at every egress so deleting the grant is a live
revocation. The order-capable key is its **own** env, distinct from the read-only account key.
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
ALLOWED_ORDER_HOSTS = frozenset({"fapi.binance.com"})
# Venue cap is 60000; mirror account.py's conservative value.
RECV_WINDOW_MS = 5000

# Order types LP4 can express. MARKET is the entry/close; the two conditional types are the
# LP5 protective bracket. Verified against the venue's New Order contract (2026-07-25):
# a conditional type carries a ``stopPrice``, and ``workingType`` selects the trigger price.
ORDER_TYPE_MARKET = "MARKET"
ORDER_TYPE_STOP_MARKET = "STOP_MARKET"
ORDER_TYPE_TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"
CONDITIONAL_ORDER_TYPES = frozenset({ORDER_TYPE_STOP_MARKET, ORDER_TYPE_TAKE_PROFIT_MARKET})
SUPPORTED_ORDER_TYPES = frozenset({ORDER_TYPE_MARKET}) | CONDITIONAL_ORDER_TYPES
WORKING_TYPE_MARK_PRICE = "MARK_PRICE"
WORKING_TYPE_CONTRACT_PRICE = "CONTRACT_PRICE"
WORKING_TYPES = frozenset({WORKING_TYPE_MARK_PRICE, WORKING_TYPE_CONTRACT_PRICE})

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

    Supported types (``order_type_exchange``): ``MARKET`` for an entry or a close, plus
    ``STOP_MARKET`` / ``TAKE_PROFIT_MARKET`` for the LP5 protective bracket. A conditional type
    **requires** a positive ``stop_price``: the venue lists ``stopPrice`` as optional across all
    types, but a conditional order without one is meaningless, so it is required here rather than
    sent empty and rejected at the venue.

    Two venue constraints are enforced rather than discovered at run time (both verified against
    the New Order contract, 2026-07-25):

    - ``closePosition=true`` is **mutually exclusive with both ``quantity`` and ``reduceOnly``**,
      and is only valid on a conditional type. So a close-all bracket leg sends neither.
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

    request: dict[str, Any] = {
        "symbol": symbol,
        "side": side,
        "type": order_type,
        "newClientOrderId": client_order_id,
    }

    if order_type in CONDITIONAL_ORDER_TYPES:
        stop_price = intent.get("stop_price")
        if not (isinstance(stop_price, (int, float)) and stop_price > 0):
            raise ToolError(
                MALFORMED_INTENT,
                f"{order_type} needs a positive stop_price (a conditional order without a "
                "trigger is meaningless)",
            )
        request["stopPrice"] = float(stop_price)
        working_type = intent.get("working_type")
        if working_type is not None:
            if working_type not in WORKING_TYPES:
                raise ToolError(
                    MALFORMED_INTENT,
                    f"working_type must be one of {sorted(WORKING_TYPES)}, got {working_type!r}",
                )
            request["workingType"] = working_type
    elif close_position:
        # closePosition is a Close-All conditional-order behaviour; on a MARKET order it is not
        # a thing the venue accepts, so refuse rather than send something that would be rejected.
        raise ToolError(
            MALFORMED_INTENT,
            f"close_position is only valid on {sorted(CONDITIONAL_ORDER_TYPES)}, not {order_type}",
        )

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
        self, symbol: str, client_order_id: str, *, timeout_seconds: int = 10
    ) -> dict[str, Any] | None: ...

    def cancel_order(
        self, symbol: str, client_order_id: str, *, timeout_seconds: int = 10
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
        self._submitted[str(req["newClientOrderId"])] = req
        return {"dry_run": True, "accepted": True, "clientOrderId": req["newClientOrderId"]}

    def cancel_order(
        self, symbol: str, client_order_id: str, *, timeout_seconds: int = 10
    ) -> dict[str, Any] | None:
        """Forget the order, as a cancel would. ``None`` when there was nothing to cancel."""
        req = self._submitted.pop(str(client_order_id), None)
        if req is None:
            return None
        return {"symbol": req["symbol"], "clientOrderId": client_order_id,
                "status": "CANCELED", "dry_run": True}

    def fetch_order(
        self, symbol: str, client_order_id: str, *, timeout_seconds: int = 10
    ) -> dict[str, Any] | None:
        req = self._submitted.get(str(client_order_id))
        if req is None:
            return None
        # A closePosition bracket leg carries neither quantity nor reduceOnly (they are mutually
        # exclusive with it at the venue), so the synthetic echo mirrors that shape too.
        # A CONDITIONAL order rests as NEW until its trigger price is reached — it does not fill
        # on submission. Echoing FILLED for one would let a dry run "confirm" a protective order
        # in a state the venue would never report, which is exactly the confidence a dry run
        # must not manufacture.
        return {
            "symbol": req["symbol"],
            "side": req["side"],
            "status": "NEW" if req["type"] in CONDITIONAL_ORDER_TYPES else "FILLED",
            "executedQty": req.get("quantity", 0.0),
            "reduceOnly": req.get("reduceOnly", False),
            "closePosition": req.get("closePosition") == "true",
            "orderId": f"dryrun-{str(client_order_id)[:16]}",
            "dry_run": True,
        }


class BinanceFuturesOrderAdapter:
    """The real adapter — constructed only behind the ``live_trading`` grant, host-allowlisted,
    re-asserting authorization at every egress.

    **This is the repository's first WRITE network egress.** Every other network path
    (``account``, ``market_data``) is GET-only. The credential posture mirrors ``account.py``
    exactly, because the signature travels in the query string:

    - the key is read from its **own** env at call time (never stored on the instance), and a
      missing credential is reported **by name only**;
    - a transport failure raises a deliberately **generic** error — the signed URL never reaches
      a message, a log, or a record;
    - authorization is re-asserted before every request, so deleting the grant is a live
      revocation mid-flight.

    It still sends nothing on its own: ``submit_and_reconcile`` refuses unless the final guard
    PASSed, and the whole adapter is only reachable once the operator has minted the
    ``live_trading`` grant and set the order key."""

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
            "POST", ORDER_PATH, dict(order_request), timeout_seconds=timeout_seconds
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

    def fetch_order(
        self, symbol: str, client_order_id: str, *, timeout_seconds: int = 10
    ) -> dict[str, Any] | None:
        """The order's state at the venue, or ``None`` when the venue has no such order.

        ``None`` is the venue's own "order does not exist" answer (code -2013) — a truthful
        NOT_FOUND, meaning the submit did not land. Any other rejection raises, so an ambiguous
        query becomes UNRECONCILABLE rather than being read as "no position". Queried by
        ``origClientOrderId``, which is the idempotency key.

        Retention caveat (venue-documented): an order that was cancelled/expired with no fill
        stops being queryable after 3 days, so ``None`` is only a reliable "did not land" signal
        near the time of the submit — which is exactly when reconcile runs."""
        body, code = self._signed_request(
            "GET", ORDER_PATH, {"symbol": symbol, "origClientOrderId": client_order_id},
            timeout_seconds=timeout_seconds,
        )
        if code is not None:
            if code == VENUE_ORDER_DOES_NOT_EXIST:
                return None
            msg = body.get("msg") if isinstance(body, dict) else None
            raise ToolError(ORDER_REJECTED, f"venue refused the order query (code {code}): {msg}")
        return body if isinstance(body, dict) else None

    def cancel_order(
        self, symbol: str, client_order_id: str, *, timeout_seconds: int = 10
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
        the exit path, after the position is confirmed closed."""
        body, code = self._signed_request(
            "DELETE", ORDER_PATH, {"symbol": symbol, "origClientOrderId": client_order_id},
            timeout_seconds=timeout_seconds,
        )
        if code is not None:
            if code in (VENUE_UNKNOWN_ORDER, VENUE_ORDER_DOES_NOT_EXIST):
                return None
            msg = body.get("msg") if isinstance(body, dict) else None
            raise ToolError(ORDER_REJECTED, f"venue refused the cancel (code {code}): {msg}")
        return body if isinstance(body, dict) else None


def select_order_adapter(*, now: str | None = None, root: Path | None = None) -> OrderAdapter:
    """Return the real order adapter if the ``live_trading`` grant is open, else the inert one.

    The gated factory receives the ``Authorization``, so the capable adapter cannot be
    constructed before the gate opens (the ``select_gated`` safety property)."""
    return safety_gate.select_gated(
        env_var=LIVE_TRADING_ENV,
        opt_in_value=REAL_LIVE_TRADING,
        flags=LIVE_TRADING_FLAGS,
        provider_id=LIVE_TRADING_PROVIDER_ID,
        default_factory=DryRunOrderAdapter,
        gated_factory=lambda authorization: BinanceFuturesOrderAdapter(authorization=authorization),
        now=now,
        root=root,
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
        # No venue answer at all: the fill is unknown, NOT empty — `_fill_facts(None)` reports
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
        "fill": _fill_facts(venue_order),
        "submit_error": submit_error,
        "submit_response": submit_response,
        "created_at": now,
    }


def _fill_facts(venue_order: Mapping[str, Any] | None) -> dict[str, Any]:
    """The venue's own fill numbers, coerced to floats where they parse.

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
