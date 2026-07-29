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

**Scope, matching the source exactly**: cost application is confined to backtest/
factory scoring. The source's live paper kernel (``paper_position_kernel.py`` / this
port's ``paper.py``) never imports ``cost_model`` — grep confirms every caller of the
source cost model lives under ``backtesting/`` or ``strategy_factory/`` (the factory's
robustness-scoring path), never the live paper route. Paper trading measures pure
signal quality on intended fills; costs are what the factory's robustness scorer
needs to judge whether an edge survives realistic frictions. This port keeps that
boundary: **live paper R stays cost-free by design, unchanged** — only
``factory.backtest_spec`` (C8) applies costs, and only to feed C8b's
``cost_robustness`` component (previously always zero for lack of these inputs).
"""

from __future__ import annotations

from dataclasses import dataclass

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

# --- funding: the cost this model did not have, and the one that dominates ------------------
#
# These are PERPETUAL futures. There is no expiry, and the mechanism that keeps the contract
# near spot is a payment between longs and shorts every 8 hours, charged on NOTIONAL. Until
# 2026-07-29 this model charged fees and slippage and nothing else, which is defensible for an
# intraday book and wrong by an order of magnitude for this one:
#
#   modelled per trade : entry taker 5bps + slippage 3bps + exit maker 2bps  = 10 bps
#   omitted per trade  : 24 days x 3 intervals x 1bp                         = 72 bps
#
# `_EXIT_PARAMS` allows `max_holding_bars` up to 48, so a 1d spec holds 12-48 DAYS — 36 to 144
# funding settlements. Against a book whose measured expectancy is +0.08R with a 95% interval
# of [-0.32, +0.48], a systematically omitted ~0.2R is not a refinement.
#
# Two properties make this worth charging properly rather than approximating:
#
# **It is directional.** A long PAYS when the rate is positive and a short RECEIVES. Fees and
# slippage are direction-blind, so the factory could rank long and short specs on one scale;
# funding breaks that, and a model that omits it ranks them as though their carry were equal
# when it differs by twice the figure above.
#
# **It is already measured.** The cycle fetches DEFAULT_FUNDING_RECORDS real settlements per
# symbol for the `funding_rate`/`funding_zscore` features, so the venue's own per-interval
# history covers the replay window. Charging a modelled constant when the real series is right
# there would be inventing a number this repo already has — so `backtest_spec` charges the
# actual settlements, and the constant below is only the fallback.
#
# 1.0 bp is Binance USD-M's BASE funding rate (0.01% per 8h) — the value the venue clamps
# toward and the long-run BTC/ETH mean. It is a fallback, not a measurement, and it is used
# only when the funding series is absent or empty. `cost_summary.cost_model.funding_source`
# records which of the two a candidate was scored under, because "charged the venue's own
# history" and "charged a constant" are different qualities of evidence.
DEFAULT_FUNDING_BPS_PER_INTERVAL = 1.0

# Settlements per day at this venue (00:00 / 08:00 / 16:00 UTC). Used only by the fallback,
# which has no event times to count.
FUNDING_INTERVALS_PER_DAY = 3

FUNDING_SOURCE_VENUE = "venue_history"
FUNDING_SOURCE_FALLBACK = "modelled_constant"
# No funding accounted for at all. Not produced by this module — it is what `pool.cost_basis_of`
# reports for evidence minted before funding was charged, so the store can say which candidates
# carry the omission rather than having it inferred from a rate that is simply missing.
FUNDING_SOURCE_UNCHARGED = "uncharged"


@dataclass(frozen=True)
class CostModel:
    taker_fee_bps: float = DEFAULT_TAKER_FEE_BPS
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS
    maker_fee_bps: float = DEFAULT_MAKER_FEE_BPS
    funding_bps_per_interval: float = DEFAULT_FUNDING_BPS_PER_INTERVAL

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
    net_r: float         # after fees + slippage + funding — the honest simulated outcome
    fee_cost_r: float    # taker + maker together, the figure that comes off net_r
    slippage_cost_r: float
    # The maker share of `fee_cost_r`, carried separately because `pool.expectancy_at` re-derives
    # an old candidate's expectancy at a different TAKER rate, and that rescale is only linear in
    # the taker portion. Zero on a taker exit, which is what every pre-2026-07-28 record is.
    maker_fee_cost_r: float = 0.0
    # Carry over the holding window. SIGNED, unlike every other field here: a short in a
    # positive-funding regime is paid to hold, so this is the one cost term that can be negative
    # and the only reason `net_r` can exceed the on-fill figure. Zero on a trade that closed
    # inside one interval, and on every record minted before 2026-07-29.
    funding_cost_r: float = 0.0


def funding_cost_r(
    direction: str, entry_price: float, risk: float, funding_rate_sum: float
) -> float:
    """Carry over one holding window, in R. Signed: a long pays a positive rate, a short earns it.

    ``funding_rate_sum`` is the sum of the settlement rates the position was actually open
    across, as fractions (0.0001 = 1 bp), which is exactly the shape
    ``/fapi/v1/fundingRate`` returns. Summing first is not an approximation — each settlement is
    charged on notional at the same rate structure, so the total is linear in the rates.

    Quantity cancels the same way it does for fees: a payment is ``qty * price * rate`` and one
    R is ``qty * risk_per_unit``, so the ratio is ``price * rate / risk_per_unit`` with no
    quantity anywhere. That is what keeps this module R-only (see the module docstring).

    ``entry_price`` stands in for the mark price at each settlement. The real charge is on the
    mark at the moment of settlement, which drifts from entry over the hold — but the drift is
    unbiased (it is the same price path the trade's own R already measures) and using entry
    keeps this a pure function of the position, with no second price series to keep aligned.
    Stated rather than silently assumed: for a trade that runs far in its favour this
    UNDER-charges a long, which is the unsafe direction, bounded by the target distance.
    """
    if risk <= 0:
        return 0.0
    sign = 1.0 if direction == "LONG" else -1.0
    return sign * entry_price * funding_rate_sum / risk


def apply_cost_model(
    direction: str, entry_price: float, exit_price: float, risk: float, *,
    cost: CostModel | None = None, close_reason: str | None = None,
    funding_rate_sum: float = 0.0,
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

    ``funding_rate_sum`` is the carry over the holding window (see :func:`funding_cost_r`). It
    defaults to 0.0 — no carry — and that default is deliberately the *optimistic* one, against
    the rule above, because the alternative is worse: this function cannot see how long the
    position was open, so any non-zero default would be a holding period invented here rather
    than measured by the caller that has the bars. The safety lives one level up instead, where
    it can be honest: ``factory.backtest_spec`` always passes a real sum, and a candidate scored
    with no funding term at all is refused at the promotion door by ``pool.cost_basis_rank``.
    """
    cost = cost or CostModel()
    if risk <= 0:
        return CostBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
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
    # Charged on the ENTRY fill, not the mid: the position that carries is the one that was
    # actually opened, and that is the price the notional is denominated in.
    carry_r = funding_cost_r(direction, entry_fill, risk, funding_rate_sum)
    net_r = on_fill_r - fee_cost_r - carry_r

    return CostBreakdown(
        gross_r=round(gross_r, 8),
        net_r=round(net_r, 8),
        fee_cost_r=round(fee_cost_r, 8),
        slippage_cost_r=round(slippage_cost_r, 8),
        maker_fee_cost_r=round(maker_fee_cost_r, 8),
        funding_cost_r=round(carry_r, 8),
    )
