"""Metrics basket diagnostic: where the recorded sum of M04 came from, leg by leg.

``docs/proposals/METRICS_BASKET_SEARCH_V0.1.md`` Q5 (Thomas 2026-11-11: "진단 진행"). Display only: the verdict
(CONFIRM_PASS, annual Sharpe 1.357) and M04's definition do not move with anything printed here.

- **What it splits:** the engine's own daily rows (``cross_section_basket.simulate`` with ``basket_search``'s weigher)
  into one row per held coin and day: price, funding, cost. The baskets are the engine's: a wrapper records each
  rebalance's targets and nothing else decides a weight.
- **Reconciliation first:** every day's price, cost, funding and net must add back to the engine's row, and the
  span's totals to the recorded report (:data:`RECORDED`). If they do not, nothing is printed but that fact.
- **What it never prints:** a Sharpe, a pass field, or any what-if (without funding, with a cap, without a coin).
  A number comparable to the entry floor under a changed definition would be a second judgement.
- **One candidate, two spans:** M04 on its development span, as often as needed, and on the locked span once
  (:data:`LOCKED_READ`). There is no candidate flag and no parameter flag.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import basket_search as bs  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import metrics_basket_search as ms  # noqa: E402

SPEC = ms.SPEC
CANDIDATE = "M04"
SPANS = {"dev": ms.DEV_LONG, "confirm": ms.CONFIRM}
# The reports the split must add back to (``~/research/metrics-search/M04.json`` and ``M04-confirm.json``).
RECORDED = {
    "dev": {"sum_pct": 144.02, "funding_pct": 2.8, "cost_pct": 19.45, "forced_closes": 16},
    "confirm": {"sum_pct": 163.85, "funding_pct": 78.58, "cost_pct": 11.94, "forced_closes": 13},
}
# The run that read the locked span for this diagnostic. Once set, ``--span confirm`` is refused: Q5 was one read.
LOCKED_READ: str | None = None
TOLERANCE = 1e-9
NEAR_LAST_BAR = (7, 14)                  # days before a coin's last bar, for a coin whose bars end inside the span
TOP_COINS = (1, 3, 5)
TOP_WEEKS = 5
PARTS = ("price", "funding", "cost")


class DoesNotReconcile(Exception):
    """The split does not add back to the engine's rows or to the recorded report; names only, no values."""


def settlement_class(count: int) -> str:
    return "0-2" if count < 3 else "3" if count == 3 else "4-8" if count <= 8 else "9+"


