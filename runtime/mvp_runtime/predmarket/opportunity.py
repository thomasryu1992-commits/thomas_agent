"""PM1 — is the gap between two venues real once it is paid for? Read-only; decides nothing.

The observation half of the track. Given one operator-confirmed pair and both venues' current
quotes, this computes what a market-neutral cross-venue position would actually earn, net of
the taker fee on **both** legs, and records it. Repeated over weeks, those records answer the
three questions PM1 exists to answer: **how often, how large, and how long they last.**

**The arithmetic, stated once.** A binary contract pays $1 if the event happens. Holding YES
on one venue and NO on the other pays exactly $1 whichever way it resolves, so the position is
market-neutral and its profit is decided entirely at entry. Buying NO at ``1-b`` is the same
trade as selling YES at ``b`` (both venues keep ``no_ask = 1 - yes_bid`` internally), so:

    cost    = yes_ask_A + (1 - yes_bid_B)
    payout  = 1
    gross   = yes_bid_B - yes_ask_A

which needs only the YES side of both books — exactly what the adapters carry. Both directions
are evaluated; at most one can be positive.

**Fees are not a detail here, they are the finding.** Both venues charge ``rate x P x (1-P)``,
which **peaks at 50/50** — precisely where two venues disagree often enough to look like an
opportunity. A mid-priced crypto pair costs about 3 cents per contract across the two legs, so
a 2-cent gross gap is a loss reported as a find. An unadjusted detector would produce a
report full of them, consistently, for weeks.

**Size is recorded, never assumed.** A 4-cent net edge on 3 contracts and on 3,000 contracts
are different facts, and the pessimistic fill model that turns depth into a realistic size is
PM2's job. Here the size at the touch rides along so PM2 has something honest to model and so
a report can separate "frequent" from "worth doing".

**Nothing here trades, and nothing here decides.** An observation is evidence. Whether the
edge is worth acting on is the PM2 criteria decision, which is Thomas's and is deliberately
still open.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from .fees import round_trip_fee
from .market_data import KALSHI, POLYMARKET, PredMarket, VenueQuote

OPPORTUNITY_VERSION = "predmarket_opportunity.v0.1"

# Directions, named from where the YES leg is bought.
BUY_YES_KALSHI = "BUY_YES_KALSHI_SELL_YES_POLYMARKET"
BUY_YES_POLYMARKET = "BUY_YES_POLYMARKET_SELL_YES_KALSHI"

# Why no number could be produced. An observation with a reason is still worth recording:
# "we looked and could not see" is different from "we did not look", and a report that
# silently omits unquoted scans overstates how often the pair was observable.
NOT_QUOTED = "NOT_QUOTED"
NO_SIZE = "NO_SIZE_AT_TOUCH"


@dataclass(frozen=True)
class Leg:
    """One side of the position, priced at the touch."""

    venue: str
    action: str          # BUY_YES | SELL_YES (selling YES == buying NO)
    price: float
    size: float | None

    def as_dict(self) -> dict[str, Any]:
        return {"venue": self.venue, "action": self.action, "price": self.price, "size": self.size}


def _direction(
    *,
    buy_venue: str,
    buy_price: float | None,
    buy_size: float | None,
    sell_venue: str,
    sell_price: float | None,
    sell_size: float | None,
    polymarket_category: Any,
    contracts: float,
) -> dict[str, Any] | None:
    """One direction's economics, or ``None`` when either leg is unquoted."""
    if buy_price is None or sell_price is None:
        return None
    gross = round(sell_price - buy_price, 10)
    kalshi_price = buy_price if buy_venue == KALSHI else sell_price
    poly_price = buy_price if buy_venue == POLYMARKET else sell_price
    fees = round_trip_fee(
        kalshi_price=kalshi_price,
        polymarket_price=poly_price,
        contracts=contracts,
        polymarket_category=polymarket_category,
    )
    total_fee = fees["total_fee_usd"]
    # Per contract, so gross and net are comparable regardless of the size at the touch.
    fee_per_contract = None if total_fee is None else round(total_fee / contracts, 10)
    net = None if fee_per_contract is None else round(gross - fee_per_contract, 10)
    # The size an execution could actually take is the smaller side: a 500-contract bid is
    # no help against a 3-contract ask.
    sizes = [s for s in (buy_size, sell_size) if s is not None]
    size_at_touch = min(sizes) if len(sizes) == 2 else None
    return {
        "direction": BUY_YES_KALSHI if buy_venue == KALSHI else BUY_YES_POLYMARKET,
        "buy": Leg(buy_venue, "BUY_YES", buy_price, buy_size).as_dict(),
        # Selling YES is buying NO; named as sell because that is what the YES book shows.
        "sell": Leg(sell_venue, "SELL_YES", sell_price, sell_size).as_dict(),
        "gross_edge": gross,
        "fees": fees,
        "fee_per_contract": fee_per_contract,
        "net_edge": net,
        "size_at_touch": size_at_touch,
        "is_opportunity": bool(net is not None and net > 0.0),
    }


