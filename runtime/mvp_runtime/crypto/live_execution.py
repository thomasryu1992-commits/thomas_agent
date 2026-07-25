"""LP4 order adapter — submit + reconcile one guard-approved live order.

**Increment 1 (this module): the skeleton.** The default inert ``DryRunOrderAdapter``, the
Safety-Flag gate selection, the ``submit_and_reconcile`` orchestration, and the
``reconcile_status`` vocabulary land here — all exercisable and tested with **zero network and
zero venue**. The real Binance signed HTTP send + reconcile is a **gated stub** deferred to
increment 2, so **no code can send an order yet**: ``live_readiness.ORDER_PATH_IMPLEMENTED`` and
``financial_transaction_execution_implemented`` both stay OFF, honestly.

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

# reconcile_status vocabulary. RECONCILED is reused from live_promotion so the canary record's
# clean-derivation (clean iff RECONCILED and no mismatch) matches this by construction.
MISMATCH = "MISMATCH"
NOT_FOUND = "NOT_FOUND"
UNRECONCILABLE = "UNRECONCILABLE"

ORDER_PATH_NOT_IMPLEMENTED = "ORDER_PATH_NOT_IMPLEMENTED"
GUARD_NOT_APPROVED = "GUARD_NOT_APPROVED"
MALFORMED_INTENT = "MALFORMED_LIVE_ORDER_INTENT"


def build_order_request(intent: Mapping[str, Any]) -> dict[str, Any]:
    """The venue order params from a guard-approved intent. Fail-closed on a malformed intent.

    ``reduceOnly`` is set **from the intent** — the structural boundary the close guard relies
    on: a "close" that dropped this flag could open a position, so LP4 carries it faithfully.
    LP4 submits MARKET orders only (the intent already fixes ``order_type_exchange: MARKET``)."""
    symbol = intent.get("symbol")
    side = intent.get("side")
    quantity = intent.get("quantity")
    client_order_id = intent.get("client_order_id")
    if not (isinstance(symbol, str) and symbol):
        raise ToolError(MALFORMED_INTENT, "order intent is missing a symbol")
    if side not in ("BUY", "SELL"):
        raise ToolError(MALFORMED_INTENT, f"order intent side must be BUY or SELL, got {side!r}")
    if not (isinstance(quantity, (int, float)) and quantity > 0):
        raise ToolError(MALFORMED_INTENT, "order intent needs a positive quantity")
    if not (isinstance(client_order_id, str) and client_order_id):
        raise ToolError(MALFORMED_INTENT, "order intent is missing client_order_id (run enrich_order_identity)")
    if intent.get("order_type_exchange") != "MARKET":
        raise ToolError(MALFORMED_INTENT, "LP4 submits MARKET orders only")
    return {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": float(quantity),
        "reduceOnly": bool(intent.get("reduce_only")),
        "newClientOrderId": client_order_id,
    }


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

    def fetch_order(
        self, symbol: str, client_order_id: str, *, timeout_seconds: int = 10
    ) -> dict[str, Any] | None:
        req = self._submitted.get(str(client_order_id))
        if req is None:
            return None
        return {
            "symbol": req["symbol"],
            "side": req["side"],
            "status": "FILLED",
            "executedQty": req["quantity"],
            "reduceOnly": req["reduceOnly"],
            "orderId": f"dryrun-{str(client_order_id)[:16]}",
            "dry_run": True,
        }


class BinanceFuturesOrderAdapter:
    """The real adapter — constructed only behind the ``live_trading`` grant, host-allowlisted,
    re-asserting authorization at every egress.

    **INCREMENT 1 STUB.** The actual signed ``POST /fapi/v1/order`` + reconcile ``GET`` is
    deferred to increment 2, so both methods raise ``ORDER_PATH_NOT_IMPLEMENTED`` rather than
    reaching the venue. Its presence as a *gated* stub keeps the gate skeleton real while
    ``ORDER_PATH_IMPLEMENTED`` / ``financial_transaction_execution_implemented`` stay honestly
    OFF — no code can send an order yet. When increment 2 fills these in, it mirrors
    ``account.py``'s signed-request posture (names-only errors, the signed URL never logged) and
    flips the two flags in lockstep."""

    tool_id = ORDER_ADAPTER_TOOL_ID
    tool_version = ORDER_ADAPTER_TOOL_VERSION
    provider_id = LIVE_TRADING_PROVIDER_ID
    network_egress = True

    def __init__(self, *, base_url: str = ORDER_BASE_URL, authorization: Authorization | None = None):
        import urllib.parse

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

    def submit(self, order_request: Mapping[str, Any], *, timeout_seconds: int = 10) -> dict[str, Any]:
        self._assert()
        raise ToolError(
            ORDER_PATH_NOT_IMPLEMENTED,
            "the real order submit path is not implemented yet (LP4 increment 2)",
        )

    def fetch_order(
        self, symbol: str, client_order_id: str, *, timeout_seconds: int = 10
    ) -> dict[str, Any] | None:
        self._assert()
        raise ToolError(
            ORDER_PATH_NOT_IMPLEMENTED,
            "the real reconcile path is not implemented yet (LP4 increment 2)",
        )


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
        "reduce_only": request["reduceOnly"],
        "submit_error": submit_error,
        "submit_response": submit_response,
        "created_at": now,
    }
