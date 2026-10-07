"""Binance spot and Simple Earn balances — read-only, behind its own env gate.

Thomas 2026-10-07 (appendix C of ``docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md``;
``docs/proposals/TOTAL_ASSET_ALLOCATION_V0.1.md`` Q10): the holdings board sums the Binance spot
wallet and Simple Earn next to the Toss account and the futures margin (P2, ``holdings/combined.py``),
so the target allocation and the drawdown line are read against the whole of what is held.

**Which key, and what that cost — decided knowingly.** This module reads with the two variables
``crypto/account.py`` already reads (``BINANCE_ACCOUNT_API_KEY`` / ``_SECRET``), forwarded to
scheduler-maint as well (Thomas 2026-10-07, option A over a new read-only key). The recorded cost:
``docs/BUILD_HISTORY.md`` (2026-07-28) holds that this is the same venue key the order credentials
are derived from, so it may carry futures-trading permission at the venue — read-only here is a
property of this code, not of the key. What stays off this lane is everything that would let the
image use that permission: the live-trading switch, the order-key variables, the confirmation
phrases and ``MVP_ACCOUNT_FEED`` (``tests/test_deployment_env_passthrough.py``).

**Read-only by construction.** One host (``api.binance.com``), four GET paths, by constant: the spot
account and the two Simple Earn position lists (signed), and the public price list (no key). There is no
order, transfer, subscribe or redeem method, and a path outside the list is refused before a socket opens.

**Why not ``crypto/account.py``.** That seam is the live money path's account: the risk lane reads it
and the daily-loss breaker meters it. Nothing in ``holdings/`` imports ``crypto/`` and nothing in
``crypto/`` imports ``holdings/`` (``EXPANSION_READINESS_REVIEW_V0.1.md`` Q4), so the two variable
names are repeated below rather than imported.

**Valued the way P2 values the futures margin.** :class:`WalletSnapshot` holds class totals in USDT;
``holdings.combined`` converts them with the Toss mid-rate the same fire read, USDT taken as one US
dollar (Thomas 2026-10-07, the P2 decision): one rate per fire, so every KRW figure on one board rests
on the same rate, and the rate itself is never stored.

**What leaves.** Per-asset quantities and prices stay in this process. Only KRW class totals reach the
stored snapshot.

**Unverified until the first live read.** Binance has no testnet for ``/sapi``. Three things rest on
the official docs alone:

- the Earn row fields: ``totalAmount`` for flexible, ``amount`` for locked;
- the spot wallet listing flexible Earn again as ``LD<asset>``. A spot ``LD``-row is skipped only when
  ``<asset>`` is in the flexible rows, because ``LDO`` is a real coin;
- ``omitZeroBalances``. Zero rows are also dropped here regardless.

A missing or non-numeric field is ``None`` plus a warning, never zero.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .. import safety_gate, timeutil
from ..errors import SafetyGateBlocked, ToolBlocked, ToolError
from ..safety_gate import NETWORK_ACCESS, Authorization

TOOL_VERSION = "0.1.0"

# The gate. Its own variable, not MVP_ACCOUNT_FEED — see the module docstring.
BINANCE_WALLET_ENV = "MVP_BINANCE_WALLET"
BINANCE_WALLET_ON = "binance_wallet"
BINANCE_WALLET_PROVIDER = "binance_wallet"
_NETWORK_FLAGS = (NETWORK_ACCESS,)

# The same two variables crypto/account.py reads (Thomas 2026-10-07, option A). Repeated, not
# imported: holdings/ does not import crypto/. A test pins that the names agree.
API_KEY_ENV = "BINANCE_ACCOUNT_API_KEY"
API_SECRET_ENV = "BINANCE_ACCOUNT_API_SECRET"

BINANCE_BASE_URL = "https://api.binance.com"
SPOT_ACCOUNT_PATH = "/api/v3/account"
FLEXIBLE_PATH = "/sapi/v1/simple-earn/flexible/position"
LOCKED_PATH = "/sapi/v1/simple-earn/locked/position"
PRICES_PATH = "/api/v3/ticker/price"

# The whole egress surface, (base, path) by constant. Anything else is refused before a socket opens.
ALLOWED_REQUESTS = frozenset({
    (BINANCE_BASE_URL, SPOT_ACCOUNT_PATH),
    (BINANCE_BASE_URL, FLEXIBLE_PATH),
    (BINANCE_BASE_URL, LOCKED_PATH),
    (BINANCE_BASE_URL, PRICES_PATH),
})

RECV_WINDOW_MS = 5000
EARN_PAGE_SIZE = 100
EARN_MAX_PAGES = 10
# Per call. Four calls ride the holdings fire next to Toss's, under the maintenance pass budget (60 s).
DEFAULT_TIMEOUT_SECONDS = 5

QUOTE = "USDT"
CLASS_BTC = "btc"
CLASS_ETH = "eth"
CLASS_STABLE = "stable"
CLASS_OTHER = "other"
CLASSES = (CLASS_BTC, CLASS_ETH, CLASS_STABLE, CLASS_OTHER)
STABLECOINS = frozenset({"USDT", "USDC", "FDUSD", "TUSD", "USDP", "DAI"})


def asset_class(asset: str) -> str:
    if asset == "BTC":
        return CLASS_BTC
    if asset == "ETH":
        return CLASS_ETH
    if asset in STABLECOINS:
        return CLASS_STABLE
    return CLASS_OTHER


def _number(value: Any, warnings: list[str], where: str) -> float | None:
    """One decimal, or ``None`` with a warning. Never a silent zero."""
    if value is None or (isinstance(value, str) and not value.strip()):
        warnings.append(f"{where}: missing")
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        warnings.append(f"{where}: not numeric")
        return None


@dataclass(frozen=True)
class WalletSnapshot:
    """Class totals in USDT. No asset, no quantity, no price.

    ``earn_usdt`` is ``None`` when the Earn read failed, never an empty zero."""

    spot_usdt: Mapping[str, float]
    earn_usdt: Mapping[str, float] | None
    unpriced_assets: int
    collected_at: str
    latency_ms: int
    warnings: tuple[str, ...] = field(default_factory=tuple)


class WalletFeed(Protocol):
    feed_id: str
    feed_version: str
    network_egress: bool

    def wallet_snapshot(self, *, timeout_seconds: int) -> WalletSnapshot | None: ...


class NoWalletFeed:
    """The inert default: no key, no socket. ``None`` is a normal state, not an error."""

    feed_id = "none"
    feed_version = f"{TOOL_VERSION}-none"
    network_egress = False

    def wallet_snapshot(self, *, timeout_seconds: int) -> WalletSnapshot | None:
        return None


class _ApiRejected(ToolError):
    """A 4xx/5xx carrying the venue's own numeric code, never its message."""

    def __init__(self, host: str, status: int, code: str):
        reason = {401: "BINANCE_WALLET_FORBIDDEN", 403: "BINANCE_WALLET_FORBIDDEN",
                  418: "BINANCE_WALLET_RATE_LIMITED", 429: "BINANCE_WALLET_RATE_LIMITED"}.get(
                      status, "BINANCE_WALLET_REJECTED")
        super().__init__(reason, f"{host} refused the read (HTTP {status}, code {code or '?'})")
        self.status = status
        self.code = code


