"""A closed live trade's outcome row (crypto PR7d-3).

`build_live_outcome_record` turns a closed live position into the row the ledger keeps: the realized R
on actual fills, and the stop slippage measured against the trigger on stop exits. The executing leg
(`live_leg`) calls it when it closes a position. It lived in `live_pnl`, beside the ledger that stores
the row, which put the leg's import of it upward. It is the execution layer's settlement arithmetic:
pure, with no I/O. `live_pnl` re-exports it as the same object.
"""

from __future__ import annotations

from typing import Any

from runtime.read_only_kernel import integrity

from .vocabulary import R_BASIS_FILLED, STOP_EXIT_REASONS


LIVE_PROVENANCE = "mvp_live_kernel"


def realized_stop_slippage_bps(
    side: Any, stop_price: Any, exit_price: Any
) -> float | None:
    """How far past its trigger a stop actually filled, in bps of the trigger. Signed.

    Positive is ADVERSE — the fill was worse than the price the stop named — and negative is a
    fill better than the trigger, which a fast reversal can produce and which is information,
    not an error, so it is recorded rather than floored. ``side`` is the CLOSE order's side,
    exactly as the outcome row carries it: SELL closes a LONG (adverse is below the trigger),
    BUY closes a SHORT (adverse is above it).

    This is the measurement `cost.DEFAULT_STOP_SLIPPAGE_BPS` is waiting for. That constant is
    inherited and unmeasured; the first two live observations (23.5 and 0.0 bps, 2026-08-06,
    REMAINING_WORK §C) were reconstructed by hand from the resting order because nothing stamped
    the trigger onto the outcome. This function is that stamp's arithmetic — pure, so the row
    builder and any re-derivation agree to the digit.

    ``None`` whenever the figure cannot be computed honestly: a missing or non-positive trigger,
    a missing exit, an unrecognised side. Never 0.0, which is a measurement ("filled exactly at
    the trigger"), not an absence.
    """
    if side not in ("SELL", "BUY"):
        return None
    if (
        not isinstance(stop_price, (int, float)) or isinstance(stop_price, bool)
        or not isinstance(exit_price, (int, float)) or isinstance(exit_price, bool)
        or stop_price <= 0 or exit_price <= 0
    ):
        return None
    adverse = (stop_price - exit_price) if side == "SELL" else (exit_price - stop_price)
    return round(adverse / stop_price * 10000.0, 4)


