"""The point-in-time contract (E3 of CRYPTO_ARCHIVE_BACKFILL_V0.1, "결정 (Thomas 2026-10-21)").

A feature row for bar t is what the strategy knows when bar t closes. So it must be the same whether or
not anything that became known AFTER that instant is in the snapshot. Truncating by label is not enough:
the daily OI bug passed every label-ordered check, because the vendor labels a row with its period's
start and carries its close. The truncation here cuts every series by when its row is KNOWN in the
world, declared below per snapshot key, and the declaration is the vendor's semantics, not the code's.

Two properties, both behavioural:
- every snapshot key `build_feature_rows` reads has a declared availability (recorded by running it,
  not by reading its source), so a new feed cannot ship without saying when its rows are known;
- every row of a frame built from the full snapshot equals the same row built from the snapshot cut
  at that row's close, for every column.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any, Callable

import pytest

from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.crypto import features
from runtime.mvp_runtime.crypto.market_data import HIGHER_TIMEFRAME, MockMarketDataCollector, collect_market_data

NOW = "2026-10-20T12:00:00Z"


def _shift(stamp: str, hours: float) -> str:
    return timeutil.format_iso(timeutil.parse_iso(stamp) + timedelta(hours=hours))


def _candles(symbol: str, timeframe: str, limit: int) -> list[dict[str, Any]]:
    snapshot, _ = collect_market_data(symbol, timeframe, collector=MockMarketDataCollector(), now=NOW, limit=limit)
    return snapshot["candles"]


# Each frame the factory mines below 1d, deep enough that every warm-up fills: a leak shorter than one bar
# is invisible on that frame (the bar's own close already knows it), so each timeframe is checked on its own.
TF_HOURS = {"4h": 4, "1h": 1, "15m": 0.25}
BARS = {"4h": 600, "1h": 1200, "15m": 2400}


def _full_snapshot(timeframe: str = "4h") -> dict[str, Any]:
    """Every leg the factory attaches, on one frame, with values that move on every reading."""
    bars = BARS[timeframe]
    higher = HIGHER_TIMEFRAME[timeframe]
    htf_bars = int(bars * TF_HOURS[timeframe] / {"1d": 24, "4h": 4, "1h": 1}[higher]) + 60
    candles = _candles("ETHUSDT", timeframe, bars)
    first, last = candles[0]["open_time"], candles[-1]["close_time"]
    days = []
    day = timeutil.parse_iso(first[:10] + "T00:00:00Z")
    while timeutil.format_iso(day) <= last:
        days.append(timeutil.format_iso(day))
        day += timedelta(days=1)
    settlements = []
    stamp = timeutil.parse_iso(first)
    while timeutil.format_iso(stamp) <= last:
        settlements.append(timeutil.format_iso(stamp))
        stamp += timedelta(hours=8)
    hours = []
    stamp = timeutil.parse_iso(first)
    while timeutil.format_iso(stamp) <= last:
        hours.append(timeutil.format_iso(stamp))
        stamp += timedelta(hours=1)
    grid = [{"timestamp": c["open_time"], "value": c["close"] * (1 + 0.001 * math.sin(i))}
            for i, c in enumerate(candles)]
    return {
        "symbol": "ETHUSDT",
        "timeframe": timeframe,
        "candles": candles,
        "htf_candles": _candles("ETHUSDT", higher, htf_bars),
        "reference_symbol": "BTCUSDT",
        "reference_candles": _candles("BTCUSDT", timeframe, bars),
        "peer_candles": {sym: _candles(sym, timeframe, bars) for sym in ("BTCUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT")},
        "mark_prices": grid,
        "index_prices": [{**g, "value": g["value"] * 0.999} for g in grid],
        "premium_index": [{"timestamp": g["timestamp"], "value": 0.0001 * math.cos(i)} for i, g in enumerate(grid)],
        "funding": [{"timestamp": s, "funding_rate": 0.0001 * math.sin(i / 3)} for i, s in enumerate(settlements)],
        "open_interest": [{"timestamp": d, "open_interest": 1e6 * (1 + 0.05 * math.sin(i))}
                          for i, d in enumerate(days)],
        "liquidations": [{"timestamp": d, "long_liquidation": 1e5 * (1.5 + math.sin(i)),
                          "short_liquidation": 1e5 * (1.5 + math.cos(i))} for i, d in enumerate(days)],
        "positioning": [{"series": series, "period": "1h", "timestamp": h,
                         "long_ratio": 0.5 + 0.1 * math.sin(i / (k + 2)), "short_ratio": 0.5}
                        for i, h in enumerate(hours)
                        for k, series in enumerate(("top_position", "global_account", "top_account"))],
    }


def _close(row: dict[str, Any]) -> str:
    return row["close_time"]


def _grid_close(row: dict[str, Any]) -> str:
    """Placeholder: a grid row closes one bar of ITS frame after it opens, so `_cut` resolves it."""
    raise AssertionError("resolved in _cut, which knows the frame")


GRID = frozenset({"mark_prices", "index_prices", "premium_index"})


def _daily_vendor(row: dict[str, Any]) -> str:
    """Coinalyze daily rows: labelled with the day's START, carrying its close or total
    (`market_data.CoinalyzeLiquidationFeed._parse` / `_parse_open_interest` drop the forming day)."""
    return _shift(row["timestamp"], 24)


def _positioning(row: dict[str, Any]) -> str:
    """Binance `/futures/data/*LongShort*Ratio` rows: a reading AT the label, published a few minutes after
    it and never revised. Measured 2026-10-21 09:47-10:38 UTC (162 polls of BTCUSDT, all three series, 5m
    and 1h): the 10:00 1h row first appeared between +94 s and +213 s and kept its value through 20 polls
    inside its hour; 5m rows appeared within +7..+293 s; no row's value ever moved
    (`docs/history/2026-10-21-positioning-availability.md`). Ten minutes is that maximum with margin."""
    return _shift(row["timestamp"], 10 / 60)


# When each series' row is known in the world. The vendor's semantics, stated here independently of
# the constants in `features`, so a wrong constant there fails this test instead of passing it.
AVAILABLE_AT: dict[str, Callable[[dict[str, Any]], str]] = {
    "candles": _close,
    "htf_candles": _close,
    "reference_candles": _close,
    "peer_candles": _close,          # per peer symbol
    "mark_prices": _grid_close,      # requested on the candle grid: the bar opening at T closes at T+tf
    "index_prices": _grid_close,
    "premium_index": _grid_close,
    "funding": lambda row: row["timestamp"],   # the settlement instant: the rate is fixed then
    "open_interest": _daily_vendor,
    "liquidations": _daily_vendor,
    "positioning": _positioning,
}
# Read, but not time series.
METADATA = frozenset({"symbol", "reference_symbol", "timeframe"})
# Read, and NOT yet declared. Empty since positioning was measured (2026-10-21); a key added here must
# say why it is safe without a declaration.
UNVERIFIED: frozenset[str] = frozenset()


def _cut(snapshot: dict[str, Any], cutoff: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in snapshot.items():
        if key not in AVAILABLE_AT:
            out[key] = value
        elif key == "peer_candles":
            out[key] = {sym: [r for r in rows if AVAILABLE_AT[key](r) <= cutoff] for sym, rows in value.items()}
        elif key in GRID:
            step = TF_HOURS[snapshot["timeframe"]]
            out[key] = [r for r in value if _shift(r["timestamp"], step) <= cutoff]
        else:
            out[key] = [r for r in value if AVAILABLE_AT[key](r) <= cutoff]
    return out


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-15) or (math.isnan(a) and math.isnan(b))
    return a == b


def _leaks(snapshot: dict[str, Any], *, cuts: int = 20) -> list[str]:
    full = features.build_feature_rows(snapshot)
    n = len(snapshot["candles"])
    leaks: list[str] = []
    for t in sorted({n // 4 + k * (n - n // 4 - 1) // (cuts - 1) for k in range(cuts)}):
        cut_rows = features.build_feature_rows(_cut(snapshot, snapshot["candles"][t]["close_time"]))
        assert len(cut_rows) == t + 1
        for column in sorted(full[t]):
            if not _same(full[t][column], cut_rows[t].get(column)):
                leaks.append(f"bar {t} {column}: full={full[t][column]!r} cut={cut_rows[t].get(column)!r}")
    return leaks


class _Recorder(dict):
    """A snapshot that remembers which keys the feature builder asked for."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.asked: set[str] = set()

    def get(self, key: Any, default: Any = None) -> Any:
        self.asked.add(key)
        return super().get(key, default)

    def __getitem__(self, key: Any) -> Any:
        self.asked.add(key)
        return super().__getitem__(key)

    def __contains__(self, key: object) -> bool:
        self.asked.add(str(key))
        return super().__contains__(key)


