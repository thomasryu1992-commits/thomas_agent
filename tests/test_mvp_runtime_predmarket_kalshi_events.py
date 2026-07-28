"""Kalshi discovery through the event index — the change that made the venue usable.

Measured against the live API on 2026-07-27, and the numbers are why this exists rather
than a better filter:

===========================================  =========  =======  ==============
entrance                                     markets    parlays  two-sided quote
===========================================  =========  =======  ==============
``/markets``, paginated 8 pages deep             8,000    8,000              26
``/events?with_nested_markets=true``, 6 pages    7,840        0           7,017
===========================================  =========  =======  ==============

The flat index is not noisy, it is the wrong index: Kalshi's auto-generated ``KXMVE…``
parlay combinations fill it entirely, and no amount of paging reaches past them.

The second half of the change is smaller to write and probably larger in effect. A nested
market publishes a whole question — *"Will Klaus Iohannis be the next Secretary General of
NATO?"* — where the flat index offers only ``yes_sub_title``, *"Klaus Iohannis"*. The
matcher had been scoring that fragment against Polymarket's full sentences.
"""

from __future__ import annotations

from runtime.mvp_runtime.predmarket import market_data as md
from runtime.mvp_runtime.predmarket import matching, screening
from runtime.mvp_runtime.predmarket.market_data import KALSHI, POLYMARKET, PredMarket, VenueQuote

NOW = "2026-07-27T06:00:00Z"

# Trimmed from the live response. One multi-market event, exactly the shape that broke the
# old parser's assumption that a Kalshi market has no question of its own.
NATO_EVENT = {
    "events": [{
        "event_ticker": "KXNEXTNATOSECGEN-99",
        "series_ticker": "KXNEXTNATOSECGEN",
        "title": "Who will be the next Secretary General of NATO?",
        "sub_title": "Before Jan 1, 2099",
        "category": "Elections",
        "mutually_exclusive": True,
        "markets": [
            {"ticker": "KXNEXTNATOSECGEN-99-KIOH", "event_ticker": "KXNEXTNATOSECGEN-99",
             "title": "Will Klaus Iohannis be the next Secretary General of NATO?",
             "yes_sub_title": "Klaus Iohannis", "status": "active",
             "close_time": "2099-01-08T15:00:00Z",
             "yes_bid_dollars": "0.0800", "yes_ask_dollars": "0.1400",
             "yes_bid_size_fp": "400.00", "yes_ask_size_fp": "400.00"},
            {"ticker": "KXNEXTNATOSECGEN-99-KKAL", "event_ticker": "KXNEXTNATOSECGEN-99",
             "title": "Will Kaja Kallas be the next Secretary General of NATO?",
             "yes_sub_title": "Kaja Kallas", "status": "active",
             "close_time": "2099-01-08T15:00:00Z",
             "yes_bid_dollars": "0.0400", "yes_ask_dollars": "0.1000"},
        ],
    }],
    "cursor": "",
}


# --- the parse ------------------------------------------------------------------

def test_an_event_becomes_its_markets_not_one_market():
    """A mutually-exclusive event holds one ordinary binary market per candidate. Collapsing
    it into a single market would lose the thing being priced; skipping it because it is
    "multi-outcome" would drop the entire elections category — on both venues, since
    Polymarket structures these the same way."""
    markets = md.parse_kalshi_events(NATO_EVENT)
    assert [m.market_id for m in markets] == ["KXNEXTNATOSECGEN-99-KIOH", "KXNEXTNATOSECGEN-99-KKAL"]
    assert all(m.group_id == "KXNEXTNATOSECGEN-99" for m in markets)
    assert markets[0].quote.quoted() and markets[0].quote.yes_bid == 0.08


def test_the_market_keeps_its_own_question_rather_than_the_fragment():
    """THE fix. ``yes_sub_title`` is "Klaus Iohannis" — a name, sharing almost no vocabulary
    with the sentence the other venue asks."""
    market = md.parse_kalshi_events(NATO_EVENT)[0]
    assert market.title == "Will Klaus Iohannis be the next Secretary General of NATO?"


