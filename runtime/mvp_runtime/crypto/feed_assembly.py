"""One context's market inputs, assembled, and how far a live entry may trust them (crypto PR7e-2).

`attach_feeds` puts the derivative legs on a context's snapshot (funding, the mark, index and premium
prices, liquidations, open interest) from the collector and the separately gated liquidation feed, and
on request accumulates the long-horizon stores. `attach_htf`, `attach_reference`,
`attach_cross_section` and `attach_positioning` add the higher timeframe, the reference symbol, the
cross-sectional cohort and the accumulated positioning rows. Every leg degrades rather than blocks,
and names its failure with a degrade code. `optional_data_health` then judges those legs for the live
entry door (PR2d-2, Thomas decision 28): a degrade code, or a reading older than its feed's bound,
refuses the context for a live entry. Paper, the counterfactual shadow and the probe do not read it.

This is market work: it reads the venue and the vendors through `market_data` and the market stores,
and nothing above them. It lived in `cycle`, the orchestrator that calls it once per context, which
re-exports every name here as the same object.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from .. import timeutil
from ..errors import ToolBlocked, ToolError
from . import oi_store, orderbook_store, positioning_store
from .market_data import (
    CROSS_SECTION_DEGRADED,
    CROSS_SECTION_UNIVERSE,
    DEFAULT_FUNDING_RECORDS,
    DERIVATIVE_HISTORY_DAYS,
    FUNDING_DEGRADED,
    HIGHER_TIMEFRAME,
    INDEX_PRICE_DEGRADED,
    LIQUIDATION_DEGRADED,
    MARK_PRICE_DEGRADED,
    OPEN_INTEREST_DEGRADED,
    PREMIUM_INDEX_DEGRADED,
    REFERENCE_DEGRADED,
    REFERENCE_SYMBOL,
    TIMEFRAMES,
    MarketDataCollector,
    collect_market_data,
)

# The higher-timeframe leg could not be read this cycle; htf_* specs stay no-entry.
HTF_DEGRADED = "HTF_DEGRADED"

# Funding events fetched per cycle: ≥3/day covers the deepest replay window.
#
# Read from `market_data` for the reason stated one line below about the liquidation depth, which
# this constant did not follow: it was a second literal 1,600 beside `DEFAULT_FUNDING_RECORDS`,
# and the two would have parted the moment either moved. They nearly did — the replay window
# doubled on 2026-08-04 and only the `market_data` copy was written down as depending on it.
_FUNDING_RECORDS = DEFAULT_FUNDING_RECORDS
# Read from `market_data` rather than restated here: `factory.templates_for_timeframe` now gates
# the oi_* families on the same depth, and two numbers for one fetch would let the gate and the
# fetch drift apart silently.
_LIQUIDATION_DAYS = DERIVATIVE_HISTORY_DAYS

# --- the optional data an entry is judged on (PR2d-2, Thomas decision 28) ----------------------
#
# Every leg above degrades rather than blocks, which is right for paper and wrong for money in two
# ways the investigation measured (`pr2d-missing-checks-investigation.md` §4). A feed that failed
# leaves its columns None, so a strategy reading it cannot fire — but neither can it VETO: two
# strategies on one context that would have disagreed become one that enters alone. And a feed
# that stopped updating keeps its last value forever (`features._asof_align` carries it forward
# with no age limit), so a condition can hold on a reading days old with no degrade code at all.
#
# So the live entry door refuses the context (decision 28): any degrade code below, or any feed
# older than its bound. Paper, the counterfactual shadow and the probe are unaffected — they do
# not read this.
OPTIONAL_DATA_DEGRADED_CODES = frozenset({
    FUNDING_DEGRADED, MARK_PRICE_DEGRADED, INDEX_PRICE_DEGRADED, PREMIUM_INDEX_DEGRADED,
    LIQUIDATION_DEGRADED, OPEN_INTEREST_DEGRADED, HTF_DEGRADED, REFERENCE_DEGRADED,
    CROSS_SECTION_DEGRADED,
})
# How old the reading a bar carries may be, per feed on its own cadence — two periods each (Thomas
# decision 28). Measured against the BAR's open time, the instant the as-of join keys on: the
# decision reads the value that bar carries, and a 1d bar opens a day before it is decided on.
# The same-grid legs (mark, index, premium, the reference, the cohort) join exactly and need none.
FUNDING_MAX_AGE_HOURS = 16.0          # settlements every 8 hours
DAILY_SERIES_MAX_AGE_HOURS = 48.0     # liquidations and open interest; the forming day is dropped
POSITIONING_MAX_AGE_HOURS = 3.0       # accumulated hourly by this runtime
OPTIONAL_FEED_MAX_AGE_HOURS = {
    "funding": FUNDING_MAX_AGE_HOURS,
    "liquidations": DAILY_SERIES_MAX_AGE_HOURS,
    "open_interest": DAILY_SERIES_MAX_AGE_HOURS,
    "positioning": POSITIONING_MAX_AGE_HOURS,
}
# The column each optional leg puts on a bar as its reading there — the one with the least warmup
# of its own, so a healthy leg fills it. None at the decision bar means the bar carries nothing
# from that leg: an answer that came back empty with no degrade code, or a same-grid series (a
# cached reference or peer read) that stops a bar short. Either way a strategy reading the leg
# can neither fire nor veto (review of #892).
OPTIONAL_LEG_COLUMNS = {
    "funding": ("funding_rate",),
    "mark_prices": ("mark_price",),
    "index_prices": ("index_price",),
    "premium_index": ("premium_index",),
    "liquidations": ("long_liquidation", "short_liquidation"),
    "open_interest": ("open_interest",),
    "htf_candles": ("htf_rsi",),
    "reference_candles": ("ref_roc_4",),
    "peer_candles": ("xs_rank_pct",),
    "positioning": ("positioning_divergence",),
}


def _feed_readings(feed: str, events: Any) -> list[Any]:
    """The events a feed's columns are aligned from. Positioning pairs two of its series by time
    (`features._positioning_columns`), so a time only one of them carries is no reading: the
    columns stay on the last pair however fresh the other series is."""
    if not isinstance(events, list):
        return []
    if feed != "positioning":
        return events
    times: dict[str, set[str]] = {}
    for row in events:
        if not (isinstance(row, Mapping) and isinstance(row.get("series"), str)
                and isinstance(row.get("timestamp"), str)):
            continue
        # Only a reading the columns would use: a ratio that is a number (the features' rule).
        value = row.get("long_ratio")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        times.setdefault(row["series"], set()).add(row["timestamp"])
    paired = (times.get("top_position") or set()) & (times.get("global_account") or set())
    return [{"timestamp": stamp} for stamp in paired]


def optional_data_health(
    snapshot: Mapping[str, Any], *, codes: Sequence[str], bar_time: Any, row: Any = None,
) -> dict[str, Any]:
    """What the live entry door judges a context's optional data on (PR2d-2). Pure.

    - ``degraded``: this cycle's degrade codes from the optional legs.
    - ``stale``: each feed whose reading at ``bar_time`` — the last event at or before the bar's
      open, the one the as-of join gives the bar — is older than its bound, or that holds events
      and none readable at or before the bar.
    - ``missing``: each optional leg the snapshot carries that put no reading on the decision
      bar (``row``, `OPTIONAL_LEG_COLUMNS`): an answer that came back empty without a degrade
      code, or a same-grid series that stops a bar short.

    A leg the snapshot does not carry is not judged: not configured, not applicable (the
    reference symbol's own context, a timeframe with no higher one), or nothing to read (no
    positioning rows for the symbol) — its columns are None, as they always were. ``bar_readable``
    says whether ``bar_time`` could be read; when it could not, every carried feed is stale."""
    degraded = sorted({str(code) for code in codes if code in OPTIONAL_DATA_DEGRADED_CODES})
    try:
        bar = timeutil.parse_iso(str(bar_time))
    except (TypeError, ValueError, OverflowError):
        bar = None
    values = row if isinstance(row, Mapping) else {}
    own_proxy = str(snapshot.get("symbol") or "") == str(snapshot.get("reference_symbol") or "-")
    missing = [
        leg for leg, columns in OPTIONAL_LEG_COLUMNS.items()
        if leg in snapshot and not (leg == "reference_candles" and own_proxy)
        and all(values.get(column) is None for column in columns)
    ]
    feeds: dict[str, dict[str, Any]] = {}
    stale: list[str] = []
    for feed, bound in OPTIONAL_FEED_MAX_AGE_HOURS.items():
        state: dict[str, Any] = {"max_age_hours": bound, "last_event_at": None, "age_hours": None}
        if feed not in snapshot:
            feeds[feed] = {**state, "state": "absent"}
            continue
        events = snapshot.get(feed)
        if not events:
            feeds[feed] = {**state, "state": "empty"}
            continue
        last = None
        for event in _feed_readings(feed, events):
            stamp = event.get("timestamp") if isinstance(event, Mapping) else None
            try:
                moment = timeutil.parse_iso(str(stamp)) if isinstance(stamp, str) else None
            except (TypeError, ValueError, OverflowError):
                moment = None
            if moment is not None and bar is not None and moment <= bar and (last is None or moment > last):
                last = moment
        if last is None:
            feeds[feed] = {**state, "state": "unreadable"}
            stale.append(feed)
            continue
        age = (bar - last).total_seconds() / 3600.0
        fresh = age <= bound
        feeds[feed] = {**state, "state": "ok" if fresh else "stale",
                       "last_event_at": timeutil.format_iso(last), "age_hours": round(age, 2)}
        if not fresh:
            stale.append(feed)
    return {"bar_time": bar_time, "bar_readable": bar is not None, "degraded": degraded,
            "stale": stale, "missing": missing, "feeds": feeds}


def attach_feeds(
    snapshot: dict[str, Any],
    *,
    collector: MarketDataCollector,
    liquidation_feed: Any | None,
    now: str,
    root: Path | None = None,
    accumulate: bool = False,
) -> tuple[list[str], dict[str, str]]:
    """Fetch the C9 derivative feeds onto ``snapshot`` (mutating it). Degrade-only.

    ``accumulate`` opts this call into the durable long-horizon stores that feed nothing today
    (currently ``positioning_store``). It defaults to **off** because everything else here only
    mutates ``snapshot``, so a caller that has not asked for durable state should not get any —
    the ``routing_marks`` rule in ``paper.run_paper_update``, where a dry run keeps no marks
    because it keeps no state. The live cycle turns it on; the factory fire does not, since one
    accumulator on the 15-minute cadence is enough and two would only exercise the throttle.
    The failure direction of the default is quiet rather than dangerous: a production caller that
    forgot it would show as flat ``positioning_store.coverage``, which is what that function is
    for. ``oi_store`` needs no such flag — its own accumulation is already gated behind a
    liquidation feed that a caller must supply.

    **This covers only THIS call's symbol, and that is no longer where the store's scope is
    decided.** It was, and what that cost is measured in :func:`cycle.accumulate_positioning_cohort`:
    per-context accumulation records whatever the fan-out visited, so a cohort member the pool
    stopped routing stopped being recorded, permanently and silently. The fan-out now sweeps
    the declared cohort itself, and this flag covers what that sweep cannot reach — the
    operator's single-symbol cycle, which has one context and no fan-out. The overlap costs
    nothing: the store's hourly throttle answers the second asker ``skipped_fresh`` without
    opening a socket.

    Funding comes from the market-data collector when it has the capability (the
    same grant); liquidations from the separately-gated feed. Semantics per feed:
    fetched → real series; fetch FAILED → the key is present and empty, so the
    features are NaN-honest (indeterminate, never a constant) and the failure is a
    reason code; feed NOT CONFIGURED → the key stays absent and the features keep
    the source's legacy constants. Returns ``(reason_codes, feed_status)``."""
    reason_codes: list[str] = []
    status: dict[str, str] = {}
    symbol = str(snapshot.get("symbol") or "")

    if hasattr(collector, "funding_history"):
        try:
            snapshot["funding"] = collector.funding_history(symbol, records=_FUNDING_RECORDS, timeout_seconds=10)
            status["funding"] = "ok"
        except (ToolError, ToolBlocked):
            snapshot["funding"] = []  # series semantics: indeterminate, never constant
            status["funding"] = "degraded"
            reason_codes.append(FUNDING_DEGRADED)
    else:
        status["funding"] = "absent"

    # Derivative PRICE series (mark, index, premium index). Same collector, same grant, same
    # time grid as the candles — so the depth is not a tunable constant like funding's records
    # or liquidations' days: it is exactly the candle count. Deriving it that way is what keeps
    # the factory's 12,000-bar replay and the live cycle's short window on ONE code path, so a
    # premium_* family cannot be scored against a depth the router will not reproduce.
    bars = len(snapshot.get("candles") or [])
    timeframe = str(snapshot.get("timeframe") or "")
    # A grid to join onto is a precondition, not an error. These series are requested AT the
    # candle interval and matched on the candle open times, so with no candles or no known
    # interval there is nothing to request them against — the keys stay absent, the columns
    # stay None, and a spec reading them does not trade. That is the same outcome as a
    # collector without the capability, which is why it reports the same status; the case
    # arises on the degraded-collection path, where the cycle was not going to trade anyway.
    if hasattr(collector, "derivative_price_klines") and bars and timeframe in TIMEFRAMES:
        for kind, key, code in (
            ("mark", "mark_prices", MARK_PRICE_DEGRADED),
            ("index", "index_prices", INDEX_PRICE_DEGRADED),
            ("premium", "premium_index", PREMIUM_INDEX_DEGRADED),
        ):
            try:
                snapshot[key] = collector.derivative_price_klines(
                    symbol, timeframe, kind=kind, limit=max(1, bars), timeout_seconds=10
                )
                status[key] = "ok"
            except (ToolError, ToolBlocked):
                # Series semantics, as with funding: key PRESENT and empty, so the columns are
                # indeterminate rather than falling back to the pre-C13 fabricated constants.
                snapshot[key] = []
                status[key] = "degraded"
                reason_codes.append(code)
    else:
        status["mark_prices"] = status["index_prices"] = status["premium_index"] = "absent"

    # Positioning ratios, accumulated into a store the runtime retains itself. Like `oi_store`
    # above, this feeds NOTHING — `snapshot` is untouched, so the features, the backtest and the
    # live router are byte-identical to what they were without it. The reason it runs anyway is
    # that the vendor keeps 30 days and the factory replays 500, so a day not recorded today can
    # never be recovered; wiring a feature to it now would mint families over a window that is
    # 94% indeterminate. Accumulate now, decide later — `positioning_store.coverage` reports
    # progress and the flip stays an explicit change. Throttled to one request per series per
    # symbol per hour inside the store, so twenty contexts do not become sixty requests.
    if accumulate:
        positioning = positioning_store.record_positioning(
            symbol=symbol, collector=collector, now=now, root=root,
        )
        status["positioning"] = str(positioning["status"])
        # The resting book, same flag and same reason, one difference: this vendor keeps no
        # history at all, so the accumulation is not merely ahead of the feature that will read
        # it — it is the only copy that will ever exist. `accumulate_orderbook_cohort` is what
        # covers the fan-out; this covers the operator's single-symbol cycle, which has one
        # context and no sweep. The overlap costs nothing — the store's period throttle answers
        # the second asker `skipped_fresh` without opening a socket.
        orderbook = orderbook_store.record_orderbook(
            symbol=symbol, collector=collector, now=now, root=root,
        )
        status["orderbook"] = str(orderbook["status"])
    else:
        status["positioning"] = "not_accumulating"
        status["orderbook"] = "not_accumulating"

    if liquidation_feed is not None and getattr(liquidation_feed, "feed_id", "none") != "none":
        try:
            snapshot["liquidations"] = liquidation_feed.liquidation_history(
                symbol, days=_LIQUIDATION_DAYS, timeout_seconds=10
            )
            status["liquidations"] = "ok"
        except (ToolError, ToolBlocked):
            snapshot["liquidations"] = []
            status["liquidations"] = "degraded"
            reason_codes.append(LIQUIDATION_DEGRADED)
        # Open interest rides the SAME feed object, provider and grant — one
        # authorization, one egress chokepoint. Its own key and reason code so a
        # partial outage is legible: liquidations can be fine while OI is not.
        try:
            snapshot["open_interest"] = liquidation_feed.open_interest_history(
                symbol, days=_LIQUIDATION_DAYS, timeout_seconds=10
            )
            status["open_interest"] = "ok"
        except (ToolError, ToolBlocked):
            snapshot["open_interest"] = []
            status["open_interest"] = "degraded"
            reason_codes.append(OPEN_INTEREST_DEGRADED)
        # The hourly series, accumulated into a store the runtime retains itself. It feeds
        # NOTHING here — `snapshot` is untouched, so the features, the backtest and the live
        # router all keep reading the daily series above and stay identical to each other.
        # What this writes is future depth: the vendor keeps ~84 days of hourly history, the
        # factory replays 500, and the only way past a retention window is to stop depending
        # on it. Throttled to one vendor request per symbol per hour inside the store, so the
        # twenty contexts of a pool fan-out do not become twenty requests.
        oi_1h = oi_store.record_intraday_oi(
            symbol=symbol, feed=liquidation_feed, now=now, root=root,
        )
        status["open_interest_1h"] = str(oi_1h["status"])
    else:
        status["liquidations"] = "absent"
        status["open_interest"] = "absent"
    return reason_codes, status


