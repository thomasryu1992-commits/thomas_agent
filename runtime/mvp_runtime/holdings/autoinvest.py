"""G6-buy: place a buy scenario Thomas approved, once, inside the domestic regular session (Thomas 2026-10-26).

``docs/proposals/G6_BUY_CASH_AUTOINVEST_V0.1.md`` holds the decision and every line below; this is its code.

- :func:`approve` is the terminal command's half (``holdings_board --autoinvest-approve``): it checks the
  scenario and writes it as ``pending``. Only instruments in the current candidates, whole domestic shares or
  US dollar amounts, a KRW cap no higher than the daily limit.
- :func:`run` is the holdings fire's half.
  - Every fire it reads back each open order and books what filled. An order the exchange cancelled at the
    close leaves its remainder for the next trading day.
  - At most once per KST trading day, inside the regular session, it places the remaining quantity of each item
    with no open order. These are limit orders (Thomas 2026-10-27): the price is the lowest ask at which the book
    covers the quantity, held for the day if more than 2% above the last price.
  - Before each POST it writes an ``intent`` line to the journal and after it the outcome. It stops at the
    first rejection or unknown outcome and never resends.
  - Market orders were dropped because a market buy reserves cash at the upper price limit: two orders of
    scenario B locked the cash the third needed.
  - A US item (§2g, Thomas 2026-10-27) is a dollar amount of a US ETF, from the USD cash Thomas exchanged in the
    app; the API never converts. It is placed once per US regular session (until seventy minutes before the
    close) as a market order by amount. It is done once anything of it filled; one closed with nothing filled
    is placed again in the next session.
- :func:`notice` gives the Telegram line: counts and state, never an instrument or an amount (H3).

Both files name instruments and amounts, so they are LOCAL_ONLY (the hook's file regex): the scenario
``holdings_autoinvest.json`` and the append-only journal ``holdings_autoinvest_journal.jsonl``.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from runtime.read_only_kernel import integrity

from .. import timeutil
from ..errors import ToolError
from ..filelock import locked
from .toss_orders import OrderRejected, OrderUnknown

SCENARIO_FILENAME = "holdings_autoinvest.json"
JOURNAL_FILENAME = "holdings_autoinvest_journal.jsonl"
RECORD_TYPE = "holdings_autoinvest.v1"
AUTOINVEST_REFUSED = "HOLDINGS_AUTOINVEST_REFUSED"

DAILY_CAP_KRW = 500_000            # G6-buy: 50만 원 a KST day, every scenario together
MAX_ITEMS = 10
VALID_DAYS = 7
OPEN_AFTER = timedelta(minutes=5)
CLOSE_BEFORE = timedelta(minutes=30)
# G6 §2g: Toss takes an amount order until an hour before the US close; ten more minutes of margin.
US_CLOSE_BEFORE = timedelta(minutes=70)
FALLBACK_FEE_RATE = 0.001          # when the account's KR commission cannot be read
# Limit orders (Thomas 2026-10-27): the book's price may sit at most this far above the last price, or the day holds.
PRICE_GUARD = 0.02
# A placed order ends in one of these; anything else (pending, partial, a status Toss adds later) is still open.
DONE_STATUSES = frozenset({"FILLED", "CANCELED", "REJECTED", "REPLACED"})
KST = timezone(timedelta(hours=9))

PENDING, EXECUTED, STOPPED, UNKNOWN, EXPIRED = "pending", "executed", "stopped", "unknown", "expired"


def scenario_path(state_dir: Path) -> Path:
    return state_dir / SCENARIO_FILENAME


def journal_path(state_dir: Path) -> Path:
    return state_dir / JOURNAL_FILENAME


def _read(state_dir: Path) -> dict[str, Any]:
    target = scenario_path(state_dir)
    if not target.exists():
        return {}
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ToolError(AUTOINVEST_REFUSED, f"{SCENARIO_FILENAME} does not parse") from None
    return body if isinstance(body, dict) else {}


def _write(state_dir: Path, body: Mapping[str, Any]) -> None:
    target = scenario_path(state_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps({**body, "record_type": RECORD_TYPE}, ensure_ascii=False, sort_keys=True, indent=1),
                   encoding="utf-8")
    tmp.replace(target)


def journal(state_dir: Path) -> list[dict[str, Any]]:
    target = journal_path(state_dir)
    if not target.exists():
        return []
    rows = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _append(state_dir: Path, row: Mapping[str, Any]) -> None:
    target = journal_path(state_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _kst(now: str) -> datetime:
    return timeutil.parse_iso(now).astimezone(KST)


# --- approval (Thomas's terminal) -------------------------------------------------------------------------------

US_AMOUNT = re.compile(r"^\$(\d+(\.\d{1,2})?)$")


def parse_spec(spec: str) -> list[dict[str, Any]]:
    """``"069500=1,114260=1"`` (or ``toss:domestic:069500=1``) into items, and (G6 §2g) a US dollar amount as
    ``toss:overseas:VT=$123.45``; refuses anything else."""
    items = []
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        ident, sep, qty = part.partition("=")
        ident = ident.strip()
        dollars = US_AMOUNT.match(qty.strip())
        if dollars:
            symbol = ident.split(":")[-1]
            if (not sep or not ident.startswith("toss:overseas:") or not re.match(r"^[A-Z][A-Z.\-]{0,9}$", symbol)
                    or float(dollars.group(1)) <= 0):
                raise ToolError(AUTOINVEST_REFUSED, "expected toss:overseas:TICKER=$DOLLARS for a US amount")
            items.append({"instrument": ident, "amount_usd": f"{float(dollars.group(1)):.2f}"})
            continue
        if not ident.startswith("toss:"):
            ident = f"toss:domestic:{ident}"
        try:
            quantity = int(qty.strip())
        except ValueError:
            quantity = 0
        if not sep or quantity < 1 or not ident.startswith("toss:domestic:") or len(ident.split(":")[2]) != 6:
            raise ToolError(AUTOINVEST_REFUSED, "expected SYMBOL=WHOLE_SHARES[,...], domestic 6-character codes")
        items.append({"instrument": ident, "quantity": quantity})
    if not items or len(items) > MAX_ITEMS or len({i["instrument"] for i in items}) != len(items):
        raise ToolError(AUTOINVEST_REFUSED, f"1..{MAX_ITEMS} distinct instruments")
    return items


def approve(state_dir: Path, spec: str, *, max_krw: int, candidates: Mapping[str, Iterable[str]],
            now: str | None = None) -> dict[str, Any]:
    """Write Thomas's scenario as ``pending``. Every instrument must be one of the current candidates, so a
    typo cannot buy an arbitrary stock. A new approval replaces the last one, unless that one still has an open
    order; its own ids are new."""
    items = parse_spec(spec)
    class_of = {i: asset_class for asset_class, ids in candidates.items() for i in ids}
    outside = [i["instrument"] for i in items if i["instrument"] not in class_of]
    if outside:
        raise ToolError(AUTOINVEST_REFUSED, f"{len(outside)} instrument(s) are not current candidates")
    for item in items:
        item["asset_class"] = class_of[item["instrument"]]     # what it is bought for; see classify_bought
    if not isinstance(max_krw, int) or not 0 < max_krw <= DAILY_CAP_KRW:
        raise ToolError(AUTOINVEST_REFUSED, f"--max-krw must be 1..{DAILY_CAP_KRW:,}")
    stamp = now or timeutil.utc_now_iso()
    scenario_id = integrity.short_id("ai", f"{stamp}|{spec}|{max_krw}")
    expires = (timeutil.parse_iso(stamp) + timedelta(days=VALID_DAYS)).isoformat().replace("+00:00", "Z")
    body = {"scenario_id": scenario_id, "items": items, "max_krw": max_krw, "approved_at": stamp,
            "expires_at": expires, "status": PENDING}
    with locked(scenario_path(state_dir).with_suffix(".lock"), code="HOLDINGS_AUTOINVEST_LOCKED",
                label="holdings autoinvest"):
        # A replaced scenario takes its open orders' tracking with it: an order at the exchange would go unbooked.
        if any(i.get("open") for i in _read(state_dir).get("items") or []):
            raise ToolError(AUTOINVEST_REFUSED, "the current scenario has an open order; approve again once it "
                                                "closes (filled, or cancelled at the close)")
        _write(state_dir, body)
    return body


# --- the fire ---------------------------------------------------------------------------------------------------

def _session(day: Mapping[str, Any] | None, today: str) -> tuple[datetime, datetime] | None:
    regular = ((day or {}).get("integrated") or {}).get("regularMarket") if isinstance(day, Mapping) else None
    if not isinstance(regular, Mapping):
        return None
    bounds = []
    for key in ("startTime", "endTime"):
        raw = str(regular.get(key) or "")
        try:
            at = (datetime.fromisoformat(raw) if "T" in raw
                  else datetime.fromisoformat(f"{today}T{raw}").replace(tzinfo=KST))
        except ValueError:
            return None
        bounds.append(at if at.tzinfo else at.replace(tzinfo=KST))
    return bounds[0], bounds[1]


def _hold(state_dir: Path, body: dict[str, Any], reason: str) -> dict[str, Any]:
    body["hold_reason"] = reason
    _notice(body, f"hold|{reason}", f"[보유 자산] 자동 매수 보류 — {_HOLD_TEXT.get(reason, reason)}. 다음 거래일에 다시 봅니다.")
    _write(state_dir, body)
    return {"status": f"hold:{reason}"}


_HOLD_TEXT = {
    "orders_off": "주문 경로가 꺼져 있습니다(MVP_TOSS_ORDERS)",
    "over_scenario_cap": "추정 금액이 승인한 상한을 넘습니다",
    "over_daily_cap": "오늘 자동 매수 한도(50만 원)를 넘습니다",
    "insufficient_buying_power": "주문 가능 금액이 부족합니다",
    "buying_power_unreadable": "주문 가능 금액을 읽지 못했습니다",
    "price_unreadable": "현재가를 읽지 못한 종목이 있습니다",
    "read_failed": "토스 조회에 실패했습니다",
    "book_too_thin": "매도 호가 잔량이 주문 수량보다 적습니다",
    "price_guard": f"호가 가격이 현재가보다 {round(PRICE_GUARD * 100)}% 넘게 높습니다",
    "rate_unreadable": "원·달러 환율을 읽지 못했습니다",
    "insufficient_usd": "달러 주문 가능 금액이 부족합니다(앱에서 환전 필요)",
}


def _notice(body: dict[str, Any], event: str, text: str) -> None:
    body["notice"] = {"token": f"{body['scenario_id']}|{event}", "text": text}


def _is_us(item: Mapping[str, Any]) -> bool:
    return "amount_usd" in item


def _remaining(item: Mapping[str, Any]) -> int:
    """Shares still to buy; a US amount item counts as one until it is done."""
    if _is_us(item):
        return 0 if item.get("done") else 1
    return int(item["quantity"]) - int(item.get("filled") or 0)


def _track(feed: Any, body: dict[str, Any], now: str) -> None:
    """Read back every open order (each fire, in or out of session). FILLED closes it; CANCELED, REJECTED or
    REPLACED closes it with what filled, and the rest waits for the next trading day; anything else (pending,
    partial, a status Toss adds later) keeps it open, so no second order is placed beside it.

    A US amount order (§2g) is done once anything of it filled; one closed with nothing filled is placed again in
    the next US regular session."""
    closed_short = us_unfilled = 0
    for item in body["items"]:
        held = item.get("open")
        if not held:
            continue
        try:
            state = feed.fill_state(held["order_id"])
        except ToolError:
            continue                                   # unknown this fire: the order stays open, nothing new
        status = state.get("status")
        if _is_us(item):
            got = float(state.get("filled") or 0)
            if status == "FILLED" or (status in DONE_STATUSES and got > 0):
                item.update(done=True, filled_shares=got, spent_usd=float(state.get("filled_amount") or 0),
                            spent_krw=float(held.get("est_krw") or 0))
                item.pop("open", None)
            elif status in DONE_STATUSES:
                item.pop("open", None)
                us_unfilled += 1
            continue
        filled = int(min(float(state.get("filled") or 0), held["quantity"]))
        if status == "FILLED" or (status in DONE_STATUSES and filled >= held["quantity"]):
            filled = held["quantity"]
        elif status not in DONE_STATUSES:
            held["filled_so_far"] = filled
            continue
        item["filled"] = int(item.get("filled") or 0) + filled
        item["spent_krw"] = round(float(item.get("spent_krw") or 0) + filled * held["price"])
        item.pop("open", None)
        closed_short += held["quantity"] - filled
    left = sum(_remaining(i) for i in body["items"])
    if us_unfilled and left:
        _notice(body, f"us_short|{now}", f"[보유 자산] 자동 매수 — 미국 금액 주문 {us_unfilled}건이 체결되지 않고 "
                                          "닫혔습니다. 다음 미국 정규장에 다시 주문합니다.")
    if left == 0:
        body.update(status=EXECUTED, completed_at=now)
        _notice(body, "filled", f"[보유 자산] 자동 매수 — 승인한 시나리오 {len(body['items'])}종목 전량 체결.")
    elif closed_short:
        _notice(body, f"short|{now}", f"[보유 자산] 자동 매수 — 미체결 {closed_short}주는 장 마감에 취소됐습니다. "
                                       "다음 거래일에 남은 수량만 다시 주문합니다.")


def _committed_krw(body: Mapping[str, Any]) -> float:
    """What the scenario has spent or holds open, in KRW: a US order at its estimate when placed."""
    total = sum(float(i.get("spent_krw") or 0) for i in body["items"])
    for item in body["items"]:
        held = item.get("open")
        if held:
            total += float(held.get("est_krw") or 0) if _is_us(item) else held["quantity"] * held["price"]
    return total


def _limit_price(levels: list[tuple[float, float]], quantity: int) -> float | None:
    """The lowest ask at which the book's cumulative volume covers ``quantity``; None when it never does."""
    total = 0.0
    for price, volume in levels:
        total += volume
        if total >= quantity:
            return price
    return None


