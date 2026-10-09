"""H6b: the cash-flow ledger — shadow only. Records what moved money; changes no verdict.

``docs/proposals/PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md`` (Thomas 2026-10-08, D-H6-1–10). Each holdings fire
reads the venues' cash-flow histories (H6a) and appends what it has not seen to
``holdings_cash_flows.jsonl``, a :mod:`chained_log`. Nothing here issues or redeems a unit, computes a
NAV per unit or touches the drawdown: that is H6c, after the shadow period (D-H6-9). Every line carries
``accounting_mode = shadow``.

**Exactly once (D-H6-5).** An event's identity is ``source_event_key`` (``<source>:<the venue's id>``).
A key already in the ledger is never written again, so re-reading is safe — and each source is
re-read from :data:`OVERLAP_MS` before its cursor, which catches a flow the venue confirms late. A row
the venue still calls pending is not written; the cursor stays behind it, so it is read again until it
settles. A recorded success that later reads as failed is answered by a ``flow_reversed`` line.

**One movement, two records.** ``economic_event_id`` is the key unless another source recorded the same
asset and amount within :data:`CROSS_SOURCE_WINDOW_MS`; then both are ``possible_duplicate`` and HELD —
never merged by guess.

**Three statuses (D-H6-6).** ``source_confidence`` (CONFIRMED from a venue record; UNRESOLVED for a Toss
residual), ``valuation_status`` (UNVALUED throughout H6b: nothing is priced yet) and
``accounting_status`` (READY, or HELD with a reason). Fiat card/bank payments are HELD: whether one was
paid from outside the declared scope is not yet known (D-H6-7's caution, applied).

**Schema is earned, not assumed.** H6a's first probe saw only empty lists, so each source starts
``UNVERIFIED_NO_ROWS``. The first row that normalizes makes it ``VERIFIED``; a row that does not is
written as ``malformed_source_event`` (HELD) and the source becomes ``MISMATCH``.

**Toss is SHADOW_ONLY (D-H6-7).** Per currency pocket, the change in cash buying power less what closed
orders explain; a residual beyond one minor unit is ``toss_residual`` (UNRESOLVED), a KRW/USD pair that
offsets at the read's rate is ``internal_exchange``. Recorded for study; it affects nothing, because what
``cashBuyingPower`` does around settlement is not yet known.

**Local only.** Lines carry assets and amounts (as strings: the chain refuses floats). No door reads the
ledger or the state file; the stored snapshot gets counts (:func:`summary`).
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import timeutil
from ..errors import MvpRuntimeError, ToolError
from . import chained_log

FILENAME = "holdings_cash_flows.jsonl"
STATE_FILENAME = "holdings_cash_flow_state.json"
RECORD_TYPE = "holdings_cash_flow_event.v1"
LEDGER_TAMPERED = "HOLDINGS_CASH_FLOW_LEDGER_TAMPERED"
MALFORMED_SOURCE_EVENT = "MALFORMED_SOURCE_EVENT"

ACCOUNTING_MODE = "shadow"
ACCOUNTING_METHOD = "hourly_approximation_v1"
START_LOOKBACK_MS = 7 * 86_400_000   # the first read reaches back 7 days (Thomas 2026-10-08)
OVERLAP_MS = 86_400_000              # every later read re-reads the last day
CROSS_SOURCE_WINDOW_MS = 10 * 60_000
READ_BUDGET_SECONDS = 20.0           # the histories share the maintenance pass; the rest wait an hour

CONFIRMED, RECONCILED, UNRESOLVED = "CONFIRMED", "RECONCILED", "UNRESOLVED"
READY, HELD = "READY", "HELD"
UNVALUED = "UNVALUED"
SCHEMA_NO_ROWS, SCHEMA_VERIFIED, SCHEMA_MISMATCH = "UNVERIFIED_NO_ROWS", "VERIFIED", "MISMATCH"

EVENT_FLOW = "flow_recorded"
EVENT_INTERNAL = "internal_transfer"
EVENT_REVERSED = "flow_reversed"
EVENT_MALFORMED = "malformed_source_event"
EVENT_TOSS_RESIDUAL = "toss_residual"
EVENT_TOSS_EXCHANGE = "internal_exchange"


def ledger_path(state_dir: Path) -> Path:
    return state_dir / FILENAME


def state_path(state_dir: Path) -> Path:
    return state_dir / STATE_FILENAME


def verify(state_dir: Path) -> list[dict[str, Any]]:
    return chained_log.verify(ledger_path(state_dir), record_type=RECORD_TYPE, tamper_code=LEDGER_TAMPERED)


def load_state(state_dir: Path) -> dict[str, Any]:
    target = state_path(state_dir)
    if not target.exists():
        return {"sources": {}, "toss": None}
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ToolError(LEDGER_TAMPERED, f"{STATE_FILENAME} does not parse") from None
    return body if isinstance(body, dict) else {"sources": {}, "toss": None}


def _save_state(state_dir: Path, body: Mapping[str, Any]) -> None:
    target = state_path(state_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(target)


# --- normalization: one venue row -> one event, or None while it is pending -------------------------

def _num(value: Any) -> str:
    """A decimal kept as the venue's own string; refuses anything that is not a finite number."""
    text = str(value).strip()
    float(text)
    if text.lower() in ("nan", "inf", "-inf", "infinity", "-infinity"):
        raise ValueError(text)
    return text