def attach_htf(
    snapshot: dict[str, Any],
    *,
    collector: MarketDataCollector,
    now: str,
    limit: int | None = None,
) -> str | None:
    """Fetch the higher-timeframe candles onto ``snapshot`` (mutating it). Degrade-only.

    One step up ``market_data.HIGHER_TIMEFRAME``; the top of the ladder has none, and
    a fetch failure leaves the key ABSENT rather than empty. Both cases mean the same
    honest thing downstream — the HTF columns stay indeterminate, so an htf_* spec
    matches nothing and simply does not trade this cycle. Returns a reason code when
    the fetch degraded, else None.

    Deliberately never raises: the HTF leg is a *filter*, and a filter that cannot be
    read must not take down the cycle that would have traded without it."""
    symbol = str(snapshot.get("symbol") or "")
    higher = HIGHER_TIMEFRAME.get(str(snapshot.get("timeframe") or ""))
    if higher is None:
        return None
    # Enough higher bars for the indicators to warm up (MIN_WARM_CANDLES) with room
    # to spare; the alignment only ever reads the last closed one per lower bar.
    want = limit if limit is not None else 240
    try:
        htf_snapshot, _ = collect_market_data(symbol, higher, collector=collector, now=now, limit=want)
    except (ToolError, ToolBlocked):
        return HTF_DEGRADED
    candles = htf_snapshot.get("candles") or []
    if not candles:
        return HTF_DEGRADED
    snapshot["htf_candles"] = candles
    snapshot["htf_timeframe"] = higher
    return None


