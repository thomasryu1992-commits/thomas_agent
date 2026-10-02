"""Korea Investment & Securities (KIS) account holdings — read-only, behind its own env gate.

P1 of ``docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md``: the read-only multi-account board's first
account. Thomas decided on 2026-10-02 that the board comes first (D1), that it may be built during the
research pause while it has no order path, changes no judgement and is read by no door (D4), and that
the first regulatory record is this broker's (D3, appendix A: "querying one's own account through the
KIS Open API is something this project may operate", strength provisional).

**Why this is not behind ``crypto.account.select_account_feed``.** The proposal's §2 named that seam
as the default home. It cannot carry this account: ``MVP_ACCOUNT_FEED`` selects the feed the live
money path reads (``crypto/live_route.py`` and ``crypto/account_store.py`` call ``read_account``,
and the daily-loss breaker meters its realized windows). A KRW cash account on that seam would put
a second venue into the live risk input. So this capability has its own opt-in, its own provider id
and its own package, and nothing under ``crypto/`` imports it — which is how D4's "no door reads it"
stays a property of the import graph rather than a promise (pinned in the tests).

**Read-only by construction.** The feed has no order method. KIS issues one app key that can both
read and order (the customer terms define no read-only scope, arts. 3 and 7), so read-only is
enforced here rather than by the key: the only endpoints this module can reach are the token
endpoint and two balance inquiries, by path constants.

**What the terms put on this module** (appendix A, read from the source 2026-10-02):

- quotes may not be given to a third party (art. 5(3)). The holdings rows carry prices (``prpr``,
  ``ovrs_now_pric1``) and per-symbol values from which a price follows. They reach the full board
  only; anything that can leave the process — a console verb, a prompt — gets
  :func:`holdings.board.aggregate_view`, which carries no symbol and no per-symbol number;
- load "above a certain level" can suspend or terminate access (arts. 9, 10, 12): a read is a
  handful of calls, pagination is capped, and a token rejection is never retried;
- the key may not be lent or delegated (art. 5(2)): it is read by name from the environment at call
  time and never stored, logged, or echoed. Claude does not handle it.

**The token is a secret, held in memory only.** ``/oauth2/tokenP`` returns a bearer token valid for
24 hours. KIS sends the account holder a KakaoTalk notice on every issuance and returns the same
token when asked again within six hours (the comment above ``auth`` in KIS's own
``examples_llm/kis_auth.py``), and issuance is limited to about once a minute. So the feed issues at
most one token per process and keeps it on the object; it never touches disk. A one-shot run of
``scripts/holdings_board.py`` therefore costs one issuance — and one notice — per run.

**Field semantics are unverified.** The response fields below are taken from KIS's own sample column
maps (``chk_inquire_balance.py``, ``chk_inquire_present_balance.py``), not from a response this code
has seen. Parsing is defensive: a missing or non-numeric field becomes ``None`` plus a warning, never
a crash and never a zero. The first live run, by Thomas with the keys only Thomas holds, is the
verification step.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from .. import safety_gate, timeutil
from ..errors import SafetyGateBlocked, ToolBlocked, ToolError
from ..safety_gate import NETWORK_ACCESS, Authorization

TOOL_ID = "holdings.kis_account.readonly"
TOOL_VERSION = "0.1.0"

# The gate. Its own variable, not MVP_ACCOUNT_FEED — see the module docstring.
KIS_ACCOUNT_ENV = "MVP_KIS_ACCOUNT"
KIS_ACCOUNT_ON = "kis"
KIS_PROVIDER = "kis_account"
_NETWORK_FLAGS = (NETWORK_ACCESS,)

# Which KIS server to read. A plain setting, not part of the gate: `select_env_gated` matches one
# opt-in value, and "real" vs "demo" is a choice inside an opened gate. Unset means real, because
# reading the real account is the point of P1; anything else that is not a known name refuses.
KIS_SERVER_ENV = "KIS_SERVER"
SERVER_REAL = "real"
SERVER_DEMO = "demo"
DEFAULT_SERVER = SERVER_REAL
BASE_URLS = {
    SERVER_REAL: "https://openapi.koreainvestment.com:9443",
    SERVER_DEMO: "https://openapivts.koreainvestment.com:29443",
}

# Credential and account variable NAMES, as module constants so a deployment test can name them
# from here (the naver_research reasoning). The account number is not a secret but is personal
# data: it is masked in everything this package renders.
KIS_APP_KEY_ENV = "KIS_APP_KEY"
KIS_APP_SECRET_ENV = "KIS_APP_SECRET"
KIS_ACCOUNT_NO_ENV = "KIS_ACCOUNT_NO"
KIS_ACCOUNT_PRODUCT_CODE_ENV = "KIS_ACCOUNT_PRODUCT_CODE"

TOKEN_PATH = "/oauth2/tokenP"
DOMESTIC_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
OVERSEAS_BALANCE_PATH = "/uapi/overseas-stock/v1/trading/inquire-present-balance"
# Transaction ids per server, from KIS's samples: domestic 주식잔고조회, overseas 해외주식 체결기준현재잔고.
DOMESTIC_TR_ID = {SERVER_REAL: "TTTC8434R", SERVER_DEMO: "VTTC8434R"}
OVERSEAS_TR_ID = {SERVER_REAL: "CTRP6504R", SERVER_DEMO: "VTRP6504R"}

# `tr_cont` of M or F on a response means another page follows. A personal account fits in one or
# two; the cap bounds what a misbehaving continuation can cost against the terms' load clause.
MAX_PAGES = 5
# Re-issue a token this long before KIS says it expires, so a read never starts on a dying token.
TOKEN_REFRESH_MARGIN_SECONDS = 600
# Used only when the token response carries no usable `expires_in`.
DEFAULT_TOKEN_LIFETIME_SECONDS = 23 * 3600

MARKET_DOMESTIC = "domestic"
MARKET_OVERSEAS = "overseas"


@dataclass(frozen=True)
class Holding:
    """One position as KIS reports it. Carries price-revealing numbers: full board only."""

    market: str
    symbol: str
    name: str
    quantity: float | None
    value: float | None
    unrealized_pnl: float | None
    currency: str


@dataclass(frozen=True)
class MarketTotals:
    """KIS's own totals for one side of the account, in KRW. ``None`` means not read, never zero."""

    market: str
    holdings_value_krw: float | None
    unrealized_pnl_krw: float | None
    # Domestic only. The overseas side's cash is not read in P1: which output field holds it in
    # KRW, and whether it overlaps the domestic deposit, is unverified — guessing would risk
    # counting the same won twice.
    cash_krw: float | None = None


