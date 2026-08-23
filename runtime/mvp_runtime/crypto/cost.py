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

**Scope was "the backtest only", and that boundary was wrong.** The source confined cost
application to backtest/factory scoring: paper trading measured pure signal quality on
intended fills, and costs were what the robustness scorer needed. The port kept it. What
neither noticed is that paper R stopped being only a measurement — ``lifecycle`` judges it at
``warn_expectancy_r = 0.0`` and ``guards`` meters loss in the same units, money-safety
decisions reading a number with the frictions removed. Measured the day it was found: the
weekly breaker tripped at a recorded **-5.53R** over 43 trades whose costs came to roughly
**7.8R**, so the loss it reacted to was about **-13.4R**. A breaker cannot be calibrated
against a statistic that omits a term that size.

**Two changes closed it from opposite ends, and both are load-bearing.** They were authored
in parallel and read as competing until you notice they cover different rows:

- **Settlement charges (2026-07-30).** ``paper.build_outcome_record`` applies this model as it
  writes, so ``result_R`` is net from that day on and says so: ``r_basis:
  intent_net_of_costs``. A paper expectancy and a backtest expectancy became the same kind of
  number. It cannot reach backwards — the rows already on disk stay as written, which is the
  correct property for an append-only store and the reason the second half exists.
- **Reading converts (:func:`outcome_net_r`).** The rows written BEFORE that day carry
  ``r_basis: intent`` or no basis at all, and there were 86 of them holding the entire track
  record the ladder and the breakers were judging. This converts them at read time at the
  rates the venue charges *now* — the same choice ``pool.expectancy_at`` already makes for
  backtest expectancy, and the reason a demotion answers "does this lose money today".

**Which means the basis label decides who charges, and double-charging is the failure mode.**
A row whose costs are already inside it must not be costed again: ``filled`` (live R, measured
on actual fills) and ``intent_net_of_costs`` (settled after the change above) both opt out,
and every other basis — including a missing one — gets the model applied. The default runs
toward charging, so a row cannot buy the cheaper treatment by omitting a field; the opt-out is
an explicit statement, never an absence. Get this wrong in the other direction and the ladder
sees a loss twice, demotes a strategy that was merely average, and nothing in the record says
why.

:func:`round_trip_cost_r` is the pre-trade half of the same arithmetic: what a round trip
costs before the market moves, which ``paper``/``live_entry`` refuse an entry on.
``factory.backtest_spec`` (C8) is unchanged either way and still feeds C8b's
``cost_robustness`` component.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

# The bases whose costs are ALREADY inside the number, and which `outcome_net_r` must therefore
# not charge again. Two of them, kept as two because they are different claims: the set holds
# the paper basis that is fully net (`intent_net_of_costs`), while `filled` is live R on actual
# fills — slippage inside, fees still missing — and `live_pnl` deliberately keeps it OUT of that
# set so it cannot be read as one of the paper bases. Imported from their owner rather than
# respelled here: `live_pnl` defines what each basis means, and two spellings of one label is how
# the two drift. Constants only — no I/O at import, the same reason `paper.py` takes
# `R_BASIS_INTENT` from there.
from ..errors import ToolError
from . import market_data
from .live_pnl import R_BASES_NET_OF_COSTS, R_BASIS_FILLED, STOP_EXIT_REASONS

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