def run(feed: Any, orders: Any, state_dir: Path, *, now: str) -> dict[str, Any]:
    """One look per fire: open orders are read back every fire; new orders at most once per KST trading day,
    inside the session. See the module docstring."""
    with locked(scenario_path(state_dir).with_suffix(".lock"), code="HOLDINGS_AUTOINVEST_LOCKED",
                label="holdings autoinvest"):
        body = _read(state_dir)
        if body.get("status") != PENDING:
            return {"status": "none"}
        local = _kst(now)
        today = local.date().isoformat()
        _track(feed, body, now)
        if body["status"] == EXECUTED:
            _write(state_dir, body)
            return {"status": EXECUTED}
        if timeutil.parse_iso(now) >= timeutil.parse_iso(body["expires_at"]):
            got = sum(int(i.get("filled") or 0) for i in body["items"])
            body.update(status=EXPIRED)
            _notice(body, "expired", f"[보유 자산] 자동 매수 시나리오 만료 — 체결 {got}주, 남은 "
                                     f"{sum(_remaining(i) for i in body['items'])}주는 새 시나리오로 승인해야 합니다.")
            _write(state_dir, body)
            return {"status": EXPIRED}
        if not getattr(orders, "enabled", False):
            if body.get("hold_reason") == "orders_off":
                _write(state_dir, body)
                return {"status": "hold:orders_off"}
            return _hold(state_dir, body, "orders_off")
        wanted = [i for i in body["items"] if _remaining(i) > 0 and not i.get("open") and not _is_us(i)]
        wanted_us = [i for i in body["items"] if _remaining(i) > 0 and not i.get("open") and _is_us(i)]
        if not wanted and not wanted_us:
            _write(state_dir, body)
            return {"status": "waiting"}
        if wanted and body.get("last_attempt_day") != today:
            try:
                session = _session(feed.kr_market_day(today), today)
            except ToolError:
                return _hold(state_dir, body, "read_failed")    # told once; the next fire looks again
            if session is not None and session[0] + OPEN_AFTER <= local <= session[1] - CLOSE_BEFORE:
                body["last_attempt_day"] = today
                try:
                    checked = _checks(feed, state_dir, body, today, wanted)
                except ToolError:
                    return _hold(state_dir, body, "read_failed")  # a read that failed holds the day, never half-orders
                if "status" in checked:
                    return checked
                return _place(orders, state_dir, body, today, now, wanted, checked["prices"], checked["estimates"])
        if wanted_us:
            try:
                session_day = _us_session(feed, now)
            except ToolError:
                return _hold(state_dir, body, "read_failed")
            if session_day is not None and body.get("last_us_attempt") != session_day:
                body["last_us_attempt"] = session_day
                try:
                    checked = _checks_us(feed, state_dir, body, today, wanted_us)
                except ToolError:
                    return _hold(state_dir, body, "read_failed")
                if "status" in checked:
                    return checked
                return _place_us(orders, state_dir, body, today, now, session_day, wanted_us, checked["estimates"])
        _write(state_dir, body)
        return {"status": "done_today" if wanted and not wanted_us and body.get("last_attempt_day") == today
                else "outside_session"}


