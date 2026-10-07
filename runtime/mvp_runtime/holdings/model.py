"""The holdings lane's broker-neutral shapes: what a feed returns and what the board reads.

A feed (today ``toss_account``) fills these; ``board`` and ``store`` read only these. Kept apart from
any one broker's module so replacing the broker — KIS to Toss on 2026-10-07 — does not move the
renders or the store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

MARKET_DOMESTIC = "domestic"
MARKET_OVERSEAS = "overseas"


@dataclass(frozen=True)
class Holding:
    """One position as the broker reports it. Carries price-revealing numbers: full board only."""

    market: str
    symbol: str
    name: str
    quantity: float | None
    value: float | None
    unrealized_pnl: float | None
    currency: str


@dataclass(frozen=True)
class MarketTotals:
    """One side of the account, in KRW. ``None`` means not read, never zero."""

    market: str
    holdings_value_krw: float | None
    unrealized_pnl_krw: float | None
    # Cash-based buying power in this side's currency, converted to KRW — not a deposit balance.
    cash_krw: float | None = None


@dataclass(frozen=True)
class HoldingsSnapshot:
    account: str  # masked
    broker: str
    domestic: MarketTotals | None
    overseas: MarketTotals | None
    holdings: tuple[Holding, ...]
    collected_at: str
    latency_ms: int
    warnings: tuple[str, ...] = field(default_factory=tuple)
    # The USD->KRW mid-rate this read used, for the P2 combined total in the same process. In-process
    # only: board.aggregate_view does not carry it, so it is never stored, rendered or sent.
    usd_krw_rate: float | None = None


class HoldingsFeed(Protocol):
    """Read-only holdings access. Every method is a read; there is no order sibling to gate."""

    feed_id: str
    feed_version: str
    network_egress: bool

    def holdings_snapshot(self, *, timeout_seconds: int) -> HoldingsSnapshot | None: ...


class NoHoldingsFeed:
    """The inert default: no key, no socket. ``None`` is a normal state, not an error."""

    feed_id = "none"
    feed_version = "0.2.0-none"
    network_egress = False

    def holdings_snapshot(self, *, timeout_seconds: int) -> HoldingsSnapshot | None:
        return None


def mask_account(account_no: str) -> str:
    """``****8901``. The last four digits identify the account to its owner; nothing more."""
    digits = "".join(ch for ch in str(account_no) if ch.isdigit())
    return f"****{digits[-4:]}" if len(digits) >= 4 else "****"
