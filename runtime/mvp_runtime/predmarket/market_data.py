"""PM1 — read-only market data from Kalshi and Polymarket. Nothing here trades.

The first code of the prediction-market track, and the narrowest possible piece of it: two
public read adapters and one normalized shape for what they return. No account, no key, no
funds, and no path to an order — this module imports nothing that can place one.

**The two venues quote the same thing in different words, so normalization happens here.**
Downstream (pair matching, the fee-adjusted detector) compares Kalshi against Polymarket;
if either one's vocabulary leaked past this boundary, every comparison would carry a units
bug waiting to be found by a fake arbitrage signal. So one shape, one unit — probability in
``[0, 1]`` for the YES side, sizes in contracts — and each adapter converts into it.

**Field names come from the venues' own documentation, checked 2026-07-26, not memory.**
That mattered immediately: Kalshi now serves prices as **decimal-dollar strings**
(``yes_bid_dollars``, e.g. ``"0.5600"``) rather than the integer cents an older API used, so
a parser written from recollection would have read nothing at all. A Kalshi contract settles
at $1, so dollars *are* probability and no conversion is needed beyond parsing.

**Polymarket needs two calls, and the second one is the honest one.** Gamma lists markets
and carries ``clobTokenIds``; its ``outcomePrices`` are a last/derived figure, not a quote
anyone will trade with. The real best bid/ask lives in the CLOB order book
(``/book?token_id=...``), so a market is only quoted here once its book has been read. A
market whose book was not read is returned **unquoted**, never with a guessed price.

**No bid is not a bid of zero.** Every price is ``float | None``
(:func:`~..coerce.as_optional_float`), and a non-positive or >= 1 value becomes ``None`` —
those are the venue's ways of saying "nobody is quoting this side", and recording them as
0.0 would manufacture a 100%-margin opportunity out of an empty book. This is the single
most important line in the module.

**Degrade, never block** (the ``MARKET_DATA_DEGRADED`` precedent): a venue that is down,
rate-limited or unreachable produces a recorded degrade, not a failed run. One venue being
readable while the other is not simply means no cross-venue comparison this scan.

**The gate is the only thing between a config typo and an outbound socket.** Both endpoints
are public and need no credential, so ``safety_gate.select_gated`` — one grant per venue,
``kalshi_market_data`` / ``polymarket_market_data``, each separately scoped and revocable —
is the whole boundary. The default is a deterministic mock that opens no socket, and the env
var alone fails closed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from runtime.read_only_kernel import integrity

from .. import safety_gate, timeutil
from ..coerce import as_optional_float
from ..errors import ToolBlocked, ToolError
from ..safety_gate import NETWORK_ACCESS, Authorization

PREDMARKET_TOOL_ID = "predmarket.market_data.readonly"
PREDMARKET_TOOL_VERSION = "0.1.0"
PREDMARKET_TOOL_CLASS = "read"

KALSHI = "kalshi"
POLYMARKET = "polymarket"
# Named for the door, because that is what the operator uses: the account, the key, the host
# and the funds are all Binance's, and every market here arrives through Binance's prediction
# API. The markets themselves are Predict.fun's on BNB Smart Chain — which matters at exactly
# one moment, when the operator compares resolution rules — and the payload says so on every
# row (`vendor: "PREDICT_FUN"`), so that fact is carried by the data rather than by the label.
BINANCE = "binance"
VENUES = (KALSHI, POLYMARKET, BINANCE)

# One grant per venue, per the roadmap: they are independent capabilities, not a failover
# chain, so revoking one must not touch the others.
KALSHI_PROVIDER_ID = "kalshi_market_data"
POLYMARKET_PROVIDER_ID = "polymarket_market_data"
BINANCE_PROVIDER_ID = "binance_prediction"
KALSHI_ENV = "MVP_KALSHI_MARKET_DATA"
POLYMARKET_ENV = "MVP_POLYMARKET_MARKET_DATA"
BINANCE_ENV = "MVP_BINANCE_PREDICTION"
# The one venue of the three that is NOT keyless. Every prediction endpoint is SIGNED, so a
# key and a secret are both needed. Metadata only ever: the names are reported, the values are
# never logged, audited or recorded (the standing secrets rule), and because the signature
# rides in the query string a transport failure is reported generically.
BINANCE_API_KEY_ENV = "BINANCE_PREDICTION_API_KEY"
BINANCE_API_SECRET_ENV = "BINANCE_PREDICTION_API_SECRET"

_NETWORK_FLAGS = (NETWORK_ACCESS,)

# Every outbound read identifies this client by name and purpose. Not decoration: Polymarket
# sits behind Cloudflare, which refuses Python's default `Python-urllib/3.x` signature with
# **error 1010** ("access denied based on your browser's signature") — a 403 our transport
# layer reported as a bare TOOL_TRANSPORT, indistinguishable from the venue being down. Found
# 2026-07-27 on the deployed scheduler, where Polymarket was the only venue failing.
#
# This says who we are; it does not pretend to be a browser. If a venue blocks an honestly
# identified client, that is the venue declining to be read, and the answer is to stop reading
# it — not to wear a costume.
USER_AGENT = "thomas-agent/0.1 (prediction-market observation; +read-only)"

# A read that failed is recorded, never silent — the crypto MARKET_DATA_DEGRADED posture.
PREDMARKET_DEGRADED = "PREDMARKET_DATA_DEGRADED"
# Deliberately NOT the same code as a degrade. "Nobody configured a key" and "the venue is
# down" are different facts about a scan, and a report that conflated them would show an
# outage where there was an unfinished setup step.
API_KEY_MISSING = "PREDMARKET_API_KEY_MISSING"

# Bounds. A scan asks for markets, not for the whole venue: the per-scan cap is a scheduler
# decision (roadmap decision #1, still open), and these are the hard ceilings under it.
DEFAULT_MARKET_LIMIT = 100
MAX_MARKET_LIMIT = 500
# Polymarket prices one market per order-book call, so this bounds the calls a single
# collection can make. Markets past it come back unquoted rather than silently dropped.
DEFAULT_BOOK_LIMIT = 25
MAX_BOOK_LIMIT = 100


def _probability(value: Any) -> float | None:
    """A venue price as a probability in ``(0, 1)``, or ``None``.

    ``None`` for anything that did not parse **and** for 0 or 1 and beyond. Both venues
    report an empty side as ``0`` (Kalshi ``"0.0000"``, an empty CLOB book side), and a
    resting order at exactly 0 or 1 does not exist. Reading those as prices would invent a
    free contract; the honest answer is "this side is not quoted".
    """
    parsed = as_optional_float(value)
    if parsed is None or not (0.0 < parsed < 1.0):
        return None
    return parsed


def _size(value: Any) -> float | None:
    """A resting size in contracts, or ``None``. Zero is no size, which is not a size."""
    parsed = as_optional_float(value)
    if parsed is None or parsed <= 0.0:
        return None
    return parsed


@dataclass(frozen=True)
class VenueQuote:
    """Best bid/ask for the YES side of one market, in probability units.

    NO is not carried separately: on a binary market NO is 1 − YES by construction, and
    deriving it once downstream beats two venues disagreeing about which side they meant.
    """

    yes_bid: float | None = None
    yes_ask: float | None = None
    yes_bid_size: float | None = None
    yes_ask_size: float | None = None

    def quoted(self) -> bool:
        """Both sides present and ordered. An inverted book (bid >= ask) is a crossed or
        stale read, not an instant profit, so it does not count as quoted."""
        return (
            self.yes_bid is not None
            and self.yes_ask is not None
            and self.yes_bid < self.yes_ask
        )

    def mid(self) -> float | None:
        if not self.quoted():
            return None
        return round((float(self.yes_bid) + float(self.yes_ask)) / 2.0, 6)

    def as_dict(self) -> dict[str, Any]:
        return {
            "yes_bid": self.yes_bid,
            "yes_ask": self.yes_ask,
            "yes_bid_size": self.yes_bid_size,
            "yes_ask_size": self.yes_ask_size,
            "quoted": self.quoted(),
            "mid": self.mid(),
        }


@dataclass(frozen=True)
class PredMarket:
    """One market on one venue, in this package's vocabulary.

    ``market_id`` is what the venue's own order book keys on — a Kalshi ticker, a Polymarket
    CLOB token id — so it is what a later phase would trade. ``group_id`` is the venue's
    grouping above it (Kalshi ``event_ticker``, Polymarket ``conditionId``), which pair
    matching needs because the same real-world event is one group and several markets.
    """

    venue: str
    market_id: str
    group_id: str | None
    title: str
    close_time: str | None
    status: str | None
    quote: VenueQuote = field(default_factory=VenueQuote)
    category: str | None = None
    # The venue's own fee rate for THIS market, when it publishes one (Binance's prediction
    # API returns `feeRateBps` per topic). A rate the venue stated beats a rate from a table.
    fee_rate_bps: int | None = None
    # The constituent markets, when the venue says this one is built out of others — Kalshi
    # publishes `mve_selected_legs` for its multivariate parlays. `None` means the venue did
    # not say so; an empty tuple never occurs. Carried rather than reduced to a boolean
    # because the legs are the evidence: "this is a parlay" is arguable, "this is the basket
    # of these nine tickers" is not. Note `market_type` cannot stand in — Kalshi reports a
    # nine-leg parlay as "binary", which is true of its payoff and useless as a filter.
    derived_from: tuple[str, ...] | None = None
    # Whether the venue says it will currently accept an order (Polymarket's
    # `enableOrderBook`/`acceptingOrders`). `None` is *did not say*, never *no*.
    accepting_orders: bool | None = None
    # Traded volume as the venue reports it (Kalshi `volume_fp`, Gamma `volumeNum`, Binance
    # `tradeVolume`). Not comparable ACROSS venues — different units, different histories —
    # and never used as one. It ranks a venue's own markets against each other, which is how
    # discovery finds the head of each listing instead of whatever the page happened to hold.
    volume: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "venue": self.venue,
            "market_id": self.market_id,
            "group_id": self.group_id,
            "title": self.title,
            "category": self.category,
            "close_time": self.close_time,
            "status": self.status,
            "fee_rate_bps": self.fee_rate_bps,
            "derived_from": list(self.derived_from) if self.derived_from is not None else None,
            "accepting_orders": self.accepting_orders,
            "volume": self.volume,
            "quote": self.quote.as_dict(),
        }


@dataclass
class PredMarketSnapshot:
    venue: str
    markets: list[PredMarket]
    source: str
    is_synthetic: bool
    collector_version: str = PREDMARKET_TOOL_VERSION
    latency_ms: int = 0
    # Whether order books were asked for at all. Without it, a discovery read's
    # `quoted_count: 0` is indistinguishable from a venue with an empty book on every
    # market — the same "absence read as a value" mistake this module exists to avoid.
    quotes_requested: bool = True


class PredMarketCollector(Protocol):
    venue: str
    tool_id: str
    tool_version: str

    def list_markets(
        self,
        *,
        limit: int,
        timeout_seconds: int,
        market_ids: Sequence[str] | None = None,
        with_quotes: bool = True,
    ) -> PredMarketSnapshot: ...


# --- the inert default ----------------------------------------------------------

class MockPredMarketCollector:
    """Deterministic, network-free collector for tests and pre-gate runs.

    Markets are a pure function of ``(venue, index)`` and honestly marked
    ``is_synthetic=True``, so nothing downstream can mistake a rehearsal for an observation.
    The two venues' mocks deliberately produce **the same titles at different prices** —
    that is the shape pair matching and the detector are built against, and a mock that
    priced them identically would let a detector bug pass.
    """

    tool_id = PREDMARKET_TOOL_ID
    tool_version = f"{PREDMARKET_TOOL_VERSION}-mock"
    network_egress = False

    _TITLES = (
        "Will the Fed cut rates at the next meeting?",
        "Will BTC close above 100k on Dec 31?",
        "Will the incumbent win the next general election?",
        "Will inflation print below 3% next month?",
    )

    def __init__(self, venue: str = KALSHI) -> None:
        self.venue = require_venue(venue)
        self.source = f"mock.{self.venue}"

    def list_markets(
        self,
        *,
        limit: int,
        timeout_seconds: int,
        market_ids: Sequence[str] | None = None,
        with_quotes: bool = True,
    ) -> PredMarketSnapshot:
        # A small deterministic offset per venue, so the same event is priced differently on
        # the two mocks — the cross-venue gap the detector is supposed to find.
        skew = 0.03 if self.venue == POLYMARKET else 0.0
        markets: list[PredMarket] = []
        for index, title in enumerate(self._TITLES[: max(0, limit)]):
            bid = round(0.40 + 0.05 * index + skew, 4)
            markets.append(PredMarket(
                venue=self.venue,
                market_id=f"{self.venue.upper()}-MOCK-{index:02d}",
                group_id=f"{self.venue.upper()}-MOCK-EVENT-{index:02d}",
                title=title,
                category="mock",
                close_time="2026-12-31T23:59:00Z",
                status="active",
                quote=VenueQuote(
                    yes_bid=bid,
                    yes_ask=round(bid + 0.02, 4),
                    yes_bid_size=100.0,
                    yes_ask_size=100.0,
                ),
            ))
        if market_ids is not None:
            wanted = set(market_ids)
            markets = [m for m in markets if m.market_id in wanted]
        if not with_quotes:
            # The mock's prices cost nothing, but it honours the contract anyway: a caller
            # that forgot to ask for quotes must behave the same here as against a venue.
            markets = [
                PredMarket(
                    venue=m.venue, market_id=m.market_id, group_id=m.group_id, title=m.title,
                    close_time=m.close_time, status=m.status, category=m.category,
                    fee_rate_bps=m.fee_rate_bps, derived_from=m.derived_from,
                    accepting_orders=m.accepting_orders, volume=m.volume, quote=VenueQuote(),
                )
                for m in markets
            ]
        return PredMarketSnapshot(
            venue=self.venue,
            markets=markets,
            source=self.source,
            is_synthetic=True,
            collector_version=self.tool_version,
            quotes_requested=with_quotes,
        )


def require_venue(venue: Any) -> str:
    if venue not in VENUES:
        raise ToolBlocked("INVALID_VENUE", f"venue must be one of {sorted(VENUES)}")
    return str(venue)


def _clamp(value: Any, default: int, ceiling: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, ceiling))


# --- the records ----------------------------------------------------------------

def collect_pred_markets(
    venue: str,
    *,
    collector: PredMarketCollector,
    now: str,
    limit: int = DEFAULT_MARKET_LIMIT,
    timeout_seconds: int = 10,
    market_ids: Sequence[str] | None = None,
    with_quotes: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Collect one venue's open markets. Returns ``(snapshot, tool_use_record)``.

    Mirrors ``crypto.market_data.collect_market_data`` field for field, including the
    input/output hashes that bind the record to the snapshot it describes. Fails closed
    (``ToolBlocked``) on an invalid venue or a collector error — the *caller* decides to
    degrade, exactly as the crypto cycle does, because only the caller knows whether the
    other venue answered.
    """
    venue = require_venue(venue)
    limit = _clamp(limit, DEFAULT_MARKET_LIMIT, MAX_MARKET_LIMIT)
    try:
        result = collector.list_markets(
            limit=limit, timeout_seconds=timeout_seconds, market_ids=market_ids,
            with_quotes=with_quotes,
        )
    except (ToolError, TimeoutError) as exc:
        raise ToolBlocked("TOOL_ERROR", str(exc)) from exc

    markets = [m.as_dict() for m in result.markets if isinstance(m, PredMarket)]
    quoted = [m for m in markets if m["quote"]["quoted"]]
    snapshot = {
        "snapshot_version": "0.1",
        "venue": venue,
        "markets": markets,
        "market_count": len(markets),
        # Reported separately on purpose: an unquoted market is a market this scan cannot
        # compare, and a scan where that number is most of the list is a degraded scan even
        # though every call succeeded.
        "quoted_count": len(quoted),
        # Reported so `quoted_count: 0` can never be misread. A discovery read asks for no
        # books at all, and "nobody is quoting these" is a different finding from "we did
        # not ask" — the module's own rule about absences, applied to itself.
        "quotes_requested": bool(getattr(result, "quotes_requested", True)),
        "source": result.source,
        "is_synthetic": bool(result.is_synthetic),
        "created_at": now,
    }
    input_sha256 = integrity.sha256_record(
        {"tool_id": collector.tool_id, "venue": venue, "limit": limit,
         "market_ids": sorted(market_ids) if market_ids is not None else None,
         "with_quotes": bool(with_quotes)}
    )
    record = {
        "tool_id": collector.tool_id,
        "tool_version": collector.tool_version,
        "tool_class": PREDMARKET_TOOL_CLASS,
        "operation": "collect_pred_markets",
        "venue": venue,
        "input_sha256": input_sha256,
        "market_count": len(markets),
        "quoted_count": len(quoted),
        "quotes_requested": bool(getattr(result, "quotes_requested", True)),
        "source": result.source,
        "is_synthetic": bool(result.is_synthetic),
        "output_sha256": integrity.sha256_record({"markets": markets}),
        "latency_ms": int(result.latency_ms),
        "read_only": True,
        "external_action": False,
        "network_egress": bool(getattr(collector, "network_egress", False)),
        "created_at": now,
    }
    return snapshot, record


