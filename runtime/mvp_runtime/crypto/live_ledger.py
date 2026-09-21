"""The live outcome ledger, read back (crypto PR7d-3).

The ledger (`live_outcomes.jsonl`) is the lane's own record of what its live trades earned; the
corrections store (`live_correction`) is the governed amendment of that record. Both are records of what
the lane did, not observations of the market, and several layers read them back: the breakers (risk)
judge the day's and the week's losses on them, the slippage probe (execution) calibrates on the stop
fills, and the outcome and report layers summarise them. This module holds only the reading: the raw
rows, the rows with approved corrections applied, the split into what analysis may use, and the stop
slippage observations. The writer (`RealLiveLedger`) stays in `live_pnl`, which re-exports every name
here as the same object.

It sits in the store layer, below every reader. The feedback loop from outcomes back into risk closes
through this store, not through the outcome module that writes it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from runtime.read_only_kernel import integrity

from .. import jsonl
from ..errors import MvpRuntimeError, ToolError
from . import live_correction
from .state import VENUE_MAINNET, state_dir, venue_state_dir
from .vocabulary import STOP_EXIT_REASONS


LIVE_OUTCOMES_FILENAME = "live_outcomes.jsonl"


LIVE_HISTORY_UNREADABLE = "LIVE_HISTORY_UNREADABLE"


LIVE_HISTORY_TAMPERED = "LIVE_HISTORY_TAMPERED"


LIVE_HISTORY_DUPLICATE = "LIVE_HISTORY_DUPLICATE"


def stop_slippage_observations(
    outcomes: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """The recorded stop-slippage sample, oldest first — what re-deciding the constant reads.

    One row per stop close that carries a computable ``stop_slippage_bps``. Rows from before the
    field existed are absent, not zero: the two 2026-08-06 stops can never appear here because
    their triggers were not stamped, and their hand-measured figures (23.5 / 0.0 bps) stay in
    REMAINING_WORK §C. So this list UNDER-counts the true sample by exactly those rows, and a
    reader averaging it should say so.
    """
    sample: list[dict[str, Any]] = []
    for record in outcomes:
        if not isinstance(record, Mapping):
            continue
        if record.get("close_reason") not in STOP_EXIT_REASONS:
            continue
        bps = record.get("stop_slippage_bps")
        if isinstance(bps, bool) or not isinstance(bps, (int, float)):
            continue
        sample.append({
            "closed_at_utc": record.get("closed_at_utc"),
            "symbol": record.get("symbol"),
            "side": record.get("side"),
            "stop_price": record.get("stop_price"),
            "exit_price": record.get("exit_price"),
            "stop_slippage_bps": float(bps),
            "outcome_id": record.get("outcome_id"),
        })
    return sample


def read_live_outcomes_raw(root: Path | None = None, *, venue: str = VENUE_MAINNET) -> list[dict[str, Any]]:
    """The live outcome FILE, oldest first — a VERIFIED read, corrections not applied.

    Missing store = honestly empty (nothing has traded yet). Anything unreadable, tampered,
    or duplicated raises, because every caller of this history is a risk decision: a history
    that cannot prove itself must not be allowed to argue that the breaker is clear.

    **Use this only where the FILE is the question** — currently one place, the settlement-id
    dedupe on append. Everything that reasons about what happened reads
    :func:`read_live_outcomes` instead, which is the same rows with corrections applied. The
    dedupe cannot: a VOID correction removes a row from the corrected view, and a dedupe that
    read the view would then not see the settlement already on disk and would append it twice —
    and one duplicate fails EVERY verified read of this history.
    """
    path = venue_state_dir(root, venue=venue) / LIVE_OUTCOMES_FILENAME
    outcomes: list[dict[str, Any]] = []
    seen_outcome_ids: set[str] = set()
    seen_settlement_ids: set[str] = set()
    # Reads only. The append below keeps its own fsync, which append_lines does not do.
    for lineno, record in jsonl.iter_numbered(
        path,
        read_code=LIVE_HISTORY_UNREADABLE,
        label="live outcomes",
        exc_type=ToolError,
    ):
        if not isinstance(record, dict):
            continue
        stored = record.get("record_sha256")
        body = {k: v for k, v in record.items() if k != "record_sha256"}
        if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
            raise ToolError(LIVE_HISTORY_TAMPERED, f"live outcomes line {lineno} fails its self-hash")
        outcome_id = record.get("outcome_id")
        if isinstance(outcome_id, str) and outcome_id:
            if outcome_id in seen_outcome_ids:
                raise ToolError(LIVE_HISTORY_DUPLICATE, f"duplicate outcome_id: {outcome_id}")
            seen_outcome_ids.add(outcome_id)
        settlement_id = record.get("settlement_id")
        if isinstance(settlement_id, str) and settlement_id:
            if settlement_id in seen_settlement_ids:
                raise ToolError(LIVE_HISTORY_DUPLICATE, f"duplicate settlement_id: {settlement_id}")
            seen_settlement_ids.add(settlement_id)
        outcomes.append(record)
    return outcomes


def read_live_outcomes(root: Path | None = None) -> list[dict[str, Any]]:
    """The live history as this runtime believes it — the file, plus any approved corrections.

    Unchanged signature and unchanged rows whenever no correction exists, which is every
    deployment until one is written: with the corrections file absent this reads nothing extra,
    touches no approval store, and returns exactly what it always did. That is what let the
    correction record be added at ONE chokepoint — `breaker_watch`, `cycle`, `live_promotion`,
    `run_slippage_probe` and this module's own readers all pass through here, and not one of
    them needed a line.

    A correction that cannot prove itself raises, exactly as a tampered outcome row does. The
    alternative — skipping a correction we cannot verify — would leave the breaker reading a
    figure the operator believes was corrected, which is the one failure a correction mechanism
    must not have.
    """
    outcomes = read_live_outcomes_raw(root)
    path = state_dir(root) / live_correction.CORRECTIONS_FILENAME
    if not path.exists():
        return outcomes
    corrections = live_correction.read_corrections(path)
    if not corrections:
        return outcomes
    return live_correction.apply_corrections(
        outcomes, corrections, approvals=_approvals_for(corrections, root)
    )


def _approvals_for(
    corrections: list[dict[str, Any]], root: Path | None
) -> dict[str, dict[str, Any]] | None:
    """The approval records the given corrections name, or ``None`` if the store cannot be read.

    ``None`` is a refusal downstream, not a pass. Reads only the ids actually referenced rather
    than the whole store, because this runs on every read of the live history and the store
    holds every approval this deployment has ever issued.
    """
    from ..approval_store import ApprovalStore  # local: nothing on the cold path needs it
    try:
        store = ApprovalStore.default(root)
        found: dict[str, dict[str, Any]] = {}
        for correction in corrections:
            approval_id = correction.get("approval_id")
            if not isinstance(approval_id, str) or approval_id in found:
                continue
            record = store.get(approval_id)
            if record is not None:
                found[approval_id] = dict(record)
        return found
    except (OSError, MvpRuntimeError):
        # Narrow on purpose. An unreadable store must fail the correction closed, but a bug in
        # this function must surface as a bug rather than disguise itself as "no approvals".
        return None


# Why a row can be legible to the breaker but not to the R-based consumers.
UNKNOWN_R = "LIVE_OUTCOME_NO_RECORDED_RISK"


def live_outcomes_for_analysis(
    outcomes: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split live outcomes into rows the R-based consumers may read, and rows they may not.

    Returns ``(readable, excluded)``. A readable row is exactly the shape
    ``guards.run_risk_guard``, ``lifecycle`` and ``outcome_math.summarize_outcomes`` already
    consume — ``result_R``, ``created_at_utc``, ``outcome_closed``, plus the lineage the
    lifecycle groups by — so no consumer needs a live-specific branch.

    **The exclusion is the point.** ``guards._closed_rows`` reads a missing ``result_R`` as
    ``0.0``, which is a *breakeven*. A live loss whose risk was never recorded would
    therefore shorten a loss streak instead of extending it, and dilute expectancy toward
    zero — a fail-open in the one direction that matters. Such rows are excluded and
    reported with ``UNKNOWN_R`` rather than passed through with a fabricated R. They remain
    fully visible to the daily-loss breaker, which reads ``realized_pnl_usdt`` and needs no
    R at all: the money is never lost from the accounting, only from the R statistics that
    cannot honestly include it.

    Pure: no I/O. The caller decides what to do with each list.
    """
    readable: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for record in outcomes:
        if not isinstance(record, Mapping) or record.get("outcome_closed") is not True:
            continue
        result_r = record.get("result_R")
        if not isinstance(result_r, (int, float)) or isinstance(result_r, bool):
            excluded.append({
                "outcome_id": record.get("outcome_id"),
                "symbol": record.get("symbol"),
                "realized_pnl_usdt": record.get("realized_pnl_usdt"),
                "reason": UNKNOWN_R,
            })
            continue
        readable.append({
            "outcome_closed": True,
            "result_R": float(result_r),
            # Pre-bridge rows carry only closed_at_utc; either satisfies the consumers.
            "created_at_utc": record.get("created_at_utc") or record.get("closed_at_utc"),
            "strategy_id": record.get("strategy_id"),
            "candidate_id": record.get("candidate_id"),
            "strategy_rule_hash": record.get("strategy_rule_hash"),
            "strategy_generation_id": record.get("strategy_generation_id"),
            "strategy_artifact_sha256": record.get("strategy_artifact_sha256"),
            "symbol": record.get("symbol"),
            "close_reason": record.get("close_reason"),
            "realized_pnl_usdt": record.get("realized_pnl_usdt"),
            # Kept so a consumer that mixes streams can still tell them apart.
            "stage": record.get("stage") or "live",
        })
    return readable, excluded
