"""C11 counterfactual tracker — what the gates actually cost (source port).

The system fails closed, so most matched signals never become positions — a guard
refuses them. Nothing recorded what those trades *would have done*, which makes a
gate that is too conservative indistinguishable from one that is saving money: both
leave no trace in the outcome registry. This shadows every blocked-but-actionable
signal: the plan the router would have taken is settled against real candles with
the SAME exit math as the paper kernel, and the settled hypothetical is appended —
tagged with the block reasons — to the counterfactual registry. Per-reason expectancy
turns gate calibration into an empirical question.

Purely observational, structurally: shadow outcomes live in their OWN file
(``counterfactual_outcomes.jsonl`` — the same separation the C7 import enforced), so
the risk guard can never mistake one for realized P&L; ``hypothetical: true`` rides
every record; nothing here feeds a gate decision. The shadow book is runtime private
state and persists only through the real gated store path (a dry-run cycle computes
and drops, like every other paper effect). A persistently-blocking guard re-fires the
same signal every cycle, so the open book is capped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from ..errors import ToolError
from ..filelock import locked
from .paper import position_max_hold, settle_trade_plan, state_dir

COUNTERFACTUAL_TRACKER_VERSION = "counterfactual_tracker.v1"
BOOK_FILENAME = "counterfactual_positions.json"
OUTCOMES_FILENAME = "counterfactual_outcomes.jsonl"

# A signal blocked by a persistent condition (a daily loss limit, say) re-fires
# every cycle. The cap bounds the shadow book so a stuck gate cannot grow it
# without limit.
MAX_OPEN_COUNTERFACTUALS = 50

MISSED_OPPORTUNITY = "MISSED_OPPORTUNITY"
AVOIDED_LOSS = "AVOIDED_LOSS"
NEUTRAL_BLOCK = "NEUTRAL_BLOCK"


def classify_counterfactual(result_r: float) -> str:
    """A blocked trade that would have won is a cost; one that would have lost is
    the gate earning its keep."""
    if result_r > 0:
        return MISSED_OPPORTUNITY
    if result_r < 0:
        return AVOIDED_LOSS
    return NEUTRAL_BLOCK


def _book_path(root: Path | None) -> Path:
    return state_dir(root) / BOOK_FILENAME


def load_open_counterfactuals(root: Path | None = None) -> list[dict[str, Any]]:
    path = _book_path(root)
    if not path.is_file():
        return []
    try:
        book = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # An unreadable OBSERVATIONAL book is dropped, not fatal: unlike the position
        # store, nothing real can be double-opened over it — losing shadows loses
        # calibration data only, and that loss is visible in the book rewrite.
        return []
    rows = book.get("positions") if isinstance(book, dict) else None
    return [r for r in rows or [] if isinstance(r, dict) and r.get("status") == "OPEN"]


def _save_book(rows: list[dict[str, Any]], *, root: Path | None, now: str) -> None:
    path = _book_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code="COUNTERFACTUAL_BOOK_LOCKED", label="counterfactual book"):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "counterfactual_tracker_version": COUNTERFACTUAL_TRACKER_VERSION,
            "updated_at_utc": now,
            "open_count": len(rows),
            "positions": rows,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)


def build_shadow_plan(
    entry_plan: Mapping[str, Any], *, block_reasons: list[str], now: str
) -> dict[str, Any]:
    """The shadow position a blocked-but-actionable signal would have opened.

    Same fields the settle math needs (the C5 plan shape) plus the block context
    that makes per-reason calibration possible."""
    plan = {
        "status": "OPEN",
        "symbol": entry_plan.get("symbol"),
        "timeframe": entry_plan.get("timeframe"),
        "direction": entry_plan.get("direction"),
        "entry_price": entry_plan.get("entry_price"),
        "stop_loss": entry_plan.get("stop_loss"),
        "take_profit": entry_plan.get("take_profit"),
        "risk": entry_plan.get("risk"),
        # Exit parity rides into the shadow too: a counterfactual settled on a
        # different time-exit than the real trade would have used measures nothing.
        "max_holding_bars": entry_plan.get("max_holding_bars"),
        "holding_candles": 0,
        "strategy_id": entry_plan.get("strategy_id"),
        "strategy_rule_hash": entry_plan.get("strategy_rule_hash"),
        "block_reasons": sorted({str(r) for r in block_reasons if str(r)}),
        "opened_at_utc": now,
    }
    plan["counterfactual_id"] = integrity.short_id(
        "counterfactual",
        {"strategy_id": str(plan["strategy_id"]), "entry": str(plan["entry_price"]), "opened_at": now},
    )
    return plan


def build_counterfactual_outcome_record(
    plan: Mapping[str, Any], *, close_reason: str, exit_price: float | None, result_r: float, now: str
) -> dict[str, Any]:
    record = {
        "counterfactual_outcome_version": COUNTERFACTUAL_TRACKER_VERSION,
        "counterfactual_id": plan.get("counterfactual_id"),
        "outcome_closed": True,
        # No order ever existed. This must never be read as realized P&L.
        "hypothetical": True,
        "classification": classify_counterfactual(result_r),
        "result_R": round(float(result_r), 8),
        "close_reason": close_reason,
        "exit_price": exit_price,
        "holding_candles": int(plan.get("holding_candles", 0) or 0),
        "symbol": plan.get("symbol"),
        "timeframe": plan.get("timeframe"),
        "direction": plan.get("direction"),
        "entry_price": plan.get("entry_price"),
        "stop_loss": plan.get("stop_loss"),
        "take_profit": plan.get("take_profit"),
        "risk": plan.get("risk"),
        "block_reasons": plan.get("block_reasons") or [],
        "strategy_id": plan.get("strategy_id"),
        "strategy_rule_hash": plan.get("strategy_rule_hash"),
        "opened_at_utc": plan.get("opened_at_utc"),
        "created_at_utc": now,
        "provenance": "mvp_counterfactual_tracker",
        "kind": "counterfactual",
    }
    record["record_sha256"] = integrity.sha256_record(record)
    return record


def _append_outcomes(records: list[dict[str, Any]], *, root: Path | None) -> None:
    path = state_dir(root) / OUTCOMES_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code="COUNTERFACTUAL_STORE_LOCKED", label="counterfactual outcomes"):
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_counterfactual_outcomes(root: Path | None = None) -> list[dict[str, Any]]:
    path = state_dir(root) / OUTCOMES_FILENAME
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ToolError("COUNTERFACTUAL_HISTORY_UNREADABLE", f"counterfactual outcomes unreadable: {exc.strerror}") from exc
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ToolError("COUNTERFACTUAL_HISTORY_UNREADABLE",
                            f"counterfactual outcomes line {i + 1} is not valid JSON") from exc
        if isinstance(record, dict):
            rows.append(record)
    return rows


def run_counterfactual_update(
    *,
    blocked_plan: Mapping[str, Any] | None,
    block_reasons: list[str],
    last_candle: Mapping[str, Any] | None,
    last_close: float | None,
    timeframe: str,
    now: str,
    root: Path | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """One cycle's shadow-book step: settle every open shadow, then maybe open one.

    ``persist=False`` (dry-run store) computes the settlement summary without
    writing — the same effect discipline as every other paper mutation."""
    rows = load_open_counterfactuals(root)
    still_open: list[dict[str, Any]] = []
    settled: list[dict[str, Any]] = []
    for plan in rows:
        max_hold, _ = position_max_hold(plan, str(plan.get("timeframe") or timeframe))
        reason, exit_price, result_r = settle_trade_plan(plan, last_candle, last_close, max_hold, False)
        if reason is None:
            still_open.append(plan)
            continue
        settled.append(build_counterfactual_outcome_record(
            plan, close_reason=reason, exit_price=exit_price, result_r=float(result_r), now=now,
        ))

    opened: dict[str, Any] | None = None
    if blocked_plan is not None and len(still_open) < MAX_OPEN_COUNTERFACTUALS:
        opened = build_shadow_plan(blocked_plan, block_reasons=block_reasons, now=now)
        still_open.append(opened)

    if persist:
        if settled:
            _append_outcomes(settled, root=root)
        _save_book(still_open, root=root, now=now)

    return {
        "settled": [
            {"counterfactual_id": r["counterfactual_id"], "classification": r["classification"],
             "result_R": r["result_R"], "block_reasons": r["block_reasons"]}
            for r in settled
        ],
        "opened": opened.get("counterfactual_id") if opened else None,
        "open_count": len(still_open),
    }


def summarize_counterfactuals(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Per-block-reason calibration: what each refusing guard cost or saved."""
    by_reason: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("outcome_closed") is not True:
            continue
        result_r = float(record.get("result_R") or 0.0)
        for reason in record.get("block_reasons") or ["unattributed"]:
            bucket = by_reason.setdefault(str(reason), {
                "closed_count": 0, "missed_opportunity": 0, "avoided_loss": 0, "_sum": 0.0,
            })
            bucket["closed_count"] += 1
            bucket["_sum"] += result_r
            if result_r > 0:
                bucket["missed_opportunity"] += 1
            elif result_r < 0:
                bucket["avoided_loss"] += 1
    for bucket in by_reason.values():
        bucket["expectancy_R"] = round(bucket.pop("_sum") / bucket["closed_count"], 8)
    return by_reason