def degraded_pred_market_record(
    collector: PredMarketCollector, venue: str, reason_code: str, *, now: str
) -> dict[str, Any]:
    """The record for a collection whose venue failed — recorded, never silent."""
    tool_id = getattr(collector, "tool_id", PREDMARKET_TOOL_ID)
    return {
        "tool_id": tool_id,
        "tool_version": getattr(collector, "tool_version", PREDMARKET_TOOL_VERSION),
        "tool_class": PREDMARKET_TOOL_CLASS,
        "operation": "collect_pred_markets",
        "venue": venue,
        "input_sha256": integrity.sha256_record({"tool_id": tool_id, "venue": venue}),
        "market_count": 0,
        "quoted_count": 0,
        "source": getattr(collector, "source", "unknown"),
        "is_synthetic": False,
        "output_sha256": integrity.sha256_record({"markets": []}),
        "latency_ms": 0,
        "read_only": True,
        "external_action": False,
        "network_egress": bool(getattr(collector, "network_egress", False)),
        "degraded": True,
        "degraded_reason_code": reason_code,
        "created_at": now,
    }


# --- the gate -------------------------------------------------------------------

def select_pred_market_collector(
    venue: str,
    *,
    now: str | None = None,
    root: Path | None = None,
    min_close_time: str | None = None,
) -> PredMarketCollector:
    """Choose one venue's collector — the enforced Safety-Flag Gate chokepoint.

    Defaults to the network-free mock (no gate needed; it reaches nothing). The real
    adapter is returned ONLY when the caller opts in *and* that venue's own grant
    authorizes ``network_access``. Both endpoints are public and keyless, so this gate is
    the entire boundary between a config typo and an outbound socket — and one grant per
    venue means authorizing Kalshi reads never authorizes Polymarket's.

    ``min_close_time`` (ISO-8601) asks the venues that support it to drop markets closing
    before that instant server-side. It is a discovery-time convenience, never a
    correctness boundary — ``screening.screen_market`` re-checks every row that comes back,
    so a venue ignoring the parameter cannot widen the gate. It is not applied when
    ``market_ids`` names specific markets: a confirmed leg is re-read whatever its horizon.
    """
    venue = require_venue(venue)
    if venue == KALSHI:
        return safety_gate.select_gated(
            env_var=KALSHI_ENV,
            opt_in_value=KALSHI_PROVIDER_ID,
            flags=_NETWORK_FLAGS,
            provider_id=KALSHI_PROVIDER_ID,
            default_factory=lambda: MockPredMarketCollector(KALSHI),
            gated_factory=lambda authorization: KalshiPublicCollector(
                authorization=authorization, min_close_time=min_close_time
            ),
            now=now,
            root=root,
        )
    if venue == POLYMARKET:
        return safety_gate.select_gated(
            env_var=POLYMARKET_ENV,
            opt_in_value=POLYMARKET_PROVIDER_ID,
            flags=_NETWORK_FLAGS,
            provider_id=POLYMARKET_PROVIDER_ID,
            default_factory=lambda: MockPredMarketCollector(POLYMARKET),
            gated_factory=lambda authorization: PolymarketPublicCollector(
                authorization=authorization, min_close_time=min_close_time
            ),
            now=now,
            root=root,
        )
    return safety_gate.select_gated(
        env_var=BINANCE_ENV,
        opt_in_value=BINANCE_PROVIDER_ID,
        flags=_NETWORK_FLAGS,
        provider_id=BINANCE_PROVIDER_ID,
        default_factory=lambda: MockPredMarketCollector(BINANCE),
        gated_factory=lambda authorization: BinancePredictionCollector(
            authorization=authorization, min_close_time=min_close_time
        ),
        now=now,
        root=root,
    )


