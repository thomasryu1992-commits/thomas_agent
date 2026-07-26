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
from typing import Any, Mapping, Protocol

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
# Named for what it is, not for its storefront. Binance Wallet surfaces these markets, but the
# venue is Predict.fun on BNB Smart Chain: the counterparty, the settlement and — the part that
# decides pairing — the **resolution rules** are Predict.fun's. Calling it "binance" would put
# the wrong name in front of the operator at exactly the moment they compare those rules.
PREDICTFUN = "predictfun"
VENUES = (KALSHI, POLYMARKET, PREDICTFUN)

# One grant per venue, per the roadmap: they are independent capabilities, not a failover
# chain, so revoking one must not touch the others.
KALSHI_PROVIDER_ID = "kalshi_market_data"
POLYMARKET_PROVIDER_ID = "polymarket_market_data"
PREDICTFUN_PROVIDER_ID = "binance_prediction"
KALSHI_ENV = "MVP_KALSHI_MARKET_DATA"
POLYMARKET_ENV = "MVP_POLYMARKET_MARKET_DATA"
PREDICTFUN_ENV = "MVP_BINANCE_PREDICTION"
# The one venue of the three that is NOT keyless. Every prediction endpoint is SIGNED, so a
# key and a secret are both needed. Metadata only ever: the names are reported, the values are
# never logged, audited or recorded (the standing secrets rule), and because the signature
# rides in the query string a transport failure is reported generically.
PREDICTFUN_API_KEY_ENV = "BINANCE_PREDICTION_API_KEY"
PREDICTFUN_API_SECRET_ENV = "BINANCE_PREDICTION_API_SECRET"

_NETWORK_FLAGS = (NETWORK_ACCESS,)

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


class PredMarketCollector(Protocol):
    venue: str
    tool_id: str
    tool_version: str

    def list_markets(self, *, limit: int, timeout_seconds: int) -> PredMarketSnapshot: ...


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

    def list_markets(self, *, limit: int, timeout_seconds: int) -> PredMarketSnapshot:
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
        return PredMarketSnapshot(
            venue=self.venue,
            markets=markets,
            source=self.source,
            is_synthetic=True,
            collector_version=self.tool_version,
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
        result = collector.list_markets(limit=limit, timeout_seconds=timeout_seconds)
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
        "source": result.source,
        "is_synthetic": bool(result.is_synthetic),
        "created_at": now,
    }
    input_sha256 = integrity.sha256_record(
        {"tool_id": collector.tool_id, "venue": venue, "limit": limit}
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
    venue: str, *, now: str | None = None, root: Path | None = None
) -> PredMarketCollector:
    """Choose one venue's collector — the enforced Safety-Flag Gate chokepoint.

    Defaults to the network-free mock (no gate needed; it reaches nothing). The real
    adapter is returned ONLY when the caller opts in *and* that venue's own grant
    authorizes ``network_access``. Both endpoints are public and keyless, so this gate is
    the entire boundary between a config typo and an outbound socket — and one grant per
    venue means authorizing Kalshi reads never authorizes Polymarket's.
    """
    venue = require_venue(venue)
    if venue == KALSHI:
        return safety_gate.select_gated(
            env_var=KALSHI_ENV,
            opt_in_value=KALSHI_PROVIDER_ID,
            flags=_NETWORK_FLAGS,
            provider_id=KALSHI_PROVIDER_ID,
            default_factory=lambda: MockPredMarketCollector(KALSHI),
            gated_factory=lambda authorization: KalshiPublicCollector(authorization=authorization),
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
            gated_factory=lambda authorization: PolymarketPublicCollector(authorization=authorization),
            now=now,
            root=root,
        )
    return safety_gate.select_gated(
        env_var=PREDICTFUN_ENV,
        opt_in_value=PREDICTFUN_PROVIDER_ID,
        flags=_NETWORK_FLAGS,
        provider_id=PREDICTFUN_PROVIDER_ID,
        default_factory=lambda: MockPredMarketCollector(PREDICTFUN),
        gated_factory=lambda authorization: PredictFunCollector(authorization=authorization),
        now=now,
        root=root,
    )