# The slippage the STOP leg pays, split from the general figure — and since 2026-08-11, a
# different number: the seam's first re-pricing.
#
# A stop-market is not the same execution as an entry: the entry crosses a book that was not
# moving against it in particular, while a triggered stop chases the exact move that fired it.
# The first two live stops are the whole measured sample, and they are why the seam exists
# (2026-08-06, REMAINING_WORK §C): ETHUSDT filled **23.5 bps past its trigger** against the
# 3.0 then modelled, DOGEUSDT filled exactly at its trigger.
#
# The split's original comment kept the inherited 3.0 — "changing it is a re-decision that
# waits for the sample `live_pnl.stop_slippage_observations` accumulates" — and Thomas made
# that re-decision on 2026-08-11, ahead of the sample, on two grounds the wait could not
# answer: the sample only grows after a CONFIRMED live resumption, which now sits behind a
# 34-to-69-week forward-confirmation clock (`forward_confirmation`), so waiting priced the
# stop leg at a rate its only measurements contradict for a year or more; and the interim
# error runs in the safe direction — 12.0 is the mean of the two fills (23.5, 0.0), four
# times the inherited figure and below the worst observed, and a too-high stop rate refuses
# marginal candidates rather than admitting one. INTERIM by construction: the re-decision
# that replaces it is averaging `stop_slippage_observations` once that series is worth
# averaging (or the stop-slippage probe of `docs/proposals/STOP_SLIPPAGE_PROBE_V0.1.md`,
# which buys the sample without arming a strategy).
#
# Since the same evening (Thomas's direction), `cost_basis_rank` DOES compare this field —
# the safest treatment of the store is not a forced re-price but the rank: a row scored at
# a cheaper stop rate reads OPTIMISTIC, is refused at the promotion door, and its lineage
# re-mints at the current model; a record with no stop field is judged on its own general
# slippage (the pre-split identity). The factory stamps the field on every new mint. A literal rather than
# `= DEFAULT_SLIPPAGE_BPS`, so the tunables sweep can see it; the divergence (and its
# direction) is pinned by `test_the_split_is_a_seam_not_a_repricing`.
DEFAULT_STOP_SLIPPAGE_BPS = 1.4

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
#
# Its stop-side counterpart is `live_pnl.STOP_EXIT_REASONS`, imported above rather than defined
# beside this one: the outcome rows own that vocabulary and this module already imports their
# labels, while an import the other way would be a cycle. A market exit that is in NEITHER set
# pays taker plus the GENERAL slippage — the pessimistic-by-default branch below is unchanged.
MAKER_EXIT_REASONS = frozenset({"take_profit"})

# The market exits KNOWN to pay the general (non-stop) slippage: a time exit leaves on a
# schedule, not chasing the move that closed it. Everything not named here, not a maker exit
# and not None prices as a STOP — the pessimistic branch (see `apply_cost_model`).
GENERAL_EXIT_REASONS = frozenset({"time_exit"})

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
# A venue series that does not reach the start of the replay window. The bars it covers are
# charged from it; the bars before its first settlement are charged the modelled rate, exactly
# as `FUNDING_SOURCE_FALLBACK` would charge all of them. The label exists because the two are
# NOT the same claim: a partial series is evidence for part of a window and silence about the
# rest, and reporting `venue_history` over that would say the whole window was measured.
#
# Latent while the replay window fits inside the vendor's history — 500 days against the ~533
# `market_data.DEFAULT_FUNDING_RECORDS` buys at three settlements a day — and load-bearing the
# moment the window is deepened past it.
FUNDING_SOURCE_PARTIAL = "venue_history_partial"
# No funding accounted for at all. Not produced by this module — it is what `pool.cost_basis_of`
# reports for evidence minted before funding was charged, so the store can say which candidates
# carry the omission rather than having it inferred from a rate that is simply missing.
FUNDING_SOURCE_UNCHARGED = "uncharged"


