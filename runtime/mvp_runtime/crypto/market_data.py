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
import os
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
from ..errors import MvpRuntimeError, ToolBlocked, ToolError
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

# C9 derivative feeds. Funding rides the SAME binance_futures grant (another public
# endpoint of the already-authorized provider); liquidations come from Coinalyze,
# which is its own provider with its own key and its own per-machine grant — one
# grant per provider, exactly like the model failover chain.
FUNDING_DEGRADED = "FUNDING_DEGRADED"
LIQUIDATION_DEGRADED = "LIQUIDATION_DEGRADED"
OPEN_INTEREST_DEGRADED = "OPEN_INTEREST_DEGRADED"
LIQUIDATION_FEED_ENV = "MVP_LIQUIDATION_FEED"

# Derivative PRICE series — mark, index, and the premium index that funding is computed
# from. All three are kline-shaped public endpoints of the already-authorized
# ``binance_futures`` provider, on the SAME time grid and interval as the OHLCV klines,
# so they need no new provider, key or grant.
#
# Why they matter enough to spend three requests on. The feature row has carried
# ``mark_price``/``index_price``/``mark_index_basis_bps`` since C3 as documented no-feed
# fallbacks — close, close, and a hardcoded ``0.0`` — while the factory's mintable
# vocabulary admitted all three. A literal condition on a basis that is always exactly
# zero can only ever be true or only ever false, so the column was worse than absent.
# And the premium index is the same information funding carries, at BAR resolution
# instead of one 8h event: ``funding_zscore`` is a step function that changes three times
# a day, which is what a 1h ``funding_fade_*`` spec has been timing itself on.
MARK_PRICE_DEGRADED = "MARK_PRICE_DEGRADED"
INDEX_PRICE_DEGRADED = "INDEX_PRICE_DEGRADED"
PREMIUM_INDEX_DEGRADED = "PREMIUM_INDEX_DEGRADED"

# Endpoint path per series kind. A closed mapping rather than an interpolated string: an
# unknown kind would otherwise become a 404 at egress, or worse a different series
# answered on the same shape.
DERIVATIVE_KLINE_PATHS: dict[str, str] = {
    "mark": "markPriceKlines",
    "index": "indexPriceKlines",
    "premium": "premiumIndexKlines",
}
DERIVATIVE_KINDS = frozenset(DERIVATIVE_KLINE_PATHS)
# The venue caps these at 1000 rows per call (klines allow 1500), and they page by the
# same exclusive ``endTime`` walk.
DERIVATIVE_PAGE_LIMIT = 1000
DERIVATIVE_MAX_PAGES = 60

# Open-interest aggregation intervals the feed will request. `daily` is what every feature path
# reads — it is the only depth that covers the factory's 500-day replay, since hourly history
# stops ~84 days back (measured on the vendor 2026-07-29). `1hour` exists for `oi_store`, which
# retains the hourly series itself against the day it is deep enough to replace the daily one.
# A closed set rather than a pass-through string: an unknown interval would be answered by the
# vendor with a series of some other cadence, and the store would count it as hours.
OI_INTERVAL_DAILY = "daily"
OI_INTERVAL_1H = "1hour"
OI_INTERVAL_SECONDS = {OI_INTERVAL_DAILY: 86_400, OI_INTERVAL_1H: 3_600}
OI_INTERVALS = frozenset(OI_INTERVAL_SECONDS)
COINALYZE = "coinalyze_market_data"
FUNDING_PAGE_LIMIT = 1000  # venue cap per /fapi/v1/fundingRate call
FUNDING_MAX_PAGES = 4      # 8h cadence: 4 pages ≈ 3.6 years — beyond any window we replay
DEFAULT_FUNDING_RECORDS = 1600  # ≥ 3 events/day × FACTORY_DEPTH_DAYS, with head-room

# Closed vocabulary, identical on both collectors and on Binance's interval strings.
TIMEFRAMES: dict[str, int] = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}

# The higher timeframe each authorable timeframe reads its regime from — one step up
# the LADDER THE RUNTIME ALREADY TRADES, so an HTF context is itself a collected
# context rather than a new data dependency. Ratios are 4x/4x/6x, the conventional
# band for a regime filter. ``1d`` is deliberately absent: the step above it (1w) is
# not collected, and a timeframe with no entry here simply has no HTF features —
# they stay indeterminate, so an htf_* spec can never match there.
HIGHER_TIMEFRAME: dict[str, str] = {"15m": "1h", "1h": "4h", "4h": "1d"}

# The market proxy every other symbol's relative strength is measured against. BTC rather
# than an index because it is the one series this runtime already collects that the rest of
# the universe actually co-moves with, and because a real index would be a new vendor.
#
# It is a CONFIGURED constant rather than a per-cycle choice on purpose: relative strength is
# only comparable across candidates if every candidate measured it against the same thing, and
# a reference that moved between generations would make two lineages' `rel_strength_roc_4`
# thresholds mean different things while looking identical.
REFERENCE_SYMBOL = "BTCUSDT"
REFERENCE_DEGRADED = "REFERENCE_DEGRADED"
_SYMBOL_PATTERN = re.compile(r"\A[A-Z0-9]{5,20}\Z")  # e.g. BTCUSDT; anchored (QA wave 7)
MAX_CANDLES = 60_000
DEFAULT_CANDLES = 120

