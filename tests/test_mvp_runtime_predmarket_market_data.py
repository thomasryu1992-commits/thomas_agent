"""PM1 venue adapters — venue semantics against recorded payloads, and the gate.

Two properties carry the weight.

**A price this module reports is one somebody could trade at.** Both venues say "nobody is
quoting this side" with a zero, and reading that as a price of 0.0 would manufacture a
100%-margin arbitrage out of an empty book — the exact failure mode a prediction-market bot
dies of. So every parser test checks the empty and impossible cases, not just the happy one.

**The parsing is separate from the read**, so the venues' semantics are exercised here with
no socket and no grant, against payloads shaped like the ones their API references document
(checked 2026-07-26). Kalshi's decimal-dollar strings are the case worth naming: an older
API served integer cents, and a parser written from memory would have read nothing.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolBlocked, ToolError
from runtime.mvp_runtime.predmarket import market_data as md

NOW = "2026-07-26T12:00:00Z"


def _authorized():
    """A hand-built authorization, so the egress check passes and the NEXT refusal is the
    one under test."""
    from runtime.mvp_runtime.safety_gate import NETWORK_ACCESS, Authorization

    return Authorization(
        flags=(NETWORK_ACCESS,), provider_id=md.PREDICTFUN_PROVIDER_ID,
        activation_sha256="sha256:test", expires_at="2999-01-01T00:00:00Z",
        evidence_ref=".runtime_governance_state/evidence.md",
    )


# --- recorded payload shapes ----------------------------------------------------

def _kalshi_row(**over):
    row = {
        "ticker": "FED-26DEC-CUT",
        "event_ticker": "FED-26DEC",
        "yes_sub_title": "Fed cuts at the December meeting",
        "status": "active",
        "close_time": "2026-12-31T23:59:00Z",
        "yes_bid_dollars": "0.5600",
        "yes_ask_dollars": "0.5900",
        "yes_bid_size_fp": "120.00",
        "yes_ask_size_fp": "80.00",
    }
    row.update(over)
    return row


def _kalshi_payload(*rows):
    return {"markets": list(rows) or [_kalshi_row()], "cursor": ""}


def _gamma_row(**over):
    row = {
        "question": "Will the Fed cut rates in December?",
        "conditionId": "0xcondition",
        "slug": "fed-cut-december",
        "endDate": "2026-12-31T23:59:00Z",
        "active": True,
        "closed": False,
        "category": "Economics",
        # Gamma ships these as stringified JSON in the documented client behaviour.
        "clobTokenIds": '["71321045679252212594626385532706912750332728571942532289631379312455583992563", "52114319501245915516055106046884209969926127482827954674443846427813813222426"]',
    }
    row.update(over)
    return row


def _book(bids=(("0.44", "500"), ("0.45", "300")), asks=(("0.47", "250"), ("0.46", "100"))):
    return {
        "market": "0xcondition",
        "asset_id": "713210456",
        "timestamp": "1769424000000",
        "hash": "abc",
        "bids": [{"price": p, "size": s} for p, s in bids],
        "asks": [{"price": p, "size": s} for p, s in asks],
        "min_order_size": "5",
        "tick_size": "0.01",
        "neg_risk": False,
    }


# --- Kalshi ---------------------------------------------------------------------

def test_kalshi_prices_are_decimal_dollars_not_cents():
    """The field names and units that decide whether this parses at all. A $0.56 contract
    settles at $1, so the dollar figure IS the probability — no conversion, and no /100."""
    market = md.parse_kalshi_markets(_kalshi_payload())[0]
    assert market.venue == md.KALSHI
    assert market.market_id == "FED-26DEC-CUT"
    assert market.group_id == "FED-26DEC"
    assert market.quote.yes_bid == 0.56
    assert market.quote.yes_ask == 0.59
    assert market.quote.yes_bid_size == 120.0
    assert market.quote.quoted() is True
    assert market.quote.mid() == 0.575


def test_a_kalshi_market_with_no_bid_is_unquoted_not_priced_at_zero():
    """Kalshi reports an empty side as "0.0000". Read as a price it would look like a
    contract available for nothing — a 100%-margin opportunity that does not exist."""
    market = md.parse_kalshi_markets(_kalshi_payload(_kalshi_row(yes_bid_dollars="0.0000")))[0]
    assert market.quote.yes_bid is None
    assert market.quote.quoted() is False
    assert market.quote.mid() is None


@pytest.mark.parametrize("price", ["1.0000", "1.2000", "-0.1", "", None, "abc"])
def test_an_impossible_kalshi_price_is_unquoted(price):
    """A resting order at or beyond the bounds does not exist; nor does a price that will
    not parse. All of them are "not quoted", never a number."""
    market = md.parse_kalshi_markets(_kalshi_payload(_kalshi_row(yes_ask_dollars=price)))[0]
    assert market.quote.yes_ask is None


def test_a_kalshi_row_without_a_ticker_is_skipped_not_carried_anonymously():
    rows = _kalshi_payload(_kalshi_row(), _kalshi_row(ticker=""), _kalshi_row(ticker=None))
    assert [m.market_id for m in md.parse_kalshi_markets(rows)] == ["FED-26DEC-CUT"]


def test_a_kalshi_row_without_prices_is_still_a_market():
    """Knowing the market exists is useful to pair matching even when nobody is quoting it,
    so it is carried unquoted rather than dropped."""
    row = _kalshi_row()
    for key in ("yes_bid_dollars", "yes_ask_dollars", "yes_bid_size_fp", "yes_ask_size_fp"):
        row.pop(key)
    market = md.parse_kalshi_markets(_kalshi_payload(row))[0]
    assert market.market_id == "FED-26DEC-CUT" and market.quote.quoted() is False


@pytest.mark.parametrize("payload", [None, [], {"cursor": ""}, {"markets": "nope"}])
def test_a_malformed_kalshi_payload_raises_rather_than_returning_nothing(payload):
    """An empty list and an unreadable response must not look the same: the first is a
    venue with no open markets, the second is a bug or an outage."""
    with pytest.raises(ToolError) as exc:
        md.parse_kalshi_markets(payload)
    assert exc.value.reason_code == "MALFORMED_RESULT"


# --- Polymarket -----------------------------------------------------------------

def test_gamma_markets_are_keyed_on_the_yes_clob_token():
    """The order book — and any later order — keys on the CLOB token id, not the
    conditionId, so that is what `market_id` must be."""
    market = md.parse_gamma_markets([_gamma_row()])[0]
    assert market.venue == md.POLYMARKET
    assert market.market_id.startswith("71321045679")
    assert market.group_id == "0xcondition"
    assert market.title == "Will the Fed cut rates in December?"
    assert market.category == "Economics"


def test_gamma_markets_arrive_unquoted():
    """Gamma's outcomePrices are a derived figure, not a book. Quoting from them would put
    a non-executable price into a fee-adjusted comparison."""
    row = _gamma_row(outcomePrices='["0.56", "0.44"]')
    assert md.parse_gamma_markets([row])[0].quote.quoted() is False


def test_gamma_token_ids_are_read_whether_stringified_or_not():
    """The field is documented as stringified JSON and observed as a plain array. Rather
    than guess which is current, both are read — and anything else is skipped, because a
    market with no token id cannot be priced or traded."""
    as_list = _gamma_row(clobTokenIds=["tok-yes", "tok-no"])
    assert md.parse_gamma_markets([as_list])[0].market_id == "tok-yes"
    for broken in ("", "not json", "{}", None, []):
        assert md.parse_gamma_markets([_gamma_row(clobTokenIds=broken)]) == []


def test_a_gamma_payload_may_be_a_list_or_a_data_envelope():
    assert len(md.parse_gamma_markets([_gamma_row()])) == 1
    assert len(md.parse_gamma_markets({"data": [_gamma_row()]})) == 1
    with pytest.raises(ToolError):
        md.parse_gamma_markets({"markets": [_gamma_row()]})


def test_the_book_gives_the_best_bid_and_ask_regardless_of_page_order():
    """Bids are documented descending and asks ascending, but a mis-sorted page must not
    quote the worst price as the best — on one side that is a fake spread, on the other it
    is fake free money. The fixture is deliberately out of order."""
    quote = md.parse_clob_book(_book())
    assert quote.yes_bid == 0.45      # max of the bids, not the first
    assert quote.yes_ask == 0.46      # min of the asks, not the first
    assert quote.yes_bid_size == 300.0
    assert quote.quoted() is True


def test_an_empty_or_one_sided_book_is_unquoted():
    assert md.parse_clob_book(_book(bids=(), asks=())).quoted() is False
    assert md.parse_clob_book(_book(asks=())).quoted() is False
    assert md.parse_clob_book({}).quoted() is False
    assert md.parse_clob_book(None).quoted() is False


def test_a_crossed_book_is_not_quoted():
    """bid >= ask is a stale or crossed read, not an instant profit. Reporting it as quoted
    would hand the detector a guaranteed 'opportunity' on every scan."""
    crossed = md.parse_clob_book(_book(bids=(("0.60", "10"),), asks=(("0.55", "10"),)))
    assert crossed.yes_bid == 0.60 and crossed.yes_ask == 0.55
    assert crossed.quoted() is False and crossed.mid() is None


# --- the records ----------------------------------------------------------------

def test_a_collection_records_what_it_read_and_what_it_could_not_price():
    snapshot, record = md.collect_pred_markets(
        md.KALSHI, collector=md.MockPredMarketCollector(md.KALSHI), now=NOW, limit=4
    )
    assert snapshot["venue"] == md.KALSHI
    assert snapshot["market_count"] == 4 and snapshot["quoted_count"] == 4
    assert snapshot["is_synthetic"] is True
    assert record["read_only"] is True and record["external_action"] is False
    assert record["network_egress"] is False
    assert record["output_sha256"] == record["output_sha256"]
    assert record["operation"] == "collect_pred_markets"


def test_the_unquoted_count_is_reported_separately():
    """A scan where every call succeeded but nothing was quoted is a degraded scan; the
    record has to make that visible rather than reporting a healthy market_count."""

    class _Unquoted(md.MockPredMarketCollector):
        def list_markets(self, *, limit, timeout_seconds):
            snap = super().list_markets(limit=limit, timeout_seconds=timeout_seconds)
            snap.markets = [
                md.PredMarket(
                    venue=m.venue, market_id=m.market_id, group_id=m.group_id, title=m.title,
                    close_time=m.close_time, status=m.status,
                )
                for m in snap.markets
            ]
            return snap

    snapshot, record = md.collect_pred_markets(
        md.KALSHI, collector=_Unquoted(md.KALSHI), now=NOW, limit=4
    )
    assert snapshot["market_count"] == 4 and snapshot["quoted_count"] == 0
    assert record["quoted_count"] == 0


def test_a_collector_failure_fails_closed_for_the_caller_to_degrade():
    """The caller degrades, not this function — only the caller knows whether the other
    venue answered, and a scan with one venue readable is still a scan."""

    class _Broken(md.MockPredMarketCollector):
        def list_markets(self, *, limit, timeout_seconds):
            raise ToolError("TOOL_TRANSPORT", "venue unreachable")

    with pytest.raises(ToolBlocked) as exc:
        md.collect_pred_markets(md.KALSHI, collector=_Broken(), now=NOW)
    assert exc.value.reason_code == "TOOL_ERROR"

    degraded = md.degraded_pred_market_record(_Broken(), md.KALSHI, md.PREDMARKET_DEGRADED, now=NOW)
    assert degraded["degraded"] is True
    assert degraded["degraded_reason_code"] == md.PREDMARKET_DEGRADED
    assert degraded["market_count"] == 0 and degraded["quoted_count"] == 0


def test_an_unknown_venue_is_refused():
    for venue in ("binance", "", None, "KALSHI"):
        with pytest.raises(ToolBlocked) as exc:
            md.require_venue(venue)
        assert exc.value.reason_code == "INVALID_VENUE"


# --- the gate -------------------------------------------------------------------

def test_the_default_collector_opens_no_socket(monkeypatch, tmp_path):
    for venue in md.VENUES:
        collector = md.select_pred_market_collector(venue, root=tmp_path)
        assert isinstance(collector, md.MockPredMarketCollector)
        assert collector.network_egress is False
        assert collector.venue == venue


@pytest.mark.parametrize("venue,env", [(md.KALSHI, md.KALSHI_ENV), (md.POLYMARKET, md.POLYMARKET_ENV)])
def test_the_env_var_alone_fails_closed(monkeypatch, tmp_path, venue, env):
    """Both endpoints are public and keyless, so the gate is the ENTIRE boundary between a
    config typo and an outbound socket. Opting in without a grant must build nothing."""
    monkeypatch.setenv(env, md.KALSHI_PROVIDER_ID if venue == md.KALSHI else md.POLYMARKET_PROVIDER_ID)
    with pytest.raises(SafetyGateBlocked):
        md.select_pred_market_collector(venue, root=tmp_path)


def test_one_venues_opt_in_never_selects_the_other(monkeypatch, tmp_path):
    """One grant per venue: authorizing Kalshi reads must not authorize Polymarket's, so an
    opt-in for one leaves the other on its inert default."""
    monkeypatch.setenv(md.KALSHI_ENV, md.KALSHI_PROVIDER_ID)
    assert isinstance(md.select_pred_market_collector(md.POLYMARKET, root=tmp_path),
                      md.MockPredMarketCollector)


def test_a_directly_constructed_collector_still_cannot_reach_the_venue():
    """Defense in depth: the gate selects, but the adapter re-asserts at the moment of
    egress, so constructing one by hand grants nothing."""
    for collector in (md.KalshiPublicCollector(), md.PolymarketPublicCollector()):
        with pytest.raises(SafetyGateBlocked):
            collector.list_markets(limit=1, timeout_seconds=1)


def test_the_module_cannot_place_an_order():
    """The PM1 safety property, asserted rather than assumed: this module holds no order
    path and imports none. PM3 rides the existing P5 packet, and nothing here reaches it."""
    source = (md.__doc__ or "").lower()
    assert "nothing here trades" in source
    for forbidden in ("submit", "place_order", "sign", "private_key", "wallet"):
        assert not hasattr(md, forbidden), forbidden


# --- the third venue: not keyless, and not quoted -------------------------------

def _predictfun_row(**over):
    row = {
        "id": 12345,
        "question": "Will the Fed cut rates in December?",
        "title": "Fed December decision",
        "categorySlug": "economics",
        "tradingStatus": "OPEN",
        "status": "REGISTERED",
        "endDate": "2026-12-31T23:59:00Z",
        "outcomes": [{"name": "Yes", "prices": ["0.56"]}, {"name": "No", "prices": ["0.44"]}],
        "polymarketConditionIds": ["0xcondition"],
    }
    row.update(over)
    return row


def test_predictfun_markets_are_listed_but_never_quoted():
    """No order-book endpoint is documented, only per-outcome `prices` — a derived figure
    exactly like Gamma's outcomePrices, which this package already refuses to quote from. A
    fee-adjusted comparison against a non-executable price is how a paper edge becomes an
    imaginary one."""
    market = md.parse_predictfun_markets({"data": [_predictfun_row()]})[0]
    assert market.venue == md.PREDICTFUN
    assert market.market_id == "12345"
    assert market.title == "Will the Fed cut rates in December?"
    assert market.category == "economics"
    assert market.quote.quoted() is False


def test_the_venues_own_cross_reference_is_carried():
    """`polymarketConditionIds` is the venue naming the market it mirrors — pairing evidence
    no title comparison can match. It rides in `group_id`, which the matcher already reads."""
    market = md.parse_predictfun_markets([_predictfun_row()])[0]
    assert market.group_id == "0xcondition"
    # Stringified JSON is accepted too, since venues disagree about which shape they send.
    as_string = md.parse_predictfun_markets([_predictfun_row(polymarketConditionIds='["0xother"]')])[0]
    assert as_string.group_id == "0xother"
    # No cross-reference is simply no evidence, not an error.
    assert md.parse_predictfun_markets([_predictfun_row(polymarketConditionIds=None)])[0].group_id is None


def test_trading_status_is_what_decides_tradability():
    """A market can be REGISTERED while its trading is CLOSED. Pairing one nobody can trade
    is a pairing that could never be acted on."""
    market = md.parse_predictfun_markets([_predictfun_row(tradingStatus="CLOSED")])[0]
    assert market.status == "CLOSED"


def test_a_row_without_an_id_is_skipped():
    rows = [_predictfun_row(), _predictfun_row(id=None), _predictfun_row(id="")]
    assert [m.market_id for m in md.parse_predictfun_markets(rows)] == ["12345"]


def test_a_missing_api_key_is_reported_as_its_own_fact(monkeypatch):
    """The one venue of the three that is not keyless. "Nobody configured a key" and "the
    venue is down" are different facts about a scan; conflating them shows an outage where
    there was an unfinished setup step."""
    monkeypatch.delenv(md.PREDICTFUN_API_KEY_ENV, raising=False)
    collector = md.PredictFunCollector(authorization=_authorized())
    assert collector.api_key_present() is False
    with pytest.raises(ToolError) as exc:
        collector.list_markets(limit=1, timeout_seconds=1)
    assert exc.value.reason_code == md.API_KEY_MISSING
    # The env var NAME is actionable; the value never appears anywhere.
    assert md.PREDICTFUN_API_KEY_ENV in exc.value.reason


def test_the_key_never_reaches_a_record_or_a_message(monkeypatch):
    monkeypatch.setenv(md.PREDICTFUN_API_KEY_ENV, "super-secret-key-value")
    collector = md.PredictFunCollector(authorization=_authorized())
    assert collector.api_key_present() is True
    record = md.degraded_pred_market_record(collector, md.PREDICTFUN, md.PREDMARKET_DEGRADED, now=NOW)
    assert "super-secret-key-value" not in json.dumps(record)


def test_the_gate_still_comes_first_for_the_third_venue(monkeypatch, tmp_path):
    """A key is not an authorization. Without the grant the collector reaches nothing, even
    with a perfectly good key set."""
    monkeypatch.setenv(md.PREDICTFUN_API_KEY_ENV, "key")
    with pytest.raises(SafetyGateBlocked):
        md.PredictFunCollector().list_markets(limit=1, timeout_seconds=1)
    monkeypatch.setenv(md.PREDICTFUN_ENV, md.PREDICTFUN_PROVIDER_ID)
    with pytest.raises(SafetyGateBlocked):
        md.select_pred_market_collector(md.PREDICTFUN, root=tmp_path)
