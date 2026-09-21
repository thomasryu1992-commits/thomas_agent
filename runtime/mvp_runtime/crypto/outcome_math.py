"""The outcome maths more than one layer of the crypto lane reads (crypto PR7b-2).

`net_result_r` re-reads one paper outcome net of fees, slippage and carry; `summarize_outcomes` reduces
outcomes to R metrics. The guards (risk) and the lifecycle, forward confirmation and the factory
backtest (strategy) read them, and both lived in `feedback`, the report module above every one of those
readers. This is strategy-layer work by what it does: pure maths over the cost model, used to score
strategies (the factory backtest, the lifecycle ladder, forward confirmation), and below the guards
(risk) that also read it. `feedback` re-exports both as the same functions.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..coerce import as_float as _f
from .cost import FUNDING_INTERVALS_PER_DAY, CostModel, funding_cost_r, outcome_net_r
from .market_data import TIMEFRAMES
from .vocabulary import R_BASES_NET_OF_COSTS


def summarize_outcomes(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Core outcome metrics over R (source math verbatim; drawdown is peak-to-trough
    of cumulative R, reported positive). ``by_strategy`` is this port's analog of the
    source's per-signal summary — the axis C8's generation work needs."""
    rows = [dict(r) for r in records if isinstance(r, Mapping)]
    closed = [r for r in rows if r.get("outcome_closed") is True]
    result_rs = [_f(r.get("result_R")) for r in closed]
    wins = [v for v in result_rs if v > 0]
    losses = [v for v in result_rs if v < 0]
    win_count = len(wins)
    loss_count = len(losses)
    expectancy = sum(result_rs) / len(result_rs) if result_rs else 0.0
    # Realized payoff legs (M4a): the average winning R and the average losing R as a
    # positive magnitude, so avg_win_R / avg_loss_R is the realized reward:risk. Kept
    # separate from expectancy — the ranking wants win-rate and payoff as two axes.
    avg_win_r = sum(wins) / win_count if win_count else 0.0
    avg_loss_r = -sum(losses) / loss_count if loss_count else 0.0
    cumulative = peak = max_dd = 0.0
    for value in result_rs:
        cumulative += value
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)

    by_strategy = _grouped(closed, lambda row: str(row.get("strategy_id") or "unattributed"))

    return {
        "outcome_count": len(rows),
        "closed_count": len(closed),
        "win_count": win_count,
        "loss_count": loss_count,
        "breakeven_count": sum(1 for v in result_rs if v == 0),
        "expectancy": round(expectancy, 8),
        "win_loss_ratio": round(win_count / loss_count, 8) if loss_count else float(win_count),
        "average_R": round(expectancy, 8),
        "avg_win_R": round(avg_win_r, 8),
        "avg_loss_R": round(avg_loss_r, 8),
        "max_drawdown": round(max_dd, 8),
        "by_strategy": by_strategy,
    }


def _grouped(closed: list[dict[str, Any]], key_of: Any, *, keep_total: bool = False) -> dict[str, dict[str, Any]]:
    """Closed rows grouped by ``key_of(row)``: count, wins, losses and mean R per group. A row
    whose key is empty belongs to no group. ``keep_total`` keeps each group's unrounded sum of R
    (``total_r``), for a reader that combines groups."""
    groups: dict[str, dict[str, Any]] = {}
    for row in closed:
        key = key_of(row)
        if not key:
            continue
        bucket = groups.setdefault(key, {"closed_count": 0, "win_count": 0, "loss_count": 0, "_sum": 0.0})
        value = _f(row.get("result_R"))
        bucket["closed_count"] += 1
        bucket["_sum"] += value
        bucket["win_count"] += 1 if value > 0 else 0
        bucket["loss_count"] += 1 if value < 0 else 0
    for bucket in groups.values():
        total = bucket.pop("_sum")
        bucket["expectancy"] = round(total / bucket["closed_count"], 8)
        if keep_total:
            bucket["total_r"] = total
    return groups