def _checks(feed: Any, state_dir: Path, body: dict[str, Any], today: str,
            wanted: list[dict[str, Any]]) -> dict[str, Any]:
    """Every read and check before the first POST: ACTIVE ETFs, the book's price for each remaining quantity
    and its guard, the three caps, KRW cash. Returns a hold or stop result (``status``) or prices and estimates."""
    symbols = [i["instrument"].split(":")[2] for i in wanted]
    info = {str(r.get("symbol")): r for r in feed.stock_info(symbols)}
    bad = [s for s in symbols if (info.get(s) or {}).get("status") != "ACTIVE"
           or (info.get(s) or {}).get("securityType") not in ("ETF", "FOREIGN_ETF")]
    if bad:
        body.update(status=STOPPED, stop_reason="not_an_active_etf")
        _notice(body, "stopped|not_an_active_etf",
                f"[보유 자산] 자동 매수 중단 — 거래 가능한 ETF가 아닌 종목 {len(bad)}개. 주문 0건.")
        _write(state_dir, body)
        return {"status": "stopped:not_an_active_etf"}
    last = feed.last_prices(symbols)
    if any(s not in last for s in symbols):
        return _hold(state_dir, body, "price_unreadable")
    prices: dict[str, int] = {}
    for item, symbol in zip(wanted, symbols):
        price = _limit_price(feed.asks(symbol), _remaining(item))
        if price is None:
            return _hold(state_dir, body, "book_too_thin")
        if price > last[symbol] * (1 + PRICE_GUARD):
            return _hold(state_dir, body, "price_guard")
        prices[item["instrument"]] = int(round(price))
    fee = FALLBACK_FEE_RATE
    for row in feed.commission_rates():
        if row.get("marketCountry") == "KR":
            try:
                fee = float(row["commissionRate"])
            except (KeyError, TypeError, ValueError):
                pass
    estimates = {i["instrument"]: _remaining(i) * prices[i["instrument"]] * (1 + fee) for i in wanted}
    total = sum(estimates.values())
    spent_before = _committed_krw(body)
    spent_today = sum(float(r.get("est_krw") or 0) for r in journal(state_dir)
                      if r.get("day") == today and r.get("state") == "intent")
    body["estimate_krw"] = round(total)
    if spent_before + total > body["max_krw"]:
        return _hold(state_dir, body, "over_scenario_cap")
    if spent_today + total > DAILY_CAP_KRW:
        return _hold(state_dir, body, "over_daily_cap")
    power = feed.cash_krw()
    if power is None:
        return _hold(state_dir, body, "buying_power_unreadable")
    if total > power:
        body["shortfall_krw"] = round(total - power)
        return _hold(state_dir, body, "insufficient_buying_power")
    return {"prices": prices, "estimates": estimates}


