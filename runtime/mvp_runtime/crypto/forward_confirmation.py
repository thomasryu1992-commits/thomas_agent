"""Forward confirmation — the holdout's own test, applied to data nobody could have seen.

Thomas approved #690 §5-1 on 2026-08-11. The OBSERVATION tier accumulates forward paper
evidence; this module is the rule that lets it reach the arming door: same thresholds as
the backtest holdout (``MIN_HOLDOUT_TRADES``, ``CONFIDENCE_Z``, the ``_periods_confirm``
slice test at ``t_critical_95``), same slice width the holdout derives (tail/10 of the
timeframe's replay target: 21 days at 4h, 42 at 1d), applied to outcomes settled AFTER the
lineage was minted — which by construction were not available to fit against (A2's reuse
contamination cannot occur). No threshold is minted here; every constant is imported from
the judge it mirrors.

The refusal side is symmetric on purpose (a confirmation must be able to fail):
``FORWARD_CONTRADICTED`` — enough priceable closes and the net edge does not clear its own
dispersion — excludes the lineage from this path exactly as a CONTRADICTED holdout does.

What is deliberately NOT here:

- **No zero-filling.** A calendar slice the lineage never traded in is absence of
  evidence, not a break-even observation — dropped, same as the holdout's rule.
- **No ``sid:`` attribution.** Confirmation evidence must be lineage-exact, so only the
  ``cand:`` and ``gen:generation:rule_hash`` keys of `lifecycle.outcome_attribution_key`
  count; imported display-name-only history can never confirm anybody.
- **No automatic consequence.** The one consumer is the LIVE-tier gate at the promotion
  door (``assert_live_tier_confirmed``): arming stays the operator ask it always was, and
  ``pool.disarm_live_tier`` stays the only automatic ``live_tier`` writer.
- **No hidden attempt count.** The gate stamps how many lineages were under observation
  when it judged (``observed_lineages``) into its refusal and its pass-through record —
  the ``attempts_by_context`` principle: a first confirmation out of twelve watched lines
  is a different finding than one out of one, and the ask Thomas answers should say which.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Iterable, Mapping

from .. import timeutil
from ..errors import ToolError
from .feedback import net_result_r
from .lifecycle import outcome_attribution_key
from .robustness import CONFIDENCE_Z, MIN_HOLDOUT_PERIODS, MIN_HOLDOUT_TRADES, t_critical_95

FORWARD_CONFIRMED = "FORWARD_CONFIRMED"        # unseen forward record shows an edge clearing its noise
FORWARD_CONTRADICTED = "FORWARD_CONTRADICTED"  # enough forward closes, and the edge does not clear it
FORWARD_INSUFFICIENT = "FORWARD_INSUFFICIENT"  # the record cannot be judged (too few, no spread, thin slices)

# 1d trades ~0.03/day (backtest average); at MIN_HOLDOUT_TRADES=25 forward confirmation
# takes ~25 months — unreachable in practice. Thomas 2026-08-21: 10 for 1d, 25 elsewhere.
MIN_FORWARD_TRADES_1D = 10

_FORWARD_TRADE_FLOORS: dict[str, int] = {
    "1d": MIN_FORWARD_TRADES_1D,
}


def min_forward_trades(timeframe: str | None) -> int:
    """The trade floor for forward confirmation, scaled by timeframe."""
    return _FORWARD_TRADE_FLOORS.get(str(timeframe or ""), MIN_HOLDOUT_TRADES)


def slice_width_days(timeframe: str) -> float | None:
    """The holdout's own slice width, in calendar days, for this timeframe.

    ``factory_candle_target(tf) × HOLDOUT_FRACTION / HOLDOUT_PERIODS`` bars, converted
    through the timeframe's bar length — the exact derivation the backtest holdout slices
    with, so 4h reads 21 days and 1d reads 42 and both MOVE if the window ever does.
    ``None`` for a timeframe the factory does not target: a width invented here would be
    a threshold minted outside the judge it claims to mirror.
    """
    from .factory import HOLDOUT_FRACTION, HOLDOUT_PERIODS  # local: heavy module
    from .market_data import TIMEFRAMES, factory_candle_target  # local: heavy module

    minutes = TIMEFRAMES.get(str(timeframe or ""))
    try:
        target = factory_candle_target(str(timeframe))
    except Exception:
        return None
    if not minutes or not isinstance(target, int) or target <= 0:
        return None
    return target * HOLDOUT_FRACTION / HOLDOUT_PERIODS * minutes / 1440.0


def lineage_keys(record: Mapping[str, Any]) -> frozenset[str]:
    """The exact-attribution keys a candidate's forward outcomes may carry.

    ``sid:`` is deliberately absent — see the module docstring."""
    keys: set[str] = set()
    candidate_id = record.get("candidate_id")
    if isinstance(candidate_id, str) and candidate_id:
        keys.add(f"cand:{candidate_id}")
    generation = record.get("generation_id")
    rule_hash = record.get("strategy_rule_hash")
    if isinstance(generation, str) and generation and isinstance(rule_hash, str) and rule_hash:
        keys.add(f"gen:{generation}:{rule_hash}")
    return frozenset(keys)


def forward_outcomes_for(
    record: Mapping[str, Any], outcomes: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """This lineage's CLOSED forward outcomes, in store order."""
    keys = lineage_keys(record)
    if not keys:
        return []
    return [
        o for o in outcomes
        if isinstance(o, Mapping) and o.get("outcome_closed") is True
        and outcome_attribution_key(o) in keys
    ]


