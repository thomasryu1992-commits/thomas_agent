"""Toss Securities account holdings — read-only, behind its own env gate.

The holdings board's account since Thomas's 2026-10-07 decision ("토스만 쓰려고 해"), which replaced
the Korea Investment & Securities feed this package started with. The regulatory record is appendix B
of ``docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md``: "querying one's own account through the Toss
Securities Open API is something this project may operate", strength provisional.

**Why not ``crypto.account.select_account_feed``.** ``MVP_ACCOUNT_FEED`` is the live money path's
account (``crypto/live_route.py`` and ``crypto/account_store.py`` read it, and the daily-loss breaker
meters it). This capability has its own opt-in, its own provider id and its own package. Nothing in
``runtime/`` reaches it except the scheduler's ``holdings_refresh`` fire and the console verb (pinned in
the tests).

**Read-only by construction.** Toss's OAuth scopes are empty (``openapi.json``), so the same client
opens order APIs. Read-only is therefore enforced here: the feed reaches the token endpoint and five
reads (accounts, holdings, cash buying power, the exchange rate and, since H6a, the closed-order list),
by path constants, every one a GET, and has no method that places, modifies or cancels an order. Since
RB2' (2026-10-26) the candidate report adds four more GETs: listed ETFs, stock info, daily candles and the
account's commission rates. They serve Thomas's terminal only (raw quotes never reach a model prompt).

**What the official docs put on this module** (read 2026-10-07):

- data may serve only the investor's own trading purpose and may not be distributed to a third party
  (FAQ, data policy). The holdings rows carry ``lastPrice`` and per-symbol values, so they reach the
  terminal's full board only. Everything that can leave the process goes through
  ``board.aggregate_view``. The exchange rate this module reads never leaves it: only the KRW totals it
  produces do, and never beside the USD subtotal they came from, a pair that would give the rate back;
- the server's IP must be on the client's allowed list, or every call is 403 (``edge-blocked``);
- **one valid token per client.** Issuing one revokes the previous one at once (``401 token-revoked``).
  So scheduler-maint is the one issuer, and on ``token-revoked`` or ``expired-token`` the feed reissues
  **once** and retries the call **once**. A second 401 is a degraded read, never a loop. Anything else
  that issues a token for the same client — the terminal's live ``--full`` read, or the same client
  pasted into another tool — revokes the lane's token, and the lane's next fire pays one reissue.

**The token is a secret, held in memory only**, on the feed object. ``holdings.store`` keeps the feed
between fires, so one token serves the lane until it nears expiry.

**Money math.** Toss's overview amounts are per-currency subtotals (the ``Price`` schema: no
conversion between currencies). The domestic side is Toss's ``krw`` subtotal. The overseas side is its
``usd`` subtotal converted **here** with Toss's display mid-rate (``midRate``, refreshed each minute).
If the rate cannot be read, the overseas KRW figures are absent, not guessed. Cash is
``cashBuyingPower`` (cash-based buying power, no margin), which is not a deposit balance, and every
render says so.

**Unverified until the first live read.** There is no demo server. The shapes come from Toss's own
``openapi.json`` examples. Parsing degrades: a missing or non-numeric field is ``None`` plus a warning,
never zero.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .. import safety_gate, timeutil
from ..errors import SafetyGateBlocked, ToolBlocked, ToolError
from ..safety_gate import NETWORK_ACCESS, Authorization
from .model import (
    MARKET_DOMESTIC,
    MARKET_OVERSEAS,
    Holding,
    HoldingsFeed,
    HoldingsSnapshot,
    MarketTotals,
    NoHoldingsFeed,
    mask_account,
)

TOOL_VERSION = "0.2.0"
BROKER = "toss"

# The gate. Its own variable, not MVP_ACCOUNT_FEED — see the module docstring.
TOSS_ACCOUNT_ENV = "MVP_TOSS_ACCOUNT"
TOSS_ACCOUNT_ON = "toss"
TOSS_PROVIDER = "toss_account"
_NETWORK_FLAGS = (NETWORK_ACCESS,)

# Credential and account variable NAMES, as module constants so the deployment test names them from
# here. TOSS_ACCOUNT_SEQ is optional: needed only when the client sees more than one brokerage account.
TOSS_CLIENT_ID_ENV = "TOSS_CLIENT_ID"
TOSS_CLIENT_SECRET_ENV = "TOSS_CLIENT_SECRET"
TOSS_ACCOUNT_SEQ_ENV = "TOSS_ACCOUNT_SEQ"

BASE_URL = "https://openapi.tossinvest.com"
TOKEN_PATH = "/oauth2/token"
ACCOUNTS_PATH = "/api/v1/accounts"
HOLDINGS_PATH = "/api/v1/holdings"
BUYING_POWER_PATH = "/api/v1/buying-power"
EXCHANGE_RATE_PATH = "/api/v1/exchange-rate"
# H6a (Thomas 2026-10-08, D-H6-3): the closed-order list, for matching cash changes to fills. A GET of
# orders already placed; the feed has no method that places, modifies or cancels one.
ORDERS_PATH = "/api/v1/orders"
ORDERS_PAGE_LIMIT = 100
# RB2' (Thomas 2026-10-26): the candidate screening report's reads — market data and listings, no account
# data except the account's commission rates. Every one a GET (`scripts/rebalance_candidates.py`).
STOCKS_ALL_PATH = "/api/v1/stocks/all"
STOCKS_PATH = "/api/v1/stocks"
CANDLES_PATH = "/api/v1/candles"
COMMISSIONS_PATH = "/api/v1/commissions"
STOCKS_PAGE_LIMIT = 200
# G6-buy (Thomas 2026-10-26): the executor's pre-order reads. The order itself is not here: it lives in
# `toss_orders.py`, behind its own gate, so this feed stays a read with no order method.
PRICES_PATH = "/api/v1/prices"
KR_CALENDAR_PATH = "/api/v1/market-calendar/KR"
US_CALENDAR_PATH = "/api/v1/market-calendar/US"
# G6-buy limit orders (Thomas 2026-10-27): the price comes from the book; a placed order's fill is read back.
ORDERBOOK_PATH = "/api/v1/orderbook"
ACCOUNT_HEADER = "X-Tossinvest-Account"
BROKERAGE = "BROKERAGE"

# The 401 codes that mean "your token, not your request": reissue once, retry once.
_TOKEN_CODES = frozenset({"token-revoked", "expired-token"})
# Re-issue a token this long before Toss says it expires, so a read never starts on a dying token.
TOKEN_REFRESH_MARGIN_SECONDS = 600
DEFAULT_TOKEN_LIFETIME_SECONDS = 23 * 3600


def _number(value: Any, warnings: list[str], where: str) -> float | None:
    """One decimal string, or ``None`` with a warning. Never a silent zero."""
    if value is None or (isinstance(value, str) and not value.strip()):
        warnings.append(f"{where}: missing")
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        warnings.append(f"{where}: not numeric")
        return None


def _times(value: float | None, rate: float | None) -> float | None:
    return None if value is None or rate is None else value * rate


class _ApiRejected(ToolError):
    """A 4xx/5xx from an API call, carrying Toss's own error code (never its message)."""

    def __init__(self, status: int, code: str):
        reason = {403: "TOSS_FORBIDDEN", 429: "TOSS_RATE_LIMITED"}.get(status, "TOSS_REJECTED")
        super().__init__(reason, f"Toss refused the read (HTTP {status}, code {code or '?'})")
        self.status = status
        self.code = code