def _place(orders: Any, state_dir: Path, body: dict[str, Any], today: str, now: str, wanted: list[dict[str, Any]],
           prices: Mapping[str, int], estimates: Mapping[str, float]) -> dict[str, Any]:
    """Today's limit orders in turn, journalled before and after; stops at the first rejection or unknown."""
    placed, outcome, code = 0, PENDING, None
    sent = {r.get("client_order_id") for r in journal(state_dir)}
    for item in wanted:
        cid = integrity.short_id("ord", f"{body['scenario_id']}|{item['instrument']}|{today}")
        if cid in sent:                         # an earlier attempt reached this order: never resend
            outcome, code = UNKNOWN, "journaled"
            break
        quantity, price = _remaining(item), prices[item["instrument"]]
        base = {"at": now, "day": today, "scenario_id": body["scenario_id"], "client_order_id": cid,
                "instrument": item["instrument"], "asset_class": item.get("asset_class"), "quantity": quantity,
                "price": price,
                "est_krw": round(estimates[item["instrument"]])}
        order_id, outcome, code = _send(state_dir, base, lambda: orders.buy_kr(
            item["instrument"].split(":")[2], quantity, price=price, client_order_id=cid))
        if order_id is None:
            break
        item["open"] = {"order_id": order_id, "quantity": quantity, "price": price, "day": today}
        placed += 1
    return _placed(state_dir, body, today, now, placed, outcome, code, "지정가")


