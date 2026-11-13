"""Signal lab: where, if anywhere, a variable ranks next week's returns — by scenario, liquidity tier and horizon.

``docs/proposals/STRATEGY_RESEARCH_REBUILD_V0.1.md`` (Thomas 2026-11-13). The tools before this one measured one
thing: a finished basket's net Sharpe. That folds the signal, the portfolio and the costs into a single number, so a
weak signal reads as a failure and nothing says in which market state, which coins or which holding period a variable
works. This tool measures the layer underneath, before any strategy exists.

- **A cell** is one variable, one liquidity tier, one horizon and one scenario. Its reading is the rank correlation
  between the variable at a weekly decision and the return over the horizon that follows, averaged over the
  decisions in that scenario, with its t value, the number of decisions and the number of separate spells.
- **The return is the one a book would hold:** from the open after the decision to the open ``horizon`` days later,
  counted only for a coin that traded on the decision day and on both of those days. Before costs.
- **Scenarios are read at the decision, from the past only:** the market trend (the regime label of
  ``regime_strategies``), Bitcoin's volatility, the dispersion of the coins' returns and the level of funding, each
  against its own trailing median; and trend crossed with volatility.
- **Chance is measured, not assumed.** Random variables go through the same grid — some redrawn every week, some
  fixed per coin like a slow characteristic. A real cell is read against what those produce.
- **The span is development only** (:data:`SPAN`). The market is cut at its last day and a decision whose return
  would reach past it is dropped, so nothing here reads 2025 or later.
- **No verdict.** A cell that meets :func:`candidates`' rule is a lead to write up as a hypothesis, at most
  :data:`CANDIDATE_CAP` of them; it is not a strategy and not evidence that one exists.

``run`` is sandbox-only and prints the whole map as JSON.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts import binance_archive as ba  # noqa: E402
from scripts.research import basket_search as bs  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import depth_basket_search as ds  # noqa: E402
from scripts.research import forward_panel as fp  # noqa: E402
from scripts.research import metrics_basket_search as ms  # noqa: E402
from scripts.research import regime_strategies as rg  # noqa: E402
from scripts.research import spot_share_search as sp  # noqa: E402

SPEC = "STRATEGY_RESEARCH_REBUILD_V0.1"
SPAN = bs.DEV                              # 2020-01-01 .. 2024-12-31; nothing later is read
HORIZONS = (7, 28)                         # days held; 28 is read on every fourth decision so windows do not overlap
TIERS = {"top30": (0, 30), "rank31_100": (30, 100)}    # by the month's trailing volume, highest first
LOOKBACK = 30
MIN_NAMES = 8                              # coins a decision needs before its cross-section is ranked
MIN_DECISIONS = 20                         # decisions a cell needs before it shows a number
MIN_SPELLS = 4                             # separate stretches a scenario cell needs: one long bull run is one sample
REFERENCE_DECISIONS = 52                   # trailing decisions a scenario's own median is taken over
REFERENCE_MIN = 26
CONTROLS_WEEKLY = 20                       # random variables redrawn at every decision
CONTROLS_FIXED = 20                        # random variables fixed per coin, like a slow characteristic
CANDIDATE_CAP = 3
MIN_PERSISTENCE = 0.5                      # week-to-week rank correlation a lead needs: one-day reversal died on cost
REFERENCE = bs.REFERENCE

Variable = Callable[[xsb.Market, str, date], "float | None"]


def _with_lookback(feature: bs.Feature, lookback: int = LOOKBACK) -> Variable:
    return lambda market, symbol, day: feature(market, symbol, day, lookback)


def _control(kind: str, k: int) -> Variable:
    def value(market: xsb.Market, symbol: str, day: date) -> float:
        stamp = day if kind == "week" else date(2000, 1, 1)
        return int(xsb.random_key(f"{SPEC}|{kind}|{k}", stamp, symbol)[:12], 16) / 16 ** 12
    return value


def variables() -> dict[str, tuple[str, Variable]]:
    """``{name: (group, function)}`` — every variable the data on disk supports, each at a stated lookback."""
    out: dict[str, tuple[str, Variable]] = {}
    for name, feature in bs.FEATURES.items():
        out[f"{name}_{LOOKBACK}"] = ("price_volume", _with_lookback(feature))
    for lookback in (7, 30, 90):
        out[f"momentum_{lookback}"] = ("price_volume", _with_lookback(xsb.trailing_return, lookback))
    for lookback in (7, 30):
        out[f"funding_{lookback}"] = ("funding", _with_lookback(xsb.trailing_funding, lookback))
    for name, feature in ms.FEATURES.items():
        out[f"{name}_{LOOKBACK}"] = ("positioning", _with_lookback(feature))
    for name, feature in ds.FEATURES.items():
        out[f"{name}_{LOOKBACK}"] = ("depth", _with_lookback(feature))
    for name, feature in sp.FEATURES.items():
        out[f"{name}_{LOOKBACK}"] = ("spot", _with_lookback(feature))
    return out


def controls() -> dict[str, tuple[str, Variable]]:
    out = {f"control_week_{k:02d}": ("control", _control("week", k)) for k in range(CONTROLS_WEEKLY)}
    out.update({f"control_fixed_{k:02d}": ("control", _control("fixed", k)) for k in range(CONTROLS_FIXED)})
    return out


# --- the market, cut ---------------------------------------------------------------------------------------------

def cut_market(market: xsb.Market, span: tuple[date, date] = SPAN) -> xsb.Market:
    """``market`` with no bar, settlement or attached series after the span's last day."""
    held = bs.cut(market, span[1])
    for name in ("metrics", "depth", "spot"):
        series = getattr(market, name, None)
        if series is not None:
            setattr(held, name, {s: {d: v for d, v in days.items() if d <= span[1]} for s, days in series.items()})
    for name in ("spot_of", "spot_files"):
        if hasattr(market, name):
            setattr(held, name, getattr(market, name))
    return held


