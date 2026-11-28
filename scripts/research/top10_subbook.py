"""Top-10 sub-book of the B-POS and PC screens: the basket's own legs on the month's top-10 coins, a read that only drops.

``docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md`` §153 (Thomas 2026-11-24, fixed before the first window opened) and
§154 (this code). The four screening baskets are top-30 books; the redesign's MVP trades the top 10 only (§145).
B-POS and PC simulate each coin on its own and average the coins' daily returns, so keeping only the top-10 coins is
the same strategy holding fewer coins. S-MOM30 and M04 rank 30 coins into a long and a short side; their top-10 legs
are not a balanced book, so they get no read here.

- **Top 10:** each window month's first ten of the §145 universe, built as ``members-top10.json`` was:
  :func:`cross_section_basket.universe_at` with ``top_n=10`` on the perp archive with its contract types (trailing
  volume, highest first, read before the month starts). A month that ranks fewer than ten coins is ``BLOCKED``.
- **Sub-book:** the basket's legs exactly as its screen simulated them, captured from the screen's own call of
  :func:`funding_basis_screen.summarise` (nothing is simulated again, neither screen is edited). A leg keeps its
  daily return and funding only on days of months whose top 10 holds the coin. Each day averages the legs present
  that day, as the basket's portfolio does (:func:`funding_basis.portfolio`).
- **Verdict:** ``TOP10_KEEP`` only when the basket's own verdict is ``SCREEN_KEEP`` and the sub-book's mean daily
  return over the whole window is above zero; otherwise ``TOP10_DROP``. Days with no sub-book coin count as zero,
  which changes no sign. The read can only drop more: a basket the screen drops is ``TOP10_DROP`` whatever its
  sub-book shows. A basket that is ``NOT_YET`` or ``BLOCKED`` leaves this read in the same state, not judged.
- **Shown, never judged:** top-10 coins per month, those also in the basket book, coin-days with a non-zero return,
  the sub-book's annualised return and annual Sharpe.
- **Never:** sub-books over 2020 – 2026-09; another threshold, ranking or averaging after a window opens.

It runs in the research sandbox only, reads the research cache and writes nothing.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import statistics
import sys
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import funding_basis as fb  # noqa: E402
from scripts.research import funding_basis_screen as sc  # noqa: E402

SPEC = "TOP10_SUBBOOK_V0.1"
TOP = 10
BASKETS = ("B-POS", "PC")
KEEP, DROP = "TOP10_KEEP", "TOP10_DROP"
NOT_YET, BLOCKED, JUDGED = sc.NOT_YET, "BLOCKED", sc.JUDGED


class Blocked(Exception):
    """The top-10 ranking cannot be built from what is on disk."""


class NotYet(Exception):
    """The perp archive does not yet reach the last window month's ranking days."""


def window(basket: str) -> tuple[date, date]:
    """The basket's registered screen window, read from its own module (one source of truth)."""
    if basket == "B-POS":
        from scripts.research import funding_basis_v02 as v
        return v.SCREEN_WINDOW
    if basket == "PC":
        from scripts.research import premium_convergence as pc
        return pc.SCREEN_WINDOW
    raise ValueError(f"no top-10 read for {basket!r}; only {', '.join(BASKETS)}")


def month_starts(start: date, end: date) -> list[date]:
    out, m = [], date(start.year, start.month, 1)
    while m <= end:
        out.append(m)
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return out


def top10_by_month(market: xsb.Market, start: date, end: date) -> dict[str, frozenset[str]]:
    """``{'YYYY-MM': the month's top 10}`` for every month the window touches; reads nothing on or after each
    month's first day."""
    out = {}
    for m in month_starts(start, end):
        members = xsb.universe_at(market, m, top_n=TOP)
        if len(members) < TOP:
            raise Blocked(f"{m:%Y-%m} ranks {len(members)} coins, fewer than {TOP}")
        out[f"{m:%Y-%m}"] = frozenset(members)
    return out


def sub_legs(legs: Mapping[str, Any], top10: Mapping[str, frozenset[str]]) -> dict[str, fb.Leg]:
    """Each leg cut to the days of months whose top 10 holds its coin; a leg left with no day is dropped."""
    out = {}
    for name, leg in legs.items():
        days = [d for d in leg.returns if name in top10.get(fb.month_key(d), ())]
        if days:
            out[name] = fb.Leg(returns={d: leg.returns[d] for d in days},
                               funding={d: leg.funding.get(d, 0.0) for d in days})
    return out


