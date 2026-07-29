"""PM1 — is the gap between two venues real once it is paid for? Read-only; decides nothing.

The observation half of the track. Given one operator-confirmed event group and its venues'
current quotes, this computes what a market-neutral cross-venue position would actually earn, net of
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
from .market_data import PredMarket, VenueQuote

OPPORTUNITY_VERSION = "predmarket_opportunity.v0.1"

def direction_name(buy_venue: str, sell_venue: str) -> str:
    """The direction, named from where the YES leg is bought. Derived rather than enumerated,
    so a third venue needs no new constant."""
    return f"BUY_YES_{buy_venue.upper()}_SELL_YES_{sell_venue.upper()}"

# Why no number could be produced. An observation with a reason is still worth recording:
# "we looked and could not see" is different from "we did not look", and a report that
# silently omits unquoted scans overstates how often the pair was observable.
NOT_QUOTED = "NOT_QUOTED"
NO_SIZE = "NO_SIZE_AT_TOUCH"


def _has_depth(size: float | None) -> bool:
    """Whether there is anything at the touch to trade against.

    A reported ``0`` is as untradeable as no report at all, and the venue is more certain of
    it. Booleans are excluded on principle — ``True`` is not one contract.
    """
    return isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0


# An edge so large it refutes its own premise. Two venues quoting ONE question do not sit a
# quarter of a dollar apart; if they appear to, they are not quoting one question — whatever
# their titles and settlement text say.
IMPLAUSIBLE_EDGE = "IMPLAUSIBLE_EDGE"

# Where that line sits, and it is a policy choice inside an empty band rather than a measured
# boundary. Live on 2026-07-28, the confirmed pairings separated cleanly:
#
#     genuine readings   |net| <= 0.035   (fees alone are ~0.025/contract, so this is the
#                                          scale the whole phase operates at)
#     broken pairings    |net| >= 0.699   Binance quoting a July-FOMC market at 0.73 against
#                                         Polymarket's 0.0015 on $2.56M of liquidity; and
#                                         0.82 against 0.003 on a market Binance had traded
#                                         $7.17 of, with no bids at all
#
# Nothing observed lives between 0.035 and 0.699. The threshold sits in that gap, far above
# anything real and far below every artifact — and it FLAGS rather than drops: the row keeps
# its numbers and its reason, because a pairing that produces one of these is evidence about
# the pairing, and deleting it would delete the evidence.
MAX_PLAUSIBLE_NET_EDGE = 0.25


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
    buy_category: Any,
    sell_category: Any,
    buy_fee_bps: Any,
    sell_fee_bps: Any,
    contracts: float,
) -> dict[str, Any] | None:
    """One direction's economics, or ``None`` when either leg is unquoted."""
    if buy_price is None or sell_price is None:
        return None
    gross = round(sell_price - buy_price, 10)
    fees = round_trip_fee(
        [
            {"venue": buy_venue, "price": buy_price, "category": buy_category,
             "fee_rate_bps": buy_fee_bps},
            {"venue": sell_venue, "price": sell_price, "category": sell_category,
             "fee_rate_bps": sell_fee_bps},
        ],
        contracts=contracts,
    )
    total_fee = fees["total_fee_usd"]
    # Per contract, so gross and net are comparable regardless of the size at the touch.
    fee_per_contract = None if total_fee is None else round(total_fee / contracts, 10)
    net = None if fee_per_contract is None else round(gross - fee_per_contract, 10)
    # The size an execution could actually take is the smaller side: a 500-contract bid is
    # no help against a 3-contract ask.
    sizes = [s for s in (buy_size, sell_size) if s is not None]
    size_at_touch = min(sizes) if len(sizes) == 2 else None
    # Kept as the venue reported it, including a reported zero: the record states what was
    # seen, and `evaluate_pairing` decides what it means. Rewriting 0 to None here would
    # erase the distinction between a venue that said "none" and one that said nothing.
    return {
        "direction": direction_name(buy_venue, sell_venue),
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


def evaluate_pairing(
    left: PredMarket,
    right: PredMarket,
    *,
    contracts: float = 1.0,
    now: str,
    event_id: str | None = None,
) -> dict[str, Any]:
    """One observation of one leg pairing inside a confirmed event group. Pure: no I/O.

    Venue-agnostic by construction: the two markets are ``left`` and ``right``, not "the
    Kalshi one" and "the Polymarket one", so a third venue is compared by the same code that
    compares the first two. ``pairs.pairings_of`` enumerates which pairings exist; this
    prices one of them.

    Both directions are computed and the better *net* one is reported as ``best``. Better by
    net rather than gross on purpose: the two directions can pay different fees, because each
    leg's fee depends on its own price, so the wider gross gap is not always the one worth
    having.
    """
    left_quote: VenueQuote = left.quote
    right_quote: VenueQuote = right.quote

    reasons: list[str] = []
    if not (left_quote.quoted() and right_quote.quoted()):
        reasons.append(NOT_QUOTED)

    directions = [
        _direction(
            buy_venue=left.venue, buy_price=left_quote.yes_ask, buy_size=left_quote.yes_ask_size,
            sell_venue=right.venue, sell_price=right_quote.yes_bid, sell_size=right_quote.yes_bid_size,
            buy_category=left.category, sell_category=right.category,
            buy_fee_bps=left.fee_rate_bps, sell_fee_bps=right.fee_rate_bps, contracts=contracts,
        ),
        _direction(
            buy_venue=right.venue, buy_price=right_quote.yes_ask, buy_size=right_quote.yes_ask_size,
            sell_venue=left.venue, sell_price=left_quote.yes_bid, sell_size=left_quote.yes_bid_size,
            buy_category=right.category, sell_category=left.category,
            buy_fee_bps=right.fee_rate_bps, sell_fee_bps=left.fee_rate_bps, contracts=contracts,
        ),
    ]
    priced = [d for d in directions if d is not None and d["net_edge"] is not None]
    best = max(priced, key=lambda d: d["net_edge"]) if priced else None
    if best is not None and not _has_depth(best["size_at_touch"]):
        # Priced but with no depth on one side: an edge nobody could take any of.
        #
        # `None` and `0` are different facts — did not say, versus said none — and both mean
        # the same thing here, which is why they share a reason. What they must NOT share is
        # silence: a venue reporting size 0 used to produce no reason at all, because the
        # check asked only about `None`. Seen live on 2026-07-28, a 0.09c "opportunity" on a
        # touch of zero contracts, sitting in the count next to real ones.
        reasons.append(NO_SIZE)
    implausible = best is not None and abs(best["net_edge"]) >= MAX_PLAUSIBLE_NET_EDGE
    if implausible:
        # Not a find. A gap this wide between two venues that publish the same question means
        # one of them is not quoting that question — a stale book, a mirrored listing nobody
        # trades, a market about a different instance. Confirmed live twice on 2026-07-28,
        # both at the top of the report, both looking like the best thing in it.
        reasons.append(IMPLAUSIBLE_EDGE)

    legs = [
        {"venue": left.venue, "market_id": left.market_id, "category": left.category,
         "quote": left_quote.as_dict()},
        {"venue": right.venue, "market_id": right.market_id, "category": right.category,
         "quote": right_quote.as_dict()},
    ]
    record = {
        "opportunity_version": OPPORTUNITY_VERSION,
        "event_id": event_id,
        "legs": legs,
        "contracts": contracts,
        "directions": [d for d in directions if d is not None],
        "best": best,
        # The three numbers a report is built from, hoisted for readability.
        "gross_edge": best["gross_edge"] if best else None,
        "net_edge": best["net_edge"] if best else None,
        # An implausible edge, or one computed off a half-open book, is never an opportunity —
        # however positive its arithmetic. The numbers stay on the row; only the claim is
        # withdrawn, which is the treatment `IMPLAUSIBLE_EDGE` already established.
        #
        # `NOT_QUOTED` joined it on 2026-07-28. A venue quoting one side still lets ONE
        # direction be computed — buy where there is an ask, sell where there is a bid — so the
        # arithmetic is valid and the row should keep it. But a half-open book is where the thin
        # side lives: observed live the same day, a Binance leg quoting `ask=0.10 size=100`
        # against a Polymarket bid with size 54,972. That direction happened to be a loss, so
        # nothing was miscounted; with the signs reversed it would have been counted as an
        # opportunity, and **"how often" is the number PM1 exists to produce.** Overstating the
        # frequency is the direction that later misleads PM2 and PM3.
        # `NO_SIZE` joined them on 2026-07-29, having been recorded but never acted on since
        # it was introduced. The comment where it is raised already called it "an edge nobody
        # could take any of" — and the record then went on to call it an opportunity anyway.
        # Frequency is the number PM1 exists to produce, so a touch nobody could trade must
        # not be in it.
        "is_opportunity": bool(
            best and best["is_opportunity"] and not implausible
            and NOT_QUOTED not in reasons and NO_SIZE not in reasons
        ),
        "reasons": reasons,
        "observed_at_utc": now,
        # Stated on every record: observing is not acting, and this phase has no order path.
        "authorizes_trading": False,
    }
    record["observation_id"] = integrity.short_id(
        "predmarket_observation",
        {"event": str(event_id),
         "legs": [f"{leg['venue']}:{leg['market_id']}" for leg in legs],
         "at": now},
    )
    return record


def evaluate_group(
    group: Mapping[str, Any],
    markets_by_key: Mapping[str, PredMarket],
    *,
    contracts: float = 1.0,
    now: str,
) -> list[dict[str, Any]]:
    """Every pairing inside one confirmed group, priced. ``markets_by_key`` is keyed
    ``"venue:market_id"``.

    This is what grouping bought: one operator confirmation yields ``n(n-1)/2`` observations,
    and a venue added later contributes its pairings without another confirmation. A leg with
    no current market (that venue's scan degraded) is skipped here rather than compared
    against nothing — the pairings that *were* readable still get recorded.
    """
    from .pairs import pairings_of  # local: pairs imports nothing from here, keep it that way

    observations: list[dict[str, Any]] = []
    for left_leg, right_leg in pairings_of(group):
        left = markets_by_key.get(f"{left_leg['venue']}:{left_leg['market_id']}")
        right = markets_by_key.get(f"{right_leg['venue']}:{right_leg['market_id']}")
        if left is None or right is None:
            continue
        observations.append(evaluate_pairing(
            left, right, contracts=contracts, now=now, event_id=group.get("event_id")
        ))
    return observations


def observation_status_line(record: Mapping[str, Any]) -> str:
    """One ASCII line for the console (Windows consoles are cp949)."""
    legs = " <-> ".join(
        f"{leg.get('venue')}:{leg.get('market_id')}" for leg in record.get("legs") or []
    )
    if record.get("reasons"):
        return f"{legs}: no reading ({','.join(record['reasons'])})"
    best = record.get("best") or {}
    verdict = "OPPORTUNITY" if record.get("is_opportunity") else "no edge"
    return (
        f"{legs}: {verdict} gross={record.get('gross_edge')} net={record.get('net_edge')} "
        f"size={best.get('size_at_touch')} [{best.get('direction')}]"
    )


__all__ = [
    "IMPLAUSIBLE_EDGE",
    "MAX_PLAUSIBLE_NET_EDGE",
    "NOT_QUOTED",
    "NO_SIZE",
    "OPPORTUNITY_VERSION",
    "Leg",
    "direction_name",
    "evaluate_group",
    "evaluate_pairing",
    "observation_status_line",
]
