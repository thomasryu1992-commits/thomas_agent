"""Positioning history the runtime retains itself, because the vendor keeps 30 days.

The store that **feeds nothing**, which is what most of these tests are really about. There is no
feature to assert on and no family to backtest; what has to hold is that the accumulation is
honest, idempotent, self-healing, and does not write when nobody asked it to.

The load-bearing cases, in the order they would bite:

1. **The trailing incomplete period is dropped.** The store de-duplicates on
   ``(symbol, series, timestamp)`` and is append-only, so a partial reading written now would be
   the value kept *forever* while the venue's later corrected value for that same timestamp is
   discarded as a duplicate. This is the one bug that would silently poison a 500-day
   accumulation from its first hour.
2. **Accumulation is opt-in.** Every other thing `attach_feeds` does only mutates the snapshot;
   a caller that keeps no durable state must not get a durable store (the ``routing_marks``
   rule). The repo's own conftest guard caught this the first time it was wrong.
3. **Each series degrades independently**, because a partial vendor outage is the normal case and
   one endpoint refusing must not cost the other two their readings.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import positioning_store
from runtime.mvp_runtime.crypto.cycle import accumulate_positioning_cohort, attach_feeds
from runtime.mvp_runtime.crypto.market_data import (
    CROSS_SECTION_UNIVERSE,
    POSITIONING_SERIES,
    BinanceFuturesCollector,
    MockMarketDataCollector,
)
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-07-30T12:00:00Z"
HOUR_MS = 3_600_000


def _payload(rows):
    return json.dumps([
        {"symbol": "BTCUSDT", "longShortRatio": "1.5",
         "longAccount": str(long_ratio), "shortAccount": str(round(1 - long_ratio, 4)),
         "timestamp": stamp}
        for stamp, long_ratio in rows
    ])


# --- the collector -------------------------------------------------------------

def test_parses_the_account_legs_and_renames_them():
    """The venue calls both legs ``longAccount``/``shortAccount`` on every one of these
    endpoints, including the POSITION-ratio one where they carry position proportions. The rows
    are renamed on the way in so the store never stores a name that means something else."""
    raw = _payload([(1_750_000_000_000, 0.62)])
    page = BinanceFuturesCollector()._parse_positioning_page(
        raw, series="top_position", now_ms=1_750_000_000_000 + 2 * HOUR_MS, period_ms=HOUR_MS
    )
    assert page == {1_750_000_000_000: {"long_ratio": 0.62, "short_ratio": 0.38}}


def test_the_still_open_period_is_dropped():
    """The bug that would poison the accumulation from its first hour.

    The store dedupes on (symbol, series, timestamp) and is append-only, so a partial reading
    written now is the value kept forever — the venue's corrected value for that same timestamp
    arrives later and is discarded as a duplicate. Dropping it is what makes the accumulation
    converge on the venue's own numbers rather than on whatever it happened to see first."""
    start = 1_750_000_000_000
    raw = _payload([(start, 0.6), (start + HOUR_MS, 0.7)])
    # Now is halfway through the SECOND period, so only the first has closed.
    page = BinanceFuturesCollector()._parse_positioning_page(
        raw, series="top_position", now_ms=start + HOUR_MS + HOUR_MS // 2, period_ms=HOUR_MS
    )
    assert set(page) == {start}


def test_longshortratio_is_not_carried():
    """It is exactly long/short, so storing it beside both legs creates a third field that can
    disagree with the other two after rounding."""
    raw = _payload([(1_750_000_000_000, 0.62)])
    page = BinanceFuturesCollector()._parse_positioning_page(
        raw, series="top_position", now_ms=1_750_000_000_000 + 2 * HOUR_MS, period_ms=HOUR_MS
    )
    assert set(page[1_750_000_000_000]) == {"long_ratio", "short_ratio"}


def test_malformed_payload_refuses():
    with pytest.raises(ToolError):
        BinanceFuturesCollector()._parse_positioning_page(
            '[{"timestamp": "not-a-number"}]', series="top_position", now_ms=0, period_ms=HOUR_MS
        )


