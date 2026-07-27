"""PM1 screening — which markets are worth looking at, and the noise that made it necessary.

Every fixture here is a trimmed **real payload**, read from the live venues on 2026-07-27
while diagnosing why the deployed scan produced five Kalshi markets with zero quotes. The
listings were not broken; they were full of things this pipeline cannot use:

- Kalshi's open listing is mostly ``KXMVE…`` parlays — one market that is a basket of nine
  others, quoted 0.00/0.00, whose title is a comma-joined list of legs;
- Binance's is mostly five-minute crypto markets that close before an operator could read the
  proposal naming them;
- Polymarket serves markets with ``active: true, closed: false`` whose end date passed months
  ago.

The tests that matter most are the two that pin *how* a market is excluded: on a field the
venue itself publishes, and never silently.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.predmarket import market_data as md
from runtime.mvp_runtime.predmarket import screening
from runtime.mvp_runtime.predmarket.market_data import BINANCE, KALSHI, POLYMARKET, PredMarket

NOW = "2026-07-27T06:00:00Z"


def _market(venue=KALSHI, *, close_time="2026-08-15T00:00:00Z", derived_from=None,
            accepting_orders=None, market_id="M-1", title="Will the Fed cut rates?"):
    return PredMarket(
        venue=venue, market_id=market_id, group_id=None, title=title,
        close_time=close_time, status="active",
        derived_from=derived_from, accepting_orders=accepting_orders,
    )


# --- the gates ------------------------------------------------------------------

def test_a_venue_declared_parlay_is_excluded_and_names_its_legs():
    """THE Kalshi case. A basket of nine other markets is not an event any other venue
    quotes, so it can never be one leg of a cross-venue group — and the exclusion rests on
    Kalshi's own ``mve_selected_legs``, not on a guess about its title."""
    parlay = _market(derived_from=("KXLIGAMXGAME-26AUG01ATLMON-ATL", "KXLIGAMXGAME-26AUG01CRAALA-CRA"))
    verdict = screening.screen_market(parlay, now=NOW)
    assert verdict.observable is False
    assert screening.DERIVED_COMBINATION in verdict.reasons
    assert verdict.derived_leg_count == 2


def test_market_type_binary_does_not_rescue_a_parlay():
    """Kalshi reports that nine-leg basket as ``market_type: "binary"``. True of its payoff,
    useless as a filter — which is exactly why the parser reads ``mve_selected_legs`` instead
    and why nothing here consults a type field."""
    row = {
        "ticker": "KXMVESPORTSMULTIGAMEEXTENDED-S202603398E54657-ABC",
        "market_type": "binary",
        "event_ticker": "KXMVESPORTSMULTIGAMEEXTENDED-S202603398E54657",
        "mve_collection_ticker": "KXMVESPORTSMULTIGAMEEXTENDED-R",
        "mve_selected_legs": [
            {"market_ticker": "KXLIGAMXGAME-26AUG01ATLMON-ATL", "side": "yes"},
            {"market_ticker": "KXLIGAMXGAME-26AUG01CRAALA-CRA", "side": "yes"},
        ],
        "yes_sub_title": "yes Atlas,yes Cruz Azul",
        "close_time": "2026-08-17T01:00:00Z",
        "yes_bid_dollars": "0.0000", "yes_ask_dollars": "0.0000",
    }
    parsed = md.parse_kalshi_markets({"markets": [row]})[0]
    assert parsed.derived_from == ("KXLIGAMXGAME-26AUG01ATLMON-ATL", "KXLIGAMXGAME-26AUG01CRAALA-CRA")
    assert screening.screen_market(parsed, now=NOW).observable is False


def test_a_collection_ticker_alone_is_enough_to_call_it_derived():
    """A parlay that names its collection but not its legs has still told us what it is."""
    parsed = md.parse_kalshi_markets({"markets": [{
        "ticker": "KXMVECROSSCATEGORY-S1-X",
        "mve_collection_ticker": "KXMVECROSSCATEGORY-R",
        "close_time": "2026-08-17T01:00:00Z",
    }]})[0]
    assert parsed.derived_from == ("KXMVECROSSCATEGORY-R",)