def _send(state_dir: Path, base: Mapping[str, Any], post: Any) -> tuple[str | None, str, str | None]:
    """One POST between its journal lines: (order id, PENDING, None) when accepted, else (None, outcome, code)."""
    _append(state_dir, {**base, "state": "intent"})
    try:
        order_id = post()
    except OrderRejected as exc:
        # What Toss said, whole (code, message, request id, body shape): the first live 422 kept only its status.
        _append(state_dir, {**base, "state": "rejected", "code": exc.toss_code or str(exc.status), "toss": exc.detail})
        return None, STOPPED, exc.toss_code or str(exc.status)
    except OrderUnknown as exc:
        _append(state_dir, {**base, "state": "unknown", "code": exc.why, "toss": exc.detail})
        return None, UNKNOWN, "unknown"
    except ToolError as exc:
        _append(state_dir, {**base, "state": "rejected", "code": exc.reason_code})
        return None, STOPPED, exc.reason_code
    _append(state_dir, {**base, "state": "accepted", "order_id": order_id})
    return order_id, PENDING, None


def _placed(state_dir: Path, body: dict[str, Any], today: str, now: str, placed: int, outcome: str, code: str | None,
            kind: str) -> dict[str, Any]:
    body.update(status=outcome, last_placed_at=now, **({"stop_reason": code} if code else {}))
    text = {PENDING: f"[보유 자산] 자동 매수 — {kind} {placed}건 주문. 체결은 다음 실행에서 확인합니다.",
            STOPPED: f"[보유 자산] 자동 매수 중단 — 주문 {placed}건, 거부 1건({code}). 남은 몫은 새 시나리오로 승인해야 합니다.",
            UNKNOWN: f"[보유 자산] 자동 매수 확인 필요 — 주문 {placed}건, 접수 여부를 모르는 주문 1건. "
                     "다시 보내지 않습니다. 다음 실행의 체결 내역에서 확인됩니다."}[outcome]
    _notice(body, f"{outcome}|{today}|{kind}", text)
    body.pop("hold_reason", None)
    _write(state_dir, body)
    return {"status": f"{'ordered' if outcome == PENDING else outcome}:{placed}"}


