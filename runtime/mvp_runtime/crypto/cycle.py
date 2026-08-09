"""C7 cycle orchestration — one governed pass of the five ported stages.

The Dynamic-Task-Team shape the contract promised: data (C2) → research features
(C3) → validation guards (C4) → paper update (C5) → feedback (C6), as one function
whose sub-records ride back to the caller for the ledger. Fail-closed where the
contract says BLOCK, degraded where it says DEGRADE:

**LP5.3 step 3 added a live leg (C5b), and it reaches the venue through exactly one module:**
``crypto/live_route``. That indirection is not layering for its own sake — it is what keeps
"which code can start a live order" a question with a single answer, and a test pins it. The
leg is inert on any machine that has not set ``MVP_LIVE_TRADING=real``: it reads no account and
opens no socket, and this cycle behaves exactly as it did before the wiring existed.

- A backend failure at collection **degrades** the cycle (``MARKET_DATA_DEGRADED``
  recorded; empty snapshot fails the health guard → no-new-position) — never blocks.
  A *configuration* failure (bad symbol/timeframe) still raises: that is a broken
  schedule, not a broken exchange.
- An unreadable outcome history or a tampered strategy pool refuses to trade
  (fail-closed verdict / no routing) while the cycle still completes and reports.
- The kill switch binds inside ``run_paper_update`` (C5); a PAUSED/KILLED runtime
  refuses the paper step and the cycle surfaces that refusal in its record.
- Feedback runs every cycle (the source rule) — a no-trade cycle still learns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.read_only_kernel import integrity

from ..control import ControlStore
from ..errors import MvpRuntimeError, ToolBlocked, ToolError
from . import feedback, oi_store, pool, positioning_store
from .features import latest_feature_row
from .guards import (
    RISK_LIMITS_UNUSABLE_PROBLEM,
    merge_trade_verdict,
    paper_trade_verdict,
    risk_guard_unavailable,
    risk_guard_unreadable,
    run_data_health_check,
    run_risk_guard,
)
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
    MARKET_DATA_DEGRADED,
    REFERENCE_DEGRADED,
    REFERENCE_SYMBOL,
    TIMEFRAMES,
    MarketDataCollector,
    PeerCandleCache,
    PerSymbolFeedCache,
    collect_market_data,
    degraded_market_data_record,
)
from .counterfactual import run_counterfactual_update
from .lifecycle import run_lifecycle, split_for_record as lifecycle_split
from .live_allowance import evaluate_live_allowance
from .live_pnl import live_outcomes_for_analysis, read_live_outcomes
from .live_route import (
    DEFAULT_TIMING_CONTEXT,
    ROUTE_DISABLED,
    live_position_contexts,
    live_route_status_line,
    run_live_leg,
)
from .paper import (
    ENTRY_COST_UNECONOMIC,
    PaperStore,
    build_entry_plan,
    list_open_positions,
    read_outcomes,
    run_paper_update,
)
from .risk_limits import resolve_risk_limits

CYCLE_VERSION = "crypto_cycle.v0.1"
POOL_CYCLE_VERSION = "crypto_pool_cycle.v0.1"

# A PAUSED/KILLED runtime refuses every context identically, so a kill refusal from
# one sub-cycle stops the whole fan-out; any other refusal is that context's alone.
_KILL_CODES = frozenset({"RUNTIME_KILLED", "RUNTIME_PAUSED"})

# Collection failures that degrade the cycle; anything else is a config error.
_DEGRADABLE_CODES = {"TOOL_ERROR"}

# The higher-timeframe leg could not be read this cycle; htf_* specs stay no-entry.
HTF_DEGRADED = "HTF_DEGRADED"

# At least one live outcome could not be given an honest R, so the R-based guard did not read
# it. Surfaced rather than silent: the money is still in the daily-loss breaker, but a row the
# streak logic never saw is something an operator should know about.
LIVE_OUTCOMES_EXCLUDED = "LIVE_OUTCOMES_EXCLUDED_FROM_RISK_GUARD"
# #615 §5 — at least one live-armed lineage spent its allowance this cycle and left the live
# tier. Not a failure and not a verdict on the strategy: the amount we were willing to risk on
# an unproven lineage is gone, and it keeps papering.
LIVE_ALLOWANCE_SPENT = "LIVE_ALLOWANCE_SPENT"

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
    decided.** It was, and what that cost is measured in :func:`accumulate_positioning_cohort`:
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
    else:
        status["positioning"] = "not_accumulating"

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


def attach_positioning(snapshot: dict[str, Any], *, root: Path | None = None) -> None:
    """Put the accumulated positioning readings on ``snapshot`` (mutating it). Never raises.

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
    rows = positioning_store.read_rows(root, symbol=symbol)
    if rows:
        snapshot["positioning"] = rows


def attach_mining_legs(
    snapshot: dict[str, Any],
    *,
    collector: MarketDataCollector,
    timeframe: str,
    now: str,
    root: Path | None = None,
    liquidation_feed: Any | None = None,
    candle_target: Any = None,
) -> None:
    """Every leg **the factory mines on**, in one place. Mutating, degrade-only, never raises.

    The five attaches above are individually correct and were individually copied. That is the
    defect this function exists to end rather than a tidiness preference: a caller that assembles
    four of the five gets a frame where the fifth family's columns are None down the whole
    window, and the failure is silent in the worst way — the spec does not error, it scores as a
    no-trade spec and is judged for it. The same mistake has now been made twice on two different
    call sites (the null control, and the family proposer), both times by writing `attach_feeds`
    and stopping, and both times invisible until somebody counted the None columns.

    So the rule is stated once here: **anything that BACKTESTS or REPLAYS a spec must build its
    frame with this function.** ``run_crypto_cycle`` deliberately does not — the live cycle
    attaches its own legs with ``accumulate=True`` and its own live-depth limits, because it is
    routing rather than mining.

    ``candle_target`` is ``market_data.factory_candle_target`` passed in rather than imported,
    so this module keeps its current import surface and a caller mining at a different depth
    stays able to say so. ``None`` means "live defaults", which is what a caller with no replay
    span wants.

    Every leg degrades rather than raises: a leg that cannot be read leaves its columns None,
    which is the state a caller who never called it would have had anyway.

    **Why each leg is here** — carried over from the factory block these lines were extracted
    from, because it is the reasoning a future caller needs in order not to drop one:

    - ``attach_feeds`` (C9): the factory backtests on the same feed-enriched frame the router
      evaluates. One feature source for backtest and live — the source rule.
    - ``attach_htf``: mining ``htf_*`` families over a frame with no higher timeframe scores
      every one of them as a no-trade spec. The window must cover the replay span, so the
      depth is the HIGHER timeframe's own target rather than this one's.
    - ``attach_reference``: same rule for the cross-asset leg — a ``rel_strength_*`` family
      mined over a frame with no reference series scores as a no-trade spec. Same timeframe as
      the frame being mined, so the same depth.
    - ``attach_cross_section``: same again, and **the most expensive of the four** — the cohort
      is five peers at the replay span, so it pages roughly five times what the frame itself
      did. Paid on the factory's own schedule rather than the 15-minute one. The alternative is
      scoring ``xs_*`` families over a frame where every rank is None, which does not produce a
      cheap verdict, it produces a WRONG one (no trades, FRAGILE, retired).
    - ``attach_positioning``: a LOCAL read of what this runtime has accumulated — no request,
      no grant. Attached unconditionally because the columns are honest at any coverage
      (absent = None); whether a positioning family may be MINTED against them is the separate
      ``positioning_store.coverage_summary`` question, which the CALLER measures and passes to
      ``run_factory``. This function does not answer it and must not be read as doing so.
    """
    attach_feeds(snapshot, collector=collector, liquidation_feed=liquidation_feed,
                 now=now, root=root, accumulate=False)
    depth = candle_target(timeframe) if candle_target is not None else None
    higher = HIGHER_TIMEFRAME.get(timeframe)
    attach_htf(snapshot, collector=collector, now=now,
               limit=candle_target(higher) if (candle_target is not None and higher) else None)
    attach_reference(snapshot, collector=collector, now=now, limit=depth)
    attach_cross_section(snapshot, collector=collector, now=now, limit=depth)
    attach_positioning(snapshot, root=root)


