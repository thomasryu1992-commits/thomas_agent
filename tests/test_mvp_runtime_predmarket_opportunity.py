"""PM1 fees and the opportunity detector — the arithmetic that decides what gets reported.

The roadmap's invariant is that every observation is fee-adjusted or it is not recorded, and
these tests are what makes that more than a sentence. The number that matters: both venues
charge ``rate x P x (1-P)``, which **peaks at 50/50** — exactly where two venues disagree
often enough to look like an opportunity. So the first test is the one that would have caught
an unadjusted detector: a 2-cent gross gap at mid price is a **loss**, and must be reported
as one.

Fee constants were verified against both venues' own fee documentation on 2026-07-26,
including the correction that Polymarket now charges taker fees by category — the roadmap
had modelled its cost as gas and spread only.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.errors import ToolBlocked
from runtime.mvp_runtime.predmarket import fees, opportunity
from runtime.mvp_runtime.predmarket.market_data import KALSHI, POLYMARKET, PredMarket, VenueQuote

NOW = "2026-07-26T12:00:00Z"


def _market(venue, *, bid=None, ask=None, bid_size=100.0, ask_size=100.0, category=None):
    return PredMarket(
        venue=venue,
        market_id=f"{venue}-1",
        group_id=None,
        title="Will the Fed cut rates in December?",
        close_time="2026-12-31T23:59:00Z",
        status="active",
        category=category,
        quote=VenueQuote(yes_bid=bid, yes_ask=ask, yes_bid_size=bid_size, yes_ask_size=ask_size),
    )


# --- the fee model --------------------------------------------------------------

def test_the_kalshi_fee_matches_the_published_formula():
    """roundup(0.07 x C x P x (1-P)). At 50/50 that is $1.75 per 100 contracts."""
    assert fees.taker_fee(KALSHI, price=0.5, contracts=100) == pytest.approx(1.75, abs=1e-4)
    # Symmetric in P, and smaller toward the extremes where P(1-P) collapses.
    assert fees.taker_fee(KALSHI, price=0.1, contracts=100) == fees.taker_fee(KALSHI, price=0.9, contracts=100)
    assert fees.taker_fee(KALSHI, price=0.1, contracts=100) < fees.taker_fee(KALSHI, price=0.5, contracts=100)


@pytest.mark.parametrize("category,peak_per_100", [
    ("crypto", 1.75), ("sports", 1.25), ("economics", 1.25), ("politics", 1.00), ("tech", 1.00),
])
def test_the_polymarket_rates_reproduce_the_venues_own_peak_table(category, peak_per_100):
    """The rates were derived from the venue's published peaks rather than guessed, so they
    have to reproduce them: crypto $1.75 / sports $1.25 / politics $1.00 per 100 shares at
    p = 0.50."""
    assert fees.taker_fee(POLYMARKET, price=0.5, contracts=100, category=category) == pytest.approx(
        peak_per_100, abs=1e-4
    )


def test_a_fee_free_category_is_free_and_an_unknown_one_is_expensive():
    """Unknown is priced at the TOP of the table. Over-estimating costs a missed observation;
    under-estimating reports an opportunity that does not exist."""
    assert fees.taker_fee(POLYMARKET, price=0.5, contracts=100, category="geopolitics") == 0.0
    unknown = fees.taker_fee(POLYMARKET, price=0.5, contracts=100, category="brand new category")
    assert unknown == fees.taker_fee(POLYMARKET, price=0.5, contracts=100, category="crypto")
    # A missing category is unknown too, not free.
    assert fees.taker_fee(POLYMARKET, price=0.5, contracts=100, category=None) == unknown


def test_the_fee_is_the_same_whichever_side_is_bought():
    """P(1-P) is symmetric, so buying NO at 1-p costs what buying YES at p costs. A caller
    never has to declare its side, and cannot get it wrong."""
    assert fees.taker_fee(KALSHI, price=0.62, contracts=10) == fees.taker_fee(KALSHI, price=0.38, contracts=10)


@pytest.mark.parametrize("price", [None, 0.0, 1.0, 1.5, -0.2, "", "abc"])
def test_an_unpriceable_leg_has_no_knowable_fee(price):
    """None, never 0.0: an unpriceable leg is not a free one, and reporting it as free is how
    a fake edge survives the cost model."""
    assert fees.taker_fee(KALSHI, price=price, contracts=1) is None
    assert fees.round_trip_fee(
        [{"venue": KALSHI, "price": price}, {"venue": POLYMARKET, "price": 0.5}]
    )["total_fee_usd"] is None


def test_rounding_is_up_on_both_venues():
    """Kalshi documents rounding up; Polymarket documents no rule. Rounding a cost DOWN
    would claim the trade was cheaper than it was, in the one phase built to decide whether
    an edge survives its costs."""
    tiny = fees.taker_fee(KALSHI, price=0.5, contracts=0.001)
    assert tiny == fees.KALSHI_ROUNDING_UNIT  # 0.0000175 rounded up to a centicent
    assert fees.taker_fee(POLYMARKET, price=0.5, contracts=0.001, category="crypto") > 0.0


def test_a_round_trip_charges_both_venues():
    """Cross-venue arbitrage crosses the spread on both legs. Charging one would understate
    the cost by roughly half."""
    both = fees.round_trip_fee(
        [{"venue": KALSHI, "price": 0.5},
         {"venue": POLYMARKET, "price": 0.5, "category": "crypto"}],
        contracts=100,
    )
    assert [leg["fee_usd"] for leg in both["legs"]] == [pytest.approx(1.75, abs=1e-4)] * 2
    assert both["total_fee_usd"] == pytest.approx(3.50, abs=1e-4)


def test_an_unknown_venue_is_refused():
    """A venue nobody added is refused rather than priced. `binance` is a known venue now, so
    the case is pinned on one that is not."""
    with pytest.raises(ToolBlocked):
        fees.taker_fee("bybit", price=0.5)


# --- the detector ---------------------------------------------------------------

def test_a_two_cent_gap_at_mid_price_is_a_loss_not_a_find():
    """THE test. Kalshi asks 0.50, Polymarket bids 0.52 — a 2-cent gross gap that an
    unadjusted detector reports as an opportunity, every scan, for weeks. The two legs cost
    about 3.5 cents per contract at mid price, so it is a loss."""
    record = opportunity.evaluate_pairing(
        _market(KALSHI, bid=0.48, ask=0.50),
        _market(POLYMARKET, bid=0.52, ask=0.54, category="crypto"),
        now=NOW,
    )
    assert record["gross_edge"] == pytest.approx(0.02)
    assert record["net_edge"] < 0.0
    assert record["is_opportunity"] is False


def test_a_gap_wide_enough_to_pay_for_itself_is_reported():
    """The same shape with a 10-cent gap clears ~3.5 cents of fees and survives."""
    record = opportunity.evaluate_pairing(
        _market(KALSHI, bid=0.43, ask=0.45),
        _market(POLYMARKET, bid=0.55, ask=0.57, category="crypto"),
        now=NOW,
    )
    assert record["is_opportunity"] is True
    assert record["net_edge"] == pytest.approx(record["gross_edge"] - 0.035, abs=1e-3)
    assert record["best"]["direction"] == opportunity.direction_name(KALSHI, POLYMARKET)


def test_a_fee_free_category_keeps_the_whole_gross_on_its_leg():
    """Geopolitical markets are fee-free on Polymarket, so only the Kalshi leg costs — which
    changes which pairs are worth watching at all."""
    charged = opportunity.evaluate_pairing(
        _market(KALSHI, bid=0.43, ask=0.45), _market(POLYMARKET, bid=0.55, ask=0.57, category="crypto"),
        now=NOW,
    )
    free = opportunity.evaluate_pairing(
        _market(KALSHI, bid=0.43, ask=0.45), _market(POLYMARKET, bid=0.55, ask=0.57, category="geopolitics"),
        now=NOW,
    )
    assert free["net_edge"] > charged["net_edge"]
    poly_leg = next(l for l in free["best"]["fees"]["legs"] if l["venue"] == POLYMARKET)
    assert poly_leg["fee_usd"] == 0.0


def test_both_directions_are_evaluated_and_the_better_NET_one_wins():
    """The two directions can pay different fees, because each leg's fee depends on its own
    price — so the wider gross gap is not always the one worth having."""
    record = opportunity.evaluate_pairing(
        _market(KALSHI, bid=0.60, ask=0.62),
        _market(POLYMARKET, bid=0.40, ask=0.42, category="politics"),
        now=NOW,
    )
    assert len(record["directions"]) == 2
    assert record["best"]["direction"] == opportunity.direction_name(POLYMARKET, KALSHI)
    nets = [d["net_edge"] for d in record["directions"]]
    assert record["net_edge"] == max(nets)


def test_an_unquoted_side_produces_a_recorded_non_reading_not_a_zero():
    """'We looked and could not see' is different from 'we did not look'. A report that
    silently omitted unquoted scans would overstate how often the pair was observable."""
    record = opportunity.evaluate_pairing(
        _market(KALSHI, bid=None, ask=None),
        _market(POLYMARKET, bid=0.52, ask=0.54, category="crypto"),
        now=NOW,
    )
    assert opportunity.NOT_QUOTED in record["reasons"]
    assert record["net_edge"] is None and record["is_opportunity"] is False
    assert record["observation_id"]


def test_depth_is_recorded_and_the_smaller_side_binds():
    """A 500-contract bid is no help against a 3-contract ask. PM2 models the fill; PM1 has
    to hand it an honest number."""
    record = opportunity.evaluate_pairing(
        _market(KALSHI, bid=0.43, ask=0.45, ask_size=3.0),
        _market(POLYMARKET, bid=0.55, ask=0.57, bid_size=500.0, category="crypto"),
        now=NOW,
    )
    assert record["best"]["size_at_touch"] == 3.0


def test_an_edge_with_no_depth_is_flagged():
    """Priced on both sides but with nothing resting: an edge nobody could take any of."""
    record = opportunity.evaluate_pairing(
        _market(KALSHI, bid=0.43, ask=0.45, ask_size=None),
        _market(POLYMARKET, bid=0.55, ask=0.57, bid_size=None, category="crypto"),
        now=NOW,
    )
    assert opportunity.NO_SIZE in record["reasons"]


def test_an_observation_states_that_it_authorizes_nothing():
    record = opportunity.evaluate_pairing(
        _market(KALSHI, bid=0.43, ask=0.45), _market(POLYMARKET, bid=0.55, ask=0.57), now=NOW
    )
    assert record["authorizes_trading"] is False
    assert opportunity.observation_status_line(record).startswith(
        "kalshi:kalshi-1 <-> polymarket:polymarket-1"
    )


def test_the_detector_cannot_trade():
    """PM1's standing property: this module holds no order path and imports none."""
    for forbidden in ("submit", "place_order", "sign", "wallet", "confirm_group"):
        assert not hasattr(opportunity, forbidden), forbidden


