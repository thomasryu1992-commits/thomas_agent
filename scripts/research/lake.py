"""The research sandbox's market data: the venue's own rows, read back from a local cache.

``docs/proposals/RESEARCH_SANDBOX_V0.1.md`` §4.2–§4.3. The sandbox has no network and no keys, so the
collector the runtime would select (Binance, behind its env gate) is replaced by :class:`LakeCollector`,
which serves the same three public series from what ``fetch_public`` stored:

- candles (``collect``), funding (``funding_history``) and mark / index / premium closes
  (``derivative_price_klines``);
- each parsed by the **runtime's own parser** (``BinanceFuturesCollector._parse`` and
  ``_parse_derivative_page``) over the raw rows the venue returned, so the sandbox cannot read a row
  differently from production.

The keyed Coinalyze series (open interest, liquidations) come from the research lake snapshot once
the runtime stores them (step 3). Until then :class:`LakeLiquidationFeed` raises, so ``attach_feeds``
marks the feed degraded and every OI feature is None: an OI spec is "starved", never fed a stand-in.

:func:`install` swaps the runtime's two selectors and makes the network-free mock collector refuse,
so a script that selects its collector by name cannot quietly measure synthetic data.

Cache layout under ``<cache>/binance/`` (one JSON value per line, oldest first, the venue's raw form):
``klines/<SYMBOL>.<tf>.jsonl`` (12-slot kline rows), ``funding/<SYMBOL>.jsonl`` (``fundingRate`` items),
``derivative/<kind>/<SYMBOL>.<tf>.jsonl`` (12-slot rows).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterator

from runtime.mvp_runtime.crypto import market_data
from runtime.mvp_runtime.errors import ToolError

SOURCE = "research_lake"
LAKE_EMPTY = "RESEARCH_LAKE_EMPTY"
LAKE_NO_COINALYZE = "RESEARCH_LAKE_NO_COINALYZE"
MOCK_REFUSED = "RESEARCH_SANDBOX_MOCK_REFUSED"


def klines_path(cache: Path, symbol: str, timeframe: str) -> Path:
    return cache / "binance" / "klines" / f"{symbol}.{timeframe}.jsonl"


def funding_path(cache: Path, symbol: str) -> Path:
    return cache / "binance" / "funding" / f"{symbol}.jsonl"


def derivative_path(cache: Path, kind: str, symbol: str, timeframe: str) -> Path:
    return cache / "binance" / "derivative" / kind / f"{symbol}.{timeframe}.jsonl"


def read_rows(path: Path) -> Iterator[Any]:
    """Every parseable line; a torn last line is skipped, never repaired."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _by_open_time(rows: Iterator[Any]) -> list[Any]:
    """Latest-wins on the open time (slot 0), oldest first."""
    keyed: dict[int, Any] = {}
    for row in rows:
        if isinstance(row, list) and row and isinstance(row[0], (int, float)):
            keyed[int(row[0])] = row
    return [keyed[k] for k in sorted(keyed)]


class LakeCollector:
    """The Binance collector's three public series, served from the cache. Same provider id, so a spec
    minted on Binance data names the same vocabulary here."""

    tool_id = market_data.MARKET_DATA_TOOL_ID
    tool_version = "0.1.0-research-lake"
    provider_id = market_data.BINANCE_FUTURES
    network_egress = False
    source = SOURCE

    def __init__(self, cache: Path):
        self._cache = Path(cache)
        # Parse helpers only: an unauthorized instance cannot open a socket, and these never try.
        self._parser = market_data.BinanceFuturesCollector()

    def collect(self, symbol: str, timeframe: str, *, limit: int, timeout_seconds: int) -> market_data.MarketSnapshot:
        rows = _by_open_time(read_rows(klines_path(self._cache, symbol, timeframe)))
        if not rows:
            raise ToolError(LAKE_EMPTY, f"no cached {symbol} {timeframe} candles; run fetch_public first")
        parsed = self._parser._parse(json.dumps(rows), now_ms=int(time.time() * 1000))
        window = [candle for _, candle in sorted(parsed, key=lambda pair: pair[0])][-limit:]
        return market_data.MarketSnapshot(
            symbol=symbol, timeframe=timeframe, candles=window, source=self.source, is_synthetic=False,
            collector_version=self.tool_version, latency_ms=0, depth_capped=len(window) < limit,
        )

    def funding_history(self, symbol: str, *, records: int, timeout_seconds: int) -> list[dict[str, Any]]:
        keyed: dict[int, dict[str, Any]] = {}
        for item in read_rows(funding_path(self._cache, symbol)):
            try:
                stamp = int(item["fundingTime"])
                keyed[stamp] = {"timestamp": self._parser._iso(stamp), "funding_rate": float(item["fundingRate"])}
            except (TypeError, ValueError, KeyError):
                continue
        if not keyed:
            raise ToolError(LAKE_EMPTY, f"no cached {symbol} funding; run fetch_public first")
        return [keyed[k] for k in sorted(keyed)][-records:]

    def derivative_price_klines(self, symbol: str, timeframe: str, *, kind: str, limit: int,
                                timeout_seconds: int) -> list[dict[str, Any]]:
        rows = _by_open_time(read_rows(derivative_path(self._cache, kind, symbol, timeframe)))
        if not rows:
            raise ToolError(LAKE_EMPTY, f"no cached {symbol} {timeframe} {kind} prices; run fetch_public first")
        closes = self._parser._parse_derivative_page(json.dumps(rows), kind=kind, now_ms=int(time.time() * 1000))
        ordered = [{"timestamp": self._parser._iso(stamp), "value": closes[stamp]} for stamp in sorted(closes)]
        return ordered[-limit:]


class LakeLiquidationFeed:
    """The keyed Coinalyze series. Absent until the runtime stores them (RESEARCH_SANDBOX step 3), so
    both reads raise and the runtime marks the feed degraded: OI features are None, never stand-ins."""

    def __init__(self, lake: Path | None):
        self._lake = lake

    def open_interest_history(self, symbol: str, *, days: int, timeout_seconds: int,
                              interval: str = market_data.OI_INTERVAL_DAILY) -> list[dict[str, Any]]:
        raise ToolError(LAKE_NO_COINALYZE, "the research lake carries no Coinalyze open interest yet")

    def liquidation_history(self, symbol: str, *, days: int, timeout_seconds: int) -> list[dict[str, Any]]:
        raise ToolError(LAKE_NO_COINALYZE, "the research lake carries no Coinalyze liquidations yet")


def install(*, cache: Path, lake: Path | None) -> None:
    """Point the runtime's selectors at the cache and make the mock collector refuse."""

    def _collector(*, now: str | None = None, root: Path | None = None) -> LakeCollector:
        return LakeCollector(cache)

    def _feed(*, now: str | None = None, root: Path | None = None) -> LakeLiquidationFeed:
        return LakeLiquidationFeed(lake)

    def _refuse(self, *args: Any, **kwargs: Any) -> Any:
        raise ToolError(MOCK_REFUSED, "the research sandbox never measures the synthetic mock collector")

    market_data.select_market_data_collector = _collector
    market_data.select_liquidation_feed = _feed
    market_data.MockMarketDataCollector.collect = _refuse