def attribute(market: xsb.Market, candidate: bs.Candidate, span: tuple[date, date],
              features: Mapping[str, bs.Feature] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """``(legs, sim)``: one row per held coin and day (and per coin traded out that day), and the engine's run.

    Raises :class:`DoesNotReconcile` unless every day's rows add back to the engine's daily row."""
    start, end = span
    monthly = xsb.universes(market, top_n=bs.TOP_N, start=date(start.year, start.month, 1) - timedelta(days=1), end=end)
    monthly = {date(m.year, m.month, 1): v for m, v in monthly.items()}
    inner, targets = bs.weigher(candidate, features), {}

    def recording(mkt, members, day, signal, *, seed=None):
        out = inner(mkt, members, day, signal, seed=seed)
        targets[day] = dict(out)
        return out

    sim = xsb.simulate(market, bs.CADENCE, monthly, start=start, end=end, weigher=recording)
    unit = (xsb.cost.DEFAULT_TAKER_FEE_BPS + xsb.cost.DEFAULT_SLIPPAGE_BPS) / 10_000
    settlements = {s: Counter(xsb._utc_day(t) for t, _ in rows) for s, rows in market.funding.items()}
    last_bar = {s: max(bars) for s, bars in market.bars.items() if bars}
    weights: dict[str, float] = {}
    legs: list[dict[str, Any]] = []
    seen: set[date] = set()
    for day in xsb.held_days(start, end):
        decision = day - timedelta(days=1)
        traded: dict[str, float] = {}
        if xsb.is_rebalance_day(bs.CADENCE, decision):
            target = targets[decision]
            for symbol in set(weights) | set(target):
                traded[symbol] = abs(target.get(symbol, 0.0) - weights.get(symbol, 0.0)) * unit
            weights = dict(target)
        if not weights:
            continue                                   # the engine books nothing on a day it holds nothing
        rows: dict[str, dict[str, Any]] = {}
        closing: list[str] = []
        for symbol, w in weights.items():
            today = market.bars[symbol].get(day)
            if today is None:
                closing.append(symbol)                 # no bar today: the leg ended before it; nothing earned
                continue
            tomorrow = market.bars[symbol].get(day + timedelta(days=1))
            row = {"day": day, "symbol": symbol, "weight": w, "cost": 0.0, "forced": tomorrow is None,
                   "price": w * ((today.close if tomorrow is None else tomorrow.open) / today.open - 1),
                   "funding": -w * xsb.funding_on(market, symbol, day)[0],
                   "settlements": settlements.get(symbol, Counter())[day],
                   "days_to_last_bar": (last_bar[symbol] - day).days if last_bar[symbol] < end else None}
            if tomorrow is None:
                row["cost"] += abs(w) * unit
                closing.append(symbol)
            rows[symbol] = row
        for symbol, paid in traded.items():
            if paid:
                rows.setdefault(symbol, {"day": day, "symbol": symbol, "weight": 0.0, "cost": 0.0, "forced": False,
                                         "price": 0.0, "funding": 0.0, "settlements": 0, "days_to_last_bar": None})
                rows[symbol]["cost"] += paid
        for symbol in closing:
            weights.pop(symbol)
        engine = sim["daily"].get(day)
        mine = {"gross": sum(r["price"] for r in rows.values()), "cost": sum(r["cost"] for r in rows.values()),
                "funding": sum(r["funding"] for r in rows.values())}
        mine["net"] = mine["gross"] - mine["cost"] + mine["funding"]
        if engine is None or any(abs(mine[k] - engine[k]) > TOLERANCE for k in mine):
            raise DoesNotReconcile(f"day {day}: " + ("no engine row" if engine is None else
                                   ",".join(k for k in mine if abs(mine[k] - engine[k]) > TOLERANCE)))
        seen.add(day)
        legs.extend(rows.values())
    if seen != set(sim["daily"]):
        raise DoesNotReconcile(f"{len(set(sim['daily']) - seen)} engine day(s) without a split")
    return legs, sim


def check_recorded(sim: Mapping[str, Any], span: tuple[date, date], recorded: Mapping[str, Any]) -> None:
    """Raise unless the engine's run is the one on record (the same rounding ``basket_search.measure`` prints)."""
    daily = sim["daily"]
    got = {"sum_pct": round(sum(bs.annual_sharpe(daily, *span)[1]) * 100, 2),
           "funding_pct": round(sum(r["funding"] for r in daily.values()) * 100, 2),
           "cost_pct": round(sum(r["cost"] for r in daily.values()) * 100, 2),
           "forced_closes": sim["forced_closes"]["count"]}
    wrong = [k for k in recorded if got[k] != recorded[k]]
    if wrong:
        raise DoesNotReconcile("not the recorded run: " + ",".join(wrong))


def _pct(value: float) -> float:
    return round(value * 100, 2)


def _parts(rows: list[dict[str, Any]]) -> dict[str, float]:
    out = {k: _pct(sum(r[k] for r in rows)) for k in PARTS}
    out["net"] = _pct(sum(r["price"] + r["funding"] - r["cost"] for r in rows))
    return out


def summarise(legs: list[dict[str, Any]], sim: Mapping[str, Any], end: date) -> dict[str, Any]:
    """The fixed cuts of the proposal's "진단" section; sums of the recorded total only."""
    held = [r for r in legs if r["weight"]]
    forced = [r for r in held if r["forced"]]
    by_month, by_coin, by_week = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in legs:
        by_month[r["day"].strftime("%Y-%m")].append(r)
        by_coin[r["symbol"]].append(r)
        by_week[xsb.week_of(r["day"])].append(r)
    total_funding = sum(r["funding"] for r in legs)
    coin_funding = sorted(((sum(r["funding"] for r in rows), s) for s, rows in by_coin.items()), reverse=True)
    week_funding = sorted(((sum(r["funding"] for r in rows), w) for w, rows in by_week.items()), reverse=True)
    share = lambda value: round(value / total_funding, 3) if total_funding else None  # noqa: E731
    coins = [{"symbol": s, **_parts(rows), "long_days": sum(1 for r in rows if r["weight"] > 0),
              "short_days": sum(1 for r in rows if r["weight"] < 0)} for s, rows in by_coin.items()]
    near = {}
    for limit in NEAR_LAST_BAR:
        rows = [r for r in held if r["days_to_last_bar"] is not None and r["days_to_last_bar"] <= limit]
        near[f"within_{limit}_days"] = {"leg_days": len(rows), **_parts(rows)}
    by_class = defaultdict(list)
    for r in held:
        by_class[settlement_class(r["settlements"])].append(r)
    return {
        "totals_pct": _parts(legs),
        "by_month_pct": {m: _parts(rows) for m, rows in sorted(by_month.items())},
        "by_coin_pct": sorted(coins, key=lambda c: (-abs(c["funding"]), c["symbol"])),
        "funding_concentration": {
            "coins_with_a_leg": len({r["symbol"] for r in held}),
            "received_pct": _pct(sum(v for v, _ in coin_funding if v > 0)),
            "paid_pct": _pct(sum(v for v, _ in coin_funding if v < 0)),
            "top_coins_share_of_net_funding": {str(k): share(sum(v for v, _ in coin_funding[:k])) for k in TOP_COINS},
            "top_weeks_share_of_net_funding": share(sum(v for v, _ in week_funding[:TOP_WEEKS])),
            "top_weeks": [{"week": w.isoformat(), **_parts(by_week[w])} for _, w in week_funding[:TOP_WEEKS]],
            "weeks_with_a_leg": len(by_week),
        },
        "by_side_pct": {side: _parts([r for r in held if (r["weight"] > 0) == (side == "long")])
                        for side in ("long", "short")},
        "by_settlements_per_day": {k: {"leg_days": len(rows), **_parts(rows)} for k, rows in sorted(by_class.items())},
        # the engine counts every leg whose next bar is missing: a leg still held on the span's last day, a hole in a
        # coin's bars, and a coin whose bars end inside the span. Only the last is a delisting.
        "near_last_bar": {**near, "forced_closes": {
            "count": sim["forced_closes"]["count"], "net_pct": _pct(sim["forced_closes"]["net"]),
            "on_the_spans_last_day": sum(1 for r in forced if r["day"] == end),
            "a_hole_in_the_bars": sum(1 for r in forced if r["day"] != end and r["days_to_last_bar"] is None),
            "bars_end_inside_the_span": sum(1 for r in forced if r["day"] != end and r["days_to_last_bar"] is not None)}},
        "leg_days": len(held),
    }


def diagnose(market: xsb.Market, candidate: bs.Candidate, span: tuple[date, date],
             features: Mapping[str, bs.Feature] | None = None, *,
             recorded: Mapping[str, Any] | None = None) -> dict[str, Any]:
    legs, sim = attribute(market, candidate, span, features)
    if recorded is not None:
        check_recorded(sim, span, recorded)
    return {"span": [d.isoformat() for d in span], "reconciled_days": len(sim["daily"]), **summarise(legs, sim, span[1])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="metrics_basket_diagnostic", description=__doc__.splitlines()[0])
    parser.add_argument("--span", choices=tuple(SPANS), required=True)
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    args = parser.parse_args(argv)
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    if args.span == "confirm" and LOCKED_READ:
        print(f"refused: the diagnostic read the locked span once ({LOCKED_READ})", file=sys.stderr)
        return EXIT_USAGE
    archive = cache / "archive"
    snapshot = xsb.read_contract_types(archive)
    daily = ms.load_daily(cache)
    if snapshot is None or not daily:
        print(f"BLOCKED: no contract types under {archive} or no derived metrics under {cache}", file=sys.stderr)
        return EXIT_BLOCKED
    span = SPANS[args.span]
    market = xsb.load_market(archive)
    market.contract_types = snapshot[0]
    market = ms.cut(market, daily, span[1])
    try:
        body = diagnose(market, ms.CANDIDATES[CANDIDATE], span, ms.FEATURES, recorded=RECORDED[args.span])
    except DoesNotReconcile as exc:
        print(f"BLOCKED: the split does not reconcile ({exc}); nothing is shown", file=sys.stderr)
        return EXIT_BLOCKED
    print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC, "candidate": CANDIDATE,
                      "display_only": True, "which_span": args.span, **body}, indent=1, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
