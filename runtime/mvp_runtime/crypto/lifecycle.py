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
- **Judged on NET R.** The thresholds are stated in R and have always been read as "below
  this the strategy loses money", but their input — paper ``result_R`` — is cost-free by
  construction, so before 2026-07-30 nothing here could see a strategy whose gross edge was
  smaller than its fees. ``compute_metrics`` now converts through ``outcome_math.net_result_r``
  (fees, slippage and carry — the same three terms the performance report reads); the
  threshold numbers are unchanged because it is their meaning that was broken, not their value.
- Only outcomes ATTRIBUTED to a strategy feed its windows, and attribution is by
  LINEAGE (``candidate_id``, else the generation+rule-hash pair) rather than the
  display ``strategy_id`` — which the factory restarts at S001 every generation, so
  grouping by it would judge a fresh strategy on the history of the one it replaced.
  Imported history with no lineage at all honestly feeds nothing.

Effect discipline: :func:`evaluate_lifecycle` and the performance math are pure. The
one effect — updating pool statuses — goes through ``pool.apply_status_decisions`` (locked,
transition-guarded, and applied only to the lineage and status each decision judged) and is
applied by the cycle ONLY when the paper store is the real
gated store; a dry-run cycle computes and records the decisions without persisting,
exactly like every other paper effect.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from runtime.read_only_kernel import integrity

from ..errors import ToolError
from . import outcome_math
# Moved to the leaf (PR3b-1) so the router can key on a lineage without importing this module,
# which then imported `feedback`, which imports `paper`. Re-exported: its callers import it from here.
from .candidate_identity import entry_attribution_keys as _entry_attribution_keys
from .candidate_identity import own_attribution_keys as _own_attribution_keys
from .candidate_identity import predecessor_keys as _predecessor_keys
from .candidate_identity import lineage_of, outcome_attribution_key

DEFAULT_WINDOWS = (20, 30, 50, 100)

_RANK = {"PAPER_ACTIVE": 0, "WARNING": 1, "PROBATION": 2, "SUSPENDED": 3, "ARCHIVED": 4}
TERMINAL_STATUSES = frozenset({"SUSPENDED", "ARCHIVED"})


def outcome_judged_r(outcome: Mapping[str, Any]) -> tuple[float, bool]:
    """The R this outcome is judged on, and whether costs are in it.

    The thresholds below are written as if their input were net of costs —
    ``warn_expectancy_r = 0.0`` only means "losing money" if the number it compares has paid
    for the round trip. Paper R has not (``cost.py``), so until 2026-07-30 the whole ladder
    graded a gross figure against net rungs and a strategy at +0.02R gross / −0.30R net could
    never be demoted at all. ``outcome_math.net_result_r`` supplies the conversion — fees and
    slippage through ``cost.outcome_net_r``, plus the carry that function cannot derive on its
    own (funding over ``holding_candles × timeframe``) — so the ladder judges the same figure
    the performance report prints, and a row that is already net of fees and slippage
    (``intent_net_of_costs``) is costed its remaining carry instead of falling into the
    gross fallback below.

    A row it cannot price keeps ``result_R`` rather than being dropped: excluding it would
    shrink the rolling window, and a window that never fills escalates nothing — the same
    "no verdict is reachable" failure `promotion_backlog.days_to_lifecycle_window` exists to surface.
    The second element of the tuple is what makes the compromise legible instead of silent;
    :func:`compute_metrics` counts both populations onto the metrics.
    """
    net = outcome_math.net_result_r(outcome)
    if net is None:
        return float(outcome.get("result_R") or 0.0), False
    return float(net), True