def test_the_event_lends_its_category_to_every_market_in_it():
    """Kalshi markets carried no category at all, so ``matching._category_agreement`` was
    permanently ``None`` on every Kalshi pairing. The event has one."""
    assert all(m.category == "Elections" for m in md.parse_kalshi_events(NATO_EVENT))


def test_a_full_question_matches_the_other_venue_where_the_fragment_did_not():
    """The end-to-end reason the title change is worth making: same two markets, same
    matcher, opposite verdict."""
    polymarket = PredMarket(
        venue=POLYMARKET, market_id="tok", group_id=None,
        title="Will Klaus Iohannis be the next Secretary General of NATO?",
        close_time="2099-01-08T15:00:00Z", status="active", category=None,
        quote=VenueQuote(yes_bid=0.10, yes_ask=0.12),
    )
    from_events = md.parse_kalshi_events(NATO_EVENT)[0]
    from_flat = md.parse_kalshi_markets({"markets": NATO_EVENT["events"][0]["markets"][:1]})[0]

    # The flat row still has `title` in this payload, so force the fragment the old index
    # actually served to show what the matcher was being handed.
    fragment = PredMarket(
        venue=KALSHI, market_id=from_flat.market_id, group_id=None,
        title="Klaus Iohannis", close_time=from_flat.close_time, status="active",
    )
    assert matching.judge_pair(fragment, polymarket).is_candidate is False
    assert matching.judge_pair(from_events, polymarket).is_candidate is True


def test_a_market_without_a_ticker_is_skipped_not_carried_anonymously():
    payload = {"events": [{"event_ticker": "E", "category": "X", "markets": [
        {"title": "no ticker here"}, {"ticker": "GOOD", "title": "keeps this one"},
    ]}]}
    assert [m.market_id for m in md.parse_kalshi_events(payload)] == ["GOOD"]


def test_a_malformed_events_payload_fails_closed():
    import pytest

    for payload in ("not an object", {"no_events_key": []}):
        with pytest.raises(md.ToolError) as exc:
            md.parse_kalshi_events(payload)
        assert exc.value.reason_code == "MALFORMED_RESULT"


def test_the_parlay_gate_still_applies_to_an_event_listing():
    """``/events`` returned zero parlays in 7,840 markets, so this never fires today. It
    stays because "the venue does not currently serve these here" is not a guarantee, and
    the gate costs one field read."""
    payload = {"events": [{"event_ticker": "E", "markets": [
        {"ticker": "P", "title": "a basket", "close_time": "2026-08-15T00:00:00Z",
         "mve_selected_legs": [{"market_ticker": "LEG-1"}]},
    ]}]}
    parsed = md.parse_kalshi_events(payload)[0]
    assert parsed.derived_from == ("LEG-1",)
    assert screening.screen_market(parsed, now=NOW).observable is False


def test_both_endpoints_produce_the_same_market():
    """A confirmed leg is re-read through ``/markets``. If the two paths disagreed about a
    market's identity or price, an observation would stop describing the thing that was
    proposed."""
    row = NATO_EVENT["events"][0]["markets"][0]
    from_events = md.parse_kalshi_events(NATO_EVENT)[0]
    from_flat = md.parse_kalshi_markets({"markets": [row]})[0]
    for field in ("market_id", "title", "close_time", "status", "quote"):
        assert getattr(from_events, field) == getattr(from_flat, field)
    # Category is the one honest difference: it lives on the event, and the flat payload has
    # none to give. `None` is "unknown", which the matcher already excludes from its vote.
    assert from_flat.category is None and from_events.category == "Elections"


# --- the read -------------------------------------------------------------------

def _collector(monkeypatch, pages):
    seen: list[str] = []
    remaining = list(pages)

    def _capture(url, **kw):
        seen.append(url)
        return remaining.pop(0) if remaining else {"events": [], "cursor": ""}

    monkeypatch.setattr(md, "_get_json", _capture)
    monkeypatch.setattr(md.safety_gate, "assert_authorization", lambda *a, **k: None)
    return md.KalshiPublicCollector(min_close_time="2026-07-27T12:00:00Z"), seen


