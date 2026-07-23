"""C10 strategy lifecycle — auto-demote a decaying strategy; never auto-promote.

Port of the source's S9 (per-strategy rolling performance) + S10 (lifecycle ladder
ACTIVE → WARNING → PROBATION → SUSPENDED → ARCHIVED). The rules that made the source
version safe carry over verbatim, and they happen to be exactly this runtime's risk
asymmetry:

- **Auto-degradation is permitted; auto-reactivation is not.** Demotion only ever
  restricts what may trade (WARNING/PROBATION still occupy a routing slot; SUSPENDED/
  ARCHIVED cannot enter) — the kill-switch direction. Recovery of WARNING/PROBATION
  back to PAPER_ACTIVE is reversible label movement inside the occupying set.
  SUSPENDED/ARCHIVED are TERMINAL here: reactivation is the manual re-validation path,
  which in this runtime is the C8b promotion door — a Thomas-approved ask.
- **Never discarded on win rate alone**: expectancy and profit factor over a FULL
  rolling window carry the decision (a young strategy is never degraded on thin data),
  and suspension needs 2 consecutive failing evaluations, archive 3 — one bad window
  never suspends outright.
- Only outcomes ATTRIBUTED to a strategy feed its windows, and attribution is by
  LINEAGE (``candidate_id``, else the generation+rule-hash pair) rather than the
  display ``strategy_id`` — which the factory restarts at S001 every generation, so
  grouping by it would judge a fresh strategy on the history of the one it replaced.
  Imported history with no lineage at all honestly feeds nothing.

Effect discipline: :func:`evaluate_lifecycle` and the performance math are pure. The
one effect — updating pool statuses — goes through ``pool.update_statuses`` (locked,
transition-guarded) and is applied by the cycle ONLY when the paper store is the real
gated store; a dry-run cycle computes and records the decisions without persisting,
exactly like every other paper effect.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from runtime.read_only_kernel import integrity

DEFAULT_WINDOWS = (20, 30, 50, 100)

_RANK = {"PAPER_ACTIVE": 0, "WARNING": 1, "PROBATION": 2, "SUSPENDED": 3, "ARCHIVED": 4}
TERMINAL_STATUSES = frozenset({"SUSPENDED", "ARCHIVED"})


def compute_metrics(outcomes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """R-based metrics for one attributed-outcome ledger (source S4d subset —
    the fields S9/S10 and the report consume). Empty ledger → Nones, never zeros."""
    r = [float(o.get("result_R") or 0.0) for o in outcomes]
    trade_count = len(r)
    if trade_count == 0:
        return {
            "trade_count": 0, "win_rate": None, "expectancy_r": None,
            "profit_factor": None, "gross_profit_r": 0.0, "gross_loss_r": 0.0,
            "total_net_r": 0.0,
        }
    wins = [x for x in r if x > 0]
    losses = [x for x in r if x < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trade_count": trade_count,
        "win_rate": len(wins) / trade_count,
        "expectancy_r": sum(r) / trade_count,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None,
        "gross_profit_r": round(gross_profit, 8),
        "gross_loss_r": round(gross_loss, 8),
        "total_net_r": round(sum(r), 8),
    }


def compute_strategy_performance(
    strategy_id: str,
    outcomes: Sequence[Mapping[str, Any]],
    *,
    windows: Sequence[int] = DEFAULT_WINDOWS,
    backtest_win_rate: float | None = None,
    now: str,
) -> dict[str, Any]:
    """The S9 report: rolling + lifetime metrics over chronological outcomes.

    A rolling-N window is only ``window_full`` once the strategy has N outcomes;
    the lifecycle requires a full window before escalating."""
    ordered = list(outcomes)
    lifetime = compute_metrics(ordered)
    report: dict[str, Any] = {
        "strategy_id": strategy_id,
        "trade_count": len(ordered),
        "lifetime": lifetime,
    }
    for w in windows:
        window_trades = ordered[-w:]
        metrics = compute_metrics(window_trades)
        metrics["window_full"] = len(window_trades) >= w
        report[f"rolling_{w}"] = metrics

    report["backtest_win_rate"] = backtest_win_rate
    live_win_rate = lifetime.get("win_rate")
    if backtest_win_rate is not None and live_win_rate is not None:
        report["live_vs_backtest_win_rate_drop"] = round(backtest_win_rate - live_win_rate, 6)
    else:
        report["live_vs_backtest_win_rate_drop"] = None
    report["strategy_performance_report_id"] = integrity.short_id(
        "strategy_performance", {"strategy_id": strategy_id, "n": str(len(ordered)), "at": now}
    )
    report["created_at_utc"] = now
    return report


@dataclass(frozen=True)
class LifecycleThresholds:
    warn_window: int = 20
    warn_expectancy_r: float = 0.0
    warn_profit_factor: float = 1.0

    probation_window: int = 30
    probation_expectancy_r: float = -0.05
    probation_profit_factor: float = 0.9
    probation_win_rate_drop: float = 0.15

    suspend_window: int = 50
    suspend_expectancy_r: float = 0.0
    suspend_profit_factor: float = 0.9
    suspend_consecutive: int = 2

    archive_min_trades: int = 100
    archive_expectancy_r: float = 0.0
    archive_consecutive: int = 3


def _full_window(performance: Mapping[str, Any], window: int) -> Mapping[str, Any] | None:
    metrics = performance.get(f"rolling_{window}")
    if not metrics or not metrics.get("window_full"):
        return None
    return metrics


def _lt(value: Any, bound: float) -> bool:
    return value is not None and value < bound


def _le(value: Any, bound: float) -> bool:
    return value is not None and value <= bound


def evaluate_lifecycle(
    current_status: str,
    performance: Mapping[str, Any],
    *,
    consecutive_failures: int = 0,
    thresholds: LifecycleThresholds | None = None,
    now: str,
) -> dict[str, Any]:
    """Decide the next lifecycle status (source S10, verbatim). Pure."""
    t = thresholds or LifecycleThresholds()
    strategy_id = performance.get("strategy_id")
    reasons: list[str] = []

    m_warn = _full_window(performance, t.warn_window)
    warn = m_warn is not None and (
        _lt(m_warn.get("expectancy_r"), t.warn_expectancy_r)
        or _lt(m_warn.get("profit_factor"), t.warn_profit_factor)
    )
    if warn:
        reasons.append(f"rolling_{t.warn_window}_below_warn_thresholds")

    m_prob = _full_window(performance, t.probation_window)
    probation = m_prob is not None and (
        _le(m_prob.get("expectancy_r"), t.probation_expectancy_r)
        or _lt(m_prob.get("profit_factor"), t.probation_profit_factor)
    )
    win_rate_drop = performance.get("live_vs_backtest_win_rate_drop")
    if win_rate_drop is not None and win_rate_drop > t.probation_win_rate_drop:
        probation = True
        reasons.append("live_win_rate_dropped_below_backtest")
    if probation and f"rolling_{t.probation_window}_below_warn_thresholds" not in reasons:
        reasons.append(f"rolling_{t.probation_window}_below_probation_thresholds")

    m_susp = _full_window(performance, t.suspend_window)
    suspend_metrics = m_susp is not None and (
        _lt(m_susp.get("expectancy_r"), t.suspend_expectancy_r)
        and _lt(m_susp.get("profit_factor"), t.suspend_profit_factor)
    )

    lifetime = performance.get("lifetime") or {}
    archive_metrics = (
        (lifetime.get("trade_count") or 0) >= t.archive_min_trades
        and _lt(lifetime.get("expectancy_r"), t.archive_expectancy_r)
    )

    # A degradation this evaluation extends the consecutive-failure streak.
    failure = bool(warn or probation)
    new_consecutive = consecutive_failures + 1 if failure else 0

    if current_status in TERMINAL_STATUSES:
        new_status = current_status
        reasons = ["terminal_state_requires_manual_reactivation"]
    elif archive_metrics and new_consecutive >= t.archive_consecutive:
        new_status = "ARCHIVED"
        reasons.append("archive_conditions_met")
    elif suspend_metrics and new_consecutive >= t.suspend_consecutive:
        new_status = "SUSPENDED"
        reasons.append("suspend_conditions_met")
    elif probation:
        new_status = "PROBATION"
    elif warn:
        new_status = "WARNING"
    else:
        new_status = "PAPER_ACTIVE"
        if current_status in ("WARNING", "PROBATION"):
            reasons.append("recovered_to_active")

    prev_rank = _RANK.get(current_status, 0)
    new_rank = _RANK.get(new_status, 0)
    decision: dict[str, Any] = {
        "strategy_id": strategy_id,
        "previous_status": current_status,
        "new_status": new_status,
        "status_changed": new_status != current_status,
        "is_escalation": new_rank > prev_rank,
        "is_recovery": new_rank < prev_rank,
        "consecutive_failures": new_consecutive,
        "new_entry_blocked": new_status in TERMINAL_STATUSES,
        "requires_manual_reactivation": new_status in TERMINAL_STATUSES,
        "reasons": reasons,
    }
    decision["strategy_lifecycle_decision_id"] = integrity.short_id(
        "strategy_lifecycle_decision",
        {"strategy_id": str(strategy_id), "prev": current_status, "next": new_status, "at": now},
    )
    decision["created_at_utc"] = now
    return decision


def outcome_attribution_key(record: Mapping[str, Any]) -> str:
    """The lineage an outcome belongs to — never the display name alone.

    ``strategy_id`` restarts at S001 every factory generation, so grouping by it mixes
    a replaced strategy's history into its successor's evaluation: a fresh strategy
    inherits the losses that got its predecessor replaced, or hides behind its wins.
    Preference order: the exact ``candidate_id``; else the (generation, rule hash)
    pair, which is equally lineage-precise and is what pre-lineage outcomes carry;
    else the bare id (imported history with no lineage at all, honestly coarse)."""
    candidate_id = record.get("candidate_id")
    if isinstance(candidate_id, str) and candidate_id:
        return f"cand:{candidate_id}"
    generation = record.get("strategy_generation_id") or record.get("generation_id")
    rule_hash = record.get("strategy_rule_hash")
    if isinstance(generation, str) and generation and isinstance(rule_hash, str) and rule_hash:
        return f"gen:{generation}:{rule_hash}"
    strategy_id = record.get("strategy_id")
    return f"sid:{strategy_id}" if isinstance(strategy_id, str) and strategy_id else ""


def _entry_attribution_keys(entry: Mapping[str, Any]) -> set[str]:
    """Every key an outcome of THIS pool entry could carry, across three eras of
    record-keeping. An outcome is keyed at the best precision IT has, so the entry
    must accept all three or history written before a field existed goes unattributed:

    - ``cand:`` — outcomes since the lineage reached the trading path. Exact.
    - ``gen:``  — outcomes carrying (generation, rule hash). Also lineage-precise:
      a different generation of the same display name keys differently, which is
      what stops a replaced strategy from inheriting its predecessor's record.
    - ``sid:``  — imported history that carries nothing but the display name. It
      cannot be placed in a lineage because it never recorded one, so it attaches to
      whoever holds that name. This is the ONE imprecise join, it is confined to
      pre-lineage records, and the set only shrinks: every new outcome keys on
      ``cand:`` and can never be absorbed by a different lineage. Dropping it instead
      would silently zero out the lifecycle's input for strategies still trading on
      imported history — blinding the auto-demotion this module exists for.
    """
    keys: set[str] = set()
    candidate_id = entry.get("candidate_id")
    if isinstance(candidate_id, str) and candidate_id:
        keys.add(f"cand:{candidate_id}")
    generation = entry.get("generation_id") or entry.get("strategy_generation_id")
    rule_hash = entry.get("strategy_rule_hash")
    if isinstance(generation, str) and generation and isinstance(rule_hash, str) and rule_hash:
        keys.add(f"gen:{generation}:{rule_hash}")
    strategy_id = entry.get("strategy_id")
    if isinstance(strategy_id, str) and strategy_id:
        keys.add(f"sid:{strategy_id}")
    return keys


def run_lifecycle(
    active_pool: Mapping[str, Any],
    outcomes: Sequence[Mapping[str, Any]],
    *,
    now: str,
    thresholds: LifecycleThresholds | None = None,
) -> list[dict[str, Any]]:
    """Evaluate every pool strategy against its attributed outcomes. Pure.

    Attribution is by LINEAGE (:func:`outcome_attribution_key`), not by display name:
    a strategy is judged only on trades its own lineage made. Returns one decision per
    non-terminal strategy (terminal ones are left untouched without even an evaluation
    — the source rule). The caller applies ``status_changed`` decisions through
    ``pool.update_statuses``."""
    by_lineage: dict[str, list[Mapping[str, Any]]] = {}
    for outcome in outcomes:
        if outcome.get("outcome_closed") is not True:
            continue
        key = outcome_attribution_key(outcome)
        if key:
            by_lineage.setdefault(key, []).append(outcome)

    decisions: list[dict[str, Any]] = []
    for entry in active_pool.get("active_strategies") or []:
        strategy_id = entry.get("strategy_id")
        status = str(entry.get("status") or "PAPER_ACTIVE")
        if not isinstance(strategy_id, str) or not strategy_id or status in TERMINAL_STATUSES:
            continue
        attributed = sorted(
            (o for key in _entry_attribution_keys(entry) for o in by_lineage.get(key, [])),
            key=lambda o: str(o.get("created_at_utc") or ""),
        )
        performance = compute_strategy_performance(
            strategy_id, attributed, backtest_win_rate=entry.get("backtest_win_rate"), now=now,
        )
        decisions.append(evaluate_lifecycle(
            status, performance,
            consecutive_failures=int(entry.get("lifecycle_consecutive_failures") or 0),
            thresholds=thresholds, now=now,
        ))
    return decisions