def test_an_ordinary_market_is_not_marked_derived():
    """The other half of the gate: a plain binary market carries ``None``, not an empty
    tuple, so "the venue did not say" stays distinguishable from "it said no legs"."""
    parsed = md.parse_kalshi_markets({"markets": [{
        "ticker": "KXBTCD-26JUL2703-T73299.99",
        "close_time": "2026-08-17T01:00:00Z",
        "yes_bid_dollars": "0.5600", "yes_ask_dollars": "0.5800",
    }]})[0]
    assert parsed.derived_from is None
    assert screening.screen_market(parsed, now=NOW).observable is True


def test_a_five_minute_market_cannot_outlive_the_confirmation_it_needs():
    """THE Binance case. The pipeline is propose -> a human confirms -> watch scans observe.
    A market closing in five minutes is not a missed opportunity; it was never reachable,
    because the human step has not finished by then."""
    verdict = screening.screen_market(
        _market(BINANCE, close_time="2026-07-27T06:05:00Z"), now=NOW)
    assert verdict.observable is False
    assert screening.HORIZON_TOO_SHORT in verdict.reasons
    assert verdict.hours_to_close == pytest.approx(5 / 60, abs=1e-3)


def test_an_already_expired_listing_reports_negative_time_not_zero():
    """Polymarket serves ``active: true, closed: false`` on markets whose end date passed
    months ago. Clamping that to zero would file it as "closing very soon" — the same row as
    a market that really is about to close, and a wrong answer to why it was excluded."""
    verdict = screening.screen_market(
        _market(POLYMARKET, close_time="2025-12-31T12:00:00Z"), now=NOW)
    assert verdict.hours_to_close is not None and verdict.hours_to_close < -1000
    assert screening.HORIZON_TOO_SHORT in verdict.reasons


def test_an_unreadable_close_time_fails_closed():
    """The horizon question is the whole gate; a market that never said when it closes leaves
    it permanently unanswerable, so it is refused rather than assumed generous."""
    verdict = screening.screen_market(_market(close_time=None), now=NOW)
    assert verdict.observable is False
    assert screening.CLOSE_TIME_UNKNOWN in verdict.reasons


def test_a_venue_that_did_not_say_whether_it_takes_orders_is_not_refused():
    """The asymmetry with the rule above, and it is deliberate. An absent
    ``acceptingOrders`` is silence that the market's own book answers at the next scan;
    an absent close time is a question nothing later resolves. Unknown is not "no" — the
    same rule ``matching`` follows for a missing category."""
    assert screening.screen_market(_market(accepting_orders=None), now=NOW).observable is True
    refused = screening.screen_market(_market(accepting_orders=False), now=NOW)
    assert refused.observable is False
    assert screening.NOT_ACCEPTING_ORDERS in refused.reasons


def test_every_failing_gate_is_recorded_not_just_the_first():
    """A reviewer asking why the list emptied out gets the whole answer in one pass."""
    verdict = screening.screen_market(
        _market(close_time="2026-07-27T06:01:00Z", derived_from=("A",), accepting_orders=False),
        now=NOW,
    )
    assert set(verdict.reasons) == {
        screening.DERIVED_COMBINATION, screening.HORIZON_TOO_SHORT, screening.NOT_ACCEPTING_ORDERS,
    }


def test_the_horizon_is_a_parameter_so_a_confirmed_leg_is_never_dropped():
    """A watch scan re-reads groups an operator already confirmed. The horizon question was
    settled then, and a leg with an hour left still owes the report its closing readings."""
    soon = _market(close_time="2026-07-27T07:00:00Z")
    assert screening.screen_market(soon, now=NOW).observable is False
    assert screening.screen_market(soon, now=NOW, min_horizon_hours=0).observable is True


# --- nothing is dropped quietly ---------------------------------------------------