# --- G6 §2g: the US amount orders (Thomas 2026-10-27) ------------------------------------------------------------

def _us_session(feed: Any, now: str) -> str | None:
    """The US date of the regular session ``now`` is inside, from five minutes after its open to seventy minutes
    before its close (Toss takes an amount order until an hour before), or None."""
    at = timeutil.parse_iso(now)
    us_day = (at - timedelta(hours=5)).date().isoformat()          # New York's date, near enough to pick the week
    for day, start, end in feed.us_regular_sessions(us_day):
        try:
            opened, closed = datetime.fromisoformat(start), datetime.fromisoformat(end)
        except ValueError:
            continue
        if opened + OPEN_AFTER <= at <= closed - US_CLOSE_BEFORE:
            return day
    return None


def _us_fee(feed: Any) -> float:
    for row in feed.commission_rates():
        if row.get("marketCountry") == "US":
            try:
                return float(row["commissionRate"])
            except (KeyError, TypeError, ValueError):
                pass
    return FALLBACK_FEE_RATE


def _checks_us(feed: Any, state_dir: Path, body: dict[str, Any], today: str,
               wanted: list[dict[str, Any]]) -> dict[str, Any]:
    """Before the first US POST: ACTIVE ETFs, the rate, the three caps in KRW, USD cash. No price guard: an amount
    order fixes the dollars, not the shares."""
    symbols = [i["instrument"].split(":")[2] for i in wanted]
    info = {str(r.get("symbol")): r for r in feed.stock_info(symbols)}
    bad = [s for s in symbols if (info.get(s) or {}).get("status") != "ACTIVE"
           or (info.get(s) or {}).get("securityType") not in ("ETF", "FOREIGN_ETF")]
    if bad:
        body.update(status=STOPPED, stop_reason="not_an_active_etf")
        _notice(body, "stopped|not_an_active_etf",
                f"[보유 자산] 자동 매수 중단 — 거래 가능한 ETF가 아닌 종목 {len(bad)}개. 주문 0건.")
        _write(state_dir, body)
        return {"status": "stopped:not_an_active_etf"}
    rate = feed.usd_krw_rate()
    if not rate:
        return _hold(state_dir, body, "rate_unreadable")
    fee = _us_fee(feed)
    dollars = {i["instrument"]: float(i["amount_usd"]) * (1 + fee) for i in wanted}
    estimates = {k: v * rate for k, v in dollars.items()}
    total = sum(estimates.values())
    spent_before = _committed_krw(body)
    spent_today = sum(float(r.get("est_krw") or 0) for r in journal(state_dir)
                      if r.get("day") == today and r.get("state") == "intent")
    body["estimate_krw"] = round(total)
    if spent_before + total > body["max_krw"]:
        return _hold(state_dir, body, "over_scenario_cap")
    if spent_today + total > DAILY_CAP_KRW:
        return _hold(state_dir, body, "over_daily_cap")
    power = feed.cash_usd()
    if power is None:
        return _hold(state_dir, body, "buying_power_unreadable")
    if sum(dollars.values()) > power:
        body["shortfall_usd"] = round(sum(dollars.values()) - power, 2)
        return _hold(state_dir, body, "insufficient_usd")
    return {"estimates": estimates}


