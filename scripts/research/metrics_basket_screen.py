"""Metrics basket screen: M04 over one fixed four-month forward window.

``docs/proposals/METRICS_BASKET_SCREEN_V0.1.md`` (pre-registered; Thomas 2026-11-11: "등록 진행"). The rule is
``STRATEGY_SCREEN_GATE_V0.1``: ``SCREEN_KEEP`` when the mean daily net return over the whole window is above zero,
else ``SCREEN_DROP``. A strategy it keeps is still unconfirmed. The past is not judged again: M04's development and
locked spans were each read once (``METRICS_BASKET_SEARCH_V0.1``), and no day before the window is a result here.

- **The strategy (unchanged):** ``metrics_basket_search.CANDIDATES["M04"]`` through ``basket_search.weigher``, on
  the point-in-time top-:data:`TOP_N` coin perps, each Sunday close, equal weights, the engine's costs and funding.
- **Window:** :data:`SCREEN_WINDOW` only. It starts flat; the first basket is decided at the Sunday close the day
  before it, from that month's members. Every leg is closed at the last day's close; later bars are not read.
- **Coverage before any return** (a file not fetched yet looks like a coin nobody can rank, the M03 trap):
  1. klines reach the window's last day and hold at least ``TOP_N`` perps in every window month and the month
     before, else ``NOT_YET``;
  2. an archive-wide kline hole inside the window or the 31 days before it is ``BLOCKED`` (no rule registered);
  3. a member's traded day (quote volume above zero) inside its month's part of the window, or the week after it,
     needs a funding settlement, unless that month's funding file is on disk (the archive was asked and has none
     for that day: a delisting), else ``NOT_YET``;
  4. every day a member has a bar, from :data:`LOOKBACK_DAYS` before its month to the month's end, needs a built
     daily metrics row, unless the archive was asked and does not serve that day (``missing.json``), else
     ``NOT_YET``; and those excused days must have been asked again after the window closed (``refresh`` records
     it), because a day asked before the archive publishes it would otherwise stay excused for good.
- **Shown, never judged:** the mean of eight seeded random baskets and the split the diagnostic fixed (price,
  funding and cost by side, by settlements a day, by coin). No variant strategy is computed.
- **Commands:** ``run`` (sandbox only), ``plan`` (what is short; symbols and dates only), ``refresh`` (host: the
  metrics and funding files the window needs, through ``scripts/binance_archive.py``'s own readers, then ``build``).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts import binance_archive as ba  # noqa: E402
from scripts.research import basket_search as bs  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import funding_basis_screen as sc  # noqa: E402
from scripts.research import metrics_basket_diagnostic as md  # noqa: E402
from scripts.research import metrics_basket_search as ms  # noqa: E402
from scripts.research import momentum_basket_screen as mbs  # noqa: E402

SPEC = sc.SPEC
MODEL = "METRICS_BASKET_SCREEN_V0.1"
STRATEGY = "M04"
TOP_N = bs.TOP_N
SCREEN_WINDOW = (date(2027, 3, 1), date(2027, 6, 30))
LOOKBACK_DAYS = ms.PLAN_LOOKBACK          # the 30 days a feature reads before a decision
HELD_OVER_DAYS = 7                        # a leg decided in one month is held at most a week into the next
PUBLICATION_LAG_DAYS = 3                  # the archive served day D-2 and not D-1 on 2026-11-11; never ask younger
RANDOM_SEEDS = xsb.RANDOM_SEEDS
KEEP, DROP, NOT_YET, JUDGED = sc.KEEP, sc.DROP, sc.NOT_YET, sc.JUDGED
REFRESH_MARKER = "screen_refresh.json"    # beside ``missing.json``: how far the excused days were asked again


def universes(market: xsb.Market, start: date, end: date) -> dict[date, list[str]]:
    """The window months' members and the month before's, ranked as every measurement of M04 ranked them."""
    return xsb.universes(market, top_n=TOP_N, start=date(start.year, start.month, 1) - timedelta(days=1), end=end)


def _month_end(first: date) -> date:
    return date(first.year + (first.month == 12), first.month % 12 + 1, 1) - timedelta(days=1)


def _klines_short(market: xsb.Market, start: date, end: date) -> list[str]:
    short: list[str] = []
    last = max((max(bars) for bars in market.bars.values() if bars), default=None)
    if last is None or last < end:
        short.append(f"klines through {last}")
    for first in xsb.month_starts(start - timedelta(days=1), end):
        through = min(_month_end(first), end)
        alive = sum(1 for bars in market.bars.values() if any(first <= d <= through for d in bars))
        if alive < TOP_N:
            short.append(f"klines {first:%Y-%m}: {alive} perps")
    return short


def coverage(market: xsb.Market, daily: ms.Daily, start: date, end: date, *, absent: set[str],
             funding_months: set[tuple[str, str]], refreshed_through: date | None) -> dict[str, Any]:
    """``{"short": [...], "excused": {...}}``: what the cache still lacks, as symbols, months and counts only.

    ``absent`` is the names the metrics archive does not serve, ``funding_months`` the ``(symbol, "YYYY-MM")``
    funding files on disk, ``refreshed_through`` the last day whose excused files were asked again."""
    short = _klines_short(market, start, end)
    if short:
        return {"short": short, "excused": {}}             # the universe cannot be named before the klines are there
    monthly = universes(market, start, end)
    excused = {"funding_days": 0, "metrics_days": 0}
    for first in xsb.month_starts(start - timedelta(days=1), end):
        for symbol in monthly[first]:
            bars, settled = market.bars[symbol], market.funding_day(symbol) or {}
            lo, hi = max(first, start), min(_month_end(first) + timedelta(days=HELD_OVER_DAYS), end)
            lacking: dict[str, int] = {}
            for day in (d for d in bars if lo <= d <= hi and bars[d].quote_volume > 0 and d not in settled):
                key = day.strftime("%Y-%m")
                if (symbol, key) in funding_months:
                    excused["funding_days"] += 1
                else:
                    lacking[key] = lacking.get(key, 0) + 1
            short += [f"{symbol} funding {key}: {n} day(s)" for key, n in sorted(lacking.items())]
            rows, missing = daily.get(symbol) or {}, 0
            lo, hi = first - timedelta(days=LOOKBACK_DAYS), min(_month_end(first), end)
            for day in (d for d in bars if lo <= d <= hi and d not in rows):
                if ba.daily_name(symbol, ba.METRICS, day) in absent:
                    excused["metrics_days"] += 1
                else:
                    missing += 1
            if missing:
                short.append(f"{symbol} metrics for {first:%Y-%m}: {missing} day(s)")
    if refreshed_through is None or refreshed_through < end:
        short.append(f"metrics days the archive did not serve were asked again through {refreshed_through}")
    return {"short": short, "excused": excused}


def _split(market: xsb.Market, start: date, end: date) -> dict[str, Any]:
    """The diagnostic's fixed cuts for the window; a split that does not add back is shown as unavailable."""
    try:
        body = md.diagnose(market, ms.CANDIDATES[STRATEGY], (start, end), ms.FEATURES)
    except md.DoesNotReconcile as exc:
        return {"unavailable": f"the split does not reconcile ({exc})"}
    return {k: body[k] for k in ("totals_pct", "by_side_pct", "by_settlements_per_day", "funding_concentration",
                                 "near_last_bar", "by_coin_pct", "leg_days")}