def test_the_summary_says_what_was_removed_and_why():
    """"0 candidates" and "0 candidates out of 3, all parlays" are different findings. A
    filter that produced the first from the second would hide its own mistakes for as long
    as it was wrong."""
    result = screening.screen_markets([
        _market(market_id="ok"),
        _market(market_id="parlay", derived_from=("A", "B")),
        _market(market_id="expired", close_time="2025-01-01T00:00:00Z"),
    ], now=NOW)

    assert result["screened_count"] == 3
    assert result["observable_count"] == 1
    assert [m.market_id for m in result["observable"]] == ["ok"]
    assert result["excluded_by_reason"] == {
        screening.DERIVED_COMBINATION: 1, screening.HORIZON_TOO_SHORT: 1,
    }
    # The excluded rows themselves survive, or the threshold is unfalsifiable.
    assert {row["market_id"] for row in result["excluded"]} == {"parlay", "expired"}


def test_the_status_line_is_ascii_for_a_cp949_console():
    line = screening.screening_status_line(screening.screen_markets(
        [_market(market_id="parlay", derived_from=("A",))], now=NOW))
    line.encode("ascii")
    assert "DERIVED_COMBINATION=1" in line


def test_screening_ignores_things_that_are_not_markets():
    assert screening.screen_markets([None, "x", 7], now=NOW)["screened_count"] == 0


# --- the server-side push ---------------------------------------------------------

def test_the_horizon_converts_to_both_shapes_the_venues_ask_for():
    """Kalshi's ``min_close_ts`` takes epoch seconds, Gamma's ``end_date_min`` an ISO
    instant. Same horizon, two encodings, one place that computes it."""
    seconds = screening.min_close_epoch_seconds(now=NOW, min_horizon_hours=6)
    assert seconds == int(md.timeutil.parse_iso("2026-07-27T12:00:00Z").timestamp())
    assert screening.min_close_iso(now=NOW, min_horizon_hours=6).startswith("2026-07-27T12:00:00")


def test_an_unreadable_now_sends_no_server_side_filter():
    assert screening.min_close_epoch_seconds(now="not a time") is None
    assert screening.min_close_iso(now="not a time") is None


def test_kalshi_asks_the_venue_to_filter_but_never_for_named_legs(monkeypatch):
    """The optimisation must not become a correctness boundary in the wrong direction: a
    confirmed leg is re-read whatever its horizon, so ``min_close_ts`` is sent for discovery
    and never alongside ``tickers``.

    It also pins which endpoint each mode uses — discovery through ``/events``, a named
    re-read through ``/markets`` — because that split is what makes Kalshi usable at all.
    """
    seen: list[str] = []

    def _capture(url, **kw):
        seen.append(url)
        return {"events": []} if "/events?" in url else {"markets": []}

    monkeypatch.setattr(md, "_get_json", _capture)
    monkeypatch.setattr(md.safety_gate, "assert_authorization", lambda *a, **k: None)
    collector = md.KalshiPublicCollector(min_close_time="2026-07-27T12:00:00Z")

    collector.list_markets(limit=10, timeout_seconds=5)
    assert "/events?" in seen[-1] and "min_close_ts=" in seen[-1]
    assert "with_nested_markets=true" in seen[-1]

    collector.list_markets(limit=10, timeout_seconds=5, market_ids=["KXBTCD-1"])
    assert "/markets?" in seen[-1] and "tickers=" in seen[-1]
    assert "min_close_ts=" not in seen[-1]


def test_polymarket_asks_gamma_to_filter_but_never_for_named_legs(monkeypatch):
    seen: list[str] = []

    def _capture(url, **kw):
        seen.append(url)
        return []

    monkeypatch.setattr(md, "_get_json", _capture)
    monkeypatch.setattr(md.safety_gate, "assert_authorization", lambda *a, **k: None)
    collector = md.PolymarketPublicCollector(min_close_time="2026-07-27T12:00:00Z")

    collector.list_markets(limit=10, timeout_seconds=5)
    assert "end_date_min=" in seen[-1]

    collector.list_markets(limit=10, timeout_seconds=5, market_ids=["token"])
    assert "end_date_min=" not in seen[-1]