def _page(n, cursor):
    return {"events": [{"event_ticker": f"E{n}", "category": "C", "markets": [
        {"ticker": f"E{n}-M{i}", "title": f"question {n}-{i}",
         "close_time": "2026-08-15T00:00:00Z", "status": "active"} for i in range(3)
    ]}], "cursor": cursor}


def test_discovery_pages_through_the_cursor(monkeypatch):
    collector, seen = _collector(monkeypatch, [_page(1, "c1"), _page(2, "c2"), _page(3, "")])
    snapshot = collector.list_markets(limit=7, timeout_seconds=5)
    assert len(snapshot.markets) == 7          # 3 + 3 + 3, trimmed to the caller's limit
    assert len(seen) == 3 and "cursor=c1" in seen[1] and "cursor=c2" in seen[2]


def test_discovery_keeps_sweeping_after_the_limit_is_met_because_ranking_needs_a_corpus(monkeypatch):
    """Deliberately not an early exit, and it looks like waste until you ask what the first
    three markets would be. Kalshi honours no ordering parameter, so stopping at ``limit``
    returns whichever markets the venue put first — which is how discovery ended up
    comparing 2045-dated novelties against Polymarket's 2028 primaries. You cannot rank a
    corpus you did not read."""
    collector, seen = _collector(monkeypatch, [_page(1, "c1"), _page(2, "c2"), _page(3, "")])
    assert len(collector.list_markets(limit=3, timeout_seconds=5).markets) == 3
    assert len(seen) == 3


def test_an_exhausted_cursor_ends_the_walk(monkeypatch):
    collector, seen = _collector(monkeypatch, [_page(1, "")])
    assert len(collector.list_markets(limit=100, timeout_seconds=5).markets) == 3
    assert len(seen) == 1


def test_a_cursor_that_never_advances_cannot_loop_forever(monkeypatch):
    """The runaway guard. A venue that keeps handing back the same cursor would otherwise
    page until the request timed out — on a scheduled job, indefinitely."""
    collector, seen = _collector(monkeypatch, [_page(1, "same")] * 50)
    collector.list_markets(limit=10_000, timeout_seconds=5)
    assert len(seen) == md.KalshiPublicCollector.MAX_EVENT_PAGES


# --- ranking --------------------------------------------------------------------

def _vol(market_id, volume):
    return PredMarket(venue=KALSHI, market_id=market_id, group_id=None, title=market_id,
                      close_time="2026-08-15T00:00:00Z", status="active", volume=volume)


def test_the_busiest_markets_come_first():
    ranked = md.rank_by_volume([_vol("quiet", 10.0), _vol("busy", 8_202_055.0), _vol("mid", 500.0)])
    assert [m.market_id for m in ranked] == ["busy", "mid", "quiet"]


def test_a_market_with_no_reported_volume_sorts_last_not_first():
    """Unknown is not "busy". Sorting silence to the front would let a venue's missing field
    outrank its own numbers — and discovery keeps only the head, so that market would take a
    slot from one the venue said was heavily traded."""
    ranked = md.rank_by_volume([_vol("unknown", None), _vol("quiet", 1.0), _vol("zero", 0.0)])
    assert [m.market_id for m in ranked] == ["quiet", "zero", "unknown"]


def test_ranking_is_stable_so_a_venue_with_no_volumes_is_left_alone():
    original = [_vol(f"m{i}", None) for i in range(5)]
    assert [m.market_id for m in md.rank_by_volume(original)] == [m.market_id for m in original]


