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
    MARKET_DATA_DEGRADED,
    TIMEFRAMES,
    MarketDataCollector,
    collect_market_data,
    degraded_market_data_record,
)
from .paper import PaperStore, read_outcomes, run_paper_update

CYCLE_VERSION = "crypto_cycle.v0.1"

# Collection failures that degrade the cycle; anything else is a config error.
_DEGRADABLE_CODES = {"TOOL_ERROR"}


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

    # 2) research features (C3).
    feature_row = latest_feature_row(snapshot)

    # 3) validation guards (C4) — stricter-wins; unreadable history fails closed.
    health = run_data_health_check(snapshot, now=now, timeframe_minutes=TIMEFRAMES[timeframe])
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

    # 5) feedback (C6) — every cycle, even a no-trade one. The report reads the
    # store as persisted: in dry-run it honestly reports the durable (empty) truth.
    try:
        report, report_text = feedback.run_paper_performance_report(now=now, root=root)
    except ToolError as exc:
        report, report_text = None, f"performance report unavailable: {exc.reason_code}"
        if exc.reason_code not in reason_codes:
            reason_codes.append(exc.reason_code)

    record = {
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
