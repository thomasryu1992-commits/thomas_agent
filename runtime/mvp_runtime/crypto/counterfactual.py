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
import os
from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from .. import jsonl, timeutil
from ..errors import ToolError
from ..filelock import locked
from . import market_data
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

# A shadow only advances `holding_candles` in the cycle that owns its context, so a shadow
# whose context leaves the fan-out has its clock FROZEN — `settle_trade_plan` can never reach
# the time exit, and the row occupies a cap slot forever. Measured 2026-08-04: **30 of 30 open
# shadows sat outside the fan-out's five contexts** (ETHUSDT 1d ×16, BNBUSDT 4h ×10, and four
# others), i.e. 30 of the 50 slots were permanently spent and the instrument that prices gate
# refusals was 60% of the way to silently dropping every new one.
#
# `pool_cycle_contexts` already solved the same problem for real positions by visiting their
# contexts — deliberately NOT copied here, because that buys candle collection forever for
# contexts nothing trades.
#
# **The predicate is the shadow's OWN clock, not its context's membership.** Contexts come
# back (1d re-entered the rotation on 2026-08-04), and expiring on absence would delete a
# shadow that is still legitimately running — measured, 17 of the 30 were still inside their
# own budget, the ETHUSDT 1d block at 242h against 27 bars × 24h = 648h. Past that budget the
# position could not still be open under its own rules however many candles anyone counted,
# and that is also why waiting is worse than expiring: a returning context would settle these
# against TODAY's candles and feed a fabricated outcome to gate calibration, which is exactly
# what `settles_in_context` exists to prevent in the other direction.
EXPIRED_STATUS = "EXPIRED"
EXPIRY_REASON_CLOCK_FROZEN = "holding_budget_elapsed_unsettled"

COUNTERFACTUAL_BOOK_UNVERIFIABLE = "COUNTERFACTUAL_BOOK_UNVERIFIABLE"
COUNTERFACTUAL_HISTORY_TAMPERED = "COUNTERFACTUAL_HISTORY_TAMPERED"
COUNTERFACTUAL_HISTORY_DUPLICATE = "COUNTERFACTUAL_HISTORY_DUPLICATE"
NATIVE_PROVENANCE = "mvp_counterfactual_tracker"


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
    """Open shadows, or raise if the book cannot be read.

    Raising rather than returning empty is the point: the caller used to treat an
    unreadable book as "no shadows" and then REWROTE it, destroying whatever was
    there and hiding the loss. The cycle degrades on this (shadows are observational,
    they must never block trading) but the book is preserved for inspection."""
    path = _book_path(root)
    if not path.is_file():
        return []
    try:
        book = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(
            COUNTERFACTUAL_BOOK_UNVERIFIABLE,
            f"counterfactual book unreadable: {type(exc).__name__}",
        ) from exc
    rows = book.get("positions") if isinstance(book, dict) else None
    if rows is None and not isinstance(book, dict):
        raise ToolError(COUNTERFACTUAL_BOOK_UNVERIFIABLE, "counterfactual book is not an object")
    return [r for r in rows or [] if isinstance(r, dict) and r.get("status") == "OPEN"]


def settles_in_context(plan: Mapping[str, Any], *, symbol: str, timeframe: str) -> bool:
    """Whether this shadow may be judged by the current cycle's candles.

    The guard L1a put on real positions, which the shadow book reintroduced: settling
    a BTC shadow against ETH candles produces a fabricated hypothetical and, worse,
    feeds it to gate calibration. A shadow missing its context cannot prove it belongs
    to this cycle, so it is left alone too."""
    return (
        str(plan.get("symbol") or "") == symbol
        and str(plan.get("timeframe") or "") == timeframe
    )


