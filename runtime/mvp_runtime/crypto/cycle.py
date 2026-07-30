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
    risk_guard_unavailable,
    risk_guard_unreadable,
    run_data_health_check,
    run_risk_guard,
)
from .market_data import (
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
    PerSymbolFeedCache,
    ReferenceCandleCache,
    collect_market_data,
    degraded_market_data_record,
)
from .counterfactual import run_counterfactual_update
from .lifecycle import run_lifecycle, split_for_record as lifecycle_split
from .live_pnl import live_outcomes_for_analysis, read_live_outcomes
from .live_route import ROUTE_DISABLED, live_position_symbols, live_route_status_line, run_live_leg
from .paper import (
    ENTRY_COST_UNECONOMIC,
    PaperStore,
    build_entry_plan,
    list_open_positions,
    read_outcomes,
    run_paper_update,
    split_by_provenance,
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

# Funding events fetched per cycle: ≥3/day covers the deepest replay window.
_FUNDING_RECORDS = 1600
_LIQUIDATION_DAYS = 520


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

    ``cache`` is a :class:`~.market_data.ReferenceCandleCache` for one fan-out. Without it
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
    reference_cache: Any | None = None,
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
            "source": collection_record["source"], "is_synthetic": False,
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
        snapshot, collector=collector, now=now, cache=reference_cache
    )
    if reference_reason:
        reason_codes.append(reference_reason)

    # 2) research features (C3).
    feature_row = latest_feature_row(snapshot)

    # 3) validation guards (C4) — stricter-wins; unreadable history fails closed.
    health = run_data_health_check(snapshot, now=now, timeframe_minutes=TIMEFRAMES[timeframe])
    outcomes: list[dict[str, Any]] | None = None

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
    if risk_limits is not None:
        try:
            outcomes = read_outcomes(root)
            # The risk guard judges **this runtime's own** trading only. The store also holds
            # history imported from the frozen crypto_AI_System, which is real but was produced by
            # different code — so it cannot answer "is THIS system losing right now", which is the
            # only question a breaker asks. Measured 2026-07-25: 112 imported rows worth +266.8R sat
            # inside the rolling week, so the weekly-loss breaker could not trip however this runtime
            # performed. A breaker that cannot trip is not a breaker.
            #
            # Deliberately scoped to the guard. `run_lifecycle` below keeps the full history on
            # purpose: imported outcomes carry strategy lineage, and promotion/demotion is a
            # performance judgement about a strategy, not a safety brake on this runtime.
            own_outcomes, _imported = split_by_provenance(outcomes)
            # LP5.3: live results are this runtime's own trading too — and the only kind that costs
            # real money — so the breaker must see them. They live in their own store, so the paper
            # split above never sees them; without this the guard would ignore live losses entirely.
            #
            # Routed through LP5.4's bridge rather than concatenated raw: `guards._closed_rows` reads
            # a missing `result_R` as 0.0, i.e. a BREAKEVEN, so an R-less live loss would SHORTEN a
            # loss streak. The bridge drops those rows (they stay visible to the daily-loss breaker,
            # which needs no R). An unreadable or tampered live history raises, and fails the guard
            # closed exactly like an unreadable paper history — a history that cannot prove itself
            # must not be allowed to argue the breaker is clear.
            live_readable, live_excluded = live_outcomes_for_analysis(read_live_outcomes(root))
            if live_excluded:
                reason_codes.append(LIVE_OUTCOMES_EXCLUDED)
            risk = run_risk_guard(own_outcomes + live_readable, now=now, limits=risk_limits)
        except ToolError as exc:
            risk = risk_guard_unreadable(f"{exc.reason_code}: {exc}", now=now)
            reason_codes.append(exc.reason_code)
    verdict = merge_trade_verdict(health, risk)

    # Strategy pool: tampered/unreadable = do not route (trade nothing), still cycle.
    try:
        active_pool = pool.load_active_pool(root)
    except ToolError as exc:
        active_pool = {"active_strategies": []}
        reason_codes.append(exc.reason_code)

    # 4) paper update (C5) — kill-switch bound inside; refusals propagate.
    # The same gated collector resolves an ambiguous exit at 1m — a refinement, so a
    # failure degrades the settlement to its pessimistic assumption, never blocks it.
    paper_summary, paper_records = run_paper_update(
        snapshot, feature_row, active_pool, verdict,
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
    block_reasons = list(verdict.get("problems") or [])
    if cost_refused:
        block_reasons.append(ENTRY_COST_UNECONOMIC)
    blocked_plan = None
    if (not bool(verdict.get("allow_new_position")) or cost_refused) and paper_summary.get("opened") is None:
        blocked_plan = build_entry_plan(shared_route, feature_row, now=now) if shared_route else None
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

    # 4c) the live leg (LP5.3 step 3) — the one step that can move real money, behind the one
    # module that may. On a machine that has not opted in this returns DISABLED having read
    # nothing, so the whole branch costs one env check. It runs AFTER the paper
    # step so it can share that step's routing result rather than re-evaluating the pool, and
    # it is given the same C4 verdict — a live entry can never be permitted where a paper one
    # was not. Never raises: `run_live_leg` reports, because a traceback here would be
    # indistinguishable from "no live activity".
    live = run_live_leg(
        route=shared_route,
        feature_row=feature_row,
        verdict=verdict,
        symbol=symbol,
        collector=collector,
        now=now,
        root=root,
        control_store=control_store,
    )
    reason_codes.extend(live["live_reason_codes"])

    # 5) feedback (C6) — every cycle, even a no-trade one. The report reads the
    # store as persisted: in dry-run it honestly reports the durable (empty) truth.
    try:
        report, report_text = feedback.run_paper_performance_report(now=now, root=root)
    except ToolError as exc:
        report, report_text = None, f"performance report unavailable: {exc.reason_code}"
        if exc.reason_code not in reason_codes:
            reason_codes.append(exc.reason_code)

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
        "verdict_status": verdict["status"],
        "verdict_problems": verdict["problems"],
        # The breaker limits this cycle was judged against, and the record they came from. Kept
        # even though the rest of the verdict is not: the limits are configurable now, so
        # "ALLOW" no longer states what was allowed, and a ledger row nobody can re-check
        # against the numbers in force at the time is not an audit trail. ~150 bytes against
        # the 24KB record the lifecycle trim above was worth doing for.
        "risk_limits": verdict["risk_guard"].get("limits"),
        "route_status": paper_summary.get("route_status"),
        "settled": paper_summary.get("settled"),
        "opened": paper_summary.get("opened"),
        "open_skipped": paper_summary.get("open_skipped"),
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
        "lifecycle_evaluated": len(lifecycle_decisions),
        "lifecycle_applied": lifecycle_applied,
        "counterfactual": counterfactual_summary,
        "report_status": report.get("status") if report else None,
        "report_text": report_text,
        "created_at": now,
    }
    record["cycle_id"] = integrity.short_id(
        "crypto_cycle", {"symbol": symbol, "timeframe": timeframe, "at": now}
    )
    return record