def build_live_outcome_record(
    *,
    realized_pnl_usdt: float,
    symbol: str,
    side: str,
    quantity: float,
    entry_price: float | None = None,
    exit_price: float | None = None,
    entry_order_id: Any = None,
    exit_order_id: Any = None,
    strategy_id: str | None = None,
    position_id: str | None = None,
    close_reason: str | None = None,
    opened_at_utc: str | None = None,
    risk_usdt: float | None = None,
    candidate_id: str | None = None,
    strategy_rule_hash: str | None = None,
    strategy_generation_id: str | None = None,
    strategy_artifact_sha256: str | None = None,
    exit_source: str | None = None,
    stop_price: float | None = None,
    risk_snapshot_sha256: str | None = None,
    now: str,
) -> dict[str, Any]:
    """One closed live position, self-hashed.

    ``settlement_id`` is derived from the position identity (``position_id`` + exit order),
    so a retried settlement rebuilds the SAME id even though ``outcome_id`` and the row hash
    move with ``now``. That stable identity is what lets ``live_pnl.RealLiveLedger.append_outcome``
    skip a settlement it already holds: the retry that follows a failed book-clear completes
    the clear instead of doubling the day's realized P&L. The read-side duplicate check in
    :func:`live_ledger.read_live_outcomes` is NOT that protection — it is the alarm for a duplicate that
    lands anyway (a hand-edited file), and it fails the WHOLE history rather than
    double-count, so a duplicate reaching the disk takes the breaker, the risk guard and
    promotion down with it until an operator repairs the ledger. Detection at read time is
    the last line, never the plan.

    **LP5.4 — the outcome bridge.** The original shape carried only what the daily-loss
    breaker needs (``realized_pnl_usdt``), which left the risk guard, the lifecycle demoter,
    and the C6 feedback report blind to live results: they key on ``result_R``,
    ``created_at_utc``, and strategy LINEAGE, none of which existed here. The four additive
    arguments close that gap at the source, so a settled live position is legible to the
    same machinery a paper one is:

    - ``risk_usdt`` — the position's entry↔stop distance in quote terms (LP5.1 records it
      as ``risk``). ``result_R`` is computed from it, and **only** from it: with no recorded
      risk there is no honest R, so ``result_R`` stays ``None`` rather than becoming 0.0.
      That distinction is load-bearing — ``guards._closed_rows`` reads a missing
      ``result_R`` as ``0.0``, i.e. a breakeven, so a real live loss with no risk recorded
      would *shorten* a loss streak instead of extending it. The bridge below therefore
      excludes such rows rather than passing them.
    - ``candidate_id`` / ``strategy_rule_hash`` / ``strategy_generation_id`` — the lineage
      the lifecycle groups by. Without it a live result would be attributed to whatever
      strategy currently answers to that display id, which the factory restarts at S001
      every generation.

    ``created_at_utc`` mirrors ``closed_at_utc`` because that is the field name every
    consumer reads for an outcome's time. Both are emitted rather than one renamed: the
    live record's own vocabulary stays intact, and the analytic key is present too.

    ``exit_source`` says WHERE the exit price came from — the closing order this runtime
    sent, a venue bracket leg, or the account's fill list. It was stamped on the settle
    RESULT when the fill-history fallback shipped, which put it in the cycle record and not
    on the row it describes: a consumer reading `live_outcomes.jsonl` alone had to infer the
    provenance from ``close_reason``, and that inference is wrong in both directions — a
    `venue_external_close` could in principle be priced by a leg, and a `stop_loss` priced
    from the history is exactly what this runtime now produces.

    Written on every path rather than only the fallback, so ABSENT means one thing: a row
    from before this field existed. That is the ``lifecycle_*`` provenance rule — absent is
    "an older runtime wrote this", which is a different answer from any of the values and
    must not be collapsed into the most common one.

    ``stop_price`` is the position's resting trigger at close, stamped so the row can carry
    its own ``stop_slippage_bps`` (:func:`realized_stop_slippage_bps`) — the measurement
    `cost.DEFAULT_STOP_SLIPPAGE_BPS` has been waiting for since the 2026-08-06 stops had to be
    reconstructed by hand. Stamped on EVERY row that has one, but the slippage figure is
    computed only for :data:`STOP_EXIT_REASONS`: a time exit's distance from a trigger it never
    touched is not slippage, and recording it as such would pollute the one sample this exists
    to accumulate. ``None`` on both fields means what absence means everywhere in this record —
    not measured — never "measured at zero".
    """
    risk = float(risk_usdt) if isinstance(risk_usdt, (int, float)) and risk_usdt else 0.0
    realized = round(float(realized_pnl_usdt), 8)
    body: dict[str, Any] = {
        "realized_pnl_usdt": realized,
        # R is the realized P&L over what was risked. None when the risk was not recorded —
        # never 0.0, which would read as a breakeven trade to every consumer.
        "result_R": round(realized / risk, 8) if risk > 0 else None,
        "risk_usdt": risk if risk > 0 else None,
        "symbol": symbol,
        "side": side,
        "quantity": float(quantity),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "entry_order_id": entry_order_id,
        "exit_order_id": exit_order_id,
        "strategy_id": strategy_id,
        "candidate_id": candidate_id,
        "strategy_rule_hash": strategy_rule_hash,
        "strategy_generation_id": strategy_generation_id,
        # The artifact the entry was approved as (PR3a-2). None for a position opened before it
        # rode on the order; absent on a row written before this field existed.
        "strategy_artifact_sha256": strategy_artifact_sha256,
        "position_id": position_id,
        # The pre-order snapshot the entry left under (PR2b) — why this trade was allowed. None
        # for a position opened before the gate existed.
        "risk_snapshot_sha256": risk_snapshot_sha256,
        "close_reason": close_reason,
        # Where the exit PRICE came from, beside the reason the position closed. The two
        # answer different questions and neither implies the other.
        "exit_source": exit_source,
        # The resting trigger and how far past it the stop actually filled (adverse-positive
        # bps). Slippage only on a stop close — see the docstring for why a time exit must not
        # contribute to this sample.
        "stop_price": float(stop_price) if isinstance(stop_price, (int, float))
                      and not isinstance(stop_price, bool) and stop_price > 0 else None,
        "stop_slippage_bps": (
            realized_stop_slippage_bps(side, stop_price, exit_price)
            if close_reason in STOP_EXIT_REASONS else None
        ),
        "opened_at_utc": opened_at_utc,
        "closed_at_utc": now,
        # The analytic time key. Same instant as closed_at_utc; named as the consumers read it.
        "created_at_utc": now,
        "outcome_closed": True,
        "stage": "live",
        "provenance": LIVE_PROVENANCE,
        # What this R is measured against. Paper R is computed on INTENDED fills and, since
        # 2026-07-30, NET of modelled fees and slippage (`R_BASIS_INTENT_NET`). Live R is computed
        # on ACTUAL fills, so real slippage is already inside it but fees are NOT charged here.
        # The three bases are therefore still not the same statistic, and the
        # consumers that pool them — the risk guard, the lifecycle demoter, the C6 report — can
        # only be read honestly if the difference is visible in the row rather than known by
        # whoever remembers it. Live R is the more pessimistic of the two, so the breaker trips
        # sooner and demotion comes faster: the distortion runs in the conservative direction,
        # which is why this is recorded rather than corrected.
        "r_basis": R_BASIS_FILLED,
    }
    body["outcome_id"] = integrity.short_id(
        "live_out", {"position_id": position_id, "closed_at": now, "symbol": symbol}
    )
    body["settlement_id"] = integrity.short_id(
        "live_settle", {"position_id": position_id, "exit_order_id": exit_order_id}
    )
    body["record_sha256"] = integrity.sha256_record(body)
    return body