# --- a venue whose rate the watch scan cannot see --------------------------------

def test_a_binance_leg_is_priceable_without_the_venue_stating_a_rate():
    """Found by the first confirmed group. A watch scan re-reads a Binance leg by
    `marketId:tokenId`; `market/detail` is keyed by `marketTopicId` and the order-book
    response carries no fee at all, so the venue's own `feeRateBps` is simply not in hand.

    Without a fallback every observation came back `net_edge: None` — real books on both
    sides, a computed gross edge, and still filed as unreadable, forever. That is not
    caution, it is a venue this phase can never collect data on.
    """
    from runtime.mvp_runtime.predmarket.market_data import BINANCE

    assert fees.taker_fee(BINANCE, price=0.40, contracts=100) is not None
    assert fees.round_trip_fee(
        [{"venue": BINANCE, "price": 0.401},
         {"venue": POLYMARKET, "price": 0.39, "category": "crypto"}]
    )["total_fee_usd"] is not None


def test_the_fallback_never_costs_less_than_the_rate_it_stands_in_for():
    """THE property. The stated rate is applied flat (`rate x P`); a shaped fallback
    (`rate x P x (1-P)`) is cheaper at every price because `(1-P) < 1`. A fallback that
    under-charges the case it replaces is worse than no fallback — it turns "we could not
    price this" into "this looks profitable"."""
    from runtime.mvp_runtime.predmarket.market_data import BINANCE

    for price in (0.05, 0.25, 0.5, 0.75, 0.95):
        stated = fees.taker_fee(BINANCE, price=price, contracts=100, fee_rate_bps=200)
        fallback = fees.taker_fee(BINANCE, price=price, contracts=100)
        assert fallback >= stated, f"fallback undercharges at p={price}"


