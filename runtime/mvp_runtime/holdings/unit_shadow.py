"""H6b-shadow: unitized NAV beside the drawdown — computed and shown, never judged by.

Thomas 2026-10-28 (``docs/proposals/H6B_UNIT_SHADOW_V0.1.md``, S1–S5 all A), on the units of
``PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md`` §4: a deposit issues units and a withdrawal burns them, so the NAV per
unit moves with the market and not with money in or out. The drawdown the board judges (``combined``), the
peak file, ``--reset-peak`` and every alert are untouched (D-H6-9).

**Points.** Each fire whose portfolio NAV is complete appends one line to ``holdings_nav_points.jsonl``
(:data:`FILENAME`): the NAV, the USD/KRW rate and the USDT price of each priced wallet asset that fire used,
and whether H6 was ready. Hash-chained (``chained_log``), local only: the lines carry KRW totals, so no door
reads this file (a test pins it, as for the baseline log).

**The path is recomputed every fire** (S4) from the points and the cash-flow ledger, so a flow Thomas
confirms later lands at its own point on the next fire, and nothing about units is ever written to the
ledger: ``units_changed`` lines begin with H6c, when the units become the authority.

- **Start (S1):** the first point at which H6 was ready, at NAV/unit = 100 (D-H6-4).
- **What moves units (S2):** an external deposit or withdrawal only. On Binance, a post-cutover flow that is
  READY, or a held payment Thomas closed as one; on Toss, a residual closed as one (by Thomas or by the
  rule). Income and fees are the account's own result, and read_incomplete, semantics_mismatch, exclusions,
  internal transfers and exchanges move no money in or out.
- **Valuation (S3):** at the prices of the point the flow lands on — the point that first follows the flow
  (Binance: the venue's event time; Toss: the window's end). USDT, USDC and FDUSD are one dollar, KRW is
  itself, any other asset is its wallet price at that point. No price there: the flow is unvalued.
- **Held:** an open exception after the start, or an unvalued flow, stops the path at the point before it.
  NAV/unit stays where it was until the next fire's recomputation gets past it.

``hourly_approximation_v1`` (§4): a flow is valued at the first complete NAV after it, so that hour's market
move on the flow's amount is credited to the units that stood before it.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from . import cash_flows, chained_log

FILENAME = "holdings_nav_points.jsonl"
RECORD_TYPE = "holdings_nav_point.v1"
EVENT_POINT = "nav_point"
NAV_POINTS_TAMPERED = "HOLDINGS_NAV_POINTS_TAMPERED"
NAV_POINTS_LOCKED = "HOLDINGS_NAV_POINTS_LOCKED"

START_NAV_PER_UNIT = 100.0
H6C_MIN_DAYS = 14
USD_STABLE = frozenset({"USDT", "USDC", "FDUSD"})
UNIT_KINDS = ("deposit", "withdrawal")

STATE_WAITING = "waiting_ready"     # no point at which H6 was ready yet
STATE_RUNNING = "running"
STATE_HELD = "held"                 # an open exception or an unvalued flow stops the path
STATE_BROKEN = "broken"             # the path could not be computed this fire (a chain, a read)
STATES = (STATE_WAITING, STATE_RUNNING, STATE_HELD, STATE_BROKEN)
HELD_OPEN_EXCEPTION = "open_exception"
HELD_UNVALUED = "unvalued_flow"
HELD_INVALID_NAV = "invalid_nav"
HELD_REASONS = (HELD_OPEN_EXCEPTION, HELD_UNVALUED, HELD_INVALID_NAV)


def path(state_dir: Path) -> Path:
    return state_dir / FILENAME


def verify(state_dir: Path) -> list[dict[str, Any]]:
    return chained_log.verify(path(state_dir), record_type=RECORD_TYPE, tamper_code=NAV_POINTS_TAMPERED)


def record_point(state_dir: Path, *, at: str, nav_krw: float, usd_krw_rate: float | None,
                 asset_price_usdt: Mapping[str, float], h6_ready: bool) -> list[dict[str, Any]]:
    """One point for one complete fire; a second line for the same ``at`` is dropped."""
    fields = {"event": EVENT_POINT, "point_key": f"nav:{at}", "at": at, "nav_krw": f"{nav_krw:.0f}",
              "usd_krw_rate": None if usd_krw_rate is None else f"{usd_krw_rate:.4f}",
              "asset_price_usdt": {asset: f"{price:.10g}" for asset, price in sorted(asset_price_usdt.items())
                                   if isinstance(price, (int, float)) and price > 0},
              "h6_ready": h6_ready is True}
    return chained_log.append_unique(path(state_dir), record_type=RECORD_TYPE, tamper_code=NAV_POINTS_TAMPERED,
                                     lock_code=NAV_POINTS_LOCKED, label="holdings NAV points",
                                     build=lambda existing: [fields], key="point_key")


def _ms(at: Any) -> int | None:
    try:
        return int(datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def unit_flows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The ledger's external deposits and withdrawals (S2), each with the time it lands by."""
    closed = cash_flows.resolutions(rows)
    status = cash_flows.effective_status(rows)
    flows = []
    for row in rows:
        event, key = row.get("event"), str(row.get("source_event_key"))
        kind = (closed.get(key) or {}).get("kind")
        if event == cash_flows.EVENT_FLOW:
            if row.get("accounting_eligibility") != cash_flows.ELIGIBLE:
                continue
            if kind is None and status.get(key) == cash_flows.READY and row.get("direction") in ("in", "out"):
                kind = "deposit" if row["direction"] == "in" else "withdrawal"
            if kind not in UNIT_KINDS:
                continue
            flows.append({"key": key, "kind": kind, "asset": str(row.get("asset") or ""),
                          "amount": row.get("amount"), "at_ms": row.get("event_ms")})
        elif event == cash_flows.EVENT_TOSS_RESIDUAL and kind in UNIT_KINDS:
            residual = _number(row.get("residual"))
            flows.append({"key": key, "kind": kind, "asset": "USD" if row.get("pocket") == "USD" else "KRW",
                          "amount": None if residual is None else abs(residual),
                          "at_ms": _ms(row.get("window_end"))})
    return flows