class BinanceWalletFeed:
    """Read of the Binance spot wallet and Simple Earn. Built only by :func:`select_wallet_feed`
    behind the gate; every egress re-checks that authorization. Has no order method."""

    feed_id = BINANCE_WALLET_PROVIDER
    feed_version = f"{TOOL_VERSION}-binance"
    provider_id = BINANCE_WALLET_PROVIDER
    network_egress = True

    def __init__(self, *, authorization: Authorization | None = None) -> None:
        self._authorization = authorization

    @staticmethod
    def _credentials() -> tuple[str, str]:
        api_key = os.environ.get(API_KEY_ENV, "").strip()
        api_secret = os.environ.get(API_SECRET_ENV, "").strip()
        missing = [name for name, value in ((API_KEY_ENV, api_key), (API_SECRET_ENV, api_secret)) if not value]
        if missing:
            # Names only — a message that echoed a value would put a secret in a log.
            raise ToolError("NO_API_KEY", f"environment variables not set: {', '.join(missing)}")
        return api_key, api_secret

    def _check_gate(self) -> None:
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )

    # -- transport -------------------------------------------------------------------------

    def _get(self, base: str, path: str, params: Mapping[str, Any] | None = None, *,
             signed: bool, what: str, timeout_seconds: int) -> Any:
        if (base, path) not in ALLOWED_REQUESTS:
            raise ToolBlocked("BINANCE_WALLET_PATH_REFUSED", f"{what}: not a read this feed makes")
        self._check_gate()
        query = dict(params or {})
        headers = {"Accept": "application/json"}
        if signed:
            api_key, api_secret = self._credentials()
            query["recvWindow"] = RECV_WINDOW_MS
            query["timestamp"] = int(time.time() * 1000)
            encoded = urllib.parse.urlencode(query)
            signature = hmac.new(api_secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
            encoded = f"{encoded}&signature={signature}"
            headers["X-MBX-APIKEY"] = api_key
        else:
            encoded = urllib.parse.urlencode(query)
        url = f"{base}{path}" + (f"?{encoded}" if encoded else "")
        request = urllib.request.Request(url, method="GET", headers=headers)
        host = urllib.parse.urlparse(base).hostname or "?"
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            code = ""
            try:
                body = json.loads(exc.read().decode("utf-8"))
                if isinstance(body, dict):
                    code = str(body.get("code") or (body.get("error") or {}).get("name") or "")
            except (ValueError, AttributeError, OSError):
                pass
            raise _ApiRejected(host, exc.code, code) from None
        except (OSError, http.client.HTTPException):
            # Deliberately generic: a signed URL carries its signature, so it never reaches a message.
            raise ToolError("TOOL_TRANSPORT", f"{what} failed or timed out") from None
        try:
            return json.loads(raw)
        except ValueError:
            raise ToolError("MALFORMED_RESULT", f"{what} returned an unparseable response") from None

    # -- the reads -------------------------------------------------------------------------

    def _spot(self, warnings: list[str], *, timeout_seconds: int) -> dict[str, float]:
        body = self._get(BINANCE_BASE_URL, SPOT_ACCOUNT_PATH, {"omitZeroBalances": "true"},
                         signed=True, what="spot account", timeout_seconds=timeout_seconds)
        if not isinstance(body, dict) or not isinstance(body.get("balances"), list):
            raise ToolError("MALFORMED_RESULT", "spot account carried no balances")
        out: dict[str, float] = {}
        for row in body["balances"]:
            if not isinstance(row, dict):
                continue
            asset = str(row.get("asset") or "").strip()
            free = _number(row.get("free"), warnings, f"spot {asset} free")
            locked = _number(row.get("locked"), warnings, f"spot {asset} locked")
            if not asset or free is None or locked is None:
                continue
            if free + locked > 0:
                out[asset] = out.get(asset, 0.0) + free + locked
        return out

    def _earn(self, path: str, amount_field: str, what: str, warnings: list[str], *,
              timeout_seconds: int) -> dict[str, float]:
        out: dict[str, float] = {}
        seen = 0
        for page in range(1, EARN_MAX_PAGES + 1):
            body = self._get(BINANCE_BASE_URL, path, {"current": page, "size": EARN_PAGE_SIZE},
                             signed=True, what=what, timeout_seconds=timeout_seconds)
            rows = body.get("rows") if isinstance(body, dict) else None
            if not isinstance(rows, list):
                raise ToolError("MALFORMED_RESULT", f"{what} carried no rows")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                asset = str(row.get("asset") or "").strip()
                amount = _number(row.get(amount_field), warnings, f"{what} {asset} {amount_field}")
                if asset and amount is not None and amount > 0:
                    out[asset] = out.get(asset, 0.0) + amount
            seen += len(rows)
            total = body.get("total")
            if not rows or len(rows) < EARN_PAGE_SIZE or (isinstance(total, int) and seen >= total):
                return out
        warnings.append(f"{what}: more than {EARN_MAX_PAGES} pages; the rest not read")
        return out

    def _prices(self, *, timeout_seconds: int) -> dict[str, float]:
        rows = self._get(BINANCE_BASE_URL, PRICES_PATH, signed=False, what="price list",
                         timeout_seconds=timeout_seconds)
        prices: dict[str, float] = {}
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict):
                try:
                    price = float(row.get("price"))
                except (TypeError, ValueError):
                    continue
                if price > 0:
                    prices[str(row.get("symbol") or "")] = price
        return prices

    @staticmethod
    def _value(asset: str, quantity: float, prices: Mapping[str, float]) -> float | None:
        if asset == QUOTE:
            return quantity
        if f"{asset}{QUOTE}" in prices:
            return quantity * prices[f"{asset}{QUOTE}"]
        if f"{QUOTE}{asset}" in prices:
            return quantity / prices[f"{QUOTE}{asset}"]
        return None

    def wallet_snapshot(self, *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> WalletSnapshot:
        self._check_gate()
        started = time.monotonic()
        warnings: list[str] = []
        spot = self._spot(warnings, timeout_seconds=timeout_seconds)   # a failure here fails the read
        earn: dict[str, float] | None
        try:
            flexible = self._earn(FLEXIBLE_PATH, "totalAmount", "flexible earn", warnings,
                                  timeout_seconds=timeout_seconds)
            locked = self._earn(LOCKED_PATH, "amount", "locked earn", warnings, timeout_seconds=timeout_seconds)
        except ToolError as exc:
            warnings.append(f"earn positions unavailable ({exc.reason_code}); earn not counted")
            flexible, earn = {}, None
        else:
            earn = dict(flexible)
            for asset, amount in locked.items():
                earn[asset] = earn.get(asset, 0.0) + amount
        # The spot wallet lists flexible Earn again as LD<asset>. Skip only a confirmed duplicate.
        spot = {asset: qty for asset, qty in spot.items()
                if not (asset.startswith("LD") and asset[2:] in flexible)}

        needs_price = any(asset != QUOTE for asset in list(spot) + list(earn or {}))
        prices = self._prices(timeout_seconds=timeout_seconds) if needs_price else {}
        unpriced: set[str] = set()

        def by_class(holdings: Mapping[str, float]) -> dict[str, float]:
            totals = {name: 0.0 for name in CLASSES}
            for asset, quantity in holdings.items():
                value = self._value(asset, quantity, prices)
                if value is None:
                    unpriced.add(asset)
                    continue
                totals[asset_class(asset)] += value
            return totals

        spot_usdt = by_class(spot)
        earn_usdt = by_class(earn) if earn is not None else None
        if unpriced:
            # A count, not the names: the names are holdings, and holdings stay in this process.
            warnings.append(f"{len(unpriced)} asset(s) with no USDT price; left out of the totals")
        return WalletSnapshot(
            spot_usdt=spot_usdt,
            earn_usdt=earn_usdt,
            unpriced_assets=len(unpriced),
            collected_at=timeutil.utc_now_iso(),
            latency_ms=int((time.monotonic() - started) * 1000),
            warnings=tuple(warnings),
        )


def select_wallet_feed() -> WalletFeed:
    """The Binance feed if ``MVP_BINANCE_WALLET=binance_wallet``, else the inert one. The capable feed
    is built by the gate, so it cannot exist before its authorization does."""
    return safety_gate.select_env_gated(
        env_var=BINANCE_WALLET_ENV,
        opt_in_value=BINANCE_WALLET_ON,
        flags=_NETWORK_FLAGS,
        provider_id=BINANCE_WALLET_PROVIDER,
        default_factory=NoWalletFeed,
        gated_factory=lambda authorization: BinanceWalletFeed(authorization=authorization),
    )


def read_wallet(
    *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS, feed: WalletFeed | None = None,
) -> tuple[WalletSnapshot | None, str | None]:
    """Read once. Degrades rather than raising: ``(snapshot, None)``, or ``(None, reason_code)`` when the
    gate is closed (``NOT_CONFIGURED``) or the read failed (the error's own code)."""
    try:
        if feed is None:
            feed = select_wallet_feed()
        snapshot = feed.wallet_snapshot(timeout_seconds=timeout_seconds)
    except (ToolError, ToolBlocked, SafetyGateBlocked) as exc:
        return None, exc.reason_code
    if snapshot is None:
        return None, "NOT_CONFIGURED"
    return snapshot, None