def run_crypto_cycle(
    *,
    collector: MarketDataCollector,
    store: PaperStore,
    now: str,
    symbol: str = "BTCUSDT",
    timeframe: str = "1d",
    limit: int = 120,
    root: Path | None = None,
    control_store: ControlStore | None = None,
    liquidation_feed: Any | None = None,
    routing_marks: Any | None = None,
    candle_cache: Any | None = None,
) -> dict[str, Any]:
    """Run one full crypto cycle. Returns the cycle record (sub-records included).

    Raises only on configuration errors (invalid symbol/timeframe) and on the
    kill-switch refusal from the paper step — both are caller decisions, not
    market conditions."""
    reason_codes: list[str] = []

    # 1) data (C2) — degrade on backend failure, never block the cycle.
    try:
        snapshot, collection_record = collect_market_data(
            symbol, timeframe, collector=collector, now=now, limit=limit
        )
    except ToolBlocked as exc:
        if exc.reason_code not in _DEGRADABLE_CODES:
            raise
        collection_record = degraded_market_data_record(collector, symbol, timeframe, MARKET_DATA_DEGRADED, now=now)
        snapshot = {
            "snapshot_version": "0.1", "symbol": symbol, "timeframe": timeframe,
            "candles": [], "candle_count": 0, "last_close": None, "last_candle_time": None,
            "source": collection_record["source"],
            # Read off the record rather than defaulted: a degraded collection still knows
            # which venue it failed to reach, and a stub that quietly said binance would put
            # the wrong venue on anything mined from the cycle that carried it.
            "venue": collection_record["venue"], "is_synthetic": False,
            "degraded": True, "created_at": now,
        }
        reason_codes.append(MARKET_DATA_DEGRADED)

    # 1b) derivative feeds (C9) — enrichment; degrade-only, never block.
    feed_reasons, feed_status = attach_feeds(
        snapshot, collector=collector, liquidation_feed=liquidation_feed, now=now, root=root,
        # The live cycle is the accumulator: it runs on the 15-minute cadence the positioning
        # store's hourly throttle is sized for, and it is the one path that always has a root.
        accumulate=True,
    )
    reason_codes.extend(feed_reasons)

    # 1c) higher-timeframe context — the regime leg htf_* specs filter on. Degrade-only.
    htf_reason = attach_htf(snapshot, collector=collector, now=now)
    if htf_reason:
        reason_codes.append(htf_reason)

    # 1d) cross-asset context — the market proxy rel_strength_* specs measure against.
    # Degrade-only, and a no-op when this cycle's symbol IS the proxy.
    reference_reason = attach_reference(
        snapshot, collector=collector, now=now, cache=candle_cache
    )
    if reference_reason:
        reason_codes.append(reference_reason)

    # 1e) cross-sectional context — the cohort xs_* specs rank this symbol within. Degrade-only
    # and PER PEER: five of six members arriving is a usable cohort, so the leg thins rather
    # than failing. Same cache as the reference leg above, because both read other symbols'
    # candles and the answers do not depend on which leg asked.
    cross_section_reason = attach_cross_section(
        snapshot, collector=collector, now=now, cache=candle_cache
    )
    if cross_section_reason:
        reason_codes.append(cross_section_reason)

    # 1f) positioning — the store's own accumulated readings. A local read, so no request and no
    # degrade code. The ROUTER must see the same columns the backtest scored, which is the whole
    # reason this is here and not only on the factory path.
    attach_positioning(snapshot, root=root)

    # 2) research features (C3).
    feature_row = latest_feature_row(snapshot)

    # 3) validation guards (C4) — stricter-wins; unreadable history fails closed.
    health = run_data_health_check(snapshot, now=now, timeframe_minutes=TIMEFRAMES[timeframe])
    outcomes: list[dict[str, Any]] | None = None

    # Strategy pool: tampered/unreadable = do not route (trade nothing), still cycle.
    #
    # Read HERE, above the guard, rather than after the verdict where it used to sit. The
    # drawdown baseline's re-check needs the routable set (`guards.drawdown_baseline`), and the
    # ordering carries the whole fail-closed property: `routable_ids` is None when the pool could
    # not be read, which is deliberately NOT the empty set the degraded `active_pool` produces.
    # Empty would say "every retired lineage is confirmed retired" and release the entire
    # exclusion; None says "I cannot tell" and keeps every loss in the window. The one failure
    # that could clear a breaker is the one that must not.
    #
    # `live_routable_ids` takes the same None-on-failure shape for the mirror-image reason
    # (#610 Part 1). Both the empty set and None refuse every live entry, so the money path is
    # safe either way — but they are different operator problems, and the reason code has to
    # say which: "no strategy is armed for live" is the expected state after this shipped,
    # while "the pool could not be read" is a fault whose fix is somewhere else entirely.
    routable_ids: set[str] | None
    live_routable_ids: set[str] | None
    # #615 §5. Read alongside the breaker's own read below; `readable` stays False on any path
    # that did not produce a trustworthy history, and the allowance treats that as a breach.
    live_readable: list[dict[str, Any]] = []
    live_history_readable = False
    try:
        active_pool = pool.load_active_pool(root)
        routable_ids = pool.routable_strategy_ids(active_pool)
        live_routable_ids = pool.live_routable_strategy_ids(active_pool)
    except ToolError as exc:
        active_pool = {"active_strategies": []}
        routable_ids = None
        live_routable_ids = None
        reason_codes.append(exc.reason_code)

    # The breaker limits themselves: the registered per-machine record when one is registered
    # and current, the `guards` defaults otherwise. A record that cannot be used — tampered,
    # unparseable, outside the code bounds, or past its validity window — fails the guard closed
    # rather than falling back to the defaults. The fallback is the tempting branch and the wrong
    # one: an operator who *tightened* a breaker would have it silently loosened back to the
    # default by the very failure that was supposed to be conservative.
    try:
        risk_limits = resolve_risk_limits(root, now=now)
    except ToolError as exc:
        risk_limits = None
        reason_codes.append(exc.reason_code)
        risk = risk_guard_unavailable(RISK_LIMITS_UNUSABLE_PROBLEM, f"{exc.reason_code}: {exc}", now=now)

    # Guarded rather than folded into the try below: with no usable limits there is nothing to
    # judge the history against, so reading it would produce numbers no breaker can rule on.
    # The paper history. Still read here and still handed to the feedback report,
    # `run_lifecycle` and the counterfactual book below — but NO LONGER to the loss breaker.
    # `outcomes` stays None when this raises, which is what makes the report re-read and raise
    # the same way, and Gate 0 refuse on the absence.
    try:
        outcomes = read_outcomes(root)
    except ToolError as exc:
        reason_codes.append(exc.reason_code)

    if risk_limits is not None:
        try:
            # **The loss breakers judge LIVE outcomes, and only live outcomes.**
            #
            # They used to judge `own_outcomes + live_readable` — this runtime's paper book plus
            # its live one — and with live having traded nothing, that meant a real-money door was
            # opened and closed entirely by a simulation. Measured 2026-07-31: the breaker read
            # `weekly -19.35R` and `drawdown -44.79R` off **86 paper rows and 0 live ones**, and
            # 757 cycles were HELD on it while the router had a live entry candidate. A loss limit
            # exists to stop money being lost; not one of those rows lost any.
            #
            # `paper_trade_verdict` took the paper leg off this guard because a loss is evidence
            # there rather than damage. This takes the paper *evidence* off the live leg for the
            # mirror-image reason: it is evidence about a different book, at different size, with
            # no cost of being wrong — and a brake on real money has to answer "is the money
            # losing right now", which paper cannot answer at any sample size. Each leg now meters
            # what it actually risks.
            #
            # Routed through LP5.4's bridge rather than read raw: `guards._closed_rows` reads a
            # missing `result_R` as 0.0, i.e. a BREAKEVEN, so an R-less live loss would SHORTEN a
            # loss streak. The bridge drops those rows; they stay visible to the venue-sourced
            # daily-loss breaker in `live_route`, which needs no R. An unreadable or tampered LIVE
            # history still fails this guard closed — a history that cannot prove itself must not
            # be allowed to argue the breaker is clear.
            #
            # **What now covers an unreadable PAPER store**, which used to fail this guard closed:
            # Gate 0. `run_paper_performance_report` re-reads and raises, `report` is None, and
            # `plan_live_entry` refuses a non-Mapping `live_candidate`. The live door still fails
            # closed on that store — through the check that actually reads it, rather than through
            # a breaker that no longer does.
            live_readable, live_excluded = live_outcomes_for_analysis(read_live_outcomes(root))
            live_history_readable = True
            if live_excluded:
                reason_codes.append(LIVE_OUTCOMES_EXCLUDED)
            risk = run_risk_guard(
                live_readable, now=now, limits=risk_limits,
                routable_strategy_ids=routable_ids,
            )
        except ToolError as exc:
            risk = risk_guard_unreadable(f"{exc.reason_code}: {exc}", now=now)
            reason_codes.append(exc.reason_code)
    # TWO verdicts, because the two legs are metering different things. `live_verdict` is the
    # merge that has always gated real money — unchanged, so a ledger row from before this split
    # means what a live-gating row means now. `paper_verdict` is data health alone: paper loses
    # no money, and the loss breakers it used to answer to were suppressing the very sample the
    # lifecycle ladder needs to demote the strategies that tripped them (see
    # `guards.paper_trade_verdict` for the measurement).
    #
    # Both are computed every cycle even when only one is consulted, so the record can say what
    # the other one would have decided. That is what keeps an unbraked paper book still able to
    # describe a braked live one.
    live_verdict = merge_trade_verdict(health, risk)
    paper_verdict = paper_trade_verdict(health)

    # 4) paper update (C5) — kill-switch bound inside; refusals propagate.
    # The same gated collector resolves an ambiguous exit at 1m — a refinement, so a
    # failure degrades the settlement to its pessimistic assumption, never blocks it.
    paper_summary, paper_records = run_paper_update(
        snapshot, feature_row, active_pool, paper_verdict,
        store=store, now=now, root=root, control_store=control_store,
        intrabar_collector=collector, routing_marks=routing_marks,
    )
    if paper_summary.get("settle_refused"):
        reason_codes.append(paper_summary["settle_refused"]["reason_code"])
    if paper_summary.get("settle_recovered"):
        reason_codes.append(paper_summary["settle_recovered"]["reason_code"])
    if paper_summary.get("intrabar_degraded"):
        reason_codes.append(paper_summary["intrabar_degraded"]["reason_code"])

    # 4b) counterfactuals (C11) — purely observational: settle every open shadow
    # with the same exit math, and when the guards refused an actionable signal
    # THIS cycle, shadow the plan the router would have taken (tagged with the
    # refusing reasons). Persisted only through the real gated store.
    # The route is the paper step's own evaluation, reused. It used to be computed a second
    # time here with identical arguments — two evaluations of the same strategies against the
    # same feature row, and therefore two chances to disagree about what the pool said this
    # cycle. LP5.3 adds a third consumer (the live leg), which is what made sharing worth doing
    # rather than merely tidy. `None` when the paper step returned before routing (a settlement
    # race), and the fallback is the honest one: no shadow rather than a re-derived route.
    shared_route = paper_summary.get("route")
    # The economics gate refuses inside the paper step, where the guard verdict allowed the
    # entry — so the condition below has to name it, or the one refusal whose calibration is
    # genuinely unknown would be the one refusal nothing shadows. It is the only `open_refused`
    # reason added here: the concurrency caps refuse because a slot is taken, and shadowing
    # those would fill the book with plans the runtime had no room for either way.
    cost_refused = (paper_summary.get("open_refused") or {}).get("reason_code") == ENTRY_COST_UNECONOMIC
    # The PAPER verdict, because this registry exists to price refusals that stopped a PAPER
    # trade — a shadow is what stands in for an outcome that never happened, and after the
    # verdict split the loss breakers no longer stop one. Those cycles now produce a real
    # settled row instead, which is strictly better evidence than the simulation of it. What
    # still lands here is what still refuses paper: data health, the economics gate, the caps.
    block_reasons = list(paper_verdict.get("problems") or [])
    if cost_refused:
        block_reasons.append(ENTRY_COST_UNECONOMIC)
    blocked_plan = None
    # The ``opened is None`` clause is **redundant by construction and kept deliberately**: a
    # cycle that opened had an allowing verdict (so the first branch is false) and no refusal (so
    # the second is), which is why removing it alone changes no behaviour and no test. It is here
    # because the invariant it states — a trade that HAPPENED is never also shadowed, or it would
    # be double-counted into every per-reason bucket — is the property a future third branch
    # would break silently. Do not "simplify" the inner guards on the strength of this one.
    if shared_route and paper_summary.get("opened") is None:
        # A cost refusal joins the guard block rather than forming a third branch: both are
        # "the router had a candidate and something upstream of the caps refused it", and
        # `block_reasons` already carries ENTRY_COST_UNECONOMIC from above. It also keeps the
        # branches mutually exclusive — `run_paper_update` refuses on cost BEFORE it reaches
        # the position caps, so a cost-refused cycle can never also carry `open_refused`.
        if not bool(paper_verdict.get("allow_new_position")) or cost_refused:
            blocked_plan = build_entry_plan(shared_route, feature_row, now=now)
        else:
            # A POSITION CAP refused a plan the router had already built — the portfolio count,
            # the per-symbol count, or the directional lean. Shadowed for the same reason a
            # guard block is: a refusal nobody can price is a refusal nobody can tune. These
            # three have existed without that, so "what did the caps cost" has only ever been
            # answerable by simulation — including in the PR that added the directional one,
            # whose benefit numbers came from mock candles rather than from this machine.
            #
            # Mutually exclusive with the branch above by construction: `run_paper_update` only
            # reaches its cap checks when the verdict allows a new position, so a refusal cannot
            # coexist with a guard block. And `open_refused` is set only INSIDE the freshness
            # gate, so this opens one shadow per refused candle rather than one per tick — the
            # guard-blocked branch above has no such property, because a tripped breaker
            # persists across every tick of a coarse timeframe.
            refusal = paper_summary.get("open_refused")
            if refusal:
                blocked_plan = build_entry_plan(shared_route, feature_row, now=now)
                block_reasons = [str(refusal["reason_code"])]
    candles_for_cf = snapshot.get("candles") or []
    counterfactual_summary = run_counterfactual_update(
        blocked_plan=blocked_plan,
        block_reasons=block_reasons,
        last_candle=candles_for_cf[-1] if candles_for_cf else None,
        last_close=(candles_for_cf[-1] or {}).get("close") if candles_for_cf else None,
        symbol=symbol,
        timeframe=timeframe,
        now=now,
        root=root,
        persist=bool(getattr(store, "filesystem_write", False)),
    )
    if counterfactual_summary.get("degraded"):
        reason_codes.append(counterfactual_summary["degraded"])

    # 5) feedback (C6) — every cycle, even a no-trade one. The report reads the
    # store as persisted: in dry-run it honestly reports the durable (empty) truth.
    # Handed the history this cycle already read and verified at step 3, rather than paying for
    # a second full parse + per-record hash of the same file. `outcomes` is None only when that
    # read RAISED, and passing None then is the point: the report re-reads, raises the same way,
    # and the except below records it — a report over a history nobody could verify is exactly
    # what must not be produced.
    #
    # It runs BEFORE the live leg, which is the one ordering change Gate 0 needed. It is a pure
    # read over the pre-settlement snapshot taken at step 3 — it does not look at what the paper
    # step just did, so moving it earlier cannot change what it says, and the alternative
    # (persisting this report and having the live leg read the PREVIOUS cycle's) would have added
    # a store to avoid a move that costs nothing. Nothing inside `run_paper_update`'s portfolio
    # lock is touched or re-ordered, which is the restructuring that was actually worth avoiding.
    try:
        report, report_text = feedback.run_paper_performance_report(
            now=now, root=root, outcomes=outcomes,
            # Gate 0 judges the pool that would actually trade. `routable_ids` is None when the
            # pool could not be read, which scopes the report to nothing and refuses — the
            # opposite direction to the drawdown baseline above, and right for the opposite
            # reason: an unverifiable population must withhold an eligibility claim, where it
            # must keep losses inside a brake.
            routable_strategy_ids=routable_ids,
        )
    except ToolError as exc:
        report, report_text = None, f"performance report unavailable: {exc.reason_code}"
        if exc.reason_code not in reason_codes:
            reason_codes.append(exc.reason_code)

    # Gate 0's runtime enforcement, and the operator acknowledgement that existed to override
    # it, were removed 2026-08-03. `report` is still computed and still reaches the ledger
    # below — the measurement was never the problem. What is gone is its authority over the
    # live door, which it could not exercise: the routable set is whichever batch was promoted
    # last, so the sample resets and the acknowledgement voids on the same event, and the gate's
    # only reachable state was the override. `docs/proposals/GATE0_CANNOT_BE_SATISFIED_V0.1.md`
    # has the measurement; `live_entry`'s docstring records the removal at the door itself.

    # 4c) the LIVE PERMISSION phase — the per-lineage allowance (#615 §5). It runs BEFORE the leg
    # below, and the ordering is the control: a lineage whose allowance is already spent must not
    # be able to open one more real position because the permission was recomputed too late.
    #
    # **What it judges is the history read at step 3**, which holds every outcome a PREVIOUS cycle
    # settled. An outcome settled by THIS cycle's leg is not in it and does not need to be:
    # `live_route` returns ROUTE_SETTLED before it reaches its entry block, so a cycle that closes
    # a position never opens one, and the next cycle re-reads that file at step 3 with the new row
    # in it. That short-circuit is what makes "settle, then re-read, then decide permission, then
    # enter" hold across two cycles without splitting this leg in half.
    #
    # This block used to sit AFTER the leg, on the argument that a settlement made this cycle
    # would then be visible to it. It was not: `live_readable` is read at step 3 either way, so
    # the placement bought nothing it claimed and cost the one thing that mattered — the leg had
    # already run, on the un-narrowed set, so a spent allowance still got one more live entry.
    #
    # It is not a verdict. At this sample size no test can say a strategy is bad; what this says
    # is that the amount we were willing to risk on an unproven lineage is spent. The effect is a
    # TIER move, so the lineage keeps its slot and keeps papering — and `disarm_live_tier` has no
    # argument for a target tier, so this can only ever take permission away.
    #
    # Additive to the pool-wide breaker, never a replacement (#615 §5.3): a targeted stop that
    # let the portfolio brake relax would leave more trading running than today, which is a
    # money-door widening and a separate approval.
    live_allowance: dict[str, Any] | None = None
    if live_routable_ids:
        live_allowance = evaluate_live_allowance(
            # None when the history could not be read, which reports every armed lineage as
            # breached — the conservative direction, and the one the docstring argues for.
            live_readable if live_history_readable else None,
            live_routable_strategy_ids=live_routable_ids,
            pool=active_pool,
        )
        breached = sorted({b["strategy_id"] for b in live_allowance["breached"]})
        if breached:
            # **The refusal is in memory, and it does not wait for the write.** Narrowing the set
            # handed to the leg is what actually stops the entry; persisting the tier move is a
            # separate, best-effort step below. A breach detected but not persistable — a dry-run
            # store, a locked pool, a raising writer — therefore still refuses this cycle. The
            # money path must not need the disk to hold a limit it has already measured as spent,
            # and "some other gate probably catches it" is not a property this door may rely on.
            live_routable_ids = {sid for sid in live_routable_ids if sid not in breached}
            live_allowance["blocked_from_live_this_cycle"] = breached
            reason_codes.append(LIVE_ALLOWANCE_SPENT)
            # `disarmed` is None when the durable move did not land: not attempted (dry-run
            # store), or attempted and refused. The two are told apart by the reason code a
            # refusal appends — the in-cycle block above is identical either way.
            live_allowance["disarmed"] = None
            if getattr(store, "filesystem_write", False):
                try:
                    live_allowance["disarmed"] = pool.disarm_live_tier(
                        breached, root=root, now=now,
                        reasons=sorted({r for b in live_allowance["breached"] for r in b["reasons"]}),
                    )
                except ToolError as exc:
                    reason_codes.append(exc.reason_code)

    # 4d) the live leg (LP5.3 step 3) — the one step that can move real money, behind the one
    # module that may. On a machine that has not opted in this returns DISABLED having read
    # nothing, so the whole branch costs one env check. It runs AFTER the paper
    # step so it can share that step's routing result rather than re-evaluating the pool.
    #
    # It is given `live_verdict`, which is the FULL merge including the loss breakers — so this
    # leg is metered exactly as it was before the split, and dropping the paper breaker widened
    # nothing here. The old comment on this line said a live entry can never be permitted where
    # a paper one was not, and that sentence held two separate guarantees:
    #
    #   - the SIGNAL ceiling — live only ever trades what the paper router proposed. That is
    #     `shared_route` above, and it is untouched.
    #   - the RISK-STATE ceiling — live inherited paper's breaker verdict. That is what moved:
    #     live now answers to its own, which is stricter than the paper leg's rather than equal
    #     to it, because `merge_trade_verdict` still folds in every breaker the paper leg drops.
    #
    # Gate 0 used to be handed in here as the door in front of the venue that the paper leg's
    # split no longer answered to. It is gone (2026-08-03) because it could not be satisfied,
    # and what stands in its place is not a second aggregate but the per-strategy ladder:
    # `routable_strategy_ids` is derived from lifecycle status, so a strategy the ladder has
    # SUSPENDED cannot route here at all, and the ladder judges each one on its OWN record, net
    # of costs, at any sample size. Beneath that the registered budget, the venue-sourced daily
    # loss breaker, the bracket breaker and both kill switches all still bind.
    #
    # Never raises: `run_live_leg` reports, because a traceback here would be indistinguishable
    # from "no live activity".
    live = run_live_leg(
        route=shared_route,
        # #610 Part 1 — read from the SAME pool object the ladder just ran on, so the live door
        # and the lifecycle cannot disagree about which strategies exist. Occupying a slot is no
        # longer the same fact as being allowed to spend money; `live_routable_strategy_ids` is
        # the narrower of the two and an entry has to say so explicitly to be in it.
        live_routable_strategy_ids=live_routable_ids,
        feature_row=feature_row,
        verdict=live_verdict,
        symbol=symbol,
        collector=collector,
        now=now,
        # This cycle's own context. Only the timeframe a position was opened at may advance its
        # holding counter — every other one still settles and protects it. See
        # `live_route.position_timing_context`.
        timeframe=timeframe,
        root=root,
        control_store=control_store,
    )
    reason_codes.extend(live["live_reason_codes"])

    # 5b) lifecycle (C10) — auto-demote decaying strategies, never auto-promote.
    # Evaluated every cycle (pure); APPLIED only through the real gated store, the
    # same effect discipline as every other paper mutation. An unreadable outcome
    # history skips evaluation (no honest windows to judge on).
    lifecycle_decisions: list[dict[str, Any]] = []
    lifecycle_applied = 0
    if outcomes is not None:
        lifecycle_decisions = run_lifecycle(active_pool, outcomes, now=now)
        changed = [d for d in lifecycle_decisions if d.get("status_changed")]
        if changed:
            reason_codes.append("LIFECYCLE_TRANSITION")
        if getattr(store, "filesystem_write", False) and lifecycle_decisions:
            try:
                lifecycle_applied = pool.update_statuses(lifecycle_decisions, root=root)
            except ToolError as exc:
                reason_codes.append(exc.reason_code)
        for decision in changed:
            report_text += (
                f"\nlifecycle: {decision['strategy_id']} "
                f"{decision['previous_status']} -> {decision['new_status']}"
                + (" (manual reactivation required)" if decision["requires_manual_reactivation"] else "")
            )

    # The full list stays in play for the runtime (update_statuses above already used it);
    # this governs only what the ledger keeps.
    lifecycle_noteworthy, lifecycle_unchanged = lifecycle_split(lifecycle_decisions)

    record = {
        "feeds": feed_status,
        "cycle_version": CYCLE_VERSION,
        "symbol": symbol,
        "timeframe": timeframe,
        "degraded": bool(snapshot.get("degraded", False)),
        "reason_codes": reason_codes,
        "collection": collection_record,
        # UNCHANGED MEANING, deliberately: this is the merged verdict that gates real money, so
        # a row written before the paper/live split says the same thing as one written after and
        # a window spanning that day mixes nothing. The paper leg's own verdict is a NEW field
        # below rather than a redefinition of this one — the alternative is the defect
        # `r_basis` exists to mark, one field name quietly holding two populations.
        "verdict_status": live_verdict["status"],
        "verdict_problems": live_verdict["problems"],
        # What the PAPER leg decided, which is what the `route` / `opened` / `open_refused`
        # fields on this same record are the consequence of. Recorded even when it agrees with
        # the line above, because "they agreed" is itself the fact a later reader needs: a cycle
        # where paper opened while the live gate refused is now an ordinary cycle, not an
        # inconsistency, and only having both figures makes that legible.
        "paper_verdict_status": paper_verdict["status"],
        "paper_verdict_problems": paper_verdict["problems"],
        # The breaker limits this cycle was judged against, and the record they came from. Kept
        # even though the rest of the verdict is not: the limits are configurable now, so
        # "ALLOW" no longer states what was allowed, and a ledger row nobody can re-check
        # against the numbers in force at the time is not an audit trail. ~150 bytes against
        # the 24KB record the lifecycle trim above was worth doing for.
        "risk_limits": live_verdict["risk_guard"].get("limits"),
        "route_status": paper_summary.get("route_status"),
        # Which strategies fired and were declined by the regime filter. The ids rather than the
        # whole route, because this record is deliberately trimmed (the lifecycle note below is
        # the same discipline) — but not merely a count, because "which one" is the actionable
        # part: a strategy excluded on every cycle for a week is a demotion candidate, and a
        # count cannot say that. Empty on almost every cycle, which is why it is a list and not
        # a status.
        "regime_excluded": list(
            (paper_summary.get("route") or {}).get("regime_excluded_strategy_ids") or []
        ),
        "settled": paper_summary.get("settled"),
        "opened": paper_summary.get("opened"),
        "open_skipped": paper_summary.get("open_skipped"),
        # A cap declined a plan the router had already built. Previously this reached the ledger
        # only inside `paper_records`' event stream and never the status line, so the two count
        # caps have been invisible to anyone reading a fire's output — which was tolerable while
        # they only fired at 20 positions, and is not now that a THIRD cap reads the book's
        # directional shape and can decline a half-full book. Same argument as
        # `regime_excluded`: a refusal an operator cannot see is a refusal they cannot act on.
        "open_refused": paper_summary.get("open_refused"),
        "paper_records": paper_records,
        # The live leg, reported distinctly from paper on purpose: a ledger where the two are
        # indistinguishable is one where nobody can answer "did this system trade real money
        # today?" without reading code. `live_halt` rides separately because `run_pool_cycle`
        # reads it to decide whether the rest of the fan-out may run at all.
        "live_route_status": live["live_route_status"],
        "live_opened": live["live_opened"],
        "live_settled": live["live_settled"],
        "live_reason_codes": live["live_reason_codes"],
        "live_halt": live["halt"],
        # Only decisions that DECIDED something are stored whole. A cycle evaluates every
        # active strategy and most conclude "nothing to do"; persisting all of those made
        # lifecycle_decisions 90% of a 24KB record and 99.7% of a 56MB ledger, for one bit
        # each ("evaluated"). That bit is kept — as an id in lifecycle_unchanged — and the
        # count below says how many were evaluated in total, so nothing is unaccounted for.
        "lifecycle_decisions": lifecycle_noteworthy,
        "lifecycle_unchanged": lifecycle_unchanged,
        # #615 §5. Present even when nothing breached, so a reader can tell "evaluated, nothing
        # spent" from "never evaluated" — the distinction `lifecycle_evaluated` exists for.
        "live_allowance": live_allowance,
        "lifecycle_evaluated": len(lifecycle_decisions),
        "lifecycle_applied": lifecycle_applied,
        "counterfactual": counterfactual_summary,
        "report_status": report.get("status") if report else None,
        # What the paper evidence says about this pool. Kept on the record after Gate 0's
        # enforcement was removed (2026-08-03), because the question is still worth answering
        # — "has this pool shown an edge net of costs" is exactly what an operator working the
        # go-live checklist needs. What changed is that nothing refuses on it. The companion
        # `live_candidate_ack` field is gone with the acknowledgement it reported.
        "live_candidate_eligible": report.get("live_candidate_eligible") if report else None,
        "report_text": report_text,
        "created_at": now,
    }
    record["cycle_id"] = integrity.short_id(
        "crypto_cycle", {"symbol": symbol, "timeframe": timeframe, "at": now}
    )
    return record


