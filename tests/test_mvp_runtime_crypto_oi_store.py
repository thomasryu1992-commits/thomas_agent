"""The hourly OI store: retention we own, feeding nothing until it is deep enough.

The `oi_*` families read a DAILY open-interest series — `open_interest_history` requested
`interval="daily"` and `_open_interest_event_columns` says a change there is day against day
whatever bar size it aligns onto. Four of the ten lineages promoted 2026-07-29 are `oi_*`, so
four live-capable strategies time an hour off a value that steps once a day.

The switch cannot simply be made: measured on the vendor 2026-07-29, hourly history begins ~84
days back and older windows return empty, while every timeframe below 1d replays 500 calendar
days. Pointing the feed at `1hour` would leave 83% of each replay window indeterminate and
delete the evidence behind the strategies it was meant to improve.

Under test: the store accumulates without touching the snapshot, one vendor request per symbol
per hour survives a twenty-context fan-out, a re-fetch is idempotent, coverage is a SPAN rather
than a row count, and every failure direction is toward collecting less rather than raising.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from runtime.mvp_runtime.crypto import oi_store
from runtime.mvp_runtime.crypto.cycle import attach_feeds
from runtime.mvp_runtime.crypto.market_data import (
    OI_INTERVAL_1H,
    OI_INTERVAL_DAILY,
    MockMarketDataCollector,
)
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-07-29T12:00:00Z"


def _hours(count, *, start_day=28, start_hour=0, first=1000.0):
    """`count` consecutive hourly readings, oldest first."""
    rows = []
    for i in range(count):
        day = start_day + (start_hour + i) // 24
        hour = (start_hour + i) % 24
        rows.append({"timestamp": f"2026-07-{day:02d}T{hour:02d}:00:00Z",
                     "open_interest": first + i})
    return rows


def _hours_ending_before_now(count, *, hours_before_now=0):
    """`count` readings whose newest sits ``hours_before_now`` behind ``NOW``.

    The offset no longer decides freshness — the throttle measures from the last refresh
    ATTEMPT, not from the newest reading — but it is kept because the vendor's newest complete
    hour really does lag the clock, and a fixture that ignored that would test a series the
    parser never produces."""
    newest = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc) - timedelta(hours=hours_before_now)
    oldest = newest - timedelta(hours=count - 1)
    return [
        {"timestamp": (oldest + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "open_interest": 1000.0 + i}
        for i in range(count)
    ]


class _Feed:
    """Records every call so the throttle can be asserted on request count, not on effect."""

    feed_id = "coinalyze_market_data"

    def __init__(self, rows=None, error=None):
        self._rows = rows if rows is not None else _hours_ending_before_now(48)
        self._error = error
        self.calls = []

    def liquidation_history(self, symbol, *, days, timeout_seconds):
        return []

    def open_interest_history(self, symbol, *, days, timeout_seconds, interval=OI_INTERVAL_DAILY):
        self.calls.append({"symbol": symbol, "days": days, "interval": interval})
        if self._error is not None:
            raise self._error
        return self._rows


def test_a_first_refresh_seeds_and_a_second_within_the_hour_does_not_call_the_vendor(tmp_path):
    """Twenty contexts fan out at a 15-minute cadence. Without the throttle that is eighty
    vendor requests an hour to record five new readings."""
    feed = _Feed()
    first = oi_store.record_intraday_oi(symbol="BTCUSDT", feed=feed, now=NOW, root=tmp_path)
    assert first["status"] == "seeded"
    assert first["written"] == 48
    assert feed.calls[0]["interval"] == OI_INTERVAL_1H
    assert feed.calls[0]["days"] == oi_store.SEED_DAYS

    again = oi_store.record_intraday_oi(symbol="BTCUSDT", feed=feed, now=NOW, root=tmp_path)
    assert again["status"] == "skipped_fresh"
    assert len(feed.calls) == 1, "the second context must not reach the vendor"


def test_a_refresh_an_hour_later_asks_for_a_short_window(tmp_path):
    feed = _Feed()
    oi_store.record_intraday_oi(symbol="BTCUSDT", feed=feed, now=NOW, root=tmp_path)
    later = oi_store.record_intraday_oi(
        symbol="BTCUSDT", feed=feed, now="2026-07-29T13:30:00Z", root=tmp_path)
    assert later["status"] == "appended"
    assert feed.calls[-1]["days"] == oi_store.REFRESH_DAYS


def test_a_re_fetch_writes_nothing_twice(tmp_path):
    """Refreshes overlap by design. Writing every fetched row would grow the file by 72
    duplicates an hour while the read-side latest-wins hid it."""
    feed = _Feed()
    oi_store.record_intraday_oi(symbol="BTCUSDT", feed=feed, now=NOW, root=tmp_path)
    second = oi_store.append_rows(feed._rows, symbol="BTCUSDT", root=tmp_path)
    assert second == 0
    assert len(oi_store.read_rows(tmp_path, symbol="BTCUSDT")) == 48


def test_a_gap_shorter_than_the_vendor_window_heals_on_the_next_read(tmp_path):
    """The self-heal that makes an outage a delay rather than a permanent hole."""
    oi_store.append_rows(_hours(5), symbol="BTCUSDT", root=tmp_path)
    oi_store.append_rows(_hours(5, start_hour=10), symbol="BTCUSDT", root=tmp_path)
    assert oi_store.coverage(tmp_path, symbol="BTCUSDT")["gap_hours"] == 5

    oi_store.append_rows(_hours(15), symbol="BTCUSDT", root=tmp_path)
    assert oi_store.coverage(tmp_path, symbol="BTCUSDT")["gap_hours"] == 0


def test_coverage_is_a_span_not_a_row_count(tmp_path):
    """A row count divided by 24 would report a gapped store as fully covered — and gaps are
    exactly what the refresh exists to close."""
    oi_store.append_rows(_hours(1), symbol="BTCUSDT", root=tmp_path)
    oi_store.append_rows(
        [{"timestamp": "2026-07-29T00:00:00Z", "open_interest": 2000.0}],
        symbol="BTCUSDT", root=tmp_path)
    cov = oi_store.coverage(tmp_path, symbol="BTCUSDT")
    assert cov["rows"] == 2
    assert cov["covered_days"] == 1.0
    assert cov["gap_hours"] == 23


def test_an_empty_store_is_not_eligible_and_says_what_it_needs(tmp_path):
    # 1,000 since `FACTORY_DEPTH_DAYS` doubled on 2026-08-04 — this store's target IS the
    # replay window, deliberately, so the accumulation it is waiting on doubled with it. The
    # live store held ~88 days when that happened; the wait it is reporting got longer, and
    # that is a real cost of the deeper window rather than a number to pin independently.
    cov = oi_store.coverage(tmp_path, symbol="BTCUSDT")
    assert cov["rows"] == 0 and cov["eligible"] is False
    assert cov["required_days"] == oi_store.REQUIRED_COVERAGE_DAYS == 1000


def test_eligibility_takes_the_weakest_symbol(tmp_path):
    """A source switch is per timeframe across every symbol the pool trades, so the symbol with
    the least history decides — a mean would let a deep BTC series carry a thin DOGE one."""
    oi_store.append_rows(_hours(2), symbol="BTCUSDT", root=tmp_path)
    summary = oi_store.coverage_summary(tmp_path, symbols=["BTCUSDT", "DOGEUSDT"])
    assert summary["eligible"] is False
    assert summary["min_covered_days"] == 0.0
    assert [c["symbol"] for c in summary["symbols"]] == ["BTCUSDT", "DOGEUSDT"]


def test_a_vendor_failure_degrades_and_writes_nothing(tmp_path):
    """A missed hour heals on the next refresh; a raise here would take down a cycle that has
    real work to do."""
    feed = _Feed(error=ToolError("TOOL_TRANSPORT", "vendor down"))
    result = oi_store.record_intraday_oi(symbol="BTCUSDT", feed=feed, now=NOW, root=tmp_path)
    assert result["status"] == "degraded"
    assert result["reason"] == "TOOL_TRANSPORT"
    assert oi_store.read_rows(tmp_path) == []


def test_no_feed_configured_is_skipped_not_degraded(tmp_path):
    class _Absent:
        feed_id = "none"

    result = oi_store.record_intraday_oi(symbol="BTCUSDT", feed=_Absent(), now=NOW, root=tmp_path)
    assert result["status"] == "skipped_no_feed"


def test_a_corrupt_line_costs_one_hour_rather_than_the_whole_read(tmp_path):
    """This store backs a number on a board, not an approval an operator signs — so it reports
    less coverage than exists rather than refusing to answer."""
    oi_store.append_rows(_hours(3), symbol="BTCUSDT", root=tmp_path)
    path = oi_store.oi_1h_path(tmp_path)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    assert len(oi_store.read_rows(tmp_path, symbol="BTCUSDT")) == 3


def test_rows_are_hash_stamped_when_they_become_durable(tmp_path):
    oi_store.append_rows(_hours(1), symbol="BTCUSDT", root=tmp_path)
    row = json.loads(oi_store.oi_1h_path(tmp_path).read_text(encoding="utf-8").splitlines()[0])
    assert row["record_type"] == oi_store.RECORD_TYPE
    assert row["record_sha256"].startswith("sha256:")


def test_a_row_without_a_usable_reading_is_dropped_rather_than_stored_as_zero(tmp_path):
    written = oi_store.append_rows(
        [{"timestamp": "2026-07-29T00:00:00Z", "open_interest": None},
         {"timestamp": "", "open_interest": 5.0},
         {"timestamp": "2026-07-29T01:00:00Z", "open_interest": True},
         {"timestamp": "2026-07-29T02:00:00Z", "open_interest": 7.5}],
        symbol="BTCUSDT", root=tmp_path)
    assert written == 1


def test_a_symbolless_append_is_refused(tmp_path):
    with pytest.raises(ToolError) as exc:
        oi_store.append_rows(_hours(1), symbol="  ", root=tmp_path)
    assert exc.value.reason_code == "OI_SYMBOL_MISSING"


def test_the_store_feeds_nothing_into_the_snapshot(tmp_path):
    """The load-bearing property while the history builds: features, backtest and live router
    all keep reading the daily series, so the two stay identical to each other."""
    snapshot = {"symbol": "BTCUSDT", "timeframe": "1h", "candles": []}
    feed = _Feed()
    _, status = attach_feeds(snapshot, collector=MockMarketDataCollector(),
                             liquidation_feed=feed, now=NOW, root=tmp_path)

    assert "open_interest_1h" not in snapshot, "the hourly series must not reach the features"
    assert status["open_interest_1h"] == "seeded"
    # The daily fetch still happened, and it is the one the snapshot carries.
    assert any(c["interval"] == OI_INTERVAL_DAILY for c in feed.calls)
    assert oi_store.read_rows(tmp_path, symbol="BTCUSDT"), "but the store did accumulate"


def test_an_unknown_interval_is_refused_by_name():
    """A pass-through string would be answered with a series of some other cadence, and the
    store would count it as hours."""
    from runtime.mvp_runtime.crypto.market_data import CoinalyzeLiquidationFeed

    feed = CoinalyzeLiquidationFeed(authorization=None)
    with pytest.raises(ToolError) as exc:
        feed.open_interest_history("BTCUSDT", days=1, timeout_seconds=1, interval="30min")
    assert exc.value.reason_code == "OI_INTERVAL_UNKNOWN"


# --- the throttle, after it was measured and found not to throttle -------------
#
# The first version measured freshness from the newest stored READING. The vendor's newest
# complete hour is always at least an hour behind the clock, so "over an hour old" was true for
# almost every minute and all four contexts of a symbol re-asked inside one fire. It shipped,
# and the first fire after it reported four `appended` statuses per symbol with nothing
# appended. These pin the attempt-based version.

def test_four_contexts_of_one_symbol_reach_the_vendor_once(tmp_path):
    """The fan-out, as it actually runs: four timeframes per symbol, one fire."""
    feed = _Feed()
    for _ in range(4):
        oi_store.record_intraday_oi(symbol="BTCUSDT", feed=feed, now=NOW, root=tmp_path)
    assert len(feed.calls) == 1, feed.calls


def test_a_later_fire_in_the_same_hour_also_skips(tmp_path):
    """Fires are 15 minutes apart. Measuring from the reading made every one of them ask."""
    feed = _Feed()
    for minute in ("00", "15", "30", "45"):
        oi_store.record_intraday_oi(
            symbol="BTCUSDT", feed=feed, now=f"2026-07-29T12:{minute}:00Z", root=tmp_path)
    assert len(feed.calls) == 1


def test_the_next_hour_asks_again(tmp_path):
    feed = _Feed()
    oi_store.record_intraday_oi(symbol="BTCUSDT", feed=feed, now="2026-07-29T12:00:00Z", root=tmp_path)
    oi_store.record_intraday_oi(symbol="BTCUSDT", feed=feed, now="2026-07-29T13:00:00Z", root=tmp_path)
    assert len(feed.calls) == 2


def test_a_refusing_vendor_is_asked_once_an_hour_not_once_a_context(tmp_path):
    """The path that most needs the throttle. A marker written only on success would turn a
    refusing vendor into four asks per fire — sixteen an hour, which is how the cap was hit."""
    feed = _Feed(error=ToolError("TOOL_TRANSPORT", "vendor down"))
    for _ in range(4):
        r = oi_store.record_intraday_oi(symbol="BTCUSDT", feed=feed, now=NOW, root=tmp_path)
    assert len(feed.calls) == 1
    assert r["status"] == "skipped_fresh"


def test_the_attempt_is_stamped_before_the_request(tmp_path):
    """A fetch that hangs or raises still counts as an attempt, so a marker stamped after a
    successful return only would leave the failing path unthrottled."""
    class _Raises:
        feed_id = "coinalyze_market_data"

        def open_interest_history(self, symbol, *, days, timeout_seconds, interval=None):
            raise RuntimeError("boom")

    result = oi_store.record_intraday_oi(symbol="BTCUSDT", feed=_Raises(), now=NOW, root=tmp_path)
    assert result["status"] == "degraded"
    assert oi_store.read_refresh_marks(tmp_path) == {"BTCUSDT": NOW}


def test_a_corrupt_marks_file_asks_again_rather_than_stopping_accumulation(tmp_path):
    """Fail-open toward asking — the opposite of this store's read direction, on purpose. A
    marks file that read as "recently attempted" would silently stop the store growing."""
    oi_store.record_refresh_attempt("BTCUSDT", now=NOW, root=tmp_path)
    oi_store.refresh_marks_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert oi_store.read_refresh_marks(tmp_path) == {}

    feed = _Feed()
    result = oi_store.record_intraday_oi(symbol="BTCUSDT", feed=feed, now=NOW, root=tmp_path)
    assert result["status"] == "seeded"
    assert len(feed.calls) == 1


def test_one_symbol_being_fresh_does_not_hold_back_another(tmp_path):
    feed = _Feed()
    oi_store.record_intraday_oi(symbol="BTCUSDT", feed=feed, now=NOW, root=tmp_path)
    oi_store.record_intraday_oi(symbol="ETHUSDT", feed=feed, now=NOW, root=tmp_path)
    assert [c["symbol"] for c in feed.calls] == ["BTCUSDT", "ETHUSDT"]
