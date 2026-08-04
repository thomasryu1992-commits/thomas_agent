"""Candle archive tests.

The archive exists because Hyperliquid's 5,000-candle window ROLLS: at 15m and 1h the venue
can never serve a factory-depth window however old the market gets, so whatever is not kept
before it rolls away is unrecoverable. These tests pin the properties that make keeping it
safe — idempotent refresh, no fabricated legs, a damaged file that still answers — and the
arithmetic that says which timeframes are exposed.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import candle_archive as archive
from runtime.mvp_runtime.crypto import market_data
from runtime.mvp_runtime.errors import ToolError

VENUE = "hyperliquid"
SYMBOL = "xyz:XLE"
NOW_MS = 1_780_000_000_000


def _candle(day: int, **overrides):
    base = {
        "open_time": f"2026-08-{day:02d}T00:00:00Z",
        "close_time": f"2026-08-{day:02d}T01:00:00Z",
        "open": 58.9, "high": 60.7, "low": 58.2, "close": 59.3, "volume": 5221.48,
        "trade_count": 365,
    }
    base.update(overrides)
    return base


def test_append_then_read_round_trips(tmp_path):
    written = archive.append_candles(
        [_candle(1), _candle(2)], venue=VENUE, symbol=SYMBOL, timeframe="1h", root=tmp_path
    )
    assert written == 2
    rows = archive.read_rows(VENUE, SYMBOL, "1h", tmp_path)
    assert [r["open_time"] for r in rows] == ["2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z"]
    assert rows[0]["close"] == 59.3
    assert rows[0]["record_sha256"]


def test_a_refresh_that_overlaps_writes_only_what_is_new(tmp_path):
    # The overlap is deliberate — a request bounded exactly at the newest held bar can return
    # nothing — so re-writing what is held has to cost zero rows, not one file's worth a run.
    archive.append_candles([_candle(1), _candle(2)], venue=VENUE, symbol=SYMBOL, timeframe="1h", root=tmp_path)
    again = archive.append_candles([_candle(1), _candle(2)], venue=VENUE, symbol=SYMBOL, timeframe="1h", root=tmp_path)
    assert again == 0
    overlapping = archive.append_candles(
        [_candle(2), _candle(3)], venue=VENUE, symbol=SYMBOL, timeframe="1h", root=tmp_path
    )
    assert overlapping == 1
    assert len(archive.read_rows(VENUE, SYMBOL, "1h", tmp_path)) == 3


def test_absent_legs_are_none_and_present_ones_survive(tmp_path):
    # This venue reports no flow legs and does report a trade count. None rather than a
    # derived stand-in: the fail-closed evaluator must see indeterminate, not a fabrication.
    archive.append_candles([_candle(1)], venue=VENUE, symbol=SYMBOL, timeframe="1h", root=tmp_path)
    row = archive.read_rows(VENUE, SYMBOL, "1h", tmp_path)[0]
    assert row["quote_volume"] is None
    assert row["taker_buy_base"] is None
    assert row["taker_buy_quote"] is None
    assert row["trade_count"] == 365.0


def test_a_malformed_candle_is_dropped_rather_than_guessed_at(tmp_path):
    written = archive.append_candles(
        [_candle(1), _candle(2, close="not-a-number"), {"open_time": "x"}],
        venue=VENUE, symbol=SYMBOL, timeframe="1h", root=tmp_path,
    )
    assert written == 1
    assert [r["open_time"] for r in archive.read_rows(VENUE, SYMBOL, "1h", tmp_path)] == [
        "2026-08-01T00:00:00Z"
    ]


def test_a_damaged_file_still_answers(tmp_path):
    # The consumer is a coverage number. Reporting less than exists is the safe direction; a
    # store that refused to answer would take the board down with it.
    archive.append_candles([_candle(1)], venue=VENUE, symbol=SYMBOL, timeframe="1h", root=tmp_path)
    path = archive.archive_path(VENUE, SYMBOL, "1h", tmp_path)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("{not json\n")
        handle.write(json.dumps({"no_open_time": 1}) + "\n")
    rows = archive.read_rows(VENUE, SYMBOL, "1h", tmp_path)
    assert len(rows) == 1


def test_a_later_line_wins_so_a_correction_can_be_appended(tmp_path):
    archive.append_candles([_candle(1)], venue=VENUE, symbol=SYMBOL, timeframe="1h", root=tmp_path)
    path = archive.archive_path(VENUE, SYMBOL, "1h", tmp_path)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"open_time": "2026-08-01T00:00:00Z", "close": 99.0}) + "\n")
    assert archive.read_rows(VENUE, SYMBOL, "1h", tmp_path)[0]["close"] == 99.0


def test_the_dex_prefix_survives_into_the_filename(tmp_path):
    # `AVGO` is listed on both `xyz` and `para`. A name that dropped the prefix would merge
    # two different books into one file.
    archive.append_candles([_candle(1)], venue=VENUE, symbol="xyz:AVGO", timeframe="1h", root=tmp_path)
    archive.append_candles([_candle(2)], venue=VENUE, symbol="para:AVGO", timeframe="1h", root=tmp_path)
    assert len(archive.read_rows(VENUE, "xyz:AVGO", "1h", tmp_path)) == 1
    assert len(archive.read_rows(VENUE, "para:AVGO", "1h", tmp_path)) == 1
    assert archive.archive_path(VENUE, "xyz:AVGO", "1h", tmp_path) != archive.archive_path(
        VENUE, "para:AVGO", "1h", tmp_path
    )


@pytest.mark.parametrize("symbol", ["../escape", "xyz:XLE/../../x", "a/b", ""])
def test_a_name_that_would_escape_the_archive_is_refused(symbol):
    # The symbol comes from a venue response, so it is input.
    with pytest.raises(ToolError) as exc:
        archive.archive_path(VENUE, symbol, "1h")
    assert exc.value.reason_code == "ARCHIVE_NAME_INVALID"


def test_an_unknown_timeframe_is_refused(tmp_path):
    with pytest.raises(ToolError) as exc:
        archive.append_candles([_candle(1)], venue=VENUE, symbol=SYMBOL, timeframe="7m", root=tmp_path)
    assert exc.value.reason_code == "ARCHIVE_TIMEFRAME_INVALID"


@pytest.mark.parametrize(
    "timeframe,days,servable",
    [("15m", 52, False), ("1h", 208, False), ("4h", 833, True), ("1d", 5000, True)],
)
def test_the_ceiling_says_which_timeframes_the_venue_can_never_serve(timeframe, days, servable):
    # The load-bearing arithmetic of this whole module. Below FACTORY_DEPTH_DAYS the venue's
    # window is shallower than the factory needs and rolls forward, so the archive is the only
    # path and a gap there is permanent.
    assert round(archive.ceiling_days(timeframe)) == days
    assert (archive.ceiling_days(timeframe) >= market_data.FACTORY_DEPTH_DAYS) is servable
    coverage = archive.coverage(VENUE, SYMBOL, timeframe)
    assert coverage["venue_can_serve_factory_depth"] is servable


def test_coverage_reports_an_empty_book_without_inventing_one(tmp_path):
    coverage = archive.coverage(VENUE, SYMBOL, "1h", tmp_path)
    assert coverage["rows"] == 0
    assert coverage["oldest_open_time"] is None
    assert coverage["newest_open_time"] is None


def test_refresh_is_incremental_after_the_first_run(tmp_path):
    collector = market_data.MockMarketDataCollector()
    first = archive.refresh_book(
        collector, venue=VENUE, symbol=SYMBOL, timeframe="1h", now_ms=NOW_MS, root=tmp_path
    )
    assert first["requested"] == archive.VENUE_CANDLE_CEILING
    assert first["written"] == first["returned"] > 0

    second = archive.refresh_book(
        collector, venue=VENUE, symbol=SYMBOL, timeframe="1h", now_ms=NOW_MS, root=tmp_path
    )
    # Sized from what is already held, not from the ceiling — a steady-state run must not ask
    # for five thousand candles to learn that nothing changed.
    assert second["requested"] < first["requested"]
    assert second["written"] == 0
    assert second["degraded"] is False


def test_refresh_degrades_on_a_backend_failure_rather_than_raising(tmp_path):
    # One unreachable symbol must not take the other eighty-seven down with it, and the next
    # run refills the gap so long as it lands inside the ceiling.
    class _Unreachable:
        def collect(self, *args, **kwargs):
            raise ToolError("TOOL_TRANSPORT", "venue unreachable")

    result = archive.refresh_book(
        _Unreachable(), venue=VENUE, symbol=SYMBOL, timeframe="1h", now_ms=NOW_MS, root=tmp_path
    )
    assert result["degraded"] is True
    assert result["written"] == 0
    assert result["reason_code"] == "TOOL_TRANSPORT"
    assert archive.read_rows(VENUE, SYMBOL, "1h", tmp_path) == []


def test_the_archive_feeds_nothing():
    # `oi_store`'s posture, kept deliberately: this store accumulates and reports, and nothing
    # in the feature or routing path reads it. Re-basing a feature source under strategies that
    # can route is an explicit change, not one a depth threshold makes while nobody is looking.
    import pathlib

    runtime_dir = pathlib.Path(__file__).resolve().parents[1] / "runtime"
    hits = sorted(
        path.name
        for path in runtime_dir.rglob("*.py")
        if "candle_archive" in path.read_text(encoding="utf-8")
    )
    assert hits == ["candle_archive.py"], f"the archive gained a runtime consumer: {hits}"
