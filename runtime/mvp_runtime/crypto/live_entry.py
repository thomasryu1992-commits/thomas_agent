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
2d. **bar marks** (PR2a) — one entry per context per bar, and paper's post-stop-loss
   cooldown; both used to reach this leg only through paper, which applies them after the
   route it hands over is already built;
2e. **freshness** (PR2c-1, Thomas decision 24) — the plan is sized and bracketed on its bar's
   close, which for a 1d context can be most of a day old. At the moment of the decision the
   account read must be at most a minute old, the market's price (a 1m close) at most five
   minutes old, and within 50 bps of that bar close;
3. **reconciliation** — does the local book agree with the venue for this symbol
   (LP5.1: the venue is the truth; a drifted or unreadable book refuses entries);
4. **capacity** — LP5's own concurrency caps (2 open, 1 per symbol);
5. **filters** — the venue's real lot step / minimums / tick (LP5.3's reader);
6. **bracket** — the protective stop and target, rounded to the venue's tick **first** — and
   the market's price must still sit between them (PR2c-1);
7. **economics** — what a round trip costs as a share of the rounded risk
   (``cost.MAX_ENTRY_COST_R``): a stop tight enough that fees and slippage eat a quarter of
   the 1R being risked cannot be profitable at any win rate, and that is arithmetic rather
   than an estimate;
8. **sizing** — LP5.2, against the *rounded* stop, so the size matches the stop that
   would actually be placed; the cap is judged at the higher of the bar close and the
   market's price (PR2c-1);
9. **guard** — LP3's ``evaluate_live_order_guard`` on the finished intent, told the
   truthful venue exposure, with its caps judged at that same higher price.

Steps 1-5 (2b-2e included) accumulate: an operator sees every reason at once, the guard's own
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

import dataclasses
import math
from typing import Any, Mapping, Sequence

from ..coerce import as_float as _f
from .cost import MAX_ENTRY_COST_R, round_trip_cost_r, worst_case_carry_r
from .market_data import (
    REFERENCE_PRICE_MAX_AGE_SECONDS,
    reference_quote_age_seconds,
    reference_quote_problem,
)
from .paper import STOP_BEYOND_LIQUIDATION, stop_beyond_liquidation_refusal
from .execution_stage import StageStatus
from .live_order import (
    LIVE_ENTRY_BAR_ALREADY_ENTERED as BAR_ALREADY_ENTERED,
    LIVE_ENTRY_BAR_UNKNOWN as BAR_UNKNOWN,
    LIVE_ENTRY_MARKS_UNKNOWN as MARKS_UNKNOWN,
    LIVE_ENTRY_SYMBOL_IN_FLIGHT as SYMBOL_IN_FLIGHT,
    LIVE_ENTRY_STOP_LOSS_COOLDOWN as STOP_LOSS_COOLDOWN,
    MAX_ACCOUNT_AGE_SECONDS,
    MAX_CONSECUTIVE_BRACKET_FAILURES,
    account_age_seconds,
    account_fresh,
    build_live_order_intent,
    entry_context_key,
    evaluate_live_order_guard,
    live_entry_holds,
    normalize_symbols,
)
from .live_position import compute_open_notional_usdt, entry_allowed, live_capacity
from .live_sizing import RISK_PER_TRADE_FRACTION, SymbolFilters, round_price_to_tick, size_live_order
from . import pre_order_gate
from .execution_stage import PURPOSE_AUTONOMOUS
from .state import VENUE_MAINNET

LIVE_ENTRY_VERSION = "live_entry.v0.1"

# Statuses. READY is the only one an execution step may act on.
STATUS_NO_ROUTE = "NO_ROUTE"
STATUS_REFUSED = "REFUSED"
STATUS_READY = "READY"

