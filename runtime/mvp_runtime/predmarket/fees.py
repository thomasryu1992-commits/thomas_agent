"""PM1 — what a round trip actually costs. Verified against both venues, 2026-07-26.

The roadmap's own invariant: *every observation is fee-adjusted or it is not recorded.* This
module is why that is enforceable, and it exists as its own file because the constants below
are the difference between a report about arbitrage and a report about nothing.

**Both venues charge the same shape**, which is the useful discovery:

    fee = rate x contracts x P x (1 - P)

- **Kalshi** (taker): ``roundup(M x 0.07 x C x P x (1-P))``, M defaulting to 1, rounded **up**
  to a centicent. Maker orders use 0.0175 with M defaulting to 0 — i.e. normally free.
- **Polymarket** (taker): ``C x feeRate x p x (1-p)`` charged on the shares traded, with
  ``feeRate`` **by category**: crypto 0.07, sports/economics/culture/weather/other 0.05,
  finance/politics/mentions/tech 0.04. **Geopolitical and world-event markets are fee-free.**
  Makers are never charged.

**The roadmap was out of date and this corrects it.** It modelled Polymarket's cost as "gas +
spread", from a period when the venue charged no trading fee. It does now, and at the prices
that matter most: the ``P x (1-P)`` shape peaks at 50/50, which is exactly where two venues
disagree often enough to look like an opportunity. Concretely, a mid-priced Kalshi/crypto pair
costs about **3 cents per contract** in fees alone (0.07x0.25 + 0.07x0.25), so a 2-cent gross
gap is not a thin edge — it is a loss. An unadjusted observation would report it as a find,
every scan, for weeks.

**Rounding is pessimistic on both legs.** Kalshi documents rounding up; Polymarket does not,
but an observation that rounds a cost down is a claim the trade was cheaper than it was, and
this whole phase exists to decide whether an edge survives its costs. Where the venues differ,
the more expensive reading wins.

**Fees are taker-only here, by construction.** Cross-venue arbitrage crosses the spread on
both legs — that is what makes it immediate — so the maker rates are recorded for completeness
and never used for an opportunity estimate. Assuming a maker fill would be assuming a fill
that may never come.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ..coerce import as_optional_float
from .market_data import BINANCE, KALSHI, POLYMARKET, require_venue

FEES_VERSION = "predmarket_fees.v0.1"
FEES_VERIFIED_ON = "2026-07-26"

# --- Kalshi (docs.kalshi.com fee schedule / fee rounding) -----------------------
KALSHI_TAKER_RATE = 0.07
KALSHI_MAKER_RATE = 0.0175          # recorded, unused: an arb leg is a taker
# "rounded up such that fee + positionCost lands on a centicent" — 1/100 of a cent.
KALSHI_ROUNDING_UNIT = 0.0001

# --- Polymarket (docs.polymarket.com/trading/fees) ------------------------------
# Derived from and cross-checked against the venue's own peak table: crypto $1.75 per 100
# shares at p=0.50 implies 100 x rate x 0.25 = 1.75, i.e. rate 0.07. The other tiers check
# out the same way ($1.25 -> 0.05, $1.00 -> 0.04), so the rates below are not a guess.
POLYMARKET_TAKER_RATES: dict[str, float] = {
    "crypto": 0.07,
    "sports": 0.05,
    "economics": 0.05,
    "culture": 0.05,
    "weather": 0.05,
    "other": 0.05,
    "finance": 0.04,
    "politics": 0.04,
    "mentions": 0.04,
    "tech": 0.04,
}
# The venue's own default for a category this map does not name. The HIGHEST rate on purpose:
# an unknown category must not be quoted the cheapest fee, or a new category silently makes
# every observation optimistic.
POLYMARKET_DEFAULT_TAKER_RATE = 0.07
# Fee-free by venue policy. Kept as data rather than folded into the rate map so the reason
# ("this category is exempt") stays distinguishable from "this category costs 0.0".
POLYMARKET_FEE_FREE_CATEGORIES = frozenset({"geopolitics", "world", "world events", "geopolitical"})

# Polymarket publishes no rounding rule; we round up like Kalshi, because an observation that
# rounds a cost down claims the trade was cheaper than it was.
POLYMARKET_ROUNDING_UNIT = 0.0001

# What Binance was observed publishing as `feeRateBps` — 200 bps on 199 of 200 topics on
# 2026-07-27, the remaining topic stating none. Used only when the venue's own rate is not in
# hand, which on a watch scan it never is. See the note beside its `_VENUE_FEES` row.
BINANCE_OBSERVED_TAKER_RATE = 0.02


def _round_up(amount: float, unit: float) -> float:
    """Round ``amount`` up to the next ``unit``. Pessimistic by construction.

    ``steps`` is quantized before the ceiling because rounding up amplifies float
    representation error into a real charge. ``0.07 x 100 x 0.1 x 0.9`` evaluates to
    ``0.6300000000000001`` while the same product with the factors swapped is exactly
    ``0.63`` — without the quantization the first is charged one extra centicent, which
    breaks the ``P x (1-P)`` symmetry both venues' formulas guarantee and would make a fee
    depend on which side of the book a caller happened to name. Caught by this module's own
    symmetry test.
    """
    if unit <= 0:
        return amount
    steps = round(amount / unit, 9)
    whole = math.ceil(steps)
    return round(whole * unit, 10)


def polymarket_taker_rate(category: Any) -> float:
    """The taker rate for one Polymarket category, or the most expensive one if unknown.

    Unknown is priced at the top of the table rather than the middle or the bottom: the cost
    of over-estimating a fee is a missed observation, and the cost of under-estimating it is
    a reported opportunity that does not exist.
    """
    if not isinstance(category, str) or not category.strip():
        return POLYMARKET_DEFAULT_TAKER_RATE
    key = category.strip().lower()
    if key in POLYMARKET_FEE_FREE_CATEGORIES:
        return 0.0
    return POLYMARKET_TAKER_RATES.get(key, POLYMARKET_DEFAULT_TAKER_RATE)


# One row per venue, so a third venue is a data addition rather than another branch. Every
# row is `rate x contracts x P x (1-P)` rounded up; venues differ only in the rate (flat or
# per category) and the rounding unit.
#
# **A venue with no row here has no knowable cost, and says so** — `taker_fee` returns None,
# every pairing involving it reports `net_edge: None`, and the fix is a verified row rather
# than a plausible number.
#
# Predict.fun has no row and does not need one: **the venue reports its own rate per market**
# (`feeRateBps` on Binance's prediction topics), which beats any table. A leg may therefore
# carry `fee_rate_bps` and it wins over the venue schedule — a rate the venue stated about
# THIS market is better evidence than a constant about the venue.
_VENUE_FEES: dict[str, dict[str, Any]] = {
    KALSHI: {
        "rate_of": lambda _category: KALSHI_TAKER_RATE,
        "rounding_unit": KALSHI_ROUNDING_UNIT,
    },
    POLYMARKET: {
        "rate_of": lambda category: polymarket_taker_rate(category),
        "rounding_unit": POLYMARKET_ROUNDING_UNIT,
    },
    # A fallback, and only ever that: every path that can see the venue's own `feeRateBps`
    # uses it instead (the stated rate is applied above, before this table is consulted).
    #
    # It exists because a watch scan CANNOT see it. A confirmed leg is `marketId:tokenId`,
    # `market/detail` is keyed by `marketTopicId`, and the order-book response carries no fee
    # at all — checked 2026-07-27. Without a fallback every observation of a Binance leg came
    # back with `net_edge: None`: the pairing had real books on both sides, a computed gross
    # edge, and was still filed as unreadable, forever. That is not caution, it is a venue
    # this phase can never collect data on.
    #
    # The rate is what the venue was observed publishing: 200 bps on 199 of 200 topics, the
    # remaining one stating none. An observation, not a published schedule — so it is applied
    # with the already-pessimistic flat model and every record says `fee_schedule_verified:
    # false`. A market charging more than 200 bps would be under-costed here; that is the one
    # direction this module dislikes, and it is why the stated rate always wins when there is
    # one, and why this number is written down rather than inferred at each call site.
    BINANCE: {
        "rate_of": lambda _category: BINANCE_OBSERVED_TAKER_RATE,
        "rounding_unit": POLYMARKET_ROUNDING_UNIT,
        # Both flags are read: `flat` picks the pessimistic formula, `verified` keeps the
        # record honest about where the number came from.
        "flat": True,
        "verified": False,
    },
}


# How a venue-reported bps rate is applied. Binance publishes `feeRateBps` per market but not
# the formula, and the two candidates differ: `rate x P x (1-P)` (the Kalshi/Polymarket shape)
# or a flat `rate x P` of notional. Flat is larger at every price, so it is the one used until
# the formula is confirmed — over-estimating a cost skips an observation, under-estimating
# reports an opportunity that is not there. Recorded as `fee_model` so it is visible and
# correctable rather than assumed silently.
FEE_MODEL_SHAPED = "rate*P*(1-P)"
FEE_MODEL_FLAT_ASSUMED = "assumed_flat_rate*P_of_notional"


def taker_fee(
    venue: str,
    *,
    price: Any,
    contracts: Any = 1.0,
    category: Any = None,
    fee_rate_bps: Any = None,
) -> float | None:
    """The taker fee for ``contracts`` at ``price`` on ``venue``, or ``None``.

    ``None`` when the price is not a usable probability — an unpriceable leg has no knowable
    cost, and returning 0.0 would make it look free. That distinction is the whole reason
    prices are ``float | None`` upstream.

    The fee is symmetric in ``P``: buying NO at ``1-p`` costs the same as buying YES at ``p``,
    because ``P x (1-P)`` is unchanged. So a caller never has to say which side it took.
    """
    venue = require_venue(venue)
    p = as_optional_float(price)
    size = as_optional_float(contracts)
    if p is None or size is None or not (0.0 < p < 1.0) or size <= 0:
        return None

    stated = as_optional_float(fee_rate_bps)
    if stated is not None and stated >= 0:
        # The venue said what this market costs. Applied flat (the pessimistic reading) until
        # the formula is confirmed — see FEE_MODEL_FLAT_ASSUMED.
        if stated == 0:
            return 0.0
        return _round_up((stated / 10_000.0) * size * p, POLYMARKET_ROUNDING_UNIT)

    schedule = _VENUE_FEES.get(venue)
    if schedule is None:
        return None  # no stated rate and no schedule — see _VENUE_FEES
    rate = schedule["rate_of"](category)
    if rate <= 0.0:
        return 0.0
    if schedule.get("flat"):
        # An unverified schedule is applied flat, exactly as an unverified STATED rate is.
        # Anything else lets the fallback come out cheaper than the number it stands in for:
        # shaped costs `rate x P x (1-P)`, flat costs `rate x P`, and `(1-P) < 1` always. A
        # fallback that under-charges the case it replaces is worse than no fallback, because
        # it turns "we could not price this" into "this looks profitable".
        return _round_up(rate * size * p, schedule["rounding_unit"])
    return _round_up(rate * size * p * (1.0 - p), schedule["rounding_unit"])


def round_trip_fee(
    legs: Sequence[Mapping[str, Any]], *, contracts: Any = 1.0
) -> dict[str, Any]:
    """Every leg of one cross-venue position. Returns the per-leg parts and the total.

    ``legs`` is ``[{"venue": ..., "price": ..., "category": ...}, ...]`` — venue-agnostic on
    purpose, so a third venue costs a row in ``_VENUE_FEES`` and nothing here.

    A cross-venue position pays a taker fee on **every** leg; charging one would understate the
    cost by roughly half. If any leg cannot be priced, ``total_fee_usd`` is ``None`` — the
    position's cost is unknown, and an unknown cost is not a zero cost.
    """
    priced: list[dict[str, Any]] = []
    total: float | None = 0.0
    for leg in legs:
        venue = require_venue((leg or {}).get("venue"))
        category = leg.get("category")
        schedule = _VENUE_FEES.get(venue)
        stated_bps = as_optional_float(leg.get("fee_rate_bps"))
        fee = taker_fee(
            venue, price=leg.get("price"), contracts=contracts, category=category,
            fee_rate_bps=leg.get("fee_rate_bps"),
        )
        priced.append({
            "venue": venue,
            "price": as_optional_float(leg.get("price")),
            "fee_rate_bps": stated_bps,
            "fee_model": (
                FEE_MODEL_FLAT_ASSUMED
                if stated_bps is not None or (schedule is not None and schedule.get("flat"))
                else FEE_MODEL_SHAPED
            ),
            # None, not 0.0: "we have not read this venue's fee schedule" is a different fact
            # from "this leg is free", and only the second one is ever good news.
            "taker_rate": schedule["rate_of"](category) if schedule is not None else None,
            # A stated rate is its own verification: the venue told us about this market.
            # A schedule that marks itself unverified is NOT — it is a rate this repo observed
            # the venue publishing, not one the venue documented, and a record that called it
            # verified would launder the difference.
            "fee_schedule_verified": bool(
                stated_bps is not None
                or (schedule is not None and schedule.get("verified", True))
            ),
            "fee_usd": fee,
        })
        if fee is None or total is None:
            total = None
        else:
            total = round(total + fee, 10)
    return {
        "fees_version": FEES_VERSION,
        "legs": priced,
        "contracts": as_optional_float(contracts),
        "total_fee_usd": total,
    }


def fee_summary() -> dict[str, Any]:
    """The constants and where they came from — so a record can carry its own provenance."""
    return {
        "fees_version": FEES_VERSION,
        "verified_on": FEES_VERIFIED_ON,
        "kalshi_taker_rate": KALSHI_TAKER_RATE,
        "kalshi_maker_rate_unused": KALSHI_MAKER_RATE,
        "polymarket_taker_rates": dict(POLYMARKET_TAKER_RATES),
        "polymarket_default_taker_rate": POLYMARKET_DEFAULT_TAKER_RATE,
        "polymarket_fee_free_categories": sorted(POLYMARKET_FEE_FREE_CATEGORIES),
        "venues_with_a_verified_schedule": sorted(_VENUE_FEES),
        "shape": "rate * contracts * P * (1 - P), rounded up",
        "taker_only": True,
    }


__all__ = [
    "FEE_MODEL_FLAT_ASSUMED",
    "FEE_MODEL_SHAPED",
    "FEES_VERIFIED_ON",
    "FEES_VERSION",
    "KALSHI_MAKER_RATE",
    "KALSHI_TAKER_RATE",
    "POLYMARKET_DEFAULT_TAKER_RATE",
    "POLYMARKET_FEE_FREE_CATEGORIES",
    "POLYMARKET_TAKER_RATES",
    "fee_summary",
    "polymarket_taker_rate",
    "round_trip_fee",
    "taker_fee",
]