def attach_reference(
    snapshot: dict[str, Any],
    *,
    collector: MarketDataCollector,
    now: str,
    limit: int | None = None,
    cache: Any | None = None,
) -> str | None:
    """Fetch the market-proxy candles onto ``snapshot`` (mutating it). Degrade-only.

    One extra candle read per cycle, at THIS frame's own timeframe, so the reference series
    lands on the same grid the features join it on. Skipped entirely when the cycle's symbol
    IS the proxy: relative strength against oneself is undefined, and fetching a series only
    to compute a column of zeros would spend a request to manufacture a constant.

    Never raises, for the ``attach_htf`` reason: the reference leg is context, and context
    that cannot be read must not take down a cycle that would have traded without it. A
    failed fetch leaves the key ABSENT, so every ``ref_*`` column is indeterminate and a
    relative-strength spec does not trade this cycle. Returns a reason code on degrade.

    ``cache`` is a :class:`~.market_data.PeerCandleCache` for one fan-out. Without it
    every context fetches the proxy again, and since the proxy is a constant that is sixteen
    reads for four distinct series across a 5×4 fan-out — the redundancy
    :class:`~.market_data.PerSymbolFeedCache` exists to stop, arriving by another door.
    """
    symbol = str(snapshot.get("symbol") or "")
    timeframe = str(snapshot.get("timeframe") or "")
    if not symbol or timeframe not in TIMEFRAMES or symbol == REFERENCE_SYMBOL:
        return None
    # The correlation window is the deepest reference consumer (REFERENCE_CORR_WINDOW bars),
    # so the live default covers it with room to spare; the factory passes its replay depth.
    want = limit if limit is not None else 240
    try:
        if cache is not None:
            candles = cache.candles(timeframe, limit=want, now=now)
        else:
            reference, _ = collect_market_data(
                REFERENCE_SYMBOL, timeframe, collector=collector, now=now, limit=want
            )
            candles = reference.get("candles") or []
    except (ToolError, ToolBlocked):
        return REFERENCE_DEGRADED
    if not candles:
        return REFERENCE_DEGRADED
    snapshot["reference_candles"] = candles
    snapshot["reference_symbol"] = REFERENCE_SYMBOL
    return None