@pytest.mark.parametrize("bad", [
    {"series": "takerlongshortRatio", "period": "1h"},   # deliberately not collected
    {"series": "top_position", "period": "3h"},          # not a venue period
])
def test_unknown_series_or_period_refuses(bad):
    with pytest.raises(ToolError):
        MockMarketDataCollector().positioning_history(
            "BTCUSDT", limit=5, timeout_seconds=1, **bad
        )


def test_mock_series_do_not_move_together():
    """The store collects three series because large capital and account headcount can DISAGREE.
    A mock whose series moved together would make the only interesting case untestable."""
    collector = MockMarketDataCollector()
    walks = {
        series: [row["long_ratio"] for row in collector.positioning_history(
            "BTCUSDT", series=series, period="1h", limit=20, timeout_seconds=1)]
        for series in sorted(POSITIONING_SERIES)
    }
    assert len({tuple(v) for v in walks.values()}) == len(walks)


# --- the store ------------------------------------------------------------------

def test_append_is_idempotent_across_overlapping_refreshes(tmp_path):
    """A refresh overlaps the previous one by design, so writing every fetched row would grow the
    file by 72 duplicates an hour while the read-side latest-wins hid it."""
    rows = [
        {"timestamp": f"2026-07-{20 + i}T00:00:00Z", "long_ratio": 0.55, "short_ratio": 0.45}
        for i in range(3)
    ]
    first = positioning_store.append_rows(rows, symbol="BTCUSDT", series="top_position", root=tmp_path)
    second = positioning_store.append_rows(rows, symbol="BTCUSDT", series="top_position", root=tmp_path)
    assert (first, second) == (3, 0)
    assert len(positioning_store.read_rows(tmp_path)) == 3


def test_rows_are_self_hashed(tmp_path):
    from runtime.read_only_kernel import integrity

    positioning_store.append_rows(
        [{"timestamp": "2026-07-20T00:00:00Z", "long_ratio": 0.55, "short_ratio": 0.45}],
        symbol="BTCUSDT", series="top_position", root=tmp_path,
    )
    row = positioning_store.read_rows(tmp_path)[0]
    body = {k: v for k, v in row.items() if k != "record_sha256"}
    assert row["record_sha256"] == integrity.sha256_record(body)


@pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.5, True, None, "0.5"])
def test_a_ratio_outside_the_open_unit_interval_is_refused(tmp_path, bad):
    """These are proportions of a split that sums to one, so a value at or beyond the bounds is a
    broken payload — and these rows are durable evidence, so the moment to reject is before they
    become permanent."""
    written = positioning_store.append_rows(
        [{"timestamp": "2026-07-20T00:00:00Z", "long_ratio": bad, "short_ratio": 0.45}],
        symbol="BTCUSDT", series="top_position", root=tmp_path,
    )
    assert written == 0


def test_series_are_stored_independently(tmp_path):
    """The pair is the point, so the three series must not collide on one key."""
    row = [{"timestamp": "2026-07-20T00:00:00Z", "long_ratio": 0.6, "short_ratio": 0.4}]
    for series in sorted(POSITIONING_SERIES):
        positioning_store.append_rows(row, symbol="BTCUSDT", series=series, root=tmp_path)
    assert len(positioning_store.read_rows(tmp_path)) == len(POSITIONING_SERIES)
    assert len(positioning_store.read_rows(tmp_path, series="top_position")) == 1


def test_unknown_series_cannot_be_written(tmp_path):
    with pytest.raises(ToolError):
        positioning_store.append_rows([], symbol="BTCUSDT", series="invented", root=tmp_path)