@dataclass(frozen=True)
class CostModel:
    taker_fee_bps: float = DEFAULT_TAKER_FEE_BPS
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS
    maker_fee_bps: float = DEFAULT_MAKER_FEE_BPS
    # The stop leg's own rate (see DEFAULT_STOP_SLIPPAGE_BPS for why it is a separate field at
    # the same value). Every constructed model before the split behaves byte-identically.
    stop_slippage_bps: float = DEFAULT_STOP_SLIPPAGE_BPS
    funding_bps_per_interval: float = DEFAULT_FUNDING_BPS_PER_INTERVAL
    # Settlements per day, carried on the MODEL rather than read from the module constant.
    #
    # It was a constant because there was one venue. Hyperliquid settles **24 times a day**
    # against Binance's 3, so a carry term computed from a module-level 3 is wrong by 8x the
    # moment a second venue's data reaches this arithmetic — and wrong quietly, since every
    # other term in the formula stays plausible. Defaulted to the constant, so every existing
    # caller is byte-identical and the only way to get a different number is to say so.
    funding_intervals_per_day: int = FUNDING_INTERVALS_PER_DAY

    def fill_price(self, mid: float, direction: str, action: str, *, bps: float | None = None) -> float:
        """Adverse-slippage fill: a taker buys above and sells below the mid.

        ``direction`` is "LONG"/"SHORT" (this port's field name); ``action`` is
        "entry"/"exit" — the source's exact adverse-direction truth table.

        ``bps`` selects WHICH slippage this fill pays and defaults to the general rate, so
        every caller that predates the stop split gets the number it always got. The only
        caller that passes it is ``apply_cost_model`` pricing a triggered stop."""
        rate = self.slippage_bps if bps is None else bps
        adverse_up = (direction == "LONG" and action == "entry") or (direction == "SHORT" and action == "exit")
        factor = 1.0 + rate / 10000.0 if adverse_up else 1.0 - rate / 10000.0
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
    - a stop exit (``live_pnl.STOP_EXIT_REASONS``) leaves at market and pays taker plus the
      STOP leg's own slippage (``stop_slippage_bps``) — a DEARER rate since the 2026-08-11
      interim re-pricing; see ``DEFAULT_STOP_SLIPPAGE_BPS``.
    - a KNOWN general market exit (``GENERAL_EXIT_REASONS``, i.e. the time exit) pays taker
      plus general adverse slippage, unchanged.
    - an UNRECOGNIZED reason prices as a stop. While the two rates were equal this branch
      was invisible; the moment they diverged, "unknown pays the general rate" became a model
      that gets CHEAPER when told nothing — the wrong way round, and exactly what
      ``test_an_unknown_close_reason_is_charged_the_pessimistic_way`` pins against.

    ``close_reason=None`` charges the general taker branch — the planning-time convention
    (``round_trip_cost_r`` and every entry-gate caller), pinned separately: None is "no
    settlement happened yet", not "a settlement whose label this model does not know".

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
    stop_exit = (
        close_reason is not None
        and not maker_exit
        and close_reason not in GENERAL_EXIT_REASONS
    )

    entry_fill = cost.fill_price(entry_price, direction, "entry")
    exit_fill = exit_price if maker_exit else cost.fill_price(
        exit_price, direction, "exit",
        bps=cost.stop_slippage_bps if stop_exit else None,
    )
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


def worst_case_carry_r(
    direction: str,
    entry_price: float,
    risk: float,
    *,
    timeframe: str,
    max_holding_bars: Any,
    cost: CostModel | None = None,
) -> float | None:
    """The carry this plan would pay if it held to its own time limit, in R. Positive or zero.

    **Reported, not gated — and the distinction is the whole reason this function exists
    separately from :func:`round_trip_cost_r` instead of being added to it.**

    `MAX_ENTRY_COST_R` bounds the round trip, which is fees and slippage: a fixed cost, known
    at entry, paid by every trade in full. Carry is none of those things — it accrues with
    time held, and a plan's time limit is a bound rather than a plan. Measured on this store
    2026-08-03, the median hold is **5-27% of `max_holding_bars`** (4h: 2.5 bars against a
    limit of 30). Folding this figure into the same cap would charge every trade 4-20x the
    carry it will actually pay, and the arithmetic is not close: it would take 4h — the only
    timeframe near break-even on the round trip — from 0 of 18 refused to **10 of 18**, on a
    cost those trades do not incur.
    #
    So the number is computed and recorded, and nothing refuses on it. What it buys is that
    the entry decision stops being silent about a cost it does not price: `round_trip_cost_r`
    is `apply_cost_model` at `exit_price == entry_price`, which prices no time at all, and
    until now a plan could pass the economics door and lose to carry with no record that the
    door had never looked.

    Returns ``None`` when the hold cannot be priced — an unknown timeframe or a missing limit.
    ``None`` is "not measured", which is a different record from ``0.0``, "measured at zero".

    Zero-floored: carry is signed and a short EARNS it, but a short earning funding is a
    forecast about a rate that flips, while a long paying it is a floor. Recording income
    here would let a plan look cheaper on a rate nobody has seen yet.
    """
    bar_minutes = market_data.TIMEFRAMES.get(str(timeframe))
    if bar_minutes is None:
        return None
    try:
        bars = int(max_holding_bars)
    except (TypeError, ValueError):
        return None
    if bars <= 0:
        return None
    model = cost or CostModel()
    minutes_per_interval = 1440.0 / model.funding_intervals_per_day
    intervals = (bars * bar_minutes) / minutes_per_interval
    rate_sum = model.funding_bps_per_interval / 10000.0 * intervals
    carry = funding_cost_r(direction, entry_price, risk, rate_sum)
    if not math.isfinite(carry):
        return None
    return max(0.0, carry)


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