def screen(market: xsb.Market, daily: ms.Daily, *, absent: set[str], funding_months: set[tuple[str, str]],
           refreshed_through: date | None) -> dict[str, Any]:
    """The report. ``NOT_YET`` carries only what is short; ``JUDGED`` carries the verdict."""
    start, end = SCREEN_WINDOW
    head = {"spec": SPEC, "model": MODEL, "top_n": TOP_N, "window": [start.isoformat(), end.isoformat()],
            "strategies_screened": [STRATEGY], "definition": ms.CANDIDATES[STRATEGY].as_dict()}
    covered = coverage(market, daily, start, end, absent=absent, funding_months=funding_months,
                       refreshed_through=refreshed_through)
    if covered["short"]:
        return {**head, "status": NOT_YET, "short": covered["short"]}
    gaps = mbs.holes(market, start, end)
    if gaps:
        raise mbs.Hole("archive-wide kline hole on " + ", ".join(d.isoformat() for d in gaps))
    monthly = universes(market, start, end)
    held = ms.cut(market, daily, end)                    # no bar, settlement or metric after the window is read
    weigher = bs.weigher(ms.CANDIDATES[STRATEGY], ms.FEATURES)
    run = lambda **kw: xsb.simulate(held, bs.CADENCE, monthly, start=start, end=end, weigher=weigher, **kw)  # noqa: E731
    judged = mbs._shown(run())
    judged["verdict"] = KEEP if judged["mean_bps_per_day"] > 0 else DROP
    random = [mbs._shown(run(seed=f"{MODEL}|{k}"))["mean_bps_per_day"] for k in range(RANDOM_SEEDS)]
    return {**head, "status": JUDGED, "universe_hash": xsb.universe_hash(monthly),
            "strategies": {STRATEGY: judged}, "excused": covered["excused"],
            "shown_only": {"random_baskets_mean_bps_per_day": round(statistics.fmean(random), 4),
                           "split": _split(held, start, end)}}