def pool_cycle_contexts(
    root: Path | None = None, *, default_timeframe: str = DEFAULT_TIMING_CONTEXT
) -> list[tuple[str, str]]:
    """Every ``(symbol, timeframe)`` one pool pass must visit, sorted.

    The union of three sets, because a cycle both *opens* and *settles*:

    - the pool's routable contexts (:func:`pool.routable_contexts`) — so every
      strategy is actually evaluated, not just the ones scoped to one default
      symbol;
    - the contexts of every currently OPEN paper position — so a position whose
      strategy has since been demoted out of the routable set is still visited by
      its own symbol's cycle and can settle, never stranded; and
    - the ``(symbol, timeframe)`` of every open **live** position (LP5.3). Same rule,
      higher stakes: a live position whose strategy has been demoted would otherwise
      have no cycle that could settle it, and it holds real money.

      The timeframe here is the position's **own** — the one its ``max_holding_bars``
      was backtested against — and it is added even when the symbol is already being
      visited at some other one. That is the counterpart to
      ``live_route.position_timing_context``: only the owning context may advance a
      position's holding counter, so the owning context has to be guaranteed to run.
      This used to pair the symbol with ``default_timeframe`` and only when it was
      otherwise unvisited, which meant a 4h position on a symbol still routed at 15m
      was serviced by a cycle counting 15m bars.

    A tampered/unreadable pool or position book contributes nothing rather than
    raising: each per-context cycle re-reads and records its own fail-closed reason,
    so one corrupt book cannot starve the rest. An empty union is returned as-is;
    the caller decides the fallback.

    **Order is part of the answer, not presentation.** The fan-out runs sequentially and its
    scarce resources are consumed in order — two live slots, twenty paper ones — and a live
    incident stops it outright, leaving the remaining contexts unvisited. Sorting alphabetically
    made both of those alphabetical. The order is now, in tiers:

    1. contexts holding an open **live** position — real money, and settling or protecting it is
       the most urgent thing a fire does. First also means a halt cannot strand them;
    2. contexts holding an open **paper** position, for the same reason at lower stakes;
    3. everything else by the best ``champion_score`` routable there, descending — the evidence
       the pool was promoted on, standing in for an arbitration the fan-out cannot do without a
       second pass (see :func:`pool.context_scores`);
    4. ``(symbol, timeframe)`` as the tiebreak, so the order stays deterministic."""
    contexts: set[tuple[str, str]] = set()
    scores: dict[tuple[str, str], float] = {}
    try:
        active = pool.load_active_pool(root)
        scores = pool.context_scores(active)
        contexts.update(scores)
    except MvpRuntimeError:
        pass  # per-context cycles below still re-read and record the pool's state

    holding_paper: set[tuple[str, str]] = set()
    try:
        for context, _position in list_open_positions(root):
            holding_paper.add((context.symbol, context.timeframe))
    except MvpRuntimeError:
        pass
    contexts.update(holding_paper)

    holding_live = set(live_position_contexts(root, default_timeframe=default_timeframe))
    contexts.update(holding_live)

    def rank(context: tuple[str, str]) -> tuple[int, float, str, str]:
        tier = 0 if context in holding_live else (1 if context in holding_paper else 2)
        return (tier, -scores.get(context, 0.0), context[0], context[1])

    return sorted(contexts, key=rank)