def holding_budget_elapsed(plan: Mapping[str, Any], *, now: str) -> bool:
    """Whether this shadow's own clock says it could not still be open.

    Wall-clock, because the candle counter is the thing that fails: ``holding_candles`` only
    advances in the cycle owning the shadow's context, so a shadow outside the fan-out is
    frozen short of ``max_holding_bars`` forever. The budget is that same limit read as a
    span — bars × the timeframe's minutes — so a shadow is judged by exactly the number its
    spec was backtested with (the :func:`paper.position_max_hold` parity rule), never by a
    fixed timeout invented here.

    Unreadable inputs return False: a shadow that cannot state its own budget is left alone
    rather than expired on a guess, which is the same fail-closed direction as
    :func:`settles_in_context`.
    """
    timeframe = str(plan.get("timeframe") or "")
    minutes = market_data.TIMEFRAMES.get(timeframe)
    if not minutes:
        return False
    bars, _legacy = position_max_hold(plan, timeframe)
    if not bars or bars <= 0:
        return False
    opened = plan.get("opened_at_utc")
    if not isinstance(opened, str) or not opened:
        return False
    try:
        age_minutes = (timeutil.parse_iso(now) - timeutil.parse_iso(opened)).total_seconds() / 60.0
    except (ValueError, TypeError):
        return False
    return age_minutes > float(bars) * float(minutes)


def expire_shadow(plan: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    """The same row, closed as EXPIRED with the reason and the time. Writes no outcome.

    Deliberately not an outcome row: an outcome carries a ``result_R`` and feeds per-reason
    expectancy, and this shadow has no result — nobody ever priced it. Recording a 0.0 would
    dilute the gate's measured cost with trades that were never judged.

    It leaves the BOOK, and the trace lives in the cycle record instead. The book has only
    ever held open shadows (``load_open_counterfactuals`` filters on ``OPEN`` and the writer
    persists exactly what it was handed), so a row kept there would be dropped on the next
    save anyway — a durable-looking trace that is not one. The caller reports the ids, and
    ``run_crypto_cycle`` already puts that summary on the cycle record that reaches the
    ledger, which is where every other per-cycle decision is auditable."""
    return {
        **dict(plan),
        "status": EXPIRED_STATUS,
        "expired_at_utc": now,
        "expiry_reason": EXPIRY_REASON_CLOCK_FROZEN,
    }


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
        "candidate_id": entry_plan.get("candidate_id"),
        "strategy_rule_hash": entry_plan.get("strategy_rule_hash"),
        "strategy_generation_id": entry_plan.get("strategy_generation_id"),
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
        "candidate_id": plan.get("candidate_id"),
        "strategy_rule_hash": plan.get("strategy_rule_hash"),
        "strategy_generation_id": plan.get("strategy_generation_id"),
        "opened_at_utc": plan.get("opened_at_utc"),
        "created_at_utc": now,
        "provenance": NATIVE_PROVENANCE,
        "kind": "counterfactual",
    }
    # Idempotency key: one shadow settles exactly once, so it derives from the shadow
    # alone — a retried settlement mints the SAME id and is recognised as a duplicate.
    record["counterfactual_settlement_id"] = integrity.short_id(
        "cf_settle", {"counterfactual_id": plan.get("counterfactual_id")}
    )
    record["record_sha256"] = integrity.sha256_record(record)
    return record


def _append_outcomes(records: list[dict[str, Any]], *, root: Path | None) -> None:
    """Append settled shadows, skipping any whose settlement is already recorded.

    The dup check runs under the same lock as the write, so a retry after a crash
    between the append and the book rewrite completes the settlement instead of
    doubling it."""
    path = state_dir(root) / OUTCOMES_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code="COUNTERFACTUAL_STORE_LOCKED", label="counterfactual outcomes"):
        recorded = {
            r.get("counterfactual_settlement_id")
            for r in read_counterfactual_outcomes(root)
            if r.get("counterfactual_settlement_id")
        }
        fresh = [r for r in records if r.get("counterfactual_settlement_id") not in recorded]
        if not fresh:
            return
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            for record in fresh:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def read_counterfactual_outcomes(root: Path | None = None) -> list[dict[str, Any]]:
    """All shadow outcomes — a VERIFIED read, mirroring ``paper.read_outcomes``.

    Native records (provenance ``mvp_counterfactual_tracker``) must recompute their
    ``record_sha256``, and settlement ids must be unique. Imported rows carry the
    SOURCE's hash over pre-import fields, so their tamper evidence is the audited
    import batch, not a recompute here."""
    path = state_dir(root) / OUTCOMES_FILENAME
    rows: list[dict[str, Any]] = []
    seen_settlements: set[str] = set()
    # Streams rather than materializing the file twice, and keeps this reader's own error
    # class: the tool chokepoints above catch ToolError, so raising jsonl's PersistenceError
    # here would fail past the caller instead of at it.
    for lineno, record in jsonl.iter_numbered(
        path,
        read_code="COUNTERFACTUAL_HISTORY_UNREADABLE",
        label="counterfactual outcomes",
        exc_type=ToolError,
    ):
        if not isinstance(record, dict):
            continue
        if record.get("provenance") == NATIVE_PROVENANCE:
            stored = record.get("record_sha256")
            body = {k: v for k, v in record.items() if k != "record_sha256"}
            if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
                raise ToolError(
                    COUNTERFACTUAL_HISTORY_TAMPERED,
                    f"counterfactual outcomes line {lineno} fails its self-hash",
                )
        settlement_id = record.get("counterfactual_settlement_id")
        if isinstance(settlement_id, str) and settlement_id:
            if settlement_id in seen_settlements:
                raise ToolError(
                    COUNTERFACTUAL_HISTORY_DUPLICATE,
                    f"duplicate counterfactual_settlement_id: {settlement_id}",
                )
            seen_settlements.add(settlement_id)
        rows.append(record)
    return rows