@dataclass(frozen=True)
class HoldingsSnapshot:
    account: str  # masked
    server: str
    domestic: MarketTotals | None
    overseas: MarketTotals | None
    holdings: tuple[Holding, ...]
    collected_at: str
    latency_ms: int
    warnings: tuple[str, ...] = field(default_factory=tuple)


class HoldingsFeed(Protocol):
    """Read-only holdings access. Every method is a read; there is no order sibling to gate."""

    feed_id: str
    feed_version: str
    network_egress: bool

    def holdings_snapshot(self, *, timeout_seconds: int) -> HoldingsSnapshot | None: ...


class NoHoldingsFeed:
    """The inert default: no key, no socket. ``None`` is a normal state, not an error."""

    feed_id = "none"
    feed_version = f"{TOOL_VERSION}-none"
    network_egress = False

    def holdings_snapshot(self, *, timeout_seconds: int) -> HoldingsSnapshot | None:
        return None


def mask_account(account_no: str, product_code: str) -> str:
    """``****1234-01``. The last four digits identify which account to its owner; nothing more."""
    digits = "".join(ch for ch in str(account_no) if ch.isdigit())
    tail = digits[-4:] if len(digits) >= 4 else ""
    return f"****{tail}-{str(product_code).strip()}" if tail else "****"


def _number(row: dict[str, Any], key: str, warnings: list[str], where: str) -> float | None:
    """One numeric field, or ``None`` with a warning. Never a silent zero."""
    raw = row.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        warnings.append(f"{where}: field {key} missing")
        return None
    try:
        return float(str(raw).replace(",", ""))
    except ValueError:
        warnings.append(f"{where}: field {key} not numeric")
        return None