def judge_forward(
    record: Mapping[str, Any], outcomes: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """One lineage's forward verdict, with the numbers that justify it.

    Mirrors ``robustness.holdout_status`` branch for branch: the trade floor, then the
    spread, then the simple interval (whose failure is the CONTRADICTED side), then the
    slice test — and every branch that cannot be computed reads INSUFFICIENT, never a pass.
    """
    rows = forward_outcomes_for(record, outcomes)
    priced: list[tuple[float, float]] = []  # (days since epoch, net R)
    for row in rows:
        net = net_result_r(row)
        if net is None:
            continue  # a row that cannot price itself is not evidence
        try:
            moment = timeutil.parse_iso(str(row.get("created_at_utc") or ""))
        except (ValueError, TypeError):
            continue  # nor is one that cannot place itself in time
        priced.append((moment.timestamp() / 86400.0, float(net)))

    def verdict(status: str, **extra: Any) -> dict[str, Any]:
        return {
            "status": status,
            "closed_count": len(rows),
            "priceable_count": len(priced),
            **extra,
        }

    spec = record.get("strategy_spec") or {}
    trade_floor = min_forward_trades(spec.get("timeframe"))
    if len(priced) < trade_floor:
        return verdict(FORWARD_INSUFFICIENT)
    nets = [n for _, n in priced]
    spread = statistics.stdev(nets)
    if spread <= 0:
        return verdict(FORWARD_INSUFFICIENT)
    mean = statistics.mean(nets)
    if mean - CONFIDENCE_Z * spread / math.sqrt(len(nets)) <= 0:
        return verdict(FORWARD_CONTRADICTED, mean_net_r=round(mean, 6))

    width = slice_width_days(str(spec.get("timeframe") or ""))
    if width is None:
        return verdict(FORWARD_INSUFFICIENT, mean_net_r=round(mean, 6))
    first = min(day for day, _ in priced)
    slices: dict[int, float] = {}
    for day, net in priced:
        index = int((day - first) // width)
        slices[index] = slices.get(index, 0.0) + net
    values = list(slices.values())  # empty slices simply do not appear — never zero-filled
    if len(values) < MIN_HOLDOUT_PERIODS:
        return verdict(
            FORWARD_INSUFFICIENT, mean_net_r=round(mean, 6),
            active_slices=len(values), slice_width_days=round(width, 2),
        )
    slice_spread = statistics.stdev(values)
    confirmed = (
        slice_spread > 0
        and statistics.mean(values)
        - t_critical_95(len(values) - 1) * slice_spread / math.sqrt(len(values)) > 0
    )
    return verdict(
        FORWARD_CONFIRMED if confirmed else FORWARD_INSUFFICIENT,
        mean_net_r=round(mean, 6),
        active_slices=len(values), slice_width_days=round(width, 2),
    )


def assert_live_tier_confirmed(
    records: list[Mapping[str, Any]],
    *,
    outcomes: Iterable[Mapping[str, Any]],
    observed_lineages: int,
) -> None:
    """Every lineage armed LIVE is confirmed somewhere unseen — holdout or forward.

    The condition #648 disarmed the pool for, as a door instead of a migration: a backtest
    holdout CONFIRMED (recomputed, `pool.candidate_quality`) passes, a FORWARD_CONFIRMED
    record passes, and everything else refuses with both statuses named. ``observed_lineages``
    — how many lines were under observation when this judgment ran — is stamped into the
    refusal text so the ask Thomas reads carries the attempt count a first confirmation
    must be read against.

    Raises ``CANDIDATE_UNCONFIRMED_FOR_LIVE``.
    """
    from . import pool  # local: pool is heavy and imports widely

    from .robustness import HOLDOUT_CONFIRMED

    outcome_rows = list(outcomes)
    unconfirmed: list[str] = []
    for record in records:
        quality = pool.candidate_quality(record)
        if quality["holdout_status"] == HOLDOUT_CONFIRMED:
            continue
        forward = judge_forward(record, outcome_rows)
        if forward["status"] == FORWARD_CONFIRMED:
            continue
        unconfirmed.append(
            f"{quality['candidate_id']} ({record.get('strategy_id')}): "
            f"holdout={quality['holdout_status']}, forward={forward['status']}"
            f"(n={forward['priceable_count']})"
        )
    if not unconfirmed:
        return
    raise ToolError(
        "CANDIDATE_UNCONFIRMED_FOR_LIVE",
        "arming LIVE needs a confirmation earned on unseen data — a CONFIRMED holdout or a "
        f"FORWARD_CONFIRMED record — and these lineages have neither: {'; '.join(unconfirmed)}. "
        f"Judged over a cohort of {observed_lineages} observed lineage(s). Wait for the forward "
        "record, re-mint at the current window, or pass the explicit "
        "--allow-unconfirmed-holdout escape (the waiver rides into the approval reason).",
    )