def pool_cycle_contexts(
    root: Path | None = None, *, default_timeframe: str = "1d"
) -> list[tuple[str, str]]:
    """Every ``(symbol, timeframe)`` one pool pass must visit, sorted.

    The union of three sets, because a cycle both *opens* and *settles*:

    - the pool's routable contexts (:func:`pool.routable_contexts`) — so every
      strategy is actually evaluated, not just the ones scoped to one default
      symbol;
    - the contexts of every currently OPEN paper position — so a position whose
      strategy has since been demoted out of the routable set is still visited by
      its own symbol's cycle and can settle, never stranded; and
    - the symbol of every open **live** position (LP5.3). Same rule, higher stakes:
      a live position whose strategy has been demoted would otherwise have no cycle
      that could settle it, and it holds real money. Live positions are keyed by
      symbol alone, so one is paired with ``default_timeframe`` when the symbol is
      not already being visited — the timeframe governs which candles are collected
      and which strategies route, neither of which the settlement reads.

    A tampered/unreadable pool or position book contributes nothing rather than
    raising: each per-context cycle re-reads and records its own fail-closed reason,
    so one corrupt book cannot starve the rest. An empty union is returned as-is;
    the caller decides the fallback."""
    contexts: set[tuple[str, str]] = set()
    try:
        contexts.update(pool.routable_contexts(pool.load_active_pool(root)))
    except MvpRuntimeError:
        pass  # per-context cycles below still re-read and record the pool's state
    try:
        for context, _position in list_open_positions(root):
            contexts.add((context.symbol, context.timeframe))
    except MvpRuntimeError:
        pass
    visited = {symbol for symbol, _timeframe in contexts}
    for symbol in live_position_symbols(root):
        if symbol not in visited:
            contexts.add((symbol, default_timeframe))
    return sorted(contexts)


def run_pool_cycle(
    *,
    collector: MarketDataCollector,
    store: PaperStore,
    now: str,
    default_symbol: str = "BTCUSDT",
    default_timeframe: str = "1d",
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
    are named in ``unvisited`` rather than silently missing."""
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
    # The reference leg has the same shape of redundancy: the proxy symbol is a CONSTANT, so
    # across this fan-out the only distinct reads are one per timeframe, while
    # `attach_reference` runs once per (symbol, timeframe) — sixteen asks for four answers on a
    # 5x4 grid. Same lifetime rule as above: one fan-out, then discarded.
    reference_cache = ReferenceCandleCache(collector)

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
                routing_marks=routing_marks, reference_cache=reference_cache,
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

    summary = {
        "pool_cycle_version": POOL_CYCLE_VERSION,
        "contexts": [{"symbol": s, "timeframe": t} for s, t in contexts],
        "cycles": cycles,
        "skipped": skipped,
        "live_halt": halted,
        "unvisited": unvisited,
        "created_at": now,
    }
    summary["pool_cycle_id"] = integrity.short_id(
        "crypto_pool_cycle", {"contexts": summary["contexts"], "at": now}
    )
    return summary


def cycle_status_line(record: dict[str, Any]) -> str:
    """The one-line status a scheduler fire records for this cycle."""
    parts = [f"verdict={record['verdict_status']}", f"route={record['route_status']}"]
    if record.get("degraded"):
        parts.insert(0, "degraded")
    if record.get("settled"):
        parts.append(f"settled={record['settled']['close_reason']}({record['settled']['result_R']}R)")
    if record.get("opened"):
        parts.append(f"opened={record['opened']['direction']}:{record['opened'].get('strategy_id')}")
    if record.get("open_skipped"):
        parts.append(f"held={record['open_skipped']['reason_code']}")
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
    parts = [head]
    parts.extend(f"{r['symbol']} {r['timeframe']}: {cycle_status_line(r)}" for r in cycles)
    parts.extend(f"{s['symbol']} {s['timeframe']}: skipped({s['reason_code']})" for s in skipped)
    return " | ".join(parts)


__all__ = [
    "cycle_status_line",
    "pool_cycle_contexts",
    "pool_cycle_status_line",
    "run_crypto_cycle",
    "run_pool_cycle",
]
