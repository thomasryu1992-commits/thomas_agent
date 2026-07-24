"""C12 cost model — fees and slippage for the factory backtest (source S4b port).

Ports the fee/slippage decomposition from ``crypto_AI_System/backtesting/
cost_model.py``, in **R-space only**: this port's accounting is deliberately
R-based, no quantity or notional fields anywhere (see ``paper.py``: "paper sizing
added nothing but noise"). A taker always fills at an adverse price (buys higher,
sells lower) and pays a fee on both legs; because ``risk_amount = qty *
risk_per_unit``, quantity cancels out of every R-denominated ratio algebraically —
verified numerically against the source's qty-based ``settle_trade`` (matches to
floating-point precision for both LONG and SHORT). The reduced form is a pure
function of ``(entry_price, exit_price, risk_per_unit, direction)`` with no qty
tracked anywhere, so nothing about the deliberate R-only design changes.

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

# Source defaults (backtesting/cost_model.py CostModel), unchanged.
DEFAULT_TAKER_FEE_BPS = 2.5
DEFAULT_SLIPPAGE_BPS = 3.0


@dataclass(frozen=True)
class CostModel:
    taker_fee_bps: float = DEFAULT_TAKER_FEE_BPS
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS

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
    fee_cost_r: float
    slippage_cost_r: float


def apply_cost_model(
    direction: str, entry_price: float, exit_price: float, risk: float, *, cost: CostModel | None = None,
) -> CostBreakdown:
    """Decompose a gross (intended-price) R multiple into net R after costs.

    ``risk`` is risk-per-unit (``|entry - stop|``) — exactly the position's existing
    ``risk`` field; no quantity/notional is needed (see module docstring for why it
    algebraically cancels). ``risk <= 0`` is the source's own division guard and
    returns all zeros rather than raising — defensive; a built entry plan never has
    a non-positive risk (``build_entry_plan`` already refuses those).
    """
    cost = cost or CostModel()
    if risk <= 0:
        return CostBreakdown(0.0, 0.0, 0.0, 0.0)
    sign = 1.0 if direction == "LONG" else -1.0
    gross_r = sign * (exit_price - entry_price) / risk

    entry_fill = cost.fill_price(entry_price, direction, "entry")
    exit_fill = cost.fill_price(exit_price, direction, "exit")
    on_fill_r = sign * (exit_fill - entry_fill) / risk
    slippage_cost_r = gross_r - on_fill_r
    fee_cost_r = (entry_fill + exit_fill) * cost.taker_fee_bps / 10000.0 / risk
    net_r = on_fill_r - fee_cost_r

    return CostBreakdown(
        gross_r=round(gross_r, 8),
        net_r=round(net_r, 8),
        fee_cost_r=round(fee_cost_r, 8),
        slippage_cost_r=round(slippage_cost_r, 8),
    )
