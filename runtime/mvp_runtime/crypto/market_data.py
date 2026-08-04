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
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence

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
# The second venue, and a SEPARATE grant: one file per provider is the gate's rule, so
# authorizing Binance's public reads never authorizes Hyperliquid's. Selection is a CHOICE
# between the two, never a chain — the model provider's ordered failover is the wrong shape
# here and `select_market_data_collector` says why.
HYPERLIQUID = "hyperliquid"
# The candle archive opts in on its OWN axis. `MVP_MARKET_DATA` names the single venue the
# pipeline collects from, so pointing it at Hyperliquid would take the crypto pipeline off
# Binance — including the leg that can place a real order. Archiving is a second consumer with
# a different venue, which that axis cannot express. The liquidation feed set this precedent
# (`MVP_LIQUIDATION_FEED`): a second data consumer gets its own env and its own selector, and
# shares the PROVIDER grant, because one grant per provider is the rule and this is the same
# provider making the same egress to the same host.
CANDLE_ARCHIVE_ENV = "MVP_CANDLE_ARCHIVE"
# Public-endpoint reads cross the network but invoke no model — network_access only.
_NETWORK_FLAGS = (NETWORK_ACCESS,)

# --- what each venue can actually answer -------------------------------------------------
#
# A feature is only mintable where the data behind it exists. Declared per venue rather than
# discovered per call, and the direction matters: an UNKNOWN venue raises rather than
# resolving to an empty set, because "this venue provides nothing" and "nobody has said what
# this venue provides" must not produce the same silence.
#
# Why declaring feeds beats listing features: a new feature is assigned to a feed once, and
# every venue's answer follows. `factory._FEATURE_FEED` is TOTAL over the vocabulary — every
# mintable feature names a feed, candle-derived ones included — so a feature nobody classified
# resolves to a feed no venue declares and is mintable nowhere, rather than everywhere.
FEED_CANDLES = "candles"                    # OHLCV itself: no venue can be mined without it
FEED_FUNDING = "funding"                    # a funding-rate SERIES, not just the current rate
FEED_DERIVATIVE_PRICE = "derivative_price"  # mark / index / premium as candle-aligned series
FEED_LIQUIDATION = "liquidation"            # forced closes (Coinalyze — crypto-only vendor)
FEED_OPEN_INTEREST = "open_interest"        # outstanding position, as HISTORY
FEED_POSITIONING = "positioning"            # long/short account ratio (venue-specific)
FEED_TAKER_FLOW = "taker_flow"              # the candle's aggressor split (`taker_buy_base`)
FEED_TRADE_COUNT = "trade_count"            # the candle's trade count

KNOWN_FEEDS = frozenset({
    FEED_CANDLES, FEED_FUNDING, FEED_DERIVATIVE_PRICE, FEED_LIQUIDATION, FEED_OPEN_INTEREST,
    FEED_POSITIONING, FEED_TAKER_FLOW, FEED_TRADE_COUNT,
})

# Measured against each venue rather than assumed (Hyperliquid: 2026-08-03, via
# `candleSnapshot`, `fundingHistory` and `metaAndAssetCtxs`).
#
# Hyperliquid's absences are two different things and the difference is recorded because it
# will matter later. **Never**: liquidations have no equity-covering vendor, and the candle
# carries no aggressor split, so no request would produce either. **Not yet**: open interest,
# mark, oracle and premium all EXIST on `metaAndAssetCtxs` — but only as a current snapshot,
# and these features need a candle-aligned series. That history can be accumulated by polling
# (the `oi_store` shape) and cannot be back-filled, because the venue serves no past for them
# at all. Both are ungated identically today; only one of them stays that way.
VENUE_FEEDS: dict[str, frozenset[str]] = {
    BINANCE_FUTURES: frozenset(KNOWN_FEEDS),
    HYPERLIQUID: frozenset({FEED_CANDLES, FEED_FUNDING, FEED_TRADE_COUNT}),
}

# The feed a feature that named none resolves to. Deliberately NOT in `KNOWN_FEEDS`, so it
# cannot be declared by a venue even by accident — `test_no_venue_can_declare_the_unclassified
# _feed` holds that. An unclassified feature is therefore mintable NOWHERE: still a mistake,
# but one that blocks specs instead of minting them against data that does not exist.
UNCLASSIFIED_FEED = "unclassified"


def venue_feeds(venue: str) -> frozenset[str]:
    """Which feeds ``venue`` provides. Fail-closed on a venue nobody has declared."""
    try:
        return VENUE_FEEDS[venue]
    except (KeyError, TypeError):
        raise ToolBlocked(
            "UNKNOWN_VENUE", f"venue {venue!r} has no declared feeds; declare it before use"
        ) from None

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

# --- being rate limited is not "the exchange is slow" -----------------------------------------
#
# Every read here caught ``(TimeoutError, urllib.error.URLError)`` and raised TOOL_TRANSPORT
# with "failed or timed out". `urllib.error.HTTPError` is a SUBCLASS of `URLError`, so a 429
# (rate limited) and a 418 (IP banned, which is where Binance escalates a 429 that keeps
# knocking) both arrived as a generic timeout — the runtime reported a ban as latency, and the
# operator reading the ledger had no way to tell the two apart.
#
# They are opposite failures. A timeout says "try again"; a 429 says "stop, and if you do not,
# this becomes a ban". So this is not a cosmetic reason code: `PerRunFeedCache` latches on it and
# stops the rest of the fire from knocking, which is the only response that does not escalate.
TOOL_RATE_LIMITED = "TOOL_RATE_LIMITED"
# Venue statuses that mean "you are asking too often". 418 is Binance's escalation of a 429 that
# kept going; treating it as anything softer than a full stop is what turns minutes into days.
_RATE_LIMIT_STATUSES = frozenset({429, 418})