def _get_json(url: str, *, timeout_seconds: int, headers: Mapping[str, str] | None = None) -> Any:
    """One public GET, parsed. Transport errors are deliberately generic — the URL never
    reaches a message, log or record (the R3 posture; here it also keeps venue query
    parameters out of the audit trail)."""
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            **(dict(headers or {})),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
            raw = response.read().decode("utf-8")
    except (TimeoutError, urllib.error.URLError):
        raise ToolError("TOOL_TRANSPORT", "prediction-market request failed or timed out") from None
    try:
        return json.loads(raw)
    except ValueError:
        raise ToolError("MALFORMED_RESULT", "prediction-market backend returned an unparseable response") from None


# --- Kalshi ---------------------------------------------------------------------

class KalshiPublicCollector:
    """Kalshi public REST markets (read-only, no API key).

    Endpoint and field names verified against Kalshi's API reference on 2026-07-26. The
    detail that decides whether this parses at all: **prices are decimal-dollar strings**
    (``yes_bid_dollars`` = ``"0.5600"``), not the integer cents an older version of this API
    served. A contract settles at $1, so the dollar figure *is* the probability.

    Sizes arrive as fixed-point strings (``yes_bid_size_fp``, e.g. ``"10.00"``) and are read
    as contracts.

    **Two endpoints, because they answer different questions** (measured 2026-07-27):

    - Discovery reads ``/events?with_nested_markets=true``. The flat ``/markets`` index is
      unusable for it — paginated eight pages deep it returned **8,000 markets, all 8,000 of
      them ``KXMVE…`` parlays** and 26 with a two-sided quote. Six pages of ``/events``
      returned 1,200 events holding **7,840 markets, zero parlays, 7,017 quoted**. This is
      not a filtering problem that a better predicate solves; it is the wrong index.
    - A watch scan still reads ``/markets?tickers=…``, which fetches exactly the confirmed
      legs in one call. Re-reading known tickers through an event listing would mean paging
      until they happened to appear.

    The nested market also carries a **full question** — ``"Will Klaus Iohannis be the next
    Secretary General of NATO?"`` — where the flat index offers only ``yes_sub_title``,
    ``"Klaus Iohannis"``. That fragment is what the matcher had been scoring against
    Polymarket's complete sentences, and it is the single biggest reason Kalshi legs did not
    match. The event's ``category`` arrives with it too, so ``matching`` can finally compare
    a field that was always ``None`` on this venue.
    """

    venue = KALSHI
    tool_id = PREDMARKET_TOOL_ID
    tool_version = f"{PREDMARKET_TOOL_VERSION}-kalshi"
    provider_id = KALSHI_PROVIDER_ID
    network_egress = True
    source = "kalshi_public"

    BASE = "https://api.elections.kalshi.com/trade-api/v2"
    PAGE_LIMIT = 1000  # the venue's documented maximum for /markets
    EVENT_PAGE_LIMIT = 200
    # One page of events already holds well over a thousand markets, so this is a runaway
    # guard rather than a budget: it bounds the call count if the cursor ever stops
    # advancing, and a short page is never mistaken for the end of the listing.
    MAX_EVENT_PAGES = 5

    def __init__(self, *, authorization: Authorization | None = None, min_close_time: str | None = None):
        self._authorization = authorization
        self._min_close_time = min_close_time

    def list_markets(
        self,
        *,
        limit: int,
        timeout_seconds: int,
        market_ids: Sequence[str] | None = None,
        with_quotes: bool = True,
    ) -> PredMarketSnapshot:
        # Chokepoint: re-verify at the moment of egress (defense in depth).
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )
        started = time.monotonic()
        if market_ids:
            markets = self._read_named(market_ids, limit=limit, timeout_seconds=timeout_seconds)
        else:
            markets = self._read_events(limit=limit, timeout_seconds=timeout_seconds)
        return PredMarketSnapshot(
            venue=self.venue,
            markets=markets,
            source=self.source,
            is_synthetic=False,
            collector_version=self.tool_version,
            latency_ms=int((time.monotonic() - started) * 1000),
            # False on a discovery read even though these markets ARE quoted: `/events`
            # returns prices inline, so Kalshi costs nothing extra for them and throwing
            # away data already paid for would be worse. The flag records what was asked
            # for, not what happened to arrive.
            quotes_requested=with_quotes,
        )

    def _read_named(
        self, market_ids: Sequence[str], *, limit: int, timeout_seconds: int
    ) -> list[PredMarket]:
        """The confirmed legs, in one call. ``tickers`` is a comma-separated allowlist and
        this endpoint returns prices inline, so a targeted read stays a single request — and
        no horizon is applied: a group with an hour left still owes the report its closing
        observations."""
        params: dict[str, Any] = {
            "limit": min(int(limit), self.PAGE_LIMIT),
            "status": "open",
            "tickers": ",".join(sorted(set(market_ids))),
        }
        payload = _get_json(
            f"{self.BASE}/markets?{urllib.parse.urlencode(params)}",
            timeout_seconds=timeout_seconds,
        )
        return parse_kalshi_markets(payload)[:limit]

    def _read_events(self, *, limit: int, timeout_seconds: int) -> list[PredMarket]:
        """Discovery, through the event index: sweep several pages, then keep the busiest.

        ``status=open`` and ``min_close_ts`` are both honoured here (checked against a
        horizon far enough out to empty the page, because a filter everything passes proves
        nothing). The horizon push is still only an optimisation — ``screening`` re-checks
        every market that comes back.

        **The sweep runs to its page budget even once ``limit`` markets are in hand**, which
        looks wasteful and is the whole point: you cannot rank a corpus you did not read.
        Kalshi honours no ordering parameter at all — ``order``, ``sort_by``, ``min_volume``
        and ``category`` were each measured returning byte-identical pages on 2026-07-27 —
        so the head of this venue's listing is reachable only by fetching a lot and sorting
        locally. Taking the first hundred instead cost nothing and produced nothing: those
        pages were 2045- and 2035-dated novelties while Polymarket's were 2028 primaries,
        and 30,000 judged pairings yielded zero candidates. Sorted by volume, the top of the
        same sweep is "Will Gavin Newsom be the Democratic Presidential nominee in 2028?" —
        which is a market Polymarket also lists.

        Volume is the venue's own lifetime figure and is used only to rank Kalshi against
        Kalshi. ``volume_24h_fp`` is the obvious alternative and would favour what is being
        traded right now rather than what has ever been big; it is a judgement call, and
        this one is recorded rather than hidden.
        """
        params: dict[str, Any] = {
            "limit": self.EVENT_PAGE_LIMIT,
            "status": "open",
            "with_nested_markets": "true",
        }
        horizon = _epoch_seconds(self._min_close_time) if self._min_close_time else None
        if horizon is not None:
            params["min_close_ts"] = horizon

        markets: list[PredMarket] = []
        cursor: str | None = None
        for _page in range(self.MAX_EVENT_PAGES):
            page_params = {**params, **({"cursor": cursor} if cursor else {})}
            payload = _get_json(
                f"{self.BASE}/events?{urllib.parse.urlencode(page_params)}",
                timeout_seconds=timeout_seconds,
            )
            markets.extend(parse_kalshi_events(payload))
            cursor = payload.get("cursor") if isinstance(payload, Mapping) else None
            if not cursor:
                break
        return rank_by_volume(markets)[:limit]


