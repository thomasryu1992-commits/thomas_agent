"""#615 §5 — the per-lineage LIVE allowance. A spend control, not a verdict.

The evaluation stage cannot act on a single strategy. Measured 2026-08-08: the lifecycle ladder
needs a FULL rolling window (20 / 30 / 50 / 100) and the five routable lineages held 0, 1, 3, 0
and 1 attributed paper outcomes between them, so no rung is reachable — `lifecycle.py`'s own
comment concedes "at any window a 4h lineage will reach this decade". The only automatic device
that *can* fire is the pool-wide loss breaker, and it halts everything rather than demoting
anything.

**So this does not try to judge.** At n=2 no test can say "this strategy is bad", and pretending
otherwise is how a system starts retiring healthy strategies on noise — the same trap that makes
lowering the ladder's windows the wrong fix (the code's own measurement: 31.6% false-PROBATION at
n=5, 1.9% at n=50). What it says instead is narrower and defensible at any sample size:

    the amount we were willing to risk on an unproven lineage is spent.

That is the language this repo already speaks. `max_order_notional_usdt`, `max_daily_order_count`,
`max_open_notional_usdt`, `daily_loss_limit_usdt` are allowances, not verdicts about the market.
This is one more, scoped to a lineage instead of to the account.

**The effect is a tier move, not a demotion.** A breaching lineage leaves the live tier and keeps
papering — it stays in the pool, keeps its slot, keeps accruing the evidence the ladder will one
day be able to judge. Nothing here writes a status, and nothing here can arm anything:
`pool.disarm_live_tier` has no argument for a target tier.

**Additive, never a replacement.** The obvious follow-on — "targeted stops mean the pool-wide
breaker can relax" — is refused. That change would leave MORE trading running than today, which
is a money-door widening and a separate approval. The breaker keeps its numbers.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..coerce import as_optional_float as _f
from .lifecycle import outcome_attribution_key

LIVE_ALLOWANCE_VERSION = "live_allowance.v0.1"

# The draft values, and their weakness is stated rather than hidden (#615 §8-A). They are the
# pool-wide breaker's numbers narrowed to one lineage's share; the live sample is 3 outcomes, so
# there is nothing to calibrate against and will not be for months.
#
# **An allowance does not need a calibrated threshold the way a verdict does** — it encodes how
# much we were willing to risk before pausing to look, which is a preference, not a measurement.
# What it must not do is bind so tight that lineages leave the live tier before they can produce
# any evidence at all. If that happens the honest reading is "there is nothing worth proving
# live right now", not "the numbers are wrong".
MAX_CONSECUTIVE_LIVE_LOSSES = 2
MAX_CUMULATIVE_LIVE_LOSS_R = -2.0

BREACH_CONSECUTIVE = "LIVE_ALLOWANCE_CONSECUTIVE_LOSSES"
BREACH_CUMULATIVE_R = "LIVE_ALLOWANCE_CUMULATIVE_R"
# The live history could not be trusted. Conservative by design (#615 §5.4): a history that
# cannot prove itself must not be able to argue that an allowance is still unspent.
BREACH_HISTORY_UNREADABLE = "LIVE_ALLOWANCE_HISTORY_UNREADABLE"


def _consecutive_losses(rows: Sequence[Mapping[str, Any]]) -> int:
    """Newest-first until a non-loss, the pool-wide breaker's own rule (`guards._consecutive_losses`).

    Deliberately the same shape so the two controls cannot disagree about what a streak is —
    the only difference between them is the population they count over."""
    count = 0
    for row in reversed(list(rows)):
        value = _f(row.get("result_R"))
        if value is not None and value < 0:
            count += 1
        else:
            break
    return count


def evaluate_live_allowance(
    live_outcomes: Sequence[Mapping[str, Any]] | None,
    *,
    live_routable_strategy_ids: set[str] | None,
    pool: Mapping[str, Any] | None = None,
    max_consecutive: int = MAX_CONSECUTIVE_LIVE_LOSSES,
    max_cumulative_r: float = MAX_CUMULATIVE_LIVE_LOSS_R,
) -> dict[str, Any]:
    """Which live-armed lineages have spent their allowance. Pure — writes nothing.

    Returns ``{"breached": [{strategy_id, lineage, reasons, consecutive, cumulative_r}], ...}``.
    Only lineages that are **currently live-armed** can appear: disarming what is already
    disarmed is noise, and a lineage that never had the permission never spent an allowance.

    ``live_outcomes`` of ``None`` means the history could not be read. Every armed lineage is
    then reported as breached on ``BREACH_HISTORY_UNREADABLE`` — the conservative direction, and
    the asymmetry is deliberate: being wrong here costs paper evidence for a while, while being
    wrong the other way spends real money against a limit nobody could verify.

    Attribution is by LINEAGE (`outcome_attribution_key`), never by display id — `strategy_id`
    restarts at S001 each generation, so counting by it would charge a fresh strategy for its
    predecessor's losses. An outcome with no attributable key is counted against nothing and
    reported separately: misattributing a live loss is worse than not attributing it.
    """
    armed = set(live_routable_strategy_ids or set())
    result: dict[str, Any] = {
        "live_allowance_version": LIVE_ALLOWANCE_VERSION,
        "armed_count": len(armed),
        "breached": [],
        "unattributed_outcomes": 0,
        "limits": {"max_consecutive": max_consecutive, "max_cumulative_r": max_cumulative_r},
    }
    if not armed:
        return result

    if live_outcomes is None:
        result["breached"] = [
            {"strategy_id": sid, "lineage": None, "reasons": [BREACH_HISTORY_UNREADABLE],
             "consecutive": None, "cumulative_r": None}
            for sid in sorted(armed)
        ]
        return result

    # The armed lineages, keyed the way the outcomes are. Taken from the pool when one is given
    # so a display id and its lineage can be matched; without a pool the display id is all there
    # is, which is the coarse-but-honest fallback `outcome_attribution_key` itself ends on.
    lineage_of: dict[str, str] = {}
    for entry in ((pool or {}).get("active_strategies") or []):
        if not isinstance(entry, Mapping):
            continue
        sid = str(entry.get("strategy_id") or "")
        if sid in armed:
            lineage_of[sid] = outcome_attribution_key({
                "candidate_id": entry.get("candidate_id"),
                "strategy_generation_id": entry.get("generation_id"),
                "strategy_rule_hash": entry.get("strategy_rule_hash"),
                "strategy_id": sid,
            })

    by_lineage: dict[str, list[Mapping[str, Any]]] = {}
    for outcome in live_outcomes:
        if not isinstance(outcome, Mapping) or outcome.get("outcome_closed") is not True:
            continue
        key = outcome_attribution_key(outcome)
        if not key:
            result["unattributed_outcomes"] += 1
            continue
        by_lineage.setdefault(key, []).append(outcome)

    for sid in sorted(armed):
        lineage = lineage_of.get(sid, f"id:{sid}")
        rows = by_lineage.get(lineage) or []
        if not rows:
            continue
        consecutive = _consecutive_losses(rows)
        cumulative = sum(_f(r.get("result_R")) or 0.0 for r in rows)
        reasons = []
        if consecutive >= max_consecutive:
            reasons.append(BREACH_CONSECUTIVE)
        if cumulative <= max_cumulative_r:
            reasons.append(BREACH_CUMULATIVE_R)
        if reasons:
            result["breached"].append({
                "strategy_id": sid,
                "lineage": lineage,
                "reasons": reasons,
                "consecutive": consecutive,
                "cumulative_r": round(cumulative, 6),
            })
    return result


__all__ = [
    "BREACH_CONSECUTIVE",
    "BREACH_CUMULATIVE_R",
    "BREACH_HISTORY_UNREADABLE",
    "LIVE_ALLOWANCE_VERSION",
    "MAX_CONSECUTIVE_LIVE_LOSSES",
    "MAX_CUMULATIVE_LIVE_LOSS_R",
    "evaluate_live_allowance",
]