def _get_json(url: str, *, timeout_seconds: int, headers: Mapping[str, str] | None = None) -> Any:
    """One public GET, parsed. Transport errors are deliberately generic — the URL never
    reaches a message, log or record (the R3 posture; here it also keeps venue query
    parameters out of the audit trail)."""
    request = urllib.request.Request(
        url, method="GET", headers={"Accept": "application/json", **(dict(headers or {}))}
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
    """

    venue = KALSHI
    tool_id = PREDMARKET_TOOL_ID
    tool_version = f"{PREDMARKET_TOOL_VERSION}-kalshi"
    provider_id = KALSHI_PROVIDER_ID
    network_egress = True
    source = "kalshi_public"

    BASE = "https://api.elections.kalshi.com/trade-api/v2"
    PAGE_LIMIT = 1000  # the venue's documented maximum for /markets

    def __init__(self, *, authorization: Authorization | None = None):
        self._authorization = authorization

    def list_markets(self, *, limit: int, timeout_seconds: int) -> PredMarketSnapshot:
        # Chokepoint: re-verify at the moment of egress (defense in depth).
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )
        started = time.monotonic()
        params = {"limit": min(int(limit), self.PAGE_LIMIT), "status": "open"}
        payload = _get_json(
            f"{self.BASE}/markets?{urllib.parse.urlencode(params)}",
            timeout_seconds=timeout_seconds,
        )
        markets = parse_kalshi_markets(payload)[:limit]
        return PredMarketSnapshot(
            venue=self.venue,
            markets=markets,
            source=self.source,
            is_synthetic=False,
            collector_version=self.tool_version,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


def parse_kalshi_markets(payload: Any) -> list[PredMarket]:
    """Kalshi's ``/markets`` payload as normalized markets. Pure — no network.

    Separate from the read so the venue's semantics can be tested exhaustively against
    recorded payloads with no socket and no grant. A row missing an identity (``ticker``) is
    skipped rather than carried as an anonymous market; a row missing prices is carried
    **unquoted**, because knowing the market exists is itself useful to pair matching.
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
        ticker = row.get("ticker")
        if not isinstance(ticker, str) or not ticker:
            continue
        markets.append(PredMarket(
            venue=KALSHI,
            market_id=ticker,
            group_id=row.get("event_ticker") if isinstance(row.get("event_ticker"), str) else None,
            # A Kalshi market object carries no free-text question; the subtitle is what
            # distinguishes markets inside one event. Pair matching (PM1's next increment)
            # needs the event title too, which lives on /events — noted rather than faked.
            title=str(row.get("yes_sub_title") or row.get("subtitle") or ticker),
            category=None,
            close_time=row.get("close_time") if isinstance(row.get("close_time"), str) else None,
            status=row.get("status") if isinstance(row.get("status"), str) else None,
            quote=VenueQuote(
                yes_bid=_probability(row.get("yes_bid_dollars")),
                yes_ask=_probability(row.get("yes_ask_dollars")),
                yes_bid_size=_size(row.get("yes_bid_size_fp")),
                yes_ask_size=_size(row.get("yes_ask_size_fp")),
            ),
        ))
    return markets


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

    def __init__(self, *, authorization: Authorization | None = None, book_limit: int = DEFAULT_BOOK_LIMIT):
        self._authorization = authorization
        self._book_limit = _clamp(book_limit, DEFAULT_BOOK_LIMIT, MAX_BOOK_LIMIT)

    def list_markets(self, *, limit: int, timeout_seconds: int) -> PredMarketSnapshot:
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )
        started = time.monotonic()
        params = {"limit": int(limit), "active": "true", "closed": "false"}
        payload = _get_json(
            f"{self.GAMMA_BASE}/markets?{urllib.parse.urlencode(params)}",
            timeout_seconds=timeout_seconds,
        )
        markets = parse_gamma_markets(payload)[:limit]

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
        ))
    return markets


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


# --- Predict.fun markets, reached through Binance -------------------------------

