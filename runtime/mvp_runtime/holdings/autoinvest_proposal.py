"""G6-buy's scenario, drafted by the holdings fire (Thomas 2026-10-27); Thomas still confirms it before any order.

``docs/proposals/G6_BUY_CASH_AUTOINVEST_V0.1.md`` §2e holds the decision: "승인: G6 매수 시나리오 자동 작성(Q1~Q5
기본값: 토스 원화 전부−여유 3%, 1주씩 부족액 큰 자산군, 자산군별 후보 1위·채권 ¾·¼, 거래일 1회·5만 원 이상, Thomas
확인 후 주문) 설계·구현 (Thomas 2026-10-27)".

- Q1 the budget is the Toss KRW cash (no more than the daily cap) less 3%, which leaves room for the executor's 2%
  price guard and the fee. The scenario's cap is the cash itself, so a book price a little over the last price
  still fits.
- Q2 whole shares go one at a time to the slot whose KRW gap to target is largest, while a share still fits the
  budget and the slot is still under target. Nothing is bought past a target.
- Q3 each class buys its first candidate only. Bonds are two slots, Korean 3y (three quarters of the bond target)
  and US Treasuries 7-10y (a quarter, D-A), when the automatic pick names the US one.
- Q4 one draft per KST trading day, when the budget is at least 50,000 KRW and no scenario is pending or unknown.
  An unapproved draft is replaced by the next day's.
- Q5 nothing is ordered from a draft. ``holdings_board --autoinvest-approve`` with no spec shows it on Thomas's
  terminal and turns it into a scenario on his ``y``.

**The USD slot (§2g, Thomas 2026-10-27).** A class whose first candidate is overseas (global equity, a US-listed
world ETF) is a dollar slot. It fills in ``USD_STEP_KRW`` steps from the account's USD cash (less 3%), and
when that runs out, from the KRW budget as an exchange Thomas makes in the app: Toss's API never converts. The
draft orders the dollars there are as one amount; the part that needs exchanging is shown on Thomas's terminal
(``exchange_krw``), and Telegram says only that an exchange is needed. Once he exchanges, the next draft spends it.

:func:`slots` and :func:`draft` are pure. The draft names instruments and amounts, so its file
``holdings_autoinvest_proposal.json`` is LOCAL_ONLY; the Telegram line carries counts only (H3).
"""

from __future__ import annotations

import json
import math
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

from runtime.read_only_kernel import integrity

from .. import timeutil
from ..errors import ToolError
from ..filelock import locked
from . import allocation, autoinvest

PROPOSAL_FILENAME = "holdings_autoinvest_proposal.json"
PROPOSAL_RECORD_TYPE = "holdings_autoinvest_proposal.v1"

HEADROOM = 0.03                    # Q1: kept back from the cash for the 2% price guard and the fee
MIN_BUDGET_KRW = 50_000            # Q4
US_BOND_SHARE = 0.25               # D-A (TOTAL_ASSET_ALLOCATION, 2026-10-20): a quarter of bonds in US Treasuries
MAX_AGE = timedelta(days=4)        # a Friday draft is still approvable on Monday morning, never a week-old one
USD_STEP_KRW = 10_000              # §2g: a dollar slot fills in steps of this much exposure (an amount order)
MIN_US_ORDER_USD = 1.0             # a dollar amount below this is not ordered
# The classes a draft may buy. Coin is held outside Toss and cash is what is being spent.
BOUGHT = (allocation.GLOBAL_EQUITY, allocation.DOMESTIC_EQUITY, allocation.BONDS, allocation.GOLD)
KRW, USD = "krw", "usd"


def proposal_path(state_dir: Path) -> Path:
    return state_dir / PROPOSAL_FILENAME


def read(state_dir: Path) -> dict[str, Any]:
    target = proposal_path(state_dir)
    if not target.exists():
        return {}
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ToolError(autoinvest.AUTOINVEST_REFUSED, f"{PROPOSAL_FILENAME} does not parse") from None
    return body if isinstance(body, dict) else {}


