"""C12 cost model — fees and slippage for the factory backtest (source S4b port).

Ports the fee/slippage decomposition from ``crypto_AI_System/backtesting/
cost_model.py``, in **R-space only**: this port's accounting is deliberately
R-based, no quantity or notional fields anywhere (see ``paper.py``: "paper sizing
added nothing but noise"). A taker always fills at an adverse price (buys higher,
sells lower) and pays a fee; because ``risk_amount = qty *
risk_per_unit``, quantity cancels out of every R-denominated ratio algebraically —
verified numerically against the source's qty-based ``settle_trade`` (matches to
floating-point precision for both LONG and SHORT). The reduced form is a pure
function of ``(entry_price, exit_price, risk_per_unit, direction)`` with no qty
tracked anywhere, so nothing about the deliberate R-only design changes.

**The two legs are no longer charged the same way** (2026-07-28). The entry is a MARKET order
and pays taker plus adverse slippage on every path. The exit depends on how it left: a target
now rests as a maker LIMIT (``live_leg``), so it fills AT the target and pays the maker rate,
while a stop, a time exit and a manual exit all still leave at market. ``apply_cost_model``
therefore takes the ``close_reason``, and ``CostBreakdown`` carries the maker share separately
so ``pool.expectancy_at`` can still rescale the taker portion exactly.

**Scope was "the backtest only", and that boundary was wrong** (2026-07-30). The source
confined cost application to backtest/factory scoring: paper trading measured pure signal
quality on intended fills, and costs were what the robustness scorer needed. The port kept
it. What neither noticed is that ``lifecycle.LifecycleThresholds`` judges paper outcomes at
``warn_expectancy_r = 0.0`` and ``guards`` meters daily loss in the same units — thresholds
written as if the number were net, reading a number that never had a cost subtracted. On
this store the gap is not marginal: 86 native paper outcomes are +0.041R gross and −0.506R
once this module is applied to them, so every strategy the ladder was built to demote sat
comfortably above every rung.

So the boundary now runs a different way, and the distinction is worth stating precisely:

- **What is STORED stays cost-free.** ``paper.build_outcome_record`` keeps ``result_R`` as
  the intended-fill move over the entry risk, byte-identical to before, because a stored
  outcome is durable evidence and rewriting what past rows mean is how two populations end
  up sharing a field name.
- **What is JUDGED is net.** :func:`outcome_net_r` converts at read time, at the rates the
  venue charges *now* — the same choice ``pool.expectancy_at`` already makes for backtest
  expectancy.

:func:`round_trip_cost_r` is the pre-trade half of the same arithmetic: what a round trip
costs before the market moves, which ``paper``/``live_entry`` refuse an entry on.
``factory.backtest_spec`` (C8) still applies costs the way it did, feeding C8b's
``cost_robustness`` component.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

# The R basis whose costs are ALREADY inside the number (live R is computed on actual fills,
# so slippage is in it). Imported from its owner rather than respelled here: `live_pnl` defines
# what each basis means, and two spellings of one label is how the two drift. Constant only —
# no I/O at import, the same reason `paper.py` takes `R_BASIS_INTENT` from there.
from .live_pnl import R_BASIS_FILLED

# The taker rate this venue actually charges, measured — not the source default.
#
# The port carried 2.5 bps from `backtesting/cost_model.py` unchanged, and that number is
# half what Binance USD-M charges at standard tier. Measured on this account 2026-07-26:
# 0.1291 USDT of commission over roughly 258 USDT of fills (two canary entries at ~64.5
# USDT and their two closes) — 5.0 bps per fill, which is the published standard taker rate.
#
# Backtest evidence exists to predict live results, so it has to charge the live rate. The
# cost of keeping the old value is stated in the same breath: every candidate scored under
# 2.5 bps keeps the numbers it was scored with, because `backtest_evidence` is durable and
# is never rewritten. That is why `cost_summary.cost_model` records the rates each candidate
# was scored under — the store can hold two bases and say so, rather than silently mixing.
#
# A lower actual rate (VIP tier, BNB discount) only makes a real edge better than reported,
# which is the safe direction for evidence that gates real money.
DEFAULT_TAKER_FEE_BPS = 5.0

# Unchanged from the source. Slippage is a market property rather than a published rate, and
# nothing here has measured it — a canary is a single market order, not a sample.
DEFAULT_SLIPPAGE_BPS = 3.0

# The maker rate, for the one leg that can earn it: the take-profit exit.
#
# From 2026-07-28 `live_leg` places the target as a resting `reduceOnly` LIMIT instead of a
# `TAKE_PROFIT_MARKET`, because a target is by construction a price the market has to come TO.
# A resting order at that price is a maker fill; the conditional it replaced triggered into a
# market order and paid taker plus adverse slippage to reach a price it had already reached.
#
# 2.0 bps is Binance USD-M's PUBLISHED standard maker rate. Unlike the taker figure above it is
# **not measured on this account** — no maker fill has been placed yet, so there is nothing to
# measure. Stated rather than silently assumed, because the direction of the error matters: if
# the real maker rate is higher, this model reports an edge slightly better than reality, which
# is the UNSAFE direction. The first live maker fill should replace this with a measurement.
DEFAULT_MAKER_FEE_BPS = 2.0

# The one close reason that exits as a maker. A stop and a time exit both leave at market, and a
# manual exit is a market order by definition; only the target rests. Keeping this as a named
# set rather than an `== "take_profit"` check means a new close reason has to make an explicit
# decision about which side of the fee it lands on.
MAKER_EXIT_REASONS = frozenset({"take_profit"})

# How much of one R a round trip may cost before the entry is refused (`round_trip_cost_r`).
#
# Measured, not chosen for roundness. Of the candidates in this store scored at the current cost
# model, those with POSITIVE net expectancy have a median of +0.172R and a 75th percentile of
# +0.369R (2026-07-30, n=102). A plan whose friction alone is 0.25R therefore needs an edge in
# the top quartile of everything this factory has ever produced merely to break even — and
# `round_trip_cost_r` prices friction pessimistically, so a real trade pays at least it.
#
# What it refuses, measured against the 86 native paper outcomes this runtime has settled:
# 59 of them (69%) — 48 of 50 on 15m, 11 of 17 on 1h, none at 4h or 1d. That distribution is the
# finding rather than a side effect. The five strategies routing on 2026-07-30 were all 15m, all
# carried NEGATIVE backtest expectancy at the rates this venue charges, and between them turned
# +0.041R/trade gross into −0.506R.
#
# It lives HERE, not beside `paper`'s other entry caps, because two doors enforce it: the paper
# book and `live_entry`. One number, so the simulated book and the money path cannot disagree
# about what is economic.
#
# Two properties keep an unregistered constant acceptable, the same two `paper`'s concurrency
# caps rest on. It can only ever make the runtime trade LESS — no value of it admits an entry the
# existing checks refuse, so it cannot widen risk and needs no gate of its own. And a wrong
# refusal is otherwise SILENT, since nothing records the trades that did not happen: that is why
# `cycle` shadows this refusal into the counterfactual registry, the mechanism that exists to
# tell a gate that saves money from one that is merely too tight. Registering it through
# `risk_limits` (a sixth key, a schema version) is a separate decision — that mechanism exists
# so an operator can RELAX a realized-loss breaker within code bounds, and this is neither a
# breaker nor relaxable.
MAX_ENTRY_COST_R = 0.25


@dataclass(frozen=True)
class CostModel:
    taker_fee_bps: float = DEFAULT_TAKER_FEE_BPS
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS
    maker_fee_bps: float = DEFAULT_MAKER_FEE_BPS

    def fill_price(self, mid: float, direction: str, action: str) -> float:
        """Adverse-slippage fill: a taker buys above and sells below the mid.

        ``direction`` is "LONG"/"SHORT" (this port's field name); ``action`` is
        "entry"/"exit" — the source's exact adverse-direction truth table."""
        adverse_up = (direction == "LONG" and action == "entry") or (direction == "SHORT" and action == "exit")
        factor = 1.0 + self.slippage_bps / 10000.0 if adverse_up else 1.0 - self.slippage_bps / 10000.0
        return mid * factor


@dataclass(frozen=True)
class CostBreakdown:
    gross_r: float       # on intended (mid) prices, no costs — what settle_trade_plan already returns
    net_r: float         # after fees + slippage — the honest simulated outcome
    fee_cost_r: float    # taker + maker together, the figure that comes off net_r
    slippage_cost_r: float
    # The maker share of `fee_cost_r`, carried separately because `pool.expectancy_at` re-derives
    # an old candidate's expectancy at a different TAKER rate, and that rescale is only linear in
    # the taker portion. Zero on a taker exit, which is what every pre-2026-07-28 record is.
    maker_fee_cost_r: float = 0.0


def apply_cost_model(
    direction: str, entry_price: float, exit_price: float, risk: float, *,
    cost: CostModel | None = None, close_reason: str | None = None,
) -> CostBreakdown:
    """Decompose a gross (intended-price) R multiple into net R after costs.

    ``risk`` is risk-per-unit (``|entry - stop|``) — exactly the position's existing
    ``risk`` field; no quantity/notional is needed (see module docstring for why it
    algebraically cancels). ``risk <= 0`` is the source's own division guard and
    returns all zeros rather than raising — defensive; a built entry plan never has
    a non-positive risk (``build_entry_plan`` already refuses those).

    ``close_reason`` selects how the EXIT leg is charged, and only the exit leg — the entry is a
    MARKET order on every path, so it always pays taker plus adverse slippage:

    - a maker exit (``MAKER_EXIT_REASONS``, i.e. the target) fills **at the target price** and
      pays the maker rate. No adverse slippage: a resting limit order does not cross the spread,
      and `settle_trade_plan` already returns the target price itself as the exit — so this is
      the branch where the model and the venue finally agree.
    - anything else leaves at market: taker rate plus adverse slippage, unchanged.

    ``close_reason=None`` charges the taker branch. That keeps every existing caller's numbers
    identical and makes the pessimistic case the default — a cost model that got optimistic
    when it was told nothing would be the wrong way round.
    """
    cost = cost or CostModel()
    if risk <= 0:
        return CostBreakdown(0.0, 0.0, 0.0, 0.0, 0.0)
    sign = 1.0 if direction == "LONG" else -1.0
    gross_r = sign * (exit_price - entry_price) / risk
    maker_exit = close_reason in MAKER_EXIT_REASONS

    entry_fill = cost.fill_price(entry_price, direction, "entry")
    exit_fill = exit_price if maker_exit else cost.fill_price(exit_price, direction, "exit")
    on_fill_r = sign * (exit_fill - entry_fill) / risk
    slippage_cost_r = gross_r - on_fill_r
    exit_rate = cost.maker_fee_bps if maker_exit else cost.taker_fee_bps
    maker_fee_cost_r = (exit_fill * exit_rate / 10000.0 / risk) if maker_exit else 0.0
    fee_cost_r = (
        entry_fill * cost.taker_fee_bps + exit_fill * exit_rate
    ) / 10000.0 / risk
    net_r = on_fill_r - fee_cost_r

    return CostBreakdown(
        gross_r=round(gross_r, 8),
        net_r=round(net_r, 8),
        fee_cost_r=round(fee_cost_r, 8),
        slippage_cost_r=round(slippage_cost_r, 8),
        maker_fee_cost_r=round(maker_fee_cost_r, 8),
    )


def round_trip_cost_r(
    direction: str, entry_price: float, risk: float, *, cost: CostModel | None = None
) -> float:
    """What one round trip costs in R before the market moves at all. Positive.

    ``apply_cost_model`` evaluated at ``exit_price == entry_price``: the gross move is zero,
    so what remains is pure friction — taker in, taker plus adverse slippage out. Expressed
    as a positive number, so 0.25 reads as "a quarter of one R".

    This is the quantity that decides whether a trade can be profitable at all, and it is
    not a property of the strategy but of the **stop distance**: ``risk`` is
    ``stop_atr × ATR``, so a short timeframe's tight stop divides the same fixed bps by a
    smaller number. Measured on this store 2026-07-30, per closed backtest trade:
    15m 0.341R, 1h 0.168R, 4h 0.077R, 1d 0.029R — a twelvefold spread on the same rates,
    against gross expectancies that ranged only +0.01R to +0.23R.

    The **taker** exit is charged deliberately. A trade that reaches its target pays the
    cheaper maker leg, but nothing at entry time knows whether this one will, and pricing
    the cheaper exit would let a plan clear a cost floor on the assumption that it wins.
    This is what a losing trade actually pays.

    Unpriceable input returns ``inf`` rather than ``apply_cost_model``'s zeros. That
    divergence is the point: zero cost is the fail-OPEN answer for a gate, and the gate
    below is the only caller. A non-positive risk or an unknown direction means the friction
    is unknown, and unknown must not read as free.
    """
    if direction not in ("LONG", "SHORT") or not (entry_price > 0) or not (risk > 0):
        return math.inf
    return -apply_cost_model(
        direction, entry_price, entry_price, risk, cost=cost, close_reason=None
    ).net_r


def outcome_net_r(record: Mapping[str, Any], *, cost: CostModel | None = None) -> float | None:
    """A settled outcome's R after the costs its own basis leaves out. ``None`` when unknown.

    Paper R is cost-free by construction (see the module docstring): it is the intended-fill
    move over the entry risk, and no fee or slippage has ever been subtracted from it. So the
    demoter and the risk breaker have been reading a gross number against thresholds written
    as if it were net — a strategy at +0.02R gross and −0.30R net stays PAPER_ACTIVE forever.
    This is the conversion, applied at READ time at the CURRENT rates, the same choice
    ``pool.expectancy_at`` makes and for the same reason: the question a demotion answers is
    "does this lose money at what the venue charges *now*", not at whatever it charged when
    the row was written.

    Two rules make the fallbacks safe:

    - **Only an explicit ``filled`` basis opts out.** Live R is measured on actual fills, so
      slippage is already inside it and charging the full model would double-count; that row
      keeps its own number (its missing fees are a separate, still-open gap). Every other
      value — ``intent``, absent, unrecognised — gets the costs charged. The default has to be
      the pessimistic branch, or a row could buy itself the cheaper treatment by omitting a
      field.
    - **A row that cannot price itself returns ``None``, never a guess.** The caller keeps
      ``result_R`` and reports the mix rather than inventing a net figure. Imported history
      carries no prices at all and lands here.

    ``risk`` (per-unit, ``|entry - stop|``) is read from the record when present. Rows written
    before it was recorded reconstruct it from the identity paper settlement already
    guarantees — ``result_R = ±(exit - entry) / risk`` — which is exact to the stored
    rounding, and undefined only at ``result_R == 0``.
    """
    if record.get("r_basis") == R_BASIS_FILLED:
        return None
    result_r = record.get("result_R")
    entry = record.get("entry_price")
    exit_price = record.get("exit_price")
    direction = record.get("direction")
    if (
        isinstance(result_r, bool) or not isinstance(result_r, (int, float))
        or not isinstance(entry, (int, float)) or isinstance(entry, bool)
        or not isinstance(exit_price, (int, float)) or isinstance(exit_price, bool)
        or direction not in ("LONG", "SHORT")
        or entry <= 0 or exit_price <= 0
    ):
        return None

    risk = record.get("risk")
    if not (isinstance(risk, (int, float)) and not isinstance(risk, bool) and risk > 0):
        if not result_r:
            return None
        signed = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
        risk = signed / float(result_r)
        if not (risk > 0):
            return None

    return apply_cost_model(
        str(direction), float(entry), float(exit_price), float(risk),
        cost=cost, close_reason=record.get("close_reason"),
    ).net_r