def _first_row(value: Any) -> dict[str, Any] | None:
    """KIS returns a summary block as a dict or as a one-element list, depending on the call."""
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def parse_domestic(pages: list[dict[str, Any]], warnings: list[str]) -> tuple[MarketTotals | None, list[Holding]]:
    """Domestic 주식잔고조회: ``output1`` rows are holdings, ``output2`` is the account summary.

    Totals come from KIS's own summary (``scts_evlu_amt`` 유가평가금액, ``dnca_tot_amt``
    예수금총금액, ``evlu_pfls_smtl_amt`` 평가손익합계금액) rather than a sum of rows, so a page
    this code did not fetch cannot shrink them. The summary is read from the LAST page."""
    holdings: list[Holding] = []
    for page in pages:
        for row in page.get("output1") or []:
            if not isinstance(row, dict):
                continue
            quantity = _number(row, "hldg_qty", warnings, "domestic holding")
            if quantity == 0:
                continue  # sold today; KIS keeps the row until settlement
            holdings.append(Holding(
                market=MARKET_DOMESTIC,
                symbol=str(row.get("pdno") or "").strip(),
                name=str(row.get("prdt_name") or "").strip(),
                quantity=quantity,
                value=_number(row, "evlu_amt", warnings, "domestic holding"),
                unrealized_pnl=_number(row, "evlu_pfls_amt", warnings, "domestic holding"),
                currency="KRW",
            ))
    summary = _first_row(pages[-1].get("output2")) if pages else None
    if summary is None:
        warnings.append("domestic: account summary missing")
        return None, holdings
    totals = MarketTotals(
        market=MARKET_DOMESTIC,
        holdings_value_krw=_number(summary, "scts_evlu_amt", warnings, "domestic summary"),
        unrealized_pnl_krw=_number(summary, "evlu_pfls_smtl_amt", warnings, "domestic summary"),
        cash_krw=_number(summary, "dnca_tot_amt", warnings, "domestic summary"),
    )
    return totals, holdings


def parse_overseas(pages: list[dict[str, Any]], warnings: list[str]) -> tuple[MarketTotals | None, list[Holding]]:
    """Overseas 체결기준현재잔고, requested in KRW (``WCRC_FRCR_DVSN_CD=01``): ``output1`` rows are
    holdings, ``output3`` the account totals.

    Unverified against a live response: that ``evlu_amt_smtl_amt`` (평가금액합계금액) and
    ``tot_evlu_pfls_amt`` (총평가손익금액) are KRW in this mode. The per-row value is reported
    as KIS gives it, with the row's own currency code beside it."""
    holdings: list[Holding] = []
    for page in pages:
        for row in page.get("output1") or []:
            if not isinstance(row, dict):
                continue
            quantity = _number(row, "cblc_qty13", warnings, "overseas holding")
            if quantity == 0:
                continue
            holdings.append(Holding(
                market=MARKET_OVERSEAS,
                symbol=str(row.get("pdno") or "").strip(),
                name=str(row.get("prdt_name") or "").strip(),
                quantity=quantity,
                value=_number(row, "frcr_evlu_amt2", warnings, "overseas holding"),
                unrealized_pnl=_number(row, "evlu_pfls_amt2", warnings, "overseas holding"),
                currency=str(row.get("buy_crcy_cd") or "").strip() or "?",
            ))
    summary = _first_row(pages[-1].get("output3")) if pages else None
    if summary is None:
        warnings.append("overseas: account totals missing")
        return None, holdings
    totals = MarketTotals(
        market=MARKET_OVERSEAS,
        holdings_value_krw=_number(summary, "evlu_amt_smtl_amt", warnings, "overseas totals"),
        unrealized_pnl_krw=_number(summary, "tot_evlu_pfls_amt", warnings, "overseas totals"),
    )
    return totals, holdings


def _server() -> str:
    choice = os.environ.get(KIS_SERVER_ENV, "").strip().lower() or DEFAULT_SERVER
    if choice not in BASE_URLS:
        raise ToolBlocked(
            "KIS_SERVER_UNKNOWN",
            f"{KIS_SERVER_ENV} must be one of {sorted(BASE_URLS)}",
        )
    return choice