class TossHoldingsFeed:
    """Read of one Toss Securities account. Built only by :func:`select_holdings_feed` behind the
    gate; every egress re-checks that authorization. Has no order method."""

    feed_id = TOSS_PROVIDER
    feed_version = f"{TOOL_VERSION}-toss"
    provider_id = TOSS_PROVIDER
    network_egress = True

    def __init__(self, *, authorization: Authorization | None = None) -> None:
        self._authorization = authorization
        # (token, monotonic deadline). In memory only — the token is a bearer secret.
        self._token: tuple[str, float] | None = None
        # (accountSeq, accountNo), resolved once: an account does not change under a process.
        self._account: tuple[int, str] | None = None

    # -- credentials and gate --------------------------------------------------------------

    @staticmethod
    def _credentials() -> tuple[str, str]:
        client_id = os.environ.get(TOSS_CLIENT_ID_ENV, "").strip()
        client_secret = os.environ.get(TOSS_CLIENT_SECRET_ENV, "").strip()
        missing = [name for name, value in ((TOSS_CLIENT_ID_ENV, client_id),
                                            (TOSS_CLIENT_SECRET_ENV, client_secret)) if not value]
        if missing:
            # Names only — a message that echoed a value would put a secret in a log.
            raise ToolError("NO_API_KEY", f"environment variables not set: {', '.join(missing)}")
        return client_id, client_secret

    def _check_gate(self) -> None:
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )

    # -- transport -------------------------------------------------------------------------

    @staticmethod
    def _parse(raw: str, what: str) -> dict[str, Any]:
        try:
            body = json.loads(raw)
        except ValueError:
            raise ToolError("MALFORMED_RESULT", f"{what} returned an unparseable response") from None
        if not isinstance(body, dict):
            raise ToolError("MALFORMED_RESULT", f"{what} returned an unparseable response")
        return body

    def _issue_token(self, *, timeout_seconds: int) -> str:
        client_id, client_secret = self._credentials()
        request = urllib.request.Request(
            f"{BASE_URL}{TOKEN_PATH}",
            method="POST",
            data=urllib.parse.urlencode({
                "grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret,
            }).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            # The status only. The body is never read: a credential failure is never retried, and
            # nothing this module sent should come back into a log.
            raise ToolError("TOSS_TOKEN_REJECTED", f"token request rejected (HTTP {exc.code})") from None
        except (TimeoutError, urllib.error.URLError):
            raise ToolError("TOOL_TRANSPORT", "token request failed or timed out") from None
        body = self._parse(raw, "token request")
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise ToolError("MALFORMED_RESULT", "token response carried no access token")
        try:
            lifetime = float(body.get("expires_in"))
        except (TypeError, ValueError):
            lifetime = DEFAULT_TOKEN_LIFETIME_SECONDS
        self._token = (token, time.monotonic() + max(0.0, lifetime - TOKEN_REFRESH_MARGIN_SECONDS))
        return token

    def _access_token(self, *, timeout_seconds: int) -> str:
        if self._token is not None and time.monotonic() < self._token[1]:
            return self._token[0]
        return self._issue_token(timeout_seconds=timeout_seconds)

    def _get_once(self, path: str, params: dict[str, str], *, token: str, account: bool,
                  what: str, timeout_seconds: int) -> Any:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if account:
            headers[ACCOUNT_HEADER] = str(self._account[0]) if self._account else ""
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        request = urllib.request.Request(f"{BASE_URL}{path}{query}", method="GET", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            code = ""
            try:
                error = json.loads(exc.read().decode("utf-8")).get("error") or {}
                code = str(error.get("code") or "")
            except (ValueError, AttributeError, OSError):
                pass
            # Toss's own code only, never its message: the code is enough to look up.
            raise _ApiRejected(exc.code, code) from None
        except (TimeoutError, urllib.error.URLError):
            raise ToolError("TOOL_TRANSPORT", f"{what} failed or timed out") from None
        body = self._parse(raw, what)
        if "result" not in body:
            raise ToolError("MALFORMED_RESULT", f"{what} carried no result")
        return body["result"]

    def _get(self, path: str, params: dict[str, str] | None = None, *, account: bool, what: str,
             timeout_seconds: int) -> Any:
        self._check_gate()
        token = self._access_token(timeout_seconds=timeout_seconds)
        try:
            return self._get_once(path, params or {}, token=token, account=account, what=what,
                                  timeout_seconds=timeout_seconds)
        except _ApiRejected as exc:
            if exc.status != 401 or exc.code not in _TOKEN_CODES:
                raise
        # The token was revoked (another issuer for this client) or expired: reissue once, retry
        # once. A second failure propagates — never a loop.
        self._token = None
        self._check_gate()
        token = self._issue_token(timeout_seconds=timeout_seconds)
        return self._get_once(path, params or {}, token=token, account=account, what=what,
                              timeout_seconds=timeout_seconds)

    # -- the reads -------------------------------------------------------------------------

    def open_orders(self, *, timeout_seconds: int = 10) -> list[dict[str, Any]]:
        """Every order still open (``status=OPEN``: pending, partly filled, pending cancel or replace). Toss
        returns them all at once, without paging. quiet_window.v2 counts them: a pending buy holds buying power.
        The rows carry symbols and amounts: in this process only."""
        self._resolve_account(timeout_seconds=timeout_seconds)
        result = self._get(ORDERS_PATH, {"status": "OPEN"}, account=True, what="open orders",
                           timeout_seconds=timeout_seconds)
        orders = result.get("orders") if isinstance(result, dict) else None
        if not isinstance(orders, list):
            raise ToolError("MALFORMED_RESULT", "open orders carried no list")
        return [order for order in orders if isinstance(order, dict)]

    def closed_orders(self, *, date_from: str | None, date_to: str | None, max_pages: int = 10,
                      timeout_seconds: int = 10) -> tuple[list[dict[str, Any]], bool]:
        """Closed orders whose ``orderedAt`` falls in ``date_from``..``date_to`` (KST dates, inclusive; both
        ``None`` asks for the whole history, which Toss gives when neither is sent), and whether the pages ran
        out before the list did. The rows carry symbols and amounts: in this process only (H6a; the ledger that
        uses them is H6b; the overseas gain, RB3')."""
        self._resolve_account(timeout_seconds=timeout_seconds)
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(max_pages):
            params = {"status": "CLOSED", "limit": str(ORDERS_PAGE_LIMIT)}
            params.update({k: v for k, v in (("from", date_from), ("to", date_to)) if v is not None})
            if cursor:
                params["cursor"] = cursor
            result = self._get(ORDERS_PATH, params, account=True, what="closed orders",
                               timeout_seconds=timeout_seconds)
            orders = result.get("orders") if isinstance(result, dict) else None
            if not isinstance(orders, list):
                raise ToolError("MALFORMED_RESULT", "closed orders carried no list")
            rows.extend(order for order in orders if isinstance(order, dict))
            cursor = result.get("nextCursor") if result.get("hasNext") else None
            if not cursor:
                return rows, False
        return rows, True

    # -- the candidate report's reads (RB2'): listings and market data, for Thomas's terminal only ----------------

    def listed_etfs(self, market: str, *, timeout_seconds: int = 10) -> list[dict[str, Any]]:
        """Every ACTIVE ETF Toss trades on ``market`` (KOSPI, KOSDAQ, KR_ETC, NYSE, NASDAQ, AMEX)."""
        rows = self._get(STOCKS_ALL_PATH, {"market": market, "status": "ACTIVE", "securityType": "ETF"},
                         account=False, what="listed ETFs", timeout_seconds=timeout_seconds)
        if not isinstance(rows, list):
            raise ToolError("MALFORMED_RESULT", "listed ETFs carried no list")
        return [row for row in rows if isinstance(row, dict)]

    def stock_info(self, symbols: list[str], *, timeout_seconds: int = 10) -> list[dict[str, Any]]:
        """Basic info (market, currency, status, leverage factor) for up to 200 symbols."""
        if not 0 < len(symbols) <= STOCKS_PAGE_LIMIT:
            raise ToolError("TOOL_USAGE", f"stock info takes 1..{STOCKS_PAGE_LIMIT} symbols")
        rows = self._get(STOCKS_PATH, {"symbols": ",".join(symbols)}, account=False, what="stock info",
                         timeout_seconds=timeout_seconds)
        if not isinstance(rows, list):
            raise ToolError("MALFORMED_RESULT", "stock info carried no list")
        return [row for row in rows if isinstance(row, dict)]

    def daily_candles(self, symbol: str, *, count: int = 20, timeout_seconds: int = 10) -> list[dict[str, Any]]:
        """The latest ``count`` daily candles (newest first), unadjusted."""
        page = self._get(CANDLES_PATH, {"symbol": symbol, "interval": "1d", "count": str(count)}, account=False,
                         what="daily candles", timeout_seconds=timeout_seconds)
        candles = page.get("candles") if isinstance(page, dict) else None
        if not isinstance(candles, list):
            raise ToolError("MALFORMED_RESULT", "daily candles carried no list")
        return [c for c in candles if isinstance(c, dict)]

    def commission_rates(self, *, timeout_seconds: int = 10) -> list[dict[str, Any]]:
        """This account's commission rate per market country (KR, US)."""
        self._resolve_account(timeout_seconds=timeout_seconds)
        rows = self._get(COMMISSIONS_PATH, account=True, what="commission rates", timeout_seconds=timeout_seconds)
        if not isinstance(rows, list):
            raise ToolError("MALFORMED_RESULT", "commission rates carried no list")
        return [row for row in rows if isinstance(row, dict)]

    def usd_krw_rate(self, *, timeout_seconds: int = 10) -> float | None:
        """Toss's USD/KRW mid-rate now, or None when it cannot be read."""
        return self._usd_krw_rate([], timeout_seconds=timeout_seconds)

    # -- the G6-buy executor's reads (G6_BUY_CASH_AUTOINVEST_V0.1): still reads, all GETs --------------------------

    def cash_krw(self, *, timeout_seconds: int = 10) -> float | None:
        """The account's KRW cash buying power, or None when it cannot be read."""
        self._resolve_account(timeout_seconds=timeout_seconds)
        return self._cash("KRW", [], timeout_seconds=timeout_seconds)

    def cash_usd(self, *, timeout_seconds: int = 10) -> float | None:
        """The account's USD cash buying power (G6 §2g), or None when it cannot be read. Toss's API never
        converts KRW (``faq.md``), so this is what Thomas exchanged in the app."""
        self._resolve_account(timeout_seconds=timeout_seconds)
        return self._cash("USD", [], timeout_seconds=timeout_seconds)

    def last_prices(self, symbols: list[str], *, timeout_seconds: int = 10) -> dict[str, float]:
        """The last price per symbol (up to 200); a symbol without a readable price is left out."""
        if not 0 < len(symbols) <= STOCKS_PAGE_LIMIT:
            raise ToolError("TOOL_USAGE", f"prices take 1..{STOCKS_PAGE_LIMIT} symbols")
        rows = self._get(PRICES_PATH, {"symbols": ",".join(symbols)}, account=False, what="prices",
                         timeout_seconds=timeout_seconds)
        out: dict[str, float] = {}
        for row in rows if isinstance(rows, list) else []:
            try:
                out[str(row["symbol"])] = float(row["lastPrice"])
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def asks(self, symbol: str, *, timeout_seconds: int = 10) -> list[tuple[float, float]]:
        """The ask side of ``symbol``'s book, lowest price first, as (price, volume); unreadable levels left out."""
        book = self._get(ORDERBOOK_PATH, {"symbol": symbol}, account=False, what="orderbook",
                         timeout_seconds=timeout_seconds)
        levels = []
        for row in (book.get("asks") if isinstance(book, dict) else None) or []:
            try:
                levels.append((float(row["price"]), float(row["volume"])))
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(levels)

    def fill_state(self, order_id: str, *, timeout_seconds: int = 10) -> dict[str, Any]:
        """One placed order's status and filled quantity (``GET /api/v1/orders/{orderId}``), still a read."""
        self._resolve_account(timeout_seconds=timeout_seconds)
        row = self._get(f"{ORDERS_PATH}/{urllib.parse.quote(order_id, safe='')}", account=True,
                        what="order detail", timeout_seconds=timeout_seconds)
        if not isinstance(row, dict):
            raise ToolError("MALFORMED_RESULT", "order detail carried no object")
        execution = row.get("execution") or {}
        try:
            filled = float(execution.get("filledQuantity") or 0)
            amount = float(execution.get("filledAmount") or 0)
        except (TypeError, ValueError):
            raise ToolError("MALFORMED_RESULT", "order detail carried no readable filled quantity") from None
        return {"status": str(row.get("status") or ""), "filled": filled, "filled_amount": amount}

    def kr_market_day(self, date: str, *, timeout_seconds: int = 10) -> dict[str, Any] | None:
        """``today`` of Toss's domestic market calendar for ``date`` (KST), or None when it carries none."""
        result = self._get(KR_CALENDAR_PATH, {"date": date}, account=False, what="KR market calendar",
                           timeout_seconds=timeout_seconds)
        today = result.get("today") if isinstance(result, dict) else None
        return today if isinstance(today, dict) else None

    def us_regular_sessions(self, date: str, *, timeout_seconds: int = 10) -> list[tuple[str, str, str]]:
        """The US regular sessions around ``date`` (US local): (US date, start, end), the times ISO with the KST
        offset, as Toss's calendar gives them for the previous, this and the next business day. A closed day has
        no regular session and is left out."""
        result = self._get(US_CALENDAR_PATH, {"date": date}, account=False, what="US market calendar",
                           timeout_seconds=timeout_seconds)
        out = []
        for key in ("previousBusinessDay", "today", "nextBusinessDay"):
            day = result.get(key) if isinstance(result, dict) else None
            regular = day.get("regularMarket") if isinstance(day, dict) else None
            if isinstance(regular, dict) and regular.get("startTime") and regular.get("endTime"):
                out.append((str(day.get("date")), str(regular["startTime"]), str(regular["endTime"])))
        return out

    def _resolve_account(self, *, timeout_seconds: int) -> tuple[int, str]:
        if self._account is not None:
            return self._account
        rows = self._get(ACCOUNTS_PATH, account=False, what="account list", timeout_seconds=timeout_seconds)
        brokerage = [
            row for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict) and row.get("accountType") == BROKERAGE
            and isinstance(row.get("accountSeq"), int)
        ]
        wanted = os.environ.get(TOSS_ACCOUNT_SEQ_ENV, "").strip()
        if wanted:
            chosen = [row for row in brokerage if str(row["accountSeq"]) == wanted]
            if not chosen:
                raise ToolBlocked("TOSS_ACCOUNT_NOT_FOUND",
                                  f"{TOSS_ACCOUNT_SEQ_ENV} names no brokerage account this client sees")
        elif len(brokerage) == 1:
            chosen = brokerage
        elif not brokerage:
            raise ToolError("TOSS_NO_ACCOUNT", "this client sees no brokerage account")
        else:
            # Never a guess between accounts: the board would be a confident answer about the wrong one.
            raise ToolBlocked("TOSS_ACCOUNT_AMBIGUOUS",
                              f"{len(brokerage)} brokerage accounts; set {TOSS_ACCOUNT_SEQ_ENV}")
        self._account = (chosen[0]["accountSeq"], str(chosen[0].get("accountNo") or ""))
        return self._account

    def _usd_krw_rate(self, warnings: list[str], *, timeout_seconds: int) -> float | None:
        try:
            result = self._get(EXCHANGE_RATE_PATH, {"baseCurrency": "USD", "quoteCurrency": "KRW"},
                               account=False, what="exchange rate", timeout_seconds=timeout_seconds)
        except ToolError as exc:
            warnings.append(f"exchange rate unavailable ({exc.reason_code}); overseas KRW not computed")
            return None
        return _number((result or {}).get("midRate") if isinstance(result, dict) else None,
                       warnings, "exchange rate midRate")

    def _cash(self, currency: str, warnings: list[str], *, timeout_seconds: int) -> float | None:
        try:
            result = self._get(BUYING_POWER_PATH, {"currency": currency}, account=True,
                               what=f"{currency} buying power", timeout_seconds=timeout_seconds)
        except ToolError as exc:
            warnings.append(f"{currency} cash buying power unavailable ({exc.reason_code})")
            return None
        return _number((result or {}).get("cashBuyingPower") if isinstance(result, dict) else None,
                       warnings, f"{currency} cashBuyingPower")

    def holdings_snapshot(self, *, timeout_seconds: int = 10) -> HoldingsSnapshot:
        self._check_gate()
        started = time.monotonic()
        warnings: list[str] = []
        _, account_no = self._resolve_account(timeout_seconds=timeout_seconds)
        overview = self._get(HOLDINGS_PATH, account=True, what="holdings", timeout_seconds=timeout_seconds)
        if not isinstance(overview, dict):
            raise ToolError("MALFORMED_RESULT", "holdings returned no overview")

        def subtotal(block: str, sub: str, currency: str) -> float | None:
            node = overview.get(block) or {}
            node = (node.get(sub) or {}) if sub else node
            raw = node.get(currency) if isinstance(node, dict) else None
            if currency == "usd" and raw is None:
                return 0.0  # documented: usd is null when no overseas stock is held
            return _number(raw, warnings, f"holdings {block}.{sub}.{currency}")

        holdings: list[Holding] = []
        for row in overview.get("items") or []:
            if not isinstance(row, dict):
                continue
            market = MARKET_DOMESTIC if row.get("marketCountry") == "KR" else MARKET_OVERSEAS
            holdings.append(Holding(
                market=market,
                symbol=str(row.get("symbol") or "").strip(),
                name=str(row.get("name") or "").strip(),
                quantity=_number(row.get("quantity"), warnings, "holding quantity"),
                value=_number((row.get("marketValue") or {}).get("amount"), warnings, "holding value"),
                unrealized_pnl=_number((row.get("profitLoss") or {}).get("amount"), warnings, "holding pnl"),
                currency=str(row.get("currency") or "?"),
                # Not the board's figure: a missing one costs only the overseas-gain reconciliation, never a warning.
                average_price=_number(row.get("averagePurchasePrice"), [], "holding average price"),
            ))

        usd_value = subtotal("marketValue", "amount", "usd")
        usd_pnl = subtotal("profitLoss", "amount", "usd")
        usd_cash = self._cash("USD", warnings, timeout_seconds=timeout_seconds)
        # One rate per read, asked for every read: P2 converts the Binance USDT balance with the same
        # rate (holdings.combined), so the two KRW figures on one board rest on one rate. Until P2 it
        # was skipped when nothing was in USD; one call an hour is the cost of the shared rate.
        rate = self._usd_krw_rate(warnings, timeout_seconds=timeout_seconds)

        domestic = MarketTotals(
            market=MARKET_DOMESTIC,
            holdings_value_krw=subtotal("marketValue", "amount", "krw"),
            unrealized_pnl_krw=subtotal("profitLoss", "amount", "krw"),
            cash_krw=self._cash("KRW", warnings, timeout_seconds=timeout_seconds),
        )
        overseas = MarketTotals(
            market=MARKET_OVERSEAS,
            holdings_value_krw=_times(usd_value, rate),
            unrealized_pnl_krw=_times(usd_pnl, rate),
            cash_krw=_times(usd_cash, rate),
        )
        return HoldingsSnapshot(
            account=mask_account(account_no),
            broker=BROKER,
            domestic=domestic,
            overseas=overseas,
            holdings=tuple(holdings),
            collected_at=timeutil.utc_now_iso(),
            latency_ms=int((time.monotonic() - started) * 1000),
            warnings=tuple(warnings),
            usd_krw_rate=rate,
        )


def select_holdings_feed() -> HoldingsFeed:
    """The Toss feed if ``MVP_TOSS_ACCOUNT=toss``, else the inert one. The capable feed is built by
    the gate, so it cannot exist before its authorization does."""
    return safety_gate.select_env_gated(
        env_var=TOSS_ACCOUNT_ENV,
        opt_in_value=TOSS_ACCOUNT_ON,
        flags=_NETWORK_FLAGS,
        provider_id=TOSS_PROVIDER,
        default_factory=NoHoldingsFeed,
        gated_factory=lambda authorization: TossHoldingsFeed(authorization=authorization),
    )


def read_holdings(
    *, timeout_seconds: int = 10, feed: HoldingsFeed | None = None,
) -> tuple[HoldingsSnapshot | None, str | None]:
    """Read once. Degrades rather than raising: ``(snapshot, None)``, or ``(None, reason_code)``
    when the gate is closed (``NOT_CONFIGURED``) or the read failed (the error's own code).

    ``feed`` lets a long-lived caller reuse a feed it already selected, and with it the feed's
    in-memory token (``holdings.store``). Absent, the gate selects one for this read."""
    try:
        if feed is None:
            feed = select_holdings_feed()
        snapshot = feed.holdings_snapshot(timeout_seconds=timeout_seconds)
    except (ToolError, ToolBlocked, SafetyGateBlocked) as exc:
        return None, exc.reason_code
    if snapshot is None:
        return None, "NOT_CONFIGURED"
    return snapshot, None
