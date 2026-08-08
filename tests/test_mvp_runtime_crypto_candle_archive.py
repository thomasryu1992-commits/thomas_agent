"""Candle archive tests.

The archive exists because Hyperliquid's 5,000-candle window ROLLS: at 15m and 1h the venue
can never serve a factory-depth window however old the market gets, so whatever is not kept
before it rolls away is unrecoverable. These tests pin the properties that make keeping it
safe — idempotent refresh, no fabricated legs, a damaged file that still answers — and the
arithmetic that says which timeframes are exposed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from runtime.mvp_runtime.crypto import candle_archive as archive
from runtime.mvp_runtime.crypto import market_data
from runtime.mvp_runtime.errors import ToolBlocked, ToolError
from runtime.mvp_runtime.safety_gate import Authorization

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


def test_a_row_edited_after_the_write_stops_being_believed(tmp_path):
    """The hash was written and never read, which made it a field rather than evidence.

    Skipped rather than raised on: `pool.read_candidates` raises because it gates a promotion
    ask, and this store's consumer is a coverage number where reporting less than exists is the
    safe direction.
    """
    archive.append_candles([_candle(1)], venue=VENUE, symbol=SYMBOL, timeframe="1h", root=tmp_path)
    path = archive.archive_path(VENUE, SYMBOL, "1h", tmp_path)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    row["close"] = 999_999.0                      # edit the price, leave the stored hash alone
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    assert archive.read_rows(VENUE, SYMBOL, "1h", tmp_path) == []
    assert archive.coverage(VENUE, SYMBOL, "1h", tmp_path)["tampered_rows"] == 1


def _tamper(path, *, open_time):
    """Edit one stored row's price and leave its hash alone."""
    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        row = json.loads(line)
        if row.get("open_time") == open_time:
            row["close"] = 999_999.0
        out.append(json.dumps(row))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def test_an_edited_newest_row_does_not_get_to_set_the_refresh_horizon(tmp_path):
    """`newest_open_time` verifies from the newest candidate down, and the direction is the
    safety-relevant part.

    Trusting an unverified maximum would let one edited row claim a future `open_time`, and a
    refresh sized from that asks for a single bar forever while the venue's window rolls past
    everything it is not asking for. Skipping to the newest INTACT row over-fetches at worst,
    and `append_candles` drops the overlap.
    """
    archive.append_candles([_candle(1), _candle(2), _candle(3)],
                           venue=VENUE, symbol=SYMBOL, timeframe="1h", root=tmp_path)
    _tamper(archive.archive_path(VENUE, SYMBOL, "1h", tmp_path),
            open_time="2026-08-03T00:00:00Z")

    assert archive.newest_open_time(VENUE, SYMBOL, "1h", tmp_path) == "2026-08-02T00:00:00Z"


def test_a_refetch_repairs_an_edited_row_instead_of_skipping_it(tmp_path):
    """A row that fails verification is deliberately not counted as already-held.

    It is skipped on read either way, so letting the re-fetch append a good copy lets
    latest-wins repair the book rather than leaving a hole the archive can never fill.
    """
    archive.append_candles([_candle(1)], venue=VENUE, symbol=SYMBOL, timeframe="1h", root=tmp_path)
    _tamper(archive.archive_path(VENUE, SYMBOL, "1h", tmp_path),
            open_time="2026-08-01T00:00:00Z")
    assert archive.read_rows(VENUE, SYMBOL, "1h", tmp_path) == []      # nothing believable held

    written = archive.append_candles([_candle(1)], venue=VENUE, symbol=SYMBOL,
                                     timeframe="1h", root=tmp_path)
    assert written == 1
    rows = archive.read_rows(VENUE, SYMBOL, "1h", tmp_path)
    assert len(rows) == 1 and rows[0]["close"] == 59.3
    assert archive.coverage(VENUE, SYMBOL, "1h", tmp_path)["tampered_rows"] == 1


