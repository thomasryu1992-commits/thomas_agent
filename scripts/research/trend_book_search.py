"""Trend book search: does following each coin's own trend add anything over simply holding the coins?

``docs/proposals/FLOW_AND_TREND_SEARCH_V0.1.md`` part B (Thomas 2026-11-11: "순서대로 전부"). Trend rules on this
universe were measured before and none was confirmed (the regime sets' breakouts and high-proximity rules, the
S-MOM30 legs). What was not measured is the always-on form: every member long or short by the sign of its own
trailing return, sized by inverse volatility.

- **What is judged is the excess, not the trend book.** A book that is long whatever rises was long through
  2020–2021, so its Sharpe is partly the market's. The judged book is *trend minus always-long at the same sizing*:
  each member's inverse-volatility share ``v`` (the shares sum to one), long ``+v`` when its trailing return is
  positive and short ``−v`` otherwise, minus ``+v`` always. That leaves ``−2v`` on every member in a downtrend and
  nothing elsewhere, at most two units in total: the same total position as a basket.
- **Definitions (fixed before any run, in this order):** :data:`DEFINITIONS`: trailing 30 days, then 90 days. The
  first to clear the entry rule ends the search. No other lookback, sizing or filter is tried.
- **Span:** ``basket_search.DEV`` (2020-01..2024-12). Nothing later is read.
- **Entry rule:** ``basket_search.clears_entry``, on the judged book.
- **Shown, never judged:** the always-long book and the full trend book over the same span, the seeded random
  twins (the same number of members shorted, drawn at random), and the weekly correlation with S-MOM30, which is
  already on a forward screen.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import basket_search as bs  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import momentum_basket_screen as mbs  # noqa: E402

SPEC = "FLOW_AND_TREND_SEARCH_V0.1"
DEV = bs.DEV
DEFINITIONS = {"T01": 30, "T02": 90}      # trailing days whose sign is the trend; fixed before any run
EXCESS, TREND, ALWAYS_LONG = "excess", "trend", "always_long"
REFERENCE_SIGNAL = "S-MOM30"

Weigher = Callable[..., dict[str, float]]


def shares(market: xsb.Market, members, day: date, lookback: int) -> tuple[dict[str, float], dict[str, float]]:
    """``(inverse-volatility shares summing to one, trailing returns)`` over the members that have both."""
    vols = {s: mbs.trailing_vol(market, s, day) for s in members}
    rets = {s: xsb.trailing_return(market, s, day, lookback) for s in members}
    sized = [s for s in members if vols[s] is not None and rets[s] is not None]
    if len(sized) < xsb.MIN_UNIVERSE:
        return {}, {}
    total = sum(1 / vols[s] for s in sized)
    return {s: (1 / vols[s]) / total for s in sized}, {s: rets[s] for s in sized}


def book(lookback: int, form: str = EXCESS) -> Weigher:
    """A ``cross_section_basket.simulate`` weigher: the judged excess book, or one of the two shown books."""
    def weights(market: xsb.Market, members, day: date, signal: str, *, seed: str | None = None) -> dict[str, float]:
        share, rets = shares(market, members, day, lookback)
        if not share:
            return {}
        down = {s for s in share if rets[s] <= 0}
        if seed is not None:                                # the same number shorted, drawn at random
            down = set(sorted(share, key=lambda s: xsb.random_key(seed, day, s))[:len(down)])
        if form == ALWAYS_LONG:
            return dict(share)
        if form == TREND:
            return {s: (-v if s in down else v) for s, v in share.items()}
        return {s: -2.0 * share[s] for s in sorted(down)}
    return weights


def _weekly_correlation(a: dict[date, float], b: dict[date, float]) -> float | None:
    weeks = sorted(set(a) & set(b))
    if len(weeks) < 8:
        return None
    xs, ys = [a[w] for w in weeks], [b[w] for w in weeks]
    if statistics.stdev(xs) == 0 or statistics.stdev(ys) == 0:
        return None
    return round(statistics.correlation(xs, ys), 3)


def measure(market: xsb.Market, lookback: int, span: tuple[date, date] = DEV) -> dict[str, Any]:
    """One definition over one span, on a market already cut at the span's end."""
    start, end = span
    monthly = xsb.universes(market, top_n=bs.TOP_N, start=date(start.year, start.month, 1) - timedelta(days=1), end=end)
    monthly = {date(m.year, m.month, 1): v for m, v in monthly.items()}
    run = lambda form, **kw: xsb.simulate(market, bs.CADENCE, monthly, start=start, end=end,  # noqa: E731
                                          weigher=book(lookback, form), **kw)
    sim = run(EXCESS)
    sharpe, values = bs.annual_sharpe(sim["daily"], start, end)
    weeks = xsb.weekly(sim["daily"])
    random = [bs.annual_sharpe(run(EXCESS, seed=f"{SPEC}|trend|{k}")["daily"], start, end)[0]
              for k in range(xsb.RANDOM_SEEDS)]
    random = [v for v in random if v is not None]
    body = {
        "span": [start.isoformat(), end.isoformat()], "months": len(values), "annual_sharpe": sharpe,
        "weeks": len(weeks), "mean_bps_per_week": round(statistics.fmean(weeks.values()) * 10_000, 1) if weeks else None,
        "sum_pct": round(sum(values) * 100, 2), "negative_months": sum(1 for v in values if v < 0),
        "years_pct": {str(y): round(sum(r["net"] for d, r in sim["daily"].items() if d.year == y) * 100, 2)
                      for y in sorted({d.year for d in sim["daily"]})},
        "cost_pct": round(sum(r["cost"] for r in sim["daily"].values()) * 100, 2),
        "funding_pct": round(sum(r["funding"] for r in sim["daily"].values()) * 100, 2),
        "random_twins_mean_annual_sharpe": round(statistics.fmean(random), 3) if random else None,
    }
    body["total_position_annual_pct"] = bs.total_position_annual_pct(body["sum_pct"], body["months"])
    body["entry_return_total_pct"] = bs.ENTRY_RETURN_TOTAL_PCT
    body["pass"] = bs.clears_entry(body)
    shown = {}
    for form in (ALWAYS_LONG, TREND):
        other = run(form)
        shown[form] = {"annual_sharpe": bs.annual_sharpe(other["daily"], start, end)[0],
                       "sum_pct": round(sum(r["net"] for r in other["daily"].values()) * 100, 2)}
    reference = xsb.simulate(market, REFERENCE_SIGNAL, monthly, start=start, end=end)
    shown["weekly_correlation_with_" + REFERENCE_SIGNAL] = _weekly_correlation(weeks, xsb.weekly(reference["daily"]))
    return {**body, "shown_only": shown}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trend_book_search", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("list", "explore"))
    parser.add_argument("--definition", choices=tuple(DEFINITIONS), default=None)
    parser.add_argument("--archive", type=Path, default=None, help="default: $RESEARCH_CACHE/archive")
    args = parser.parse_args(argv)
    if args.command == "list":
        print(json.dumps({"development_span": [d.isoformat() for d in DEV], "definitions": DEFINITIONS}, indent=1))
        return EXIT_OK
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    if args.definition is None:
        print(f"refused: name a definition ({', '.join(DEFINITIONS)})", file=sys.stderr)
        return EXIT_USAGE
    try:
        bs.check_development_span(DEV)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_USAGE
    archive = args.archive or xsb._archive_default()
    snapshot = xsb.read_contract_types(archive)
    if snapshot is None:
        print(f"BLOCKED: no readable {xsb.CONTRACT_TYPES_FILENAME} under {archive}", file=sys.stderr)
        return EXIT_BLOCKED
    market = xsb.load_market(archive)
    market.contract_types = snapshot[0]
    market = bs.cut(market, DEV[1])                      # before anything is computed
    report = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC, "definition": args.definition,
              "lookback_days": DEFINITIONS[args.definition], "entry_sharpe": bs.ENTRY_SHARPE,
              **measure(market, DEFINITIONS[args.definition])}
    print(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
