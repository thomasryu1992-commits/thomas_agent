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
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from ..control import ControlStore
from ..errors import MvpRuntimeError, ToolBlocked, ToolError
from . import feedback, pool, positioning_store
from .features import latest_feature_row
# One context's market inputs are assembled in `feed_assembly` (market) since crypto PR7e-2:
# `run_crypto_cycle` calls the attaches and `optional_data_health` once per context. Every name is
# re-exported, as the same object, for the callers that read them as `cycle.<name>`; the scheduler's
# factory dispatches reach `attach_mining_legs` that way.
from .feed_assembly import (  # noqa: F401
    DAILY_SERIES_MAX_AGE_HOURS, FUNDING_MAX_AGE_HOURS, HTF_DEGRADED, OPTIONAL_DATA_DEGRADED_CODES,
    OPTIONAL_FEED_MAX_AGE_HOURS, OPTIONAL_LEG_COLUMNS, POSITIONING_MAX_AGE_HOURS, _FUNDING_RECORDS,
    _LIQUIDATION_DAYS, _feed_readings, attach_cross_section, attach_feeds, attach_htf,
    attach_mining_legs, attach_positioning, attach_reference, optional_data_health,
)
# The retention stores' cohort sweeps are `cohort_retention`'s (market) since crypto PR7e-6. The fan-out
# below calls them after its context loop; they are re-exported, as the same objects, for the callers
# that read them as `cycle.<name>`.
from .cohort_retention import (  # noqa: F401
    accumulate_open_interest_cohort, accumulate_orderbook_cohort, accumulate_positioning_cohort,
    retention_cohort,
)
from .guards import (
    RISK_LIMITS_UNUSABLE_PROBLEM,
    merge_trade_verdict,
    paper_trade_verdict,
    risk_guard_unavailable,
    risk_guard_unreadable,
    run_data_health_check,
    run_risk_guard,
)
# The optional legs' degrade codes are judged in `feed_assembly` now; they stay imported here because
# readers name them as `cycle.<code>` beside `cycle.optional_data_health`.
from .market_data import (  # noqa: F401
    CROSS_SECTION_DEGRADED,
    FUNDING_DEGRADED,
    INDEX_PRICE_DEGRADED,
    LIQUIDATION_DEGRADED,
    MARK_PRICE_DEGRADED,
    OPEN_INTEREST_DEGRADED,
    PREMIUM_INDEX_DEGRADED,
    MARKET_DATA_DEGRADED,
    REFERENCE_DEGRADED,
    TIMEFRAMES,
    MarketDataCollector,
    PeerCandleCache,
    PerSymbolFeedCache,
    collect_market_data,
    degraded_market_data_record,
)
from .cooldown import CooldownMarkStore
from .counterfactual import read_counterfactual_outcomes, run_counterfactual_update
from .forward_book import run_forward_book_update
from .lifecycle import run_lifecycle, split_for_record as lifecycle_split
from .live_allowance import evaluate_live_allowance
from .live_pnl import excluded_outcomes_digest, live_outcomes_for_analysis, read_live_outcomes
from .live_route import (
    DEFAULT_TIMING_CONTEXT,
    ROUTE_DISABLED,
    live_position_contexts,
    live_route_status_line,
    run_live_leg,
)
from .paper import (
    ENTRY_COST_UNECONOMIC,
    SUPPORTING_SHADOW_REASONS,
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

# At least one live outcome could not be given an honest R, so the R-based guard did not read
# it. Surfaced rather than silent: the money is still in the daily-loss breaker, but a row the
# streak logic never saw is something an operator should know about.
LIVE_OUTCOMES_EXCLUDED = "LIVE_OUTCOMES_EXCLUDED_FROM_RISK_GUARD"
# #615 §5 — at least one live-armed lineage spent its allowance this cycle and left the live
# tier. Not a failure and not a verdict on the strategy: the amount we were willing to risk on
# an unproven lineage is gone, and it keeps papering.
LIVE_ALLOWANCE_SPENT = "LIVE_ALLOWANCE_SPENT"


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
    cooldown_marks: Any | None = None,
    candle_cache: Any | None = None,
    positioning_rows: list[dict[str, Any]] | None = None,
    paper_outcomes: list[dict[str, Any]] | None = None,
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
    if positioning_rows is not None and feed_status.get("positioning") in ("seeded", "appended"):
        # This context's own feed step just appended to the store, so the fan-out's pre-read
        # is stale for exactly this symbol — fall back to the fresh read it replaced.
        positioning_rows = None
    attach_positioning(snapshot, root=root, pre_read=positioning_rows)

    # 2) research features (C3).
    feature_row = latest_feature_row(snapshot)
    # The optional data the live entry door judges this context on (PR2d-2): computed here, where
    # the legs' degrade codes and the series the row was built from are both in hand.
    optional_data = optional_data_health(
        snapshot,
        codes=[*feed_reasons, htf_reason, reference_reason, cross_section_reason],
        bar_time=feature_row.get("timestamp") if isinstance(feature_row, Mapping) else None,
        row=feature_row,
    )

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
    # The lineage keys those entries accept (PR3b-3): the drawdown rebase's re-check, None beside
    # a None set — an empty set would release every sealed lineage on a failed read.
    routable_lineages: set[str] | None
    live_routable_ids: set[str] | None
    # Which approval armed each of them (PR2b) — None beside a None set, for the same reason.
    live_arm_approvals: dict[str, str | None] | None
    # #615 §5. Read alongside the breaker's own read below; `readable` stays False on any path
    # that did not produce a trustworthy history, and the allowance treats that as a breach.
    live_readable: list[dict[str, Any]] = []
    live_history_readable = False
    try:
        active_pool = pool.load_active_pool(root)
        routable_ids = pool.routable_strategy_ids(active_pool)
        routable_lineages = pool.routable_lineage_keys(active_pool)
        live_routable_ids = pool.live_routable_strategy_ids(active_pool)
        live_arm_approvals = pool.live_arm_approvals(active_pool)
    except ToolError as exc:
        active_pool = {"active_strategies": []}
        routable_ids = None
        routable_lineages = None
        live_routable_ids = None
        live_arm_approvals = None
        reason_codes.append(exc.reason_code)

    # The breaker limits themselves: the registered per-machine record when one is registered
    # and usable, the `guards` defaults otherwise. A record that cannot be used — tampered,
    # unparseable, outside the code bounds, or a legacy record past the window it carries — fails
    # the guard closed rather than falling back to the defaults. The fallback is the tempting
    # branch and the wrong one: an operator who *tightened* a breaker would have it silently
    # loosened back to the default by the very failure that was supposed to be conservative.
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
    #
    # ``paper_outcomes`` is the fan-out's one verified read of this ledger, handed down so
    # nine contexts do not each pay the per-row SHA256 of the same file (the LIVE ledger below
    # is deliberately NOT handed down: it is small, and the allowance's settle-then-re-read
    # contract depends on that read being fresh). The hand-down's own freshness contract lives
    # in ``run_pool_cycle``: a context that settles evicts the snapshot, so a later context
    # reads fresh exactly when today's per-context read would have seen something new.
    try:
        outcomes = paper_outcomes if paper_outcomes is not None else read_outcomes(root)
    except ToolError as exc:
        reason_codes.append(exc.reason_code)

    # Hoisted: the record below is written on every path, including the ones where the guard
    # never read the history. `None` there means "not measured this cycle", which the absent
    # key on the record then means too — distinct from a measured zero.
    live_excluded_digest: dict[str, Any] | None = None
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
                # The code alone says a row was dropped, never how many or for how much, so an
                # operator could not tell two cents of unmeasurable risk from a material loss
                # leaving the R statistics. Both readings were live on 2026-08-23.
                live_excluded_digest = excluded_outcomes_digest(live_excluded)
            risk = run_risk_guard(
                live_readable, now=now, limits=risk_limits,
                routable_strategy_ids=routable_ids, routable_lineages=routable_lineages,
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
    #
    # Same-bar routing priority (the fast-context cap, Thomas 2026-08-24): realized
    # per-lineage evidence — this runtime's OWN paper rows plus the supporting-shadow
    # settlements, summarized with the same math as the report's `by_strategy` but keyed by
    # lineage, never display id (PR3b-1, Thomas decision 35). Computed only
    # when this context can hold more than one routable strategy, so a flat-cap context pays
    # nothing for it. Observational at this point: an unreadable store degrades the ranking
    # to champion_score (`realized_stats=None`, the pre-evidence behaviour) rather than
    # blocking the cycle — the route must not need the shadow book to trade.
    realized_stats = None
    if pool.max_routable_per_context(timeframe) > 1 and outcomes is not None:
        try:
            own_rows, _imported = split_by_provenance(outcomes)
            shadow_rows = [
                r for r in read_counterfactual_outcomes(root)
                if set(r.get("block_reasons") or []) & SUPPORTING_SHADOW_REASONS
            ]
            # One row per trade of a rule (review of PR3c-1): a rule installed twice records each
            # trade as one entry's own row and the other's benched shadow.
            realized_stats = feedback.realized_by_lineage(
                feedback.distinct_trades(list(own_rows) + shadow_rows))
        except ToolError as exc:
            reason_codes.append(exc.reason_code)
            realized_stats = None
    paper_summary, paper_records = run_paper_update(
        snapshot, feature_row, active_pool, paper_verdict,
        store=store, now=now, root=root, control_store=control_store,
        intrabar_collector=collector, routing_marks=routing_marks,
        cooldown_marks=cooldown_marks, realized_stats=realized_stats,
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
        # The signals the router declined this candle (built inside the paper step's
        # freshness gate, so one per closed candle): they open as supporting shadows so a
        # benched lineage still accrues the evidence the priority ranking above reads.
        supporting_plans=paper_summary.get("supporting_plans"),
    )
    if counterfactual_summary.get("degraded"):
        reason_codes.append(counterfactual_summary["degraded"])

    # 4b') the per-strategy forward book (Thomas 2026-08-29) — observational, like the
    # shadow step above, and for the same reason placed outside every lock: each OCCUPYING
    # strategy scoped to this context advances one bar of its OWN virtual stream, so the
    # 5-1 forward evidence accrues per lineage instead of being serialized behind the
    # routed book's one-position-per-context slot. Reads the same candle the shadows
    # settled on; writes only through its own store, and only when this cycle's store
    # writes at all.
    forward_summary = run_forward_book_update(
        pool=active_pool,
        feature_row=feature_row,
        last_candle=candles_for_cf[-1] if candles_for_cf else None,
        last_close=(candles_for_cf[-1] or {}).get("close") if candles_for_cf else None,
        symbol=symbol,
        timeframe=timeframe,
        now=now,
        root=root,
        persist=bool(getattr(store, "filesystem_write", False)),
    )
    if forward_summary.get("degraded"):
        reason_codes.append(forward_summary["degraded"])

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
        live_arm_approvals=live_arm_approvals,
        optional_data=optional_data,
    )
    reason_codes.extend(live["live_reason_codes"])

    # 5b) lifecycle (C10) — auto-demote decaying strategies, never auto-promote.
    # Evaluated every cycle (pure); APPLIED only through the real gated store, the
    # same effect discipline as every other paper mutation. An unreadable outcome
    # history skips evaluation (no honest windows to judge on).
    lifecycle_decisions: list[dict[str, Any]] = []
    lifecycle_applied = 0
    # Decisions the pool write skipped: judged on a lineage the display id no longer names by the
    # time of the locked write (PR3b-2, Thomas decision 36). The next cycle judges the entry again.
    lifecycle_stale: list[dict[str, Any]] = []
    if outcomes is not None:
        lifecycle_decisions = run_lifecycle(active_pool, outcomes, now=now)
        changed = [d for d in lifecycle_decisions if d.get("status_changed")]
        if changed:
            reason_codes.append("LIFECYCLE_TRANSITION")
        write_refused: str | None = None
        if getattr(store, "filesystem_write", False) and lifecycle_decisions:
            try:
                applied = pool.apply_status_decisions(lifecycle_decisions, root=root)
                lifecycle_applied, lifecycle_stale = applied["changed"], applied["stale"]
            except ToolError as exc:
                reason_codes.append(exc.reason_code)
                write_refused = exc.reason_code
        if lifecycle_stale:
            reason_codes.append(pool.LIFECYCLE_DECISION_STALE)
        skipped = {str(d.get("strategy_id")) for d in lifecycle_stale}
        for decision in changed:
            not_applied = (write_refused if write_refused is not None
                           else "the pool changed since it was judged"
                           if str(decision["strategy_id"]) in skipped else None)
            report_text += (
                f"\nlifecycle: {decision['strategy_id']} "
                f"{decision['previous_status']} -> {decision['new_status']}"
                + (" (manual reactivation required)" if decision["requires_manual_reactivation"] else "")
                + (f" (not applied: {not_applied})" if not_applied else "")
            )

    # The full list stays in play for the runtime (the status write above already used it);
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
        # What the leg saw of the execution stage (PR1a) — None when the gate was closed.
        "live_execution_stage": live.get("execution_stage"),
        # And of the gate's operator switches (PR5a): what the readiness board on a console reads.
        "live_gate": live.get("live_gate"),
        # The cooldown a live stop-out wrote this cycle (PR2a) — None on every other cycle. Paper's
        # refusal record carries its bound; this is where the live one becomes auditable.
        "live_stop_cooldown": live.get("live_stop_cooldown"),
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
        "lifecycle_stale": lifecycle_stale,
        "counterfactual": counterfactual_summary,
        "forward_book": forward_summary,
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
    # Set only when there was something to drop, and therefore only when
    # LIVE_OUTCOMES_EXCLUDED_FROM_RISK_GUARD is in `reason_codes` above. Absent means the guard
    # dropped nothing OR never ran — the reason codes beside it already distinguish those, and a
    # zeroed digest on every clean cycle would be a field a reader learns to skip. Added rather
    # than folded into the literal so a record written by an older build, which has neither key,
    # stays readable by exactly the same consumers.
    if live_excluded_digest:
        record["live_outcomes_excluded"] = live_excluded_digest
    # The optional feeds at this bar (PR2d-2): their ages on every cycle whose bar could be read,
    # open gate or not — the measurement the bounds are to be judged against (~90 bytes a row) —
    # and the feeds past their bound or missing from the bar only when there are any. A cycle with
    # no readable bar (a degraded collection) records none: its feeds were not judged, only
    # refused. The legs' degrade codes are in `reason_codes` already.
    if optional_data["bar_readable"]:
        ages = {feed: state["age_hours"] for feed, state in optional_data["feeds"].items()
                if state["age_hours"] is not None}
        if ages:
            record["optional_data_ages"] = ages
        for key in ("stale", "missing"):
            if optional_data[key]:
                record[f"optional_data_{key}"] = list(optional_data[key])
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
    cooldown_marks: Any | None = None,
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
    # The positioning store has the same shape of redundancy by a third door: every context
    # reads only its own symbol's rows, and which context is asking cannot change the answer
    # within one fire — yet each read re-parsed the whole store (6.8 MB × nine contexts a
    # fire on the live host, growing forever). One parse serves the fan-out. Freshness stays
    # exact: a context whose feed step appends falls back to a fresh read inside
    # `run_crypto_cycle`, and every LATER context of that symbol reads fresh too (the stale
    # set below). Same lifetime rule as the caches above: one fan-out, then discarded.
    positioning_by_symbol = positioning_store.read_rows_grouped(root)
    stale_positioning: set[str] = set()
    # The paper outcome ledger has the same fan-out redundancy at a higher unit price: its
    # read is VERIFIED — a SHA256 per native row plus whole-store dedup — and each context
    # paid it to see a file that changes mid-fire only when a context settles (measured 98ms
    # over 3,000 rows; `feedback.py` already hands one context's read to its own report on
    # the same argument). One verified read serves the fan-out until any context reports a
    # settlement or skips mid-cycle — after that every remaining context reads fresh, which
    # is exactly the freshness a same-fire settlement needs (see the step-3 comment in
    # `run_crypto_cycle`). An unreadable ledger is NOT snapshotted as an absence: each
    # context re-reads and records its own refusal, as before.
    paper_outcomes_snapshot: list[dict[str, Any]] | None
    try:
        paper_outcomes_snapshot = read_outcomes(root)
    except ToolError:
        paper_outcomes_snapshot = None

    cycles: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    halted: dict[str, Any] | None = None
    unvisited: list[dict[str, Any]] = []
    for index, (symbol, timeframe) in enumerate(contexts):
        if halted is not None:
            unvisited.append({"symbol": symbol, "timeframe": timeframe})
            continue
        positioning_key = str(symbol).strip().upper()
        try:
            record = run_crypto_cycle(
                collector=collector, store=store, now=now,
                symbol=symbol, timeframe=timeframe, limit=limit, root=root,
                control_store=control_store, liquidation_feed=liquidation_feed,
                routing_marks=routing_marks, cooldown_marks=cooldown_marks,
                candle_cache=candle_cache,
                positioning_rows=None if positioning_key in stale_positioning
                else positioning_by_symbol.get(positioning_key, []),
                # A fresh list per context, so no consumer can mutate a sibling's view.
                paper_outcomes=(
                    list(paper_outcomes_snapshot)
                    if paper_outcomes_snapshot is not None else None
                ),
            )
        except MvpRuntimeError as exc:
            if exc.reason_code in _KILL_CODES:
                raise  # global stop — every remaining context would refuse the same
            skipped.append({"symbol": symbol, "timeframe": timeframe, "reason_code": exc.reason_code})
            # Whether this context settled before it raised cannot be known from here, and a
            # missed settlement is the unsafe staleness — so the snapshot is dropped.
            paper_outcomes_snapshot = None
            continue
        if (record.get("feeds") or {}).get("positioning") in ("seeded", "appended"):
            stale_positioning.add(positioning_key)
        if record.get("settled") is not None:
            paper_outcomes_snapshot = None
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
    # Third store, same rule, one period rather than one hour — this one's throttle IS the fan-out
    # cadence, because the venue serves no history to catch up from.
    orderbook = accumulate_orderbook_cohort(
        collector=collector, now=now, root=root, contexts=contexts,
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
        "orderbook": orderbook,
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
    # The same line for the order book, and the argument above applies with the one softening
    # clause removed: "an hour missed here is not retryable, the vendor serves 30 days" was the
    # reason positioning earns a place on the status line, and this venue serves none. A period
    # lost here cannot be recovered by any later fire, so it reaches the operator now or never.
    book_degraded = sorted(
        symbol for symbol, status in (summary.get("orderbook") or {}).items()
        if status == "degraded"
    )
    if book_degraded:
        head += f" orderbook-degraded={','.join(book_degraded)}"
    # The marker `pool_cycle_is_stalled` reads back off `last_status` on the next fire, so its
    # threshold and the predicate's are the same number by construction. `degraded=N/M` rides
    # alongside for the operator: "6/11" and "11/11" are different incidents and the token that
    # arms the switch cannot say which.
    cycle_degraded = sum(1 for c in cycles if c.get("degraded"))
    if cycle_degraded:
        head += f" degraded={cycle_degraded}/{len(cycles)}"
    if cycles and 2 * cycle_degraded >= len(cycles):
        head += " majority_degraded"
    if cycles and cycle_degraded == len(cycles):
        head += " all_degraded"
    parts = [head]
    parts.extend(f"{r['symbol']} {r['timeframe']}: {cycle_status_line(r)}" for r in cycles)
    parts.extend(f"{s['symbol']} {s['timeframe']}: skipped({s['reason_code']})" for s in skipped)
    return " | ".join(parts)


PIPELINE_STALLED = "PIPELINE_STALLED"


def cycle_is_stalled(record: Mapping[str, Any], previous_status: str | None) -> bool:
    """Whether THIS degraded cycle follows one that also degraded — a stalled pipeline.

    Same pattern as ``data_review.review_loop_is_stalled``: the first degraded fire stays
    quiet (delivered on the status line), the second consecutive one raises onto the failure
    alert. At a 15-minute cadence two consecutive degraded fires is 30 minutes of a pipeline
    producing nothing — long enough to be a real problem rather than a transient venue hiccup.
    """
    if not record.get("degraded"):
        return False
    previous = str(previous_status or "")
    return previous.startswith("degraded") or PIPELINE_STALLED in previous


def pool_cycle_is_stalled(summary: Mapping[str, Any], previous_status: str | None) -> bool:
    """Whether a MAJORITY of this pool fire's contexts degraded, twice in a row.

    **Majority rather than the original `all`**, Thomas 2026-08-22. `all` over the eleven
    contexts the pool runs meant ten dead feeds and one alive stayed silent indefinitely; the
    argument for tightening is that the cost of doing so is only alert noise, and the evidence
    says there is none to spend: across 909 terminal fires over nine days **no context degraded
    even once**, so `any`, `majority` and `all` would each have fired exactly zero times. Half
    is the point where "the pool is not working" beats "one venue feed is flaky".

    What this does NOT do is stop trading, and that asymmetry is why widening it is cheap:
    `run_pool_cycle` has already run and its records are already on the ledger by the time this
    is consulted (see `scheduler._execute`). Raising only re-labels the fire `failed` and puts
    it on the operator's alert — the next fire runs the full cycle exactly as before.
    """
    cycles = summary.get("cycles") or []
    if not cycles:
        return False
    if 2 * sum(1 for c in cycles if c.get("degraded")) < len(cycles):
        return False
    previous = str(previous_status or "")
    # `all_degraded` is accepted alongside `majority_degraded` for one reason only: a fire
    # recorded by the deployed-but-older image writes the former and this one reads the latter,
    # so the pair keeps a stall that straddles a deploy from silently restarting its count.
    return ("majority_degraded" in previous or "all_degraded" in previous
            or PIPELINE_STALLED in previous)


__all__ = [
    "PIPELINE_STALLED",
    "accumulate_open_interest_cohort",
    "accumulate_orderbook_cohort",
    "accumulate_positioning_cohort",
    "attach_cross_section",
    "attach_positioning",
    "cycle_is_stalled",
    "cycle_status_line",
    "pool_cycle_contexts",
    "pool_cycle_is_stalled",
    "pool_cycle_status_line",
    "retention_cohort",
    "run_crypto_cycle",
    "run_pool_cycle",
]