def _place_us(orders: Any, state_dir: Path, body: dict[str, Any], today: str, now: str, session_day: str,
              wanted: list[dict[str, Any]], estimates: Mapping[str, float]) -> dict[str, Any]:
    """This US session's amount orders in turn, journalled before and after, as the domestic ones."""
    placed, outcome, code = 0, PENDING, None
    sent = {r.get("client_order_id") for r in journal(state_dir)}
    for item in wanted:
        cid = integrity.short_id("ord", f"{body['scenario_id']}|{item['instrument']}|us:{session_day}")
        if cid in sent:
            outcome, code = UNKNOWN, "journaled"
            break
        base = {"at": now, "day": today, "us_session": session_day, "scenario_id": body["scenario_id"],
                "client_order_id": cid, "instrument": item["instrument"], "asset_class": item.get("asset_class"),
                "quantity": None, "amount_usd": item["amount_usd"], "est_krw": round(estimates[item["instrument"]])}
        order_id, outcome, code = _send(state_dir, base, lambda: orders.buy_us_amount(
            item["instrument"].split(":")[2], item["amount_usd"], client_order_id=cid))
        if order_id is None:
            break
        item["open"] = {"order_id": order_id, "amount_usd": item["amount_usd"], "day": session_day,
                        "est_krw": round(estimates[item["instrument"]])}
        placed += 1
    return _placed(state_dir, body, today, now, placed, outcome, code, "미국 금액")


