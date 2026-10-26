"""The rebalancing proposal: the decided 5/25 band rule as orders, in the §8.5 tax order. Thomas places them.

``docs/proposals/REBALANCING_PROPOSAL_V0.1.md`` (RB1–RB6, Thomas 2026-10-26) over the rule
``TOTAL_ASSET_ALLOCATION_V0.1.md`` already decided:
- checked monthly;
- band b = min(5pp, 0.25 x target), with stock as one 40% sleeve and no band on cash;
- one class outside its band returns *every* class to target;
- executed cheapest tax first (§8.5): surplus cash, then tax-free sells, then overseas sells inside the year's
  2.5M KRW deduction, then taxable sells.

**Pure.** The terminal mode (``scripts/holdings_board.py --rebalance``) hands in the last fire's positions and
class totals, Thomas's approved candidates and, when known, the year's realised overseas gain. Nothing here
reads a file, a socket or the clock, and nothing it returns leaves the process: it names instruments and
amounts, so it is LOCAL_ONLY under H3 and renders on Thomas's terminal only.

**What it is not.** It places no order (P3) and is not G6, the automatic rebalancing decided to wait for H6c.
The engine margin (target 0) is left out: moving it is the engine lane's, not this calculator's (RB5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from . import allocation

# RB4 (Thomas 2026-10-26): no order under 50,000 KRW. It is not stored anywhere: the next check recomputes the
# drift, and what was left out this time is still in it.
MIN_ORDER_KRW = 50_000
OVERSEAS_DEDUCTION_KRW = 2_500_000

TAX_FREE, TAXED_DIVIDEND, OVERSEAS = "tax_free", "taxed_dividend", "overseas_deduction"
# §8 of TOTAL_ASSET_ALLOCATION: a domestic equity ETF's gain is tax-free; a domestic-listed foreign, bond or
# gold ETF's gain is dividend income at 15.4%; an overseas direct holding's gain above the year's 2.5M is 22%.
TAX_RATE = {TAX_FREE: 0.0, TAXED_DIVIDEND: 0.154, OVERSEAS: 0.22}
STEP = {TAX_FREE: 2, OVERSEAS: 3, TAXED_DIVIDEND: 4}

# The classes the plan moves. Engine margin is out (RB5); coin is held outside Toss, so its lines are manual.
PLANNED = (allocation.GLOBAL_EQUITY, allocation.DOMESTIC_EQUITY, allocation.BONDS, allocation.GOLD,
           allocation.COIN, allocation.CASH)
MANUAL = frozenset({allocation.COIN})


@dataclass(frozen=True)
class Position:
    """One held instrument, as the last fire read it."""

    instrument: str            # toss:<domestic|overseas>:<symbol> or binance:<ASSET>
    asset_class: str
    quantity: float | None
    value_krw: float
    pnl_krw: float | None      # the broker's unrealised gain, for the tax estimate

    @property
    def market(self) -> str:
        parts = self.instrument.split(":")
        return parts[1] if parts[0] == "toss" and len(parts) == 3 else "external"

    @property
    def price_krw(self) -> float | None:
        if not self.quantity or self.quantity <= 0:
            return None
        return self.value_krw / self.quantity


def tax_kind(position: Position, overrides: Mapping[str, str] | None = None) -> str:
    """§8's table from market and class (RB3), unless Thomas's entry names another for this instrument."""
    if overrides and overrides.get(position.instrument) in TAX_RATE:
        return overrides[position.instrument]
    if position.market == "overseas":
        return OVERSEAS
    if position.market == "domestic" and position.asset_class == allocation.DOMESTIC_EQUITY:
        return TAX_FREE
    if position.market == "domestic":
        return TAXED_DIVIDEND
    return TAX_FREE        # BTC spot: no tax on a 2026 sale (§8.5); the year rule is the caller's to apply


@dataclass
class Plan:
    status: str                                   # due / not_due / refused
    reason: str | None = None
    nav_krw: float = 0.0
    drift: dict[str, dict[str, Any]] = field(default_factory=dict)
    orders: list[dict[str, Any]] = field(default_factory=list)
    carried: list[dict[str, Any]] = field(default_factory=list)
    not_bought: list[str] = field(default_factory=list)
    after: dict[str, float] = field(default_factory=dict)
    est_tax_krw: float = 0.0
    overseas_gain_ytd_krw: float | None = None
    notes: list[str] = field(default_factory=list)


def _band_drift(class_krw: Mapping[str, float], nav: float) -> dict[str, dict[str, Any]]:
    out = {}
    for group, members in allocation.BAND_GROUPS.items():
        if group == allocation.ENGINE_MARGIN:
            continue
        target = sum(allocation.TARGET_PCT[m] for m in members)
        weight = sum(class_krw.get(m, 0.0) for m in members) / nav * 100.0
        band = allocation.band_pp(target)
        drift = weight - target
        out[group] = {"weight_pct": round(weight, 2), "target_pct": target, "band_pp": band,
                      "drift_pp": round(drift, 2), "outside": abs(drift) > band}
    return out


def _ratio(position: Position) -> float:
    return (position.pnl_krw or 0.0) / position.value_krw if position.value_krw else 0.0


def _sell_order(position: Position, amount: float, step: int, kind: str) -> dict[str, Any]:
    price = position.price_krw
    whole = position.market == "domestic"
    if whole and price:
        quantity = math.floor(amount / price)
        amount = quantity * price
    else:
        quantity = None if price is None else amount / price
    gain = 0.0 if position.pnl_krw is None or not position.value_krw else position.pnl_krw * amount / position.value_krw
    return {"side": "SELL", "instrument": position.instrument, "asset_class": position.asset_class, "step": step,
            "tax_kind": kind, "amount_krw": round(amount), "quantity": quantity, "whole_shares": whole,
            "gain_krw": round(gain), "manual": position.asset_class in MANUAL}


def plan(*, class_krw: Mapping[str, float], positions: Iterable[Position], complete: bool,
         candidates: Mapping[str, list[str]] | None = None, tax_overrides: Mapping[str, str] | None = None,
         overseas_gain_ytd_krw: float | None = None, force: bool = False) -> Plan:
    """The orders that return every planned class to target, cheapest tax first. See the module docstring."""
    positions = [p for p in positions if p.asset_class in PLANNED and p.value_krw > 0]
    if not complete:
        return Plan(status="refused", reason="the portfolio NAV is not complete (a part unread, or unclassified)")
    nav = sum(class_krw.get(c, 0.0) for c in PLANNED)
    if nav <= 0:
        return Plan(status="refused", reason="nothing held")
    drift = _band_drift(class_krw, nav)
    due = any(row["outside"] for row in drift.values())
    result = Plan(status="due" if due else "not_due", nav_krw=nav, drift=drift,
                  overseas_gain_ytd_krw=overseas_gain_ytd_krw)
    if not due and not force:
        return result

    target = {c: nav * allocation.TARGET_PCT[c] / 100.0 for c in PLANNED}
    excess = {c: max(0.0, class_krw.get(c, 0.0) - target[c]) for c in PLANNED if c != allocation.CASH}
    short = {c: max(0.0, target[c] - class_krw.get(c, 0.0)) for c in PLANNED if c != allocation.CASH}

    # Sells: each over-target class gives up its excess, its positions taken in tax order (§8.5 steps 2-4),
    # the smallest gain ratio first within a step.
    deduction_left = (None if overseas_gain_ytd_krw is None
                      else max(0.0, OVERSEAS_DEDUCTION_KRW - overseas_gain_ytd_krw))
    if deduction_left is None:
        result.notes.append("this year's overseas gain is unknown: step 3 is skipped, so overseas sells wait for "
                            "step 4 and are taxed in the estimate as if the 2.5M deduction were used up")
    sells: list[dict[str, Any]] = []
    room = deduction_left
    for asset_class, need in excess.items():
        if need <= 0:
            continue
        if asset_class in MANUAL:
            sells.append({"side": "SELL", "instrument": f"{asset_class}:manual", "asset_class": asset_class,
                          "step": STEP[TAX_FREE], "tax_kind": TAX_FREE, "amount_krw": round(need), "quantity": None,
                          "whole_shares": False, "gain_krw": 0, "manual": True})
            continue
        held = sorted((p for p in positions if p.asset_class == asset_class), key=lambda p: (_ratio(p), p.instrument))
        sold = {p.instrument: 0.0 for p in held}

        def sell(position: Position, amount: float, step: int) -> None:
            nonlocal need
            order = _sell_order(position, amount, step, tax_kind(position, tax_overrides))
            sold[position.instrument] += order["amount_krw"]
            need -= order["amount_krw"]
            sells.append(order)

        # Step 2: tax-free sells.
        for position in held:
            if need > 0 and tax_kind(position, tax_overrides) == TAX_FREE:
                sell(position, min(need, position.value_krw), STEP[TAX_FREE])
        # Step 3 (§2.3): overseas sells only as far as the year's deduction still covers their gain. Unknown
        # gain: no step 3, the overseas holdings wait for step 4.
        if room is not None:
            for position in held:
                if need <= 0 or tax_kind(position, tax_overrides) != OVERSEAS:
                    continue
                ratio = _ratio(position)
                cap = position.value_krw if ratio <= 0 else min(position.value_krw, room / ratio)
                if cap >= 1:
                    before = len(sells)
                    sell(position, min(need, cap), STEP[OVERSEAS])
                    room -= sells[-1]["gain_krw"] if len(sells) > before else 0.0
        # Step 4: what is left of the overseas and the taxed holdings, the smallest gain ratio first.
        for position in held:
            left = position.value_krw - sold[position.instrument]
            if need > 0 and left >= 1 and tax_kind(position, tax_overrides) != TAX_FREE:
                sell(position, min(need, left), STEP[TAXED_DIVIDEND])

    # Buys: each under-target class is filled from surplus cash and the sells, into Thomas's approved
    # candidates (RB2): the candidate with the smallest current holding first, so the class spreads across them.
    buys: list[dict[str, Any]] = []
    for asset_class, need in short.items():
        if need <= 0:
            continue
        approved = list((candidates or {}).get(asset_class) or [])
        if asset_class in MANUAL:
            buys.append({"side": "BUY", "instrument": f"{asset_class}:manual", "asset_class": asset_class, "step": 1,
                         "tax_kind": None, "amount_krw": round(need), "quantity": None, "whole_shares": False,
                         "gain_krw": 0, "manual": True})
            continue
        if not approved:
            result.not_bought.append(asset_class)
            continue
        held_value = {i: sum(p.value_krw for p in positions if p.instrument == i) for i in approved}
        price = {i: next((p.price_krw for p in positions if p.instrument == i), None) for i in approved}
        # Water-fill the candidates toward an equal share of the class.
        level_target = (sum(held_value.values()) + need) / len(approved)
        for instrument in sorted(approved, key=lambda i: (held_value[i], i)):
            amount = min(need, max(0.0, level_target - held_value[instrument]))
            if amount <= 0:
                continue
            whole = instrument.split(":")[1:2] == ["domestic"]
            quantity = None
            if price[instrument]:
                quantity = math.floor(amount / price[instrument]) if whole else amount / price[instrument]
                if whole:
                    amount = quantity * price[instrument]
            buys.append({"side": "BUY", "instrument": instrument, "asset_class": asset_class, "step": 1,
                         "tax_kind": None, "amount_krw": round(amount), "quantity": quantity, "whole_shares": whole,
                         "gain_krw": 0, "manual": False, "quantity_at_order": price[instrument] is None})
            need -= amount

    # RB4: below 50,000 KRW is carried, not ordered; the next check finds it again in the drift.
    for order in sells + buys:
        if order["amount_krw"] <= 0:       # a whole share costs more than the amount: nothing to place or carry
            continue
        # RB4'' (Thomas 2026-10-26): a whole-share buy of at least one share is placed whatever its amount.
        whole_buy = order["side"] == "BUY" and order["whole_shares"] and (order["quantity"] or 0) >= 1
        (result.orders if order["amount_krw"] >= MIN_ORDER_KRW or whole_buy else result.carried).append(order)
    result.orders.sort(key=lambda o: (o["step"], o["side"] != "SELL", o["instrument"]))

    # Tax: taxable steps by their rate on the gain; overseas gains above what is left of the deduction.
    left = deduction_left
    for order in result.orders:
        gain = max(0.0, order["gain_krw"])
        kind = order["tax_kind"]
        if kind == OVERSEAS:
            covered = 0.0 if left is None else min(gain, left)
            if left is not None:
                left -= covered
            order["est_tax_krw"] = round((gain - covered) * TAX_RATE[OVERSEAS])
        elif kind in TAX_RATE:
            order["est_tax_krw"] = round(gain * TAX_RATE[kind])
        else:
            order["est_tax_krw"] = 0
        result.est_tax_krw += order["est_tax_krw"]

    after = dict(class_krw)
    for order in result.orders:
        delta = order["amount_krw"] if order["side"] == "BUY" else -order["amount_krw"]
        after[order["asset_class"]] = after.get(order["asset_class"], 0.0) + delta
        after[allocation.CASH] = after.get(allocation.CASH, 0.0) - delta
    result.after = {c: round((after.get(c, 0.0) - target[c]) / nav * 100.0, 2) for c in PLANNED}
    return result


# --- the last fire's positions, and the terminal render --------------------------------------------------------

def toss_positions(snapshot: Any, entries: Any) -> list[dict[str, Any]]:
    """Each classified Toss holding of this fire, for its own local file (``store.positions_path``; the board and
    the snapshot stay free of any symbol): instrument, class, quantity, value and unrealised gain in KRW. Placed exactly as the allocation places it
    (its code table, then Thomas's entries); an unplaced holding is left out, and the allocation is then
    partial, which refuses the plan anyway."""
    entry_map = dict(entries.entries) if entries else {}
    rows = []
    for holding in snapshot.holdings:
        instrument = f"toss:{holding.market}:{holding.symbol}"
        asset_class = allocation.CLASSIFICATION.get(holding.symbol) or entry_map.get(instrument)
        value = allocation._holding_krw(holding.value, holding.currency, snapshot.usd_krw_rate)
        if asset_class is None or value is None:
            continue
        pnl = allocation._holding_krw(holding.unrealized_pnl, holding.currency, snapshot.usd_krw_rate)
        rows.append({"instrument": instrument, "asset_class": asset_class, "quantity": holding.quantity,
                     "value_krw": value, "pnl_krw": pnl})
    return rows


def plan_from_local(body: Any, *, positions: Any, candidates: Mapping[str, list[str]] | None,
                    age_seconds: float | None,
                    stale_after_seconds: float, overseas_gain_ytd_krw: float | None = None,
                    force: bool = False) -> Plan:
    """The plan over the last fire's local file: refused when it is old, its allocation partial, or its
    positions missing (a fire before this calculator existed)."""
    if not isinstance(body, Mapping):
        return Plan(status="refused", reason="no local board yet")
    if age_seconds is None or age_seconds > stale_after_seconds:
        return Plan(status="refused", reason=f"the last fire is older than {int(stale_after_seconds // 3600)} h")
    table = body.get("allocation")
    rows = positions
    if not isinstance(table, Mapping) or not isinstance(rows, list):
        return Plan(status="refused", reason="the last fire carries no allocation or positions yet")
    class_krw = {name: float((row or {}).get("krw") or 0.0) for name, row in (table.get("classes") or {}).items()}
    positions = [Position(str(r["instrument"]), str(r["asset_class"]), r.get("quantity"), float(r["value_krw"]),
                          r.get("pnl_krw")) for r in rows]
    return plan(class_krw=class_krw, positions=positions, complete=table.get("complete") is True,
                candidates=candidates, overseas_gain_ytd_krw=overseas_gain_ytd_krw, force=force)


def _won(value: float) -> str:
    return f"{value:,.0f}"


def render(result: Plan, *, as_of: Any) -> list[str]:
    """Terminal lines. Names instruments and amounts: LOCAL_ONLY, never a door, a channel or a model prompt."""
    lines = ["REBALANCE - terminal only; do not paste into a chat, a model prompt or a channel",
             f"snapshot    : {as_of}"]
    if result.status == "refused":
        return lines + [f"refused     : {result.reason}"]
    bands = ", ".join(f"{g} {row['drift_pp']:+.1f}pp{' OUTSIDE' if row['outside'] else ''} (±{row['band_pp']:g})"
                      for g, row in result.drift.items())
    lines += [f"NAV (excl. engine margin): {_won(result.nav_krw)} KRW", f"bands       : {bands}",
              "verdict     : " + ("rebalance due (one class outside returns every class)" if result.status == "due"
                                  else "no class outside its band")]
    if result.status != "due" and not result.orders:
        return lines
    labels = {1: "1 cash→buy", 2: "2 tax-free sell", 3: "3 overseas (deduction)", 4: "4 taxed sell"}
    lines.append("plan (tax order §8.5; Thomas places every order):")
    for o in result.orders:
        qty = ("" if o["quantity"] is None else
               f" {o['quantity']:,.0f} sh" if o["whole_shares"] else f" {o['quantity']:,.4f} units")
        extra = (" (manual, outside Toss)" if o["manual"] else
                 " (quantity at order: no price held)" if o.get("quantity_at_order") else "")
        tax = f"  est. tax {_won(o['est_tax_krw'])}" if o.get("est_tax_krw") else ""
        lines.append(f"  {labels[o['step']]:24} {o['side']:4} {o['instrument']:28} {_won(o['amount_krw']):>14} KRW"
                     f"{qty}{extra}{tax}")
    for o in result.carried:
        lines.append(f"  carried (< {_won(MIN_ORDER_KRW)} KRW, next check) {o['side']} {o['instrument']} "
                     f"{_won(o['amount_krw'])} KRW")
    for name in result.not_bought:
        lines.append(f"  not bought  : {name} has no candidates yet (the holdings fire picks them; "
                     "--approve-candidates overrides)")
    lines.append("after       : " + ", ".join(f"{c} {v:+.1f}pp" for c, v in result.after.items()))
    gain = ("unknown (overseas sells taxed in full in the estimate)" if result.overseas_gain_ytd_krw is None
            else f"{_won(result.overseas_gain_ytd_krw)} of {_won(OVERSEAS_DEDUCTION_KRW)} deduction")
    lines += [f"overseas gain this year: {gain}", f"est. tax    : {_won(result.est_tax_krw)} KRW (estimate)",
              "fees        : not included"]
    lines += [f"note        : {n}" for n in result.notes]
    return lines