def test_gamma_order_flags_need_both_and_silence_stays_silence():
    """``enableOrderBook`` and ``acceptingOrders`` are independent; a market needs both, and
    a market that states neither yields ``None`` rather than ``False``."""
    def parse(extra):
        return md.parse_gamma_markets([{
            "clobTokenIds": '["tok"]', "question": "q", "endDate": "2026-08-15T00:00:00Z", **extra,
        }])[0]

    assert parse({}).accepting_orders is None
    assert parse({"enableOrderBook": True, "acceptingOrders": True}).accepting_orders is True
    assert parse({"enableOrderBook": True, "acceptingOrders": False}).accepting_orders is False
    assert parse({"enableOrderBook": False}).accepting_orders is False


def test_binance_skips_the_detail_and_book_calls_for_a_market_that_closes_too_soon(monkeypatch):
    """This venue publishes no horizon parameter, and its listing is dominated by five-minute
    markets. Screening after the fact would still be correct — and would have spent two
    signed calls per market to learn nothing."""
    listing = {"marketTopics": [
        {"marketTopicId": 4445346, "question": "Bitcoin Up or Down - 2:30AM-2:35AM ET",
         "endDate": 1785134100000},                      # 2026-07-27T06:35Z — five minutes out
        {"marketTopicId": 4209994, "question": "Will the Fed decrease interest rates?",
         "endDate": 1785369600000},                      # days out
    ]}
    calls: list[str] = []

    def _signed(self, path, params, *, timeout_seconds):
        calls.append(path)
        if path == self.LIST_PATH:
            return listing
        raise md.ToolError("TOOL_TRANSPORT", "not reached in this test")

    monkeypatch.setattr(md.BinancePredictionCollector, "_signed_get", _signed)
    monkeypatch.setattr(md.safety_gate, "assert_authorization", lambda *a, **k: None)
    collector = md.BinancePredictionCollector(min_close_time="2026-07-27T12:00:00Z")

    snapshot = collector.list_markets(limit=10, timeout_seconds=5)
    assert [m.market_id for m in snapshot.markets] == ["4209994"]
    # One detail attempt, for the survivor. The five-minute market cost nothing.
    assert calls.count(md.BinancePredictionCollector.DETAIL_PATH) == 1


def test_an_unreadable_horizon_never_silently_drops_a_market():
    """The one permissive spot in the package, and it is permissive so that the screen — not
    the collector — is what removes a market, and therefore what reports it."""
    assert md._reaches_horizon(None, "2026-07-27T12:00:00Z") is True
    assert md._reaches_horizon("2026-07-27T13:00:00Z", "garbage") is True
    assert md._reaches_horizon("2026-07-27T11:00:00Z", "2026-07-27T12:00:00Z") is False


# --- the fields survive a round trip through the snapshot -------------------------

def test_the_new_venue_facts_survive_the_snapshot_round_trip():
    """``propose`` and the watch scan both rebuild markets from a JSON snapshot. If the two
    new fields did not survive that, every rebuilt parlay would screen as an ordinary market
    and the filter would be silently off in exactly the place it runs."""
    from runtime.mvp_runtime.predmarket import observations as obs
    from runtime.mvp_runtime.predmarket import pairs_cli

    original = _market(derived_from=("A", "B"), accepting_orders=False, market_id="X")
    snapshot = {"markets": [original.as_dict()]}

    for rebuilt in (list(obs._markets_by_key(snapshot).values())[0], pairs_cli._markets_of(snapshot)[0]):
        assert rebuilt.derived_from == ("A", "B")
        assert rebuilt.accepting_orders is False
        assert screening.screen_market(rebuilt, now=NOW).observable is False

    plain = _market(market_id="Y").as_dict()
    assert pairs_cli._markets_of({"markets": [plain]})[0].derived_from is None


# --- discovery pays for nothing it does not read --------------------------------