class KisHoldingsFeed:
    """Signed read of one KIS account. Built only by :func:`select_holdings_feed` behind the gate;
    every egress re-checks that authorization. Has no order method."""

    feed_id = KIS_PROVIDER
    feed_version = f"{TOOL_VERSION}-kis"
    provider_id = KIS_PROVIDER
    network_egress = True

    def __init__(self, *, authorization: Authorization | None = None) -> None:
        # The host is fixed at construction from a closed map: a key must never be sent to a host
        # this code did not name, and an unknown server name refuses here, before any socket.
        self._server = _server()
        self._base_url = BASE_URLS[self._server]
        self._authorization = authorization
        # (token, monotonic deadline). In memory only — the token is a bearer secret.
        self._token: tuple[str, float] | None = None

    # -- credentials -----------------------------------------------------------------------

    @staticmethod
    def _env(*names: str) -> list[str]:
        values = [os.environ.get(name, "").strip() for name in names]
        missing = [name for name, value in zip(names, values) if not value]
        if missing:
            # Names only — a message that echoed a value would put a secret in a log.
            raise ToolError("NO_API_KEY", f"environment variables not set: {', '.join(missing)}")
        return values

    def _check_gate(self) -> None:
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )

    # -- transport -------------------------------------------------------------------------

    def _send(self, request: urllib.request.Request, *, what: str, timeout_seconds: int) -> tuple[dict[str, Any], str]:
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
                tr_cont = str(response.headers.get("tr_cont") or "").strip()
        except urllib.error.HTTPError as exc:
            # Before the URLError arm: HTTPError subclasses it. The status only — never the URL,
            # a header, or the body, which for the token call would echo the app key back.
            raise ToolError("KIS_REJECTED", f"{what} rejected (HTTP {exc.code})") from None
        except (TimeoutError, urllib.error.URLError):
            raise ToolError("TOOL_TRANSPORT", f"{what} failed or timed out") from None
        try:
            body = json.loads(raw)
        except ValueError:
            raise ToolError("MALFORMED_RESULT", f"{what} returned an unparseable response") from None
        if not isinstance(body, dict):
            raise ToolError("MALFORMED_RESULT", f"{what} returned an unparseable response")
        return body, tr_cont

    def _access_token(self, *, timeout_seconds: int) -> str:
        if self._token is not None and time.monotonic() < self._token[1]:
            return self._token[0]
        app_key, app_secret = self._env(KIS_APP_KEY_ENV, KIS_APP_SECRET_ENV)
        request = urllib.request.Request(
            f"{self._base_url}{TOKEN_PATH}",
            method="POST",
            data=json.dumps({
                "grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )
        # One attempt. A rejection here is a degraded read, never a retry: issuance is limited to
        # about once a minute, so a retry loop is a self-inflicted lockout (and a KakaoTalk notice
        # to the account holder per attempt).
        body, _ = self._send(request, what="token request", timeout_seconds=timeout_seconds)
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise ToolError("MALFORMED_RESULT", "token response carried no access token")
        try:
            lifetime = float(body.get("expires_in"))
        except (TypeError, ValueError):
            lifetime = DEFAULT_TOKEN_LIFETIME_SECONDS
        self._token = (token, time.monotonic() + max(0.0, lifetime - TOKEN_REFRESH_MARGIN_SECONDS))
        return token

    def _inquire(
        self, path: str, tr_id: str, params: dict[str, str], *, what: str,
        timeout_seconds: int, warnings: list[str],
        next_params: Any = None,
    ) -> list[dict[str, Any]]:
        app_key, app_secret = self._env(KIS_APP_KEY_ENV, KIS_APP_SECRET_ENV)
        pages: list[dict[str, Any]] = []
        tr_cont = ""
        for _ in range(MAX_PAGES):
            self._check_gate()
            token = self._access_token(timeout_seconds=timeout_seconds)
            request = urllib.request.Request(
                f"{self._base_url}{path}?{urllib.parse.urlencode(params)}",
                method="GET",
                headers={
                    "Content-Type": "application/json; charset=UTF-8",
                    "authorization": f"Bearer {token}",
                    "appkey": app_key,
                    "appsecret": app_secret,
                    "tr_id": tr_id,
                    "tr_cont": tr_cont,
                    "custtype": "P",
                },
            )
            body, more = self._send(request, what=what, timeout_seconds=timeout_seconds)
            if str(body.get("rt_cd", "")).strip() != "0":
                # KIS's own message code, not its text: the code is enough to look up and cannot
                # carry anything this module sent.
                raise ToolError("KIS_REJECTED", f"{what} refused (msg_cd {body.get('msg_cd', '?')})")
            pages.append(body)
            if more not in ("M", "F"):
                return pages
            tr_cont = "N"
            if next_params is not None:
                params = next_params(params, body)
        warnings.append(f"{what}: stopped after {MAX_PAGES} pages; holdings may be incomplete")
        return pages

    # -- the read --------------------------------------------------------------------------

    def holdings_snapshot(self, *, timeout_seconds: int = 10) -> HoldingsSnapshot:
        self._check_gate()
        account_no, product_code = self._env(KIS_ACCOUNT_NO_ENV, KIS_ACCOUNT_PRODUCT_CODE_ENV)
        started = time.monotonic()
        warnings: list[str] = []
        base = {"CANO": account_no, "ACNT_PRDT_CD": product_code}

        domestic_pages = self._inquire(
            DOMESTIC_BALANCE_PATH, DOMESTIC_TR_ID[self._server],
            {**base, "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01",
             "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
             "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""},
            what="domestic balance", timeout_seconds=timeout_seconds, warnings=warnings,
            next_params=lambda params, body: {
                **params,
                "CTX_AREA_FK100": str(body.get("ctx_area_fk100") or ""),
                "CTX_AREA_NK100": str(body.get("ctx_area_nk100") or ""),
            },
        )
        domestic, holdings = parse_domestic(domestic_pages, warnings)

        overseas: MarketTotals | None = None
        try:
            overseas_pages = self._inquire(
                OVERSEAS_BALANCE_PATH, OVERSEAS_TR_ID[self._server],
                {**base, "WCRC_FRCR_DVSN_CD": "01", "NATN_CD": "000", "TR_MKET_CD": "00",
                 "INQR_DVSN_CD": "00"},
                what="overseas balance", timeout_seconds=timeout_seconds, warnings=warnings,
            )
        except ToolError as exc:
            # The domestic side already succeeded. Losing the overseas side narrows the answer;
            # it does not discard the part that worked. Narrowing means ABSENT, never zero.
            warnings.append(f"overseas balance unavailable ({exc.reason_code})")
        else:
            overseas, overseas_holdings = parse_overseas(overseas_pages, warnings)
            holdings.extend(overseas_holdings)

        return HoldingsSnapshot(
            account=mask_account(account_no, product_code),
            server=self._server,
            domestic=domestic,
            overseas=overseas,
            holdings=tuple(holdings),
            collected_at=timeutil.utc_now_iso(),
            latency_ms=int((time.monotonic() - started) * 1000),
            warnings=tuple(warnings),
        )


def select_holdings_feed() -> HoldingsFeed:
    """The KIS feed if ``MVP_KIS_ACCOUNT=kis``, else the inert one. The capable feed is built by
    the gate, so it cannot exist before its authorization does."""
    return safety_gate.select_env_gated(
        env_var=KIS_ACCOUNT_ENV,
        opt_in_value=KIS_ACCOUNT_ON,
        flags=_NETWORK_FLAGS,
        provider_id=KIS_PROVIDER,
        default_factory=NoHoldingsFeed,
        gated_factory=lambda authorization: KisHoldingsFeed(authorization=authorization),
    )


def read_holdings(*, timeout_seconds: int = 10) -> tuple[HoldingsSnapshot | None, str | None]:
    """Read once. Degrades rather than raising: ``(snapshot, None)``, or ``(None, reason_code)``
    when the gate is closed (``NOT_CONFIGURED``) or the read failed (the error's own code)."""
    try:
        feed = select_holdings_feed()
        snapshot = feed.holdings_snapshot(timeout_seconds=timeout_seconds)
    except (ToolError, ToolBlocked, SafetyGateBlocked) as exc:
        return None, exc.reason_code
    if snapshot is None:
        return None, "NOT_CONFIGURED"
    return snapshot, None