def test_a_row_carrying_no_hash_is_kept_because_absence_is_not_a_mismatch(tmp_path):
    """Every row written before the check existed carries no hash. Treating that as tampering
    would empty every book on this machine the first time the new code ran."""
    path = archive.archive_path(VENUE, SYMBOL, "1h", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"open_time": "2026-08-01T00:00:00Z", "close": 59.3}) + "\n",
                    encoding="utf-8")
    assert len(archive.read_rows(VENUE, SYMBOL, "1h", tmp_path)) == 1
    assert archive.coverage(VENUE, SYMBOL, "1h", tmp_path)["tampered_rows"] == 0


def test_an_unreadable_line_is_counted_as_well_as_skipped(tmp_path):
    archive.append_candles([_candle(1)], venue=VENUE, symbol=SYMBOL, timeframe="1h", root=tmp_path)
    with open(archive.archive_path(VENUE, SYMBOL, "1h", tmp_path), "a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    cov = archive.coverage(VENUE, SYMBOL, "1h", tmp_path)
    assert cov["rows"] == 1 and cov["unreadable_rows"] == 1


def test_coverage_names_the_hole_the_row_count_cannot_show(tmp_path):
    """`rows`, `oldest` and `newest` are identical for a contiguous book and one missing a
    month out of its middle — so the failure this module exists to prevent was the one thing
    its report could not show."""
    archive.append_candles([_candle(d) for d in (1, 2, 3)],
                           venue=VENUE, symbol=SYMBOL, timeframe="1d", root=tmp_path)
    archive.append_candles([_candle(d) for d in (28, 29, 30)],
                           venue=VENUE, symbol=SYMBOL, timeframe="1d", root=tmp_path)
    cov = archive.coverage(VENUE, SYMBOL, "1d", tmp_path)
    assert cov["rows"] == 6
    assert cov["missing_bars"] == 24
    assert cov["largest_gap_bars"] == 24


def test_a_contiguous_book_reports_zero_and_a_book_too_small_reports_none(tmp_path):
    """Zero gap and no measurement are different answers, and the second must not read as the
    first on the number an operator watches to see a hole is not forming."""
    archive.append_candles([_candle(d) for d in (1, 2, 3)],
                           venue=VENUE, symbol=SYMBOL, timeframe="1d", root=tmp_path)
    assert archive.coverage(VENUE, SYMBOL, "1d", tmp_path)["largest_gap_bars"] == 0

    empty = archive.coverage(VENUE, "xyz:EWY", "1d", tmp_path)
    assert empty["missing_bars"] is None and empty["largest_gap_bars"] is None


def _hour(offset_hours: int) -> str:
    base = datetime(2026, 8, 4, tzinfo=timezone.utc) + timedelta(hours=offset_hours)
    return base.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_permanence_is_a_question_about_when_not_about_size(tmp_path):
    """A ten-bar hole from a year ago is as gone as a ten-thousand-bar one, while a large
    recent hole still refills — so no boolean is derived from gap size alone.

    The venue serves the newest `VENUE_CANDLE_CEILING` bars and nothing behind them.
    """
    now = datetime(2026, 8, 4, tzinfo=timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    beyond = archive.VENUE_CANDLE_CEILING + 1000       # 1h bars, so hours

    # A SMALL gap, entirely older than the window: one missing bar, unreachable.
    archive.append_candles(
        [_candle(1, open_time=_hour(-beyond)), _candle(1, open_time=_hour(-beyond + 2))],
        venue=VENUE, symbol="xyz:OLD", timeframe="1h", root=tmp_path,
    )
    old = archive.coverage(VENUE, "xyz:OLD", "1h", tmp_path, now_ms=now_ms)
    assert old["largest_gap_bars"] == 1
    assert old["unreachable_missing_bars"] == 1

    # A MUCH LARGER gap, entirely inside the window: nothing lost, the next refresh closes it.
    archive.append_candles(
        [_candle(1, open_time=_hour(-100)), _candle(1, open_time=_hour(-1))],
        venue=VENUE, symbol="xyz:NEW", timeframe="1h", root=tmp_path,
    )
    new = archive.coverage(VENUE, "xyz:NEW", "1h", tmp_path, now_ms=now_ms)
    assert new["largest_gap_bars"] == 98
    assert new["unreachable_missing_bars"] == 0


def test_without_a_clock_permanence_is_not_guessed(tmp_path):
    archive.append_candles([_candle(d) for d in (1, 30)],
                           venue=VENUE, symbol=SYMBOL, timeframe="1d", root=tmp_path)
    cov = archive.coverage(VENUE, SYMBOL, "1d", tmp_path)
    assert cov["largest_gap_bars"] == 28
    assert cov["unreachable_missing_bars"] is None


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


# `servable` moved for 4h when `FACTORY_DEPTH_DAYS` doubled to 1,000 on 2026-08-04: the venue's
# own 5,000-candle window is 833 days there, which cleared a 500-day factory and does not clear
# a 1,000-day one. So the archive is now the only path at 15m, 1h **and 4h** — three of four
# timeframes rather than two — and 1d is the only one the venue can still serve outright. The
# ceilings themselves are properties of the venue and did not move.
@pytest.mark.parametrize(
    "timeframe,days,servable",
    [("15m", 52, False), ("1h", 208, False), ("4h", 833, False), ("1d", 5000, True)],
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


def test_opting_in_needs_no_grant_but_still_carries_an_authorization(monkeypatch, tmp_path):
    # Thomas decision 2026-08-04: env-only, joining `live_trading`. The grant's 30-day TTL
    # cannot bound a job that has to run for months against a rolling window — a renewal gap
    # is not a pause, it is a hole nothing can fill.
    monkeypatch.setenv(market_data.CANDLE_ARCHIVE_ENV, market_data.HYPERLIQUID)
    collector = market_data.select_candle_archive_collector(
        now="2026-08-04T00:00:00Z", root=tmp_path
    )
    assert isinstance(collector, market_data.HyperliquidCollector)
    # The grant is what was removed; the egress re-check is not. A real Authorization means
    # `collect` still re-verifies immediately before opening a socket.
    assert isinstance(collector._authorization, Authorization)


def test_the_pipeline_still_needs_its_grant(monkeypatch, tmp_path):
    # The exception is scoped to the archive. Nothing about it may loosen the gate that
    # governs the venue the money path collects from.
    monkeypatch.setenv(market_data.MARKET_DATA_ENV, market_data.BINANCE_FUTURES)
    with pytest.raises(Exception) as exc:
        market_data.select_market_data_collector(now="2026-08-04T00:00:00Z", root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def _no_sleep(_seconds):
    """Pacing is real time; every pass in this file injects it away and asserts on the calls."""


class _FakeVenue:
    def __init__(self, symbols=("xyz:XLE", "xyz:SMH"), fail_on=(), rate_limit_after=None):
        self._symbols = list(symbols)
        self._fail_on = set(fail_on)
        self._rate_limit_after = rate_limit_after
        self.reads = []

    def live_symbols(self, *, dexes, timeout_seconds=20):
        return list(self._symbols)

    def collect(self, symbol, timeframe, *, limit, timeout_seconds):
        self.reads.append((symbol, timeframe))
        if self._rate_limit_after is not None and len(self.reads) > self._rate_limit_after:
            raise ToolError(market_data.TOOL_RATE_LIMITED, "asking too often")
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
        _FakeVenue(), venue=VENUE, now_ms=NOW_MS, root=tmp_path, sleep=_no_sleep
    )
    assert summary["symbols"] == 2
    assert summary["books"] == 2 * len(archive.ARCHIVE_TIMEFRAMES)
    assert summary["written"] == summary["books"] * 5
    assert summary["blocked"] is False
    assert summary["deferred"] == 0 and summary["skipped"] == 0


def test_one_unreachable_symbol_does_not_cost_the_others(tmp_path):
    summary = archive.run_candle_archive(
        _FakeVenue(fail_on=("xyz:SMH",)), venue=VENUE, now_ms=NOW_MS, root=tmp_path,
        sleep=_no_sleep,
    )
    assert summary["degraded"] == len(archive.ARCHIVE_TIMEFRAMES)
    assert summary["written"] == len(archive.ARCHIVE_TIMEFRAMES) * 5  # the other symbol kept
    assert archive.read_rows(VENUE, "xyz:XLE", "1h", tmp_path)
    assert archive.read_rows(VENUE, "xyz:SMH", "1h", tmp_path) == []


def test_a_pass_with_archiving_off_writes_nothing_and_says_why(tmp_path, monkeypatch):
    monkeypatch.delenv(market_data.CANDLE_ARCHIVE_ENV, raising=False)
    summary = archive.run_candle_archive(
        market_data.select_candle_archive_collector(), venue=VENUE, now_ms=NOW_MS, root=tmp_path,
        sleep=_no_sleep,
    )
    assert summary["blocked"] is True
    assert summary["reason_code"] == "ARCHIVE_NOT_ENABLED"
    assert summary["written"] == 0


# --- the venue will not answer a burst, and the tail must not pay for it -----------------
#
# Measured on the first real pass (2026-08-04T14:59:57Z): 352 reads issued as fast as the loop
# could manage, ~70 answered, 282 came back TOOL_RATE_LIMITED — and the fire still reported
# COMPLETED, because 282 != 352. The books that landed were the first twenty symbols
# alphabetically, every hour, forever: the tail was not slow to fill, it was unreachable.

def test_reads_are_paced_rather_than_burst(tmp_path):
    slept = []
    venue = _FakeVenue()
    summary = archive.run_candle_archive(
        venue, venue=VENUE, now_ms=NOW_MS, root=tmp_path,
        request_interval_seconds=0.25, sleep=slept.append,
    )
    # One wait BETWEEN each pair of reads, and none before the first — a pass of n reads
    # waits n-1 times, not n.
    assert len(slept) == summary["books"] - 1
    assert set(slept) == {0.25}


def test_a_rate_limited_book_latches_and_the_pass_stops(tmp_path):
    """A 429 is the step before a ban, so the answer is to stop asking — `PerRunFeedCache`'s
    posture. Retrying the other books would be the one response that escalates it."""
    venue = _FakeVenue(symbols=[f"xyz:S{i}" for i in range(10)], rate_limit_after=3)
    summary = archive.run_candle_archive(
        venue, venue=VENUE, now_ms=NOW_MS, root=tmp_path, sleep=_no_sleep,
    )
    assert summary["rate_limited"] is True
    assert summary["books"] == 4          # three answered, the fourth latched
    assert len(venue.reads) == 4          # and nothing was asked after it
    # 10 symbols x 4 timeframes fits the default budget, so all 40 were planned and 36 of them
    # never got asked. Not folded into `degraded`: "we never asked" is not evidence about a book.
    assert summary["skipped"] == 10 * len(archive.ARCHIVE_TIMEFRAMES) - 4
    assert summary["degraded"] == 1


def test_a_pass_is_bounded_so_it_cannot_hold_the_tick(tmp_path):
    """`run_due` is sequential and shares its tick with the live leg's `_settle_or_protect`,
    so an unbounded 352-book pass would trade position protection for archive latency."""
    venue = _FakeVenue(symbols=[f"xyz:S{i}" for i in range(10)])
    summary = archive.run_candle_archive(
        venue, venue=VENUE, now_ms=NOW_MS, root=tmp_path, books_per_pass=8, sleep=_no_sleep,
    )
    assert summary["books"] == 8
    assert len(venue.reads) == 8
    assert summary["deferred"] == 10 * len(archive.ARCHIVE_TIMEFRAMES) - 8
    assert summary["skipped"] == 0        # bounded on purpose is not the same as cut short


# --- a bounded pass spends its budget on what is perishing -------------------------------
#
# Measured 2026-08-04: the archive held ~4,753 bars of 15m (≈49 days against a 52-day ceiling)
# and 134 of 1d (the symbols' whole listed history). Symbol-major order gave a quarter of the
# symbols all four timeframes and the rest nothing — spending the budget on 1d books that
# cannot lose anything while unarchived 15m books shed ~12 bars an hour, permanently.

def _fill(tmp_path, symbol, timeframe):
    """Give a book a file, so `plan_pass` sees it as a refresh rather than a first fill."""
    archive.append_candles(
        [{"open_time": "2026-01-01T00:00:00Z", "close_time": "2026-01-01T00:14:59Z",
          "open": 1.0, "high": 2.0, "low": 1.0, "close": 1.5, "volume": 9.0}],
        venue=VENUE, symbol=symbol, timeframe=timeframe, root=tmp_path,
    )


def test_a_fresh_archive_takes_every_symbols_fastest_timeframe_first(tmp_path):
    order = archive.plan_pass(["S0", "S1", "S2"], archive.ARCHIVE_TIMEFRAMES,
                              venue=VENUE, now_ms=NOW_MS, root=tmp_path)
    assert [tf for _, tf in order[:3]] == ["15m"] * 3       # every symbol's 15m...
    assert order[3][1] == "1h"                              # ...before anyone's 1h
    assert [tf for _, tf in order] == sorted(
        [tf for _, tf in order], key=archive.ARCHIVE_TIMEFRAMES.index
    )


def test_a_first_fill_outranks_a_refresh_even_of_a_faster_timeframe(tmp_path):
    """The priority is loss, not size. An archived 15m book sheds nothing while it waits — its
    next refresh sizes from the newest bar it holds. An unarchived 1d book is not losing either,
    but it is the only one of the two that has never been captured at all."""
    _fill(tmp_path, "HELD", "15m")
    order = archive.plan_pass(["HELD"], ("15m", "1d"), venue=VENUE, now_ms=NOW_MS, root=tmp_path)
    assert order == [("HELD", "1d"), ("HELD", "15m")]


def test_the_rotating_symbol_order_survives_the_first_fill_ranking(tmp_path):
    # Stable: ranking decides the groups, the caller's rotation decides order inside them.
    rotated = ["S2", "S0", "S1"]
    order = archive.plan_pass(rotated, ("15m",), venue=VENUE, now_ms=NOW_MS, root=tmp_path)
    assert [symbol for symbol, _ in order] == rotated


# --- the slow timeframes must not fall off the end of the budget forever ------------------
#
# Ranking refreshes by timeframe as well put 4h at work-list index 176 and 1d at 264, past a
# 100-book budget whatever the symbol rotation did — that rotation moves symbols INSIDE a
# timeframe and never crosses the boundary between them. Measured on the live store
# 2026-08-05, from the deployed code:
#     within budget   : {'15m': 88, '1h': 12}
#     never attempted : {'1h': 76, '4h': 88, '1d': 88}

def test_a_fully_archived_store_still_reaches_its_slowest_timeframe(tmp_path):
    """The regression, at the size it actually happened: 88 symbols x 4 timeframes, budget 100.

    Every book exists, so every book is a refresh — which is exactly the state the live store
    reached, and exactly when ranking refreshes stops being an ordering and becomes an exclusion.
    """
    symbols = [f"S{i:02d}" for i in range(88)]
    for timeframe in archive.ARCHIVE_TIMEFRAMES:
        for symbol in symbols:
            _fill(tmp_path, symbol, timeframe)

    reached = set()
    for minute in range(24):                      # a day of the registered hourly cadence
        order = archive.plan_pass(symbols, archive.ARCHIVE_TIMEFRAMES, venue=VENUE,
                                  now_ms=NOW_MS + minute * 60 * 60_000, root=tmp_path)
        reached.update(tf for _, tf in order[:archive.ARCHIVE_BOOKS_PER_PASS])
    assert reached == set(archive.ARCHIVE_TIMEFRAMES)


def test_no_book_sits_permanently_past_the_budget(tmp_path):
    """Stronger than "4h appears sometimes": the union of the windows must cover every book.

    The offset advances one book per minute, so consecutive windows of a cadence shorter than
    the budget overlap instead of leaving gaps — while the universe holds still. The test
    below is the same property when it does not."""
    symbols = [f"S{i}" for i in range(10)]
    timeframes = ("15m", "1h", "4h", "1d")
    for timeframe in timeframes:
        for symbol in symbols:
            _fill(tmp_path, symbol, timeframe)

    budget, seen = 8, set()
    for minute in range(60):
        order = archive.plan_pass(symbols, timeframes, venue=VENUE,
                                  now_ms=NOW_MS + minute * 60_000, root=tmp_path)
        seen.update(order[:budget])
    assert seen == {(s, tf) for tf in timeframes for s in symbols}


def test_coverage_survives_a_universe_that_keeps_growing(tmp_path):
    """The offset is ``minutes % len(refreshes)``, so every listing moves the modulus and the
    window jumps rather than advancing. That is the real operating condition — the venue listed
    three symbols in three hours on 2026-08-05 — and the test above cannot see it, because it
    holds the universe still.

    **Coverage is conditional, and the condition is a race**: a new symbol inserts its books
    into the 15m region and pushes every later book forward, while the offset advances one book
    per minute. Coverage holds while the offset outruns the growth. At the registered hourly
    cadence that is 60 books per pass against 4 per listing — a 15x margin at the observed rate
    of roughly one listing an hour, which is what this models. Written as a rate rather than a
    fixed count so the margin, not just the outcome, is what fails if either side changes.
    """
    timeframes = ("15m", "1h", "4h", "1d")
    symbols = [f"S{i:02d}" for i in range(80)]
    for timeframe in timeframes:
        for symbol in symbols:
            _fill(tmp_path, symbol, timeframe)
    original = {(s, tf) for tf in timeframes for s in symbols}

    budget, seen = 100, set()
    for hour in range(12):                                   # hourly, like the registered kind
        new = f"N{hour:02d}"                                 # and one listing per pass
        symbols.append(new)
        for timeframe in timeframes:
            _fill(tmp_path, new, timeframe)
        order = archive.plan_pass(symbols, timeframes, venue=VENUE,
                                  now_ms=NOW_MS + hour * 3_600_000, root=tmp_path)
        seen.update(order[:budget])

    missed = original - seen
    assert not missed, f"{len(missed)} of the original books were never reached: {sorted(missed)[:5]}"


def test_refreshes_rotate_but_first_fills_never_do(tmp_path):
    """A first fill is losing history now, so it holds the front no matter what the clock says.
    A refresh is not, so it takes its turn."""
    symbols = [f"S{i}" for i in range(6)]
    for symbol in symbols:                    # every 1h book archived, every 15m book missing
        _fill(tmp_path, symbol, "1h")

    heads = set()
    for minute in range(6):
        order = archive.plan_pass(symbols, ("15m", "1h"), venue=VENUE,
                                  now_ms=NOW_MS + minute * 60_000, root=tmp_path)
        assert [tf for _, tf in order[:6]] == ["15m"] * 6      # the first fills, always first
        heads.add(order[6][0])                                 # the refresh half rotates
    assert len(heads) > 1


def test_a_bounded_pass_on_a_fresh_store_covers_every_symbols_15m(tmp_path):
    """End to end: the budget is smaller than the universe, and 15m still comes out whole."""
    symbols = [f"xyz:S{i}" for i in range(10)]
    venue = _FakeVenue(symbols=symbols)
    summary = archive.run_candle_archive(
        venue, venue=VENUE, now_ms=NOW_MS, root=tmp_path, books_per_pass=12, sleep=_no_sleep,
    )
    assert summary["books"] == 12
    fifteens = {symbol for symbol, timeframe in venue.reads if timeframe == "15m"}
    assert fifteens == set(symbols)          # all ten, from a budget of twelve
    assert summary["deferred"] == 10 * len(archive.ARCHIVE_TIMEFRAMES) - 12


def test_successive_passes_do_not_starve_the_same_tail(tmp_path):
    """The defect itself. A bounded pass that always started at the venue's first symbol
    reached symbols 0-1 every time and the rest never — so the fix is that the START moves."""
    symbols = [f"xyz:S{i}" for i in range(10)]
    touched = set()
    first_symbol_each_pass = []
    for minute in range(10):
        venue = _FakeVenue(symbols=symbols)
        archive.run_candle_archive(
            venue, venue=VENUE, now_ms=NOW_MS + minute * 60_000, root=tmp_path,
            books_per_pass=8, sleep=_no_sleep,
        )
        touched.update(symbol for symbol, _ in venue.reads)
        first_symbol_each_pass.append(venue.reads[0][0])
    assert touched == set(symbols)                 # nobody is unreachable any more
    assert len(set(first_symbol_each_pass)) > 1    # and the start really does move


def test_the_scheduler_knows_the_kind():
    from runtime.mvp_runtime import scheduler

    assert scheduler.KIND_CANDLE_ARCHIVE in scheduler.KINDS


# --- off vs broken: the defect this increment fixes --------------------------
#
# `_notify_status_change` alerts on a FAILED fire and on recovery from one. A summary string
# is a COMPLETED fire and reaches nobody. Reporting "blocked" for both states — which the
# handler did until 2026-08-04 — meant an archive that stopped working announced it only
# inside a completion nobody is told about, while the window it races kept rolling.

def _fire(schedule_kind, tmp_path, monkeypatch):
    from runtime.mvp_runtime import scheduler

    monkeypatch.setattr("time.time", lambda: NOW_MS / 1000.0)
    # The archive paces its reads in real seconds. Resolved at call time inside
    # `run_candle_archive`, so patching it here is what keeps these fires instant.
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    schedule = scheduler.Schedule(
        schedule_id="s1", kind=schedule_kind, request="", interval_seconds=86400,
        enabled=True, created_by="test", created_at="2026-08-04T00:00:00Z",
        next_run_at="2026-08-04T00:00:00Z",
    )
    return scheduler._execute(
        schedule, now="2026-08-04T00:00:00Z", ledger=None, working_memory=None,
        programization=None, registry=None, provider=None, search_tool=None,
        repo_root=tmp_path, executor=None,
    )


def test_archiving_switched_off_is_a_quiet_completion(tmp_path, monkeypatch):
    # Off on purpose must NOT alert. A machine that never enabled archiving would otherwise
    # raise a failure every cadence, and an alert that always fires stops being read.
    monkeypatch.delenv(market_data.CANDLE_ARCHIVE_ENV, raising=False)
    from runtime.mvp_runtime import scheduler

    assert _fire(scheduler.KIND_CANDLE_ARCHIVE, tmp_path, monkeypatch) == "candle_archive=off"


def test_archiving_on_but_unable_to_read_the_universe_raises(tmp_path, monkeypatch):
    # The state the old handler hid. Enabled, not working, and the window keeps rolling —
    # so this has to become a FAILED fire, which is the only thing the alert path carries.
    from runtime.mvp_runtime import scheduler
    from runtime.mvp_runtime.crypto import market_data as md

    monkeypatch.setenv(md.CANDLE_ARCHIVE_ENV, md.HYPERLIQUID)

    class _Unreachable(md.HyperliquidCollector):
        def live_symbols(self, *, dexes, timeout_seconds=20):
            raise ToolError("TOOL_TRANSPORT", "venue unreachable")

    monkeypatch.setattr(
        md, "select_candle_archive_collector",
        lambda **kwargs: _Unreachable(authorization=None),
    )
    with pytest.raises(Exception) as exc:
        _fire(scheduler.KIND_CANDLE_ARCHIVE, tmp_path, monkeypatch)
    assert exc.value.reason_code == "ARCHIVE_UNIVERSE_UNREADABLE"


def test_a_universe_that_answers_but_no_book_that_does_raises(tmp_path, monkeypatch):
    # An outage rather than a slow day: every symbol degraded. Silence here costs exactly
    # what this store exists to prevent.
    from runtime.mvp_runtime import scheduler
    from runtime.mvp_runtime.crypto import market_data as md

    monkeypatch.setenv(md.CANDLE_ARCHIVE_ENV, md.HYPERLIQUID)
    monkeypatch.setattr(
        md, "select_candle_archive_collector",
        lambda **kwargs: _FakeVenue(fail_on=("xyz:XLE", "xyz:SMH")),
    )
    with pytest.raises(Exception) as exc:
        _fire(scheduler.KIND_CANDLE_ARCHIVE, tmp_path, monkeypatch)
    assert exc.value.reason_code == "ARCHIVE_ALL_BOOKS_DEGRADED"


def test_a_partial_outage_reports_without_raising(tmp_path, monkeypatch):
    # One symbol down is not an incident — the next pass refills it inside the ceiling, and
    # raising here would spend the operator's attention on something that self-heals.
    from runtime.mvp_runtime import scheduler
    from runtime.mvp_runtime.crypto import market_data as md

    monkeypatch.setenv(md.CANDLE_ARCHIVE_ENV, md.HYPERLIQUID)
    monkeypatch.setattr(
        md, "select_candle_archive_collector",
        lambda **kwargs: _FakeVenue(fail_on=("xyz:SMH",)),
    )
    summary = _fire(scheduler.KIND_CANDLE_ARCHIVE, tmp_path, monkeypatch)
    assert summary.startswith("candle_archive symbols=2")
    assert "degraded=4" in summary


def test_a_rate_limited_pass_reaches_the_operator(tmp_path, monkeypatch):
    """The gap that let the real 80% loss pass as a completed day.

    `ARCHIVE_ALL_BOOKS_DEGRADED` needs degraded == books, and a rate-limited pass never gets
    there: it STOPS, so most books are `skipped` rather than degraded. 282 of 352 lost, and the
    fire reported COMPLETED — reaching nobody, while the window it races kept rolling."""
    from runtime.mvp_runtime import scheduler
    from runtime.mvp_runtime.crypto import market_data as md

    monkeypatch.setenv(md.CANDLE_ARCHIVE_ENV, md.HYPERLIQUID)
    monkeypatch.setattr(
        md, "select_candle_archive_collector",
        lambda **kwargs: _FakeVenue(symbols=[f"xyz:S{i}" for i in range(10)],
                                    rate_limit_after=3),
    )
    with pytest.raises(Exception) as exc:
        _fire(scheduler.KIND_CANDLE_ARCHIVE, tmp_path, monkeypatch)
    assert exc.value.reason_code == "ARCHIVE_RATE_LIMITED"
    # The all-degraded check cannot be what caught it: only one book actually degraded.
    assert "not attempted" in str(exc.value)


def test_the_sweep_bound_is_a_property_of_the_cadence_not_the_rotation(tmp_path):
    """Evenly spaced passes tile; the scheduler does not space them evenly.

    The offset is a function of the CLOCK, not of the pass count, so an irregular gap moves the
    window by an irregular number of books: two hours between fires advances the offset 120
    while the window is 100 wide, and the 20 in between wait a whole lap. `plan_pass`'s first
    paragraph describes the tiling and is where a reader stops.

    Measured over the 88 real fires 2026-08-04T14:59Z..2026-08-08T06:59Z, the spacing runs
    49.1-120.0 minutes (median 60.1), and enumerating against the live store over those actual
    times gives max 12.0h / p90 7.0h / median 6.0h against a tiling bound of 6 — coverage intact
    (376 of 376), the bound twice what the arithmetic says.

    Pinned synthetically because the live figure is a fact about the venue and the scheduler,
    not about this function; what belongs in a test is that the two cadences differ at all.
    """
    symbols = [f"S{i:02d}" for i in range(25)]
    timeframes = ("15m", "1h", "4h", "1d")            # 100 books
    for timeframe in timeframes:
        for symbol in symbols:
            _fill(tmp_path, symbol, timeframe)
    budget = 40

    def worst_gap(spacings_minutes):
        seen: dict[tuple[str, str], int] = {}
        gap, minute = 0, 0
        for index, step in enumerate(spacings_minutes):
            minute += step
            order = archive.plan_pass(symbols, timeframes, venue=VENUE,
                                      now_ms=NOW_MS + minute * 60_000, root=tmp_path)
            for book in order[:budget]:
                if book in seen:
                    gap = max(gap, index - seen[book])
                seen[book] = index
        return gap

    even = worst_gap([60] * 40)
    # The observed shape: mostly hourly with the 49- and 120-minute fires the ledger records.
    uneven = worst_gap(([60, 60, 120, 49, 60, 60, 60, 120] * 5))
    assert even <= 3, "evenly spaced passes should tile within the window's own arithmetic"
    assert uneven > even, (
        "the tiling bound only holds for evenly spaced passes — if this stops being true the "
        "docstring's measured figure should be re-derived, not the other way round"
    )