# Factory replay depth, expressed in CALENDAR days rather than a constant bar count.
# A flat 500-bar window is ~1.4 years at 1d but five hours at 15m: all three of the
# factory's walk-forward slices land in the same session, so every candidate mined
# there is single-regime by construction and cannot clear the robustness score.
#
# Every timeframe a strategy can actually be authored at (strategy.ALLOWED_TIMEFRAMES,
# which is narrower than TIMEFRAMES above) fits under MAX_CANDLES at this depth — 15m
# is the deepest at 48k bars. The clamp is therefore head-room, not a live limit; it
# bounds egress and memory if the strategy vocabulary is ever widened downward.
FACTORY_DEPTH_DAYS = 500

# The floor the calendar span does not provide. Expressing depth in days fixed the fast
# timeframes and left the slow ones starved in the mirror-image way: 500 days is 48k bars
# at 15m but 500 at 1d, and the robustness scorer counts TRADES, which scale with bars and
# not with calendar span. Every 1d lineage therefore scored on ~350 replay bars and ~16
# trades — under the scorer's critical trades-per-parameter — so a 1d candidate was FRAGILE
# by construction, whatever its edge. Measured 2026-07-29 on the 25 live 1d strategies,
# same specs and same cost model, only the window changed: at 500 bars the scorer returned
# 0 ROBUST / 20 FRAGILE with holdout CONFIRMED 11; at this floor, 12 ROBUST / 1 FRAGILE
# with holdout CONFIRMED 15.
#
# One-sided by construction: the floor can only lengthen a window, never shorten one, so
# every timeframe the calendar reasoning above already bound stays bound (4h is the nearest
# at 3,000 bars and does not move). Only 1d changes.
#
# The value is what the venue can actually answer, not a round number: the shortest history
# among the routed USD-M perpetuals is ~2.1k daily bars (SOLUSDT), so a higher floor would
# collect short and score a window it never received.
MIN_FACTORY_BARS = 2_000


@dataclass
class Candle:
    open_time: str  # UTC ISO-8601, candle open
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: str  # UTC ISO-8601, candle close
    # The venue sends these in the SAME kline row as the OHLCV above — they cost no extra
    # request, no extra vendor and no extra grant, and the collector discarded them for the
    # whole life of the port. `taker_buy_base` is the load-bearing one: over `volume` it is
    # the bar's order-flow imbalance, which is the only field here that price history cannot
    # reconstruct. See `features` for what is derived and why only the normalized forms are
    # mintable.
    #
    # Default None, never 0.0: a bar whose venue did not report them is *unknown*, and the
    # fail-closed evaluator must treat it as indeterminate rather than as balanced flow.
    quote_volume: float | None = None    # notional turnover — comparable across symbols
    trade_count: float | None = None     # number of trades in the bar
    taker_buy_base: float | None = None  # base-asset volume bought by the AGGRESSOR side
    taker_buy_quote: float | None = None # the same in quote terms


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


def _optional_float(row: list, index: int) -> float | None:
    """``row[index]`` as a float, or None when absent/unparseable.

    The counterpart of the required OHLCV coercion: these fields must never turn a venue
    payload change into a raised error (the cycle would stop trading everything, not just
    the specs that read them), and must never turn one into a fabricated number either.
    None is the one answer that is both."""
    if index >= len(row):
        return None
    value = row[index]
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
            # Flow legs, synthesized on the same hash walk so a mock run exercises the
            # taker_* columns instead of leaving them permanently indeterminate. The buy
            # share is deliberately correlated with the bar's own drift (a rising bar was
            # bought), because a mock whose flow is independent of its price would make the
            # divergence families untestable — they exist precisely to catch the case where
            # the two DISAGREE, and a mock that never agrees cannot show the difference.
            buy_share = min(0.9, max(0.1, 0.5 + drift * 8.0 + (_frac(f"{symbol}|{timeframe}|t{i}") - 0.5) * 0.2))
            trade_count = round(20.0 + 180.0 * _frac(f"{symbol}|{timeframe}|n{i}"))
            opened = self._ANCHOR + i * step
            candles.append(Candle(
                open_time=timeutil.format_iso(opened),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                close_time=timeutil.format_iso(opened + step),
                quote_volume=round(volume * close, 6),
                trade_count=float(trade_count),
                taker_buy_base=round(volume * buy_share, 6),
                taker_buy_quote=round(volume * buy_share * close, 6),
            ))
        return MarketSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            source=self.source,
            is_synthetic=True,
            latency_ms=0,
        )

    def derivative_price_klines(
        self, symbol: str, timeframe: str, *, kind: str, limit: int, timeout_seconds: int
    ) -> list[dict[str, Any]]:
        """Deterministic mark / index / premium series on the SAME grid as ``collect``.

        Derived from ``collect``'s own candles rather than from a second walk, so the grid
        cannot drift from the OHLCV grid — which is the property the real endpoints have by
        construction and the one a mock most easily breaks. A mock whose derivative bars
        landed on different open times would make the join look wrong in tests that are
        actually testing the join.

        The mark sits a hair off the close and the index a hair off the mark, so
        ``mark_index_basis_bps`` is small, signed and non-constant — a mock that returned
        mark == index would leave the basis a fabricated zero, which is the exact defect
        this whole series exists to remove.
        """
        if kind not in DERIVATIVE_KLINE_PATHS:
            raise ToolError("INVALID_DERIVATIVE_KIND", f"kind must be one of {sorted(DERIVATIVE_KINDS)}")
        candles = self.collect(symbol, timeframe, limit=limit, timeout_seconds=timeout_seconds).candles
        rows: list[dict[str, Any]] = []
        for i, candle in enumerate(candles):
            # Basis in the low single-digit bps, both signs, stable per (symbol, tf, bar).
            offset = (_frac(f"{symbol}|{timeframe}|basis{i}") - 0.5) * 0.0004
            if kind == "mark":
                value = round(candle.close * (1.0 + offset), 6)
            elif kind == "index":
                value = round(candle.close, 6)
            else:  # premium — a signed fraction, the funding basis at bar resolution
                value = round(offset, 8)
            rows.append({"timestamp": candle.open_time, "value": value})
        return rows

    def funding_history(self, symbol: str, *, records: int, timeout_seconds: int) -> list[dict[str, Any]]:
        """Deterministic 8h funding events on the same anchor grid (C9 mock feed)."""
        step = timedelta(hours=8)
        rows: list[dict[str, Any]] = []
        for i in range(min(records, 600)):
            moment = self._ANCHOR + i * step
            rate = round((_frac(f"{symbol}|funding|{i}") - 0.5) * 0.002, 8)
            rows.append({"timestamp": timeutil.format_iso(moment), "funding_rate": rate})
        return rows


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