def evaluate_pair(
    kalshi: PredMarket,
    polymarket: PredMarket,
    *,
    contracts: float = 1.0,
    now: str,
    pair_id: str | None = None,
) -> dict[str, Any]:
    """One observation of one confirmed pair. Pure: no I/O, no venue, no order.

    Both directions are computed and the better *net* one is reported as ``best``. Better by
    net rather than gross on purpose: the two directions can pay different fees, because the
    fee depends on each leg's own price, so the wider gross gap is not always the one worth
    having.
    """
    k_quote: VenueQuote = kalshi.quote
    p_quote: VenueQuote = polymarket.quote

    reasons: list[str] = []
    if not (k_quote.quoted() and p_quote.quoted()):
        reasons.append(NOT_QUOTED)

    directions = [
        _direction(
            buy_venue=KALSHI, buy_price=k_quote.yes_ask, buy_size=k_quote.yes_ask_size,
            sell_venue=POLYMARKET, sell_price=p_quote.yes_bid, sell_size=p_quote.yes_bid_size,
            polymarket_category=polymarket.category, contracts=contracts,
        ),
        _direction(
            buy_venue=POLYMARKET, buy_price=p_quote.yes_ask, buy_size=p_quote.yes_ask_size,
            sell_venue=KALSHI, sell_price=k_quote.yes_bid, sell_size=k_quote.yes_bid_size,
            polymarket_category=polymarket.category, contracts=contracts,
        ),
    ]
    priced = [d for d in directions if d is not None and d["net_edge"] is not None]
    best = max(priced, key=lambda d: d["net_edge"]) if priced else None
    if best is not None and best["size_at_touch"] is None:
        # Priced but with no depth on one side: an edge nobody could take any of.
        reasons.append(NO_SIZE)

    record = {
        "opportunity_version": OPPORTUNITY_VERSION,
        "pair_id": pair_id,
        "kalshi_market_id": kalshi.market_id,
        "polymarket_market_id": polymarket.market_id,
        "kalshi_quote": k_quote.as_dict(),
        "polymarket_quote": p_quote.as_dict(),
        "polymarket_category": polymarket.category,
        "contracts": contracts,
        "directions": [d for d in directions if d is not None],
        "best": best,
        # The three numbers a report is built from, hoisted for readability.
        "gross_edge": best["gross_edge"] if best else None,
        "net_edge": best["net_edge"] if best else None,
        "is_opportunity": bool(best and best["is_opportunity"]),
        "reasons": reasons,
        "observed_at_utc": now,
        # Stated on every record: observing is not acting, and this phase has no order path.
        "authorizes_trading": False,
    }
    record["observation_id"] = integrity.short_id(
        "predmarket_observation",
        {"pair": str(pair_id), "k": kalshi.market_id, "p": polymarket.market_id, "at": now},
    )
    return record


def observation_status_line(record: Mapping[str, Any]) -> str:
    """One ASCII line for the console (Windows consoles are cp949)."""
    if record.get("reasons"):
        return (
            f"{record.get('kalshi_market_id')} <-> {record.get('polymarket_market_id')}: "
            f"no reading ({','.join(record['reasons'])})"
        )
    best = record.get("best") or {}
    verdict = "OPPORTUNITY" if record.get("is_opportunity") else "no edge"
    return (
        f"{record.get('kalshi_market_id')} <-> {record.get('polymarket_market_id')}: {verdict} "
        f"gross={record.get('gross_edge')} net={record.get('net_edge')} "
        f"size={best.get('size_at_touch')} [{best.get('direction')}]"
    )


__all__ = [
    "BUY_YES_KALSHI",
    "BUY_YES_POLYMARKET",
    "NOT_QUOTED",
    "NO_SIZE",
    "OPPORTUNITY_VERSION",
    "Leg",
    "evaluate_pair",
    "observation_status_line",
]