def accumulate_positioning_cohort(
    *,
    collector: MarketDataCollector,
    now: str,
    root: Path | None,
    contexts: list[tuple[str, str]],
) -> dict[str, str]:
    """Refresh the positioning store for the DECLARED cohort, not for what the pool traded.

    **The scope of a retention store cannot be a side effect of routing.** Accumulation used
    to ride on ``attach_feeds(accumulate=True)``, which runs once per *visited* context — so
    the store covered whatever ``pool_cycle_contexts`` happened to yield that fire, and a
    symbol leaving the routable set stopped being recorded with nothing saying so. Measured
    2026-08-04 on this host: the fan-out was visiting six contexts across four symbols, and

    - ``BNBUSDT`` had been frozen since 2026-07-31T09:20Z — 31 days recorded, then nothing,
      from the day it dropped out of the routable set while still holding 17 pool entries;
    - ``XRPUSDT`` held **zero** rows and always had, because it is a cohort member that the
      pool has never traded, so no context ever carried it.

    That is fatal in a way an ordinary outage is not: the vendor serves 30 days and the
    factory replays 500, so an hour not recorded today is not late, it is **gone**. And it
    compounds — ``coverage_summary`` reports ``eligible`` as the AND over cells, so one
    permanently-empty member holds the gate shut however long the other five accumulate.

    The cohort is the right scope because :data:`CROSS_SECTION_UNIVERSE` is already declared
    for exactly this reason — "not whichever symbols the pool happens to route today" — and a
    store that must cover the cohort was the one thing still following the pool. Visited
    symbols are unioned in rather than assumed to be a subset, so a pool that grows past the
    cohort keeps its own positioning rather than silently losing it.

    Cost is bounded by the store's own hourly throttle, not by this call: at most one request
    per (symbol, series) per hour whoever asks, so a 15-minute fan-out over six symbols costs
    18 requests an hour and the other three fires return ``skipped_fresh`` having opened no
    socket. Never raises — ``record_positioning`` reports per-series degradation instead, and
    a collection miss must not cost a fire its cycles.

    Runs after the context loop, and after a ``live_halt`` too. The halt exists to stop this
    runtime *acting* on a picture of real money it no longer trusts; this opens no order path,
    writes only a local append-only store, and its data is unrecoverable if skipped — so
    deferring it to the next fire would trade a real loss for no safety.
    """
    return {
        symbol: str(
            positioning_store.record_positioning(
                symbol=symbol, collector=collector, now=now, root=root,
            )["status"]
        )
        for symbol in retention_cohort(contexts)
    }


