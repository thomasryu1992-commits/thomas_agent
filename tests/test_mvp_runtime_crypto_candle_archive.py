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
from runtime.mvp_runtime.errors import ToolBlocked, ToolError

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


class _RealisticCollector:
    """A non-synthetic stand-in. The Mock cannot be used here — the archive refuses
    `is_synthetic` snapshots on purpose, which the test below pins."""

    def __init__(self, bars: int = 400):
        self._bars = bars

    def collect(self, symbol, timeframe, *, limit, timeout_seconds):
        step = market_data.TIMEFRAMES[timeframe]
        candles = [
            market_data.Candle(
                open_time=f"2026-01-{1 + i // 24:02d}T{i % 24:02d}:00:00Z",
                open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0,
                close_time=f"2026-01-{1 + i // 24:02d}T{i % 24:02d}:59:59Z",
                trade_count=7.0,
            )
            for i in range(min(limit, self._bars))
        ]
        return market_data.MarketSnapshot(
            symbol=symbol, timeframe=timeframe, candles=candles,
            source="test", is_synthetic=False,
        )
        # `step` is unused deliberately: the grid only has to be unique and ordered.


def test_the_archive_refuses_a_synthetic_snapshot(tmp_path):
    # The one store whose entire value is holding what the venue really served. A synthetic
    # bar in it is indistinguishable from a real one a year later, and an empty archive is
    # recoverable where a poisoned one is not.
    result = archive.refresh_book(
        market_data.MockMarketDataCollector(),
        venue=VENUE, symbol=SYMBOL, timeframe="1h", now_ms=NOW_MS, root=tmp_path,
    )
    assert result["degraded"] is True
    assert result["reason_code"] == "ARCHIVE_REFUSES_SYNTHETIC"
    assert archive.read_rows(VENUE, SYMBOL, "1h", tmp_path) == []


def test_refresh_is_incremental_after_the_first_run(tmp_path):
    collector = _RealisticCollector()
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


def test_nothing_in_the_feature_or_routing_path_reads_the_archive():
    # `oi_store`'s posture, kept deliberately. The scheduler MAY import this module — it runs
    # the job — but re-basing a feature source under strategies that can route is an explicit
    # change, not one a depth threshold makes while nobody is looking.
    #
    # Keyed on IMPORTS rather than on the substring: `market_data` legitimately names
    # `select_candle_archive_collector`, and a test that tripped on that would be pinning
    # spelling instead of dependency.
    import pathlib
    import re

    runtime_dir = pathlib.Path(__file__).resolve().parents[1] / "runtime"
    importers = set()
    pattern = re.compile(r"^\s*(?:from\s+\.*\S*\s+import\s+[^\n]*\bcandle_archive\b"
                         r"|from\s+\S*candle_archive\s+import\b"
                         r"|import\s+\S*candle_archive\b)", re.MULTILINE)
    for path in runtime_dir.rglob("*.py"):
        if path.name == "candle_archive.py":
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            importers.add(path.name)
    assert importers <= {"scheduler.py"}, f"the archive gained a feature-path consumer: {sorted(importers)}"


# --- the archive's own selector axis, and the scheduler kind ------------------

def test_the_archive_axis_does_not_move_the_pipeline_venue(monkeypatch):
    # The property this whole axis exists for. `MVP_MARKET_DATA` names the ONE venue the
    # pipeline collects from, and the crypto pipeline's leg can place a real order — so
    # enabling the archive must not take it off Binance.
    monkeypatch.setenv(market_data.MARKET_DATA_ENV, market_data.BINANCE_FUTURES)
    monkeypatch.setenv(market_data.CANDLE_ARCHIVE_ENV, market_data.HYPERLIQUID)
    # The pipeline still asks for Binance (and fails closed here only for want of a grant,
    # which is the Binance path being chosen, not the Hyperliquid one).
    with pytest.raises(Exception) as exc:
        market_data.select_market_data_collector(now="2026-08-04T00:00:00Z")
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def test_archiving_is_off_unless_its_own_env_names_the_venue(monkeypatch):
    monkeypatch.delenv(market_data.CANDLE_ARCHIVE_ENV, raising=False)
    collector = market_data.select_candle_archive_collector()
    assert isinstance(collector, market_data.NoCandleArchiveCollector)