# Refusal reasons, each naming exactly which door closed.
NO_PLAN = "LIVE_ENTRY_NO_PLAN"
VERDICT_REFUSED = "LIVE_ENTRY_VERDICT_REFUSED"
BRACKET_BREAKER_REFUSED = "LIVE_ENTRY_BRACKET_BREAKER_TRIPPED"
# The venue refused or could not answer five signed calls of one class in a row (PR2d-1).
API_BREAKER_REFUSED = "LIVE_ENTRY_API_BREAKER_TRIPPED"
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
# The same string live_route records when the book read fails — one name for one fact,
# informational there, a refusal here since the entry went fail-closed (Thomas 2026-08-30).
BOOK_UNREADABLE_REFUSED = "LIVE_ENTRY_ORDERBOOK_UNREADABLE"
SIZING_REFUSED = "LIVE_ENTRY_SIZING_REFUSED"
LIQUIDATION_REFUSED = "LIVE_ENTRY_STOP_BEYOND_LIQUIDATION"
GUARD_REFUSED = "LIVE_ENTRY_GUARD_REFUSED"
INTENT_REFUSED = "LIVE_ENTRY_INTENT_REFUSED"
# PR2c-1 (Thomas decision 24): the facts are fresh enough to act on at the moment of the decision.
ACCOUNT_STALE = "LIVE_ENTRY_ACCOUNT_STALE"
REFERENCE_PRICE_UNUSABLE = "LIVE_ENTRY_REFERENCE_PRICE_UNUSABLE"
PRICE_DIVERGED = "LIVE_ENTRY_PRICE_DIVERGED"
PRICE_BEYOND_BRACKET = "LIVE_ENTRY_PRICE_BEYOND_BRACKET"
# PR2c-2a: the risk limits in force are no longer the ones the verdict was judged on; the verdict
# names none; or none could be resolved.
RISK_LIMITS_CHANGED = "LIVE_ENTRY_RISK_LIMITS_CHANGED"
RISK_LIMITS_UNNAMED = "LIVE_ENTRY_RISK_LIMITS_UNNAMED"
RISK_LIMITS_UNRESOLVED = "LIVE_ENTRY_RISK_LIMITS_UNRESOLVED"

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
#
# And the door's own failure mode is closed too (Thomas 2026-08-30): a book that cannot be
# READ refuses the entry instead of skipping the check — see the refusal below for the
# measurement and the settle/protect carve-out. Reopens when unreadable-book refusals start
# costing real entries (the code on the cycle record makes that countable).
MAX_ENTRY_SPREAD_BPS = 50.0

# How far the market's price may have moved from the bar close the plan is sized and bracketed on
# (Thomas decisions 18 and 24, PR2c-1). The same 50 bps as the spread door above, and for a related
# reason: past it the plan describes a market that is no longer there. It is not a cost control
# either — the stop, the target and the size all stay the bar close's; this door only decides
# whether that plan may still be sent.
MAX_REFERENCE_DIVERGENCE_BPS = 50.0

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


# What to assume when the venue cannot be asked. **Deliberately NOT `paper.ASSUMED_LEVERAGE`,
# and the split is the point:** that constant answers "what is this account set to" and is
# maintained against a verified reading, while this one answers "what should I assume when I
# cannot check" — and the two have opposite failure costs. Being wrong LOW here lets through a
# live entry whose stop sits beyond a liquidation the guard failed to see; being wrong HIGH
# only refuses an entry that would have been fine. So this stays at the venue's own maximum
# default for perpetuals rather than tracking the account down, and lowering it is a decision
# about what an UNREADABLE account deserves, not about what this one is configured to.
#
# Reading the venue is the normal path and it makes this number rare; it is the answer for a
# degraded account read, not for daily operation.
UNREADABLE_ACCOUNT_LEVERAGE = 20


def configured_leverage_for(snapshot: Any | None, symbol: str) -> tuple[float, str]:
    """The leverage the venue will apply to ``symbol``, and where that number came from.

    **The fallback is the RESTRICTIVE direction, which here is the higher number.** A higher
    leverage puts the liquidation price closer to entry, so the guard refuses more — an
    unreadable account therefore keeps :data:`UNREADABLE_ACCOUNT_LEVERAGE`, never inherits
    another symbol's setting, and never resolves to something permissive. Returning the
    source alongside the value keeps a refusal auditable: "refused at 20x assumed" and
    "refused at 5x reported" are different facts about the account.

    Why read it at all: the guard used to apply one standing number in backtest, paper and
    live alike. Measured 2026-08-31 across the candidate store, that number refused 98.4% of
    the 1d tier's entries (112,358 against 1,837 closed) while risk-based sizing
    (`live_sizing.RISK_PER_TRADE_FRACTION`) never asks for more than ~1.2x of equity. On the
    live path the honest input is what the account is actually set to.
    """
    configured = getattr(snapshot, "configured_leverage", None)
    if isinstance(configured, Mapping):
        value = configured.get(symbol)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return float(value), "venue"
    return float(UNREADABLE_ACCOUNT_LEVERAGE), "assumed"