def test_a_stated_rate_still_wins_over_the_fallback():
    """Discovery sees `feeRateBps` on the topic listing. When the venue has spoken, its
    number is used — the table is only for when it has not."""
    from runtime.mvp_runtime.predmarket.market_data import BINANCE

    assert fees.taker_fee(BINANCE, price=0.4, contracts=100, fee_rate_bps=0) == 0.0
    assert fees.taker_fee(BINANCE, price=0.4, contracts=100, fee_rate_bps=500) > \
        fees.taker_fee(BINANCE, price=0.4, contracts=100)


def test_an_observed_rate_is_never_recorded_as_a_verified_schedule():
    """The rate is what this repo observed the venue publishing, not what the venue
    documented. A record calling it verified would launder the difference — and
    `fee_schedule_verified` is exactly the field a later reader trusts."""
    from runtime.mvp_runtime.predmarket.market_data import BINANCE

    legs = fees.round_trip_fee(
        [{"venue": BINANCE, "price": 0.4},
         {"venue": POLYMARKET, "price": 0.4, "category": "crypto"}])["legs"]
    binance_leg = next(l for l in legs if l["venue"] == BINANCE)
    poly_leg = next(l for l in legs if l["venue"] == POLYMARKET)
    assert binance_leg["fee_schedule_verified"] is False
    assert binance_leg["fee_model"] == fees.FEE_MODEL_FLAT_ASSUMED
    assert poly_leg["fee_schedule_verified"] is True
    assert poly_leg["fee_model"] == fees.FEE_MODEL_SHAPED


