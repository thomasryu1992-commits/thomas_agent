"""LP4: the signed live order adapter — the one place that can POST a real order.

Ports ``execution/live_canary_adapter.py`` from the frozen ``crypto_AI_System`` project
(built and verified against the real venue there: signed testnet FILL 2026-07-15, one
mainnet canary FILL reconciled with zero mismatches 2026-07-16). Everything shipped before
this module either **reads** (``account.py``) or **refuses** (``live_order.py``); this is
the first code in the runtime that can place, query, or cancel an order at a venue.

It is nonetheless **not reachable** from any autonomous path. Nothing imports it — no
cycle, scheduler, pipeline, or operator leg — exactly as the live surface was before it
(`CRYPTO_LIVE_EXECUTION_VERIFICATION_V0.1.md` §"the live surface is standalone"). The
three off-switches (``live_readiness.ORDER_PATH_IMPLEMENTED``, the policy's
``financial_transaction_execution_implemented`` / ``financial_executor_enabled``) all stay
**false**: LP5 wires an order path, and flipping them is that increment's deliberate,
lockstep step. Until then a real submission is unreachable, not merely disabled.

What the port keeps, verbatim in intent:

* **Host allowlist at construction.** Only ``fapi.binance.com`` may ever be signed
  against. A URL typo fails loudly rather than pointing a live key somewhere unexpected —
  the same rule ``account.py`` already enforces for the read key.
* **Secrets never surface.** The key/secret are read from the environment at call time,
  never stored on the object, never returned, logged, or recorded; the signature is
  redacted out of the echoed request, and a transport failure reports a deliberately
  generic message because the signed URL would otherwise carry the signature into it.
* **Never auto-retry.** A POST that failed ambiguously (timeout / 5xx / rate limit) may
  already be live at the venue. Retrying it is how one order becomes two. The adapter
  classifies the failure and stops; resolving it is the caller's job, by querying the
  deterministic ``client_order_id`` the intent already carries.
* **``reduce_only`` is translated faithfully** into the venue's ``reduceOnly`` flag. The
  verification record calls this out as the one translation that must be right: the close
  path's "can only ever shrink a position, never open one" guarantee rests entirely on it.

Gating is the ``live_trading`` grant — the single per-machine switch that also gates the
P&L ledger, the daily counter, and the canary registry (`CRYPTO_LIVE_EXECUTION_V0.1.md`
§"One grant is the whole switch"). Deleting the grant file is a live revocation because the
authorization is re-asserted at the moment of egress. The default is
:class:`DryRunLiveAdapter`, which opens no socket; the env var alone fails closed.
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
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .. import safety_gate, timeutil
from ..errors import ToolBlocked, ToolError
from ..safety_gate import Authorization
from .live_pnl import LIVE_TRADING_ENV, LIVE_TRADING_FLAGS, LIVE_TRADING_PROVIDER_ID, REAL_LIVE_TRADING

ADAPTER_TOOL_ID = "crypto.live_order.adapter"
ADAPTER_TOOL_VERSION = "0.1.0"

# Mainnet USD-M Futures only, checked at construction. Testnet is deliberately absent: a
# testnet host here would mean one class could be pointed at either venue, and "which venue
# did this order go to" must never depend on a config value.
ALLOWED_LIVE_HOSTS = frozenset({"fapi.binance.com"})
DEFAULT_LIVE_BASE_URL = "https://fapi.binance.com"

ORDER_PATH = "/fapi/v1/order"

# The trading key is its OWN pair of env vars, separate from the read-only account key
# (`account.py`). One key that can trade and one that can only read must be rotatable and
# revocable independently — the read key is deliberately the narrower blast radius.
LIVE_API_KEY_ENV = "BINANCE_LIVE_API_KEY"
LIVE_API_SECRET_ENV = "BINANCE_LIVE_API_SECRET"

RECV_WINDOW_MS = 5000
DEFAULT_TIMEOUT_SECONDS = 10

# Failure classes. The distinction is the whole safety property of this module:
#
# - AMBIGUOUS: the POST may have reached the venue (timeout, 5xx, 429). The order might be
#   live. Never retry; resolve by querying the client order id.
# - REJECTED: the venue answered and refused (4xx other than 429). No order exists.
# - UNKNOWN: anything that fits neither, treated as ambiguous by callers that must choose,
#   because "we do not know" is not "nothing happened".
CLASSIFICATION_AMBIGUOUS = "AMBIGUOUS"
CLASSIFICATION_REJECTED = "REJECTED"
CLASSIFICATION_UNKNOWN = "UNKNOWN"

_AMBIGUOUS_STATUS = frozenset({408, 429, 500, 502, 503, 504})

# Transport signature: (method, url, params, headers, timeout) -> (status_code, body).
# Injectable so tests exercise every path without a socket.
Transport = Callable[[str, str, dict, dict, float], "tuple[int, Any]"]


def classify_exchange_error(*, status_code: int | None = None, error_name: str | None = None) -> str:
    """Classify a failed submission. Ambiguity is the default, never the exception.

    A transport-level failure (no status at all) is always ambiguous: the request may have
    been delivered and the *response* lost, which is indistinguishable from here. Only a
    venue answer that is definitively a refusal counts as REJECTED."""
    if status_code is None:
        return CLASSIFICATION_AMBIGUOUS if error_name else CLASSIFICATION_UNKNOWN
    if status_code in _AMBIGUOUS_STATUS or status_code >= 500:
        return CLASSIFICATION_AMBIGUOUS
    if 400 <= status_code < 500:
        return CLASSIFICATION_REJECTED
    return CLASSIFICATION_UNKNOWN


def redact_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """The request as it may be echoed into a record: signature removed.

    The rest is metadata the operator needs to reconcile an order (symbol, side, quantity,
    client id). The signature is the only credential-derived field in it."""
    redacted = dict(params)
    if "signature" in redacted:
        redacted["signature"] = "***redacted***"
    return redacted


class LiveOrderAdapterProtocol(Protocol):
    """What a live order adapter can do. Deliberately narrow: submit, query, cancel."""

    adapter_id: str
    adapter_version: str
    network_egress: bool

    def submit_order(self, intent: Mapping[str, Any]) -> dict[str, Any]: ...
    def query_order(self, symbol: str, client_order_id: str) -> dict[str, Any]: ...
    def cancel_order(self, symbol: str, client_order_id: str) -> dict[str, Any]: ...


class DryRunLiveAdapter:
    """The default adapter: describes the order it *would* send and opens no socket.

    Not a mock of a venue — it never claims an order exists. ``submitted`` is False and
    there is no exchange order id, so a caller that mistakes it for the real adapter
    records a non-submission rather than a fictional fill."""

    adapter_id = "dry_run"
    adapter_version = f"{ADAPTER_TOOL_VERSION}-dryrun"
    network_egress = False

    def submit_order(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "ok": False,
            "submitted": False,
            "dry_run": True,
            "http_status": None,
            "client_order_id": intent.get("client_order_id"),
            "exchange_order_id": None,
            "classification": None,
            "request": {
                "symbol": intent.get("symbol"),
                "side": intent.get("side"),
                "type": intent.get("order_type_exchange", "MARKET"),
                "quantity": intent.get("quantity"),
                "reduceOnly": "true" if intent.get("reduce_only") else None,
            },
            "error": {"code": None, "msg": "dry-run adapter: no order was sent"},
        }

    def query_order(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        return {"ok": False, "dry_run": True, "http_status": None, "response": None,
                "error": {"code": None, "msg": "dry-run adapter: no venue to query"}}

    def cancel_order(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        return {"ok": False, "dry_run": True, "http_status": None, "response": None,
                "error": {"code": None, "msg": "dry-run adapter: no order to cancel"}}


def _urllib_transport(
    method: str, url: str, params: dict, headers: dict, timeout: float
) -> tuple[int, Any]:
    """The real transport: urllib, matching ``account.py`` (no third-party dependency).

    The signature rides in the query string, so the URL is built here and never returned to
    a caller — the exception path deliberately raises a generic error instead."""
    encoded = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url}?{encoded}", method=method,
        headers={"Accept": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=float(timeout)) as response:
            raw = response.read().decode("utf-8")
            status = int(response.status)
    except urllib.error.HTTPError as exc:  # the venue answered with an error status
        try:
            raw = exc.read().decode("utf-8")
        except Exception:  # noqa: BLE001 — a body-less error still classifies by status
            raw = ""
        status = int(exc.code)
    try:
        return status, json.loads(raw) if raw else {}
    except ValueError:
        return status, {"raw_text_present": True}


class LiveOrderAdapter:
    """Signed Binance USD-M Futures order adapter — the real one.

    Constructed only through :func:`select_live_adapter` after the Safety-Flag Gate opens
    for ``live_trading``; every operation re-asserts that authorization at the moment of
    egress, so deleting the grant file stops submissions immediately."""

    adapter_id = LIVE_TRADING_PROVIDER_ID
    adapter_version = f"{ADAPTER_TOOL_VERSION}-binance"
    provider_id = LIVE_TRADING_PROVIDER_ID
    network_egress = True

    def __init__(
        self,
        *,
        authorization: Authorization | None = None,
        base_url: str = DEFAULT_LIVE_BASE_URL,
        transport: Transport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        recv_window: int = RECV_WINDOW_MS,
    ) -> None:
        host = (urllib.parse.urlparse(base_url).hostname or "").lower()
        if host not in ALLOWED_LIVE_HOSTS:
            raise ToolBlocked(
                "HOST_NOT_ALLOWED",
                "live order base URL is not an allowed live host",
            )
        self._base_url = base_url.rstrip("/")
        self._authorization = authorization
        self._transport: Transport = transport or _urllib_transport
        self._timeout = float(timeout_seconds)
        self._recv_window = int(recv_window)

    # -- signing ---------------------------------------------------------------
    def _credentials(self) -> tuple[str, str]:
        api_key = os.environ.get(LIVE_API_KEY_ENV, "").strip()
        api_secret = os.environ.get(LIVE_API_SECRET_ENV, "").strip()
        if not api_key or not api_secret:
            # Names only — the absence of a credential is reportable, its value never is.
            raise ToolError(
                "NO_API_KEY",
                f"live trading credentials are not configured "
                f"({LIVE_API_KEY_ENV}/{LIVE_API_SECRET_ENV})",
            )
        return api_key, api_secret

    def _signed(self, params: Mapping[str, Any], api_secret: str) -> dict[str, Any]:
        signed = {k: v for k, v in params.items() if v is not None}
        signed.setdefault("recvWindow", self._recv_window)
        signed["timestamp"] = int(time.time() * 1000)
        encoded = urllib.parse.urlencode(signed)
        signed["signature"] = hmac.new(
            api_secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return signed

    def _send(self, method: str, path: str, params: Mapping[str, Any]) -> dict[str, Any]:
        """One signed request. Returns a result dict; never raises for a venue failure.

        A failure is data here, not an exception, because the *classification* of a failed
        submit (ambiguous vs rejected) is what the caller must act on — and an exception
        would tempt a retry, which is the one thing this module must never do."""
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=LIVE_TRADING_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )
        api_key, api_secret = self._credentials()
        signed = self._signed(params, api_secret)
        try:
            status_code, body = self._transport(
                method, f"{self._base_url}{path}", signed,
                {"X-MBX-APIKEY": api_key}, self._timeout,
            )
        except Exception as exc:  # noqa: BLE001 — transport failure of any kind
            # Deliberately generic: the exception text can embed the signed URL, so only
            # the exception TYPE is reported, never its message.
            return {
                "ok": False,
                "http_status": None,
                "error": {"code": None, "msg": f"live order request failed ({type(exc).__name__})"},
                "classification": classify_exchange_error(error_name=type(exc).__name__),
                "request": redact_params(signed),
            }

        ok = 200 <= int(status_code) < 300
        result: dict[str, Any] = {
            "ok": ok,
            "http_status": int(status_code),
            "response": body if ok else None,
            "request": redact_params(signed),
        }
        if not ok:
            result["error"] = {
                "code": body.get("code") if isinstance(body, dict) else None,
                "msg": body.get("msg") if isinstance(body, dict) else None,
            }
            result["classification"] = classify_exchange_error(status_code=int(status_code))
        return result

    # -- operations ------------------------------------------------------------
    def submit_order(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        """Submit ONE order from an order intent. Never auto-retries.

        The intent is the one built and guarded upstream (``live_order.build_live_order_intent``
        → ``evaluate_live_order_guard`` / ``evaluate_live_close_guard``); this method
        translates it to venue parameters and sends it. ``reduce_only`` becomes the venue's
        ``reduceOnly`` flag — the translation the close path's shrink-only guarantee rests
        on."""
        client_order_id = intent.get("client_order_id")
        if not client_order_id:
            # Without a deterministic id an ambiguous submit is unresolvable: there would be
            # nothing to query. Refuse before signing rather than send an unrecoverable order.
            raise ToolError(
                "MISSING_CLIENT_ORDER_ID",
                "a live order intent must carry a client_order_id before submission",
            )
        order_type = str(intent.get("order_type_exchange") or "MARKET")
        params: dict[str, Any] = {
            "symbol": intent.get("symbol"),
            "side": intent.get("side"),
            "type": order_type,
            "quantity": intent.get("quantity"),
            "newClientOrderId": client_order_id,
        }
        if intent.get("reduce_only"):
            params["reduceOnly"] = "true"
        if order_type == "LIMIT":
            params["price"] = intent.get("entry_price")
            params["timeInForce"] = intent.get("time_in_force", "GTC")

        result = self._send("POST", ORDER_PATH, params)
        response = result.get("response") if isinstance(result.get("response"), dict) else {}
        result["exchange_order_id"] = (response or {}).get("orderId")
        result["client_order_id"] = client_order_id
        result["submitted"] = bool(result.get("ok"))
        result["dry_run"] = False
        return result

    def query_order(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        """Read one order back by its client id — how an ambiguous submit is resolved."""
        return self._send("GET", ORDER_PATH, {"symbol": symbol, "origClientOrderId": client_order_id})

    def cancel_order(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        return self._send("DELETE", ORDER_PATH, {"symbol": symbol, "origClientOrderId": client_order_id})


def select_live_adapter(
    *, now: str | None = None, root: Path | None = None, transport: Transport | None = None,
) -> LiveOrderAdapterProtocol:
    """Return the real adapter if the ``live_trading`` gate opens, else the dry-run one.

    The same chokepoint every other gated capability uses: the capable implementation is
    built **by** the gate from its own Authorization, so it cannot exist before the grant
    does, and ``MVP_LIVE_TRADING=real`` without a valid local activation fails closed."""
    return safety_gate.select_gated(
        env_var=LIVE_TRADING_ENV,
        opt_in_value=REAL_LIVE_TRADING,
        flags=LIVE_TRADING_FLAGS,
        provider_id=LIVE_TRADING_PROVIDER_ID,
        default_factory=DryRunLiveAdapter,
        gated_factory=lambda authorization: LiveOrderAdapter(
            authorization=authorization, transport=transport,
        ),
        now=now,
        root=root,
    )


def submission_record(
    result: Mapping[str, Any], *, adapter: LiveOrderAdapterProtocol, intent: Mapping[str, Any], now: str,
) -> dict[str, Any]:
    """Evidence record for one submission attempt — metadata only.

    Carries what reconciliation needs (ids, symbol, side, quantity, outcome, classification)
    and nothing derived from a credential: the echoed request is already signature-redacted,
    and no raw venue payload is copied in."""
    return {
        "tool_id": ADAPTER_TOOL_ID,
        "tool_version": getattr(adapter, "adapter_version", ADAPTER_TOOL_VERSION),
        "adapter_id": getattr(adapter, "adapter_id", "unknown"),
        "operation": "submit_order",
        "symbol": intent.get("symbol"),
        "side": intent.get("side"),
        "quantity": intent.get("quantity"),
        "reduce_only": bool(intent.get("reduce_only")),
        "client_order_id": result.get("client_order_id"),
        "exchange_order_id": result.get("exchange_order_id"),
        "submitted": bool(result.get("submitted")),
        "dry_run": bool(result.get("dry_run")),
        "http_status": result.get("http_status"),
        "classification": result.get("classification"),
        "error": result.get("error"),
        "external_action": bool(result.get("submitted")),
        "network_egress": bool(getattr(adapter, "network_egress", False)),
        "created_at": now,
    }


__all__ = [
    "ALLOWED_LIVE_HOSTS",
    "CLASSIFICATION_AMBIGUOUS",
    "CLASSIFICATION_REJECTED",
    "CLASSIFICATION_UNKNOWN",
    "DryRunLiveAdapter",
    "LiveOrderAdapter",
    "LiveOrderAdapterProtocol",
    "classify_exchange_error",
    "redact_params",
    "select_live_adapter",
    "submission_record",
]