def classify_bought(state_dir: Path, *, candidates: Mapping[str, Iterable[str]], us_bonds: Iterable[str],
                    now: str) -> list[str]:
    """G6 §2f (Thomas 2026-10-27): enter each instrument this path bought (an ``accepted`` journal row) that no
    entry places yet, in the class it was bought for. A row from before the class was journalled takes its class
    from the current candidates. A bond names its currency (the US Treasury pick is USD, any other bond KRW),
    because the bond class has none. It never changes an entry that exists, Thomas's or its own; an instrument
    whose class cannot be told is left for Thomas. Returns the instruments it entered."""
    from . import allocation, classification

    bought = {}
    for row in journal(state_dir):
        if row.get("state") == "accepted" and row.get("instrument"):
            bought[str(row["instrument"])] = row.get("asset_class") or bought.get(str(row["instrument"]))
    if not bought:
        return []
    current = classification.load(state_dir)        # unreadable: raises, and nothing is written
    class_of = {i: asset_class for asset_class, ids in candidates.items() for i in ids}
    us = set(us_bonds)
    assign: dict[str, str] = {}
    currency: dict[str, str] = {}
    for instrument, recorded in sorted(bought.items()):
        if instrument in current.entries or allocation.CLASSIFICATION.get(instrument.split(":")[-1]):
            continue
        asset_class = recorded or class_of.get(instrument)
        if asset_class not in classification.ASSIGNABLE_CLASSES:
            continue
        assign[instrument] = asset_class
        if asset_class == allocation.BONDS:
            currency[instrument] = "usd" if instrument in us else "krw"
    if assign:
        classification.apply(state_dir, assign=assign, currency=currency, now=now, source="g6_buy")
    return sorted(assign)


def notice(state_dir: Path) -> tuple[str, str] | None:
    body = _read(state_dir)
    note = body.get("notice") or {}
    if not note.get("token") or note["token"] == body.get("told_token"):
        return None
    return note["token"], note["text"]


def mark_told(state_dir: Path, token: str, *, now: str) -> None:
    with locked(scenario_path(state_dir).with_suffix(".lock"), code="HOLDINGS_AUTOINVEST_LOCKED",
                label="holdings autoinvest"):
        body = _read(state_dir)
        body.update(told_token=token, told_at=now)
        _write(state_dir, body)


def _status_item(item: Mapping[str, Any]) -> str:
    if _is_us(item):
        state = (f"done, {item.get('filled_shares', 0):g} sh for ${item.get('spent_usd', 0):,.2f}" if item.get("done")
                 else "open" if item.get("open") else "to place")
        return f"{item['instrument']} ${item['amount_usd']} ({state})"
    return (f"{item['instrument']} {int(item.get('filled') or 0)}/{item['quantity']}"
            + (f" (open {item['open']['quantity']} @ {item['open']['price']:,})" if item.get("open") else ""))


def render_status(state_dir: Path) -> list[str]:
    """Thomas's terminal: the scenario and today's journal, with amounts. LOCAL_ONLY."""
    body = _read(state_dir)
    lines = ["AUTOINVEST - terminal only; do not paste into a chat, a model prompt or a channel"]
    if not body:
        return lines + ["scenario    : none approved"]
    lines += [f"scenario    : {body.get('scenario_id')} {body.get('status')} (approved {body.get('approved_at')}, "
              f"expires {body.get('expires_at')})",
              "items       : " + ", ".join(_status_item(i) for i in body.get("items", [])),
              f"cap         : {body.get('max_krw'):,} KRW; estimate {body.get('estimate_krw', '-')}"]
    for key in ("hold_reason", "shortfall_krw", "stop_reason", "last_placed_at", "completed_at", "executed_at"):
        if key in body:
            lines.append(f"{key:12}: {body[key]}")
    for row in journal(state_dir)[-20:]:
        what = (f"${row['amount_usd']}" if row.get("amount_usd")
                else f"x{row['quantity']}" + (f" @ {row['price']:,}" if row.get("price") else ""))
        lines.append(f"  {row['at']} {row['state']:8} {row['instrument']} {what} "
                     f"~{row['est_krw']:,} KRW {row.get('order_id') or row.get('code') or ''}")
        toss = row.get("toss") or {}
        if toss:
            lines.append(f"      toss: {toss.get('status')} code={toss.get('code') or '-'} body={toss.get('body_kind')} "
                         f"request_id={toss.get('request_id') or '-'} message={toss.get('message') or '-'}")
            if toss.get("body_head") or toss.get("content_type"):
                lines.append(f"      body: type={toss.get('content_type') or '-'} head={toss.get('body_head') or '-'}")
    return lines
