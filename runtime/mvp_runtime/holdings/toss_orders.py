"""The Toss buy-order adapter (G6-buy, Thomas 2026-10-26) — the one way this repo places a Toss order.

``docs/proposals/G6_BUY_CASH_AUTOINVEST_V0.1.md``: buy only, domestic only, whole shares, market orders, a
scenario Thomas approved. This module is the order half; the reads stay in ``toss_account.TossHoldingsFeed``,
which keeps no order method.

**Gate.** ``MVP_TOSS_ORDERS=buy_only`` on scheduler-maint, the environment alone (CLAUDE.md, env-only gate).
Unset, :func:`select_buy_orders` returns :class:`NoBuyOrders` and nothing capable is built. Every POST re-reads
the variable first (``safety_gate.assert_authorization``). Thomas sets it; Claude never does.

**One direction.** ``side`` is the constant ``BUY``. There is no sell, modify or cancel method, and the only
path is ``POST /api/v1/orders``.

**One token.** Toss keeps one valid token per client, so the adapter borrows the holdings feed's: same object,
same process. Issuing its own would revoke the lane's on every order. On a ``token-revoked``/``expired-token``
401 it reissues once through the feed and resends once with the same ``clientOrderId``, which Toss treats as
the same order for ten minutes.

**Outcomes.** :class:`OrderRejected` means Toss answered and placed nothing: a 4xx other than
``request-in-progress``. :class:`OrderUnknown` means it may or may not have been placed: a timeout, a dropped
connection, a 5xx or ``request-in-progress``. The caller stops on either and never resends on its own.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .. import safety_gate, timeutil
from ..errors import ToolError
from ..safety_gate import NETWORK_ACCESS, Authorization
from .toss_account import ACCOUNT_HEADER, BASE_URL, ORDERS_PATH, TossHoldingsFeed, _TOKEN_CODES

TOSS_ORDERS_ENV = "MVP_TOSS_ORDERS"
BUY_ONLY = "buy_only"
TOSS_ORDERS_PROVIDER = "toss_orders"
_FLAGS = (NETWORK_ACCESS,)
SIDE = "BUY"
ORDER_REJECTED = "TOSS_ORDER_REJECTED"
ORDER_UNKNOWN = "TOSS_ORDER_UNKNOWN"
ORDER_REFUSED = "TOSS_ORDER_REFUSED"


class OrderRejected(ToolError):
    def __init__(self, status: int, code: str, detail: dict[str, Any] | None = None):
        super().__init__(ORDER_REJECTED, f"Toss rejected the order ({status} {code or 'no code'})")
        self.status, self.toss_code, self.detail = status, code, detail or {}


class OrderUnknown(ToolError):
    def __init__(self, why: str, detail: dict[str, Any] | None = None):
        super().__init__(ORDER_UNKNOWN, f"the order may or may not be placed ({why})")
        self.why, self.detail = why, detail or {}


MESSAGE_LIMIT = 200


def error_detail(exc: urllib.error.HTTPError) -> dict[str, Any]:
    """What Toss said about a refused order, for the local journal only (LOCAL_ONLY; never a channel).

    The first live 422 (2026-10-27) left nothing but its status: the code was not where the envelope puts it, and
    the message and request id were not kept. So every shape is read here: the documented ``{"error": {...}}``
    envelope, a flat ``{"code": ...}``, a non-JSON body. ``message`` is Toss's user-facing text (``ApiError``:
    "사용자에게 노출 가능한"), cut to a line; ``request_id`` is what Toss support asks for; ``body_kind`` says
    which shape arrived. Nothing here is a secret: the token is in the request, not the answer."""
    headers = exc.headers or {}
    detail: dict[str, Any] = {"status": exc.code, "code": "", "message": "", "body_kind": "empty",
                              "request_id": str(headers.get("X-Request-Id") or headers.get("referenceId") or ""),
                              "content_type": str(headers.get("Content-Type") or "")[:60]}
    try:
        raw = exc.read()
    except OSError:
        raw = b""
    if not raw:
        return detail
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        detail["body_kind"] = "non_json"
        return detail
    error = body.get("error") if isinstance(body, dict) else None
    source = error if isinstance(error, dict) else body if isinstance(body, dict) else {}
    detail["body_kind"] = "envelope" if isinstance(error, dict) else "flat" if source else "other_json"
    detail["code"] = str(source.get("code") or "")
    detail["message"] = str(source.get("message") or "")[:MESSAGE_LIMIT]
    detail["request_id"] = str(source.get("requestId") or detail["request_id"])
    return detail


class NoBuyOrders:
    """The inert default: no order can be placed."""

    enabled = False

    def buy_kr(self, symbol: str, quantity: int, *, client_order_id: str, timeout_seconds: int = 10) -> str:
        raise ToolError(ORDER_REFUSED, f"{TOSS_ORDERS_ENV} is not {BUY_ONLY}: no order path")


class TossBuyOrders:
    """Market buys of whole shares of a domestic symbol, through the holdings feed's token."""

    enabled = True

    def __init__(self, authorization: Authorization, feed: TossHoldingsFeed) -> None:
        self._authorization = authorization
        self._feed = feed

    def _check_gate(self) -> None:
        safety_gate.assert_authorization(self._authorization, required_flags=_FLAGS,
                                         provider_id=TOSS_ORDERS_PROVIDER, now=timeutil.utc_now_iso())

    def _post_once(self, body: dict[str, Any], *, token: str, timeout_seconds: int) -> str:
        self._check_gate()                  # the egress re-check: the env var is read again, right here
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
                   "Content-Type": "application/json", ACCOUNT_HEADER: str(self._feed._account[0])}
        request = urllib.request.Request(f"{BASE_URL}{ORDERS_PATH}", method="POST", headers=headers,
                                         data=json.dumps(body).encode("utf-8"))
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = error_detail(exc)
            code = detail["code"]
            if exc.code >= 500 or code == "request-in-progress":
                raise OrderUnknown(f"{exc.code} {code}".strip(), detail) from None
            raise OrderRejected(exc.code, code, detail) from None
        except (TimeoutError, urllib.error.URLError, OSError):
            raise OrderUnknown("timeout or dropped connection") from None
        try:
            order_id = (json.loads(raw).get("result") or {}).get("orderId")
        except (ValueError, AttributeError):
            order_id = None
        if not order_id:
            raise OrderUnknown("accepted without an orderId")
        return str(order_id)

    def buy_kr(self, symbol: str, quantity: int, *, client_order_id: str, timeout_seconds: int = 10) -> str:
        """Place one market BUY of ``quantity`` whole shares of the domestic ``symbol``; returns Toss's orderId."""
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
            raise ToolError(ORDER_REFUSED, "a domestic buy is a whole number of shares, at least one")
        if not symbol or not symbol.isalnum() or len(symbol) != 6:
            raise ToolError(ORDER_REFUSED, "a domestic symbol is a 6-character KRX code")
        body = {"clientOrderId": client_order_id, "symbol": symbol, "side": SIDE, "orderType": "MARKET",
                "timeInForce": "DAY", "quantity": str(quantity)}
        self._feed._resolve_account(timeout_seconds=timeout_seconds)
        token = self._feed._access_token(timeout_seconds=timeout_seconds)
        try:
            return self._post_once(body, token=token, timeout_seconds=timeout_seconds)
        except OrderRejected as exc:
            if exc.status != 401 or exc.toss_code not in _TOKEN_CODES:
                raise
        self._feed._token = None
        token = self._feed._issue_token(timeout_seconds=timeout_seconds)
        return self._post_once(body, token=token, timeout_seconds=timeout_seconds)


def select_buy_orders(feed: Any) -> NoBuyOrders | TossBuyOrders:
    """The order adapter for this fire: capable only behind ``MVP_TOSS_ORDERS=buy_only`` and over a real Toss
    feed (the token it borrows)."""
    if not isinstance(feed, TossHoldingsFeed):
        return NoBuyOrders()
    return safety_gate.select_env_gated(
        env_var=TOSS_ORDERS_ENV,
        opt_in_value=BUY_ONLY,
        flags=_FLAGS,
        provider_id=TOSS_ORDERS_PROVIDER,
        default_factory=NoBuyOrders,
        gated_factory=lambda authorization: TossBuyOrders(authorization, feed),
    )
