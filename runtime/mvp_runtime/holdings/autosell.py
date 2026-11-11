"""G6-sell: place a sell scenario Thomas confirmed, inside the domestic regular session (Thomas 2026-11-11).

``docs/proposals/G6_SELL_REBALANCE_V0.1.md`` holds the decision (SQ1-SQ5, every item as recommended); this is its
code. It is the sell half of G6 and mirrors G6-buy (``autoinvest.py``) line for line where the two agree.

- :func:`draft` is pure. It takes the rebalancing calculator's plan (the same one ``--rebalance`` shows) and keeps
  the sell lines this path may place (SQ1, SQ2): a domestic instrument, whole shares, taxed as a tax-free or a
  domestic-listed taxable ETF. Overseas and manual lines stay Thomas's. A line is cut to what fits the day's
  500,000 KRW (SQ4) and never takes a buy slot's instrument below that slot's target, so the buy draft and the
  sell draft cannot work against each other. What is cut is not stored: the next monthly check finds it again.
- :func:`refresh` is the fire's half of drafting. Once a month, on the first trading day from the monthly check
  day (RB5) on, it reads the plan's verdict. Only a verdict consumes the month: a closed gate, a refused plan, a
  closed market day or a failed read leave it for the next fire.
- :func:`approve` is the terminal's half (``holdings_board --autosell-approve``): Thomas sees the table and types
  ``y``. The draft is checked against the last fire's positions, so nothing more than is held can be approved.
- :func:`run` is the fire's half of placing.
  - Every fire it reads back each open order and books what filled. An order the exchange cancelled at the close
    leaves its remainder for the next trading day.
  - At most once per KST trading day, inside the regular session, it places the remaining quantity of each item
    with no open order: a limit sell at the highest bid at which the book covers the quantity, held for the day
    if that is more than 2% below the last price.
  - Right before placing it reads the shares held again. Fewer than the scenario still sells holds the whole
    scenario for the day (Thomas may have sold in the app).
  - It never sells an instrument the pending buy scenario is buying.
  - Before each POST it writes an ``intent`` line to its own journal and after it the outcome. It stops at the
    first rejection or unknown outcome and never resends.
- Sell proceeds are not spent here (SQ3): once they settle, the next buy draft sees them as cash.

The three files name instruments and amounts, so they are LOCAL_ONLY: the draft ``holdings_autosell_proposal.json``,
the scenario ``holdings_autosell.json`` and the append-only journal ``holdings_autosell_journal.jsonl``. The
journal is its own file because the buy path reads every row of the buy journal as a buy. A Telegram line carries
counts and state only (H3).

Unverified until the first real sell (the packet's §6): Toss's rejection codes for a sell, and whether the order
book answer names its bid side ``bids``. An empty bid side holds the day.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from .. import timeutil
from ..errors import ToolError
from ..filelock import locked
from . import autoinvest, rebalance
from .toss_orders import OrderRejected, OrderUnknown

SCENARIO_FILENAME = "holdings_autosell.json"
JOURNAL_FILENAME = "holdings_autosell_journal.jsonl"
PROPOSAL_FILENAME = "holdings_autosell_proposal.json"
RECORD_TYPE = "holdings_autosell.v1"
PROPOSAL_RECORD_TYPE = "holdings_autosell_proposal.v1"
AUTOSELL_REFUSED = "HOLDINGS_AUTOSELL_REFUSED"

DAILY_SELL_CAP_KRW = 500_000       # SQ4: 50만 원 a KST day, apart from the buy cap
HEADROOM = 0.03                    # the draft leaves this much under the cap for a price that rose since the fire
MAX_ITEMS = 10
VALID_DAYS = 7
MAX_AGE = timedelta(days=4)        # a Friday draft is still approvable on Monday morning, never a week-old one
# SQ2: what this path may sell. An overseas direct holding is Thomas's, whatever tax kind he named for it.
SOLD_KINDS = (rebalance.TAX_FREE, rebalance.TAXED_DIVIDEND)
_DOMESTIC = re.compile(r"^toss:domestic:[0-9A-Za-z]{6}$")

PENDING, EXECUTED, STOPPED, UNKNOWN, EXPIRED = (autoinvest.PENDING, autoinvest.EXECUTED, autoinvest.STOPPED,
                                                autoinvest.UNKNOWN, autoinvest.EXPIRED)


def scenario_path(state_dir: Path) -> Path:
    return state_dir / SCENARIO_FILENAME


def journal_path(state_dir: Path) -> Path:
    return state_dir / JOURNAL_FILENAME


def proposal_path(state_dir: Path) -> Path:
    return state_dir / PROPOSAL_FILENAME


def _read_file(target: Path) -> dict[str, Any]:
    if not target.exists():
        return {}
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ToolError(AUTOSELL_REFUSED, f"{target.name} does not parse") from None
    return body if isinstance(body, dict) else {}


def _write_file(target: Path, body: Mapping[str, Any], record_type: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps({**body, "record_type": record_type}, ensure_ascii=False, sort_keys=True, indent=1),
                   encoding="utf-8")
    tmp.replace(target)


def _read(state_dir: Path) -> dict[str, Any]:
    return _read_file(scenario_path(state_dir))


def _write(state_dir: Path, body: Mapping[str, Any]) -> None:
    _write_file(scenario_path(state_dir), body, RECORD_TYPE)


def read_proposal(state_dir: Path) -> dict[str, Any]:
    return _read_file(proposal_path(state_dir))


def _write_proposal(state_dir: Path, body: Mapping[str, Any]) -> None:
    with locked(proposal_path(state_dir).with_suffix(".lock"), code="HOLDINGS_AUTOSELL_PROPOSAL_LOCKED",
                label="holdings autosell proposal"):
        _write_file(proposal_path(state_dir), body, PROPOSAL_RECORD_TYPE)


def journal(state_dir: Path) -> list[dict[str, Any]]:
    target = journal_path(state_dir)
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append(state_dir: Path, row: Mapping[str, Any]) -> None:
    target = journal_path(state_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _active(scenario: Mapping[str, Any]) -> bool:
    """A scenario that may still place or holds an order at the exchange."""
    return scenario.get("status") == PENDING or any(i.get("open") for i in scenario.get("items") or [])


# --- the pure half: the plan's sell lines this path may place ---------------------------------------------------

def draft(plan: rebalance.Plan, *, slot_rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """The sell scenario the plan allows today: ``items`` (instrument, whole quantity, estimate, tax kind and the
    estimated gain and tax of what is kept), the totals, and ``left_out`` as counts by reason.

    A line is kept in the plan's own tax order. Its amount is the least of the plan's amount, what the day's cap
    still has, and (for a buy slot's instrument) what the slot holds over its target. Under 50,000 KRW it is not
    placed (RB4') and not stored."""
    room = {str(row["instrument"]): max(0.0, float(row["held_krw"]) - float(row["target_krw"])) for row in slot_rows}
    budget = DAILY_SELL_CAP_KRW * (1 - HEADROOM)
    items: list[dict[str, Any]] = []
    left_out = {"manual": 0, "overseas": 0, "tax_kind": 0, "kept_for_buy_slot": 0, "over_cap": 0, "below_minimum": 0}
    seen: set[str] = set()
    for order in plan.orders:
        if order.get("side") != "SELL":
            continue
        instrument = str(order.get("instrument"))
        if order.get("manual"):
            left_out["manual"] += 1
            continue
        if not _DOMESTIC.match(instrument):
            left_out["overseas"] += 1
            continue
        kind = order.get("tax_kind")
        if kind not in SOLD_KINDS or not order.get("whole_shares") or instrument in seen:
            left_out["tax_kind"] += 1
            continue
        seen.add(instrument)
        quantity = int(order.get("quantity") or 0)
        amount = float(order.get("amount_krw") or 0)
        if quantity < 1 or amount <= 0:
            continue
        price = amount / quantity
        slot_room = room.get(instrument, math.inf)
        take = min(quantity, math.floor(min(amount, slot_room, budget) / price + 1e-9))
        # One reason per line that is cut or dropped: the slot it would empty, the cap, or RB4's minimum.
        cut = None if take >= quantity else "kept_for_buy_slot" if slot_room < min(amount, budget) else "over_cap"
        if len(items) >= MAX_ITEMS:
            left_out["over_cap"] += 1
            continue
        if take < 1 or take * price < rebalance.MIN_ORDER_KRW:
            left_out[cut or "below_minimum"] += 1
            continue
        if cut:
            left_out[cut] += 1
        kept = take * price
        gain = float(order.get("gain_krw") or 0) * kept / amount
        items.append({"instrument": instrument, "asset_class": order.get("asset_class"), "quantity": take,
                      "price_krw": round(price), "est_krw": round(kept), "step": order.get("step"),
                      "tax_kind": kind, "gain_krw": round(gain),
                      "est_tax_krw": round(max(0.0, gain) * rebalance.TAX_RATE[kind])})
        budget -= kept
    return {"items": items, "estimate_krw": sum(i["est_krw"] for i in items),
            "est_tax_krw": sum(i["est_tax_krw"] for i in items), "left_out": left_out,
            "status": "drafted" if items else "nothing_to_sell"}


# --- the fire's half of drafting --------------------------------------------------------------------------------

def refresh(feed: Any, state_dir: Path, *, now: str, plan: rebalance.Plan, slot_rows: list[Mapping[str, Any]],
            as_of: Any = None) -> str:
    """This month's check, once: on a trading day, over a plan that could be judged, while no sell scenario is
    active. Returns a status word. ``drafted`` writes the draft and its notice; ``not_due`` and ``nothing_to_sell``
    write only the month's verdict. Every other word leaves the month open. A failed read raises ``ToolError``."""
    if _active(_read(state_dir)):
        return "scenario_active"
    today = autoinvest._kst(now).date().isoformat()
    month = today[:7]
    if read_proposal(state_dir).get("checked_month") == month:
        return "checked_this_month"
    if plan.status == "refused":
        return "plan_refused"
    if autoinvest._session(feed.kr_market_day(today), today) is None:
        return "not_trading_day"
    result = draft(plan, slot_rows=slot_rows) if plan.status == "due" else {"items": [], "status": "not_due"}
    body: dict[str, Any] = {"checked_month": month, "checked_at": now, "verdict": result["status"]}
    if result["items"]:
        names: dict[str, str] = {}
        try:
            names = {str(r.get("symbol")): str(r.get("name") or "")
                     for r in feed.stock_info([i["instrument"].split(":")[2] for i in result["items"]])}
        except ToolError:
            pass                                       # a name is for Thomas's eye only; the id is what is ordered
        for item in result["items"]:
            item["name"] = names.get(item["instrument"].split(":")[2], "")
        proposal_id = integrity.short_id("sp", f"{today}|{json.dumps(result['items'], sort_keys=True)}")
        body.update({**result, "proposal_id": proposal_id, "made_on": today, "made_at": now, "plan_as_of": as_of,
                     "notice": {"token": proposal_id, "text": _proposal_text(result)}})
    _write_proposal(state_dir, body)
    return result["status"]


def _proposal_text(result: Mapping[str, Any]) -> str:
    """Counts and words only (H3): how many items and shares, never an instrument or an amount."""
    shares = sum(int(i["quantity"]) for i in result["items"])
    manual = result["left_out"]["manual"] + result["left_out"]["overseas"]
    rest = f" 수동으로 남는 매도 줄 {manual}개는 --rebalance에서 확인하세요." if manual else ""
    return (f"[보유 자산] 매도 시나리오 준비 — {len(result['items'])}종목 {shares}주. Thomas 터미널에서 "
            f"--autosell-approve로 확인하고 승인하면 주문합니다. 승인하지 않으면 다음 달 점검에서 다시 봅니다.{rest}")


def proposal_notice(state_dir: Path) -> tuple[str, str] | None:
    body = read_proposal(state_dir)
    note = body.get("notice") or {}
    if not note.get("token") or note["token"] == body.get("told_token"):
        return None
    return note["token"], note["text"]


def mark_proposal_told(state_dir: Path, token: str, *, now: str) -> None:
    body = read_proposal(state_dir)
    if body:
        body.update(told_token=token, told_at=now)
        _write_proposal(state_dir, body)


# --- Thomas's terminal ------------------------------------------------------------------------------------------

_KIND_TEXT = {rebalance.TAX_FREE: "tax-free", rebalance.TAXED_DIVIDEND: "taxed 15.4% (dividend income)"}


def render(state_dir: Path) -> list[str]:
    """The draft as Thomas confirms it, with amounts. LOCAL_ONLY."""
    body = read_proposal(state_dir)
    lines = ["AUTOSELL PROPOSAL - terminal only; do not paste into a chat, a model prompt or a channel"]
    if not body.get("items"):
        return lines + [f"proposal    : none (this month's check: {body.get('verdict') or 'not yet'})"]
    lines.append(f"proposal    : {body['proposal_id']} made {body['made_at']} (plan of {body.get('plan_as_of')})")
    for item in body["items"]:
        lines.append(f"  {item['instrument']:22} {item.get('name') or '':24} SELL x{item['quantity']} @ "
                     f"~{item['price_krw']:,} = ~{item['est_krw']:,} KRW  {_KIND_TEXT.get(item['tax_kind'], '?')}"
                     f"  est. gain {item['gain_krw']:,}  est. tax {item['est_tax_krw']:,}")
    lines.append(f"estimate    : ~{body['estimate_krw']:,} KRW of sells, est. tax {body['est_tax_krw']:,} KRW; day cap "
                 f"{DAILY_SELL_CAP_KRW:,} KRW (limit orders at the bid side of the book, held a day if more than "
                 f"{autoinvest.PRICE_GUARD:.0%} under the last price)")
    left = {k: v for k, v in (body.get("left_out") or {}).items() if v}
    if left:
        lines.append("left out    : " + ", ".join(f"{k} {v}" for k, v in sorted(left.items()))
                     + " (see --rebalance; not stored, the next check finds them again)")
    lines.append("proceeds    : not spent here; once settled the next buy draft sees them as cash")
    if body.get("approved_scenario"):
        lines.append(f"approved    : already, as {body['approved_scenario']}")
    return lines


def approve(state_dir: Path, *, now: str, positions: list[Mapping[str, Any]] | None) -> dict[str, Any]:
    """Turn the shown draft into Thomas's scenario, as ``pending``. Refuses a missing, stale or already approved
    draft, an item that is not a whole number of domestic shares, more shares than the last fire held, and a
    total over the day's cap. A new approval replaces the last scenario unless that one still has an open order."""
    body = read_proposal(state_dir)
    if not body.get("items"):
        raise ToolError(AUTOSELL_REFUSED, "no drafted sell scenario to approve")
    if body.get("approved_scenario"):
        raise ToolError(AUTOSELL_REFUSED, f"this draft is already {body['approved_scenario']}")
    if timeutil.parse_iso(now) - timeutil.parse_iso(body["made_at"]) > MAX_AGE:
        raise ToolError(AUTOSELL_REFUSED, "the draft is older than 4 days; the next monthly check drafts again")
    if positions is None:
        raise ToolError(AUTOSELL_REFUSED, "the last fire left no positions to check the draft against")
    held = {str(row.get("instrument")): float(row.get("quantity") or 0) for row in positions}
    items = []
    for item in body["items"]:
        instrument, quantity = str(item.get("instrument")), item.get("quantity")
        if (not _DOMESTIC.match(instrument) or not isinstance(quantity, int) or isinstance(quantity, bool)
                or quantity < 1 or item.get("tax_kind") not in SOLD_KINDS):
            raise ToolError(AUTOSELL_REFUSED, "a sell item is a whole number of domestic shares of a kind this path sells")
        if quantity > math.floor(held.get(instrument, 0.0)):
            raise ToolError(AUTOSELL_REFUSED, "the draft sells more shares than the last fire held")
        items.append({"instrument": instrument, "quantity": quantity, "asset_class": item.get("asset_class"),
                      "tax_kind": item["tax_kind"], "est_krw": int(item.get("est_krw") or 0)})
    if len(items) > MAX_ITEMS or len({i["instrument"] for i in items}) != len(items):
        raise ToolError(AUTOSELL_REFUSED, f"1..{MAX_ITEMS} distinct instruments")
    if sum(i["est_krw"] for i in items) > DAILY_SELL_CAP_KRW:
        raise ToolError(AUTOSELL_REFUSED, f"the draft is over the day's {DAILY_SELL_CAP_KRW:,} KRW")
    scenario_id = integrity.short_id("as", f"{now}|{body['proposal_id']}")
    expires = (timeutil.parse_iso(now) + timedelta(days=VALID_DAYS)).isoformat().replace("+00:00", "Z")
    scenario = {"scenario_id": scenario_id, "proposal_id": body["proposal_id"], "items": items,
                "max_krw": DAILY_SELL_CAP_KRW, "approved_at": now, "expires_at": expires, "status": PENDING}
    with locked(scenario_path(state_dir).with_suffix(".lock"), code="HOLDINGS_AUTOSELL_LOCKED",
                label="holdings autosell"):
        # A replaced scenario takes its open orders' tracking with it: an order at the exchange would go unbooked.
        if any(i.get("open") for i in _read(state_dir).get("items") or []):
            raise ToolError(AUTOSELL_REFUSED, "the current sell scenario has an open order; approve again once it "
                                              "closes (filled, or cancelled at the close)")
        _write(state_dir, scenario)
    body.update(approved_scenario=scenario_id, approved_at=now)
    _write_proposal(state_dir, body)
    return scenario


# --- the fire's half of placing ---------------------------------------------------------------------------------

_HOLD_TEXT = {
    "orders_off": "매도 경로가 꺼져 있습니다(MVP_TOSS_ORDERS)",
    "over_scenario_cap": "추정 금액이 승인한 상한을 넘습니다",
    "over_daily_cap": "오늘 자동 매도 한도(50만 원)를 넘습니다",
    "holding_short": "보유 수량이 시나리오 수량보다 적습니다",
    "buying_same_instrument": "매수 시나리오가 같은 종목을 사고 있습니다",
    "buy_scenario_unreadable": "매수 시나리오를 읽지 못했습니다",
    "price_unreadable": "현재가를 읽지 못한 종목이 있습니다",
    "read_failed": "토스 조회에 실패했습니다",
    "book_too_thin": "매수 호가 잔량이 주문 수량보다 적습니다",
    "price_guard": f"호가 가격이 현재가보다 {round(autoinvest.PRICE_GUARD * 100)}% 넘게 낮습니다",
}


def _notice(body: dict[str, Any], event: str, text: str) -> None:
    body["notice"] = {"token": f"{body['scenario_id']}|{event}", "text": text}


def _hold(state_dir: Path, body: dict[str, Any], reason: str) -> dict[str, Any]:
    body["hold_reason"] = reason
    _notice(body, f"hold|{reason}", f"[보유 자산] 자동 매도 보류 — {_HOLD_TEXT.get(reason, reason)}. 다음 거래일에 다시 봅니다.")
    _write(state_dir, body)
    return {"status": f"hold:{reason}"}


def _remaining(item: Mapping[str, Any]) -> int:
    return int(item["quantity"]) - int(item.get("filled") or 0)


def _track(feed: Any, body: dict[str, Any], now: str) -> None:
    """Read back every open order (each fire, in or out of session). FILLED closes it; CANCELED, REJECTED or
    REPLACED closes it with what filled, and the rest waits for the next trading day; anything else keeps it open,
    so no second order is placed beside it."""
    closed_short = 0
    for item in body["items"]:
        held = item.get("open")
        if not held:
            continue
        try:
            state = feed.fill_state(held["order_id"])
        except ToolError:
            continue                                   # unknown this fire: the order stays open, nothing new
        status = state.get("status")
        filled = int(min(float(state.get("filled") or 0), held["quantity"]))
        if status == "FILLED" or (status in autoinvest.DONE_STATUSES and filled >= held["quantity"]):
            filled = held["quantity"]
        elif status not in autoinvest.DONE_STATUSES:
            held["filled_so_far"] = filled
            continue
        item["filled"] = int(item.get("filled") or 0) + filled
        item["sold_krw"] = round(float(item.get("sold_krw") or 0) + filled * held["price"])
        item.pop("open", None)
        closed_short += held["quantity"] - filled
    if sum(_remaining(i) for i in body["items"]) == 0:
        body.update(status=EXECUTED, completed_at=now)
        _notice(body, "filled", f"[보유 자산] 자동 매도 — 승인한 시나리오 {len(body['items'])}종목 전량 체결. "
                                "대금은 정산 뒤 다음 매수 시나리오가 씁니다.")
    elif closed_short:
        _notice(body, f"short|{now}", f"[보유 자산] 자동 매도 — 미체결 {closed_short}주는 장 마감에 취소됐습니다. "
                                       "다음 거래일에 남은 수량만 다시 주문합니다.")


def _committed_krw(body: Mapping[str, Any]) -> float:
    """What the scenario has sold or holds open, in KRW."""
    total = sum(float(i.get("sold_krw") or 0) for i in body["items"])
    return total + sum(i["open"]["quantity"] * i["open"]["price"] for i in body["items"] if i.get("open"))


def run(feed: Any, orders: Any, state_dir: Path, *, now: str,
        buy_scenario: Mapping[str, Any] | None) -> dict[str, Any]:
    """One look per fire: open orders are read back every fire; new orders at most once per KST trading day,
    inside the session. ``buy_scenario`` is the buy path's scenario file, or None when it could not be read."""
    with locked(scenario_path(state_dir).with_suffix(".lock"), code="HOLDINGS_AUTOSELL_LOCKED",
                label="holdings autosell"):
        body = _read(state_dir)
        if body.get("status") != PENDING:
            return {"status": "none"}
        local = autoinvest._kst(now)
        today = local.date().isoformat()
        _track(feed, body, now)
        if body["status"] == EXECUTED:
            _write(state_dir, body)
            return {"status": EXECUTED}
        if timeutil.parse_iso(now) >= timeutil.parse_iso(body["expires_at"]):
            got = sum(int(i.get("filled") or 0) for i in body["items"])
            body.update(status=EXPIRED)
            _notice(body, "expired", f"[보유 자산] 자동 매도 시나리오 만료 — 체결 {got}주, 남은 "
                                     f"{sum(_remaining(i) for i in body['items'])}주는 다음 달 점검에서 다시 봅니다.")
            _write(state_dir, body)
            return {"status": EXPIRED}
        if not getattr(orders, "enabled", False):
            if body.get("hold_reason") == "orders_off":
                _write(state_dir, body)
                return {"status": "hold:orders_off"}
            return _hold(state_dir, body, "orders_off")
        wanted = [i for i in body["items"] if _remaining(i) > 0 and not i.get("open")]
        if not wanted:
            _write(state_dir, body)
            return {"status": "waiting"}
        if body.get("last_attempt_day") == today:
            _write(state_dir, body)
            return {"status": "done_today"}
        try:
            session = autoinvest._session(feed.kr_market_day(today), today)
        except ToolError:
            return _hold(state_dir, body, "read_failed")        # told once; the next fire looks again
        if session is None or not (session[0] + autoinvest.OPEN_AFTER <= local
                                   <= session[1] - autoinvest.CLOSE_BEFORE):
            _write(state_dir, body)
            return {"status": "outside_session"}
        body["last_attempt_day"] = today
        try:
            checked = _checks(feed, state_dir, body, today, wanted, buy_scenario)
        except ToolError:
            return _hold(state_dir, body, "read_failed")        # a read that failed holds the day, never half-orders
        if "status" in checked:
            return checked
        return _place(orders, state_dir, body, today, now, wanted, checked["prices"], checked["estimates"])


def _checks(feed: Any, state_dir: Path, body: dict[str, Any], today: str, wanted: list[dict[str, Any]],
            buy_scenario: Mapping[str, Any] | None) -> dict[str, Any]:
    """Every read and check before the first POST: ACTIVE ETFs, no instrument the buy scenario is buying, the
    shares still held, the book's price for each remaining quantity and its guard, the two caps. Returns a hold
    or stop result (``status``) or prices and estimates."""
    symbols = [i["instrument"].split(":")[2] for i in wanted]
    info = {str(r.get("symbol")): r for r in feed.stock_info(symbols)}
    bad = [s for s in symbols if (info.get(s) or {}).get("status") != "ACTIVE"
           or (info.get(s) or {}).get("securityType") not in ("ETF", "FOREIGN_ETF")]
    if bad:
        body.update(status=STOPPED, stop_reason="not_an_active_etf")
        _notice(body, "stopped|not_an_active_etf",
                f"[보유 자산] 자동 매도 중단 — 거래 가능한 ETF가 아닌 종목 {len(bad)}개. 주문 0건.")
        _write(state_dir, body)
        return {"status": "stopped:not_an_active_etf"}
    if buy_scenario is None:
        return _hold(state_dir, body, "buy_scenario_unreadable")
    if _active(buy_scenario):
        buying = {str(i.get("instrument")) for i in buy_scenario.get("items") or []
                  if i.get("open") or autoinvest._remaining(i) > 0}
        if any(i["instrument"] in buying for i in wanted):
            return _hold(state_dir, body, "buying_same_instrument")
    held = feed.domestic_quantities()
    if any(math.floor(held.get(symbol, 0.0)) < _remaining(item) for item, symbol in zip(wanted, symbols)):
        return _hold(state_dir, body, "holding_short")
    last = feed.last_prices(symbols)
    if any(s not in last for s in symbols):
        return _hold(state_dir, body, "price_unreadable")
    prices: dict[str, int] = {}
    for item, symbol in zip(wanted, symbols):
        price = autoinvest._limit_price(feed.bids(symbol), _remaining(item))
        if price is None:
            return _hold(state_dir, body, "book_too_thin")
        if price < last[symbol] * (1 - autoinvest.PRICE_GUARD):
            return _hold(state_dir, body, "price_guard")
        prices[item["instrument"]] = int(round(price))
    estimates = {i["instrument"]: _remaining(i) * prices[i["instrument"]] for i in wanted}
    total = sum(estimates.values())
    sold_today = sum(float(r.get("est_krw") or 0) for r in journal(state_dir)
                     if r.get("day") == today and r.get("state") == "intent")
    body["estimate_krw"] = round(total)
    if _committed_krw(body) + total > body["max_krw"]:
        return _hold(state_dir, body, "over_scenario_cap")
    if sold_today + total > DAILY_SELL_CAP_KRW:
        return _hold(state_dir, body, "over_daily_cap")
    return {"prices": prices, "estimates": estimates}


def _place(orders: Any, state_dir: Path, body: dict[str, Any], today: str, now: str, wanted: list[dict[str, Any]],
           prices: Mapping[str, int], estimates: Mapping[str, float]) -> dict[str, Any]:
    """Today's limit sells in turn, journalled before and after; stops at the first rejection or unknown."""
    placed, outcome, code = 0, PENDING, None
    sent = {r.get("client_order_id") for r in journal(state_dir)}
    for item in wanted:
        cid = integrity.short_id("sord", f"{body['scenario_id']}|{item['instrument']}|{today}")
        if cid in sent:                         # an earlier attempt reached this order: never resend
            outcome, code = UNKNOWN, "journaled"
            break
        quantity, price = _remaining(item), prices[item["instrument"]]
        base = {"at": now, "day": today, "side": "SELL", "scenario_id": body["scenario_id"], "client_order_id": cid,
                "instrument": item["instrument"], "asset_class": item.get("asset_class"), "quantity": quantity,
                "price": price, "est_krw": round(estimates[item["instrument"]])}
        _append(state_dir, {**base, "state": "intent"})
        try:
            order_id = orders.sell_kr(item["instrument"].split(":")[2], quantity, price=price, client_order_id=cid)
        except OrderRejected as exc:
            code = exc.toss_code or str(exc.status)
            _append(state_dir, {**base, "state": "rejected", "code": code, "toss": exc.detail})
            outcome = STOPPED
            break
        except OrderUnknown as exc:
            _append(state_dir, {**base, "state": "unknown", "code": exc.why, "toss": exc.detail})
            outcome, code = UNKNOWN, "unknown"
            break
        except ToolError as exc:
            _append(state_dir, {**base, "state": "rejected", "code": exc.reason_code})
            outcome, code = STOPPED, exc.reason_code
            break
        _append(state_dir, {**base, "state": "accepted", "order_id": order_id})
        item["open"] = {"order_id": order_id, "quantity": quantity, "price": price, "day": today}
        placed += 1
    body.update(status=outcome, last_placed_at=now, **({"stop_reason": code} if code else {}))
    text = {PENDING: f"[보유 자산] 자동 매도 — 지정가 {placed}건 주문. 체결은 다음 실행에서 확인합니다.",
            STOPPED: f"[보유 자산] 자동 매도 중단 — 주문 {placed}건, 거부 1건({code}). 남은 몫은 다음 달 점검에서 다시 봅니다.",
            UNKNOWN: f"[보유 자산] 자동 매도 확인 필요 — 주문 {placed}건, 접수 여부를 모르는 주문 1건. "
                     "다시 보내지 않습니다. 다음 실행의 체결 내역에서 확인됩니다."}[outcome]
    _notice(body, f"{outcome}|{today}", text)
    body.pop("hold_reason", None)
    _write(state_dir, body)
    return {"status": f"{'ordered' if outcome == PENDING else outcome}:{placed}"}


def notice(state_dir: Path) -> tuple[str, str] | None:
    body = _read(state_dir)
    note = body.get("notice") or {}
    if not note.get("token") or note["token"] == body.get("told_token"):
        return None
    return note["token"], note["text"]


def mark_told(state_dir: Path, token: str, *, now: str) -> None:
    with locked(scenario_path(state_dir).with_suffix(".lock"), code="HOLDINGS_AUTOSELL_LOCKED",
                label="holdings autosell"):
        body = _read(state_dir)
        body.update(told_token=token, told_at=now)
        _write(state_dir, body)


def render_status(state_dir: Path) -> list[str]:
    """Thomas's terminal: the sell scenario and its journal, with amounts. LOCAL_ONLY."""
    body = _read(state_dir)
    lines = ["AUTOSELL - terminal only; do not paste into a chat, a model prompt or a channel"]
    if not body:
        return lines + ["scenario    : none approved"]
    lines += [f"scenario    : {body.get('scenario_id')} {body.get('status')} (approved {body.get('approved_at')}, "
              f"expires {body.get('expires_at')})",
              "items       : " + ", ".join(
                  f"{i['instrument']} {int(i.get('filled') or 0)}/{i['quantity']}"
                  + (f" (open {i['open']['quantity']} @ {i['open']['price']:,})" if i.get("open") else "")
                  for i in body.get("items", [])),
              f"cap         : {body.get('max_krw'):,} KRW; estimate {body.get('estimate_krw', '-')}"]
    for key in ("hold_reason", "stop_reason", "last_placed_at", "completed_at"):
        if key in body:
            lines.append(f"{key:12}: {body[key]}")
    for row in journal(state_dir)[-20:]:
        lines.append(f"  {row['at']} {row['state']:8} SELL {row['instrument']} x{row['quantity']} @ {row['price']:,} "
                     f"~{row['est_krw']:,} KRW {row.get('order_id') or row.get('code') or ''}")
        toss = row.get("toss") or {}
        if toss:
            lines.append(f"      toss: {toss.get('status')} code={toss.get('code') or '-'} body={toss.get('body_kind')} "
                         f"request_id={toss.get('request_id') or '-'} message={toss.get('message') or '-'}")
    return lines
