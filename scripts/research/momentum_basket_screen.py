"""Momentum basket screen: the 30-day cross-sectional momentum basket, sized by inverse volatility, over one fixed
four-month forward window.

``docs/proposals/MOMENTUM_BASKET_V0.1.md`` (pre-registered; Thomas 2026-11-10). The rule is
``STRATEGY_SCREEN_GATE_V0.1``: ``SCREEN_KEEP`` when the mean daily net return over the whole window is above zero,
else ``SCREEN_DROP``. A strategy it keeps is still unconfirmed. It reads only the research cache's archive and
writes nothing, and it reads no day before the window as a result: the past was measured once already
(``CROSS_SECTION_CARRY_AND_WIDE_MOMENTUM_V0.1``, ``CROSS_SECTION_SPOT_HOLDOUT_V0.1``) and is not judged again.

- **Baskets (unchanged):** ``cross_section_basket``'s ``S-MOM30`` on the point-in-time top-:data:`TOP_N` coin
  perps: each Sunday close, the top fifth by 30-day return long and the bottom fifth short, held a week.
- **The one change, sizing:** inside each side a member's weight is proportional to ``1 / σ``, where ``σ`` is the
  standard deviation of its daily close-to-close returns over the same 30 days. Each side still sums to 1. A
  member with fewer than :data:`MIN_VOL_RETURNS` daily returns in those 30 days, or none that vary, is not ranked.
- **Costs and funding:** ``cross_section_basket.simulate``'s, unchanged.
- **Window:** :data:`SCREEN_WINDOW` only. It starts flat; the first basket is decided at the last Sunday close on
  or before the day before the window. Every leg is closed at the last day's close.
- **Coverage before any return.** A month not fetched yet looks like delisted coins and unpaid funding, so:
  1. the kline archive must reach the window's last day and hold at least ``TOP_N`` perps in every window month
     and in the month before it, else ``NOT_YET``;
  2. a day missing from the klines of a quarter of the perps alive across it (an archive hole,
     ``FUNDING_BASIS_V0.2`` §10) inside the window or the 31 days before it is ``BLOCKED``: the basket would read
     it as every leg ending, and no rule for it is registered here;
  3. a member needs a funding settlement on each day it traded (quote volume above zero) inside its month's part
     of the window and the week after it (a leg decided in one month is held into the next), unless that month's
     funding file is on disk: then the archive was asked and has none for that day (a delisted coin keeps flat
     zero-volume bars and its funding files stop). Else ``NOT_YET`` (``plan`` lists the symbols to fetch).
     Amended 2026-11-11, before the window opened (``MOMENTUM_BASKET_V0.1`` "수정"): the rule as first registered
     asked for a settlement on every day with a bar, so a member delisted inside the window held it at ``NOT_YET``
     for good. ``metrics_basket_screen`` uses this same function.
- **Shown, never judged:** the same baskets at equal weights, the mean of eight seeded random baskets, weekly
  means, costs, funding and forced closes.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import funding_basis_screen as sc  # noqa: E402
from scripts.research import funding_basis_v021_screen as s21  # noqa: E402

SPEC = sc.SPEC
MODEL = "MOMENTUM_BASKET_V0.1"
SIGNAL = "S-MOM30"
TOP_N = 30
VOL_LOOKBACK_DAYS = xsb.SIGNALS[SIGNAL]["lookback"]      # the signal's own 30 days: no second window is chosen
MIN_VOL_RETURNS = 20
SCREEN_WINDOW = (date(2027, 2, 1), date(2027, 5, 31))
RANDOM_SEEDS = xsb.RANDOM_SEEDS
HELD_OVER_DAYS = 7                        # a leg decided in one month is held at most a week into the next
KEEP, DROP, NOT_YET, JUDGED = sc.KEEP, sc.DROP, sc.NOT_YET, sc.JUDGED


class Hole(Exception):
    """An archive-wide kline hole sits inside the window or the days its first basket reads."""


def trailing_vol(market: xsb.Market, symbol: str, day: date) -> float | None:
    """Standard deviation of the daily close-to-close returns over the 30 days to ``day``'s close; None when
    fewer than :data:`MIN_VOL_RETURNS` of them exist or they do not vary."""
    bars = market.bars.get(symbol) or {}
    returns = []
    for k in range(VOL_LOOKBACK_DAYS):
        now, then = bars.get(day - timedelta(days=k)), bars.get(day - timedelta(days=k + 1))
        if now is not None and then is not None:
            returns.append(now.close / then.close - 1)
    if len(returns) < MIN_VOL_RETURNS:
        return None
    sd = statistics.stdev(returns)
    return sd if sd > 0 else None


def vol_weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                *, seed: str | None = None) -> dict[str, float]:
    """``target_weights``'s baskets over the members that have a volatility, each side sized by ``1 / σ``."""
    vols = {s: trailing_vol(market, s, day) for s in members}
    sized = [s for s in members if vols[s] is not None]
    equal = xsb.target_weights(market, sized, day, signal, seed=seed)
    out: dict[str, float] = {}
    for sign in (1.0, -1.0):
        side = [s for s, w in equal.items() if w * sign > 0]
        total = sum(1 / vols[s] for s in side)
        out.update({s: sign * (1 / vols[s]) / total for s in side})
    return out


