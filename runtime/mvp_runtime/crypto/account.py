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
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from .. import safety_gate, timeutil
from ..errors import ToolBlocked, ToolError
from ..safety_gate import NETWORK_ACCESS, Authorization

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

RECV_WINDOW_MS = 5000
INCOME_PAGE_LIMIT = 1000  # venue cap per /fapi/v1/income call
QUOTE_ASSET = "USDT"

# Realized-P&L windows reported by a snapshot, in days. The longest one bounds the single
# income query; the shorter ones are bucketed from the same rows (one call, three windows).
PNL_WINDOW_DAYS: tuple[int, ...] = (1, 7, 30)

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


class AccountFeed(Protocol):
    """Read-only account access.

    The protocol deliberately has exactly one method. There is no ``submit``/``cancel``
    sibling to forget to gate — an order-placing capability cannot be reached through an
    ``AccountFeed`` reference at all.
    """

    feed_id: str
    feed_version: str

    def account_snapshot(self, *, timeout_seconds: int) -> AccountSnapshot | None: ...


def _f(value: Any, default: float = 0.0) -> float:
    """Venue numbers arrive as strings; a malformed one must not crash a read."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class NoAccountFeed:
    """Default, inert feed: no key, no socket, no account. Returns None, never raises —
    an unconfigured live account is a normal state, not an error."""

    feed_id = "none"
    feed_version = f"{ACCOUNT_TOOL_VERSION}-none"
    network_egress = False

    def account_snapshot(self, *, timeout_seconds: int) -> AccountSnapshot | None:
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
            # narrow the answer, not discard the part that worked.
            income = []
            warnings.append(f"realized P&L unavailable ({exc.reason_code})")

        latency_ms = int((time.monotonic() - started) * 1000)
        return self._build(account, income, latency_ms=latency_ms, warnings=warnings)

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
            realized_windows=bucket_income(income, now_ms=int(time.time() * 1000)),
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


def bucket_income(rows: Any, *, now_ms: int) -> dict[str, dict[str, float]]:
    """Sum realized P&L, commission and funding into each reporting window.

    ``net`` is the figure that matches the account: realized profit minus what the venue
    actually took. Reporting realized alone would flatter every window.
    """
    windows: dict[str, dict[str, float]] = {
        f"{days}d": {"realized": 0.0, "commission": 0.0, "funding": 0.0, "net": 0.0}
        for days in PNL_WINDOW_DAYS
    }
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


def select_account_feed(
    *, now: str | None = None, root: Any | None = None
) -> AccountFeed:
    """Return the live account feed if the gate is open for it, else the inert one.

    The capable feed is constructed **by** the gate, so it cannot exist before the
    authorization does. Setting the env var without a valid local grant fails closed.
    """
    return safety_gate.select_gated(
        env_var=ACCOUNT_FEED_ENV,
        opt_in_value=BINANCE_ACCOUNT,
        flags=_NETWORK_FLAGS,
        provider_id=BINANCE_ACCOUNT,
        default_factory=NoAccountFeed,
        gated_factory=lambda authorization: BinanceFuturesAccountFeed(authorization=authorization),
        now=now,
        root=root,
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
        bucket = snapshot.realized_windows.get(key, {})
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


def read_account(*, timeout_seconds: int = 10) -> tuple[AccountSnapshot | None, dict[str, Any]]:
    """Read the live account once, returning the snapshot and its evidence record.

    Degrades rather than raising: a transport failure, an unparseable body, or a missing
    credential yields ``(None, record)`` with ``ACCOUNT_DATA_DEGRADED`` in the record, so a
    caller that wants a status board still gets one.
    """
    feed = select_account_feed()
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Live exchange account board (read-only: balance, positions, P&L)."
    )
    parser.add_argument("--json", action="store_true", help="emit the snapshot as JSON")
    parser.add_argument("--timeout", type=int, default=10, help="per-request timeout in seconds")
    args = parser.parse_args(argv)

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
