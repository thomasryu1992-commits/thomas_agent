"""LP5.2 — how large a live order may be. Pure arithmetic: no I/O, no venue, no gate.

``size_live_order`` is the one genuinely new piece of LP5 with no paper precedent (paper
trades in R, not in contracts). It answers a single question — *given this plan, this
account, and this registered budget, what quantity may be ordered?* — and it answers
"none" whenever it cannot answer honestly. Design: decision 2 of
``docs/runtime-contracts/LP5_POSITION_KERNEL_DESIGN_V0.1.md``.

    size = min(risk-based candidate, budget candidate), floored to the venue's lot step

**Why ``min()`` here is not the "clamping" the repo forbids.** What is forbidden is the
*guard* silently resizing an approved order, which would desync the size from the decision
that approved it. Here the size is decided **before** the guard, and
``evaluate_live_order_guard`` then independently verifies the final notional against the
per-order cap and the absolute ceiling. Two independent checks agreeing by construction,
not one check quietly rewriting the other's input.

**Venue filters are an input, never a memory.** Lot step, minimum quantity, minimum
notional, and price tick differ per symbol and change over time; the design record is
explicit that they must come from the venue's ``exchangeInfo`` and not be implemented from
memory. So this module takes them as :class:`SymbolFilters` and **refuses when they are
absent** — an unrounded size is rejected by the venue outright, and guessing a step here
would be inventing the one number nobody may invent. LP5.3 supplies real filters from a
venue read; until then every call refuses, which is the honest state.

**Refuse rather than default**, on every unhappy path: no equity, no stop distance, no
filters, a floored size of zero, below the venue's minimums, or a result that would still
breach the budget. ``build_live_order_intent``'s posture — the cap is a ceiling, never a
size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from ..coerce import as_float as _f

SIZING_VERSION = "live_sizing.v0.1"

# Fraction of usable equity risked on one trade. A module constant, not a budget field:
# the registered budget's job is the USDT caps the operator signed off and the guard
# verifies, and this fraction can only ever make an order SMALLER than those caps (the
# final size is the min of the two candidates). It therefore cannot widen anything the
# operator registered. Making it operator-tunable means a `live_trading_budget` schema bump
# and a re-registration — a deliberate governance step, not a constant edit, and worth
# doing only once live sizing has actually been exercised.
RISK_PER_TRADE_FRACTION = 0.01

# Refusal reasons. Each names exactly what was missing or breached, so a refusal is
# actionable rather than a bare "no".
NO_EQUITY = "SIZING_NO_EQUITY"
NO_RISK_DISTANCE = "SIZING_NO_RISK_DISTANCE"
NO_ENTRY_PRICE = "SIZING_NO_ENTRY_PRICE"
NO_FILTERS = "SIZING_NO_VENUE_FILTERS"
NO_BUDGET_CAP = "SIZING_NO_BUDGET_CAP"
BELOW_MIN_QTY = "SIZING_BELOW_VENUE_MIN_QTY"
BELOW_MIN_NOTIONAL = "SIZING_BELOW_VENUE_MIN_NOTIONAL"
ROUNDS_TO_ZERO = "SIZING_ROUNDS_TO_ZERO"
EXCEEDS_BUDGET = "SIZING_EXCEEDS_BUDGET_CAP"
ABOVE_MAX_QTY = "SIZING_ABOVE_VENUE_MAX_QTY"


@dataclass(frozen=True)
class SymbolFilters:
    """The venue's per-symbol trading rules, as read from ``exchangeInfo``.

    Never constructed from constants in this repository. ``step_size`` is the LOT_SIZE
    quantity increment, ``min_qty`` its floor, ``min_notional`` the MIN_NOTIONAL filter,
    and ``tick_size`` the PRICE_FILTER increment (used for the LP5.3 bracket's stop and
    target prices, which the venue rejects unrounded exactly as it rejects an unrounded
    quantity). ``max_qty`` is the lot ceiling; ``0.0`` means the reader did not learn one,
    so it is simply not checked rather than treated as a limit of zero.

    LP5.3's reader (``live_filters``) folds MARKET_LOT_SIZE into these, taking the
    stricter of the two lot filters, because LP5 entries and closes are MARKET orders.
    """

    step_size: float
    min_qty: float
    min_notional: float
    tick_size: float = 0.0
    max_qty: float = 0.0

    def valid(self) -> bool:
        """A filter set is usable only if the increments are positive. A zero step would
        make ``floor_to_step`` a division by zero, and a zero minimum would let a dust
        order through — both are "unknown", not "unrestricted"."""
        return self.step_size > 0 and self.min_qty > 0 and self.min_notional > 0


def _exact_multiple(count: int, increment: float) -> float:
    """``count * increment`` without the binary-float residue.

    The residue is why this is not a plain multiply. ``count * increment`` carries an error of
    roughly ``value * 2**-52``, and ``round(..., 12)`` — what both callers below used to end on —
    erases it only while that error stays under the twelfth decimal. Above ``1e-12 * 2**52``,
    about 4.5e3, it does not: ``640001 * 0.1`` is ``64000.100000000006``, which survives the
    round, reaches ``urlencode`` as its own repr, and is refused by the venue as **-1111
    Precision is over the maximum defined for this asset**.

    Measured on the live account 2026-08-18T14:28:52Z: a BTCUSDT ``STOP_MARKET`` leg rejected on
    exactly this, the entry already filled, and `live_leg` rule 2 closing the naked position —
    40% of BTCUSDT prices at the tick the venue publishes for it hit the residue.

    **The threshold is a property of the magnitude, not of the symbol.** Every BTCUSDT price is
    above it, no ETHUSDT or SOLUSDT price is at today's levels, and an ETHUSDT above ~4,096 would
    be. So this is not a BTCUSDT special case and must not be narrowed into one.

    ``Decimal(str(increment))`` is the exact decimal the venue's filter names — ``Decimal(0.1)``
    would re-import the very error being removed — and ``float()`` of the exact product is the
    nearest double, whose repr is the shortest round-tripping form, which is what the venue reads.
    """
    return float(Decimal(count) * Decimal(str(increment)))


def floor_to_step(value: float, step: float) -> float:
    """Round DOWN to the venue's increment. Down, never nearest: rounding up could push a
    size past the per-order cap the budget registered, turning a sizing helper into a way
    to exceed an operator's limit by one lot.

    The multiply-then-floor is done in integer space to avoid the binary-float artefact
    where ``0.3 / 0.1`` is ``2.9999...`` and floors to 2 — a whole lot lost to
    representation."""
    if step <= 0 or value <= 0:
        return 0.0
    steps = math.floor(round(value / step, 9))
    return _exact_multiple(steps, step)


def round_price_to_tick(price: float, tick: float, *, mode: str = "down") -> float:
    """Round a price to the venue's tick. ``mode`` picks the safe direction for the caller:
    a protective stop rounds so it does not drift further from the entry than intended.
    Returns 0.0 when the tick is unknown — the caller must refuse rather than send an
    unrounded price."""
    if tick <= 0 or price <= 0:
        return 0.0
    ticks = price / tick
    n = math.floor(round(ticks, 9)) if mode == "down" else math.ceil(round(ticks, 9))
    return _exact_multiple(n, tick)


def _refusal(reasons: list[str], **detail: Any) -> dict[str, Any]:
    return {
        "sizing_version": SIZING_VERSION,
        "sizable": False,
        "quantity": 0.0,
        "notional_usdt": 0.0,
        "reasons": reasons,
        **detail,
    }


def size_live_order(
    plan: Mapping[str, Any],
    *,
    equity_usdt: float,
    max_order_notional_usdt: float,
    filters: SymbolFilters | None,
    risk_fraction: float = RISK_PER_TRADE_FRACTION,
) -> dict[str, Any]:
    """Decide the quantity for one live entry, or refuse. Pure.

    ``plan`` is an entry plan as ``paper.build_entry_plan`` produces it: ``entry_price``
    and ``risk``, where ``risk`` is the per-unit entry↔stop distance in quote terms. The
    two candidates:

    - **risk-based** — ``equity * risk_fraction / risk_per_unit``: the size at which the
      stop being hit costs the intended fraction of the account.
    - **budget** — ``max_order_notional_usdt / entry_price``: the largest size the
      registered per-order cap admits.

    The smaller wins, floored to the venue's lot step. Then the venue's own minimums and
    the budget cap are re-checked on the FINAL size, so a floored result that slipped below
    a minimum or (impossibly, but checked) above the cap refuses rather than ships.

    Returns a record either way: ``sizable`` plus both candidates, so an operator reading a
    refusal can see which constraint bound and by how much.
    """
    entry_price = _f(plan.get("entry_price"))
    risk_per_unit = _f(plan.get("risk"))
    equity = _f(equity_usdt)
    cap = _f(max_order_notional_usdt)

    # Refuse before computing anything on a missing input — a zero here is "unknown", and
    # every one of these would otherwise produce a confident-looking wrong number.
    reasons: list[str] = []
    if entry_price <= 0:
        reasons.append(NO_ENTRY_PRICE)
    if risk_per_unit <= 0:
        # No stop distance means the risk-based candidate is undefined; sizing on the cap
        # alone would silently drop the risk half of "min of two".
        reasons.append(NO_RISK_DISTANCE)
    if equity <= 0:
        reasons.append(NO_EQUITY)
    if cap <= 0:
        # 0 means "not configured" everywhere in the live stack, and 0 blocks.
        reasons.append(NO_BUDGET_CAP)
    if filters is None or not filters.valid():
        reasons.append(NO_FILTERS)
    if reasons:
        return _refusal(reasons, equity_usdt=equity, max_order_notional_usdt=cap)

    assert filters is not None  # narrowed by the guard above
    risk_budget_usdt = equity * max(0.0, float(risk_fraction))
    risk_quantity = risk_budget_usdt / risk_per_unit
    budget_quantity = cap / entry_price
    bound_by = "risk" if risk_quantity <= budget_quantity else "budget"

    # Volatility scaling, applied to the WINNER of the two candidates rather than to the risk
    # fraction — which is the whole reason it works. On any realistic equity the budget cap is
    # what binds (at a 60 USDT cap the risk leg only wins below roughly 30 USDT of equity), and
    # while the cap binds the notional is fixed, so the risk actually taken is
    # `cap × stop_distance / price` and therefore RISES with volatility. Scaling `risk_fraction`
    # would leave that untouched; scaling the final quantity does not. See
    # `paper.volatility_size_multiplier` for the measurement and for why the multiplier is
    # capped at 1.0 (it may shrink an order, never widen one past what was registered).
    #
    # Read off the plan rather than recomputed here: the plan is what the execution step is
    # given, and a number this module derived for itself is a number the two could disagree
    # about. A plan without the field — anything built before this existed — scales by 1.0.
    vol_multiplier = _f(plan.get("vol_size_multiplier")) if "vol_size_multiplier" in plan else 1.0
    if not (0.0 < vol_multiplier <= 1.0):
        # Out of range means the plan is not one this module can trust to shrink safely, and the
        # safe reading of an untrustworthy multiplier is "do not scale" rather than "guess".
        vol_multiplier = 1.0

    raw_quantity = min(risk_quantity, budget_quantity) * vol_multiplier
    quantity = floor_to_step(raw_quantity, filters.step_size)
    notional = round(quantity * entry_price, 8)

    detail: dict[str, Any] = {
        "equity_usdt": equity,
        "risk_fraction": float(risk_fraction),
        "risk_budget_usdt": round(risk_budget_usdt, 8),
        "risk_per_unit": risk_per_unit,
        "risk_quantity": round(risk_quantity, 12),
        "budget_quantity": round(budget_quantity, 12),
        "max_order_notional_usdt": cap,
        "bound_by": bound_by,
        # Recorded even when it is 1.0. The refusal and success records are what an operator
        # reads to understand a size, and "the volatility scaler did nothing" is a different
        # statement from "there was no volatility scaler" — the second is what every record
        # before this said, and it is what a record with the field missing still means.
        "vol_size_multiplier": vol_multiplier,
        "step_size": filters.step_size,
        "entry_price": entry_price,
    }

    if quantity <= 0:
        return _refusal([ROUNDS_TO_ZERO], **detail)
    if quantity < filters.min_qty:
        return _refusal([BELOW_MIN_QTY], min_qty=filters.min_qty, quantity_before_refusal=quantity, **detail)
    if filters.max_qty > 0 and quantity > filters.max_qty:
        # Unreachable under a 60 USDT cap on any liquid symbol, and checked anyway: the
        # venue rejects an oversized MARKET order outright, and refusing here keeps the
        # reason legible instead of surfacing as an opaque venue error at egress.
        return _refusal([ABOVE_MAX_QTY], max_qty=filters.max_qty, quantity_before_refusal=quantity, **detail)
    if notional < filters.min_notional:
        return _refusal(
            [BELOW_MIN_NOTIONAL], min_notional=filters.min_notional,
            notional_before_refusal=notional, **detail,
        )
    if notional > cap:
        # Cannot happen after min() + floor, and checked anyway: a sizing helper that could
        # exceed the operator's registered cap is the one bug that must never ship silently.
        return _refusal([EXCEEDS_BUDGET], notional_before_refusal=notional, **detail)

    return {
        "sizing_version": SIZING_VERSION,
        "sizable": True,
        "quantity": quantity,
        "notional_usdt": notional,
        "reasons": [],
        **detail,
    }


def usable_equity_usdt(snapshot: Any | None) -> float:
    """The equity a NEW live position may be sized against, or 0.0 (which refuses).

    ``available_balance`` rather than wallet or margin balance: it is what the venue will
    actually let a new position use, and it is the smallest of the three, so sizing is
    conservative by construction. An unreadable account returns 0.0, and 0.0 refuses —
    never a guess, and never a stale local number standing in for the venue's own figure.
    """
    if snapshot is None:
        return 0.0
    return max(0.0, _f(getattr(snapshot, "available_balance", 0.0)))


__all__ = [
    "ABOVE_MAX_QTY",
    "BELOW_MIN_NOTIONAL",
    "BELOW_MIN_QTY",
    "EXCEEDS_BUDGET",
    "NO_BUDGET_CAP",
    "NO_ENTRY_PRICE",
    "NO_EQUITY",
    "NO_FILTERS",
    "NO_RISK_DISTANCE",
    "RISK_PER_TRADE_FRACTION",
    "ROUNDS_TO_ZERO",
    "SIZING_VERSION",
    "SymbolFilters",
    "floor_to_step",
    "round_price_to_tick",
    "size_live_order",
    "usable_equity_usdt",
]
