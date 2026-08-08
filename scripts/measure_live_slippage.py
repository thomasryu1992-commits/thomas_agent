#!/usr/bin/env python3
"""Realized slippage on live fills, against the rate the cost model assumes. Read-only.

``REMAINING_WORK.md`` §F8 measures what the assumption is worth: the median candidate at the
current cost basis stops paying at **4.3 bps** and `cost.DEFAULT_SLIPPAGE_BPS` is **3.0**. §G1
indexes that constant as `INHERITED` — carried from the source system, never measured here — and
names *"enough live fills to measure realized slippage against intended price"* as what reopens
it. This is the instrument for that, and it reports what it cannot measure as loudly as what it
can.

**Both legs are measurable now; the entry became so on 2026-08-06 and only for rows opened
after that.** The asymmetry this tool was written against was a recording gap rather than a
fact about the venue: a protective stop is submitted at a price the runtime chose, so
``bracket[].stop_price`` against the fill was always a measurement, while a MARKET entry
recorded only ``fill.avg_price`` — the venue answers ``price: "0.00"`` for a market order, so
the intent had to be kept on this side or not at all. It now is: ``live_execution`` records
``intended_price`` on the entry (#585) and ``live_promotion`` on the canary (#589), both
present-and-None when the plan carried no price rather than substituting the fill.

**The gap closed forward, not backward.** Nothing backfills a row opened before that date, so
the population splits by age and the split is reported rather than averaged over — see
``_entries_by_position`` and the "no recorded intent" counts in the summary. Read a small
entry-side n as "the instrument is young", not as "the venue rarely slips."

A resting take-profit is included as a control: it fills AT its price by construction, so a
non-zero figure there is a bug in this script or in the leg, not a market observation.

Run:  python scripts/measure_live_slippage.py
"""

from __future__ import annotations

import json
import pathlib
import statistics
import sys
from typing import Any, Iterator

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime.crypto.cost import DEFAULT_SLIPPAGE_BPS  # noqa: E402

STATE = ROOT / ".runtime_governance_state"
OUTCOMES = STATE / "crypto" / "live_outcomes.jsonl"
CANARIES = STATE / "crypto" / "live_canary_orders.jsonl"
LEDGER = STATE / "runtime_ledger" / "records.jsonl"