def retention_cohort(contexts: list[tuple[str, str]]) -> list[str]:
    """The symbols a retention store must cover: the declared cohort, unioned with what the
    pool actually visited. Sorted, so a sweep is deterministic.

    One function because it is one rule. Both accumulating stores answer the same question and
    got different answers when only one of them was fixed — see
    :func:`accumulate_open_interest_cohort`."""
    symbols = {str(symbol).strip().upper() for symbol, _timeframe in contexts}
    symbols.update(CROSS_SECTION_UNIVERSE)
    return sorted(s for s in symbols if s)


def accumulate_open_interest_cohort(
    *,
    liquidation_feed: Any | None,
    now: str,
    root: Path | None,
    contexts: list[tuple[str, str]],
) -> dict[str, str]:
    """Refresh the hourly open-interest store for the DECLARED cohort, not for what the pool
    traded. The positioning sweep's rule, applied to the store that still followed routing.

    :func:`accumulate_positioning_cohort` fixed this for positioning on 2026-08-04 and recorded
    why: a retention store's scope cannot be a side effect of routing, because the vendor serves
    ~84 days of hourly history and the factory replays 500 — an hour not recorded today is not
    late, it is **gone**. The hourly OI store was left on the old footing, riding
    :func:`attach_feeds`, which runs once per *visited* context.

    Measured on this host 2026-08-04, the same day the positioning sweep landed: the store held
    **10,644 rows across exactly five symbols** — BTC, ETH, SOL, BNB, DOGE — and **XRPUSDT held
    zero and always had**, because it is a cohort member the pool has never routed, so no
    context ever carried it. That is the identical footprint the positioning docstring reports
    for XRP, on the store nobody moved.

    Cost is the store's own hourly throttle, not this call: ``record_intraday_oi`` measures from
    the last ATTEMPT, so widening the scope to six symbols costs at most six vendor requests an
    hour whoever asks, and the other three fires of a 15-minute fan-out return ``skipped_fresh``
    having opened no socket. Never raises — the store is degrade-only and reports per-symbol
    instead, because a collection miss must not cost a fire its cycles.

    Left deliberately ALONGSIDE the ``attach_feeds`` call rather than replacing it: removing
    that write is a separate change (it is the one that makes ``attach_feeds`` a pure read), and
    the throttle means the extra call site costs nothing. Adding coverage first is the half that
    cannot regress anything."""
    return {
        symbol: str(
            oi_store.record_intraday_oi(
                symbol=symbol, feed=liquidation_feed, now=now, root=root,
            )["status"]
        )
        for symbol in retention_cohort(contexts)
    }


