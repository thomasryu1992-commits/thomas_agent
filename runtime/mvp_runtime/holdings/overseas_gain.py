"""This year's realised overseas gain, rebuilt from Toss's closed orders (REBALANCING_PROPOSAL RB3', §2.5).

The 2.5M KRW yearly deduction decides how much an overseas sell may realise before it is taxed (§2.3 step 3).
Toss has no realised-gain field, so every overseas symbol is rebuilt from its first fill: buys add, sells take
their cost out, and a sell *settled* this year (``settlementDate``, not ``filledAt``) adds its gain.

**An estimate, and said so.** Two rules are not confirmed yet (§2.5, "확인 필요"):
- the cost method: FIFO and the moving average are both computed; the planner uses FIFO (the §3 sample line)
  and the render says "method unconfirmed";
- the KRW rate: a fill is converted at the rate the fire recorded for its KST day; a day before recording
  began uses the current rate and is counted as approximate.

**Commission.** A buy's commission is part of its cost and a sell's commission and tax come off its proceeds
(both are 필요경비). The broker's ``averagePurchasePrice`` excludes commission (its ``purchaseAmount`` is
quantity × that price), so the reconciliation compares a moving average without it.

**Refused, never partial:** a truncated read, a sell with nothing bought before it, a held symbol whose rebuilt
quantity or average price disagrees with the broker (a transfer in, a split, a missing page), a rebuilt
position the broker no longer holds, or a fill with no rate to convert it. The planner then treats the gain as
unknown (PR-A's path: overseas sells taxed in full).

**Pure:** no file, socket or clock. The rows carry symbols and amounts; what leaves this module is totals.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Iterable, Mapping

from .. import timeutil

FIFO, MOVING_AVERAGE = "fifo", "moving_average"
# The broker rounds its average price; a rebuilt one this close is the same position.
PRICE_TOLERANCE = 0.005
QUANTITY_TOLERANCE = 1e-6
SYMBOL_FIELDS = ("symbol", "stockCode")


@dataclass(frozen=True)
class Gain:
    status: str                       # ok / refused
    reason: str | None
    year: int
    fifo_krw: float | None = None
    moving_average_krw: float | None = None
    sells_counted: int = 0
    unsettled_sells: int = 0
    approx_fx_fills: int = 0

    def as_record(self) -> dict[str, Any]:
        return {"status": self.status, "reason": self.reason, "year": self.year,
                "fifo_krw": None if self.fifo_krw is None else round(self.fifo_krw),
                "moving_average_krw": None if self.moving_average_krw is None else round(self.moving_average_krw),
                "sells_counted": self.sells_counted, "unsettled_sells": self.unsettled_sells,
                "approx_fx_fills": self.approx_fx_fills}


@dataclass(frozen=True)
class _Fill:
    symbol: str
    side: str
    quantity: float
    amount: float              # filledAmount, in the order's currency
    costs: float               # commission + tax
    at: str
    day: str                   # the KST day its rate is taken from: settlementDate, else the fill's own day
    settled: str | None


def kst_day(at: str) -> str:
    return (timeutil.parse_iso(at) + timedelta(hours=9)).date().isoformat()


def _number(value: Any) -> float:
    return float(value or 0)


def _fills(orders: Iterable[Mapping[str, Any]]) -> list[_Fill]:
    fills = []
    for order in orders:
        if str(order.get("currency") or "").upper() != "USD":
            continue
        side = str(order.get("side") or "").upper()
        execution = order.get("execution") or {}
        quantity = _number(execution.get("filledQuantity"))
        if side not in ("BUY", "SELL") or quantity <= 0:
            continue
        symbol = next((str(order[n]) for n in SYMBOL_FIELDS if order.get(n) not in (None, "")), None)
        at = execution.get("filledAt") or order.get("orderedAt")
        if symbol is None or not at:
            raise ValueError("a filled overseas order without a symbol or a fill time")
        settled = str(execution.get("settlementDate") or "") or None
        fills.append(_Fill(symbol, side, quantity, _number(execution.get("filledAmount")),
                           _number(execution.get("commission")) + _number(execution.get("tax")),
                           str(at), settled or kst_day(str(at)), settled))
    return sorted(fills, key=lambda f: (timeutil.parse_iso(f.at), f.side != "BUY"))


def realised(orders: Iterable[Mapping[str, Any]], *, truncated: bool, year: int, rates: Mapping[str, float],
             current_rate: float | None, held: Mapping[str, tuple[float | None, float | None]]) -> Gain:
    """``held``: each overseas symbol the broker holds now -> (quantity, average purchase price in USD)."""
    if truncated:
        return Gain("refused", "the closed-order read was truncated", year)
    try:
        fills = _fills(orders)
    except (TypeError, ValueError) as exc:
        return Gain("refused", f"an unreadable closed order ({exc})", year)

    approx = 0

    def krw(usd: float, day: str) -> float:
        nonlocal approx
        rate = rates.get(day)
        if rate is None:
            if current_rate is None:
                raise LookupError(day)
            approx += 1
            rate = current_rate
        return usd * rate

    lots: dict[str, deque[list[float]]] = {}          # FIFO: [quantity, KRW cost per unit]
    average: dict[str, list[float]] = {}             # moving average: [quantity, KRW cost, USD cost w/o commission]
    gain = {FIFO: 0.0, MOVING_AVERAGE: 0.0}
    counted = unsettled = 0
    try:
        for f in fills:
            book = average.setdefault(f.symbol, [0.0, 0.0, 0.0])
            queue = lots.setdefault(f.symbol, deque())
            if f.side == "BUY":
                cost = krw(f.amount + f.costs, f.day)
                queue.append([f.quantity, cost / f.quantity])
                book[0] += f.quantity
                book[1] += cost
                book[2] += f.amount
                continue
            if f.quantity > book[0] + QUANTITY_TOLERANCE:
                return Gain("refused", f"a sell larger than what was bought before it ({f.symbol})", year)
            share = f.quantity / book[0]
            avg_cost = book[1] * share
            book[0] -= f.quantity
            book[1] -= avg_cost
            book[2] -= book[2] * share
            fifo_cost, left = 0.0, f.quantity
            while left > QUANTITY_TOLERANCE:
                lot = queue[0]
                take = min(left, lot[0])
                fifo_cost += take * lot[1]
                lot[0] -= take
                left -= take
                if lot[0] <= QUANTITY_TOLERANCE:
                    queue.popleft()
            if f.settled is None:
                unsettled += 1
                continue
            if int(f.settled[:4]) != year:
                continue
            proceeds = krw(f.amount - f.costs, f.day)
            gain[FIFO] += proceeds - fifo_cost
            gain[MOVING_AVERAGE] += proceeds - avg_cost
            counted += 1
    except LookupError as missing:
        return Gain("refused", f"no USD/KRW rate for {missing.args[0]} and none current", year)

    for symbol in set(average) | set(held):
        quantity, _, usd_cost = average.get(symbol, (0.0, 0.0, 0.0))
        broker_quantity, broker_price = held.get(symbol, (0.0, None))
        if abs(quantity - (broker_quantity or 0.0)) > QUANTITY_TOLERANCE * max(1.0, quantity):
            return Gain("refused", f"rebuilt quantity disagrees with the broker ({symbol})", year)
        if quantity > QUANTITY_TOLERANCE and broker_price:
            if abs(usd_cost / quantity - broker_price) > PRICE_TOLERANCE * broker_price:
                return Gain("refused", f"rebuilt average price disagrees with the broker ({symbol})", year)
    return Gain("ok", None, year, gain[FIFO], gain[MOVING_AVERAGE], counted, unsettled, approx)
