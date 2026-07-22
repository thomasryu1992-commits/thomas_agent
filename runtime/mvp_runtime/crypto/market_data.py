"""C2 Crypto market-data collection — read-only, gated, degradable.

The first ported capability of the Crypto Pipeline (``CRYPTO_PIPELINE_V0.1.md``): OHLCV
candles for one symbol/timeframe from an exchange's **public** REST API, recorded as
tamper-evident evidence exactly like an R3 search: collector identity, the request and
its input hash, a summary of the returned candles with an output hash over all of them,
latency, and the read-only scope. Fail-closed on an invalid symbol/timeframe, a
collector error/timeout, or an unparseable response.

``MockMarketDataCollector`` is deterministic and network-free, so the collection path is
built and tested before any live flag exists — the same order R3 was built in. The real
``BinanceFuturesCollector`` needs **no API key** (public endpoints), but crossing the
network is the gated capability, not the secret: it is only ever constructed through
``select_market_data_collector`` after the Safety-Flag Gate authorizes ``network_access``
for the ``binance_futures`` provider, and it re-verifies that authorization at the moment
of egress.

A backend failure at run time is the caller's cue to **degrade, never block** — record
``degraded_market_data_record`` with ``MARKET_DATA_DEGRADED`` and let the cycle continue
without live data (the R3 ``SEARCH_DEGRADED`` / source-system synthetic-fallback
precedent). Wiring into pipeline stages lands with C3, when a research engine exists to
consume the snapshot.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from runtime.read_only_kernel import integrity

from .. import safety_gate, timeutil
from ..errors import ToolBlocked, ToolError
from ..safety_gate import NETWORK_ACCESS, Authorization

MARKET_DATA_TOOL_ID = "crypto.market_data.readonly"
MARKET_DATA_TOOL_VERSION = "0.1.0"
MARKET_DATA_TOOL_CLASS = "read"

# Opting into the real network-backed collector. Like search and the model provider,
# the env var alone is NOT sufficient: the Safety-Flag Gate must authorize
# network_access for the backend's own provider id first.
MARKET_DATA_ENV = "MVP_MARKET_DATA"
BINANCE_FUTURES = "binance_futures"
# Public-endpoint reads cross the network but invoke no model — network_access only.
_NETWORK_FLAGS = (NETWORK_ACCESS,)

# The degraded-run reason code the pipeline audits when a live backend fails and the
# cycle continues without live data (C3 wiring; the SEARCH_DEGRADED analog).
MARKET_DATA_DEGRADED = "MARKET_DATA_DEGRADED"

# Closed vocabulary, identical on both collectors and on Binance's interval strings.
TIMEFRAMES: dict[str, int] = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
_SYMBOL_PATTERN = re.compile(r"\A[A-Z0-9]{5,20}\Z")  # e.g. BTCUSDT; anchored (QA wave 7)
MAX_CANDLES = 500
DEFAULT_CANDLES = 120


@dataclass
class Candle:
    open_time: str  # UTC ISO-8601, candle open
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: str  # UTC ISO-8601, candle close


@dataclass
class MarketSnapshot:
    symbol: str
    timeframe: str
    candles: list[Candle]
    source: str
    is_synthetic: bool
    collector_version: str = MARKET_DATA_TOOL_VERSION
    latency_ms: int = 0


class MarketDataCollector(Protocol):
    tool_id: str
    tool_version: str

    def collect(
        self, symbol: str, timeframe: str, *, limit: int, timeout_seconds: int
    ) -> MarketSnapshot: ...


def _frac(seed: str) -> float:
    """Deterministic value in [0, 1) from a seed string (mock price synthesis)."""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / float(0x100000000)


class MockMarketDataCollector:
    """Deterministic, network-free collector for tests and pre-gate pipeline runs.

    Candles are a pure function of (symbol, timeframe, limit): a fixed time grid
    anchored at 2026-01-01T00:00:00Z and a hash-derived price walk, honestly marked
    ``is_synthetic=True`` so downstream health checks treat it as non-trade-eligible
    (the source system's synthetic-collector contract).
    """

    tool_id = MARKET_DATA_TOOL_ID
    tool_version = MARKET_DATA_TOOL_VERSION
    network_egress = False  # deterministic, in-process; no outbound call
    source = "mock.market_data"
    _ANCHOR = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def collect(
        self, symbol: str, timeframe: str, *, limit: int, timeout_seconds: int
    ) -> MarketSnapshot:
        step = timedelta(minutes=TIMEFRAMES[timeframe])
        close = 100.0 + 900.0 * _frac(f"{symbol}|{timeframe}")
        candles: list[Candle] = []
        for i in range(limit):
            open_price = close
            drift = (_frac(f"{symbol}|{timeframe}|{i}") - 0.5) * 0.04
            close = round(open_price * (1.0 + drift), 6)
            high = round(max(open_price, close) * 1.005, 6)
            low = round(min(open_price, close) * 0.995, 6)
            volume = round(1000.0 * (0.5 + _frac(f"{symbol}|{timeframe}|v{i}")), 3)
            opened = self._ANCHOR + i * step
            candles.append(Candle(
                open_time=timeutil.format_iso(opened),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                close_time=timeutil.format_iso(opened + step),
            ))
        return MarketSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            source=self.source,
            is_synthetic=True,
            latency_ms=0,
        )


def _require_symbol(symbol: Any) -> str:
    if not isinstance(symbol, str) or not symbol.strip():
        raise ToolBlocked("EMPTY_SYMBOL", "market-data symbol must be a non-empty string")
    if not _SYMBOL_PATTERN.fullmatch(symbol):
        raise ToolBlocked("INVALID_SYMBOL", "symbol must be 5-20 uppercase alphanumerics (e.g. BTCUSDT)")
    return symbol


def _require_timeframe(timeframe: Any) -> str:
    if timeframe not in TIMEFRAMES:
        raise ToolBlocked("INVALID_TIMEFRAME", f"timeframe must be one of {sorted(TIMEFRAMES)}")
    return timeframe


def collect_market_data(
    symbol: str,
    timeframe: str,
    *,
    collector: MarketDataCollector,
    now: str,
    limit: int = DEFAULT_CANDLES,
    timeout_seconds: int = 10,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Collect one symbol/timeframe of OHLCV. Returns ``(snapshot, tool_use_record)``.

    ``snapshot`` is the JSON-ready market snapshot for downstream stages (candles
    included); the record captures the collector identity, the request + input hash, a
    candle summary + an output hash over the full candle list, latency, and the
    read-only scope. The record carries the summary rather than every candle — the
    snapshot itself is the pipeline artifact that gets persisted as evidence (C3), and
    ``output_sha256`` binds the two verifiably. Fails closed (``ToolBlocked``) on an
    invalid request or a collector error/timeout.
    """
    symbol = _require_symbol(symbol)
    timeframe = _require_timeframe(timeframe)
    limit = max(1, min(int(limit), MAX_CANDLES))
    try:
        result = collector.collect(symbol, timeframe, limit=limit, timeout_seconds=timeout_seconds)
    except (ToolError, TimeoutError) as exc:
        raise ToolBlocked("TOOL_ERROR", str(exc)) from exc

    candles = [
        {
            "open_time": c.open_time,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
            "close_time": c.close_time,
        }
        for c in result.candles
        if isinstance(c, Candle)
    ]
    snapshot = {
        "snapshot_version": "0.1",
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": candles,
        "candle_count": len(candles),
        "last_close": candles[-1]["close"] if candles else None,
        "last_candle_time": candles[-1]["close_time"] if candles else None,
        "source": result.source,
        "is_synthetic": bool(result.is_synthetic),
        "created_at": now,
    }
    input_sha256 = integrity.sha256_record(
        {"tool_id": collector.tool_id, "symbol": symbol, "timeframe": timeframe, "limit": limit}
    )
    output_sha256 = integrity.sha256_record({"candles": candles})
    record = {
        "tool_id": collector.tool_id,
        "tool_version": collector.tool_version,
        "tool_class": MARKET_DATA_TOOL_CLASS,
        "operation": "collect_market_data",
        "symbol": symbol,
        "timeframe": timeframe,
        "input_sha256": input_sha256,
        "candle_count": len(candles),
        "last_close": snapshot["last_close"],
        "last_candle_time": snapshot["last_candle_time"],
        "source": result.source,
        "is_synthetic": bool(result.is_synthetic),
        "output_sha256": output_sha256,
        "latency_ms": int(result.latency_ms),
        "read_only": True,
        "external_action": False,
        # Whether this collection crossed the network boundary (mock=False, real=True).
        "network_egress": bool(getattr(collector, "network_egress", False)),
        "created_at": now,
    }
    return snapshot, record


def degraded_market_data_record(
    collector: MarketDataCollector, symbol: str, timeframe: str, reason_code: str, *, now: str
) -> dict[str, Any]:
    """The tool_use record for a collection whose backend failed — recorded, never silent.

    Live data is enrichment for a paper cycle, not the cycle itself: an unreachable or
    rate-limited exchange must not block the run (the R3 ``SEARCH_DEGRADED`` precedent,
    and the source system's own real-fetch-falls-back-to-synthetic contract). Same shape
    as a successful record — zero candles — plus ``degraded`` and the failure's
    ``reason_code``, so the audit trail says exactly why this cycle ran without live data.
    """
    tool_id = getattr(collector, "tool_id", MARKET_DATA_TOOL_ID)
    return {
        "tool_id": tool_id,
        "tool_version": getattr(collector, "tool_version", MARKET_DATA_TOOL_VERSION),
        "tool_class": MARKET_DATA_TOOL_CLASS,
        "operation": "collect_market_data",
        "symbol": symbol,
        "timeframe": timeframe,
        "input_sha256": integrity.sha256_record(
            {"tool_id": tool_id, "symbol": symbol, "timeframe": timeframe}
        ),
        "candle_count": 0,
        "last_close": None,
        "last_candle_time": None,
        "source": getattr(collector, "source", "unknown"),
        "is_synthetic": False,
        "output_sha256": integrity.sha256_record({"candles": []}),
        "latency_ms": 0,
        "read_only": True,
        "external_action": False,
        # Capability, as in collect_market_data: the failed attempt was made by a
        # network-capable collector even though no successful egress happened.
        "network_egress": bool(getattr(collector, "network_egress", False)),
        "degraded": True,
        "degraded_reason_code": reason_code,
        "created_at": now,
    }


def select_market_data_collector(
    *, now: str | None = None, root: Path | None = None
) -> MarketDataCollector:
    """Choose the market-data collector — the enforced Safety-Flag Gate chokepoint.

    Defaults to the deterministic, network-free ``MockMarketDataCollector`` (no gate
    needed; it performs no network I/O). The real Binance collector is returned ONLY
    when both (a) the caller opts in via ``MVP_MARKET_DATA=binance_futures`` AND (b) the
    Safety-Flag Gate authorizes ``network_access`` against that provider's own local,
    integrity-checked activation record. The env var alone fails closed
    (``SafetyGateBlocked``) — a public endpoint needs no key, so the gate is the ONLY
    thing standing between a config typo and an outbound socket.

    ``safety_gate.select_gated`` enforces the ordering: no network-capable collector is
    constructed until the gate has opened. One backend — collection degrades instead of
    failing over (see ``degraded_market_data_record``).
    """
    return safety_gate.select_gated(
        env_var=MARKET_DATA_ENV,
        opt_in_value=BINANCE_FUTURES,
        flags=_NETWORK_FLAGS,
        provider_id=BINANCE_FUTURES,
        default_factory=MockMarketDataCollector,
        gated_factory=lambda authorization: BinanceFuturesCollector(authorization=authorization),
        now=now,
        root=root,
    )


class BinanceFuturesCollector:
    """Real OHLCV via Binance USD-M Futures public klines (read-only, no API key).

    Public data, but an outbound HTTPS GET — so the Safety-Flag Gate must be open for
    the ``binance_futures`` provider before this class is ever constructed, and
    ``collect`` re-verifies the authorization at the moment of egress (defense in
    depth, the R3 adapter posture). The still-forming candle is dropped so downstream
    indicators never see a candle whose close is still moving (the source system's
    ``drop_forming_candle`` contract).
    """

    tool_id = MARKET_DATA_TOOL_ID
    tool_version = "0.1.0-binance"
    provider_id = BINANCE_FUTURES
    network_egress = True  # makes an outbound HTTPS call — recorded as network egress
    source = "binance_futures_public"
    _ENDPOINT = "https://fapi.binance.com/fapi/v1/klines"

    def __init__(self, *, authorization: Authorization | None = None):
        # Egress authorization from the Safety-Flag Gate. Without it, collect() refuses
        # to open a socket — a directly-constructed collector cannot bypass the gate.
        self._authorization = authorization

    def collect(
        self, symbol: str, timeframe: str, *, limit: int, timeout_seconds: int
    ) -> MarketSnapshot:
        # Chokepoint: re-verify authorization at the moment of egress (defense in depth).
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )
        # Ask for one extra row: the venue returns the still-forming candle last, and
        # dropping it must not shrink the caller's requested window.
        params = urllib.parse.urlencode(
            {"symbol": symbol, "interval": timeframe, "limit": min(limit + 1, MAX_CANDLES + 1)}
        )
        request = urllib.request.Request(
            f"{self._ENDPOINT}?{params}", method="GET", headers={"Accept": "application/json"}
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except (TimeoutError, urllib.error.URLError):
            # Deliberately generic — never echo the URL (the R3 transport-error posture).
            raise ToolError("TOOL_TRANSPORT", "market-data request failed or timed out") from None
        latency_ms = int((time.monotonic() - started) * 1000)

        candles = self._parse(raw, limit, now_ms=int(time.time() * 1000))
        return MarketSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            source=self.source,
            is_synthetic=False,
            collector_version=self.tool_version,
            latency_ms=latency_ms,
        )

    def _parse(self, raw: str, limit: int, *, now_ms: int) -> list[Candle]:
        try:
            rows = json.loads(raw)
        except ValueError:
            raise ToolError("MALFORMED_RESULT", "market-data backend returned an unparseable response") from None
        if not isinstance(rows, list):
            raise ToolError("MALFORMED_RESULT", "market-data backend returned an unparseable response")

        candles: list[Candle] = []
        for row in rows:
            # Kline row: [open_time_ms, open, high, low, close, volume, close_time_ms, ...]
            if not isinstance(row, list) or len(row) < 7:
                raise ToolError("MALFORMED_RESULT", "market-data backend returned an unparseable response")
            try:
                open_ms, close_ms = int(row[0]), int(row[6])
                candles.append(Candle(
                    open_time=self._iso(open_ms),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    close_time=self._iso(close_ms),
                ))
            except (TypeError, ValueError):
                raise ToolError("MALFORMED_RESULT", "market-data backend returned an unparseable response") from None
            if close_ms >= now_ms:
                candles.pop()  # still-forming candle — its close is still moving
        return candles[-limit:]

    @staticmethod
    def _iso(epoch_ms: int) -> str:
        # Pure arithmetic, not fromtimestamp: Windows' underlying gmtime rejects
        # far-future epochs (OSError 22), and a venue-supplied timestamp is input.
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=epoch_ms)
        return timeutil.format_iso(epoch)