def rank_by_volume(markets: Sequence[PredMarket]) -> list[PredMarket]:
    """A venue's markets, busiest first. Stable, and never mixes venues.

    Discovery compares the *head* of each venue's listing, because a market that is heavily
    traded on one venue is the kind of market the other venue also lists. The tails are
    where two venues stop overlapping — Kalshi's 2045-dated novelties have no counterpart
    anywhere, and pairing them against Polymarket's 2028 primaries produced 30,000 judged
    pairings and zero candidates.

    A market whose volume the venue did not report sorts last rather than first: unknown is
    not "busy", and the alternative would let a venue's silence outrank its own numbers.
    Ties keep the venue's order, so a page with no volumes anywhere is returned untouched.
    """
    return sorted(markets, key=lambda m: -(m.volume if m.volume is not None else -1.0))


def parse_kalshi_events(payload: Any) -> list[PredMarket]:
    """Kalshi's ``/events?with_nested_markets=true`` payload as normalized markets. Pure.

    One event holds many markets — "Who will be the next Secretary General of NATO?" holds
    one per candidate — and each of those is an ordinary binary YES/NO market. So an event
    is flattened into its markets rather than becoming one; ``mutually_exclusive`` is not a
    reason to skip anything, and treating it as one would drop the whole election category
    on both venues.

    Two fields come from the event and matter more than the plumbing suggests: its
    ``category`` (the flat ``/markets`` index publishes none, so this comparison was dead on
    Kalshi) and its ``event_ticker`` as ``group_id``.
    """
    if not isinstance(payload, Mapping):
        raise ToolError("MALFORMED_RESULT", "kalshi events payload is not an object")
    events = payload.get("events")
    if not isinstance(events, list):
        raise ToolError("MALFORMED_RESULT", "kalshi events payload carries no events list")

    markets: list[PredMarket] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        category = event.get("category") if isinstance(event.get("category"), str) else None
        event_ticker = event.get("event_ticker") if isinstance(event.get("event_ticker"), str) else None
        for row in event.get("markets") or []:
            if not isinstance(row, Mapping):
                continue
            market = _kalshi_market(row, category=category, group_id=event_ticker)
            if market is not None:
                markets.append(market)
    return markets