# --- the cache as the screen needs it described ------------------------------------------------------------

def read_absent(cache: Path) -> set[str]:
    path = cache / "archive-metrics" / ba.MISSING_FILENAME
    try:
        return {name for names in json.loads(path.read_text()).values() for name in names}
    except (OSError, ValueError):
        return set()


def read_funding_months(archive: Path) -> set[tuple[str, str]]:
    out = set()
    for path in (archive / xsb.FUNDING_KIND).glob("*/*.zip"):
        out.add((path.parent.name, path.stem[-7:]))
    return out


def read_refreshed_through(cache: Path) -> date | None:
    """The last day ``refresh`` asked again for, or None unless that run could already name the last month's members."""
    try:
        marker = json.loads((cache / "archive-metrics" / REFRESH_MARKER).read_text())
        if marker["members_named_through"] < SCREEN_WINDOW[1].strftime("%Y-%m"):
            return None
        return date.fromisoformat(marker["through"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


# --- the host side: fetch what the window needs ------------------------------------------------------------

def nameable_months(market: xsb.Market, start: date, end: date) -> list[date]:
    """The month before the window and the window months whose universe the klines on disk can already name."""
    last = max((max(bars) for bars in market.bars.values() if bars), default=None)
    return [m for m in xsb.month_starts(start - timedelta(days=1), end)
            if last is not None and m - timedelta(days=1) <= last]


def forget_absent(dest: Path, plan: list[tuple[str, date, date]]) -> int:
    """Drop the plan's days from ``missing.json`` so the next fetch asks the archive for them once more."""
    path = dest / ba.MISSING_FILENAME
    if not path.exists():
        return 0
    missing: dict[str, list[str]] = json.loads(path.read_text())
    asked = {ba.daily_name(symbol, ba.METRICS, day) for symbol, first, last in plan for day in ba.days(first, last)}
    kept = {symbol: [n for n in names if n not in asked] for symbol, names in missing.items()}
    dropped = sum(len(v) for v in missing.values()) - sum(len(v) for v in kept.values())
    path.write_text(json.dumps({k: sorted(v) for k, v in kept.items() if v}, indent=1))
    return dropped


def refresh(cache: Path, *, today: date, fetch: Callable[..., Any] = ba.fetch,
            fetch_monthly: Callable[..., Any] = ba.fetch_monthly, quiet: Callable[[], bool] | None = None,
            say: Callable[[str], None] = print) -> int:
    """Fetch the metrics days and funding months the window needs, as far as the klines can name members and the
    archive has published, then rebuild the daily values. Safe to rerun; present files are never asked again."""
    from scripts.research import funding_basis_refresh as fbr

    quiet = quiet or (lambda: fbr.in_quiet(datetime.now(timezone.utc)))
    archive, dest = cache / "archive", cache / "archive-metrics"
    snapshot = xsb.read_contract_types(archive)
    if snapshot is None:
        say(f"BLOCKED: no readable {xsb.CONTRACT_TYPES_FILENAME} under {archive}")
        return EXIT_BLOCKED
    market = xsb.load_market(archive, funding=False)
    market.contract_types = snapshot[0]
    start, end = SCREEN_WINDOW
    months = nameable_months(market, start, end)
    through = min(end, today - timedelta(days=PUBLICATION_LAG_DAYS))
    if not months or through < months[0] - timedelta(days=LOOKBACK_DAYS):
        say(f"metrics basket screen: nothing to fetch yet (window {start}..{end})")
        return EXIT_OK
    monthly = {m: xsb.universe_at(market, m, top_n=TOP_N) for m in months}
    plan = ms.fetch_plan(monthly, lookback=LOOKBACK_DAYS, end=through)
    say(f"metrics basket screen: members named through {months[-1]:%Y-%m}, days through {through}, "
        f"{len(plan)} span(s); asking again for {forget_absent(dest, plan)} day(s) the archive did not serve")
    total = {"kept": 0, "present": 0, "missing": 0}
    for symbol, first, last in plan:
        if quiet():
            say("HALTED: inside the quiet window 07:30–09:00 UTC; rerun the same command after 09:00 UTC")
            return EXIT_BLOCKED
        tally = fetch(dest, symbols=[symbol], start=first, end=last)[symbol]
        for key in total:
            total[key] += tally[key]
    members = sorted({s for v in monthly.values() for s in v})
    fetch_monthly(archive, kind=xsb.FUNDING_KIND, symbols=members, start=months[0], end=through)
    built = ms.build(cache, say=lambda _line: None)
    (dest / REFRESH_MARKER).write_text(json.dumps({"through": through.isoformat(), "at": timeutil.utc_now_iso(),
                                                   "members_named_through": months[-1].strftime("%Y-%m")}, indent=1))
    say(f"metrics basket screen: metrics kept {total['kept']}, present {total['present']}, not served "
        f"{total['missing']}; funding asked for {len(members)} member(s); {built} daily row(s) built")
    return EXIT_OK


# --- the command line ----------------------------------------------------------------------------------------

def main(argv: list[str] | None = None, *, today: date | None = None) -> int:
    parser = argparse.ArgumentParser(prog="metrics_basket_screen", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("run", "plan", "refresh"),
                        help="run: the screen (sandbox); plan: what is short; refresh: fetch and build (host)")
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    if args.command == "run" and not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    if args.command == "refresh":
        if xsb._in_sandbox():
            print("refused: refresh fetches; it runs on the host, not in the sandbox", file=sys.stderr)
            return EXIT_USAGE
        return refresh(cache, today=today or datetime.now(timezone.utc).date())
    archive = cache / "archive"
    snapshot = xsb.read_contract_types(archive)
    if snapshot is None:
        print(f"BLOCKED: no readable {xsb.CONTRACT_TYPES_FILENAME} under {archive}", file=sys.stderr)
        return EXIT_BLOCKED
    market = xsb.load_market(archive)
    market.contract_types = snapshot[0]
    daily = ms.load_daily(cache)
    described = {"absent": read_absent(cache), "funding_months": read_funding_months(archive),
                 "refreshed_through": read_refreshed_through(cache)}
    start, end = SCREEN_WINDOW
    if args.command == "plan":
        covered = coverage(market, daily, start, end, **described)
        print(json.dumps({"window": [start.isoformat(), end.isoformat()], "short": covered["short"],
                          "excused": covered["excused"]}, indent=1, sort_keys=True))
        return EXIT_OK
    try:
        report = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), **screen(market, daily, **described)}
    except mbs.Hole as hole:
        print(f"BLOCKED: {hole}", file=sys.stderr)
        return EXIT_BLOCKED
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False))
        return EXIT_OK
    if report["status"] == NOT_YET:
        print(f"NOT_YET: window {report['window'][0]}..{report['window'][1]}; short: {'; '.join(report['short'])}")
        return EXIT_OK
    row = report["strategies"][STRATEGY]
    print(f"{STRATEGY}: {row['verdict']} | mean {row['mean_bps_per_day']} bps/day over {row['days']} days "
          f"({row['mean_bps_per_week']} bps/week, {row['weeks']} weeks) | cost {row['cost_pct']}% "
          f"funding {row['funding_pct']}% | forced closes {row['forced_closes']}")
    print(f"  shown only: random baskets {report['shown_only']['random_baskets_mean_bps_per_day']} bps/day")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