def last_day(market: xsb.Market) -> date | None:
    days = [max(bars) for bars in market.bars.values() if bars]
    for name in ("metrics", "depth", "spot"):
        days += [max(v) for v in (getattr(market, name, None) or {}).values() if v]
    return max(days) if days else None


# --- decisions, returns, scenarios -------------------------------------------------------------------------------

def decisions(span: tuple[date, date], horizon: int) -> list[date]:
    """The weekly decision days whose whole forward window lies inside the span; every fourth for a 28-day horizon."""
    days = [d for d in xsb.held_days(span[0], span[1])
            if xsb.is_rebalance_day(bs.CADENCE, d) and d + timedelta(days=1 + horizon) <= span[1]]
    return days[::max(1, horizon // 7)]


def forward_return(market: xsb.Market, symbol: str, day: date, horizon: int) -> float | None:
    """From the open after ``day`` to the open ``horizon`` days later; None unless the coin traded on ``day`` and on
    both of those days (a frozen bar that outlives trading is not a return)."""
    bars = market.bars.get(symbol) or {}
    now, start, end = bars.get(day), bars.get(day + timedelta(days=1)), bars.get(day + timedelta(days=1 + horizon))
    if any(b is None or not b.quote_volume or b.quote_volume <= 0 for b in (now, start, end)):
        return None
    return end.open / start.open - 1 if start.open > 0 else None


def _median_split(value: float | None, history: Sequence[float], high: str, low: str) -> str | None:
    if value is None or len(history) < REFERENCE_MIN:
        return None
    return high if value > statistics.median(history) else low


def scenario_labels(market: xsb.Market, monthly: Mapping[date, Sequence[str]], days: Sequence[date],
                    span: tuple[date, date] = SPAN) -> dict[date, dict[str, str]]:
    """``{decision: {axis: label}}``, each label read from that day and earlier. ``monthly`` is the top tier."""
    trend = rg.regime_labels(rg.Data(market, monthly), end=span[1])["labels"]
    btc = market.bars.get(REFERENCE) or {}

    def btc_vol(day: date) -> float | None:
        closes = [btc.get(day - timedelta(days=k)) for k in range(LOOKBACK + 1)]
        if any(b is None for b in closes):
            return None
        returns = [closes[k].close / closes[k + 1].close - 1 for k in range(LOOKBACK)]
        return statistics.pstdev(returns)

    def members(day: date) -> Sequence[str]:
        return monthly.get(date(day.year, day.month, 1), ())

    def dispersion(day: date) -> float | None:
        values = [v for v in (xsb.trailing_return(market, s, day, 7) for s in members(day)) if v is not None]
        return statistics.pstdev(values) if len(values) >= MIN_NAMES else None

    def heat(day: date) -> float | None:
        values = [v for v in (xsb.trailing_funding(market, s, day, 7) for s in members(day)) if v is not None]
        return statistics.median(values) if len(values) >= MIN_NAMES else None

    # Each axis is compared with its own values at the earlier weekly decisions, never with a later one.
    weekly = [d for d in xsb.held_days(span[0], span[1]) if xsb.is_rebalance_day(bs.CADENCE, d)]
    axes = {"vol": (btc_vol, "high", "low"), "dispersion": (dispersion, "wide", "narrow"), "heat": (heat, "hot", "cool")}
    series = {axis: {d: read(d) for d in weekly} for axis, (read, _, _) in axes.items()}
    out: dict[date, dict[str, str]] = {}
    for day in days:
        labels: dict[str, str] = {}
        if trend.get(day) is not None:
            labels["trend"] = str(trend[day]).lower()
        for axis, (_, high, low) in axes.items():
            earlier = [series[axis][d] for d in weekly if d < day and series[axis][d] is not None][-REFERENCE_DECISIONS:]
            label = _median_split(series[axis].get(day), earlier, high, low)
            if label is not None:
                labels[axis] = label
        out[day] = labels
    return out


def scenario_keys(labels: Mapping[str, str]) -> list[str]:
    keys = ["all"] + [f"{axis}={labels[axis]}" for axis in ("trend", "vol", "dispersion", "heat") if axis in labels]
    if "trend" in labels and "vol" in labels:
        keys.append(f"trend={labels['trend']}|vol={labels['vol']}")
    return keys


# --- one cross-section -------------------------------------------------------------------------------------------

def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    out, i = [0.0] * len(values), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def rank_correlation(pairs: Sequence[tuple[float, float]]) -> float | None:
    """Spearman's rank correlation, average ranks for ties; None with no spread on a side."""
    a, b = _ranks([p[0] for p in pairs]), _ranks([p[1] for p in pairs])
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def tercile_spread(pairs: Sequence[tuple[float, float]]) -> float:
    """Mean forward return of the top third by the variable minus the bottom third's."""
    ordered = sorted(pairs, key=lambda p: p[0])
    k = max(1, len(ordered) // 3)
    return statistics.fmean(r for _, r in ordered[-k:]) - statistics.fmean(r for _, r in ordered[:k])


def spells(indices: Sequence[int]) -> int:
    """How many separate stretches of consecutive decisions ``indices`` (positions in the decision list) make."""
    ordered = sorted(indices)
    return sum(1 for i, value in enumerate(ordered) if i == 0 or value != ordered[i - 1] + 1)


def cell(samples: Sequence[tuple[int, float, float]], *, scenario: str) -> dict[str, Any]:
    """One cell from its decisions' ``(position, rank correlation, tercile spread)``."""
    n, runs = len(samples), spells([s[0] for s in samples])
    if n < MIN_DECISIONS or (scenario != "all" and runs < MIN_SPELLS):
        return {"decisions": n, "spells": runs, "too_few": True}
    ics = [s[1] for s in samples]
    spread = statistics.fmean(s[2] for s in samples)
    sd = statistics.stdev(ics)
    return {"decisions": n, "spells": runs, "too_few": False,
            "mean_rank_correlation": round(statistics.fmean(ics), 4),
            "t": round(statistics.fmean(ics) / (sd / math.sqrt(n)), 2) if sd > 0 else None,
            "positive_share": round(sum(1 for v in ics if v > 0) / n, 3),
            "tercile_spread_bps": round(spread * 10_000, 1)}


# --- the grid ----------------------------------------------------------------------------------------------------

def tier_members(market: xsb.Market, span: tuple[date, date]) -> dict[str, dict[date, list[str]]]:
    widest = max(hi for _, hi in TIERS.values())
    monthly = xsb.universes(market, top_n=widest, start=date(span[0].year, span[0].month, 1) - timedelta(days=1),
                            end=span[1])
    return {tier: {date(m.year, m.month, 1): members[lo:hi] for m, members in monthly.items()}
            for tier, (lo, hi) in TIERS.items()}


def grid(market: xsb.Market, names: Mapping[str, tuple[str, Variable]], span: tuple[date, date] = SPAN) -> dict[str, Any]:
    """Every cell of ``names`` over the span, with coverage, week-to-week rank persistence and what was dropped.
    ``market`` must already be cut at the span's last day."""
    latest = last_day(market)
    if latest is not None and latest > span[1]:
        raise ValueError(f"the market reaches {latest}, past the span's last day {span[1]}; cut it first")
    tiers = tier_members(market, span)
    samples: dict[tuple[str, str, int, str], list[tuple[int, float, float]]] = {}
    coverage: dict[tuple[str, str], list[int]] = {}
    persistence: dict[tuple[str, str], list[float]] = {}
    dropped = {"untraded_returns": 0, "returns_asked": 0}
    for horizon in HORIZONS:
        days = decisions(span, horizon)
        labels = scenario_labels(market, tiers["top30"], days, span)
        for tier, monthly in tiers.items():
            previous: dict[str, dict[str, float]] = {}
            for position, day in enumerate(days):
                members = monthly.get(date(day.year, day.month, 1), ())
                returns = {}
                for symbol in members:
                    dropped["returns_asked"] += 1
                    value = forward_return(market, symbol, day, horizon)
                    if value is None:
                        dropped["untraded_returns"] += 1
                    else:
                        returns[symbol] = value
                keys = scenario_keys(labels[day])
                for name, (_, read) in names.items():
                    values = {s: v for s in returns if (v := read(market, s, day)) is not None}
                    if horizon == HORIZONS[0]:
                        coverage.setdefault((name, tier), []).append(len(values))
                        before = previous.get(name)
                        if before:
                            shared = [(before[s], values[s]) for s in values if s in before]
                            if len(shared) >= MIN_NAMES and (rho := rank_correlation(shared)) is not None:
                                persistence.setdefault((name, tier), []).append(rho)
                        previous[name] = values
                    if len(values) < MIN_NAMES:
                        continue
                    pairs = [(values[s], returns[s]) for s in values]
                    ic = rank_correlation(pairs)
                    if ic is None:
                        continue
                    sample = (position, ic, tercile_spread(pairs))
                    for key in keys:
                        samples.setdefault((name, tier, horizon, key), []).append(sample)
    cells = [{"variable": name, "group": names[name][0], "tier": tier, "horizon_days": horizon, "scenario": key,
              **cell(rows, scenario=key)}
             for (name, tier, horizon, key), rows in sorted(samples.items())]
    summary = {f"{name}|{tier}": {"mean_names_ranked": round(statistics.fmean(counts), 1),
                                  "share_of_decisions_ranked": round(sum(1 for c in counts if c >= MIN_NAMES) / len(counts), 3),
                                  "rank_persistence": (round(statistics.fmean(persistence[(name, tier)]), 3)
                                                       if persistence.get((name, tier)) else None)}
               for (name, tier), counts in sorted(coverage.items())}
    return {"cells": cells, "variables": summary, "dropped": dropped}


# --- reading the grid against chance -----------------------------------------------------------------------------

def _shown(cells: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [c for c in cells if not c["too_few"] and c.get("t") is not None]


def chance(control_cells: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """What the random variables produced: per horizon, the largest |t| anywhere in the grid and how often |t| > 2."""
    out = {}
    for horizon in HORIZONS:
        ts = sorted(abs(c["t"]) for c in _shown(control_cells) if c["horizon_days"] == horizon)
        out[str(horizon)] = {"cells": len(ts), "largest_abs_t": ts[-1] if ts else None,
                             "p95_abs_t": ts[int(0.95 * (len(ts) - 1))] if ts else None,
                             "share_abs_t_over_2": round(sum(1 for t in ts if t > 2) / len(ts), 4) if ts else None}
    return out


def candidates(real: Mapping[str, Any], against: Mapping[str, Any]) -> dict[str, Any]:
    """The leads, by the rule fixed before the run: a shown cell whose |t| is above the largest any random variable
    reached at that horizon, whose variable keeps its rank from week to week, and whose sign agrees with the same
    variable's all-weeks cell in its tier. At most :data:`CANDIDATE_CAP`, largest |t| first."""
    whole = {(c["variable"], c["tier"], c["horizon_days"]): c for c in _shown(real["cells"]) if c["scenario"] == "all"}
    found = []
    for c in _shown(real["cells"]):
        bar = against[str(c["horizon_days"])]["largest_abs_t"]
        steady = real["variables"].get(f"{c['variable']}|{c['tier']}", {}).get("rank_persistence")
        base = whole.get((c["variable"], c["tier"], c["horizon_days"]))
        if bar is None or abs(c["t"]) <= bar or steady is None or steady < MIN_PERSISTENCE:
            continue
        if base is None or (base["mean_rank_correlation"] > 0) != (c["mean_rank_correlation"] > 0):
            continue
        found.append(c)
    found.sort(key=lambda c: (-abs(c["t"]), c["variable"], c["tier"], c["scenario"]))
    return {"rule": "abs(t) above the random variables' largest at that horizon; rank persistence at least "
                    f"{MIN_PERSISTENCE}; same sign as the variable's all-weeks cell in its tier",
            "cap": CANDIDATE_CAP, "met_the_rule": len(found), "leads": found[:CANDIDATE_CAP]}


def excess(real_cells: Sequence[Mapping[str, Any]], against: Mapping[str, Any]) -> dict[str, Any]:
    """How many real cells show |t| > 2 beside how many the random variables' rate would give."""
    out = {}
    for horizon in HORIZONS:
        shown = [c for c in _shown(real_cells) if c["horizon_days"] == horizon]
        rate = against[str(horizon)]["share_abs_t_over_2"]
        out[str(horizon)] = {"cells": len(shown), "abs_t_over_2": sum(1 for c in shown if abs(c["t"]) > 2),
                             "expected_from_random": round(rate * len(shown), 1) if rate is not None else None}
    return out


# --- the baseline Thomas approved: the plain pool of the registered definitions ------------------------------------

def pool(market: xsb.Market, span: tuple[date, date] = SPAN, *, simulate: Callable[..., Any] = xsb.simulate) -> dict[str, Any]:
    """Every registered definition as registered and their equal-weight pool over the span, net of the engine's
    costs (the forward panel's reading, on development years). A member without a basket on a day counts as zero."""
    start, end = span
    monthly = xsb.universes(market, top_n=bs.TOP_N, start=date(start.year, start.month, 1) - timedelta(days=1), end=end)
    monthly = {date(m.year, m.month, 1): v for m, v in monthly.items()}
    rows, daily = {}, {}
    for name, candidate, features in fp.members():
        sim = simulate(market, bs.CADENCE, monthly, start=start, end=end, weigher=bs.weigher(candidate, features))
        sharpe, values = bs.annual_sharpe(sim["daily"], start, end)
        rows[name] = {"annual_sharpe": sharpe, "sum_pct": round(sum(values) * 100, 2), "months": len(values)}
        daily[name] = {day: row["net"] for day, row in sim["daily"].items()}
    days = sorted({day for series in daily.values() for day in series})
    pooled = {day: {"net": sum(series.get(day, 0.0) for series in daily.values()) / len(daily)} for day in days}
    sharpe, values = bs.annual_sharpe(pooled, start, end)
    report = {"annual_sharpe": sharpe, "sum_pct": round(sum(values) * 100, 2), "months": len(values)}
    report["total_position_annual_pct"] = bs.total_position_annual_pct(report["sum_pct"], report["months"])
    report["clears_entry"] = bs.clears_entry(report)
    report["years_pct"] = {str(y): round(sum(r["net"] for d, r in pooled.items() if d.year == y) * 100, 2)
                           for y in sorted({d.year for d in pooled})}
    return {"members": rows, "pool": report, "rule": "equal weight of every registered definition; no basket counts as zero"}


# --- the host side -----------------------------------------------------------------------------------------------

def hold(cache: Path, span: tuple[date, date] = SPAN) -> xsb.Market | None:
    """The market cut at the span's last day with positioning, depth and spot attached, or None when a piece is missing."""
    archive = cache / "archive"
    snapshot = xsb.read_contract_types(archive)
    try:
        listing = set(json.loads((cache / sp.SPOT_DIR / ba.SYMBOLS_FILENAME).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        listing = set()
    if snapshot is None or not listing:
        return None
    market = xsb.load_market(archive, funding=True)
    market.contract_types = snapshot[0]
    held = ds.cut(market, ds.load_daily(cache), span[1], ms.load_daily(cache))
    held = sp.attach(held, xsb.load_market(cache / sp.SPOT_DIR, funding=False), listing, span[1], sp.months_on_disk(cache))
    return cut_market(held, span)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="signal_lab", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("list", "run"))
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    args = parser.parse_args(argv)
    if args.command == "list":
        print(json.dumps({"spec": SPEC, "span": [d.isoformat() for d in SPAN], "horizons": list(HORIZONS),
                          "tiers": {k: list(v) for k, v in TIERS.items()},
                          "variables": {name: group for name, (group, _) in variables().items()},
                          "controls": len(controls())}, indent=1))
        return EXIT_OK
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    bs.check_development_span(SPAN)
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    market = hold(cache)
    if market is None:
        print("BLOCKED: contract types or the spot symbol list is missing from the cache", file=sys.stderr)
        return EXIT_BLOCKED
    real, random = grid(market, variables()), grid(market, controls())
    against = chance(random["cells"])
    print(json.dumps({"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC,
                      "span": [d.isoformat() for d in SPAN], "last_day_read": last_day(market).isoformat(),
                      "before_costs": True, "judgement": None,
                      "chance": against, "excess": excess(real["cells"], against),
                      "candidates": candidates(real, against), "dropped": real["dropped"],
                      "variables": real["variables"], "cells": real["cells"],
                      "baseline_pool": pool(market)},
                     indent=1, sort_keys=True, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