def test_discovery_returns_the_busiest_markets_not_the_first_ones(monkeypatch):
    """End to end: the interesting market is on the LAST page the venue serves, which is
    exactly where the real one was."""
    def page(n, cursor, volumes):
        return {"events": [{"event_ticker": f"E{n}", "category": "C", "markets": [
            {"ticker": f"E{n}-M{i}", "title": f"q{n}-{i}", "close_time": "2026-08-15T00:00:00Z",
             "status": "active", "volume_fp": f"{v}"} for i, v in enumerate(volumes)]}],
            "cursor": cursor}

    collector, _seen = _collector(monkeypatch, [
        page(1, "c1", [1.0, 2.0]), page(2, "c2", [3.0, 4.0]), page(3, "", [9999.0, 5.0]),
    ])
    got = collector.list_markets(limit=2, timeout_seconds=5).markets
    assert [m.market_id for m in got] == ["E3-M0", "E3-M1"]
    assert got[0].volume == 9999.0


def test_polymarket_asks_gamma_for_its_busiest_first(monkeypatch):
    """Gamma, unlike Kalshi, honours an ordering parameter — so its head is one call away
    and no sweep is needed. Never sent when specific legs are named."""
    seen: list[str] = []
    monkeypatch.setattr(md, "_get_json", lambda url, **kw: (seen.append(url), [])[1])
    monkeypatch.setattr(md.safety_gate, "assert_authorization", lambda *a, **k: None)
    collector = md.PolymarketPublicCollector(min_close_time="2026-07-27T12:00:00Z")

    collector.list_markets(limit=10, timeout_seconds=5)
    assert "order=volumeNum" in seen[-1] and "ascending=false" in seen[-1]

    collector.list_markets(limit=10, timeout_seconds=5, market_ids=["tok"])
    assert "order=volumeNum" not in seen[-1]


def test_every_venue_reports_a_volume_from_its_own_field():
    """Three venues, three field names, one meaning. Never compared across venues — the
    units and histories differ — only used to rank a venue against itself."""
    kalshi = md.parse_kalshi_events({"events": [{"event_ticker": "E", "markets": [
        {"ticker": "K", "title": "q", "volume_fp": "1234.00"}]}]})[0]
    poly = md.parse_gamma_markets([
        {"clobTokenIds": '["tok"]', "question": "q", "volumeNum": 874557.51}])[0]
    binance = md.parse_prediction_topics(
        {"marketTopics": [{"marketTopicId": 1, "question": "q", "tradeVolume": "3771607.43"}]})[0]
    assert (kalshi.volume, poly.volume, binance.volume) == (1234.0, 874557.51, 3771607.43)


# --- the head, and a tail that rotates ------------------------------------------

def test_the_head_is_taken_every_run_and_the_tail_moves():
    """The two goals that used to be a choice. "Always look at the busiest markets" is where
    the cross-venue overlap lives; "cover the venue eventually" is what a capped slice loses.
    Taking the head plus one rotating window is both."""
    ranked = list(range(20))
    windows = [md.select_head_and_tail(ranked, limit=6, rotation=r) for r in range(4)]
    heads = {tuple(h) for h, _ in windows}
    assert heads == {(0, 1, 2)}, "the head must not move between runs"
    tails = [tuple(t) for _, t in windows]
    assert tails == [(3, 4, 5), (6, 7, 8), (9, 10, 11), (12, 13, 14)]


def test_the_window_wraps_and_covers_every_market_below_the_head():
    """The claim rotation makes: over enough runs, nothing below the head is never looked
    at. A window that failed to wrap would leave a permanent blind spot."""
    ranked = list(range(20))
    seen: set[int] = set()
    for rotation in range(12):
        head, tail = md.select_head_and_tail(ranked, limit=6, rotation=rotation)
        seen.update(head + tail)
    assert seen == set(ranked)


def test_a_tail_shorter_than_its_slots_is_returned_whole():
    """The honest degenerate case: there is nothing to rotate through, so rotation does
    nothing rather than dropping markets to keep a window size."""
    for rotation in (0, 1, 99):
        head, tail = md.select_head_and_tail([1, 2, 3, 4], limit=10, rotation=rotation)
        assert head + tail == [1, 2, 3, 4]