class PredictFunCollector:
    """Predict.fun markets over **Binance's** Prediction Trading REST API.

    Two names, one venue, and the split is deliberate:

    - the **venue** is Predict.fun on BNB Smart Chain — whose markets these are, and whose
      resolution rules the operator compares at confirmation time. The API says so itself
      (``vendor: "PREDICT_FUN"``);
    - the **route** is Binance, which is where the credential, the host and the rate limit
      come from. Hence the grant ``binance_prediction`` and the ``BINANCE_PREDICTION_*`` keys.

    Chosen over Predict.fun's own REST API (Thomas, 2026-07-26) because the money path
    decides it: funding a Binance prediction account is a transfer from an existing balance,
    while the direct venue needs a self-custodied BNB-chain wallet. Reading through the same
    door that will later trade keeps one credential and one identifier space.

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

    venue = PREDICTFUN
    tool_id = PREDMARKET_TOOL_ID
    tool_version = f"{PREDMARKET_TOOL_VERSION}-binance-prediction"
    provider_id = PREDICTFUN_PROVIDER_ID
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
    ):
        host = (urllib.parse.urlparse(base_url).hostname or "").lower()
        if host not in self.ALLOWED_HOSTS:
            # Refuse at construction: a signed key must never be pointed at an unexpected
            # host, and a URL typo should fail loudly rather than leak a credential.
            raise ToolBlocked("HOST_NOT_ALLOWED", "prediction base URL is not an allowed host")
        self._base_url = base_url.rstrip("/")
        self._authorization = authorization
        self._book_limit = _clamp(book_limit, DEFAULT_BOOK_LIMIT, MAX_BOOK_LIMIT)

    def credentials_present(self) -> bool:
        return bool(
            os.environ.get(PREDICTFUN_API_KEY_ENV, "").strip()
            and os.environ.get(PREDICTFUN_API_SECRET_ENV, "").strip()
        )

    def _signed_get(self, path: str, params: Mapping[str, Any], *, timeout_seconds: int) -> Any:
        api_key = os.environ.get(PREDICTFUN_API_KEY_ENV, "").strip()
        api_secret = os.environ.get(PREDICTFUN_API_SECRET_ENV, "").strip()
        if not api_key or not api_secret:
            # Names only — the absence of a credential is reportable, its value never is.
            # A distinct code from a degrade: an unfinished setup step is not an outage.
            raise ToolError(
                API_KEY_MISSING,
                f"prediction credentials are not configured "
                f"({PREDICTFUN_API_KEY_ENV}/{PREDICTFUN_API_SECRET_ENV})",
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
            headers={"Accept": "application/json", "X-MBX-APIKEY": api_key},
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

    def list_markets(self, *, limit: int, timeout_seconds: int) -> PredMarketSnapshot:
        # Chokepoint: re-verify at the moment of egress (defense in depth).
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )
        started = time.monotonic()
        listing = self._signed_get(
            self.LIST_PATH,
            {"limit": min(int(limit), self.PAGE_LIMIT), "offset": 0},
            timeout_seconds=timeout_seconds,
        )
        topics = parse_prediction_topics(listing)[:limit]

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
            try:
                book = self._signed_get(
                    self.BOOK_PATH,
                    {"vendor": self.VENDOR, "marketId": market_id, "tokenId": token_id},
                    timeout_seconds=timeout_seconds,
                )
            except ToolError:
                book = None
            markets.append(PredMarket(
                venue=PREDICTFUN,
                # Keyed on the outcome token, like Polymarket: it is what the book and any
                # later order key on.
                market_id=token_id,
                # The cross-reference axis. Predict.fun's conditionId is the same shape as
                # Polymarket's, so a group can be matched on it rather than on wording.
                group_id=condition_id or topic.group_id,
                title=topic.title,
                category=topic.category,
                close_time=topic.close_time,
                status=topic.status,
                fee_rate_bps=topic.fee_rate_bps,
                quote=parse_prediction_book(book) if book is not None else VenueQuote(),
            ))

        return PredMarketSnapshot(
            venue=self.venue,
            markets=markets,
            source=self.source,
            is_synthetic=False,
            collector_version=self.tool_version,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


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
            venue=PREDICTFUN,
            market_id=str(topic_id),
            group_id=None,
            title=str(row.get("question") or row.get("title") or topic_id),
            category=row.get("chartType") if isinstance(row.get("chartType"), str) else None,
            close_time=_ms_to_iso(row.get("endDate")),
            # tradingStatus lives on the individual markets; the topic's own status is the
            # coarser one, and a topic that is not REGISTERED cannot be traded either way.
            status=row.get("status") if isinstance(row.get("status"), str) else None,
            fee_rate_bps=int(fee_bps) if isinstance(fee_bps, (int, float)) and not isinstance(fee_bps, bool) else None,
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
    "PREDICTFUN",
    "PREDICTFUN_API_KEY_ENV",
    "PREDICTFUN_API_SECRET_ENV",
    "PREDICTFUN_ENV",
    "PREDICTFUN_PROVIDER_ID",
    "PREDMARKET_DEGRADED",
    "PREDMARKET_TOOL_ID",
    "PREDMARKET_TOOL_VERSION",
    "VENUES",
    "KalshiPublicCollector",
    "MockPredMarketCollector",
    "PolymarketPublicCollector",
    "PredictFunCollector",
    "PredMarket",
    "PredMarketCollector",
    "PredMarketSnapshot",
    "VenueQuote",
    "collect_pred_markets",
    "degraded_pred_market_record",
    "parse_clob_book",
    "parse_gamma_markets",
    "parse_kalshi_markets",
    "parse_prediction_book",
    "parse_prediction_topics",
    "parse_prediction_yes_outcome",
    "require_venue",
    "select_pred_market_collector",
]