def _funding_intervals(outcome: Mapping[str, Any], *, intervals_per_day: int = FUNDING_INTERVALS_PER_DAY) -> float:
    """How many 8h settlements this position was open across, from its bars and timeframe.

    Paper does not record wall-clock duration, but it records `holding_candles` and the
    `timeframe` those candles are in, and that product IS the duration. A row missing either
    is charged nothing on this axis rather than a guess — the fee legs still apply, and the
    resulting figure is stated as the optimistic side of the honest one."""
    bars = _f(outcome.get("holding_candles"))
    minutes = TIMEFRAMES.get(str(outcome.get("timeframe") or ""))
    if bars <= 0 or not minutes:
        return 0.0
    return (bars * minutes / 1440.0) * intervals_per_day


def net_result_r(
    outcome: Mapping[str, Any], *, cost: CostModel | None = None
) -> float | None:
    """One paper outcome re-read NET of fees, slippage and carry — or None if it cannot be.

    **This adds carry and delegates the rest.** `cost.outcome_net_r` owns the conversion — the
    basis rule (a row whose costs are already inside it must not be charged again), the risk
    denominator, and the cost model itself. This function used to re-implement all three, and
    the two copies had already drifted: only one of them knew about `intent_net_of_costs`, so
    the ladder and the board would have disagreed about the same row from the first settlement
    after 2026-08-03. One concept, one owner; what is genuinely this module's is the carry.

    Carry belongs here rather than there because deriving it needs `holding_candles × timeframe`
    and therefore `market_data.TIMEFRAMES`, which `cost` deliberately does not import — it is a
    constants-and-arithmetic module with no I/O at import. It is charged at the model's base
    rate rather than the venue's realized settlements: unlike the backtest, a settled paper row
    has no funding series attached, and refetching one per row at report time would put a
    network read behind a board that must render offline."""
    cost = cost or CostModel()
    carry = (_funding_intervals(outcome, intervals_per_day=cost.funding_intervals_per_day)
             * cost.funding_bps_per_interval / 10000.0)

    # **A row that is ALREADY net is costed here, not reported as uncostable.**
    #
    # `cost.outcome_net_r` returns None for these deliberately, so a settlement that already
    # charged fees and slippage is never charged them twice. But None means two different
    # things to a caller — "this row cannot be priced" and "this row is already priced" — and
    # `summarize_net_of_costs` was reading both as the first. Every row this runtime has minted
    # since 2026-07-30 labels itself `intent_net_of_costs`, so the effect was that the whole
    # forward record counted as uncostable: measured 2026-08-01 on the first settlement of the
    # newly promoted pool, a +1.778R take-profit produced `costed_count: 0` and
    # `OUTCOME_NOT_COSTABLE`. Gate 0 requires `not failure_modes`, so that mode alone would have
    # held it shut **however well the pool traded** — the third time this exact latch has
    # appeared, and the first time on the runtime's own current rows.
    #
    # What the two bases each contain has to be exact, or this trades a latch for a wrong
    # number. `paper.build_outcome_record` charges fees and slippage at settlement and records
    # no funding term, and `outcome_net_r` says the same in its own words: `intent_net_of_costs`
    # is "fees and slippage both inside". Carry is therefore the one term still owed, which is
    # also the term this function already owns (see the docstring above). So the net figure is
    # `result_R` minus carry, charged on the ENTRY FILL exactly as `apply_cost_model` charges
    # it — the same call, not a second copy of the arithmetic.
    if outcome.get("r_basis") in R_BASES_NET_OF_COSTS:
        result_r = outcome.get("result_R")
        if isinstance(result_r, bool) or not isinstance(result_r, (int, float)):
            return None
        if not carry:
            return round(float(result_r), 8)
        entry = outcome.get("entry_price")
        risk = outcome.get("risk")
        direction = outcome.get("direction")
        if (
            not isinstance(entry, (int, float)) or isinstance(entry, bool) or entry <= 0
            or not isinstance(risk, (int, float)) or isinstance(risk, bool) or risk <= 0
            or direction not in ("LONG", "SHORT")
        ):
            # Carry is owed and cannot be priced, so the row genuinely is uncostable. Returning
            # `result_R` here would report a figure that silently omits a cost this function
            # exists to charge.
            return None
        entry_fill = cost.fill_price(entry, direction, "entry")
        return round(float(result_r) - funding_cost_r(direction, entry_fill, risk, carry), 8)

    return outcome_net_r(outcome, cost=cost, funding_rate_sum=carry)