def test_nothing_to_select_from_is_not_an_error():
    assert md.select_head_and_tail([], limit=10) == ([], [])
    assert md.select_head_and_tail([1, 2, 3], limit=0) == ([], [])


def test_the_rotation_index_comes_from_the_clock_not_from_stored_state():
    """A stored counter would be one more piece of per-machine state, and it would reset on
    redeploy — leaving the window wherever a restart happened to drop it. From the clock,
    consecutive runs see consecutive windows and two machines reading at the same hour
    agree."""
    period = md.ROTATION_PERIOD_SECONDS
    first = md.rotation_index("2026-07-27T00:00:00Z", period_seconds=period)
    later = md.rotation_index("2026-07-27T06:00:00Z", period_seconds=period)
    assert later == first + 1
    # Same window twice inside one period.
    assert md.rotation_index("2026-07-27T05:59:00Z", period_seconds=period) == first


def test_an_unreadable_clock_falls_back_to_the_first_window():
    assert md.rotation_index("not a time") == 0


def test_kalshi_rotates_over_the_corpus_it_already_swept(monkeypatch):
    """This venue's sweep is already paid for — 5 calls reach ~6,500 markets — so rotating
    here costs matcher CPU and not a single extra request."""
    def page(cursor, volumes, tag):
        return {"events": [{"event_ticker": f"E{tag}", "category": "C", "markets": [
            {"ticker": f"{tag}-{i}", "title": f"q{tag}-{i}", "status": "active",
             "close_time": "2026-08-15T00:00:00Z", "volume_fp": f"{v}"}
            for i, v in enumerate(volumes)]}], "cursor": cursor}

    pages = [page("c1", [100.0, 90.0, 80.0], "A"), page("", [70.0, 60.0, 50.0], "B")]
    seen_ids = []
    for rotation in (0, 1):
        collector, _ = _collector(monkeypatch, list(pages))
        collector._rotation = rotation
        got = collector.list_markets(limit=4, timeout_seconds=5).markets
        seen_ids.append([m.market_id for m in got])

    # limit 4 -> 2 head slots, 2 tail slots over a 4-long tail: two windows.
    assert seen_ids[0] == ["A-0", "A-1", "A-2", "B-0"]
    assert seen_ids[1] == ["A-0", "A-1", "B-1", "B-2"]
    assert seen_ids[0][:2] == seen_ids[1][:2]


# --- a Binance topic is an event, not a market ------------------------------------

def test_a_binance_topic_yields_one_leg_per_candidate_each_with_its_own_name(monkeypatch):
    """End to end for the defect. The topic's `question` names ONE candidate; its markets are
    one per candidate. Taking the title from the topic and the token from "the first OPEN
    market" put Rick Caruso's name on Michael Younger's token — quoted 0.998 against
    Polymarket's 0.0005, on a market that had never traded."""
    listing = {"marketTopics": [{
        "marketTopicId": 4423246,
        "question": "Will Rick Caruso win the California Governor Election in 2026?",
        "title": "California Governor Election Winner",
        "endDate": 1793664000000,
        "feeRateBps": 200,
        "tradeVolume": "34765.02",          # the TOPIC's aggregate
        "status": "REGISTERED",
    }]}
    detail = {"markets": [
        {"marketId": 6774172, "conditionId": "0xyounger", "tradingStatus": "OPEN",
         "tradeVolume": "0", "description": "resolves on the certified result",
         "question": "Will Michael Younger win the California Governor Election in 2026?",
         "outcomes": [{"name": "YES", "tokenId": "tokY"}]},
        {"marketId": 6774162, "conditionId": "0xbecerra", "tradingStatus": "OPEN",
         "tradeVolume": "7421.14", "description": "resolves on the certified result",
         "question": "Will Xavier Becerra win the California Governor Election in 2026?",
         "outcomes": [{"name": "YES", "tokenId": "tokB"}]},
    ]}

    def _signed(self, path, params, *, timeout_seconds):
        return listing if path == self.LIST_PATH else detail

    monkeypatch.setattr(md.BinancePredictionCollector, "_signed_get", _signed)
    monkeypatch.setattr(md.safety_gate, "assert_authorization", lambda *a, **k: None)

    got = md.BinancePredictionCollector().list_markets(
        limit=10, timeout_seconds=5, with_quotes=False).markets

    assert len(got) == 2, "one leg per candidate, not one per topic"
    by_id = {m.market_id: m for m in got}
    younger, becerra = by_id["6774172:tokY"], by_id["6774162:tokB"]

    # Each leg is named after the outcome it can actually trade.
    assert "Michael Younger" in younger.title
    assert "Xavier Becerra" in becerra.title
    assert "Rick Caruso" not in younger.title + becerra.title

    # Its own volume, not the topic's $34,765 — which is what let a never-traded market pass
    # a gate built to refuse exactly that.
    assert younger.volume == 0.0 and becerra.volume == 7421.14
    assert screening.screen_market(younger, now="2026-07-28T00:00:00Z").observable is False
    assert screening.NO_TRADED_VOLUME in screening.screen_market(
        younger, now="2026-07-28T00:00:00Z").reasons

    # Its own cross-reference axis: every candidate in one race has a different conditionId.
    assert younger.group_id == "0xyounger" and becerra.group_id == "0xbecerra"

    # Facts that ARE the topic's still come from it.
    assert younger.fee_rate_bps == becerra.fee_rate_bps == 200
    assert younger.close_time == becerra.close_time