def run_pool_cycle(
    *,
    collector: MarketDataCollector,
    store: PaperStore,
    now: str,
    default_symbol: str = "BTCUSDT",
    default_timeframe: str = DEFAULT_TIMING_CONTEXT,
    limit: int = 120,
    root: Path | None = None,
    control_store: ControlStore | None = None,
    liquidation_feed: Any | None = None,
    routing_marks: Any | None = None,
) -> dict[str, Any]:
    """Fan one governed pass out over every context the pool trades. Returns a summary.

    :func:`run_crypto_cycle` only ever routes the strategies scoped to its single
    symbol, so a pool spread across symbols left most strategies ``unevaluable`` and
    every non-default symbol's open position unsettled — the symbol-starved router.
    This runs one full cycle per :func:`pool_cycle_contexts` entry, falling back to
    the default context when there is nothing to route and nothing open (a heartbeat
    that still collects data), and aggregates the sub-records for the caller's ledger.

    Per-context isolation is the whole point: a configuration or state failure in one
    context (a malformed pool symbol, an unreadable position book) is recorded under
    ``skipped`` and the remaining contexts still run — it can never again starve them.
    The one refusal that is *not* per-context is the kill switch: a PAUSED/KILLED
    runtime refuses every context identically, so that refusal propagates and stops
    the whole fan-out, exactly as it stops a single cycle.

    **A live incident is the second such refusal** (LP5.3). Per-context isolation is
    right for paper — one broken book must not starve the others — and wrong for real
    money: an unprotected position that would not close, a venue-side close this
    runtime cannot price, or a book that disagrees with the venue all mean the
    runtime's picture of real money is now wrong, and opening positions in *other*
    contexts under that uncertainty is the failure the isolation would cause. So a
    cycle reporting ``live_halt`` stops the fan-out, and the contexts that never ran
    are named in ``unvisited`` rather than silently missing.

    The fan-out is also where the positioning store is accumulated, over the declared cohort
    rather than over the contexts this fire visited — see
    :func:`accumulate_positioning_cohort` for why that distinction was costing coverage."""
    contexts = pool_cycle_contexts(root, default_timeframe=default_timeframe) or [
        (default_symbol, default_timeframe)
    ]

    # Both Coinalyze series are per-SYMBOL, but `attach_feeds` runs per (symbol, timeframe) —
    # so this fan-out asked for each symbol's series four times, 40 requests to read 10. The
    # redundancy was invisible until the hourly store added five more per fire and the vendor
    # started refusing whoever came last: ETH and SOL degraded on every Coinalyze series,
    # including the daily open interest and liquidations the router depends on. The cache lives
    # for THIS fan-out only, so it cannot serve a stale day to a later fire.
    if liquidation_feed is not None:
        liquidation_feed = PerSymbolFeedCache(liquidation_feed)
    # The context legs have the same shape of redundancy, by another door: they read OTHER
    # symbols' candles, and which symbol is asking cannot change the answer within one fire.
    # The reference leg reads a CONSTANT proxy, so its only distinct reads are one per
    # timeframe while `attach_reference` runs once per (symbol, timeframe) — sixteen asks for
    # four answers on a 5x4 grid. The cross-sectional leg is that multiplied: six cohort
    # members × four timeframes × five contexts is 120 asks for 24 answers. ONE cache serves
    # both, because a cached candle read has no opinion about which leg wanted it. Same
    # lifetime rule as above: one fan-out, then discarded.
    candle_cache = PeerCandleCache(collector)

    cycles: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    halted: dict[str, Any] | None = None
    unvisited: list[dict[str, Any]] = []
    for index, (symbol, timeframe) in enumerate(contexts):
        if halted is not None:
            unvisited.append({"symbol": symbol, "timeframe": timeframe})
            continue
        try:
            record = run_crypto_cycle(
                collector=collector, store=store, now=now,
                symbol=symbol, timeframe=timeframe, limit=limit, root=root,
                control_store=control_store, liquidation_feed=liquidation_feed,
                routing_marks=routing_marks, candle_cache=candle_cache,
            )
        except MvpRuntimeError as exc:
            if exc.reason_code in _KILL_CODES:
                raise  # global stop — every remaining context would refuse the same
            skipped.append({"symbol": symbol, "timeframe": timeframe, "reason_code": exc.reason_code})
            continue
        cycles.append(record)
        if record.get("live_halt"):
            halted = {
                "symbol": symbol,
                "timeframe": timeframe,
                "live_route_status": record.get("live_route_status"),
                "reason_codes": list(record.get("live_reason_codes") or []),
                "at_index": index,
            }

    positioning = accumulate_positioning_cohort(
        collector=collector, now=now, root=root, contexts=contexts,
    )
    # Same scope rule, the other accumulating store. Cheap because both throttle hourly.
    open_interest_1h = accumulate_open_interest_cohort(
        liquidation_feed=liquidation_feed, now=now, root=root, contexts=contexts,
    )

    summary = {
        "pool_cycle_version": POOL_CYCLE_VERSION,
        "contexts": [{"symbol": s, "timeframe": t} for s, t in contexts],
        "cycles": cycles,
        "skipped": skipped,
        "live_halt": halted,
        "unvisited": unvisited,
        "positioning": positioning,
        "open_interest_1h": open_interest_1h,
        "created_at": now,
    }
    summary["pool_cycle_id"] = integrity.short_id(
        "crypto_pool_cycle", {"contexts": summary["contexts"], "at": now}
    )
    return summary