def test_the_matcher_never_looks_at_a_price():
    """The premise the whole change rests on. `judge_pair` compares titles, close times,
    categories and cross-references; if it ever started reading a quote, discovery would be
    silently judging on data it no longer fetches."""
    import inspect

    from runtime.mvp_runtime.predmarket import matching

    source = inspect.getsource(matching)
    for priced in ("quote", "yes_bid", "yes_ask", "mid("):
        assert priced not in source, f"the matcher now reads {priced!r}; discovery must fetch books again"


def test_polymarket_discovery_makes_one_call_instead_of_one_per_market(monkeypatch):
    """A book call per market, for a number nobody reads, one HTTP round trip at a time."""
    calls: list[str] = []

    def _capture(url, **kw):
        calls.append(url)
        return [{"clobTokenIds": f'["tok{i}"]', "question": f"q{i}",
                 "endDate": "2026-12-31T00:00:00Z"} for i in range(8)] if "gamma" in url else {}

    monkeypatch.setattr(md, "_get_json", _capture)
    monkeypatch.setattr(md.safety_gate, "assert_authorization", lambda *a, **k: None)
    collector = md.PolymarketPublicCollector(pages=1)

    quoted = collector.list_markets(limit=8, timeout_seconds=5)
    books = [c for c in calls if "/book?" in c]
    assert len(books) == 8                       # one per market, for a price nobody reads
    assert quoted.quotes_requested is True

    calls.clear()
    listing = collector.list_markets(limit=8, timeout_seconds=5, with_quotes=False)
    assert [c for c in calls if "/book?" in c] == []
    assert len(listing.markets) == 8
    assert listing.quotes_requested is False

    # The remaining calls scale with PAGES, not with markets — that is the whole saving.
    calls.clear()
    md.PolymarketPublicCollector(pages=3).list_markets(
        limit=8, timeout_seconds=5, with_quotes=False)
    assert len(calls) == 3


def test_binance_discovery_keeps_the_detail_call_and_drops_only_the_book(monkeypatch):
    """`detail` carries the conditionId that becomes `group_id`, and a venue-asserted
    cross-reference is the strongest matching evidence there is. Dropping it to save a call
    would cost the one signal that cannot be fooled by two events worded alike."""
    paths: list[str] = []

    def _signed(self, path, params, *, timeout_seconds):
        paths.append(path)
        if path == self.LIST_PATH:
            return {"marketTopics": [{"marketTopicId": 7, "question": "q",
                                      "endDate": 1798779600000}]}
        if path == self.DETAIL_PATH:
            return {"markets": [{"marketId": 5, "tradingStatus": "OPEN",
                                 "conditionId": "0xabc",
                                 "outcomes": [{"name": "Yes", "tokenId": "99"}]}]}
        return {"bids": [], "asks": []}

    monkeypatch.setattr(md.BinancePredictionCollector, "_signed_get", _signed)
    monkeypatch.setattr(md.safety_gate, "assert_authorization", lambda *a, **k: None)
    collector = md.BinancePredictionCollector()

    listing = collector.list_markets(limit=5, timeout_seconds=5, with_quotes=False)
    assert md.BinancePredictionCollector.DETAIL_PATH in paths
    assert md.BinancePredictionCollector.BOOK_PATH not in paths
    # The cross-reference survived the saving — that is the point of keeping `detail`.
    assert listing.markets[0].group_id == "0xabc"
    assert listing.markets[0].market_id == "5:99"

    paths.clear()
    collector.list_markets(limit=5, timeout_seconds=5)
    assert md.BinancePredictionCollector.BOOK_PATH in paths


def test_a_listing_read_says_it_did_not_ask_for_quotes():
    """`quoted_count: 0` from a discovery read must not be readable as "nobody is quoting
    these". That is the module's own rule about absences, applied to itself."""
    collector = md.MockPredMarketCollector(KALSHI)
    snapshot, record = md.collect_pred_markets(
        KALSHI, collector=collector, now=NOW, with_quotes=False)
    assert snapshot["market_count"] > 0
    assert snapshot["quoted_count"] == 0
    assert snapshot["quotes_requested"] is False and record["quotes_requested"] is False

    quoted, _ = md.collect_pred_markets(KALSHI, collector=collector, now=NOW)
    assert quoted["quoted_count"] > 0 and quoted["quotes_requested"] is True