def test_binance_ranks_the_outcomes_by_their_own_volume(monkeypatch):
    """A topic's busiest candidate is not its quietest, and the first ranking only chose
    which topics were worth a signed call."""
    listing = {"marketTopics": [{"marketTopicId": 1, "question": "race",
                                 "endDate": 1793664000000, "tradeVolume": "100"}]}
    detail = {"markets": [
        {"marketId": i, "tradingStatus": "OPEN", "tradeVolume": str(v),
         "question": f"candidate {i}", "outcomes": [{"name": "YES", "tokenId": f"t{i}"}]}
        for i, v in ((1, 5.0), (2, 900.0), (3, 50.0))
    ]}

    def _signed(self, path, params, *, timeout_seconds):
        return listing if path == self.LIST_PATH else detail

    monkeypatch.setattr(md.BinancePredictionCollector, "_signed_get", _signed)
    monkeypatch.setattr(md.safety_gate, "assert_authorization", lambda *a, **k: None)

    got = md.BinancePredictionCollector().list_markets(
        limit=10, timeout_seconds=5, with_quotes=False).markets
    assert [m.volume for m in got] == [900.0, 50.0, 5.0]


def test_flattening_costs_no_extra_signed_calls(monkeypatch):
    """One `detail` per topic, as before — a topic with five candidates still costs one call.
    The discovery path fetches no books at all, so the correction is free."""
    paths: list[str] = []
    listing = {"marketTopics": [{"marketTopicId": 1, "question": "race",
                                 "endDate": 1793664000000, "tradeVolume": "100"}]}
    detail = {"markets": [
        {"marketId": i, "tradingStatus": "OPEN", "tradeVolume": "10",
         "question": f"candidate {i}", "outcomes": [{"name": "YES", "tokenId": f"t{i}"}]}
        for i in range(1, 6)
    ]}

    def _signed(self, path, params, *, timeout_seconds):
        paths.append(path)
        return listing if path == self.LIST_PATH else detail

    monkeypatch.setattr(md.BinancePredictionCollector, "_signed_get", _signed)
    monkeypatch.setattr(md.safety_gate, "assert_authorization", lambda *a, **k: None)

    got = md.BinancePredictionCollector().list_markets(
        limit=10, timeout_seconds=5, with_quotes=False).markets
    assert len(got) == 5
    assert paths.count(md.BinancePredictionCollector.DETAIL_PATH) == 1
    assert md.BinancePredictionCollector.BOOK_PATH not in paths