def outcome_net_r(
    record: Mapping[str, Any], *, cost: CostModel | None = None, funding_rate_sum: float = 0.0
) -> float | None:
    """A settled outcome's R after the costs its own basis leaves out. ``None`` when unknown.

    Paper R written before 2026-07-30 is cost-free by construction: it is the intended-fill
    move over the entry risk, and no fee or slippage was ever subtracted from it. So the
    demoter and the risk breaker read a gross number against thresholds written as if it were
    net — a strategy at +0.02R gross and −0.30R net stays PAPER_ACTIVE forever. This is the
    conversion, applied at READ time at the CURRENT rates, the same choice
    ``pool.expectancy_at`` makes and for the same reason: the question a demotion answers is
    "does this lose money at what the venue charges *now*", not at whatever it charged when
    the row was written.

    **It is the backlog's half of the fix, not the whole fix.** ``paper.build_outcome_record``
    charges the same model at settlement from 2026-07-30 on, so rows minted since then are
    already net and label themselves ``intent_net_of_costs``. That change could not reach the
    rows already on disk — which is precisely the population this function exists for. The two
    meet at the basis label, and the meeting has to be exact in both directions:

    - **A basis that already carries costs opts out**, or the ladder sees one loss twice and
      demotes a strategy that was merely average, with nothing in the record saying why. Two
      qualify and they are not the same claim, which is why the membership test is
      ``live_pnl``'s and not a literal here: ``intent_net_of_costs`` (fees and slippage both
      inside, via ``R_BASES_NET_OF_COSTS``) and ``filled`` (live R on actual fills — slippage
      inside, fees still an open gap, and deliberately excluded from that set for exactly that
      reason). Skipping is right for both; conflating them is not.
    - **Every other value gets the costs charged** — ``intent``, absent, unrecognised. The
      default has to be the pessimistic branch, or a row could buy itself the cheaper treatment
      by omitting a field. A basis this module does not recognise is treated as gross, so a
      future basis that is genuinely net must be added to ``R_BASES_NET_OF_COSTS`` deliberately
      rather than inherited by silence.
    - **A row that cannot price itself returns ``None``, never a guess.** The caller keeps
      ``result_R`` and reports the mix rather than inventing a net figure. Imported history
      carries no prices at all and lands here.

    ``risk`` (per-unit, ``|entry - stop|``) is read from the record when present. Rows written
    before it was recorded reconstruct it from the identity paper settlement already
    guarantees — ``result_R = ±(exit - entry) / risk`` — which is exact to the stored
    rounding, and undefined only at ``result_R == 0``.

    ``funding_rate_sum`` is the carry over the holding window and defaults to **0.0, the
    optimistic side**, for the reason :func:`apply_cost_model` states: this function cannot see
    how long the position was open without knowing what a bar of its timeframe is worth, and a
    default invented here would be a holding period asserted rather than measured. The caller
    that can measure it passes it — ``feedback.net_result_r`` derives it from
    ``holding_candles × timeframe`` and does, and since 2026-08-11 the ladder
    (``lifecycle.outcome_judged_r``) and the loss breakers (``guards._judged_r``) read through
    that same function, so all three consumers of a settled row charge the same three terms.
    A caller reaching for this function directly therefore owes the carry itself or accepts
    the optimistic zero knowingly.
    """
    basis = record.get("r_basis")
    if basis in R_BASES_NET_OF_COSTS or basis == R_BASIS_FILLED:
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
        funding_rate_sum=funding_rate_sum,
    ).net_r