def test_a_listing_read_and_a_quoted_read_are_different_inputs():
    """The input hash binds the record to what was asked for. Two reads that differ only in
    whether books were fetched must not share a hash, or a cached record could answer the
    wrong question."""
    collector = md.MockPredMarketCollector(KALSHI)
    _, listing = md.collect_pred_markets(KALSHI, collector=collector, now=NOW, with_quotes=False)
    _, quoted = md.collect_pred_markets(KALSHI, collector=collector, now=NOW)
    assert listing["input_sha256"] != quoted["input_sha256"]


def test_discovery_asks_for_no_books(monkeypatch):
    """End to end: the operator's `propose` and the scheduled discovery scan share one body,
    and that body must not be fetching prices."""
    from runtime.mvp_runtime.predmarket import pairs_cli

    asked: list[bool] = []
    real = md.collect_pred_markets

    def _capture(venue, **kw):
        asked.append(kw.get("with_quotes", True))
        return real(venue, **kw)

    monkeypatch.setattr(pairs_cli, "collect_pred_markets", _capture)
    pairs_cli.run_discovery(now=NOW, venues=[KALSHI, POLYMARKET])
    assert asked == [False, False]


def test_every_venue_records_what_was_asked_for_not_what_arrived():
    """The flag exists so `quoted_count: 0` cannot be misread, and it only works if every
    collector sets it. Measuring against the live venues caught Binance reporting
    `quotes_requested: True` beside `quoted_count: 0` — exactly the misreading the field was
    added to prevent.

    Kalshi is the interesting one: `/events` returns prices inline, so a discovery read gets
    them for nothing and keeps them. The flag records what was ASKED for, and a record
    showing `quotes_requested: False` beside a real quoted count is the honest description
    of that — throwing away data already paid for would be the alternative.
    """
    for venue in (KALSHI, POLYMARKET, BINANCE):
        collector = md.MockPredMarketCollector(venue)
        listing = collector.list_markets(limit=4, timeout_seconds=5, with_quotes=False)
        quoted = collector.list_markets(limit=4, timeout_seconds=5)
        assert listing.quotes_requested is False, venue
        assert quoted.quotes_requested is True, venue


# --- an id we cannot use again --------------------------------------------------

def test_a_binance_topic_id_is_not_a_candidate():
    """Found live 2026-07-27: 72 of 92 Binance markets carried a topic id rather than the
    `marketId:tokenId` its order book needs, because `market/detail` runs only for the first
    `book_limit` topics. All 72 passed screening.

    A candidate's id is a promise — every scan after confirmation re-reads its legs by it.
    Proposing one we cannot ask about again spends the scarcest resource in the pipeline (a
    human comparing resolution rules) and then records a permanent non-reading.
    """
    topic_only = _market(BINANCE, market_id="4445372", title="Bitcoin Up or Down on July 27?")
    verdict = screening.screen_market(topic_only, now=NOW)
    assert verdict.observable is False
    assert screening.MARKET_ID_NOT_RE_READABLE in verdict.reasons

    resolved = _market(BINANCE, market_id="6797387:949728")
    assert screening.screen_market(resolved, now=NOW).observable is True


def test_the_other_venues_ids_are_re_readable_as_they_stand():
    """Kalshi tickers and Polymarket CLOB token ids are what their endpoints already take;
    only Binance needs a composite. A gate that refused them would empty the pipeline."""
    assert screening.screen_market(
        _market(KALSHI, market_id="KXPRESNOMD-28-AOC"), now=NOW).observable is True
    assert screening.screen_market(
        _market(POLYMARKET, market_id="10706498543549433311339"), now=NOW).observable is True


def test_an_empty_market_id_is_refused_on_every_venue():
    for venue in (KALSHI, POLYMARKET, BINANCE):
        verdict = screening.screen_market(_market(venue, market_id="  "), now=NOW)
        assert screening.MARKET_ID_NOT_RE_READABLE in verdict.reasons, venue