def _month(day: date) -> date:
    return date(day.year, day.month, 1)


def _month_end(first: date) -> date:
    return date(first.year + (first.month == 12), first.month % 12 + 1, 1) - timedelta(days=1)


def universes(market: xsb.Market, start: date, end: date) -> dict[date, list[str]]:
    """The window months' members and the month before's (its last Sunday decides the first basket)."""
    return xsb.universes(market, top_n=TOP_N, start=_month(start - timedelta(days=1)), end=end)


def funding_plan(market: xsb.Market, start: date, end: date) -> dict[str, list[str]]:
    """``{YYYY-MM: symbols}`` whose funding the window needs: the month's members, then the month before's (their
    legs are held into its first week). Symbols and months only."""
    monthly = universes(market, start, end)
    out = {}
    for m in xsb.month_starts(start, end):
        before = monthly.get(_month(m - timedelta(days=1)), [])
        out[m.strftime("%Y-%m")] = list(monthly[m]) + [s for s in before if s not in monthly[m]]
    return out


def read_funding_months(archive: Path) -> set[tuple[str, str]]:
    """``{(symbol, "YYYY-MM")}`` of the monthly funding files on disk."""
    return {(path.parent.name, path.stem[-7:]) for path in (archive / xsb.FUNDING_KIND).glob("*/*.zip")}


def funding_shortfall(market: xsb.Market, monthly: Mapping[date, Sequence[str]], start: date, end: date,
                      funding_months: frozenset[tuple[str, str]] | set[tuple[str, str]] = frozenset(),
                      ) -> tuple[list[str], int]:
    """``(short, excused_days)`` for the members of every month in ``monthly``: a day a member traded inside its
    month's part of [start, end], or the :data:`HELD_OVER_DAYS` after it, needs a settlement unless that day's
    monthly funding file is in ``funding_months``. Symbols, months and counts only."""
    lacking: dict[tuple[str, str], set[date]] = {}
    excused: set[tuple[str, date]] = set()
    for first in sorted(monthly):
        lo, hi = max(first, start), min(_month_end(first) + timedelta(days=HELD_OVER_DAYS), end)
        for symbol in monthly[first]:
            bars, settled = market.bars[symbol], market.funding_day(symbol) or {}
            for day in (d for d in bars if lo <= d <= hi and bars[d].quote_volume > 0 and d not in settled):
                key = day.strftime("%Y-%m")
                if (symbol, key) in funding_months:
                    excused.add((symbol, day))
                else:
                    lacking.setdefault((symbol, key), set()).add(day)     # a day two months ask for counts once
    short = [f"{symbol} funding {key}: {len(days)} day(s)" for (symbol, key), days in sorted(lacking.items())]
    return short, len(excused)


def shortfall(market: xsb.Market, start: date, end: date,
              funding_months: frozenset[tuple[str, str]] | set[tuple[str, str]] = frozenset()) -> list[str]:
    """What the archive still lacks for the window, as symbols and dates only; empty when it is covered."""
    short: list[str] = []
    last = max((max(bars) for bars in market.bars.values() if bars), default=None)
    if last is None or last < end:
        short.append(f"klines through {last}")
    for first in xsb.month_starts(start - timedelta(days=1), end):
        through = min(_month_end(first), end)
        alive = sum(1 for bars in market.bars.values() if any(first <= d <= through for d in bars))
        if alive < TOP_N:
            short.append(f"klines {first:%Y-%m}: {alive} perps")
    if short:
        return short                       # the universe cannot be named before the klines are there
    return funding_shortfall(market, universes(market, start, end), start, end, funding_months)[0]


def holes(market: xsb.Market, start: date, end: date) -> list[date]:
    traded = {s: set(bars) for s, bars in market.bars.items() if xsb.is_coin(market, s)}
    lo = start - timedelta(days=VOL_LOOKBACK_DAYS + 1)
    return sorted(d for d in s21.archive_holes(traded) if lo <= d <= end)