def classify_transport_error(exc: BaseException, label: str) -> ToolError:
    """One shared reading of a failed HTTP read → the typed error to raise.

    Deliberately never echoes the URL (the R3 transport-error posture — a signed request carries
    its signature in the query string, and this classifier is shared with paths that sign).
    ``Retry-After`` rides into the message when the venue sends one, because "rate limited" and
    "rate limited, come back in 120 seconds" are different operational facts.
    """
    status = getattr(exc, "code", None)
    if isinstance(status, int) and status in _RATE_LIMIT_STATUSES:
        retry_after = ""
        try:
            value = exc.headers.get("Retry-After")  # type: ignore[attr-defined]
            if value:
                retry_after = f"; venue asked for {str(value).strip()}s"
        except Exception:  # noqa: BLE001 — a missing/odd header must not mask the rate limit
            pass
        return ToolError(
            TOOL_RATE_LIMITED,
            f"{label} was rate limited by the venue (HTTP {status}{retry_after})",
        )
    return ToolError("TOOL_TRANSPORT", f"{label} request failed or timed out")


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

# Which query parameter each endpoint names the instrument with. Not cosmetic: the index series
# is quoted per PAIR (one index for BTCUSDT however many contracts settle against it) while mark
# and premium are per contract, and the venue rejects the wrong key with 400 `-1102`. Kept as a
# table beside the paths so the two cannot drift — a new kind has to answer both questions.
DERIVATIVE_KLINE_SYMBOL_PARAMS: dict[str, str] = {
    "mark": "symbol",
    "index": "pair",
    "premium": "symbol",
}

# Positioning statistics — WHO is holding what, which is the one thing neither price nor flow
# reports. Three public `/futures/data/` endpoints of the same authorized provider.
#
# The pair is the point, not any single series: `top_position` is the position bias of the top
# 20% of accounts by margin balance, `global_account` is the bias of ALL accounts counted one
# each. Large capital and account headcount disagreeing is a statement no other column here can
# make — every existing feature would read the two situations identically. `top_account` sits
# beside `top_position` for the same reason at a smaller scale: the two differ exactly when a few
# large positions inside the top cohort outweigh the many.
#
# `takerlongshortRatio` is deliberately NOT collected. It restates the aggressor split the kline
# legs already carry (`taker_buy_base`), and those come with 500 days of history for free while
# this endpoint family keeps 30 — collecting it would be paying a retention wall for a worse copy
# of something already held.
POSITIONING_PATHS: dict[str, str] = {
    "top_position": "topLongShortPositionRatio",
    "top_account": "topLongShortAccountRatio",
    "global_account": "globalLongShortAccountRatio",
}
POSITIONING_SERIES = frozenset(POSITIONING_PATHS)
POSITIONING_PAGE_LIMIT = 500   # venue cap per call
POSITIONING_MAX_PAGES = 4      # 30-day retention at 1h is 720 rows — two pages, with head-room
# Aggregation periods the venue offers, with their lengths, so a partial trailing period can be
# recognized and dropped. A closed map rather than a pass-through string: an unknown period would
# be answered with a series of some other cadence and the store would count it as hours.
POSITIONING_PERIOD_SECONDS: dict[str, int] = {
    "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200,
    "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400,
}
POSITIONING_DEGRADED = "POSITIONING_DEGRADED"
# The venue caps these at 1000 rows per call (klines allow 1500), and they page by the
# same exclusive ``endTime`` walk.
DERIVATIVE_PAGE_LIMIT = 1000
DERIVATIVE_MAX_PAGES = 60

# How much derivative history (liquidations, open interest) the runtime asks the feed for.
#
# Lived in `cycle._LIQUIDATION_DAYS` until 2026-08-04, where it was one consumer's private
# number. It is moved here because a SECOND consumer now needs it and they must not disagree:
# `factory.templates_for_timeframe` gates the oi_* families on whether this depth reaches the
# replay window, and a gate reading a different number than the fetch would either mint
# families the data cannot answer or refuse ones it can.
#
# 520 covers the 500-day calendar replay with head-room — see the interval comment below, which
# states that premise — and it does NOT cover 1d, whose window is a 2,000-BAR floor
# (`MIN_FACTORY_BARS`) rather than a calendar span, i.e. 2,000 days. That gap is what the gate
# in the factory exists for.
DERIVATIVE_HISTORY_DAYS = 520

# Open-interest aggregation intervals the feed will request. `daily` is what every feature path
# reads — it is the only depth that covers the factory's 500-day replay, since hourly history
# stops ~84 days back (measured on the vendor 2026-07-29). **"The factory's 500-day replay" is
# true of every timeframe except 1d**, which replays `MIN_FACTORY_BARS` = 2,000 daily bars, so
# the daily series answers about a quarter of it; `DERIVATIVE_HISTORY_DAYS` above and the oi_*
# gate in `factory.templates_for_timeframe` are where that exception is handled. `1hour` exists
# for `oi_store`, which
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

# The cohort every symbol's momentum is RANKED against — the cross-sectional universe.
#
# A declared constant, not "whichever symbols the pool happens to route today", and the
# reason is the same one that pins ``REFERENCE_SYMBOL`` one notch harder. A rank is a
# statement *about a cohort*: ``xs_rank_pct >= 0.8`` means "in the strongest fifth of THESE
# symbols". If the cohort followed the pool, a spec mined when six symbols were routable
# would be evaluated against three after two demotions — same threshold, different claim,
# and nothing in the record would show the substitution. Worse, the cohort would then depend
# on the pool, which depends on the backtests, which were scored against the cohort.
#
# **Changing this tuple invalidates every ``xs_*`` backtest mined under the old cohort**, in
# the ordinary way that changing ``features.ROC_FAST`` or ``PERCENTILE_WINDOW`` invalidates
# every spec mined under the old window. It is not a new hazard class, but it is a louder
# instance of one, which is why ``xs_members`` rides on the feature row: a record then shows
# how many members its rank was actually computed over rather than leaving a reader to assume
# the constant that happens to be in the file today.
#
# Six large-cap USD-M perps. Six rather than three because ``xs_rank_pct`` has only
# ``members - 1`` distinct values, so a three-symbol cohort offers {0, 0.5, 1.0} and a mined
# threshold is a choice among three buckets; and six rather than twenty because every member
# is one more candle series per timeframe per fan-out, paid on every fire.
CROSS_SECTION_UNIVERSE: tuple[str, ...] = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
)
CROSS_SECTION_DEGRADED = "CROSS_SECTION_DEGRADED"
_SYMBOL_PATTERN = re.compile(r"\A[A-Z0-9]{5,20}\Z")  # e.g. BTCUSDT; anchored (QA wave 7)