def test_the_watch_scan_can_still_price_everything_discovery_proposes():
    """The property the gate exists to guarantee, stated as a round trip: anything that
    survives screening carries an id the watch scan's own splitter accepts."""
    for market_id, usable in (("6797387:949728", True), ("4445372", False), ("5:abc", True)):
        market = _market(BINANCE, market_id=market_id)
        observable = screening.screen_market(market, now=NOW).observable
        assert observable is usable
        if observable:
            assert md.split_binance_market_id(market.market_id) is not None


# --- a confirmed leg must be re-readable, every scan ------------------------------

def test_polymarket_asks_the_venue_for_the_named_legs_rather_than_filtering_a_page(monkeypatch):
    """Found by the first confirmed group ever recorded. The named path used to fetch
    Gamma's front page and filter client-side; that group's Polymarket leg was not in the
    first 1,200 rows of the default order, so every watch scan recorded MARKET_NOT_LISTED —
    a leg an operator had personally confirmed, permanently unobservable, reported as though
    the venue had delisted it.

    `clob_token_ids` is Gamma's server-side allowlist, the exact analogue of Kalshi's
    `tickers`. Using it is not an optimisation.
    """
    calls: list[str] = []

    def _capture(url, **kw):
        calls.append(url)
        if "/book?" in url:
            return {"bids": [{"price": "0.40", "size": "10"}], "asks": [{"price": "0.44", "size": "10"}]}
        return [{"clobTokenIds": '["deep-token"]', "question": "a market nobody pages to",
                 "endDate": "2027-01-01T00:00:00Z"}]

    monkeypatch.setattr(md, "_get_json", _capture)
    monkeypatch.setattr(md.safety_gate, "assert_authorization", lambda *a, **k: None)

    snapshot = md.PolymarketPublicCollector().list_markets(
        limit=100, timeout_seconds=5, market_ids=["deep-token"])
    listing = calls[0]
    assert "clob_token_ids=deep-token" in listing
    assert "order=volumeNum" not in listing, "the discovery ordering must not leak here"
    assert [m.market_id for m in snapshot.markets] == ["deep-token"]
    assert snapshot.markets[0].quote.quoted() is True


def test_every_named_leg_gets_its_book_however_many_there_are(monkeypatch):
    """The book budget bounds discovery, where the list is whatever the venue served. A named
    leg was confirmed by an operator so that it would be measured; dropping it past an
    arbitrary cut-off puts a hole in the report's denominator that reads as a quiet book."""
    wanted = [f"tok{i}" for i in range(md.DEFAULT_BOOK_LIMIT + 8)]
    books: list[str] = []

    def _capture(url, **kw):
        if "/book?" in url:
            books.append(url)
            return {"bids": [], "asks": []}
        return [{"clobTokenIds": f'["{t}"]', "question": f"q{t}",
                 "endDate": "2027-01-01T00:00:00Z"} for t in wanted]

    monkeypatch.setattr(md, "_get_json", _capture)
    monkeypatch.setattr(md.safety_gate, "assert_authorization", lambda *a, **k: None)

    md.PolymarketPublicCollector().list_markets(
        limit=200, timeout_seconds=5, market_ids=wanted)
    assert len(books) == len(wanted)


def test_discovery_still_respects_the_book_budget(monkeypatch):
    """The other half: nothing above changed what an unbounded discovery read may spend."""
    rows = [{"clobTokenIds": f'["t{i}"]', "question": f"q{i}",
             "endDate": "2027-01-01T00:00:00Z"} for i in range(md.DEFAULT_BOOK_LIMIT + 10)]
    books: list[str] = []

    def _capture(url, **kw):
        if "/book?" in url:
            books.append(url)
            return {"bids": [], "asks": []}
        return rows

    monkeypatch.setattr(md, "_get_json", _capture)
    monkeypatch.setattr(md.safety_gate, "assert_authorization", lambda *a, **k: None)

    md.PolymarketPublicCollector(pages=1).list_markets(limit=200, timeout_seconds=5)
    assert len(books) == md.DEFAULT_BOOK_LIMIT