def test_the_documented_venues_are_untouched_by_the_fallback():
    """Kalshi and Polymarket publish their formulas; nothing above may quietly re-price
    them."""
    assert fees.taker_fee(KALSHI, price=0.5, contracts=100) == pytest.approx(1.75, abs=1e-4)
    assert fees.taker_fee(POLYMARKET, price=0.5, contracts=100, category="crypto") == \
        pytest.approx(1.75, abs=1e-4)


def test_an_unpriceable_binance_leg_is_still_unpriceable():
    """The fallback supplies a missing RATE, never a missing price."""
    from runtime.mvp_runtime.predmarket.market_data import BINANCE

    assert fees.taker_fee(BINANCE, price=None, contracts=100) is None
    assert fees.taker_fee(BINANCE, price=0.0, contracts=100) is None


# --- an edge that refutes its own premise ----------------------------------------

def _wide(bid, ask, other_bid, other_ask):
    from runtime.mvp_runtime.predmarket.market_data import BINANCE

    return opportunity.evaluate_pairing(
        _market(BINANCE, bid=bid, ask=ask),
        _market(POLYMARKET, bid=other_bid, ask=other_ask, category="crypto"),
        now=NOW,
    )


def test_a_seventy_cent_edge_is_not_an_opportunity():
    """Live on 2026-07-28, twice, both at the top of the report and both looking like the
    best thing in it:

        Binance quoted a July-FOMC market at 0.73 against Polymarket's 0.0015, on $2.56M of
        Polymarket liquidity. Two venues quoting ONE question do not sit a quarter apart.

    So the arithmetic is kept and the claim is withdrawn: the row still carries its gross
    edge, its net edge and its fees, because a pairing that produces this is evidence about
    the pairing — deleting it would delete the evidence.
    """
    record = _wide(0.716, 0.73, 0.001, 0.002)
    assert record["net_edge"] > 0.5
    assert record["is_opportunity"] is False
    assert opportunity.IMPLAUSIBLE_EDGE in record["reasons"]
    assert record["gross_edge"] is not None and record["best"] is not None


def test_an_implausibly_NEGATIVE_edge_is_flagged_too():
    """The Olise shape: 0.82 on one venue against 0.003 on the other. Sign does not change
    what a 80-cent disagreement means about the pairing."""
    record = _wide(0.819, 0.821, 0.002, 0.003)
    assert opportunity.IMPLAUSIBLE_EDGE in record["reasons"]
    assert record["is_opportunity"] is False


def test_an_edge_at_the_scale_this_phase_operates_at_is_untouched():
    """Fees alone are ~2.5 cents per contract, so real findings live in cents. The threshold
    sits in the empty band between 0.035 and 0.699 — nothing observed lives there."""
    record = opportunity.evaluate_pairing(
        _market(KALSHI, bid=0.43, ask=0.45),
        _market(POLYMARKET, bid=0.55, ask=0.57, category="crypto"),
        now=NOW,
    )
    assert record["is_opportunity"] is True
    assert opportunity.IMPLAUSIBLE_EDGE not in record["reasons"]
    assert abs(record["net_edge"]) < opportunity.MAX_PLAUSIBLE_NET_EDGE