def _kalshi_market(
    row: Mapping[str, Any], *, category: str | None, group_id: str | None
) -> PredMarket | None:
    """One Kalshi market row, from either endpoint. ``None`` when it has no identity.

    The title preference is the point of this helper. A nested market publishes ``title`` —
    *"Will Klaus Iohannis be the next Secretary General of NATO?"* — a complete question of
    the same shape Polymarket asks. ``yes_sub_title`` is *"Klaus Iohannis"*, a fragment that
    shares almost no vocabulary with the other venue's sentence and scored accordingly. The
    fragment stays as a fallback rather than a first choice.
    """
    ticker = row.get("ticker")
    if not isinstance(ticker, str) or not ticker:
        return None
    return PredMarket(
        venue=KALSHI,
        market_id=ticker,
        group_id=group_id or (row.get("event_ticker") if isinstance(row.get("event_ticker"), str) else None),
        title=str(row.get("title") or row.get("yes_sub_title") or row.get("subtitle") or ticker),
        category=category,
        close_time=row.get("close_time") if isinstance(row.get("close_time"), str) else None,
        status=row.get("status") if isinstance(row.get("status"), str) else None,
        derived_from=parse_kalshi_mve_legs(row),
        volume=_size(row.get("volume_fp")),
        quote=VenueQuote(
            yes_bid=_probability(row.get("yes_bid_dollars")),
            yes_ask=_probability(row.get("yes_ask_dollars")),
            yes_bid_size=_size(row.get("yes_bid_size_fp")),
            yes_ask_size=_size(row.get("yes_ask_size_fp")),
        ),
    )


def parse_kalshi_markets(payload: Any) -> list[PredMarket]:
    """Kalshi's ``/markets`` payload as normalized markets. Pure — no network.

    Separate from the read so the venue's semantics can be tested exhaustively against
    recorded payloads with no socket and no grant. A row missing an identity (``ticker``) is
    skipped rather than carried as an anonymous market; a row missing prices is carried
    **unquoted**, because knowing the market exists is itself useful to pair matching.

    Shares ``_kalshi_market`` with the event listing, so a market re-read as a confirmed leg
    is the same object it was when it was proposed. It carries no ``category`` — that lives
    on the event, and inventing one from this payload is exactly the "unknown as a value"
    mistake the matcher is built to avoid.
    """
    if not isinstance(payload, Mapping):
        raise ToolError("MALFORMED_RESULT", "kalshi markets payload is not an object")
    rows = payload.get("markets")
    if not isinstance(rows, list):
        raise ToolError("MALFORMED_RESULT", "kalshi markets payload carries no markets list")

    markets: list[PredMarket] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        market = _kalshi_market(row, category=None, group_id=None)
        if market is not None:
            markets.append(market)
    return markets


def parse_kalshi_mve_legs(row: Mapping[str, Any]) -> tuple[str, ...] | None:
    """The constituent market tickers of a Kalshi multivariate parlay, or ``None``. Pure.

    ``mve_selected_legs`` is Kalshi naming its own product: a market that is a basket of
    other markets ("yes Atlas, yes Cruz Azul, yes Leon, …", nine legs, quoted 0.00/0.00).
    Those baskets dominate the open listing and no other venue lists them, so they can never
    be a leg of a cross-venue group — see ``screening.DERIVED_COMBINATION``.

    Falls back to ``mve_collection_ticker`` when the leg array is absent but the collection
    is named, because the claim being made is the same one and a market that says it belongs
    to a parlay collection has already told us what it is.
    """
    legs = row.get("mve_selected_legs")
    if isinstance(legs, list):
        tickers = tuple(
            str(leg["market_ticker"]) for leg in legs
            if isinstance(leg, Mapping) and isinstance(leg.get("market_ticker"), str)
            and leg["market_ticker"]
        )
        if tickers:
            return tickers
    collection = row.get("mve_collection_ticker")
    if isinstance(collection, str) and collection.strip():
        return (collection.strip(),)
    return None


# --- Polymarket -----------------------------------------------------------------

class PolymarketPublicCollector:
    """Polymarket Gamma (markets) + CLOB (order books), read-only, no key.

    Two calls per market by design. Gamma says which markets exist and carries the
    ``clobTokenIds``; its ``outcomePrices`` are a derived/last figure, **not** a quote — so
    the best bid/ask comes from the CLOB book for the YES token. A market whose book was not
    read (past ``book_limit``, or a failed book call) is returned unquoted rather than priced
    from the Gamma figure, because a fee-adjusted comparison against a non-executable price
    is exactly how a paper edge becomes an imaginary one.

    Verified against Polymarket's API reference on 2026-07-26: Gamma at
    ``gamma-api.polymarket.com/markets``, books at ``clob.polymarket.com/book?token_id=``
    returning ``bids``/``asks`` arrays of ``{price, size}`` strings in ``[0, 1]``.
    """

    venue = POLYMARKET
    tool_id = PREDMARKET_TOOL_ID
    tool_version = f"{PREDMARKET_TOOL_VERSION}-polymarket"
    provider_id = POLYMARKET_PROVIDER_ID
    network_egress = True
    source = "polymarket_public"

    GAMMA_BASE = "https://gamma-api.polymarket.com"
    CLOB_BASE = "https://clob.polymarket.com"

    def __init__(
        self,
        *,
        authorization: Authorization | None = None,
        book_limit: int = DEFAULT_BOOK_LIMIT,
        min_close_time: str | None = None,
    ):
        self._authorization = authorization
        self._book_limit = _clamp(book_limit, DEFAULT_BOOK_LIMIT, MAX_BOOK_LIMIT)
        self._min_close_time = min_close_time

    def list_markets(
        self,
        *,
        limit: int,
        timeout_seconds: int,
        market_ids: Sequence[str] | None = None,
        with_quotes: bool = True,
    ) -> PredMarketSnapshot:
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )
        started = time.monotonic()
        params = {"limit": int(limit), "active": "true", "closed": "false"}
        if market_ids is None:
            # Discovery only, as on Kalshi. Gamma, unlike Kalshi, honours an ordering
            # parameter, so its head is one call away instead of a five-page sweep.
            params["order"] = "volumeNum"
            params["ascending"] = "false"
            if self._min_close_time:
                # It earns its keep here: Gamma serves markets with `active: true,
                # closed: false` whose end date passed months ago (observed 2026-07-27), so
                # without this the page is partly already-expired markets.
                params["end_date_min"] = self._min_close_time
        payload = _get_json(
            f"{self.GAMMA_BASE}/markets?{urllib.parse.urlencode(params)}",
            timeout_seconds=timeout_seconds,
        )
        markets = parse_gamma_markets(payload)[:limit]
        if market_ids is not None:
            # A watch scan wants the confirmed legs, not the front of the listing. Filtering
            # BEFORE the per-market book calls is the whole saving: one Gamma call plus one
            # book call per wanted market, instead of `book_limit` books of whatever happened
            # to be listed first.
            wanted = set(market_ids)
            markets = [m for m in markets if m.market_id in wanted]

        if not with_quotes:
            # Discovery reads listings only. The matcher compares titles, close times,
            # categories and cross-references — it never looks at a price — so a book call
            # here buys a number nobody reads, one HTTP round trip at a time.
            return PredMarketSnapshot(
                venue=self.venue, markets=markets, source=self.source, is_synthetic=False,
                collector_version=self.tool_version,
                latency_ms=int((time.monotonic() - started) * 1000),
                quotes_requested=False,
            )

        priced: list[PredMarket] = []
        for index, market in enumerate(markets):
            if index >= self._book_limit:
                priced.append(market)  # beyond the call budget: unquoted, not guessed
                continue
            try:
                book = _get_json(
                    f"{self.CLOB_BASE}/book?{urllib.parse.urlencode({'token_id': market.market_id})}",
                    timeout_seconds=timeout_seconds,
                )
            except ToolError:
                # One market's book failing is not the scan failing. It stays unquoted.
                priced.append(market)
                continue
            priced.append(PredMarket(
                venue=market.venue, market_id=market.market_id, group_id=market.group_id,
                title=market.title, close_time=market.close_time, status=market.status,
                category=market.category, quote=parse_clob_book(book),
            ))

        return PredMarketSnapshot(
            venue=self.venue,
            markets=priced,
            source=self.source,
            is_synthetic=False,
            collector_version=self.tool_version,
            latency_ms=int((time.monotonic() - started) * 1000),
            quotes_requested=with_quotes,
        )