def cycle_status_line(record: dict[str, Any]) -> str:
    """The one-line status a scheduler fire records for this cycle.

    ``verdict=`` is the PAPER leg's, because every other token on this line — the route, what
    settled, what opened, what a cap held — is a paper fact, and pairing them with the live
    gate's answer would read as a contradiction on any cycle where paper trades while live is
    refused. That cycle is now ordinary rather than impossible, so the live gate gets its own
    token, and only when it disagrees: a line that repeated the same verdict twice on every
    quiet fire is a line an operator learns to skip.
    """
    parts = [f"verdict={record.get('paper_verdict_status') or record['verdict_status']}",
             f"route={record['route_status']}"]
    if record.get("paper_verdict_status") and record["paper_verdict_status"] != record["verdict_status"]:
        parts.insert(1, f"live-gate={record['verdict_status']}")
    if record.get("degraded"):
        parts.insert(0, "degraded")
    if record.get("settled"):
        parts.append(f"settled={record['settled']['close_reason']}({record['settled']['result_R']}R)")
    if record.get("opened"):
        parts.append(f"opened={record['opened']['direction']}:{record['opened'].get('strategy_id')}")
    if record.get("open_skipped"):
        parts.append(f"held={record['open_skipped']['reason_code']}")
    # A built plan a cap declined. The directional one carries the numbers because they are the
    # actionable part — "the book leans 5 long against the limit of 4" tells an operator the
    # book's shape, where a bare reason code would only say a trade did not happen.
    refused = record.get("open_refused")
    if refused:
        detail = f"refused={refused['reason_code']}"
        if refused["reason_code"] == "POSITION_LIMIT_DIRECTIONAL_SKEW":
            detail += (
                f"({refused['direction']} {refused['aligned']}v{refused['opposing']}"
                f" lean={refused['lean_after']}>{refused['limit']})"
            )
        parts.append(detail)
    # A route that entered nothing because every match was regime-excluded otherwise reads
    # exactly like one where nothing matched, and those want different responses: the first says
    # a strategy fired in a regime its own backtest lost money in, the second says the market did
    # not offer a setup. Printed only when it happened, for the reason the live leg is — a field
    # that is empty on almost every line teaches the reader to skip it.
    excluded = record.get("regime_excluded") or []
    if excluded:
        parts.append(f"regime_excluded={','.join(str(s) for s in excluded)}")
    # Only when the live leg actually did something. A DISABLED leg is every machine that has
    # not been through the operator checklist, and printing it on every line would train the
    # reader to skip exactly the field that matters on the machine where it is not DISABLED.
    if record.get("live_route_status") not in (None, ROUTE_DISABLED):
        parts.append(live_route_status_line(record))
    return " ".join(parts)