def entry_freshness(
    snapshot: Any | None,
    reference_quote: Mapping[str, Any] | None,
    *,
    plan: Mapping[str, Any],
    clock: str,
) -> dict[str, Any]:
    """What the freshness door judges (PR2c-1, decision 24), as one record. Pure.

    - ``account_fresh``: the account was read at most :data:`MAX_ACCOUNT_AGE_SECONDS` before
      ``clock``. An account that cannot say when it was read (none was) is not fresh.
    - ``reference_problem``: why the market's price cannot be used at ``clock``, or None
      (`market_data.reference_quote_problem`: the read's own refusal, or an age past
      :data:`REFERENCE_PRICE_MAX_AGE_SECONDS` at ``clock``).
    - ``divergence_bps``: how far that price is from the plan's entry, its bar close; None when
      either is unusable. ``within_divergence`` only at or under
      :data:`MAX_REFERENCE_DIVERGENCE_BPS`."""
    collected_at = getattr(snapshot, "collected_at", None) if snapshot is not None else None
    quote = dict(reference_quote) if isinstance(reference_quote, Mapping) else None
    problem = reference_quote_problem(quote, clock=clock)
    price = float(quote["price"]) if quote is not None and problem is None else None
    entry = _f(plan.get("entry_price"))
    divergence = (abs(price - entry) / entry * 10_000.0
                  if price is not None and 0 < entry < math.inf else None)
    return {
        "clock": clock,
        "account_collected_at": collected_at,
        "account_age_seconds": account_age_seconds(collected_at, clock=clock),
        "account_max_age_seconds": MAX_ACCOUNT_AGE_SECONDS,
        "account_fresh": account_fresh(collected_at, clock=clock),
        "reference_price": price,
        "reference_close_time": quote.get("close_time") if quote is not None else None,
        "reference_age_seconds": reference_quote_age_seconds(quote, clock=clock),
        "reference_max_age_seconds": REFERENCE_PRICE_MAX_AGE_SECONDS,
        "reference_problem": problem,
        "entry_price": entry,
        "divergence_bps": round(divergence, 6) if divergence is not None else None,
        "divergence_limit_bps": MAX_REFERENCE_DIVERGENCE_BPS,
        "within_divergence": divergence is not None and divergence <= MAX_REFERENCE_DIVERGENCE_BPS,
    }


