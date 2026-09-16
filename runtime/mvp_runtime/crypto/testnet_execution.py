"""The signed testnet order path (PR1d-1; Thomas decision 2, 2026-09-15).

Climbing the execution stage from SIGNED_TESTNET to LIVE_AUTONOMOUS needs evidence that this
machine can sign, place, protect, cancel and reconcile a real order at the venue — evidence the
exchange's own dry-run endpoint structurally cannot give (it refuses conditional orders, which is
precisely the API the protective legs moved to and precisely what broke on 2026-08-02). So the
machine earns it on the venue's TESTNET, with no money at stake.

**Why a module of its own rather than a mode of the live adapter.** The import graph is the proof
that a testnet order cannot reach live state. What is shared with ``live_execution`` is only its
pure functions — building the request, judging a reconcile, translating the algo field names, and
the submit/reconcile orchestration, which takes the adapter as an argument — so the two paths can
never disagree about what RECONCILED means. What is NOT shared is everything that decides where a
request goes or what it touches:

- its own **host allowlist** (``ALLOWED_ORDER_HOSTS`` is never widened; the live adapter still
  refuses a testnet URL at construction, as it has since 2026-07-25);
- its own **key pair**, read at call time from its own env names — the live adapter reads its
  credentials from module constants, so inheriting its signing method would have signed testnet
  requests with the mainnet key;
- its own **provider id**, which is what ``assert_authorization`` compares, so a testnet
  authorization cannot open the live ledger, counter, book or adapter;
- its own **env opt-in**, so the live switch neither enables nor disables this path;
- its own **venue state** (PR1d-0's axis): its counter, book, ledger and breaker are the testnet
  venue's, never the live ones.

It is still the same posture as the live adapter in the ways that matter: constructed only behind
its opt-in and handed its ``Authorization``, re-asserting that authorization before every request,
reporting a missing credential by name only, and never letting the signed URL reach a message, a
log or a record.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

from .. import safety_gate, timeutil
from ..errors import ToolError
from ..safety_gate import FILESYSTEM_WRITE, NETWORK_ACCESS, Authorization
from .live_execution import (
    ALGO_ORDER_PATH,
    NO_ORDER_API_KEY,
    ORDER_MALFORMED_RESULT,
    ORDER_PATH,
    ORDER_REJECTED,
    ORDER_TRANSPORT,
    RECV_WINDOW_MS,
    VENUE_DUPLICATE_CLIENT_ORDER_ID,
    VENUE_ORDER_DOES_NOT_EXIST,
    VENUE_UNKNOWN_ORDER,
    is_algo_request,
    normalize_algo_order,
)
from .live_order import LiveOrderCounter as _LiveOrderCounter
from .state import VENUE_TESTNET

TESTNET_ADAPTER_TOOL_ID = "crypto.testnet.order_adapter"
TESTNET_ADAPTER_TOOL_VERSION = "0.1.0"

# The venue's own testnet host. A separate constant and a separate allowlist: widening the live
# one would let the live adapter sign for testnet, which is the reverse of the property wanted.
TESTNET_BASE_URL = "https://testnet.binancefuture.com"
ALLOWED_TESTNET_HOSTS = frozenset({"testnet.binancefuture.com"})

# The opt-in and the credentials, all distinct from the live ones. `MVP_LIVE_TRADING` neither
# enables nor disables this path, and the mainnet key cannot sign here.
TESTNET_TRADING_ENV = "MVP_TESTNET_TRADING"
REAL_TESTNET_TRADING = "real"
TESTNET_API_KEY_ENV = "MVP_TESTNET_ORDER_API_KEY"
TESTNET_API_SECRET_ENV = "MVP_TESTNET_ORDER_API_SECRET"

# Its own provider id: `assert_authorization` compares this, so a testnet grant opens nothing live.
TESTNET_PROVIDER_ID = "signed_testnet"
TESTNET_TRADING_FLAGS = (NETWORK_ACCESS, FILESYSTEM_WRITE)

TESTNET_HOST_NOT_ALLOWED = "TESTNET_ORDER_HOST_NOT_ALLOWED"


class DryRunTestnetOrderAdapter:
    """The inert default: records what WOULD be sent and answers a synthetic FILLED order without
    opening a socket, so the whole cycle can be rehearsed with no venue and no credentials."""

    tool_id = TESTNET_ADAPTER_TOOL_ID
    tool_version = f"{TESTNET_ADAPTER_TOOL_VERSION}-dryrun"
    venue = VENUE_TESTNET
    network_egress = False

    def __init__(self) -> None:
        self._submitted: dict[str, dict[str, Any]] = {}

    def submit(self, order_request: Mapping[str, Any], *, timeout_seconds: int = 10) -> dict[str, Any]:
        req = dict(order_request)
        client_id = str(req.get("clientAlgoId") or req["newClientOrderId"])
        self._submitted[client_id] = req
        return {"dry_run": True, "accepted": True, "clientOrderId": client_id}

    def fetch_order(self, symbol: str, client_order_id: str, *, timeout_seconds: int = 10,
                    algo: bool = False) -> dict[str, Any] | None:
        req = self._submitted.get(client_order_id)
        if req is None:
            return None
        quantity = str(req.get("quantity") or "0")
        return {
            "dry_run": True, "symbol": req.get("symbol"), "side": req.get("side"),
            "status": "NEW" if algo else "FILLED",
            "executedQty": "0" if algo else quantity,
            "origQty": quantity,
            "reduceOnly": bool(req.get("reduceOnly")),
            "clientOrderId": client_order_id,
        }

    def cancel_order(self, symbol: str, client_order_id: str, *, timeout_seconds: int = 10,
                     algo: bool = False) -> dict[str, Any] | None:
        return {"dry_run": True, "status": "CANCELED"} if self._submitted.pop(client_order_id, None) else None


class BinanceTestnetOrderAdapter:
    """The real testnet adapter. Same posture as the live one, none of its credentials or state."""

    tool_id = TESTNET_ADAPTER_TOOL_ID
    tool_version = TESTNET_ADAPTER_TOOL_VERSION
    provider_id = TESTNET_PROVIDER_ID
    venue = VENUE_TESTNET
    # True because this really does cross the network — the operator banner must say so. It is not
    # what makes a path "live": every live consumer reads `select_order_adapter`, which cannot
    # return this class.
    network_egress = True

    def __init__(self, *, base_url: str = TESTNET_BASE_URL, authorization: Authorization | None = None):
        host = (urllib.parse.urlparse(base_url).hostname or "").lower()
        if host not in ALLOWED_TESTNET_HOSTS:
            # A URL typo must fail loudly rather than sign a request to an unexpected host — and
            # in this direction the unexpected host would be the mainnet one.
            raise ToolError(TESTNET_HOST_NOT_ALLOWED, "testnet base URL is not an allowed testnet host")
        self._base_url = base_url.rstrip("/")
        self._authorization = authorization

    def _assert(self) -> None:
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=TESTNET_TRADING_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )

    def _signed_request(self, method: str, path: str, params: Mapping[str, Any], *,
                        timeout_seconds: int) -> tuple[Any, int | None]:
        """One signed testnet request. Returns ``(parsed_body, venue_error_code)``.

        Deliberately not inherited from the live adapter: that method reads its credentials from
        the live module's constants, so sharing it would sign testnet requests with the mainnet
        key. The duplication is the smaller cost."""
        self._assert()
        api_key = os.environ.get(TESTNET_API_KEY_ENV, "").strip()
        api_secret = os.environ.get(TESTNET_API_SECRET_ENV, "").strip()
        if not api_key or not api_secret:
            # Names only — the absence of a credential is reportable, its value never is.
            raise ToolError(
                NO_ORDER_API_KEY,
                f"testnet order credentials are not configured "
                f"({TESTNET_API_KEY_ENV}/{TESTNET_API_SECRET_ENV})",
            )
        query = {k: v for k, v in params.items() if v is not None}
        query.setdefault("recvWindow", RECV_WINDOW_MS)
        query["timestamp"] = int(time.time() * 1000)
        encoded = urllib.parse.urlencode(query)
        signature = hmac.new(api_secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
        request = urllib.request.Request(
            f"{self._base_url}{path}?{encoded}&signature={signature}",
            method=method,
            headers={"Accept": "application/json", "X-MBX-APIKEY": api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            # The venue puts its reason in the body. Read it; never echo the request URL, which
            # carries the signature.
            try:
                body = json.loads(exc.read().decode("utf-8"))
                code = int(body.get("code")) if isinstance(body, dict) else None
            except Exception:  # noqa: BLE001 — an unreadable error body must not mask the failure
                body, code = None, None
            if code is None:
                raise ToolError(ORDER_TRANSPORT, f"testnet order request rejected (HTTP {exc.code})") from None
            return body, code
        except (TimeoutError, urllib.error.URLError):
            raise ToolError(ORDER_TRANSPORT, "testnet order request failed or timed out") from None
        try:
            return json.loads(raw), None
        except ValueError:
            raise ToolError(ORDER_MALFORMED_RESULT, "testnet order endpoint returned an unparseable response") from None

    def submit(self, order_request: Mapping[str, Any], *, timeout_seconds: int = 10) -> dict[str, Any]:
        body, code = self._signed_request(
            "POST",
            ALGO_ORDER_PATH if is_algo_request(order_request) else ORDER_PATH,
            dict(order_request),
            timeout_seconds=timeout_seconds,
        )
        if code is not None:
            if code == VENUE_DUPLICATE_CLIENT_ORDER_ID:
                raise ToolError(
                    ORDER_REJECTED,
                    f"duplicate client order id ({code}) — the original order already landed; "
                    "reconcile decides the outcome",
                )
            msg = body.get("msg") if isinstance(body, dict) else None
            raise ToolError(ORDER_REJECTED, f"testnet rejected the order (code {code}): {msg}")
        return body if isinstance(body, dict) else {}

    def fetch_order(self, symbol: str, client_order_id: str, *, timeout_seconds: int = 10,
                    algo: bool = False) -> dict[str, Any] | None:
        params = ({"clientAlgoId": client_order_id} if algo
                  else {"symbol": symbol, "origClientOrderId": client_order_id})
        body, code = self._signed_request(
            "GET", ALGO_ORDER_PATH if algo else ORDER_PATH, params, timeout_seconds=timeout_seconds
        )
        if code is not None:
            if code == VENUE_ORDER_DOES_NOT_EXIST:
                return None
            msg = body.get("msg") if isinstance(body, dict) else None
            raise ToolError(ORDER_REJECTED, f"testnet refused the order query (code {code}): {msg}")
        if not isinstance(body, dict):
            return None
        return normalize_algo_order(body) if algo else body

    def cancel_order(self, symbol: str, client_order_id: str, *, timeout_seconds: int = 10,
                     algo: bool = False) -> dict[str, Any] | None:
        body, code = self._signed_request(
            "DELETE",
            ALGO_ORDER_PATH if algo else ORDER_PATH,
            {"clientAlgoId": client_order_id} if algo
            else {"symbol": symbol, "origClientOrderId": client_order_id},
            timeout_seconds=timeout_seconds,
        )
        if code is not None:
            if code in (VENUE_UNKNOWN_ORDER, VENUE_ORDER_DOES_NOT_EXIST):
                return None       # already gone: filled, cancelled, or never placed
            msg = body.get("msg") if isinstance(body, dict) else None
            raise ToolError(ORDER_REJECTED, f"testnet refused the cancel (code {code}): {msg}")
        return body if isinstance(body, dict) else None


def select_testnet_order_adapter(*, now: str | None = None, root: Any = None):
    """The capable testnet adapter behind its own opt-in, else the inert one.

    Its own env gate, so the live switch is neither a prerequisite nor a way in: a machine with
    ``MVP_LIVE_TRADING`` unset can still earn its testnet evidence, and one with it set gains no
    testnet capability by that alone."""
    return safety_gate.select_env_gated(
        env_var=TESTNET_TRADING_ENV,
        opt_in_value=REAL_TESTNET_TRADING,
        flags=TESTNET_TRADING_FLAGS,
        provider_id=TESTNET_PROVIDER_ID,
        default_factory=DryRunTestnetOrderAdapter,
        gated_factory=lambda authorization: BinanceTestnetOrderAdapter(authorization=authorization),
    )




# --- the testnet order guard ---------------------------------------------------------
#
# Its own guard rather than a purpose-shaped branch in `evaluate_live_order_guard`. Half of that
# guard is about caps a registered budget declares in real USDT, which no testnet venue has (and
# should not: a budget is an authorization to spend money). Teaching the live guard to skip those
# for one purpose would put a bypass in the one function whose whole value is that it has none.
# What this guard keeps is everything that is about the MACHINE's posture rather than the money:
# the stage, both halts, and the intent's own shape. What it adds is the bound the testnet path
# carries in code, because there is no record to declare one.

TESTNET_MAX_ORDER_NOTIONAL_USDT = 200.0
TESTNET_MAX_DAILY_ORDERS = 20

TESTNET_STATUS_BLOCKED = "BLOCKED"
TESTNET_STATUS_REPAIR_REQUIRED = "REPAIR_REQUIRED"
TESTNET_STATUS_READY = "READY"


def evaluate_testnet_order_guard(
    intent: Mapping[str, Any],
    *,
    gate_open: bool,
    runtime_active: bool,
    manual_kill_switch: bool,
    submitted_today: int,
    execution_stage: Any,
    max_notional_usdt: float = TESTNET_MAX_ORDER_NOTIONAL_USDT,
    max_daily_orders: int = TESTNET_MAX_DAILY_ORDERS,
) -> dict[str, Any]:
    """The last gate before a testnet order. Pure: reads no file and opens no socket.

    Checks accumulate, like the live guard's, so the caller sees the complete refusal."""
    from .execution_stage import PURPOSE_TESTNET, required_stage
    from .live_order import _shape_repairs

    blocks: list[str] = []
    # 1. The stage. A testnet order is the evidence for the climb out of SIGNED_TESTNET, so that
    #    is the rung it needs — and a machine below it (or with no binding record) sends nothing.
    if not execution_stage.allows(PURPOSE_TESTNET):
        needs = required_stage(PURPOSE_TESTNET)
        why = (f"reads {execution_stage.stage}" if execution_stage.valid
               else f"reads READ_ONLY ({execution_stage.reason_code})")
        blocks.append(
            f"execution stage {why}; a signed testnet order needs {needs} - register a transition "
            "with scripts/register_execution_stage.py (Thomas approves it)"
        )
    # 2. The opt-in. Its own switch, never the live one.
    if not gate_open:
        blocks.append(f"signed testnet trading is not enabled ({TESTNET_TRADING_ENV} is not '{REAL_TESTNET_TRADING}')")
    # 3. Both halts. A machine its operator has stopped sends nothing anywhere, money or not.
    if manual_kill_switch:
        blocks.append("manual kill switch is engaged")
    if not runtime_active:
        blocks.append("runtime is not ACTIVE; kill_blocks external_execution forbids an order")
    # 4. The bound this path carries in code, because no record declares one for a venue that
    #    trades no money. Both are the testnet venue's own counter (PR1d-0).
    notional = 0.0
    try:
        notional = float(intent.get("order_notional_usdt") or 0.0)
    except (TypeError, ValueError):
        notional = 0.0
    if notional > max_notional_usdt:
        blocks.append(f"testnet order notional {notional} exceeds the path's cap {max_notional_usdt}")
    if submitted_today >= max_daily_orders:
        blocks.append(f"testnet daily order cap reached ({submitted_today}/{max_daily_orders})")
    # 5. A connectivity probe must never ride an order path, here either.
    if intent.get("connectivity_test"):
        blocks.append("connectivity_test intent cannot use the testnet order path")
    if intent.get("reduce_only") and notional <= 0:
        pass  # a reduceOnly close carries no new exposure; its size is the position's
    repairs = _shape_repairs(intent)
    status = (TESTNET_STATUS_BLOCKED if blocks
              else TESTNET_STATUS_REPAIR_REQUIRED if repairs else TESTNET_STATUS_READY)
    return {
        "status": status,
        "approved": status == TESTNET_STATUS_READY,
        "blocks": blocks,
        "repairs": repairs,
        "venue": VENUE_TESTNET,
        "execution_stage": execution_stage.stage,
        "notional_usdt": notional,
        "submitted_today": submitted_today,
        "close_guard": False,
    }


