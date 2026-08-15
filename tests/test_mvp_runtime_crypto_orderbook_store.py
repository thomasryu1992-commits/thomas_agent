"""Resting-liquidity history the runtime retains itself, because the venue keeps NONE.

The third store that **feeds nothing**, and the first one whose vendor offers no history at all.
That single difference is what most of these tests are about, because it removes the safety net
the other two accumulators are built on: ``positioning_store`` and ``oi_store`` can be wrong for
a while and catch up, since a gap shorter than the vendor's retention window heals on the next
refresh. Here there is nothing to ask for. A period recorded wrongly is wrong permanently, a
period not recorded is gone, and both are indistinguishable in 2029 from a venue outage.

The load-bearing cases, in the order they would bite:

1. **The row is keyed to the period, not to the instant.** A snapshot taken at 12:07 must be the
   12:00 row, or the store writes several rows per period, ``coverage`` counts them against a
   15-minute grid, and the eligibility date it reports is a fiction.
2. **A malformed level raises rather than being dropped.** The imbalance sums a side, so silently
   skipping one unreadable level produces a well-formed and WRONG number — which then becomes a
   durable row that no later reader can identify as damaged.
3. **The imbalance sign is asserted, not assumed.** It is the whole content of the series and it
   is one subtraction; a reversed sign would be invisible until a family read it in 2029.
4. **The cohort, not the routed set.** Both stores before this one shipped per-context and both
   had to be rescued (``XRPUSDT`` at zero rows, ``BNBUSDT`` frozen 31 days, 2026-08-04). There
   the fix could still backfill to the retention wall. Here it could not.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import orderbook_store
from runtime.mvp_runtime.crypto.cycle import (
    accumulate_orderbook_cohort,
    attach_feeds,
    pool_cycle_status_line,
)
from runtime.mvp_runtime.crypto.market_data import (
    CROSS_SECTION_UNIVERSE,
    FACTORY_DEPTH_DAYS,
    ORDER_BOOK_LEVELS,
    BinanceFuturesCollector,
    MockMarketDataCollector,
)
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-08-15T12:00:00Z"


def _book(bids, asks):
    return {"bids": list(bids), "asks": list(asks)}


# --- the collector's half: the venue's shape, and nothing else -------------------

def test_levels_are_parsed_to_floats_and_sorted_best_first():
    """The venue documents bids descending and asks ascending but the sort is not trusted: every
    level is summed, so ORDER alone would not move the imbalance — but the best bid and best ask
    set the spread, and a reversed side would put the worst price where the caller reads the
    best."""
    parse = BinanceFuturesCollector._parse_book_side
    bids = parse([["99.0", "1"], ["100.0", "2"]], side="bids", descending=True)
    asks = parse([["102.0", "1"], ["101.0", "2"]], side="asks", descending=False)
    assert bids[0] == (100.0, 2.0)
    assert asks[0] == (101.0, 2.0)


def test_an_unreadable_level_raises_rather_than_being_skipped():
    """Skipping it would produce a book that is well-formed and WRONG — one level lighter than
    the venue's, in a number whose only consumer sums the side — and the store would write that
    as durable evidence."""
    for bad in ([["nope", "1"]], [["100.0"]], [{"price": 1}], "not-a-list"):
        with pytest.raises(ToolError):
            BinanceFuturesCollector._parse_book_side(bad, side="bids", descending=True)


def test_a_non_positive_level_is_a_malformed_payload():
    """Zero-quantity levels are how a DIFF stream signals a removal; this is a snapshot endpoint,
    which does not send them. One arriving means the payload is not the shape this parser thinks
    it is, and guessing is the failure the store is durable enough to make permanent."""
    for bad in ([["100.0", "0"]], [["0", "1"]], [["-100.0", "1"]]):
        with pytest.raises(ToolError):
            BinanceFuturesCollector._parse_book_side(bad, side="bids", descending=True)


@pytest.mark.parametrize("limit", [21, 0, 200])
def test_an_off_grid_depth_limit_is_refused(limit):
    """The venue does not reject an unrecognized limit, it silently rounds it — and the store
    would then record a band other than the one its rows claim."""
    with pytest.raises(ToolError):
        MockMarketDataCollector().order_book("BTCUSDT", limit=limit, timeout_seconds=1)


def test_the_mock_book_leans():
    """A mock that always came out balanced would let a sign error through every test that reads
    it, which is the one bug in this module that nothing else would catch."""
    book = MockMarketDataCollector().order_book("BTCUSDT", limit=ORDER_BOOK_LEVELS, timeout_seconds=1)
    assert orderbook_store.summarize_book(book)["imbalance"] != 0.0


# --- the arithmetic: pure, so it is testable without a network -------------------

def test_imbalance_is_signed_toward_the_heavier_side():
    """The whole content of this series is one subtraction, and a reversed sign would be
    invisible until a family read it in 2029."""
    bid_heavy = orderbook_store.summarize_book(_book([(100.0, 3.0)], [(101.0, 1.0)]))
    ask_heavy = orderbook_store.summarize_book(_book([(100.0, 1.0)], [(101.0, 3.0)]))
    assert bid_heavy["imbalance"] == pytest.approx(0.5)
    assert ask_heavy["imbalance"] == pytest.approx(-0.5)


def test_a_balanced_book_is_exactly_zero():
    assert orderbook_store.summarize_book(_book([(100.0, 2.0)], [(101.0, 2.0)]))["imbalance"] == 0.0


def test_imbalance_stays_within_the_unit_interval():
    """Bounded by construction is what makes it storable as durable evidence: a ratio would be
    unbounded and a raw difference would not compare across symbols whose books differ by three
    orders of magnitude in size.

    The interval is closed, not open, and this case is why: the quotient is strictly inside it
    for any two positive sides, but ``round(..., 8)`` reaches the endpoint once one side outweighs
    the other by ~10^8. That is a real book with one side of the band effectively empty."""
    lopsided = orderbook_store.summarize_book(_book([(100.0, 1e9)], [(101.0, 1e-9)]))
    assert -1.0 <= lopsided["imbalance"] <= 1.0
    assert lopsided["imbalance"] == 1.0, "an effectively one-sided band stores as the endpoint"

    ordinary = orderbook_store.summarize_book(_book([(100.0, 1000.0)], [(101.0, 1.0)]))
    assert -1.0 < ordinary["imbalance"] < 1.0, "a merely lopsided book stays off the endpoint"


def test_depth_sums_the_whole_band_not_just_the_top():
    got = orderbook_store.summarize_book(
        _book([(100.0, 1.0), (99.0, 2.0)], [(101.0, 4.0), (102.0, 8.0)])
    )
    assert got["bid_depth"] == pytest.approx(3.0)
    assert got["ask_depth"] == pytest.approx(12.0)


def test_spread_and_mid_come_off_the_touch():
    got = orderbook_store.summarize_book(_book([(99.99, 1.0), (99.0, 5.0)], [(100.01, 1.0)]))
    assert got["mid"] == pytest.approx(100.0)
    assert got["spread_bps"] == pytest.approx(2.0)


@pytest.mark.parametrize("book", [
    _book([], [(101.0, 1.0)]),          # no bids
    _book([(100.0, 1.0)], []),          # no asks
    _book([(101.0, 1.0)], [(100.0, 1.0)]),  # crossed
    _book([(100.0, 1.0)], [(100.0, 1.0)]),  # locked
])
def test_a_book_this_cannot_describe_raises(book):
    """Not a sentinel. An empty or crossed side is not a thin market — it is a payload that does
    not mean what this function would claim, and the caller writes the result straight into an
    append-only file."""
    with pytest.raises(ToolError):
        orderbook_store.summarize_book(book)


# --- the period grid -------------------------------------------------------------

@pytest.mark.parametrize("moment,expected", [
    ("2026-08-15T12:00:00Z", "2026-08-15T12:00:00Z"),
    ("2026-08-15T12:07:31Z", "2026-08-15T12:00:00Z"),
    ("2026-08-15T12:14:59Z", "2026-08-15T12:00:00Z"),
    ("2026-08-15T12:15:00Z", "2026-08-15T12:15:00Z"),
    ("2026-08-15T23:59:59Z", "2026-08-15T23:45:00Z"),
])
def test_the_row_is_keyed_to_the_period_containing_the_snapshot(moment, expected):
    """The snapshot is taken at an arbitrary instant; the row belongs to the period. This is what
    makes a re-ask inside one period a duplicate rather than a second row, and what lets coverage
    count periods against a grid instead of counting rows."""
    assert orderbook_store.period_start(moment) == expected


def test_an_unparseable_now_raises_rather_than_guessing():
    """Every other read in the module degrades and this one must not: a row stamped to a guessed
    period is durable, wrong, and afterwards indistinguishable from a correct one."""
    with pytest.raises(ToolError):
        orderbook_store.period_start("not-a-time")


# --- the store -------------------------------------------------------------------

def _summary(imbalance=0.25):
    return {"bid_depth": 5.0, "ask_depth": 3.0, "imbalance": imbalance,
            "spread_bps": 1.5, "mid": 100.0}


def test_a_period_already_held_is_not_written_again(tmp_path):
    """Idempotent under the overlap the throttle allows: the cohort sweep and the operator's
    single-symbol cycle can both reach one symbol in one fire."""
    first = orderbook_store.append_snapshot(_summary(), symbol="BTCUSDT", timestamp=NOW, root=tmp_path)
    second = orderbook_store.append_snapshot(_summary(0.9), symbol="BTCUSDT", timestamp=NOW, root=tmp_path)
    assert (first, second) == (1, 0)
    rows = orderbook_store.read_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["imbalance"] == pytest.approx(0.25), "the FIRST snapshot in a period wins"


def test_rows_are_self_hashed(tmp_path):
    orderbook_store.append_snapshot(_summary(), symbol="BTCUSDT", timestamp=NOW, root=tmp_path)
    row = orderbook_store.read_rows(tmp_path)[0]
    assert row["record_sha256"].startswith("sha256:")
    assert row["levels"] == ORDER_BOOK_LEVELS
    assert row["period"] == orderbook_store.ORDERBOOK_PERIOD


def test_damaged_lines_are_skipped_not_raised(tmp_path):
    """The one consumer is a coverage number, and reporting less coverage than exists is the safe
    direction; a store that refused to answer would take the board down with it."""
    orderbook_store.append_snapshot(_summary(), symbol="BTCUSDT", timestamp=NOW, root=tmp_path)
    path = orderbook_store.orderbook_path(tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    assert len(orderbook_store.read_rows(tmp_path)) == 1


def test_a_row_without_a_symbol_or_timestamp_is_refused(tmp_path):
    for kwargs in ({"symbol": "", "timestamp": NOW}, {"symbol": "BTCUSDT", "timestamp": ""}):
        with pytest.raises(ToolError):
            orderbook_store.append_snapshot(_summary(), root=tmp_path, **kwargs)


# --- the throttle ----------------------------------------------------------------

def test_the_throttle_measures_from_the_last_attempt():
    """Not from the newest stored row. Here that version is sharper than the inherited reason:
    the newest row is stamped to the CURRENT period the moment the first snapshot lands, so a
    reading-age throttle would read as due the instant the period turned even if the attempt had
    already failed — twenty knocks a fire at a refusing endpoint."""
    assert orderbook_store.is_due(None, NOW) is True
    assert orderbook_store.is_due("2026-08-15T11:52:00Z", NOW) is False   # 8 min — too soon
    assert orderbook_store.is_due("2026-08-15T11:45:00Z", NOW) is True    # a full period
    assert orderbook_store.is_due("nonsense", NOW) is True                # cannot say → ask


def test_a_second_call_in_the_same_period_opens_no_socket(tmp_path):
    class _Counting(MockMarketDataCollector):
        calls = 0

        def order_book(self, symbol, *, limit, timeout_seconds):
            type(self).calls += 1
            return super().order_book(symbol, limit=limit, timeout_seconds=timeout_seconds)

    collector = _Counting()
    orderbook_store.record_orderbook(symbol="BTCUSDT", collector=collector, now=NOW, root=tmp_path)
    after_first = _Counting.calls
    result = orderbook_store.record_orderbook(
        symbol="BTCUSDT", collector=collector, now=NOW, root=tmp_path
    )
    assert _Counting.calls == after_first, "the throttle let a redundant request through"
    assert result["status"] == "skipped_fresh"


def test_a_failed_fetch_still_counts_as_an_attempt(tmp_path):
    """A marker written only on success turns a refusing endpoint into one ask per caller per
    fire. A persistent failure now costs one ask per period."""
    class _Broken(MockMarketDataCollector):
        def order_book(self, symbol, *, limit, timeout_seconds):
            raise ToolError("TOOL_TRANSPORT", "boom")

    result = orderbook_store.record_orderbook(
        symbol="BTCUSDT", collector=_Broken(), now=NOW, root=tmp_path
    )
    assert result["status"] == "degraded" and result["reason"] == "TOOL_TRANSPORT"
    assert orderbook_store.read_refresh_marks(tmp_path).get("BTCUSDT") == NOW
    assert orderbook_store.is_due(NOW, NOW) is False


def test_a_degenerate_book_degrades_rather_than_storing_it(tmp_path):
    """The venue answered, so the attempt is spent — but nothing is written. A row is durable and
    this one would be a lie about the market."""
    class _Empty(MockMarketDataCollector):
        def order_book(self, symbol, *, limit, timeout_seconds):
            return {"bids": [], "asks": []}

    result = orderbook_store.record_orderbook(
        symbol="BTCUSDT", collector=_Empty(), now=NOW, root=tmp_path
    )
    assert result["status"] == "degraded" and result["reason"] == "ORDERBOOK_SIDE_EMPTY"
    assert orderbook_store.read_rows(tmp_path) == []


def test_a_collector_without_the_capability_is_a_skip_not_an_error(tmp_path):
    class _NoCapability:
        pass

    result = orderbook_store.record_orderbook(
        symbol="BTCUSDT", collector=_NoCapability(), now=NOW, root=tmp_path
    )
    assert result == {"symbol": "BTCUSDT", "status": "skipped_no_feed", "written": 0}


# --- coverage: the entire visible output of the accumulation ---------------------

def test_coverage_measures_the_span_not_the_row_count(tmp_path):
    for stamp in ("2026-08-15T12:00:00Z", "2026-08-15T13:00:00Z"):
        orderbook_store.append_snapshot(_summary(), symbol="BTCUSDT", timestamp=stamp, root=tmp_path)
    got = orderbook_store.coverage(tmp_path, symbol="BTCUSDT")
    assert got["rows"] == 2
    # Two decimal places on a day is 14.4 minutes — just under one period at this cadence, and
    # the coarsest of the three stores' resolutions because it has the finest period. Harmless
    # for what the number decides (a 1000-day threshold) and worth pinning so a later reader
    # does not take 0.04 for a bug: it is one hour, rounded on the shared scale.
    assert got["covered_days"] == 0.04
    assert got["gap_periods"] == 3, "two rows an hour apart leave three 15m periods unfilled"
    assert got["eligible"] is False


def test_a_gap_is_permanent_and_a_later_run_does_not_close_it(tmp_path):
    """**The difference from every other store in this package.** ``positioning_store`` reports
    ``gap_periods`` as a queue the next refresh drains; here it is a fact. A later fire records
    the CURRENT period and can never reach back, because the venue serves no history — so the
    hole stays exactly the size it was."""
    orderbook_store.append_snapshot(
        _summary(), symbol="BTCUSDT", timestamp="2026-08-15T11:00:00Z", root=tmp_path
    )
    before = orderbook_store.coverage(tmp_path, symbol="BTCUSDT")["gap_periods"]
    orderbook_store.record_orderbook(
        symbol="BTCUSDT", collector=MockMarketDataCollector(), now=NOW, root=tmp_path
    )
    after = orderbook_store.coverage(tmp_path, symbol="BTCUSDT")
    assert after["rows"] == 2, "the current period was recorded"
    assert after["gap_periods"] > before, "the hole between them is not fillable, and grew"


def test_an_empty_store_reports_no_coverage(tmp_path):
    got = orderbook_store.coverage(tmp_path, symbol="BTCUSDT")
    assert got["rows"] == 0 and got["covered_days"] == 0.0 and got["eligible"] is False


def test_coverage_summary_reports_the_weakest_cell(tmp_path):
    orderbook_store.append_snapshot(_summary(), symbol="BTCUSDT", timestamp=NOW, root=tmp_path)
    summary = orderbook_store.coverage_summary(tmp_path, symbols=["BTCUSDT", "ETHUSDT"])
    assert len(summary["cells"]) == 2
    assert summary["min_covered_days"] == 0.0
    assert summary["eligible"] is False


def test_eligibility_needs_the_factory_replay_span():
    """Not a new threshold: eligibility means the store can answer every bar the factory asks
    about. 1000 days from a standing start is what the estimate priced before this was built."""
    assert orderbook_store.REQUIRED_COVERAGE_DAYS == FACTORY_DEPTH_DAYS


def test_the_stored_band_is_the_band_the_collector_fetches():
    """A store whose rows claim a band and a collector that fetches another would be undetectable
    in the data and permanent in the file."""
    assert orderbook_store.DEPTH_LEVELS == ORDER_BOOK_LEVELS


# --- the wiring ------------------------------------------------------------------

def test_accumulation_is_opt_in(tmp_path):
    """Everything else `attach_feeds` does only mutates the snapshot, so a caller that keeps no
    durable state must not get a durable store (the ``routing_marks`` rule)."""
    snapshot = {"symbol": "BTCUSDT", "timeframe": "1h", "candles": []}
    _reasons, status = attach_feeds(
        snapshot, collector=MockMarketDataCollector(), liquidation_feed=None, now=NOW, root=tmp_path,
    )
    assert status["orderbook"] == "not_accumulating"
    assert orderbook_store.read_rows(tmp_path) == []


def test_opted_in_accumulation_writes_and_feeds_nothing(tmp_path):
    """The whole shape of this increment: the store fills and the snapshot is untouched. If a key
    appeared here, the backtest and the live router would have started reading a series that is
    100% indeterminate over every replay window there will be until 2029."""
    snapshot = {"symbol": "BTCUSDT", "timeframe": "1h", "candles": []}
    before = set(snapshot)
    _reasons, status = attach_feeds(
        snapshot, collector=MockMarketDataCollector(), liquidation_feed=None, now=NOW,
        root=tmp_path, accumulate=True,
    )
    assert status["orderbook"] == "appended"
    assert orderbook_store.read_rows(tmp_path), "nothing was accumulated"
    assert set(snapshot) - before == {"funding"}, "the order-book store must feed no feature"


def test_the_cohort_is_recorded_where_the_pool_routes_nothing(tmp_path):
    """**The load-bearing case, and the one whose cost is highest here.** Both stores before this
    shipped per-context and both had to be rescued on 2026-08-04 — there the fix could still
    backfill to the vendor's retention wall. This venue has none, so a cohort member missed on
    day one is a hole in 2029's window that nothing can close."""
    status = accumulate_orderbook_cohort(
        collector=MockMarketDataCollector(), now=NOW, root=tmp_path,
        contexts=[("BTCUSDT", "4h")],
    )
    assert set(status) == set(CROSS_SECTION_UNIVERSE)
    for symbol in CROSS_SECTION_UNIVERSE:
        assert orderbook_store.read_rows(tmp_path, symbol=symbol), symbol