def run_counterfactual_update(
    *,
    blocked_plan: Mapping[str, Any] | None,
    block_reasons: list[str],
    last_candle: Mapping[str, Any] | None,
    last_close: float | None,
    symbol: str,
    timeframe: str,
    now: str,
    root: Path | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """One cycle's shadow-book step: settle this context's shadows, then maybe open one.

    Only shadows whose (symbol, timeframe) match this cycle are touched — a foreign
    shadow is carried forward untouched, never judged on candles it was not opened
    against and never advanced toward its time exit. An unreadable book degrades the
    step (``COUNTERFACTUAL_BOOK_UNVERIFIABLE``) and writes NOTHING, so the damaged file
    survives for inspection instead of being silently overwritten; shadows are
    observational, so this must never block the cycle.

    ``persist=False`` (dry-run store) computes the settlement summary without
    writing — the same effect discipline as every other paper mutation."""
    try:
        rows = load_open_counterfactuals(root)
    except ToolError as exc:
        return {
            "settled": [], "opened": None, "open_count": None,
            "degraded": exc.reason_code,
        }

    still_open: list[dict[str, Any]] = []
    settled: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    foreign = 0
    for plan in rows:
        if not settles_in_context(plan, symbol=symbol, timeframe=timeframe):
            # Only here, never for a shadow this cycle CAN judge: an in-context row reaches
            # the time exit on its own and settling beats expiring, because settling produces
            # the priced outcome this whole book exists for.
            if holding_budget_elapsed(plan, now=now):
                expired.append(expire_shadow(plan, now=now))
                continue
            foreign += 1
            still_open.append(plan)          # untouched: no settle, no holding advance
            continue
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
        # Outcomes first, then the book: a crash between them leaves a settled shadow
        # still open, which the settlement-id dup check recognises and completes on the
        # next cycle rather than settling it twice.
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
        # The only durable trace of an expiry — see `expire_shadow`. Named ids rather than a
        # count, because "which shadow stopped being priceable" is the question a later reader
        # of the gate's cost will have.
        "expired": [
            {"counterfactual_id": r.get("counterfactual_id"),
             "symbol": r.get("symbol"), "timeframe": r.get("timeframe"),
             "block_reasons": r.get("block_reasons"), "reason": r.get("expiry_reason")}
            for r in expired
        ],
        "foreign_context_skipped": foreign,
    }


def r_values_by_reason(records: list[Mapping[str, Any]]) -> dict[str, list[float]]:
    """The individual R values behind each block reason's bucket.

    Same grouping as :func:`summarize_counterfactuals` and deliberately beside it: a record
    carries a LIST of reasons and lands in every one of their buckets, so a second
    implementation of that rule elsewhere would drift and the two views would disagree about
    which trades a gate blocked.

    Exists because the summary reports a mean and a count, and a mean is not a verdict. A
    gate rated "costing money" off two blocked trades is a rounding of noise, and the board
    cannot tell that from an aggregate.
    """
    by_reason: dict[str, list[float]] = {}
    for record in records:
        if record.get("outcome_closed") is not True:
            continue
        result_r = float(record.get("result_R") or 0.0)
        for reason in record.get("block_reasons") or ["unattributed"]:
            by_reason.setdefault(str(reason), []).append(result_r)
    return by_reason


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