def _maybe_json_list(value: Any) -> list[Any]:
    """Gamma ships some array fields as *stringified* JSON. Accept both shapes.

    Documented as strings by the community clients and observed as plain arrays elsewhere;
    rather than guess which is current, both are read and anything else yields an empty
    list — which downstream reads as "this market has no usable token id", i.e. skip it.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def parse_gamma_markets(payload: Any) -> list[PredMarket]:
    """Gamma's ``/markets`` payload as normalized, **unquoted** markets. Pure.

    Unquoted on purpose: Gamma's ``outcomePrices`` are not a book. The market id is the
    **YES CLOB token id**, because that is what the order book and any later order key on.
    A market whose token ids cannot be read is skipped — without them there is nothing to
    price and nothing to trade.
    """
    rows = payload if isinstance(payload, list) else None
    if rows is None and isinstance(payload, Mapping):
        rows = payload.get("data") if isinstance(payload.get("data"), list) else None
    if rows is None:
        raise ToolError("MALFORMED_RESULT", "polymarket markets payload is not a list")

    markets: list[PredMarket] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        token_ids = _maybe_json_list(row.get("clobTokenIds"))
        if not token_ids:
            continue
        yes_token = token_ids[0]
        if not isinstance(yes_token, str) or not yes_token:
            continue
        markets.append(PredMarket(
            venue=POLYMARKET,
            market_id=yes_token,
            group_id=row.get("conditionId") if isinstance(row.get("conditionId"), str) else None,
            title=str(row.get("question") or row.get("slug") or yes_token),
            category=row.get("category") if isinstance(row.get("category"), str) else None,
            close_time=row.get("endDate") if isinstance(row.get("endDate"), str) else None,
            status="active" if row.get("active") and not row.get("closed") else "inactive",
            accepting_orders=_gamma_accepting_orders(row),
            volume=as_optional_float(row.get("volumeNum")),
        ))
    return markets


def _gamma_accepting_orders(row: Mapping[str, Any]) -> bool | None:
    """Will Polymarket currently take an order on this market? ``None`` when it did not say.

    Two independent flags — ``enableOrderBook`` (there is a book at all) and
    ``acceptingOrders`` (it is open right now) — and a market needs both. Either one being
    absent is silence, not consent: ``None`` propagates and ``screening`` does not exclude on
    it, because the book itself answers the same question at the next scan.

    This matters more than it looks: Gamma serves markets with ``active: true, closed: false``
    whose end date passed months ago (observed 2026-07-27), so its status fields alone do not
    establish that anything is tradable.
    """
    stated = [row[key] for key in ("enableOrderBook", "acceptingOrders")
              if isinstance(row.get(key), bool)]
    if not stated:
        return None
    return all(stated)


def parse_clob_book(payload: Any) -> VenueQuote:
    """A CLOB order book as the best YES bid/ask. Pure.

    The venue documents bids descending and asks ascending, but this takes the max bid and
    the min ask rather than trusting the order: a mis-sorted page would otherwise quote the
    worst price on the book as the best, which reads as a huge spread — or, on the other
    side, as free money.
    """
    if not isinstance(payload, Mapping):
        return VenueQuote()
    best_bid = best_bid_size = None
    best_ask = best_ask_size = None
    for level in payload.get("bids") or []:
        if not isinstance(level, Mapping):
            continue
        price = _probability(level.get("price"))
        if price is not None and (best_bid is None or price > best_bid):
            best_bid, best_bid_size = price, _size(level.get("size"))
    for level in payload.get("asks") or []:
        if not isinstance(level, Mapping):
            continue
        price = _probability(level.get("price"))
        if price is not None and (best_ask is None or price < best_ask):
            best_ask, best_ask_size = price, _size(level.get("size"))
    return VenueQuote(
        yes_bid=best_bid, yes_ask=best_ask,
        yes_bid_size=best_bid_size, yes_ask_size=best_ask_size,
    )


# --- Binance prediction markets -------------------------------------------------

class BinancePredictionCollector:
    """Prediction markets over Binance's Prediction Trading REST API.

    Chosen over Predict.fun's own REST API (Thomas, 2026-07-26) because the money path
    decides it: funding a Binance prediction account is a transfer from an existing balance,
    while the direct venue needs a self-custodied BNB-chain wallet. Reading through the same
    door that will later trade keeps one credential and one identifier space.

    **The underlying markets are Predict.fun's on BNB Smart Chain**, which matters at exactly
    one moment — when the operator compares resolution rules before confirming a pairing. The
    payload states it on every row (``vendor``, ``chainId``), so the fact travels with the data
    and does not need to live in the venue's name.

    Verified against the published spec on 2026-07-26, and it changed two earlier refusals:

    - **there is a real order book** (``/order-book`` returns ``bids``/``asks`` of
      ``{price, size}``), so this venue can be *quoted*. The direct Predict.fun API published
      none, which is why the first version of this collector listed markets and priced none.
    - **the venue reports its own fee**, ``feeRateBps`` per topic, so its legs no longer have
      to be treated as having no knowable cost.

    **Everything is signed.** Every prediction endpoint takes a ``timestamp`` and an HMAC
    signature; there is no public read here, so the key is not an optimisation but the
    price of entry. The signing follows ``crypto/account.py`` exactly, including the rule
    that a transport failure is reported generically because the signature is in the URL.

    Three calls deep, and bounded: ``market/list`` gives topics, ``market/detail`` gives each
    topic's outcome tokens, ``order-book`` gives one token's book. Markets past the budget
    come back **unquoted** rather than dropped or guessed.
    """

    venue = BINANCE
    tool_id = PREDMARKET_TOOL_ID
    tool_version = f"{PREDMARKET_TOOL_VERSION}-binance-prediction"
    provider_id = BINANCE_PROVIDER_ID
    network_egress = True
    source = "binance_prediction"

    BASE = "https://api.binance.com"
    ALLOWED_HOSTS = frozenset({"api.binance.com"})
    LIST_PATH = "/sapi/v1/w3w/wallet/prediction/market/list"
    DETAIL_PATH = "/sapi/v1/w3w/wallet/prediction/market/detail"
    BOOK_PATH = "/sapi/v1/w3w/wallet/prediction/order-book"
    PAGE_LIMIT = 100          # the venue's documented max for market/list
    RECV_WINDOW_MS = 5000
    VENDOR = "predict_fun"    # the order-book call names the vendor explicitly

    def __init__(
        self,
        *,
        authorization: Authorization | None = None,
        book_limit: int = DEFAULT_BOOK_LIMIT,
        base_url: str = BASE,
        min_close_time: str | None = None,
    ):
        host = (urllib.parse.urlparse(base_url).hostname or "").lower()
        if host not in self.ALLOWED_HOSTS:
            # Refuse at construction: a signed key must never be pointed at an unexpected
            # host, and a URL typo should fail loudly rather than leak a credential.
            raise ToolBlocked("HOST_NOT_ALLOWED", "prediction base URL is not an allowed host")
        self._base_url = base_url.rstrip("/")
        self._authorization = authorization
        self._book_limit = _clamp(book_limit, DEFAULT_BOOK_LIMIT, MAX_BOOK_LIMIT)
        self._min_close_time = min_close_time

    def credentials_present(self) -> bool:
        return bool(
            os.environ.get(BINANCE_API_KEY_ENV, "").strip()
            and os.environ.get(BINANCE_API_SECRET_ENV, "").strip()
        )

    def _signed_get(self, path: str, params: Mapping[str, Any], *, timeout_seconds: int) -> Any:
        api_key = os.environ.get(BINANCE_API_KEY_ENV, "").strip()
        api_secret = os.environ.get(BINANCE_API_SECRET_ENV, "").strip()
        if not api_key or not api_secret:
            # Names only — the absence of a credential is reportable, its value never is.
            # A distinct code from a degrade: an unfinished setup step is not an outage.
            raise ToolError(
                API_KEY_MISSING,
                f"prediction credentials are not configured "
                f"({BINANCE_API_KEY_ENV}/{BINANCE_API_SECRET_ENV})",
            )
        query = dict(params)
        query.setdefault("recvWindow", self.RECV_WINDOW_MS)
        query["timestamp"] = int(time.time() * 1000)
        encoded = urllib.parse.urlencode(query)
        signature = hmac.new(
            api_secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        request = urllib.request.Request(
            f"{self._base_url}{path}?{encoded}&signature={signature}",
            method="GET",
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
                "X-MBX-APIKEY": api_key,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except (TimeoutError, urllib.error.URLError):
            # Generic on purpose: the URL carries the signature.
            raise ToolError("TOOL_TRANSPORT", "prediction request failed or timed out") from None
        try:
            return json.loads(raw)
        except ValueError:
            raise ToolError("MALFORMED_RESULT", "prediction backend returned an unparseable response") from None

    def _quote_known(
        self, market_ids: Sequence[str], *, timeout_seconds: int
    ) -> PredMarketSnapshot:
        """Quote markets we already know the ids of — **one order-book call each**.

        The discovery walk (list -> detail -> book) exists to *find* markets. Re-running it
        every two minutes to re-read markets an operator already confirmed would spend
        ``1 + 2N`` calls to learn nothing new, and each of these endpoints carries an IP
        weight of 200. A confirmed leg carries ``marketId:tokenId``, which is exactly what the
        book needs, so a watch scan costs one call per leg.

        Identity fields (title, close time, category, fee rate) are not re-fetched: the group
        that named this market is the authority on what it is, and the scan only needs the
        price. The fee rate therefore arrives as ``None`` on this path and the leg is priced
        at the pessimistic default — see the fee module.
        """
        started = time.monotonic()
        markets: list[PredMarket] = []
        for raw in market_ids:
            split = split_binance_market_id(raw)
            if split is None:
                # Not a composite id: nothing to ask the venue. Recorded unquoted rather than
                # guessed, which is what every other unreadable leg does.
                markets.append(PredMarket(
                    venue=BINANCE, market_id=str(raw), group_id=None, title="",
                    close_time=None, status=None,
                ))
                continue
            market_id, token_id = split
            try:
                book = self._signed_get(
                    self.BOOK_PATH,
                    {"vendor": self.VENDOR, "marketId": market_id, "tokenId": token_id},
                    timeout_seconds=timeout_seconds,
                )
            except ToolError:
                book = None
            markets.append(PredMarket(
                venue=BINANCE, market_id=str(raw), group_id=None, title="",
                close_time=None, status=None,
                quote=parse_prediction_book(book) if book is not None else VenueQuote(),
            ))
        return PredMarketSnapshot(
            venue=self.venue, markets=markets, source=self.source, is_synthetic=False,
            collector_version=self.tool_version,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    def list_markets(
        self,
        *,
        limit: int,
        timeout_seconds: int,
        market_ids: Sequence[str] | None = None,
        with_quotes: bool = True,
    ) -> PredMarketSnapshot:
        # Chokepoint: re-verify at the moment of egress (defense in depth).
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )
        if market_ids is not None:
            return self._quote_known(market_ids, timeout_seconds=timeout_seconds)
        started = time.monotonic()
        listing = self._signed_get(
            self.LIST_PATH,
            {"limit": min(int(limit), self.PAGE_LIMIT), "offset": 0},
            timeout_seconds=timeout_seconds,
        )
        topics = parse_prediction_topics(listing)[:limit]
        if self._min_close_time:
            # Before the detail/book calls, not after. This venue has no server-side horizon
            # parameter, and its listing is dominated by five-minute crypto markets ("Bitcoin
            # Up or Down — 2:30AM-2:35AM ET"): without this the call budget is spent two calls
            # at a time on markets that close before an operator could read the proposal.
            # The topic already carries `close_time`, so the skip costs nothing to decide.
            topics = [t for t in topics if _reaches_horizon(t.close_time, self._min_close_time)]
        # Busiest first, and here it decides more than the order of a list: only the first
        # `book_limit` topics get a detail and a book call, so this is what those scarce
        # signed calls are spent on. Untouched when specific ids were named — that path
        # returns above, before any of this.
        topics = rank_by_volume(topics)

        markets: list[PredMarket] = []
        for index, topic in enumerate(topics):
            if index >= self._book_limit:
                markets.append(topic)   # past the call budget: unquoted, never guessed
                continue
            try:
                detail = self._signed_get(
                    self.DETAIL_PATH,
                    {"marketTopicId": topic.market_id},
                    timeout_seconds=timeout_seconds,
                )
                outcome = parse_prediction_yes_outcome(detail)
            except ToolError:
                markets.append(topic)   # one topic's detail failing is not the scan failing
                continue
            if outcome is None:
                markets.append(topic)
                continue
            market_id, token_id, condition_id = outcome
            book = None
            if with_quotes:
                # `detail` above stays: it carries the conditionId that becomes `group_id`,
                # and a venue-asserted cross-reference is the strongest matching evidence
                # there is. Only the BOOK is price-only, so only the book is skipped.
                try:
                    book = self._signed_get(
                        self.BOOK_PATH,
                        {"vendor": self.VENDOR, "marketId": market_id, "tokenId": token_id},
                        timeout_seconds=timeout_seconds,
                    )
                except ToolError:
                    book = None
            markets.append(PredMarket(
                venue=BINANCE,
                # `marketId:tokenId`, because the order book needs BOTH and only the token is
                # not enough to ask for a quote. Composite so a confirmed leg can be re-read
                # later in ONE call instead of walking list -> detail -> book again: a watch
                # scan every two minutes cannot afford to rediscover what it already knows.
                market_id=f"{market_id}:{token_id}",
                # The cross-reference axis. Predict.fun's conditionId is the same shape as
                # Polymarket's, so a group can be matched on it rather than on wording.
                group_id=condition_id or topic.group_id,
                title=topic.title,
                category=topic.category,
                close_time=topic.close_time,
                status=topic.status,
                fee_rate_bps=topic.fee_rate_bps,
                volume=topic.volume,
                quote=parse_prediction_book(book) if book is not None else VenueQuote(),
            ))

        return PredMarketSnapshot(
            venue=self.venue,
            markets=markets,
            source=self.source,
            is_synthetic=False,
            collector_version=self.tool_version,
            latency_ms=int((time.monotonic() - started) * 1000),
            quotes_requested=with_quotes,
        )


def is_re_readable(market: PredMarket) -> bool:
    """Can a later scan ask this venue for THIS market again?

    A candidate's id is a promise: an operator confirms a group, and every scan after that
    re-reads its legs by id. A market whose id the venue cannot be asked about again is
    proposable and unobservable — it spends the scarcest resource in the whole pipeline (a
    human comparing resolution rules) and then produces a permanent non-reading.

    Found live 2026-07-27: **72 of 92** Binance markets carried a topic id rather than the
    ``marketId:tokenId`` its order book needs, because ``market/detail`` is only called for
    the first ``book_limit`` topics. All 72 passed screening.

    Per-venue because only this module knows what each venue's ids mean. Kalshi tickers and
    Polymarket CLOB token ids are re-readable as they stand; Binance needs the composite.
    """
    market_id = market.market_id
    if not isinstance(market_id, str) or not market_id.strip():
        return False
    if market.venue == BINANCE:
        return split_binance_market_id(market_id) is not None
    return True


def split_binance_market_id(market_id: Any) -> tuple[int, str] | None:
    """``"5567895:112233"`` as ``(marketId, tokenId)``, or ``None``.

    The order book needs both. A leg confirmed before this format existed — or hand-edited —
    yields ``None``, and the caller records an unquoted market rather than guessing an id.
    """
    if not isinstance(market_id, str) or ":" not in market_id:
        return None
    left, _, right = market_id.partition(":")
    try:
        return int(left), right.strip()
    except ValueError:
        return None


def _reaches_horizon(close_time: Any, min_close_time: Any) -> bool:
    """Is this market still open at ``min_close_time``? Unreadable times answer ``True``.

    Permissive on purpose, and it is the one place in this package that is. Everywhere else
    an unknown fails closed; here the caller's own screen is about to re-check the same
    market against the same horizon and record a reason. Refusing here instead would drop
    the market before it could be counted, which is how a filter stops being able to report
    what it removed.
    """
    left, right = _epoch_seconds(close_time), _epoch_seconds(min_close_time)
    if left is None or right is None:
        return True
    return left >= right


def _epoch_seconds(value: Any) -> int | None:
    """An ISO-8601 instant as Unix seconds, or ``None`` when it will not parse.

    The other direction from ``_ms_to_iso``, and here for the same reason: this package
    speaks ISO throughout, and Kalshi's ``min_close_ts`` wants an epoch. ``None`` means the
    server-side filter is simply not sent — the client-side screen is the authority, so an
    unreadable horizon costs a bigger payload and nothing else.
    """
    try:
        return int(timeutil.parse_iso(str(value)).timestamp())
    except (TypeError, ValueError):
        return None


def _ms_to_iso(value: Any) -> str | None:
    """A venue epoch-millisecond stamp as UTC ISO-8601, or ``None``. The rest of this package
    compares close times as ISO strings, so the conversion belongs at the boundary."""
    parsed = as_optional_float(value)
    if parsed is None or parsed <= 0:
        return None
    return timeutil.format_iso(datetime.fromtimestamp(parsed / 1000.0, tz=timezone.utc))


def parse_prediction_topics(payload: Any) -> list[PredMarket]:
    """``market/list`` as normalized, **unquoted** topics. Pure — no network.

    A topic is the event; its tradable outcomes come from ``market/detail``. Carried unquoted
    because the list response's ``markets`` array is empty by design, so a price here would
    have to be invented.

    ``market_id`` is temporarily the **topic id** — the detail call needs it — and is replaced
    by the outcome token id once the detail is read. A topic that never gets that far is
    returned as-is, which is honest: it is a market we know exists and could not price.
    """
    if not isinstance(payload, Mapping):
        raise ToolError("MALFORMED_RESULT", "prediction markets payload is not an object")
    rows = payload.get("marketTopics")
    if not isinstance(rows, list):
        raise ToolError("MALFORMED_RESULT", "prediction markets payload carries no marketTopics")

    topics: list[PredMarket] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        topic_id = row.get("marketTopicId")
        if topic_id is None or str(topic_id).strip() == "":
            continue
        fee_bps = row.get("feeRateBps")
        topics.append(PredMarket(
            venue=BINANCE,
            market_id=str(topic_id),
            group_id=None,
            title=str(row.get("question") or row.get("title") or topic_id),
            category=row.get("chartType") if isinstance(row.get("chartType"), str) else None,
            close_time=_ms_to_iso(row.get("endDate")),
            # tradingStatus lives on the individual markets; the topic's own status is the
            # coarser one, and a topic that is not REGISTERED cannot be traded either way.
            status=row.get("status") if isinstance(row.get("status"), str) else None,
            fee_rate_bps=int(fee_bps) if isinstance(fee_bps, (int, float)) and not isinstance(fee_bps, bool) else None,
            volume=as_optional_float(row.get("tradeVolume")),
        ))
    return topics


def parse_prediction_yes_outcome(payload: Any) -> tuple[int, str, str | None] | None:
    """``market/detail`` → ``(marketId, YES tokenId, conditionId)``, or ``None``. Pure.

    Takes the first market whose ``tradingStatus`` is OPEN and reads its YES outcome. A topic
    with no open market, or an outcome with no token id, yields ``None`` — there is nothing
    to price and nothing a later order could key on.
    """
    if not isinstance(payload, Mapping):
        return None
    for market in payload.get("markets") or []:
        if not isinstance(market, Mapping):
            continue
        if str(market.get("tradingStatus") or "").upper() != "OPEN":
            continue
        market_id = market.get("marketId")
        if not isinstance(market_id, int):
            continue
        for outcome in market.get("outcomes") or []:
            if not isinstance(outcome, Mapping):
                continue
            if str(outcome.get("name") or "").upper() != "YES":
                continue
            token_id = outcome.get("tokenId")
            if not (isinstance(token_id, str) and token_id.strip()):
                continue
            condition = market.get("conditionId")
            return (
                market_id,
                token_id.strip(),
                condition.strip() if isinstance(condition, str) and condition.strip() else None,
            )
    return None


def parse_prediction_book(payload: Any) -> VenueQuote:
    """``/order-book`` as the best YES bid/ask. Pure.

    Same shape as Polymarket's book (``{price, size}`` strings in ``[0,1]``) and the same
    caution: take the max bid and the min ask rather than trusting page order, so a
    mis-sorted page cannot quote the worst price on the book as the best.
    """
    if not isinstance(payload, Mapping):
        return VenueQuote()
    best_bid = best_bid_size = None
    best_ask = best_ask_size = None
    for level in payload.get("bids") or []:
        if not isinstance(level, Mapping):
            continue
        price = _probability(level.get("price"))
        if price is not None and (best_bid is None or price > best_bid):
            best_bid, best_bid_size = price, _size(level.get("size"))
    for level in payload.get("asks") or []:
        if not isinstance(level, Mapping):
            continue
        price = _probability(level.get("price"))
        if price is not None and (best_ask is None or price < best_ask):
            best_ask, best_ask_size = price, _size(level.get("size"))
    return VenueQuote(
        yes_bid=best_bid, yes_ask=best_ask,
        yes_bid_size=best_bid_size, yes_ask_size=best_ask_size,
    )


__all__ = [
    "API_KEY_MISSING",
    "USER_AGENT",
    "DEFAULT_BOOK_LIMIT",
    "DEFAULT_MARKET_LIMIT",
    "KALSHI",
    "KALSHI_ENV",
    "KALSHI_PROVIDER_ID",
    "MAX_BOOK_LIMIT",
    "MAX_MARKET_LIMIT",
    "POLYMARKET",
    "POLYMARKET_ENV",
    "POLYMARKET_PROVIDER_ID",
    "BINANCE",
    "BINANCE_API_KEY_ENV",
    "BINANCE_API_SECRET_ENV",
    "BINANCE_ENV",
    "BINANCE_PROVIDER_ID",
    "PREDMARKET_DEGRADED",
    "PREDMARKET_TOOL_ID",
    "PREDMARKET_TOOL_VERSION",
    "VENUES",
    "KalshiPublicCollector",
    "MockPredMarketCollector",
    "PolymarketPublicCollector",
    "BinancePredictionCollector",
    "PredMarket",
    "PredMarketCollector",
    "PredMarketSnapshot",
    "VenueQuote",
    "collect_pred_markets",
    "is_re_readable",
    "degraded_pred_market_record",
    "parse_clob_book",
    "parse_gamma_markets",
    "parse_kalshi_markets",
    "parse_prediction_book",
    "parse_prediction_topics",
    "parse_prediction_yes_outcome",
    "require_venue",
    "split_binance_market_id",
    "select_pred_market_collector",
]
