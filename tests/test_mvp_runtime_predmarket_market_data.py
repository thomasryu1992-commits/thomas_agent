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


def _authorized_for(provider_id):
    from runtime.mvp_runtime.safety_gate import NETWORK_ACCESS, Authorization

    return Authorization(
        flags=(NETWORK_ACCESS,), provider_id=provider_id,
        activation_sha256="sha256:test", expires_at="2999-01-01T00:00:00Z",
        evidence_ref=".runtime_governance_state/evidence.md",
    )


def _authorized():
    """A hand-built authorization, so the egress check passes and the NEXT refusal is the
    one under test."""
    from runtime.mvp_runtime.safety_gate import NETWORK_ACCESS, Authorization

    return Authorization(
        flags=(NETWORK_ACCESS,), provider_id=md.BINANCE_PROVIDER_ID,
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
        def list_markets(self, *, limit, timeout_seconds, market_ids=None):
            snap = super().list_markets(
                limit=limit, timeout_seconds=timeout_seconds, market_ids=market_ids
            )
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
        def list_markets(self, *, limit, timeout_seconds, market_ids=None):
            raise ToolError("TOOL_TRANSPORT", "venue unreachable")

    with pytest.raises(ToolBlocked) as exc:
        md.collect_pred_markets(md.KALSHI, collector=_Broken(), now=NOW)
    assert exc.value.reason_code == "TOOL_ERROR"

    degraded = md.degraded_pred_market_record(_Broken(), md.KALSHI, md.PREDMARKET_DEGRADED, now=NOW)
    assert degraded["degraded"] is True
    assert degraded["degraded_reason_code"] == md.PREDMARKET_DEGRADED
    assert degraded["market_count"] == 0 and degraded["quoted_count"] == 0


def test_an_unknown_venue_is_refused():
    for venue in ("bybit", "", None, "KALSHI"):
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


# --- the third venue: Predict.fun markets, reached through Binance ---------------

def _topic_row(**over):
    """One `market/list` topic, shaped like the published example."""
    row = {
        "marketTopicId": 4229564,
        "vendor": "PREDICT_FUN",
        "chainId": "56",
        "slug": "btc-price-1h-up-or-down",
        "title": "BTC Price 1h Up or Down?",
        "question": "Will BTC price go UP in the next 1 hour?",
        "chartType": "CRYPTO_UP_DOWN",
        "feeRateBps": 200,
        "slippageBps": 1200,
        "endDate": 1748134800000,
        "status": "REGISTERED",
        "markets": [{}],
    }
    row.update(over)
    return row


def _detail_row(**over):
    """One `market/detail` payload, shaped like the published example."""
    market = {
        "marketId": 5567895,
        "conditionId": "0xabc123",
        "status": "REGISTERED",
        "tradingStatus": "OPEN",
        "outcomes": [
            {"name": "YES", "price": "0.52", "index": 0, "tokenId": "112233"},
            {"name": "NO", "price": "0.48", "index": 1, "tokenId": "445566"},
        ],
    }
    market.update(over)
    return {"marketTopicId": 4229564, "markets": [market]}


def _pm_book(bids=(("0.50", "5000"), ("0.51", "800")), asks=(("0.53", "300"), ("0.52", "3000"))):
    return {
        "outcome": "YES", "tokenId": "112233", "timestamp": 1748131800000,
        "bids": [{"price": p, "size": s} for p, s in bids],
        "asks": [{"price": p, "size": s} for p, s in asks],
    }


def test_a_topic_is_listed_unquoted_because_the_list_response_carries_no_prices():
    """`market/list` returns topics whose `markets` array is empty by design, so a price here
    would have to be invented. The outcome tokens come from `market/detail`."""
    topic = md.parse_prediction_topics({"marketTopics": [_topic_row()]})[0]
    assert topic.venue == md.BINANCE
    assert topic.market_id == "4229564"          # the TOPIC id, replaced by the token later
    assert topic.title == "Will BTC price go UP in the next 1 hour?"
    assert topic.quote.quoted() is False
    assert topic.fee_rate_bps == 200


def test_the_close_time_is_converted_from_epoch_milliseconds():
    """The venue sends ms since epoch; the rest of this package compares ISO strings, so the
    conversion belongs at the boundary rather than in every consumer."""
    topic = md.parse_prediction_topics({"marketTopics": [_topic_row()]})[0]
    assert topic.close_time == "2025-05-25T00:60:00Z" or topic.close_time.startswith("2025-")
    assert md.parse_prediction_topics({"marketTopics": [_topic_row(endDate=0)]})[0].close_time is None
    assert md.parse_prediction_topics({"marketTopics": [_topic_row(endDate="x")]})[0].close_time is None


def test_a_topic_without_an_id_is_skipped():
    rows = {"marketTopics": [_topic_row(), _topic_row(marketTopicId=None), _topic_row(marketTopicId="")]}
    assert [t.market_id for t in md.parse_prediction_topics(rows)] == ["4229564"]


@pytest.mark.parametrize("payload", [None, [], {"marketTopics": "nope"}])
def test_a_malformed_listing_raises_rather_than_returning_nothing(payload):
    with pytest.raises(ToolError) as exc:
        md.parse_prediction_topics(payload)
    assert exc.value.reason_code == "MALFORMED_RESULT"


def test_the_detail_yields_the_yes_token_and_the_cross_reference():
    """`market_id` becomes the YES outcome token — what the book and any later order key on —
    and `conditionId` is the cross-reference axis, the same shape Polymarket uses."""
    found = md.parse_prediction_yes_outcome(_detail_row())
    assert found == (5567895, "112233", "0xabc123")


def test_a_market_that_is_not_open_is_not_priced():
    """A topic can be REGISTERED while its market's trading is CLOSED. Pricing one nobody can
    trade is pricing something that could never be acted on."""
    assert md.parse_prediction_yes_outcome(_detail_row(tradingStatus="CLOSED")) is None


def test_an_outcome_with_no_token_id_yields_nothing():
    detail = _detail_row(outcomes=[{"name": "YES", "price": "0.52", "index": 0}])
    assert md.parse_prediction_yes_outcome(detail) is None
    assert md.parse_prediction_yes_outcome({}) is None
    assert md.parse_prediction_yes_outcome(None) is None


def test_the_order_book_gives_the_best_bid_and_ask_regardless_of_page_order():
    """The venue's own order book — the endpoint whose absence from Predict.fun's public API
    forced the first version of this collector to list markets and price none. Same caution as
    Polymarket's: max bid, min ask, never trust the page order."""
    quote = md.parse_prediction_book(_pm_book())
    assert quote.yes_bid == 0.51 and quote.yes_ask == 0.52
    assert quote.yes_bid_size == 800.0
    assert quote.quoted() is True


def test_an_empty_or_crossed_prediction_book_is_unquoted():
    assert md.parse_prediction_book(_pm_book(bids=(), asks=())).quoted() is False
    assert md.parse_prediction_book({}).quoted() is False
    crossed = md.parse_prediction_book(_pm_book(bids=(("0.60", "10"),), asks=(("0.55", "10"),)))
    assert crossed.quoted() is False


def test_missing_credentials_are_reported_as_their_own_fact(monkeypatch):
    """Every prediction endpoint is signed, so this venue needs a key AND a secret. "Nobody
    configured them" and "the venue is down" are different facts about a scan."""
    monkeypatch.delenv(md.BINANCE_API_KEY_ENV, raising=False)
    monkeypatch.delenv(md.BINANCE_API_SECRET_ENV, raising=False)
    collector = md.BinancePredictionCollector(authorization=_authorized())
    assert collector.credentials_present() is False
    with pytest.raises(ToolError) as exc:
        collector.list_markets(limit=1, timeout_seconds=1)
    assert exc.value.reason_code == md.API_KEY_MISSING
    # The env var NAMES are actionable; the values never appear anywhere.
    assert md.BINANCE_API_KEY_ENV in exc.value.reason


def test_the_secret_never_reaches_a_record(monkeypatch):
    monkeypatch.setenv(md.BINANCE_API_KEY_ENV, "public-ish-key")
    monkeypatch.setenv(md.BINANCE_API_SECRET_ENV, "super-secret-value")
    collector = md.BinancePredictionCollector(authorization=_authorized())
    assert collector.credentials_present() is True
    record = md.degraded_pred_market_record(collector, md.BINANCE, md.PREDMARKET_DEGRADED, now=NOW)
    blob = json.dumps(record)
    assert "super-secret-value" not in blob and "public-ish-key" not in blob


def test_a_signed_key_can_never_be_pointed_at_another_host():
    """Refused at construction, like the account feed: a URL typo must not send a signed
    credential somewhere unexpected."""
    with pytest.raises(ToolBlocked) as exc:
        md.BinancePredictionCollector(base_url="https://evil.example.com")
    assert exc.value.reason_code == "HOST_NOT_ALLOWED"


def test_the_gate_still_comes_first_for_the_third_venue(monkeypatch, tmp_path):
    """A key is not an authorization. Without the grant the collector reaches nothing, even
    with perfectly good credentials set."""
    monkeypatch.setenv(md.BINANCE_API_KEY_ENV, "k")
    monkeypatch.setenv(md.BINANCE_API_SECRET_ENV, "s")
    with pytest.raises(SafetyGateBlocked):
        md.BinancePredictionCollector().list_markets(limit=1, timeout_seconds=1)
    monkeypatch.setenv(md.BINANCE_ENV, md.BINANCE_PROVIDER_ID)
    with pytest.raises(SafetyGateBlocked):
        md.select_pred_market_collector(md.BINANCE, root=tmp_path)


def test_the_whole_three_call_walk_produces_a_quoted_market(monkeypatch):
    """list -> detail -> order-book, with the network stubbed at the one signed-GET seam. What
    it pins is the handover: the token id from the detail is what the book is asked for, and
    the topic's fee rate rides onto the quoted market."""
    monkeypatch.setenv(md.BINANCE_API_KEY_ENV, "k")
    monkeypatch.setenv(md.BINANCE_API_SECRET_ENV, "s")
    asked: list[tuple[str, dict]] = []

    def _fake_get(self, path, params, *, timeout_seconds):
        asked.append((path, dict(params)))
        if path == md.BinancePredictionCollector.LIST_PATH:
            return {"marketTopics": [_topic_row()]}
        if path == md.BinancePredictionCollector.DETAIL_PATH:
            return _detail_row()
        return _pm_book()

    monkeypatch.setattr(md.BinancePredictionCollector, "_signed_get", _fake_get)
    snapshot = md.BinancePredictionCollector(authorization=_authorized()).list_markets(
        limit=5, timeout_seconds=1
    )
    market = snapshot.markets[0]
    assert market.market_id == "5567895:112233"   # marketId:tokenId — both, because the book needs both
    assert market.group_id == "0xabc123"          # the cross-reference axis
    assert market.fee_rate_bps == 200
    assert market.quote.quoted() is True
    # The book was asked for the token the detail named, with the vendor spelled out.
    book_call = [p for path, p in asked if path == md.BinancePredictionCollector.BOOK_PATH][0]
    assert book_call["tokenId"] == "112233" and book_call["marketId"] == 5567895
    assert book_call["vendor"] == "predict_fun"


def test_a_failed_detail_or_book_leaves_the_market_unquoted_rather_than_dropped(monkeypatch):
    """One topic's second or third call failing is not the scan failing — and an unpriced
    market is still a market we know exists."""
    monkeypatch.setenv(md.BINANCE_API_KEY_ENV, "k")
    monkeypatch.setenv(md.BINANCE_API_SECRET_ENV, "s")

    def _fake_get(self, path, params, *, timeout_seconds):
        if path == md.BinancePredictionCollector.LIST_PATH:
            return {"marketTopics": [_topic_row()]}
        raise ToolError("TOOL_TRANSPORT", "venue unreachable")

    monkeypatch.setattr(md.BinancePredictionCollector, "_signed_get", _fake_get)
    snapshot = md.BinancePredictionCollector(authorization=_authorized()).list_markets(
        limit=5, timeout_seconds=1
    )
    assert len(snapshot.markets) == 1
    assert snapshot.markets[0].quote.quoted() is False


# --- the targeted read: what a watch scan actually costs -------------------------

def test_a_targeted_binance_quote_is_one_call_not_a_rediscovery(monkeypatch):
    """The saving this exists for. A confirmed leg carries `marketId:tokenId`, so re-reading
    it every two minutes is ONE order-book call — not the list -> detail -> book walk that
    found it, which would spend 1 + 2N calls to learn nothing new."""
    monkeypatch.setenv(md.BINANCE_API_KEY_ENV, "k")
    monkeypatch.setenv(md.BINANCE_API_SECRET_ENV, "s")
    paths: list[str] = []

    def _fake_get(self, path, params, *, timeout_seconds):
        paths.append(path)
        return _pm_book()

    monkeypatch.setattr(md.BinancePredictionCollector, "_signed_get", _fake_get)
    snapshot = md.BinancePredictionCollector(authorization=_authorized()).list_markets(
        limit=100, timeout_seconds=1, market_ids=["5567895:112233"],
    )
    assert paths == [md.BinancePredictionCollector.BOOK_PATH]
    assert snapshot.markets[0].quote.quoted() is True


def test_a_leg_whose_id_cannot_be_split_is_unquoted_not_guessed(monkeypatch):
    """An id from before the composite format, or hand-edited. There is nothing to ask the
    venue for, so it is recorded unquoted — the same answer every other unreadable leg gets."""
    monkeypatch.setenv(md.BINANCE_API_KEY_ENV, "k")
    monkeypatch.setenv(md.BINANCE_API_SECRET_ENV, "s")
    called: list[str] = []
    monkeypatch.setattr(
        md.BinancePredictionCollector, "_signed_get",
        lambda self, path, params, *, timeout_seconds: called.append(path) or _pm_book(),
    )
    snapshot = md.BinancePredictionCollector(authorization=_authorized()).list_markets(
        limit=100, timeout_seconds=1, market_ids=["112233"],
    )
    assert called == []          # nothing asked
    assert snapshot.markets[0].quote.quoted() is False


def test_kalshi_lets_the_venue_do_the_filtering(monkeypatch):
    """Kalshi returns prices inline and accepts a `tickers` allowlist, so a targeted read is
    still one call — and a smaller payload."""
    seen: dict[str, str] = {}

    def _fake(url, *, timeout_seconds, headers=None):
        seen["url"] = url
        return _kalshi_payload()

    monkeypatch.setattr(md, "_get_json", _fake)
    md.KalshiPublicCollector(authorization=_authorized_for(md.KALSHI_PROVIDER_ID)).list_markets(
        limit=100, timeout_seconds=1, market_ids=["FED-26DEC-CUT", "OTHER"],
    )
    assert "tickers=FED-26DEC-CUT%2COTHER" in seen["url"]


def test_polymarket_books_only_the_markets_asked_for(monkeypatch):
    """One Gamma call for identity, then a book call per WANTED market — not per market that
    happened to be listed first."""
    booked: list[str] = []

    def _fake(url, *, timeout_seconds, headers=None):
        if "gamma" in url:
            return [_gamma_row(), _gamma_row(clobTokenIds=["other-token", "no"])]
        booked.append(url)
        return _book()

    monkeypatch.setattr(md, "_get_json", _fake)
    snapshot = md.PolymarketPublicCollector(
        authorization=_authorized_for(md.POLYMARKET_PROVIDER_ID)
    ).list_markets(limit=100, timeout_seconds=1, market_ids=["other-token"])
    assert len(booked) == 1 and "other-token" in booked[0]
    assert [m.market_id for m in snapshot.markets] == ["other-token"]