def compute_metrics(outcomes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """R-based metrics for one attributed-outcome ledger (source S4d subset —
    the fields S9/S10 and the report consume). Empty ledger → Nones, never zeros.

    R is **net of costs wherever the row can price them** (:func:`outcome_judged_r`), and
    ``r_basis_gross_rows`` says how many could not — a non-zero count means this window mixes
    two statistics, which is the C2 gap made visible rather than closed.
    """
    judged = [outcome_judged_r(o) for o in outcomes]
    r = [value for value, _ in judged]
    trade_count = len(r)
    if trade_count == 0:
        return {
            "trade_count": 0, "win_rate": None, "expectancy_r": None,
            "profit_factor": None, "gross_profit_r": 0.0, "gross_loss_r": 0.0,
            "total_net_r": 0.0, "r_basis_net_rows": 0, "r_basis_gross_rows": 0,
        }
    wins = [x for x in r if x > 0]
    losses = [x for x in r if x < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net_rows = sum(1 for _, costed in judged if costed)
    return {
        "trade_count": trade_count,
        "win_rate": len(wins) / trade_count,
        "expectancy_r": sum(r) / trade_count,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None,
        "gross_profit_r": round(gross_profit, 8),
        "gross_loss_r": round(gross_loss, 8),
        "total_net_r": round(sum(r), 8),
        # How many rows in this window had their costs charged, and how many kept a gross
        # `result_R` because they could not be priced. Reported rather than reconciled: the
        # decision above is only as honest as its inputs, and a reader has to be able to see
        # when it was made on a mix.
        "r_basis_net_rows": net_rows,
        "r_basis_gross_rows": trade_count - net_rows,
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
    # The win-rate rule is the only one that reads LIFETIME trades rather than a rolling
    # window, so it is the only one `_full_window` does not already guard — and until
    # 2026-08-02 it had no guard at all. A single losing trade put `live_win_rate` at 0.0
    # against a backtest rate of 0.411, a drop of 0.411 past a 0.15 threshold, and demoted a
    # freshly promoted lineage to PROBATION on its first settlement. Reproduced: `n=1 loss ->
    # PROBATION`. The five lineages promoted 2026-07-31 escaped only because the first
    # settlement was a WIN.
    #
    # **50, and it is the sharpest instrument in this ladder rather than the bluntest.** A
    # Bernoulli rate converges far faster than the mean of a heavy-tailed R distribution:
    # measured on the 4h ROBUST candidates (win rate 0.411, per-trade R sd 1.402), this
    # threshold sits **2.16 SE** from the truth at 50 trades where the rolling-50 expectancy
    # test sits at 0.60. False-PROBATION on a strategy performing exactly to its backtest:
    # 31.6% at n=5, 10.6% at n=20, **1.9% at n=50**. That is the whole reason to guard it
    # rather than weaken it — the rule is worth keeping precisely because it can decide
    # something the R test cannot decide at any window a 4h lineage will reach this decade.
    #
    # Deliberately not tied to `probation_window` (30). This guards a LIFETIME statistic, and
    # the number that matters is where its own false-positive rate becomes acceptable, which
    # is a property of the win rate rather than of any rolling window's size.
    probation_win_rate_min_trades: int = 50

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

    # Two independent routes to PROBATION, and each now appends only its OWN reason. They used
    # to share one: the rolling-window reason was appended whenever `probation` was true from
    # either route, so a demotion the win-rate rule caused reported
    # `rolling_30_below_probation_thresholds` — naming a window that was not full and had not
    # been consulted. A reason code that attributes a decision to the wrong rule is worse than
    # a missing one, because it sends the reader to re-derive a threshold that did not fire.
    m_prob = _full_window(performance, t.probation_window)
    probation_metrics = m_prob is not None and (
        _le(m_prob.get("expectancy_r"), t.probation_expectancy_r)
        or _lt(m_prob.get("profit_factor"), t.probation_profit_factor)
    )
    if probation_metrics:
        reasons.append(f"rolling_{t.probation_window}_below_probation_thresholds")

    # The lifetime win-rate route, guarded by its own minimum sample — see
    # `probation_win_rate_min_trades`. Below it the statistic is not weak, it is misleading:
    # one losing trade reads as a total collapse in win rate.
    # An absent or non-numeric `trade_count` refuses the rule rather than waiving the guard:
    # not knowing the sample is the state this rule reads worst, so it must not be the state
    # in which it fires. The R-based rules above are unaffected and still judge the lineage.
    win_rate_drop = performance.get("live_vs_backtest_win_rate_drop")
    trade_count = performance.get("trade_count")
    win_rate_probation = (
        win_rate_drop is not None
        and isinstance(trade_count, int)
        and trade_count >= t.probation_win_rate_min_trades
        and win_rate_drop > t.probation_win_rate_drop
    )
    if win_rate_probation:
        reasons.append("live_win_rate_dropped_below_backtest")

    probation = probation_metrics or win_rate_probation

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


# The one status transition an operator may originate. Every other transition in this
# module is a verdict on realized performance, and that is the right default — but a
# strategy promoted in error has no performance signal to wait for, and waiting is not
# neutral: it occupies a routing slot, and `route_entries` picks ONE strategy per
# context, so the slot-holder is the only one that ever produces the outcomes the
# automatic path would need in order to demote it. Without this the runtime can only
# retire a strategy by REPLACING the whole pool through the promotion door, which
# resets every member's status — reactivating terminal entries as a side effect.
OPERATOR_RETIREMENT_REASON = "operator_retired"


def operator_retirement_decision(
    entry: Mapping[str, Any], *, reason: str, retired_by: str, now: str,
) -> dict[str, Any]:
    """One operator-originated SUSPEND, in the shape ``pool.apply_status_decisions`` accepts.

    Deliberately not a second way to compute a status: the record carries the same
    fields ``evaluate_lifecycle`` produces, so the pool entry that results is
    identical in SHAPE to an automatic demotion and fully distinguishable in
    PROVENANCE (``reasons == ["operator_retired"]`` plus the operator's own text).

    SUSPENDED only. Reactivation is the approval door and ARCHIVED is the lifecycle's
    own escalation; neither is something an operator reaches through this verb. An
    entry that is already terminal refuses here rather than at the store, so the
    caller reads which strategy blocked instead of a whole-batch refusal.

    ``consecutive_failures`` carries over untouched — retiring a strategy is a
    statement about the pool, never about the strategy's record.
    """
    strategy_id = entry.get("strategy_id")
    if not (isinstance(strategy_id, str) and strategy_id):
        raise ToolError("LIFECYCLE_DECISION_INVALID", "pool entry has no strategy_id")
    if not (isinstance(reason, str) and reason.strip()):
        raise ToolError("RETIREMENT_REASON_REQUIRED", f"{strategy_id}: a retirement needs a reason")
    current = str(entry.get("status"))
    if current in TERMINAL_STATUSES:
        raise ToolError(
            "LIFECYCLE_TERMINAL_IMMUTABLE",
            f"{strategy_id} is already {current}; there is nothing to retire",
        )
    decision: dict[str, Any] = {
        "strategy_id": strategy_id,
        # The lineage retired, all three fields (the generation since PR3b-2): the pool write
        # refuses the decision if the id names another lineage by then.
        **lineage_of(entry),
        "previous_status": current,
        "new_status": "SUSPENDED",
        "status_changed": True,
        "is_escalation": True,
        "is_recovery": False,
        "consecutive_failures": int(entry.get("lifecycle_consecutive_failures") or 0),
        "new_entry_blocked": True,
        "requires_manual_reactivation": True,
        "reasons": [OPERATOR_RETIREMENT_REASON],
        "retirement_reason": reason.strip(),
        "retired_by": retired_by,
    }
    decision["strategy_lifecycle_decision_id"] = integrity.short_id(
        "strategy_lifecycle_decision",
        {"strategy_id": strategy_id, "prev": current, "next": "SUSPENDED", "at": now},
    )
    decision["created_at_utc"] = now
    return decision


def run_lifecycle(
    active_pool: Mapping[str, Any],
    outcomes: Sequence[Mapping[str, Any]],
    *,
    now: str,
    thresholds: LifecycleThresholds | None = None,
) -> list[dict[str, Any]]:
    """Evaluate every pool strategy against its attributed outcomes. Pure.

    Attribution is by LINEAGE (:func:`outcome_attribution_key`), not by display name:
    a strategy is judged only on trades its own lineage made, and those of the lineages it replaced
    (PR3c, Thomas decision 41) — the stricter of the two judgements, so an inherited record never
    loosens one. Returns one decision per
    non-terminal strategy (terminal ones are left untouched without even an evaluation
    — the source rule). Each decision names the lineage it judged, and the caller applies them
    through ``pool.apply_status_decisions``, which skips one whose display id names another
    lineage by then (PR3b-2)."""
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
        def judged(keys: set[str]) -> dict[str, Any]:
            attributed = sorted(
                (o for key in keys for o in by_lineage.get(key, [])),
                key=lambda o: str(o.get("created_at_utc") or ""),
            )
            performance = compute_strategy_performance(
                strategy_id, attributed, backtest_win_rate=entry.get("backtest_win_rate"), now=now,
            )
            return evaluate_lifecycle(
                status, performance,
                consecutive_failures=int(entry.get("lifecycle_consecutive_failures") or 0),
                thresholds=thresholds, now=now,
            )

        decision = judged(_entry_attribution_keys(entry))
        inherited = _predecessor_keys(entry)
        if inherited:
            # An inherited record tightens a judgement, never loosens it (PR3c, review of PR3c-1):
            # a predecessor's wins in a rolling window, or its late-closing position, must not hold
            # off a demotion the entry's own record already calls for.
            own = judged(_own_attribution_keys(entry))
            if (_RANK.get(own["new_status"], 0), own["consecutive_failures"]) > (
                    _RANK.get(decision["new_status"], 0), decision["consecutive_failures"]):
                decision = own
            decision = {**decision, "inherited_lineage_keys": sorted(inherited)}
        # The lineage judged (PR3b-2, Thomas decision 36). The pool can change between this read
        # and the locked write, and the display id may then name another lineage: the write
        # refuses this one decision rather than demote a strategy on another's record.
        decisions.append({**decision, **lineage_of(entry)})
    return decisions


# --- what the LEDGER keeps ---------------------------------------------------

def is_noteworthy(decision: Mapping[str, Any]) -> bool:
    """True when a lifecycle decision decided or flagged anything at all.

    A cycle evaluates every active strategy, so most decisions conclude "nothing to do".
    Those carry exactly one fact — *this strategy was evaluated* — and 20,020 of them on
    the live host agreed on all nine other fields (``status_changed`` false, no reasons,
    no flags, zero consecutive failures, status unchanged). Their ``created_at_utc`` is the
    cycle's own timestamp and their id is a hash of the rest, so neither is independent
    information either.

    Anything that is NOT that exact default is noteworthy and kept whole — including a
    strategy merely *approaching* demotion (``consecutive_failures`` above zero), which is
    an early warning a count would erase. The rule is deliberately "not the boring default"
    rather than a list of interesting fields, so a field added later is kept by default
    instead of silently dropped.
    """
    if decision.get("status_changed"):
        return True
    if decision.get("consecutive_failures"):
        return True
    if decision.get("reasons"):
        return True
    return any(decision.get(flag) for flag in
               ("is_escalation", "is_recovery", "new_entry_blocked",
                "requires_manual_reactivation"))


def split_for_record(decisions: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Split evaluated decisions into ``(noteworthy_in_full, unchanged_strategy_ids)``.

    The runtime keeps working with the FULL list — ``pool.apply_status_decisions`` still receives
    every decision. This only governs what is persisted."""
    noteworthy = [dict(d) for d in decisions if is_noteworthy(d)]
    quiet = [str(d.get("strategy_id")) for d in decisions if not is_noteworthy(d)]
    return noteworthy, quiet