# HIP-3 builder-deployed markets are named `dex:ticker` (`xyz:XLE`, `para:AVGO`) and the
# prefix is the venue's own format, not decoration: `AVGO` is listed on BOTH `xyz` and
# `para`, so stripping it would silently merge two different books into one symbol. The
# pattern is therefore a lowercase dex segment, a colon, then the ticker — which is why
# `_SYMBOL_PATTERN` cannot simply be widened: uppercase-only and a 5-char floor reject
# every HIP-3 name (`xyz:XLE` for both reasons, bare `XLE` for the floor).
#
# Per-venue rather than one permissive union, for the reason the anchored pattern exists at
# all: a symbol that is well-formed for the WRONG venue must not reach that venue's endpoint.
_HIP3_SYMBOL_PATTERN = re.compile(r"\A[a-z0-9]{2,10}:[A-Z0-9]{2,20}\Z")

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
    # Fewer closed candles came back than the caller asked for — the venue has no more,
    # in either direction it can run out: a hard per-request ceiling, or a market too
    # young to fill the window. Beside `is_synthetic` because it answers the same kind of
    # question ("what IS this data") rather than reporting a failure; the collection
    # succeeded and returned everything that exists.
    #
    # Recorded because the caller cannot infer it and the consequence is silent. The
    # factory asks for `factory_candle_target` bars — 12,000 at 1h — and a venue with a
    # 5,000-row ceiling answers 5,000 with no error. Without this flag a window too
    # shallow for the walk-forward slices to span more than one regime is indistinguishable
    # from a normal collection, and the robustness score it produces looks earned.
    #
    # The two causes are deliberately NOT split: one response cannot tell a ceiling from a
    # short history, and the consequence is identical either way.
    depth_capped: bool = False


class MarketDataCollector(Protocol):
    tool_id: str
    tool_version: str
    # Which venue's data this collector returns, and therefore which vocabulary a spec mined
    # from it may name. Already carried for the Safety-Flag Gate (one grant per provider) and
    # reused rather than restated: a collector that needed its own `venue` field could come to
    # disagree with the provider it is actually authorized against, and two statements of one
    # fact is the shape #462 removed from the feature tables.
    provider_id: str

    def collect(
        self, symbol: str, timeframe: str, *, limit: int, timeout_seconds: int
    ) -> MarketSnapshot: ...


def collector_venue(collector: Any) -> str:
    """The venue a collector speaks for.

    Defaults to ``binance_futures`` for a collector that declares nothing — the migration
    fact, not a guess: every collector that existed before this field did was Binance's, and
    the two that carry one already name a declared venue (``test_every_collector_speaks_for_a
    _declared_venue`` holds that they keep doing so).

    The default is the fail-OPEN direction and is bounded downstream rather than here: an
    undeclared venue reaching `known_features` raises, so a collector inventing a venue name
    stops at the factory instead of minting against a vocabulary nobody described.
    """
    return str(getattr(collector, "provider_id", BINANCE_FUTURES))


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
    # The venue this stands in for. Declared rather than left to `collector_venue`'s default
    # because the Mock is also what `select_gated` returns when a grant is MISSING, and a
    # silent default there would be the one case where nobody chose the answer.
    provider_id = BINANCE_FUTURES
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

    def positioning_history(
        self, symbol: str, *, series: str, period: str, limit: int, timeout_seconds: int
    ) -> list[dict[str, Any]]:
        """Deterministic positioning ratios on the anchor grid (mock accumulation feed).

        The three series are given DIFFERENT walks on purpose. The whole reason the store
        collects more than one is that large capital and account headcount can disagree, so a
        mock whose series moved together would make the only interesting case untestable.
        """
        if series not in POSITIONING_PATHS:
            raise ToolError("INVALID_POSITIONING_SERIES", f"series must be one of {sorted(POSITIONING_SERIES)}")
        if period not in POSITIONING_PERIOD_SECONDS:
            raise ToolError("INVALID_POSITIONING_PERIOD", f"period must be one of {sorted(POSITIONING_PERIOD_SECONDS)}")
        step = timedelta(seconds=POSITIONING_PERIOD_SECONDS[period])
        rows: list[dict[str, Any]] = []
        for i in range(max(0, int(limit))):
            moment = self._ANCHOR + i * step
            # Centred near a balanced book, bounded well inside (0, 1) so no row is degenerate.
            long_ratio = round(0.5 + (_frac(f"{symbol}|{series}|{i}") - 0.5) * 0.4, 6)
            rows.append({
                "timestamp": timeutil.format_iso(moment),
                "long_ratio": long_ratio,
                "short_ratio": round(1.0 - long_ratio, 6),
            })
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


