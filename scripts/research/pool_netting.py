"""Pool netting, stage 1: what a per-symbol net book would have held, computed from the pool's forward record.

``docs/proposals/NETTING_STAGE1_DERIVED_BOOK_V0.1.md`` (Thomas 2026-11-13, S1–S4). No book is written: every
figure is recomputed from the per-strategy forward record each time, so there is nothing a live path could read.

- **What it reads:** :data:`ALLOWED` — the seven fields the overlap measurement reads, plus ``entry_price`` and
  ``risk``. Of those two only their ratio is used: ``entry_price / risk`` is the position's notional per 1R of
  risk, the same sizing the live leg uses (quantity = equity x 1% / risk). No result, exit price, cost or close
  reason survives the load. The forward cohort is never read.
- **Bets:** one bet is one symbol and one direction (S3). The design's correlation rule needs daily results, which
  this stage does not read.
- **Three ways to weigh a position** (S4 — both splits are computed and neither is chosen):
  ``v0`` every strategy 1R, as today; ``v1`` the k strategies of a bet hold 1/k each at all times, so the bet is
  always 1R and the others are resized whenever one comes or goes (inside the book: what a newcomer takes the
  others give up at the same instant, so only the net reaches the market); ``v2`` a strategy takes 1/k when it enters
  (k counts itself, those already in and those entering at the same instant) and keeps it until it closes.
- **Netted volume** is counted only where opposite-signed changes share a timestamp: at each instant, the sum of
  the strategies' absolute changes minus the absolute net change. Opposite positions that merely overlap in time
  save no order; they lower the net notional.
- **Units:** notional is in "the money of 1R" until an equity is assumed; the order-size reading assumes two.
- **What it cannot see:** closed positions only (the last days undercount), fills at the recorded entry price, no
  partial fills, no protective orders.
- **No judgement.** No strategy is named, and nothing here feeds a door.

Runs in the research sandbox, over the lake snapshot it is given.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from runtime.mvp_runtime.crypto import cost as costs  # noqa: E402
from runtime.mvp_runtime.crypto.live_sizing import RISK_PER_TRADE_FRACTION  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402

SPEC = "NETTING_STAGE1_DERIVED_BOOK_V0.1"
SOURCE = "forward_outcomes.jsonl"          # the pool's per-strategy forward book, as the lake exports it
ALLOWED = ("candidate_id", "symbol", "timeframe", "direction", "opened_at_utc", "created_at_utc", "seeded",
           "entry_price", "risk")
SIGN = {"LONG": 1, "SHORT": -1}
VARIANTS = ("v0", "v1", "v2")
ASSUMED_EQUITIES = (1000.0, 10000.0)       # USDT; assumptions, not anyone's account
ORDER_LIMIT_USDT = 60.0                    # the registered per-order limit the design doc cites (its section 6, item 3)
_EPS = 1e-12


def _only_allowed(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """The JSON object with every field outside :data:`ALLOWED` dropped at parse time."""
    return {key: value for key, value in pairs if key in ALLOWED}


def _positive(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


def read(lines: Iterable[str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The positions and what became of every row. A position keeps a strategy, a symbol, a direction, both times,
    the seeded mark and ``per_r`` — its notional per 1R — and neither of the two numbers that ratio came from."""
    out, seen = [], Counter()
    for line in lines:
        if not line.strip():
            continue
        seen["rows"] += 1
        try:
            row = json.loads(line, object_pairs_hook=_only_allowed)
        except ValueError:
            seen["left_out_unreadable_line"] += 1
            continue
        if not isinstance(row, dict) or not row.get("candidate_id") or not row.get("symbol"):
            seen["left_out_no_strategy_or_symbol"] += 1
            continue
        sign = SIGN.get(str(row.get("direction")).upper())
        if sign is None:
            seen["left_out_no_direction"] += 1
            continue
        try:
            opened = timeutil.parse_iso(str(row["opened_at_utc"])).timestamp()
            closed = timeutil.parse_iso(str(row["created_at_utc"])).timestamp()
        except (KeyError, ValueError, TypeError):
            seen["left_out_no_open_or_close_time"] += 1
            continue
        if closed <= opened:
            seen["left_out_no_time_held"] += 1
            continue
        price, risk = _positive(row.get("entry_price")), _positive(row.get("risk"))
        if price is None or risk is None:
            seen["left_out_no_entry_price_or_risk"] += 1
            continue
        seen["kept"] += 1
        out.append({"who": str(row["candidate_id"]), "symbol": str(row["symbol"]), "sign": sign,
                    "open": opened, "close": closed, "seeded": bool(row.get("seeded")), "per_r": price / risk})
    return out, dict(sorted(seen.items()))


def load(lines: Iterable[str]) -> list[dict[str, Any]]:
    return read(lines)[0]


