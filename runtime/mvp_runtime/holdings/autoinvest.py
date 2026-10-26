"""G6-buy: place a buy scenario Thomas approved, once, inside the domestic regular session (Thomas 2026-10-26).

``docs/proposals/G6_BUY_CASH_AUTOINVEST_V0.1.md`` holds the decision and every line below; this is its code.

- :func:`approve` is the terminal command's half (``holdings_board --autoinvest-approve``): it checks the
  scenario and writes it as ``pending``. Only domestic instruments in the current candidates, whole shares, a
  KRW cap no higher than the daily limit.
- :func:`run` is the holdings fire's half. Once per KST trading day, inside the regular session, it checks
  everything, then places the scenario's orders one by one through the buy adapter (``toss_orders``). Before
  each POST it writes an ``intent`` line to the journal and after it the outcome. It stops at the first
  rejection or unknown outcome and never resends.
- :func:`notice` gives the Telegram line: counts and state, never an instrument or an amount (H3).

Both files name instruments and amounts, so they are LOCAL_ONLY (the hook's file regex): the scenario
``holdings_autoinvest.json`` and the append-only journal ``holdings_autoinvest_journal.jsonl``.
"""

from __future__ import annotations

import json
import os
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
FALLBACK_FEE_RATE = 0.001          # when the account's KR commission cannot be read
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