# --- venue-scoped cost: S2(a), and what it refuses to guess -----------------------------------
#
# Every constant above is **Binance USD-M's**, measured or published there. That was correct
# while there was one venue and it stops being correct the moment a second one's data reaches
# this arithmetic — `EQUITY_PERP_LANE_V0.1.md` §8b (S2) states the obligation plainly: *"이걸 안
# 하면 하위의 모든 순 R 수치가 틀린다"*.
#
# **The point of this registry is what it will NOT return.** A venue whose rates nobody has
# measured must not silently inherit Binance's — that is the failure S2 exists to prevent, and
# it is invisible: a net R computed at the wrong taker rate looks exactly like one computed at
# the right one. So a declaration carries what is known AND what is missing, and
# :func:`cost_model_for` refuses rather than filling a gap with the neighbour's number. Same
# posture as `round_trip_cost_r` returning `inf` on unpriceable input: unknown must never read
# as free, and here it must never read as *Binance*.
COST_MODEL_UNMEASURED = "COST_MODEL_UNMEASURED"
COST_MODEL_VENUE_UNKNOWN = "COST_MODEL_VENUE_UNKNOWN"


@dataclass(frozen=True)
class VenueCostDeclaration:
    """One venue's cost structure, including the fields nobody has measured yet.

    ``None`` on a rate field is "not measured", never "zero" — the distinction `worst_case_carry_r`
    already makes for an unpriceable hold, for the same reason.

    ``funding_multiplier`` and ``deployer_fee_scale`` are **recorded and deliberately not wired
    into the arithmetic.** They are measured facts about the venue (see the Hyperliquid entry) and
    keeping them here stops the measurement being lost, but applying them needs an answer this
    repo does not have: whether the venue's own ``fundingRate`` series already has the multiplier
    inside it. Charging it on top of a series that already carries it would double it, and that
    error is the same shape as the one this whole registry exists to prevent. Measure, then wire.
    """

    venue: str
    taker_fee_bps: float | None
    slippage_bps: float | None
    maker_fee_bps: float | None
    funding_bps_per_interval: float | None
    funding_intervals_per_day: int | None
    funding_multiplier: float | None = None
    deployer_fee_scale: float | None = None
    # The stop leg's slippage where a venue has measured it separately. ``None`` here does NOT
    # mean unmeasured-refuse (it is deliberately absent from ``_REQUIRED``): it means "no
    # separate figure — the stop pays this venue's own general slippage", which is what every
    # venue did before the split and keeps a declared venue's stop leg denominated in ITS
    # number rather than inheriting Binance's default. A venue whose GENERAL slippage is
    # unmeasured still refuses on that field as before.
    stop_slippage_bps: float | None = None
    note: str = ""

    _REQUIRED = ("taker_fee_bps", "slippage_bps", "maker_fee_bps",
                 "funding_bps_per_interval", "funding_intervals_per_day")

    def missing(self) -> tuple[str, ...]:
        """The rate fields this venue cannot yet supply. Empty means a model can be built."""
        return tuple(name for name in self._REQUIRED if getattr(self, name) is None)