def read(basket_verdict: str, legs: Mapping[str, Any], top10: Mapping[str, frozenset[str]],
         start: date, end: date) -> dict[str, Any]:
    """The verdict and the shown-only numbers of the sub-book."""
    sub = sub_legs(legs, top10)
    daily, _ = fb.portfolio(sub) if sub else ({}, {})
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    values = [daily.get(d, 0.0) for d in days]
    mean = math.fsum(values) / len(values)
    sd = statistics.pstdev(values)
    return {
        "basket_verdict": basket_verdict,
        "verdict": KEEP if basket_verdict == sc.KEEP and mean > 0 else DROP,
        "days": len(values),
        "mean_bps_per_day": round(mean * 10_000, 4),
        "annualised_return_pct": round(mean * 365 * 100, 4),
        "annual_sharpe": None if sd == 0 else round(mean / sd * math.sqrt(365), 3),
        "top10": {m: sorted(s) for m, s in sorted(top10.items())},
        "in_book_by_month": {m: sorted(s for s in legs if s in members) for m, members in sorted(top10.items())},
        "coin_days_nonzero": sum(1 for leg in sub.values() for r in leg.returns.values() if r != 0),
    }


@contextmanager
def capturing() -> Iterator[list[tuple[str, dict[str, Any]]]]:
    """Record the legs every screen hands to :func:`funding_basis_screen.summarise`; restore it afterwards."""
    seen: list[tuple[str, dict[str, Any]]] = []
    original = sc.summarise

    def spy(strategy: str, legs: Mapping[str, Any]) -> dict[str, Any]:
        seen.append((strategy, dict(legs)))
        return original(strategy, legs)

    sc.summarise = spy
    try:
        yield seen
    finally:
        sc.summarise = original


def judge(basket: str, run_screen: Callable[[], dict[str, Any]],
          load: Callable[[date, date], Mapping[str, frozenset[str]]]) -> dict[str, Any]:
    """Run the basket's screen as registered; only when it judged, rank the top 10 and read its legs on them."""
    start, end = window(basket)
    head = {"spec": SPEC, "basket": basket, "window": [start.isoformat(), end.isoformat()]}
    with capturing() as seen:
        report = run_screen()
    if report.get("status") != JUDGED:
        return {**head, "status": report.get("status"), "basket_report": report}
    row = report["strategies"][basket]
    (legs,) = [legs for strategy, legs in seen if strategy == basket]
    try:
        top10 = load(start, end)
    except NotYet as short:
        return {**head, "status": NOT_YET, "short": [str(short)], "basket_report": report}
    return {**head, "status": JUDGED, "basket_report": report, "top10_read": read(row["verdict"], legs, top10, start, end)}


def load_top10(cache: Path, start: date, end: date) -> dict[str, frozenset[str]]:
    """The window months' top 10 from the perp archive, the market freed before the screen loads."""
    from scripts.research import signal_lab as sl

    archive = cache / "archive"
    market = sl.load_until(archive, end)
    types = xsb.read_contract_types(archive)
    if types is None:
        raise Blocked(f"no contract types under {archive}")
    market.contract_types = types[0]
    need = month_starts(start, end)[-1] - timedelta(days=1)
    reach = sl.last_day(market)
    if reach is None or reach < need:
        raise NotYet(f"perp klines through {reach}, the last month's ranking needs {need}")
    try:
        return top10_by_month(market, start, end)
    finally:
        del market
        gc.collect()


def screen_runner(basket: str, cache: Path) -> Callable[[], dict[str, Any]]:
    if basket == "B-POS":
        from scripts.research import funding_basis_v021_screen as bs
        return lambda: bs.screen(bs.load(cache))
    from scripts.research import premium_convergence as pc
    from scripts.research import premium_convergence_screen as ps

    def run() -> dict[str, Any]:
        series, missing = pc.load(cache)
        if missing:
            raise Blocked(f"no 1h bars, funding or archive klines for {','.join(missing)}")
        return ps.screen(series)
    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="top10_subbook", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("run",))
    parser.add_argument("--basket", choices=BASKETS, required=True)
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    from scripts.research import funding_basis_v02 as v
    from scripts.research import premium_convergence as pc

    try:
        report = judge(args.basket, screen_runner(args.basket, cache), lambda s, e: load_top10(cache, s, e))
    except (OSError, ValueError, KeyError) as error:
        print(f"BLOCKED: cannot read the inputs under {cache}: {error}", file=sys.stderr)
        return EXIT_BLOCKED
    except (Blocked, v.Blocked, fb.DataGap, pc.DataGap) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return EXIT_BLOCKED
    print(json.dumps({"measured_at": timeutil.utc_now_iso(), **fb.provenance(), **report}, indent=1, sort_keys=True,
                     default=str))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