def _require_symbol(symbol: Any, *, pattern: re.Pattern[str] | None = None) -> str:
    """Validate a symbol against its VENUE's format.

    ``pattern`` defaults to the Binance form so every existing caller keeps the behaviour
    it had. A collector that names a different venue supplies its own via
    ``symbol_pattern`` (read in :func:`collect_market_data`) — the venue owns what one of
    its symbols looks like, and a per-venue pattern is what stops a name that is
    well-formed for one venue from being sent to another.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ToolBlocked("EMPTY_SYMBOL", "market-data symbol must be a non-empty string")
    if not (pattern or _SYMBOL_PATTERN).fullmatch(symbol):
        raise ToolBlocked("INVALID_SYMBOL", f"symbol {symbol!r} is not valid for this venue")
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
    symbol = _require_symbol(symbol, pattern=getattr(collector, "symbol_pattern", None))
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
        # `source` names the COLLECTOR ("mock.market_data", "binance_futures_public"); this
        # names the venue whose data it returned. They are not the same question and the
        # factory needs the second one: a spec mined from these candles may only reference
        # the features this venue can serve, and until the snapshot said so, that fact lived
        # in an env var read at collector construction and reached nothing downstream.
        "venue": collector_venue(collector),
        "is_synthetic": bool(result.is_synthetic),
        "depth_capped": bool(getattr(result, "depth_capped", False)),
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
        "venue": collector_venue(collector),
        "is_synthetic": bool(result.is_synthetic),
        "depth_capped": bool(getattr(result, "depth_capped", False)),
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
        "venue": collector_venue(collector),
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

    **Two venues now, and this is a CHOICE, not a chain.** ``MVP_MARKET_DATA`` names
    exactly one backend; whichever it names goes through ``select_gated`` and so through
    its OWN provider grant. The model provider's ordered failover chain
    (``select_gated_chain``) is deliberately not reused: that exists so a second provider
    can answer the same question when the first is unavailable, and these two answer
    *different* questions — a Binance symbol has no meaning on Hyperliquid, and silently
    substituting one venue's candles for another's is the failure this shape prevents.
    A backend that is down degrades (above); it does not hand the symbol to a stranger.

    An unrecognised value is not an error here: it matches neither opt-in, so the inert
    Mock is returned. That is the same fail-closed direction the single-venue form had —
    a typo reaches nothing.
    """
    if os.environ.get(MARKET_DATA_ENV, "").strip().lower() == HYPERLIQUID:
        return safety_gate.select_gated(
            env_var=MARKET_DATA_ENV,
            opt_in_value=HYPERLIQUID,
            flags=_NETWORK_FLAGS,
            provider_id=HYPERLIQUID,
            default_factory=MockMarketDataCollector,
            gated_factory=lambda authorization: HyperliquidCollector(authorization=authorization),
            now=now,
            root=root,
        )
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


class NoCandleArchiveCollector:
    """The archive's inert default. Refuses, rather than fabricating.

    **The Mock is deliberately not the default here**, and that is the whole point of this
    class existing. `MockMarketDataCollector` is the right inert default for the pipeline —
    deterministic, offline, and honestly marked `is_synthetic=True` so health checks refuse to
    trade on it. But the archive's entire value is that it holds what the venue really served
    at a moment that cannot be revisited, and a synthetic bar written into it is
    indistinguishable from a real one a year later. An empty archive is recoverable by turning
    the archive on; a poisoned one is not.
    """

    tool_id = MARKET_DATA_TOOL_ID
    tool_version = f"{MARKET_DATA_TOOL_VERSION}-no-archive"
    network_egress = False
    source = "no_candle_archive"

    def collect(self, symbol: str, timeframe: str, *, limit: int, timeout_seconds: int) -> MarketSnapshot:
        raise ToolBlocked(
            "ARCHIVE_NOT_ENABLED",
            f"candle archiving is off; set {CANDLE_ARCHIVE_ENV} and grant the provider to enable it",
        )

    def live_symbols(self, *, dexes: Sequence[str], timeout_seconds: int = 20) -> list[str]:
        raise ToolBlocked("ARCHIVE_NOT_ENABLED", "candle archiving is off")