def _ms(value: Any) -> int:
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    # Binance's withdraw history writes "YYYY-MM-DD HH:MM:SS" in UTC.
    return int(timeutil.parse_iso(text.replace(" ", "T") + ("" if text.endswith("Z") else "Z")).timestamp() * 1000)


def _event(direction: str, asset: Any, amount: Any, at_ms: int, *, fee: Any = None) -> dict[str, Any]:
    return {"direction": direction, "asset": str(asset).strip(), "amount": _num(amount),
            "fee": None if fee in (None, "") else _num(fee), "event_ms": at_ms}


# Each: row -> (venue id, event or None if not terminal, failed: bool). A KeyError/ValueError/TypeError
# is a schema mismatch. Status vocabularies are Binance's documented ones.
def _crypto_deposit(row: Mapping[str, Any]):
    status = int(row["status"])            # 0 pending, 6 credited, 1 success, 7 wrong, 8 waiting
    event = _event("in", row["coin"], row["amount"], _ms(row["insertTime"])) if status in (1, 6) else None
    return str(row["id"]), event, status == 7


def _crypto_withdraw(row: Mapping[str, Any]):
    status = int(row["status"])            # 6 completed; 1 cancelled, 3 rejected, 5 failure
    event = (_event("out", row["coin"], row["amount"], _ms(row.get("completeTime") or row["applyTime"]),
                    fee=row.get("transactionFee")) if status == 6 else None)
    return str(row["id"]), event, status in (1, 3, 5)


def _fiat_order(direction: str):
    def normalize(row: Mapping[str, Any]):
        status = str(row["status"])
        event = (_event(direction, row["fiatCurrency"], row["indicatedAmount"] if "indicatedAmount" in row
                        else row["amount"], _ms(row.get("updateTime") or row["createTime"]), fee=row.get("totalFee"))
                 if status == "Successful" else None)
        return str(row["orderNo"]), event, status in ("Failed", "Expired")
    return normalize


def _fiat_payment(direction: str):
    def normalize(row: Mapping[str, Any]):
        status = str(row["status"])
        asset, amount = ((row["cryptoCurrency"], row["obtainAmount"]) if direction == "in"
                         else (row["cryptoCurrency"], row["sourceAmount"]))
        event = _event(direction, asset, amount, _ms(row.get("updateTime") or row["createTime"]),
                       fee=row.get("totalFee")) if status == "Completed" else None
        return str(row["orderNo"]), event, status in ("Failed", "Refunded")
    return normalize