def test_the_inert_default_is_not_the_mock(monkeypatch):
    # Deliberate: the Mock is the right inert default for the PIPELINE, and the wrong one
    # here. A synthetic bar written into this store cannot be told from a real one later.
    monkeypatch.delenv(market_data.CANDLE_ARCHIVE_ENV, raising=False)
    collector = market_data.select_candle_archive_collector()
    assert not isinstance(collector, market_data.MockMarketDataCollector)
    with pytest.raises(ToolBlocked) as exc:
        collector.collect("xyz:XLE", "1h", limit=5, timeout_seconds=5)
    assert exc.value.reason_code == "ARCHIVE_NOT_ENABLED"


def test_opting_in_without_a_grant_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv(market_data.CANDLE_ARCHIVE_ENV, market_data.HYPERLIQUID)
    with pytest.raises(Exception) as exc:
        market_data.select_candle_archive_collector(now="2026-08-04T00:00:00Z", root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


class _FakeVenue:
    def __init__(self, symbols=("xyz:XLE", "xyz:SMH"), fail_on=()):
        self._symbols = list(symbols)
        self._fail_on = set(fail_on)

    def live_symbols(self, *, dexes, timeout_seconds=20):
        return list(self._symbols)

    def collect(self, symbol, timeframe, *, limit, timeout_seconds):
        if symbol in self._fail_on:
            raise ToolError("TOOL_TRANSPORT", "venue unreachable")
        candles = [
            market_data.Candle(
                open_time=f"2026-01-01T{i:02d}:00:00Z", open=1.0, high=2.0, low=1.0,
                close=1.5, volume=9.0, close_time=f"2026-01-01T{i:02d}:59:59Z",
            )
            for i in range(min(limit, 5))
        ]
        return market_data.MarketSnapshot(
            symbol=symbol, timeframe=timeframe, candles=candles,
            source="test", is_synthetic=False,
        )


def test_a_pass_covers_every_symbol_and_timeframe(tmp_path):
    summary = archive.run_candle_archive(
        _FakeVenue(), venue=VENUE, now_ms=NOW_MS, root=tmp_path
    )
    assert summary["symbols"] == 2
    assert summary["books"] == 2 * len(archive.ARCHIVE_TIMEFRAMES)
    assert summary["written"] == summary["books"] * 5
    assert summary["blocked"] is False


def test_one_unreachable_symbol_does_not_cost_the_others(tmp_path):
    summary = archive.run_candle_archive(
        _FakeVenue(fail_on=("xyz:SMH",)), venue=VENUE, now_ms=NOW_MS, root=tmp_path
    )
    assert summary["degraded"] == len(archive.ARCHIVE_TIMEFRAMES)
    assert summary["written"] == len(archive.ARCHIVE_TIMEFRAMES) * 5  # the other symbol kept
    assert archive.read_rows(VENUE, "xyz:XLE", "1h", tmp_path)
    assert archive.read_rows(VENUE, "xyz:SMH", "1h", tmp_path) == []


def test_a_pass_with_archiving_off_writes_nothing_and_says_why(tmp_path, monkeypatch):
    monkeypatch.delenv(market_data.CANDLE_ARCHIVE_ENV, raising=False)
    summary = archive.run_candle_archive(
        market_data.select_candle_archive_collector(), venue=VENUE, now_ms=NOW_MS, root=tmp_path
    )
    assert summary["blocked"] is True
    assert summary["reason_code"] == "ARCHIVE_NOT_ENABLED"
    assert summary["written"] == 0


def test_the_scheduler_knows_the_kind():
    from runtime.mvp_runtime import scheduler

    assert scheduler.KIND_CANDLE_ARCHIVE in scheduler.KINDS