def pool_cycle_status_line(summary: dict[str, Any]) -> str:
    """The one-line status a scheduler fire records for a whole pool fan-out."""
    cycles = summary.get("cycles") or []
    skipped = summary.get("skipped") or []
    unvisited = summary.get("unvisited") or []
    head = f"pool_cycle contexts={len(cycles)}"
    if skipped:
        head += f" skipped={len(skipped)}"
    halt = summary.get("live_halt")
    if halt:
        # First on the line, before any per-context detail: a fan-out that stopped early is
        # the headline, and a reader who stops after the first phrase must still learn it.
        head += (
            f" LIVE HALT at {halt['symbol']} {halt['timeframe']}"
            f" ({','.join(halt['reason_codes']) or halt['live_route_status']})"
            f" unvisited={len(unvisited)}"
        )
    # Only the symbols whose hour was LOST, and only when there are any. What this sweep exists
    # to close was silent for four days on one symbol and forever on another, so a degraded
    # symbol has to reach the fire's own status line — an hour missed here is not retryable,
    # the vendor serves 30 days. Named rather than counted, because which symbol it is decides
    # whether anyone should act: one bad hour heals on the next fire, the same symbol every
    # hour does not. Silent when clean, for `cycle_status_line`'s reason — a line that named
    # all six on every quiet fire is a line an operator learns to skip.
    degraded = sorted(
        symbol for symbol, status in (summary.get("positioning") or {}).items()
        if status == "degraded"
    )
    if degraded:
        head += f" positioning-degraded={','.join(degraded)}"
    parts = [head]
    parts.extend(f"{r['symbol']} {r['timeframe']}: {cycle_status_line(r)}" for r in cycles)
    parts.extend(f"{s['symbol']} {s['timeframe']}: skipped({s['reason_code']})" for s in skipped)
    return " | ".join(parts)


__all__ = [
    "accumulate_open_interest_cohort",
    "accumulate_positioning_cohort",
    "attach_cross_section",
    "attach_positioning",
    "cycle_status_line",
    "pool_cycle_contexts",
    "pool_cycle_status_line",
    "retention_cohort",
    "run_crypto_cycle",
    "run_pool_cycle",
]