def _pay(row: Mapping[str, Any]):
    amount = _num(row["amount"])
    direction = "out" if amount.startswith("-") else "in"
    event = _event(direction, row["currency"], amount.lstrip("-+"), _ms(row["transactionTime"]))
    return str(row["transactionId"]), event, False


def _transfer(row: Mapping[str, Any]):
    status = str(row["status"])
    event = (_event("internal", row["asset"], row["amount"], _ms(row["timestamp"])) if status == "CONFIRMED"
             else None)
    return str(row["tranId"]), event, status == "FAILED"


NORMALIZERS: dict[str, Callable[[Mapping[str, Any]], tuple[str, dict[str, Any] | None, bool]]] = {
    "crypto_deposit": _crypto_deposit,
    "crypto_withdraw": _crypto_withdraw,
    "fiat_deposit": _fiat_order("in"),
    "fiat_withdraw": _fiat_order("out"),
    "fiat_buy": _fiat_payment("in"),
    "fiat_sell": _fiat_payment("out"),
    "pay": _pay,
    "transfer_spot_to_futures": _transfer,
    "transfer_futures_to_spot": _transfer,
}
# Payments may be paid from a fiat balance outside the declared scope: HELD until that is known.
# Pay's sign convention is unverified on a real row: HELD until the first one is read.
HELD_BY_SOURCE = {"fiat_buy": "fiat_payment_scope_unverified", "fiat_sell": "fiat_payment_scope_unverified",
                  "pay": "pay_direction_unverified"}


# --- the Binance pass --------------------------------------------------------------------------------