def test_damaged_lines_are_skipped_not_raised(tmp_path):
    positioning_store.append_rows(
        [{"timestamp": "2026-07-20T00:00:00Z", "long_ratio": 0.6, "short_ratio": 0.4}],
        symbol="BTCUSDT", series="top_position", root=tmp_path,
    )
    path = positioning_store.positioning_path(tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    assert len(positioning_store.read_rows(tmp_path)) == 1  # the good row survives


def test_throttle_measures_from_the_last_attempt(tmp_path):
    """Not from the newest stored reading. That version is what ``oi_store`` shipped first and had
    to fix: the venue's newest COMPLETE period is always at least one period behind the clock, so
    "the newest row is over an hour old" is true for almost every minute of every hour and the
    throttle could never skip."""
    assert positioning_store.is_due(None, NOW) is True  # never asked
    assert positioning_store.is_due("2026-07-30T11:30:00Z", NOW) is False  # 30 min — too soon
    assert positioning_store.is_due("2026-07-30T11:00:00Z", NOW) is True   # a full period
    assert positioning_store.is_due("nonsense", NOW) is True  # cannot say → ask


def test_refresh_reports_per_series_and_degrades_independently(tmp_path):
    """A partial vendor outage is the normal case: one endpoint refusing must not cost the other
    two their readings, and the overall status must not report success when one failed."""
    class _PartlyBroken(MockMarketDataCollector):
        def positioning_history(self, symbol, *, series, period, limit, timeout_seconds):
            if series == "top_position":
                raise ToolError("TOOL_TRANSPORT", "boom")
            return super().positioning_history(
                symbol, series=series, period=period, limit=limit, timeout_seconds=timeout_seconds
            )

    result = positioning_store.record_positioning(
        symbol="BTCUSDT", collector=_PartlyBroken(), now=NOW, root=tmp_path
    )
    assert result["series"]["top_position"]["status"] == "degraded"
    assert result["series"]["global_account"]["status"] == "seeded"
    assert result["status"] == "degraded", "one failed series must not report as success"
    assert result["written"] > 0, "the healthy series still wrote"


def test_refresh_without_the_capability_is_a_skip_not_an_error(tmp_path):
    class _NoCapability:
        pass

    result = positioning_store.record_positioning(
        symbol="BTCUSDT", collector=_NoCapability(), now=NOW, root=tmp_path
    )
    assert result == {"symbol": "BTCUSDT", "status": "skipped_no_feed", "written": 0, "series": {}}


def test_second_call_in_the_same_period_opens_no_socket(tmp_path):
    """The throttle is what makes a twenty-context fan-out safe: only the first context after the
    period turns reaches the venue.

    Runs at the module ``NOW`` with no regard for where the mock's synthesized grid sits, and that
    independence is the point of measuring from the attempt: under the reading-age throttle this
    same test needed a ``now`` hand-aligned to the mock's January anchor, because every stored row
    read as months stale and the store judged itself due."""
    class _CountingCollector(MockMarketDataCollector):
        def __init__(self):
            self.calls = 0

        def positioning_history(self, symbol, *, series, period, limit, timeout_seconds):
            self.calls += 1
            return super().positioning_history(
                symbol, series=series, period=period, limit=limit, timeout_seconds=timeout_seconds
            )

    collector = _CountingCollector()
    positioning_store.record_positioning(
        symbol="BTCUSDT", collector=collector, now=NOW, root=tmp_path
    )
    after_seed = collector.calls
    result = positioning_store.record_positioning(
        symbol="BTCUSDT", collector=collector, now=NOW, root=tmp_path
    )
    assert collector.calls == after_seed, "the throttle let a redundant request through"
    assert result["status"] == "skipped_fresh"


# --- coverage: the number that reports progress toward eligibility ---------------

def test_coverage_measures_the_span_not_the_row_count(tmp_path):
    """A gap in the middle would flatter a row count into claiming coverage a replay could not
    use, so the span is the measure and the gap is reported beside it."""
    positioning_store.append_rows(
        [{"timestamp": "2026-01-01T00:00:00Z", "long_ratio": 0.5, "short_ratio": 0.5},
         {"timestamp": "2026-01-03T00:00:00Z", "long_ratio": 0.5, "short_ratio": 0.5}],
        symbol="BTCUSDT", series="top_position", root=tmp_path,
    )
    got = positioning_store.coverage(tmp_path, symbol="BTCUSDT", series="top_position")
    assert got["rows"] == 2
    assert got["covered_days"] == pytest.approx(2.0)
    assert got["gap_periods"] == 47, "two rows two days apart leave 47 hours unfilled"
    assert got["eligible"] is False


def test_empty_store_reports_no_coverage(tmp_path):
    got = positioning_store.coverage(tmp_path, symbol="BTCUSDT", series="top_position")
    assert got["rows"] == 0 and got["covered_days"] == 0.0 and got["eligible"] is False


def test_coverage_summary_reports_the_weakest_cell(tmp_path):
    """A source flip would be per series across every symbol the pool trades, and the pair is
    what the data is FOR — so one series being ready is not readiness."""
    positioning_store.append_rows(
        [{"timestamp": "2026-01-01T00:00:00Z", "long_ratio": 0.5, "short_ratio": 0.5}],
        symbol="BTCUSDT", series="top_position", root=tmp_path,
    )
    summary = positioning_store.coverage_summary(tmp_path, symbols=["BTCUSDT", "ETHUSDT"])
    assert len(summary["cells"]) == 2 * len(POSITIONING_SERIES)
    assert summary["min_covered_days"] == 0.0
    assert summary["eligible"] is False


def test_eligibility_needs_the_factory_replay_span(tmp_path):
    """Not a new threshold: eligibility means the store can answer every bar the factory asks
    about, and any smaller number would flip a feature source onto a window with holes."""
    from runtime.mvp_runtime.crypto.market_data import FACTORY_DEPTH_DAYS

    assert positioning_store.REQUIRED_COVERAGE_DAYS == FACTORY_DEPTH_DAYS


# --- the wiring -----------------------------------------------------------------

def test_accumulation_is_opt_in(tmp_path):
    """Everything else `attach_feeds` does only mutates the snapshot, so a caller that keeps no
    durable state must not get a durable store. The conftest guard caught this the first time it
    was wrong — ten existing call sites would have started writing real state."""
    snapshot = {"symbol": "BTCUSDT", "timeframe": "1h", "candles": []}
    _reasons, status = attach_feeds(
        snapshot, collector=MockMarketDataCollector(), liquidation_feed=None, now=NOW, root=tmp_path,
    )
    assert status["positioning"] == "not_accumulating"
    assert positioning_store.read_rows(tmp_path) == []


def test_opted_in_accumulation_writes_and_feeds_nothing(tmp_path):
    """The whole shape of this increment: the store fills, and the snapshot is untouched. If a
    key appeared here, the backtest and the live router would have started reading a series that
    is 94% indeterminate over every replay window."""
    snapshot = {"symbol": "BTCUSDT", "timeframe": "1h", "candles": []}
    before = set(snapshot)
    _reasons, status = attach_feeds(
        snapshot, collector=MockMarketDataCollector(), liquidation_feed=None, now=NOW,
        root=tmp_path, accumulate=True,
    )
    assert status["positioning"] == "seeded"
    assert positioning_store.read_rows(tmp_path), "nothing was accumulated"
    assert set(snapshot) - before == {"funding"}, "the positioning store must feed no feature"


# --- the accumulator's SCOPE ----------------------------------------------------
#
# Opting in (above) decides WHETHER a caller accumulates. These decide WHICH symbols, which
# used to be answered by accident: accumulation rode on the per-context feed step, so the
# store covered whatever the fan-out visited and a cohort member the pool stopped routing
# stopped being recorded. Measured on the live host 2026-08-04 — `XRPUSDT` at zero rows since
# the store shipped, `BNBUSDT` frozen since 2026-07-31 — and unrecoverable in both cases,
# because the vendor serves 30 days and these tests guard a 500-day accumulation.

class _CountingCollector:
    """A real mock collector that also counts what reached the venue."""

    def __init__(self, broken: str | None = None):
        self._inner = MockMarketDataCollector()
        self._broken = broken
        self.calls = 0

    def positioning_history(self, symbol, **kwargs):
        self.calls += 1
        if self._broken is not None and symbol == self._broken:
            raise ToolError("POSITIONING_UNAVAILABLE", "the venue refused this symbol")
        return self._inner.positioning_history(symbol, **kwargs)


def test_the_cohort_is_recorded_where_the_pool_routes_nothing(tmp_path):
    """**The load-bearing case.** One visited context, and every cohort member still gets its
    hour. Before this, a member the pool did not route was never asked for — and one such
    member is enough to hold `coverage_summary`'s AND shut forever, however long the rest
    accumulate."""
    status = accumulate_positioning_cohort(
        collector=MockMarketDataCollector(), now=NOW, root=tmp_path,
        contexts=[("BTCUSDT", "4h")],
    )
    assert set(status) == set(CROSS_SECTION_UNIVERSE)
    for symbol in CROSS_SECTION_UNIVERSE:
        assert positioning_store.read_rows(tmp_path, symbol=symbol), symbol


def test_a_routed_symbol_outside_the_cohort_is_not_dropped(tmp_path):
    """Union, not substitution. The cohort is the floor; a pool that grows past it must keep
    its own positioning rather than trade one coverage hole for another."""
    status = accumulate_positioning_cohort(
        collector=MockMarketDataCollector(), now=NOW, root=tmp_path,
        contexts=[("ADAUSDT", "1h")],
    )
    assert "ADAUSDT" in status and set(CROSS_SECTION_UNIVERSE) <= set(status)
    assert positioning_store.read_rows(tmp_path, symbol="ADAUSDT")


def test_the_sweep_pays_the_throttle_not_the_fan_out_rate(tmp_path):
    """Six symbols × three series once an hour, whoever asks. The 15-minute fan-out calls this
    four times an hour and three of those must open no socket — otherwise widening the scope
    would have quadrupled the request bill it was meant to leave alone."""
    collector = _CountingCollector()
    accumulate_positioning_cohort(collector=collector, now=NOW, root=tmp_path, contexts=[])
    first = collector.calls
    assert first == len(CROSS_SECTION_UNIVERSE) * len(POSITIONING_SERIES)
    accumulate_positioning_cohort(collector=collector, now=NOW, root=tmp_path, contexts=[])
    assert collector.calls == first, "the hourly throttle must answer the second fire"


def test_one_symbol_refusing_does_not_cost_the_others_their_hour(tmp_path):
    """Per-symbol isolation, for the reason `run_pool_cycle` isolates contexts: the sweep runs
    over six symbols against one venue, and an hour lost to somebody else's outage is an hour
    lost permanently."""
    collector = _CountingCollector(broken="XRPUSDT")
    status = accumulate_positioning_cohort(
        collector=collector, now=NOW, root=tmp_path, contexts=[],
    )
    assert status["XRPUSDT"] == "degraded"
    assert not positioning_store.read_rows(tmp_path, symbol="XRPUSDT")
    for symbol in (s for s in CROSS_SECTION_UNIVERSE if s != "XRPUSDT"):
        assert positioning_store.read_rows(tmp_path, symbol=symbol), symbol


# The predecessor of the two tests below asserted that NO feature column read this store — a
# tripwire whose stated job was to turn red the day someone wired it and make them read this
# module's docstring first. It did exactly that, and reading it changed the shape of the wiring
# rather than the decision to do it: the columns are honest at any coverage (absent = None, the
# `open_interest` posture), so attaching them is safe today. What is NOT safe is MINTING a family
# against a window the store cannot cover — that family would be un-scoreable, retired as FRAGILE,
# and blamed for missing data. So the tripwire moved rather than being deleted: it now guards the
# minting gate, which is where the hazard actually lives.

def test_the_columns_are_present_and_indeterminate_without_coverage(tmp_path):
    """Wired, and honest about having nothing to say. A fabricated 0.0 divergence would be the
    `liquidation_spike_ratio` hazard: in range, plausible, and matched by half of all mineable
    conditions."""
    from runtime.mvp_runtime.crypto import features
    from runtime.mvp_runtime.crypto.features import build_feature_rows

    row = build_feature_rows({
        "symbol": "BTCUSDT", "timeframe": "1h",
        "candles": [{"open_time": "2026-07-20T00:00:00Z", "close_time": "2026-07-20T01:00:00Z",
                     "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0}],
    })[0]
    for column in features.POSITIONING_COLUMNS:
        assert column in row, column
        assert row[column] is None, column


def test_the_families_are_not_mintable_until_the_store_covers_the_window():
    """**The tripwire's successor, and the load-bearing guard of this increment.** The vendor
    keeps 30 days and the factory replays 500, so a family minted now would put every trade in the
    newest walk-forward slice and none in the older ones — `temporal_consistency` 0 by
    construction, un-scoreable however real the edge. Fail closed, and the DEFAULT is the closed
    one: a caller who did not measure gets no positioning families."""
    from runtime.mvp_runtime.crypto import factory

    assert factory.POSITIONING_FAMILIES, "the families must exist to be gated"
    for timeframe in ("15m", "1h", "4h", "1d"):
        minted = {t.family for t in factory.templates_for_timeframe(timeframe)}
        assert not (factory.POSITIONING_FAMILIES & minted), timeframe
        eligible = {t.family for t in factory.templates_for_timeframe(
            timeframe, positioning_eligible=True)}
        assert factory.POSITIONING_FAMILIES <= eligible, timeframe


def test_eligibility_is_the_stores_own_measurement_not_a_new_number():
    """`REQUIRED_COVERAGE_DAYS` is the factory's replay span, so "eligible" means exactly "can
    answer every bar the factory will ask about". A smaller threshold here would be a second
    authority on how much history a feature needs."""
    from runtime.mvp_runtime.crypto.market_data import FACTORY_DEPTH_DAYS

    assert positioning_store.REQUIRED_COVERAGE_DAYS == FACTORY_DEPTH_DAYS


def test_a_store_with_only_the_vendor_window_is_not_eligible(tmp_path):
    """The situation on every machine today: the seed takes the vendor's 30 days, which is 6% of
    the replay window. Measured rather than asserted, so this stays true if either number moves."""
    for series in sorted(POSITIONING_SERIES):
        positioning_store.append_rows(
            [{"timestamp": f"2026-07-{day:02d}T00:00:00Z", "long_ratio": 0.5, "short_ratio": 0.5}
             for day in (1, 30)],
            root=tmp_path, symbol="BTCUSDT", series=series,
        )
    summary = positioning_store.coverage_summary(tmp_path, symbols=["BTCUSDT"])
    assert summary["min_covered_days"] < summary["required_days"]
    assert summary["eligible"] is False


# --- the feature columns --------------------------------------------------------

def _seed(root, *, symbol="ETHUSDT", hours=48, top=0.60, crowd=0.50, top_account=0.55):
    for hour in range(hours):
        stamp = f"2026-07-{1 + hour // 24:02d}T{hour % 24:02d}:00:00Z"
        for series, value in (("top_position", top), ("global_account", crowd),
                              ("top_account", top_account)):
            positioning_store.append_rows(
                [{"timestamp": stamp, "long_ratio": value, "short_ratio": 1.0 - value}],
                root=root, symbol=symbol, series=series,
            )


def _hourly_candles(hours=48):
    return [{"open_time": f"2026-07-{1 + h // 24:02d}T{h % 24:02d}:00:00Z",
             "close_time": f"2026-07-{1 + (h + 1) // 24:02d}T{(h + 1) % 24:02d}:00:00Z",
             "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0}
            for h in range(hours)]


def test_the_divergence_is_top_positions_against_the_whole_account_population(tmp_path):
    """The research record's specific pair — large capital's POSITIONS against every ACCOUNT.
    `top_account` is collected too and is deliberately not either leg: it counts heads inside the
    same cohort, so pairing it with `global_account` would compare headcount to headcount and
    measure nothing about capital."""
    from runtime.mvp_runtime.crypto.cycle import attach_positioning
    from runtime.mvp_runtime.crypto.features import build_feature_rows

    _seed(tmp_path, top=0.62, crowd=0.47, top_account=0.99)
    snapshot = {"symbol": "ETHUSDT", "timeframe": "1h", "candles": _hourly_candles()}
    attach_positioning(snapshot, root=tmp_path)
    row = build_feature_rows(snapshot)[-1]
    assert row["positioning_divergence"] == pytest.approx(0.62 - 0.47)
    assert row["positioning_top_account_long"] == 0.99, "collected, on the row, and not a leg"


def test_a_missing_leg_makes_the_divergence_indeterminate_not_zero(tmp_path):
    """A gap against an unknown is not zero. Only `top_position` arrives here, so there is nothing
    to measure it against — and a 0.0 divergence would be matched by half of all mineable
    conditions while meaning "we could not tell"."""
    from runtime.mvp_runtime.crypto.cycle import attach_positioning
    from runtime.mvp_runtime.crypto.features import build_feature_rows

    for hour in range(24):
        positioning_store.append_rows(
            [{"timestamp": f"2026-07-01T{hour:02d}:00:00Z", "long_ratio": 0.6, "short_ratio": 0.4}],
            root=tmp_path, symbol="ETHUSDT", series="top_position",
        )
    snapshot = {"symbol": "ETHUSDT", "timeframe": "1h", "candles": _hourly_candles(24)}
    attach_positioning(snapshot, root=tmp_path)
    row = build_feature_rows(snapshot)[-1]
    assert row["positioning_divergence"] is None
    assert row["positioning_divergence_zscore"] is None


def test_a_bar_between_two_readings_carries_the_EARLIER_one(tmp_path):
    """**Backward as-of, the funding and open-interest rule — not the exact open-time join the
    derivative-price and cross-section legs use.** The store keeps an hourly series on its own
    cadence, so a 15m bar at :15 must carry the reading at :00. An exact join would blank three
    bars in four; a FORWARD fill would leak a reading the bar could not have known."""
    from runtime.mvp_runtime.crypto.cycle import attach_positioning
    from runtime.mvp_runtime.crypto.features import build_feature_rows

    for hour, top in ((0, 0.60), (1, 0.90)):
        for series, value in (("top_position", top), ("global_account", 0.50)):
            positioning_store.append_rows(
                [{"timestamp": f"2026-07-01T{hour:02d}:00:00Z",
                  "long_ratio": value, "short_ratio": 1.0 - value}],
                root=tmp_path, symbol="ETHUSDT", series=series,
            )
    quarter = [{"open_time": f"2026-07-01T00:{m:02d}:00Z", "close_time": f"2026-07-01T00:{m + 15:02d}:00Z",
                "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0}
               for m in (0, 15, 30, 45)]
    snapshot = {"symbol": "ETHUSDT", "timeframe": "15m", "candles": quarter}
    attach_positioning(snapshot, root=tmp_path)
    rows = build_feature_rows(snapshot)
    assert [r["positioning_divergence"] for r in rows] == [pytest.approx(0.10)] * 4, (
        "every bar in the hour must carry that hour's reading, and none the next hour's"
    )


def test_the_change_is_measured_over_READINGS_not_over_bars(tmp_path):
    """The `open_interest` lesson, not rediscovered: a diff of the ALIGNED column is zero on every
    bar between two hourly readings — three in four on a 15m frame — so the derived values are
    computed in the series' own time base and aligned afterwards."""
    from runtime.mvp_runtime.crypto import features
    from runtime.mvp_runtime.crypto.cycle import attach_positioning
    from runtime.mvp_runtime.crypto.features import build_feature_rows

    hours = features.POSITIONING_CHANGE_PERIODS + 1
    for hour in range(hours):
        top = 0.50 if hour < features.POSITIONING_CHANGE_PERIODS else 0.70
        for series, value in (("top_position", top), ("global_account", 0.50)):
            positioning_store.append_rows(
                [{"timestamp": f"2026-07-{1 + hour // 24:02d}T{hour % 24:02d}:00:00Z",
                  "long_ratio": value, "short_ratio": 1.0 - value}],
                root=tmp_path, symbol="ETHUSDT", series=series,
            )
    snapshot = {"symbol": "ETHUSDT", "timeframe": "1h", "candles": _hourly_candles(hours)}
    attach_positioning(snapshot, root=tmp_path)
    rows = build_feature_rows(snapshot)
    # The newest reading is 0.20 above the one POSITIONING_CHANGE_PERIODS readings earlier.
    assert rows[-1]["positioning_divergence_change"] == pytest.approx(0.20)
    # And a bar that far back has no earlier reading to difference against — None, not 0.0.
    assert rows[0]["positioning_divergence_change"] is None


def test_the_attach_reads_only_this_symbols_rows(tmp_path):
    """The store holds every traded symbol. A frame enriched with another symbol's positioning
    would be silently wrong rather than empty, which is the worst of the two."""
    from runtime.mvp_runtime.crypto.cycle import attach_positioning

    _seed(tmp_path, symbol="ETHUSDT", hours=4, top=0.60)
    _seed(tmp_path, symbol="BTCUSDT", hours=4, top=0.20)
    snapshot = {"symbol": "ETHUSDT", "timeframe": "1h", "candles": _hourly_candles(4)}
    attach_positioning(snapshot, root=tmp_path)
    assert {r["symbol"] for r in snapshot["positioning"]} == {"ETHUSDT"}


def test_an_empty_store_leaves_the_key_absent(tmp_path):
    """No rows is the ordinary case until coverage accumulates, and it must look exactly like
    "not configured": key absent, every column None, no spec trading on it."""
    from runtime.mvp_runtime.crypto.cycle import attach_positioning

    snapshot = {"symbol": "ETHUSDT", "timeframe": "1h", "candles": _hourly_candles(4)}
    attach_positioning(snapshot, root=tmp_path)
    assert "positioning" not in snapshot


# --- the attempt marker ---------------------------------------------------------

def test_the_attempt_is_marked_even_when_the_venue_refuses(tmp_path):
    """A marker written only on success turns a refusing endpoint into one ask per context per
    fire — 240 an hour across three series and twenty contexts, to record nothing."""
    class _Broken(MockMarketDataCollector):
        def __init__(self):
            self.calls = 0

        def positioning_history(self, symbol, *, series, period, limit, timeout_seconds):
            self.calls += 1
            raise ToolError("TOOL_TRANSPORT", "boom")

    collector = _Broken()
    first = positioning_store.record_positioning(
        symbol="BTCUSDT", collector=collector, now=NOW, root=tmp_path
    )
    assert first["status"] == "degraded"
    after_failure = collector.calls
    assert after_failure == len(POSITIONING_SERIES)

    second = positioning_store.record_positioning(
        symbol="BTCUSDT", collector=collector, now=NOW, root=tmp_path
    )
    assert collector.calls == after_failure, "a failed attempt was not marked"
    assert second["status"] == "skipped_fresh"


def test_damaged_marks_read_as_ask_again(tmp_path):
    """Fail-OPEN toward asking, the opposite direction from this store's row reads. A corrupt
    marks file that read as "recently attempted" would silently stop a 500-day accumulation while
    the board went on reporting whatever coverage it had."""
    positioning_store.record_refresh_attempt("BTCUSDT", "top_position", now=NOW, root=tmp_path)
    assert positioning_store.read_refresh_marks(tmp_path)

    positioning_store.refresh_marks_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert positioning_store.read_refresh_marks(tmp_path) == {}


def test_marks_are_per_series_not_per_symbol(tmp_path):
    """Three series are asked independently, so one being fresh must not silence the others."""
    positioning_store.record_refresh_attempt("BTCUSDT", "top_position", now=NOW, root=tmp_path)
    marks = positioning_store.read_refresh_marks(tmp_path)
    assert set(marks) == {"BTCUSDT|top_position"}


def test_a_lost_marks_file_refreshes_rather_than_reseeding(tmp_path):
    """Whether a call is a seed is a property of the STORE, not of the marks: a machine whose
    marks file was deleted must not re-request 30 days it already holds."""
    asked: list[int] = []

    class _RecordingCollector(MockMarketDataCollector):
        def positioning_history(self, symbol, *, series, period, limit, timeout_seconds):
            asked.append(limit)
            return super().positioning_history(
                symbol, series=series, period=period, limit=limit, timeout_seconds=timeout_seconds
            )

    collector = _RecordingCollector()
    positioning_store.record_positioning(
        symbol="BTCUSDT", collector=collector, now=NOW, root=tmp_path
    )
    assert asked and set(asked) == {positioning_store.SEED_ROWS}

    positioning_store.refresh_marks_path(tmp_path).unlink()
    asked.clear()
    positioning_store.record_positioning(
        symbol="BTCUSDT", collector=collector, now="2026-07-30T14:00:00Z", root=tmp_path
    )
    assert set(asked) == {positioning_store.REFRESH_ROWS}, "re-seeded a store that already had rows"