def one_holder_per_strategy(positions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """A strategy holds one position on a symbol at a time: a row that opens while an earlier row of the same
    strategy and symbol is still open is set aside (the overlap measurement's rule). Returns what is kept and how
    many rows were set aside."""
    kept: list[dict[str, Any]] = []
    last_close: dict[tuple[str, str], float] = {}
    dropped = 0
    for p in sorted(positions, key=lambda p: (p["open"], p["close"], p["who"])):
        key = (p["who"], p["symbol"])
        if p["open"] < last_close.get(key, -math.inf):
            dropped += 1
            continue
        last_close[key] = p["close"]
        kept.append(p)
    return kept, dropped


def _entry_share(p: dict[str, Any], positions: list[dict[str, Any]]) -> float:
    """v2: one over the strategies in the bet at the instant ``p`` enters — itself, those already in, and those
    entering at the same instant."""
    return 1.0 / sum(1 for q in positions if q["sign"] == p["sign"] and q["open"] <= p["open"] < q["close"])


def _weights(variant: str, held: list[dict[str, Any]], entry_share: dict[int, float]) -> dict[int, float]:
    if variant == "v0":
        return {id(p): 1.0 for p in held}
    if variant == "v2":
        return {id(p): entry_share[id(p)] for p in held}
    in_bet = Counter(p["sign"] for p in held)
    return {id(p): 1.0 / in_bet[p["sign"]] for p in held}


def symbol_walk(positions: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    """One symbol under one weighting: the stretches between events (what was held) and the events themselves
    (what changed). ``positions`` are one symbol's, already one holder per strategy."""
    times = sorted({p["open"] for p in positions} | {p["close"] for p in positions})
    entry_share = {id(p): _entry_share(p, positions) for p in positions}
    by_id = {id(p): p for p in positions}
    stretches, events = [], []
    before: dict[int, float] = {}                        # signed notional (in 1R money) per position
    for index, t in enumerate(times):
        held = [p for p in positions if p["open"] <= t < p["close"]]
        weight = _weights(variant, held, entry_share)
        after = {id(p): p["sign"] * weight[id(p)] * p["per_r"] for p in held}
        changes = {key: after.get(key, 0.0) - before.get(key, 0.0) for key in set(after) | set(before)}
        moved = {key: delta for key, delta in changes.items() if abs(delta) > _EPS}
        if moved:
            events.append({
                "gross": sum(abs(d) for d in moved.values()), "net": abs(sum(moved.values())),
                # A resize is a change to a position that neither opened nor closed at this instant.
                "resizes": [abs(d) for key, d in moved.items() if key in after and key in before],
                "entries": [abs(d) for key, d in moved.items()
                            if key in after and key not in before and by_id[key]["open"] == t],
            })
        if index + 1 < len(times):
            bets = Counter()
            for p in held:
                bets[p["sign"]] += weight[id(p)]
            stretches.append({
                "start": t, "end": times[index + 1],
                "gross": sum(abs(q) for q in after.values()), "net": abs(sum(after.values())),
                "risk": sum(weight.values()), "largest_bet": max(bets.values(), default=0.0),
            })
        before = after
    return {"stretches": stretches, "events": events}


def _weighted(pairs: list[tuple[float, float]]) -> dict[str, float | None]:
    """Time-weighted mean, 95th percentile and maximum of (duration, value) pairs."""
    total = sum(d for d, _ in pairs)
    if total <= 0:
        return {"mean": None, "p95": None, "max": None}
    running, p95 = 0.0, None
    for duration, value in sorted(pairs, key=lambda pair: pair[1]):
        running += duration
        if p95 is None and running >= 0.95 * total - _EPS:
            p95 = value
    return {"mean": round(sum(d * v for d, v in pairs) / total, 4), "p95": round(p95, 4),
            "max": round(max(v for _, v in pairs), 4)}


def _spread(values: list[float]) -> dict[str, float | None]:
    """10th percentile, median and 90th percentile by nearest rank."""
    if not values:
        return {"p10": None, "median": None, "p90": None}
    ordered = sorted(values)

    def at(share: float) -> float:
        return round(ordered[min(len(ordered) - 1, max(0, math.ceil(share * len(ordered)) - 1))], 2)

    return {"p10": at(0.10), "median": at(0.50), "p90": at(0.90)}


def _order_sizes(amounts: list[float]) -> dict[str, Any]:
    """Order sizes in USDT at each assumed equity: ``amounts`` are in 1R money, and 1R is 1% of equity."""
    out = {}
    for equity in ASSUMED_EQUITIES:
        usdt = [a * RISK_PER_TRADE_FRACTION * equity for a in amounts]
        out[f"equity_{int(equity)}"] = {
            **_spread(usdt), "count": len(usdt),
            "share_over_order_limit": round(sum(1 for u in usdt if u > ORDER_LIMIT_USDT) / len(usdt), 4) if usdt else None,
        }
    return out


def _fill_cost_fraction() -> float:
    """What one fill costs as a fraction of its notional: half the cost model's round trip (taker and slippage)."""
    return costs.round_trip_cost_r("LONG", 1.0, 1.0, cost=costs.CostModel()) / 2.0


def _summary(walks: list[dict[str, Any]]) -> dict[str, Any]:
    """Readings 1–6 over one symbol's walk, or over several symbols' walks laid on one clock."""
    bounds = sorted({s[edge] for w in walks for s in w["stretches"] for edge in ("start", "end")})
    gross, net, risk, largest = [], [], [], []
    for start, end in zip(bounds, bounds[1:]):
        live = [s for w in walks for s in w["stretches"] if s["start"] <= start and s["end"] >= end]
        duration = end - start
        gross.append((duration, sum(s["gross"] for s in live)))
        net.append((duration, sum(s["net"] for s in live)))            # netting is per symbol, never across symbols
        risk.append((duration, sum(s["risk"] for s in live)))
        largest.append((duration, max((s["largest_bet"] for s in live), default=0.0)))
    events = [e for w in walks for e in w["events"]]
    turnover = sum(e["gross"] for e in events)
    netted = sum(e["gross"] - e["net"] for e in events)
    resizes = [r for e in events for r in e["resizes"]]
    return {
        "notional_in_1r_money": {"gross": _weighted(gross), "net": _weighted(net)},
        "open_risk_r": _weighted(risk),
        "largest_bet_r": _weighted(largest)["max"],
        "turnover_in_1r_money": round(turnover, 4),
        "netted_turnover_in_1r_money": round(netted, 4),
        "netted_share_of_turnover": round(netted / turnover, 4) if turnover > 0 else None,
        # What reaches the market: in a net book only a symbol's net change at an instant is an order.
        "market_turnover_in_1r_money": round(turnover - netted, 4),
        "fill_cost_saved_r": round(netted * _fill_cost_fraction(), 4),
        "resize_orders": {"count": len(resizes), "turnover_in_1r_money": round(sum(resizes), 4)},
        "order_sizes_usdt": {
            "strategy_slice_at_entry": _order_sizes([a for e in events for a in e["entries"]]),
            "symbol_net_order": _order_sizes([e["net"] for e in events if e["net"] > _EPS]),
        },
    }


def measure(positions: list[dict[str, Any]]) -> dict[str, Any]:
    kept, set_aside = one_holder_per_strategy(positions)
    symbols = sorted({p["symbol"] for p in kept})
    out: dict[str, Any] = {
        "positions": len(kept), "strategies": len({p["who"] for p in kept}),
        "rows_set_aside_as_one_strategys_second_position": set_aside,
        "span": ([timeutil.format_iso(datetime.fromtimestamp(min(p["open"] for p in kept), tz=timezone.utc)),
                  timeutil.format_iso(datetime.fromtimestamp(max(p["close"] for p in kept), tz=timezone.utc))]
                 if kept else None),
        "notional_per_1r": _spread([p["per_r"] for p in kept]),
    }
    for variant in VARIANTS:
        walks = {s: symbol_walk([p for p in kept if p["symbol"] == s], variant) for s in symbols}
        out[variant] = {"by_symbol": {s: _summary([walks[s]]) for s in symbols},
                        "all_symbols": _summary(list(walks.values()))}
    return out


def report(positions: list[dict[str, Any]]) -> dict[str, Any]:
    """Everything with the seeded rows, and again without them."""
    return {"with_seeded": measure(positions), "without_seeded": measure([p for p in positions if not p["seeded"]]),
            "seeded_positions": sum(1 for p in positions if p["seeded"]), "display_only": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pool_netting", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("run",))
    parser.add_argument("--lake", type=Path, default=None, help="default: $RESEARCH_LAKE")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    lake = args.lake or (Path(os.environ["RESEARCH_LAKE"]) if os.environ.get("RESEARCH_LAKE") else None)
    path = lake / SOURCE if lake else None
    if path is None or not path.is_file():
        print(f"BLOCKED: no {SOURCE} in the lake snapshot", file=sys.stderr)
        return EXIT_BLOCKED
    positions, rows = read(path.read_text(encoding="utf-8").splitlines())
    if not positions:
        print(f"BLOCKED: {SOURCE} holds no readable position", file=sys.stderr)
        return EXIT_BLOCKED
    print(json.dumps({"measured_at": timeutil.utc_now_iso(), "spec": SPEC, "source": SOURCE,
                      "lake_snapshot": lake.resolve().name, "fields_read": list(ALLOWED), "rows": rows,
                      "closed_positions_only": True, "variants": list(VARIANTS),
                      "assumed_equities_usdt": list(ASSUMED_EQUITIES), "order_limit_usdt": ORDER_LIMIT_USDT,
                      "risk_per_trade_fraction": RISK_PER_TRADE_FRACTION,
                      "fill_cost_fraction": _fill_cost_fraction(), **report(positions)},
                     indent=1, sort_keys=True))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
