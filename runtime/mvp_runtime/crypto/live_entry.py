"""LP5.3 — the live entry **decision**. Decides everything; sends nothing.

This is the assembly LP5 was missing: the pieces LP5.1 and LP5.2 built (the position book,
reconciliation, truthful exposure, sizing) plus the pre-existing LP3 intent + final guard,
run in one order, on one set of facts, producing one auditable answer to *should this
context open a live position, and at what size and with what protective bracket?*

**It cannot send an order.** It takes no adapter, imports no adapter, and returns a plan —
the egress step (LP5.3's execution increment) is a separate module that takes this record
and the guard verdict inside it. Splitting decide from send is what lets every refusal path
here be tested exhaustively with no venue, no grant, and no key.

Order of checks, and why:

1. **route** — is there an entry candidate at all;
2. **verdict** — did the C4 guards allow a new position this cycle (**required**: an absent or
   malformed verdict refuses, it does not skip the check);
2b. ~~**live candidate (Gate 0)**~~ — **removed 2026-08-03.** Gate 0 is defined by
   ``CRYPTO_LIVE_EXECUTION_V0.1.md`` as an item on the **operator** go-live checklist, under
   a heading reading "Every step is Thomas's". #409 wired the computed
   ``live_candidate_eligible`` in here as a refusal, which turned a checklist item into a
   pool-wide aggregate that refuses every strategy's entries for any one strategy's record.
   Measured 2026-08-03, that aggregate could not be satisfied at all: the routable set is
   whichever batch was promoted last, promotions land every 1-3 days, and the sample it needs
   (20) takes ~32 days of a frozen pool to accrue — so the gate's only reachable state was the
   operator acknowledgement that #413 added to override it. A gate whose sole satisfiable
   state is "overridden" is a signature requirement wearing an evidence gate's clothes.
   Design and measurement: ``docs/proposals/GATE0_CANNOT_BE_SATISFIED_V0.1.md``. The
   measurement is not deleted — ``feedback.live_candidate_eligible`` is still computed and
   still reported; what is deleted is its authority over this door;
3. **reconciliation** — does the local book agree with the venue for this symbol
   (LP5.1: the venue is the truth; a drifted or unreadable book refuses entries);
4. **capacity** — LP5's own concurrency caps (2 open, 1 per symbol);
5. **filters** — the venue's real lot step / minimums / tick (LP5.3's reader);
6. **bracket** — the protective stop and target, rounded to the venue's tick **first**;
7. **economics** — what a round trip costs as a share of the rounded risk
   (``cost.MAX_ENTRY_COST_R``): a stop tight enough that fees and slippage eat a quarter of
   the 1R being risked cannot be profitable at any win rate, and that is arithmetic rather
   than an estimate;
8. **sizing** — LP5.2, against the *rounded* stop, so the size matches the stop that
   would actually be placed;
9. **guard** — LP3's ``evaluate_live_order_guard`` on the finished intent, told the
   truthful venue exposure.

Steps 1-5 (2b included) accumulate: an operator sees every reason at once, the guard's own
posture.
Steps 6-9 are sequential because each consumes the previous one's output, and a step that
cannot run is reported as the refusal it is rather than skipped.

**Rounding both bracket legs toward the entry** is the one arithmetic decision worth
stating. A tick-rounded stop that drifts *away* from the entry silently increases the risk
the size was computed for; rounding toward the entry can only make the realised risk
smaller than planned. The same direction on the target takes profit no later than planned.
After rounding, a stop that no longer sits strictly on its own side of the entry is a
**refusal**, never a repair.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ..coerce import as_float as _f
from .cost import MAX_ENTRY_COST_R, round_trip_cost_r, worst_case_carry_r
from .paper import STOP_BEYOND_LIQUIDATION, stop_beyond_liquidation_refusal
from .live_order import (
    MAX_CONSECUTIVE_BRACKET_FAILURES,
    build_live_order_intent,
    evaluate_live_order_guard,
)
from .live_position import compute_open_notional_usdt, entry_allowed, live_capacity
from .live_sizing import RISK_PER_TRADE_FRACTION, SymbolFilters, round_price_to_tick, size_live_order

LIVE_ENTRY_VERSION = "live_entry.v0.1"

# Statuses. READY is the only one an execution step may act on.
STATUS_NO_ROUTE = "NO_ROUTE"
STATUS_REFUSED = "REFUSED"
STATUS_READY = "READY"

# Refusal reasons, each naming exactly which door closed.
NO_PLAN = "LIVE_ENTRY_NO_PLAN"
VERDICT_REFUSED = "LIVE_ENTRY_VERDICT_REFUSED"
BRACKET_BREAKER_REFUSED = "LIVE_ENTRY_BRACKET_BREAKER_TRIPPED"
RECONCILE_REFUSED = "LIVE_ENTRY_RECONCILE_REFUSED"
CAPACITY_REFUSED = "LIVE_ENTRY_CAPACITY_REFUSED"
NO_FILTERS = "LIVE_ENTRY_NO_VENUE_FILTERS"
# #610 Part 1. Two codes, not one, because they are different operator problems: the strategy is
# deliberately not armed for live (expected, and cleared at the promotion door), versus the
# runtime could not read which strategies are armed (a fault, and the pool read is where to look).
NOT_LIVE_ROUTABLE = "LIVE_ENTRY_STRATEGY_NOT_LIVE_ROUTABLE"
LIVE_TIER_UNKNOWN = "LIVE_ENTRY_LIVE_TIER_UNKNOWN"
BRACKET_UNPRICEABLE = "LIVE_ENTRY_BRACKET_UNPRICEABLE"
COST_REFUSED = "LIVE_ENTRY_COST_REFUSED"
# A plan whose stop MOVES after entry, which this leg cannot execute.
#
# The live stop is a `closePosition` STOP_MARKET placed once at entry and cancelled on close;
# there is no amend path, and `cancel_bracket_legs` exists to remove a leg, not to reprice one.
# So a spec carrying `breakeven_at_r` or `trail_atr` would trade live on a stop that never
# moves while its backtest evidence was built on one that does — the exact backtest/live exit
# divergence `max_holding_bars` rides into the plan to prevent, and in the direction that
# matters most: the evidence would claim a risk profile the money path does not have.
#
# Refused rather than silently degraded to a fixed stop. Degrading would trade a strategy
# nobody scored; refusing costs nothing today (the factory mints no such spec yet) and turns
# into a visible, named blocker the moment one is promoted. Closing it means cancel/replace of
# a live protective order on every bar, which is a change to the money path and a separate
# decision with its own approval.
MANAGED_EXIT_REFUSED = "LIVE_ENTRY_MANAGED_EXIT_UNSUPPORTED"
SPREAD_REFUSED = "LIVE_ENTRY_SPREAD_TOO_WIDE"
SIZING_REFUSED = "LIVE_ENTRY_SIZING_REFUSED"
LIQUIDATION_REFUSED = "LIVE_ENTRY_STOP_BEYOND_LIQUIDATION"
GUARD_REFUSED = "LIVE_ENTRY_GUARD_REFUSED"
INTENT_REFUSED = "LIVE_ENTRY_INTENT_REFUSED"

# A dislocation breaker, NOT a cost control — kept at 50 deliberately, Thomas 2026-08-22, after
# the number was measured and found to be ~15x the widest spread this venue has shown.
#
# The measurement (3,806 book samples over the six-symbol universe) is what makes the choice a
# decision rather than an oversight: per-symbol medians run 0.015 bps (BTCUSDT) to 1.42 bps
# (DOGEUSDT) — a hundredfold spread — while each symbol's own max sits only 1.5-3.3x above its
# median, and the widest reading anywhere was 3.2 bps. So no absolute limit between 5 and 50
# would have refused a single entry, and the reason to prefer the loose end is that **entry
# economics are already owned elsewhere**: `MAX_ENTRY_COST_R` prices the friction against the R
# being risked and refuses on that basis. A second, tighter cost door here would be a competing
# authority over one question, which is the failure this package warns about everywhere else.
#
# What is left for this door is the case the cost model cannot see: a book so wide that the
# quote is not a market. At 50 bps every symbol above is 15x-3000x its own normal, so a reading
# that trips this is a liquidity event on any of them.
#
# Reopens when: a symbol whose ordinary spread is a material fraction of 50 bps joins the
# universe — the hundredfold span above is the warning that one could — or realized slippage on
# live entries shows the cost door letting through fills this would have caught.
MAX_ENTRY_SPREAD_BPS = 50.0

# Which venue price the protective orders trigger on. MARK_PRICE rather than the last
# traded price: a stop that triggers on a single wick print on one venue's tape is the
# classic way to be stopped out of a position that never actually moved.
BRACKET_WORKING_TYPE = "MARK_PRICE"


def price_bracket(
    plan: Mapping[str, Any], filters: SymbolFilters
) -> tuple[dict[str, Any] | None, str | None]:
    """The protective stop and target, rounded to the venue's tick. Pure.

    Both legs round **toward the entry** (see the module docstring): for a LONG the stop
    sits below the entry so it rounds up, and the target above so it rounds down; a SHORT
    mirrors that. Returns ``(bracket, None)`` or ``(None, reason)``.

    Refuses — rather than repairing — when a rounded leg lands on or past the entry. That
    happens on a symbol whose tick is coarse relative to the planned distance, and the
    honest answer there is "this plan cannot be protected at this venue's granularity",
    not a stop nudged to a price the strategy never chose.
    """
    entry = _f(plan.get("entry_price"))
    stop = _f(plan.get("stop_loss"))
    target = _f(plan.get("take_profit"))
    direction = str(plan.get("direction") or "").upper()
    tick = filters.tick_size

    if entry <= 0 or stop <= 0 or target <= 0 or tick <= 0 or direction not in {"LONG", "SHORT"}:
        return None, BRACKET_UNPRICEABLE

    if direction == "LONG":
        stop_rounded = round_price_to_tick(stop, tick, mode="up")
        target_rounded = round_price_to_tick(target, tick, mode="down")
        ok = 0 < stop_rounded < entry < target_rounded
        stop_side, target_side = "SELL", "SELL"
    else:
        stop_rounded = round_price_to_tick(stop, tick, mode="down")
        target_rounded = round_price_to_tick(target, tick, mode="up")
        ok = 0 < target_rounded < entry < stop_rounded
        stop_side, target_side = "BUY", "BUY"

    if not ok:
        return None, BRACKET_UNPRICEABLE

    return {
        "stop_loss": stop_rounded,
        "take_profit": target_rounded,
        # The per-unit distance the size is computed from, AFTER rounding — so the
        # quantity corresponds to the stop that would actually be placed at the venue.
        "risk_per_unit": round(abs(entry - stop_rounded), 12),
        "stop_side": stop_side,
        "take_profit_side": target_side,
        "working_type": BRACKET_WORKING_TYPE,
        "tick_size": tick,
    }, None


def plan_live_entry(
    plan: Mapping[str, Any] | None,
    *,
    symbol: str,
    reconciliation: Mapping[str, Any],
    local_positions: list[Mapping[str, Any]],
    snapshot: Any | None,
    filters: SymbolFilters | None,
    limits: Any,
    budget_registered: bool,
    gate_open: bool,
    runtime_active: bool,
    daily_loss_breached: bool,
    clean_canary_orders: int,
    submitted_today: int,
    equity_usdt: float,
    now: str,
    # No default. It used to be `= None`, skipped when absent — which silently opened the C4
    # door: a caller that forgot the verdict got an entry the data-health and risk guards never
    # saw. That is the same fail-open class as `current_open_notional_usdt = 0.0`, which LP5.1c
    # closed, and it was worse in one way: the test helper omitted it too, so the untested
    # branch was the *guarded* one. A missing or malformed verdict now refuses.
    verdict: Mapping[str, Any],
    # #610 Part 1 — the ids the pool says may open a REAL position. No default, and `None`
    # refuses: see the door at 2a.
    live_routable_strategy_ids: set[str] | None,
    # How many live entries in a row filled and could not be protected. No default, for the two
    # reasons above and one of its own: this door exists because the runtime placed two live
    # entries whose protective stop was refused and re-entered on the next signal regardless.
    # A gate with a permissive default is a gate the caller that forgot it never meets, and the
    # caller that would forget this one is the autonomous leg.
    bracket_failures_consecutive: int,
    # The registered budget's symbol allowlist, threaded to the guard. Empty blocks every
    # symbol, so a caller that does not state the scope cannot authorize an entry outside it —
    # the same fail-closed default the guard gives `budget_registered`.
    allowed_symbols: Sequence[str] = (),
    filters_reason: str | None = None,
    risk_fraction: float = RISK_PER_TRADE_FRACTION,
    spread_bps: float | None = None,
) -> dict[str, Any]:
    """Decide one live entry, or refuse it. Pure: no I/O, no venue, no order.

    Every runtime fact arrives as an argument — the caller reads the account, the control
    state, the budget and the book once and passes them in, so this function is exhaustively
    testable and so the same facts drive every check rather than each door re-reading and
    possibly disagreeing.

    Returns a decision record. ``status`` is ``READY`` only when the final guard approved;
    the record then carries ``intent``, ``sizing``, ``bracket`` and ``guard`` — everything
    an execution step needs and nothing it may re-derive for itself.
    """
    reasons: list[str] = []
    detail: dict[str, Any] = {}

    # 1. Is there anything to trade?
    if not isinstance(plan, Mapping) or not plan:
        return _decision(STATUS_NO_ROUTE, [NO_PLAN], symbol=symbol, now=now)

    # The exit terms travel from the plan to the position record through this decision, for the
    # same "read once, share it" reason every other fact here does — the alternative is the
    # execution step reaching back into the plan and the two disagreeing about which spec's
    # numbers applied. `max_holding_bars` in particular must be the value THIS entry was
    # planned under: it is what the position is judged by for its whole life, so re-reading a
    # spec that may have been edited mid-hold would move the exit of an already-open trade.
    detail["exit_terms"] = {
        "timeframe": plan.get("timeframe"),
        "max_holding_bars": plan.get("max_holding_bars"),
    }

    # 2a. **May THIS strategy spend money?** The question slot 2b used to ask, asked in a form
    # that can be satisfied. Gate 0 was a pool-wide aggregate and was unsatisfiable; this is a
    # per-strategy permission the operator grants at the promotion door and nothing else can
    # move — see `pool.live_routable_strategy_ids` for why it is a field rather than a status.
    #
    # `None` refuses. A caller that could not determine the live-routable set is a caller that
    # does not know whether this strategy may trade, and the parameter has no default for the
    # same reason `verdict` has none: the last time a door here was skippable-when-absent, the
    # test helper omitted it too and the untested branch was the guarded one.
    #
    # Placed with the accumulating doors rather than before them so an operator reading a
    # refusal sees every closed door at once, and placed AFTER the plan exists so a strategy
    # with no route is still reported as NO_ROUTE rather than as a permission problem.
    strategy_id = str(plan.get("strategy_id") or "")
    if live_routable_strategy_ids is None:
        reasons.append(LIVE_TIER_UNKNOWN)
    elif strategy_id not in live_routable_strategy_ids:
        reasons.append(NOT_LIVE_ROUTABLE)
        detail["live_tier"] = {
            "strategy_id": strategy_id or None,
            "live_routable_count": len(live_routable_strategy_ids),
        }

    # 2-4. The cheap doors, accumulated so a refusal names every closed one at once.
    if not isinstance(verdict, Mapping) or not bool(verdict.get("allow_new_position")):
        # A malformed verdict is refused rather than ignored: "I could not read the guards" and
        # "the guards said no" have the same correct consequence here.
        reasons.append(VERDICT_REFUSED)
        detail["verdict_problems"] = (
            list(verdict.get("problems") or []) if isinstance(verdict, Mapping) else ["verdict missing or malformed"]
        )

    # 2b was Gate 0 and is gone; see the docstring. The bracket breaker below kept its 2c
    # numbering deliberately — the reason codes it emits are in ledger rows going back weeks,
    # and renumbering a door to close a gap in a comment would make those rows harder to read
    # for no gain.

    # 2c. The bracket breaker. An entry that fills and cannot be protected is closed again
    # safely, so no single one of these is an emergency — and that is exactly why the loop ran
    # unbounded: each iteration ends tidily, costs only fees, and looks like nothing happened.
    # What it means in aggregate is that this runtime currently cannot hold a position, and
    # re-entering on the next signal spends money to learn that again. The streak is counted
    # durably (`live_order.read_bracket_failures`) and only a resting bracket or an operator
    # clears it; it deliberately survives the UTC midnight that refills the order budget.
    if bracket_failures_consecutive >= MAX_CONSECUTIVE_BRACKET_FAILURES:
        reasons.append(BRACKET_BREAKER_REFUSED)
        detail["bracket_failures_consecutive"] = bracket_failures_consecutive
        detail["bracket_failure_limit"] = MAX_CONSECUTIVE_BRACKET_FAILURES

    if not entry_allowed(reconciliation, symbol):
        reasons.append(RECONCILE_REFUSED)
        detail["reconcile_status"] = (
            reconciliation.get("status") if isinstance(reconciliation, Mapping) else None
        )

    capacity = live_capacity(list(local_positions), symbol=symbol)
    if not capacity["allowed"]:
        reasons.append(CAPACITY_REFUSED)
    detail["capacity"] = capacity

    if filters is None or not filters.valid() or filters.tick_size <= 0:
        reasons.append(NO_FILTERS)
        detail["filters_reason"] = filters_reason

    # Read off the PLAN, not the spec: the plan is what this leg executes, and a spec whose
    # management rules failed to ride into it would pass a spec-side check and still trade the
    # wrong exit. Either field present means the stop is meant to move.
    managed = {
        key: plan.get(key) for key in ("breakeven_at_r", "trail_distance") if plan.get(key) is not None
    }
    if managed:
        reasons.append(MANAGED_EXIT_REFUSED)
        detail["managed_exit"] = managed

    if spread_bps is not None and spread_bps > MAX_ENTRY_SPREAD_BPS:
        reasons.append(SPREAD_REFUSED)
        detail["spread_bps"] = round(spread_bps, 6)
        detail["spread_limit_bps"] = MAX_ENTRY_SPREAD_BPS

    if reasons:
        return _decision(STATUS_REFUSED, reasons, symbol=symbol, now=now, **detail)

    assert filters is not None  # narrowed above

    # 5. The protective bracket, priced before the size so the size matches the real stop.
    bracket, bracket_reason = price_bracket(plan, filters)
    if bracket is None:
        return _decision(
            STATUS_REFUSED, [bracket_reason or BRACKET_UNPRICEABLE], symbol=symbol, now=now, **detail
        )
    detail["bracket"] = bracket

    # 5a. The liquidation guard, on the rounded bracket stop — the price the venue will use.
    liq_refusal = stop_beyond_liquidation_refusal(
        {**dict(plan), "stop_loss": bracket["stop_loss"]},
    )
    if liq_refusal is not None:
        detail["liquidation_refusal"] = liq_refusal
        return _decision(STATUS_REFUSED, [LIQUIDATION_REFUSED], symbol=symbol, now=now, **detail)

    # 5b. The economics door, and it belongs HERE rather than beside the cheap checks above:
    #     the friction is a share of the risk, and the risk that will actually apply is the
    #     tick-rounded one this bracket just produced. Rounding moves the stop TOWARD the entry
    #     (see the module docstring), so it can only shrink the risk and therefore only raise
    #     the cost share — checking the unrounded plan would clear trades the venue's own
    #     granularity had made worse.
    #
    #     `run_paper_update` refuses the same plan on the same rule. This is not a duplicate
    #     check: the live leg is handed the shared ROUTE, not the paper step's refusal, so a
    #     paper entry declined here for cost would otherwise still reach a real order.
    cost_r = round_trip_cost_r(
        str(plan.get("direction") or "").upper(), _f(plan.get("entry_price")), _f(bracket["risk_per_unit"])
    )
    detail["round_trip_cost_r"] = round(cost_r, 6) if math.isfinite(cost_r) else "inf"
    # The cost this door does NOT price, recorded beside the one it does. `round_trip_cost_r`
    # is `apply_cost_model` at `exit_price == entry_price` — it prices no time, so carry
    # reaches the record only at settlement, and until now a plan could clear the economics
    # door and lose to carry with nothing saying the door had not looked.
    #
    # Reported, never added to `cost_r`. The cap bounds a cost every trade pays in full; this
    # is the carry a trade would pay holding to its own limit, and the median hold on this
    # store is 5-27% of that limit. Adding it would refuse 10 of 18 4h trades on a cost they
    # do not incur. See `cost.worst_case_carry_r` for the measurement.
    detail["worst_case_carry_r"] = worst_case_carry_r(
        str(plan.get("direction") or ""),
        _f(plan.get("entry_price")) or 0.0,
        _f(bracket["risk_per_unit"]) or 0.0,
        timeframe=plan.get("timeframe"),
        max_holding_bars=plan.get("max_holding_bars"),
    )
    if cost_r > MAX_ENTRY_COST_R:
        detail["cost_limit_r"] = MAX_ENTRY_COST_R
        return _decision(STATUS_REFUSED, [COST_REFUSED], symbol=symbol, now=now, **detail)

    # 6. Sizing, against the ROUNDED stop distance.
    sizing = size_live_order(
        {**dict(plan), "stop_loss": bracket["stop_loss"], "risk": bracket["risk_per_unit"]},
        equity_usdt=equity_usdt,
        max_order_notional_usdt=_f(getattr(limits, "max_order_notional_usdt", 0.0)),
        filters=filters,
        risk_fraction=risk_fraction,
    )
    detail["sizing"] = sizing
    if not sizing["sizable"]:
        return _decision(STATUS_REFUSED, [SIZING_REFUSED], symbol=symbol, now=now, **detail)

    # 7. The intent. Carries the rounded bracket prices, so what the guard judges and what
    #    would be sent are the same numbers.
    try:
        intent = build_live_order_intent(
            {**dict(plan), "stop_loss": bracket["stop_loss"], "take_profit": bracket["take_profit"]},
            symbol=symbol,
            quantity=sizing["quantity"],
            notional_usdt=sizing["notional_usdt"],
            now=now,
        )
    except Exception as exc:  # noqa: BLE001 — a malformed plan is a refusal, not a crash
        detail["intent_error"] = getattr(exc, "reason_code", type(exc).__name__)
        return _decision(STATUS_REFUSED, [INTENT_REFUSED], symbol=symbol, now=now, **detail)
    detail["intent"] = intent

    # 8. The final guard, told the truthful venue exposure. An unreadable account reports
    #    exposure AT the cap (LP5.1), so the guard refuses rather than admits.
    guard = evaluate_live_order_guard(
        intent,
        gate_open=gate_open,
        runtime_active=runtime_active,
        daily_loss_breached=daily_loss_breached,
        clean_canary_orders=clean_canary_orders,
        submitted_today=submitted_today,
        current_open_notional_usdt=compute_open_notional_usdt(
            snapshot, at_cap=_f(getattr(limits, "max_open_notional_usdt", 0.0))
        ),
        budget_registered=budget_registered,
        allowed_symbols=allowed_symbols,
        limits=limits,
    )
    detail["guard"] = guard
    if not guard["approved"]:
        return _decision(STATUS_REFUSED, [GUARD_REFUSED], symbol=symbol, now=now, **detail)

    return _decision(STATUS_READY, [], symbol=symbol, now=now, **detail)


def _decision(status: str, reasons: list[str], *, symbol: str, now: str, **detail: Any) -> dict[str, Any]:
    return {
        "live_entry_version": LIVE_ENTRY_VERSION,
        "status": status,
        # The single boolean an execution step reads. False on every path but one, and it
        # is derived from the guard's own `approved`, never asserted independently.
        "ready": status == STATUS_READY and bool((detail.get("guard") or {}).get("approved")),
        "symbol": symbol,
        "reasons": reasons,
        "created_at": now,
        **detail,
    }


def entry_status_line(decision: Mapping[str, Any]) -> str:
    """One ASCII line for the console (Windows consoles are cp949)."""
    parts = [f"live_entry {decision.get('symbol')}: {decision.get('status')}"]
    reasons = decision.get("reasons") or []
    if reasons:
        parts.append("(" + ",".join(str(r) for r in reasons) + ")")
    sizing = decision.get("sizing")
    if isinstance(sizing, Mapping) and sizing.get("sizable"):
        parts.append(f"qty={sizing['quantity']} notional={sizing['notional_usdt']}")
    guard = decision.get("guard")
    if isinstance(guard, Mapping):
        parts.append(f"guard={guard.get('status')}")
    return " ".join(parts)


__all__ = [
    "BRACKET_BREAKER_REFUSED",
    "BRACKET_UNPRICEABLE",
    "BRACKET_WORKING_TYPE",
    "CAPACITY_REFUSED",
    "COST_REFUSED",
    "GUARD_REFUSED",
    "INTENT_REFUSED",
    "LIVE_ENTRY_VERSION",
    "NO_FILTERS",
    "NO_PLAN",
    "RECONCILE_REFUSED",
    "SIZING_REFUSED",
    "STATUS_NO_ROUTE",
    "STATUS_READY",
    "STATUS_REFUSED",
    "VERDICT_REFUSED",
    "entry_status_line",
    "plan_live_entry",
    "price_bracket",
]
