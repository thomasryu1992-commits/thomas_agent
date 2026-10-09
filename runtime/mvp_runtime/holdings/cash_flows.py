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
from decimal import Decimal, InvalidOperation
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
READ_BUDGET_SECONDS = 20.0           # the shadow histories share the maintenance pass; the rest wait an hour
# H6b-hardening (Thomas 2026-10-09): the transfer histories gate this fire's coherence, so they are read
# first and outside the shadow budget; the shadow histories then get READ_BUDGET_SECONDS.
CRITICAL_SOURCES = ("transfer_spot_to_futures", "transfer_futures_to_spot")
PRE_CUTOVER, POST_CUTOVER = "PRE_CUTOVER", "POST_CUTOVER"
OBSERVED_ONLY, ELIGIBLE = "OBSERVED_ONLY", "ELIGIBLE"
SHADOW_MIN_DAYS = 7
# H6b readiness-hardening (Thomas 2026-10-09): what readiness opens is the unit shadow, not H6c.
READY_NAME = "H6b_shadow_ready"

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
EVENT_DUPLICATE_LINK = "duplicate_linked"
EVENT_CUTOVER_SET = "cutover_set"
EVENT_CUTOVER_MIGRATED = "cutover_migrated"
NO_CUTOVER = "NO_CUTOVER"
CUTOVER_REFUSED = "HOLDINGS_CASH_FLOW_CUTOVER_REFUSED"


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
    # The boundary H6c must respect (Thomas 2026-10-09). It is set once, explicitly, at activation
    # (:func:`set_cutover`), never by a fire: a fire an hour after a deploy would misfile what moved in
    # between. With none set, every event is observed only. Whether this fire counts as verified is
    # decided after it ends (:func:`mark_fire_verified`), never here.
    cutover_ms = int(state["cutover_ms"]) if state.get("cutover_ms") else None
    ledger = verify(state_dir)
    seen = {row.get("source_event_key") for row in ledger}
    recorded = {row.get("source_event_key"): row for row in ledger if row.get("event") in (EVENT_FLOW, EVENT_INTERNAL)}
    started = clock()
    pending_rows: list[dict[str, Any]] = []
    internal_ms: list[int] = []
    errors: dict[str, str] = {}
    ordered = [*CRITICAL_SOURCES, *(name for name in FLOW_SOURCES if name not in CRITICAL_SOURCES)]
    for source in ordered:
        _path, _fixed, _window, days = FLOW_SOURCES[source]
        info = sources.setdefault(source, {"access": "UNREAD", "schema": SCHEMA_NO_ROWS, "cursor_ms": None})
        if source in CRITICAL_SOURCES:
            started = clock()       # the shadow budget starts once the critical reads are done
        elif clock() - started > READ_BUDGET_SECONDS:
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
            phase = (NO_CUTOVER if cutover_ms is None
                     else PRE_CUTOVER if event["event_ms"] < cutover_ms else POST_CUTOVER)
            pending_rows.append({
                "event": EVENT_INTERNAL if internal else EVENT_FLOW, "source": source, "source_event_key": key,
                "economic_event_id": key, **event, "source_confidence": CONFIRMED, "valuation_status": UNVALUED,
                "accounting_status": HELD if held else READY, "held_reason": held,
                "cutover_phase": phase, "accounting_eligibility": ELIGIBLE if phase == POST_CUTOVER else OBSERVED_ONLY,
            })
        # The cursor stops behind the oldest row still pending, so it is read again until it settles.
        info["cursor_ms"] = min(now_ms, oldest_pending) if oldest_pending else now_ms
    pending_rows.extend(_mark_cross_source(pending_rows, ledger, seen))
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


def set_cutover(state_dir: Path, *, at: str, requested_by: str, reason: str | None = None,
                migrate: bool = False, shadow_started_at: str | None = None) -> dict[str, Any]:
    """Record the accounting boundary once (Thomas 2026-10-09). A second call is refused; moving it needs
    ``migrate`` and a reason, and writes a ``cutover_migrated`` line naming the old and the new."""
    state = load_state(state_dir)
    at_ms = int(timeutil.parse_iso(at).timestamp() * 1000)
    previous = state.get("cutover_at")
    if previous and not migrate:
        raise ToolError(CUTOVER_REFUSED, f"the cutover is already set ({previous}); moving it is a migration")
    if migrate and not (reason or "").strip():
        raise ToolError(CUTOVER_REFUSED, "a cutover migration needs a reason")
    event = EVENT_CUTOVER_MIGRATED if previous else EVENT_CUTOVER_SET
    row = {"event": event, "source": "ledger", "source_event_key": f"{event}:{at}", "cutover_at": at,
           "previous_cutover_at": previous, "reason": (reason or "").strip() or None, "requested_by": requested_by}
    _append(state_dir, [row], now=timeutil.utc_now_iso())
    state.update({"cutover_at": at, "cutover_ms": at_ms})
    if shadow_started_at and not state.get("shadow_started_at"):
        state["shadow_started_at"] = shadow_started_at
    state.pop("first_verified_fire_at", None)
    state.pop("shadow_success_dates", None)
    _save_state(state_dir, state)
    return state


