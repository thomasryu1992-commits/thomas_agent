"""C7 cycle orchestration — one governed pass of the five ported stages.

The Dynamic-Task-Team shape the contract promised: data (C2) → research features
(C3) → validation guards (C4) → paper update (C5) → feedback (C6), as one function
whose sub-records ride back to the caller for the ledger. Fail-closed where the
contract says BLOCK, degraded where it says DEGRADE:

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
from ..errors import ToolBlocked, ToolError
from . import feedback, pool
from .features import latest_feature_row
from .guards import merge_trade_verdict, risk_guard_unreadable, run_data_health_check, run_risk_guard
from .market_data import (
    FUNDING_DEGRADED,
    LIQUIDATION_DEGRADED,
    MARKET_DATA_DEGRADED,
    TIMEFRAMES,
    MarketDataCollector,
    collect_market_data,
    degraded_market_data_record,
)
from .counterfactual import run_counterfactual_update
from .lifecycle import run_lifecycle
from .paper import PaperStore, build_entry_plan, read_outcomes, route_entries, run_paper_update

CYCLE_VERSION = "crypto_cycle.v0.1"

# Collection failures that degrade the cycle; anything else is a config error.
_DEGRADABLE_CODES = {"TOOL_ERROR"}

# Funding events fetched per cycle: ≥3/day covers the deepest replay window.
_FUNDING_RECORDS = 1600
_LIQUIDATION_DAYS = 520


def attach_feeds(
    snapshot: dict[str, Any],
    *,
    collector: MarketDataCollector,
    liquidation_feed: Any | None,
    now: str,
) -> tuple[list[str], dict[str, str]]:
    """Fetch the C9 derivative feeds onto ``snapshot`` (mutating it). Degrade-only.

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
    else:
        status["liquidations"] = "absent"
    return reason_codes, status


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
        snapshot, collector=collector, liquidation_feed=liquidation_feed, now=now,
    )
    reason_codes.extend(feed_reasons)

    # 2) research features (C3).
    feature_row = latest_feature_row(snapshot)

    # 3) validation guards (C4) — stricter-wins; unreadable history fails closed.
    health = run_data_health_check(snapshot, now=now, timeframe_minutes=TIMEFRAMES[timeframe])
    outcomes: list[dict[str, Any]] | None = None
    try:
        outcomes = read_outcomes(root)
        risk = run_risk_guard(outcomes, now=now)
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
    paper_summary, paper_records = run_paper_update(
        snapshot, feature_row, active_pool, verdict,
        store=store, now=now, root=root, control_store=control_store,
    )
    if paper_summary.get("settle_refused"):
        reason_codes.append(paper_summary["settle_refused"]["reason_code"])
    if paper_summary.get("settle_recovered"):
        reason_codes.append(paper_summary["settle_recovered"]["reason_code"])

    # 4b) counterfactuals (C11) — purely observational: settle every open shadow
    # with the same exit math, and when the guards refused an actionable signal
    # THIS cycle, shadow the plan the router would have taken (tagged with the
    # refusing reasons). Persisted only through the real gated store.
    blocked_plan = None
    if not bool(verdict.get("allow_new_position")) and paper_summary.get("opened") is None:
        cf_route = route_entries(active_pool, feature_row, symbol=symbol, timeframe=timeframe, now=now)
        blocked_plan = build_entry_plan(cf_route, feature_row, now=now)
    candles_for_cf = snapshot.get("candles") or []
    counterfactual_summary = run_counterfactual_update(
        blocked_plan=blocked_plan,
        block_reasons=list(verdict.get("problems") or []),
        last_candle=candles_for_cf[-1] if candles_for_cf else None,
        last_close=(candles_for_cf[-1] or {}).get("close") if candles_for_cf else None,
        timeframe=timeframe,
        now=now,
        root=root,
        persist=bool(getattr(store, "filesystem_write", False)),
    )

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
        "route_status": paper_summary.get("route_status"),
        "settled": paper_summary.get("settled"),
        "opened": paper_summary.get("opened"),
        "paper_records": paper_records,
        "lifecycle_decisions": lifecycle_decisions,
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


def cycle_status_line(record: dict[str, Any]) -> str:
    """The one-line status a scheduler fire records for this cycle."""
    parts = [f"verdict={record['verdict_status']}", f"route={record['route_status']}"]
    if record.get("degraded"):
        parts.insert(0, "degraded")
    if record.get("settled"):
        parts.append(f"settled={record['settled']['close_reason']}({record['settled']['result_R']}R)")
    if record.get("opened"):
        parts.append(f"opened={record['opened']['direction']}:{record['opened'].get('strategy_id')}")
    return " ".join(parts)


__all__ = ["cycle_status_line", "run_crypto_cycle"]