def attach_cross_section(
    snapshot: dict[str, Any],
    *,
    collector: MarketDataCollector,
    now: str,
    limit: int | None = None,
    cache: Any | None = None,
) -> str | None:
    """Fetch the cohort's candles onto ``snapshot`` (mutating it). Degrade-only.

    The peers of :data:`~.market_data.CROSS_SECTION_UNIVERSE` minus the traded symbol, each at
    THIS frame's own timeframe so the features can join them on bar open time. The traded
    symbol needs no fetch — its own momentum is already in the row, and it is the thing being
    ranked.

    **Degradation is per peer, not all-or-nothing**, which is the one place this differs in
    posture from :func:`attach_reference`. There, one series either arrived or the whole leg
    was indeterminate; here five peers arriving out of six is a perfectly usable cohort, and
    refusing to rank because the sixth timed out would throw away a measurement the runtime
    has. So a failed peer is simply absent from the cohort, ``features.xs_members`` records
    how many answered, and :data:`~.features.MIN_CROSS_SECTION_MEMBERS` is the floor below
    which no rank is reported at all. The reason code fires when **any** peer failed, so a
    thinned cohort is visible in the record rather than only in a column nobody reads.

    Never raises, for the :func:`attach_htf` reason. With no peer answering, the key stays
    ABSENT, every ``xs_*`` column is None, and an ``xs_*`` spec does not trade this cycle.

    ``cache`` is a :class:`~.market_data.PeerCandleCache` for one fan-out, and here it is
    doing considerably more work than for the reference leg: six members × four timeframes ×
    five contexts is 120 asks for 24 answers. Without one this leg would be the largest source
    of redundant vendor reads in the runtime.
    """
    symbol = str(snapshot.get("symbol") or "")
    timeframe = str(snapshot.get("timeframe") or "")
    if not symbol or timeframe not in TIMEFRAMES:
        return None
    peers = [member for member in CROSS_SECTION_UNIVERSE if member != symbol]
    if not peers:
        return None
    # The SAME default as `attach_reference`, and that is load-bearing rather than tidy: both
    # legs read peer candles through one cache keyed on (symbol, timeframe, depth), so a
    # different default here would make the proxy's series a cache MISS between the two legs —
    # two reads of one answer, which is the redundancy the cache exists to remove. The deepest
    # consumer is the dispersion reference (XS_DISPERSION_WINDOW bars), and 240 covers it with
    # the same room to spare the reference correlation gets.
    want = limit if limit is not None else 240
    collected: dict[str, list[dict[str, Any]]] = {}
    degraded = False
    for peer in peers:
        try:
            if cache is not None:
                candles = cache.candles(timeframe, limit=want, now=now, symbol=peer)
            else:
                peer_snapshot, _ = collect_market_data(
                    peer, timeframe, collector=collector, now=now, limit=want
                )
                candles = peer_snapshot.get("candles") or []
        except (ToolError, ToolBlocked):
            degraded = True
            continue
        if candles:
            collected[peer] = list(candles)
        else:
            degraded = True
    if collected:
        snapshot["peer_candles"] = collected
        snapshot["cross_section_universe"] = list(CROSS_SECTION_UNIVERSE)
    return CROSS_SECTION_DEGRADED if degraded else None