def price_between_legs(direction: Any, price: Any, bracket: Mapping[str, Any]) -> bool:
    """Whether ``price`` sits strictly between the bracket's stop and target, on the side its
    direction trades from. Pure. A price that is not a positive finite number sits nowhere."""
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not (0 < price < math.inf):
        return False
    stop, target = _f(bracket.get("stop_loss")), _f(bracket.get("take_profit"))
    side = str(direction or "").upper()
    if side == "LONG":
        return stop < price < target
    if side == "SHORT":
        return target < price < stop
    return False


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
    # PR2d-1: whether the API error breaker has tripped (`live_order.api_breaker_status`). A
    # required fact, like the bracket streak: a door that forgets it opens the one this closes.
    api_breaker_tripped: bool,
    # The machine's execution stage, resolved once by the leg and threaded to the guard (PR1b).
    # No default: a caller that does not state the stage must not be able to enter. The leg reads
    # it beside the budget, so the record it stamps and the rung the guard judged are one answer.
    execution_stage: StageStatus,
    # The bar this decision was evaluated on (the feature row's `timestamp`, the bar's open time)
    # and the live entry marks as the leg read them (PR2a). No defaults, for `verdict`'s reason:
    # the doors they feed are the ones that stop a second entry on one bar and an entry inside a
    # stop-loss cooldown, and a caller that forgot them must not be the caller that skips both.
    entry_bar_time: str | None,
    entry_marks: Mapping[str, Any] | None,
    # The wall clock at the moment of the decision, and the market's price as the leg read it just
    # before (PR2c-1, decision 24). No defaults, for `verdict`'s reason: they feed the doors that
    # stop an entry priced on a market that has moved, and on an account read too long ago.
    # ``reference_quote`` is `market_data.read_reference_quote`'s record; None refuses.
    clock: str,
    reference_quote: Mapping[str, Any] | None,
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

    # 2c-2. The API error breaker (PR2d-1, Thomas decisions 18 and 27). Five signed calls of one
    # class in a row that the venue refused, could not answer, or answered unreadably. What it
    # bounds is the entry that leaves with an outcome nobody knows: the send fails, the symbol's
    # claim holds for thirty minutes, and the next fire tries again up to the daily cap. It is
    # latched — only `scripts/clear_api_breaker.py` opens it — because closes and reads keep
    # answering while the door is shut, and any success would otherwise clear it.
    if api_breaker_tripped:
        reasons.append(API_BREAKER_REFUSED)
        detail["api_breaker_tripped"] = True

    # 2d. One entry per context per bar, and the post-stop-loss cooldown (PR2a) — paper's two
    # rules, which sit below the line where paper publishes the route this leg is handed. The
    # context is the plan's own timeframe: the route was evaluated there. Each hold is its own
    # reason code (`live_order.live_entry_holds`), so the ledger says which rule held the bar.
    # With `now`, an entry another door still has in flight on this symbol holds it too (PR2b-2).
    holds = live_entry_holds(
        entry_marks, symbol=symbol, timeframe=plan.get("timeframe"), bar_time=entry_bar_time, now=now,
    )
    if holds:
        reasons.extend(holds)
        detail["entry_holds"] = holds
    detail["entry_bar"] = {
        "context_key": entry_context_key(symbol, plan.get("timeframe")),
        "symbol": symbol,
        "timeframe": plan.get("timeframe"),
        "bar_time": entry_bar_time,
    }

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

    if spread_bps is None:
        # Fail-closed for the ENTRY only (Thomas 2026-08-30). An unreadable book is
        # correlated with exactly the dislocation this guard exists for — venue stress
        # degrades data endpoints before order endpoints — and this was the one guard on
        # the money path whose failure mode was "off". Measured before deciding: 0
        # unreadable books in 33,604 recorded cycles, so the refusal costs nothing on the
        # observed record; the settle/protect path runs before the entry block and is
        # untouched, so no position can be trapped by it. The route still degrades rather
        # than raises — it records the same code and hands None down to this refusal.
        reasons.append(BOOK_UNREADABLE_REFUSED)
    elif spread_bps > MAX_ENTRY_SPREAD_BPS:
        reasons.append(SPREAD_REFUSED)
        detail["spread_bps"] = round(spread_bps, 6)
        detail["spread_limit_bps"] = MAX_ENTRY_SPREAD_BPS

    # 2e. Fresh enough to act on now (PR2c-1). Judged at ``clock``, the moment of the decision, not
    #     at ``now``, the fire's start: an entry is decided up to a minute into its fire.
    freshness = entry_freshness(snapshot, reference_quote, plan=plan, clock=clock)
    detail["freshness"] = freshness
    if not freshness["account_fresh"]:
        reasons.append(ACCOUNT_STALE)
    if freshness["reference_problem"] is not None:
        reasons.append(REFERENCE_PRICE_UNUSABLE)
    elif freshness["divergence_bps"] is not None and not freshness["within_divergence"]:
        # A plan with no usable entry price has no divergence to judge; the bracket refuses it.
        reasons.append(PRICE_DIVERGED)

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

    # 5-. The market's price must still sit between the protective legs (PR2c-1). A stop it has
    #     already crossed triggers the moment it rests — the venue refuses a closePosition stop that
    #     would, and the entry is then closed naked — and a target it has crossed means the move
    #     the plan was for is over. Rounded legs, because those are the orders that would rest.
    reference_price = freshness["reference_price"]
    if not price_between_legs(plan.get("direction"), reference_price, bracket):
        detail["price_beyond_bracket"] = {
            "reference_price": reference_price,
            "stop_loss": bracket["stop_loss"],
            "take_profit": bracket["take_profit"],
        }
        return _decision(STATUS_REFUSED, [PRICE_BEYOND_BRACKET], symbol=symbol, now=now, **detail)

    # 5a. The liquidation guard, on the rounded bracket stop — the price the venue will use,
    #     at the leverage the venue will actually apply rather than at a standing assumption.
    guard_leverage, leverage_source = configured_leverage_for(snapshot, symbol)
    detail["liquidation_leverage"] = guard_leverage
    detail["liquidation_leverage_source"] = leverage_source
    liq_refusal = stop_beyond_liquidation_refusal(
        {**dict(plan), "stop_loss": bracket["stop_loss"]},
        leverage=guard_leverage,
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
        cap_price=reference_price,
    )
    detail["sizing"] = sizing
    if not sizing["sizable"]:
        return _decision(STATUS_REFUSED, [SIZING_REFUSED], symbol=symbol, now=now, **detail)

    # 7. The intent. Carries the rounded bracket prices, so what the guard judges and what
    #    would be sent are the same numbers.
    try:
        intent = build_live_order_intent(
            # `candle_time` keys the client order id (decision 16, PR2a): two attempts on one
            # bar are one order to the venue, never two. It was the wall clock.
            {**dict(plan), "stop_loss": bracket["stop_loss"], "take_profit": bracket["take_profit"],
             "candle_time": entry_bar_time},
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
    open_notional = compute_open_notional_usdt(
        snapshot, at_cap=_f(getattr(limits, "max_open_notional_usdt", 0.0))
    )
    # What that exposure covers, for the symbol claim to judge the global caps again under its lock
    # (PR2c-3): the figure, and the positions of the book it was read beside. The entry is judged
    # only on a book the venue agrees with, so the figure is those positions' (review of #888: by
    # position, not by symbol, so one replaced on its symbol since is counted as new).
    detail["exposure_seen"] = {
        "open_notional_usdt": open_notional,
        "position_ids": sorted({str(p.get("position_id")) for p in local_positions
                                if isinstance(p, Mapping) and p.get("position_id")}),
    }
    guard = evaluate_live_order_guard(
        intent,
        gate_open=gate_open,
        runtime_active=runtime_active,
        daily_loss_breached=daily_loss_breached,
        submitted_today=submitted_today,
        current_open_notional_usdt=open_notional,
        budget_registered=budget_registered,
        allowed_symbols=allowed_symbols,
        limits=limits,
        execution_stage=execution_stage,
        reference_price=reference_price,
    )
    detail["guard"] = guard
    if not guard["approved"]:
        return _decision(STATUS_REFUSED, [GUARD_REFUSED], symbol=symbol, now=now, **detail)

    return _decision(STATUS_READY, [], symbol=symbol, now=now, **detail)


# --- what the gate re-reads (PR2c-2a) -----------------------------------------------------------

# The facts another writer can move between the leg's first read and the gate: an operator's halt or
# disarm, a re-registered budget or risk limits, a demoted stage or tier, the day's orders another
# door spent, a bracket failure another door recorded. Read again right before the gate and folded
# in by :func:`narrow_entry_facts`. The account, the book, the filters and the market price are not:
# they are what the order was sized on, and a second read would move the size by noise.
GUARD_REREAD_FIELDS = (
    "execution_stage", "runtime_active", "limits", "budget_registered", "allowed_symbols",
    "submitted_today", "daily_loss_breached",
)
REREAD_FIELDS = (*GUARD_REREAD_FIELDS, "live_routable_strategy_ids", "bracket_failures_consecutive",
                 "api_breaker_tripped")
_RISK_LIMITS_IDENTITY = ("source", "limits_id", "record_sha256")
# The caps a `LiveOrderLimits` carries; the stricter of two reads is the lower of each.
_CAP_FIELDS = ("max_order_notional_usdt", "absolute_max_notional_usdt", "max_daily_order_count",
               "max_open_notional_usdt", "daily_loss_limit_usdt")


def stricter_limits(first: Any, fresh: Any) -> Any:
    """The caps both reads allow: the lower of each, and a manual kill engaged on either. Pure.

    ``fresh`` carries everything else (the confirmation phrases). A first read that is not a
    `LiveOrderLimits` narrows nothing."""
    if not (dataclasses.is_dataclass(first) and dataclasses.is_dataclass(fresh)):
        return fresh
    changes: dict[str, Any] = {name: min(getattr(first, name), getattr(fresh, name)) for name in _CAP_FIELDS}
    changes["manual_kill_switch"] = bool(first.manual_kill_switch) or bool(fresh.manual_kill_switch)
    return dataclasses.replace(fresh, **changes)


def risk_limits_moved(judged: Any, in_force: Sequence[Any]) -> str | None:
    """Why the risk limits in force are not the ones a verdict was judged on, or None. Pure.

    ``judged`` is the verdict's ``risk_guard.limits``; ``in_force`` is what resolves now (one record
    per clock it was resolved at). Identity is the source and the record, not the numbers: a
    re-registered set with equal numbers is still a different authority."""
    if not isinstance(judged, Mapping):
        return RISK_LIMITS_UNNAMED
    wanted = tuple(judged.get(key) for key in _RISK_LIMITS_IDENTITY)
    if not in_force:
        return RISK_LIMITS_UNRESOLVED
    for record in in_force:
        if not isinstance(record, Mapping) or tuple(record.get(key) for key in _RISK_LIMITS_IDENTITY) != wanted:
            return RISK_LIMITS_CHANGED
    return None


def narrow_guard_facts(first: Mapping[str, Any], fresh: Mapping[str, Any]) -> dict[str, Any]:
    """The final guard's facts with a re-read folded in, only ever narrowing. Pure.

    - the stage is the fresh one (it is resolved on the same `now`);
    - the runtime may trade, and a valid budget backs the order, only if both reads say so;
    - the caps are the stricter of the two reads (:func:`stricter_limits`), and the symbol
      allowlist is what both reads share;
    - today's loss has breached the limit if either read says so — the fresh read judges the same
      realized figure against its own limit;
    - the day's count is the fresh one.

    A fact that improved cannot widen what is sent: the caps only tighten, every door re-runs its
    checks on these facts, and the autonomous gate refuses an order the tighter caps would size
    differently."""
    kw = dict(first)
    kw["execution_stage"] = fresh["execution_stage"]
    kw["runtime_active"] = bool(first.get("runtime_active")) and bool(fresh["runtime_active"])
    kw["limits"] = stricter_limits(first.get("limits"), fresh["limits"])
    kw["daily_loss_breached"] = bool(first.get("daily_loss_breached")) or bool(fresh["daily_loss_breached"])
    kw["budget_registered"] = bool(first.get("budget_registered")) and bool(fresh["budget_registered"])
    both = set(normalize_symbols(fresh["allowed_symbols"] or ()))
    kw["allowed_symbols"] = [s for s in normalize_symbols(first.get("allowed_symbols") or ()) if s in both]
    kw["submitted_today"] = int(fresh["submitted_today"])
    return kw


def narrow_entry_facts(first: Mapping[str, Any], fresh: Mapping[str, Any]) -> dict[str, Any]:
    """The decision's facts with the gate's re-read folded in, only ever narrowing. Pure.

    :func:`narrow_guard_facts`, and: the live tier is what both reads share; the bracket breaker is
    the higher of the two; a risk-limits problem the re-read found turns the verdict into a
    refusal. The gate re-derives the decision on the result, so an order sized on the first read
    that these facts would size differently fails its `intent_matches_decision`."""
    kw = narrow_guard_facts(first, fresh)
    first_ids, fresh_ids = first.get("live_routable_strategy_ids"), fresh["live_routable_strategy_ids"]
    kw["live_routable_strategy_ids"] = (
        None if first_ids is None or fresh_ids is None else set(first_ids) & set(fresh_ids)
    )
    kw["bracket_failures_consecutive"] = max(
        int(first.get("bracket_failures_consecutive") or 0), int(fresh["bracket_failures_consecutive"]))
    # Tripped on either read is tripped (PR2d-1): a breaker that tripped since the first read
    # shuts this entry too.
    kw["api_breaker_tripped"] = bool(first.get("api_breaker_tripped")) or bool(fresh["api_breaker_tripped"])
    problem = fresh.get("risk_limits_problem")
    if problem:
        verdict = dict(first["verdict"]) if isinstance(first.get("verdict"), Mapping) else {}
        kw["verdict"] = {**verdict, "allow_new_position": False,
                         "problems": [*(verdict.get("problems") or ()), str(problem)]}
    return kw


# --- the pre-order gate for an autonomous entry (PR2b) ------------------------------------------

# Every door of `plan_live_entry`, named, with the reason codes that mean it refused.
ENTRY_DOORS: tuple[tuple[str, frozenset[str]], ...] = (
    ("strategy_armed_live", frozenset({NOT_LIVE_ROUTABLE, LIVE_TIER_UNKNOWN})),
    ("risk_verdict_allows", frozenset({VERDICT_REFUSED})),
    ("bracket_breaker_clear", frozenset({BRACKET_BREAKER_REFUSED})),
    ("api_breaker_clear", frozenset({API_BREAKER_REFUSED})),
    ("entry_bar_open", frozenset({BAR_UNKNOWN, BAR_ALREADY_ENTERED, MARKS_UNKNOWN, STOP_LOSS_COOLDOWN})),
    ("symbol_not_in_flight", frozenset({SYMBOL_IN_FLIGHT})),
    ("book_reconciled", frozenset({RECONCILE_REFUSED})),
    ("capacity_available", frozenset({CAPACITY_REFUSED})),
    ("venue_filters_valid", frozenset({NO_FILTERS})),
    ("fixed_exit_only", frozenset({MANAGED_EXIT_REFUSED})),
    ("spread_within_limit", frozenset({BOOK_UNREADABLE_REFUSED, SPREAD_REFUSED})),
    ("account_fresh", frozenset({ACCOUNT_STALE})),
    ("reference_price_fresh", frozenset({REFERENCE_PRICE_UNUSABLE})),
    ("price_within_divergence", frozenset({PRICE_DIVERGED})),
    ("bracket_priced", frozenset({BRACKET_UNPRICEABLE})),
    ("price_between_protective_legs", frozenset({PRICE_BEYOND_BRACKET})),
    ("stop_inside_liquidation", frozenset({LIQUIDATION_REFUSED})),
    ("entry_economic", frozenset({COST_REFUSED})),
    ("order_sizable", frozenset({SIZING_REFUSED})),
    ("intent_built", frozenset({INTENT_REFUSED})),
    ("final_guard_approved", frozenset({GUARD_REFUSED})),
)
CHECK_DECISION_READY = "entry_decision_ready"
CHECK_INTENT_MATCHES_DECISION = "intent_matches_decision"
CHECK_BRACKET_MATCHES_DECISION = "bracket_matches_intent"


def _limits_facts(limits: Any) -> dict[str, Any]:
    """The caps an entry was judged against — numbers only, never the operator's phrases."""
    return {name: getattr(limits, name, None) for name in (
        "max_order_notional_usdt", "absolute_max_notional_usdt", "max_daily_order_count",
        "max_open_notional_usdt", "daily_loss_limit_usdt", "manual_kill_switch",
    )}


def gate_live_entry(
    intent: Mapping[str, Any],
    *,
    bracket: Mapping[str, Any] | None,
    decision_kwargs: Mapping[str, Any],
    profile: Mapping[str, Any],
    now: str,
) -> dict[str, Any]:
    """The pre-order gate for an autonomous entry (PR2b). Pure.

    Re-runs :func:`plan_live_entry` on ``decision_kwargs`` — the facts the leg read — and seals what
    it finds. The decision is pure, so the re-run re-verifies every door with no second copy of any
    rule. What is about to leave must be what those facts decide: the ``intent`` (a size changed
    after planning, a moved stop, another symbol) and the ``bracket`` the leg will place — its
    protective orders come from that record, not from the intent, so it is checked on its own. Either
    one drifting is a failed check rather than a trusted value. The re-derived decision's own guard
    contributes its checks by name."""
    rederived = plan_live_entry(**dict(decision_kwargs))
    ready = rederived.get("status") == STATUS_READY and rederived.get("ready") is True
    reasons = set(rederived.get("reasons") or [])
    checks = [pre_order_gate.check(CHECK_DECISION_READY, ready, None if ready else sorted(reasons))]
    for door, codes in ENTRY_DOORS:
        refused = sorted(reasons & codes)
        if ready or refused:
            # A refused decision stops at its first sequential door, so only the doors it names
            # are known; a ready one passed every door.
            checks.append(pre_order_gate.check(door, not refused, refused or None))
    guard = rederived.get("guard") if isinstance(rederived.get("guard"), Mapping) else {}
    checks.extend(dict(c) for c in guard.get("checks") or () if isinstance(c, Mapping))

    expected = rederived.get("intent") if isinstance(rederived.get("intent"), Mapping) else None
    same_order = bool(ready and expected is not None
                      and pre_order_gate.intent_fingerprint(intent) == pre_order_gate.intent_fingerprint(expected))
    checks.append(pre_order_gate.check(
        CHECK_INTENT_MATCHES_DECISION, same_order,
        None if same_order else "the order is not the one these facts decide"))
    priced = rederived.get("bracket") if isinstance(rederived.get("bracket"), Mapping) else None
    bracket_agrees = bool(ready and priced is not None and isinstance(bracket, Mapping)
                          and dict(bracket) == dict(priced))
    checks.append(pre_order_gate.check(
        CHECK_BRACKET_MATCHES_DECISION, bracket_agrees,
        None if bracket_agrees else "the protective orders are not the ones the decision priced"))

    kw = decision_kwargs
    verdict = kw.get("verdict") if isinstance(kw.get("verdict"), Mapping) else {}
    marks = kw.get("entry_marks") if isinstance(kw.get("entry_marks"), Mapping) else None
    plan_timeframe = (kw.get("plan") or {}).get("timeframe") if isinstance(kw.get("plan"), Mapping) else None
    context = entry_context_key(kw.get("symbol"), plan_timeframe)
    reconciliation = kw.get("reconciliation") if isinstance(kw.get("reconciliation"), Mapping) else {}
    filters = kw.get("filters")
    facts = {
        "equity_usdt": kw.get("equity_usdt"),
        "spread_bps": kw.get("spread_bps"),
        "submitted_today": kw.get("submitted_today"),
        "daily_loss_breached": kw.get("daily_loss_breached"),
        "runtime_active": kw.get("runtime_active"),
        "gate_open": kw.get("gate_open"),
        "budget_registered": kw.get("budget_registered"),
        "bracket_failures_consecutive": kw.get("bracket_failures_consecutive"),
        "api_breaker_tripped": kw.get("api_breaker_tripped"),
        "allowed_symbols": list(kw.get("allowed_symbols") or ()),
        "entry_bar_time": kw.get("entry_bar_time"),
        # The moment the decision was judged at (PR2c-1); the gate seals it as `decided_at`.
        "clock": kw.get("clock"),
        # What the bar and in-flight doors judged, for this context and symbol only (PR2b-2 review).
        "entry_marks": None if marks is None else {
            "entered": (marks.get("entered") or {}).get(context),
            "cooldown": (marks.get("cooldown") or {}).get(context),
            "in_flight": (marks.get("in_flight") or {}).get(str(kw.get("symbol") or "")),
        },
        "local_positions": len(kw.get("local_positions") or ()),
        "reconcile_status": reconciliation.get("status"),
        "verdict": {"status": verdict.get("status"),
                    "allow_new_position": verdict.get("allow_new_position"),
                    "problems": list(verdict.get("problems") or ())},
        "limits": _limits_facts(kw.get("limits")),
        "filters": ({name: getattr(filters, name, None)
                     for name in ("step_size", "min_qty", "min_notional", "tick_size")}
                    if filters is not None else None),
        "decision": {key: rederived.get(key) for key in (
            "status", "reasons", "capacity", "bracket", "sizing", "round_trip_cost_r",
            "worst_case_carry_r", "liquidation_leverage", "liquidation_leverage_source",
            "exit_terms", "entry_bar", "freshness",
        )},
        "guard": {key: guard.get(key) for key in (
            "status", "notional_usdt", "order_notional_usdt", "reference_price",
            "effective_cap_usdt", "open_exposure_cap_usdt",
            "current_open_notional_usdt", "submitted_today", "max_daily_order_count",
            "daily_loss_limit_usdt", "daily_loss_breached",
        )},
    }
    lineage = {field: intent.get(field) for field in (
        "strategy_id", "candidate_id", "strategy_rule_hash", "strategy_generation_id",
        "timeframe", "candle_time", "order_intent_id", "idempotency_key", "client_order_id",
    )}
    return pre_order_gate.evaluate_pre_order_gate(
        intent, purpose=PURPOSE_AUTONOMOUS, venue=VENUE_MAINNET, checks=checks,
        profile=profile, lineage=lineage, facts=facts, now=now, decided_at=kw.get("clock"),
    )


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
    "ACCOUNT_STALE",
    "BAR_ALREADY_ENTERED",
    "BAR_UNKNOWN",
    "API_BREAKER_REFUSED",
    "BRACKET_BREAKER_REFUSED",
    "BRACKET_UNPRICEABLE",
    "BRACKET_WORKING_TYPE",
    "CAPACITY_REFUSED",
    "COST_REFUSED",
    "GUARD_REFUSED",
    "INTENT_REFUSED",
    "LIVE_ENTRY_VERSION",
    "MARKS_UNKNOWN",
    "MAX_REFERENCE_DIVERGENCE_BPS",
    "NO_FILTERS",
    "NO_PLAN",
    "PRICE_BEYOND_BRACKET",
    "PRICE_DIVERGED",
    "RECONCILE_REFUSED",
    "REFERENCE_PRICE_UNUSABLE",
    "GUARD_REREAD_FIELDS",
    "REREAD_FIELDS",
    "RISK_LIMITS_CHANGED",
    "RISK_LIMITS_UNNAMED",
    "RISK_LIMITS_UNRESOLVED",
    "SIZING_REFUSED",
    "STATUS_NO_ROUTE",
    "STATUS_READY",
    "STATUS_REFUSED",
    "STOP_LOSS_COOLDOWN",
    "SYMBOL_IN_FLIGHT",
    "VERDICT_REFUSED",
    "ENTRY_DOORS",
    "entry_freshness",
    "entry_status_line",
    "gate_live_entry",
    "narrow_entry_facts",
    "narrow_guard_facts",
    "plan_live_entry",
    "price_between_legs",
    "price_bracket",
    "risk_limits_moved",
    "stricter_limits",
]