class TestnetOrderCounter(_LiveOrderCounter):
    """The testnet venue's own daily counter.

    The live counter's file format and its locked read-modify-write, on the testnet venue's path
    (PR1d-0's axis) and behind the testnet provider id — so a testnet submission cannot be counted
    against the live daily cap, and the live authorization cannot write this one. Subclassed
    rather than copied because the thing being shared is a counter file, not a capability: the
    separation that matters (host, credentials, provider, state path) is unchanged."""

    provider_id = TESTNET_PROVIDER_ID

    def __init__(self, *, root: Any = None, authorization: Authorization | None = None):
        super().__init__(root=root, authorization=authorization, venue=VENUE_TESTNET)


def count_testnet_today(root: Any = None, *, day: str | None = None) -> int:
    """Testnet orders submitted today. An ungated read, like the live one."""
    from .live_order import count_today

    return count_today(root, day=day, venue=VENUE_TESTNET)


__all__ = [
    "ALLOWED_TESTNET_HOSTS",
    "BinanceTestnetOrderAdapter",
    "DryRunTestnetOrderAdapter",
    "REAL_TESTNET_TRADING",
    "TESTNET_ADAPTER_TOOL_ID",
    "TESTNET_API_KEY_ENV",
    "TESTNET_API_SECRET_ENV",
    "TESTNET_BASE_URL",
    "TESTNET_HOST_NOT_ALLOWED",
    "TESTNET_PROVIDER_ID",
    "TESTNET_TRADING_ENV",
    "TESTNET_TRADING_FLAGS",
    "TESTNET_MAX_DAILY_ORDERS",
    "TESTNET_MAX_ORDER_NOTIONAL_USDT",
    "TestnetOrderCounter",
    "count_testnet_today",
    "evaluate_testnet_order_guard",
    "select_testnet_order_adapter",
]