def _rows(path: pathlib.Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            yield row


def _entries_by_position() -> dict[str, dict[str, Any]]:
    """The entry leg as submitted and filled, keyed by position.

    Carries `intended_price` from 2026-08-06 on. Rows opened before that have only the fill and
    are reported as unmeasurable rather than assumed to have filled at their intent.
    """
    out: dict[str, dict[str, Any]] = {}
    for row in _rows(LEDGER):
        record = row.get("record") or row
        opened = record.get("live_opened")
        if not isinstance(opened, dict):
            continue
        position_id = (opened.get("position") or {}).get("position_id")
        entry = opened.get("entry")
        if position_id and isinstance(entry, dict):
            out[str(position_id)] = entry
    return out


def _brackets_by_position() -> dict[str, list[dict[str, Any]]]:
    """The protective legs as submitted, keyed by the position they belong to.

    Read from the ledger rather than from ``live_positions/`` because that record is removed when
    the book goes flat — the very moment the outcome this measures becomes available.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for row in _rows(LEDGER):
        record = row.get("record") or row
        opened = record.get("live_opened")
        if not isinstance(opened, dict):
            continue
        position = opened.get("position") or {}
        position_id = position.get("position_id")
        bracket = opened.get("bracket")
        if position_id and isinstance(bracket, list):
            out[str(position_id)] = [leg for leg in bracket if isinstance(leg, dict)]
    return out


def _adverse_bps(intended: float, realized: float, side: str, close_reason: str) -> float | None:
    """Slippage as adverse basis points of the intended price. Positive means worse.

    ``side`` on a live outcome is **the closing order's side, not the position's direction** —
    both rows on this machine read ``BUY`` and both are SHORT positions being bought back. Read
    the other way the sign inverts, which is how the first run of this reported a stop that
    filled 4.46 above its trigger as an improvement.

    So: closing with a BUY means paying, and a higher fill is worse. Closing with a SELL means
    receiving, and a lower fill is worse.
    """
    if not (intended > 0 and realized > 0):
        return None
    closing_side = str(side).upper()
    if closing_side not in ("BUY", "SELL"):
        return None
    delta = (realized - intended) if closing_side == "BUY" else (intended - realized)
    return delta / intended * 10_000.0


def measure() -> dict[str, Any]:
    brackets = _brackets_by_position()
    entries = _entries_by_position()
    measured: list[dict[str, Any]] = []
    unmeasurable = 0
    entries_without_intent = 0
    for position_id, entry in sorted(entries.items()):
        intended = entry.get("intended_price")
        realized = (entry.get("fill") or {}).get("avg_price")
        if not isinstance(intended, (int, float)) or not isinstance(realized, (int, float)):
            entries_without_intent += 1
            continue
        # The entry BUYS to open a long and SELLS to open a short, so the adverse direction is
        # the mirror of the exit's: paying more, or receiving less, than the plan assumed.
        side = str((entry.get("submit_response") or {}).get("side") or "").upper()
        bps = _adverse_bps(float(intended), float(realized), side, "entry")
        if bps is None:
            entries_without_intent += 1
            continue
        measured.append({"symbol": entry.get("symbol"), "close_reason": "entry",
                         "intended": float(intended), "realized": float(realized),
                         "adverse_bps": round(bps, 2), "closed_at_utc": entry.get("created_at")})
    canaries_without_intent = 0
    for canary in _rows(CANARIES):
        intended, realized = canary.get("intended_price"), canary.get("fill_avg_price")
        if not isinstance(intended, (int, float)) or not isinstance(realized, (int, float)):
            canaries_without_intent += 1
            continue
        bps = _adverse_bps(float(intended), float(realized), str(canary.get("side") or ""), "canary")
        if bps is None:
            canaries_without_intent += 1
            continue
        measured.append({"symbol": canary.get("symbol"), "close_reason": "canary",
                         "intended": float(intended), "realized": float(realized),
                         "adverse_bps": round(bps, 2),
                         "closed_at_utc": canary.get("recorded_at_utc")})
    for outcome in _rows(OUTCOMES):
        if outcome.get("outcome_closed") is not True:
            continue
        legs = brackets.get(str(outcome.get("position_id")) or "")
        reason = str(outcome.get("close_reason") or "")
        if not legs or reason not in ("stop_loss", "take_profit"):
            unmeasurable += 1
            continue
        # The stop is the leg carrying a trigger; the target rests at its own price.
        wanted = "stop_price" if reason == "stop_loss" else "price"
        intended = None
        for leg in legs:
            value = leg.get(wanted)
            if isinstance(value, (int, float)) and value:
                intended = float(value)
                break
        realized = outcome.get("exit_price")
        if intended is None or not isinstance(realized, (int, float)):
            unmeasurable += 1
            continue
        bps = _adverse_bps(intended, float(realized), str(outcome.get("side") or ""), reason)
        if bps is None:
            unmeasurable += 1
            continue
        measured.append({
            "symbol": outcome.get("symbol"), "close_reason": reason,
            "intended": intended, "realized": float(realized), "adverse_bps": round(bps, 2),
            "closed_at_utc": outcome.get("closed_at_utc"),
        })
    return {"measured": measured, "unmeasurable": unmeasurable,
            "entries_without_intent": entries_without_intent,
            "canaries_without_intent": canaries_without_intent,
            "modelled_bps": DEFAULT_SLIPPAGE_BPS}


def render(result: dict[str, Any]) -> str:
    rows = result["measured"]
    out = [f"modelled slippage (cost.DEFAULT_SLIPPAGE_BPS): {result['modelled_bps']} bps per leg",
           ""]
    if not rows:
        out.append("no exit leg has both an intended and a realized price yet.")
    else:
        out.append(f"{'symbol':<10}{'leg':<14}{'intended':>12}{'realized':>12}{'adverse bps':>13}")
        for row in rows:
            out.append(f"{row['symbol']:<10}{row['close_reason']:<14}{row['intended']:>12.5f}"
                       f"{row['realized']:>12.5f}{row['adverse_bps']:>13.2f}")
        stops = [r["adverse_bps"] for r in rows if r["close_reason"] == "stop_loss"]
        if stops:
            out.append("")
            # Each multiple sits against the figure it divides. It used to trail the whole line
            # — `median 11.73 bps  worst 23.47  against 3.0 modelled (7.8x)` — where `(7.8x)`
            # reads as the median's, and 7.8 is the WORST reading; the median's is 3.9. A reader
            # who quotes the summary rather than the table gets a number twice too large.
            #
            # Deliberately not extended to the entry and canary lines below. Their slippage can
            # be NEGATIVE (BTCUSDT's live entry filled 4.67 bps BETTER than intended), and a
            # multiple of an assumption is meaningless once the sign flips — `-1.6x modelled`
            # states nothing a reader can use, and pooling it into a median of multiples would
            # be worse. The bps figure carries its own sign; a ratio does not.
            modelled = result["modelled_bps"]
            median = statistics.median(stops)
            out.append(f"stop fills: n={len(stops)}  "
                       f"median {median:.2f} bps ({median/modelled:.1f}x modelled {modelled})  "
                       f"worst {max(stops):.2f} ({max(stops)/modelled:.1f}x)")
    out.append("")
    out.append(f"exits with no comparable pair: {result['unmeasurable']}")
    out.append("")
    entries = [r for r in rows if r["close_reason"] == "entry"]
    if entries:
        out.append(f"entry fills: n={len(entries)}  median "
                   f"{statistics.median(r['adverse_bps'] for r in entries):.2f} bps")
    canaries = [r for r in rows if r["close_reason"] == "canary"]
    if canaries:
        out.append(f"canary fills: n={len(canaries)}  median "
                   f"{statistics.median(r['adverse_bps'] for r in canaries):.2f} bps")
    out.append(f"entries with no recorded intent: {result['entries_without_intent']}"
               "   canaries with none: " + str(result["canaries_without_intent"])
               + "   (both predate `intended_price`, 2026-08-06)")
    out.append("")
    out.append("A canary is an entry-only MARKET order placed to validate the path, so it is the")
    out.append("one entry this runtime can make without routing a strategy signal — the")
    out.append("instrument for this constant while live entries are held down.")
    return "\n".join(out)


def main() -> int:
    print(render(measure()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