def factory_candle_target(timeframe: str) -> int:
    """Bars covering ``FACTORY_DEPTH_DAYS`` at ``timeframe``, floored at
    ``MIN_FACTORY_BARS`` and clamped to ``MAX_CANDLES``.

    The factory's replay window is a calendar span, not a bar count — see
    ``FACTORY_DEPTH_DAYS`` — with a bar floor underneath it, because a span alone
    buys 1d too few trades for the scorer to judge (see ``MIN_FACTORY_BARS``). The
    floor binds 1d only; every faster timeframe is already deeper than it.
    """
    minutes = TIMEFRAMES[_require_timeframe(timeframe)]
    calendar_bars = FACTORY_DEPTH_DAYS * 1440 // minutes
    return max(1, min(max(calendar_bars, MIN_FACTORY_BARS), MAX_CANDLES))


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
            # Present but possibly None — the snapshot states what the venue reported,
            # including that it reported nothing. Dropping the keys when they are None
            # would make "the venue stopped sending flow" indistinguishable from "this
            # snapshot predates the flow legs", and the two deserve the same downstream
            # treatment only by accident.
            "quote_volume": c.quote_volume,
            "trade_count": c.trade_count,
            "taker_buy_base": c.taker_buy_base,
            "taker_buy_quote": c.taker_buy_quote,
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
    # Rows the venue serves per klines call, and the page budget that bounds one
    # collection's egress. MAX_CANDLES needs 40 full pages; the rest is head-room for
    # short pages, and the cap is what stops a misbehaving venue from paging forever.
    PAGE_LIMIT = 1500
    MAX_PAGES = 60

    def __init__(self, *, authorization: Authorization | None = None):
        # Egress authorization from the Safety-Flag Gate. Without it, collect() refuses
        # to open a socket — a directly-constructed collector cannot bypass the gate.
        self._authorization = authorization

    def collect(
        self, symbol: str, timeframe: str, *, limit: int, timeout_seconds: int
    ) -> MarketSnapshot:
        """Assemble ``limit`` closed candles, paging backward past the venue's page cap.

        Pages exactly like ``funding_history``: walk ``endTime`` to just before the
        oldest bar seen, stop when the venue runs out, refuse to spin without backward
        progress. A deep window is what makes the fast timeframes replayable at all —
        one page of 5m bars is five hours.
        """
        # Chokepoint: re-verify authorization at the moment of egress (defense in depth).
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )
        started = time.monotonic()
        now_ms = int(time.time() * 1000)
        # Keyed by open time: pages are requested by an exclusive endTime, but a venue
        # that repeats a boundary bar must not have it counted twice.
        collected: dict[int, Candle] = {}
        end_time: int | None = None
        pages = 0
        while len(collected) < limit and pages < self.MAX_PAGES:
            pages += 1
            # One extra row: the venue returns the still-forming candle last, and
            # dropping it must not shrink the caller's requested window.
            params: dict[str, Any] = {
                "symbol": symbol,
                "interval": timeframe,
                "limit": min(self.PAGE_LIMIT, limit - len(collected) + 1),
            }
            if end_time is not None:
                params["endTime"] = end_time
            request = urllib.request.Request(
                f"{self._ENDPOINT}?{urllib.parse.urlencode(params)}",
                method="GET", headers={"Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                    raw = response.read().decode("utf-8")
            except (TimeoutError, urllib.error.URLError):
                # Deliberately generic — never echo the URL (the R3 transport-error posture).
                raise ToolError("TOOL_TRANSPORT", "market-data request failed or timed out") from None
            page = self._parse(raw, now_ms=now_ms)
            if not page:
                break  # the venue has no more history in this direction
            oldest = min(open_ms for open_ms, _ in page)
            collected.update(page)
            if end_time is not None and oldest >= end_time:
                break  # no backward progress — refuse to spin
            end_time = oldest - 1
        latency_ms = int((time.monotonic() - started) * 1000)

        candles = [collected[open_ms] for open_ms in sorted(collected)]
        return MarketSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles[-limit:],
            source=self.source,
            is_synthetic=False,
            collector_version=self.tool_version,
            latency_ms=latency_ms,
        )

    def _parse(self, raw: str, *, now_ms: int) -> list[tuple[int, Candle]]:
        """One klines page as ``(open_time_ms, Candle)``, still-forming bar dropped."""
        try:
            rows = json.loads(raw)
        except ValueError:
            raise ToolError("MALFORMED_RESULT", "market-data backend returned an unparseable response") from None
        if not isinstance(rows, list):
            raise ToolError("MALFORMED_RESULT", "market-data backend returned an unparseable response")

        parsed: list[tuple[int, Candle]] = []
        for row in rows:
            # The venue's kline row is TWELVE fields, not seven:
            #   [0] open_time_ms  [1] open  [2] high  [3] low  [4] close  [5] volume
            #   [6] close_time_ms [7] quote_volume [8] trade_count
            #   [9] taker_buy_base [10] taker_buy_quote [11] unused
            # Fields 7-10 arrive in every response the collector already makes.
            if not isinstance(row, list) or len(row) < 7:
                raise ToolError("MALFORMED_RESULT", "market-data backend returned an unparseable response")
            try:
                open_ms, close_ms = int(row[0]), int(row[6])
                candle = Candle(
                    open_time=self._iso(open_ms),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    close_time=self._iso(close_ms),
                    # Optional, deliberately: the OHLCV legs above are the contract and a row
                    # missing them is malformed, but a row missing the flow legs is a venue
                    # that changed its payload — and the honest response to that is columns
                    # that go indeterminate (so flow specs stop trading), not a dead cycle.
                    quote_volume=_optional_float(row, 7),
                    trade_count=_optional_float(row, 8),
                    taker_buy_base=_optional_float(row, 9),
                    taker_buy_quote=_optional_float(row, 10),
                )
            except (TypeError, ValueError):
                raise ToolError("MALFORMED_RESULT", "market-data backend returned an unparseable response") from None
            if close_ms >= now_ms:
                continue  # still-forming candle — its close is still moving
            parsed.append((open_ms, candle))
        return parsed

    @staticmethod
    def _iso(epoch_ms: int) -> str:
        # Pure arithmetic, not fromtimestamp: Windows' underlying gmtime rejects
        # far-future epochs (OSError 22), and a venue-supplied timestamp is input.
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=epoch_ms)
        return timeutil.format_iso(epoch)

    def funding_history(self, symbol: str, *, records: int, timeout_seconds: int) -> list[dict[str, Any]]:
        """Real 8h funding events (public ``/fapi/v1/fundingRate``), oldest first.

        Pages backward past the 1000-row cap exactly like the source collector:
        walk ``endTime`` to just before the oldest event seen, stop when the venue
        runs out, refuse to spin without backward progress. Same grant as candles —
        another public endpoint of the already-authorized provider."""
        safety_gate.assert_authorization(
            self._authorization, required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id, now=timeutil.utc_now_iso(),
        )
        collected: list[dict[str, Any]] = []
        end_time: int | None = None
        pages = 0
        while len(collected) < records and pages < FUNDING_MAX_PAGES:
            pages += 1
            params: dict[str, Any] = {
                "symbol": symbol, "limit": min(FUNDING_PAGE_LIMIT, records - len(collected)),
            }
            if end_time is not None:
                params["endTime"] = end_time
            request = urllib.request.Request(
                f"https://fapi.binance.com/fapi/v1/fundingRate?{urllib.parse.urlencode(params)}",
                method="GET", headers={"Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                    raw = response.read().decode("utf-8")
            except (TimeoutError, urllib.error.URLError):
                raise ToolError("TOOL_TRANSPORT", "funding request failed or timed out") from None
            try:
                payload = json.loads(raw)
                page = [
                    {"timestamp": self._iso(int(item["fundingTime"])),
                     "funding_time_ms": int(item["fundingTime"]),
                     "funding_rate": float(item["fundingRate"])}
                    for item in payload
                ]
            except (TypeError, ValueError, KeyError):
                raise ToolError("MALFORMED_RESULT", "funding backend returned an unparseable response") from None
            if not page:
                break
            oldest = min(item["funding_time_ms"] for item in page)
            collected = page + collected
            if end_time is not None and oldest >= end_time:
                break  # no backward progress — refuse to spin
            end_time = oldest - 1
        rows = sorted(collected, key=lambda r: r["funding_time_ms"])[-records:]
        return [{"timestamp": r["timestamp"], "funding_rate": r["funding_rate"]} for r in rows]

    def derivative_price_klines(
        self, symbol: str, timeframe: str, *, kind: str, limit: int, timeout_seconds: int
    ) -> list[dict[str, Any]]:
        """One derivative price series on the OHLCV time grid, oldest first.

        ``kind`` selects the endpoint (:data:`DERIVATIVE_KLINE_PATHS`): the mark price, the
        index price, or the premium index. Returns
        ``[{"timestamp": <bar open, ISO>, "value": <bar close>}, ...]`` — only the close,
        because the close is the value that is known at the moment the row's own ``close``
        is known, and carrying a high/low no caller reads would invite someone to use one.

        **Keyed by bar OPEN time, and that is the whole alignment contract.** These series
        share the klines' interval and grid, so the bar opening at T here is the same bar as
        the OHLCV bar opening at T; joining on the open time and taking the close pairs two
        values that are both first known at the bar's close. That is simultaneous, not
        look-ahead — unlike the HTF leg, which spans a different grid and must therefore key
        on the higher candle's CLOSE. Two different rules because the two are different
        situations; see ``features._same_grid_column``.

        Pages backward exactly like ``collect`` and ``funding_history``: walk ``endTime`` to
        just before the oldest bar seen, stop when the venue runs out, refuse to spin without
        backward progress. Same grant as candles — a public endpoint of the already
        authorized provider.
        """
        if kind not in DERIVATIVE_KLINE_PATHS:
            raise ToolError("INVALID_DERIVATIVE_KIND", f"kind must be one of {sorted(DERIVATIVE_KINDS)}")
        safety_gate.assert_authorization(
            self._authorization, required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id, now=timeutil.utc_now_iso(),
        )
        path = DERIVATIVE_KLINE_PATHS[kind]
        now_ms = int(time.time() * 1000)
        collected: dict[int, float] = {}
        end_time: int | None = None
        pages = 0
        while len(collected) < limit and pages < DERIVATIVE_MAX_PAGES:
            pages += 1
            params: dict[str, Any] = {
                "symbol": symbol, "interval": timeframe,
                # One extra row for the still-forming bar this drops, so dropping it does
                # not shrink the caller's window (the ``collect`` rule).
                "limit": min(DERIVATIVE_PAGE_LIMIT, limit - len(collected) + 1),
            }
            if end_time is not None:
                params["endTime"] = end_time
            request = urllib.request.Request(
                f"https://fapi.binance.com/fapi/v1/{path}?{urllib.parse.urlencode(params)}",
                method="GET", headers={"Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                    raw = response.read().decode("utf-8")
            except (TimeoutError, urllib.error.URLError):
                # Never echo the URL (the R3 transport-error posture).
                raise ToolError("TOOL_TRANSPORT", f"{kind} price request failed or timed out") from None
            page = self._parse_derivative_page(raw, kind=kind, now_ms=now_ms)
            if not page:
                break
            oldest = min(page)
            collected.update(page)
            if end_time is not None and oldest >= end_time:
                break  # no backward progress — refuse to spin
            end_time = oldest - 1

        ordered = sorted(collected)[-limit:]
        return [{"timestamp": self._iso(open_ms), "value": collected[open_ms]} for open_ms in ordered]

    def _parse_derivative_page(self, raw: str, *, kind: str, now_ms: int) -> dict[int, float]:
        """One derivative-kline page as ``{open_time_ms: close}``, still-forming bar dropped.

        These rows reuse the twelve-slot kline shape but the venue fills the volume-ish slots
        with placeholder zeros, so only indices 0 (open time), 4 (close) and 6 (close time)
        are read. The premium index is legitimately **negative** when the perpetual trades
        below its index, so no sign constraint is applied here — a caller that needs
        positivity (the basis divisor) checks it where it needs it.
        """
        try:
            rows = json.loads(raw)
        except ValueError:
            raise ToolError("MALFORMED_RESULT", f"{kind} price backend returned an unparseable response") from None
        if not isinstance(rows, list):
            raise ToolError("MALFORMED_RESULT", f"{kind} price backend returned an unparseable response")

        parsed: dict[int, float] = {}
        for row in rows:
            if not isinstance(row, list) or len(row) < 7:
                raise ToolError("MALFORMED_RESULT", f"{kind} price backend returned an unparseable response")
            try:
                open_ms, close_ms, value = int(row[0]), int(row[6]), float(row[4])
            except (TypeError, ValueError):
                raise ToolError("MALFORMED_RESULT", f"{kind} price backend returned an unparseable response") from None
            if close_ms >= now_ms:
                continue  # still forming — its close is still moving
            parsed[open_ms] = value
        return parsed

    def exchange_info(self, *, timeout_seconds: int) -> dict[str, Any]:
        """The venue's own trading rules (public ``/fapi/v1/exchangeInfo``), raw.

        Same grant as candles and funding — a third public endpoint of the already
        authorized provider, so no new provider, grant, or gate. Returns the payload
        unparsed; ``live_filters.parse_symbol_filters`` turns it into the per-symbol lot
        step / minimum notional / price tick, because that parsing must be testable
        without a network and the numbers must never be written from memory.

        Deliberately NOT on ``MockMarketDataCollector``: a mock that answered here would
        hand live sizing invented lot steps, which is the one number nobody may invent.
        Its absence is what makes a mock run refuse to size rather than size wrongly."""
        safety_gate.assert_authorization(
            self._authorization, required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id, now=timeutil.utc_now_iso(),
        )
        request = urllib.request.Request(
            "https://fapi.binance.com/fapi/v1/exchangeInfo",
            method="GET", headers={"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except (TimeoutError, urllib.error.URLError):
            raise ToolError("TOOL_TRANSPORT", "exchange-info request failed or timed out") from None
        try:
            payload = json.loads(raw)
        except ValueError:
            raise ToolError("MALFORMED_RESULT", "exchange-info returned an unparseable response") from None
        if not isinstance(payload, dict):
            raise ToolError("MALFORMED_RESULT", "exchange-info returned an unparseable response")
        return payload


# --- C9 liquidation feed (Coinalyze — its own provider, key, and grant) -------

class LiquidationFeed(Protocol):
    """The Coinalyze-backed derivative feed: liquidations and open interest.

    One object, one provider, one grant — the name is historical (liquidations came
    first) and kept so every selection site stays put."""

    feed_id: str

    def liquidation_history(self, symbol: str, *, days: int, timeout_seconds: int) -> list[dict[str, Any]]: ...

    def open_interest_history(self, symbol: str, *, days: int, timeout_seconds: int,
                              interval: str = OI_INTERVAL_DAILY) -> list[dict[str, Any]]: ...


class PerSymbolFeedCache:
    """One vendor read per (symbol, series) for the life of one fan-out.

    Both Coinalyze series are **per-symbol**: `liquidation_history` and
    `open_interest_history` take a symbol and a day count and nothing else. But
    `cycle.attach_feeds` runs once per (symbol, TIMEFRAME), so the shipped 20-context
    fan-out asked for each symbol's series **four times** — 40 requests to read 10.
    Downstream that redundancy was invisible: the series is aligned onto each timeframe's
    candles after the fetch, so four identical responses produced four correct frames.

    It stopped being invisible when the hourly store added five more requests per fire.
    Measured on the first fire after that deployed: BNB/BTC/DOGE returned `ok` and
    ETH/SOL came back `degraded` on **every** Coinalyze series — including the daily
    open interest and the liquidations the router already depended on. Contexts are
    visited in a fixed order, so a per-window request cap always lands on whoever is
    last, and the first symbols look healthy while the last ones silently lose features.

    Caching is behaviour-preserving rather than a trade-off, and that is the reason to
    prefer it over raising the cap or slowing the loop: within one fire the answer to
    "what was this symbol's daily series" cannot legitimately differ between the 15m
    context and the 4h one. The cache lives for **one fan-out** — it is constructed per
    call in `run_pool_cycle` and thrown away — so it can never serve a stale day to a
    later fire.

    Failures are cached too, deliberately. A vendor that just refused this symbol will
    refuse it again in the same second, and re-asking three more times per fire is how
    the cap was reached in the first place; the refusal is re-raised identically so every
    context still records its own reason code.
    """

    def __init__(self, feed: Any):
        self._feed = feed
        self._results: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        self._errors: dict[tuple[str, ...], BaseException] = {}
        self.calls = 0

    @property
    def feed_id(self) -> str:
        return getattr(self._feed, "feed_id", "none")

    @property
    def network_egress(self) -> bool:
        return bool(getattr(self._feed, "network_egress", False))

    def _cached(self, key: tuple[str, ...], fetch: Any) -> list[dict[str, Any]]:
        if key in self._errors:
            raise self._errors[key]
        if key in self._results:
            return self._results[key]
        self.calls += 1
        try:
            rows = fetch()
        except BaseException as exc:  # noqa: BLE001 — re-raised unchanged below
            self._errors[key] = exc
            raise
        self._results[key] = rows
        return rows

    def liquidation_history(self, symbol: str, *, days: int, timeout_seconds: int) -> list[dict[str, Any]]:
        return self._cached(
            ("liquidations", str(symbol), str(days)),
            lambda: self._feed.liquidation_history(
                symbol, days=days, timeout_seconds=timeout_seconds),
        )

    def open_interest_history(self, symbol: str, *, days: int, timeout_seconds: int,
                              interval: str = OI_INTERVAL_DAILY) -> list[dict[str, Any]]:
        return self._cached(
            ("open_interest", str(symbol), str(days), str(interval)),
            lambda: self._feed.open_interest_history(
                symbol, days=days, timeout_seconds=timeout_seconds, interval=interval),
        )


class ReferenceCandleCache:
    """One reference-symbol read per (timeframe, depth) for the life of one fan-out.

    The same redundancy :class:`PerSymbolFeedCache` was built for, arriving by a different
    door. The reference series is always :data:`REFERENCE_SYMBOL`, so the only thing that
    varies across a fan-out is the timeframe — but ``attach_reference`` runs per
    (symbol, timeframe), so a 5-symbol × 4-timeframe fan-out asks for **four** distinct BTC
    series sixteen times: once for every non-proxy symbol at every timeframe. Four reads
    would answer all sixteen questions identically, because within one fire "what were BTC's
    hourly candles" cannot legitimately differ depending on which altcoin is asking.

    Same contract as the feed cache, for the same reasons: constructed per fan-out and
    thrown away, so it can never serve a stale bar to a later fire; **failures cached too**,
    because a venue that just refused this series will refuse it again in the same second and
    re-asking three more times per fire is how a rate cap gets reached; and the refusal is
    re-raised unchanged, so every context still records its own reason code.
    """

    def __init__(self, collector: Any):
        self._collector = collector
        self._results: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._errors: dict[tuple[str, str], BaseException] = {}
        self.calls = 0

    def candles(self, timeframe: str, *, limit: int, now: str) -> list[dict[str, Any]]:
        key = (str(timeframe), str(limit))
        if key in self._errors:
            raise self._errors[key]
        if key in self._results:
            return self._results[key]
        self.calls += 1
        try:
            snapshot, _record = collect_market_data(
                REFERENCE_SYMBOL, timeframe, collector=self._collector, now=now, limit=limit
            )
        except BaseException as exc:  # noqa: BLE001 — re-raised unchanged below
            self._errors[key] = exc
            raise
        rows = list(snapshot.get("candles") or [])
        self._results[key] = rows
        return rows


class NoLiquidationFeed:
    """Default: the feed is ABSENT (not degraded) — features keep the source's
    legacy no-series constants, exactly the pre-C9 behavior."""

    feed_id = "none"
    network_egress = False

    def liquidation_history(self, symbol: str, *, days: int, timeout_seconds: int) -> list[dict[str, Any]]:
        raise ToolError("FEED_ABSENT", "no liquidation feed is configured")

    def open_interest_history(self, symbol: str, *, days: int, timeout_seconds: int,
                              interval: str = OI_INTERVAL_DAILY) -> list[dict[str, Any]]:
        raise ToolError("FEED_ABSENT", "no open-interest feed is configured")


class CoinalyzeLiquidationFeed:
    """Coinalyze daily long/short liquidation aggregates for the Binance perp.

    Its own provider (``coinalyze_market_data``): own API key (read by NAME at call
    time, sent in the ``api_key`` header, never logged) and own per-machine grant —
    authorizing Binance never authorizes Coinalyze. The still-forming current day is
    dropped (the source loader's rule): a partial day's liquidation total would read
    as a artificially quiet day."""

    feed_id = COINALYZE
    provider_id = COINALYZE
    network_egress = True
    _ENDPOINT = "https://api.coinalyze.net/v1/liquidation-history"
    _OI_ENDPOINT = "https://api.coinalyze.net/v1/open-interest-history"

    def __init__(self, *, api_key_env: str = "COINALYZE_API_KEY",
                 authorization: Authorization | None = None):
        self._api_key_env = api_key_env  # the NAME of the env var, never the value
        self._authorization = authorization

    def liquidation_history(self, symbol: str, *, days: int, timeout_seconds: int) -> list[dict[str, Any]]:
        safety_gate.assert_authorization(
            self._authorization, required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id, now=timeutil.utc_now_iso(),
        )
        api_key = os.environ.get(self._api_key_env)
        if not api_key:
            raise ToolError("NO_API_KEY", f"environment variable {self._api_key_env} is not set")
        now_s = int(time.time())
        params = urllib.parse.urlencode({
            "symbols": f"{symbol}_PERP.A",  # Coinalyze's code for a Binance USDT perp
            "interval": "daily",
            # Explicit range: the API's default window is hour-based and silently
            # truncates a daily request (the source client's documented pitfall).
            "from": now_s - (int(days) + 2) * 86400,
            "to": now_s,
        })
        request = urllib.request.Request(
            f"{self._ENDPOINT}?{params}", method="GET",
            headers={"Accept": "application/json", "api_key": api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except (TimeoutError, urllib.error.URLError):
            # Deliberately generic — never echo the URL or key.
            raise ToolError("TOOL_TRANSPORT", "liquidation request failed or timed out") from None
        return self._parse(raw, days, now_s=now_s)

    def _parse(self, raw: str, days: int, *, now_s: int) -> list[dict[str, Any]]:
        try:
            payload = json.loads(raw)
        except ValueError:
            raise ToolError("MALFORMED_RESULT", "liquidation backend returned an unparseable response") from None
        if isinstance(payload, dict) and "data" in payload:
            payload = payload["data"]
        if not isinstance(payload, list):
            raise ToolError("MALFORMED_RESULT", "liquidation backend returned an unparseable response")
        rows: list[dict[str, Any]] = []
        day_start_today = (now_s // 86400) * 86400
        for item in payload:
            for h in (item.get("history") or []) if isinstance(item, dict) else []:
                try:
                    t = int(h["t"])
                    t_s = t // 1000 if t > 10_000_000_000 else t
                    if t_s >= day_start_today:
                        continue  # still-forming current day — dropped
                    rows.append({
                        "timestamp": BinanceFuturesCollector._iso(t_s * 1000),
                        "long_liquidation": float(h.get("l") or 0.0),
                        "short_liquidation": float(h.get("s") or 0.0),
                    })
                except (TypeError, ValueError, KeyError):
                    raise ToolError("MALFORMED_RESULT",
                                    "liquidation backend returned an unparseable response") from None
        rows.sort(key=lambda r: r["timestamp"])
        return rows[-days:]

    def open_interest_history(self, symbol: str, *, days: int, timeout_seconds: int,
                              interval: str = OI_INTERVAL_DAILY) -> list[dict[str, Any]]:
        """Open-interest closes for the same perp. Same provider, same grant.

        Open interest is the third leg of the positioning picture the runtime already
        collects two of (funding, liquidations): how much position is *outstanding*,
        as opposed to what it costs to hold (funding) or what was forcibly closed
        (liquidations). It rides the existing ``coinalyze_market_data`` provider — same
        key, same authorization, same egress chokepoint — so it widens what the runtime
        reads, never what it is allowed to reach.

        Like the liquidation series, the still-forming current period is dropped: a partial
        close is not a close.

        ``interval`` defaults to ``daily``, which is what every feature path reads and the only
        depth that covers the factory's 500-day replay — hourly history stops ~84 days back
        (measured 2026-07-29; older windows return empty, and Binance's own endpoint is
        shallower). ``OI_INTERVAL_1H`` exists for :mod:`oi_store`, which accumulates the hourly
        series into a store the runtime retains itself and feeds to nothing until it is deep
        enough to replace the daily one. The parameter is the whole change here: the endpoint,
        the key, the grant and the parse are shared, so the two intervals cannot drift apart."""
        if interval not in OI_INTERVALS:
            raise ToolError(
                "OI_INTERVAL_UNKNOWN",
                f"open-interest interval {interval!r} is not one of {sorted(OI_INTERVALS)}",
            )
        safety_gate.assert_authorization(
            self._authorization, required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id, now=timeutil.utc_now_iso(),
        )
        api_key = os.environ.get(self._api_key_env)
        if not api_key:
            raise ToolError("NO_API_KEY", f"environment variable {self._api_key_env} is not set")
        now_s = int(time.time())
        params = urllib.parse.urlencode({
            "symbols": f"{symbol}_PERP.A",
            "interval": interval,
            "from": now_s - (int(days) + 2) * 86400,
            "to": now_s,
        })
        request = urllib.request.Request(
            f"{self._OI_ENDPOINT}?{params}", method="GET",
            headers={"Accept": "application/json", "api_key": api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except (TimeoutError, urllib.error.URLError):
            raise ToolError("TOOL_TRANSPORT", "open-interest request failed or timed out") from None
        rows = self._parse_open_interest(raw, days, now_s=now_s, interval=interval)
        # `days` bounds a DAILY series one row per day. An hourly series has 24, so trimming to
        # the same number would silently return the last `days` hours and a coverage count built
        # on it would be 24x short. The row cap is the vendor's (~2000/response) either way.
        return rows if interval != OI_INTERVAL_DAILY else rows[-days:]

    def _parse_open_interest(self, raw: str, days: int, *, now_s: int,
                             interval: str = OI_INTERVAL_DAILY) -> list[dict[str, Any]]:
        try:
            payload = json.loads(raw)
        except ValueError:
            raise ToolError("MALFORMED_RESULT", "open-interest backend returned an unparseable response") from None
        if isinstance(payload, dict) and "data" in payload:
            payload = payload["data"]
        if not isinstance(payload, list):
            raise ToolError("MALFORMED_RESULT", "open-interest backend returned an unparseable response")
        rows: list[dict[str, Any]] = []
        # The still-forming period, sized to the interval actually requested. Reading the day
        # boundary for an hourly series would drop every row since midnight — up to 23 complete
        # hours thrown away as "still forming", which on a store that accumulates would be a
        # permanent hole rather than a delay.
        period = OI_INTERVAL_SECONDS.get(interval, 86400)
        period_start_now = (now_s // period) * period
        for item in payload:
            for h in (item.get("history") or []) if isinstance(item, dict) else []:
                try:
                    t = int(h["t"])
                    t_s = t // 1000 if t > 10_000_000_000 else t
                    if t_s >= period_start_now:
                        continue  # still-forming current period — dropped
                    rows.append({
                        "timestamp": BinanceFuturesCollector._iso(t_s * 1000),
                        # The OHLC of open interest across the period; the close is the
                        # standing position at its end, which is the quantity a
                        # point-in-time feature should carry.
                        "open_interest": float(h["c"]),
                    })
                except (TypeError, ValueError, KeyError):
                    raise ToolError("MALFORMED_RESULT",
                                    "open-interest backend returned an unparseable response") from None
        rows.sort(key=lambda r: r["timestamp"])
        # Trimming to `days` happens in the caller, which knows whether one row means one day.
        return rows


def select_liquidation_feed(*, now: str | None = None, root: Path | None = None) -> LiquidationFeed:
    """Choose the liquidation feed — the enforced Safety-Flag Gate chokepoint.

    Defaults to :class:`NoLiquidationFeed` (feed absent → the features keep the
    source's legacy constants). The real Coinalyze feed is returned ONLY when both
    (a) the caller opts in via ``MVP_LIQUIDATION_FEED=coinalyze_market_data`` AND
    (b) the gate authorizes ``network_access`` for that provider's own local
    activation record. The env var alone fails closed."""
    return safety_gate.select_gated(
        env_var=LIQUIDATION_FEED_ENV,
        opt_in_value=COINALYZE,
        flags=_NETWORK_FLAGS,
        provider_id=COINALYZE,
        default_factory=NoLiquidationFeed,
        gated_factory=lambda authorization: CoinalyzeLiquidationFeed(authorization=authorization),
        now=now,
        root=root,
    )


# --- reference price: one candle, for verifying a hand-declared notional ----------------

REFERENCE_PRICE_MAX_AGE_SECONDS = 300

PRICE_SYNTHETIC = "REFERENCE_PRICE_SYNTHETIC"
PRICE_ABSENT = "REFERENCE_PRICE_ABSENT"
PRICE_STALE = "REFERENCE_PRICE_STALE"
PRICE_UNREADABLE = "REFERENCE_PRICE_UNREADABLE"


def read_reference_price(
    symbol: str,
    *,
    collector: MarketDataCollector,
    now: str,
    timeframe: str = "1m",
    timeout_seconds: int = 10,
) -> tuple[float | None, str | None]:
    """The venue's most recent close for ``symbol``, or ``(None, reason_code)``.

    Goes through :func:`collect_market_data` on the gated collector rather than opening its
    own connection — same tool identity, same evidence record, no new tool id to authorize.

    **Every uncertain answer is a refusal**, because this exists to bound a live order and a
    price that reads LOW is precisely what lets a bigger position past a cap:

    - synthetic — ``MockMarketDataCollector`` synthesises a hash-derived walk. It is a
      plausible-looking number with no relationship to the venue, which is worse than none.
    - absent — a snapshot with no candles.
    - stale — the last close is older than five minutes, so the price predates whatever moved
      the market since.
    - unreadable — a malformed close, or the collector failing closed.

    Returns the reason code rather than raising: the caller reports it beside its other
    refusals so an operator who must fix several things sees them in one run.
    """
    try:
        snapshot, _ = collect_market_data(
            symbol, timeframe, collector=collector, now=now, limit=1,
            timeout_seconds=timeout_seconds,
        )
    except MvpRuntimeError:
        return None, PRICE_UNREADABLE

    if snapshot.get("is_synthetic"):
        return None, PRICE_SYNTHETIC

    candles = snapshot.get("candles") or []
    if not candles:
        return None, PRICE_ABSENT

    last = candles[-1]
    try:
        close = float(last["close"])
        closed_at = timeutil.parse_iso(str(last["close_time"]))
    except (KeyError, TypeError, ValueError):
        return None, PRICE_UNREADABLE
    if not (close > 0):
        return None, PRICE_UNREADABLE

    try:
        age = (timeutil.parse_iso(now) - closed_at).total_seconds()
    except (TypeError, ValueError):
        return None, PRICE_UNREADABLE
    if age > REFERENCE_PRICE_MAX_AGE_SECONDS:
        return None, PRICE_STALE

    return close, None