def _exception_ms(row: Mapping[str, Any]) -> int | None:
    if row.get("event") == cash_flows.EVENT_TOSS_RESIDUAL:
        return _ms(row.get("window_end"))
    if row.get("event") in (cash_flows.EVENT_FLOW, cash_flows.EVENT_INTERNAL):
        return row.get("event_ms") if isinstance(row.get("event_ms"), int) else None
    return _ms(row.get("at"))


def value_krw(flow: Mapping[str, Any], point: Mapping[str, Any]) -> float | None:
    """The flow's KRW value at the point it lands on (S3), or None when that point cannot price it."""
    amount = _number(flow.get("amount"))
    if amount is None or amount < 0:
        return None
    asset = flow.get("asset")
    if asset == "KRW":
        return amount
    rate = _number(point.get("usd_krw_rate"))
    if rate is None or rate <= 0:
        return None
    if asset == "USD" or asset in USD_STABLE:
        return amount * rate
    price = _number((point.get("asset_price_usdt") or {}).get(asset))
    return None if price is None or price <= 0 else amount * price * rate


def replay(points: list[Mapping[str, Any]], rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """The unit path from the first ready point through the last point it can pass. Pure."""
    out: dict[str, Any] = {"state": STATE_WAITING, "method": cash_flows.ACCOUNTING_METHOD,
                           "flows_applied": {kind: 0 for kind in UNIT_KINDS}, "held": 0, "held_reason": None,
                           "h6c_askable": False}
    start = next((i for i, p in enumerate(points) if p.get("h6_ready") is True), None)
    if start is None:
        return out
    first = points[start]
    start_ms, nav0 = _ms(first.get("at")), _number(first.get("nav_krw"))
    last_ms = _ms(points[-1].get("at"))
    out.update({"started_at": first.get("at"),
                "days": round(max(0, (last_ms or 0) - (start_ms or 0)) / 86_400_000, 1)})
    if start_ms is None or nav0 is None or nav0 <= 0:
        out.update({"state": STATE_HELD, "held": 1, "held_reason": HELD_INVALID_NAV})
        return out
    units, per_unit = nav0 / START_NAV_PER_UNIT, START_NAV_PER_UNIT
    peak, peak_at, as_of = per_unit, first.get("at"), first.get("at")
    flows = [f for f in unit_flows(rows) if isinstance(f["at_ms"], int)]     # one at or before the start never lands
    blocking = []
    for row in cash_flows.open_exceptions(rows).values():
        moment = _exception_ms(row)
        if moment is None or moment > start_ms:     # no readable time holds from the start
            blocking.append(start_ms + 1 if moment is None else moment)
    first_block = min(blocking, default=None)
    applied = out["flows_applied"]
    previous_ms, reason, unvalued = start_ms, None, 0
    for point in points[start + 1:]:
        point_ms, nav = _ms(point.get("at")), _number(point.get("nav_krw"))
        if point_ms is None or nav is None or nav <= 0:
            reason = HELD_INVALID_NAV
            break
        if first_block is not None and first_block <= point_ms:
            reason = HELD_OPEN_EXCEPTION
            break
        landing = [f for f in flows if previous_ms < f["at_ms"] <= point_ms]
        values = [value_krw(f, point) for f in landing]
        if any(v is None for v in values):
            reason, unvalued = HELD_UNVALUED, sum(1 for v in values if v is None)
            break
        net = sum(v if f["kind"] == "deposit" else -v for f, v in zip(landing, values))
        before = nav - net
        if before <= 0:
            reason = HELD_INVALID_NAV
            break
        # D-H6-8: each flow is its own issue or burn at the unit value before the point's flows.
        unit_value = before / units
        for flow, flow_value in zip(landing, values):
            units += (flow_value if flow["kind"] == "deposit" else -flow_value) / unit_value
            applied[flow["kind"]] += 1
        per_unit = nav / units
        if per_unit > peak:
            peak, peak_at = per_unit, point.get("at")
        previous_ms, as_of = point_ms, point.get("at")
    held = len(blocking) + unvalued if reason in (HELD_OPEN_EXCEPTION, HELD_UNVALUED) else (1 if reason else 0)
    out.update({
        "state": STATE_HELD if reason else STATE_RUNNING, "as_of": as_of, "held": held, "held_reason": reason,
        "nav_per_unit": round(per_unit, 4), "peak_nav_per_unit": round(peak, 4), "peak_at": peak_at,
        "drawdown_pct": round((per_unit / peak - 1.0) * 100.0, 2),
    })
    out["h6c_askable"] = (out["state"] == STATE_RUNNING and out["days"] >= H6C_MIN_DAYS
                          and all(applied[kind] >= 1 for kind in UNIT_KINDS))
    return out


def shadow(state_dir: Path) -> dict[str, Any]:
    """The block for this fire: both chains verified, then :func:`replay`. A broken chain raises."""
    return replay(verify(state_dir), cash_flows.verify(state_dir))