def parse_spec(spec: str) -> list[dict[str, Any]]:
    """``"069500=1,114260=1"`` (or ``toss:domestic:069500=1``) into items; refuses anything else."""
    items = []
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        ident, sep, qty = part.partition("=")
        ident = ident.strip()
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
    typo cannot buy an arbitrary stock. A new approval replaces the last one; its own ids are new."""
    items = parse_spec(spec)
    allowed = {i for ids in candidates.values() for i in ids}
    outside = [i["instrument"] for i in items if i["instrument"] not in allowed]
    if outside:
        raise ToolError(AUTOINVEST_REFUSED, f"{len(outside)} instrument(s) are not current candidates")
    if not isinstance(max_krw, int) or not 0 < max_krw <= DAILY_CAP_KRW:
        raise ToolError(AUTOINVEST_REFUSED, f"--max-krw must be 1..{DAILY_CAP_KRW:,}")
    stamp = now or timeutil.utc_now_iso()
    scenario_id = integrity.short_id("ai", f"{stamp}|{spec}|{max_krw}")
    expires = (timeutil.parse_iso(stamp) + timedelta(days=VALID_DAYS)).isoformat().replace("+00:00", "Z")
    body = {"scenario_id": scenario_id, "items": items, "max_krw": max_krw, "approved_at": stamp,
            "expires_at": expires, "status": PENDING}
    with locked(scenario_path(state_dir).with_suffix(".lock"), code="HOLDINGS_AUTOINVEST_LOCKED",
                label="holdings autoinvest"):
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
    body["notice"] = {"token": f"{body['scenario_id']}|hold|{reason}",
                      "text": f"[보유 자산] 자동 매수 보류 — {_HOLD_TEXT.get(reason, reason)}. 다음 거래일에 다시 봅니다."}
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
}


def run(feed: Any, orders: Any, state_dir: Path, *, now: str) -> dict[str, Any]:
    """One look per fire; at most one attempt per KST trading day. See the module docstring."""
    with locked(scenario_path(state_dir).with_suffix(".lock"), code="HOLDINGS_AUTOINVEST_LOCKED",
                label="holdings autoinvest"):
        body = _read(state_dir)
        if body.get("status") != PENDING:
            return {"status": "none"}
        local = _kst(now)
        today = local.date().isoformat()
        if timeutil.parse_iso(now) >= timeutil.parse_iso(body["expires_at"]):
            body.update(status=EXPIRED, notice={"token": f"{body['scenario_id']}|expired",
                                                "text": "[보유 자산] 자동 매수 시나리오가 실행되지 않고 만료됐습니다."})
            _write(state_dir, body)
            return {"status": EXPIRED}
        if not getattr(orders, "enabled", False):
            return _hold(state_dir, body, "orders_off") if body.get("hold_reason") != "orders_off" else {
                "status": "hold:orders_off"}
        if body.get("last_attempt_day") == today:
            return {"status": "done_today"}
        try:
            session = _session(feed.kr_market_day(today), today)
        except ToolError:
            return _hold(state_dir, body, "read_failed")    # told once; the next fire looks again
        if session is None or not (session[0] + OPEN_AFTER <= local <= session[1] - CLOSE_BEFORE):
            return {"status": "outside_session"}
        body["last_attempt_day"] = today
        try:
            checked = _checks(feed, state_dir, body, today)
        except ToolError:
            return _hold(state_dir, body, "read_failed")    # a read that failed holds the day, never half-orders
        if "status" in checked:
            return checked
        return _place(orders, state_dir, body, today, now, checked["estimates"])


def _checks(feed: Any, state_dir: Path, body: dict[str, Any], today: str) -> dict[str, Any]:
    """Every read and check before the first POST: ACTIVE ETFs, prices, the three caps, KRW cash. Returns a
    hold or stop result (``status``) or the per-item estimates."""
    items = body["items"]
    symbols = [i["instrument"].split(":")[2] for i in items]
    info = {str(r.get("symbol")): r for r in feed.stock_info(symbols)}
    bad = [s for s in symbols if (info.get(s) or {}).get("status") != "ACTIVE"
           or (info.get(s) or {}).get("securityType") not in ("ETF", "FOREIGN_ETF")]
    if bad:
        body.update(status=STOPPED, stop_reason="not_an_active_etf",
                    notice={"token": f"{body['scenario_id']}|stopped|not_an_active_etf",
                            "text": f"[보유 자산] 자동 매수 중단 — 거래 가능한 ETF가 아닌 종목 {len(bad)}개. 주문 0건."})
        _write(state_dir, body)
        return {"status": "stopped:not_an_active_etf"}
    prices = feed.last_prices(symbols)
    if any(s not in prices for s in symbols):
        return _hold(state_dir, body, "price_unreadable")
    fee = FALLBACK_FEE_RATE
    for row in feed.commission_rates():
        if row.get("marketCountry") == "KR":
            try:
                fee = float(row["commissionRate"])
            except (KeyError, TypeError, ValueError):
                pass
    estimates = {i["instrument"]: i["quantity"] * prices[i["instrument"].split(":")[2]] * (1 + fee)
                 for i in items}
    total = sum(estimates.values())
    spent = sum(float(r.get("est_krw") or 0) for r in journal(state_dir)
                if r.get("day") == today and r.get("state") == "intent")
    body["estimate_krw"] = round(total)
    if total > body["max_krw"]:
        return _hold(state_dir, body, "over_scenario_cap")
    if spent + total > DAILY_CAP_KRW:
        return _hold(state_dir, body, "over_daily_cap")
    power = feed.cash_krw()
    if power is None:
        return _hold(state_dir, body, "buying_power_unreadable")
    if total > power:
        body["shortfall_krw"] = round(total - power)
        return _hold(state_dir, body, "insufficient_buying_power")
    return {"estimates": estimates}


def _place(orders: Any, state_dir: Path, body: dict[str, Any], today: str, now: str,
           estimates: Mapping[str, float]) -> dict[str, Any]:
    """The scenario's orders in turn, journalled before and after; stops at the first rejection or unknown."""
    items = body["items"]

    placed, outcome, code = 0, EXECUTED, None
    sent = {r.get("client_order_id") for r in journal(state_dir)}
    for item in items:
        cid = integrity.short_id("ord", f"{body['scenario_id']}|{item['instrument']}")
        if cid in sent:                         # an earlier attempt reached this order: never resend
            outcome, code = UNKNOWN, "journaled"
            break
        base = {"at": now, "day": today, "scenario_id": body["scenario_id"], "client_order_id": cid,
                "instrument": item["instrument"], "quantity": item["quantity"],
                "est_krw": round(estimates[item["instrument"]])}
        _append(state_dir, {**base, "state": "intent"})
        try:
            order_id = orders.buy_kr(item["instrument"].split(":")[2], item["quantity"], client_order_id=cid)
        except OrderRejected as exc:
            _append(state_dir, {**base, "state": "rejected", "code": exc.toss_code or str(exc.status)})
            outcome, code = STOPPED, exc.toss_code or str(exc.status)
            break
        except OrderUnknown as exc:
            _append(state_dir, {**base, "state": "unknown", "code": exc.why})
            outcome, code = UNKNOWN, "unknown"
            break
        except ToolError as exc:
            _append(state_dir, {**base, "state": "rejected", "code": exc.reason_code})
            outcome, code = STOPPED, exc.reason_code
            break
        _append(state_dir, {**base, "state": "accepted", "order_id": order_id})
        placed += 1
    body.update(status=outcome, executed_at=now, placed=placed, **({"stop_reason": code} if code else {}))
    text = {EXECUTED: f"[보유 자산] 자동 매수 — 승인한 시나리오 {placed}건 접수. 체결은 다음 실행의 체결 내역에서 확인됩니다.",
            STOPPED: f"[보유 자산] 자동 매수 중단 — 접수 {placed}건, 거부 1건({code}). 남은 몫은 새 시나리오로 승인해야 합니다.",
            UNKNOWN: f"[보유 자산] 자동 매수 확인 필요 — 접수 {placed}건, 접수 여부를 모르는 주문 1건. "
                     "다시 보내지 않습니다. 다음 실행의 체결 내역에서 확인됩니다."}[outcome]
    body["notice"] = {"token": f"{body['scenario_id']}|{outcome}", "text": text}
    body.pop("hold_reason", None)
    _write(state_dir, body)
    return {"status": f"{outcome}:{placed}"}


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


def render_status(state_dir: Path) -> list[str]:
    """Thomas's terminal: the scenario and today's journal, with amounts. LOCAL_ONLY."""
    body = _read(state_dir)
    lines = ["AUTOINVEST - terminal only; do not paste into a chat, a model prompt or a channel"]
    if not body:
        return lines + ["scenario    : none approved"]
    lines += [f"scenario    : {body.get('scenario_id')} {body.get('status')} (approved {body.get('approved_at')}, "
              f"expires {body.get('expires_at')})",
              "items       : " + ", ".join(f"{i['instrument']} x{i['quantity']}" for i in body.get("items", [])),
              f"cap         : {body.get('max_krw'):,} KRW; estimate {body.get('estimate_krw', '-')}"]
    for key in ("hold_reason", "shortfall_krw", "stop_reason", "placed", "executed_at"):
        if key in body:
            lines.append(f"{key:12}: {body[key]}")
    for row in journal(state_dir)[-20:]:
        lines.append(f"  {row['at']} {row['state']:8} {row['instrument']} x{row['quantity']} "
                     f"~{row['est_krw']:,} KRW {row.get('order_id') or row.get('code') or ''}")
    return lines