VENUE_COST_DECLARATIONS: dict[str, VenueCostDeclaration] = {
    # Unchanged: the constants above, restated as a declaration so one venue is not a special
    # case of the registry that holds it.
    market_data.BINANCE_FUTURES: VenueCostDeclaration(
        venue=market_data.BINANCE_FUTURES,
        taker_fee_bps=DEFAULT_TAKER_FEE_BPS,
        slippage_bps=DEFAULT_SLIPPAGE_BPS,
        maker_fee_bps=DEFAULT_MAKER_FEE_BPS,
        # Explicit rather than the its-own-general fallback: the 12.0 interim IS a Binance
        # figure (the mean of this account's two measured stop fills), so it belongs on this
        # declaration — the fallback rule exists for venues with no stop measurement at all.
        stop_slippage_bps=DEFAULT_STOP_SLIPPAGE_BPS,
        funding_bps_per_interval=DEFAULT_FUNDING_BPS_PER_INTERVAL,
        funding_intervals_per_day=FUNDING_INTERVALS_PER_DAY,
        funding_multiplier=1.0,
        note="taker measured on this account 2026-07-26; maker is the published rate; stop "
             "slippage is the 2026-08-11 interim (mean of the two measured stop fills).",
    ),
    # Measured against the venue 2026-08-04 (`EQUITY_PERP_S1_MEASUREMENTS_V0.1.md`), public
    # read-only endpoints. Two fields are real numbers and three are holes, and the holes are
    # the reason this entry exists at all rather than being left out:
    #
    # - **24 settlements a day**, against Binance's 3. Eight times as often.
    # - **`assetToFundingMultiplier` 0.5** on every cohort symbol — a term the parent design did
    #   not have. Settling 8x more often while each settlement is halved is not either change
    #   alone, and a model that moved one without the other would be wrong in a direction that
    #   looks reasonable.
    # - **`deployerFeeScale` 1.0**, measured; its exact semantic (a multiplier on the base, or a
    #   share of an allowed cut) is **not** settled, so it is recorded and not applied.
    # - **Base taker/maker: unmeasured.** They are volume-tiered and `userFees` answers only for
    #   a real address, so this needs an account on that venue — Thomas's step, named as such in
    #   the S1 measurements. Copying the public table would be an estimate wearing a
    #   measurement's label.
    # - **Slippage: unmeasured.** Binance's 3.0 bps is itself an unmeasured carry-over from the
    #   source system, and an on-chain book is not the same market microstructure. Inheriting it
    #   here would be inheriting a guess about the wrong venue.
    market_data.HYPERLIQUID: VenueCostDeclaration(
        venue=market_data.HYPERLIQUID,
        taker_fee_bps=None,
        slippage_bps=None,
        maker_fee_bps=None,
        funding_bps_per_interval=None,
        funding_intervals_per_day=24,
        funding_multiplier=0.5,
        deployer_fee_scale=1.0,
        note=("funding cadence and multipliers measured 2026-08-04; base taker/maker need an "
              "account (`userFees`) and slippage needs an L2-book measurement."),
    ),
}


def cost_model_for(venue: str) -> CostModel:
    """The cost model for ``venue``, or a typed refusal naming what is missing.

    Never falls back to :class:`CostModel`'s defaults for an unknown or incompletely measured
    venue. The default model is *Binance's*, and handing it to another venue would produce a net
    R that is wrong in a way nothing downstream can detect — which is exactly what S2(a) is for.
    """
    declaration = VENUE_COST_DECLARATIONS.get(str(venue))
    if declaration is None:
        raise ToolError(
            COST_MODEL_VENUE_UNKNOWN,
            f"no cost declaration for venue {venue!r}; costs cannot be priced (fail-closed)",
        )
    missing = declaration.missing()
    if missing:
        raise ToolError(
            COST_MODEL_UNMEASURED,
            f"venue {venue!r} has unmeasured cost fields {list(missing)}; "
            f"{declaration.note}",
        )
    return CostModel(
        taker_fee_bps=float(declaration.taker_fee_bps),
        slippage_bps=float(declaration.slippage_bps),
        maker_fee_bps=float(declaration.maker_fee_bps),
        # The venue's own separate stop figure when one is declared, else ITS general slippage —
        # never the module default, which is Binance's number (see the field's comment above).
        stop_slippage_bps=float(
            declaration.slippage_bps if declaration.stop_slippage_bps is None
            else declaration.stop_slippage_bps
        ),
        funding_bps_per_interval=float(declaration.funding_bps_per_interval),
        funding_intervals_per_day=int(declaration.funding_intervals_per_day),
    )