def _write(state_dir: Path, body: Mapping[str, Any]) -> None:
    target = proposal_path(state_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    with locked(target.with_suffix(".lock"), code="HOLDINGS_AUTOINVEST_PROPOSAL_LOCKED",
                label="holdings autoinvest proposal"):
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps({**body, "record_type": PROPOSAL_RECORD_TYPE}, ensure_ascii=False, sort_keys=True,
                                  indent=1), encoding="utf-8")
        tmp.replace(target)


# --- the pure half ----------------------------------------------------------------------------------------------

def slots(*, class_krw: Mapping[str, float], positions: Iterable[Mapping[str, Any]], currencies: Mapping[str, str],
          candidates: Mapping[str, list[str]], us_bonds: Iterable[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """What each buyable slot holds against its target, and why a class has no slot (``notes``).

    ``class_krw`` is the fire's complete allocation; the NAV is the planned classes' sum, as the rebalancing
    calculator takes it. A US bond holding is a bond position Thomas's entries expose to USD. A slot whose
    instrument is overseas is paid in dollars (§2g)."""
    from .rebalance import PLANNED

    nav = sum(float(class_krw.get(c) or 0.0) for c in PLANNED)
    out: list[dict[str, Any]] = []
    notes: list[str] = []
    us = set(us_bonds)
    for asset_class in BOUGHT:
        ids = list(candidates.get(asset_class) or [])
        target = nav * allocation.TARGET_PCT[asset_class] / 100.0
        held = float(class_krw.get(asset_class) or 0.0)
        if asset_class == allocation.BONDS and any(i in us for i in ids) and any(i not in us for i in ids):
            us_held = sum(float(p.get("value_krw") or 0.0) for p in positions
                          if p.get("asset_class") == allocation.BONDS and currencies.get(str(p.get("instrument"))) == "usd")
            picks = (("bonds_kr3y", next(i for i in ids if i not in us), target * (1 - US_BOND_SHARE), held - us_held),
                     ("bonds_us7_10y", next(i for i in ids if i in us), target * US_BOND_SHARE, us_held))
        else:
            if asset_class == allocation.BONDS and ids:
                notes.append("bonds_unsplit")          # Thomas's own bond list: no US pick to split by
            picks = ((asset_class, ids[0] if ids else None, target, held),)
        for key, instrument, slot_target, slot_held in picks:
            if instrument is None:
                notes.append(f"{key}:no_candidate")
                continue
            out.append({"key": key, "asset_class": asset_class, "instrument": instrument,
                        "currency": USD if instrument.startswith("toss:overseas:") else KRW,
                        "target_krw": slot_target, "held_krw": slot_held})
    return out, notes


def draft(slot_rows: list[dict[str, Any]], *, prices: Mapping[str, float], cash_krw: float, fee_rate: float,
          cash_usd: float = 0.0, usd_krw: float | None = None, us_fee_rate: float = 0.0) -> dict[str, Any]:
    """Q1-Q2 over two pools. A KRW slot takes whole shares from the KRW budget; a USD slot takes ``USD_STEP_KRW``
    steps from the USD cash first, then from the KRW budget as an exchange to make. Each step goes to the largest
    gap a step still fits, never past a target; the whole draft stays inside the daily cap.

    Returns ``items`` (KRW items with ``quantity``, a USD item with ``amount_usd``), ``exchange_krw`` and
    ``exchange_usd`` (to exchange in the app), the ``budget_krw``, the ``estimate_krw`` and the scenario cap."""
    cap_left = autoinvest.DAILY_CAP_KRW * (1 - HEADROOM)
    krw_spendable = min(float(cash_krw), float(autoinvest.DAILY_CAP_KRW))
    krw_left = krw_spendable * (1 - HEADROOM)
    usd_left = float(cash_usd or 0) * usd_krw * (1 - HEADROOM) if usd_krw else 0.0     # in KRW
    budget = min(krw_left + usd_left, cap_left)
    result: dict[str, Any] = {"budget_krw": round(budget), "items": [], "estimate_krw": 0, "max_krw": int(krw_spendable),
                              "exchange_krw": 0, "exchange_usd": 0.0, "unpriced": []}
    if budget < MIN_BUDGET_KRW:
        result["status"] = "below_minimum"
        return result
    cost: dict[str, float] = {}
    for row in slot_rows:
        if row["currency"] == USD:
            if usd_krw:
                cost[row["key"]] = USD_STEP_KRW * (1 + us_fee_rate)
            else:
                result["unpriced"].append(row["key"])
            continue
        price = prices.get(row["instrument"].split(":")[2])
        if price and price > 0:
            cost[row["key"]] = float(price) * (1 + fee_rate)
        else:
            result["unpriced"].append(row["key"])
    step = {row["key"]: (USD_STEP_KRW if row["currency"] == USD else cost.get(row["key"], 0.0) / (1 + fee_rate))
            for row in slot_rows}
    added = {row["key"]: 0.0 for row in slot_rows}
    units = {row["key"]: 0 for row in slot_rows}
    from_usd = from_krw_exchange = 0.0
    while True:
        best, best_gap = None, 0.0
        for row in sorted(slot_rows, key=lambda r: r["key"]):
            key = row["key"]
            if key not in cost or cost[key] > cap_left:
                continue
            fits = (cost[key] <= usd_left + krw_left) if row["currency"] == USD else cost[key] <= krw_left
            gap = row["target_krw"] - row["held_krw"] - added[key]
            if fits and gap > best_gap:
                best, best_gap = row, gap
        if best is None:
            break
        key, spend = best["key"], cost[best["key"]]
        if best["currency"] == USD:
            take = min(spend, usd_left)
            usd_left -= take
            krw_left -= spend - take
            from_usd += take
            from_krw_exchange += spend - take
        else:
            krw_left -= spend
        units[key] += 1
        added[key] += step[key]
        cap_left -= spend
    for row in slot_rows:
        key = row["key"]
        if not units[key]:
            continue
        if row["currency"] == KRW:
            result["items"].append({"instrument": row["instrument"], "asset_class": row["asset_class"], "slot": key,
                                    "quantity": units[key], "price_krw": round(cost[key] / (1 + fee_rate)),
                                    "est_krw": round(units[key] * cost[key])})
            continue
        dollars = math.floor(from_usd / (1 + us_fee_rate) / usd_krw * 100) / 100
        if dollars >= MIN_US_ORDER_USD:
            result["items"].append({"instrument": row["instrument"], "asset_class": row["asset_class"], "slot": key,
                                    "amount_usd": f"{dollars:.2f}", "est_krw": round(from_usd)})
    result["exchange_krw"] = round(from_krw_exchange)
    result["exchange_usd"] = round(from_krw_exchange / usd_krw, 2) if usd_krw else 0.0
    result["estimate_krw"] = sum(i["est_krw"] for i in result["items"])
    usd_items = sum(i["est_krw"] for i in result["items"] if "amount_usd" in i)
    result["max_krw"] = int(min(autoinvest.DAILY_CAP_KRW, krw_spendable + usd_items / (1 - HEADROOM)))
    result["status"] = ("drafted" if result["items"] else "exchange_only" if result["exchange_krw"] else "nothing_fits")
    return result


# --- the fire's half --------------------------------------------------------------------------------------------

def refresh(feed: Any, state_dir: Path, *, now: str, board: Mapping[str, Any] | None,
            positions: list[Mapping[str, Any]] | None, currencies: Mapping[str, str],
            candidates: Mapping[str, list[str]], us_bonds: Iterable[str]) -> str:
    """Q4: draft today's scenario once, on a trading day, while nothing is pending. Returns a status word; only
    ``drafted`` and ``exchange_only`` write the file (and with it the notice). A failed read raises ``ToolError``
    to the caller."""
    scenario = autoinvest._read(state_dir)
    if scenario.get("status") in (autoinvest.PENDING, autoinvest.UNKNOWN) or any(
            i.get("open") for i in scenario.get("items") or []):
        return "scenario_active"
    today = autoinvest._kst(now).date().isoformat()
    if read(state_dir).get("made_on") == today:
        return "drafted_today"
    table = (board or {}).get("allocation")
    if not isinstance(table, Mapping) or table.get("complete") is not True or positions is None:
        return "nav_incomplete"
    class_krw = {name: float((row or {}).get("krw") or 0.0) for name, row in (table.get("classes") or {}).items()}
    slot_rows, notes = slots(class_krw=class_krw, positions=positions, currencies=currencies,
                             candidates=candidates, us_bonds=us_bonds)
    cash = feed.cash_krw()
    if cash is None:
        return "cash_unreadable"
    cash_usd, rate = 0.0, None
    if any(row["currency"] == USD for row in slot_rows):
        rate = feed.usd_krw_rate()
        cash_usd = feed.cash_usd() or 0.0
    reachable = min(float(cash), autoinvest.DAILY_CAP_KRW) + (cash_usd * rate if rate else 0.0)
    if min(reachable, autoinvest.DAILY_CAP_KRW) * (1 - HEADROOM) < MIN_BUDGET_KRW:
        return "below_minimum"
    if autoinvest._session(feed.kr_market_day(today), today) is None:
        return "not_trading_day"
    symbols = [row["instrument"].split(":")[2] for row in slot_rows if row["currency"] == KRW]
    prices = feed.last_prices(symbols) if symbols else {}
    fee, us_fee = autoinvest.FALLBACK_FEE_RATE, autoinvest.FALLBACK_FEE_RATE
    for row in feed.commission_rates():
        try:
            if row.get("marketCountry") == "KR":
                fee = float(row["commissionRate"])
            elif row.get("marketCountry") == "US":
                us_fee = float(row["commissionRate"])
        except (KeyError, TypeError, ValueError):
            pass
    result = draft(slot_rows, prices=prices, cash_krw=cash, fee_rate=fee, cash_usd=cash_usd, usd_krw=rate,
                   us_fee_rate=us_fee)
    if result["status"] not in ("drafted", "exchange_only"):
        return result["status"]
    names = {}
    if result["items"]:
        try:
            names = {str(r.get("symbol")): str(r.get("name") or "")
                     for r in feed.stock_info([i["instrument"].split(":")[2] for i in result["items"]])}
        except ToolError:
            pass                                       # a name is for Thomas's eye only; the id is what is ordered
    for item in result["items"]:
        item["name"] = names.get(item["instrument"].split(":")[2], "")
    proposal_id = integrity.short_id("ap", f"{today}|{json.dumps(result['items'], sort_keys=True)}|{result['exchange_krw']}")
    _write(state_dir, {**result, "proposal_id": proposal_id, "made_on": today, "made_at": now,
                       "cash_krw": round(float(cash)), "cash_usd": round(cash_usd, 2), "usd_krw": rate, "fee_rate": fee,
                       "notes": notes, "notice": {"token": proposal_id, "text": _notice_text(result)}})
    return result["status"]


def _notice_text(result: Mapping[str, Any]) -> str:
    """Counts and words only (H3): how many items, and whether an exchange is needed, never an amount."""
    exchange = " 글로벌 주식 몫은 달러 환전이 필요합니다(금액은 터미널)." if result["exchange_krw"] else ""
    if not result["items"]:
        return ("[보유 자산] 매수 시나리오 — 지금 주문할 몫은 없고, 글로벌 주식 몫은 달러 환전이 필요합니다. 금액은 Thomas "
                "터미널(--autoinvest-approve)에서 확인하세요. 환전하면 다음 거래일 시나리오가 그 달러로 삽니다.")
    shares = sum(int(i.get("quantity") or 0) for i in result["items"])
    us = sum(1 for i in result["items"] if "amount_usd" in i)
    what = f"{len(result['items'])}종목 {shares}주" + (f", 미국 금액 주문 {us}건" if us else "")
    return (f"[보유 자산] 매수 시나리오 준비 — {what}. Thomas 터미널에서 --autoinvest-approve로 확인하고 승인하면 "
            f"주문합니다. 승인하지 않으면 다음 거래일에 새로 만듭니다.{exchange}")


def notice(state_dir: Path) -> tuple[str, str] | None:
    body = read(state_dir)
    note = body.get("notice") or {}
    if not note.get("token") or note["token"] == body.get("told_token"):
        return None
    return note["token"], note["text"]


def mark_told(state_dir: Path, token: str, *, now: str) -> None:
    body = read(state_dir)
    if body:
        body.update(told_token=token, told_at=now)
        _write(state_dir, body)


# --- Thomas's terminal ------------------------------------------------------------------------------------------

def render(state_dir: Path) -> list[str]:
    """The draft as Thomas confirms it, with amounts. LOCAL_ONLY."""
    body = read(state_dir)
    lines = ["AUTOINVEST PROPOSAL - terminal only; do not paste into a chat, a model prompt or a channel"]
    if not body.get("items") and not body.get("exchange_krw"):
        return lines + ["proposal    : none"]
    usd = f", USD cash ${body.get('cash_usd', 0):,.2f}" if body.get("usd_krw") else ""
    lines.append(f"proposal    : {body['proposal_id']} made {body['made_at']} "
                 f"(Toss KRW cash {body['cash_krw']:,}{usd}; budget {body['budget_krw']:,} after {HEADROOM:.0%} headroom)")
    for item in body.get("items") or []:
        what = (f"${item['amount_usd']} (amount order)" if "amount_usd" in item
                else f"x{item['quantity']} @ ~{item['price_krw']:,}")
        lines.append(f"  {item['instrument']:22} {item.get('name') or '':24} {item['slot']:15} {what} "
                     f"= ~{item['est_krw']:,} KRW")
    if body.get("items"):
        lines.append(f"estimate    : ~{body['estimate_krw']:,} KRW; cap {body['max_krw']:,} KRW (domestic: limit orders "
                     f"at the book, held a day if over +{autoinvest.PRICE_GUARD:.0%}; US: market by amount, in the US "
                     "regular session)")
    if body.get("exchange_krw"):
        lines.append(f"exchange    : ~{body['exchange_krw']:,} KRW (~${body['exchange_usd']:,.2f}) into USD in the Toss app "
                     "for global equity; the next draft spends it (the API never converts)")
    if body.get("notes"):
        lines.append("left out    : " + ", ".join(body["notes"]))
    if body.get("approved_scenario"):
        lines.append(f"approved    : already, as {body['approved_scenario']}")
    return lines


def approve(state_dir: Path, *, now: str, candidates: Mapping[str, Iterable[str]]) -> dict[str, Any]:
    """Q5: turn the shown draft into Thomas's scenario, through the same :func:`autoinvest.approve` a typed spec
    goes through. Refuses a missing, stale or already approved draft."""
    body = read(state_dir)
    if not body.get("items"):
        raise ToolError(autoinvest.AUTOINVEST_REFUSED, "no drafted scenario to approve")
    if body.get("approved_scenario"):
        raise ToolError(autoinvest.AUTOINVEST_REFUSED, f"this draft is already {body['approved_scenario']}")
    if timeutil.parse_iso(now) - timeutil.parse_iso(body["made_at"]) > MAX_AGE:
        raise ToolError(autoinvest.AUTOINVEST_REFUSED, "the draft is older than 4 days; the next trading day drafts again")
    spec = ",".join(f"{i['instrument']}=${i['amount_usd']}" if "amount_usd" in i
                    else f"{i['instrument'].split(':')[2]}={i['quantity']}" for i in body["items"])
    scenario = autoinvest.approve(state_dir, spec, max_krw=int(body["max_krw"]), candidates=candidates, now=now)
    body.update(approved_scenario=scenario["scenario_id"], approved_at=now)
    _write(state_dir, body)
    return scenario