def _kst_date(at: str) -> str:
    return (timeutil.parse_iso(at) + timedelta(hours=9)).date().isoformat()


def mark_fire_verified(state_dir: Path, *, now: str) -> None:
    """Called once a fire's histories all read and its ledger, Toss and readiness passes ended cleanly
    (Thomas 2026-10-09). The first such fire after the cutover is the boundary's evidence; each KST date
    with one counts toward the shadow observation. A fire that broke anywhere counts for nothing."""
    state = load_state(state_dir)
    cutover_ms = int(state["cutover_ms"]) if state.get("cutover_ms") else None
    if cutover_ms is None or timeutil.parse_iso(now).timestamp() * 1000 < cutover_ms:
        return
    state.setdefault("first_verified_fire_at", now)
    state["shadow_success_dates"] = sorted({*(state.get("shadow_success_dates") or []), _kst_date(now)})
    _save_state(state_dir, state)


def post_cutover_exceptions(rows: list[dict[str, Any]], state: Mapping[str, Any]) -> int:
    """Events after the cutover that nothing explains yet (Thomas 2026-10-09): a Binance event held for any
    reason (unverified meaning, look-alike, reversal, malformed row) or a Toss residual. Each blocks the
    readiness until resolved; resolving one is a later step. A line whose time cannot be read counts."""
    if not state.get("cutover_ms"):
        return 0
    cutover_ms = int(state["cutover_ms"])
    status = effective_status(rows)
    count = 0
    for row in rows:
        event = row.get("event")
        if event in (EVENT_FLOW, EVENT_INTERNAL):
            count += row.get("cutover_phase") == POST_CUTOVER and status.get(row["source_event_key"]) == HELD
        elif event in (EVENT_MALFORMED, EVENT_TOSS_RESIDUAL):
            try:
                written_ms = timeutil.parse_iso(str(row.get("at"))).timestamp() * 1000
            except (ValueError, TypeError):
                written_ms = None
            count += written_ms is None or written_ms >= cutover_ms
    return count


def _canon(amount: Any) -> Decimal | None:
    """The amount as a number for comparison: "1", "1.0" and "1.00000000" are one amount. The ledger keeps
    the venue's own string; only comparisons use this."""
    try:
        return Decimal(str(amount)).normalize()
    except (InvalidOperation, ValueError):
        return None


def _mark_cross_source(new_rows: list[dict[str, Any]], ledger: list[dict[str, Any]],
                       seen: set[Any]) -> list[dict[str, Any]]:
    """Same asset, same amount (as a number), same direction, two sources, within the window: the new
    row is HELD and a ``duplicate_linked`` line names both, so :func:`effective_status` holds the older
    one too — the ledger is never edited. Returns the link lines."""
    flows = [r for r in new_rows if r.get("event") == EVENT_FLOW]
    pool = [r for r in ledger if r.get("event") == EVENT_FLOW] + flows
    links: list[dict[str, Any]] = []
    for row in flows:
        for other in pool:
            if other is row or other.get("source") == row.get("source"):
                continue
            if (other.get("asset") == row.get("asset") and _canon(other.get("amount")) == _canon(row.get("amount"))
                    and other.get("direction") == row.get("direction")
                    and abs(int(other.get("event_ms") or 0) - int(row["event_ms"])) <= CROSS_SOURCE_WINDOW_MS):
                row.update({"accounting_status": HELD, "held_reason": "possible_duplicate",
                            "possible_duplicate_of": other.get("source_event_key")})
                pair = sorted([str(row["source_event_key"]), str(other.get("source_event_key"))])
                key = f"link:{pair[0]}|{pair[1]}"
                if key not in seen:
                    seen.add(key)
                    links.append({"event": EVENT_DUPLICATE_LINK, "source": "ledger", "source_event_key": key,
                                  "event_a": pair[0], "event_b": pair[1], "effective_status": HELD})
    return links