def attach_positioning(
    snapshot: dict[str, Any], *, root: Path | None = None,
    pre_read: list[dict[str, Any]] | None = None,
) -> None:
    """Put the accumulated positioning readings on ``snapshot`` (mutating it). Never raises.

    ``pre_read`` is this symbol's slice of one fan-out-level ``read_rows_grouped`` parse —
    the ``PeerCandleCache`` shape for a store instead of a vendor. ``None`` means "no
    hand-down" and reads the store as before; a handed-down EMPTY list is an answer, not an
    absence, so it is used as-is. The caller owns freshness: a context whose own feed step
    just appended must hand ``None`` (see ``cycle.run_crypto_cycle``).

    A LOCAL read, unlike every other attach in this module: the rows come from the store this
    runtime has been filling since `positioning_store` shipped, not from a vendor. So there is no
    request, no grant, and no degrade code — `positioning_store.read_rows` answers with less
    rather than refusing (damaged lines are skipped), which is the posture that module chose for
    exactly this consumer.

    Reads only THIS symbol's rows. The store holds every traded symbol, and a frame enriched with
    another symbol's positioning would be silently wrong rather than empty.

    No rows (the ordinary case until coverage accumulates) leaves the key ABSENT, so every
    ``positioning_*`` column is None and a spec reading one does not trade — which is why
    :data:`~.factory.POSITIONING_FAMILIES` are not minted until
    :func:`positioning_store.coverage_summary` says the window is covered. Attaching is safe
    before that; MINTING against it is not.
    """
    symbol = str(snapshot.get("symbol") or "")
    if not symbol:
        return
    rows = pre_read if pre_read is not None else positioning_store.read_rows(root, symbol=symbol)
    if rows:
        snapshot["positioning"] = rows
