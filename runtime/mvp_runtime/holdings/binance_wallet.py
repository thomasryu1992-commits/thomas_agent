"""Binance spot and Simple Earn balances — read-only, behind its own env gate.

Thomas 2026-10-07 (appendix C of ``docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md``;
``docs/proposals/TOTAL_ASSET_ALLOCATION_V0.1.md`` Q10): the holdings board sums the Binance spot
wallet and Simple Earn next to the Toss account and the futures margin (P2, ``holdings/combined.py``),
so the target allocation and the drawdown line are read against the whole of what is held.

**Which key: the read-only one (H1-b, Thomas 2026-10-08).** This module reads with the dedicated venue
key Thomas issued with "Enable Reading" only (``BINANCE_READ_API_KEY`` / ``_SECRET``), the same pair
``crypto/account.py`` reads on its READ plane. It never reads the trading pair (``BINANCE_ACCOUNT_*``) or the
order key, and there is no fallback: without the read pair the feed fails closed with ``NO_API_KEY``.
History: option A (2026-10-07) had shared the account key, which is the order key; H1-a removed it from
scheduler-maint the same day, and this pair replaced it. Which service receives the read pair is pinned in
``tests/test_deployment_env_passthrough.py``; the wallet gate stays off until H2–H4 (appendix C; H3 and
H4 kept as preconditions by Thomas 2026-10-08), and a one-off probe that reads with it is not an activation.

**Read-only by construction.** One host (``api.binance.com``), GET paths by constant: the spot account and
the two Simple Earn position lists (signed), the public price list (no key) and, since H6a, the cash-flow
histories in :data:`FLOW_SOURCES` (signed; records of money that already moved). There is no order,
transfer, subscribe or redeem method, and a path outside the list is refused before a socket opens.

**Why not ``crypto/account.py``.** That seam is the live money path's account: the risk lane reads it
and the daily-loss breaker meters it. Nothing in ``holdings/`` imports ``crypto/`` and nothing in
``crypto/`` imports ``holdings/`` (``EXPANSION_READINESS_REVIEW_V0.1.md`` Q4), so the two variable
names are repeated below rather than imported.

**Valued the way P2 values the futures margin.** :class:`WalletSnapshot` holds class totals in USDT;
``holdings.combined`` converts them with the Toss mid-rate the same fire read, USDT taken as one US
dollar (Thomas 2026-10-07, the P2 decision): one rate per fire, so every KRW figure on one board rests
on the same rate, and the rate itself is never stored.

**What leaves.** Per-asset quantities, prices and names stay in this process. The KRW class totals reach
the terminal-only local file; the stored snapshot the doors read carries none of them (H3,
``holdings.disclosure``).

**Unverified until the first live read.** Binance has no testnet for ``/sapi``. Three things rest on
the official docs alone:

- the Earn row fields: ``totalAmount`` for flexible, ``amount`` for locked;
- the spot wallet listing flexible Earn again as ``LD<asset>``. A spot ``LD``-row is skipped only when
  ``<asset>`` is in the flexible rows, because ``LDO`` is a real coin;
- ``omitZeroBalances``. Zero rows are also dropped here regardless.

A missing or non-numeric field is ``None`` plus a warning, never zero, and the row is counted in
``invalid_rows`` so the board can refuse to call the total complete (H2).
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

# The read-only pair crypto/account.py reads on its READ plane (H1-b). Repeated, not imported:
# holdings/ does not import crypto/. A test pins that the names agree.
API_KEY_ENV = "BINANCE_READ_API_KEY"
API_SECRET_ENV = "BINANCE_READ_API_SECRET"

BINANCE_BASE_URL = "https://api.binance.com"
SPOT_ACCOUNT_PATH = "/api/v3/account"
FLEXIBLE_PATH = "/sapi/v1/simple-earn/flexible/position"
LOCKED_PATH = "/sapi/v1/simple-earn/locked/position"
PRICES_PATH = "/api/v3/ticker/price"

# H6a (Thomas 2026-10-08, PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md D-H6-3): the cash-flow histories, every one
# a signed GET (USER_DATA), checked against Binance's own connector source on 2026-10-08. Each is a
# record of money that already moved; none moves money. Name -> (path, fixed params, the window
# parameter names, the documented longest window in days).
DEPOSIT_HISTORY_PATH = "/sapi/v1/capital/deposit/hisrec"
WITHDRAW_HISTORY_PATH = "/sapi/v1/capital/withdraw/history"
FIAT_ORDERS_PATH = "/sapi/v1/fiat/orders"
FIAT_PAYMENTS_PATH = "/sapi/v1/fiat/payments"
PAY_TRANSACTIONS_PATH = "/sapi/v1/pay/transactions"
UNIVERSAL_TRANSFER_PATH = "/sapi/v1/asset/transfer"
FLOW_SOURCES: dict[str, tuple[str, dict[str, Any], tuple[str, str], int]] = {
    "crypto_deposit": (DEPOSIT_HISTORY_PATH, {}, ("startTime", "endTime"), 89),
    "crypto_withdraw": (WITHDRAW_HISTORY_PATH, {}, ("startTime", "endTime"), 89),
    "fiat_deposit": (FIAT_ORDERS_PATH, {"transactionType": 0}, ("beginTime", "endTime"), 89),
    "fiat_withdraw": (FIAT_ORDERS_PATH, {"transactionType": 1}, ("beginTime", "endTime"), 89),
    "fiat_buy": (FIAT_PAYMENTS_PATH, {"transactionType": 0}, ("beginTime", "endTime"), 89),
    "fiat_sell": (FIAT_PAYMENTS_PATH, {"transactionType": 1}, ("beginTime", "endTime"), 89),
    "pay": (PAY_TRANSACTIONS_PATH, {}, ("startTime", "endTime"), 89),
    # Internal moves (D-H6-1 §5: double counting across the skew window), spot <-> USD-M futures.
    "transfer_spot_to_futures": (UNIVERSAL_TRANSFER_PATH, {"type": "MAIN_UMFUTURE"}, ("startTime", "endTime"), 7),
    "transfer_futures_to_spot": (UNIVERSAL_TRANSFER_PATH, {"type": "UMFUTURE_MAIN"}, ("startTime", "endTime"), 7),
}
# PR-0 (2026-10-10): each history answers at most one page, sized by its own parameter. Name -> (the page
# size parameter, its documented maximum, whether the body carries ``total``, the row's venue id). Binance's
# pages (checked 2026-10-10): deposit/withdraw ``limit`` max 1000, fiat ``rows`` max 500 with ``total``,
# Pay ``limit`` max 100 and no page or total, universal transfer ``size`` max 100 (default 10) with
# ``total``. No page documents its sort order or whether its bounds are inclusive, so a full page is read
# again as two halves of its window rather than by page number or offset.
FLOW_PAGES: dict[str, tuple[str, int, bool, str]] = {
    "crypto_deposit": ("limit", 1000, False, "id"),
    "crypto_withdraw": ("limit", 1000, False, "id"),
    "fiat_deposit": ("rows", 500, True, "orderNo"),
    "fiat_withdraw": ("rows", 500, True, "orderNo"),
    "fiat_buy": ("rows", 500, True, "orderNo"),
    "fiat_sell": ("rows", 500, True, "orderNo"),
    "pay": ("limit", 100, False, "transactionId"),
    "transfer_spot_to_futures": ("size", 100, True, "tranId"),
    "transfer_futures_to_spot": ("size", 100, True, "tranId"),
}
# A history that cannot be shown complete: the source fails this fire and keeps its cursor, so the next
# fire reads the same stretch again. Never a partial list read as the whole.
FLOW_INCOMPLETE = "BINANCE_FLOW_HISTORY_INCOMPLETE"
# Per source and read. One request in the normal case, as before; splits only when a page comes back full.
# Small on purpose: withdraw history and fiat orders weigh heavily against the account's request limit.
FLOW_MAX_REQUESTS = 8
# Fiat and Pay wrap their rows in ``code`` ("000000" when it worked) and ``success``. A body that says the
# call failed, says neither, or contradicts itself is a refused read — never an empty history.
FLOW_ENVELOPE_SOURCES = frozenset({"fiat_deposit", "fiat_withdraw", "fiat_buy", "fiat_sell", "pay"})
FLOW_OK_CODE = "000000"
FLOW_READ_SECONDS = 15.0

# The whole egress surface, (base, path) by constant. Anything else is refused before a socket opens.
ALLOWED_REQUESTS = frozenset({
    (BINANCE_BASE_URL, SPOT_ACCOUNT_PATH),
    (BINANCE_BASE_URL, FLEXIBLE_PATH),
    (BINANCE_BASE_URL, LOCKED_PATH),
    (BINANCE_BASE_URL, PRICES_PATH),
    *((BINANCE_BASE_URL, path) for path, _params, _window, _days in FLOW_SOURCES.values()),
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

    ``earn_usdt`` is ``None`` when the Earn read failed, never an empty zero. The three valuation facts
    H2 judges by (Thomas 2026-10-08, ``holdings.combined``) are fields, not warning text: ``unpriced_assets``
    (held, no USDT price, left out), ``earn_truncated`` (more Earn pages than read) and ``invalid_rows``
    (a balance row whose asset or amount was missing or not numeric, skipped). Any of them makes the
    wallet's value a lower bound, never a total."""

    spot_usdt: Mapping[str, float]
    earn_usdt: Mapping[str, float] | None
    unpriced_assets: int
    collected_at: str
    latency_ms: int
    warnings: tuple[str, ...] = field(default_factory=tuple)
    earn_truncated: bool = False
    invalid_rows: int = 0
    # H3 (Thomas 2026-10-08): which assets make up each class, for the single-holding rule
    # (``holdings.disclosure``). In-process only: no stored block carries it.
    class_assets: Mapping[str, frozenset[str]] = field(default_factory=dict)
    # H5a (Thomas 2026-10-08): each priced asset's USDT value (spot plus Earn), so the allocation can
    # place an asset the classification file names. In-process only, like ``class_assets``.
    asset_usdt: Mapping[str, float] = field(default_factory=dict)


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