def test_a_routed_symbol_outside_the_cohort_is_not_dropped(tmp_path):
    """Union, not substitution — the cohort is the floor."""
    status = accumulate_orderbook_cohort(
        collector=MockMarketDataCollector(), now=NOW, root=tmp_path,
        contexts=[("ADAUSDT", "1h")],
    )
    assert "ADAUSDT" in status and set(CROSS_SECTION_UNIVERSE) <= set(status)
    assert orderbook_store.read_rows(tmp_path, symbol="ADAUSDT")


def test_the_sweep_pays_the_throttle_not_the_fan_out_rate(tmp_path):
    """Six symbols once per period, whoever asks. The 15-minute fan-out and the per-context feed
    step both reach this, and only one of them may open a socket."""
    class _Counting(MockMarketDataCollector):
        calls = 0

        def order_book(self, symbol, *, limit, timeout_seconds):
            type(self).calls += 1
            return super().order_book(symbol, limit=limit, timeout_seconds=timeout_seconds)

    collector = _Counting()
    accumulate_orderbook_cohort(
        collector=collector, now=NOW, root=tmp_path, contexts=[("BTCUSDT", "4h")],
    )
    first_sweep = _Counting.calls
    assert first_sweep == len(CROSS_SECTION_UNIVERSE)
    accumulate_orderbook_cohort(
        collector=collector, now=NOW, root=tmp_path, contexts=[("BTCUSDT", "4h")],
    )
    assert _Counting.calls == first_sweep, "the second sweep in one period must open no socket"


def test_a_lost_period_reaches_the_fire_status_line():
    """Named on the line, not counted. The positioning row earns its place because "an hour
    missed is not retryable, the vendor serves 30 days"; this venue serves none, so a lost period
    reaches the operator now or never."""
    line = pool_cycle_status_line({
        "cycles": [], "skipped": [], "unvisited": [],
        "orderbook": {"BTCUSDT": "appended", "XRPUSDT": "degraded"},
    })
    assert "orderbook-degraded=XRPUSDT" in line
    assert "BTCUSDT" not in line, "a healthy symbol must not be named"


def test_a_clean_sweep_says_nothing():
    """Silent when clean, for ``cycle_status_line``'s reason — a line that named all six on every
    quiet fire is a line an operator learns to skip."""
    line = pool_cycle_status_line({
        "cycles": [], "skipped": [], "unvisited": [],
        "orderbook": {symbol: "appended" for symbol in CROSS_SECTION_UNIVERSE},
    })
    assert "orderbook-degraded" not in line