def _shown(sim: Mapping[str, Any]) -> dict[str, Any]:
    daily = sim["daily"]
    net = [daily[d]["net"] for d in sorted(daily)]
    weeks = xsb.weekly(daily)
    return {
        "days": len(net),
        "mean_bps_per_day": round(statistics.fmean(net) * 10_000, 4) if net else 0.0,
        "weeks": len(weeks),
        "mean_bps_per_week": round(statistics.fmean(weeks.values()) * 10_000, 2) if weeks else None,
        "sum_pct": round(sum(net) * 100, 4),
        "gross_pct": round(sum(v["gross"] for v in daily.values()) * 100, 4),
        "cost_pct": round(sum(v["cost"] for v in daily.values()) * 100, 4),
        "funding_pct": round(sum(v["funding"] for v in daily.values()) * 100, 4),
        "forced_closes": sim["forced_closes"]["count"],
        "unranked_member_rebalances": sim["unranked_member_rebalances"],
    }


def screen(market: xsb.Market, funding_months: frozenset[tuple[str, str]] | set[tuple[str, str]] = frozenset(),
           ) -> dict[str, Any]:
    """The report. ``NOT_YET`` carries only what is short; ``JUDGED`` carries the verdict."""
    start, end = SCREEN_WINDOW
    head = {"spec": SPEC, "model": MODEL, "signal": SIGNAL, "top_n": TOP_N,
            "window": [start.isoformat(), end.isoformat()], "strategies_screened": ["M"]}
    short = shortfall(market, start, end, funding_months)
    if short:
        return {**head, "status": NOT_YET, "short": short}
    gaps = holes(market, start, end)
    if gaps:
        raise Hole("archive-wide kline hole on " + ", ".join(d.isoformat() for d in gaps))
    monthly = universes(market, start, end)
    # Bars after the window are not read: every leg then ends at the last day's close, whatever the archive holds.
    cut = xsb.Market(bars={s: {d: b for d, b in bars.items() if d <= end} for s, bars in market.bars.items()},
                     funding=market.funding, contract_types=market.contract_types)
    run = lambda **kw: xsb.simulate(cut, SIGNAL, monthly, start=start, end=end, **kw)  # noqa: E731
    judged = _shown(run(weigher=vol_weights))
    judged["verdict"] = KEEP if judged["mean_bps_per_day"] > 0 else DROP
    random = [_shown(run(weigher=vol_weights, seed=f"{MODEL}|{k}"))["mean_bps_per_day"] for k in range(RANDOM_SEEDS)]
    return {**head, "status": JUDGED, "universe_hash": xsb.universe_hash(monthly),
            "strategies": {"M": judged},
            "excused_funding_days": funding_shortfall(market, monthly, start, end, funding_months)[1],
            "shown_only": {"equal_weight": _shown(run()),
                           "random_baskets_mean_bps_per_day": round(statistics.fmean(random), 4)}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="momentum_basket_screen", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("run", "plan"),
                        help="run: the screen; plan: the members whose funding each window month needs")
    parser.add_argument("--archive", type=Path, default=None, help="default: $RESEARCH_CACHE/archive")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "run" and not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    archive = args.archive or xsb._archive_default()
    snapshot = xsb.read_contract_types(archive)
    if snapshot is None:
        print(f"BLOCKED: no readable {xsb.CONTRACT_TYPES_FILENAME} under {archive}", file=sys.stderr)
        return EXIT_BLOCKED
    market = xsb.load_market(archive)
    market.contract_types = snapshot[0]
    start, end = SCREEN_WINDOW
    funding_months = read_funding_months(archive)
    if args.command == "plan":
        short = shortfall(market, start, end, funding_months)
        klines_short = [line for line in short if line.startswith("klines")]
        plan = None if klines_short else funding_plan(market, start, end)
        print(json.dumps({"window": [start.isoformat(), end.isoformat()], "short": short, "funding": plan},
                         indent=1, sort_keys=True))
        return EXIT_OK
    try:
        report = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), **screen(market, funding_months)}
    except Hole as hole:
        print(f"BLOCKED: {hole}", file=sys.stderr)
        return EXIT_BLOCKED
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return EXIT_OK
    if report["status"] == NOT_YET:
        print(f"NOT_YET: window {report['window'][0]}..{report['window'][1]}; short: {'; '.join(report['short'])}")
        return EXIT_OK
    row = report["strategies"]["M"]
    print(f"M: {row['verdict']} | mean {row['mean_bps_per_day']} bps/day over {row['days']} days "
          f"({row['mean_bps_per_week']} bps/week, {row['weeks']} weeks) | cost {row['cost_pct']}% "
          f"funding {row['funding_pct']}% | forced closes {row['forced_closes']}")
    print(f"  shown only: equal weight {report['shown_only']['equal_weight']['mean_bps_per_day']} bps/day, "
          f"random baskets {report['shown_only']['random_baskets_mean_bps_per_day']} bps/day")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
