"""Live exchange account read — balance, open positions, realized/unrealized P&L.

The first leg of the live-execution port (``CRYPTO_LIVE_EXECUTION_V0.1.md``). It answers
"what is actually in the account right now" against the real venue, and it answers nothing
else: this module is **read-only by construction**. ``BinanceFuturesAccountFeed`` exposes no
method that can place, amend, or cancel an order — the capability is absent from the class,
not merely disabled by a flag (the source system's ``LiveReadOnlyProbe`` posture, whose
GET-only shape was verified against mainnet on 2026-07-16).

That makes this the same effect tier as ``market_data``: an outbound read behind a
per-provider ``network_access`` grant, `INTERNAL_READ` in permission terms, and **not** a
trading capability. Placing an order is a different effect tier entirely (external +
financial) and is deliberately not reachable from here.

Unlike the public klines endpoint, account endpoints are **signed**: an API key travels in
the ``X-MBX-APIKEY`` header and an HMAC-SHA256 signature over the query string proves it.
The secret is read from the environment at call time and never stored, returned, logged, or
audited — and because the signature rides in the URL, transport failures are reported with a
generic message that never echoes the request (the R3 transport-error posture).

A backend failure **degrades, never blocks**: callers record ``ACCOUNT_DATA_DEGRADED`` and
carry on without live account figures, exactly as ``MARKET_DATA_DEGRADED`` does for candles.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from .. import safety_gate, timeutil
from ..cli_common import force_utf8_io
from ..errors import ToolBlocked, ToolError
from ..safety_gate import NETWORK_ACCESS, Authorization
from ..coerce import as_float as _f

ACCOUNT_TOOL_ID = "crypto.account.readonly"
ACCOUNT_TOOL_VERSION = "0.1.0"
ACCOUNT_TOOL_CLASS = "read"

# Opting into the real account feed. Like every other network capability, the env var alone
# is NOT sufficient: the Safety-Flag Gate must authorize network_access for this provider's
# own id first, and the grant is re-verified at the moment of egress.
ACCOUNT_FEED_ENV = "MVP_ACCOUNT_FEED"
BINANCE_ACCOUNT = "binance_futures_account"
_NETWORK_FLAGS = (NETWORK_ACCESS,)

# The account feed gets its OWN provider id and therefore its own per-machine grant, even
# though it talks to the same venue as `binance_futures`. That grant authorizes a key with
# a strictly wider blast radius (it can read balances), so it must be scoped, expired and
# revocable on its own — one grant per provider, exactly like the model failover chain.
ACCOUNT_API_KEY_ENV = "BINANCE_ACCOUNT_API_KEY"
ACCOUNT_API_SECRET_ENV = "BINANCE_ACCOUNT_API_SECRET"

# Degraded-run reason code: a live account read failed and the caller continues without it.
ACCOUNT_DATA_DEGRADED = "ACCOUNT_DATA_DEGRADED"

# Mainnet USD-M Futures only. Checked at construction so a misconfigured base URL cannot
# quietly point the signed key at another host (the source adapter's host-allowlist rule).
ALLOWED_ACCOUNT_HOSTS = frozenset({"fapi.binance.com"})
DEFAULT_ACCOUNT_BASE_URL = "https://fapi.binance.com"

ACCOUNT_PATH = "/fapi/v2/account"
INCOME_PATH = "/fapi/v1/income"

# Per-FILL history, which `/fapi/v1/income` cannot give. Income reports COMMISSION as a
# per-window total with no way to tell which leg paid it, so the taker rate in `cost.py` had
# to be hand-derived on 2026-07-26 by dividing one window's commission by an estimated
# notional — and the maker rate could not be derived at all, because a maker exit's fee and a
# taker entry's fee land in the same bucket. This endpoint returns `commission`,
# `commissionAsset`, `quoteQty` and a `maker` boolean per fill, which is exactly the split.
USER_TRADES_PATH = "/fapi/v1/userTrades"

RECV_WINDOW_MS = 5000
INCOME_PAGE_LIMIT = 1000  # venue cap per /fapi/v1/income call
USER_TRADES_PAGE_LIMIT = 1000  # venue cap per /fapi/v1/userTrades call
QUOTE_ASSET = "USDT"

# How far back a fee measurement looks by default. Fee tiers change, so an unbounded history
# would average across rates the account no longer pays; 30 days matches the longest window
# the snapshot already reports.
FEE_MEASUREMENT_DAYS = 30

# Realized-P&L windows reported by a snapshot, in days. The longest one bounds the single
# income query; the shorter ones are bucketed from the same rows (one call, three windows).
PNL_WINDOW_DAYS: tuple[int, ...] = (1, 7, 30)

# The UTC calendar day, bucketed from the same rows as the rolling windows above.
#
# It exists because the rolling `1d` window is NOT the same statistic as "today", and the
# difference is not academic for a *daily loss limit*: a net sum over a wider window can be
# LESS negative, because it also picks up the wider window's profits. Yesterday 23:00 +50,
# today 01:00 -30 reads as -30 on the calendar day and +20 on the rolling 24h — today's loss
# hidden behind yesterday's profit. The rest of this runtime means the calendar day when it
# says "daily" (`live_order.count_today` counts the daily order cap on `utc_day()`), so the
# loss limit in the same registered budget must be able to speak the same way.
PNL_WINDOW_TODAY = "today"

# Income types that move real money. Realized P&L alone overstates the result — commission
# and funding are what the venue actually took — so the net figure carries all three.
_REALIZED = "REALIZED_PNL"
_COMMISSION = "COMMISSION"
_FUNDING = "FUNDING_FEE"
_COUNTED_INCOME = (_REALIZED, _COMMISSION, _FUNDING)


@dataclass
class AccountPosition:
    """One open position as the venue reports it. Read-only; nothing here can close it."""

    symbol: str
    side: str  # LONG | SHORT, derived from the signed position amount
    quantity: float  # absolute size in base units
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: float
    notional: float


@dataclass
class AccountSnapshot:
    """Point-in-time account state. Every figure comes from the venue, none is computed
    from local paper state — this is the real book, not the simulation."""

    asset: str
    wallet_balance: float
    margin_balance: float
    available_balance: float
    unrealized_pnl: float
    positions: list[AccountPosition]
    realized_windows: dict[str, dict[str, float]]
    source: str
    collected_at: str
    feed_version: str = ACCOUNT_TOOL_VERSION
    latency_ms: int = 0
    warnings: list[str] = field(default_factory=list)
    # symbol -> the leverage the venue will apply when a position IS opened there. Separate
    # from `positions` because a symbol holding nothing still has a setting, and that setting
    # is what the liquidation guard needs BEFORE the first fill. See
    # :func:`parse_configured_leverage`.
    configured_leverage: dict[str, float] = field(default_factory=dict)


class AccountFeed(Protocol):
    """Read-only account access.

    Every method here is a GET. There is no ``submit``/``cancel`` sibling to forget to gate —
    an order-placing capability cannot be reached through an ``AccountFeed`` reference at all.

    That invariant used to be stated as "the protocol deliberately has exactly one method",
    which was a proxy for it and stopped being true when ``fill_history`` was added. The count
    was never the point; **read-only** was. A second GET does not widen the blast radius —
    it reads the same account under the same grant — and stating the rule directly means the
    next read does not have to argue with a sentence about arithmetic.
    """

    feed_id: str
    feed_version: str

    def account_snapshot(self, *, timeout_seconds: int) -> AccountSnapshot | None: ...

    # `fill_history`, not `user_trades` after the endpoint it calls. A structural test
    # (`test_account_feed_has_no_order_capability`) refuses any public method on these classes
    # whose name contains "order"/"submit"/"cancel"/"trade"/…, and that guard is valuable
    # precisely because it is blunt enough to be unarguable — carving an exception into it for
    # a method that only reads would teach the next reader that the list is negotiable.
    # "Fill history" is also the more accurate name: it returns fills, and in this codebase
    # "trade" names the action.
    def fill_history(
        self, symbol: str, *, start_ms: int, timeout_seconds: int
    ) -> list[dict[str, Any]] | None: ...


class NoAccountFeed:
    """Default, inert feed: no key, no socket, no account. Returns None, never raises —
    an unconfigured live account is a normal state, not an error."""

    feed_id = "none"
    feed_version = f"{ACCOUNT_TOOL_VERSION}-none"
    network_egress = False

    def account_snapshot(self, *, timeout_seconds: int) -> AccountSnapshot | None:
        return None

    def fill_history(
        self, symbol: str, *, start_ms: int, timeout_seconds: int
    ) -> list[dict[str, Any]] | None:
        # None, not []: an empty list is a real answer ("this account has no fills") and a
        # fee rate measured over it would be an honest absence. No feed at all is a different
        # statement, and the two must not collapse into one.
        return None


class BinanceFuturesAccountFeed:
    """Signed read of a real Binance USD-M Futures account.

    Constructed only through :func:`select_account_feed` after the Safety-Flag Gate opens
    for ``binance_futures_account``; ``account_snapshot`` re-verifies that authorization at
    the moment of egress. Read-only by construction: this class has no order method.
    """

    feed_id = BINANCE_ACCOUNT
    feed_version = f"{ACCOUNT_TOOL_VERSION}-binance"
    provider_id = BINANCE_ACCOUNT
    network_egress = True
    source = "binance_futures_account"

    def __init__(
        self,
        *,
        authorization: Authorization | None = None,
        base_url: str = DEFAULT_ACCOUNT_BASE_URL,
    ) -> None:
        host = (urllib.parse.urlparse(base_url).hostname or "").lower()
        if host not in ALLOWED_ACCOUNT_HOSTS:
            # Refuse at construction: a signed key must never be pointed at an unexpected
            # host, and a URL typo should fail loudly rather than leak a credential.
            raise ToolBlocked(
                "HOST_NOT_ALLOWED",
                "account base URL is not an allowed live host",
            )
        self._base_url = base_url.rstrip("/")
        self._authorization = authorization

    def account_snapshot(self, *, timeout_seconds: int = 10) -> AccountSnapshot:
        # Chokepoint: re-verify authorization at the moment of egress (defense in depth).
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )
        started = time.monotonic()
        account = self._signed_get(ACCOUNT_PATH, {}, timeout_seconds=timeout_seconds)

        warnings: list[str] = []
        income: Any = None
        try:
            income = self._signed_get(
                INCOME_PATH,
                {
                    "startTime": int(time.time() * 1000) - max(PNL_WINDOW_DAYS) * 86_400_000,
                    "limit": INCOME_PAGE_LIMIT,
                },
                timeout_seconds=timeout_seconds,
            )
        except ToolError as exc:
            # Balances and positions already succeeded. Losing the P&L history should
            # narrow the answer, not discard the part that worked. Narrowing means the
            # windows are ABSENT, never zero: a zero here would read downstream as "no
            # loss today" on the series the daily-loss breaker meters.
            warnings.append(f"realized P&L unavailable ({exc.reason_code})")
        else:
            if isinstance(income, list) and len(income) >= INCOME_PAGE_LIMIT:
                # A full page is the venue cap, and the endpoint returns rows ascending,
                # so what fell off the end is the NEWEST income — exactly the window the
                # daily-loss breaker measures. Like ``fill_history`` below, this call
                # deliberately does not paginate; unlike a fee measurement, a possibly
                # truncated P&L answer must not travel at all, because ``bucket_income``
                # would read the missing newest rows as a comfortable zero. Withholding
                # the windows makes ``venue_daily_realized_net`` answer ``None`` and the
                # breaker fall back to the local ledger — the breaker can only trip
                # earlier from this, never later.
                income = None
                warnings.append(
                    f"realized P&L income page full ({INCOME_PAGE_LIMIT} rows); "
                    "windows withheld as possibly truncated"
                )

        latency_ms = int((time.monotonic() - started) * 1000)
        return self._build(account, income, latency_ms=latency_ms, warnings=warnings)

    def fill_history(
        self, symbol: str, *, start_ms: int, timeout_seconds: int = 10
    ) -> list[dict[str, Any]]:
        """This account's own fills for one symbol since ``start_ms``. A GET, nothing else.

        Same egress chokepoint as ``account_snapshot`` — the grant is re-verified here rather
        than trusted from construction, because that is the property the gate actually
        guarantees and a second entry point is a second place to forget it.

        The venue requires a symbol on this endpoint and caps a page at 1000 fills; a canary
        account is nowhere near that, so this deliberately does not paginate. It returns what
        the page holds and the caller decides — a silent truncation dressed as a complete
        history is exactly the failure a fee measurement must not make, so the count travels
        with the answer (:class:`MeasuredFeeRate.fills`) rather than being dropped here.
        """
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )
        rows = self._signed_get(
            USER_TRADES_PATH,
            {"symbol": symbol, "startTime": int(start_ms), "limit": USER_TRADES_PAGE_LIMIT},
            timeout_seconds=timeout_seconds,
        )
        if not isinstance(rows, list):
            raise ToolError("MALFORMED_RESULT", "live account returned an unparseable fill history")
        return [row for row in rows if isinstance(row, dict)]

    def _signed_get(
        self, path: str, params: dict[str, Any], *, timeout_seconds: int
    ) -> Any:
        api_key = os.environ.get(ACCOUNT_API_KEY_ENV, "").strip()
        api_secret = os.environ.get(ACCOUNT_API_SECRET_ENV, "").strip()
        if not api_key or not api_secret:
            # Names only — the absence of a credential is reportable, its value never is.
            raise ToolError(
                "NO_API_KEY",
                f"live account credentials are not configured "
                f"({ACCOUNT_API_KEY_ENV}/{ACCOUNT_API_SECRET_ENV})",
            )
        query = dict(params)
        query.setdefault("recvWindow", RECV_WINDOW_MS)
        query["timestamp"] = int(time.time() * 1000)
        encoded = urllib.parse.urlencode(query)
        signature = hmac.new(
            api_secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        request = urllib.request.Request(
            f"{self._base_url}{path}?{encoded}&signature={signature}",
            method="GET",
            headers={"Accept": "application/json", "X-MBX-APIKEY": api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except (TimeoutError, urllib.error.URLError):
            # Deliberately generic: the URL carries the signature, so it must never reach
            # a message, a log, or a record (the market-data transport posture).
            raise ToolError("TOOL_TRANSPORT", "live account request failed or timed out") from None
        try:
            return json.loads(raw)
        except ValueError:
            raise ToolError("MALFORMED_RESULT", "live account returned an unparseable response") from None

    def _build(
        self,
        account: Any,
        income: Any,
        *,
        latency_ms: int,
        warnings: list[str],
    ) -> AccountSnapshot:
        if not isinstance(account, dict):
            raise ToolError("MALFORMED_RESULT", "live account returned an unparseable response")
        return AccountSnapshot(
            asset=QUOTE_ASSET,
            wallet_balance=_f(account.get("totalWalletBalance")),
            margin_balance=_f(account.get("totalMarginBalance")),
            available_balance=_f(account.get("availableBalance")),
            unrealized_pnl=_f(account.get("totalUnrealizedProfit")),
            positions=parse_positions(account.get("positions")),
            configured_leverage=parse_configured_leverage(account.get("positions")),
            # ``None`` income means "unreadable or possibly truncated": no windows at all,
            # so every reader must say "unknown" rather than mistake absence for zero.
            realized_windows=(
                bucket_income(income, now_ms=int(time.time() * 1000))
                if income is not None
                else {}
            ),
            source=self.source,
            collected_at=timeutil.utc_now_iso(),
            feed_version=self.feed_version,
            latency_ms=latency_ms,
            warnings=warnings,
        )


def parse_positions(rows: Any) -> list[AccountPosition]:
    """Open positions only. A venue reports every symbol it knows about, most with a zero
    amount; those are not positions and are dropped."""
    positions: list[AccountPosition] = []
    if not isinstance(rows, list):
        return positions
    for row in rows:
        if not isinstance(row, dict):
            continue
        amount = _f(row.get("positionAmt"))
        if amount == 0.0:
            continue
        positions.append(
            AccountPosition(
                symbol=str(row.get("symbol") or ""),
                side="LONG" if amount > 0 else "SHORT",
                quantity=abs(amount),
                entry_price=_f(row.get("entryPrice")),
                mark_price=_f(row.get("markPrice")),
                unrealized_pnl=_f(row.get("unrealizedProfit")),
                leverage=_f(row.get("leverage")),
                notional=abs(_f(row.get("notional"))),
            )
        )
    positions.sort(key=lambda p: p.symbol)
    return positions


def parse_configured_leverage(rows: Any) -> dict[str, float]:
    """Every symbol's configured leverage, INCLUDING the ones holding nothing.

    ``/fapi/v2/account`` reports a row per symbol the venue knows about and most carry
    ``positionAmt == 0``, which :func:`parse_positions` drops — correctly, because a zero
    amount is not a position. The leverage on those rows is not noise though: it is the
    setting that WILL apply when one is opened, and before the first fill it is the only
    place the venue states it.

    A separate function rather than a widening of `parse_positions`, because "what am I
    holding" and "what setting would apply" are different questions; merging them would make
    an empty book indistinguishable from an unconfigured one, which is exactly the
    distinction the liquidation guard needs.

    Rows without a usable positive leverage are omitted rather than defaulted, so a caller
    can tell "the venue did not say" from "the venue said something", and pick its own
    fail-closed answer for the first case.
    """
    leverage: dict[str, float] = {}
    if not isinstance(rows, list):
        return leverage
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "")
        if not symbol:
            continue
        value = _f(row.get("leverage"))
        if value > 0:
            leverage[symbol] = value
    return leverage


def bucket_income(rows: Any, *, now_ms: int) -> dict[str, dict[str, float]]:
    """Sum realized P&L, commission and funding into each reporting window.

    ``net`` is the figure that matches the account: realized profit minus what the venue
    actually took. Reporting realized alone would flatter every window.
    """
    windows: dict[str, dict[str, float]] = {
        f"{days}d": {"realized": 0.0, "commission": 0.0, "funding": 0.0, "net": 0.0}
        for days in PNL_WINDOW_DAYS
    }
    windows[PNL_WINDOW_TODAY] = {"realized": 0.0, "commission": 0.0, "funding": 0.0, "net": 0.0}
    # Start of the UTC calendar day containing ``now_ms``. Derived from the same clock the
    # rolling windows use, so the two cannot disagree about when "now" is.
    day_start_ms = (
        datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000.0
    )
    if not isinstance(rows, list):
        return windows
    for row in rows:
        if not isinstance(row, dict):
            continue
        income_type = str(row.get("incomeType") or "")
        if income_type not in _COUNTED_INCOME:
            continue
        if str(row.get("asset") or QUOTE_ASSET) != QUOTE_ASSET:
            continue
        amount = _f(row.get("income"))
        stamp = _f(row.get("time"))
        age_days = (now_ms - stamp) / 86_400_000.0
        key = {
            _REALIZED: "realized",
            _COMMISSION: "commission",
            _FUNDING: "funding",
        }[income_type]
        for days in PNL_WINDOW_DAYS:
            if age_days <= days:
                bucket = windows[f"{days}d"]
                bucket[key] = round(bucket[key] + amount, 8)
                bucket["net"] = round(bucket["net"] + amount, 8)
        if stamp >= day_start_ms:
            bucket = windows[PNL_WINDOW_TODAY]
            bucket[key] = round(bucket[key] + amount, 8)
            bucket["net"] = round(bucket["net"] + amount, 8)
    return windows


def return_pct(net_pnl: float, margin_balance: float) -> float | None:
    """Return over the window, against the balance the window started from.

    ``margin_balance - net`` is that starting balance. It is an approximation — a deposit or
    withdrawal inside the window moves it — so the caller labels it as such rather than
    presenting it as an audited performance figure. Returns None when the basis is not a
    positive number, instead of inventing a percentage.
    """
    basis = margin_balance - net_pnl
    if basis <= 0:
        return None
    return round(net_pnl / basis * 100.0, 4)


# --- measured fee rates (the maker/taker split income cannot give) -----------------

MAKER, TAKER = "maker", "taker"


@dataclass(frozen=True)
class MeasuredFeeRate:
    """What this account was actually charged on one side of the book.

    ``rate_bps`` is None whenever the number would be invented rather than measured, and the
    other fields say which case it was. That distinction is the whole point of the class:
    `cost.DEFAULT_MAKER_FEE_BPS` is Binance's PUBLISHED rate carrying an explicit "should be
    replaced with a measurement", and a measurement that quietly returns 0.0 for "no maker
    fill has ever happened" would look exactly like a real reading of a zero-fee venue.
    """

    fills: int
    notional_usdt: float
    commission_usdt: float
    rate_bps: float | None
    # Why there is no rate, when there is none. Empty on a successful measurement.
    unmeasurable_reason: str = ""
    # Commission assets seen on this side. A BNB-paid fee is a real discount, not a bug, but
    # it is not denominated in the notional's currency — so it is named, never divided.
    commission_assets: tuple[str, ...] = ()


def measure_fee_rates(trades: Any) -> dict[str, MeasuredFeeRate]:
    """Split fills into maker/taker and derive the effective bps rate of each. Pure.

    ``rate_bps = commission / notional * 10000`` per side, over the fills the venue itself
    labelled — ``maker`` is a boolean on every ``/fapi/v1/userTrades`` row, so neither side is
    inferred from an order type this code chose.

    Three cases refuse rather than answer, each named in ``unmeasurable_reason``:

    - **no fills on that side.** The live state as of 2026-07-28: every order this account has
      ever placed is a MARKET canary, so the maker side has a sample of zero. "No maker fill
      has happened yet" and "the maker fee is zero" are opposite claims about real money.
    - **zero notional.** Nothing to divide by.
    - **a commission asset other than USDT.** Paying fees in BNB is a legitimate discount, but
      that commission is not in the notional's units and converting it would need a BNB price
      this function does not have and must not guess.

    A negative rate is returned as-is: some tiers rebate the maker side, and clamping it would
    hide the one result that would most change how the cost model is written.
    """
    buckets: dict[str, list[dict[str, Any]]] = {MAKER: [], TAKER: []}
    for row in trades if isinstance(trades, list) else []:
        if isinstance(row, dict):
            buckets[MAKER if row.get("maker") else TAKER].append(row)

    measured: dict[str, MeasuredFeeRate] = {}
    for side, rows in buckets.items():
        notional = sum(_f(row.get("quoteQty")) for row in rows)
        commission = sum(_f(row.get("commission")) for row in rows)
        assets = tuple(sorted({
            str(row.get("commissionAsset") or "").upper() for row in rows
        } - {""}))
        if not rows:
            reason = f"no {side} fill in the window"
        elif set(assets) - {QUOTE_ASSET}:
            reason = f"commission paid in {', '.join(assets)}, not {QUOTE_ASSET} only"
        elif notional <= 0:
            reason = "fills carry no notional"
        else:
            reason = ""
        measured[side] = MeasuredFeeRate(
            fills=len(rows),
            notional_usdt=round(notional, 8),
            commission_usdt=round(commission, 8),
            rate_bps=round(commission / notional * 10_000.0, 4) if not reason else None,
            unmeasurable_reason=reason,
            commission_assets=assets,
        )
    return measured


def select_account_feed(
    *, now: str | None = None, root: Any | None = None
) -> AccountFeed:
    """Return the live account feed if the gate is open for it, else the inert one.

    The capable feed is constructed **by** the gate, so it cannot exist before the
    authorization does. The environment is the gate (Thomas 2026-08-10): an unset or
    different ``MVP_ACCOUNT_FEED`` selects the inert feed, never a network path.
    """
    del now, root  # the environment is the gate (Thomas 2026-08-10)
    return safety_gate.select_env_gated(
        env_var=ACCOUNT_FEED_ENV,
        opt_in_value=BINANCE_ACCOUNT,
        flags=_NETWORK_FLAGS,
        provider_id=BINANCE_ACCOUNT,
        default_factory=NoAccountFeed,
        gated_factory=lambda authorization: BinanceFuturesAccountFeed(authorization=authorization),
    )


def snapshot_record(snapshot: AccountSnapshot | None, *, feed: AccountFeed, now: str) -> dict[str, Any]:
    """Evidence record for one account read — the market-data tool_use record's shape.

    Metadata only: balances and sizes are the answer the operator asked for, but no
    credential, no signed URL, and no raw venue payload ever enters it.
    """
    record: dict[str, Any] = {
        "tool_id": ACCOUNT_TOOL_ID,
        "tool_version": getattr(feed, "feed_version", ACCOUNT_TOOL_VERSION),
        "tool_class": ACCOUNT_TOOL_CLASS,
        "operation": "account_snapshot",
        "feed_id": getattr(feed, "feed_id", "none"),
        "read_only": True,
        "external_action": False,
        "network_egress": bool(getattr(feed, "network_egress", False)),
        "created_at": now,
    }
    if snapshot is None:
        record["configured"] = False
        return record
    record.update(
        {
            "configured": True,
            "asset": snapshot.asset,
            "wallet_balance": snapshot.wallet_balance,
            "margin_balance": snapshot.margin_balance,
            "available_balance": snapshot.available_balance,
            "unrealized_pnl": snapshot.unrealized_pnl,
            "open_position_count": len(snapshot.positions),
            # The settings, not the holdings: what the liquidation guard would assume for a
            # symbol this account has never traded. Recorded so a refusal can be read back
            # against the leverage that produced it.
            "configured_leverage": dict(snapshot.configured_leverage),
            "realized_windows": snapshot.realized_windows,
            "source": snapshot.source,
            "collected_at": snapshot.collected_at,
            "latency_ms": snapshot.latency_ms,
            "warnings": list(snapshot.warnings),
        }
    )
    return record


def render_account_text(snapshot: AccountSnapshot | None) -> str:
    """ASCII-only account board. Windows consoles are cp949 and die on fancy dashes."""
    if snapshot is None:
        return "account     : not configured (no live account feed)"
    lines = [
        "=== live account ===",
        f"balance     : {snapshot.wallet_balance:.2f} {snapshot.asset} wallet, "
        f"{snapshot.margin_balance:.2f} margin, {snapshot.available_balance:.2f} available",
        f"unrealized  : {snapshot.unrealized_pnl:+.2f} {snapshot.asset}",
    ]
    for days in PNL_WINDOW_DAYS:
        key = f"{days}d"
        bucket = snapshot.realized_windows.get(key)
        if not isinstance(bucket, dict):
            # Withheld (unreadable or possibly truncated income) renders as unknown.
            # A board that prints +0.00 for a window nobody read is the confident zero
            # the withholding exists to prevent.
            lines.append(f"realized {key:4}: n/a (income history withheld)")
            continue
        net = bucket.get("net", 0.0)
        pct = return_pct(net, snapshot.margin_balance)
        pct_text = "n/a" if pct is None else f"{pct:+.2f}%"
        lines.append(
            f"realized {key:4}: {net:+.2f} {snapshot.asset} ({pct_text} approx) "
            f"[pnl {bucket.get('realized', 0.0):+.2f}, "
            f"fee {bucket.get('commission', 0.0):+.2f}, "
            f"funding {bucket.get('funding', 0.0):+.2f}]"
        )
    if snapshot.positions:
        lines.append(f"positions   : {len(snapshot.positions)} open")
        for position in snapshot.positions:
            lines.append(
                f"  {position.symbol:12} {position.side:5} qty {position.quantity:g} "
                f"@ {position.entry_price:g} mark {position.mark_price:g} "
                f"upnl {position.unrealized_pnl:+.2f} lev {position.leverage:g}x"
            )
    else:
        lines.append("positions   : none")
    lines.append(f"collected   : {snapshot.collected_at} ({snapshot.latency_ms} ms)")
    for warning in snapshot.warnings:
        lines.append(f"WARNING     : {warning}")
    return "\n".join(lines)


def read_account(
    *, timeout_seconds: int = 10, root: Any | None = None
) -> tuple[AccountSnapshot | None, dict[str, Any]]:
    """Read the live account once, returning the snapshot and its evidence record.

    Degrades rather than raising: a transport failure, an unparseable body, or a missing
    credential yields ``(None, record)`` with ``ACCOUNT_DATA_DEGRADED`` in the record, so a
    caller that wants a status board still gets one.

    ``root`` is the state root the grant is read from, threaded through to
    ``select_account_feed``. It exists because a caller that redirects state — every
    ``--root`` in ``scripts/`` — was silently not redirecting *this* read: the grant lookup
    resolved from the running code's repo instead, so a run whose other state came from
    ``--root`` refused with ``ACTIVATION_MISSING`` while a valid grant sat in the directory
    it was told to use. ``None`` keeps the repo-local default, which is every normal run.
    """
    feed = select_account_feed(root=root)
    now = timeutil.utc_now_iso()
    try:
        snapshot = feed.account_snapshot(timeout_seconds=timeout_seconds)
    except ToolError as exc:
        record = snapshot_record(None, feed=feed, now=now)
        record["degraded"] = True
        record["degraded_reason_code"] = ACCOUNT_DATA_DEGRADED
        record["error_reason_code"] = exc.reason_code
        return None, record
    return snapshot, snapshot_record(snapshot, feed=feed, now=now)


def read_fee_rates(
    symbol: str, *, days: int = FEE_MEASUREMENT_DAYS, timeout_seconds: int = 10,
    root: Any | None = None, now_ms: int | None = None,
) -> tuple[dict[str, MeasuredFeeRate] | None, dict[str, Any]]:
    """Measure this account's maker/taker rates from its own fills. Read-only, degrades.

    Same shape and same posture as :func:`read_account` — a transport failure, a missing
    credential or an unconfigured feed yields ``(None, record)`` rather than raising, because
    a fee measurement is diagnostic and must never be able to take a caller down with it.

    ``(None, …)`` means *the read did not happen*. A successful read over an account with no
    fills returns a dict whose sides both carry ``rate_bps=None`` and say why — the difference
    between "could not look" and "looked, and there is nothing there" is the difference between
    retrying and accepting an answer.
    """
    feed = select_account_feed(root=root)
    now = timeutil.utc_now_iso()
    start_ms = (now_ms if now_ms is not None else int(time.time() * 1000)) - days * 86_400_000
    record: dict[str, Any] = {
        "tool_id": ACCOUNT_TOOL_ID,
        "tool_version": getattr(feed, "feed_version", ACCOUNT_TOOL_VERSION),
        "tool_class": ACCOUNT_TOOL_CLASS,
        "operation": "fee_rate_measurement",
        "feed_id": getattr(feed, "feed_id", "none"),
        "read_only": True,
        "external_action": False,
        "network_egress": bool(getattr(feed, "network_egress", False)),
        "symbol": symbol,
        "window_days": days,
        "created_at": now,
    }
    try:
        trades = feed.fill_history(symbol, start_ms=start_ms, timeout_seconds=timeout_seconds)
    except ToolError as exc:
        record.update({"configured": True, "degraded": True,
                       "degraded_reason_code": ACCOUNT_DATA_DEGRADED,
                       "error_reason_code": exc.reason_code})
        return None, record
    if trades is None:
        record["configured"] = False
        return None, record

    measured = measure_fee_rates(trades)
    record.update({
        "configured": True,
        # Rates and counts only. A fill row carries an order id and an exact price; this
        # record is evidence that a measurement happened, not a copy of the venue's book.
        "measured": {side: asdict(rate) for side, rate in measured.items()},
    })
    return measured, record


def render_fee_rates(measured: dict[str, MeasuredFeeRate] | None, *, symbol: str, days: int) -> str:
    """The operator-facing board. States the sample size next to every rate, always.

    A bps figure with no fill count behind it is how the 2026-07-26 taker reading came to be
    quoted as settled: it was two canary entries and their closes, which is a fine measurement
    of a published rate and a poor one of anything that varies.
    """
    if measured is None:
        return f"=== measured fee rates ({symbol}, {days}d) ===\nnot configured or read failed"
    lines = [f"=== measured fee rates ({symbol}, {days}d) ==="]
    for side in (MAKER, TAKER):
        rate = measured[side]
        if rate.rate_bps is None:
            lines.append(f"{side:6}: -            ({rate.unmeasurable_reason})")
        else:
            lines.append(
                f"{side:6}: {rate.rate_bps:8.4f} bps  over {rate.fills} fill(s), "
                f"{rate.commission_usdt:+.6f} / {rate.notional_usdt:.2f} USDT"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(
        description="Live exchange account board (read-only: balance, positions, P&L)."
    )
    parser.add_argument("--json", action="store_true", help="emit the snapshot as JSON")
    parser.add_argument("--timeout", type=int, default=10, help="per-request timeout in seconds")
    parser.add_argument("--fee-rates", metavar="SYMBOL", default=None,
                        help="instead of the board, measure this symbol's maker/taker fee "
                             "rates from this account's own fills")
    parser.add_argument("--fee-window-days", type=int, default=FEE_MEASUREMENT_DAYS,
                        help="how far back --fee-rates looks (default 30)")
    args = parser.parse_args(argv)

    if args.fee_rates:
        measured, record = read_fee_rates(
            args.fee_rates, days=args.fee_window_days, timeout_seconds=args.timeout,
        )
        if args.json:
            sys.stdout.write(json.dumps({"record": record}, ensure_ascii=False, indent=1) + "\n")
        else:
            sys.stdout.write(
                render_fee_rates(measured, symbol=args.fee_rates, days=args.fee_window_days) + "\n"
            )
        return 1 if record.get("degraded") else 0

    snapshot, record = read_account(timeout_seconds=args.timeout)
    if args.json:
        payload: dict[str, Any] = {"record": record}
        if snapshot is not None:
            payload["snapshot"] = asdict(snapshot)
            payload["return_pct"] = {
                key: return_pct(bucket.get("net", 0.0), snapshot.margin_balance)
                for key, bucket in snapshot.realized_windows.items()
            }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    else:
        sys.stdout.write(render_account_text(snapshot) + "\n")
        if record.get("degraded"):
            sys.stdout.write(
                f"DEGRADED    : live account read failed ({record.get('error_reason_code')})\n"
            )
    return 1 if record.get("degraded") else 0


if __name__ == "__main__":
    raise SystemExit(main())
