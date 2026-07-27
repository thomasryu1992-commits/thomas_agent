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
    and never alongside ``tickers``."""
    seen: list[str] = []

    def _capture(url, **kw):
        seen.append(url)
        return {"markets": []}

    monkeypatch.setattr(md, "_get_json", _capture)
    monkeypatch.setattr(md.safety_gate, "assert_authorization", lambda *a, **k: None)
    collector = md.KalshiPublicCollector(min_close_time="2026-07-27T12:00:00Z")

    collector.list_markets(limit=10, timeout_seconds=5)
    assert "min_close_ts=" in seen[-1]

    collector.list_markets(limit=10, timeout_seconds=5, market_ids=["KXBTCD-1"])
    assert "min_close_ts=" not in seen[-1] and "tickers=" in seen[-1]


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