def test_every_series_the_features_read_declares_when_its_rows_are_known():
    recorder = _Recorder(_full_snapshot())
    features.build_feature_rows(recorder)
    undeclared = recorder.asked - set(AVAILABLE_AT) - METADATA - UNVERIFIED
    assert not undeclared, (
        f"build_feature_rows reads {sorted(undeclared)} with no declared availability: add when its rows are "
        "known to AVAILABLE_AT (the vendor's semantics), or the point-in-time test cannot see it leak")
    assert UNVERIFIED <= recorder.asked, "an UNVERIFIED key is no longer read; drop it from the list"


@pytest.mark.parametrize("timeframe", sorted(BARS))
def test_no_feature_row_depends_on_anything_known_after_its_close(timeframe):
    snapshot = _full_snapshot(timeframe)
    # The contract is only as strong as the columns it sees filled: every leg must carry values late in
    # the frame, or a leak there would compare None with None and pass.
    late = features.build_feature_rows(snapshot)[-5]
    empty = sorted(k for k, v in late.items() if v is None)
    assert not empty, f"columns the fixture leaves empty: {empty}"
    leaks = _leaks(snapshot, cuts={"4h": 20, "1h": 12, "15m": 8}[timeframe])
    assert not leaks, "look-ahead:\n" + "\n".join(leaks[:20])


@pytest.mark.parametrize("constant", ["OI_READING_KNOWN_AFTER_HOURS", "LIQUIDATION_READING_KNOWN_AFTER_HOURS"])
def test_the_contract_catches_a_daily_vendor_series_aligned_on_its_label(monkeypatch, constant):
    """The two bugs this test exists for, put back: each must show up as a leak in its own columns."""
    monkeypatch.setattr(features, constant, 0)
    leaks = _leaks(_full_snapshot())
    prefix = "open_interest" if constant.startswith("OI") else "liquidation"
    assert any(prefix in leak.split()[2] for leak in leaks), leaks[:5]   # "bar <t> <column>: ..."