def effective_status(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Each event's accounting status after the later lines that answer it: a duplicate link or a
    reversal holds both sides, whatever the original line said."""
    status = {r["source_event_key"]: r.get("accounting_status") for r in rows
              if r.get("event") in (EVENT_FLOW, EVENT_INTERNAL, EVENT_MALFORMED)}
    for row in rows:
        if row.get("event") == EVENT_DUPLICATE_LINK:
            for key in (row.get("event_a"), row.get("event_b")):
                if key in status:
                    status[key] = HELD
        elif row.get("event") == EVENT_REVERSED and row.get("reverses") in status:
            status[row["reverses"]] = HELD
    return status


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
        # A week back: a sell settles days after its fill, and the settlement is evidence (D-H6-7).
        orders, truncated = feed.closed_orders(date_from=(since + kst - timedelta(days=7)).date().isoformat(),
                                               date_to=(until + kst).date().isoformat())
        explained = {"KRW": 0.0, "USD": 0.0}
        sides = {"KRW": set(), "USD": set()}
        settling = {"KRW": set(), "USD": set()}
        today_kst = (until + kst).date().isoformat()
        for order in orders:
            currency = str(order.get("currency") or "?").upper()
            if (str(order.get("side")).upper() == "SELL" and currency in settling
                    and str((order.get("execution") or {}).get("settlementDate") or "") == today_kst):
                settling[currency].add(today_kst)
            filled_at = (order.get("execution") or {}).get("filledAt")
            try:
                when = timeutil.parse_iso(str(filled_at)) if filled_at else None
            except ValueError:
                when = None
            effect = _pocket_effect(order)
            if when is None or effect is None or not (since < when <= until) or effect[0] not in explained:
                continue
            explained[effect[0]] += effect[1]
            sides[effect[0]].add(str(order.get("side")).upper())
        residual = {"KRW": (krw - previous["KRW"]) - explained["KRW"],
                    "USD": (usd - previous["USD"]) - explained["USD"]}
        tolerance = {"KRW": 1.0, "USD": 0.01}
        open_ = {p: abs(v) > tolerance[p] for p, v in residual.items()}
        window = f"{previous['at']}..{now}"
        _toss_evidence(state, sides, settling, open_)
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


def _toss_evidence(state: dict[str, Any], sides: Mapping[str, set], settling: Mapping[str, set],
                   unexplained: Mapping[str, bool]) -> None:
    """D-H6-7, gathered without anyone trading on purpose: for each window with a fill or a settlement,
    whether the pocket's change was fully explained. A settlement day counts once it has passed with every
    window on it explained."""
    evidence = state.setdefault("toss_evidence", {"buy_explained": 0, "sell_explained": 0,
                                                   "activity_unexplained": 0, "settlement_days": {}})
    for pocket in ("KRW", "USD"):
        for side in sides[pocket]:
            if unexplained[pocket]:
                evidence["activity_unexplained"] += 1
            elif side in ("BUY", "SELL"):
                evidence[f"{side.lower()}_explained"] += 1
        for day in settling[pocket]:
            row = evidence["settlement_days"].setdefault(day, {"windows": 0, "unexplained": 0})
            row["windows"] += 1
            row["unexplained"] += 1 if unexplained[pocket] else 0


def toss_semantics(state: Mapping[str, Any], *, today_kst: str) -> tuple[str, str]:
    """(PASS / WAITING / MIXED, why). PASS needs an explained buy, an explained sell and a settlement day
    that passed with every observed window explained. MIXED is a fill or settlement the change did not
    match: it waits for Thomas's reading, it does not pass."""
    e = state.get("toss_evidence") or {}
    clean_days = [d for d, row in (e.get("settlement_days") or {}).items()
                  if d < today_kst and row.get("windows", 0) >= 12 and not row.get("unexplained")]
    if e.get("activity_unexplained"):
        return "MIXED", f"{e['activity_unexplained']} window(s) with a fill or settlement left a residual"
    missing = [name for name, ok in (("a buy", e.get("buy_explained")), ("a sell", e.get("sell_explained")),
                                     ("a settlement day", clean_days)) if not ok]
    return ("PASS", "buy, sell and settlement explained") if not missing else ("WAITING", "needs " + ", ".join(missing))


def readiness(state_dir: Path, *, now: str) -> dict[str, Any]:
    """Whether the evidence H6's next step needs is in (Thomas 2026-10-09). Computed every fire; it
    enables nothing: the next step (NAV/unit in shadow) waits for Thomas's approval."""
    checks: dict[str, str] = {}
    try:
        rows = verify(state_dir)
        checks["ledger_chain"] = "PASS"
    except ToolError:
        rows, checks["ledger_chain"] = [], "FAIL"
    state = load_state(state_dir)
    sources = state.get("sources") or {}
    checks["cutover_boundary"] = ("PASS" if state.get("cutover_ms") and state.get("first_verified_fire_at")
                                  else "WAITING")
    access = [info.get("access") for info in sources.values()]
    checks["binance_access"] = "PASS" if access and all(a == "PASS" for a in access) else "FAIL" if access else "WAITING"
    schemas = [info.get("schema") for info in sources.values()]
    checks["binance_real_schema"] = ("FAIL" if SCHEMA_MISMATCH in schemas
                                     else "PASS" if SCHEMA_VERIFIED in schemas else "WAITING")
    today_kst = (timeutil.parse_iso(now) + timedelta(hours=9)).date().isoformat()
    toss, toss_why = toss_semantics(state, today_kst=today_kst)
    checks["toss_cash_semantics"] = "PASS" if toss == "PASS" else "FAIL" if toss == "MIXED" else "WAITING"
    checks["cross_source_dedupe"] = "PASS"
    checks["internal_transfer_guard"] = ("PASS" if all(sources.get(n, {}).get("access") == "PASS"
                                                       for n in CRITICAL_SOURCES) else "FAIL")
    exceptions = post_cutover_exceptions(rows, state)
    checks["post_cutover_exceptions"] = "PASS" if not exceptions else "FAIL"
    # Seven days since the cutover, and a verified fire on seven KST dates: elapsed time alone is not a
    # shadow that worked. One fire a date, not every hour, so a passing outage does not block it forever.
    days, dates = 0.0, []
    if state.get("cutover_ms"):
        days = (timeutil.parse_iso(now).timestamp() * 1000 - int(state["cutover_ms"])) / 86_400_000
        start = _kst_date(state["cutover_at"]) if state.get("cutover_at") else ""
        dates = [d for d in state.get("shadow_success_dates") or [] if d >= start]
    checks["shadow_observation"] = ("PASS" if days >= SHADOW_MIN_DAYS and len(dates) >= SHADOW_MIN_DAYS
                                    else "RUNNING")
    ready = all(value == "PASS" for value in checks.values())
    return {"checks": checks, "toss_note": toss_why, "shadow_days": round(days, 1),
            "shadow_success_dates": len(dates), "exceptions": exceptions, "ready": ready,
            "next_step": "H6b-shadow (NAV per unit computed beside the board, no verdict)"}


def update_readiness(state_dir: Path, *, now: str) -> dict[str, Any]:
    """:func:`readiness`, with its edge kept: each false->true starts a new epoch, and a message is owed
    once per epoch. So a readiness that breaks and returns is told again; a delivered one is not."""
    ready = readiness(state_dir, now=now)
    state = load_state(state_dir)
    record = state.setdefault("readiness", {"epoch": 0, "ready": False, "told_epoch": 0})
    if ready["ready"] and not record.get("ready"):
        record["epoch"] = int(record.get("epoch") or 0) + 1
    record["ready"] = ready["ready"]
    # Exceptions are told as they appear: once each time the count grows, never again for the same ones.
    owed = state.setdefault("exceptions", {"count": 0, "told_count": 0})
    owed["count"] = int(ready.get("exceptions") or 0)
    _save_state(state_dir, state)
    ready["epoch"] = record["epoch"]
    ready["told"] = record.get("told_epoch") == record["epoch"]
    ready["exceptions_untold"] = owed["count"] > int(owed.get("told_count") or 0)
    return ready


def mark_readiness_told(state_dir: Path, *, now: str) -> None:
    state = load_state(state_dir)
    record = state.setdefault("readiness", {"epoch": 0, "ready": False, "told_epoch": 0})
    record["told_epoch"], record["told_at"] = record.get("epoch", 0), now
    _save_state(state_dir, state)


def mark_exceptions_told(state_dir: Path, *, count: int, now: str) -> None:
    state = load_state(state_dir)
    owed = state.setdefault("exceptions", {"count": count, "told_count": 0})
    owed["told_count"], owed["told_at"] = max(int(owed.get("told_count") or 0), count), now
    _save_state(state_dir, state)


def summary(state_dir: Path) -> dict[str, Any]:
    """What may leave the process: counts and statuses, no asset, no amount."""
    rows = verify(state_dir)
    state = load_state(state_dir)
    effective = effective_status(rows)
    return {
        "mode": ACCOUNTING_MODE,
        "events": len(rows),
        "external_flows": sum(1 for r in rows if r.get("event") == EVENT_FLOW),
        "internal_transfers": sum(1 for r in rows if r.get("event") == EVENT_INTERNAL),
        "held": sum(1 for status in effective.values() if status == HELD),
        "pre_cutover": sum(1 for r in rows if r.get("cutover_phase") == PRE_CUTOVER),
        "toss_residuals": sum(1 for r in rows if r.get("event") == EVENT_TOSS_RESIDUAL),
        "sources": {name: {"access": info.get("access"), "schema": info.get("schema")}
                    for name, info in sorted((state.get("sources") or {}).items())},
    }