def select_candle_archive_collector(
    *, now: str | None = None, root: Path | None = None
) -> MarketDataCollector:
    """Choose the archive's collector — its own axis, the same gate, the same provider grant.

    Independent of :func:`select_market_data_collector` on purpose: the pipeline and the
    archive read different venues at the same time, and a single-valued env cannot say that.
    Not opted in returns :class:`NoCandleArchiveCollector`, which writes nothing rather than
    writing something synthetic.
    """
    return safety_gate.select_gated(
        env_var=CANDLE_ARCHIVE_ENV,
        opt_in_value=HYPERLIQUID,
        flags=_NETWORK_FLAGS,
        provider_id=HYPERLIQUID,
        default_factory=NoCandleArchiveCollector,
        gated_factory=lambda authorization: HyperliquidCollector(authorization=authorization),
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
            except (TimeoutError, urllib.error.URLError) as exc:
                raise classify_transport_error(exc, "market-data") from None
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
        window = candles[-limit:]
        return MarketSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            candles=window,
            source=self.source,
            is_synthetic=False,
            collector_version=self.tool_version,
            latency_ms=latency_ms,
            # This venue pages backward, so a short window means its history ran out (or
            # MAX_PAGES stopped the walk) rather than a per-request ceiling. Same fact for
            # the caller either way: fewer bars than the window it asked to score.
            depth_capped=len(window) < limit,
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
            except (TimeoutError, urllib.error.URLError) as exc:
                raise classify_transport_error(exc, "funding") from None
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
                # The index series is keyed on the PAIR, the other two on the symbol, and the
                # venue enforces it: `indexPriceKlines` with `symbol=` returns 400 `-1102
                # Mandatory parameter 'pair' was not sent`. Sending `symbol` to all three left
                # index degraded on every cycle while mark and premium reported ok — a partial
                # failure, which is why it read as a feed blip rather than as a wrong request.
                # The cost was not an outage: `index_price` and `mark_index_basis_bps` stayed
                # indeterminate, so the constant-0.0 basis C13 exists to remove was replaced by
                # a permanently absent one instead of a real measurement.
                DERIVATIVE_KLINE_SYMBOL_PARAMS[kind]: symbol,
                "interval": timeframe,
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

    def positioning_history(
        self, symbol: str, *, series: str, period: str, limit: int, timeout_seconds: int
    ) -> list[dict[str, Any]]:
        """One positioning-ratio series, oldest first. Same grant as candles and funding.

        ``series`` selects the endpoint (:data:`POSITIONING_PATHS`). Returns
        ``[{"timestamp": <period start, ISO>, "long_ratio": float, "short_ratio": float}, ...]``.

        **The trailing incomplete period is dropped**, the still-forming-candle rule applied to a
        different shape and for a sharper reason than usual: the store that consumes this
        de-duplicates on ``(symbol, series, timestamp)`` and is append-only, so a partial
        period written now would be the value kept forever and the venue's later corrected
        reading for that same timestamp would be discarded as a duplicate. Dropping it is what
        makes the accumulation converge on the venue's own numbers.

        ``longShortRatio`` is not returned even though the venue sends it: it is exactly
        ``longAccount / shortAccount``, so storing it alongside both legs creates a third field
        that can disagree with the other two after rounding. The legs are what the venue
        measured; the ratio is arithmetic any reader can redo.

        Pages backward by the same exclusive ``endTime`` walk as ``funding_history`` — the
        30-day retention window at 1h is 720 rows against a 500-row cap, so a seed needs two.
        """
        if series not in POSITIONING_PATHS:
            raise ToolError("INVALID_POSITIONING_SERIES", f"series must be one of {sorted(POSITIONING_SERIES)}")
        if period not in POSITIONING_PERIOD_SECONDS:
            raise ToolError("INVALID_POSITIONING_PERIOD", f"period must be one of {sorted(POSITIONING_PERIOD_SECONDS)}")
        safety_gate.assert_authorization(
            self._authorization, required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id, now=timeutil.utc_now_iso(),
        )
        path = POSITIONING_PATHS[series]
        period_ms = POSITIONING_PERIOD_SECONDS[period] * 1000
        now_ms = int(time.time() * 1000)
        collected: dict[int, dict[str, float]] = {}
        end_time: int | None = None
        pages = 0
        while len(collected) < limit and pages < POSITIONING_MAX_PAGES:
            pages += 1
            params: dict[str, Any] = {
                "symbol": symbol, "period": period,
                "limit": min(POSITIONING_PAGE_LIMIT, limit - len(collected)),
            }
            if end_time is not None:
                params["endTime"] = end_time
            request = urllib.request.Request(
                f"https://fapi.binance.com/futures/data/{path}?{urllib.parse.urlencode(params)}",
                method="GET", headers={"Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                    raw = response.read().decode("utf-8")
            except (TimeoutError, urllib.error.URLError):
                # Never echo the URL (the R3 transport-error posture).
                raise ToolError("TOOL_TRANSPORT", f"{series} positioning request failed or timed out") from None
            page = self._parse_positioning_page(raw, series=series, now_ms=now_ms, period_ms=period_ms)
            if not page:
                break
            oldest = min(page)
            collected.update(page)
            if end_time is not None and oldest >= end_time:
                break  # no backward progress — refuse to spin
            end_time = oldest - 1

        ordered = sorted(collected)[-limit:]
        return [
            {"timestamp": self._iso(stamp), **collected[stamp]}
            for stamp in ordered
        ]

    def _parse_positioning_page(
        self, raw: str, *, series: str, now_ms: int, period_ms: int
    ) -> dict[int, dict[str, float]]:
        """One positioning page as ``{period_start_ms: {"long_ratio", "short_ratio"}}``.

        ``longAccount``/``shortAccount`` are the field names on every one of these endpoints,
        including the POSITION-ratio one where they carry position proportions rather than
        account counts — a venue naming quirk, not a mistake here. The rows are renamed on the
        way in so the store never stores a name that means something else.
        """
        try:
            rows = json.loads(raw)
        except ValueError:
            raise ToolError("MALFORMED_RESULT", f"{series} positioning backend returned an unparseable response") from None
        if not isinstance(rows, list):
            raise ToolError("MALFORMED_RESULT", f"{series} positioning backend returned an unparseable response")

        parsed: dict[int, dict[str, float]] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ToolError("MALFORMED_RESULT", f"{series} positioning backend returned an unparseable response")
            try:
                stamp = int(row["timestamp"])
                long_ratio = float(row["longAccount"])
                short_ratio = float(row["shortAccount"])
            except (KeyError, TypeError, ValueError):
                raise ToolError("MALFORMED_RESULT", f"{series} positioning backend returned an unparseable response") from None
            if stamp + period_ms > now_ms:
                continue  # the period has not closed — its ratio is still moving
            parsed[stamp] = {"long_ratio": long_ratio, "short_ratio": short_ratio}
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
        except (TimeoutError, urllib.error.URLError) as exc:
            raise classify_transport_error(exc, "exchange-info") from None
        try:
            payload = json.loads(raw)
        except ValueError:
            raise ToolError("MALFORMED_RESULT", "exchange-info returned an unparseable response") from None
        if not isinstance(payload, dict):
            raise ToolError("MALFORMED_RESULT", "exchange-info returned an unparseable response")
        return payload


# --- S1 Hyperliquid collector (HIP-3 equity perps — its own provider and grant) ----------

class HyperliquidCollector:
    """Real OHLCV via Hyperliquid's public ``info`` endpoint (read-only, no API key).

    Same posture as :class:`BinanceFuturesCollector`: public data, but an outbound HTTPS
    request, so the Safety-Flag Gate must be open for the ``hyperliquid`` provider before
    this class is constructed, and ``collect`` re-verifies at the moment of egress.

    Three things differ from the Binance collector, all forced by the venue:

    - **Symbols are ``dex:ticker``** (`xyz:XLE`). ``symbol_pattern`` publishes that format
      so :func:`collect_market_data` validates against this venue's shape rather than
      Binance's, which rejects every HIP-3 name.
    - **No paging.** One request answers with at most ``PAGE_LIMIT`` rows and there is
      nothing behind them — this is a hard ceiling, not a page boundary, so a backward
      walk would re-ask for history the venue does not have. A short window is reported
      through ``depth_capped`` instead of being papered over with more requests.
    - **The request is a POST with a JSON body**, and the window is expressed as a time
      range rather than a count, so ``limit`` is converted to a ``startTime`` here.
    """

    tool_id = MARKET_DATA_TOOL_ID
    tool_version = f"{MARKET_DATA_TOOL_VERSION}-hyperliquid"
    provider_id = HYPERLIQUID
    network_egress = True  # makes an outbound HTTPS call — recorded as network egress
    source = "hyperliquid_public"
    symbol_pattern = _HIP3_SYMBOL_PATTERN
    _ENDPOINT = "https://api.hyperliquid.xyz/info"
    # The venue serves at most this many candles per request and nothing older. Measured
    # 2026-08-03: `xyz:SP500` listed 2026-03-18 and returns its full 138 days at 1h (3,304
    # rows) but only 52 days at 15m (5,007 rows) — so the ceiling is real and the older 15m
    # history is already unreachable. Named a LIMIT rather than a page size for that reason.
    PAGE_LIMIT = 5_000

    def __init__(self, *, authorization: Authorization | None = None):
        # Egress authorization from the Safety-Flag Gate. Without it, collect() refuses
        # to open a socket — a directly-constructed collector cannot bypass the gate.
        self._authorization = authorization

    def collect(
        self, symbol: str, timeframe: str, *, limit: int, timeout_seconds: int
    ) -> MarketSnapshot:
        """At most ``limit`` closed candles, newest last. One request, no paging."""
        # Chokepoint: re-verify authorization at the moment of egress (defense in depth).
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )
        started = time.monotonic()
        now_ms = int(time.time() * 1000)
        minutes = TIMEFRAMES[timeframe]
        # One extra bar of span: the venue returns the still-forming candle, and dropping
        # it must not shrink the caller's requested window. `max(0, …)` because a window
        # longer than the epoch is a caller's arithmetic, not a request worth sending.
        span_ms = (min(limit, self.PAGE_LIMIT) + 1) * minutes * 60_000
        payload = json.dumps({
            "type": "candleSnapshot",
            "req": {
                "coin": symbol,
                "interval": timeframe,
                "startTime": max(0, now_ms - span_ms),
                "endTime": now_ms,
            },
        }).encode("utf-8")
        request = urllib.request.Request(
            self._ENDPOINT,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except (TimeoutError, urllib.error.URLError) as exc:
            raise classify_transport_error(exc, "market-data") from None
        latency_ms = int((time.monotonic() - started) * 1000)

        parsed = self._parse(raw, now_ms=now_ms)
        # Keyed by open time before ordering: a venue that repeats a boundary bar must not
        # have it counted twice (the Binance collector's rule, kept even without paging).
        by_open = {open_ms: candle for open_ms, candle in parsed}
        candles = [by_open[open_ms] for open_ms in sorted(by_open)]
        window = candles[-limit:]
        return MarketSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            candles=window,
            source=self.source,
            is_synthetic=False,
            collector_version=self.tool_version,
            latency_ms=latency_ms,
            depth_capped=len(window) < limit,
        )

    def _parse(self, raw: str, *, now_ms: int) -> list[tuple[int, Candle]]:
        """The candle array as ``(open_time_ms, Candle)``, still-forming bar dropped."""
        try:
            rows = json.loads(raw)
        except ValueError:
            raise ToolError("MALFORMED_RESULT", "market-data backend returned an unparseable response") from None
        if not isinstance(rows, list):
            raise ToolError("MALFORMED_RESULT", "market-data backend returned an unparseable response")

        parsed: list[tuple[int, Candle]] = []
        for row in rows:
            # The venue's candle is an OBJECT, not a positional row:
            #   t open_ms   T close_ms   s symbol   i interval
            #   o open   c close   h high   l low   v volume   n trade count
            # OHLCV arrive as STRINGS; `n` is an integer.
            if not isinstance(row, dict):
                raise ToolError("MALFORMED_RESULT", "market-data backend returned an unparseable response")
            try:
                open_ms, close_ms = int(row["t"]), int(row["T"])
                candle = Candle(
                    open_time=self._iso(open_ms),
                    open=float(row["o"]),
                    high=float(row["h"]),
                    low=float(row["l"]),
                    close=float(row["c"]),
                    volume=float(row["v"]),
                    close_time=self._iso(close_ms),
                    # `quote_volume`, `taker_buy_base` and `taker_buy_quote` are STRUCTURALLY
                    # absent here — this venue's candle does not carry them, and no request
                    # or grant would produce them. They stay None rather than being derived
                    # (`volume * close` would be a plausible-looking fabrication) or dropped,
                    # so a flow spec goes indeterminate and refuses to trade instead of
                    # matching on a number nobody measured. `trade_count` IS reported.
                    trade_count=float(row["n"]) if row.get("n") is not None else None,
                )
            except (KeyError, TypeError, ValueError):
                raise ToolError("MALFORMED_RESULT", "market-data backend returned an unparseable response") from None
            if close_ms >= now_ms:
                continue  # still-forming candle — its close is still moving
            parsed.append((open_ms, candle))
        return parsed

    @staticmethod
    def _iso(epoch_ms: int) -> str:
        # Pure arithmetic, not fromtimestamp — the Binance collector's reason: Windows'
        # gmtime rejects far-future epochs and a venue-supplied timestamp is input.
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=epoch_ms)
        return timeutil.format_iso(epoch)

    def live_symbols(self, *, dexes: Sequence[str], timeout_seconds: int = 20) -> list[str]:
        """Every non-delisted symbol on the named builder dexes, sorted.

        Read at run time rather than declared, because a symbol that lists and is not archived
        loses history nobody can recover. The DEX list is the declared part — see
        ``candle_archive.ARCHIVE_DEXES`` for why that asymmetry is the safe one.

        ``perpDexs`` and ``allPerpMetas`` are index-aligned (entry *i* of one describes the dex
        whose universe is entry *i* of the other), and the main perp dex occupies slot 0 with a
        null name. Verified against the venue 2026-08-03. A length mismatch is refused rather
        than zipped short: pairing a universe with the wrong dex would archive symbols under a
        deployer that does not list them.
        """
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_NETWORK_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )
        wanted = {str(d) for d in dexes}
        listings = self._info({"type": "perpDexs"}, timeout_seconds=timeout_seconds)
        metas = self._info({"type": "allPerpMetas"}, timeout_seconds=timeout_seconds)
        if not isinstance(listings, list) or not isinstance(metas, list):
            raise ToolError("MALFORMED_RESULT", "venue metadata returned an unparseable response")
        if len(listings) != len(metas):
            raise ToolError("MALFORMED_RESULT", "venue metadata is not index-aligned")
        names: list[str] = []
        for listing, block in zip(listings, metas):
            name = listing.get("name") if isinstance(listing, dict) else None
            if name not in wanted:
                continue
            universe = (block or {}).get("universe") if isinstance(block, dict) else None
            for asset in universe or []:
                if not isinstance(asset, dict) or asset.get("isDelisted"):
                    continue
                symbol = asset.get("name")
                if isinstance(symbol, str) and self.symbol_pattern.fullmatch(symbol):
                    names.append(symbol)
        return sorted(set(names))

    def _info(self, body: dict[str, Any], *, timeout_seconds: int) -> Any:
        """One POST to the info endpoint, decoded. Transport failures classify like collect's."""
        request = urllib.request.Request(
            self._ENDPOINT,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
                raw = response.read().decode("utf-8")
        except (TimeoutError, urllib.error.URLError) as exc:
            raise classify_transport_error(exc, "market-data") from None
        try:
            return json.loads(raw)
        except ValueError:
            raise ToolError("MALFORMED_RESULT", "venue metadata returned an unparseable response") from None


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


class PeerCandleCache:
    """One candle read per (symbol, timeframe, depth) for the life of one fan-out.

    The same redundancy :class:`PerSymbolFeedCache` was built for, arriving by a different
    door — the **context legs**, which read *other symbols'* candles alongside the one the
    cycle is trading. Two legs need that today and they overlap heavily:

    - the reference leg (``cycle.attach_reference``) always reads :data:`REFERENCE_SYMBOL`,
      so across a fan-out the only thing that varies is the timeframe — yet it runs per
      (symbol, timeframe), asking for **four** distinct BTC series sixteen times on a
      5×4 grid;
    - the cross-sectional leg (``cycle.attach_cross_section``) reads every member of
      :data:`CROSS_SECTION_UNIVERSE` except the traded symbol, which is the same overlap
      multiplied: without a cache, six members × four timeframes × five contexts is 120 asks
      for 24 answers.

    In both cases the redundant reads are redundant for the same reason: within one fire,
    "what were ETH's hourly candles" cannot legitimately differ depending on which symbol is
    asking. So one cache serves both legs rather than one per leg — the object is a cache of
    *candle reads*, and which leg wanted them is not a property of the answer.

    Same contract as the feed cache, for the same reasons: constructed per fan-out and
    thrown away, so it can never serve a stale bar to a later fire; **failures cached too**,
    because a venue that just refused this series will refuse it again in the same second and
    re-asking once per remaining context is how a rate cap gets reached; and the refusal is
    re-raised unchanged, so every context still records its own reason code.

    ``symbol`` defaults to the reference symbol, so the reference leg reads exactly as it did
    before the cross-sectional one existed.
    """

    def __init__(self, collector: Any):
        self._collector = collector
        self._results: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        self._errors: dict[tuple[str, str, str], BaseException] = {}
        self.calls = 0

    def candles(
        self, timeframe: str, *, limit: int, now: str, symbol: str = REFERENCE_SYMBOL
    ) -> list[dict[str, Any]]:
        key = (str(symbol), str(timeframe), str(limit))
        if key in self._errors:
            raise self._errors[key]
        if key in self._results:
            return self._results[key]
        self.calls += 1
        try:
            snapshot, _record = collect_market_data(
                symbol, timeframe, collector=self._collector, now=now, limit=limit
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
        except (TimeoutError, urllib.error.URLError) as exc:
            # Never echoes the URL or key — see `classify_transport_error`.
            raise classify_transport_error(exc, "liquidation") from None
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
        except (TimeoutError, urllib.error.URLError) as exc:
            raise classify_transport_error(exc, "open-interest") from None
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


# --- one fan-out, one request per distinct question -------------------------------------------

class PerRunFeedCache:
    """Memoizes the per-SYMBOL reads a pool fan-out repeats, for the length of ONE fire.

    A cycle is keyed by ``(symbol, timeframe)`` but funding, liquidations, open interest and the
    venue's trading rules are keyed by **symbol alone**. So a pool spread over 5 symbols x 4
    timeframes asked the venue the same question four times per symbol, per fire, with identical
    arguments: 40 funding pages, 20 liquidation windows and 20 open-interest windows where 10, 5
    and 5 would answer. `oi_store` already solved exactly this for the hourly OI series — its own
    docstring names the arithmetic ("80 vendor requests an hour to record 5 new readings") — but
    it solved it for one feed, with a disk throttle, and the other three kept fanning out.

    Measured over that fan-out, 2026-07-29: **115 venue calls become 45**, and the funding line
    understates it because each of those calls is two pages at ``DEFAULT_FUNDING_RECORDS``. What
    this buys is wall clock rather than quota — every call is a fresh `urlopen` with its own TLS
    handshake, and the scheduler runs schedules sequentially, so a fan-out holds the tick for as
    long as it takes. That tick is shared with the live leg's `_settle_or_protect`.

    Deliberately a memo, not a cache:

    * **It lives for one fire.** The scheduler constructs it beside the collector and drops it
      when the fire ends, so the data a cycle reads is never older than that cycle. Nothing here
      makes a 15-minute cadence stale — it makes one cadence cost one request.
    * **A failure is memoized too, and re-raised.** Funding that fails for BTCUSDT at 15m fails
      at 1h; retrying is three more timeouts on a scheduler that runs everything sequentially.
      Each context still records its own reason code, because the raise reaches each of them.
    * **It adds no egress and no authority.** It wraps an already-gated collector and forwards
      every attribute it does not memoize, including `network_egress`, `provider_id` and the
      safety-flag pair. It cannot make a call the wrapped object could not.

    ``__getattr__`` rather than declared methods, for one specific reason: `attach_feeds` asks
    ``hasattr(collector, "funding_history")`` to decide whether a collector *has* the capability,
    and `exchange_info` is deliberately absent from `MockMarketDataCollector` so a mock run
    refuses to size rather than sizing on invented lot steps. Declaring those methods here would
    answer yes on a collector that cannot, which is the one thing this wrapper must not do.
    """

    # Keyed by symbol alone at the venue, so identical across every timeframe of one fan-out.
    _MEMOIZED = frozenset({
        "funding_history", "liquidation_history", "open_interest_history", "exchange_info",
    })

    def __init__(self, inner: Any):
        # Set through __dict__ so __getattr__ can never recurse looking for it.
        self.__dict__["_inner"] = inner
        self.__dict__["_memo"] = {}
        self.__dict__["requests"] = 0   # what was actually asked of the venue
        self.__dict__["hits"] = 0       # what a repeat context did not have to ask again
        self.__dict__["rate_limited"] = None  # the venue's refusal, once it has given one

    def _latch(self) -> None:
        """Refuse before opening a socket, once the venue has said we are asking too often.

        A 429 is not a transient failure to retry past — at this venue it is the step BEFORE a
        418 IP ban, and the thing that causes the escalation is continuing to knock. A fan-out
        that keeps going after the first refusal makes twenty more requests, which is precisely
        how minutes of throttling become days of ban.

        The scope is deliberate and narrow. This latches **market data**: candles, funding,
        liquidations, open interest, exchangeInfo. It cannot reach the order adapter or the
        account read — those are separate objects on separate endpoints with separate limits, and
        neither is wrapped here — so a rate-limited fire still settles, protects and closes an
        open live position. What it does stop is opening new ones, which is the correct posture
        for a runtime that cannot currently see the market.
        """
        latched = self.__dict__["rate_limited"]
        if latched is not None:
            raise latched

    def collect(self, symbol: str, timeframe: str, *, limit: int, timeout_seconds: int) -> MarketSnapshot:
        """Candles, served from a DEEPER cached window when one exists for this ``(symbol,
        timeframe)``.

        The overlap is structural rather than accidental: `attach_htf` fetches one step up the
        ladder at 240 bars, and the context for that very timeframe fetches its own 120 — so a
        pool routing 15m/1h/4h/1d collects 1h and 4h twice each, every fire. Both windows end at
        the same last closed candle (that is what `drop_forming_candle` guarantees), so the tail
        of the deeper one IS what the shallower request would have returned.

        A shallower cached window never serves a deeper request — that would silently shorten an
        indicator's warm-up, which is the kind of quiet difference this repo spends its rules
        preventing.

        The comparison is against the depth **asked for**, not the depth received. A venue with
        less history than requested (a recently listed symbol) returns a short window, and keying
        on the returned length would make every such symbol miss forever — the case where the
        fan-out is *cheapest* to dedup. Asking for 240 and getting 90 means 90 is all there is,
        so a later 120-bar request has the same 90 waiting for it.
        """
        key = ("collect", symbol, timeframe)
        cached = self._memo.get(key)
        if cached is not None:
            asked_for, snapshot = cached
            if asked_for >= limit:
                self.__dict__["hits"] += 1
                return replace(snapshot, candles=snapshot.candles[-limit:]) if limit else snapshot
        self._latch()
        self.__dict__["requests"] += 1
        try:
            snapshot = self._inner.collect(
                symbol, timeframe, limit=limit, timeout_seconds=timeout_seconds
            )
        except ToolError as exc:
            self._note_rate_limit(exc)
            raise
        self._memo[key] = (limit, snapshot)
        return snapshot

    def _note_rate_limit(self, exc: ToolError) -> None:
        """Latch the FIRST refusal and keep it. A later one would say the same thing about a
        request this wrapper should not have made."""
        if getattr(exc, "reason_code", None) == TOOL_RATE_LIMITED and self.rate_limited is None:
            self.__dict__["rate_limited"] = exc

    def __getattr__(self, name: str) -> Any:
        # getattr on the inner object first: an attribute it does not have must stay absent here,
        # so `hasattr` keeps answering about the real capability.
        attr = getattr(self._inner, name)
        if name not in self._MEMOIZED or not callable(attr):
            return attr

        def memoized(*args: Any, **kwargs: Any) -> Any:
            key = (name, args, tuple(sorted(kwargs.items())))
            if key in self._memo:
                self.__dict__["hits"] += 1
                outcome, value = self._memo[key]
                if outcome == "raised":
                    raise value
                return value
            self._latch()
            self.__dict__["requests"] += 1
            try:
                value = attr(*args, **kwargs)
            except MvpRuntimeError as exc:
                if isinstance(exc, ToolError):
                    self._note_rate_limit(exc)
                self._memo[key] = ("raised", exc)
                raise
            self._memo[key] = ("returned", value)
            return value

        return memoized