def collect_binance(feed: Any, state_dir: Path, *, now: str, now_ms: int | None = None,
                    clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Read every history from its cursor (less the overlap), append what is new, move the cursors.
    Returns ``{"written": n, "internal_ms": [...], "errors": {source: code}}``. A source whose read fails
    keeps its cursor and its status says why; the others go on."""
    from .binance_wallet import FLOW_SOURCES

    now_ms = now_ms if now_ms is not None else int(timeutil.parse_iso(now).timestamp() * 1000)
    state = load_state(state_dir)
    sources = state.setdefault("sources", {})
    ledger = verify(state_dir)
    seen = {row.get("source_event_key") for row in ledger}
    recorded = {row.get("source_event_key"): row for row in ledger if row.get("event") in (EVENT_FLOW, EVENT_INTERNAL)}
    started = clock()
    pending_rows: list[dict[str, Any]] = []
    internal_ms: list[int] = []
    errors: dict[str, str] = {}
    for source, (_path, _fixed, _window, days) in FLOW_SOURCES.items():
        info = sources.setdefault(source, {"access": "UNREAD", "schema": SCHEMA_NO_ROWS, "cursor_ms": None})
        if clock() - started > READ_BUDGET_SECONDS:
            errors[source] = "READ_BUDGET"
            continue
        cursor = info.get("cursor_ms") or now_ms - START_LOOKBACK_MS
        start = max(cursor - OVERLAP_MS, now_ms - days * 86_400_000 + 60_000)
        try:
            rows = feed.flow_history(source, start_ms=start, end_ms=now_ms)
        except MvpRuntimeError as exc:
            info["access"] = f"FAIL {exc.reason_code}"
            errors[source] = exc.reason_code
            continue
        info["access"] = "PASS"
        oldest_pending = None
        for row in rows:
            try:
                venue_id, event, failed = NORMALIZERS[source](row)
            except (KeyError, ValueError, TypeError):
                info["schema"] = SCHEMA_MISMATCH
                digest = hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]
                key = f"{source}:malformed:{digest}"
                if key in seen:
                    continue
                seen.add(key)
                pending_rows.append({"event": EVENT_MALFORMED, "source": source, "source_event_key": key,
                                     "accounting_status": HELD, "held_reason": MALFORMED_SOURCE_EVENT})
                continue
            if info["schema"] != SCHEMA_MISMATCH:
                info["schema"] = SCHEMA_VERIFIED
            key = f"{source}:{venue_id}"
            if failed and key in recorded and not any(
                    r.get("reverses") == key for r in ledger + pending_rows):
                pending_rows.append({"event": EVENT_REVERSED, "source": source, "source_event_key": f"{key}:reversal",
                                     "reverses": key, "accounting_status": HELD, "held_reason": "venue_reversed"})
                continue
            if event is None:
                if not failed:
                    t = _row_time(row)
                    oldest_pending = t if oldest_pending is None or (t and t < oldest_pending) else oldest_pending
                continue
            if event["direction"] == "internal":
                internal_ms.append(event["event_ms"])
            if key in seen:
                continue
            seen.add(key)
            internal = event["direction"] == "internal"
            held = HELD_BY_SOURCE.get(source)
            pending_rows.append({
                "event": EVENT_INTERNAL if internal else EVENT_FLOW, "source": source, "source_event_key": key,
                "economic_event_id": key, **event, "source_confidence": CONFIRMED, "valuation_status": UNVALUED,
                "accounting_status": HELD if held else READY, "held_reason": held,
            })
        # The cursor stops behind the oldest row still pending, so it is read again until it settles.
        info["cursor_ms"] = min(now_ms, oldest_pending) if oldest_pending else now_ms
    _mark_cross_source(pending_rows, ledger)
    written = _append(state_dir, pending_rows, now=now)
    state["updated_at"] = now
    _save_state(state_dir, state)
    return {"written": len(written), "internal_ms": sorted(internal_ms), "errors": errors}


def _row_time(row: Mapping[str, Any]) -> int | None:
    for name in ("insertTime", "applyTime", "createTime", "transactionTime", "timestamp"):
        if name in row:
            try:
                return _ms(row[name])
            except (ValueError, TypeError):
                return None
    return None


def _mark_cross_source(new_rows: list[dict[str, Any]], ledger: list[dict[str, Any]]) -> None:
    """Same asset, same amount, same direction, two sources, within the window: both HELD, never merged."""
    flows = [r for r in new_rows if r.get("event") == EVENT_FLOW]
    pool = [r for r in ledger if r.get("event") == EVENT_FLOW] + flows
    for row in flows:
        for other in pool:
            if other is row or other.get("source") == row.get("source"):
                continue
            if (other.get("asset") == row.get("asset") and other.get("amount") == row.get("amount")
                    and other.get("direction") == row.get("direction")
                    and abs(int(other.get("event_ms") or 0) - int(row["event_ms"])) <= CROSS_SOURCE_WINDOW_MS):
                row.update({"accounting_status": HELD, "held_reason": "possible_duplicate",
                            "possible_duplicate_of": other.get("source_event_key")})


def _append(state_dir: Path, rows: list[dict[str, Any]], *, now: str) -> list[dict[str, Any]]:
    if not rows:
        return []
    stamped = [{"at": now, "accounting_mode": ACCOUNTING_MODE, "accounting_method": ACCOUNTING_METHOD, **row}
               for row in rows]
    return chained_log.append(ledger_path(state_dir), record_type=RECORD_TYPE, tamper_code=LEDGER_TAMPERED,
                              lock_code="HOLDINGS_CASH_FLOW_LEDGER_LOCKED", label="holdings cash-flow ledger",
                              build=lambda _previous: {"rows": stamped})


# --- the Toss shadow pass ------------------------------------------------------------------------------

def _pocket_effect(order: Mapping[str, Any]) -> tuple[str, float] | None:
    """(currency, cash effect) of one closed order's fills, or None if it filled nothing readable."""
    execution = order.get("execution") or {}
    try:
        filled = float(execution.get("filledAmount") or 0)
        commission = float(execution.get("commission") or 0)
        tax = float(execution.get("tax") or 0)
    except (TypeError, ValueError):
        return None
    if not filled:
        return None
    sign = -1.0 if str(order.get("side")).upper() == "BUY" else 1.0
    return str(order.get("currency") or "?").upper(), sign * filled - commission - tax


def collect_toss(feed: Any, snapshot: Any, state_dir: Path, *, now: str) -> dict[str, Any]:
    """Residuals per pocket since the last fire, explained by closed orders filled in between. Shadow:
    recorded as UNRESOLVED or as an internal exchange; nothing reads them for a verdict."""
    state = load_state(state_dir)
    rate = getattr(snapshot, "usd_krw_rate", None)
    domestic, overseas = getattr(snapshot, "domestic", None), getattr(snapshot, "overseas", None)
    krw = getattr(domestic, "cash_krw", None)
    usd = (getattr(overseas, "cash_krw", None) / rate) if rate and getattr(overseas, "cash_krw", None) is not None else None
    previous = state.get("toss")
    state["toss"] = {"at": now, "KRW": krw, "USD": usd}
    rows: list[dict[str, Any]] = []
    if previous and krw is not None and usd is not None and previous.get("KRW") is not None \
            and previous.get("USD") is not None:
        since, until = timeutil.parse_iso(previous["at"]), timeutil.parse_iso(now)
        kst = timedelta(hours=9)
        orders, truncated = feed.closed_orders(date_from=(since + kst).date().isoformat(),
                                               date_to=(until + kst).date().isoformat())
        explained = {"KRW": 0.0, "USD": 0.0}
        for order in orders:
            filled_at = (order.get("execution") or {}).get("filledAt")
            try:
                when = timeutil.parse_iso(str(filled_at)) if filled_at else None
            except ValueError:
                when = None
            effect = _pocket_effect(order)
            if when is None or effect is None or not (since < when <= until) or effect[0] not in explained:
                continue
            explained[effect[0]] += effect[1]
        residual = {"KRW": (krw - previous["KRW"]) - explained["KRW"],
                    "USD": (usd - previous["USD"]) - explained["USD"]}
        tolerance = {"KRW": 1.0, "USD": 0.01}
        open_ = {p: abs(v) > tolerance[p] for p, v in residual.items()}
        window = f"{previous['at']}..{now}"
        if open_["KRW"] and open_["USD"] and rate and residual["KRW"] * residual["USD"] < 0 \
                and abs(abs(residual["KRW"]) - abs(residual["USD"]) * rate) <= 0.01 * abs(residual["KRW"]):
            rows.append({"event": EVENT_TOSS_EXCHANGE, "source": "toss", "source_event_key": f"toss:exchange:{window}",
                         "source_confidence": RECONCILED, "accounting_status": "SHADOW_ONLY",
                         "krw_residual": f"{residual['KRW']:.0f}", "usd_residual": f"{residual['USD']:.2f}"})
        else:
            for pocket, flag in open_.items():
                if flag:
                    rows.append({"event": EVENT_TOSS_RESIDUAL, "source": "toss",
                                 "source_event_key": f"toss:residual:{pocket}:{window}", "pocket": pocket,
                                 "residual": f"{residual[pocket]:.{0 if pocket == 'KRW' else 2}f}",
                                 "orders_truncated": truncated, "source_confidence": UNRESOLVED,
                                 "valuation_status": UNVALUED, "accounting_status": "SHADOW_ONLY"})
    written = _append(state_dir, rows, now=now)
    _save_state(state_dir, state)
    return {"written": len(written)}


def summary(state_dir: Path) -> dict[str, Any]:
    """What may leave the process: counts and statuses, no asset, no amount."""
    rows = verify(state_dir)
    state = load_state(state_dir)
    return {
        "mode": ACCOUNTING_MODE,
        "events": len(rows),
        "external_flows": sum(1 for r in rows if r.get("event") == EVENT_FLOW),
        "internal_transfers": sum(1 for r in rows if r.get("event") == EVENT_INTERNAL),
        "held": sum(1 for r in rows if r.get("accounting_status") == HELD),
        "toss_residuals": sum(1 for r in rows if r.get("event") == EVENT_TOSS_RESIDUAL),
        "sources": {name: {"access": info.get("access"), "schema": info.get("schema")}
                    for name, info in sorted((state.get("sources") or {}).items())},
    }