_ROW_TIME_FIELDS = ("insertTime", "transactionTime", "timestamp", "createTime", "updateTime")


def _row_ms(row: Mapping[str, Any]) -> int | None:
    """A history row's time in ms where the venue gives one as a number (withdrawals give text: None)."""
    for name in _ROW_TIME_FIELDS:
        value = row.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
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

    def _spot(self, warnings: list[str], invalid: list[str], *, timeout_seconds: int) -> dict[str, float]:
        body = self._get(BINANCE_BASE_URL, SPOT_ACCOUNT_PATH, {"omitZeroBalances": "true"},
                         signed=True, what="spot account", timeout_seconds=timeout_seconds)
        if not isinstance(body, dict) or not isinstance(body.get("balances"), list):
            raise ToolError("MALFORMED_RESULT", "spot account carried no balances")
        out: dict[str, float] = {}
        for row in body["balances"]:
            if not isinstance(row, dict):
                invalid.append("spot")
                continue
            asset = str(row.get("asset") or "").strip()
            free = _number(row.get("free"), warnings, f"spot {asset} free")
            locked = _number(row.get("locked"), warnings, f"spot {asset} locked")
            if not asset or free is None or locked is None:
                invalid.append("spot")
                continue
            if free + locked > 0:
                out[asset] = out.get(asset, 0.0) + free + locked
        return out

    def _earn(self, path: str, amount_field: str, what: str, warnings: list[str], invalid: list[str],
              truncated: list[str], *, timeout_seconds: int) -> dict[str, float]:
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
                    invalid.append(what)
                    continue
                asset = str(row.get("asset") or "").strip()
                amount = _number(row.get(amount_field), warnings, f"{what} {asset} {amount_field}")
                if not asset or amount is None:
                    invalid.append(what)
                    continue
                if amount > 0:
                    out[asset] = out.get(asset, 0.0) + amount
            seen += len(rows)
            total = body.get("total")
            if not rows or len(rows) < EARN_PAGE_SIZE or (isinstance(total, int) and seen >= total):
                return out
        warnings.append(f"{what}: more than {EARN_MAX_PAGES} pages; the rest not read")
        truncated.append(what)
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

    def flow_history(self, source: str, *, start_ms: int, end_ms: int,
                     timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> list[dict[str, Any]]:
        """One cash-flow history read (H6a). The raw rows, in this process only: they carry amounts and
        assets. Refuses a source it does not know and a window longer than the venue documents."""
        if source not in FLOW_SOURCES:
            raise ToolBlocked("BINANCE_WALLET_PATH_REFUSED", f"{source}: not a history this feed reads")
        path, fixed, (start_name, end_name), days = FLOW_SOURCES[source]
        if end_ms <= start_ms or end_ms - start_ms > days * 86_400_000:
            raise ToolError("MALFORMED_REQUEST", f"{source}: window must be within {days} days")
        size_name, size, has_total, id_name = FLOW_PAGES[source]
        started, requests = time.monotonic(), 0
        found: dict[str, dict[str, Any]] = {}
        loose: list[dict[str, Any]] = []               # rows without their venue id: never folded by content
        # One instant wider on each side, so a row on the asked first or last instant arrives whichever way
        # the venue bounds a window (no page says) — unless that would pass the venue's longest window.
        lo, hi = start_ms - 1, end_ms + 1
        if hi - lo > days * 86_400_000:
            lo, hi = start_ms, end_ms
        pending = [(lo, hi)]
        while pending:
            start, end = pending.pop()
            if requests >= FLOW_MAX_REQUESTS or time.monotonic() - started > FLOW_READ_SECONDS:
                raise ToolError(FLOW_INCOMPLETE, f"{source} history: more than {requests} pieces or "
                                f"{FLOW_READ_SECONDS:.0f} s; the rest not read")
            requests += 1
            body = self._get(BINANCE_BASE_URL, path, {**fixed, start_name: start, end_name: end, size_name: size},
                             signed=True, what=f"{source} history", timeout_seconds=timeout_seconds)
            if source in FLOW_ENVELOPE_SOURCES:
                code = body.get("code") if isinstance(body, dict) else None
                success = body.get("success") if isinstance(body, dict) else None
                said_ok = code == FLOW_OK_CODE or success is True
                said_failed = (code is not None and code != FLOW_OK_CODE) or (success is not None and success is not True)
                if said_failed or not said_ok:
                    # The venue's code only, as for an HTTP refusal; its message never reaches a log.
                    shown = str(code) if isinstance(code, (str, int)) and str(code).isalnum() and len(str(code)) <= 12 else ""
                    raise _ApiRejected(urllib.parse.urlparse(BINANCE_BASE_URL).hostname or "?", 200, shown)
            rows = body if isinstance(body, list) else (
                (body.get("data") if isinstance(body.get("data"), list) else body.get("rows"))
                if isinstance(body, dict) else None)
            if rows is None and isinstance(body, dict) and body.get("total") == 0:
                rows = []
            if not isinstance(rows, list):
                raise ToolError("MALFORMED_RESULT", f"{source} history carried no rows")
            if not all(isinstance(row, dict) for row in rows):
                raise ToolError("MALFORMED_RESULT", f"{source} history carried a row that is not an object")
            # Where the venue documents ``total`` it is part of the answer: rows without a whole-number total
            # are not shown complete. Only a quiet window (no rows) may come without one.
            total = body.get("total") if isinstance(body, dict) else None
            if has_total and (rows or total is not None) and (
                    not isinstance(total, int) or isinstance(total, bool) or total < len(rows)
                    or (total > len(rows) and len(rows) < size)):
                raise ToolError(FLOW_INCOMPLETE, f"{source} history: total {total!r}, rows {len(rows)}")
            whole = has_total and total == len(rows)
            if len(rows) >= size and not whole:
                # A full page may be the first ``size`` of more. Read each side again, split where the page's
                # own rows sit (their median time, so a burst inside a long window is cut in few reads) or,
                # without readable times, at the middle. The sides overlap on two instants, ``middle`` and
                # ``middle + 1``, so every row is reached whichever way the venue bounds a window (no page
                # says); the copies are folded below.
                if end - start < 3:
                    raise ToolError(FLOW_INCOMPLETE, f"{source} history: a full page in a window too short to split")
                times = sorted(t for t in (_row_ms(row) for row in rows) if t is not None and start < t < end)
                middle = times[len(times) // 2] if times else (start + end) // 2
                middle = min(max(middle, start + 1), end - 2)
                pending.extend([(middle, end), (start, middle + 1)])
                continue
            for row in rows:
                if row.get(id_name) in (None, ""):
                    loose.append(row)                   # the normalizer's to judge (a malformed-row event)
                    continue
                key = f"id:{row[id_name]}"
                if key in found and found[key] != row:
                    raise ToolError(FLOW_INCOMPLETE, f"{source} history: one id with two contents")
                found[key] = row
        if loose and requests > 1:
            # The pieces overlap, so a row without its id may be one row read twice or two rows alike: unknown.
            raise ToolError(FLOW_INCOMPLETE, f"{source} history: a row without its id in a read of {requests} pieces")
        return [*found.values(), *loose]

    def wallet_snapshot(self, *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> WalletSnapshot:
        self._check_gate()
        started = time.monotonic()
        warnings: list[str] = []
        invalid: list[str] = []
        truncated: list[str] = []
        spot = self._spot(warnings, invalid, timeout_seconds=timeout_seconds)   # a failure here fails the read
        earn: dict[str, float] | None
        try:
            flexible = self._earn(FLEXIBLE_PATH, "totalAmount", "flexible earn", warnings, invalid, truncated,
                                  timeout_seconds=timeout_seconds)
            locked = self._earn(LOCKED_PATH, "amount", "locked earn", warnings, invalid, truncated,
                                timeout_seconds=timeout_seconds)
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
        members: dict[str, set[str]] = {name: set() for name in CLASSES}
        per_asset: dict[str, float] = {}

        def by_class(holdings: Mapping[str, float]) -> dict[str, float]:
            totals = {name: 0.0 for name in CLASSES}
            for asset, quantity in holdings.items():
                value = self._value(asset, quantity, prices)
                if value is None:
                    unpriced.add(asset)
                    continue
                totals[asset_class(asset)] += value
                if value:
                    members[asset_class(asset)].add(asset)
                    per_asset[asset] = per_asset.get(asset, 0.0) + value
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
            earn_truncated=bool(truncated),
            invalid_rows=len(invalid),
            class_assets={name: frozenset(assets) for name, assets in members.items() if assets},
            asset_usdt=dict(per_asset),
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
