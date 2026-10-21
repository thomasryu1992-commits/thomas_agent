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
from runtime.mvp_runtime.crypto import features, positioning_store
from runtime.mvp_runtime.crypto.market_data import MockMarketDataCollector, collect_market_data

NOW = "2026-10-20T12:00:00Z"
TF_HOURS = 4


def _shift(stamp: str, hours: float) -> str:
    return timeutil.format_iso(timeutil.parse_iso(stamp) + timedelta(hours=hours))


def _candles(symbol: str, timeframe: str, limit: int) -> list[dict[str, Any]]:
    snapshot, _ = collect_market_data(symbol, timeframe, collector=MockMarketDataCollector(), now=NOW, limit=limit)
    return snapshot["candles"]


def _full_snapshot() -> dict[str, Any]:
    """Every leg the factory attaches, on a 4h frame, with values that move on every reading."""
    candles = _candles("ETHUSDT", "4h", 600)
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
    grid = [{"timestamp": c["open_time"], "value": c["close"] * (1 + 0.001 * math.sin(i))}
            for i, c in enumerate(candles)]
    return {
        "symbol": "ETHUSDT",
        "timeframe": "4h",
        "candles": candles,
        "htf_candles": _candles("ETHUSDT", "1d", 130),
        "reference_symbol": "BTCUSDT",
        "reference_candles": _candles("BTCUSDT", "4h", 600),
        "peer_candles": {sym: _candles(sym, "4h", 600) for sym in ("BTCUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT")},
        "mark_prices": grid,
        "index_prices": [{**g, "value": g["value"] * 0.999} for g in grid],
        "premium_index": [{"timestamp": g["timestamp"], "value": 0.0001 * math.cos(i)} for i, g in enumerate(grid)],
        "funding": [{"timestamp": s, "funding_rate": 0.0001 * math.sin(i / 3)} for i, s in enumerate(settlements)],
        "open_interest": [{"timestamp": d, "open_interest": 1e6 * (1 + 0.05 * math.sin(i))}
                          for i, d in enumerate(days)],
        "liquidations": [{"timestamp": d, "long_liquidation": 1e5 * (1.5 + math.sin(i)),
                          "short_liquidation": 1e5 * (1.5 + math.cos(i))} for i, d in enumerate(days)],
    }


def _close(row: dict[str, Any]) -> str:
    return row["close_time"]


def _grid_close(row: dict[str, Any]) -> str:
    return _shift(row["timestamp"], TF_HOURS)


def _daily_vendor(row: dict[str, Any]) -> str:
    """Coinalyze daily rows: labelled with the day's START, carrying its close or total
    (`market_data.CoinalyzeLiquidationFeed._parse` / `_parse_open_interest` drop the forming day)."""
    return _shift(row["timestamp"], 24)


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
}
# Read, but not time series.
METADATA = frozenset({"symbol", "reference_symbol", "timeframe"})
# Read, and NOT yet declared, with the reason it is safe today. The positioning rows store no fetch time,
# so whether their label is a period start or a point reading is not measured; no spec may read them
# while `positioning_store.MINTING_DECIDED` is False (pinned below).
UNVERIFIED = frozenset({"positioning"})


def _cut(snapshot: dict[str, Any], cutoff: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in snapshot.items():
        if key not in AVAILABLE_AT:
            out[key] = value
        elif key == "peer_candles":
            out[key] = {sym: [r for r in rows if AVAILABLE_AT[key](r) <= cutoff] for sym, rows in value.items()}
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


def test_positioning_stays_unverified_only_while_no_spec_may_read_it():
    assert positioning_store.MINTING_DECIDED is False, (
        "positioning minting is open: measure whether a positioning row's label is its period's start or a "
        "point reading, declare it in AVAILABLE_AT, and drop it from UNVERIFIED")


def test_no_feature_row_depends_on_anything_known_after_its_close():
    snapshot = _full_snapshot()
    # The contract is only as strong as the columns it sees filled: every leg but positioning must carry
    # values late in the frame, or a leak there would compare None with None and pass.
    late = features.build_feature_rows(snapshot)[-5]
    empty = sorted(k for k, v in late.items() if v is None and "positioning" not in k)
    assert not empty, f"columns the fixture leaves empty: {empty}"
    leaks = _leaks(snapshot)
    assert not leaks, "look-ahead:\n" + "\n".join(leaks[:20])


@pytest.mark.parametrize("constant", ["OI_READING_KNOWN_AFTER_HOURS", "LIQUIDATION_READING_KNOWN_AFTER_HOURS"])
def test_the_contract_catches_a_daily_vendor_series_aligned_on_its_label(monkeypatch, constant):
    """The two bugs this test exists for, put back: each must show up as a leak in its own columns."""
    monkeypatch.setattr(features, constant, 0)
    leaks = _leaks(_full_snapshot())
    prefix = "open_interest" if constant.startswith("OI") else "liquidation"
    assert any(prefix in leak.split()[2] for leak in leaks), leaks[:5]   # "bar <t> <column>: ..."
